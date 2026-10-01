"""Vehicle duties are a reverse view of TrainRun.vehicle_id, not another schedule."""

from datetime import date


def validate_vehicle(value):
    if not value.id or not value.name.strip() or value.mode not in {"rail", "metro"}:
        raise ValueError("车辆编号、名称或制式无效")
    if value.photo_asset and (
        ".." in value.photo_asset.replace("\\", "/").split("/")
        or value.photo_asset.startswith(("/", "\\"))
        or ":" in value.photo_asset
    ):
        raise ValueError("车辆图片必须是工作区内的相对资产引用")
    return value


def vehicle_duties(repo, vehicle_id):
    if vehicle_id not in repo.vehicles:
        raise ValueError("未知车辆")
    duties = []
    for run in repo.train_runs.values():
        if run.vehicle_id != vehicle_id:
            continue
        if run.traffic_type not in {"unknown", "passenger", "freight", "mixed"}:
            raise ValueError(f"车次 {run.id} 客货类型无效")
        stops = repo.stops_for(run.id)
        if not stops:
            continue
        first, last = stops[0], stops[-1]
        start = (
            first.departure_time_s
            if first.departure_time_s is not None
            else first.arrival_time_s
        )
        end = (
            last.arrival_time_s
            if last.arrival_time_s is not None
            else last.departure_time_s
        )
        if start is None or end is None or end < start:
            raise ValueError("车辆担当车次时刻不完整或倒退")
        origin = date.fromisoformat(run.service_date).toordinal() * 86400
        duties.append(
            {
                "vehicle_id": vehicle_id,
                "train_run_id": run.id,
                "train_number": run.train_number,
                "service_date": run.service_date,
                "start_s": start,
                "end_s": end,
                "absolute_start_s": origin + start,
                "absolute_end_s": origin + end,
                "origin_station_id": run.origin_station_id,
                "destination_station_id": run.destination_station_id,
            }
        )
    return tuple(
        sorted(duties, key=lambda v: (v["absolute_start_s"], v["train_run_id"]))
    )


def validate_vehicle_assignments(repo):
    for run in repo.train_runs.values():
        if run.traffic_type not in {"unknown", "passenger", "freight", "mixed"}:
            raise ValueError(f"车次 {run.id} 客货类型无效")
        if run.vehicle_id and run.vehicle_id not in repo.vehicles:
            raise ValueError(f"车次 {run.id} 引用未知车辆")
        if (
            run.vehicle_id
            and run.origin_station_id in repo.stations
            and repo.vehicles[run.vehicle_id].mode
            != repo.stations[run.origin_station_id].mode
        ):
            raise ValueError(f"车次 {run.id} 与车辆制式不一致")
    for vehicle in repo.vehicles.values():
        validate_vehicle_assignment(repo, vehicle.id)
    return True


def validate_vehicle_assignment(repo, vehicle_id):
    vehicle = validate_vehicle(repo.vehicles[vehicle_id])
    for run in repo.train_runs.values():
        if (
            run.vehicle_id == vehicle_id
            and run.origin_station_id in repo.stations
            and vehicle.mode != repo.stations[run.origin_station_id].mode
        ):
            raise ValueError(f"车次 {run.id} 与车辆制式不一致")
    duties = vehicle_duties(repo, vehicle_id)
    for previous, current in zip(duties, duties[1:]):
        if previous["absolute_end_s"] > current["absolute_start_s"]:
            raise ValueError(f"vehicle {vehicle_id}: duty overlap")
    return True
