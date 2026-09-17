import pytest
import json
from copy import deepcopy
from desktop.operating import Plan, parse_time, format_time


def line():
    return {
        "id": "sh-1",
        "name": "1号线",
        "color": "#ff0000",
        "path": {
            "coordinates": [[0, 0], [0.01, 0]],
            "cumulative": [0, 1000],
            "length_m": 1000,
        },
        "stations": [
            {"id": "a", "name": "A", "distance_m": 0},
            {"id": "b", "name": "B", "distance_m": 1000},
        ],
    }


def test_stop_times_control_dwell_and_motion():
    plan = Plan([line()])
    train = plan.add_train("sh-1", "T1", 25200, "forward")
    plan.edit_stop("T1", 0, 25200, 25230)
    plan.edit_stop("T1", 1, 25330, 25360)
    assert plan.position("T1", 25220)["distance_m"] == 0
    assert plan.position("T1", 25280)["distance_m"] == pytest.approx(500)
    assert plan.position("T1", 25340)["distance_m"] == 1000
    assert plan.position("T1", 25100) is None
    assert train["id"] == "T1"


def test_invalid_edit_is_atomic_and_reverse_train_moves_backwards():
    plan = Plan([line()])
    plan.add_train("sh-1", "T1", 25200, "reverse")
    before = dict(plan.trains[0]["stops"][0])
    with pytest.raises(ValueError):
        plan.edit_stop("T1", 0, 25300, 25200)
    assert plan.trains[0]["stops"][0] == before
    assert plan.position("T1", 25201)["distance_m"] == 1000
    with pytest.raises(ValueError):
        plan.add_train("sh-1", "T1", 25200, "forward")


def test_roundtrip_and_day_extended_times(tmp_path):
    plan = Plan([line()])
    plan.add_train("sh-1", "T1", 25200, "forward")
    file = tmp_path / "plan.json"
    plan.save(file)
    restored = Plan([line()])
    restored.load(file)
    assert restored.trains == plan.trains
    assert parse_time("25:01:03") == 90063
    assert format_time(90063) == "25:01:03"
    with pytest.raises(ValueError):
        parse_time("07:60")


def test_shift_updates_all_stops_and_rejects_invalid_shift():
    plan = Plan([line()])
    plan.add_train("sh-1", "T1", 25200)
    before = deepcopy(plan.trains)
    plan.shift_train("T1", 120)
    for a, b in zip(before[0]["stops"], plan.trains[0]["stops"]):
        assert b["arrival_s"] == a["arrival_s"] + 120
        assert b["departure_s"] == a["departure_s"] + 120
    shifted = deepcopy(plan.trains)
    with pytest.raises(ValueError):
        plan.shift_train("T1", -30000)
    assert plan.trains == shifted


@pytest.mark.parametrize("bad", [None, {}, [None], [{"id": "T1"}]])
def test_malformed_import_is_rejected_atomically(tmp_path, bad):
    plan = Plan([line()])
    plan.add_train("sh-1", "T1", 25200)
    before = deepcopy(plan.trains)
    file = tmp_path / "bad.json"
    file.write_text(
        json.dumps(
            {"schema": "railscope.operating-plan.v1", "official": False, "trains": bad}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        plan.load(file)
    assert plan.trains == before


def test_nonfinite_distance_and_unknown_line_are_rejected():
    plan = Plan([line()])
    plan.add_train("sh-1", "T1", 25200)
    bad = deepcopy(plan.trains)
    bad[0]["stops"][0]["distance_m"] = float("nan")
    with pytest.raises(ValueError):
        plan.validate(bad)
    with pytest.raises(ValueError):
        plan.add_train("missing", "T2", 25200)
