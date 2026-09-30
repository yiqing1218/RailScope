from copy import deepcopy
from dataclasses import replace

import pytest

from railscope.demo import load_demo
from railscope.domain import DispatchEvent, DispatchScenario, StationRoute
from railscope.integrity import path_refs, validate_repository
from railscope.services.dispatch import add_event, recalculate
from railscope.services.timetable import effective_run


@pytest.mark.parametrize('problem', ['orphan_stop', 'station', 'sequence', 'times', 'origin', 'destination'])
def test_unresolved_runs_still_validate_timetable_references(problem):
    repo = load_demo()
    run = repo.train_runs['run-101']
    repo.train_runs[run.id] = replace(run, corridor_id=None, verification_status='unresolved')
    index = next(i for i, stop in enumerate(repo.stops) if stop.train_run_id == run.id)
    stop = repo.stops[index]
    if problem == 'orphan_stop':
        repo.stops.append(replace(stop, train_run_id='missing'))
    elif problem == 'station':
        repo.stops[index] = replace(stop, station_id='missing')
    elif problem == 'sequence':
        repo.stops[index] = replace(stop, sequence=2)
    elif problem == 'times':
        repo.stops[index] = replace(stop, departure_time_s=-1)
    else:
        field = problem + '_station_id'
        repo.train_runs[run.id] = replace(repo.train_runs[run.id], **{field: 'missing'})
    with pytest.raises(ValueError):
        validate_repository(repo)


def test_run_endpoints_must_match_first_and_last_stops():
    repo = load_demo()
    repo.train_runs['run-101'] = replace(repo.train_runs['run-101'], destination_station_id='station-b')
    with pytest.raises(ValueError, match='destination'):
        validate_repository(repo)


def test_block_membership_cannot_reference_deleted_edge():
    repo = load_demo()
    repo.block_edges[0] = replace(repo.block_edges[0], edge_id='missing')
    with pytest.raises(ValueError, match='block'):
        validate_repository(repo)


def test_hold_moves_departure_but_keeps_actual_arrival():
    repo = load_demo()
    before = repo.stops_for('run-101')
    add_event(repo, DispatchEvent('hold', 'base-2026-09-15', 'run-101', 'hold_train',
                               new_value=('station-b', 300)))
    after = effective_run(repo, 'base-2026-09-15', 'run-101').stops
    assert after[1].arrival_time_s == before[1].arrival_time_s
    assert after[1].departure_time_s == before[1].departure_time_s + 300
    assert after[2].arrival_time_s == before[2].arrival_time_s + 300
    assert repo.stops_for('run-101') == before


def test_scenario_only_occupies_its_service_date():
    repo = load_demo()
    repo.scenarios['next-day'] = DispatchScenario('next-day', 'Next day', '2026-09-16')
    recalculate(repo, 'next-day')
    assert not [o for o in repo.occupancies if o.scenario_id == 'next-day']
    assert not [c for c in repo.conflicts if c.scenario_id == 'next-day']
    assert [o for o in repo.occupancies if o.scenario_id == 'base-2026-09-15']


def test_failed_recalculate_does_not_publish_half_of_derived_state(monkeypatch):
    repo = load_demo()
    original = deepcopy((repo.occupancies, repo.conflicts))
    repo.events.append(DispatchEvent('cancel', 'base-2026-09-15', 'run-102', 'cancel_train', new_value=True))

    def fail(*_):
        raise ValueError('conflict calculation failed')

    monkeypatch.setattr('railscope.services.dispatch.detect_conflicts', fail)
    with pytest.raises(ValueError, match='failed'):
        recalculate(repo, 'base-2026-09-15')
    assert (repo.occupancies, repo.conflicts) == original


def test_block_direction_uses_block_edge_orientation():
    repo = load_demo()
    member = repo.block_edges[0]
    repo.block_edges[0] = replace(member, forward=False)
    recalculate(repo, 'base-2026-09-15')
    occupied = [o for o in repo.occupancies if o.train_run_id == 'run-101' and o.resource_id == member.block_id]
    assert occupied and all(o.direction == 'reverse' for o in occupied)


def test_unverified_station_route_cannot_be_a_formal_stop_reference():
    repo = load_demo()
    repo.station_routes['route-b'] = StationRoute('route-b', 'station-b',
        path_refs(repo, [('edge-bc', True)]), 'node-b', 'node-c')
    i = next(i for i, s in enumerate(repo.stops) if s.train_run_id == 'run-101' and s.station_id == 'station-b')
    repo.stops[i] = replace(repo.stops[i], station_route_id='route-b')
    with pytest.raises(ValueError, match='unverified'):
        validate_repository(repo)


def test_unknown_direction_cannot_enter_a_corridor():
    repo = load_demo()
    repo.edges['edge-ab'] = replace(repo.edges['edge-ab'], direction='unknown')
    with pytest.raises(ValueError, match='方向'):
        path_refs(repo, [('edge-ab', True)])
