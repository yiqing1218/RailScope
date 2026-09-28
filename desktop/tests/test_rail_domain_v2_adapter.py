from copy import deepcopy
from dataclasses import asdict
import json
import sqlite3

from desktop.domain_adapter import build_repository
from desktop.station_track_semantics import migrate_track_override, track_number
from desktop.tests.test_full_corridor import fixture
from railscope.domain import StationTrack
from railscope.integrity import path_refs


def graph_fixture():
    payload, edges = fixture()
    for edge in edges:
        edge['way_tags'] = {'railway': 'rail', 'usage': 'main', 'highspeed': 'yes'}
    points = [{'properties': {'osm_node_id': node, 'kind': kind, 'name': '同名设施'}}
              for node, kind in ((1, 'station'), (2, 'signal_box'), (3, 'station'))]
    return payload, {'edges': edges, 'points': points}


def test_station_main_track_keeps_semantics_and_internal_id_is_not_number(tmp_path):
    payload, graph = graph_fixture()
    payload['trains'][0]['stops'][0]['station_track_id'] = 'e1'
    before = deepcopy((payload, graph))
    repo, bindings = build_repository(graph, payload, tmp_path / 'identity.sqlite')
    track = repo.station_tracks[repo.stops[0].station_track_id]
    assert track.track_number is None
    assert track.track_role == 'main_track'
    assert track.railway_class == 'high_speed'
    assert track.name == '同名设施 · 站内正线'
    assert track.edge_refs[0].edge_id == bindings['edges']['e1']
    assert len(repo.station_track_edges) == 1
    assert (payload, graph) == before
    assert len(repo.operational_points) == 3
    assert {p.point_type for p in repo.operational_points.values()} == {'station', 'signal_box'}
    assert len({p.id for p in repo.operational_points.values() if p.point_type == 'station'}) == 2
    again, repeated = build_repository(graph, payload, tmp_path / 'identity.sqlite')
    assert repeated == bindings and again.operational_points == repo.operational_points


def test_source_and_manual_numbers_survive_but_generated_number_migrates(tmp_path):
    payload, graph = graph_fixture()
    graph['edges'][0]['way_tags'].update({'service': 'siding', 'railway:track_ref': 'Ⅲ'})
    payload['trains'][0]['stops'][0]['station_track_id'] = 'e1'
    repo, bindings = build_repository(graph, payload, tmp_path / 'identity.sqlite')
    initial = repo.station_tracks[repo.stops[0].station_track_id]
    assert initial.track_number == 'Ⅲ'
    assert initial.track_role == 'unknown'
    refs = path_refs(repo, [(bindings['edges']['e1'], True), (bindings['edges']['e2'], True)])
    track = StationTrack('STTR-preserved', initial.station_id, '第98股道', '98',
        edge_refs=refs, length_m=refs[-1].end_distance_m, is_virtual=False,
        verification_status='automatic_reference')
    saved = {'station_track': asdict(track), 'source_edge_ids': ['e1', 'e2'],
             'source': 'automatic_yard_track_number', 'track_number': '98',
             'display_name': '第98股道', 'station_name': '同名设施'}
    before = deepcopy(saved)
    migrated, _ = build_repository(graph, payload, tmp_path / 'identity.sqlite', {'object:section_id:RS-one': saved})
    result = migrated.station_tracks['STTR-preserved']
    assert result.track_number is None and '98' not in result.name
    assert result.legacy_metadata['automatic_numbering']['track_number'] == '98'
    assert result.edge_refs == refs and len(migrated.station_track_edges) == 2
    assert saved == before
    manual = {**saved, 'source': 'manual', 'verification_status': 'user_named',
              'station_track': {**asdict(track), 'name': '人工核验Ⅲ道', 'track_number': 'Ⅲ', 'verification_status': 'user_named'}}
    verified, _ = build_repository(graph, payload, tmp_path / 'identity.sqlite', {'object:section_id:RS-one': manual})
    assert verified.station_tracks[track.id].name == '人工核验Ⅲ道'
    assert verified.station_tracks[track.id].track_number == 'Ⅲ'


def test_display_migration_preserves_metadata_and_genuine_manual_labels():
    saved = {'display_name': '新龙华站 · 第98股道（暂编）', 'track_number': 98,
             'source': 'automatic_yard_track_number', 'color': '#abc123', 'station_name': '新龙华站'}
    result = migrate_track_override(saved, track_role='shunting_track')
    assert result['display_name'] == '新龙华站 · 调车线'
    assert result['track_number'] is None and result['color'] == '#abc123'
    assert result['display_alias'] == saved['display_name']
    assert migrate_track_override(result) == result
    manual = {**saved, 'source': 'manual', 'verification_status': 'user_named'}
    assert migrate_track_override(manual) == manual
    assert track_number({'railway:track_ref': 'STTR-84F71A'}) is None
    assert track_number({'railway:track_ref': 'XLH-T0098'}) is None
    assert track_number({'service': 'siding', 'ref': '3043'}) is None
    assert track_number({'name': '上海虹桥站Ⅰ道'}) == 'Ⅰ'
    internal = {'station_track': {'id': 'STTR-one', 'track_number': 'e98', 'name': '旧标签'},
                'source_edge_ids': ['e98']}
    assert migrate_track_override(internal)['station_track']['track_number'] is None


def test_selected_station_load_keeps_multi_edge_track_and_workspace_metadata(tmp_path):
    from desktop.station_tracks import load_station_tracks, track_overrides
    _, graph = graph_fixture()
    edges = graph['edges'][:2]
    for edge in edges:
        edge['way_tags'].update(service='yard')
    with sqlite3.connect(tmp_path / 'rail_catalog.sqlite') as db:
        db.execute('CREATE TABLE catalog(id,station_name,data)')
        db.execute('INSERT INTO catalog VALUES(?,?,?)', ('ST-test', '测试站', json.dumps({'provinces': ['测试省']})))
    with sqlite3.connect(tmp_path / 'rail.sqlite') as db:
        db.executescript('CREATE TABLE features(id,kind,data); CREATE TABLE edges(id,data); '
            'CREATE TABLE rail_feature_groups(feature_id,group_id); '
            'CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);')
        for i, edge in enumerate(edges, 1):
            props = {'network_edge_id': edge['id'], 'section_id': 'RS-one', 'way_tags': edge['way_tags']}
            feature = {'properties': props, 'geometry': {'type': 'LineString', 'coordinates': edge['coordinates']}}
            db.execute('INSERT INTO edges VALUES(?,?)', (edge['id'], json.dumps(edge)))
            db.execute('INSERT INTO features VALUES(?,?,?)', (i, 'rail', json.dumps(feature)))
            db.execute('INSERT INTO rail_feature_groups VALUES(?,?)', (i, 'ST-test'))
    station = {'name': '测试站', 'kind': 'station', 'infrastructure_id': 'node/1', 'province': '测试省'}
    overrides = {'object:section_id:RS-one': {'display_name': '第98股道（暂编）', 'track_number': '98',
        'source': 'automatic_yard_track_number', 'color': '#123abc', 'catalog_parent': ['自定义']}}
    repo, rows, _ = load_station_tracks(tmp_path, tmp_path / 'identity.sqlite', station, overrides)
    track = next(iter(repo.station_tracks.values()))
    assert len(track.edge_refs) == 2 and len(repo.station_track_edges) == 2
    assert track.track_number is None and track.track_role == 'unknown'
    assert track.name == '测试站 · 站线（用途待核实）'
    saved = track_overrides(repo, rows)['object:section_id:RS-one']
    assert saved['track_number'] is None
    assert saved['color'] == '#123abc' and saved['catalog_parent'] == ['自定义']
    assert saved['station_track']['legacy_metadata']['automatic_numbering']['track_number'] == '98'
    reread, _, _ = load_station_tracks(tmp_path, tmp_path / 'identity.sqlite', station, {'object:section_id:RS-one': saved})
    assert next(iter(reread.station_tracks.values())) == track
    with sqlite3.connect(tmp_path / 'rail_catalog.sqlite') as db:
        db.execute("UPDATE catalog SET station_name='未关联站场' WHERE id='ST-test'")
    linked, _, _ = load_station_tracks(tmp_path, tmp_path / 'identity.sqlite', station,
                                       {'ST-test': {'station_source': 'node/1'},
                                        'object:section_id:RS-one': saved})
    assert next(iter(linked.station_tracks.values())) == track


def test_canonical_route_intent_uses_stable_references_without_changing_path(tmp_path):
    from desktop.rail_lines import line_identity
    from railscope.workspace import SQLiteWorkspace
    payload, graph = graph_fixture()
    source_line = line_identity(graph['edges'][0])[0]
    sequence = [{'kind': 'endpoint', 'node_id': 'station:node/1'},
                {'kind': 'line', 'line_id': source_line}, {'kind': 'endpoint', 'node_id': 5}]
    payload['routes'][0]['sequence'] = sequence
    payload['routes'][0]['extensions']['railscope.org/line-resolution'] = {'policy': 'auto',
        'selection': {'requested_sequence': sequence}, 'snapshot': 'SN-old'}
    for train in payload['trains']:
        for stop in train['stops']:
            stop['arrival_s'] += 86400
            stop['departure_s'] += 86400
    repo, bindings = build_repository(graph, payload, tmp_path / 'identity.sqlite')
    intent = next(iter(repo.route_intents.values()))
    corridor = next(iter(repo.corridors.values()))
    assert [(s.kind, s.reference_id) for s in intent.steps] == [
        ('station', bindings['stations']['1']), ('infrastructure_line', bindings['lines'][source_line]),
        ('node', bindings['nodes']['5'])]
    assert intent.provenance['requested_sequence'] == sequence
    assert corridor.route_intent_id == intent.id and corridor.resolution_mode == 'automatic_reference'
    assert [r.edge_id for r in corridor.edge_refs] == [bindings['edges'][e['id']] for e in graph['edges']]
    store = SQLiteWorkspace(tmp_path / 'workspace.sqlite')
    store.seed(repo)
    loaded, _ = store.load()
    assert loaded.route_intents == repo.route_intents and loaded.corridors == repo.corridors
    assert loaded.stops == repo.stops and min(s.arrival_time_s for s in loaded.stops) > 86400

    unknown = deepcopy(payload)
    unknown_sequence = deepcopy(sequence)
    unknown_sequence[0]['node_id'] = 'station:node/unavailable'
    unknown['routes'][0]['extensions']['railscope.org/line-resolution']['selection']['requested_sequence'] = unknown_sequence
    unresolved, _ = build_repository(graph, unknown, tmp_path / 'identity.sqlite')
    unresolved_intent = next(iter(unresolved.route_intents.values()))
    assert unresolved_intent.steps == () and unresolved_intent.verification_status == 'unresolved'
    assert unresolved_intent.provenance['requested_sequence'] == unknown_sequence
    assert unresolved_intent.provenance['unresolved_aliases'][0]['source_reference'] == 'station:node/unavailable'
    assert unresolved.stations == repo.stations and unresolved.nodes == repo.nodes
    assert next(iter(unresolved.corridors.values())).edge_refs == corridor.edge_refs
