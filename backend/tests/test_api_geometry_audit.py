from dataclasses import replace

from railscope import main
from railscope.demo import load_demo
from railscope.domain import BlockEdge, BlockSection
from railscope.services.blocks import create_virtual_blocks


def test_virtual_blocks_are_idempotent():
    repo = load_demo()
    create_virtual_blocks(repo)
    before = list(repo.block_edges)
    create_virtual_blocks(repo)
    assert repo.block_edges == before


def test_block_rendering_preserves_all_directed_members(monkeypatch):
    repo = load_demo()
    keys = list(repo.edges)[:2]
    repo.blocks = {'B': BlockSection('B', 'multi', 'a', 'b', 1)}
    repo.block_edges = [BlockEdge('B', keys[0], 1, True), BlockEdge('B', keys[1], 2, False)]
    monkeypatch.setattr(main, 'repo', repo)
    geometry = main.block_geojson()['features'][0]['geometry']
    assert geometry['type'] == 'MultiLineString'
    assert geometry['coordinates'] == [repo.edges[keys[0]].coordinates, repo.edges[keys[1]].coordinates[::-1]]


def test_edge_crossing_bbox_is_returned_even_without_vertex_inside(monkeypatch):
    repo = load_demo()
    original = next(iter(repo.edges.values()))
    crossing = replace(original, coordinates=((0, 0), (2, 2)))
    outside = replace(original, id='outside', coordinates=((0, 0), (.4, 2)))
    repo.edges = {crossing.id: crossing, outside.id: outside}
    monkeypatch.setattr(main, 'repo', repo)
    assert main.edges('0.9,0.9,1.1,1.1') == [crossing]
