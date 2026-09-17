# RailScope V0 + V1 + V5 plan

Version scope follows the current request.  The master specification remains the
architecture, data, GIS, topology, and engineering baseline; its V2/V3 product
scope is intentionally not implemented in this iteration.

## Phase 0 — engineering foundation

- [x] Create monorepo layout and shared development commands.
- [x] Add FastAPI application, configuration, structured logging, health API,
  Docker/PostGIS compose file, and Alembic baseline.
- [x] Add React/Vite shell and Tauri desktop shell.
- [x] Add a self-contained demo data set.

## Phase 1 — infrastructure GIS and railway topology

- [x] Separate infrastructure geometry, station, topology node, and topology edge models.
- [x] Provide demo and generic GeoJSON import paths plus import reports.
- [x] Build and validate a directed railway topology.
- [x] Provide spatial, detail, layer, search, and source-attribution APIs.
- [x] Build a PMTiles-ready frontend source boundary and inspectable layer UI.

## Phase 2 — V5 minimal operations

- [x] Add TrainRun, StopTime, RoutePath and weighted route matching.
- [x] Add manual route override and deterministic route-distance position interface.
- [x] Add block, block-edge, station-track and headway-rule resources.

## Phase 3 — V5 occupancy, conflicts, and dispatch

- [x] Calculate scenario-scoped occupancies outside API/UI code.
- [x] Detect overlap, opposing movement, headway and station-track conflicts.
- [x] Keep scheduled facts immutable; derive effective operations from dispatch events.
- [x] Add delay, hold, cancel, route-change, track-assignment and reset actions.
- [x] Provide operations UI and API endpoints.

## Phase 4 — verification and handoff

- [x] Run backend unit/API tests (8 passed).
- [x] Install frontend dependencies and run unit tests/build (1 passed; build passed).
- [x] Validate Alembic SQL generation and update documentation/final report.
