"""Compact V2 semantic projection beside the legacy topology tables.

Source edges are streamed once. Geometry stays in the RTree source database;
all lookups below are bounded by explicit IDs or SQL paging.
"""
import json
try:
    from .rail_semantics import semantic_record, aggregate_semantics
except ImportError:
    from rail_semantics import semantic_record, aggregate_semantics


def create_schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS edge_semantics(
          edge_id TEXT PRIMARY KEY, railway_class TEXT NOT NULL,
          line_role TEXT NOT NULL, track_role TEXT NOT NULL,
          facility_id TEXT, yard_id TEXT, zone_id TEXT,
          construction_status TEXT NOT NULL, verification_status TEXT,
          confidence REAL, provenance TEXT NOT NULL, facility_only INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS semantic_class ON edge_semantics(railway_class,track_role,edge_id);
        CREATE INDEX IF NOT EXISTS semantic_facility ON edge_semantics(facility_id,yard_id,zone_id,edge_id);
        CREATE TABLE IF NOT EXISTS line_semantics(line_id TEXT PRIMARY KEY,data TEXT NOT NULL,facility_only INTEGER NOT NULL);
    ''')


def insert_edge(db, edge, replace=True):
    facts = semantic_record(edge)
    service = edge.get('way_tags', {}).get('service')
    facility_only = int(facts.get('facility_only') or service in ('yard', 'siding') or
                        facts['track_role'] not in ('main_track', 'unknown'))
    db.execute(('INSERT OR REPLACE' if replace else 'INSERT OR IGNORE') +
               ' INTO edge_semantics VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
        edge['id'], facts['railway_class'], facts['line_role'], facts['track_role'],
        facts['facility_id'], facts['yard_id'], facts['zone_id'],
        facts['construction_status'], facts['verification_status'], facts['confidence'],
        json.dumps(facts['provenance'], ensure_ascii=False), facility_only))


def finalize(db):
    # SQL aggregation avoids loading a national edge/entity list into Python.
    columns = ('railway_class', 'line_role', 'track_role', 'facility_id', 'yard_id', 'zone_id',
               'construction_status')
    expressions = ','.join(
        f"CASE WHEN count(DISTINCT coalesce(s.{key},''))=1 THEN min(s.{key}) ELSE " +
        ("NULL" if key.endswith('_id') else "'unknown'") + ' END' for key in columns)
    rows = db.execute('SELECT e.line_id,' + expressions + ',min(s.facility_only) '
                      'FROM edges e JOIN edge_semantics s ON s.edge_id=e.id GROUP BY e.line_id')
    for row in rows:
        facts = dict(zip(columns, row[1:-1]))
        facts.update(verification_status='derived', confidence=None, provenance={
            key: {'value': facts[key], 'source': 'shared_edge_membership', 'verification_status': 'derived',
                  'evidence': '逐属性汇总，混合值为 unknown；明细见 edge_semantics.provenance',
                  'snapshot_id': None, 'confidence': None} for key in columns})
        db.execute('INSERT INTO line_semantics VALUES(?,?,?)',
                   (row[0], json.dumps(facts, ensure_ascii=False), row[-1]))


def edge_records(db, ids):
    result = {}
    ids = list(dict.fromkeys(ids))
    for start in range(0, len(ids), 800):
        batch = ids[start:start+800]
        rows = db.execute('SELECT * FROM edge_semantics WHERE edge_id IN (' + ','.join('?' for _ in batch) + ')', batch)
        for row in rows:
            result[row[0]] = dict(zip(('railway_class','line_role','track_role','facility_id',
                'yard_id','zone_id','construction_status','verification_status','confidence'), row[1:10]))
            result[row[0]].update(provenance=json.loads(row[10]), facility_only=bool(row[11]))
    return result


def line_record(db, members):
    rows = []
    for start in range(0, len(members), 800):
        batch = members[start:start+800]
        rows.extend(db.execute('SELECT data,facility_only FROM line_semantics WHERE line_id IN (' +
                    ','.join('?' for _ in batch) + ')', batch).fetchall())
    if not rows:
        return {}
    facts = json.loads(rows[0][0]) if len(rows) == 1 else aggregate_semantics(json.loads(row[0]) for row in rows)
    facts['facility_only'] = all(row[1] for row in rows)
    return facts
