"""Depot/station parity without moving passing main lines or whole mixed groups."""
import json
import sqlite3
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt

from desktop.rail_facility_ownership import facility_track_owners
from desktop.rail_station_catalog_model import sync_station_catalog, StationCatalogModel
from desktop.rail_catalog_ui import RailCatalog
from desktop.rail_catalog_model import sync_catalog_directory
from desktop.rail_catalog_index import RailCatalogIndex, build_index
from desktop.rail_store import viewport


def dataset(tmp_path):
    stations=[{'id':'way/1','name':'甲车辆段','kind':'depot','station_type':'车辆段','province':'甲省','city':'甲市'},
              {'id':'way/2','name':'乙动车运用所','kind':'yard','station_type':'动车所/客整所','province':'甲省','city':'甲市'},
              {'id':'node/3','name':'附近客运站','station_type':'客运站','province':'甲省','city':'甲市'}]
    areas=[]
    for key,w,e in [('way/1',120,120.004),('way/2',120.006,120.01)]:
        areas.append({'properties':{'infrastructure_id':key,'associated_station_ids':[key],
                      'boundary_kind':'station_outline'},'geometry':{'type':'Polygon','coordinates':[
                     [[w,30],[e,30],[e,30.004],[w,30.004],[w,30]]]}})
    tracks=[]
    for number,key,w,e,role in [(1,'NE-a',120.001,120.003,'depot_track'),
                              (2,'NE-b',120.007,120.009,'stabling_track'),
                              (3,'NE-main',120.001,120.003,'main_track')]:
        tracks.append({'properties':{'network_edge_id':key,'catalog_group_id':'ST-mixed' if number<3 else 'RL-main',
                        'line_name':key,'track_role':role,'track_type':'段管线','osm_way_id':number,
                        'way_tags':{'railway':'rail','service':'main' if number==3 else 'yard'}},
                       'geometry':{'type':'LineString','coordinates':[[w,30.002],[e,30.002]]}})
    with sqlite3.connect(tmp_path/'rail.sqlite') as db:
        db.executescript('CREATE TABLE features(id INTEGER PRIMARY KEY,kind TEXT,service TEXT,data TEXT); '
                        'CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy); '
                        'CREATE TABLE rail_feature_groups(feature_id INTEGER,group_id TEXT);')
        for i,feature in enumerate(tracks+areas,1):
            kind='rail' if i<=3 else 'railStationAreas'
            db.execute('INSERT INTO features VALUES(?,?,?,?)',(i,kind,'main' if i==3 else 'yard',json.dumps(feature)))
            if i<=3:
                p=feature['geometry']['coordinates'];db.execute('INSERT INTO bounds VALUES(?,?,?,?,?)',(i,p[0][0],p[1][0],30.002,30.002))
                db.execute('INSERT INTO rail_feature_groups VALUES(?,?)',(i,feature['properties']['catalog_group_id']))
    (tmp_path/'rail_lines.sqlite').touch()
    records={'ST-mixed':{'id':'ST-mixed','catalog_group_id':'ST-mixed','name':'附近客运站站场股道',
                        'station_name':'附近客运站','provinces':['甲省'],'track_role':'shunting_track','facility_only':True},
             'RL-main':{'id':'RL-main','catalog_group_id':'RL-main','name':'干线','track_role':'main_track'}}
    catalog=RailCatalogIndex(build_index(tmp_path,catalog=records))
    sync_catalog_directory(catalog,{},lambda key,record:(record,('普速铁路',),record['name']))
    return stations,tracks,catalog


def test_real_area_assigns_each_track_to_its_own_facility_and_keeps_main_line(tmp_path,monkeypatch):
    import desktop.rail_station_catalog_model as module
    stations,tracks,catalog=dataset(tmp_path)
    original=(tmp_path/'rail.sqlite').read_bytes()
    owners=facility_track_owners(tmp_path,stations,{})
    assert {key:record['station_id'] for key,record in owners.items()}=={'NE-a':'way/1','NE-b':'way/2'}
    assert all(record['source']=='osm_facility_area_membership' and record['snapshot']
               and record['verification_status']=='automatic_reference' and record['confidence'] is None for record in owners.values())
    monkeypatch.setattr(module,'rail_station_records',lambda *args,**kwargs:(stations,3))
    assert sync_station_catalog(tmp_path,catalog.path,[],{})
    model=StationCatalogModel(catalog.path)
    assert model.track_ids_for_catalog({'ST-mixed'})=={'object:network_edge_id:NE-a','object:network_edge_id:NE-b'}
    assert model.track_ids_below('station:way/1')=={'object:network_edge_id:NE-a'}
    assert model.track_ids_below('station:way/2')=={'object:network_edge_id:NE-b'}
    assert not model.track_ids_below('station:node/3')
    assert not model.ids_below('station:node/3')[1]  # No empty old mixed group.
    host=SimpleNamespace(catalog=catalog,station_model=model)
    assert RailCatalog.effective_directory_path(host,'object:network_edge_id:NE-a')==(
        '车站目录','甲省','甲市','车辆段','甲车辆段','站内轨道','段管线')
    feature=RailCatalog._segment_feature(SimpleNamespace(catalog=catalog,directory=tmp_path),'segment:1')
    assert feature['properties']['network_edge_id']=='NE-a'
    assert (tmp_path/'rail.sqlite').read_bytes()==original


def test_manual_owner_pending_and_role_override_replay_without_source_changes(tmp_path,monkeypatch):
    import desktop.rail_station_catalog_model as module
    stations,_,catalog=dataset(tmp_path)
    monkeypatch.setattr(module,'rail_station_records',lambda *args,**kwargs:(stations,3))
    edits={'object:network_edge_id:NE-a':{'station_id':'way/2','station_assignment':'manual'}}
    assert sync_station_catalog(tmp_path,catalog.path,[],edits)
    model=StationCatalogModel(catalog.path)
    assert model.track_ids_below('station:way/2')=={'object:network_edge_id:NE-a','object:network_edge_id:NE-b'}
    source_edit={'object:network_edge_id:NE-a':{'station_id':'ST-domain-id','station_source':'way/2'}}
    assert facility_track_owners(tmp_path,stations,source_edit)['NE-a']['station_id']=='way/2'
    edits={'object:network_edge_id:NE-a':{'station_assignment':'pending'}}
    assert sync_station_catalog(tmp_path,catalog.path,[],edits)
    model.reset_from_disk()
    assert model.track_ids_below('folder:["stations","待核对"]')=={'object:network_edge_id:NE-a'}
    edits={'object:network_edge_id:NE-a':{'rail_semantics':{'track_role':'main_track'}}}
    assert 'NE-a' not in facility_track_owners(tmp_path,stations,edits)


def test_new_source_snapshot_replaces_baseline_before_assignment_undo(tmp_path,monkeypatch):
    import desktop.rail_station_catalog_model as module
    stations, _, catalog = dataset(tmp_path)
    monkeypatch.setattr(module,'rail_station_records',lambda *args,**kwargs:(stations,3))
    sync_station_catalog(tmp_path,catalog.path,[],{})
    with sqlite3.connect(tmp_path/'rail.sqlite') as db:
        raw = json.loads(db.execute("SELECT data FROM features WHERE id=4").fetchone()[0])
        raw['properties'].update(infrastructure_id='way/2', associated_station_ids=['way/2'])
        db.execute('UPDATE features SET data=? WHERE id=4',(json.dumps(raw),))
    assert sync_station_catalog(tmp_path,catalog.path,[],{})
    key = 'object:network_edge_id:NE-a'
    module.update_station_assignments(catalog.path,{key:{}},{key:{'station_id':'node/3','station_assignment':'manual'}})
    module.update_station_assignments(catalog.path,{key:{}},{})
    with sqlite3.connect(catalog.path) as db:
        assert db.execute('SELECT station_id FROM rail_facility_track_baseline WHERE object_id=?',(key,)).fetchone()[0] == 'way/2'
        assert db.execute('SELECT station_id FROM rail_facility_track_owners WHERE object_id=?',(key,)).fetchone()[0] == 'way/2'


def test_facility_track_checkbox_and_toggle_do_not_show_other_depot(qtbot,tmp_path,monkeypatch):
    import desktop.rail_station_catalog_model as module
    stations,_,catalog=dataset(tmp_path)
    monkeypatch.setattr(module,'rail_station_records',lambda *args,**kwargs:(stations,3))
    sync_station_catalog(tmp_path,catalog.path,[],{})
    model=StationCatalogModel(catalog.path)
    index=model.reveal_id('segment','1')
    assert index.isValid() and model._node(index).kind=='facility_track'
    assert model.flags(index)&Qt.ItemFlag.ItemIsUserCheckable
    model.visible_state(False,set(),set(),False,set(),set(),{'object:network_edge_id:NE-a'},set())
    assert model.data(index,Qt.ItemDataRole.CheckStateRole)==Qt.CheckState.Checked
    calls=[]
    host=SimpleNamespace(facility_tracks_visible=set(),facility_tracks_hidden=set(),
        map=SimpleNamespace(call=lambda *args:calls.append(args)),enabled_requested=SimpleNamespace(emit=lambda:None))
    RailCatalog._toggle_facility_tracks(host,model.track_ids_below('station:way/1'),True)
    assert calls==[('setRailEdgeSelection',['NE-a'],[])]
    assert model.track_ids_below('station:way/2').isdisjoint(host.facility_tracks_visible)


def test_edge_viewport_selection_and_facility_mode_remain_consistent(tmp_path,monkeypatch):
    import desktop.rail_station_catalog_model as module
    stations,_,catalog=dataset(tmp_path)
    monkeypatch.setattr(module,'rail_station_records',lambda *args,**kwargs:(stations,3))
    sync_station_catalog(tmp_path,catalog.path,[],{})
    def read(selection):
        return {f['properties']['network_edge_id'] for f in viewport(tmp_path,'rail',(119.99,29.99,120.02,30.02),16,selection)['features']}
    assert read({'included_edges':['NE-a'],'only_explicit':True})=={'NE-a'}
    assert read({'groups':['ST-mixed'],'excluded_edges':['NE-b']})=={'NE-a'}
    assert read({'groups':['RL-main'],'included_edges':['NE-a']})=={'NE-main','NE-a'}
    assert read({'facility':'lines'})=={'NE-main'}
    assert read({'facility':'lines','only_explicit':True,'included_edges':['NE-a']})=={'NE-a'}
    assert read({'facility':'facilities'})=={'NE-a','NE-b'}


@pytest.mark.parametrize('name,kind', [('甲车辆基地','车辆段'),('乙动车运用所','动车所'),
                                      ('丙动车运用检修所','动车所'),('丁检修基地','检修站')])
def test_facility_names_and_types_use_station_editor_without_extra_station_suffix(name,kind):
    from desktop.catalog_metadata import station_type
    from desktop.rail_station_directory import display_name
    assert station_type({'name':name})==kind
    assert display_name(name)==name


def test_missing_outline_does_not_create_fake_facility_boundary(tmp_path):
    stations,_,_=dataset(tmp_path)
    with sqlite3.connect(tmp_path/'rail.sqlite') as db:
        db.execute("DELETE FROM features WHERE kind='railStationAreas'")
    assert facility_track_owners(tmp_path,stations,{})=={}


def test_connected_named_access_track_belongs_to_depot_but_main_track_stops_traversal(tmp_path):
    stations,tracks,_=dataset(tmp_path)
    access={'properties':{'network_edge_id':'NE-access','catalog_group_id':'RL-access','line_name':'甲动车段出入线',
                         'way_tags':{'railway':'rail','name':'甲动车段出入线'}},
            'geometry':{'type':'LineString','coordinates':[[119.99,30.002],[120.001,30.002]]}}
    main={'properties':{'network_edge_id':'NE-outside-main','catalog_group_id':'RL-main','track_role':'main_track',
                       'way_tags':{'railway':'rail','name':'甲动车段旁正线','usage':'main'}},
          'geometry':{'type':'LineString','coordinates':[[119.98,30.002],[119.99,30.002]]}}
    with sqlite3.connect(tmp_path/'rail.sqlite') as db,sqlite3.connect(tmp_path/'rail_lines.sqlite') as index:
        db.execute('CREATE TABLE edges(id TEXT PRIMARY KEY,data TEXT)')
        index.executescript('CREATE TABLE edges(id TEXT PRIMARY KEY,a,b); CREATE INDEX edge_from ON edges(a); CREATE INDEX edge_to ON edges(b);')
        for key,a,b,feature in [('NE-a','NN-a','NN-b',tracks[0]),('NE-b','NN-y','NN-z',tracks[1]),
                                ('NE-access','NN-entry','NN-a',access),('NE-outside-main','NN-main','NN-entry',main)]:
            edge={'id':key,'from_node':a,'to_node':b,'coordinates':feature['geometry']['coordinates'],
                  'way_tags':feature['properties']['way_tags'],'track_role':feature['properties'].get('track_role','unknown')}
            db.execute('INSERT INTO edges VALUES(?,?)',(key,json.dumps(edge)))
            index.execute('INSERT INTO edges VALUES(?,?,?)',(key,a,b))
        for i,feature in ((6,access),(7,main)):
            db.execute('INSERT INTO features VALUES(?,?,?,?)',(i,'rail','main',json.dumps(feature)))
            p=feature['geometry']['coordinates'];db.execute('INSERT INTO bounds VALUES(?,?,?,?,?)',(i,p[0][0],p[1][0],30.002,30.002))
    owners=facility_track_owners(tmp_path,stations,{})
    assert owners['NE-access']['station_id']=='way/1'
    assert owners['NE-access']['source']=='connected_facility_access_track'
    assert 'NE-outside-main' not in owners and 'NE-main' not in owners


def test_overlapping_named_facility_outlines_do_not_guess_one_owner(tmp_path):
    stations,_,_=dataset(tmp_path)
    with sqlite3.connect(tmp_path/'rail.sqlite') as db:
        raw=db.execute("SELECT data FROM features WHERE id=4").fetchone()[0]
        area=json.loads(raw);area['properties']['infrastructure_id']='way/2';area['properties']['associated_station_ids']=['way/2']
        db.execute('INSERT INTO features VALUES(8,?,?,?)',('railStationAreas','yard',json.dumps(area)))
    assert 'NE-a' not in facility_track_owners(tmp_path,stations,{})


def test_editing_depot_track_changes_only_that_object_not_the_legacy_group():
    calls=[]
    key='object:network_edge_id:NE-a'
    host=SimpleNamespace(overrides={key:{'rail_semantics':{'railway_class':'conventional'}}},
        save_overrides=lambda *args,**kwargs:calls.append((args,kwargs)))
    RailCatalog.save_facility_track_metadata(host,key,'甲车辆段检修线（参考）',{'track_role':'maintenance_track'})
    assert calls[0][0]==({},)
    assert set(calls[0][1]['object_changes'])=={key}
    change=calls[0][1]['object_changes'][key]
    assert change['display_name']==change['line_name']=='甲车辆段检修线'
    assert change['rail_semantics']['railway_class']=='conventional'
    assert change['rail_semantics']['track_role']=='maintenance_track'


def test_map_filters_apply_exact_track_visibility_alongside_catalog_groups():
    import shutil
    import subprocess
    from pathlib import Path
    node=shutil.which('node')
    if not node:
        pytest.skip('JavaScript runtime unavailable')
    path=Path(__file__).parents[1]/'assets'/'map.js'
    source=path.read_text(encoding='utf-8')
    function=source[source.index('function applyRailWays(){'):source.index('async function updateRailViewport(){')]
    probe=r'''
const filters={};
const map={getLayer:()=>true,setFilter:(id,value)=>filters[id]=value};
const visibility={rail:true,railStationTracks:false,railConstruction:false};
let railSections=[],railWays=[],railGroups=[],railExclude=false,railIncludedEdges=[],railExcludedEdges=[];
function evaluate(x,p){
  if(!Array.isArray(x))return x;
  const args=x.slice(1);
  switch(x[0]){
    case 'literal':return args[0];case 'get':return p[args[0]];
    case 'in':return evaluate(args[1],p).includes(evaluate(args[0],p));
    case '!':return !evaluate(args[0],p);case '==':return evaluate(args[0],p)===evaluate(args[1],p);
    case 'all':return args.every(a=>evaluate(a,p));case 'any':return args.some(a=>evaluate(a,p));
    case 'coalesce':return args.map(a=>evaluate(a,p)).find(a=>a!==undefined&&a!==null);
    case 'case':return evaluate(args[0],p)?evaluate(args[1],p):evaluate(args[2],p);
  }
  throw Error('Unsupported expression '+x[0]);
}
function visible(edge,group){return evaluate(filters.rail,{network_edge_id:edge,catalog_group_id:group,construction_status:'operating'});}
railIncludedEdges=['NE-a'];applyRailWays();
if(!visible('NE-a','ST-mixed')||visible('NE-b','ST-mixed'))throw Error('Direct depot selection leaked another track');
railGroups=['ST-mixed'];railExcludedEdges=['NE-b'];applyRailWays();
if(!visible('NE-a','ST-mixed')||visible('NE-b','ST-mixed'))throw Error('Explicit hidden track ignored');
railExclude=true;railExcludedEdges=[];applyRailWays();
if(!visible('NE-a','ST-mixed')||visible('NE-b','ST-mixed'))throw Error('Explicit track must override excluded legacy group');
'''
    subprocess.run([node,'-e',function+probe],check=True,capture_output=True,text=True)
