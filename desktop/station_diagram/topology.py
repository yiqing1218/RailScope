"""Connections only. Coordinates and spatial intersections cannot add nodes."""

from collections import defaultdict
from .types import Graph


def build_graph(repo, keys, validate=True):
    adjacency, endpoints = defaultdict(list), {}
    for key in sorted(keys):
        edge = repo.edges[key]
        a, b = edge.from_node_id, edge.to_node_id
        if validate and (a not in repo.nodes or b not in repo.nodes):
            raise ValueError("物理轨道缺少真实端点：" + key)
        if validate and len(edge.coordinates) < 2:
            raise ValueError("物理轨道缺少完整几何：" + key)
        endpoints[key] = (a, b)
        adjacency[a].append(key)
        adjacency[b].append(key)
    remaining, components = set(keys), []
    while remaining:
        component, queue = set(), [min(remaining)]
        while queue:
            key = queue.pop()
            if key in component:
                continue
            component.add(key)
            for node in endpoints[key]:
                queue.extend(k for k in adjacency[node] if k not in component)
        remaining -= component
        components.append(component)
    return Graph(endpoints, dict(adjacency), components)


def chains(graph, keys, signature=lambda key: None):
    """Collapse only degree-two nodes with equal presentation ownership."""
    remaining, result = set(keys), []

    def continuation(node, key):
        incident = graph.adjacency[node]
        return len(incident) == 2 and all(
            k in keys and signature(k) == signature(key) for k in incident
        )

    while remaining:
        first = min(remaining)
        # Find a boundary in this component, not an arbitrary mid-chain edge.
        queue, seen, starts = [first], set(), []
        while queue:
            key = queue.pop()
            if key in seen:
                continue
            seen.add(key)
            for node in graph.endpoints[key]:
                if not continuation(node, key):
                    starts.append((node, key))
                else:
                    queue.extend(k for k in graph.adjacency[node] if k in remaining)
        node, first = min(starts) if starts else (graph.endpoints[first][0], first)
        legs = []
        while True:
            key = next(
                (
                    k
                    for k in graph.adjacency[node]
                    if k in remaining
                    and (not legs or signature(k) == signature(legs[-1][0]))
                ),
                None,
            )
            if key is None:
                break
            a, b = graph.endpoints[key]
            legs.append((key, node == a))
            remaining.remove(key)
            node = b if node == a else a
            if not continuation(node, key):
                break
        result.append(legs)
    return result
