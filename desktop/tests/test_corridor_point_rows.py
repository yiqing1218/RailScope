import pytest

from desktop.rail_line_store import DiskRailLineLibrary
from desktop.tests.test_line_membership import aliases, edge, install, seq


def test_legacy_nearest_anchor_recovers_both_parallel_tracks(tmp_path):
    tracks = [edge('up', 1, 2, '沪蓉线'), edge('down', 3, 4, '沪蓉线')]
    tracks[0]['coordinates'] = [[118, 32], [119, 32]]
    tracks[1]['coordinates'] = [[118, 32.0001], [119, 32.0001]]
    index = install(tmp_path, tracks)
    aliases(index, [('node/a', '南京南', 100, 1, 1, 'automatic_nearby_topology_node', .99, 118, 32),
                    ('node/b', '合肥南', 101, 4, 1, 'automatic_nearby_topology_node', .99, 119, 32)])
    lib = DiskRailLineLibrary(index)
    line = lib.search_lines('沪蓉线')[0]['id']
    for start, end in [('station:node/a', 'station:node/b'), ('station:node/b', 'station:node/a')]:
        assert end in dict(lib.reachable_nodes(start, line))
        assert len(lib.resolve(seq(start, line, end), 'auto')) == 1
    with pytest.raises(ValueError, match='unresolved'):
        lib.resolve(seq('station:node/a', line, 'station:node/b'), 'strict')


def test_search_choice_keeps_station_name_visible(qtbot):
    from desktop.corridor_ui import SearchChoice
    choice = SearchChoice(lambda q: [], '站点', 'station:a', '南京南站 · 接轨：' + '长线路名称 / ' * 20)
    qtbot.addWidget(choice)
    choice.resize(220, 40)
    choice.show()
    choice.clearFocus()
    qtbot.wait(20)
    assert choice.lineEdit().cursorPosition() == 0


def test_two_column_rows_line_first_transfer_and_legacy_roundtrip(qtbot, tmp_path):
    from desktop.corridor_ui import CorridorSequenceTable
    tracks = [edge('a', 1, 2, '甲线'), edge('b', 2, 3, '乙线'), edge('c', 2, 4, '丙线')]
    lib = DiskRailLineLibrary(install(tmp_path, tracks))
    aliases(lib.path, [('node/mid', '换线站', 100, 2, 1, 'source', 1, 118, 32)])
    a, b = lib.search_lines('甲线')[0]['id'], lib.search_lines('乙线')[0]['id']
    table = CorridorSequenceTable(lib)
    qtbot.addWidget(table)
    table.set_sequence(seq(1, a, 2) + seq(2, b, 3)[1:])
    assert table.columnCount() == 2 and table.rowCount() == 3
    assert table.sequence() == seq(1, a, 2) + seq(2, b, 3)[1:]
    table.set_sequence([])
    table.assign(0, 0, 1)
    table.assign(0, 1, a)
    table.assign(1, 1, b)
    assert table.cellWidget(1, 0).currentData() == 'station:node/mid'
    assert table.rowCount() == 3
    table.assign(2, 0, 3)
    assert table.sequence() == seq(1, a, 'station:node/mid') + seq(0, b, 3)[1:]
    assert 4 not in dict(table.point_choices(1, ''))
    table.remove_point(1)
    # Deleting a transfer must not silently keep an endpoint on the wrong line.
    assert table.cellWidget(1, 0).currentData() is None
