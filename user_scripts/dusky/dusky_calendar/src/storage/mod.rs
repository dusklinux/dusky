//! Runtime XDG storage, `calendars.toml` manifest, atomic `.ics` persistence,
//! and an ordered single-thread background write worker.

pub mod ics;

use crate::InitialView;
use crate::model::{CalendarColor, CalendarMeta, EditScope, Event, EventKey};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, Sender};
use std::thread::JoinHandle;
use std::{env, process};

const MANIFEST_FILE: &str = "calendars.toml";
const CALENDARS_SUBDIR: &str = "calendars";

const MIN_SAVED_WINDOW_WIDTH: f32 = 360.0;
const MAX_SAVED_WINDOW_WIDTH: f32 = 7680.0;
const MIN_SAVED_WINDOW_HEIGHT: f32 = 600.0;
const MAX_SAVED_WINDOW_HEIGHT: f32 = 4320.0;

fn sanitize_window_dim(value: Option<f32>, min: f32, max: f32) -> Option<f32> {
    value.filter(|v| v.is_finite() && *v >= min && *v <= max)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StoredView {
    Month,
    Week,
    Agenda,
}

impl Default for StoredView {
    fn default() -> Self {
        Self::Month
    }
}

impl From<InitialView> for StoredView {
    fn from(value: InitialView) -> Self {
        match value {
            InitialView::Month => Self::Month,
            InitialView::Week => Self::Week,
            InitialView::Agenda => Self::Agenda,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UserPrefs {
    #[serde(default)]
    pub default_view: StoredView,
    #[serde(default = "default_true")]
    pub week_start_monday: bool,
    #[serde(default = "default_true")]
    pub use_24h_time: bool,
    #[serde(default = "default_true")]
    pub show_week_numbers: bool,
    #[serde(default = "default_week_hour_height")]
    pub week_hour_height: f32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub window_width: Option<f32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub window_height: Option<f32>,
}

fn default_true() -> bool {
    true
}

fn default_week_hour_height() -> f32 {
    60.0
}

impl Default for UserPrefs {
    fn default() -> Self {
        Self {
            default_view: StoredView::Month,
            week_start_monday: true,
            use_24h_time: true,
            show_week_numbers: true,
            week_hour_height: 60.0,
            window_width: None,
            window_height: None,
        }
    }
}

impl UserPrefs {
    pub fn valid_window_size(&self) -> Option<(f32, f32)> {
        let w = sanitize_window_dim(
            self.window_width,
            MIN_SAVED_WINDOW_WIDTH,
            MAX_SAVED_WINDOW_WIDTH,
        )?;
        let h = sanitize_window_dim(
            self.window_height,
            MIN_SAVED_WINDOW_HEIGHT,
            MAX_SAVED_WINDOW_HEIGHT,
        )?;
        Some((w, h))
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CalendarManifest {
    #[serde(default)]
    prefs: UserPrefs,
    #[serde(default)]
    calendars: Vec<CalendarMeta>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ImportSummary {
    pub imported: usize,
    pub updated: usize,
    pub skipped: usize,
}

enum WriteOp {
    AtomicWrite { path: PathBuf, content: String },
    RemoveFile { path: PathBuf },
    FlushAndStop(Sender<()>),
}

struct PersistenceWorker {
    tx: Option<Sender<WriteOp>>,
    handle: Option<JoinHandle<()>>,
}

impl PersistenceWorker {
    fn spawn() -> Self {
        let (tx, rx) = mpsc::channel::<WriteOp>();
        let handle = std::thread::Builder::new()
            .name("calendar-persist".into())
            .spawn(move || {
                while let Ok(op) = rx.recv() {
                    match op {
                        WriteOp::AtomicWrite { path, content } => {
                            if let Err(err) = atomic_write_string(&path, &content) {
                                eprintln!(
                                    "dusky_calendar: failed writing {}: {err}",
                                    path.display()
                                );
                            }
                        }
                        WriteOp::RemoveFile { path } => {
                            let _ = fs::remove_file(&path);
                        }
                        WriteOp::FlushAndStop(ack) => {
                            let _ = ack.send(());
                            break;
                        }
                    }
                }
            })
            .ok();

        Self {
            tx: Some(tx),
            handle,
        }
    }

    fn enqueue_write(&self, path: PathBuf, content: String) {
        if let Some(tx) = &self.tx {
            if tx.send(WriteOp::AtomicWrite { path: path.clone(), content: content.clone() }).is_err()
            {
                let _ = atomic_write_string(&path, &content);
            }
        } else {
            let _ = atomic_write_string(&path, &content);
        }
    }

    fn enqueue_remove(&self, path: PathBuf) {
        if let Some(tx) = &self.tx {
            if tx.send(WriteOp::RemoveFile { path: path.clone() }).is_err() {
                let _ = fs::remove_file(&path);
            }
        } else {
            let _ = fs::remove_file(&path);
        }
    }

    fn flush_and_stop(&mut self) {
        if let Some(tx) = self.tx.take() {
            let (ack_tx, ack_rx) = mpsc::channel();
            if tx.send(WriteOp::FlushAndStop(ack_tx)).is_ok() {
                let _ = ack_rx.recv();
            }
        }
        if let Some(handle) = self.handle.take() {
            let _ = handle.join();
        }
    }
}

impl Drop for PersistenceWorker {
    fn drop(&mut self) {
        self.flush_and_stop();
    }
}

/// Atomically writes `content` to `path` via a same-directory temp file, `sync_all`, and `rename`.
pub fn atomic_write_string(path: &Path, content: &str) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }

    let file_name = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("calendar.tmp");
    let tmp_path = path.with_file_name(format!(".{file_name}.{}.tmp", process::id()));

    let write_res = (|| -> std::io::Result<()> {
        let mut file = File::create(&tmp_path)?;
        file.write_all(content.as_bytes())?;
        file.sync_all()?;
        Ok(())
    })();

    if let Err(err) = write_res {
        let _ = fs::remove_file(&tmp_path);
        return Err(err);
    }

    if let Err(err) = fs::rename(&tmp_path, path) {
        let _ = fs::remove_file(&tmp_path);
        return Err(err);
    }
    Ok(())
}

/// Resolves `$XDG_DATA_HOME/dusky/dusky_calendar` (fallback `$HOME/.local/share/dusky/dusky_calendar`).
pub fn resolve_data_dir(override_dir: Option<&Path>) -> PathBuf {
    if let Some(custom) = override_dir {
        return custom.to_path_buf();
    }

    let base = env::var_os("XDG_DATA_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .unwrap_or_else(|| {
            PathBuf::from(env::var_os("HOME").unwrap_or_default()).join(".local/share")
        });
    base.join("dusky/dusky_calendar")
}

/// Lightweight startup reader that loads saved window dimensions from `calendars.toml`
/// without parsing `.ics` files or spawning the background persistence worker.
pub fn load_saved_window_size(override_dir: Option<&Path>) -> Option<(f32, f32)> {
    let manifest_path = resolve_data_dir(override_dir).join(MANIFEST_FILE);
    let raw = fs::read_to_string(manifest_path).ok()?;
    let manifest = toml::from_str::<CalendarManifest>(&raw).ok()?;
    manifest.prefs.valid_window_size()
}

/// Resolves the user's Downloads directory for one-click `.ics` export.
pub fn resolve_export_dir() -> PathBuf {
    if let Some(dir) = env::var_os("XDG_DOWNLOAD_DIR")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .filter(|p| p.is_absolute() && p.is_dir())
    {
        return dir;
    }

    let home = PathBuf::from(env::var_os("HOME").unwrap_or_default());
    let user_dirs = env::var_os("XDG_CONFIG_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .unwrap_or_else(|| home.join(".config"))
        .join("user-dirs.dirs");

    if let Ok(raw) = fs::read_to_string(&user_dirs) {
        for line in raw.lines() {
            let trimmed = line.trim();
            if let Some(val) = trimmed.strip_prefix("XDG_DOWNLOAD_DIR=") {
                let unquoted = val.trim().trim_matches('"');
                let candidate = if let Some(rest) = unquoted.strip_prefix("$HOME/") {
                    home.join(rest)
                } else if unquoted == "$HOME" {
                    home.clone()
                } else {
                    PathBuf::from(unquoted)
                };
                if candidate.is_absolute() && candidate.is_dir() {
                    return candidate;
                }
            }
        }
    }

    let downloads = home.join("Downloads");
    if downloads.is_dir() {
        downloads
    } else if home.is_dir() {
        home
    } else {
        PathBuf::from("/tmp")
    }
}

fn sanitize_filename_stem(raw: &str) -> String {
    let mut out = String::with_capacity(raw.len());
    for ch in raw.chars() {
        if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
            out.push(ch);
        } else if ch.is_whitespace() && !out.ends_with('_') {
            out.push('_');
        }
        if out.len() >= 48 {
            break;
        }
    }
    let trimmed = out.trim_matches('_');
    if trimmed.is_empty() {
        "event".to_string()
    } else {
        trimmed.to_string()
    }
}

fn unique_export_path(dir: &Path, stem: &str) -> PathBuf {
    let base = sanitize_filename_stem(stem);
    let primary = dir.join(format!("{base}.ics"));
    if !primary.exists() {
        return primary;
    }
    for idx in 2..=999 {
        let candidate = dir.join(format!("{base}-{idx}.ics"));
        if !candidate.exists() {
            return candidate;
        }
    }
    dir.join(format!("{base}-{}.ics", Utc::now().timestamp()))
}

pub struct CalendarStore {
    pub data_dir: PathBuf,
    pub prefs: UserPrefs,
    pub calendars: Vec<CalendarMeta>,
    events_by_calendar: HashMap<String, Vec<Event>>,
    worker: PersistenceWorker,
}

impl CalendarStore {
    pub fn load(override_dir: Option<&Path>) -> Self {
        let data_dir = resolve_data_dir(override_dir);
        let cal_dir = data_dir.join(CALENDARS_SUBDIR);
        let _ = fs::create_dir_all(&cal_dir);

        let manifest_path = data_dir.join(MANIFEST_FILE);
        let mut manifest = fs::read_to_string(&manifest_path)
            .ok()
            .and_then(|raw| toml::from_str::<CalendarManifest>(&raw).ok())
            .unwrap_or_else(|| CalendarManifest {
                prefs: UserPrefs::default(),
                calendars: Vec::new(),
            });

        manifest.prefs.week_hour_height = manifest.prefs.week_hour_height.clamp(40.0, 120.0);
        if let Some((w, h)) = manifest.prefs.valid_window_size() {
            manifest.prefs.window_width = Some(w);
            manifest.prefs.window_height = Some(h);
        } else {
            manifest.prefs.window_width = None;
            manifest.prefs.window_height = None;
        }

        let mut needs_manifest_save = !manifest_path.is_file();
        if manifest.calendars.is_empty() {
            manifest.calendars.push(CalendarMeta::default_personal());
            needs_manifest_save = true;
        }

        if !manifest.calendars.iter().any(|c| c.is_default) {
            manifest.calendars[0].is_default = true;
            needs_manifest_save = true;
        }

        let mut events_by_calendar = HashMap::with_capacity(manifest.calendars.len());
        let mut bootstrap_ics: Vec<(PathBuf, String)> = Vec::new();

        for cal in &manifest.calendars {
            let ics_path = cal_dir.join(&cal.file_name);
            let events = if ics_path.is_file() {
                match fs::read_to_string(&ics_path) {
                    Ok(raw) => match ics::parse_ics(&raw, &cal.id) {
                        Ok(parsed) => parsed.events,
                        Err(err) => {
                            eprintln!(
                                "dusky_calendar: failed parsing {}: {err}",
                                ics_path.display()
                            );
                            Vec::new()
                        }
                    },
                    Err(_) => Vec::new(),
                }
            } else {
                let empty_ics = ics::serialize_calendar(&cal.name, &[]);
                bootstrap_ics.push((ics_path, empty_ics));
                Vec::new()
            };
            events_by_calendar.insert(cal.id.clone(), events);
        }

        let store = Self {
            data_dir,
            prefs: manifest.prefs,
            calendars: manifest.calendars,
            events_by_calendar,
            worker: PersistenceWorker::spawn(),
        };

        if needs_manifest_save {
            store.queue_manifest_save();
        }
        for (path, content) in bootstrap_ics {
            store.worker.enqueue_write(path, content);
        }

        store
    }

    fn manifest_path(&self) -> PathBuf {
        self.data_dir.join(MANIFEST_FILE)
    }

    fn calendar_ics_path(&self, file_name: &str) -> PathBuf {
        self.data_dir.join(CALENDARS_SUBDIR).join(file_name)
    }

    fn queue_manifest_save(&self) {
        let manifest = CalendarManifest {
            prefs: self.prefs.clone(),
            calendars: self.calendars.clone(),
        };
        if let Ok(encoded) = toml::to_string_pretty(&manifest) {
            self.worker.enqueue_write(self.manifest_path(), encoded);
        }
    }

    fn queue_calendar_save(&self, calendar_id: &str) {
        let Some(cal) = self.calendar(calendar_id) else {
            return;
        };
        let events = self
            .events_by_calendar
            .get(calendar_id)
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        let content = ics::serialize_calendar(&cal.name, events);
        self.worker
            .enqueue_write(self.calendar_ics_path(&cal.file_name), content);
    }

    pub fn save_prefs(&self) {
        self.queue_manifest_save();
    }

    pub fn flush_and_shutdown(&mut self) {
        self.worker.flush_and_stop();
    }

    pub fn default_calendar_id(&self) -> String {
        self.calendars
            .iter()
            .find(|c| c.is_default)
            .or_else(|| self.calendars.first())
            .map(|c| c.id.clone())
            .unwrap_or_default()
    }

    pub fn calendar(&self, id: &str) -> Option<&CalendarMeta> {
        self.calendars.iter().find(|c| c.id == id)
    }

    pub fn events_for_calendar(&self, calendar_id: &str) -> &[Event] {
        self.events_by_calendar
            .get(calendar_id)
            .map(Vec::as_slice)
            .unwrap_or(&[])
    }

    pub fn total_event_count(&self) -> usize {
        self.events_by_calendar.values().map(Vec::len).sum()
    }

    pub fn visible_events(&self) -> Vec<Event> {
        let mut out = Vec::new();
        for cal in &self.calendars {
            if !cal.visible {
                continue;
            }
            if let Some(events) = self.events_by_calendar.get(&cal.id) {
                out.extend(events.iter().cloned());
            }
        }
        out
    }

    pub fn find_event(&self, calendar_id: &str, key: &EventKey) -> Option<&Event> {
        self.events_by_calendar
            .get(calendar_id)?
            .iter()
            .find(|ev| ev.uid == key.uid && ev.recurrence_id == key.recurrence_id)
    }

    pub fn find_event_any_calendar(&self, key: &EventKey) -> Option<&Event> {
        self.events_by_calendar
            .values()
            .flat_map(|v| v.iter())
            .find(|ev| ev.uid == key.uid && ev.recurrence_id == key.recurrence_id)
    }

    pub fn create_calendar(&mut self, name: String, color: CalendarColor) -> CalendarMeta {
        let trimmed = name.trim();
        let final_name = if trimmed.is_empty() {
            "New Calendar".to_string()
        } else {
            trimmed.to_string()
        };
        let meta = CalendarMeta::new(final_name, color, self.calendars.is_empty());
        let id = meta.id.clone();
        self.calendars.push(meta.clone());
        self.events_by_calendar.insert(id.clone(), Vec::new());
        self.queue_manifest_save();
        self.queue_calendar_save(&id);
        meta
    }

    pub fn update_calendar(
        &mut self,
        id: &str,
        name: String,
        color: CalendarColor,
        make_default: bool,
    ) -> bool {
        let Some(index) = self.calendars.iter().position(|c| c.id == id) else {
            return false;
        };
        let trimmed = name.trim();
        if !trimmed.is_empty() {
            self.calendars[index].name = trimmed.to_string();
        }
        self.calendars[index].color = color;
        if make_default {
            for (i, cal) in self.calendars.iter_mut().enumerate() {
                cal.is_default = i == index;
            }
        }
        self.queue_manifest_save();
        self.queue_calendar_save(id);
        true
    }

    pub fn toggle_calendar_visibility(&mut self, id: &str) -> bool {
        let Some(cal) = self.calendars.iter_mut().find(|c| c.id == id) else {
            return false;
        };
        cal.visible = !cal.visible;
        self.queue_manifest_save();
        true
    }

    pub fn delete_calendar(&mut self, id: &str) -> Result<String, String> {
        if self.calendars.len() <= 1 {
            return Err("Cannot delete the last remaining calendar.".to_string());
        }
        let Some(index) = self.calendars.iter().position(|c| c.id == id) else {
            return Err("Calendar not found.".to_string());
        };
        let removed = self.calendars.remove(index);
        self.events_by_calendar.remove(&removed.id);

        if removed.is_default && !self.calendars.is_empty() {
            self.calendars[0].is_default = true;
        }

        self.queue_manifest_save();
        self.worker
            .enqueue_remove(self.calendar_ics_path(&removed.file_name));
        Ok(removed.name)
    }

    /// Inserts or updates an event, handling calendar moves and single-occurrence overrides.
    pub fn upsert_event(
        &mut self,
        old_calendar_id: Option<&str>,
        old_key: Option<&EventKey>,
        mut event: Event,
    ) {
        event.normalize_reminders();
        event.updated_at = Utc::now();

        let target_cal_id = if self.calendar(&event.calendar_id).is_some() {
            event.calendar_id.clone()
        } else {
            let fallback = self.default_calendar_id();
            event.calendar_id = fallback.clone();
            fallback
        };

        let mut moved_from: Option<String> = None;
        if let (Some(prev_cal), Some(prev_key)) = (old_calendar_id, old_key) {
            if prev_cal != target_cal_id {
                let mut moved_overrides = Vec::new();
                let mut removed_any = false;
                if let Some(prev_list) = self.events_by_calendar.get_mut(prev_cal) {
                    let before = prev_list.len();
                    if prev_key.recurrence_id.is_none() {
                        // Moving a master event also moves its overrides.
                        prev_list.retain(|ev| {
                            if ev.uid == prev_key.uid && ev.recurrence_id.is_some() {
                                let mut cloned = ev.clone();
                                cloned.calendar_id = target_cal_id.clone();
                                moved_overrides.push(cloned);
                                false
                            } else {
                                ev.uid != prev_key.uid
                            }
                        });
                    } else {
                        prev_list.retain(|ev| {
                            !(ev.uid == prev_key.uid && ev.recurrence_id == prev_key.recurrence_id)
                        });
                    }
                    removed_any = prev_list.len() != before;
                }
                if !moved_overrides.is_empty() {
                    self.events_by_calendar
                        .entry(target_cal_id.clone())
                        .or_default()
                        .extend(moved_overrides);
                }
                if removed_any {
                    moved_from = Some(prev_cal.to_string());
                }
            }
        }

        let target_list = self
            .events_by_calendar
            .entry(target_cal_id.clone())
            .or_default();

        if let Some(existing) = target_list
            .iter_mut()
            .find(|ev| ev.uid == event.uid && ev.recurrence_id == event.recurrence_id)
        {
            event.sequence = existing.sequence.saturating_add(1);
            event.created_at = existing.created_at;
            *existing = event;
        } else {
            target_list.push(event);
        }

        if let Some(prev_cal) = moved_from {
            self.queue_calendar_save(&prev_cal);
        }
        self.queue_calendar_save(&target_cal_id);
    }

    /// Saves a single-occurrence edit of a recurring series by creating/updating a `RECURRENCE-ID` override.
    pub fn upsert_single_occurrence_override(
        &mut self,
        calendar_id: &str,
        master_uid: &str,
        occurrence_id: DateTime<Utc>,
        mut override_event: Event,
    ) {
        override_event.uid = master_uid.to_string();
        override_event.calendar_id = calendar_id.to_string();
        override_event.recurrence_id = Some(occurrence_id);
        override_event.rrule = None;
        override_event.exdates.clear();
        override_event.rdates.clear();
        self.upsert_event(None, None, override_event);
    }

    /// Deletes an event or recurring occurrence according to `scope`.
    pub fn delete_event(
        &mut self,
        calendar_id: &str,
        key: &EventKey,
        scope: EditScope,
        occurrence_id: Option<DateTime<Utc>>,
    ) -> bool {
        let Some(events) = self.events_by_calendar.get_mut(calendar_id) else {
            return false;
        };

        let mut changed = false;
        match scope {
            EditScope::All => {
                let before = events.len();
                events.retain(|ev| ev.uid != key.uid);
                changed = events.len() != before;
            }
            EditScope::Single => {
                let target_occ = key.recurrence_id.or(occurrence_id);
                let has_master = events
                    .iter()
                    .any(|ev| ev.uid == key.uid && ev.recurrence_id.is_none());

                if let Some(occ) = target_occ {
                    let before = events.len();
                    events.retain(|ev| {
                        !(ev.uid == key.uid && ev.recurrence_id == Some(occ))
                    });
                    if events.len() != before {
                        changed = true;
                    }
                    if has_master {
                        if let Some(master) = events
                            .iter_mut()
                            .find(|ev| ev.uid == key.uid && ev.recurrence_id.is_none())
                        {
                            if master.is_recurring() {
                                master.add_exdate(occ);
                                master.sequence = master.sequence.saturating_add(1);
                                master.updated_at = Utc::now();
                                changed = true;
                            } else {
                                events.retain(|ev| ev.uid != key.uid);
                                changed = true;
                            }
                        }
                    }
                } else {
                    let before = events.len();
                    events.retain(|ev| ev.uid != key.uid);
                    changed = events.len() != before;
                }
            }
        }

        if changed {
            self.queue_calendar_save(calendar_id);
        }
        changed
    }

    /// Parses and imports an `.ics` file into `target_calendar_id`, deduplicating by `(uid, recurrence_id)`.
    pub fn import_ics_file(
        &mut self,
        path: &Path,
        target_calendar_id: &str,
    ) -> Result<ImportSummary, String> {
        let raw = fs::read_to_string(path)
            .map_err(|err| format!("Could not read {}: {err}", path.display()))?;
        let cal_id = if self.calendar(target_calendar_id).is_some() {
            target_calendar_id.to_string()
        } else {
            self.default_calendar_id()
        };

        let parsed = ics::parse_ics(&raw, &cal_id)?;
        let list = self.events_by_calendar.entry(cal_id.clone()).or_default();

        let mut imported = 0usize;
        let mut updated = 0usize;
        for event in parsed.events {
            if let Some(existing) = list
                .iter_mut()
                .find(|e| e.uid == event.uid && e.recurrence_id == event.recurrence_id)
            {
                *existing = event;
                updated += 1;
            } else {
                list.push(event);
                imported += 1;
            }
        }

        if imported > 0 || updated > 0 {
            self.queue_calendar_save(&cal_id);
        }

        Ok(ImportSummary {
            imported,
            updated,
            skipped: parsed.skipped,
        })
    }

    pub fn export_calendar_to_downloads(&self, calendar_id: &str) -> Result<PathBuf, String> {
        let cal = self
            .calendar(calendar_id)
            .ok_or_else(|| "Calendar not found.".to_string())?;
        let events = self.events_for_calendar(calendar_id);
        let content = ics::serialize_calendar(&cal.name, events);
        let dir = resolve_export_dir();
        let dest = unique_export_path(&dir, &cal.name);
        atomic_write_string(&dest, &content)
            .map_err(|err| format!("Could not write {}: {err}", dest.display()))?;
        Ok(dest)
    }

    pub fn export_event_to_downloads(
        &self,
        calendar_id: &str,
        key: &EventKey,
    ) -> Result<PathBuf, String> {
        let cal = self
            .calendar(calendar_id)
            .ok_or_else(|| "Calendar not found.".to_string())?;
        let events = self.events_for_calendar(calendar_id);
        let matching: Vec<Event> = events
            .iter()
            .filter(|ev| ev.uid == key.uid)
            .cloned()
            .collect();
        if matching.is_empty() {
            return Err("Event not found.".to_string());
        }
        let stem = matching
            .iter()
            .find(|ev| ev.recurrence_id == key.recurrence_id)
            .or_else(|| matching.first())
            .map(|ev| ev.summary.as_str())
            .unwrap_or("event");
        let content = ics::serialize_calendar(&cal.name, &matching);
        let dir = resolve_export_dir();
        let dest = unique_export_path(&dir, stem);
        atomic_write_string(&dest, &content)
            .map_err(|err| format!("Could not write {}: {err}", dest.display()))?;
        Ok(dest)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::theme::SemanticToken;
    use chrono::NaiveDate;

    #[test]
    fn bootstraps_persists_reloads_and_deletes_events_atomically() {
        let root = std::env::temp_dir().join(format!(
            "dusky-calendar-store-test-{}",
            uuid::Uuid::new_v4()
        ));
        let _ = fs::remove_dir_all(&root);

        let default_cal_id;
        let ev_key;
        {
            let mut store = CalendarStore::load(Some(&root));
            assert_eq!(store.calendars.len(), 1);
            assert_eq!(store.calendars[0].name, "Personal");
            default_cal_id = store.default_calendar_id();

            let work = store.create_calendar(
                "Work".into(),
                CalendarColor::Token(SemanticToken::ProfileBlue),
            );
            assert_eq!(store.calendars.len(), 2);

            let mut ev = Event::new_all_day(
                &work.id,
                "Release Day",
                NaiveDate::from_ymd_opt(2026, 11, 26).unwrap(),
                NaiveDate::from_ymd_opt(2026, 11, 27).unwrap(),
            );
            ev.location = Some("HQ".into());
            ev_key = ev.key();
            store.upsert_event(None, None, ev);
            store.flush_and_shutdown();
        }

        // Reload fresh from disk and verify everything survived intact.
        let mut reloaded = CalendarStore::load(Some(&root));
        assert_eq!(reloaded.calendars.len(), 2);
        assert_eq!(reloaded.default_calendar_id(), default_cal_id);
        let work_id = reloaded
            .calendars
            .iter()
            .find(|c| c.name == "Work")
            .unwrap()
            .id
            .clone();
        let found = reloaded.find_event(&work_id, &ev_key).unwrap();
        assert_eq!(found.summary, "Release Day");
        assert_eq!(found.location.as_deref(), Some("HQ"));

        reloaded.prefs.window_width = Some(1120.0);
        reloaded.prefs.window_height = Some(740.0);
        reloaded.save_prefs();

        assert!(reloaded.delete_event(&work_id, &ev_key, EditScope::All, None));
        assert!(reloaded.find_event(&work_id, &ev_key).is_none());
        reloaded.flush_and_shutdown();

        assert_eq!(load_saved_window_size(Some(&root)), Some((1120.0, 740.0)));

        let _ = fs::remove_dir_all(&root);
    }
}
