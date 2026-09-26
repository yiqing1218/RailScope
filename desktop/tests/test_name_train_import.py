import json
from copy import deepcopy

import pytest

from desktop.rail_tables import merge_csv, export_template
from desktop.station_positions import POSITION_KEY, STOP_POSITION_KEY
from desktop.tests.test_full_corridor import fixture


def named_plan():
    payload, edges = fixture()
    payload['trains'] = []
    payload['routes'][0]['name'] = '南京南至合肥南'
    payload['routes'][0]['extensions'][POSITION_KEY] = [
        {'station_name': name, 'station_id': f'station:{node}', 'node_id': node,
         'edge_id': edge, 'offset_m': 100., 'distance_m': distance,
         'source': 'synthetic', 'snapshot': 'test'}
        for name, node, edge, distance in [('南京南', 1, 'e1', 100), ('合肥南站', 5, 'e4', 3500)]
    ]
    return payload, edges


def test_import_names_without_ids_or_sequence_and_keep_track_offsets():
    payload, _ = named_plan()
    before = deepcopy(payload)
    result = merge_csv('车次,站名,到达时间,出发时间\nG9,南京南站,,08:00\nG9,合肥南,09:00,\n', payload)
    train = result['trains'][0]
    assert train['route_id'] == 'corridor/full'
    assert [stop['node_id'] for stop in train['stops']] == [1, 5]
    assert train['stops'][0]['extensions'][STOP_POSITION_KEY]['offset_m'] == 100
    assert train['stops'][0]['arrival_s'] == train['stops'][0]['departure_s'] == 28800
    assert payload == before
    assert 'station_name_match' in json.dumps(train['extensions'])


def test_ambiguous_corridors_unknown_station_and_reverse_order_are_atomic():
    payload, _ = named_plan()
    twin = deepcopy(payload['routes'][0]); twin.update(id='other', name='另一完整通道')
    payload['routes'].append(twin)
    text = 'train_id,stop_name,arrival,departure\nG9,南京南,08:00,08:00\nG9,合肥南,09:00,09:00\n'
    with pytest.raises(ValueError, match='多条通道'):
        merge_csv(text, payload)
    selected = text.replace('train_id,', 'route_name,train_id,').replace('\nG9,', '\n南京南至合肥南,G9,')
    assert merge_csv(selected, payload)['trains'][0]['route_id'] == 'corridor/full'
    with pytest.raises(ValueError, match='未知站'):
        merge_csv(selected.replace(',合肥南,', ',未知站,'), payload)
    with pytest.raises(ValueError, match='站序'):
        merge_csv(selected.replace('南京南,08', '合肥南,08').replace('合肥南,09', '南京南,09'), payload)
    assert payload['trains'] == []


def test_name_template_works_without_existing_train():
    payload, _ = named_plan()
    text = export_template(payload)
    assert 'node_id' not in text.splitlines()[0]
    assert '南京南' in text and '合肥南' in text
    assert merge_csv(text, payload)['trains']


def test_same_named_stations_are_not_guessed_and_explicit_id_can_disambiguate():
    payload, _ = named_plan()
    twin = deepcopy(payload['routes'][0]['extensions'][POSITION_KEY][0])
    twin.update(node_id=2, station_id='another-station', distance_m=800)
    payload['routes'][0]['extensions'][POSITION_KEY].insert(1, twin)
    text = 'train_id,stop_name,arrival,departure\nG9,南京南,08:00,08:00\nG9,合肥南,09:00,09:00\n'
    with pytest.raises(ValueError, match='同名站'):
        merge_csv(text, payload)
    precise = text.replace('train_id,','node_id,train_id,').replace('\nG9,南京南,','\n1,G9,南京南,').replace('\nG9,合肥南,','\n5,G9,合肥南,')
    assert merge_csv(precise,payload)['trains'][0]['stops'][0]['node_id'] == 1


def test_corridor_delete_checks_references_and_supports_undo(qtbot, tmp_path):
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub
    from desktop.tests.test_workspace_revision import install_reference_database
    install_reference_database(tmp_path)
    editor = RailEditor(MapStub(), tmp_path, tmp_path/'plan.json')
    qtbot.addWidget(editor)
    original = editor.document()
    used = original['routes'][0]['id']
    with pytest.raises(ValueError, match='车次'):
        editor.delete_corridor(used)
    assert editor.document() == original
    spare = deepcopy(original['routes'][0]); spare['id'] = 'SPARE'
    payload = deepcopy(original); payload['routes'].append(spare)
    editor.accept_batch(payload)
    editor.delete_corridor('SPARE')
    assert 'SPARE' not in [r['id'] for r in editor.document()['routes']]
    editor.undo()
    assert 'SPARE' in [r['id'] for r in editor.document()['routes']]
    assert editor.document()['trains'] == original['trains']
    editor.timer.stop()
