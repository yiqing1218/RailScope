"""Editable line relationships use the same station overrides as corridor selection."""


def line_relationships(library, line_ids):
    line_ids = list(dict.fromkeys(library.workspace.canonical(value) for value in line_ids))
    stations, connected = {}, {}
    with library.connect() as db:
        for line_id in line_ids:
            if line_id not in library.lines:
                continue
            stations.update(library.line_stations(line_id))
            custom = library.metadata.get(line_id, {})
            if 'connected_line_ids' in custom:
                candidates = custom['connected_line_ids']
            else:
                members = library.workspace.members(line_id)
                candidates = [row[0] for row in db.execute(
                    'SELECT DISTINCT other.line_id FROM main.line_nodes own JOIN main.line_nodes other '
                    f"ON other.node_id=own.node_id WHERE own.line_id IN ({','.join('?' for _ in members)})", members)]
            for other in candidates:
                other = library.workspace.canonical(other)
                if other not in line_ids and other in library.lines:
                    connected[other] = library.line_name(other, library.lines[other]['source_name'])
    connected = dict(sorted(connected.items(), key=lambda item: (item[1].startswith('未命名'), item[1], item[0])))
    return {'station_ids': list(stations), 'station_names': list(stations.values()),
            'connected_line_ids': list(connected), 'connected_line_names': list(connected.values())}


def relationship_changes(library, line_ids, before, station_ids, connected_ids):
    """Validate every change before returning one atomic override update."""
    line_ids = list(dict.fromkeys(library.workspace.canonical(value) for value in line_ids))
    station_ids = list(dict.fromkeys(station_ids))
    connected_ids = list(dict.fromkeys(library.workspace.canonical(value) for value in connected_ids))
    if not line_ids or any(value not in library.lines for value in [*line_ids, *connected_ids]):
        raise ValueError('线路已变化，请重新打开编辑器')
    if set(line_ids) & set(connected_ids):
        raise ValueError('相接线路不能包含本线路')
    changes = {}
    for endpoint in set(before['station_ids']) ^ set(station_ids):
        selected = [row['id'] for row in library.connected_lines(endpoint, limit=10000)]
        retained = [value for value in selected if value not in line_ids]
        if endpoint in station_ids:
            retained.append(line_ids[0])
        connections = library.station_connection_override(endpoint, retained)
        # Existing fixed anchors must survive an unrelated line membership edit.
        old = library._station_connection_override(library._station_sources(endpoint)) or []
        old = {item['line_id']: item for item in old}
        connections = [old.get(item['line_id'], item) for item in connections]
        if not endpoint.startswith('station:'):
            raise ValueError('请选择具有稳定编号的车站或线路所')
        changes[endpoint] = {'connected_lines': connections}
    old_connected = set(before['connected_line_ids'])
    new_connected = set(connected_ids)
    if old_connected != new_connected:
        for line_id in line_ids:
            changes.setdefault(line_id, {}).update(connected_line_ids=connected_ids,
                relationship_source='manual', relationship_verification_status='user_reference')
        for other in old_connected ^ new_connected:
            values = line_relationships(library, [other])['connected_line_ids']
            values = [value for value in values if value not in line_ids]
            if other in new_connected:
                values.extend(line_ids)
            changes.setdefault(other, {}).update(connected_line_ids=list(dict.fromkeys(values)),
                relationship_source='manual', relationship_verification_status='user_reference')
    return changes
