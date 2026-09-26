import sqlite3

from PySide6.QtCore import Qt

from metro_line_directory import MetroLineDirectoryModel, sync_line_directory


def test_metro_line_directory_pages_groups_and_preserves_partial_switch(qapp, tmp_path):
    database = tmp_path / "metro.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    routes = [
        {"osm_relation_id": 11, "display_color": "#ff0000"},
        {"osm_relation_id": 12, "display_color": "#00ff00"},
        {"osm_relation_id": 13, "display_color": "#0000ff"},
    ]
    paths = {11: ("甲省", "甲市", "仙林线"),
             12: ("甲省", "甲市", "仙林线"),
             13: ("乙省", "乙市", "三号线")}
    keys, _ = sync_line_directory(database, routes, lambda route: paths[route["osm_relation_id"]], {})
    model = MetroLineDirectoryModel(database, {route["osm_relation_id"]: route for route in routes})
    model.route_keys = keys
    model.fetchMore()
    assert model.rowCount() == 2
    assert keys[11] == keys[12] != keys[13]

    line = model.index_for_key(keys[11])
    assert line.isValid()
    assert model.relation_ids_below(keys[11]) == {11, 12}
    model.set_visible_routes({11})
    assert model.data(line, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.PartiallyChecked
    assert model.data(line.parent(), Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.PartiallyChecked

    calls = []
    model.toggled.connect(lambda key, on: calls.append((key, on)))
    assert model.setData(line, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
    assert calls == [(keys[11], True)]
    assert model.setData(line, 2, Qt.ItemDataRole.CheckStateRole)
    assert calls[-1] == (keys[11], True)

    model.set_search("三号")
    model.fetchMore()
    assert model.rowCount() == 1
    assert model.data(model.index(0, 0)).startswith("乙省")

    model.set_search("")
    model.fetchMore()
    old_province = model.index_for_key('folder:["甲省"]')
    assert old_province.isValid()
    paths[11] = ("乙省", "乙市", "三号线")
    moved_keys, affected = sync_line_directory(
        database, routes, lambda route: paths[route["osm_relation_id"]], {}
    )
    model.route_keys = moved_keys
    model.refresh_affected(affected)
    assert model.relation_ids_below(keys[11]) == {12}
    assert model.relation_ids_below(moved_keys[11]) == {11, 13}
    assert model.data(old_province) == "甲省 · 1 项"
