import json
import sqlite3
from contextlib import closing

from university_store import build_index, viewport


def test_campus_directory_outline_and_poi_share_stable_id_on_reimport(tmp_path):
    source = tmp_path / "campus.osm"
    source.write_text('''<osm version="0.6">
      <node id="1" lon="121" lat="31"/>
      <node id="2" lon="121.02" lat="31"/>
      <node id="3" lon="121.02" lat="31.02"/>
      <node id="4" lon="121" lat="31.02"/>
      <node id="5" lon="121.01" lat="31.01"><tag k="amenity" v="university"/><tag k="name" v="测试大学"/></node>
      <node id="6" lon="122" lat="32"><tag k="amenity" v="college"/><tag k="name" v="测试学院"/></node>
      <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/>
        <tag k="amenity" v="university"/><tag k="name" v="测试大学"/>
        <tag k="addr:province" v="上海市"/><tag k="addr:city" v="上海市"/></way>
    </osm>''', encoding="utf-8")
    output, identities = tmp_path / "universities.sqlite", tmp_path / "identity.sqlite"
    assert build_index(source, output, identities) == 2
    with closing(sqlite3.connect(output)) as db:
        original = dict(db.execute("SELECT name,id FROM campuses"))
        assert db.execute("SELECT count(*) FROM raw_features").fetchone()[0] == 3
        assert db.execute("SELECT total FROM directory_nodes WHERE label='上海市' AND parent_id=''").fetchone()[0] == 1
    result = viewport(output, [120, 30, 123, 33], [original["测试大学"]])
    assert len(result["features"]) == 2
    assert {f["properties"]["campus_id"] for f in result["features"]} == {original["测试大学"]}
    assert {f["geometry"]["type"] for f in result["features"]} == {"MultiPolygon", "Point"}
    assert all(f["properties"]["snapshot"] for f in result["features"])
    assert len(viewport(output, [120, 30, 123, 33], [])['features']) == 0
    missing = viewport(output, [120, 30, 123, 33], [original["测试学院"]])
    assert len(missing["features"]) == 1
    assert missing["features"][0]["properties"]["boundary_status"] == "missing_source_outline"
    build_index(source, output, identities)
    with closing(sqlite3.connect(output)) as db:
        assert dict(db.execute("SELECT name,id FROM campuses")) == original


def test_override_changes_both_presentations_without_overwriting_source(tmp_path):
    from university_store import write_index
    records = [{"type":"Feature", "properties":{"source_id":"way/1","source_tags":{"name":"原大学","amenity":"university"}},
                "geometry":{"type":"Polygon","coordinates":[[[120,30],[121,30],[121,31],[120,31],[120,30]]]}}]
    output = tmp_path / "universities.sqlite"
    write_index(records, output, tmp_path / "identities.sqlite")
    with closing(sqlite3.connect(output)) as db:
        ident = db.execute("SELECT id FROM campuses").fetchone()[0]
    result = viewport(output,[119,29,122,32],overrides={ident:{"name":"新大学"}})
    assert all(f["properties"]["name"] == "新大学" for f in result["features"])
    assert any(f["properties"]["geometry_origin"] == "derived_label_anchor" for f in result["features"])
    with closing(sqlite3.connect(output)) as db:
        assert json.loads(db.execute("SELECT data FROM raw_features").fetchone()[0])["properties"]["source_tags"]["name"] == "原大学"


def test_native_directory_checkbox_controls_one_campus_and_rename_is_consistent(tmp_path, qtbot, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QInputDialog
    from university_store import database_path, write_index
    from university_ui import UniversityCatalog

    records = [{"type":"Feature", "properties":{"source_id":"node/1","source_tags":{"name":"测试学院","amenity":"college", "addr:province":"测试省", "addr:city":"测试市"}},
                "geometry":{"type":"Point","coordinates":[120,30]}}]
    write_index(records, database_path(tmp_path), tmp_path / 'data/user_settings/workspace.sqlite')
    calls=[]
    catalog=UniversityCatalog(tmp_path,SimpleNamespace(call=lambda *args:calls.append(args)))
    qtbot.addWidget(catalog)
    ident=next(iter(catalog.all_ids))
    index=catalog.model.index_for_key('campus:'+ident)
    assert index.isValid()
    catalog.model.setData(index,Qt.CheckState.Checked,Qt.ItemDataRole.CheckStateRole)
    assert catalog.visible == {ident}
    assert ('setUniversitySelection',[ident]) in calls
    monkeypatch.setattr(QInputDialog,'getText',lambda *args,**kwargs:('新学院',True))
    catalog.rename(ident)
    assert catalog.model.data(index) == '新学院'
    assert json.loads(catalog.override_path.read_text('utf-8'))[ident]['name'] == '新学院'


def test_viewport_budget_does_not_split_campus_outline_from_poi(tmp_path, monkeypatch):
    from university_store import write_index
    from viewport_settings import DEFAULT
    records = [{"type":"Feature", "properties":{"source_id":"way/1","source_tags":{"name":"测试大学","amenity":"university"}},
                "geometry":{"type":"Polygon","coordinates":[[[120,30],[121,30],[121,31],[120,31],[120,30]]]}}]
    output = tmp_path / "universities.sqlite"
    write_index(records, output, tmp_path / "identities.sqlite")
    monkeypatch.setitem(DEFAULT, 'vertices', 1)
    result = viewport(output, [119,29,122,32])
    assert result['features'] == []
    assert result['truncated'] is True
    assert result['budget']['reasons'] == ['vertices']


def test_directory_normalizes_source_provinces_and_rejects_postcodes():
    from university_store import AdministrativeLookup
    lookup = AdministrativeLookup(None)
    assert lookup.locate([121,31], {'addr:state':'Shanghai','addr:city':'Shanghai'}) == ('上海市','上海市')
    assert lookup.locate([113,23], {'addr:province':'廣東省','addr:city':'广州市'}) == ('广东省','广州市')
    assert lookup.locate([121,31], {'addr:province':'201314','addr:city':'12345'}) == ('未归属省份','未归属城市')


def test_missing_campus_override_is_preserved_and_reported_as_conflict(tmp_path, qtbot):
    from types import SimpleNamespace
    from university_ui import UniversityCatalog
    path = tmp_path / 'data/user_settings/universities.json'
    path.parent.mkdir(parents=True)
    edits = {'UNI-old': {'name': '人工校园名称'}}
    path.write_text(json.dumps(edits),encoding='utf-8')
    catalog=UniversityCatalog(tmp_path,SimpleNamespace(call=lambda *args:None))
    qtbot.addWidget(catalog)
    assert catalog.overrides == edits
    conflicts=json.loads(path.with_name('university_conflicts.json').read_text('utf-8'))
    assert conflicts[0]['campus_id']=='UNI-old'
    assert conflicts[0]['verification_status']=='unresolved'
    assert '重新关联' in catalog.summary.text()


def test_university_http_post_returns_linked_presentations_and_rejects_invalid_bounds(tmp_path, monkeypatch):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    import pytest
    import launcher
    from university_store import database_path, write_index
    records=[{'type':'Feature','properties':{'source_id':'way/1','source_tags':{'name':'测试大学','amenity':'university'}},
              'geometry':{'type':'Polygon','coordinates':[[[120,30],[121,30],[121,31],[120,31],[120,30]]]}}]
    path=database_path(tmp_path)
    write_index(records,path,tmp_path/'identities.sqlite')
    with closing(sqlite3.connect(path)) as db:
        ident=db.execute('SELECT id FROM campuses').fetchone()[0]
    monkeypatch.setattr(launcher,'ROOT',tmp_path)
    server=ThreadingHTTPServer(('127.0.0.1',0),launcher.LocalHandler)
    server.config={'universityOverrides':{ident:{'name':'人工大学'}}}
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    def request(bounds):
        return Request(f'http://127.0.0.1:{server.server_port}/api/universities',
            data=json.dumps({'bbox':bounds,'selected':json.dumps([ident]),'kind':'all','zoom':'14'}).encode(),
            headers={'Content-Type':'application/json'})
    try:
        with urlopen(request('119,29,122,32'),timeout=3) as response:
            result=json.load(response)
        assert len(result['features'])==2
        assert {f['properties']['name'] for f in result['features']}=={'人工大学'}
        with pytest.raises(HTTPError) as error:
            urlopen(request('nan,29,122,32'),timeout=3)
        assert error.value.code==400
    finally:
        server.shutdown();server.server_close();worker.join()
