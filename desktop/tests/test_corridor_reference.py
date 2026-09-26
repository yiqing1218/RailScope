from copy import deepcopy

import pytest

from desktop.rail_line_store import DiskRailLineLibrary
from desktop.rail_lines import RESOLUTION_KEY, RailLineLibrary, line_identity
from desktop.tests.test_line_membership import aliases, edge, install, seq


def test_auto_selects_reachable_station_anchors_and_strict_still_requires_choice(tmp_path):
    edges = [edge("a", 1, 2), edge("b", 2, 3), edge("unreachable", 7, 8)]
    index = install(tmp_path, edges)
    aliases(index, [("node/end", "终点", 100, 8, 1, "source", 1, 118, 32),
                    ("node/end", "终点", 100, 3, 10, "source", 1, 118, 32),
                    ("node/end", "终点", 100, 2, 50, "source", 1, 118, 32)])
    lib = DiskRailLineLibrary(index)
    line = line_identity(edges[0])[0]
    sequence = seq(1, line, "station:node/end")
    normalized, path = lib.resolve_with_sequence(sequence, "auto")
    # Choose a near-station anchor only after checking connectivity, not the
    # unreachable nearest node or an earlier node merely shortening the journey.
    assert normalized[-1]["node_id"] == 3
    assert [v["edge_id"] for v in path] == ["a", "b"]
    with pytest.raises(ValueError, match="unresolved"):
        lib.resolve(sequence, "strict")
    fixed = {"station:node/end": {"connected_lines": [{"line_id": line, "anchor_node": 8,
              "anchor_policy": "fixed"}]}}
    with pytest.raises(ValueError, match="不连通"):
        DiskRailLineLibrary(index, metadata=fixed).resolve(sequence, "auto")


def test_auto_uses_weighted_operating_path_with_deterministic_ties():
    edges = [edge("long", 1, 3, length_m=300), edge("a", 1, 2, length_m=10),
             edge("b", 2, 3, length_m=10), edge("same", 2, 3, length_m=10),
             edge("closed", 1, 3, length_m=1, direction="reverse"),
             edge("future", 1, 3, length_m=1, construction_status="planned")]
    sequence = seq(1, line_identity(edges[0])[0], 3)
    for values in (edges, list(reversed(edges))):
        library = RailLineLibrary(values, [])
        assert [v["edge_id"] for v in library.resolve(sequence, "auto")] == ["a", "b"]
        with pytest.raises(ValueError, match="多条合法"):
            library.resolve(sequence)
    library = RailLineLibrary([edge("oneway", 1, 2, direction="forward")], [])
    with pytest.raises(ValueError, match="不连通"):
        library.resolve(seq(2, next(iter(library.lines)), 1), "auto")


def test_auto_checks_the_complete_multiline_chain(tmp_path):
    edges = [edge("a", 1, 2, "甲"), edge("b", 1, 3, "甲"),
             edge("c", 2, 8, "乙"), edge("d", 3, 4, "乙")]
    index = install(tmp_path, edges)
    aliases(index, [("node/mid", "换线站", 100, 2, 1, "source", 1, 118, 32),
                    ("node/mid", "换线站", 100, 3, 20, "source", 1, 118, 32)])
    lib = DiskRailLineLibrary(index)
    sequence = seq(1, line_identity(edges[0])[0], "station:node/mid")
    sequence += seq(0, line_identity(edges[2])[0], 4)[1:]
    normalized, path = lib.resolve_with_sequence(sequence, "auto")
    assert normalized[2]["node_id"] == 3
    assert [v["edge_id"] for v in path] == ["b", "d"]


def test_manual_interval_overrides_the_automatic_shortest_path(tmp_path):
    edges = [edge("short", 1, 3, length_m=1), edge("a", 1, 2, length_m=10),
             edge("b", 2, 3, length_m=10), edge("tail", 3, 4), edge("entry", 0, 1)]
    lib = DiskRailLineLibrary(install(tmp_path, edges))
    line = line_identity(edges[0])[0]
    sequence = seq(1, line, 3)
    assert lib.resolve(sequence, "auto")[0]["edge_id"] == "short"
    section = next(s for s in lib.search_sections("", line) if len(s["path"]) == 2)
    sequence[1]["section_id"] = section["id"]
    assert [v["edge_id"] for v in lib.resolve(sequence, "auto")] == ["a", "b"]


def test_saved_reference_is_replayed_and_a_missing_edge_is_reported(tmp_path):
    edges = [edge("a", 1, 2, length_m=10), edge("b", 2, 3, length_m=10),
             edge("short", 1, 3, length_m=1), edge("tail", 3, 4)]
    lib = DiskRailLineLibrary(install(tmp_path, edges))
    line = line_identity(edges[0])[0]
    sequence = seq(1, line, 3)
    chosen = [{"edge_id": e, "direction": "forward"} for e in ("a", "b")]
    selection = {"requested_sequence": sequence, "resolved_sequence": sequence, "path": chosen}
    assert lib.resolve(sequence, "auto", selection=selection) == chosen
    assert lib.resolve(seq(1, line, 4), "auto", selection=selection)[0]["edge_id"] == "short"
    broken = deepcopy(selection)
    broken["path"][0]["edge_id"] = "missing"
    with pytest.raises(ValueError, match="缺少区间"):
        lib.resolve(sequence, "auto", selection=broken)
    broken = deepcopy(selection)
    broken["path"][0]["direction"] = "reverse"
    with pytest.raises(ValueError, match="不连续"):
        lib.resolve(sequence, "auto", selection=broken)


def test_csv_preserves_large_reference_selection_and_membership():
    from desktop.rail_lines import export_corridor_csv, import_corridor_csv

    sequence = seq("NN-A", "RLU-test", "NN-B")
    extensions = {
        RESOLUTION_KEY: {"policy": "auto", "selection": {
            "requested_sequence": sequence, "resolved_sequence": sequence,
            "path": [{"edge_id": f"NE-{index:040d}", "direction": "forward"} for index in range(3000)]}},
        "railscope.org/line-membership": {"groups": {"RLU-test": ["RL-A", "RL-B"]}},
    }
    document = {"corridors": [{"id": "COR-LONG", "name": "长通道", "sequence": sequence, "extensions": extensions}]}
    assert import_corridor_csv(export_corridor_csv(document))["corridors"] == document["corridors"]


def test_default_dialog_apply_reopen_roundtrip_and_domain_status(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QComboBox, QDialogButtonBox, QTableWidget, QMessageBox
    from desktop.corridor_ui import CorridorPanel
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub

    edges = [edge("w1:0-1", 1, 2), edge("w2:0-1", 2, 3), edge("w3:0-1", 1, 3)]
    install(tmp_path, edges)
    editor = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
    qtbot.addWidget(editor)
    # The editor may rebuild its index with reference metadata on first use.
    library = editor.line_library()
    aliases(library.path, [("node/start", "始发", 100, 1, 1, "source", 1, 118, 32),
                          ("node/end", "终到", 101, 2, 40, "source", 1, 118.01, 32),
                          ("node/end", "终到", 101, 3, 1, "source", 1, 118.01, 32)])
    sequence = seq("station:node/start", line_identity(edges[0])[0], "station:node/end")
    panel = CorridorPanel(editor)
    qtbot.addWidget(panel)
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))

    def apply_dialog(reopen=False):
        dialog = QApplication.activeModalWidget()
        try:
            policy = dialog.findChild(QComboBox, "corridorResolutionPolicy")
            assert policy.currentData() == "auto"
            assert policy.findData("strict") >= 0
            table = dialog.findChild(QTableWidget)
            if reopen:
                assert [table.cellWidget(0, 0).currentData(), table.cellWidget(0, 1).currentData(), table.cellWidget(1, 0).currentData()] == [
                    sequence[0]["node_id"], sequence[1]["line_id"], sequence[2]["node_id"]]
            else:
                for col, value in enumerate((sequence[0]["node_id"], sequence[1]["line_id"], sequence[2]["node_id"])):
                    combo = table.cellWidget(1, 0) if col == 2 else table.cellWidget(0, col)
                    combo.addItem(str(value), value)
                    combo.setCurrentIndex(combo.count() - 1)
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
        except Exception as error:
            errors.append(error)
        finally:
            dialog.reject()

    QTimer.singleShot(10, apply_dialog)
    panel.edit_table(None)
    assert not errors
    route = editor.document()["routes"][-1]
    resolution = route["extensions"][RESOLUTION_KEY]
    assert resolution["selection"]["requested_sequence"] == sequence
    assert resolution["selection"]["path"] == route["path"]
    assert resolution["snapshot"] and resolution["version"] == 2
    corridor = editor.domain_repo.corridors[route["id"]]
    assert corridor.verification_status == "automatic_reference_not_dispatch_verified"
    assert all(ref.edge_id.startswith("NE-") for ref in corridor.edge_refs)
    QTimer.singleShot(10, lambda: apply_dialog(True))
    panel.edit_table(route["id"])
    assert not errors
    exported = editor.corridors_document(table=True)
    exported["corridors"][-1]["id"] = "COR-REPLAY"
    editor.merge_corridors(exported)
    assert editor.document()["routes"][-1]["path"] == route["path"]
    from desktop.rail_lines import export_corridor_csv, import_corridor_csv
    csv_document = import_corridor_csv(export_corridor_csv(exported))
    csv_document["corridors"][-1]["id"] = "COR-CSV-REPLAY"
    editor.merge_corridors(csv_document)
    assert editor.document()["routes"][-1]["path"] == route["path"]
    editor.save()
    restored = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
    qtbot.addWidget(restored)
    assert restored.document()["routes"][-1]["path"] == route["path"]
    assert restored.document()["routes"][-1]["extensions"][RESOLUTION_KEY]["selection"]["requested_sequence"] == sequence
    edited = restored.corridors_document(table=True)
    edited["corridors"] = [edited["corridors"][-1]]
    manual = seq(1, sequence[1]["line_id"], 2) + seq(2, sequence[1]["line_id"], 3)[1:]
    edited["corridors"][0]["sequence"] = manual
    restored.merge_corridors(edited)
    changed = restored.document()["routes"][-1]
    assert [leg["edge_id"] for leg in changed["path"]] == ["w1:0-1", "w2:0-1"]
    assert changed["extensions"][RESOLUTION_KEY]["selection"]["requested_sequence"] == manual
    editor.timer.stop()
    restored.timer.stop()
