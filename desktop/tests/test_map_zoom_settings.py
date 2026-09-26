import json
import sqlite3

from desktop.map_zoom_settings import DEFAULT, load, save
from desktop.rail_store import viewport


def test_minimum_zoom_is_editable_and_rail_stations_ignore_urban_only_pois(tmp_path):
    settings = tmp_path / "zooms.json"
    value = {**DEFAULT, "railStations": 5, "railSwitches": 4}
    assert save(settings, value)["railStations"] == 5
    assert load(settings)["railSwitches"] == 4
    settings.write_text(json.dumps({"railStations": -3, "railSwitches": 99}), encoding="utf-8")
    assert load(settings)["railStations"] == DEFAULT["railStations"]

    with sqlite3.connect(tmp_path / "rail.sqlite") as db:
        db.executescript(
            "CREATE TABLE features(id INTEGER PRIMARY KEY,kind TEXT,service TEXT,data TEXT);"
            "CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);"
        )
        for ident, tags in ((1, {"railway": "station", "train": "yes"}),
                            (2, {"railway": "station", "station": "subway"})):
            feature = {"type": "Feature", "properties": {
                "kind": "station", "osm_node_id": ident, "node_tags": tags,
            }, "geometry": {"type": "Point", "coordinates": [121, 31]}}
            db.execute("INSERT INTO features VALUES(?,?,?,?)", (ident, "railPoints", "main", json.dumps(feature)))
            db.execute("INSERT INTO bounds VALUES(?,?,?,?,?)", (ident, 121, 121, 31, 31))
    result = viewport(tmp_path, "railPoints", [120, 30, 122, 32], 5, min_zooms=value)
    assert [feature["properties"]["osm_node_id"] for feature in result["features"]] == [1]
