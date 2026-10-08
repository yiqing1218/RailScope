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


def test_corridor_checkbox_controls_all_trains_and_map_positions(tmp_path):
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
        editor.set_corridor_visible('c',False)
        assert editor.hidden_trains=={'G1','G2'}
        assert not editor.current_vehicle_features
        panel.refresh()
        root=panel.tree.topLevelItem(0)
        control=panel.tree.itemWidget(root,1)
        assert isinstance(control,SquareSwitch) and not control.isChecked()
        control.click();app.processEvents()
        assert editor.hidden_trains==set() and editor.visible_corridors=={'c'}
        assert editor.current_vehicle_features[0]['properties']['trip_id']=='G1'
        editor.set_reference_visible(False)
        assert editor.hidden_trains=={'G1','G2'} and not editor.visible_corridors
        editor.set_reference_visible(True)
        assert not editor.hidden_trains and editor.visible_corridors=={'c'}
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
