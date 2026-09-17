"""Loss-aware OSM PBF importer for Chinese urban metro route relations.

The importer deliberately reads *route relations* before reading railway ways.
That is the only dependable way to retain a line's name, reference, operator,
network, colour and ordered membership rather than treating disconnected track
geometry as a guessed metro line.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
import gc
import json
from pathlib import Path
import re
from typing import Any

from ...domain import ImportReport

OSM_ATTRIBUTION = "© OpenStreetMap contributors"
OSM_LICENSE = "ODbL 1.0"
SUPPORTED_ROUTES = frozenset({"subway", "light_rail"})
FALLBACK_COLOR = "#64748b"
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?(?:[0-9a-fA-F]{2})?$")
_CSS_COLORS = frozenset({
    "aliceblue", "aqua", "aquamarine", "azure", "beige", "black", "blue",
    "brown", "chartreuse", "chocolate", "coral", "crimson", "cyan", "gold",
    "goldenrod", "gray", "green", "indigo", "ivory", "khaki", "lime",
    "magenta", "maroon", "navy", "olive", "orange", "orchid", "pink", "plum",
    "purple", "red", "salmon", "silver", "teal", "tomato", "turquoise",
    "violet", "white", "yellow", "yellowgreen",
})


@dataclass(frozen=True)
class MetroImportResult:
    geojson_path: Path
    catalog_path: Path
    manifest_path: Path
    report: ImportReport


def _tags(tags: Any) -> dict[str, str]:
    return {str(key): str(value) for key, value in tags}


def color_metadata(tags: dict[str, str]) -> tuple[str, str | None, str]:
    """Return display colour, unmodified OSM value, and its provenance."""
    for key in ("colour", "color"):
        raw = tags.get(key)
        if raw is None:
            continue
        candidate = raw.strip()
        if _HEX_COLOR.fullmatch(candidate) or candidate.lower() in _CSS_COLORS:
            return candidate, raw, f"osm:{key}"
        # Preserve an invalid/non-renderable source value verbatim in metadata;
        # never invent a brand colour for a real metro route.
        return FALLBACK_COLOR, raw, f"unrenderable:osm:{key}"
    return FALLBACK_COLOR, None, "missing"


def route_record(relation_id: int, tags: dict[str, str], members: list[dict[str, Any]]) -> dict[str, Any]:
    display_color, color_raw, color_source = color_metadata(tags)
    return {
        "osm_relation_id": relation_id,
        "line_key": f"osm-relation-{relation_id}",
        "name": tags.get("name") or tags.get("name:zh") or tags.get("ref") or f"OSM relation {relation_id}",
        "ref": tags.get("ref"),
        "operator": tags.get("operator"),
        "network": tags.get("network"),
        "route": tags.get("route"),
        "display_color": display_color,
        "color_raw": color_raw,
        "color_source": color_source,
        "relation_tags": tags,
        "members": members,
        "source": "OpenStreetMap",
        "attribution": OSM_ATTRIBUTION,
        "license": OSM_LICENSE,
    }


class _MetroRelationsHandler:  # constructed only after osmium is available
    def __init__(self) -> None:
        import osmium  # type: ignore

        class Handler(osmium.SimpleHandler):
            def __init__(inner) -> None:
                super().__init__()
                inner.routes: dict[int, dict[str, Any]] = {}
                inner.required_way_ids: set[int] = set()
                inner.required_node_ids: set[int] = set()
                inner.node_routes: dict[int, set[int]] = {}
                inner.relations_read = 0

            def relation(inner, relation: Any) -> None:
                inner.relations_read += 1
                tags = _tags(relation.tags)
                if tags.get("type") != "route" or tags.get("route") not in SUPPORTED_ROUTES:
                    return
                members = []
                for sequence, member in enumerate(relation.members):
                    item = {"sequence": sequence, "type": str(member.type), "ref": int(member.ref), "role": member.role or ""}
                    members.append(item)
                    if str(member.type) == "w":
                        inner.required_way_ids.add(int(member.ref))
                    if str(member.type) == "n":
                        inner.required_node_ids.add(int(member.ref))
                        inner.node_routes.setdefault(int(member.ref), set()).add(int(relation.id))
                inner.routes[int(relation.id)] = route_record(int(relation.id), tags, members)

        self.handler = Handler()


def _coordinates(way: Any) -> list[list[float]] | None:
    coordinates: list[list[float]] = []
    try:
        for node in way.nodes:
            if not node.location.valid():
                return None
            coordinates.append([node.location.lon, node.location.lat])
    except RuntimeError:
        return None
    return coordinates if len(coordinates) >= 2 else None


def _atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def extract_metro_routes(pbf_path: Path, output_dir: Path) -> MetroImportResult:
    """Extract OSM subway/light-rail relations and their complete member-way geometry.

    This does not infer routes from track tags. Relations whose member way has no
    recoverable geometry are counted in the manifest, so omissions are visible.
    """
    try:
        import osmium  # type: ignore
    except ImportError as error:  # pragma: no cover - exercised by dependency boundary
        raise RuntimeError("osmium is required; install railscope[gis]") from error
    if not pbf_path.is_file():
        raise FileNotFoundError(f"OSM PBF was not found: {pbf_path}")

    relation_scan = _MetroRelationsHandler().handler
    relation_scan.apply_file(str(pbf_path), locations=False)
    routes: dict[int, dict[str, Any]] = relation_scan.routes
    required_way_ids: set[int] = relation_scan.required_way_ids

    class StationHandler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.nodes: dict[int, dict[str, Any]] = {}
        def node(self, node: Any) -> None:
            node_id = int(node.id)
            if node_id not in relation_scan.required_node_ids:
                return
            tags = _tags(node.tags)
            if tags.get("public_transport") not in {"platform", "stop_position", "station"} and tags.get("railway") not in {"station", "halt"}:
                return
            self.nodes[node_id] = {"tags": tags, "coordinates": [node.location.lon, node.location.lat]}

    station_scan = StationHandler()
    station_scan.apply_file(str(pbf_path), locations=False)

    class WayHandler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.ways: dict[int, tuple[dict[str, str], list[list[float]]]] = {}

        def way(self, way: Any) -> None:
            way_id = int(way.id)
            if way_id not in required_way_ids:
                return
            coordinates = _coordinates(way)
            if coordinates is not None:
                self.ways[way_id] = (_tags(way.tags), coordinates)

    way_scan = WayHandler()
    # Node locations are needed to produce WGS84 LineStrings.  A file-backed
    # sparse index avoids keeping China's complete node coordinate index in RAM.
    output_dir.mkdir(parents=True, exist_ok=True)
    location_index = output_dir / f".{pbf_path.name}.node-locations.idx"
    way_geometries: dict[int, tuple[dict[str, str], list[list[float]]]] = {}
    try:
        way_scan.apply_file(str(pbf_path), locations=True, idx=f"sparse_file_array,{location_index}")
        way_geometries = way_scan.ways
    finally:
        # pyosmium may release the native file mapping only after a collection
        # cycle on Windows.  A failed cleanup must not discard a completed import.
        del way_scan
        gc.collect()
        try:
            location_index.unlink(missing_ok=True)
        except PermissionError:
            pass

    features: list[dict[str, Any]] = []
    missing_way_ids: set[int] = set()
    color_sources: Counter[str] = Counter()
    for relation_id in sorted(routes):
        route = routes[relation_id]
        color_sources[route["color_source"]] += 1
        for member in route["members"]:
            if member["type"] != "w":
                continue
            item = way_geometries.get(member["ref"])
            if item is None:
                missing_way_ids.add(member["ref"])
                continue
            way_tags, coordinates = item
            # Feature properties intentionally carry both exact raw tag maps.
            # This is the lossless source record used by the desktop renderer.
            properties = {
                "route_relation_id": relation_id,
                "line_key": route["line_key"],
                "line_name": route["name"],
                "ref": route["ref"],
                "operator": route["operator"],
                "network": route["network"],
                "route": route["route"],
                "display_color": route["display_color"],
                "color_raw": route["color_raw"],
                "color_source": route["color_source"],
                "osm_way_id": member["ref"],
                "member_role": member["role"],
                "member_sequence": member["sequence"],
                "relation_tags": route["relation_tags"],
                "way_tags": way_tags,
                "source": "OpenStreetMap",
                "attribution": OSM_ATTRIBUTION,
                "license": OSM_LICENSE,
            }
            features.append({"type": "Feature", "properties": properties, "geometry": {"type": "LineString", "coordinates": coordinates}})

    imported_at = datetime.now(UTC).isoformat()
    geojson = {"type": "FeatureCollection", "name": "china_osm_metro_routes", "features": features}
    station_features = []
    for node_id, station in station_scan.nodes.items():
        tags = station["tags"]
        station_features.append({"type": "Feature", "properties": {"osm_node_id": node_id, "name": tags.get("name") or tags.get("name:zh") or "未命名地铁站点", "station_tags": tags, "route_relation_ids": sorted(relation_scan.node_routes[node_id]), "source": "OpenStreetMap", "attribution": OSM_ATTRIBUTION}, "geometry": {"type": "Point", "coordinates": station["coordinates"]}})
    stations_geojson = {"type": "FeatureCollection", "name": "china_osm_metro_stations", "features": station_features}
    catalog = {
        "schema": "railscope.osm-metro-route-catalog.v1",
        "source": str(pbf_path), "imported_at": imported_at,
        "attribution": OSM_ATTRIBUTION, "license": OSM_LICENSE,
        "routes": [routes[relation_id] for relation_id in sorted(routes)],
    }
    manifest = {
        "schema": "railscope.osm-metro-import-manifest.v1",
        "source": str(pbf_path), "imported_at": imported_at,
        "attribution": OSM_ATTRIBUTION, "license": OSM_LICENSE,
        "relations_read": relation_scan.relations_read,
        "route_relations_accepted": len(routes),
        "referenced_way_ids": len(required_way_ids),
        "ways_with_geometry": len(way_geometries),
        "line_member_features_written": len(features),
        "metro_station_features_written": len(station_features),
        "missing_or_invalid_way_ids": sorted(missing_way_ids),
        "color_sources": dict(sorted(color_sources.items())),
        "scope": "OSM type=route and route in {subway, light_rail}; raw relation and way tags retained per feature",
    }
    # Path.stem only strips '.pbf' and would leave a misleading '.osm' in
    # output names for the conventional '<region>-latest.osm.pbf' download.
    dataset_slug = pbf_path.name.removesuffix(".osm.pbf").removesuffix("-latest")
    geojson_path = output_dir / f"{dataset_slug}_metro_routes.geojson"
    stations_path = output_dir / f"{dataset_slug}_metro_stations.geojson"
    catalog_path = output_dir / f"{dataset_slug}_metro_route_catalog.json"
    manifest_path = output_dir / f"{dataset_slug}_metro_import_manifest.json"
    _atomic_json_write(geojson_path, geojson)
    _atomic_json_write(stations_path, stations_geojson)
    _atomic_json_write(catalog_path, catalog)
    _atomic_json_write(manifest_path, manifest)
    warnings: list[str] = []
    if missing_way_ids:
        warnings.append(f"{len(missing_way_ids)} referenced ways had no recoverable geometry; IDs are listed in the manifest")
    unrenderable = color_sources.get("missing", 0) + sum(count for source, count in color_sources.items() if source.startswith("unrenderable:"))
    if unrenderable:
        warnings.append(f"{unrenderable} route relations have no renderable OSM colour; map uses neutral grey and retains raw values")
    return MetroImportResult(geojson_path, catalog_path, manifest_path, ImportReport(str(pbf_path), relation_scan.relations_read, len(features), len(missing_way_ids), tuple(warnings)))
