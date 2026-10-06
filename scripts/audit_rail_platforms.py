"""Audit every national station; optionally refresh facility-mode evidence."""
import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'backend')]
from desktop.data_install import active_rail_directory, active_directory
from desktop.rail_platform_audit import audit_platforms


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'docs/audit/rail-platforms-2026-10-06')
    parser.add_argument('--refresh-modes', type=Path, help='Local OSM/PBF source; save derived facility mode evidence')
    parser.add_argument('--location-index', type=Path, help='Reuse an existing complete OSM node-location index')
    args = parser.parse_args()
    directory = active_rail_directory(ROOT)
    if args.refresh_modes:
        from desktop.rail_transport_context import build_transport_context
        build_transport_context(directory, args.refresh_modes, args.location_index)
    from desktop.rail_platform_associations import rebuild_associations
    edits = rebuild_associations(directory)
    print(f'已检查全体站台与站区关联，更新 {len(edits)} 项独立关联记录；原始几何保留。')
    rows, summary = audit_platforms(directory, active_directory(ROOT)/'metro.sqlite')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.with_suffix('.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=list(rows[0]) if rows else ['station_source_id'])
        writer.writeheader(); writer.writerows(rows)
    args.output.with_suffix('.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k != 'unassociated_source_ids'},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
