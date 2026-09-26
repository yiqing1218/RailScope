import json
import sqlite3

import pytest

from desktop.rail_line_store import DiskRailLineLibrary, build_line_index
from desktop.rail_lines import line_identity


def edge(ident, a, b, name="仙宁线", highspeed="no", **extra):
    return {"id": ident, "from_node": a, "to_node": b,
            "node_ids": [a, b],
            "coordinates": [[118 + a / 10000, 32], [118 + b / 10000, 32]],
            "way_tags": {"name": name, "ref": "X", "highspeed": highspeed}, **extra}


def install(tmp_path, edges):
    source, target = tmp_path / "rail.sqlite", tmp_path / "rail_lines.sqlite"
    with sqlite3.connect(source) as db:
        db.executescript("CREATE TABLE edges(id TEXT PRIMARY KEY,data TEXT); CREATE TABLE features(kind TEXT,data TEXT)")
        db.executemany("INSERT INTO edges VALUES(?,?)", ((e["id"], json.dumps(e)) for e in edges))
    build_line_index(source, target, [], [])
    return target


def seq(start, line, end):
    return [{"kind": "endpoint", "node_id": start}, {"kind": "line", "line_id": line},
            {"kind": "endpoint", "node_id": end}]


def aliases(path, rows):
    with sqlite3.connect(path) as db:
        db.executemany("INSERT INTO station_aliases VALUES(?,?,?,?,?,?,?,?,?)", rows)


def test_tag_boundary_and_reachable_station_anchor(tmp_path):
    edges = [edge("a", 1, 2), edge("b", 2, 3, highspeed="yes"), edge("c", 3, 4),
             edge("opposite", 7, 8)]
    index = install(tmp_path, edges)
    aliases(index, [("node/start", "仙林", 100, 1, 10, "source", 1, 118, 32),
                    ("node/end", "南京南", 101, 8, 5, "source", 1, 118.001, 32),
                    ("node/end", "南京南", 101, 4, 50, "source", 1, 118.001, 32)])
    lib = DiskRailLineLibrary(index)
    choices = lib.connected_lines("station:node/start")
    assert len(choices) == 1 and choices[0]["edge_count"] == 4
    line = choices[0]["id"]
    assert any(key == "station:node/end" for key, _ in lib.reachable_nodes("station:node/start", line))
    sequence = seq("station:node/start", line, "station:node/end")
    assert lib.normalize_sequence(sequence)[-1]["node_id"] == 4  # not nearest, unreachable 8
    assert [v["edge_id"] for v in lib.resolve(sequence)] == ["a", "b", "c"]
    # Existing source IDs remain valid; no geometry or edge identity rewrite.
    assert lib.resolve(seq(1, line_identity(edges[1])[0], 4)) == lib.resolve(sequence)
    # Legacy station forms stored the closest anchor as 'manual' even though
    # the user had selected only a line. Preserve that line, not the wrong track.
    override = {"station:node/end": {"connected_lines": [{"line_id": line, "anchor_node": 8,
                 "distance_m": 5, "source": "manual", "verification_status": "user_verified"}]}}
    legacy = DiskRailLineLibrary(index, metadata=override)
    assert legacy.resolve(sequence) == lib.resolve(sequence)
    assert any(key == "station:node/end" for key, _ in legacy.reachable_nodes("station:node/start", line))
    override["station:node/end"]["connected_lines"][0]["anchor_policy"] = "fixed"
    with pytest.raises(ValueError, match="不连通"):
        DiskRailLineLibrary(index, metadata=override).resolve(sequence)


def test_grouping_requires_shared_topology_and_matching_identity(tmp_path):
    a = edge("a", 1, 2)
    remote = edge("remote", 3, 4, highspeed="yes")
    changed_ref = edge("ref", 2, 5, highspeed="yes")
    changed_ref["way_tags"]["ref"] = "other"
    yard = edge("yard", 2, 6, highspeed="yes")
    yard["way_tags"]["service"] = "siding"
    planned = edge("future", 2, 7, highspeed="yes", construction_status="planned")
    lib = DiskRailLineLibrary(install(tmp_path, [a, remote, changed_ref, yard, planned]))
    assert not lib.workspace.groups
    with pytest.raises(ValueError):
        lib.resolve(seq(1, line_identity(a)[0], 4))


def test_manual_merge_split_and_export_preserve_existing_references(tmp_path):
    a, b = edge("a", 1, 2, "甲线"), edge("b", 2, 3, "乙线")
    index = install(tmp_path, [a, b])
    ids = [line_identity(e)[0] for e in (a, b)]
    grouped = {key: {"assembly_id": "RLU-test", "assembly_name": "手工联络线"} for key in ids}
    lib = DiskRailLineLibrary(index, metadata=grouped)
    assert lib.search_lines("手工联络线")[0]["id"] == "RLU-test"
    assert {s["id"] for s in lib.search_sections("", "RLU-test")} == {s["id"] for s in lib.sections()}
    sequence = seq(1, "RLU-test", 3)
    path = lib.resolve(sequence)
    saved = lib.membership_provenance(sequence)
    assert DiskRailLineLibrary(index).with_membership(saved).resolve(sequence) == path
    split = {key: {"separate_catalog_entry": True} for key in ids}
    split["line-assembly:RLU-test"] = {"members": ids, "name": "手工联络线", "active": False}
    lib = DiskRailLineLibrary(index, metadata=split)
    assert {r["id"] for r in lib.connected_lines(2)} == set(ids)
    assert lib.resolve(sequence) == path  # historical group remains resolvable
    assert not lib.search_lines("手工联络线")
    with pytest.raises(ValueError, match="conflict"):
        lib.with_membership({"groups": {"RLU-missing": ["missing"]}})
    with pytest.raises(ValueError, match="conflict"):
        lib.with_membership({"groups": {"RLU-test": ids[:1]}})


def test_manual_composition_does_not_bridge_a_gap_or_choose_a_branch(tmp_path):
    edges = [edge("a", 1, 2, "甲线"), edge("b", 2, 3, "乙线"),
             edge("c", 2, 4, "乙线"), edge("d", 4, 3, "乙线"), edge("far", 8, 9, "丙线")]
    index = install(tmp_path, edges)
    metadata = {line_identity(e)[0]: {"assembly_id": "RLU-test", "assembly_name": "组合线"} for e in edges}
    lib = DiskRailLineLibrary(index, metadata=metadata)
    with pytest.raises(ValueError, match="不连通"):
        lib.resolve(seq(1, "RLU-test", 9))
    with pytest.raises(ValueError, match="分支"):
        lib.resolve(seq(1, "RLU-test", 3))


def test_multiple_reachable_station_anchors_remain_unresolved(tmp_path):
    index = install(tmp_path, [edge("a", 1, 2), edge("b", 2, 3)])
    aliases(index, [("node/end", "终点", 100, 2, 1, "source", 1, 118, 32),
                    ("node/end", "终点", 100, 3, 30, "source", 1, 118, 32)])
    lib = DiskRailLineLibrary(index)
    line = lib.search_lines()[0]["id"]
    with pytest.raises(ValueError, match="unresolved"):
        lib.resolve(seq(1, line, "station:node/end"))
    assert lib.resolve(seq(1, line, 3))


def test_reachable_picker_respects_direction_and_operating_status(tmp_path):
    index = install(tmp_path, [edge("a", 1, 2, direction="forward"),
                               edge("future", 2, 3, construction_status="planned")])
    aliases(index, [("node/start", "始发", 101, 1, 0, "source", 1, 118, 32),
                    ("node/end", "终到", 102, 2, 0, "source", 1, 118.002, 32)])
    lib = DiskRailLineLibrary(index)
    line = lib.search_lines()[0]["id"]
    assert not lib.reachable_nodes("station:node/end", line)
    assert {key for key, label in lib.reachable_nodes(1, line, physical=True)} >= {2}
    assert 3 not in {key for key, label in lib.reachable_nodes(1, line, physical=True)}


def test_catalog_operations_change_corridor_membership_and_undo_keeps_references(qtbot, tmp_path):
    from desktop.rail_catalog_ui import RailCatalog
    from desktop.tests.test_operating_ui import MapStub

    edges = [edge("w1:0-1", 1, 2, "甲线"), edge("w2:0-1", 2, 3, "乙线")]
    index = install(tmp_path, edges)
    ids = [line_identity(e)[0] for e in edges]
    catalog = {key: {"id": key, "name": e["way_tags"]["name"], "line_id": key,
                     "province": "江苏省", "track_type": "普速铁路线", "way_ids": [number]}
               for number, (key, e) in enumerate(zip(ids, edges), 1)}
    (tmp_path / "rail_catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    settings = tmp_path / "workspace.json"
    widget = RailCatalog(tmp_path, settings, MapStub(), shared_path=tmp_path / "no-shared.json")
    qtbot.addWidget(widget)

    def library():
        return DiskRailLineLibrary(index, metadata=json.loads(settings.read_text(encoding="utf-8")))

    assembly = widget.merge_line_segments(set(ids), "手工线")
    sequence = seq(1, assembly, 3)
    path = library().resolve(sequence)
    widget.undo_catalog()
    assert library().resolve(sequence) == path
    assert {line["id"] for line in library().connected_lines(2)} == set(ids)
    widget.redo_catalog()
    assert [line["id"] for line in library().connected_lines(2)] == [assembly]
    widget.split_line_assembly({ids[0]})
    assert library().resolve(sequence) == path
    assert {line["id"] for line in library().connected_lines(2)} == set(ids)


def test_operating_editor_replays_exported_manual_membership(qtbot, tmp_path):
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub

    edges = [edge("w1:0-1", 1, 2, "甲线"), edge("w2:0-1", 2, 3, "乙线")]
    install(tmp_path, edges)
    metadata_path = tmp_path / "manual.json"
    metadata_path.write_text(json.dumps({line_identity(e)[0]: {
        "assembly_id": "RLU-test", "assembly_name": "组合线"} for e in edges}), encoding="utf-8")
    editor = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json", metadata_path)
    qtbot.addWidget(editor)
    editor.merge_corridors({"schema": "railscope.rail-corridors.v2", "source": "test", "required_capabilities": [],
                           "extensions": {}, "corridors": [{"id": "COR-MANUAL", "name": "测试",
                           "sequence": seq(1, "RLU-test", 3), "extensions": {}}]})
    exported = editor.corridors_document(table=True)
    assert exported["corridors"][0]["extensions"]["railscope.org/line-membership"]["groups"]
    original_path = editor.document()["routes"][0]["path"]
    group_id = editor.domain_bindings["lines"]["RLU-test"]
    assert len([value for value in editor.domain_repo.memberships if value.line_id == group_id]) == 2
    assert len(editor.domain_repo.edges) == 2
    metadata_path.write_text("{}", encoding="utf-8")
    editor.invalidate_line_library()
    exported["corridors"][0]["id"] = "COR-REPLAY"
    editor.merge_corridors(exported)
    assert editor.document()["routes"][-1]["path"] == original_path
    editor.timer.stop()


def test_exact_section_cannot_bypass_nonoperating_edge_check():
    from desktop.rail_lines import RailLineLibrary

    e = edge("closed", 1, 2, construction_status="planned")
    library = RailLineLibrary([e], [])
    # Exact-section lookup is tested independently of the section picker's
    # filters: imported notation must enforce the same operational invariant.
    library.section = lambda ident, line: {"from_node": 1, "to_node": 2,
        "path": [{"edge_id": "closed", "direction": "forward"}]}
    sequence = seq(1, line_identity(e)[0], 2)
    sequence[1]["section_id"] = "RS-test"
    with pytest.raises(ValueError, match="非运营"):
        library.resolve(sequence)


def test_exact_sections_survive_apply_and_export_with_unloaded_branches(qtbot, tmp_path):
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub

    edges = [edge("w1:0-1", 1, 2), edge("w2:0-1", 2, 3),
             edge("w3:0-1", 2, 4), edge("w4:0-1", 4, 3), edge("w5:0-1", 3, 5)]
    install(tmp_path, edges)
    editor = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
    qtbot.addWidget(editor)
    library = editor.line_library()
    line = line_identity(edges[0])[0]
    sections = library.search_sections("", line)
    sequence = [{"kind": "endpoint", "node_id": 1}]
    for a, b in [(1, 2), (2, 3)]:
        section = next(s for s in sections if {s["from_node"], s["to_node"]} == {a, b} and len(s["path"]) == 1)
        sequence += [{"kind": "line", "line_id": line, "section_id": section["id"]},
                     {"kind": "endpoint", "node_id": b}]
    editor.merge_corridors({"schema": "railscope.rail-corridors.v2", "source": "test", "required_capabilities": [],
                           "extensions": {}, "corridors": [{"id": "COR-SECTION", "name": "区间选择",
                           "sequence": sequence, "extensions": {}}]})
    assert editor.corridors_document(table=True)["corridors"][0]["sequence"] == sequence
    from desktop.corridor_ui import CorridorPanel
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QTableWidget, QDialogButtonBox
    panel = CorridorPanel(editor)
    qtbot.addWidget(panel)
    problems = []

    def reopen():
        dialog = QApplication.activeModalWidget()
        try:
            table = dialog.findChild(QTableWidget)
            assert [table.cellWidget(row, 1).property("sectionId") for row in range(2)] == [v["section_id"] for v in sequence[1::2]]
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
        except Exception as error:
            problems.append(error)
        finally:
            dialog.reject()

    QTimer.singleShot(20, reopen)
    panel.edit_table("COR-SECTION")
    assert not problems
    assert editor.document()["routes"][0]["sequence"] == sequence
    editor.timer.stop()
