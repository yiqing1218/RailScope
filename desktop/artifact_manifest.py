"""Dependency records for rebuildable adapters, separate from user overrides."""
from datetime import datetime, timezone
import hashlib
import json


def manifest(artifact, algorithm, inputs, revisions=None, schema=1):
    encoded = json.dumps(inputs, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return {'artifact': artifact, 'schema_version': schema, 'algorithm_version': algorithm,
            'inputs': inputs, 'input_fingerprint': hashlib.sha256(encoded.encode()).hexdigest(),
            'created_at': datetime.now(timezone.utc).isoformat(),
            'dependency_revisions': revisions or {}, 'output_version': 1, 'complete': True}


def install_manifest(db, record, key='artifact_manifest'):
    db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',
               (key, json.dumps(record, ensure_ascii=False, separators=(',', ':'))))


def read_manifest(db):
    row = db.execute("SELECT value FROM metadata WHERE key='artifact_manifest'").fetchone()
    return json.loads(row[0]) if row else None
