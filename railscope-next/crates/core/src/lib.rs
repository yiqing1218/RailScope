pub mod business_lines;
pub mod cartography;
pub mod catalog;
pub mod exchange;
pub mod external_data;
pub mod graph;
pub mod operations;
pub mod pack;
pub mod precision;
mod station_marks;
mod tile_cache;
pub mod workspace;

use anyhow::{Context, Result, ensure};
use pack::{Manifest, Pack};
use rusqlite::Connection;
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex, RwLock,
        atomic::{AtomicBool, Ordering},
    },
};

type ActivePack = Option<Arc<Pack>>;

pub struct Engine {
    root: PathBuf,
    bundled: Option<PathBuf>,
    active: RwLock<ActivePack>,
    previous: Mutex<ActivePack>,
    workspace: Mutex<Connection>,
    disk_tiles: Option<Arc<tile_cache::DiskTiles>>,
}

impl Engine {
    pub fn new(root: PathBuf, workspace: PathBuf) -> Result<Self> {
        Self::with_bundled_packs(root, workspace, None)
    }

    pub fn with_bundled_packs(
        root: PathBuf,
        workspace: PathBuf,
        bundled: Option<PathBuf>,
    ) -> Result<Self> {
        fs::create_dir_all(&root)?;
        let cache_path = workspace.with_file_name("precision-tiles.sqlite");
        let disk_tiles = match tile_cache::DiskTiles::open(&cache_path) {
            Ok(cache) => Some(Arc::new(cache)),
            Err(error) => {
                tracing::warn!(%error, "could not initialize disk tile cache");
                None
            }
        };
        let db = Connection::open(workspace)?;
        workspace::initialize(&db)?;
        operations::initialize(&db)?;
        business_lines::initialize(&db)?;
        let saved = workspace::active(&db)?;
        let engine = Self {
            root,
            bundled,
            active: RwLock::new(None),
            previous: Mutex::new(None),
            workspace: Mutex::new(db),
            disk_tiles,
        };
        let packs = engine.packs()?;
        let preferred = packs
            .iter()
            .find(|pack| Some(&pack.snapshot) == saved.as_ref());
        for manifest in preferred.into_iter().chain(
            packs
                .iter()
                .filter(|pack| Some(&pack.snapshot) != saved.as_ref()),
        ) {
            match engine.activate(&manifest.snapshot) {
                Ok(_) => break,
                Err(error) => {
                    tracing::warn!(snapshot = %manifest.snapshot, %error, "could not activate pack")
                }
            }
        }
        Ok(engine)
    }

    pub fn packs(&self) -> Result<Vec<Manifest>> {
        let mut packs = pack::available(&self.root)?;
        if let Some(root) = &self.bundled {
            for manifest in pack::available(root)? {
                if !packs
                    .iter()
                    .any(|installed| installed.snapshot == manifest.snapshot)
                {
                    packs.push(manifest);
                }
            }
        }
        Ok(packs)
    }

    pub fn install_pack(
        &self,
        source: &Path,
        cancel: &AtomicBool,
        report: impl Fn(u64, u64),
    ) -> Result<Manifest> {
        let manifest = Manifest::read(source)?;
        let destination = self.root.join(&manifest.snapshot);
        ensure!(!destination.exists(), "此快照已安装，版本目录不能覆盖");
        let temporary = tempfile::Builder::new()
            .prefix(".install-")
            .tempdir_in(&self.root)?;
        let stage = temporary.path().join(&manifest.snapshot);
        fs::create_dir(&stage)?;
        let names = ["manifest.json", "canonical.sqlite", "transport.pmtiles"];
        let mut total = 0_u64;
        for name in names {
            let info = fs::symlink_metadata(source.join(name))?;
            ensure!(info.file_type().is_file(), "数据包包含无效文件");
            total = total
                .checked_add(info.len())
                .context("数据包文件大小无效")?;
        }
        let mut copied = 0;
        let mut chunk = vec![0_u8; 1024 * 1024];
        for name in names {
            let mut input = fs::File::open(source.join(name))?;
            let mut file = fs::File::create_new(stage.join(name))?;
            loop {
                ensure!(!cancel.load(Ordering::Relaxed), "数据包安装已取消");
                let count = input.read(&mut chunk)?;
                if count == 0 {
                    break;
                }
                file.write_all(&chunk[..count])?;
                copied += count as u64;
                report(copied, total);
            }
            file.sync_all()?;
        }
        drop(Pack::open(&stage)?);
        ensure!(!cancel.load(Ordering::Relaxed), "数据包安装已取消");
        fs::rename(stage, destination)?;
        Ok(manifest)
    }

    pub fn active(&self) -> ActivePack {
        self.active.read().unwrap().clone()
    }

    pub fn activate(&self, snapshot: &str) -> Result<Manifest> {
        ensure!(pack::valid_id(snapshot), "数据包标识无效");
        if let Some(active) = self
            .active()
            .filter(|pack| pack.manifest.snapshot == snapshot)
        {
            return Ok(active.manifest.clone());
        }
        let mut path = self.root.join(snapshot);
        if !path.exists()
            && let Some(root) = &self.bundled
        {
            path = root.join(snapshot);
        }
        ensure!(
            fs::symlink_metadata(&path)?.file_type().is_dir(),
            "数据包文件夹无效"
        );
        let pack = Arc::new(Pack::open_cached(&path, self.disk_tiles.clone())?);
        let mut workspace = self.workspace.lock().unwrap();
        let mut active = self.active.write().unwrap();
        if let Some(active) = active
            .as_ref()
            .filter(|pack| pack.manifest.snapshot == snapshot)
        {
            return Ok(active.manifest.clone());
        }
        workspace::activate(&mut workspace, active.as_deref(), &pack)?;
        let mut previous = self.previous.lock().unwrap();
        *previous = active.replace(pack.clone());
        Ok(pack.manifest.clone())
    }

    pub fn for_snapshot(&self, snapshot: &str) -> Result<Arc<Pack>> {
        self.active()
            .filter(|pack| pack.manifest.snapshot == snapshot)
            .context("活动数据包已切换，请刷新对象")
    }

    pub fn for_tile(&self, snapshot: &str) -> Option<Arc<Pack>> {
        self.for_snapshot(snapshot).ok().or_else(|| {
            self.previous
                .lock()
                .unwrap()
                .clone()
                .filter(|pack| pack.manifest.snapshot == snapshot)
        })
    }

    pub fn with_workspace<T>(
        &self,
        snapshot: &str,
        callback: impl FnOnce(&mut Connection, &Pack) -> Result<T>,
    ) -> Result<T> {
        let mut workspace = self.workspace.lock().unwrap();
        let active = self.for_snapshot(snapshot)?;
        callback(&mut workspace, &active)
    }
}
