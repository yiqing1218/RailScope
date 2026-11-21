use super::*;
use sha2::{Digest, Sha256};

type Aliases = BTreeMap<String, String>;

fn key(value: &Value) -> Result<String> {
    value
        .as_str()
        .map(str::to_owned)
        .or_else(|| value.as_i64().map(|n| n.to_string()))
        .context("缺少旧版对象编号")
}
fn alias(value: &str, category: &str, aliases: &Aliases) -> String {
    aliases
        .get(&format!("{category}:{value}"))
        .or_else(|| aliases.get(value))
        .cloned()
        .unwrap_or_else(|| value.to_owned())
}
fn control(db: &Connection, value: &Value, aliases: &Aliases) -> Result<String> {
    let old = key(value)?;
    let mapped = alias(&old, "node", aliases);
    if identifier(&mapped).is_ok() {
        return Ok(mapped);
    }
    let numeric = old
        .parse::<i64>()
        .context("旧版控制点需要明确的 node: 映射")?;
    let mut sql =
        db.prepare("SELECT id FROM node WHERE source_ref=? AND network='rail' LIMIT 2")?;
    let rows = sql
        .query_map([format!("osm:node:{numeric}")], |r| r.get::<_, String>(0))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    ensure!(
        rows.len() == 1,
        "控制点 {old} 无唯一来源匹配，请填写 node:{old} 映射"
    );
    Ok(rows[0].clone())
}
fn array<'a>(value: &'a Value, key: &str) -> Result<&'a [Value]> {
    value[key]
        .as_array()
        .map(Vec::as_slice)
        .with_context(|| format!("缺少旧版数组 {key}"))
}

fn corridor(
    tx: &Connection,
    canonical: &Connection,
    pack: &Pack,
    raw: &Value,
    source: &Provenance,
    aliases: &Aliases,
) -> Result<Corridor> {
    let old = key(&raw["id"])?;
    let name = raw["name"].as_str().unwrap_or(&old).to_owned();
    let prepared = raw["migration_source"].as_str();
    let legacy_key = format!("legacy-import:{old}");
    if prepared.is_some() && let Some(existing)=from_row(tx,
        "SELECT payload FROM ops_corridor WHERE snapshot=? AND json_extract(payload,'$.source_id')=?
         ORDER BY version DESC LIMIT 1",params![pack.manifest.snapshot,legacy_key])?{
        return Ok(existing);
    }
    if let Some(items) = raw["sequence"].as_array() {
        ensure!(
            (3..=35).contains(&items.len()) && items.len() % 2 == 1,
            "旧版通道编排需要端点与线路交替"
        );
        let mut intent = Vec::new();
        for pair in items.chunks(2) {
            ensure!(pair[0]["kind"] == "endpoint", "旧版通道编排缺少端点");
            let node_id = control(canonical, &pair[0]["node_id"], aliases)?;
            let line_id = if let Some(line) = pair.get(1) {
                ensure!(line["kind"] == "line", "旧版通道编排缺少线路");
                let old = key(&line["line_id"])?;
                let mapped = alias(&old, "line", aliases);
                identifier(&mapped)
                    .with_context(|| format!("线路 {old} 需提供 line:{old} 映射"))?;
                Some(mapped)
            } else {
                None
            };
            intent.push(corridors::ControlStep {
                node_id,
                line_id,
                via_edge: None,
            });
        }
        let value = corridors::resolve(
            pack,
            canonical,
            name,
            "rail",
            intent,
            false,
            &AtomicBool::new(false),
        )?;
        let mut draft: Corridor = serde_json::from_value(value["corridor"].clone())?;
        draft.provenance = source.clone();
        return save_corridor(tx, canonical, pack, draft);
    }
    let refs = array(raw, "path")?
        .iter()
        .enumerate()
        .map(|(sequence, item)| -> Result<EdgeRef> {
            let old = key(&item["edge_id"])?;
            let edge_id = alias(&old, "edge", aliases);
            identifier(&edge_id)
                .with_context(|| format!("旧物理区段 {old} 需提供 edge:{old} 映射"))?;
            let forward = match item["direction"].as_str() {
                Some("forward") => true,
                Some("reverse") => false,
                _ => bail!("旧区段缺少明确方向"),
            };
            Ok(EdgeRef {
                edge_id,
                sequence,
                forward,
                start_distance_m: 0.,
                end_distance_m: 0.,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    save_corridor(
        tx,
        canonical,
        pack,
        Corridor {
            id: String::new(),
            name,
            version: 0,
            snapshot_id: pack.manifest.snapshot.clone(),
            origin_node_id: String::new(),
            destination_node_id: String::new(),
            edge_refs: refs,
            verification_status: "automatic_reference".into(),
            resolution_mode: "manual".into(),
            source_id: legacy_key,
            provenance: Provenance {
                source_url_or_reference: prepared.map_or_else(
                    || source.source_url_or_reference.clone(),
                    |evidence| format!("旧通道 {old} · {evidence}"),
                ),
                ..source.clone()
            },
            warnings: vec!["旧版通道按当前物理图核对，可信度为自动参考".into()],
            candidates: Vec::new(),
            intent: Vec::new(),
        },
    )
}

fn convert(
    tx: &Connection,
    canonical: &Connection,
    pack: &Pack,
    value: &Value,
    date: &str,
    aliases: &Aliases,
) -> Result<Value> {
    let schema = value["schema"].as_str().context("文件缺少旧版 schema")?;
    let rail_file = matches!(
        schema,
        "railscope.rail-corridors.v1"
            | "railscope.rail-corridors.v2"
            | "railscope.rail-plan.v1"
            | "railscope.rail-plan.v2"
    );
    let metro_file = matches!(
        schema,
        "railscope.operating-plan.v1" | "railscope.operating-plan.v2"
    );
    ensure!(rail_file || metro_file, "不支持该旧版交换格式");
    ensure!(
        value["required_capabilities"]
            .as_array()
            .is_none_or(Vec::is_empty),
        "旧版文件包含需要单独确认的运行能力"
    );
    if metro_file {
        ensure!(value["official"] != true, "不能自动认定旧文件的官方时刻");
        ensure!(
            ["cycles", "vehicles"]
                .iter()
                .all(|name| value[*name].as_array().is_none_or(Vec::is_empty)),
            "旧循环或车辆仍须人工映射。请先提取运行实例，再用已映射的往返模板构建循环"
        );
    }
    ensure!(
        value["station_routes"].as_array().is_none_or(Vec::is_empty),
        "需单独审校旧版站内进路"
    );
    let date = value["service_date"].as_str().unwrap_or(date);
    service_epoch(date, "Asia/Shanghai")?;
    let provenance = Provenance {
        source_kind: "user_defined".into(),
        source_url_or_reference: format!("旧版计划导入 · {schema}"),
        source_timestamp: None,
        confidence: None,
        verified_by: None,
    };
    let mut routes = HashMap::new();
    for field in ["corridors", "routes"] {
        if let Some(items) = value[field].as_array() {
            ensure!(items.len() <= 1000, "旧版通道列表超出当前导入预算");
            for item in items {
                let id = key(&item["id"])?;
                ensure!(!routes.contains_key(&id), "旧版通道 ID 重复");
                routes.insert(
                    id,
                    corridor(tx, canonical, pack, item, &provenance, aliases)?,
                );
            }
        }
    }
    let mut imported = 0;
    if let Some(trains) = value["trains"].as_array() {
        ensure!(trains.len() <= 5000, "旧版车次列表超出当前导入预算");
        for train in trains {
            let old = key(&train["id"])?;
            ensure!(
                train["station_paths"].is_null()
                    && train["cycle_id"].is_null()
                    && train["vehicle_id"].is_null(),
                "车次 {old} 需要审校站内径路或车辆引用"
            );
            let path = if metro_file {
                let route = key(&train["line_id"])?;
                let direction = key(&train["direction"])?;
                let mapped = alias(&format!("{route}:{direction}"), "corridor", aliases);
                if identifier(&mapped).is_ok() {
                    Some(from_row(tx,"SELECT payload FROM ops_corridor WHERE id=? AND snapshot=? ORDER BY version DESC LIMIT 1",params![mapped,pack.manifest.snapshot])?
                        .context("明确映射的运行通道不在当前快照")?)
                } else {
                    None
                }
            } else if let Some(r) = train["route_id"].as_str() {
                Some(
                    routes
                        .get(r)
                        .cloned()
                        .with_context(|| format!("旧车次通道 {r} 未解析"))?,
                )
            } else if train["path"].is_array() {
                let value = corridor(tx, canonical, pack, train, &provenance, aliases)?;
                routes.insert(format!("车次:{old}"), value.clone());
                Some(value)
            } else {
                bail!("旧车次缺少共享通道引用")
            };
            let mut stops = Vec::new();
            for (sequence, item) in array(train, "stops")?.iter().enumerate() {
                let from = if metro_file {
                    &item["station_id"]
                } else {
                    &item["node_id"]
                };
                let station_code = key(from)?;
                let station_id = alias(&station_code, "station", aliases);
                station(canonical, &station_id).with_context(|| {
                    format!("车站 {station_code} 需提供 station:{station_code} 映射")
                })?;
                ensure!(
                    item["station_route_id"].is_null() && item["track_change"].is_null(),
                    "旧时刻包含有待审校的站内进路"
                );
                let anchor = if let Some(path) = &path {
                    let node = control(canonical, from, aliases).or_else(|_| {
                        let list = corridors::controls(canonical, &station_id)?;
                        let list = list.as_array().context("未找到站点控制锚点")?;
                        ensure!(list.len() == 1, "站点需要唯一、显式的物理锚点");
                        key(&list[0]["id"])
                    })?;
                    let mut found = None;
                    for part in &path.edge_refs {
                        let segment = track(canonical, &part.edge_id)?;
                        let (start, end) = oriented(&segment, part.forward);
                        if start == node {
                            found = Some((part.edge_id.clone(), part.sequence, 0.));
                            break;
                        }
                        if end == node {
                            found = Some((part.edge_id.clone(), part.sequence, segment.meters));
                            break;
                        }
                    }
                    Some(found.with_context(|| format!("车站 {station_code} 控制点不在通道中"))?)
                } else {
                    None
                };
                let mapped = |field: &str, prefix: &str| -> Result<Option<String>> {
                    item.get(field)
                        .filter(|value| !value.is_null())
                        .map(|value| Ok(alias(&key(value)?, prefix, aliases)))
                        .transpose()
                };
                stops.push(Stop {
                    station_id,
                    sequence,
                    arrival_time_s: item["arrival_s"].as_i64(),
                    departure_time_s: item["departure_s"].as_i64(),
                    pass_through: item["pass_through"].as_bool().unwrap_or(false),
                    platform_id: mapped("platform_id", "platform")?,
                    station_track_id: mapped("station_track_id", "edge")?,
                    station_route_id: None,
                    stop_edge_id: anchor.as_ref().map(|x| x.0.clone()),
                    stop_edge_sequence: anchor.as_ref().map(|x| x.1),
                    stop_offset_m: anchor.map(|x| x.2),
                });
            }
            let svc = save_service(
                tx,
                Service {
                    id: String::new(),
                    train_code: old.clone(),
                    operator: None,
                    mode: if metro_file { "metro" } else { "rail" }.into(),
                    origin_station_id: None,
                    destination_station_id: None,
                    provenance: provenance.clone(),
                },
            )?;
            save_run(
                tx,
                canonical,
                &pack.manifest.snapshot,
                Run {
                    id: String::new(),
                    service_id: svc.id,
                    train_number: old,
                    service_date: date.into(),
                    time_zone: "Asia/Shanghai".into(),
                    plan_version: 0,
                    status: if train["enabled"] == false {
                        "cancelled"
                    } else {
                        "scheduled"
                    }
                    .into(),
                    origin_station_id: None,
                    destination_station_id: None,
                    corridor_id: path.as_ref().map(|c| c.id.clone()),
                    corridor_version: path.as_ref().map(|c| c.version),
                    stops,
                    provenance: provenance.clone(),
                },
            )?;
            imported += 1;
        }
    }
    ensure!(!routes.is_empty() || imported > 0, "旧版文件没有可导入对象");
    Ok(json!({"corridors":routes.len(),"runs":imported,"pending":false}))
}

pub(super) fn import(
    db: &mut Connection,
    canonical: &Connection,
    pack: &Pack,
    content: &str,
    date: &str,
    aliases: &Aliases,
) -> Result<Value> {
    ensure!(
        content.len() <= 16 * 1024 * 1024 && aliases.len() <= 20_000,
        "旧版导入超过容量"
    );
    let doc: Value = serde_json::from_str(content)?;
    let value = doc.get("plan").unwrap_or(&doc);
    let snapshot = &pack.manifest.snapshot;
    let id = format!("{:x}", Sha256::digest(content));
    let tx = db.transaction()?;
    match convert(&tx, canonical, pack, value, date, aliases) {
        Ok(result) => {
            tx.execute(
                "DELETE FROM ops_import_issue WHERE snapshot=? AND id=?",
                params![snapshot, id],
            )?;
            tx.commit()?;
            Ok(result)
        }
        Err(error) => {
            tx.rollback()?;
            let reason = format!("{error:#}");
            db.execute("INSERT INTO ops_import_issue(snapshot,id,schema,reason) VALUES(?,?,?,?) ON CONFLICT(snapshot,id) DO UPDATE SET reason=excluded.reason",
                params![snapshot,id,value["schema"].as_str().unwrap_or("unknown"),reason])?;
            Ok(json!({"corridors":0,"runs":0,"pending":true,"issue":reason}))
        }
    }
}

pub(super) fn issues(db: &Connection, snapshot: &str) -> Result<Value> {
    let mut stmt=db.prepare("SELECT id,schema,reason FROM ops_import_issue WHERE snapshot=? ORDER BY created DESC LIMIT 100")?;
    let rows=stmt.query_map([snapshot],|r|Ok(json!({"id":r.get::<_,String>(0)?,"schema":r.get::<_,String>(1)?,"reason":r.get::<_,String>(2)?})))?;
    Ok(json!(rows.collect::<rusqlite::Result<Vec<_>>>()?))
}
