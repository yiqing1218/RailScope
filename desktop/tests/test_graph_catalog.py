import sqlite3
import json
import pytest

from desktop.catalog_metadata import (
    metro_station_directory,
    rail_switch_owner,
    station_type,
)
from desktop.rail_line_store import DiskRailLineLibrary
from desktop.metro_data import iter_geojson_features


class HierarchyStub:
    def parent(self, route):
        return "上海市", "上海", route["name"]


def test_national_geojson_can_be_scanned_without_loading_the_whole_file(tmp_path):
    path = tmp_path / "routes.geojson"
    features = [
        {"type": "Feature", "properties": {"id": value}, "geometry": None}
        for value in range(3)
    ]
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )
    assert list(iter_geojson_features(path, chunk_size=17)) == features


def test_interchange_station_has_line_platform_objects_linked_to_one_physical_station():
    routes = [
        {"osm_relation_id": 1, "name": "1号线"},
        {"osm_relation_id": 2, "name": "2号线"},
    ]
    station = {
        "properties": {
            "infrastructure_id": "MS-SHARED",
            "name": "人民广场",
            "route_relation_ids": [1, 2],
        },
        "geometry": {"type": "Point", "coordinates": [121.47, 31.23]},
    }
    grouped, entities = metro_station_directory(
        [station], routes, HierarchyStub()
    )
    assert set(entities) == {"MS-SHARED@line-1", "MS-SHARED@line-2"}
    assert grouped["上海市"]["上海"]["1号线"][0]["id"] == "MS-SHARED@line-1"
    assert grouped["上海市"]["上海"]["2号线"][0]["id"] == "MS-SHARED@line-2"
    assert {value["physical_station_id"] for value in entities.values()} == {"MS-SHARED"}


def test_true_shared_operation_platform_is_referenced_by_multiple_line_directories():
    routes = [
        {"osm_relation_id": 3, "name": "3号线"},
        {"osm_relation_id": 4, "name": "4号线"},
    ]
    station = {
        "properties": {
            "infrastructure_id": "MS-ZHONGTAN",
            "name": "中潭路",
            "route_relation_ids": [3, 4],
            "shared_platform_relation_groups": [[3, 4]],
        },
        "geometry": {"type": "Point", "coordinates": [121.44, 31.25]},
    }
    grouped, entities = metro_station_directory([station], routes, HierarchyStub())
    assert set(entities) == {"MS-ZHONGTAN@shared-1"}
    assert grouped["上海市"]["上海"]["3号线"][0] is grouped["上海市"]["上海"]["4号线"][0]


def test_duplicate_source_station_is_shown_once_per_line():
    route = {"osm_relation_id": 1, "name": "1号线"}
    station = {
        "properties": {"infrastructure_id": "MS-ONE", "name": "测试站", "route_relation_ids": [1]},
        "geometry": {"type": "Point", "coordinates": [121.47, 31.23]},
    }
    grouped, entities = metro_station_directory([station, station], [route], HierarchyStub())
    assert len(entities) == 1
    assert len(grouped["上海市"]["上海"]["1号线"]) == 1


def test_manual_signal_box_is_the_stable_owner_of_all_member_switches(tmp_path):
    overrides = {
        "station:signalbox/RSB-TEST": {
            "display_name": "测试线路所",
            "coordinates": [117.28, 31.80],
            "folder_path": ["安徽省", "合肥市"],
            "member_switch_ids": [101, 102],
            "connected_lines": [{"line_id": "RL-A", "anchor_node": 1}],
            "line_names": ["甲线"],
        }
    }
    owner = rail_switch_owner(tmp_path, 102, [], overrides)
    assert owner["id"] == "signalbox/RSB-TEST"
    assert owner["name"] == "测试线路所"
    assert owner["member_switch_ids"] == [101, 102]
    assert owner["province"] == "安徽省" and owner["city"] == "合肥市"


def test_station_type_never_invents_an_unstated_role():
    assert station_type({"passenger": "yes", "freight": "yes"}, "station") == "客货运站"
    assert station_type({"name": "测试编组站"}, "station") == "编组站"
    assert station_type({"railway": "station"}, "station") == "未定义"
    assert station_type({"railway": "station", "usage": "main"}, "station") == "未定义"
    assert station_type({}, "signal_box") == "线路所"


def test_corridor_choices_follow_station_line_station_graph(tmp_path):
    path = tmp_path / "rail_lines.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE lines(id TEXT PRIMARY KEY,source_name TEXT,edge_count INTEGER,track_type TEXT,evidence TEXT);
            CREATE TABLE edges(id TEXT PRIMARY KEY,line_id TEXT,a,b,construction INTEGER,length_m REAL,track_type TEXT,evidence TEXT,source_way TEXT,direction TEXT);
            CREATE TABLE nodes(id PRIMARY KEY,label TEXT,kind TEXT,x REAL,y REAL);
            CREATE TABLE node_aliases(source_id PRIMARY KEY,node_id NOT NULL);
            CREATE TABLE line_nodes(line_id TEXT,node_id,PRIMARY KEY(line_id,node_id));
            CREATE TABLE station_aliases(source_id TEXT,alias TEXT,station_node_id,anchor_node,distance_m REAL,verification_status TEXT,confidence REAL,PRIMARY KEY(source_id,alias,anchor_node));
            """
        )
        db.executemany(
            "INSERT INTO lines VALUES(?,?,?,?,?)",
            [("RL-A", "甲线", 1, "普速铁路线", "test"), ("RL-B", "乙线", 0, "普速铁路线", "test")],
        )
        db.execute(
            "INSERT INTO edges VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("NE-1", "RL-A", 1, 2, 0, 10, "普速铁路线", "test", "1", "both"),
        )
        db.executemany(
            "INSERT INTO nodes VALUES(?,?,?,?,?)",
            [(1, "甲站", "station", 120, 30), (2, "乙站", "station", 121, 30)],
        )
        db.executemany("INSERT INTO node_aliases VALUES(?,?)", [(1, 1), (2, 2)])
        db.executemany(
            "INSERT INTO line_nodes VALUES(?,?)",
            [("RL-A", 1), ("RL-A", 2), ("RL-B", 1)],
        )
        db.executemany(
            "INSERT INTO station_aliases VALUES(?,?,?,?,?,?,?)",
            [
                ("node/1", "甲站", 1, 1, 0, "source", 1),
                ("node/2", "乙站", 2, 2, 0, "source", 1),
            ],
        )
    library = DiskRailLineLibrary(path)
    start = library.search_endpoints("甲站")[0][0]
    assert {line["id"] for line in library.connected_lines(start)} == {"RL-A", "RL-B"}
    assert library.reachable_nodes(start, "RL-A", "乙站")[0][0] == "station:node/2"
    sequence = [
        {"kind": "endpoint", "node_id": start},
        {"kind": "line", "line_id": "RL-A"},
        {"kind": "endpoint", "node_id": "station:node/2"},
    ]
    assert library.resolve(sequence) == [{"edge_id": "NE-1", "direction": "forward"}]
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM line_nodes WHERE line_id='RL-B'")
        db.executemany("INSERT INTO line_nodes VALUES(?,?)", [("RL-B", 2), ("RL-B", 3)])
        db.execute("INSERT INTO nodes VALUES(?,?,?,?,?)", (3, "丙站", "station", 122, 30))
        db.execute("INSERT INTO edges VALUES(?,?,?,?,?,?,?,?,?,?)", ("NE-2", "RL-B", 2, 3, 0, 10, "普速铁路线", "test", "2", "both"))
    assert library.common_transfer_endpoint(start, "RL-A", "RL-B") == "station:node/2"
    assert "graph" not in library.lines["RL-A"]
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO nodes VALUES(?,?,?,?,?)", (4, "丁站", "station", 123, 30))
        db.execute("INSERT INTO edges VALUES(?,?,?,?,?,?,?,?,?,?)", ("NE-3", "RL-A", 2, 4, 0, 10, "普速铁路线", "test", "3", "both"))
        db.executemany("INSERT INTO line_nodes VALUES(?,?)", [("RL-A", 4), ("RL-B", 4)])
    assert library.common_transfer_endpoint(start, "RL-A", "RL-B") is None


def test_corridor_station_picker_merges_duplicate_station_objects_and_hides_switches(tmp_path):
    path = tmp_path / "rail_lines.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE lines(id TEXT PRIMARY KEY,source_name TEXT,edge_count INTEGER,track_type TEXT,evidence TEXT);
            CREATE TABLE edges(id TEXT PRIMARY KEY,line_id TEXT,a,b,construction INTEGER,length_m REAL,track_type TEXT,evidence TEXT,source_way TEXT,direction TEXT);
            CREATE TABLE nodes(id PRIMARY KEY,label TEXT,kind TEXT,x REAL,y REAL);
            CREATE TABLE node_aliases(source_id PRIMARY KEY,node_id NOT NULL);
            CREATE TABLE line_nodes(line_id TEXT,node_id,PRIMARY KEY(line_id,node_id));
            CREATE TABLE station_aliases(source_id TEXT,alias TEXT,station_node_id,anchor_node,distance_m REAL,verification_status TEXT,confidence REAL,source_x REAL,source_y REAL,PRIMARY KEY(source_id,alias,anchor_node));
            """
        )
        db.executemany(
            "INSERT INTO lines VALUES(?,?,?,?,?)",
            [
                ("RL-A", "合福高速铁路", 1, "高速铁路线", "test"),
                ("RL-B", "合蚌客运专线", 1, "高速铁路线", "test"),
                ("RL-Y", "未命名轨道·w9", 1, "渡线 / 道岔连接轨", "test"),
            ],
        )
        db.executemany(
            "INSERT INTO edges VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                ("NE-A", "RL-A", 1, 2, 0, 10, "高速铁路线", "test", "1", "both"),
                ("NE-B", "RL-B", 3, 4, 0, 10, "高速铁路线", "test", "2", "both"),
                ("NE-Y", "RL-Y", 5, 6, 0, 1, "渡线 / 道岔连接轨", "test", "9", "both"),
            ],
        )
        db.executemany(
            "INSERT INTO nodes VALUES(?,?,?,?,?)",
            [
                (1, None, None, 117.28, 31.80), (2, "甲", "station", 117.4, 31.8),
                (3, None, None, 117.281, 31.801), (4, "乙", "station", 117.5, 31.8),
                (5, "道岔 5", "switch", 117.282, 31.802), (6, None, None, 117.283, 31.803),
            ],
        )
        db.executemany("INSERT INTO node_aliases VALUES(?,?)", [(i, i) for i in range(1, 7)])
        db.executemany(
            "INSERT INTO line_nodes VALUES(?,?)",
            [("RL-A", 1), ("RL-A", 2), ("RL-B", 3), ("RL-B", 4), ("RL-Y", 5), ("RL-Y", 6)],
        )
        db.executemany(
            "INSERT INTO station_aliases VALUES(?,?,?,?,?,?,?,?,?)",
            [
                ("node/100", "合肥南站", 100, 1, 10, "nearby", 0.9, 117.2801, 31.8001),
                ("way/200", "合肥南站", None, 3, 15, "nearby", 0.8, 117.2802, 31.8002),
            ],
        )
    library = DiskRailLineLibrary(path)
    stations = library.search_endpoints("合肥南")
    assert len(stations) == 1
    assert "合福高速铁路" in stations[0][1] and "合蚌客运专线" in stations[0][1]
    assert set(library.endpoint_nodes(stations[0][0])) == {1, 3}
    assert library.endpoint_choice_label(1).startswith("合肥南站 · 接轨：")
    assert {line["id"] for line in library.connected_lines(stations[0][0])} == {"RL-A", "RL-B"}
    assert not library.search_endpoints("道岔")


def test_manual_station_connections_replace_detected_lines_and_bind_real_nearby_nodes(
    tmp_path,
):
    path = tmp_path / "rail_lines.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE lines(id TEXT PRIMARY KEY,source_name TEXT,edge_count INTEGER,track_type TEXT,evidence TEXT);
            CREATE TABLE edges(id TEXT PRIMARY KEY,line_id TEXT,a,b,construction INTEGER,length_m REAL,track_type TEXT,evidence TEXT,source_way TEXT,direction TEXT);
            CREATE TABLE nodes(id PRIMARY KEY,label TEXT,kind TEXT,x REAL,y REAL);
            CREATE TABLE node_aliases(source_id PRIMARY KEY,node_id NOT NULL);
            CREATE TABLE line_nodes(line_id TEXT,node_id,PRIMARY KEY(line_id,node_id));
            CREATE TABLE station_aliases(source_id TEXT,alias TEXT,station_node_id,anchor_node,distance_m REAL,verification_status TEXT,confidence REAL,source_x REAL,source_y REAL,PRIMARY KEY(source_id,alias,anchor_node));
            """
        )
        db.executemany(
            "INSERT INTO lines VALUES(?,?,?,?,?)",
            [
                ("RL-A", "甲线", 1, "普速铁路线", "test"),
                ("RL-B", "乙线", 1, "普速铁路线", "test"),
                ("RL-FAR", "远方线", 1, "普速铁路线", "test"),
            ],
        )
        db.executemany(
            "INSERT INTO edges VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                ("NE-A", "RL-A", 1, 2, 0, 10, "普速铁路线", "test", "1", "both"),
                ("NE-B", "RL-B", 3, 4, 0, 10, "普速铁路线", "test", "2", "both"),
                ("NE-F", "RL-FAR", 5, 6, 0, 10, "普速铁路线", "test", "3", "both"),
            ],
        )
        db.executemany(
            "INSERT INTO nodes VALUES(?,?,?,?,?)",
            [
                (1, None, None, 117.2800, 31.8000),
                (2, "甲线终点", "station", 117.3000, 31.8000),
                (3, None, None, 117.2805, 31.8004),
                (4, "乙线终点", "station", 117.3100, 31.8000),
                (5, None, None, 118.0000, 32.0000),
                (6, "远方终点", "station", 118.1000, 32.0000),
            ],
        )
        db.executemany(
            "INSERT INTO node_aliases VALUES(?,?)", [(value, value) for value in range(1, 7)]
        )
        db.executemany(
            "INSERT INTO line_nodes VALUES(?,?)",
            [
                ("RL-A", 1), ("RL-A", 2), ("RL-B", 3),
                ("RL-B", 4), ("RL-FAR", 5), ("RL-FAR", 6),
            ],
        )
        db.executemany(
            "INSERT INTO station_aliases VALUES(?,?,?,?,?,?,?,?,?)",
            [
                ("node/100", "测试站", 100, 1, 0, "source", 1, 117.2800, 31.8000),
                ("node/4", "乙线终点", 4, 4, 0, "source", 1, 117.3100, 31.8000),
            ],
        )
    base = DiskRailLineLibrary(path)
    endpoint = base.search_endpoints("测试站")[0][0]
    assert [line["id"] for line in base.connected_lines(endpoint)] == ["RL-A"]

    connections = base.station_connection_override(endpoint, ["RL-B"])
    assert connections == [
        {
            "line_id": "RL-B",
            "anchor_node": 3,
            "distance_m": pytest.approx(connections[0]["distance_m"]),
            "source": "manual",
            "verification_status": "user_verified",
            "anchor_policy": "auto_reachable",
            "anchor_verification_status": "automatic_nearest_hint",
        }
    ]
    assert connections[0]["distance_m"] < 100
    metadata = {"station:node/100": {"connected_lines": connections}}
    overridden = DiskRailLineLibrary(path, metadata=metadata)
    endpoint = overridden.search_endpoints("测试站")[0][0]
    assert [line["id"] for line in overridden.connected_lines(endpoint)] == ["RL-B"]
    assert "乙线" in overridden.endpoint_choice_label(endpoint)
    assert "甲线" not in overridden.endpoint_choice_label(endpoint)
    assert 3 in overridden.endpoint_nodes(endpoint)
    assert overridden.resolve_endpoint(endpoint, ["RL-B"]) == 3
    assert endpoint in {
        value for value, _label in overridden.search_endpoints("测试站", line_id="RL-B")
    }
    assert overridden.reachable_nodes(endpoint, "RL-B", "乙线终点")[0][0] == "station:node/4"
    assert overridden.reachable_nodes("station:node/4", "RL-B", "测试站")[0][0] == endpoint

    with pytest.raises(ValueError, match="没有找到.*真实轨道节点"):
        base.station_connection_override(endpoint, ["RL-FAR"])
