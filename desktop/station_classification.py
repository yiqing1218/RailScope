"""Two independent station presentation axes, with auditable source evidence.

This adapter changes directory placement only; infrastructure and operating IDs
remain owned by the shared domain repository.
"""
from functools import lru_cache
import json
from pathlib import Path

TECHNICAL_TYPES = ('中间站', '区段站', '编组站', '待核实')
BUSINESS_TYPES = ('客运站', '货运站', '客货运站', '待核实')
ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'data/catalog/station_classification_reference.json'
CLASSIFICATION_FIELDS = ('technical_type', 'business_type', 'classification_provenance')
OTHER_TYPES = frozenset((
    '线路所', '信号所', '乘降所', '动车段', '动车所', '客车整备所', '动车所/客整所',
    '货场', '车场', '到达场', '出发场', '到发场', '调车场', '存车场', '整备场',
    '车辆段', '客车车辆段', '货车车辆段', '机务段', '机务折返段', '机车整备所',
    '检修站', '整修站', '检修所', '站修所', '列检所', '工务段', '电务段',
    '供电段', '综合维修段',
))


@lru_cache(maxsize=2)
def _references(path, stamp):
    if stamp is None:
        return {}
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if payload.get('schema') != 'railscope.station-classification-reference.v1':
        raise ValueError('车站分类资料版本无效')
    return payload['stations']


def classification(record, custom=None):
    record, custom = record or {}, custom or {}
    props = record.get('properties', {})
    tags = props.get('node_tags', {})
    legacy = custom.get('station_type') or record.get('station_type', '未定义')
    stamp = REFERENCE.stat().st_mtime_ns if REFERENCE.exists() else None
    ref = _references(str(REFERENCE), stamp).get(record.get('id'), {})
    matched = ref.get('coordinates') == record.get('coordinates')
    other = legacy in OTHER_TYPES or (
        record.get('kind') in ('yard', 'depot', 'workshop', 'works', 'engine_shed')
        and legacy not in TECHNICAL_TYPES[:-1]
        and legacy not in BUSINESS_TYPES[:-1]
    ) or (legacy == '编组站' and str(record.get('name', '')).endswith('场'))
    # OSM railway=yard can describe an entire named freight/marshalling station.
    # Upgrade only station-like yard owners with location-matched explicit evidence;
    # named depots and individual station yards remain under Other.
    station_yard_types = ('车场', '工业站', '港湾站', '集装箱办理站')
    if legacy in station_yard_types and matched and any(ref.get(field) in allowed[:-1]
            for field,allowed in (('technical_type',TECHNICAL_TYPES),('business_type',BUSINESS_TYPES))):
        other = False
    result = {'technical_type': '待核实', 'business_type': '待核实',
              'classification_group': '其他' if other else '车站',
              'classification_provenance': {}}
    if other:
        return result
    source = record.get('station_type_provenance', {
        'source': 'legacy_workspace_type', 'snapshot': 'legacy',
        'verification_status': 'source_unverified', 'confidence': None})
    if legacy in TECHNICAL_TYPES[:-1]:
        result['technical_type'] = legacy
        result['classification_provenance']['technical_type'] = source
    passenger, freight = tags.get('passenger'), tags.get('freight')
    inferred_exclusive = ('station_type' not in custom and
        ((legacy == '客运站' and passenger == 'yes' and freight in (None, '')) or
         (legacy == '货运站' and freight == 'yes' and passenger in (None, ''))))
    if legacy in BUSINESS_TYPES[:-1] and not inferred_exclusive:
        result['business_type'] = legacy
        result['classification_provenance']['business_type'] = source
    # A single positive passenger/freight flag does not establish exclusivity.
    explicit = {('yes', 'yes'): '客货运站', ('yes', 'no'): '客运站',
                ('no', 'yes'): '货运站'}.get((passenger, freight))
    if explicit:
        result['business_type'] = explicit
        result['classification_provenance']['business_type'] = source
    # The reference must still identify this source record at its location.
    if matched:
        for field, allowed in (('technical_type', TECHNICAL_TYPES), ('business_type', BUSINESS_TYPES)):
            if ref.get(field) in allowed[:-1]:
                result[field] = ref[field]
                result['classification_provenance'][field] = ref.get('provenance', {}).get(field, {})
    # Existing explicit human choices have priority over public reference data.
    for field, allowed in (('technical_type', TECHNICAL_TYPES), ('business_type', BUSINESS_TYPES)):
        if custom.get('station_type') in allowed[:-1]:
            result[field] = custom['station_type']
            result['classification_provenance'][field] = {
                'source':'manual_workspace_legacy_type','snapshot':'workspace',
                'verification_status':'manual','confidence':None}
    for field, allowed in (('technical_type', TECHNICAL_TYPES), ('business_type', BUSINESS_TYPES)):
        if field in custom and custom[field] in allowed:
            result[field] = custom[field]
            result['classification_provenance'][field] = custom.get('classification_provenance', {}).get(field, {
                'source': 'manual_workspace', 'snapshot': 'workspace',
                'verification_status': 'manual', 'confidence': None})
    return result


def station_catalog_path(record, custom=None):
    try:
        from .catalog_metadata import station_directory_path
    except ImportError:
        from catalog_metadata import station_directory_path
    geographic = station_directory_path(record, custom)
    values = classification(record, custom)
    if values['classification_group'] == '其他':
        legacy = (custom or {}).get('station_type') or record.get('station_type', '未定义')
        return (*geographic[:2], '其他', legacy, *geographic[2:])
    technical = values['technical_type']
    business = values['business_type']
    return (*geographic[:2], technical if technical != '待核实' else '技术作业待核实',
            business if business != '待核实' else '业务性质待核实', *geographic[2:])


def station_destination_changes(path):
    """Translate a tree drop into fields, without duplicating taxonomy folders."""
    path = list(path)
    if path[:1] == ['已归档']:
        path = path[1:]
    changes = {}
    labels = {*TECHNICAL_TYPES[:-1], '技术作业待核实', '其他'}
    index = next((i for i in range(1, min(3, len(path))) if path[i] in labels), None)
    if index is None:
        return {'folder_path': path}
    label = path[index]
    end = index + 1
    if label == '其他':
        if len(path) > end and path[end] in OTHER_TYPES:
            end += 1
    else:
        changes['technical_type'] = '待核实' if label == '技术作业待核实' else label
        if len(path) > end and path[end] in {*BUSINESS_TYPES[:-1], '业务性质待核实'}:
            changes['business_type'] = '待核实' if path[end] == '业务性质待核实' else path[end]
            end += 1
    changes['folder_path'] = path[:index] + path[end:]
    return changes
