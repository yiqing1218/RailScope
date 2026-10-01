"""Ephemeral drawing DTOs; infrastructure lives exclusively in railscope.domain."""

from dataclasses import dataclass, field


@dataclass
class Extraction:
    inner: set[str]
    selected: set[str]
    boundary_source: str
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Ownership:
    system: str | None
    yard_id: str | None
    yard_name: str | None
    line_id: str | None
    railway_class: str
    source: str
    status: str = "source_unverified"
    evidence: tuple[str, ...] = ()
    yard_type: str = "unknown"


@dataclass
class Lane:
    id: str
    edge_ids: set[str]
    source_y: float
    y: float = 0
    yard_id: str | None = None
    yard_name: str | None = None
    system: str | None = None
    track_number: str | None = None


@dataclass
class Graph:
    endpoints: dict[str, tuple[str, str]]
    adjacency: dict[str, list[str]]
    components: list[set[str]]

    def other(self, edge, node):
        a, b = self.endpoints[edge]
        return b if node == a else a
