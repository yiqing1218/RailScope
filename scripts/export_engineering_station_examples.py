"""Reproduce engineering SVG/PNG/PDF from a read-only local railway snapshot."""

import argparse
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend"), str(ROOT / "desktop")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from desktop.catalog_workspace import read_overrides  # noqa: E402
from desktop.station_tracks import load_station_tracks, schematic_station_info  # noqa: E402
from desktop.station_diagram_layout import DiagramOptions, build_layout  # noqa: E402
from desktop.station_diagram_render import render_svg, write_diagram  # noqa: E402
from desktop.rail_style_ui import load_styles  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--identity", type=Path)
    parser.add_argument("--styles", type=Path)
    parser.add_argument("--stations", nargs="+", default=["义乌", "南京南", "上海虹桥"])
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/logs/station_engineering_actual"
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    overrides = read_overrides(ROOT / "data/catalog/rail_catalog_overrides.json")
    if args.overrides:
        overrides.update(read_overrides(args.overrides))
    stamps = {
        p.name: (p.stat().st_size, p.stat().st_mtime_ns)
        for p in args.dataset.glob("*.sqlite")
    }
    options = DiagramOptions()
    manifest = {
        "algorithm": "engineering_topology_lanes_v1",
        "dataset": args.dataset.name,
        "verification_status": "automatic_reference_not_dispatch_verified",
        "confidence": None,
        "examples": [],
    }
    with tempfile.TemporaryDirectory(prefix="railscope-engineering-") as temp:
        identity = Path(temp) / "identity.sqlite"
        if args.identity and args.identity.exists():
            with (
                closing(
                    sqlite3.connect(
                        args.identity.resolve().as_uri() + "?mode=ro", uri=True
                    )
                ) as src,
                closing(sqlite3.connect(identity)) as dst,
            ):
                src.backup(dst)
        for name in args.stations:
            with closing(
                sqlite3.connect(
                    (args.dataset / "rail_lines.sqlite").resolve().as_uri()
                    + "?mode=ro",
                    uri=True,
                )
            ) as db:
                rows = db.execute(
                    "SELECT source_id FROM station_directory WHERE name IN (?,?)",
                    (name, name + "站"),
                ).fetchall()
            if len(rows) != 1:
                raise ValueError("未找到唯一车站实体：" + name)
            print("读取：" + name, flush=True)
            repo, track_rows, context = load_station_tracks(
                args.dataset,
                identity,
                {"name": name, "station_source_id": rows[0][0]},
                overrides,
                approach_depth=options.topology_depth,
            )
            source_digest = lambda: hashlib.sha256(
                json.dumps(
                    [asdict(repo), context], sort_keys=True, ensure_ascii=False
                ).encode()
            ).hexdigest()
            before = source_digest()
            info = schematic_station_info(args.dataset, repo, track_rows, overrides)
            if args.styles:
                info["rail_styles"] = load_styles(args.styles)
            svg = render_svg(repo, context, options, info)
            layout = build_layout(repo, context, options)
            assert before == source_digest()
            assert all(
                math.dist(d.points[0], layout.nodes[repo.edges[k].from_node_id]) < 1e-7
                and math.dist(d.points[-1], layout.nodes[repo.edges[k].to_node_id])
                < 1e-7
                for k, d in layout.edges.items()
            )
            for suffix in ("svg", "png", "pdf"):
                write_diagram(args.output / (name + "." + suffix), svg, options)
            # Local reproducibility cache stays ignored, outside source GIS data.
            (args.output / (name + ".source.json")).write_text(
                json.dumps(
                    {"repo": asdict(repo), "context": context, "info": info},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            item = {
                "station": name,
                "station_id": next(iter(repo.stations)),
                "edges": len(layout.edges),
                "lanes": len(layout.lanes),
                "ports": len(layout.ports),
                "warnings": layout.warnings,
                "source_digest": before,
                "files": [name + "." + s for s in ("svg", "png", "pdf")],
            }
            manifest["examples"].append(item)
            print(
                name
                + ": "
                + str(item["edges"])
                + " edges, "
                + str(item["lanes"])
                + " lanes, "
                + str(item["ports"])
                + " ports",
                flush=True,
            )
    assert stamps == {
        p.name: (p.stat().st_size, p.stat().st_mtime_ns)
        for p in args.dataset.glob("*.sqlite")
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return app


if __name__ == "__main__":
    main()
