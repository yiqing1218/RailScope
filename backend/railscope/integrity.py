"""Reference and physical-path invariants shared by every repository/editor."""
from __future__ import annotations

import math
from .domain import DirectedEdgeRef


def ordered_path_nodes(repo, edge_refs):
    """Return every real source node on a directed path with cumulative distance.

    Legacy reference assets may still carry a stop at an OSM geometry vertex.
    The vertex remains explicit and migratable even before the edge is split at
    that control point in a newer infrastructure snapshot.
    """
    source_nodes = {
        source_id: node.id
        for node in repo.nodes.values()
        for source_id in node.source_node_ids
    }
    result = []
    for ref in edge_refs:
        edge = repo.edges[ref.edge_id]
        if len(edge.osm_node_ids) == len(edge.coordinates) and edge.osm_node_ids:
            ids = [source_nodes.get(source_id) for source_id in edge.osm_node_ids]
            if any(node_id is None for node_id in ids):
                raise ValueError(f'Edge 源节点尚未映射：{edge.id}')
            coordinates = list(edge.coordinates)
        else:
            ids = [edge.from_node_id, edge.to_node_id]
            coordinates = [edge.coordinates[0], edge.coordinates[-1]]
        if not ref.forward:
            ids.reverse()
            coordinates.reverse()
        measured = [0.0]
        for a, b in zip(coordinates, coordinates[1:]):
            lat1, lat2 = math.radians(a[1]), math.radians(b[1])
            h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(math.radians(b[0]-a[0])/2)**2
            measured.append(measured[-1] + 6371008.8 * 2 * math.asin(min(1, math.sqrt(h))))
        scale = edge.length_m / measured[-1] if measured[-1] else 0
        entries = [(node_id, ref.start_distance_m + distance * scale) for node_id, distance in zip(ids, measured)]
        if entries:
            entries[0] = (entries[0][0], ref.start_distance_m)
            entries[-1] = (entries[-1][0], ref.end_distance_m)
        if result and entries and result[-1][0] == entries[0][0]:
            entries = entries[1:]
        result.extend(entries)
    return tuple(result)


def path_refs(repo, legs, allow_nonoperating=False):
    refs, previous, distance = [], None, 0.0
    for sequence, leg in enumerate(legs, 1):
        key, forward = leg
        edge = repo.edges.get(key)
        if edge is None:
            raise ValueError(f'NetworkEdge 不存在：{key}')
        if type(forward) is not bool:
            raise ValueError('轨道方向必须是布尔值')
        a,b=(edge.from_node_id,edge.to_node_id) if forward else (edge.to_node_id,edge.from_node_id)
        if a not in repo.nodes or b not in repo.nodes:
            raise ValueError(f'Edge 端点不存在：{key}')
        if previous is not None and a!=previous:
            raise ValueError(f'Corridor 轨道连接不连续：{key}')
        if edge.direction=='closed' or (edge.direction=='forward' and not forward) or (edge.direction=='reverse' and forward):
            raise ValueError(f'Edge 方向不允许：{key}')
        if not allow_nonoperating and edge.construction_status!='operating':
            raise ValueError(f'非运营轨道不能进入正式通道：{key}')
        if not math.isfinite(edge.length_m) or edge.length_m<=0:
            raise ValueError(f'Edge 长度无效：{key}')
        refs.append(DirectedEdgeRef(key,sequence,forward,distance,distance+edge.length_m))
        previous=b
        distance+=edge.length_m
    if not refs:
        raise ValueError('完整路径不能为空')
    return tuple(refs)


def references(repo, kind, ident):
    """Return stable IDs, not map features; include transitive corridor/run impact."""
    result={key:[] for key in ('lines','sections','corridors','station_routes','station_tracks','train_runs',
                              'operational_points','yards','station_zones','route_intents','edges','blocks')}
    if kind=='edge':
        result['lines']=[m.line_id for m in repo.memberships if m.edge_id==ident]
        for collection in ('sections','corridors','station_routes','station_tracks'):
            result[collection]=[p.id for p in getattr(repo,collection).values() if any(r.edge_id==ident for r in p.edge_refs)]
        result['station_tracks'] += [m.station_track_id for m in repo.station_track_edges if m.edge_id==ident]
        result['blocks'] = [m.block_id for m in repo.block_edges if m.edge_id==ident]
        result['train_runs'] += [s.train_run_id for s in repo.stops if s.stop_edge_id==ident]
    elif kind=='station':
        node_ids={n.id for n in repo.nodes.values() if n.station_id==ident}
        station=repo.stations.get(ident)
        if station:
            node_ids.add(station.anchor_node_id)
        edges={e.id for e in repo.edges.values() if {e.from_node_id,e.to_node_id}&node_ids}
        for edge in edges:
            for key, values in references(repo,'edge',edge).items():
                result[key].extend(values)
        result['station_routes'] += [r.id for r in repo.station_routes.values() if r.station_id==ident]
        for collection in ('station_tracks','operational_points','yards','station_zones'):
            result[collection] += [r.id for r in getattr(repo,collection).values() if r.station_id==ident]
        result['edges'] += [e.id for e in repo.edges.values() if e.facility_id==ident]
        result['train_runs'] += [s.train_run_id for s in repo.stops if s.station_id==ident]
        result['train_runs'] += [t.id for t in repo.train_runs.values() if ident in (t.origin_station_id,t.destination_station_id)]
    elif kind=='corridor':
        result['corridors']=[ident]
    elif kind=='yard':
        result['station_tracks'] = [t.id for t in repo.station_tracks.values() if t.yard_id==ident]
        result['station_zones'] = [z.id for z in repo.station_zones.values() if z.yard_id==ident]
        result['edges'] = [e.id for e in repo.edges.values() if e.yard_id==ident]
    elif kind=='station_zone':
        result['station_tracks'] = [t.id for t in repo.station_tracks.values() if t.zone_id==ident]
        result['edges'] = [e.id for e in repo.edges.values() if e.zone_id==ident]
    elif kind=='operational_point':
        result['edges'] = [e.id for e in repo.edges.values() if e.facility_id==ident]
    elif kind=='line':
        result['edges'] = [e.id for e in repo.edges.values() if e.infrastructure_line_id==ident]
        result['edges'] += [m.edge_id for m in repo.memberships if m.line_id==ident]
        result['station_tracks'] = [t.id for t in repo.station_tracks.values() if t.infrastructure_line_id==ident]
    elif kind=='node':
        result['operational_points'] = [p.id for p in repo.operational_points.values() if ident in p.node_ids]
        result['edges'] = [e.id for e in repo.edges.values() if ident in (e.from_node_id,e.to_node_id)]
    elif kind=='route_intent':
        result['route_intents'] = [ident]
    intent_kind = 'infrastructure_line' if kind=='line' else kind
    result['route_intents'] += [r.id for r in repo.route_intents.values()
        if any(step.kind==intent_kind and step.reference_id==ident for step in r.steps)]
    result['corridors'] += [c.id for c in repo.corridors.values() if c.route_intent_id in result['route_intents']]
    result['train_runs'] += [t.id for t in repo.train_runs.values() if t.corridor_id in result['corridors']]
    return {key:sorted(set(value)) for key,value in result.items()}


def validate_repository(repo):
    errors=[]
    from .presentation import valid_color, design_speed
    from .rail_semantics import RAILWAY_CLASSES, LINE_ROLES, TRACK_ROLES
    for line in repo.lines.values():
        try: design_speed(line.design_speed_kmh)
        except ValueError as exc: errors.append(f'line {line.id}: {exc}')
        if line.railway_class not in RAILWAY_CLASSES or line.line_role not in LINE_ROLES:
            errors.append(f'line {line.id}: invalid railway semantics')
    for corridor in repo.corridors.values():
        try: valid_color(corridor.color)
        except ValueError as exc: errors.append(f'corridor {corridor.id}: {exc}')
        if corridor.route_intent_id and corridor.route_intent_id not in repo.route_intents:
            errors.append(f'corridor {corridor.id}: route intent missing')
        if corridor.resolution_mode not in {'automatic_reference','strict_unique','manual_verified','legacy'}:
            errors.append(f'corridor {corridor.id}: invalid resolution mode')
    for point in repo.operational_points.values():
        if point.point_type not in {'station','junction_post','block_post','signal_box','other_control_point'}:
            errors.append(f'operational point {point.id}: invalid point type')
        if point.station_id and point.station_id not in repo.stations:
            errors.append(f'operational point {point.id}: station missing')
        if any(node not in repo.nodes for node in point.node_ids):
            errors.append(f'operational point {point.id}: node missing')
    for yard in repo.yards.values():
        if yard.station_id not in repo.stations:
            errors.append(f'yard {yard.id}: station missing')
    for zone in repo.station_zones.values():
        if zone.station_id not in repo.stations:
            errors.append(f'station zone {zone.id}: station missing')
        if zone.yard_id and (zone.yard_id not in repo.yards or repo.yards[zone.yard_id].station_id!=zone.station_id):
            errors.append(f'station zone {zone.id}: yard missing or belongs to another station')
    intent_collections={'operational_point':repo.operational_points,'infrastructure_line':repo.lines,
                        'node':repo.nodes,'station':repo.stations}
    for intent in repo.route_intents.values():
        if [s.sequence for s in intent.steps] != list(range(1,len(intent.steps)+1)):
            errors.append(f'route intent {intent.id}: invalid sequence')
        for step in intent.steps:
            if step.kind not in intent_collections or step.reference_id not in intent_collections[step.kind]:
                errors.append(f'route intent {intent.id}: missing {step.kind} {step.reference_id}')
            if step.direction not in {'forward','reverse','both','unknown'}:
                errors.append(f'route intent {intent.id}: invalid direction')
        kinds=[s.kind for s in intent.steps]
        if kinds and (len(kinds)%2==0 or any((k=='infrastructure_line') != (i%2==1) for i,k in enumerate(kinds))):
            errors.append(f'route intent {intent.id}: expected endpoint / line / endpoint sequence')
    for geometry in repo.service_area_geometries.values():
        if geometry.service_area_id not in repo.service_areas:
            errors.append(f'service geometry {geometry.id}: owner missing')
    for track in repo.station_tracks.values():
        if track.track_role not in TRACK_ROLES or track.railway_class not in RAILWAY_CLASSES:
            errors.append(f'station track {track.id}: invalid railway semantics')
        if track.infrastructure_line_id and track.infrastructure_line_id not in repo.lines:
            errors.append(f'station track {track.id}: line missing')
        for attr,collection in (('yard_id',repo.yards),('zone_id',repo.station_zones)):
            ident=getattr(track,attr)
            if ident and (ident not in collection or collection[ident].station_id!=track.station_id):
                errors.append(f'station track {track.id}: {attr} missing or belongs to another station')
        if track.yard_id in repo.yards and track.zone_id in repo.station_zones:
            zone=repo.station_zones[track.zone_id]
            if zone.yard_id and zone.yard_id!=track.yard_id:
                errors.append(f'station track {track.id}: inconsistent yard/zone')
        if track.edge_refs:
            try:
                expected = path_refs(repo, [(r.edge_id, r.forward) for r in track.edge_refs], allow_nonoperating=True)
                if expected != track.edge_refs:
                    raise ValueError('股道边序或累计里程无效')
            except ValueError as exc:
                errors.append(f'station track {track.id}: {exc}')
    track_members={}
    for member in repo.station_track_edges:
        if member.station_track_id not in repo.station_tracks or member.edge_id not in repo.edges:
            errors.append(f'station track membership {member.station_track_id}: missing track/edge')
        if member.direction not in {'forward','reverse'}:
            errors.append(f'station track membership {member.station_track_id}: invalid direction')
        track_members.setdefault(member.station_track_id,[]).append(member)
    for track_id,members in track_members.items():
        members=sorted(members,key=lambda m:m.sequence)
        if [m.sequence for m in members]!=list(range(1,len(members)+1)):
            errors.append(f'station track membership {track_id}: invalid sequence')
        try:
            expected=path_refs(repo,[(m.edge_id,m.forward) for m in members],allow_nonoperating=True)
            track=repo.station_tracks.get(track_id)
            if track and track.edge_refs and track.edge_refs!=expected:
                errors.append(f'station track membership {track_id}: disagrees with legacy edge_refs')
        except ValueError as exc:
            errors.append(f'station track membership {track_id}: {exc}')
    for edge in repo.edges.values():
        if edge.from_node_id not in repo.nodes or edge.to_node_id not in repo.nodes:
            errors.append(f'edge {edge.id}: endpoint missing')
        if edge.construction_status not in {'operating','construction','planned','disused','unknown'}:
            errors.append(f'edge {edge.id}: invalid construction_status')
        if len(edge.coordinates)<2 or not math.isfinite(edge.length_m) or edge.length_m<=0:
            errors.append(f'edge {edge.id}: invalid geometry/length')
        if edge.railway_class not in RAILWAY_CLASSES or edge.line_role not in LINE_ROLES or edge.track_role not in TRACK_ROLES:
            errors.append(f'edge {edge.id}: invalid railway semantics')
        if edge.infrastructure_line_id and edge.infrastructure_line_id not in repo.lines:
            errors.append(f'edge {edge.id}: infrastructure line missing')
        if edge.facility_id and not any(edge.facility_id in collection for collection in (repo.stations,repo.operational_points,repo.service_areas)):
            errors.append(f'edge {edge.id}: facility missing')
        facility_station = edge.facility_id if edge.facility_id in repo.stations else None
        if edge.facility_id in repo.operational_points:
            facility_station = repo.operational_points[edge.facility_id].station_id
        for attr,collection in (('yard_id',repo.yards),('zone_id',repo.station_zones)):
            ident=getattr(edge,attr)
            if ident and ident not in collection:
                errors.append(f'edge {edge.id}: {attr} missing')
            elif ident and facility_station and collection[ident].station_id!=facility_station:
                errors.append(f'edge {edge.id}: {attr} belongs to another station')
        if edge.yard_id in repo.yards and edge.zone_id in repo.station_zones:
            zone=repo.station_zones[edge.zone_id]
            if zone.station_id!=repo.yards[edge.yard_id].station_id or (zone.yard_id and zone.yard_id!=edge.yard_id):
                errors.append(f'edge {edge.id}: inconsistent yard/zone')
    for member in repo.memberships:
        if member.edge_id not in repo.edges or member.line_id not in repo.lines:
            errors.append(f'membership {member.edge_id}: missing edge/line')
    for collection in ('sections','corridors','station_routes'):
        for path in getattr(repo,collection).values():
            try:
                expected=path_refs(repo,[(r.edge_id,r.forward) for r in path.edge_refs],allow_nonoperating=collection=='sections')
                if expected!=path.edge_refs:
                    raise ValueError('路径序号或累计里程不一致')
                a=repo.edges[expected[0].edge_id]
                b=repo.edges[expected[-1].edge_id]
                start=a.from_node_id if expected[0].forward else a.to_node_id
                end=b.to_node_id if expected[-1].forward else b.from_node_id
                if hasattr(path,'origin_node_id') and (path.origin_node_id,path.destination_node_id)!=(start,end):
                    raise ValueError('通道起终端点不一致')
                if hasattr(path,'entry_node_id') and (path.entry_node_id,path.exit_node_id)!=(start,end):
                    raise ValueError('车站进路起终端点不一致')
                if getattr(path,'snapshot_id',None):
                    if path.snapshot_id not in repo.snapshots:
                        raise ValueError('通道基础设施快照不存在')
                    if any(repo.edges[r.edge_id].snapshot_id!=path.snapshot_id for r in expected):
                        raise ValueError('通道与轨道快照不兼容，须迁移核验')
            except ValueError as exc:
                errors.append(f'{collection} {path.id}: {exc}')
    for station in repo.stations.values():
        if station.anchor_node_id not in repo.nodes:
            errors.append(f'station {station.id}: anchor missing')
    for collection in ('station_areas','platforms','station_tracks','station_routes','stop_positions','entrances'):
        for obj in getattr(repo,collection).values():
            if obj.station_id not in repo.stations:
                errors.append(f'{collection} {obj.id}: station missing')
    for run in repo.train_runs.values():
        if run.service_id and run.service_id not in repo.train_services:
            errors.append(f'run {run.id}: service missing')
        path=repo.corridors.get(run.corridor_id) if run.corridor_id else None
        if not path:
            if run.corridor_id:
                errors.append(f'run {run.id}: path missing')
            continue  # unresolved imports may exist, but cannot simulate.
        try:
            order=[node for node, _ in ordered_path_nodes(repo, path.edge_refs)]
        except (KeyError, ValueError) as exc:
            errors.append(f'run {run.id}: invalid path nodes ({exc})')
            continue
        cursor=-1
        previous=-1
        try:
            from .services.timetable.canonical import stop_distances
            stop_distances(repo, path.edge_refs, repo.stops_for(run.id))
        except (KeyError, ValueError) as exc:
            errors.append(f'run {run.id}: {exc}')
        for stop in repo.stops_for(run.id):
            station=repo.stations.get(stop.station_id)
            if not station:
                errors.append(f'run {run.id}: station {stop.station_id} missing')
                continue
            try:
                if stop.stop_edge_id is None:
                    cursor=order.index(station.anchor_node_id,cursor+1)
            except ValueError:
                errors.append(f'run {run.id}: stops not in path order ({stop.station_id})')
            times=[t for t in (stop.arrival_time_s,stop.departure_time_s) if t is not None]
            if not times or any(type(t) is not int or t<previous for t in times) or times!=sorted(times):
                errors.append(f'run {run.id}: stop times not monotonic')
            if times: previous=times[-1]
            for ident,collection in ((stop.platform_id,'platforms'),(stop.station_track_id,'station_tracks'),(stop.station_route_id,'station_routes')):
                if ident:
                    item=getattr(repo,collection).get(ident)
                    if item is None or item.station_id!=stop.station_id:
                        errors.append(f'run {run.id}: invalid {collection} reference {ident}')
    if errors:
        raise ValueError('\n'.join(errors))
    return True


def delete_edge(repo, edge_id):
    refs=references(repo,'edge',edge_id)
    if any(refs[k] for k in ('sections','corridors','station_routes','station_tracks','train_runs','blocks')):
        raise ValueError(f"轨道被 {len(refs['corridors'])} 个 Corridor、{len(refs['train_runs'])} 个 TrainRun 引用：{refs}")
    del repo.edges[edge_id]
    repo.memberships[:]=[m for m in repo.memberships if m.edge_id!=edge_id]
