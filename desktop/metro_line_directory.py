"""SQLite-backed, paged directory for metro lines and construction routes."""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap

try:
    from .lazy_directory import SqliteDirectoryModel
except ImportError:
    from lazy_directory import SqliteDirectoryModel


TABLE = "line_directory_nodes"


def sync_line_directory(database, routes, parent_for, overrides):
    """Persist grouping metadata, keeping OSM and user overrides separate."""
    groups = defaultdict(list)
    for route in routes:
        relation = int(route["osm_relation_id"])
        province, city, source_label = parent_for(route)
        custom = overrides.get(str(relation), {})
        label = str(custom.get("display_name") or source_label)
        if custom.get("archived", False):
            province, city = "已归档", province + " / " + city
        groups[(province, city, label)].append(relation)
    payload = sorted((list(path), sorted(ids)) for path, ids in groups.items())
    fingerprint = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    relation_keys = {}
    affected = set()
    with closing(sqlite3.connect(database)) as db:
        db.executescript(f"""
            CREATE TABLE IF NOT EXISTS {TABLE}(id TEXT PRIMARY KEY,parent_id TEXT NOT NULL,
                label TEXT NOT NULL,kind TEXT NOT NULL,object_id TEXT,path TEXT NOT NULL,
                child_count INTEGER NOT NULL,total INTEGER NOT NULL,archived INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS metro_line_parent ON {TABLE}(parent_id,label);
            CREATE INDEX IF NOT EXISTS metro_line_object ON {TABLE}(object_id);
        """)
        previous = db.execute(
            "SELECT value FROM metadata WHERE key='line_directory_fingerprint'"
        ).fetchone()
        if not previous or previous[0] != fingerprint:
            folders = {}
            counts = Counter()
            desired = {}
            for path, relations in payload:
                province, city, label = path
                labels = [province, city]
                parent = ""
                for depth in (1, 2):
                    parts = labels[:depth]
                    key = "folder:" + json.dumps(parts, ensure_ascii=False)
                    folders[key] = (parent, parts[-1], json.dumps(parts, ensure_ascii=False))
                    counts[key] += 1
                    parent = key
                key = "object:" + json.dumps(path, ensure_ascii=False)
                desired[key] = (
                    key, parent, label, "object", json.dumps(relations),
                    json.dumps(labels, ensure_ascii=False), 0, 1, int(province == "已归档"),
                )
            for key, (parent, label, encoded) in folders.items():
                desired[key] = (
                    key, parent, label, "folder", None, encoded, 0,
                    counts[key], int(encoded.startswith('["已归档"')),
                )
            # Child counts are calculated after all desired rows exist. Only
            # changed SQLite rows and their loaded Qt branches are reconciled.
            child_counts = Counter(row[1] for row in desired.values())
            desired = {
                key: (*row[:6], child_counts[key] if row[3] == "folder" else 0,
                      *row[7:])
                for key, row in desired.items()
            }
            old = {
                row[0]: row for row in db.execute(f"SELECT * FROM {TABLE}")
            }
            for key, row in old.items():
                if key not in desired:
                    db.execute(f"DELETE FROM {TABLE} WHERE id=?", (key,))
                    affected.update((key, row[1]))
            for key, row in desired.items():
                if old.get(key) != row:
                    db.execute(
                        f"INSERT OR REPLACE INTO {TABLE} VALUES(?,?,?,?,?,?,?,?,?)", row
                    )
                    affected.update((key, row[1]))
                    if key in old:
                        affected.add(old[key][1])
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES('line_directory_fingerprint',?)",
                (fingerprint,),
            )
        for key, encoded in db.execute(
            f"SELECT id,object_id FROM {TABLE} WHERE kind='object'"
        ):
            for relation in json.loads(encoded):
                relation_keys[int(relation)] = key
        db.commit()
    return relation_keys, affected


class MetroLineDirectoryModel(SqliteDirectoryModel):
    def __init__(self, database, route_lookup, parent=None):
        super().__init__(database, TABLE, parent)
        self.route_lookup = route_lookup
        self.route_keys = {}
        self.active_relations = set()
        self.partial_ids = set()
        self.partial_folders = set()
        self._icons = {}

    def relation_ids_below(self, key):
        return {
            int(relation)
            for encoded in self.ids_below(key)
            for relation in json.loads(encoded)
        }

    def set_visible_routes(self, relation_ids):
        self.active_relations = set(relation_ids)
        visible_groups = set()
        partial_groups = set()
        counts = Counter()
        partial_folders = set()
        with self._connect() as db:
            for key, encoded, path in db.execute(
                f"SELECT id,object_id,path FROM {TABLE} WHERE kind='object'"
            ):
                relations = set(json.loads(encoded))
                active = relations & self.active_relations
                if not active:
                    continue
                if active == relations:
                    visible_groups.add(encoded)
                else:
                    partial_groups.add(key)
                labels = json.loads(path)
                for depth in range(1, len(labels) + 1):
                    folder = "folder:" + json.dumps(labels[:depth], ensure_ascii=False)
                    counts[folder] += 1
                    if active != relations:
                        partial_folders.add(folder)
        self.visible_ids = visible_groups
        self.visible_counts = counts
        self.partial_ids = partial_groups
        self.partial_folders = partial_folders
        self._emit_loaded(self.root)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = self._node(index)
        if role == Qt.ItemDataRole.CheckStateRole:
            if node.key in self.partial_ids or node.key in self.partial_folders:
                return Qt.CheckState.PartiallyChecked
        if role == Qt.ItemDataRole.DecorationRole and node.kind == "object":
            relations = json.loads(node.object_id)
            route = self.route_lookup.get(relations[0]) if relations else None
            color = str((route or {}).get("display_color") or "#718096")
            if color not in self._icons:
                swatch = QPixmap(12, 12)
                swatch.fill(QColor(color))
                self._icons[color] = QIcon(swatch)
            return self._icons[color]
        return super().data(index, role)

    def key_for_relation(self, relation):
        return self.route_keys.get(int(relation))
