"""Reference routing through real station connections on a bounded working graph."""

from collections import defaultdict
from heapq import heappop, heappush
from itertools import count

try:
    from .rail_lines import edge_length, traversal_allowed
except ImportError:
    from rail_lines import edge_length, traversal_allowed


def _paths_from(library, graph, origin, line_id, targets, forbidden):
    """One arrival's search cannot backtrack through its already travelled path."""
    root = (origin, False)
    scores, previous = {root: 0.0}, {}
    serial = count()
    queue = [(0.0, next(serial), root)]
    remaining = {(node, True) for node in targets if node not in forbidden}
    while queue and remaining:
        score, _, key = heappop(queue)
        if score != scores[key]:
            continue
        remaining.discard(key)
        node, used_line = key
        for other, ident, direction in graph.get(node, ()):
            if other in forbidden:
                continue
            target = (other, used_line or library.edge_lines[ident] == line_id)
            rank = score + edge_length(library.edges[ident])
            if target not in scores or rank < scores[target]:
                scores[target] = rank
                previous[target] = (key, ident, direction)
                heappush(queue, (rank, next(serial), target))
    for node in targets:
        key = (node, True)
        if node in forbidden or key not in scores or key in remaining:
            continue
        legs, visited, cursor = [], set(), key
        while cursor in previous and cursor[0] not in visited:
            visited.add(cursor[0])
            cursor, ident, direction = previous[cursor]
            legs.append({"edge_id": ident, "direction": direction})
        if cursor == root:
            yield node, scores[key], list(reversed(legs))


def station_transfer_path(library, sequence, candidates, local_edges, gaps):
    """Route rows jointly, allowing off-line track only at named transfer stations.

    Each row must traverse its requested line. Boundary candidates can lie on
    either side's track, so a crossover before OR after the station is possible.
    Only edge references are carried between rows, never geometry copies.
    """
    states = {node: ((0.0, gaps[0].get(node, 0.0)), []) for node in candidates[0]}
    for row, line in enumerate(sequence[1::2]):
        line_id = line["line_id"]
        allowed = set(library.lines[line_id]["edge_ids"])
        if line.get("section_id"):
            # An explicit physical interval is authoritative; do not add detours.
            allowed = {leg["edge_id"] for leg in library.section(line["section_id"], line_id)["path"]}
        else:
            allowed.update(local_edges.get(row, ()))
            allowed.update(local_edges.get(row + 1, ()))
        graph = defaultdict(list)
        for ident in sorted(allowed):
            edge = library.edges[ident]
            if edge.get("construction") or edge.get("construction_status", "operating") != "operating":
                continue
            a, b = edge["from_node"], edge["to_node"]
            for start, end, direction in ((a, b, "forward"), (b, a, "reverse")):
                if traversal_allowed(edge, direction):
                    graph[start].append((end, ident, direction))
        next_states = {}
        for origin, (score, previous_chunks) in sorted(states.items(), key=lambda value: str(value[0])):
            visited = {origin}
            for notation, part in previous_chunks:
                visited.add(notation[0]["node_id"])
                for leg in part:
                    edge = library.edges[leg["edge_id"]]
                    visited.add(edge["to_node"] if leg["direction"] == "forward" else edge["from_node"])
            for node, length, row_path in _paths_from(library, graph, origin, line_id, candidates[row + 1], visited):
                rank = (score[0] + length, score[1] + gaps[row + 1].get(node, 0.0))
                if node in next_states and next_states[node][0] <= rank:
                    continue
                notation = library.describe(row_path)
                if line.get("section_id"):
                    notation = [{"kind": "endpoint", "node_id": origin}, dict(line),
                                {"kind": "endpoint", "node_id": node}]
                next_states[node] = (rank, previous_chunks + [(notation, row_path)])
        states = next_states
        if not states:
            raise ValueError(f"第 {row + 1} 行未找到经过所选线路的连续站内换线路径")
    _, chunks = min(states.values(), key=lambda value: value[0])
    normalized, path = [], []
    for notation, legs in chunks:
        normalized.extend(notation if not normalized else notation[1:])
        path.extend(legs)
    library.validate_selected_path(normalized, path)
    return normalized, path
