# Architecture

RailScope has four deliberate layers: normalized GIS geometry, explicit
`NetworkNode`/`NetworkEdge` topology, immutable scheduled operations
(`TrainRun`, `StopTime`, `RoutePath`), and scenario-derived operations
(`TrackOccupancy`, `Conflict`, `DispatchEvent`). No layer is represented by a
map line from another layer.

The backend is a modular monolith. `topology` validates graph facts; `routing`
maps station anchors to an edge path; `timetable` derives effective stops;
`blocks`, `occupancy`, `conflicts`, and `dispatch` implement V5 resources and
manual actions. API/UI only orchestrate these services.

The current deterministic repository is an executable demo adapter. SQLAlchemy,
PostGIS and Alembic form the production persistence boundary. Database changes
are migration-only; the demo never requires PostGIS to prove domain behavior.
