from pathlib import Path
from copy import deepcopy

import pytest

from desktop.catalog_metadata import CatalogOverrides
from desktop.rail_catalog_ui import RailCatalog
from desktop.rail_ui import RailEditor
from desktop.tests.test_operating_ui import MapStub


@pytest.mark.parametrize('raw', ['{broken', '{"schema":"test","overrides":{"x":7}}'])
def test_failed_metro_load_cannot_overwrite_original(tmp_path, raw):
    path = tmp_path / 'metro.json'
    path.write_text(raw, encoding='utf-8')
    store = CatalogOverrides(path, 'test')
    with pytest.raises(ValueError):
        store.load()
    with pytest.raises(ValueError, match='载入'):
        store.update('new', display_name='新名称')
    assert path.read_text(encoding='utf-8') == raw


def test_failed_shared_catalog_load_blocks_local_write(qtbot, tmp_path, monkeypatch):
    shared = tmp_path / 'shared.json'
    shared.write_text('{broken', encoding='utf-8')
    monkeypatch.setattr('desktop.rail_catalog_ui.QMessageBox.warning', lambda *_: None)
    widget = RailCatalog(tmp_path, tmp_path / 'local.json', MapStub(), shared_path=shared)
    qtbot.addWidget(widget)
    with pytest.raises(ValueError, match='载入'):
        widget._save_local_overrides({'x': {'display_name': '新名称'}})
    assert not widget.path.exists()


def test_corrupt_rail_plan_keeps_original_after_edit_and_save(qtbot, tmp_path):
    path = tmp_path / 'rail.json'
    path.write_text('{broken', encoding='utf-8')
    editor = RailEditor(MapStub(), tmp_path, path)
    qtbot.addWidget(editor)
    editor.changed('编辑后的界面')
    editor.save()
    assert path.read_text(encoding='utf-8') == '{broken'
    assert '载入' in editor.message.text()


def test_domain_rejection_does_not_publish_partial_plan(qtbot, tmp_path, monkeypatch):
    editor = RailEditor(MapStub(), tmp_path, tmp_path / 'rail.json')
    qtbot.addWidget(editor)
    editor.undo_stack.append({'existing': 'command'})
    editor.redo_stack.append({'existing': 'redo'})
    editor.playing = True
    before = deepcopy((editor.graph, editor.rail_payload, editor.undo_stack, editor.redo_stack))
    old_plan, old_repo = editor.plan, editor.domain_repo
    editor.map.view.calls.clear()

    def reject(*_args, **_kwargs):
        raise ValueError('rejected by shared domain')

    monkeypatch.setattr('desktop.domain_adapter.build_repository', reject)
    candidate = editor.empty_payload()
    candidate['source'] = 'new candidate'
    with pytest.raises(ValueError, match='shared domain'):
        editor.apply_payload(candidate)
    assert (editor.graph, editor.rail_payload, editor.undo_stack, editor.redo_stack) == before
    assert editor.plan is old_plan and editor.domain_repo is old_repo
    assert editor.playing
    assert editor.map.view.calls == []


def test_failed_atomic_override_write_keeps_disk_and_live_state(tmp_path, monkeypatch):
    store = CatalogOverrides(tmp_path / 'metro.json', 'test')
    store.update('station-1', display_name='original')
    original = store.path.read_bytes()
    values = store.values

    def fail(*_args):
        raise OSError('disk failure')

    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError, match='disk failure'):
        store.update('station-1', display_name='new')
    assert store.values is values and values['station-1']['display_name'] == 'original'
    assert store.path.read_bytes() == original
    assert not list(tmp_path.glob('*.tmp'))
