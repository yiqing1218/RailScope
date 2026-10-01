"""Synthetic identity reimport benchmark; does not touch real project datasets."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from railscope.domain import DatasetSnapshot
from railscope.identity import IdentityRegistry


def measure(registry_type, directory, count):
    registry = registry_type(directory / 'identities.sqlite')
    def source(shift):
        for i in range(count):
            node = 2 * (i + shift)
            yield {'id': f'w{i + shift}:0', 'osm_way_id': str(i + shift),
                   'from_node': node, 'to_node': node + 1, 'node_ids': [node, node + 1],
                   'coordinates': [[i * .0001, 30], [i * .0001 + .00001, 30]],
                   'way_tags': {'railway': 'rail'}}
    def snapshot(number):
        return DatasetSnapshot(f'S{number}', 'test', 'synthetic', '2026-09-29', '2026-09-29', str(number))
    registry.commit(registry.prepare(source(0), snapshot(1)))
    start = perf_counter()
    prepared = registry.prepare(source(count), snapshot(2))
    elapsed = perf_counter() - start
    assert len(prepared.edges) == count and len(prepared.conflicts) == count
    return round(elapsed, 6)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before-ref', default='147cb55')
    parser.add_argument('--count', type=int, default=2000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='.audit-benchmark-', dir=ROOT) as directory:
        folder = Path(directory)
        baseline = folder / 'before.py'
        baseline.write_bytes(subprocess.check_output(
            ['git', 'show', f'{args.before_ref}:backend/railscope/identity.py'], cwd=ROOT))
        spec = importlib.util.spec_from_file_location('railscope._audit_before', baseline)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        result = {'fixture': 'all source IDs replaced; synthetic straight edges; no real PBF',
                  'count': args.count, 'before_ref': args.before_ref,
                  'before_seconds': measure(module.IdentityRegistry, folder / 'before', args.count),
                  'after_seconds': measure(IdentityRegistry, folder / 'after', args.count)}
        result['speedup'] = round(result['before_seconds'] / result['after_seconds'], 2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
