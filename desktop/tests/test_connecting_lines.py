from copy import deepcopy
import pytest

from desktop.connecting_lines import connecting_line_override, CONNECTING_LINE_COLOR
from desktop.display_names import apply_names, apply_rail_presentation
from desktop.rail_semantics import semantic_record
from desktop.rail_style_ui import defaults
from desktop.rail_style_resolver import STYLE_SELECTIONS, line_selection


@pytest.mark.parametrize('name', ['甲乙联络线', '甲乙连络线'])
def test_old_branch_review_and_renamed_object_agree_with_map_and_details(name):
    raw = {'name': '旧支线', 'line_name': '旧支线', 'network_edge_id': 'NE-one',
           'catalog_group_id': 'RL-one', 'track_role': 'main_track',
           'way_tags': {'railway': 'rail', 'usage': 'branch', 'highspeed': 'no'}}
    edit = {'display_name': name, 'line_name': name, 'rail_semantics': {
        'line_role': 'branch_line', 'source': 'rail_line_review', 'scope': 'line_group'}}
    original = deepcopy((raw, edit))
    delta = connecting_line_override(raw, edit, 'snapshot-one')
    corrected = {**edit, **delta}
    feature = {'properties': deepcopy(raw), 'geometry': {'type': 'LineString', 'coordinates': [[120,30],[121,30]]}}
    collection = {'features': [feature]}
    apply_names(collection, {'RL-one': corrected})
    apply_rail_presentation(collection, {}, {'RL-one': corrected})
    details = semantic_record(raw, corrected)
    assert line_selection(details)[2] == line_selection(feature['properties'])[2] == 'connecting_line'
    assert feature['properties']['rail_display_color'] == CONNECTING_LINE_COLOR
    assert details['provenance']['line_role']['source'] == 'user_name_classification'
    assert semantic_record(raw, edit, include_provenance=False)['line_role'] == 'connecting_line'
    assert (raw, edit) == original


def test_name_rule_preserves_real_yard_roles_lifecycle_and_unrelated_lines():
    raw = {'name': '甲联络线', 'network_edge_id': 'NE-one',
           'way_tags': {'railway': 'construction', 'construction': 'rail', 'service': 'crossover'}}
    edit = {'rail_semantics': {'source': 'rail_line_review', 'scope': 'line_group',
            'track_role': 'unknown', 'construction_status': 'unknown', 'line_role': 'branch_line'}}
    facts = semantic_record(raw, {**edit, **connecting_line_override(raw, edit, 'v1')})
    assert facts['track_role'] == 'crossover' and facts['construction_status'] == 'construction'
    assert connecting_line_override({'name': '甲支线'}, {}, 'v1') == {}
    assert connecting_line_override(raw, {'display_name': '新支线名称'}, 'v1') == {}
    assert semantic_record(raw, {'rail_semantics': {'line_role': 'dedicated_line',
        'source': 'workspace_override'}})['line_role'] == 'dedicated_line'


def test_all_connecting_line_styles_are_green_including_high_speed():
    styles = defaults()
    for key, (group, category, function, band) in STYLE_SELECTIONS.items():
        if group == 'track' and function == 'connecting_line':
            assert styles[key]['color'] == CONNECTING_LINE_COLOR


@pytest.mark.parametrize('delta', [
    {'rail_semantics': {'line_role': 'connecting_line', 'construction_status': 'construction'}},
    {'rail_semantics': {'line_role': 'connecting_line', 'track_role': 'crossover'}},
    {'rail_semantics': {'line_role': 'connecting_line', 'facility_id': 'ST-new'}},
    {'rail_semantics': {'line_role': 'connecting_line'}, 'station_source': 'node/2'},
    {'track_type': '车辆段 / 检修线'},
])
def test_structural_or_lifecycle_edits_keep_dependency_refresh(delta):
    from desktop.entity_refresh import line_classification_change
    before = {'rail_semantics': {'line_role': 'branch_line'}}
    assert line_classification_change(before, {**before, **delta}) is False
