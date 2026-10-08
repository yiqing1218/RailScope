from train_label_settings import DEFAULT, load, save, normalize


def test_train_label_size_and_zoom_survive_restart_and_reject_invalid_values(tmp_path):
    path = tmp_path/'labels.json'
    assert load(path) == DEFAULT
    assert save(path, {'size': 22, 'minZoom': 8}) == {'size': 22, 'minZoom': 8}
    assert load(path) == {'size': 22, 'minZoom': 8}
    assert normalize({'size': True, 'minZoom': 23}) == DEFAULT
    assert normalize({'size': 32, 'minZoom': 0}) == {'size': 32, 'minZoom': 0}
    path.write_text('broken', encoding='utf-8')
    assert load(path) == DEFAULT


def test_native_menu_applies_both_settings_and_reopens_with_saved_values(tmp_path, qtbot, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QDialog, QMainWindow, QSpinBox
    import launcher
    monkeypatch.setattr(launcher, 'ROOT', tmp_path)
    window = QMainWindow()
    qtbot.addWidget(window)
    calls = []
    window.config = {'trainLabels': DEFAULT.copy()}
    window.map = SimpleNamespace(call=lambda *args: calls.append(args))
    def apply(dialog):
        assert dialog.windowTitle() == '车次号显示'
        controls = dialog.findChildren(QSpinBox)
        assert [c.value() for c in controls] == [12,11]
        controls[0].setValue(22);controls[1].setValue(8)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, 'exec', apply)
    launcher.Desk.edit_train_labels(window)
    assert calls == [('setTrainLabels', {'size':22, 'minZoom':8})]
    assert load(tmp_path/'data/user_settings/train_labels.json') == {'size':22, 'minZoom':8}
    def reopen(dialog):
        assert [c.value() for c in dialog.findChildren(QSpinBox)] == [22,8]
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog, 'exec', reopen)
    launcher.Desk.edit_train_labels(window)
    assert len(calls) == 1
