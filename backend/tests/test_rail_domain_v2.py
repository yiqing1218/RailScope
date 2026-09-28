from dataclasses import asdict, replace
import importlib.util
import io
import json
from pathlib import Path
import sqlite3

import pytest

from railscope.domain import (
    Corridor, InfrastructureLine, NetworkEdge, NetworkNode, OperationalPoint,
    ResolvedCorridor, RouteIntent, RouteIntentStep, Station, StationTrack,
    StationTrackEdge, StationZone, Yard,
)
from railscope.integrity import delete_edge, path_refs, references, validate_repository
from railscope.rail_semantics import (
    Classification, classify_facility_context, classify_line_role,
    classify_operational_status, classify_railway_class, classify_track_role,
    edge_semantics, legacy_semantics,
)
from railscope.repository import RailRepository
from railscope.workspace import EditSession, SQLiteWorkspace, decode


def station_repo():
    repo = RailRepository()
    repo.lines['IL-A'] = InfrastructureLine('IL-A', '上海南线', 'rail', railway_class='conventional', line_role='main_line')
    for key, x in [('N-a',120),('N-b',120.1),('N-c',120.2)]:
        repo.nodes[key] = NetworkNode(key,x,30)
    repo.stations['ST-a'] = Station('ST-a','新龙华',120,30,'N-a')
    repo.stations['ST-b'] = Station('ST-b','异地同名站',120.2,30,'N-c')
    for key,a,b in [('NE-a','N-a','N-b'),('NE-b','N-b','N-c')]:
        repo.edges[key] = NetworkEdge(key,a,b,((repo.nodes[a].lon,30),(repo.nodes[b].lon,30)),100,
            infrastructure_line_id='IL-A', railway_class='conventional',track_role='main_track',facility_id='ST-a')
    repo.yards['Y-a'] = Yard('Y-a','ST-a','到发场','arrival_departure')
    repo.station_zones['Z-a'] = StationZone('Z-a','ST-a','西咽喉',yard_id='Y-a')
    repo.operational_points['OP-a'] = OperationalPoint('OP-a','新龙华站','station','ST-a',('N-a',))
    repo.operational_points['OP-b'] = OperationalPoint('OP-b','另一边界','junction_post',node_ids=('N-c',))
    return repo


def test_station_mainline_remains_main_track_and_has_independent_facility_and_zone():
    raw = {'way_tags': {'railway':'rail','highspeed':'yes','usage':'main'},
           'facility_id':'ST-a','yard_id':'Y-a','zone_id':'Z-a','snapshot_id':'snap-1'}
    result = edge_semantics(raw)
    assert result['railway_class'] == 'high_speed'
    assert result['line_role'] == 'main_line'
    assert result['track_role'] == 'main_track'
    assert result['facility_id'] == 'ST-a' and result['zone_id'] == 'Z-a'
    assert result['provenance']['track_role']['snapshot_id'] == 'snap-1'
    assert result['provenance']['track_role']['verification_status'] == 'osm_explicit'
    assert raw['way_tags'] == {'railway':'rail','highspeed':'yes','usage':'main'}
    json.dumps(result)


@pytest.mark.parametrize('service,expected',[('spur','spur_track'),('crossover','crossover'),('yard','unknown'),('siding','unknown')])
def test_osm_service_is_not_an_official_line_or_station_function(service, expected):
    result = edge_semantics({'way_tags':{'railway':'rail','service':service,'name':'高铁到发场联络线'}})
    assert result['track_role'] == expected
    assert result['line_role'] == 'unknown'
    assert result['railway_class'] == 'unknown'
    if expected == 'unknown':
        assert result['provenance']['track_role']['verification_status'] == 'unverified'


def test_all_classifier_results_have_immutable_provenance_contract():
    for classifier in (classify_railway_class,classify_line_role,classify_track_role,
                       classify_operational_status,classify_facility_context):
        value = classifier({'railway':'rail','usage':'main'},source='OSM',snapshot_id='S1')
        assert isinstance(value,Classification)
        assert value.source == 'OSM' and value.snapshot_id == 'S1'
        assert set(asdict(value)) == {'value','evidence','verification_status','confidence','source','snapshot_id'}
        with pytest.raises(AttributeError):
            value.value = 'guessed'


def test_lifecycle_tags_take_priority_over_old_boolean_and_unknown_is_not_operating():
    assert edge_semantics({'construction':False})['construction_status']=='operating'
    assert edge_semantics({'construction':True})['construction_status']=='construction'
    assert edge_semantics({'construction':False,'way_tags':{'railway':'construction'}})['construction_status']=='construction'
    assert edge_semantics({})['construction_status']=='unknown'
    assert edge_semantics({'way_tags':{'railway':'rail','disused':'yes'}})['construction_status']=='disused'


def test_source_tags_override_legacy_name_heuristics_and_explicit_override_keeps_its_audit():
    assert legacy_semantics('支线 / 岔道')['line_role']=='unknown'
    assert edge_semantics({'track_type':'高速铁路线','way_tags':{'railway':'rail'}})['railway_class']=='unknown'
    audit={'value':'arrival_departure_track','source':'workspace_override','snapshot_id':'S1',
           'evidence':'官方图','verification_status':'user_verified','confidence':1}
    result=edge_semantics({'way_tags':{'service':'yard'},'track_role':'arrival_departure_track','provenance':{'track_role':audit}})
    assert result['track_role']=='arrival_departure_track'
    assert result['provenance']['track_role']==audit


@pytest.mark.parametrize('invalid',[{'track_role':'throat'},{'railway_class':['high_speed']},
    {'facility_id':123},{'confidence':float('nan')},{'confidence':1.1}])
def test_invalid_explicit_semantics_never_become_silent_unknown_or_verified_facts(invalid):
    with pytest.raises(ValueError,match='Invalid'):
        edge_semantics(invalid)


def test_saved_aggregate_provenance_is_retained_without_claiming_osm_source():
    membership={'source':'workspace_membership','snapshot_id':'snap-1',
                'evidence':'User selected memberships','verification_status':'derived','confidence':None}
    result=edge_semantics({'railway_class':'conventional','verification_status':'derived',
                           'provenance':{'membership':membership}})
    assert result['provenance']['membership']==membership
    assert result['provenance']['railway_class']['source']=='saved_canonical'
    assert result['provenance']['railway_class']['verification_status']=='derived'
    with pytest.raises(ValueError,match='Conflicting'):
        edge_semantics({'track_role':'main_track','provenance':{'track_role':{'value':'crossover'}}})


def test_multiedge_station_track_membership_and_integrity_guards():
    repo=station_repo()
    repo.station_tracks['T1']=StationTrack('T1','ST-a','人工命名',track_role='arrival_departure_track',yard_id='Y-a',zone_id='Z-a')
    repo.station_track_edges=[StationTrackEdge('T1','NE-a',1),StationTrackEdge('T1','NE-b',2)]
    validate_repository(repo)
    assert references(repo,'edge','NE-b')['station_tracks']==['T1']
    with pytest.raises(ValueError,match='引用'):
        delete_edge(repo,'NE-b')
    repo.station_track_edges[1]=replace(repo.station_track_edges[1],direction='reverse')
    with pytest.raises(ValueError,match='不连续'):
        validate_repository(repo)


def test_membership_disagreement_with_legacy_refs_cannot_be_saved():
    repo=station_repo()
    refs=path_refs(repo,[('NE-a',True)])
    repo.station_tracks['T1']=StationTrack('T1','ST-a','旧股道',edge_refs=refs)
    repo.station_track_edges=[StationTrackEdge('T1','NE-b',1)]
    with pytest.raises(ValueError,match='disagrees'):
        validate_repository(repo)


@pytest.mark.parametrize('field,value,match',[('yard_id','Y-missing','yard_id missing'),('zone_id','Z-missing','zone_id missing'),('facility_id','ST-missing','facility missing')])
def test_dangling_facility_references_rejected(field,value,match):
    repo=station_repo()
    repo.edges['NE-a']=replace(repo.edges['NE-a'],**{field:value})
    with pytest.raises(ValueError,match=match):
        validate_repository(repo)


def test_same_name_does_not_merge_facilities_or_allow_cross_station_yard():
    repo=station_repo()
    repo.yards['Y-b']=Yard('Y-b','ST-b','到发场')
    repo.edges['NE-a']=replace(repo.edges['NE-a'],yard_id='Y-b')
    with pytest.raises(ValueError,match='another station'):
        validate_repository(repo)
    assert len(repo.yards)==2


def test_route_intent_and_resolved_corridor_roundtrip_preserve_physical_selection(tmp_path):
    repo=station_repo()
    repo.route_intents['RI-1']=RouteIntent('RI-1','已保存意图',(
        RouteIntentStep(1,'operational_point','OP-a'),RouteIntentStep(2,'infrastructure_line','IL-A','forward'),
        RouteIntentStep(3,'operational_point','OP-b')))
    refs=path_refs(repo,[('NE-a',True),('NE-b',True)])
    repo.corridors['C1']=Corridor('C1','通道',refs,'N-a','N-c',route_intent_id='RI-1')
    repo.station_tracks['T1']=StationTrack('T1','ST-a','Ⅲ道',track_number='Ⅲ',verification_status='user_verified',
        edge_refs=refs,track_role='arrival_departure_track',yard_id='Y-a')
    store=SQLiteWorkspace(tmp_path/'workspace.sqlite')
    store.seed(repo)
    loaded,revision=store.load()
    assert isinstance(loaded.corridors['C1'],ResolvedCorridor)
    assert loaded.corridors['C1'].edge_refs==refs
    assert loaded.route_intents==repo.route_intents
    assert loaded.station_tracks['T1'].track_number=='Ⅲ'
    assert len(loaded.station_track_edges)==2
    store.save(loaded,revision)
    assert store.load()[0]==loaded
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT value FROM workspace_meta WHERE key='schema_version'").fetchone()[0]==2
    assert references(loaded,'operational_point','OP-a')['corridors']==['C1']


def test_unknown_old_number_is_recoverable_and_manual_number_is_preserved():
    old={'id':'T1','station_id':'ST-a','name':'人工名称','track_number':'98','role':'yard'}
    migrated=decode(StationTrack,old)
    assert migrated.name=='人工名称' and migrated.track_number is None
    assert migrated.legacy_metadata['track_number']=='98'
    assert migrated.track_role=='unknown'
    assert decode(StationTrack,{**old,'verification_status':'user_named','track_number':'Ⅲ'}).track_number=='Ⅲ'
    assert decode(StationTrack,{**old,'verification_status':'user_verified','track_number':'NE-23871'}).track_number is None
    fresh=StationTrack('T2','ST-a','已输入号码',track_number='1')
    assert decode(StationTrack,asdict(fresh))==fresh


def test_future_or_unknown_workspace_data_is_never_silently_dropped(tmp_path):
    store=SQLiteWorkspace(tmp_path/'workspace.sqlite')
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO workspace_source VALUES('future_object','f1','{}')")
    with pytest.raises(ValueError,match='未知工作区对象'):
        store.load()
    with pytest.raises(ValueError,match='未知工作区对象'):
        store.save(RailRepository(),0)
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM workspace_source').fetchone()[0]==1
        db.execute("UPDATE workspace_meta SET value=999 WHERE key='schema_version'")
    with pytest.raises(ValueError,match='高于'):
        SQLiteWorkspace(store.path)


def test_station_merge_rebinds_all_new_facility_owners(tmp_path):
    repo=station_repo()
    store=SQLiteWorkspace(tmp_path/'workspace.sqlite')
    store.seed(repo)
    session=EditSession(store)
    session.merge_stations('ST-b','ST-a')
    validate_repository(session.repo)
    assert session.repo.yards['Y-a'].station_id=='ST-b'
    assert session.repo.station_zones['Z-a'].station_id=='ST-b'
    assert session.repo.operational_points['OP-a'].station_id=='ST-b'
    assert session.repo.edges['NE-a'].facility_id=='ST-b'


def test_postgres_migration_compiles_additive_schema_without_rewriting_paths(monkeypatch):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path=Path(__file__).parents[1]/'alembic'/'versions'/'0005_rail_domain_v2.py'
    spec=importlib.util.spec_from_file_location('v2_migration',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    buffer=io.StringIO()
    context=MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':buffer})
    monkeypatch.setattr(module,'op',Operations(context))
    module.upgrade()
    sql=buffer.getvalue()
    assert 'CREATE TABLE station_track_edge' in sql and 'CREATE TABLE route_intent_step' in sql
    assert 'DROP TABLE' not in sql and 'UPDATE corridor_edge' not in sql and 'UPDATE stop_time' not in sql
    assert 'FOREIGN KEY(edge_id) REFERENCES network_edge' in sql
    with pytest.raises(RuntimeError,match='export'):
        module.downgrade()
