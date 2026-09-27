#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate RailScope v2 public-data reconstructions for Shanghai Metro 10/13/14/15/16/17/18/Pujiang."""
from __future__ import annotations
import argparse,json,re,sys,math
from pathlib import Path

SPEC=Path(__file__).resolve().parent/"metro_10_13_14_15_16_17_18_pujiang_research_spec.json"
DWELL=20

def hms(v):
    p=[int(x) for x in str(v).split(":")]
    while len(p)<3:p.append(0)
    return p[0]*3600+p[1]*60+p[2]
def fmt(v): return f"{v//3600:02d}:{v%3600//60:02d}:{v%60:02d}"
def norm(v): return re.sub(r"\s+","",str(v)).replace("（","(").replace("）",")").replace("·","").removesuffix("站")
ALIASES={
 "一大会址新天地":{"一大会址新天地","新天地"},
 "一大会址黄陂南路":{"一大会址黄陂南路","黄陂南路"},
 "景洪路":{"景洪路","华泾西"},
 "国家会展中心":{"国家会展中心","诸光路"},
 "浦东南路":{"浦东南路","东昌路"}
}
def name_match(a,b):
    na,nb=norm(a),norm(b)
    return na==nb or na in {norm(x) for x in ALIASES.get(nb,set())} or nb in {norm(x) for x in ALIASES.get(na,set())}

def find_root(start):
    start=Path(start).resolve()
    for r in [start,*start.parents]:
        if (r/"desktop"/"metro_data.py").is_file() and (r/"desktop"/"operating.py").is_file(): return r
    raise SystemExit("找不到 yiqing1218/RailScope 根目录")

def load_lines(root):
    sys.path.insert(0,str(root/"desktop"))
    from data_install import active_directory
    from metro_data import build_shanghai_lines
    d=active_directory(root)
    fs=[d/"china_metro_route_catalog.json",d/"china_metro_routes.geojson",d/"china_metro_stations.geojson"]
    if not all(p.is_file() for p in fs): raise SystemExit("请先在 RailScope 中导入全国地铁 OSM 数据")
    c=json.loads(fs[0].read_text(encoding="utf-8")); r=json.loads(fs[1].read_text(encoding="utf-8")); s=json.loads(fs[2].read_text(encoding="utf-8"))
    base=build_shanghai_lines(c["routes"],r["features"],s["features"])
    expanded=[]
    for line in base:
        expanded.append(line)
        for v in line.get("variants",[]):
            expanded.append({**line,**v,"id":line["id"]+"@"+str(v["relation_id"]),"name":line["name"]+" · "+v["source_name"]})
    return expanded,d

def choose(lines,base_id,required):
    cand=[]
    for l in lines:
        if base_id and not (l["id"]==base_id or l["id"].startswith(base_id+"@")): continue
        names=[x["name"] for x in l.get("stations",[])]
        if all(any(name_match(x,n) for x in names) for n in required): cand.append(l)
    if not cand: raise ValueError(f"找不到线路 {base_id or '*'}，要求站点 {required}")
    return max(cand,key=lambda x:len(x.get("stations",[])))

def resolve(line,canonical):
    out={}
    for n in canonical:
        m=[x for x in line["stations"] if name_match(x["name"],n)]
        if not m: raise ValueError(f"{line['name']} 缺少车站 {n}")
        out[n]=min(m,key=lambda x:x["distance_m"])
    return out

def trip(line,p,origin,dest,dep,tid,source,pass_stations=None):
    canonical=p["stations"]; f=p["forward_offsets_min"]; r=p["reverse_offsets_min"]
    rs=resolve(line,canonical); oi,di=canonical.index(origin),canonical.index(dest)
    local=line["stations"]; a,b=local.index(rs[origin]),local.index(rs[dest])
    direction="forward" if b>a else "reverse"
    ls=local[a:b+1] if b>a else local[a:b-1:-1]
    expected=canonical[oi:di+1] if di>oi else canonical[oi:di-1:-1]
    if len(ls)!=len(expected) or any(not name_match(x["name"],n) for x,n in zip(ls,expected)):
        raise ValueError(f"{line['name']} {origin}->{dest} 本地站序与公开站序不一致")
    if di>oi:
        base=f[oi]; offs=[(f[i]-base)*60 for i in range(oi,di+1)]
    else:
        n=len(canonical); ro=lambda i:r[n-1-i]; base=ro(oi); offs=[(ro(i)-base)*60 for i in range(oi,di-1,-1)]
    stops=[]; pass_stations=set(pass_stations or [])
    for k,(st,o,nm) in enumerate(zip(ls,offs,expected)):
        control=dep+o
        ispass=nm in pass_stations
        if k in (0,len(ls)-1) or ispass: arr=leave=control
        else:
            prev=stops[-1]["departure_s"]; dwell=min(DWELL,max(0,control-prev-1)); arr,leave=control-dwell,control
        rec={"station_id":st["id"],"arrival_s":int(arr),"departure_s":int(leave),"distance_m":st["distance_m"]}
        if ispass: rec["extensions"]={"user.local/pass":{"pass":True}}
        stops.append(rec)
    return {"id":tid,"line_id":line["id"],"direction":direction,"enabled":True,"source":source,"stops":stops}

def deps(a,b,h,phase=0):
    t=hms(a)+phase; e=hms(b); out=[]
    while t<=e: out.append(t); t+=h
    return out

def add(tr,line,p,o,d,a,b,h,tag,phase=0,source_extra=""):
    for x in deps(a,b,h,phase):
        tr.append(trip(line,p,o,d,x,f"{tag}-{fmt(x).replace(':','')}",
            f"公开资料重建，非官方逐车次运行图；{o}→{d}；按{h}秒平均间隔铺图。{source_extra}"))

def special_trip(line,p,origin,dest,dep,tid,controls,source):
    canonical=p["stations"]; rs=resolve(line,canonical)
    oi,di=canonical.index(origin),canonical.index(dest)
    seq=canonical[oi:di+1] if di>oi else canonical[oi:di-1:-1]
    f=p["forward_offsets_min"]; r=p["reverse_offsets_min"]; n=len(canonical)
    if di>oi:
        base=f[oi]; normal=[(f[i]-base)*60 for i in range(oi,di+1)]
    else:
        ro=lambda i:r[n-1-i]; base=ro(oi); normal=[(ro(i)-base)*60 for i in range(oi,di-1,-1)]
    ctrl=[(seq.index(name),sec) for name,sec in controls.items()]
    ctrl.sort()
    rel=[0]*len(seq)
    for (ia,ta),(ib,tb) in zip(ctrl,ctrl[1:]):
        na,nb=normal[ia],normal[ib]
        for j in range(ia,ib+1):
            q=0 if nb==na else (normal[j]-na)/(nb-na)
            rel[j]=round(ta+q*(tb-ta))
    passset={x for x in seq if x not in controls}
    # Build using local geometry but custom times.
    local=line["stations"]; aidx=local.index(rs[origin]); bidx=local.index(rs[dest])
    direction="forward" if bidx>aidx else "reverse"; ls=local[aidx:bidx+1] if bidx>aidx else local[aidx:bidx-1:-1]
    stops=[]
    for k,(st,nm,t) in enumerate(zip(ls,seq,rel)):
        rec={"station_id":st["id"],"arrival_s":dep+t,"departure_s":dep+t,"distance_m":st["distance_m"]}
        if nm in passset: rec["extensions"]={"user.local/pass":{"pass":True}}
        stops.append(rec)
    return {"id":tid,"line_id":line["id"],"direction":direction,"enabled":True,"source":source,"stops":stops}

def build10(lines,cfg,day):
    phz=cfg["profiles"]["hangzhong"]; phq=cfg["profiles"]["hongqiao"]
    lhz=choose(lines,"sh-10",["航中路","基隆路"]); lhq=choose(lines,"sh-10",["虹桥火车站","基隆路"])
    tr=[]
    if day=="weekday":
        # early representative
        for j,(a,b) in enumerate([("05:30","07:00"),("20:00","22:20")]):
            add(tr,lhq,phq,"虹桥火车站","基隆路",a,b,600,f"L10-HQ-JL-O{j}")
            add(tr,lhz,phz,"航中路","江湾体育场",a,b,900,f"L10-HZ-JW-O{j}",300)
        # AM representative solution to published ranges
        add(tr,lhq,phq,"虹桥火车站","基隆路","07:00","09:00",270,"L10-AM-HQ-JL")
        add(tr,lhq,phq,"虹桥火车站","江湾体育场","07:00","09:00",1350,"L10-AM-HQ-JW",135)
        add(tr,lhz,phz,"航中路","江湾体育场","07:00","09:00",450,"L10-AM-HZ-JW",225)
        # flat exact density solution
        for o,d,l,pfx,phase in [("虹桥火车站","基隆路",lhq,phq,0),("虹桥火车站","江湾体育场",lhq,phq,240),("航中路","基隆路",lhz,phz,480)]:
            add(tr,l,pfx,o,d,"09:00","17:30",720,"L10-FLAT-"+("HQJL" if d=="基隆路" and o=="虹桥火车站" else "HQJW" if o=="虹桥火车站" else "HZJL"),phase)
        # PM exact representative
        add(tr,lhq,phq,"虹桥火车站","基隆路","17:30","20:00",270,"L10-PM-HQ-JL")
        add(tr,lhz,phz,"航中路","江湾体育场","17:30","20:00",540,"L10-PM-HZ-JW",135)
    else:
        # weekend: HQ-JL 10m + HZ-XJC 10m + HQ-JW 15m -> published segment densities
        for a,b in [("05:30","07:00"),("22:30","23:00")]:
            add(tr,lhq,phq,"虹桥火车站","基隆路",a,b,720,"L10-WE-OUT-HQ")
            add(tr,lhz,phz,"航中路","新江湾城",a,b,720,"L10-WE-OUT-HZ",360)
        add(tr,lhq,phq,"虹桥火车站","基隆路","07:00","22:30",600,"L10-WE-HQ-JL")
        add(tr,lhz,phz,"航中路","新江湾城","07:00","22:30",600,"L10-WE-HZ-XJC",300)
        add(tr,lhq,phq,"虹桥火车站","江湾体育场","07:00","22:30",900,"L10-WE-HQ-JW",150)
    return tr

def build13(lines,c,day):
    l=choose(lines,"sh-13",["金运路","张江路"]); p={"stations":c["stations"],"forward_offsets_min":c["forward_offsets_min"],"reverse_offsets_min":c["reverse_offsets_min"]}; tr=[]
    if day=="weekday":
        for a,b,h in [("05:30","07:30",480),("09:30","17:00",420),("19:30","22:30",480)]:
            add(tr,l,p,"金运路","张江路",a,b,h,"L13-FULL-F"); add(tr,l,p,"张江路","金运路",a,b,h,"L13-FULL-R",h//2)
        for a,b,h in [("07:30","09:30",330),("17:00","19:30",360)]:
            add(tr,l,p,"金运路","张江路",a,b,h,"L13-PEAK-FULL-F"); add(tr,l,p,"张江路","金运路",a,b,h,"L13-PEAK-FULL-R",h//2)
            add(tr,l,p,"金运路","华鹏路",a,b,h,"L13-PEAK-SHORT-F",h//2); add(tr,l,p,"华鹏路","金运路",a,b,h,"L13-PEAK-SHORT-R")
    else:
        add(tr,l,p,"金运路","张江路","05:30","08:30",480,"L13-WE-OUT-F"); add(tr,l,p,"张江路","金运路","05:30","08:30",480,"L13-WE-OUT-R",240)
        add(tr,l,p,"金运路","张江路","08:30","20:30",360,"L13-WE-PEAK-F"); add(tr,l,p,"张江路","金运路","08:30","20:30",360,"L13-WE-PEAK-R",180)
        add(tr,l,p,"金运路","张江路","20:30","22:30",480,"L13-WE-LATE-F"); add(tr,l,p,"张江路","金运路","20:30","22:30",480,"L13-WE-LATE-R",240)
    return tr

def build14(lines,c,day):
    l=choose(lines,"sh-14",["封浜","桂桥路"]); p={"stations":c["stations"],"forward_offsets_min":c["forward_offsets_min"],"reverse_offsets_min":c["reverse_offsets_min"]}; tr=[]
    periods=([("05:30","07:00",600),("07:00","09:20",400),("09:20","16:40",720),("16:40","20:00",480),("20:00","22:30",600)]
             if day=="weekday" else [("05:30","07:00",600),("07:00","21:00",600),("21:00","22:30",600)])
    for j,(a,b,h) in enumerate(periods):
        add(tr,l,p,"封浜","桂桥路",a,b,h,f"L14-FULL-F-{j}"); add(tr,l,p,"桂桥路","封浜",a,b,h,f"L14-FULL-R-{j}",h//2)
        # Current public structure: short route Zhenxin Xincun - Lantian Road; same headway as full during principal periods.
        add(tr,l,p,"真新新村","蓝天路",a,b,h,f"L14-SHORT-F-{j}",h//2); add(tr,l,p,"蓝天路","真新新村",a,b,h,f"L14-SHORT-R-{j}")
    return tr

def build15(lines,c,day):
    l=choose(lines,"sh-15",["紫竹高新区","顾村公园"]); p={"stations":c["stations"],"forward_offsets_min":c["forward_offsets_min"],"reverse_offsets_min":c["reverse_offsets_min"]}; tr=[]
    if day=="weekday":
        periods=[("05:30","07:00",720,720,None),("07:00","09:00",440,440,880),("09:00","16:30",720,720,None),("16:30","20:00",540,540,1080),("20:00","22:30",720,720,None)]
    else:
        periods=[("05:30","08:00",780,780,None),("08:00","19:00",720,720,None),("19:00","22:30",780,780,None)]
    for j,(a,b,fullh,shorth,northh) in enumerate(periods):
        add(tr,l,p,"紫竹高新区","顾村公园",a,b,fullh,f"L15-FULL-F-{j}"); add(tr,l,p,"顾村公园","紫竹高新区",a,b,fullh,f"L15-FULL-R-{j}",fullh//2)
        add(tr,l,p,"双柏路","古浪路",a,b,shorth,f"L15-SHORT-F-{j}",shorth//2,source_extra="双柏路—古浪路小交路已由公开资料确认。")
        add(tr,l,p,"古浪路","双柏路",a,b,shorth,f"L15-SHORT-R-{j}",source_extra="双柏路—古浪路小交路已由公开资料确认。")
        if northh:
            note="为匹配公开的古浪路—顾村公园高峰平均间隔而加入的推定补充运行线；实际逐趟交路未公开确认。"
            add(tr,l,p,"古浪路","顾村公园",a,b,northh,f"L15-INFER-NORTH-F-{j}",northh//2,note)
            add(tr,l,p,"顾村公园","古浪路",a,b,northh,f"L15-INFER-NORTH-R-{j}",0,note)
    return tr

def build16(lines,c,day):
    l=choose(lines,"sh-16",["龙阳路","滴水湖"]); p={"stations":c["stations"],"forward_offsets_min":c["forward_offsets_min"],"reverse_offsets_min":c["reverse_offsets_min"]}; tr=[]
    if day=="weekday":
        for a,b,h in [("05:50","07:30",600),("07:30","10:30",220),("10:30","16:30",480),("16:30","21:30",257),("21:30","22:30",600)]:
            add(tr,l,p,"龙阳路","滴水湖",a,b,h,"L16-LOCAL-F"); add(tr,l,p,"滴水湖","龙阳路",a,b,h,"L16-LOCAL-R",h//2)
        bf=["07:00"]+[f"{h:02d}:00" for h in range(10,22)]
        br=["07:00"]+[f"{h:02d}:00" for h in range(10,18)]+["19:00","20:00","21:00"]
        for t in bf:
            d=hms(t); tr.append(special_trip(l,p,"龙阳路","滴水湖",d,"L16-RAPID-F-"+t.replace(":",""),
                {"龙阳路":0,"罗山路":360,"新场":1080,"惠南":1560,"临港大道":2580,"滴水湖":2760},
                "公开大站车时刻重建；仅停龙阳路、罗山路、新场、惠南、临港大道、滴水湖。"))
        for t in br:
            d=hms(t); tr.append(special_trip(l,p,"滴水湖","龙阳路",d,"L16-RAPID-R-"+t.replace(":",""),
                {"滴水湖":0,"临港大道":180,"惠南":1260,"新场":1740,"罗山路":2460,"龙阳路":2760},
                "公开大站车时刻重建；中间非指定站为通过。"))
        for t in ["07:30","07:45"]:
            d=hms(t); tr.append(special_trip(l,p,"龙阳路","滴水湖",d,"L16-DIRECT-F-"+t.replace(":",""),
                {"龙阳路":0,"滴水湖":2040},"公开工作日直达车；中间站全部通过。"))
        for t in ["17:20","18:00"]:
            d=hms(t); tr.append(special_trip(l,p,"滴水湖","龙阳路",d,"L16-DIRECT-R-"+t.replace(":",""),
                {"滴水湖":0,"龙阳路":2040},"公开工作日直达车；中间站全部通过。"))
    else:
        for a,b,h in [("06:00","07:00",600),("07:00","20:30",390),("20:30","22:30",600)]:
            add(tr,l,p,"龙阳路","滴水湖",a,b,h,"L16-WE-LOCAL-F"); add(tr,l,p,"滴水湖","龙阳路",a,b,h,"L16-WE-LOCAL-R",h//2)
        for hr in range(7,22):
            t=f"{hr:02d}:00"; d=hms(t)
            tr.append(special_trip(l,p,"龙阳路","滴水湖",d,"L16-WE-RAPID-F-"+t.replace(":",""),{"龙阳路":0,"罗山路":360,"新场":1080,"惠南":1560,"临港大道":2580,"滴水湖":2760},"公开双休日大站车时刻重建。"))
            tr.append(special_trip(l,p,"滴水湖","龙阳路",d,"L16-WE-RAPID-R-"+t.replace(":",""),{"滴水湖":0,"临港大道":180,"惠南":1260,"新场":1740,"罗山路":2460,"龙阳路":2760},"公开双休日大站车时刻重建。"))
    return tr

def build17(lines,c,day):
    l=choose(lines,"sh-17",["西岑","虹桥火车站"]); p={"stations":c["stations"],"forward_offsets_min":c["forward_offsets_min"],"reverse_offsets_min":c["reverse_offsets_min"]}; tr=[]
    if day=="weekday":
        for a,b,h in [("05:30","07:00",600),("09:30","17:00",600),("20:00","23:00",600)]:
            add(tr,l,p,"西岑","虹桥火车站",a,b,h,"L17-FULL-F"); add(tr,l,p,"虹桥火车站","西岑",a,b,h,"L17-FULL-R",h//2)
        for a,b,h in [("07:00","09:30",360),("17:00","20:00",480)]:
            add(tr,l,p,"西岑","虹桥火车站",a,b,h,"L17-PEAK-FULL-F"); add(tr,l,p,"虹桥火车站","西岑",a,b,h,"L17-PEAK-FULL-R",h//2)
            add(tr,l,p,"淀山湖大道","虹桥火车站",a,b,h,"L17-PEAK-SHORT-F",h//2); add(tr,l,p,"虹桥火车站","淀山湖大道",a,b,h,"L17-PEAK-SHORT-R")
    else:
        add(tr,l,p,"西岑","虹桥火车站","05:30","07:30",600,"L17-WE-OUT-F"); add(tr,l,p,"虹桥火车站","西岑","05:30","07:30",600,"L17-WE-OUT-R",300)
        add(tr,l,p,"西岑","虹桥火车站","07:30","21:00",420,"L17-WE-PEAK-F"); add(tr,l,p,"虹桥火车站","西岑","07:30","21:00",420,"L17-WE-PEAK-R",210)
        add(tr,l,p,"西岑","虹桥火车站","21:00","23:00",600,"L17-WE-LATE-F"); add(tr,l,p,"虹桥火车站","西岑","21:00","23:00",600,"L17-WE-LATE-R",300)
    return tr

def build18(lines,c,day):
    l=choose(lines,"sh-18",["航头","康文路"]); p={"stations":c["stations"],"forward_offsets_min":c["forward_offsets_min"],"reverse_offsets_min":c["reverse_offsets_min"]}; tr=[]
    if day=="weekday":
        # Off peak single full route
        for a,b,h in [("05:30","07:00",600),("09:00","17:00",450),("19:00","22:16",600)]:
            add(tr,l,p,"航头","康文路",a,b,h,"L18-FULL-F"); add(tr,l,p,"康文路","航头",a,b,h,"L18-FULL-R",h//2)
        # AM: published patterns, chosen headways reproduce ~6m south outer, ~4m north extension, ~2:40/3:00 core.
        add(tr,l,p,"航头","康文路","07:00","09:00",360,"L18-AM-FULL-F")
        add(tr,l,p,"沈梅路","长江南路","07:00","09:00",480,"L18-AM-SM-CJ-F",120)
        add(tr,l,p,"沈梅路","康文路","07:00","09:00",720,"L18-AM-SM-KW-F",240)
        add(tr,l,p,"康文路","航头","07:00","09:00",360,"L18-AM-FULL-R",180)
        add(tr,l,p,"长江南路","沈梅路","07:00","09:00",540,"L18-AM-CJ-SM-R",90)
        add(tr,l,p,"康文路","沈梅路","07:00","09:00",720,"L18-AM-KW-SM-R",360)
        # PM public route families; exact per-trip phase not public, this is interval-consistent reconstruction.
        add(tr,l,p,"航头","康文路","17:00","19:00",420,"L18-PM-FULL-F")
        add(tr,l,p,"航头","长江南路","17:00","19:00",420,"L18-PM-HT-CJ-F",210)
        add(tr,l,p,"沈梅路","康文路","17:00","19:00",840,"L18-PM-SM-KW-F",105)
        add(tr,l,p,"康文路","航头","17:00","19:00",420,"L18-PM-FULL-R",210)
        add(tr,l,p,"长江南路","航头","17:00","19:00",420,"L18-PM-CJ-HT-R")
        add(tr,l,p,"康文路","沈梅路","17:00","19:00",840,"L18-PM-KW-SM-R",315)
    else:
        add(tr,l,p,"航头","康文路","05:30","22:16",450,"L18-WE-FULL-F")
        add(tr,l,p,"康文路","航头","05:55","22:16",450,"L18-WE-FULL-R",225)
    return tr

def buildP(lines,c,day):
    l=choose(lines,None,["沈杜公路","汇臻路"]); p={"stations":c["stations"],"forward_offsets_min":c["forward_offsets_min"],"reverse_offsets_min":c["reverse_offsets_min"]}; tr=[]
    if day=="weekday":
        for a,b,h in [("05:10","07:30",600),("07:30","09:00",255),("09:00","18:00",600),("18:00","20:00",300),("20:00","22:30",600)]:
            add(tr,l,p,"沈杜公路","汇臻路",a,b,h,"PJ-F"); add(tr,l,p,"汇臻路","沈杜公路",a,b,h,"PJ-R",h//2)
    else:
        add(tr,l,p,"沈杜公路","汇臻路","05:10","22:30",600,"PJ-WE-F"); add(tr,l,p,"汇臻路","沈杜公路","05:10","22:10",600,"PJ-WE-R",300)
    return tr

def payload(tr,lines,day,note):
    return {"schema":"railscope.operating-plan.v2","official":False,"system":"metro","required_capabilities":[],
      "extensions":{"user.local/public-reconstruction":{"lines":lines,"daytype":day,
      "method":"2026官方逐站首末班表 + 公开交路/平均间隔 + 本机 RailScope OSM station_id/distance_m",
      "warning":"不是ATS、司机时刻表或真实车底周转表；推定交路均在source/spec中明确标注。","note":note}},
      "cycles":[],"vehicles":[],"trains":sorted(tr,key=lambda x:(x["stops"][0]["arrival_s"],x["id"]))}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,default=Path.cwd())
    ap.add_argument("--daytype",choices=["weekday","weekend","all"],default="all")
    ap.add_argument("--lines",default="10,13,14,15,16,17,18,浦江")
    ap.add_argument("--output-dir",type=Path,default=None)
    a=ap.parse_args()
    root=find_root(a.root); raw=json.loads(SPEC.read_text(encoding="utf-8")); cfg=raw["lines"]; lines,active=load_lines(root)
    sys.path.insert(0,str(root/"desktop")); from operating import Plan
    builders={"10":build10,"13":build13,"14":build14,"15":build15,"16":build16,"17":build17,"18":build18,"浦江":buildP}
    days=["weekday","weekend"] if a.daytype=="all" else [a.daytype]
    chosen=[x.strip() for x in a.lines.split(",") if x.strip()]
    od=a.output_dir or root/"data"/"processed"/"operations"/"public_reconstruction_final_batch"; od.mkdir(parents=True,exist_ok=True)
    for day in days:
        total=[]
        for no in chosen:
            tr=builders[no](lines,cfg[no],day); p=payload(tr,[no],day,cfg[no].get("note",cfg[no].get("patterns","")))
            path=od/f"shanghai_metro_line{no}_{day}_public_reconstruction.json"
            path.write_text(json.dumps(p,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
            Plan(lines,system="metro").load(path); print("OK",path,"trips=",len(tr)); total+=tr
        p=payload(total,chosen,day,"剩余8条线路合并计划")
        path=od/f"shanghai_metro_final_batch_{day}_public_reconstruction.json"
        path.write_text(json.dumps(p,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
        Plan(lines,system="metro").load(path); print("OK",path,"trips=",len(total))
    print("Active metro dataset:",active)
if __name__=="__main__": main()
