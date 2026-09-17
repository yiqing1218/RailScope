"""Extract explicitly tagged, under-construction urban rail from an OSM PBF.

This importer is intentionally conservative.  It does not guess a construction
status from a project name or opening date: an object must either belong to a
construction-state subway/light-rail route relation, or be an explicitly
tagged construction subway/light-rail/metro way.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import gc
from pathlib import Path
from typing import Any

from ...domain import ImportReport
from .metro import (
    OSM_ATTRIBUTION,
    OSM_LICENSE,
    SUPPORTED_ROUTES,
    _atomic_json_write,
    _coordinates,
    _tags,
    color_metadata,
    route_record,
)

_TRUE = frozenset({"yes", "true", "1", "construction", "under_construction"})
_URBAN_CONSTRUCTION = frozenset({"subway", "light_rail", "metro"})


@dataclass(frozen=True)
class ConstructionMetroImportResult:
    geojson_path: Path
    manifest_path: Path
    report: ImportReport


def is_construction_metro_tags(tags: dict[str, str]) -> bool:
    """Whether an OSM tag map explicitly describes construction urban rail."""
    railway = tags.get("railway")
    construction = tags.get("construction", "").strip().lower()
    lifecycle = tags.get("lifecycle", "").strip().lower()
    state = tags.get("state", "").strip().lower()
    if railway == "construction":
        # Some tracks retain the earlier proposed subtype after construction
        # starts (Hefei S1). railway=construction supplies the state; proposed
        # supplies only the subtype. Never include railway=proposed this way.
        for key in ("construction:railway", "construction", "proposed"):
            value = tags.get(key, "").strip().lower()
            if not value or value in _TRUE:
                continue
            return value in _URBAN_CONSTRUCTION
    return railway in SUPPORTED_ROUTES and (
        construction in _TRUE or lifecycle == "construction" or state == "construction"
    )


def construction_feature(way_id, tags, coordinates, route_ids=None, inherited=None):
    """Shared loss-aware feature builder for PBF imports and regional repairs."""
    display_color, color_raw, color_source = color_metadata(tags)
    subtype = next(
        (
            tags[key]
            for key in ("construction:railway", "construction", "proposed")
            if tags.get(key, "").strip().lower() in _URBAN_CONSTRUCTION
        ),
        tags.get("railway"),
    )
    return {
        "type": "Feature",
        "properties": {
            "osm_way_id": way_id,
            "route_relation_ids": route_ids or [],
            "line_key": inherited["line_key"] if inherited else f"osm-way-{way_id}",
            "line_name": inherited["name"]
            if inherited
            else tags.get("name")
            or tags.get("name:zh")
            or tags.get("ref")
            or "未命名在建城市轨道",
            "ref": inherited["ref"] if inherited else tags.get("ref"),
            "operator": inherited["operator"] if inherited else tags.get("operator"),
            "network": inherited["network"] if inherited else tags.get("network"),
            "route": inherited["route"] if inherited else subtype,
            "display_color": inherited["display_color"] if inherited else display_color,
            "color_raw": inherited["color_raw"] if inherited else color_raw,
            "color_source": inherited["color_source"] if inherited else color_source,
            "status": "construction",
            "relation_tags": inherited["relation_tags"] if inherited else {},
            "way_tags": tags,
            "source": "OpenStreetMap",
            "attribution": OSM_ATTRIBUTION,
            "license": OSM_LICENSE,
        },
        "geometry": {"type": "LineString", "coordinates": coordinates},
    }


def _is_construction_route(tags: dict[str, str]) -> bool:
    return (
        tags.get("type") == "route"
        and tags.get("route") in SUPPORTED_ROUTES
        and (
            tags.get("construction", "").strip().lower() in _TRUE
            or tags.get("lifecycle", "").strip().lower() == "construction"
            or tags.get("state", "").strip().lower() == "construction"
        )
    )


def extract_construction_metro(
    pbf_path: Path, output_dir: Path
) -> ConstructionMetroImportResult:
    """Write a loss-aware GeoJSON layer of explicitly tagged construction metro."""
    try:
        import osmium  # type: ignore
    except ImportError as error:  # pragma: no cover
        raise RuntimeError("osmium is required; install railscope[gis]") from error
    if not pbf_path.is_file():
        raise FileNotFoundError(f"OSM PBF was not found: {pbf_path}")

    class RelationHandler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.routes: dict[int, dict[str, Any]] = {}
            self.way_routes: dict[int, list[int]] = {}
            self.relations_read = 0

        def relation(self, relation: Any) -> None:
            self.relations_read += 1
            tags = _tags(relation.tags)
            if not _is_construction_route(tags):
                return
            members = []
            for sequence, member in enumerate(relation.members):
                item = {
                    "sequence": sequence,
                    "type": str(member.type),
                    "ref": int(member.ref),
                    "role": member.role or "",
                }
                members.append(item)
                if str(member.type) == "w":
                    self.way_routes.setdefault(int(member.ref), []).append(
                        int(relation.id)
                    )
            self.routes[int(relation.id)] = route_record(
                int(relation.id), tags, members
            )

    relations = RelationHandler()
    relations.apply_file(str(pbf_path), locations=False)

    class WayHandler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.features: list[dict[str, Any]] = []
            self.invalid_way_ids: list[int] = []

        def way(self, way: Any) -> None:
            way_id = int(way.id)
            tags = _tags(way.tags)
            route_ids = relations.way_routes.get(way_id, [])
            if not route_ids and not is_construction_metro_tags(tags):
                return
            coordinates = _coordinates(way)
            if coordinates is None:
                self.invalid_way_ids.append(way_id)
                return
            inherited = relations.routes.get(route_ids[0]) if route_ids else None
            self.features.append(
                construction_feature(way_id, tags, coordinates, route_ids, inherited)
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    index = output_dir / f".{pbf_path.name}.construction-node-locations.idx"
    way_scan = WayHandler()
    try:
        way_scan.apply_file(
            str(pbf_path), locations=True, idx=f"sparse_file_array,{index}"
        )
        features = way_scan.features
        invalid_way_ids = way_scan.invalid_way_ids
    finally:
        del way_scan
        gc.collect()
        try:
            index.unlink(missing_ok=True)
        except PermissionError:
            pass

    imported_at = datetime.now(UTC).isoformat()
    dataset_slug = pbf_path.name.removesuffix(".osm.pbf").removesuffix("-latest")
    geojson_path = output_dir / f"{dataset_slug}_metro_construction.geojson"
    manifest_path = output_dir / f"{dataset_slug}_metro_construction_manifest.json"
    _atomic_json_write(
        geojson_path,
        {
            "type": "FeatureCollection",
            "name": "china_osm_construction_metro",
            "features": features,
        },
    )
    _atomic_json_write(
        manifest_path,
        {
            "schema": "railscope.osm-construction-metro-import-manifest.v1",
            "source": str(pbf_path),
            "imported_at": imported_at,
            "attribution": OSM_ATTRIBUTION,
            "license": OSM_LICENSE,
            "relations_read": relations.relations_read,
            "construction_route_relations_accepted": len(relations.routes),
            "construction_way_features_written": len(features),
            "missing_or_invalid_way_ids": sorted(set(invalid_way_ids)),
            "scope": "Explicit OSM construction subway/light-rail route relations, plus construction urban-rail ways; railway=construction accepts subtype from construction:railway, construction, or retained proposed tags. Pure proposed tracks are excluded. Raw tags retained.",
        },
    )
    warnings = []
    if invalid_way_ids:
        warnings.append(
            f"{len(set(invalid_way_ids))} construction ways had no recoverable geometry; IDs are listed in the manifest"
        )
    return ConstructionMetroImportResult(
        geojson_path,
        manifest_path,
        ImportReport(
            str(pbf_path),
            relations.relations_read,
            len(features),
            len(invalid_way_ids),
            tuple(warnings),
        ),
    )
