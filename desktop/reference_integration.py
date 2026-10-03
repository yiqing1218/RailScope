"""Adapters from sourced profiles to shared domain objects, never GIS rewrites."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
import re
from uuid import uuid5, NAMESPACE_URL

from railscope.domain import InfrastructureLifecycle, Yard


def evidence(profile):
    return {k: profile.get(k) for k in ('source', 'source_url', 'snapshot_id',
            'retrieved_at', 'verification_status', 'confidence')}


def vehicle_from_profile(vehicle, profile):
    if profile.get('kind') != 'vehicle':
        raise ValueError('请选择车型资料')
    parameters = deepcopy(profile.get('attributes', {}))
    parameters.update(deepcopy(vehicle.parameters))
    parameters.setdefault('reference_profile', {'id': profile['id'], **evidence(profile)})
    return replace(vehicle, model=vehicle.model or profile['name'], parameters=parameters)


def structured_yard(yard):
    """Source '站台' means platform face; it is not a physical island number."""
    label = yard['name']
    match = re.search(r'\[\s*(\d+)\s*[~～\-至]\s*(\d+)\s*(站台|股道|道)\s*\]', label)
    value = dict(yard)
    value['yard_name'] = label[:match.start()].strip() if match else label
    if match:
        value['number_range'] = [int(match[1]), int(match[2])]
        value['number_kind'] = 'platform_face' if match[3]=='站台' else 'track'
    return value


def station_events(profile):
    events = []
    for index, scope in enumerate(profile.get('scopes', ())):
        value = scope.get('opened') or scope.get('source_notes', '').strip()
        if re.fullmatch(r'\d{4}(?:-\d{2}){0,2}', value):
            events.append({'kind': 'opened', 'date': value, 'scope': scope.get('name', ''),
                           'scope_index': index, 'lines': scope.get('lines', []),
                           'date_precision': ('year','month','day')[value.count('-')],
                           **evidence(profile)})
    value = profile.get('attributes', {}).get('commissioning_date')
    if value and not any(e['date']==value for e in events):
        if re.fullmatch(r'\d{4}(?:-\d{2}){0,2}', value):
            events.append({'kind':'opened', 'date':value, 'scope':'车站',
                           'date_precision':('year','month','day')[value.count('-')], **evidence(profile)})
    return events


def reference_lifecycle(profile, ident, aliases, name):
    events = station_events(profile)
    exact = []
    for event in events:
        if event['date_precision']=='day':
            try:
                date.fromisoformat(event['date'])
                exact.append(event['date'])
            except ValueError:
                pass
    return InfrastructureLifecycle(ident, opened=min(exact) if exact else None,
        source='china-emu.cn', snapshot_id=profile['snapshot_id'],
        verification_status='external_reference_unverified', source_aliases=tuple(sorted(aliases)),
        display_name=name, provenance={'reference_profile_id':profile['id'],
            'events':events, **evidence(profile)})


YARD_TYPES = {'高速场':'high_speed', '高铁场':'high_speed', '普速场':'conventional',
              '城际场':'intercity', '综合场':'mixed', '货运场':'freight'}


def yard_bindings(repo, profile, context=()):
    """Return number-based proposals with stable track IDs and explicit uncertainty.

    Repeated track numbers and conflicting source scopes remain unresolved.
    Platform-face -> track equality is always a proposal needing confirmation.
    """
    by_number = {}
    for track in repo.station_tracks.values():
        number = str(track.track_number or '').removesuffix('道')
        if number.isdigit():
            by_number.setdefault(int(number), []).append(track)
    platform_refs = []
    if context:
        from shapely.geometry import shape
        from shapely.ops import transform
        try:
            from .station_schematic import station_projection
        except ImportError:
            from station_schematic import station_projection
        local, *_ = station_projection(repo)
        for feature in context:
            props = feature.get('properties', {})
            tags = props.get('way_tags', {})
            if props.get('boundary_kind')!='platform' and tags.get('railway')!='platform':
                continue
            numbers = {int(v) for v in re.split(r'[;/,\s]+',str(tags.get('ref') or props.get('ref') or '')) if v.isdigit()}
            if numbers:
                platform_refs.append((numbers,transform(lambda x,y,z=None:local((x,y)),shape(feature['geometry'])),
                                      props.get('infrastructure_id')))
    bindings = []
    for index, scope in enumerate(profile.get('scopes', ())):
        for raw in scope.get('yards', ()):
            yard = structured_yard(raw)
            if 'number_range' not in yard:
                bindings.append({'yard':yard, 'scope_index':index, 'track_id':None,
                                 'number':None, 'status':'missing_number_range'})
                continue
            a,b = yard['number_range']
            if not 0 <= a <= b <= 1000:
                continue
            for number in range(a,b+1):
                tracks = by_number.get(number, [])
                compatible = {'high_speed':'high_speed','conventional':'conventional','freight':'freight'}.get(YARD_TYPES.get(yard['yard_name']))
                if len(tracks)>1 and compatible:
                    matches = [t for t in tracks if t.railway_class==compatible]
                    if len(matches)==1:
                        tracks = matches
                proof = []
                if len(tracks)==1 and platform_refs:
                    from shapely.geometry import LineString
                    paths = [LineString([local(p) for p in repo.edges[ref.edge_id].coordinates]) for ref in tracks[0].edge_refs]
                    proof = [source for nums,geometry,source in platform_refs if number in nums and source
                             and any(path.distance(geometry)<=12 for path in paths)]
                bindings.append({'yard':yard, 'scope_index':index, 'number':number,
                    'track_id':tracks[0].id if len(tracks)==1 else None,
                    'platform_source_ids':proof,
                    'status':'platform_face_track_reference' if len(tracks)==1 and proof else
                             'reference_number_match' if len(tracks)==1 else
                             'ambiguous_track_number' if tracks else 'missing_track_number'})
                if len(tracks)==1 and compatible and tracks[0].railway_class not in ('unknown',compatible):
                    bindings[-1]['status'] = 'conflicting_railway_class'
    counts = {}
    for item in bindings:
        if item['track_id']:
            counts[item['track_id']] = counts.get(item['track_id'],0)+1
    for item in bindings:
        if counts.get(item['track_id'],0)>1:
            item['status']='ambiguous_source_scope'
    return bindings


def apply_yard_binding(repo, profile, binding, track_id, confirmed=False):
    track = repo.station_tracks[track_id]
    yard = binding['yard']
    status = 'user_verified' if confirmed else 'external_reference_unverified'
    if not confirmed and not (binding['status']=='platform_face_track_reference' or
                             binding['status']=='reference_number_match' and yard.get('number_kind')=='track'):
        raise ValueError('站台面与股道的对应需要确认')
    ident = 'Y-' + uuid5(NAMESPACE_URL, track.station_id+'/reference-yard/'+str(binding['scope_index'])+'/'+yard['yard_name']).hex
    proof = {**evidence(profile), 'verification_status':status, 'confidence':None,
             'number_kind':yard.get('number_kind'), 'source_number':binding['number'],
             'track_id':track_id, 'scope_index':binding['scope_index'], 'name':yard['yard_name'],
             'platform_source_ids':binding.get('platform_source_ids', []),
             'source':'workspace_override' if confirmed else 'china-emu.cn'}
    yard_type = YARD_TYPES.get(yard['yard_name'], 'unknown')
    repo.yards[ident] = Yard(ident,track.station_id,yard['yard_name'],yard_type,
        source_id=proof['source'], snapshot_id=profile['snapshot_id'],verification_status=status,
        provenance={'reference_binding':proof, 'line_names':yard.get('line_names', [])})
    provenance = {**track.provenance,'yard':proof}
    platform_number = track.platform_number
    if yard.get('number_kind')=='platform_face' and binding['number'] is not None:
        if confirmed or not platform_number:
            platform_number = str(binding['number'])
            provenance['platform_number'] = proof
    repo.station_tracks[track_id] = replace(track, yard_id=ident,
        platform_number=platform_number, provenance=provenance)


def integrate_station_yards(repo, profile, context=()):
    for binding in yard_bindings(repo,profile,context):
        key = binding['track_id']
        if not key:
            continue
        track = repo.station_tracks[key]
        if track.yard_id or track.provenance.get('yard', {}).get('source')=='workspace_override':
            continue
        if binding['status']=='platform_face_track_reference' or (
                binding['status']=='reference_number_match' and binding['yard'].get('number_kind')=='track'):
            apply_yard_binding(repo,profile,binding,key)
