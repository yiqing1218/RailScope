"""Paged presentation of stations and their railway facilities.

Ownership in this cache is only a directory placement. Inferred name matches
remain marked for review and never change infrastructure or operating IDs.
"""

from contextlib import closing
from collections import Counter
import hashlib
import json
import sqlite3

from PySide6.QtCore import QModelIndex, Qt

try:
    from .lazy_directory import SqliteDirectoryModel
    from .catalog_metadata import rail_station_records, station_directory_path
    from .rail_station_types import FACILITY_TYPES
    from .rail_facility_ownership import facility_track_owners
except ImportError:
    from lazy_directory import SqliteDirectoryModel
    from catalog_metadata import rail_station_records, station_directory_path
    from rail_station_types import FACILITY_TYPES
    from rail_facility_ownership import facility_track_owners


def _key(parts):
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def station_catalog_signature(directory, catalog_path, overrides):
    """Hash the station-catalog inputs so the caller can mark it fresh."""
    source = directory / "rail_lines.sqlite"
    if not source.exists():
        return None
    station_edits = {k: {field: v[field] for field in ("folder_path", "archived", "station_type") if field in v}
                     for k, v in overrides.items()
                     if k.startswith("station:") and any(field in v for field in ("folder_path", "archived", "station_type"))}
    facility_edits = {k: {field: v[field] for field in ("station_id", "station_assignment") if field in v}
                      for k, v in overrides.items() if "station_id" in v or "station_assignment" in v}
    segment_edits = {k: {field: v[field] for field in ("display_name", "line_name", "track_type", "station_id", "station_source", "station_assignment", "rail_semantics", "line_kind") if field in v}
                     for k, v in overrides.items() if k.startswith("object:") and
                     any(field in v for field in ("display_name", "line_name", "track_type", "station_id", "station_source", "station_assignment", "rail_semantics", "line_kind"))}
    with closing(sqlite3.connect(catalog_path)) as db:
        catalog_version = db.execute("SELECT value FROM metadata WHERE key='paged_directory_signature'").fetchone()
    geometry = directory/'rail.sqlite'
    return hashlib.sha256(_key([11, source.stat().st_mtime_ns, geometry.stat().st_mtime_ns if geometry.exists() else None,
                                 catalog_version[0] if catalog_version else "",
                                 station_edits, facility_edits, segment_edits]).encode()).hexdigest()


def sync_station_catalog(directory, catalog_path, regions, overrides, signature=None):
    """Build one disk-backed tree; do nothing when its inputs are unchanged."""
    signature = signature or station_catalog_signature(directory, catalog_path, overrides)
    if signature is None:
        return
    with closing(sqlite3.connect(catalog_path)) as db:
        old = db.execute("SELECT value FROM metadata WHERE key='station_catalog_signature'").fetchone()
        if old and old[0] == signature:
            return False
        stations, _ = rail_station_records(directory, regions, limit=100000, overrides=overrides)
        track_owners = facility_track_owners(directory,stations,overrides)
        fully_owned = set()
        counts = Counter(owner.get('catalog_id') for owner in track_owners.values())
        source_geometry = directory/'rail.sqlite'
        if counts and source_geometry.exists():
            with closing(sqlite3.connect(source_geometry)) as geometry_db:
                if geometry_db.execute("SELECT 1 FROM sqlite_master WHERE name='rail_feature_groups'").fetchone():
                    for group,count in counts.items():
                        if group:
                            total = geometry_db.execute('SELECT count(*) FROM rail_feature_groups WHERE group_id=?',(group,)).fetchone()[0]
                            if total and count==total:
                                fully_owned.add(group)
        db.execute("BEGIN")
        db.execute("CREATE TABLE IF NOT EXISTS rail_station_nodes (id TEXT PRIMARY KEY,parent_id TEXT NOT NULL,label TEXT NOT NULL,kind TEXT NOT NULL,object_id TEXT,path TEXT NOT NULL,child_count INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,searchable TEXT NOT NULL DEFAULT '',station_total INTEGER NOT NULL DEFAULT 0,facility_total INTEGER NOT NULL DEFAULT 0)")
        existing_columns = {row[1] for row in db.execute("PRAGMA table_info(rail_station_nodes)")}
        for column in ("station_total", "facility_total", "track_total"):
            if column not in existing_columns:
                db.execute(f"ALTER TABLE rail_station_nodes ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
        db.execute("CREATE INDEX IF NOT EXISTS rail_station_parent ON rail_station_nodes(parent_id,kind,label,id)")
        db.execute("CREATE INDEX IF NOT EXISTS rail_station_object ON rail_station_nodes(object_id,kind)")
        db.execute("CREATE TABLE IF NOT EXISTS rail_station_matches (id TEXT PRIMARY KEY)")
        db.execute("DELETE FROM rail_station_nodes")
        db.execute("DELETE FROM rail_station_matches")
        db.execute('CREATE TABLE IF NOT EXISTS rail_facility_track_owners(object_id TEXT PRIMARY KEY,station_id TEXT,data TEXT,catalog_id TEXT)')
        if 'catalog_id' not in {row[1] for row in db.execute('PRAGMA table_info(rail_facility_track_owners)')}:
            db.execute('ALTER TABLE rail_facility_track_owners ADD COLUMN catalog_id TEXT')
        db.execute('CREATE INDEX IF NOT EXISTS rail_facility_track_catalog ON rail_facility_track_owners(catalog_id)')
        db.execute('CREATE TABLE IF NOT EXISTS rail_facility_track_baseline(object_id TEXT PRIMARY KEY,station_id TEXT,data TEXT,catalog_id TEXT)')
        db.execute('CREATE INDEX IF NOT EXISTS rail_facility_baseline_catalog ON rail_facility_track_baseline(catalog_id)')
        db.execute('DELETE FROM rail_facility_track_owners')
        rows = []
        folders = set()
        totals = {}
        station_totals = {}
        facility_totals = {}
        track_totals = {}
        root = ["stations"]

        def add_folder(parent, ident, label, path):
            if ident not in folders:
                rows.append((ident, parent, label, "folder", None, _key(path), 0, label.casefold()))
                folders.add(ident)
            return ident

        station_by_id = {}
        name_owners = {}
        for record in stations:
            sid = record["id"]
            custom = overrides.get("station:" + sid, {})
            name = custom.get("display_name") or record["name"]
            folder = station_directory_path(record, custom)
            station_kind = custom.get('station_type') or record.get('station_type')
            if not custom.get('folder_path') and station_kind in FACILITY_TYPES:
                folder = (*folder, station_kind)
            archived = bool(custom.get("archived"))
            path = root + (["已归档"] if archived else []) + list(folder)
            parent = ""
            for depth in range(2, len(path) + 1):
                ident = "folder:" + _key(path[:depth])
                parent = add_folder(parent, ident, path[depth-1], path[:depth])
            ident = "station:" + sid
            rows.append((ident, parent, name, "station", sid, _key(path + [sid]), int(archived),
                         (name + " " + sid + " " + " ".join(folder)).casefold()))
            station_by_id[sid] = (ident, path + [sid], name)
            province = record.get("province")
            if province:
                key = (str(record["name"]).removesuffix("站").casefold(), province)
                name_owners.setdefault(key, set()).add(sid)
            for depth in range(2, len(path) + 1):
                key = "folder:" + _key(path[:depth])
                totals[key] = totals.get(key, 0) + 1
                station_totals[key] = station_totals.get(key, 0) + 1

        pending = add_folder("", "folder:" + _key(root + ["待核对"]), "待核对", root + ["待核对"])
        facility_parents = {}
        # The existing facilities view already resolves manual line names and
        # track roles. Reuse its members instead of reading raw OSM geometry.
        for catalog_id, label, raw, facility_path in db.execute(
            "SELECT m.catalog_id,d.label,c.data,d.path FROM rail_directory_nodes d "
            "JOIN rail_directory_members m ON m.node_id=d.id "
            "JOIN catalog c ON c.id=m.catalog_id "
            "WHERE d.view='facilities' AND d.kind='object' ORDER BY m.catalog_id"
        ):
            if catalog_id in fully_owned:
                continue  # All its real members are now under their exact owners.
            record = json.loads(raw)
            edited = overrides.get(catalog_id, {})
            owner_id = edited.get("station_id", record.get("station_id"))
            certainty = "已指定"
            if edited.get("station_assignment") == "pending":
                owner_id = None
            elif owner_id not in station_by_id:
                source_id = edited.get("station_source", record.get("station_source"))
                owner_id = source_id if source_id in station_by_id else None
                if owner_id is None:
                    province = record.get("provinces") or []
                    candidates = {sid for p in province for sid in name_owners.get(
                        (str(record.get("station_name") or "").removesuffix("站").casefold(), p), ())}
                    if len(candidates) == 1 and record.get("station_name"):
                        owner_id = next(iter(candidates))
                        certainty = "名称匹配，待核对"
            if owner_id:
                station_node, station_path, _ = station_by_id[owner_id]
                base_path = station_path + ["站内轨道"]
                base = add_folder(station_node, "facility-folder:" + _key(base_path), "站内轨道", base_path)
                path = base_path
                parent = base
                ancestors = [station_node, base]
                if certainty != "已指定":
                    path = base_path + ["名称匹配，待核对"]
                    parent = add_folder(base, "facility-folder:" + _key(path), path[-1], path)
                    ancestors.append(parent)
                ancestors += ["folder:" + _key(station_path[:depth]) for depth in range(2, len(station_path))]
            else:
                old_path = json.loads(facility_path)
                category = old_path[-1] if len(old_path) > 2 else "用途待核实"
                path = root + ["待核对", category]
                parent = add_folder(pending, "folder:" + _key(path), category, path)
                ancestors = [pending, parent]
            rows.append(("facility:" + catalog_id, parent, label, "facility", catalog_id,
                         _key(path + [catalog_id]), int(bool(record.get("archived"))),
                         (label + " " + catalog_id + " " + str(record.get("station_name") or "")).casefold()))
            facility_parents[catalog_id] = ("facility:" + catalog_id, path + [catalog_id], bool(record.get("archived")))
            for ancestor in ancestors:
                totals[ancestor] = totals.get(ancestor, 0) + 1
                facility_totals[ancestor] = facility_totals.get(ancestor, 0) + 1
        source = directory / "rail_lines.sqlite"
        with closing(sqlite3.connect(source)) as rail_db:
            if facility_parents and rail_db.execute("SELECT 1 FROM sqlite_master WHERE name='rail_feature_groups'").fetchone():
                rail_db.execute("CREATE TEMP TABLE selected_facility_groups(id TEXT PRIMARY KEY)")
                rail_db.executemany("INSERT INTO selected_facility_groups VALUES(?)",
                                    ((key,) for key in facility_parents))
                seen_segments = set()
                for group_id, feature_id, raw in rail_db.execute(
                    "SELECT g.group_id,f.id,f.data FROM selected_facility_groups s "
                    "JOIN rail_feature_groups g ON g.group_id=s.id "
                    "JOIN features f ON f.id=g.feature_id ORDER BY g.group_id,f.id"
                ):
                    parent_info = facility_parents.get(group_id)
                    if parent_info is None:
                        continue
                    props = json.loads(raw).get("properties", {})
                    edge_id = props.get("network_edge_id") or props.get("section_id")
                    if not edge_id or (group_id, edge_id) in seen_segments:
                        continue
                    seen_segments.add((group_id, edge_id))
                    if edge_id in track_owners:
                        continue  # Its exact source segment belongs to the real depot below.
                    object_key = "object:network_edge_id:" + str(edge_id) if props.get("network_edge_id") else "object:section_id:" + str(edge_id)
                    custom = overrides.get(object_key, {})
                    line_name = custom.get("line_name") if "line_name" in custom else props.get("line_name")
                    name = (line_name or custom.get("display_name") or props.get("display_name") or str(edge_id))
                    name = str(name).replace('（参考）', '').replace('(参考)', '').strip()
                    endpoints = "→".join(str(props.get(key) or "?") for key in ("from_name", "to_name"))
                    parent_id, parent_path, archived = parent_info
                    rows.append(("segment:" + str(feature_id), parent_id,
                                 name + " · " + endpoints, "segment", object_key,
                                 _key(parent_path + [str(feature_id)]), int(archived),
                                 (str(name) + " " + endpoints + " " + str(edge_id)).casefold()))
        for edge_id,owner in track_owners.items():
            if owner['station_id'] in station_by_id:
                station_node,station_path,station_name = station_by_id[owner['station_id']]
            else:
                station_node,station_path,station_name = pending,root+['待核对'],'待核对'
            base_path = station_path+['站内轨道']
            parent = add_folder(station_node,'facility-folder:'+_key(base_path),'站内轨道',base_path)
            props = owner['properties']
            custom = overrides.get(owner['object_key'],{})
            label = custom.get('line_name') or custom.get('display_name') or props.get('line_name') or props.get('display_name') or '未命名轨道'
            label = str(label).replace('（参考）','').replace('(参考)','').strip()
            category = custom.get('track_type') or props.get('track_type') or '用途待核实'
            path = base_path+[category]
            parent = add_folder(parent,'facility-folder:'+_key(path),category,path)
            ident = 'segment:'+str(owner['feature_id'])
            rows.append((ident,parent,label,'facility_track',owner['object_key'],_key(path+[str(edge_id)]),0,
                         (station_name+' '+label+' '+category+' '+str(edge_id)).casefold()))
            db.execute('INSERT INTO rail_facility_track_owners(object_id,station_id,data,catalog_id) VALUES(?,?,?,?)',
                (owner['object_key'],owner['station_id'],json.dumps({k:v for k,v in owner.items() if k!='properties'},ensure_ascii=False),owner.get('catalog_id')))
            db.execute('INSERT OR IGNORE INTO rail_facility_track_baseline VALUES(?,?,?,?)',
                (owner['object_key'],owner.get('source_station_id',owner['station_id']),json.dumps({k:v for k,v in owner.items() if k!='properties'},ensure_ascii=False),owner.get('catalog_id')))
            ancestors = [station_node,'facility-folder:'+_key(base_path),parent]
            ancestors += ['folder:'+_key(station_path[:depth]) for depth in range(2,len(station_path))]
            for ancestor in ancestors:
                totals[ancestor] = totals.get(ancestor,0)+1
                track_totals[ancestor] = track_totals.get(ancestor,0)+1
        db.executemany("INSERT INTO rail_station_nodes(id,parent_id,label,kind,object_id,path,archived,searchable) VALUES(?,?,?,?,?,?,?,?)", rows)
        db.executemany("UPDATE rail_station_nodes SET total=? WHERE id=?",
                       ((count, key) for key, count in totals.items()))
        db.executemany("UPDATE rail_station_nodes SET station_total=? WHERE id=?",
                       ((count, key) for key, count in station_totals.items()))
        db.executemany("UPDATE rail_station_nodes SET facility_total=? WHERE id=?",
                       ((count, key) for key, count in facility_totals.items()))
        db.executemany('UPDATE rail_station_nodes SET track_total=? WHERE id=?',((count,key) for key,count in track_totals.items()))
        db.execute("UPDATE rail_station_nodes SET child_count=(SELECT count(*) FROM rail_station_nodes c WHERE c.parent_id=rail_station_nodes.id)")
        db.execute("INSERT OR REPLACE INTO metadata VALUES('station_catalog_signature',?)", (signature,))
        db.commit()
        return True


def update_station_label(catalog_path, station_id, label):
    with closing(sqlite3.connect(catalog_path)) as db:
        row = db.execute("SELECT path FROM rail_station_nodes WHERE id=?",
                         ("station:" + station_id,)).fetchone()
        if row is None:
            return False
        path = json.loads(row[0])
        db.execute("UPDATE rail_station_nodes SET label=?,searchable=? WHERE id=?",
                   (label, (label + " " + station_id + " " + " ".join(path[1:-1])).casefold(),
                    "station:" + station_id))
        db.commit()
    return True


def ensure_ownership_baseline(catalog_path):
    """Adopt a legacy projection once at startup, without another polygon scan."""
    with closing(sqlite3.connect(catalog_path)) as db, db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='rail_facility_track_baseline'").fetchone():
            return
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='rail_facility_track_owners'").fetchone():
            return
        db.execute('CREATE TABLE rail_facility_track_baseline(object_id TEXT PRIMARY KEY,station_id TEXT,data TEXT,catalog_id TEXT)')
        db.execute('CREATE INDEX rail_facility_baseline_catalog ON rail_facility_track_baseline(catalog_id)')
        db.execute('INSERT INTO rail_facility_track_baseline SELECT * FROM rail_facility_track_owners')
        db.execute("INSERT OR REPLACE INTO metadata VALUES('ownership_baseline_origin','legacy_complete_projection;source_verification_required')")


def update_station_placement(catalog_path, record, custom):
    """Move one owner and its cached children; no nationwide ownership scan."""
    return bool(update_station_directory(catalog_path, [(record, custom)]))


def update_station_directory(catalog_path, records):
    """One transaction for K stations; descendants inherit their parent's place."""
    affected = set()
    with closing(sqlite3.connect(catalog_path)) as db, db:
        db.row_factory = sqlite3.Row
        for record, custom in records:
            affected.update(_update_station_directory(db, record, custom))
    return affected


def update_station_assignments(catalog_path, changes, overrides):
    """Reparent explicit assets using indexed cached owners; never infer geometry."""
    affected = set()
    with closing(sqlite3.connect(catalog_path)) as db, db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='rail_station_nodes'").fetchone():
            return affected

        def ancestors(parent):
            return [row[0] for row in db.execute('WITH RECURSIVE a(id,parent_id) AS ('
                'SELECT id,parent_id FROM rail_station_nodes WHERE id=? UNION ALL '
                'SELECT n.id,n.parent_id FROM rail_station_nodes n JOIN a ON n.id=a.parent_id) SELECT id FROM a', (parent,))]

        for key in changes:
            custom = overrides.get(key, {})
            is_track = key.startswith('object:')
            roots = db.execute('SELECT * FROM rail_station_nodes WHERE object_id=? AND kind IN (\'facility\',\'segment\',\'facility_track\')', (key,)).fetchall()
            if not is_track:
                owned = db.execute('SELECT object_id FROM rail_facility_track_owners WHERE catalog_id=?', (key,)).fetchall()
                for (object_id,) in owned:
                    roots += db.execute("SELECT * FROM rail_station_nodes WHERE object_id=? AND kind='facility_track'", (object_id,)).fetchall()
            for row in roots:
                object_id = row['object_id']
                owner = custom.get('station_id') or custom.get('station_source')
                if custom.get('station_assignment') == 'pending':
                    owner = None
                elif 'station_id' not in custom and 'station_source' not in custom:
                    if is_track or row['kind'] == 'facility_track':
                        baseline = db.execute('SELECT station_id FROM rail_facility_track_baseline WHERE object_id=?', (object_id,)).fetchone()
                        if not baseline:
                            baseline = db.execute('SELECT station_id FROM rail_facility_track_owners WHERE object_id=?', (object_id,)).fetchone()
                        owner = baseline[0] if baseline else None
                    else:
                        raw = db.execute('SELECT data FROM catalog WHERE id=?', (key,)).fetchone()
                        source = json.loads(raw[0]) if raw else {}
                        owner = source.get('station_id') or source.get('station_source')
                station = db.execute("SELECT id,path FROM rail_station_nodes WHERE id=?", ('station:' + str(owner),)).fetchone() if owner else None
                if station:
                    parent, path = station['id'], json.loads(station['path'])
                    path += ['站内轨道']
                    ident = 'facility-folder:' + _key(path)
                else:
                    parent, path = '', ['stations', '待核对']
                    ident = 'folder:' + _key(path)
                db.execute("INSERT OR IGNORE INTO rail_station_nodes(id,parent_id,label,kind,path,searchable) VALUES(?,?,?,'folder',?,?)",
                    (ident, parent, path[-1], _key(path), path[-1].casefold()))
                old_ancestors, new_ancestors = set(ancestors(row['parent_id'])), set(ancestors(ident))
                counts = [row['station_total'], max(row['facility_total'], int(row['kind']=='facility')),
                          max(row['track_total'], int(row['kind']=='facility_track'))]
                total = max(row['total'], sum(counts))
                db.execute('UPDATE rail_station_nodes SET parent_id=?,path=? WHERE id=?', (ident, _key(path+[object_id]), row['id']))
                if row['kind'] == 'facility_track':
                    db.execute('UPDATE rail_facility_track_owners SET station_id=? WHERE object_id=?', (owner or '', object_id))
                for sign, keys in ((-1, old_ancestors-new_ancestors), (1, new_ancestors-old_ancestors)):
                    for ancestor in keys:
                        db.execute('UPDATE rail_station_nodes SET total=max(0,total+?),station_total=max(0,station_total+?),facility_total=max(0,facility_total+?),track_total=max(0,track_total+?) WHERE id=?',
                            (sign*total, *[sign*n for n in counts], ancestor))
                affected.update(old_ancestors | new_ancestors | {row['id'], ''})
        # Leaves before parents. Keep real stations even when their assets leave.
        for ident in sorted(affected, key=len, reverse=True):
            db.execute('UPDATE rail_station_nodes SET child_count=(SELECT count(*) FROM rail_station_nodes WHERE parent_id=?) WHERE id=?', (ident, ident))
            db.execute("DELETE FROM rail_station_nodes WHERE id=? AND kind='folder' AND child_count=0", (ident,))
    return affected


def _update_station_directory(db, record, custom):
    ident = 'station:' + record['id']
    folder = station_directory_path(record, custom)
    kind = custom.get('station_type') or record.get('station_type')
    if not custom.get('folder_path') and kind in FACILITY_TYPES:
        folder = (*folder, kind)
    new_path = ['stations'] + (['已归档'] if custom.get('archived') else []) + list(folder) + [record['id']]
    row = db.execute('SELECT * FROM rail_station_nodes WHERE id=?', (ident,)).fetchone()
    if row is None:
        return set()
    old_path = json.loads(row['path'])
    totals = {column: row[column] for column in ('total', 'station_total', 'facility_total', 'track_total')}
    totals['station_total'] = max(1, totals['station_total'])
    totals['total'] = max(totals['total'], sum(totals[column] for column in ('station_total', 'facility_total', 'track_total')))
    old_folders = ['folder:' + _key(old_path[:depth]) for depth in range(2, len(old_path))]
    new_folders = ['folder:' + _key(new_path[:depth]) for depth in range(2, len(new_path))]
    parent = ''
    for depth, key in zip(range(2, len(new_path)), new_folders):
        db.execute("INSERT OR IGNORE INTO rail_station_nodes(id,parent_id,label,kind,path,archived,searchable) VALUES(?,?,?,'folder',?,0,?)",
            (key, parent, new_path[depth-1], _key(new_path[:depth]), new_path[depth-1].casefold()))
        parent = key
    if old_path != new_path:
        # parent_id is authoritative. Do not duplicate a changed ancestor path
        # into every facility/track child, or rename their stable cache IDs.
        db.execute('UPDATE rail_station_nodes SET parent_id=?,path=? WHERE id=?', (parent, _key(new_path), ident))
        for sign, keys in ((-1, set(old_folders) - set(new_folders)), (1, set(new_folders) - set(old_folders))):
            for key in keys:
                db.execute('UPDATE rail_station_nodes SET ' + ','.join(f'{column}={column}+?' for column in totals) + ' WHERE id=?',
                           (*[sign * value for value in totals.values()], key))
    label = custom.get('display_name') or record['name']
    db.execute('UPDATE rail_station_nodes SET label=?,archived=?,searchable=? WHERE id=?',
        (label, int(bool(custom.get('archived'))), (label+' '+record['id']+' '+' '.join(folder)).casefold(), ident))
    for key in reversed(old_folders):
        if not db.execute('SELECT 1 FROM rail_station_nodes WHERE parent_id=? LIMIT 1', (key,)).fetchone():
            db.execute('DELETE FROM rail_station_nodes WHERE id=?', (key,))
    for key in set(old_folders) | set(new_folders):
        db.execute('UPDATE rail_station_nodes SET child_count=(SELECT count(*) FROM rail_station_nodes WHERE parent_id=?) WHERE id=?', (key, key))
    return set(old_folders) | set(new_folders) | {ident, ''}


class StationCatalogModel(SqliteDirectoryModel):
    """The station tree only instantiates the currently expanded 128-row page."""

    def __init__(self, path, parent=None):
        super().__init__(path, "rail_station_nodes", parent)
        self.station_master = False
        self.station_direct = set()
        self.station_excluded = set()
        self.facility_master = False
        self.facility_direct = set()
        self.facility_excluded = set()
        self.track_direct = set()
        self.track_excluded = set()
        self._folder_totals = {}
        self._direct_counts = {}
        self._excluded_counts = {}

    def _membership_counts(self, station_ids, facility_ids, track_ids=()):
        keys = ["station:" + str(value) for value in station_ids]
        keys.extend("facility:" + str(value) for value in facility_ids)
        if not keys and not track_ids:
            return {}
        with self._connect() as db:
            db.execute("CREATE TEMP TABLE selected_station_nodes(id TEXT PRIMARY KEY)")
            db.executemany("INSERT OR IGNORE INTO selected_station_nodes VALUES(?)", ((key,) for key in keys))
            db.executemany("INSERT OR IGNORE INTO selected_station_nodes SELECT id FROM rail_station_nodes WHERE kind='facility_track' AND object_id=?",((key,) for key in track_ids))
            rows = db.execute(
                "WITH RECURSIVE ancestors(id,parent_id,kind) AS ("
                "SELECT n.id,n.parent_id,n.kind FROM rail_station_nodes n "
                "JOIN selected_station_nodes s ON s.id=n.id "
                "UNION ALL SELECT p.id,p.parent_id,a.kind FROM rail_station_nodes p "
                "JOIN ancestors a ON p.id=a.parent_id WHERE a.parent_id<>''"
                ") SELECT id,kind,count(*) FROM ancestors GROUP BY id,kind"
            ).fetchall()
        return {(key, kind): count for key, kind, count in rows}

    def capture_membership(self, object_ids):
        captured = {}
        with self._connect() as db:
            for object_id in object_ids:
                for (key,) in db.execute('SELECT id FROM rail_station_nodes WHERE object_id=?', (object_id,)):
                    chain = [row[0] for row in db.execute('WITH RECURSIVE a(id,parent_id) AS ('
                        'SELECT id,parent_id FROM rail_station_nodes WHERE id=? UNION ALL '
                        'SELECT n.id,n.parent_id FROM rail_station_nodes n JOIN a ON n.id=a.parent_id) SELECT id FROM a', (key,))]
                    weights = [{kind: counts.get((key, kind), 0) for kind in ('station','facility','facility_track')}
                               for counts in (self._direct_counts, self._excluded_counts)]
                    captured[key] = (set(chain), weights)
        return captured

    def reconcile_membership(self, captured):
        with self._connect() as db:
            for key, (before, weights) in captured.items():
                after = {row[0] for row in db.execute('WITH RECURSIVE a(id,parent_id) AS ('
                    'SELECT id,parent_id FROM rail_station_nodes WHERE id=? UNION ALL '
                    'SELECT n.id,n.parent_id FROM rail_station_nodes n JOIN a ON n.id=a.parent_id) SELECT id FROM a', (key,))}
                for counts, weight in zip((self._direct_counts, self._excluded_counts), weights):
                    for sign, ancestors in ((-1, before-after), (1, after-before)):
                        for ancestor in ancestors:
                            for kind, number in weight.items():
                                counts[(ancestor, kind)] = max(0, counts.get((ancestor, kind), 0)+sign*number)

    def _totals(self, key):
        if key not in self._folder_totals:
            with self._connect() as db:
                self._folder_totals[key] = db.execute(
                    "SELECT station_total,facility_total,track_total FROM rail_station_nodes WHERE id=?", (key,)
                ).fetchone() or (0, 0, 0)
        return self._folder_totals[key]

    def _search_clause(self):
        if not self.search:
            return ""
        return " AND id IN (SELECT id FROM rail_station_matches)"

    def _search_args(self):
        return ()

    def set_search(self, query):
        query = query.strip().casefold()
        if query == self.search:
            return
        if query:
            with self._connect() as db:
                db.execute("CREATE TABLE IF NOT EXISTS rail_station_matches (id TEXT PRIMARY KEY)")
                db.execute("DELETE FROM rail_station_matches")
                db.execute(
                    "WITH RECURSIVE matches(id,parent_id) AS ("
                    "SELECT id,parent_id FROM rail_station_nodes WHERE kind IN ('station','facility','segment','facility_track') "
                    "AND searchable LIKE ? ESCAPE '\\' "
                    "UNION ALL SELECT parent.id,parent.parent_id FROM rail_station_nodes parent "
                    "JOIN matches child ON parent.id=child.parent_id WHERE child.parent_id<>''"
                    ") INSERT OR IGNORE INTO rail_station_matches SELECT id FROM matches",
                    ("%" + query.replace("\\", "\\\\").replace("%", "\\%")
                     .replace("_", "\\_") + "%",),
                )
                db.commit()
        super().set_search(query)

    def refresh_labels(self, keys):
        if self.search:
            self.refresh_affected(keys)
        else:
            super().refresh_labels(keys)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role == Qt.ItemDataRole.CheckStateRole:
            node = self._node(index)
            if node.archived:
                return Qt.CheckState.Unchecked
            if node.kind == "station":
                selected = (node.object_id not in self.station_excluded if self.station_master
                            else node.object_id in self.station_direct)
                return Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked
            if node.kind == "facility":
                selected = (node.object_id not in self.facility_excluded if self.facility_master
                            else node.object_id in self.facility_direct)
                return Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked
            if node.kind == 'facility_track':
                selected = node.object_id not in self.track_excluded if self.facility_master else node.object_id in self.track_direct
                return Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked
            if node.kind == "segment":
                return None
            station_total, facility_total, track_total = self._totals(node.key)
            selected_stations = (station_total - self._excluded_counts.get((node.key, "station"), 0)
                                 if self.station_master else self._direct_counts.get((node.key, "station"), 0))
            selected_facilities = (facility_total - self._excluded_counts.get((node.key, "facility"), 0)
                                   if self.facility_master else self._direct_counts.get((node.key, "facility"), 0))
            selected_tracks = (track_total-self._excluded_counts.get((node.key,'facility_track'),0)
                               if self.facility_master else self._direct_counts.get((node.key,'facility_track'),0))
            selected = selected_stations + selected_facilities + selected_tracks
            total = station_total + facility_total + track_total
            return (Qt.CheckState.Checked if total and selected >= total else
                    Qt.CheckState.PartiallyChecked if selected else Qt.CheckState.Unchecked)
        if index.isValid() and role == Qt.ItemDataRole.DisplayRole:
            node = self._node(index)
            if node.kind == "station" and node.child_count:
                return node.label + f" · {node.total} 项设施"
        return super().data(index, role)

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if not self._node(index).archived and self._node(index).kind != "segment":
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        if self._node(index).kind in ("facility","facility_track"):
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        if self._node(index).kind in ("station", "folder", "facility"):
            flags |= Qt.ItemFlag.ItemIsDropEnabled
        return flags

    def visible_state(self, station_master, station_direct, station_excluded,
                      facility_master, facility_direct, facility_excluded, track_direct=(), track_excluded=()):
        self.station_master = station_master
        self.station_direct = set(station_direct)
        self.station_excluded = set(station_excluded)
        self.facility_master = facility_master
        self.facility_direct = set(facility_direct)
        self.facility_excluded = set(facility_excluded)
        self.track_direct = set(track_direct)
        self.track_excluded = set(track_excluded)
        self._direct_counts = self._membership_counts(self.station_direct, self.facility_direct,self.track_direct)
        self._excluded_counts = self._membership_counts(self.station_excluded, self.facility_excluded,self.track_excluded)
        self._emit_loaded(self.root)

    def reset_from_disk(self):
        self._folder_totals.clear()
        super().reset_from_disk()

    def refresh_affected(self, keys):
        keys = set(keys)
        for key in keys:
            self._folder_totals.pop(key, None)
        if self.search:
            with self._connect() as db, db:
                depths = {}
                for key in list(keys):
                    chain = [row[0] for row in db.execute('WITH RECURSIVE a(id,parent_id) AS ('
                        'SELECT id,parent_id FROM rail_station_nodes WHERE id=? UNION ALL '
                        'SELECT n.id,n.parent_id FROM rail_station_nodes n JOIN a ON n.id=a.parent_id) SELECT id FROM a', (key,))]
                    keys.update(chain)
                    for position, ident in enumerate(chain):
                        depths[ident] = max(depths.get(ident, 0), len(chain)-position)
                db.executemany('DELETE FROM rail_station_matches WHERE id=?', ((key,) for key in keys))
                for key in sorted(keys, key=lambda value: depths.get(value, 0), reverse=True):
                    row = db.execute('SELECT kind,searchable FROM rail_station_nodes WHERE id=?', (key,)).fetchone()
                    if row is None:
                        continue
                    direct = row[0] != 'folder' and self.search in row[1]
                    child = db.execute('SELECT 1 FROM rail_station_nodes n JOIN rail_station_matches m ON m.id=n.id WHERE n.parent_id=? LIMIT 1', (key,)).fetchone()
                    if direct or child:
                        db.execute('INSERT OR IGNORE INTO rail_station_matches VALUES(?)', (key,))
        super().refresh_affected(keys)

    def ids_below(self, key):
        with self._connect() as db:
            rows = db.execute('WITH RECURSIVE children(id) AS (SELECT ? UNION ALL '
                'SELECT n.id FROM rail_station_nodes n JOIN children c ON n.parent_id=c.id) '
                'SELECT n.kind,n.object_id FROM rail_station_nodes n JOIN children c ON n.id=c.id', (key,)).fetchall()
        return ({ident for kind, ident in rows if kind == "station"},
                {ident for kind, ident in rows if kind == "facility"})

    def track_ids_below(self,key):
        with self._connect() as db:
            return {ident for (ident,) in db.execute('WITH RECURSIVE children(id) AS (SELECT ? UNION ALL '
                'SELECT n.id FROM rail_station_nodes n JOIN children c ON n.parent_id=c.id) '
                "SELECT n.object_id FROM rail_station_nodes n JOIN children c ON n.id=c.id WHERE n.kind='facility_track'", (key,))}

    def track_ids_for_catalog(self,groups):
        if not groups:
            return set()
        with self._connect() as db:
            db.execute('CREATE TEMP TABLE selected_track_groups(id TEXT PRIMARY KEY)')
            db.executemany('INSERT OR IGNORE INTO selected_track_groups VALUES(?)',((key,) for key in groups))
            return {key for (key,) in db.execute('SELECT o.object_id FROM selected_track_groups g '
                'JOIN rail_facility_track_owners o ON o.catalog_id=g.id')}

    def reveal_id(self, kind, object_id):
        key = kind + ":" + str(object_id)
        with self._connect() as db:
            chain = []
            current = key
            while current:
                chain.append(current)
                row = db.execute("SELECT parent_id FROM rail_station_nodes WHERE id=?", (current,)).fetchone()
                if row is None:
                    return QModelIndex()
                current = row[0]
        chain.reverse()
        self.clear_spotlight()
        parent = QModelIndex()
        for node_id in chain:
            index = self._find_child(parent, node_id)
            if index is None:
                while self.canFetchMore(parent):
                    before = self.rowCount(parent)
                    self.fetchMore(parent)
                    index = self._find_child(parent, node_id)
                    if index is not None or self.rowCount(parent) == before:
                        break
            if index is None:
                return QModelIndex()
            parent = index
        return parent

    def _find_child(self, parent, key):
        for i in range(self.rowCount(parent)):
            index = self.index(i, 0, parent)
            if self._node(index).key == key:
                return index
        return None

    def clear_spotlight(self):
        spotlight = getattr(self.root, "spotlight", None)
        if spotlight in self.root.children:
            row = self.root.children.index(spotlight)
            self.beginRemoveRows(QModelIndex(), row, row)
            self.root.children.pop(row)
            self.endRemoveRows()
        self.root.spotlight = None

    def release_branch(self, index):
        node = self._node(index)
        if node.children:
            self.beginRemoveRows(index, 0, len(node.children) - 1)
            node.children.clear()
            node.fetched = False
            self.endRemoveRows()
