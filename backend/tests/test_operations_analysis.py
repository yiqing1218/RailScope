from dataclasses import replace
import pytest
from railscope.domain import (
    NetworkNode,
    NetworkEdge,
    Corridor,
    Station,
    StationTrack,
    StationRoute,
    TrainRun,
    StopTime,
)
from railscope.integrity import path_refs, validate_repository
from railscope.repository import RailRepository
from railscope.services.analysis import (
    section_statistics,
    station_timetable,
    corridor_diagram_svg,
    station_state,
    station_track_intervals,
    corridor_archive,
)
from railscope.services.station_routing import effective_path, resolve_station_route
from railscope.services.simulation import TimetableLinearInterpolationModel


def fixture():
    repo = RailRepository()
    for key, point in [("a", (0, 0)), ("b", (1, 0)), ("c", (2, 0)), ("p", (1, 1))]:
        repo.nodes[key] = NetworkNode(key, *point)
    for key in ("a", "b", "c"):
        node = repo.nodes[key]
        repo.stations[key] = Station(key, key, *[node.lon, node.lat], key)
    for key, a, b in [
        ("ab", "a", "b"),
        ("bc", "b", "c"),
        ("ap", "a", "p"),
        ("pc", "p", "c"),
    ]:
        repo.edges[key] = NetworkEdge(
            key,
            a,
            b,
            (
                (repo.nodes[a].lon, repo.nodes[a].lat),
                (repo.nodes[b].lon, repo.nodes[b].lat),
            ),
            100,
            facility_id="b" if "p" in key else None,
        )
    refs = path_refs(repo, [("ab", True), ("bc", True)])
    repo.corridors["forward"] = Corridor("forward", "A-C", refs, "a", "c")
    repo.corridors["other"] = replace(repo.corridors["forward"], id="other")
    repo.corridors["reverse"] = Corridor(
        "reverse", "C-A", path_refs(repo, [("bc", False), ("ab", False)]), "c", "a"
    )
    for key, corridor, stations, start in [
        ("G1", "forward", ("a", "b", "c"), 0),
        ("K2", "other", ("a", "b", "c"), 600),
        ("D3", "reverse", ("c", "b", "a"), 1200),
    ]:
        repo.train_runs[key] = TrainRun(
            key,
            "2026-09-30",
            key,
            stations[0],
            stations[-1],
            corridor_id=corridor,
            verification_status="user_verified",
            traffic_type="passenger",
        )
        repo.stops.extend(
            StopTime(
                key, s, i + 1, start + i * 100, start + i * 100 + (20 if i == 1 else 0)
            )
            for i, s in enumerate(stations)
        )
    return repo


def test_shared_physical_section_counts_all_corridors_and_directions():
    repo = fixture()
    result = section_statistics(
        repo, repo.corridors["forward"].edge_refs, "2026-09-30", 0, 1800
    )
    assert result["count"] == 3 and result["by_direction"] == {
        "forward": 2,
        "reverse": 1,
    }
    assert (
        result["by_category"] == {"G": 1, "K": 1, "D": 1}
        and result["density_per_hour"] == 6
    )
    assert result["average_runtime_s"] == 200
    assert (
        section_statistics(
            repo, repo.corridors["forward"].edge_refs, "2026-09-30", 600, 1200
        )["count"]
        == 1
    )
    assert (
        section_statistics(repo, repo.corridors["forward"].edge_refs, "2020-01-01")[
            "average_speed_kmh"
        ]
        is None
    )


def test_station_timetable_and_diagram_include_all_reverse_and_dwell():
    repo = fixture()
    rows = station_timetable(repo, "b", "2026-09-30")
    assert [r["train_number"] for r in rows] == ["G1", "K2", "D3"] and rows[0][
        "stop_type"
    ] == "停靠"
    svg = corridor_diagram_svg(repo, "forward", "2026-09-30", linewidth=3, font_size=18)
    assert "D3" in svg and 'stroke-width="3"' in svg and 'font-size="18"' in svg


def test_platform_detour_moves_animation_without_mutating_corridor():
    repo = fixture()
    track = StationTrack(
        "track", "b", "1道", edge_refs=path_refs(repo, [("ap", True), ("pc", True)])
    )
    repo.station_tracks[track.id] = track
    found = resolve_station_route(repo, "b", "a", "c", "track")
    assert found["status"] == "automatic_reference"
    repo.station_routes["route"] = StationRoute(
        "route",
        "b",
        found["edge_refs"],
        "a",
        "c",
        "automatic_reference",
        source="station_topology",
    )
    index = next(
        i
        for i, s in enumerate(repo.stops)
        if s.train_run_id == "G1" and s.station_id == "b"
    )
    repo.stops[index] = replace(
        repo.stops[index],
        station_track_id="track",
        station_route_id="route",
        stop_edge_id="ap",
        stop_offset_m=100,
    )
    refs = repo.corridors["forward"].edge_refs
    validate_repository(repo)
    assert [
        r.edge_id for r in effective_path(repo, "forward", repo.stops_for("G1"))
    ] == ["ap", "pc"]
    assert repo.corridors["forward"].edge_refs is refs
    state = TimetableLinearInterpolationModel().position_at_time(repo, "", "G1", 110)
    assert state["state"] == "dwelling" and state["coordinate"] == [1, 1]
    assert state["route_status"] == "automatic_reference"
    assert (
        station_state(repo, "b", "2026-09-30", 110)["tracks"]["track"]["state"]
        == "occupied"
    )
    interval = station_track_intervals(repo, "b", "2026-09-30")[0]
    assert interval["arrival_s"] == 0 and interval["departure_s"] == 200
    assert len(station_state(repo, "b", "2026-09-30", 80)["trains"]) == 1
    repo.edges["pc"] = replace(repo.edges["pc"], direction="closed")
    assert resolve_station_route(repo, "b", "a", "c", "track")["status"] == "unresolved"


def test_archive_contains_real_control_points_in_distance_order():
    from railscope.domain import OperationalPoint

    repo = fixture()
    repo.operational_points["op"] = OperationalPoint("op", "B线路所", node_ids=("b",))
    data = corridor_archive(repo, "forward")
    assert data["operational_points"][0]["name"] == "B线路所"
    assert data["operational_points"][0]["distance_m"] == 100
    assert [edge["id"] for edge in data["edges"]] == ["ab", "bc"]


def test_terminal_dwell_and_station_track_use_same_time_boundaries():
    repo = fixture()
    repo.station_tracks["terminal"] = StationTrack(
        "terminal", "c", "终到股道", edge_refs=path_refs(repo, [("bc", True)])
    )
    index = next(
        i
        for i, s in enumerate(repo.stops)
        if s.train_run_id == "G1" and s.station_id == "c"
    )
    repo.stops[index] = replace(
        repo.stops[index],
        departure_time_s=260,
        station_track_id="terminal",
        stop_edge_id="bc",
        stop_offset_m=100,
    )
    model = TimetableLinearInterpolationModel()
    assert model.position_at_time(repo, "", "G1", 230)["state"] == "dwelling"
    assert (
        station_state(repo, "c", "2026-09-30", 230)["tracks"]["terminal"]["state"]
        == "occupied"
    )
    assert model.position_at_time(repo, "", "G1", 260)["state"] == "finished"
