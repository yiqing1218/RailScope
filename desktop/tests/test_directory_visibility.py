import sqlite3
from types import SimpleNamespace


def test_metro_stations_are_independent_of_lines_and_rail(tmp_path):
    from launcher import Desk
    database = tmp_path / "metro.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE directory_nodes(object_id TEXT,kind TEXT,archived INTEGER)")
        db.executemany("INSERT INTO directory_nodes VALUES(?,'object',?)", [('a',0),('b',0),('old',1)])
    view = SimpleNamespace(metro_db=database,flags={'stations':True,'railStations':False},
                           metro_station_master=True, station_direct_visible=set(),station_exclusions=set(),
                           visible_lines=set())
    assert Desk.effective_station_ids(view) == {'a','b'}
    view.station_exclusions.add('a')
    assert Desk.effective_station_ids(view) == {'b'}
    view.metro_station_master=False
    view.station_direct_visible={'a','old'}
    view.station_exclusions.clear()
    assert Desk.effective_station_ids(view) == {'a'}
    view.flags['stations']=False
    view.flags['railStations']=True
    assert Desk.effective_station_ids(view) == set()


def test_native_checkbox_click_turns_on_a_lazy_folder(qtbot,tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTreeView
    from lazy_directory import SqliteDirectoryModel
    from desktop.tests.test_metro_line_directory import sync_line_directory
    database=tmp_path/'model.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    sync_line_directory(database,[{'osm_relation_id':1}],lambda route:('省','市','线'),{})
    model=SqliteDirectoryModel(database,'line_directory_nodes')
    model.fetchMore()
    tree=QTreeView();tree.setModel(model);tree.setHeaderHidden(True);tree.show();qtbot.addWidget(tree)
    index=model.index(0,0)
    calls=[];model.toggled.connect(lambda key,on:calls.append(on))
    # Space uses Qt's native delegate setData(int), unlike direct enum tests.
    tree.setCurrentIndex(index);tree.setFocus()
    qtbot.keyClick(tree,Qt.Key.Key_Space)
    assert calls == [True]


def test_hidden_tall_tab_does_not_leave_a_blank_tail(qtbot):
    from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
    from desktop.components import CurrentPageTabs
    tabs=CurrentPageTabs();qtbot.addWidget(tabs)
    for height in (60,600):
        page=QWidget();layout=QVBoxLayout(page);label=QLabel('目录内容')
        label.setFixedHeight(height);layout.addWidget(label);tabs.addTab(page,str(height))
    short=tabs.sizeHint().height()
    tabs.setCurrentIndex(1)
    tall=tabs.sizeHint().height()
    assert tall-short == 540
    tabs.setCurrentIndex(0)
    assert tabs.minimumSizeHint().height() == short
