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
    from .lazy_directory import SqliteDirectoryModel, PAGE_SIZE
    from .rail_semantics import semantic_record
    from .components import directory_checkbox_style
except ImportError:
    from lazy_directory import SqliteDirectoryModel, PAGE_SIZE
    from rail_semantics import semantic_record
    from components import directory_checkbox_style


PRESENTATION_VERSION = 2


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def facility_path(record):
    """Present explicit facility identity; never merge unknown owners by name."""
    facts = semantic_record(record, record)
    role = facts.get("track_role", "unknown")
    facility = facts.get("facility_id")
    is_facility = bool(facility or facts.get("yard_id") or facts.get("zone_id") or facts.get("facility_only"))
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
    owner = (str(record.get("facility_name") or record.get("station_name") or facility)
             + " · " + str(facility)) if facility else "设施归属待核实"
    path = [owner]
    if facts.get("yard_id"):
        path.append(str(record.get("yard_name") or facts["yard_id"]))
    if facts.get("zone_id"):
        path.append(str(record.get("zone_name") or facts["zone_id"]))
    path.append(labels.get(role, "用途待核实"))
    return tuple(path)


def sync_catalog_directory(catalog, overrides, resolve, mode=0, aliases=None):
    """Stream records into a rebuildable SQLite cache, without Qt row objects."""
    signature = hashlib.sha256(_json([PRESENTATION_VERSION, mode, overrides, aliases or {}]).encode()).hexdigest()
    with closing(sqlite3.connect(catalog.path)) as db:
        old = db.execute("SELECT value FROM metadata WHERE key='paged_directory_signature'").fetchone()
        if old and old[0] == signature:
            return
        # DDL stays in this transaction: a failed resolver preserves the old index.
        db.execute("BEGIN")
        db.execute("CREATE TABLE IF NOT EXISTS rail_directory_nodes (id TEXT PRIMARY KEY,parent_id TEXT NOT NULL,label TEXT NOT NULL,kind TEXT NOT NULL,object_id TEXT,path TEXT NOT NULL,child_count INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,view TEXT NOT NULL,searchable TEXT NOT NULL DEFAULT '')")
        db.execute("CREATE INDEX IF NOT EXISTS rail_directory_parent ON rail_directory_nodes(parent_id,view,kind,label,id)")
        db.execute("CREATE TABLE IF NOT EXISTS rail_directory_members (node_id TEXT NOT NULL,catalog_id TEXT NOT NULL,PRIMARY KEY(node_id,catalog_id))")
        db.execute("CREATE INDEX IF NOT EXISTS rail_directory_catalog ON rail_directory_members(catalog_id,node_id)")
        db.execute("DELETE FROM rail_directory_members")
        db.execute("DELETE FROM rail_directory_nodes")
        for key, raw in db.execute("SELECT id,data FROM catalog ORDER BY id"):
            record, line_path, label = resolve(key, json.loads(raw))
            facts_path = facility_path({"id": key, **record})
            if facts_path is None and line_path and line_path[0] == "车站设施":
                facts_path = tuple(line_path[1:])
            # A station mainline appears in both views; service tracks only in facilities.
            role = semantic_record(record, record).get("track_role", "unknown")
            views = []
            if facts_path is None or role == "main_track" or record.get("assembly_id"):
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
                    db.execute("INSERT OR IGNORE INTO rail_directory_nodes(id,parent_id,label,kind,path,archived,view) VALUES(?,?,?,'folder',?,?,?)",
                               (folder, parent, prefix[-1], json.dumps(prefix, ensure_ascii=False), int(bool(record.get("archived"))), view))
                    parent = folder
                assembly = record.get("assembly_id")
                # Same-name presentation grouping is not a claim of line identity.
                grouping = [view, assembly] if assembly else [view, path, label.casefold(), key if record.get("separate_catalog_entry") else ""]
                node_id = "object:" + hashlib.sha256(_json(grouping).encode()).hexdigest()
                search = " ".join(str(record.get(field) or "") for field in ("name", "line_name", "station_name", "line_id", "railway_class", "line_role", "track_role")) + " " + key + " " + label
                db.execute("INSERT OR IGNORE INTO rail_directory_nodes(id,parent_id,label,kind,object_id,path,archived,view,searchable) VALUES(?,?,?,'object',?,?,?,?,?)",
                           (node_id, parent, label, key, json.dumps(full_path, ensure_ascii=False), int(bool(record.get("archived"))), view, search.casefold()))
                db.execute("INSERT INTO rail_directory_members VALUES(?,?)", (node_id,key))
                db.execute("UPDATE rail_directory_nodes SET total=total+1,searchable=searchable||? WHERE id=?", (" " + search.casefold(),node_id))
                for depth in range(2,len(full_path)+1):
                    folder = "folder:" + json.dumps(full_path[:depth],ensure_ascii=False)
                    db.execute("UPDATE rail_directory_nodes SET total=total+1 WHERE id=?",(folder,))
        db.execute("UPDATE rail_directory_nodes SET child_count=(SELECT count(*) FROM rail_directory_nodes c WHERE c.parent_id=rail_directory_nodes.id)")
        db.execute("INSERT OR REPLACE INTO metadata VALUES('paged_directory_signature',?)", (signature,))
        db.commit()


class RailDirectoryModel(SqliteDirectoryModel):
    """One page per expanded branch; row check states include unloaded members."""
    def __init__(self, path, view="lines", parent=None):
        super().__init__(path, "rail_directory_nodes", parent)
        self.view = view
        self.root.key = "folder:" + json.dumps([view], ensure_ascii=False)
        self.all_visible = False
        self.reveal_target = None

    def hasChildren(self, parent=QModelIndex()):
        return self._node(parent).child_count > 0 if parent.isValid() else self._count_children(self.root.key) > 0

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
            row=db.execute("SELECT d.id,d.path FROM rail_directory_nodes d JOIN rail_directory_members m ON m.node_id=d.id WHERE m.catalog_id=? AND d.view=?",(key,self.view)).fetchone()
        if row is None:
            return QModelIndex()
        # Map focus must not walk every preceding page in a 100k-row folder.
        # Temporarily show the exact object's branch; a new search restores the
        # ordinary directory. The map-header query is retained by the widget.
        self.beginResetModel()
        self.reveal_target = row[0]
        self.root.children.clear()
        self.root.fetched = False
        self.endResetModel()
        parts=json.loads(row[1])
        targets=["folder:"+json.dumps(parts[:depth],ensure_ascii=False) for depth in range(2,len(parts)+1)]+[row[0]]
        parent=QModelIndex()
        for target in targets:
            while True:
                found=next((self.index(i,0,parent) for i in range(self.rowCount(parent)) if self._node(self.index(i,0,parent)).key==target),None)
                if found is not None:
                    parent=found
                    break
                if not self.canFetchMore(parent):
                    return QModelIndex()
                self.fetchMore(parent)
        return parent

    def set_search(self, query):
        self.reveal_target = None
        super().set_search(query)

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
