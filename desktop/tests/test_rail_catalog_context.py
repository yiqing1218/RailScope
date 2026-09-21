import json
from copy import deepcopy

from desktop.rail_catalog_ui import RailCatalog
from desktop.tests.test_operating_ui import MapStub


def make_catalog(qtbot, tmp_path):
    source = {
        key: {
            "name": name,
            "province": "上海市",
            "corridor": "不适用",
            "section": "虹桥站",
            "way_ids": [way],
            "track_type": "站场股道（类型待核对）",
        }
        for key, name, way in [
            ("track20", "上海虹桥站20道", 20),
            ("track21", "上海虹桥站21道", 21),
        ]
    }
    path = tmp_path / "rail_catalog.json"
    path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", MapStub())
    qtbot.addWidget(widget)
    return widget, path


def test_context_rename_move_archive_restore_persist_without_source_edits(
    qtbot, tmp_path, monkeypatch
):
    from PySide6.QtWidgets import QInputDialog

    widget, path = make_catalog(qtbot, tmp_path)
    original_file, original_catalog = path.read_bytes(), deepcopy(widget.catalog)
    original_id = "track20"
    menu = widget.item_menu(widget.items["track20"])
    assert [action.text() for action in menu.actions() if not action.isSeparator()] == [
        "重命名…",
        "移动到文件夹…",
        "查看端点与相邻线段…",
        "归档",
    ]
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("虹桥20道（自用）", True)
    )
    menu.actions()[0].trigger()
    assert widget.items["track20"].text(0).startswith("虹桥20道（自用）")
    assert widget.items["track20"].data(0, 0x0100) == original_id
    widget.move_items({"track20"}, ["我的目录", "虹桥站", "站台"])
    assert widget.parents("track20") == ("我的目录", "虹桥站", "站台")
    widget.toggle("track20", True)
    menu = widget.item_menu(widget.items["track20"])
    menu.actions()[-1].trigger()
    assert "track20" not in widget.visible
    assert widget.parents("track20")[0] == "已归档"
    assert not widget.tree.itemWidget(widget.items["track20"], 1).isEnabled()
    widget.set_all(True)
    assert widget.visible == {"track21"}
    widget.toggle("track20", True)
    assert widget.visible == {"track21"}
    restored = RailCatalog(tmp_path, tmp_path / "settings.json", MapStub())
    qtbot.addWidget(restored)
    assert restored.parents("track20") == ("已归档", "我的目录", "虹桥站", "站台")
    assert restored.display_name("track20").startswith("虹桥20道（自用）")
    menu = restored.item_menu(restored.items["track20"])
    assert menu.actions()[-1].text() == "取消归档 / 恢复"
    menu.actions()[-1].trigger()
    assert restored.parents("track20") == ("我的目录", "虹桥站", "站台")
    assert restored.visible == set()
    assert widget.catalog == original_catalog
    assert path.read_bytes() == original_file


def test_folder_rename_preserves_descendants_and_archive_is_reversible(qtbot, tmp_path):
    widget, path = make_catalog(qtbot, tmp_path)
    original = path.read_bytes()
    widget.move_items({"track20"}, ["站场", "到发线"])
    widget.move_items({"track21"}, ["站场", "调车线"])
    widget.rename_folder(("站场",), "虹桥站场")
    assert widget.parents("track20") == ("虹桥站场", "到发线")
    assert widget.parents("track21") == ("虹桥站场", "调车线")
    group = widget.groups[("虹桥站场",)]
    widget.item_menu(group).actions()[-1].trigger()
    assert all(widget.parents(key)[0] == "已归档" for key in widget.catalog)
    widget.item_menu(widget.groups[("已归档",)]).actions()[-1].trigger()
    assert widget.parents("track20") == ("虹桥站场", "到发线")
    widget.mode.setCurrentIndex(1)
    assert widget.parents("track20") == ("虹桥站场", "到发线")
    assert path.read_bytes() == original


def test_failed_directory_save_leaves_state_and_map_unchanged(
    qtbot, tmp_path, monkeypatch
):
    import pytest
    from pathlib import Path

    widget, _ = make_catalog(qtbot, tmp_path)
    widget.toggle("track20", True)
    previous_calls = list(widget.map.calls)
    monkeypatch.setattr(
        Path,
        "replace",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("保存失败")),
    )
    with pytest.raises(OSError):
        widget.archive_items({"track20"})
    assert widget.overrides == {}
    assert widget.visible == {"track20"}
    assert widget.map.calls == previous_calls


def test_move_context_action_opens_editable_folder_dialog(qtbot, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QComboBox, QDialogButtonBox

    widget, _ = make_catalog(qtbot, tmp_path)
    problems = []

    def choose():
        dialog = QApplication.activeModalWidget()
        try:
            destination = dialog.findChild(QComboBox)
            destination.setCurrentText("工作区 / 站场 / 自定义线路")
            dialog.findChild(QDialogButtonBox).button(
                QDialogButtonBox.StandardButton.Ok
            ).click()
        except Exception as error:
            problems.append(error)
        finally:
            dialog.reject()

    QTimer.singleShot(30, choose)
    widget.item_menu(widget.items["track20"]).actions()[1].trigger()
    assert not problems
    assert widget.parents("track20") == ("工作区", "站场", "自定义线路")


def test_large_directory_prioritizes_named_business_lines_and_omits_station_groups(
    qtbot, tmp_path
):
    source = {
        **{
            f"RL-U-{index}": {
                "name": f"未命名轨道·w{index}",
                "line_id": f"RL-U-{index}",
                "way_ids": [index],
                "track_type": "未确认类型",
            }
            for index in range(4100)
        },
        "RL-NAMED": {
            "name": "京沪高速铁路",
            "line_id": "RL-NAMED",
            "way_ids": [5000],
            "track_type": "高速铁路线",
        },
        "ST-YARD": {
            "name": "上海虹桥站",
            "line_id": None,
            "way_ids": [5001],
            "track_type": "高速铁路站场股道",
        },
    }
    (tmp_path / "rail_catalog.json").write_text(
        json.dumps(source, ensure_ascii=False), encoding="utf-8"
    )
    widget = RailCatalog(tmp_path, tmp_path / "settings.json", MapStub())
    qtbot.addWidget(widget)
    widget.resize(380, 900)
    widget.show()
    qtbot.wait(1)
    assert "RL-NAMED" in widget.items
    assert "ST-YARD" not in widget.items
    assert not any(key.startswith("RL-U-") for key in widget.items)
    assert widget.tree.topLevelItemCount() > 0
    assert widget.tree.verticalScrollBar().value() == 0
    assert widget.tree.y() < 80
