"""Correct named connecting lines in the workspace; preview unless --apply."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'backend'))
from desktop.catalog_workspace import read_overrides
from desktop.connecting_lines import connecting_line_name, connecting_line_override, NAME_RULE_VERSION, CONNECTING_LINE_COLOR
from desktop.data_install import active_rail_directory
from desktop.display_names import apply_names, object_key
from desktop.rail_catalog_index import RailCatalogIndex
from desktop.rail_line_workspace import EffectiveOverrides


def collect_changes(catalog, overrides, directory, snapshot):
    changes, matched = {}, {}
    previous_roles = Counter()

    def add(key, record, edit):
        delta = connecting_line_override(record, edit, snapshot)
        if delta:
            delta = {field: value for field, value in delta.items() if edit.get(field) != value}
            if delta:
                changes[key] = delta

    for key, record in catalog.items():
        edit = overrides.get(key, {})
        name = connecting_line_name(record, edit)
        if not name:
            continue
        matched[key] = name
        previous_roles[edit.get('rail_semantics', {}).get('line_role', record.get('line_role', 'unknown'))] += 1
        add(key, record, edit)
        line_id = record.get('line_id')
        if line_id and line_id != key:
            line_edit = overrides.get(line_id, {})
            add(line_id, {'name': name}, {field: value for field, value in line_edit.items()
                if field not in ('display_name', 'line_name', 'assembly_name')})
    # A segment's explicit override has higher priority than its line owner.
    # Visit existing owners only, using the indexed group-to-feature lookup.
    render = directory / 'rail.sqlite'
    if render.exists():
        with closing(sqlite3.connect(render.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            for key in matched:
                for (raw,) in db.execute('SELECT f.data FROM rail_feature_groups g JOIN features f '
                    'ON f.id=g.feature_id WHERE g.group_id=?', (key,)):
                    feature = json.loads(raw)
                    ident = object_key(feature.get('properties', {}))
                    if ident not in overrides:
                        continue
                    apply_names({'features': [feature]}, overrides)
                    add(ident, feature['properties'], overrides.get(ident, {}))
    for key, edit in overrides.items():
        if key.startswith('object:'):
            add(key, {}, edit)
    return changes, matched, dict(previous_roles)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--settings', type=Path, default=ROOT / 'data/user_settings/rail_catalog.json')
    parser.add_argument('--report', type=Path, default=ROOT / 'data/user_settings/connecting-line-classification.json')
    args = parser.parse_args()
    directory = args.directory or active_rail_directory(ROOT)
    snapshot = directory.name
    catalog = RailCatalogIndex(directory / 'rail_catalog.sqlite')
    overrides = EffectiveOverrides(read_overrides(args.settings))
    changes, matched, previous = collect_changes(catalog, overrides, directory, snapshot)
    catalog.close()
    report = {'version': NAME_RULE_VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
              'snapshot': snapshot, 'matched_catalog_objects': len(matched),
              'previous_roles': previous, 'changed_owners': len(changes), 'applied': False}
    if args.apply and changes:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        from desktop.rail_catalog_ui import RailCatalog

        class MapStub:
            def call(self, *args):
                pass

        class ClassificationCatalog(RailCatalog):
            def _replay_workspace_labels(self):
                # This command edits no names. Normal desktop crash recovery
                # can replay labels; this migration must visit changed owners only.
                pass

        app = QApplication.instance() or QApplication([])
        widget = ClassificationCatalog(directory, args.settings, MapStub())
        protected = {path: (path.stat().st_size, path.stat().st_mtime_ns)
                     for path in (directory / 'rail.sqlite', directory / 'rail_lines.sqlite') if path.exists()}
        with closing(sqlite3.connect(catalog.path)) as db:
            station_rows = db.execute('SELECT id,parent_id,kind,object_id FROM rail_station_nodes ORDER BY id').fetchall()
        def reject_rebuild(*args, **kwargs):
            raise AssertionError('Connecting-line correction entered a nationwide rebuild')
        original_prepare = widget._prepare_station_catalog
        widget.populate = widget._populate_paged_directory = widget._prepare_station_catalog = reject_rebuild
        started = time.perf_counter()
        widget.save_overrides({key: value for key, value in changes.items() if key in widget.catalog},
                              {key: value for key, value in changes.items() if key not in widget.catalog})
        report['save_seconds'] = round(time.perf_counter() - started, 3)
        failures = [key for key in matched if widget.meta(key)['line_role'] != 'connecting_line' or
                    widget.overrides.get(key, {}).get('color') != CONNECTING_LINE_COLOR]
        if failures:
            raise RuntimeError(f'{len(failures)} connecting-line owners failed verification')
        with closing(sqlite3.connect(catalog.path)) as db:
            assert station_rows == db.execute('SELECT id,parent_id,kind,object_id FROM rail_station_nodes ORDER BY id').fetchall()
        assert all((path.stat().st_size, path.stat().st_mtime_ns) == stamp for path, stamp in protected.items())
        assert original_prepare() is False
        report.update(applied=True, verified_catalog_objects=len(matched), source_unchanged=True,
                      station_membership_unchanged=True)
        widget.catalog.close()
        widget.close()
        app.processEvents()
    if args.apply:
        from desktop.rail_style_ui import load_styles, validate_styles
        from desktop.rail_style_resolver import STYLE_SELECTIONS, CONFIGURED_STYLE_KEYS
        style_path = args.settings.with_name('rail_styles.json')
        styles = load_styles(style_path)
        configured = styles[CONFIGURED_STYLE_KEYS]
        for key, selection in STYLE_SELECTIONS.items():
            if selection[0] == 'track' and selection[2] == 'connecting_line':
                styles[key]['color'] = CONNECTING_LINE_COLOR
                if key not in configured:
                    configured.append(key)
        for key in ('联络线 / 匝道', 'line.connecting_line'):
            styles[key]['color'] = CONNECTING_LINE_COLOR
        styles = validate_styles(styles)
        temporary = style_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(styles, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(style_path)
        report.update(applied=True, connecting_line_color=CONNECTING_LINE_COLOR)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
