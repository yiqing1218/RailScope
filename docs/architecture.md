# Architecture

RailScope has four deliberate layers: normalized source geometry, explicit
`NetworkNode`/`NetworkEdge` topology, shared complete directed `Corridor`
paths plus immutable `TrainRun`/`StopTime` schedules, and scenario-derived
operations (`TrackOccupancy`, `Conflict`, `DispatchEvent`). A map line is only
a rendering of one of these facts and never becomes its identity.

The backend is a modular monolith. `topology` validates graph facts; `routing`
maps station anchors to an edge path; `timetable` derives effective stops;
`blocks`, `occupancy`, `conflicts`, and `dispatch` implement V5 resources and
manual actions. API/UI only orchestrate these services.

The Qt desktop workspace, deterministic demo and future PostGIS repository all
adapt to the same `railscope.domain` objects. The desktop keeps nationwide
geometry in viewport stores and materializes only selected domain objects in
its SQLite source/override workspace. Database changes are migration-only; the
demo never requires PostGIS to prove domain behavior.
