"""Produce an immutable RailScope Next pack from a local OSM extract."""
import argparse
import hashlib
import json
import math
import re
import sqlite3
import tempfile
import unicodedata
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from pypinyin import Style, lazy_pinyin
from shapely.geometry import LineString

from osm_source import Extract
from tiles import build_tiles

NAMESPACE = uuid.UUID("94087940-65db-56d8-9176-4061c7254980")
SCHEMA = Path(__file__).with_name("canonical.sql")
FALLBACK = {"rail": "国铁轨道", "metro": "城市轨道", "road": "高速公路",
            "station": "车站", "facility": "铁路设施", "poi": "交通设施", "line": "线路"}


def identity(key):
    return uuid.uuid5(NAMESPACE, key)


def checksum(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def json_text(record):
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def meters(coordinates):
    total = 0
    for (x1, y1), (x2, y2) in zip(coordinates, coordinates[1:]):
        a = math.sin(math.radians(y2-y1) / 2) ** 2
        a += math.cos(math.radians(y1)) * math.cos(math.radians(y2)) * math.sin(math.radians(x2-x1) / 2) ** 2
        total += 12742017.6 * math.asin(math.sqrt(min(1, a)))
    return total


def direction(tags, network):
    flag = tags.get("oneway")
    if flag == "-1":
        return 0, 1
    if flag in {"yes", "1", "true"} or (flag is None and network == "road" and tags.get("highway") == "motorway"):
        return 1, 0
    return 1, 1


@lru_cache(maxsize=16384)
def search_terms(labels):
    words = set()
    for label in dict.fromkeys(filter(None, labels)):
        label = unicodedata.normalize("NFKC", label).casefold()
        words.update(re.findall(r"[^\W_]+", label))
        words.update(label[i:i+n] for n in (1, 2, 3) for i in range(len(label)-n+1)
                     if re.fullmatch(r"[\u3400-\u9fff]+", label[i:i+n]))
        if re.search(r"[\u3400-\u9fff]", label):
            words.add("".join(lazy_pinyin(label)))
            words.add("".join(lazy_pinyin(label, style=Style.FIRST_LETTER)))
    return " ".join(sorted(words))


class Canonical:
    def __init__(self, db):
        self.db = db
        self.members = defaultdict(list)
        self.known_nodes = set()

    def save_source(self, source):
        self.db.execute("INSERT OR IGNORE INTO source_object VALUES (?, ?, ?)",
                        (source.ref, source.version, json_text(source.tags)))

    def entity(self, source, key, description, geometry=None, bounds=None):
        self.save_source(source)
        ident = identity(key)
        eid, rid = str(ident), (ident.int & ((1 << 52) - 1)) + 1
        kind, category, status = description
        tags = source.tags
        name = tags.get("name:zh") or tags.get("name") or tags.get("ref") or FALLBACK[kind]
        confidence = "low" if status == "unknown" or category == "unknown" else "medium"
        self.db.execute("INSERT INTO entity VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (eid, rid, name, kind, category, status, source.ref, "automatic_reference", confidence))
        if geometry is not None:
            bounds = geometry.bounds
        minx, miny, maxx, maxy = bounds
        coords = (minx, maxx, miny, maxy)
        row = self.db.execute("INSERT INTO geometry VALUES (NULL, ?, ?, ?, ?, ?, ?)",
                              (eid, None if geometry is None else geometry.wkb, *coords)).lastrowid
        self.db.execute("INSERT INTO spatial VALUES (?, ?, ?, ?, ?)", (row, *coords))
        # Unnamed tracks share a generic display label and are browsable by category.
        if name != FALLBACK[kind]:
            aliases = tuple(tags.get(key, "") for key in ("name:zh", "name:en", "ref", "old_name", "alt_name", "official_name"))
            self.db.execute("INSERT INTO entity_search VALUES (?, ?)", (eid, search_terms((name, *aliases))))
        self.members[source.ref].append((eid, bounds, kind))
        return eid

    def node(self, network, node, coordinate, port=None):
        node_ref = f"osm:node:{node}"
        eid = str(identity(f"{network}:{node_ref}" + (f":{port}" if port else "")))
        if eid not in self.known_nodes:
            self.db.execute("INSERT OR IGNORE INTO source_object VALUES (?, 0, '{}')", (node_ref,))
            self.db.execute("INSERT INTO node VALUES (?, ?, ?, ?, ?)", (eid, node_ref, network, *coordinate))
            self.known_nodes.add(eid)
        return eid

    def write(self, extract):
        for source, desc, geometry in extract.objects:
            self.entity(source, f"object:{source.ref}", desc, geometry)
        for way, start, end, network, stable_part in extract.edges():
            coordinates = way.coordinates[start:end+1]
            length = meters(coordinates)
            if not length:
                extract.skipped["zero_length_edge"] += 1
                continue
            # UUIDs distinguish physical segments without exposing source IDs as business IDs.
            eid = self.entity(way.source, f"edge:{way.source.ref}:{stable_part}", way.description,
                              LineString(coordinates))
            nodes = []
            for idx in (start, end):
                node = way.nodes[idx]
                port = way.source.ref if network == "rail" and node in extract.diamonds else None
                nodes.append(self.node(network, node, way.coordinates[idx], port))
            self.db.execute("INSERT INTO edge VALUES (?, ?, ?, ?, ?, ?)",
                            (eid, *nodes, *direction(way.source.tags, network), length))
        for source, route, refs in extract.relations:
            matches = []
            for sequence, (ref, role) in enumerate(refs):
                members = self.members.get(ref, ())
                if route == "road":
                    members = [m for m in members if m[2] in {"road", "poi"}]
                if role in {"backward", "reverse"}:
                    members = list(reversed(members))
                for pos, (eid, bounds, _) in enumerate(members):
                    matches.append((sequence, pos, eid, role, bounds))
            wanted = {"road"} if route == "road" else {"metro", "rail"}
            if not any(any(m[2] in wanted for m in self.members.get(ref, ())) for ref, _ in refs):
                continue
            boxes = [m[4] for m in matches]
            bounds = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                      max(b[2] for b in boxes), max(b[3] for b in boxes))
            line = self.entity(source, f"business:{source.ref}", ("line", route, "unknown"), bounds=bounds)
            self.db.executemany("INSERT INTO line_member VALUES (?, ?, ?, ?, ?)",
                                ((line, sequence, pos, eid, role) for sequence, pos, eid, role, _ in matches))


def build(osm, output, region, snapshot, maxzoom, location_index, land=None):
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", snapshot):
        raise ValueError("Snapshot must be a safe lowercase identifier")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / snapshot
    if destination.exists():
        raise FileExistsError(f"Snapshot already exists: {destination}")
    with tempfile.TemporaryDirectory(dir=output, prefix=".build-") as working:
        stage = Path(working) / snapshot
        stage.mkdir()
        print("Reading OSM", flush=True)
        extract = Extract()
        index = f"sparse_file_array,{Path(working) / 'locations.bin'}" if location_index == "disk" else "flex_mem"
        extract.apply_file(osm, locations=True, idx=index)
        print(f"Normalizing {len(extract.ways):,} ways and {len(extract.objects):,} facilities", flush=True)
        dbpath = stage / "canonical.sqlite"
        db = sqlite3.connect(dbpath)
        try:
            db.executescript(SCHEMA.read_text(encoding="utf-8"))
            store = Canonical(db)
            store.write(extract)
            skipped = dict(extract.skipped)
            del store, extract
            db.commit()
            bounds = db.execute("SELECT min(min_x), min(min_y), max(max_x), max(max_y) FROM geometry").fetchone()
            if bounds[0] is None:
                raise ValueError("The extract contains no supported transport features")
            if db.execute("PRAGMA foreign_key_check").fetchone() or db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise ValueError("Canonical integrity check failed")
            print("Writing MVT / PMTiles", flush=True)
            build_tiles(db, stage / "transport.pmtiles", bounds, maxzoom, land)
            counts = dict(db.execute("SELECT kind, count(*) FROM entity GROUP BY kind"))
        finally:
            db.close()
        manifest = {
            "schema": 1, "snapshot": snapshot, "region": region,
            "source": "OpenStreetMap", "source_file": osm.name, "source_sha256": checksum(osm),
            "license": "ODbL 1.0", "generator": "railscope-next-osm-v1", "crs": "EPSG:4326",
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "bounds": bounds, "min_zoom": 3, "max_zoom": maxzoom, "counts": counts,
            "basemap": None if land is None else "Natural Earth · 1:10m land (public domain)",
            "basemap_sha256": None if land is None else checksum(land),
            "skipped": skipped,
            "files": {name: checksum(stage / name) for name in ["canonical.sqlite", "transport.pmtiles"]},
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stage.rename(destination)
        print(f"Ready: {destination} · {sum(counts.values()):,} entities", flush=True)
        return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("osm", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--region", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--maxzoom", type=int, choices=range(8, 17), default=14, metavar="8..16")
    parser.add_argument("--location-index", choices=("memory", "disk"), default="memory")
    parser.add_argument("--land", type=Path, help="Optional Natural Earth 1:10m land GeoJSON")
    args = parser.parse_args()
    build(args.osm, args.output, args.region, args.snapshot, args.maxzoom, args.location_index, args.land)


if __name__ == "__main__":
    main()
