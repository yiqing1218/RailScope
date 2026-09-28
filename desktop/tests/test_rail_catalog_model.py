import json
import sqlite3
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QTreeView

from desktop.rail_catalog_index import RailCatalogIndex, build_index
from desktop.rail_catalog_model import RailDirectoryModel, sync_catalog_directory
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
    catalog = RailCatalogIndex(build_index(tmp_path))
    assert set(catalog) == set(keys)
    assert "旧名称目录" not in catalog


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


def test_pages_are_sqlite_backed_and_focus_does_not_read_preceding_pages(qtbot, tmp_path):
    catalog, _ = _index(tmp_path, {f"RL-{i}": _record(i) for i in range(3000)})
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
    assert model.ids_below(model._node(index).key) == {"RL-2999"}
    assert _loaded(model.root) == 2
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
    }
    catalog, _ = _index(tmp_path, records)
    lines = RailDirectoryModel(catalog.path)
    facilities = RailDirectoryModel(catalog.path, "facilities")
    assert lines.reveal_catalog_id("main").isValid()
    assert facilities.reveal_catalog_id("main").isValid()
    assert not lines.reveal_catalog_id("yard-a").isValid()
    assert not lines.reveal_catalog_id("unknown").isValid()
    facilities.fetchMore()
    with sqlite3.connect(catalog.path) as db:
        paths = {key: json.loads(path) for key, path in db.execute(
            "SELECT m.catalog_id,d.path FROM rail_directory_members m JOIN rail_directory_nodes d ON d.id=m.node_id WHERE view='facilities'")}
    assert paths["yard-a"][1] != paths["yard-b"][1]
    assert paths["unknown"][1] == "设施归属待核实"


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
        sync_catalog_directory(catalog, {"a": {"display_name": "新名称"}}, fail)
    with sqlite3.connect(catalog.path) as db:
        assert db.execute("SELECT count(*) FROM rail_directory_members").fetchone()[0] == 1


def test_national_ui_uses_paged_views_and_keeps_edits_in_override(qtbot, tmp_path):
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
    assert widget.select_way(699, group_id="RL-699")
    index = widget.line_browser.currentIndex()
    assert index.isValid()
    opened = []
    widget.feature_activated.connect(opened.append)
    widget.line_browser.doubleClicked.emit(index)
    assert opened[-1]["properties"]["name"] == records["RL-699"]["name"]
    widget.rename_item("RL-699", "新显示名")
    index = widget.line_model.reveal_catalog_id("RL-699")
    assert widget.line_model.data(index) == "新显示名"
    widget.archive_items({"RL-699"})
    index = widget.line_model.reveal_catalog_id("RL-699")
    assert not (widget.line_model.flags(index) & Qt.ItemFlag.ItemIsUserCheckable)
    assert source.read_bytes() == original
