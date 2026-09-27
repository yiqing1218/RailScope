#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RailScope public-data reconstruction generator for Shanghai Metro Lines 2, 3, 7, 8, 9.

Target: yiqing1218/RailScope, schema railscope.operating-plan.v2
This generator must run INSIDE a local RailScope checkout after national metro data is imported.

It deliberately reads local RailScope-derived OSM station IDs and distance_m values.
Output is official=false and must not be represented as an official dispatch train graph.
"""
from __future__ import annotations
import argparse, json, math, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC_FILE = HERE / "metro_2_3_7_8_9_research_spec.json"

DWELL_S = 20

def hms(value: str) -> int:
    p = [int(x) for x in value.split(":")]
    while len(p) < 3: p.append(0)
    return p[0]*3600 + p[1]*60 + p[2]

def fmt(s: int) -> str:
    return f"{s//3600:02d}:{s%3600//60:02d}:{s%60:02d}"

def norm(value: str) -> str:
    v = str(value).strip().replace("（","(").replace("）",")")
    v = re.sub(r"\s+","",v).removesuffix("站")
    return v.replace("·","").replace("•","").replace("・","")

def find_root(start: Path) -> Path:
    for r in [start.resolve(), *start.resolve().parents]:
        if (r/"desktop"/"metro_data.py").is_file() and (r/"desktop"/"operating.py").is_file():
            return r
    raise SystemExit("找不到 yiqing1218/RailScope 根目录。请在 RailScope 根目录运行本脚本，或用 --root 指定。")

def load_local_lines(root: Path):
    sys.path.insert(0, str(root/"desktop"))
    from data_install import active_directory
    from metro_data import build_shanghai_lines
    active = active_directory(root)
    files = [
        active/"china_metro_route_catalog.json",
        active/"china_metro_routes.geojson",
        active/"china_metro_stations.geojson",
    ]
    if not all(p.is_file() for p in files):
        raise SystemExit("缺少全国地铁派生数据。请先在 RailScope 执行“数据源 → 自动下载 / 更新全国地铁…”。")
    catalog = json.loads(files[0].read_text(encoding="utf-8"))
    routes = json.loads(files[1].read_text(encoding="utf-8"))
    stations = json.loads(files[2].read_text(encoding="utf-8"))
    return build_shanghai_lines(catalog["routes"], routes["features"], stations["features"]), active

def resolve_line(line, cfg):
    local = line["stations"]
    by_norm = {}
    for s in local:
        by_norm.setdefault(norm(s["name"]), []).append(s)

    resolved = {}
    for cname in cfg["stations"]:
        candidates = []
        names = [cname] + cfg.get("aliases", {}).get(cname, [])
        for n in names:
            candidates += by_norm.get(norm(n), [])
        uniq = {s["id"]: s for s in candidates}
        if len(uniq) != 1:
            raise ValueError(
                f"{cfg['name']}：无法唯一匹配车站 {cname!r}；候选={list(uniq.values())}；"
                f"本地站名={[s['name'] for s in local]}"
            )
        resolved[cname] = next(iter(uniq.values()))

    idx = [local.index(resolved[n]) for n in cfg["stations"]]
    if idx == sorted(idx):
        canonical_is_forward = True
    elif idx == sorted(idx, reverse=True):
        canonical_is_forward = False
    else:
        raise ValueError(f"{cfg['name']}：本地OSM站序与公开站序不连续，为避免错图停止。")
    return resolved, canonical_is_forward

def reverse_index_offset(cfg, canonical_index):
    n = len(cfg["stations"])
    # reverse_offsets_min are stored in travel order from canonical last -> canonical first
    return cfg["reverse_offsets_min"][n - 1 - canonical_index]

def relative_offsets(cfg, oi, di):
    if di > oi:
        base = cfg["forward_offsets_min"][oi]
        return [(cfg["forward_offsets_min"][i]-base)*60 for i in range(oi, di+1)]
    base = reverse_index_offset(cfg, oi)
    return [(reverse_index_offset(cfg, i)-base)*60 for i in range(oi, di-1, -1)]

def make_trip(cfg, resolved, canonical_is_forward, origin, dest, dep_s, trip_id, source_note):
    oi, di = cfg["stations"].index(origin), cfg["stations"].index(dest)
    cis = list(range(oi, di+1)) if di > oi else list(range(oi, di-1, -1))
    offs = relative_offsets(cfg, oi, di)
    travel_canonical_forward = di > oi
    direction = "forward" if travel_canonical_forward == canonical_is_forward else "reverse"
    stops = []
    for k,(ci,off) in enumerate(zip(cis,offs)):
        st = resolved[cfg["stations"][ci]]
        control = dep_s + off
        if k == 0 or k == len(cis)-1:
            arr = dep = control
        else:
            prev = stops[-1]["departure_s"]
            dwell = min(DWELL_S, max(0, control-prev-1))
            arr, dep = control-dwell, control
        stops.append({
            "station_id": st["id"],
            "arrival_s": int(arr),
            "departure_s": int(dep),
            "distance_m": st["distance_m"],
        })
    return {
        "id": trip_id,
        "line_id": cfg["line_id"],
        "direction": direction,
        "enabled": True,
        "source": source_note,
        "stops": stops,
    }

def departures(start, end, step):
    a,b = hms(start),hms(end)
    if step <= 0: raise ValueError("headway must be positive")
    out=[]; t=a
    while t <= b:
        out.append(t); t += step
    if out and out[-1] != b and b-a > 0:
        # Do not force an artificial extra train at the boundary; next program window supplies it.
        pass
    return out

def build_line_plan(cfg, local_line, resolved, canonical_is_forward, daytype):
    trains=[]; seen=set()
    program = cfg[f"{daytype}_program"]
    for seq, seg in enumerate(program,1):
        if seg["dir"] == "F":
            origin = seg.get("origin", cfg["stations"][0])
            dest = seg.get("destination", cfg["stations"][-1])
        else:
            origin = seg.get("origin", cfg["stations"][-1])
            dest = seg.get("destination", cfg["stations"][0])
        for dep in departures(seg["start"], seg["end"], int(seg["headway"])):
            stamp = fmt(dep).replace(":","")
            key = (origin,dest,dep)
            if key in seen: continue
            seen.add(key)
            tid = f"L{cfg['line_id'].split('-')[-1]}-{daytype.upper()}-{seg['dir']}-{seq:02d}-{stamp}"
            source = (
                "公开资料重建，非官方逐车次运行图。"
                f"{cfg['name']}；{origin}→{dest}；"
                f"本时段按公开平均/区间行车间隔 {seg['headway']} 秒确定性铺图。"
            )
            trains.append(make_trip(cfg,resolved,canonical_is_forward,origin,dest,dep,tid,source))
    trains.sort(key=lambda t:(t["stops"][0]["arrival_s"],t["id"]))
    return {
        "schema":"railscope.operating-plan.v2",
        "official":False,
        "system":"metro",
        "required_capabilities":[],
        "extensions":{
            "user.local/public-reconstruction":{
                "line":cfg["name"],
                "daytype":daytype,
                "method":"公开首末班车逐站时间剖面 + 公开平均行车间隔 + 本机RailScope OSM station_id/distance_m",
                "notes":cfg["notes"],
                "sources":cfg["sources"],
                "warning":"不是ATS、司机时刻表或车底周转表；推定车次不得冒充官方运行图。"
            }
        },
        "cycles":[],
        "vehicles":[],
        "trains":trains,
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,default=Path.cwd())
    ap.add_argument("--daytype",choices=["weekday","weekend","all"],default="all")
    ap.add_argument("--lines",default="2,3,7,8,9",help="comma-separated")
    ap.add_argument("--output-dir",type=Path,default=None)
    args=ap.parse_args()

    project=find_root(args.root)
    specs=json.loads(SPEC_FILE.read_text(encoding="utf-8"))["lines"]
    local_lines,active=load_local_lines(project)
    local_by_id={x["id"]:x for x in local_lines}
    selected=[x.strip() for x in args.lines.split(",") if x.strip()]
    daytypes=["weekday","weekend"] if args.daytype=="all" else [args.daytype]
    out=args.output_dir or (project/"data"/"processed"/"operations"/"public_reconstruction_batch")
    out.mkdir(parents=True,exist_ok=True)

    sys.path.insert(0,str(project/"desktop"))
    from operating import Plan

    combined={d:[] for d in daytypes}
    for no in selected:
        if no not in specs: raise SystemExit(f"本包没有{no}号线参数")
        cfg=specs[no]
        line=local_by_id.get(cfg["line_id"])
        if not line or not line.get("path"):
            raise SystemExit(f"{cfg['name']} 本地线路 {cfg['line_id']} 不可用")
        resolved,canon_f=resolve_line(line,cfg)
        for d in daytypes:
            payload=build_line_plan(cfg,line,resolved,canon_f,d)
            path=out/f"shanghai_metro_line{no}_{d}_public_reconstruction.json"
            path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
            Plan(local_lines,system="metro").load(path)
            combined[d]+=payload["trains"]
            print(f"OK {path}  trips={len(payload['trains'])}")

    for d in daytypes:
        payload={
            "schema":"railscope.operating-plan.v2","official":False,"system":"metro",
            "required_capabilities":[],
            "extensions":{
                "user.local/public-reconstruction":{
                    "lines":selected,"daytype":d,
                    "warning":"多线路公开资料重建，不是官方运行图。"
                }
            },
            "cycles":[],"vehicles":[],"trains":sorted(combined[d],key=lambda t:(t["stops"][0]["arrival_s"],t["id"]))
        }
        path=out/f"shanghai_metro_lines_{'_'.join(selected)}_{d}_public_reconstruction.json"
        path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
        Plan(local_lines,system="metro").load(path)
        print(f"OK {path}  trips={len(payload['trains'])}")

    print(f"\nRailScope data snapshot: {active}")
    print("Import from RailScope metro operations plan menu. All outputs are official=false.")

if __name__=="__main__":
    main()
