"""Workspace presentation names, independent of OSM facts and domain identity."""
import json

try:
    from .yard_track_names import yard_track_key
    from .rail_semantics import semantic_record
    from .rail_style_resolver import style_key
    from .station_track_semantics import migrate_track_override
except ImportError:
    from yard_track_names import yard_track_key
    from rail_semantics import semantic_record
    from rail_style_resolver import style_key
    from station_track_semantics import migrate_track_override


_EDGE_ROLES_CACHE = {}


def _edge_roles(library):
    """Cache the expensive edge-role scan; it does not depend on overrides."""
    key = (str(library.path), library.path.stat().st_mtime_ns)
    cached = _EDGE_ROLES_CACHE.get(key)
    if cached is not None:
        return cached
    roles = {}
    with library.connect() as db:
        for line, kind in db.execute('SELECT line_id,track_type FROM edges GROUP BY line_id,track_type'):
            roles.setdefault(line, set()).add(kind)
    if len(_EDGE_ROLES_CACHE) > 8:
        _EDGE_ROLES_CACHE.clear()
    _EDGE_ROLES_CACHE[key] = roles
    return roles


def rail_line_presentation(library):
    """One compact style/parent lookup, shared by every viewport of a snapshot."""
    if not hasattr(library, 'connect'):
        return {}
    cached = getattr(library, '_line_presentation', None)
    if cached is not None:
        return cached
    roles = _edge_roles(library)
    result = {}
    main_roles = {'高速铁路线', '普速铁路线', '货运铁路线'}
    snapshot = str(library.path.stat().st_mtime_ns)
    for line, kinds in roles.items():
        known = kinds - {'未确认类型'}
        style = next(iter(known)) if len(known) == 1 and known <= main_roles else None
        metadata = library.metadata.get(line, {})
        custom = metadata.get('track_type')
        speed = metadata.get('technical_attributes', {}).get('design_speed_kmh')
        members = library.workspace.members(line)
        # Most source lines are single unnamed tracks. Sending an identity
        # entry for every one would bloat the browser config by tens of MB.
        if len(kinds) == 1 and not custom and not speed and members == [line]:
            continue
        for source in members:
            result[source] = {'line_id': line, 'fallback_type': style,
                              'override_type': custom, 'snapshot': snapshot, 'design_speed_kmh': speed}
        result.setdefault(line, {'line_id': line, 'fallback_type': style,
                                'override_type': custom, 'snapshot': snapshot, 'design_speed_kmh': speed})
    library._line_presentation = result
    return result


def decode_properties(collection):
    """MapLibre picked features serialize nested GeoJSON properties as JSON."""
    for feature in collection.get('features', []):
        props = feature.get('properties', {})
        for key, value in props.items():
            if isinstance(value, str) and value.lstrip()[:1] in ('{', '['):
                try:
                    props[key] = json.loads(value)
                except ValueError:
                    pass
    return collection


def apply_rail_presentation(collection, presentation, overrides=None):
    """Keep physical IDs/facts; inherit only missing display styles on one line."""
    overrides = overrides or {}
    decode_properties(collection)
    for feature in collection.get('features', []):
        props = feature.get('properties', {})
        for field in ('line_ids', 'operating_line_ids', 'construction_line_ids'):
            if field in props:
                props[field] = sorted({presentation.get(key, {}).get('line_id', key) for key in props[field]})
        source = props.get('source_line_id') or props.get('line_id')
        entry = presentation.get(source, {})
        if props.get('network_edge_id'):
            tags = props.get('way_tags', {})
            from railscope.presentation import source_design_speed
            speed = next((overrides[key]['technical_attributes']['design_speed_kmh']
                          for key in (object_key(props), props.get('catalog_group_id'), entry.get('line_id'), source)
                          if key in overrides and overrides[key].get('technical_attributes', {}).get('design_speed_kmh')),
                         None) or entry.get('design_speed_kmh') or source_design_speed(tags)
            if speed:
                from railscope.presentation import design_speed
                try:
                    props['design_speed_kmh'] = design_speed(speed)
                except ValueError:
                    pass
        if not entry or not props.get('network_edge_id'):
            continue
        props['source_line_id'] = source
        props['line_id'] = entry['line_id']
        props.pop('display_track_type', None)
        props.pop('display_style_provenance', None)
        custom = next((overrides[key]['track_type'] for key in
                       (object_key(props), props.get('catalog_group_id'), props['line_id'])
                       if key in overrides and overrides[key].get('track_type')), entry.get('override_type'))
        style = custom or (entry.get('fallback_type') if props.get('track_type') == '未确认类型' else None)
        if style:
            props['display_track_type'] = style
            props['display_style_provenance'] = {
                'source': 'workspace_override' if custom else 'same_line_unambiguous_track_style',
                'snapshot': entry['snapshot'], 'version': 1,
                'verification_status': 'user_classified' if custom else 'display_only_inference',
                'confidence': None}
    for feature in collection.get('features', []):
        props = feature.get('properties', {})
        if not props.get('network_edge_id'):
            continue
        props.update(semantic_record(props))
        for key in (props.get('line_id'), props.get('catalog_group_id'), object_key(props)):
            if key in overrides:
                props.update(semantic_record(props, overrides[key]))
        props['rail_style_key'] = style_key(props)
        # Historical user style classifications remain presentation only.
        legacy = next((overrides[key].get('track_type') for key in
            (object_key(props), props.get('catalog_group_id'), props.get('line_id'))
            if key in overrides and overrides[key].get('track_type')), None)
        if legacy:
            props['rail_style_key'] = style_key({
                **semantic_record({'track_type': legacy}),
                'design_speed_kmh': props.get('design_speed_kmh')})
    return collection


def object_key(properties):
    for field in ('service_id', 'network_edge_id', 'network_node_id', 'section_id', 'infrastructure_id',
                  'catalog_id', 'station_id', 'route_key'):
        value = properties.get(field)
        if value is not None and str(value) not in ('', 'node/None', 'way/None', 'relation/None'):
            return 'object:' + field + ':' + str(value)
    for field in ('osm_node_id', 'osm_way_id', 'osm_relation_id'):
        if properties.get(field) is not None:
            return 'object:' + field + ':' + str(properties[field])
    return None


def apply_names(collection, overrides=None, line_names=None, way_names=None, station_directory=None):
    overrides, line_names, way_names = overrides or {}, line_names or {}, way_names or {}
    decode_properties(collection)
    if station_directory is not None:
        try:
            from .rail_station_directory import apply_station_names
        except ImportError:
            from rail_station_directory import apply_station_names
        apply_station_names(collection, station_directory, overrides)
    for feature in collection.get('features', []):
        props = feature.get('properties', {})
        group_line_edit = overrides.get(props.get('catalog_group_id'), {})
        object_line_edit = overrides.get(object_key(props), {})
        if 'line_name' in group_line_edit:
            props['line_name'] = group_line_edit['line_name']
        if 'line_name' in object_line_edit:
            props['line_name'] = object_line_edit['line_name']
        yard_key = yard_track_key(props)
        track = migrate_track_override(overrides.get(yard_key, {}),
            station_name=props.get('station_name', ''), track_role=props.get('track_role', 'unknown'))
        if track.get('station_track_id'):
            props['station_track_id'] = track['station_track_id']
            props['track_number'] = track.get('track_number')
            props['track_name'] = track.get('display_name')
        if yard_key:
            object_edit = overrides.get(object_key(props), {})
            if 'line_name' in object_edit:
                props['line_name'] = object_edit['line_name']
            group_edit = overrides.get(props.get('catalog_group_id'), {})
            line_name = str(props.get('line_name') or group_edit.get('line_name') or '').strip()
            if line_name.startswith('未命名'):
                line_name = ''
            label = (line_name or object_edit.get('display_name') or group_edit.get('display_name') or
                     props.get('display_name') or track.get('display_name'))
            if label:
                props['display_name'] = label
                props['line_display_name'] = label
            continue
        node = props.get('osm_node_id')
        keys = [object_key(props), yard_track_key(props)]
        keys += ['object:service_id:' + key for key in props.get('service_alias_ids', [])]
        if node is not None:
            keys += [f'switch:node/{node}', f'node:{node}', f'station:node/{node}']
        if props.get('infrastructure_id'):
            keys.append('station:' + str(props['infrastructure_id']))
        keys += [props.get('catalog_group_id'), props.get('line_id'), props.get('catalog_id'),
                 props.get('station_id'), str(props.get('route_relation_id')), str(props.get('osm_relation_id'))]
        custom = next((overrides[key] for key in keys if key in overrides and overrides[key].get('display_name')), {})
        custom = migrate_track_override(custom, station_name=props.get('station_name', ''),
                                        track_role=props.get('track_role', 'unknown'))
        name = custom.get('display_name')
        if custom.get('source') == 'automatic_station_group' and not str(props.get('line_name', '')).startswith('未命名轨道'):
            name = None
            custom = {}
        if not name and props.get('line_id'):
            name = line_names.get(props['line_id']) or way_names.get(str(props.get('osm_way_id')))
        if not name:
            continue
        # Old name exports include the identity suffix. The ID remains a
        # separate property and is never needed in the on-map text label.
        for ident in (props.get('line_id'), props.get('catalog_group_id')):
            if ident and name.endswith(' · ' + str(ident)):
                name = name[:-(len(str(ident)) + 3)]
        props['display_name'] = name
        if props.get('service_id'):
            props.update({key: custom[key] for key in ('province', 'city', 'county') if key in custom})
        if props.get('line_id') or feature.get('geometry', {}).get('type') in ('LineString', 'MultiLineString'):
            props['line_display_name'] = name
        props['display_name_source'] = custom.get('source', 'workspace_override')
        props['display_name_verification_status'] = custom.get('verification_status', 'user_named')
        if custom.get('snapshot'):
            props['display_name_snapshot'] = custom['snapshot']
        if 'confidence' in custom:
            props['display_name_confidence'] = custom['confidence']
    return collection
