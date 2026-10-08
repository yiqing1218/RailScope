import json
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout
from desktop.session_time import SessionTime
from desktop.rail import compile_rail_plan, shared_document
from desktop.domain_adapter import build_repository
from desktop.operating import Plan
from desktop.operating_ui import OperationsEditor
from desktop.tests.test_operating_ui import MapStub
from railscope.domain import InfrastructureLifecycle, Vehicle
from railscope.workspace import WorkspaceObjects
from railscope.services.analysis import station_timetable


def test_shared_clock_only_one_player_advances_and_jump_syncs(tmp_path):
    app = QApplication.instance() or QApplication([])
    from desktop.station_schematic import ensure_export_font

    ensure_export_font()
    session = SessionTime()
    editors = [
        OperationsEditor(Plan([]), MapStub(), [], tmp_path / f"{i}.json")
        for i in range(2)
    ]
    sidebars = []
    for editor in editors:
        editor.bind_session(session)
        sidebars.append(editor.sidebar())
    editors[0].play()
    editors[1].play()
    assert not editors[0].playing and session.driver is editors[1]
    editors[0].clock = 3600
    assert editors[1].clock == 3600 and session.seconds == 3600
    session.set_day("2015-06-01")
    assert session.day == "2015-06-01" and not session.current_date
    for editor in editors:
        editor.timer.stop()
        editor.close()


def test_actual_date_mode_rolls_over_and_invalid_input_does_not_change_mode(
    monkeypatch,
):
    import pytest
    import desktop.session_time as module
    from datetime import date

    app = QApplication.instance() or QApplication([])

    class Calendar(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 1)

    session = module.SessionTime()
    session.set_day("2015-01-01")
    session.check_today()
    assert session.day == "2015-01-01"
    monkeypatch.setattr(module, "date", Calendar)
    session.set_day()
    assert session.day == "2026-10-01" and session.current_date
    with pytest.raises(ValueError):
        session.set_day("invalid")
    assert session.current_date
    session.day = "2026-09-30"
    session.check_today()
    assert session.day == "2026-10-01"


def detour_fixture():
    points = {1: [120, 30], 2: [120.01, 30], 3: [120.02, 30], 4: [120.01, 30.001]}
    edges = [
        {
            "id": key,
            "from_node": a,
            "to_node": b,
            "node_ids": [a, b],
            "coordinates": [points[a], points[b]],
            "construction": False,
            "way_tags": {
                "railway": "rail",
                "service": "siding" if key in ("ap", "pc") else "main",
            },
        }
        for key, a, b in [("ab", 1, 2), ("bc", 2, 3), ("ap", 1, 4), ("pc", 4, 3)]
    ]
    payload = {
        "schema": "railscope.rail-plan.v2",
        "service_date": "2026-09-30",
        "timezone": "Asia/Shanghai",
        "source": "test",
        "extensions": {},
        "required_capabilities": [],
        "routes": [
            {
                "id": "c",
                "name": "A-C",
                "extensions": {},
                "path": [
                    {"edge_id": key, "direction": "forward"} for key in ("ab", "bc")
                ],
            }
        ],
        "station_routes": [
            {
                "id": "route",
                "station_id": "2",
                "entry_node_id": "1",
                "exit_node_id": "3",
                "edge_refs": [
                    {"edge_id": key, "direction": "forward"} for key in ("ap", "pc")
                ],
                "verification_status": "user_verified",
            }
        ],
        "trains": [
            {
                "id": "G1",
                "route_id": "c",
                "extensions": {
                    "railscope.org/provenance": {"verification_status": "user_verified"}
                },
                "stops": [
                    {"node_id": 1, "arrival_s": 0, "departure_s": 0},
                    {
                        "node_id": 2,
                        "arrival_s": 100,
                        "departure_s": 120,
                        "station_track_id": "ap",
                        "station_route_id": "route",
                        "platform_ref": "way/10",
                    },
                    {"node_id": 3, "arrival_s": 300, "departure_s": 300},
                ],
            }
        ],
    }
    platforms = [
        {
            "type": "Feature",
            "properties": {"osm_way_id": 10},
            "geometry": {
                "type": "LineString",
                "coordinates": [[120.005, 30.001], [120.01, 30.001]],
            },
        }
    ]
    graph = {
        "edges": edges,
        "points": [
            {
                "properties": {"osm_node_id": node, "name": name, "kind": "station"},
                "geometry": {"type": "Point", "coordinates": points[node]},
            }
            for node, name in [(1, "A"), (2, "B"), (3, "C")]
        ],
    }
    return payload, graph, platforms


def test_desktop_detour_roundtrip_same_station_and_real_platform(tmp_path):
    payload, graph, platforms = detour_fixture()
    before = deepcopy(payload)
    plan, lines = compile_rail_plan(payload, graph["edges"], graph["points"], platforms)
    assert [leg["edge_id"] for leg in lines[0]["resolved_rail_path"]] == ["ap", "pc"]
    assert (
        payload == before
        and shared_document(payload)["routes"][0]["path"] == before["routes"][0]["path"]
    )
    assert plan.position("G1", 110)["state"] == "停站"
    repo, bindings = build_repository(graph, payload, tmp_path / "workspace.sqlite")
    station_id = bindings["stations"]["2"]
    row = station_timetable(repo, station_id, "2026-09-30")[0]
    assert (
        row["station_id"] == station_id
        and repo.platforms[row["platform_id"]].source_id == "way/10"
    )
    assert [
        r.edge_id for r in repo.corridors[bindings["corridors"]["c"]].edge_refs
    ] == [bindings["edges"][key] for key in ("ab", "bc")]


def test_construction_opening_uses_workspace_calendar_not_raw_source(tmp_path):
    from desktop.temporal_adapter import temporal_edges

    payload, graph, _ = detour_fixture()
    graph["edges"][0]["construction_status"] = "construction"
    graph["edges"][0]["construction"] = True
    store = WorkspaceObjects(tmp_path / "workspace.sqlite")
    store.put(
        "lifecycles",
        InfrastructureLifecycle(
            "life",
            construction_started="2020-01-01",
            opened="2026-09-30",
            source_aliases=("object:network_edge_id:ab",),
        ),
    )
    projected = temporal_edges(
        graph["edges"], store.collection("lifecycles"), "2026-09-30"
    )
    assert (
        projected[0]["construction_status"] == "operating"
        and graph["edges"][0]["construction_status"] == "construction"
    )
    assert (
        temporal_edges(graph["edges"], store.collection("lifecycles"), "2025-01-01")[0][
            "construction_status"
        ]
        == "construction"
    )


def test_six_module_shell_and_colored_stacked_details(tmp_path, monkeypatch):
    import launcher

    class MapWidget(QWidget):
        is_ready = False

        def __init__(self, server):
            super().__init__()
            self.bridge = launcher.Bridge(self)
            self.calls = []
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel("地图画布：此测试验证 Qt 工作台，地图渲染另测。"))

        def call(self, *args):
            self.calls.append(args)

    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher, "DATA", tmp_path / "data/processed/osm")
    monkeypatch.setattr(launcher, "MapView", MapWidget)
    app = QApplication.instance() or QApplication([])
    window = launcher.Desk()
    window.show()
    app.processEvents()
    assert window.side_pages.count() == 6 and window.detail_tabs.count() == 7
    for index in range(6):
        window.open_sidebar(index)
        assert (
            window.side_pages.currentIndex() == index
            and window.module_buttons[index].isChecked()
        )
    window.open_sidebar(1)
    app.processEvents()
    full_height = window.map_workspace.height()
    assert window.left.height() == full_height - 24
    window.open_rail_operations()
    app.processEvents()
    popup = window.editor_windows[1]
    assert popup.isWindow() and popup.isVisible() and not popup.isModal()
    assert window.rail_operations.window() is popup
    popup.resize(1400, 780)
    app.processEvents()
    assert window.rail_operations.width() >= popup.width() - 40
    assert window.map_workspace.height() == full_height
    original = popup.pos()
    popup.move(original.x() + 20, original.y() + 30)
    assert popup.pos() != original
    popup.close()
    app.processEvents()
    assert not popup.isVisible()
    window.open_sidebar(2)
    app.processEvents()
    page = window.side_pages.widget(2)
    assert page.horizontalScrollBar().maximum() == 0
    assert not page.horizontalScrollBar().isVisible()
    assert not window.workbench.vehicle_catalog.objects.horizontalScrollBar().isVisible()
    assert not window.workbench.vehicle_catalog.details.horizontalScrollBar().isVisible()
    window.display_feature(
        {
            "layer": "rail",
            "properties": {
                "name": "测试线路",
                "network_edge_id": "NE-TEST",
                "line_id": "IL-TEST",
            },
            "geometry": {"type": "LineString", "coordinates": [[120, 30], [121, 31]]},
        }
    )
    assert window.properties.columnCount() == 1
    # The delegate takes label/value colors from the active palette.
    assert window.properties.item(0, 0).data(Qt.ItemDataRole.UserRole) == "label"
    assert window.properties.item(1, 0).data(Qt.ItemDataRole.UserRole) != "label"
    window.history.show()
    app.processEvents()
    window.history.slider.setValue(2015)
    assert window.session_time.day == "2015-01-01"
    artifact = (
        Path(__file__).resolve().parents[2]
        / "docs/features/artifacts/workbench-shell.png"
    )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    assert window.grab().save(str(artifact))
    window.history.dialog.close()
    window.operations.timer.stop()
    window.rail_operations.timer.stop()
    window.close()


def test_vehicle_assignment_can_undo_and_redo_without_losing_plan_collections(tmp_path):
    from PySide6.QtWidgets import QMainWindow
    from desktop.operations_workbench import Workbench

    app = QApplication.instance() or QApplication([])
    line = {
        "id": "sh-1",
        "name": "1号线",
        "ref": "1",
        "relation_id": 10,
        "variants": [],
        "color": "#c82732",
        "path": {
            "coordinates": [[121, 31], [121.01, 31]],
            "length_m": 1000,
            "cumulative": [0, 1000],
        },
        "stations": [
            {"id": "a", "name": "A", "distance_m": 0},
            {"id": "b", "name": "B", "distance_m": 1000},
        ],
    }
    plan = Plan([line])
    plan.add_train("sh-1", "T1", 25200)
    desk = QMainWindow()
    desk.operations = OperationsEditor(plan, MapStub(), [line], tmp_path / "plan.json")
    sidebar = desk.operations.sidebar()
    desk.operations.selected_train = "T1"
    workbench = Workbench(desk, tmp_path / "workspace.sqlite", SessionTime(desk))
    workbench.store.put("vehicles", Vehicle("VEH-1", "一号车", mode="metro"))
    workbench.selected_vehicle = lambda: "VEH-1"
    before = plan.snapshot()
    workbench.assign_vehicle()
    assert (
        plan.train("T1")["extensions"]["railscope.org/vehicle"]["vehicle_id"] == "VEH-1"
    )
    desk.operations.undo()
    assert plan.snapshot() == before
    desk.operations.redo()
    assert plan.train("T1")["vehicle_id"] == "VEH-1"
    desk.operations.timer.stop()
    sidebar.close()
    desk.close()


def test_diagram_exports_svg_png_pdf_and_csv_preserves_operating_refs(tmp_path):
    from desktop.rail_tables import export_csv, merge_csv
    from desktop.station_diagram_layout import DiagramOptions
    from desktop.station_diagram_render import write_diagram
    from backend.tests.test_operations_analysis import fixture
    from railscope.services.analysis import corridor_diagram_svg
    from PySide6.QtGui import QImage

    app = QApplication.instance() or QApplication([])
    svg = corridor_diagram_svg(
        fixture(), "forward", "2026-09-30", linewidth=3, font_size=18
    )
    for suffix in ("svg", "png", "pdf"):
        path = tmp_path / f"diagram.{suffix}"
        write_diagram(path, svg, DiagramOptions())
        assert path.stat().st_size > 500
        if suffix == "pdf":
            assert path.read_bytes().startswith(b"%PDF")
        if suffix == "png":
            assert QImage(str(path)).size().width() == 1400
    payload, _, _ = detour_fixture()
    payload["trains"][0]["extensions"].update(
        {
            "railscope.org/vehicle": {"vehicle_id": "VEH-1"},
            "railscope.org/traffic": {"type": "passenger"},
        }
    )
    empty = deepcopy(payload)
    empty["trains"] = []
    result = merge_csv(export_csv(payload), empty)
    stop = result["trains"][0]["stops"][1]
    assert (
        stop["platform_ref"],
        stop["station_track_id"],
        stop["station_route_id"],
    ) == ("way/10", "ap", "route")
    assert (
        result["trains"][0]["extensions"]["railscope.org/vehicle"]["vehicle_id"]
        == "VEH-1"
    )


def test_vehicle_photo_replacement_keeps_prior_asset(tmp_path, monkeypatch):
    from PySide6.QtWidgets import (
        QMainWindow,
        QDialog,
        QDialogButtonBox,
        QPushButton,
        QFileDialog,
    )
    from PySide6.QtGui import QImage, QColor
    from types import SimpleNamespace
    from desktop.operations_workbench import Workbench

    app = QApplication.instance() or QApplication([])
    desk = QMainWindow()
    desk.rail_operations = SimpleNamespace(domain_repo=None)
    desk.operations = SimpleNamespace(plan=Plan([]))
    desk.shanghai_lines = []
    workbench = Workbench(desk, tmp_path / "workspace.sqlite", SessionTime(desk))
    workbench.store.put("vehicles", Vehicle("VEH-1", "车一"))
    source = tmp_path / "photo.png"
    image = QImage(40, 40, QImage.Format.Format_ARGB32)
    image.fill(QColor("red"))
    image.save(str(source))
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *args: (str(source), "PNG")
    )

    def edit(dialog):
        next(
            b for b in dialog.findChildren(QPushButton) if b.text() == "选择图片…"
        ).click()
        dialog.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Save
        ).click()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", edit)
    workbench.edit_vehicle("VEH-1")
    first = workbench.store.collection("vehicles")["VEH-1"].photo_asset
    original = (tmp_path / first).read_bytes()
    image.fill(QColor("blue"))
    image.save(str(source))
    workbench.edit_vehicle("VEH-1")
    second = workbench.store.collection("vehicles")["VEH-1"].photo_asset
    assert first != second and (tmp_path / first).read_bytes() == original
    assert (tmp_path / second).read_bytes() != original
    desk.close()


def test_platform_loads_only_its_referenced_extra_track(tmp_path):
    from dataclasses import asdict, replace
    import sqlite3
    from railscope.domain import StationTrack, DirectedEdgeRef

    payload, graph, _ = detour_fixture()
    identity = tmp_path / "workspace.sqlite"
    repo, bindings = build_repository(graph, payload, identity)
    stop = repo.stops_for(bindings["train_runs"]["G1"])[1]
    track = StationTrack(
        "TRK-extra",
        stop.station_id,
        "另一侧股道",
        edge_refs=(DirectedEdgeRef("NE-extra", 1, True, 0, 50),),
    )
    platform = repo.platforms[stop.platform_id]
    WorkspaceObjects(identity).put(
        "platforms",
        replace(platform, station_track_ids=(*platform.station_track_ids, track.id)),
    )
    edge = {
        "id": "NE-extra",
        "from_node": 2,
        "to_node": 5,
        "node_ids": [2, 5],
        "length_m": 50,
        "coordinates": [[120.01, 30], [120.012, 30.001]],
        "construction": False,
        "way_tags": {"railway": "rail", "service": "siding"},
    }
    source = tmp_path / "physical.sqlite"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE edges(id TEXT PRIMARY KEY,data TEXT)")
        db.execute("INSERT INTO edges VALUES(?,?)", (edge["id"], json.dumps(edge)))
    overrides = {
        "track:extra": {"station_track": asdict(track), "source_edge_ids": ["NE-extra"]}
    }
    result, _ = build_repository(graph, payload, identity, overrides, source)
    assert "TRK-extra" in result.station_tracks and "NE-extra" in result.edges
    assert len(graph["edges"]) == 4 and len(result.edges) == 5
    assert set(result.platforms[platform.id].station_track_ids) == {
        stop.station_track_id,
        track.id,
    }


def test_station_view_follows_plan_edits_and_shared_clock(tmp_path):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QMainWindow
    from desktop.operations_workbench import Workbench, StationRuntime
    from backend.tests.test_operations_analysis import fixture

    app = QApplication.instance() or QApplication([])

    class Editor(QObject):
        updated = Signal()
        station_labels_changed = Signal(object)

    desk = QMainWindow()
    desk.rail_operations = Editor(desk)
    desk.rail_operations.domain_repo = fixture()
    session = SessionTime(desk)
    session.set_day("2026-09-30")
    workbench = Workbench(desk, tmp_path / "workspace.sqlite", session)
    dialog = StationRuntime(workbench, desk.rail_operations.domain_repo, "b")
    assert dialog.table.rowCount() == 3
    latest = deepcopy(desk.rail_operations.domain_repo)
    latest.train_runs.pop("K2")
    latest.stops = [s for s in latest.stops if s.train_run_id != "K2"]
    desk.rail_operations.domain_repo = latest
    desk.rail_operations.updated.emit()
    assert dialog.table.rowCount() == 2
    session.set_seconds(80)
    assert dialog.slider.value() == 80
    dialog.close()
    desk.close()
