from dataclasses import asdict, replace
import json
import sqlite3

import pytest

from railscope.domain import Station, StationTrack, ServiceArea, ServiceAreaGeometry
from railscope.integrity import delete_edge, path_refs, validate_repository
from railscope.workspace import SQLiteWorkspace
from desktop.domain_adapter import build_repository
from desktop.tests.test_full_corridor import fixture
from desktop.station_tracks import automatic_numbering
from desktop.station_schematic import station_svg


def test_entity_properties_persist_and_track_references_protect_edges(tmp_path):
    payload, edges = fixture()
    payload['routes'][0]['color'] = '#234abc'
    repo, bindings = build_repository({'edges': edges}, payload, tmp_path/'identity.sqlite')
    station = next(iter(repo.stations.values()))
    key = bindings['edges'][edges[0]['id']]
    repo.station_tracks['TRK-one'] = StationTrack('TRK-one', station.id, '到发1道', '1',
        is_virtual=False, edge_refs=path_refs(repo, [(key, True)]))
    repo.service_areas['RSA-one'] = ServiceArea('RSA-one', '统一服务区', 120, 31)
    repo.service_area_geometries['SAG-one'] = ServiceAreaGeometry('SAG-one', 'RSA-one', 'poi', {'type':'Point','coordinates':[120,31]})
    store = SQLiteWorkspace(tmp_path/'workspace.sqlite'); store.seed(repo)
    loaded, _ = store.load()
    assert next(iter(loaded.corridors.values())).color == '#234abc'
    assert loaded.station_tracks == repo.station_tracks
    assert loaded.service_area_geometries == repo.service_area_geometries
    loaded.corridors.clear(); loaded.train_runs.clear(); loaded.stops.clear()
    with pytest.raises(ValueError): delete_edge(loaded, key)
    loaded.service_areas.clear()
    with pytest.raises(ValueError, match='owner missing'): validate_repository(loaded)


def test_numbering_keeps_manual_names_and_schematic_uses_shared_geometry(tmp_path, qtbot):
    payload, edges = fixture()
    repo, bindings = build_repository({'edges':edges}, payload, tmp_path/'identity.sqlite')
    station=next(iter(repo.stations.values()))
    for index, edge in enumerate(edges[:3]):
        key=bindings['edges'][edge['id']]
        refs=path_refs(repo,[(key,True)])
        repo.station_tracks[str(index)]=StationTrack(str(index),station.id,
            '货1道' if index==0 else '未编号股道', '1' if index==0 else None,
            edge_refs=refs,length_m=refs[-1].end_distance_m,is_virtual=False,
            verification_status='user_named' if index==0 else 'source_unverified')
    assert automatic_numbering(repo)==2
    assert repo.station_tracks['0'].name=='货1道'
    assert {t.track_number for t in repo.station_tracks.values()}=={'1',None}
    assert all(repo.station_tracks[key].provenance['display_alias']['verification_status'] == 'display_only'
               for key in ('1', '2'))
    assert automatic_numbering(repo)==0
    from desktop.station_track_ui import StationTrackDialog
    dialog=StationTrackDialog(repo);qtbot.addWidget(dialog)
    dialog.table.item(1,0).setText('Ⅰ道');dialog.table.item(1,1).setText('Ⅰ');dialog.collect()
    assert repo.station_tracks['0'].name=='货1道'
    assert repo.station_tracks['1'].name=='Ⅰ道'
    svg=station_svg(repo)
    assert 'Ⅰ道' in svg and 'data-edge-id=' in svg and 'data-track-id=' in svg
    assert '<image' not in svg


def test_speed_is_independent_of_category_and_construction():
    from desktop.display_names import apply_rail_presentation
    from desktop.rail_style_ui import defaults, validate_styles, SPEED_STYLE_KEYS
    from desktop.line_metadata import normalize_line_attributes
    feature={'properties':{'network_edge_id':'NE-one','line_id':'RL-one','track_type':'高速铁路线',
        'construction':True,'way_tags':{'maxspeed':'160','maxspeed:design':'350'}}}
    apply_rail_presentation({'features':[feature]}, {})
    assert feature['properties']['design_speed_kmh']==350
    assert feature['properties']['construction'] is True
    assert feature['properties']['track_type']=='高速铁路线'
    styles=defaults()
    for key,pattern in zip(SPEED_STYLE_KEYS,('dotted','dashed','long_dash','dash_dot')):styles[key]['pattern']=pattern
    assert validate_styles(styles)==styles
    assert normalize_line_attributes({'design_speed_kmh':'250'},'rail')['design_speed_kmh']=='250'
    with pytest.raises(ValueError):normalize_line_attributes({'design_speed_kmh':'快'},'rail')


def test_picked_map_properties_keep_entity_membership_and_names():
    from desktop.display_names import apply_rail_presentation, apply_names
    props = {'network_edge_id':'NE-one', 'line_id':'RL-one',
             'way_tags':'{"maxspeed:design":"350"}',
             'line_ids':'["RL-one"]', 'construction_line_ids':'["RL-one"]'}
    data = {'features':[{'properties':props}]}
    apply_rail_presentation(data, {})
    assert props['line_ids'] == ['RL-one']
    assert props['construction_line_ids'] == ['RL-one']
    assert props['design_speed_kmh'] == 350
    service = {'service_id':'RSA-area','service_alias_ids':'["RSA-point"]'}
    apply_names({'features':[{'properties':service}]},
                {'object:service_id:RSA-point':{'display_name':'服务区新名称'}})
    assert service['display_name'] == '服务区新名称'


def test_legacy_service_poi_merges_into_unique_area_and_all_names_match(tmp_path):
    from desktop.road_services import create_tables, services, viewport, service_repository
    from desktop.display_names import apply_names
    path=tmp_path/'services.sqlite'
    with sqlite3.connect(path) as db:
        create_tables(db)
        for key,name in [('RSA-area','某服务区'),('RSA-point','某服务区（停车场）')]:
            db.execute('INSERT INTO services VALUES(?,?,?,?,?,?,?,?,?)',(key,name,'浙江','杭州','区',120,31,120.01,31.01))
        for index,owner,kind,geom in [(1,'RSA-area','outline',{'type':'Polygon','coordinates':[[[120,31],[120.01,31],[120.01,31.01],[120,31.01],[120,31]]]}),
                (2,'RSA-point','poi',{'type':'Point','coordinates':[120.005,31.005]})]:
            data={'type':'Feature','properties':{'service_id':owner,'asset_kind':kind,'source_ref':str(index)},'geometry':geom}
            db.execute('INSERT INTO service_features VALUES(?,?,?,?)',(index,owner,kind,json.dumps(data)))
            db.execute('INSERT INTO service_bounds VALUES(?,?,?,?,?)',(index,120,120.01,31,31.01))
    overrides={'object:service_id:RSA-area':{'display_name':'改名服务区'}}
    assert [s['id'] for s in services(path)]==['RSA-area']
    repo=service_repository(path,overrides)
    assert len(repo.service_areas)==1 and len(repo.service_area_geometries)==2
    assert repo.service_areas['RSA-area'].name=='改名服务区'
    data=viewport(path,[119,30,121,32],['RSA-area'],15);apply_names(data,overrides)
    assert {f['properties']['display_name'] for f in data['features']}=={'改名服务区'}
    assert {f['properties']['service_id'] for f in data['features']}=={'RSA-area'}
    assert viewport(path,[119,30,121,32],[],15)['features']==[]


def test_corridor_color_round_trips_csv_and_rejects_invalid():
    from desktop.rail_lines import export_corridor_csv, import_corridor_csv
    from railscope.presentation import valid_color
    route={'id':'COR-one','name':'蓝色通道','color':'#3344aa','sequence':[{'kind':'endpoint','node_id':1},{'kind':'line','line_id':'RL-one'},{'kind':'endpoint','node_id':2}],'extensions':{}}
    assert import_corridor_csv(export_corridor_csv({'corridors':[route]}))['corridors']==[route]
    with pytest.raises(ValueError):valid_color('bad')


def test_hidden_lines_and_states_are_removed_before_viewport_budget(tmp_path):
    from desktop.rail_store import viewport
    with sqlite3.connect(tmp_path/'rail.sqlite') as db:
        db.executescript('CREATE TABLE features(id INTEGER PRIMARY KEY,kind,service,data); CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);')
        for i,group,state in [(1,'RL-hidden','operating'),(2,'RL-hidden','operating'),(3,'RL-build','construction'),(4,'RL-open','operating')]:
            feature={'type':'Feature','properties':{'catalog_group_id':group,'construction_status':state},'geometry':{'type':'LineString','coordinates':[[120,31],[120.01,31.01]]}}
            db.execute('INSERT INTO features VALUES(?,?,?,?)',(i,'rail','main',json.dumps(feature)))
            db.execute('INSERT INTO bounds VALUES(?,?,?,?,?)',(i,120,120.01,31,31.01))
    result=viewport(tmp_path,'rail',[119,30,121,32],12,{'groups':['RL-hidden'],'exclude':True,'states':['operating']},
                    {'features':1,'bytes':None,'vertices':None,'feature_bytes':None})
    assert [f['properties']['catalog_group_id'] for f in result['features']]==['RL-open']
    assert result['truncated'] is False


def test_point_visibility_uses_incident_edge_state_on_mixed_line(tmp_path):
    from desktop.rail_store import viewport
    with sqlite3.connect(tmp_path/'rail.sqlite') as db:
        db.executescript('CREATE TABLE features(id INTEGER PRIMARY KEY,kind,service,data); CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);')
        for node in (1,2,3):
            feature={'type':'Feature','properties':{'osm_node_id':node,'kind':'station'},'geometry':{'type':'Point','coordinates':[120,31]}}
            db.execute('INSERT INTO features VALUES(?,?,?,?)',(node,'railPoints','main',json.dumps(feature)))
            db.execute('INSERT INTO bounds VALUES(?,?,?,?,?)',(node,120,120,31,31))
    with sqlite3.connect(tmp_path/'rail_lines.sqlite') as db:
        db.executescript("""CREATE TABLE edges(line_id,a,b,construction); CREATE TABLE node_aliases(source_id,node_id); CREATE TABLE station_aliases(station_node_id,anchor_node); CREATE TABLE lines(id,source_name);
            INSERT INTO lines VALUES('RL-mixed','同一条线路'); INSERT INTO edges VALUES('RL-mixed',1,2,1),('RL-mixed',2,3,0);
            INSERT INTO node_aliases VALUES(1,1),(2,2); INSERT INTO station_aliases VALUES(3,3);""")
    data=viewport(tmp_path,'railPoints',[119,30,121,32],15)
    points={f['properties']['osm_node_id']:f['properties'] for f in data['features']}
    assert points[1]['construction_line_ids']==['RL-mixed'] and points[1]['operating_line_ids']==[]
    assert points[2]['construction_line_ids']==points[2]['operating_line_ids']==['RL-mixed']
    assert points[3]['construction_line_ids']==[] and points[3]['operating_line_ids']==['RL-mixed']


def test_numbered_track_fragments_join_only_at_real_shared_nodes():
    from desktop.station_tracks import track_sections, track_number
    edges = {key:{'id':key,'from_node':a,'to_node':b,'way_tags':{'railway:track_ref':'3'}}
             for key,a,b in [('a',1,2),('b',2,3),('c',8,9)]}
    sections = {'RS-a':{'a'},'RS-b':{'b'},'RS-c':{'c'}}
    assert set(track_sections(sections,edges,{}))=={('RS-a','RS-b'),('RS-c',)}
    overrides = {f'object:section_id:RS-{key}':{'display_name':name,'verification_status':'user_named'} for key,name in [('a','上行3道'),('b','下行3道')]}
    assert len(track_sections(sections,edges,overrides))==3
    assert track_number({'service':'siding','ref':'3043'}) is None
    assert track_number({'name':'上海虹桥站Ⅰ道'})=='Ⅰ'


def test_train_and_track_editor_resolve_the_same_track_entity(tmp_path):
    payload,edges=fixture()
    identity=tmp_path/'identity.sqlite'
    repo,bindings=build_repository({'edges':edges},payload,identity)
    first=repo.stops[0]
    edge=bindings['edges']['e1']
    track=StationTrack('TRK-named',first.station_id,'Ⅰ道','Ⅰ',is_virtual=False,
                       edge_refs=path_refs(repo,[(edge,True)]))
    payload['trains'][0]['stops'][0]['station_track_id']='e1'
    line=next(iter(bindings['lines']))
    overrides={'object:section_id:RS-one':{'station_track':asdict(track),'source_edge_ids':['e1']},
               line:{'technical_attributes':{'design_speed_kmh':'250'}}}
    rebuilt,_=build_repository({'edges':edges},payload,identity,overrides)
    assert rebuilt.stops[0].station_track_id=='TRK-named'
    assert rebuilt.station_tracks['TRK-named'].name=='Ⅰ道'
    assert rebuilt.lines[bindings['lines'][line]].design_speed_kmh==250
