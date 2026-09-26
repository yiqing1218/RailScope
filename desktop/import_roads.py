"""Build the local national expressway index from a downloaded OSM PBF."""

import argparse
from pathlib import Path
from road_store import build_index, database_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pbf", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=database_path(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    count = build_index(args.pbf, args.output, lambda value: print(value if isinstance(value, str) else f"已提取 {value:,} 个高速路段", flush=True))
    print(f"完成：{count:,} 个高速路段；索引：{args.output}", flush=True)
