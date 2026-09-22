"""Editable line facts stored in the workspace layer, separate from OSM."""

RAIL_LINE_FIELDS = (
    ("operating_status", "运营状态", "如：运营中、在建、规划、停运"),
    ("design_speed_kmh", "设计速度（km/h）", "线路设计速度"),
    ("max_speed_kmh", "最高运行速度（km/h）", "当前允许的最高运行速度"),
    ("construction_start_date", "开工时间", "可填写 YYYY、YYYY-MM 或完整日期"),
    ("opening_date", "开通时间", "可填写分期开通信息"),
    ("length_km", "线路长度（km）", "营业或正线长度"),
    ("track_count", "正线线数", "如：复线、四线"),
    ("gauge_mm", "轨距（mm）", "标准轨通常为 1435"),
    ("electrification", "电气化方式", "如：电气化、非电气化"),
    ("power_supply", "供电制式", "如：25 kV 50 Hz 接触网"),
    ("signal_system", "信号 / 闭塞制式", "如：CTCS-3、自动闭塞"),
    ("route_usage", "线路用途", "如：客运、货运、客货共线"),
    ("owner", "资产 / 建设单位", "线路资产或建设责任单位"),
    ("operator", "运营单位", "实际运营维护单位"),
    ("remarks", "备注", "分期、共线、限速等补充说明"),
)

METRO_LINE_FIELDS = (
    ("operating_status", "运营状态", "如：运营中、在建、规划、停运"),
    ("vehicle_type", "车辆制式", "如：A 型、B 型、L 型、单轨"),
    ("formation", "列车编组", "如：6 节 A 型"),
    ("max_speed_kmh", "最高运行速度（km/h）", "线路最高运行速度"),
    ("construction_start_date", "开工时间", "可填写 YYYY、YYYY-MM 或完整日期"),
    ("opening_date", "开通时间", "可填写分期开通信息"),
    ("length_km", "线路长度（km）", "运营里程或线路长度"),
    ("station_count", "车站数量", "可填写换乘站口径说明"),
    ("gauge_mm", "轨距（mm）", "钢轮钢轨线路通常为 1435"),
    ("power_supply", "供电制式", "如：DC 1500 V 接触网"),
    ("signal_system", "信号系统", "如：CBTC"),
    ("automation_level", "自动化等级", "如：GoA2、GoA4"),
    ("depot", "车辆基地 / 停车场", "可填写多个基地"),
    ("owner", "建设 / 资产单位", "线路建设或资产单位"),
    ("operator", "运营单位", "线路运营单位"),
    ("remarks", "备注", "分期、支线、共线等补充说明"),
)

FIELD_LABELS = {key: label for key, label, _hint in (*RAIL_LINE_FIELDS, *METRO_LINE_FIELDS)}


def source_line_attributes(properties, kind, custom=None):
    """Prefer explicit source tags, then apply user-owned workspace values."""
    properties = properties or {}
    tags = {**properties.get("way_tags", {}), **properties.get("relation_tags", {})}
    result = {
        "operating_status": "在建" if properties.get("construction") else "运营中",
        "max_speed_kmh": tags.get("maxspeed", ""),
        "construction_start_date": tags.get("construction:start_date", ""),
        "opening_date": tags.get("opening_date") or tags.get("start_date", ""),
        "length_km": tags.get("length") or tags.get("distance", ""),
        "gauge_mm": tags.get("gauge", ""),
        "power_supply": " ".join(
            value
            for value in (
                tags.get("voltage", ""),
                tags.get("frequency", ""),
                tags.get("electrified", ""),
            )
            if value
        ),
        "signal_system": tags.get("railway:cbtc") or tags.get("signal_system", ""),
        "owner": properties.get("owner") or tags.get("owner", ""),
        "operator": properties.get("operator") or tags.get("operator", ""),
    }
    if kind == "metro":
        result.update(
            vehicle_type=tags.get("train:type") or tags.get("rolling_stock", ""),
            formation=tags.get("train:formation", ""),
            automation_level=tags.get("automation") or tags.get("goa", ""),
            depot=tags.get("depot", ""),
        )
    allowed = {field[0] for field in (METRO_LINE_FIELDS if kind == "metro" else RAIL_LINE_FIELDS)}
    result = {key: str(value).strip() for key, value in result.items() if key in allowed and value not in (None, "")}
    result.update(normalize_line_attributes(custom or {}, kind))
    return result


def normalize_line_attributes(values, kind):
    fields = METRO_LINE_FIELDS if kind == "metro" else RAIL_LINE_FIELDS
    allowed = {key for key, _label, _hint in fields}
    if not isinstance(values, dict) or set(values) - allowed:
        raise ValueError("线路概览属性包含未知字段")
    result = {}
    for key, value in values.items():
        if value is None:
            continue
        text = str(value).strip()
        limit = 2000 if key == "remarks" else 300
        if len(text) > limit or any(ord(character) < 32 and character not in "\n\t" for character in text):
            raise ValueError("线路概览属性内容过长或包含控制字符")
        if text:
            result[key] = text
    return result
