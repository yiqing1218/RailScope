//! The native SQLite adapter for the RailScope directed-edge and operating-plan contract.
use crate::{catalog, graph, pack::Pack};
use anyhow::{Context, Result, bail, ensure};
use chrono::{NaiveDate, TimeZone};
use geo::Geometry;
use geozero::{ToGeo, wkb::Wkb};
use rusqlite::{Connection, OptionalExtension, params};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, HashMap, HashSet},
    sync::atomic::AtomicBool,
    time::Instant,
};
use uuid::Uuid;

mod corridors;
mod cycles;
mod legacy;
mod schedule;
mod sim;

pub use corridors::query as corridor_query;

#[derive(Clone, Serialize, Deserialize)]
pub struct Provenance {
    source_kind: String,
    source_url_or_reference: String,
    #[serde(default)]
    source_timestamp: Option<String>,
    #[serde(default)]
    confidence: Option<f64>,
    #[serde(default)]
    verified_by: Option<String>,
}
impl Provenance {
    fn validate(&self) -> Result<()> {
        ensure!(
            matches!(
                self.source_kind.as_str(),
                "demo" | "user_defined" | "publisher" | "gtfs" | "osm"
            ),
            "INVALID_SOURCE · 未知数据来源类型"
        );
        ensure!(
            !self.source_url_or_reference.trim().is_empty()
                && self.source_url_or_reference.len() <= 512,
            "INVALID_SOURCE · 请输入可追溯的数据来源"
        );
        ensure!(
            self.confidence.is_none_or(|c| (0.0..=1.0).contains(&c)),
            "INVALID_SOURCE · 可信度超出范围"
        );
        Ok(())
    }
    fn demo(reference: &str) -> Self {
        Self {
            source_kind: "demo".into(),
            source_url_or_reference: reference.into(),
            source_timestamp: None,
            confidence: None,
            verified_by: None,
        }
    }
}

#[derive(Clone, Serialize, Deserialize)]
struct EdgeRef {
    edge_id: String,
    sequence: usize,
    forward: bool,
    #[serde(default)]
    start_distance_m: f64,
    #[serde(default)]
    end_distance_m: f64,
}
#[derive(Clone, Serialize, Deserialize)]
struct Corridor {
    id: String,
    name: String,
    version: u32,
    snapshot_id: String,
    #[serde(default)]
    origin_node_id: String,
    #[serde(default)]
    destination_node_id: String,
    edge_refs: Vec<EdgeRef>,
    verification_status: String,
    resolution_mode: String,
    #[serde(default)]
    source_id: String,
    provenance: Provenance,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    warnings: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    candidates: Vec<Candidate>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    intent: Vec<corridors::ControlStep>,
}
#[derive(Clone, Serialize, Deserialize)]
struct Candidate {
    edge_ids: Vec<String>,
    meters: f64,
}

#[derive(Clone, Serialize, Deserialize)]
struct Service {
    id: String,
    train_code: String,
    operator: Option<String>,
    mode: String,
    origin_station_id: Option<String>,
    destination_station_id: Option<String>,
    provenance: Provenance,
}
#[derive(Clone, Serialize, Deserialize)]
struct Run {
    id: String,
    service_id: String,
    train_number: String,
    service_date: String,
    time_zone: String,
    plan_version: u32,
    status: String,
    origin_station_id: Option<String>,
    destination_station_id: Option<String>,
    corridor_id: Option<String>,
    corridor_version: Option<u32>,
    stops: Vec<Stop>,
    provenance: Provenance,
}
#[derive(Clone, Serialize, Deserialize)]
struct Stop {
    station_id: String,
    sequence: usize,
    arrival_time_s: Option<i64>,
    departure_time_s: Option<i64>,
    #[serde(default)]
    pass_through: bool,
    platform_id: Option<String>,
    station_route_id: Option<String>,
    station_track_id: Option<String>,
    stop_edge_id: Option<String>,
    stop_edge_sequence: Option<usize>,
    stop_offset_m: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    timing_evidence: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    anchor_source: Option<String>,
}
#[derive(Clone, Serialize, Deserialize)]
struct StockType {
    id: String,
    name: String,
    series: Option<String>,
    max_speed_kmh: Option<f64>,
    length_m: Option<f64>,
    provenance: Provenance,
}
#[derive(Clone, Serialize, Deserialize)]
struct Trainset {
    id: String,
    name: String,
    identifier: Option<String>,
    stock_type_id: Option<String>,
    provenance: Provenance,
}
#[derive(Clone, Serialize, Deserialize)]
struct Assignment {
    run_id: String,
    trainset_id: String,
}
#[derive(Clone, Serialize, Deserialize)]
struct Scenario {
    id: String,
    name: String,
    headway_s: u32,
    turnaround_s: u32,
    delays: BTreeMap<String, i64>,
}
impl Default for Scenario {
    fn default() -> Self {
        Self {
            id: String::new(),
            name: "假设性场景".into(),
            headway_s: 120,
            turnaround_s: 900,
            delays: BTreeMap::new(),
        }
    }
}
impl Scenario {
    fn validate(&self) -> Result<()> {
        ensure!(
            self.headway_s <= 3600
                && self.turnaround_s <= 14400
                && self.delays.len() <= 1000
                && self
                    .delays
                    .values()
                    .all(|value| (0..=86400).contains(value)),
            "INVALID_SCENARIO · 资源间隔或延误超出范围"
        );
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case")]
enum Request {
    List {
        service_date: Option<String>,
    },
    ResolveCorridor {
        start_id: String,
        end_id: String,
        strict: bool,
        name: String,
        #[serde(default)]
        waypoints: Vec<String>,
        #[serde(default)]
        line_ids: Vec<String>,
    },
    SaveCorridor {
        corridor: Corridor,
    },
    ArchiveCorridor {
        id: String,
    },
    CancelRun {
        id: String,
        version: u32,
    },
    GenerateCycle {
        cycle: cycles::Cycle,
    },
    ImportLegacy {
        content: String,
        service_date: String,
        #[serde(default)]
        aliases: BTreeMap<String, String>,
    },
    LegacyIssues,
    SaveService {
        service: Service,
    },
    SaveRun {
        run: Run,
    },
    SaveStock {
        stock: StockType,
    },
    SaveTrainset {
        trainset: Trainset,
    },
    Assign {
        assignment: Assignment,
    },
    GetRun {
        id: String,
    },
    PreviewRun {
        run: Run,
        time_s: f64,
    },
    SuggestAnchors {
        run: Run,
    },
    SaveScenario {
        scenario: Scenario,
    },
    Simulate {
        run_ids: Vec<String>,
        time_s: f64,
        level: String,
        scenario: Option<Scenario>,
        #[serde(default)]
        resources: bool,
    },
    ImportTimetable {
        format: String,
        content: String,
        service_date: Option<String>,
        #[serde(default)]
        stop_map: BTreeMap<String, String>,
    },
    ExportTimetable {
        format: String,
        run_ids: Option<Vec<String>>,
    },
    SeedDemo {
        corridor_id: String,
        version: u32,
        origin_station_id: String,
        destination_station_id: String,
        service_date: String,
    },
}

pub fn initialize(db: &Connection) -> Result<()> {
    db.execute_batch(include_str!("operations.sql"))?;
    Ok(())
}

fn identifier(id: &str) -> Result<()> {
    ensure!(
        Uuid::parse_str(id).is_ok(),
        "INVALID_ID · 必须引用 RailScope 稳定 UUID"
    );
    Ok(())
}
fn new_id(id: &mut String) -> Result<()> {
    if id.is_empty() {
        *id = Uuid::new_v4().to_string();
    }
    identifier(id)
}
fn text(value: &str, max: usize) -> Result<()> {
    ensure!(
        !value.trim().is_empty() && value.chars().count() <= max,
        "INVALID_TEXT · 必填字段缺失或长度超限"
    );
    Ok(())
}
fn from_row<T: DeserializeOwned>(
    db: &Connection,
    sql: &str,
    args: impl rusqlite::Params,
) -> Result<Option<T>> {
    let serialized: Option<String> = db.query_row(sql, args, |r| r.get(0)).optional()?;
    serialized
        .map(|s| serde_json::from_str(&s).map_err(Into::into))
        .transpose()
}
fn all<T: DeserializeOwned>(db: &Connection, sql: &str) -> Result<Vec<T>> {
    let mut stmt = db.prepare(sql)?;
    let mut rows = stmt.query([])?;
    let mut list = Vec::new();
    while let Some(row) = rows.next()? {
        list.push(serde_json::from_str(&row.get::<_, String>(0)?)?);
    }
    Ok(list)
}

#[derive(Clone)]
struct Track {
    from: String,
    to: String,
    forward: bool,
    reverse: bool,
    meters: f64,
    kind: String,
    status: String,
    class: String,
    max_speed: Option<f64>,
    shape: Vec<[f64; 2]>,
}
fn track(db: &Connection, id: &str) -> Result<Track> {
    identifier(id)?;
    let raw=db.query_row(
        "SELECT edge.from_node,edge.to_node,edge.forward,edge.reverse,edge.meters,e.kind,e.status,e.class,g.wgs84_wkb,
                json_extract(s.tags,'$.maxspeed')
         FROM edge JOIN entity e ON e.id=edge.id JOIN geometry g ON g.entity_id=e.id
         JOIN source_object s ON s.ref=e.source_ref WHERE edge.id=?", [id], |r|
         Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?,r.get(5)?,r.get(6)?,r.get(7)?,
             r.get::<_,Vec<u8>>(8)?,r.get::<_,Option<String>>(9)?))).optional()?
        .context("NO_PHYSICAL_PATH · 此实体没有原始轨道")?;
    let Geometry::LineString(shape) = Wkb(raw.8).to_geo()? else {
        bail!("NO_PHYSICAL_PATH · 轨道几何类型不正确")
    };
    ensure!(shape.0.len() >= 2, "NO_PHYSICAL_PATH · 轨道几何不足");
    Ok(Track {
        from: raw.0,
        to: raw.1,
        forward: raw.2,
        reverse: raw.3,
        meters: raw.4,
        kind: raw.5,
        status: raw.6,
        class: raw.7,
        max_speed: raw
            .9
            .and_then(|s| s.parse::<f64>().ok())
            .filter(|v| v.is_finite() && *v > 0.0),
        shape: shape.0.iter().map(|c| [c.x, c.y]).collect(),
    })
}
fn oriented(t: &Track, forward: bool) -> (&str, &str) {
    if forward {
        (&t.from, &t.to)
    } else {
        (&t.to, &t.from)
    }
}
fn usable(track: &Track, mode: &str, forward: bool) -> Result<()> {
    ensure!(
        matches!(mode, "rail" | "metro")
            && track.kind == mode
            && track.status == "operating"
            && track.class != "unknown",
        "NO_PHYSICAL_PATH · 轨道制式或运营状态不可用于此通道"
    );
    ensure!(
        if forward {
            track.forward
        } else {
            track.reverse
        },
        "DIRECTION_CONFLICT · 轨道不允许此方向"
    );
    if mode == "metro" {
        // RailScope's operations opening is limited to the Shanghai metro.
        ensure!(
            track
                .shape
                .iter()
                .all(|p| (120.6..=122.3).contains(&p[0]) && (30.5..=31.9).contains(&p[1])),
            "REGION_UNAVAILABLE · 地铁运行目前仅开放上海"
        );
    }
    Ok(())
}
fn is_diamond(db: &Connection, node: &str) -> Result<bool> {
    Ok(db.query_row(
        "SELECT EXISTS(SELECT 1 FROM node n JOIN source_object s ON s.ref=n.source_ref
        WHERE n.id=? AND json_extract(s.tags,'$.railway')='railway_crossing')",
        [node],
        |r| r.get(0),
    )?)
}

fn validate_corridor(
    db: &Connection,
    snapshot: &str,
    corridor: &mut Corridor,
) -> Result<Vec<Track>> {
    text(&corridor.name, 120)?;
    corridor.provenance.validate()?;
    ensure!(
        corridor.snapshot_id == snapshot,
        "DATA_VERSION_CONFLICT · 通道绑定其他快照"
    );
    ensure!(
        matches!(
            corridor.verification_status.as_str(),
            "automatic_reference" | "verified"
        ),
        "NO_PHYSICAL_PATH · 持久化通道需要完整物理径路"
    );
    if corridor.verification_status == "verified" {
        ensure!(
            corridor.provenance.source_kind == "publisher"
                && corridor
                    .provenance
                    .verified_by
                    .as_ref()
                    .is_some_and(|v| !v.trim().is_empty()),
            "UNVERIFIED_OPERATION_RULES · 请输入发布方资料与核验人"
        );
    }
    ensure!(
        (1..=10000).contains(&corridor.edge_refs.len()),
        "NO_PHYSICAL_PATH · 通道为空或超出路径预算"
    );
    let mut result = Vec::new();
    let mut distance = 0.0;
    let mut prior = String::new();
    let mut mode = String::new();
    for (sequence, reference) in corridor.edge_refs.iter_mut().enumerate() {
        identifier(&reference.edge_id)?;
        let current = track(db, &reference.edge_id)?;
        if sequence == 0 {
            mode = current.kind.clone();
        }
        usable(&current, &mode, reference.forward)?;
        let (from, to) = oriented(&current, reference.forward);
        if sequence == 0 {
            corridor.origin_node_id = from.into();
        } else {
            ensure!(prior == from, "NO_PHYSICAL_PATH · 相邻物理边不连续");
            ensure!(
                !is_diamond(db, from)?,
                "NO_PHYSICAL_PATH · 菱形交叉缺少接轨依据"
            );
        }
        prior = to.into();
        reference.sequence = sequence;
        reference.start_distance_m = distance;
        distance += current.meters;
        reference.end_distance_m = distance;
        result.push(current);
    }
    corridor.destination_node_id = prior;
    Ok(result)
}

fn solve(
    pack: &Pack,
    canonical: &Connection,
    start: &str,
    end: &str,
    strict: bool,
    name: String,
    waypoints: &[String],
    line_ids: &[String],
) -> Result<Corridor> {
    ensure!(
        waypoints.len() <= 16 && line_ids.len() <= 32,
        "INVALID_INPUT · 途经区段或线路限制过多"
    );
    text(&name, 120)?;
    let mode = track(canonical, start)?.kind;
    ensure!(
        matches!(mode.as_str(), "rail" | "metro"),
        "NO_PHYSICAL_PATH · 请选择铁路或地铁物理边"
    );
    for line_id in line_ids {
        identifier(line_id)?;
        ensure!(
            catalog::by_id(canonical, line_id)?.is_some_and(|v| v.kind == "line"),
            "INVALID_LINE · 未知业务线路"
        );
    }
    let lines = serde_json::to_string(line_ids)?;
    let cancel = AtomicBool::new(false);
    let mut ids = Vec::new();
    let anchors = std::iter::once(start)
        .chain(waypoints.iter().map(String::as_str))
        .chain(std::iter::once(end))
        .collect::<Vec<_>>();
    let mut alternatives = Vec::new();
    for pair in anchors.windows(2) {
        let route = graph::route_options(
            canonical,
            pair[0],
            pair[1],
            &cancel,
            Some(&mode),
            "",
            &lines,
        )
        .context("NO_PHYSICAL_PATH · 找不到添加条件内的显式接轨路径")?;
        if strict {
            ensure!(
                route.edges.len() <= 48,
                "PATH_AMBIGUOUS · 通道过长，无法在检索预算内确认唯一性，请指定中间物理边"
            );
        }
        for edge in route.edges.iter().skip(1).take(if strict {
            route.edges.len().saturating_sub(2)
        } else {
            3
        }) {
            match graph::route_options(
                canonical,
                pair[0],
                pair[1],
                &cancel,
                Some(&mode),
                edge,
                &lines,
            ) {
                Ok(other) => {
                    ensure!(
                        !strict,
                        "PATH_AMBIGUOUS · 检测到多条合法路径，请指定中间物理区段"
                    );
                    alternatives.push(Candidate {
                        edge_ids: other.edges,
                        meters: other.meters,
                    });
                    break;
                }
                Err(error) if strict && !error.to_string().contains("未找到显式") => {
                    bail!("PATH_AMBIGUOUS · 无法确认路径唯一性 · {error}")
                }
                Err(_) => {}
            }
        }
        for id in route.edges {
            if ids.last() != Some(&id) {
                ids.push(id);
            }
        }
    }
    let tracks = ids
        .iter()
        .map(|id| track(canonical, id))
        .collect::<Result<Vec<_>>>()?;
    let mut solutions = Vec::new();
    for first in [true, false] {
        if usable(&tracks[0], &mode, first).is_err() {
            continue;
        }
        let mut refs = vec![first];
        let mut end_node = oriented(&tracks[0], first).1;
        for track in tracks.iter().skip(1) {
            let direction = [true, false].into_iter().find(|direction| {
                oriented(track, *direction).0 == end_node
                    && usable(track, &mode, *direction).is_ok()
            });
            let Some(direction) = direction else {
                break;
            };
            refs.push(direction);
            end_node = oriented(track, direction).1;
        }
        if refs.len() == tracks.len() {
            solutions.push(refs);
        }
    }
    ensure!(
        !solutions.is_empty(),
        "DIRECTION_CONFLICT · 途经区段不能组成连续方向"
    );
    ensure!(
        !strict || solutions.len() == 1,
        "PATH_AMBIGUOUS · 起终边需要明确走行方向"
    );
    let mut corridor = Corridor {
        id: String::new(),
        name,
        version: 0,
        snapshot_id: pack.manifest.snapshot.clone(),
        origin_node_id: String::new(),
        destination_node_id: String::new(),
        edge_refs: ids
            .into_iter()
            .zip(solutions.remove(0))
            .enumerate()
            .map(|(i, (id, forward))| EdgeRef {
                edge_id: id,
                sequence: i,
                forward,
                start_distance_m: 0.0,
                end_distance_m: 0.0,
            })
            .collect(),
        verification_status: "automatic_reference".into(),
        resolution_mode: if strict {
            "strict_unique"
        } else {
            "automatic_reference"
        }
        .into(),
        source_id: "canonical-track-graph".into(),
        provenance: Provenance {
            source_kind: "osm".into(),
            source_url_or_reference: pack.manifest.source_file.clone(),
            source_timestamp: None,
            confidence: None,
            verified_by: None,
        },
        warnings: vec!["依据显式接轨和方向的自动参考，实际调度进路需要独立核验".into()],
        candidates: alternatives,
        intent: Vec::new(),
    };
    validate_corridor(canonical, &pack.manifest.snapshot, &mut corridor)?;
    Ok(corridor)
}

fn latest_run(db: &Connection, id: &str) -> Result<Run> {
    from_row(
        db,
        "SELECT payload FROM ops_run WHERE id=? ORDER BY version DESC LIMIT 1",
        [id],
    )?
    .context("MISSING_TIMETABLE · 找不到运行实例")
}

fn scheduled_window(run: &Run) -> Result<(i64, i64)> {
    let epoch = service_epoch(&run.service_date, &run.time_zone)?;
    let start = run
        .stops
        .first()
        .and_then(|s| s.arrival_time_s.or(s.departure_time_s))
        .context("MISSING_TIMETABLE · 缺少起点时刻")?;
    let end = run
        .stops
        .last()
        .and_then(|s| s.departure_time_s.or(s.arrival_time_s))
        .context("MISSING_TIMETABLE · 缺少终点时刻")?;
    Ok((epoch + start, epoch + end))
}

fn validate_assignment(db: &Connection, run: &Run, trainset: &str) -> Result<()> {
    if run.status == "cancelled" {
        return Ok(());
    }
    let (first, last) = scheduled_window(run)?;
    let mut stmt=db.prepare("SELECT r.payload FROM ops_assignment a JOIN ops_run r ON a.run_id=r.id AND a.run_version=r.version
      WHERE a.trainset_id=? AND a.run_id!=? AND r.version=(SELECT max(version) FROM ops_run WHERE id=r.id)")?;
    let mut rows = stmt.query(params![trainset, run.id])?;
    while let Some(row) = rows.next()? {
        let other: Run = serde_json::from_str(&row.get::<_, String>(0)?)?;
        if other.status == "cancelled" {
            continue;
        }
        let (start, end) = scheduled_window(&other)?;
        ensure!(
            last <= start || end <= first,
            "VEHICLE_OVERLAP · 车组与 {}（{}）的计划时段重叠",
            other.train_number,
            other.service_date
        );
    }
    Ok(())
}
fn corridor_for(db: &Connection, run: &Run) -> Result<Option<Corridor>> {
    match (&run.corridor_id, run.corridor_version) {
        (Some(id), Some(v)) => from_row(
            db,
            "SELECT payload FROM ops_corridor WHERE id=? AND version=?",
            params![id, v],
        ),
        _ => Ok(None),
    }
}
fn service_for(db: &Connection, id: &str) -> Result<Service> {
    from_row(db, "SELECT payload FROM ops_service WHERE id=?", [id])?
        .context("MISSING_SERVICE · 找不到车次服务")
}
fn save_service(db: &Connection, mut service: Service) -> Result<Service> {
    new_id(&mut service.id)?;
    text(&service.train_code, 32)?;
    ensure!(
        matches!(service.mode.as_str(), "rail" | "metro"),
        "INVALID_SERVICE · 请选择国铁或地铁"
    );
    service.provenance.validate()?;
    if let Ok(old) = service_for(db, &service.id) {
        ensure!(
            old.mode == service.mode,
            "INVALID_SERVICE · 既有车次服务不能修改制式"
        );
    }
    db.execute("INSERT INTO ops_service VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
               params![service.id,serde_json::to_string(&service)?])?;
    Ok(service)
}
fn stop_distance(corridor: &Corridor, stop: &Stop) -> Option<f64> {
    let index = stop.stop_edge_sequence?;
    let path = corridor.edge_refs.get(index)?;
    let offset = stop.stop_offset_m?;
    (stop.stop_edge_id.as_ref() == Some(&path.edge_id)
        && (0.0..=path.end_distance_m - path.start_distance_m + 0.00001).contains(&offset))
    .then_some(path.start_distance_m + offset)
}
fn station(db: &Connection, id: &str) -> Result<catalog::Summary> {
    identifier(id)?;
    catalog::by_id(db, id)?
        .filter(|s| s.kind == "station")
        .context("MISSING_STOP_ANCHOR · 找不到指定车站")
}
fn service_epoch(date: &str, timezone: &str) -> Result<i64> {
    let date = NaiveDate::parse_from_str(date, "%Y-%m-%d")
        .context("INVALID_DATE · 运营日期格式 YYYY-MM-DD")?;
    let zone = timezone
        .parse::<chrono_tz::Tz>()
        .map_err(|_| anyhow::anyhow!("INVALID_TIMEZONE · 无效运营时区"))?;
    Ok(zone
        .from_local_datetime(&date.and_hms_opt(0, 0, 0).unwrap())
        .single()
        .context("INVALID_TIMEZONE · 当日零点不唯一")?
        .timestamp())
}
fn save_run(db: &Connection, canonical: &Connection, snapshot: &str, mut run: Run) -> Result<Run> {
    new_id(&mut run.id)?;
    let service = service_for(db, &run.service_id)?;
    run.train_number = service.train_code.clone();
    run.provenance.validate()?;
    service_epoch(&run.service_date, &run.time_zone)?;
    ensure!(
        matches!(run.status.as_str(), "scheduled" | "cancelled"),
        "INVALID_STATUS · 时刻表仅导入计划或停运"
    );
    ensure!(
        (2..=2500).contains(&run.stops.len()),
        "MISSING_TIMETABLE · 至少两个有效停站"
    );
    ensure!(
        run.corridor_id.is_some() == run.corridor_version.is_some(),
        "DATA_VERSION_CONFLICT · 通道版本缺失"
    );
    let mut corridor = corridor_for(db, &run)?;
    if run.corridor_id.is_some() {
        ensure!(
            corridor.is_some(),
            "DATA_VERSION_CONFLICT · 未找到固定通道版本"
        );
    }
    if let Some(corridor) = &mut corridor {
        let tracks = validate_corridor(canonical, snapshot, corridor)?;
        ensure!(
            tracks[0].kind == service.mode,
            "DIRECTION_CONFLICT · 运行制式与通道不一致"
        );
    }
    let mut last_time = -1;
    let mut last_distance = -1.0;
    for (i, stop) in run.stops.iter_mut().enumerate() {
        stop.sequence = i;
        let station = station(canonical, &stop.station_id)?;
        if service.mode == "metro" {
            let [w, s, e, n] = station.bounds;
            ensure!(
                (120.6..=122.3).contains(&w)
                    && (120.6..=122.3).contains(&e)
                    && (30.5..=31.9).contains(&s)
                    && (30.5..=31.9).contains(&n),
                "REGION_UNAVAILABLE · 地铁运行目前仅开放上海"
            );
        }
        let arrival = stop
            .arrival_time_s
            .or(stop.departure_time_s)
            .context("MISSING_TIMETABLE · 停站缺少时间")?;
        let departure = stop.departure_time_s.unwrap_or(arrival);
        if let Some(evidence) = &stop.timing_evidence {
            text(evidence, 240)?;
        }
        if let Some(source) = &stop.anchor_source {
            text(source, 120)?;
        }
        ensure!(
            arrival >= last_time
                && (0..=7 * 86400).contains(&arrival)
                && (arrival..=7 * 86400).contains(&departure)
                && (!stop.pass_through || departure == arrival),
            "INVALID_TIMETABLE · 停站时序、跨日或通过时间有误"
        );
        ensure!(
            stop.station_route_id.is_none(),
            "UNVERIFIED_OPERATION_RULES · 当前快照未安装已核验 StationRoute"
        );
        if let Some(id) = &stop.platform_id {
            ensure!(
                catalog::by_id(canonical, id)?.is_some_and(|e| e.class == "platform"),
                "MISSING_STOP_ANCHOR · 站台来源未找到"
            );
        }
        if let Some(id) = &stop.station_track_id {
            track(canonical, id)?;
        }
        if stop.stop_edge_id.is_some()
            || stop.stop_edge_sequence.is_some()
            || stop.stop_offset_m.is_some()
        {
            let distance = corridor
                .as_ref()
                .and_then(|c| stop_distance(c, stop))
                .context("MISSING_STOP_ANCHOR · 停站锚点必须明确引用有序通道边和偏移")?;
            ensure!(
                distance >= last_distance,
                "DIRECTION_CONFLICT · 停站锚点逆序"
            );
            last_distance = distance;
        }
        last_time = departure;
    }
    run.origin_station_id = Some(run.stops[0].station_id.clone());
    run.destination_station_id = Some(run.stops[run.stops.len() - 1].station_id.clone());
    let current: u32 = db.query_row(
        "SELECT coalesce(max(version),0) FROM ops_run WHERE id=?",
        [&run.id],
        |r| r.get(0),
    )?;
    ensure!(
        run.plan_version == current,
        "DATA_VERSION_CONFLICT · 运行实例已被编辑，请刷新"
    );
    if current > 0 {
        let assigned = db
            .query_row(
                "SELECT trainset_id FROM ops_assignment WHERE run_id=? AND run_version=?",
                params![run.id, current],
                |row| row.get::<_, String>(0),
            )
            .optional()?;
        if let Some(trainset) = assigned {
            validate_assignment(db, &run, &trainset)?;
        }
    }
    run.plan_version = current + 1;
    db.execute(
        "INSERT INTO ops_run VALUES(?,?,?,?,?,?,?)",
        params![
            run.id,
            run.plan_version,
            run.service_id,
            run.service_date,
            run.corridor_id,
            run.corridor_version,
            serde_json::to_string(&run)?
        ],
    )?;
    if current > 0 {
        db.execute("INSERT INTO ops_assignment SELECT run_id,?,trainset_id FROM ops_assignment WHERE run_id=? AND run_version=?",
                   params![run.plan_version,run.id,current])?;
    }
    Ok(run)
}

fn save_corridor(
    db: &Connection,
    canonical: &Connection,
    pack: &Pack,
    mut corridor: Corridor,
) -> Result<Corridor> {
    new_id(&mut corridor.id)?;
    validate_corridor(canonical, &pack.manifest.snapshot, &mut corridor)?;
    corridors::validate_intent(canonical, &corridor)?;
    ensure!(
        !db.query_row(
            "SELECT EXISTS(SELECT 1 FROM ops_corridor_archive WHERE id=?)",
            [&corridor.id],
            |r| r.get::<_, bool>(0)
        )?,
        "归档通道可复制为新通道"
    );
    let current: u32 = db.query_row(
        "SELECT coalesce(max(version),0) FROM ops_corridor WHERE id=?",
        [&corridor.id],
        |r| r.get(0),
    )?;
    ensure!(
        corridor.version == current,
        "DATA_VERSION_CONFLICT · 通道已修改，请刷新最新版本"
    );
    corridor.version = current + 1;
    db.execute(
        "INSERT INTO ops_corridor VALUES(?,?,?,?)",
        params![
            corridor.id,
            corridor.version,
            corridor.snapshot_id,
            serde_json::to_string(&corridor)?
        ],
    )?;
    Ok(corridor)
}

fn assignment(db: &Connection, run: &Run) -> Result<Option<Trainset>> {
    from_row(
        db,
        "SELECT t.payload FROM ops_assignment a JOIN ops_trainset t ON t.id=a.trainset_id
      WHERE a.run_id=? AND a.run_version=?",
        params![run.id, run.plan_version],
    )
}
fn stock_for(db: &Connection, trainset: Option<&Trainset>) -> Result<Option<StockType>> {
    match trainset.and_then(|value| value.stock_type_id.as_ref()) {
        Some(id) => from_row(db, "SELECT payload FROM ops_stock WHERE id=?", [id]),
        None => Ok(None),
    }
}

pub fn execute(db: &mut Connection, pack: &Pack, request: Value) -> Result<Value> {
    let canonical = pack.connection()?;
    let snapshot = pack.manifest.snapshot.as_str();
    match serde_json::from_value::<Request>(request)? {
        Request::List { service_date } => {
            if let Some(date) = service_date.as_deref() {
                NaiveDate::parse_from_str(date, "%Y-%m-%d")?;
            }
            let mut statement = db.prepare("SELECT payload,
                  version=(SELECT max(version) FROM ops_corridor WHERE id=c.id)
                    AND NOT EXISTS(SELECT 1 FROM ops_corridor_archive a WHERE a.id=c.id) AS current_version
                  FROM ops_corridor c WHERE snapshot=?1 AND (
                  current_version OR EXISTS(SELECT 1 FROM ops_run r WHERE r.corridor_id=c.id AND r.corridor_version=c.version
                    AND (?2 IS NULL OR r.service_date=?2) AND r.version=(SELECT max(version) FROM ops_run WHERE id=r.id))
                ) ORDER BY id,version DESC")?;
            let payloads = statement.query_map(params![snapshot, service_date], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, bool>(1)?))
            })?;
            let mut corridors = Vec::new();
            let mut classify = canonical.prepare("SELECT kind FROM entity WHERE id=?")?;
            let mut find_line = canonical
                .prepare("SELECT id,name FROM entity WHERE kind='line' AND id=? LIMIT 1")?;
            let mut find_source_line = canonical
                .prepare("SELECT id,name FROM entity WHERE kind='line' AND source_ref=? LIMIT 1")?;
            let mut find_covering_lines = canonical.prepare(
                "WITH path(id) AS (SELECT DISTINCT value FROM json_each(?1))
                 SELECT l.id,l.name FROM path
                 CROSS JOIN line_member m ON m.member_id=path.id
                 CROSS JOIN entity l ON l.id=m.line_id AND l.kind='line'
                 GROUP BY l.id HAVING count(DISTINCT path.id)=(SELECT count(*) FROM path) LIMIT 2",
            )?;
            let mut line_override = db.prepare("SELECT json_extract(payload,'$.name') FROM overrides WHERE snapshot=? AND entity=?")?;
            for payload in payloads {
                let (payload, current_version) = payload?;
                let plan: Corridor = serde_json::from_str(&payload)?;
                let mut corridor = serde_json::to_value(&plan)?;
                corridor["current_version"] = json!(current_version);
                if let Some(edge) = plan.edge_refs.first() {
                    let kind: Option<String> = classify
                        .query_row([&edge.edge_id], |row| row.get(0))
                        .optional()?;
                    corridor["mode"] = json!(kind);
                }
                let mut lines = Vec::new();
                let mut add_line = |key: &str, is_source: bool| -> Result<()> {
                    let query = if is_source {
                        &mut find_source_line
                    } else {
                        &mut find_line
                    };
                    let line: Option<(String, String)> = query
                        .query_row([key], |row| Ok((row.get(0)?, row.get(1)?)))
                        .optional()?;
                    if let Some(line) = line {
                        lines.push(line);
                    }
                    Ok(())
                };
                for line in plan
                    .intent
                    .iter()
                    .filter_map(|step| step.line_id.as_deref())
                {
                    add_line(line, false)?;
                }
                if plan.intent.is_empty()
                    && plan
                        .provenance
                        .source_url_or_reference
                        .starts_with("osm:relation:")
                {
                    add_line(&plan.provenance.source_url_or_reference, true)?;
                }
                if lines.is_empty() && !plan.edge_refs.is_empty() {
                    let edges: Vec<_> = plan.edge_refs.iter().map(|edge| &edge.edge_id).collect();
                    let matches = find_covering_lines
                        .query_map([serde_json::to_string(&edges)?], |row| {
                            Ok((row.get(0)?, row.get(1)?))
                        })?
                        .collect::<rusqlite::Result<Vec<_>>>()?;
                    if matches.len() == 1 {
                        lines = matches;
                    }
                }
                let lines = lines
                    .into_iter()
                    .map(|(id, name)| -> Result<Value> {
                        let name = line_override
                            .query_row([snapshot, &id], |row| row.get::<_, Option<String>>(0))
                            .optional()?
                            .flatten()
                            .unwrap_or(name);
                        Ok(json!({"id":id,"name":name}))
                    })
                    .collect::<Result<Vec<_>>>()?;
                corridor["lines"] = json!(lines);
                corridors.push(corridor);
            }
            let mut service_query = db.prepare(
                "SELECT s.payload FROM ops_service s
                 WHERE NOT EXISTS(SELECT 1 FROM ops_run r WHERE r.service_id=s.id)
                    OR EXISTS(
                        SELECT 1 FROM ops_run r
                        LEFT JOIN ops_corridor c ON c.id=r.corridor_id AND c.version=r.corridor_version
                        WHERE r.service_id=s.id AND (c.snapshot IS NULL OR c.snapshot=?1)
                          AND r.version=(SELECT max(version) FROM ops_run WHERE id=r.id)
                    )
                 ORDER BY s.id",
            )?;
            let services = service_query
                .query_map([snapshot], |row| row.get::<_, String>(0))?
                .map(|payload| Ok(serde_json::from_str::<Service>(&payload?)?))
                .collect::<Result<Vec<_>>>()?;
            let mut runs_query = db.prepare(
                "SELECT r.payload FROM ops_run r
                LEFT JOIN ops_corridor c ON c.id=r.corridor_id AND c.version=r.corridor_version
                WHERE (?1 IS NULL OR r.service_date=?1) AND (c.snapshot IS NULL OR c.snapshot=?2)
                 AND r.version=(SELECT max(version) FROM ops_run WHERE id=r.id)
                ORDER BY r.service_date DESC,r.id LIMIT 2000",
            )?;
            let payloads = runs_query.query_map(params![service_date, snapshot], |row| {
                row.get::<_, String>(0)
            })?;
            let runs = payloads
                .map(|payload| Ok(serde_json::from_str::<Run>(&payload?)?))
                .collect::<Result<Vec<_>>>()?;
            let mut date_query = db.prepare("SELECT DISTINCT r.service_date,json_extract(s.payload,'$.mode') FROM ops_run r
                JOIN ops_service s ON s.id=r.service_id
                LEFT JOIN ops_corridor c ON c.id=r.corridor_id AND c.version=r.corridor_version
                WHERE (c.snapshot IS NULL OR c.snapshot=?1) AND r.version=(SELECT max(version) FROM ops_run WHERE id=r.id)
                ORDER BY r.service_date DESC LIMIT 730")?;
            let mut dates_by_mode = BTreeMap::<String, Vec<String>>::new();
            let mut dates = Vec::<String>::new();
            let rows = date_query.query_map([snapshot], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })?;
            for row in rows {
                let (date, mode) = row?;
                if dates.last() != Some(&date) {
                    dates.push(date.clone());
                }
                dates_by_mode.entry(mode).or_default().push(date);
            }
            let stock_types =
                all::<StockType>(db, "SELECT payload FROM ops_stock ORDER BY id LIMIT 1000")?;
            let trainsets = all::<Trainset>(
                db,
                "SELECT payload FROM ops_trainset ORDER BY id LIMIT 2000",
            )?;
            let scenarios =
                all::<Scenario>(db, "SELECT payload FROM ops_scenario ORDER BY id LIMIT 100")?;
            let mut assignments_query = db.prepare(
                "SELECT a.run_id,a.trainset_id FROM ops_assignment a
                JOIN ops_run r ON r.id=a.run_id AND r.version=a.run_version
                LEFT JOIN ops_corridor c ON c.id=r.corridor_id AND c.version=r.corridor_version
                WHERE (?1 IS NULL OR r.service_date=?1) AND (c.snapshot IS NULL OR c.snapshot=?2)
                  AND r.version=(SELECT max(version) FROM ops_run WHERE id=r.id)",
            )?;
            let assignments = assignments_query
                .query_map(params![service_date, snapshot], |row| {
                    Ok(Assignment {
                        run_id: row.get(0)?,
                        trainset_id: row.get(1)?,
                    })
                })?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            let cycles =
                all::<cycles::Cycle>(db, "SELECT payload FROM ops_cycle ORDER BY id LIMIT 100")?;
            Ok(
                json!({ "corridors":corridors,"services":services,"runs":runs,"dates":dates,"dates_by_mode":dates_by_mode,"stock_types":stock_types,
                       "trainsets":trainsets,"scenarios":scenarios,"assignments":assignments,"cycles":cycles }),
            )
        }
        Request::ResolveCorridor {
            start_id,
            end_id,
            strict,
            name,
            waypoints,
            line_ids,
        } => Ok(serde_json::to_value(solve(
            pack, &canonical, &start_id, &end_id, strict, name, &waypoints, &line_ids,
        )?)?),
        Request::SaveCorridor { corridor } => Ok(serde_json::to_value(save_corridor(
            db, &canonical, pack, corridor,
        )?)?),
        Request::ArchiveCorridor { id } => {
            identifier(&id)?;
            let exists = db.query_row(
                "SELECT EXISTS(SELECT 1 FROM ops_corridor WHERE id=? AND snapshot=?)",
                params![id, snapshot],
                |r| r.get::<_, bool>(0),
            )?;
            ensure!(exists, "找不到当前快照的通道");
            db.execute(
                "INSERT OR IGNORE INTO ops_corridor_archive(id) VALUES(?)",
                [id],
            )?;
            Ok(Value::Null)
        }
        Request::CancelRun { id, version } => {
            let tx = db.transaction()?;
            let mut run = latest_run(&tx, &id)?;
            ensure!(
                run.plan_version == version,
                "DATA_VERSION_CONFLICT · 请刷新车次版本"
            );
            run.status = "cancelled".into();
            let saved = save_run(&tx, &canonical, snapshot, run)?;
            tx.commit()?;
            Ok(serde_json::to_value(saved)?)
        }
        Request::SaveService { service } => Ok(serde_json::to_value(save_service(db, service)?)?),
        Request::GenerateCycle { cycle } => cycles::generate(db, &canonical, snapshot, cycle),
        Request::ImportLegacy {
            content,
            service_date,
            aliases,
        } => legacy::import(db, &canonical, pack, &content, &service_date, &aliases),
        Request::LegacyIssues => legacy::issues(db, snapshot),
        Request::SaveRun { run } => {
            let tx = db.transaction()?;
            let run = save_run(&tx, &canonical, snapshot, run)?;
            tx.commit()?;
            Ok(serde_json::to_value(run)?)
        }
        Request::SaveStock { mut stock } => {
            new_id(&mut stock.id)?;
            text(&stock.name, 100)?;
            stock.provenance.validate()?;
            ensure!(
                stock
                    .max_speed_kmh
                    .is_none_or(|v| (1.0..=1000.0).contains(&v))
                    && stock.length_m.is_none_or(|v| (1.0..=5000.0).contains(&v)),
                "INVALID_STOCK · 无效车型参数"
            );
            db.execute("INSERT INTO ops_stock VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",params![stock.id,serde_json::to_string(&stock)?])?;
            Ok(serde_json::to_value(stock)?)
        }
        Request::SaveTrainset { mut trainset } => {
            new_id(&mut trainset.id)?;
            text(&trainset.name, 100)?;
            trainset.provenance.validate()?;
            db.execute(
                "INSERT INTO ops_trainset VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET
                stock_type_id=excluded.stock_type_id,payload=excluded.payload",
                params![
                    trainset.id,
                    trainset.stock_type_id,
                    serde_json::to_string(&trainset)?
                ],
            )?;
            Ok(serde_json::to_value(trainset)?)
        }
        Request::Assign { assignment } => {
            let latest = latest_run(db, &assignment.run_id)?;
            validate_assignment(db, &latest, &assignment.trainset_id)?;
            db.execute("INSERT INTO ops_assignment VALUES(?,?,?) ON CONFLICT(run_id,run_version) DO UPDATE SET trainset_id=excluded.trainset_id",
                params![assignment.run_id,latest.plan_version,assignment.trainset_id])?;
            Ok(serde_json::to_value(assignment)?)
        }
        Request::GetRun { id } => sim::details(db, &canonical, snapshot, &id),
        Request::PreviewRun { run, time_s } => {
            sim::preview_run(db, &canonical, snapshot, &run, time_s)
        }
        Request::SuggestAnchors { run } => sim::suggest_anchors(db, &canonical, snapshot, run),
        Request::SaveScenario { mut scenario } => {
            scenario.validate()?;
            new_id(&mut scenario.id)?;
            text(&scenario.name, 100)?;
            db.execute("INSERT INTO ops_scenario VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                       params![scenario.id,serde_json::to_string(&scenario)?])?;
            Ok(serde_json::to_value(scenario)?)
        }
        Request::Simulate {
            run_ids,
            time_s,
            level,
            scenario,
            resources,
        } => sim::simulate(
            db,
            &canonical,
            snapshot,
            &run_ids,
            time_s,
            &level,
            &scenario.unwrap_or_default(),
            resources,
        ),
        Request::ImportTimetable {
            format,
            content,
            service_date,
            stop_map,
        } => schedule::import(
            db,
            &canonical,
            pack,
            &format,
            &content,
            service_date.as_deref(),
            &stop_map,
        ),
        Request::ExportTimetable { format, run_ids } => {
            schedule::export(db, &canonical, &format, run_ids.as_deref())
        }
        Request::SeedDemo {
            corridor_id,
            version,
            origin_station_id,
            destination_station_id,
            service_date,
        } => schedule::demo(
            db,
            &canonical,
            pack,
            &corridor_id,
            version,
            &origin_station_id,
            &destination_station_id,
            &service_date,
        ),
    }
}

pub struct Clock {
    anchor: Instant,
    time_s: f64,
    rate: f64,
    playing: bool,
}
impl Default for Clock {
    fn default() -> Self {
        Self {
            anchor: Instant::now(),
            time_s: 8.0 * 3600.0,
            rate: 1.0,
            playing: false,
        }
    }
}
impl Clock {
    pub fn apply(&mut self, action: &str, value: Option<f64>) -> Result<Value> {
        let now = Instant::now();
        let time = if self.playing {
            self.time_s + now.duration_since(self.anchor).as_secs_f64() * self.rate
        } else {
            self.time_s
        };
        let time = time.clamp(0.0, 604800.0);
        let value = value.unwrap_or_default();
        match action {
            "get" => {
                return Ok(
                    json!({"time_s":time,"rate":self.rate,"playing":self.playing&&time<604800.0}),
                );
            }
            "play" => self.playing = true,
            "pause" => self.playing = false,
            "reset" => {
                self.time_s = 28800.;
                self.playing = false;
                self.anchor = now;
                return self.apply("get", None);
            }
            "seek" if (0.0..=604800.0).contains(&value) => {}
            "rate" if (0.125..=128.0).contains(&value) => self.rate = value,
            _ => bail!("INVALID_CLOCK · 模拟时间或倍率无效"),
        }
        self.time_s = if action == "seek" { value } else { time };
        self.anchor = now;
        Ok(json!({"time_s":self.time_s,"rate":self.rate,"playing":self.playing}))
    }
}
