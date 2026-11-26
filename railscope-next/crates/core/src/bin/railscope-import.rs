use anyhow::{Context, Result, ensure};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use railscope_next_core::{external_data, operations, pack::Pack, workspace};
use rusqlite::Connection;
use serde_json::{Value, json};
use std::{env, fs::File, io::Read, path::Path};

const USAGE: &str = "railscope-import [--check] <数据包目录> <workspace.sqlite> <metadata|json|csv|gtfs> <文件> [GTFS 运营日期] [GTFS 站点映射 JSON]";

fn read(path: &Path, limit: usize) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    File::open(path)?
        .take((limit + 1) as u64)
        .read_to_end(&mut bytes)?;
    ensure!(bytes.len() <= limit, "输入文件超出容量");
    Ok(bytes)
}

fn run() -> Result<()> {
    let mut args: Vec<_> = env::args_os().skip(1).collect();
    if args.is_empty() || args == ["--help"] {
        println!("{USAGE}");
        return Ok(());
    }
    let preview = args.first().is_some_and(|arg| arg == "--check");
    if preview {
        args.remove(0);
    }
    ensure!((4..=6).contains(&args.len()), "{USAGE}");
    let format = args[2].to_str().context(USAGE)?;
    ensure!(
        matches!(format, "metadata" | "json" | "csv" | "gtfs")
            && if format == "gtfs" {
                args.len() >= 5
            } else {
                args.len() == 4
            },
        "{USAGE}"
    );
    let limit = if format == "json" {
        32 * 1024 * 1024
    } else {
        external_data::MAX_BYTES
    };
    let bytes = read(Path::new(&args[3]), limit)?;
    let pack = Pack::open(Path::new(&args[0]))?;
    let mut db = Connection::open(Path::new(&args[1]))?;
    workspace::initialize(&db)?;
    operations::initialize(&db)?;
    let result = if format == "metadata" {
        serde_json::to_value(external_data::import(
            &mut db,
            &pack,
            &String::from_utf8(bytes)?,
            preview,
        )?)?
    } else {
        let content = if format == "gtfs" {
            STANDARD.encode(bytes)
        } else {
            String::from_utf8(bytes)?
        };
        let stop_map: Value = args
            .get(5)
            .map(|path| -> Result<Value> {
                let bytes = read(Path::new(path), 4 * 1024 * 1024)?;
                Ok(serde_json::from_str(
                    String::from_utf8(bytes)?.trim_start_matches('\u{feff}'),
                )?)
            })
            .transpose()?
            .unwrap_or_else(|| json!({}));
        operations::execute(
            &mut db,
            &pack,
            json!({
                "action": "import_external_plan", "format": format, "content": content,
                "service_date": args.get(4).and_then(|date| date.to_str()),
                "stop_map": stop_map, "preview": preview
            }),
        )?
    };
    println!("{}", serde_json::to_string_pretty(&result)?);
    Ok(())
}

fn main() -> std::process::ExitCode {
    match run() {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error:#}");
            std::process::ExitCode::FAILURE
        }
    }
}
