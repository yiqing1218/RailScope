"""Logical station resolution; raw OSM features remain immutable source records.

Relation membership is stronger evidence than names or proximity. The registry
allocates internal IDs and retains source aliases across imports; an ambiguous
new source member is kept separate instead of silently merging existing stations.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import sqlite3
import unicodedata
from uuid import uuid4

from railscope.domain import Station, StationArea
from railscope.identity import IdentityRegistry


def source_key(feature):
    props = feature["properties"]
    for kind in ("node", "way", "relation"):
        if f"osm_{kind}_id" in props:
            return f"osm:{kind}:{props[f'osm_{kind}_id']}"
    raise ValueError("Station source feature needs an explicit source object ID")


def station_tags(feature):
    props = feature["properties"]
    return props.get("station_tags", props.get("node_tags", {}))


def normalized_name(name):
    return "".join(unicodedata.normalize("NFKC", str(name)).split()).casefold().removesuffix("站")


def _distance(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 12742000 * math.asin(min(1, math.sqrt(h)))


def _relations(feature, field):
    props = feature["properties"]
    return set(props.get(field, []))


def group_station_sources(features, radius_m=350):
    """Return groups with conservative network/name/proximity fallback.

    Conflicting explicit relations never merge merely because their names match.
    Unknown networks are allowed for legacy exports, but receive lower confidence.
    """
    groups, grid, relations = [], defaultdict(set), defaultdict(set)
    for feature in sorted(features, key=source_key):
        props, tags = feature["properties"], station_tags(feature)
        coordinate = feature["geometry"]["coordinates"]
        name = normalized_name(props.get("name", tags.get("name", "")))
        networks = set(props.get("station_networks", [])) or {tags.get("network", "")}
        networks.discard("")
        relation_keys = [(field, value) for field in ("stop_area_relation_ids", "station_relation_ids")
                         for value in _relations(feature, field)]
        explicit = set()
        for field in ("stop_area_relation_ids", "station_relation_ids"):
            explicit = {index for key in relation_keys if key[0] == field for index in relations[key]}
            if explicit:
                break
        x, y = math.floor(coordinate[0] / .005), math.floor(coordinate[1] / .005)
        candidates = {i for a in range(x-1, x+2) for b in range(y-1, y+2) for i in grid[a, b]}
        fallback = []
        for index in candidates:
            group = groups[index]
            if not name or name.startswith("未命名") or group["name"] != name:
                continue
            if networks and group["networks"] and not networks.intersection(group["networks"]):
                continue
            conflict = any(
                _relations(feature, field) and group[field]
                and not _relations(feature, field).intersection(group[field])
                for field in ("stop_area_relation_ids", "station_relation_ids")
            )
            if not conflict and _distance(coordinate, group["anchor"]) <= radius_m:
                fallback.append(index)
        matches = explicit or set(fallback)
        # More than one valid cluster needs review, never arbitrary first-match.
        if len(matches) == 1:
            index = next(iter(matches))
            group = groups[index]
        else:
            index = len(groups)
            group = {"members": [], "name": name, "anchor": coordinate, "networks": set(),
                     "stop_area_relation_ids": set(), "station_relation_ids": set(),
                     "verification_status": "unresolved" if len(matches) > 1 else "automatic_match"}
            groups.append(group)
        group["members"].append(feature)
        group["networks"].update(networks)
        for field in ("stop_area_relation_ids", "station_relation_ids"):
            group[field].update(_relations(feature, field))
        grid[x, y].add(index)
        for key in relation_keys:
            relations[key].add(index)
    return groups


def anchor_feature(members):
    return min(members, key=lambda f: (
        station_tags(f).get("railway") not in ("station", "halt")
        and station_tags(f).get("public_transport") != "station", source_key(f)))


class StationRegistry:
    """Persistent source aliases to canonical domain Station, separate from imports."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.identities = IdentityRegistry(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS station_entities(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS station_source_aliases(
                    source_key TEXT PRIMARY KEY, station_id TEXT NOT NULL REFERENCES station_entities(id));
                CREATE INDEX IF NOT EXISTS station_alias_target ON station_source_aliases(station_id);
                CREATE TABLE IF NOT EXISTS station_area_entities(
                    source_key TEXT PRIMARY KEY, id TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
            """)
            db.commit()

    def resolve(self, groups, mode="metro"):
        """Resolve a complete batch atomically and return (Station, source members).

        Existing IDs are never silently coalesced after a new automatic match.
        Alias conflicts are represented by unresolved stations for manual review.
        """
        results = []
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            aliases = dict(db.execute("SELECT source_key, station_id FROM station_source_aliases"))
            members_by_id = defaultdict(set)
            for key, station_id in aliases.items():
                if key.startswith(("osm:node:", "osm:way:", "osm:relation:")):
                    members_by_id[station_id].add(key)
            for group in groups:
                members = group["members"]
                known = {aliases[source_key(f)] for f in members if source_key(f) in aliases}
                relation_aliases = [f"osm:{field}:{value}" for field in
                    ("stop_area_relation_ids", "station_relation_ids") for value in group[field]]
                relation_known = {aliases[key] for key in relation_aliases if key in aliases}
                known.update(relation_known)
                partition = defaultdict(list)
                fallback = next(iter(known)) if len(known) == 1 else "ST-" + uuid4().hex
                for feature in members:
                    partition[aliases.get(source_key(feature), fallback)].append(feature)
                for station_id, sources in partition.items():
                    anchor = anchor_feature(sources)
                    lon, lat = anchor["geometry"]["coordinates"]
                    keys = tuple(sorted(source_key(f) for f in sources))
                    members_by_id[station_id].update(keys)
                    station = Station(id=station_id, name=anchor["properties"].get("name", ""),
                        lon=lon, lat=lat,
                        anchor_node_id=self.identities.resolve_node(source_key(anchor), (lon, lat), mode, db), mode=mode,
                        source_member_ids=tuple(sorted(members_by_id[station_id])), source_id="osm",
                        verification_status="unresolved" if len(known) > 1 else group["verification_status"],
                        confidence=.95 if group["stop_area_relation_ids"] else .75 if group["networks"] else .5)
                    db.execute("INSERT INTO station_entities VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                               (station_id, json.dumps(asdict(station), ensure_ascii=False)))
                    for key in keys:
                        db.execute("INSERT OR IGNORE INTO station_source_aliases VALUES(?,?)", (key, station_id))
                        aliases[key] = station_id
                    results.append((station, sources))
                if len(partition) == 1 and len(known) <= 1:
                    station_id = next(iter(partition))
                    for key in relation_aliases:
                        db.execute("INSERT OR IGNORE INTO station_source_aliases VALUES(?,?)", (key, station_id))
                        aliases[key] = station_id
            # Previously resolved aliases may now appear in different automatic
            # groups after name/network edits. They are still one business object.
            pooled, latest = defaultdict(list), {}
            for station, sources in results:
                pooled[station.id].extend(sources)
                latest[station.id] = station
            results = []
            for station_id, sources in pooled.items():
                anchor = anchor_feature(sources)
                lon, lat = anchor["geometry"]["coordinates"]
                station = replace(latest[station_id], lon=lon, lat=lat,
                    name=anchor["properties"].get("name", ""),
                    anchor_node_id=self.identities.resolve_node(source_key(anchor), (lon, lat), mode, db),
                    source_member_ids=tuple(sorted(members_by_id[station_id])))
                db.execute("UPDATE station_entities SET data=? WHERE id=?",
                           (json.dumps(asdict(station), ensure_ascii=False), station_id))
                results.append((station, sources))
        return results

    def station_id(self, source_alias):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT station_id FROM station_source_aliases WHERE source_key=?", (source_alias,)).fetchone()
        return row[0] if row else None

    def register_area(self, source_alias, station_ids, area_type, geometry):
        """Persist genuine source polygons; unresolved ownership stays explicit."""
        if geometry.get("type") not in ("Polygon", "MultiPolygon"):
            raise ValueError("StationArea requires a real Polygon or MultiPolygon")
        if area_type not in ("station_outline", "station_building", "platform", "track_area"):
            raise ValueError("Unknown station area type")
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute("SELECT id FROM station_area_entities WHERE source_key=?", (source_alias,)).fetchone()
            area_id = row[0] if row else "SA-" + uuid4().hex
            owners = sorted(set(station_ids))
            area = StationArea(id=area_id, station_id=owners[0] if len(owners) == 1 else "",
                area_type=area_type, geometry=geometry, source_id=source_alias,
                verification_status="automatic_match" if len(owners) == 1 else "unresolved")
            db.execute("INSERT INTO station_area_entities VALUES(?,?,?) ON CONFLICT(source_key) DO UPDATE SET data=excluded.data",
                       (source_alias, area_id, json.dumps(asdict(area), ensure_ascii=False)))
        return area
