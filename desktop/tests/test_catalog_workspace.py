"""Regression tests for the authoritative edit state, reload and failed writes."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from desktop.catalog_metadata import CatalogOverrides
from desktop.rail_catalog_ui import RailCatalog
from desktop.tests.test_operating_ui import MapStub


def test_metro_lookup_keeps_live_override_reference(tmp_path, monkeypatch):
    from desktop.metro_store import StationLookup
    store = CatalogOverrides(tmp_path / 'metro.json', 'test')
    lookup = StationLookup(tmp_path / 'metro.sqlite', store.values)
    monkeypatch.setattr('desktop.metro_store.find_station_alias', lambda *_:
        {'name': '源站名', 'physical_station_id': 'ST-1'})
    store.update('alias-1', display_name='第一次改名')
    store.update('alias-1', display_name='第二次改名', archived=True)
    assert lookup['alias-1']['name'] == '第二次改名'
    assert lookup['alias-1']['archived']
    store.load()
    assert lookup['alias-1']['name'] == '第二次改名'


def catalog(qtbot, tmp_path):
    (tmp_path / 'rail_catalog.json').write_text(json.dumps({'RL-1': {
        'name': '源线路', 'way_ids': [1], 'track_type': '普速铁路线'}}), encoding='utf-8')
    widget = RailCatalog(tmp_path, tmp_path / 'settings.json', MapStub(),
                         shared_path=tmp_path / 'missing-shared.json')
    qtbot.addWidget(widget)
    return widget


def test_reload_workspace_wins_over_old_exchange(qtbot, tmp_path):
    old = {'schema': 'railscope.catalog-exchange.v1', 'kind': 'rail-lines',
           'overrides': {'RL-1': {'display_name': '旧交换名', 'folder_path': ['旧目录']}}}
    exchange = tmp_path / 'rail_line_directory.json'
    exchange.write_text(json.dumps(old), encoding='utf-8')
    widget = catalog(qtbot, tmp_path)
    widget.rename_item('RL-1', '新工作区名')
    widget.move_items({'RL-1'}, ['新目录'])
    # Simulate a copied older exchange file; the local project remains authoritative.
    exchange.write_text(json.dumps(old), encoding='utf-8')
    reloaded = catalog(qtbot, tmp_path)
    assert reloaded.display_name('RL-1') == '新工作区名'
    assert reloaded.parents('RL-1') == ('新目录',)


def test_edit_does_not_rewrite_exchange_seed(qtbot, tmp_path):
    exchange = tmp_path / 'rail_line_directory.json'
    exchange.write_text(json.dumps({'RL-1': {'display_name': '种子名'}}), encoding='utf-8')
    original = exchange.read_bytes()
    widget = catalog(qtbot, tmp_path)
    widget.rename_item('RL-1', '新名字')
    assert exchange.read_bytes() == original
    widget.undo_catalog()
    assert widget.display_name('RL-1') == '种子名'
    widget.redo_catalog()
    assert widget.display_name('RL-1') == '新名字'


def test_failed_undo_preserves_data_and_both_history_stacks(qtbot, tmp_path, monkeypatch):
    widget = catalog(qtbot, tmp_path)
    widget.rename_item('RL-1', '新名字')
    previous = deepcopy((widget.local_overrides, widget.catalog_undo, widget.catalog_redo))
    from desktop.catalog_workspace import read_overrides
    disk = read_overrides(widget.path)
    def fail_write(entries):
        raise OSError('simulated disk failure')
    monkeypatch.setattr(widget.workspace, '_write_entries', fail_write)
    with pytest.raises(OSError, match='disk failure'):
        widget.undo_catalog()
    assert (widget.local_overrides, widget.catalog_undo, widget.catalog_redo) == previous
    assert read_overrides(widget.path) == disk


@pytest.mark.parametrize('raw', ['{broken', '{"RL-1": {"archived": "bad"}}'])
def test_corrupt_workspace_cannot_be_overwritten_by_later_edit(tmp_path, raw):
    from desktop.catalog_workspace import CatalogWorkspace
    path = tmp_path / 'settings.json'
    path.write_text(raw, encoding='utf-8')
    store = CatalogWorkspace(path)
    with pytest.raises(ValueError):
        store.load()
    with pytest.raises(ValueError, match='载入'):
        store.update({'RL-1': {'display_name': 'replacement'}})
    assert path.read_text(encoding='utf-8') == raw
