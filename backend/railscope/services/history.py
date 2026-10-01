"""Calendar validity; source tags stay unchanged when an opening date is reached."""

from dataclasses import replace
from datetime import date
from typing import Protocol



def validate_lifecycle(value):
    if not value.id or value.mode not in {"rail", "metro", "road", "other"}:
        raise ValueError("Invalid history owner or mode")
    previous = None
    for raw in (value.construction_started, value.opened, value.closed):
        if raw is None:
            continue
        parsed = date.fromisoformat(raw)
        if parsed.isoformat() != raw or previous is not None and parsed < previous:
            raise ValueError("建设、开通、停运日期必须按顺序，格式 YYYY-MM-DD")
        previous = parsed
    return value


def effective_lifecycle(records, entity_id, visited=None):
    value = records.get(entity_id)
    if value is None:
        return None
    visited = set() if visited is None else set(visited)
    if entity_id in visited:
        raise ValueError("lifecycle inheritance cycle")
    visited.add(entity_id)
    if value.parent_id and value.parent_id not in records:
        raise ValueError("lifecycle parent missing")
    parent = (
        effective_lifecycle(records, value.parent_id, visited)
        if value.parent_id
        else None
    )
    if parent:
        if parent.mode != value.mode:
            raise ValueError("lifecycle parent mode mismatch")
        value = replace(
            value,
            **{
                field: getattr(value, field) or getattr(parent, field)
                for field in ("construction_started", "opened", "closed")
            },
        )
    return validate_lifecycle(value)


def lifecycle_state(value, as_of=None):
    """Half-open intervals; no end date means an unbounded future ray."""
    day = date.fromisoformat(as_of) if as_of else date.today()
    if value is None:
        return "unknown"
    validate_lifecycle(value)
    if value.closed and day >= date.fromisoformat(value.closed):
        return "disused"
    if value.opened and day >= date.fromisoformat(value.opened):
        return "operating"
    if value.construction_started and day < date.fromisoformat(
        value.construction_started
    ):
        return "absent"
    if (
        value.opened
        and not value.construction_started
        and day < date.fromisoformat(value.opened)
    ):
        return "unknown"
    if value.construction_started:
        return "construction"
    return "unknown"


class HistorySnapshotProvider(Protocol):
    def geometry_at(self, entity_id: str, as_of: str) -> dict | None: ...
    def changes_between(self, start: str, end: str) -> tuple: ...
