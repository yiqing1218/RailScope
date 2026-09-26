import sqlite3
from types import SimpleNamespace


def test_station_list_details_are_scrollable_and_clear_on_next_selection(qtbot):
    from launcher import Desk
    from PySide6.QtWidgets import QTableWidget, QPlainTextEdit
    table = QTableWidget(0, 2)
    qtbot.addWidget(table)
    view = SimpleNamespace(properties=table)
    names = '\n'.join(f'{i}. 站点{i}' for i in range(1, 109))
    Desk.set_property_rows(view, [('经过车站', names)])
    field = table.cellWidget(0, 1)
    assert isinstance(field, QPlainTextEdit) and field.isReadOnly()
    assert field.toPlainText().endswith('108. 站点108')
    assert table.rowHeight(0) == 240
    Desk.set_property_rows(view, [('名称', '南京南')])
    assert table.cellWidget(0, 1) is None
    assert table.item(0, 1).text() == '南京南'


def test_rail_master_excludes_metro_pois_platforms_and_outlines(tmp_path):
    import json
    from desktop.rail_store import build_index, viewport
    from desktop.metro_store import ensure_index
    from desktop.tests.test_metro_store import _collection
    point = {'type': 'Feature', 'properties': {'osm_node_id': 100, 'name': '地铁站', 'kind': 'station'},
             'geometry': {'type': 'Point', 'coordinates': [121,31]}}
    area = {'type': 'Feature', 'properties': {'osm_way_id': 200, 'boundary_kind': 'platform',
            'way_tags': {'railway': 'platform', 'train': 'yes'}},
            'geometry': {'type': 'Polygon', 'coordinates': [[[121,31],[121.01,31],[121.01,31.01],[121,31]]]}}
    _collection(tmp_path/'china_metro_stations.geojson', [point])
    _collection(tmp_path/'china_metro_station_areas.geojson', [area])
    metro = ensure_index(tmp_path)
    rail_point = {**point, 'properties': {'osm_node_id': 101, 'name': '铁路站', 'kind': 'station'}}
    bus = {**area, 'properties': {'osm_way_id': 201, 'way_tags': {'highway': 'platform'}}}
    build_index(tmp_path, [], [point, rail_point], [area, bus], [])
    with sqlite3.connect(tmp_path/'rail.sqlite') as db:
        db.execute("INSERT INTO features VALUES(1000,'railStationAreas','main',?)", (json.dumps(area),))
        db.execute('INSERT INTO bounds VALUES(1000,121,121.01,31,31.01)')
    args = (tmp_path, [120,30,122,32], 17)
    for kind in ('railPlatforms', 'railStationAreas'):
        assert viewport(args[0], kind, *args[1:], metro_database=metro)['features'] == []
    assert [f['properties']['osm_node_id'] for f in viewport(args[0], 'railPoints', *args[1:], metro_database=metro)['features']] == [101]
    # Source rows stay intact; these are display predicates, not deletions.
    with sqlite3.connect(tmp_path/'rail.sqlite') as db:
        assert db.execute('SELECT count(*) FROM features').fetchone()[0] == 5


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
