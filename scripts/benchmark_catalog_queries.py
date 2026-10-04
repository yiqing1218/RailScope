"""Read-only directory and optional national facility ownership benchmark."""
import argparse
from contextlib import closing
import cProfile
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'desktop'), str(ROOT / 'backend')]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def benchmark(project, repetitions=5, ownership=False, profile=None):
    from PySide6.QtWidgets import QApplication
    from data_install import active_rail_directory
    from rail_catalog_model import RailDirectoryModel
    from catalog_workspace import read_overrides
    directory = active_rail_directory(project)
    files = [directory / name for name in ('rail.sqlite', 'rail_lines.sqlite', 'rail_catalog.sqlite')]
    stamps = [(p.stat().st_size, p.stat().st_mtime_ns) for p in files]
    app = QApplication.instance() or QApplication([])
    model = RailDirectoryModel(files[-1])
    timings, counts = {}, {}
    for query in ('', '京沪', '虹桥'):
        # Measure preparation as well as all subsequent repaint queries.
        model.search = query
        timings[query] = []
        for _ in range(repetitions):
            start = perf_counter()
            counts[query] = model._count_children(model.root.key)
            timings[query].append(round((perf_counter() - start) * 1000, 3))
    report = {'directory_timings_ms': timings, 'directory_root_counts': counts}
    if ownership:
        from catalog_metadata import rail_station_records
        from rail_facility_ownership import facility_track_owners
        local = read_overrides(project / 'data/user_settings/rail_catalog.json')
        shared = read_overrides(project / 'data/catalog/rail_catalog_overrides.json')
        metadata = {k: {**shared.get(k, {}), **local.get(k, {})} for k in shared.keys() | local.keys()}
        stations, _ = rail_station_records(directory, [], limit=100000, overrides=metadata)
        profiler = cProfile.Profile() if profile else None
        start = perf_counter()
        if profiler:
            profiler.enable()
        owners = facility_track_owners(directory, stations, metadata)
        if profiler:
            profiler.disable()
            profiler.dump_stats(str(profile))
        report['ownership'] = {'stations': len(stations), 'owners': len(owners),
            'elapsed_ms': round((perf_counter() - start) * 1000, 3), 'hash': digest(owners)}
    # Sample full semantic records too: provenance must survive the fast path.
    from rail_semantics import semantic_record
    with closing(sqlite3.connect(files[0].resolve().as_uri() + '?mode=ro', uri=True)) as db:
        semantics = [semantic_record(json.loads(raw)['properties']) for raw, in db.execute(
            "SELECT data FROM features WHERE kind='rail' ORDER BY id LIMIT 2000")]
    report['semantic_sample_hash'] = digest(semantics)
    assert app is not None
    assert stamps == [(p.stat().st_size, p.stat().st_mtime_ns) for p in files]
    report['source_unchanged'] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--ownership', action='store_true')
    parser.add_argument('--profile', type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.profile:
        args.profile.parent.mkdir(parents=True, exist_ok=True)
    report = benchmark(args.project, args.repetitions, args.ownership, args.profile)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
