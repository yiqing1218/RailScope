from __future__ import annotations
from collections import Counter
from ...domain import NetworkEdge
from ...repository import RailRepository


def validate_topology(repo: RailRepository) -> dict[str, object]:
    edges = tuple(repo.edges.values())
    degree = Counter(n for e in edges for n in (e.from_node_id, e.to_node_id))
    invalid_direction = [e.id for e in edges if e.direction not in {"both", "forward", "reverse", "closed"}]
    zero_length = [e.id for e in edges if e.length_m <= 0]
    self_loops = [e.id for e in edges if e.from_node_id == e.to_node_id]
    pairs = Counter((e.from_node_id, e.to_node_id, tuple(e.coordinates)) for e in edges)
    duplicate = [e.id for e in edges if pairs[(e.from_node_id, e.to_node_id, tuple(e.coordinates))] > 1]
    isolated = [n.id for n in repo.nodes.values() if degree[n.id] == 0]
    dangling = [n for n, d in degree.items() if d == 1]
    broken_paths = [p.id for p in repo.routes.values() if any(ref.edge_id not in repo.edges for ref in p.edge_refs)]
    return {"nodes": len(repo.nodes), "edges": len(edges), "isolated_nodes": isolated,
            "dangling_edges": dangling, "zero_length_edges": zero_length,
            "duplicate_edges": duplicate, "self_loop_edges": self_loops,
            "broken_route_paths": broken_paths, "invalid_direction": invalid_direction,
            "status": "FAIL" if zero_length or self_loops or invalid_direction or broken_paths else "PASS"}
