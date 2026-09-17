from copy import deepcopy
import pytest

from desktop.tests.test_workspace_revision import reference


def test_corridor_validation_shared_trains_and_transfer_reservation():
    from desktop.rail import shared_document, validate_corridors

    data = reference()
    route = shared_document(data["plan"])["routes"][0]
    route["name"] = "京沪下行参考通道"
    route["track_changes"] = [
        {
            "node_id": data["plan"]["trains"][0]["stops"][1]["node_id"],
            "from_track": "",
            "to_track": "",
            "via_node": None,
            "extensions": {},
        }
    ]
    validate_corridors([route], data["edges"])
    broken = deepcopy(route)
    broken["track_changes"][0]["node_id"] = -1
    with pytest.raises(ValueError, match="通道"):
        validate_corridors([broken], data["edges"])


def test_independent_corridor_import_export_and_map_train_selection(tmp_path):
    from PySide6.QtWidgets import QApplication
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub

    app = QApplication.instance() or QApplication([])
    assert app
    view = MapStub()
    editor = RailEditor(view, tmp_path, tmp_path / "plan.json")
    side = editor.sidebar()
    editor.add_train_number("G3", "G1", 24000)
    document = editor.corridors_document()
    assert len(document["corridors"]) == 1
    route = document["corridors"][0]
    route["name"] = "京沪下行参考通道"
    route["track_changes"] = []
    editor.merge_corridors(document)
    assert len(editor.plan.trains) == 2
    assert all(t["route_id"] == route["id"] for t in editor.document()["trains"])
    editor.line_combo.setCurrentIndex(editor.line_combo.findData("rail/G3"))
    editor.show_reference_route()
    feature = next(
        c[1]["features"][0] for c in reversed(view.calls) if c[0] == "setRailPlan"
    )
    assert feature["properties"]["corridor_id"] == route["id"]
    assert set(feature["properties"]["train_ids"]) == {"G1", "G3"}
    assert feature["properties"]["name"] == "京沪下行参考通道"
    assert editor.corridors_document()["corridors"][0]["name"] == route["name"]
    node = reference()["plan"]["trains"][0]["stops"][1]["node_id"]
    route["track_changes"] = [
        {
            "node_id": node,
            "from_track": "",
            "to_track": "2",
            "via_node": None,
            "extensions": {},
        }
    ]
    editor.merge_corridors(document)
    editor.line_combo.setCurrentIndex(editor.line_combo.findData("rail/G3"))
    assert editor.table.item(1, 8).text() == "2"
    from PySide6.QtCore import Qt

    assert not editor.table.item(1, 8).flags() & Qt.ItemFlag.ItemIsEditable
    assert editor.document()["routes"][0]["track_changes"][0]["to_track"] == "2"
    bad = deepcopy(document)
    leg = bad["corridors"][0]["path"][0]
    leg["direction"] = "forward" if leg["direction"] == "reverse" else "reverse"
    before = editor.document()
    with pytest.raises(ValueError):
        editor.merge_corridors(bad)
    assert editor.document() == before
    other = deepcopy(route)
    other["id"] = "channel/up-test"
    other["name"] = "京沪上行参考通道"
    other["path"] = [
        {
            "edge_id": leg["edge_id"],
            "direction": "forward" if leg["direction"] == "reverse" else "reverse",
        }
        for leg in reversed(other["path"])
    ]
    editor.merge_corridors({**document, "corridors": [other]})
    editor.show_corridor(other["id"])
    preview = next(
        c[1]["features"] for c in reversed(view.calls) if c[0] == "setRailPlan"
    )
    assert preview[0]["properties"]["train_ids"] == []
    assert len(editor.plan.trains) == 2, "预览通道不能生成车次"
    editor.add_train_on_corridor("G5", other["id"], 25200, 43200)
    assert len(editor.plan.trains) == 3
    assert editor.document()["trains"][-1]["route_id"] == other["id"]
    assert len(editor.plan.trains[-1]["stops"]) == 2
    editor.timer.stop()
    side.close()
    editor.close()


def test_saved_corridor_without_train_survives_restart(tmp_path):
    from PySide6.QtWidgets import QApplication
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub

    app = QApplication.instance() or QApplication([])
    assert app
    path = tmp_path / "plan.json"
    editor = RailEditor(MapStub(), tmp_path, path)
    payload = editor.document()
    payload["trains"] = []
    editor.apply_payload(payload)
    editor.save()
    restored = RailEditor(MapStub(), tmp_path, path)
    assert (
        not restored.plan.trains
        and len(restored.corridors_document()["corridors"]) == 1
    )
    for item in (editor, restored):
        item.timer.stop()
        item.close()
