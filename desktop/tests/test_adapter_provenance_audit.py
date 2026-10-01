"""Reading a saved DTO must not turn inferred/reference data into verified data."""
import pytest

from desktop.domain_adapter import build_repository


@pytest.mark.parametrize('explicit', [False, True])
def test_saved_path_and_timetable_keep_actual_verification_status(tmp_path, explicit):
    graph = {'points': [], 'edges': [{
        'id': 'e1', 'from_node': 1, 'to_node': 2,
        'coordinates': [[120, 30], [120.01, 30]], 'node_ids': [1, 2],
        'construction': False, 'way_tags': {'railway': 'rail', 'usage': 'main'},
    }]}
    payload = {'schema': 'railscope.rail-plan.v2', 'service_date': '2026-09-30',
        'timezone': 'Asia/Shanghai', 'source': 'synthetic audit',
        'required_capabilities': [], 'extensions': {}, 'station_routes': [],
        'routes': [{'id': 'route-1', 'path': [{'edge_id': 'e1', 'direction': 'forward'}],
                    'extensions': {}}],
        'trains': [{'id': 'G_TEST', 'route_id': 'route-1',
                    'extensions': {}, 'stops': [
                        {'node_id': 1, 'arrival_s': None, 'departure_s': 25200},
                        {'node_id': 2, 'arrival_s': 25300, 'departure_s': None}]}]}
    if explicit:
        payload['routes'][0]['extensions']['railscope.org/line-resolution'] = {
            'verification_status': 'automatic_reference_not_dispatch_verified', 'confidence': 0.6}
        payload['trains'][0]['extensions'] = {'railscope.org/provenance': {
            'verification_status': 'user_verified', 'source_version': 'audit-fixture-v1'}}
    repo, _ = build_repository(graph, payload, tmp_path / 'workspace.sqlite')
    corridor = next(iter(repo.corridors.values()))
    run = next(iter(repo.train_runs.values()))
    assert corridor.verification_status == ('automatic_reference_not_dispatch_verified' if explicit else 'unverified')
    assert corridor.confidence == (0.6 if explicit else None)
    assert run.verification_status == ('user_verified' if explicit else 'unverified')
    assert run.source_version == ('audit-fixture-v1' if explicit else None)
