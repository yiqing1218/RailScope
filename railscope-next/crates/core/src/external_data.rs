//! Versioned, snapshot-bound metadata imports for the workspace database.
use crate::{cartography, catalog, pack::Pack, workspace};
use anyhow::{Context, Result, ensure};
use chrono::NaiveDate;
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, HashSet};

pub const MAX_BYTES: usize = 16 * 1024 * 1024;

#[derive(Serialize)]
pub struct LineTrack {
    edge_id: String,
    from_node_id: String,
    to_node_id: String,
    forward: bool,
    reverse: bool,
    status: String,
    class: String,
    meters: f64,
}

pub fn line_tracks(pack: &Pack, line_id: &str) -> Result<Vec<LineTrack>> {
    let canonical = pack.connection()?;
    ensure!(
        canonical.query_row(
            "SELECT EXISTS(SELECT 1 FROM entity WHERE id=? AND kind='line')",
            [line_id],
            |row| row.get::<_, bool>(0)
        )?,
        "当前快照找不到该 RailScope 线路 ID"
    );
    let mut query = canonical.prepare(
        "SELECT e.id,e.from_node,e.to_node,e.forward,e.reverse,t.status,t.class,e.meters
         FROM line_member m JOIN edge e ON e.id=m.member_id JOIN entity t ON t.id=e.id
         WHERE m.line_id=? AND t.kind IN ('rail','metro') ORDER BY m.sequence,m.position",
    )?;
    let mut rows = query.query([line_id])?;
    let mut seen = HashSet::new();
    let mut tracks = Vec::new();
    while let Some(row) = rows.next()? {
        let edge_id: String = row.get(0)?;
        if seen.insert(edge_id.clone()) {
            tracks.push(LineTrack {
                edge_id,
                from_node_id: row.get(1)?,
                to_node_id: row.get(2)?,
                forward: row.get(3)?,
                reverse: row.get(4)?,
                status: row.get(5)?,
                class: row.get(6)?,
                meters: row.get(7)?,
            });
        }
    }
    Ok(tracks)
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Source {
    reference: String,
    version: String,
    verification_status: String,
    confidence: Option<f64>,
}

impl Source {
    fn validate(&self) -> Result<()> {
        text(&self.reference, 512)?;
        text(&self.version, 120)?;
        ensure!(
            matches!(
                self.verification_status.as_str(),
                "user_asserted" | "verified" | "automatic_reference"
            ) && self.confidence.is_none_or(|c| (0.0..=1.0).contains(&c))
                && (self.verification_status != "automatic_reference" || self.confidence.is_some()),
            "资料核验状态或置信度无效"
        );
        Ok(())
    }
}

#[derive(Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct Profile {
    pub former_names: Vec<String>,
    pub other_names: Vec<String>,
    business: Option<String>,
    technical: Option<String>,
    station_grade: Option<String>,
    rail_type: Option<String>,
    railway_grade: Option<String>,
    urban_system: Option<String>,
    city: Option<String>,
    opened_on: Option<String>,
    speed_kmh: Option<f64>,
    gauge_mm: Option<u32>,
    electrification: Option<String>,
    track_type: Option<String>,
    sections: Vec<Section>,
    extra: BTreeMap<String, String>,
}

#[derive(Clone, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct Section {
    code: String,
    name: String,
    status: String,
    opened_on: Option<String>,
    edge_ids: Vec<String>,
}

fn text(value: &str, limit: usize) -> Result<()> {
    ensure!(
        !value.is_empty() && value.trim() == value && value.chars().count() <= limit,
        "资料字段为空、带首尾空白或超长"
    );
    Ok(())
}

fn choice(value: &Option<String>, allowed: &[&str], field: &str) -> Result<()> {
    ensure!(
        value.as_deref().is_none_or(|v| allowed.contains(&v)),
        "{field} 的值不在模板枚举中"
    );
    Ok(())
}

fn date(value: Option<&str>) -> Result<()> {
    if let Some(value) = value {
        ensure!(
            NaiveDate::parse_from_str(value, "%Y-%m-%d").is_ok_and(|d| d
                .format("%Y-%m-%d")
                .to_string()
                == value
                && !value.starts_with("0000")),
            "开通日期须采用 YYYY-MM-DD"
        );
    }
    Ok(())
}

impl Profile {
    pub fn is_empty(&self) -> bool {
        *self == Self::default()
    }

    pub(crate) fn validate(&self, canonical: &Connection, id: &str, kind: &str) -> Result<()> {
        if self.is_empty() {
            return Ok(());
        }
        ensure!(
            matches!(kind, "station" | "line" | "rail" | "metro"),
            "此对象类型不支持资料扩展"
        );
        ensure!(
            kind == "station"
                || (self.business.is_none()
                    && self.technical.is_none()
                    && self.station_grade.is_none()),
            "运输业务、技术作业和车站等级只能填在车站上"
        );
        ensure!(
            kind == "line"
                || (self.rail_type.is_none()
                    && self.railway_grade.is_none()
                    && self.sections.is_empty()),
            "铁路等级、线路类型和分期开通区段只能填在线路上"
        );
        ensure!(
            matches!(kind, "rail" | "metro") || self.track_type.is_none(),
            "轨道用途只能填在物理轨道上"
        );
        choice(
            &self.business,
            &["passenger", "freight", "mixed", "non_business"],
            "business",
        )?;
        choice(
            &self.technical,
            &["marshalling", "district", "intermediate"],
            "technical",
        )?;
        choice(
            &self.station_grade,
            &["special", "1", "2", "3", "4", "5"],
            "station_grade",
        )?;
        choice(
            &self.rail_type,
            &[
                "high_speed",
                "intercity",
                "mixed_traffic",
                "heavy_haul",
                "suburban",
                "other",
            ],
            "rail_type",
        )?;
        choice(
            &self.railway_grade,
            &["I", "II", "III", "IV"],
            "railway_grade",
        )?;
        ensure!(
            self.railway_grade.is_none() || self.rail_type.as_deref() == Some("mixed_traffic"),
            "I–IV 级用于客货共线铁路，请同时设置 rail_type= mixed_traffic"
        );
        choice(
            &self.urban_system,
            &[
                "metro",
                "light_rail",
                "suburban_rapid",
                "maglev",
                "straddle_monorail",
                "suspended_monorail",
                "automated_guideway",
                "tram",
                "guided_rubber_tire",
                "electronic_guided_rubber_tire",
                "other",
            ],
            "urban_system",
        )?;
        choice(
            &self.electrification,
            &["overhead", "third_rail", "none", "other"],
            "electrification",
        )?;
        choice(
            &self.track_type,
            &[
                "main",
                "arrival_departure",
                "siding",
                "yard",
                "depot",
                "crossover",
                "other",
            ],
            "track_type",
        )?;
        if let Some(city) = &self.city {
            text(city, 80)?;
        }
        date(self.opened_on.as_deref())?;
        ensure!(
            self.speed_kmh.is_none_or(|v| v > 0.0 && v <= 1000.0)
                && self.gauge_mm.is_none_or(|v| (100..=5000).contains(&v)),
            "速度或轨距超出范围"
        );
        for names in [&self.former_names, &self.other_names] {
            ensure!(
                names.len() <= 32 && names.iter().collect::<HashSet<_>>().len() == names.len(),
                "曾用名或其他用名重复或超过 32 个"
            );
            for name in names {
                text(name, 120)?;
            }
        }
        ensure!(
            self.extra.len() <= 40 && self.sections.len() <= 64,
            "扩展属性或线路区段数量超限"
        );
        for (key, value) in &self.extra {
            text(key, 80)?;
            text(value, 1000)?;
        }
        if self.sections.is_empty() {
            return Ok(());
        }
        let mut codes = HashSet::new();
        let mut member = canonical.prepare_cached(
            "SELECT EXISTS(SELECT 1 FROM line_member m JOIN edge e ON e.id=m.member_id
             JOIN entity track ON track.id=e.id
             WHERE m.line_id=? AND e.id=? AND track.kind IN ('rail','metro'))",
        )?;
        for section in &self.sections {
            text(&section.code, 80)?;
            text(&section.name, 120)?;
            date(section.opened_on.as_deref())?;
            ensure!(
                codes.insert(&section.code)
                    && matches!(
                        section.status.as_str(),
                        "operating" | "construction" | "planned" | "disused" | "unknown"
                    )
                    && (1..=20000).contains(&section.edge_ids.len()),
                "区段编号重复、状态无效或物理边数量超限"
            );
            let mut edges = HashSet::new();
            for edge in &section.edge_ids {
                ensure!(
                    edges.insert(edge)
                        && member.query_row(params![id, edge], |row| row.get::<_, bool>(0))?,
                    "区段 {} 含重复物理边或不属于该线路的 RailScope 边 ID：{edge}",
                    section.code
                );
            }
        }
        Ok(())
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Document {
    schema_version: u32,
    snapshot_id: String,
    source: Source,
    records: Vec<Record>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Record {
    entity_id: String,
    kind: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    reference: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    source: Option<Source>,
    changes: Map<String, Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    original: Option<Map<String, Value>>,
}

#[derive(Deserialize)]
pub struct ExportFilter {
    pub kind: String,
    #[serde(default)]
    pub query: String,
    pub cursor: Option<String>,
    pub parent: Option<String>,
}

#[derive(Serialize)]
pub struct Export {
    pub content: String,
    pub count: usize,
    pub next: Option<String>,
}

const COMMON_FIELDS: &[&str] = &[
    "name",
    "designation",
    "operator",
    "category",
    "group",
    "note",
];

pub fn export(db: &Connection, pack: &Pack, filter: ExportFilter) -> Result<Export> {
    ensure!(
        matches!(filter.kind.as_str(), "station" | "line" | "rail" | "metro"),
        "不支持的对象资料类型"
    );
    let snapshot = &pack.manifest.snapshot;
    let canonical = pack.connection()?;
    let mut page = catalog::list(
        pack,
        catalog::Filter {
            kind: Some(filter.kind.clone()),
            query: filter.query,
            cursor: filter.cursor,
            parent: filter.parent,
            limit: 100,
            ..Default::default()
        },
    )?;
    if page.items.is_empty() {
        return Ok(Export {
            content: String::new(),
            count: 0,
            next: None,
        });
    }
    let mut document = Document {
        schema_version: 1,
        snapshot_id: snapshot.clone(),
        source: Source {
            reference: "RailScope 导出后人工回填".into(),
            version: snapshot.clone(),
            verification_status: "user_asserted".into(),
            confidence: None,
        },
        records: Vec::new(),
    };
    for item in &page.items {
        let stored = get_override(db, snapshot, &item.id)?;
        let (changes, reference) = editable_fields(&canonical, &item.id, &item.kind, &stored)?;
        document.records.push(Record {
            entity_id: item.id.clone(),
            kind: item.kind.clone(),
            reference: Some(reference),
            source: None,
            original: Some(changes.clone()),
            changes,
        });
    }
    loop {
        let content = serde_json::to_string_pretty(&document)?;
        if content.len() <= MAX_BYTES {
            return Ok(Export {
                content,
                count: document.records.len(),
                next: page.next,
            });
        }
        ensure!(document.records.len() > 1, "单条对象资料超过导出大小限制");
        document.records.truncate(document.records.len() / 2);
        page.next = document
            .records
            .last()
            .map(|record| record.entity_id.clone());
    }
}

fn get_override(db: &Connection, snapshot: &str, id: &str) -> Result<workspace::Override> {
    Ok(workspace::payload(db, snapshot, id)?
        .map(|payload| serde_json::from_str(&payload))
        .transpose()?
        .unwrap_or_default())
}

fn editable_fields(
    canonical: &Connection,
    id: &str,
    kind: &str,
    stored: &workspace::Override,
) -> Result<(Map<String, Value>, Value)> {
    let (name, class, status, tags, verified): (String, String, String, String, bool) = canonical
        .query_row(
        "SELECT e.name,e.class,e.status,s.tags,e.verification='verified'
         FROM entity e JOIN source_object s ON s.ref=e.source_ref WHERE e.id=?",
        [id],
        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
    )?;
    let tags: Value = serde_json::from_str(&tags)?;
    let mut reference = json!({"name":name,"class":class,"status":status});
    if !stored.import_sources.is_empty() {
        reference["field_sources"] = json!(stored.import_sources);
    }
    let value = serde_json::to_value(stored)?;
    let populated = |value: &Value| match value {
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(items) => !items.is_empty(),
        value => !value.is_null(),
    };
    let prefer = |stored: &Value, suggested: &Value| {
        [stored, suggested]
            .into_iter()
            .find(|value| populated(value))
            .cloned()
            .unwrap_or(Value::Null)
    };
    let mut fields = Map::new();
    for &key in COMMON_FIELDS {
        let suggested = match key {
            "name" => &reference["name"],
            "designation" => &tags["ref"],
            "operator" => &tags["operator"],
            _ => &Value::Null,
        };
        fields.insert(key.into(), prefer(&value[key], suggested));
    }
    let mut profile = Map::new();
    let mut keys = vec!["former_names", "other_names", "city", "opened_on", "extra"];
    let semantics = serde_json::to_value(cartography::classify(
        &tags, kind, &class, &status, verified,
    ))?;
    if kind == "station" {
        keys.extend(["business", "technical", "station_grade", "urban_system"]);
        fields.insert(
            "associated_lines".into(),
            prefer(&value["associated_lines"], &Value::Null),
        );
    } else {
        keys.extend(["speed_kmh", "gauge_mm", "electrification", "urban_system"]);
    }
    if kind == "line" {
        keys.extend(["rail_type", "railway_grade", "sections"]);
    }
    if matches!(kind, "rail" | "metro") {
        keys.push("track_type");
        let track: Option<Value> = canonical.query_row(
            "SELECT from_node,to_node,forward,reverse,meters FROM edge WHERE id=?", [id],
            |r| Ok(json!({"from_node_id":r.get::<_,String>(0)?,"to_node_id":r.get::<_,String>(1)?,
                "forward":r.get::<_,bool>(2)?,"reverse":r.get::<_,bool>(3)?,"meters":r.get::<_,f64>(4)?})),
        ).optional()?;
        reference["track"] = json!(track);
    }
    for key in keys {
        profile.insert(key.into(), prefer(&value["profile"][key], &semantics[key]));
    }
    fields.insert("profile".into(), json!(profile));
    Ok((fields, reference))
}

fn edited_fields(
    original: &Map<String, Value>,
    changes: &Map<String, Value>,
    current: &Map<String, Value>,
) -> Result<Map<String, Value>> {
    let mut result = Map::new();
    for (key, input) in changes {
        let before = original
            .get(key)
            .context(format!("导出基准缺少字段 {key}"))?;
        if before == input {
            continue;
        }
        if let (Some(before), Some(input), Some(current)) = (
            before.as_object(),
            input.as_object(),
            current.get(key).and_then(Value::as_object),
        ) {
            // Profile fields are independent; each array or custom-property object is one field.
            if key == "profile" {
                let edits = edited_fields(before, input, current)?;
                if !edits.is_empty() {
                    result.insert(key.clone(), json!(edits));
                }
                continue;
            }
        }
        let now = current
            .get(key)
            .with_context(|| format!("未识别的导出字段 {key}"))?;
        if now != input {
            ensure!(now == before, "资料字段 {key} 已修改，请重新导出后回填");
            result.insert(key.clone(), input.clone());
        }
    }
    Ok(result)
}

#[derive(Serialize)]
pub struct Report {
    pub records: usize,
    pub changed: usize,
    pub preview: bool,
}

pub fn import(db: &mut Connection, pack: &Pack, content: &str, preview: bool) -> Result<Report> {
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let result = import_in(&tx, pack, content, preview)?;
    if !preview {
        tx.commit()?;
    }
    Ok(result)
}

pub(crate) fn import_in(
    db: &Connection,
    pack: &Pack,
    content: &str,
    preview: bool,
) -> Result<Report> {
    ensure!(content.len() <= MAX_BYTES, "资料文件超过 16 MiB");
    let document: Document = serde_json::from_str(content.trim_start_matches('\u{feff}'))?;
    let snapshot = &pack.manifest.snapshot;
    ensure!(document.schema_version == 1, "资料 schema_version 须为 1");
    ensure!(
        &document.snapshot_id == snapshot,
        "资料文件须使用当前数据包的 snapshot_id"
    );
    ensure!(
        (1..=10000).contains(&document.records.len()),
        "资料记录数量须为 1–10000"
    );
    document.source.validate()?;
    let canonical = pack.connection()?;
    let mut lookup = canonical.prepare("SELECT kind FROM entity WHERE id=?")?;
    let mut seen = HashSet::new();
    let mut changed = 0;
    for (index, record) in document.records.iter().enumerate() {
        let mut save = || -> Result<bool> {
            let id = &record.entity_id;
            let source = record.source.as_ref().unwrap_or(&document.source);
            source.validate()?;
            let kind: String = lookup
                .query_row([id], |row| row.get(0))
                .optional()?
                .context("当前快照找不到该 RailScope 实体 ID")?;
            ensure!(
                kind == record.kind
                    && matches!(kind.as_str(), "station" | "line" | "rail" | "metro"),
                "对象类型与资料记录不符"
            );
            let old = get_override(db, snapshot, id)?;
            let mut edits = None;
            if let Some(original) = &record.original {
                let (current, _) = editable_fields(&canonical, id, &kind, &old)?;
                let fields = edited_fields(original, &record.changes, &current)?;
                if fields.is_empty() {
                    return Ok(false);
                }
                edits = Some(fields);
            }
            let mut value = serde_json::to_value(&old)?;
            let mut sources = old.import_sources;
            apply_changes(
                value.as_object_mut().unwrap(),
                &mut sources,
                edits.as_ref().unwrap_or(&record.changes),
                source,
            )?;
            let mut merged: workspace::Override = serde_json::from_value(value)?;
            merged.import_sources = sources;
            merged.validate_associations(&canonical, &kind)?;
            merged.profile.validate(&canonical, id, &kind)?;
            workspace::record(db, snapshot, id, &merged, "external_import")
        };
        ensure!(
            seen.insert(&record.entity_id),
            "第 {} 条资料的实体 ID 重复",
            index + 1
        );
        if save().with_context(|| format!("第 {} 条资料 · {}", index + 1, record.entity_id))? {
            changed += 1;
        }
    }
    Ok(Report {
        records: document.records.len(),
        changed,
        preview,
    })
}

fn apply_changes(
    value: &mut Map<String, Value>,
    sources: &mut BTreeMap<String, Source>,
    changes: &Map<String, Value>,
    source: &Source,
) -> Result<()> {
    ensure!(!changes.is_empty(), "changes 至少需要一个字段");
    let patch = |target: &mut Map<String, Value>,
                 key: &str,
                 field: String,
                 input: &Value,
                 sources: &mut BTreeMap<String, Source>| {
        if input.is_null() {
            target.remove(key);
            sources.remove(&field);
        } else {
            target.insert(key.into(), input.clone());
            sources.insert(field, source.clone());
        }
    };
    for (key, input) in changes {
        match key.as_str() {
            "name" | "note" | "group" | "designation" | "operator" | "category"
            | "associated_lines" => {
                patch(value, key, key.clone(), input, sources);
            }
            "profile" if input.is_null() => {
                value.remove(key);
                sources.retain(|field, _| !field.starts_with("profile."));
            }
            "profile" => {
                let fields = input
                    .as_object()
                    .filter(|fields| !fields.is_empty())
                    .context("profile 应包含要更新的字段")?;
                // Validate even null-valued keys so misspelled clear requests fail.
                let defaults = serde_json::to_value(Profile::default())?;
                for field in fields.keys() {
                    ensure!(defaults.get(field).is_some(), "不支持写入资料字段 {field}");
                }
                let target = value
                    .entry(key.clone())
                    .or_insert_with(|| Value::Object(Map::new()))
                    .as_object_mut()
                    .unwrap();
                for (field, input) in fields {
                    patch(target, field, format!("profile.{field}"), input, sources);
                }
            }
            _ => anyhow::bail!("不支持写入字段 {key}"),
        }
    }
    Ok(())
}
