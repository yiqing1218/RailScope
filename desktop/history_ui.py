"""Calendar editor and compact timeline; history is a canonical workspace override."""

from dataclasses import asdict, replace
from datetime import date
import sqlite3
from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QHBoxLayout,
    QDateEdit,
    QSlider,
    QPushButton,
    QLabel,
    QLineEdit,
    QComboBox,
    QDialogButtonBox,
    QMessageBox,
)
from railscope.domain import InfrastructureLifecycle
from railscope.identity import IdentityRegistry
from railscope.services.history import effective_lifecycle


class HistoryController:
    def __init__(self, desk, store, session):
        self.desk, self.store, self.session = desk, store, session
        self.records = store.collection("lifecycles")
        self.session.calendar_changed.connect(self.publish)
        self.dialog = None

    def effective_for_feature(self, props, mode):
        from display_names import presentation_keys

        keys = presentation_keys(props)
        direct = {
            key
            for key in keys
            if key.startswith(("object:", "station:", "node:", "switch:"))
        }
        if mode == 'rail' and direct:
            self.import_station_reference(props, direct)
        candidates = [
            r
            for r in self.records.values()
            if r.mode == mode and direct.intersection(r.source_aliases)
        ]
        if not candidates:
            keys.update(props.get("operating_line_ids") or [])
            keys.update(props.get("construction_line_ids") or [])
            if mode == "metro":
                keys.update(
                    "metro:relation/" + str(v)
                    for v in [
                        props.get("route_relation_id"),
                        props.get("osm_relation_id"),
                        *(props.get("route_relation_ids") or []),
                    ]
                    if v is not None
                )
            if mode == "road":
                keys.update(
                    "road:route/" + str(v) for v in props.get("route_keys") or []
                )
            candidates = [
                r
                for r in self.records.values()
                if r.mode == mode and keys.intersection(r.source_aliases)
            ]
        return (
            effective_lifecycle(self.records, candidates[0].id)
            if len(candidates) == 1
            else None
        )

    def import_station_reference(self, props, aliases):
        profile = props.get('external_reference')
        if not profile:
            try:
                from .china_emu import station_reference
            except ImportError:
                from china_emu import station_reference
            profile = station_reference(props,props)
        if not profile or profile.get('kind') != 'station':
            return
        try:
            from .reference_integration import reference_lifecycle
        except ImportError:
            from reference_integration import reference_lifecycle
        from display_names import object_key
        existing = [r for r in self.records.values() if r.mode=='rail' and set(r.source_aliases).intersection(aliases)]
        if len(existing)>1:
            return
        if existing and existing[0].source != 'china-emu.cn':
            return
        ident = existing[0].id if existing else IdentityRegistry(self.store.path).resolve_alias(
            'infrastructure_history', 'rail/'+(object_key(props) or min(aliases)), 'INF')
        value = reference_lifecycle(profile,ident,aliases,props.get('name') or profile['name'])
        existing_date = props.get('station_overview', {}).get('commissioning_date')
        if existing_date and len(existing_date)==10:
            try:
                date.fromisoformat(existing_date)
                value = replace(value,opened=existing_date,provenance={**value.provenance,
                    'opened':{'source':'existing_station_overview', 'value':existing_date},
                    'conflicts':props.get('reference_conflicts', {})})
            except ValueError:
                pass
        if self.store.seed_reference('lifecycles',value):
            self.records = self.store.collection('lifecycles')
            # Publish directly to avoid re-entering the details refresh.
            self.desk.map.call('setInfrastructureHistory', self.session.day,
                [asdict(effective_lifecycle(self.records,k)) for k in self.records])

    def publish(self):
        records = [
            asdict(effective_lifecycle(self.records, key)) for key in self.records
        ]
        self.desk.map.call("setInfrastructureHistory", self.session.day, records)
        if self.dialog and self.dialog.isVisible():
            self.date_input.blockSignals(True)
            self.date_input.setDate(QDate.fromString(self.session.day, "yyyy-MM-dd"))
            self.date_input.blockSignals(False)
            self.slider.blockSignals(True)
            self.slider.setValue(int(self.session.day[:4]))
            self.slider.blockSignals(False)
            self.status.setText(
                ("当前实际日期 · " if self.session.current_date else "回溯日期 · ")
                + self.session.day
            )
        if hasattr(self.desk, "detail_views") and hasattr(self.desk, "workbench"):
            self.desk.workbench.refresh_history_details()

    def show(self):
        if self.dialog is None:
            self.dialog = QDialog(self.desk)
            self.dialog.setWindowTitle("铁路 / 地铁 / 公路 · 时间回溯")
            self.dialog.resize(720, 230)
            layout = QVBoxLayout(self.dialog)
            self.status = QLabel()
            layout.addWidget(self.status)
            self.slider = QSlider(Qt.Orientation.Horizontal)
            self.slider.setRange(1800, 2100)
            self.slider.setValue(date.today().year)
            self.slider.valueChanged.connect(
                lambda year: self.session.set_day(f"{year:04d}-01-01")
            )
            layout.addWidget(self.slider)
            row = QHBoxLayout()
            self.date_input = QDateEdit()
            self.date_input.setCalendarPopup(True)
            self.date_input.setDisplayFormat("yyyy-MM-dd")
            self.date_input.setDateRange(QDate(1800, 1, 1), QDate(2200, 12, 31))
            self.date_input.dateChanged.connect(
                lambda d: self.session.set_day(d.toString("yyyy-MM-dd"))
            )
            row.addWidget(self.date_input)
            current = QPushButton("返回实际日期")
            current.clicked.connect(lambda: self.session.set_day())
            row.addWidget(current)
            edit = QPushButton("编辑选中对象的时间")
            edit.clicked.connect(self.edit_selected)
            row.addWidget(edit)
            layout.addLayout(row)
            hint = QLabel(
                "建设前不显示；建设至开通为虚线；开通后运营；停运后灰色。未知日期保持未知，原始 OSM 不变。\n预设开通日期在实际日期到达后自动生效；无停运日期持续到未来。"
            )
            hint.setWordWrap(True)
            layout.addWidget(hint)
        self.dialog.show()
        self.publish()
        self.dialog.raise_()

    def edit_selected(self):
        try:
            from display_names import object_key, presentation_keys

            props = self.desk.selected_data.get("properties", {})
            layer = self.desk.selected_data.get("layer", "")
            if not props:
                raise ValueError("请先在地图或目录中选择基础设施对象")
            mode = (
                "metro"
                if layer
                in ("metro", "stations", "construction", "areas-fill", "areas-outline")
                else "road"
                if layer.startswith("road")
                else "other"
                if layer.startswith("imported")
                else "rail"
            )
            alias = object_key(props)
            if not alias:
                raise ValueError("当前对象尚无稳定来源引用")
            is_line = self.desk.selected_data.get("geometry", {}).get("type") in (
                "LineString",
                "MultiLineString",
            ) or layer in (
                "rail",
                "rail-stripes",
                "rail-construction",
                "rail-line-labels",
                "metro",
                "construction",
                "road",
                "road-construction",
            )
            primary = (
                (props.get("line_id") or props.get("catalog_group_id") or alias)
                if is_line
                else alias
            )
            if (
                is_line
                and mode == "metro"
                and (props.get("route_relation_id") or props.get("osm_relation_id"))
            ):
                primary = "metro:relation/" + str(
                    props.get("route_relation_id") or props.get("osm_relation_id")
                )
            if (
                is_line
                and mode == "road"
                and props.get("route_keys")
                and len(props["route_keys"]) == 1
            ):
                primary = "road:route/" + props["route_keys"][0]
            if is_line:
                keys = tuple(
                    sorted(
                        {
                            key
                            for key in (
                                primary,
                                props.get("line_id"),
                                props.get("catalog_group_id"),
                                "line-assembly:" + props["assembly_id"]
                                if props.get("assembly_id")
                                else None,
                            )
                            if key
                        }
                    )
                )
            else:
                keys = tuple(
                    sorted(
                        {
                            key
                            for key in presentation_keys(props)
                            if key == alias
                            or key.startswith(("station:", "node:", "switch:"))
                        }
                    )
                )
            if mode=='rail' and not is_line:
                self.import_station_reference(props, keys)
            existing = next(
                (
                    v
                    for v in self.records.values()
                    if v.mode == mode and primary in v.source_aliases
                ),
                None,
            )
            ident = (
                existing.id
                if existing
                else IdentityRegistry(self.store.path).resolve_alias(
                    "infrastructure_history", mode + "/" + str(primary), "INF"
                )
            )
            value = existing or InfrastructureLifecycle(
                ident,
                mode=mode,
                source_aliases=keys,
                display_name=str(
                    props.get("line_display_name")
                    or props.get("display_name")
                    or props.get("name")
                    or primary
                ),
            )
            dialog = QDialog(self.desk)
            dialog.setWindowTitle(
                "基础设施历史 · "
                + str(props.get("display_name") or props.get("name") or ident)
            )
            form = QFormLayout(dialog)
            scope = QComboBox()
            scope.addItem(
                "整条线路（成员线段和有唯一归属的站点继承）"
                if is_line
                else "当前设施对象",
                primary,
            )
            if is_line and primary != alias:
                scope.addItem("仅当前物理对象", alias)
            form.addRow("作用范围", scope)
            fields = {}
            for field, label in (
                ("construction_started", "开始建设"),
                ("opened", "开通运营"),
                ("closed", "结束运营"),
            ):
                edit = QLineEdit(getattr(value, field) or "")
                edit.setPlaceholderText("YYYY-MM-DD；未知留空")
                fields[field] = edit
                form.addRow(label, edit)
            parent = QComboBox()
            parent.addItem("独立时间范围", None)
            for other in self.records.values():
                if other.id != ident and other.mode == mode:
                    parent.addItem(
                        other.display_name
                        or " / ".join(other.source_aliases[:1])
                        or other.id,
                        other.id,
                    )
            parent.setCurrentIndex(max(0, parent.findData(value.parent_id)))
            form.addRow("继承线路时间", parent)
            preview = QLabel()
            preview.setWordWrap(True)
            form.addRow("时间段", preview)

            def update():
                preview.setText(
                    "建设 "
                    + (fields["construction_started"].text() or "未知")
                    + " ── 开通 "
                    + (fields["opened"].text() or "未知")
                    + " ── "
                    + (fields["closed"].text() or "→ 未来")
                )

            for edit in fields.values():
                edit.textChanged.connect(update)
            update()
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Save
                | QDialogButtonBox.StandardButton.Cancel
            )
            form.addRow(buttons)
            buttons.rejected.connect(dialog.reject)

            def save():
                try:
                    owner = scope.currentData()
                    current = value
                    if owner != primary:
                        current = next(
                            (
                                r
                                for r in self.records.values()
                                if r.mode == mode and owner in r.source_aliases
                            ),
                            None,
                        ) or replace(
                            value,
                            id=IdentityRegistry(self.store.path).resolve_alias(
                                "infrastructure_history", mode + "/" + owner, "INF"
                            ),
                            source_aliases=(owner,),
                        )
                    edited = replace(
                        current,
                        **{
                            key: edit.text().strip() or None
                            for key, edit in fields.items()
                        },
                        parent_id=parent.currentData(),
                        source='manual', verification_status='user_defined', confidence=None,
                    )
                    self.store.put("lifecycles", edited)
                    self.records = self.store.collection("lifecycles")
                    self.publish()
                    dialog.accept()
                except (ValueError, OSError, sqlite3.Error) as error:
                    QMessageBox.warning(dialog, "历史未保存", str(error))

            buttons.accepted.connect(save)
            dialog.exec()
        except (ValueError, OSError, sqlite3.Error) as error:
            QMessageBox.warning(self.desk, "历史对象无法编辑", str(error))
