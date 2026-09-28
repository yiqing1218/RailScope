"""Track numbering evidence and legacy display migration, without geometry."""
from copy import deepcopy
import re


TRACK_ROLE_LABELS = {
    'main_track': '站内正线', 'arrival_departure_track': '到发线',
    'shunting_track': '调车线', 'lead_track': '牵出线', 'freight_track': '货物线',
    'depot_track': '段管线', 'locomotive_running_track': '机车走行线',
    'maintenance_track': '检修线', 'stabling_track': '存车线',
    'turnback_track': '折返线', 'crossover': '渡线', 'spur_track': '支接用途轨道',
    'safety_siding': '安全线', 'escape_siding': '避难线',
    'other_station_track': '其他站线', 'unknown': '站线（用途待核实）',
}

_INTERNAL_ID = re.compile(r'^(?:STTR|TRK|NE|RS|IL)-|^[A-Z]+-T\d+$')


def semantic_track_name(station_name, role='unknown'):
    label = TRACK_ROLE_LABELS.get(role, TRACK_ROLE_LABELS['unknown'])
    return f'{station_name} · {label}' if station_name else label


def track_number(tags):
    """Only explicit source numbering; an internal ID is never a number."""
    value = str(tags.get('railway:track_ref') or '').strip()
    if value and not _INTERNAL_ID.match(value):
        return value
    match = re.search(r'([0-9ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)道$', tags.get('name', ''))
    if match:
        return match[1]
    value = str(tags.get('ref') or '')
    if tags.get('service') in ('yard', 'siding') and re.fullmatch(r'\d{1,3}|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+', value):
        return value
    return None


def migrate_track_override(value, station_name='', track_role='unknown'):
    """Return a copy. Preserve old generated numbers only as display metadata."""
    result = deepcopy(value)
    raw = result.get('station_track', {})
    manual = result.get('source') == 'manual' or result.get('verification_status') in (
        'user_named', 'user_verified', 'official_confirmed')
    number = result.get('track_number') or raw.get('track_number')
    internal = bool(number) and (_INTERNAL_ID.match(str(number)) or
        str(number) in {str(v) for v in (*result.get('source_edge_ids', []), raw.get('id'), result.get('station_track_id')) if v})
    automatic = not manual and (result.get('source') == 'automatic_yard_track_number'
        or raw.get('verification_status') == 'automatic_reference' and bool(raw.get('track_number'))
        or result.get('verification_status') == 'automatic_reference' and bool(result.get('track_number')))
    if not automatic and not internal:
        return result
    legacy = {key: result[key] for key in ('display_name', 'track_number', 'source', 'snapshot', 'version') if key in result}
    if raw:
        legacy['station_track_name'] = raw.get('name')
        legacy['station_track_number'] = raw.get('track_number')
    name = (result.get('display_name') or raw.get('name')) if internal and manual else None
    name = name or semantic_track_name(station_name or result.get('station_name'), track_role)
    result.update(track_number=None, display_name=name, source='station_track_semantic_label', version=3)
    result['legacy_metadata'] = {**result.get('legacy_metadata', {}), 'automatic_numbering': legacy}
    result['display_alias'] = legacy.get('display_name') or legacy.get('station_track_name')
    if raw:
        raw.update(track_number=None, name=name)
        raw['legacy_metadata'] = {**raw.get('legacy_metadata', {}), 'automatic_numbering': legacy}
    return result
