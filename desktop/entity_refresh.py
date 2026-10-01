"""Field-level view invalidation; presentation owners remain adapter keys."""
NAME_FIELDS = {'display_name', 'line_name', 'assembly_name'}
PLACEMENT_FIELDS = {'folder_path', 'directory_view', 'archived', 'line_kind',
                    'station_id', 'station_source', 'station_assignment', 'station_type'}
ROUTING_FIELDS = {'connected_lines', 'connected_line_ids', 'rail_semantics',
                  'track_type', 'line_kind', 'assembly_id', 'members', 'active', 'geometry'}
STYLE_FIELDS = {'rail_semantics', 'track_type', 'color', 'width', 'line_kind'}


def field_delta(before, after):
    before, after = before or {}, after or {}
    delta = {field: after.get(field) for field in before.keys() | after.keys()
             if before.get(field) != after.get(field)}
    if 'technical_attributes' in delta:
        old, new = before.get('technical_attributes') or {}, after.get('technical_attributes') or {}
        delta['technical_attributes'] = {key: new.get(key) for key in old.keys() | new.keys()
                                         if old.get(key) != new.get(key)}
    return delta


def needs_map(change):
    return bool((NAME_FIELDS | STYLE_FIELDS | {'geometry', 'archived', 'station_type'}) & change.keys()) or (
        'technical_attributes' in change and any(key in (change['technical_attributes'] or {})
            for key in ('speed_band', 'design_speed_kmh')))


def ordinary_attributes(change):
    return not bool((NAME_FIELDS | PLACEMENT_FIELDS | ROUTING_FIELDS | STYLE_FIELDS | {'geometry'}) & change.keys()) and not needs_map(change)
