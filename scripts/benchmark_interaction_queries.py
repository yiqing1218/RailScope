"""Read-only national query benchmark; does not open or modify user editors."""
import argparse
import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'desktop'), str(ROOT / 'backend')]


def benchmark(project, repetitions=5):
    from data_install import active_rail_directory
    from catalog_workspace import read_overrides
    from rail_line_store import DiskRailLineLibrary
    from rail_line_workspace import LineWorkspace
    source = active_rail_directory(project) / 'rail_lines.sqlite'
    files = [source, active_rail_directory(project) / 'rail.sqlite']
    stamps = [(p.stat().st_size, p.stat().st_mtime_ns) for p in files]
    overrides = read_overrides(project / 'data/user_settings/rail_catalog.json')
    shared = read_overrides(project / 'data/catalog/rail_catalog_overrides.json')
    metadata = {k: {**shared.get(k, {}), **overrides.get(k, {})}
                for k in shared.keys() | overrides.keys()}
    counters = {'membership_installs': 0, 'membership_install_ms': 0.0}
    original = LineWorkspace.install

    def install(self, db):
        start = perf_counter()
        try:
            return original(self, db)
        finally:
            counters['membership_installs'] += 1
            counters['membership_install_ms'] += (perf_counter() - start) * 1000

    LineWorkspace.install = install
    try:
        start = perf_counter()
        library = DiskRailLineLibrary(source, metadata=metadata)
        initialization = (perf_counter() - start) * 1000
        with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            stations = [r[0] for r in db.execute(
                'SELECT source_id FROM station_directory ORDER BY source_id LIMIT 10')]
            key = db.execute('SELECT id FROM lines ORDER BY id LIMIT 1').fetchone()[0]
        timings = {}
        result_sizes = {}
        result_hashes = {}
        for name, action in (
            ('indexed_line_lookup', lambda: library.lines[key]),
            ('station_connections', lambda: [library.connected_lines('station:' + k) for k in stations]),
            ('line_search', lambda: library.search_lines('京沪', limit=20)),
        ):
            print('Measuring ' + name, flush=True)
            timings[name] = []
            for _ in range(repetitions):
                start = perf_counter()
                result = action()
                timings[name].append(round((perf_counter() - start) * 1000, 3))
                result_sizes[name] = len(result)
                result_hashes[name] = hashlib.sha256(json.dumps(result, ensure_ascii=False,
                    sort_keys=True, default=str).encode('utf-8')).hexdigest()
        assert stamps == [(p.stat().st_size, p.stat().st_mtime_ns) for p in files]
        return {'read_only': True, 'source_unchanged': True,
                'station_samples': len(stations), 'effective_overrides': len(metadata),
                'workspace_members': len(library.workspace.targets),
                'initialization_ms': round(initialization, 3), 'timings_ms': timings,
                'result_sizes': result_sizes, 'result_hashes': result_hashes, 'counters': counters}
    finally:
        LineWorkspace.install = original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=5)
    args = parser.parse_args()
    report = benchmark(args.project, args.repetitions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
