"""Export the active railway station directory as station name / OSM node ID CSV."""

import argparse
import csv
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))

from data_install import active_rail_directory
from rail_station_directory import load_directory


def read_overrides(path):
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("overrides", payload)


def main():
    parser = argparse.ArgumentParser(description="导出车站名与 Node ID 对照表")
    parser.add_argument(
        "-o", "--output", type=Path,
        default=ROOT / "output" / "车站名与NodeID.csv",
        help="CSV 输出路径",
    )
    args = parser.parse_args()

    rail_directory = active_rail_directory(ROOT)
    stations = load_directory(rail_directory / "rail_lines.sqlite")
    overrides = {}
    for path in (
        ROOT / "data" / "catalog" / "rail_catalog_overrides.json",
        ROOT / "data" / "catalog" / "rail_station_directory.json",
        ROOT / "data" / "user_settings" / "rail_catalog.json",
    ):
        overrides.update(read_overrides(path))

    rows = []
    for source_id, record in stations.items():
        if not source_id.startswith("node/"):
            continue
        node_id = source_id.removeprefix("node/")
        name = overrides.get("station:" + source_id, {}).get("display_name") or record.get("name", "")
        if not name or name == node_id:
            name = f"节点 {node_id}"
        rows.append((name, node_id))
    rows.sort(key=lambda row: (row[0].casefold(), row[1]))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("车站名", "Node ID"))
        writer.writerows(rows)

    print(f"已导出 {len(rows):,} 个车站：{args.output.resolve()}")


if __name__ == "__main__":
    main()
