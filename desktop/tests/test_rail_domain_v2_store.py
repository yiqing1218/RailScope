"""Cross-layer V2 checks: source immutability, semantic index and replay."""
import json
import sqlite3

import pytest

from desktop.rail_line_store import DiskRailLineLibrary, build_line_index, fingerprint, index_ready
from desktop.rail_semantics import semantic_record
from desktop.rail_style_resolver import style_key
from desktop.rail_style_ui import defaults, validate_styles
from desktop.route_intent import promote_route, reresolve_route


def _edge(key, start, end, tags, **extra):
    return {'id': key, 'from_node': start, 'to_node': end,
            'node_ids': [start, end], 'coordinates': [[start, 0], [end, 0]],
            'line_id': 'IL-one', 'line_name': '某铁路', 'construction_status': 'operating',
            'way_tags': tags, **extra}


def _install(tmp_path, edges):
    source, target = tmp_path / 'rail.sqlite', tmp_path / 'rail_lines.sqlite'
    with sqlite3.connect(source) as db:
        db.executescript('CREATE TABLE edges(id TEXT PRIMARY KEY,data TEXT);'
                         'CREATE TABLE features(kind TEXT,data TEXT);')
        db.executemany('INSERT INTO edges VALUES(?,?)',
                       [(edge['id'], json.dumps(edge, ensure_ascii=False)) for edge in edges])
    before = source.read_bytes()
    build_line_index(source, target, [], [])
    assert source.read_bytes() == before
    assert index_ready(target, fingerprint(source, []))
    return source, target


def test_index_keeps_main_track_through_station_and_unknown_siding(tmp_path):
    edges = [_edge('NE-main-a', 1, 2, {'railway':'rail','usage':'main','highspeed':'no'}),
             _edge('NE-main-b', 2, 3, {'railway':'rail','usage':'main','highspeed':'no'}, facility_id='ST-one'),
             _edge('NE-siding', 2, 4, {'railway':'rail','service':'siding','highspeed':'no'}, facility_id='ST-one')]
    _, target = _install(tmp_path, edges)
    library = DiskRailLineLibrary(target)
    assert library.edges['NE-main-b']['track_role'] == 'main_track'
    assert library.edges['NE-main-b']['facility_id'] == 'ST-one'
    assert library.edges['NE-siding']['track_role'] == 'unknown'
    assert library.edges['NE-siding']['facility_only'] is True
    with sqlite3.connect(target) as db:
        assert db.execute('SELECT count(*) FROM edge_semantics').fetchone() == (3,)
        assert db.execute('SELECT count(*) FROM node_bounds').fetchone()[0] == 4
        assert json.loads(db.execute('SELECT value FROM metadata WHERE key="source"').fetchone()[0])[0] == 17
    assert not hasattr(library.edges['NE-main-b'], 'coordinates')


def test_failed_rebuild_leaves_previous_index_and_stable_ids(tmp_path):
    edges = [_edge('NE-one', 1, 2, {'railway':'rail','usage':'main'})]
    source, target = _install(tmp_path, edges)
    original = target.read_bytes()
    with sqlite3.connect(source) as db:
        db.execute('INSERT INTO edges VALUES(?,?)', ('NE-bad', '{bad-json'))
    with pytest.raises(json.JSONDecodeError):
        build_line_index(source, target, [], [])
    assert target.read_bytes() == original
    assert DiskRailLineLibrary(target).edges['NE-one']['id'] == 'NE-one'


def test_workspace_status_override_rejects_nonoperating_route(tmp_path):
    _, target = _install(tmp_path, [_edge('NE-one', 1, 2, {'railway':'rail','usage':'main'})])
    base = DiskRailLineLibrary(target)
    sequence = [{'kind':'endpoint','node_id':1}, {'kind':'line','line_id':'IL-one'},
                {'kind':'endpoint','node_id':2}]
    assert base.resolve(sequence) == [{'edge_id':'NE-one','direction':'forward'}]
    overridden = DiskRailLineLibrary(target, metadata={'object:network_edge_id:NE-one': {
        'rail_semantics': {'construction_status':'planned','verification_status':'user_verified'}}})
    with pytest.raises(ValueError):
        overridden.resolve(sequence)
    with pytest.raises(ValueError):
        semantic_record({'way_tags':{}}, {'rail_semantics':{'track_role':'throat'}})


def test_v2_route_intent_roundtrip_and_explicit_reresolution(tmp_path):
    _source, target = _install(tmp_path, [_edge('NE-one', 1, 2, {'railway':'rail','usage':'main'})])
    route = {'id':'COR-one', 'name':'测试通道',
             'sequence':[{'kind':'endpoint','node_id':1},{'kind':'line','line_id':'IL-one'},
                         {'kind':'endpoint','node_id':2}],
             'path':[{'edge_id':'NE-one','direction':'forward'}],
             'extensions':{'railscope.org/line-resolution':{'policy':'auto'}}}
    original = promote_route(route)
    assert promote_route(original) == original
    original['route_intent']['id'] = 'RI-user-stable'
    new = reresolve_route(original, DiskRailLineLibrary(target), 'snapshot-next')
    assert new['route_intent']['id'] == 'RI-user-stable'
    assert new['path'] == route['path']
    assert new['resolved_corridor']['snapshot_id'] == 'snapshot-next'
    assert original['path'] == route['path']


def test_old_style_values_migrate_to_domain_keys():
    old = {key: value for key, value in defaults().items() if not key.startswith(('class.','role.','line.'))}
    old['高速铁路线'] = {'color':'#123456','width':3.25,'pattern':'solid'}
    new = validate_styles(old)
    assert new['class.high_speed'] == old['高速铁路线']
    assert style_key({'railway_class':'high_speed','track_role':'main_track'}) == 'class.high_speed'
    assert style_key({'railway_class':'high_speed','track_role':'arrival_departure_track'}) == 'class.high_speed.station'
