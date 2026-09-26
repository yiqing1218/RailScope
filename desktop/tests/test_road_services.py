import json
import sqlite3
from contextlib import closing

import osmium

from desktop.road_store import build_index, routes, viewport
from desktop.road_services import services, viewport as service_viewport


def test_service_assets_and_construction_are_independent(tmp_path, qtbot):
    pbf = tmp_path / 'services.osm.pbf'
    with osmium.SimpleWriter(str(pbf)) as writer:
        coords = [(120,31),(120.02,31),(120.02,31.02),(120,31.02),
                  (120.005,31.005),(120.01,31.005),(120.01,31.01),(120.005,31.01)]
        for i, xy in enumerate(coords, 1):
            writer.add_node(osmium.osm.mutable.Node(id=i, location=xy))
        writer.add_node(osmium.osm.mutable.Node(id=20, location=(120.008,31.008),
            tags={'highway':'services','name':'测试服务区'}))
        writer.add_node(osmium.osm.mutable.Node(id=21, location=(120.1,31.1),
            tags={'highway':'rest_area','name':'只有点的休息区'}))
        for ident, nodes, tags in [
            (30,[1,2],{'highway':'motorway','ref':'G2','name':'测试高速'}),
            (31,[2,3],{'highway':'construction','construction':'motorway','ref':'G2','name':'测试高速'}),
            (32,[1,2,3,4,1],{'highway':'services','name':'测试服务区'}),
            (33,[5,6,7,8,5],{'building':'yes'}),
            (34,[3,4],{'highway':'proposed','proposed':'motorway','ref':'G9'}),
        ]:
            writer.add_way(osmium.osm.mutable.Way(id=ident,nodes=nodes,tags=tags))
    database = tmp_path / 'roads.sqlite'
    assert build_index(pbf,database) == 2
    assert {r['key'] for r in routes(database)} == {'G/G2','G/G2/construction'}
    for zoom in (4,15):
        data = viewport(database,[119,30,122,32],zoom=zoom)
        assert {f['properties']['construction'] for f in data['features']} == {False,True}
        assert {f['properties']['name'] for f in data['features']} == {'测试高速'}
    entries = services(database)
    assert len(entries) == 2  # The node inside the source footprint is one display entity.
    service = next(r for r in entries if r['name']=='测试服务区')
    data = service_viewport(database,[119,30,122,32],[service['id']],15)
    assert {f['properties']['asset_kind'] for f in data['features']} == {'poi','outline','building'}
    assert {f['properties']['service_id'] for f in data['features']} == {service['id']}
    poi = next(f for f in data['features'] if f['properties']['asset_kind']=='poi')
    assert poi['properties']['source_pois'][0]['osm_node_id'] == '20'
    assert {f['properties']['asset_kind'] for f in service_viewport(database,[119,30,122,32],None,5)['features']} == {'poi'}
    assert service_viewport(database,[119,30,122,32],[],15)['features'] == []
    lone = next(r for r in entries if r['name']=='只有点的休息区')
    assert len(service_viewport(database,[119,30,122,32],[lone['id']],15)['features']) == 1

    from desktop.road_catalog_ui import RoadCatalog
    from desktop.tests.test_operating_ui import MapStub
    widget = RoadCatalog(database,MapStub())
    qtbot.addWidget(widget)
    widget.set_all(True,construction=True)
    assert widget.visible_routes == {'G/G2/construction'}
    widget.set_all(True,construction=False)
    widget.set_all(False,construction=False)
    assert widget.visible_routes == {'G/G2/construction'}
    widget.set_services_all(True)
    assert widget.visible_services == {r['id'] for r in entries}
    widget.set_services_all(False)
    assert not widget.visible_services and widget.visible_routes
    with closing(sqlite3.connect(database)) as db:
        paths = [json.loads(r[0]) for r in db.execute("SELECT path FROM service_directory WHERE kind='object'")]
        assert all(len(p)==3 and p[0]=='浙江省' for p in paths)
    # Re-importing the same source keeps display identities stable.
    build_index(pbf,database)
    assert {r['id'] for r in services(database)} == {r['id'] for r in entries}
