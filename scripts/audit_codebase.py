"""Reproducible, read-only source inventory. Never scan local datasets or secrets."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess


TEXT_SUFFIXES = {'.py', '.js', '.cjs', '.ts', '.tsx', '.rs', '.ps1', '.cmd',
                 '.md', '.toml', '.ini', '.html', '.css', '.yml', '.yaml',
                 '.json', '.mako', '.csv', '.svg', '.txt'}
TEXT_NAMES = {'.gitignore', 'Makefile', '.env.example'}


def audit(root: Path) -> dict:
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode('utf-8').split('\0')
    files, counts = [], Counter()
    for name in sorted(tracked):
        path = root / name
        if (path.suffix not in TEXT_SUFFIXES and path.name not in TEXT_NAMES or
                name.startswith(('data/', 'output/')) or
                name.startswith('docs/audit/') and path.suffix == '.json'):
            continue
        raw = path.read_bytes()
        source = raw.decode('utf-8-sig')
        entry = {'path': name, 'lines': len(source.splitlines()),
                 'sha256': hashlib.sha256(raw).hexdigest()}
        if path.suffix == '.json':
            json.loads(source)
        entry['markers'] = [{'line': i, 'kind': match[0]} for i, line in enumerate(source.splitlines(), 1)
                            if (match := re.findall(r'\b(?:TODO|FIXME|NotImplemented)\b', line))]
        if path.suffix == '.py':
            tree = ast.parse(source, filename=name)
            entry['imports'] = sorted({alias.name for node in ast.walk(tree)
                if isinstance(node, ast.Import) for alias in node.names} |
                {'.' * node.level + (node.module or '') for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom)})
            entry['functions'] = [{'name': node.name, 'line': node.lineno,
                                   'lines': node.end_lineno - node.lineno + 1}
                for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.end_lineno - node.lineno >= 79]
            # Candidates require call-chain review; they are not proven bugs.
            entry['silent_handlers'] = [node.lineno for node in ast.walk(tree)
                if isinstance(node, ast.ExceptHandler) and all(isinstance(s, ast.Pass) for s in node.body)]
            entry['broad_handlers'] = [node.lineno for node in ast.walk(tree)
                if isinstance(node, ast.ExceptHandler) and (node.type is None or
                    isinstance(node.type, ast.Name) and node.type.id in ('Exception', 'BaseException'))]
            entry['suppressed_handlers'] = [node.lineno for node in ast.walk(tree)
                if isinstance(node, ast.ExceptHandler) and all(isinstance(s, (ast.Pass, ast.Continue)) for s in node.body)]
            counts['python_files'] += 1
            counts['silent_handler_candidates'] += len(entry['silent_handlers'])
            counts['broad_handler_candidates'] += len(entry['broad_handlers'])
        counts['files'] += 1
        counts['lines'] += entry['lines']
        files.append(entry)
    return {'summary': dict(counts), 'files': files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = audit(root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()
