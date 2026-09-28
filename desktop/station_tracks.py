"""Selected-station adapter: shared StationTrack entities reference physical edges.

Source geometry stays in rail.sqlite; names/numbers and stable bindings are
stored in the existing workspace override layer and survive source reimports.
"""
from collections import defaultdict
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path
import json
import math
import sqlite3

from railscope.domain import Station, StationTrack, StationTrackEdge
from railscope.identity import IdentityRegistry
from railscope.integrity import path_refs
try:
    from .domain_adapter import build_repository
    from .station_track_semantics import track_number, migrate_track_override, semantic_track_name
except ImportError:
    from domain_adapter import build_repository
    from station_track_semantics import track_number, migrate_track_override, semantic_track_name


def ordered_track_legs(edges):
    """Orient one endpoint-delimited physical track; never invent a connection."""
    adjacency = defaultdict(list)
    for edge in edges:
        adjacency[edge['from_node']].append(edge)
        adjacency[edge['to_node']].append(edge)
    if any(len(values) > 2 for values in adjacency.values()):
        raise ValueError('股道包含分支，需要先整理真实道岔边界')
    ends = sorted((n for n, values in adjacency.items() if len(values) == 1), key=str)
    current = ends[0] if ends else min(adjacency, key=str)
    seen, result = set(), []
    while len(seen) < len(edges):
        edge = next((e for e in adjacency[current] if e['id'] not in seen), None)
        if edge is None:
            raise ValueError('股道源区间不连续，不能合并命名')
        forward = edge['from_node'] == current
        result.append((edge['id'], forward))
        seen.add(edge['id'])
        current = edge['to_node'] if forward else edge['from_node']
    return result


def track_sections(sections, by_edge, overrides):
    """Join named fragments only with equal source track numbers and real nodes."""
    parent = {key:key for key in sections}
    manual_names = {key: set() for key in sections}
    def root(key):
        while key != parent[key]:
            parent[key] = parent[parent[key]]; key = parent[key]
        return key
    endpoint_groups = defaultdict(list)
    for section, members in sections.items():
        values = {track_number(by_edge[key].get('way_tags', {})) for key in members}
        values.discard(None)
        if len(values) != 1:
            continue
        number = next(iter(values))
        custom = overrides.get('object:section_id:' + section, {})
        manual = custom.get('display_name') if custom.get('source') == 'manual' or custom.get('verification_status') in ('user_named', 'user_verified', 'official_confirmed') else None
        if manual:
            manual_names[section].add(manual)
        for key in members:
            for node in (by_edge[key]['from_node'], by_edge[key]['to_node']):
                endpoint_groups[(number,node)].append((section,manual))
    for group in endpoint_groups.values():
        if len({section for section, _ in group}) > 2:
            continue
        for a, a_name in group:
            for b, b_name in group:
                ra,rb=root(a),root(b)
                names = manual_names[ra] | manual_names[rb]
                if len(names) > 1:
                    continue
                parent[max(ra,rb)]=min(ra,rb)
                manual_names[min(ra,rb)] = names
    joined = defaultdict(list)
    for key in sections:joined[root(key)].append(key)
    return [tuple(sorted(group)) for group in joined.values()]


def load_station_tracks(directory, identity_path, station, overrides):
    """Return repository + source bindings + real platform/switch geometries."""
    directory = Path(directory)
    name = str(station.get('name') or station.get('display_name') or '').removesuffix('站')
    group = station.get('catalog_group_id')
    with closing(sqlite3.connect(directory / 'rail_catalog.sqlite')) as db:
        if group and str(group).startswith('ST-'):
            row = db.execute('SELECT station_name FROM catalog WHERE id=?', (group,)).fetchone()
            if row:
                name = row[0].removesuffix('站')
        groups = [(key, json.loads(raw)) for key, raw in db.execute(
            "SELECT id,data FROM catalog WHERE station_name IN (?,?)", (name, name + '站'))]
        source = station.get('infrastructure_id') if station.get('kind') in ('station', 'halt') else station.get('station_source')
        if source:
            groups = [(key, row) for key, row in groups
                      if not overrides.get(key, {}).get('station_source')
                      or overrides[key]['station_source'] == source]
            manual_groups = {key for key, override in overrides.items()
                             if key.startswith('ST-') and override.get('station_source') == source}
            for key in sorted(manual_groups - {key for key, _ in groups}):
                row = db.execute('SELECT data FROM catalog WHERE id=?', (key,)).fetchone()
                if row:
                    groups.append((key, json.loads(row[0])))
    province = station.get('province')
    if province:
        groups = [(key, row) for key, row in groups if province in row.get('provinces', [])]
    regions = {tuple(row.get('provinces', [])) for _, row in groups}
    if len(regions) > 1 and not province:
        raise ValueError('存在同名异地站场，请从地图选择具体车站')
    features = []
    with closing(sqlite3.connect(directory / 'rail.sqlite')) as db:
        for key, _ in groups:
            features.extend(json.loads(row[0]) for row in db.execute(
                'SELECT f.data FROM rail_feature_groups g JOIN features f ON f.id=g.feature_id WHERE g.group_id=?', (key,)))
        if not features:
            raise ValueError('该站尚无已关联的站场股道，请先整理车站与股道关系')
        edge_ids = {f['properties']['network_edge_id'] for f in features}
        edges = [json.loads(db.execute('SELECT data FROM edges WHERE id=?', (key,)).fetchone()[0]) for key in sorted(edge_ids)]
        coords = [p for edge in edges for p in edge['coordinates']]
        west, south = min(p[0] for p in coords), min(p[1] for p in coords)
        east, north = max(p[0] for p in coords), max(p[1] for p in coords)
        # Context is bounded by the identified station yard, not a generic radius.
        context = [json.loads(raw) for (raw,) in db.execute(
            "SELECT f.data FROM bounds b JOIN features f ON f.id=b.id WHERE b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=? AND f.kind IN ('railPlatforms','railPoints','rail')",
            (west, east, south, north))]
        yard_nodes = {node for e in edges for node in e.get('node_ids', (e['from_node'], e['to_node']))}
        access_ids = {f['properties']['network_edge_id'] for f in context
                      if f['geometry']['type'] == 'LineString' and f['properties'].get('network_edge_id')
                      and {f['properties'].get('from_node_id'), f['properties'].get('to_node_id')} & yard_nodes} - edge_ids
        for key in sorted(access_ids):
            edges.append(json.loads(db.execute('SELECT data FROM edges WHERE id=?', (key,)).fetchone()[0]))
    document = {'schema': 'railscope.rail-plan.v2', 'routes': [], 'trains': [], 'extensions': {},
                'required_capabilities': [], 'service_date': '2026-01-01', 'timezone': 'Asia/Shanghai', 'source': 'station infrastructure'}
    repo, bindings = build_repository({'edges': edges, 'points': []}, document, identity_path)
    registry = IdentityRegistry(identity_path)
    source = station.get('infrastructure_id') if station.get('kind') in ('station','halt') else None
    if not source:
        with closing(sqlite3.connect(directory / 'rail_lines.sqlite')) as db:
            candidates = [(key, json.loads(raw)) for key, raw in db.execute(
                'SELECT source_id,data FROM station_directory WHERE name IN (?,?)', (name, name+'站'))]
        candidates = [(key, f) for key, f in candidates if west-.01 <= f['geometry']['coordinates'][0] <= east+.01
                      and south-.01 <= f['geometry']['coordinates'][1] <= north+.01]
        if len(candidates) != 1:
            raise ValueError('站场尚未唯一关联到车站实体，请从地图车站对象进入')
        source = candidates[0][0]
    station_id = registry.resolve_alias('station', 'osm/' + source, 'ST')
    anchor = next(iter(repo.nodes.values()))
    repo.stations[station_id] = Station(station_id, name + '站', anchor.lon, anchor.lat, anchor.id,
                                       source_member_ids=(source,), verification_status='automatic_reference')
    sections = defaultdict(set)
    for feature in features:
        props = feature['properties']
        sections[props.get('section_id') or props['network_edge_id']].add(props['network_edge_id'])
    by_edge = {edge['id']: edge for edge in edges}
    rows = {}
    for source_sections in track_sections(sections, by_edge, overrides):
        members = set().union(*(sections[section] for section in source_sections))
        keys = ['object:' + ('section_id:' if section.startswith('RS-') else 'network_edge_id:') + section for section in source_sections]
        saved_values = [overrides.get(key, {}) for key in keys]
        saved = next((v for v in saved_values if v.get('source') == 'manual' or v.get('verification_status') in ('user_named', 'user_verified', 'official_confirmed')), saved_values[0])
        # Source section IDs remain aliases, not per-map-fragment business IDs.
        ident = registry.resolve_alias('station_track', source_sections[0], 'TRK')
        source_edges = [by_edge[value] for value in sorted(members)]
        refs = path_refs(repo, [(bindings['edges'][key], forward) for key, forward in ordered_track_legs(source_edges)], allow_nonoperating=True)
        def shared_attribute(attribute, default=None):
            values = {getattr(repo.edges[ref.edge_id], attribute) for ref in refs}
            return next(iter(values)) if len(values) == 1 else default
        role = shared_attribute('track_role', 'unknown')
        saved = migrate_track_override(saved, name + '站', role)
        raw = saved.get('station_track', {})
        if raw.get('edge_refs') and {ref['edge_id'] for ref in raw['edge_refs']} != {ref.edge_id for ref in refs}:
            raise ValueError('股道物理引用发生变化，需要迁移核对：' + (saved.get('display_name') or ident))
        tags = [edge.get('way_tags', {}) for edge in source_edges]
        # A main line's ref (e.g. 3043) identifies the railway, never its track.
        numbers = {track_number(t) for t in tags} - {None}
        number = str(saved.get('track_number') or raw.get('track_number') or (next(iter(numbers)) if len(numbers) == 1 else ''))
        track_name = saved.get('display_name') or raw.get('name') or next((t['name'] for t in tags if t.get('name') and t.get('service') in ('yard','siding')), '')
        if saved.get('source') == 'osm_track_ref' and number:
            track_name = number + ('' if number.endswith('道') else '道')
        if not track_name and number:
            track_name = number + ('' if number.endswith('道') else '道')
        provenance = dict(raw.get('provenance', {}))
        provenance.setdefault('track_role', {'source': 'shared_network_edges',
            'edge_ids': [ref.edge_id for ref in refs], 'verification_status': 'derived'})
        if number and 'track_number' not in provenance:
            provenance['track_number'] = {'source': 'workspace_override' if saved.get('track_number') else 'osm_explicit',
                'evidence': number, 'verification_status': saved.get('verification_status', 'osm_explicit')}
        entity = StationTrack(raw.get('id') or ident, station_id, track_name or semantic_track_name(name + '站', role), number or None,
            length_m=refs[-1].end_distance_m, is_virtual=False, edge_refs=refs,
            track_role=raw.get('track_role', role), railway_class=shared_attribute('railway_class', 'unknown'),
            infrastructure_line_id=shared_attribute('infrastructure_line_id'),
            yard_id=raw.get('yard_id') or shared_attribute('yard_id'), zone_id=raw.get('zone_id') or shared_attribute('zone_id'),
            provenance=provenance, legacy_metadata=raw.get('legacy_metadata', saved.get('legacy_metadata', {})),
            source_member_ids=source_sections, snapshot_id=str((directory / 'rail.sqlite').stat().st_mtime_ns),
            verification_status=saved.get('verification_status', 'user_named' if saved.get('source') == 'manual' else 'source_unverified'))
        ident = entity.id
        repo.station_tracks[ident] = entity
        repo.station_track_edges.extend(StationTrackEdge(ident, ref.edge_id, ref.sequence,
            'forward' if ref.forward else 'reverse') for ref in refs)
        points = [p for edge in source_edges for p in edge['coordinates']]
        rows[ident] = {'keys': keys, 'source_edges': tuple(sorted(members)),
                       'saved_overrides': {key: overrides.get(key, {}) for key in keys},
                       'station_source': source, 'station_name': name + '站',
                       'bounds': [[min(p[0] for p in points), min(p[1] for p in points)],
                                  [max(p[0] for p in points), max(p[1] for p in points)]]}
    source_node = str(source).removeprefix('node/')
    context = [f for f in context if f['geometry']['type'] not in ('Polygon', 'MultiPolygon')
               or source_node in set(map(str, f['properties'].get('associated_station_ids', [])))]
    return repo, rows, context


def track_overrides(repo, rows):
    return {source_key: {**rows[key].get('saved_overrides', {}).get(source_key, {}),
            'station_track': asdict(track), 'station_track_id': track.id,
            'source_edge_ids': list(rows[key]['source_edges']),
            'display_name': track.name, 'track_number': track.track_number,
            'source': 'manual' if track.verification_status == 'user_named' else 'station_track_entity',
            'station_source': rows[key]['station_source'], 'station_name': rows[key]['station_name'],
            'bounds': rows[key]['bounds'], 'snapshot': track.snapshot_id, 'version': 3,
            'legacy_metadata': track.legacy_metadata,
            'confidence': None, 'verification_status': track.verification_status}
            for key, track in repo.station_tracks.items() for source_key in rows[key]['keys']}


def automatic_numbering(repo, replace_automatic=False):
    """Assign display aliases only. Official numbering always needs evidence."""
    if not repo.station_tracks:
        return 0
    positions = {}
    for key, track in repo.station_tracks.items():
        coords = [p for ref in track.edge_refs for p in repo.edges[ref.edge_id].coordinates]
        positions[key] = (sum(p[0] for p in coords)/len(coords), sum(p[1] for p in coords)/len(coords))
    values = list(positions.values())
    cx, cy = (sum(p[j] for p in values)/len(values) for j in (0, 1))
    cosine = math.cos(math.radians(cy))
    local = {key: ((x-cx)*cosine, y-cy) for key, (x,y) in positions.items()}
    xx=sum(x*x for x,y in local.values()); yy=sum(y*y for x,y in local.values()); xy=sum(x*y for x,y in local.values())
    angle = .5*math.atan2(2*xy, xx-yy)
    transverse = lambda key: -local[key][0]*math.sin(angle)+local[key][1]*math.cos(angle)
    candidates = [key for key, track in repo.station_tracks.items()
        if not track.track_number and track.verification_status not in ('user_named', 'user_verified', 'official_confirmed')
        and (replace_automatic or 'display_alias' not in track.provenance)]
    for number, key in enumerate(sorted(candidates, key=lambda k: (transverse(k), k)), 1):
        track = repo.station_tracks[key]
        provenance = {**track.provenance, 'display_alias': {'value': f'显示序号 {number}',
            'source': 'automatic_transverse_order', 'verification_status': 'display_only', 'snapshot_id': track.snapshot_id}}
        generic = track.name == '未编号股道' or '暂编' in track.name
        repo.station_tracks[key] = replace(track,
            name=semantic_track_name(repo.stations[track.station_id].name, track.track_role) if generic else track.name,
            provenance=provenance)
    return len(candidates)
