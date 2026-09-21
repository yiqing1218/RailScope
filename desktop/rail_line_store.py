"""Compact disk-backed line directory. Load graph data only for selected lines."""

from collections.abc import Mapping
from itertools import groupby
from pathlib import Path
from contextlib import contextmanager, closing
import json
import sqlite3
from uuid import uuid4

try:
    from .rail_lines import RailLineLibrary, line_identity, edge_length, edge_endpoints
    from .rail_categories import track_type
    from .geometry import distance_m
except ImportError:
    from rail_lines import RailLineLibrary, line_identity, edge_length, edge_endpoints
    from rail_categories import track_type
    from geometry import distance_m

INDEX_VERSION = 10


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
            CREATE TABLE edges(id TEXT PRIMARY KEY,line_id TEXT,a,b,construction INTEGER,length_m REAL,track_type TEXT,evidence TEXT,source_way TEXT,direction TEXT);
            CREATE TABLE nodes(id PRIMARY KEY,label TEXT,kind TEXT,x REAL,y REAL);
            CREATE TABLE node_aliases(source_id PRIMARY KEY,node_id NOT NULL);
            CREATE TABLE line_nodes(line_id TEXT,node_id,PRIMARY KEY(line_id,node_id));
            CREATE TABLE station_aliases(source_id TEXT,alias TEXT,station_node_id,anchor_node,distance_m REAL,verification_status TEXT,confidence REAL,PRIMARY KEY(source_id,alias,anchor_node));
        """)

        def add(edge, replace=True):
            ident, name = line_identity(edge)
            a, b = edge_endpoints(edge)
            category, evidence = track_type(edge.get("way_tags", {}))
            category = edge.get("track_type", category)
            evidence = edge.get("track_type_evidence", evidence)
            db.execute(
                "INSERT OR REPLACE INTO node_aliases VALUES(?,?)",
                (edge.get("from_node", a), a),
            )
            db.execute(
                "INSERT OR REPLACE INTO node_aliases VALUES(?,?)",
                (edge.get("to_node", b), b),
            )
            db.execute(
                "INSERT OR IGNORE INTO lines(id,source_name,track_type,evidence) VALUES(?,?,?,?)",
                (ident, name, category, evidence),
            )
            db.execute(
                ("INSERT OR REPLACE" if replace else "INSERT OR IGNORE")
                + " INTO edges VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    edge["id"],
                    ident,
                    a,
                    b,
                    int(edge.get("construction", False)),
                    edge_length(edge),
                    category,
                    evidence,
                    str(edge.get("osm_way_id") or edge.get("source_edge_id", edge["id"]).split(":")[0].removeprefix("w")),
                    edge.get("direction", "both"),
                ),
            )
            coordinates = edge.get("coordinates", [])
            if coordinates:
                for node, coordinate in (
                    (a, coordinates[0]),
                    (b, coordinates[-1]),
                ):
                    db.execute(
                        "INSERT INTO nodes(id,x,y) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET x=coalesce(nodes.x,excluded.x),y=coalesce(nodes.y,excluded.y)",
                        (node, coordinate[0], coordinate[1]),
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
                CREATE TABLE node_spatial(rowid INTEGER PRIMARY KEY,node_id UNIQUE);
                INSERT INTO node_spatial(node_id) SELECT id FROM nodes;
                CREATE VIRTUAL TABLE node_bounds USING rtree(id,minx,maxx,miny,maxy);
                INSERT INTO node_bounds SELECT s.rowid,n.x,n.x,n.y,n.y FROM nodes n JOIN node_spatial s ON s.node_id=n.id WHERE n.x IS NOT NULL AND n.y IS NOT NULL;
                UPDATE lines SET edge_count=(SELECT count(*) FROM edges WHERE line_id=lines.id);
                DELETE FROM lines WHERE edge_count=0;
            """)

            def label(point):
                prop = point.get("properties", {})
                ident = prop.get("infrastructure_node_id", prop.get("osm_node_id"))
                db.execute(
                    "UPDATE nodes SET label=?,kind=? WHERE id=?",
                    (prop.get("name") or None, prop.get("kind"), ident),
                )

            station_points = {}
            for index, (raw,) in enumerate(
                src.execute("SELECT data FROM features WHERE kind='railPoints'")
            ):
                point = json.loads(raw)
                label(point)
                props = point.get("properties", {})
                if (
                    props.get("kind") in ("station", "halt")
                    and point.get("geometry", {}).get("type") == "Point"
                ):
                    station_points[props.get("osm_node_id")] = point
                if index % 5000 == 0:
                    progress(f"整理车站、线路所与道岔名称：{index:,}")
            # Bundled station anchors have better labels than generic switches.
            for point in points:
                label(point)
                props = point.get("properties", {})
                if (
                    props.get("kind") in ("station", "halt")
                    and point.get("geometry", {}).get("type") == "Point"
                ):
                    station_points[props.get("osm_node_id")] = point

            def nearby_anchors(coordinate):
                x, y = coordinate
                candidates = []
                for radius in (0.02, 0.06):
                    candidates = db.execute(
                        "SELECT n.id,n.x,n.y,ln.line_id FROM node_bounds b "
                        "JOIN node_spatial s ON s.rowid=b.id JOIN nodes n ON n.id=s.node_id JOIN line_nodes ln ON ln.node_id=n.id "
                        "WHERE b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?",
                        (x - radius, x + radius, y - radius, y + radius),
                    ).fetchall()
                    if candidates:
                        break
                if not candidates:
                    return []
                per_line = {}
                for node, nx, ny, line_id in candidates:
                    gap = distance_m(coordinate, [nx, ny])
                    if gap > 5000:
                        continue
                    current = per_line.get(line_id)
                    if current is None or (gap, node) < (current[1], current[0]):
                        per_line[line_id] = (node, gap)
                # Keep nearby alternatives so a station remains selectable after
                # the user chooses a specific physical line in a dense yard.
                result, seen = [], set()
                for node, gap in sorted(per_line.values(), key=lambda item: (item[1], item[0])):
                    if node in seen:
                        continue
                    seen.add(node)
                    result.append((node, gap))
                    if len(result) >= 48:
                        break
                return result

            def add_alias(alias, source_id, station_node, coordinate):
                if not alias or not coordinate:
                    return
                exact = db.execute(
                    "SELECT id FROM nodes WHERE id=?", (station_node,)
                ).fetchone()
                if exact:
                    anchors = [(station_node, 0.0)]
                    status = "source_node"
                else:
                    anchors = nearby_anchors(coordinate)
                    status = (
                        "automatic_nearby_topology_node" if anchors else "unresolved"
                    )
                if not anchors:
                    anchors = [(None, None)]
                for anchor, gap in anchors:
                    confidence = (
                        1.0
                        if status == "source_node"
                        else max(0.1, round(1 - gap / 5000, 3))
                        if anchor
                        else None
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO station_aliases VALUES(?,?,?,?,?,?,?)",
                        (source_id, alias, station_node, anchor, gap, status, confidence),
                    )

            def area_center(geometry):
                if geometry.get("type") == "Polygon":
                    points = [point for ring in geometry.get("coordinates", []) for point in ring]
                elif geometry.get("type") == "MultiPolygon":
                    points = [
                        point
                        for polygon in geometry.get("coordinates", [])
                        for ring in polygon
                        for point in ring
                    ]
                else:
                    points = []
                if not points:
                    return None
                xs, ys = zip(*(point[:2] for point in points))
                return [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2]

            for station_node, point in station_points.items():
                props = point["properties"]
                add_alias(
                    props.get("name"),
                    "node/" + str(station_node),
                    station_node,
                    point["geometry"]["coordinates"],
                )

            # Real station buildings/outlines supply aliases such as 上海站 even
            # when the OSM station node itself is labelled 上海.
            tables = {
                row[0]
                for row in src.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "features" in tables:
                for (raw,) in src.execute(
                    "SELECT data FROM features WHERE kind='railStationAreas'"
                ):
                    area = json.loads(raw)
                    props = area.get("properties", {})
                    alias = props.get("source_name")
                    station_ids = props.get("associated_station_ids", [])
                    station_node = station_ids[0] if station_ids else None
                    station = station_points.get(station_node)
                    coordinate = (
                        station["geometry"]["coordinates"]
                        if station
                        else area_center(area.get("geometry", {}))
                    )
                    if alias and coordinate:
                        source_kind = "way" if "osm_way_id" in props else "relation"
                        source_number = props.get("osm_" + source_kind + "_id")
                        add_alias(
                            alias,
                            f"{source_kind}/{source_number}",
                            station_node,
                            coordinate,
                        )
            db.execute("CREATE INDEX station_alias_name ON station_aliases(alias)")
            db.execute("CREATE INDEX station_alias_anchor ON station_aliases(anchor_node)")
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
            fields = "id,label,kind" if self.kind == "nodes" else "*"
            row = db.execute(
                "SELECT " + fields + " FROM " + self.kind + " WHERE id=?", (key,)
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
        ident, line, a, b, construction, length, kind, evidence, source_way, direction = row
        return {
            "id": ident,
            "from_node": a,
            "to_node": b,
            "construction": bool(construction),
            "line_id": line,
            "length_m": length,
            "track_type": kind,
            "type_evidence": evidence,
            "osm_way_id": int(source_way) if str(source_way).isdigit() else source_way,
            "direction": direction,
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
            "stop": "停车位置",
            "junction": "线路所",
            "signal_box": "线路所",
            "switch": "道岔",
            "signal": "信号点",
            "topology_junction": "拓扑岔接点",
            "line_change": "线路归属变化点",
            "line_terminal": "线路端点",
        }.get(kind, "轨道端点")
        return f"{name or '未命名' + label} · {label} · {ident}"

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
        else:
            clause = (
                " AND (a.alias IS NULL OR a.distance_m=(SELECT min(a2.distance_m) "
                "FROM station_aliases a2 WHERE a2.source_id=a.source_id "
                "AND a2.alias=a.alias AND a2.anchor_node IS NOT NULL))"
            )
        term = (
            "%" + query.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        )
        with self.connect() as db:
            rows = db.execute(
                "SELECT n.id,coalesce(a.alias,n.label),coalesce(n.kind,'station'),a.distance_m "
                "FROM nodes n LEFT JOIN station_aliases a ON a.anchor_node=n.id "
                "WHERE (a.alias LIKE ? ESCAPE '!' OR n.label LIKE ? ESCAPE '!' OR CAST(n.id AS TEXT) LIKE ? ESCAPE '!' OR n.kind LIKE ? ESCAPE '!')"
                + clause
                + " ORDER BY a.alias IS NULL,a.distance_m,n.label IS NULL,coalesce(a.alias,n.label),n.id LIMIT ?",
                (term, term, term, term, *args, limit * 3),
            ).fetchall()
        result, seen = [], set()
        for node, name, kind, gap in rows:
            key = (node, name)
            if key in seen:
                continue
            seen.add(key)
            label = self.node_label((node, name, kind))
            if gap and name:
                label += f" · 邻近轨道端点 {gap:.0f} m"
            result.append((node, label))
            if len(result) >= limit:
                break
        return result

    def selected_library(self, ids):
        # Reuse the graph resolver with only the requested lines, never the country.
        library = RailLineLibrary([], [])
        library.split_nodes = set()
        library.control_nodes = set()
        with self.connect() as db:
            for line_id in dict.fromkeys(ids):
                record = self.lines[line_id]
                from collections import defaultdict

                record.update(edge_ids=[], graph=defaultdict(list))
                library.lines[line_id] = record
                for ident, a, b, construction, length, kind, evidence, direction in db.execute(
                    "SELECT id,a,b,construction,length_m,track_type,evidence,direction FROM edges WHERE line_id=? ORDER BY id",
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
                        "direction": direction,
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
                    if row[1] or row[2]:
                        library.control_nodes.add(row[0])
                for (node,) in db.execute(
                    "SELECT node_id FROM line_nodes WHERE node_id IN (SELECT node_id FROM line_nodes WHERE line_id=?) GROUP BY node_id HAVING count(*)>1",
                    (line_id,),
                ):
                    library.split_nodes.add(node)
        return library

    def search_sections(self, query="", line_id=None, limit=100):
        if not line_id:
            return []
        return self.selected_library([line_id]).search_sections(query, line_id, limit)

    def section(self, section_id, line_id=None):
        if not line_id:
            raise ValueError("选择 RS 区间前必须先选择铁路线")
        return self.selected_library([line_id]).section(section_id, line_id)

    def resolve(self, sequence, policy="strict"):
        if not isinstance(sequence, list):
            raise ValueError("通道序列必须是数组")
        sequence = self.normalize_sequence(sequence)
        ids = [
            e.get("line_id")
            for e in sequence
            if isinstance(e, dict) and e.get("kind") == "line"
        ]
        if any(not isinstance(key, str) or key not in self.lines for key in ids):
            raise ValueError("铁路线编号不存在，请先载入相应的铁路数据")
        return self.selected_library(ids).resolve(sequence, policy)

    def normalize_sequence(self, sequence):
        sequence = json.loads(json.dumps(sequence))
        with self.connect() as db:
            for index in range(0, len(sequence), 2):
                entry = sequence[index]
                if not isinstance(entry, dict) or "node_id" not in entry:
                    continue
                row = db.execute(
                    "SELECT node_id FROM node_aliases WHERE source_id=?",
                    (entry["node_id"],),
                ).fetchone()
                if row:
                    entry["node_id"] = row[0]
        return sequence

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
        # A national snapshot can contain hundreds of thousands of inferred
        # line identities. Scan the edge table once and keep only one line's
        # compact graph in memory instead of opening the database per line.
        with self.connect() as db:
            split_nodes = {
                row[0]
                for row in db.execute(
                    "SELECT node_id FROM line_nodes GROUP BY node_id "
                    "HAVING count(*)>1"
                )
            }
            rows = db.execute(
                "SELECT e.line_id,l.source_name,e.id,e.a,e.b,e.construction,"
                "e.length_m,e.track_type,e.evidence,e.source_way,e.direction,"
                "na.label,na.kind,na.x,na.y,nb.label,nb.kind,nb.x,nb.y "
                "FROM edges e JOIN lines l ON l.id=e.line_id "
                "LEFT JOIN nodes na ON na.id=e.a LEFT JOIN nodes nb ON nb.id=e.b "
                "ORDER BY e.line_id,e.id"
            )
            for line_id, group in groupby(rows, key=lambda row: row[0]):
                edges, node_rows, node_coordinates = [], {}, {}
                for row in group:
                    (
                        _,
                        source_name,
                        edge_id,
                        a,
                        b,
                        construction,
                        length,
                        category,
                        evidence,
                        source_way,
                        direction,
                        a_label,
                        a_kind,
                        ax,
                        ay,
                        b_label,
                        b_kind,
                        bx,
                        by,
                    ) = row
                    edges.append(
                        {
                            "id": edge_id,
                            "from_node": a,
                            "to_node": b,
                            "construction": bool(construction),
                            "length_m": length,
                            "track_type": category,
                            "type_evidence": evidence,
                            "osm_way_id": int(source_way)
                            if str(source_way).isdigit()
                            else source_way,
                            "direction": direction,
                            "line_id": line_id,
                            "line_name": source_name,
                        }
                    )
                    node_rows[a] = (a, a_label, a_kind)
                    node_rows[b] = (b, b_label, b_kind)
                    node_coordinates[a] = [ax, ay] if ax is not None else None
                    node_coordinates[b] = [bx, by] if bx is not None else None
                library = RailLineLibrary(edges, [])
                library.nodes.update(
                    {
                        node: self.node_label(node_rows[node])
                        for node in node_rows
                    }
                )
                library.control_nodes = {
                    node
                    for node, (_, label, kind) in node_rows.items()
                    if label or kind
                }
                library.split_nodes = set(node_rows).intersection(split_nodes)
                for section in library.sections():
                    section["from_name"] = library.nodes[section["from_node"]]
                    section["to_name"] = library.nodes[section["to_node"]]
                    section["from_coordinate"] = node_coordinates.get(
                        section["from_node"]
                    )
                    section["to_coordinate"] = node_coordinates.get(
                        section["to_node"]
                    )
                    yield section

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
                            for row in db.execute(
                                "SELECT id,label,kind FROM nodes ORDER BY id"
                            )
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
