"""Audit current directory slots; no application implementation is changed.

Use --national for a private SQLite snapshot of the installed dataset. Source
geometry/topology connections are forced read-only. MapStub records bridge
commands; browser painting and active TrainRun rebuilding are outside timing.
"""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from functools import wraps
import gc
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'desktop'), str(ROOT / 'backend')]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


class Probe:
    def __init__(self):
        self.phase = 'initialization'
        self.elapsed = defaultdict(lambda: defaultdict(list))
        self.counts = defaultdict(Counter)
        self.patches = []

    def wrap(self, owner, name, label=None):
        original = getattr(owner, name)
        label = label or name
        @wraps(original)
        def invoke(*args, **kwargs):
            phase = self.phase
            self.counts[phase][label] += 1
            if name == '_write_entries':
                self.counts[phase]['workspace_rows_written'] += len(args[1])
            start = perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                self.elapsed[phase][label].append((perf_counter() - start) * 1000)
        handle = patch.object(owner, name, invoke)
        handle.start()
        self.patches.append(handle)

    def operation(self, phase, widget, callback):
        self.phase = phase
        widget.map.calls.clear()
        start = perf_counter()
        callback()
        result = {
            'elapsed_ms': round((perf_counter() - start) * 1000, 3),
            'calls': dict(self.counts[phase]),
            'nested_timings_ms': {key: [round(value, 3) for value in values]
                                  for key, values in self.elapsed[phase].items()},
            'map_commands': dict(Counter(value[0] for value in widget.map.calls)),
        }
        print(phase, result['elapsed_ms'], 'ms', flush=True)
        self.phase = 'between_operations'
        return result

    def close(self):
        for handle in reversed(self.patches):
            handle.stop()


def install_probe(ui, probe):
    import catalog_workspace
    import rail_catalog_model
    import lazy_directory
    import rail_station_catalog_model
    from rail_ui import RailEditor
    for name in ('save_overrides', '_save_local_overrides', '_update_paged_directory',
                 '_prepare_station_catalog', '_sync_paged_visibility', 'populate',
                 '_populate_paged_directory', '_refresh_station_changes',
                 'send_station_visibility'):
        probe.wrap(ui.RailCatalog, name)
    for name in ('update_catalog_directory', 'station_catalog_signature',
                 'sync_station_catalog', 'update_station_placement',
                 'sync_catalog_directory', 'build_catalog_index'):
        probe.wrap(ui, name)
    probe.wrap(rail_catalog_model, '_directory_signature')
    probe.wrap(catalog_workspace.CatalogWorkspace, '_write_entries')
    probe.wrap(lazy_directory.SqliteDirectoryModel, 'reset_from_disk', 'directory_model_reset')
    probe.wrap(lazy_directory.SqliteDirectoryModel, 'refresh_affected')
    probe.wrap(rail_station_catalog_model.StationCatalogModel, 'reset_from_disk', 'station_model_reset')
    for name in ('invalidate_line_library', 'line_library', 'retain_line_library_for_directory_move'):
        probe.wrap(RailEditor, name)


def close_widget(widget):
    widget.catalog.close()
    widget.close()
    widget.deleteLater()
    gc.collect()


def synthetic(ui, app, probe, base):
    from desktop.tests.test_operating_ui import MapStub
    from rail_line_workspace import expand_assembly_changes
    from catalog_workspace import CatalogWorkspace
    results = []
    # Change national catalog size independently from overlay size.
    for total, overlay in ((700, 0), (7000, 0), (700, 1000), (700, 10000), (700, 100000)):
        directory = base / f'catalog-{total}-overlays-{overlay}'
        directory.mkdir()
        records = {f'RL-{i}': {'name': f'Line {i}', 'way_ids': [i], 'track_role': 'main_track'}
                   for i in range(total)}
        (directory / 'rail_catalog.json').write_text(json.dumps(records), encoding='utf-8')
        widget = ui.RailCatalog(directory, directory / 'settings.json', MapStub())
        # Ordinary edge notes are deliberately unrelated to directory placement.
        notes = {f'object:network_edge_id:NE-{i}': {'technical_attributes': {'remarks': 'audit'}}
                 for i in range(overlay)}
        widget.workspace.values.update(notes)
        widget.overrides.update(notes)
        cases = {}
        prefix = f'catalog={total},overlay={overlay}'
        for label, count in (('one_line', 1), ('hundred_lines', 100)):
            cases[label] = probe.operation(prefix + '/' + label, widget,
                lambda count=count, label=label: widget.move_items(
                    {f'RL-{i}' for i in range(count)}, ['Audit', label]))
        cases['undo'] = probe.operation(prefix + '/undo', widget, widget.undo_catalog)
        cases['redo'] = probe.operation(prefix + '/redo', widget, widget.redo_catalog)
        results.append({'catalog_count': total, 'unrelated_override_count': overlay, 'operations': cases})
        close_widget(widget)
        app.processEvents()
    assemblies = []
    for member_count in (1, 100, 1000, 10000):
        # Isolate existing expansion/write cost; total overlay size stays 10,000.
        overrides = {f'RL-{i}': {'technical_attributes': {'remarks': 'audit'}} for i in range(10000)}
        for i in range(member_count):
            overrides[f'RL-{i}']['assembly_id'] = 'RLU-audit'
        overrides['line-assembly:RLU-audit'] = {
            'name': 'Audit assembly', 'active': True, 'members': [f'IL-{i}' for i in range(member_count)]}
        workspace = CatalogWorkspace(base / f'assembly-{member_count}.json')
        workspace.path.write_text(json.dumps(overrides), encoding='utf-8')
        workspace.load()
        samples = []
        for repeat in range(3):
            start = perf_counter()
            expanded = expand_assembly_changes(workspace.values, {'RL-0': {'folder_path': ['Audit', str(repeat)]}})
            expansion_ms = (perf_counter() - start) * 1000
            start = perf_counter()
            workspace.update(expanded)
            samples.append({'expanded_owners': len(expanded), 'expansion_ms': round(expansion_ms, 3),
                            'sqlite_commit_ms': round((perf_counter() - start) * 1000, 3)})
        assemblies.append({'member_count': member_count, 'total_overrides': 10001, 'samples': samples})
    return {'directory_scaling': results, 'assembly_expansion_and_persistence': assemblies}


def national(ui, app, probe, base):
    import launcher
    from data_install import active_rail_directory
    from rail_ui import RailEditor
    from rail_line_store import fingerprint, index_ready
    from catalog_workspace import edit_database
    from desktop.tests.test_operating_ui import MapStub
    source = active_rail_directory(ROOT)
    if not index_ready(source / 'rail_lines.sqlite', fingerprint(source / 'rail.sqlite', [])):
        raise RuntimeError('Installed line index is stale; audit refuses to rebuild source data')
    directory = base / 'national'
    directory.mkdir()
    stamps = {path.name: (path.stat().st_size, path.stat().st_mtime_ns)
              for path in source.iterdir() if path.is_file() and path.suffix in ('.json', '.sqlite')}
    def content_hashes():
        hashes = {}
        # The running desktop may write its rebuildable catalog projections.
        # Geometry/topology are immutable; the catalog is backed up below.
        for name in ('rail.sqlite', 'rail_lines.sqlite'):
            digest = hashlib.sha256()
            with (source / name).open('rb') as stream:
                while block := stream.read(8*1024*1024):
                    digest.update(block)
            hashes[name] = digest.hexdigest()
        return hashes
    original_hashes = content_hashes()
    for path in source.iterdir():
        if path.name == 'rail_catalog.sqlite':
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as src, \
                    closing(sqlite3.connect(directory / path.name)) as dst:
                src.backup(dst)
        elif path.is_file() and path.suffix in ('.sqlite', '.json'):
            os.link(path, directory / path.name)
    settings = directory / 'settings/rail_catalog.json'
    settings.parent.mkdir()
    seed = ROOT / 'data/user_settings/rail_catalog.json'
    if seed.exists():
        shutil.copy2(seed, settings)
    if edit_database(seed).exists():
        with closing(sqlite3.connect(edit_database(seed).resolve().as_uri() + '?mode=ro', uri=True)) as src, \
                closing(sqlite3.connect(edit_database(settings))) as dst:
            src.backup(dst)
    legacy = (ROOT / 'data/user_settings/rail_catalog.json').with_suffix('.edits.sqlite')
    if legacy.exists() and not edit_database(settings).exists():
        with closing(sqlite3.connect(legacy.resolve().as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(settings.with_suffix('.edits.sqlite'))) as dst:
            src.backup(dst)
    for name in ('rail_line_directory.json', 'rail_station_directory.json'):
        seed = ROOT / 'data/catalog' / name
        if seed.exists():
            shutil.copy2(seed, settings.with_name(name))
    raw_connect = sqlite3.connect
    protected = {path.resolve() for path in source.glob('*.sqlite')} | {
        path.resolve() for path in directory.glob('*.sqlite') if path.name != 'rail_catalog.sqlite'}
    def catalog_rows_hash():
        digest = hashlib.sha256()
        with closing(raw_connect((directory/'rail_catalog.sqlite').resolve().as_uri() + '?mode=ro', uri=True)) as db:
            for row in db.execute('SELECT id,data FROM catalog ORDER BY id'):
                digest.update(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode())
                digest.update(b'\n')
        return digest.hexdigest()
    catalog_before = catalog_rows_hash()
    class AuditConnection(sqlite3.Connection):
        def __init__(self, database, *args, **kwargs):
            super().__init__(database, *args, **kwargs)
            self.audit_catalog = 'rail_catalog.sqlite' in str(database)
            self.audit_closed = False
            def trace(sql):
                normalized = ' '.join(sql.upper().split()).rstrip(';')
                for table in ('RAIL_STATION_NODES', 'RAIL_FACILITY_TRACK_OWNERS',
                              'RAIL_DIRECTORY_NODES', 'CATALOG'):
                    if normalized == 'DELETE FROM ' + table:
                        probe.counts[probe.phase]['full_delete_' + table.lower()] += 1
            self.set_trace_callback(trace)

        def close(self):
            if not self.audit_closed and self.audit_catalog:
                probe.counts[probe.phase]['catalog_sql_changed_rows'] += self.total_changes
            self.audit_closed = True
            super().close()

    def read_only_sources(database, *args, **kwargs):
        if not kwargs.get('uri') and isinstance(database, (str, Path)) and Path(database).resolve() in protected:
            database = Path(database).resolve().as_uri() + '?mode=ro'
            kwargs['uri'] = True
        kwargs.setdefault('factory', AuditConnection)
        return raw_connect(database, *args, **kwargs)
    probe.phase = 'national_initialization'
    with patch.object(sqlite3, 'connect', read_only_sources):
        start = perf_counter()
        widget = ui.RailCatalog(directory, settings, MapStub())
        operations = SimpleNamespace(directory=directory, path=settings.with_name('plan.json'),
            catalog_metadata_path=settings, graph={'edges': [], 'points': []}, rail_payload=None)
        operations.line_library = lambda: RailEditor.line_library(operations)
        operations.invalidate_line_library = lambda: RailEditor.invalidate_line_library(operations)
        operations.retain_line_library_for_directory_move = lambda: RailEditor.retain_line_library_for_directory_move(operations)
        operations.push_corridors = lambda: probe.counts[probe.phase].update(['push_corridors_requested'])
        host = SimpleNamespace(rail_catalog_widget=widget, rail_operations=operations, map=widget.map,
            config={}, metro_line_overrides=SimpleNamespace(values={}), metro_station_overrides=SimpleNamespace(values={}),
            selected_data={})
        host.refresh_directory_overrides = lambda change=None: launcher.Desk.refresh_directory_overrides(host, change)
        host.refresh_signal_boxes = lambda: launcher.Desk.refresh_signal_boxes(host)
        host.refresh_entity_changes = lambda changes: launcher.Desk.refresh_entity_changes(host, changes)
        widget.topology_changed.connect(lambda change: launcher.Desk.refresh_topology_changes(host, change))
        widget.semantic_changed.connect(lambda change: launcher.Desk.refresh_semantic_changes(host, change))
        widget.station_assignment_changed.connect(host.refresh_directory_overrides)
        widget.directory_changed.connect(host.refresh_directory_overrides)
        widget.entities_changed.connect(lambda delta: launcher.Desk.refresh_entity_changes(host, delta))
        widget.station_presentation_changed.connect(lambda: launcher.Desk.refresh_signal_boxes(host))
        operations.line_library()
        init_ms = (perf_counter() - start) * 1000
        with widget.catalog._connect() as db:
            lines = [row[0] for row in db.execute("SELECT DISTINCT m.catalog_id FROM rail_directory_members m "
                "JOIN rail_directory_nodes n ON n.id=m.node_id WHERE n.view='lines' ORDER BY m.catalog_id LIMIT 100")]
            facility = db.execute("SELECT m.catalog_id FROM rail_directory_members m JOIN rail_directory_nodes n "
                "ON n.id=m.node_id WHERE n.view='facilities' ORDER BY m.catalog_id LIMIT 1").fetchone()
            stations = [row[0] for row in db.execute("SELECT object_id FROM rail_station_nodes "
                "WHERE kind='station' ORDER BY object_id LIMIT 100")]
            counts = {name: db.execute(f'SELECT count(*) FROM {name}').fetchone()[0]
                      for name in ('catalog', 'rail_directory_nodes', 'rail_station_nodes', 'rail_facility_track_owners')}
            track = db.execute('SELECT object_id FROM rail_facility_track_owners ORDER BY object_id LIMIT 1').fetchone()
        if not lines or not stations or not facility or not track:
            raise RuntimeError('National fixture does not include all required directory object kinds')
        for sid in stations:
            if widget.station_record(sid) is None:
                raise RuntimeError('Station lookup failed before timing')
        override_count = len(widget.overrides)
        cases = {}
        actions = [
            ('one_line', lambda: widget.move_items({lines[0]}, ['Audit', 'line'])),
            ('one_line_undo', widget.undo_catalog), ('one_line_redo', widget.redo_catalog),
            ('one_station', lambda: widget.save_station_changes({stations[0]}, folder_path=['Audit', 'station'])),
            ('one_station_undo', widget.undo_catalog), ('one_station_redo', widget.redo_catalog),
            ('hundred_lines', lambda: widget.move_items(set(lines), ['Audit', '100 lines'])),
            ('hundred_stations', lambda: widget.save_station_changes(set(stations), folder_path=['Audit', '100 stations'])),
            ('one_facility_folder', lambda: widget.move_items({facility[0]}, ['Audit', 'facility'])),
            ('one_track_assignment', lambda: widget._assign_station_assets(set(), {track[0]}, stations[0])),
        ]
        for name, callback in actions:
            cases[name] = probe.operation('national/' + name, widget, callback)
            app.processEvents()
        repeated = defaultdict(list)
        for repeat in range(5):
            prefix = ['Audit', 'repeat-' + str(repeat)]
            samples = [
                ('one_line', lambda: widget.move_items({lines[0]}, prefix + ['line'])),
                ('one_station', lambda: widget.save_station_changes({stations[0]}, folder_path=prefix + ['station'])),
                ('hundred_lines', lambda: widget.move_items(set(lines), prefix + ['100 lines'])),
                ('hundred_stations', lambda: widget.save_station_changes(set(stations), folder_path=prefix + ['100 stations'])),
                ('one_facility_folder', lambda: widget.move_items({facility[0]}, prefix + ['facility'])),
                ('one_track_assignment', lambda: widget._assign_station_assets(set(), {track[0]}, stations[(repeat+1) % 2])),
            ]
            for name, callback in samples:
                repeated[name].append(probe.operation(f'national/repeat-{repeat}/{name}', widget, callback))
                app.processEvents()
        result = {'file_bytes': {name: value[0] for name, value in stamps.items() if name.endswith('.sqlite')},
                  'initialization_ms': round(init_ms, 3), 'row_counts_before_moves': counts,
                  'override_count_before_moves': override_count,
                  'operations': cases, 'repeated_operations': dict(repeated)}
        plan_source = ROOT / 'data/processed/operations/rail_plan.json'
        if plan_source.exists():
            print('verify installed active plan references', flush=True)
            from dataclasses import asdict
            from railscope.integrity import validate_repository
            plan_path = settings.with_name('active-plan.json')
            shutil.copy2(plan_source, plan_path)
            editor = RailEditor(widget.map, directory, plan_path, catalog_metadata_path=settings)
            editor.timer.stop()
            if editor._plan_load_error:
                raise RuntimeError('Installed active plan failed validation: ' + editor._plan_load_error)
            def domain_snapshot(repo):
                validate_repository(repo)
                fields = ('nodes','edges','lines','stations','corridors','station_routes','train_runs','train_services')
                values = {field: {key: asdict(value) for key,value in getattr(repo,field).items()} for field in fields}
                values['stops'] = [asdict(value) for value in repo.stops]
                return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            before = domain_snapshot(editor.domain_repo)
            original_document = json.dumps(editor.document(), ensure_ascii=False, sort_keys=True)
            retained_library = editor.line_library()
            widget.directory_changed.connect(lambda change: editor.retain_line_library_for_directory_move())
            widget.station_assignment_changed.connect(lambda change: editor.retain_line_library_for_directory_move())
            widget.move_items({lines[0]}, ['Audit','active-plan-line'])
            widget.save_station_changes({stations[0]}, folder_path=['Audit','active-plan-station'])
            widget._assign_station_assets(set(), {track[0]}, stations[1])
            widget.undo_catalog()
            widget.redo_catalog()
            repo, _ = editor.canonical_repository(editor.workspace_identity_path)
            after_domain = domain_snapshot(repo)
            if before != after_domain or json.dumps(editor.document(), ensure_ascii=False, sort_keys=True) != original_document:
                raise RuntimeError('Directory edit changed physical/operating references in installed plan')
            result['loaded_plan_integrity'] = {'domain_sha256_before': before, 'domain_sha256_after': after_domain,
                'train_runs': len(repo.train_runs), 'corridors': len(repo.corridors), 'station_routes': len(repo.station_routes),
                'edges': len(repo.edges), 'line_library_retained': editor.line_library() is retained_library,
                'plan_document_unchanged': True, 'repository_validation_passed': True}
            editor.close()
        close_widget(widget)
        app.processEvents()
    after = {name: (source / name).stat() for name in stamps}
    changed_sources = [name for name, value in stamps.items()
                       if (after[name].st_size, after[name].st_mtime_ns) != value]
    if any(name != 'rail_catalog.sqlite' for name in changed_sources):
        raise RuntimeError('Source snapshot changed during audit; timings cannot be certified')
    result['source_files_size_and_mtime_unchanged'] = not changed_sources
    result['external_mutable_projection_changes'] = changed_sources
    result['geometry_topology_size_and_mtime_unchanged'] = True
    catalog_after = catalog_rows_hash()
    if catalog_before != catalog_after:
        raise RuntimeError('Directory edit modified catalog source records in the isolated snapshot')
    result['catalog_source_rows_sha256_before'] = catalog_before
    result['catalog_source_rows_sha256_after'] = catalog_after
    print('verify source content hashes', flush=True)
    after_hashes = content_hashes()
    if original_hashes != after_hashes:
        raise RuntimeError('Source content changed during edit verification')
    result['source_sha256_before'] = original_hashes
    result['source_sha256_after'] = after_hashes
    return result


def run(output, include_national=False):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    import rail_catalog_ui as ui
    app = QApplication.instance() or QApplication([])
    font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc'
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    errors = []
    original_hook = sys.excepthook
    def exception_hook(kind, error, trace):
        errors.append(kind.__name__ + ': ' + str(error))
        original_hook(kind, error, trace)
    def no_pbf(event, args):
        if event == 'open' and args and str(args[0]).casefold().endswith('.pbf'):
            raise RuntimeError('Unexpected PBF access during directory edit audit')
    sys.addaudithook(no_pbf)
    sys.excepthook = exception_hook
    probe = Probe()
    install_probe(ui, probe)
    scratch = ROOT / 'data/processed/directory-audit'
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='run-', dir=scratch, ignore_cleanup_errors=True) as temporary:
            base = Path(temporary)
            result = {'source_ref': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                      'boundary': 'Current production Qt slots/SQLite, instrumented wall time; MapStub excludes browser paint. '
                                  'Timed slots use no active TrainRun and record push_corridors; installed active-plan integrity is verified separately. '
                                  'Synthetic notes are injected before timing.',
                      'synthetic': synthetic(ui, app, probe, base)}
            if include_national:
                result['national'] = national(ui, app, probe, base)
            result['exceptions'] = errors
            if errors:
                raise RuntimeError('A Qt signal failed; audit results are invalid')
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        probe.close()
        sys.excepthook = original_hook
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--national', action='store_true')
    options = parser.parse_args()
    run(options.output, options.national)
