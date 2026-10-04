"""Station/facility owners must be shared by map, inspector and directory."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('tags,expected', [
    ({'railway': 'yard'}, '车场'),
    ({'railway': 'service_station'}, '整备场'),
    ({'name': '甲动车段'}, '动车段'),
    ({'name': '甲动车运用所'}, '动车所'),
    ({'name': '甲客车技术整备所'}, '客车整备所'),
    ({'name': '甲整修站'}, '整修站'),
    ({'name': '甲站修所'}, '站修所'),
    ({'name': '甲列检所'}, '列检所'),
    ({'name': '甲机车整备所'}, '机车整备所'),
    ({'name': '甲机务折返段'}, '机务折返段'),
    ({'name': '甲货车车辆段'}, '货车车辆段'),
    ({'name': '甲调车场'}, '调车场'),
    ({'name': '甲到发场'}, '到发场'),
    ({'name': '甲工业站'}, '工业站'),
    ({'railway': 'yard', 'railway:yard:purpose': 'intermodal'}, '集装箱办理站'),
    ({'railway': 'yard', 'railway:yard:purpose': 'storage;maintenance'}, '车场'),
    ({'name': '城市停车场'}, '未定义'),
    ({'railway': 'crossing'}, '未定义'),
])
def test_facility_types_from_explicit_source_evidence(tags, expected):
    from desktop.catalog_metadata import station_type, STATION_TYPES
    assert station_type(tags) == expected
    assert expected in STATION_TYPES


@pytest.mark.parametrize('layer,props', [
    ('rail-points', {'station_source_id': 'way/11', 'kind': 'depot'}),
    ('rail-station-labels', {'station_key': 'station:way/11'}),
    ('rail-station-fill', {'infrastructure_id': 'way/99', 'station_key': 'station:way/11'}),
    ('rail-station-outline', {'infrastructure_id': 'way/99', 'associated_station_ids': '["way/11"]'}),
    ('rail-platform-fill', {'associated_station_ids': ['node/11']}),
])
def test_map_representations_resolve_the_same_directory_owner(monkeypatch, layer, props):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    import launcher
    record = {'id': 'node/11' if props.get('associated_station_ids') == ['node/11'] else 'way/11'}
    host = SimpleNamespace(rail_catalog_widget=SimpleNamespace(
        station_record=lambda key: record if key == record['id'] else None))
    assert launcher.Desk._rail_station_record_for_feature(host, {'layer': layer, 'properties': props}) == record


def test_unassociated_area_is_editable_and_browsable_after_import(qtbot, tmp_path):
    from desktop.import_rail import extract as import_rail
    from desktop.rail_boundaries import extract
    from desktop.catalog_metadata import rail_station_records
    osm = tmp_path / 'facilities.osm'
    osm.write_text('''<osm version="0.6">
    <node id="1" lat="30" lon="120"/><node id="2" lat="30.001" lon="120"/>
    <node id="3" lat="30.001" lon="120.001"/><node id="4" lat="30" lon="120.001"/>
    <node id="8" lat="30.01" lon="120.01"><tag k="railway:facility" v="maintenance"/>
      <tag k="name" v="甲列检所"/></node>
    <way id="11"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/>
      <tag k="railway" v="yard"/></way>
    <way id="12"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/>
      <tag k="railway:facility" v="maintenance"/><tag k="name" v="乙站修所"/></way>
    <way id="20"><nd ref="1"/><nd ref="2"/><tag k="railway" v="rail"/></way>
    </osm>''', encoding='utf-8')
    directory = tmp_path / 'rail'
    import_rail(osm, directory)
    extract(osm, directory, progress=lambda *_: None)
    records, _ = rail_station_records(directory, [], limit=1000)
    owners = {record['id']: record for record in records}
    assert owners['way/11']['station_type'] == '车场'
    assert owners['way/12']['station_type'] == '站修所'
    assert owners['node/8']['station_type'] == '列检所'
    with sqlite3.connect(directory / 'rail.sqlite') as db:
        raw = db.execute("SELECT data FROM features WHERE kind='railStationAreas' AND json_extract(data,'$.properties.infrastructure_id')='way/11'").fetchone()[0]
    area = json.loads(raw)
    assert area['properties']['associated_station_ids'] == ['way/11']
    assert area['properties']['geometry_source'] == 'osm_polygon'
    from desktop.rail_catalog_ui import RailCatalog
    from desktop.tests.test_operating_ui import MapStub
    widget = RailCatalog(directory, tmp_path / 'settings.json', MapStub())
    qtbot.addWidget(widget)
    assert widget.select_station_record('way/11')
    assert widget.station_record('way/11')['station_type'] == '车场'
    assert widget.select_station_record('way/12')


@pytest.mark.parametrize('kind', ['车辆段', '动车所', '动车段', '客车整备所', '编组站', '车场', '整修站', '列检所'])
def test_map_and_directory_facility_open_same_editor(qtbot, monkeypatch, kind):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    import launcher
    from PySide6.QtWidgets import QDialog, QLabel, QMainWindow, QTabWidget, QWidget
    record = {'id': 'way/11', 'name': '甲' + kind, 'station_type': kind, 'osm_node_id': None,
              'province': '甲省', 'city': '甲市', 'line_ids': [], 'line_names': [],
              'properties': {'kind': 'depot', 'station_source_id': 'way/11', 'node_tags': {}}}
    opened = []
    class Dialog(QDialog):
        def exec(self):
            tabs = self.findChild(QTabWidget)
            opened.append((self.windowTitle(), [tabs.tabText(i) for i in range(tabs.count())],
                           {label.text() for label in self.findChildren(QLabel)}))
            return QDialog.DialogCode.Rejected
    class Connections(QWidget):
        def __init__(self, *args, **kwargs):
            super().__init__()
    class Host(QMainWindow):
        _rail_station_record_for_feature = launcher.Desk._rail_station_record_for_feature
    host = Host()
    qtbot.addWidget(host)
    host.rail_catalog_widget = SimpleNamespace(station_record=lambda key: record if key == 'way/11' else None,
        station_record_by_id={'way/11': record}, overrides={}, catalog={})
    host.rail_operations = SimpleNamespace(line_library=lambda: object())
    host.route_lookup, host.station_lookup = {}, {}
    host.edit_selected_metadata = lambda feature: launcher.Desk.edit_selected_metadata(host, feature)
    monkeypatch.setattr(launcher, 'QDialog', Dialog)
    monkeypatch.setattr(launcher, 'StationConnectionSelector', Connections)
    monkeypatch.setattr(launcher.QMessageBox, 'information', lambda *_: None)
    host.edit_selected_metadata({'layer': 'rail-points', 'properties': {'station_source_id': 'way/11', 'kind': 'depot'}})
    launcher.Desk.edit_rail_station_metadata(host, 'way/11')
    assert len(opened) == 2 and opened[0] == opened[1]
    assert {'站台数量', '股道数量', '补充属性'} <= opened[0][2]


def test_map_double_click_sends_a_resolvable_layer_and_owner():
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('JavaScript runtime unavailable')
    source = (Path(__file__).parents[1] / 'assets/map.js').read_text(encoding='utf-8')
    handler = source[source.index("  map.on('dblclick',event=>{"):source.index("  map.on('click',event=>{")]
    normalize = source[source.index('function normalizedFeature('):source.index('function publishSelection(')]
    probe = '''
const handlers={}, calls=[], selected=[];
let prevented=false;
const feature={layer:{id:'rail-station-fill'},properties:{station_key:'station:way/11',associated_station_ids:'["way/11"]'},geometry:{type:'Polygon'}};
const map={on:(name,fn)=>handlers[name]=fn,queryRenderedFeatures:()=>[feature]};
const selectableLayers=()=>['rail-station-fill'];
const selectFeature=value=>selected.push(value);
const report=(...value)=>calls.push(value);
'''
    probe += normalize + handler + '''
handlers.dblclick({point:{x:1,y:1},preventDefault:()=>prevented=true});
const payload=JSON.parse(calls[0][1]);
if(!prevented||selected[0]!==feature||calls[0][0]!=='featureActivated'||payload.layer!=='rail-station-fill'||payload.properties.associated_station_ids[0]!=='way/11')throw Error('Station activation did not preserve its owner and layer');
'''
    subprocess.run([node, '-e', probe], check=True, capture_output=True, text=True)


def test_map_activation_reveals_station_directory_owner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    import launcher
    focused, details = [], []
    record = {'id': 'way/11'}
    host = SimpleNamespace(open_sidebar=lambda index: focused.append(index),
        rail_catalog_widget=SimpleNamespace(select_station_record=lambda key: focused.append(key)),
        _rail_station_record_for_feature=lambda _: record, display_feature=details.append)
    feature = {'layer': 'rail-station-fill', 'properties': {'station_key': 'station:way/11'}}
    launcher.Desk.select_map_feature(host, json.dumps(feature))
    assert focused == [0, 'way/11'] and details == [feature]


def test_pedestrian_crossing_is_not_a_station_owner():
    from desktop.rail_station_types import station_point_kind
    from desktop.rail_station_directory import station_candidates
    assert station_point_kind({'railway': 'crossing'}) is None
    assert station_candidates({'kind': 'crossing', 'osm_node_id': 8}) == []
