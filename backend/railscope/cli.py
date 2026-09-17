from __future__ import annotations
import argparse
from pathlib import Path
from .demo import load_demo
from .services.topology import validate_topology
from .services.importers import import_geojson, import_osm_pbf
from .services.importers import extract_construction_metro, extract_metro_routes


def main() -> None:
    parser = argparse.ArgumentParser(prog="railscope")
    command = parser.add_subparsers(dest="command", required=True)
    demo = command.add_parser("demo"); demo.add_argument("action", choices=["load"])
    topology = command.add_parser("topology"); topology.add_argument("action", choices=["validate"])
    gis = command.add_parser("gis"); gis.add_argument("action", choices=["import"]); gis.add_argument("path")
    osm = command.add_parser("osm"); osm.add_argument("action", choices=["import"]); osm.add_argument("path")
    metro = command.add_parser("metro"); metro.add_argument("action", choices=["import", "construction"]); metro.add_argument("path"); metro.add_argument("--output", default="../data/processed/osm")
    args = parser.parse_args()
    if args.command == "demo":
        repo = load_demo(); print({"demo": "loaded", "stations": len(repo.stations), "edges": len(repo.edges), "conflicts": len(repo.conflicts)})
    elif args.command == "topology":
        print(validate_topology(load_demo()))
    elif args.command == "gis":
        print(import_geojson(Path(args.path)))
    elif args.command == "metro":
        result = extract_metro_routes(Path(args.path), Path(args.output)) if args.action == "import" else extract_construction_metro(Path(args.path), Path(args.output))
        print(result.report)
        print({"geojson": str(result.geojson_path), "manifest": str(result.manifest_path), **({"catalog": str(result.catalog_path)} if hasattr(result, "catalog_path") else {})})
    else:
        print(import_osm_pbf(Path(args.path)))


if __name__ == "__main__":
    main()
