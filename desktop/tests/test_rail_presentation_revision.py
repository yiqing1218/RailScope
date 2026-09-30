import json
from copy import deepcopy
import pytest

from desktop.rail_style_resolver import style_key, speed_band, STYLE_SELECTIONS
from desktop.rail_style_ui import defaults, validate_styles, RailStyleDialog
from desktop.display_names import apply_rail_presentation, apply_names
from desktop.rail_catalog_ui import RailCatalog
from desktop.tests.test_operating_ui import MapStub


@pytest.mark.parametrize('speed,band', [(350,'300-350'), (300,'300-350'), (299,'250-300'),
    (250,'250-300'), (249,'200-250'), (200,'200-250'), (199,'150-200'), (150,'150-200'), (None,'unknown')])
def test_range_boundaries_and_unknown(speed, band):
    assert speed_band({'design_speed_kmh': speed}) == band


def test_every_supported_class_function_has_a_unique_style():
    assert len(STYLE_SELECTIONS) == len(set(STYLE_SELECTIONS.values()))
    for key, (group, category, function, band) in STYLE_SELECTIONS.items():
        facts = {'railway_class': category, 'line_role': function if group=='track' else 'unknown',
                 'track_role': 'main_track' if group=='track' else function,
                 'facility_only': group=='station', 'speed_band': band}
        assert style_key(facts) == key


def test_unresolved_line_uses_default_without_claiming_main_line():
    from desktop.rail_style_resolver import line_selection
    facts={'railway_class':'conventional','line_role':'unknown','track_role':'unknown'}
    assert line_selection(facts)[2]=='unknown'
    assert style_key(facts)=='_default'


def test_legacy_connector_does_not_get_overwritten_by_old_generic_style():
    props = {'network_edge_id':'NE-1', 'catalog_group_id':'RL-a', 'line_id':'IL-a',
             'name':'甲乙联络线', 'track_type':'普速铁路线',
             'way_tags':{'railway':'rail','usage':'main','highspeed':'no'}}
    feature = {'type':'Feature','properties':props,'geometry':{'type':'LineString','coordinates':[[120,30],[121,30]]}}
    collection = {'features':[feature]}
    apply_rail_presentation(collection, {}, {'RL-a':{'track_type':'普速铁路线'}})
    assert props['rail_style_key'] == 'track.conventional.connecting_line'
    assert props['provenance']['line_role']['verification_status'] == 'inferred'


def test_default_style_and_add_remove_roundtrip(qtbot):
    styles=defaults();dialog=RailStyleDialog(styles);qtbot.addWidget(dialog)
    dialog.group_choice.setCurrentIndex(dialog.group_choice.findData('station'))
    dialog.class_choice.setCurrentIndex(dialog.class_choice.findData('freight'))
    dialog.function_choice.setCurrentIndex(dialog.function_choice.findData('crossover'))
    dialog.add_style();key='station.freight.crossover'
    dialog.controls[key][0].setText('#112233')
    saved=validate_styles(dialog.value())
    assert saved[key]['color']=='#112233'
    dialog.remove_style();dialog.controls['_default'][0].setText('#445566')
    assert dialog.value()[key]['color']=='#445566'


def test_old_speed_style_file_preserves_custom_palette():
    from desktop.rail_style_resolver import COMPAT_STYLE_SOURCES
    from desktop.rail_style_ui import LEGACY_STYLE_TYPES, ZOOM_CURVE_KEY
    current=defaults()
    old={k:deepcopy(current[k]) for k in (*COMPAT_STYLE_SOURCES,*LEGACY_STYLE_TYPES,ZOOM_CURVE_KEY)}
    old['class.high_speed.350']['color']='#123456'
    old['line.connecting_line']['color']='#654321'
    migrated=validate_styles(old)
    assert migrated['track.high_speed.main_line.300-350']['color']=='#123456'
    assert migrated['track.conventional.connecting_line']['color']=='#654321'


def test_combined_line_reedit_updates_all_names_semantics_and_speed(qtbot,tmp_path):
    records={f'RL-{key}':{'name':key,'way_ids':[i],'railway_class':category,
             'line_role':'main_line','track_role':'main_track'}
             for i,(key,category) in enumerate((('a','conventional'),('b','high_speed')),1)}
    (tmp_path/'rail_catalog.json').write_text(json.dumps(records),encoding='utf-8')
    widget=RailCatalog(tmp_path,tmp_path/'settings.json',MapStub());qtbot.addWidget(widget)
    widget.merge_line_segments(set(records),'合并线')
    widget.save_overrides({'RL-b':{'display_name':'新合并名','rail_semantics':{
        'railway_class':'high_speed','line_role':'connecting_line','track_role':'main_track'},
        'technical_attributes':{'speed_band':'250-300'}}})
    for key in records:
        meta=widget.meta(key)
        assert widget.display_name(key)=='新合并名'
        assert style_key(meta)=='track.high_speed.connecting_line.250-300'
        props={'network_edge_id':'NE-'+key,'catalog_group_id':key,'name':'旧名字',
               'track_type':'普速铁路线','way_tags':{'railway':'rail','usage':'main','highspeed':'no'}}
        collection={'features':[{'properties':props,'geometry':{'type':'LineString','coordinates':[[120,30],[121,30]]}}]}
        apply_names(collection,widget.overrides);apply_rail_presentation(collection,{},widget.overrides)
        assert props['display_name']=='新合并名'
        assert props['rail_style_key']=='track.high_speed.connecting_line.250-300'


def test_old_assembly_gets_one_attribute_set_without_renumbering(qtbot,tmp_path):
    records={'RL-a':{'name':'旧甲','line_id':'IL-a','way_ids':[1], 'railway_class':'conventional',
                      'line_role':'connecting_line','track_role':'main_track'},
             'RL-b':{'name':'旧乙','line_id':'IL-b','way_ids':[2], 'railway_class':'high_speed',
                      'line_role':'main_line','track_role':'main_track'}}
    saved={key:{'assembly_id':'RLU-old','assembly_name':'已合并线','folder_path':['旧自定义目录']}
           for key in records}
    saved['line-assembly:RLU-old']={'name':'已合并线','members':['IL-a','IL-b'],'active':True}
    (tmp_path/'rail_catalog.json').write_text(json.dumps(records),encoding='utf-8')
    (tmp_path/'settings.json').write_text(json.dumps(saved),encoding='utf-8')
    widget=RailCatalog(tmp_path,tmp_path/'settings.json',MapStub());qtbot.addWidget(widget)
    for key in records:
        assert style_key(widget.meta(key))=='track.conventional.connecting_line'
        assert widget.display_name(key)=='已合并线'
        assert widget.meta(key)['folder_path']==['旧自定义目录']
    assert widget.overrides['line-assembly:RLU-old']['members']==['IL-a','IL-b']
    assert widget.catalog['RL-b']['way_ids']==[2]


def test_directory_move_does_not_rebuild_or_invalidate_topology(qtbot,tmp_path,monkeypatch):
    records={f'RL-{i}':{'name':f'线路{i}','way_ids':[i],'track_role':'main_track'} for i in range(700)}
    (tmp_path/'rail_catalog.json').write_text(json.dumps(records),encoding='utf-8')
    widget=RailCatalog(tmp_path,tmp_path/'settings.json',MapStub());qtbot.addWidget(widget)
    events=[];widget.metadata_changed.connect(lambda: events.append('topology'))
    widget.directory_changed.connect(lambda:events.append('directory'))
    def reject(*args,**kwargs): raise AssertionError('单线移目录不得重建全国目录')
    monkeypatch.setattr(widget,'populate',reject)
    monkeypatch.setattr(widget,'_populate_paged_directory',reject)
    widget.move_items({'RL-699'},['自定义','新目录'])
    assert events==['directory']
    assert widget.line_model.reveal_catalog_id('RL-699').isValid()
    widget.undo_catalog();assert widget.meta('RL-699').get('folder_path') is None
    widget.redo_catalog();assert widget.meta('RL-699')['folder_path']==['自定义','新目录']
    assert events==['directory','directory','directory']


@pytest.mark.parametrize('tags,expected', [({'railway':'depot'},'车辆段'),
    ({'railway':'workshop'},'检修站'),({'railway:facility':'classification_yard'},'编组站'),
    ({'railway':'engine_shed'},'机务段'),({'name':'动车所'},'动车所')])
def test_facility_types(tags,expected):
    from desktop.catalog_metadata import station_type
    assert station_type(tags)==expected


def test_station_type_from_real_area_keeps_original_node_tags(tmp_path):
    import sqlite3
    from desktop.rail_station_directory import build_station_directory
    source=tmp_path/'rail.sqlite'
    point={'properties':{'osm_node_id':1,'kind':'station','name':'甲','node_tags':{'railway':'station'}},
           'geometry':{'type':'Point','coordinates':[120,30]}}
    area={'properties':{'infrastructure_id':'way/8','way_tags':{'railway:facility':'classification_yard'},
          'associated_station_ids':[1]},'geometry':{'type':'Polygon','coordinates':[]}}
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE features(id PRIMARY KEY,kind,network,data)')
        db.executemany('INSERT INTO features VALUES(?,?,?,?)',[(1,'railPoints','main',json.dumps(point)),
                                                             (2,'railStationAreas','main',json.dumps(area))])
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE station_aliases(source_id,alias)')
        build_station_directory(db,source)
        props=json.loads(db.execute('SELECT data FROM station_directory').fetchone()[0])['properties']
    assert props['station_type_hint']=='编组站'
    assert props['station_type_provenance']['source_member_ids']==['way/8']
    with sqlite3.connect(source) as db:
        assert json.loads(db.execute('SELECT data FROM features WHERE id=1').fetchone()[0])==point


def test_real_platform_relation_recovers_far_outline_and_depot(tmp_path):
    from desktop.import_rail import extract as import_rail
    from desktop.rail_boundaries import extract
    source=tmp_path/'source.osm'
    source.write_text('''<osm version="0.6">
    <node id="1" lat="30" lon="120"><tag k="railway" v="station"/><tag k="name" v="甲站"/></node>
    <node id="2" lat="30" lon="120.022"/><node id="3" lat="30.001" lon="120.022"/>
    <node id="4" lat="30.001" lon="120.023"/><node id="5" lat="30" lon="120.023"/>
    <node id="6" lat="30.01" lon="120.01"/><node id="7" lat="30.011" lon="120.01"/>
    <node id="8" lat="30.011" lon="120.011"/><node id="9" lat="30.01" lon="120.011"/>
    <way id="10"><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="5"/><nd ref="2"/>
    <tag k="railway" v="platform"/><tag k="train" v="yes"/></way>
    <way id="11"><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="9"/><nd ref="6"/>
    <tag k="railway" v="depot"/><tag k="name" v="甲车辆段"/></way>
    <way id="12"><nd ref="1"/><nd ref="2"/><tag k="railway" v="rail"/><tag k="name" v="甲线"/></way>
    <relation id="20"><member type="node" ref="1" role="stop"/><member type="way" ref="10" role="platform"/>
    <tag k="type" v="public_transport"/><tag k="public_transport" v="stop_area"/><tag k="train" v="yes"/></relation>
    </osm>''',encoding='utf-8')
    directory=tmp_path/'rail';import_rail(source,directory)
    from desktop.rail_line_store import build_line_index, fingerprint, index_ready
    build_line_index(directory/'rail.sqlite',directory/'rail_lines.sqlite',[],[])
    report=extract(source,directory,progress=lambda _:None)
    assert index_ready(directory/'rail_lines.sqlite',fingerprint(directory/'rail.sqlite',[]))
    platforms=json.loads((directory/'rail_platforms.geojson').read_text(encoding='utf-8'))['features']
    platform=next(f for f in platforms if f['properties'].get('osm_way_id')==10)
    assert platform['geometry']['type'] in ('Polygon','MultiPolygon')
    assert platform['properties']['association_source']=='osm_stop_area_membership'
    assert platform['properties']['associated_station_ids']==[1]
    assert report['facility_points']==0  # rerun reuses the existing source-stable facility
    import sqlite3
    with sqlite3.connect(directory/'rail.sqlite') as db:
        row=db.execute("SELECT data FROM features WHERE kind='railPoints' AND json_extract(data,'$.properties.station_source_id')='way/11'").fetchone()
    props=json.loads(row[0])['properties']
    assert props['kind']=='depot' and 'osm_node_id' not in props
    assert props['geometry_source']=='osm_area_representative_point'


def test_station_svg_shows_line_names_and_destinations_without_track_numbers():
    from railscope.domain import Station, NetworkNode, NetworkEdge, InfrastructureLine, StationTrack
    from railscope.repository import RailRepository
    from railscope.integrity import path_refs
    from desktop.station_schematic import station_svg
    # Use the same shared repository and continuous path refs as the real exporter.
    repo=RailRepository()
    repo.nodes={'NN-a':NetworkNode('NN-a',120,30), 'NN-b':NetworkNode('NN-b',120.01,30)}
    repo.lines={'IL-a':InfrastructureLine('IL-a','甲乙线','rail')}
    repo.edges={'NE-a':NetworkEdge('NE-a','NN-a','NN-b',((120,30),(120.01,30)),1000,
                infrastructure_line_id='IL-a',track_role='main_track',construction_status='operating')}
    repo.stations={'ST-a':Station('ST-a','甲站',120,30,'NN-a')}
    refs=path_refs(repo,[('NE-a',True)])
    repo.station_tracks={'TRK-a':StationTrack('TRK-a','ST-a','甲站 · 12道','12',length_m=1000,edge_refs=refs)}
    text=station_svg(repo,station_info={'summary':'客运站 · 站台：2 · 股道：6',
        'line_destinations':{'IL-a':{'left':'乙站','right':'丙站'}}})
    assert '甲乙线 · 往乙站' in text and '甲乙线 · 往丙站' in text
    assert '客运站 · 站台：2 · 股道：6' in text
    assert '12道' not in text and '站台 12' not in text
    from dataclasses import replace
    repo.lines['IL-a']=replace(repo.lines['IL-a'],name='第12道')
    assert '12道' not in station_svg(repo)


@pytest.mark.parametrize('station_name', ['甲站','北京南站','某车辆段'])
def test_generic_station_export_keeps_map_proportions_and_rectangular_platforms(station_name):
    import math
    import re
    from xml.etree import ElementTree as ET
    from railscope.domain import Station, NetworkNode, NetworkEdge, InfrastructureLine, StationTrack
    from railscope.repository import RailRepository
    from railscope.integrity import path_refs
    from desktop.station_schematic import station_svg, station_projection
    repo = RailRepository()
    coords = ((120,30),(120.006,30.002),(120.012,30))
    repo.nodes = {'NN-a':NetworkNode('NN-a',*coords[0]),'NN-b':NetworkNode('NN-b',*coords[-1])}
    repo.lines = {'IL-a':InfrastructureLine('IL-a','甲乙干线','rail')}
    repo.edges = {'NE-a':NetworkEdge('NE-a','NN-a','NN-b',coords,1200,infrastructure_line_id='IL-a')}
    repo.stations = {'ST-a':Station('ST-a',station_name,*coords[0],'NN-a')}
    repo.station_tracks = {'TRK-a':StationTrack('TRK-a','ST-a','第12道',edge_refs=path_refs(repo,[('NE-a',True)]))}
    context = [{'properties':{'way_tags':{'railway':'platform'},'infrastructure_id':'way/platform'},
                'geometry':{'type':'LineString','coordinates':[[120.004,30.0021],[120.008,30.003]]}}]
    original = deepcopy(context)
    text = station_svg(repo,context)
    svg = ET.fromstring(text)
    paths = [element for element in svg.iter() if 'data-edge-id' in element.attrib]
    points = [tuple(map(float,point.split(','))) for point in re.findall(r'[\d.]+,[\d.]+',paths[0].attrib['d'])]
    local, *_ = station_projection(repo)
    source = [local(point) for point in coords]
    scales = [(points[1][0]-points[0][0])/(source[1][0]-source[0][0]),
              -(points[1][1]-points[0][1])/(source[1][1]-source[0][1])]
    assert math.isclose(*scales,rel_tol=1e-4)
    assert len(points)==len(coords)  # No clipped or simplified station track.
    left,top,right,bottom = map(float,next(element.attrib['data-track-bounds'] for element in svg.iter() if 'data-track-bounds' in element.attrib).split(','))
    assert all(left<x<right and top<y<bottom for x,y in points)
    symbols = [element for element in svg.iter() if 'data-platform-id' in element.attrib]
    assert len(symbols)==1 and symbols[0].tag.endswith('rect') and 'rotate(' in symbols[0].attrib['transform']
    assert context==original
    assert len({(path.attrib['stroke'],path.attrib['stroke-width']) for path in paths})==1
    assert 'stroke-dasharray' not in text and '岔接点' not in text and '12道' not in text
    assert not [element for element in svg.iter() if element.tag.endswith('path') and 'data-edge-id' not in element.attrib]
    for element in svg.iter():
        if 'data-line-id' in element.attrib:
            assert float(element.attrib['x'])<left or float(element.attrib['x'])>right


def test_station_directions_use_full_line_termini_and_explicit_overrides(tmp_path):
    import sqlite3
    from railscope.domain import Station, NetworkNode, NetworkEdge, InfrastructureLine
    from railscope.repository import RailRepository
    from desktop.station_tracks import schematic_station_info
    from desktop.line_metadata import normalize_line_attributes
    repo = RailRepository()
    repo.stations = {'ST-a':Station('ST-a','中途站',118,32,'NN-a')}
    repo.nodes = {'NN-a':NetworkNode('NN-a',118,32),'NN-b':NetworkNode('NN-b',118.1,32)}
    repo.lines = {'IL-a':InfrastructureLine('IL-a','京沪高速线','rail',source_id='RL-a')}
    repo.edges = {'NE-a':NetworkEdge('NE-a','NN-a','NN-b',((118,32),(118.1,32)),1000,infrastructure_line_id='IL-a')}
    with sqlite3.connect(tmp_path/'rail_lines.sqlite') as db:
        db.execute('CREATE TABLE station_directory(source_id TEXT,name TEXT,data TEXT)')
        for key,name,coord in [('node/1','中途站',(118,32)),('node/2','附近节点站',(118.1,32))]:
            db.execute('INSERT INTO station_directory VALUES (?,?,?)',(key,name,json.dumps({'properties':{},'geometry':{'coordinates':coord}})))
    rows = {'TRK-a':{'station_source':'node/1'}}
    info = schematic_station_info(tmp_path,repo,rows,{})
    assert info['line_destinations']['IL-a']['left']=='北京'
    assert info['line_destinations']['IL-a']['right']=='上海'
    custom = normalize_line_attributes({'start_terminal':'甲城','end_terminal':'乙城'},'rail')
    info = schematic_station_info(tmp_path,repo,rows,{'RL-a':{'technical_attributes':custom}})
    assert info['line_destinations']['IL-a']['left']=='甲城'
    assert info['line_destinations']['IL-a']['right']=='乙城'
    assert info['line_destinations']['IL-a']['source']=='workspace_railway_terminals'
    from dataclasses import replace
    repo.lines['IL-a']=replace(repo.lines['IL-a'],name='任意铁路')
    assert not schematic_station_info(tmp_path,repo,rows,{})['line_destinations']


def test_export_station_without_station_directory_track_group(tmp_path):
    import sqlite3
    from desktop.import_rail import extract
    from desktop.rail_line_store import build_line_index
    from desktop.station_tracks import load_station_tracks
    from desktop.station_schematic import station_svg
    source = tmp_path/'station.osm'
    source.write_text('''<osm version="0.6">
    <node id="1" lat="30" lon="120"><tag k="railway" v="station"/><tag k="name" v="其他站"/></node>
    <node id="2" lat="30" lon="120.005"/><node id="3" lat="30" lon="120.01"/>
    <node id="4" lat="30.0001" lon="120.004"/><node id="5" lat="30.0001" lon="120.006"/>
    <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><tag k="railway" v="rail"/><tag k="name" v="甲乙干线"/></way>
    <way id="11"><nd ref="4"/><nd ref="5"/><tag k="railway" v="platform"/><tag k="train" v="yes"/></way>
    <relation id="12"><member type="node" ref="1" role="stop"/><member type="way" ref="11" role="platform"/>
    <tag k="type" v="public_transport"/><tag k="public_transport" v="stop_area"/><tag k="train" v="yes"/></relation>
    </osm>''',encoding='utf-8')
    directory = tmp_path/'rail'
    extract(source,directory)
    build_line_index(directory/'rail.sqlite',directory/'rail_lines.sqlite',[],[])
    with sqlite3.connect(directory/'rail_catalog.sqlite') as db:
        db.execute('CREATE TABLE catalog(id,station_name,data)')
    repo, rows, context = load_station_tracks(directory,tmp_path/'identity.sqlite',
        {'name':'其他站','station_source_id':'node/1'}, {})
    assert rows and all(row['station_source']=='node/1' for row in rows.values())
    svg = station_svg(repo,context)
    assert '其他站平面布置图' in svg and 'data-platform-id' in svg
    assert all(edge.id.startswith('NE-') for edge in repo.edges.values())


def test_reading_old_classifications_keeps_saved_directory_paths(tmp_path):
    from desktop.rail_catalog_ui import _read_catalog_overrides
    values = {'RL-a':{'folder_path':['高速铁路','八纵八横','京沪通道'],'track_type':'联络线 / 匝道'},
              'RL-b':{'folder_path':['高速铁路','区域高速铁路','1 华北']},
              'RL-c':{'folder_path':['普速铁路','国家铁路干线']},
              'RL-d':{'folder_path':None,'display_name':'甲线'}}
    path = tmp_path/'legacy.json'
    path.write_text(json.dumps(values,ensure_ascii=False),encoding='utf-8')
    assert _read_catalog_overrides(path)==values


def test_folder_change_retains_line_library_until_other_inputs_change(tmp_path):
    from types import SimpleNamespace
    from desktop.rail_ui import RailEditor
    metadata=tmp_path/'overrides.json';metadata.write_text('{}',encoding='utf-8')
    host=SimpleNamespace(catalog_metadata_path=metadata,
        _line_library_signature=('original-source','original-extra','original-index',1,'original-plan'))
    RailEditor.retain_line_library_for_directory_move(host)
    assert host._line_library_signature==('original-source','original-extra','original-index',
                                          metadata.stat().st_mtime_ns,'original-plan')
