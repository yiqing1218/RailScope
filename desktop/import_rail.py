"""Physical national rail tracks, platforms and junctions; never invented topology."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from railscope.services.importers.native_paths import native_path
from railscope.domain import DatasetSnapshot
from railscope.identity import IdentityRegistry, new_id


def extract(pbf, output, identity_path=None):
    import osmium

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    tracks, points, platforms, relations = [], [], [], []

    class Handler(osmium.SimpleHandler):
        def node(self, node):
            tags = dict(node.tags)
            kind = tags.get("railway")
            if kind not in (
                "station",
                "halt",
                "stop",
                "switch",
                "railway_crossing",
                "crossing",
                "level_crossing",
                "junction",
                "signal_box",
                "buffer_stop",
                "signal",
            ):
                return
            if (
                tags.get("station") in ("subway", "light_rail")
                or tags.get("subway") == "yes"
            ):
                return
            if node.location.valid():
                points.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "osm_node_id": node.id,
                            "kind": kind,
                            "name": tags.get("name", ""),
                            "node_tags": tags,
                            "source": "OpenStreetMap",
                        },
                        "geometry": {
                            "type": "Point",
                            "coordinates": [node.location.lon, node.location.lat],
                        },
                    }
                )

        def way(self, way):
            tags = dict(way.tags)
            kind = tags.get("railway")
            is_platform = (
                kind == "platform" or tags.get("public_transport") == "platform"
            )
            if (
                kind != "rail"
                and not (kind == "construction" and tags.get("construction") == "rail")
                and not is_platform
            ):
                return
            if is_platform and (
                tags.get("subway") == "yes" or tags.get("train") == "no"
            ):
                return
            try:
                coords = [[n.lon, n.lat] for n in way.nodes]
            except (osmium.InvalidLocationError, RuntimeError):
                return
            if len(coords) < 2:
                return
            props = {
                "osm_way_id": way.id,
                "name": tags.get("name", tags.get("ref", str(way.id))),
                "way_tags": tags,
                "source": "OpenStreetMap",
                "license": "ODbL 1.0",
                "attribution": "© OpenStreetMap contributors",
            }
            if is_platform:
                closed = len(coords) >= 4 and way.nodes[0].ref == way.nodes[-1].ref
                props["mode_verified"] = tags.get("train") == "yes"
                platforms.append(
                    {
                        "type": "Feature",
                        "properties": props,
                        "geometry": {
                            "type": "Polygon" if closed else "LineString",
                            "coordinates": [coords] if closed else coords,
                        },
                    }
                )
            else:
                props.update(
                    node_ids=[n.ref for n in way.nodes],
                    construction=kind == "construction",
                    service=tags.get("service", "main"),
                )
                tracks.append(
                    {
                        "type": "Feature",
                        "properties": props,
                        "geometry": {"type": "LineString", "coordinates": coords},
                    }
                )

        def relation(self, relation):
            tags = dict(relation.tags)
            if (
                tags.get("route") not in ("railway", "train")
                or tags.get("type") != "route"
            ):
                return
            relations.append(
                {
                    "osm_relation_id": relation.id,
                    "name": tags.get("name", str(relation.id)),
                    "relation_tags": tags,
                    "members": [
                        {"type": m.type, "ref": m.ref, "role": m.role}
                        for m in relation.members
                    ],
                }
            )

    handler = Handler()
    idx = native_path(output / ".rail-locations.idx", output=True)
    handler.apply_file(
        str(native_path(pbf)),
        locations=True,
        idx=f"sparse_file_array,{idx}",
        filters=[osmium.filter.KeyFilter("railway", "public_transport", "route")],
    )
    degree = Counter()
    used = set()
    node_coordinates = {}
    node_lines = {}
    way_relations = {}
    for relation in relations:
        for member in relation["members"]:
            if member["type"] == "w":
                way_relations.setdefault(member["ref"], []).append(
                    relation["osm_relation_id"]
                )
    for track in tracks:
        ids = track["properties"]["node_ids"]
        tags = track["properties"]["way_tags"]
        line_key = (
            tags.get("name") or tags.get("full_name") or tags.get("ref") or "",
            tags.get("ref", ""),
            tags.get("service", "main"),
        )
        used.update(ids)
        for node_id, coordinate in zip(ids, track["geometry"]["coordinates"]):
            node_coordinates.setdefault(node_id, coordinate)
            node_lines.setdefault(node_id, set()).add(line_key)
        for a, b in zip(ids, ids[1:]):
            degree[a] += 1
            degree[b] += 1
        track["properties"]["rail_relation_ids"] = way_relations.get(
            track["properties"]["osm_way_id"], []
        )
    explicit = {p["properties"]["osm_node_id"] for p in points}
    # Every physical branch, line-membership change and terminal becomes a
    # searchable control point, even when OSM has no named point feature there.
    for node_id in sorted(used - explicit):
        kind = None
        if degree[node_id] > 2:
            kind = "topology_junction"
        elif len(node_lines.get(node_id, ())) > 1:
            kind = "line_change"
        elif degree[node_id] == 1:
            kind = "line_terminal"
        if kind:
            points.append(
                {
                    "type": "Feature",
                    "properties": {
                        "osm_node_id": node_id,
                        "kind": kind,
                        "name": "",
                        "node_tags": {},
                        "source": "RailScope topology inference from OpenStreetMap",
                        "verification_status": "OSM-derived",
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": node_coordinates[node_id],
                    },
                }
            )
    # Discard subway-only switches, but retain named rail stations off the track.
    points = [
        p
        for p in points
        if p["properties"]["kind"]
        in (
            "station",
            "halt",
            "stop",
            "junction",
            "signal_box",
            "topology_junction",
            "line_change",
            "line_terminal",
        )
        or p["properties"]["osm_node_id"] in used
    ]
    # Signals/ordinary shape vertices do not become artificial route segments.
    split = {p["properties"]["osm_node_id"] for p in points
             if p["properties"]["kind"] in {"station", "halt", "stop", "switch", "junction", "signal_box", "topology_junction", "line_change", "line_terminal", "railway_crossing", "buffer_stop"}} | {
        n for n, d in degree.items() if d != 2
    }
    edges = []
    for track in tracks:
        ids = track["properties"]["node_ids"]
        coords = track["geometry"]["coordinates"]
        start = 0
        for i in range(1, len(ids)):
            if i == len(ids) - 1 or ids[i] in split:
                edges.append(
                    {
                        "id": f"w{track['properties']['osm_way_id']}:{start}-{i}",
                        "from_node": ids[start],
                        "to_node": ids[i],
                        "node_ids": ids[start : i + 1],
                        "coordinates": coords[start : i + 1],
                        "construction": track["properties"]["construction"],
                        "direction": (
                            "forward"
                            if track["properties"]["way_tags"].get("oneway") in ("yes", "1", "true")
                            else "reverse"
                            if track["properties"]["way_tags"].get("oneway") == "-1"
                            else "both"
                        ),
                        "way_tags": track["properties"]["way_tags"],
                        "rail_relation_ids": track["properties"]["rail_relation_ids"],
                    }
                )
                start = i
    if not tracks:
        raise ValueError("未找到国铁轨道，旧数据未替换")
    registry = IdentityRegistry(identity_path or output / "identity.sqlite")
    now = datetime.now(timezone.utc).isoformat()
    snapshot = DatasetSnapshot(new_id("SNAP"), "national-rail", "osm", now,
                               now[:10], str(Path(pbf).stat().st_mtime_ns))
    prepared = registry.prepare(edges, snapshot)
    edges = prepared.edges
    try:
        from .rail_categories import track_type
    except ImportError:
        from rail_categories import track_type
    for edge in edges:
        edge["track_type"], edge["track_type_evidence"] = track_type(
            edge.get("way_tags", {})
        )
    for point in points:
        alias = "osm/node/" + str(point["properties"]["osm_node_id"])
        if alias in prepared.nodes:
            point["properties"]["infrastructure_node_id"] = prepared.nodes[alias]["id"]
    by_way = {}
    for edge in edges:
        by_way.setdefault(str(edge["osm_way_id"]), []).append(edge["id"])
    for track in tracks:
        props = track["properties"]
        way = str(props["osm_way_id"])
        props["source_object_id"] = "osm/way/" + way
        props["network_edge_ids"] = by_way.get(way, [])
        props["construction_status"] = (
            "construction" if props.get("construction") else "operating"
        )
    for name, features in [
        ("rail_tracks.geojson", tracks),
        ("rail_points.geojson", points),
        ("rail_platforms.geojson", platforms),
    ]:
        (output / name).write_text(
            json.dumps(
                {"type": "FeatureCollection", "features": features}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
    (output / "rail_graph.json").write_text(
        json.dumps(
            {
                "edges": edges,
                "points": points,
                "relations": relations,
                "snapshot_id": snapshot.id,
                "notice": "OSM 物理连接，不是联锁进路。站台归属和几台几线仅以原始标注为准；未标注不推断。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    try:
        from .rail_store import build_index
    except ImportError:
        from rail_store import build_index
    build_index(output, tracks, points, platforms, edges)
    try:
        from .rail_boundaries import extract as extract_boundaries
    except ImportError:
        from rail_boundaries import extract as extract_boundaries
    boundaries = extract_boundaries(pbf, output)
    report = {
        "tracks": len(tracks),
        "points": len(points),
        "platforms": len(platforms),
        "edges": len(edges),
        "relations": len(relations),
        "source": str(pbf),
        "license": "ODbL 1.0",
        "boundary_polygons": boundaries["polygons"],
        "snapshot_id": snapshot.id,
        "previous_snapshot_id": prepared.previous_snapshot_id,
        "migration_conflicts": len(prepared.conflicts),
    }
    (output / "rail_migration.json").write_text(json.dumps({
        "snapshot_id": snapshot.id, "previous_snapshot_id": prepared.previous_snapshot_id,
        "changes": prepared.changes, "conflicts": prepared.conflicts,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "rail_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    registry.commit(prepared)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pbf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--identity-db", type=Path, default=ROOT / "data/user_settings/workspace.sqlite")
    args = parser.parse_args()
    print(extract(args.pbf, args.output, args.identity_db))
