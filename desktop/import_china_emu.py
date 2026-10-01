"""Fetch reference facts for RailScope; raw infrastructure is read-only."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3

try:
    from .china_emu import REFERENCE_PATH, ReferenceStore, atomic_json, import_references, line_reference
except ImportError:
    from china_emu import REFERENCE_PATH, ReferenceStore, atomic_json, import_references, line_reference


def local_station_names(directory):
    path = Path(directory) / "rail.sqlite"
    if not path.exists():
        return None
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return [row[0] for row in db.execute(
            "SELECT json_extract(data,'$.properties.name') FROM features WHERE kind='railPoints' "
            "AND json_extract(data,'$.properties.kind') IN ('station','halt','signal_box','junction')"
        ) if row[0]]


def coverage_report(directory, payload):
    store = ReferenceStore(payload["profiles"])
    result = {"counts": payload["counts"], "errors": payload["errors"],
              "matched_line_objects": 0, "matched_line_names": [], "matched_station_names": [],
              "note": "精确名称与地区/线路证据匹配；参数为未核验参考。原始基础设施只读。"}
    path = Path(directory) / "rail_catalog.sqlite"
    names = Counter()
    if path.exists():
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            for (data,) in db.execute("SELECT data FROM catalog WHERE unnamed=0"):
                record = json.loads(data)
                if line_reference(record, store):
                    result["matched_line_objects"] += 1
                    names[record.get("line_name") or record.get("name")] += 1
    result["matched_line_names"] = [{"name": name, "objects": count} for name, count in sorted(names.items())]
    wanted = set(local_station_names(directory) or [])
    from_station = {p["name"].removesuffix("站") for p in payload["profiles"] if p["kind"] == "station"}
    result["matched_station_names"] = sorted(n for n in wanted if n.removesuffix("站") in from_station)
    result["station_name_candidates"] = len(result["matched_station_names"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REFERENCE_PATH)
    parser.add_argument("--rail-directory", type=Path, help="只读底图目录；仅抓取已有车站")
    args = parser.parse_args()
    names = local_station_names(args.rail_directory) if args.rail_directory else None
    payload = import_references(args.output, station_names=names, progress=lambda m: print(m, flush=True))
    if args.rail_directory:
        report = coverage_report(args.rail_directory, payload)
        atomic_json(args.output.parent / "coverage.json", report)
    print(json.dumps(payload["counts"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
