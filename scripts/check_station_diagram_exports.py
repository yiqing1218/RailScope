"""Check repeated real-data station exports, including rendered PNG and PDF."""

# ruff: noqa: E402 -- Qt environment must be configured before importing Qt.

import argparse
import json
import math
import os
from pathlib import Path
from xml.etree import ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QImage
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QApplication
from shapely.geometry import LineString


def check_svg(path):
    root = ET.parse(path).getroot()
    meta = json.loads(root.find("{http://www.w3.org/2000/svg}metadata").text)
    annotations = meta["annotations"]
    assert len({(a["kind"], a["id"]) for a in annotations}) == len(annotations), path
    width, height = float(root.attrib["width"]), float(root.attrib["height"])
    for i, a in enumerate(annotations):
        x0, y0, x1, y1 = a["bounds"]
        assert 0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height, (path, a)
        for b in annotations[i + 1 :]:
            u0, v0, u1, v1 = b["bounds"]
            assert not (x0 < u1 and x1 > u0 and y0 < v1 and y1 > v0), (path, a, b)
    assert not any(
        "data-switch-node" in e.attrib or "data-crossing" in e.attrib
        for e in root.iter()
    ), path
    assert not any(
        a["kind"] == "track" and a["text"].startswith("T") for a in annotations
    ), path
    pairs = 0
    for port in meta["ports"]:
        extensions = [
            e
            for e in meta["schematic_extensions"]
            if e["edge_id"] in port["edge_ids"]
            and any(math.dist(e["points"][-1], p) < 0.01 for p in port["points"])
        ]
        for i, a in enumerate(extensions):
            for b in extensions[i + 1 :]:
                assert not LineString(a["points"]).crosses(LineString(b["points"])), (
                    path,
                    port["key"],
                )
                assert math.dist(a["points"][-1], b["points"][-1]) > 1, (
                    path,
                    port["key"],
                )
                pairs += 1
    return meta, pairs


def check(directory):
    app = QApplication.instance() or QApplication([])
    manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
    results = []
    for index, example in enumerate(manifest["examples"], 1):
        name = example["station_name"]
        stem = f"{index:02d}-{name}-after"
        assert all(example["checks"].values()), name
        assert example["checks"]["variant_shared_junctions"], name
        meta, pairs = check_svg(directory / (stem + ".svg"))
        annotations = meta["annotations"]
        variants = {}
        for variant in example["variants"]:
            variant_meta, variant_pairs = check_svg(
                directory / f"{index:02d}-{name}-{variant}.svg"
            )
            variants[variant] = {
                "annotation_count": len(variant_meta["annotations"]),
                "checked_exit_pairs": variant_pairs,
                "checked": True,
            }
        image = QImage(str(directory / (stem + ".png")))
        assert not image.isNull() and (image.width(), image.height()) == (2400, 1297), (
            name
        )
        document = QPdfDocument()
        assert (
            document.load(str(directory / (stem + ".pdf"))) == QPdfDocument.Error.None_
        ), name
        assert document.pageCount() == 1, name
        size = document.pagePointSize(0)
        assert math.isclose(size.width() / size.height(), 2400 / 1297, rel_tol=0.002), (
            name
        )
        document.close()
        results.append(
            {
                "station": name,
                "loaded_edges": example["loaded_edges"],
                "drawn_edges": example["drawn_edges"],
                "source_platform_bodies": example["platforms"],
                "annotation_count": len(annotations),
                "checked_exit_pairs": pairs,
                "variant_checks": variants,
                "warnings": meta["warnings"],
                "checks": {
                    "real_shared_nodes": True,
                    "source_unchanged": True,
                    "label_boxes_clear": True,
                    "exit_extensions_do_not_cross": True,
                    "source_numbering_only": True,
                    "svg_png_pdf": True,
                    "pdf_single_page_correct_ratio": True,
                },
            }
        )
        print(
            name,
            "checked",
            len(annotations),
            "labels,",
            pairs,
            "exit pairs",
            flush=True,
        )
    assert len(results) >= 10, "至少需要十站"
    output = {
        "algorithm": "yard_centered_parallel_outlets_v4",
        "stations": results,
        "limits": "源对象连接检查不等于已核验联锁进路；图示台体计数不等于官方站场规模。",
    }
    (directory / "checks.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), "utf-8"
    )
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    check(parser.parse_args().directory)
