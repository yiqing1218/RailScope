"""Conservative railway facts shared by importers, repositories and presentation.

An OSM claim is evidence, not an official railway or interlocking assertion.
Names, neighbouring geometry and display categories cannot establish facts.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import math


RAILWAY_CLASSES = frozenset({"high_speed", "conventional", "freight", "other", "metro", "industrial", "unknown"})
LINE_ROLES = frozenset({"main_line", "branch_line", "connecting_line", "dedicated_line", "industrial_line", "unknown"})
TRACK_ROLES = frozenset({
    "main_track", "arrival_departure_track", "shunting_track", "lead_track", "freight_track",
    "depot_track", "locomotive_running_track", "maintenance_track", "stabling_track",
    "turnback_track", "crossover", "spur_track", "safety_siding", "escape_siding",
    "other_station_track", "unknown",
})
OPERATIONAL_STATUSES = frozenset({"operating", "construction", "planned", "disused", "unknown"})
SEMANTIC_ENUMS = {"railway_class": RAILWAY_CLASSES, "line_role": LINE_ROLES,
                  "track_role": TRACK_ROLES, "construction_status": OPERATIONAL_STATUSES}


def validate_semantic_fields(value: dict) -> None:
    """Validate explicit facts before applying an override or storing an index."""
    for key, allowed in SEMANTIC_ENUMS.items():
        if key in value and (not isinstance(value[key], str) or value[key] not in allowed):
            raise ValueError(f'Invalid {key}: expected a canonical semantic value')
    for key in ("facility_id", "yard_id", "zone_id"):
        if key in value and value[key] is not None and (not isinstance(value[key], str) or not value[key].strip()):
            raise ValueError(f'Invalid {key}: expected a stable ID or null')
    confidence = value.get("confidence")
    if confidence is not None and (type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError('Invalid confidence: expected a number between 0 and 1')


@dataclass(frozen=True)
class Classification:
    value: object
    evidence: str
    verification_status: str = "unverified"
    confidence: float | None = None
    source: str | None = None
    snapshot_id: str | None = None


def _claim_record(claim):
    """Detach evidence without dataclasses.asdict recursively copying scalars."""
    return {key: value if type(value) in (str, int, float, bool, type(None)) else deepcopy(value)
            for key, value in vars(claim).items()}


def _tags(raw):
    for key in ("way_tags", "source_tags", "tags"):
        if isinstance(raw.get(key), dict):
            return raw[key]
    return raw


def _claim(value, evidence, source, snapshot_id, confidence=0.85):
    return Classification(value, evidence, "osm_explicit", confidence, source, snapshot_id)


def _unknown(source, snapshot_id, evidence="Insufficient explicit evidence"):
    return Classification("unknown", evidence, "unverified", None, source, snapshot_id)


def classify_railway_class(tags: dict, *, source="OpenStreetMap", snapshot_id=None) -> Classification:
    tags = _tags(tags)
    if tags.get("railway") in {"subway", "light_rail"}:
        return _claim("metro", "OSM railway=" + tags["railway"], source, snapshot_id)
    if tags.get("highspeed") == "yes":
        return _claim("high_speed", "OSM highspeed=yes", source, snapshot_id)
    if tags.get("usage") == "industrial":
        return _claim("industrial", "OSM usage=industrial", source, snapshot_id)
    if tags.get("usage") == "freight":
        return _claim("freight", "OSM usage=freight", source, snapshot_id)
    if tags.get("highspeed") == "no" and tags.get("railway", "rail") == "rail":
        return _claim("conventional", "OSM highspeed=no", source, snapshot_id)
    return _unknown(source, snapshot_id)


def classify_line_role(tags: dict, *, source="OpenStreetMap", snapshot_id=None) -> Classification:
    tags = _tags(tags)
    role = {"main": "main_line", "branch": "branch_line", "industrial": "industrial_line"}.get(tags.get("usage"))
    if role:
        return _claim(role, "OSM usage=" + tags["usage"], source, snapshot_id)
    return _unknown(source, snapshot_id)


def classify_track_role(tags: dict, *, source="OpenStreetMap", snapshot_id=None) -> Classification:
    tags = _tags(tags)
    service = tags.get("service")
    role = {"crossover": "crossover", "spur": "spur_track"}.get(service)
    if role:
        return _claim(role, "OSM service=" + service, source, snapshot_id)
    if service in {"yard", "siding"}:
        return _unknown(source, snapshot_id, "OSM service=" + service + "; precise track function unknown")
    if tags.get("usage") == "main" and not service:
        return _claim("main_track", "OSM usage=main without service override", source, snapshot_id)
    return _unknown(source, snapshot_id)


def classify_operational_status(tags: dict, *, source="OpenStreetMap", snapshot_id=None) -> Classification:
    tags = _tags(tags)
    railway = tags.get("railway")
    for key, value in (("construction", "construction"), ("proposed", "planned"),
                       ("planned", "planned"), ("disused", "disused"), ("abandoned", "disused")):
        if railway == key or tags.get(key + ":railway") or tags.get(key) in {"yes", "rail", "subway", "light_rail"}:
            return _claim(value, "OSM lifecycle=" + key, source, snapshot_id)
    if railway in {"rail", "subway", "light_rail", "tram", "narrow_gauge", "monorail"}:
        return _claim("operating", "OSM railway=" + railway, source, snapshot_id, 0.75)
    return _unknown(source, snapshot_id)


def classify_facility_context(edge: dict, *, source="OpenStreetMap", snapshot_id=None) -> Classification:
    # IDs must have been resolved to canonical objects by the repository adapter.
    value = {key: edge.get(key) or None for key in ("facility_id", "yard_id", "zone_id")}
    known = any(value.values())
    return Classification(value, "Explicit canonical facility references" if known else "No resolved facility references",
                          edge.get("verification_status", "unverified") if known else "unverified",
                          edge.get("confidence") if known else None, source, snapshot_id)


def legacy_semantics(track_type: str) -> dict:
    """Compatibility hints only: old composite labels included name inference."""
    railway_class = {
        "高速铁路线": "high_speed", "高速铁路站场股道": "high_speed",
        "普速铁路线": "conventional", "普速铁路站场股道": "conventional",
        "货运铁路线": "freight", "货运站场股道": "freight",
        "metro": "metro", "地铁": "metro",
    }.get(track_type, "unknown")
    # Old branch/crossover/depot labels were also inferred from names: retain
    # the label in evidence, but never turn it into a professional fact.
    result = {"railway_class": railway_class, "line_role": "unknown", "track_role": "unknown"}
    result["provenance"] = {
        key: _claim_record(Classification(value, "Legacy track_type=" + str(track_type),
                                   "inferred" if value != "unknown" else "unverified",
                                   0.3 if value != "unknown" else None, "legacy_migration", None))
        for key, value in result.items()
    }
    return result


def edge_semantics(edge: dict, *, include_provenance=True) -> dict:
    """Read canonical facts, optionally projecting values for read-only filters.

    Both modes use the same validation and classifiers. Persisted/domain DTOs
    retain detached provenance by default; value projections never mutate it.
    """
    validate_semantic_fields(edge)
    has_source_tags = any(isinstance(edge.get(k), dict) for k in ("way_tags", "source_tags", "tags")) or any(k in edge for k in ("railway", "usage", "service", "highspeed"))
    source = edge.get("source_id") or edge.get("source") or ("OpenStreetMap" if has_source_tags else "saved_canonical")
    snapshot_id = edge.get("snapshot_id") or edge.get("source_version")
    tags = _tags(edge)
    classifiers = {"railway_class": classify_railway_class, "line_role": classify_line_role,
                   "track_role": classify_track_role, "construction_status": classify_operational_status}
    enums = SEMANTIC_ENUMS
    stored_provenance = edge.get("provenance") or {}
    if not isinstance(stored_provenance, dict) or any(not isinstance(v, dict) for v in stored_provenance.values()):
        raise ValueError('Invalid provenance: expected per-attribute dictionaries')
    provenance = deepcopy(stored_provenance) if include_provenance else dict(stored_provenance)
    result = {}
    old = None
    for key, classifier in classifiers.items():
        claim = classifier(tags, source=source, snapshot_id=snapshot_id)
        if key in edge and edge[key] in enums[key]:
            claim = Classification(edge[key], "Saved canonical attribute", edge.get("verification_status", "unverified"),
                                   edge.get("confidence"), source, snapshot_id)
            stored = stored_provenance.get(key)
            if isinstance(stored, dict):
                if "value" in stored and stored["value"] != edge[key]:
                    raise ValueError(f'Conflicting {key} value and provenance')
        # Only decode a label when no raw tags remain. Raw evidence wins over
        # historical name-derived guesses, even if that evidence says unknown.
        elif claim.value == "unknown" and key != "construction_status" and not has_source_tags:
            if old is None:
                old = legacy_semantics(edge.get("track_type") or edge.get("railway_type") or "")
            claim = Classification(**old["provenance"][key])
        elif key == "construction_status" and claim.value == "unknown" and type(edge.get("construction")) is bool:
            claim = Classification("construction" if edge["construction"] else "operating",
                                   "Legacy DTO construction Boolean", "inferred", 0.5, "legacy_migration", snapshot_id)
        result[key] = claim.value
        if key not in provenance:
            provenance[key] = _claim_record(claim) if include_provenance else {
                'verification_status': claim.verification_status, 'confidence': claim.confidence}
    facility = classify_facility_context(edge, source=source, snapshot_id=snapshot_id)
    for key, value in facility.value.items():
        result[key] = value
        stored = stored_provenance.get(key)
        if not isinstance(stored, dict):
            provenance[key] = {**_claim_record(facility), "value": value} if include_provenance else {
                'verification_status': facility.verification_status, 'confidence': facility.confidence}
    result["verification_status"] = edge.get("verification_status") or (
        "osm_explicit" if any(p.get("verification_status") == "osm_explicit" for p in provenance.values()) else "unverified")
    known_confidences = [p.get("confidence") for p in provenance.values() if p.get("confidence") is not None]
    result["confidence"] = edge.get("confidence", min(known_confidences) if known_confidences else None)
    if include_provenance:
        result["provenance"] = provenance
    return result
