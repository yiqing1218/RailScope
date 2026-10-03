"""Reparse cached station pages; no network requests or infrastructure changes."""
import argparse
import hashlib
import json
from pathlib import Path

try:
    from .china_emu import SHARED_REFERENCE_PATH, REFERENCE_PATH, atomic_json, parse_profile
    from .reference_integration import structured_yard
except ImportError:
    from china_emu import SHARED_REFERENCE_PATH, REFERENCE_PATH, atomic_json, parse_profile
    from reference_integration import structured_yard


def upgrade(path, cache):
    path, cache = Path(path), Path(cache)
    payload = json.loads(path.read_text(encoding='utf-8'))
    changed = 0
    for index, profile in enumerate(payload['profiles']):
        if profile['kind'] != 'station':
            continue
        old = json.dumps(profile, ensure_ascii=False, sort_keys=True)
        if any(s.get('yards') for s in profile.get('scopes', [])):
            cached = cache / (hashlib.sha256(profile['source_url'].encode()).hexdigest()+'.json')
            if cached.exists():
                raw = json.loads(cached.read_text(encoding='utf-8'))
                candidate = parse_profile(raw['html'], profile['source_url'], profile['retrieved_at'])
                if candidate['snapshot_id'] == profile['snapshot_id']:
                    profile = candidate
        for scope in profile.get('scopes', []):
            scope['yards'] = [structured_yard(yard) for yard in scope.get('yards', [])]
        payload['profiles'][index] = profile
        changed += json.dumps(profile, ensure_ascii=False, sort_keys=True) != old
    if changed:
        atomic_json(path,payload)
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path',type=Path,default=SHARED_REFERENCE_PATH)
    parser.add_argument('--cache',type=Path,default=REFERENCE_PATH.parent/'cache')
    args = parser.parse_args()
    print(f'升级车站资料 {upgrade(args.path,args.cache)} 条：{args.path}')


if __name__ == '__main__':
    main()
