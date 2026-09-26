"""Build local province/city/county boundaries from an OSM PBF snapshot."""
import argparse
from pathlib import Path
from admin_store import build_index, database_path

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pbf", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=database_path(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    build_index(args.pbf, args.output, lambda message: print(message, flush=True))
