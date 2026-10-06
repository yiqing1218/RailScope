"""Audit all source intervals/catalog objects and apply a reversible workspace review."""
import argparse
from collections import Counter
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'backend')]
from desktop.data_install import active_rail_directory
from desktop.rail_line_review import (scan_groups, review_group, match_reference,
                                      reference_index, reviewed_styles, combine_groups, FIELDS)
from desktop.rail_semantics import semantic_record
from desktop.rail_categories import catalog_parents
from desktop.rail_style_resolver import style_key, CLASS_LABELS, ROLE_LABELS, STATUS_LABELS
from desktop.rail_style_resolver import speed_band
from desktop.catalog_workspace import CatalogWorkspace, read_overrides
from desktop.rail_line_workspace import effective_override, expand_assembly_changes
from desktop.china_emu import Client, parse_profile, load_store, atomic_json, SCHEMA

OUTPUT = ROOT/'data/processed/rail-review-20261006'


def refresh_reference(output):
    profiles = [p for p in load_store().profiles if p['kind'] == 'line']
    client = Client(output/'reference-cache')
    refreshed, failures = [], []
    def fetch(profile):
        html = client.fetch(profile['source_url'], refresh=True)
        return parse_profile(html, profile['source_url'], client.timestamps[profile['source_url']])
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = {pool.submit(fetch, p): p for p in profiles}
        for index, future in enumerate(as_completed(pending), 1):
            try:
                refreshed.append(future.result())
            except (OSError, ValueError) as error:
                profile = pending[future]
                failures.append({'source_url': profile['source_url'], 'error': str(error)})
                refreshed.append(profile)
            if index % 50 == 0:
                print(f'已逐页刷新线路资料 {index}/{len(profiles)}；失败 {len(failures)}', flush=True)
    atomic_json(output/'references.json', {'profiles': refreshed, 'failures': failures})
    print(f'线路资料刷新结束：{len(refreshed)} 页，失败 {len(failures)}', flush=True)


def legacy_type(facts, facility):
    role = facts['track_role']
    if role == 'crossover':
        return '渡线 / 道岔连接轨'
    if role == 'turnback_track':
        return '折返线'
    if role in ('maintenance_track', 'depot_track'):
        return '车辆段 / 检修线'
    label = {'high_speed': '高速铁路', 'conventional': '普速铁路',
             'freight': '货运', 'industrial': '货运'}.get(facts['railway_class'])
    if facility:
        return label+'站场股道' if label else '站场股道（类型待核对）'
    if facts['line_role'] == 'connecting_line':
        return '联络线 / 匝道'
    if facts['line_role'] == 'branch_line':
        return '支线 / 岔道'
    return ('货运铁路线' if label == '货运' else label+'线') if label else '未确认类型'


def search_records(output):
    result = {}
    for path in sorted(output.glob('search*.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            batch = json.loads(line)
            for name in batch['names']:
                result[name] = batch
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--refresh-reference', action='store_true')
    parser.add_argument('--refresh-only', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.refresh_reference:
        refresh_reference(args.output)
    if args.refresh_only:
        return
    directory = active_rail_directory(ROOT)
    cache = args.output/'source-groups.json'
    snapshot = str((directory/'rail.sqlite').stat().st_mtime_ns)
    if cache.exists() and (payload := json.loads(cache.read_text(encoding='utf-8'))).get('snapshot') == snapshot and payload.get('version') == 3:
        groups = payload['groups']
        for group in groups.values():
            group['design_speeds'] = {None if k == 'null' else int(k): v for k, v in group['design_speeds'].items()}
    else:
        groups = scan_groups(directory)
        atomic_json(cache, {'version': 3, 'snapshot': snapshot, 'groups': groups})
    ref_file = args.output/'references.json'
    profiles = json.loads(ref_file.read_text(encoding='utf-8'))['profiles'] if ref_file.exists() else load_store().profiles
    references = reference_index(profiles)
    source_file = ROOT/'data/catalog/rail_line_review_sources.json'
    claims = json.loads(source_file.read_text(encoding='utf-8'))['claims'] if source_file.exists() else []
    official = {name: claim for claim in claims for name in claim['names']}
    searched = search_records(args.output)
    workspace = CatalogWorkspace(ROOT/'data/user_settings/rail_catalog.json')
    workspace.load((ROOT/'data/catalog/rail_line_directory.json', ROOT/'data/catalog/rail_station_directory.json'))
    shared = read_overrides(ROOT/'data/catalog/rail_catalog_overrides.json')
    overrides = {key: {**shared.get(key, {}), **workspace.values.get(key, {})}
                 for key in shared.keys() | workspace.values.keys()}
    assembly_members = {}
    for key, value in overrides.items():
        if key in groups and value.get('assembly_id'):
            marker = overrides.get('line-assembly:' + value['assembly_id'], {})
            if marker.get('active', True):
                assembly_members.setdefault(value['assembly_id'], []).append(key)
    assembly_groups = {ident: combine_groups(groups[key] for key in members)
                       for ident, members in assembly_members.items()}
    changes, rows, totals = {}, [], Counter()
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect((directory/'rail_catalog.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        for key, raw in db.execute('SELECT id,data FROM catalog ORDER BY id'):
            record = json.loads(raw)
            edit = effective_override(overrides, key, {})
            current = {**record, **edit}
            current.update(semantic_record(record, current))
            name = str(record.get('line_name') or record.get('line_display_name') or record.get('name') or '').split(' · ')[0]
            group = groups.get(key)
            assembly = edit.get('assembly_id')
            if assembly in assembly_groups:
                group = assembly_groups[assembly]
            totals['catalog_objects'] += 1
            if not group:
                totals['no_source_group'] += 1
                missing_review = {'version': 1, 'source_snapshot': snapshot,
                    'verification_status': 'unresolved_missing_current_source',
                    'source_intervals': 0, 'reviewed_at': now}
                if edit.get('classification_review', {}).get('source_snapshot') != snapshot:
                    changes[key] = {'classification_review': missing_review}
                rows.append({'catalog_id': key, 'source_line_name': name, 'source_intervals': 0,
                             'old_class': current['railway_class'], 'new_class': current['railway_class'],
                             'line_role': current['line_role'], 'track_role': current['track_role'],
                             'operating_status': current['construction_status'], 'speed_band': 'unknown',
                             'old_directory': ' / '.join(edit.get('folder_path') or catalog_parents(current, 0)),
                             'new_directory': ' / '.join(edit.get('folder_path') or catalog_parents(current, 0)),
                             'style_key': style_key(current), 'mixed_fields': '', 'manual_conflicts': '',
                             'reference_url': '', 'primary_claim_url': '',
                             'search_status': searched.get(name, {}).get('status', 'source_identity_only'),
                             'verification': 'unresolved_missing_current_source'})
                continue
            totals['reviewed_objects'] += 1
            review_name = edit.get('assembly_name') or name
            profile = match_reference(review_name, references)
            primary = official.get(review_name)
            if primary and primary.get('bounds'):
                west,south,east,north = primary['bounds']
                x1,y1,x2,y2 = group['bounds']
                if not (west <= x1 <= x2 <= east and south <= y1 <= y2 <= north):
                    primary = None
            facts = review_group(group, profile, primary)
            # Explicit professional user decisions remain auditable; a source
            # disagreement is a conflict, not permission to invent a track role.
            manual = edit.get('rail_semantics', {})
            conflicts = []
            for field in FIELDS:
                proof = manual.get('provenance', {}).get(field, {})
                user_verified = (manual.get('verification_status') == 'user_verified' or
                                 proof.get('verification_status') == 'user_verified')
                if user_verified and manual.get(field) not in (None, 'unknown'):
                    if facts[field] not in ('unknown', manual[field]):
                        conflicts.append(field)
                    facts[field] = manual[field]
                    facts['provenance'][field] = current['provenance'][field]
            manual_technical = edit.get('technical_attributes', {})
            previous_review = edit.get('classification_review', {})
            speed_is_manual = (not previous_review or
                previous_review.get('design_speed_provenance', {}).get('verification_status') == 'user_verified')
            if manual_technical.get('design_speed_kmh') and speed_is_manual:
                manual_band = speed_band({'design_speed_kmh': manual_technical['design_speed_kmh']})
                if manual_band != 'unknown':
                    if facts['speed_band'] not in ('unknown', manual_band):
                        conflicts.append('design_speed_kmh')
                    facts['speed_band'] = manual_band
                    facts['design_speed_kmh'] = int(float(manual_technical['design_speed_kmh']))
                    facts['design_speed_provenance'] = {'source': 'workspace_override',
                        'verification_status': 'user_verified', 'evidence': '保留既有人工轨道设计速度'}
            elif manual_technical.get('speed_band') and speed_is_manual:
                manual_band = speed_band({'speed_band': manual_technical['speed_band']})
                if manual_band != 'unknown':
                    if facts['speed_band'] not in ('unknown', manual_band): conflicts.append('speed_band')
                    facts['speed_band'] = manual_band
                    facts['design_speed_kmh'] = None
                    facts['design_speed_provenance'] = {'source': 'workspace_override',
                        'verification_status': 'user_verified', 'evidence': '保留既有人工速度范围；未补造单一设计速度'}
            facility = ((edit.get('line_kind') == 'station') if assembly else key.startswith('ST-')) or bool(group['facility_edges'] == group['edge_count'])
            if manual.get('track_role') not in (None, 'unknown', 'main_track'):
                facility = True
            facts['facility_only'] = facility
            semantic = {field: facts[field] for field in FIELDS}
            semantic.update(source='rail_line_review', version=1, snapshot_id=snapshot,
                            verification_status='automatic_reference', confidence=None, scope='line_group',
                            evidence='全量来源区间逐属性复查；外部资料单独记录', provenance=facts['provenance'])
            for proof in semantic['provenance'].values():
                proof.setdefault('snapshot_id', snapshot)
            technical = {**current.get('technical_attributes', {}),
                         'operating_status': STATUS_LABELS[facts['construction_status']]}
            if facts['railway_class'] == 'high_speed':
                technical['speed_band'] = facts['speed_band']
                if facts.get('design_speed_kmh') is not None:
                    technical['design_speed_kmh'] = str(facts['design_speed_kmh'])
            # Unverified speed guesses from an older review must not survive.
            if not (manual_technical.get('design_speed_kmh') and speed_is_manual) and facts.get('design_speed_kmh') is None:
                technical.pop('design_speed_kmh', None)
            proposed = {**current, 'rail_semantics': semantic,
                        **semantic_record(record, {'rail_semantics': semantic}),
                        'facility_only': facility, 'technical_attributes': technical}
            if facility and proposed['track_role'] == 'main_track':
                # Source main-track evidence remains a track, even when named
                # as a station group in an old cache.
                facility = False
                proposed['facility_only'] = False
            path = list(catalog_parents(proposed, 0))
            review = {'version': 1, 'source_snapshot': snapshot, 'reviewed_at': now,
                      'source_intervals': group['edge_count'], 'mixed_fields': facts['mixed_fields'],
                      'conflicts': conflicts, 'source_values': group['values'],
                      'design_speed_provenance': facts['design_speed_provenance'],
                      'reference_url': profile['source_url'] if profile else None}
            delta = {'rail_semantics': semantic, 'track_type': legacy_type(proposed, facility),
                     'technical_attributes': technical, 'classification_review': review}
            # Reclassify line folders; station/yard ownership stays in the
            # existing facility repository instead of becoming a guessed line.
            if not facility:
                delta.update(folder_path=path, directory_view='lines', line_kind='track')
                if edit.get('folder_path') != path and edit.get('folder_path'):
                    delta['classification_previous_folder_path'] = edit['folder_path']
            elif proposed['track_role'] != 'main_track':
                delta.update(folder_path=path, directory_view='facilities', line_kind='station')
                if edit.get('folder_path') != path and edit.get('folder_path'):
                    delta['classification_previous_folder_path'] = edit['folder_path']
            changed_facts = any(current.get(field) != facts[field] for field in FIELDS)
            changed_folder = edit.get('folder_path') is not None and edit['folder_path'] != path
            changed_type = current.get('track_type') != delta['track_type']
            changed_speed = facts['railway_class'] == 'high_speed' and current.get('technical_attributes', {}).get('speed_band') != facts['speed_band']
            review_changed = any(previous_review.get(field) != value for field, value in review.items()
                                 if field != 'reviewed_at')
            if changed_facts or changed_folder or changed_type or changed_speed or review_changed:
                changes[key] = delta
            totals['mixed_objects'] += bool(facts['mixed_fields'])
            totals['manual_conflicts'] += bool(conflicts)
            totals['reference_matched'] += profile is not None
            totals['class_'+facts['railway_class']] += 1
            old_path = edit.get('folder_path') or list(catalog_parents(current, 0))
            row = {'catalog_id': key, 'source_line_name': name, 'source_intervals': group['edge_count'],
                   'old_class': current['railway_class'], 'new_class': facts['railway_class'],
                   'line_role': proposed['line_role'], 'track_role': proposed['track_role'],
                   'operating_status': facts['construction_status'], 'speed_band': facts['speed_band'],
                   'old_directory': ' / '.join(old_path), 'new_directory': ' / '.join(path),
                   'style_key': style_key(proposed), 'mixed_fields': '|'.join(facts['mixed_fields']),
                   'manual_conflicts': '|'.join(conflicts),
                   'reference_url': profile['source_url'] if profile else '',
                   'primary_claim_url': primary.get('source_url', '') if primary else '',
                   'search_status': searched.get(name, {}).get('status', 'source_identity_only'),
                   'verification': 'unresolved' if facts['railway_class'] == 'unknown' or conflicts else
                                   facts['provenance']['railway_class']['verification_status']}
            rows.append(row)
    # Persist one shared classification for every existing assembly. The
    # established adapter keeps all physical IDs and user membership intact.
    changes = expand_assembly_changes(overrides, changes)
    atomic_json(args.output/'proposed-overrides.json', {'changes': changes})
    with (args.output/'all-catalog-objects.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    totals['source_intervals'] = sum(g['rendered_members'] for g in groups.values())
    totals['source_groups'] = len(groups)
    totals['searched_names'] = len(searched)
    totals['changed_class'] = sum(r['old_class'] != r['new_class'] for r in rows)
    totals['changed_directory'] = sum(r['old_directory'] != r['new_directory'] for r in rows)
    totals['named_catalog_objects'] = sum(not r['source_line_name'].startswith(('未命名', '站线（')) for r in rows)
    totals['proposed_workspace_changes'] = len(changes)
    with closing(sqlite3.connect((directory/'rail_lines.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        totals['infrastructure_lines'] = db.execute('SELECT count(*) FROM lines').fetchone()[0]
        totals['named_line_labels'] = db.execute("SELECT count(DISTINCT source_name) FROM lines WHERE source_name NOT LIKE '未命名%' AND source_name NOT LIKE '站线（%'").fetchone()[0]
        totals['infrastructure_lines_with_source'] = sum(ident in groups for ident, in db.execute('SELECT id FROM lines'))
        line_rows = []
        for ident, name in db.execute('SELECT id,source_name FROM lines ORDER BY id'):
            group = groups.get(ident)
            primary = official.get(name)
            if primary and primary.get('bounds') and group:
                w,s,e,n = primary['bounds']; x1,y1,x2,y2 = group['bounds']
                if not (w <= x1 <= x2 <= e and s <= y1 <= y2 <= n): primary = None
            facts = review_group(group, match_reference(name, references), primary) if group else {}
            line_rows.append({'line_id':ident,'source_line_name':name,'source_intervals':group['edge_count'] if group else 0,
                              **{field:facts.get(field,'unknown') for field in FIELDS},
                              'speed_band':facts.get('speed_band','unknown'),
                              'mixed_fields':'|'.join(facts.get('mixed_fields',[])),
                              'verification':'source_reference_audit',
                              'search_status':searched.get(name,{}).get('status','source_identity_only')})
        with (args.output/'all-infrastructure-lines.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(line_rows[0]));writer.writeheader();writer.writerows(line_rows)
    atomic_json(args.output/'summary.json', dict(totals))
    if args.apply:
        from desktop.rail_style_ui import load_styles, validate_styles
        style_path = ROOT/'data/user_settings/rail_styles.json'
        styles = validate_styles(reviewed_styles(load_styles(style_path)))
        # Exact owner values are saved for restoration without copying GIS.
        before = {key: workspace.values.get(key) for key in changes}
        if workspace.cached_values() is None or str((directory/'rail.sqlite').stat().st_mtime_ns) != snapshot:
            raise RuntimeError('来源或人工工作区在检查期间变化；请重新检查后应用，避免覆盖新编辑。')
        if not (args.output/'before-overrides.json').exists():
            atomic_json(args.output/'before-overrides.json', {'overrides': before})
        workspace.update(changes)
        original = json.loads(style_path.read_text(encoding='utf-8')) if style_path.exists() else {}
        if not (args.output/'before-styles.json').exists():
            atomic_json(args.output/'before-styles.json', original)
        # Use the existing public style loader to retain migrated user colors.
        atomic_json(style_path, styles)
        if ref_file.exists():
            reference_path = ROOT/'data/user_settings/china_emu/reference.json'
            original = json.loads(reference_path.read_text(encoding='utf-8')) if reference_path.exists() else None
            backup = args.output/'before-reference.json'
            if not backup.exists(): atomic_json(backup, {'reference': original})
            updated = {p['id']: p for p in (original or {}).get('profiles', [])}
            updated.update({p['id']: p for p in profiles if p.get('id')})
            atomic_json(reference_path, {**(original or {}), 'schema': SCHEMA, 'profiles': list(updated.values())})
        print(f'已保存 {len(changes):,} 个独立工作区分类；没有修改原始轨道。', flush=True)
    print(json.dumps(dict(totals), ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
