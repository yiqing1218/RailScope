from copy import deepcopy
from types import SimpleNamespace

from railscope.domain import Station
from railscope.repository import RailRepository
from desktop.plan_labels import PlanLabels, POSITION_KEY, STOP_KEY


def fixture(count=2):
    positions = [{'station_id': 'station:node/' + str(i), 'station_name': '旧' + str(i),
                  'edge_id': 'NE-' + str(i), 'offset_m': 30} for i in range(count)]
    route = {'id': 'CO-1', 'name': '旧0 → 旧1 · 单向参考通道',
             'path': [{'edge_id': 'NE-1', 'direction': 'forward'}],
             'extensions': {POSITION_KEY: positions}}
    stops = [{'node_id': i, 'arrival': '08:00', 'extensions': {STOP_KEY: deepcopy(p)}}
             for i, p in enumerate(positions)]
    payload = {'routes': [route], 'trains': [{'id': 'G1', 'stops': stops}]}
    plan = SimpleNamespace(lines={'rail/G1': {'stations': [{'name': p['station_name']} for p in positions]}})
    graph = {'points': [{'properties': {'osm_node_id': i, 'name': '旧' + str(i)}} for i in range(count)]}
    return payload, graph, plan


def test_labels_touch_only_changed_station_and_preserve_path_ids_and_times():
    payload, graph, plan = fixture(10000)
    before = deepcopy(payload)
    calls = []
    library = SimpleNamespace(station_directory={'node/1': {}},
        endpoint_label=lambda key: calls.append(key) or '新站名')
    repo = RailRepository()
    station = Station('ST-1', '旧1', 120, 30, 'NN-1')
    repo.stations['ST-1'] = station
    labels = PlanLabels(payload, graph, plan)
    changed = labels.refresh(library, {'station:node/1'}, repo, {'station_sources': {'node/1': 'ST-1'}})
    assert calls == ['station:node/1'] and changed == {'station:node/1': '新站名'}
    assert payload['routes'][0]['name'] == '旧0 → 新站名 · 单向参考通道'
    assert graph['points'][1]['properties']['name'] == '新站名'
    assert plan.lines['rail/G1']['stations'][1]['name'] == '新站名'
    assert repo.stations['ST-1'].name == '新站名' and station.name == '旧1'
    # Normalise only known presentation fields; every remaining ID and time
    # must match exactly, including all 9,999 unaffected stops.
    payload['routes'][0]['name'] = before['routes'][0]['name']
    payload['routes'][0]['extensions'][POSITION_KEY][1]['station_name'] = '旧1'
    payload['trains'][0]['stops'][1]['extensions'][STOP_KEY]['station_name'] = '旧1'
    assert payload == before


def test_manual_titles_stay_and_no_change_has_no_render_work():
    payload, graph, plan = fixture()
    payload['routes'][0]['name'] = '人工命名的通道'
    names = {'station:node/1': '新站名'}
    library = SimpleNamespace(station_directory={'node/1': {}}, endpoint_label=names.__getitem__)
    labels = PlanLabels(payload, graph, plan)
    assert labels.refresh(library, set(names)) == names
    assert payload['routes'][0]['name'] == '人工命名的通道'
    assert not labels.refresh(library, set(names))
    assert not labels.refresh(library, {'RL-1'})
    names['station:node/1'] = '旧1'
    assert labels.refresh(library, set(names)) == names  # undo rename
