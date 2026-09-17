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
    def __init__(self, lines, system="metro"):
        self.lines = {line["id"]: line for line in lines}
        self.trains = []
        self.system = system
        self.cycles, self.vehicles = [], []
        self.extensions = {}

    def snapshot(self):
        return deepcopy((self.trains, self.cycles, self.vehicles, self.extensions))

    def restore(self, snapshot):
        self.trains, self.cycles, self.vehicles, self.extensions = deepcopy(snapshot)

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
            strict_fields(
                train,
                required,
                {"vehicle_id", "cycle_id", "turnback_s", "extensions"},
                "车次",
            )
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
            ids = [s["id"] for s in stations]
            stop_ids = [s["station_id"] for s in stops]
            if len(stop_ids) < 2 or stop_ids[0] not in ids:
                raise ValueError(f"{name}：站序至少需要两个连续车站")
            offset = ids.index(stop_ids[0])
            stations = stations[offset : offset + len(stops)]
            if stop_ids != [s["id"] for s in stations]:
                raise ValueError(f"{name}：站序与线路不匹配，不能在编辑中改动站序")
            previous = -1
            for stop, station in zip(stops, stations):
                strict_fields(
                    stop,
                    {"station_id", "arrival_s", "departure_s", "distance_m"},
                    {"extensions"},
                    "停站",
                )
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
            if "vehicle_id" in train and (
                not isinstance(train["vehicle_id"], str)
                or not train["vehicle_id"].strip()
            ):
                raise ValueError("车辆编号不能为空")
            if "turnback_s" in train and (
                type(train["turnback_s"]) is not int or train["turnback_s"] < 0
            ):
                raise ValueError("折返时间必须是非负整数秒")
        blocks = {}
        for train in trains:
            if train["enabled"]:
                blocks.setdefault(train.get("vehicle_id", train["id"]), []).append(
                    train
                )
        for vehicle, trips in blocks.items():
            trips.sort(key=lambda t: t["stops"][0]["arrival_s"])
            for previous, following in zip(trips, trips[1:]):
                end, start = previous["stops"][-1], following["stops"][0]
                if start["arrival_s"] < end["departure_s"] + previous.get(
                    "turnback_s", 0
                ):
                    raise ValueError(f"{vehicle}：车次重叠或折返时间不足")
                if end["station_id"] != start["station_id"]:
                    raise ValueError(f"{vehicle}：前后车次端点不相接，不能瞬移")

    def make_cycle(
        self, line_id, cycle_id, first, last, turnback_s=120, dwell_s=30, speed_mps=18
    ):
        line = self.lines.get(line_id)
        if (
            not line
            or not line.get("path")
            or not 0 <= first < last < len(line["stations"])
        ):
            raise ValueError("交路端点必须是该线路上两个不同且有序的车站")
        if (
            type(turnback_s) is not int
            or turnback_s < 1
            or type(dwell_s) is not int
            or dwell_s < 0
            or not math.isfinite(speed_mps)
            or speed_mps <= 0
        ):
            raise ValueError("折返、停站或速度无效")
        selected = line["stations"][first : last + 1]
        legs = []
        for stations in (selected, selected[::-1]):
            stops, clock = [], 0
            for index, station in enumerate(stations):
                if index:
                    clock += max(
                        1,
                        round(
                            abs(
                                station["distance_m"]
                                - stations[index - 1]["distance_m"]
                            )
                            / speed_mps
                        ),
                    )
                stops.append(
                    {
                        "station_id": station["id"],
                        "arrival_s": clock,
                        "departure_s": clock + dwell_s,
                        "distance_m": station["distance_m"],
                    }
                )
                clock += dwell_s
            legs.append(stops)
        cycle = {
            "id": cycle_id,
            "line_id": line_id,
            "outbound": legs[0],
            "inbound": legs[1],
            "turnback_s": turnback_s,
        }
        self.validate_cycle(cycle)
        return cycle

    def validate_cycle(self, cycle):
        strict_fields(
            cycle,
            {"id", "line_id", "outbound", "inbound", "turnback_s"},
            {"extensions"},
            "循环模板",
        )
        if (
            not isinstance(cycle["id"], str)
            or not cycle["id"].strip()
            or type(cycle["turnback_s"]) is not int
            or cycle["turnback_s"] < 1
        ):
            raise ValueError("循环模板编号或折返秒数无效")
        for direction, key in (("forward", "outbound"), ("reverse", "inbound")):
            self.validate(
                [
                    {
                        "id": "template",
                        "line_id": cycle["line_id"],
                        "direction": direction,
                        "stops": cycle[key],
                        "enabled": True,
                        "source": "循环模板",
                    }
                ]
            )
            if cycle[key][0]["arrival_s"] != 0:
                raise ValueError("模板首站相对到达必须为 0 秒")
        if [s["station_id"] for s in cycle["outbound"]] != [
            s["station_id"] for s in cycle["inbound"]
        ][::-1]:
            raise ValueError("往返循环的站序必须互为反向")

    def generate_fleet(self, cycle, vehicles):
        self.validate_cycle(cycle)
        if not isinstance(vehicles, list) or not vehicles or len(vehicles) > 500:
            raise ValueError("车辆数必须在 1–500 之间")
        candidates, seen = [], set()
        duration = (
            cycle["outbound"][-1]["departure_s"]
            + cycle["inbound"][-1]["departure_s"]
            + 2 * cycle["turnback_s"]
        )
        for vehicle in vehicles:
            strict_fields(
                vehicle, {"id", "start_s", "end_s"}, {"extensions"}, "车辆投放"
            )
            if (
                not isinstance(vehicle["id"], str)
                or not vehicle["id"].strip()
                or vehicle["id"] in seen
            ):
                raise ValueError("车辆编号不能为空或重复")
            seen.add(vehicle["id"])
            start, end = vehicle["start_s"], vehicle["end_s"]
            if (
                any(type(v) is not int for v in (start, end))
                or not 0 <= start < end < 172800
            ):
                raise ValueError("车辆投放时间窗无效")
            if end - start < duration:
                raise ValueError(f"{vehicle['id']}：时间窗不足一个完整往返循环")
            clock, number = start, 1
            while clock + duration <= end:
                for direction, key in (("forward", "outbound"), ("reverse", "inbound")):
                    stops = deepcopy(cycle[key])
                    for stop in stops:
                        stop["arrival_s"] += clock
                        stop["departure_s"] += clock
                    candidates.append(
                        {
                            "id": f"{vehicle['id']}/{number:04d}",
                            "vehicle_id": vehicle["id"],
                            "cycle_id": cycle["id"],
                            "line_id": cycle["line_id"],
                            "direction": direction,
                            "enabled": True,
                            "source": "用户循环与车辆投放生成，非官方运行图",
                            "turnback_s": cycle["turnback_s"],
                            "stops": stops,
                        }
                    )
                    clock = stops[-1]["departure_s"] + cycle["turnback_s"]
                    number += 1
                if len(candidates) > 20000:
                    raise ValueError("生成车次超过 20000，请缩小范围")
        # Replace this cycle's fleet, keep other lines and other full/short cycles.
        remaining = [t for t in self.trains if t.get("cycle_id") != cycle["id"]]
        self.validate(remaining + candidates)
        new_vehicles = [{**v, "cycle_id": cycle["id"]} for v in vehicles]
        retained = [v for v in self.vehicles if v["cycle_id"] != cycle["id"]]
        if {v["id"] for v in retained} & seen:
            raise ValueError("车辆编号已用于另一交路；跨交路须显式连接径路")
        self.trains = remaining + candidates
        self.cycles = [c for c in self.cycles if c["id"] != cycle["id"]] + [
            deepcopy(cycle)
        ]
        self.vehicles = retained + new_vehicles
        return candidates

    def vehicle_positions(self, time_s):
        blocks = {}
        for train in self.trains:
            if train["enabled"]:
                blocks.setdefault(train.get("vehicle_id", train["id"]), []).append(
                    train
                )
        result = []
        for trips in blocks.values():
            trips.sort(key=lambda t: t["stops"][0]["arrival_s"])
            for index, train in enumerate(trips):
                position = self.position(train["id"], time_s)
                if position:
                    result.append((train, position))
                    break
                if (
                    index + 1 < len(trips)
                    and train["stops"][-1]["departure_s"]
                    < time_s
                    < trips[index + 1]["stops"][0]["arrival_s"]
                ):
                    end = train["stops"][-1]
                    result.append(
                        (
                            train,
                            {
                                "distance_m": end["distance_m"],
                                "station_id": end["station_id"],
                                "state": "折返等待",
                            },
                        )
                    )
                    break
        return result

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
                    "schema": "railscope.operating-plan.v2",
                    "official": False,
                    "system": self.system,
                    "required_capabilities": [],
                    "extensions": self.extensions,
                    "cycles": self.cycles,
                    "vehicles": self.vehicles,
                    "trains": self.trains,
                },
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        # Same strict contract for export and import, including template references.
        try:
            Plan(list(self.lines.values()), self.system).load(temporary)
        except (ValueError, TypeError):
            temporary.unlink(missing_ok=True)
            raise
        temporary.replace(path)

    def load(self, path):
        payload = read_plan(path)
        if (
            not isinstance(payload, dict)
            or payload.get("schema")
            not in ("railscope.operating-plan.v1", "railscope.operating-plan.v2")
            or payload.get("official") is not False
        ):
            raise ValueError("只接受 RailScope 运行计划 v1/v2；不能冒充真实运营数据")
        cycles, vehicles, extensions = [], [], {}
        if payload["schema"].endswith("v2"):
            strict_fields(
                payload,
                {
                    "schema",
                    "official",
                    "system",
                    "trains",
                    "cycles",
                    "vehicles",
                    "extensions",
                    "required_capabilities",
                },
                set(),
                "运行计划",
            )
            if payload["system"] != self.system:
                raise ValueError("国铁和地铁计划须在各自模块导入")
            if (
                not isinstance(payload["required_capabilities"], list)
                or payload["required_capabilities"]
            ):
                raise ValueError("计划要求尚不支持的运行能力；不能静默忽略后执行")
            cycles, vehicles, extensions = (
                payload["cycles"],
                payload["vehicles"],
                payload["extensions"],
            )
            if not isinstance(cycles, list) or not isinstance(vehicles, list):
                raise ValueError("循环和车辆必须是数组")
            validate_extensions(extensions)
            cycle_ids, vehicle_ids = set(), set()
            for cycle in cycles:
                self.validate_cycle(cycle)
                if cycle["id"] in cycle_ids:
                    raise ValueError("循环编号重复")
                cycle_ids.add(cycle["id"])
            for vehicle in vehicles:
                strict_fields(
                    vehicle,
                    {"id", "cycle_id", "start_s", "end_s"},
                    {"extensions"},
                    "车辆",
                )
                if (
                    not isinstance(vehicle["id"], str)
                    or not vehicle["id"].strip()
                    or vehicle["id"] in vehicle_ids
                    or vehicle["cycle_id"] not in cycle_ids
                ):
                    raise ValueError("车辆编号重复或循环引用不存在")
                if (
                    any(type(vehicle[k]) is not int for k in ("start_s", "end_s"))
                    or not 0 <= vehicle["start_s"] < vehicle["end_s"] < 172800
                ):
                    raise ValueError("车辆时间窗无效")
                vehicle_ids.add(vehicle["id"])
        trains = payload.get("trains")
        if not isinstance(trains, list):
            raise ValueError("列车计划必须是数组")
        self.validate(trains)
        for train in trains:
            if "cycle_id" in train:
                vehicle = next(
                    (v for v in vehicles if v["id"] == train.get("vehicle_id")), None
                )
                if (
                    not vehicle
                    or train["cycle_id"] != vehicle["cycle_id"]
                    or not vehicle["start_s"]
                    <= train["stops"][0]["arrival_s"]
                    <= train["stops"][-1]["departure_s"]
                    <= vehicle["end_s"]
                ):
                    raise ValueError("车次循环、车辆引用或投放时间窗无效")
        self.trains = deepcopy(trains)
        self.cycles, self.vehicles, self.extensions = deepcopy(
            (cycles, vehicles, extensions)
        )


def validate_extensions(value):
    if not isinstance(value, dict) or any(
        not isinstance(k, str) or "/" not in k for k in value
    ):
        raise ValueError("扩展必须是命名空间对象，例如 example.org/overtaking")
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as error:
        raise ValueError("扩展必须是有限数值的 JSON 数据") from error


def strict_fields(value, required, optional, title):
    if not isinstance(value, dict) or not required.issubset(value):
        raise ValueError(title + "字段不完整")
    unknown = set(value) - required - optional
    if unknown:
        raise ValueError(title + "未知字段：" + ", ".join(sorted(unknown)))
    if "extensions" in value:
        validate_extensions(value["extensions"])


def read_plan(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("JSON 重复字段：" + key)
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError("JSON 不允许 NaN/Infinity：" + value)

    return json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=pairs,
        parse_constant=nonfinite,
    )
