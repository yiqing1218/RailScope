use crate::{
    catalog,
    pack::{Pack, valid_bounds},
};
use anyhow::{Result, ensure};
use rusqlite::{Connection, OptionalExtension, Transaction, TransactionBehavior, params};
use serde::{Deserialize, Serialize};

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Override {
    pub name: String,
    pub note: String,
    pub color: String,
    pub point: Option<[f64; 2]>,
    pub group: String,
    pub width: Option<f64>,
    pub opacity: Option<f64>,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub designation: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub operator: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub category: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub associated_lines: Vec<String>,
    #[serde(skip_serializing_if = "crate::external_data::Profile::is_empty")]
    pub profile: crate::external_data::Profile,
    #[serde(skip_serializing_if = "std::collections::BTreeMap::is_empty")]
    pub import_sources: std::collections::BTreeMap<String, crate::external_data::Source>,
}

impl Override {
    pub fn validate_associations(&self, canonical: &Connection, kind: &str) -> Result<()> {
        if self.associated_lines.is_empty() {
            return Ok(());
        }
        ensure!(
            kind == "station" && self.associated_lines.len() <= 32,
            "仅车站可关联最多 32 条线路"
        );
        let mut ids = std::collections::HashSet::new();
        let mut line =
            canonical.prepare("SELECT EXISTS(SELECT 1 FROM entity WHERE id=? AND kind='line')")?;
        for id in &self.associated_lines {
            ensure!(
                ids.insert(id) && line.query_row([id], |row| row.get::<_, bool>(0))?,
                "关联线路重复、已移除或不属于当前快照"
            );
        }
        Ok(())
    }

    fn validate(&self) -> Result<()> {
        ensure!(
            self.name.chars().count() <= 120 && self.note.chars().count() <= 4000,
            "名称或备注过长"
        );
        ensure!(
            self.group.chars().count() <= 80 && self.group.trim() == self.group,
            "分组名称无效"
        );
        ensure!(
            [&self.designation, &self.operator, &self.category]
                .iter()
                .all(|value| value.chars().count() <= 120),
            "编号、运营方或分类过长"
        );
        ensure!(
            self.color.is_empty()
                || (self.color.len() == 7
                    && self.color.starts_with('#')
                    && self
                        .color
                        .bytes()
                        .skip(1)
                        .all(|byte| byte.is_ascii_hexdigit())),
            "颜色必须采用 #RRGGBB"
        );
        ensure!(
            self.point
                .is_none_or(|[lon, lat]| valid_bounds([lon, lat, lon, lat])),
            "坐标超出 WGS84 范围"
        );
        ensure!(
            self.width
                .is_none_or(|value| (0.25..=24.0).contains(&value))
                && self
                    .opacity
                    .is_none_or(|value| (0.0..=1.0).contains(&value)),
            "线宽须为 0.25–24 像素，透明度须为 0–1"
        );
        Ok(())
    }
}

#[derive(Serialize)]
pub struct State {
    pub revision: i64,
    pub value: Override,
    pub can_undo: bool,
    pub can_redo: bool,
}

pub fn initialize(db: &Connection) -> Result<()> {
    db.execute_batch("
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id=1), revision INTEGER NOT NULL, active TEXT
        ) STRICT;
        INSERT OR IGNORE INTO settings VALUES (1, 0, NULL);
        CREATE TABLE IF NOT EXISTS overrides (
            snapshot TEXT NOT NULL, entity TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (snapshot, entity)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS override_group ON overrides(snapshot,json_extract(payload,'$.group'),entity);
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY, snapshot TEXT NOT NULL, entity TEXT NOT NULL,
            before_value TEXT NOT NULL, after_value TEXT NOT NULL,
            applied INTEGER NOT NULL, superseded INTEGER NOT NULL DEFAULT 0
        ) STRICT;
        CREATE INDEX IF NOT EXISTS history_lookup ON history(snapshot, superseded, applied, id);
        CREATE TABLE IF NOT EXISTS audit (
            revision INTEGER PRIMARY KEY, command_id INTEGER NOT NULL REFERENCES history(id),
            action TEXT NOT NULL, changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) STRICT;
        CREATE TABLE IF NOT EXISTS migrations (
            source_snapshot TEXT NOT NULL, snapshot TEXT NOT NULL,
            PRIMARY KEY (source_snapshot, snapshot)
        ) STRICT;
        CREATE TABLE IF NOT EXISTS conflicts (
            snapshot TEXT NOT NULL, source_snapshot TEXT NOT NULL, entity TEXT NOT NULL, reason TEXT NOT NULL,
            PRIMARY KEY (snapshot, source_snapshot, entity)
        ) STRICT;
    ")?;
    Ok(())
}

fn revision(db: &Connection) -> Result<i64> {
    Ok(db.query_row("SELECT revision FROM settings", [], |r| r.get(0))?)
}

pub(crate) fn payload(db: &Connection, snapshot: &str, entity: &str) -> Result<Option<String>> {
    Ok(db
        .query_row(
            "SELECT payload FROM overrides WHERE snapshot=? AND entity=?",
            [snapshot, entity],
            |r| r.get(0),
        )
        .optional()?)
}

pub fn active(db: &Connection) -> Result<Option<String>> {
    Ok(db.query_row("SELECT active FROM settings", [], |r| r.get(0))?)
}

pub fn activate(db: &mut Connection, previous: Option<&Pack>, next: &Pack) -> Result<()> {
    let snapshot = &next.manifest.snapshot;
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    if let Some(previous) =
        previous.filter(|previous| previous.manifest.region == next.manifest.region)
    {
        migrate(&tx, previous, next)?;
    }
    tx.execute("UPDATE settings SET active=? WHERE id=1", [snapshot])?;
    tx.commit()?;
    Ok(())
}

fn migrate(db: &Connection, old: &Pack, new: &Pack) -> Result<()> {
    let from = &old.manifest.snapshot;
    let to = &new.manifest.snapshot;
    if db.execute("INSERT OR IGNORE INTO migrations VALUES (?,?)", [from, to])? == 0 {
        return Ok(());
    }
    let old_db = old.connection()?;
    let new_db = new.connection()?;
    let signature = "SELECT e.kind, e.source_ref, s.source_version, s.tags, g.wgs84_wkb
                     FROM entity e JOIN source_object s ON s.ref=e.source_ref
                     JOIN geometry g ON g.entity_id=e.id WHERE e.id=?";
    let mut old_signature = old_db.prepare(signature)?;
    let mut new_signature = new_db.prepare(signature)?;
    let read = |r: &rusqlite::Row<'_>| -> rusqlite::Result<_> {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, u32>(2)?,
            r.get::<_, String>(3)?,
            r.get::<_, Option<Vec<u8>>>(4)?,
        ))
    };
    let mut statement = db.prepare("SELECT entity, payload FROM overrides WHERE snapshot=?")?;
    let mut records = statement.query([from])?;
    while let Some(row) = records.next()? {
        let id: String = row.get(0)?;
        let value: String = row.get(1)?;
        if value == serde_json::to_string(&Override::default())? {
            continue;
        }
        let overrides: Override = serde_json::from_str(&value)?;
        let before = old_signature.query_row([&id], read).optional()?;
        let after = new_signature.query_row([&id], read).optional()?;
        let reason = if after.is_none() {
            "实体已移除或拆分"
        } else if before != after {
            "原始实体已变更"
        } else if overrides
            .validate_associations(&new_db, &after.as_ref().unwrap().0)
            .is_err()
        {
            "车站关联线路已移除或变更"
        } else if overrides
            .profile
            .validate(&new_db, &id, &after.as_ref().unwrap().0)
            .is_err()
        {
            "外部资料引用的线路区段已移除或变更"
        } else if let Some(existing) = payload(db, to, &id)? {
            if existing == value {
                continue;
            }
            "目标工作区已有不同覆盖"
        } else {
            write(db, to, &id, &value)?;
            continue;
        };
        db.execute(
            "INSERT OR IGNORE INTO conflicts VALUES (?,?,?,?)",
            [to, from, &id, reason],
        )?;
    }
    crate::business_lines::migrate(db, old, new, &old_db, &new_db)?;
    db.execute(
        "INSERT OR IGNORE INTO conflicts(snapshot,source_snapshot,entity,reason)
         SELECT ?1,?2,id,'通道与车次仍指向原图谱版本' FROM ops_corridor WHERE snapshot=?2 GROUP BY id",
        [to,from],
    )?;
    Ok(())
}

#[derive(Serialize)]
pub struct Conflict {
    pub entity: String,
    pub source_snapshot: String,
    pub reason: String,
}

pub fn conflicts(db: &Connection, snapshot: &str) -> Result<Vec<Conflict>> {
    let mut statement = db.prepare("SELECT entity, source_snapshot, reason FROM conflicts WHERE snapshot=? ORDER BY entity LIMIT 100")?;
    Ok(statement
        .query_map([snapshot], |row| {
            Ok(Conflict {
                entity: row.get(0)?,
                source_snapshot: row.get(1)?,
                reason: row.get(2)?,
            })
        })?
        .collect::<rusqlite::Result<_>>()?)
}

#[derive(Serialize)]
pub struct Visual {
    pub id: String,
    pub render_id: i64,
    pub kind: String,
    pub class: String,
    pub(crate) station: Option<crate::station_marks::Display>,
    pub value: Override,
}

pub fn visuals(db: &Connection, pack: &Pack, after: Option<&str>) -> Result<Vec<Visual>> {
    let mut modified = db.prepare(
        "SELECT entity, payload FROM overrides WHERE snapshot=?1 AND entity > coalesce(?2,'')
         AND (json_extract(payload,'$.name') != '' OR json_extract(payload,'$.color') != '' OR json_extract(payload,'$.point') IS NOT NULL
              OR json_extract(payload,'$.width') IS NOT NULL OR json_extract(payload,'$.opacity') IS NOT NULL)
         ORDER BY entity LIMIT 160",
    )?;
    let canonical = pack.connection()?;
    let mut base = canonical.prepare("SELECT render_id,kind,class FROM entity WHERE id=?")?;
    let mut rows = modified.query(params![pack.manifest.snapshot, after])?;
    let mut output = Vec::new();
    let stations = pack.stations()?;
    while let Some(row) = rows.next()? {
        let id: String = row.get(0)?;
        let text: String = row.get(1)?;
        let info = base
            .query_row([&id], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
            .optional()?;
        if let Some((render_id, kind, class)) = info {
            output.push(Visual {
                id,
                render_id,
                station: stations.display(render_id as u64).cloned(),
                kind,
                class,
                value: serde_json::from_str(&text)?,
            });
        }
    }
    Ok(output)
}

#[derive(Serialize)]
pub struct Group {
    pub name: String,
    pub count: u32,
}

pub fn groups(db: &Connection, snapshot: &str) -> Result<Vec<Group>> {
    let mut statement = db.prepare(
        "SELECT json_extract(payload,'$.group'),count(*) FROM overrides
         WHERE snapshot=? AND json_extract(payload,'$.group') > ''
         GROUP BY json_extract(payload,'$.group') ORDER BY 1 LIMIT 200",
    )?;
    Ok(statement
        .query_map([snapshot], |row| {
            Ok(Group {
                name: row.get(0)?,
                count: row.get(1)?,
            })
        })?
        .collect::<rusqlite::Result<_>>()?)
}

pub fn group_members(
    db: &Connection,
    pack: &Pack,
    group: &str,
    after: Option<&str>,
) -> Result<catalog::Page> {
    ensure!(
        !group.is_empty() && group.chars().count() <= 80,
        "分组名称无效"
    );
    let mut statement = db.prepare(
        "SELECT entity,payload FROM overrides
         WHERE snapshot=?1 AND json_extract(payload,'$.group')=?2 AND entity > coalesce(?3,'')
         ORDER BY entity LIMIT 81",
    )?;
    let mut rows = statement.query(params![pack.manifest.snapshot, group, after])?;
    let canonical = pack.connection()?;
    let mut items = Vec::new();
    let mut next = None;
    let mut count = 0;
    let mut cursor = None;
    while let Some(row) = rows.next()? {
        if count == 80 {
            next = cursor;
            break;
        }
        let id: String = row.get(0)?;
        let payload: String = row.get(1)?;
        if let Some(mut item) = catalog::by_id(&canonical, &id)? {
            apply_summary(&mut item, serde_json::from_str(&payload)?);
            items.push(item);
        }
        cursor = Some(id);
        count += 1;
    }
    Ok(catalog::Page { items, next })
}

pub fn stations_for_line(
    db: &Connection,
    canonical: &Connection,
    snapshot: &str,
    id: &str,
) -> Result<Vec<catalog::Summary>> {
    let mut query = db.prepare(
        "SELECT entity, json_extract(payload,'$.name') FROM overrides
         WHERE snapshot=?1 AND EXISTS(SELECT 1 FROM json_each(payload,'$.associated_lines') WHERE value=?2)
         ORDER BY entity LIMIT 150",
    )?;
    let mut rows = query.query(params![snapshot, id])?;
    let mut stations = Vec::new();
    while let Some(row) = rows.next()? {
        let station: String = row.get(0)?;
        if let Some(mut item) =
            catalog::by_id(canonical, &station)?.filter(|item| item.kind == "station")
        {
            if let Some(name) = row
                .get::<_, Option<String>>(1)?
                .filter(|name| !name.is_empty())
            {
                item.name = name;
            }
            stations.push(item);
        }
    }
    stations.sort_by(|a, b| a.name.cmp(&b.name).then(a.id.cmp(&b.id)));
    Ok(stations)
}

pub fn state(db: &Connection, snapshot: &str, entity: &str) -> Result<State> {
    let value = payload(db, snapshot, entity)?
        .map(|value| serde_json::from_str(&value))
        .transpose()?
        .unwrap_or_default();
    let status = |applied: bool| {
        db.query_row(
            "SELECT EXISTS(SELECT 1 FROM history WHERE snapshot=? AND applied=? AND superseded=0)",
            params![snapshot, applied],
            |r| r.get(0),
        )
    };
    Ok(State {
        revision: revision(db)?,
        value,
        can_undo: status(true)?,
        can_redo: status(false)?,
    })
}

fn apply_summary(item: &mut catalog::Summary, value: Override) {
    if !value.name.is_empty() {
        item.name = value.name;
    }
    if !value.category.is_empty() {
        item.workspace_category = Some(value.category);
    }
}

pub fn catalog_overrides(
    db: &Connection,
    pack: &Pack,
    filter: &catalog::Filter,
    page: &mut catalog::Page,
) -> Result<()> {
    let snapshot = &pack.manifest.snapshot;
    let term = filter.query.trim();
    if !term.is_empty()
        && filter.cursor.is_none()
        && filter.parent.is_none()
        && !term.starts_with("osm:")
        && uuid::Uuid::parse_str(term).is_err()
    {
        let mut matches = db.prepare(
            "SELECT entity FROM overrides WHERE snapshot=?1 AND
             (instr(lower(coalesce(json_extract(payload,'$.name'),'')),lower(?2))>0
              OR instr(lower(coalesce(json_extract(payload,'$.designation'),'')),lower(?2))>0
              OR instr(lower(coalesce(json_extract(payload,'$.category'),'')),lower(?2))>0
              OR EXISTS(SELECT 1 FROM json_each(payload,'$.profile.former_names') WHERE instr(lower(value),lower(?2))>0)
              OR EXISTS(SELECT 1 FROM json_each(payload,'$.profile.other_names') WHERE instr(lower(value),lower(?2))>0))
             ORDER BY entity LIMIT 100",
        )?;
        let ids = matches.query_map(params![snapshot, term], |row| row.get::<_, String>(0))?;
        let canonical = pack.connection()?;
        let mut extra = Vec::new();
        for id in ids {
            let id = id?;
            if page.items.iter().any(|item| item.id == id) {
                continue;
            }
            if let Some(item) = catalog::by_id(&canonical, &id)?.filter(|item| {
                filter.kind.as_ref().is_none_or(|kind| item.kind == *kind)
                    && (filter.classes.is_empty() || filter.classes.contains(&item.class))
                    && filter.bbox.is_none_or(|[w, s, e, n]| {
                        let [x1, y1, x2, y2] = item.bounds;
                        x2 >= w && y2 >= s && x1 <= e && y1 <= n
                    })
            }) {
                extra.push(item);
                if extra.len() == 20 {
                    break;
                }
            }
        }
        page.items.splice(0..0, extra);
    }
    let mut lookup =
        db.prepare_cached("SELECT payload FROM overrides WHERE snapshot=? AND entity=?")?;
    for item in &mut page.items {
        let value: Option<String> = lookup
            .query_row(params![snapshot, item.id], |row| row.get(0))
            .optional()?;
        if let Some(value) = value {
            apply_summary(item, serde_json::from_str(&value)?);
        }
    }
    Ok(())
}

fn transaction(db: &mut Connection, expected: i64) -> Result<Transaction<'_>> {
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    ensure!(revision(&tx)? == expected, "工作区版本冲突，请刷新后再编辑");
    Ok(tx)
}

fn write(db: &Connection, snapshot: &str, entity: &str, value: &str) -> Result<()> {
    db.execute("INSERT INTO overrides VALUES (?, ?, ?) ON CONFLICT(snapshot,entity) DO UPDATE SET payload=excluded.payload",
               [snapshot, entity, value])?;
    Ok(())
}

fn audit(db: &Connection, command: i64, action: &str) -> Result<()> {
    db.execute("UPDATE settings SET revision=revision+1 WHERE id=1", [])?;
    db.execute(
        "INSERT INTO audit(revision,command_id,action) SELECT revision,?,? FROM settings",
        params![command, action],
    )?;
    Ok(())
}

pub fn apply(
    db: &mut Connection,
    snapshot: &str,
    entity: &str,
    expected: i64,
    mut value: Override,
) -> Result<State> {
    let tx = transaction(db, expected)?;
    let before: Override = payload(&tx, snapshot, entity)?
        .map(|payload| serde_json::from_str(&payload))
        .transpose()?
        .unwrap_or_default();
    value.profile = before.profile.clone();
    let prior = serde_json::to_value(&before)?;
    let next = serde_json::to_value(&value)?;
    value.import_sources = before.import_sources;
    value
        .import_sources
        .retain(|field, _| field.starts_with("profile.") || prior.get(field) == next.get(field));
    record(&tx, snapshot, entity, &value, "apply")?;
    tx.commit()?;
    state(db, snapshot, entity)
}

pub(crate) fn record(
    tx: &Connection,
    snapshot: &str,
    entity: &str,
    value: &Override,
    action: &str,
) -> Result<bool> {
    value.validate()?;
    let old =
        payload(tx, snapshot, entity)?.unwrap_or(serde_json::to_string(&Override::default())?);
    let new = serde_json::to_string(&value)?;
    if old != new {
        tx.execute(
            "UPDATE history SET superseded=1 WHERE snapshot=? AND applied=0",
            [snapshot],
        )?;
        tx.execute("INSERT INTO history(snapshot,entity,before_value,after_value,applied) VALUES(?,?,?,?,1)",
                   [snapshot, entity, &old, &new])?;
        let command = tx.last_insert_rowid();
        write(tx, snapshot, entity, &new)?;
        audit(tx, command, action)?;
    }
    Ok(old != new)
}

pub fn replay(
    db: &mut Connection,
    snapshot: &str,
    entity: &str,
    expected: i64,
    redo: bool,
) -> Result<State> {
    let tx = transaction(db, expected)?;
    let (sort, field) = if redo {
        ("ASC", "after_value")
    } else {
        ("DESC", "before_value")
    };
    let sql = format!(
        "SELECT id, entity, {field} FROM history WHERE snapshot=? AND applied=? AND superseded=0
                       ORDER BY id {sort} LIMIT 1"
    );
    let record: Option<(i64, String, String)> = tx
        .query_row(&sql, params![snapshot, !redo], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?))
        })
        .optional()?;
    if let Some((command, target, value)) = record {
        tx.execute(
            "UPDATE history SET applied=? WHERE id=?",
            params![redo, command],
        )?;
        write(&tx, snapshot, &target, &value)?;
        audit(&tx, command, if redo { "redo" } else { "undo" })?;
    }
    tx.commit()?;
    state(db, snapshot, entity)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn edits_keep_revisions_and_replace_redo_branch() -> Result<()> {
        let mut db = Connection::open_in_memory()?;
        initialize(&db)?;
        let value = Override {
            note: "test".into(),
            color: "#2b604d".into(),
            ..Override::default()
        };
        assert_eq!(apply(&mut db, "first", "a", 0, value.clone())?.revision, 1);
        assert!(apply(&mut db, "first", "a", 0, Override::default()).is_err());
        assert_eq!(state(&db, "first", "a")?.value.note, "test");
        assert!(state(&db, "second", "a")?.value.note.is_empty());

        let undone = replay(&mut db, "first", "a", 1, false)?;
        assert!(undone.value.note.is_empty());
        assert!(undone.can_redo);
        assert_eq!(
            replay(&mut db, "first", "a", 2, true)?.value.color,
            "#2b604d"
        );
        replay(&mut db, "first", "a", 3, false)?;
        let another = apply(&mut db, "first", "b", 4, value)?;
        assert!(!another.can_redo);
        assert_eq!(another.revision, 5);
        assert_eq!(
            db.query_row("SELECT count(*) FROM audit", [], |r| r.get::<_, u32>(0))?,
            5
        );
        Ok(())
    }

    #[test]
    fn rejects_invalid_point_and_color_before_committing() -> Result<()> {
        let mut db = Connection::open_in_memory()?;
        initialize(&db)?;
        for value in [
            Override {
                color: "rgb(0,0,0)".into(),
                ..Override::default()
            },
            Override {
                point: Some([181.0, 31.5]),
                ..Override::default()
            },
        ] {
            assert!(apply(&mut db, "first", "a", 0, value).is_err());
        }
        assert_eq!(state(&db, "first", "a")?.revision, 0);
        Ok(())
    }
}
