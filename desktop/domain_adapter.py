"""Strict desktop DTO -> shared RailScope domain adapter.

The desktop JSON format remains a compatibility/import format. Every object
crossing the repository boundary receives a stable RailScope ID here.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from pathlib import Path
import sqlite3

from railscope.domain import (
    Corridor, DatasetSnapshot, InfrastructureLine, LineMembership, NetworkEdge, NetworkNode,
    Station, StationRoute, StationTrack, StopTime, TrainRun, TrainService,
)
from railscope.identity import IdentityRegistry
from railscope.integrity import ordered_path_nodes, path_refs, validate_repository
from railscope.repository import RailRepository
from railscope.services.simulation.geometry import distance_m

try:
    from .rail import migrate_legacy_train_paths, shared_document
    from .rail_lines import line_identity
    from .rail_categories import track_type
    from .rail_line_workspace import membership_targets, MEMBERSHIP_KEY
except ImportError:
    from rail import migrate_legacy_train_paths, shared_document
    from rail_lines import line_identity
    from rail_categories import track_type
    from rail_line_workspace import membership_targets, MEMBERSHIP_KEY


def _length(edge):
    if edge.get("length_m"):
        return float(edge["length_m"])
    return sum(distance_m(a, b) for a, b in zip(edge["coordinates"], edge["coordinates"][1:]))


def build_repository(graph, payload, identity_path):
    """Return `(RailRepository, bindings)` without mutating desktop DTOs."""
    registry = IdentityRegistry(Path(identity_path))
    with closing(sqlite3.connect(registry.path)) as identity_db, identity_db:
        return _build_repository(graph, payload, registry, identity_db)


def _build_repository(graph, payload, registry, identity_db):
    document = migrate_legacy_train_paths(shared_document(payload), graph["edges"])
    repo, bindings = RailRepository(), defaultdict(dict)
    node_coordinates = {}
    for edge in graph["edges"]:
        for source_node, coordinate in zip(edge.get("node_ids", (edge["from_node"], edge["to_node"])), edge["coordinates"]):
            node_coordinates[str(source_node)] = tuple(coordinate)
    point_by_node = {
        str(point["properties"]["osm_node_id"]): point
        for point in graph.get("points", [])
        if point.get("properties", {}).get("osm_node_id") is not None
    }
    for source_node, coordinate in node_coordinates.items():
        canonical = registry.resolve_node("osm:node:" + source_node, coordinate, "rail", identity_db)
        bindings["nodes"][source_node] = canonical
        props = point_by_node.get(source_node, {}).get("properties", {})
        repo.nodes[canonical] = NetworkNode(
            canonical, *coordinate, node_type=props.get("kind", "geometry_vertex"),
            source_id="osm/node/" + source_node, source_node_ids=(source_node,),
        )
    for edge in graph["edges"]:
        source_id = str(edge["id"])
        canonical = source_id if source_id.startswith("NE-") else registry.resolve_alias("edge", source_id, "NE", identity_db)
        bindings["edges"][source_id] = canonical
        if edge.get("source_edge_id"):
            bindings["edges"][str(edge["source_edge_id"])] = canonical
        proposed_line, proposed_name = line_identity(edge)
        line_id = proposed_line if proposed_line.startswith("IL-") else registry.resolve_alias("line", proposed_line, "IL", identity_db)
        bindings["lines"][proposed_line] = line_id
        repo.lines.setdefault(line_id, InfrastructureLine(
            line_id, edge.get("line_name") or proposed_name, "rail",
            edge.get("track_type"), source_id=proposed_line,
            construction_status=edge.get("construction_status", "construction" if edge.get("construction") else "operating"),
            verification_status=edge.get("verification_status", "OSM-derived"),
        ))
        from_node = bindings["nodes"][str(edge["from_node"])]
        to_node = bindings["nodes"][str(edge["to_node"])]
        repo.edges[canonical] = NetworkEdge(
            canonical, from_node, to_node, tuple(tuple(p) for p in edge["coordinates"]),
            _length(edge),
            railway_type=edge.get("track_type")
            or track_type(edge.get("way_tags", {}))[0],
            service=edge.get("way_tags", {}).get("service"),
            direction=edge.get("direction", "both"),
            infrastructure_line_id=line_id, source_id=source_id,
            construction_status=edge.get("construction_status", "construction" if edge.get("construction") else "operating"),
            snapshot_id=edge.get("snapshot_id"), osm_way_id=str(edge.get("osm_way_id") or "") or None,
            osm_node_ids=tuple(map(str, edge.get("node_ids", ()))),
            source_tags=dict(edge.get("way_tags", {})),
            verification_status=edge.get("verification_status", "OSM-derived"),
        )
        repo.memberships.append(LineMembership(canonical, line_id, source_id, "OSM-derived"))
    # Workspace groupings are additional memberships on the shared physical
    # edges. They never duplicate geometry or overwrite original ownership.
    known_memberships = {(entry.edge_id, entry.line_id) for entry in repo.memberships}
    source_edges = {edge["id"]: edge for edge in graph["edges"]}
    for route in document["routes"]:
        snapshot = route.get("extensions", {}).get(MEMBERSHIP_KEY)
        targets = membership_targets(snapshot)
        for leg in route["path"]:
            edge = source_edges[leg["edge_id"]]
            group = targets.get(line_identity(edge)[0])
            if not group:
                continue
            line_id = group if group.startswith("IL-") else registry.resolve_alias("line", group, "IL", identity_db)
            bindings["lines"][group] = line_id
            repo.lines.setdefault(line_id, InfrastructureLine(
                line_id, snapshot.get("names", {}).get(group, group), "rail",
                source_id=group, construction_status="operating",
                verification_status="topology_checked_not_dispatch_verified",
            ))
            pair = (bindings["edges"][edge["id"]], line_id)
            if pair not in known_memberships:
                repo.memberships.append(LineMembership(*pair, "workspace/" + snapshot.get("version", "unknown"),
                                                        "topology_checked_not_dispatch_verified"))
                known_memberships.add(pair)
    for snapshot_id in {edge.snapshot_id for edge in repo.edges.values() if edge.snapshot_id}:
        repo.snapshots[snapshot_id] = DatasetSnapshot(
            snapshot_id, "national-rail", "osm", "unknown", "unknown", "unknown"
        )
    def station_source(stop):
        extensions = stop.get('extensions', {})
        key = (extensions.get('railscope.org/track-position', {}).get('station_id')
               or extensions.get('railscope.org/stop-name', {}).get('station_key'))
        if str(key).startswith('station:'):
            return str(key).removeprefix('station:')
        props = point_by_node.get(str(stop['node_id']), {}).get('properties', {})
        source = extensions.get('railscope.org/station-anchor', {}).get('source_station_node')
        return 'node/' + str(source or props.get('source_station_node', stop['node_id']))

    def stop_station(stop):
        return bindings['station_sources'][station_source(stop)]

    for stop in (s for train in document['trains'] for s in train['stops']):
        source_node = str(stop['node_id'])
        node_id = bindings["nodes"].get(source_node)
        if not node_id:
            raise ValueError(f"经停节点未在完整物理路径中：{source_node}")
        point = point_by_node.get(source_node, {})
        props = point.get("properties", {})
        source = station_source(stop)
        station_id = registry.resolve_alias("station", "osm/" + source, "ST", identity_db)
        bindings['station_sources'][source] = station_id
        bindings["stations"].setdefault(source_node, station_id)
        node = repo.nodes[node_id]
        position = stop.get('extensions', {}).get('railscope.org/track-position', {})
        name = (stop.get('extensions', {}).get('railscope.org/stop-name', {}).get('display_name')
                or position.get('station_name') or props.get('name', source_node))
        repo.stations.setdefault(station_id, Station(
            station_id, name, node.lon, node.lat, node_id,
            source_member_ids=("osm:" + source.replace('/', ':'),), source_id="osm",
            verification_status="OSM-derived",
        ))
        repo.nodes[node_id] = NetworkNode(**{**node.__dict__, "station_id": station_id})
    route_by_source = {}
    for route in document["routes"]:
        source_id = route["id"]
        corridor_id = source_id if source_id.startswith("COR-") else registry.resolve_alias("corridor", source_id, "COR", identity_db)
        bindings["corridors"][source_id] = corridor_id
        refs = path_refs(repo, [(bindings["edges"][leg["edge_id"]], leg["direction"] == "forward") for leg in route["path"]])
        first, last = repo.edges[refs[0].edge_id], repo.edges[refs[-1].edge_id]
        corridor = Corridor(
            corridor_id, route.get("name", source_id), refs,
            first.from_node_id if refs[0].forward else first.to_node_id,
            last.to_node_id if refs[-1].forward else last.from_node_id,
            snapshot_id=first.snapshot_id, source_id=source_id,
            verification_status=(route.get("extensions", {}).get("railscope.org/line-resolution", {})
                                 .get("verification_status")
                                 or route.get("extensions", {}).get("verification_status", "user_verified")),
        )
        repo.corridors[corridor_id] = corridor
        route_by_source[source_id] = corridor
    for value in document.get("station_routes", []):
        source_id = value["id"]
        station_route_id = registry.resolve_alias("station_route", source_id, "SR", identity_db)
        bindings["station_routes"][source_id] = station_route_id
        station_source = str(value["station_id"]).removeprefix("station/")
        if station_source not in bindings["stations"]:
            raise ValueError(f"车站进路引用未知车站：{value['station_id']}")
        refs = path_refs(repo, [(bindings["edges"][leg["edge_id"]], leg["direction"] == "forward") for leg in value["edge_refs"]])
        repo.station_routes[station_route_id] = StationRoute(
            station_route_id, bindings["stations"][station_source], refs,
            bindings["nodes"][str(value["entry_node_id"])], bindings["nodes"][str(value["exit_node_id"])],
            value.get("verification_status", "unverified"),
        )
    service_date = document["service_date"]
    corridor_nodes = {}
    for train in document["trains"]:
        service_id = registry.resolve_alias("train_service", train["id"], "SVC", identity_db)
        run_source = service_date + "/" + train["id"]
        run_id = registry.resolve_alias("train_run", run_source, "RUN", identity_db)
        bindings["train_runs"][train["id"]] = run_id
        repo.train_services.setdefault(service_id, TrainService(service_id, train["id"], source_id=document["source"]))
        stops = train["stops"]
        corridor_id = bindings["corridors"][train["route_id"]]
        repo.train_runs[run_id] = TrainRun(
            run_id, service_date, train["id"], stop_station(stops[0]),
            stop_station(stops[-1]), service_id=service_id,
            corridor_id=corridor_id, source_id=document["source"], verification_status="user_verified",
        )
        for sequence, stop in enumerate(stops, 1):
            station_route_id = bindings["station_routes"].get(stop.get("station_route_id"))
            source_track = stop.get("station_track_id")
            track_id = None
            if source_track:
                edge_id = bindings["edges"][source_track]
                track_id = registry.resolve_alias(
                    "station_track", stop_station(stop) + "/" + edge_id, "STTR", identity_db
                )
                bindings["station_tracks"][source_track] = track_id
                edge = repo.edges[edge_id]
                repo.station_tracks.setdefault(track_id, StationTrack(
                    track_id, stop_station(stop),
                    point_by_node.get(str(stop["node_id"]), {}).get("properties", {}).get("name", source_track),
                    track_number=str(source_track), length_m=edge.length_m, is_virtual=False,
                ))
            position = stop.get("extensions", {}).get("railscope.org/track-position", {})
            stop_edge = bindings["edges"].get(position.get("edge_id"))
            if position and stop_edge is None:
                raise ValueError("停靠轨道不在共享基础设施中")
            offset = position.get("offset_m")
            station_id = stop_station(stop)
            stop_node = bindings["nodes"][str(stop["node_id"])]
            if not position and repo.stations[station_id].anchor_node_id != stop_node:
                # A logical station can serve several tracks. Preserve this
                # stop's selected physical node instead of borrowing its anchor.
                refs = repo.corridors[corridor_id].edge_refs
                if corridor_id not in corridor_nodes:
                    corridor_nodes[corridor_id] = dict(ordered_path_nodes(repo, refs))
                distance = corridor_nodes[corridor_id].get(stop_node)
                if distance is None:
                    raise ValueError("停站点不在完整通道上")
                ref = next(r for r in refs if r.start_distance_m <= distance <= r.end_distance_m)
                stop_edge = ref.edge_id
                offset = (distance - ref.start_distance_m if ref.forward
                          else ref.end_distance_m - distance)
            repo.stops.append(StopTime(
                run_id, station_id, sequence,
                stop["arrival_s"], stop["departure_s"], station_track_id=track_id,
                station_route_id=station_route_id,
                stop_edge_id=stop_edge, stop_offset_m=offset,
            ))
    validate_repository(repo)
    return repo, {key: dict(value) for key, value in bindings.items()}
