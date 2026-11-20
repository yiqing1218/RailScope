use crate::pack::{Pack, valid_bounds};
use anyhow::{Result, ensure};
use geozero::{ToJson, wkb::Wkb};
use rusqlite::{Connection, OptionalExtension, Row, params};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use unicode_normalization::UnicodeNormalization;

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
    pub memberships: Vec<Summary>,
    pub has_geometry: bool,
    pub has_edge: bool,
}

#[derive(Default, Deserialize)]
#[serde(default)]
pub struct Filter {
    pub query: String,
    pub kind: Option<String>,
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
    })
}

pub fn by_id(db: &Connection, id: &str) -> Result<Option<Summary>> {
    let sql = format!("SELECT {SUMMARY} FROM {ENTITY} WHERE e.id=?");
    Ok(db.query_row(&sql, [id], summary).optional()?)
}

pub fn list(pack: &Pack, filter: Filter) -> Result<Page> {
    ensure!(filter.query.chars().count() <= 128, "查询长度超过限制");
    ensure!(filter.bbox.is_none_or(valid_bounds), "无效的查询范围");
    let connection = pack.connection()?;
    let fts = filter.query.nfkc().collect::<String>().to_lowercase();
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
    if !query.is_empty() {
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
    if !query.is_empty() {
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
    if !query.is_empty() {
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
                r.get(10)?,
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
    Ok(Some(Entity {
        summary: base,
        source_ref,
        source_version,
        verification,
        confidence,
        tags: serde_json::from_str(&tags)?,
        memberships,
        has_geometry,
        has_edge,
    }))
}

pub fn geometry(connection: &Connection, id: &str) -> Result<Option<Value>> {
    let bytes: Option<Option<Vec<u8>>> = connection
        .query_row(
            "SELECT wgs84_wkb FROM geometry WHERE entity_id=?1 LIMIT 1",
            params![id],
            |r| r.get(0),
        )
        .optional()?;
    bytes
        .flatten()
        .map(|wkb| Ok(serde_json::from_str(&Wkb(wkb).to_json()?)?))
        .transpose()
}
