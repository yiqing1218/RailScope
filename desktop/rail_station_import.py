"""Resolve user-facing stop names within existing complete corridors."""

from copy import deepcopy

try:
    from .station_positions import POSITION_KEY, STOP_POSITION_KEY
except ImportError:
    from station_positions import POSITION_KEY, STOP_POSITION_KEY


def name_key(value):
    return ''.join(str(value).split()).removesuffix('站').casefold()


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
        for leg in route['path']:
            edge = edge_map.get(leg['edge_id'])
            if edge:
                nodes = edge.get('node_ids', [edge['from_node'], edge['to_node']])
                if leg['direction'] == 'reverse':
                    nodes = nodes[::-1]
                ordered.extend(nodes if not ordered else nodes[1:])
        order = {node: i for i, node in enumerate(ordered)}
        references = route.get('extensions', {}).get(POSITION_KEY, [])
        preferred = {name_key(p['station_name']) for p in references}
        choices = [{'name': p['station_name'], 'node_id': p['node_id'], 'position': p,
                    'order': order.get(p['node_id'], p['distance_m'])} for p in references]
        if library is not None:
            for position in references:
                renamed = library.metadata.get(position['station_id'], {}).get('display_name')
                if renamed:
                    preferred.add(name_key(renamed))
                    choices.append({'name': renamed, 'node_id': position['node_id'], 'position': position,
                                    'order': order.get(position['node_id'], position['distance_m'])})
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
        # Duplicate aliases for one physical anchor are one choice; distinct
        # same-named stations remain ambiguous and require an explicit ID.
        unique = {(name_key(c['name']), c['node_id']): c for c in [*grouped.values(), *choices]}
        result[route['id']] = sorted(unique.values(), key=lambda c: c['order'])
    return result


def resolve_stops(rows, choices):
    result, previous = [], -1
    for number, row in rows:
        node = row.get('node_id', '')
        if node and not node.isdigit():
            raise ValueError(f'第 {number} 行节点编号无效')
        matching = [c for c in choices if (c['node_id'] == int(node) if node else
                    name_key(c['name']) == name_key(row.get('stop_name', '')))]
        if node and not matching:
            # Legacy explicit IDs are verified by compile_rail_plan, including
            # manually selected control points with no station alias.
            result.append({'node_id': int(node)})
            continue
        label = row.get('stop_name') or node
        if not matching:
            raise ValueError(f'第 {number} 行：通道中找不到车站“{label}”')
        if node and row.get('stop_name'):
            matching = [c for c in matching if name_key(c['name']) == name_key(row['stop_name'])]
            if not matching:
                raise ValueError(f'第 {number} 行车站名称与节点编号不一致：{label}')
        if node and matching:
            matching = [next((c for c in matching if c.get('position')), matching[0])]
        matching = [c for c in matching if c['order'] > previous]
        if not matching:
            raise ValueError(f'第 {number} 行：站序倒退或重复：{label}')
        if len(matching) != 1:
            raise ValueError(f'第 {number} 行：通道内有多个同名站“{label}”，请在软件中核对后填写 node_id')
        chosen = matching[0]
        previous = chosen['order']
        stop = {'node_id': chosen['node_id']}
        if chosen.get('position'):
            stop['extensions'] = {STOP_POSITION_KEY: deepcopy(chosen['position'])}
        result.append(stop)
    return result
