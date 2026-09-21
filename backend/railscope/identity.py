"""Persistent source-to-domain identity registry; never silently rebind old paths.

Preparation is read-only. Commit only after the derived dataset was successfully
written, so a failed extraction cannot advance the identity snapshot.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import sqlite3
from uuid import uuid4

from .domain import DatasetSnapshot


def new_id(prefix):
    return prefix + "-" + uuid4().hex


def construction_status(tags, construction=False):
    railway = tags.get("railway")
    if construction or railway == "construction":
        return "construction"
    if railway in ("proposed", "planned"):
        return "planned"
    if railway in ("disused", "abandoned", "razed"):
        return "disused"
    return "operating" if railway in ("rail", "subway", "light_rail", "tram") else "unknown"


def _near_geometry(a, b, tolerance_m=8):
    """Symmetric vertex-to-polyline distance, independent of added shape vertices."""
    lat = math.radians(a[0][1])
    sx, sy = 111320 * math.cos(lat), 111320
    def distance(p, q, r):
        x, y = (p[0]-q[0])*sx, (p[1]-q[1])*sy
        dx, dy = (r[0]-q[0])*sx, (r[1]-q[1])*sy
        t = max(0, min(1, (x*dx+y*dy)/(dx*dx+dy*dy))) if dx or dy else 0
        return math.hypot(x-t*dx, y-t*dy)
    return all(min(distance(p, q, r) for q, r in zip(target, target[1:])) <= tolerance_m
               for source, target in ((a,b),(b,a)) for p in source)


@dataclass
class IdentityImport:
    snapshot: DatasetSnapshot
    previous_snapshot_id: str | None
    edges: list[dict]
    nodes: dict[str, dict]
    changes: list[dict]
    conflicts: list[dict]


class IdentityRegistry:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS identity_snapshots(id TEXT PRIMARY KEY,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS identity_edges(snapshot_id TEXT,id TEXT,data TEXT NOT NULL,PRIMARY KEY(snapshot_id,id));
                CREATE TABLE IF NOT EXISTS identity_nodes(source_id TEXT PRIMARY KEY,id TEXT NOT NULL,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS identity_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS identity_changes(snapshot_id TEXT,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS identity_aliases(
                    kind TEXT NOT NULL, source_id TEXT NOT NULL, id TEXT NOT NULL,
                    PRIMARY KEY(kind,source_id), UNIQUE(kind,id));
            """)

    def resolve_alias(self, kind, source_id, prefix, db=None):
        """Resolve a non-node source/business alias to a persistent RailScope ID."""
        if not all(isinstance(value, str) and value.strip() for value in (kind, source_id, prefix)):
            raise ValueError("Identity alias fields cannot be empty")
        if db is None:
            with closing(sqlite3.connect(self.path)) as connection, connection:
                return self.resolve_alias(kind, source_id, prefix, connection)
        row = db.execute(
            "SELECT id FROM identity_aliases WHERE kind=? AND source_id=?",
            (kind, source_id),
        ).fetchone()
        if row:
            return row[0]
        ident = new_id(prefix)
        db.execute("INSERT INTO identity_aliases VALUES(?,?,?)", (kind, source_id, ident))
        return ident

    def resolve_node(self, source_alias, coordinates, mode="metro", db=None):
        """Get a canonical node ID; caller may reuse its transaction connection."""
        source_alias = str(source_alias).replace("osm:node:", "osm/node/")
        if not source_alias.startswith("osm/node/"):
            raise ValueError("Expected an OSM node source alias")
        if len(coordinates) != 2 or not all(math.isfinite(value) for value in coordinates):
            raise ValueError("Node coordinates must be two finite numbers")
        if not -180 <= coordinates[0] <= 180 or not -90 <= coordinates[1] <= 90:
            raise ValueError("Node coordinates are out of bounds")
        if db is None:
            with closing(sqlite3.connect(self.path)) as connection, connection:
                return self.resolve_node(source_alias, coordinates, mode, connection)
        row = db.execute("SELECT id,data FROM identity_nodes WHERE source_id=?", (source_alias,)).fetchone()
        node = json.loads(row[1]) if row else {"id": new_id("NN"), "source_id": source_alias}
        node.update(coordinates=list(coordinates), mode=mode)
        db.execute("INSERT INTO identity_nodes VALUES(?,?,?) ON CONFLICT(source_id) DO UPDATE SET data=excluded.data",
                   (source_alias, node["id"], json.dumps(node, ensure_ascii=False)))
        return node["id"]

    def prepare(self, source_edges, snapshot):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT value FROM identity_meta WHERE key='active'").fetchone()
            previous = row[0] if row else None
            old = [json.loads(r[0]) for r in db.execute("SELECT data FROM identity_edges WHERE snapshot_id=?", (previous,))]
            nodes = {key: json.loads(raw) for key, raw in db.execute("SELECT source_id,data FROM identity_nodes")}
        current_nodes = {}
        endpoint_index = defaultdict(list)
        for edge in old:
            endpoint_index[frozenset((edge['from_node_id'],edge['to_node_id']))].append(edge)
        used, output, changes, conflicts = set(), [], [], []
        # Source node IDs remain aliases, never business references.
        for original in source_edges:
            edge = deepcopy(original)
            for key, index in (("from_node",0),("to_node",-1)):
                alias = "osm/node/" + str(edge[key])
                node = nodes.get(alias)
                coords = edge['coordinates'][index]
                if node is None:
                    node = {"id": new_id("NN"), "coordinates":coords, "source_id":alias}
                else:
                    node = {**node, "coordinates": coords}
                current_nodes[alias] = node
                nodes[alias] = node
                edge[key+'_id'] = node['id']
            source_key = edge.get('source_edge_id',edge['id'])
            source_way = str(edge.get('osm_way_id') or source_key.split(':')[0].removeprefix('w'))
            candidates = [e for e in endpoint_index[frozenset((edge['from_node_id'],edge['to_node_id']))]
                          if e['id'] not in used and _near_geometry(e['coordinates'],edge['coordinates'])]
            if not candidates:
                candidates = [
                    e for e in old
                    if e['id'] not in used and str(e.get('osm_way_id')) == source_way
                    and _near_geometry(e['coordinates'], edge['coordinates'])
                ]
            if len(candidates)>1:
                # Exact source evidence can distinguish genuinely parallel tracks.
                exact = [e for e in candidates if e.get('source_edge_id')==source_key]
                if len(exact)==1:
                    candidates=exact
            if len(candidates)==1:
                match=candidates[0]
                edge['id']=match['id']
                used.add(match['id'])
                status='unchanged' if match['coordinates']==edge['coordinates'] else 'geometry_changed'
                reversed_orientation = (
                    edge['from_node_id'] == match['to_node_id']
                    and edge['to_node_id'] == match['from_node_id']
                )
                if {
                    edge['from_node_id'], edge['to_node_id']
                } != {match['from_node_id'], match['to_node_id']}:
                    direct = sum(
                        (a-b)**2
                        for a,b in zip(edge['coordinates'][0], match['coordinates'][0])
                    ) + sum(
                        (a-b)**2
                        for a,b in zip(edge['coordinates'][-1], match['coordinates'][-1])
                    )
                    reverse = sum(
                        (a-b)**2
                        for a,b in zip(edge['coordinates'][0], match['coordinates'][-1])
                    ) + sum(
                        (a-b)**2
                        for a,b in zip(edge['coordinates'][-1], match['coordinates'][0])
                    )
                    reversed_orientation = reverse < direct
                if reversed_orientation:
                    # Preserve canonical orientation so old forward/reverse references stay valid.
                    edge['from_node'],edge['to_node']=edge['to_node'],edge['from_node']
                    edge['coordinates'].reverse()
                    edge['node_ids']=list(reversed(edge.get('node_ids',[])))
                    if edge.get('direction') == 'forward':
                        edge['direction'] = 'reverse'
                    elif edge.get('direction') == 'reverse':
                        edge['direction'] = 'forward'
                # A source node may be replaced while remaining the same physical
                # endpoint. Bind its new source alias to the existing RailScope ID.
                for key, canonical in (("from_node", match['from_node_id']), ("to_node", match['to_node_id'])):
                    alias = "osm/node/" + str(edge[key])
                    current_nodes[alias]['id'] = canonical
                    nodes[alias]['id'] = canonical
                    edge[key+'_id'] = canonical
                edge['line_id']=match.get('line_id')
            else:
                edge['id']=new_id('NE')
                status='added'
                if candidates:
                    conflicts.append({'kind':'ambiguous_match','source_edge_id':source_key,'candidates':[e['id'] for e in candidates]})
            edge.update(source_edge_id=source_key,source='osm',snapshot_id=snapshot.id,
                        verification_status='OSM-derived',source_version=snapshot.version)
            edge['osm_way_id']=source_way
            edge['construction_status']=construction_status(edge.get('way_tags',{}), edge.get('construction',False))
            changes.append({'status':status,'old_ids':[edge['id']] if status!='added' else [],'new_ids':[edge['id']]})
            output.append(edge)
        # Line IDs are durable entities; matching physical edges keep prior memberships.
        groups={}
        for e in old:
            tags=e.get('way_tags',{})
            key=(tags.get('name') or tags.get('ref') or str(e.get('osm_way_id','')),tags.get('service','main'))
            if e.get('line_id'):
                groups.setdefault(key,e['line_id'])
        for e in output:
            tags=e.get('way_tags',{})
            key=(tags.get('name') or tags.get('ref') or str(e['osm_way_id']),tags.get('service','main'))
            if e.get('line_id'):
                groups.setdefault(key,e['line_id'])
        for e in output:
            tags=e.get('way_tags',{})
            key=(tags.get('name') or tags.get('ref') or str(e['osm_way_id']),tags.get('service','main'))
            e['line_id']=e.get('line_id') or groups.setdefault(key,new_id('IL'))
            e['line_name']=tags.get('name') or tags.get('ref') or '未命名轨道'
        added=[e for e in output if e['id'] not in used]
        for e in old:
            if e['id'] in used:
                continue
            old_nodes=set(map(str,e.get('node_ids',[])))
            related=[n for n in added if len(old_nodes.intersection(map(str,n.get('node_ids',[]))))>=2]
            status='split' if len(related)>1 else 'merged' if related else 'removed'
            record={'status':status,'old_ids':[e['id']],'new_ids':[n['id'] for n in related]}
            changes.append(record)
            conflicts.append({**record,'kind':'reference_migration_required'})
        return IdentityImport(snapshot,previous,output,current_nodes,changes,conflicts)

    def commit(self, prepared):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT value FROM identity_meta WHERE key='active'").fetchone()
            if (row[0] if row else None)!=prepared.previous_snapshot_id:
                raise ValueError('基础设施版本已变化，请重新准备导入')
            snap=prepared.snapshot
            db.execute('INSERT INTO identity_snapshots VALUES(?,?)',(snap.id,json.dumps(asdict(snap))))
            db.executemany('INSERT INTO identity_edges VALUES(?,?,?)',((snap.id,e['id'],json.dumps(e,ensure_ascii=False)) for e in prepared.edges))
            db.executemany('INSERT OR REPLACE INTO identity_nodes VALUES(?,?,?)',((key,n['id'],json.dumps(n)) for key,n in prepared.nodes.items()))
            db.executemany('INSERT INTO identity_changes VALUES(?,?)',((snap.id,json.dumps(c)) for c in prepared.changes))
            db.execute("INSERT OR REPLACE INTO identity_meta VALUES('active',?)",(snap.id,))

    def snapshot_edges(self, snapshot_id):
        with closing(sqlite3.connect(self.path)) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM identity_edges WHERE snapshot_id=?',(snapshot_id,))]
