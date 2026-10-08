import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

from airport_store import build_index, database_path, viewport, write_index


def sources():
    tags = {'name': '测试机场', 'aeroway': 'aerodrome', 'iata': 'TST', 'icao': 'ZTST',
            'addr:province': '上海市', 'addr:city': '上海市'}
    return [
        {'type': 'Feature', 'properties': {'source_id': 'way/10', 'source_tags': tags},
         'geometry': {'type': 'Polygon', 'coordinates': [[[121,31],[121.02,31],[121.02,31.02],[121,31.02],[121,31]]]}},
        {'type': 'Feature', 'properties': {'source_id': 'node/5', 'source_tags': tags},
         'geometry': {'type': 'Point', 'coordinates': [121.01,31.01]}},
        {'type': 'Feature', 'properties': {'source_id': 'node/6', 'source_tags': {'name': '只有点位的机场', 'aeroway': 'aerodrome'}},
         'geometry': {'type': 'Point', 'coordinates': [122,32]}},
    ]


def test_airport_outline_poi_directory_identity_and_raw_source_survive_reimport(tmp_path):
    output, identities = database_path(tmp_path), tmp_path/'identities.sqlite'
    assert write_index(sources(), output, identities, snapshot='fixture-v1') == 2
    with closing(sqlite3.connect(output)) as db:
        ids = dict(db.execute('SELECT name,id FROM airports'))
        assert db.execute('SELECT count(*) FROM raw_features').fetchone()[0] == 3
        assert db.execute("SELECT total FROM directory_nodes WHERE label='上海市' AND parent_id=''").fetchone()[0] == 1
    ident = ids['测试机场']
    assert ident.startswith('APT')
    pair = viewport(output, [120,30,123,33], [ident], overrides={ident: {'name': '人工机场名'}})['features']
    assert {f['geometry']['type'] for f in pair} == {'Point', 'Polygon'}
    assert {f['properties']['airport_id'] for f in pair} == {ident}
    assert {f['properties']['name'] for f in pair} == {'人工机场名'}
    assert all(f['properties']['iata'] == 'TST' and f['properties']['icao'] == 'ZTST' for f in pair)
    assert all(f['properties']['snapshot'] == 'fixture-v1' for f in pair)
    missing = viewport(output, [120,30,123,33], [ids['只有点位的机场']])['features']
    assert len(missing) == 1 and missing[0]['properties']['boundary_status'] == 'missing_source_outline'
    assert not viewport(output, [120,30,123,33], [])['features']
    assert len(viewport(output, [120,30,123,33], [ident], kind='poi')['features']) == 1
    assert len(viewport(output, [120,30,123,33], [ident], kind='outline')['features']) == 1
    write_index(sources(), output, identities, snapshot='fixture-v2')
    with closing(sqlite3.connect(output)) as db:
        assert dict(db.execute('SELECT name,id FROM airports')) == ids
        assert json.loads(db.execute("SELECT data FROM raw_features WHERE source_id='way/10'").fetchone()[0])['properties']['source_tags']['name'] == '测试机场'


def test_pbf_import_extracts_aerodromes_and_excludes_runways_and_helipads(tmp_path):
    source = tmp_path/'airports.osm'
    source.write_text('''<osm version="0.6">
      <node id="1" lon="121" lat="31"><tag k="aeroway" v="aerodrome"/><tag k="name" v="源机场"/></node>
      <node id="2" lon="122" lat="32"><tag k="aeroway" v="helipad"/><tag k="name" v="直升机坪"/></node>
      <node id="3" lon="123" lat="33"><tag k="amenity" v="university"/><tag k="name" v="大学"/></node>
    </osm>''', encoding='utf-8')
    output = database_path(tmp_path)
    assert build_index(source, output, tmp_path/'identity.sqlite') == 1
    result = viewport(output, [120,30,124,34])
    assert [f['properties']['name'] for f in result['features']] == ['源机场']


def test_airport_budget_never_splits_paired_features_and_custom_limits_apply(tmp_path):
    from viewport_settings import DEFAULT
    output = database_path(tmp_path)
    write_index(sources(), output, tmp_path/'identity.sqlite')
    one = viewport(output, [120,30,121.1,31.1], limits={**DEFAULT, 'features': 1})
    assert one['features'] == [] and one['truncated']
    assert one['budget']['limits']['features'] == 1
    two = viewport(output, [120,30,121.1,31.1], limits={**DEFAULT, 'features': 2})
    assert len(two['features']) == 2


def test_airport_codes_from_linked_poi_are_not_lost_or_guessed_on_conflict(tmp_path):
    records = sources()[:2]
    records[0]['properties']['source_tags'] = {'name':'测试机场', 'aeroway':'aerodrome'}
    output = database_path(tmp_path)
    write_index(records, output, tmp_path/'identity.sqlite')
    assert all(f['properties']['iata'] == 'TST' for f in viewport(output, [120,30,123,33])['features'])
    records[0]['properties']['source_tags']['iata'] = 'OTHER'
    write_index(records, output, tmp_path/'identity.sqlite')
    assert all('iata' not in f['properties'] for f in viewport(output, [120,30,123,33])['features'])


def test_airport_directory_checkbox_and_rename_control_both_presentations(tmp_path, qtbot, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QInputDialog
    from airport_ui import AirportCatalog
    write_index(sources(), database_path(tmp_path), tmp_path/'identity.sqlite')
    calls = []
    catalog = AirportCatalog(tmp_path, SimpleNamespace(call=lambda *args: calls.append(args)))
    qtbot.addWidget(catalog)
    with closing(sqlite3.connect(catalog.path)) as db:
        ident = db.execute("SELECT id FROM airports WHERE name='测试机场'").fetchone()[0]
    index = catalog.model.index_for_key('airport:'+ident)
    catalog.model.setData(index, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
    assert catalog.visible == {ident} and ('setAirportSelection',[ident]) in calls
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('新机场',True))
    catalog.rename(ident)
    assert catalog.model.data(index) == '新机场'
    assert catalog.override_path.name == 'airports.json'
    catalog.set_all(False)
    assert not catalog.visible and ('setAirportSelection',[]) in calls


def test_airport_http_post_validates_bounds_and_applies_overrides(tmp_path, monkeypatch):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    import pytest
    import launcher
    write_index(sources(), database_path(tmp_path), tmp_path/'identity.sqlite')
    with closing(sqlite3.connect(database_path(tmp_path))) as db:
        ident = db.execute("SELECT id FROM airports WHERE name='测试机场'").fetchone()[0]
    monkeypatch.setattr(launcher, 'ROOT', tmp_path)
    server = ThreadingHTTPServer(('127.0.0.1',0), launcher.LocalHandler)
    server.config = {'airportOverrides': {ident: {'name': 'API机场'}}}
    worker = Thread(target=server.serve_forever, daemon=True);worker.start()
    def request(bounds):
        return Request(f'http://127.0.0.1:{server.server_port}/api/airports',
                       data=json.dumps({'bbox': bounds, 'selected': json.dumps([ident]), 'zoom': '14'}).encode(),
                       headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request('120,30,123,33'), timeout=3) as response:
            result = json.load(response)
        assert len(result['features']) == 2
        assert {f['properties']['name'] for f in result['features']} == {'API机场'}
        with pytest.raises(HTTPError) as error:
            urlopen(request('nan,30,123,33'), timeout=3)
        assert error.value.code == 400
    finally:
        server.shutdown();server.server_close();worker.join()
