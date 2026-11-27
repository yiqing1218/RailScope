//! File-based, snapshot-bound exchange of arbitrarily many bounded documents.
use crate::{external_data, operations, pack::Pack};
use anyhow::{Context, Result, ensure};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use rusqlite::{Connection, TransactionBehavior};
use serde::{Deserialize, Serialize};
use std::{
    collections::HashSet,
    fs::File,
    io::{Read, Write},
    path::Path,
    sync::atomic::{AtomicBool, Ordering},
};
use zip::{CompressionMethod, ZipArchive, ZipWriter, write::SimpleFileOptions};

const MANIFEST: &str = "railscope-exchange.json";
const DOCUMENT_LIMIT: u64 = 32 * 1024 * 1024;
const KINDS: &[&str] = &["station", "line", "rail", "metro", "corridors", "runs"];

#[derive(Deserialize)]
pub struct Scope {
    pub kind: String,
    #[serde(default)]
    pub query: String,
    pub parent: Option<String>,
    pub date: Option<String>,
    pub format: String,
}

#[derive(Serialize, Deserialize)]
struct Manifest {
    version: u32,
    snapshot: String,
    documents: Vec<Document>,
}

#[derive(Serialize, Deserialize)]
struct Document {
    path: String,
    format: String,
    date: Option<String>,
    count: u64,
}

#[derive(Default, Serialize)]
pub struct Report {
    pub documents: usize,
    pub records: u64,
    pub changed: u64,
    pub first_date: Option<String>,
}

fn active(stop: &AtomicBool) -> Result<()> {
    ensure!(!stop.load(Ordering::Relaxed), "已取消资料任务");
    Ok(())
}

pub fn export_to(
    db: &mut Connection,
    pack: &Pack,
    path: &Path,
    scope: &Scope,
    stop: &AtomicBool,
    progress: impl Fn(u64, u64),
) -> Result<Report> {
    ensure!(
        scope.kind == "all" || KINDS.contains(&scope.kind.as_str()),
        "未知资料类型"
    );
    ensure!(
        matches!(scope.format.as_str(), "json" | "csv" | "gtfs"),
        "未知文件格式"
    );
    ensure!(
        scope.format != "gtfs" || scope.date.is_some(),
        "GTFS 批量导出需要选择运营日期"
    );
    let parent = path.parent().context("缺少保存目录")?;
    let mut staged = tempfile::NamedTempFile::new_in(parent)?;
    let db = db.transaction()?;
    let mut manifest = Manifest {
        version: 1,
        snapshot: pack.manifest.snapshot.clone(),
        documents: Vec::new(),
    };
    let mut report = Report::default();
    let mut zip = ZipWriter::new(staged.as_file_mut());
    let options = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
    for &kind in KINDS {
        if scope.kind != "all" && scope.kind != kind {
            continue;
        }
        let mut cursor = None;
        loop {
            active(stop)?;
            let (format, content, count, next) = if matches!(kind, "corridors" | "runs") {
                let format = if kind == "runs" && scope.kind != "all" {
                    scope.format.as_str()
                } else {
                    "json"
                };
                let result = operations::export_exchange_page(
                    &db,
                    pack,
                    format,
                    kind,
                    scope.date.as_deref(),
                    cursor.as_deref(),
                )?;
                (
                    format,
                    result["content"]
                        .as_str()
                        .context("缺少导出内容")?
                        .to_owned(),
                    result["count"].as_u64().context("缺少导出计数")?,
                    result["next"].as_str().map(str::to_owned),
                )
            } else {
                let batch = external_data::export(
                    &db,
                    pack,
                    external_data::ExportFilter {
                        kind: kind.into(),
                        query: scope.query.clone(),
                        cursor: cursor.clone(),
                        parent: scope.parent.clone(),
                    },
                )?;
                ("metadata", batch.content, batch.count as u64, batch.next)
            };
            if count == 0 {
                break;
            }
            let name = format!(
                "data/{kind}-{:06}.{}",
                report.documents + 1,
                match format {
                    "csv" => "csv",
                    "gtfs" => "zip",
                    _ => "json",
                }
            );
            zip.start_file(&name, options)?;
            if format == "gtfs" {
                zip.write_all(&STANDARD.decode(&content)?)?;
            } else {
                zip.write_all(content.as_bytes())?;
            }
            manifest.documents.push(Document {
                path: name,
                format: format.into(),
                date: if format == "gtfs" {
                    scope.date.clone()
                } else {
                    None
                },
                count,
            });
            report.documents += 1;
            report.records += count;
            progress(report.records, 0);
            let Some(next) = next else { break };
            ensure!(cursor.as_ref() != Some(&next), "资料分页游标未推进");
            cursor = Some(next);
        }
    }
    ensure!(report.records > 0, "当前范围没有可导出的资料");
    zip.start_file(MANIFEST, options)?;
    serde_json::to_writer(&mut zip, &manifest)?;
    zip.finish()?;
    staged.as_file().sync_all()?;
    active(stop)?;
    staged.persist(path).context("无法保存资料 ZIP")?;
    Ok(report)
}

fn document(zip: &mut ZipArchive<File>, name: &str, limit: u64) -> Result<Vec<u8>> {
    let file = zip
        .by_name(name)
        .with_context(|| format!("资料 ZIP 缺少 {name}"))?;
    ensure!(
        !file.is_dir() && file.size() <= limit,
        "资料子文件超出容量或格式无效"
    );
    let mut bytes = Vec::new();
    file.take(limit + 1).read_to_end(&mut bytes)?;
    ensure!(bytes.len() as u64 <= limit, "资料子文件超出容量");
    Ok(bytes)
}

pub fn import_from(
    db: &mut Connection,
    pack: &Pack,
    path: &Path,
    preview: bool,
    stop: &AtomicBool,
    progress: impl Fn(u64, u64),
) -> Result<Report> {
    let mut zip = ZipArchive::new(File::open(path)?)?;
    let manifest: Manifest =
        serde_json::from_slice(&document(&mut zip, MANIFEST, 16 * 1024 * 1024)?)?;
    ensure!(
        manifest.version == 1
            && manifest.snapshot == pack.manifest.snapshot
            && !manifest.documents.is_empty(),
        "资料 ZIP 版本无效或与当前快照不匹配"
    );
    let mut seen = HashSet::new();
    let total = manifest.documents.len() as u64;
    progress(0, total);
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let mut report = Report::default();
    for entry in manifest.documents {
        active(stop)?;
        ensure!(
            entry.path.starts_with("data/") && seen.insert(entry.path.clone()),
            "资料 ZIP 有重复或无效文件"
        );
        let bytes = document(&mut zip, &entry.path, DOCUMENT_LIMIT)?;
        let content = if entry.format == "gtfs" {
            STANDARD.encode(bytes)
        } else {
            String::from_utf8(bytes)?
        };
        let apply = || -> Result<(u64, u64, Option<String>)> {
            if entry.format == "metadata" {
                let item = external_data::import_in(&tx, pack, &content, preview)?;
                return Ok((item.records as u64, item.changed as u64, None));
            }
            let result = operations::import_exchange_page(
                &tx,
                pack,
                &entry.format,
                &content,
                entry.date.as_deref(),
                preview,
            )?;
            let count = result["runs"].as_u64().unwrap_or(0)
                + result["corridors"].as_u64().unwrap_or(0)
                + result["services"].as_u64().unwrap_or(0);
            let changed = if result["duplicate"] == true {
                0
            } else {
                result["changed"].as_u64().unwrap_or(count)
            };
            let day = result["first_date"]
                .as_str()
                .filter(|day| !day.is_empty())
                .map(str::to_owned);
            Ok((entry.count, changed, day))
        };
        let (count, changed, day) = apply().with_context(|| entry.path.clone())?;
        report.records += count;
        report.changed += changed;
        report.documents += 1;
        if let Some(day) = day {
            if report.first_date.as_ref().is_none_or(|prior| day < *prior) {
                report.first_date = Some(day);
            }
        }
        progress(report.documents as u64, total);
    }
    active(stop)?;
    if !preview {
        tx.commit()?;
    }
    Ok(report)
}
