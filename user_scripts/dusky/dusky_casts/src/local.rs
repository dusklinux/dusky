//! A user-selected filesystem library. File bytes are never copied or uploaded.
use crate::{
    db::{Db, now},
    player::Player,
};
use anyhow::{Context, Result};
use rusqlite::{OptionalExtension, params};
use serde_json::json;
use std::{
    os::unix::fs::MetadataExt,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

pub fn resolve_path(text: &str) -> Result<PathBuf> {
    let text = text.trim();
    anyhow::ensure!(!text.is_empty(), "Enter a local library folder");
    let path = if text == "~" {
        crate::db::xdg("HOME", "")
    } else if let Some(rest) = text.strip_prefix("~/") {
        crate::db::xdg("HOME", "").join(rest)
    } else {
        PathBuf::from(text)
    };
    anyhow::ensure!(path.is_absolute(), "Use an absolute folder path or ~/…");
    Ok(path)
}

pub fn directory(text: &str) -> Result<PathBuf> {
    let path = resolve_path(text)?;
    let path = path
        .canonicalize()
        .context("Opening local library folder")?;
    anyhow::ensure!(path.is_dir(), "Local library path must be a folder");
    Ok(path)
}

fn parse_leading_num(file_name: &str) -> Option<i64> {
    let digits: String = file_name
        .chars()
        .take_while(|c| c.is_ascii_digit())
        .collect();
    if digits.is_empty() {
        None
    } else {
        digits.parse::<i64>().ok()
    }
}

struct File {
    url: String,
    path: PathBuf,
    size: i64,
    modified: String,
    mtime_secs: i64,
    previous: Option<Option<i64>>,
    audio: Option<(String, i64, i64)>,
}

#[derive(Debug, Clone, Default)]
pub struct Scan {
    pub audio: usize,
    pub probed: usize,
    pub skipped: usize,
    pub drive_locked: bool,
}

fn metadata_timestamp(metadata: &serde_json::Value) -> i64 {
    let Some(map) = metadata.as_object() else {
        return 0;
    };
    for key in ["creation_time", "date", "tdrc", "tyer", "origtime", "year"] {
        let Some((_, value)) = map.iter().find(|(name, _)| name.eq_ignore_ascii_case(key)) else {
            continue;
        };
        let Some(value) = value
            .as_str()
            .map(str::trim)
            .filter(|value| !value.is_empty())
        else {
            continue;
        };
        if let Ok(datetime) = chrono::DateTime::parse_from_rfc3339(value) {
            return datetime.timestamp().max(0);
        }
        if let Ok(date) = chrono::NaiveDate::parse_from_str(value, "%Y-%m-%d")
            && let Some(datetime) = date.and_hms_opt(0, 0, 0)
        {
            return datetime.and_utc().timestamp().max(0);
        }
        if let Some(year) = value.get(0..4).and_then(|year| year.parse::<i32>().ok())
            && let Some(date) = chrono::NaiveDate::from_ymd_opt(year, 1, 1)
            && let Some(datetime) = date.and_hms_opt(0, 0, 0)
        {
            return datetime.and_utc().timestamp().max(0);
        }
    }
    0
}

fn is_same_configured_root(saved: &str, candidate: &Path) -> bool {
    if saved.trim().is_empty() {
        return false;
    }
    let Ok(saved_path) = resolve_path(saved) else {
        return false;
    };
    saved_path == candidate
        || saved_path
            .canonicalize()
            .ok()
            .is_some_and(|canon| canon == candidate)
}

pub fn scan(db: &mut Db, text: &str) -> Result<Scan> {
    let saved_root = db.get("local_library_path")?;
    let has_indexed = db.has_any_indexed_local_files()?;
    let saved_dev = db.number("local_library_dev").unwrap_or(0) as u64;
    let raw_path = resolve_path(text)?;
    let same_root = is_same_configured_root(&saved_root, &raw_path);

    let root = match directory(text) {
        Ok(dir) => dir,
        Err(e) => {
            if has_indexed && same_root {
                return Ok(Scan {
                    drive_locked: true,
                    ..Default::default()
                });
            }
            return Err(e);
        }
    };

    let root_dev = std::fs::metadata(&root).map(|m| m.dev()).unwrap_or(0);
    if has_indexed
        && is_same_configured_root(&saved_root, &root)
        && saved_dev > 0
        && root_dev != saved_dev
        && !db.sample_indexed_files_exist()
    {
        return Ok(Scan {
            drive_locked: true,
            ..Default::default()
        });
    }
    let mut directories = vec![root.clone()];
    let mut files = Vec::new();
    while let Some(dir) = directories.pop() {
        for entry in
            std::fs::read_dir(&dir).with_context(|| format!("Reading {}", dir.display()))?
        {
            let entry = entry?;
            let kind = entry.file_type()?;
            if kind.is_dir() {
                directories.push(entry.path());
                continue;
            }
            let path = entry.path();
            let metadata =
                std::fs::metadata(&path).with_context(|| format!("Reading {}", path.display()))?;
            if !metadata.is_file() {
                continue;
            }
            let url = reqwest::Url::from_file_path(&path)
                .map_err(|_| anyhow::anyhow!("Invalid local path"))?
                .to_string();
            let mtime_secs = metadata.mtime();
            let modified = format!(
                "{}:{}:{}:{}",
                mtime_secs,
                metadata.mtime_nsec(),
                metadata.ctime(),
                metadata.ctime_nsec()
            );
            let size = i64::try_from(metadata.len()).context("File is too large")?;
            let previous = db
                .conn
                .query_row(
                    "SELECT episode_id FROM local_files WHERE path=? AND size=? AND modified=?",
                    params![url, size, modified],
                    |r| r.get::<_, Option<i64>>(0),
                )
                .optional()?;
            files.push(File {
                url,
                path,
                size,
                modified,
                mtime_secs,
                previous,
                audio: None,
            });
        }
    }

    // Hard safety guard: if the configured folder already had indexed audio files in
    // SQLite, but directory enumeration found 0 files (e.g., an unmounted LUKS/encrypted
    // mountpoint), abort before modifying `available` or `local_queue`.
    if files.is_empty() && has_indexed && is_same_configured_root(&saved_root, &root) {
        return Ok(Scan {
            drive_locked: true,
            ..Default::default()
        });
    }

    files.sort_unstable_by(|a, b| a.url.cmp(&b.url));
    let mut probe = None;
    let mut result = Scan::default();
    for file in &mut files {
        if let Some(old) = file.previous {
            if old.is_some() {
                result.audio += 1;
            } else {
                result.skipped += 1;
            }
            continue;
        }
        result.probed += 1;
        if probe.is_none() {
            probe = Some(Player::start("off", 1.0, 0.0, true)?);
        }
        let p = probe.as_mut().unwrap();
        if p.load(&file.url, 0.0, true).is_err() {
            probe = None;
            result.skipped += 1;
            continue;
        }
        let deadline = Instant::now() + Duration::from_secs(4);
        let state = p.wait_for_load_or_end(deadline);
        if state.loaded {
            let tracks = p
                .call(json!(["get_property", "track-list"]))
                .unwrap_or_default();
            let actual = p.call(json!(["get_property", "path"])).unwrap_or_default();
            if (actual.as_str() == Some(&file.url)
                || actual.as_str() == Some(file.path.to_string_lossy().as_ref()))
                && tracks
                    .as_array()
                    .is_some_and(|tracks| tracks.iter().any(|t| t["type"] == "audio"))
            {
                let metadata = p
                    .call(json!(["get_property", "metadata"]))
                    .unwrap_or_default();
                let title = metadata
                    .as_object()
                    .and_then(|m| m.iter().find(|(k, _)| k.eq_ignore_ascii_case("title")))
                    .and_then(|(_, v)| v.as_str())
                    .filter(|s| !s.trim().is_empty())
                    .map(str::to_owned)
                    .unwrap_or_else(|| {
                        file.path
                            .file_stem()
                            .unwrap_or_default()
                            .to_string_lossy()
                            .into_owned()
                    });
                let duration = p
                    .call(json!(["get_property", "duration"]))
                    .ok()
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.0)
                    .max(0.0) as i64;
                file.audio = Some((title, duration, metadata_timestamp(&metadata)));
                result.audio += 1;
            } else {
                result.skipped += 1;
            }
            if p.call(json!(["stop"])).is_err() {
                probe = None;
            }
        } else if state.error.is_some() || state.eof || state.stopped {
            result.skipped += 1;
        } else {
            anyhow::bail!(
                "mpv timed out inspecting {}; previous library retained",
                file.path.display()
            );
        }
    }

    if result.audio == 0 && has_indexed && is_same_configured_root(&saved_root, &root) {
        return Ok(Scan {
            drive_locked: true,
            ..Default::default()
        });
    }

    let tx = db.conn.transaction()?;
    tx.execute(
        "INSERT OR IGNORE INTO podcasts(uuid,title,feed_url,added_at,subscribed) VALUES ('local-library','Local audio','local://library',?,0)",
        [now() / 1000],
    )?;
    let pid: i64 = tx.query_row(
        "SELECT id FROM podcasts WHERE uuid='local-library'",
        [],
        |r| r.get(0),
    )?;
    tx.execute("UPDATE local_files SET available=0", [])?;
    for file in files {
        let rel = file.path.strip_prefix(&root).unwrap_or(&file.path);
        let description = rel.to_string_lossy();
        let file_name = rel
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or(&description)
            .to_owned();
        let parent_dir = rel
            .parent()
            .and_then(|p| p.to_str())
            .unwrap_or("")
            .to_owned();
        let leading_num = parse_leading_num(&file_name);

        let episode_id = if let Some((title, duration, published_at)) = file.audio {
            let uuid = format!(
                "local:{}",
                uuid::Uuid::new_v5(&uuid::Uuid::NAMESPACE_URL, file.url.as_bytes())
            );
            tx.execute(
                "INSERT INTO episodes(uuid,podcast_id,title,description,audio_url,file_type,published_at,duration,size,added_at) VALUES (?,?,?,?,?,'audio/local',?,?,?,?) ON CONFLICT(uuid) DO UPDATE SET title=excluded.title,description=excluded.description,size=excluded.size,published_at=CASE WHEN excluded.published_at>0 THEN excluded.published_at ELSE episodes.published_at END,duration=CASE WHEN excluded.duration>0 THEN excluded.duration ELSE episodes.duration END",
                params![
                    uuid,
                    pid,
                    title,
                    description,
                    file.url,
                    published_at,
                    duration,
                    file.size,
                    now() / 1000
                ],
            )?;
            Some(
                tx.query_row("SELECT id FROM episodes WHERE uuid=?", [uuid], |r| {
                    r.get::<_, i64>(0)
                })?,
            )
        } else {
            file.previous.flatten()
        };
        if let Some(id) = episode_id {
            tx.execute(
                "UPDATE episodes SET description=? WHERE id=? AND description!=?",
                params![description, id, description],
            )?;
        }
        tx.execute(
            "INSERT INTO local_files(path,episode_id,size,modified,available,parent_dir,file_name,leading_num,mtime_secs) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET episode_id=excluded.episode_id,size=excluded.size,modified=excluded.modified,available=excluded.available,parent_dir=excluded.parent_dir,file_name=excluded.file_name,leading_num=excluded.leading_num,mtime_secs=excluded.mtime_secs",
            params![
                file.url,
                episode_id,
                file.size,
                file.modified,
                episode_id.is_some(),
                parent_dir,
                file_name,
                leading_num,
                file.mtime_secs
            ],
        )?;
    }
    tx.execute(
        "DELETE FROM local_queue WHERE episode_id NOT IN (SELECT episode_id FROM local_files WHERE available=1 AND episode_id IS NOT NULL)",
        [],
    )?;
    let root_str = root
        .to_str()
        .context("The selected folder path must be UTF-8")?;
    tx.execute(
        "INSERT INTO settings(key,value) VALUES ('local_library_path',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        [root_str],
    )?;
    if root_dev > 0 {
        tx.execute(
            "INSERT INTO settings(key,value) VALUES ('local_library_dev',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [root_dev.to_string()],
        )?;
    }
    tx.commit()?;
    db.refresh_local_sort_cache();
    Ok(result)
}
