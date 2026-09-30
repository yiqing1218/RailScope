"""Paged presentation of stations and their railway facilities.

Ownership in this cache is only a directory placement. Inferred name matches
remain marked for review and never change infrastructure or operating IDs.
"""

from contextlib import closing
import hashlib
import json
import sqlite3

from PySide6.QtCore import QModelIndex, Qt

try:
    from .lazy_directory import SqliteDirectoryModel, _Node
    from .catalog_metadata import rail_station_records, station_directory_path
except ImportError:
    from lazy_directory import SqliteDirectoryModel, _Node
    from catalog_metadata import rail_station_records, station_directory_path


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
    segment_edits = {k: {field: v[field] for field in ("display_name", "line_name", "track_type") if field in v}
                     for k, v in overrides.items() if k.startswith("object:") and
                     any(field in v for field in ("display_name", "line_name", "track_type"))}
    with closing(sqlite3.connect(catalog_path)) as db:
        catalog_version = db.execute("SELECT value FROM metadata WHERE key='paged_directory_signature'").fetchone()
    return hashlib.sha256(_key([8, source.stat().st_mtime_ns,
                                 catalog_version[0] if catalog_version else "",
                                 station_edits, facility_edits, segment_edits]).encode()).hexdigest()


def sync_station_catalog(directory, catalog_path, regions, overrides):
    """Build one disk-backed tree; do nothing when its inputs are unchanged."""
    signature = station_catalog_signature(directory, catalog_path, overrides)
    if signature is None:
        return
    with closing(sqlite3.connect(catalog_path)) as db:
        old = db.execute("SELECT value FROM metadata WHERE key='station_catalog_signature'").fetchone()
        if old and old[0] == signature:
            return False
        stations, _ = rail_station_records(directory, regions, limit=100000, overrides=overrides)
        db.execute("BEGIN")
        db.execute("CREATE TABLE IF NOT EXISTS rail_station_nodes (id TEXT PRIMARY KEY,parent_id TEXT NOT NULL,label TEXT NOT NULL,kind TEXT NOT NULL,object_id TEXT,path TEXT NOT NULL,child_count INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,searchable TEXT NOT NULL DEFAULT '',station_total INTEGER NOT NULL DEFAULT 0,facility_total INTEGER NOT NULL DEFAULT 0)")
        existing_columns = {row[1] for row in db.execute("PRAGMA table_info(rail_station_nodes)")}
        for column in ("station_total", "facility_total"):
            if column not in existing_columns:
                db.execute(f"ALTER TABLE rail_station_nodes ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
        db.execute("CREATE INDEX IF NOT EXISTS rail_station_parent ON rail_station_nodes(parent_id,kind,label,id)")
        db.execute("CREATE INDEX IF NOT EXISTS rail_station_object ON rail_station_nodes(object_id,kind)")
        db.execute("CREATE TABLE IF NOT EXISTS rail_station_matches (id TEXT PRIMARY KEY)")
        db.execute("DELETE FROM rail_station_nodes")
        db.execute("DELETE FROM rail_station_matches")
        rows = []
        folders = set()
        totals = {}
        station_totals = {}
        facility_totals = {}
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
            if not custom.get('folder_path') and station_kind in ('编组站', '车辆段', '检修站', '机务段', '动车所/客整所', '货场', '存车场'):
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
        db.executemany("INSERT INTO rail_station_nodes(id,parent_id,label,kind,object_id,path,archived,searchable) VALUES(?,?,?,?,?,?,?,?)", rows)
        db.executemany("UPDATE rail_station_nodes SET total=? WHERE id=?",
                       ((count, key) for key, count in totals.items()))
        db.executemany("UPDATE rail_station_nodes SET station_total=? WHERE id=?",
                       ((count, key) for key, count in station_totals.items()))
        db.executemany("UPDATE rail_station_nodes SET facility_total=? WHERE id=?",
                       ((count, key) for key, count in facility_totals.items()))
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
        self._folder_totals = {}
        self._direct_counts = {}
        self._excluded_counts = {}

    def _membership_counts(self, station_ids, facility_ids):
        keys = ["station:" + str(value) for value in station_ids]
        keys.extend("facility:" + str(value) for value in facility_ids)
        if not keys:
            return {}
        with self._connect() as db:
            db.execute("CREATE TEMP TABLE selected_station_nodes(id TEXT PRIMARY KEY)")
            db.executemany("INSERT OR IGNORE INTO selected_station_nodes VALUES(?)", ((key,) for key in keys))
            rows = db.execute(
                "WITH RECURSIVE ancestors(id,parent_id,kind) AS ("
                "SELECT n.id,n.parent_id,n.kind FROM rail_station_nodes n "
                "JOIN selected_station_nodes s ON s.id=n.id "
                "UNION ALL SELECT p.id,p.parent_id,a.kind FROM rail_station_nodes p "
                "JOIN ancestors a ON p.id=a.parent_id WHERE a.parent_id<>''"
                ") SELECT id,kind,count(*) FROM ancestors GROUP BY id,kind"
            ).fetchall()
        return {(key, kind): count for key, kind, count in rows}

    def _totals(self, key):
        if key not in self._folder_totals:
            with self._connect() as db:
                self._folder_totals[key] = db.execute(
                    "SELECT station_total,facility_total FROM rail_station_nodes WHERE id=?", (key,)
                ).fetchone() or (0, 0)
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
                    "SELECT id,parent_id FROM rail_station_nodes WHERE kind IN ('station','facility','segment') "
                    "AND searchable LIKE ? ESCAPE '\\' "
                    "UNION ALL SELECT parent.id,parent.parent_id FROM rail_station_nodes parent "
                    "JOIN matches child ON parent.id=child.parent_id WHERE child.parent_id<>''"
                    ") INSERT OR IGNORE INTO rail_station_matches SELECT id FROM matches",
                    ("%" + query.replace("\\", "\\\\").replace("%", "\\%")
                     .replace("_", "\\_") + "%",),
                )
                db.commit()
        super().set_search(query)

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
            if node.kind == "segment":
                return None
            station_total, facility_total = self._totals(node.key)
            selected_stations = (station_total - self._excluded_counts.get((node.key, "station"), 0)
                                 if self.station_master else self._direct_counts.get((node.key, "station"), 0))
            selected_facilities = (facility_total - self._excluded_counts.get((node.key, "facility"), 0)
                                   if self.facility_master else self._direct_counts.get((node.key, "facility"), 0))
            selected = selected_stations + selected_facilities
            total = station_total + facility_total
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
        if self._node(index).kind == "facility":
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        if self._node(index).kind in ("station", "folder", "facility"):
            flags |= Qt.ItemFlag.ItemIsDropEnabled
        return flags

    def visible_state(self, station_master, station_direct, station_excluded,
                      facility_master, facility_direct, facility_excluded):
        self.station_master = station_master
        self.station_direct = set(station_direct)
        self.station_excluded = set(station_excluded)
        self.facility_master = facility_master
        self.facility_direct = set(facility_direct)
        self.facility_excluded = set(facility_excluded)
        self._direct_counts = self._membership_counts(self.station_direct, self.facility_direct)
        self._excluded_counts = self._membership_counts(self.station_excluded, self.facility_excluded)
        self._emit_loaded(self.root)

    def reset_from_disk(self):
        self._folder_totals.clear()
        super().reset_from_disk()

    def ids_below(self, key):
        with self._connect() as db:
            row = db.execute("SELECT path FROM rail_station_nodes WHERE id=?", (key,)).fetchone()
            if row is None:
                return set(), set()
            path = row[0]
            rows = db.execute("SELECT kind,object_id FROM rail_station_nodes WHERE path=? "
                              "OR substr(path,1,length(?))=substr(?,1,length(?)-1)||','",
                              (path, path, path, path)).fetchall()
        return ({ident for kind, ident in rows if kind == "station"},
                {ident for kind, ident in rows if kind == "facility"})

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
