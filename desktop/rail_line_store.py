"""Compact disk-backed line directory. Load graph data only for selected lines."""

from collections.abc import Mapping
from itertools import groupby
from math import cos, radians
from pathlib import Path
from contextlib import contextmanager, closing
import json
import sqlite3
from uuid import uuid4

try:
    from .rail_lines import RailLineLibrary, line_identity, edge_length, edge_endpoints, traversal_allowed
    from .rail_categories import track_type
    from .geometry import distance_m
    from .rail_line_workspace import LineWorkspace, grouping_key, build_groups, membership_targets
except ImportError:
    from rail_lines import RailLineLibrary, line_identity, edge_length, edge_endpoints, traversal_allowed
    from rail_categories import track_type
    from geometry import distance_m
    from rail_line_workspace import LineWorkspace, grouping_key, build_groups, membership_targets

INDEX_VERSION = 14


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
    # Version 14 only adds endpoint lookup indexes. Upgrade existing compact
    # indexes in place instead of decoding the national source again.
    if destination.exists():
        with closing(sqlite3.connect(destination)) as existing:
            try:
                row = existing.execute("SELECT value FROM metadata WHERE key='source'").fetchone()
                old_signature = json.loads(row[0]) if row else []
            except (sqlite3.Error, ValueError):
                old_signature = []
            if isinstance(old_signature, list) and old_signature and old_signature[0] == 13 and old_signature[1:] == json.loads(signature)[1:]:
                progress("准备站内接轨查询索引…")
                with existing:
                    existing.execute("BEGIN IMMEDIATE")
                    existing.execute("CREATE INDEX IF NOT EXISTS edge_from ON edges(a)")
                    existing.execute("CREATE INDEX IF NOT EXISTS edge_to ON edges(b)")
                    existing.execute("UPDATE metadata SET value=? WHERE key='source'", (signature,))
                return
    temporary = destination.with_name(destination.name + "." + uuid4().hex + ".tmp")
    db = sqlite3.connect(temporary)
    try:
        db.executescript("""
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE lines(id TEXT PRIMARY KEY,source_name TEXT NOT NULL,edge_count INTEGER DEFAULT 0,track_type TEXT,evidence TEXT);
            CREATE TABLE edges(id TEXT PRIMARY KEY,line_id TEXT,a,b,construction INTEGER,length_m REAL,track_type TEXT,evidence TEXT,source_way TEXT,direction TEXT);
            CREATE INDEX edge_from ON edges(a);
            CREATE INDEX edge_to ON edges(b);
            CREATE TABLE nodes(id PRIMARY KEY,label TEXT,kind TEXT,x REAL,y REAL);
            CREATE TABLE node_aliases(source_id PRIMARY KEY,node_id NOT NULL);
            CREATE TABLE line_nodes(line_id TEXT,node_id,PRIMARY KEY(line_id,node_id));
            CREATE TABLE grouping_keys(line_id TEXT PRIMARY KEY,group_key TEXT NOT NULL);
            CREATE INDEX grouping_key_lookup ON grouping_keys(group_key);
            CREATE TABLE station_aliases(source_id TEXT,alias TEXT,station_node_id,anchor_node,distance_m REAL,verification_status TEXT,confidence REAL,source_x REAL,source_y REAL,PRIMARY KEY(source_id,alias,anchor_node));
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
            group = grouping_key(edge)
            if group is not None:
                db.execute("INSERT OR IGNORE INTO grouping_keys VALUES(?,?)", (ident, group))
            db.execute(
                ("INSERT OR REPLACE" if replace else "INSERT OR IGNORE")
                + " INTO edges VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    edge["id"],
                    ident,
                    a,
                    b,
                    int(bool(edge.get("construction")) or edge.get("construction_status", "operating") != "operating"),
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
            build_groups(db)

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
                    "SELECT node_id FROM node_aliases WHERE source_id=?", (station_node,)
                ).fetchone()
                if not exact:
                    exact = db.execute("SELECT id FROM nodes WHERE id=?", (station_node,)).fetchone()
                if exact:
                    anchors = [(exact[0], 0.0)]
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
                        "INSERT OR IGNORE INTO station_aliases "
                        "(source_id,alias,station_node_id,anchor_node,distance_m,"
                        "verification_status,confidence,source_x,source_y) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            source_id,
                            alias,
                            station_node,
                            anchor,
                            gap,
                            status,
                            confidence,
                            coordinate[0],
                            coordinate[1],
                        ),
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
                            "node/" + str(station_node)
                            if station_node is not None
                            else f"{source_kind}/{source_number}",
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
        if self.kind == "lines":
            key = self.store.workspace.canonical(key)
        with self.store.connect() as db:
            fields = "id,label,kind" if self.kind == "nodes" else "*"
            row = db.execute(
                "SELECT " + fields + " FROM " + self.kind + " WHERE id=?", (key,)
            ).fetchone()
            if row is None and self.kind == "lines" and key in self.store.workspace.retired:
                members = self.store.workspace.retired[key]
                marks = ",".join("?" for _ in members)
                records = db.execute(f"SELECT * FROM main.lines WHERE id IN ({marks})", members).fetchall()
                if len(records) == len(members) and records:
                    row = (key, self.store.workspace.names[key], sum(r[2] for r in records), "组合铁路线", "历史工作区组合；保留既有通道引用")
        if not row:
            raise KeyError(key)
        if self.kind == "lines":
            ident, name, count, kind, evidence = row
            metadata = self.store.metadata.get(ident, {})
            return {
                "id": ident,
                "source_name": name,
                "name": self.store.line_name(ident, name),
                "edge_count": count,
                "track_type": metadata.get("track_type", kind),
                "type_evidence": (
                    "用户工作区分类覆盖；原始依据：" + evidence
                    if metadata.get("track_type") and metadata.get("track_type") != kind
                    else evidence
                ),
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
    def __init__(self, path, names=None, metadata=None):
        self.path, self.names = Path(path), dict(names or {})
        self.metadata = {
            key: value
            for key, value in dict(metadata or {}).items()
            if isinstance(value, dict)
        }
        self._station_groups = {}
        self._station_group_labels = {}
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            self.workspace = LineWorkspace(db, self.metadata)
        self.lines = _DirectoryMapping(self, "lines")
        self.nodes = _DirectoryMapping(self, "nodes")
        self.edges = _DirectoryMapping(self, "edges")

    def membership_provenance(self, sequence):
        with self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='source'").fetchone()
        value = self.workspace.provenance(
            [entry["line_id"] for entry in sequence[1::2]], row[0] if row else "unknown"
        )
        value["names"] = {key: self.lines[key]["name"] for key in value["groups"]}
        return value

    def with_membership(self, value):
        """Replay an exported membership snapshot, checking every source member."""
        if not membership_targets(value):
            return self
        metadata = dict(self.metadata)
        with self.connect() as db:
            for ident, members in value["groups"].items():
                if not isinstance(ident, str) or not isinstance(members, list) or not members or any(not isinstance(m, str) for m in members):
                    raise ValueError("通道线路归属快照无效")
                known = self.workspace.groups.get(ident, self.workspace.retired.get(ident))
                if ident.startswith("RLU-") and known is not None and set(known) != set(members):
                    raise ValueError("线路归属 conflict：同一工作区线路编号对应不同成员 " + ident)
                missing = [key for key in members if not db.execute("SELECT 1 FROM main.lines WHERE id=?", (key,)).fetchone()]
                if missing:
                    raise ValueError("线路归属迁移 conflict：重新导入后缺少成员 " + "、".join(missing[:8]))
                # An imported snapshot governs this resolution only. It neither
                # rewrites the user's current directory nor drops old paths.
                for member in members:
                    metadata[member] = {**metadata.get(member, {}), "assembly_id": ident,
                                        "assembly_name": value.get("names", {}).get(ident, ident)}
        return DiskRailLineLibrary(self.path, self.names, metadata)

    def line_name(self, ident, source_name):
        return (
            self.names.get(ident)
            or self.metadata.get(ident, {}).get("display_name")
            or self.workspace.names.get(ident)
            or source_name
        )

    @staticmethod
    def _station_metadata_key(source_id):
        return "station:" + str(source_id).removeprefix("station:")

    def _station_sources(self, endpoint):
        if isinstance(endpoint, str) and endpoint.startswith("station:"):
            source_id = endpoint.removeprefix("station:")
            return self._station_groups.get(source_id, [source_id])
        with self.connect() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT DISTINCT source_id FROM station_aliases "
                    "WHERE anchor_node=? ORDER BY source_id",
                    (endpoint,),
                )
            ]

    def _station_connection_override(self, source_ids):
        for source_id in source_ids:
            metadata = self.metadata.get(self._station_metadata_key(source_id), {})
            if "connected_lines" in metadata:
                value = metadata["connected_lines"]
                return [dict(item, line_id=self.workspace.canonical(item.get("line_id")))
                        for item in value if isinstance(item, dict)] if isinstance(value, list) else []
        return None

    @staticmethod
    def _business_line(name, kind):
        return not (
            str(name).startswith("未命名轨道")
            or "站场股道" in str(kind)
            or kind in {"渡线 / 道岔连接轨", "车辆段 / 检修线", "折返线"}
        )

    def station_connection_override(self, endpoint, line_ids, max_distance_m=2500):
        """Build a user-verified station-to-line override on real graph nodes."""
        source_ids = self._station_sources(endpoint)
        if not source_ids:
            raise ValueError("车站或线路所没有可核验的稳定来源编号")
        selected = list(dict.fromkeys(self.workspace.canonical(str(value)) for value in line_ids))
        if not selected:
            return []
        with self.connect() as db:
            marks = ",".join("?" for _ in source_ids)
            coordinates = [
                (float(x), float(y))
                for x, y in db.execute(
                    "SELECT source_x,source_y FROM station_aliases "
                    f"WHERE source_id IN ({marks}) AND source_x IS NOT NULL "
                    "AND source_y IS NOT NULL",
                    source_ids,
                )
            ]
            if not coordinates:
                coordinates = [
                    (float(x), float(y))
                    for x, y in db.execute(
                        "SELECT n.x,n.y FROM station_aliases a JOIN nodes n "
                        "ON n.id=a.anchor_node "
                        f"WHERE a.source_id IN ({marks}) AND n.x IS NOT NULL "
                        "AND n.y IS NOT NULL",
                        source_ids,
                    )
                ]
            if not coordinates:
                raise ValueError("车站或线路所没有可用于核验接轨线路的坐标")
            result = []
            for line_id in selected:
                line = db.execute(
                    "SELECT source_name,track_type FROM lines WHERE id=?", (line_id,)
                ).fetchone()
                if not line:
                    raise ValueError(f"线路编号不存在：{line_id}")
                effective_name = self.line_name(line_id, line[0])
                effective_kind = self.metadata.get(line_id, {}).get(
                    "track_type", line[1]
                )
                if not self._business_line(effective_name, effective_kind):
                    raise ValueError(f"{effective_name} 不是可用于通道的业务线路")
                min_x = min(value[0] for value in coordinates) - 0.04
                max_x = max(value[0] for value in coordinates) + 0.04
                min_y = min(value[1] for value in coordinates) - 0.04
                max_y = max(value[1] for value in coordinates) + 0.04
                candidates = db.execute(
                    "SELECT n.id,n.x,n.y FROM line_nodes l JOIN nodes n "
                    "ON n.id=l.node_id WHERE l.line_id=? AND n.x BETWEEN ? AND ? "
                    "AND n.y BETWEEN ? AND ? AND n.x IS NOT NULL AND n.y IS NOT NULL",
                    (line_id, min_x, max_x, min_y, max_y),
                ).fetchall()
                best = None
                for node, x, y in candidates:
                    gap = min(
                        distance_m([sx, sy], [float(x), float(y)])
                        for sx, sy in coordinates
                    )
                    if best is None or (gap, str(node)) < (best[0], str(best[1])):
                        best = (gap, node)
                if best is None or best[0] > max_distance_m:
                    raise ValueError(
                        f"{effective_name} 在 {max_distance_m:.0f} 米内没有找到可关联的真实轨道节点"
                    )
                result.append(
                    {
                        "line_id": line_id,
                        "anchor_node": best[1],
                        "distance_m": round(best[0], 1),
                        "source": "manual",
                        "verification_status": "user_verified",
                        "anchor_policy": "auto_reachable",
                        "anchor_verification_status": "automatic_nearest_hint",
                    }
                )
        return result

    @staticmethod
    def station_display_name(name):
        value = (name or "").strip()
        if (
            value
            and any("\u4e00" <= char <= "\u9fff" for char in value)
            and not value.endswith(("站", "线路所", "信号所", "乘降所"))
        ):
            return value + "站"
        return value

    @contextmanager
    def connect(self):
        with closing(
            sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            self.workspace.install(db)
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

    def search_lines(self, query="", limit=100, offset=0, endpoint=None):
        term = (
            "%" + query.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        )
        aliases = [
            key
            for key, name in {
                **{
                    key: value.get("display_name", "")
                    for key, value in self.metadata.items()
                    if value.get("display_name")
                },
                **self.names,
            }.items()
            if query.casefold() in name.casefold()
        ]
        aliases.extend(key for key in self.workspace.targets if query.casefold() in key.casefold())
        aliases = list(dict.fromkeys(self.workspace.canonical(key) for key in aliases))
        alias_clause = " OR id IN (" + ",".join("?" for _ in aliases) + ")" if aliases else ""
        endpoint_clause = (
            " AND id IN (SELECT line_id FROM line_nodes WHERE node_id=?)"
            if endpoint is not None
            else ""
        )
        endpoint_args = [endpoint] if endpoint is not None else []
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,source_name,edge_count FROM lines WHERE (source_name LIKE ? ESCAPE '!' OR id LIKE ? ESCAPE '!'"
                + alias_clause
                + ")"
                + endpoint_clause
                + " ORDER BY source_name,id LIMIT ? OFFSET ?",
                (term, term, *aliases, *endpoint_args, limit, offset),
            ).fetchall()
        return [
            {
                "id": key,
                "source_name": name,
                "name": self.line_name(key, name),
                "edge_count": count,
            }
            for key, name, count in rows
        ]

    def connected_lines(self, node_id, query="", limit=100, physical=False):
        """Named business lines physically connected to the selected endpoint."""
        source_ids = self._station_sources(node_id)
        override = self._station_connection_override(source_ids)
        if physical and not (isinstance(node_id, str) and node_id.startswith("station:")):
            # A station's business-line override does not erase the actual
            # sidings connected to a manually chosen physical node.
            override = None
        nodes = self.endpoint_nodes(node_id)
        if not nodes and override is None:
            return []
        with self.connect() as db:
            if override is not None:
                selected = [
                    value.get("line_id")
                    for value in override
                    if isinstance(value, dict) and value.get("line_id")
                ]
                marks = ",".join("?" for _ in selected)
                rows = (
                    db.execute(
                        "SELECT id,source_name,edge_count,track_type FROM lines "
                        f"WHERE id IN ({marks})",
                        selected,
                    ).fetchall()
                    if selected
                    else []
                )
                order = {line_id: index for index, line_id in enumerate(selected)}
                rows.sort(key=lambda row: order.get(row[0], len(order)))
            else:
                marks = ",".join("?" for _ in nodes)
                rows = db.execute(
                    "SELECT DISTINCT l.id,l.source_name,l.edge_count,l.track_type FROM lines l "
                    f"WHERE l.id IN (SELECT line_id FROM line_nodes WHERE node_id IN ({marks})) "
                    "ORDER BY CASE WHEN l.source_name LIKE '未命名轨道%' THEN 1 ELSE 0 END,l.source_name,l.id LIMIT 500",
                    nodes,
                ).fetchall()
        result = []
        for key, name, count, kind in rows:
            effective = self.line_name(key, name)
            effective_kind = self.metadata.get(key, {}).get("track_type", kind)
            if not physical and not self._business_line(effective, effective_kind):
                continue
            if query.casefold() not in (effective + " " + key + " " + name).casefold():
                continue
            result.append({
                "id": key,
                "source_name": name,
                "name": effective,
                "edge_count": count,
            })
            if len(result) >= limit:
                break
        return result

    def endpoint_nodes(self, endpoint):
        if isinstance(endpoint, str) and endpoint.startswith("station:"):
            source_id = endpoint.removeprefix("station:")
            source_ids = self._station_groups.get(source_id, [source_id])
            with self.connect() as db:
                marks = ",".join("?" for _ in source_ids)
                nodes = [row[0] for row in db.execute(
                    "SELECT anchor_node,min(distance_m) FROM station_aliases "
                    f"WHERE source_id IN ({marks}) AND anchor_node IS NOT NULL "
                    "GROUP BY anchor_node ORDER BY min(distance_m)", source_ids
                )]
            override = self._station_connection_override(source_ids)
            if override is not None:
                nodes.extend(
                    value.get("anchor_node")
                    for value in override
                    if isinstance(value, dict) and value.get("anchor_node") is not None
                )
            return list(dict.fromkeys(nodes))
        with self.connect() as db:
            row = db.execute(
                "SELECT node_id FROM node_aliases WHERE source_id=?", (endpoint,)
            ).fetchone()
        return [row[0] if row else endpoint] if endpoint in self.nodes or row else []

    def endpoint_label(self, endpoint):
        if isinstance(endpoint, str) and endpoint.startswith("station:"):
            source_id = endpoint.removeprefix("station:")
            custom = self.metadata.get(self._station_metadata_key(source_id), {})
            if custom.get("display_name"):
                return str(custom["display_name"])
            if source_id in self._station_group_labels:
                return self._station_group_labels[source_id]
            with self.connect() as db:
                row = db.execute(
                    "SELECT alias FROM station_aliases WHERE source_id=? ORDER BY distance_m LIMIT 1",
                    (source_id,),
                ).fetchone()
            return self.station_display_name(row[0]) if row else source_id
        with self.connect() as db:
            row = db.execute(
                "SELECT alias FROM station_aliases WHERE anchor_node=? "
                "ORDER BY distance_m,source_id NOT LIKE 'node/%' LIMIT 1",
                (endpoint,),
            ).fetchone()
        if row:
            return self.station_display_name(row[0])
        return self.nodes[endpoint] if endpoint in self.nodes else str(endpoint)

    def endpoint_choice_label(self, endpoint):
        """Human label for an existing physical or logical endpoint."""
        label = self.endpoint_label(endpoint)
        candidates = self.search_endpoints(label, limit=30)
        for candidate, candidate_label in candidates:
            if endpoint == candidate or endpoint in self.endpoint_nodes(candidate):
                return candidate_label
        connected = [line["name"] for line in self.connected_lines(endpoint)]
        return label + " · 接轨：" + (" / ".join(connected) if connected else "待关联")

    def search_endpoints(self, query="", line_id=None, limit=100, reachable=None, physical=False):
        """Logical stations by default; exact rail nodes for explicit disambiguation."""
        line_id = self.workspace.canonical(line_id)
        term = "%" + query.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        with self.connect() as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(station_aliases)")}
            coordinates = (
                ",min(a.source_x),min(a.source_y)"
                if {"source_x", "source_y"} <= columns
                else ",NULL,NULL"
            )
            override_sources = [
                key.removeprefix("station:")
                for key, metadata in self.metadata.items()
                if key.startswith("station:")
                and isinstance(metadata.get("connected_lines"), list)
                and any(
                    isinstance(value, dict) and self.workspace.canonical(value.get("line_id")) == line_id
                    for value in metadata["connected_lines"]
                )
            ] if line_id else []
            source_marks = ",".join("?" for _ in override_sources)
            line_clause = ""
            if line_id:
                line_clause = (
                    " AND (a.anchor_node IN (SELECT node_id FROM line_nodes WHERE line_id=?)"
                    + (f" OR a.source_id IN ({source_marks})" if override_sources else "")
                    + ")"
                )
            args = [term, term]
            if line_id:
                args.append(line_id)
                args.extend(override_sources)
            if reachable is not None:
                # Filter before LIMIT so distant alphabetically earlier stations
                # cannot crowd the reachable endpoints out of the picker.
                db.execute("CREATE TEMP TABLE reachable_nodes(id PRIMARY KEY)")
                db.executemany("INSERT INTO reachable_nodes VALUES(?)", ((node,) for node in reachable))
                manual_sources = [source for source in override_sources if any(
                    isinstance(value, dict) and self.workspace.canonical(value.get("line_id")) == line_id
                    and value.get("anchor_node") in reachable
                    for value in self.metadata.get("station:" + source, {}).get("connected_lines", [])
                )]
                line_clause += " AND (a.anchor_node IN (SELECT id FROM reachable_nodes)"
                if manual_sources:
                    line_clause += " OR a.source_id IN (" + ",".join("?" for _ in manual_sources) + ")"
                    args.extend(manual_sources)
                line_clause += ")"
            rows = db.execute(
                "SELECT a.source_id,a.alias,min(a.distance_m)" + coordinates + " FROM station_aliases a "
                "WHERE (a.alias LIKE ? ESCAPE '!' OR a.source_id LIKE ? ESCAPE '!')"
                + line_clause
                + " AND a.confidence>=0.5 AND a.distance_m<=2500 "
                "GROUP BY a.source_id,a.alias ORDER BY a.alias,a.source_id NOT LIKE 'node/%',min(a.distance_m) LIMIT ?",
                (*args, limit * 12),
            ).fetchall()
            # Merge duplicate OSM points/buildings for one named station by a
            # small coordinate cell.  The selected entity keeps every physical
            # anchor, so choosing 合肥南站 does not lose any connected line.
            grouped = {}
            for source_id, alias, gap, x, y in rows:
                station_name = "".join(alias.split()).removesuffix("站").casefold()
                cell = (
                    round(float(x), 2), round(float(y), 2)
                ) if x is not None and y is not None else (source_id,)
                grouped.setdefault((station_name, cell), []).append(
                    (source_id, alias, gap)
                )

            def connected_names(nodes):
                if not nodes:
                    return []
                marks = ",".join("?" for _ in nodes)
                values = []
                for ident, source_name, kind in db.execute(
                        "SELECT DISTINCT l.id,l.source_name,l.track_type FROM lines l "
                        f"WHERE l.id IN (SELECT line_id FROM line_nodes WHERE node_id IN ({marks})) "
                        "ORDER BY CASE WHEN l.source_name LIKE '未命名轨道%' THEN 1 ELSE 0 END,l.source_name,l.id",
                        nodes,
                    ):
                    effective = self.line_name(ident, source_name)
                    effective_kind = self.metadata.get(ident, {}).get("track_type", kind)
                    if (
                        effective.startswith("未命名轨道")
                        or "站场股道" in effective_kind
                        or effective_kind in {
                            "渡线 / 道岔连接轨", "车辆段 / 检修线", "折返线"
                        }
                    ):
                        continue
                    values.append(effective)
                return list(dict.fromkeys(values))

            result = []
            query_key = query.casefold()
            for key, metadata in sorted(self.metadata.items()):
                if not key.startswith("station:signalbox/"):
                    continue
                source_id = key.removeprefix("station:")
                label = str(metadata.get("display_name") or "未命名线路所")
                if query_key not in (label + " " + source_id).casefold():
                    continue
                connections = metadata.get("connected_lines", [])
                if line_id and not any(
                    isinstance(value, dict) and self.workspace.canonical(value.get("line_id")) == line_id
                    for value in connections
                ):
                    continue
                self._station_groups[source_id] = [source_id]
                self._station_group_labels[source_id] = label
                connected = [line["name"] for line in self.connected_lines("station:" + source_id)]
                result.append((
                    "station:" + source_id,
                    label + " · 接轨：" + (" / ".join(connected) if connected else "待关联"),
                ))
                if len(result) >= limit:
                    return result
            for entries in grouped.values():
                entries.sort(key=lambda item: (item[2], item[0]))
                primary, alias, _ = entries[0]
                alias = self.station_display_name(alias)
                sources = list(dict.fromkeys(item[0] for item in entries))
                self._station_groups[primary] = sources
                override = self._station_connection_override(sources)
                if line_id and override is not None and not any(
                    isinstance(value, dict) and self.workspace.canonical(value.get("line_id")) == line_id
                    for value in override
                ):
                    continue
                if override is None:
                    marks = ",".join("?" for _ in sources)
                    nodes = [
                        row[0]
                        for row in db.execute(
                            "SELECT DISTINCT anchor_node FROM station_aliases "
                            f"WHERE source_id IN ({marks}) AND anchor_node IS NOT NULL",
                            sources,
                        )
                    ]
                    connected = connected_names(nodes)
                else:
                    connected = [
                        line["name"]
                        for line in self.connected_lines("station:" + primary)
                    ]
                self._station_group_labels[primary] = alias
                result.append((
                    "station:" + primary,
                    alias + " · 接轨：" + (" / ".join(connected) if connected else "待关联"),
                ))
                if len(result) >= limit:
                    break

            if len(result) < limit:
                control_clause = (
                    " AND n.id IN (SELECT node_id FROM line_nodes WHERE line_id=?)"
                    if line_id else ""
                )
                control_args = [line_id] if line_id else []
                control_kind = (
                    "1=1" if physical and (line_id or query) else
                    "((n.kind IN ('junction','signal_box') AND n.label IS NOT NULL) "
                    "OR (n.kind IN ('topology_junction','line_change','line_terminal') AND n.label IS NOT NULL))"
                )
                if reachable is not None:
                    control_clause += " AND n.id IN (SELECT id FROM reachable_nodes)"
                controls = db.execute(
                    "SELECT n.id,n.label,n.kind FROM nodes n WHERE "
                    + control_kind + " AND "
                    "(coalesce(n.label,'') LIKE ? ESCAPE '!' OR CAST(n.id AS TEXT) LIKE ? ESCAPE '!')"
                    + control_clause
                    + " ORDER BY n.label,n.id LIMIT ?",
                    (term, term, *control_args, limit - len(result)),
                ).fetchall()
                for node, label, kind in controls:
                    connected_lines = self.connected_lines(node, physical=physical)
                    if line_id and not any(
                        value["id"] == line_id for value in connected_lines
                    ):
                        continue
                    connected = [value["name"] for value in connected_lines]
                    result.append((
                        node,
                        self.node_label((node, label, kind))
                        + " · 接轨："
                        + (" / ".join(connected) if connected else "待关联"),
                    ))
        return result[:limit]

    def reachable_nodes(self, from_node, line_id, query="", limit=100, physical=False):
        """Named/control endpoints reachable on one selected physical line."""
        if line_id not in self.lines:
            return []
        selected = self.selected_library([line_id])
        line_id = self.workspace.canonical(line_id)
        starts = self.endpoint_candidates(from_node, [line_id])
        if not starts:
            return []
        stack, reachable = list(starts), set(starts)
        graph = selected.lines[line_id]["graph"]
        while stack:
            node = stack.pop()
            for other, edge_id, direction in graph.get(node, []):
                edge = selected.edges[edge_id]
                if edge.get("construction") or not traversal_allowed(edge, direction):
                    continue
                if other not in reachable:
                    reachable.add(other)
                    stack.append(other)
        candidates = self.search_endpoints(query, line_id, max(limit * 5, 500), reachable=reachable, physical=physical)
        start_name = self.endpoint_label(from_node)
        start_key = "".join(start_name.split()).removesuffix("站").casefold()
        result = [
            item
            for item in candidates
            if item[0] != from_node
            and (physical or "".join(self.endpoint_label(item[0]).split()).removesuffix("站").casefold()
                 != start_key)
            and any(node in reachable for node in self.endpoint_candidates(item[0], [line_id]))
        ]
        return result[:limit]

    def common_transfer_endpoint(self, start, first_line, next_line):
        """Infer a transfer only when one reachable physical node joins both lines."""
        if first_line not in self.lines or next_line not in self.lines:
            return None
        first_line, next_line = map(self.workspace.canonical, (first_line, next_line))
        selected = self.selected_library([first_line])
        graph = selected.lines[first_line]["graph"]
        stack = [node for node in self.endpoint_nodes(start) if node in graph]
        start_nodes = set(stack)
        reachable = set(stack)
        while stack:
            for other, edge_id, direction in graph.get(stack.pop(), []):
                edge = selected.edges[edge_id]
                if edge.get("construction") or not traversal_allowed(edge, direction):
                    continue
                if other not in reachable:
                    reachable.add(other)
                    stack.append(other)
        with self.connect() as db:
            shared = [node for (node,) in db.execute(
                "SELECT DISTINCT a.node_id FROM line_nodes a "
                "JOIN line_nodes b ON b.node_id=a.node_id "
                "WHERE a.line_id=? AND b.line_id=?",
                (first_line, next_line),
            ) if node in reachable and node not in start_nodes]
        if len(shared) != 1:
            return None
        physical = shared[0]
        logical = []
        for endpoint, _label in self.search_endpoints(
            self.endpoint_label(physical), line_id=first_line, limit=100
        ):
            try:
                if self.resolve_endpoint(endpoint, [first_line, next_line]) == physical:
                    logical.append(endpoint)
            except ValueError:
                continue
        return logical[0] if len(logical) == 1 else physical if not logical else None

    def endpoint_candidates(self, endpoint, adjacent_lines):
        """All eligible anchors; never choose a parallel track by distance alone."""
        adjacent_lines = list(dict.fromkeys(self.workspace.canonical(v) for v in adjacent_lines))
        sources = self._station_sources(endpoint)
        override = self._station_connection_override(sources)
        with self.connect() as db:
            if isinstance(endpoint, str) and endpoint.startswith("station:"):
                marks = ",".join("?" for _ in sources)
                candidates = [r[0] for r in db.execute(
                    "SELECT anchor_node FROM station_aliases "
                    f"WHERE source_id IN ({marks}) AND confidence>=0.5 AND distance_m<=2500 "
                    "AND anchor_node IS NOT NULL GROUP BY anchor_node ORDER BY min(distance_m)", sources)]
                fixed_anchors = []
                if override is not None:
                    if not set(adjacent_lines) <= {v.get("line_id") for v in override}:
                        return []
                    for line in adjacent_lines:
                        entries = [v for v in override if v.get("line_id") == line and v.get("anchor_node") is not None]
                        candidates.extend(v["anchor_node"] for v in entries)
                        # Previous station forms only let users select a LINE;
                        # their saved anchor was an automatically chosen nearest
                        # node, not an explicit choice of physical track.
                        default = "fixed" if any(s.startswith("signalbox/") for s in sources) else "auto_reachable"
                        fixed = {v["anchor_node"] for v in entries if v.get("anchor_policy", default) == "fixed"}
                        if fixed:
                            fixed_anchors.append(fixed)
                candidates = [node for node in dict.fromkeys(candidates)
                              if all(node in allowed for allowed in fixed_anchors)]
            else:
                candidates = self.endpoint_nodes(endpoint)
            if not candidates:
                return []
            marks = ",".join("?" for _ in candidates)
            rows = db.execute(f"SELECT node_id,line_id FROM main.line_nodes WHERE node_id IN ({marks})", candidates)
            memberships = {}
            for node, line in rows:
                memberships.setdefault(node, set()).add(line)
            return [node for node in candidates if all(
                memberships.get(node, set()).intersection(self.workspace.members(line)) for line in adjacent_lines)]

    def resolve_endpoint(self, endpoint, adjacent_lines):
        matches = self.endpoint_candidates(endpoint, adjacent_lines)
        if len(matches) == 1:
            return matches[0]
        label = self.endpoint_label(endpoint)
        if not matches:
            raise ValueError(
                f"{label} 没有同时连接所选前后线路的真实轨道端点；请补充站内连接线或选择线路所端点"
            )
        raise ValueError(
            f"{label} 在所选线路上有 {len(matches)} 个合法轨道端点，物理径路不唯一；请增加线路所/道岔端点消歧"
        )

    def search_nodes(self, query="", line_id=None, limit=100):
        line_id = self.workspace.canonical(line_id)
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

    def selected_library(self, ids, edge_ids=None):
        # Reuse the graph resolver with only the requested lines, never the country.
        library = RailLineLibrary([], [])
        library.split_nodes = set()
        library.control_nodes = set()
        with self.connect() as db:
            edge_clause = ""
            if edge_ids is not None:
                db.execute("CREATE TEMP TABLE selected_edges(id TEXT PRIMARY KEY)")
                db.executemany("INSERT OR IGNORE INTO selected_edges VALUES(?)", ((ident,) for ident in edge_ids))
                edge_clause = " AND id IN (SELECT id FROM selected_edges)"
            selected_nodes = set()
            for line_id in dict.fromkeys(self.workspace.canonical(value) for value in ids):
                # Keep the national line index small: a selected graph belongs
                # only to this temporary resolver, not to the shared cache.
                record = dict(self.lines[line_id])
                from collections import defaultdict

                record.update(edge_ids=[], graph=defaultdict(list))
                library.lines[line_id] = record
                members = self.workspace.members(line_id)
                marks = ",".join("?" for _ in members)
                for ident, a, b, construction, length, kind, evidence, direction in db.execute(
                    f"SELECT id,a,b,construction,length_m,track_type,evidence,direction FROM main.edges WHERE line_id IN ({marks})" + edge_clause + " ORDER BY id",
                    members,
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
                    selected_nodes.update((a, b))
            db.execute("CREATE TEMP TABLE selected_nodes(id PRIMARY KEY)")
            db.executemany("INSERT INTO selected_nodes VALUES(?)", ((node,) for node in selected_nodes))
            for row in db.execute("SELECT id,label,kind FROM nodes WHERE id IN (SELECT id FROM selected_nodes)"):
                library.nodes[row[0]] = self.node_label(row)
                if row[1] or row[2]:
                    library.control_nodes.add(row[0])
            for (node,) in db.execute(
                "SELECT node_id FROM main.line_nodes WHERE node_id IN (SELECT id FROM selected_nodes) "
                "GROUP BY node_id HAVING count(DISTINCT line_id)>1"
            ):
                library.split_nodes.add(node)
        return library

    def search_sections(self, query="", line_id=None, limit=100):
        if not line_id:
            return []
        line_id = self.workspace.canonical(line_id)
        return self.selected_library([line_id]).search_sections(query, line_id, limit)

    def section(self, section_id, line_id=None):
        if not line_id:
            raise ValueError("选择 RS 区间前必须先选择铁路线")
        line_id = self.workspace.canonical(line_id)
        return self.selected_library([line_id]).section(section_id, line_id)

    def switches_on_path(self, path):
        """Report only real switches traversed by this resolved physical path."""
        visited_nodes = []
        previous_end = None
        for leg in path:
            edge = self.edges[leg["edge_id"]]
            start, end = (edge["from_node"], edge["to_node"])
            if leg["direction"] == "reverse":
                start, end = end, start
            if previous_end is not None and start != previous_end:
                raise ValueError("通道物理路径不连续")
            if previous_end is None:
                visited_nodes.append(start)
            visited_nodes.append(end)
            previous_end = end
        if not visited_nodes:
            return []
        matches = set()
        with self.connect() as db:
            for offset in range(0, len(visited_nodes), 800):
                batch = visited_nodes[offset:offset+800]
                marks = ",".join("?" for _ in batch)
                matches.update(row[0] for row in db.execute(
                    f"SELECT id FROM nodes WHERE kind='switch' AND id IN ({marks})", batch
                ))
        owners = {
            int(node): custom.get("display_name", "未命名线路所")
            for key, custom in self.metadata.items()
            if key.startswith("station:signalbox/")
            for node in custom.get("member_switch_ids", [])
            if str(node).isdigit()
        }
        return [
            {"source_node_id": node, "signal_box": owners.get(node)}
            for node in dict.fromkeys(visited_nodes)
            if node in matches
        ]

    def resolve_with_sequence(self, sequence, policy="strict", selection=None):
        if not isinstance(sequence, list):
            raise ValueError("通道序列必须是数组")
        replay = policy == "auto" and isinstance(selection, dict) and sequence in (
            selection.get("requested_sequence"), selection.get("resolved_sequence")
        )
        if replay:
            sequence = selection["resolved_sequence"]
        ids = [
            e.get("line_id")
            for e in sequence
            if isinstance(e, dict) and e.get("kind") == "line"
        ]
        if any(not isinstance(key, str) or key not in self.lines for key in ids):
            raise ValueError("铁路线编号不存在，请先载入相应的铁路数据")
        if replay:
            saved_path = selection.get("path")
            if not isinstance(saved_path, list) or any(not isinstance(leg, dict) or not isinstance(leg.get("edge_id"), str) for leg in saved_path):
                raise ValueError("已保存的参考路径字段无效")
            selected = self.selected_library(ids, edge_ids=[leg["edge_id"] for leg in saved_path])
            if any(leg["edge_id"] not in selected.edges for leg in saved_path):
                raise ValueError("已保存的参考路径缺少区间或线路归属已改变，请重新编排")
            selected.control_nodes.update(entry["node_id"] for entry in sequence[::2])
        else:
            selected = self.selected_library(ids)
        try:
            normalized = self.normalize_sequence(sequence, selected, policy)
            return normalized, selected.resolve(normalized, policy, selection=selection)
        except ValueError:
            if replay or policy != "auto" or not any(
                isinstance(entry, dict) and str(entry.get("node_id", "")).startswith("station:")
                for entry in sequence[2:-1:2]
            ):
                raise
        return self.resolve_station_transfers(sequence, selected)

    def resolve(self, sequence, policy="strict", selection=None):
        return self.resolve_with_sequence(sequence, policy, selection=selection)[1]

    def _station_transfer_edges(self, endpoint, radius_m):
        """RTree and endpoint indexes restrict loading to one station vicinity."""
        sources = self._station_sources(endpoint)
        marks = ",".join("?" for _ in sources)
        with self.connect() as db:
            centres = db.execute(
                f"SELECT DISTINCT source_x,source_y FROM station_aliases WHERE source_id IN ({marks}) "
                "AND source_x IS NOT NULL AND source_y IS NOT NULL", sources).fetchall()
            if not centres:
                nodes = self.endpoint_nodes(endpoint)
                centres = db.execute(
                    f"SELECT x,y FROM nodes WHERE id IN ({','.join('?' for _ in nodes)}) AND x IS NOT NULL AND y IS NOT NULL", nodes).fetchall()
            db.execute("CREATE TEMP TABLE transfer_nodes(id PRIMARY KEY)")
            for x, y in centres:
                dy = radius_m / 110000
                dx = dy / max(0.1, cos(radians(y)))
                rows = db.execute(
                    "SELECT n.id,n.x,n.y FROM node_bounds b JOIN node_spatial s ON s.rowid=b.id "
                    "JOIN nodes n ON n.id=s.node_id WHERE b.minx<=? AND b.maxx>=? AND b.miny<=? AND b.maxy>=?",
                    (x + dx, x - dx, y + dy, y - dy)).fetchall()
                db.executemany("INSERT OR IGNORE INTO transfer_nodes VALUES(?)",
                               ((node,) for node, nx, ny in rows if distance_m([x, y], [nx, ny]) <= radius_m))
            rows = db.execute(
                "SELECT e.id,e.line_id,e.a,e.b,e.construction,e.length_m,e.track_type,e.direction,l.source_name "
                "FROM main.edges e JOIN main.lines l ON l.id=e.line_id "
                "WHERE e.a IN (SELECT id FROM transfer_nodes) AND e.b IN (SELECT id FROM transfer_nodes) "
                "AND e.construction=0 ORDER BY e.id")
            return [{"id": ident, "line_id": self.workspace.canonical(line), "line_name": name,
                     "from_node": a, "to_node": b, "construction": bool(construction),
                     "length_m": length, "track_type": kind, "direction": direction}
                    for ident, line, a, b, construction, length, kind, direction, name in rows]

    def resolve_station_transfers(self, sequence, selected):
        try:
            from .rail_transfer import station_transfer_path
        except ImportError:
            from rail_transfer import station_transfer_path
        if len(sequence) < 5 or len(sequence) % 2 != 1:
            raise ValueError("站内换线通道必须交替填写端点和线路")
        sequence = json.loads(json.dumps(sequence))
        for i, entry in enumerate(sequence):
            required = {"kind", "node_id"} if i % 2 == 0 else {"kind", "line_id"}
            allowed = required if i % 2 == 0 else required | {"section_id"}
            if (not isinstance(entry, dict) or not required <= entry.keys() or not entry.keys() <= allowed
                    or entry["kind"] != ("endpoint" if i % 2 == 0 else "line")):
                raise ValueError("通道表格字段或交替顺序无效")
            if i % 2:
                entry["line_id"] = self.workspace.canonical(entry["line_id"])
        candidates, transfer_stations = [], {}
        for row, entry in enumerate(sequence[::2]):
            index, endpoint = row * 2, entry["node_id"]
            lines = [sequence[i]["line_id"] for i in (index - 1, index + 1) if 0 <= i < len(sequence)]
            if 0 < index < len(sequence) - 1 and isinstance(endpoint, str) and endpoint.startswith("station:"):
                override = self._station_connection_override(self._station_sources(endpoint)) or []
                if any(v.get("anchor_policy") == "fixed" and v.get("line_id") in lines for v in override):
                    # Explicit anchor constraints must not be relaxed by a fallback.
                    choices = self.endpoint_candidates(endpoint, lines)
                else:
                    per_line = [self.endpoint_candidates(endpoint, [line]) for line in lines]
                    choices = list(dict.fromkeys(node for values in per_line for node in values)) if all(per_line) else []
                transfer_stations[row] = endpoint
            else:
                choices = self.endpoint_candidates(endpoint, lines)
            if not choices:
                raise ValueError(f"{self.endpoint_label(endpoint)} 缺少所选线路的有效接轨点，请检查人工接轨设置")
            candidates.append(choices)
        gaps = [self._anchor_distances(entry["node_id"], choices)
                for entry, choices in zip(sequence[::2], candidates)]
        base_edges = {ident: {**edge, "line_id": selected.edge_lines[ident]}
                      for ident, edge in selected.edges.items()}
        # Most crossovers are within 3 km; expand only on failure. No national
        # geometry or unrelated complete mainlines are loaded for connections.
        for radius in (3000, 8000, 20000):
            edges, local = dict(base_edges), {}
            for row, endpoint in transfer_stations.items():
                nearby = self._station_transfer_edges(endpoint, radius)
                local[row] = {edge["id"] for edge in nearby}
                edges.update((edge["id"], edge) for edge in nearby)
            graph = RailLineLibrary(edges.values(), [])
            graph.control_nodes.update(selected.control_nodes)
            graph.split_nodes = selected.split_nodes
            try:
                return station_transfer_path(graph, sequence, candidates, local, gaps)
            except ValueError as error:
                last_error = str(error)
        raise ValueError(last_error + "；已检查换线站周边 20 km 的真实轨道，请核对缺失连接或手工指定中间端点")

    def _anchor_distances(self, endpoint, candidates):
        if not isinstance(endpoint, str) or not endpoint.startswith("station:"):
            return dict.fromkeys(candidates, 0.0)
        sources = self._station_sources(endpoint)
        marks = ",".join("?" for _ in sources)
        with self.connect() as db:
            gaps = dict(db.execute(
                f"SELECT anchor_node,min(distance_m) FROM station_aliases WHERE source_id IN ({marks}) "
                "AND confidence>=0.5 GROUP BY anchor_node", sources))
        for value in self._station_connection_override(sources) or []:
            node = value.get("anchor_node")
            gap = value.get("distance_m")
            if node is not None and isinstance(gap, (int, float)):
                gaps[node] = min(gaps.get(node, float("inf")), max(0.0, gap))
        return {node: max(0.0, gaps.get(node, 2500.0)) for node in candidates}

    def _reference_endpoint_chain(self, sequence, candidates, selected):
        # Rank only COMPLETE reachable chains, not independently nearest anchors.
        # State is bounded by station candidates; graph trees are discarded per start.
        gaps = [self._anchor_distances(entry["node_id"], choices)
                for entry, choices in zip(sequence[::2], candidates)]
        states = {node: ((gaps[0][node], 0.0, (str(node),)), [node]) for node in candidates[0]}
        for row, line in enumerate(sequence[1::2]):
            next_states = {}
            for start, (score, chain) in sorted(states.items(), key=lambda item: str(item[0])):
                distances = {}
                if line.get("section_id"):
                    for end in candidates[row + 1]:
                        try:
                            path = selected.resolve([{"kind": "endpoint", "node_id": start}, line,
                                                     {"kind": "endpoint", "node_id": end}], "auto")
                        except ValueError:
                            continue
                        distances[end] = sum(edge_length(selected.edges[leg["edge_id"]]) for leg in path)
                else:
                    distances, _ = selected.reference_tree(line["line_id"], start, candidates[row + 1])
                for end in candidates[row + 1]:
                    if end == start or end not in distances:
                        continue
                    rank = (score[0] + gaps[row + 1][end], score[1] + distances[end], score[2] + (str(end),))
                    if end not in next_states or rank < next_states[end][0]:
                        next_states[end] = (rank, chain + [end])
            states = next_states
            if not states:
                raise ValueError(f"第 {row + 1} 行所选线路在起终点之间不连通，或手工区间不匹配；请检查接轨、运行方向和线路归属")
        return min(states.values(), key=lambda value: value[0])[1]

    def normalize_sequence(self, sequence, selected=None, policy="strict"):
        if not isinstance(sequence, list) or len(sequence) < 3 or len(sequence) % 2 != 1:
            raise ValueError("通道必须为端点—线路—端点，交替排列")
        sequence = json.loads(json.dumps(sequence))
        ids = []
        for index in range(1, len(sequence), 2):
            entry = sequence[index]
            if not isinstance(entry, dict) or entry.get("kind") != "line" or entry.get("line_id") not in self.lines:
                raise ValueError("铁路线编号不存在，请先载入相应的铁路数据")
            entry["line_id"] = self.workspace.canonical(entry["line_id"])
            ids.append(entry["line_id"])
        candidates = []
        for index in range(0, len(sequence), 2):
            entry = sequence[index]
            if not isinstance(entry, dict) or entry.get("kind") != "endpoint" or "node_id" not in entry:
                raise ValueError("通道端点字段或交替顺序无效")
            adjacent = []
            if index > 0 and sequence[index - 1].get("kind") == "line":
                adjacent.append(sequence[index - 1].get("line_id"))
            if index + 1 < len(sequence) and sequence[index + 1].get("kind") == "line":
                adjacent.append(sequence[index + 1].get("line_id"))
            choices = self.endpoint_candidates(entry["node_id"], adjacent)
            if not choices:
                raise ValueError(f"{self.endpoint_label(entry['node_id'])} 没有连接所选线路的有效轨道端点；请检查接轨线路或使用手工拆分、组合修正归属")
            candidates.append(choices)
        if all(len(values) == 1 for values in candidates):
            for entry, values in zip(sequence[::2], candidates):
                entry["node_id"] = values[0]
            return sequence
        selected = selected or self.selected_library(ids)
        if policy == "auto":
            chain = self._reference_endpoint_chain(sequence, candidates, selected)
            for entry, node in zip(sequence[::2], chain):
                entry["node_id"] = node
            return sequence
        # Dynamic programming counts compatible endpoint chains (capped at two).
        # No geometric distance ranking and no cartesian-product enumeration.
        states = {node: (1, [node]) for node in candidates[0]}
        for row, line_id in enumerate(ids):
            next_states = {}
            graph = selected.lines[line_id]["graph"]
            for start, (count, chain) in states.items():
                reachable, stack = {start}, [start]
                while stack:
                    for other, edge_id, direction in graph.get(stack.pop(), []):
                        edge = selected.edges[edge_id]
                        if edge.get("construction") or not traversal_allowed(edge, direction):
                            continue
                        if other not in reachable:
                            reachable.add(other)
                            stack.append(other)
                for end in candidates[row + 1]:
                    if end == start or end not in reachable:
                        continue
                    old_count = next_states.get(end, (0, None))[0]
                    next_states[end] = (min(2, old_count + count), chain + [end])
            states = next_states
            if not states:
                raise ValueError(f"第 {row + 1} 行所选线路在起终点之间不连通（已检查接轨点和运行方向）；请用拆分、组合修正线路归属，不能跨越实际断轨")
        if sum(count for count, chain in states.values()) != 1:
            detail = "；".join(
                self.endpoint_label(sequence[index * 2]["node_id"]) + "：" + "、".join(map(str, values[:6]))
                for index, values in enumerate(candidates) if len(values) > 1
            )
            raise ValueError("存在多个可达接轨端点，严格径路 unresolved；可改用「自动选择可走通的参考路径」，或勾选「手工调整」指定道岔/轨道端点。" + detail)
        chain = next(iter(states.values()))[1]
        for entry, node in zip(sequence[::2], chain):
            entry["node_id"] = node
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
                    "SELECT node_id FROM main.line_nodes GROUP BY node_id "
                    "HAVING count(DISTINCT line_id)>1"
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
                    metadata = self.metadata.get(line_id, {})
                    if metadata.get("track_type"):
                        section["track_type"] = metadata["track_type"]
                        section["type_evidence"] = (
                            "用户工作区分类覆盖；原始依据："
                            + section.get("type_evidence", "待核对")
                        )
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
                stream.write('{"schema":"railscope.rail-graph.v1",')
                generators = [
                    (
                        "lines",
                        (
                            {
                                "id": key,
                                "name": self.line_name(key, name),
                                "source_name": name,
                                "edge_count": count,
                                "track_type": self.metadata.get(key, {}).get("track_type", kind),
                                "type_evidence": (
                                    "用户工作区分类覆盖；原始依据：" + evidence
                                    if self.metadata.get(key, {}).get("track_type")
                                    and self.metadata[key]["track_type"] != kind
                                    else evidence
                                ),
                            }
                            for key, name, count, kind, evidence in db.execute(
                                "SELECT id,source_name,edge_count,track_type,evidence FROM lines ORDER BY id"
                            )
                        ),
                    ),
                    (
                        "station_entities",
                        (
                            {
                                "id": "station:" + row[0],
                                "name": row[1],
                                "anchor_node_ids": sorted([
                                    int(value) if value.isdigit() else value
                                    for value in row[2].split(",")
                                ], key=str)
                                if row[2]
                                else [],
                                "connected_line_ids": sorted(set(row[3].split(","))) if row[3] else [],
                                "verification_status": row[4],
                                "confidence": row[5],
                            }
                            for row in db.execute(
                                "SELECT a.source_id,min(a.alias),group_concat(DISTINCT a.anchor_node),"
                                "group_concat(DISTINCT ln.line_id),min(a.verification_status),max(a.confidence) "
                                "FROM station_aliases a LEFT JOIN line_nodes ln ON ln.node_id=a.anchor_node "
                                "GROUP BY a.source_id ORDER BY min(a.alias),a.source_id"
                            )
                        ),
                    ),
                    (
                        "endpoints",
                        (
                            {
                                "node_id": row[0],
                                "name": self.node_label(row[:3]),
                                "kind": row[2],
                                "coordinates": [row[3], row[4]]
                                if row[3] is not None and row[4] is not None
                                else None,
                                "connected_line_ids": sorted(set(row[5].split(","))) if row[5] else [],
                            }
                            for row in db.execute(
                                "SELECT n.id,n.label,n.kind,n.x,n.y,group_concat(DISTINCT ln.line_id) "
                                "FROM nodes n LEFT JOIN line_nodes ln ON ln.node_id=n.id "
                                "GROUP BY n.id ORDER BY n.id"
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
                stream.write(
                    ',"corridor_format":{"schema":"railscope.rail-corridors.v2",'
                    '"sequence":"endpoint-line-endpoint-line-endpoint",'
                    '"path_cache":"ordered NetworkEdge ids with direction",'
                    '"train_stops":"stored only in TrainRun.stops"}}'
                )
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
