import json
import sqlite3

from desktop.road_store import viewport


def roads(tmp_path):
    path = tmp_path/'roads.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE features(id INTEGER PRIMARY KEY,way_id INTEGER,data TEXT);'
                         'CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);'
                         'CREATE TABLE route_segments(route_key TEXT,feature_id INTEGER);')
        # Three connected source ways and one genuinely disconnected way.
        for ident, coords in enumerate([[[110,30],[111,30]],[[111,30],[112,30]],
                                        [[112,30],[113,30]],[[120,30],[121,30]]],1):
            feature = {'type':'Feature','properties':{'osm_way_id':ident,'route_keys':['G/G1'],
                       'name':'测试高速','road_class':'national'},'geometry':{'type':'LineString','coordinates':coords}}
            db.execute('INSERT INTO features VALUES(?,?,?)',(ident,ident,json.dumps(feature)))
            db.execute('INSERT INTO bounds VALUES(?,?,?,?,?)',(ident,coords[0][0],coords[-1][0],30,30))
            db.execute("INSERT INTO route_segments VALUES('G/G1',?)",(ident,))
    return path


def test_national_overview_keeps_all_segments_without_bridging_real_gaps(tmp_path):
    path = roads(tmp_path)
    before = path.read_bytes()
    result = viewport(path,[100,20,130,40],limit=1,zoom=4)
    assert result['truncated'] is False and result['generalized'] is True
    assert result['source_segment_count'] == 4
    lines = [line for feature in result['features'] for line in feature['geometry']['coordinates']]
    assert len(lines) == 2
    assert sorted((min(p[0] for p in line), max(p[0] for p in line)) for line in lines) == [(110,113),(120,121)]
    assert viewport(path,[100,20,130,40],route_keys=['G/G2'],zoom=4)['features'] == []
    assert path.read_bytes() == before
    assert viewport(path,[100,20,130,40],limit=1,zoom=15)['source_segment_count'] == 4


def test_detailed_local_view_keeps_original_way_geometry(tmp_path):
    path = roads(tmp_path)
    result = viewport(path,[109,29,110.5,31],zoom=15)
    assert not result.get('generalized')
    assert result['features'][0]['properties']['osm_way_id'] == 1
