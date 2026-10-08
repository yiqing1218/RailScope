from font_settings import DEFAULT, load, normalize, save, stylesheet


def test_fonts_are_independent_and_roundtrip(tmp_path):
    value = normalize(None)
    value['directory'] = {'family': '宋体', 'size': 15}
    path = tmp_path / 'fonts.json'
    save(path, value)
    assert load(path) == value
    assert load(path)['menu'] == DEFAULT['menu']
    assert "QTreeView, QTreeWidget, QListView { font-family:'宋体'; font-size:15px; }" in stylesheet(value)


def test_corrupt_font_entries_and_style_syntax_use_safe_defaults():
    assert normalize({'ui': None, 'map': {'family': "bad';color:red;", 'size': -1}}) == normalize(None)
