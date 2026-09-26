import json
import sqlite3

import pytest

from desktop.rail_line_store import DiskRailLineLibrary, build_line_index, fingerprint, index_ready
from desktop.rail_lines import RESOLUTION_KEY, export_corridor_csv, import_corridor_csv, line_identity
from desktop.tests.test_line_membership import aliases, edge, install, seq


def fixture(tmp_path, after=False, **connector_options):
    if after:
        edges = [edge("w1:0-1", 1, 2, "来线"), edge("w2:0-1", 2, 3, "来线"),
                 edge("w3:0-1", 5, 6, "去线"), edge("w4:0-1", 6, 7, "去线"),
                 edge("w5:0-1", 3, 6, "连接轨", **connector_options)]
        arrival, departure = 2, 5
    else:
        edges = [edge("w1:0-1", 1, 2, "来线"), edge("w2:0-1", 2, 3, "来线"),
                 edge("w3:0-1", 4, 5, "去线"), edge("w4:0-1", 5, 7, "去线"),
                 edge("w5:0-1", 2, 4, "连接轨", **connector_options)]
        arrival, departure = 3, 5
    # An unnamed siding is hidden in normal line choices but must remain routable.
    edges[-1]["way_tags"] = {"service": "siding"}
    index = install(tmp_path, edges)
    aliases(index, [("node/mid", "换线站", 100, arrival, 1, "source", 1, 118.0004, 32),
                    ("node/mid", "换线站", 100, departure, 2, "source", 1, 118.0004, 32)])
    sequence = seq(1, line_identity(edges[0])[0], "station:node/mid")
    sequence += seq(0, line_identity(edges[2])[0], 7)[1:]
    return DiskRailLineLibrary(index), sequence, edges


@pytest.mark.parametrize("after,expected", [
    (False, ["w1:0-1", "w5:0-1", "w3:0-1", "w4:0-1"]),
    (True, ["w1:0-1", "w2:0-1", "w5:0-1", "w4:0-1"]),
])
def test_transfer_before_or_after_station_uses_real_connecting_track(tmp_path, after, expected, monkeypatch):
    lib, sequence, edges = fixture(tmp_path, after)
    normalized, path = lib.resolve_with_sequence(sequence, "auto")
    assert [leg["edge_id"] for leg in path] == expected
    assert line_identity(edges[-1])[0] in [entry["line_id"] for entry in normalized[1::2]]
    assert lib.resolve(normalized, "auto") == path
    selection = {"requested_sequence": sequence, "resolved_sequence": normalized, "path": path}
    assert lib.resolve(sequence, "auto", selection=selection) == path
    with pytest.raises(ValueError, match="没有连接"):
        lib.resolve(sequence, "strict")
    connector = line_identity(edges[-1])[0]
    junction = edges[-1]["from_node"]
    assert connector not in {line["id"] for line in lib.connected_lines(junction)}
    assert connector in {line["id"] for line in lib.connected_lines(junction, physical=True)}
    assert lib.reachable_nodes(junction, connector, physical=True)
    restricted = DiskRailLineLibrary(lib.path, metadata={"station:node/mid": {
        "connected_lines": [{"line_id": sequence[1]["line_id"], "anchor_node": 3}]}})
    assert sequence[3]["line_id"] in {line["id"] for line in restricted.connected_lines(5, physical=True)}
    original = lib.selected_library

    def bounded(ids, edge_ids=None):
        assert edge_ids is not None, "重新打开只读取已保存的区间"
        graph = original(ids, edge_ids=edge_ids)
        assert set(graph.edges) == set(expected)
        return graph

    monkeypatch.setattr(lib, "selected_library", bounded)
    assert lib.resolve(sequence, "auto", selection=selection) == path


@pytest.mark.parametrize("options", [{"direction": "reverse"}, {"direction": "closed"},
                                      {"construction_status": "planned"}])
def test_transfer_rejects_forbidden_or_nonoperating_connectors(tmp_path, options):
    lib, sequence, _ = fixture(tmp_path, **options)
    with pytest.raises(ValueError, match="未找到"):
        lib.resolve(sequence, "auto")


def test_transfer_does_not_jump_between_nearby_unconnected_nodes(tmp_path):
    lib, sequence, _ = fixture(tmp_path)
    with sqlite3.connect(lib.path) as db:
        db.execute("DELETE FROM edges WHERE id='w5:0-1'")
    with pytest.raises(ValueError, match="未找到"):
        lib.resolve(sequence, "auto")


def test_transfer_expands_local_search_and_does_not_relax_manual_anchors(tmp_path):
    edges = [edge("a", 1, 2, "来线"), edge("b", 5, 7, "去线"),
             edge("c", 2, 400, "连接轨"), edge("d", 400, 5, "连接轨")]
    index = install(tmp_path, edges)
    aliases(index, [("node/mid", "换线站", 100, 2, 1, "source", 1, 118.0004, 32),
                    ("node/mid", "换线站", 100, 5, 2, "source", 1, 118.0004, 32)])
    lib = DiskRailLineLibrary(index)
    sequence = seq(1, line_identity(edges[0])[0], "station:node/mid") + seq(0, line_identity(edges[1])[0], 7)[1:]
    assert [leg["edge_id"] for leg in lib.resolve(sequence, "auto")] == ["a", "c", "d", "b"]
    fixed = {"station:node/mid": {"connected_lines": [
        {"line_id": line_identity(edges[0])[0], "anchor_node": 2, "anchor_policy": "fixed"},
        {"line_id": line_identity(edges[1])[0], "anchor_node": 5, "anchor_policy": "fixed"}]}}
    with pytest.raises(ValueError, match="接轨点"):
        DiskRailLineLibrary(index, metadata=fixed).resolve(sequence, "auto")


def test_transfer_cannot_replace_an_explicit_interval(tmp_path):
    lib, sequence, edges = fixture(tmp_path)
    # The manual row ends on the incoming station track, beyond the only
    # crossover. A path through the outgoing station track would ignore it.
    section = lib.search_sections("", line_identity(edges[0])[0])[0]
    sequence[1]["section_id"] = section["id"]
    with pytest.raises(ValueError):
        lib.resolve(sequence, "auto")


def test_transfer_keeps_a_valid_arrival_when_another_would_backtrack(tmp_path):
    edges = [edge("in-a", 1, 2, "来线", length_m=1, direction="forward"),
             edge("in-b", 2, 3, "来线", length_m=1, direction="forward"),
             edge("in-other", 1, 5, "来线", length_m=100, direction="forward"),
             edge("backtrack", 3, 2, "去线", length_m=1, direction="forward"),
             edge("out-a", 5, 6, "去线", length_m=1, direction="forward"),
             edge("out-b", 6, 2, "去线", length_m=1, direction="forward"),
             edge("out-c", 2, 7, "去线", length_m=1, direction="forward")]
    index = install(tmp_path, edges)
    aliases(index, [("node/mid", "换线站", 100, 3, 1, "source", 1, 118.0004, 32),
                    ("node/mid", "换线站", 100, 5, 2, "source", 1, 118.0004, 32)])
    lib = DiskRailLineLibrary(index)
    sequence = seq(1, line_identity(edges[0])[0], "station:node/mid") + seq(0, line_identity(edges[3])[0], 7)[1:]
    assert [leg["edge_id"] for leg in lib.resolve(sequence, "auto")] == ["in-other", "out-a", "out-b", "out-c"]


def test_index_13_upgrade_only_builds_endpoint_indexes(tmp_path, monkeypatch):
    lib, _, _ = fixture(tmp_path)
    with sqlite3.connect(lib.path) as db:
        source = json.loads(db.execute("SELECT value FROM metadata WHERE key='source'").fetchone()[0])
        source[0] = 13
        db.execute("UPDATE metadata SET value=? WHERE key='source'", (json.dumps(source),))
        db.execute("DROP INDEX edge_from")
        db.execute("DROP INDEX edge_to")
    original = json.loads

    def only_signature(text, *args, **kwargs):
        assert not text.startswith("{"), "升级不能重新读取全国 geometry JSON"
        return original(text, *args, **kwargs)

    monkeypatch.setattr(json, "loads", only_signature)
    build_line_index(tmp_path / "rail.sqlite", lib.path, [], [])
    assert index_ready(lib.path, fingerprint(tmp_path / "rail.sqlite", []))
    with sqlite3.connect(lib.path) as db:
        assert db.execute("SELECT count(*) FROM edges").fetchone()[0] == 5
        assert {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")} >= {"edge_from", "edge_to"}


def test_editor_preserves_connections_and_can_expand_for_manual_edit(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QTableWidget, QPushButton, QCheckBox, QDialogButtonBox, QMessageBox
    from desktop.corridor_ui import CorridorPanel
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub

    lib, sequence, _ = fixture(tmp_path)
    editor = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
    qtbot.addWidget(editor)
    payload = {"schema": "railscope.rail-corridors.v2", "source": "test", "required_capabilities": [],
               "extensions": {}, "corridors": [{"id": "COR-TRANSFER", "name": "站前换线", "sequence": sequence,
               "extensions": {RESOLUTION_KEY: {"policy": "auto"}}}]}
    editor.merge_corridors(payload)
    route = editor.document()["routes"][-1]
    assert route["extensions"][RESOLUTION_KEY]["selection"]["requested_sequence"] == sequence
    assert len(route["path"]) == 4
    assert all(ref.edge_id.startswith("NE-") for ref in editor.domain_repo.corridors["COR-TRANSFER"].edge_refs)
    exported = editor.corridors_document(table=True)
    exported["corridors"][-1]["id"] = "COR-CSV"
    editor.merge_corridors(import_corridor_csv(export_corridor_csv(exported)))
    assert editor.document()["routes"][-1]["path"] == route["path"]
    panel = CorridorPanel(editor)
    qtbot.addWidget(panel)
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))

    def edit():
        dialog = QApplication.activeModalWidget()
        try:
            table = dialog.findChild(QTableWidget)
            assert table.rowCount() == 2
            assert table.cellWidget(0, 2).currentData() == "station:node/mid"
            dialog.findChild(QPushButton, "corridorExpandResolvedPath").click()
            assert dialog.findChild(QCheckBox, "corridorPhysicalEndpoints").isChecked()
            assert table.rowCount() == (len(route["sequence"]) - 1) // 2
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
        except Exception as error:
            errors.append(error)
        finally:
            dialog.reject()

    QTimer.singleShot(10, edit)
    panel.edit_table("COR-TRANSFER")
    assert not errors
    assert next(r for r in editor.document()["routes"] if r["id"] == "COR-TRANSFER")["path"] == route["path"]
    editor.save()
    restored = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
    qtbot.addWidget(restored)
    assert restored.document()["routes"][-1]["path"] == route["path"]
    editor.timer.stop()
    restored.timer.stop()
