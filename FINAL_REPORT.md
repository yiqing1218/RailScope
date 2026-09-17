# RailScope V0 + V1 + V5 — final report

## Implemented scope

Implemented: engineering/map foundation (V0), infrastructure GIS and railway
topology (V1), plus minimal operations and manual dispatch core (V5). Excluded:
V2/V3 player/animation/diagram products, V4 dynamics, V6 realtime feeds and V7
signalling/interlocking.

## V0

- Monorepo, Compose PostGIS service, FastAPI app, health endpoint, Vite React
  UI and Tauri 2 desktop shell.
- Environment template, Makefile, Alembic baseline and deterministic demo data.

## V1

- Separate geometry/infrastructure, station and explicit topology edge/node
  objects.
- Topology validation, GeoJSON import reporting, OSM importer boundary,
  search/detail/spatial APIs, source attribution API and PMTiles-ready source
  boundary.
- MapLibre map, registry-driven layer controls and inspectable operations UI.

## V5

- TrainRun, StopTime, RoutePath, weighted graph routing and manual route path.
- Virtual block sections, block membership, station tracks and headway rules.
- Scenario-scoped occupancy and detectors for same-block, opposing-direction,
  headway and station-track overlap conflicts.
- Immutable scheduled data with delay, hold, cancel, change-route, assign-track,
  recalculation and reset dispatch events.

## Data flow

`GIS geometry → topology → RoutePath → effective timetable → blocks →
occupancy → conflict → DispatchEvent → recalculation`.

## How to run and verify

Follow [README.md](README.md). The bundled scenario deliberately creates
conflicts among 101, 102 and 201; select a train, apply a dispatch event, and
reset to return to scheduled facts.

## Test results

- Backend: `python -m pytest -q` — **8 passed**.
- Frontend: `npm test -- --run` — **1 passed**.
- Frontend: `npm run build` — **passed**. Vite reports a non-fatal MapLibre
  bundle-size warning (993 kB before gzip).
- Migration: `python -m alembic upgrade head --sql` — **passed**, ending at
  `0002_complete_domain_schema`.
- Demo CLI: **passed** (3 stations, 6 edges, virtual blocks and intentional
  conflicts loaded).

## Reserved interfaces and known limits

`MotionModel`, `TimetableLinearInterpolationModel`, `TrainStateProvider` and
`DispatchSolver` reserve V2/V3, V6 and automated-dispatch boundaries without
implementing those products. The current generic importer validates GeoJSON;
the OSM PBF parser requires the optional GIS dependency and a database-backed
normalization implementation. The Alembic baseline and V5 completion migration
create the formal geometry/topology/operation/resource schema. Docker could not
be verified because Docker Desktop is not installed in this environment.
