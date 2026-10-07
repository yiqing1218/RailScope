"""User-requested name classification, separate from immutable OSM attributes."""

CONNECTING_LINE_COLOR = '#2e7d32'
NAME_RULE_VERSION = 'connecting-line-name-v1'


def connecting_line_name(value, override=None):
    edit = override or {}
    name = next((str(row[field]).strip() for row, fields in (
        (edit, ('display_name', 'line_name', 'assembly_name')),
        (value, ('display_name', 'source_name', 'line_name', 'name')))
        for field in fields if row.get(field)), '')
    return name if any(word in name for word in ('联络线', '连络线')) else ''


def connecting_line_override(value, override, snapshot):
    """Keep other classifications/evidence and correct only the network role."""
    name = connecting_line_name(value, override)
    if not name:
        return {}
    semantics = dict(override.get('rail_semantics') or {})
    proof = {'value': 'connecting_line', 'source': 'user_name_classification',
             'snapshot_id': snapshot, 'version': NAME_RULE_VERSION,
             'verification_status': 'user_classified_by_name', 'confidence': None,
             'evidence': '按用户要求，名称含联络线或连络线：' + name}
    semantics.update(line_role='connecting_line',
        provenance={**semantics.get('provenance', {}), 'line_role': proof})
    # Retain the scope of an automatic line summary so its unknown/mixed
    # fields cannot overwrite explicit lifecycle or yard roles on real edges.
    semantics.setdefault('source', 'workspace_override')
    semantics.setdefault('snapshot_id', snapshot)
    result = {'rail_semantics': semantics, 'color': CONNECTING_LINE_COLOR}
    if (override.get('track_type') or value.get('track_type')) == '支线 / 岔道':
        result['track_type'] = '联络线 / 匝道'
    return result
