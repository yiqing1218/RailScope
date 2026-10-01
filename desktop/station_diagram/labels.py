"""Convergence labels placed outside the platform core with collision checks."""

import math


def convergence_labels(repo, graph, drawings, ports, interior, options):
    cl, ct, cr, cb = interior.core
    left, top, right, bottom = interior.frame
    size = options.label_size * 0.7
    result = []
    boxes = []
    for port in sorted(ports, key=lambda p: (p["side"], p["point"], p["key"])):
        if not port["visible"]:
            continue
        key = min(port["edge_ids"])
        node = next(n for n in graph.endpoints[key] if n in port["nodes"])
        # Follow a real mainline chain inward to the first topology decision.
        visited = set()
        while key not in visited:
            visited.add(key)
            other = graph.other(key, node)
            if len(graph.adjacency[other]) != 2:
                break
            following = next(k for k in graph.adjacency[other] if k != key)
            if (
                repo.edges[following].infrastructure_line_id
                != repo.edges[key].infrastructure_line_id
            ):
                break
            node, key = other, following
        side = port["side"]
        p = interior.nodes[other]
        fs = size
        half = len(port["line"].name) * fs * 0.5
        if side in ("left", "right"):
            lower, upper = (
                (left + half + 20, cl - half - 12)
                if side == "left"
                else (cr + half + 12, right - half - 20)
            )
            if lower > upper:
                fs *= 0.8
                half = len(port["line"].name) * fs * 0.5
                lower, upper = (
                    (left + half + 12, cl - half - 8)
                    if side == "left"
                    else (cr + half + 8, right - half - 12)
                )
            x = max(lower, min(upper, p[0]))
            y = p[1] - 18
        else:
            x = max(left + half, min(right - half, p[0]))
            y = ct - 45 if side == "top" else cb + 35
        # Prefer a small vertical displacement. This changes annotation only;
        # node positions and paths have already been resolved.
        candidates = [
            (x, y + delta)
            for delta in (0, -size * 1.6, size * 1.6, -size * 3.2, size * 3.2)
        ]

        def box(q):
            return (q[0] - half - 6, q[1] - fs, q[0] + half + 6, q[1] + 5)

        def overlaps(a, b):
            return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]

        chosen = next(
            (
                q
                for q in candidates
                if top + fs <= q[1] <= bottom - 5
                and not any(overlaps(box(q), b) for b in boxes)
            ),
            candidates[0],
        )
        # The label stays at the convergence region, away from edge-port text.
        if any(
            l["line_id"] == port["line"].id and math.dist(l["raw_point"], chosen) < 80
            for l in result
        ):
            continue
        boxes.append(box(chosen))
        result.append(
            {
                "line_id": port["line"].id,
                "text": port["line"].name,
                "point": interior.orient(chosen),
                "raw_point": chosen,
                "edge_id": key,
                "font_size": fs,
                "convergence_node": other,
            }
        )
    return result
