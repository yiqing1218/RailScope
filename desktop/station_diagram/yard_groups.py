"""Evidence-based drawing groups; no changes to infrastructure membership."""

from collections import defaultdict
import re
from statistics import median

from .yard_classifier import classify
from .helpers import mainline_label

COLORS = ("#1266b0", "#b2387b", "#218362", "#bc6732", "#6554b0", "#168c9a")


def railway_key(name):
    return re.sub(r"(?:铁路|线)$", "", str(name)).replace("高铁", "高速")


def group_tracks(repo, raw, options):
    ownership, warnings = classify(repo, set(raw), options)
    groups, edge_groups = {}, defaultdict(set)
    for track in sorted(repo.station_tracks.values(), key=lambda t: t.id):
        keys = {r.edge_id for r in track.edge_refs} & raw.keys()
        if not keys:
            continue
        own = ownership[min(keys)]
        rule = options.track_overrides.get(track.id, {})
        ident = rule.get("group_id") or track.yard_id
        if ident:
            entity = repo.yards.get(ident)
            name = entity.name if entity else ident.removeprefix("diagram-yard:")
            status = "manual_diagram_override" if rule.get("group_id") else own.status
        else:
            ident = "line:" + own.line_id if own.line_id else "unassigned"
            name = own.system + "（未分场）" if own.system else "未分场（待核对）"
            status = "unresolved_yard"
        group = groups.setdefault(
            ident,
            {
                "id": ident,
                "name": name,
                "track_ids": [],
                "edge_ids": set(),
                "source": status,
                "center": 0,
                "color": "#85919b",
                "caption": bool(track.yard_id or rule.get("group_id")),
                "legend": bool(
                    track.yard_id
                    or rule.get("group_id")
                    or mainline_label(repo.lines.get(own.line_id))
                ),
            },
        )
        group["track_ids"].append(track.id)
        group["edge_ids"].update(keys)
        for key in keys:
            edge_groups[key].add(ident)
    # A source line can identify a drawing group only if exactly one explicit
    # group uses it; shared trunks do not guess between competing yards.
    by_line = defaultdict(set)
    for key, values in edge_groups.items():
        if ownership[key].line_id:
            by_line[ownership[key].line_id].update(values)
    for key, own in ownership.items():
        if not edge_groups[key] and own.line_id and len(by_line[own.line_id]) == 1:
            edge_groups[key] = set(by_line[own.line_id])
    # Inherit drawing groups through physical station-track continuations only.
    # Equal-distance competing yards remain ambiguous; this is not a domain edit.
    from .helpers import edge_role
    from .topology import build_graph

    graph = build_graph(repo, set(raw), validate=False)
    frontier = {k for k in raw if edge_groups[k]}
    remaining = {
        k
        for k in raw
        if not edge_groups[k]
        and edge_role(repo, repo.edges[k], options)
        in ("station", "auxiliary", "connector")
    }
    while frontier and remaining:
        proposed = defaultdict(set)
        for key in frontier:
            for node in graph.endpoints[key]:
                for nxt in set(graph.adjacency[node]) & remaining:
                    proposed[nxt].update(edge_groups[key])
        for key, values in proposed.items():
            edge_groups[key] = values
        frontier = set(proposed)
        remaining -= frontier
    for ident, group in groups.items():
        values = [p[1] for key in group["edge_ids"] for p in raw[key]]
        group["center"] = median(values)
    for index, group in enumerate(sorted(groups.values(), key=lambda g: -g["center"])):
        rule = options.yard_overrides.get(group["id"], {})
        group["name"] = rule.get("name") or group["name"]
        explicit = {
            options.line_overrides.get(ownership[k].line_id, {}).get("color")
            or options.color_overrides.get(ownership[k].line_id)
            for k in group["edge_ids"]
        }
        explicit.discard(None)
        group["color"] = (
            rule.get("color")
            or (next(iter(explicit)) if len(explicit) == 1 else None)
            or (
                COLORS[index % len(COLORS)]
                if group["id"] != "unassigned"
                else "#85919b"
            )
        )
    for key, values in edge_groups.items():
        if len(values) == 1:
            group = groups[next(iter(values))]
            group["edge_ids"].add(key)
    for group in groups.values():
        inherited = [
            k
            for k in group["edge_ids"]
            if not ownership[k].system and not ownership[k].yard_id
        ]
        group["drawing_inheritance"] = {
            "edge_ids": sorted(inherited),
            "source": "connected_station_track_reference",
            "verification_status": "diagram_only_not_business_assignment",
        }
    return groups, dict(edge_groups), ownership, warnings
