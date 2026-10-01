"""Reserved capabilities use existing stable domain IDs and the same repository.

No GUI claims these providers exist until a concrete implementation is installed.
"""

from typing import Protocol


class HistoricalTimetableProvider(Protocol):
    def runs_for(self, repo, service_date: str, source_id: str): ...


class NetworkComparisonProvider(Protocol):
    def compare(self, repo, start_date: str, end_date: str): ...


class OperationalRelationshipProvider(Protocol):
    def relationships(self, repo, section_id: str, service_date: str): ...


class RailwayQueryProvider(Protocol):
    def query(
        self,
        repo,
        *,
        station_id=None,
        line_id=None,
        section_id=None,
        corridor_id=None,
        criteria=None,
    ): ...


class CapacityAnalysisProvider(Protocol):
    def indicators(self, repo, section_id: str, service_date: str, rules): ...


class DelayPropagationProvider(Protocol):
    def forecast(self, repo, scenario_id: str, train_run_id: str, delay_s: int): ...


class RailwaySandboxProvider(Protocol):
    def compare_scenarios(self, repo, scenario_ids: tuple[str, ...]): ...


class RailwayExportProvider(Protocol):
    def export(self, repo, object_id: str, view_kind: str, options, output_path): ...
