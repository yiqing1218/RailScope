import sqlite3
import json

from desktop.catalog_metadata import metro_station_directory, station_type
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


def test_shared_metro_station_has_two_directory_aliases_but_one_entity():
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
    assert set(entities) == {"MS-SHARED"}
    assert grouped["上海市"]["上海"]["1号线"][0]["id"] == "MS-SHARED"
    assert grouped["上海市"]["上海"]["2号线"][0]["id"] == "MS-SHARED"


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
