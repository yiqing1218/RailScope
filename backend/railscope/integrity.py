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
    result={key:[] for key in ('lines','sections','corridors','station_routes','train_runs')}
    if kind=='edge':
        result['lines']=[m.line_id for m in repo.memberships if m.edge_id==ident]
        for collection in ('sections','corridors','station_routes'):
            result[collection]=[p.id for p in getattr(repo,collection).values() if any(r.edge_id==ident for r in p.edge_refs)]
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
        result['train_runs'] += [s.train_run_id for s in repo.stops if s.station_id==ident]
    elif kind=='corridor':
        result['corridors']=[ident]
    result['train_runs'] += [t.id for t in repo.train_runs.values() if t.corridor_id in result['corridors']]
    return {key:sorted(set(value)) for key,value in result.items()}


def validate_repository(repo):
    errors=[]
    for edge in repo.edges.values():
        if edge.from_node_id not in repo.nodes or edge.to_node_id not in repo.nodes:
            errors.append(f'edge {edge.id}: endpoint missing')
        if edge.construction_status not in {'operating','construction','planned','disused','unknown'}:
            errors.append(f'edge {edge.id}: invalid construction_status')
        if len(edge.coordinates)<2 or not math.isfinite(edge.length_m) or edge.length_m<=0:
            errors.append(f'edge {edge.id}: invalid geometry/length')
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
    if any(refs[k] for k in ('sections','corridors','station_routes','train_runs')):
        raise ValueError(f"轨道被 {len(refs['corridors'])} 个 Corridor、{len(refs['train_runs'])} 个 TrainRun 引用：{refs}")
    del repo.edges[edge_id]
    repo.memberships[:]=[m for m in repo.memberships if m.edge_id!=edge_id]
