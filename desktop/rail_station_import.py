"""Resolve user-facing stop names within existing complete corridors."""

from copy import deepcopy

try:
    from .station_positions import POSITION_KEY, STOP_POSITION_KEY, stations_on_path, position_distance
    from .rail_station_directory import source_names
    from .station_search import rail_name_key
except ImportError:
    from station_positions import POSITION_KEY, STOP_POSITION_KEY, stations_on_path, position_distance
    from rail_station_directory import source_names
    from station_search import rail_name_key

STOP_NAME_KEY = 'railscope.org/stop-name'


def name_key(value):
    return rail_name_key(value)


def station_choices(payload, edges=(), points=(), library=None):
    edge_map = {edge['id']: edge for edge in edges}
    names = {item.get('node_id', item.get('anchor_node')): item.get('name', item.get('station_name', ''))
             for item in payload.get('extensions', {}).get('railscope.org/assembly', {}).get('stations', [])
             if item.get('node_id', item.get('anchor_node')) is not None}
    names.update({p['properties']['osm_node_id']: p['properties'].get('name', '')
                  for p in points if 'osm_node_id' in p.get('properties', {})})
    result = {}
    for route in payload['routes']:
        ordered = []
        distances, node_visits, travelled = {}, {}, 0.
        for leg in route['path']:
            edge = edge_map.get(leg['edge_id'])
            if edge:
                nodes = edge.get('node_ids', [edge['from_node'], edge['to_node']])
                if leg['direction'] == 'reverse':
                    nodes = nodes[::-1]
                coords = edge['coordinates'][::-1] if leg['direction'] == 'reverse' else edge['coordinates']
                from railscope.services.simulation.geometry import distance_m
                for index, node in enumerate(nodes):
                    if index:
                        travelled += distance_m(coords[index-1], coords[index])
                    distances.setdefault(node, travelled)
                    visits = node_visits.setdefault(node, [])
                    if not visits or travelled != visits[-1]:
                        visits.append(travelled)
                ordered.extend(nodes if not ordered else nodes[1:])
        order = distances or {node: i for i, node in enumerate(ordered)}
        references = deepcopy(route.get('extensions', {}).get(POSITION_KEY, []))
        if library is not None and hasattr(library, 'station_directory'):
            fresh = stations_on_path(library, route['path'], edges)
            # Keep saved platform positions, but never discard a later visit
            # to that same station after a turnback.
            for saved in references:
                same = [p for p in fresh if p['station_id'] == saved['station_id'] and abs(p['distance_m']-saved['distance_m']) < 1000]
                if same:
                    fresh.remove(min(same, key=lambda p: abs(p['distance_m']-saved['distance_m'])))
            references.extend(fresh)
        if all(leg['edge_id'] in edge_map for leg in route['path']):
            for p in references:
                occurrences = [i for i, leg in enumerate(route['path']) if leg['edge_id'] == p['edge_id']]
                if occurrences:
                    p['path_index'] = min(occurrences, key=lambda i: abs(
                        position_distance(route['path'], edge_map, {**p, 'path_index': i}) - p['distance_m']))
                    p['distance_m'] = position_distance(route['path'], edge_map, p)
        preferred = {name_key(p['station_name']) for p in references}
        choices = [{'name': p['station_name'], 'node_id': p['node_id'], 'position': p,
                    'order': p['distance_m']} for p in references]
        if library is not None:
            for position in references:
                # The corridor may have been saved using an old name (绅坊).
                # Match all aliases of that SAME station identity, retaining
                # its exact saved edge offset rather than selecting a new node.
                if hasattr(library, 'connect') and str(position['station_id']).startswith('station:'):
                    sources = library._station_sources(position['station_id'])
                    with library.connect() as db:
                        aliases = [row[0] for row in db.execute(
                            'SELECT DISTINCT alias FROM station_aliases WHERE source_id IN ('
                            + ','.join('?' for _ in sources) + ')', sources)]
                    record = getattr(library, 'station_directory', {}).get(str(position['station_id']).removeprefix('station:'))
                    if record:
                        aliases += source_names(record['feature']['properties'])
                    canonical = library.endpoint_label(position['station_id'])
                    for alias in [canonical, *aliases]:
                        preferred.add(name_key(alias))
                        choices.append({'name': library.station_display_name(alias), 'node_id': position['node_id'],
                                        'canonical_name': canonical, 'position': position, 'order': position['distance_m']})
                renamed = library.metadata.get(position['station_id'], {}).get('display_name')
                if renamed:
                    preferred.add(name_key(renamed))
                    choices.append({'name': renamed, 'node_id': position['node_id'], 'position': position,
                                    'canonical_name': renamed, 'order': position['distance_m']})
        fallback = [(node, name, str(node), 0.) for node, name in names.items()
                    if name and (not order or node in order)]
        if library is not None and hasattr(library, 'connect') and ordered:
            with library.connect() as db:
                for start in range(0, len(ordered), 800):
                    batch = ordered[start:start + 800]
                    for node, alias, source, gap, source_id in db.execute(
                            'SELECT anchor_node,alias,coalesce(station_node_id,source_id),distance_m,source_id FROM station_aliases '
                            f"WHERE anchor_node IN ({','.join('?' for _ in batch)}) AND confidence>=0.5 ORDER BY distance_m", batch):
                        fallback.append((node, library.station_display_name(alias), str(source), gap))
                        renamed = library.metadata.get('station:' + source_id, {}).get('display_name')
                        if renamed:
                            fallback.append((node, renamed, str(source), gap))
        grouped = {}
        for node, name, source, gap in fallback:
            if name_key(name) in preferred:
                continue
            key = (name_key(name), source)
            value = {'name': name, 'node_id': node, 'order': order.get(node, len(grouped)), 'gap': gap}
            if key not in grouped or gap < grouped[key]['gap']:
                grouped[key] = value
        # Legacy node-only references also need a choice for every visit.
        fallback_choices = [{**c, 'order': distance} for c in grouped.values()
                            for distance in node_visits.get(c['node_id'], [c['order']])]
        unique = {(name_key(c['name']), c.get('position', {}).get('station_id', c['node_id']), c['order']): c
                  for c in [*fallback_choices, *choices]}
        result[route['id']] = sorted(unique.values(), key=lambda c: c['order'])
    return result


def resolve_stops(rows, choices):
    # Resolve the whole sequence: later stops can disambiguate an earlier name.
    candidates = []
    for number, row in rows:
        node = row.get('node_id', '')
        if node and not node.isdigit():
            raise ValueError(f'第 {number} 行节点编号无效')
        name = row.get('stop_name', '')
        matching = [c for c in choices if name_key(c['name']) == name_key(name)] if name else list(choices)
        if node:
            exact = [c for c in matching if str(c['node_id']) == node or
                     c.get('position', {}).get('station_id') in ('station:node/' + node, 'station:' + node)]
            if exact:
                matching = exact
            elif not name:
                # Preserve legacy node-only imports; compilation verifies them.
                matching = [{'node_id': int(node), 'name': node, 'order': None, 'explicit_only': True}]
        label = row.get('stop_name') or node
        if not matching:
            from difflib import get_close_matches
            names = {name_key(c['name']): c['name'] for c in choices}
            nearby = get_close_matches(name_key(label), names, n=3, cutoff=.5)
            hint = ('；可核对：' + '、'.join(names[k] for k in nearby)) if nearby else ''
            raise ValueError(f'第 {number} 行：通道中找不到车站“{label}”' + hint)
        unique = {}
        for choice in matching:
            identity = choice.get('position', {}).get('station_id', choice['node_id'])
            key = (identity, choice['order'])
            canonical = choice.get('canonical_name', choice['name'])
            score = (int(bool(name) and name_key(canonical) != name_key(name)),
                     int(not bool(choice.get('position'))))
            if key not in unique or score < unique[key][0]:
                unique[key] = (score, choice)
        candidates.append(list(unique.values()))

    # Keep the best two equal-cost histories per endpoint to detect true
    # ambiguity without exponential enumeration of repeated visits.
    states = [((0, 0), [], -1.)]
    for (number, row), options in zip(rows, candidates):
        following = []
        for score, choice in options:
            histories = []
            for cost, history, previous in states:
                distance = choice['order']
                if distance is None or distance > previous:
                    histories.append((tuple(a+b for a,b in zip(cost, score)), [*history, choice],
                                      previous if distance is None else distance))
            if histories:
                best = min(h[0] for h in histories)
                distinct = {}
                for h in histories:
                    if h[0] == best:
                        identity = tuple(c.get('position', {}).get('station_id', c['node_id']) for c in h[1])
                        distinct.setdefault(identity, h)
                following.extend(list(distinct.values())[:2])
        if not following:
            raise ValueError(f"第 {number} 行：{row.get('stop_name') or row.get('node_id')} 的站序与完整通道不符；折返须包含在通道路径中")
        states = following
    best = min(s[0] for s in states)
    winners = [s for s in states if s[0] == best]
    def identities(state):
        return tuple(c.get('position', {}).get('station_id', c['node_id']) for c in state[1])
    if len({identities(s) for s in winners}) > 1:
        raise ValueError('仍有多个同名站符合前后站序，请使用当前完整站名或 node_id 指定车站')
    selected = min(winners, key=lambda s: tuple(c['order'] or 0 for c in s[1]))[1]
    result = []
    for (_, row), chosen in zip(rows, selected):
        if chosen.get('explicit_only'):
            result.append({'node_id': chosen['node_id']})
            continue
        stop = {'node_id': chosen['node_id'], 'extensions': {STOP_NAME_KEY: {
            'display_name': chosen.get('canonical_name', chosen['name']), 'input_name': row.get('stop_name', ''),
            'station_key': chosen.get('position', {}).get('station_id'),
            'input_node_id': row.get('node_id', ''),
            'node_id': chosen['node_id'], 'source': 'corridor_station_name_match',
            'version': 1, 'verification_status': 'matched_alias', 'confidence': None}}}
        if chosen.get('position'):
            stop['extensions'][STOP_POSITION_KEY] = deepcopy(chosen['position'])
        result.append(stop)
    return result
