"""Build local province/city/county boundaries from an OSM PBF snapshot."""
import argparse
from pathlib import Path
from admin_store import build_index, database_path
from admin_land import ensure_clipped

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pbf", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=database_path(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    build_index(args.pbf, args.output, lambda message: print(message, flush=True))
    print('正在按陆地轮廓裁切行政底图；首次使用会下载约 10 MB 海岸线数据…', flush=True)
    ensure_clipped(args.output)
    print('行政底图已就绪：仅显示陆地范围。', flush=True)
