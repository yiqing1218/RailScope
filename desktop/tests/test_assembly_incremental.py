import json
import sqlite3

from desktop.catalog_workspace import CatalogWorkspace, edit_database
from desktop.rail_line_workspace import expand_assembly_changes


def test_shared_edit_writes_one_owner_and_bounded_history(tmp_path):
    path = tmp_path / 'rail_catalog.json'
    values = {f'RL-{i}': {'assembly_id': 'RLU-test'} for i in range(10000)}
    values['line-assembly:RLU-test'] = {'members': list(values), 'active': True,
        'attributes': {'display_name': 'Old', 'folder_path': ['Old']}}
    path.write_text(json.dumps(values), encoding='utf-8')
    workspace = CatalogWorkspace(path)
    workspace.load()
    class NoScan(dict):
        def items(self):
            raise AssertionError('shared edit scanned every override')
    changes = expand_assembly_changes(NoScan(workspace.values), {'RL-0': {'folder_path': ['New']}})
    assert set(changes) == {'line-assembly:RLU-test'}
    workspace.update(changes)
    with sqlite3.connect(edit_database(path)) as db:
        assert db.execute('SELECT count(*) FROM assembly_members').fetchone()[0] == 10000
        assert len(db.execute('SELECT delta FROM command_history ORDER BY id DESC LIMIT 1').fetchone()[0]) < 500
    workspace.undo()
    assert workspace.values['line-assembly:RLU-test']['attributes']['folder_path'] == ['Old']
    workspace.redo()
    reopened = CatalogWorkspace(path)
    reopened.load()
    assert reopened.values['line-assembly:RLU-test']['members'] == list(values)[:-1]
    assert reopened.values['line-assembly:RLU-test']['attributes']['folder_path'] == ['New']


def test_plain_http_overrides_inherit_names_styles_and_owner_keys():
    from desktop.display_names import apply_names, apply_rail_presentation, presentation_keys
    from desktop.rail_line_workspace import EffectiveOverrides
    metadata = EffectiveOverrides({'RL-a': {'assembly_id': 'RLU-shared', 'display_name': 'Old', 'line_name': 'Old'},
        'line-assembly:RLU-shared': {'active': True, 'name': 'New', 'attributes': {
            'display_name': 'New', 'line_name': 'New', 'color': '#123456', 'width': 3,
            'technical_attributes': {'design_speed_kmh': 350},
            'rail_semantics': {'railway_class': 'conventional', 'line_role': 'connecting_line', 'track_role': 'main_track'}}}})
    # HTTP configuration is an ordinary dict copied from the view, as at boot.
    plain = dict(metadata)
    feature = {'properties': {'catalog_group_id': 'RL-a', 'network_edge_id': 'NE-a', 'line_id': 'IL-a',
                             'track_role': 'main_track', 'name': 'Source'},
               'geometry': {'type': 'LineString', 'coordinates': [[120,30],[121,31]]}}
    collection = {'features': [feature]}
    apply_names(collection, plain)
    apply_rail_presentation(collection, {}, plain)
    props = feature['properties']
    assert props['display_name'] == props['line_name'] == props['line_display_name'] == 'New'
    assert props['rail_display_color'] == '#123456' and props['rail_display_width'] == 3
    assert 'line-assembly:RLU-shared' in presentation_keys(props)
    assert props['design_speed_kmh'] == 350
