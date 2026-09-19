from __future__ import annotations
from math import asin, cos, radians, sin, sqrt
from .domain import *
from .repository import RailRepository
from .services.blocks import create_virtual_blocks
from .services.routing import manual_corridor
from .services.dispatch import recalculate


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(radians, (*a, *b))
    h = sin((lat2-lat1)/2)**2 + cos(lat1)*cos(lat2)*sin((lon2-lon1)/2)**2
    return 6_371_000 * 2 * asin(sqrt(h))


def seconds(value: str) -> int:
    h, m, s = map(int, value.split(":"))
    return h * 3600 + m * 60 + s


def load_demo(repo: RailRepository | None = None) -> RailRepository:
    repo = repo or RailRepository()
    source = DataSource("demo-source", "RailScope synthetic demo", "demo", "CC0-1.0", "RailScope demo geometry")
    repo.sources[source.id] = source
    repo.lines["line-main"] = InfrastructureLine("line-main", "Demo Main Line", "rail", "main", source.id)
    coords = {"node-a": (116.380, 39.900), "node-b": (116.430, 39.920), "node-c": (116.485, 39.945), "node-loop": (116.443, 39.910), "node-road-a": (116.370, 39.885), "node-road-b": (116.500, 39.955), "node-metro-a": (116.390, 39.885), "node-metro-b": (116.470, 39.935)}
    for node_id, (lon, lat) in coords.items():
        repo.nodes[node_id] = NetworkNode(node_id, lon, lat, node_type="station_anchor" if node_id in {"node-a", "node-b", "node-c"} else "junction")
    def edge(edge_id: str, a: str, b: str, service: str | None = None, railway_type: str = "main"):
        points = (coords[a], coords[b])
        repo.edges[edge_id] = NetworkEdge(edge_id, a, b, points, _distance(*points), service=service, railway_type=railway_type, infrastructure_line_id="line-main", source_id=source.id)
    edge("edge-ab", "node-a", "node-b")
    edge("edge-bc", "node-b", "node-c")
    edge("edge-b-loop", "node-b", "node-loop", "siding", "branch")
    edge("edge-loop-c", "node-loop", "node-c", "siding", "branch")
    edge("edge-road", "node-road-a", "node-road-b", None, "primary")
    repo.edges["edge-road"] = NetworkEdge(**{**repo.edges["edge-road"].__dict__, "mode": "road"})
    edge("edge-metro", "node-metro-a", "node-metro-b", None, "main")
    repo.edges["edge-metro"] = NetworkEdge(**{**repo.edges["edge-metro"].__dict__, "mode": "metro"})
    for station_id, name, node in (("station-a", "Station A", "node-a"), ("station-b", "Station B", "node-b"), ("station-c", "Station C", "node-c")):
        lon, lat = coords[node]
        repo.stations[station_id] = Station(station_id, name, lon, lat, node, code=name[-1])
    create_virtual_blocks(repo)
    repo.station_tracks["track-b-1"] = StationTrack("track-b-1", "station-b", "B-1", "1", "1")
    repo.headway_rules.append(HeadwayRule("default", 180, 0))
    repo.scenarios["base-2026-09-15"] = DispatchScenario("base-2026-09-15", "Base Scenario", "2026-09-15")
    manual_corridor(
        repo,
        "corridor-eastbound",
        ["edge-ab", "edge-bc"],
        name="Station A → Station C",
    )
    manual_corridor(
        repo,
        "corridor-westbound",
        [("edge-bc", False), ("edge-ab", False)],
        name="Station C → Station A",
    )
    for train_id, number, origin, destination, corridor_id in (
        ("run-101", "101", "station-a", "station-c", "corridor-eastbound"),
        ("run-102", "102", "station-a", "station-c", "corridor-eastbound"),
        ("run-201", "201", "station-c", "station-a", "corridor-westbound"),
    ):
        repo.train_runs[train_id] = TrainRun(
            train_id,
            "2026-09-15",
            number,
            origin,
            destination,
            train_length_m=200,
            corridor_id=corridor_id,
            verification_status="user_verified",
        )
    data = {
        "run-101": [("station-a", None, "07:00:00"), ("station-b", "07:10:00", "07:12:00"), ("station-c", "07:22:00", None)],
        "run-102": [("station-a", None, "07:02:00"), ("station-b", "07:12:00", "07:14:00"), ("station-c", "07:24:00", None)],
        "run-201": [("station-c", None, "07:05:00"), ("station-b", "07:15:00", "07:17:00"), ("station-a", "07:27:00", None)],
    }
    for train_id, entries in data.items():
        for sequence, (station_id, arrival, departure) in enumerate(entries, 1):
            repo.stops.append(StopTime(train_id, station_id, sequence, seconds(arrival) if arrival else None, seconds(departure) if departure else None))
    recalculate(repo, "base-2026-09-15")
    return repo
