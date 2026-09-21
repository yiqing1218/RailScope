from __future__ import annotations

import heapq

from ...domain import Corridor
from ...integrity import path_refs
from ...repository import RailRepository

SERVICE_COST = {
    None: 1.0,
    "main": 1.0,
    "branch": 1.2,
    "crossover": 2.0,
    "siding": 4.0,
    "yard": 8.0,
    "spur": 10.0,
}


def _cost(edge) -> float:
    return edge.length_m * SERVICE_COST.get(
        edge.service or edge.railway_type, 1.0
    )


def suggest_corridor(
    repo: RailRepository, corridor_id: str, station_ids: list[str]
) -> Corridor:
    """Return an unregistered geometric suggestion for manual verification.

    A shortest path is useful for preview and editing, but it is not evidence of
    an actual dispatch or interlocking route and therefore never becomes a
    formal Corridor merely by calling this function.
    """
    if len(station_ids) < 2:
        raise ValueError("corridor matching needs at least two stations")
    all_refs: list[tuple[str, bool]] = []
    for origin, destination in zip(station_ids, station_ids[1:]):
        start = repo.stations[origin].anchor_node_id
        end = repo.stations[destination].anchor_node_id
        adjacency: dict[str, list[tuple[str, str, bool, float]]] = {}
        for edge in repo.edges.values():
            if edge.mode != "rail" or edge.construction_status != "operating":
                continue
            if edge.direction in {"both", "forward"}:
                adjacency.setdefault(edge.from_node_id, []).append(
                    (edge.to_node_id, edge.id, True, _cost(edge))
                )
            if edge.direction in {"both", "reverse"}:
                adjacency.setdefault(edge.to_node_id, []).append(
                    (edge.from_node_id, edge.id, False, _cost(edge))
                )
        def find(banned_edge=None):
            queue = [(0.0, start, [])]
            seen: dict[str, float] = {}
            while queue:
                total, node, path = heapq.heappop(queue)
                if node in seen and seen[node] <= total:
                    continue
                seen[node] = total
                if node == end:
                    return path
                for target, edge_id, forward, cost in adjacency.get(node, []):
                    if edge_id == banned_edge:
                        continue
                    heapq.heappush(
                        queue, (total + cost, target, path + [(edge_id, forward)])
                    )
            return None

        found = find()
        if found is None:
            raise ValueError(f"ROUTE_NOT_FOUND: {origin} to {destination}")
        if any(find(edge_id) is not None for edge_id, _ in found):
            raise ValueError(
                f"ROUTE_AMBIGUOUS: {origin} to {destination}; "
                "specify intermediate control points or exact NetworkEdge sections"
            )
        all_refs.extend(found)
    refs = path_refs(repo, all_refs)
    return Corridor(
        corridor_id,
        corridor_id,
        refs,
        start_node_id(repo, refs),
        end_node_id(repo, refs),
        source_id="geometric_shortest_path",
        verification_status="unverified",
    )


def start_node_id(repo: RailRepository, refs) -> str:
    edge = repo.edges[refs[0].edge_id]
    return edge.from_node_id if refs[0].forward else edge.to_node_id


def end_node_id(repo: RailRepository, refs) -> str:
    edge = repo.edges[refs[-1].edge_id]
    return edge.to_node_id if refs[-1].forward else edge.from_node_id


def manual_corridor(
    repo: RailRepository,
    corridor_id: str,
    legs: list[str | tuple[str, bool]],
    *,
    name: str | None = None,
    verification_status: str = "user_verified",
) -> Corridor:
    directed = [(leg, True) if isinstance(leg, str) else leg for leg in legs]
    refs = path_refs(repo, directed)
    corridor = Corridor(
        corridor_id,
        name or corridor_id,
        refs,
        start_node_id(repo, refs),
        end_node_id(repo, refs),
        source_id="manual",
        verification_status=verification_status,
    )
    repo.corridors[corridor.id] = corridor
    return corridor


# Source compatibility for older integrations. The returned object is a
# Corridor suggestion and is intentionally not registered automatically.
match_route = suggest_corridor
