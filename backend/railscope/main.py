from __future__ import annotations
import logging
from uuid import uuid4
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from .demo import load_demo
from .domain import DispatchEvent, DispatchScenario
from .repository import RailRepository
from .services.dispatch import add_event, recalculate, reset
from .services.simulation import TimetableLinearInterpolationModel
from .services.topology import validate_topology

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO)
app = FastAPI(title="RailScope API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["*"], allow_headers=["*"])
repo: RailRepository = load_demo()


@app.exception_handler(ValueError)
async def domain_error(_, exc: ValueError):
    return JSONResponse(status_code=400, content={"error": {"code": "DOMAIN_ERROR", "message": str(exc), "details": {}}})


def get_or_404(items: dict, identifier: str):
    if identifier not in items:
        raise HTTPException(404, {"error": {"code": "NOT_FOUND", "message": f"Unknown id: {identifier}", "details": {}}})
    return items[identifier]


class EventRequest(BaseModel):
    scenario_id: str = "base-2026-09-15"
    train_run_id: str
    seconds: int | None = Field(default=None, ge=0)
    station_id: str | None = None
    route_path_id: str | None = None
    station_track_id: str | None = None
    reason: str | None = None


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "database": "demo-in-memory", "version": "0.1.0"}


@app.get("/api/v1/sources")
def sources(): return list(repo.sources.values())

@app.get("/api/v1/stations")
def stations(q: str | None = None):
    return [s for s in repo.stations.values() if not q or q.lower() in s.name.lower()]


@app.get("/api/v1/stations/{station_id}")
def station(station_id: str): return get_or_404(repo.stations, station_id)


@app.get("/api/v1/network/edges")
def edges(bbox: str | None = None):
    # The API is for local/debug use. PMTiles remains the nationwide rendering boundary.
    if bbox:
        min_lon, min_lat, max_lon, max_lat = map(float, bbox.split(","))
        if (max_lon-min_lon) * (max_lat-min_lat) > 25:
            raise HTTPException(400, "bbox area exceeds debug API limit")
        return [e for e in repo.edges.values() if any(min_lon <= x <= max_lon and min_lat <= y <= max_lat for x, y in e.coordinates)]
    return list(repo.edges.values())


@app.get("/api/v1/network/edges/{edge_id}")
def edge(edge_id: str): return get_or_404(repo.edges, edge_id)


@app.get("/api/v1/topology/validation")
def topology_validation(): return validate_topology(repo)


@app.get("/api/v1/search")
def search(q: str):
    query = q.lower()
    results = [{"type": "station", "id": s.id, "title": s.name, "subtitle": "Railway station"} for s in repo.stations.values() if query in s.name.lower()]
    results += [{"type": "train_run", "id": t.id, "title": t.train_number, "subtitle": t.service_date} for t in repo.train_runs.values() if query in t.train_number.lower()]
    results += [{"type": "infrastructure_line", "id": l.id, "title": l.name} for l in repo.lines.values() if query in l.name.lower()]
    return results


@app.get("/api/v1/train-runs")
def train_runs(): return list(repo.train_runs.values())


@app.get("/api/v1/train-runs/{train_id}")
def train_run(train_id: str): return get_or_404(repo.train_runs, train_id)


@app.get("/api/v1/train-runs/{train_id}/stops")
def stops(train_id: str): return repo.stops_for(train_id)


@app.get("/api/v1/route-paths/{route_id}")
def route(route_id: str): return get_or_404(repo.routes, route_id)


@app.get("/api/v1/blocks")
def blocks(): return list(repo.blocks.values())


@app.get("/api/v1/blocks/{block_id}")
def block(block_id: str): return get_or_404(repo.blocks, block_id)


@app.get("/api/v1/blocks-geojson")
def block_geojson():
    members = {member.block_id: member.edge_id for member in repo.block_edges}
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": block.id, "name": block.name},
         "geometry": {"type": "LineString", "coordinates": repo.edges[members[block.id]].coordinates}}
        for block in repo.blocks.values() if block.id in members
    ]}


@app.get("/api/v1/occupancies")
def occupancies(scenario_id: str = "base-2026-09-15"):
    return [o for o in repo.occupancies if o.scenario_id == scenario_id]


@app.get("/api/v1/conflicts")
def conflicts(scenario_id: str = "base-2026-09-15"):
    return [c for c in repo.conflicts if c.scenario_id == scenario_id]


@app.get("/api/v1/conflicts/{conflict_id}")
def conflict(conflict_id: str):
    conflict = next((c for c in repo.conflicts if c.id == conflict_id), None)
    if conflict is None: raise HTTPException(404, "conflict not found")
    return conflict


@app.get("/api/v1/scenarios")
def scenarios(): return list(repo.scenarios.values())


class ScenarioRequest(BaseModel):
    name: str
    service_date: str = "2026-09-15"


@app.post("/api/v1/scenarios")
def create_scenario(request: ScenarioRequest):
    scenario = DispatchScenario(f"scenario-{uuid4()}", request.name, request.service_date)
    repo.scenarios[scenario.id] = scenario
    recalculate(repo, scenario.id)
    return scenario


@app.get("/api/v1/dispatch/events")
def events(scenario_id: str = "base-2026-09-15"): return repo.events_for(scenario_id)


def dispatch(request: EventRequest, event_type: str, value: object | None):
    return add_event(repo, DispatchEvent(f"event-{uuid4()}", request.scenario_id, request.train_run_id, event_type, new_value=value, reason=request.reason))


@app.post("/api/v1/dispatch/delay")
def delay(request: EventRequest): return dispatch(request, "delay_train", request.seconds)


@app.post("/api/v1/dispatch/hold")
def hold(request: EventRequest):
    if request.station_id is None or request.seconds is None: raise ValueError("hold requires station_id and seconds")
    return dispatch(request, "hold_train", (request.station_id, request.seconds))


@app.post("/api/v1/dispatch/cancel")
def cancel(request: EventRequest): return dispatch(request, "cancel_train", True)


@app.post("/api/v1/dispatch/change-route")
def change_route(request: EventRequest):
    if request.route_path_id not in repo.routes: raise ValueError("change route requires an existing route_path_id")
    return dispatch(request, "change_route", request.route_path_id)


@app.post("/api/v1/dispatch/assign-track")
def assign_track(request: EventRequest):
    if request.station_id is None or request.station_track_id not in repo.station_tracks: raise ValueError("assign track requires station_id and existing station_track_id")
    return dispatch(request, "assign_track", (request.station_id, request.station_track_id))


@app.post("/api/v1/dispatch/recalculate")
def recalculate_endpoint(scenario_id: str = "base-2026-09-15"): return recalculate(repo, scenario_id)


@app.post("/api/v1/dispatch/reset")
def reset_endpoint(scenario_id: str = "base-2026-09-15"): return reset(repo, scenario_id)


@app.get("/api/v1/simulation/position/{train_id}")
def position(train_id: str, time_s: int, scenario_id: str = "base-2026-09-15"):
    return TimetableLinearInterpolationModel().position_at_time(repo, scenario_id, train_id, time_s)
