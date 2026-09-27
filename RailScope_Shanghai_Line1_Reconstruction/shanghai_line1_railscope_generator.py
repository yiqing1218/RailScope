#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate RailScope-importable Shanghai Metro Line 1 reconstructed operating plans.

Target repository:
    https://github.com/yiqing1218/RailScope
Target schema:
    railscope.operating-plan.v2

IMPORTANT:
- This is NOT an official Shanghai Metro train graph.
- It reconstructs train paths from public first/last-train information,
  published average headways, and minute-level station timing profiles.
- station_id and distance_m are read from THIS LOCAL RailScope installation,
  so the output matches the user's OSM snapshot and RailScope's strict validator.
- No real trainset/vehicle IDs are claimed; cycles and vehicles are left empty.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


CANONICAL_STATIONS = [
    "莘庄", "外环路", "莲花路", "锦江乐园", "上海南站", "漕宝路", "上海体育馆",
    "徐家汇", "衡山路", "常熟路", "陕西南路", "一大会址·黄陂南路", "人民广场",
    "新闸路", "汉中路", "上海火车站", "中山北路", "延长路", "上海马戏城",
    "汶水路", "彭浦新村", "共康路", "通河新村", "呼兰路", "共富新村",
    "宝安公路", "友谊西路", "富锦路",
]

ALIASES = {
    "一大会址·黄陂南路": {"一大会址·黄陂南路", "黄陂南路", "一大会址黄陂南路"},
    "上海火车站": {"上海火车站", "上海站"},
    "上海南站": {"上海南站"},
}

# Minute-level station time profile reconstructed from the currently published
# Line 1 first/last-train table. These are timing-control offsets, not measured
# pure running times.
#
# South -> north: offset from 莘庄.
SN_MIN = [0, 2, 4, 7, 10, 13, 16, 18, 20, 22, 24, 26, 29, 31, 33, 36,
          38, 41, 43, 45, 48, 51, 53, 55, 58, 61, 63, 65]

# North -> south: for each station in CANONICAL_STATIONS order, offset from 富锦路
# when travelling north -> south.
NS_FROM_FUJIN_MIN = [64, 62, 60, 57, 54, 51, 49, 46, 44, 42, 40, 38, 35, 33,
                     31, 30, 27, 25, 23, 20, 17, 15, 12, 10, 7, 4, 2, 0]

DWELL_S = 20

PUBLIC_SOURCE = (
    "公开资料重建：上海地铁1号线公开首末班车时刻、公开平均行车间隔及分钟级逐站"
    "时间剖面；非申通地铁官方逐车次运行图，非ATS/司机时刻表。"
)


def norm_name(value: str) -> str:
    value = str(value).strip()
    value = value.replace("（", "(").replace("）", ")")
    value = re.sub(r"\s+", "", value)
    value = value.removesuffix("站")
    value = value.replace("·", "").replace("•", "").replace("・", "")
    return value


def find_root(start: Path) -> Path:
    candidates = [start.resolve(), *start.resolve().parents]
    for root in candidates:
        if (root / "desktop" / "metro_data.py").is_file() and (root / "data").exists():
            return root
    raise SystemExit(
        "找不到 RailScope 项目根目录。请把本脚本放到 yiqing1218/RailScope 根目录，"
        "或在该根目录执行：python <脚本路径>"
    )


def load_local_line1(root: Path):
    sys.path.insert(0, str(root / "desktop"))
    from data_install import active_directory
    from metro_data import build_shanghai_lines

    active = active_directory(root)
    catalog_path = active / "china_metro_route_catalog.json"
    routes_path = active / "china_metro_routes.geojson"
    stations_path = active / "china_metro_stations.geojson"

    missing = [p for p in (catalog_path, routes_path, stations_path) if not p.is_file()]
    if missing:
        raise SystemExit(
            "缺少 RailScope 全国地铁导入结果：\n  "
            + "\n  ".join(str(p) for p in missing)
            + "\n请先在 RailScope 中执行“数据源 → 自动下载 / 更新全国地铁…”。"
        )

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    routes = json.loads(routes_path.read_text(encoding="utf-8"))
    stations = json.loads(stations_path.read_text(encoding="utf-8"))

    lines = build_shanghai_lines(
        catalog["routes"], routes["features"], stations["features"]
    )
    line = next((x for x in lines if x["id"] == "sh-1"), None)
    if not line or not line.get("path") or len(line.get("stations", [])) < 28:
        raise SystemExit(
            "本机 RailScope 没有得到可用的上海1号线 sh-1 / OSM Relation 199200。"
            "请更新地铁数据后重试。"
        )
    return lines, line, active


def resolve_station_map(line):
    local = line["stations"]
    by_norm = {}
    for s in local:
        by_norm.setdefault(norm_name(s["name"]), []).append(s)

    resolved = {}
    for canonical in CANONICAL_STATIONS:
        names = {canonical, *ALIASES.get(canonical, set())}
        candidates = []
        for name in names:
            candidates.extend(by_norm.get(norm_name(name), []))
        # Deduplicate by station id.
        unique = {s["id"]: s for s in candidates}
        if len(unique) != 1:
            local_names = [s["name"] for s in local]
            raise SystemExit(
                f"无法唯一匹配车站“{canonical}”；候选={list(unique.values())}。\n"
                f"本机1号线车站为：{local_names}"
            )
        resolved[canonical] = next(iter(unique.values()))

    indices = [local.index(resolved[name]) for name in CANONICAL_STATIONS]
    if indices == sorted(indices):
        canonical_order_is_forward = True
    elif indices == sorted(indices, reverse=True):
        canonical_order_is_forward = False
    else:
        raise SystemExit(
            "RailScope 本机1号线站序不是连续的莘庄—富锦路或其反向；为防止写错运行图，已停止。"
        )
    return resolved, canonical_order_is_forward


def hms_to_s(value: str) -> int:
    h, m, *rest = map(int, value.split(":"))
    s = rest[0] if rest else 0
    return h * 3600 + m * 60 + s


def s_to_hms(value: int) -> str:
    return f"{value//3600:02d}:{value%3600//60:02d}:{value%60:02d}"


def profile_relative_seconds(origin_idx: int, dest_idx: int, target_duration_s=None):
    if origin_idx == dest_idx:
        raise ValueError("origin == destination")

    if dest_idx > origin_idx:  # south -> north
        raw = [
            (SN_MIN[i] - SN_MIN[origin_idx]) * 60
            for i in range(origin_idx, dest_idx + 1)
        ]
    else:  # north -> south
        raw = [
            (NS_FROM_FUJIN_MIN[i] - NS_FROM_FUJIN_MIN[origin_idx]) * 60
            for i in range(origin_idx, dest_idx - 1, -1)
        ]

    if target_duration_s is not None:
        if raw[-1] <= 0:
            raise ValueError("invalid profile")
        scale = target_duration_s / raw[-1]
        raw = [round(x * scale) for x in raw]
        raw[0] = 0
        raw[-1] = int(target_duration_s)

    # Strictly increasing control times.
    for i in range(1, len(raw)):
        raw[i] = max(raw[i], raw[i - 1] + 1)
    return raw


def make_trip(
    trip_id: str,
    origin: str,
    destination: str,
    departure_s: int,
    resolved,
    canonical_order_is_forward: bool,
    target_duration_s=None,
    source=PUBLIC_SOURCE,
):
    oi = CANONICAL_STATIONS.index(origin)
    di = CANONICAL_STATIONS.index(destination)
    canonical_indices = (
        list(range(oi, di + 1)) if di > oi else list(range(oi, di - 1, -1))
    )
    offsets = profile_relative_seconds(oi, di, target_duration_s)

    # Map travel direction to RailScope's local reference-direction convention.
    travel_is_sn = di > oi
    direction = (
        "forward"
        if travel_is_sn == canonical_order_is_forward
        else "reverse"
    )

    stops = []
    for k, (ci, offset) in enumerate(zip(canonical_indices, offsets)):
        station = resolved[CANONICAL_STATIONS[ci]]
        control_s = departure_s + offset

        if k == 0:
            arrival_s = departure_s
            depart_s = departure_s
        elif k == len(canonical_indices) - 1:
            arrival_s = control_s
            depart_s = control_s
        else:
            previous_departure = stops[-1]["departure_s"]
            # Treat the public minute-level control time as departure/pass time.
            # Estimate a small dwell without violating RailScope's strict time order.
            dwell = min(DWELL_S, max(0, control_s - previous_departure - 1))
            arrival_s = control_s - dwell
            depart_s = control_s

        stops.append(
            {
                "station_id": station["id"],
                "arrival_s": int(arrival_s),
                "departure_s": int(depart_s),
                "distance_m": station["distance_m"],
            }
        )

    return {
        "id": trip_id,
        "line_id": "sh-1",
        "direction": direction,
        "enabled": True,
        "source": source,
        "stops": stops,
    }


def segment_departures(first_s, last_s, segments):
    """Generate deterministic approximate departures from published average headways."""
    values = {int(first_s), int(last_s)}
    for start, end, headway in segments:
        start = max(int(start), int(first_s))
        end = min(int(end), int(last_s))
        if start > end:
            continue
        # Anchor at the published segment boundary; this is an approximation.
        t = start
        while t <= end:
            values.add(t)
            t += int(headway)
    return sorted(x for x in values if first_s <= x <= last_s)


PROFILES = {
    # "Other periods" are publicly given only as ranges. We select a representative
    # 6 min (weekday) or 8 min (weekend) headway to make the diagram drawable.
    "mon-thu": {
        "sn_last": "22:32:00",
        "ns_last": "22:30:00",
        "segments": [
            ("05:30:00", "07:00:00", 360),
            ("07:00:00", "09:00:00", 150),
            ("09:00:00", "17:00:00", 300),
            ("17:00:00", "19:00:00", 180),
            ("19:00:00", "22:32:00", 360),
        ],
        "note": "周一至周四重建；其余时段4–9分钟取代表值6分钟。",
    },
    "friday": {
        "sn_last": "24:00:00",
        "ns_last": "24:00:00",
        "segments": [
            ("05:30:00", "07:00:00", 360),
            ("07:00:00", "09:00:00", 150),
            ("09:00:00", "15:00:00", 300),
            ("15:00:00", "19:00:00", 180),
            ("19:00:00", "22:30:00", 360),
            ("22:30:00", "24:00:00", 720),
        ],
        "note": "周五重建；延时段10–15分钟取代表值12分钟。",
    },
    "saturday": {
        "sn_last": "24:00:00",
        "ns_last": "24:00:00",
        "segments": [
            ("05:30:00", "09:00:00", 480),
            ("09:00:00", "20:00:00", 240),
            ("20:00:00", "22:30:00", 480),
            ("22:30:00", "24:00:00", 720),
        ],
        "note": "周六重建；常态其他时段6–12分钟取代表值8分钟，延时段取12分钟。",
    },
    "sunday": {
        "sn_last": "22:32:00",
        "ns_last": "22:30:00",
        "segments": [
            ("05:30:00", "09:00:00", 480),
            ("09:00:00", "20:00:00", 240),
            ("20:00:00", "22:32:00", 480),
        ],
        "note": "周日重建；其他时段6–12分钟取代表值8分钟。",
    },
}


def build_plan(profile_name, line, resolved, canonical_order_is_forward):
    p = PROFILES[profile_name]
    trains = []

    # Publicly visible multi-origin first-train structure.
    # Intermediate times are reconstructed from the minute-level Line 1 profile.
    trains.append(
        make_trip(
            "L1-EARLY-SN-SS-SHRS-045500",
            "上海南站",
            "上海火车站",
            hms_to_s("04:55:00"),
            resolved,
            canonical_order_is_forward,
            target_duration_s=24 * 60,
            source=PUBLIC_SOURCE + " 特殊首班：上海南站04:55→上海火车站05:19；中间时刻按剖面插值。",
        )
    )
    trains.append(
        make_trip(
            "L1-EARLY-SN-SS-FJ-051800",
            "上海南站",
            "富锦路",
            hms_to_s("05:18:00"),
            resolved,
            canonical_order_is_forward,
            target_duration_s=54 * 60,
            source=PUBLIC_SOURCE + " 特殊首班：上海南站05:18→富锦路06:12；中间时刻按剖面重建。",
        )
    )
    trains.append(
        make_trip(
            "L1-EARLY-NS-SHRS-XZ-053000",
            "上海火车站",
            "莘庄",
            hms_to_s("05:30:00"),
            resolved,
            canonical_order_is_forward,
            target_duration_s=34 * 60,
            source=PUBLIC_SOURCE + " 特殊首班：上海火车站05:30→莘庄06:04；中间时刻按剖面重建。",
        )
    )

    first = hms_to_s("05:30:00")
    sn_last = hms_to_s(p["sn_last"])
    ns_last = hms_to_s(p["ns_last"])
    segments = [(hms_to_s(a), hms_to_s(b), h) for a, b, h in p["segments"]]

    for dep in segment_departures(first, sn_last, segments):
        trains.append(
            make_trip(
                f"L1-SN-{s_to_hms(dep).replace(':','')}",
                "莘庄",
                "富锦路",
                dep,
                resolved,
                canonical_order_is_forward,
            )
        )

    for dep in segment_departures(first, ns_last, segments):
        trains.append(
            make_trip(
                f"L1-NS-{s_to_hms(dep).replace(':','')}",
                "富锦路",
                "莘庄",
                dep,
                resolved,
                canonical_order_is_forward,
            )
        )

    # IDs are already unique, but sorting makes output deterministic/readable.
    trains.sort(key=lambda t: (t["stops"][0]["arrival_s"], t["id"]))

    return {
        "schema": "railscope.operating-plan.v2",
        "official": False,
        "system": "metro",
        "required_capabilities": [],
        "extensions": {
            "user.local/reconstruction": {
                "line": "上海地铁1号线",
                "local_line_id": "sh-1",
                "osm_relation_hint": 199200,
                "profile": profile_name,
                "note": p["note"],
                "method": (
                    "公开首末班车+公开平均间隔+分钟级逐站时间剖面；"
                    "station_id/distance_m来自本机RailScope OSM导入结果。"
                ),
                "limitations": (
                    "不是官方逐车次运行图；非高峰“其他时段”仅有间隔范围，采用代表值；"
                    "未恢复真实车底号、车辆周转、司机交路、ATS调整和真实折返进路。"
                ),
            }
        },
        "cycles": [],
        "vehicles": [],
        "trains": trains,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="RailScope 项目根目录；默认从当前目录向上自动寻找。",
    )
    parser.add_argument(
        "--profile",
        choices=["all", *PROFILES.keys()],
        default="all",
        help="生成哪一种运营日重建计划。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="输出目录，默认 data/processed/operations/reconstructed_line1",
    )
    args = parser.parse_args()

    root = find_root(args.root)
    lines, line, active = load_local_line1(root)
    resolved, canonical_order_is_forward = resolve_station_map(line)

    output_dir = (
        args.output_dir.resolve()
        if args.output_dir
        else root / "data/processed/operations/reconstructed_line1"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    requested = list(PROFILES) if args.profile == "all" else [args.profile]
    created = []

    # Import the exact same validator used by RailScope.
    sys.path.insert(0, str(root / "desktop"))
    from operating import Plan

    for profile in requested:
        payload = build_plan(profile, line, resolved, canonical_order_is_forward)
        output = output_dir / f"shanghai_line1_public_reconstruction_{profile}.json"
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )

        # Hard validation: if this passes, the file satisfies this local RailScope build's
        # station IDs, station order, exact distance_m values, and v2 schema.
        checker = Plan(lines, system="metro")
        checker.load(output)
        created.append((output, len(payload["trains"])))

    print("RailScope 上海地铁1号线重建计划生成成功。")
    print(f"本机地铁数据：{active}")
    print(f"1号线本地ID：{line['id']}，OSM relation：{line.get('relation_id')}")
    print(f"站数：{len(line['stations'])}，方向："
          f"{'莘庄→富锦路=forward' if canonical_order_is_forward else '莘庄→富锦路=reverse'}")
    for path, count in created:
        print(f"  {path}  ({count} 个单程车次)")
    print("\n在 RailScope 中使用“运行 → 地铁 → 导入运行计划”选择上述 JSON。")
    print("这些文件 official=false；它们是公开资料重建，不是申通官方逐车次运行图。")


if __name__ == "__main__":
    main()
