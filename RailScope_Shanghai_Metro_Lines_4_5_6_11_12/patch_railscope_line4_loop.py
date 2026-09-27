#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path
import argparse, shutil

MARKER = "# RAILSCOPE_LINE4_CIRCULAR_CLOSURE_V1"
ANCHOR = '        lines.append(\n'
INJECT = '''        # RAILSCOPE_LINE4_CIRCULAR_CLOSURE_V1
        # Close Shanghai Metro Line 4 at the operating-plan layer only.
        if ref == "4" and selected.get("path") and selected.get("stations"):
            _p = selected["path"]
            _s = selected["stations"]
            if len(_s) >= 20 and len(_p.get("coordinates", [])) >= 2:
                _gap = distance_m(_p["coordinates"][0], _p["coordinates"][-1])
                if _gap <= 500 and _s[0]["distance_m"] <= 500:
                    _return_station = dict(_s[0])
                    _return_station["distance_m"] = round(_p["length_m"], 3)
                    selected = {**selected, "stations": [*_s, _return_station]}
'''

def find_root(start):
    start=Path(start).resolve()
    for r in [start,*start.parents]:
        if (r/"desktop"/"metro_data.py").is_file():
            return r
    raise SystemExit("找不到 RailScope 根目录")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default=".")
    ap.add_argument("--restore",action="store_true")
    a=ap.parse_args()
    root=find_root(a.root)
    path=root/"desktop"/"metro_data.py"
    bak=root/"desktop"/"metro_data.py.bak-before-line4-loop"
    text=path.read_text(encoding="utf-8")
    if a.restore:
        if not bak.is_file():
            raise SystemExit("没有备份可恢复")
        shutil.copy2(bak,path)
        print("已恢复",path)
        return
    if MARKER in text:
        print("4号线环线补丁已经存在。")
        return
    # Insert immediately before the final lines.append() inside build_shanghai_lines.
    pos=text.find(ANCHOR, text.find('if ref == "1":'))
    if pos < 0:
        raise SystemExit("当前 metro_data.py 与补丁基准不一致；未修改文件。")
    shutil.copy2(path,bak)
    path.write_text(text[:pos]+INJECT+text[pos:],encoding="utf-8")
    print("已应用4号线环线补丁：",path)
    print("备份：",bak)

if __name__=="__main__":
    main()
