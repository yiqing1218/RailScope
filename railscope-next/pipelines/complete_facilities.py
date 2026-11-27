"""Stage the missing operating sites and signals from the pack's original PBF."""

import argparse
import json
import shutil
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path

import osmium
from shapely.geometry import Point

from build_pack import Canonical, checksum, json_text
from enrich_pack import REF_TYPES, areas_and_ways, classify, primitives, save_geometry
from osm_source import OPERATING_SITES, Source


def save_node(db, node, tags):
    if not node.location.valid():
        return
    db.execute("INSERT OR IGNORE INTO source_node VALUES (?,?,?,?,?)",
               (node.id, node.version, node.location.lon, node.location.lat, json_text(tags)))


def collect_facilities(db, osm, stage):
    store = Canonical(db)
    ways = set()
    relation_members = set()
    tags_filter = osmium.filter.TagFilter(
        *(("railway", tag) for tag in sorted(OPERATING_SITES | {"signal"}))
    )
    bits = osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION
    for obj in primitives(osm, bits, tags_filter):
        tag_data = dict(obj.tags)
        tag = tag_data["railway"]
        kind = obj.type_str()
        ref = f"osm:{REF_TYPES[kind]}:{obj.id}"
        if db.execute("SELECT 1 FROM entity WHERE source_ref=? AND kind IN ('facility','station')",
                      (ref,)).fetchone():
            continue

        record = Source(ref, obj.version, tag_data)
        if kind == "n" and obj.location.valid():
            save_node(db, obj, tag_data)
            save_geometry(db, store, record, ("facility", tag), Point(obj.location.lon, obj.location.lat))
        elif kind == "w":
            store.save_source(record)
            db.execute("INSERT OR IGNORE INTO source_way VALUES (?,?)",
                       (obj.id, json_text([node.ref for node in obj.nodes])))
            stage.execute("INSERT INTO new_ways VALUES (?,?,?,?)",
                          (obj.id, obj.version, json_text(tag_data), json_text(("facility", tag))))
            ways.add(obj.id)
        elif kind == "r" and tag_data.get("type") == "multipolygon" and classify(tag_data):
            store.save_source(record)
            members = [(f"osm:{REF_TYPES[member.type]}:{member.ref}", member.role)
                       for member in obj.members if member.type in REF_TYPES]
            db.executemany("INSERT OR IGNORE INTO source_relation_member VALUES (?,?,?,?)",
                           ((ref, index, target, role) for index, (target, role) in enumerate(members)))
            stage.execute("INSERT INTO new_areas VALUES (?,?,?,?)",
                          (ref, obj.version, json_text(tag_data), json_text(members)))
            relation_members.update(member.ref for member in obj.members
                                    if member.type == "w" and member.role in {"outer", "inner", ""})
    return ways, relation_members


def supply_vertices(db, osm, ways, relation_members):
    missing_ways = {wid for wid in relation_members if not db.execute(
        "SELECT 1 FROM source_way WHERE osm_id=?", (wid,)).fetchone()}
    if missing_ways:
        for way in primitives(osm, osmium.osm.WAY, osmium.filter.IdFilter(missing_ways)):
            Canonical(db).save_source(Source(f"osm:way:{way.id}", way.version, dict(way.tags)))
            db.execute("INSERT OR IGNORE INTO source_way VALUES (?,?)",
                       (way.id, json_text([node.ref for node in way.nodes])))
    vertices = set()
    for way in ways | relation_members:
        row = db.execute("SELECT node_ids FROM source_way WHERE osm_id=?", (way,)).fetchone()
        if row:
            vertices.update(json.loads(row[0]))
    missing_nodes = {nid for nid in vertices if not db.execute(
        "SELECT 1 FROM source_node WHERE osm_id=?", (nid,)).fetchone()}
    if missing_nodes:
        for node in primitives(osm, osmium.osm.NODE, osmium.filter.IdFilter(missing_nodes)):
            save_node(db, node, dict(node.tags))


def complete_facilities(pack, osm, out):
    """Build a replacement beside the input, preserving snapshot and all existing IDs."""
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("source_schema", 0) < 3 or checksum(osm) != manifest["source_sha256"]:
        raise ValueError("Requires a source-v3 pack and its matching original PBF")
    if out.exists() or pack.resolve() in out.resolve().parents:
        raise ValueError("Output must be a new directory outside the input pack")
    directory = Path(tempfile.mkdtemp(dir=out.parent.resolve(), prefix=".facilities-"))
    try:
        for name in manifest["files"]:
            shutil.copyfile(pack / name, directory / name)
        with closing(sqlite3.connect(directory / "canonical.sqlite")) as db, closing(sqlite3.connect(":memory:")) as stage:
            db.execute("PRAGMA foreign_keys=ON")
            stage.executescript("""
                CREATE TABLE new_ways(id INTEGER PRIMARY KEY,version INTEGER,tags TEXT,description TEXT);
                CREATE TABLE new_areas(ref TEXT PRIMARY KEY,version INTEGER,tags TEXT,members TEXT);
            """)
            before = db.execute("SELECT count(*) FROM entity WHERE kind='facility'").fetchone()[0]
            ways, members = collect_facilities(db, osm, stage)
            supply_vertices(db, osm, ways, members)
            stats = Counter()
            areas_and_ways(db, stage, stats)
            db.commit()
            if db.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
                raise ValueError("Canonical integrity check failed")
            after = db.execute("SELECT count(*) FROM entity WHERE kind='facility'").fetchone()[0]
            manifest["counts"]["facility"] = after
            for table in ("source_node", "source_way", "source_relation_member"):
                manifest["source_objects"][table] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        completion = {"added": after - before, **stats}
        manifest.setdefault("source_audit", {})["facility_completion"] = completion
        manifest["files"]["canonical.sqlite"] = checksum(directory / "canonical.sqlite")
        (directory / "manifest.json").write_text(json_text(manifest) + "\n", encoding="utf-8")
    except Exception:
        shutil.rmtree(directory)
        raise
    try:
        directory.rename(out)
    except OSError as error:
        raise OSError(f"Completed pack could not be moved and is ready at {directory}") from error
    print(json_text(completion), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", type=Path)
    parser.add_argument("osm", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    complete_facilities(args.pack, args.osm, args.output)
