use super::*;
use base64::{Engine as _, engine::general_purpose::STANDARD};
use chrono::Datelike;
use std::io::{Cursor, Read, Write};
use zip::{ZipArchive, ZipWriter, write::SimpleFileOptions};

const LIMIT: usize = 16 * 1024 * 1024;
#[derive(Default, Serialize, Deserialize)]
struct Bundle {
    #[serde(default)]
    services: Vec<Service>,
    #[serde(default)]
    runs: Vec<Run>,
}
#[derive(Clone, Serialize, Deserialize)]
struct CsvRow {
    train_code: String,
    service_date: String,
    mode: String,
    station_id: String,
    sequence: usize,
    arrival_time_s: Option<i64>,
    departure_time_s: Option<i64>,
    #[serde(default)]
    run_ref: String,
    #[serde(default)]
    pass_through: bool,
    #[serde(default)]
    platform_id: Option<String>,
    #[serde(default)]
    corridor_id: Option<String>,
    #[serde(default)]
    corridor_version: Option<u32>,
    #[serde(default)]
    stop_edge_id: Option<String>,
    #[serde(default)]
    stop_edge_sequence: Option<usize>,
    #[serde(default)]
    stop_offset_m: Option<f64>,
}
fn stop(row: &CsvRow) -> Stop {
    Stop {
        station_id: row.station_id.clone(),
        sequence: row.sequence,
        arrival_time_s: row.arrival_time_s,
        departure_time_s: row.departure_time_s,
        pass_through: row.pass_through,
        platform_id: row.platform_id.clone(),
        station_route_id: None,
        station_track_id: None,
        stop_edge_id: row.stop_edge_id.clone(),
        stop_edge_sequence: row.stop_edge_sequence,
        stop_offset_m: row.stop_offset_m,
    }
}
fn provenance(kind: &str, reference: &str) -> Provenance {
    Provenance {
        source_kind: kind.into(),
        source_url_or_reference: reference.into(),
        source_timestamp: None,
        confidence: None,
        verified_by: None,
    }
}
fn run(service: &Service, date: &str, stops: Vec<Stop>, source: Provenance) -> Run {
    Run {
        id: String::new(),
        service_id: service.id.clone(),
        train_number: service.train_code.clone(),
        service_date: date.into(),
        time_zone: "Asia/Shanghai".into(),
        plan_version: 0,
        status: "scheduled".into(),
        origin_station_id: None,
        destination_station_id: None,
        corridor_id: None,
        corridor_version: None,
        stops,
        provenance: source,
    }
}
fn service(code: &str, mode: &str, source: Provenance) -> Service {
    Service {
        id: Uuid::new_v4().to_string(),
        train_code: code.into(),
        operator: None,
        mode: mode.into(),
        origin_station_id: None,
        destination_station_id: None,
        provenance: source,
    }
}
fn parse_csv(content: &str) -> Result<Bundle> {
    let mut groups: BTreeMap<(String, String, String, String), Vec<CsvRow>> = BTreeMap::new();
    let mut records = csv::Reader::from_reader(content.as_bytes());
    for (i, row) in records.deserialize::<CsvRow>().enumerate() {
        ensure!(i < 100_000, "IMPORT_BUDGET · CSV 行数超过上限");
        let row = row?;
        groups
            .entry((
                row.train_code.clone(),
                row.service_date.clone(),
                row.mode.clone(),
                row.run_ref.clone(),
            ))
            .or_default()
            .push(row);
    }
    let mut bundle = Bundle::default();
    let mut services = HashMap::new();
    for ((code, date, mode, _), mut rows) in groups {
        rows.sort_by_key(|r| r.sequence);
        ensure!(
            rows.windows(2)
                .all(|pair| pair[0].sequence < pair[1].sequence),
            "INVALID_TIMETABLE · CSV 有重复站序"
        );
        ensure!(
            rows.iter().all(|r| r.corridor_id == rows[0].corridor_id
                && r.corridor_version == rows[0].corridor_version),
            "DATA_VERSION_CONFLICT · 同一运行实例的通道版本不一致"
        );
        let source = provenance("user_defined", "本地 CSV 时刻表");
        let svc = services
            .entry((code.clone(), mode.clone()))
            .or_insert_with(|| service(&code, &mode, source.clone()));
        let mut run = run(svc, &date, rows.iter().map(stop).collect(), source);
        run.corridor_id = rows[0].corridor_id.clone();
        run.corridor_version = rows[0].corridor_version;
        bundle.runs.push(run);
    }
    bundle.services = services.into_values().collect();
    Ok(bundle)
}

fn parse_hms(value: &str) -> Result<i64> {
    let time = value
        .split(':')
        .map(str::parse::<i64>)
        .collect::<std::result::Result<Vec<_>, _>>()?;
    let [hours, minutes, seconds] = time.as_slice() else {
        bail!("INVALID_TIMETABLE · GTFS 时间格式错误")
    };
    ensure!(
        (0..=168).contains(hours) && (0..60).contains(minutes) && (0..60).contains(seconds),
        "INVALID_TIMETABLE · GTFS 跨日时刻超出范围"
    );
    Ok(hours * 3600 + minutes * 60 + seconds)
}
fn zipped_rows(
    archive: &mut ZipArchive<Cursor<Vec<u8>>>,
    name: &str,
    mandatory: bool,
) -> Result<Vec<BTreeMap<String, String>>> {
    let mut file = match archive.by_name(name) {
        Ok(file) => file,
        Err(zip::result::ZipError::FileNotFound) if !mandatory => return Ok(vec![]),
        Err(error) => return Err(error).with_context(|| format!("INVALID_GTFS · 缺少 {name}")),
    };
    ensure!(
        file.size() <= LIMIT as u64,
        "IMPORT_BUDGET · GTFS 表超出容量"
    );
    let mut bytes = Vec::new();
    file.by_ref()
        .take(LIMIT as u64 + 1)
        .read_to_end(&mut bytes)?;
    ensure!(bytes.len() <= LIMIT, "IMPORT_BUDGET · GTFS 表超出容量");
    let mut csv = csv::Reader::from_reader(bytes.as_slice());
    let mut rows = Vec::new();
    for record in csv.deserialize() {
        rows.push(record?);
    }
    Ok(rows)
}
fn col<'a>(row: &'a BTreeMap<String, String>, field: &str) -> Result<&'a str> {
    row.get(field)
        .filter(|v| !v.is_empty())
        .map(String::as_str)
        .with_context(|| format!("INVALID_GTFS · 缺少 {field}"))
}
fn read_gtfs(
    content: &str,
    service_date: &str,
    stop_map: &BTreeMap<String, String>,
) -> Result<Bundle> {
    let date = NaiveDate::parse_from_str(service_date, "%Y-%m-%d")?;
    let key = date.format("%Y%m%d").to_string();
    let weekday = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ][date.weekday().num_days_from_monday() as usize];
    let mut archive = ZipArchive::new(Cursor::new(STANDARD.decode(content.trim())?))?;
    let routes = zipped_rows(&mut archive, "routes.txt", true)?;
    let trips = zipped_rows(&mut archive, "trips.txt", true)?;
    let stop_times = zipped_rows(&mut archive, "stop_times.txt", true)?;
    let stops = zipped_rows(&mut archive, "stops.txt", true)?;
    let calendar = zipped_rows(&mut archive, "calendar.txt", false)?;
    let exceptions = zipped_rows(&mut archive, "calendar_dates.txt", false)?;
    ensure!(
        !calendar.is_empty() || !exceptions.is_empty(),
        "INVALID_GTFS · 缺少日历与例外日期"
    );
    let mut valid = HashSet::new();
    for row in calendar {
        if col(&row, "start_date")? <= key.as_str()
            && col(&row, "end_date")? >= key.as_str()
            && row.get(weekday).is_some_and(|v| v == "1")
        {
            valid.insert(col(&row, "service_id")?.to_owned());
        }
    }
    for row in exceptions {
        if col(&row, "date")? == key {
            let service = col(&row, "service_id")?.to_owned();
            match col(&row, "exception_type")? {
                "1" => {
                    valid.insert(service);
                }
                "2" => {
                    valid.remove(&service);
                }
                _ => bail!("INVALID_GTFS · 无效例外类型"),
            }
        }
    }
    let route_by = routes
        .iter()
        .map(|row| Ok((col(row, "route_id")?, row)))
        .collect::<Result<HashMap<_, _>>>()?;
    let stop_ids = stops
        .iter()
        .map(|row| col(row, "stop_id"))
        .collect::<Result<HashSet<_>>>()?;
    let included = trips
        .iter()
        .filter(|row| row.get("service_id").is_some_and(|v| valid.contains(v)))
        .map(|row| Ok((col(row, "trip_id")?, row)))
        .collect::<Result<BTreeMap<_, _>>>()?;
    let mut by_trip: BTreeMap<String, Vec<Stop>> = BTreeMap::new();
    for row in stop_times {
        let trip = col(&row, "trip_id")?;
        if !included.contains_key(trip) {
            continue;
        }
        let stop_id = col(&row, "stop_id")?;
        ensure!(
            stop_ids.contains(stop_id),
            "INVALID_GTFS · stop_times 引用不存在的车站"
        );
        let station = stop_map.get(stop_id).map(String::as_str).unwrap_or(stop_id);
        identifier(station).with_context(|| {
            format!("MISSING_STOP_ANCHOR · 请提供 GTFS 站点 {stop_id} 的 RailScope UUID")
        })?;
        by_trip.entry(trip.to_owned()).or_default().push(Stop {
            station_id: station.into(),
            sequence: col(&row, "stop_sequence")?.parse()?,
            arrival_time_s: row
                .get("arrival_time")
                .filter(|s| !s.is_empty())
                .map(|s| parse_hms(s))
                .transpose()?,
            departure_time_s: row
                .get("departure_time")
                .filter(|s| !s.is_empty())
                .map(|s| parse_hms(s))
                .transpose()?,
            pass_through: false,
            platform_id: None,
            station_route_id: None,
            station_track_id: None,
            stop_edge_id: None,
            stop_edge_sequence: None,
            stop_offset_m: None,
        });
    }
    let mut bundle = Bundle::default();
    let mut services = HashMap::new();
    for (trip_id, row) in included {
        let route = route_by
            .get(col(row, "route_id")?)
            .context("INVALID_GTFS · 无效线路引用")?;
        let mode = match col(route, "route_type")? {
            "1" => "metro",
            "2" => "rail",
            _ => continue,
        };
        let code = row
            .get("trip_short_name")
            .filter(|v| !v.is_empty())
            .or_else(|| route.get("route_short_name").filter(|v| !v.is_empty()))
            .map(String::as_str)
            .unwrap_or(trip_id);
        let source = provenance("gtfs", &format!("本地 GTFS · trip_id={trip_id}"));
        let svc = services
            .entry((code.to_owned(), mode))
            .or_insert_with(|| service(code, mode, source.clone()));
        let mut stops = by_trip
            .remove(trip_id)
            .context("INVALID_GTFS · 日期适用车次缺少时刻表")?;
        stops.sort_by_key(|s| s.sequence);
        ensure!(
            stops.windows(2).all(|p| p[0].sequence < p[1].sequence),
            "INVALID_GTFS · 有重复站序"
        );
        bundle.runs.push(run(svc, service_date, stops, source));
    }
    bundle.services = services.into_values().collect();
    Ok(bundle)
}

pub(super) fn import(
    db: &mut Connection,
    canonical: &Connection,
    snapshot: &str,
    format: &str,
    content: &str,
    service_date: Option<&str>,
    stop_map: &BTreeMap<String, String>,
) -> Result<Value> {
    ensure!(
        content.len() <= LIMIT * 2 && stop_map.len() <= 50000,
        "IMPORT_BUDGET · 导入内容超出容量"
    );
    let mut bundle = match format {
        "json" => serde_json::from_str(content)?,
        "csv" => parse_csv(content)?,
        "gtfs" => read_gtfs(
            content,
            service_date.context("INVALID_DATE · GTFS 需要指定运营日期")?,
            stop_map,
        )?,
        _ => bail!("INVALID_FORMAT · 支持 JSON、CSV 和 GTFS ZIP"),
    };
    ensure!(
        !bundle.runs.is_empty() && bundle.runs.len() <= 5000 && bundle.services.len() <= 5000,
        "IMPORT_BUDGET · 无可导入运行实例或数量超限"
    );
    let tx = db.transaction()?;
    let mut service_ids = HashMap::new();
    let inserted = bundle.runs.len();
    for mut svc in bundle.services.drain(..) {
        let original = svc.id.clone();
        // Imports create independent versions with new RailScope IDs.
        svc.id.clear();
        let saved = save_service(&tx, svc)?;
        service_ids.insert(original, saved.id);
    }
    for mut run in bundle.runs.drain(..) {
        if let Some(id) = service_ids.get(&run.service_id) {
            run.service_id = id.clone();
        }
        run.id.clear();
        run.plan_version = 0;
        save_run(&tx, canonical, snapshot, run)?;
    }
    let services = service_ids.len();
    tx.commit()?;
    Ok(json!({"services":services,"runs":inserted}))
}

fn csv_bytes<T: Serialize>(rows: impl IntoIterator<Item = T>) -> Result<Vec<u8>> {
    let mut writer = csv::Writer::from_writer(Vec::new());
    for row in rows {
        writer.serialize(row)?;
    }
    Ok(writer.into_inner()?)
}
fn hms(value: Option<i64>) -> String {
    value
        .map(|v| format!("{:02}:{:02}:{:02}", v / 3600, (v % 3600) / 60, v % 60))
        .unwrap_or_default()
}
fn gtfs(b: &Bundle, canonical: &Connection) -> Result<Vec<u8>> {
    let mut zip = ZipWriter::new(Cursor::new(Vec::new()));
    let opts = SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    let mut write = |name: &str, data: Vec<u8>| -> Result<()> {
        zip.start_file(name, opts)?;
        zip.write_all(&data)?;
        Ok(())
    };
    write("agency.txt",b"agency_id,agency_name,agency_url,agency_timezone\nrs,RailScope workspace,https://example.invalid/railscope,Asia/Shanghai\n".to_vec())?;
    let mut route_rows = vec![vec![
        "route_id".into(),
        "agency_id".into(),
        "route_short_name".into(),
        "route_type".into(),
    ]];
    let mut trip_rows = vec![vec![
        "trip_id".into(),
        "route_id".into(),
        "service_id".into(),
    ]];
    let mut calendar_rows = vec![vec![
        "service_id".into(),
        "date".into(),
        "exception_type".into(),
    ]];
    let mut stop_rows = vec![vec![
        "stop_id".into(),
        "stop_name".into(),
        "stop_lat".into(),
        "stop_lon".into(),
    ]];
    let mut time_rows = vec![vec![
        "trip_id".into(),
        "arrival_time".into(),
        "departure_time".into(),
        "stop_id".into(),
        "stop_sequence".into(),
    ]];
    let mut seen = HashSet::new();
    for service in &b.services {
        route_rows.push(vec![
            service.id.clone(),
            "rs".into(),
            service.train_code.clone(),
            if service.mode == "metro" { "1" } else { "2" }.into(),
        ]);
    }
    for run in b.runs.iter().filter(|run| run.status == "scheduled") {
        trip_rows.push(vec![run.id.clone(), run.service_id.clone(), run.id.clone()]);
        calendar_rows.push(vec![
            run.id.clone(),
            run.service_date.replace('-', ""),
            "1".into(),
        ]);
        for s in &run.stops {
            if seen.insert(&s.station_id) {
                let e = station(canonical, &s.station_id)?;
                ensure!(
                    e.bounds[0] == e.bounds[2] && e.bounds[1] == e.bounds[3],
                    "MISSING_STOP_ANCHOR · GTFS 导出需要有明确点几何的车站"
                );
                stop_rows.push(vec![
                    s.station_id.clone(),
                    e.name,
                    e.bounds[1].to_string(),
                    e.bounds[0].to_string(),
                ]);
            }
            time_rows.push(vec![
                run.id.clone(),
                hms(s.arrival_time_s.or(s.departure_time_s)),
                hms(s.departure_time_s.or(s.arrival_time_s)),
                s.station_id.clone(),
                (s.sequence + 1).to_string(),
            ]);
        }
    }
    for (name, rows) in [
        ("routes.txt", route_rows),
        ("trips.txt", trip_rows),
        ("calendar_dates.txt", calendar_rows),
        ("stops.txt", stop_rows),
        ("stop_times.txt", time_rows),
    ] {
        write(name, csv_bytes(rows)?)?;
    }
    Ok(zip.finish()?.into_inner())
}
pub(super) fn export(
    db: &Connection,
    canonical: &Connection,
    format: &str,
    ids: Option<&[String]>,
) -> Result<Value> {
    let runs = if let Some(ids) = ids {
        ensure!(ids.len() <= 5000, "EXPORT_BUDGET · 导出车次数超限");
        ids.iter()
            .map(|id| latest_run(db, id))
            .collect::<Result<Vec<_>>>()?
    } else {
        all(
            db,
            "SELECT payload FROM ops_run r WHERE version=(SELECT max(version) FROM ops_run WHERE id=r.id) ORDER BY service_date,id LIMIT 5000",
        )?
    };
    ensure!(!runs.is_empty(), "MISSING_TIMETABLE · 没有可导出的运行实例");
    let ids = runs.iter().map(|r| &r.service_id).collect::<HashSet<_>>();
    let services = ids
        .into_iter()
        .map(|id| service_for(db, id))
        .collect::<Result<Vec<_>>>()?;
    let bundle = Bundle { services, runs };
    let content = match format {
        "json" => serde_json::to_string_pretty(&bundle)?,
        "csv" => {
            let modes = bundle
                .services
                .iter()
                .map(|s| (&s.id, &s.mode))
                .collect::<HashMap<_, _>>();
            let rows = bundle.runs.iter().flat_map(|run| {
                run.stops.iter().map(|s| CsvRow {
                    train_code: run.train_number.clone(),
                    service_date: run.service_date.clone(),
                    mode: modes[&run.service_id].clone(),
                    run_ref: run.id.clone(),
                    station_id: s.station_id.clone(),
                    sequence: s.sequence,
                    arrival_time_s: s.arrival_time_s,
                    departure_time_s: s.departure_time_s,
                    pass_through: s.pass_through,
                    platform_id: s.platform_id.clone(),
                    corridor_id: run.corridor_id.clone(),
                    corridor_version: run.corridor_version,
                    stop_edge_id: s.stop_edge_id.clone(),
                    stop_edge_sequence: s.stop_edge_sequence,
                    stop_offset_m: s.stop_offset_m,
                })
            });
            String::from_utf8(csv_bytes(rows)?)?
        }
        "gtfs" => STANDARD.encode(gtfs(&bundle, canonical)?),
        _ => bail!("INVALID_FORMAT · 支持 JSON、CSV 和 GTFS ZIP"),
    };
    ensure!(content.len() <= LIMIT * 2, "EXPORT_BUDGET · 导出超出容量");
    Ok(json!({"format":format,"content":content}))
}
pub(super) fn demo(
    db: &mut Connection,
    canonical: &Connection,
    pack: &Pack,
    corridor_id: &str,
    version: u32,
    origin: &str,
    end: &str,
    date: &str,
) -> Result<Value> {
    let mut corridor: Corridor = from_row(
        db,
        "SELECT payload FROM ops_corridor WHERE id=? AND version=?",
        params![corridor_id, version],
    )?
    .context("NO_PHYSICAL_PATH · 先保存通道")?;
    let tracks = validate_corridor(canonical, &pack.manifest.snapshot, &mut corridor)?;
    let source = Provenance::demo("本地演示时刻与通道端点锚定，供仿真验证");
    let service = service("RS-演示", &tracks[0].kind, source.clone());
    let first = corridor.edge_refs.first().unwrap();
    let last = corridor.edge_refs.last().unwrap();
    let elapsed = ((last.end_distance_m / 8.0).ceil() as i64).max(120);
    ensure!(elapsed < 3 * 86400, "INVALID_TIMETABLE · 演示通道过长");
    let tx = db.transaction()?;
    let svc = save_service(&tx, service)?;
    let mut results = Vec::new();
    for shift in [0, 60] {
        let bind = |station_id: &str, time, edge: &EdgeRef, offset| Stop {
            station_id: station_id.into(),
            sequence: 0,
            arrival_time_s: Some(time),
            departure_time_s: Some(time),
            pass_through: false,
            platform_id: None,
            station_route_id: None,
            station_track_id: None,
            stop_edge_id: Some(edge.edge_id.clone()),
            stop_edge_sequence: Some(edge.sequence),
            stop_offset_m: Some(offset),
        };
        let mut run = run(
            &svc,
            date,
            vec![
                bind(origin, 8 * 3600 + shift, first, 0.),
                bind(
                    end,
                    8 * 3600 + shift + elapsed,
                    last,
                    last.end_distance_m - last.start_distance_m,
                ),
            ],
            source.clone(),
        );
        run.corridor_id = Some(corridor.id.clone());
        run.corridor_version = Some(corridor.version);
        results.push(save_run(&tx, canonical, &pack.manifest.snapshot, run)?);
    }
    tx.commit()?;
    Ok(json!({"runs":results}))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cross_midnight_time_is_round_tripped() {
        assert_eq!(parse_hms("25:04:02").unwrap(), 90242);
        assert_eq!(hms(Some(90242)), "25:04:02");
        assert!(parse_hms("23:60:00").is_err());
        assert_eq!(
            service_epoch("2025-01-02", "Asia/Shanghai").unwrap(),
            1735747200
        );
    }
}
