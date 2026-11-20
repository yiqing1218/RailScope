pub mod catalog;
pub mod graph;
pub mod pack;
pub mod workspace;

use anyhow::{Context, Result, ensure};
use pack::{Manifest, Pack};
use rusqlite::Connection;
use std::{
    fs,
    path::PathBuf,
    sync::{Arc, Mutex, RwLock},
};

type ActivePack = Option<Arc<Pack>>;

pub struct Engine {
    root: PathBuf,
    active: RwLock<ActivePack>,
    previous: Mutex<ActivePack>,
    workspace: Mutex<Connection>,
}

impl Engine {
    pub fn new(root: PathBuf, workspace: PathBuf) -> Result<Self> {
        fs::create_dir_all(&root)?;
        let db = Connection::open(workspace)?;
        workspace::initialize(&db)?;
        let saved = workspace::active(&db)?;
        let engine = Self {
            root,
            active: RwLock::new(None),
            previous: Mutex::new(None),
            workspace: Mutex::new(db),
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
        pack::available(&self.root)
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
        let path = self.root.join(snapshot);
        ensure!(
            fs::symlink_metadata(&path)?.file_type().is_dir(),
            "数据包文件夹无效"
        );
        let pack = Arc::new(Pack::open(&path)?);
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
