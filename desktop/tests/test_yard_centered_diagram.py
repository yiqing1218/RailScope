"""Yard selection, connected throats and consistent outbound physical pairs."""

from copy import deepcopy
from dataclasses import replace
from xml.etree import ElementTree as ET

import pytest
from shapely.geometry import LineString

from desktop.tests.test_yard_relative_diagram import yard_repo
from desktop.station_diagram_layout import DiagramOptions, build_layout
from desktop.station_schematic import station_svg
from railscope.domain import NetworkEdge, NetworkNode


def test_parallel_outlets_preserve_screen_order_and_station_spacing():
    repo, context = yard_repo()
    layout = build_layout(repo, context)
    port = next(p for p in layout.ports if p["line"].id == "IL-0")
    tracks = sorted(
        (layout.edges[f"NE-0-{i}"] for i in (0, 1)), key=lambda d: d.points[-1][1]
    )
    assert not LineString(tracks[0].points).intersects(LineString(tracks[1].points))
    near_gap = abs(tracks[1].points[-1][1] - tracks[0].points[-1][1])
    far_gap = abs(port["points"][1][1] - port["points"][0][1])
    assert far_gap == pytest.approx(near_gap, rel=0.02)


def test_platform_rails_stay_straight_with_common_bend_control_off_or_on():
    repo, context = yard_repo()
    for key in ("NE-0-0-yard", "NE-0-1-yard"):
        edge = repo.edges[key]
        a, b = edge.coordinates
        repo.edges[key] = replace(
            edge,
            coordinates=(
                a,
                ((a[0] + b[0]) / 2, a[1] + 0.0001),
                b,
            ),
        )
    straight = build_layout(repo, context)
    source = build_layout(repo, context, DiagramOptions(remove_common_bend=False))

    def bend(layout):
        points = layout.edges["NE-0-0-yard"].points
        chord = LineString([points[0], points[-1]])
        return LineString(points).hausdorff_distance(chord)

    assert bend(straight) < 0.01
    assert bend(source) < 0.01


def test_selected_yard_does_not_export_other_platform_tracks():
    repo, context = yard_repo()
    original = deepcopy(repo)
    layout = build_layout(repo, context, DiagramOptions(selected_yards=("Y-1",)))
    assert set(layout.groups) == {"Y-1"}
    assert {lane.id for lane in layout.lanes} == {"NE-1-0-yard", "NE-1-1-yard"}
    assert {"NE-1-0", "NE-1-1"} <= layout.edges.keys()
    assert repo == original


def test_yard_selection_retains_other_lines_connected_approach_refs():
    repo, context = yard_repo()
    first, second = repo.edges["NE-0-0"], repo.edges["NE-1-0"]
    repo.edges["NE-link"] = NetworkEdge(
        "NE-link",
        first.from_node_id,
        second.from_node_id,
        (first.coordinates[0], second.coordinates[0]),
        90,
        track_role="connecting_line",
    )
    # Importers also store short approach pieces as StationTracks. A different
    # yard's approach must remain visible when a selected yard connects to it.
    template = repo.station_tracks["NE-1-0-yard"]
    repo.station_tracks["TRK-approach"] = replace(
        template,
        id="TRK-approach",
        track_number=None,
        edge_refs=(replace(template.edge_refs[0], edge_id=second.id),),
    )
    layout = build_layout(repo, context, DiagramOptions(selected_yards=("Y-0",)))
    assert {"NE-link", "NE-1-0"} <= layout.edges.keys()
    assert "NE-1-0-yard" not in layout.edges


def test_station_siding_continuation_is_not_left_as_disconnected_stub():
    repo, context = yard_repo()
    e = repo.edges["NE-0-0-yard"]
    end = (e.coordinates[-1][0] + 0.0005, e.coordinates[-1][1])
    repo.nodes["NN-new"] = NetworkNode("NN-new", *end)
    repo.edges["NE-new"] = NetworkEdge(
        "NE-new",
        e.to_node_id,
        "NN-new",
        (e.coordinates[-1], end),
        50,
        infrastructure_line_id="IL-0",
        track_role="other_station_track",
        service="siding",
    )
    layout = build_layout(repo, context)
    assert "NE-new" in layout.edges
    assert layout.edges["NE-new"].points[0] == layout.edges[e.id].points[-1]


def test_unofficial_track_labels_and_crossing_ticks_are_not_drawn():
    repo, context = yard_repo()
    for key, t in list(repo.station_tracks.items()):
        repo.station_tracks[key] = replace(t, track_number=None)
    root = ET.fromstring(station_svg(repo, context))
    assert not any(e.attrib.get("data-label-kind") == "track" for e in root.iter())
    assert not any("data-switch-node" in e.attrib for e in root.iter())
    assert not any(e.attrib.get("data-crossing") == "unconnected" for e in root.iter())


@pytest.mark.parametrize("ref", ["8;9", "8&9", "8 & 9"])
def test_yard_scale_is_multiline_and_source_body_count_is_distinct_from_faces(ref):
    repo, context = yard_repo()
    context[0]["properties"]["way_tags"]["ref"] = ref
    root = ET.fromstring(station_svg(repo, context))
    labels = [e for e in root.iter() if e.attrib.get("data-label-kind") == "yard"]
    assert labels
    assert all(len(e.findall("{http://www.w3.org/2000/svg}text")) == 2 for e in labels)
    assert any("1 台体 / 2 股道 / 2 站台面" in "".join(e.itertext()) for e in labels)
    faces = [
        e for e in root.iter() if e.attrib.get("data-label-kind") == "platform-face"
    ]
    assert {"站台 8", "站台 9"} <= {"".join(e.itertext()) for e in faces}


def test_yard_selection_survives_export_dialog_settings(qtbot, tmp_path):
    from desktop.station_diagram_ui import StationDiagramDialog

    repo, context = yard_repo()
    path = tmp_path / "diagram.json"
    dialog = StationDiagramDialog(repo, context, settings_path=path)
    qtbot.addWidget(dialog)
    dialog.yard_selector.setCurrentIndex(dialog.yard_selector.findText("场 1"))
    assert dialog.refresh_preview()
    assert dialog.options().selected_yards == ("Y-1",)
    dialog.save_settings(dialog.options())
    restored = StationDiagramDialog(repo, context, settings_path=path)
    qtbot.addWidget(restored)
    assert restored.options().selected_yards == ("Y-1",)
