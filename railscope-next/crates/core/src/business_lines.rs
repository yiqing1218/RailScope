//! Business-line edits are overlays over canonical membership, independent of physical corridors.
use crate::{catalog, pack::Pack};
use anyhow::{Context, Result, ensure};
use rusqlite::{Connection, OptionalExtension, params};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::HashSet;
use uuid::Uuid;

#[derive(Clone, Serialize, Deserialize)]
struct Line {
    id: String,
    name: String,
    sources: Vec<String>,
    members: Vec<String>,
}
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case")]
enum Request {
    List,
    Create {
        name: String,
        sources: Vec<String>,
        split_at: Option<usize>,
    },
    Members {
        id: String,
        offset: usize,
    },
    Undo,
    Redo,
}
pub fn initialize(db: &Connection) -> Result<()> {
    db.execute_batch("CREATE TABLE IF NOT EXISTS line_edit_batch(
      id INTEGER PRIMARY KEY, snapshot TEXT NOT NULL, payload TEXT NOT NULL,
      applied INTEGER NOT NULL, superseded INTEGER NOT NULL DEFAULT 0)
      STRICT; CREATE INDEX IF NOT EXISTS line_edit_history ON line_edit_batch(snapshot,superseded,applied,id);")?;
    Ok(())
}
fn member_query(db: &Connection, line: &str) -> Result<Vec<String>> {
    let mut stmt = db.prepare_cached(
        "SELECT member_id FROM line_member WHERE line_id=? ORDER BY sequence,position",
    )?;
    let rows = stmt.query_map([line], |row| row.get(0))?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}

pub(crate) fn migrate(
    db: &Connection,
    old: &Pack,
    new: &Pack,
    old_canonical: &Connection,
    new_canonical: &Connection,
) -> Result<()> {
    let from = &old.manifest.snapshot;
    let to = &new.manifest.snapshot;
    let mut history = db.prepare(
        "SELECT payload FROM line_edit_batch WHERE snapshot=? AND superseded=0 AND applied=1 ORDER BY id",
    )?;
    let mut history = history.query([from])?;
    while let Some(row) = history.next()? {
        let payload: String = row.get(0)?;
        let lines: Vec<Line> = serde_json::from_str(&payload)?;
        let mut safe = old.manifest.source_sha256 == new.manifest.source_sha256;
        if safe && let Some(first) = lines.first() {
            for source in &first.sources {
                safe &=
                    member_query(old_canonical, source)? == member_query(new_canonical, source)?;
            }
        }
        if safe {
            db.execute(
                "INSERT INTO line_edit_batch(snapshot,payload,applied)
                 SELECT :to,:payload,1 WHERE NOT EXISTS
                 (SELECT 1 FROM line_edit_batch WHERE snapshot=:to AND payload=:payload)",
                rusqlite::named_params! {":to":to,":payload":payload},
            )?;
        } else {
            for line in lines {
                db.execute(
                    "INSERT OR IGNORE INTO conflicts VALUES (?,?,?,?)",
                    params![
                        to,
                        from,
                        line.id,
                        format!("工作区线路「{}」的源快照或成员已变更", line.name)
                    ],
                )?;
            }
        }
    }
    Ok(())
}

pub fn execute(db: &mut Connection, pack: &Pack, request: Value) -> Result<Value> {
    let request: Request = serde_json::from_value(request)?;
    let snapshot = &pack.manifest.snapshot;
    match request {
        Request::List => {
            let mut stmt = db.prepare(
                "SELECT line.value->>'id', line.value->>'name', json_array_length(line.value,'$.members')
                 FROM line_edit_batch batch, json_each(batch.payload) line
                 WHERE snapshot=? AND superseded=0 AND applied=1 ORDER BY batch.id DESC,line.key",
            )?;
            let lines = stmt.query_map([snapshot], |row| Ok(json!({
                "id":row.get::<_,String>(0)?,"name":row.get::<_,String>(1)?,"member_count":row.get::<_,u32>(2)?
            })))?.collect::<rusqlite::Result<Vec<_>>>()?;
            let available = |applied: bool| -> Result<bool> {
                Ok(db.query_row("SELECT EXISTS(SELECT 1 FROM line_edit_batch WHERE snapshot=? AND applied=? AND superseded=0)",
                 params![snapshot,applied],|r|r.get(0))?)
            };
            Ok(json!({"lines":lines,"undo":available(true)?,"redo":available(false)?}))
        }
        Request::Create {
            name,
            sources,
            split_at,
        } => {
            ensure!(
                !name.trim().is_empty() && name.chars().count() <= 100,
                "请输入有效线路名称"
            );
            ensure!((1..=16).contains(&sources.len()), "请选择 1–16 条源线路");
            ensure!(
                sources.iter().collect::<HashSet<_>>().len() == sources.len(),
                "请选择不同的源线路"
            );
            ensure!(
                split_at.is_none() || sources.len() == 1,
                "拆分操作需要一条源线路"
            );
            let canonical = pack.connection()?;
            let mut members = Vec::new();
            let mut unique = HashSet::new();
            for line in &sources {
                ensure!(
                    catalog::by_id(&canonical, line)?.is_some_and(|row| row.kind == "line"),
                    "源线路不在当前快照"
                );
                for id in member_query(&canonical, line)? {
                    if sources.len() == 1 || unique.insert(id.clone()) {
                        members.push(id);
                    }
                    ensure!(
                        members.len() <= 20_000,
                        "线路成员超过编辑容量，请按区段拆分原始输入"
                    );
                }
            }
            ensure!(!members.is_empty(), "源线路没有已匹配的成员");
            let make = |name, members| Line {
                id: Uuid::new_v4().to_string(),
                name,
                sources: sources.clone(),
                members,
            };
            let lines = if let Some(position) = split_at {
                ensure!(
                    position > 0 && position < members.len(),
                    "拆分位置须位于首末成员之间"
                );
                let rest = members.split_off(position);
                vec![
                    make(format!("{name}（1）"), members),
                    make(format!("{name}（2）"), rest),
                ]
            } else {
                vec![make(name, members)]
            };
            let tx = db.transaction()?;
            tx.execute(
                "UPDATE line_edit_batch SET superseded=1 WHERE snapshot=? AND applied=0",
                [snapshot],
            )?;
            tx.execute(
                "INSERT INTO line_edit_batch(snapshot,payload,applied) VALUES(?,?,1)",
                params![snapshot, serde_json::to_string(&lines)?],
            )?;
            tx.commit()?;
            Ok(json!({"created":lines.len()}))
        }
        Request::Members { id, offset } => {
            let payload: String = db
                .query_row(
                    "SELECT line.value FROM line_edit_batch batch, json_each(batch.payload) line
                 WHERE snapshot=? AND superseded=0 AND applied=1 AND line.value->>'id'=?",
                    params![snapshot, id],
                    |row| row.get(0),
                )
                .optional()?
                .context("工作区线路已撤销或快照已切换")?;
            let line: Line = serde_json::from_str(&payload)?;
            let canonical = pack.connection()?;
            let mut members = vec![];
            for id in line.members.iter().skip(offset).take(64) {
                if let Some(item) = catalog::by_id(&canonical, id)? {
                    members.push(item);
                } else {
                    anyhow::bail!("快照中缺少线路成员 · {id}");
                }
            }
            Ok(
                json!({"members":members,"next":(offset.saturating_add(64)<line.members.len()).then(||offset+64)}),
            )
        }
        action => {
            let redo = matches!(action, Request::Redo);
            let order = if redo { "ASC" } else { "DESC" };
            let sql = format!(
                "SELECT id FROM line_edit_batch WHERE snapshot=? AND applied=? AND superseded=0 ORDER BY id {order} LIMIT 1"
            );
            let tx = db.transaction()?;
            let target = tx
                .query_row(&sql, params![snapshot, !redo], |r| r.get::<_, i64>(0))
                .optional()?;
            if let Some(id) = target {
                tx.execute(
                    "UPDATE line_edit_batch SET applied=? WHERE id=?",
                    params![redo, id],
                )?;
            }
            tx.commit()?;
            Ok(json!({"applied":target.is_some()}))
        }
    }
}
