use super::*;
use lru::LruCache;
use std::{
    num::NonZeroUsize,
    sync::{Arc, Mutex, OnceLock},
};

#[derive(Clone)]
struct Leg {
    reference: EdgeRef,
    track: Track,
    points: Vec<[f64; 2]>,
    cumulative: Vec<f64>,
}
struct Prepared {
    corridor: Corridor,
    legs: Vec<Leg>,
    total: f64,
}
type Paths = Mutex<LruCache<String, Arc<Prepared>>>;
static PATHS: OnceLock<Paths> = OnceLock::new();

fn distance(a: [f64; 2], b: [f64; 2]) -> f64 {
    let h = ((b[1] - a[1]).to_radians() * 0.5).sin().powi(2)
        + a[1].to_radians().cos()
            * b[1].to_radians().cos()
            * ((b[0] - a[0]).to_radians() * 0.5).sin().powi(2);
    12_742_017.6 * h.clamp(0., 1.).sqrt().asin()
}
fn bearing(a: [f64; 2], b: [f64; 2]) -> f64 {
    let d = (b[0] - a[0]).to_radians();
    let (a, b) = (a[1].to_radians(), b[1].to_radians());
    (b.cos() * d.sin())
        .atan2(a.cos() * b.sin() - a.sin() * b.cos() * d.cos())
        .to_degrees()
        .rem_euclid(360.0)
}
fn prepare(
    db: &Connection,
    canonical: &Connection,
    snapshot: &str,
    run: &Run,
) -> Result<Arc<Prepared>> {
    let mut corridor = corridor_for(db, run)?.context("NO_PHYSICAL_PATH · 计划尚未绑定走行通道")?;
    ensure!(
        corridor.snapshot_id == snapshot,
        "DATA_VERSION_CONFLICT · 固定通道属于其他数据包"
    );
    let key = format!(
        "{}:{snapshot}:{}:{}",
        db.path().unwrap_or(""),
        corridor.id,
        corridor.version
    );
    let cache = PATHS.get_or_init(|| Mutex::new(LruCache::new(NonZeroUsize::new(64).unwrap())));
    if let Some(prepared) = cache.lock().unwrap().get(&key).cloned() {
        return Ok(prepared);
    }
    let tracks = validate_corridor(canonical, snapshot, &mut corridor)?;
    let mut legs = Vec::new();
    for (reference, track) in corridor.edge_refs.iter().zip(tracks) {
        let mut points = track.shape.clone();
        if !reference.forward {
            points.reverse();
        }
        let mut cumulative = vec![0.];
        for pair in points.windows(2) {
            cumulative.push(cumulative.last().unwrap() + distance(pair[0], pair[1]));
        }
        let total = *cumulative.last().unwrap();
        ensure!(total > 0., "NO_PHYSICAL_PATH · 零长度原始轨道");
        for value in &mut cumulative {
            *value *= track.meters / total;
        }
        legs.push(Leg {
            reference: reference.clone(),
            track,
            points,
            cumulative,
        });
    }
    let total = legs.last().unwrap().reference.end_distance_m;
    let prepared = Arc::new(Prepared {
        corridor,
        legs,
        total,
    });
    cache.lock().unwrap().put(key, prepared.clone());
    Ok(prepared)
}
fn line(path: &Prepared) -> Value {
    if path.legs.iter().map(|leg| leg.points.len()).sum::<usize>() > 150_000 {
        return Value::Null;
    }
    let points = path
        .legs
        .iter()
        .enumerate()
        .flat_map(|(i, part)| part.points.iter().skip(usize::from(i > 0)))
        .collect::<Vec<_>>();
    json!({"type":"LineString","coordinates":points})
}
fn locate(path: &Prepared, cumulative: f64) -> (&Leg, f64, [f64; 2], f64) {
    let distance = cumulative.clamp(0., path.total);
    let leg = &path.legs[path
        .legs
        .partition_point(|f| f.reference.end_distance_m < distance)
        .min(path.legs.len() - 1)];
    let offset = (distance - leg.reference.start_distance_m).clamp(0., leg.track.meters);
    let index = leg
        .cumulative
        .partition_point(|value| *value <= offset)
        .saturating_sub(1)
        .min(leg.points.len() - 2);
    let (a, b) = (leg.points[index], leg.points[index + 1]);
    let span = leg.cumulative[index + 1] - leg.cumulative[index];
    let scale = if span > 0. {
        (offset - leg.cumulative[index]) / span
    } else {
        0.
    };
    (
        leg,
        offset,
        [a[0] + (b[0] - a[0]) * scale, a[1] + (b[1] - a[1]) * scale],
        bearing(a, b),
    )
}

#[derive(Clone, Copy)]
struct Timing {
    arrival: f64,
    departure: f64,
    meters: f64,
    passes: bool,
}
#[derive(Clone, Copy)]
struct Travel {
    from: Timing,
    to: Timing,
}
impl Travel {
    fn curve(self, f: f64, eased: bool) -> (f64, f64) {
        let f = f.clamp(0., 1.);
        let start = if eased && !self.from.passes { 0.18 } else { 0. };
        let end = if eased && !self.to.passes { 0.18 } else { 0. };
        let area = 1.0 - (start + end) * 0.5;
        if f < start {
            (f * f / (2. * start * area), f / (start * area))
        } else if f > 1. - end {
            (
                1. - (1. - f).powi(2) / (2. * end * area),
                (1. - f) / (end * area),
            )
        } else {
            ((f - start * 0.5) / area, 1. / area)
        }
    }
    fn time_at(self, meters: f64, eased: bool) -> f64 {
        let span = self.to.meters - self.from.meters;
        if span <= 0. {
            return self.from.departure;
        }
        let target = ((meters - self.from.meters) / span).clamp(0., 1.);
        let (mut left, mut right) = (0., 1.);
        for _ in 0..26 {
            let middle = (left + right) * 0.5;
            if self.curve(middle, eased).0 < target {
                left = middle;
            } else {
                right = middle;
            }
        }
        self.from.departure + (self.to.arrival - self.from.departure) * (left + right) * 0.5
    }
}

fn timing(
    path: &Prepared,
    run: &Run,
    stock: Option<&StockType>,
    offset: f64,
    eased: bool,
) -> Result<Vec<Timing>> {
    let mut result: Vec<Timing> = Vec::new();
    let mut delay = offset;
    for stop in &run.stops {
        let meters = stop_distance(&path.corridor, stop)
            .context("MISSING_STOP_ANCHOR · 至少一个停站尚未设置通道边与偏移")?;
        let a = stop
            .arrival_time_s
            .or(stop.departure_time_s)
            .context("MISSING_TIMETABLE · 缺少到发时刻")?;
        let departure = stop.departure_time_s.unwrap_or(a) as f64;
        let mut arrival = a as f64 + delay;
        if let Some(previous) = result.last() {
            let delta = meters - previous.meters;
            ensure!(delta >= 0., "DIRECTION_CONFLICT · 停站锚点逆序");
            if eased && delta > 0. {
                let default = if path.legs[0].track.kind == "metro" {
                    80.0
                } else {
                    120.0
                };
                let stock_cap = stock.and_then(|value| value.max_speed_kmh);
                let limits = path
                    .legs
                    .iter()
                    .filter(|p| {
                        p.reference.start_distance_m < meters
                            && p.reference.end_distance_m > previous.meters
                    })
                    .map(|p| p.track.max_speed.unwrap_or(default))
                    .fold(f64::INFINITY, f64::min);
                let limit = stock_cap.map_or(limits, |v| v.min(limits)) / 3.6;
                let area = 1.0
                    - 0.09 * f64::from(u8::from(!previous.passes) + u8::from(!stop.pass_through));
                let feasible = previous.departure + delta / (limit * area);
                if feasible > arrival {
                    delay += feasible - arrival;
                    arrival = feasible;
                }
            }
            ensure!(
                delta == 0. || arrival > previous.departure,
                "INVALID_TIMETABLE · 有走行距离的区间缺少有效运行时长"
            );
        }
        result.push(Timing {
            arrival,
            departure: departure + delay,
            meters,
            passes: stop.pass_through,
        });
    }
    Ok(result)
}

pub(super) fn preview_run(
    db: &Connection,
    canonical: &Connection,
    snapshot: &str,
    run: &Run,
    time: f64,
) -> Result<Value> {
    ensure!(
        (2..=2500).contains(&run.stops.len()) && (0.0..=604800.0).contains(&time),
        "时刻预览需要 2–2500 个停站及有效时间"
    );
    let path = prepare(db, canonical, snapshot, run)?;
    let service = service_for(db, &run.service_id)?;
    ensure!(
        path.legs[0].track.kind == service.mode,
        "运行制式与通道不一致"
    );
    let mut previous = 0;
    for stop in &run.stops {
        let arrival = stop
            .arrival_time_s
            .or(stop.departure_time_s)
            .context("停站缺少时刻")?;
        let departure = stop.departure_time_s.unwrap_or(arrival);
        ensure!(
            arrival >= previous
                && (arrival..=604800).contains(&departure)
                && (!stop.pass_through || arrival == departure),
            "草稿时序有误"
        );
        previous = departure;
    }
    let stops = timing(&path, run, None, 0., false)?;
    Ok(sample(run, &path, &stops, time, false))
}

fn sample(run: &Run, path: &Prepared, stops: &[Timing], now: f64, eased: bool) -> Value {
    let first = stops.first().unwrap();
    let last = stops.last().unwrap();
    let i = stops.partition_point(|f| f.arrival <= now);
    let (position, speed, status) = if i == 0 {
        (first.meters, 0., "scheduled")
    } else if now > last.departure {
        (last.meters, 0., "finished")
    } else {
        let previous = stops[i - 1];
        if now <= previous.departure || i == stops.len() {
            (previous.meters, 0., "stopped")
        } else {
            let next = stops[i];
            let duration = next.arrival - previous.departure;
            let (p, v) = Travel {
                from: previous,
                to: next,
            }
            .curve((now - previous.departure) / duration, eased);
            let span = next.meters - previous.meters;
            (previous.meters + p * span, v * span / duration, "moving")
        }
    };
    let (leg, offset, point, heading) = locate(path, position);
    json!({"run_id":run.id,"time_s":now,"edge_id":leg.reference.edge_id,"edge_sequence":leg.reference.sequence,
           "distance_along_edge_m":offset,"distance_m":position,"lon":point[0],"lat":point[1],
           "heading_deg":heading,"speed_mps":speed,"status":status,"position_kind":if eased{"simulated"}else{"planned"},
           "verification_status":path.corridor.verification_status})
}

#[derive(Clone, Serialize)]
struct Occupancy {
    train_run_id: String,
    resource_type: &'static str,
    resource_id: String,
    start_time_s: f64,
    end_time_s: f64,
    direction: Option<bool>,
}
fn occupation(
    run: &Run,
    path: &Prepared,
    stops: &[Timing],
    vehicle: Option<&Trainset>,
    out: &mut Vec<Occupancy>,
) {
    for pair in stops.windows(2) {
        let travel = Travel {
            from: pair[0],
            to: pair[1],
        };
        if travel.from.meters == travel.to.meters {
            continue;
        }
        let start = path
            .legs
            .partition_point(|leg| leg.reference.end_distance_m <= travel.from.meters);
        for leg in path
            .legs
            .iter()
            .skip(start)
            .take_while(|leg| leg.reference.start_distance_m < travel.to.meters)
        {
            let a = travel.from.meters.max(leg.reference.start_distance_m);
            let b = travel.to.meters.min(leg.reference.end_distance_m);
            if b <= a {
                continue;
            }
            out.push(Occupancy {
                train_run_id: run.id.clone(),
                resource_type: "track",
                resource_id: leg.reference.edge_id.clone(),
                start_time_s: travel.time_at(a, true),
                end_time_s: travel.time_at(b, true),
                direction: Some(leg.reference.forward),
            });
        }
    }
    for (stop, timing) in run.stops.iter().zip(stops) {
        let Some(id) = stop
            .station_track_id
            .as_ref()
            .or(stop.stop_edge_id.as_ref())
        else {
            continue;
        };
        if timing.departure > timing.arrival {
            out.push(Occupancy {
                train_run_id: run.id.clone(),
                resource_type: "track",
                resource_id: id.clone(),
                start_time_s: timing.arrival,
                end_time_s: timing.departure,
                direction: None,
            });
        }
    }
    if let Some(vehicle) = vehicle {
        out.push(Occupancy {
            train_run_id: run.id.clone(),
            resource_type: "trainset",
            resource_id: vehicle.id.clone(),
            start_time_s: stops[0].arrival,
            end_time_s: stops.last().unwrap().departure,
            direction: None,
        });
    }
}

fn conflicts(occupancy: &mut [Occupancy], scenario: &Scenario) -> (Vec<Value>, bool) {
    occupancy.sort_by(|a, b| {
        a.resource_type
            .cmp(b.resource_type)
            .then(a.resource_id.cmp(&b.resource_id))
            .then(a.start_time_s.total_cmp(&b.start_time_s))
            .then(a.train_run_id.cmp(&b.train_run_id))
    });
    let mut conflicts = Vec::new();
    for (i, a) in occupancy.iter().enumerate() {
        let interval = f64::from(if a.resource_type == "trainset" {
            scenario.turnaround_s
        } else {
            scenario.headway_s
        });
        for b in occupancy.iter().skip(i + 1).take_while(|b| {
            b.resource_type == a.resource_type
                && b.resource_id == a.resource_id
                && b.start_time_s < a.end_time_s + interval
        }) {
            if a.train_run_id == b.train_run_id {
                continue;
            }
            let overlap = b.start_time_s < a.end_time_s;
            let kind = if a.resource_type == "trainset" {
                "vehicle_turnaround"
            } else if overlap && a.direction != b.direction {
                "opposing_occupation"
            } else if overlap {
                "track_occupation"
            } else {
                "headway"
            };
            if conflicts.len() >= 8192 {
                return (conflicts, true);
            }
            conflicts.push(json!({"train_run_a":a.train_run_id,"train_run_b":b.train_run_id,"resource_type":a.resource_type,
                "resource_id":a.resource_id,"start_time_s":b.start_time_s,"end_time_s":(a.end_time_s+interval).min(b.end_time_s),
                "conflict_type":kind,"severity":if overlap{"conflict"}else{"warning"}}));
        }
    }
    (conflicts, false)
}

fn diagnostic(id: &str, error: impl std::fmt::Display) -> Value {
    let message = error.to_string();
    let (code, rest) = message
        .split_once(" · ")
        .unwrap_or(("OPERATIONS_ERROR", &message));
    json!({"run_id":id,"code":code,"message":rest})
}
pub(super) fn details(
    db: &Connection,
    canonical: &Connection,
    snapshot: &str,
    id: &str,
) -> Result<Value> {
    let run = latest_run(db, id)?;
    let service = service_for(db, &run.service_id)?;
    let corridor = corridor_for(db, &run)?;
    let mut diagnostics = vec![];
    let geometry = match prepare(db, canonical, snapshot, &run) {
        Ok(path) => line(&path),
        Err(error) => {
            diagnostics.push(diagnostic(id, error));
            Value::Null
        }
    };
    let trainset = assignment(db, &run)?;
    let stock = stock_for(db, trainset.as_ref())?;
    let mut chart = Vec::new();
    for stop in &run.stops {
        let mut value = serde_json::to_value(stop)?;
        value["station_name"] =
            json!(catalog::by_id(canonical, &stop.station_id)?.map(|row| row.name));
        value["distance_m"] = json!(corridor.as_ref().and_then(|c| stop_distance(c, stop)));
        chart.push(value);
    }
    Ok(
        json!({"run":run,"service":service,"corridor":corridor,"chart":chart,"trainset":trainset,
             "stock_type":stock,"geometry":geometry,"diagnostics":diagnostics,"service_epoch_utc":service_epoch(&run.service_date,&run.time_zone)?}),
    )
}

pub(super) fn simulate(
    db: &Connection,
    canonical: &Connection,
    snapshot: &str,
    ids: &[String],
    now: f64,
    level: &str,
    scenario: &Scenario,
    resources: bool,
) -> Result<Value> {
    scenario.validate()?;
    ensure!(
        ids.len() <= 1000 && now.is_finite() && (-86400.0..=691200.0).contains(&now),
        "INVALID_SCENARIO · 模拟时间或车次数量超出范围"
    );
    ensure!(
        matches!(level, "l1" | "l2" | "l3"),
        "INVALID_SCENARIO · 未知模拟层级"
    );
    let (mut states, mut occupancies, mut diagnostics) = (Vec::new(), Vec::new(), Vec::new());
    let mut selected = HashSet::new();
    let mut resource_budget_exceeded = false;
    let eased = level != "l1";
    if level == "l3" {
        diagnostics.push(json!({"code":"UNVERIFIED_OPERATION_RULES","message":"假设性场景分析。容量、追踪间隔及车组周转参数由用户设定。"}));
    }
    for id in ids {
        if !selected.insert(id) {
            continue;
        }
        let result = (|| -> Result<Value> {
            let run = latest_run(db, id)?;
            if run.status == "cancelled" {
                return Ok(
                    json!({"run_id":id,"time_s":now,"status":"cancelled","position_kind":"planned"}),
                );
            }
            let path = prepare(db, canonical, snapshot, &run)?;
            let vehicle = assignment(db, &run)?;
            let stock = stock_for(db, vehicle.as_ref())?;
            let delay = if level == "l3" {
                *scenario.delays.get(id).unwrap_or(&0) as f64
            } else {
                0.
            };
            let timing = timing(&path, &run, stock.as_ref(), delay, eased)?;
            if level == "l3" && resources && !resource_budget_exceeded {
                let before = occupancies.len();
                occupation(&run, &path, &timing, vehicle.as_ref(), &mut occupancies);
                if occupancies.len() > 500_000 {
                    occupancies.truncate(before);
                    resource_budget_exceeded = true;
                }
            }
            Ok(sample(&run, &path, &timing, now, eased))
        })();
        match result {
            Ok(state) => states.push(state),
            Err(error) => {
                diagnostics.push(diagnostic(id, error));
                states.push(json!({"run_id":id,"time_s":now,"status":"unresolved","position_kind":"planned"}));
            }
        }
    }
    let (conflicts, truncated) = conflicts(&mut occupancies, scenario);
    let total = occupancies.len();
    occupancies.truncate(8192);
    if resource_budget_exceeded || truncated || total > occupancies.len() {
        diagnostics.push(json!({"code":"RESOURCE_BUDGET","message":"结果达到显示预算，请分日期或分通道缩小分析范围。"}));
    }
    Ok(
        json!({"time_s":now,"states":states,"occupancies":occupancies,"conflicts":conflicts,
              "diagnostics":diagnostics,"total_occupancies":total,"resource_analysis_complete":!resource_budget_exceeded}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn position_profile_is_deterministic_and_monotonic() {
        let path = Travel {
            from: Timing {
                arrival: 86_500.,
                departure: 86_500.,
                meters: 0.,
                passes: false,
            },
            to: Timing {
                arrival: 86_700.,
                departure: 86_700.,
                meters: 3000.,
                passes: false,
            },
        };
        let (position, speed) = path.curve(0.5, true);
        assert!((position - 0.5).abs() < 1e-12);
        assert!((speed - 1.0 / 0.82).abs() < 1e-12);
        assert!(path.time_at(500., true) < path.time_at(1500., true));
        assert!((path.time_at(1500., true) - 86_600.).abs() < 0.001);
    }
    #[test]
    fn hypothesis_detects_opposing_tracks_and_vehicle_reuse() {
        let op = |run: &str, kind, id: &str, begin, end, direction| Occupancy {
            train_run_id: run.into(),
            resource_type: kind,
            resource_id: id.into(),
            start_time_s: begin,
            end_time_s: end,
            direction,
        };
        let mut list = [
            op("A", "track", "edge", 0., 100., Some(true)),
            op("B", "track", "edge", 50., 120., Some(false)),
            op("A", "trainset", "vehicle", 0., 100., None),
            op("B", "trainset", "vehicle", 101., 200., None),
        ];
        let (out, truncated) = conflicts(&mut list, &Scenario::default());
        assert_eq!(out.len(), 2);
        assert!(!truncated);
        assert_eq!(out[0]["conflict_type"], "opposing_occupation");
        assert_eq!(out[1]["conflict_type"], "vehicle_turnaround");
    }
}
