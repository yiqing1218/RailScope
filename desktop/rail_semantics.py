"""Desktop DTO/override adapter for the shared railway semantic contract.

No topology, identity or geometry is inferred here. Directory placement and
legacy style settings are deliberately excluded from semantic overrides.
"""
from copy import deepcopy
import math
from railscope.rail_semantics import edge_semantics, RAILWAY_CLASSES, LINE_ROLES, TRACK_ROLES, OPERATIONAL_STATUSES

SEMANTIC_FIELDS = ('railway_class', 'line_role', 'track_role', 'facility_id',
                   'yard_id', 'zone_id', 'construction_status')


def semantic_record(value, override=None):
    result = edge_semantics(value)
    tags = value.get('way_tags', value.get('source_tags', {}))
    legacy_facility = value.get('track_type') in {
        '高速铁路站场股道', '普速铁路站场股道', '货运站场股道', '站场股道（类型待核对）',
        '渡线 / 道岔连接轨', '车辆段 / 检修线', '折返线'}
    result['facility_only'] = bool(value.get('facility_only') or
        tags.get('service') in ('yard', 'siding') or
        (not any(key in value for key in ('railway_class', 'track_role')) and legacy_facility))
    custom = (override or {}).get('rail_semantics', {})
    if not isinstance(custom, dict):
        raise ValueError('铁路语义覆盖必须是对象')
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
    result['provenance'] = deepcopy(result.get('provenance', {}))
    for key in SEMANTIC_FIELDS:
        if key not in custom:
            continue
        result[key] = custom[key]
        result['provenance'][key] = {
            'value': custom[key], 'source': 'workspace_override',
            'snapshot_id': custom.get('snapshot_id'),
            'evidence': custom.get('evidence', '用户工作区核验'),
            'verification_status': custom.get('verification_status', 'user_verified'),
            'confidence': custom.get('confidence'),
        }
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
    facts = semantic_record(value)
    role = facts['track_role']
    if role not in ('main_track', 'unknown'):
        return False
    tags = value.get('way_tags', value.get('source_tags', {}))
    # Yard/siding evidence identifies facility context, not a professional role.
    if tags.get('service') in ('yard', 'siding') or value.get('facility_only'):
        return False
    return not str(value.get('source_name', value.get('name', ''))).startswith('未命名轨道')
