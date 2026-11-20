"""Build display-only MVT layers in PMTiles v3 archives."""
import gzip
import json
import math
from collections import defaultdict

import mapbox_vector_tile
import numpy as np
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer
from shapely import from_wkb, make_valid, transform
from shapely.geometry import box, shape

LAYERS = {
    "rail": ("rail", 3),
    "metro": ("metro", 8),
    "road": ("road", 3),
    "station": ("points", 9),
    "poi": ("points", 10),
    "facility": ("facilities", 12),
}


def project(longitude, latitude):
    lat = np.deg2rad(np.clip(latitude, -85.05112878, 85.05112878))
    return (longitude + 180) / 360, (1 - np.arcsinh(np.tan(lat)) / math.pi) / 2


def tile_bounds(z, x, y):
    step = 2 ** -z
    return x * step, y * step, (x + 1) * step, (y + 1) * step


def write_archive(path, groups, bounds, maxzoom):
    west, south, east, north = bounds
    with path.open("wb") as file:
        writer = Writer(file)
        for tile_id, data in groups:
            writer.write_tile(tile_id, gzip.compress(data, mtime=0))
        writer.finalize(
            {"tile_type": TileType.MVT, "tile_compression": Compression.GZIP,
             "min_lon_e7": round(west * 1e7), "min_lat_e7": round(south * 1e7),
             "max_lon_e7": round(east * 1e7), "max_lat_e7": round(north * 1e7),
             "center_lon_e7": round((west + east) / 2 * 1e7),
             "center_lat_e7": round((south + north) / 2 * 1e7),
             "center_zoom": min(10, maxzoom)},
            {"vector_layers": [{"id": layer, "fields": {"rid": "Number", "class": "String", "status": "String", "label": "String"}}
                               for layer in sorted({"land", *(layer for layer, _ in LAYERS.values())})]}
        )


def components(geometry):
    if geometry.geom_type == "GeometryCollection":
        for part in geometry.geoms:
            yield from components(part)
    elif not geometry.is_empty:
        yield geometry


def zoom_tiles(db, z, land):
    buckets = defaultdict(lambda: defaultdict(list))
    count = 2 ** z
    step = 1 / count
    padding = step / 128
    sql = """SELECT kind, class, status, render_id, wgs84_wkb, name FROM entity
             JOIN geometry ON geometry.entity_id = entity.id
             WHERE wgs84_wkb IS NOT NULL AND kind != 'line' """
    if z < 5:
        # At country scale show operating high-speed rail and signed national motorways.
        sql += """AND status='operating' AND (
            (kind='rail' AND class='high_speed') OR
            (kind='road' AND class='motorway' AND EXISTS (
                SELECT 1 FROM source_object s WHERE s.ref=entity.source_ref
                AND json_extract(s.tags,'$.ref') GLOB 'G[0-9]*'
            ))
        ) """
    rows = db.execute(sql + "ORDER BY render_id") if z >= 3 else ()
    for kind, category, status, render_id, blob, name in rows:
        layer, minzoom = LAYERS[kind]
        if z < minzoom or (z < 11 and category in {"yard", "siding", "spur", "crossover", "motorway_link"}):
            continue
        if z < 8 and kind == "rail" and category not in {"high_speed", "conventional"}:
            continue
        geom = from_wkb(blob)
        if layer == "points" and geom.geom_type != "Point":
            geom = geom.representative_point()
        geom = transform(geom, project, interleaved=False).simplify(step / 1024, preserve_topology=True)
        if geom.is_empty:
            continue
        x0, y0, x1, y1 = geom.bounds
        properties = {"rid": render_id, "class": category, "status": status}
        if kind == "station" and name != "车站" and z >= 10:
            properties["label"] = name[:80]
        for x in range(max(0, int((x0 - padding) * count)), min(count - 1, int((x1 + padding) * count)) + 1):
            for y in range(max(0, int((y0 - padding) * count)), min(count - 1, int((y1 + padding) * count)) + 1):
                tile = tile_bounds(z, x, y)
                clip = box(tile[0] - padding, tile[1] - padding, tile[2] + padding, tile[3] + padding)
                for part in components(geom.intersection(clip)):
                    buckets[x, y][layer].append({"geometry": part, "properties": properties})
    if land and z <= 7:
        for geom in land:
            geom = geom.simplify(step / 512, preserve_topology=True)
            x0, y0, x1, y1 = geom.bounds
            for x in range(max(0, int(x0 * count)), min(count - 1, int(x1 * count)) + 1):
                for y in range(max(0, int(y0 * count)), min(count - 1, int(y1 * count)) + 1):
                    clip = box(*tile_bounds(z, x, y))
                    for part in components(geom.intersection(clip)):
                        if part.geom_type in {"Polygon", "MultiPolygon"}:
                            buckets[x, y]["land"].append({"geometry": part, "properties": {}})
    for (x, y), layers in sorted(buckets.items(), key=lambda item: zxy_to_tileid(z, *item[0])):
        tile = tile_bounds(z, x, y)
        data = mapbox_vector_tile.encode(
            [{"name": layer, "features": features} for layer, features in sorted(layers.items())],
            default_options={"quantize_bounds": tile, "y_coord_down": True}
        )
        if data:
            yield zxy_to_tileid(z, x, y), data


def build_land(land_path):
    land = []
    if land_path:
        # A cartographic backdrop only. No political boundaries are derived or drawn.
        clip = box(68, 12, 141, 57)
        features = json.loads(land_path.read_text(encoding="utf-8"))["features"]
        for feature in features:
            geom = shape(feature["geometry"])
            if geom.intersects(clip):
                geom = (geom if geom.is_valid else make_valid(geom)).intersection(clip)
                land.extend(transform(part, project, interleaved=False) for part in components(geom)
                            if part.geom_type in {"Polygon", "MultiPolygon"})
    return land


def build_tiles(db, output, bounds, maxzoom, land_path=None):
    land = build_land(land_path)

    def groups():
        for z in range(0 if land else 3, maxzoom + 1):
            print(f" MVT z{z}", flush=True)
            yield from zoom_tiles(db, z, land)
    write_archive(output, groups(), bounds, maxzoom)
