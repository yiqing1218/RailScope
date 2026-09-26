"""Read-only, bounded-cache access to the national rail business catalog."""

from collections import OrderedDict
from collections.abc import Mapping
from contextlib import closing
import json
import hashlib
import re
from pathlib import Path
import sqlite3
from uuid import uuid4

try:
    from .provinces import VERSION
except ImportError:
    from provinces import VERSION


SCHEMA = 1


def index_path(directory):
    return Path(directory) / "rail_catalog.sqlite"


def build_index(directory, catalog=None):
    directory = Path(directory)
    topology = directory / "rail_catalog.topology.json"
    fallback = directory / "rail_catalog.json"
    source = fallback
    if topology.is_file():
        with topology.open("r", encoding="utf-8") as stream:
            header = stream.read(256)
        match = re.search(r'"version"\s*:\s*"([^"]+)"', header)
        if match and match.group(1) == VERSION:
            source = topology
    target = index_path(directory)
    stamp = f"{source.name}:{source.stat().st_size}:{source.stat().st_mtime_ns}" if source.exists() else "empty"
    if target.is_file() and catalog is None:
        try:
            with closing(sqlite3.connect(target)) as db:
                saved = dict(db.execute("SELECT key,value FROM metadata"))
            if saved.get("schema") == str(SCHEMA) and saved.get("source") == stamp:
                return target
        except sqlite3.DatabaseError:
            pass
    if catalog is None:
        if source.exists():
            payload = json.loads(source.read_text(encoding="utf-8"))
            catalog = payload.get("catalog", {}) if source is topology else payload
        else:
            catalog = {}
    temporary = directory / f"rail_catalog.{uuid4().hex}.sqlite.tmp"
    try:
        with closing(sqlite3.connect(temporary)) as db:
            db.executescript("""
                CREATE TABLE catalog(id TEXT PRIMARY KEY,name TEXT NOT NULL,
                    province TEXT,station_name TEXT,line_id TEXT,
                    searchable TEXT NOT NULL,unnamed INTEGER NOT NULL,
                    data TEXT NOT NULL);
                CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE INDEX catalog_name ON catalog(name);
                CREATE INDEX catalog_province ON catalog(province);
                CREATE INDEX catalog_station ON catalog(station_name);
                CREATE INDEX catalog_line ON catalog(line_id);
                CREATE INDEX catalog_unnamed ON catalog(unnamed,name);
            """)
            for key, record in catalog.items():
                name = str(record.get("name") or key)
                searchable = " ".join(str(record.get(field) or "") for field in (
                    "name", "line_name", "line_display_name", "station_name",
                    "from_name", "to_name", "from_node", "to_node", "track_type",
                )) + " " + str(key)
                db.execute(
                    "INSERT INTO catalog VALUES(?,?,?,?,?,?,?,?)",
                    (str(key), name, record.get("province"), record.get("station_name"),
                     record.get("line_id"), searchable.casefold(),
                     int(name.startswith("未命名轨道")),
                     json.dumps(record, ensure_ascii=False, separators=(",", ":"))),
                )
            db.executemany("INSERT INTO metadata VALUES(?,?)", [
                ("schema", str(SCHEMA)), ("source", stamp),
            ])
            db.commit()
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


class RailCatalogIndex(Mapping):
    def __init__(self, path, cache_size=512):
        self.path = Path(path)
        self.cache_size = cache_size
        self.cache = OrderedDict()
        self._db = None
        self._uri = self.path.resolve().as_uri() + "?mode=ro"

    def _connect(self):
        if self._db is None:
            self._db = sqlite3.connect(self._uri, uri=True, check_same_thread=False)
            self._db.execute("PRAGMA cache_size=-4096")
        return self._db

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None

    def __del__(self):
        self.close()

    def __getitem__(self, key):
        key = str(key)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        with self._connect() as db:
            row = db.execute("SELECT data FROM catalog WHERE id=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        value = json.loads(row[0])
        self.cache[key] = value
        if len(self.cache) > self.cache_size:
            self.cache.popitem(last=False)
        return value

    def __iter__(self):
        with self._connect() as db:
            for (key,) in db.execute("SELECT id FROM catalog ORDER BY rowid"):
                yield key

    def __len__(self):
        with self._connect() as db:
            return db.execute("SELECT count(*) FROM catalog").fetchone()[0]

    def __contains__(self, key):
        if str(key) in self.cache:
            return True
        with self._connect() as db:
            return db.execute("SELECT 1 FROM catalog WHERE id=?", (str(key),)).fetchone() is not None

    def items(self):
        with self._connect() as db:
            for key, data in db.execute("SELECT id,data FROM catalog ORDER BY rowid"):
                yield key, json.loads(data)

    def values(self):
        for _, value in self.items():
            yield value

    def station_groups(self):
        with self._connect() as db:
            for key, data in db.execute("SELECT id,data FROM catalog WHERE id LIKE 'ST-%'"):
                yield key, json.loads(data)

    def candidate_keys(self, query, limit):
        query = query.strip().casefold()
        with self._connect() as db:
            if query:
                rows = db.execute(
                    "SELECT id FROM catalog WHERE searchable LIKE ? "
                    "ORDER BY unnamed,name,id LIMIT ?", (f"%{query}%", limit)
                )
            else:
                rows = db.execute(
                    "SELECT id FROM catalog WHERE unnamed=0 ORDER BY name,id LIMIT ?", (limit,)
                )
            return [row[0] for row in rows]

    def matching_count(self, query):
        query = query.strip().casefold()
        with self._connect() as db:
            if query:
                return db.execute(
                    "SELECT count(*) FROM catalog WHERE searchable LIKE ?", (f"%{query}%",)
                ).fetchone()[0]
            return db.execute("SELECT count(*) FROM catalog WHERE unnamed=0").fetchone()[0]

    def sync_directory_paths(self, mode, overrides, resolve):
        """Cache full folder membership on disk, including undisplayed results."""
        signature = hashlib.sha256(json.dumps([mode, overrides], sort_keys=True,
                                            ensure_ascii=False).encode()).hexdigest()
        with closing(sqlite3.connect(self.path)) as db:
            saved = db.execute("SELECT value FROM metadata WHERE key='directory_signature'").fetchone()
            if not saved or saved[0] != signature:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS directory_paths(id TEXT PRIMARY KEY,path TEXT NOT NULL,archived INTEGER NOT NULL);
                    CREATE INDEX IF NOT EXISTS directory_paths_parent ON directory_paths(path);
                    CREATE TABLE IF NOT EXISTS directory_totals(path TEXT PRIMARY KEY,total INTEGER NOT NULL);
                    DELETE FROM directory_paths;
                    DELETE FROM directory_totals;
                """)
                totals = {}
                for key, raw in db.execute("SELECT id,data FROM catalog"):
                    path, archived = resolve(key, json.loads(raw))
                    db.execute("INSERT INTO directory_paths VALUES(?,?,?)", (key, json.dumps(path,ensure_ascii=False), int(archived)))
                    if not archived:
                        for length in range(1,len(path)+1):
                            prefix = tuple(path[:length])
                            totals[prefix] = totals.get(prefix,0)+1
                db.executemany("INSERT INTO directory_totals VALUES(?,?)",
                               [(json.dumps(path,ensure_ascii=False),total) for path,total in totals.items()])
                db.execute("INSERT OR REPLACE INTO metadata VALUES('directory_signature',?)", (signature,))
                db.commit()
            return {tuple(json.loads(path)): total for path,total in db.execute("SELECT path,total FROM directory_totals")}

    def folder_keys(self, path):
        value = json.dumps(list(path),ensure_ascii=False)
        prefix = value[:-1] + ','
        with self._connect() as db:
            return {row[0] for row in db.execute(
                "SELECT id FROM directory_paths WHERE archived=0 AND (path=? OR (path>=? AND path<?))",
                (value,prefix,prefix+'\uffff'))}

    def selected_folder_counts(self, keys):
        counts = {}
        keys = list(keys)
        with self._connect() as db:
            for start in range(0,len(keys),500):
                batch = keys[start:start+500]
                for (raw,) in db.execute("SELECT path FROM directory_paths WHERE archived=0 AND id IN (" +
                                        ','.join('?' for _ in batch) + ')', batch):
                    path = json.loads(raw)
                    for length in range(1,len(path)+1):
                        prefix = tuple(path[:length])
                        counts[prefix] = counts.get(prefix,0)+1
        return counts

    def __deepcopy__(self, memo):
        return dict(self.items())

    def __eq__(self, other):
        return dict(self.items()) == other
