"""Compact disk-backed line directory. Load graph data only for selected lines."""

from collections.abc import Mapping
from pathlib import Path
from contextlib import contextmanager, closing
import json
import sqlite3
from uuid import uuid4

try:
    from .rail_lines import RailLineLibrary, line_identity, edge_length
    from .rail_categories import track_type
except ImportError:
    from rail_lines import RailLineLibrary, line_identity, edge_length
    from rail_categories import track_type

INDEX_VERSION = 5


def fingerprint(source, extras):
    source = Path(source)
    stat = source.stat()
    reference = [
        (e["id"], e["from_node"], e["to_node"], line_identity(e)[0]) for e in extras
    ]
    return json.dumps([INDEX_VERSION, stat.st_size, stat.st_mtime_ns, reference])


def index_ready(path, signature):
    if not Path(path).exists():
        return False
    try:
        with closing(
            sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            return db.execute(
                "SELECT value FROM metadata WHERE key=?", ("source",)
            ).fetchone() == (signature,)
    except sqlite3.Error:
        return False


def build_line_index(source, destination, extras, points, progress=lambda value: None):
    """Stream source rows; cancellation/failure leaves the previous complete index intact."""
    signature = fingerprint(source, extras)
    destination = Path(destination)
    temporary = destination.with_name(destination.name + "." + uuid4().hex + ".tmp")
    db = sqlite3.connect(temporary)
    try:
        db.executescript("""
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE lines(id TEXT PRIMARY KEY,source_name TEXT NOT NULL,edge_count INTEGER DEFAULT 0,track_type TEXT,evidence TEXT);
            CREATE TABLE edges(id TEXT PRIMARY KEY,line_id TEXT,a INTEGER,b INTEGER,construction INTEGER,length_m REAL,track_type TEXT,evidence TEXT);
            CREATE TABLE nodes(id INTEGER PRIMARY KEY,label TEXT,kind TEXT);
            CREATE TABLE line_nodes(line_id TEXT,node_id INTEGER,PRIMARY KEY(line_id,node_id));
        """)

        def add(edge, replace=True):
            ident, name = line_identity(edge)
            db.execute(
                "INSERT OR IGNORE INTO lines(id,source_name,track_type,evidence) VALUES(?,?,?,?)",
                (ident, name, *track_type(edge.get("way_tags", {}))),
            )
            db.execute(
                ("INSERT OR REPLACE" if replace else "INSERT OR IGNORE")
                + " INTO edges VALUES(?,?,?,?,?,?,?,?)",
                (
                    edge["id"],
                    ident,
                    edge["from_node"],
                    edge["to_node"],
                    int(edge.get("construction", False)),
                    edge_length(edge),
                    *track_type(edge.get("way_tags", {})),
                ),
            )

        with closing(
            sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)
        ) as src:
            count = src.execute("SELECT count(*) FROM edges").fetchone()[0]
            for index, (raw,) in enumerate(src.execute("SELECT data FROM edges")):
                add(json.loads(raw))
                if index % 4000 == 0:
                    progress(f"整理铁路名称与端点：{index:,} / {count:,}")
            for edge in extras:
                add(edge, replace=False)
            progress("建立可搜索的线路和端点目录…")
            db.executescript("""
                CREATE INDEX edge_line ON edges(line_id);
                INSERT OR IGNORE INTO line_nodes SELECT line_id,a FROM edges;
                INSERT OR IGNORE INTO line_nodes SELECT line_id,b FROM edges;
                INSERT OR IGNORE INTO nodes(id) SELECT node_id FROM line_nodes;
                CREATE INDEX endpoint_line ON line_nodes(node_id);
                UPDATE lines SET edge_count=(SELECT count(*) FROM edges WHERE line_id=lines.id);
                DELETE FROM lines WHERE edge_count=0;
            """)

            def label(point):
                prop = point.get("properties", {})
                ident = prop.get("osm_node_id")
                db.execute(
                    "UPDATE nodes SET label=?,kind=? WHERE id=?",
                    (prop.get("name") or None, prop.get("kind"), ident),
                )

            for index, (raw,) in enumerate(
                src.execute("SELECT data FROM features WHERE kind='railPoints'")
            ):
                label(json.loads(raw))
                if index % 5000 == 0:
                    progress(f"整理车站、线路所与道岔名称：{index:,}")
            # Bundled station anchors have better labels than generic switches.
            for point in points:
                label(point)
        db.execute("INSERT INTO metadata VALUES(?,?)", ("source", signature))
        progress("保存铁路索引…")
        db.commit()
        db.close()
        temporary.replace(destination)
    except BaseException:
        db.close()
        temporary.unlink(missing_ok=True)
        raise
    return destination


class _DirectoryMapping(Mapping):
    def __init__(self, store, kind):
        self.store, self.kind = store, kind

    def __len__(self):
        with self.store.connect() as db:
            return db.execute("SELECT count(*) FROM " + self.kind).fetchone()[0]

    def __iter__(self):
        with self.store.connect() as db:
            for (ident,) in db.execute("SELECT id FROM " + self.kind + " ORDER BY id"):
                yield ident

    def __getitem__(self, key):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM " + self.kind + " WHERE id=?", (key,)
            ).fetchone()
        if not row:
            raise KeyError(key)
        if self.kind == "lines":
            ident, name, count, kind, evidence = row
            return {
                "id": ident,
                "source_name": name,
                "name": self.store.names.get(ident, name) + " · " + ident,
                "edge_count": count,
                "track_type": kind,
                "type_evidence": evidence,
            }
        if self.kind == "nodes":
            return self.store.node_label(row)
        ident, line, a, b, construction, length, kind, evidence = row
        return {
            "id": ident,
            "from_node": a,
            "to_node": b,
            "construction": bool(construction),
            "line_id": line,
            "length_m": length,
            "track_type": kind,
            "type_evidence": evidence,
        }


class DiskRailLineLibrary:
    def __init__(self, path, names=None):
        self.path, self.names = Path(path), dict(names or {})
        self.lines = _DirectoryMapping(self, "lines")
        self.nodes = _DirectoryMapping(self, "nodes")
        self.edges = _DirectoryMapping(self, "edges")

    @contextmanager
    def connect(self):
        with closing(
            sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            yield db

    @staticmethod
    def node_label(row):
        ident, name, kind = row
        label = {
            "station": "车站轨道节点",
            "halt": "停靠点",
            "junction": "线路所",
            "switch": "道岔",
            "signal": "信号点",
        }.get(kind, "轨道端点")
        return name or f"{label} {ident}"

    def search_lines(self, query="", limit=100, offset=0):
        term = (
            "%" + query.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        )
        aliases = [
            key
            for key, name in self.names.items()
            if query.casefold() in name.casefold()
        ]
        clause = " OR id IN (" + ",".join("?" for _ in aliases) + ")" if aliases else ""
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,source_name,edge_count FROM lines WHERE source_name LIKE ? ESCAPE '!' OR id LIKE ? ESCAPE '!'"
                + clause
                + " ORDER BY source_name,id LIMIT ? OFFSET ?",
                (term, term, *aliases, limit, offset),
            ).fetchall()
        return [
            {
                "id": key,
                "source_name": name,
                "name": self.names.get(key, name) + " · " + key,
                "edge_count": count,
            }
            for key, name, count in rows
        ]

    def search_nodes(self, query="", line_id=None, limit=100):
        clause, args = "", []
        if line_id:
            clause = " AND n.id IN (SELECT node_id FROM line_nodes WHERE line_id=?)"
            args.append(line_id)
        term = (
            "%" + query.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        )
        with self.connect() as db:
            rows = db.execute(
                "SELECT n.id,n.label,n.kind FROM nodes n WHERE (n.label LIKE ? ESCAPE '!' OR CAST(n.id AS TEXT) LIKE ? ESCAPE '!' OR n.kind LIKE ? ESCAPE '!')"
                + clause
                + " ORDER BY n.label IS NULL,n.label,n.id LIMIT ?",
                (term, term, term, *args, limit),
            ).fetchall()
        return [
            (node, self.node_label((node, name, kind))) for node, name, kind in rows
        ]

    def selected_library(self, ids):
        # Reuse the graph resolver with only the requested lines, never the country.
        library = RailLineLibrary([], [])
        library.split_nodes = set()
        with self.connect() as db:
            for line_id in dict.fromkeys(ids):
                record = self.lines[line_id]
                from collections import defaultdict

                record.update(edge_ids=[], graph=defaultdict(list))
                library.lines[line_id] = record
                for ident, a, b, construction, length, kind, evidence in db.execute(
                    "SELECT id,a,b,construction,length_m,track_type,evidence FROM edges WHERE line_id=? ORDER BY id",
                    (line_id,),
                ):
                    library.edges[ident] = {
                        "id": ident,
                        "from_node": a,
                        "to_node": b,
                        "construction": bool(construction),
                        "length_m": length,
                        "track_type": kind,
                        "type_evidence": evidence,
                    }
                    library.edge_lines[ident] = line_id
                    record["edge_ids"].append(ident)
                    record["graph"][a].append((b, ident, "forward"))
                    record["graph"][b].append((a, ident, "reverse"))
                for row in db.execute(
                    "SELECT id,label,kind FROM nodes WHERE id IN (SELECT node_id FROM line_nodes WHERE line_id=?)",
                    (line_id,),
                ):
                    library.nodes[row[0]] = self.node_label(row)
                for (node,) in db.execute(
                    "SELECT node_id FROM line_nodes WHERE node_id IN (SELECT node_id FROM line_nodes WHERE line_id=?) GROUP BY node_id HAVING count(*)>1",
                    (line_id,),
                ):
                    library.split_nodes.add(node)
        return library

    def resolve(self, sequence, policy="strict"):
        if not isinstance(sequence, list):
            raise ValueError("通道序列必须是数组")
        ids = [
            e.get("line_id")
            for e in sequence
            if isinstance(e, dict) and e.get("kind") == "line"
        ]
        if any(not isinstance(key, str) or key not in self.lines for key in ids):
            raise ValueError("铁路线编号不存在，请先载入相应的铁路数据")
        return self.selected_library(ids).resolve(sequence, policy)

    def describe(self, path):
        sequence, previous = [], None
        for leg in path:
            edge = self.edges[leg["edge_id"]]
            key = edge["line_id"]
            a, b = (
                (edge["from_node"], edge["to_node"])
                if leg["direction"] == "forward"
                else (edge["to_node"], edge["from_node"])
            )
            if not sequence:
                sequence.append({"kind": "endpoint", "node_id": a})
            if previous != key:
                if previous:
                    sequence.append({"kind": "endpoint", "node_id": a})
                sequence.append({"kind": "line", "line_id": key})
            previous = key
        if sequence:
            sequence.append({"kind": "endpoint", "node_id": b})
        return sequence

    def sections(self):
        for ident in self.lines:
            yield from self.selected_library([ident]).sections()

    def write_export(self, path, progress=lambda text: None):
        """Stream the directory to avoid a national graph/JSON allocation."""
        path = Path(path)
        tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as stream, self.connect() as db:
                stream.write('{"schema":"railscope.rail-lines.v1",')
                generators = [
                    (
                        "lines",
                        (
                            {
                                "id": key,
                                "name": self.names.get(key, name) + " · " + key,
                                "source_name": name,
                                "edge_count": count,
                            }
                            for key, name, count in db.execute(
                                "SELECT id,source_name,edge_count FROM lines ORDER BY id"
                            )
                        ),
                    ),
                    (
                        "endpoints",
                        (
                            {"node_id": row[0], "name": self.node_label(row)}
                            for row in db.execute("SELECT * FROM nodes ORDER BY id")
                        ),
                    ),
                    ("sections", self.sections()),
                ]
                for index, (key, entries) in enumerate(generators):
                    if index:
                        stream.write(",")
                    stream.write(json.dumps(key) + ":[")
                    for number, value in enumerate(entries):
                        if number % 1000 == 0:
                            progress(f"导出 {key}：{number:,} 项")
                        if number:
                            stream.write(",")
                        json.dump(value, stream, ensure_ascii=False)
                    stream.write("]")
                stream.write("}")
            progress("完成目录导出…")
            tmp.replace(path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def changed_way_names(self, names):
        result = {}
        with self.connect() as db:
            for ident, alias in names.items():
                for (edge,) in db.execute(
                    "SELECT id FROM edges WHERE line_id=?", (ident,)
                ):
                    result[edge.split(":")[0].removeprefix("w")] = alias + " · " + ident
        return result
