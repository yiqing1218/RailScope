"""Physical exit pairs, map proportions and full-line destination annotations."""
from copy import deepcopy
from dataclasses import replace
from xml.etree import ElementTree as ET

import pytest
from shapely.geometry import LineString, box

from railscope.domain import Station, StationTrack, NetworkNode, NetworkEdge, InfrastructureLine
from railscope.repository import RailRepository
from railscope.integrity import path_refs
from desktop.station_schematic import outgoing_ports, port_destination, station_svg, station_projection


def pair_repository(count=2):
    repo = RailRepository()
    repo.stations['ST-a'] = Station('ST-a','通用站',120,30,'NN-0')
    # Three independently named railways, each with a physical outbound pair.
    for number,name in enumerate(('甲干线','乙干线','丙干线')):
        line_id = f'IL-{number}'
        repo.lines[line_id] = InfrastructureLine(line_id,name,'rail')
        for track in range(count):
            y = 30+(number*90+track*6)/111320
            coords = ((119.98,y),(119.992,y),(120.002,y))
            a,b = f'NN-{number}-{track}-a',f'NN-{number}-{track}-b'
            repo.nodes[a]=NetworkNode(a,*coords[0]);repo.nodes[b]=NetworkNode(b,*coords[-1])
            edge_id=f'NE-{number}-{track}'
            repo.edges[edge_id]=NetworkEdge(edge_id,a,b,coords,2000,infrastructure_line_id=line_id,
                                          track_role='main_track')
            # The actual yard edge is separate; preserve its whole path.
            c=f'NN-{number}-{track}-c';end=(120.004,y)
            repo.nodes[c]=NetworkNode(c,*end)
            yard_id=edge_id+'-yard'
            repo.edges[yard_id]=NetworkEdge(yard_id,b,c,(coords[-1],end),200,
                                            infrastructure_line_id=line_id,track_role='main_track')
            repo.station_tracks[yard_id]=StationTrack(yard_id,'ST-a','站内轨道',edge_refs=path_refs(repo,[(yard_id,True)]))
    context=[{'properties':{'way_tags':{'railway':'platform'},'infrastructure_id':'way/1'},
              'geometry':{'type':'Polygon','coordinates':[[[120.002,29.9999],[120.0041,29.9999],
                  [120.0041,30.0018],[120.002,30.0018],[120.002,29.9999]]]}}]
    return repo,context


@pytest.mark.parametrize('count',[1,2,4])
def test_three_exit_directions_keep_actual_track_counts_and_matching_colors(count):
    repo,context=pair_repository(count)
    source=deepcopy(repo.edges)
    info={'line_destinations':{f'IL-{i}':{'left':f'甲城{i}','right':f'乙城{i}'} for i in range(3)}}
    svg=ET.fromstring(station_svg(repo,context,station_info=info))
    labels=[e for e in svg.iter() if 'data-line-id' in e.attrib]
    assert len(labels)==3  # All three leave on one side; no fake opposite exits.
    assert {e.attrib['data-track-count'] for e in labels}=={str(count)}
    assert len({e.attrib['style'] for e in labels})==3
    paths={e.attrib['data-edge-id']:e for e in svg.iter() if 'data-edge-id' in e.attrib}
    for label in labels:
        assert label.attrib['data-end']=='left' and '往甲城' in ''.join(label.itertext())
        color=label.attrib['style'].removeprefix('fill:')
        for edge in label.attrib['data-port-edges'].split():
            assert paths[edge].attrib['stroke']==color
        assert float(label.attrib['x'])<float(label.attrib['data-port-x'])
    assert {path.attrib['stroke-width'] for path in paths.values()}=={'5.50'}
    assert all('stroke-dasharray' not in path.attrib for path in paths.values())
    assert repo.edges==source


def test_ports_do_not_label_auxiliary_lines_or_invent_connections():
    repo,context=pair_repository()
    repo.lines['IL-1']=replace(repo.lines['IL-1'],name='某动车所走行线')
    repo.lines['IL-2']=replace(repo.lines['IL-2'],line_role='connecting_line')
    text=station_svg(repo,context)
    svg=ET.fromstring(text)
    labels=[e for e in svg.iter() if 'data-line-id' in e.attrib]
    assert len(labels)==1  # Only clean main-line outlets carry labels.
    assert any('甲干线' in ''.join(label.itertext()) for label in labels)
    assert all('data-edge-id' in e.attrib for e in svg.iter() if e.tag.endswith('path'))
    assert 'data-platform-id' in text and '走行线' not in text


def test_curve_exit_uses_terminal_direction_instead_of_left_right_column():
    repo,_=pair_repository()
    local,*_=station_projection(repo)
    port={'line':repo.lines['IL-0'],'side':'right','vector':(.2,-.98)}
    info={'line_destinations':{'IL-0':{'left':'北京','right':'上海','terminals':[
        {'name':'北京','coordinates':(116.4,39.9)}, {'name':'上海','coordinates':(121.47,31.23)}]}}}
    # Both terminal cities lie east of this station; the southward actual
    # outlet must still get the southern full-line terminus.
    repo.stations['ST-a']=replace(repo.stations['ST-a'],lon=114)
    local,*_=station_projection(repo)
    assert port_destination(port,info,local)=='上海'
    port['vector']=(.2,.98)
    assert port_destination(port,info,local)=='北京'


def test_station_at_line_terminus_does_not_label_both_ends_with_other_city():
    repo,_=pair_repository()
    local,*_=station_projection(repo)
    port={'line':repo.lines['IL-0'],'side':'bottom','vector':(0,-1)}
    info={'line_destinations':{'IL-0':{'terminals':[
        {'name':'本城','coordinates':(120,30)}, {'name':'北城','coordinates':(120,40)}]}}}
    assert port_destination(port,info,local)=='本城'
    port['vector']=(0,1)
    assert port_destination(port,info,local)=='北城'


def test_frame_reaches_two_tracks_after_throat_fan():
    repo,context=pair_repository()
    # Add two sidings that join the existing pair in the outside throat.
    # These additional physical station edges must remain complete in export.
    for track in range(2):
        end=repo.edges[f'NE-0-{track}'].coordinates[1]
        start=(120.003,30+(track*6+35)/111320)
        a=f'NN-extra-{track}';b=f'NN-switch-{track}'
        repo.nodes[a]=NetworkNode(a,*start);repo.nodes[b]=NetworkNode(b,*end)
        key=f'NE-extra-{track}'
        repo.edges[key]=NetworkEdge(key,a,b,(start,end),1000,infrastructure_line_id='IL-0',track_role='other_station_track')
        repo.station_tracks[key]=StationTrack(key,'ST-a','站内轨道',edge_refs=path_refs(repo,[(key,True)]))
    svg=ET.fromstring(station_svg(repo,context))
    labels=[e for e in svg.iter() if e.attrib.get('data-line-id')=='IL-0']
    assert len(labels)==1 and labels[0].attrib['data-track-count']=='2'
    assert all(any(e.attrib.get('data-edge-id')==f'NE-extra-{i}' for e in svg.iter()) for i in (0,1))


def test_boundary_touch_is_not_an_exit():
    repo,_=pair_repository()
    geometry=LineString([(-50,0),(100,0),(50,50)])
    ports=outgoing_ports(repo,{'NE-0-0':geometry},(-100,-100,100,100),box(-100,-100,100,100))
    assert ports==[]


def test_approach_loading_follows_indexed_endpoints_and_preserves_complete_edges(tmp_path):
    import json
    import sqlite3
    from desktop.station_tracks import extend_station_approaches
    source=tmp_path/'rail.sqlite';index_path=tmp_path/'rail_lines.sqlite'
    seed={'id':'NE-a','from_node':'NN-a','to_node':'NN-b','coordinates':[[120,30],[120.01,30]]}
    continuation={'id':'NE-b','from_node':'NN-b','to_node':'NN-c','coordinates':[[120.01,30],[120.02,30]]}
    long={'id':'NE-c','from_node':'NN-c','to_node':'NN-d','coordinates':[[120.02,30],[120.1,30]]}
    distant={'id':'NE-d','from_node':'NN-d','to_node':'NN-e','coordinates':[[120.1,30],[120.2,30]]}
    isolated={'id':'NE-isolated','from_node':'NN-x','to_node':'NN-y','coordinates':[[120.01,30],[120.011,30]]}
    with sqlite3.connect(source) as db,sqlite3.connect(index_path) as index:
        db.execute('CREATE TABLE edges(id TEXT PRIMARY KEY,data TEXT)')
        index.executescript('CREATE TABLE edges(id TEXT PRIMARY KEY,a,b); CREATE INDEX edge_a ON edges(a); CREATE INDEX edge_b ON edges(b);')
        for edge in (seed,continuation,long,distant,isolated):
            db.execute('INSERT INTO edges VALUES(?,?)',(edge['id'],json.dumps(edge)))
            index.execute('INSERT INTO edges VALUES(?,?,?)',(edge['id'],edge['from_node'],edge['to_node']))
    original=source.read_bytes()
    edges=[seed]
    with sqlite3.connect(source) as db:
        extend_station_approaches(db,index_path,edges,(120,30,120.01,30),distance=2500)
    assert {edge['id'] for edge in edges}=={'NE-a','NE-b','NE-c'}
    assert next(edge for edge in edges if edge['id']=='NE-c')==long
    assert source.read_bytes()==original
