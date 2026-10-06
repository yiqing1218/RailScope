"""Selected-station adapter: shared StationTrack entities reference physical edges.

Source geometry stays in rail.sqlite; names/numbers and stable bindings are
stored in the existing workspace override layer and survive source reimports.
"""
from collections import defaultdict, deque
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path
import json
import math
import sqlite3

from railscope.domain import Station, StationTrack, StationTrackEdge, Yard
from railscope.identity import IdentityRegistry
from railscope.integrity import path_refs
try:
    from .domain_adapter import build_repository
    from .station_track_semantics import track_number, migrate_track_override, semantic_track_name
except ImportError:
    from domain_adapter import build_repository
    from station_track_semantics import track_number, migrate_track_override, semantic_track_name


def ordered_track_legs(edges):
    """Orient one endpoint-delimited physical track; never invent a connection."""
    adjacency = defaultdict(list)
    for edge in edges:
        adjacency[edge['from_node']].append(edge)
        adjacency[edge['to_node']].append(edge)
    if any(len(values) > 2 for values in adjacency.values()):
        raise ValueError('股道包含分支，需要先整理真实道岔边界')
    ends = sorted((n for n, values in adjacency.items() if len(values) == 1), key=str)
    current = ends[0] if ends else min(adjacency, key=str)
    seen, result = set(), []
    while len(seen) < len(edges):
        edge = next((e for e in adjacency[current] if e['id'] not in seen), None)
        if edge is None:
            raise ValueError('股道源区间不连续，不能合并命名')
        forward = edge['from_node'] == current
        result.append((edge['id'], forward))
        seen.add(edge['id'])
        current = edge['to_node'] if forward else edge['from_node']
    return result


def track_sections(sections, by_edge, overrides):
    """Join named fragments only with equal source track numbers and real nodes."""
    parent = {key:key for key in sections}
    manual_names = {key: set() for key in sections}
    def root(key):
        while key != parent[key]:
            parent[key] = parent[parent[key]]; key = parent[key]
        return key
    endpoint_groups = defaultdict(list)
    for section, members in sections.items():
        values = {track_number(by_edge[key].get('way_tags', {})) for key in members}
        values.discard(None)
        if len(values) != 1:
            continue
        number = next(iter(values))
        custom = overrides.get('object:section_id:' + section, {})
        manual = custom.get('display_name') if custom.get('source') == 'manual' or custom.get('verification_status') in ('user_named', 'user_verified', 'official_confirmed') else None
        if manual:
            manual_names[section].add(manual)
        for key in members:
            for node in (by_edge[key]['from_node'], by_edge[key]['to_node']):
                endpoint_groups[(number,node)].append((section,manual))
    for group in endpoint_groups.values():
        if len({section for section, _ in group}) > 2:
            continue
        for a, a_name in group:
            for b, b_name in group:
                ra,rb=root(a),root(b)
                names = manual_names[ra] | manual_names[rb]
                if len(names) > 1:
                    continue
                parent[max(ra,rb)]=min(ra,rb)
                manual_names[min(ra,rb)] = names
    joined = defaultdict(list)
    for key in sections:joined[root(key)].append(key)
    return [tuple(sorted(group)) for group in joined.values()]


def station_assets(db, source):
    """All source-associated platforms/areas, even outside track bounding boxes."""
    source_node = str(source).removeprefix('node/')
    try:
        from .rail_platform_associations import apply_associations, association_edits
    except ImportError:
        from rail_platform_associations import apply_associations, association_edits
    path = db.execute('PRAGMA database_list').fetchone()[2]
    edits = association_edits(Path(path).parent) if path else {}
    edited_sources = [key for key, edit in edits.items() if
                      {source, source_node} & set(map(str, edit.get('properties', {}).get('associated_station_ids') or []))]
    features = [json.loads(raw) for (raw,) in db.execute(
        "SELECT data FROM features WHERE kind IN ('railPlatforms','railStationAreas') AND (EXISTS "
        "(SELECT 1 FROM json_each(json_extract(data,'$.properties.associated_station_ids')) WHERE CAST(value AS TEXT) IN (?,?)) "
        "OR json_extract(data,'$.properties.infrastructure_id') IN (SELECT value FROM json_each(?)))",
        (source, source_node, json.dumps(edited_sources)))]
    features = apply_associations(features, Path(path).parent) if path else features
    return [f for f in features if source_node in set(map(str,f['properties'].get('associated_station_ids', [])))
            or source in set(map(str,f['properties'].get('associated_station_ids', [])))]


def extend_station_approaches(db, index_path, edges, bounds, distance=2500, topology_depth=None):
    """Read connected physical continuations beyond the throat, without edits.

    The endpoint index avoids scanning the national geometry store. The window
    limits traversal only; complete source edges and their identities survive.
    """
    west, south, east, north = bounds
    dx = distance / (111320 * math.cos(math.radians((south+north)/2)))
    dy = distance / 111320
    known = {edge['id'] for edge in edges}
    queue = deque()
    visited = set()
    with closing(sqlite3.connect(Path(index_path).resolve().as_uri()+'?mode=ro', uri=True)) as index:
        def enqueue(edge, depth=0):
            row = index.execute('SELECT a,b FROM edges WHERE id=?', (edge['id'],)).fetchone()
            if row:
                for node, point in zip(row, (edge['coordinates'][0],edge['coordinates'][-1])):
                    if topology_depth is not None or west-dx <= point[0] <= east+dx and south-dy <= point[1] <= north+dy:
                        queue.append((node, depth))
        for edge in edges:
            enqueue(edge)
        while queue:
            node, depth = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            if topology_depth is not None and depth >= topology_depth:
                continue
            for (key,) in index.execute('SELECT id FROM edges WHERE a=? UNION SELECT id FROM edges WHERE b=?', (node,node)):
                if key in known:
                    continue
                raw = db.execute('SELECT data FROM edges WHERE id=?', (key,)).fetchone()
                if not raw:
                    continue
                edge = json.loads(raw[0])
                if topology_depth is not None and edge.get('way_tags', {}).get('service') in ('yard', 'siding', 'spur') and not all(
                        west-dx*.08 <= p[0] <= east+dx*.08 and south-dy*.08 <= p[1] <= north+dy*.08
                        for p in edge['coordinates']):
                    # Preserve short throat fragments needed by station tracks;
                    # unrelated external sidings do not extend the neighbourhood.
                    continue
                known.add(key)
                edges.append(edge)
                enqueue(edge, depth+1)


def load_station_tracks(directory, identity_path, station, overrides, approach_depth=None):
    """Return repository + source bindings + real platform/switch geometries."""
    directory = Path(directory)
    name = str(station.get('name') or station.get('display_name') or '').removesuffix('站')
    group = station.get('catalog_group_id')
    with closing(sqlite3.connect(directory / 'rail_catalog.sqlite')) as db:
        if group and str(group).startswith('ST-'):
            row = db.execute('SELECT station_name FROM catalog WHERE id=?', (group,)).fetchone()
            if row:
                name = row[0].removesuffix('站')
        groups = [(key, json.loads(raw)) for key, raw in db.execute(
            "SELECT id,data FROM catalog WHERE station_name IN (?,?)", (name, name + '站'))]
        source = station.get('station_source_id') or station.get('infrastructure_id') or station.get('station_source')
        if source:
            groups = [(key, row) for key, row in groups
                      if not overrides.get(key, {}).get('station_source')
                      or overrides[key]['station_source'] == source]
            manual_groups = {key for key, override in overrides.items()
                             if key.startswith('ST-') and override.get('station_source') == source}
            for key in sorted(manual_groups - {key for key, _ in groups}):
                row = db.execute('SELECT data FROM catalog WHERE id=?', (key,)).fetchone()
                if row:
                    groups.append((key, json.loads(row[0])))
    province = station.get('province')
    if province:
        groups = [(key, row) for key, row in groups if province in row.get('provinces', [])]
    regions = {tuple(row.get('provinces', [])) for _, row in groups}
    if len(regions) > 1 and not province:
        raise ValueError('存在同名异地站场，请从地图选择具体车站')
    features = []
    with closing(sqlite3.connect(directory / 'rail.sqlite')) as db:
        for key, _ in groups:
            features.extend(json.loads(row[0]) for row in db.execute(
                'SELECT f.data FROM rail_feature_groups g JOIN features f ON f.id=g.feature_id WHERE g.group_id=?', (key,)))
        if not features:
            # A station may have real platforms/area and passing tracks without
            # an ST catalog group (e.g. source ways carry only a railway name).
            # Export uses its own source assets instead of requiring a manual
            # directory move, which must never move the main line under a station.
            with closing(sqlite3.connect(directory/'rail_lines.sqlite')) as index:
                candidates = index.execute('SELECT source_id,data FROM station_directory WHERE source_id=?' if source
                    else 'SELECT source_id,data FROM station_directory WHERE name IN (?,?)',
                    (source,) if source else (name,name+'站')).fetchall()
            if len(candidates) != 1:
                raise ValueError('未找到唯一车站实体，请从地图选择具体车站')
            source, raw_station = candidates[0]
            feature = json.loads(raw_station)
            assets = station_assets(db,source)
            from shapely.geometry import shape
            boxes = [shape(asset['geometry']).bounds for asset in assets]
            if boxes:
                west,south = min(b[0] for b in boxes),min(b[1] for b in boxes)
                east,north = max(b[2] for b in boxes),max(b[3] for b in boxes)
                latitude = (south+north)/2
                dx,dy = 150/(111320*math.cos(math.radians(latitude))),150/111320
                west -= dx; east += dx; south -= dy; north += dy
            else:
                x,y = feature['geometry']['coordinates']
                # Selection window only, never a fabricated station boundary.
                dx,dy = 600/(111320*math.cos(math.radians(y))),600/111320
                west,east,south,north = x-dx,x+dx,y-dy,y+dy
            features = [json.loads(raw) for (raw,) in db.execute(
                "SELECT f.data FROM bounds b JOIN features f ON f.id=b.id WHERE f.kind='rail' "
                "AND b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=? "
                "AND json_extract(f.data,'$.properties.network_edge_id') IS NOT NULL",
                (west,east,south,north))]
            if not features:
                raise ValueError('该站尚无可导出的真实轨道数据')
        # Complete extraction from a real boundary, independent of ST catalog.
        if source:
            assets = station_assets(db,source)
            try:
                from .station_diagram.extractor import station_boundaries
            except ImportError:
                from station_diagram.extractor import station_boundaries
            boundary = station_boundaries(assets)
            if boundary is not None:
                from shapely.geometry import shape
                bx0,by0,bx1,by1 = boundary.bounds
                inside = [json.loads(raw) for (raw,) in db.execute(
                    "SELECT f.data FROM bounds b JOIN features f ON f.id=b.id WHERE f.kind='rail' "
                    "AND b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?",
                    (bx0,bx1,by0,by1))]
                features.extend(f for f in inside if f['properties'].get('network_edge_id')
                                and shape(f['geometry']).intersects(boundary) and f not in features)
        edge_ids = {f['properties']['network_edge_id'] for f in features}
        edges = [json.loads(db.execute('SELECT data FROM edges WHERE id=?', (key,)).fetchone()[0]) for key in sorted(edge_ids)]
        coords = [p for edge in edges for p in edge['coordinates']]
        west, south = min(p[0] for p in coords), min(p[1] for p in coords)
        east, north = max(p[0] for p in coords), max(p[1] for p in coords)
        # Context is bounded by the identified station yard, not a generic radius.
        context = [json.loads(raw) for (raw,) in db.execute(
            "SELECT f.data FROM bounds b JOIN features f ON f.id=b.id WHERE b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=? AND f.kind IN ('railPlatforms','railStationAreas','railPoints','rail')",
            (west, east, south, north))]
        access_ids = {f['properties']['network_edge_id'] for f in context
                      if f['geometry']['type'] == 'LineString' and f['properties'].get('network_edge_id')} - edge_ids
        for key in sorted(access_ids):
            edges.append(json.loads(db.execute('SELECT data FROM edges WHERE id=?', (key,)).fetchone()[0]))
        extend_station_approaches(db, directory/'rail_lines.sqlite', edges, (west,south,east,north),
                                 topology_depth=approach_depth)
    document = {'schema': 'railscope.rail-plan.v2', 'routes': [], 'trains': [], 'extensions': {},
                'required_capabilities': [], 'service_date': '2026-01-01', 'timezone': 'Asia/Shanghai', 'source': 'station infrastructure'}
    repo, bindings = build_repository({'edges': edges, 'points': []}, document, identity_path, overrides=overrides)
    # Apply workspace railway names to the presentation adapter without changing
    # its shared infrastructure identities or source geometry.
    for source_line, line_id in bindings['lines'].items():
        edit = overrides.get(source_line, overrides.get(line_id, {}))
        if edit.get('line_kind') != 'station':
            label = edit.get('assembly_name') or edit.get('line_name') or edit.get('display_name')
            if label:
                repo.lines[line_id] = replace(repo.lines[line_id], name=label)
    for feature in features + context:
        props = feature.get('properties', {})
        edit = overrides.get(props.get('catalog_group_id'), {})
        if not edit or edit.get('line_kind') == 'station':
            continue
        source_edge = props.get('network_edge_id')
        edge = repo.edges.get(bindings['edges'].get(source_edge))
        if edge and edge.infrastructure_line_id in repo.lines:
            line = repo.lines[edge.infrastructure_line_id]
            label = edit.get('assembly_name') or edit.get('line_name') or edit.get('display_name')
            if label or edit.get('technical_attributes'):
                repo.lines[line.id] = replace(line, name=label or line.name,
                    provenance={**line.provenance, 'workspace_presentation': {
                        'technical_attributes': edit.get('technical_attributes', {}),
                        'source_group_id': props.get('catalog_group_id'),
                        'source': 'workspace_override', 'verification_status': 'user_edited'}})
    registry = IdentityRegistry(identity_path)
    source = source or station.get('station_source_id') or station.get('infrastructure_id') or station.get('station_source')
    if not source:
        with closing(sqlite3.connect(directory / 'rail_lines.sqlite')) as db:
            candidates = [(key, json.loads(raw)) for key, raw in db.execute(
                'SELECT source_id,data FROM station_directory WHERE name IN (?,?)', (name, name+'站'))]
        candidates = [(key, f) for key, f in candidates if west-.01 <= f['geometry']['coordinates'][0] <= east+.01
                      and south-.01 <= f['geometry']['coordinates'][1] <= north+.01]
        if len(candidates) != 1:
            raise ValueError('站场尚未唯一关联到车站实体，请从地图车站对象进入')
        source = candidates[0][0]
    station_id = registry.resolve_alias('station', 'osm/' + source, 'ST')
    with closing(sqlite3.connect(directory / 'rail_lines.sqlite')) as db:
        source_record = db.execute('SELECT data FROM station_directory WHERE source_id=?', (source,)).fetchone()
    station_coordinate = (json.loads(source_record[0])['geometry']['coordinates'] if source_record
                          else (next(iter(repo.nodes.values())).lon, next(iter(repo.nodes.values())).lat))
    anchor = min(repo.nodes.values(), key=lambda node:
                 ((node.lon-station_coordinate[0])*math.cos(math.radians(station_coordinate[1])))**2
                 + (node.lat-station_coordinate[1])**2)
    try:
        from .rail_station_directory import display_name
    except ImportError:
        from rail_station_directory import display_name
    full_name = overrides.get('station:' + str(source), {}).get('display_name') or display_name(name)
    repo.stations[station_id] = Station(station_id, full_name, *station_coordinate, anchor.id,
                                       source_member_ids=(source,), verification_status='automatic_reference')
    sections = defaultdict(set)
    for feature in features:
        props = feature['properties']
        sections[props.get('section_id') or props['network_edge_id']].add(props['network_edge_id'])
    by_edge = {edge['id']: edge for edge in edges}
    rows = {}
    for source_sections in track_sections(sections, by_edge, overrides):
        members = set().union(*(sections[section] for section in source_sections))
        keys = ['object:' + ('section_id:' if section.startswith('RS-') else 'network_edge_id:') + section for section in source_sections]
        saved_values = [overrides.get(key, {}) for key in keys]
        saved = next((v for v in saved_values if v.get('source') == 'manual' or v.get('verification_status') in ('user_named', 'user_verified', 'official_confirmed')), saved_values[0])
        # Source section IDs remain aliases, not per-map-fragment business IDs.
        ident = registry.resolve_alias('station_track', source_sections[0], 'TRK')
        source_edges = [by_edge[value] for value in sorted(members)]
        refs = path_refs(repo, [(bindings['edges'][key], forward) for key, forward in ordered_track_legs(source_edges)], allow_nonoperating=True)
        def shared_attribute(attribute, default=None):
            values = {getattr(repo.edges[ref.edge_id], attribute) for ref in refs}
            return next(iter(values)) if len(values) == 1 else default
        role = shared_attribute('track_role', 'unknown')
        saved = migrate_track_override(saved, full_name, role)
        raw = saved.get('station_track', {})
        if saved.get('station_yard'):
            yard = Yard(**saved['station_yard'])
            if yard.id != raw.get('yard_id') or yard.station_id != station_id:
                raise ValueError('分场引用与当前车站不一致，需先迁移核对')
            repo.yards[yard.id] = yard
        if raw.get('edge_refs') and {ref['edge_id'] for ref in raw['edge_refs']} != {ref.edge_id for ref in refs}:
            raise ValueError('股道物理引用发生变化，需要迁移核对：' + (saved.get('display_name') or ident))
        tags = [edge.get('way_tags', {}) for edge in source_edges]
        # A main line's ref (e.g. 3043) identifies the railway, never its track.
        numbers = {track_number(t) for t in tags} - {None}
        number = str(saved.get('track_number') or raw.get('track_number') or (next(iter(numbers)) if len(numbers) == 1 else ''))
        track_name = saved.get('display_name') or raw.get('name') or next((t['name'] for t in tags if t.get('name') and t.get('service') in ('yard','siding')), '')
        if saved.get('source') == 'osm_track_ref' and number:
            track_name = number + ('' if number.endswith('道') else '道')
        if not track_name and number:
            track_name = number + ('' if number.endswith('道') else '道')
        provenance = dict(raw.get('provenance', {}))
        provenance.setdefault('track_role', {'source': 'shared_network_edges',
            'edge_ids': [ref.edge_id for ref in refs], 'verification_status': 'derived'})
        if number and 'track_number' not in provenance:
            provenance['track_number'] = {'source': 'workspace_override' if saved.get('track_number') else 'osm_explicit',
                'evidence': number, 'verification_status': saved.get('verification_status', 'osm_explicit')}
        track_name = track_name.replace('（参考）', '').replace('(参考)', '').strip()
        entity = StationTrack(raw.get('id') or ident, station_id, track_name or semantic_track_name(full_name, role), number or None,
            platform_number=raw.get('platform_number'),
            length_m=refs[-1].end_distance_m, is_virtual=False, edge_refs=refs,
            track_role=raw.get('track_role', role), railway_class=raw.get('railway_class') or shared_attribute('railway_class', 'unknown'),
            infrastructure_line_id=raw.get('infrastructure_line_id') if 'infrastructure_line_id' in raw else shared_attribute('infrastructure_line_id'),
            yard_id=raw['yard_id'] if 'yard_id' in raw else shared_attribute('yard_id'), zone_id=raw.get('zone_id') or shared_attribute('zone_id'),
            provenance=provenance, legacy_metadata=raw.get('legacy_metadata', saved.get('legacy_metadata', {})),
            source_member_ids=source_sections, snapshot_id=str((directory / 'rail.sqlite').stat().st_mtime_ns),
            verification_status=saved.get('verification_status', 'user_named' if saved.get('source') == 'manual' else 'source_unverified'))
        ident = entity.id
        repo.station_tracks[ident] = entity
        repo.station_track_edges.extend(StationTrackEdge(ident, ref.edge_id, ref.sequence,
            'forward' if ref.forward else 'reverse') for ref in refs)
        points = [p for edge in source_edges for p in edge['coordinates']]
        rows[ident] = {'keys': keys, 'source_edges': tuple(sorted(members)),
                       'saved_overrides': {key: overrides.get(key, {}) for key in keys},
                       'station_source': source, 'station_name': full_name,
                       'bounds': [[min(p[0] for p in points), min(p[1] for p in points)],
                                  [max(p[0] for p in points), max(p[1] for p in points)]]}
    source_node = str(source).removeprefix('node/')
    with closing(sqlite3.connect(directory/'rail.sqlite')) as db:
        # Sparse source track ways can have zero-width bounds. Their platforms
        # still belong to the station through real OSM membership/association.
        context.extend(f for f in station_assets(db,source) if f not in context)
    context = [f for f in context if not f['properties'].get('associated_station_ids')
               or source_node in set(map(str, f['properties'].get('associated_station_ids', [])))]
    try:
        from .rail_platform_associations import apply_associations
    except ImportError:
        from rail_platform_associations import apply_associations
    context = apply_associations(context,directory)
    context = [f for f in context if not f['properties'].get('association_candidate_station_ids') or
               f['properties'].get('associated_station_ids')]
    try:
        from .rail_platforms import platform_display
    except ImportError:
        from rail_platforms import platform_display
    platforms = [f for f in context if f['properties'].get('boundary_kind') == 'platform']
    context = [f for f in context if f not in platforms] + platform_display(platforms)
    try:
        from .china_emu import station_reference
        from .reference_integration import integrate_station_yards
    except ImportError:
        from china_emu import station_reference
        from reference_integration import integrate_station_yards
    reference = station_reference({'name':full_name}, {**station, 'name':full_name,
        'line_names':[line.name for line in repo.lines.values()]})
    if reference:
        integrate_station_yards(repo,reference,context)
    try:
        from .rail_line_terminals import integrate_terminals
    except ImportError:
        from rail_line_terminals import integrate_terminals
    integrate_terminals(repo)
    return repo, rows, context


def schematic_station_info(directory, repo, rows, overrides):
    """Known station attributes and full railway termini, never nearby nodes."""
    try:
        from .catalog_metadata import station_type, station_overview
        from .station_schematic import station_projection
        from .rail_line_terminals import nominal_terminals
    except ImportError:
        from catalog_metadata import station_type, station_overview
        from station_schematic import station_projection
        from rail_line_terminals import nominal_terminals
    station = next(iter(repo.stations.values()))
    source = next((row.get('station_source') for row in rows.values() if row.get('station_source')), None)
    custom = overrides.get('station:' + str(source), {})
    with closing(sqlite3.connect(Path(directory) / 'rail_lines.sqlite')) as db:
        row = db.execute('SELECT data FROM station_directory WHERE source_id=?', (source,)).fetchone()
        props = json.loads(row[0]).get('properties', {}) if row else {}
        attributes = station_overview(props, {'name': station.name}, custom)
        kind = custom.get('station_type') or props.get('station_type_hint') or station_type(props.get('node_tags', {}), props.get('kind', ''))
        summary = [kind if kind != '未定义' else '类型待核实']
        for key, title in (('platform_count','站台'), ('track_count','股道'), ('platform_scale','站场规模'), ('station_grade','等级')):
            if attributes.get(key): summary.append(title + '：' + attributes[key])
        local, *_ = station_projection(repo)
        destinations = {}
        for line in repo.lines.values():
            if line.name.startswith('未命名'):
                continue
            edit = overrides.get(line.source_id, overrides.get(line.id, {}))
            for key, assembly in overrides.items():
                if key.startswith('line-assembly:') and line.source_id in assembly.get('members', []):
                    edit = assembly.get('attributes', {})
                    break
            attributes = {**line.provenance.get('workspace_presentation', {}).get('technical_attributes', {}),
                          **edit.get('technical_attributes', {})}
            tags = next((edge.source_tags for edge in repo.edges.values()
                         if edge.infrastructure_line_id == line.id
                         and edge.source_tags.get('from') and edge.source_tags.get('to')), {})
            start = attributes.get('start_terminal') or line.start_terminal or tags.get('from')
            end = attributes.get('end_terminal') or line.end_terminal or tags.get('to')
            reference = nominal_terminals(line.name)
            evidence = ('workspace_railway_terminals' if attributes.get('start_terminal') or attributes.get('end_terminal')
                        else line.provenance.get('terminal_reference',{}).get('source_url') or
                        ('osm_explicit_route_termini' if tags else reference[2] if reference else None))
            if not start and reference:
                start = reference[0][0]
            if not end and reference:
                end = reference[1][0]
            if not start or not end:
                continue
            def coordinate(label, fallback):
                # Only look up the specified terminus, never choose an adjacent station.
                values = db.execute('SELECT name,data FROM station_directory WHERE name=? OR name=? OR name LIKE ?',
                    (label, label+'站', label+'%站')).fetchall()
                exact = [raw for name,raw in values if name in (label,label+'站')]
                points = [json.loads(raw)['geometry']['coordinates'] for raw in (exact or [raw for _,raw in values])]
                return tuple(sum(p[i] for p in points)/len(points) for i in (0,1)) if points else fallback
            a = coordinate(start, reference[0][1] if reference and start==reference[0][0] else None)
            b = coordinate(end, reference[1][1] if reference and end==reference[1][0] else None)
            left, right = (start, end) if not a or not b or local(a)[0] <= local(b)[0] else (end, start)
            destinations[line.id] = {'left': left, 'right': right,
                'terminals': [{'name':start, 'coordinates':a}, {'name':end, 'coordinates':b}],
                'source': evidence,
                'terminal_provenance': line.provenance.get('terminal_reference',{}),
                'snapshot': str((Path(directory)/'rail_lines.sqlite').stat().st_mtime_ns),
                'verification_status': 'nominal_route_reference', 'confidence': None}
    return {'summary': ' · '.join(summary), 'line_destinations': destinations}


def track_overrides(repo, rows):
    return {source_key: {**rows[key].get('saved_overrides', {}).get(source_key, {}),
            'station_track': asdict(track), 'station_track_id': track.id,
            'station_yard': asdict(repo.yards[track.yard_id]) if track.yard_id in repo.yards else None,
            'source_edge_ids': list(rows[key]['source_edges']),
            'display_name': track.name, 'track_number': track.track_number,
            'source': 'manual' if track.verification_status == 'user_named' else 'station_track_entity',
            'station_source': rows[key]['station_source'], 'station_name': rows[key]['station_name'],
            'bounds': rows[key]['bounds'], 'snapshot': track.snapshot_id, 'version': 3,
            'legacy_metadata': track.legacy_metadata,
            'confidence': None, 'verification_status': track.verification_status}
            for key, track in repo.station_tracks.items() for source_key in rows[key]['keys']}


def automatic_numbering(repo, replace_automatic=False):
    """Assign display aliases only. Official numbering always needs evidence."""
    if not repo.station_tracks:
        return 0
    positions = {}
    for key, track in repo.station_tracks.items():
        coords = [p for ref in track.edge_refs for p in repo.edges[ref.edge_id].coordinates]
        positions[key] = (sum(p[0] for p in coords)/len(coords), sum(p[1] for p in coords)/len(coords))
    values = list(positions.values())
    cx, cy = (sum(p[j] for p in values)/len(values) for j in (0, 1))
    cosine = math.cos(math.radians(cy))
    local = {key: ((x-cx)*cosine, y-cy) for key, (x,y) in positions.items()}
    xx=sum(x*x for x,y in local.values()); yy=sum(y*y for x,y in local.values()); xy=sum(x*y for x,y in local.values())
    angle = .5*math.atan2(2*xy, xx-yy)
    transverse = lambda key: -local[key][0]*math.sin(angle)+local[key][1]*math.cos(angle)
    candidates = [key for key, track in repo.station_tracks.items()
        if not track.track_number and track.verification_status not in ('user_named', 'user_verified', 'official_confirmed')
        and (replace_automatic or 'display_alias' not in track.provenance)]
    for number, key in enumerate(sorted(candidates, key=lambda k: (transverse(k), k)), 1):
        track = repo.station_tracks[key]
        provenance = {**track.provenance, 'display_alias': {'value': f'显示序号 {number}',
            'source': 'automatic_transverse_order', 'verification_status': 'display_only', 'snapshot_id': track.snapshot_id}}
        generic = track.name == '未编号股道' or '暂编' in track.name
        repo.station_tracks[key] = replace(track,
            name=semantic_track_name(repo.stations[track.station_id].name, track.track_role) if generic else track.name,
            provenance=provenance)
    return len(candidates)
