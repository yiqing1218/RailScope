from copy import deepcopy
import json
import pytest
from desktop.operating import Plan


def profile():
    return {
        "id": "L",
        "name": "测试线",
        "path": {
            "coordinates": [[121, 31], [121.02, 31]],
            "cumulative": [0, 2000],
            "length_m": 2000,
        },
        "stations": [
            {"id": str(i), "name": str(i), "distance_m": i * 1000} for i in range(3)
        ],
    }


def test_short_turn_cycle_compiles_fleet_and_waits_without_duplicate_vehicle(tmp_path):
    plan = Plan([profile()])
    cycle = plan.make_cycle("L", "short", 0, 1, turnback_s=120)
    trips = plan.generate_fleet(
        cycle,
        [
            {"id": "V1", "start_s": 25200, "end_s": 27000},
            {"id": "V2", "start_s": 25300, "end_s": 27000},
        ],
    )
    assert {t["vehicle_id"] for t in trips} == {"V1", "V2"}
    assert all({s["station_id"] for s in t["stops"]} == {"0", "1"} for t in trips)
    plan.validate()
    first = trips[0]
    waiting = plan.vehicle_positions(first["stops"][-1]["departure_s"] + 1)
    assert next(p for t, p in waiting if t["vehicle_id"] == "V1")["state"] == "折返等待"
    assert len({t["vehicle_id"] for t, p in waiting}) == len(waiting)
    file = tmp_path / "plan.json"
    plan.extensions = {"example.org/overtaking": {"notes": "未来接口，未执行"}}
    plan.save(file)
    restored = Plan([profile()])
    restored.load(file)
    assert restored.cycles == plan.cycles and restored.vehicles == plan.vehicles
    assert restored.extensions == plan.extensions


def test_generation_and_strict_import_are_atomic(tmp_path):
    plan = Plan([profile()])
    cycle = plan.make_cycle("L", "full", 0, 2)
    plan.generate_fleet(cycle, [{"id": "V", "start_s": 0, "end_s": 3600}])
    before = deepcopy(plan.trains)
    with pytest.raises(ValueError):
        plan.generate_fleet(cycle, [{"id": "V", "start_s": 3000, "end_s": 1}])
    assert plan.trains == before
    file = tmp_path / "p.json"
    plan.save(file)
    payload = json.loads(file.read_text(encoding="utf-8"))
    payload["typo"] = 1
    file.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="未知字段"):
        plan.load(file)
    assert plan.trains == before
    plan.save(file)
    payload = json.loads(file.read_text(encoding="utf-8"))
    payload["required_capabilities"] = ["overtaking.v1"]
    file.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="尚不支持"):
        plan.load(file)


def test_overlapping_same_vehicle_and_noncontiguous_stops_rejected():
    plan = Plan([profile()])
    plan.add_train("L", "T", 0)
    train = deepcopy(plan.trains[0])
    train["id"] = "T2"
    train["vehicle_id"] = "V"
    plan.trains[0]["vehicle_id"] = "V"
    with pytest.raises(ValueError, match="重叠"):
        plan.validate([*plan.trains, train])
    train["stops"] = train["stops"][::2]
    with pytest.raises(ValueError, match="站序"):
        plan.validate([train])
