import json
import sqlite3
from copy import deepcopy

import pytest

from desktop.metro_data import associate_station_areas, display_stations
from desktop.rail import train_path
from desktop.rail_lines import RailLineLibrary


def edge(ident, a, b, length=100, **tags):
    return {
        "id": ident,
        "from_node": a,
        "to_node": b,
        "node_ids": [a, b],
        "coordinates": [[121 + a * 0.001, 31], [121 + b * 0.001, 31]],
        "length_m": length,
        "construction": False,
        "way_tags": {"name": "测试线", **tags},
    }


def leg(ident):
    return {"edge_id": ident, "direction": "forward"}


def test_mainline_uses_physical_length_and_sections_split_at_role_changes():
    library = RailLineLibrary(
        [
            edge("a", 1, 2, 1000),
            edge("b", 2, 4, 1000),
            edge("c", 1, 3, 10),
            edge("d", 3, 5, 10),
            edge("e", 5, 4, 10),
        ],
        [],
    )
    ident = next(iter(library.lines))
    sequence = [
        {"kind": "endpoint", "node_id": 1},
        {"kind": "line", "line_id": ident},
        {"kind": "endpoint", "node_id": 4},
    ]
    with pytest.raises(ValueError, match="分支"):
        library.resolve(sequence)
    assert library.resolve(sequence, "mainline") == [leg("c"), leg("d"), leg("e")]
    roles = RailLineLibrary(
        [
            edge("w1:0-1", 1, 2, highspeed="no"),
            edge("w2:0-1", 2, 3, highspeed="no", passenger="no"),
        ],
        [],
    )
    # Same railway identity but a change of physical purpose must split the section.
    assert len(roles.lines) == 1
    sections = roles.sections()
    assert {s["track_type"] for s in sections} == {"普速铁路线", "货运铁路线"}
    assert all(s["id"].startswith("RS-") and s["id"] in s["name"] for s in sections)
    assert sum(len(s["path"]) for s in sections) == 2


def test_train_station_detour_does_not_modify_shared_corridor():
    edges = [
        edge("a", 1, 2),
        edge("b", 2, 3),
        edge("c", 3, 4),
        edge("platform-in", 2, 5),
        edge("platform-out", 5, 3),
    ]
    base = [leg("a"), leg("b"), leg("c")]
    original = deepcopy(base)
    section = {
        "from_node": 2,
        "to_node": 3,
        "path": [leg("platform-in"), leg("platform-out")],
        "extensions": {},
    }
    assert train_path(base, [section], edges) == [
        leg("a"),
        leg("platform-in"),
        leg("platform-out"),
        leg("c"),
    ]
    assert base == original
    section["to_node"] = 4
    with pytest.raises(ValueError, match="回到"):
        train_path(base, [section], edges)
    section["from_node"] = 999
    with pytest.raises(ValueError, match="已经切分"):
        train_path(base, [section], edges)


def test_one_metro_marker_preserves_source_members_and_relation_association():
    def station(node, x, route):
        return {
            "type": "Feature",
            "properties": {
                "osm_node_id": node,
                "name": "共享站",
                "route_relation_ids": [route],
                "node_tags": {"operator": "原始运营方"},
            },
            "geometry": {"type": "Point", "coordinates": [x, 31]},
        }

    raw = [station(1, 121, 10), station(2, 121.0001, 20), station(3, 122, 30)]
    before = deepcopy(raw)
    markers = display_stations(raw)
    assert len(markers) == 2
    assert markers[0]["properties"]["route_relation_ids"] == [10, 20]
    assert markers[0]["properties"]["source_members"] == raw[:2]
    assert raw == before
    area = {
        "properties": {"member_station_ids": [1]},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[121.01, 31], [121.011, 31], [121.011, 31.001], [121.01, 31]]
            ],
        },
    }
    result = associate_station_areas([area], raw)[0]
    assert result["properties"]["route_relation_ids"] == [10]
    assert result["properties"]["association_source"] == "OSM 关系成员关联"


def test_national_polygons_shared_identity_and_no_track_mutation(tmp_path):
    pytest.importorskip("osmium")
    from desktop.rail_boundaries import extract

    source = tmp_path / "station.osm"
    source.write_text(
        """<osm version="0.6">
      <node id="1" lon="121" lat="31"/><node id="2" lon="121.001" lat="31"/>
      <node id="3" lon="121.001" lat="31.001"/><node id="4" lon="121" lat="31.001"/>
      <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/>
      <tag k="railway" v="platform"/><tag k="train" v="yes"/><tag k="ref" v="2"/><tag k="name" v="原始站台名"/></way>
      <way id="20"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/>
      <tag k="building" v="train_station"/></way></osm>""",
        encoding="utf-8",
    )
    station = {
        "type": "Feature",
        "properties": {"osm_node_id": 100, "kind": "station", "name": "铁路站"},
        "geometry": {"type": "Point", "coordinates": [121.0005, 31.0005]},
    }
    with sqlite3.connect(tmp_path / "rail.sqlite") as db:
        db.executescript(
            "CREATE TABLE features(id INTEGER PRIMARY KEY,kind TEXT,service TEXT,data TEXT);"
            "CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);"
            "CREATE TABLE edges(id TEXT,data TEXT);"
        )
        db.execute(
            "INSERT INTO features VALUES(1,'railPoints','main',?)",
            (json.dumps(station),),
        )
        db.execute(
            "INSERT INTO edges VALUES('sentinel',?)",
            (json.dumps(edge("sentinel", 1, 2, highspeed="yes")),),
        )
    report = extract(source, tmp_path, progress=lambda _: None)
    assert report["polygons"] == 2 and report["platform_polygons"] == 1
    with sqlite3.connect(tmp_path / "rail.sqlite") as db:
        polygons = [
            json.loads(raw)
            for (raw,) in db.execute(
                "SELECT data FROM features WHERE kind!='railPoints'"
            )
        ]
        assert db.execute("SELECT id FROM edges").fetchall() == [("sentinel",)]
        assert db.execute("SELECT count(*) FROM bounds").fetchone()[0] == 2
    assert {p["properties"]["station_id"] for p in polygons} == {"node/100"}
    assert {p["properties"]["infrastructure_id"] for p in polygons} == {
        "way/10",
        "way/20",
    }
    assert (
        next(p for p in polygons if p["properties"]["boundary_kind"] == "platform")[
            "properties"
        ]["source_name"]
        == "原始站台名"
    )
    assert len({p["properties"]["name"] for p in polygons}) == 2
    assert extract(source, tmp_path, progress=lambda _: None)["polygons"] == 2
    assert (
        json.loads(
            (tmp_path / "rail_boundary_coverage.json").read_text(encoding="utf-8")
        )[0]["station_id"]
        == "node/100"
    )
    from desktop.rail_inventory import export_inventory

    inventory_report = export_inventory(tmp_path, progress=lambda _: None)
    assert inventory_report["physical_edges"] == 1
    inventory = json.loads(
        (tmp_path / "rail_section_inventory.json").read_text(encoding="utf-8")
    )
    assert inventory["sections"][0]["track_type"] == "高速铁路线"
    assert inventory["sections"][0]["from_node"] == 1


def test_new_corridor_fills_blank_identity_without_cached_train_path(
    tmp_path, monkeypatch
):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QApplication,
        QTableWidget,
        QLineEdit,
        QDialogButtonBox,
        QMessageBox,
    )
    from desktop.corridor_ui import CorridorPanel
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub

    app = QApplication.instance() or QApplication([])
    editor = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
    panel = CorridorPanel(editor)
    existing = editor.corridors_document(table=True)["corridors"][0]["sequence"]

    def fail_warning(*args):
        raise AssertionError(str(args))

    monkeypatch.setattr(QMessageBox, "warning", fail_warning)

    def fill():
        dialog = app.activeModalWidget()
        table = dialog.findChild(QTableWidget)
        dialog.findChild(QLineEdit, "corridorId").clear()
        dialog.findChild(QLineEdit, "corridorName").clear()
        table.cellWidget(0, 0).setEditText(str(existing[0]["node_id"]))
        table.cellWidget(0, 1).setEditText(existing[1]["line_id"])
        table.cellWidget(0, 2).setEditText(str(existing[2]["node_id"]))
        dialog.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Ok
        ).click()

    QTimer.singleShot(20, fill)
    panel.edit_table(None)
    new = editor.document()["routes"][-1]
    assert new["id"].startswith("COR-") and new["name"]
    assert new["sequence"] == existing
    assert new["extensions"]["railscope.org/line-resolution"]["policy"] == "mainline"
    editor.timer.stop()
    panel.close()
    editor.close()


def test_csv_refuses_to_drop_platform_relations():
    from desktop.rail_tables import export_csv

    document = {
        "schema": "railscope.rail-plan.v2",
        "service_date": "2026-09-17",
        "timezone": "Asia/Shanghai",
        "source": "测试",
        "extensions": {},
        "required_capabilities": [],
        "routes": [{"id": "COR-X", "path": [leg("x")], "extensions": {}}],
        "trains": [
            {
                "id": "G2",
                "route_id": "COR-X",
                "extensions": {},
                "stops": [
                    {
                        "node_id": 1,
                        "arrival_s": 1,
                        "departure_s": 2,
                        "platform_ref": "relation/10",
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="JSON"):
        export_csv(document)
