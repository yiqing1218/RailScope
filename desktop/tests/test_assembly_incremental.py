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
