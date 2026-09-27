import pytest

from desktop.rail_line_store import DiskRailLineLibrary
from desktop.rail_lines import line_identity
from desktop.tests.test_line_membership import aliases, edge, install, seq


def network(tmp_path, **options):
    tracks = [edge('a', 1, 2, '绕行线'), edge('b', 4, 5, '绕行线'),
              edge('bridge1', 2, 3, '共用正线', **options),
              edge('bridge2', 3, 4, '共用正线', **options)]
    index = install(tmp_path, tracks)
    aliases(index, [('node/start', '起站', 100, 1, 0, 'source', 1, 118.0001, 32),
                    ('node/mid', '共用区间站', 101, 3, 0, 'source', 1, 118.0003, 32),
                    ('node/end', '终站', 102, 5, 0, 'source', 1, 118.0005, 32)])
    return DiskRailLineLibrary(index), line_identity(tracks[0])[0]


def test_named_gap_uses_real_tracks_and_preserves_membership(tmp_path):
    lib, line = network(tmp_path)
    choices = dict(lib.reachable_nodes('station:node/start', line, reference=True))
    assert 'station:node/end' in choices
    assert 'station:node/mid' in choices
    requested = seq('station:node/start', line, 'station:node/end')
    normalized, path = lib.resolve_with_sequence(requested, 'auto')
    assert [leg['edge_id'] for leg in path] == ['a', 'bridge1', 'bridge2', 'b']
    assert len({entry['line_id'] for entry in normalized[1::2]}) == 2
    assert lib.lines[line]['edge_count'] == 2
    selection = dict(requested_sequence=requested, resolved_sequence=normalized, path=path)
    assert lib.resolve(requested, 'auto', selection) == path
    with pytest.raises(ValueError):
        lib.resolve(requested, 'strict')


@pytest.mark.parametrize('options', [{'construction_status': 'planned'}, {'construction_status': 'unknown'},
    {'construction_status': 'disused'}, {'direction': 'reverse'}, {'direction': 'closed'}, {'length_m': 25000}])
def test_reference_cannot_cross_forbidden_or_unbounded_connection(tmp_path, options):
    lib, line = network(tmp_path, **options)
    assert 'station:node/end' not in dict(lib.reachable_nodes('station:node/start', line, reference=True))
    with pytest.raises(ValueError):
        lib.resolve(seq(1, line, 5), 'auto')


def test_next_line_intersection_is_applied_before_result_limit(tmp_path):
    tracks = [edge('a', 1, 2, '甲'), edge('b', 2, 3, '甲'), edge('c', 3, 4, '乙')]
    lib = DiskRailLineLibrary(install(tmp_path, tracks))
    aliases(lib.path, [('node/a', 'A站', 100, 2, 0, 'source', 1, 118, 32),
                      ('node/z', 'Z站', 101, 3, 0, 'source', 1, 118, 32)])
    choices = lib.reachable_nodes(1, line_identity(tracks[0])[0], limit=1, next_line=line_identity(tracks[-1])[0])
    assert choices[0][0] == 'station:node/z'


def test_inactive_selected_line_cannot_be_replaced_by_an_operating_line(tmp_path):
    tracks = [edge('a', 1, 2, '规划线路', construction_status='planned'),
              edge('b', 4, 5, '规划线路', construction_status='planned'),
              edge('other', 2, 4, '既有线路')]
    lib = DiskRailLineLibrary(install(tmp_path, tracks))
    with pytest.raises(ValueError):
        lib.resolve(seq(2, line_identity(tracks[0])[0], 4), 'auto')
