"""Full physical corridors and independent, validated train stop schedules."""

from copy import deepcopy

import pytest

from desktop.rail import compile_rail_plan, migrate_legacy_train_paths


def fixture():
    edges = [
        {
            "id": f"e{i}",
            "from_node": i,
            "to_node": i + 1,
            "node_ids": [i, i + 1],
            "coordinates": [[121 + i / 100, 31], [121 + (i + 1) / 100, 31]],
            "construction": False,
        }
        for i in range(1, 5)
    ]
    path = [{"edge_id": edge["id"], "direction": "forward"} for edge in edges]

    def stops(nodes):
        return [
            {"node_id": node, "arrival_s": node * 100, "departure_s": node * 100 + 10}
            for node in nodes
        ]

    payload = {
        "schema": "railscope.rail-plan.v2",
        "service_date": "2026-09-18",
        "timezone": "Asia/Shanghai",
        "source": "synthetic test",
        "extensions": {},
        "required_capabilities": [],
        "routes": [{"id": "corridor/full", "path": path, "extensions": {}}],
        "trains": [
            {
                "id": ident,
                "route_id": "corridor/full",
                "stops": stops(nodes),
                "extensions": {},
            }
            for ident, nodes in (
                ("direct", [1, 5]),
                ("limited", [1, 3, 5]),
                ("all", [1, 2, 3, 4, 5]),
            )
        ],
    }
    return payload, edges


def test_full_path_shared_by_direct_limited_and_all_stopping_trains():
    payload, edges = fixture()
    before = deepcopy(payload)
    plan, lines = compile_rail_plan(payload, edges, [])
    assert [len(t["stops"]) for t in plan.trains] == [2, 3, 5]
    assert len(lines[0]["path"]["coordinates"]) == 5
    assert all(line["path"] is lines[0]["path"] for line in lines)
    assert payload == before
    assert len(payload["routes"]) == 1
    assert plan.position("direct", 300)["state"] == "区间运行"


def test_legacy_station_replacement_migrates_to_complete_persisted_corridor():
    payload, edges = fixture()
    edges.extend(
        [
            {
                "id": "alt-a",
                "from_node": 2,
                "to_node": 9,
                "coordinates": [[121.02, 31], [121.025, 31.01]],
                "construction": False,
            },
            {
                "id": "alt-b",
                "from_node": 9,
                "to_node": 3,
                "coordinates": [[121.025, 31.01], [121.03, 31]],
                "construction": False,
            },
        ]
    )
    override = {
        "from_node": 2,
        "to_node": 3,
        "path": [
            {"edge_id": ident, "direction": "forward"} for ident in ("alt-a", "alt-b")
        ],
        "extensions": {},
    }
    payload["trains"][0]["station_paths"] = [override]
    before = deepcopy(payload)
    migrated = migrate_legacy_train_paths(payload, edges)
    train = migrated["trains"][0]
    assert "station_paths" not in train
    assert train["route_id"] != "corridor/full"
    assert len(migrated["routes"]) == 2
    assert len(migrated["routes"][1]["path"]) == 5
    assert migrated["trains"][1]["route_id"] == "corridor/full"
    assert migrate_legacy_train_paths(migrated, edges) == migrated
    compile_rail_plan(migrated, edges, [])
    assert payload == before


def test_stop_station_route_and_track_do_not_rewrite_corridor():
    payload, edges = fixture()
    payload["station_routes"] = [
        {
            "id": "sr/3",
            "station_id": "station/3",
            "entry_node_id": "2",
            "exit_node_id": "4",
            "edge_refs": payload["routes"][0]["path"][1:3],
        }
    ]
    stop = payload["trains"][1]["stops"][1]
    stop.update(station_track_id="e2", station_route_id="sr/3")
    compile_rail_plan(payload, edges, [])
    stop["station_route_id"] = "missing"
    with pytest.raises(ValueError, match="进路不存在"):
        compile_rail_plan(payload, edges, [])
    stop.pop("station_route_id")
    stop["station_track_id"] = "e4"
    with pytest.raises(ValueError, match="停靠节点"):
        compile_rail_plan(payload, edges, [])


@pytest.mark.parametrize("status", ["construction", "planned", "disused", "unknown"])
def test_nonoperating_edges_cannot_enter_corridor(status):
    payload, edges = fixture()
    edges[1]["construction_status"] = status
    with pytest.raises(ValueError, match="通道区间"):
        compile_rail_plan(payload, edges, [])


def test_train_stop_editor_changes_one_train_and_roundtrips(tmp_path):
    from PySide6.QtWidgets import QApplication
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub
    from desktop.tests.test_workspace_revision import install_reference_database

    app = QApplication.instance() or QApplication([])
    assert app
    install_reference_database(tmp_path)
    path = tmp_path / "plan.json"
    editor = RailEditor(MapStub(), tmp_path, path)
    editor.add_train_number("G3", "G1", 24000)
    original = editor.document()
    g3 = original["trains"][1]
    editor.set_train_stops("G3", [g3["stops"][0], g3["stops"][-1]])
    result = editor.document()
    assert result["routes"] == original["routes"]
    assert result["trains"][0] == original["trains"][0]
    assert len(result["trains"][1]["stops"]) == 2
    with pytest.raises(ValueError, match="站序倒退"):
        editor.set_train_stops("G3", list(reversed(g3["stops"])))
    assert editor.document() == result
    editor.save()
    restored = RailEditor(MapStub(), tmp_path, path)
    assert restored.document() == result
    for view in (editor, restored):
        view.timer.stop()
        view.close()
