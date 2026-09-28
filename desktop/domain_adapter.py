"""Strict desktop DTO -> shared RailScope domain adapter.

The desktop JSON format remains a compatibility/import format. Every object
crossing the repository boundary receives a stable RailScope ID here.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from pathlib import Path
import sqlite3
import json

from railscope.domain import (
    Corridor, DatasetSnapshot, InfrastructureLine, LineMembership, NetworkEdge, NetworkNode,
    Station, StationRoute, StationTrack, StationTrackEdge, OperationalPoint, StopTime, TrainRun, TrainService,
    RouteIntent, RouteIntentStep,
)
from railscope.identity import IdentityRegistry
from railscope.integrity import ordered_path_nodes, path_refs, validate_repository
from railscope.repository import RailRepository
from railscope.rail_semantics import edge_semantics
from railscope.services.simulation.geometry import distance_m

try:
    from .rail import migrate_legacy_train_paths, shared_document
    from .rail_lines import line_identity
    from .rail_categories import track_type
    from .rail_line_workspace import membership_targets, MEMBERSHIP_KEY
    from .station_track_semantics import track_number, migrate_track_override, semantic_track_name
except ImportError:
    from rail import migrate_legacy_train_paths, shared_document
    from rail_lines import line_identity
    from rail_categories import track_type
    from rail_line_workspace import membership_targets, MEMBERSHIP_KEY
    from station_track_semantics import track_number, migrate_track_override, semantic_track_name


def _length(edge):
    if edge.get("length_m"):
        return float(edge["length_m"])
    return sum(distance_m(a, b) for a, b in zip(edge["coordinates"], edge["coordinates"][1:]))


def build_repository(graph, payload, identity_path, overrides=None, source_database=None):
    """Return `(RailRepository, bindings)` without mutating desktop DTOs."""
    registry = IdentityRegistry(Path(identity_path))
    overrides = overrides or {}
    selected = {edge['id'] for edge in graph['edges']}
    named = {value['station_track']['id']: value for value in overrides.values()
             if value.get('station_track') and selected.intersection(value.get('source_edge_ids', []))}
    missing = set().union(*(set(value.get('source_edge_ids', [])) for value in named.values())) - selected if named else set()
    if missing and source_database and Path(source_database).exists():
        extra = []
        with closing(sqlite3.connect(source_database)) as db:
            for key in sorted(missing):
                row = db.execute('SELECT data FROM edges WHERE id=?', (key,)).fetchone()
                if row is None:
                    raise ValueError('已命名股道的源轨道缺失，需要迁移工作区引用：' + key)
                extra.append(json.loads(row[0]))
        graph = {**graph, 'edges': [*graph['edges'], *extra]}
    with closing(sqlite3.connect(registry.path)) as identity_db, identity_db:
        return _build_repository(graph, payload, registry, identity_db, overrides, named)


def _build_repository(graph, payload, registry, identity_db, overrides=None, named=None):
    document = migrate_legacy_train_paths(shared_document(payload), graph["edges"])
    repo, bindings = RailRepository(), defaultdict(dict)
    from railscope.presentation import design_speed, source_design_speed
    overrides = overrides or {}
    named_by_edge = {}
    from railscope.workspace import decode
    for value in (named or {}).values():
        value = migrate_track_override(value, value.get('station_name', ''),
                                       value['station_track'].get('track_role', 'unknown'))
        track = decode(StationTrack, value['station_track'])
        for source in value.get('source_edge_ids', []):
            named_by_edge[source] = track
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
        semantics = edge_semantics(edge)
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
            railway_class=semantics['railway_class'], line_role=semantics['line_role'],
            provenance={key: semantics['provenance'][key] for key in ('railway_class', 'line_role')},
            design_speed_kmh=design_speed(overrides.get(proposed_line, {}).get('technical_attributes', {}).get('design_speed_kmh')
                                          or edge.get("design_speed_kmh")) or source_design_speed(edge.get("way_tags", {})),
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
            snapshot_id=edge.get("snapshot_id"), osm_way_id=str(edge.get("osm_way_id") or "") or None,
            osm_node_ids=tuple(map(str, edge.get("node_ids", ()))),
            source_tags=dict(edge.get("way_tags", {})),
            **semantics,
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

    # Operational points retain their source identity; physical nodes are references.
    # Two facilities with the same name never become one operational point.
    for source_node, point in point_by_node.items():
        props = point.get('properties', {})
        kind = props.get('kind')
        point_type = {'station': 'station', 'halt': 'station', 'signal_box': 'signal_box',
                      'junction_post': 'junction_post', 'block_post': 'block_post'}.get(kind)
        node_id = bindings['nodes'].get(source_node)
        if point_type is None or node_id is None:
            continue
        source = 'node/' + str(props.get('source_station_node') or source_node)
        station_id = None
        if point_type == 'station':
            station_id = registry.resolve_alias('station', 'osm/' + source, 'ST', identity_db)
            node = repo.nodes[node_id]
            repo.stations.setdefault(station_id, Station(station_id, props.get('name') or '未命名车站',
                node.lon, node.lat, node_id, source_member_ids=('osm:' + source.replace('/', ':'),),
                source_id='osm', verification_status='osm_explicit'))
            bindings['stations'][source_node] = station_id
            bindings['station_sources'][source] = station_id
            repo.nodes[node_id] = NetworkNode(**{**node.__dict__, 'station_id': station_id})
        op_id = registry.resolve_alias('operational_point', 'osm/' + source, 'OP', identity_db)
        previous = repo.operational_points.get(op_id)
        nodes = tuple(dict.fromkeys((*previous.node_ids, node_id))) if previous else (node_id,)
        repo.operational_points[op_id] = OperationalPoint(op_id, props.get('name') or '未命名运营节点',
            point_type, station_id, nodes, source_id='osm/' + source,
            snapshot_id=props.get('snapshot_id'), verification_status='osm_explicit',
            provenance={'point_type': {'source': 'osm', 'evidence': {'railway': kind},
                                      'verification_status': 'osm_explicit'}})
        bindings['operational_points'][source_node] = op_id

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
        intent = route.get('route_intent', {})
        intent_source = str(intent.get('id') or source_id)
        intent_id = intent_source if intent_source.startswith('RI-') else registry.resolve_alias(
            'route_intent', intent_source, 'RI', identity_db)
        requested = intent.get('sequence', [])
        steps, unresolved = [], []
        for index, step in enumerate(requested, 1):
            reference = str(step.get('line_id') if step['kind'] == 'line' else step.get('node_id'))
            kind, canonical_reference = None, None
            if step['kind'] == 'line':
                kind = 'infrastructure_line'
                canonical_reference = bindings['lines'].get(reference) or (reference if reference in repo.lines else None)
            elif reference.startswith('station:'):
                station_key = reference.removeprefix('station:')
                kind = 'station'
                canonical_reference = bindings['station_sources'].get(station_key)
            else:
                for collection, candidate_kind in ((repo.operational_points, 'operational_point'),
                        (repo.stations, 'station'), (repo.nodes, 'node')):
                    if reference in collection:
                        kind, canonical_reference = candidate_kind, reference
                        break
                if canonical_reference is None:
                    canonical_reference = bindings['operational_points'].get(reference)
                    kind = 'operational_point' if canonical_reference else 'node'
                    canonical_reference = canonical_reference or bindings['nodes'].get(reference)
            if canonical_reference is None:
                unresolved.append({'sequence': index, 'source_reference': reference, 'kind': step['kind']})
            else:
                steps.append(RouteIntentStep(index, kind, canonical_reference,
                    {'backward': 'reverse'}.get(step.get('direction'), step.get('direction', 'unknown'))))
        # A partial list must not masquerade as a complete business intent.
        # Preserve source aliases for later resolution without inventing nodes.
        provenance = {'source': intent.get('source', 'legacy_migration'),
            'requested_sequence': requested, 'resolution_policy': intent.get('resolution_policy', 'strict'),
            'unresolved_aliases': unresolved}
        if unresolved:
            provenance['resolved_steps'] = [step.__dict__ for step in steps]
        repo.route_intents[intent_id] = RouteIntent(intent_id, route.get('name', source_id),
            tuple(steps) if not unresolved else (), snapshot_id=intent.get('snapshot_id'),
            source_id=source_id, verification_status='unresolved' if unresolved else 'unverified', provenance=provenance)
        bindings['route_intents'][intent_source] = intent_id
        refs = path_refs(repo, [(bindings["edges"][leg["edge_id"]], leg["direction"] == "forward") for leg in route["path"]])
        first, last = repo.edges[refs[0].edge_id], repo.edges[refs[-1].edge_id]
        corridor = Corridor(
            corridor_id, route.get("name", source_id), refs,
            first.from_node_id if refs[0].forward else first.to_node_id,
            last.to_node_id if refs[-1].forward else last.from_node_id,
            snapshot_id=first.snapshot_id, source_id=source_id,
            color=route.get("color", "#466979"),
            route_intent_id=intent_id, resolution_mode={'auto': 'automatic_reference',
                'strict': 'strict_unique', 'mainline': 'automatic_reference'}.get(intent.get('resolution_policy'), 'legacy'),
            provenance={'resolved_corridor': route.get('resolved_corridor', {}),
                        'source': 'desktop_saved_physical_path'},
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
        route_station_source = str(value["station_id"]).removeprefix("station/")
        if route_station_source not in bindings["stations"]:
            raise ValueError(f"车站进路引用未知车站：{value['station_id']}")
        refs = path_refs(repo, [(bindings["edges"][leg["edge_id"]], leg["direction"] == "forward") for leg in value["edge_refs"]])
        repo.station_routes[station_route_id] = StationRoute(
            station_route_id, bindings["stations"][route_station_source], refs,
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
                named_track = named_by_edge.get(source_track)
                if named_track and named_track.station_id == stop_station(stop):
                    if any(ref.edge_id not in repo.edges for ref in named_track.edge_refs):
                        raise ValueError('已命名股道引用的轨道未载入，请检查源快照')
                    track_id = named_track.id
                    bindings['station_tracks'][source_track] = track_id
                    repo.station_tracks[track_id] = named_track
                source_number = track_number(edge.source_tags)
                repo.station_tracks.setdefault(track_id, StationTrack(
                    track_id, stop_station(stop),
                    (source_number + ('' if source_number.endswith('道') else '道')) if source_number else
                        semantic_track_name(repo.stations[stop_station(stop)].name, edge.track_role),
                    track_number=source_number, length_m=edge.length_m, is_virtual=False,
                    edge_refs=path_refs(repo, [(edge_id, edge.direction != 'reverse')], allow_nonoperating=True),
                    track_role=edge.track_role, railway_class=edge.railway_class,
                    infrastructure_line_id=edge.infrastructure_line_id, yard_id=edge.yard_id, zone_id=edge.zone_id,
                    source_member_ids=(str(source_track),), snapshot_id=edge.snapshot_id,
                    provenance={'track_number': {'source': 'osm_explicit' if source_number else 'unavailable',
                        'evidence': source_number, 'verification_status': 'osm_explicit' if source_number else 'unverified'}},
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
                stop_edge_sequence=position['path_index'] + 1 if 'path_index' in position else None,
            ))
    repo.station_track_edges = [StationTrackEdge(track.id, ref.edge_id, ref.sequence,
        'forward' if ref.forward else 'reverse') for track in repo.station_tracks.values() for ref in track.edge_refs]
    validate_repository(repo)
    return repo, {key: dict(value) for key, value in bindings.items()}
