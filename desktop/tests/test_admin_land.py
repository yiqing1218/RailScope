import json
import sqlite3

from shapely.geometry import MultiPolygon, box, mapping, shape

from desktop.admin_land import ensure_clipped, clip_feature
from desktop.admin_store import viewport


def test_coastal_display_removes_sea_preserves_islands_and_source(tmp_path):
    land = MultiPolygon([box(0,0,1,2), box(1.5,1,1.7,1.2)])
    feature = {'type':'Feature','id':1,'properties':{'name':'测试沿海区','osm_relation_id':1},'geometry':mapping(box(0,0,2,2))}
    output = clip_feature(feature,land)
    assert shape(output['geometry']).equals(land)
    assert feature['geometry'] == mapping(box(0,0,2,2))
    database = tmp_path / 'admin.sqlite'
    raw = json.dumps(feature)
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE features(id INTEGER,level INTEGER,coarse TEXT,medium TEXT,detail TEXT)')
        for level in (4,5,6):
            db.execute('INSERT INTO features VALUES(?,?,?,?,?)',(level,level,raw,raw,raw))
    original = database.read_bytes()
    mask = tmp_path / 'natural_earth_10m_land.geojson'
    mask.write_text(json.dumps({'type':'FeatureCollection','features':[{'type':'Feature','geometry':mapping(land),'properties':{}}]}),encoding='utf-8')
    cache = ensure_clipped(database)
    first = cache.stat().st_mtime_ns
    for level in (4,5,6):
        for zoom in (4,8,12):
            data = viewport(database,[0,0,2,2],level,zoom)
            assert shape(data['features'][0]['geometry']).equals(land)
            assert data['features'][0]['properties']['display_geometry'] == 'land_only'
    assert ensure_clipped(database).stat().st_mtime_ns == first
    assert database.read_bytes() == original
