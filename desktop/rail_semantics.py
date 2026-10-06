"""Desktop DTO/override adapter for the shared railway semantic contract.

No topology, identity or geometry is inferred here. Directory placement and
legacy style settings are deliberately excluded from semantic overrides.
"""
import math
from railscope.rail_semantics import edge_semantics, RAILWAY_CLASSES, LINE_ROLES, TRACK_ROLES, OPERATIONAL_STATUSES

SEMANTIC_FIELDS = ('railway_class', 'line_role', 'track_role', 'facility_id',
                   'yard_id', 'zone_id', 'construction_status')


def semantic_record(value, override=None, *, include_provenance=True):
    result = edge_semantics(value, include_provenance=include_provenance)
    tags = value.get('way_tags', value.get('source_tags', {}))
    legacy_facility = value.get('track_type') in {
        '高速铁路站场股道', '普速铁路站场股道', '货运站场股道', '站场股道（类型待核对）',
        '渡线 / 道岔连接轨', '车辆段 / 检修线', '折返线'}
    result['facility_only'] = bool(value.get('facility_only') or
        tags.get('service') in ('yard', 'siding') or
        legacy_facility or str(value.get('catalog_group_id', '')).startswith('ST-'))
    custom = (override or {}).get('rail_semantics', {})
    if not isinstance(custom, dict):
        raise ValueError('铁路语义覆盖必须是对象')
    custom = dict(custom)
    # Old editors saved a free-text status which never reached rendering/routing.
    # Decode only recognized workspace values; raw OSM attributes stay intact.
    if 'construction_status' not in custom:
        from_status = (override or {}).get('technical_attributes', {}).get('operating_status')
        try:
            from .rail_style_resolver import STATUS_ALIASES
        except ImportError:
            from rail_style_resolver import STATUS_ALIASES
        if from_status in STATUS_ALIASES:
            custom['construction_status'] = STATUS_ALIASES[from_status]
        elif type((override or {}).get('construction')) is bool and (
                'construction_status' not in (override or {}) or
                override['construction'] != value.get('construction')):
            custom['construction_status'] = 'construction' if override['construction'] else 'operating'
    for key, choices in (('railway_class', RAILWAY_CLASSES), ('line_role', LINE_ROLES),
                         ('track_role', TRACK_ROLES), ('construction_status', OPERATIONAL_STATUSES)):
        if key in custom and (not isinstance(custom[key], str) or custom[key] not in choices):
            raise ValueError('铁路语义字段无效：' + key)
    for key, prefixes in (('facility_id', ('ST-', 'OP-')),
                          ('yard_id', ('Y-', 'YARD-')),
                          ('zone_id', ('Z-', 'ZONE-'))):
        if custom.get(key) is not None and (not isinstance(custom[key], str) or not custom[key].startswith(prefixes)):
            raise ValueError('设施引用必须使用稳定 RailScope ID：' + key)
    confidence = custom.get('confidence')
    if confidence is not None and (type(confidence) not in (int,float) or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError('置信度必须位于 0–1')
    # Decode old presentation labels into the SAME canonical fields. Keep the
    # migration evidence explicit; directory placement never determines a fact.
    legacy_type = str((override or {}).get('track_type') or value.get('track_type') or '')
    name = str(value.get('source_name') or value.get('line_name') or value.get('name') or '')
    hints = {}
    if result['railway_class'] == 'unknown':
        hints['railway_class'] = {'高速铁路线': 'high_speed', '高速铁路站场股道': 'high_speed',
            '普速铁路线': 'conventional', '普速铁路站场股道': 'conventional',
            '货运铁路线': 'freight', '货运站场股道': 'freight'}.get(legacy_type, 'unknown')
    if legacy_type == '联络线 / 匝道' or any(s in name for s in ('联络', '疏解', '联结线', '联线', '货联', '下联', '上联')):
        hints['line_role'] = 'connecting_line'
    elif legacy_type == '支线 / 岔道' and result['line_role'] == 'unknown':
        hints['line_role'] = 'branch_line'
    if result['track_role'] == 'unknown':
        hints['track_role'] = {'渡线 / 道岔连接轨': 'crossover',
            '车辆段 / 检修线': 'maintenance_track', '折返线': 'turnback_track'}.get(legacy_type, 'unknown')
    for key, hint in hints.items():
        if hint != 'unknown' and key not in custom:
            result[key] = hint
            if include_provenance:
                result['provenance'][key] = {'value': hint, 'source': 'legacy_migration',
                    'snapshot_id': value.get('snapshot_id') or value.get('source_version'),
                    'evidence': '旧线路属性/来源名称: ' + legacy_type + ' / ' + name,
                    'verification_status': 'inferred', 'confidence': .3}
    for key in SEMANTIC_FIELDS:
        if key not in custom:
            continue
        result[key] = custom[key]
        if include_provenance:
            result['provenance'][key] = {
                'value': custom[key], 'source': 'workspace_override',
                'snapshot_id': custom.get('snapshot_id'),
                'evidence': custom.get('evidence', '用户工作区核验'),
                'verification_status': custom.get('verification_status', 'user_verified'),
                'confidence': custom.get('confidence'),
            }
    if result['track_role'] == 'main_track':
        result['facility_only'] = False
    elif custom.get('track_role') not in (None, 'unknown'):
        result['facility_only'] = True
    elif (override or {}).get('line_kind') == 'station':
        result['facility_only'] = True
    elif (override or value).get('line_kind') == 'track' and result['track_role'] == 'unknown':
        result['facility_only'] = False
    result['construction'] = result['construction_status'] in ('construction', 'planned', 'disused')
    return result


def aggregate_semantics(values):
    """A mixed membership is explicitly unknown, never the first edge's fact."""
    rows = list(values)
    result = {}
    for key in SEMANTIC_FIELDS:
        entries = {row.get(key) for row in rows}
        result[key] = next(iter(entries)) if len(entries) == 1 else (
            None if key.endswith('_id') else 'unknown')
    provenance = {key: {'value': result[key], 'source': 'shared_edge_membership',
        'verification_status': 'derived', 'evidence': '逐属性汇总；不一致或未知时保持 unknown',
        'snapshot_id': None, 'confidence': None,
        'sources': sorted({str(row.get('provenance', {}).get(key, {}).get('source')) for row in rows
                           if row.get('provenance', {}).get(key, {}).get('source')}),
        'snapshots': sorted({str(row.get('provenance', {}).get(key, {}).get('snapshot_id')) for row in rows
                           if row.get('provenance', {}).get(key, {}).get('snapshot_id')})}
        for key in SEMANTIC_FIELDS}
    result.update(verification_status='derived', confidence=None,
                  facility_only=bool(rows) and all(row.get('facility_only') for row in rows), provenance=provenance)
    return result


def is_business_line(value):
    facts = semantic_record(value, include_provenance=False)
    role = facts['track_role']
    if role not in ('main_track', 'unknown'):
        return False
    tags = value.get('way_tags', value.get('source_tags', {}))
    # Yard/siding evidence identifies facility context, not a professional role.
    if tags.get('service') in ('yard', 'siding') or value.get('facility_only'):
        return False
    return not str(value.get('source_name', value.get('name', ''))).startswith('未命名轨道')
