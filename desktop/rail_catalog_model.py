"""SQLite presentation indexes and paged views for the national rail catalog.

The cache contains presentation memberships only. Moving a folder never changes
infrastructure identity, geometry, track role or an operational path.
"""
from contextlib import closing
import hashlib
import json
import sqlite3

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QTreeView

try:
    from .lazy_directory import SqliteDirectoryModel, PAGE_SIZE, _Node
    from .rail_semantics import semantic_record
    from .components import directory_checkbox_style
except ImportError:
    from lazy_directory import SqliteDirectoryModel, PAGE_SIZE, _Node
    from rail_semantics import semantic_record
    from components import directory_checkbox_style


PRESENTATION_VERSION = 8
LABEL_ONLY_FIELDS = {"display_name"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def facility_path(record):
    """Present explicit facility identity; never merge unknown owners by name."""
    facts = semantic_record(record, record)
    role = facts.get("track_role", "unknown")
    facility = facts.get("facility_id")
    is_facility = bool(facility or facts.get("yard_id") or facts.get("zone_id") or facts.get("facility_only")
                       or record.get("station_id") or record.get("station_source"))
    is_facility |= role not in ("main_track", "unknown")
    # Legacy station groups are source object kinds, not inferred track roles.
    is_facility |= str(record.get("catalog_group_id", record.get("id", ""))).startswith("ST-")
    if not is_facility:
        return None
    labels = {
        "main_track": "站内正线", "arrival_departure_track": "到发线",
        "shunting_track": "调车线", "lead_track": "牵出线", "freight_track": "货物线",
        "depot_track": "段管线", "locomotive_running_track": "机车走行线",
        "maintenance_track": "检修线", "stabling_track": "存车线",
        "turnback_track": "折返线", "crossover": "渡线", "spur_track": "支接用途轨道",
        "safety_siding": "安全线", "escape_siding": "避难线",
        "other_station_track": "其他站线", "unknown": "用途待核实",
    }
    owner_id = facility or record.get("station_id")
    owner = (str(record.get("facility_name") or record.get("station_name") or owner_id)
             + " · " + str(owner_id)) if owner_id else "设施归属待核实"
    path = [owner]
    if facts.get("yard_id"):
        path.append(str(record.get("yard_name") or facts["yard_id"]))
    if facts.get("zone_id"):
        path.append(str(record.get("zone_name") or facts["zone_id"]))
    path.append(labels.get(role, "用途待核实"))
    return tuple(path)


def sync_catalog_directory(catalog, overrides, resolve, mode=0, aliases=None):
    """Stream records into a rebuildable SQLite cache, without Qt row objects."""
    # Names and overview text do not change directory membership. They are
    # updated in place so editing one track does not rebuild the national tree.
    placement = {key: fields for key, edit in overrides.items()
                 if (fields := {field: value for field, value in edit.items()
                                if field not in LABEL_ONLY_FIELDS})}
    signature = hashlib.sha256(_json([PRESENTATION_VERSION, mode, placement]).encode()).hexdigest()
    with closing(sqlite3.connect(catalog.path)) as db:
        old = db.execute("SELECT value FROM metadata WHERE key='paged_directory_signature'").fetchone()
        if old and old[0] == signature:
            return False
        # DDL stays in this transaction: a failed resolver preserves the old index.
        db.execute("BEGIN")
        db.execute("CREATE TABLE IF NOT EXISTS rail_directory_nodes (id TEXT PRIMARY KEY,parent_id TEXT NOT NULL,label TEXT NOT NULL,kind TEXT NOT NULL,object_id TEXT,path TEXT NOT NULL,child_count INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,view TEXT NOT NULL,searchable TEXT NOT NULL DEFAULT '')")
        db.execute("CREATE INDEX IF NOT EXISTS rail_directory_parent ON rail_directory_nodes(parent_id,view,kind,label,id)")
        db.execute("CREATE TABLE IF NOT EXISTS rail_directory_members (node_id TEXT NOT NULL,catalog_id TEXT NOT NULL,PRIMARY KEY(node_id,catalog_id))")
        db.execute("CREATE INDEX IF NOT EXISTS rail_directory_catalog ON rail_directory_members(catalog_id,node_id)")
        db.execute("DELETE FROM rail_directory_members")
        db.execute("DELETE FROM rail_directory_nodes")
        totals = {}
        searches = {}
        seen_folders = set()
        for key, raw in db.execute("SELECT id,data FROM catalog ORDER BY id"):
            record, line_path, label = resolve(key, json.loads(raw))
            facts_path = facility_path({"id": key, **record})
            if facts_path is None and line_path and line_path[0] == "车站设施":
                facts_path = tuple(line_path[1:])
            views = []
            if facts_path is None:
                views.append(("lines", tuple(line_path)))
            if facts_path is not None:
                custom_path = record.get("folder_path")
                path = tuple(custom_path) if custom_path else facts_path
                path = (("已归档",) if record.get("archived") else ()) + path
                views.append(("facilities", path))
            for view, path in views:
                # Prefix view into the JSON path to keep identical folder names distinct.
                full_path = (view, *path)
                parent = ""
                for depth in range(1, len(full_path)+1):
                    prefix = full_path[:depth]
                    folder = "folder:" + json.dumps(prefix, ensure_ascii=False)
                    if depth == 1:
                        parent = folder
                        continue  # invisible view root
                    if folder not in seen_folders:
                        db.execute("INSERT INTO rail_directory_nodes(id,parent_id,label,kind,path,archived,view) VALUES(?,?,?,'folder',?,?,?)",
                                   (folder, parent, prefix[-1], json.dumps(prefix, ensure_ascii=False), int(bool(record.get("archived"))), view))
                        seen_folders.add(folder)
                    parent = folder
                assembly = record.get("assembly_id")
                # Same-name presentation grouping is not a claim of line identity.
                grouping = [view, assembly] if assembly else [view, path, label.casefold(), key if record.get("separate_catalog_entry") else ""]
                node_id = "object:" + hashlib.sha256(_json(grouping).encode()).hexdigest()
                search = " ".join(str(record.get(field) or "") for field in ("name", "line_name", "station_name", "line_id", "railway_class", "line_role", "track_role")) + " " + key + " " + label
                db.execute("INSERT OR IGNORE INTO rail_directory_nodes(id,parent_id,label,kind,object_id,path,archived,view,searchable) VALUES(?,?,?,'object',?,?,?,?,?)",
                           (node_id, parent, label, key, json.dumps(full_path, ensure_ascii=False), int(bool(record.get("archived"))), view, search.casefold()))
                db.execute("INSERT INTO rail_directory_members VALUES(?,?)", (node_id,key))
                totals[node_id] = totals.get(node_id, 0) + 1
                searches[node_id] = searches.get(node_id, "") + " " + search.casefold()
                for depth in range(2,len(full_path)+1):
                    folder = "folder:" + json.dumps(full_path[:depth],ensure_ascii=False)
                    totals[folder] = totals.get(folder, 0) + 1
        db.executemany("UPDATE rail_directory_nodes SET total=? WHERE id=?",
                       ((count, key) for key, count in totals.items()))
        db.executemany("UPDATE rail_directory_nodes SET searchable=searchable||? WHERE id=?",
                       ((value, key) for key, value in searches.items()))
        db.execute("UPDATE rail_directory_nodes SET child_count=(SELECT count(*) FROM rail_directory_nodes c WHERE c.parent_id=rail_directory_nodes.id)")
        db.execute("INSERT OR REPLACE INTO metadata VALUES('paged_directory_signature',?)", (signature,))
        db.commit()
        return True


def update_directory_labels(catalog, keys, resolve):
    """Update isolated object labels in both cached directories atomically.

    A shared display node needs regrouping, so its caller must rebuild instead.
    """
    updates = []
    with closing(sqlite3.connect(catalog.path)) as db:
        for key in keys:
            rows = db.execute(
                "SELECT d.id FROM rail_directory_nodes d JOIN rail_directory_members m "
                "ON m.node_id=d.id WHERE m.catalog_id=?", (key,)
            ).fetchall()
            if not rows or any(db.execute(
                "SELECT count(*) FROM rail_directory_members WHERE node_id=?", (node_id,)
            ).fetchone()[0] != 1 for (node_id,) in rows):
                return False
            record, _, label = resolve(key, catalog[key])
            search = " ".join(str(record.get(field) or "") for field in
                              ("name", "line_name", "station_name", "line_id",
                               "railway_class", "line_role", "track_role"))
            updates.extend((label, (search + " " + key + " " + label).casefold(), node_id)
                           for (node_id,) in rows)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='rail_station_nodes'").fetchone():
                db.execute("UPDATE rail_station_nodes SET label=?, searchable=? "
                           "WHERE id=? AND kind='facility'",
                           (label, (label + " " + key + " " +
                                    str(record.get("station_name") or "")).casefold(),
                            "facility:" + key))
        db.executemany("UPDATE rail_directory_nodes SET label=?, searchable=? WHERE id=?", updates)
        db.commit()
    return True


class RailDirectoryModel(SqliteDirectoryModel):
    """One page per expanded branch; row check states include unloaded members."""
    def __init__(self, path, view="lines", parent=None):
        super().__init__(path, "rail_directory_nodes", parent)
        self.view = view
        self.root.key = "folder:" + json.dumps([view], ensure_ascii=False)
        self.all_visible = False
        self.reveal_target = None

    def hasChildren(self, parent=QModelIndex()):
        return (self._node(parent).child_count > 0 if parent.isValid()
                else bool(self.root.children) or self._count_children(self.root.key) > 0)

    def _search_clause(self):
        if self.reveal_target:
            return (" AND (id=? OR (kind='folder' AND EXISTS(SELECT 1 FROM rail_directory_nodes d WHERE d.id=? "
                    "AND (d.path=rail_directory_nodes.path OR substr(d.path,1,length(rail_directory_nodes.path)-1)=substr(rail_directory_nodes.path,1,length(rail_directory_nodes.path)-1)))))")
        if not self.search:
            return ""
        return (" AND ((label||' '||searchable) LIKE ? ESCAPE '\\' OR (kind='folder' AND EXISTS(SELECT 1 FROM rail_directory_nodes d "
                "WHERE d.kind='object' AND (d.id=rail_directory_nodes.id OR "
                "d.path=rail_directory_nodes.path OR substr(d.path,1,length(rail_directory_nodes.path)-1)=substr(rail_directory_nodes.path,1,length(rail_directory_nodes.path)-1)) "
                "AND d.searchable LIKE ? ESCAPE '\\')))")

    def _search_args(self):
        if self.reveal_target:
            return (self.reveal_target, self.reveal_target)
        value = self.search.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return (f"%{value}%", f"%{value}%") if self.search else ()

    def ids_below(self, key):
        with self._connect() as db:
            row = db.execute("SELECT kind,path FROM rail_directory_nodes WHERE id=?",(key,)).fetchone()
            if row is None:
                return set()
            if row[0] == "object":
                return {r[0] for r in db.execute("SELECT catalog_id FROM rail_directory_members WHERE node_id=?",(key,))}
            path = row[1]
            return {r[0] for r in db.execute("SELECT m.catalog_id FROM rail_directory_members m JOIN rail_directory_nodes d ON d.id=m.node_id WHERE d.path=? OR substr(d.path,1,length(?)-1)=substr(?,1,length(?)-1)",(path,path,path,path))}

    def set_visibility(self, visible, all_visible=False, excluded=()):
        self.all_visible = bool(all_visible)
        self.visible_ids = set(excluded if all_visible else visible)
        counts = {}
        with self._connect() as db:
            db.execute("CREATE TEMP TABLE selected(id TEXT PRIMARY KEY)")
            db.executemany("INSERT OR IGNORE INTO selected VALUES(?)",((v,) for v in self.visible_ids))
            for node_id,path in db.execute("SELECT d.id,d.path FROM rail_directory_nodes d JOIN rail_directory_members m ON m.node_id=d.id JOIN selected s ON s.id=m.catalog_id WHERE d.archived=0 AND d.view=?",(self.view,)):
                counts[node_id] = counts.get(node_id,0)+1
                parts=json.loads(path)
                for depth in range(2,len(parts)+1):
                    key="folder:"+json.dumps(parts[:depth],ensure_ascii=False)
                    counts[key]=counts.get(key,0)+1
        self.visible_counts=counts
        self._emit_loaded(self.root)

    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role == Qt.ItemDataRole.CheckStateRole:
            node=self._node(index)
            if node.archived:
                return Qt.CheckState.Unchecked
            count=self.visible_counts.get(node.key,0)
            count=node.total-count if self.all_visible else count
            return Qt.CheckState.Checked if count>=node.total and node.total else Qt.CheckState.PartiallyChecked if count else Qt.CheckState.Unchecked
        return super().data(index,role)

    def reveal_catalog_id(self,key):
        with self._connect() as db:
            row=db.execute("SELECT d.id,d.path,d.label,d.total,d.archived FROM rail_directory_nodes d JOIN rail_directory_members m ON m.node_id=d.id WHERE m.catalog_id=? AND d.view=?",(key,self.view)).fetchone()
        if row is None:
            self.clear_spotlight()
            return QModelIndex()
        # A map focus must not reset the tree or fetch thousands of preceding
        # pages. Reuse loaded rows, otherwise pin one transient object at root.
        spotlight = getattr(self.root, "spotlight", None)
        if spotlight in self.root.children and spotlight.key != row[0]:
            self.clear_spotlight()
        parts=json.loads(row[1])
        targets=["folder:"+json.dumps(parts[:depth],ensure_ascii=False) for depth in range(2,len(parts)+1)]+[row[0]]
        parent=QModelIndex()
        for target in targets:
            found=next((self.index(i,0,parent) for i in range(self.rowCount(parent)) if self._node(self.index(i,0,parent)).key==target),None)
            if found is None:
                if spotlight in self.root.children and spotlight.key == row[0]:
                    return self.index(self.root.children.index(spotlight), 0)
                pinned = _Node(row[0], self.root, "地图选中 · " + row[2], "object", key,
                               0, row[3], bool(row[4]))
                self.beginInsertRows(QModelIndex(), 0, 0)
                self.root.children.insert(0, pinned)
                self.root.spotlight = pinned
                self.endInsertRows()
                return self.index(0, 0)
            parent=found
        return parent

    def clear_spotlight(self):
        spotlight = getattr(self.root, "spotlight", None)
        if spotlight in self.root.children:
            position = self.root.children.index(spotlight)
            self.beginRemoveRows(QModelIndex(), position, position)
            self.root.children.pop(position)
            self.endRemoveRows()
        self.root.spotlight = None

    def set_search(self, query):
        self.reveal_target = None
        super().set_search(query)
        self.root.spotlight = None

    def release_branch(self, index):
        """Collapsed branches release their page objects; SQLite retains all rows."""
        node = self._node(index)
        if node.children:
            self.beginRemoveRows(index, 0, len(node.children)-1)
            node.children.clear()
            node.fetched = False
            self.endRemoveRows()


class RailDirectoryView(QTreeView):
    move_requested = Signal(list, str)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setStyleSheet(directory_checkbox_style())
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setMinimumHeight(240)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def mousePressEvent(self, event):
        # QTreeView does not select a row on a right click on every platform.
        # Keep the menu target and the selected directory items consistent.
        if event.button() == Qt.MouseButton.RightButton:
            index = self.indexAt(event.position().toPoint())
            if index.isValid() and self.selectionModel() is not None:
                self.setCurrentIndex(index)
        super().mousePressEvent(event)

    def startDrag(self,actions):
        from PySide6.QtCore import QMimeData
        from PySide6.QtGui import QDrag
        mime=QMimeData()
        mime.setData("application/x-railscope-paged-catalog",b"move")
        drag=QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self,event):
        if event.source() is self and event.mimeData().hasFormat("application/x-railscope-paged-catalog"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self,event):
        self.dragEnterEvent(event)

    def dropEvent(self,event):
        target=self.indexAt(event.position().toPoint())
        if event.source() is self and target.isValid():
            keys=[self.model()._node(index).key for index in self.selectionModel().selectedRows()]
            self.move_requested.emit(keys,self.model()._node(target).key)
            event.acceptProposedAction()
        else:
            event.ignore()
