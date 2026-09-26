import json
import sqlite3

import pytest

from desktop.rail_line_store import DiskRailLineLibrary
from desktop.tests.test_line_membership import aliases, edge, install, seq
from desktop.station_positions import route_positions


def centered_fixture(tmp_path):
    tracks=[edge('NE-platform',1,2,'站内股道'),edge('NE-main',1,3,'正线')]
    tracks[0]['coordinates']=[[118,32],[118.01,32]]
    tracks[1]['coordinates']=[[118,32],[117.99,32]]
    tracks[0]['way_tags']['service']='siding'
    lib=DiskRailLineLibrary(install(tmp_path,tracks))
    aliases(lib.path,[('node/a','始发站',100,1,1,'source',1,118,32)])
    platform={'type':'Feature','properties':{'associated_station_ids':[100],'boundary_kind':'platform'},
              'geometry':{'type':'Polygon','coordinates':[[[118.004,31.9999],[118.006,31.9999],[118.006,32.0001],[118.004,32.0001],[118.004,31.9999]]]}}
    with sqlite3.connect(tmp_path/'rail.sqlite') as db:
        db.execute('ALTER TABLE features ADD COLUMN id INTEGER')
        db.execute('CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy)')
        db.execute("INSERT INTO features VALUES('railPlatforms',?,1)",(json.dumps(platform),))
        db.execute('INSERT INTO bounds VALUES(1,118.004,118.006,31.9999,32.0001)')
    return lib, tracks


def test_reference_terminal_covers_station_track_middle_in_both_directions(tmp_path):
    lib, tracks=centered_fixture(tmp_path)
    line=lib.search_lines('正线')[0]['id']
    for start,end in [('station:node/a',3),(3,'station:node/a')]:
        normalized,path=lib.resolve_with_sequence(seq(start,line,end),'auto')
        assert {v['edge_id'] for v in path}=={'NE-platform','NE-main'}
        assert lib.resolve(normalized,'auto')==path
        positions=route_positions(lib,path,['station:node/a'])
        assert len(positions)==1
        assert positions[0]['coordinate']==pytest.approx([118.005,32])
        assert positions[0]['edge_id']=='NE-platform'
        assert positions[0]['source']=='real_platform_extent'


def test_stop_edge_offset_drives_desktop_and_domain_interpolation(tmp_path):
    from desktop.rail import compile_rail_plan
    from desktop.domain_adapter import build_repository
    from railscope.services.timetable import effective_run,route_distances
    from desktop.tests.test_full_corridor import fixture
    from desktop.station_positions import STOP_POSITION_KEY
    payload,edges=fixture()
    position={'edge_id':'e1','offset_m':100.,'source':'real_platform_extent'}
    payload['trains'][0]['stops'][0]['extensions']={STOP_POSITION_KEY:position}
    plan,_=compile_rail_plan(payload,edges,[])
    assert plan.train('direct')['stops'][0]['distance_m']==100.
    repo,bindings=build_repository({'edges':edges,'points':[]},payload,tmp_path/'identity.sqlite')
    run=bindings['train_runs']['direct']
    assert route_distances(repo,effective_run(repo,'',run))[0].scheduled_distance_m==100.
    from railscope.services.timetable.canonical import verify_corridor, match_corridors
    corridor = repo.train_runs[run].corridor_id
    assert match_corridors(repo, run)['candidates'] == [corridor]
    assert verify_corridor(repo, run, corridor).corridor_id == corridor
    position['offset_m']=1e9
    with pytest.raises(ValueError,match='偏移'):
        compile_rail_plan(payload,edges,[])
