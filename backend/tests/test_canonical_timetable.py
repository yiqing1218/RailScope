from dataclasses import replace

import pytest

from railscope.domain import Corridor, NetworkEdge, NetworkNode, RoutePathEdge, Station
from railscope.repository import RailRepository
from railscope.services.simulation import TimetableLinearInterpolationModel
from railscope.services.simulation.geometry import route_coordinate
from railscope.services.timetable import effective_run, route_distances
from railscope.services.timetable.canonical import (
    from_12306, from_gtfs, import_timetables, match_corridors, station_distances, verify_corridor,
)


def network():
    repo = RailRepository()
    for key, point in [('a', (0, 0)), ('b', (1, 1)), ('c', (3, 1))]:
        repo.nodes[key] = NetworkNode(key, *point)
        repo.stations[key] = Station(key, key.upper(), *point, key, code=key.upper())
    repo.edges['ab'] = NetworkEdge('ab', 'a', 'b', ((0, 0), (0, 1), (1, 1)), 200)
    repo.edges['bc'] = NetworkEdge('bc', 'b', 'c', ((1, 1), (3, 1)), 800)
    refs = (RoutePathEdge('ab', 1, True, 0, 200), RoutePathEdge('bc', 2, True, 200, 1000))
    repo.corridors['cor'] = Corridor('cor', 'full corridor', refs, 'a', 'c')
    return repo


def schedule(date='2026-09-18', number='internal-1', middle=True):
    rows = [dict(station_no=1, station_telecode='A', arrive_time='----', start_time='23:50')]
    if middle:
        rows.append(dict(station_no=2, station_telecode='B', arrive_time='00:10', start_time='00:12'))
    rows.append(dict(station_no=3, station_telecode='C', arrive_time='00:40', start_time='----'))
    return from_12306(rows, service_date=date, train_code='G1', internal_train_no=number,
                      source_id='12306', source_version='2026-09')


def test_dates_internal_numbers_midnight_and_provenance():
    repo = network()
    a, b, c = import_timetables(repo, [schedule(), schedule('2027-01-10'), schedule(number='internal-2')])
    assert len({a.id, b.id, c.id}) == 3
    assert a.service_id == b.service_id != c.service_id
    assert repo.stops_for(a.id)[1].arrival_time_s == 24 * 3600 + 10 * 60
    assert a.source_version == '2026-09' and a.internal_train_no == 'internal-1'
    assert not a.route_path_id and not a.corridor_id


def test_batch_is_atomic_on_duplicate_or_ambiguous_station():
    repo = network()
    with pytest.raises(ValueError, match='duplicate train'):
        import_timetables(repo, [schedule(), schedule()])
    assert not repo.train_runs and not repo.stops and not repo.train_services
    repo.stations['other'] = replace(repo.stations['b'], id='other')
    with pytest.raises(ValueError, match='unresolved station'):
        import_timetables(repo, [schedule()])
    assert not repo.train_runs
    run, = import_timetables(repo, [schedule()], {('12306', 'B'): 'b'})
    assert repo.stops_for(run.id)[1].station_id == 'b'


def test_gtfs_extended_hours_and_duplicate_sequences():
    trip = {'trip_id': 'trip-1', 'trip_short_name': 'G1'}
    rows = [dict(trip_id='trip-1', stop_sequence=i, stop_id=station,
                 arrival_time=time, departure_time=time)
            for i, station, time in [(1, 'A', '23:55:00'), (2, 'C', '25:10:00')]]
    kwargs = dict(service_date='2026-09-18', source_id='gtfs', source_version='v1')
    value = from_gtfs(trip, rows, **kwargs)
    assert value.stops[-1].arrival_s == 25 * 3600 + 600
    with pytest.raises(ValueError, match='duplicate GTFS'):
        from_gtfs(trip, rows + rows[:1], **kwargs)
    rows[-1]['arrival_time'] = '01:10:00'
    with pytest.raises(ValueError, match='non-monotonic'):
        from_gtfs(trip, rows, **kwargs)


def test_different_stop_patterns_share_full_corridor_and_need_verification():
    repo = network()
    local, direct = import_timetables(repo, [schedule(), schedule(number='express', middle=False)])
    engine = TimetableLinearInterpolationModel()
    for run in (local, direct):
        assert match_corridors(repo, run.id) == {'status': 'automatic_match', 'candidates': ['cor']}
        assert engine.position_at_time(repo, '', run.id, 24 * 3600)['state'] == 'unresolved'
        verify_corridor(repo, run.id, 'cor')
        assert engine.position_at_time(repo, '', run.id, 24 * 3600)['state'] == 'running'
    assert len(repo.corridors) == 1 and not repo.routes


def test_multiple_matches_stay_unresolved_and_wrong_stop_order_rejected():
    repo = network()
    repo.corridors['alternative'] = replace(repo.corridors['cor'], id='alternative')
    run, = import_timetables(repo, [schedule()])
    assert match_corridors(repo, run.id)['status'] == 'unresolved'
    assert repo.train_runs[run.id].corridor_id is None
    with pytest.raises(ValueError, match='out of order'):
        station_distances(repo, repo.corridors['cor'].edge_refs, ['a', 'c', 'b'])


def test_actual_station_mileage_and_bent_edge_geometry_are_used():
    repo = network()
    run, = import_timetables(repo, [schedule()])
    verify_corridor(repo, run.id, 'cor')
    stops = route_distances(repo, effective_run(repo, '', run.id))
    assert [s.scheduled_distance_m for s in stops] == [0, 200, 1000]
    point = TimetableLinearInterpolationModel().position_at_time(repo, '', run.id, 24 * 3600)
    assert point['route_distance_m'] == 100
    # Halfway along A->B lies at its bend, not the straight station midpoint.
    assert point['coordinate'][0] == pytest.approx(0, abs=.001)
    assert point['coordinate'][1] == pytest.approx(1, abs=.001)
    reverse = (RoutePathEdge('ab', 1, False, 0, 200),)
    assert route_coordinate(repo, reverse, 0) == [1, 1]


def test_construction_edges_are_excluded_even_after_previous_verification():
    repo = network()
    run, = import_timetables(repo, [schedule()])
    verify_corridor(repo, run.id, 'cor')
    repo.edges['bc'] = replace(repo.edges['bc'], construction_status='construction')
    assert TimetableLinearInterpolationModel().position_at_time(repo, '', run.id, 24 * 3600)['state'] == 'unresolved'
    assert match_corridors(repo, run.id)['candidates'] == []
    with pytest.raises(ValueError, match='non-operating'):
        verify_corridor(repo, run.id, 'cor')


def test_small_clock_reversal_is_an_error_not_an_inferred_day():
    with pytest.raises(ValueError, match='non-monotonic'):
        from_12306([{'station_no':1,'station_name':'A','start_time':'12:30'},
                    {'station_no':2,'station_name':'C','arrive_time':'12:20'}],
                   service_date='2026-09-18', train_code='G1', internal_train_no='x',
                   source_id='12306', source_version='v1')


def test_incompatible_direction_and_deleted_edge_invalidate_matching_and_motion():
    repo = network()
    run, = import_timetables(repo, [schedule()])
    repo.edges['ab'] = replace(repo.edges['ab'], direction='reverse')
    assert match_corridors(repo, run.id)['candidates'] == []
    with pytest.raises(ValueError, match='direction'):
        verify_corridor(repo, run.id, 'cor')
    repo.edges['ab'] = replace(repo.edges['ab'], direction='both')
    verify_corridor(repo, run.id, 'cor')
    del repo.edges['bc']
    assert TimetableLinearInterpolationModel().position_at_time(repo, '', run.id, 86400)['state'] == 'unresolved'
