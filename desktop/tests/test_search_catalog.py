import json
import sqlite3

from desktop.catalog_metadata import rail_station_records


def test_city_search_matches_station_directory_location_not_only_station_name(tmp_path):
    feature = {
        "type": "Feature",
        "properties": {
            "osm_node_id": 101,
            "name": "虹桥站",
            "kind": "station",
            "node_tags": {"railway": "station"},
        },
        "geometry": {"type": "Point", "coordinates": [121.32, 31.20]},
    }
    with sqlite3.connect(tmp_path / "rail.sqlite") as db:
        db.execute("CREATE TABLE features(id INTEGER PRIMARY KEY,kind TEXT,data TEXT)")
        db.execute("INSERT INTO features VALUES(1,'railPoints',?)", (json.dumps(feature),))
    records, total = rail_station_records(
        tmp_path,
        [("上海", "上海市", 121.47, 31.23)],
        "上海",
    )
    assert total == 1
    assert records[0]["name"] == "虹桥站" and records[0]["city"] == "上海"


def test_station_search_accepts_optional_station_suffix(tmp_path):
    feature = {
        "type": "Feature",
        "properties": {
            "osm_node_id": 9354508778,
            "name": "济宁北",
            "kind": "station",
            "node_tags": {"railway": "station"},
        },
        "geometry": {"type": "Point", "coordinates": [116.7, 35.5]},
    }
    with sqlite3.connect(tmp_path / "rail.sqlite") as db:
        db.execute("CREATE TABLE features(id INTEGER PRIMARY KEY,kind TEXT,data TEXT)")
        db.execute("INSERT INTO features VALUES(1,'railPoints',?)", (json.dumps(feature),))
    records, total = rail_station_records(tmp_path, [], "济宁北站")
    assert total == 1 and records[0]["name"] == "济宁北"
