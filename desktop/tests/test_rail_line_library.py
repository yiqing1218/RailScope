import pytest

from desktop.tests.test_workspace_revision import reference, install_reference_database


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


def test_table_corridor_roundtrip_renaming_and_shared_sections(tmp_path):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub

    app = QApplication.instance() or QApplication([])
    assert app
    install_reference_database(tmp_path)
    editor = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
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
    assert library.nodes[1] == "测试线路所"
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
    assert library.search_nodes("测试", ident) == [(1, "测试线路所")]
    editor.save_line_names({ident: "试验干线"})
    assert library.search_lines("试验干线")[0]["id"] == ident
    export_path = tmp_path / "中文 线路.json"
    library.write_export(export_path)
    exported = json.loads(export_path.read_text(encoding="utf-8"))
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
            for col, query in [(1, "京沪"), (0, "北京南"), (2, "上海虹桥")]:
                choice = table.cellWidget(0, col)
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
