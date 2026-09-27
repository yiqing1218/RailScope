"""Bounded reference connections between fragments of the same selected line.

Source membership never changes: connectors retain their own line and edge IDs.
Only real, operational, direction-permitted edges can join two components.
"""

from heapq import heappop, heappush
from itertools import count

try:
    from .rail_lines import traversal_allowed
except ImportError:
    from rail_lines import traversal_allowed


def line_connectors(store, selected, line_id, max_distance_m=20000, max_nodes=12000):
    graph = {}
    for node, links in selected.lines[line_id]['graph'].items():
        active = [link for link in links if not selected.edges[link[1]].get('construction')
                  and selected.edges[link[1]].get('construction_status', 'operating') == 'operating'
                  and selected.edges[link[1]].get('direction') != 'closed']
        if active:
            graph[node] = active
    components = {}
    for origin in graph:
        if origin in components:
            continue
        component = len(components)
        todo = [origin]
        components[origin] = component
        while todo:
            for node, ident, _ in graph[todo.pop()]:
                edge = selected.edges[ident]
                if edge.get('construction') or edge.get('direction') == 'closed':
                    continue
                if node not in components:
                    components[node] = component
                    todo.append(node)
    if len(set(components.values())) < 2:
        return {}
    # Only component boundaries can leave a named line. Interior branch nodes
    # are not an invitation to route around the user's selected railway.
    terminals = [node for node, links in graph.items() if len(links) == 1]
    connectors, adjacency = {}, {}
    with store.connect() as db:
        def neighbours(node):
            if node not in adjacency:
                rows = db.execute(
                    'SELECT e.id,e.line_id,e.a,e.b,e.length_m,e.track_type,e.direction,l.source_name '
                    'FROM main.edges e JOIN main.lines l ON l.id=e.line_id '
                    'WHERE (e.a=? OR e.b=?) AND e.construction=0 AND e.direction!=\'closed\' '
                    'ORDER BY e.id', (node, node)).fetchall()
                adjacency[node] = [
                    {'id': ident, 'line_id': store.workspace.canonical(line), 'from_node': a,
                     'to_node': b, 'length_m': length, 'track_type': kind, 'direction': direction,
                     'line_name': name, 'construction': False}
                    for ident, line, a, b, length, kind, direction, name in rows]
            return adjacency[node]

        for origin in sorted(terminals, key=str):
            serial = count()
            queue, scores, previous = [(0, next(serial), origin)], {origin: 0}, {}
            visited = 0
            while queue and visited < max_nodes:
                distance, _, node = heappop(queue)
                if distance != scores[node]:
                    continue
                visited += 1
                if node != origin and node in components:
                    if components[node] != components[origin]:
                        cursor = node
                        while cursor != origin:
                            cursor, edge = previous[cursor]
                            connectors[edge['id']] = edge
                    # Never traverse another component to find a further detour.
                    continue
                for edge in neighbours(node):
                    if edge['line_id'] == line_id:
                        continue
                    forward = edge['from_node'] == node
                    other = edge['to_node'] if forward else edge['from_node']
                    if not traversal_allowed(edge, 'forward' if forward else 'reverse'):
                        continue
                    cost = distance + edge['length_m']
                    if cost <= max_distance_m and cost < scores.get(other, float('inf')):
                        scores[other] = cost
                        previous[other] = (node, edge)
                        heappush(queue, (cost, next(serial), other))
    return connectors
