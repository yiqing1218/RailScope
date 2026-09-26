import json
import sqlite3
from PySide6.QtWidgets import QApplication

from desktop.lazy_directory import SqliteDirectoryModel
from desktop.metro_store import (
    StationLookup, ensure_index, station_directory_paths,
    sync_station_directory, update_station_directory, viewport,
)


def _collection(path, features):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")


def test_metro_viewport_and_directory_use_paged_disk_rows(tmp_path):
    app = QApplication.instance() or QApplication([])
    route = {"type": "Feature", "properties": {"route_relation_id": 10},
             "geometry": {"type": "LineString", "coordinates": [[121, 31], [121.01, 31.01]]}}
    station = {"type": "Feature", "properties": {
        "osm_node_id": 1, "name": "甲站", "route_relation_ids": [10],
        "node_tags": {"railway": "station"},
    }, "geometry": {"type": "Point", "coordinates": [121.005, 31.005]}}
    area = {"type": "Feature", "properties": {"osm_way_id": 30, "way_tags": {"name": "甲站"}},
            "geometry": {"type": "Polygon", "coordinates": [[
                [121.004, 31.004], [121.006, 31.004], [121.006, 31.006],
                [121.004, 31.006], [121.004, 31.004],
            ]]}}
    _collection(tmp_path / "china_metro_routes.geojson", [route])
    _collection(tmp_path / "china_metro_stations.geojson", [station])
    platform = {**area, "properties": {"osm_way_id": 31, "boundary_kind": "platform",
                "way_tags": {"name": "甲站", "railway": "platform"}}}
    _collection(tmp_path / "china_metro_station_areas.geojson", [area, platform])
    _collection(tmp_path / "china_metro_construction.geojson", [])
    path = ensure_index(tmp_path)
    assert ensure_index(tmp_path) == path
    assert len(viewport(path, "metro", [120, 30, 122, 32], [10])["features"]) == 1
    assert viewport(path, "metro", [110, 20, 111, 21], [10])["features"] == []
    lookup = StationLookup(path, {})
    alias = next(iter(lookup))
    assert len(viewport(path, "stations", [120, 30, 122, 32], [alias])["features"]) == 1
    assert len(viewport(path, "areas", [120, 30, 122, 32], [alias])["features"]) == 2
    for kind in ("stations", "areas"):
        assert viewport(path, kind, [120, 30, 122, 32], ["unselected-station"])["features"] == []

    route_paths = {"10": ["上海市", "上海市", "一号线"]}
    sync_station_directory(path, route_paths, {})
    model = SqliteDirectoryModel(path)
    assert model.rowCount() == 0
    model.fetchMore()
    assert model.rowCount() == 1
    province = model.index(0, 0)
    model.fetchMore(province)
    city = model.index(0, 0, province)
    model.fetchMore(city)
    line = model.index(0, 0, city)
    model.fetchMore(line)
    leaf = model.index(0, 0, line)
    assert model.data(leaf) == "甲站"
    assert model.ids_below(model._node(province).key) == {alias}

    overrides = {alias: {"display_name": "新名字", "folder_path": ["江苏省", "南京市", "新线"]}}
    affected = update_station_directory(path, {alias}, route_paths, overrides)
    model.refresh_affected(affected)
    assert ["江苏省", "南京市", "新线"] in station_directory_paths(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM directory_nodes WHERE object_id=?", (alias,)).fetchone()[0] == "新名字"
