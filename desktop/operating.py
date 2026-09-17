"""Editable, explicitly non-official stop-time plans and deterministic positions."""

from copy import deepcopy
import json
import math
from pathlib import Path


def parse_time(value):
    parts = str(value).split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise ValueError("时间格式为 HH:MM[:SS]，支持跨日 24–47 时")
    h, m, s = int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) == 3 else 0
    if not (0 <= h < 48 and 0 <= m < 60 and 0 <= s < 60):
        raise ValueError("时间超出允许范围")
    return h * 3600 + m * 60 + s


def format_time(value):
    value = int(value)
    return f"{value // 3600:02d}:{value % 3600 // 60:02d}:{value % 60:02d}"


class Plan:
    def __init__(self, lines):
        self.lines = {line["id"]: line for line in lines}
        self.trains = []

    def train(self, train_id):
        return next(t for t in self.trains if t["id"] == train_id)

    def validate(self, trains=None):
        trains = self.trains if trains is None else trains
        if not isinstance(trains, list):
            raise ValueError("列车计划必须是数组")
        seen = set()
        for train in trains:
            required = {"id", "line_id", "direction", "stops", "enabled", "source"}
            if not isinstance(train, dict) or not required.issubset(train):
                raise ValueError("列车计划字段不完整")
            if (
                not isinstance(train["line_id"], str)
                or type(train["enabled"]) is not bool
                or not isinstance(train["source"], str)
            ):
                raise ValueError("线路、启用状态或数据来源格式无效")
            name = train["id"]
            if not isinstance(name, str) or not name.strip() or name in seen:
                raise ValueError("车次编号不能为空或重复")
            seen.add(name)
            line = self.lines.get(train["line_id"])
            if not line or not line.get("path"):
                raise ValueError(f"{name}：线路几何不可用")
            direction = train["direction"]
            if direction not in ("forward", "reverse"):
                raise ValueError("方向无效")
            stations = (
                line["stations"] if direction == "forward" else line["stations"][::-1]
            )
            stops = train["stops"]
            if not isinstance(stops, list) or not all(
                isinstance(s, dict)
                and {"station_id", "arrival_s", "departure_s", "distance_m"}.issubset(s)
                for s in stops
            ):
                raise ValueError(f"{name}：站点计划格式无效")
            if [s["station_id"] for s in stops] != [s["id"] for s in stations]:
                raise ValueError(f"{name}：站序与线路不匹配，不能在编辑中改动站序")
            previous = -1
            for stop, station in zip(stops, stations):
                a, d = stop["arrival_s"], stop["departure_s"]
                if any(type(v) is not int or v < 0 or v >= 172800 for v in (a, d)):
                    raise ValueError(f"{name}：到发时刻应在 00:00 至 47:59:59")
                if a > d or (previous >= 0 and a <= previous):
                    raise ValueError(
                        f"{name}：到达必须早于发车，下一站到达必须晚于上一站发车"
                    )
                mileage = stop["distance_m"]
                if (
                    type(mileage) not in (int, float)
                    or not math.isfinite(mileage)
                    or not math.isclose(mileage, station["distance_m"], abs_tol=0.01)
                ):
                    raise ValueError(f"{name}：站点里程与线路不一致")
                previous = d

    def add_train(self, line_id, train_id, start_s, direction="forward"):
        line = self.lines.get(line_id)
        if not line or not line.get("path"):
            raise ValueError("线路几何不可用")
        if direction not in ("forward", "reverse"):
            raise ValueError("方向无效")
        stations = (
            line["stations"] if direction == "forward" else line["stations"][::-1]
        )
        if len(stations) < 2:
            raise ValueError("至少需要两个已关联车站")
        time = int(start_s)
        stops = []
        for index, station in enumerate(stations):
            if index:
                time += max(
                    30,
                    round(
                        abs(station["distance_m"] - stations[index - 1]["distance_m"])
                        / 18
                    ),
                )
            stops.append(
                {
                    "station_id": station["id"],
                    "arrival_s": time,
                    "departure_s": time + 30,
                    "distance_m": station["distance_m"],
                }
            )
            time += 30
        train = {
            "id": train_id.strip(),
            "line_id": line_id,
            "direction": direction,
            "enabled": True,
            "source": "用户可编辑的几何演示计划，非官方时刻表",
            "stops": stops,
        }
        self.validate([*self.trains, train])
        self.trains.append(train)
        return train

    def edit_stop(self, train_id, index, arrival_s, departure_s):
        candidate = deepcopy(self.trains)
        train = next(t for t in candidate if t["id"] == train_id)
        train["stops"][index].update(arrival_s=arrival_s, departure_s=departure_s)
        self.validate(candidate)
        self.trains = candidate

    def shift_train(self, train_id, seconds):
        candidate = deepcopy(self.trains)
        train = next(t for t in candidate if t["id"] == train_id)
        for stop in train["stops"]:
            stop["arrival_s"] += int(seconds)
            stop["departure_s"] += int(seconds)
        self.validate(candidate)
        self.trains = candidate

    def position(self, train_id, time_s):
        train = self.train(train_id)
        stops = train["stops"]
        if (
            not train.get("enabled", True)
            or time_s < stops[0]["arrival_s"]
            or time_s > stops[-1]["departure_s"]
        ):
            return None
        for index, stop in enumerate(stops):
            if stop["arrival_s"] <= time_s <= stop["departure_s"]:
                return {
                    "distance_m": stop["distance_m"],
                    "state": "停站",
                    "station_id": stop["station_id"],
                }
            if index and stops[index - 1]["departure_s"] < time_s < stop["arrival_s"]:
                previous = stops[index - 1]
                fraction = (time_s - previous["departure_s"]) / (
                    stop["arrival_s"] - previous["departure_s"]
                )
                return {
                    "distance_m": previous["distance_m"]
                    + fraction * (stop["distance_m"] - previous["distance_m"]),
                    "state": "区间运行",
                    "station_id": None,
                }
        return None

    def save(self, path):
        self.validate()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "schema": "railscope.operating-plan.v1",
                    "official": False,
                    "trains": self.trains,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)

    def load(self, path):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != "railscope.operating-plan.v1"
            or payload.get("official") is not False
        ):
            raise ValueError("只接受 RailScope 非官方演示计划 v1，不能冒充真实运营数据")
        trains = payload.get("trains")
        if not isinstance(trains, list):
            raise ValueError("列车计划必须是数组")
        self.validate(trains)
        self.trains = deepcopy(trains)
