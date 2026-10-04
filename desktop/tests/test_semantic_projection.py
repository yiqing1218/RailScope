"""Filter projections must agree with the persisted canonical contract."""
from copy import deepcopy

import pytest

from railscope.rail_semantics import edge_semantics
from desktop.rail_semantics import semantic_record


@pytest.mark.parametrize('raw', [
    {}, {'construction': False}, {'construction': True},
    {'track_type': '高速铁路线'}, {'railway_type': '普速铁路线'},
    {'way_tags': {'railway': 'rail', 'usage': 'main', 'highspeed': 'yes'}},
    {'tags': {'railway': 'construction', 'service': 'crossover'}},
    {'source_tags': {'railway': 'rail', 'service': 'yard'}},
    {'railway': 'rail', 'disused': 'yes'},
    {'track_role': 'arrival_departure_track', 'facility_id': 'ST-a',
     'yard_id': 'Y-a', 'zone_id': 'Z-a', 'confidence': .8,
     'provenance': {'track_role': {'value': 'arrival_departure_track',
                                 'verification_status': 'user_verified', 'confidence': .9},
                    'membership': {'sources': ['S1'], 'confidence': .3}}},
])
@pytest.mark.parametrize('override', [None,
    {'track_type': '渡线 / 道岔连接轨'},
    {'rail_semantics': {'track_role': 'main_track', 'facility_id': 'ST-a', 'confidence': 1}},
])
def test_projection_matches_all_values_and_leaves_source_untouched(raw, override):
    original = deepcopy((raw, override))
    for reader, args in ((edge_semantics, (raw,)), (semantic_record, (raw, override))):
        full = reader(*args)
        projected = reader(*args, include_provenance=False)
        assert projected == {k: v for k, v in full.items() if k != 'provenance'}
    assert (raw, override) == original


@pytest.mark.parametrize('raw,override', [
    ({'track_role': 'throat'}, None),
    ({'confidence': float('nan')}, None),
    ({'provenance': {'track_role': 'bad'}}, None),
    ({'track_role': 'main_track', 'provenance': {'track_role': {'value': 'crossover'}}}, None),
    ({}, {'rail_semantics': {'yard_id': 'osm-way-1'}}),
    ({}, {'rail_semantics': {'confidence': 2}}),
])
def test_projection_does_not_bypass_fact_or_override_validation(raw, override):
    for include in (True, False):
        with pytest.raises(ValueError):
            semantic_record(raw, override, include_provenance=include)


def test_full_evidence_is_detached_even_when_canonical_value_is_saved():
    raw = {'track_role': 'main_track', 'facility_id': 'ST-a', 'provenance': {
        'track_role': {'value': 'main_track', 'sources': ['S1']},
        'facility_id': {'value': 'ST-a', 'sources': ['S2']}}}
    full = semantic_record(raw)
    full['provenance']['track_role']['sources'].append('new')
    full['provenance']['facility_id']['sources'].append('new')
    assert raw['provenance']['track_role']['sources'] == ['S1']
    assert raw['provenance']['facility_id']['sources'] == ['S2']
