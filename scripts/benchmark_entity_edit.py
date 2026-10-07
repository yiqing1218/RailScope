"""Time the production Qt edit path against an isolated national snapshot.

All SQLite databases are backed up into an isolated scratch directory; settings
and their sidecars are copied. Never mutate or hard-link the user's dataset.
"""
import argparse
from collections import defaultdict
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import subprocess
import io
import zipfile
from time import perf_counter
from types import SimpleNamespace
import gc
import sqlite3
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'desktop'), str(ROOT / 'backend')]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def run(output, count=1, source_ref=None, capture_directory=None, project=None, scratch_root=None,
        _workspace=None):
    scratch_root = Path(scratch_root or ROOT / 'data/processed/edit-benchmark').resolve()
    scratch_root.mkdir(parents=True, exist_ok=True)
    if _workspace is None:
        with tempfile.TemporaryDirectory(prefix='run-', dir=scratch_root) as temporary:
            command = [sys.executable, str(Path(__file__).resolve()), '--output',
                       str(Path(output).resolve()), '--count', str(count),
                       '--scratch-root', str(scratch_root), '--worker-workspace', temporary]
            for flag, value in (('--source-ref', source_ref),
                                ('--capture-directory', capture_directory), ('--project', project)):
                if value is not None:
                    command.extend((flag, str(value)))
            subprocess.run(command, check=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        return json.loads(Path(output).read_text(encoding='utf-8'))
    if Path(_workspace).resolve().parent != scratch_root or not Path(_workspace).name.startswith('run-'):
        raise ValueError('性能测试工作目录必须位于隔离缓存中')
    project = Path(project or ROOT)
    if source_ref:
        baseline = ROOT / 'data/processed/edit-benchmark/baseline-code'
        baseline.mkdir(parents=True, exist_ok=True)
        archive = subprocess.check_output(['git', 'archive', '--format=zip', source_ref, 'desktop', 'backend'], cwd=ROOT)
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            bundle.extractall(baseline)
        shared = baseline / 'data/catalog'
        shared.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / 'data/catalog/rail_catalog_overrides.json', shared)
        sys.path[:0] = [str(baseline), str(baseline / 'desktop'), str(baseline / 'backend')]
    from PySide6.QtWidgets import QApplication, QMainWindow, QLineEdit, QDialogButtonBox
    from PySide6.QtGui import QFontDatabase
    import launcher
    import rail_catalog_ui as ui
    import catalog_workspace
    import rail_connection_ui
    from rail_ui import RailEditor
    from data_install import active_rail_directory
    from desktop.tests.test_operating_ui import MapStub

    app = QApplication.instance() or QApplication([])
    # The offscreen Windows platform has no system font database by default.
    font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc'
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    source = active_rail_directory(project)
    source_files = list(source.glob('*.sqlite'))
    source_stamps = [(p.stat().st_size, p.stat().st_mtime_ns) for p in source_files]
    scratch_root = Path(scratch_root or ROOT / 'data/processed/edit-benchmark').resolve()
    scratch_root.mkdir(parents=True, exist_ok=True)
    required = int(sum(p.stat().st_size for p in source_files) * 1.3) + 512 * 1024 * 1024
    if shutil.disk_usage(scratch_root).free < required:
        raise OSError(f'隔离测试需要至少 {required / 1024**3:.1f} GB 可用空间；尚未复制数据库')
    print('Backing up isolated national databases…', flush=True)
    timings = defaultdict(list)
    errors = []
    exceptions = []
    pbf_accesses = []
    def audit(event, args):
        if event == 'open' and args and str(args[0]).casefold().endswith('.pbf'):
            pbf_accesses.append(event)
    sys.addaudithook(audit)
    original_hook = sys.excepthook
    def exception_hook(kind, error, trace):
        exceptions.append(kind.__name__ + ': ' + str(error))
        original_hook(kind, error, trace)
    sys.excepthook = exception_hook
    @contextmanager
    def measured(name):
        start = perf_counter()
        try:
            yield
        finally:
            timings[name].append(round((perf_counter() - start) * 1000, 3))

    def wrap(owner, name):
        original = getattr(owner, name)
        def invoke(*args, **kwargs):
            with measured(name):
                return original(*args, **kwargs)
        setattr(owner, name, invoke)

    for name in ('rail_station_records', 'update_station_label', 'sync_station_catalog',
                 'update_catalog_directory', 'update_directory_labels'):
        wrap(ui, name)
    if hasattr(catalog_workspace, 'write_json_atomic'):
        wrap(catalog_workspace, 'write_json_atomic')
    if hasattr(catalog_workspace.CatalogWorkspace, '_write_entries'):
        wrap(catalog_workspace.CatalogWorkspace, '_write_entries')
    for name in ('_save_local_overrides', '_refresh_station_items', 'populate',
                 'save_station_override', 'save_overrides', '_update_paged_directory'):
        wrap(ui.RailCatalog, name)
    wrap(RailEditor, 'line_library')
    for name in ('__init__', 'connections'):
        original = getattr(rail_connection_ui.StationConnectionSelector, name)
        def invoke(*args, _fn=original, _name='connection_selector.' + name, **kwargs):
            with measured(_name):
                return _fn(*args, **kwargs)
        setattr(rail_connection_ui.StationConnectionSelector, name, invoke)
    with nullcontext(_workspace) as temporary:
        target = Path(temporary)
        for path in source.iterdir():
            if path.suffix == '.sqlite':
                # Snapshot a live SQLite cache through its transaction boundary;
                # copying the main file alone can miss an active rollback/WAL.
                with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(target / path.name)) as dst:
                    src.backup(dst)
                # Derived cache signatures refer to the immutable source's
                # size/mtime. A faithful backup must preserve that timestamp.
                shutil.copystat(path, target / path.name)
            elif path.is_file() and path.suffix in ('.sqlite', '.json'):
                shutil.copy2(path, target / path.name)
        settings = target / 'settings' / 'rail_catalog.json'
        settings.parent.mkdir()
        original_settings = project / 'data/user_settings/rail_catalog.json'
        shutil.copy2(original_settings, settings)
        for extension in ('.workspace.sqlite', '.edits.sqlite'):
            sidecar = original_settings.with_suffix(extension)
            if sidecar.exists():
                with closing(sqlite3.connect(sidecar.resolve().as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(settings.with_suffix(extension))) as dst:
                    src.backup(dst)
        for seed_name in ('rail_line_directory.json', 'rail_station_directory.json'):
            seed_path = project / 'data/catalog' / seed_name
            if seed_path.exists():
                shutil.copy2(seed_path, settings.with_name(seed_name))
        map_view = MapStub()
        class Host(QMainWindow):
            _rail_station_record_for_feature = launcher.Desk._rail_station_record_for_feature
            edit_selected_metadata = launcher.Desk.edit_selected_metadata
            refresh_entity_changes = launcher.Desk.refresh_entity_changes
        host = Host()
        host.setStyleSheet(launcher.THEME)
        print('Preparing isolated Qt catalog…', flush=True)
        with measured('catalog_initialization'):
            widget = ui.RailCatalog(target, settings, map_view)
        host.rail_catalog_widget = widget
        operations = SimpleNamespace(directory=target, path=target / 'settings/plan.json',
            catalog_metadata_path=settings, graph={'edges': [], 'points': []}, rail_payload=None)
        operations.line_library = lambda: RailEditor.line_library(operations)
        operations.invalidate_line_library = lambda: setattr(operations, '_line_library_signature', None)
        operations.push_corridors = lambda: None
        operations.retain_line_library_for_directory_move = lambda: RailEditor.retain_line_library_for_directory_move(operations)
        operations.patch_catalog_metadata = lambda overrides, changes: RailEditor.patch_catalog_metadata(operations, overrides, changes)
        host.rail_operations = operations
        operations.catalog_editor = widget
        operations.updated = SimpleNamespace(emit=lambda: None)
        operations.names_changed = SimpleNamespace(emit=lambda: launcher.Desk.refresh_map_names(host))
        operations.save_line_names = lambda names: RailEditor.save_line_names(operations, names)
        widget.line_names_changed.connect(operations.save_line_names)
        host.route_lookup, host.station_lookup = {}, {}
        host.map, host.config = map_view, {}
        host.metro_line_overrides = host.metro_station_overrides = SimpleNamespace(values={})
        host.load_status = SimpleNamespace(setText=lambda *_: None)
        host.display_feature = lambda *_: None
        host.selected_data = {}
        # Match current production's typed subscriptions. Legacy metadata_changed
        # is compatibility only and is not connected to a whole-world refresh.
        widget.semantic_changed.connect(lambda delta: launcher.Desk.refresh_semantic_changes(host, delta))
        widget.topology_changed.connect(lambda delta: operations.invalidate_line_library())
        widget.directory_changed.connect(lambda delta: launcher.Desk.refresh_directory_overrides(host, delta))
        widget.presentation_changed.connect(lambda: launcher.Desk.refresh_catalog_presentation(host))
        if hasattr(widget, 'entities_changed'):
            widget.entities_changed.connect(lambda delta: launcher.Desk.refresh_entity_changes(host, delta))
        with operations.line_library().connect() as db:
            ids = [row[0] for row in db.execute(
                "SELECT source_id FROM station_directory ORDER BY source_id LIMIT ?", (count,))]
        original_exec = launcher.QDialog.exec
        original_warning = launcher.QMessageBox.warning
        launcher.QMessageBox.warning = lambda *args: errors.append(str(args[-1]))
        def edit_dialog(dialog):
            if capture_directory:
                images = Path(capture_directory)
                images.mkdir(parents=True, exist_ok=True)
                dialog.show()
                app.processEvents()
                dialog.grab().save(str(images / ('station-editor.png' if len(records) < len(ids) else 'line-editor.png')))
            name = dialog.findChildren(QLineEdit)[0]
            name.setText(name.text() + '计时改名')
            with measured('editor_save_click'):
                dialog.findChild(QDialogButtonBox).accepted.emit()
            return dialog.result()
        launcher.QDialog.exec = edit_dialog
        records = []
        try:
            print('Measuring station editor saves…', flush=True)
            for ident in ids:
                with measured('station_lookup'):
                    record = widget.station_record(ident)
                if not record:
                    errors.append('station lookup failed: ' + ident)
                    continue
                old_name = record['name']
                with measured('open_edit_save_total'):
                    launcher.Desk.edit_rail_station_metadata(host, ident)
                records.append({'id': ident, 'cache_updated': record['name'] == old_name + '计时改名',
                    'persisted': widget.local_overrides.get('station:' + ident, {}).get('display_name') == old_name + '计时改名'})
            print('Measuring line editor saves…', flush=True)
            with widget.catalog._connect() as db:
                line_id = db.execute('SELECT id FROM catalog WHERE line_id IS NOT NULL AND unnamed=0 ORDER BY id LIMIT 1').fetchone()[0]
            with measured('line_remarks_save'):
                widget.save_overrides({line_id: {'technical_attributes': {'remarks': '性能计时备注'}}})
            with measured('line_rename_save'):
                widget.save_overrides({line_id: {'display_name': '性能计时线路'}})
            with measured('line_color_save'):
                widget.save_overrides({line_id: {'color': '#8b2535'}})
            with measured('line_width_save'):
                widget.save_overrides({line_id: {'width': 3.25}})
            host.edit_line_metadata = lambda *args, **kwargs: launcher.Desk.edit_line_metadata(host, *args, **kwargs)
            with measured('line_open_edit_save_total'):
                launcher.Desk.edit_rail_line_metadata(host, line_id)
            for measurement, changes in [('station_remarks_save', {'custom_attributes': {'备注': '计时备注'}}),
                                         ('station_type_save', {'station_type_value': '编组站'})]:
                try:
                    with measured(measurement):
                        widget.save_station_override(ids[0], **changes)
                except Exception as error:
                    exceptions.append(type(error).__name__ + ': ' + str(error))
        finally:
            launcher.QDialog.exec = original_exec
            launcher.QMessageBox.warning = original_warning
            sys.excepthook = original_hook
        result = {'dataset': {p.name: p.stat().st_size for p in source.glob('*.sqlite')},
            'source_unchanged': source_stamps == [(p.stat().st_size, p.stat().st_mtime_ns) for p in source_files],
            'timings_ms': dict(timings), 'stations': records, 'errors': errors,
            'exceptions': exceptions, 'pbf_accesses': pbf_accesses,
            'line_entity_id': line_id, 'source_ref': source_ref or 'working-tree',
            'map_commands': [call[0] for call in map_view.calls],
            'boundary': 'Production Qt slots / SQLite; MapStub excludes browser paint and full active TrainRun load.'}
        assert result['source_unchanged'], 'Benchmark changed its source dataset'
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        from background_queries import query_queue
        assert query_queue().pool.waitForDone(30000), 'Editor background queries did not settle'
        app.processEvents()
        # Close read leases before TemporaryDirectory removes the copies.
        # A hidden Qt model can still own an open SQLite handle on Windows.
        for model_name in ('line_model', 'facility_model', 'station_model'):
            model = getattr(widget, model_name, None)
            if model is not None and hasattr(model, '_sessions'):
                model._sessions.close_current_thread()
        library = getattr(operations, '_line_library', None)
        if library is not None and hasattr(library, '_sessions'):
            library._sessions.close_current_thread()
        widget.catalog.close()
        widget.close()
        host.close()
        gc.collect()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--count', type=int, default=1)
    parser.add_argument('--source-ref', help='Benchmark archived source without changing the checkout')
    parser.add_argument('--capture-directory', help='Grab the actual Qt editor widgets before saving')
    parser.add_argument('--project', type=Path, help='Explicit source project, copied read-only')
    parser.add_argument('--scratch-root', type=Path, help='Isolated copy directory on a disk with enough free space')
    parser.add_argument('--worker-workspace', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    run(args.output, args.count, args.source_ref, args.capture_directory, args.project, args.scratch_root,
        args.worker_workspace)
