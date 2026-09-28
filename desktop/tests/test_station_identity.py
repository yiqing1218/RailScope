import json
import sqlite3
from copy import deepcopy

import pytest

from desktop.rail_line_store import DiskRailLineLibrary, build_line_index, fingerprint
from desktop.rail_lines import line_identity
from desktop.rail_station_directory import apply_station_names, refresh_plan_names
from desktop.rail_station_import import station_choices, resolve_stops, STOP_NAME_KEY
from desktop.station_positions import STOP_POSITION_KEY


def station(node, name, x):
    return {'type': 'Feature', 'properties': {'osm_node_id': node, 'kind': 'station', 'name': name,
        'node_tags': {'train': 'yes', 'railway:station_category': 'freight', 'alt_name': name + '火车站'}},
        'geometry': {'type': 'Point', 'coordinates': [x, 32.0004]}}


def fixture(tmp_path, points=None):
    points = points or [station(100, '霍邱', 118.04)]
    edge = {'id': 'NE-long', 'from_node': 1, 'to_node': 2, 'node_ids': [1, 2],
        'construction_status': 'operating',
        'coordinates': [[118, 32], [118.1, 32]], 'way_tags': {'name': '阜六线'}}
    line = line_identity(edge)[0]
    track = {'type': 'Feature', 'properties': {'network_edge_id': edge['id']},
             'geometry': {'type': 'LineString', 'coordinates': edge['coordinates']}}
    source = tmp_path / 'rail.sqlite'
    with sqlite3.connect(source) as db:
        db.executescript('CREATE TABLE edges(id TEXT PRIMARY KEY,data TEXT);'
            'CREATE TABLE features(id INTEGER PRIMARY KEY,kind TEXT,data TEXT);'
            'CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy)')
        db.execute('INSERT INTO edges VALUES(?,?)', (edge['id'], json.dumps(edge)))
        db.execute("INSERT INTO features VALUES(1,'rail',?)", (json.dumps(track),))
        db.execute('INSERT INTO bounds VALUES(1,118,118.1,32,32)')
        db.executemany("INSERT INTO features(kind,data) VALUES('railPoints',?)", [(json.dumps(p),) for p in points])
    index = tmp_path / 'rail_lines.sqlite'
    build_line_index(source, index, [], [])
    return DiskRailLineLibrary(index), edge, line


def test_long_edge_freight_station_uses_geometry_and_map_name_imports(tmp_path):
    lib, edge, line = fixture(tmp_path)
    assert lib.search_endpoints('霍邱站', line_id=line)[0][0] == 'station:node/100'
    connection = lib.station_connection_override('station:node/100', [line])[0]
    assert connection['gap_m'] < 50
    assert connection['offset_m'] > 2500
    for start, end in [('station:node/100', 2), (2, 'station:node/100')]:
        sequence = [{'kind': 'endpoint', 'node_id': start}, {'kind': 'line', 'line_id': line}, {'kind': 'endpoint', 'node_id': end}]
        assert lib.resolve(sequence, 'auto')[0]['edge_id'] == edge['id']
    payload = {'routes': [{'id': 'R', 'path': [{'edge_id': edge['id'], 'direction': 'forward'}]}]}
    lib.metadata['station:node/100'] = {'display_name': '霍邱货运站'}
    collection = {'features': [station(100, '霍邱', 118.04)]}
    apply_station_names(collection, lib.station_directory, lib.metadata)
    assert collection['features'][0]['properties']['display_name'] == '霍邱货运站'
    from desktop.catalog_metadata import rail_station_records
    records, count = rail_station_records(tmp_path, [], '霍邱货运站', overrides=lib.metadata)
    assert count == 1 and records[0]['station_key'] == 'station:node/100'
    choices = station_choices(payload, [edge], library=lib)['R']
    for name in ('霍邱', '霍邱站', '霍邱火车站', '霍邱货运站'):
        stop = resolve_stops([(2, {'stop_name': name})], choices)[0]
        assert stop['extensions'][STOP_NAME_KEY]['display_name'] == '霍邱货运站'
        assert stop['extensions'][STOP_POSITION_KEY]['station_id'] == 'station:node/100'
    assert payload['routes'][0]['path'] == [{'edge_id': edge['id'], 'direction': 'forward'}]


def test_poisoned_area_alias_migration_and_cached_corridor_name(tmp_path):
    from desktop.rail_boundaries import associate
    airport = {'type': 'Feature', 'properties': {'infrastructure_id': 'way/500',
        'source_name': '虹桥2号航站楼站（市域铁）', 'boundary_kind': 'station_building', 'way_tags': {}},
        'geometry': {'type': 'Polygon', 'coordinates': [[[118.039,32],[118.041,32],[118.04,32.001],[118.039,32]]]}}
    associate([airport], [station(100, '上海虹桥', 118.04)])
    assert airport['properties']['associated_station_ids'] == []
    lib, edge, line = fixture(tmp_path, [station(100, '上海虹桥', 118.04)])
    with sqlite3.connect(lib.path) as db:
        db.execute("INSERT INTO station_aliases SELECT source_id,'虹桥2号航站楼站（市域铁）',station_node_id,anchor_node,distance_m,verification_status,confidence,source_x,source_y FROM station_aliases LIMIT 1")
        signature = json.loads(fingerprint(tmp_path/'rail.sqlite', []))
        signature[0] = 14
        db.execute("UPDATE metadata SET value=? WHERE key='source'", (json.dumps(signature),))
    build_line_index(tmp_path/'rail.sqlite', lib.path, [], [])
    lib = DiskRailLineLibrary(lib.path)
    assert lib.endpoint_label('station:node/100') == '上海虹桥站'
    assert lib.search_endpoints('虹桥2号') == []
    p = {'station_id': 'station:node/100', 'station_name': '虹桥2号航站楼站（市域铁）站', 'edge_id': edge['id'], 'offset_m': 100}
    payload = {'routes': [{'name': p['station_name']+' → 北京南站 · 单向通道',
                         'path': [edge['id']], 'extensions': {'railscope.org/station-track-positions': [p]}}]}
    before = deepcopy(payload)
    assert refresh_plan_names(payload, lib)
    assert payload['routes'][0]['name'] == '上海虹桥站 → 北京南站 · 单向通道'
    assert p['edge_id'] == edge['id'] and p['offset_m'] == 100
    assert payload['routes'][0]['path'] == before['routes'][0]['path']


def test_two_stations_share_track_anchor_but_not_domain_identity(tmp_path):
    from desktop.rail import compile_rail_plan
    from desktop.domain_adapter import build_repository
    from desktop.tests.test_full_corridor import fixture as plan_fixture
    lib, edge, _ = fixture(tmp_path, [station(100, '货运甲', 118.03), station(200, '货运乙', 118.04)])
    payload, _ = plan_fixture()
    payload['routes'] = [dict(payload['routes'][0], path=[{'edge_id': edge['id'], 'direction': 'forward'}])]
    payload['trains'] = [payload['trains'][0]]
    payload['trains'][0]['route_id'] = payload['routes'][0]['id']
    for direction, names in [('forward', ['货运甲站', '货运乙站']), ('reverse', ['货运乙站', '货运甲站'])]:
        payload['routes'][0]['path'][0]['direction'] = direction
        choices = station_choices(payload, [edge], library=lib)[payload['routes'][0]['id']]
        stops = resolve_stops([(i+2, {'stop_name': name}) for i,name in enumerate(names)], choices)
        assert stops[0]['node_id'] == stops[1]['node_id']
        payload['trains'][0]['stops'] = [dict(s, arrival_s=i*3600, departure_s=i*3600) for i,s in enumerate(stops)]
        plan, _ = compile_rail_plan(payload, [edge], [])
        assert plan.trains[0]['stops'][0]['distance_m'] < plan.trains[0]['stops'][1]['distance_m']
        repo, bindings = build_repository({'edges': [edge], 'points': []}, payload, tmp_path/'identities.sqlite')
        assert len(repo.stations) == 2
        assert len({s.station_id for s in repo.stops}) == 2


def test_corridor_connected_lines_outside_name_field(qtbot, tmp_path):
    from desktop.corridor_ui import CorridorSequenceTable
    lib, _, line = fixture(tmp_path)
    table = CorridorSequenceTable(lib)
    qtbot.addWidget(table)
    table.add_point('station:node/100', line)
    assert table.cellWidget(0, 0).currentText() == '霍邱站'
    assert table.cellWidget(0, 0).parent().hint.toolTip() == '接轨线路：阜六线'
    table.assign(0, 0, None, notify=False)
    assert '选择车站' in table.cellWidget(0, 0).parent().hint.toolTip()
