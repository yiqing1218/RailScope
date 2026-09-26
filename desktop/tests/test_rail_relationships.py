import pytest

from desktop.tests.test_line_membership import edge, install, aliases
from desktop.rail_line_store import DiskRailLineLibrary
from desktop.rail_relationships import line_relationships, relationship_changes


def test_editing_line_stations_updates_station_picker_without_rewriting_source(tmp_path):
    database = install(tmp_path, [edge('a',1,2,'甲线'),edge('b',2,3,'乙线'),edge('c',4,5,'丙线')])
    aliases(database,[('node/100','甲站',100,1,0,'source',1,118.0001,32),
                      ('node/101','乙站',101,3,0,'source',1,118.0003,32)])
    original = database.read_bytes()
    lib = DiskRailLineLibrary(database)
    a,b,c = [lib.search_lines(name)[0]['id'] for name in ('甲线','乙线','丙线')]
    before = line_relationships(lib,[a])
    assert before['station_ids'] == ['station:node/100']
    assert before['connected_line_ids'] == [b]
    changes = relationship_changes(lib,[a],before,['station:node/101'],[c])
    edited = DiskRailLineLibrary(database,metadata=changes)
    assert line_relationships(edited,[a])['station_ids'] == ['station:node/101']
    assert {r['id'] for r in edited.connected_lines('station:node/101')} == {a,b}
    assert not edited.connected_lines('station:node/100')
    assert a in line_relationships(edited,[c])['connected_line_ids']
    assert a not in line_relationships(edited,[b])['connected_line_ids']
    assert relationship_changes(edited,[a],line_relationships(edited,[a]),['station:node/101'],[c]) == {}
    assert database.read_bytes() == original
    with pytest.raises(ValueError,match='本线路'):
        relationship_changes(lib,[a],before,before['station_ids'],[a])


def test_relationship_picker_shows_names_but_keeps_ids(qtbot):
    from desktop.rail_relationship_ui import RelationshipSelector
    widget = RelationshipSelector(lambda q: [('station:node/2','乙站')], [('station:node/1','甲站')], '搜索站名')
    qtbot.addWidget(widget)
    widget.search.setEditText('乙站')
    widget.search.find_results()
    widget.search.setCurrentIndex(0)
    widget.add_selected()
    assert widget.values() == ['station:node/1','station:node/2']
    assert widget.items.item(1).text() == '乙站'
    widget.items.setCurrentRow(0)
    widget.remove_selected()
    assert widget.values() == ['station:node/2']


def test_invalid_relationship_keeps_editor_open(qtbot, monkeypatch):
    from desktop.line_metadata_ui import LineMetadataDialog, QMessageBox
    from PySide6.QtWidgets import QDialog
    dialog = LineMetadataDialog('rail','甲线',['全国铁路'],{})
    qtbot.addWidget(dialog)
    messages = []
    monkeypatch.setattr(QMessageBox,'warning',lambda *args: messages.append(args[-1]))
    def invalid():
        raise ValueError('车站附近没有所选线路的真实轨道')
    dialog.relationship_validator = invalid
    dialog.show()
    dialog.save()
    assert dialog.isVisible() and messages
    dialog.relationship_validator = lambda: {'station:node/1': {'connected_lines':[]}}
    dialog.save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert 'station:node/1' in dialog.relationship_updates
