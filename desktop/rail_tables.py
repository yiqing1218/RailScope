"""Strict UTF-8 CSV train/stop tables referencing existing shared routes."""

import csv
import io

try:
    from .operating import parse_time, format_time
    from .rail import shared_document
except ImportError:
    from operating import parse_time, format_time
    from rail import shared_document

COLUMNS = (
    "train_id",
    "route_id",
    "sequence",
    "node_id",
    "arrival",
    "departure",
    "platform_id",
    "from_track",
    "to_track",
    "via_node",
    "change_time",
)


def merge_csv(text, payload):
    result = shared_document(payload)
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames != list(COLUMNS):
        raise ValueError("CSV 表头必须严格为：" + ",".join(COLUMNS))
    routes = {r["id"]: r for r in result["routes"]}
    existing = {t["id"] for t in result["trains"]}
    pending = {}
    for line, row in enumerate(reader, 2):
        if None in row or any(v is None for v in row.values()):
            raise ValueError(f"第 {line} 行列数不符")
        row = {k: v.strip() for k, v in row.items()}
        ident = row["train_id"]
        if not ident or ident in existing:
            raise ValueError(f"第 {line} 行车次为空或重复：{ident}")
        if row["route_id"] not in routes:
            raise ValueError(f"第 {line} 行共享径路不存在，请先导入径路 JSON")
        train = pending.setdefault(
            ident,
            {
                "id": ident,
                "route_id": row["route_id"],
                "stops": [],
                "extensions": {
                    "railscope.org/provenance": {
                        "source": "用户导入的车次 CSV；非官方计划"
                    }
                },
            },
        )
        if train["route_id"] != row["route_id"] or row["sequence"] != str(
            len(train["stops"]) + 1
        ):
            raise ValueError(f"第 {line} 行径路不一致或站序不连续（从 1 开始）")
        if not row["node_id"].isdigit():
            raise ValueError(f"第 {line} 行节点须为整数 OSM ID")
        stop = {
            "node_id": int(row["node_id"]),
            "arrival_s": parse_time(row["arrival"]),
            "departure_s": parse_time(row["departure"]),
            "track_change": {k: row[k] for k in ("from_track", "to_track", "via_node")},
        }
        stop["track_change"]["time"] = row["change_time"]
        if row["platform_id"]:
            if not row["platform_id"].isdigit():
                raise ValueError("站台 ID 必须是整数 OSM Way ID")
            stop["platform_id"] = int(row["platform_id"])
        train["stops"].append(stop)
    if not pending:
        raise ValueError("CSV 没有车次记录")
    result["trains"].extend(pending.values())
    return result  # Caller compiles the whole batch before replacing its active model.


def export_csv(payload):
    payload = shared_document(payload)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=COLUMNS)
    writer.writeheader()
    routes = {r["id"]: r for r in payload["routes"]}
    for train in payload["trains"]:
        if train.get("station_paths"):
            raise ValueError("含站场径路的车次请导出 JSON；CSV 不能无损保存嵌套路径")
        for sequence, stop in enumerate(train["stops"], 1):
            if stop.get("platform_ref"):
                raise ValueError(
                    "含统一站台引用 platform_ref 的车次请导出 JSON；旧 CSV 无法无损保存"
                )
            change = stop.get("track_change", {})
            shared = next(
                (
                    c
                    for c in routes[train["route_id"]].get("track_changes", [])
                    if c["node_id"] == stop["node_id"]
                ),
                {},
            )
            writer.writerow(
                {
                    "train_id": train["id"],
                    "route_id": train["route_id"],
                    "sequence": sequence,
                    "node_id": stop["node_id"],
                    "arrival": format_time(stop["arrival_s"]),
                    "departure": format_time(stop["departure_s"]),
                    "platform_id": stop.get("platform_id", ""),
                    **{
                        k: change.get(k, shared.get(k, ""))
                        if change.get(k, shared.get(k)) is not None
                        else ""
                        for k in ("from_track", "to_track", "via_node")
                    },
                    "change_time": change.get("time", ""),
                }
            )
    return output.getvalue()
