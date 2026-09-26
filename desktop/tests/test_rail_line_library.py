import pytest
import sqlite3

from desktop.tests.test_workspace_revision import reference, install_reference_database


def test_resolved_path_reports_only_traversed_switches_and_signal_box(tmp_path):
    from desktop.rail_line_store import DiskRailLineLibrary

    index = tmp_path / "rail_lines.sqlite"
    with sqlite3.connect(index) as db:
        db.executescript(
            "CREATE TABLE edges(id TEXT PRIMARY KEY,line_id TEXT,a INTEGER,b INTEGER,"
            "construction INTEGER,length_m REAL,track_type TEXT,evidence TEXT,source_way TEXT,direction TEXT);"
            "CREATE TABLE nodes(id INTEGER PRIMARY KEY,label TEXT,kind TEXT);"
        )
        db.executemany(
            "INSERT INTO edges VALUES(?,?,?,?,?,?,?,?,?,?)",
            [("e1", "line", 1, 2, 0, 1, "rail", "test", "1", "both"),
             ("e2", "line", 2, 3, 0, 1, "rail", "test", "2", "both"),
             ("e3", "line", 2, 4, 0, 1, "rail", "test", "3", "both")],
        )
        db.executemany("INSERT INTO nodes VALUES(?,?,?)", [(1, "A", "station"), (2, "2", "switch"), (3, "B", "station")])
    library = DiskRailLineLibrary(index, metadata={"station:signalbox/test": {"display_name": "测试线路所", "member_switch_ids": [2]}})
    assert library.switches_on_path([{"edge_id": "e1", "direction": "forward"}, {"edge_id": "e2", "direction": "forward"}]) == [
        {"source_node_id": 2, "signal_box": "测试线路所"}
    ]
    assert library.switches_on_path([{"edge_id": "e2", "direction": "reverse"}, {"edge_id": "e3", "direction": "forward"}]) == [
        {"source_node_id": 2, "signal_box": "测试线路所"}
    ]
    with pytest.raises(ValueError, match="不连续"):
        library.switches_on_path([{"edge_id": "e1", "direction": "forward"}, {"edge_id": "e2", "direction": "reverse"}])


def test_endpoint_line_table_reuses_infrastructure_and_rejects_disconnects():
    from desktop.rail_lines import RailLineLibrary

    data = reference()
    library = RailLineLibrary(data["edges"], data["points"])
    sequence = library.describe(data["plan"]["trains"][0]["path"])
    assert sequence[0]["kind"] == sequence[-1]["kind"] == "endpoint"
    assert library.resolve(sequence) == data["plan"]["trains"][0]["path"]
    assert len({v["name"] for v in library.lines.values()}) == len(library.lines)
    bad = [dict(v) for v in sequence]
    bad[-1]["node_id"] = -1
    with pytest.raises(ValueError):
        library.resolve(bad)


def test_branch_does_not_choose_an_arbitrary_track():
    from desktop.rail_lines import RailLineLibrary

    def edge(ident, a, b):
        return {
            "id": ident,
            "from_node": a,
            "to_node": b,
            "node_ids": [a, b],
            "coordinates": [[a, 0], [b, 0]],
            "construction": False,
            "way_tags": {"name": "测试线"},
        }

    library = RailLineLibrary(
        [edge("a", 1, 2), edge("b", 2, 3), edge("c", 2, 4), edge("d", 4, 3)], []
    )
    ident = next(iter(library.lines))
    with pytest.raises(ValueError, match="分支"):
        library.resolve(
            [
                {"kind": "endpoint", "node_id": 1},
                {"kind": "line", "line_id": ident},
                {"kind": "endpoint", "node_id": 3},
            ]
        )


def test_exact_section_resolves_ambiguous_route_without_overshooting_endpoints():
    from desktop.rail_lines import RailLineLibrary

    def edge(ident, a, b):
        return {
            "id": ident,
            "from_node": a,
            "to_node": b,
            "node_ids": [a, b],
            "coordinates": [[a, 0], [b, 0]],
            "construction": False,
            "way_tags": {"name": "测试线"},
        }

    library = RailLineLibrary(
        [edge("main-a", 1, 2), edge("main-b", 2, 3), edge("branch-a", 2, 4), edge("branch-b", 4, 3)],
        [{"properties": {"osm_node_id": 2, "kind": "switch", "name": "二号道岔"}}],
    )
    line_id = next(iter(library.lines))
    sections = library.search_sections("", line_id)
    chosen = next(section for section in sections if section["from_node"] == 1)
    sequence = [
        {"kind": "endpoint", "node_id": chosen["from_node"]},
        {"kind": "line", "line_id": line_id, "section_id": chosen["id"]},
        {"kind": "endpoint", "node_id": chosen["to_node"]},
    ]
    resolved = library.resolve(sequence)
    assert resolved == chosen["path"]
    first = library.edges[resolved[0]["edge_id"]]
    last = library.edges[resolved[-1]["edge_id"]]
    assert first["from_node"] == chosen["from_node"]
    assert last["to_node"] == chosen["to_node"]


def test_line_resolution_enforces_persisted_track_direction():
    from desktop.rail_lines import RailLineLibrary

    edge = {
        "id": "one-way",
        "from_node": 1,
        "to_node": 2,
        "node_ids": [1, 2],
        "coordinates": [[120, 30], [120.01, 30]],
        "construction": False,
        "direction": "forward",
        "way_tags": {"name": "单向测试线"},
    }
    library = RailLineLibrary([edge], [])
    line_id = next(iter(library.lines))
    assert library.resolve(
        [
            {"kind": "endpoint", "node_id": 1},
            {"kind": "line", "line_id": line_id},
            {"kind": "endpoint", "node_id": 2},
        ]
    ) == [{"edge_id": "one-way", "direction": "forward"}]
    with pytest.raises(ValueError, match="不连通"):
        library.resolve(
            [
                {"kind": "endpoint", "node_id": 2},
                {"kind": "line", "line_id": line_id},
                {"kind": "endpoint", "node_id": 1},
            ]
        )


def test_table_corridor_roundtrip_renaming_and_shared_sections(tmp_path):
    import json

    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub

    app = QApplication.instance() or QApplication([])
    assert app
    install_reference_database(tmp_path)
    metadata_path = tmp_path / "settings" / "rail_catalog.json"
    editor = RailEditor(
        MapStub(), tmp_path, tmp_path / "plan.json", metadata_path
    )
    document = editor.corridors_document(table=True)
    assert document["schema"] == "railscope.rail-corridors.v2"
    assert "path" not in document["corridors"][0], (
        "交换表只需要端点和线路，不逐条输入 OSM"
    )
    sequence = document["corridors"][0]["sequence"]
    assert len(sequence) == 3, "北京南—京沪高铁—上海虹桥，中间未换线节点隐含"
    document["corridors"][0]["id"] = "COR-JINGHU-DOWN"
    document["corridors"][0]["name"] = "京沪下行"
    editor.merge_corridors(document)
    old = editor.document()
    library = editor.line_library()
    ident = sequence[1]["line_id"]
    metadata_path.parent.mkdir()
    metadata_path.write_text(
        json.dumps(
            {ident: {"display_name": "工作区目录名", "track_type": "城际铁路线"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert editor.line_library().lines[ident]["name"] == "工作区目录名"
    assert editor.line_library().lines[ident]["track_type"] == "城际铁路线"
    editor.save_line_names({ident: "京沪高速铁路主线"})
    assert editor.line_library().lines[ident]["name"].startswith("京沪高速铁路主线")
    assert editor.document() == old, "改名不能破坏通道、车次或物理引用"
    sections = list(editor.line_library().sections())
    assert {leg["edge_id"] for section in sections for leg in section["path"]} == set(
        library.edges
    )
    assert sum(len(section["path"]) for section in sections) == len(library.edges)
    editor.resize(1100, 850)
    editor.show()
    editor.tabs.setCurrentIndex(1)
    QTest.qWait(150)
    assert editor.diagram.transform().m11() == 1
    assert editor.scene.sceneRect().height() >= editor.diagram.viewport().height() - 5
    assert editor.scene.sceneRect().width() >= editor.diagram.viewport().width() - 5
    editor.save()
    restored = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
    assert restored.document()["routes"][-1]["sequence"] == sequence
    assert restored.line_library().lines[ident]["name"].startswith("京沪高速铁路主线")
    for item in (editor, restored):
        item.timer.stop()
        item.close()


def test_strict_corridor_csv_rows_alternate_endpoints_and_lines():
    from desktop.rail_lines import import_corridor_csv, export_corridor_csv

    header = "corridor_id,corridor_name,segment,from_node,line_id,to_node\n"
    text = header + "COR-X,测试通道,1,1,RL-A,2\nCOR-X,测试通道,2,2,RL-B,3\n"
    document = import_corridor_csv(text)
    assert len(document["corridors"][0]["sequence"]) == 5
    assert import_corridor_csv(export_corridor_csv(document)) == document
    with pytest.raises(ValueError, match="共用端点"):
        import_corridor_csv(text.replace("2,2,RL-B", "2,9,RL-B"))
    with pytest.raises(ValueError, match="序号"):
        import_corridor_csv(text.replace("2,2,RL-B", "3,2,RL-B"))

    stable = (
        "corridor_id,corridor_name,segment,from_node,line_id,section_id,to_node\n"
        "COR-S,稳定通道,1,NN-A,IL-A,RS-A,NN-B\n"
    )
    parsed = import_corridor_csv(stable)
    assert parsed["corridors"][0]["sequence"] == [
        {"kind": "endpoint", "node_id": "NN-A"},
        {"kind": "line", "line_id": "IL-A", "section_id": "RS-A"},
        {"kind": "endpoint", "node_id": "NN-B"},
    ]


def test_national_line_directory_does_not_load_geometry(tmp_path):
    import sqlite3
    import json
    from PySide6.QtWidgets import QApplication
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub

    app = QApplication.instance() or QApplication([])
    assert app
    editor = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
    edge = {
        "id": "w900000:0-1",
        "from_node": 1,
        "to_node": 2,
        "construction": False,
        "way_tags": {"name": "另一条测试线路"},
        "coordinates": [[120, 30], [121, 31]],
        "node_ids": [1, 2],
    }
    with sqlite3.connect(tmp_path / "rail.sqlite") as db:
        db.executescript(
            "CREATE TABLE edges(id TEXT,data TEXT); CREATE TABLE features(kind TEXT,data TEXT);"
        )
        db.execute("INSERT INTO edges VALUES(?,?)", (edge["id"], json.dumps(edge)))
        db.execute(
            "INSERT INTO features VALUES(?,?)",
            (
                "railPoints",
                json.dumps(
                    {
                        "properties": {
                            "osm_node_id": 1,
                            "name": "测试线路所",
                            "kind": "junction",
                        }
                    }
                ),
            ),
        )
    library = editor.line_library()
    assert "coordinates" not in library.edges[edge["id"]]
    assert "node_ids" not in library.edges[edge["id"]]
    assert library.nodes[1] == "测试线路所 · 线路所 · 1"
    assert len(library.search_lines("另一条")) == 1
    from desktop.rail_line_store import DiskRailLineLibrary
    from desktop.rail_lines import line_identity

    assert isinstance(library, DiskRailLineLibrary)
    ident = line_identity(edge)[0]
    sequence = [
        {"kind": "endpoint", "node_id": 1},
        {"kind": "line", "line_id": ident},
        {"kind": "endpoint", "node_id": 2},
    ]
    assert library.resolve(sequence) == [
        {"edge_id": edge["id"], "direction": "forward"}
    ]
    assert library.search_nodes("测试", ident) == [(1, "测试线路所 · 线路所 · 1")]
    editor.save_line_names({ident: "试验干线"})
    assert library.search_lines("试验干线")[0]["id"] == ident
    export_path = tmp_path / "中文 线路.json"
    library.write_export(export_path)
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert exported["schema"] == "railscope.rail-graph.v1"
    assert exported["corridor_format"]["sequence"] == "endpoint-line-endpoint-line-endpoint"
    assert any(s["line_id"] == ident for s in exported["sections"])
    editor.merge_corridors(
        {
            "schema": "railscope.rail-corridors.v2",
            "source": "测试",
            "required_capabilities": [],
            "extensions": {},
            "corridors": [
                {
                    "id": "COR-TEST",
                    "name": "测试通道",
                    "sequence": sequence,
                    "extensions": {
                        "railscope.org/path-resolution": {"policy": "strict"}
                    },
                }
            ],
        }
    )
    table_document = editor.corridors_document(table=True)
    table_document["corridors"][0]["id"] = "COR-REUSE"
    editor.merge_corridors(table_document)
    assert (
        editor.document()["routes"][-1]["sequence"]
        == table_document["corridors"][0]["sequence"]
    )
    editor.timer.stop()
    editor.close()


def test_disk_sections_split_at_other_line_junction_and_cancel_is_atomic(tmp_path):
    import json
    import sqlite3
    from desktop.rail_line_store import build_line_index, DiskRailLineLibrary

    source, target = tmp_path / "rail.sqlite", tmp_path / "index.sqlite"
    with sqlite3.connect(source) as db:
        db.executescript(
            "CREATE TABLE edges(data TEXT); CREATE TABLE features(kind TEXT,data TEXT);"
        )
        for ident, a, b, name in [
            ("w1:1-2", 1, 2, "干线"),
            ("w2:2-3", 2, 3, "干线"),
            ("w3:2-4", 2, 4, "支线"),
        ]:
            db.execute(
                "INSERT INTO edges VALUES(?)",
                (
                    json.dumps(
                        {
                            "id": ident,
                            "from_node": a,
                            "to_node": b,
                            "way_tags": {"name": name},
                        }
                    ),
                ),
            )
    build_line_index(source, target, [], [])
    library = DiskRailLineLibrary(target)
    sections = list(library.sections())
    assert len(sections) == 3
    assert all(2 in (s["from_node"], s["to_node"]) for s in sections)
    saved = target.read_bytes()

    def cancel(text):
        raise ValueError("cancel")

    with pytest.raises(ValueError, match="cancel"):
        build_line_index(source, target, [], [], cancel)
    assert target.read_bytes() == saved
    assert not list(tmp_path.glob("*.tmp"))


def test_station_building_alias_maps_to_nearby_real_topology_endpoint(tmp_path):
    import json
    import sqlite3
    from desktop.rail_line_store import build_line_index, DiskRailLineLibrary

    source, target = tmp_path / "rail.sqlite", tmp_path / "index.sqlite"
    edge = {
        "id": "w1:0-1",
        "from_node": 10,
        "to_node": 11,
        "coordinates": [[121.4500, 31.2500], [121.4520, 31.2500]],
        "way_tags": {"name": "京沪铁路"},
    }
    parallel = {
        "id": "w2:0-1",
        "from_node": 20,
        "to_node": 21,
        "coordinates": [[121.4500, 31.2530], [121.4520, 31.2530]],
        "way_tags": {"name": "沪宁城际铁路"},
    }
    station = {
        "type": "Feature",
        "properties": {
            "osm_node_id": 100,
            "name": "上海",
            "kind": "station",
        },
        "geometry": {"type": "Point", "coordinates": [121.4510, 31.2510]},
    }
    building = {
        "type": "Feature",
        "properties": {
            "osm_way_id": 200,
            "source_name": "上海站",
            "associated_station_ids": [100],
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[121.45, 31.25], [121.451, 31.25], [121.45, 31.25]]],
        },
    }
    area_only = {
        "type": "Feature",
        "properties": {
            "osm_way_id": 201,
            "source_name": "仅面要素车站",
            "associated_station_ids": [],
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [121.4518, 31.2498],
                    [121.4521, 31.2498],
                    [121.4521, 31.2501],
                    [121.4518, 31.2498],
                ]
            ],
        },
    }
    with sqlite3.connect(source) as db:
        db.executescript(
            "CREATE TABLE edges(data TEXT); CREATE TABLE features(kind TEXT,data TEXT);"
        )
        db.executemany(
            "INSERT INTO edges VALUES(?)",
            ((json.dumps(item),) for item in (edge, parallel)),
        )
        db.execute("INSERT INTO features VALUES(?,?)", ("railPoints", json.dumps(station)))
        db.execute(
            "INSERT INTO features VALUES(?,?)",
            ("railStationAreas", json.dumps(building)),
        )
        db.execute(
            "INSERT INTO features VALUES(?,?)",
            ("railStationAreas", json.dumps(area_only)),
        )
    build_line_index(source, target, [], [])
    library = DiskRailLineLibrary(target)
    results = library.search_nodes("上海站")
    assert len(results) == 1
    assert results[0][0] in (10, 11)
    assert "上海站" in results[0][1] and "邻近轨道端点" in results[0][1]
    assert library.search_nodes("仅面要素车站")[0][0] == 11
    from desktop.rail_lines import line_identity

    parallel_line = line_identity(parallel)[0]
    assert library.search_nodes("上海站", parallel_line)[0][0] in (20, 21)


def test_search_choice_keeps_only_bounded_results(qtbot):
    from desktop.corridor_ui import SearchChoice

    choice = SearchChoice(lambda text: [(123, "测试车站")], "搜索")
    qtbot.addWidget(choice)
    choice.setEditText("测试")
    choice.find_results()
    assert choice.count() == 1
    choice.select_result("测试车站 · 123")
    assert choice.currentData() == 123


def test_corridor_stop_candidates_use_index_without_scanning_source(qtbot, tmp_path, monkeypatch):
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub

    install_reference_database(tmp_path)
    editor = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
    qtbot.addWidget(editor)
    editor.line_library()
    expected = editor.corridor_stop_candidates()
    assert expected
    original_connect = sqlite3.connect

    def indexed_only(path, *args, **kwargs):
        if str(path) == str(tmp_path / "rail.sqlite"):
            raise AssertionError("车站候选查询不应扫描原始铁路库")
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", indexed_only)
    assert editor.corridor_stop_candidates() == expected
    editor.timer.stop()


def test_corridor_editor_infers_single_shared_endpoint_when_next_line_is_chosen(qtbot, tmp_path):
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication, QTableWidget, QPushButton
    from desktop.corridor_ui import CorridorPanel
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub

    class ChoiceLibrary:
        lines = {"RL-A": {"name": "甲线"}, "RL-B": {"name": "乙线"}}

        def endpoint_nodes(self, value):
            return [value] if value in {"A", "X", "Z"} else []

        def endpoint_label(self, value):
            return {"A": "甲站", "X": "换线站", "Z": "终点站"}[value]

        def endpoint_choice_label(self, value):
            return self.endpoint_label(value) + " · 接轨：甲线 / 乙线"

        def search_endpoints(self, query="", line_id=None):
            return [("A", self.endpoint_choice_label("A"))]

        def connected_lines(self, endpoint, query=""):
            values = {"A": [("RL-A", "甲线")], "X": [("RL-A", "甲线"), ("RL-B", "乙线")], "Z": []}[endpoint]
            return [{"id": key, "name": name} for key, name in values if query in name]

        def search_lines(self, query=""):
            return [{"id": key, "name": value["name"]} for key, value in self.lines.items() if query in value["name"]]

        def reachable_nodes(self, *_args, **_kwargs):
            return [("X", self.endpoint_choice_label("X")), ("Z", self.endpoint_choice_label("Z"))]

        def common_transfer_endpoint(self, start, first, second):
            return "X" if (start, first, second) == ("A", "RL-A", "RL-B") else None

    editor = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
    editor.line_library = lambda interactive=True: ChoiceLibrary()
    panel = CorridorPanel(editor)
    qtbot.addWidget(editor)
    qtbot.addWidget(panel)
    problems = []

    def fill():
        dialog = QApplication.activeModalWidget()
        try:
            table = dialog.findChild(QTableWidget)
            start = table.cellWidget(0, 0)
            start.setEditText("甲站")
            start.find_results()
            start.setCurrentIndex(0)
            first_line = table.cellWidget(0, 1)
            first_line.setEditText("甲线")
            first_line.find_results()
            first_line.setCurrentIndex(0)
            assert table.cellWidget(1, 0).lineEdit().alignment() == Qt.AlignmentFlag.AlignLeft
            second_line = table.cellWidget(1, 1)
            second_line.setEditText("乙线")
            second_line.find_results()
            second_line.setCurrentIndex(0)
            assert table.columnCount() == 2 and table.rowCount() == 3
            assert table.cellWidget(1, 0).currentData() == "X"
            assert table.cellWidget(1, 1).currentData() == "RL-B"
        except Exception as error:
            problems.append(error)
        finally:
            dialog.reject()

    QTimer.singleShot(20, fill)
    panel.edit_table(None)
    assert not problems
    editor.timer.stop()


def test_dialog_creates_corridor_from_search_results_and_renames(qtbot, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QApplication,
        QLineEdit,
        QTableWidget,
        QDialogButtonBox,
    )
    from desktop.corridor_ui import CorridorPanel
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub

    install_reference_database(tmp_path)
    editor = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
    editor.line_library()
    panel = CorridorPanel(editor)
    qtbot.addWidget(editor)
    qtbot.addWidget(panel)
    problems = []

    def fill():
        dialog = QApplication.activeModalWidget()
        try:
            dialog.findChild(QLineEdit, "corridorId").setText("COR-UI-TEST")
            dialog.findChild(QLineEdit, "corridorName").setText("表格编制京沪下行")
            table = dialog.findChild(QTableWidget)
            for col, query in [(0, "北京南"), (1, "京沪"), (2, "上海虹桥")]:
                choice = table.cellWidget(1, 0) if col == 2 else table.cellWidget(0, col)
                choice.setEditText(query)
                choice.find_results()
                assert choice.count() >= 1
                choice.setCurrentIndex(0)
            dialog.findChild(QDialogButtonBox).button(
                QDialogButtonBox.StandardButton.Ok
            ).click()
        except Exception as error:
            problems.append(error)
        finally:
            dialog.reject()

    QTimer.singleShot(50, fill)
    panel.edit_table(None)
    assert not problems
    route = editor.document()["routes"][-1]
    assert route["id"] == "COR-UI-TEST"
    assert len(route["sequence"]) == 3

    def rename():
        dialog = QApplication.activeModalWidget()
        try:
            table = dialog.findChild(QTableWidget)
            assert 0 < table.rowCount() <= 100
            table.item(0, 2).setText("京沪高速铁路规范名")
            dialog.findChild(QDialogButtonBox).button(
                QDialogButtonBox.StandardButton.Save
            ).click()
        except Exception as error:
            problems.append(error)
        finally:
            dialog.reject()

    QTimer.singleShot(50, rename)
    editor.organize_lines()
    assert not problems
    assert editor.line_library().search_lines("京沪高速铁路规范名")
    editor.timer.stop()


def test_new_cross_line_corridor_uses_stored_edges_without_creating_a_train(
    qtbot, tmp_path
):
    import json
    import sqlite3
    from desktop.rail_ui import RailEditor
    from desktop.rail_lines import line_identity
    from desktop.tests.test_operating_ui import MapStub

    edges = []
    for ident, a, b, name in [("w991:0-1", 1, 2, "甲线"), ("w992:0-1", 2, 3, "乙线")]:
        edges.append(
            {
                "id": ident,
                "from_node": a,
                "to_node": b,
                "node_ids": [a, b],
                "coordinates": [[120 + a / 100, 30], [120 + b / 100, 30]],
                "construction": False,
                "way_tags": {"name": name},
            }
        )
    with sqlite3.connect(tmp_path / "rail.sqlite") as db:
        db.executescript(
            "CREATE TABLE edges(id TEXT PRIMARY KEY,data TEXT); CREATE TABLE features(kind TEXT,data TEXT);"
        )
        for edge in edges:
            db.execute("INSERT INTO edges VALUES(?,?)", (edge["id"], json.dumps(edge)))
    map_view = MapStub()
    editor = RailEditor(map_view, tmp_path, tmp_path / "plan.json")
    qtbot.addWidget(editor)
    before = len(editor.plan.trains)
    sequence = [
        {"kind": "endpoint", "node_id": 1},
        {"kind": "line", "line_id": line_identity(edges[0])[0]},
        {"kind": "endpoint", "node_id": 2},
        {"kind": "line", "line_id": line_identity(edges[1])[0]},
        {"kind": "endpoint", "node_id": 3},
    ]
    editor.merge_corridors(
        {
            "schema": "railscope.rail-corridors.v2",
            "source": "测试",
            "extensions": {},
            "required_capabilities": [],
            "corridors": [
                {
                    "id": "COR-CROSS",
                    "name": "甲乙通道",
                    "sequence": sequence,
                    "extensions": {"example.org/meta": {"kept": True}},
                }
            ],
        },
        interactive=True,
    )
    assert len(editor.plan.trains) == before
    route = editor.document()["routes"][-1]
    assert [leg["edge_id"] for leg in route["path"]] == [edge["id"] for edge in edges]
    editor.show_corridor("COR-CROSS")
    assert any(call[0] == "fit" for call in map_view.calls)
    exported = editor.corridors_document(table=True)
    assert exported["corridors"][-1]["sequence"] == sequence
    assert exported["corridors"][-1]["extensions"]["example.org/meta"]["kept"]
    editor.save()
    restored = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
    qtbot.addWidget(restored)
    assert restored.document()["routes"][-1]["sequence"] == sequence
    assert not list(tmp_path.glob("*.osm*"))
    editor.timer.stop()
    restored.timer.stop()
