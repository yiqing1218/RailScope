from copy import deepcopy

from desktop.display_names import apply_rail_presentation, rail_line_presentation
from desktop.rail_line_store import DiskRailLineLibrary
from desktop.rail_lines import line_identity
from desktop.tests.test_line_membership import edge, install


def test_partial_speed_tags_share_style_and_parent_without_changing_physical_ids(tmp_path):
    edges = [edge('first', 1, 2, '宁启线'), edge('second', 2, 3, '宁启线')]
    for item, speed in zip(edges, ('160', '200')):
        item['way_tags'].pop('highspeed')
        item['way_tags'].update(usage='main', maxspeed=speed)
    lib = DiskRailLineLibrary(install(tmp_path, edges))
    presentation = rail_line_presentation(lib)
    line = line_identity(edges[0])[0]
    features = [{'properties': {'line_id': line, 'network_edge_id': item['id'],
                               'section_id': 'RS-shared', 'infrastructure_id': 'way/' + str(i),
                               'track_type': kind, 'way_tags': item['way_tags']}}
                for i, (item, kind) in enumerate(zip(edges, ('普速铁路线', '未确认类型')))]
    original = deepcopy(features)
    apply_rail_presentation({'features': features}, presentation)
    assert {f['properties']['line_id'] for f in features} == {line}
    assert {f['properties'].get('display_track_type', f['properties']['track_type']) for f in features} == {'普速铁路线'}
    for old, new in zip(original, features):
        assert all(new['properties'][k] == v for k, v in old['properties'].items())
    assert features[1]['properties']['display_style_provenance']['verification_status'] == 'display_only_inference'
    assert rail_line_presentation(lib) is presentation


def test_mixed_confirmed_types_and_sidings_do_not_inherit_a_guessed_class(tmp_path):
    edges = [edge('main', 1, 2), edge('freight', 2, 3), edge('unknown', 3, 4)]
    edges[1]['way_tags']['usage'] = 'freight'
    edges[2]['way_tags'].pop('highspeed')
    # Force all source edges into one explicitly defined line for this fixture.
    for item in edges:
        item['line_id'] = 'RL-mixed'
    lib = DiskRailLineLibrary(install(tmp_path, edges))
    result = rail_line_presentation(lib)
    assert result['RL-mixed']['fallback_type'] is None
    collection = {'features': [{'properties': {'line_id': 'RL-mixed', 'network_edge_id': 'unknown',
                                              'track_type': '未确认类型'}}]}
    apply_rail_presentation(collection, result)
    assert 'display_track_type' not in collection['features'][0]['properties']
    apply_rail_presentation(collection, result, {'RL-mixed': {'track_type': '高速铁路线'}})
    assert collection['features'][0]['properties']['display_track_type'] == '高速铁路线'
    assert collection['features'][0]['properties']['track_type'] == '未确认类型'


def test_workspace_members_share_parent_but_preserve_source_and_edge_ids(tmp_path):
    edges = [edge('one', 1, 2), edge('two', 2, 3, highspeed='yes')]
    lib = DiskRailLineLibrary(install(tmp_path, edges))
    presentation = rail_line_presentation(lib)
    features = [{'properties': {'line_id': line_identity(e)[0], 'network_edge_id': e['id']}} for e in edges]
    apply_rail_presentation({'features': features}, presentation)
    assert len({f['properties']['line_id'] for f in features}) == 1
    assert [f['properties']['source_line_id'] for f in features] == [line_identity(e)[0] for e in edges]
    assert [f['properties']['network_edge_id'] for f in features] == ['one', 'two']
