"""Read OSM sources without treating rendering geometry as track connectivity."""

from collections import Counter, defaultdict
from dataclasses import dataclass

import osmium
from shapely.geometry import LineString, Point, Polygon

RAIL = {"rail", "narrow_gauge", "light_rail", "subway", "tram", "monorail", "maglev"}
TECHNICAL = {"switch", "signal", "railway_crossing", "level_crossing", "subway_entrance"}
OPERATING_SITES = {"yard", "junction", "crossover", "spur_junction", "signal_box", "depot", "workshop", "engine_shed"}
LIFECYCLE = {"construction": "construction", "proposed": "planned", "disused": "disused", "abandoned": "disused", "razed": "disused"}


def describe(tags, rails_only=False):
    railway = tags.get("railway")
    highway = tags.get("highway")
    status = "operating"
    markers = [name for name in LIFECYCLE if railway == name or f"{name}:railway" in tags]
    if markers:
        marker = markers[0]
        status = LIFECYCLE[marker] if len(markers) == 1 else "unknown"
        railway = tags.get(f"{marker}:railway") or (tags.get(marker) if railway == marker else railway) or "unknown"
    if railway in RAIL or railway == "unknown":
        if railway in {"subway", "light_rail", "tram", "monorail", "maglev"}:
            return "metro", railway, status
        category = "unknown"
        service = tags.get("service", "")
        if service in {"yard", "siding", "spur", "crossover"}:
            category = service
        elif service:
            category = "unknown"
        elif tags.get("highspeed") == "yes" and tags.get("usage") in {None, "", "main"}:
            category = "high_speed"
        elif tags.get("usage") in {"industrial", "military"} or tags.get("freight") == "only":
            category = "freight"
        elif tags.get("usage") in {"main", "branch"} or tags.get("highspeed") == "no":
            category = "conventional"
        return "rail", category, status
    if rails_only:
        return None
    if highway in {"motorway", "motorway_link"}:
        return "road", highway, "operating"
    if highway == "construction" and tags.get("construction") in {"motorway", "motorway_link"}:
        return "road", tags["construction"], "construction"
    if railway in {"station", "halt", "tram_stop"} or tags.get("public_transport") == "station":
        mode = "metro_station" if tags.get("station") in {"subway", "light_rail"} or tags.get("subway") == "yes" else "rail_station"
        return "station", mode, "unknown"
    if railway in TECHNICAL | OPERATING_SITES | {"platform"}:
        return "facility", railway, "unknown"
    if tags.get("public_transport") == "stop_area":
        return "facility", "station_area", "unknown"
    if highway in {"services", "rest_area", "motorway_junction"} or tags.get("barrier") == "toll_booth":
        return "poi", highway or "toll_booth", "unknown"
    if tags.get("aeroway") == "aerodrome":
        return "poi", "aerodrome", "unknown"
    return None


@dataclass
class Source:
    ref: str
    version: int
    tags: dict


@dataclass
class Way:
    source: Source
    description: tuple
    nodes: list
    coordinates: list


def source(kind, obj, tags):
    return Source(f"osm:{kind}:{obj.id}", obj.version, tags)


class Extract(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.nodes = {}
        self.ways = []
        self.objects = []
        self.relations = []
        self.switches = {}
        self.diamonds = set()
        self.skipped = Counter()

    def node(self, obj):
        if not obj.tags:
            return
        tags = dict(obj.tags)
        desc = describe(tags)
        if not desc or desc[0] in {"rail", "metro", "road"} or not obj.location.valid():
            return
        record = source("node", obj, tags)
        self.objects.append((record, desc, Point(obj.location.lon, obj.location.lat)))
        if desc[1] == "switch":
            self.switches[obj.id] = record
        elif desc[1] == "railway_crossing":
            self.diamonds.add(obj.id)

    def way(self, obj):
        tags = dict(obj.tags)
        desc = describe(tags)
        if not desc:
            return
        if not obj.nodes or not all(n.location.valid() for n in obj.nodes):
            self.skipped["way_with_missing_node_location"] += 1
            return
        record = source("way", obj, tags)
        coords = [(n.location.lon, n.location.lat) for n in obj.nodes]
        if len(coords) < 2:
            self.skipped["short_way"] += 1
            return
        if desc[0] in {"rail", "metro", "road"}:
            self.ways.append(Way(record, desc, [n.ref for n in obj.nodes], coords))
            return
        geometry = LineString(coords)
        if len(coords) >= 4 and obj.nodes[0].ref == obj.nodes[-1].ref and tags.get("area") != "no":
            polygon = Polygon(coords)
            if polygon.is_valid and not polygon.is_empty:
                geometry = polygon
        self.objects.append((record, desc, geometry))

    def relation(self, obj):
        tags = dict(obj.tags)
        route = tags.get("route")
        if route not in {"railway", "train", "subway", "light_rail", "tram", "road"}:
            return
        if route == "road" and tags.get("network", "").lower() not in {"cn:expressway", "cn-expressways", "cn:expressways"}:
            # Some OSM extracts use no network tag. The membership test is applied later.
            if not tags.get("ref", "").startswith(("G", "S")):
                return
        object_types = {"w": "way", "n": "node", "r": "relation"}
        members = [(f"osm:{object_types[m.type]}:{m.ref}", m.role) for m in obj.members]
        self.relations.append((source("relation", obj, tags), route, members))

    def edges(self):
        """Split on shared OSM nodes within a compatible physical network."""
        uses = Counter()
        for way in self.ways:
            network = "road" if way.description[0] == "road" else "rail"
            uses.update((network, node) for node in way.nodes)
        for way in self.ways:
            network = "road" if way.description[0] == "road" else "rail"
            boundaries = [0, *(i for i, node in enumerate(way.nodes[1:-1], 1)
                               if (uses[network, node] > 1 and (network != "rail" or node not in self.diamonds))
                               or (network == "rail" and node in self.switches)),
                          len(way.nodes) - 1]
            seen = Counter()
            for start, end in zip(boundaries, boundaries[1:]):
                pair = tuple(sorted((way.nodes[start], way.nodes[end])))
                seen[pair] += 1
            for start, end in zip(boundaries, boundaries[1:]):
                endpoints = way.nodes[start], way.nodes[end]
                pair = tuple(sorted(endpoints))
                stable_part = f"{pair[0]}:{pair[1]}"
                if seen[pair] > 1:
                    # Repeated endpoints are distinguished by physical node identities.
                    path = way.nodes[start:end+1]
                    stable_part += ":" + ",".join(map(str, min(path, path[::-1])))
                yield way, start, end, network, stable_part
