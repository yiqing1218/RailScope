from copy import deepcopy

import pytest

from desktop.rail import compile_rail_plan
from desktop.rail_station_import import resolve_stops, station_choices, STOP_NAME_KEY
from desktop.rail_tables import merge_csv, export_csv
from desktop.station_positions import POSITION_KEY, STOP_POSITION_KEY, position_distance


def choice(name, station, node, distance, canonical=None):
    return {'name': name, 'canonical_name': canonical or name, 'node_id': node, 'order': distance,
            'position': {'station_id': 'station:node/' + str(station), 'station_name': canonical or name,
                         'node_id': node, 'edge_id': 'e', 'offset_m': distance, 'distance_m': distance}}


def test_station_source_id_and_stale_node_map_to_current_physical_position():
    choices = [choice('巢湖站', 100, 11, 100), choice('巢湖站', 200, 22, 200, '巢湖西站')]
    for node in ('', '100', '11', '999'):
        stop = resolve_stops([(9, {'stop_name': '巢湖', 'node_id': node})], choices)[0]
        assert stop['node_id'] == 11
        assert stop['extensions'][STOP_POSITION_KEY]['station_id'] == 'station:node/100'
        assert stop['extensions'][STOP_NAME_KEY]['input_node_id'] == node


def test_whole_sequence_uses_following_station_to_resolve_same_name():
    choices = [choice('甲', 1, 1, 10), choice('同名', 2, 2, 20),
               choice('同名', 3, 3, 40), choice('乙', 4, 4, 30)]
    rows = [(i, {'stop_name': name}) for i, name in enumerate(('甲', '同名', '乙'), 2)]
    assert [s['node_id'] for s in resolve_stops(rows, choices)] == [1, 2, 4]


def test_csv_rolls_midnight_but_does_not_hide_small_time_errors():
    from desktop.tests.test_name_train_import import named_plan
    payload, _ = named_plan()
    text = '车次,站名,到达,出发\nK1,南京南,23:28,23:28\nK1,合肥南,0:04,0:08\n'
    stops = merge_csv(text, payload)['trains'][0]['stops']
    assert stops[1]['arrival_s'] == 24*3600+4*60
    assert stops[1]['departure_s'] == 24*3600+8*60
    assert stops[1]['extensions']['railscope.org/time-normalization']['arrival_day'] == 1
    explicit = merge_csv(text.replace('0:04', '24:04').replace('0:08', '24:08'), payload)['trains'][0]['stops']
    assert explicit[1]['arrival_s'] == stops[1]['arrival_s']
    wrong = merge_csv(text.replace('23:28', '10:00').replace('0:04', '9:04').replace('0:08', '9:08'), payload)
    assert wrong['trains'][0]['stops'][1]['arrival_s'] == 9*3600+4*60


def test_round_trip_same_station_and_edge_keep_occurrences_across_domain(tmp_path):
    from desktop.tests.test_station_identity import fixture, station
    from desktop.tests.test_full_corridor import fixture as plan_fixture
    from desktop.domain_adapter import build_repository
    from railscope.services.timetable.canonical import stop_distances, verify_corridor
    lib, edge, line = fixture(tmp_path, [station(100, '甲', 118.02), station(200, '乙', 118.08)])
    payload, _ = plan_fixture()
    payload['trains'] = []
    route = payload['routes'][0]
    route['path'] = [{'edge_id': edge['id'], 'direction': direction} for direction in ('forward', 'reverse')]
    sequence = [{'kind': 'endpoint', 'node_id': 1}, {'kind': 'line', 'line_id': line},
                {'kind': 'endpoint', 'node_id': 2}, {'kind': 'line', 'line_id': line},
                {'kind': 'endpoint', 'node_id': 1}]
    assert lib.resolve(sequence, 'auto') == route['path']
    assert lib.describe(route['path']) == sequence
    graph = lib.selected_library([line])
    assert graph.describe(route['path']) == sequence
    assert graph.validate_selected_path(sequence, route['path']) == route['path']
    from desktop.station_positions import route_positions
    positions = route_positions(lib, route['path'], ['station:node/100'])
    assert [p['path_index'] for p in positions] == [0, 1]
    choices = station_choices(payload, [edge], library=lib)
    # A saved first visit must not erase the second visit discovered on return.
    route['extensions'][POSITION_KEY] = [deepcopy(choices[route['id']][0]['position'])]
    choices = station_choices(payload, [edge], library=lib)
    text = '车次,站名,到达,出发\nK1,甲,23:00,23:01\nK1,乙,0:00,0:10\nK1,甲,1:00,1:00\n'
    updated = merge_csv(text, payload, choices)
    stops = updated['trains'][0]['stops']
    assert [s['extensions'][STOP_POSITION_KEY]['path_index'] for s in stops] == [0, 0, 1]
    plan, _ = compile_rail_plan(updated, [edge], [])
    repo, bindings = build_repository({'edges': [edge], 'points': []}, updated, tmp_path/'ids.sqlite')
    run = repo.train_runs[bindings['train_runs']['K1']]
    distances = stop_distances(repo, repo.corridors[run.corridor_id].edge_refs, repo.stops_for(run.id))
    assert distances == pytest.approx([s['distance_m'] for s in plan.trains[0]['stops']])
    assert distances[0] < distances[1] < distances[2]
    assert verify_corridor(repo, run.id, run.corridor_id).verification_status == 'user_verified'
    repo.stops = repo.stops[:2]
    with pytest.raises(ValueError, match='endpoints'):
        verify_corridor(repo, run.id, run.corridor_id)
    assert position_distance(route['path'], {edge['id']: edge}, stops[-1]['extensions'][STOP_POSITION_KEY]) == pytest.approx(distances[-1])
    assert updated['routes'][0]['path'] == payload['routes'][0]['path']
    restored = merge_csv(export_csv(updated), payload, choices)
    assert [s['extensions'][STOP_POSITION_KEY]['path_index'] for s in restored['trains'][0]['stops']] == [0, 0, 1]
    assert [s['arrival_s'] for s in restored['trains'][0]['stops']] == [s['arrival_s'] for s in stops]


def test_bad_occurrence_is_not_silently_moved_to_another_visit(tmp_path):
    from desktop.tests.test_station_identity import fixture
    lib, edge, _ = fixture(tmp_path)
    position = {'edge_id': edge['id'], 'offset_m': 100, 'path_index': 10}
    with pytest.raises(ValueError, match='序号'):
        position_distance([{'edge_id': edge['id'], 'direction': 'forward'}], {edge['id']: edge}, position)


def test_legacy_node_stops_can_return_to_the_same_station(tmp_path):
    from desktop.tests.test_station_identity import fixture
    from desktop.tests.test_full_corridor import fixture as plan_fixture
    _, edge, _ = fixture(tmp_path)
    payload, _ = plan_fixture()
    payload['trains'] = []
    route = payload['routes'][0]
    route['path'] = [{'edge_id': edge['id'], 'direction': direction} for direction in ('forward', 'reverse')]
    route['extensions'].pop(POSITION_KEY, None)
    payload['extensions']['railscope.org/assembly'] = {'stations': [
        {'name': '甲站', 'node_id': 1}, {'name': '乙站', 'node_id': 2}]}
    choices = station_choices(payload, [edge])
    assert len([c for c in choices[route['id']] if c['name'] == '甲站']) == 2
    text = '车次,站名,到达,出发\nK1,甲,08:00,08:00\nK1,乙,09:00,09:10\nK1,甲,10:00,10:00\n'
    updated = merge_csv(text, payload, choices)
    plan, _ = compile_rail_plan(updated, [edge], [])
    distances = [s['distance_m'] for s in plan.trains[0]['stops']]
    assert distances[0] < distances[1] < distances[2]
