"""Full source audit and evidence-backed proposals for existing RailScope IDs."""
from collections import Counter
from contextlib import closing
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import re
import sqlite3

from railscope.rail_semantics import (classify_railway_class, classify_line_role,
                                    classify_track_role, classify_operational_status)
from railscope.presentation import source_design_speed

try:
    from .rail_style_resolver import speed_band, STYLE_SELECTIONS, CONFIGURED_STYLE_KEYS, DEFAULT_STYLE_KEY
except ImportError:  # desktop/launcher.py also runs directly as a script.
    from rail_style_resolver import speed_band, STYLE_SELECTIONS, CONFIGURED_STYLE_KEYS, DEFAULT_STYLE_KEY

FIELDS = ('railway_class', 'line_role', 'track_role', 'construction_status')
CLASSIFIERS = (classify_railway_class, classify_line_role, classify_track_role, classify_operational_status)


def railway_name(value):
    value = re.sub(r'\s+', '', str(value)).split('·')[0]
    value = value.replace('高铁', '高速铁路').replace('客专', '客运专线')
    return re.sub(r'(铁路|鐵路|线|線)$', '', value)


def reference_index(profiles):
    index = {}
    for profile in profiles:
        if profile.get('kind', 'line') != 'line':
            continue
        index.setdefault(railway_name(profile['name']), []).append({**profile, '_match_kind': 'full'})
        for scope in profile.get('scopes', []):
            scoped = {**profile, 'attributes': scope['attributes'], 'scopes': [scope]}
            index.setdefault(railway_name(scope['name']), []).append({**scoped, '_match_kind': 'scope'})
            # A short alias on one section cannot supply facts for a whole line.
            # Whole-title matches already cover safe suffix/name variants.
    return index


def match_reference(name, profiles):
    index = profiles if isinstance(profiles, dict) else reference_index(profiles)
    candidates = index.get(railway_name(name), [])
    full = [p for p in candidates if p.get('_match_kind') == 'full']
    candidates = full or candidates
    unique = {json.dumps([p['source_url'], p['attributes'], p.get('scopes', [])], sort_keys=True): p
              for p in candidates}
    return {k: v for k, v in next(iter(unique.values())).items() if k != '_match_kind'} if len(unique) == 1 else None


def reference_design(profile):
    """Track design only; never substitute operating or civil-design speeds."""
    attrs = profile.get('attributes', {})
    values = [attrs['track_design_speed_kmh']] if attrs.get('track_design_speed_kmh') else [
        scope.get('attributes', {}).get('track_design_speed_kmh') for scope in profile.get('scopes', [])]
    numbers = []
    for value in values:
        match = re.fullmatch(r'\s*(\d+)\s*(?:km/h|kmh)?\s*', str(value))
        if not match:
            return None, 'unknown'
        numbers.append(int(match[1]))
    bands = {speed_band({'design_speed_kmh': v}) for v in numbers}
    return (numbers[0] if len(set(numbers)) == 1 else None,
            next(iter(bands)) if len(bands) == 1 else 'unknown') if numbers else (None, 'unknown')


def review_group(group, profile=None, official=None):
    result = {key: next(iter(values)) if len(values) == 1 else 'unknown'
              for key in FIELDS if (values := group['values'].get(key))}
    result['mixed_fields'] = [key for key in FIELDS if len(group['values'].get(key, {})) > 1]
    proofs = {}
    for key in FIELDS:
        result.setdefault(key, 'unknown')
        proofs[key] = {'value': result[key], 'source': 'rail_line_review',
                      'verification_status': 'osm_source_compared' if result[key] != 'unknown' else 'unresolved',
                      'confidence': .85 if result[key] != 'unknown' else None,
                      'evidence': '逐来源区间汇总: ' + json.dumps(group['values'].get(key, {}), ensure_ascii=False)}
    if official:
        for key in FIELDS:
            if key not in official.get('facts', {}):
                continue
            # A whole-line statement cannot erase genuinely mixed sections.
            known = set(group['values'].get(key, {})) - {'unknown'}
            value = official['facts'][key]
            if len(known) > 1:
                continue
            if key == 'railway_class' and value == 'conventional' and known & {'freight', 'industrial'}:
                continue  # broad 普速 protection lists also contain specific freight/industrial lines
            result[key] = value
            proofs[key] = {'value': value, 'source': 'rail_line_review',
                           'verification_status': 'source_checked', 'confidence': .95,
                           'source_url': official['source_url'], 'evidence': official['evidence'],
                           'external_source_snapshot': official.get('source_snapshot'),
                           'retrieved_at': official.get('reviewed_at')}
    speeds = group.get('design_speeds', {})
    bands = {speed_band({'design_speed_kmh': s}) if s is not None else 'unknown' for s in speeds}
    result['design_speed_kmh'] = next(iter(speeds)) if len(speeds) == 1 else None
    result['speed_band'] = next(iter(bands)) if len(bands) == 1 else 'unknown'
    result['design_speed_provenance'] = {'source': 'OpenStreetMap',
        'verification_status': 'osm_source_compared' if result['speed_band'] != 'unknown' else 'unresolved',
        'evidence': '轨道设计速度逐区间汇总: ' + json.dumps(speeds, ensure_ascii=False)}
    if profile:
        number, band = reference_design(profile)
        # Different explicit source design standards need section review.
        source_bands = bands - {'unknown'}
        if band != 'unknown' and len(source_bands) <= 1 and (not source_bands or source_bands == {band}):
            result.update(design_speed_kmh=number, speed_band=band)
            result['design_speed_provenance'] = {key: profile.get(key) for key in
                ('source', 'source_url', 'snapshot_id', 'retrieved_at', 'verification_status')}
        if result['railway_class'] == 'unknown' and not (set(group['values']['railway_class']) - {'unknown'}):
            if '高速' in profile['name'] and band in ('300-350', '250-300'):
                result['railway_class'] = 'high_speed'
                proofs['railway_class'] = {'value': 'high_speed', 'source': 'rail_line_review',
                    'source_url': profile['source_url'], 'snapshot_id': profile.get('snapshot_id'),
                    'verification_status': 'external_reference_unverified', 'confidence': .65,
                    'evidence': '精确名称/别名匹配的高速铁路页面及轨道设计速度；非官方资料'}
    if result['railway_class'] != 'high_speed':
        result['speed_band'] = 'unknown'
    result['provenance'] = proofs
    return result


def combine_groups(groups):
    """Summarize an existing user assembly without changing its membership."""
    result = {'values': {field: Counter() for field in FIELDS}, 'design_speeds': Counter(),
              'names': Counter(), 'edge_count': 0, 'facility_edges': 0,
              'rendered_members': 0, 'bounds': [180, 90, -180, -90]}
    for group in groups:
        for field in FIELDS:
            result['values'][field].update(group['values'][field])
        for field in ('design_speeds', 'names'):
            result[field].update(group[field])
        for field in ('edge_count', 'facility_edges', 'rendered_members'):
            result[field] += group[field]
        for index in range(4):
            result['bounds'][index] = (min if index < 2 else max)(result['bounds'][index], group['bounds'][index])
    return result


@lru_cache(maxsize=65536)
def _source_facts(encoded):
    tags = json.loads(encoded)
    return tuple(classifier(tags).value for classifier in CLASSIFIERS), source_design_speed(tags)


def scan_groups(directory, progress=print):
    groups = {}
    source = Path(directory)/'rail.sqlite'
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        rows = db.execute("SELECT g.group_id,f.data FROM features f JOIN rail_feature_groups g ON g.feature_id=f.id WHERE f.kind='rail' ORDER BY f.id")
        for count, (key, raw) in enumerate(rows, 1):
            feature = json.loads(raw)
            props = feature['properties']
            tags = props.get('way_tags', {})
            facts, speed = _source_facts(json.dumps(tags, sort_keys=True))
            for ident in {key, props.get('line_id')} - {None}:
                group = groups.setdefault(ident, {'values': {field: Counter() for field in FIELDS},
                                                 'design_speeds': Counter(), 'names': Counter(),
                                                 'edge_count': 0, 'facility_edges': 0, 'rendered_members': 0,
                                                 'bounds': [180, 90, -180, -90]})
                for field, value in zip(FIELDS, facts):
                    group['values'][field][value] += 1
                group['design_speeds'][speed] += 1
                group['names'][props.get('line_name') or tags.get('name') or ''] += 1
                group['edge_count'] += 1
                group['rendered_members'] += ident == key
                group['facility_edges'] += tags.get('service') in ('yard', 'siding', 'crossover', 'spur')
                coordinates = feature['geometry']['coordinates']
                for x, y, *_ in coordinates:
                    group['bounds'][0] = min(group['bounds'][0], x)
                    group['bounds'][1] = min(group['bounds'][1], y)
                    group['bounds'][2] = max(group['bounds'][2], x)
                    group['bounds'][3] = max(group['bounds'][3], y)
            if count % 100000 == 0:
                progress(f'已逐区间核查 {count:,} 条来源记录…')
    _source_facts.cache_clear()
    return groups


def reviewed_styles(styles):
    """Complete the existing user's palette for every valid selector tuple."""
    value = deepcopy(styles)
    configured = set(value[CONFIGURED_STYLE_KEYS])
    palette = {'conventional': '#1565c0', 'freight': '#000000', 'industrial': '#8b5a2b',
               'metro': '#7b1fa2', 'other': '#667887', 'unknown': '#667887'}
    for key, (group, category, function, band) in STYLE_SELECTIONS.items():
        if key in configured:
            continue
        template = 'track.' + category + '.main_line' + ('.'+band if category == 'high_speed' else '')
        item = deepcopy(value.get(template, value[DEFAULT_STYLE_KEY]))
        if category != 'high_speed':
            item['color'] = palette[category]
        if category == 'high_speed' and band == 'unknown':
            item['color'] = value[DEFAULT_STYLE_KEY]['color']
        if group == 'station':
            item['width'] = min(item['width'], 1.5)
        value[key] = item
    value[CONFIGURED_STYLE_KEYS] = sorted(STYLE_SELECTIONS)
    return value
