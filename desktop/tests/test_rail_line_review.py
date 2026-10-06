from collections import Counter

from desktop.rail_semantics import semantic_record


def test_group_review_never_promotes_mixed_sections_to_one_class():
    from desktop.rail_line_review import review_group
    group = {'values': {'railway_class': Counter(high_speed=10, conventional=8),
                       'line_role': Counter(main_line=18), 'track_role': Counter(main_track=18),
                       'construction_status': Counter(operating=12, construction=6)},
             'design_speeds': Counter({350: 10, 160: 8}), 'edge_count': 18}
    result = review_group(group)
    assert result['railway_class'] == 'unknown'
    assert result['construction_status'] == 'unknown'
    assert result['speed_band'] == 'unknown'
    assert 'railway_class' in result['mixed_fields']


def test_reference_speed_does_not_confuse_operation_and_civil_design():
    from desktop.rail_line_review import reference_design
    profile = {'attributes': {'civil_design_speed_kmh': '380 km/h', 'max_speed_kmh': '310 km/h'},
               'scopes': [{'attributes': {'track_design_speed_kmh': '350 km/h'}}]}
    assert reference_design(profile) == (350, '300-350')
    profile['scopes'].append({'attributes': {'track_design_speed_kmh': '250 km/h'}})
    assert reference_design(profile) == (None, 'unknown')


def test_unchanged_reference_keeps_fresh_retrieval_date_and_richer_scopes(tmp_path, monkeypatch):
    import json
    from desktop import china_emu
    profile = {'id': 'reference/a', 'kind': 'line', 'name': '甲铁路', 'snapshot_id': 'sha256:unchanged',
        'attributes': {}, 'scopes': [{'name': '完整资料', 'attributes': {}}], 'retrieved_at': '2026-10-01'}
    shared, local = tmp_path/'shared.json', tmp_path/'local.json'
    shared.write_text(json.dumps({'schema': china_emu.SCHEMA, 'profiles': [profile]}), encoding='utf-8')
    local.write_text(json.dumps({'schema': china_emu.SCHEMA, 'profiles': [
        {**profile, 'scopes': [], 'retrieved_at': '2026-10-06'}]}), encoding='utf-8')
    monkeypatch.setattr(china_emu, 'SHARED_REFERENCE_PATH', shared)
    monkeypatch.setattr(china_emu, 'REFERENCE_PATH', local)
    refreshed = china_emu.load_store().profiles[0]
    assert refreshed['retrieved_at'] == '2026-10-06'
    assert refreshed['scopes'] == profile['scopes']


def test_automatic_whole_line_review_keeps_explicit_edge_facts():
    edit = {'rail_semantics': {'railway_class': 'unknown', 'construction_status': 'unknown',
            'track_role': 'main_track', 'source': 'rail_line_review',
            'verification_status': 'automatic_reference', 'scope': 'line_group'}}
    facts = semantic_record({'network_edge_id': 'NE-1', 'way_tags': {
        'railway': 'construction', 'construction': 'rail', 'highspeed': 'yes', 'service': 'yard'}}, edit)
    assert facts['railway_class'] == 'high_speed'
    assert facts['construction_status'] == 'construction'
    assert facts['track_role'] == 'unknown'
    assert facts['facility_only'] is True


def test_review_keeps_per_field_source_and_its_verification_status():
    proof = {'value': 'conventional', 'source': 'rail_line_review',
             'source_url': 'https://www.nra.gov.cn/tlfc/tpsy/202601/t20260121_350446.shtml',
             'snapshot_id': 'review-2026-10-06', 'verification_status': 'source_checked',
             'evidence': '国家铁路局资料明确区分京广普速线和京广高铁', 'confidence': .95}
    facts = semantic_record({'railway_class': 'unknown'}, {'rail_semantics': {
        'railway_class': 'conventional', 'source': 'rail_line_review',
        'verification_status': 'automatic_reference', 'provenance': {'railway_class': proof}}})
    assert facts['provenance']['railway_class'] == proof


def test_reference_matching_keeps_distinct_and_ambiguous_railways_separate():
    from desktop.rail_line_review import match_reference
    profiles = [{'name': '京沪铁路', 'attributes': {}, 'scopes': [], 'source_url': 'ordinary'},
                {'name': '京沪高速铁路', 'attributes': {}, 'scopes': [], 'source_url': 'highspeed'}]
    assert match_reference('京沪线', profiles)['source_url'] == 'ordinary'
    assert match_reference('京沪高速线', profiles)['source_url'] == 'highspeed'
    profiles.append({'name': '京沪铁路', 'attributes': {}, 'scopes': [], 'source_url': 'other'})
    assert match_reference('京沪线', profiles) is None


def test_whole_line_uses_all_design_sections_and_rejects_partial_alias():
    from desktop.rail_line_review import match_reference, reference_design
    profiles = [{'name': '沪昆高速铁路', 'source_url': 'whole', 'attributes': {},
        'scopes': [{'name': '沪昆高速铁路沪杭段', 'aliases': ['沪昆高速线'],
                    'attributes': {'track_design_speed_kmh': '350 km/h'}},
                   {'name': '沪昆高速铁路杭州段', 'aliases': ['沪昆高速线'],
                    'attributes': {'track_design_speed_kmh': '200 km/h'}}]},
        {'name': '大明湖-济南东联络线', 'source_url': 'partial', 'attributes': {},
         'scopes': [{'name': '胶济铁路济南段', 'aliases': ['胶济线'], 'attributes': {}}]}]
    assert reference_design(match_reference('沪昆高速线', profiles)) == (None, 'unknown')
    assert match_reference('胶济线', profiles) is None
    assert match_reference('胶济铁路济南段', profiles)['source_url'] == 'partial'
    from desktop.china_emu import ReferenceStore, line_reference
    store = ReferenceStore([{**p, 'kind': 'line'} for p in profiles])
    assert line_reference({'line_name': '胶济线'}, store) is None
    assert reference_design(line_reference({'line_name': '沪昆高速线'}, store)) == (None, 'unknown')


def test_construction_mode_and_explicit_freight_restriction_are_classified():
    from railscope.rail_semantics import classify_railway_class
    assert classify_railway_class({'railway': 'construction', 'construction': 'rail',
                                   'highspeed': 'no'}).value == 'conventional'
    assert classify_railway_class({'railway': 'rail', 'passenger': 'no'}).value == 'freight'
    assert classify_railway_class({'railway': 'rail', 'highspeed': 'yes', 'usage': 'freight'}).value == 'unknown'
    assert classify_railway_class({'railway': 'rail', 'maxspeed': '160'}).value == 'unknown'


def test_automatic_mixed_status_does_not_hide_construction_in_viewport(tmp_path):
    import json
    import sqlite3
    from desktop.rail_store import viewport
    feature = {'properties': {'catalog_group_id': 'RL-1', 'construction_status': 'construction'},
               'geometry': {'type': 'LineString', 'coordinates': [[120, 30], [120.01, 30]]}}
    with sqlite3.connect(tmp_path/'rail.sqlite') as db:
        db.execute('CREATE TABLE features(id PRIMARY KEY,kind,service,data)')
        db.execute('CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy)')
        db.execute('INSERT INTO features VALUES(1,?,?,?)', ('rail','main',json.dumps(feature)))
        db.execute('INSERT INTO bounds VALUES(1,120,120.01,30,30)')
    edits = {'RL-1': {'rail_semantics': {'source': 'rail_line_review', 'scope': 'line_group',
                                      'construction_status': 'unknown'}}}
    assert len(viewport(tmp_path,'rail',[119.9,29.9,120.1,30.1],14,
                        {'states':['construction']},overrides=edits)['features']) == 1


def test_completed_palette_covers_every_class_without_changing_custom_colors():
    from desktop.rail_line_review import reviewed_styles
    from desktop.rail_style_ui import defaults, validate_styles
    from desktop.rail_style_resolver import STYLE_SELECTIONS, CONFIGURED_STYLE_KEYS
    styles = defaults()
    styles['track.conventional.main_line']['color'] = '#123456'
    reviewed = validate_styles(reviewed_styles(styles))
    assert reviewed['track.conventional.main_line']['color'] == '#123456'
    assert set(reviewed[CONFIGURED_STYLE_KEYS]) == set(STYLE_SELECTIONS)
    assert reviewed['track.freight.unknown']['color'] == '#000000'


def test_unverified_legacy_type_cannot_defeat_unknown_audit():
    source = {'network_edge_id': 'NE-1', 'way_tags': {'railway': 'rail'},
              'track_type': '高速铁路线'}
    source.update(semantic_record(source))
    result = semantic_record(source, {'rail_semantics': {'railway_class': 'unknown',
        'source': 'rail_line_review', 'scope': 'line_group'}})
    assert result['railway_class'] == 'unknown'


def test_old_source_cache_cannot_defeat_new_conflicting_tag_review():
    source = {'network_edge_id': 'NE-1', 'way_tags': {
        'railway': 'rail', 'highspeed': 'yes', 'usage': 'freight'}, 'railway_class': 'high_speed',
        'provenance': {'railway_class': {'value': 'high_speed', 'source': 'OpenStreetMap',
                                       'verification_status': 'osm_explicit'}}}
    result = semantic_record(source, {'rail_semantics': {'railway_class': 'unknown',
        'source': 'rail_line_review', 'scope': 'line_group'}})
    assert result['railway_class'] == 'unknown'


def test_inspector_uses_selected_section_facts_in_a_mixed_line(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    from launcher import Desk
    class Field:
        def setText(self, value): self.value = value
        def setPlainText(self, value): self.value = value
        def setChecked(self, value): pass
        def show(self): pass
    semantics = {'railway_class': 'unknown', 'line_role': 'main_line',
        'track_role': 'main_track', 'construction_status': 'unknown',
        'source': 'rail_line_review', 'scope': 'line_group'}
    meta = {**semantics, 'name': '甲线', 'rail_semantics': semantics,
        'technical_attributes': {'speed_band': 'unknown', 'operating_status': '状态待核实'},
        'classification_review': {'mixed_fields': ['construction_status']}}
    edit = {'rail_semantics': semantics}
    rows = []
    inspector = SimpleNamespace(config={'railDisplayOverrides': {'RL-a': edit}},
        rail_catalog_widget=SimpleNamespace(catalog={'RL-a': meta}, overrides={'RL-a': edit},
            meta=lambda key: meta, display_name=lambda key: '甲线',
            effective_directory_path=lambda key: ('轨道线', '类别待核实', '正线')),
        route_lookup={}, selected_title=Field(), selected_type=Field(), raw=Field(),
        right=Field(), detail_rail=Field(), set_property_rows=rows.extend,
        _restore_inspector_width=lambda: None, _rail_station_record_for_feature=lambda feature: None)
    Desk.display_feature(inspector, {'layer': 'rail', 'properties': {
        'catalog_group_id': 'RL-a', 'network_edge_id': 'NE-1',
        'way_tags': {'railway': 'construction', 'construction': 'rail', 'highspeed': 'yes',
                     'usage': 'main', 'designspeed': '250'}}})
    assert ('铁路类别', '高速铁路') in rows
    assert ('运营状态', '在建') in rows
    assert ('速度范围', '250–300 km/h') in rows
    assert ('分类复查', '区段属性不同，请按区段查看') in rows


def test_full_review_replays_safely_and_keeps_manual_assembly_facts(tmp_path, monkeypatch):
    import hashlib
    import json
    import sqlite3
    from scripts import review_rail_lines as cli
    from desktop.catalog_workspace import read_overrides
    from desktop.rail_line_workspace import effective_override
    directory = tmp_path/'source'; directory.mkdir()
    for folder in ('data/catalog', 'data/user_settings', 'review'):
        (tmp_path/folder).mkdir(parents=True)
    for file in ('rail_line_directory.json', 'rail_station_directory.json', 'rail_catalog_overrides.json'):
        (tmp_path/'data/catalog'/file).write_text('{}', encoding='utf-8')
    marker = 'line-assembly:RLU-a'
    edits = {'RL-a': {'assembly_id': 'RLU-a'}, 'RL-b': {'assembly_id': 'RLU-a'}, marker: {
        'active': True, 'members': ['RL-a', 'RL-b'], 'attributes': {'display_name': '人工合并线',
        'line_kind': 'track', 'folder_path': ['旧目录'], 'rail_semantics': {
            'railway_class': 'freight', 'line_role': 'main_line', 'track_role': 'main_track',
            'verification_status': 'user_verified', 'source': 'workspace_override'}}}}
    settings = tmp_path/'data/user_settings/rail_catalog.json'
    settings.write_text(json.dumps(edits, ensure_ascii=False), encoding='utf-8')
    (tmp_path/'review/references.json').write_text('{"profiles":[]}', encoding='utf-8')
    with sqlite3.connect(directory/'rail.sqlite') as db:
        db.executescript('CREATE TABLE features(id,kind,data); CREATE TABLE rail_feature_groups(feature_id,group_id);')
        for index, ident in enumerate(('RL-a', 'RL-b', 'RL-c'), 1):
            tags = {'railway': 'rail' if index == 1 else 'construction',
                    'highspeed': 'no', 'usage': 'main'}
            if index == 2: tags['construction'] = 'rail'
            if index == 3: tags = {'railway': 'rail', 'usage': 'main'}
            f = {'properties': {'line_id': ident, 'line_name': ident, 'way_tags': tags},
                 'geometry': {'type': 'LineString', 'coordinates': [[120,30], [120.01,30]]}}
            db.execute('INSERT INTO features VALUES(?,?,?)', (index,'rail',json.dumps(f)))
            db.execute('INSERT INTO rail_feature_groups VALUES(?,?)', (index,ident))
    with sqlite3.connect(directory/'rail_catalog.sqlite') as db:
        db.execute('CREATE TABLE catalog(id,data)')
        db.executemany('INSERT INTO catalog VALUES(?,?)',
            [(key,json.dumps({'name': key, 'line_name': key, 'track_type': '普速铁路线'})) for key in ('RL-a','RL-b','RL-c')])
    with sqlite3.connect(directory/'rail_lines.sqlite') as db:
        db.execute('CREATE TABLE lines(id,source_name)')
        db.executemany('INSERT INTO lines VALUES(?,?)', [('RL-a','甲线'),('RL-b','乙线'),('RL-c','丙线')])
    before = hashlib.sha256((directory/'rail.sqlite').read_bytes()).hexdigest()
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    monkeypatch.setattr(cli, 'active_rail_directory', lambda root: directory)
    monkeypatch.setattr('sys.argv', ['review_rail_lines', '--output', str(tmp_path/'review'), '--apply'])
    cli.main()
    reviewed = read_overrides(settings)
    for key in ('RL-a', 'RL-b'):
        edit = effective_override(reviewed, key)
        assert edit['rail_semantics']['railway_class'] == 'freight'
        assert edit['rail_semantics']['provenance']['railway_class']['verification_status'] == 'user_verified'
        assert edit['rail_semantics']['construction_status'] == 'unknown'
        assert edit['folder_path'] == ['轨道线', '货运铁路', '正线']
        assert edit['classification_review']['conflicts'] == ['railway_class']
    assert reviewed[marker]['members'] == edits[marker]['members']
    assert reviewed['RL-c']['rail_semantics']['railway_class'] == 'unknown'
    assert reviewed['RL-c']['folder_path'] == ['轨道线', '类别待核实', '正线']
    cli.main()
    assert json.loads((tmp_path/'review/proposed-overrides.json').read_text())['changes'] == {}
    assert hashlib.sha256((directory/'rail.sqlite').read_bytes()).hexdigest() == before
