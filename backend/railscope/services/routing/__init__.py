from __future__ import annotations
import heapq
from ...domain import RoutePath, RoutePathEdge
from ...repository import RailRepository

SERVICE_COST = {None: 1.0, "main": 1.0, "branch": 1.2, "crossover": 2.0, "siding": 4.0, "yard": 8.0, "spur": 10.0}


def _cost(edge) -> float:
    return edge.length_m * SERVICE_COST.get(edge.service or edge.railway_type, 1.0)


def match_route(repo: RailRepository, route_id: str, station_ids: list[str]) -> RoutePath:
    if len(station_ids) < 2:
        raise ValueError("route matching needs at least two stations")
    all_refs: list[tuple[str, bool]] = []
    for origin, destination in zip(station_ids, station_ids[1:]):
        start = repo.stations[origin].anchor_node_id
        end = repo.stations[destination].anchor_node_id
        adjacency: dict[str, list[tuple[str, str, bool, float]]] = {}
        for edge in repo.edges.values():
            if edge.mode != "rail" or edge.direction == "closed":
                continue
            adjacency.setdefault(edge.from_node_id, []).append((edge.to_node_id, edge.id, True, _cost(edge)))
            if edge.direction == "both":
                adjacency.setdefault(edge.to_node_id, []).append((edge.from_node_id, edge.id, False, _cost(edge)))
        queue = [(0.0, start, [])]
        seen: dict[str, float] = {}
        found = None
        while queue:
            total, node, path = heapq.heappop(queue)
            if node in seen and seen[node] <= total:
                continue
            seen[node] = total
            if node == end:
                found = path
                break
            for target, edge_id, forward, cost in adjacency.get(node, []):
                heapq.heappush(queue, (total + cost, target, path + [(edge_id, forward)]))
        if found is None:
            raise ValueError(f"ROUTE_NOT_FOUND: {origin} to {destination}")
        all_refs.extend(found)
    refs = []
    distance = 0.0
    for sequence, (edge_id, forward) in enumerate(all_refs, 1):
        edge = repo.edges[edge_id]
        refs.append(RoutePathEdge(edge_id, sequence, forward, distance, distance + edge.length_m))
        distance += edge.length_m
    path = RoutePath(route_id, tuple(refs), distance, station_ids[0], station_ids[-1])
    repo.routes[path.id] = path
    return path


def manual_route(repo: RailRepository, route_id: str, edge_ids: list[str], origin_station_id: str, destination_station_id: str) -> RoutePath:
    if not edge_ids or any(e not in repo.edges for e in edge_ids):
        raise ValueError("manual route references an unknown or empty edge sequence")
    distance = 0.0
    refs = []
    for sequence, edge_id in enumerate(edge_ids, 1):
        edge = repo.edges[edge_id]
        refs.append(RoutePathEdge(edge_id, sequence, True, distance, distance + edge.length_m))
        distance += edge.length_m
    path = RoutePath(route_id, tuple(refs), distance, origin_station_id, destination_station_id)
    repo.routes[path.id] = path
    return path
