"""Recover omitted Hefei S1 ways from OSM API; never invent connecting lines.

The original derived layer is backed up before an atomic replacement. Pure
proposed ways are rejected. Raw tags, original node order and OSM IDs retained.
"""

from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from railscope.services.importers.construction import (
    construction_feature,
    is_construction_metro_tags,
)
from railscope.services.importers.metro import _atomic_json_write

NAME = "合肥轨道交通S1线"
API = "https://api.openstreetmap.org/api/0.6"


def component_count(features):
    parents = {}

    def root(p):
        parents.setdefault(p, p)
        while parents[p] != p:
            parents[p] = parents[parents[p]]
            p = parents[p]
        return p

    for feature in features:
        coords = feature["geometry"]["coordinates"]
        a, b = (tuple(round(v, 7) for v in coords[index]) for index in (0, -1))
        parents[root(a)] = root(b)
    return len({root(p) for p in parents})


def recover(target, manifest_path, report_path):
    layer = json.loads(target.read_text(encoding="utf-8"))
    existing = {f["properties"]["osm_way_id"] for f in layer["features"]}
    before = [f for f in layer["features"] if f["properties"]["line_name"] == NAME]
    queue = [9700440915]
    visited = set()
    examined = set()
    added = []
    evidence = []
    rejected = []

    def xml(path):
        request = Request(
            API + "/" + path,
            headers={
                "User-Agent": "RailScope-GIS/0.1 (https://github.com/yiqing1218/RailScope)"
            },
        )
        with urlopen(request, timeout=20) as response:
            return ET.fromstring(response.read())

    while queue:
        if len(visited) >= 60:
            raise RuntimeError("超出限定修复范围，未覆盖数据")
        node = queue.pop(0)
        if node in visited:
            continue
        visited.add(node)
        for way in xml(f"node/{node}/ways").findall("way"):
            wid = int(way.attrib["id"])
            if wid in existing or wid in examined:
                continue
            tags = {t.attrib["k"]: t.attrib["v"] for t in way.findall("tag")}
            if tags.get("name") != NAME:
                continue
            examined.add(wid)
            if not is_construction_metro_tags(tags):
                rejected.append({"osm_way_id": wid, "tags": tags})
                continue
            full = xml(f"way/{wid}/full")
            track = full.find("way")
            tags = {t.attrib["k"]: t.attrib["v"] for t in track.findall("tag")}
            if not is_construction_metro_tags(tags):
                raise RuntimeError("源状态在下载中改变，未覆盖数据")
            refs = [int(n.attrib["ref"]) for n in track.findall("nd")]
            nodes = {
                int(n.attrib["id"]): [float(n.attrib["lon"]), float(n.attrib["lat"])]
                for n in full.findall("node")
            }
            coords = [nodes[ref] for ref in refs]
            if len(coords) < 2:
                raise RuntimeError("轨道节点不足，未覆盖数据")
            feature = construction_feature(wid, tags, coords)
            feature["properties"].update(
                {
                    "osm_version": int(track.attrib["version"]),
                    "osm_updated_at": track.attrib["timestamp"],
                    "geometry_source": f"{API}/way/{wid}/full",
                }
            )
            added.append(feature)
            queue.extend((refs[0], refs[-1]))
            evidence.append(
                {
                    "osm_way_id": wid,
                    "nodes": refs,
                    "tags": tags,
                    "version": track.attrib["version"],
                    "source": f"{API}/way/{wid}/full",
                }
            )
            print(f"Recovered OSM way {wid}: {len(coords)} original nodes", flush=True)
    after = before + added
    report = {
        "source": API,
        "line_name": NAME,
        "checked_at": datetime.now(UTC).isoformat(),
        "ways_added": evidence,
        "rejected": rejected,
        "components_before": component_count(before),
        "components_after": component_count(after),
    }
    # An idempotent check must not overwrite the original repair evidence.
    output_report = (
        report_path if added else report_path.with_stem(report_path.stem + "-check")
    )
    _atomic_json_write(output_report, report)
    if rejected or report["components_after"] != 1:
        raise RuntimeError("仍存在缺口或状态冲突，未覆盖数据；请查诊断报告")
    if added:
        backup = (
            report_path.parent
            / "hefei-s1-backups"
            / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        )
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup / target.name)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        shutil.copy2(manifest_path, backup / manifest_path.name)
        layer["features"].extend(added)
        _atomic_json_write(target, layer)
        manifest["construction_way_features_written"] = len(layer["features"])
        manifest.setdefault("regional_repairs", []).append(
            {
                "line_name": NAME,
                "checked_at": report["checked_at"],
                "source": API,
                "added_way_ids": [f["properties"]["osm_way_id"] for f in added],
                "backup": str(backup),
            }
        )
        _atomic_json_write(manifest_path, manifest)
    print(
        f"Added {len(added)} ways; disconnected components {report['components_before']} -> {report['components_after']}"
    )


if __name__ == "__main__":
    folder = ROOT / "data/processed/osm"
    recover(
        folder / "china_metro_construction.geojson",
        folder / "china_metro_construction_manifest.json",
        ROOT / "data/logs/hefei-s1-repair.json",
    )
