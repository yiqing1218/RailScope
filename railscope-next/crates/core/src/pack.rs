use crate::{precision, tile_cache::DiskTiles};
use anyhow::{Context, Result, ensure};
use flate2::read::GzDecoder;
use lru::LruCache;
use pmtiles::{Compression, PmTilesReader, TileCoord, TileType};
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File},
    io::{self, Read},
    num::NonZeroUsize,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Instant,
};

const TILE_BYTES: usize = 32 * 1024 * 1024;
const MAX_TILE: usize = 8 * 1024 * 1024;
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
enum TileMode {
    Archive,
    Detail,
    Context,
}
type TileKey = (u8, u32, u32, TileMode);
type Tile = Arc<[u8]>;

#[derive(Clone, Serialize, Deserialize)]
pub struct Manifest {
    pub schema: u32,
    pub snapshot: String,
    pub region: String,
    pub source: String,
    pub source_file: String,
    pub source_sha256: String,
    pub license: String,
    #[serde(default)]
    pub basemap: Option<String>,
    pub generator: String,
    pub crs: String,
    pub bounds: [f64; 4],
    pub min_zoom: u8,
    pub max_zoom: u8,
    pub counts: BTreeMap<String, u64>,
    pub skipped: BTreeMap<String, u64>,
    pub files: BTreeMap<String, String>,
    #[serde(default)]
    pub source_schema: u32,
    #[serde(default)]
    pub source_objects: BTreeMap<String, u64>,
    #[serde(default)]
    pub coverage_kind: Option<String>,
}

impl Manifest {
    pub fn read(path: &Path) -> Result<Self> {
        let input = File::open(path.join("manifest.json"))?;
        let manifest: Self = serde_json::from_reader(input.take(128 * 1024))?;
        ensure!(
            manifest.schema == 1
                && path.file_name().and_then(|name| name.to_str()) == Some(&manifest.snapshot)
                && valid_id(&manifest.snapshot)
                && manifest.crs == "EPSG:4326"
                && manifest.min_zoom <= manifest.max_zoom
                && manifest.max_zoom <= 22
                && valid_bounds(manifest.bounds),
            "数据包版本或范围无效"
        );
        Ok(manifest)
    }
}

pub fn valid_id(value: &str) -> bool {
    (1..=80).contains(&value.len())
        && value.as_bytes()[0].is_ascii_alphanumeric()
        && value.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || b"-_.".contains(&byte)
        })
}

pub fn valid_bounds([west, south, east, north]: [f64; 4]) -> bool {
    (-180.0..=180.0).contains(&west)
        && (-180.0..=180.0).contains(&east)
        && (-90.0..=90.0).contains(&south)
        && (-90.0..=90.0).contains(&north)
        && west <= east
        && south <= north
}

#[derive(Clone, Default, Serialize)]
pub struct TileStats {
    pub hits: u64,
    pub misses: u64,
    pub evictions: u64,
    pub bytes: usize,
    pub read_micros: u64,
    pub pending: u64,
    pub disk_hits: u64,
    pub precise_tiles: u64,
    pub precise_micros: u64,
}

struct TileCache {
    items: LruCache<TileKey, Tile>,
    stats: TileStats,
}

pub struct Pack {
    pub manifest: Manifest,
    database: PathBuf,
    archive: PmTilesReader<File>,
    cache: Mutex<TileCache>,
    disk: Option<Arc<DiskTiles>>,
    work: [Mutex<()>; 32],
}

impl Pack {
    pub fn open(path: &Path) -> Result<Self> {
        Self::open_cached(path, None)
    }

    pub(crate) fn open_cached(path: &Path, disk: Option<Arc<DiskTiles>>) -> Result<Self> {
        let manifest = Manifest::read(path)?;
        let database = path.join("canonical.sqlite");
        let tiles = path.join("transport.pmtiles");
        for (name, file) in [
            ("canonical.sqlite", &database),
            ("transport.pmtiles", &tiles),
        ] {
            let expected = manifest.files.get(name).context("清单缺少文件校验值")?;
            let mut hash = Sha256::new();
            io::copy(&mut File::open(file)?, &mut hash)?;
            ensure!(
                &format!("{:x}", hash.finalize()) == expected,
                "数据包校验失败 · {name}"
            );
        }
        let archive = PmTilesReader::new_with_path(&tiles)?;
        let header = archive.get_header();
        ensure!(
            header.tile_type == TileType::Mvt
                && matches!(
                    header.tile_compression,
                    Compression::None | Compression::Gzip
                ),
            "数据包必须包含 MVT 矢量瓦片"
        );
        let pack = Self {
            manifest,
            database,
            archive,
            cache: Mutex::new(TileCache {
                items: LruCache::new(NonZeroUsize::new(2048).unwrap()),
                stats: TileStats::default(),
            }),
            disk,
            work: std::array::from_fn(|_| Mutex::new(())),
        };
        let connection = pack.connection()?;
        let version: u32 = connection.pragma_query_value(None, "user_version", |row| row.get(0))?;
        ensure!(version == 1, "不支持的 SQLite 快照版本");
        connection.prepare("SELECT id, render_id FROM entity LIMIT 1")?;
        if pack.manifest.source_schema >= 3 {
            connection
                .prepare("SELECT osm_id, longitude, latitude, tags FROM source_node LIMIT 1")?;
            connection.prepare("SELECT osm_id, node_ids FROM source_way LIMIT 1")?;
            connection.prepare("SELECT relation_ref, ordinal, member_ref, role FROM source_relation_member LIMIT 1")?;
            connection.prepare("SELECT source_ref, wkb FROM base_feature LIMIT 1")?;
            connection.prepare(
                "SELECT edge_id, service_id, colour_source_ref FROM metro_service_track LIMIT 1",
            )?;
        }
        tracing::info!(snapshot = %pack.manifest.snapshot, "data pack validated");
        Ok(pack)
    }

    pub fn connection(&self) -> Result<Connection> {
        Ok(Connection::open_with_flags(
            &self.database,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?)
    }

    pub fn stats(&self) -> TileStats {
        self.cache.lock().unwrap().stats.clone()
    }

    pub fn tile(&self, z: u8, x: u32, y: u32) -> Result<Tile> {
        self.tile_mode(z, x, y, false)
    }

    pub fn is_precise(&self, z: u8) -> bool {
        z >= 15 || z > self.manifest.max_zoom || (self.manifest.source_schema >= 3 && z >= 9)
    }

    pub fn tile_mode(&self, z: u8, x: u32, y: u32, archive_only: bool) -> Result<Tile> {
        let mode = if !archive_only && self.is_precise(z) {
            TileMode::Detail
        } else {
            TileMode::Archive
        };
        self.cached_tile(z, x, y, mode)
    }

    pub fn context_tile(&self, z: u8, x: u32, y: u32) -> Result<Tile> {
        ensure!((3..=8).contains(&z), "无效底图概览级别");
        if self.manifest.source_schema < 3 {
            return Ok(Arc::from([]));
        }
        self.cached_tile(z, x, y, TileMode::Context)
    }

    fn cached_tile(&self, z: u8, x: u32, y: u32, mode: TileMode) -> Result<Tile> {
        let coord = TileCoord::new(z, x, y)?;
        let key = (z, x, y, mode);
        let _work = self.work
            [((x as usize).wrapping_mul(31) ^ y as usize ^ z as usize) % self.work.len()]
        .lock()
        .unwrap();
        {
            let mut cache = self.cache.lock().unwrap();
            if let Some(hit) = cache.items.get(&key).cloned() {
                cache.stats.hits += 1;
                return Ok(hit);
            }
            cache.stats.misses += 1;
            cache.stats.pending += 1;
        }
        let start = Instant::now();
        let mut disk_hit = false;
        let result: Result<Tile> = if mode != TileMode::Archive {
            (|| {
                let style_version = if mode == TileMode::Context {
                    "v6-context"
                } else {
                    "v7-sites"
                };
                let cache_key = format!(
                    "{style_version}:{}:{z}/{x}/{y}",
                    self.manifest.files["canonical.sqlite"]
                );
                if let Some(disk) = &self.disk
                    && let Ok(Some(tile)) = disk.get(&cache_key)
                {
                    disk_hit = true;
                    return Ok(Arc::from(tile));
                }
                let db = self.connection()?;
                let address = precision::Address::new(z, x, y)?;
                let data = if mode == TileMode::Context {
                    precision::generate_context(&db, address)?
                } else {
                    precision::generate(&db, address)?
                };
                if let Some(disk) = &self.disk
                    && let Err(error) = disk.put(&cache_key, &data)
                {
                    tracing::warn!(%error,"could not cache precise tile");
                }
                Ok(Arc::from(data))
            })()
        } else {
            self.read_tile(coord)
        };
        let mut cache = self.cache.lock().unwrap();
        cache.stats.pending -= 1;
        let elapsed = start.elapsed().as_micros() as u64;
        cache.stats.read_micros += elapsed;
        if disk_hit {
            cache.stats.disk_hits += 1;
        }
        if mode == TileMode::Detail && !disk_hit && result.is_ok() {
            cache.stats.precise_tiles += 1;
            cache.stats.precise_micros += elapsed;
        }
        let tile = result?;
        if let Some(existing) = cache.items.get(&key) {
            return Ok(existing.clone());
        }
        while cache.stats.bytes + tile.len() > TILE_BYTES
            || cache.items.len() == cache.items.cap().get()
        {
            if let Some((_, evicted)) = cache.items.pop_lru() {
                cache.stats.bytes -= evicted.len();
                cache.stats.evictions += 1;
            }
        }
        cache.stats.bytes += tile.len();
        cache.items.put(key, tile.clone());
        Ok(tile)
    }

    fn read_tile(&self, coord: TileCoord) -> Result<Tile> {
        let Some(raw) = self.archive.get_tile(coord)? else {
            return Ok(Arc::from([]));
        };
        ensure!(raw.len() <= MAX_TILE, "压缩瓦片超出大小限制");
        let tile = if self.archive.get_header().tile_compression == Compression::Gzip {
            let mut payload = Vec::new();
            GzDecoder::new(raw.as_ref())
                .take((MAX_TILE + 1) as u64)
                .read_to_end(&mut payload)?;
            ensure!(payload.len() <= MAX_TILE, "瓦片超出解压大小限制");
            Arc::from(payload)
        } else {
            Arc::from(raw.as_ref())
        };
        Ok(tile)
    }
}

pub fn available(root: &Path) -> Result<Vec<Manifest>> {
    let mut packs = Vec::new();
    for entry in fs::read_dir(root)? {
        let entry = entry?;
        if entry.file_type()?.is_dir()
            && let Ok(manifest) = Manifest::read(&entry.path())
        {
            packs.push(manifest);
        }
    }
    packs.sort_by(|a, b| {
        (b.region == "全国", b.source_schema, &b.snapshot).cmp(&(
            a.region == "全国",
            a.source_schema,
            &a.snapshot,
        ))
    });
    Ok(packs)
}
