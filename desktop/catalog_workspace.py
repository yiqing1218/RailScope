"""Authoritative catalog edits and command history, independent of Qt views.

Exchange files are seeds, not a second writable project. A local value wins
over its seed on every load. Persistence succeeds before state/history changes.
"""
from copy import deepcopy
from contextlib import closing
import json
import math
import re
from pathlib import Path
import sqlite3
try:
    from . import workspace_sqlite as layered
except ImportError:
    import workspace_sqlite as layered


def edit_database(path):
    return layered.database_path(path)


def override_stamp(path):
    """Both legacy seed and incremental workspace participate in invalidation."""
    return tuple((p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None
                 for p in (Path(path), edit_database(path)))


def routing_revision(path):
    """Only routing dependencies invalidate a live line library."""
    database = edit_database(path)
    if not database.exists():
        return override_stamp(path)
    revisions = layered.revisions(database)
    return (layered.identity(database), *[revisions[kind] for kind in ('source','geometry','topology','semantic')])


def _read_seed(path):
    path = Path(path)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(payload, dict) and payload.get('schema') == 'railscope.catalog-exchange.v1':
        payload = payload.get('overrides')
    return validate_overrides(payload, path)


def _read_legacy_entries(path):
    database = Path(path).with_suffix('.edits.sqlite')
    if not database.exists():
        return {}
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise ValueError(f'工作区增量数据库版本无效：{database}')
            entries = {key: json.loads(raw) if raw is not None else None
                       for key, raw in db.execute('SELECT entity_id,data FROM overrides')}
    except sqlite3.Error as error:
        raise ValueError(f'工作区增量数据库未载入：{database} / {error}') from error
    validate_overrides({key: value for key, value in entries.items() if value is not None}, database)
    return entries


def read_overrides(path):
    if edit_database(path).exists():
        try:
            values, _, _ = layered.read_database(edit_database(path))
            return validate_overrides({key: value for key, value in values.items() if value is not None}, path)
        except sqlite3.Error as error:
            raise ValueError(f'工作区增量数据库未载入：{edit_database(path)} / {error}') from error
    values = _read_seed(path)
    for key, value in _read_legacy_entries(path).items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
    return values


def validate_overrides(payload, path):
    try:
        from .station_classification import TECHNICAL_TYPES, BUSINESS_TYPES
    except ImportError:
        from station_classification import TECHNICAL_TYPES, BUSINESS_TYPES
    if not isinstance(payload, dict):
        raise ValueError(f'铁路目录文件格式无效：{path}')
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise ValueError(f'铁路目录对象格式无效：{path}')
        folder = value.get('folder_path')
        if (value.get('technical_type', '待核实') not in TECHNICAL_TYPES or
                value.get('business_type', '待核实') not in BUSINESS_TYPES):
            raise ValueError(f'车站分类无效：{key}')
        color, width = value.get('color'), value.get('width')
        if color is not None and (not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color)):
            raise ValueError(f'线路颜色必须为 #RRGGBB：{key}')
        if width is not None and (isinstance(width, bool) or not isinstance(width, (int, float)) or not math.isfinite(width) or width <= 0):
            raise ValueError(f'线路线宽必须为有限正数：{key}')
        if (type(value.get('archived', False)) is not bool or
            folder is not None and (not isinstance(folder, list) or not folder or
                any(not isinstance(part, str) or not part.strip() for part in folder))):
            raise ValueError(f'铁路目录属性格式无效：{path} / {key}')
    return payload


def station_assignment_changes(facilities, tracks, station_id, station_name):
    """Prepare one mixed assignment, leaving persistence and Qt to the caller."""
    owner = {'station_id': station_id or '',
             'station_assignment': 'manual' if station_id else 'pending'}
    folder = ['车站设施', station_name or '待核对']
    return ({key: {**owner, 'directory_view': 'facilities', 'folder_path': list(folder)}
             for key in facilities}, {key: dict(owner) for key in tracks})


def _snapshot(value):
    # Values are replaced on every command. Share immutable membership arrays,
    # never duplicate all M physical members for a one-field logical edit.
    if isinstance(value, dict) and 'members' in value:
        return {key: val if key == 'members' else deepcopy(val) for key, val in value.items()}
    return deepcopy(value)


def _history_delta(before, after):
    if before is None or after is None:
        return {'before': before, 'after': after}
    fields = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    return {'before': {key: before[key] for key in fields if key in before},
            'after': {key: after[key] for key in fields if key in after},
            'removed': sorted(fields - after.keys()), 'added': sorted(fields - before.keys())}


class CatalogWorkspace:
    def __init__(self, path):
        self.path = Path(path)
        self.values = {}
        self.undo_stack = []
        self.redo_stack = []
        self.load_failed = False
        self.dirty = False
        self.edited_keys = set()
        self.revisions = {name: 0 for name in layered.REVISION_NAMES}
        self.last_change = None
        self.identity = 'unloaded:' + str(self.path.resolve())

    def load(self, seeds=()):
        proposed = {}
        self.load_failed = True
        if edit_database(self.path).exists():
            try:
                proposed, self.revisions, self.edited_keys = layered.read_database(edit_database(self.path))
            except sqlite3.Error as error:
                raise ValueError(f'工作区增量数据库未载入：{error}') from error
            validate_overrides({key: value for key, value in proposed.items() if value is not None}, self.path)
            proposed = {key: value for key, value in proposed.items() if value is not None}
        else:
            for seed in (*seeds, self.path):
                for key, value in _read_seed(seed).items():
                    proposed[key] = {**proposed.get(key, {}), **value}
            entries = _read_legacy_entries(self.path)
            for key, value in entries.items():
                if value is None:
                    proposed.pop(key, None)
                else:
                    proposed[key] = value
            layered.migrate(edit_database(self.path), proposed,
                            [key for key, value in entries.items() if value is None], entries,
                            (*seeds, self.path, self.path.with_suffix('.edits.sqlite')))
            self.edited_keys = set(entries)
        self.values.clear()
        self.values.update(proposed)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.identity = layered.identity(edit_database(self.path))
        self.load_failed = False
        self.dirty = False

    def _write_entries(self, entries):
        database = edit_database(self.path)
        database.parent.mkdir(parents=True, exist_ok=True)
        # A command commits only the changed owners; no geometry or seed rewrite.
        try:
            with closing(sqlite3.connect(database, timeout=1)) as db, db:
                kinds = set()
                delta = {}
                for key, value in entries.items():
                    before = self.values.get(key)
                    layered.write_owner(db, key, value, before)
                    kinds.update(layered.change_types(before, value))
                    delta[key] = _history_delta(before, value)
                for kind in kinds:
                    db.execute('UPDATE revisions SET value=value+1 WHERE kind=?', (kind,))
                db.execute('INSERT INTO command_history(created_at,kinds,delta) VALUES(datetime(\'now\'),?,?)',
                           (layered.encoded(sorted(kinds)), layered.encoded(delta)))
                # Edited IDs are rows, never an O(all edits) JSON rewrite per command.
                db.executemany("UPDATE object_aliases SET status='edited' WHERE entity_id=?", ((key,) for key in entries))
        except sqlite3.Error as error:
            raise OSError(f'工作区增量保存失败：{error}') from error

    def cached_values(self):
        """Reuse loaded owner values only while the authoritative revision agrees.

        Query adapters must treat this mapping as read-only. An external writer
        or replaced workspace forces the ordinary validated disk load.
        """
        database = edit_database(self.path)
        if self.load_failed or not database.exists():
            return None
        if layered.identity(database) != self.identity or layered.revisions(database) != self.revisions:
            return None
        return self.values

    def label_edit_keys(self):
        """Recover name edits, without treating semantic/color edits as renames."""
        if self.load_failed or not edit_database(self.path).exists():
            return set()
        fields = {'display_name', 'line_name', 'assembly_name'}
        keys = set()
        with closing(sqlite3.connect(edit_database(self.path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
            # Legacy incremental entries have no journal. Retain their recovery
            # behavior, but exclude ordinary migration seeds and semantic-only IDs.
            legacy = db.execute("SELECT value FROM workspace_meta WHERE key='edited_keys'").fetchone()
            for key in json.loads(legacy[0]) if legacy else ():
                value = self.values.get(key)
                attrs = (value or {}).get('attributes', value or {})
                if value is None or fields & attrs.keys():
                    keys.add(key)
            # Large automatic classification commands can be hundreds of MB.
            # Their typed revision excludes them before any JSON is decoded.
            for (raw,) in db.execute("SELECT delta FROM command_history WHERE kinds LIKE '%\"presentation\"%'"):
                for key, delta in json.loads(raw).items():
                    before, after = delta.get('before') or {}, delta.get('after') or {}
                    if key.startswith('line-assembly:'):
                        before, after = before.get('attributes') or {}, after.get('attributes') or {}
                    if any(before.get(field) != after.get(field) for field in fields):
                        keys.add(key)
        return keys

    def _commit(self, entries):
        if self.load_failed:
            raise ValueError('工作区未成功载入；请先修复文件并重新载入，原文件保留')
        validate_overrides({key: value for key, value in entries.items() if value is not None}, self.path)
        self._write_entries(entries)
        self.revisions = layered.revisions(edit_database(self.path))
        self.last_change = {'ids': set(entries), 'types': set().union(*(
            layered.change_types(self.values.get(key), value) for key, value in entries.items())),
            'revisions': dict(self.revisions)}
        self.edited_keys.update(entries)
        for key, value in entries.items():
            if value is None:
                self.values.pop(key, None)
            else:
                self.values[key] = value
        self.dirty = True

    def update(self, changes):
        if not changes:
            return
        before = {key: _snapshot(self.values.get(key)) for key in changes}
        proposed = {}
        for key, change in changes.items():
            if not isinstance(key, str) or not isinstance(change, dict):
                raise ValueError('目录批量修改内容无效')
            value = {**self.values.get(key, {}), **deepcopy(change)}
            if value != self.values.get(key):
                proposed[key] = value
        if not proposed:
            return
        before = {key: before[key] for key in proposed}
        self._commit(proposed)
        self.undo_stack.append(before)
        del self.undo_stack[:-30]
        self.redo_stack.clear()

    def _restore(self, source, target):
        if not source:
            return None
        command = source[-1]
        old = {key: _snapshot(self.values.get(key, {})) for key in command}
        reverse = {key: _snapshot(self.values.get(key)) for key in command}
        proposed = {}
        for key, value in command.items():
            if value is None:
                proposed[key] = None
                # Older corridors may still reference an undone line assembly.
                if key.startswith('line-assembly:') and old.get(key):
                    proposed[key] = {**old[key], 'active': False}
            else:
                proposed[key] = _snapshot(value)
        changed = {key for key in command if old.get(key) != proposed.get(key)}
        self._commit(proposed)
        source.pop()
        target.append(reverse)
        return old, changed

    def undo(self):
        return self._restore(self.undo_stack, self.redo_stack)

    def redo(self):
        return self._restore(self.redo_stack, self.undo_stack)
