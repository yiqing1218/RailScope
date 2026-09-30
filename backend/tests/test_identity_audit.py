from railscope.domain import DatasetSnapshot
from railscope.identity import IdentityRegistry


def snapshot(number):
    return DatasetSnapshot(f'S{number}', 'test', 'synthetic', '2026-09-29', '2026-09-29', str(number))


def edge(way, nodes, coordinates=None):
    return {'id': f'w{way}:0', 'osm_way_id': str(way), 'from_node': nodes[0], 'to_node': nodes[-1],
            'node_ids': nodes, 'coordinates': coordinates or [[n / 10000, 30] for n in nodes],
            'way_tags': {'railway': 'rail'}}


def test_identity_source_fallback_preserves_reference_and_orientation(tmp_path):
    registry = IdentityRegistry(tmp_path / 'identity.sqlite')
    first = registry.prepare([edge(10, [1, 2])], snapshot(1))
    registry.commit(first)
    second = registry.prepare([edge(10, [4, 3], [[.0002, 30], [.0001, 30]])], snapshot(2))
    assert second.edges[0]['id'] == first.edges[0]['id']
    assert second.edges[0]['from_node_id'] == first.edges[0]['from_node_id']
    assert second.edges[0]['coordinates'] == first.edges[0]['coordinates']
    assert not second.conflicts


def test_identity_split_reports_both_new_edges_and_keeps_prepare_read_only(tmp_path):
    registry = IdentityRegistry(tmp_path / 'identity.sqlite')
    first = registry.prepare([edge(10, [1, 2, 3, 4])], snapshot(1))
    registry.commit(first)
    # Split pieces exceed geometry matching tolerance and share two raw nodes each.
    second = registry.prepare([edge(11, [1, 2]), edge(12, [3, 4])], snapshot(2))
    migration = next(c for c in second.conflicts if c['kind'] == 'reference_migration_required')
    assert migration['status'] == 'split'
    assert migration['old_ids'] == [first.edges[0]['id']]
    assert migration['new_ids'] == [e['id'] for e in second.edges]
    assert registry.snapshot_edges('S2') == []
