//! Desktop elapsed listening and two-way FTP/local stats synchronization with iOS.
use crate::db::{Db, now};
use anyhow::Result;
use chrono::{DateTime, Local};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, HashSet},
    io::Write,
    os::unix::fs::MetadataExt,
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
    time::{Duration, Instant},
};

static TMP_COUNTER: AtomicU64 = AtomicU64::new(0);
pub const IOS_STATS_FILENAME: &str = ".dusky_casts-stats-ios.json";
pub const LEGACY_IOS_STATS_FILENAME: &str = ".duskcasts-stats-ios.json";
pub const PC_STATS_FILENAME: &str = ".dusky_casts-stats-pc.json";
const IOS_STATS_CANDIDATES: [&str; 2] = [IOS_STATS_FILENAME, LEGACY_IOS_STATS_FILENAME];

#[derive(Serialize, Deserialize, Default)]
pub struct DeviceStatsManifest {
    #[serde(default)]
    pub device_id: String,
    #[serde(default)]
    pub device_name: String,
    #[serde(default)]
    pub updated_at: i64,
    #[serde(default)]
    pub days_ms: BTreeMap<String, i64>,
    #[serde(default)]
    pub days_seconds: BTreeMap<String, f64>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct StatsSyncReport {
    pub export_dirs: Vec<PathBuf>,
    pub ios_sources: Vec<PathBuf>,
    pub imported_days: usize,
    pub updated_days: usize,
    pub pc_exported: bool,
}

#[derive(Default)]
pub struct Listening {
    previous: Option<(Instant, i64, u64, f64)>,
    rem_ns: u64,
    pending: BTreeMap<String, i64>,
    last_file_sync: Option<Instant>,
}

impl Listening {
    pub fn pending(&self) -> &BTreeMap<String, i64> {
        &self.pending
    }

    pub fn sample(
        &mut self,
        instant: Instant,
        wall: DateTime<Local>,
        active: Option<(i64, u64, f64)>,
    ) {
        let previous = self.previous.take();
        if let Some((id, generation, position)) = active {
            self.previous = Some((instant, id, generation, position));
            if let Some((before, old_id, old_generation, old_position)) = previous {
                let elapsed = instant.saturating_duration_since(before);
                if id == old_id
                    && generation == old_generation
                    && position > old_position
                    && elapsed.as_millis() <= 2000
                {
                    let total_ns = elapsed.as_nanos() as u64 + self.rem_ns;
                    let delta_ms = (total_ns / 1_000_000) as i64;
                    self.rem_ns = total_ns % 1_000_000;
                    if delta_ms > 0 {
                        let end = wall.timestamp_millis();
                        let mut start = end - delta_ms;
                        while start < end {
                            let next = ((start.div_euclid(1000) + 1) * 1000).min(end);
                            if let Some(date) = DateTime::from_timestamp_millis(start) {
                                let day = date.with_timezone(&Local).format("%Y-%m-%d").to_string();
                                *self.pending.entry(day).or_default() += next - start;
                            }
                            start = next;
                        }
                    }
                } else if id != old_id || generation != old_generation {
                    self.rem_ns = 0;
                }
            }
        } else {
            self.rem_ns = 0;
        }
    }

    pub fn flush(&mut self, db: &mut Db) -> Result<()> {
        if self.pending.is_empty() {
            return Ok(());
        }
        let tx = db.conn.transaction()?;
        for (day, milliseconds) in &self.pending {
            tx.execute(
                "INSERT INTO listening_days(day,milliseconds) VALUES (?,?) ON CONFLICT(day) DO UPDATE SET milliseconds=milliseconds+excluded.milliseconds",
                rusqlite::params![day, milliseconds],
            )?;
        }
        tx.commit()?;
        self.pending.clear();
        if self
            .last_file_sync
            .is_none_or(|t| t.elapsed() >= Duration::from_secs(30))
        {
            self.last_file_sync = Some(Instant::now());
            let _ = sync_local_stats_files_report(db);
        }
        Ok(())
    }
}

fn is_stop_ancestor(path: &Path) -> bool {
    matches!(
        path.to_str(),
        Some("/" | "/mnt" | "/media" | "/run" | "/run/media" | "/home" | "/var" | "/tmp")
    )
}

fn push_unique_dir(list: &mut Vec<PathBuf>, seen: &mut HashSet<PathBuf>, dir: PathBuf) {
    if !dir.is_dir() {
        return;
    }
    let key = dir.canonicalize().unwrap_or_else(|_| dir.clone());
    if seen.insert(key) {
        list.push(dir);
    }
}

struct SyncDirs {
    export_dirs: Vec<PathBuf>,
    probe_dirs: Vec<PathBuf>,
    primary_dirs: HashSet<PathBuf>,
}

fn resolve_sync_dirs(db: &Db) -> SyncDirs {
    let mut export_dirs = Vec::new();
    let mut export_seen = HashSet::new();
    let mut probe_dirs = Vec::new();
    let mut probe_seen = HashSet::new();
    let mut primary_dirs = HashSet::new();

    let local_setting = db.get("local_library_path").unwrap_or_default();
    let local_configured = !local_setting.trim().is_empty();
    let local_resolved = if local_configured {
        crate::local::resolve_path(&local_setting).ok()
    } else {
        None
    };
    let local_accessible = local_configured && db.is_local_drive_accessible(&local_setting);
    let local_locked = local_configured && !local_accessible;

    let is_locked_local_path = |candidate: &Path| -> bool {
        local_locked
            && local_resolved
                .as_ref()
                .is_some_and(|lr| candidate.starts_with(lr) || lr.starts_with(candidate))
    };

    // 1. Explicit environment override (`DUSKY_CASTS_STATS_DIR`).
    if let Ok(env_dir) = std::env::var("DUSKY_CASTS_STATS_DIR")
        && !env_dir.trim().is_empty()
        && let Ok(resolved) = crate::local::resolve_path(&env_dir)
        && !is_locked_local_path(&resolved)
        && resolved.is_dir()
    {
        primary_dirs.insert(resolved.canonicalize().unwrap_or_else(|_| resolved.clone()));
        push_unique_dir(&mut export_dirs, &mut export_seen, resolved.clone());
        push_unique_dir(&mut probe_dirs, &mut probe_seen, resolved);
    }

    // 2. Explicit user-configured FTP stats directory (`ftp_stats_dir`).
    if let Ok(ftp_dir) = db.get("ftp_stats_dir")
        && !ftp_dir.trim().is_empty()
        && let Ok(resolved) = crate::local::resolve_path(&ftp_dir)
        && !is_locked_local_path(&resolved)
        && resolved.is_dir()
    {
        primary_dirs.insert(resolved.canonicalize().unwrap_or_else(|_| resolved.clone()));
        push_unique_dir(&mut export_dirs, &mut export_seen, resolved.clone());
        push_unique_dir(&mut probe_dirs, &mut probe_seen, resolved);
    }

    // 3. Previously auto-converged FTP stats directory (`discovered_ftp_stats_dir`).
    if let Ok(discovered) = db.get("discovered_ftp_stats_dir")
        && !discovered.trim().is_empty()
        && let Ok(resolved) = crate::local::resolve_path(&discovered)
        && !is_locked_local_path(&resolved)
        && resolved.is_dir()
    {
        push_unique_dir(&mut export_dirs, &mut export_seen, resolved.clone());
        push_unique_dir(&mut probe_dirs, &mut probe_seen, resolved);
    }

    // 4. Local database directory (`$XDG_DATA_HOME/dusky_casts`).
    if let Some(parent) = db.path.parent()
        && parent.is_dir()
    {
        let p = parent.to_path_buf();
        primary_dirs.insert(p.canonicalize().unwrap_or_else(|_| p.clone()));
        push_unique_dir(&mut export_dirs, &mut export_seen, p.clone());
        push_unique_dir(&mut probe_dirs, &mut probe_seen, p);
    }

    // 5. Accessible local audio library and up to 3 same-filesystem ancestors (read-only probe).
    if local_accessible
        && let Some(resolved) = &local_resolved
        && resolved.is_dir()
    {
        let canon = resolved.canonicalize().unwrap_or_else(|_| resolved.clone());
        primary_dirs.insert(canon.clone());
        push_unique_dir(&mut export_dirs, &mut export_seen, canon.clone());
        push_unique_dir(&mut probe_dirs, &mut probe_seen, canon.clone());

        if let Ok(root_meta) = std::fs::metadata(&canon) {
            let root_dev = root_meta.dev();
            let mut cursor = canon.as_path();
            for _ in 0..3 {
                let Some(parent) = cursor.parent() else {
                    break;
                };
                if is_stop_ancestor(parent) {
                    break;
                }
                let Ok(meta) = std::fs::metadata(parent) else {
                    break;
                };
                if !meta.is_dir() || meta.dev() != root_dev {
                    break;
                }
                push_unique_dir(&mut probe_dirs, &mut probe_seen, parent.to_path_buf());
                cursor = parent;
            }
        }
    }

    // 6. Standard user XDG/Home read-only probe when running against the default library.
    if db.path == crate::db::default_path() {
        let home = crate::db::xdg("HOME", "");
        let downloads = std::env::var_os("XDG_DOWNLOAD_DIR")
            .filter(|p| !p.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| home.join("Downloads"));
        let public = std::env::var_os("XDG_PUBLICSHARE_DIR")
            .filter(|p| !p.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| home.join("Public"));
        for dir in [home, downloads, public] {
            if !is_locked_local_path(&dir) {
                push_unique_dir(&mut probe_dirs, &mut probe_seen, dir);
            }
        }
    }

    SyncDirs {
        export_dirs,
        probe_dirs,
        primary_dirs,
    }
}

fn write_atomic_json(dir: &Path, filename: &str, payload: &[u8]) -> Result<()> {
    let seq = TMP_COUNTER.fetch_add(1, Ordering::Relaxed);
    let tmp = dir.join(format!("{filename}.tmp.{}.{seq}", std::process::id()));
    let target = dir.join(filename);
    let mut file = std::fs::File::create(&tmp)?;
    if let Err(e) = file.write_all(payload).and_then(|_| file.sync_all()) {
        let _ = std::fs::remove_file(&tmp);
        return Err(e.into());
    }
    drop(file);
    if let Err(e) = std::fs::rename(&tmp, &target) {
        let _ = std::fs::remove_file(&tmp);
        return Err(e.into());
    }
    Ok(())
}

fn parse_ios_manifest(raw: &str, merged_ios: &mut BTreeMap<String, i64>) -> bool {
    let mut found_any = false;
    if let Ok(manifest) = serde_json::from_str::<DeviceStatsManifest>(raw) {
        for (day, secs) in manifest.days_seconds {
            if secs.is_finite() && secs > 0.0 && day.len() == 10 {
                let ms = (secs * 1000.0).round() as i64;
                if ms > 0 {
                    let entry = merged_ios.entry(day).or_insert(0);
                    *entry = (*entry).max(ms);
                    found_any = true;
                }
            }
        }
        for (day, ms) in manifest.days_ms {
            if ms > 0 && day.len() == 10 {
                let entry = merged_ios.entry(day).or_insert(0);
                *entry = (*entry).max(ms);
                found_any = true;
            }
        }
    } else if let Ok(raw_map) = serde_json::from_str::<BTreeMap<String, f64>>(raw) {
        for (day, secs) in raw_map {
            if secs.is_finite() && secs > 0.0 && day.len() == 10 {
                let ms = (secs * 1000.0).round() as i64;
                if ms > 0 {
                    let entry = merged_ios.entry(day).or_insert(0);
                    *entry = (*entry).max(ms);
                    found_any = true;
                }
            }
        }
    }
    found_any
}

pub fn sync_local_stats_files(db: &mut Db) -> Result<usize> {
    Ok(sync_local_stats_files_report(db)?.imported_days)
}

pub fn sync_local_stats_files_report(db: &mut Db) -> Result<StatsSyncReport> {
    let mut dirs = resolve_sync_dirs(db);
    let stamp = now() / 1000;

    // 1. Ingest `.dusky_casts-stats-ios.json` (and legacy `.duskcasts-stats-ios.json` fallback)
    //    from any probed directory.
    let mut merged_ios: BTreeMap<String, i64> = BTreeMap::new();
    let mut ios_signatures = Vec::new();
    let mut ios_sources = Vec::new();
    let mut export_seen: HashSet<PathBuf> = dirs
        .export_dirs
        .iter()
        .map(|d| d.canonicalize().unwrap_or_else(|_| d.clone()))
        .collect();
    let mut discovered_to_persist: Option<PathBuf> = None;

    for dir in &dirs.probe_dirs {
        for filename in IOS_STATS_CANDIDATES {
            let ios_path = dir.join(filename);
            let Ok(meta) = std::fs::metadata(&ios_path) else {
                continue;
            };
            if !meta.is_file() || meta.len() == 0 {
                continue;
            }
            let Ok(raw) = std::fs::read_to_string(&ios_path) else {
                continue;
            };
            if !parse_ios_manifest(&raw, &mut merged_ios) {
                continue;
            }
            ios_signatures.push(format!(
                "{}:{}:{}:{}",
                ios_path.display(),
                meta.len(),
                meta.mtime(),
                meta.mtime_nsec()
            ));
            ios_sources.push(ios_path);

            // Auto-converge: export PC stats alongside any newly discovered iOS manifest.
            let canon = dir.canonicalize().unwrap_or_else(|_| dir.clone());
            push_unique_dir(&mut dirs.export_dirs, &mut export_seen, dir.clone());
            if discovered_to_persist.is_none() && !dirs.primary_dirs.contains(&canon) {
                discovered_to_persist = Some(canon);
            }
        }
    }

    if let Some(discovered) = discovered_to_persist
        && let Some(text) = discovered.to_str()
    {
        let _ = db.set("discovered_ftp_stats_dir", text);
    }

    let imported_days = merged_ios.len();
    let mut updated_days = 0usize;
    if !merged_ios.is_empty() {
        let sig_joined = ios_signatures.join("|");
        let prev_sig = db.get("ios_stats_file_sig").unwrap_or_default();
        let existing_rows: i64 = db
            .conn
            .query_row("SELECT count(*) FROM ios_listening_days", [], |r| r.get(0))
            .unwrap_or(0);
        if sig_joined != prev_sig || existing_rows == 0 {
            let tx = db.conn.transaction()?;
            for (day, ms) in &merged_ios {
                updated_days += tx.execute(
                    "INSERT INTO ios_listening_days(day,milliseconds,updated_at) VALUES (?,?,?) ON CONFLICT(day) DO UPDATE SET milliseconds=max(ios_listening_days.milliseconds,excluded.milliseconds),updated_at=excluded.updated_at WHERE excluded.milliseconds > ios_listening_days.milliseconds",
                    rusqlite::params![day, ms, stamp],
                )?;
            }
            tx.execute(
                "INSERT INTO settings(key,value) VALUES ('ios_stats_synced_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [stamp.to_string()],
            )?;
            tx.execute(
                "INSERT INTO settings(key,value) VALUES ('ios_stats_file_sig',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [&sig_joined],
            )?;
            tx.commit()?;
        }
    }

    // 2. Export `.dusky_casts-stats-pc.json` when PC totals or target files need updating.
    let mut days_ms = BTreeMap::new();
    let mut days_seconds = BTreeMap::new();
    let mut total_pc_ms = 0i64;
    {
        let mut stmt = db
            .conn
            .prepare("SELECT day,milliseconds FROM listening_days ORDER BY day ASC")?;
        let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))?;
        for row in rows {
            let (day, ms) = row?;
            total_pc_ms = total_pc_ms.saturating_add(ms);
            days_ms.insert(day.clone(), ms);
            days_seconds.insert(day, (ms as f64 / 1000.0 * 100.0).round() / 100.0);
        }
    }
    let pc_sig = format!("{}:{total_pc_ms}", days_ms.len());
    let prev_pc_sig = db.get("pc_stats_export_sig").unwrap_or_default();
    let any_missing = dirs
        .export_dirs
        .iter()
        .any(|dir| !dir.join(PC_STATS_FILENAME).is_file());
    let mut pc_exported = false;
    if pc_sig != prev_pc_sig || any_missing {
        let manifest = DeviceStatsManifest {
            device_id: db.get("sync_device_id").unwrap_or_default(),
            device_name: "Dusky Casts PC".into(),
            updated_at: stamp,
            days_ms,
            days_seconds,
        };
        if let Ok(bytes) = serde_json::to_vec_pretty(&manifest) {
            for dir in &dirs.export_dirs {
                if write_atomic_json(dir, PC_STATS_FILENAME, &bytes).is_ok() {
                    pc_exported = true;
                }
            }
            if pc_exported {
                let _ = db.set("pc_stats_export_sig", &pc_sig);
            }
        }
    }

    Ok(StatsSyncReport {
        export_dirs: dirs.export_dirs,
        ios_sources,
        imported_days,
        updated_days,
        pc_exported,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;
    use std::time::Duration;
    #[test]
    fn seeks_pauses_replays_suspend_and_persistence() {
        let dir = tempfile::tempdir().unwrap();
        let mut db = Db::open(dir.path().join("library.db")).unwrap();
        let mut stats = Listening::default();
        let base = Instant::now();
        let wall = Local::now();
        for (ms, active) in [
            (0, Some((1, 0, 0.0))),
            (500, Some((1, 0, 1.0))),
            (1000, Some((1, 0, 5000.0))),
            (1500, None),
            (2000, Some((1, 0, 10.0))),
            (2500, Some((1, 0, 11.0))),
            (9000, Some((1, 0, 12.0))),
            (9500, Some((1, 1, 13.0))),
            (10000, Some((2, 1, 14.0))),
        ] {
            stats.sample(
                base + Duration::from_millis(ms),
                wall + chrono::Duration::milliseconds(ms as i64),
                active,
            );
        }
        assert_eq!(stats.pending.values().sum::<i64>(), 1500);
        stats.flush(&mut db).unwrap();
        stats.flush(&mut db).unwrap();
        drop(db);
        let db = Db::open(dir.path().join("library.db")).unwrap();
        assert_eq!(
            db.conn
                .query_row("SELECT sum(milliseconds) FROM listening_days", [], |r| r
                    .get::<_, i64>(0))
                .unwrap(),
            1500
        );
    }
    #[test]
    fn midnight_is_split_by_local_calendar_day() {
        let midnight = Local
            .with_ymd_and_hms(2026, 10, 27, 0, 0, 0)
            .single()
            .unwrap();
        let mut stats = Listening::default();
        let base = Instant::now();
        stats.sample(
            base,
            midnight - chrono::Duration::milliseconds(500),
            Some((1, 0, 1.0)),
        );
        stats.sample(
            base + Duration::from_secs(1),
            midnight + chrono::Duration::milliseconds(500),
            Some((1, 0, 2.0)),
        );
        assert_eq!(stats.pending["2026-10-26"], 500);
        assert_eq!(stats.pending["2026-10-27"], 500);
    }
}
