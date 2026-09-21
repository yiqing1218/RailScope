from railscope.demo import load_demo
from railscope.services.topology import validate_topology
import pytest
from railscope.services.routing import suggest_corridor

def test_demo_topology_is_valid_and_explicit():
    repo = load_demo()
    report = validate_topology(repo)
    assert report["status"] == "PASS"
    assert repo.edges["edge-ab"].from_node_id == "node-a"
    assert "block-edge-ab" in repo.blocks

def test_route_suggestion_keeps_multiple_legal_paths_unresolved():
    repo = load_demo()
    with pytest.raises(ValueError, match="ROUTE_AMBIGUOUS"):
        suggest_corridor(repo, "route-test", ["station-a", "station-c"])
    assert "route-test" not in repo.corridors
