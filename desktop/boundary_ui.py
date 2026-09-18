"""Coverage audit distinguishes real platforms from station outlines and missing data."""

import json
from pathlib import Path
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QFileDialog,
    QHeaderView,
    QMessageBox,
)


def audit(stations, areas):
    related = {}
    for area in areas:
        for node in area["properties"].get("associated_station_ids", []):
            related.setdefault(node, []).append(area["properties"])
    records = []
    for station in stations:
        props = station["properties"]
        node = props.get("osm_node_id")
        matches = related.get(node, [])
        platform = any(a.get("boundary_kind") == "platform" for a in matches)
        status = (
            "有站台轮廓"
            if platform
            else "仅站区 / 建筑轮廓，站台待补"
            if matches
            else "无已关联真实轮廓"
        )
        records.append(
            {
                "osm_node_id": node,
                "name": props.get("name", "未命名"),
                "route_relation_ids": props.get("route_relation_ids", []),
                "status": status,
                "boundaries": [
                    {
                        "osm_way_id": a.get("osm_way_id"),
                        "osm_relation_id": a.get("osm_relation_id"),
                        "boundary_kind": a.get("boundary_kind", "station_outline"),
                        "retained_previous_snapshot": a.get(
                            "retained_previous_snapshot", False
                        ),
                        "mode_source": a.get("mode_source", "OSM 明确标注"),
                    }
                    for a in matches
                ],
            }
        )
    return records


class BoundaryDialog(QDialog):
    def __init__(self, stations, areas, parent=None, *, records=None):
        super().__init__(parent)
        self.records = audit(stations, areas) if records is None else records
        self.setWindowTitle("车站真实轮廓覆盖检查")
        self.resize(1000, 700)
        layout = QVBoxLayout(self)
        hint = QLabel(
            "站台 ≠ 站区 ≠ 建筑。轮廓在缩放级别 ≥12 才显示，还受车站及线路开关控制。空间关联待复核不代表官方边界。OSM 没有绘制的轮廓不能自动找全。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        national = records is not None
        table = QTableWidget(len(self.records), 5 if national else 4)
        table.setHorizontalHeaderLabels(
            [
                "车站",
                "共用基础设施编号",
                "轮廓状态",
                "实际轮廓类型",
                "高铁属性 / 判定状态",
            ]
            if national
            else ["车站", "OSM 节点", "轮廓状态", "关联线路 ID"]
        )
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table)
        for row, record in enumerate(self.records):
            values = (
                record["name"],
                record.get("osm_node_id", record.get("station_id", "")),
                record["status"],
                ",".join(
                    map(
                        str,
                        record.get(
                            "route_relation_ids", record.get("boundary_types", [])
                        ),
                    )
                ),
            )
            if national:
                values += (record.get("facility_class", "未判定"),)
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(str(value)))
        table.setSortingEnabled(True)
        export = QPushButton("导出缺失 / 覆盖清单 JSON…")
        export.clicked.connect(self.export)
        layout.addWidget(export)

    def export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存轮廓核查清单", "station-boundary-audit.json", "JSON (*.json)"
        )
        if path:
            try:
                Path(path).write_text(
                    json.dumps(self.records, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as error:
                QMessageBox.warning(self, "不能保存", str(error))
