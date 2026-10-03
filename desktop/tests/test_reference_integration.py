from dataclasses import replace
from types import SimpleNamespace

from PySide6.QtWidgets import QWidget, QTabWidget
from railscope.domain import Vehicle
from railscope.workspace import WorkspaceObjects
from desktop.china_emu import ReferenceStore
from desktop.tests.test_china_emu import profile
from desktop.tests.test_station_engineering import fixture
from desktop.reference_integration import (
    vehicle_from_profile, reference_lifecycle, station_events, yard_bindings,
    integrate_station_yards, apply_yard_binding,
)


def station_profile(scopes=None):
    return profile('station','测试站',{'locality':'上海市'},scopes=scopes or [{
        'name':'国铁站场','source_notes':'2010-07-01','lines':['测试高速铁路'],
        'yards':[{'name':'高速场 [1~2站台]','lines':'测试高速铁路'}],
        'attributes':{}, 'platform_numbers':['1','2']}])


def test_models_visible_in_vehicle_page_without_any_plan_or_real_units(qtbot,tmp_path,monkeypatch):
    from desktop.operations_workbench import Workbench
    import desktop.china_emu_ui as ui
    models = [profile(name='CR400AF',attributes={'length_m':'209 m'})]
    monkeypatch.setattr(ui,'load_store',lambda:ReferenceStore(models))
    desk = QWidget()
    qtbot.addWidget(desk)
    desk.rail_operations = SimpleNamespace(domain_repo=None)
    workbench = Workbench(desk,tmp_path/'workspace.sqlite',None)
    page = workbench.page(2)
    qtbot.addWidget(page)
    tabs = page.findChild(QTabWidget)
    assert tabs.currentIndex()==0 and tabs.tabText(0)=='车型资料（1）'
    assert tabs.widget(0).objects.item(0,0).text()=='CR400AF'
    assert any(tabs.widget(0).details.item(row,1).text()=='209 m' for row in range(tabs.widget(0).details.rowCount()))
    assert workbench.store.collection('vehicles')=={}
    models.append({**profile(name='CR400BF'),'id':'reference/second'})
    workbench.refresh_vehicles()
    assert tabs.tabText(0)=='车型资料（2）' and tabs.widget(0).objects.rowCount()==2


def test_model_template_fills_parameters_and_preserves_user_values():
    model = profile(name='CR400AF',attributes={'length_m':'209 m','max_speed_kmh':'350 km/h'})
    vehicle = Vehicle('VEH-1','车组 001',model='手工车型',parameters={'length_m':'210 m'})
    result = vehicle_from_profile(vehicle,model)
    assert result.id==vehicle.id and result.name==vehicle.name and result.model==vehicle.model
    assert result.parameters['length_m']=='210 m'
    assert result.parameters['max_speed_kmh']=='350 km/h'
    assert result.parameters['reference_profile']['snapshot_id']==model['snapshot_id']
    assert vehicle.parameters=={'length_m':'210 m'}


def test_history_keeps_all_scoped_events_and_partial_date_precision(tmp_path):
    p = station_profile([{'name':'主站场','source_notes':'2010-07-01','lines':['甲线']},
                         {'name':'扩建场','source_notes':'2024-12-27','lines':['乙线']}])
    value = reference_lifecycle(p,'INF-1',{'station:node/1'},'测试站')
    assert value.opened=='2010-07-01'
    assert [e['date'] for e in value.provenance['events']]==['2010-07-01','2024-12-27']
    partial = station_profile([{'name':'主场','source_notes':'2010-07'}])
    assert reference_lifecycle(partial,'INF-2',(),'测试站').opened is None
    assert station_events(partial)[0]['date_precision']=='month'
    store = WorkspaceObjects(tmp_path/'workspace.sqlite')
    assert store.seed_reference('lifecycles',value)
    store.put('lifecycles',replace(value,opened='2011-01-01',source='manual'))
    store.seed_reference('lifecycles',replace(value,opened='2009-01-01'))
    assert store.collection('lifecycles')['INF-1'].opened=='2011-01-01'


def test_selected_station_reference_enters_history_and_map(qtbot,tmp_path):
    from desktop.history_ui import HistoryController
    from desktop.session_time import SessionTime
    class Map:
        calls = []
        def call(self,*args):
            self.calls.append(args)
    desk = SimpleNamespace(map=Map())
    history = HistoryController(desk,WorkspaceObjects(tmp_path/'workspace.sqlite'),SessionTime())
    props = {'infrastructure_id':'node/1', 'name':'测试站', 'station_key':'station:node/1',
             'external_reference':station_profile()}
    value = history.effective_for_feature(props,'rail')
    assert value.opened=='2010-07-01' and value.id.startswith('INF-')
    assert desk.map.calls[-1][0]=='setInfrastructureHistory'
    assert history.store.collection('lifecycles')[value.id].provenance['events']


def numbered_repo():
    repo,context,_,_=fixture()
    for key,number in [('NE-core','1'),('NE-platform','2')]:
        repo.station_tracks[key]=replace(repo.station_tracks[key],track_number=number,yard_id=None,provenance={})
    repo.station_tracks.pop('NE-c2')
    repo.station_tracks = {t.id:t for t in repo.station_tracks.values()}
    platform={'properties':{'boundary_kind':'platform','infrastructure_id':'way/77',
                            'way_tags':{'railway':'platform','ref':'1;2'}},
              'geometry':{'type':'Polygon','coordinates':[[[119.998,30.00002],[120.002,30.00002],
                           [120.002,30.00008],[119.998,30.00008],[119.998,30.00002]]]}}
    return repo,[platform]


def test_face_to_real_track_needs_two_number_sources_and_adjacency():
    repo,context=numbered_repo()
    p=station_profile()
    proposals=yard_bindings(repo,p)
    assert all(b['status']=='reference_number_match' for b in proposals)
    integrate_station_yards(repo,p)
    assert all(t.yard_id is None for t in repo.station_tracks.values())
    integrate_station_yards(repo,p,context)
    for t in repo.station_tracks.values():
        assert t.yard_id in repo.yards
        assert t.provenance['yard']['track_id']==t.id
        assert t.platform_number==t.track_number
        assert t.provenance['yard']['platform_source_ids']==['way/77']
        assert repo.yards[t.yard_id].verification_status=='external_reference_unverified'


def test_ambiguous_scopes_and_manual_yard_are_not_overwritten():
    repo,context=numbered_repo()
    p=station_profile()
    p['scopes']=[*p['scopes'],{**p['scopes'][0],'name':'另一时期'}]
    assert all(b['status']=='ambiguous_source_scope' for b in yard_bindings(repo,p,context))
    integrate_station_yards(repo,p,context)
    assert all(t.yard_id is None for t in repo.station_tracks.values())
    p=station_profile()
    b=yard_bindings(repo,p)[0]
    apply_yard_binding(repo,p,b,b['track_id'],confirmed=True)
    before=repo.station_tracks[b['track_id']]
    integrate_station_yards(repo,p,context)
    assert repo.station_tracks[b['track_id']]==before


def test_remote_platform_with_same_number_does_not_confirm_membership():
    repo,context=numbered_repo()
    context[0]['geometry']['coordinates']=[[[121,31],[121.001,31],[121.001,31.001],[121,31.001],[121,31]]]
    integrate_station_yards(repo,station_profile(),context)
    assert all(t.yard_id is None for t in repo.station_tracks.values())
