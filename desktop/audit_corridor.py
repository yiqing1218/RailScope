"""Reproducible, bounded-memory audit of indexed line membership and a corridor.

Run: python -m desktop.audit_corridor --start 仙林 --line 仙宁线 --end 南京南
"""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from .data_install import active_rail_directory
from .rail_line_store import DiskRailLineLibrary, build_line_index, fingerprint, index_ready


def audit(library, start_query, line_query, end_query, policy="strict"):
    with closing(sqlite3.connect(library.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        groups = []
        for group, count in db.execute("SELECT group_id,count(*) FROM line_groups GROUP BY group_id"):
            rows = db.execute("SELECT l.id,l.source_name,l.edge_count,l.track_type FROM line_groups g "
                              "JOIN lines l ON l.id=g.line_id WHERE g.group_id=? ORDER BY l.id", (group,)).fetchall()
            groups.append({"id": group, "name": rows[0][1], "source_members": count,
                           "members": [dict(zip(("id", "name", "edges", "classification"), row)) for row in rows]})
    report = {"schema": "railscope.line-connectivity-audit.v1", "automatic_groups": groups,
              "automatic_group_count": len(groups), "workspace_conflicts": library.workspace.conflicts}
    try:
        def station(query):
            candidates = [(key, label) for key, label in library.search_endpoints(query)
                          if library.endpoint_label(key).removesuffix("站") == query.removesuffix("站")]
            if len(candidates) != 1:
                raise ValueError(f"端点 {query} 有 {len(candidates)} 个匹配，请使用唯一名称")
            return candidates[0][0]

        start, end = station(start_query), station(end_query)
        lines = [line for line in library.connected_lines(start) if line["name"] == line_query]
        if len(lines) != 1:
            raise ValueError(f"起点接轨线路 {line_query} 有 {len(lines)} 个匹配")
        line = lines[0]["id"]
        sequence = [{"kind": "endpoint", "node_id": start}, {"kind": "line", "line_id": line},
                    {"kind": "endpoint", "node_id": end}]
        normalized, path = library.resolve_with_sequence(sequence, policy)
        report["corridor"] = {
            "status": "automatic_reference_geometry" if policy == "auto" else "unique_continuous_geometry",
            "policy": policy, "start": start, "line_id": line, "end": end,
            "selectable": any(key == end for key, _ in library.reachable_nodes(start, line, end_query)),
            "resolved_sequence": normalized, "edge_count": len(path),
            "length_m": round(sum(library.edges[leg["edge_id"]]["length_m"] for leg in path), 1),
            "membership": library.membership_provenance(normalized),
            "dispatch_verified": False,
        }
    except (ValueError, KeyError) as error:
        report["corridor"] = {"status": "unresolved", "reason": str(error)}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--start", default="仙林")
    parser.add_argument("--line", default="仙宁线")
    parser.add_argument("--end", default="南京南")
    parser.add_argument("--workspace", type=Path, help="可选的目录覆盖文件")
    parser.add_argument("--policy", choices=("strict", "auto"), default="strict", help="唯一径路检查或自动参考路径")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--rebuild-index", action="store_true")
    args = parser.parse_args()
    directory = args.directory or active_rail_directory(Path(__file__).resolve().parents[1])
    source, index = directory / "rail.sqlite", directory / "rail_lines.sqlite"
    if args.rebuild_index:
        build_line_index(source, index, [], [])
    if not index_ready(index, fingerprint(source, [])):
        parser.error("线路索引需要更新，请打开通道编排或加 --rebuild-index")
    metadata = json.loads(args.workspace.read_text(encoding="utf-8")) if args.workspace else {}
    report = audit(DiskRailLineLibrary(index, metadata=metadata), args.start, args.line, args.end, args.policy)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
        print(f"报告：{args.report.resolve()}")
    else:
        print(output)
    return 1 if report["corridor"]["status"] == "unresolved" else 0


if __name__ == "__main__":
    raise SystemExit(main())
