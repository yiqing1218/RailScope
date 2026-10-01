"""Engineering drawings preserve source graph and explicit ownership."""

from copy import deepcopy
from dataclasses import replace

import pytest

from railscope.domain import (
    InfrastructureLine,
    NetworkEdge,
    NetworkNode,
    Station,
    StationTrack,
    Yard,
)
from railscope.integrity import path_refs
from railscope.repository import RailRepository
from desktop.station_diagram_layout import DiagramOptions, build_layout


def fixture():
    repo = RailRepository()
    repo.stations["ST-s"] = Station("ST-s", "测试站", 120, 30, "NN-l1")
    repo.lines["IL-h"] = InfrastructureLine(
        "IL-h",
        "测试高速铁路",
        "rail",
        railway_class="high_speed",
        line_role="main_line",
    )
    repo.lines["IL-c"] = InfrastructureLine(
        "IL-c",
        "测试普速铁路",
        "rail",
        railway_class="conventional",
        line_role="main_line",
    )
    repo.yards["Y-h"] = Yard("Y-h", "ST-s", "高速场", "high_speed")
    repo.yards["Y-c"] = Yard("Y-c", "ST-s", "普速场", "conventional")

    def node(key, x, y):
        repo.nodes[key] = NetworkNode(key, 120 + x / 96000, 30 + y / 111320)

    def edge(key, a, b, line="IL-h", role="main_track", middle=()):
        pa, pb = repo.nodes[a], repo.nodes[b]
        points = ((pa.lon, pa.lat), *middle, (pb.lon, pb.lat))
        repo.edges[key] = NetworkEdge(
            key, a, b, points, 100, infrastructure_line_id=line, track_role=role
        )

    for key, x, y in [
        ("NN-l1", -900, 0),
        ("NN-a", -200, 0),
        ("NN-b", 200, 0),
        ("NN-r1", 900, 0),
        ("NN-u", -200, 12),
        ("NN-v", 200, 12),
        ("NN-l2", -900, -40),
        ("NN-c", -200, -40),
        ("NN-d", 200, -40),
        ("NN-r2", 900, -40),
    ]:
        node(key, x, y)
    for key, a, b, line in [
        ("NE-l1", "NN-l1", "NN-a", "IL-h"),
        ("NE-core", "NN-a", "NN-b", "IL-h"),
        ("NE-r1", "NN-b", "NN-r1", "IL-h"),
        ("NE-l2", "NN-l2", "NN-c", "IL-c"),
        ("NE-c2", "NN-c", "NN-d", "IL-c"),
        ("NE-r2", "NN-d", "NN-r2", "IL-c"),
    ]:
        edge(key, a, b, line)
    edge("NE-fan1", "NN-a", "NN-u", role="crossover")
    edge(
        "NE-platform",
        "NN-u",
        "NN-v",
        role="arrival_departure_track",
        middle=((120, 30.00015),),
    )
    edge("NE-fan2", "NN-v", "NN-b", role="crossover")
    for key in ("NE-core", "NE-platform", "NE-c2"):
        line = repo.edges[key].infrastructure_line_id
        repo.station_tracks[key] = StationTrack(
            "TRK-" + key,
            "ST-s",
            key,
            edge_refs=path_refs(repo, [(key, True)]),
            infrastructure_line_id=line,
            yard_id="Y-h" if line == "IL-h" else "Y-c",
            railway_class="high_speed" if line == "IL-h" else "conventional",
            provenance={
                "yard": {
                    "name": "高速场" if line == "IL-h" else "普速场",
                    "source": "workspace_override",
                }
            },
        )
    context = [
        {
            "properties": {"boundary_kind": "station_area"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [119.997, 29.9995],
                        [120.003, 29.9995],
                        [120.003, 30.0003],
                        [119.997, 30.0003],
                        [119.997, 29.9995],
                    ]
                ],
            },
        }
    ]
    return repo, context, node, edge


def test_boundary_contains_disconnected_tracks_without_creating_crossing_node():
    from desktop.station_diagram.extractor import extract
    from desktop.station_diagram.topology import build_graph

    repo, context, node, edge = fixture()
    node("NN-x", -100, -50)
    node("NN-y", 100, 30)
    edge("NE-cross", "NN-x", "NN-y")
    data = extract(repo, context, DiagramOptions())
    assert "NE-cross" in data.inner
    graph = build_graph(repo, data.selected)
    assert not set(graph.adjacency["NN-x"]) & set(graph.adjacency["NN-a"])
    assert "NN-crossing" not in graph.adjacency


def test_unknown_yard_is_not_colored_by_nearest_connected_trunk():
    from desktop.station_diagram.yard_classifier import classify

    repo, _, _, _ = fixture()
    repo.lines["IL-unknown"] = InfrastructureLine("IL-unknown", "未命名轨道", "rail")
    e = repo.edges["NE-platform"]
    repo.edges[e.id] = replace(e, infrastructure_line_id="IL-unknown")
    repo.station_tracks[e.id] = replace(
        repo.station_tracks[e.id],
        infrastructure_line_id=None,
        yard_id=None,
        railway_class="unknown",
    )
    decisions, warnings = classify(repo, set(repo.edges), DiagramOptions())
    assert decisions[e.id].system is None
    assert warnings


def test_horizontal_core_uniform_spacing_and_shared_endpoints():
    repo, context, _, _ = fixture()
    old = deepcopy((repo, context))
    layout = build_layout(repo, context)
    assert len(layout.lanes) == 3
    ys = sorted(lane.y for lane in layout.lanes)
    assert ys[1] - ys[0] == pytest.approx(ys[2] - ys[1])
    for key, d in layout.edges.items():
        e = repo.edges[key]
        assert d.points[0] == layout.nodes[e.from_node_id]
        assert d.points[-1] == layout.nodes[e.to_node_id]
    assert (repo, context) == old


@pytest.mark.parametrize(
    "structure", ["main_branch", "yard_branch", "link", "separate"]
)
def test_four_external_structures_and_nonparallel_ports(structure):
    repo, context, node, edge = fixture()
    node("NN-out", 1100, 1300)
    start = {
        "main_branch": "NN-r1",
        "yard_branch": "NN-v",
        "link": "NN-r1",
        "separate": "NN-r2",
    }[structure]
    if structure == "link":
        edge("NE-link", start, "NN-r2", line="IL-c", role="crossover")
    else:
        edge("NE-branch", start, "NN-out", line="IL-c")
    layout = build_layout(repo, context, DiagramOptions(topology_depth=8))
    key = "NE-link" if structure == "link" else "NE-branch"
    assert key in layout.edges
    assert layout.edges[key].path
    if structure != "link":
        p = next(p for p in layout.ports if key in p["edge_ids"])
        assert p["side"] in ("top", "right")
        assert abs(p["angle_degrees"]) > 20


def test_conflicting_explicit_assignments_remain_unresolved():
    from desktop.station_diagram.yard_classifier import classify

    repo, _, _, _ = fixture()
    original = repo.station_tracks["NE-core"]
    repo.station_tracks["TRK-conflict"] = replace(
        original, id="TRK-conflict", infrastructure_line_id="IL-c", yard_id="Y-c"
    )
    decisions, warnings = classify(repo, set(repo.edges), DiagramOptions())
    assert decisions["NE-core"].system is None
    assert decisions["NE-core"].status == "unresolved"
    assert warnings


def test_final_direction_can_reverse_station_lane_order():
    repo, context, _, _ = fixture()
    for key, y in [("NE-r1", -0.003), ("NE-r2", 0.003)]:
        edge = repo.edges[key]
        node = repo.nodes[edge.to_node_id]
        repo.nodes[node.id] = replace(node, lat=30 + y)
        repo.edges[key] = replace(
            edge, coordinates=(edge.coordinates[0], (node.lon, 30 + y))
        )
    layout = build_layout(repo, context)
    ports = {p["line"].id: p for p in layout.ports if p["side"] == "right"}
    assert ports["IL-c"]["point"][1] < ports["IL-h"]["point"][1]
    lanes = {l.system: l.y for l in layout.lanes}
    assert lanes["测试高速铁路"] < lanes["测试普速铁路"]


def test_vertical_mainline_exit_is_not_mistaken_for_a_platform_end():
    repo, context, node, edge = fixture()
    node("NN-north", 0, 1600)
    edge("NE-north", "NN-b", "NN-north", line="IL-c")
    layout = build_layout(repo, context)
    port = next(p for p in layout.ports if "NE-north" in p["edge_ids"])
    assert port["side"] == "top"
    assert port["point"][1] == layout.plot_bounds[1]


def test_manual_pending_assignment_is_not_replaced_by_source_line():
    from desktop.station_diagram.yard_classifier import classify

    repo, _, _, _ = fixture()
    old = repo.station_tracks["NE-core"]
    repo.station_tracks["NE-core"] = replace(
        old,
        infrastructure_line_id=None,
        yard_id=None,
        railway_class="unknown",
        provenance={
            "line_membership": {"source": "workspace_override", "line_id": None},
            "yard": {
                "source": "workspace_override",
                "name": "",
                "railway_class": "unknown",
            },
        },
    )
    own, warnings = classify(repo, set(repo.edges), DiagramOptions())
    assert own["NE-core"].line_id is None
    assert own["NE-core"].system is None
    assert warnings


def test_nonconnecting_drawing_crossings_do_not_create_switches():
    from desktop.station_diagram.crossings import crossing_symbols
    from desktop.station_diagram.topology import build_graph
    from desktop.station_diagram_layout import DiagramEdge

    repo, _, node, edge = fixture()
    node("NN-x", 0, -50)
    node("NN-y", 0, 30)
    edge("NE-cross", "NN-x", "NN-y")
    graph = build_graph(repo, {"NE-core", "NE-cross"})
    drawings = {
        "NE-core": DiagramEdge("NE-core", ((0, 0), (100, 0)), "main", False, "throat"),
        "NE-cross": DiagramEdge(
            "NE-cross", ((50, -50), (50, 50)), "main", False, "throat"
        ),
    }
    nodes = {"NN-a": (0, 0), "NN-b": (100, 0), "NN-x": (50, -50), "NN-y": (50, 50)}
    assert crossing_symbols(drawings, graph, nodes)[0]["point"] == (50, 0)
    assert all(len(keys) == 1 for keys in graph.adjacency.values())


def test_source_boundary_is_not_a_building_and_missing_refs_are_rejected():
    from desktop.station_diagram.extractor import extract

    repo, context, _, _ = fixture()
    context[0]["properties"]["boundary_kind"] = "station_building"
    result = extract(repo, context, DiagramOptions())
    assert (
        result.boundary_source == "associated_station_tracks_reference"
        and result.warnings
    )
    del repo.edges["NE-core"]
    with pytest.raises(ValueError, match="缺少物理轨道"):
        extract(repo, context, DiagramOptions())


def test_shared_yard_editor_survives_override_and_workspace_roundtrip(qtbot, tmp_path):
    from desktop.station_track_ui import StationTrackDialog
    from desktop.station_tracks import track_overrides
    from desktop.station_diagram.renderer import edge_color
    from railscope.workspace import SQLiteWorkspace
    from railscope.integrity import validate_repository
    from PySide6.QtWidgets import QTableWidgetItem

    repo, _, _, _ = fixture()
    dialog = StationTrackDialog(repo)
    qtbot.addWidget(dialog)
    row = dialog.keys.index("NE-platform")
    dialog.table.setItem(row, 5, QTableWidgetItem("城际场"))
    dialog.table.cellWidget(row, 6).setCurrentIndex(
        dialog.table.cellWidget(row, 6).findData("other")
    )
    dialog.table.cellWidget(row, 7).setCurrentIndex(0)
    dialog.table.cellWidget(row, 8).setCurrentIndex(
        dialog.table.cellWidget(row, 8).findData("intercity")
    )
    dialog.collect()
    track = repo.station_tracks["NE-platform"]
    yard = repo.yards[track.yard_id]
    assert yard.yard_type == "intercity" and yard.station_id == track.station_id
    rows = {
        key: {
            "keys": [key],
            "source_edges": list(
                t.edge_refs[i].edge_id for i in range(len(t.edge_refs))
            ),
            "station_source": "node/test",
            "station_name": "测试站",
            "bounds": [],
        }
        for key, t in repo.station_tracks.items()
    }
    saved = track_overrides(repo, rows)["NE-platform"]
    assert saved["station_yard"]["id"] == yard.id
    assert saved["station_track"]["infrastructure_line_id"] is None
    repo.station_tracks = {track.id: track for track in repo.station_tracks.values()}
    store = SQLiteWorkspace(tmp_path / "workspace.sqlite")
    store.seed(repo)
    imported, _ = store.load()
    validate_repository(imported)
    layout = build_layout(imported)
    own = layout.ownership["NE-platform"]
    assert own.yard_type == "intercity"
    assert (
        edge_color(
            imported,
            imported.edges["NE-platform"],
            DiagramOptions(),
            system=own.system,
            ownership=own,
        )
        == "#a13cad"
    )


def test_editor_validation_is_atomic(qtbot):
    from desktop.station_track_ui import StationTrackDialog

    repo, _, _, _ = fixture()
    old = deepcopy(repo)
    dialog = StationTrackDialog(repo)
    qtbot.addWidget(dialog)
    dialog.table.item(0, 0).setText("已修改")
    dialog.table.item(1, 0).setText("")
    with pytest.raises(ValueError, match="不能为空"):
        dialog.collect()
    assert repo == old
