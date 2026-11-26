use super::*;

#[derive(Clone, Deserialize, Serialize)]
pub(super) struct ControlStep {
    pub(super) node_id: String,
    pub(super) line_id: Option<String>,
    pub(super) via_edge: Option<String>,
}

fn label(n: &str, name: Option<String>) -> String {
    name.filter(|v| !v.is_empty())
        .unwrap_or_else(|| format!("物理端点 {}", n.get(..8).unwrap_or(n)))
}

pub(super) fn controls(db: &Connection, entity_id: &str) -> Result<Value> {
    identifier(entity_id)?;
    let mut statement=db.prepare("SELECT n.id,n.longitude,n.latitude,COALESCE(NULLIF(json_extract(src.tags,'$.name:zh'),''),json_extract(src.tags,'$.name')) FROM node n
        JOIN source_object src ON src.ref=n.source_ref WHERE n.network='rail' AND n.id IN (
         SELECT from_node FROM edge WHERE id=?1 UNION SELECT to_node FROM edge WHERE id=?1
         UNION SELECT ?1 UNION SELECT n2.id FROM entity e JOIN node n2 ON n2.source_ref=e.source_ref WHERE e.id=?1
        ) ORDER BY n.id LIMIT 32")?;
    let mut values = statement.query_map([entity_id],|row| {
        let id:String=row.get(0)?;
        Ok(json!({"id":id,"name":label(&id,row.get(3)?),"point":[row.get::<_,f64>(1)?,row.get::<_,f64>(2)?]}))
    })?.collect::<rusqlite::Result<Vec<_>>>()?;
    if values.is_empty()
        && db.query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_schema WHERE name='source_relation_member')",
            [],
            |row| row.get::<_, bool>(0),
        )?
    {
        let mut statement=db.prepare("SELECT DISTINCT n.id,n.longitude,n.latitude,json_extract(src.tags,'$.name')
           FROM entity e JOIN source_relation_member station ON station.member_ref=e.source_ref
           JOIN source_object area ON area.ref=station.relation_ref AND json_extract(area.tags,'$.public_transport')='stop_area'
           JOIN source_relation_member stop ON stop.relation_ref=area.ref AND stop.role IN ('stop','stop_entry_only','stop_exit_only')
           JOIN node n ON n.source_ref=stop.member_ref AND n.network='rail' JOIN source_object src ON src.ref=n.source_ref
           WHERE e.id=? AND json_extract(src.tags,'$.public_transport')='stop_position' ORDER BY n.id LIMIT 32")?;
        values=statement.query_map([entity_id],|row| {
            let id:String=row.get(0)?;
            Ok(json!({"id":id,"name":label(&id,row.get(3)?),"point":[row.get::<_,f64>(1)?,row.get::<_,f64>(2)?]}))
        })?.collect::<rusqlite::Result<Vec<_>>>()?;
    }
    if values.is_empty() {
        if let Some(station) = catalog::by_id(db, entity_id)?.filter(|item| {
            item.kind == "station"
                && matches!(item.class.as_str(), "rail_station" | "metro_station")
        }) {
            let [w, s, e, n] = station.bounds;
            let (x, y) = ((w + e) / 2., (s + n) / 2.);
            let xscale = 111200. * y.to_radians().cos().abs().max(0.1);
            let radius = if station.class == "metro_station" {
                250.
            } else {
                450.
            };
            let mode = if station.class == "metro_station" {
                "metro"
            } else {
                "rail"
            };
            let mut statement = db.prepare(
                "WITH nearby AS (
              SELECT edge.from_node,edge.to_node FROM spatial s JOIN geometry g ON g.rowid=s.rowid
              JOIN entity e ON e.id=g.entity_id JOIN edge ON edge.id=e.id
              WHERE s.max_x>=?1 AND s.min_x<=?2 AND s.max_y>=?3 AND s.min_y<=?4
                AND e.kind=?5 AND e.status='operating' AND e.class!='unknown'
              ), ids AS (SELECT from_node id FROM nearby UNION SELECT to_node FROM nearby)
              SELECT node.id,node.longitude,node.latitude FROM ids JOIN node ON node.id=ids.id",
            )?;
            let mut candidates = statement
                .query_map(
                    params![
                        x - radius / xscale,
                        x + radius / xscale,
                        y - radius / 111200.,
                        y + radius / 111200.,
                        mode
                    ],
                    |row| {
                        let lon: f64 = row.get(1)?;
                        let lat: f64 = row.get(2)?;
                        Ok((
                            ((lon - x) * xscale).hypot((lat - y) * 111200.),
                            row.get::<_, String>(0)?,
                            lon,
                            lat,
                        ))
                    },
                )?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            candidates.retain(|entry| entry.0 <= radius);
            candidates.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
            values.extend(candidates.into_iter().take(24).map(|(meters, id, x, y)| {
                json!({
                    "id":id,"name":station.name,"point":[x,y],"meters":meters,
                    "evidence":"nearby_track_reference","verification_status":"automatic_reference"
                })
            }));
        }
    }
    Ok(json!(values))
}

pub(super) fn lines(db: &Connection, node: &str, mode: &str) -> Result<Value> {
    identifier(node)?;
    ensure!(matches!(mode, "rail" | "metro"), "请选择铁路或上海地铁");
    let mut stmt=db.prepare("SELECT DISTINCT l.id,l.name FROM (
      SELECT id FROM edge WHERE from_node=?1 AND forward=1 UNION SELECT id FROM edge WHERE to_node=?1 AND reverse=1
      ) x JOIN entity e ON e.id=x.id JOIN line_member m ON m.member_id=x.id JOIN entity l ON l.id=m.line_id
      WHERE e.kind=?2 AND e.status='operating' AND e.class!='unknown' ORDER BY l.name,l.id LIMIT 160")?;
    let rows = stmt.query_map(params![node, mode], |row| {
        Ok(json!({"id":row.get::<_,String>(0)?,"name":row.get::<_,String>(1)?}))
    })?;
    Ok(json!(rows.collect::<rusqlite::Result<Vec<_>>>()?))
}

fn destinations(
    db: &Connection,
    node: &str,
    line: &str,
    mode: &str,
    cancel: &AtomicBool,
) -> Result<Value> {
    identifier(node)?;
    identifier(line)?;
    let (_, visited) = graph::from_node(db, node, None, mode, line, "", cancel)?;
    let ids = serde_json::to_string(&visited.keys().collect::<Vec<_>>())?;
    let mut stmt=db.prepare("SELECT n.id,n.longitude,n.latitude,COALESCE(NULLIF(json_extract(s.tags,'$.name:zh'),''),json_extract(s.tags,'$.name'),json_extract(s.tags,'$.ref'))
      FROM json_each(?) j JOIN node n ON n.id=j.value JOIN source_object s ON s.ref=n.source_ref
      WHERE COALESCE(json_extract(s.tags,'$.railway'),'')!='railway_crossing'")?;
    let rows = stmt.query_map([ids], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, f64>(1)?,
            row.get::<_, f64>(2)?,
            row.get::<_, Option<String>>(3)?,
        ))
    })?;
    let mut found = rows.collect::<rusqlite::Result<Vec<_>>>()?;
    found.retain(|v| v.0 != node);
    found.sort_by(|a, b| {
        b.3.is_some()
            .cmp(&a.3.is_some())
            .then_with(|| visited[&a.0].total_cmp(&visited[&b.0]))
            .then_with(|| a.0.cmp(&b.0))
    });
    Ok(json!(found.into_iter().take(1600).map(|(id,x,y,name)|json!({"id":id,"name":label(&id,name),"point":[x,y],"meters":visited[&id]})).collect::<Vec<_>>()))
}

pub(super) fn resolve(
    pack: &Pack,
    db: &Connection,
    name: String,
    mode: &str,
    steps: Vec<ControlStep>,
    strict: bool,
    cancel: &AtomicBool,
) -> Result<Value> {
    text(&name, 120)?;
    ensure!(
        (2..=18).contains(&steps.len()) && matches!(mode, "rail" | "metro"),
        "INVALID_INPUT · 需要 2–18 个控制端点和有效制式"
    );
    ensure!(
        steps
            .last()
            .is_some_and(|step| step.line_id.is_none() && step.via_edge.is_none()),
        "最后一行只能填写终点"
    );
    let mut edges = Vec::new();
    for (i, pair) in steps.windows(2).enumerate() {
        let row = &pair[0];
        let from = row.node_id.as_str();
        let to = pair[1].node_id.as_str();
        let line = row
            .line_id
            .as_deref()
            .with_context(|| format!("第 {} 行请选择驶向下一端点的线路", i + 1))?;
        for key in [from, to, line] {
            identifier(key)?;
        }
        ensure!(from != to, "第 {} 行的起终控制端点相同", i + 1);
        let find = |a, b, excluded| {
            graph::from_node(db, a, Some(b), mode, line, excluded, cancel).map(|v| v.0)
        };
        let path = if let Some(via) = &row.via_edge {
            let track = track(db, via)?;
            ensure!(
                db.query_row(
                    "SELECT EXISTS(SELECT 1 FROM line_member WHERE line_id=? AND member_id=?)",
                    params![line, via],
                    |r| r.get::<_, bool>(0)
                )?,
                "精确区段不属于所选线路"
            );
            let mut choices = Vec::new();
            for direction in [true, false] {
                if usable(&track, mode, direction).is_err() {
                    continue;
                }
                let (entry, exit) = oriented(&track, direction);
                if let (Ok(mut a), Ok(b)) = (find(from, entry, via), find(exit, to, via)) {
                    a.push((via.clone(), direction));
                    a.extend(b);
                    choices.push(a);
                }
            }
            ensure!(
                !strict || choices.len() <= 1,
                "第 {} 行指定区段有两个方向，请缩小控制端点范围",
                i + 1
            );
            choices
                .into_iter()
                .min_by_key(Vec::len)
                .context("指定区段与两个控制端点无法连续连接")?
        } else {
            let route = find(from, to, "")
                .with_context(|| format!("NO_PHYSICAL_PATH · 第 {} 行找不到显式连接", i + 1))?;
            if strict {
                ensure!(
                    route.len() <= 64,
                    "PATH_AMBIGUOUS · 第 {} 行超过严格校验预算，请插入控制点",
                    i + 1
                );
                for (edge, _) in &route {
                    match find(from, to, edge) {
                        Ok(_) => {
                            bail!("PATH_AMBIGUOUS · 第 {} 行有多个候选，请指定物理区段", i + 1)
                        }
                        Err(error) if !error.to_string().contains("未找到显式") => {
                            return Err(error);
                        }
                        Err(_) => {}
                    }
                }
            }
            route
        };
        edges.extend(path);
    }
    let mut corridor = Corridor {
        id: String::new(),
        version: 0,
        name,
        snapshot_id: pack.manifest.snapshot.clone(),
        origin_node_id: String::new(),
        destination_node_id: String::new(),
        edge_refs: edges
            .into_iter()
            .enumerate()
            .map(|(sequence, (edge_id, forward))| EdgeRef {
                edge_id,
                sequence,
                forward,
                start_distance_m: 0.,
                end_distance_m: 0.,
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
        warnings: vec!["当前为显式接轨的拓扑参考径路".into()],
        candidates: Vec::new(),
        intent: steps,
    };
    preview(db, &pack.manifest.snapshot, &mut corridor)
}

pub(super) fn preview(db: &Connection, snapshot: &str, corridor: &mut Corridor) -> Result<Value> {
    let tracks = validate_corridor(db, snapshot, corridor)?;
    validate_intent(db, corridor)?;
    let geometries = corridor
        .edge_refs
        .iter()
        .map(|item| catalog::geometry(db, &item.edge_id))
        .collect::<Result<Vec<_>>>()?;
    Ok(
        json!({"corridor":corridor,"mode":tracks[0].kind,"geometry":{"type":"GeometryCollection","geometries":geometries.into_iter().flatten().collect::<Vec<_>>()}}),
    )
}

pub(super) fn validate_intent(db: &Connection, corridor: &Corridor) -> Result<()> {
    if corridor.intent.is_empty() {
        return Ok(());
    }
    ensure!(
        (2..=18).contains(&corridor.intent.len())
            && corridor.intent[0].node_id == corridor.origin_node_id
            && corridor
                .intent
                .last()
                .is_some_and(|step| step.node_id == corridor.destination_node_id
                    && step.line_id.is_none()
                    && step.via_edge.is_none()),
        "编排控制端点与物理路径不一致"
    );
    let mut membership = db.prepare_cached(
        "SELECT EXISTS(SELECT 1 FROM line_member WHERE line_id=? AND member_id=?)",
    )?;
    let mut refs = corridor.edge_refs.iter();
    for pair in corridor.intent.windows(2) {
        let from = &pair[0];
        let to = &pair[1];
        let line = from.line_id.as_deref().context("缺少驶向下一端点的线路")?;
        let mut reached = false;
        let mut via_seen = from.via_edge.is_none();
        for item in refs.by_ref() {
            ensure!(
                membership.query_row(params![line, item.edge_id], |r| r.get::<_, bool>(0))?,
                "物理区段与编排线路不一致"
            );
            via_seen |= from.via_edge.as_ref() == Some(&item.edge_id);
            let track = track(db, &item.edge_id)?;
            if oriented(&track, item.forward).1 == to.node_id {
                reached = true;
                break;
            }
        }
        ensure!(reached && via_seen, "编排端点或指定区段未经过");
    }
    ensure!(refs.next().is_none(), "编排结束后还有未归属的物理区段");
    Ok(())
}

#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case")]
enum Query {
    Controls {
        id: String,
    },
    Lines {
        node: String,
        mode: String,
    },
    Destinations {
        node: String,
        line: String,
        mode: String,
    },
    Resolve {
        name: String,
        mode: String,
        steps: Vec<ControlStep>,
        strict: bool,
    },
    Preview {
        corridor: Corridor,
    },
    Reverse {
        corridor: Corridor,
    },
}

pub fn query(pack: &Pack, request: Value, cancel: &AtomicBool) -> Result<Value> {
    let db = pack.connection()?;
    let snapshot = &pack.manifest.snapshot;
    match serde_json::from_value(request)? {
        Query::Controls { id } => controls(&db, &id),
        Query::Lines { node, mode } => lines(&db, &node, &mode),
        Query::Destinations { node, line, mode } => destinations(&db, &node, &line, &mode, cancel),
        Query::Resolve {
            name,
            mode,
            steps,
            strict,
        } => resolve(pack, &db, name, &mode, steps, strict, cancel),
        Query::Preview { mut corridor } => preview(&db, snapshot, &mut corridor),
        Query::Reverse { mut corridor } => {
            corridor.id.clear();
            corridor.version = 0;
            if let Some(first) = corridor.intent.first() {
                let last = ControlStep {
                    node_id: first.node_id.clone(),
                    line_id: None,
                    via_edge: None,
                };
                corridor.intent = corridor
                    .intent
                    .windows(2)
                    .rev()
                    .map(|leg| ControlStep {
                        node_id: leg[1].node_id.clone(),
                        line_id: leg[0].line_id.clone(),
                        via_edge: leg[0].via_edge.clone(),
                    })
                    .chain(std::iter::once(last))
                    .collect();
            }
            corridor.name = format!("{} · 反向", corridor.name);
            corridor.verification_status = "automatic_reference".into();
            corridor.resolution_mode = "manual".into();
            corridor.edge_refs.reverse();
            for (index, edge) in corridor.edge_refs.iter_mut().enumerate() {
                edge.sequence = index;
                edge.forward = !edge.forward;
            }
            corridor.origin_node_id.clear();
            corridor.destination_node_id.clear();
            preview(&db, snapshot, &mut corridor)
        }
    }
}
