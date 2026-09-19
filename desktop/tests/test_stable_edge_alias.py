import sqlite3


def test_viewport_store_loads_legacy_source_alias_as_stable_edge(tmp_path):
    from desktop.rail_store import build_index, load_edges

    edge = {
        "id": "NE-stable",
        "source_edge_id": "w123:0-2",
        "from_node": 1,
        "to_node": 2,
        "coordinates": [[120, 30], [121, 30]],
        "construction": False,
        "way_tags": {"railway": "rail"},
    }
    build_index(tmp_path, [], [], [], [edge])
    assert load_edges(tmp_path, ["NE-stable"])[0]["id"] == "NE-stable"
    legacy = load_edges(tmp_path, ["w123:0-2"])[0]
    assert legacy["id"] == "NE-stable"
    assert legacy["requested_edge_alias"] == "w123:0-2"
    with sqlite3.connect(tmp_path / "rail.sqlite") as db:
        assert db.execute("SELECT id FROM edge_aliases WHERE alias='w123:0-2'").fetchone() == ("NE-stable",)


def test_legacy_plan_is_migrated_to_stable_edge_reference():
    from desktop.rail import resolve_edge_aliases

    payload = {"routes": [{"path": [{"edge_id": "w123:0-2", "direction": "forward"}]}],
               "trains": [], "extensions": {}}
    migrated = resolve_edge_aliases(payload, [{"id": "NE-stable", "requested_edge_alias": "w123:0-2"}])
    assert migrated["routes"][0]["path"][0]["edge_id"] == "NE-stable"
    assert payload["routes"][0]["path"][0]["edge_id"] == "w123:0-2"
