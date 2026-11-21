use super::*;

#[derive(Clone, Serialize, Deserialize)]
pub(super) struct Cycle {
    id: String,
    name: String,
    outbound_id: String,
    outbound_version: u32,
    return_id: String,
    return_version: u32,
    service_date: String,
    first_departure_s: i64,
    end_s: i64,
    turnaround_s: i64,
    launch_gap_s: i64,
    vehicles: usize,
    #[serde(default)]
    trainsets: Vec<String>,
}

fn first(run: &Run) -> i64 {
    let stop = &run.stops[0];
    stop.departure_time_s.or(stop.arrival_time_s).unwrap_or(0)
}

fn last(run: &Run) -> i64 {
    let stop = run.stops.last().unwrap();
    stop.departure_time_s.or(stop.arrival_time_s).unwrap_or(0)
}

fn shifted(template: &Run, date: &str, start: i64, provenance: &Provenance) -> Run {
    let mut run = template.clone();
    let displacement = start - first(template);
    run.id.clear();
    run.plan_version = 0;
    run.service_date = date.into();
    run.provenance = provenance.clone();
    for stop in &mut run.stops {
        stop.arrival_time_s = stop.arrival_time_s.map(|t| t + displacement);
        stop.departure_time_s = stop.departure_time_s.map(|t| t + displacement);
    }
    run
}

pub(super) fn generate(
    db: &mut Connection,
    canonical: &Connection,
    snapshot: &str,
    mut cycle: Cycle,
) -> Result<Value> {
    text(&cycle.name, 120)?;
    new_id(&mut cycle.id)?;
    ensure!(
        (1..=100).contains(&cycle.vehicles)
            && (30..=14400).contains(&cycle.turnaround_s)
            && (30..=14400).contains(&cycle.launch_gap_s)
            && (0..=604800).contains(&cycle.first_departure_s)
            && (cycle.first_departure_s + 1..=604800).contains(&cycle.end_s),
        "INVALID_CYCLE · 检查运营窗、折返、投入间隔和车辆数量"
    );
    ensure!(
        cycle.trainsets.len() <= cycle.vehicles
            && cycle.trainsets.iter().collect::<HashSet<_>>().len() == cycle.trainsets.len(),
        "INVALID_CYCLE · 车组必须唯一且数量不得超过投放车辆"
    );
    let tx = db.transaction()?;
    let load = |id: &str, version: u32| -> Result<Run> {
        let run: Run = from_row(
            &tx,
            "SELECT payload FROM ops_run WHERE id=? AND version=?",
            params![id, version],
        )?
        .context("循环所选模板版本不存在")?;
        let service = service_for(&tx, &run.service_id)?;
        ensure!(
            service.mode == "metro"
                && run.status == "scheduled"
                && run.stops.len() > 1
                && run.corridor_id.is_some()
                && run.stops.iter().all(|stop| stop.stop_edge_id.is_some()),
            "循环需要上海地铁的有径路、有锚点且未停运车次模板"
        );
        Ok(run)
    };
    let out = load(&cycle.outbound_id, cycle.outbound_version)?;
    let back = load(&cycle.return_id, cycle.return_version)?;
    ensure!(
        out.id != back.id
            && out.origin_station_id == back.destination_station_id
            && out.destination_station_id == back.origin_station_id
            && out.origin_station_id != out.destination_station_id,
        "INVALID_CYCLE · 往返模板终点和起点必须使用相同稳定车站 ID"
    );
    let provenance = Provenance {
        source_kind: "user_defined".into(),
        source_url_or_reference: format!("工作区计划循环 · {}", cycle.name),
        source_timestamp: None,
        confidence: None,
        verified_by: None,
    };
    let mut count = 0;
    for car in 0..cycle.vehicles {
        let mut time = cycle.first_departure_s + car as i64 * cycle.launch_gap_s;
        let mut outward = true;
        loop {
            let template = if outward { &out } else { &back };
            if time + last(template) - first(template) > cycle.end_s {
                break;
            }
            ensure!(
                count < 1000,
                "INVALID_CYCLE · 超过 1,000 趟车次，请缩小生成时间窗"
            );
            let run = save_run(
                &tx,
                canonical,
                snapshot,
                shifted(template, &cycle.service_date, time, &provenance),
            )?;
            if let Some(trainset) = cycle.trainsets.get(car) {
                validate_assignment(&tx, &run, trainset)?;
                tx.execute(
                    "INSERT INTO ops_assignment VALUES(?,?,?)",
                    params![run.id, run.plan_version, trainset],
                )?;
            }
            count += 1;
            outward = !outward;
            time = last(&run) + cycle.turnaround_s;
        }
    }
    ensure!(count > 0, "INVALID_CYCLE · 运营窗内放不下一趟完整运行");
    tx.execute(
        "INSERT INTO ops_cycle VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
        params![cycle.id, serde_json::to_string(&cycle)?],
    )?;
    tx.commit()?;
    Ok(json!({"template":cycle,"runs":count}))
}
