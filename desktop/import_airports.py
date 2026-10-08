"""CLI: derive airport outlines and POIs from an existing OSM snapshot."""
import argparse
from pathlib import Path
from airport_store import build_index, database_path, install_index

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--pbf", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--identity", type=Path, help="自定义身份库；需同时指定 --output")
    parser.add_argument("--admin", type=Path, help="自定义行政区索引；需同时指定 --output")
    args = parser.parse_args()
    progress = lambda text: print(text, flush=True)
    if not args.output and (args.identity or args.admin):
        parser.error("--identity / --admin 需同时指定 --output，避免改动活动数据集的身份来源")
    count = build_index(args.pbf, args.output, args.identity or root / "data/user_settings/workspace.sqlite", args.admin or root / "data/processed/admin/admin.sqlite", progress) if args.output else install_index(root, args.pbf, progress)
    print(f"机场图层已建立：{count:,} 个机场；{args.output or database_path(root)}", flush=True)
