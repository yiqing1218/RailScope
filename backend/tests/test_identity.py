from dataclasses import replace
from railscope.domain import DatasetSnapshot
from railscope.identity import IdentityRegistry


def edge(key='w10:0-1',nodes=(1,2),coords=((120,30),(120.01,30))):
    return dict(id=key,from_node=nodes[0],to_node=nodes[-1],node_ids=list(nodes),coordinates=[list(p) for p in coords],way_tags={'railway':'rail','name':'测试线'})


def snapshot(key):
    return DatasetSnapshot(key,'rail','osm','2026-09-18T00:00:00Z','2026-09-18',key)


def test_shape_vertex_and_way_rename_preserve_identity_orientation_and_history(tmp_path):
    registry=IdentityRegistry(tmp_path/'identity.sqlite')
    first=registry.prepare([edge()],snapshot('s1')); registry.commit(first)
    second=registry.prepare([edge('w99:0-2',(2,3,1),((120.01,30),(120.005,30),(120,30)))],snapshot('s2'))
    assert first.edges[0]['id']==second.edges[0]['id']
    assert first.edges[0]['line_id']==second.edges[0]['line_id']
    assert second.edges[0]['from_node']==1
    assert second.changes[0]['status']=='geometry_changed'
    registry.commit(second)
    assert registry.snapshot_edges('s1')[0]['coordinates']==[[120,30],[120.01,30]]


def test_split_keeps_old_reference_unbound_and_emits_conflict(tmp_path):
    registry=IdentityRegistry(tmp_path/'identity.sqlite')
    first=registry.prepare([edge(nodes=(1,3,2),coords=((120,30),(120.005,30),(120.01,30)))],snapshot('s1'));registry.commit(first)
    second=registry.prepare([edge('w10:0-1',(1,3),((120,30),(120.005,30))),edge('w10:1-2',(3,2),((120.005,30),(120.01,30)))],snapshot('s2'))
    assert first.edges[0]['id'] not in {e['id'] for e in second.edges}
    assert {e['line_id'] for e in second.edges} == {first.edges[0]['line_id']}
    assert any(c['status']=='split' for c in second.changes)
    assert second.conflicts


def test_parallel_tracks_ambiguous_not_silently_rebound(tmp_path):
    registry=IdentityRegistry(tmp_path/'identity.sqlite')
    first=registry.prepare([edge('w1:0-1'),edge('w2:0-1')],snapshot('s1'));registry.commit(first)
    second=registry.prepare([edge('w3:0-1')],snapshot('s2'))
    assert second.conflicts[0]['kind']=='ambiguous_match'
    assert second.edges[0]['id'] not in {e['id'] for e in first.edges}


def test_replaced_osm_endpoint_alias_keeps_internal_node_and_edge_ids(tmp_path):
    registry=IdentityRegistry(tmp_path/'identity.sqlite')
    first=registry.prepare([edge()],snapshot('s1'));registry.commit(first)
    changed=edge('w10:0-1',(101,202),((120,30),(120.01,30)))
    second=registry.prepare([changed],snapshot('s2'))
    assert second.edges[0]['id']==first.edges[0]['id']
    assert second.edges[0]['from_node_id']==first.edges[0]['from_node_id']
    assert second.edges[0]['to_node_id']==first.edges[0]['to_node_id']
    assert second.nodes['osm/node/101']['id']==first.edges[0]['from_node_id']
