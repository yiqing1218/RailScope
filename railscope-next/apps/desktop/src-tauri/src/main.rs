#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use anyhow::{Context, Result, ensure};
use railscope_next_core::{Engine, catalog, graph, operations as rail_ops, pack, workspace};
use serde::Serialize;
use serde_json::Value;
use std::{
    collections::HashMap,
    fs::File,
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
};
use tauri::{Manager, State, http};
use tokio::sync::Semaphore;

type AppResult<T> = std::result::Result<T, String>;
type Core<'a> = State<'a, Arc<Engine>>;
#[derive(Default)]
struct Routes(Mutex<HashMap<String, Arc<AtomicBool>>>);
struct TileWorkers(Arc<Semaphore>);
struct OpsClock(Mutex<rail_ops::Clock>);
#[derive(Default)]
struct InstallJobs(Mutex<HashMap<String, Arc<InstallProgress>>>);
#[derive(Default)]
struct InstallProgress {
    done: AtomicU64,
    total: AtomicU64,
    stop: AtomicBool,
}

#[tauri::command]
async fn install_data_pack(
    path: String,
    job: String,
    engine: Core<'_>,
    jobs: State<'_, Arc<InstallJobs>>,
) -> AppResult<pack::Manifest> {
    if path.len() > 4096 || !PathBuf::from(&path).is_absolute() || !pack::valid_id(&job) {
        return Err("请输入数据包文件夹的绝对路径".into());
    }
    let engine = Arc::clone(engine.inner());
    let jobs = Arc::clone(jobs.inner());
    let progress = Arc::new(InstallProgress::default());
    {
        let mut entries = jobs.0.lock().unwrap();
        if !entries.is_empty() {
            return Err("已有数据包安装任务".into());
        }
        entries.insert(job.clone(), progress.clone());
    }
    let result = background(move || {
        engine.install_pack(&PathBuf::from(path), &progress.stop, |done, total| {
            progress.done.store(done, Ordering::Relaxed);
            progress.total.store(total, Ordering::Relaxed);
        })
    })
    .await;
    jobs.0.lock().unwrap().remove(&job);
    result
}

#[tauri::command]
fn data_pack_job(job: String, cancel: bool, jobs: State<'_, Arc<InstallJobs>>) -> Option<[u64; 2]> {
    let jobs = jobs.0.lock().unwrap();
    let progress = jobs.get(&job)?;
    if cancel {
        progress.stop.store(true, Ordering::Relaxed);
    }
    Some([
        progress.done.load(Ordering::Relaxed),
        progress.total.load(Ordering::Relaxed),
    ])
}

#[tauri::command]
async fn operations(snapshot: String, request: Value, engine: Core<'_>) -> AppResult<Value> {
    let engine = Arc::clone(engine.inner());
    background(move || {
        engine.with_workspace(&snapshot, |db, pack| rail_ops::execute(db, pack, request))
    })
    .await
}

#[tauri::command]
async fn edit_business_line(
    snapshot: String,
    request: Value,
    engine: Core<'_>,
) -> AppResult<Value> {
    let engine = Arc::clone(engine.inner());
    background(move || {
        engine.with_workspace(&snapshot, |db, pack| {
            railscope_next_core::business_lines::execute(db, pack, request)
        })
    })
    .await
}

#[tauri::command]
fn operations_clock(
    action: String,
    value: Option<f64>,
    clock: State<'_, OpsClock>,
) -> AppResult<Value> {
    clock
        .0
        .lock()
        .unwrap()
        .apply(&action, value)
        .map_err(|error| error.to_string())
}

async fn background<T: Send + 'static>(
    f: impl FnOnce() -> Result<T> + Send + 'static,
) -> AppResult<T> {
    tauri::async_runtime::spawn_blocking(f)
        .await
        .map_err(|_| "后台任务无法完成".to_string())?
        .map_err(|err| {
            tracing::warn!(error = %err, "operation failed");
            err.to_string()
        })
}

#[derive(Serialize)]
struct DataPacks {
    active: Option<pack::Manifest>,
    packs: Vec<pack::Manifest>,
}

#[tauri::command]
async fn list_data_packs(engine: Core<'_>) -> AppResult<DataPacks> {
    let engine = Arc::clone(engine.inner());
    background(move || {
        Ok(DataPacks {
            active: engine.active().map(|pack| pack.manifest.clone()),
            packs: engine.packs()?,
        })
    })
    .await
}

#[tauri::command]
async fn activate_data_pack(snapshot: String, engine: Core<'_>) -> AppResult<pack::Manifest> {
    let engine = Arc::clone(engine.inner());
    background(move || engine.activate(&snapshot)).await
}

#[tauri::command]
async fn catalog_page(
    snapshot: String,
    filter: catalog::Filter,
    engine: Core<'_>,
) -> AppResult<catalog::Page> {
    let engine = Arc::clone(engine.inner());
    background(move || catalog::list(engine.for_snapshot(&snapshot)?.as_ref(), filter)).await
}

#[derive(Serialize)]
struct Inspection {
    entity: catalog::Entity,
    workspace: workspace::State,
}

#[tauri::command]
async fn get_entity(snapshot: String, id: String, engine: Core<'_>) -> AppResult<Inspection> {
    let engine = Arc::clone(engine.inner());
    background(move || {
        engine.with_workspace(&snapshot, |db, pack| {
            let entity = catalog::get(pack, &id)?.context("当前快照没有该对象")?;
            let workspace = workspace::state(db, &snapshot, &entity.summary.id)?;
            Ok(Inspection { entity, workspace })
        })
    })
    .await
}

#[tauri::command]
async fn get_geometry(snapshot: String, id: String, engine: Core<'_>) -> AppResult<Option<Value>> {
    let engine = Arc::clone(engine.inner());
    background(move || catalog::geometry(&engine.for_snapshot(&snapshot)?.connection()?, &id)).await
}

#[tauri::command]
async fn get_osm_source(
    snapshot: String,
    reference: String,
    offset: u32,
    engine: Core<'_>,
) -> AppResult<Value> {
    let engine = Arc::clone(engine.inner());
    background(move || {
        catalog::raw_source(engine.for_snapshot(&snapshot)?.as_ref(), &reference, offset)
    })
    .await
}

#[tauri::command]
async fn update_workspace(
    snapshot: String,
    id: String,
    expected: i64,
    value: workspace::Override,
    engine: Core<'_>,
) -> AppResult<workspace::State> {
    let engine = Arc::clone(engine.inner());
    background(move || {
        engine.with_workspace(&snapshot, |db, pack| {
            let entity = catalog::get(pack, &id)?.context("当前快照没有该对象")?;
            if value.point.is_some() {
                let geometry = catalog::geometry(&pack.connection()?, &entity.summary.id)?;
                ensure!(
                    !entity.has_edge
                        && geometry.as_ref().and_then(|geom| geom["type"].as_str())
                            == Some("Point"),
                    "该实体的几何编辑需要保留基础设施拓扑"
                );
            }
            workspace::apply(db, &snapshot, &entity.summary.id, expected, value)
        })
    })
    .await
}

#[tauri::command]
async fn workspace_visuals(
    snapshot: String,
    after: Option<String>,
    engine: Core<'_>,
) -> AppResult<Vec<workspace::Visual>> {
    let engine = Arc::clone(engine.inner());
    background(move || {
        engine.with_workspace(&snapshot, |db, pack| {
            workspace::visuals(db, pack, after.as_deref())
        })
    })
    .await
}

#[tauri::command]
async fn workspace_conflicts(
    snapshot: String,
    engine: Core<'_>,
) -> AppResult<Vec<workspace::Conflict>> {
    let engine = Arc::clone(engine.inner());
    background(move || {
        engine.with_workspace(&snapshot, |db, _| workspace::conflicts(db, &snapshot))
    })
    .await
}

#[tauri::command]
async fn workspace_groups(snapshot: String, engine: Core<'_>) -> AppResult<Vec<workspace::Group>> {
    let engine = Arc::clone(engine.inner());
    background(move || engine.with_workspace(&snapshot, |db, _| workspace::groups(db, &snapshot)))
        .await
}

#[tauri::command]
async fn workspace_group_members(
    snapshot: String,
    group: String,
    after: Option<String>,
    engine: Core<'_>,
) -> AppResult<catalog::Page> {
    let engine = Arc::clone(engine.inner());
    background(move || {
        engine.with_workspace(&snapshot, |db, pack| {
            workspace::group_members(db, pack, &group, after.as_deref())
        })
    })
    .await
}

#[tauri::command]
async fn replay_workspace(
    snapshot: String,
    id: String,
    expected: i64,
    redo: bool,
    engine: Core<'_>,
) -> AppResult<workspace::State> {
    let engine = Arc::clone(engine.inner());
    background(move || {
        engine.with_workspace(&snapshot, |db, _| {
            workspace::replay(db, &snapshot, &id, expected, redo)
        })
    })
    .await
}

#[tauri::command]
async fn query_path(
    snapshot: String,
    start: String,
    end: String,
    job: String,
    engine: Core<'_>,
    routes: State<'_, Arc<Routes>>,
) -> AppResult<graph::Route> {
    let engine = Arc::clone(engine.inner());
    let routes = Arc::clone(routes.inner());
    let cancel = Arc::new(AtomicBool::new(false));
    {
        let mut jobs = routes.0.lock().unwrap();
        if jobs.len() >= 2 || jobs.contains_key(&job) || job.len() > 80 {
            return Err("正在运行其他路径查询".into());
        }
        jobs.insert(job.clone(), cancel.clone());
    }
    let result = background(move || {
        graph::route(
            engine.for_snapshot(&snapshot)?.as_ref(),
            &start,
            &end,
            &cancel,
        )
    })
    .await;
    routes.0.lock().unwrap().remove(&job);
    result
}

#[tauri::command]
fn cancel_path(job: String, routes: State<'_, Arc<Routes>>) {
    if let Some(token) = routes.0.lock().unwrap().get(&job) {
        token.store(true, Ordering::Relaxed);
    }
}

#[tauri::command]
fn diagnostics(engine: Core<'_>) -> Option<pack::TileStats> {
    engine.active().map(|pack| pack.stats())
}

fn tile(engine: &Engine, path: &str) -> Result<(Arc<[u8]>, &'static str)> {
    let parts: Vec<_> = path.trim_start_matches('/').split('/').collect();
    let (snapshot, z, x, y, archive) = match parts.as_slice() {
        [snapshot, z, x, y] => (snapshot, z, x, y, false),
        [snapshot, z, x, y, "archive"] => (snapshot, z, x, y, true),
        _ => anyhow::bail!("invalid tile path"),
    };
    let pack = engine.for_tile(snapshot).context("inactive snapshot")?;
    let zoom = z.parse()?;
    let source = if !archive && pack.is_precise(zoom) {
        "original_source"
    } else {
        "overview"
    };
    Ok((
        pack.tile_mode(zoom, x.parse()?, y.parse()?, archive)?,
        source,
    ))
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let data = std::env::var_os("RAILSCOPE_NEXT_PROFILE")
                .map(PathBuf::from)
                .map_or_else(|| app.path().app_local_data_dir(), Ok)?;
            std::fs::create_dir_all(&data)?;
            let log = File::create(data.join("railscope.log"))?;
            let _ = tracing_subscriber::fmt()
                .with_ansi(false)
                .with_writer(Mutex::new(log))
                .with_env_filter(
                    tracing_subscriber::EnvFilter::try_from_default_env()
                        .unwrap_or_else(|_| "info".into()),
                )
                .try_init();
            let packs = std::env::var_os("RAILSCOPE_NEXT_PACKS")
                .map(PathBuf::from)
                .unwrap_or_else(|| data.join("packs"));
            let engine = Engine::new(packs, data.join("workspace.sqlite"))?;
            app.manage(Arc::new(engine));
            app.manage(Arc::new(Routes::default()));
            app.manage(OpsClock(Mutex::new(rail_ops::Clock::default())));
            app.manage(Arc::new(InstallJobs::default()));
            app.manage(TileWorkers(Arc::new(Semaphore::new(4))));
            Ok(())
        })
        .register_asynchronous_uri_scheme_protocol(
            "railscope-tile",
            |context, request, responder| {
                let engine = Arc::clone(context.app_handle().state::<Arc<Engine>>().inner());
                let limiter = context.app_handle().state::<TileWorkers>().0.clone();
                let origin = request
                    .headers()
                    .get(http::header::ORIGIN)
                    .and_then(|value| value.to_str().ok())
                    .unwrap_or("")
                    .to_owned();
                let allowed = matches!(
                    origin.as_str(),
                    "" | "http://tauri.localhost" | "tauri://localhost"
                ) || (cfg!(debug_assertions) && origin == "http://127.0.0.1:1420");
                let path = request.uri().path().to_owned();
                let method = request.method().clone();
                tauri::async_runtime::spawn(async move {
                    let mut response = http::Response::builder()
                        .header("content-type", "application/vnd.mapbox-vector-tile");
                    if allowed && !origin.is_empty() {
                        response = response
                            .header("access-control-allow-origin", origin)
                            .header("vary", "Origin");
                    }
                    if !allowed || method != http::Method::GET || path.len() > 140 {
                        responder.respond(response.status(403).body(Vec::new()).unwrap());
                        return;
                    }
                    let Ok(permit) = limiter.acquire_owned().await else {
                        responder.respond(response.status(503).body(Vec::new()).unwrap());
                        return;
                    };
                    let result = background(move || {
                        let _permit = permit;
                        tile(&engine, &path)
                    })
                    .await;
                    let response = match result {
                        Ok((bytes, precision)) => response
                            .header("cache-control", "public, max-age=86400, immutable")
                            .header("x-railscope-precision", precision)
                            .body(bytes.to_vec()),
                        Err(_) => response.status(404).body(Vec::new()),
                    };
                    responder.respond(response.unwrap());
                });
            },
        )
        .invoke_handler(tauri::generate_handler![
            list_data_packs,
            activate_data_pack,
            install_data_pack,
            data_pack_job,
            catalog_page,
            get_entity,
            get_geometry,
            get_osm_source,
            update_workspace,
            replay_workspace,
            workspace_visuals,
            workspace_conflicts,
            workspace_groups,
            workspace_group_members,
            query_path,
            cancel_path,
            diagnostics,
            operations,
            edit_business_line,
            operations_clock,
        ])
        .run(tauri::generate_context!())
        .expect("failed to start RailScope Next");
}
