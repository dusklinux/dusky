use anyhow::{Context, Result, bail};
use rusqlite::{Connection, OptionalExtension, params, params_from_iter, types::Value as SqlValue};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::{HashMap, HashSet},
    os::unix::fs::{MetadataExt, PermissionsExt},
    path::{Path, PathBuf},
    time::{Duration, SystemTime, UNIX_EPOCH},
};

const ID_CHUNK_SIZE: usize = 500;

pub fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64
}

pub fn xdg(var: &str, fallback: &str) -> PathBuf {
    std::env::var_os(var)
        .filter(|p| !p.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(std::env::var_os("HOME").expect("HOME must be set")).join(fallback)
        })
}

pub fn default_path() -> PathBuf {
    xdg("XDG_DATA_HOME", ".local/share").join("dusky_casts/library.db")
}

pub fn legacy_default_path() -> PathBuf {
    xdg("XDG_DATA_HOME", ".local/share").join("duskcasts/library.db")
}

fn sqlite_sidecar(path: &Path, suffix: &str) -> PathBuf {
    let mut os = path.as_os_str().to_os_string();
    os.push(suffix);
    PathBuf::from(os)
}

fn remove_file_if_exists(path: &Path) -> Result<()> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(err) => Err(err).with_context(|| format!("Removing {}", path.display())),
    }
}

struct CleanupGuard(Option<PathBuf>);

impl CleanupGuard {
    fn new(path: PathBuf) -> Self {
        Self(Some(path))
    }

    fn disarm(&mut self) {
        self.0 = None;
    }
}

impl Drop for CleanupGuard {
    fn drop(&mut self) {
        if let Some(path) = self.0.take() {
            let _ = std::fs::remove_file(&path);
            let _ = std::fs::remove_file(sqlite_sidecar(&path, "-wal"));
            let _ = std::fs::remove_file(sqlite_sidecar(&path, "-shm"));
            let _ = std::fs::remove_file(sqlite_sidecar(&path, "-journal"));
        }
    }
}

/// One-shot, non-destructive migration from `$XDG_DATA_HOME/duskcasts/library.db`
/// into `target`. Returns `Ok(true)` if a migration occurred, `Ok(false)` otherwise.
pub fn migrate_legacy_library(target: &Path) -> Result<bool> {
    migrate_legacy_library_from(&legacy_default_path(), target)
}

/// Migrate a legacy SQLite library (including uncheckpointed WAL frames) into `target`
/// via `VACUUM INTO`, holding an exclusive non-blocking lock on the legacy lockfile.
pub fn migrate_legacy_library_from(legacy_db: &Path, target: &Path) -> Result<bool> {
    if target.exists() || !legacy_db.is_file() {
        return Ok(false);
    }

    let legacy_dir = legacy_db
        .parent()
        .context("Legacy library has no parent directory")?;
    let target_dir = target
        .parent()
        .context("Target library has no parent directory")?;

    // Hold the legacy instance lock across the entire migration so we never race
    // a running pre-rename `duskcasts` process.
    let legacy_lock_path = legacy_db.with_extension("lock");
    let legacy_lock = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(&legacy_lock_path)
        .with_context(|| format!("Opening legacy lock {}", legacy_lock_path.display()))?;
    match legacy_lock.try_lock() {
        Ok(()) => {}
        Err(std::fs::TryLockError::WouldBlock) => {
            bail!(
                "Legacy Dusk Casts instance is still running; close it before starting Dusky Casts"
            );
        }
        Err(std::fs::TryLockError::Error(err)) => {
            return Err(err)
                .with_context(|| format!("Locking legacy library {}", legacy_lock_path.display()));
        }
    }

    std::fs::create_dir_all(target_dir)
        .with_context(|| format!("Creating library directory {}", target_dir.display()))?;

    let target_name = target
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("library.db");
    let staging = target_dir.join(format!(".{target_name}.migrating"));
    remove_file_if_exists(&staging)?;
    remove_file_if_exists(&sqlite_sidecar(&staging, "-wal"))?;
    remove_file_if_exists(&sqlite_sidecar(&staging, "-shm"))?;
    remove_file_if_exists(&sqlite_sidecar(&staging, "-journal"))?;

    let mut cleanup = CleanupGuard::new(staging.clone());
    let staging_str = staging
        .to_str()
        .context("Migration staging path must be valid UTF-8")?;

    // Opening legacy_db recovers any uncheckpointed -wal/-shm frames; VACUUM INTO
    // writes a standalone compacted copy preserving schema, data, indexes, and user_version.
    {
        let legacy_conn = Connection::open(legacy_db)
            .with_context(|| format!("Opening legacy library {}", legacy_db.display()))?;
        legacy_conn.busy_timeout(Duration::from_secs(10))?;
        legacy_conn
            .execute("VACUUM INTO ?1", [staging_str])
            .with_context(|| format!("Migrating legacy library into {}", staging.display()))?;
    }

    std::fs::set_permissions(&staging, std::fs::Permissions::from_mode(0o600))
        .with_context(|| format!("Setting permissions on {}", staging.display()))?;

    // Rewrite `discovered_ftp_stats_dir` inside the staging DB if it pointed directly
    // at the legacy data directory.
    {
        let staging_conn = Connection::open(&staging)
            .with_context(|| format!("Opening migrated staging DB {}", staging.display()))?;
        staging_conn.busy_timeout(Duration::from_secs(10))?;
        let has_settings: bool = staging_conn
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'settings')",
                [],
                |row| row.get(0),
            )
            .unwrap_or(false);
        if has_settings {
            let discovered: Option<String> = staging_conn
                .query_row(
                    "SELECT value FROM settings WHERE key = 'discovered_ftp_stats_dir'",
                    [],
                    |row| row.get(0),
                )
                .optional()?;
            if let Some(dir_str) = discovered
                && !dir_str.trim().is_empty()
            {
                let old_canon = legacy_dir
                    .canonicalize()
                    .unwrap_or_else(|_| legacy_dir.to_path_buf());
                let saved_path = PathBuf::from(dir_str.trim());
                let saved_canon = saved_path.canonicalize().unwrap_or(saved_path);
                if saved_canon == old_canon {
                    let new_dir = target_dir
                        .canonicalize()
                        .unwrap_or_else(|_| target_dir.to_path_buf());
                    staging_conn.execute(
                        "UPDATE settings SET value = ?1 WHERE key = 'discovered_ftp_stats_dir'",
                        [new_dir.to_string_lossy().as_ref()],
                    )?;
                }
            }
        }
    }

    // Clear any orphaned target WAL/SHM files before publishing the migrated database.
    remove_file_if_exists(&sqlite_sidecar(target, "-wal"))?;
    remove_file_if_exists(&sqlite_sidecar(target, "-shm"))?;
    std::fs::rename(&staging, target).with_context(|| {
        format!(
            "Publishing migrated library {} -> {}",
            staging.display(),
            target.display()
        )
    })?;
    cleanup.disarm();

    // Copy any legacy iOS stats manifest stored inside the legacy data directory.
    let target_ios_stats = target_dir.join(crate::listening::IOS_STATS_FILENAME);
    if !target_ios_stats.exists() {
        let candidate = [
            legacy_dir.join(crate::listening::IOS_STATS_FILENAME),
            legacy_dir.join(crate::listening::LEGACY_IOS_STATS_FILENAME),
        ]
        .into_iter()
        .find(|p| p.is_file());
        if let Some(source_ios) = candidate {
            let tmp_ios = target_dir.join(".dusky_casts-stats-ios.json.migrating");
            if let Ok(bytes) = std::fs::read(&source_ios)
                && std::fs::write(&tmp_ios, bytes).is_ok()
            {
                let _ = std::fs::set_permissions(&tmp_ios, std::fs::Permissions::from_mode(0o600));
                if std::fs::rename(&tmp_ios, &target_ios_stats).is_err() {
                    let _ = std::fs::remove_file(&tmp_ios);
                }
            }
        }
    }

    drop(legacy_lock);
    Ok(true)
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Episode {
    pub id: i64,
    pub uuid: String,
    pub podcast_uuid: String,
    pub podcast: String,
    pub title: String,
    pub description: String,
    pub audio_url: String,
    pub downloaded_path: String,
    pub duration: i64,
    pub position: i64,
    pub playing_status: i32,
    pub starred: bool,
    pub archived: bool,
    pub published_at: i64,
}

impl Episode {
    pub fn is_local(&self) -> bool {
        self.uuid.starts_with("local:")
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Podcast {
    pub id: i64,
    pub uuid: String,
    pub title: String,
    pub author: String,
    pub description: String,
    pub artwork_url: String,
    pub feed_url: String,
    pub added_at: i64,
    pub last_refreshed: i64,
    pub sort_order: i64,
    pub folder_uuid: String,
    pub folder_name: String,
    pub episodes_sort: i64,
    pub show_archived: bool,
    pub unplayed_count: i64,
    pub episode_count: i64,
    pub archived_count: i64,
    pub latest_published_at: i64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum UpNextSort {
    Newest,
    Oldest,
    Shortest,
    Longest,
}

struct PlaylistRow {
    manual: bool,
    show_archived: bool,
    all_podcasts: bool,
    podcast_uuids: String,
    audio_video: i64,
    finished: bool,
    partially_played: bool,
    unplayed: bool,
    downloaded: bool,
    not_downloaded: bool,
    downloading: bool,
    starred: bool,
    filter_hours: i64,
    filter_duration: bool,
    longer_than: i64,
    shorter_than: i64,
    sort_type: i64,
}

struct PlaylistQueryParts {
    where_sql: String,
    where_params: Vec<SqlValue>,
    order_sql: &'static str,
    order_uses_playlist_id: bool,
}

struct BuiltFilterQuery {
    from_sql: &'static str,
    where_sql: String,
    where_params: Vec<SqlValue>,
    order_sql: String,
}

pub struct Db {
    pub conn: Connection,
    pub path: PathBuf,
    pub has_legacy_local_files: bool,
    local_files_empty: bool,
}

impl Db {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let conn = Connection::open(path)?;
        conn.busy_timeout(Duration::from_secs(10))?;
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA synchronous=NORMAL;
             PRAGMA temp_store=MEMORY;
             PRAGMA mmap_size=67108864;
             PRAGMA foreign_keys=ON;",
        )?;
        let version: i32 = conn.query_row("PRAGMA user_version", [], |r| r.get(0))?;
        if version < 1 {
            conn.execute_batch(include_str!("schema.sql"))?;
            // Additive migrations from older Python releases; retain all user data.
            for (table, fields) in [
                (
                    "episodes",
                    vec![
                        "playing_status_modified",
                        "played_up_to_modified",
                        "starred_modified",
                        "duration_modified",
                        "archived",
                        "archived_modified",
                        "needs_sync",
                    ],
                ),
                ("podcasts", vec!["needs_sync", "subscribed"]),
            ] {
                let columns = conn
                    .prepare(&format!("PRAGMA table_info({table})"))?
                    .query_map([], |r| r.get::<_, String>(1))?
                    .collect::<rusqlite::Result<Vec<_>>>()?;
                for field in fields {
                    if !columns.iter().any(|c| c == field) {
                        let default = if field == "subscribed" { 1 } else { 0 };
                        conn.execute_batch(&format!(
                            "ALTER TABLE {table} ADD COLUMN {field} INTEGER DEFAULT {default}"
                        ))?;
                    }
                }
            }
            conn.execute_batch(
                "CREATE TABLE IF NOT EXISTS remote_episodes(uuid TEXT PRIMARY KEY, record BLOB NOT NULL);
                 CREATE TABLE IF NOT EXISTS rust_runtime(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                 PRAGMA user_version = 1;",
            )?;
        }
        if version < 2 {
            let lf_cols = conn
                .prepare("PRAGMA table_info(local_files)")?
                .query_map([], |r| r.get::<_, String>(1))?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            for (col, def) in [
                ("parent_dir", "TEXT NOT NULL DEFAULT ''"),
                ("file_name", "TEXT NOT NULL DEFAULT ''"),
                ("leading_num", "INTEGER"),
                ("mtime_secs", "INTEGER NOT NULL DEFAULT 0"),
            ] {
                if !lf_cols.iter().any(|c| c == col) {
                    conn.execute_batch(&format!("ALTER TABLE local_files ADD COLUMN {col} {def}"))?;
                }
            }
            conn.execute_batch(
                "UPDATE local_files
                 SET file_name = path,
                     mtime_secs = CAST(modified AS INTEGER)
                 WHERE file_name = '';
                 CREATE TABLE IF NOT EXISTS ios_listening_days (
                     day TEXT PRIMARY KEY,
                     milliseconds INTEGER NOT NULL DEFAULT 0 CHECK(milliseconds >= 0),
                     updated_at INTEGER NOT NULL DEFAULT 0
                 );
                 PRAGMA user_version = 2;",
            )?;
        }
        if version < 3 {
            conn.execute_batch(
                "DROP INDEX IF EXISTS idx_local_files_sort_filename;
                 DROP INDEX IF EXISTS idx_local_files_sort_path;
                 CREATE INDEX IF NOT EXISTS idx_local_files_available_ep ON local_files(available, episode_id);
                 CREATE INDEX IF NOT EXISTS idx_local_files_ep ON local_files(episode_id) WHERE episode_id IS NOT NULL;
                 CREATE INDEX IF NOT EXISTS idx_local_files_sort_filename ON local_files(
                     available,
                     (CASE WHEN leading_num IS NOT NULL THEN 0 ELSE 1 END) ASC,
                     leading_num ASC,
                     file_name COLLATE NOCASE ASC,
                     path ASC
                 );
                 CREATE INDEX IF NOT EXISTS idx_local_files_sort_path ON local_files(
                     available,
                     parent_dir COLLATE NOCASE ASC,
                     (CASE WHEN leading_num IS NOT NULL THEN 0 ELSE 1 END) ASC,
                     leading_num ASC,
                     file_name COLLATE NOCASE ASC,
                     path ASC
                 );
                 CREATE INDEX IF NOT EXISTS idx_local_files_sort_mtime ON local_files(available, mtime_secs ASC, path ASC);
                 CREATE INDEX IF NOT EXISTS idx_queue_position ON queue(position, id);
                 CREATE INDEX IF NOT EXISTS idx_local_queue_position ON local_queue(position, episode_id);
                 CREATE INDEX IF NOT EXISTS idx_playlist_episodes_uuid ON playlist_episodes(episode_uuid);
                 PRAGMA user_version = 3;",
            )?;
        }
        if version < 4 {
            let pod_cols = conn
                .prepare("PRAGMA table_info(podcasts)")?
                .query_map([], |r| r.get::<_, String>(1))?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            for (col, def) in [
                ("subscribed", "INTEGER DEFAULT 1"),
                ("episodes_sort", "INTEGER DEFAULT 0"),
                ("show_archived", "INTEGER DEFAULT 0"),
            ] {
                if !pod_cols.iter().any(|c| c == col) {
                    conn.execute_batch(&format!("ALTER TABLE podcasts ADD COLUMN {col} {def}"))?;
                }
            }
            conn.execute_batch(
                "CREATE INDEX IF NOT EXISTS idx_episodes_podcast_archived_pub ON episodes(podcast_id, archived, published_at DESC, id DESC);
                 CREATE INDEX IF NOT EXISTS idx_episodes_archived_pub ON episodes(archived, published_at DESC, id DESC);
                 CREATE INDEX IF NOT EXISTS idx_episodes_status_archived ON episodes(playing_status, archived);
                 CREATE INDEX IF NOT EXISTS idx_episodes_starred ON episodes(published_at DESC, id DESC) WHERE starred=1;
                 CREATE INDEX IF NOT EXISTS idx_episodes_downloaded ON episodes(published_at DESC, id DESC) WHERE downloaded_path!='';
                 CREATE INDEX IF NOT EXISTS idx_queue_position ON queue(position, id);
                 CREATE INDEX IF NOT EXISTS idx_local_queue_position ON local_queue(position, episode_id);
                 CREATE INDEX IF NOT EXISTS idx_playlist_episodes_uuid ON playlist_episodes(episode_uuid);
                 PRAGMA user_version = 4;",
            )?;
        }
        let mut db = Self {
            conn,
            path: path.to_path_buf(),
            has_legacy_local_files: false,
            local_files_empty: true,
        };
        db.refresh_local_sort_cache();
        if db.get("sync_device_id")?.is_empty() {
            db.set("sync_device_id", &uuid::Uuid::new_v4().to_string())?;
        }
        Ok(db)
    }

    pub fn optimize(&self) -> Result<()> {
        self.conn.execute_batch("PRAGMA optimize;")?;
        Ok(())
    }

    pub fn refresh_local_sort_cache(&mut self) {
        let has_files: bool = self
            .conn
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM local_files WHERE available=1)",
                [],
                |r| r.get(0),
            )
            .unwrap_or(false);
        if !has_files {
            self.has_legacy_local_files = false;
            self.local_files_empty = true;
            return;
        }
        self.local_files_empty = false;
        self.has_legacy_local_files = self
            .conn
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM local_files WHERE available=1 AND (file_name='' OR (leading_num IS NULL AND file_name GLOB '[0-9]*')))",
                [],
                |r| r.get(0),
            )
            .unwrap_or(false);
    }

    pub fn get(&self, key: &str) -> Result<String> {
        Ok(self
            .conn
            .query_row("SELECT value FROM settings WHERE key=?", [key], |r| {
                r.get::<_, String>(0)
            })
            .optional()?
            .unwrap_or_default())
    }

    pub fn number(&self, key: &str) -> Result<i64> {
        Ok(self.get(key)?.parse().unwrap_or(0))
    }

    pub fn set(&self, key: &str, value: &str) -> Result<()> {
        self.conn.execute(
            "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE value!=excluded.value",
            params![key, value],
        )?;
        Ok(())
    }

    pub fn rows(&self, sql: &str) -> Result<Vec<Value>> {
        let mut stmt = self.conn.prepare(sql)?;
        let names: Vec<String> = stmt.column_names().map(|s| s.to_string()).collect();
        let rows = stmt.query_map([], |row| {
            let mut map = serde_json::Map::new();
            for (i, name) in names.iter().enumerate() {
                use rusqlite::types::ValueRef;
                let value = match row.get_ref(i)? {
                    ValueRef::Null => Value::Null,
                    ValueRef::Integer(v) => v.into(),
                    ValueRef::Real(v) => v.into(),
                    ValueRef::Text(v) => String::from_utf8_lossy(v).into_owned().into(),
                    ValueRef::Blob(_) => Value::Null,
                };
                map.insert(name.clone(), value);
            }
            Ok(Value::Object(map))
        })?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    fn podcast_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<Podcast> {
        Ok(Podcast {
            id: r.get(0)?,
            uuid: r.get(1)?,
            title: r.get(2)?,
            author: r.get(3)?,
            description: r.get(4)?,
            artwork_url: r.get(5)?,
            feed_url: r.get(6)?,
            added_at: r.get(7)?,
            last_refreshed: r.get(8)?,
            sort_order: r.get(9)?,
            folder_uuid: r.get(10)?,
            folder_name: r.get(11)?,
            episodes_sort: r.get(12)?,
            show_archived: r.get::<_, i64>(13)? != 0,
            unplayed_count: r.get(14)?,
            episode_count: r.get(15)?,
            archived_count: r.get(16)?,
            latest_published_at: r.get(17)?,
        })
    }

    pub fn podcast_summaries(&self) -> Result<Vec<Podcast>> {
        let mut stmt = self.conn.prepare(
            "SELECT
                p.id,
                p.uuid,
                p.title,
                p.author,
                p.description,
                p.artwork_url,
                p.feed_url,
                p.added_at,
                p.last_refreshed,
                p.sort_order,
                p.folder_uuid,
                coalesce(f.name, '') AS folder_name,
                coalesce(p.episodes_sort, 0) AS episodes_sort,
                coalesce(p.show_archived, 0) AS show_archived,
                coalesce(agg.unplayed_count, 0) AS unplayed_count,
                coalesce(agg.episode_count, 0) AS episode_count,
                coalesce(agg.archived_count, 0) AS archived_count,
                coalesce(agg.latest_published_at, 0) AS latest_published_at
             FROM podcasts p
             LEFT JOIN folders f ON f.uuid = p.folder_uuid AND f.is_deleted = 0
             LEFT JOIN (
                 SELECT
                     podcast_id,
                     SUM(CASE WHEN archived = 0 AND playing_status != 2 THEN 1 ELSE 0 END) AS unplayed_count,
                     COUNT(*) AS episode_count,
                     SUM(CASE WHEN archived = 1 THEN 1 ELSE 0 END) AS archived_count,
                     MAX(published_at) AS latest_published_at
                 FROM episodes
                 GROUP BY podcast_id
             ) agg ON agg.podcast_id = p.id
             WHERE p.subscribed = 1
             ORDER BY p.sort_order, p.title COLLATE NOCASE",
        )?;
        Ok(stmt
            .query_map([], Self::podcast_row)?
            .collect::<rusqlite::Result<Vec<_>>>()?)
    }

    pub fn podcasts(&self) -> Result<Vec<Podcast>> {
        self.podcast_summaries()
    }

    pub fn podcast_by_id(&self, id: i64) -> Result<Option<Podcast>> {
        let mut stmt = self.conn.prepare(
            "SELECT
                p.id,
                p.uuid,
                p.title,
                p.author,
                p.description,
                p.artwork_url,
                p.feed_url,
                p.added_at,
                p.last_refreshed,
                p.sort_order,
                p.folder_uuid,
                coalesce(f.name, '') AS folder_name,
                coalesce(p.episodes_sort, 0) AS episodes_sort,
                coalesce(p.show_archived, 0) AS show_archived,
                coalesce(agg.unplayed_count, 0) AS unplayed_count,
                coalesce(agg.episode_count, 0) AS episode_count,
                coalesce(agg.archived_count, 0) AS archived_count,
                coalesce(agg.latest_published_at, 0) AS latest_published_at
             FROM podcasts p
             LEFT JOIN folders f ON f.uuid = p.folder_uuid AND f.is_deleted = 0
             LEFT JOIN (
                 SELECT
                     podcast_id,
                     SUM(CASE WHEN archived = 0 AND playing_status != 2 THEN 1 ELSE 0 END) AS unplayed_count,
                     COUNT(*) AS episode_count,
                     SUM(CASE WHEN archived = 1 THEN 1 ELSE 0 END) AS archived_count,
                     MAX(published_at) AS latest_published_at
                 FROM episodes
                 WHERE podcast_id = ?1
                 GROUP BY podcast_id
             ) agg ON agg.podcast_id = p.id
             WHERE p.id = ?1",
        )?;
        Ok(stmt.query_row([id], Self::podcast_row).optional()?)
    }

    pub fn set_podcast_sort(&mut self, podcast_id: i64, sort: i64) -> Result<()> {
        let clamped = sort.clamp(0, 3);
        self.conn.execute(
            "UPDATE podcasts SET episodes_sort = ?1 WHERE id = ?2",
            params![clamped, podcast_id],
        )?;
        Ok(())
    }

    pub fn set_podcast_show_archived(
        &mut self,
        podcast_id: i64,
        show_archived: bool,
    ) -> Result<()> {
        self.conn.execute(
            "UPDATE podcasts SET show_archived = ?1 WHERE id = ?2",
            params![show_archived as i64, podcast_id],
        )?;
        Ok(())
    }

    pub fn listening_days_all(&self) -> Result<Vec<(String, i64)>> {
        let mut stmt = self
            .conn
            .prepare("SELECT day, milliseconds FROM listening_days ORDER BY day DESC")?;
        Ok(stmt
            .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))?
            .collect::<rusqlite::Result<Vec<_>>>()?)
    }

    pub fn ios_listening_days_all(&self) -> Result<Vec<(String, i64)>> {
        let mut stmt = self
            .conn
            .prepare("SELECT day, milliseconds FROM ios_listening_days ORDER BY day DESC")?;
        Ok(stmt
            .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))?
            .collect::<rusqlite::Result<Vec<_>>>()?)
    }

    pub fn has_indexed_local_files(&self) -> Result<bool> {
        Ok(self.conn.query_row(
            "SELECT EXISTS(SELECT 1 FROM local_files WHERE available=1 AND episode_id IS NOT NULL)",
            [],
            |r| r.get(0),
        )?)
    }

    pub fn has_any_indexed_local_files(&self) -> Result<bool> {
        Ok(self.conn.query_row(
            "SELECT EXISTS(SELECT 1 FROM local_files WHERE episode_id IS NOT NULL)",
            [],
            |r| r.get(0),
        )?)
    }

    pub fn sample_indexed_files_exist(&self) -> bool {
        if let Ok(mut stmt) = self.conn.prepare(
            "SELECT path FROM local_files WHERE episode_id IS NOT NULL ORDER BY available DESC LIMIT 3",
        ) && let Ok(urls) = stmt.query_map([], |r| r.get::<_, String>(0))
        {
            let mut checked = 0;
            for url in urls.flatten() {
                checked += 1;
                if reqwest::Url::parse(&url)
                    .ok()
                    .and_then(|u| u.to_file_path().ok())
                    .is_some_and(|p| p.is_file())
                {
                    return true;
                }
            }
            return checked == 0;
        }
        true
    }

    pub fn is_local_drive_accessible(&self, configured_path: &str) -> bool {
        let trimmed = configured_path.trim();
        if trimmed.is_empty() {
            return true;
        }
        let resolved = if trimmed == "~" {
            xdg("HOME", "")
        } else if let Some(rest) = trimmed.strip_prefix("~/") {
            xdg("HOME", "").join(rest)
        } else {
            PathBuf::from(trimmed)
        };
        let Ok(meta) = std::fs::metadata(&resolved) else {
            return false;
        };
        if !meta.is_dir() {
            return false;
        }
        let has_indexed = self.has_any_indexed_local_files().unwrap_or(false);
        if !has_indexed {
            return true;
        }
        let Ok(mut entries) = std::fs::read_dir(&resolved) else {
            return false;
        };
        if entries.next().is_none() {
            return false;
        }
        let saved_dev = self.number("local_library_dev").unwrap_or(0) as u64;
        if saved_dev > 0 && meta.dev() != saved_dev && !self.sample_indexed_files_exist() {
            return false;
        }
        self.sample_indexed_files_exist()
    }

    pub fn queued_ids(&self) -> Result<HashSet<i64>> {
        let mut set = HashSet::new();
        for sql in [
            "SELECT episode_id FROM queue",
            "SELECT episode_id FROM local_queue",
        ] {
            let mut stmt = self.conn.prepare(sql)?;
            for id in stmt.query_map([], |r| r.get::<_, i64>(0))? {
                set.insert(id?);
            }
        }
        Ok(set)
    }

    fn local_order_sql(&self) -> Result<String> {
        let sort = self.get("local_sort")?;
        let asc = self.get("local_sort_asc")? != "0";
        let dir = if asc { "ASC" } else { "DESC" };
        let use_legacy = self.has_legacy_local_files
            || (self.local_files_empty
                && self
                    .conn
                    .query_row(
                        "SELECT EXISTS(SELECT 1 FROM local_files WHERE available=1 AND (file_name='' OR (leading_num IS NULL AND file_name GLOB '[0-9]*')))",
                        [],
                        |r| r.get(0),
                    )
                    .unwrap_or(false));
        if !use_legacy {
            return Ok(match sort.as_str() {
                "filename" => format!(
                    "(CASE WHEN lf.leading_num IS NOT NULL THEN 0 ELSE 1 END) {dir}, lf.leading_num {dir}, lf.file_name COLLATE NOCASE {dir}, lf.path {dir}"
                ),
                "path" => format!(
                    "lf.parent_dir COLLATE NOCASE {dir}, (CASE WHEN lf.leading_num IS NOT NULL THEN 0 ELSE 1 END) {dir}, lf.leading_num {dir}, lf.file_name COLLATE NOCASE {dir}, lf.path {dir}"
                ),
                "modified" => format!("lf.mtime_secs {dir}, lf.path {dir}"),
                "date" => format!(
                    "CASE WHEN e.published_at>0 THEN e.published_at ELSE e.added_at END {dir}, e.id {dir}"
                ),
                "duration" => {
                    format!("e.duration {dir}, e.title COLLATE NOCASE ASC, e.audio_url ASC")
                }
                _ => format!("e.title COLLATE NOCASE {dir}, e.audio_url {dir}"),
            });
        }
        let raw_basename = "substr(e.description, length(rtrim(e.description, replace(e.description, '/', ''))) + 1)";
        let fname = format!(
            "CASE WHEN lf.file_name IS NOT NULL AND lf.file_name!='' THEN lf.file_name ELSE {raw_basename} END"
        );
        let parent_path = format!(
            "CASE WHEN lf.parent_dir IS NOT NULL AND lf.parent_dir!='' THEN lf.parent_dir WHEN length(e.description)>length({raw_basename}) THEN substr(e.description,1,length(e.description)-length({raw_basename})-1) ELSE '' END"
        );
        let num_order = format!(
            "CASE WHEN lf.leading_num IS NOT NULL THEN 0 WHEN substr({fname},1,1) BETWEEN '0' AND '9' THEN 0 ELSE 1 END {dir}, CASE WHEN lf.leading_num IS NOT NULL THEN lf.leading_num WHEN substr({fname},1,1) BETWEEN '0' AND '9' THEN CAST({fname} AS INTEGER) END {dir}, {fname} COLLATE NOCASE {dir}"
        );
        let mod_order = format!(
            "CASE WHEN lf.mtime_secs>0 THEN lf.mtime_secs ELSE CAST(lf.modified AS INTEGER) END {dir}, CAST(substr(lf.modified, instr(lf.modified, ':') + 1) AS INTEGER) {dir}, e.audio_url {dir}"
        );
        Ok(match sort.as_str() {
            "filename" => {
                format!("{num_order}, e.description COLLATE NOCASE {dir}, e.audio_url {dir}")
            }
            "path" => format!(
                "{parent_path} COLLATE NOCASE {dir}, {num_order}, e.description COLLATE NOCASE {dir}, e.audio_url {dir}"
            ),
            "modified" => mod_order,
            "date" => format!(
                "CASE WHEN e.published_at>0 THEN e.published_at ELSE e.added_at END {dir}, e.id {dir}"
            ),
            "duration" => format!("e.duration {dir}, e.title COLLATE NOCASE ASC, e.audio_url ASC"),
            _ => format!("e.title COLLATE NOCASE {dir}, e.audio_url {dir}"),
        })
    }

    pub fn partition_local_remote_ids(&self, ids: &[i64]) -> Result<(Vec<i64>, Vec<i64>)> {
        if ids.is_empty() {
            return Ok((Vec::new(), Vec::new()));
        }
        let mut local_set = HashSet::new();
        for chunk in ids.chunks(ID_CHUNK_SIZE) {
            let placeholders = std::iter::repeat_n("?", chunk.len())
                .collect::<Vec<_>>()
                .join(",");
            let sql = format!(
                "SELECT id FROM episodes WHERE id IN ({placeholders}) AND uuid LIKE 'local:%'"
            );
            let mut stmt = self.conn.prepare(&sql)?;
            for id in stmt.query_map(params_from_iter(chunk.iter()), |r| r.get::<_, i64>(0))? {
                local_set.insert(id?);
            }
        }
        let mut local_ids = Vec::new();
        let mut remote_ids = Vec::new();
        for &id in ids {
            if local_set.contains(&id) {
                local_ids.push(id);
            } else {
                remote_ids.push(id);
            }
        }
        Ok((local_ids, remote_ids))
    }

    fn build_standard_query(
        &self,
        filter: &str,
        query: &str,
        podcast: i64,
    ) -> Result<BuiltFilterQuery> {
        let mut conditions: Vec<&'static str> = Vec::new();
        let mut where_params: Vec<SqlValue> = Vec::new();

        let (from_sql, order_sql, is_cloud_only) = match filter {
            "Local files" => {
                conditions.push("lf.available = 1");
                (
                    "FROM episodes e JOIN podcasts p ON e.podcast_id = p.id JOIN local_files lf ON lf.episode_id = e.id",
                    self.local_order_sql()?,
                    false,
                )
            }
            "Local queue" => {
                conditions.push("lf.available = 1");
                (
                    "FROM local_queue lq JOIN episodes e ON e.id = lq.episode_id JOIN podcasts p ON e.podcast_id = p.id JOIN local_files lf ON lf.episode_id = e.id",
                    "lq.position ASC, lq.episode_id ASC".to_owned(),
                    false,
                )
            }
            "Up Next" => (
                "FROM queue q JOIN episodes e ON e.id = q.episode_id JOIN podcasts p ON e.podcast_id = p.id",
                "q.position ASC, q.id ASC".to_owned(),
                true,
            ),
            "Downloads" => {
                conditions.push("e.downloaded_path != ''");
                (
                    "FROM episodes e JOIN podcasts p ON e.podcast_id = p.id",
                    "e.published_at DESC, e.id DESC".to_owned(),
                    true,
                )
            }
            _ => {
                match filter {
                    "In progress" => conditions.push("e.playing_status = 1 AND e.archived = 0"),
                    "Starred" => conditions.push("e.starred = 1"),
                    "Archived" => conditions.push("e.archived = 1"),
                    "History" => conditions.push("e.playing_status = 2"),
                    _ => conditions.push("e.archived = 0"),
                }
                if podcast > 0 {
                    (
                        "FROM episodes e JOIN podcasts p ON e.podcast_id = p.id",
                        "e.published_at DESC, e.id DESC".to_owned(),
                        true,
                    )
                } else {
                    conditions.push("(e.uuid NOT LIKE 'local:%' OR lf.available = 1)");
                    (
                        "FROM episodes e JOIN podcasts p ON e.podcast_id = p.id LEFT JOIN local_files lf ON lf.episode_id = e.id",
                        "e.published_at DESC, e.id DESC".to_owned(),
                        false,
                    )
                }
            }
        };

        if podcast > 0 {
            conditions.push("p.id = ?");
            where_params.push(SqlValue::Integer(podcast));
        }

        if !query.is_empty() {
            if is_cloud_only {
                conditions.push(
                    "(instr(lower(e.title), lower(?)) > 0 OR instr(lower(p.title), lower(?)) > 0)",
                );
                where_params.push(SqlValue::Text(query.to_owned()));
                where_params.push(SqlValue::Text(query.to_owned()));
            } else {
                conditions.push(
                    "(instr(lower(e.title), lower(?)) > 0 OR instr(lower(p.title), lower(?)) > 0 OR (e.uuid LIKE 'local:%' AND instr(lower(e.description), lower(?)) > 0))",
                );
                where_params.push(SqlValue::Text(query.to_owned()));
                where_params.push(SqlValue::Text(query.to_owned()));
                where_params.push(SqlValue::Text(query.to_owned()));
            }
        }

        let where_sql = if conditions.is_empty() {
            "1=1".to_owned()
        } else {
            conditions.join(" AND ")
        };

        Ok(BuiltFilterQuery {
            from_sql,
            where_sql,
            where_params,
            order_sql,
        })
    }

    pub fn episodes(
        &self,
        filter: &str,
        query: &str,
        podcast: i64,
        limit: usize,
        offset: usize,
    ) -> Result<Vec<Episode>> {
        let built = self.build_standard_query(filter, query, podcast)?;
        let sql = format!(
            "SELECT e.id,e.uuid,p.uuid,p.title,e.title,CASE WHEN e.uuid LIKE 'local:%' THEN e.description ELSE '' END,e.audio_url,e.downloaded_path,e.duration,e.position,e.playing_status,e.starred,e.archived,e.published_at {} WHERE {} ORDER BY {} LIMIT ? OFFSET ?",
            built.from_sql, built.where_sql, built.order_sql
        );
        let mut args = built.where_params;
        args.push(SqlValue::Integer(limit as i64));
        args.push(SqlValue::Integer(offset as i64));
        self.query_episodes(&sql, params_from_iter(args.iter()))
    }

    pub fn episodes_by_ids(&self, ids: &[i64]) -> Result<Vec<Episode>> {
        if ids.is_empty() {
            return Ok(Vec::new());
        }
        let mut by_id: HashMap<i64, Episode> = HashMap::with_capacity(ids.len());
        for chunk in ids.chunks(ID_CHUNK_SIZE) {
            let placeholders = std::iter::repeat_n("?", chunk.len())
                .collect::<Vec<_>>()
                .join(",");
            let sql = format!(
                "SELECT e.id,e.uuid,p.uuid,p.title,e.title,CASE WHEN e.uuid LIKE 'local:%' THEN e.description ELSE '' END,e.audio_url,e.downloaded_path,e.duration,e.position,e.playing_status,e.starred,e.archived,e.published_at FROM episodes e JOIN podcasts p ON e.podcast_id = p.id WHERE e.id IN ({placeholders})"
            );
            let mut stmt = self.conn.prepare(&sql)?;
            for row in stmt.query_map(params_from_iter(chunk.iter()), Self::episode_row)? {
                let ep = row?;
                by_id.insert(ep.id, ep);
            }
        }
        Ok(ids.iter().filter_map(|id| by_id.get(id).cloned()).collect())
    }

    fn podcast_filter_parts(
        podcast_id: i64,
        filter: &str,
        query: &str,
        sort: i64,
    ) -> (String, Vec<SqlValue>, &'static str) {
        let mut conditions = vec!["e.podcast_id = ?"];
        let mut args = vec![SqlValue::Integer(podcast_id)];
        match filter {
            "All" => {}
            "Unplayed" => conditions.push("e.archived = 0 AND e.playing_status != 2"),
            "In Progress" => conditions.push("e.archived = 0 AND e.playing_status = 1"),
            "Downloaded" => conditions.push("e.downloaded_path != ''"),
            "Archived" => conditions.push("e.archived = 1"),
            _ => conditions.push("e.archived = 0"),
        }
        if !query.is_empty() {
            conditions.push("instr(lower(e.title), lower(?)) > 0");
            args.push(SqlValue::Text(query.to_owned()));
        }
        let order = match sort {
            1 => "e.published_at ASC, e.id ASC",
            2 => {
                "CASE WHEN e.duration > 0 THEN 0 ELSE 1 END ASC, e.duration ASC, e.published_at DESC, e.id DESC"
            }
            3 => {
                "CASE WHEN e.duration > 0 THEN 0 ELSE 1 END ASC, e.duration DESC, e.published_at DESC, e.id DESC"
            }
            _ => "e.published_at DESC, e.id DESC",
        };
        (conditions.join(" AND "), args, order)
    }

    pub fn podcast_episode_ids(
        &self,
        podcast_id: i64,
        filter: &str,
        query: &str,
        sort: i64,
    ) -> Result<Vec<i64>> {
        let (where_sql, args, order_sql) =
            Self::podcast_filter_parts(podcast_id, filter, query, sort);
        let sql = format!("SELECT e.id FROM episodes e WHERE {where_sql} ORDER BY {order_sql}");
        let mut stmt = self.conn.prepare(&sql)?;
        Ok(stmt
            .query_map(params_from_iter(args.iter()), |r| r.get(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?)
    }

    pub fn podcast_episodes(
        &self,
        podcast_id: i64,
        filter: &str,
        query: &str,
        sort: i64,
        limit: usize,
        offset: usize,
    ) -> Result<Vec<Episode>> {
        let (where_sql, mut args, order_sql) =
            Self::podcast_filter_parts(podcast_id, filter, query, sort);
        let sql = format!(
            "SELECT e.id,e.uuid,p.uuid,p.title,e.title,'',e.audio_url,e.downloaded_path,e.duration,e.position,e.playing_status,e.starred,e.archived,e.published_at FROM episodes e JOIN podcasts p ON e.podcast_id = p.id WHERE {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?"
        );
        args.push(SqlValue::Integer(limit as i64));
        args.push(SqlValue::Integer(offset as i64));
        self.query_episodes(&sql, params_from_iter(args.iter()))
    }

    pub fn podcast_view_stats(
        &self,
        podcast_id: i64,
        filter: &str,
        query: &str,
        active_id: Option<i64>,
    ) -> Result<(usize, f64, Option<f64>)> {
        let (where_sql, where_args, _) = Self::podcast_filter_parts(podcast_id, filter, query, 0);
        self.run_stats_aggregate("FROM episodes e", &where_sql, where_args, false, active_id)
    }

    pub fn local_all_ids(&self, query: &str) -> Result<Vec<i64>> {
        let order = self.local_order_sql()?;
        let (where_sql, args) = if query.is_empty() {
            ("lf.available = 1", Vec::new())
        } else {
            (
                "lf.available = 1 AND (instr(lower(e.title), lower(?)) > 0 OR instr(lower(e.description), lower(?)) > 0)",
                vec![
                    SqlValue::Text(query.to_owned()),
                    SqlValue::Text(query.to_owned()),
                ],
            )
        };
        let sql = format!(
            "SELECT e.id FROM episodes e JOIN local_files lf ON lf.episode_id = e.id WHERE {where_sql} ORDER BY {order}"
        );
        let mut stmt = self.conn.prepare(&sql)?;
        Ok(stmt
            .query_map(params_from_iter(args.iter()), |row| row.get(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?)
    }

    pub fn set_local_queue(&mut self, ids: &[i64]) -> Result<()> {
        let tx = self.conn.transaction()?;
        tx.execute("DELETE FROM local_queue", [])?;
        {
            let mut stmt =
                tx.prepare("INSERT OR IGNORE INTO local_queue(episode_id,position) VALUES (?,?)")?;
            for (position, id) in ids.iter().enumerate() {
                stmt.execute(params![id, position as i64])?;
            }
        }
        tx.commit()?;
        Ok(())
    }

    pub fn sort_local_queue(&mut self) -> Result<()> {
        let order = self.local_order_sql()?;
        let sql = format!(
            "SELECT q.episode_id FROM local_queue q JOIN episodes e ON e.id=q.episode_id JOIN local_files lf ON lf.episode_id=q.episode_id WHERE lf.available=1 ORDER BY {order}"
        );
        let ids = self
            .conn
            .prepare(&sql)?
            .query_map([], |row| row.get::<_, i64>(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        self.set_local_queue(&ids)
    }

    pub fn fetch_page(
        &self,
        nav: &str,
        query: &str,
        podcast: i64,
        limit: usize,
        offset: usize,
    ) -> Result<Vec<Episode>> {
        if let Some(rest) = nav.strip_prefix("playlist:") {
            let id = rest.parse::<i64>().unwrap_or(0);
            self.playlist_view(id, query, limit, offset)
        } else if let Some(uuid) = nav.strip_prefix("folder:") {
            self.folder_view(uuid, query, limit, offset)
        } else {
            self.episodes(nav, query, podcast, limit, offset)
        }
    }

    fn run_stats_aggregate(
        &self,
        from_sql: &str,
        where_sql: &str,
        where_params: Vec<SqlValue>,
        is_queue_view: bool,
        active_id: Option<i64>,
    ) -> Result<(usize, f64, Option<f64>)> {
        let active_val = match active_id {
            Some(id) => SqlValue::Integer(id),
            None => SqlValue::Null,
        };
        let (sql, mut args) = if is_queue_view {
            let rem = "CASE WHEN e.playing_status = 1 THEN max(max(CAST(coalesce(e.duration, 0) AS REAL), 0.0) - max(CAST(coalesce(e.position, 0) AS REAL) / 1000.0, 0.0), 0.0) ELSE max(CAST(coalesce(e.duration, 0) AS REAL), 0.0) END";
            (
                format!(
                    "SELECT COUNT(*), coalesce(SUM({rem}), 0.0), MAX(CASE WHEN e.id = ? THEN {rem} END) {from_sql} WHERE {where_sql}"
                ),
                vec![active_val],
            )
        } else {
            let rem = "CASE WHEN e.playing_status = 2 AND e.id IS NOT ? THEN 0.0 WHEN e.playing_status = 1 THEN max(max(CAST(coalesce(e.duration, 0) AS REAL), 0.0) - max(CAST(coalesce(e.position, 0) AS REAL) / 1000.0, 0.0), 0.0) ELSE max(CAST(coalesce(e.duration, 0) AS REAL), 0.0) END";
            (
                format!(
                    "SELECT COUNT(*), coalesce(SUM({rem}), 0.0), MAX(CASE WHEN e.id = ? THEN {rem} END) {from_sql} WHERE {where_sql}"
                ),
                vec![active_val.clone(), active_val.clone(), active_val],
            )
        };
        args.extend(where_params);
        let (count, total, active): (i64, f64, Option<f64>) =
            self.conn
                .query_row(&sql, params_from_iter(args.iter()), |r| {
                    Ok((r.get(0)?, r.get(1)?, r.get(2)?))
                })?;
        Ok((count.max(0) as usize, total.max(0.0), active))
    }

    pub fn view_stats(
        &self,
        nav: &str,
        query: &str,
        podcast: i64,
        active_id: Option<i64>,
    ) -> Result<(usize, f64, Option<f64>)> {
        let is_queue_view = matches!(nav, "Up Next" | "Local queue");
        if let Some(rest) = nav.strip_prefix("playlist:") {
            let id = rest.parse::<i64>().unwrap_or(0);
            let Some(parts) = self.playlist_sql_parts(id, query)? else {
                return Ok((0, 0.0, None));
            };
            return self.run_stats_aggregate(
                "FROM episodes e JOIN podcasts p ON p.id = e.podcast_id",
                &parts.where_sql,
                parts.where_params,
                false,
                active_id,
            );
        }
        if let Some(uuid) = nav.strip_prefix("folder:") {
            let (where_sql, where_params) = if query.is_empty() {
                (
                    "p.folder_uuid = ? AND e.archived = 0",
                    vec![SqlValue::Text(uuid.to_owned())],
                )
            } else {
                (
                    "p.folder_uuid = ? AND e.archived = 0 AND instr(lower(e.title), lower(?)) > 0",
                    vec![
                        SqlValue::Text(uuid.to_owned()),
                        SqlValue::Text(query.to_owned()),
                    ],
                )
            };
            return self.run_stats_aggregate(
                "FROM episodes e JOIN podcasts p ON p.id = e.podcast_id",
                where_sql,
                where_params,
                false,
                active_id,
            );
        }
        let built = self.build_standard_query(nav, query, podcast)?;
        self.run_stats_aggregate(
            built.from_sql,
            &built.where_sql,
            built.where_params,
            is_queue_view,
            active_id,
        )
    }

    pub fn view_ids(&self, nav: &str, query: &str, podcast: i64) -> Result<Vec<i64>> {
        if let Some(rest) = nav.strip_prefix("playlist:") {
            let id = rest.parse::<i64>().unwrap_or(0);
            let Some(parts) = self.playlist_sql_parts(id, query)? else {
                return Ok(Vec::new());
            };
            let sql = format!(
                "SELECT e.id FROM episodes e JOIN podcasts p ON p.id = e.podcast_id WHERE {} ORDER BY {}, e.id",
                parts.where_sql, parts.order_sql
            );
            let mut args = parts.where_params;
            if parts.order_uses_playlist_id {
                args.push(SqlValue::Integer(id));
            }
            let mut stmt = self.conn.prepare(&sql)?;
            return Ok(stmt
                .query_map(params_from_iter(args.iter()), |r| r.get(0))?
                .collect::<rusqlite::Result<Vec<_>>>()?);
        }
        if let Some(uuid) = nav.strip_prefix("folder:") {
            let (where_sql, args) = if query.is_empty() {
                (
                    "p.folder_uuid = ? AND e.archived = 0",
                    vec![SqlValue::Text(uuid.to_owned())],
                )
            } else {
                (
                    "p.folder_uuid = ? AND e.archived = 0 AND instr(lower(e.title), lower(?)) > 0",
                    vec![
                        SqlValue::Text(uuid.to_owned()),
                        SqlValue::Text(query.to_owned()),
                    ],
                )
            };
            let sql = format!(
                "SELECT e.id FROM episodes e JOIN podcasts p ON p.id = e.podcast_id WHERE {where_sql} ORDER BY e.published_at DESC, e.id DESC"
            );
            let mut stmt = self.conn.prepare(&sql)?;
            return Ok(stmt
                .query_map(params_from_iter(args.iter()), |r| r.get(0))?
                .collect::<rusqlite::Result<Vec<_>>>()?);
        }
        let built = self.build_standard_query(nav, query, podcast)?;
        let sql = format!(
            "SELECT e.id {} WHERE {} ORDER BY {}",
            built.from_sql, built.where_sql, built.order_sql
        );
        let mut stmt = self.conn.prepare(&sql)?;
        Ok(stmt
            .query_map(params_from_iter(built.where_params.iter()), |r| r.get(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?)
    }

    pub fn view_all_ids(&self, nav: &str, query: &str, podcast: i64) -> Result<Vec<i64>> {
        self.view_ids(nav, query, podcast)
    }

    pub fn queue_summary(
        &self,
        local: bool,
        active_id: Option<i64>,
    ) -> Result<(usize, f64, Option<f64>, Option<Episode>)> {
        let sql = if local {
            "SELECT e.id,e.uuid,p.uuid,p.title,e.title,e.description,e.audio_url,e.downloaded_path,e.duration,e.position,e.playing_status,e.starred,e.archived,e.published_at FROM local_queue q JOIN episodes e ON e.id=q.episode_id JOIN podcasts p ON p.id=e.podcast_id JOIN local_files lf ON lf.episode_id=q.episode_id WHERE lf.available=1 ORDER BY q.position,q.episode_id"
        } else {
            "SELECT e.id,e.uuid,p.uuid,p.title,e.title,CASE WHEN e.uuid LIKE 'local:%' THEN e.description ELSE '' END,e.audio_url,e.downloaded_path,e.duration,e.position,e.playing_status,e.starred,e.archived,e.published_at FROM queue q JOIN episodes e ON e.id=q.episode_id JOIN podcasts p ON p.id=e.podcast_id ORDER BY q.position,q.id"
        };
        let rows = self
            .conn
            .prepare(sql)?
            .query_map([], Self::episode_row)?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        let count = rows.len();
        let mut total = 0.0f64;
        let mut active = None;
        let mut seen_active = false;
        let mut next_ep = None;
        for ep in &rows {
            let duration = (ep.duration as f64).max(0.0);
            let remaining = if ep.playing_status == 1 {
                (duration - (ep.position as f64 / 1000.0).max(0.0)).max(0.0)
            } else {
                duration
            };
            total += remaining;
            if Some(ep.id) == active_id {
                active = Some(remaining);
                seen_active = true;
            } else if seen_active && next_ep.is_none() {
                next_ep = Some(ep.clone());
            }
        }
        if next_ep.is_none() {
            next_ep = rows.into_iter().find(|ep| Some(ep.id) != active_id);
        }
        Ok((count, total, active, next_ep))
    }

    fn episode_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<Episode> {
        Ok(Episode {
            id: r.get(0)?,
            uuid: r.get(1)?,
            podcast_uuid: r.get(2)?,
            podcast: r.get(3)?,
            title: r.get(4)?,
            description: r.get(5)?,
            audio_url: r.get(6)?,
            downloaded_path: r.get(7)?,
            duration: r.get(8)?,
            position: r.get(9)?,
            playing_status: r.get(10)?,
            starred: r.get(11)?,
            archived: r.get(12)?,
            published_at: r.get(13)?,
        })
    }

    pub fn episode(&self, id: i64) -> Result<Episode> {
        self.conn
            .query_row(
                "SELECT e.id,e.uuid,p.uuid,p.title,e.title,e.description,e.audio_url,e.downloaded_path,e.duration,e.position,e.playing_status,e.starred,e.archived,e.published_at FROM episodes e JOIN podcasts p ON e.podcast_id=p.id WHERE e.id=?",
                [id],
                Self::episode_row,
            )
            .context("Episode unavailable")
    }

    fn modified_column(field: &str) -> Result<&'static str> {
        match field {
            "position" => Ok("played_up_to_modified"),
            "playing_status" => Ok("playing_status_modified"),
            "starred" => Ok("starred_modified"),
            "archived" => Ok("archived_modified"),
            "duration" => Ok("duration_modified"),
            _ => bail!("Invalid episode field"),
        }
    }

    pub fn change(&mut self, id: i64, field: &str, value: i64) -> Result<()> {
        let modified = Self::modified_column(field)?;
        self.conn.execute(
            &format!(
                "UPDATE episodes SET {field}=?,{modified}=max(?,{modified}+1),needs_sync=1 WHERE id=?"
            ),
            params![value, now(), id],
        )?;
        Ok(())
    }

    pub fn change_batch(&mut self, ids: &[i64], field: &str, value: i64) -> Result<()> {
        let modified = Self::modified_column(field)?;
        if ids.is_empty() {
            return Ok(());
        }
        let stamp = now();
        let tx = self.conn.transaction()?;
        {
            let mut stmt = tx.prepare(&format!(
                "UPDATE episodes SET {field}=?1,{modified}=max(?2,{modified}+1),needs_sync=1 WHERE id=?3"
            ))?;
            for &id in ids {
                stmt.execute(params![value, stamp, id])?;
            }
        }
        tx.commit()?;
        Ok(())
    }

    pub fn progress(&mut self, id: i64, position: f64, duration: f64) -> Result<()> {
        let tx = self.conn.transaction()?;
        let stamp = now();
        let position = (position.max(0.0) * 1000.0) as i64;
        tx.execute(
            "UPDATE episodes SET position=?,played_up_to_modified=max(?,played_up_to_modified+1),needs_sync=1 WHERE id=? AND position!=?",
            params![position, stamp, id, position],
        )?;
        if duration > 0.0 {
            tx.execute(
                "UPDATE episodes SET duration=?,duration_modified=max(?,duration_modified+1),needs_sync=1 WHERE id=? AND duration!=?",
                params![duration as i64, stamp, id, duration as i64],
            )?;
        }
        tx.commit()?;
        Ok(())
    }

    pub fn active(&self, id: Option<i64>) -> Result<()> {
        self.conn.execute(
            "INSERT INTO rust_runtime VALUES ('active',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE value!=excluded.value",
            [id.unwrap_or(0).to_string()],
        )?;
        Ok(())
    }

    pub fn queue(&self) -> Result<Vec<i64>> {
        Ok(self
            .conn
            .prepare("SELECT episode_id FROM queue ORDER BY position,id")?
            .query_map([], |r| r.get(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?)
    }

    pub fn queue_for(&self, local: bool) -> Result<Vec<i64>> {
        if !local {
            return self.queue();
        }
        Ok(self
            .conn
            .prepare("SELECT q.episode_id FROM local_queue q JOIN local_files f ON f.episode_id=q.episode_id WHERE f.available=1 ORDER BY q.position,q.episode_id")?
            .query_map([], |r| r.get(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?)
    }

    pub fn edit_queue(&mut self, id: i64, action: i32) -> Result<()> {
        self.edit_queue_batch(&[id], action)
    }

    pub fn edit_queue_batch(&mut self, ids: &[i64], action: i32) -> Result<()> {
        anyhow::ensure!((1..=4).contains(&action), "Invalid queue action");
        if ids.is_empty() {
            return Ok(());
        }
        let mut seen = HashSet::new();
        let deduped: Vec<i64> = ids.iter().copied().filter(|id| seen.insert(*id)).collect();
        let tx = self.conn.transaction()?;
        let mut local_batch = Vec::new();
        let mut remote_batch = Vec::new();
        {
            let mut stmt =
                tx.prepare("SELECT uuid, uuid LIKE 'local:%' FROM episodes WHERE id=?")?;
            for id in deduped {
                if let Some((uuid, is_local)) = stmt
                    .query_row([id], |r| Ok((r.get::<_, String>(0)?, r.get::<_, bool>(1)?)))
                    .optional()?
                {
                    if is_local {
                        local_batch.push(id);
                    } else {
                        remote_batch.push((id, uuid));
                    }
                }
            }
        }

        if !local_batch.is_empty() {
            let mut del_stmt = tx.prepare("DELETE FROM local_queue WHERE episode_id=?")?;
            for &id in &local_batch {
                del_stmt.execute([id])?;
            }
            drop(del_stmt);
            if action != 4 {
                if matches!(action, 1 | 2) {
                    tx.execute(
                        "UPDATE local_queue SET position = position + ?1",
                        [local_batch.len() as i64],
                    )?;
                    let mut ins_stmt =
                        tx.prepare("INSERT INTO local_queue(episode_id,position) VALUES (?1,?2)")?;
                    for (idx, &id) in local_batch.iter().enumerate() {
                        ins_stmt.execute(params![id, idx as i64])?;
                    }
                } else {
                    let base: i64 = tx.query_row(
                        "SELECT coalesce(max(position),-1)+1 FROM local_queue",
                        [],
                        |r| r.get(0),
                    )?;
                    let mut ins_stmt =
                        tx.prepare("INSERT INTO local_queue(episode_id,position) VALUES (?1,?2)")?;
                    for (idx, &id) in local_batch.iter().enumerate() {
                        ins_stmt.execute(params![id, base + idx as i64])?;
                    }
                }
            }
        }

        if !remote_batch.is_empty() {
            let mut del_stmt = tx.prepare("DELETE FROM queue WHERE episode_id=?")?;
            for (id, _) in &remote_batch {
                del_stmt.execute([id])?;
            }
            drop(del_stmt);
            if action != 4 {
                if matches!(action, 1 | 2) {
                    tx.execute(
                        "UPDATE queue SET position = position + ?1",
                        [remote_batch.len() as i64],
                    )?;
                    let mut ins_stmt =
                        tx.prepare("INSERT INTO queue(episode_id,position) VALUES (?1,?2)")?;
                    for (idx, (id, _)) in remote_batch.iter().enumerate() {
                        ins_stmt.execute(params![id, idx as i64])?;
                    }
                } else {
                    let base: i64 =
                        tx.query_row("SELECT coalesce(max(position),-1)+1 FROM queue", [], |r| {
                            r.get(0)
                        })?;
                    let mut ins_stmt =
                        tx.prepare("INSERT INTO queue(episode_id,position) VALUES (?1,?2)")?;
                    for (idx, (id, _)) in remote_batch.iter().enumerate() {
                        ins_stmt.execute(params![id, base + idx as i64])?;
                    }
                }
            }
            let ordered_remote: Vec<&(i64, String)> = if matches!(action, 1 | 2) {
                remote_batch.iter().rev().collect()
            } else {
                remote_batch.iter().collect()
            };
            let mut act_stmt = tx.prepare(
                "INSERT INTO queue_actions(action,episode_uuid,modified) VALUES (?1,?2,?3)",
            )?;
            for (stamp, (_, uuid)) in (now()..).zip(ordered_remote) {
                act_stmt.execute(params![action, uuid, stamp])?;
            }
        }
        tx.commit()?;
        Ok(())
    }

    pub fn clear_up_next(&mut self) -> Result<()> {
        let tx = self.conn.transaction()?;
        let uuids: Vec<String> = tx
            .prepare("SELECT e.uuid FROM queue q JOIN episodes e ON e.id=q.episode_id ORDER BY q.position,q.id")?
            .query_map([], |r| r.get(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        tx.execute("DELETE FROM queue", [])?;
        for (stamp, uuid) in (now()..).zip(uuids) {
            tx.execute(
                "INSERT INTO queue_actions(action,episode_uuid,modified) VALUES (4,?,?)",
                params![uuid, stamp],
            )?;
        }
        tx.commit()?;
        Ok(())
    }

    pub fn reorder_queue(&mut self, ids: &[i64]) -> Result<()> {
        if ids
            .first()
            .map(|id| self.episode(*id))
            .transpose()?
            .is_some_and(|e| e.is_local())
        {
            for id in ids {
                anyhow::ensure!(
                    self.episode(*id)?.is_local(),
                    "Cannot mix local and synced queues"
                );
            }
            let tx = self.conn.transaction()?;
            tx.execute("DELETE FROM local_queue", [])?;
            {
                let mut stmt =
                    tx.prepare("INSERT INTO local_queue(episode_id,position) VALUES (?,?)")?;
                for (position, id) in ids.iter().enumerate() {
                    stmt.execute(params![id, position as i64])?;
                }
            }
            tx.commit()?;
            return Ok(());
        }
        for id in ids {
            anyhow::ensure!(
                !self.episode(*id)?.is_local(),
                "Cannot mix local and synced queues"
            );
        }
        let tx = self.conn.transaction()?;
        tx.execute("DELETE FROM queue", [])?;
        let mut uuids = Vec::with_capacity(ids.len());
        {
            let mut ins =
                tx.prepare("INSERT OR IGNORE INTO queue(episode_id,position) VALUES (?,?)")?;
            let mut sel = tx.prepare("SELECT uuid FROM episodes WHERE id=?")?;
            for (pos, id) in ids.iter().enumerate() {
                ins.execute(params![id, pos as i64])?;
                uuids.push(sel.query_row([id], |r| r.get::<_, String>(0))?);
            }
        }
        tx.execute(
            "INSERT INTO queue_actions(action,episode_uuids,modified) VALUES (5,?,?)",
            params![uuids.join(","), now()],
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn sort_up_next(&mut self, sort: UpNextSort) -> Result<()> {
        let current_queue = self.queue()?;
        if current_queue.len() <= 1 {
            return Ok(());
        }
        let active_id: i64 = self
            .conn
            .query_row(
                "SELECT coalesce((SELECT value FROM rust_runtime WHERE key='active'),'0')",
                [],
                |r| r.get::<_, String>(0),
            )?
            .parse()
            .unwrap_or(0);
        let pinned_head = (active_id > 0 && current_queue.first().copied() == Some(active_id))
            .then_some(active_id);
        let order = match sort {
            UpNextSort::Newest => "e.published_at DESC, q.position ASC, q.id ASC",
            UpNextSort::Oldest => {
                "CASE WHEN e.published_at > 0 THEN 0 ELSE 1 END ASC, e.published_at ASC, q.position ASC, q.id ASC"
            }
            UpNextSort::Shortest => {
                "CASE WHEN e.duration > 0 THEN 0 ELSE 1 END ASC, max(CASE WHEN e.playing_status = 1 THEN max(CAST(e.duration AS REAL) - max(CAST(e.position AS REAL) / 1000.0, 0.0), 0.0) ELSE CAST(e.duration AS REAL) END, 0.0) ASC, q.position ASC, q.id ASC"
            }
            UpNextSort::Longest => {
                "CASE WHEN e.duration > 0 THEN 0 ELSE 1 END ASC, max(CASE WHEN e.playing_status = 1 THEN max(CAST(e.duration AS REAL) - max(CAST(e.position AS REAL) / 1000.0, 0.0), 0.0) ELSE CAST(e.duration AS REAL) END, 0.0) DESC, q.position ASC, q.id ASC"
            }
        };
        let mut sorted: Vec<i64> = self
            .conn
            .prepare(&format!(
                "SELECT q.episode_id FROM queue q JOIN episodes e ON e.id = q.episode_id ORDER BY {order}"
            ))?
            .query_map([], |r| r.get(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        if let Some(head) = pinned_head {
            sorted.retain(|&id| id != head);
            sorted.insert(0, head);
        }
        if sorted != current_queue {
            self.reorder_queue(&sorted)?;
        }
        Ok(())
    }

    pub fn reorder_playlist(&mut self, playlist_id: i64, ids: &[i64]) -> Result<()> {
        let tx = self.conn.transaction()?;
        {
            let mut stmt = tx.prepare(
                "UPDATE playlist_episodes SET position=? WHERE playlist_id=? AND episode_uuid=(SELECT uuid FROM episodes WHERE id=?)",
            )?;
            for (pos, id) in ids.iter().enumerate() {
                stmt.execute(params![pos as i64, playlist_id, id])?;
            }
        }
        tx.execute(
            "UPDATE playlists SET needs_sync=1,sort_type=4,local_modified=max(?,local_modified+1) WHERE id=?",
            params![now(), playlist_id],
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn remove_playlist_episodes(&mut self, playlist_id: i64, ids: &[i64]) -> Result<()> {
        if ids.is_empty() {
            return Ok(());
        }
        let tx = self.conn.transaction()?;
        {
            let mut stmt = tx.prepare(
                "DELETE FROM playlist_episodes WHERE playlist_id=? AND episode_uuid=(SELECT uuid FROM episodes WHERE id=?)",
            )?;
            for id in ids {
                stmt.execute(params![playlist_id, id])?;
            }
        }
        tx.execute(
            "UPDATE playlists SET needs_sync=1,sort_type=4,local_modified=max(?,local_modified+1) WHERE id=?",
            params![now(), playlist_id],
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn clear_playlist_episodes(&mut self, playlist_id: i64) -> Result<()> {
        let tx = self.conn.transaction()?;
        tx.execute(
            "DELETE FROM playlist_episodes WHERE playlist_id=?",
            [playlist_id],
        )?;
        tx.execute(
            "UPDATE playlists SET needs_sync=1,sort_type=4,local_modified=max(?,local_modified+1) WHERE id=?",
            params![now(), playlist_id],
        )?;
        tx.commit()?;
        Ok(())
    }

    fn query_episodes(&self, sql: &str, args: impl rusqlite::Params) -> Result<Vec<Episode>> {
        Ok(self
            .conn
            .prepare(sql)?
            .query_map(args, Self::episode_row)?
            .collect::<rusqlite::Result<Vec<_>>>()?)
    }

    pub fn folder_view(
        &self,
        uuid: &str,
        query: &str,
        limit: usize,
        offset: usize,
    ) -> Result<Vec<Episode>> {
        let (where_sql, mut args) = if query.is_empty() {
            (
                "p.folder_uuid = ? AND e.archived = 0",
                vec![SqlValue::Text(uuid.to_owned())],
            )
        } else {
            (
                "p.folder_uuid = ? AND e.archived = 0 AND instr(lower(e.title), lower(?)) > 0",
                vec![
                    SqlValue::Text(uuid.to_owned()),
                    SqlValue::Text(query.to_owned()),
                ],
            )
        };
        args.push(SqlValue::Integer(limit as i64));
        args.push(SqlValue::Integer(offset as i64));
        let sql = format!(
            "SELECT e.id,e.uuid,p.uuid,p.title,e.title,'',e.audio_url,e.downloaded_path,e.duration,e.position,e.playing_status,e.starred,e.archived,e.published_at FROM episodes e JOIN podcasts p ON p.id=e.podcast_id WHERE {where_sql} ORDER BY e.published_at DESC,e.id DESC LIMIT ? OFFSET ?"
        );
        self.query_episodes(&sql, params_from_iter(args.iter()))
    }

    fn load_playlist_row(&self, id: i64) -> Result<Option<PlaylistRow>> {
        Ok(self
            .conn
            .query_row(
                "SELECT manual,show_archived,all_podcasts,podcast_uuids,audio_video,finished,partially_played,unplayed,downloaded,not_downloaded,downloading,starred,filter_hours,filter_duration,longer_than,shorter_than,sort_type FROM playlists WHERE id=?",
                [id],
                |r| {
                    Ok(PlaylistRow {
                        manual: r.get::<_, i64>(0)? != 0,
                        show_archived: r.get::<_, i64>(1)? != 0,
                        all_podcasts: r.get::<_, i64>(2)? != 0,
                        podcast_uuids: r.get(3)?,
                        audio_video: r.get(4)?,
                        finished: r.get::<_, i64>(5)? != 0,
                        partially_played: r.get::<_, i64>(6)? != 0,
                        unplayed: r.get::<_, i64>(7)? != 0,
                        downloaded: r.get::<_, i64>(8)? != 0,
                        not_downloaded: r.get::<_, i64>(9)? != 0,
                        downloading: r.get::<_, i64>(10)? != 0,
                        starred: r.get::<_, i64>(11)? != 0,
                        filter_hours: r.get(12)?,
                        filter_duration: r.get::<_, i64>(13)? != 0,
                        longer_than: r.get(14)?,
                        shorter_than: r.get(15)?,
                        sort_type: r.get(16)?,
                    })
                },
            )
            .optional()?)
    }

    fn playlist_sql_parts(&self, id: i64, query: &str) -> Result<Option<PlaylistQueryParts>> {
        let Some(p) = self.load_playlist_row(id)? else {
            return Ok(None);
        };
        let mut conditions: Vec<String> = Vec::new();
        let mut where_params: Vec<SqlValue> = Vec::new();

        if !query.is_empty() {
            conditions.push("instr(lower(e.title), lower(?)) > 0".into());
            where_params.push(SqlValue::Text(query.to_owned()));
        }
        if !p.manual {
            conditions.push("p.feed_url NOT LIKE 'local://%'".into());
        }
        if !p.show_archived {
            conditions.push("e.archived = 0".into());
        }
        if p.manual {
            conditions.push(
                "e.uuid IN (SELECT episode_uuid FROM playlist_episodes WHERE playlist_id = ?)"
                    .into(),
            );
            where_params.push(SqlValue::Integer(id));
            conditions.push(
                "(e.uuid NOT LIKE 'local:%' OR EXISTS (SELECT 1 FROM local_files lf WHERE lf.episode_id = e.id AND lf.available = 1))"
                    .into(),
            );
        } else {
            if !p.all_podcasts && !p.podcast_uuids.is_empty() {
                conditions.push("instr(',' || ? || ',', ',' || p.uuid || ',') > 0".into());
                where_params.push(SqlValue::Text(p.podcast_uuids));
            }
            match p.audio_video {
                1 => conditions.push("e.file_type LIKE 'audio%'".into()),
                2 => conditions.push("e.file_type LIKE 'video%'".into()),
                _ => {}
            }
            let statuses = [
                (p.finished, "2"),
                (p.partially_played, "1"),
                (p.unplayed, "0"),
            ]
            .into_iter()
            .filter(|(enabled, _)| *enabled)
            .map(|(_, v)| v)
            .collect::<Vec<_>>();
            if !statuses.is_empty() {
                conditions.push(format!("e.playing_status IN ({})", statuses.join(",")));
            }
            let mut downloads = Vec::new();
            if p.downloaded {
                downloads.push("e.download_state = 3");
            }
            if p.not_downloaded {
                downloads.push("e.download_state NOT IN (1,2,3)");
            }
            if p.downloading {
                downloads.push("e.download_state IN (1,2)");
            }
            if !downloads.is_empty() {
                conditions.push(format!("({})", downloads.join(" OR ")));
            }
            if p.starred {
                conditions.push("e.starred = 1".into());
            }
            if p.filter_hours > 0 {
                conditions.push("e.published_at >= ?".into());
                where_params.push(SqlValue::Integer(
                    now() / 1000 - p.filter_hours.saturating_mul(3600),
                ));
            }
            if p.filter_duration {
                if p.longer_than > 0 {
                    conditions.push("e.duration >= ?".into());
                    where_params.push(SqlValue::Integer(p.longer_than));
                }
                if p.shorter_than > 0 {
                    conditions.push("e.duration <= ?".into());
                    where_params.push(SqlValue::Integer(p.shorter_than));
                }
            }
        }

        let where_sql = if conditions.is_empty() {
            "1=1".into()
        } else {
            conditions.join(" AND ")
        };
        let (order_sql, order_uses_playlist_id) = match p.sort_type {
            1 => ("e.published_at ASC", false),
            2 => ("e.duration ASC", false),
            3 => ("e.duration DESC", false),
            4 if p.manual => (
                "(SELECT position FROM playlist_episodes WHERE playlist_id = ? AND episode_uuid = e.uuid)",
                true,
            ),
            _ => ("e.published_at DESC", false),
        };
        Ok(Some(PlaylistQueryParts {
            where_sql,
            where_params,
            order_sql,
            order_uses_playlist_id,
        }))
    }

    pub fn playlist_view(
        &self,
        id: i64,
        query: &str,
        limit: usize,
        offset: usize,
    ) -> Result<Vec<Episode>> {
        let Some(parts) = self.playlist_sql_parts(id, query)? else {
            return Ok(Vec::new());
        };
        let sql = format!(
            "SELECT e.id,e.uuid,p.uuid,p.title,e.title,CASE WHEN e.uuid LIKE 'local:%' THEN e.description ELSE '' END,e.audio_url,e.downloaded_path,e.duration,e.position,e.playing_status,e.starred,e.archived,e.published_at FROM episodes e JOIN podcasts p ON p.id=e.podcast_id WHERE {} ORDER BY {},e.id LIMIT ? OFFSET ?",
            parts.where_sql, parts.order_sql
        );
        let mut args = parts.where_params;
        if parts.order_uses_playlist_id {
            args.push(SqlValue::Integer(id));
        }
        args.push(SqlValue::Integer(limit as i64));
        args.push(SqlValue::Integer(offset as i64));
        self.query_episodes(&sql, params_from_iter(args.iter()))
    }
}

pub fn s<'a>(row: &'a Value, key: &str) -> &'a str {
    row[key].as_str().unwrap_or("")
}

pub fn n(row: &Value, key: &str) -> i64 {
    row[key].as_i64().unwrap_or(0)
}

#[cfg(test)]
mod migration_tests {
    use super::*;

    #[test]
    fn migrate_fresh_without_legacy_db_returns_false() -> Result<()> {
        let tmp = tempfile::tempdir()?;
        let legacy_db = tmp.path().join("duskcasts/library.db");
        let target_db = tmp.path().join("dusky_casts/library.db");

        let migrated = migrate_legacy_library_from(&legacy_db, &target_db)?;
        assert!(!migrated);
        assert!(!target_db.exists());
        Ok(())
    }

    #[test]
    fn migrate_wal_db_preserves_uncheckpointed_rows_permissions_and_ios_stats() -> Result<()> {
        let tmp = tempfile::tempdir()?;
        let legacy_dir = tmp.path().join("duskcasts");
        let target_dir = tmp.path().join("dusky_casts");
        let legacy_db = legacy_dir.join("library.db");
        let target_db = target_dir.join("library.db");

        // Hold an active WAL read snapshot while dropping the writer so `-wal` frames stay uncheckpointed on disk.
        let _wal_holder = {
            let db = Db::open(&legacy_db)?;
            db.conn.execute_batch("PRAGMA wal_autocheckpoint = 0;")?;
            let holder = Connection::open(&legacy_db)?;
            holder.execute_batch("BEGIN DEFERRED; SELECT COUNT(*) FROM sqlite_master;")?;
            db.conn.execute(
                "INSERT INTO podcasts (uuid, title, author, description, artwork_url, feed_url, added_at)
                 VALUES ('pod-1', 'Systems Show', 'Host', '', '', 'https://example.invalid/feed', 1000)",
                [],
            )?;
            db.conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('discovered_ftp_stats_dir', ?1)",
                [legacy_dir.to_string_lossy().as_ref()],
            )?;
            holder
        };

        let wal_path = sqlite_sidecar(&legacy_db, "-wal");
        assert!(
            wal_path.is_file() && std::fs::metadata(&wal_path)?.len() > 0,
            "expected active uncheckpointed WAL file"
        );

        let legacy_ios = legacy_dir.join(crate::listening::LEGACY_IOS_STATS_FILENAME);
        std::fs::write(&legacy_ios, r#"{"updated_at":12345,"days_seconds":{}}"#)?;

        let migrated = migrate_legacy_library_from(&legacy_db, &target_db)?;
        drop(_wal_holder);
        assert!(migrated);
        assert!(target_db.is_file());
        assert!(legacy_db.is_file());

        let mode = std::fs::metadata(&target_db)?.permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);

        let opened = Db::open(&target_db)?;
        let title: String = opened.conn.query_row(
            "SELECT title FROM podcasts WHERE uuid = 'pod-1'",
            [],
            |row| row.get(0),
        )?;
        assert_eq!(title, "Systems Show");

        let user_version: i32 = opened
            .conn
            .query_row("PRAGMA user_version", [], |row| row.get(0))?;
        assert!(user_version >= 1);

        let rewritten_dir: String = opened.conn.query_row(
            "SELECT value FROM settings WHERE key = 'discovered_ftp_stats_dir'",
            [],
            |row| row.get(0),
        )?;
        assert_eq!(
            PathBuf::from(rewritten_dir).canonicalize()?,
            target_dir.canonicalize()?
        );

        let copied_ios = target_dir.join(crate::listening::IOS_STATS_FILENAME);
        assert!(copied_ios.is_file());
        assert_eq!(
            std::fs::read_to_string(&copied_ios)?,
            r#"{"updated_at":12345,"days_seconds":{}}"#
        );
        Ok(())
    }

    #[test]
    fn migrate_refuses_when_legacy_lock_is_held() -> Result<()> {
        let tmp = tempfile::tempdir()?;
        let legacy_dir = tmp.path().join("duskcasts");
        let target_dir = tmp.path().join("dusky_casts");
        let legacy_db = legacy_dir.join("library.db");
        let target_db = target_dir.join("library.db");

        {
            let _db = Db::open(&legacy_db)?;
        }

        let lock_file = std::fs::OpenOptions::new()
            .create(true)
            .truncate(false)
            .write(true)
            .open(legacy_db.with_extension("lock"))?;
        lock_file.try_lock()?;

        let err = migrate_legacy_library_from(&legacy_db, &target_db)
            .expect_err("expected migration to fail while legacy lock is held");
        assert!(
            err.to_string()
                .contains("Legacy Dusk Casts instance is still running"),
            "unexpected error: {err:#}"
        );
        assert!(!target_db.exists());
        drop(lock_file);
        Ok(())
    }

    #[test]
    fn migrate_is_noop_when_target_already_exists() -> Result<()> {
        let tmp = tempfile::tempdir()?;
        let legacy_db = tmp.path().join("duskcasts/library.db");
        let target_db = tmp.path().join("dusky_casts/library.db");

        {
            let legacy = Db::open(&legacy_db)?;
            legacy.conn.execute(
                "INSERT INTO podcasts (uuid, title, author, description, artwork_url, feed_url, added_at)
                 VALUES ('legacy-only', 'Old Show', 'Host', '', '', 'https://example.invalid/old', 1)",
                [],
            )?;
            let target = Db::open(&target_db)?;
            target.conn.execute(
                "INSERT INTO podcasts (uuid, title, author, description, artwork_url, feed_url, added_at)
                 VALUES ('new-only', 'New Show', 'Host', '', '', 'https://example.invalid/new', 2)",
                [],
            )?;
        }

        let migrated = migrate_legacy_library_from(&legacy_db, &target_db)?;
        assert!(!migrated);

        let opened = Db::open(&target_db)?;
        let count_new: i64 = opened.conn.query_row(
            "SELECT COUNT(*) FROM podcasts WHERE uuid = 'new-only'",
            [],
            |r| r.get(0),
        )?;
        let count_old: i64 = opened.conn.query_row(
            "SELECT COUNT(*) FROM podcasts WHERE uuid = 'legacy-only'",
            [],
            |r| r.get(0),
        )?;
        assert_eq!(count_new, 1);
        assert_eq!(count_old, 0);
        Ok(())
    }
}
