"""Normalized user workspace adapter; source/domain IDs are never regenerated."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

SCHEMA = 2
REVISION_NAMES = ('source', 'geometry', 'topology', 'semantic', 'directory',
                  'presentation', 'assignment', 'operation', 'attributes')
DIRECTORY_FIELDS = frozenset(('folder_path', 'directory_view', 'archived'))
PRESENTATION_FIELDS = frozenset(('display_name', 'line_name', 'assembly_name', 'color', 'width'))
ASSIGNMENT_FIELDS = frozenset(('station_id', 'station_source', 'station_assignment'))


def database_path(path):
    path = Path(path)
    return path.with_name('workspace.sqlite') if path.name == 'rail_catalog.json' else path.with_suffix('.workspace.sqlite')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def change_types(before, after):
    before, after = before or {}, after or {}
    fields = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    if 'attributes' in fields:
        old, new = before.get('attributes') or {}, after.get('attributes') or {}
        fields.discard('attributes')
        fields |= {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
    kinds = set()
    if fields & DIRECTORY_FIELDS:
        kinds.add('directory')
    if fields & PRESENTATION_FIELDS or 'archived' in fields:
        kinds.add('presentation')
    if fields & ASSIGNMENT_FIELDS:
        kinds.add('assignment')
    if fields & {'rail_semantics', 'track_type', 'line_kind', 'station_type'}:
        kinds.add('semantic')
    if fields & {'connected_lines', 'connected_line_ids', 'assembly_id', 'members', 'active'}:
        kinds.add('topology')
    if fields & {'geometry'}:
        kinds.add('geometry')
    if fields & {'corridor', 'train_run', 'station_route'}:
        kinds.add('operation')
    known = DIRECTORY_FIELDS | PRESENTATION_FIELDS | ASSIGNMENT_FIELDS | {
        'rail_semantics', 'track_type', 'line_kind', 'station_type', 'connected_lines',
        'connected_line_ids', 'assembly_id', 'members', 'active', 'geometry',
        'corridor', 'train_run', 'station_route'}
    if fields - known:
        kinds.add('attributes')
    return kinds


def create_schema(db):
    db.executescript('''
      CREATE TABLE workspace_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE revisions(kind TEXT PRIMARY KEY,value INTEGER NOT NULL);
      CREATE TABLE object_overrides(entity_id TEXT PRIMARY KEY,data TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE object_aliases(entity_id TEXT PRIMARY KEY,domain_id TEXT,source_snapshot TEXT,status TEXT NOT NULL DEFAULT 'adapter_alias');
      CREATE TABLE directory_folders(id TEXT PRIMARY KEY,parent_id TEXT NOT NULL,view TEXT NOT NULL,name TEXT NOT NULL,UNIQUE(parent_id,view,name));
      CREATE TABLE directory_membership(entity_id TEXT PRIMARY KEY,folder_id TEXT,data TEXT NOT NULL);
      CREATE INDEX directory_folder_members ON directory_membership(folder_id,entity_id);
      CREATE TABLE presentation_overrides(entity_id TEXT PRIMARY KEY,data TEXT NOT NULL);
      CREATE TABLE station_assignments(entity_id TEXT PRIMARY KEY,station_id TEXT,data TEXT NOT NULL);
      CREATE INDEX assignments_by_station ON station_assignments(station_id,entity_id);
      CREATE TABLE line_assemblies(assembly_id TEXT PRIMARY KEY,entity_id TEXT UNIQUE,data TEXT NOT NULL,shared TEXT NOT NULL,attributes_present INTEGER NOT NULL,members_present INTEGER NOT NULL);
      CREATE TABLE assembly_members(assembly_id TEXT NOT NULL,member_id TEXT NOT NULL,position INTEGER NOT NULL,PRIMARY KEY(assembly_id,member_id));
      CREATE INDEX member_assembly ON assembly_members(member_id,assembly_id);
      CREATE TABLE command_history(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,kinds TEXT NOT NULL,delta TEXT NOT NULL);
      CREATE TABLE migration_conflicts(entity_id TEXT NOT NULL,reason TEXT NOT NULL,data TEXT NOT NULL);
    ''')
    db.execute(f'PRAGMA user_version={SCHEMA}')
    db.executemany('INSERT INTO revisions VALUES(?,0)', ((name,) for name in REVISION_NAMES))


def _parts(key, value):
    if value is None:
        return None
    marker = key.startswith('line-assembly:')
    attrs = value.get('attributes', {}) if marker else value
    directory = {field: attrs[field] for field in DIRECTORY_FIELDS if field in attrs}
    presentation = {field: attrs[field] for field in PRESENTATION_FIELDS if field in attrs}
    assignment = {field: attrs[field] for field in ASSIGNMENT_FIELDS if field in attrs}
    rest = {field: val for field, val in attrs.items()
            if field not in DIRECTORY_FIELDS | PRESENTATION_FIELDS | ASSIGNMENT_FIELDS}
    assembly = None
    if marker:
        assembly = ({field: val for field, val in value.items() if field not in ('attributes', 'members')},
                    rest, 'attributes' in value, 'members' in value)
        rest = {}
    return rest, directory, presentation, assignment, assembly, value.get('members', []) if marker else None


def _folder(db, directory):
    parent = ''
    view = directory.get('directory_view') or 'default'
    for label in directory.get('folder_path') or []:
        row = db.execute('SELECT id FROM directory_folders WHERE parent_id=? AND view=? AND name=?',
                         (parent, view, label)).fetchone()
        if row:
            parent = row[0]
        else:
            ident = 'FOLDER-' + uuid4().hex
            db.execute('INSERT INTO directory_folders VALUES(?,?,?,?)', (ident, parent, view, label))
            parent = ident
    return parent or None


def write_owner(db, key, value, before=None):
    new, old = _parts(key, value), _parts(key, before)
    if new is None:
        db.execute("INSERT OR REPLACE INTO object_overrides VALUES(?,'{}',1)", (key,))
        db.execute('INSERT OR IGNORE INTO object_aliases(entity_id) VALUES(?)', (key,))
        for table in ('directory_membership', 'presentation_overrides', 'station_assignments'):
            db.execute(f'DELETE FROM {table} WHERE entity_id=?', (key,))
        if key.startswith('line-assembly:'):
            assembly = key.removeprefix('line-assembly:')
            db.execute('DELETE FROM assembly_members WHERE assembly_id=?', (assembly,))
            db.execute('DELETE FROM line_assemblies WHERE assembly_id=?', (assembly,))
        return
    if old is None or old[0] != new[0]:
        db.execute('INSERT OR REPLACE INTO object_overrides VALUES(?,?,0)', (key, encoded(new[0])))
    # Field ownership matters: a folder-only command writes no property/assignment rows.
    for index, table in ((1, 'directory_membership'), (2, 'presentation_overrides'), (3, 'station_assignments')):
        if old is not None and old[index] == new[index]:
            continue
        fields = new[index]
        if not fields:
            db.execute(f'DELETE FROM {table} WHERE entity_id=?', (key,))
        elif index == 1:
            folder = _folder(db, fields)
            stored = {field: val for field, val in fields.items() if field != 'folder_path'}
            stored['has_folder'] = 'folder_path' in fields
            db.execute('INSERT OR REPLACE INTO directory_membership VALUES(?,?,?)', (key, folder, encoded(stored)))
        elif index == 3:
            db.execute('INSERT OR REPLACE INTO station_assignments VALUES(?,?,?)',
                       (key, fields.get('station_id'), encoded(fields)))
        else:
            db.execute(f'INSERT OR REPLACE INTO {table} VALUES(?,?)', (key, encoded(fields)))
    if new[4] is not None:
        assembly = key.removeprefix('line-assembly:')
        if old is None or old[4] != new[4]:
            meta, shared, attrs_present, members_present = new[4]
            db.execute('INSERT OR REPLACE INTO line_assemblies VALUES(?,?,?,?,?,?)',
                       (assembly, key, encoded(meta), encoded(shared), int(attrs_present), int(members_present)))
        if old is None or old[5] != new[5]:
            db.execute('DELETE FROM assembly_members WHERE assembly_id=?', (assembly,))
            db.executemany('INSERT INTO assembly_members VALUES(?,?,?)',
                           ((assembly, member, position) for position, member in enumerate(new[5])))
    db.execute('INSERT OR IGNORE INTO object_aliases(entity_id) VALUES(?)', (key,))


def read_database(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA:
            raise ValueError('工作区数据库版本无效')
        values = {key: None if deleted else json.loads(raw) for key, raw, deleted in
                  db.execute('SELECT entity_id,data,deleted FROM object_overrides')}
        markers = {}
        for assembly, key, raw, shared, attrs_present, members_present in db.execute('SELECT * FROM line_assemblies'):
            value = json.loads(raw)
            if attrs_present:
                value['attributes'] = json.loads(shared)
            if members_present:
                value['members'] = [row[0] for row in db.execute(
                    'SELECT member_id FROM assembly_members WHERE assembly_id=? ORDER BY position', (assembly,))]
            values[key] = value
            markers[key] = attrs_present
        # Resolve parent paths once at load; normal commands use indexed folder lookups.
        folders = {ident: (parent, label) for ident, parent, label in db.execute('SELECT id,parent_id,name FROM directory_folders')}
        paths = {}
        def folder_path(ident):
            if not ident:
                return []
            if ident not in paths:
                parent, label = folders[ident]
                paths[ident] = folder_path(parent) + [label]
            return paths[ident]
        for table in ('directory_membership', 'presentation_overrides', 'station_assignments'):
            for key, raw in db.execute(f'SELECT entity_id,data FROM {table}'):
                attrs = json.loads(raw)
                if table == 'directory_membership':
                    if attrs.pop('has_folder', False):
                        ident = db.execute('SELECT folder_id FROM directory_membership WHERE entity_id=?', (key,)).fetchone()[0]
                        attrs['folder_path'] = list(folder_path(ident))
                if values.get(key) is None:
                    raise ValueError('工作区字段存在悬空对象引用')
                if key in markers:
                    values[key].setdefault('attributes', {}).update(attrs)
                else:
                    values[key].update(attrs)
        revisions = dict(db.execute('SELECT kind,value FROM revisions'))
        edited = json.loads(db.execute("SELECT value FROM workspace_meta WHERE key='edited_keys'").fetchone()[0])
        edited.extend(row[0] for row in db.execute("SELECT entity_id FROM object_aliases WHERE status='edited'"))
    return values, revisions, set(edited)


def migrate(path, values, tombstones=(), edited=(), backups=()):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    if backups:
        backup = path.parent / 'legacy-workspace-backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        for original in backups:
            original = Path(original)
            if not original.exists():
                continue
            backup.mkdir(parents=True, exist_ok=True)
            if original.suffix == '.sqlite':
                with closing(sqlite3.connect(original.resolve().as_uri() + '?mode=ro', uri=True)) as src, \
                        closing(sqlite3.connect(backup / original.name)) as dst:
                    src.backup(dst)
            else:
                shutil.copy2(original, backup / original.name)
    try:
        with closing(sqlite3.connect(temporary)) as db:
            create_schema(db)
            with db:
                for key, value in values.items():
                    write_owner(db, key, value)
                for key in tombstones:
                    write_owner(db, key, None)
                db.executemany('INSERT INTO workspace_meta VALUES(?,?)', (
                    ('created_at', datetime.now(timezone.utc).isoformat()), ('algorithm_version', 'normalized-workspace-v1'),
                    ('workspace_id', uuid4().hex),
                    ('migration_complete', '1'), ('edited_keys', encoded(list(edited)))))
        # The old workspace remains readable until the fully committed file is published.
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def revisions(path):
    path = Path(path)
    if not path.exists():
        return {name: 0 for name in REVISION_NAMES}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        return dict(db.execute('SELECT kind,value FROM revisions'))


def identity(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute("SELECT value FROM workspace_meta WHERE key='workspace_id'").fetchone()
    return row[0] if row else str(Path(path).resolve())
