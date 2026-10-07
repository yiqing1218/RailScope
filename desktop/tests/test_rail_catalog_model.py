import json
import sqlite3
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeView

from desktop.rail_catalog_index import RailCatalogIndex, build_index
from desktop.rail_catalog_model import RailDirectoryModel, sync_catalog_directory
from desktop.rail_station_catalog_model import StationCatalogModel, sync_station_catalog
from desktop.rail_catalog_ui import RailCatalog
from desktop.tests.test_operating_ui import MapStub


def _record(number, **extra):
    return {"name": f"测试铁路{number:05}", "way_ids": [number],
            "railway_class": "conventional", "line_role": "main_line",
            "track_role": "main_track", **extra}


def _index(tmp_path, records, overrides=None):
    catalog = RailCatalogIndex(build_index(tmp_path, records))
    changes = overrides or {}

    def resolve(key, raw):
        value = {**raw, **changes.get(key, {})}
        return value, value.get("folder_path", ("普速铁路",)), value.get("display_name", value["name"])

    sync_catalog_directory(catalog, changes, resolve)
    return catalog, resolve


def _loaded(node):
    return len(node.children) + sum(_loaded(child) for child in node.children)


def test_previous_topology_catalog_keeps_stable_line_ids_until_reclassified(tmp_path):
    (tmp_path / "rail_catalog.json").write_text(
        json.dumps({"旧名称目录": _record(1)}, ensure_ascii=False), encoding="utf-8")
    keys = {"RL-one": _record(1), "RL-two": _record(2),
            "ST-yard": {**_record(3), "station_name": "测试站"}}
    (tmp_path / "rail_catalog.topology.json").write_text(
        json.dumps({"version": "topology-line-endpoint-catalog-v9", "catalog": keys}, ensure_ascii=False),
        encoding="utf-8")
    with sqlite3.connect(tmp_path / "rail.sqlite") as db:
        db.executescript("CREATE TABLE features(id INTEGER PRIMARY KEY,data TEXT);"
                         "CREATE TABLE rail_feature_groups(feature_id INTEGER,group_id TEXT);")
        db.execute("INSERT INTO features VALUES(?,?)", (1, json.dumps({"properties": {
            "line_name": "待补入线路", "way_tags": {"railway": "rail", "usage": "main"}}})))
        db.execute("INSERT INTO rail_feature_groups VALUES(?,?)", (1, "RL-rendered"))
    catalog = RailCatalogIndex(build_index(tmp_path))
    assert set(catalog) == set(keys) | {"RL-rendered"}
    assert "旧名称目录" not in catalog
    assert catalog["RL-rendered"]["source"] == "rendered_group_fallback"


def test_locked_catalog_index_updates_rows_without_losing_old_on_failure(tmp_path, monkeypatch):
    (tmp_path / "rail_catalog.json").write_text(json.dumps({"old": _record(1)}), encoding="utf-8")
    target = build_index(tmp_path)
    with sqlite3.connect(target) as db:
        db.execute("INSERT INTO metadata VALUES('paged_directory_signature','stale')")
    (tmp_path / "rail_catalog.topology.json").write_text(
        json.dumps({"version": "topology-line-endpoint-catalog-v9",
                    "catalog": {"RL-new": _record(2)}}), encoding="utf-8")
    original_replace = Path.replace

    def locked(self, destination):
        if str(self).endswith(".sqlite.tmp"):
            raise PermissionError("open SQLite file")
        return original_replace(self, destination)

    monkeypatch.setattr(Path, "replace", locked)
    build_index(tmp_path)
    with sqlite3.connect(target) as db:
        assert [row[0] for row in db.execute("SELECT id FROM catalog")] == ["RL-new"]
        assert db.execute("SELECT value FROM metadata WHERE key='paged_directory_signature'").fetchone() is None


def test_pages_are_sqlite_backed_and_reveal_navigates_to_real_item(qtbot, tmp_path):
    catalog, _ = _index(tmp_path, {f"RL-{i}": _record(i) for i in range(3000)})
    assert catalog.groups_for_way(2999) == ["RL-2999"]
    model = RailDirectoryModel(catalog.path)
    assert model.rowCount() == 0
    model.fetchMore()
    folder = model.index(0, 0)
    assert model.rowCount(folder) == 0
    model.fetchMore(folder)
    assert model.rowCount(folder) == 128
    assert model.canFetchMore(folder)
    model.fetchMore(folder)
    assert model.rowCount(folder) == 256
    index = model.reveal_catalog_id("RL-2999")
    assert index.isValid()
    node = model._node(index)
    assert not node.label.startswith("地图选中")
    assert model.ids_below(node.key) == {"RL-2999"}
    assert not any(getattr(child, "label", "").startswith("地图选中")
                   for child in model.root.children)
    model.set_search("")
    model.fetchMore()
    folder = model.index(0, 0)
    model.fetchMore(folder)
    model.release_branch(folder)
    assert model.rowCount(folder) == 0
    assert model.canFetchMore(folder)


def test_unloaded_folder_members_and_assembly_checkbox_states(qtbot, tmp_path):
    records = {f"RL-{i}": _record(i) for i in range(350)}
    changes = {"RL-0": {"assembly_id": "RLU-A", "assembly_name": "组合"},
               "RL-349": {"assembly_id": "RLU-A", "assembly_name": "组合"}}
    catalog, _ = _index(tmp_path, records, changes)
    model = RailDirectoryModel(catalog.path)
    model.fetchMore()
    folder = model.index(0, 0)
    assert len(model.ids_below(model._node(folder).key)) == 350
    model.set_visibility({"RL-349"})
    assert model.data(folder, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.PartiallyChecked
    model.set_visibility(set(), True, {"RL-349"})
    assert model.data(folder, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.PartiallyChecked
    model.set_visibility(set(), True)
    assert model.data(folder, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
    calls = []
    model.toggled.connect(lambda key, on: calls.append((model.ids_below(key), on)))
    model.setData(folder, Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)
    assert calls == [(set(records), True)]
    assembly = model.reveal_catalog_id("RL-0")
    assert model.ids_below(model._node(assembly).key) == {"RL-0", "RL-349"}


def test_facility_partition_uses_roles_and_stable_facility_ids(qtbot, tmp_path):
    records = {
        "main": _record(1, facility_id="ST-A", station_name="同名站"),
        "yard-a": _record(2, facility_id="ST-A", station_name="同名站", track_role="shunting_track"),
        "yard-b": _record(3, facility_id="ST-B", station_name="同名站", track_role="shunting_track"),
        "unknown": _record(4, track_role="unknown", facility_only=True),
        "yard-linked": _record(5, station_id="ST-C", station_name="人工关联站", facility_only=True),
        "station-main": _record(6, station_id="ST-D", station_name="站内正线"),
        "line-to-yard": _record(7),
    }
    catalog, _ = _index(tmp_path, records)
    lines = RailDirectoryModel(catalog.path)
    facilities = RailDirectoryModel(catalog.path, "facilities")
    assert lines.reveal_catalog_id("main").isValid()
    assert not facilities.reveal_catalog_id("main").isValid()
    assert not lines.reveal_catalog_id("yard-a").isValid()
    assert not lines.reveal_catalog_id("unknown").isValid()
    assert lines.reveal_catalog_id("station-main").isValid()
    assert not facilities.reveal_catalog_id("station-main").isValid()
    facilities.fetchMore()
    with sqlite3.connect(catalog.path) as db:
        paths = {key: json.loads(path) for key, path in db.execute(
            "SELECT m.catalog_id,d.path FROM rail_directory_members m JOIN rail_directory_nodes d ON d.id=m.node_id WHERE view='facilities'")}
    assert paths["yard-a"][1] != paths["yard-b"][1]
    assert paths["unknown"][1] == "设施归属待核实"
    assert "yard-linked" not in paths  # explicitly classified main tracks stay with lines
    changes = {"yard-a": {"directory_view": "lines", "folder_path": ["普速铁路", "人工归线"]},
               "line-to-yard": {"directory_view": "facilities", "station_id": "ST-A"}}
    def moved(key, raw):
        record = {**raw, **changes.get(key, {})}
        return record, record.get("folder_path", ["普速铁路"]), record.get("name", key)
    sync_catalog_directory(catalog, changes, moved)
    lines.reset_from_disk()
    facilities.reset_from_disk()
    assert not lines.reveal_catalog_id("yard-a").isValid()
    assert facilities.reveal_catalog_id("yard-a").isValid()
    assert not facilities.reveal_catalog_id("line-to-yard").isValid()
    assert lines.reveal_catalog_id("line-to-yard").isValid()


def test_station_facilities_share_paged_tree_without_guessing_ambiguous_owner(qtbot, tmp_path, monkeypatch):
    import desktop.rail_station_catalog_model as station_module
    from types import SimpleNamespace
    from desktop.rail_catalog_model import update_directory_labels

    stations = [
        {"id": "node/1", "name": "甲站", "province": "甲省", "city": "甲市"},
        {"id": "node/2", "name": "同名站", "province": "乙省", "city": "乙市"},
        {"id": "node/3", "name": "同名站", "province": "乙省", "city": "乙市"},
    ]
    monkeypatch.setattr(station_module, "rail_station_records", lambda *args, **kwargs: (stations, 3))
    with sqlite3.connect(tmp_path / "rail_lines.sqlite") as rail_db:
        rail_db.executescript("CREATE TABLE features(id INTEGER PRIMARY KEY,data TEXT);"
                              "CREATE TABLE rail_feature_groups(feature_id INTEGER,group_id TEXT);")
        for number in (1, 2):
            feature = {"properties": {"catalog_group_id": "explicit",
                        "network_edge_id": f"RS-{number}", "line_name": f"甲站{number}道",
                        "from_name": "A", "to_name": "B"}}
            rail_db.execute("INSERT INTO features VALUES(?,?)", (number, json.dumps(feature, ensure_ascii=False)))
            rail_db.execute("INSERT INTO rail_feature_groups VALUES(?,?)", (number, "explicit"))
    records = {
        "explicit": _record(1, station_name="任意名称", station_id="node/1", track_role="shunting_track"),
        "inferred": _record(2, station_name="甲", provinces=["甲省"], track_role="shunting_track"),
        "ambiguous": _record(3, station_name="同名", provinces=["乙省"], track_role="shunting_track"),
        "unknown": _record(4, track_role="shunting_track"),
    }
    catalog, resolve = _index(tmp_path, records)
    assert sync_station_catalog(tmp_path, catalog.path, [], {}) is True
    model = StationCatalogModel(catalog.path)
    assert model.ids_below("station:node/1") == ({"node/1"}, {"explicit", "inferred"})
    pending = 'folder:["stations","待核对"]'
    assert model.ids_below(pending) == (set(), {"ambiguous", "unknown"})
    with sqlite3.connect(catalog.path) as db:
        assert db.execute("SELECT facility_total FROM rail_station_nodes WHERE id='station:node/1'").fetchone()[0] == 2
        assert db.execute("SELECT p.label FROM rail_station_nodes f JOIN rail_station_nodes p ON p.id=f.parent_id WHERE f.id='facility:explicit'").fetchone()[0] == "站内轨道"
        assert db.execute("SELECT p.label FROM rail_station_nodes f JOIN rail_station_nodes p ON p.id=f.parent_id WHERE f.id='facility:inferred'").fetchone()[0] == "名称匹配，待核对"
    assert sync_station_catalog(tmp_path, catalog.path, [], {}) is False
    with sqlite3.connect(catalog.path) as db:
        segments = db.execute("SELECT label,object_id FROM rail_station_nodes "
                              "WHERE parent_id='facility:explicit' ORDER BY object_id").fetchall()
    assert segments == [("甲站1道 · A→B", "object:network_edge_id:RS-1"),
                        ("甲站2道 · A→B", "object:network_edge_id:RS-2")]
    assert sync_station_catalog(tmp_path, catalog.path, [], {
        "object:network_edge_id:RS-1": {"line_name": "甲站改名", "track_type": "渡线 / 道岔连接轨"}})
    with sqlite3.connect(catalog.path) as db:
        segments = db.execute("SELECT label FROM rail_station_nodes WHERE parent_id='facility:explicit' "
                              "ORDER BY object_id").fetchall()
    assert segments == [("甲站改名 · A→B",), ("甲站2道 · A→B",)]
    location = RailCatalog.effective_directory_path(
        SimpleNamespace(catalog=catalog, station_model=model), "explicit")
    assert location == ("车站目录", "甲省", "甲市", "技术作业待核实", "业务性质待核实", "甲站", "站内轨道")
    assert RailCatalog.effective_directory_path(
        SimpleNamespace(catalog=catalog, station_model=model), "unknown") == (
            "车站目录", "待核对", "调车线")
    edits = {"explicit": {"display_name": "新股道名"}}
    def renamed(key, raw):
        record = {**raw, **edits.get(key, {})}
        return record, ("普速铁路",), record.get("display_name", record["name"])
    assert update_directory_labels(catalog, edits, renamed)
    assert sync_catalog_directory(catalog, edits, renamed) is False
    with sqlite3.connect(catalog.path) as db:
        assert db.execute("SELECT label FROM rail_station_nodes WHERE id='facility:explicit'").fetchone()[0] == "新股道名"
    model.set_search("甲站")
    model.fetchMore()
    assert model.rowCount() > 0


def test_station_source_id_places_track_under_exact_station(qtbot, tmp_path, monkeypatch):
    import desktop.rail_station_catalog_model as station_module

    stations = [{"id": "node/1", "name": "同名站", "province": "甲省", "city": "甲市"},
                {"id": "node/2", "name": "同名站", "province": "甲省", "city": "甲市"}]
    monkeypatch.setattr(station_module, "rail_station_records", lambda *args, **kwargs: (stations, 2))
    (tmp_path / "rail_lines.sqlite").touch()
    catalog, _ = _index(tmp_path, {"track": _record(1, station_name="同名站", provinces=["甲省"],
                                                  station_source="node/2", track_role="shunting_track")})
    sync_station_catalog(tmp_path, catalog.path, [], {})
    model = StationCatalogModel(catalog.path)
    assert model.ids_below("station:node/2") == ({"node/2"}, {"track"})
    assert model.ids_below("station:node/1") == ({"node/1"}, set())


def test_search_honors_override_names_and_literal_wildcards(qtbot, tmp_path):
    catalog, _ = _index(tmp_path, {"a": _record(1, line_id="IL-EXACT-ID"), "b": _record(2)},
                        {"b": {"display_name": "新名字%_"}})
    model = RailDirectoryModel(catalog.path)
    model.set_search("%_")
    model.fetchMore()
    folder = model.index(0, 0)
    model.fetchMore(folder)
    assert model.rowCount(folder) == 1
    leaf = model.index(0, 0, folder)
    assert model.data(leaf) == "新名字%_"
    assert model.ids_below(model._node(leaf).key) == {"b"}
    model.set_search("IL-EXACT-ID")
    model.fetchMore()
    folder = model.index(0, 0)
    model.fetchMore(folder)
    assert model.rowCount(folder) == 1
    assert model.ids_below(model._node(model.index(0, 0, folder)).key) == {"a"}


def test_failed_cache_rebuild_keeps_previous_directory(tmp_path):
    catalog, _ = _index(tmp_path, {"a": _record(1)})

    def fail(key, record):
        raise ValueError("resolver failed")

    with pytest.raises(ValueError, match="resolver failed"):
        sync_catalog_directory(catalog, {"a": {"folder_path": ["新目录"]}}, fail)
    with sqlite3.connect(catalog.path) as db:
        assert db.execute("SELECT count(*) FROM rail_directory_members").fetchone()[0] == 1


def test_national_ui_uses_paged_views_and_keeps_edits_in_override(qtbot, tmp_path, monkeypatch):
    records = {f"RL-{i}": _record(i) for i in range(700)}
    source = tmp_path / "rail_catalog.json"
    source.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    original = source.read_bytes()
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", MapStub())
    qtbot.addWidget(widget)
    assert isinstance(widget.line_browser, QTreeView)
    assert widget.tree.topLevelItemCount() == 0
    assert widget.items == {}
    assert _loaded(widget.line_model.root) <= 128
    widget.move_items({"RL-699"}, ["自定义", "线路"])
    assert widget.meta("RL-699")["folder_path"] == ["自定义", "线路"]
    from desktop.catalog_workspace import read_overrides
    workspace_record = read_overrides(tmp_path / "settings.json")
    assert workspace_record["RL-699"]["folder_path"] == ["自定义", "线路"]
    assert not (tmp_path / "rail_line_directory.json").exists()
    existing_folder = widget.line_model._node(widget.line_model.index(0, 0))
    widget.line_model.fetchMore(widget.line_model.index(0, 0))
    loaded_children = tuple(existing_folder.children)
    assert widget.select_way(699, group_id="RL-699")
    assert existing_folder in widget.line_model.root.children
    assert tuple(existing_folder.children) == loaded_children
    index = widget.line_browser.currentIndex()
    assert index.isValid()
    opened = []
    widget.feature_activated.connect(opened.append)
    widget.line_browser.doubleClicked.emit(index)
    assert opened[-1]["properties"]["name"] == records["RL-699"]["name"]
    widget.reload_names(tmp_path / "missing-way-names.json")
    with monkeypatch.context() as patch:
        patch.setattr("desktop.rail_catalog_ui.sync_catalog_directory",
                      lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("改名不应重建全国目录")))
        widget.rename_item("RL-699", "新显示名")
        names = tmp_path / "rail_way_names.json"
        names.write_text(json.dumps({"698": "线路新别名"}, ensure_ascii=False), encoding="utf-8")
        widget.reload_names(names)
    index = widget.line_model.reveal_catalog_id("RL-699")
    assert widget.line_model.data(index).endswith("新显示名")
    assert widget.line_model.data(widget.line_model.reveal_catalog_id("RL-698")).endswith("线路新别名")
    widget.archive_items({"RL-699"})
    index = widget.line_model.reveal_catalog_id("RL-699")
    assert not (widget.line_model.flags(index) & Qt.ItemFlag.ItemIsUserCheckable)
    assert source.read_bytes() == original


def test_national_master_uses_compact_facility_filter(qtbot, tmp_path):
    records = {f"RL-{i}": _record(i, facility_only=i % 2 == 0) for i in range(700)}
    (tmp_path / "rail_catalog.json").write_text(
        json.dumps(records, ensure_ascii=False), encoding="utf-8")
    map_view = MapStub()
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", map_view)
    qtbot.addWidget(widget)

    widget.set_line_master("operating", True)
    assert ("setRailFacilityMode", "lines", []) in map_view.calls
    assert ("setRailSelection", None, None) in map_view.calls
    assert widget.line_model.all_visible
    assert not widget.facility_model.all_visible

    widget.set_station_track_master(True)
    assert ("setRailFacilityMode", "all", []) in map_view.calls
    assert widget.facility_model.all_visible


def test_update_catalog_directory_only_resolves_changed_keys(tmp_path):
    from desktop.rail_catalog_model import update_catalog_directory
    catalog, resolve = _index(tmp_path, {f"RL-{i}": _record(i) for i in range(60)})
    resolved = []

    def tracking_resolve(key, raw):
        resolved.append(key)
        return resolve(key, raw)

    changes = {"RL-5": {"rail_semantics": {"railway_class": "conventional",
                                           "line_role": "connecting_line",
                                           "track_role": "main_track"}}}
    changed, _ = update_catalog_directory(catalog, ["RL-5"], changes, tracking_resolve)
    assert changed
    assert set(resolved) == {"RL-5"}


def test_update_catalog_directory_matches_full_rebuild_structure(tmp_path):
    from desktop.rail_catalog_model import update_catalog_directory, sync_catalog_directory
    from desktop.rail_catalog_index import RailCatalogIndex, build_index

    records = {f"RL-{i}": _record(i) for i in range(30)}
    records["RL-3"] = _record(3, station_name="某站", station_id="ST-A",
                              track_role="shunting_track")

    def resolve_for(changes):
        def resolve(key, raw):
            value = {**raw, **changes.get(key, {})}
            if value.get("rail_semantics"):
                from desktop.rail_semantics import semantic_record
                value.update(semantic_record(raw, value))
            parents = (tuple(value["folder_path"])
                       if isinstance(value.get("folder_path"), list) and value.get("folder_path")
                       else ("普速铁路",))
            return value, parents, value.get("display_name", value["name"])
        return resolve

    incremental_dir = tmp_path / "incremental"
    full_dir = tmp_path / "full"
    incremental_dir.mkdir()
    full_dir.mkdir()
    catalog = RailCatalogIndex(build_index(incremental_dir, records))
    other_catalog = RailCatalogIndex(build_index(full_dir, records))

    changes = {"RL-3": {"rail_semantics": {"railway_class": "conventional",
                                           "line_role": "connecting_line",
                                           "track_role": "main_track"}}}
    sync_catalog_directory(catalog, {}, resolve_for({}))
    changed, facility_changed = update_catalog_directory(
        catalog, ["RL-3"], changes, resolve_for(changes))
    assert changed and facility_changed

    def dump(path):
        with sqlite3.connect(path) as db:
            nodes = db.execute(
                "SELECT id,parent_id,label,kind,object_id,path,child_count,total,archived,view "
                "FROM rail_directory_nodes ORDER BY id").fetchall()
            members = db.execute(
                "SELECT node_id,catalog_id FROM rail_directory_members ORDER BY node_id,catalog_id").fetchall()
        return nodes, members

    sync_catalog_directory(other_catalog, changes, resolve_for(changes))
    assert dump(catalog.path) == dump(other_catalog.path)
