"""Small, attributable OSM high-speed railway importer for bounded desktop requests."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

OVERPASS_ENDPOINTS = (
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)


@dataclass(frozen=True)
class OSMImportResult:
    geojson: dict
    feature_count: int
    source_url: str


def fetch_highspeed_railway(min_lon: float, min_lat: float, max_lon: float, max_lat: float, timeout_s: int = 90) -> OSMImportResult:
    """Fetch rail ways explicitly tagged highspeed=yes within a bounded WGS84 bbox."""
    if min_lon >= max_lon or min_lat >= max_lat or (max_lon - min_lon) * (max_lat - min_lat) > 12:
        raise ValueError("OSM desktop import requires a valid, bounded area no larger than 12 square degrees")
    query = f'''[out:json][timeout:75];way["railway"="rail"]["highspeed"="yes"]({min_lat},{min_lon},{max_lat},{max_lon});out tags geom;'''
    body = urlencode({"data": query}).encode("utf-8")
    last_error: Exception | None = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            request = Request(endpoint, data=body, headers={"User-Agent": "RailScope/0.1 local-desktop-importer", "Accept": "application/json"})
            with urlopen(request, timeout=timeout_s) as response:
                payload = json.load(response)
            features = []
            for way in payload.get("elements", []):
                coordinates = [[point["lon"], point["lat"]] for point in way.get("geometry", [])]
                if len(coordinates) < 2:
                    continue
                tags = way.get("tags", {})
                features.append({"type": "Feature", "properties": {"osm_id": way["id"], "railway": "rail", "highspeed": "yes", "name": tags.get("name"), "ref": tags.get("ref"), "maxspeed": tags.get("maxspeed"), "electrified": tags.get("electrified"), "source": "OpenStreetMap", "attribution": "© OpenStreetMap contributors", "osm_tags": tags}, "geometry": {"type": "LineString", "coordinates": coordinates}})
            return OSMImportResult({"type": "FeatureCollection", "features": features}, len(features), endpoint)
        except Exception as error:  # Network/server failures are retried against the next public endpoint.
            last_error = error
    raise RuntimeError(f"OSM high-speed query failed: {last_error}")


def save_raw_geojson(result: OSMImportResult, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result.geojson, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination
