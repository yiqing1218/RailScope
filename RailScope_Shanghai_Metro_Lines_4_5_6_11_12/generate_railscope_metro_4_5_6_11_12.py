#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate RailScope v2 reconstructed plans for Shanghai Metro 4/5/6/11/12."""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

SPEC_PATH=Path(__file__).resolve().parent/"metro_4_5_6_11_12_research_spec.json"
DWELL_S=20

def hms(v):
    p=[int(x) for x in str(v).split(":")]
    while len(p)<3:p.append(0)
    return p[0]*3600+p[1]*60+p[2]

def fmt(v): return f"{v//3600:02d}:{v%3600//60:02d}:{v%60:02d}"

def norm(v):
    return re.sub(r"\s+","",str(v)).replace("（","(").replace("）",")").replace("·","").removesuffix("站")

ALIASES={
    "外高桥保税区北":{"外高桥保税区北","外高桥保税区北站"},
    "外高桥保税区南":{"外高桥保税区南","外高桥保税区南站"},
}

def name_match(a,b):
    aa=norm(a)
    return aa==norm(b) or aa in {norm(x) for x in ALIASES.get(b,set())}

def find_root(start):
    start=Path(start).resolve()
    for r in [start,*start.parents]:
        if (r/"desktop"/"metro_data.py").is_file() and (r/"desktop"/"operating.py").is_file():
            return r
    raise SystemExit("找不到 yiqing1218/RailScope 根目录")

def load_lines(root):
    sys.path.insert(0,str(root/"desktop"))
    from data_install import active_directory
    from metro_data import build_shanghai_lines
    d=active_directory(root)
    files=[d/"china_metro_route_catalog.json",d/"china_metro_routes.geojson",d/"china_metro_stations.geojson"]
    if not all(p.is_file() for p in files):
        raise SystemExit("请先在 RailScope 中导入全国地铁 OSM 数据")
    catalog=json.loads(files[0].read_text(encoding="utf-8"))
    routes=json.loads(files[1].read_text(encoding="utf-8"))
    stations=json.loads(files[2].read_text(encoding="utf-8"))
    base=build_shanghai_lines(catalog["routes"],routes["features"],stations["features"])
    expanded=[]
    for line in base:
        expanded.append(line)
        for variant in line.get("variants",[]):
            key=line["id"]+"@"+str(variant["relation_id"])
            expanded.append({**line,**variant,"id":key,"name":line["name"]+" · "+variant["source_name"]})
    return expanded,d

def choose_variant(lines,base_id,required):
    c=[]
    for line in lines:
        if not (line["id"]==base_id or line["id"].startswith(base_id+"@")): continue
        names=[s["name"] for s in line.get("stations",[])]
        if all(any(name_match(n,r) for n in names) for r in required):
            c.append(line)
    if not c: raise ValueError(f"{base_id} 找不到包含 {required} 的本地线路方案")
    return max(c,key=lambda x:len(x.get("stations",[])))

def resolve(line,canonical):
    out={}
    for n in canonical:
        m=[s for s in line["stations"] if name_match(s["name"],n)]
        if not m: raise ValueError(f"{line['name']} 缺少 {n}")
        out[n]=min(m,key=lambda s:s["distance_m"])
    return out

def rev_offsets(fwd):
    edges=[b-a for a,b in zip(fwd,fwd[1:])]
    r=[0]
    for e in reversed(edges): r.append(r[-1]+e)
    return r

def trip(line,canonical,fwd,rev,origin,dest,dep,tid,source):
    rs=resolve(line,canonical)
    oi,di=canonical.index(origin),canonical.index(dest)
    local=line["stations"]; o=rs[origin]; d=rs[dest]
    a,b=local.index(o),local.index(d)
    direction="forward" if b>a else "reverse"
    ls=local[a:b+1] if direction=="forward" else local[a:b-1:-1]
    exp=canonical[oi:di+1] if di>oi else canonical[oi:di-1:-1]
    if len(ls)!=len(exp) or any(not name_match(s["name"],n) for s,n in zip(ls,exp)):
        raise ValueError(f"{line['name']}：{origin}->{dest} 站序与本地OSM不一致")
    if di>oi:
        base=fwd[oi]; offs=[(fwd[i]-base)*60 for i in range(oi,di+1)]
    else:
        n=len(canonical)
        ro=lambda i: rev[n-1-i]
        base=ro(oi); offs=[(ro(i)-base)*60 for i in range(oi,di-1,-1)]
    stops=[]
    for k,(st,off) in enumerate(zip(ls,offs)):
        control=dep+off
        if k in (0,len(ls)-1): arr=leave=control
        else:
            prev=stops[-1]["departure_s"]; dwell=min(DWELL_S,max(0,control-prev-1))
            arr,leave=control-dwell,control
        stops.append({"station_id":st["id"],"arrival_s":int(arr),"departure_s":int(leave),"distance_m":st["distance_m"]})
    return {"id":tid,"line_id":line["id"],"direction":direction,"enabled":True,"source":source,"stops":stops}

def deps(start,end,headway,phase=0):
    t=hms(start)+phase; e=hms(end); out=[]
    while t<=e: out.append(t); t+=headway
    return out

def add(trains,line,p,origin,dest,start,end,h,tag,phase=0):
    r=p.get("reverse_offsets_min") or rev_offsets(p["forward_offsets_min"])
    for d in deps(start,end,h,phase):
        trains.append(trip(line,p["stations"],p["forward_offsets_min"],r,origin,dest,d,
            f"{tag}-{fmt(d).replace(':','')}",
            f"公开资料重建，非官方逐车次运行图；{origin}→{dest}；按{h}秒平均间隔铺图。"))

def build5(lines,cfg,day):
    pf=cfg["routes"]["fengxian"]; pm=cfg["routes"]["minhang"]
    lf=choose_variant(lines,"sh-5",["莘庄","奉贤新城"]); lm=choose_variant(lines,"sh-5",["莘庄","闵行开发区"])
    tr=[]
    if day=="weekday":
        A=[("05:50","07:30",1080),("07:30","09:30",225),("09:30","17:00",600),("17:00","20:00",270),("20:00","22:40",1080)]
        B=[("06:00","07:30",1080),("07:30","09:30",450),("09:30","17:00",600),("17:00","20:00",540),("20:00","22:35",1080)]
    else:
        A=[("05:50","07:00",1080),("07:00","20:00",480),("20:00","22:40",1080)]
        B=[("06:00","07:00",1080),("07:00","20:00",480),("20:00","22:35",1080)]
    for j,(a,b,h) in enumerate(A):
        add(tr,lf,pf,"莘庄","奉贤新城",a,b,h,f"L5-FX-F-{j}")
        add(tr,lf,pf,"奉贤新城","莘庄",a,b,h,f"L5-FX-R-{j}",h//2)
    for j,(a,b,h) in enumerate(B):
        add(tr,lm,pm,"莘庄","闵行开发区",a,b,h,f"L5-MH-F-{j}",h//2)
        add(tr,lm,pm,"闵行开发区","莘庄",a,b,h,f"L5-MH-R-{j}")
    return tr

def build6(lines,cfg,day):
    line=choose_variant(lines,"sh-6",["港城路","东方体育中心"])
    p={"stations":cfg["stations"],"forward_offsets_min":cfg["forward_offsets_min"],"reverse_offsets_min":cfg["reverse_offsets_min"]}
    tr=[]
    periods=([("05:30","07:20",660,660),("07:20","09:00",240,240),("09:00","16:30",600,600),("16:30","19:00",225,450),("19:00","22:30",660,660)]
             if day=="weekday" else
             [("05:30","08:30",660,660),("08:30","20:30",600,300),("20:30","22:30",660,660)])
    for j,(a,b,fh,sh) in enumerate(periods):
        add(tr,line,p,"港城路","东方体育中心",a,b,fh,f"L6-FULL-F-{j}")
        add(tr,line,p,"东方体育中心","港城路",a,b,fh,f"L6-FULL-R-{j}",fh//2)
        add(tr,line,p,"巨峰路","高青路",a,b,sh,f"L6-SHORT-F-{j}",sh//2)
        add(tr,line,p,"高青路","巨峰路",a,b,sh,f"L6-SHORT-R-{j}")
    return tr

def build12(lines,cfg,day):
    line=choose_variant(lines,"sh-12",["七莘路","金海路"])
    p={"stations":cfg["stations"],"forward_offsets_min":cfg["forward_offsets_min"],"reverse_offsets_min":cfg["reverse_offsets_min"]}
    tr=[]
    periods=([("05:30","07:30",420,None),("07:30","09:30",300,300),("09:30","16:30",360,None),("16:30","20:00",450,450),("20:00","22:30",420,None)]
             if day=="weekday" else
             [("05:30","08:00",540,None),("08:00","19:00",360,None),("19:00","22:30",540,None)])
    for j,(a,b,fh,sh) in enumerate(periods):
        add(tr,line,p,"七莘路","金海路",a,b,fh,f"L12-FULL-F-{j}")
        add(tr,line,p,"金海路","七莘路",a,b,fh,f"L12-FULL-R-{j}",fh//2)
        if sh:
            add(tr,line,p,"虹梅路","巨峰路",a,b,sh,f"L12-SHORT-F-{j}",sh//2)
            add(tr,line,p,"巨峰路","虹梅路",a,b,sh,f"L12-SHORT-R-{j}")
    return tr

def build11(lines,cfg,day):
    ph=cfg["routes"]["huaqiao"].copy(); pj=cfg["routes"]["jiading"].copy()
    ph["reverse_offsets_min"]=rev_offsets(ph["forward_offsets_min"])
    pj["reverse_offsets_min"]=rev_offsets(pj["forward_offsets_min"])
    lh=choose_variant(lines,"sh-11",["花桥","迪士尼"]); lj=choose_variant(lines,"sh-11",["嘉定北","迪士尼"])
    tr=[]
    if day=="weekend":
        periods=[("05:37","08:00",720),("08:00","20:00",600),("20:00","22:00",720)]
        for j,(a,b,h) in enumerate(periods):
            add(tr,lh,ph,"花桥","迪士尼",a,b,h,f"L11-HQ-DIS-F-{j}")
            add(tr,lj,pj,"嘉定北","迪士尼",a,b,h,f"L11-JD-DIS-F-{j}",h//2)
            add(tr,lh,ph,"迪士尼","花桥",a,b,h,f"L11-DIS-HQ-R-{j}")
            add(tr,lj,pj,"迪士尼","嘉定北",a,b,h,f"L11-DIS-JD-R-{j}",h//2)
        return tr
    for j,(a,b,h) in enumerate([("05:37","07:30",720),("09:00","18:00",720),("19:00","22:00",720)]):
        add(tr,lh,ph,"花桥","迪士尼",a,b,h,f"L11-HQ-DIS-F-{j}")
        add(tr,lj,pj,"嘉定北","迪士尼",a,b,h,f"L11-JD-DIS-F-{j}",h//2)
        add(tr,lh,ph,"迪士尼","花桥",a,b,h,f"L11-DIS-HQ-R-{j}")
        add(tr,lj,pj,"迪士尼","嘉定北",a,b,h,f"L11-DIS-JD-R-{j}",h//2)
    # AM: branch full + branch-to-Luoshan + Nanxiang-Luoshan.
    for line,p,prefix,origin,phase in [(lh,ph,"HQ","花桥",0),(lj,pj,"JD","嘉定北",360)]:
        add(tr,line,p,origin,"迪士尼","07:30","09:00",720,f"L11-AM-{prefix}-DIS-F",phase)
        add(tr,line,p,origin,"罗山路","07:30","09:00",720,f"L11-AM-{prefix}-LS-F",(phase+180)%720)
    add(tr,lh,ph,"南翔","罗山路","07:30","09:00",360,"L11-AM-NX-LS-F",90)
    for line,p,prefix,dest,phase in [(lh,ph,"HQ","花桥",0),(lj,pj,"JD","嘉定北",450)]:
        add(tr,line,p,"迪士尼",dest,"07:30","09:00",900,f"L11-AM-DIS-{prefix}-R",phase)
        add(tr,line,p,"罗山路",dest,"07:30","09:00",900,f"L11-AM-LS-{prefix}-R",(phase+225)%900)
    add(tr,lh,ph,"罗山路","南翔","07:30","09:00",450,"L11-AM-LS-NX-R",112)
    # PM
    for line,p,prefix,origin,phase in [(lh,ph,"HQ","花桥",0),(lj,pj,"JD","嘉定北",420)]:
        add(tr,line,p,origin,"迪士尼","18:00","19:00",840,f"L11-PM-{prefix}-DIS-F",phase)
        add(tr,line,p,origin,"罗山路","18:00","19:00",840,f"L11-PM-{prefix}-LS-F",(phase+210)%840)
    for line,p,prefix,dest,phase in [(lh,ph,"HQ","花桥",0),(lj,pj,"JD","嘉定北",360)]:
        add(tr,line,p,"迪士尼",dest,"18:00","19:00",720,f"L11-PM-DIS-{prefix}-R",phase)
        add(tr,line,p,"罗山路",dest,"18:00","19:00",720,f"L11-PM-LS-{prefix}-R",(phase+180)%720)
    return tr

def rotate(seq,i): return seq[i:]+seq[:i]

def build4(lines,cfg,day):
    line=choose_variant(lines,"sh-4",["宜山路","世纪大道","上海体育馆"])
    s=line["stations"]
    if len(s)<27 or s[0]["id"]!=s[-1]["id"] or s[-1]["distance_m"]<=s[0]["distance_m"]:
        raise SystemExit("4号线未形成闭环站序。请先运行 patch_railscope_line4_loop.py。")
    unique=s[:-1]; names=[x["name"] for x in unique]
    inner=cfg["inner_station_order"]; outer=cfg["outer_station_order"]
    def match(order):
        for i,n in enumerate(order):
            if name_match(names[0],n):
                r=rotate(order,i)
                if len(r)==len(names) and all(name_match(a,b) for a,b in zip(names,r)): return i
        return None
    mi,mo=match(inner),match(outer)
    if (mi is None)==(mo is None): raise SystemExit("无法判定本机4号线forward对应内圈还是外圈")
    fk="inner" if mi is not None else "outer"; rk="outer" if fk=="inner" else "inner"
    def offsets(kind):
        order=inner if kind=="inner" else outer
        cum=cfg["inner_offsets_min_including_return"] if kind=="inner" else cfg["outer_offsets_min_including_return"]
        edges=[b-a for a,b in zip(cum,cum[1:])]
        idx=match(order)
        if idx is None:
            # reverse local order starts at same break station
            idx=next(i for i,n in enumerate(order) if name_match(names[0],n))
        e=rotate(edges,idx); o=[0]
        for x in e:o.append(o[-1]+x)
        return o
    fo,ro=offsets(fk),offsets(rk)
    tr=[]
    periods=([("05:25","07:30",600,600),("07:30","09:00",240 if fk=="inner" else 400,240 if rk=="inner" else 400),
              ("09:00","17:00",480,480),("17:00","19:30",300,300),("19:30","21:30",600,600)]
             if day=="weekday" else
             [("05:25","08:00",600,600),("08:00","20:00",390,390),("20:00","21:30",600,600)])
    def loop_trip(direction,dep,offs,tag):
        stations=s if direction=="forward" else s[::-1]
        stops=[]
        for k,(st,m) in enumerate(zip(stations,offs)):
            control=dep+m*60
            if k in (0,len(stations)-1): arr=leave=control
            else:
                prev=stops[-1]["departure_s"]; dwell=min(DWELL_S,max(0,control-prev-1))
                arr,leave=control-dwell,control
            stops.append({"station_id":st["id"],"arrival_s":int(arr),"departure_s":int(leave),"distance_m":st["distance_m"]})
        return {"id":tag,"line_id":line["id"],"direction":direction,"enabled":True,
                "source":"2026公开资料重建的4号线完整环行车次，非官方逐车次运行图。","stops":stops}
    for j,(a,b,hf,hr) in enumerate(periods):
        for d in deps(a,b,hf): tr.append(loop_trip("forward",d,fo,f"L4-{fk.upper()}-{j}-{fmt(d).replace(':','')}"))
        for d in deps(a,b,hr,hr//2): tr.append(loop_trip("reverse",d,ro,f"L4-{rk.upper()}-{j}-{fmt(d).replace(':','')}"))
    return tr

def payload(trains,which,day,notes):
    return {"schema":"railscope.operating-plan.v2","official":False,"system":"metro","required_capabilities":[],
            "extensions":{"user.local/public-reconstruction":{"lines":which,"daytype":day,
            "method":"2026官方逐站首末班表+公开分段平均间隔+本机RailScope OSM station_id/distance_m",
            "warning":"不是ATS、司机时刻表或真实车底周转表。","notes":notes}},
            "cycles":[],"vehicles":[],"trains":sorted(trains,key=lambda t:(t["stops"][0]["arrival_s"],t["id"]))}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,default=Path.cwd())
    ap.add_argument("--daytype",choices=["weekday","weekend","all"],default="all")
    ap.add_argument("--lines",default="4,5,6,11,12")
    ap.add_argument("--output-dir",type=Path,default=None)
    a=ap.parse_args()
    root=find_root(a.root)
    cfg=json.loads(SPEC_PATH.read_text(encoding="utf-8"))["lines"]
    lines,active=load_lines(root)
    sys.path.insert(0,str(root/"desktop"))
    from operating import Plan
    builders={"4":build4,"5":build5,"6":build6,"11":build11,"12":build12}
    days=["weekday","weekend"] if a.daytype=="all" else [a.daytype]
    chosen=[x.strip() for x in a.lines.split(",") if x.strip()]
    od=a.output_dir or root/"data"/"processed"/"operations"/"public_reconstruction_4_5_6_11_12"
    od.mkdir(parents=True,exist_ok=True)
    for day in days:
        alltr=[]
        for no in chosen:
            tr=builders[no](lines,cfg[no],day)
            p=payload(tr,[no],day,cfg[no].get("notes",[cfg[no].get("note","")]))
            path=od/f"shanghai_metro_line{no}_{day}_public_reconstruction.json"
            path.write_text(json.dumps(p,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
            Plan(lines,system="metro").load(path)
            print("OK",path,"trips=",len(tr)); alltr+=tr
        p=payload(alltr,chosen,day,["多线路合并文件"])
        path=od/f"shanghai_metro_lines_{'_'.join(chosen)}_{day}_public_reconstruction.json"
        path.write_text(json.dumps(p,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
        Plan(lines,system="metro").load(path)
        print("OK",path,"trips=",len(alltr))
    print("Active metro dataset:",active)

if __name__=="__main__":
    main()
