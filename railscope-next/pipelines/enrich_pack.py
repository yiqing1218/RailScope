"""Build a new immutable pack retaining relevant OSM primitives and source geometry.

The input is a compatible canonical snapshot and the same licensed PBF. Static
PMTiles are shared by content; source-detail tiles are generated from SQLite.
"""
import argparse
import json
import shutil
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path

import osmium
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import polygonize

from build_pack import Canonical, checksum, json_text
from osm_source import Source

SCHEMA = Path(__file__).with_name("source_v3.sql")
MODE = {"subway", "light_rail", "tram", "monorail"}
REF_TYPES = {"n": "node", "w": "way", "r": "relation"}
UPSERT_SOURCE = """INSERT INTO source_object VALUES(?,?,?) ON CONFLICT(ref)
    DO UPDATE SET source_version=excluded.source_version,tags=excluded.tags"""


def classify(tags):
    railway, highway = tags.get("railway"), tags.get("highway")
    transport = tags.get("public_transport")
    if railway == "platform" or transport == "platform" and (
        tags.get("train") == "yes" or tags.get("subway") == "yes" or tags.get("tram") == "yes"
    ):
        return "facility", "platform"
    if tags.get("building") == "train_station":
        return "facility", "station_building"
    if tags.get("landuse") == "railway":
        return "facility", "station_area"
    if railway == "yard":
        return "facility", "yard"
    if railway == "monorail":
        return "metro", "monorail"
    if highway in {"services", "rest_area"}:
        return "poi", highway
    if highway in {"trunk", "primary", "secondary", "tertiary"} or highway in {"residential", "unclassified"} and "name" in tags:
        return "base_road", highway
    if tags.get("natural") in {"water", "wetland"} or tags.get("waterway") in {"river", "canal", "riverbank"}:
        return "base_water", tags.get("natural") or tags["waterway"]
    if tags.get("landuse") in {"forest", "grass", "recreation_ground"} or tags.get("leisure") in {"park", "nature_reserve"}:
        return "base_green", tags.get("landuse") or tags["leisure"]
    if "building" in tags and ("name" in tags or tags.get("amenity") in {"school", "hospital"}):
        return "base_building", tags["building"]
    if tags.get("place") in {"city","town","village","suburb","county","province","state"}:
        return "base_place", tags["place"]
    if tags.get("boundary") == "administrative" and tags.get("admin_level") in {"2","3","4","5","6","7"}:
        return "base_admin", tags["admin_level"]
    return None


def primitives(osm, bits, *filters):
    stream = osmium.FileProcessor(osm, entities=bits, thread_pool=osmium.io.ThreadPool(4))
    for predicate in filters:
        stream.with_filter(predicate)
    return stream


def insert_base(db, record, kind, category, geom):
    if geom.is_empty or not geom.is_valid:
        return
    db.execute("INSERT OR IGNORE INTO source_object VALUES(?,?,?)", (record.ref, record.version, json_text(record.tags)))
    name = record.tags.get("name:zh") or record.tags.get("name") or ""
    row = db.execute("INSERT INTO base_feature(source_ref,kind,class,name,wkb) VALUES(?,?,?,?,?)",
                     (record.ref,kind,category,name,geom.wkb)).lastrowid
    west,south,east,north=geom.bounds
    db.execute("INSERT INTO base_spatial VALUES(?,?,?,?,?)", (row,west,east,south,north))


def save_geometry(db, store, record, description, geom):
    kind, category = description
    if kind.startswith("base_"):
        insert_base(db, record, kind, category, geom)
        return
    identity = store.db.execute("SELECT id FROM entity WHERE source_ref=? AND kind=? LIMIT 1", (record.ref, kind)).fetchone()
    if identity:
        return
    store.entity(record, f"object:{record.ref}", (kind,category,"unknown"), geom)


def fetch_way(db, way_id):
    row = db.execute("SELECT node_ids FROM source_way WHERE osm_id=?", (way_id,)).fetchone()
    if not row:
        return None
    coordinates = db.execute("""SELECT n.longitude,n.latitude FROM json_each(?) p
        LEFT JOIN source_node n ON n.osm_id=p.value ORDER BY CAST(p.key AS INTEGER)""", row).fetchall()
    return coordinates if len(coordinates)>1 and all(x is not None for x,_ in coordinates) else None


def add_relations(db, osm, stage, stats):
    required = set()
    colors = {}
    for obj in primitives(osm, osmium.osm.RELATION):
        tags = dict(obj.tags)
        route = tags.get("route")
        master = tags.get("route_master")
        desc = classify(tags)
        area = tags.get("type") in {"multipolygon","boundary"} and desc and desc[0] not in {"metro","base_road","base_place"}
        related = route in MODE | {"railway","train","road"} or master in MODE or tags.get("public_transport") in {"stop_area","stop_area_group"}
        if not area and not related:
            continue
        ref = f"osm:relation:{obj.id}"
        members = [(f"osm:{REF_TYPES[m.type]}:{m.ref}",m.role) for m in obj.members if m.type in REF_TYPES]
        db.execute(UPSERT_SOURCE, (ref,obj.version,json_text(tags)))
        db.executemany("INSERT INTO source_relation_member VALUES(?,?,?,?)",
                       ((ref,ordinal,member,role) for ordinal,(member,role) in enumerate(members)))
        if area:
            required.update(int(member.rsplit(":",1)[1]) for member,role in members
                            if member.startswith("osm:way:") and role in {"outer","inner",""})
            stage.execute("INSERT INTO new_areas VALUES (?,?,?,?)", (ref,obj.version,json_text(tags),json_text(members)))
        if route in MODE or master in MODE:
            colors[ref]=(tags,members)
        stats["relations"]+=1
        if stats["relations"]%20000==0:
            print(f"Raw relations {stats['relations']:,}",flush=True)
    return required, colors


def add_ways(db, osm, stage, needed_ways, stats):
    vertices=set()
    local=stage.execute("SELECT setting FROM options").fetchone()[0]
    retained={int(ref[8:]) for (ref,) in db.execute("SELECT ref FROM source_object WHERE ref LIKE 'osm:way:%'")}
    for obj in primitives(osm, osmium.osm.WAY):
        relevant=obj.id in retained or obj.id in needed_ways
        tags = dict(obj.tags)
        desc=classify(tags)
        if not relevant and not desc:
            continue
        if not relevant and desc[0]=="base_road" and desc[1] in {"residential","unclassified"} and not local:
            continue
        ids=[n.ref for n in obj.nodes]
        if len(ids)<2: continue
        vertices.update(ids)
        db.execute("INSERT INTO source_way VALUES(?,?)", (obj.id,json_text(ids)))
        ref=f"osm:way:{obj.id}"
        db.execute(UPSERT_SOURCE, (ref,obj.version,json_text(tags)))
        if desc and (desc[0].startswith("base_") or obj.id not in retained):
            stage.execute("INSERT INTO new_ways VALUES(?,?,?,?)", (obj.id,obj.version,json_text(tags),json_text(desc)))
        stats["ways"]+=1
        if stats["ways"]%250000==0:
            print(f"Raw ways {stats['ways']:,}",flush=True)
    vertices.update(int(ref[9:]) for (ref,) in db.execute("SELECT ref FROM source_object WHERE ref LIKE 'osm:node:%'"))
    return vertices


def add_nodes(db, osm, node_filter, stats):
    def rows():
        for node in primitives(osm, osmium.osm.NODE, node_filter):
            if node.location.valid():
                stats["nodes"]+=1
                if stats["nodes"] % 5_000_000 == 0:
                    print(f"Raw nodes {stats['nodes']:,}", flush=True)
                yield node.id,node.version,node.location.lon,node.location.lat,json_text(dict(node.tags))
    db.executemany("INSERT INTO source_node VALUES(?,?,?,?,?)",rows())
    db.execute("""UPDATE source_object AS s SET (source_version,tags)=(
         SELECT source_version,tags FROM source_node WHERE osm_id=CAST(substr(s.ref,10) AS INTEGER))
         WHERE s.ref LIKE 'osm:node:%' AND EXISTS(
         SELECT 1 FROM source_node WHERE osm_id=CAST(substr(s.ref,10) AS INTEGER))""")
    for place in primitives(osm, osmium.osm.NODE, osmium.filter.TagFilter(
            *[("place",x) for x in ("city","town","village","suburb","county","province","state")])):
        if not place.location.valid(): continue
        tags=dict(place.tags)
        if not (tags.get("name:zh") or tags.get("name")): continue
        record=Source(f"osm:node:{place.id}",place.version,tags)
        insert_base(db,record,"base_place",tags["place"],Point(place.location.lon,place.location.lat))
        db.execute("INSERT OR IGNORE INTO source_node VALUES(?,?,?,?,?)",
                   (place.id,place.version,place.location.lon,place.location.lat,json_text(tags)))


def areas_and_ways(db, stage, stats):
    store=Canonical(db)
    for index, (osm_id, version, tags, desc) in enumerate(stage.execute("SELECT * FROM new_ways"), 1):
        if index % 250_000 == 0:
            print(f"Assembled ways {index:,}", flush=True)
        parts=fetch_way(db,osm_id)
        if not parts:
            stats["missing_way_coordinates"]+=1
            continue
        tags,desc=json.loads(tags),json.loads(desc)
        record=Source(f"osm:way:{osm_id}",version,tags)
        area=desc[0] in {"facility","poi","base_water","base_green","base_building","base_admin"}
        if area and len(parts)>=4 and parts[0]==parts[-1] and tags.get("area")!="no":
            geom=Polygon(parts)
            if not geom.is_valid:
                stats["invalid_way_area"]+=1
                if desc[0] in {"base_building","base_green"}: continue
                geom=LineString(parts)
        else:
            if desc[0] in {"base_green","base_building"}: continue
            geom=LineString(parts)
        save_geometry(db,store,record,desc,geom)
    for ref,version,tags,members in stage.execute("SELECT * FROM new_areas"):
        tags=json.loads(tags)
        outer,inner=[],[]
        valid=True
        for member,role in json.loads(members):
            if not member.startswith("osm:way:") or role not in {"outer","inner",""}: continue
            points=fetch_way(db,int(member.rsplit(":",1)[1]))
            if points is None:
                valid=False
                break
            (inner if role=="inner" else outer).append(LineString(points))
        if not valid or not outer:
            stats["unresolved_area"]+=1
            continue
        shells=list(polygonize(outer))
        holes=list(polygonize(inner))
        if not shells or any(not any(shell.covers(hole) for shell in shells) for hole in holes):
            stats["unresolved_area"]+=1
            continue
        parts=[Polygon(shell.exterior.coords,[hole.exterior.coords for hole in holes if shell.covers(hole)]) for shell in shells]
        geom=parts[0] if len(parts)==1 else MultiPolygon(parts)
        if not geom.is_valid:
            stats["invalid_relation_area"]+=1
            continue
        save_geometry(db,store,Source(ref,version,tags),classify(tags),geom)


def metro_members(db, historical):
    by_route={}
    for master,(tags,members) in historical.items():
        if tags.get("route_master") not in MODE: continue
        for route,_ in members:
            if route in historical: by_route.setdefault(route,[]).append(master)
    for route,(tags,members) in historical.items():
        if tags.get("route") not in MODE: continue
        line=db.execute("SELECT id FROM entity WHERE source_ref=? AND kind='line'",(route,)).fetchone()
        if not line: continue
        # Preserve child line ID for editing, and identify the parent source of its color.
        parents=by_route.get(route,[])
        parent=parents[0] if len(parents)==1 else None
        colour=historical[parent][0].get('colour') if parent else None
        source=parent if colour else route
        colour=colour or tags.get('colour')
        db.execute("""INSERT OR IGNORE INTO metro_service_track
            SELECT lm.member_id,lm.line_id,?,?,? FROM line_member lm
            JOIN edge ON edge.id=lm.member_id JOIN entity ON entity.id=edge.id
            WHERE lm.line_id=? AND entity.kind='metro'""",(route,colour,source,line[0]))


def enrich(pack, osm, output, snapshot):
    import re
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}",snapshot):
        raise ValueError("Invalid snapshot ID")
    manifest=json.loads((pack/"manifest.json").read_text(encoding="utf-8"))
    if checksum(osm)!=manifest["source_sha256"]:
        raise ValueError("PBF does not match original snapshot hash")
    target=output/snapshot
    if target.exists(): raise FileExistsError(target)
    stats=Counter()
    with tempfile.TemporaryDirectory(dir=output,prefix=".source-v3-") as root:
        directory=Path(root)/snapshot
        directory.mkdir()
        for name in ("canonical.sqlite","transport.pmtiles"):
            shutil.copyfile(pack/name,directory/name)
        with closing(sqlite3.connect(directory/"canonical.sqlite")) as db, closing(sqlite3.connect(Path(root)/"staging.sqlite")) as stage:
            db.execute("PRAGMA journal_mode=OFF")
            db.execute("PRAGMA synchronous=OFF")
            db.execute("PRAGMA cache_size=-65536")
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript(SCHEMA.read_text(encoding="utf-8"))
            stage.executescript("""PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
                CREATE TABLE new_ways(id INTEGER PRIMARY KEY,version INTEGER,tags TEXT,description TEXT);
                CREATE TABLE new_areas(ref TEXT PRIMARY KEY,version INTEGER,tags TEXT,members TEXT);
                CREATE TABLE options(setting INTEGER);""")
            stage.execute("INSERT INTO options VALUES(?)",(manifest["region"]!="全国",))
            db.execute("INSERT INTO source_snapshot VALUES(?,?,?)",
                       (manifest["source_sha256"],osm.name,manifest["license"]))
            print("Reading relation membership",flush=True)
            wanted, colors=add_relations(db,osm,stage,stats)
            print("Reading way references",flush=True)
            vertices=add_ways(db,osm,stage,wanted,stats)
            print(f"Resolving {len(vertices):,} raw nodes",flush=True)
            node_filter=osmium.filter.IdFilter(vertices)
            del vertices
            add_nodes(db,osm,node_filter,stats)
            del node_filter, wanted
            print("Assembling source areas and geographic context",flush=True)
            areas_and_ways(db,stage,stats)
            metro_members(db,colors)
            db.commit()
            if db.execute("PRAGMA quick_check(1)").fetchone()!=("ok",):
                raise ValueError("Canonical integrity check failed")
            manifest["counts"]=dict(db.execute("SELECT kind,count(*) FROM entity GROUP BY kind"))
            manifest["source_objects"]={k:db.execute(f"SELECT count(*) FROM {k}").fetchone()[0] for k in ("source_node","source_way","source_relation_member","base_feature","metro_service_track")}
        manifest.update(snapshot=snapshot,generator="railscope-next-source-v3",source_schema=3,source_audit=dict(stats),
                        basemap="OpenStreetMap · Natural Earth",coverage_kind="source_extract")
        manifest["files"]={name:checksum(directory/name) for name in ("canonical.sqlite","transport.pmtiles")}
        (directory/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
        directory.rename(target)
        print("Ready",target,manifest["source_objects"],flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack",type=Path)
    parser.add_argument("osm",type=Path)
    parser.add_argument("output",type=Path)
    parser.add_argument("snapshot")
    args=parser.parse_args()
    enrich(args.pack,args.osm,args.output,args.snapshot)
