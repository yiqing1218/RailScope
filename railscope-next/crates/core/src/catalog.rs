use crate::pack::{Pack, valid_bounds};
use anyhow::{Result, ensure};
use geozero::{ToJson, wkb::Wkb};
use rusqlite::{Connection, OptionalExtension, Row, params};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use unicode_normalization::UnicodeNormalization;
mod site;

const SUMMARY: &str = "e.id, e.render_id, e.name, e.kind, e.class, e.status,
                       g.min_x, g.min_y, g.max_x, g.max_y";
const ENTITY: &str = "entity e JOIN geometry g ON g.entity_id = e.id";

#[derive(Clone, Serialize)]
pub struct Summary {
    pub id: String,
    pub render_id: i64,
    pub name: String,
    pub kind: String,
    pub class: String,
    pub status: String,
    pub bounds: [f64; 4],
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workspace_category: Option<String>,
}

#[derive(Serialize)]
pub struct Entity {
    #[serde(flatten)]
    pub summary: Summary,
    pub source_ref: String,
    pub source_version: u32,
    pub verification: String,
    pub confidence: String,
    pub tags: Value,
    pub semantics: crate::cartography::Semantics,
    pub memberships: Vec<Summary>,
    pub station_links: Vec<StationLink>,
    pub site: Option<site::Site>,
    pub has_geometry: bool,
    pub has_edge: bool,
}

#[derive(Serialize)]
pub struct StationLink {
    line: Summary,
    relation: String,
    evidence: String,
    source_ref: String,
}

#[derive(Clone, Default, Deserialize)]
#[serde(default)]
pub struct Filter {
    pub query: String,
    pub kind: Option<String>,
    pub classes: Vec<String>,
    pub parent: Option<String>,
    pub bbox: Option<[f64; 4]>,
    pub cursor: Option<String>,
    pub limit: u32,
}

#[derive(Serialize)]
pub struct Page {
    pub items: Vec<Summary>,
    pub next: Option<String>,
}

fn summary(row: &Row<'_>) -> rusqlite::Result<Summary> {
    Ok(Summary {
        id: row.get(0)?,
        render_id: row.get(1)?,
        name: row.get(2)?,
        kind: row.get(3)?,
        class: row.get(4)?,
        status: row.get(5)?,
        bounds: [row.get(6)?, row.get(7)?, row.get(8)?, row.get(9)?],
        workspace_category: None,
    })
}

pub fn by_id(db: &Connection, id: &str) -> Result<Option<Summary>> {
    let sql = format!("SELECT {SUMMARY} FROM {ENTITY} WHERE e.id=?");
    Ok(db.query_row(&sql, [id], summary).optional()?)
}

pub fn list(pack: &Pack, filter: Filter) -> Result<Page> {
    ensure!(filter.query.chars().count() <= 128, "查询长度超过限制");
    ensure!(
        filter.classes.len() <= 16 && filter.classes.iter().all(|class| class.len() <= 48),
        "目录子类超出范围"
    );
    ensure!(filter.bbox.is_none_or(valid_bounds), "无效的查询范围");
    let connection = pack.connection()?;
    let fts = filter.query.nfkc().collect::<String>().to_lowercase();
    let entity_id = uuid::Uuid::parse_str(fts.trim())
        .ok()
        .map(|id| id.to_string());
    let source_ref = fts.trim().strip_prefix("osm:").and_then(|ref_| {
        let (kind, id) = ref_.split_once(':')?;
        (matches!(kind, "node" | "way" | "relation") && id.parse::<i64>().is_ok())
            .then_some(fts.trim())
    });
    let query = fts
        .split(|ch: char| !ch.is_alphanumeric())
        .filter(|word| !word.is_empty())
        .map(|word| format!("\"{word}\"*"))
        .collect::<Vec<_>>()
        .join(" AND ");
    if !filter.query.trim().is_empty() && query.is_empty() {
        return Ok(Page {
            items: vec![],
            next: None,
        });
    }
    let limit = if filter.limit == 0 {
        80
    } else {
        filter.limit.min(150)
    };
    let mut sql = format!("SELECT {SUMMARY} FROM {ENTITY} ");
    if !query.is_empty() && source_ref.is_none() && entity_id.is_none() {
        sql.push_str("JOIN entity_search ON entity_search.entity_id = e.id ");
    }
    if filter.bbox.is_some() {
        sql.push_str("JOIN spatial s ON s.rowid = g.rowid ");
    }
    sql.push_str(
        "WHERE (:cursor IS NULL OR (e.name,e.id) > (SELECT name,id FROM entity WHERE id=:cursor)) ",
    );
    if filter.kind.is_some() {
        sql.push_str("AND e.kind = :kind ");
    }
    if !filter.classes.is_empty() {
        sql.push_str("AND e.class IN (SELECT value FROM json_each(:classes)) ");
    }
    if source_ref.is_some() {
        sql.push_str("AND e.source_ref=:source_ref ");
    } else if entity_id.is_some() {
        sql.push_str("AND e.id=:entity_id ");
    } else if !query.is_empty() {
        sql.push_str("AND entity_search MATCH :query ");
    }
    if filter.bbox.is_some() {
        sql.push_str("AND s.max_x >= :west AND s.max_y >= :south AND s.min_x <= :east AND s.min_y <= :north ");
    }
    if filter.parent.is_some() {
        sql.push_str("AND e.id IN (SELECT member_id FROM line_member WHERE line_id=:parent) ");
    }
    sql.push_str("ORDER BY e.name,e.id LIMIT :limit");
    let mut statement = connection.prepare(&sql)?;
    statement.raw_bind_parameter(
        statement.parameter_index(":cursor")?.unwrap(),
        &filter.cursor,
    )?;
    statement.raw_bind_parameter(statement.parameter_index(":limit")?.unwrap(), limit + 1)?;
    if let Some(kind) = filter.kind {
        statement.raw_bind_parameter(statement.parameter_index(":kind")?.unwrap(), kind)?;
    }
    if !filter.classes.is_empty() {
        statement.raw_bind_parameter(
            statement.parameter_index(":classes")?.unwrap(),
            serde_json::to_string(&filter.classes)?,
        )?;
    }
    if let Some(source_ref) = source_ref {
        statement.raw_bind_parameter(
            statement.parameter_index(":source_ref")?.unwrap(),
            source_ref,
        )?;
    } else if let Some(entity_id) = entity_id {
        statement
            .raw_bind_parameter(statement.parameter_index(":entity_id")?.unwrap(), entity_id)?;
    } else if !query.is_empty() {
        statement.raw_bind_parameter(statement.parameter_index(":query")?.unwrap(), query)?;
    }
    if let Some(parent) = filter.parent {
        statement.raw_bind_parameter(statement.parameter_index(":parent")?.unwrap(), parent)?;
    }
    if let Some(bbox) = filter.bbox {
        for (parameter, value) in [":west", ":south", ":east", ":north"].into_iter().zip(bbox) {
            statement.raw_bind_parameter(statement.parameter_index(parameter)?.unwrap(), value)?;
        }
    }
    let mut rows = statement.raw_query();
    let mut items = Vec::new();
    while let Some(row) = rows.next()? {
        items.push(summary(row)?);
    }
    let next = if items.len() > limit as usize {
        items.pop();
        items.last().map(|item| item.id.clone())
    } else {
        None
    };
    Ok(Page { items, next })
}

pub fn raw_source(pack: &Pack, reference: &str, offset: u32) -> Result<Value> {
    let mut parts = reference.split(':');
    ensure!(parts.next() == Some("osm"), "无效 OSM 对象引用");
    let kind = parts.next().unwrap_or_default();
    let id = parts.next().and_then(|value| value.parse::<i64>().ok());
    let Some(id) = id else {
        anyhow::bail!("无效 OSM 对象编号")
    };
    ensure!(
        parts.next().is_none() && matches!(kind, "node" | "way" | "relation"),
        "无效 OSM 对象类型"
    );
    let db = pack.connection()?;
    let rec: Option<(u32, String)> = db
        .query_row(
            "SELECT source_version,tags FROM source_object WHERE ref=?",
            [reference],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?;
    let mut result = serde_json::json!({"ref":reference,"kind":kind,"osm_id":id,"snapshot":pack.manifest.snapshot,
      "source":pack.manifest.source_file,"license":pack.manifest.license,"complete":pack.manifest.source_schema>=3,"rows":[]});
    if let Some((version, tags)) = rec {
        result["version"] = serde_json::json!(version);
        result["tags"] = serde_json::from_str(&tags)?;
    }
    if pack.manifest.source_schema < 3 {
        return Ok(result);
    }
    match kind {
        "node" => {
            if let Some((x, y, v, t)) = db
                .query_row(
                    "SELECT longitude,latitude,source_version,tags FROM source_node WHERE osm_id=?",
                    [id],
                    |r| {
                        Ok((
                            r.get::<_, f64>(0)?,
                            r.get::<_, f64>(1)?,
                            r.get::<_, u32>(2)?,
                            r.get::<_, String>(3)?,
                        ))
                    },
                )
                .optional()?
            {
                result["point"] = serde_json::json!([x, y]);
                result["version"] = serde_json::json!(v);
                result["tags"] = serde_json::from_str(&t)?;
            } else {
                result["complete"] = Value::Bool(false);
            }
        }
        "way" => {
            let mut stmt=db.prepare("SELECT CAST(item.key AS INTEGER), item.value, n.longitude,n.latitude
                FROM source_way w,json_each(w.node_ids) item LEFT JOIN source_node n ON n.osm_id=item.value
                WHERE w.osm_id=? LIMIT 101 OFFSET ?")?;
            let mut rows = stmt.query(params![id, offset])?;
            let mut block = vec![];
            while let Some(r) = rows.next()? {
                block.push(serde_json::json!({"ordinal":r.get::<_,u32>(0)?,
                "ref":format!("osm:node:{}",r.get::<_,i64>(1)?),
                "longitude":r.get::<_,Option<f64>>(2)?,"latitude":r.get::<_,Option<f64>>(3)?}));
            }
            result["next"] =
                serde_json::json!((block.len() > 100).then(|| offset.saturating_add(100)));
            block.truncate(100);
            result["rows"] = serde_json::json!(block);
        }
        _ => {
            let mut stmt = db.prepare(
                "SELECT ordinal,member_ref,role FROM source_relation_member WHERE relation_ref=?
                ORDER BY ordinal LIMIT 101 OFFSET ?",
            )?;
            let mut rows = stmt.query(params![reference, offset])?;
            let mut block = vec![];
            while let Some(r) = rows.next()? {
                block.push(serde_json::json!({"ordinal":r.get::<_,u32>(0)?,
                "ref":r.get::<_,String>(1)?,"role":r.get::<_,String>(2)?}));
            }
            result["next"] =
                serde_json::json!((block.len() > 100).then(|| offset.saturating_add(100)));
            block.truncate(100);
            result["rows"] = serde_json::json!(block);
        }
    }
    Ok(result)
}

pub fn get(pack: &Pack, id: &str) -> Result<Option<Entity>> {
    let connection = pack.connection()?;
    let predicate = if id.parse::<u64>().is_ok() {
        "e.render_id = ?1"
    } else {
        "e.id = ?1"
    };
    let sql = format!(
        "SELECT {SUMMARY}, e.source_ref, s.source_version, e.verification,
                      e.confidence, s.tags, g.wgs84_wkb IS NOT NULL,
                      EXISTS (SELECT 1 FROM edge WHERE id=e.id)
                      FROM {ENTITY} JOIN source_object s ON s.ref=e.source_ref WHERE {predicate}"
    );
    let Some((
        base,
        source_ref,
        source_version,
        verification,
        confidence,
        tags,
        has_geometry,
        has_edge,
    )) = connection
        .query_row(&sql, [id], |r| {
            Ok((
                summary(r)?,
                r.get::<_, String>(10)?,
                r.get(11)?,
                r.get(12)?,
                r.get(13)?,
                r.get::<_, String>(14)?,
                r.get(15)?,
                r.get(16)?,
            ))
        })
        .optional()?
    else {
        return Ok(None);
    };
    let sql = format!("SELECT {SUMMARY} FROM {ENTITY} WHERE e.id IN
                       (SELECT line_id FROM line_member WHERE member_id=?1) ORDER BY e.name,e.id LIMIT 50");
    let mut statement = connection.prepare(&sql)?;
    let memberships = statement
        .query_map([&base.id], summary)?
        .collect::<rusqlite::Result<_>>()?;
    let has_geometry=has_geometry || (base.kind=="line" && connection.query_row(
        "SELECT EXISTS(SELECT 1 FROM line_member m JOIN geometry g ON g.entity_id=m.member_id WHERE m.line_id=? AND g.wgs84_wkb IS NOT NULL)", [&base.id], |r|r.get::<_,bool>(0))?);
    let tags = serde_json::from_str(&tags)?;
    let semantics = crate::cartography::classify(
        &tags,
        &base.kind,
        &base.class,
        &base.status,
        verification == "verified",
    );
    let role = crate::cartography::site_role(
        &tags,
        &base.kind,
        &base.class,
        &base.name,
        semantics.render_class,
    );
    let site = site::inspect(&connection, &base, &tags, role)?;
    let mut station_links = if site.is_some() {
        station_links(
            &connection,
            &base.id,
            &source_ref,
            pack.manifest.source_schema >= 3,
        )?
    } else {
        Vec::new()
    };
    if let Some(site) = &site {
        for link in site::area_lines(&connection, site)? {
            if !station_links
                .iter()
                .any(|prior| prior.line.id == link.line.id)
            {
                station_links.push(link);
            }
        }
    }
    Ok(Some(Entity {
        summary: base,
        source_ref,
        source_version,
        verification,
        confidence,
        tags,
        semantics,
        memberships,
        station_links,
        site,
        has_geometry,
        has_edge,
    }))
}

fn station_links(
    db: &Connection,
    id: &str,
    source: &str,
    has_relations: bool,
) -> Result<Vec<StationLink>> {
    let stop_area = if has_relations {
        "UNION ALL SELECT line.id, 2
         FROM source_relation_member own JOIN source_object area ON area.ref=own.relation_ref
         JOIN source_relation_member stop ON stop.relation_ref=area.ref
         JOIN source_relation_member service ON service.member_ref=stop.member_ref
            AND service.role IN ('stop','stop_entry_only','stop_exit_only','platform','platform_entry_only','platform_exit_only')
         JOIN entity line ON line.source_ref=service.relation_ref AND line.kind='line'
         WHERE own.member_ref=?2 AND json_extract(area.tags,'$.public_transport')='stop_area'"
    } else {
        ""
    };
    let sql = format!(
        "WITH anchor(id) AS (SELECT n.id FROM entity own JOIN node n ON n.source_ref=own.source_ref
         WHERE own.id=?1 AND n.network='rail'),
        tracks(id) AS (SELECT e.id FROM anchor a JOIN edge e ON e.from_node=a.id
                       UNION SELECT e.id FROM anchor a JOIN edge e ON e.to_node=a.id),
        matches(id,evidence) AS (
        SELECT line_id,1 FROM line_member WHERE member_id=?1
        UNION ALL SELECT m.line_id,3 FROM tracks t JOIN line_member m ON m.member_id=t.id
          JOIN entity track ON track.id=t.id WHERE track.kind IN ('rail','metro') AND track.status='operating'
        {stop_area}
        ) SELECT {SUMMARY},e.source_ref,min(matches.evidence)
        FROM matches JOIN entity e ON e.id=matches.id JOIN geometry g ON g.entity_id=e.id
        WHERE e.class IN ('railway','subway','light_rail','tram','train')
        GROUP BY e.id ORDER BY e.name,e.id LIMIT 120"
    );
    let mut query = db.prepare(&sql)?;
    let read = |r: &Row| {
        let line = summary(r)?;
        let relation = match line.class.as_str() {
            "railway" => "infrastructure",
            "train" => "route_reference",
            _ => "service",
        }
        .to_owned();
        let evidence = match r.get::<_, u8>(11)? {
            1 => "OSM 线路成员",
            2 => "OSM stop_area 成员关联",
            _ => "同一原始轨道端点",
        };
        Ok(StationLink {
            line,
            relation,
            source_ref: r.get(10)?,
            evidence: evidence.into(),
        })
    };
    if has_relations {
        Ok(query
            .query_map(params![id, source], read)?
            .collect::<rusqlite::Result<_>>()?)
    } else {
        Ok(query
            .query_map([id], read)?
            .collect::<rusqlite::Result<_>>()?)
    }
}

pub fn geometry(connection: &Connection, id: &str) -> Result<Option<Value>> {
    let bytes: Option<Option<Vec<u8>>> = connection
        .query_row(
            "SELECT wgs84_wkb FROM geometry WHERE entity_id=?1 LIMIT 1",
            params![id],
            |r| r.get(0),
        )
        .optional()?;
    if let Some(wkb) = bytes.flatten() {
        return Ok(Some(serde_json::from_str(&Wkb(wkb).to_json()?)?));
    }
    let mut q = connection.prepare(
        "SELECT g.wgs84_wkb FROM line_member m JOIN geometry g ON g.entity_id=m.member_id
      JOIN entity e ON e.id=m.member_id WHERE m.line_id=? AND e.kind IN ('rail','metro','road')
      AND g.wgs84_wkb IS NOT NULL ORDER BY m.sequence,m.position LIMIT 6001",
    )?;
    let mut rows = q.query([id])?;
    let mut members = Vec::new();
    let mut bytes = 0;
    while let Some(row) = rows.next()? {
        let geometry: Vec<u8> = row.get(0)?;
        bytes += geometry.len();
        if bytes > 8 * 1024 * 1024 || members.len() >= 6000 {
            break;
        }
        members.push(serde_json::from_str::<Value>(&Wkb(geometry).to_json()?)?);
    }
    Ok((!members.is_empty())
        .then(|| serde_json::json!({"type":"GeometryCollection","geometries":members})))
}
