"""Boundary first; outside selection follows stable endpoint connectivity."""

from collections import deque
from shapely.geometry import LineString, shape, box
from shapely.ops import unary_union
from .topology import build_graph
from .types import Extraction


def station_boundaries(context):
    result = []
    for feature in context:
        props = feature.get("properties", {})
        kind = props.get("boundary_kind") or props.get("area_type")
        geometry = feature.get("geometry", {})
        if kind in (
            "station_area",
            "station_boundary",
            "railway_station",
            "station_site",
        ) and geometry.get("type") in ("Polygon", "MultiPolygon"):
            area = shape(geometry)
            if not area.is_valid:
                raise ValueError("真实站区边界无效，需先核对源数据")
            result.append(area)
    return unary_union(result) if result else None


def extract(repo, context, options):
    from .helpers import edge_role

    refs = {
        ref.edge_id for track in repo.station_tracks.values() for ref in track.edge_refs
    }
    if not refs <= set(repo.edges):
        raise ValueError("股道引用缺少物理轨道，需先迁移核对")
    boundary = station_boundaries(context)
    warnings = []
    if boundary is not None:
        inner = {
            key
            for key, e in repo.edges.items()
            if LineString(e.coordinates).intersects(boundary)
        }
        source = "real_station_boundary"
    else:
        inner = set(refs)
        source = "associated_station_tracks_reference"
        warnings.append("缺少完整真实站区边界：使用已关联股道与真实站台选取参考范围。")
        # Complete short throat fragments between station track endpoints.
        nodes = {
            node
            for key in inner
            for node in (repo.edges[key].from_node_id, repo.edges[key].to_node_id)
        }
        inner.update(
            key
            for key, e in repo.edges.items()
            if e.from_node_id in nodes and e.to_node_id in nodes
        )
    if not inner:
        raise ValueError("没有可导出的站内真实轨道")
    graph = build_graph(repo, set(repo.edges), validate=False)
    # StationTrack paths often stop at the first switch. Short siding fragments
    # beyond that switch are still the throat, rather than external main lines.
    points = [p for k in inner for p in repo.edges[k].coordinates]
    throat_window = box(
        min(p[0] for p in points) - 0.002,
        min(p[1] for p in points) - 0.002,
        max(p[0] for p in points) + 0.002,
        max(p[1] for p in points) + 0.002,
    )

    def allowed(key):
        status = repo.edges[key].construction_status
        return (
            status == "operating"
            or status == "construction"
            and options.include_construction
        )

    distances = {key: 0 for key in inner if allowed(key)}
    if len(distances) < len(inner):
        warnings.append(
            f"{len(inner) - len(distances)} 条非运营站内轨道未纳入本图；在建轨道可按内容设置显示。"
        )
    queue = deque(sorted(distances))
    while queue:
        key = queue.popleft()
        if distances[key] >= options.topology_depth:
            continue
        for node in graph.endpoints[key]:
            for nxt in graph.adjacency[node]:
                if nxt in distances or not allowed(nxt):
                    continue
                if (
                    nxt not in inner
                    and edge_role(repo, repo.edges[nxt], options)
                    not in ("main", "connector")
                    and not throat_window.intersects(
                        LineString(repo.edges[nxt].coordinates)
                    )
                ):
                    continue
                distances[nxt] = distances[key] + 1
                queue.append(nxt)
    selected = set()
    for key in distances:
        e = repo.edges[key]
        role = edge_role(repo, e, options)
        if not options.line_overrides.get(e.infrastructure_line_id, {}).get(
            "visible", True
        ):
            continue
        visible = {
            "main": options.show_main,
            "station": options.show_station,
            "auxiliary": options.show_station,
            "connector": options.show_connectors,
        }
        if key not in inner:
            visible["main"] &= options.show_outer_main
            visible["connector"] &= options.show_outer_connectors
        if visible[role]:
            selected.add(key)
    if len(selected) < len(distances):
        warnings.append("已按内容设置隐藏部分轨道；当前图不包含完整站内连接。")
    return Extraction(inner, selected, source, warnings)
