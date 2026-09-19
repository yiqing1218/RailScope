from desktop.tests.test_full_corridor import fixture


def test_desktop_plan_adapts_to_one_shared_domain_contract(tmp_path):
    from desktop.domain_adapter import build_repository

    payload, edges = fixture()
    graph = {"edges": edges, "points": [
        {"type": "Feature", "properties": {"osm_node_id": i, "kind": "station", "name": f"站{i}"},
         "geometry": {"type": "Point", "coordinates": [121 + i / 100, 31]}}
        for i in range(1, 6)
    ]}
    repo, bindings = build_repository(graph, payload, tmp_path / "workspace.sqlite")
    assert len(repo.corridors) == 1
    assert len(repo.train_runs) == 3
    corridor = next(iter(repo.corridors.values()))
    assert [ref.sequence for ref in corridor.edge_refs] == [1, 2, 3, 4]
    assert len(repo.stops_for(bindings["train_runs"]["direct"])) == 2
    assert len(repo.stops_for(bindings["train_runs"]["all"])) == 5
    assert all(key.startswith("NE-") for key in repo.edges)
    again, again_bindings = build_repository(graph, payload, tmp_path / "workspace.sqlite")
    assert set(repo.edges) == set(again.edges)
    assert bindings == again_bindings


def test_rail_editor_keeps_canonical_repository_in_sync(tmp_path):
    from PySide6.QtWidgets import QApplication
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub

    app = QApplication.instance() or QApplication([])
    editor = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
    assert editor.domain_repo is not None
    assert len(editor.domain_repo.corridors) == len(editor.rail_payload["routes"])
    assert len(editor.domain_repo.train_runs) == len(editor.rail_payload["trains"])
    editor.timer.stop()
    editor.close()
