"""Workspace presentation names, independent of OSM facts and domain identity."""


def object_key(properties):
    for field in ('network_edge_id', 'network_node_id', 'section_id', 'infrastructure_id',
                  'catalog_id', 'station_id', 'route_key', 'service_id'):
        value = properties.get(field)
        if value is not None and str(value) not in ('', 'node/None', 'way/None', 'relation/None'):
            return 'object:' + field + ':' + str(value)
    for field in ('osm_node_id', 'osm_way_id', 'osm_relation_id'):
        if properties.get(field) is not None:
            return 'object:' + field + ':' + str(properties[field])
    return None


def apply_names(collection, overrides=None, line_names=None, way_names=None, station_directory=None):
    overrides, line_names, way_names = overrides or {}, line_names or {}, way_names or {}
    if station_directory is not None:
        try:
            from .rail_station_directory import apply_station_names
        except ImportError:
            from rail_station_directory import apply_station_names
        apply_station_names(collection, station_directory, overrides)
    for feature in collection.get('features', []):
        props = feature.get('properties', {})
        node = props.get('osm_node_id')
        keys = [object_key(props)]
        if node is not None:
            keys += [f'switch:node/{node}', f'node:{node}', f'station:node/{node}']
        if props.get('infrastructure_id'):
            keys.append('station:' + str(props['infrastructure_id']))
        keys += [props.get('catalog_group_id'), props.get('line_id'), props.get('catalog_id'),
                 props.get('station_id'), str(props.get('route_relation_id')), str(props.get('osm_relation_id'))]
        custom = next((overrides[key] for key in keys if key in overrides and overrides[key].get('display_name')), {})
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
        if props.get('line_id') or feature.get('geometry', {}).get('type') in ('LineString', 'MultiLineString'):
            props['line_display_name'] = name
        props['display_name_source'] = custom.get('source', 'workspace_override')
        props['display_name_verification_status'] = custom.get('verification_status', 'user_named')
        if custom.get('snapshot'):
            props['display_name_snapshot'] = custom['snapshot']
        if 'confidence' in custom:
            props['display_name_confidence'] = custom['confidence']
    return collection
