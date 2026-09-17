from __future__ import annotations
import json
from pathlib import Path
from ...domain import ImportReport
from .overpass import OSMImportResult, fetch_highspeed_railway, save_raw_geojson
from .metro import MetroImportResult, extract_metro_routes
from .construction import ConstructionMetroImportResult, extract_construction_metro


def import_geojson(path: Path) -> ImportReport:
    payload = json.loads(path.read_text(encoding="utf-8"))
    features = payload.get("features", [])
    accepted = [f for f in features if f.get("geometry", {}).get("type") in {"LineString", "Point"}]
    return ImportReport(str(path), len(features), len(accepted), len(features) - len(accepted))


def import_osm_pbf(path: Path) -> ImportReport:
    try:
        import osmium  # type: ignore # optional production parser boundary
    except ImportError:
        return ImportReport(str(path), errors=("osmium is not installed; install railscope[gis] to import PBF",))
    return ImportReport(str(path), warnings=("OSM PBF parser boundary is available; normalization requires a configured database importer.",))
