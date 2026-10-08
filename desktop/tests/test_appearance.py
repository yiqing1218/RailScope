import json

from PySide6.QtWidgets import QApplication, QFrame, QWidget


def test_appearance_roundtrip_and_invalid_values(tmp_path):
    from appearance import DEFAULT, load, normalize, save

    path = tmp_path / "appearance.json"
    assert load(path) == DEFAULT
    value = {"palette": "ocean", "glass": True, "opacity": 82, "density": "compact"}
    assert save(path, value) == value
    assert load(path) == value
    assert normalize({"palette": "invalid", "opacity": 0}) == DEFAULT
    path.write_text("bad json", encoding="utf-8")
    assert load(path) == DEFAULT


def test_all_palettes_style_native_and_web_controls():
    from appearance import DEFAULT, PALETTES, stylesheet, web_theme

    for key in PALETTES:
        settings = {**DEFAULT, "palette": key}
        css = stylesheet(settings)
        assert "QTreeView" in css and "QDoubleSpinBox" in css
        assert web_theme(settings)["accent"] in css
        assert "QScrollArea > QWidget" in css


def test_panels_overlay_map_and_release_space_when_hidden(qtbot):
    from glass_workspace import GlassPanel, MapWorkspace

    map_widget, left, right, rail = QWidget(), GlassPanel(), GlassPanel(), QFrame()
    workspace = MapWorkspace(map_widget, left, right, rail)
    qtbot.addWidget(workspace)
    workspace.resize(1200, 720)
    workspace.show()
    QApplication.processEvents()
    assert map_widget.geometry() == workspace.rect()
    assert left.geometry().right() < right.geometry().left()
    assert workspace.insets()["left"] > left.width()
    left.hide()
    QApplication.processEvents()
    assert workspace.insets()["left"] < 100
    assert map_widget.width() == 1200


def test_detail_values_follow_the_theme_instead_of_fixed_dark_ink(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPalette
    from appearance import DEFAULT, native_palette, web_theme
    from property_overview import PropertyOverview

    table = PropertyOverview()
    qtbot.addWidget(table)
    theme = {**DEFAULT, 'palette': 'graphite'}
    table.setPalette(native_palette(theme))
    table.set_rows([('校园名称', '复旦大学')])
    assert table.item(1, 0).data(Qt.ItemDataRole.ForegroundRole) is None
    assert table.palette().color(QPalette.ColorRole.Text).name() == web_theme(theme)['text']
    assert table.palette().color(QPalette.ColorRole.PlaceholderText).name() == web_theme(theme)['muted']


def test_native_panels_show_live_map_without_capturing_any_backdrop(qtbot):
    from PySide6.QtGui import QColor, QPainter
    from glass_workspace import GlassPanel, MapWorkspace

    class AnimatedMap(QWidget):
        color = '#ff0000'
        def grab(self, *args):
            raise AssertionError('Live native transparency must never sample the map')
        def paintEvent(self, event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(self.color))

    canvas = AnimatedMap()
    panels = [GlassPanel() for _ in range(3)]
    workspace = MapWorkspace(canvas, *panels)
    qtbot.addWidget(workspace)
    workspace.resize(1200,720);workspace.show()
    QApplication.processEvents()
    point = panels[0].geometry().center()
    assert workspace.grab().toImage().pixelColor(point).name() == '#ff0000'
    canvas.color = '#0000ff';canvas.update()
    QApplication.processEvents()
    assert workspace.grab().toImage().pixelColor(point).name() == '#0000ff'
    assert workspace.insets()['panels']['left']['visible']
    panels[0].hide();QApplication.processEvents()
    assert not workspace.insets()['panels']['left']['visible']
