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


def edit_database(path):
    return Path(path).with_suffix('.edits.sqlite')


def override_stamp(path):
    """Both legacy seed and incremental workspace participate in invalidation."""
    return tuple((p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None
                 for p in (Path(path), edit_database(path)))


def _read_seed(path):
    path = Path(path)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(payload, dict) and payload.get('schema') == 'railscope.catalog-exchange.v1':
        payload = payload.get('overrides')
    return validate_overrides(payload, path)


def _read_entries(path):
    database = edit_database(path)
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
    values = _read_seed(path)
    for key, value in _read_entries(path).items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
    return values


def validate_overrides(payload, path):
    if not isinstance(payload, dict):
        raise ValueError(f'铁路目录文件格式无效：{path}')
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise ValueError(f'铁路目录对象格式无效：{path}')
        folder = value.get('folder_path')
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


class CatalogWorkspace:
    def __init__(self, path):
        self.path = Path(path)
        self.values = {}
        self.undo_stack = []
        self.redo_stack = []
        self.load_failed = False
        self.dirty = False
        self.edited_keys = set()

    def load(self, seeds=()):
        proposed = {}
        self.load_failed = True
        for seed in (*seeds, self.path):
            for key, value in _read_seed(seed).items():
                proposed[key] = {**proposed.get(key, {}), **value}
        entries = _read_entries(self.path)
        for key, value in entries.items():
            if value is None:
                proposed.pop(key, None)
            else:
                proposed[key] = value
        self.values.clear()
        self.values.update(proposed)
        self.edited_keys = set(entries)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.load_failed = False
        self.dirty = False

    def _write_entries(self, entries):
        database = edit_database(self.path)
        database.parent.mkdir(parents=True, exist_ok=True)
        # A command commits only the changed owners; no geometry or seed rewrite.
        try:
            with closing(sqlite3.connect(database, timeout=1)) as db, db:
                db.execute('CREATE TABLE IF NOT EXISTS overrides(entity_id TEXT PRIMARY KEY,data TEXT)')
                db.execute('PRAGMA user_version=1')
                db.executemany('INSERT OR REPLACE INTO overrides VALUES(?,?)',
                    ((key, json.dumps(value, ensure_ascii=False, allow_nan=False,
                        separators=(',', ':')) if value is not None else None)
                     for key, value in entries.items()))
        except sqlite3.Error as error:
            raise OSError(f'工作区增量保存失败：{error}') from error

    def _commit(self, entries):
        if self.load_failed:
            raise ValueError('工作区未成功载入；请先修复文件并重新载入，原文件保留')
        validate_overrides({key: value for key, value in entries.items() if value is not None}, self.path)
        self._write_entries(entries)
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
        before = {key: deepcopy(self.values.get(key)) for key in changes}
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
        old = {key: deepcopy(self.values.get(key, {})) for key in command}
        reverse = {key: deepcopy(self.values.get(key)) for key in command}
        proposed = {}
        for key, value in command.items():
            if value is None:
                proposed[key] = None
                # Older corridors may still reference an undone line assembly.
                if key.startswith('line-assembly:') and old.get(key):
                    proposed[key] = {**old[key], 'active': False}
            else:
                proposed[key] = deepcopy(value)
        changed = {key for key in command if old.get(key) != proposed.get(key)}
        self._commit(proposed)
        source.pop()
        target.append(reverse)
        return old, changed

    def undo(self):
        return self._restore(self.undo_stack, self.redo_stack)

    def redo(self):
        return self._restore(self.redo_stack, self.undo_stack)
