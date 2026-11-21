use anyhow::Result;
use rusqlite::{Connection, OptionalExtension, params};
use std::{path::Path, sync::Mutex};

const BUDGET: i64 = 384 * 1024 * 1024;

pub struct DiskTiles(Mutex<Connection>);

impl DiskTiles {
    pub fn open(path: &Path) -> Result<Self> {
        let db = Connection::open(path)?;
        db.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA synchronous=NORMAL;
             PRAGMA journal_size_limit=16777216;
             CREATE TABLE IF NOT EXISTS tile (
               key TEXT NOT NULL UNIQUE, payload BLOB NOT NULL, touched INTEGER NOT NULL
             ) STRICT;
             CREATE INDEX IF NOT EXISTS tile_touch ON tile(touched);
             CREATE TABLE IF NOT EXISTS size (id INTEGER PRIMARY KEY CHECK(id=1), bytes INTEGER NOT NULL);
             INSERT OR IGNORE INTO size SELECT 1, IFNULL(sum(length(payload)),0) FROM tile;
             CREATE TRIGGER IF NOT EXISTS tile_added AFTER INSERT ON tile BEGIN
               UPDATE size SET bytes=bytes+length(NEW.payload) WHERE id=1;
             END;
             CREATE TRIGGER IF NOT EXISTS tile_removed AFTER DELETE ON tile BEGIN
               UPDATE size SET bytes=bytes-length(OLD.payload) WHERE id=1;
             END;",
        )?;
        Ok(Self(Mutex::new(db)))
    }

    pub fn get(&self, key: &str) -> Result<Option<Vec<u8>>> {
        let db = self.0.lock().unwrap();
        let data = db
            .query_row("SELECT payload FROM tile WHERE key=?", [key], |r| r.get(0))
            .optional()?;
        if data.is_some() {
            db.execute(
                "UPDATE tile SET touched=unixepoch() WHERE key=? AND touched<unixepoch()-5",
                [key],
            )?;
        }
        Ok(data)
    }

    pub fn put(&self, key: &str, payload: &[u8]) -> Result<()> {
        let mut db = self.0.lock().unwrap();
        let tx = db.transaction()?;
        tx.execute(
            "INSERT OR IGNORE INTO tile VALUES(?,?,unixepoch())",
            params![key, payload],
        )?;
        while tx.query_row("SELECT bytes FROM size WHERE id=1", [], |r| {
            r.get::<_, i64>(0)
        })? > BUDGET
        {
            if tx.execute("DELETE FROM tile WHERE rowid IN (SELECT rowid FROM tile ORDER BY touched LIMIT 24)",[])?==0 {
                break;
            }
        }
        tx.commit()?;
        Ok(())
    }
}
