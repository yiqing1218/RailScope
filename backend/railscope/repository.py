"""Small deterministic repository used by demo/API; SQLAlchemy persistence can replace it at this boundary."""
from dataclasses import dataclass, field
from .domain import *


@dataclass
class RailRepository:
    snapshots: dict[str, DatasetSnapshot] = field(default_factory=dict)
    memberships: list[LineMembership] = field(default_factory=list)
    sections: dict[str, RouteSection] = field(default_factory=dict)
    corridors: dict[str, Corridor] = field(default_factory=dict)
    station_routes: dict[str, StationRoute] = field(default_factory=dict)
    train_services: dict[str, TrainService] = field(default_factory=dict)
    station_areas: dict[str, StationArea] = field(default_factory=dict)
    platforms: dict[str, Platform] = field(default_factory=dict)
    stop_positions: dict[str, StopPosition] = field(default_factory=dict)
    entrances: dict[str, Entrance] = field(default_factory=dict)
    sources: dict[str, DataSource] = field(default_factory=dict)
    lines: dict[str, InfrastructureLine] = field(default_factory=dict)
    nodes: dict[str, NetworkNode] = field(default_factory=dict)
    edges: dict[str, NetworkEdge] = field(default_factory=dict)
    stations: dict[str, Station] = field(default_factory=dict)
    train_runs: dict[str, TrainRun] = field(default_factory=dict)
    stops: list[StopTime] = field(default_factory=list)
    blocks: dict[str, BlockSection] = field(default_factory=dict)
    block_edges: list[BlockEdge] = field(default_factory=list)
    station_tracks: dict[str, StationTrack] = field(default_factory=dict)
    headway_rules: list[HeadwayRule] = field(default_factory=list)
    scenarios: dict[str, DispatchScenario] = field(default_factory=dict)
    events: list[DispatchEvent] = field(default_factory=list)
    occupancies: list[TrackOccupancy] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)

    def stops_for(self, train_id: str) -> tuple[StopTime, ...]:
        return tuple(sorted((s for s in self.stops if s.train_run_id == train_id), key=lambda s: s.sequence))

    def events_for(self, scenario_id: str, train_id: str | None = None) -> tuple[DispatchEvent, ...]:
        return tuple(e for e in self.events if e.scenario_id == scenario_id and (train_id is None or e.train_run_id == train_id))

    def reset_scenario(self, scenario_id: str) -> None:
        self.events = [e for e in self.events if e.scenario_id != scenario_id]
        self.occupancies = [o for o in self.occupancies if o.scenario_id != scenario_id]
        self.conflicts = [c for c in self.conflicts if c.scenario_id != scenario_id]
