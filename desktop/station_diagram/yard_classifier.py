"""Explicit membership only. No nearest-trunk or spatial color propagation."""

from collections import defaultdict
from .types import Ownership


CLASS_LABELS = {
    "high_speed": "高速场",
    "conventional": "普速场",
    "intercity": "城际场",
    "freight": "货运场",
}
CLASS_COLORS = {
    "high_speed": "#2463a3",
    "conventional": "#343d46",
    "intercity": "#a13cad",
    "freight": "#278557",
}


def business_line(line):
    from .helpers import mainline_label

    return bool(
        line
        and not line.name.startswith("未命名")
        and (
            line.line_role in ("main_line", "branch_line", "connecting_line")
            or mainline_label(line)
        )
    )


def classify(repo, keys, options):
    tracks = defaultdict(list)
    for t in repo.station_tracks.values():
        for ref in t.edge_refs:
            tracks[ref.edge_id].append(t)
    memberships = defaultdict(set)
    for m in repo.memberships:
        if business_line(repo.lines.get(m.line_id)):
            memberships[m.edge_id].add(m.line_id)
    result, unknown, conflicts = {}, [], []
    for key in sorted(keys):
        e = repo.edges[key]
        ts = tracks[key]
        for t in ts:
            if t.yard_id and t.yard_id not in repo.yards:
                raise ValueError("股道引用缺少明确分场实体：" + t.id)
            if t.yard_id and repo.yards[t.yard_id].station_id != t.station_id:
                raise ValueError("股道分场不属于当前车站：" + t.id)
            if t.infrastructure_line_id and t.infrastructure_line_id not in repo.lines:
                raise ValueError("股道引用未知业务线路：" + t.id)
        own = options.line_overrides.get(e.infrastructure_line_id, {})
        manual_lines = [
            t
            for t in ts
            if t.provenance.get("line_membership", {}).get("source")
            == "workspace_override"
        ]
        manual_yards = [
            t
            for t in ts
            if t.provenance.get("yard", {}).get("source") == "workspace_override"
        ]
        line_values = {t.infrastructure_line_id for t in manual_lines or ts}
        explicit_lines = {
            value for value in line_values if business_line(repo.lines.get(value))
        }
        explicit_yards = {t.yard_id for t in manual_yards or ts if t.yard_id}
        classes = {
            t.railway_class for t in manual_yards or ts if t.railway_class != "unknown"
        }
        conflict = (
            len(explicit_lines) > 1
            or len(explicit_yards) > 1
            or len(classes) > 1
            or bool(manual_lines)
            and len(line_values) > 1
        )
        yard = (
            next(iter(explicit_yards))
            if len(explicit_yards) == 1
            else None
            if manual_yards
            else e.yard_id
        )
        entity = repo.yards.get(yard)
        line = (
            next(iter(explicit_lines))
            if len(explicit_lines) == 1
            else None
            if manual_lines
            else e.infrastructure_line_id
        )
        if not business_line(repo.lines.get(line)) and not manual_lines:
            candidates = memberships[key]
            line = next(iter(candidates)) if len(candidates) == 1 else None
            conflict |= len(candidates) > 1
        cls = (
            next(iter(classes))
            if len(classes) == 1
            else "unknown"
            if manual_yards
            else e.railway_class
        )
        if cls == "unknown" and line in repo.lines and not manual_yards:
            cls = repo.lines[line].railway_class
        names = {
            t.provenance.get("yard", {}).get("name") for t in ts if t.yard_id == yard
        }
        names.discard(None)
        name = (
            entity.name
            if entity
            else next(iter(names))
            if len(names) == 1
            else CLASS_LABELS.get(cls)
            if yard
            else None
        )
        source = (
            "station_track_assignment"
            if explicit_lines or explicit_yards
            else "domain_line_membership"
            if line
            else "unavailable"
        )
        system = (
            repo.lines[line].name
            if line
            else (name or yard)
            if yard and (cls != "unknown" or entity and entity.yard_type != "unknown")
            else None
        )
        if own.get("system"):
            system = own["system"]
            source = "manual_diagram_override"
            conflict = False
        if conflict:
            result[key] = Ownership(
                None,
                None,
                None,
                None,
                "unknown",
                "conflicting_explicit_membership",
                "unresolved",
                tuple(t.id for t in ts),
            )
            conflicts.append(key)
        else:
            result[key] = Ownership(
                system,
                yard,
                name,
                line,
                cls,
                source,
                evidence=tuple(t.id for t in ts),
                yard_type=entity.yard_type if entity else "unknown",
            )
            if system is None:
                unknown.append(key)
    warnings = []
    if unknown:
        warnings.append(
            f"{len(unknown)} 条轨道归属未明确，使用中性灰色；未沿接轨关系猜测颜色。"
        )
    if conflicts:
        warnings.append(
            f"{len(conflicts)} 条轨道存在相互冲突的归属，保持待核对："
            + "、".join(conflicts[:4])
        )
    pending_yards = sum(bool(tracks[k]) and not result[k].yard_id for k in keys)
    if pending_yards:
        warnings.append(
            f"{pending_yards} 条站内轨道尚未明确分场归属；可在“股道名称与编号”中填写分场和业务线路。"
        )
    return result, warnings
