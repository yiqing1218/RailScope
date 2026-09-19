from railscope.demo import load_demo
from railscope.services.topology import validate_topology
from railscope.services.routing import suggest_corridor

def test_demo_topology_is_valid_and_explicit():
    repo = load_demo()
    report = validate_topology(repo)
    assert report["status"] == "PASS"
    assert repo.edges["edge-ab"].from_node_id == "node-a"
    assert "block-edge-ab" in repo.blocks

def test_weighted_route_suggestion_is_unverified_and_not_registered():
    repo = load_demo()
    path = suggest_corridor(repo, "route-test", ["station-a", "station-c"])
    assert [r.edge_id for r in path.edge_refs] == ["edge-ab", "edge-bc"]
    assert path.verification_status == "unverified"
    assert path.id not in repo.corridors
