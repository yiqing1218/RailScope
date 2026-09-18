"""Export the nationwide, endpoint-delimited physical rail inventory without copying GIS."""

import argparse
from pathlib import Path
import sqlite3
from contextlib import closing

from .data_install import atomic_json
from .rail_line_store import (
    build_line_index,
    DiskRailLineLibrary,
    fingerprint,
    index_ready,
)


def export_inventory(directory, destination=None, progress=print):
    directory = Path(directory).resolve()
    source = directory / "rail.sqlite"
    index = directory / "rail_lines.sqlite"
    if not index_ready(index, fingerprint(source, [])):
        build_line_index(source, index, [], [], progress)
    library = DiskRailLineLibrary(index)
    destination = (
        Path(destination) if destination else directory / "rail_section_inventory.json"
    )
    library.write_export(destination, progress)
    with closing(sqlite3.connect(index)) as db:
        report = {
            "schema": "railscope.rail-inventory-report.v1",
            "source": "rail.sqlite",
            "physical_edges": db.execute("SELECT count(*) FROM edges").fetchone()[0],
            "indexed_lines": db.execute("SELECT count(*) FROM lines").fetchone()[0],
            "indexed_endpoints": db.execute("SELECT count(*) FROM nodes").fetchone()[0],
            "edge_roles": dict(
                db.execute("SELECT track_type,count(*) FROM edges GROUP BY track_type")
            ),
            "inventory": destination.name,
            "notice": "物理轨道用途依据 OSM 标签；未知保留，不等于已验证的全国官方线路名录。",
        }
    atomic_json(directory / "rail_section_manifest.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    print(export_inventory(arguments.directory, arguments.output))
