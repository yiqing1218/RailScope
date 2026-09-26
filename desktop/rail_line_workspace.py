"""Effective line membership, layered over immutable physical infrastructure.

Only the small set of grouped line IDs is held in memory. Geometry and edges
stay in SQLite; source IDs remain valid aliases and are never renumbered.
"""

from collections import defaultdict
import hashlib
import json


ASSEMBLY_PREFIX = "line-assembly:"
MEMBERSHIP_KEY = "railscope.org/line-membership"


def membership_targets(value):
    """Validate a portable membership snapshot shared by both DTO adapters."""
    if value is None:
        return {}
    if not isinstance(value, dict) or not isinstance(value.get("groups", {}), dict):
        raise ValueError("通道线路归属快照无效")
    if (not isinstance(value.get("names", {}), dict)
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.get("names", {}).items())):
        raise ValueError("通道线路名称快照无效")
    if "version" in value and not isinstance(value["version"], str):
        raise ValueError("通道线路归属版本无效")
    targets = {}
    for ident, members in value.get("groups", {}).items():
        if not isinstance(ident, str) or not ident or not isinstance(members, list) or not members:
            raise ValueError("通道线路归属快照无效")
        for member in members:
            if not isinstance(member, str) or not member:
                raise ValueError("通道线路归属成员无效")
            if member in targets and targets[member] != ident:
                raise ValueError("通道线路归属 conflict：同一源线路被重复分配")
            targets[member] = ident
    return targets


def grouping_key(edge):
    tags = edge.get("way_tags", {})
    name = tags.get("name") or tags.get("full_name")
    # Explicit domain identities, yards, unnamed ways and inactive tracks must
    # never be conflated merely because their display names happen to match.
    if (edge.get("line_id") or not name or tags.get("service", "main") != "main"
            or edge.get("construction")
            or edge.get("construction_status", "operating") != "operating"
            or tags.get("railway", "rail") not in {"rail", "narrow_gauge"}):
        return None
    return json.dumps([name, tags.get("ref", ""), tags.get("railway", "rail")], ensure_ascii=False)


def build_groups(db):
    """Join tag variants only at shared physical nodes, never by proximity."""
    parent = {}

    def root(key):
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    db.executescript("""
        CREATE TEMP TABLE grouping_candidates AS SELECT line_id FROM grouping_keys WHERE group_key IN
            (SELECT group_key FROM grouping_keys GROUP BY group_key HAVING count(*)>1);
        CREATE TEMP TABLE grouping_nodes(line_id TEXT,node_id,PRIMARY KEY(line_id,node_id));
        INSERT OR IGNORE INTO grouping_nodes
            SELECT line_id,a FROM edges WHERE construction=0 AND line_id IN (SELECT line_id FROM grouping_candidates);
        INSERT OR IGNORE INTO grouping_nodes
            SELECT line_id,b FROM edges WHERE construction=0 AND line_id IN (SELECT line_id FROM grouping_candidates);
    """)
    pairs = db.execute(
        "SELECT DISTINCT a.line_id,b.line_id FROM grouping_keys ka "
        "JOIN grouping_keys kb ON ka.group_key=kb.group_key AND ka.line_id<kb.line_id "
        "JOIN grouping_nodes a ON a.line_id=ka.line_id "
        "JOIN grouping_nodes b ON b.line_id=kb.line_id AND b.node_id=a.node_id"
    )
    for a, b in pairs:
        ra, rb = root(a), root(b)
        parent[max(ra, rb)] = min(ra, rb)
    db.execute("CREATE TABLE line_groups(line_id TEXT PRIMARY KEY,group_id TEXT NOT NULL)")
    db.executemany("INSERT INTO line_groups VALUES(?,?)", ((key, root(key)) for key in parent))
    db.execute("CREATE INDEX line_group_members ON line_groups(group_id)")
    db.execute("DROP TABLE grouping_nodes")
    db.execute("DROP TABLE grouping_candidates")


class LineWorkspace:
    def __init__(self, db, metadata):
        self.targets, self.groups, self.retired, self.conflicts = {}, {}, {}, []
        auto = defaultdict(list)
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='line_groups'").fetchone():
            for member, group in db.execute("SELECT line_id,group_id FROM line_groups"):
                custom = metadata.get(member, {})
                if not custom.get("separate_catalog_entry") and not custom.get("assembly_id"):
                    auto[group].append(member)
        for members in auto.values():
            if len(members) > 1:
                self.groups[min(members)] = sorted(members)
        manual = defaultdict(list)
        self.names = {}
        for key, value in metadata.items():
            if value.get("assembly_id"):
                manual[value["assembly_id"]].append(key)
                self.names[value["assembly_id"]] = value.get("assembly_name") or value["assembly_id"]
            if key.startswith(ASSEMBLY_PREFIX) and isinstance(value.get("members"), list):
                ident = key.removeprefix(ASSEMBLY_PREFIX)
                self.retired[ident] = value["members"]
                self.names[ident] = value.get("name") or ident
        for ident, members in manual.items():
            missing = [key for key in members if not db.execute("SELECT 1 FROM lines WHERE id=?", (key,)).fetchone()]
            if missing:
                self.conflicts.append({"line_id": ident, "missing_members": missing})
                continue
            self.groups[ident] = sorted(members)
        for ident, members in self.groups.items():
            for key in members:
                self.targets[key] = ident

    def canonical(self, ident):
        return self.targets.get(ident, ident)

    def members(self, ident):
        ident = self.canonical(ident)
        return self.groups.get(ident, self.retired.get(ident, [ident]))

    def install(self, db):
        if not self.groups:
            return
        db.executescript("""
            CREATE TEMP TABLE workspace_members(source_id TEXT PRIMARY KEY,target_id TEXT NOT NULL);
            CREATE INDEX temp.workspace_targets ON workspace_members(target_id);
            CREATE TEMP TABLE workspace_lines(id TEXT PRIMARY KEY,source_name TEXT,edge_count INTEGER,track_type TEXT,evidence TEXT);
        """)
        db.executemany("INSERT INTO workspace_members VALUES(?,?)", self.targets.items())
        db.execute(
            "INSERT INTO workspace_lines SELECT m.target_id,min(l.source_name),sum(l.edge_count),"
            "CASE WHEN count(DISTINCT l.track_type)=1 THEN min(l.track_type) ELSE '组合铁路线' END,"
            "'共享物理端点的线路归属；原始分类保留' "
            "FROM workspace_members m JOIN main.lines l ON l.id=m.source_id GROUP BY m.target_id"
        )
        db.executemany("UPDATE workspace_lines SET source_name=?,evidence='用户工作区线路组合' WHERE id=?",
                       ((name, key) for key, name in self.names.items()))
        # UNION ALL lets SQLite push line_id predicates into both branches and
        # use edge_line / the line_nodes primary key, without a national scan.
        db.executescript("""
            CREATE TEMP VIEW lines AS
                SELECT * FROM main.lines WHERE id NOT IN (SELECT source_id FROM workspace_members)
                UNION ALL SELECT * FROM workspace_lines;
            CREATE TEMP VIEW edges AS
                SELECT * FROM main.edges WHERE line_id NOT IN (SELECT source_id FROM workspace_members)
                UNION ALL SELECT e.id,m.target_id,e.a,e.b,e.construction,e.length_m,e.track_type,e.evidence,e.source_way,e.direction
                FROM workspace_members m JOIN main.edges e ON e.line_id=m.source_id;
            CREATE TEMP VIEW line_nodes AS
                SELECT * FROM main.line_nodes WHERE line_id NOT IN (SELECT source_id FROM workspace_members)
                UNION ALL SELECT m.target_id,n.node_id FROM workspace_members m
                JOIN main.line_nodes n ON n.line_id=m.source_id;
        """)

    def provenance(self, ids, snapshot):
        groups = {key: list(self.members(key)) for key in dict.fromkeys(ids)
                  if len(self.members(key)) > 1 or key.startswith("RLU-")}
        return {
            "source": "workspace_and_indexed_shared_nodes", "snapshot": snapshot,
            "verification_status": "topology_checked_not_dispatch_verified",
            "groups": groups,
            "version": hashlib.sha256(json.dumps(groups, sort_keys=True).encode()).hexdigest()[:16],
        }
