"""Current yard-relative engine: geometry, labeling and source facts contracts."""

from copy import deepcopy
from dataclasses import replace
import json
from xml.etree import ElementTree as ET

import pytest

from railscope.domain import Yard
from desktop.tests.test_station_outlets import pair_repository
from desktop.station_diagram_layout import DiagramOptions, build_layout
from desktop.station_schematic import station_svg, port_destination
from desktop.rail_line_terminals import scope_terminals, integrate_terminals


def yard_repo():
    repo, context = pair_repository()
    for index in range(3):
        ident = "Y-" + str(index)
        repo.yards[ident] = Yard(ident, "ST-a", "场 " + str(index), "high_speed")
        for key, t in list(repo.station_tracks.items()):
            if key.startswith("NE-" + str(index)):
                repo.station_tracks[key] = replace(
                    t,
                    yard_id=ident,
                    track_number=str(index * 2 + int(key.split("-")[2]) + 1),
                )
    return repo, context


def test_distinct_yards_have_uniform_colors_without_rewriting_source():
    repo, context = yard_repo()
    original = deepcopy((repo, context))
    svg = ET.fromstring(station_svg(repo, context))
    paths = {
        e.attrib["data-edge-id"]: e for e in svg.iter() if "data-edge-id" in e.attrib
    }
    groups = []
    for index in range(3):
        colors = {
            paths[k].attrib["stroke"]
            for k in paths
            if k.startswith("NE-" + str(index)) and k.endswith("yard")
        }
        assert len(colors) == 1
        groups.extend(colors)
    assert len(set(groups)) == 3
    assert repo == original[0] and context == original[1]


def test_real_junctions_are_shared_and_extensions_are_separate():
    repo, context = yard_repo()
    layout = build_layout(repo, context)
    for key, drawing in layout.edges.items():
        edge = repo.edges[key]
        assert drawing.points[0] == layout.nodes[edge.from_node_id]
        assert drawing.points[-1] == layout.nodes[edge.to_node_id]
    assert layout.extensions
    assert all(e["edge_id"] in repo.edges for e in layout.extensions)
    assert not build_layout(
        repo, context, DiagramOptions(align_main_outlets=False)
    ).extensions


def test_every_track_and_physical_platform_gets_one_nonoverlapping_label():
    repo, context = yard_repo()
    root = ET.fromstring(station_svg(repo, context))
    meta = json.loads(root.find("{http://www.w3.org/2000/svg}metadata").text)
    labels = meta["annotations"]
    assert len([p for p in labels if p["kind"] == "track"]) == len(repo.station_tracks)
    assert len([p for p in labels if p["kind"] == "physical-platform"]) == 1
    assert len({p["id"] for p in labels if p["kind"] == "track"}) == len(
        repo.station_tracks
    )
    for index, a in enumerate(labels):
        for b in labels[index + 1 :]:
            x0, y0, x1, y1 = a["bounds"]
            u0, v0, u1, v1 = b["bounds"]
            assert not (x0 < u1 and x1 > u0 and y0 < v1 and y1 > v0)


def test_display_ids_are_not_written_as_official_platform_numbers():
    repo, context = yard_repo()
    before = deepcopy(repo.station_tracks)
    layout = build_layout(repo, context)
    assert any(p["text"].startswith("台体 ") for p in layout.annotations)
    assert repo.station_tracks == before
    assert all(t.platform_number is None for t in repo.station_tracks.values())


def test_scoped_terminals_require_continuity_and_manual_facts_win():
    assert scope_terminals([{"span": "甲~乙"}, {"span": "乙~丙"}]) == ("甲", "丙")
    assert scope_terminals([{"span": "甲~乙"}, {"span": "丁~丙"}]) is None
    repo, _ = pair_repository()
    repo.lines["IL-0"] = replace(
        repo.lines["IL-0"], name="沪昆高速线", start_terminal="人工起点"
    )
    integrate_terminals(repo)
    assert repo.lines["IL-0"].start_terminal == "人工起点"
    assert repo.lines["IL-0"].end_terminal == "昆明南"
    assert repo.lines["IL-0"].provenance["terminal_reference"]["snapshot_id"]


def test_station_at_route_terminus_labels_the_other_terminus():
    repo, _ = pair_repository()
    line = repo.lines["IL-0"]
    info = {
        "line_destinations": {
            line.id: {
                "terminals": [
                    {"name": "本站", "coordinates": (120, 30)},
                    {"name": "终点", "coordinates": (119, 29)},
                ]
            }
        }
    }
    assert (
        port_destination(
            {"line": line, "vector": (1, 1), "side": "right"},
            info,
            lambda p: ((p[0] - 120) * 100000, (p[1] - 30) * 100000),
        )
        == "终点"
    )


def test_editor_persists_yard_track_and_extension_controls(qtbot, tmp_path):
    from desktop.station_diagram_ui import StationDiagramDialog

    repo, context = yard_repo()
    path = tmp_path / "settings.json"
    dialog = StationDiagramDialog(repo, context, settings_path=path)
    qtbot.addWidget(dialog)
    assert dialog.controls["remove_common_bend"].isChecked()
    row = next(
        i
        for i in range(dialog.yard_table.rowCount())
        if dialog.yard_table.item(i, 0).data(256) == "Y-0"
    )
    dialog.yard_table.item(row, 2).setText("#123456")
    dialog.track_table.item(0, 1).setText("人工图示分场")
    dialog.track_table.item(0, 2).setText("试验股道")
    dialog.port_table.cellWidget(0, 3).setCurrentIndex(2)
    dialog.controls["station_compression"].setValue(4)
    dialog.controls["platform_width"].setValue(1.4)
    dialog.platform_table.item(0, 1).setText("A")
    dialog.platform_table.item(0, 2).setText("1;2")
    options = dialog.options()
    dialog.save_settings(options)
    other = StationDiagramDialog(repo, context, settings_path=path)
    qtbot.addWidget(other)
    assert other.options() == options
    assert options.yard_overrides["Y-0"]["color"] == "#123456"
    assert any(
        rule.get("faces") == "1;2" for rule in options.platform_overrides.values()
    )
    assert any(rule.get("extend") is False for rule in options.port_overrides.values())
    assert any(
        rule.get("label") == "试验股道" for rule in options.track_overrides.values()
    )


def test_common_bending_is_computed_separately_for_each_yard():
    from desktop.station_diagram_geometry import baseline_value

    repo, context = yard_repo()
    for key, edge in list(repo.edges.items()):
        if key.endswith("yard"):
            a, b = edge.coordinates
            amount = 0.0001 if key.startswith("NE-0") else -0.00015
            repo.edges[key] = replace(
                edge,
                coordinates=(a, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + amount), b),
            )
    original = deepcopy(repo)
    layout = build_layout(repo, context)
    first = layout.group_baselines["Y-0"]
    second = layout.group_baselines["Y-1"]
    assert baseline_value(first, (first[0][0] + first[-1][0]) / 2) > baseline_value(
        first, first[0][0]
    )
    assert baseline_value(second, (second[0][0] + second[-1][0]) / 2) < baseline_value(
        second, second[0][0]
    )
    assert repo == original


def test_length_uniform_direction_is_independent_of_vertex_density():
    from desktop.station_diagram.outlet_layout import linear_direction

    path = [(0, 0), (100, 40), (220, 50)]
    dense = [(x, x * 0.4) for x in range(101)] + [(220, 50)]
    assert linear_direction([path], 1) == pytest.approx(linear_direction([dense], 1))
    assert linear_direction([path], 1) == pytest.approx(
        linear_direction([path[:2], path[1:]], 1)
    )


def test_first_order_outlets_preserve_real_endpoint_connections():
    from desktop.tests.test_station_engineering import fixture

    repo, context, _, _ = fixture()
    layout = build_layout(repo, context)
    for key in ("NE-r1", "NE-r2"):
        points = layout.edges[key].parts[-1]
        a, b = points[-2:]
        for p in points[-3:]:
            assert (p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (
                b[0] - a[0]
            ) == pytest.approx(0, abs=1e-6)


def test_source_platform_faces_are_distinct_from_physical_body_id():
    repo, context = yard_repo()
    context = deepcopy(context)
    context[0]["properties"]["way_tags"] = {"railway": "platform", "ref": "8;9"}
    layout = build_layout(repo, context)
    assert {a["text"] for a in layout.annotations if a["kind"] == "platform-face"} == {
        "站台 8",
        "站台 9",
    }
    assert [
        a["text"] for a in layout.annotations if a["kind"] == "physical-platform"
    ] == ["台体 1"]


def test_connected_target_mainline_survives_outside_station_selection():
    from desktop.tests.test_station_engineering import fixture
    from railscope.domain import InfrastructureLine

    repo, context, node, edge = fixture()
    repo.lines["IL-target"] = InfrastructureLine(
        "IL-target", "目标铁路", "rail", line_role="main_line"
    )
    node("NN-target-a", 1100, 600)
    node("NN-target-b", 1700, 600)
    edge("NE-connector-target", "NN-r2", "NN-target-a", line="IL-c", role="crossover")
    edge("NE-target", "NN-target-a", "NN-target-b", line="IL-target")
    layout = build_layout(repo, context)
    assert "NE-target" in layout.edges
    assert any(
        a["kind"] == "main-line" and a["text"] == "目标铁路" for a in layout.annotations
    )


def test_platform_polygon_does_not_invent_boarding_face_count():
    repo, context = yard_repo()
    layout = build_layout(repo, context)
    assert [a["text"] for a in layout.annotations if a["kind"] == "platform-face"] == [
        "面号待核对"
    ]
    assert any("未推定面数" in warning for warning in layout.warnings)


def test_older_local_snapshot_gets_missing_terminal_fields(tmp_path):
    from desktop.china_emu import atomic_json, load_store, SCHEMA
    from desktop.tests.test_china_emu import profile

    path = tmp_path / "reference.json"
    p = profile(
        "line",
        "测试铁路",
        {"gauge_mm": "1435"},
        [{"name": "测试铁路", "span": "甲~乙", "attributes": {"gauge_mm": "1435"}}],
    )
    atomic_json(path, {"schema": SCHEMA, "profiles": [p]})
    before = path.read_bytes()
    store = load_store(path)
    matched = store.match("line", ["测试铁路"])
    assert matched is not None
    assert matched["attributes"]["start_terminal"] == "甲"
    assert matched["attributes"]["end_terminal"] == "乙"
    assert matched["snapshot_id"] == p["snapshot_id"]
    assert path.read_bytes() == before


def test_conflicting_yard_assignments_remain_neutral_in_rendered_svg():
    from desktop.tests.test_station_engineering import fixture

    repo, context, _, _ = fixture()
    track = repo.station_tracks["NE-core"]
    repo.station_tracks["TRK-conflict"] = replace(
        track, id="TRK-conflict", infrastructure_line_id="IL-c", yard_id="Y-c"
    )
    svg = ET.fromstring(station_svg(repo, context))
    path = next(p for p in svg.iter() if p.attrib.get("data-edge-id") == "NE-core")
    assert path.attrib["stroke"] == "#85919b"


def test_auxiliary_only_switch_does_not_extend_mainline_interaction_boundary():
    from types import SimpleNamespace
    from desktop.station_diagram.station_extent import functional_interval

    repo = SimpleNamespace(
        edges={key: SimpleNamespace(from_node_id="NN-aux") for key in ("a", "b", "c")}
    )
    raw = {key: ((1000, 0), (1100, 0)) for key in repo.edges}
    graph = SimpleNamespace(adjacency={"NN-aux": list(raw)})
    ownership = {key: SimpleNamespace(line_id=None) for key in raw}
    interval, _ = functional_interval(
        repo,
        raw,
        graph,
        set(raw),
        [(0, 0), (10, 0)],
        dict.fromkeys(raw, "auxiliary"),
        ownership,
    )
    assert interval == (-15, 25)


def test_editor_batches_selected_tracks_into_a_drawing_yard(qtbot, monkeypatch):
    from desktop.station_diagram_ui import StationDiagramDialog, QInputDialog
    from PySide6.QtCore import QItemSelectionModel

    repo, context = yard_repo()
    before = deepcopy(repo)
    dialog = StationDiagramDialog(repo, context)
    qtbot.addWidget(dialog)
    selected = dialog.track_table.selectionModel()
    for row in (0, 1):
        selected.select(
            dialog.track_table.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("图示新场", True))
    dialog.assign_selected_yard()
    assert (
        sum(
            rule.get("group_id") == "diagram-yard:图示新场"
            for rule in dialog.options().track_overrides.values()
        )
        == 2
    )
    assert repo == before


def test_manual_port_position_cannot_silently_place_caption_outside_canvas():
    repo, context = yard_repo()
    port = build_layout(repo, context).ports[0]
    options = DiagramOptions(port_overrides={port["key"]: {"dy": 1000}})
    with pytest.raises(ValueError, match="端口标签"):
        station_svg(repo, context, options=options)
