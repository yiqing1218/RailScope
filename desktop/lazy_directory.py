"""Paged Qt model for a disk-backed directory.

Only expanded pages become Python nodes. Checkboxes are paint roles, so no
per-row QWidget is created. SQLite remains the source for unloaded children.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import closing
import json
import sqlite3

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, Signal


PAGE_SIZE = 128


@dataclass(eq=False)
class _Node:
    key: str
    parent: "_Node | None"
    label: str = ""
    kind: str = ""
    object_id: str | None = None
    child_count: int = 0
    total: int = 0
    archived: bool = False
    children: list = field(default_factory=list)
    fetched: bool = False


class SqliteDirectoryModel(QAbstractItemModel):
    toggled = Signal(str, bool)

    def __init__(self, db_path, table="directory_nodes", parent=None):
        super().__init__(parent)
        self.db_path = str(db_path)
        self.table = table
        self.root = _Node("", None)
        self.visible_ids = set()
        self.visible_counts = {}
        self.search = ""

    def _connect(self):
        return closing(sqlite3.connect(self.db_path))

    def _node(self, index):
        return index.internalPointer() if index.isValid() else self.root

    def index(self, row, column, parent=QModelIndex()):
        node = self._node(parent)
        if column != 0 or row < 0 or row >= len(node.children):
            return QModelIndex()
        return self.createIndex(row, column, node.children[row])

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        parent = self._node(index).parent
        if parent is None or parent is self.root:
            return QModelIndex()
        grandparent = parent.parent or self.root
        return self.createIndex(grandparent.children.index(parent), 0, parent)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.column() > 0 else len(self._node(parent).children)

    def columnCount(self, parent=QModelIndex()):
        return 1

    def hasChildren(self, parent=QModelIndex()):
        return self._node(parent).child_count > 0 if parent.isValid() else self._count_children("") > 0

    def _count_children(self, key):
        with self._connect() as db:
            return db.execute(
                f"SELECT count(*) FROM {self.table} WHERE parent_id=?" + self._search_clause(),
                (key, *self._search_args()),
            ).fetchone()[0]

    def _search_clause(self):
        if not self.search:
            return ""
        # Directory paths are materialised in the cache. A matching descendant
        # keeps every ancestor visible without creating its Qt item.
        return (f" AND (label LIKE ? OR EXISTS(SELECT 1 FROM {self.table} d "
                f"WHERE (d.path={self.table}.path OR d.path LIKE "
                f"substr({self.table}.path,1,length({self.table}.path)-1)||',%') "
                "AND d.kind='object' AND d.label LIKE ?))")

    def _search_args(self):
        value = f"%{self.search}%"
        return (value, value) if self.search else ()

    def canFetchMore(self, parent=QModelIndex()):
        node = self._node(parent)
        return len(node.children) < self._count_children(node.key)

    def fetchMore(self, parent=QModelIndex()):
        node = self._node(parent)
        node.fetched = True
        with self._connect() as db:
            rows = db.execute(
                f"SELECT id,label,kind,object_id,child_count,total,archived "
                f"FROM {self.table} WHERE parent_id=?" + self._search_clause()
                + " ORDER BY kind='object',label,id LIMIT ? OFFSET ?",
                (node.key, *self._search_args(), PAGE_SIZE, len(node.children)),
            ).fetchall()
        if not rows:
            return
        start = len(node.children)
        self.beginInsertRows(parent, start, start + len(rows) - 1)
        node.children.extend(
            _Node(key, node, label, kind, object_id, child_count, total, bool(archived))
            for key, label, kind, object_id, child_count, total, archived in rows
        )
        self.endInsertRows()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = self._node(index)
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{node.label} · {node.total} 项" if node.kind == "folder" else node.label
        if role == Qt.ItemDataRole.UserRole:
            return node.object_id if node.kind == "object" else node.key
        if role == Qt.ItemDataRole.ToolTipRole:
            return node.object_id or node.label
        if role == Qt.ItemDataRole.CheckStateRole and node.kind in ("object", "folder"):
            if node.kind == "object":
                return Qt.CheckState.Checked if node.object_id in self.visible_ids else Qt.CheckState.Unchecked
            count = self.visible_counts.get(node.key, 0)
            return (Qt.CheckState.Checked if count >= node.total and node.total
                    else Qt.CheckState.PartiallyChecked if count else Qt.CheckState.Unchecked)
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        value = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if not self._node(index).archived:
            value |= Qt.ItemFlag.ItemIsUserCheckable
        return value

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.CheckStateRole or not index.isValid():
            return False
        node = self._node(index)
        if node.archived:
            return False
        state = value.value if isinstance(value, Qt.CheckState) else value
        self.toggled.emit(node.key, state == Qt.CheckState.Checked.value)
        return True

    def set_visible(self, object_ids):
        self.visible_ids = set(object_ids)
        counts = {}
        if self.visible_ids:
            with self._connect() as db:
                db.execute("CREATE TEMP TABLE visible_aliases(id TEXT PRIMARY KEY)")
                db.executemany("INSERT OR IGNORE INTO visible_aliases VALUES(?)", ((value,) for value in self.visible_ids))
                for path in db.execute(
                    f"SELECT d.path FROM {self.table} d JOIN visible_aliases v ON v.id=d.object_id WHERE d.kind='object'"
                ):
                    # Folder depth is bounded, so each visible object updates
                    # only its few ancestors, not every other object.
                    parts = json.loads(path[0])
                    for depth in range(1, len(parts) + 1):
                        key = "folder:" + json.dumps(parts[:depth], ensure_ascii=False)
                        counts[key] = counts.get(key, 0) + 1
        self.visible_counts = counts
        self._emit_loaded(self.root)

    def _emit_loaded(self, parent):
        if parent.children:
            self.dataChanged.emit(
                self.createIndex(0, 0, parent.children[0]),
                self.createIndex(len(parent.children) - 1, 0, parent.children[-1]),
                [Qt.ItemDataRole.CheckStateRole],
            )
            for child in parent.children:
                self._emit_loaded(child)

    def set_search(self, query):
        self.beginResetModel()
        self.search = query.strip()
        self.root.children.clear()
        self.root.fetched = False
        self.endResetModel()

    def reset_from_disk(self):
        self.beginResetModel()
        self.root.children.clear()
        self.root.fetched = False
        self.endResetModel()

    def index_for_key(self, key):
        with self._connect() as db:
            row = db.execute(f"SELECT path FROM {self.table} WHERE id=?", (key,)).fetchone()
        if row is None:
            return QModelIndex()
        path = json.loads(row[0])
        parent = QModelIndex()
        keys = ["folder:" + json.dumps(path[:depth], ensure_ascii=False) for depth in range(1, len(path) + 1)]
        if not keys or key != keys[-1]:
            keys.append(key)
        for target in keys:
            while True:
                matches = [self.index(i, 0, parent) for i in range(self.rowCount(parent))]
                found = next((index for index in matches if self._node(index).key == target), None)
                if found is not None:
                    parent = found
                    break
                if not self.canFetchMore(parent):
                    return QModelIndex()
                self.fetchMore(parent)
        return parent

    def ids_below(self, key):
        with self._connect() as db:
            row = db.execute(f"SELECT kind,object_id,path FROM {self.table} WHERE id=?", (key,)).fetchone()
            if row is None:
                return set()
            kind, object_id, path = row
            if kind == "object":
                return {object_id}
            return {value for (value,) in db.execute(
                f"SELECT DISTINCT object_id FROM {self.table} WHERE kind='object' "
                "AND (path=? OR path LIKE substr(?,1,length(?)-1)||',%')",
                (path, path, path),
            )}

    def refresh_affected(self, keys):
        """Reconcile loaded branches after SQLite rows change; keep other nodes."""
        keys = set(keys)

        def walk(node, index):
            if node.key in keys or (node is self.root and "" in keys):
                self._refresh_loaded_node(node, index)
            for row, child in enumerate(list(node.children)):
                if child in node.children:
                    walk(child, self.createIndex(node.children.index(child), 0, child))

        walk(self.root, QModelIndex())

    def _refresh_loaded_node(self, node, index):
        with self._connect() as db:
            if node is not self.root:
                row = db.execute(
                    f"SELECT label,child_count,total,archived FROM {self.table} WHERE id=?",
                    (node.key,),
                ).fetchone()
                if row:
                    node.label, node.child_count, node.total, archived = row
                    node.archived = bool(archived)
                    self.dataChanged.emit(index, index)
            if not node.fetched:
                return
            target = db.execute(
                f"SELECT id,label,kind,object_id,child_count,total,archived "
                f"FROM {self.table} WHERE parent_id=?" + self._search_clause()
                + " ORDER BY kind='object',label,id LIMIT ?",
                (node.key, *self._search_args(), max(PAGE_SIZE, len(node.children))),
            ).fetchall()
        target_keys = [value[0] for value in target]
        existing = {child.key: child for child in node.children}
        for position in range(len(node.children) - 1, -1, -1):
            if node.children[position].key not in target_keys:
                self.beginRemoveRows(index, position, position)
                node.children.pop(position)
                self.endRemoveRows()
        for position, row in enumerate(target):
            key, label, kind, object_id, child_count, total, archived = row
            child = existing.get(key)
            if child is None:
                child = _Node(key, node, label, kind, object_id, child_count, total, bool(archived))
            else:
                child.label, child.child_count, child.total, child.archived = label, child_count, total, bool(archived)
            if position < len(node.children) and node.children[position] is child:
                continue
            if child in node.children:
                old_position = node.children.index(child)
                self.beginRemoveRows(index, old_position, old_position)
                node.children.pop(old_position)
                self.endRemoveRows()
            self.beginInsertRows(index, position, position)
            node.children.insert(position, child)
            self.endInsertRows()
