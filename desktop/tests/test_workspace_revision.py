import csv
import io
import json
from pathlib import Path
from copy import deepcopy

import pytest
import sqlite3


def reference():
    return json.loads(
        (Path(__file__).parents[1] / "examples/g1-reference.json").read_text(
            encoding="utf-8"
        )
    )


def install_reference_database(directory):
    """Create a tiny current-infrastructure database; production never reads the asset."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    data = reference()
    coordinates = {
        node: coordinate
        for edge in data["edges"]
        for node, coordinate in zip(edge["node_ids"], edge["coordinates"])
    }
    points = []
    for point in data["points"]:
        props = point["properties"]
        points.append(
            {
                "type": "Feature",
                "properties": {
                    **props,
                    "kind": "station",
                    "source": "test current railway database",
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": coordinates[props["osm_node_id"]],
                },
            }
        )
    with sqlite3.connect(directory / "rail.sqlite") as db:
        db.executescript(
            "CREATE TABLE edges(id TEXT PRIMARY KEY,data TEXT);"
            "CREATE TABLE edge_aliases(alias TEXT PRIMARY KEY,id TEXT);"
            "CREATE TABLE features(kind TEXT,data TEXT);"
        )
        db.executemany(
            "INSERT INTO edges VALUES(?,?)",
            ((edge["id"], json.dumps(edge)) for edge in data["edges"]),
        )
        db.executemany(
            "INSERT INTO edge_aliases VALUES(?,?)",
            ((edge["id"], edge["id"]) for edge in data["edges"]),
        )
        db.executemany(
            "INSERT INTO features VALUES(?,?)",
            (("railPoints", json.dumps(point)) for point in points),
        )
    return directory / "rail.sqlite"


def test_shared_routes_and_change_track_reservation():
    from desktop.rail import compile_rail_plan, shared_document

    data = reference()
    other = deepcopy(data["plan"]["trains"][0])
    other["id"] = "G3"
    other["stops"][1]["track_change"] = {
        "from_track": "",
        "to_track": "2",
        "via_node": "",
        "time": "",
    }
    data["plan"]["trains"].append(other)
    payload = shared_document(data["plan"])
    assert payload["schema"] == "railscope.rail-plan.v2"
    assert len(payload["routes"]) == 1
    plan, lines = compile_rail_plan(payload, data["edges"], data["points"])
    assert len(plan.trains) == 2
    assert lines[0]["path"] is lines[1]["path"]
    assert plan.trains[1]["vehicle_id"] == "G3"
    assert (
        plan.trains[1]["stops"][1]["extensions"]["railscope.org/rail-stop"][
            "track_change"
        ]["to_track"]
        == "2"
    )


def test_csv_batches_are_strict_and_reference_existing_routes():
    from desktop.rail import shared_document
    from desktop.rail_tables import COLUMNS, merge_csv

    base = shared_document(reference()["plan"])
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=COLUMNS)
    writer.writeheader()
    for i, stop in enumerate(reference()["plan"]["trains"][0]["stops"]):
        writer.writerow(
            {
                "train_id": "G3",
                "route_id": base["routes"][0]["id"],
                "sequence": i + 1,
                "node_id": stop["node_id"],
                "arrival": "06:30:00" if i == 0 else f"{7 + i // 2:02}:00:00",
                "departure": "06:30:00" if i == 0 else f"{7 + i // 2:02}:01:00",
            }
        )
    merged = merge_csv(output.getvalue(), base)
    assert len(merged["trains"]) == 2 and len(merged["routes"]) == 1
    with pytest.raises(ValueError, match="重复"):
        merge_csv(output.getvalue(), merged)
    assert len(base["trains"]) == 1
    with pytest.raises(ValueError):
        merge_csv(output.getvalue().replace("route_id", "extra"), base)


def test_tree_grows_without_inner_scrollbar():
    from PySide6.QtWidgets import QApplication, QTreeWidgetItem
    from PySide6.QtCore import Qt
    from desktop.components import GrowingTree

    app = QApplication.instance() or QApplication([])
    tree = GrowingTree()
    parent = QTreeWidgetItem(tree, ["parent"])
    for i in range(30):
        QTreeWidgetItem(parent, [str(i)])
    tree.show()
    app.processEvents()
    collapsed = tree.height()
    parent.setExpanded(True)
    app.processEvents()
    assert tree.height() > collapsed + 300
    assert tree.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    tree.close()


def test_builtin_g1_no_load_buttons_compact_and_train_visibility(tmp_path):
    from PySide6.QtWidgets import QApplication, QPushButton
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub

    app = QApplication.instance() or QApplication([])
    install_reference_database(tmp_path)
    editor = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
    side = editor.sidebar()
    assert editor.plan.trains[0]["id"] == "G1"
    assert (
        not editor.enabled
        and not editor.playing
        and not editor.route_switch.isChecked()
    )
    assert not any(
        "G1" in b.text() for w in (editor, side) for b in w.findChildren(QPushButton)
    )
    editor.resize(1100, 650)
    editor.show()
    app.processEvents()
    assert editor.tabs.height() > 450
    assert editor.table.columnCount() == 14
    editor.play()
    assert len(editor.current_vehicle_features) == 1
    editor.set_trains_visible({"G1"}, False)
    assert not editor.current_vehicle_features
    editor.set_trains_visible({"G1"}, True)
    assert len(editor.current_vehicle_features) == 1
    editor.timer.stop()
    editor.close()
    side.close()


def test_manual_train_batch_undo_and_reserved_fields_roundtrip(tmp_path):
    from PySide6.QtWidgets import QApplication
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub
    from desktop.rail_tables import export_csv, merge_csv

    app = QApplication.instance() or QApplication([])
    assert app
    install_reference_database(tmp_path)
    editor = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
    editor.add_train_number("G3", "G1", 24000)
    assert len(editor.plan.trains) == 2 and len(editor.document()["routes"]) == 1
    assert editor.plan.lines["rail/G1"]["path"] is editor.plan.lines["rail/G3"]["path"]
    assert editor.selected_train == "G3" and editor.table.rowCount() == 7
    editor.table.item(1, 10).setText("07:29:00")
    assert (
        editor.document()["trains"][1]["stops"][1]["track_change"]["time"] == "07:29:00"
    )
    editor.undo()
    assert (
        not editor.document()["trains"][1]["stops"][1]
        .get("track_change", {})
        .get("time")
    )
    editor.undo()
    assert len(editor.plan.trains) == 1
    editor.redo()
    assert len(editor.plan.trains) == 2
    before = editor.document()
    with pytest.raises(ValueError, match="重复"):
        editor.add_train_number("G1", "G3", 25200)
    assert editor.document() == before
    text = export_csv(before).replace("G3,", "G5,")
    # Import only G5 rows; G1 already exists and may not be silently overwritten.
    lines = text.splitlines()
    text = "\n".join(
        [lines[0]] + [line for line in lines[1:] if line.startswith("G5,")]
    )
    editor.accept_batch(merge_csv(text, before))
    assert len(editor.plan.trains) == 3 and len(editor.document()["routes"]) == 1
    editor.table.item(1, 9).setText("not-a-node")
    assert editor.table.item(1, 9).text() == ""
    editor.timer.stop()
    editor.close()


def test_styles_strict_width_color_and_persistence(tmp_path):
    from desktop.rail_style_ui import defaults, validate_styles, load_styles

    styles = defaults()
    styles["高速铁路线"] = {"color": "#127ac2", "width": 3.5}
    path = tmp_path / "styles.json"
    path.write_text(json.dumps(styles), encoding="utf-8")
    assert load_styles(path) == styles
    styles["高速铁路线"]["width"] = float("nan")
    with pytest.raises(ValueError):
        validate_styles(styles)
