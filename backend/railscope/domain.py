"""Domain facts. Geometry, topology, operation and dispatch intentionally differ."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

ResourceType = Literal["edge", "block", "station_track", "platform"]


@dataclass(frozen=True)
class DataSource:
    id: str
    name: str
    source_type: str
    license: str | None = None
    attribution: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class InfrastructureLine:
    id: str
    name: str
    mode: str
    railway_type: str | None = None
    source_id: str | None = None
    construction_status: str = "unknown"
    verification_status: str = "unverified"
    confidence: float | None = None


@dataclass(frozen=True)
class NetworkNode:
    id: str
    lon: float
    lat: float
    mode: str = "rail"
    node_type: str = "junction"
    station_id: str | None = None
    source_id: str | None = None
    source_node_ids: tuple[str, ...] = ()
    snapshot_id: str | None = None


@dataclass(frozen=True)
class NetworkEdge:
    id: str
    from_node_id: str
    to_node_id: str
    coordinates: tuple[tuple[float, float], ...]
    length_m: float
    mode: str = "rail"
    railway_type: str | None = "main"
    service: str | None = None
    direction: str = "both"
    infrastructure_line_id: str | None = None
    source_id: str | None = None
    construction_status: str = "operating"
    snapshot_id: str | None = None
    osm_way_id: str | None = None
    osm_node_ids: tuple[str, ...] = ()
    source_tags: dict = field(default_factory=dict)
    verification_status: str = "unverified"
    confidence: float | None = None


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    lon: float
    lat: float
    anchor_node_id: str
    mode: str = "rail"
    code: str | None = None
    source_member_ids: tuple[str, ...] = ()
    verification_status: str = "unverified"
    source_id: str | None = None
    confidence: float | None = None

    @property
    def anchor_point(self):
        return (self.lon, self.lat)


@dataclass(frozen=True)
class TrainRun:
    id: str
    service_date: str
    train_number: str
    origin_station_id: str
    destination_station_id: str
    route_path_id: str | None = None
    train_length_m: float | None = None
    service_id: str | None = None
    corridor_id: str | None = None
    internal_train_no: str | None = None
    source_id: str | None = None
    source_version: str | None = None
    verification_status: str = "unverified"


@dataclass(frozen=True)
class StopTime:
    train_run_id: str
    station_id: str
    sequence: int
    arrival_time_s: int | None
    departure_time_s: int | None
    scheduled_distance_m: float | None = None
    station_track_id: str | None = None
    platform_id: str | None = None
    station_route_id: str | None = None


@dataclass(frozen=True)
class RoutePathEdge:
    edge_id: str
    sequence: int
    forward: bool
    start_distance_m: float
    end_distance_m: float


@dataclass(frozen=True)
class RoutePath:
    id: str
    edge_refs: tuple[RoutePathEdge, ...]
    total_length_m: float
    origin_station_id: str
    destination_station_id: str


# Desktop DTOs and SQL/PostGIS rows adapt to these same domain objects.
AtomicEdge = NetworkEdge
LogicalStation = Station


@dataclass(frozen=True)
class DatasetSnapshot:
    id: str
    dataset_id: str
    source: str
    retrieved_at: str
    effective_date: str
    version: str


@dataclass(frozen=True)
class LineMembership:
    edge_id: str
    line_id: str
    source_id: str | None = None
    verification_status: str = "unverified"


@dataclass(frozen=True)
class RouteSection:
    id: str
    edge_refs: tuple[RoutePathEdge, ...]
    origin_node_id: str
    destination_node_id: str


@dataclass(frozen=True)
class Corridor:
    """One complete directed physical path; intermediate stations are implicit."""
    id: str
    name: str
    edge_refs: tuple[RoutePathEdge, ...]
    origin_node_id: str
    destination_node_id: str
    snapshot_id: str | None = None
    source_id: str | None = None
    verification_status: str = "unverified"
    confidence: float | None = None


@dataclass(frozen=True)
class StationRoute:
    id: str
    station_id: str
    edge_refs: tuple[RoutePathEdge, ...]
    entry_node_id: str
    exit_node_id: str
    verification_status: str = "unverified"


@dataclass(frozen=True)
class TrainService:
    id: str
    train_code: str
    internal_train_no: str | None = None
    source_id: str | None = None
    source_version: str | None = None


@dataclass(frozen=True)
class StationArea:
    id: str
    station_id: str
    area_type: str
    geometry: dict
    source_id: str
    verification_status: str = "unverified"


@dataclass(frozen=True)
class Platform:
    id: str
    station_id: str
    name: str
    area_id: str | None = None
    source_id: str | None = None


@dataclass(frozen=True)
class StopPosition:
    id: str
    station_id: str
    node_id: str


@dataclass(frozen=True)
class Entrance:
    id: str
    station_id: str
    lon: float
    lat: float
    source_id: str | None = None


@dataclass(frozen=True)
class BlockSection:
    id: str
    name: str
    start_node_id: str
    end_node_id: str
    length_m: float
    direction: str = "both"
    block_type: str = "virtual"


@dataclass(frozen=True)
class BlockEdge:
    block_id: str
    edge_id: str
    sequence: int
    forward: bool = True


@dataclass(frozen=True)
class StationTrack:
    id: str
    station_id: str
    name: str
    track_number: str | None = None
    platform_number: str | None = None
    direction: str = "both"
    length_m: float = 0
    is_virtual: bool = True


@dataclass(frozen=True)
class HeadwayRule:
    id: str
    same_direction_min_s: int
    opposite_direction_min_s: int
    block_id: str | None = None


@dataclass(frozen=True)
class DispatchScenario:
    id: str
    name: str
    service_date: str


@dataclass(frozen=True)
class DispatchEvent:
    id: str
    scenario_id: str
    train_run_id: str
    event_type: Literal["delay_train", "hold_train", "cancel_train", "change_route", "assign_track"]
    effective_time_s: int | None = None
    original_value: object | None = None
    new_value: object | None = None
    reason: str | None = None


@dataclass(frozen=True)
class EffectiveRun:
    train_run: TrainRun
    stops: tuple[StopTime, ...]
    route_path_id: str | None
    cancelled: bool = False


@dataclass(frozen=True)
class TrackOccupancy:
    id: str
    train_run_id: str
    resource_type: ResourceType
    resource_id: str
    start_time_s: int
    end_time_s: int
    direction: str
    scenario_id: str


@dataclass(frozen=True)
class Conflict:
    id: str
    scenario_id: str
    conflict_type: str
    train_run_a: str
    train_run_b: str
    resource_type: ResourceType
    resource_id: str
    start_time_s: int
    end_time_s: int
    severity: str


@dataclass(frozen=True)
class ImportReport:
    source: str
    objects_read: int = 0
    objects_imported: int = 0
    objects_skipped: int = 0
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


def shifted_stop(stop: StopTime, seconds: int) -> StopTime:
    return replace(stop,
        arrival_time_s=None if stop.arrival_time_s is None else stop.arrival_time_s + seconds,
        departure_time_s=None if stop.departure_time_s is None else stop.departure_time_s + seconds)
