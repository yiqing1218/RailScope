import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
import pytest
from PySide6.QtWidgets import QApplication,QMainWindow
from desktop.tests.test_history_workbench import detour_fixture
from desktop.tests.test_operating_ui import MapStub
from desktop.rail_ui import RailEditor
from desktop.operations_workbench import Workbench
from desktop.session_time import SessionTime
from railscope.domain import Vehicle
from desktop.components import SquareSwitch
from desktop.corridor_ui import CorridorPanel
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton
from desktop.components import Switch


def setup_editor(tmp_path):
    app=QApplication.instance() or QApplication([])
    desk=QMainWindow()
    directory=tmp_path/'infrastructure';directory.mkdir()
    settings=tmp_path/'settings';settings.mkdir()
    editor=RailEditor(MapStub(),directory,tmp_path/'plan.json',settings/'catalog.json')
    sidebar=editor.sidebar()
    payload,graph,platforms=detour_fixture()
    from desktop.rail_store import build_index
    for point in graph["points"]:
        point["type"]="Feature"
    build_index(directory, [], graph["points"], platforms, graph["edges"])
    editor.graph=graph;editor.platforms=platforms
    editor.apply_payload(payload)
    desk.rail_operations=editor
    desk.operations=SimpleNamespace(plan=SimpleNamespace(trains=[]))
    workbench=Workbench(desk,settings/'workspace.sqlite',SessionTime(desk))
    return app,desk,editor,sidebar,workbench


def test_top_path_toggle_is_independent_but_directory_toggle_controls_all_its_trains(tmp_path):
    app,desk,editor,sidebar,workbench=setup_editor(tmp_path)
    try:
        second=deepcopy(editor.rail_payload)
        train=deepcopy(second['trains'][0]);train['id']='G2'
        for stop in train['stops']:
            stop['arrival_s']+=3600;stop['departure_s']+=3600
        second['trains'].append(train)
        editor.apply_payload(second)
        panel=CorridorPanel(editor)
        editor.clock=50;editor.set_enabled(True)
        editor.set_reference_visible(False)
        assert not editor.hidden_trains
        assert editor.current_vehicle_features
        panel.refresh()
        root=panel.tree.topLevelItem(0)
        assert panel.tree.columnCount()==1 and root.checkState(0)==Qt.CheckState.Checked
        root.setCheckState(0,Qt.CheckState.Unchecked);app.processEvents()
        assert editor.hidden_trains=={'G1','G2'} and not editor.visible_corridors
        assert not editor.current_vehicle_features
        panel.tree.topLevelItem(0).setCheckState(0,Qt.CheckState.Checked);app.processEvents()
        assert editor.hidden_trains==set() and editor.visible_corridors=={'c'}
        assert editor.current_vehicle_features[0]['properties']['trip_id']=='G1'
        editor.set_trains_visible({'G2'},False);panel.refresh()
        assert panel.tree.topLevelItem(0).checkState(0)==Qt.CheckState.PartiallyChecked
        panel.tree.topLevelItem(0).setCheckState(0,Qt.CheckState.Checked);app.processEvents()
        assert not editor.hidden_trains
        editor.play()
        clock=editor.clock
        editor.apply_payload(editor.document())
        assert editor.playing and editor.clock==clock
        editor.set_reference_visible(False)
        assert not editor.hidden_trains and not editor.visible_corridors
        assert editor.current_vehicle_features[0]['properties']['trip_id']=='G1'
        editor.show_corridor('c','G1')
        assert not editor.visible_corridors  # Selecting a train cannot re-enable its hidden path.
        panel.train_master.click();app.processEvents()
        assert editor.hidden_trains=={'G1','G2'} and not editor.current_vehicle_features
        editor.set_reference_visible(True)
        assert editor.hidden_trains=={'G1','G2'} and editor.visible_corridors=={'c'}
        panel.close()
    finally:
        editor.timer.stop();sidebar.close();editor.close();desk.close()


def test_running_sidebar_has_left_checkboxes_full_names_and_master_toggle(tmp_path):
    app,desk,editor,sidebar,workbench=setup_editor(tmp_path)
    try:
        assert not sidebar.findChildren(Switch)
        assert isinstance(editor.enabled_switch,SquareSwitch)
        assert isinstance(editor.vehicle_switch,SquareSwitch)
        panel=CorridorPanel(editor)
        editor.directory_layout.addWidget(panel)
        sidebar.resize(310,1000);sidebar.show();app.processEvents()
        assert editor.time_input.width()>=80 and editor.beijing_switch.isVisible()
        assert panel.tree.columnCount()==1
        panel.master.click();app.processEvents()
        assert editor.visible_corridors=={'c'} and not editor.hidden_trains
        root=panel.tree.topLevelItem(0)
        root.setExpanded(True)
        root.child(0).setCheckState(0,Qt.CheckState.Unchecked);app.processEvents()
        assert editor.hidden_trains=={'G1'}
        assert panel.tree.topLevelItem(0).checkState(0)==Qt.CheckState.Unchecked
        assert not panel.master.isMixed()
        panel.train_master.click();app.processEvents()
        assert editor.hidden_trains==set() and editor.visible_corridors=={'c'}
        panel.master.click();app.processEvents()
        assert editor.hidden_trains==set() and not editor.visible_corridors
        payload=deepcopy(editor.rail_payload)
        name='北京南—京沪高速铁路—上海虹桥—沪昆高速铁路—杭州东完整运行通道'
        payload['routes'][0]['name']=name
        editor.apply_payload(payload);app.processEvents();panel.refresh();app.processEvents()
        root=panel.tree.topLevelItem(0)
        assert name in root.text(0) and name in root.toolTip(0)
        assert panel.tree.visualItemRect(root).height()>40
        assert panel.tree.horizontalScrollBar().maximum()==0
        captions={button.text() for button in sidebar.findChildren(QPushButton)}
        assert {'保存计划','导入计划','导出计划','定位线路'}<=captions
        panel.close()
    finally:
        editor.timer.stop();sidebar.close();editor.close();desk.close()


def test_vehicle_binding_saves_reloads_rejects_overlap_and_can_unassign(tmp_path):
    app,desk,editor,sidebar,workbench=setup_editor(tmp_path)
    try:
        workbench.store.put('vehicles',Vehicle('VEH-A','一号车',model='CR400',code='001'))
        workbench.refresh_vehicles()
        workbench.add_vehicle_controls(editor)
        editor.selected_train='G1'
        workbench.bind_vehicle(editor,'VEH-A')
        _,combo,_=workbench.vehicle_controls[0]
        assert combo.currentData()=='VEH-A'
        combo.setCurrentIndex(0)
        editor.updated.emit()
        assert combo.currentData() is None  # Simulation ticks preserve pending edits.
        run=next(iter(editor.domain_repo.train_runs.values()))
        assert run.vehicle_id=='VEH-A'
        assert editor.document()['trains'][0]['extensions']['railscope.org/vehicle']['vehicle_id']=='VEH-A'
        editor.apply_payload(editor.document())
        assert next(iter(editor.domain_repo.train_runs.values())).vehicle_id=='VEH-A'
        editor.clock=50;editor.set_enabled(True);editor.push_positions()
        props=editor.current_vehicle_features[0]['properties']
        assert (props['vehicle_id'],props['trip_id'],props['vehicle_name'])==('VEH-A','G1','一号车')
        before=editor.document()
        overlap=deepcopy(before)
        train=deepcopy(overlap['trains'][0]);train['id']='G2'
        overlap['trains'].append(train)
        with pytest.raises(ValueError,match='overlap|重叠'):
            editor.accept_batch(overlap)
        assert editor.document()==before
        workbench.store.put('vehicles',Vehicle('VEH-M','地铁车',mode='metro'))
        with pytest.raises(ValueError,match='制式'):
            workbench.bind_vehicle(editor,'VEH-M')
        workbench.bind_vehicle(editor,None)
        assert next(iter(editor.domain_repo.train_runs.values())).vehicle_id is None
        editor.undo()
        assert next(iter(editor.domain_repo.train_runs.values())).vehicle_id=='VEH-A'
    finally:
        editor.timer.stop();sidebar.close();editor.close();desk.close()


def test_master_controls_filtered_out_corridors_and_trains(tmp_path):
    app,desk,editor,sidebar,workbench=setup_editor(tmp_path)
    try:
        payload=deepcopy(editor.rail_payload)
        route=deepcopy(payload['routes'][0]);route['id']='c2';route['name']='另一条完整通道'
        payload['routes'].append(route)
        train=deepcopy(payload['trains'][0]);train['id']='G2';train['route_id']='c2'
        payload['trains'].append(train)
        editor.apply_payload(payload)
        panel=CorridorPanel(editor)
        panel.search.setText('G1')
        assert panel.tree.topLevelItemCount()==1
        panel.master.click();app.processEvents()
        assert editor.visible_corridors=={'c','c2'} and not editor.hidden_trains
        panel.master.click();app.processEvents()
        assert not editor.visible_corridors and not editor.hidden_trains
        # A directory group affects only its own children, even while filtered.
        panel.tree.topLevelItem(0).setCheckState(0,Qt.CheckState.Unchecked);app.processEvents()
        assert editor.hidden_trains=={'G1'} and not editor.visible_corridors
        panel.tree.topLevelItem(0).setCheckState(0,Qt.CheckState.Checked);app.processEvents()
        assert not editor.hidden_trains and editor.visible_corridors=={'c'}
        panel.master.click();app.processEvents()  # Mixed path state turns all on.
        panel.master.click();app.processEvents()  # Then paths only off.
        panel.train_master.click();app.processEvents()
        assert not editor.visible_corridors and editor.hidden_trains=={'G1','G2'}
        panel.close()
    finally:
        editor.timer.stop();sidebar.close();editor.close();desk.close()


def test_switching_run_panel_preserves_both_running_layers():
    from launcher import Desk
    app=QApplication.instance() or QApplication([])
    map_view=MapStub()
    def unexpected_pause():
        raise AssertionError('切换界面不能暂停运行')
    metro=SimpleNamespace(pause=unexpected_pause,vehicle_switch=SquareSwitch(True))
    rail=SimpleNamespace(pause=unexpected_pause,vehicle_switch=SquareSwitch(True),
        route_switch=SquareSwitch(False),visible_corridors={'c'})
    host=SimpleNamespace(operations=metro,rail_operations=rail,map=map_view,
        run_pages=SimpleNamespace(setCurrentIndex=lambda index:None))
    Desk.change_run_mode(host,1)
    assert ('setVisibility','vehicles',True) in map_view.calls
    assert ('setVisibility','railVehicles',True) in map_view.calls
    assert ('setVisibility','railPlan',True) in map_view.calls


def test_beijing_replays_reference_schedule_without_hiding_vehicles_on_date_mismatch(tmp_path,monkeypatch):
    from datetime import datetime,timezone,timedelta
    import desktop.operating_ui as module
    app,desk,editor,sidebar,workbench=setup_editor(tmp_path)
    session=SessionTime();editor.bind_session(session)
    source_date=editor.plan.service_date
    try:
        now=datetime(2026,10,8,0,0,50,tzinfo=timezone(timedelta(hours=8)))
        monkeypatch.setattr(module,'beijing_now',lambda:now)
        editor.beijing_switch.click()
        assert editor.playing and editor.enabled and session.day=='2026-10-08'
        assert editor.current_vehicle_features[0]['properties']['trip_id']=='G1'
        assert editor.document()['service_date']==source_date
        now=datetime(2026,10,9,0,0,1,tzinfo=now.tzinfo);editor.tick()
        assert session.day=='2026-10-09' and editor.current_vehicle_features
        assert '1 列车在运行' in editor.count_label.text()
        editor.beijing_switch.click()
        assert editor.playing and editor.current_vehicle_features
        editor.beijing_switch.click();editor.pause()
        assert not editor.playing and editor.current_vehicle_features
    finally:
        editor.timer.stop();session._timer.stop();sidebar.close();editor.close();desk.close()


def test_running_rail_layer_visibility_is_replayed_after_renderer_restart(tmp_path):
    app,desk,editor,sidebar,workbench=setup_editor(tmp_path)
    try:
        editor.clock=50;editor.play()
        view=editor.map.view;view.calls.clear()
        editor.map_ready()
        assert ('setVisibility','railVehicles',True) in view.calls
        assert editor.current_vehicle_features
        editor.set_enabled(False);view.calls.clear();editor.map_ready()
        assert ('setVisibility','railVehicles',False) in view.calls
        assert editor.playing and not editor.current_vehicle_features
    finally:
        editor.timer.stop();sidebar.close();editor.close();desk.close()


def test_desk_restores_live_player_visibility_after_stale_initial_flags(monkeypatch):
    from unittest.mock import MagicMock
    import launcher
    app=QApplication.instance() or QApplication([])
    host=MagicMock()
    host.config={'appearance':{},'fonts':{},'trainLabels':{}}
    host.flags={'vehicles':False,'railVehicles':False}
    host.base_switches={};host.overlay_actions={}
    host.base_combo.currentIndex.return_value=0;host.run_mode.currentIndex.return_value=1
    host.operations.vehicle_switch=SquareSwitch(True)
    host.rail_operations.vehicle_switch=SquareSwitch(True)
    host.rail_operations.visible_corridors={'c'}
    host.university_catalog.visible=set();host.airport_catalog.visible=set()
    host.change_run_mode.side_effect=lambda index:launcher.Desk.change_run_mode(host,index)
    monkeypatch.setattr(launcher,'web_theme',lambda value:value)
    launcher.Desk.restore_map_state(host)
    calls=[call.args for call in host.map.call.call_args_list]
    for key in ('vehicles','railVehicles'):
        assert [call[2] for call in calls if call[:2]==('setVisibility',key)][-1] is True
