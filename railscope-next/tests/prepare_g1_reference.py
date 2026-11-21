"""Adapt the repository's railway and Shanghai metro references to directed snapshots."""
import argparse
import heapq
import json
import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from shapely import from_wkb, transform
from shapely.geometry import Point


def local_scale(lat):
    return 111200 * math.cos(math.radians(lat)), 111200


def nearby_nodes(nodes, anchor, radius):
    xscale, yscale = local_scale(anchor[1])
    result = {}
    for node, (x, y) in nodes.items():
        meters = math.hypot((x - anchor[0]) * xscale, (y - anchor[1]) * yscale)
        if meters <= radius:
            result[node] = meters
    return result


def directed_path(db, first, last, route_ref="osm:relation:356778", mode="rail", radius=300):
    nodes = {}
    graph = defaultdict(list)
    line = db.execute("SELECT id FROM entity WHERE kind='line' AND source_ref=?", (route_ref,)).fetchone()
    if line is None:
        raise ValueError(f"The business line {route_ref} is absent from the snapshot")
    for eid, start, end, forward, reverse, meters, x1, y1, x2, y2 in db.execute(
        """SELECT DISTINCT x.*, a.longitude, a.latitude, b.longitude, b.latitude
           FROM line_member m CROSS JOIN edge x ON x.id=m.member_id CROSS JOIN entity e ON e.id=x.id
           CROSS JOIN node a ON a.id=x.from_node CROSS JOIN node b ON b.id=x.to_node
           WHERE m.line_id=? AND e.kind=? AND e.status='operating'""", (line[0],mode),
    ):
        nodes.update({start: (x1, y1), end: (x2, y2)})
        if forward:
            graph[start].append((end, eid, True, meters))
        if reverse:
            graph[end].append((start, eid, False, meters))

    origins = nearby_nodes(nodes, first, radius)
    destinations = nearby_nodes(nodes, last, radius)
    best = dict(origins)
    previous = {node: None for node in origins}
    queue = [(dist, node) for node, dist in origins.items()]
    heapq.heapify(queue)
    while queue:
        dist, node = heapq.heappop(queue)
        if dist != best[node]:
            continue
        for target, eid, forward, meters in graph[node]:
            cost = dist + meters
            if cost < best.get(target, math.inf):
                best[target] = cost
                previous[target] = (node, eid, forward)
                heapq.heappush(queue, (cost, target))
    found = best.keys() & destinations.keys()
    if not found:
        raise ValueError(f"No continuous directed {route_ref} path joins the two source anchors")
    dest = min(found, key=lambda node: (best[node] + destinations[node], node))
    edges = []
    node = dest
    while previous[node] is not None:
        node, eid, forward = previous[node]
        edges.append({"edge_id": eid, "forward": forward})
    edges.reverse()
    for seq, edge in enumerate(edges):
        edge["sequence"] = seq
    return node, dest, edges


def path_shapes(db, edges):
    shapes = []
    for edge in edges:
        data, meters = db.execute("SELECT g.wgs84_wkb,o.meters FROM geometry g JOIN edge o ON o.id=g.entity_id WHERE o.id=?", (edge["edge_id"],)).fetchone()
        geom = from_wkb(data)
        shapes.append((geom if edge["forward"] else geom.reverse(), meters))
    return shapes


def station_shapes(db, name, classification):
    yield from ((sid, from_wkb(data).centroid) for sid, data in db.execute(
        """SELECT e.id,g.wgs84_wkb FROM entity e JOIN geometry g ON g.entity_id=e.id
           WHERE e.name=? AND e.kind='station' AND e.class=? AND g.wgs84_wkb IS NOT NULL ORDER BY e.id""",
        (name, classification),
    ))


def station_anchor(stations, shapes, name):
    candidates = []
    center = Point(0, 0)
    for sid, point in stations:
        xscale, yscale = local_scale(point.y)
        project = lambda x, y: ((x-point.x) * xscale, (y-point.y) * yscale)
        local_shapes = [transform(line, project, interleaved=False) for line, _ in shapes]
        index = min(range(len(shapes)), key=lambda i: local_shapes[i].distance(center))
        geom = local_shapes[index]
        candidates.append((geom.distance(center), sid, index, geom.project(center, normalized=True)))
    if not candidates:
        raise ValueError(f"Station {name} is absent from the snapshot")
    return min(candidates)


def build_bundle(db, edges, timetable, classification, train_code, service_date, mode, provenance):
    shapes = path_shapes(db, edges)
    stops, anchors = [], []
    for seq, row in enumerate(timetable):
        stations = list(station_shapes(db, row["name"], classification))
        if mode == "rail" and len(stations) != 1:
            raise ValueError(f"Cannot unambiguously identify {row['name']} in the snapshot")
        distance, sid, index, fraction = station_anchor(stations, shapes, row["name"])
        if stops and (index, fraction) <= anchors[-1]["order"]:
            raise ValueError(f"The reference anchor for {row['name']} reverses or duplicates path order")
        anchors.append({"station": row["name"], "station_id": sid,
                        "offset_from_station_m": round(distance, 1), "order": (index, fraction)})
        stops.append({"station_id": sid, "sequence": seq, "arrival_time_s": row["arrival_s"],
                      "departure_time_s": row["departure_s"], "stop_edge_id": edges[index]["edge_id"],
                      "stop_edge_sequence": index, "stop_offset_m": fraction * shapes[index][1]})

    service = {"id": "reference-service", "train_code": train_code, "mode": mode, "provenance": provenance}
    run = {"id": "", "service_id": service["id"], "train_number": train_code,
           "service_date": service_date, "time_zone": "Asia/Shanghai", "plan_version": 0,
           "status": "scheduled", "stops": stops, "provenance": provenance}
    return {"services": [service], "runs": [run]}, anchors


def fixture(manifest, start, end, edges, route, name, bundle, anchors):
    corridor = {"id": "", "version": 0, "snapshot_id": manifest["snapshot"], "name": name,
                "origin_node_id": start, "destination_node_id": end, "edge_refs": edges,
                "provenance": {"source_kind": "osm", "source_url_or_reference": route},
                "verification_status": "automatic_reference", "resolution_mode": "automatic_reference"}
    return {"corridor": corridor, "bundle": bundle, "reference_anchors": anchors}


def prepare(pack, examples):
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    historical = json.loads((examples / "g1-service.json").read_text(encoding="utf-8"))
    reference = json.loads((examples / "g1-reference.json").read_text(encoding="utf-8"))
    route = "osm:relation:356778"
    with closing(sqlite3.connect((pack / "canonical.sqlite").resolve().as_uri() + "?mode=ro", uri=True)) as db:
        def coordinate(point):
            source = f"osm:node:{point['properties']['osm_node_id']}"
            row = db.execute("SELECT longitude,latitude FROM node WHERE source_ref=? AND network='rail'", (source,)).fetchone()
            if row is None:
                raise ValueError(f"Missing legacy reference anchor {source}")
            return row

        start, end, edges = directed_path(db, coordinate(reference["points"][0]), coordinate(reference["points"][-1]), route)
        provenance = {"source_kind": "user_defined", "source_url_or_reference": historical["timetable_url"],
                      "source_timestamp": historical["service_date"]}
        bundle, anchors = build_bundle(db, edges, historical["stops"], "rail_station", historical["train_id"],
                                       historical["service_date"], "rail", provenance)
    return fixture(manifest, start, end, edges, route, "G1 京沪高铁历史参考", bundle, anchors)


def prepare_metro(pack, specification):
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    stops = json.loads(specification.read_text(encoding="utf-8"))["stations_south_to_north"]
    train_code = "上海地铁1号线首班重建"
    route, mode = "osm:relation:6800210", "metro"
    timetable = []
    for row in stops:
        t = 5 * 3600 + 30 * 60 + 60 * row["sn_offset_min"]
        timetable.append({"name": row["name"], "arrival_s": t, "departure_s": t})
    with closing(sqlite3.connect((pack / "canonical.sqlite").resolve().as_uri() + "?mode=ro", uri=True)) as db:
        first = next(station_shapes(db, stops[0]["name"], "metro_station"))[1]
        last = next(station_shapes(db, stops[-1]["name"], "metro_station"))[1]
        start, end, edges = directed_path(db, first.coords[0], last.coords[0], route, mode)
        provenance = {"source_kind": "user_defined", "source_url_or_reference": f"公开资料重建 · {specification.name}"}
        bundle, anchors = build_bundle(db, edges, timetable, "metro_station", train_code, "2026-09-17", mode, provenance)
    return fixture(manifest, start, end, edges, route, "上海地铁 1 号线 莘庄向富锦路参考通道", bundle, anchors)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--examples", type=Path, default=Path("desktop/examples"))
    parser.add_argument("--metro-line1", type=Path, help="Use the repository’s Line 1 public reconstruction specification")
    args = parser.parse_args()
    result = prepare_metro(args.pack, args.metro_line1) if args.metro_line1 else prepare(args.pack, args.examples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
