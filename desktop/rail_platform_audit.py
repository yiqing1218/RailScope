"""Per-station source/display audit; this never invents station geometry."""
from collections import Counter, defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def audit_platforms(directory, metro_database=None):
    from .rail_platforms import platform_display
    from .transport_modes import metro_source_ids, other_transport, urban_facility_ids
    from .china_emu import station_reference
    directory = Path(directory)
    metro_ids = metro_source_ids(metro_database)
    urban_ids = urban_facility_ids(directory)
    snapshot = str((directory/'rail.sqlite').stat().st_mtime_ns)
    with closing(sqlite3.connect((directory/'rail_lines.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        stations = [(key, name, json.loads(raw)) for key,name,raw in
                    db.execute('SELECT source_id,name,data FROM station_directory ORDER BY name,source_id')]
    with closing(sqlite3.connect((directory/'rail.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        platforms = [json.loads(raw) for (raw,) in db.execute("SELECT data FROM features WHERE kind='railPlatforms'")]
    from .rail_platform_associations import apply_associations
    platforms = apply_associations(platforms,directory)
    grouped = defaultdict(list)
    unassociated = []
    for platform in platforms:
        props = platform['properties']
        source_kind = 'way' if props.get('osm_way_id') is not None else 'relation'
        if other_transport(props.get('way_tags', {})) or (source_kind,str(props.get('osm_'+source_kind+'_id'))) in metro_ids:
            continue
        owners = props.get('associated_station_ids', [])
        if not owners:
            unassociated.append(props.get('infrastructure_id'))
        for owner in owners:
            source = str(owner) if str(owner).startswith(('node/','way/','relation/')) else 'node/'+str(owner)
            grouped[source].append(platform)
    rows = []
    for key, name, feature in stations:
        props = feature['properties']
        if props.get('kind') not in ('station','halt') or key in urban_ids or other_transport(props.get('node_tags', {})):
            continue
        originals = grouped[key]
        display = platform_display(originals)
        counts = Counter(f['geometry']['type'] for f in display)
        tags = props.get('node_tags', {})
        declared = tags.get('platforms') or tags.get('station:platforms')
        reference = station_reference(props, {'name': name})
        if not declared and reference:
            declared = reference.get('attributes', {}).get('platform_count')
        expected = int(str(declared)) if str(declared).isdigit() else None
        faces = counts['Polygon'] + counts['MultiPolygon']
        lines = counts['LineString']
        issues = []
        if not originals:
            issues.append('未获取到关联站台源对象')
        if lines:
            issues.append('来源含站台线，不能当作真实面')
        if len(originals) != len(display):
            issues.append('重复源面，仅显示去重')
        if expected is not None and expected != faces + lines:
            issues.append('源对象数量与标注或参考不一致，待核验')
        rows.append({'station_source_id': key, 'name': name, 'source_objects': len(originals),
            'display_objects': len(display), 'polygon_objects': faces, 'line_objects': lines,
            'duplicate_objects': len(originals)-len(display), 'declared_platform_count': expected,
            'count_source': 'osm_station_tags' if tags.get('platforms') or tags.get('station:platforms') else
                            'local_unverified_reference' if expected is not None else 'not_available',
            'issues': '；'.join(issues) or '源对象与显示一致，实际完整性未核验',
            'source_ids': '|'.join(str(f['properties'].get('infrastructure_id') or f['properties'].get('osm_way_id')) for f in originals)})
    summary = {'source': 'local_osm_snapshot_and_display_rules', 'snapshot': snapshot,
        'verification_status': 'automatic_audit', 'stations': len(rows),
        'with_platforms': sum(r['source_objects'] > 0 for r in rows),
        'line_only_stations': sum(r['line_objects'] > 0 and r['polygon_objects'] == 0 for r in rows),
        'stations_with_duplicates': sum(r['duplicate_objects'] > 0 for r in rows),
        'unassociated_platforms': len(unassociated),
        'unassociated_source_ids': unassociated,
        'notice': '逐站检查本地源对象、关联和显示；未逐站核验实地站台数量，未补造缺失边界。',
        'hongqiao': [r for r in rows if r['name'] == '上海虹桥站']}
    return rows, summary
