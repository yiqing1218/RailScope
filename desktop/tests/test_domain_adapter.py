from desktop.tests.test_full_corridor import fixture


def test_shared_station_keeps_each_trains_track_position(tmp_path):
    from desktop.domain_adapter import build_repository
    from railscope.services.timetable.canonical import stop_distances

    payload, edges = fixture()
    # Two routes stop at different physical nodes of the same logical station.
    payload['trains'] = [payload['trains'][1]]
    payload['trains'][0]['stops'] = [
        {'node_id': n, 'arrival_s': i * 100, 'departure_s': i * 100}
        for i, n in enumerate((1, 3, 5))
    ]
    edges.extend([
        dict(edges[0], id='other-a', from_node=6, to_node=7, node_ids=[6, 7]),
        dict(edges[1], id='other-b', from_node=7, to_node=8, node_ids=[7, 8]),
    ])
    payload['routes'].append({'id': 'other', 'path': [
        {'edge_id': key, 'direction': 'forward'} for key in ('other-a', 'other-b')
    ], 'extensions': {}})
    payload['trains'].append({'id': 'other', 'route_id': 'other', 'stops': [
        {'node_id': n, 'arrival_s': i * 100, 'departure_s': i * 100}
        for i, n in enumerate((6, 7, 8))
    ], 'extensions': {}})
    points = [{'properties': {'osm_node_id': node, 'source_station_node': 99,
                              'name': '共同车站'}} for node in (3, 7)]
    repo, bindings = build_repository({'edges': edges, 'points': points}, payload,
                                      tmp_path / 'workspace.sqlite')
    assert bindings['stations']['3'] == bindings['stations']['7']
    for train in payload['trains']:
        run = repo.train_runs[bindings['train_runs'][train['id']]]
        refs = repo.corridors[run.corridor_id].edge_refs
        distances = stop_distances(repo, refs, repo.stops_for(run.id))
        assert 0 == distances[0] < distances[1] < distances[2]


def test_desktop_plan_adapts_to_one_shared_domain_contract(tmp_path):
    from desktop.domain_adapter import build_repository

    payload, edges = fixture()
    graph = {"edges": edges, "points": [
        {"type": "Feature", "properties": {"osm_node_id": i, "kind": "station", "name": f"站{i}"},
         "geometry": {"type": "Point", "coordinates": [121 + i / 100, 31]}}
        for i in range(1, 6)
    ]}
    repo, bindings = build_repository(graph, payload, tmp_path / "workspace.sqlite")
    assert len(repo.corridors) == 1
    assert len(repo.train_runs) == 3
    corridor = next(iter(repo.corridors.values()))
    assert [ref.sequence for ref in corridor.edge_refs] == [1, 2, 3, 4]
    assert len(repo.stops_for(bindings["train_runs"]["direct"])) == 2
    assert len(repo.stops_for(bindings["train_runs"]["all"])) == 5
    assert all(key.startswith("NE-") for key in repo.edges)
    again, again_bindings = build_repository(graph, payload, tmp_path / "workspace.sqlite")
    assert set(repo.edges) == set(again.edges)
    assert bindings == again_bindings


def test_rail_editor_keeps_canonical_repository_in_sync(tmp_path):
    from PySide6.QtWidgets import QApplication
    from desktop.rail_ui import RailEditor
    from desktop.tests.test_operating_ui import MapStub

    app = QApplication.instance() or QApplication([])
    editor = RailEditor(MapStub(), tmp_path, tmp_path / "plan.json")
    assert editor.domain_repo is not None
    assert len(editor.domain_repo.corridors) == len(editor.rail_payload["routes"])
    assert len(editor.domain_repo.train_runs) == len(editor.rail_payload["trains"])
    editor.timer.stop()
    editor.close()
