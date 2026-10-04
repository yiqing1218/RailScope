"""Station straightness, monotone throats and usable manual yard membership."""

from copy import deepcopy
from dataclasses import replace

import pytest
from shapely.geometry import box

from desktop.tests.test_yard_relative_diagram import yard_repo
from desktop.station_diagram_layout import DiagramOptions, build_layout, DiagramEdge
from desktop.station_diagram.connection_layout import (
    arrange_yard_connections,
    cubic,
    point,
)
from desktop.station_diagram.topology import build_graph
from desktop.station_diagram.yard_classifier import classify
from desktop.station_diagram.reference_layout import smooth_main_references
from desktop.station_diagram.types import Graph


@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
def test_platform_tracks_are_exactly_straight_even_without_yard_data(orientation):
    repo, context = yard_repo()
    key = "NE-0-0-yard"
    a, b = repo.edges[key].coordinates
    repo.edges[key] = replace(
        repo.edges[key],
        coordinates=(
            a,
            ((a[0] + b[0]) / 2, a[1] + 0.0001),
            b,
        ),
    )
    for ident, track in list(repo.station_tracks.items()):
        repo.station_tracks[ident] = replace(track, yard_id=None)
    original = deepcopy(repo)
    layout = build_layout(repo, context, DiagramOptions(orientation=orientation))
    points = layout.edges[key].points
    cross = 1 if orientation == "landscape" else 0
    assert max(p[cross] for p in points) - min(p[cross] for p in points) < 0.01
    assert repo == original


def test_station_track_throat_refs_use_a_monotone_template():
    repo, _ = yard_repo()
    key = "NE-0-0-yard"
    edge = repo.edges[key]
    raw = {key: ((20, 0), (30, 10), (40, 0))}
    nodes = {edge.from_node_id: (200, 100), edge.to_node_id: (400, 100)}
    graph = build_graph(repo, {key})
    ownership, _ = classify(repo, {key}, DiagramOptions())
    drawing = ((200, 100), (300, 170), (400, 100))
    edges = {key: DiagramEdge(key, drawing, "station", False, "station")}
    arrange_yard_connections(
        repo,
        graph,
        raw,
        edges,
        nodes,
        ownership,
        (0, 10),
        box(0, 0, 1000, 1000),
        [],
        [],
    )
    assert max(p[1] for p in edges[key].points) < 100.01
    assert edges[key].points[0] == nodes[edge.from_node_id]
    assert edges[key].points[-1] == nodes[edge.to_node_id]


def test_bad_outward_tangents_cannot_add_a_small_reverse_loop():
    curve = cubic((10, 20), (20, 30), start_outward=(1, 0), end_outward=(-1, 0))
    samples = [point(curve, i / 30) for i in range(31)]
    assert all(10 <= p[0] <= 20 and 20 <= p[1] <= 30 for p in samples)
    assert all(a[0] <= b[0] for a, b in zip(samples, samples[1:]))


def test_number_ranges_create_a_selectable_persistent_yard(qtbot, tmp_path):
    from desktop.station_diagram_ui import StationDiagramDialog

    repo, context = yard_repo()
    original = deepcopy(repo)
    path = tmp_path / "diagram.json"
    dialog = StationDiagramDialog(repo, context, settings_path=path)
    qtbot.addWidget(dialog)
    dialog.yard_track_numbers.setText("1-2,4")
    dialog.yard_assignment_name.setText("自定义场")
    dialog.apply_yard_assignment()
    rules = dialog.options().track_overrides
    ids = {
        t.id for t in repo.station_tracks.values() if t.track_number in ("1", "2", "4")
    }
    assert {
        k for k, v in rules.items() if v.get("group_id") == "diagram-yard:自定义场"
    } == ids
    assert dialog.yard_selector.findText("自定义场") >= 0
    dialog.yard_selector.setCurrentIndex(dialog.yard_selector.findText("自定义场"))
    assert dialog.refresh_preview()
    assert set(build_layout(repo, context, dialog.options()).groups) == {
        "diagram-yard:自定义场"
    }
    dialog.save_settings(dialog.options())
    restored = StationDiagramDialog(repo, context, settings_path=path)
    qtbot.addWidget(restored)
    assert restored.options().track_overrides == rules
    assert restored.yard_selector.currentText() == "自定义场"
    assert repo == original


def test_manual_yard_assignment_rejects_missing_numbers_without_partial_edits(qtbot):
    from desktop.station_diagram_ui import StationDiagramDialog

    repo, context = yard_repo()
    dialog = StationDiagramDialog(repo, context)
    qtbot.addWidget(dialog)
    before = deepcopy(dialog.options().track_overrides)
    dialog.yard_track_numbers.setText("1,99")
    dialog.yard_assignment_name.setText("自定义场")
    dialog.apply_yard_assignment()
    assert dialog.options().track_overrides == before
    assert "99" in dialog.status.text()


def test_click_preview_selects_unnumbered_rail_and_applies_yard(qtbot):
    from PySide6.QtCore import Qt
    from desktop.station_diagram_ui import StationDiagramDialog

    repo, context = yard_repo()
    key = "NE-0-0-yard"
    repo.station_tracks[key] = replace(repo.station_tracks[key], track_number=None)
    dialog = StationDiagramDialog(repo, context)
    qtbot.addWidget(dialog)
    layout = dialog.current_layout
    points = layout.edges[key].points
    target = tuple((points[0][i] + points[-1][i]) / 2 for i in (0, 1))
    dialog.select_preview_track(target, Qt.KeyboardModifier.NoModifier)
    selected = {
        dialog.track_table.item(i.row(), 0).data(Qt.ItemDataRole.UserRole)
        for i in dialog.track_table.selectedIndexes()
    }
    assert key in selected and dialog.preview.selected_parts
    dialog.yard_assignment_name.setText("手工选择场")
    rows = sorted({i.row() for i in dialog.track_table.selectedIndexes()})
    dialog.set_yard_rows(rows, dialog.yard_assignment_name.text())
    assert (
        dialog.options().track_overrides[key]["group_id"] == "diagram-yard:手工选择场"
    )
    assert dialog.yard_selector.findText("手工选择场") >= 0


def test_reset_manual_yard_updates_selector_and_returns_to_all(qtbot):
    from desktop.station_diagram_ui import StationDiagramDialog

    repo, context = yard_repo()
    dialog = StationDiagramDialog(repo, context)
    qtbot.addWidget(dialog)
    dialog.yard_track_numbers.setText("1 - 2")
    dialog.yard_assignment_name.setText("手工场")
    dialog.apply_yard_assignment()
    dialog.yard_selector.setCurrentIndex(dialog.yard_selector.findText("手工场"))
    assert dialog.refresh_preview()
    dialog.reset_editor("track")
    assert dialog.options().selected_yards == ()
    assert dialog.yard_selector.findText("手工场") == -1
    assert dialog.current_layout.groups


def test_main_reference_smooths_switch_wiggles_when_no_node_is_inside_platforms():
    # The platform edge crosses the whole band without a geometry split there.
    # Three real switches outside it must form one broad curve, not three humps.
    graph = Graph(
        {"a": ("n0", "n1"), "b": ("n1", "n2"), "c": ("n2", "n3"), "d": ("n3", "n4")},
        {
            "n0": ["a"],
            "n1": ["a", "b"],
            "n2": ["b", "c"],
            "n3": ["c", "d"],
            "n4": ["d"],
        },
        [],
    )
    raw = {
        "a": ((-10, 0), (20, 0)),
        "b": ((20, 0), (30, 0)),
        "c": ((30, 0), (40, 0)),
        "d": ((40, 0), (50, 0)),
    }
    nodes = {
        "n0": (-10, 100),
        "n1": (20, 110),
        "n2": (30, 90),
        "n3": (40, 115),
        "n4": (50, 100),
    }
    edges = {
        k: DiagramEdge(k, (nodes[aa], nodes[bb]), "main", False, "station")
        for k, (aa, bb) in graph.endpoints.items()
    }
    edges["a"] = replace(
        edges["a"], points=((-10, 100), (0, 100), (10, 100), (20, 110))
    )
    source = deepcopy(raw)
    changed = smooth_main_references(
        graph,
        raw,
        edges,
        nodes,
        [[((-10, 0), (20, 0), (30, 0), (40, 0), (50, 0))]],
        (0, 10),
        (-20, 60),
        core_frame=(0, 50, 10, 150),
    )
    assert set(changed) == {"b", "c", "d"}
    assert all(abs(nodes[n][1] - 100) < 0.001 for n in ("n1", "n2", "n3", "n4"))
    assert raw == source


def test_yard_scale_counts_rails_instead_of_duplicate_source_track_fragments():
    repo, context = yard_repo()
    for key in ("NE-0-0-yard", "NE-0-1-yard"):
        t = repo.station_tracks[key]
        repo.station_tracks["fragment:" + key] = replace(
            t, id="fragment:" + key, track_number=None
        )
    layout = build_layout(repo, context)
    scale = layout.groups["Y-0"]["display_scale"]
    assert scale["source_track_objects"] == 4
    assert scale["tracks"] == 2


def test_small_selected_yard_does_not_stretch_rails_and_platforms_to_page_height():
    from desktop.station_diagram.track_layout import core_rail_count

    repo, context = yard_repo()
    layout = build_layout(repo, context, DiagramOptions(selected_yards=("Y-0",)))
    assert core_rail_count(layout, layout.groups["Y-0"]["edge_ids"]) == 2
    rails = [layout.edges[k] for k in ("NE-0-0-yard", "NE-0-1-yard")]
    assert abs(rails[0].points[0][1] - rails[1].points[0][1]) < layout.height * 0.06


def test_explicit_manual_yard_color_resolves_display_without_changing_source_conflict():
    from desktop.station_schematic import station_svg
    from xml.etree import ElementTree as ET

    repo, context = yard_repo()
    key = "NE-0-0-yard"
    t = repo.station_tracks[key]
    repo.station_tracks["conflict"] = replace(t, id="conflict", yard_id="Y-1")
    opts = DiagramOptions(
        track_overrides={
            ident: {"group_id": "diagram-yard:手工场"} for ident in (key, "conflict")
        },
        yard_overrides={"diagram-yard:手工场": {"color": "#112233"}},
    )
    original = deepcopy(repo)
    root = ET.fromstring(station_svg(repo, context, options=opts))
    path = next(p for p in root.iter() if p.attrib.get("data-edge-id") == key)
    assert path.attrib["stroke"] == "#112233"
    layout = build_layout(repo, context, opts)
    assert layout.ownership[key].status == "unresolved"
    assert repo == original
