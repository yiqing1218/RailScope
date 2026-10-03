"""Named railway termini for presentation, separate from operating topology.

Explicit workspace/OSM route termini take priority over sourced contiguous
railway sections. Nominal fallback cities never extend or certify a Corridor.
"""

import re
from dataclasses import replace


def scope_terminals(scopes):
    """Only contiguous explicit source sections establish whole-line termini."""
    pairs = []
    for scope in scopes:
        parts = re.split(r"\s*[~～]\s*", str(scope.get("span", "")))
        if len(parts) != 2 or not all(parts):
            return None
        pairs.append(tuple(parts))
    if not pairs or any(a[1] != b[0] for a, b in zip(pairs, pairs[1:])):
        return None
    return pairs[0][0], pairs[-1][1]


def terminal_key(name):
    return re.sub(r"(?:铁路|线)$", "", name).replace("高铁", "高速")


def source_terminals(name):
    try:
        from .china_emu import load_store
    except ImportError:
        from china_emu import load_store
    hits = [
        p
        for p in load_store().profiles
        if p["kind"] == "line" and terminal_key(p["name"]) == terminal_key(name)
    ]
    values = [(scope_terminals(p.get("scopes", ())), p) for p in hits]
    values = [(pair, p) for pair, p in values if pair]
    if len({pair for pair, _ in values}) != 1:
        return None
    pair, profile = values[0]
    return pair, {
        k: profile.get(k)
        for k in (
            "source",
            "source_url",
            "snapshot_id",
            "verification_status",
            "confidence",
        )
    }


def integrate_terminals(repo):
    """Populate canonical line facts below manual facts; never rewrite GIS."""
    for ident, line in list(repo.lines.items()):
        facts = line.provenance.get("workspace_presentation", {}).get(
            "technical_attributes", {}
        )
        reference = source_terminals(line.name)
        start = (
            facts.get("start_terminal")
            or line.start_terminal
            or (reference[0][0] if reference else None)
        )
        end = (
            facts.get("end_terminal")
            or line.end_terminal
            or (reference[0][1] if reference else None)
        )
        proof = (
            {"source": "workspace_override"}
            if facts.get("start_terminal") or facts.get("end_terminal")
            else reference[1]
            if reference
            else None
        )
        if start or end:
            proof = dict(proof or line.provenance.get("terminal_reference", {}))
            field_proofs = {}
            for key in ("start_terminal", "end_terminal"):
                if facts.get(key):
                    field_proofs[key] = {
                        "source": "workspace_override",
                        "verification_status": "user_provided",
                    }
                elif getattr(line, key):
                    field_proofs[key] = (
                        line.provenance.get("terminal_reference", {})
                        .get("fields", {})
                        .get(key, {"source": "canonical_line_fact"})
                    )
                elif reference:
                    field_proofs[key] = reference[1]
            proof["fields"] = field_proofs
            repo.lines[ident] = replace(
                line,
                start_terminal=start,
                end_terminal=end,
                provenance={
                    **line.provenance,
                    **({"terminal_reference": proof} if proof else {}),
                },
            )


# Coordinates orient city labels in the diagram, never create infrastructure.
REFERENCE_TERMINALS = {
    "京沪": (
        ("北京", (116.4, 39.9)),
        ("上海", (121.47, 31.23)),
        "user_requested_nominal_cities",
    ),
    "京雄": (
        ("北京", (116.4, 39.9)),
        ("雄安", (116.16, 39.05)),
        "https://www.xiongan.gov.cn/2019-09/21/c_1210288167.htm",
    ),
    "京津": (
        ("北京", (116.4, 39.9)),
        ("天津", (117.2, 39.1)),
        "https://www.nra.gov.cn/ztzl/hy/gsgt/",
    ),
    "雄忻": (
        ("雄安", (116.16, 39.05)),
        ("忻州", (112.73, 38.42)),
        "https://www.xiongan.gov.cn/2022-10/02/c_1211689867.htm",
    ),
    "京港": (
        ("北京", (116.4, 39.9)),
        ("香港", (114.17, 22.3)),
        "https://www.ndrc.gov.cn/xxgk/zcfb/ghwb/201607/W020190905497828820842.pdf",
    ),
}


def nominal_terminals(name):
    """Match a complete railway name, not a connector containing its prefix."""
    for prefix, value in REFERENCE_TERMINALS.items():
        if re.fullmatch(prefix + r"(?:高速|高铁|城际)?(?:铁路|线)?", name):
            return value
    return None
