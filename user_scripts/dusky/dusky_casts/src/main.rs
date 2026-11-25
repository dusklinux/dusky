use anyhow::{Context, Result};
use dusky_casts::{
    api::Api,
    db::{self, Db, Episode, Podcast, UpNextSort, n, s},
    library,
    listening::StatsSyncReport,
    player::{Player, TRIMS},
    sync,
};
use iced::{Subscription, Task};
mod icons;
mod theme;
mod ui;
use serde_json::Value;
use std::{
    collections::{HashMap, HashSet, VecDeque},
    path::PathBuf,
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};

pub(crate) const VIRTUAL_OVERSCAN: usize = 4;
const VIRTUAL_STEP: usize = 2;
pub(crate) const DRAWER_OVERSCAN: usize = 4;
const DRAWER_STEP: usize = 2;
const ROW_CACHE_MAX: usize = 320;
const DRAWER_CACHE_MAX: usize = 160;
const ARTWORK_DECODE_DIM: u32 = 180;
const ARTWORK_BATCH: usize = 8;
const SEARCH_DEBOUNCE_MS: u64 = 80;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum ConfirmAction {
    ClearUpNext,
    ClearLocalQueue,
    ClearPlaylist(i64),
    DeleteFolder(i64),
    DeletePlaylist(i64),
    Unsubscribe(i64),
}

#[derive(Clone, Debug)]
pub(crate) struct RowEntry {
    pub episode: Episode,
    pub date_label: String,
    pub duration_label: String,
    pub local_filename: String,
    pub local_stem: String,
    pub local_parent: String,
}

impl RowEntry {
    pub fn from_episode(episode: Episode) -> Self {
        let date_label = chrono::DateTime::from_timestamp(episode.published_at, 0)
            .filter(|_| episode.published_at > 0)
            .map(|d| d.format("%b %-d").to_string())
            .unwrap_or_default();
        let duration_label = format_clock(episode.duration as f64);
        let (local_filename, local_stem, local_parent) = if episode.is_local() {
            let fname = episode
                .description
                .rsplit('/')
                .next()
                .unwrap_or(&episode.description)
                .to_owned();
            let stem = std::path::Path::new(&fname)
                .file_stem()
                .and_then(|v| v.to_str())
                .unwrap_or(&fname)
                .to_owned();
            let parent = episode
                .description
                .rsplit_once('/')
                .map(|(p, _)| p.to_owned())
                .unwrap_or_default();
            (fname, stem, parent)
        } else {
            (String::new(), String::new(), String::new())
        };
        Self {
            episode,
            date_label,
            duration_label,
            local_filename,
            local_stem,
            local_parent,
        }
    }
}

pub(crate) fn format_clock(value: f64) -> String {
    let secs = value.max(0.0) as u64;
    if secs >= 3600 {
        format!("{}:{:02}:{:02}", secs / 3600, (secs / 60) % 60, secs % 60)
    } else {
        format!("{}:{:02}", secs / 60, secs % 60)
    }
}

#[derive(Default, Clone)]
pub(crate) struct AccountViewData {
    pub signed_in: bool,
    pub history: Vec<(String, i64, i64)>, // (day, pc_ms, ios_ms), newest first
    pub total_ms: i64,
    pub today_ms: i64,
    pub ios_total_ms: i64,
    pub ios_today_ms: i64,
    pub week_pc_ms: i64,
    pub week_ios_ms: i64,
    pub month_pc_ms: i64,
    pub month_ios_ms: i64,
    pub ios_synced_at: i64,
    pub stats_summary_at: i64,
    pub own_ms: i64,
    pub other_ms: i64,
    pub stats_error: String,
    pub last_sync_time: i64,
    pub last_sync_error: String,
    pub podcast_count: usize,
    pub pending_sync_count: i64,
}

#[derive(Debug, Clone)]
pub(crate) enum Msg {
    Noop,
    Tick,
    Ipc,
    Focused,
    Resized(iced::Size),
    Escape,
    Scroll(f32, f32, f32),
    DrawerScroll(f32, f32, f32),
    PageScroll(i32),
    Shift(bool),
    Captured(iced::window::Screenshot),
    ArtworkDone(Vec<(String, u32, u32, Vec<u8>)>),
    LocalStatsDone(std::result::Result<StatsSyncReport, String>),
    Nav(String),
    Podcast(i64),
    BackFromPodcast,
    PodcastsGridToggle(bool),
    PodcastsSort(String),
    PodcastsFolderFilter(String),
    PodcastsQuery(String),
    PodcastFilter(String),
    PodcastSort(i64),
    TogglePodcastDesc,
    RefreshPodcast(i64),
    PodcastMarkAllPlayed(i64),
    PodcastArchivePlayed(i64),
    Filter(String),
    ApplyFilter(u64),
    ToggleSelect(i64),
    SelectAll,
    SelectAbove,
    SelectBelow,
    ClearSelection,
    RequestConfirm(ConfirmAction),
    Confirm,
    CancelConfirm,
    ClearPlaylist(i64),
    BulkMoveTop,
    BulkMoveBottom,
    BulkMove(i32),
    BulkRemove,
    BulkQueue(i32),
    BulkPlayed,
    BulkArchive,
    MoveEdge(i64, bool),
    SortUpNext(UpNextSort),
    ToggleQueueDrawer,
    CloseQueueDrawer,
    QueueDrawerContext(bool),
    OpenQueueFullView,
    DrawerMove(i64, i32),
    Play(i64),
    Pause,
    Seek(f64),
    Skip(f64),
    Speed(f64),
    StepSpeed(f64),
    SaveSpeed,
    Volume(f64),
    SaveVolume,
    Trim(String),
    Star(i64),
    Archive(i64),
    Played(i64),
    Queue(i64, i32),
    Move(i64, i32),
    Next,
    Info(i64),
    CloseInfo,
    Email(String),
    Password(String),
    Login,
    Logout,
    Sync,
    Refresh,
    Feed(String),
    AddFeed,
    Discover(String),
    Search,
    Subscribe(usize),
    Download(i64),
    Downloaded(i64, std::result::Result<Outcome, String>),
    CancelDownload(i64),
    Done(std::result::Result<Outcome, String>),
    Close,
    Name(String),
    CreateFolder,
    CreatePlaylist,
    StartRename(bool, i64, String),
    EditRename(String),
    CommitRename,
    CancelRename,
    DeleteFolder(i64),
    DeletePlaylist(i64),
    Assign(i64, String),
    Unsubscribe(i64),
    Membership(i64, i64, bool),
    TogglePlaylistPicker(Option<i64>),
    PlaylistSort(i64, i64),
    PlayAll,
    QueueAllView,
    ToggleAccountAllDays,
    FtpStatsDir(String),
    SaveFtpStatsDir,
    ClearFtpStatsDir,
    SyncLocalStats,
    CheckLocalDrive,
    Sleep(u64),
    TogglePlaybackSettings,
    LocalPath(String),
    ScanLocal,
    ToggleLocalPathEdit,
    CancelLocalPathEdit,
    LocalDisplay(String),
    LocalSort(String),
    ToggleLocalSortDir,
    ToggleAutoQueue,
    QueueAllLocal,
    ClearLocalQueue,
    MediaSkip(i64),
    MediaNext,
    MediaPrev,
    MediaPlay,
    MediaPause,
    ToggleRemainingTime,
}

#[derive(Default)]
struct IpcBridge {
    has_episode: bool,
    paused: bool,
    title: String,
    seq: i64,
    commands: VecDeque<String>,
}

static IPC_PENDING: AtomicBool = AtomicBool::new(false);
static IPC_NOTIFY: OnceLock<Arc<tokio::sync::Notify>> = OnceLock::new();

fn ipc_notify() -> Arc<tokio::sync::Notify> {
    IPC_NOTIFY
        .get_or_init(|| Arc::new(tokio::sync::Notify::new()))
        .clone()
}

fn ipc_stream() -> impl iced::futures::Stream<Item = Msg> {
    iced::stream::channel(
        16,
        |mut output: iced::futures::channel::mpsc::Sender<Msg>| async move {
            use iced::futures::SinkExt;
            let notify = ipc_notify();
            loop {
                notify.notified().await;
                if output.send(Msg::Ipc).await.is_err() {
                    break;
                }
            }
        },
    )
}

fn mpv_ipc_path() -> PathBuf {
    let runtime = std::env::var_os("XDG_RUNTIME_DIR")
        .filter(|p| !p.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(format!("/run/user/{}", unsafe { libc::getuid() })));
    runtime.join(format!("mpv-ipc-{}", std::process::id()))
}

fn handle_ipc_stream(stream: std::os::unix::net::UnixStream, bridge: Arc<Mutex<IpcBridge>>) {
    use std::io::{BufRead, BufReader, Write};
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(1)));
    let mut writer = match stream.try_clone() {
        Ok(w) => w,
        Err(_) => return,
    };
    let reader = BufReader::new(stream);
    for line in reader.lines() {
        let Ok(line) = line else { break };
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if !trimmed.starts_with('{') {
            if let Ok(mut b) = bridge.lock() {
                b.commands.push_back(trimmed.to_owned());
                IPC_PENDING.store(true, Ordering::Release);
                ipc_notify().notify_one();
            }
            continue;
        }
        let Ok(req) = serde_json::from_str::<Value>(trimmed) else {
            continue;
        };
        let req_id = req.get("request_id").cloned().unwrap_or(Value::Null);
        let cmd: Vec<&str> = req["command"]
            .as_array()
            .map(|arr| arr.iter().filter_map(Value::as_str).collect())
            .unwrap_or_default();
        let mut b = match bridge.lock() {
            Ok(guard) => guard,
            Err(_) => break,
        };
        let mut wake_ui = false;
        let data = match cmd.as_slice() {
            ["get_property", "pid"] => Value::from(std::process::id()),
            ["get_property", "idle-active"] => Value::from(!b.has_episode),
            ["get_property", "pause"] => Value::from(b.paused),
            ["get_property", "playlist-count"] => {
                Value::from(if b.has_episode { b.seq + 2 } else { 0 })
            }
            ["get_property", "playlist-pos"] => Value::from(if b.has_episode { b.seq } else { -1 }),
            ["get_property", "user-data/dusky_player/enabled"] => Value::from(true),
            ["get_property", "media-title"] => Value::from(b.title.clone()),
            ["get_property", "playlist"] => serde_json::json!([{"id": 1}]),
            ["get_property", "user-data/osd-router-stop-id"] => Value::from(1),
            ["set_property", "pause", ..] => {
                let arg = req["command"].as_array().and_then(|a| a.get(2));
                let pause = arg
                    .and_then(Value::as_bool)
                    .or_else(|| {
                        arg.and_then(Value::as_str).map(|s| {
                            matches!(s.trim().to_ascii_lowercase().as_str(), "yes" | "true" | "1")
                        })
                    })
                    .unwrap_or(!b.paused);
                b.paused = pause;
                b.commands
                    .push_back(if pause { "pause" } else { "play" }.into());
                wake_ui = true;
                Value::Null
            }
            ["cycle", "pause", ..] => {
                b.paused = !b.paused;
                b.commands.push_back("play-pause".into());
                wake_ui = true;
                Value::Null
            }
            ["script-binding", "dusky_player/media-next"] | ["playlist-next", ..] => {
                b.seq += 1;
                b.commands.push_back("next".into());
                wake_ui = true;
                Value::Null
            }
            ["script-binding", "dusky_player/media-prev"] | ["playlist-prev", ..] => {
                b.seq += 1;
                b.commands.push_back("prev".into());
                wake_ui = true;
                Value::Null
            }
            ["stop", ..] => {
                b.paused = true;
                b.seq += 1;
                b.commands.push_back("pause".into());
                wake_ui = true;
                Value::Null
            }
            _ => Value::Null,
        };
        drop(b);
        if wake_ui {
            IPC_PENDING.store(true, Ordering::Release);
            ipc_notify().notify_one();
        }
        let reply = serde_json::json!({
            "data": data,
            "error": "success",
            "request_id": req_id,
        });
        if writeln!(writer, "{reply}").is_err() {
            break;
        }
    }
}

fn spawn_ipc_listener(path: PathBuf, bridge: Arc<Mutex<IpcBridge>>) -> Option<PathBuf> {
    let _ = std::fs::remove_file(&path);
    let listener = std::os::unix::net::UnixListener::bind(&path).ok()?;
    let _ = std::thread::Builder::new()
        .name("dusky_casts-ipc".into())
        .spawn(move || {
            for stream in listener.incoming().flatten() {
                let bridge = bridge.clone();
                std::thread::spawn(move || handle_ipc_stream(stream, bridge));
            }
        });
    Some(path)
}

#[derive(Debug, Clone)]
pub(crate) enum Outcome {
    Status {
        message: String,
        reload_db: bool,
        stats_report: Option<StatsSyncReport>,
    },
    Search(Vec<Value>),
}

enum Job {
    LocalScan(String),
    Sync,
    Login(String, String),
    Refresh,
    RefreshPodcast(i64),
    Feed(String),
    Search(String),
    Subscribe(Value),
    Download(i64, Arc<library::DownloadControl>),
}

pub(crate) struct App {
    pub(crate) db: Db,
    pub(crate) player: Option<Player>,
    pub(crate) current: Option<Episode>,
    pub(crate) view_ids: Vec<i64>,
    pub(crate) row_cache: HashMap<i64, RowEntry>,
    pub(crate) drawer_ids: Vec<i64>,
    pub(crate) drawer_cache: HashMap<i64, RowEntry>,
    pub(crate) podcasts: Vec<Podcast>,
    pub(crate) folders: Vec<Value>,
    pub(crate) playlists: Vec<Value>,
    pub(crate) nav: String,
    pub(crate) podcast: i64,
    pub(crate) podcast_filter: String,
    pub(crate) podcast_desc_expanded: bool,
    pub(crate) podcasts_grid: bool,
    pub(crate) podcasts_sort: String,
    pub(crate) podcasts_folder: String,
    pub(crate) podcasts_query: String,
    pub(crate) query: String,
    pub(crate) active_query: String,
    filter_gen: u64,
    pub(crate) selected: HashSet<i64>,
    pub(crate) last_selected: Option<i64>,
    pub(crate) shift_held: bool,
    pub(crate) email: String,
    pub(crate) password: String,
    pub(crate) feed: String,
    pub(crate) discover: String,
    pub(crate) results: Vec<Value>,
    pub(crate) name: String,
    pub(crate) editing_item: Option<(bool, i64, String)>,
    pub(crate) confirm_action: Option<ConfirmAction>,
    pub(crate) busy: bool,
    queued_job: Option<Job>,
    pub(crate) downloads: HashMap<i64, Arc<library::DownloadControl>>,
    pending_sync: bool,
    pub(crate) status: String,
    pub(crate) status_is_error: bool,
    pub(crate) speed: f64,
    pub(crate) volume: f64,
    pub(crate) trim: String,
    last_save: Instant,
    last_sync: Instant,
    next_sync: Instant,
    last_focus_stats: Instant,
    last_theme_check: Instant,
    pub(crate) failures: u32,
    pub(crate) info: Option<Episode>,
    pub(crate) info_playlists: HashSet<i64>,
    pub(crate) sleep: Option<Instant>,
    pub(crate) sleep_eof: bool,
    closing: Option<Instant>,
    quit_after: Option<Instant>,
    screenshot: Option<PathBuf>,
    screenshot_at: Instant,
    artwork_busy: bool,
    artwork_tried: HashSet<String>,
    pub(crate) artwork_handles: HashMap<String, iced::widget::image::Handle>,
    pub(crate) appearance: theme::Appearance,
    pub(crate) playback_settings: bool,
    pub(crate) queue_drawer_open: bool,
    pub(crate) queue_drawer_local: bool,
    pub(crate) listening: dusky_casts::listening::Listening,
    pub(crate) local_path: String,
    pub(crate) editing_local_path: bool,
    pub(crate) local_display: String,
    pub(crate) local_sort: String,
    pub(crate) local_sort_asc: bool,
    pub(crate) local_autoqueue: bool,
    pub(crate) media_skip: i64,
    pub(crate) show_remaining: bool,
    pub(crate) view_stats: (usize, f64, Option<f64>),
    pub(crate) queue_summary: (usize, f64, Option<f64>, Option<Episode>),
    pub(crate) cloud_queue_count: usize,
    pub(crate) local_queue_count: usize,
    pub(crate) account_stats: AccountViewData,
    pub(crate) account_show_all_days: bool,
    pub(crate) stats_report: StatsSyncReport,
    pub(crate) ftp_stats_dir_input: String,
    pub(crate) local_drive_locked: bool,
    pub(crate) window_size: iced::Size,
    pub(crate) main_scroll_id: iced::widget::Id,
    pub(crate) drawer_scroll_id: iced::widget::Id,
    pub(crate) window_start: usize,
    pub(crate) scroll_y: f32,
    pub(crate) viewport_height: f32,
    pub(crate) drawer_window_start: usize,
    pub(crate) drawer_scroll_y: f32,
    pub(crate) drawer_viewport_height: f32,
    pub(crate) queued_ids: HashSet<i64>,
    pub(crate) playlist_picker: Option<i64>,
    player_error_latched: bool,
    ipc_bridge: Arc<Mutex<IpcBridge>>,
    ipc_paths: Vec<PathBuf>,
}

impl App {
    fn new(db: Db, quit_after: Option<Duration>) -> Result<Self> {
        db.active(None)?;
        let speed = db
            .get("speed")?
            .parse::<f64>()
            .unwrap_or(1.0)
            .clamp(0.25, 4.0);
        let volume = db
            .get("volume")?
            .parse::<f64>()
            .unwrap_or(0.8)
            .clamp(0.0, 1.5)
            * 100.0;
        let trim = {
            let value = db.get("trim")?;
            if TRIMS.contains(&value.as_str()) {
                value
            } else {
                dusky_casts::player::default_trim()
            }
        };
        let email = db.get("account_email")?;
        let local_path = db.get("local_library_path")?;
        let ftp_stats_dir_input = db.get("ftp_stats_dir")?;
        let editing_local_path = local_path.is_empty();
        let local_display = match db.get("local_display")?.as_str() {
            v @ ("file_title" | "path" | "title" | "title_file") => v.into(),
            "filename" | "title_filename" => "title_file".into(),
            "title_path" => "path".into(),
            _ => "file_title".into(),
        };
        let local_sort = match db.get("local_sort")?.as_str() {
            v @ ("filename" | "path" | "title" | "modified" | "date" | "duration") => v.into(),
            "name" => "title".into(),
            _ => "filename".into(),
        };
        let local_sort_asc = db.get("local_sort_asc")? != "0";
        let local_autoqueue = db.get("local_autoqueue")? != "0";
        let show_remaining = db.get("show_remaining")? == "1";
        let podcasts_grid = db.get("podcasts_grid")? != "0";
        let podcasts_sort = match db.get("podcasts_sort")?.as_str() {
            v @ ("sort_order" | "title" | "latest" | "added") => v.into(),
            _ => "sort_order".into(),
        };
        let media_skip = match db.get("media_skip_secs")?.parse::<i64>().unwrap_or(15) {
            v @ (0 | 15 | 30 | 45 | 60) => v,
            _ => 15,
        };
        let ipc_bridge = Arc::new(Mutex::new(IpcBridge {
            paused: true,
            ..Default::default()
        }));
        let mut ipc_paths = Vec::new();
        if let Some(p) = spawn_ipc_listener(db.path.with_extension("sock"), ipc_bridge.clone()) {
            ipc_paths.push(p);
        }
        if db.path == db::default_path()
            && let Some(p) = spawn_ipc_listener(mpv_ipc_path(), ipc_bridge.clone())
        {
            ipc_paths.push(p);
        }
        let current = db
            .number("last_episode_id")
            .ok()
            .filter(|id| *id > 0)
            .and_then(|id| db.episode(id).ok());
        let signed_in = !db.get("account_token")?.is_empty();
        let local_drive_locked =
            !local_path.is_empty() && !db.is_local_drive_accessible(&local_path);
        let (nav, podcast) = if let Ok(view) = std::env::var("DUSKY_CASTS_VIEW") {
            (view, 0)
        } else {
            let saved_nav = db.get("last_nav")?;
            let saved_pod = db.number("last_podcast").unwrap_or(0);
            if !saved_nav.is_empty() {
                (saved_nav, saved_pod)
            } else if current.as_ref().is_some_and(Episode::is_local) {
                ("Local queue".into(), 0)
            } else {
                ("All episodes".into(), 0)
            }
        };
        let queue_drawer_local = current
            .as_ref()
            .map(Episode::is_local)
            .unwrap_or(matches!(nav.as_str(), "Local files" | "Local queue"));
        let mut app = Self {
            db,
            player: None,
            current,
            view_ids: Vec::new(),
            row_cache: HashMap::new(),
            drawer_ids: Vec::new(),
            drawer_cache: HashMap::new(),
            podcasts: Vec::new(),
            folders: Vec::new(),
            playlists: Vec::new(),
            nav,
            podcast,
            podcast_filter: "Active".into(),
            podcast_desc_expanded: false,
            podcasts_grid,
            podcasts_sort,
            podcasts_folder: "all".into(),
            podcasts_query: String::new(),
            query: String::new(),
            active_query: String::new(),
            filter_gen: 0,
            selected: HashSet::new(),
            last_selected: None,
            shift_held: false,
            email,
            password: String::new(),
            feed: String::new(),
            discover: String::new(),
            results: Vec::new(),
            name: String::new(),
            editing_item: None,
            confirm_action: None,
            busy: false,
            queued_job: None,
            downloads: HashMap::new(),
            pending_sync: false,
            status: "Ready".into(),
            status_is_error: false,
            speed,
            volume,
            trim,
            last_save: Instant::now(),
            last_sync: Instant::now(),
            next_sync: Instant::now() + Duration::from_secs(if signed_in { 2 } else { 300 }),
            last_focus_stats: Instant::now() - Duration::from_secs(60),
            last_theme_check: Instant::now(),
            failures: 0,
            info: None,
            info_playlists: HashSet::new(),
            sleep: None,
            sleep_eof: false,
            closing: None,
            quit_after: quit_after.map(|d| Instant::now() + d),
            screenshot: std::env::var_os("DUSKY_CASTS_SCREENSHOT").map(PathBuf::from),
            screenshot_at: Instant::now()
                + Duration::from_millis(
                    std::env::var("DUSKY_CASTS_SCREENSHOT_DELAY")
                        .ok()
                        .and_then(|s| s.parse().ok())
                        .unwrap_or(2000),
                ),
            artwork_busy: false,
            artwork_tried: HashSet::new(),
            artwork_handles: HashMap::new(),
            appearance: theme::Appearance::load(),
            playback_settings: false,
            queue_drawer_open: false,
            queue_drawer_local,
            listening: Default::default(),
            local_path,
            editing_local_path,
            local_display,
            local_sort,
            local_sort_asc,
            local_autoqueue,
            media_skip,
            show_remaining,
            view_stats: (0, 0.0, None),
            queue_summary: (0, 0.0, None, None),
            cloud_queue_count: 0,
            local_queue_count: 0,
            account_stats: AccountViewData::default(),
            account_show_all_days: false,
            stats_report: StatsSyncReport::default(),
            ftp_stats_dir_input,
            local_drive_locked,
            window_size: iced::Size::new(1200.0, 850.0),
            main_scroll_id: iced::widget::Id::unique(),
            drawer_scroll_id: iced::widget::Id::unique(),
            window_start: 0,
            scroll_y: 0.0,
            viewport_height: 700.0,
            drawer_window_start: 0,
            drawer_scroll_y: 0.0,
            drawer_viewport_height: 360.0,
            queued_ids: HashSet::new(),
            playlist_picker: None,
            player_error_latched: false,
            ipc_bridge,
            ipc_paths,
        };
        app.reload()?;
        app.validate_nav();
        Ok(app)
    }

    fn boot_task(&mut self) -> Task<Msg> {
        let stats_task = self.spawn_local_stats_sync();
        let art_task = self.maybe_fetch_artwork();
        Task::batch([stats_task, art_task])
    }

    fn validate_nav(&mut self) {
        let mut changed = false;
        if self.podcast > 0 && !self.podcasts.iter().any(|p| p.id == self.podcast) {
            self.podcast = 0;
            self.nav = "Podcasts".into();
            changed = true;
        }
        if self.nav == "Podcast" && self.podcast == 0 {
            self.nav = "Podcasts".into();
            changed = true;
        }
        if let Some(uuid) = self.nav.strip_prefix("folder:")
            && !self.folders.iter().any(|f| s(f, "uuid") == uuid)
        {
            self.nav = "Podcasts".into();
            changed = true;
        }
        if let Some(pid) = self
            .nav
            .strip_prefix("playlist:")
            .and_then(|v| v.parse::<i64>().ok())
            && !self.playlists.iter().any(|p| n(p, "id") == pid)
        {
            self.nav = "Podcasts".into();
            changed = true;
        }
        if changed {
            let _ = self.reload_episodes();
        }
    }

    pub(crate) fn is_compact(&self) -> bool {
        self.window_size.width < 850.0
    }

    pub(crate) fn is_dense(&self) -> bool {
        self.window_size.height < 780.0
    }

    pub(crate) fn main_row_layout(&self) -> (f32, f32) {
        let dense = self.is_dense();
        let is_local = matches!(self.nav.as_str(), "Local files" | "Local queue");
        let gap = if dense { 3.0 } else { 4.0 };
        let height = if is_local {
            if dense { 44.0 } else { 50.0 }
        } else if dense {
            48.0
        } else {
            56.0
        };
        (height, gap)
    }

    pub(crate) fn drawer_row_layout() -> (f32, f32) {
        (46.0, 4.0)
    }

    pub(crate) fn selected_podcast(&self) -> Option<&Podcast> {
        (self.podcast > 0)
            .then(|| self.podcasts.iter().find(|p| p.id == self.podcast))
            .flatten()
    }

    pub(crate) fn first_episode(&self) -> Option<Episode> {
        let id = *self.view_ids.first()?;
        self.row_cache
            .get(&id)
            .map(|entry| entry.episode.clone())
            .or_else(|| self.db.episode(id).ok())
    }

    fn sample_listening(&mut self) {
        let state = self.player.as_ref().map(|p| p.state());
        self.listening.sample(
            Instant::now(),
            chrono::Local::now(),
            state
                .filter(|s| s.loaded && !s.paused && !s.buffering && !s.eof && s.error.is_none())
                .and_then(|s| {
                    self.current
                        .as_ref()
                        .map(|e| (e.id, s.generation, s.position))
                }),
        );
    }

    fn sync_ipc(&self) {
        if let Ok(mut b) = self.ipc_bridge.lock() {
            b.has_episode = self.current.is_some() || !self.view_ids.is_empty();
            b.paused = self
                .player
                .as_ref()
                .map(|p| p.state().paused)
                .unwrap_or(true);
            b.title = self
                .current
                .as_ref()
                .map(|e| e.title.clone())
                .or_else(|| {
                    self.view_ids
                        .first()
                        .and_then(|id| self.row_cache.get(id))
                        .map(|r| r.episode.title.clone())
                })
                .unwrap_or_default();
        }
    }

    fn refresh_info_playlists(&mut self) {
        self.info_playlists.clear();
        let Some(ep) = &self.info else { return };
        if let Ok(mut stmt) = self
            .db
            .conn
            .prepare("SELECT playlist_id FROM playlist_episodes WHERE episode_uuid=?")
            && let Ok(rows) = stmt.query_map([&ep.uuid], |r| r.get::<_, i64>(0))
        {
            for id in rows.flatten() {
                self.info_playlists.insert(id);
            }
        }
    }

    fn refresh_account_stats(&mut self) {
        let now_local = chrono::Local::now();
        let today = now_local.format("%Y-%m-%d").to_string();
        let week_cutoff = (now_local - chrono::Duration::days(6))
            .format("%Y-%m-%d")
            .to_string();
        let month_cutoff = (now_local - chrono::Duration::days(29))
            .format("%Y-%m-%d")
            .to_string();
        let signed_in = !self.db.get("account_token").unwrap_or_default().is_empty();

        let pc_days = self.db.listening_days_all().unwrap_or_default();
        let ios_days = self.db.ios_listening_days_all().unwrap_or_default();

        let mut merged: std::collections::BTreeMap<String, (i64, i64)> =
            std::collections::BTreeMap::new();
        let (mut total_ms, mut today_ms, mut week_pc_ms, mut month_pc_ms) =
            (0i64, 0i64, 0i64, 0i64);
        for (day, ms) in pc_days {
            total_ms = total_ms.saturating_add(ms);
            if day == today {
                today_ms = today_ms.saturating_add(ms);
            }
            if day >= week_cutoff {
                week_pc_ms = week_pc_ms.saturating_add(ms);
            }
            if day >= month_cutoff {
                month_pc_ms = month_pc_ms.saturating_add(ms);
            }
            merged.entry(day).or_default().0 = ms;
        }
        let (mut ios_total_ms, mut ios_today_ms, mut week_ios_ms, mut month_ios_ms) =
            (0i64, 0i64, 0i64, 0i64);
        for (day, ms) in ios_days {
            ios_total_ms = ios_total_ms.saturating_add(ms);
            if day == today {
                ios_today_ms = ios_today_ms.saturating_add(ms);
            }
            if day >= week_cutoff {
                week_ios_ms = week_ios_ms.saturating_add(ms);
            }
            if day >= month_cutoff {
                month_ios_ms = month_ios_ms.saturating_add(ms);
            }
            merged.entry(day).or_default().1 = ms;
        }
        let history: Vec<(String, i64, i64)> = merged
            .into_iter()
            .rev()
            .map(|(day, (pc, ios))| (day, pc, ios))
            .collect();

        let ios_synced_at = self.db.number("ios_stats_synced_at").unwrap_or(0);
        let stats_summary_at = self.db.number("stats_summary_at").unwrap_or(0);
        let own_ms = dusky_casts::stats::total_ms(&self.db).unwrap_or(total_ms);
        let other_ms = self
            .db
            .number("stats_other_seconds")
            .unwrap_or(0)
            .saturating_mul(1000);
        let stats_error = self.db.get("stats_error").unwrap_or_default();
        let last_sync_time = self.db.number("last_sync_time").unwrap_or(0);
        let last_sync_error = self.db.get("last_sync_error").unwrap_or_default();
        let pending_sync_count: i64 = self
            .db
            .conn
            .query_row(
                "SELECT
                    (SELECT count(*) FROM episodes e JOIN podcasts p ON p.id=e.podcast_id WHERE e.needs_sync=1 AND p.feed_url LIKE 'pcast://%') +
                    (SELECT count(*) FROM podcasts WHERE needs_sync=1 AND feed_url LIKE 'pcast://%') +
                    (SELECT count(*) FROM folders WHERE needs_sync=1) +
                    (SELECT count(*) FROM playlists WHERE needs_sync=1) +
                    (SELECT count(*) FROM podcast_tombstones) +
                    (SELECT count(*) FROM queue_actions)",
                [],
                |r| r.get(0),
            )
            .unwrap_or(0);

        self.account_stats = AccountViewData {
            signed_in,
            history,
            total_ms,
            today_ms,
            ios_total_ms,
            ios_today_ms,
            week_pc_ms,
            week_ios_ms,
            month_pc_ms,
            month_ios_ms,
            ios_synced_at,
            stats_summary_at,
            own_ms,
            other_ms,
            stats_error,
            last_sync_time,
            last_sync_error,
            podcast_count: self.podcasts.len(),
            pending_sync_count,
        };
    }

    fn refresh_summaries(&mut self) {
        let active_id = self.current.as_ref().map(|episode| episode.id);
        self.view_stats = if self.podcast > 0 {
            self.db
                .podcast_view_stats(
                    self.podcast,
                    &self.podcast_filter,
                    &self.active_query,
                    active_id,
                )
                .unwrap_or((0, 0.0, None))
        } else if matches!(
            self.nav.as_str(),
            "Podcasts" | "Account" | "Discover" | "Organize"
        ) {
            (0, 0.0, None)
        } else {
            self.db
                .view_stats(&self.nav, &self.active_query, 0, active_id)
                .unwrap_or((0, 0.0, None))
        };
        let is_local_context = self
            .current
            .as_ref()
            .map(Episode::is_local)
            .unwrap_or(matches!(self.nav.as_str(), "Local files" | "Local queue"));
        self.queue_summary = self
            .db
            .queue_summary(is_local_context, active_id)
            .unwrap_or((0, 0.0, None, None));
        self.cloud_queue_count = self
            .db
            .conn
            .query_row("SELECT count(*) FROM queue", [], |r| r.get::<_, i64>(0))
            .unwrap_or(0)
            .max(0) as usize;
        self.local_queue_count = self
            .db
            .conn
            .query_row(
                "SELECT count(*) FROM local_queue q JOIN local_files f ON f.episode_id=q.episode_id WHERE f.available=1",
                [],
                |r| r.get::<_, i64>(0),
            )
            .unwrap_or(0)
            .max(0) as usize;
        self.queued_ids = self.db.queued_ids().unwrap_or_default();
    }

    fn ensure_window_cached(&mut self) {
        let total = self.view_ids.len();
        if total == 0 {
            self.row_cache.clear();
            return;
        }
        let (row_h, row_gap) = self.main_row_layout();
        let row_stride = (row_h + row_gap).max(1.0);
        let capacity = ((self.viewport_height / row_stride).ceil() as usize)
            .saturating_add(VIRTUAL_OVERSCAN * 2 + 4);
        let start = self.window_start.min(total);
        let end = (start + capacity).min(total);

        let missing: Vec<i64> = self.view_ids[start..end]
            .iter()
            .copied()
            .filter(|id| !self.row_cache.contains_key(id))
            .collect();
        if !missing.is_empty()
            && let Ok(episodes) = self.db.episodes_by_ids(&missing)
        {
            for ep in episodes {
                let id = ep.id;
                self.row_cache.insert(id, RowEntry::from_episode(ep));
            }
        }
        if self.row_cache.len() > ROW_CACHE_MAX {
            let keep_start = start.saturating_sub(80);
            let keep_end = (end + 80).min(total);
            let keep: HashSet<i64> = self.view_ids[keep_start..keep_end]
                .iter()
                .copied()
                .collect();
            self.row_cache.retain(|id, _| keep.contains(id));
        }
    }

    fn ensure_drawer_cached(&mut self) {
        let total = self.drawer_ids.len();
        if total == 0 {
            self.drawer_cache.clear();
            return;
        }
        let (row_h, row_gap) = Self::drawer_row_layout();
        let row_stride = (row_h + row_gap).max(1.0);
        let capacity = ((self.drawer_viewport_height / row_stride).ceil() as usize)
            .saturating_add(DRAWER_OVERSCAN * 2 + 4);
        let start = self.drawer_window_start.min(total);
        let end = (start + capacity).min(total);

        let missing: Vec<i64> = self.drawer_ids[start..end]
            .iter()
            .copied()
            .filter(|id| !self.drawer_cache.contains_key(id))
            .collect();
        if !missing.is_empty()
            && let Ok(episodes) = self.db.episodes_by_ids(&missing)
        {
            for ep in episodes {
                let id = ep.id;
                self.drawer_cache.insert(id, RowEntry::from_episode(ep));
            }
        }
        if self.drawer_cache.len() > DRAWER_CACHE_MAX {
            let keep_start = start.saturating_sub(40);
            let keep_end = (end + 40).min(total);
            let keep: HashSet<i64> = self.drawer_ids[keep_start..keep_end]
                .iter()
                .copied()
                .collect();
            self.drawer_cache.retain(|id, _| keep.contains(id));
        }
    }

    fn update_cached_episode(&mut self, id: i64, mut f: impl FnMut(&mut Episode)) {
        if let Some(entry) = self.row_cache.get_mut(&id) {
            f(&mut entry.episode);
        }
        if let Some(entry) = self.drawer_cache.get_mut(&id) {
            f(&mut entry.episode);
        }
        if let Some(cur) = &mut self.current
            && cur.id == id
        {
            f(cur);
        }
        if let Some(inf) = &mut self.info
            && inf.id == id
        {
            f(inf);
        }
    }

    fn maybe_fetch_artwork(&mut self) -> Task<Msg> {
        if self.artwork_busy || self.closing.is_some() {
            return Task::none();
        }
        let mut batch: Vec<(String, String)> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();

        for p in &self.podcasts {
            if batch.len() >= ARTWORK_BATCH {
                break;
            }
            if !p.uuid.is_empty()
                && !self.artwork_handles.contains_key(&p.uuid)
                && !self.artwork_tried.contains(&p.uuid)
                && seen.insert(p.uuid.clone())
            {
                batch.push((p.uuid.clone(), p.artwork_url.clone()));
            }
        }
        if batch.len() < ARTWORK_BATCH
            && let Some(cur) = &self.current
            && !cur.podcast_uuid.is_empty()
            && !cur.is_local()
            && !self.artwork_handles.contains_key(&cur.podcast_uuid)
            && !self.artwork_tried.contains(&cur.podcast_uuid)
            && seen.insert(cur.podcast_uuid.clone())
        {
            let url = self
                .podcasts
                .iter()
                .find(|p| p.uuid == cur.podcast_uuid)
                .map(|p| p.artwork_url.clone())
                .unwrap_or_default();
            batch.push((cur.podcast_uuid.clone(), url));
        }
        if batch.is_empty() {
            return Task::none();
        }
        for (uuid, _) in &batch {
            self.artwork_tried.insert(uuid.clone());
        }
        self.artwork_busy = true;
        Task::perform(run_artwork_job(batch), Msg::ArtworkDone)
    }

    fn spawn_local_stats_sync(&mut self) -> Task<Msg> {
        let _ = self.listening.flush(&mut self.db);
        let path = self.db.path.clone();
        Task::perform(run_local_stats_job(path), Msg::LocalStatsDone)
    }

    fn reload_drawer(&mut self) {
        let mut ids = self
            .db
            .queue_for(self.queue_drawer_local)
            .unwrap_or_default();
        if let Some(cur) = &self.current
            && cur.is_local() == self.queue_drawer_local
        {
            ids.retain(|&id| id != cur.id);
        }
        self.drawer_ids = ids;
        self.drawer_cache.clear();
        if self.drawer_window_start >= self.drawer_ids.len() {
            self.drawer_window_start = 0;
        }
        if self.queue_drawer_open {
            self.ensure_drawer_cached();
        }
    }

    fn reload_episodes(&mut self) -> Result<()> {
        self.row_cache.clear();
        self.view_ids = if self.podcast > 0 {
            let sort = self
                .selected_podcast()
                .map(|p| p.episodes_sort)
                .unwrap_or(0);
            self.db.podcast_episode_ids(
                self.podcast,
                &self.podcast_filter,
                &self.active_query,
                sort,
            )?
        } else if matches!(
            self.nav.as_str(),
            "Podcasts" | "Account" | "Discover" | "Organize"
        ) {
            Vec::new()
        } else {
            self.db.view_ids(&self.nav, &self.active_query, 0)?
        };
        if self.window_start >= self.view_ids.len() {
            self.window_start = 0;
        }
        self.ensure_window_cached();
        self.refresh_summaries();
        self.refresh_info_playlists();
        if self.queue_drawer_open {
            self.reload_drawer();
        }
        if !self.selected.is_empty() {
            let valid: HashSet<i64> = self.view_ids.iter().copied().collect();
            self.selected.retain(|id| valid.contains(id));
            if self.last_selected.is_some_and(|id| !valid.contains(&id)) {
                self.last_selected = None;
            }
        }
        self.sync_ipc();
        Ok(())
    }

    fn reload(&mut self) -> Result<()> {
        self.db.refresh_local_sort_cache();
        if !self.local_path.is_empty() {
            self.local_drive_locked = !self.db.is_local_drive_accessible(&self.local_path);
        }
        self.podcasts = self.db.podcast_summaries()?;
        self.folders = self
            .db
            .rows("SELECT * FROM folders WHERE is_deleted=0 ORDER BY sort_position,name")?;
        self.playlists = self
            .db
            .rows("SELECT * FROM playlists WHERE is_deleted=0 ORDER BY sort_position,title")?;
        self.refresh_account_stats();
        self.reload_episodes()
    }

    fn task(&mut self, job: Job) -> Task<Msg> {
        if self.busy {
            if matches!(job, Job::Sync) {
                self.pending_sync = true;
            } else {
                self.queued_job = Some(job);
            }
            return Task::none();
        }
        self.busy = true;
        self.status = "Working…".into();
        self.status_is_error = false;
        let path = self.db.path.clone();
        Task::perform(run_job(path, job), Msg::Done)
    }

    fn request_sync(&mut self) -> Task<Msg> {
        if let Err(e) = self.listening.flush(&mut self.db) {
            self.status = format!("Saving listening statistics: {e:#}");
            return Task::none();
        }
        self.last_sync = Instant::now();
        if self.db.get("account_token").unwrap_or_default().is_empty() {
            self.next_sync = self.last_sync + Duration::from_secs(300);
            return self.spawn_local_stats_sync();
        }
        if self.busy {
            self.pending_sync = true;
            Task::none()
        } else {
            self.pending_sync = false;
            self.task(Job::Sync)
        }
    }

    fn save(&mut self) -> Result<()> {
        let had_pending = !self.listening.pending().is_empty();
        self.listening.flush(&mut self.db)?;
        if had_pending && self.nav == "Account" {
            self.refresh_account_stats();
        }
        if let Some(e) = &mut self.current {
            self.db.set("last_episode_id", &e.id.to_string())?;
            if let Some(p) = &self.player {
                let state = p.state();
                if state.loaded || state.eof {
                    e.position = (state.position.max(0.0) * 1000.0) as i64;
                    if state.duration > 0.0 {
                        e.duration = state.duration as i64;
                    }
                    self.db.progress(e.id, state.position, state.duration)?;
                }
            }
        }
        self.last_save = Instant::now();
        Ok(())
    }

    fn play(&mut self, id: i64) -> Result<()> {
        self.sample_listening();
        self.save()?;
        self.player_error_latched = false;
        let e = self.db.episode(id)?;
        if e.is_local() {
            let url = reqwest::Url::parse(&e.audio_url)?;
            let path = url
                .to_file_path()
                .map_err(|_| anyhow::anyhow!("Invalid local file path"))?;
            if !path.is_file() {
                self.local_drive_locked = true;
                self.current = Some(e);
                self.db.set("last_episode_id", &id.to_string())?;
                anyhow::bail!(
                    "Encrypted media drive is locked or unmounted — queue and progress retained"
                );
            }
            self.local_drive_locked = false;
        }
        if self
            .player
            .as_mut()
            .is_some_and(|p| !p.is_running() || p.state().error.is_some())
        {
            self.player.take();
        }
        if self.player.is_none() {
            self.player = Some(Player::start(
                &self.trim,
                self.speed,
                self.volume,
                std::env::var("DUSKY_CASTS_SILENT").as_deref() == Ok("1"),
            )?);
        }
        let uri = if !e.downloaded_path.is_empty()
            && std::path::Path::new(&e.downloaded_path).is_file()
        {
            &e.downloaded_path
        } else {
            &e.audio_url
        };
        anyhow::ensure!(!uri.is_empty(), "Episode has no playable URL");
        self.db.active(Some(id))?;
        self.db.set("last_episode_id", &id.to_string())?;
        let mut resume = if e.playing_status == 1 || (e.playing_status == 0 && e.position > 0) {
            e.position as f64 / 1000.0
        } else {
            0.0
        };
        if e.duration > 0 && resume >= e.duration as f64 - 1.0 {
            resume = (e.duration as f64 - 5.0).max(0.0);
        }
        self.player.as_mut().unwrap().load(uri, resume, false)?;
        self.db.progress(id, resume, e.duration as f64)?;
        self.db.change(id, "playing_status", 1)?;
        self.db.edit_queue(id, 1)?;
        self.queue_drawer_local = e.is_local();
        self.current = Some(e);
        self.next_sync = Instant::now() + Duration::from_secs(20);
        Ok(())
    }

    fn next(&mut self) -> Result<()> {
        let local = self
            .current
            .as_ref()
            .map(Episode::is_local)
            .unwrap_or(matches!(self.nav.as_str(), "Local files" | "Local queue"));
        if local
            && !self.local_path.is_empty()
            && !self.db.is_local_drive_accessible(&self.local_path)
        {
            self.local_drive_locked = true;
            anyhow::bail!(
                "Encrypted media drive is locked or unmounted — queue and progress retained"
            );
        }
        let queue = self.db.queue_for(local)?;
        let current = self.current.as_ref().map(|e| e.id);
        if let Some(current) = current {
            self.db.edit_queue(current, 4)?;
        }
        self.current = None;
        if let Some(id) = queue.into_iter().find(|id| Some(*id) != current) {
            self.play(id)?;
        } else {
            self.db.active(None)?;
            self.current = None;
        }
        Ok(())
    }

    fn reset_main_scroll(&mut self) -> Task<Msg> {
        self.window_start = 0;
        self.scroll_y = 0.0;
        self.ensure_window_cached();
        iced::widget::operation::snap_to(
            self.main_scroll_id.clone(),
            iced::widget::scrollable::RelativeOffset::START,
        )
    }

    fn reset_drawer_scroll(&mut self) -> Task<Msg> {
        self.drawer_window_start = 0;
        self.drawer_scroll_y = 0.0;
        self.ensure_drawer_cached();
        iced::widget::operation::snap_to(
            self.drawer_scroll_id.clone(),
            iced::widget::scrollable::RelativeOffset::START,
        )
    }

    fn scroll_main_by(&mut self, pages: f32) -> Task<Msg> {
        if self.view_ids.is_empty() {
            return Task::none();
        }
        let (row_h, row_gap) = self.main_row_layout();
        let row_stride = (row_h + row_gap).max(1.0);
        let max_y = (self.view_ids.len() as f32 * row_stride - self.viewport_height).max(0.0);
        let delta = (self.viewport_height * 0.85).max(row_stride * 4.0) * pages;
        let next_y = (self.scroll_y + delta).clamp(0.0, max_y);
        self.scroll_y = next_y;
        self.window_start =
            ((next_y / row_stride).floor() as usize).saturating_sub(VIRTUAL_OVERSCAN);
        self.ensure_window_cached();
        iced::widget::operation::scroll_to(
            self.main_scroll_id.clone(),
            iced::widget::scrollable::AbsoluteOffset {
                x: Some(0.0),
                y: Some(next_y),
            },
        )
    }

    fn scroll_main_edge(&mut self, to_end: bool) -> Task<Msg> {
        if self.view_ids.is_empty() {
            return Task::none();
        }
        if !to_end {
            return self.reset_main_scroll();
        }
        let (row_h, row_gap) = self.main_row_layout();
        let row_stride = (row_h + row_gap).max(1.0);
        let max_y = (self.view_ids.len() as f32 * row_stride - self.viewport_height).max(0.0);
        self.scroll_y = max_y;
        self.window_start =
            ((max_y / row_stride).floor() as usize).saturating_sub(VIRTUAL_OVERSCAN);
        self.ensure_window_cached();
        iced::widget::operation::snap_to(
            self.main_scroll_id.clone(),
            iced::widget::scrollable::RelativeOffset::END,
        )
    }
}

impl App {
    fn handle(&mut self, msg: Msg) -> Result<Task<Msg>> {
        if matches!(
            msg,
            Msg::Tick
                | Msg::Close
                | Msg::Pause
                | Msg::MediaPause
                | Msg::Seek(_)
                | Msg::Skip(_)
                | Msg::Next
        ) {
            self.sample_listening();
        }
        let mut sync_needed = false;
        let mut reload_all = false;
        let mut reload_eps = false;
        let mut reload_summaries = false;
        match msg {
            Msg::Noop => return Ok(Task::none()),
            Msg::Resized(size) => {
                self.window_size = size;
                self.viewport_height = (size.height - 170.0).max(240.0);
                self.drawer_viewport_height = (size.height * 0.55).clamp(200.0, 520.0);
                self.ensure_window_cached();
                if self.queue_drawer_open {
                    self.ensure_drawer_cached();
                }
                return Ok(Task::none());
            }
            Msg::Escape => {
                if self.confirm_action.take().is_some() {
                    return Ok(Task::none());
                }
                if self.editing_item.take().is_some() {
                    return Ok(Task::none());
                }
                if self.playlist_picker.take().is_some() {
                    return Ok(Task::none());
                }
                if self.queue_drawer_open {
                    self.queue_drawer_open = false;
                    return Ok(Task::none());
                }
                if self.playback_settings {
                    self.playback_settings = false;
                    return Ok(Task::none());
                }
                if self.info.take().is_some() {
                    self.info_playlists.clear();
                    return Ok(Task::none());
                }
                if !self.selected.is_empty() {
                    self.selected.clear();
                    self.last_selected = None;
                    return Ok(Task::none());
                }
                if self.podcast > 0 {
                    return self.handle(Msg::BackFromPodcast);
                }
                return Ok(Task::none());
            }
            Msg::PageScroll(dir) => {
                return Ok(match dir {
                    -2 => self.scroll_main_edge(false),
                    2 => self.scroll_main_edge(true),
                    -1 => self.scroll_main_by(-1.0),
                    1 => self.scroll_main_by(1.0),
                    _ => Task::none(),
                });
            }
            Msg::LocalPath(value) => {
                self.local_path = value;
                return Ok(Task::none());
            }
            Msg::ScanLocal => return Ok(self.task(Job::LocalScan(self.local_path.clone()))),
            Msg::ToggleLocalPathEdit => {
                self.editing_local_path = true;
                return Ok(Task::none());
            }
            Msg::CancelLocalPathEdit => {
                self.local_path = self.db.get("local_library_path")?;
                self.editing_local_path = false;
                return Ok(Task::none());
            }
            Msg::TogglePlaybackSettings => {
                self.playback_settings = !self.playback_settings;
                if self.playback_settings {
                    self.queue_drawer_open = false;
                }
                return Ok(Task::none());
            }
            Msg::ToggleQueueDrawer => {
                self.queue_drawer_open = !self.queue_drawer_open;
                self.confirm_action = None;
                if self.queue_drawer_open {
                    self.playback_settings = false;
                    self.queue_drawer_local = self
                        .current
                        .as_ref()
                        .map(Episode::is_local)
                        .unwrap_or_else(|| {
                            if matches!(self.nav.as_str(), "Local files" | "Local queue") {
                                true
                            } else if self.nav == "Up Next" {
                                false
                            } else {
                                self.cloud_queue_count == 0 && self.local_queue_count > 0
                            }
                        });
                    self.reload_drawer();
                    return Ok(self.reset_drawer_scroll());
                }
                return Ok(Task::none());
            }
            Msg::CloseQueueDrawer => {
                self.queue_drawer_open = false;
                self.confirm_action = None;
                return Ok(Task::none());
            }
            Msg::QueueDrawerContext(local) => {
                self.queue_drawer_local = local;
                self.confirm_action = None;
                self.reload_drawer();
                return Ok(self.reset_drawer_scroll());
            }
            Msg::OpenQueueFullView => {
                let target = if self.queue_drawer_local {
                    "Local queue"
                } else {
                    "Up Next"
                };
                self.queue_drawer_open = false;
                return self.handle(Msg::Nav(target.into()));
            }
            Msg::DrawerMove(id, delta) => {
                let is_local = self.db.episode(id)?.is_local();
                let mut ids = self.db.queue_for(is_local)?;
                let pinned_head = self
                    .current
                    .as_ref()
                    .is_some_and(|c| c.is_local() == is_local && ids.first() == Some(&c.id));
                let min_idx = usize::from(pinned_head);
                if let Some(index) = ids.iter().position(|i| *i == id)
                    && index >= min_idx
                    && ids.len() > min_idx
                {
                    let target =
                        (index as i32 + delta).clamp(min_idx as i32, ids.len() as i32 - 1) as usize;
                    if target != index {
                        ids.swap(index, target);
                        self.db.reorder_queue(&ids)?;
                        sync_needed = !is_local;
                    }
                }
                if matches!(self.nav.as_str(), "Up Next" | "Local queue") {
                    reload_eps = true;
                } else {
                    reload_summaries = true;
                }
            }
            Msg::LocalDisplay(value) => {
                self.local_display = value;
                self.db.set("local_display", &self.local_display)?;
                return Ok(Task::none());
            }
            Msg::LocalSort(value) => {
                self.local_sort = value;
                self.db.set("local_sort", &self.local_sort)?;
                if self.nav == "Local queue" {
                    self.db.sort_local_queue()?;
                }
                self.reload_episodes()?;
                return Ok(self.reset_main_scroll());
            }
            Msg::ToggleLocalSortDir => {
                self.local_sort_asc = !self.local_sort_asc;
                self.db.set(
                    "local_sort_asc",
                    if self.local_sort_asc { "1" } else { "0" },
                )?;
                if self.nav == "Local queue" {
                    self.db.sort_local_queue()?;
                }
                self.reload_episodes()?;
                return Ok(self.reset_main_scroll());
            }
            Msg::ToggleAutoQueue => {
                self.local_autoqueue = !self.local_autoqueue;
                self.db.set(
                    "local_autoqueue",
                    if self.local_autoqueue { "1" } else { "0" },
                )?;
                return Ok(Task::none());
            }
            Msg::QueueAllLocal => {
                let ids = self.db.local_all_ids(&self.active_query)?;
                self.db.set_local_queue(&ids)?;
                reload_eps = true;
            }
            Msg::ClearLocalQueue => {
                self.db.set_local_queue(&[])?;
                self.selected.clear();
                self.last_selected = None;
                self.confirm_action = None;
                reload_eps = true;
            }
            Msg::ToggleRemainingTime => {
                self.show_remaining = !self.show_remaining;
                self.db.set(
                    "show_remaining",
                    if self.show_remaining { "1" } else { "0" },
                )?;
                return Ok(Task::none());
            }
            Msg::MediaSkip(secs) => {
                self.media_skip = secs;
                self.db.set("media_skip_secs", &secs.to_string())?;
                return Ok(Task::none());
            }
            Msg::Ipc => {
                if !IPC_PENDING.swap(false, Ordering::AcqRel) {
                    return Ok(Task::none());
                }
                let commands: Vec<String> = self
                    .ipc_bridge
                    .lock()
                    .map(|mut b| b.commands.drain(..).collect())
                    .unwrap_or_default();
                if commands.is_empty() {
                    return Ok(Task::none());
                }
                let mut tasks = Vec::new();
                for command in commands {
                    match command.as_str() {
                        "next" => tasks.push(self.handle(Msg::MediaNext)?),
                        "prev" => tasks.push(self.handle(Msg::MediaPrev)?),
                        "play-pause" => tasks.push(self.handle(Msg::Pause)?),
                        "play" => tasks.push(self.handle(Msg::MediaPlay)?),
                        "pause" => tasks.push(self.handle(Msg::MediaPause)?),
                        _ => {}
                    }
                }
                self.sync_ipc();
                return Ok(Task::batch(tasks));
            }
            Msg::MediaPlay => {
                if self.current.is_none()
                    || self
                        .player
                        .as_ref()
                        .is_none_or(|p| p.state().error.is_some())
                {
                    return self.handle(Msg::Pause);
                }
                if let Some(p) = &mut self.player {
                    p.pause(false)?;
                }
                self.save()?;
                self.db.active(self.current.as_ref().map(|e| e.id))?;
                self.sync_ipc();
                return Ok(if self.current.as_ref().is_some_and(Episode::is_local) {
                    Task::none()
                } else {
                    self.request_sync()
                });
            }
            Msg::MediaPause => {
                if let Some(p) = &mut self.player {
                    p.pause(true)?;
                    self.save()?;
                    self.db.active(None)?;
                    self.sync_ipc();
                    return Ok(if self.current.as_ref().is_some_and(Episode::is_local) {
                        Task::none()
                    } else {
                        self.request_sync()
                    });
                }
                return Ok(Task::none());
            }
            Msg::MediaNext => {
                if self.media_skip > 0 {
                    return self.handle(Msg::Skip(self.media_skip as f64));
                }
                return self.handle(Msg::Next);
            }
            Msg::MediaPrev => {
                if self.media_skip > 0 {
                    return self.handle(Msg::Skip(-(self.media_skip as f64)));
                }
                if self
                    .player
                    .as_ref()
                    .is_some_and(|p| p.state().position > 3.0)
                {
                    return self.handle(Msg::Seek(0.0));
                }
                if let Some(current) = &self.current {
                    let previous = if current.is_local() {
                        let ids = self.db.local_all_ids(&self.active_query)?;
                        ids.iter()
                            .position(|id| *id == current.id)
                            .and_then(|i| i.checked_sub(1))
                            .map(|i| ids[i])
                    } else {
                        self.view_ids
                            .iter()
                            .position(|id| *id == current.id)
                            .and_then(|i| i.checked_sub(1))
                            .map(|i| self.view_ids[i])
                    };
                    if let Some(id) = previous {
                        let is_local = self.db.episode(id)?.is_local();
                        self.play(id)?;
                        self.reload()?;
                        return Ok(if is_local {
                            Task::none()
                        } else {
                            self.request_sync()
                        });
                    }
                }
                return self.handle(Msg::Seek(0.0));
            }
            Msg::Scroll(scroll_y, viewport_h, row_stride) => {
                self.scroll_y = scroll_y.max(0.0);
                let vp_changed = if viewport_h > 0.0 {
                    let changed = (viewport_h - self.viewport_height).abs() >= 1.0;
                    self.viewport_height = viewport_h;
                    changed
                } else {
                    false
                };
                let stride = row_stride.max(1.0);
                let raw_first = (self.scroll_y / stride).floor() as usize;
                let target_start = raw_first.saturating_sub(VIRTUAL_OVERSCAN);
                let step_changed = target_start.abs_diff(self.window_start) >= VIRTUAL_STEP
                    || (target_start == 0 && self.window_start != 0);
                if step_changed {
                    self.window_start = target_start;
                    self.ensure_window_cached();
                } else if vp_changed {
                    self.ensure_window_cached();
                }
                return Ok(Task::none());
            }
            Msg::DrawerScroll(scroll_y, viewport_h, row_stride) => {
                self.drawer_scroll_y = scroll_y.max(0.0);
                let vp_changed = if viewport_h > 0.0 {
                    let changed = (viewport_h - self.drawer_viewport_height).abs() >= 1.0;
                    self.drawer_viewport_height = viewport_h;
                    changed
                } else {
                    false
                };
                let stride = row_stride.max(1.0);
                let raw_first = (self.drawer_scroll_y / stride).floor() as usize;
                let target_start = raw_first.saturating_sub(DRAWER_OVERSCAN);
                let step_changed = target_start.abs_diff(self.drawer_window_start) >= DRAWER_STEP
                    || (target_start == 0 && self.drawer_window_start != 0);
                if step_changed {
                    self.drawer_window_start = target_start;
                    self.ensure_drawer_cached();
                } else if vp_changed {
                    self.ensure_drawer_cached();
                }
                return Ok(Task::none());
            }
            Msg::Shift(held) => {
                self.shift_held = held;
                return Ok(Task::none());
            }
            Msg::Focused => {
                self.appearance.refresh();
                let mut tasks = Vec::new();
                if self.last_focus_stats.elapsed() >= Duration::from_secs(30) {
                    self.last_focus_stats = Instant::now();
                    tasks.push(self.spawn_local_stats_sync());
                }
                if !self.busy
                    && self.closing.is_none()
                    && self.failures == 0
                    && self.last_sync.elapsed() >= Duration::from_secs(60)
                    && !self.db.get("account_token").unwrap_or_default().is_empty()
                {
                    tasks.push(self.request_sync());
                }
                return Ok(Task::batch(tasks));
            }
            Msg::ArtworkDone(decoded) => {
                self.artwork_busy = false;
                for (uuid, w, h, pixels) in decoded {
                    self.artwork_handles
                        .insert(uuid, iced::widget::image::Handle::from_rgba(w, h, pixels));
                }
                return Ok(self.maybe_fetch_artwork());
            }
            Msg::LocalStatsDone(result) => {
                if let Ok(report) = result {
                    self.stats_report = report;
                    self.refresh_account_stats();
                }
                return Ok(Task::none());
            }
            Msg::Captured(screenshot) => {
                if let Some(path) = self.screenshot.take() {
                    let file = std::io::BufWriter::new(std::fs::File::create(path)?);
                    let mut encoder =
                        png::Encoder::new(file, screenshot.size.width, screenshot.size.height);
                    encoder.set_color(png::ColorType::Rgba);
                    encoder.set_depth(png::BitDepth::Eight);
                    encoder.write_header()?.write_image_data(&screenshot.rgba)?;
                    self.status = "Window screenshot saved".into();
                }
                return Ok(Task::none());
            }
            Msg::Tick => {
                if self.closing.is_some() {
                    return Ok(iced::exit());
                }
                self.sync_ipc();
                if self.last_theme_check.elapsed() >= Duration::from_secs(5) {
                    self.last_theme_check = Instant::now();
                    self.appearance.refresh();
                }
                if self.screenshot.is_some() && Instant::now() >= self.screenshot_at {
                    self.screenshot_at = Instant::now() + Duration::from_secs(3600);
                    return Ok(iced::window::latest()
                        .and_then(iced::window::screenshot)
                        .map(Msg::Captured));
                }
                if self.quit_after.is_some_and(|t| Instant::now() >= t) {
                    return self.handle(Msg::Close);
                }
                if self.sleep.is_some_and(|t| Instant::now() >= t) {
                    self.sleep = None;
                    if let Some(p) = &mut self.player {
                        p.pause(true)?;
                    }
                    self.save()?;
                    self.db.active(None)?;
                    return Ok(if self.current.as_ref().is_some_and(Episode::is_local) {
                        Task::none()
                    } else {
                        self.request_sync()
                    });
                }
                if let Some(p) = &self.player {
                    let state = p.state();
                    if let Some(error) = &state.error {
                        if !self.player_error_latched {
                            self.player_error_latched = true;
                            if self.current.as_ref().is_some_and(Episode::is_local)
                                && !self.db.is_local_drive_accessible(&self.local_path)
                            {
                                self.local_drive_locked = true;
                                self.status = "Encrypted media drive disconnected — playback paused and queue retained".into();
                            } else {
                                self.status = error.clone();
                            }
                            self.status_is_error = true;
                            self.db.active(None)?;
                        }
                    } else {
                        self.player_error_latched = false;
                    }
                    if state.eof && self.current.is_some() {
                        self.save()?;
                        let finished_local = self.current.as_ref().is_some_and(Episode::is_local);
                        let id = self.current.as_ref().unwrap().id;
                        self.db.change(id, "playing_status", 2)?;
                        if self.sleep_eof {
                            self.sleep_eof = false;
                            self.current = None;
                            self.db.active(None)?;
                        } else {
                            self.next()?;
                        }
                        self.reload()?;
                        sync_needed = !finished_local;
                    } else if state.loaded
                        && !state.paused
                        && self.last_save.elapsed() >= Duration::from_secs(5)
                    {
                        self.save()?;
                    }
                }
                if Instant::now() >= self.next_sync && !self.busy {
                    return Ok(self.request_sync());
                }
                let artwork_task = self.maybe_fetch_artwork();
                let sync_task = if sync_needed {
                    self.request_sync()
                } else {
                    Task::none()
                };
                return Ok(Task::batch([artwork_task, sync_task]));
            }
            Msg::Nav(nav) => {
                self.nav = nav;
                self.podcast = 0;
                self.podcast_desc_expanded = false;
                self.query.clear();
                self.active_query.clear();
                self.filter_gen = self.filter_gen.wrapping_add(1);
                self.selected.clear();
                self.last_selected = None;
                self.info = None;
                self.info_playlists.clear();
                self.confirm_action = None;
                self.playlist_picker = None;
                self.editing_item = None;
                self.db.set("last_nav", &self.nav)?;
                self.db.set("last_podcast", "0")?;
                self.reload_episodes()?;
                let scroll_task = self.reset_main_scroll();
                if self.nav == "Account" {
                    self.refresh_account_stats();
                    return Ok(Task::batch([scroll_task, self.spawn_local_stats_sync()]));
                }
                return Ok(scroll_task);
            }
            Msg::Podcast(id) => {
                self.podcast = id;
                self.nav = "Podcast".into();
                self.podcast_filter = self
                    .selected_podcast()
                    .filter(|p| p.show_archived)
                    .map(|_| "All")
                    .unwrap_or("Active")
                    .into();
                self.podcast_desc_expanded = false;
                self.query.clear();
                self.active_query.clear();
                self.filter_gen = self.filter_gen.wrapping_add(1);
                self.selected.clear();
                self.last_selected = None;
                self.info = None;
                self.info_playlists.clear();
                self.confirm_action = None;
                self.playlist_picker = None;
                self.editing_item = None;
                self.db.set("last_nav", &self.nav)?;
                self.db.set("last_podcast", &id.to_string())?;
                self.reload_episodes()?;
                return Ok(self.reset_main_scroll());
            }
            Msg::BackFromPodcast => return self.handle(Msg::Nav("Podcasts".into())),
            Msg::PodcastsGridToggle(grid) => {
                self.podcasts_grid = grid;
                self.db.set("podcasts_grid", if grid { "1" } else { "0" })?;
                return Ok(Task::none());
            }
            Msg::PodcastsSort(sort) => {
                self.podcasts_sort = sort;
                self.db.set("podcasts_sort", &self.podcasts_sort)?;
                return Ok(Task::none());
            }
            Msg::PodcastsFolderFilter(folder) => {
                self.podcasts_folder = folder;
                return Ok(Task::none());
            }
            Msg::PodcastsQuery(q) => {
                self.podcasts_query = q;
                return Ok(Task::none());
            }
            Msg::PodcastFilter(filter) => {
                self.podcast_filter = filter;
                if self.podcast > 0 {
                    let show_archived = matches!(self.podcast_filter.as_str(), "All" | "Archived");
                    self.db
                        .set_podcast_show_archived(self.podcast, show_archived)?;
                    if let Some(p) = self.podcasts.iter_mut().find(|p| p.id == self.podcast) {
                        p.show_archived = show_archived;
                    }
                }
                self.selected.clear();
                self.last_selected = None;
                self.reload_episodes()?;
                return Ok(self.reset_main_scroll());
            }
            Msg::PodcastSort(sort) => {
                if self.podcast > 0 {
                    self.db.set_podcast_sort(self.podcast, sort)?;
                    if let Some(p) = self.podcasts.iter_mut().find(|p| p.id == self.podcast) {
                        p.episodes_sort = sort.clamp(0, 3);
                    }
                    self.reload_episodes()?;
                    return Ok(self.reset_main_scroll());
                }
                return Ok(Task::none());
            }
            Msg::TogglePodcastDesc => {
                self.podcast_desc_expanded = !self.podcast_desc_expanded;
                return Ok(Task::none());
            }
            Msg::RefreshPodcast(id) => return Ok(self.task(Job::RefreshPodcast(id))),
            Msg::PodcastMarkAllPlayed(id) => {
                let ids = self.db.podcast_episode_ids(id, "Unplayed", "", 0)?;
                if !ids.is_empty() {
                    self.db.change_batch(&ids, "playing_status", 2)?;
                    sync_needed = true;
                    reload_all = true;
                }
            }
            Msg::PodcastArchivePlayed(id) => {
                let ids: Vec<i64> = self
                    .db
                    .conn
                    .prepare(
                        "SELECT id FROM episodes WHERE podcast_id=?1 AND archived=0 AND playing_status=2",
                    )?
                    .query_map([id], |r| r.get(0))?
                    .collect::<rusqlite::Result<Vec<_>>>()?;
                if !ids.is_empty() {
                    self.db.change_batch(&ids, "archived", 1)?;
                    sync_needed = true;
                    reload_all = true;
                }
            }
            Msg::Filter(q) => {
                self.query = q;
                self.filter_gen = self.filter_gen.wrapping_add(1);
                let generation = self.filter_gen;
                if self.query.is_empty() {
                    self.active_query.clear();
                    self.reload_episodes()?;
                    return Ok(self.reset_main_scroll());
                }
                return Ok(Task::perform(debounce_filter(generation), Msg::ApplyFilter));
            }
            Msg::ApplyFilter(generation) => {
                if generation != self.filter_gen {
                    return Ok(Task::none());
                }
                self.active_query = self.query.clone();
                self.reload_episodes()?;
                return Ok(self.reset_main_scroll());
            }
            Msg::ToggleSelect(id) => {
                if self.shift_held
                    && let Some(anchor) = self.last_selected
                    && let (Some(a), Some(b)) = (
                        self.view_ids.iter().position(|eid| *eid == anchor),
                        self.view_ids.iter().position(|eid| *eid == id),
                    )
                {
                    let (start, end) = if a <= b { (a, b) } else { (b, a) };
                    self.selected
                        .extend(self.view_ids[start..=end].iter().copied());
                    self.last_selected = Some(id);
                    return Ok(Task::none());
                }
                if !self.selected.remove(&id) {
                    self.selected.insert(id);
                    self.last_selected = Some(id);
                }
                return Ok(Task::none());
            }
            Msg::SelectAll => {
                self.last_selected = self.view_ids.last().copied();
                self.selected.extend(self.view_ids.iter().copied());
                return Ok(Task::none());
            }
            Msg::SelectAbove => {
                let anchor_idx = self
                    .last_selected
                    .and_then(|id| self.view_ids.iter().position(|eid| *eid == id))
                    .or_else(|| {
                        self.view_ids
                            .iter()
                            .rposition(|eid| self.selected.contains(eid))
                    });
                if let Some(idx) = anchor_idx {
                    self.selected.extend(self.view_ids[..=idx].iter().copied());
                }
                return Ok(Task::none());
            }
            Msg::SelectBelow => {
                let anchor_idx = self
                    .last_selected
                    .and_then(|id| self.view_ids.iter().position(|eid| *eid == id))
                    .or_else(|| {
                        self.view_ids
                            .iter()
                            .position(|eid| self.selected.contains(eid))
                    });
                if let Some(idx) = anchor_idx {
                    self.selected.extend(self.view_ids[idx..].iter().copied());
                }
                return Ok(Task::none());
            }
            Msg::ClearSelection => {
                self.selected.clear();
                self.last_selected = None;
                return Ok(Task::none());
            }
            Msg::RequestConfirm(action) => {
                self.confirm_action = Some(action);
                return Ok(Task::none());
            }
            Msg::CancelConfirm => {
                self.confirm_action = None;
                return Ok(Task::none());
            }
            Msg::Confirm => {
                let Some(action) = self.confirm_action.take() else {
                    return Ok(Task::none());
                };
                return match action {
                    ConfirmAction::ClearUpNext => {
                        self.db.clear_up_next()?;
                        self.selected.clear();
                        self.last_selected = None;
                        self.reload_episodes()?;
                        Ok(self.request_sync())
                    }
                    ConfirmAction::ClearLocalQueue => self.handle(Msg::ClearLocalQueue),
                    ConfirmAction::ClearPlaylist(pid) => self.handle(Msg::ClearPlaylist(pid)),
                    ConfirmAction::DeleteFolder(id) => self.handle(Msg::DeleteFolder(id)),
                    ConfirmAction::DeletePlaylist(id) => self.handle(Msg::DeletePlaylist(id)),
                    ConfirmAction::Unsubscribe(id) => self.handle(Msg::Unsubscribe(id)),
                };
            }
            Msg::ClearPlaylist(pid) => {
                self.db.clear_playlist_episodes(pid)?;
                self.selected.clear();
                self.last_selected = None;
                self.confirm_action = None;
                sync_needed = true;
                reload_eps = true;
            }
            Msg::SortUpNext(sort) => {
                self.db.sort_up_next(sort)?;
                sync_needed = true;
                reload_eps = true;
            }
            Msg::BulkMoveTop => {
                if self.selected.is_empty() {
                    return Ok(Task::none());
                }
                if let Some(pid) = self
                    .nav
                    .strip_prefix("playlist:")
                    .and_then(|s| s.parse::<i64>().ok())
                {
                    let current = self.db.view_ids(&self.nav, "", 0)?;
                    let mut ids: Vec<i64> = current
                        .iter()
                        .copied()
                        .filter(|id| self.selected.contains(id))
                        .collect();
                    ids.extend(current.into_iter().filter(|id| !self.selected.contains(id)));
                    self.db.reorder_playlist(pid, &ids)?;
                    sync_needed = true;
                } else {
                    let local = self.nav == "Local queue";
                    let current = self.db.queue_for(local)?;
                    let pinned = self
                        .current
                        .as_ref()
                        .filter(|c| c.is_local() == local && current.first() == Some(&c.id))
                        .map(|c| c.id);
                    let mut ids = Vec::with_capacity(current.len());
                    if let Some(head) = pinned {
                        ids.push(head);
                    }
                    let rest = &current[usize::from(pinned.is_some())..];
                    ids.extend(rest.iter().copied().filter(|id| self.selected.contains(id)));
                    ids.extend(
                        rest.iter()
                            .copied()
                            .filter(|id| !self.selected.contains(id)),
                    );
                    self.db.reorder_queue(&ids)?;
                    sync_needed = !local;
                }
                reload_eps = true;
            }
            Msg::BulkMoveBottom => {
                if self.selected.is_empty() {
                    return Ok(Task::none());
                }
                if let Some(pid) = self
                    .nav
                    .strip_prefix("playlist:")
                    .and_then(|s| s.parse::<i64>().ok())
                {
                    let current = self.db.view_ids(&self.nav, "", 0)?;
                    let mut ids: Vec<i64> = current
                        .iter()
                        .copied()
                        .filter(|id| !self.selected.contains(id))
                        .collect();
                    ids.extend(current.into_iter().filter(|id| self.selected.contains(id)));
                    self.db.reorder_playlist(pid, &ids)?;
                    sync_needed = true;
                } else {
                    let local = self.nav == "Local queue";
                    let current = self.db.queue_for(local)?;
                    let pinned = self
                        .current
                        .as_ref()
                        .filter(|c| c.is_local() == local && current.first() == Some(&c.id))
                        .map(|c| c.id);
                    let mut ids = Vec::with_capacity(current.len());
                    if let Some(head) = pinned {
                        ids.push(head);
                    }
                    let rest = &current[usize::from(pinned.is_some())..];
                    ids.extend(
                        rest.iter()
                            .copied()
                            .filter(|id| !self.selected.contains(id)),
                    );
                    ids.extend(rest.iter().copied().filter(|id| self.selected.contains(id)));
                    self.db.reorder_queue(&ids)?;
                    sync_needed = !local;
                }
                reload_eps = true;
            }
            Msg::BulkMove(delta) => {
                if self.selected.is_empty() {
                    return Ok(Task::none());
                }
                let step = |ids: &mut [i64], selected: &HashSet<i64>| {
                    if delta < 0 {
                        for i in 1..ids.len() {
                            if selected.contains(&ids[i]) && !selected.contains(&ids[i - 1]) {
                                ids.swap(i - 1, i);
                            }
                        }
                    } else if delta > 0 {
                        for i in (0..ids.len().saturating_sub(1)).rev() {
                            if selected.contains(&ids[i]) && !selected.contains(&ids[i + 1]) {
                                ids.swap(i, i + 1);
                            }
                        }
                    }
                };
                if let Some(pid) = self
                    .nav
                    .strip_prefix("playlist:")
                    .and_then(|s| s.parse::<i64>().ok())
                {
                    let mut ids = self.db.view_ids(&self.nav, "", 0)?;
                    step(&mut ids, &self.selected);
                    self.db.reorder_playlist(pid, &ids)?;
                    sync_needed = true;
                } else {
                    let local = self.nav == "Local queue";
                    let mut ids = self.db.queue_for(local)?;
                    let pinned_head = self
                        .current
                        .as_ref()
                        .is_some_and(|c| c.is_local() == local && ids.first() == Some(&c.id));
                    let start = usize::from(pinned_head).min(ids.len());
                    step(&mut ids[start..], &self.selected);
                    self.db.reorder_queue(&ids)?;
                    sync_needed = !local;
                }
                reload_eps = true;
            }
            Msg::BulkRemove => {
                let ids: Vec<i64> = self
                    .view_ids
                    .iter()
                    .copied()
                    .filter(|id| self.selected.contains(id))
                    .collect();
                if let Some(pid) = self
                    .nav
                    .strip_prefix("playlist:")
                    .and_then(|s| s.parse::<i64>().ok())
                {
                    self.db.remove_playlist_episodes(pid, &ids)?;
                    sync_needed = true;
                } else {
                    self.db.edit_queue_batch(&ids, 4)?;
                    sync_needed = self.nav != "Local queue";
                }
                self.selected.clear();
                self.last_selected = None;
                reload_eps = true;
            }
            Msg::BulkQueue(action) => {
                let ids: Vec<i64> = self
                    .view_ids
                    .iter()
                    .copied()
                    .filter(|id| self.selected.contains(id))
                    .collect();
                let (_, remote_ids) = self.db.partition_local_remote_ids(&ids)?;
                self.db.edit_queue_batch(&ids, action)?;
                sync_needed = !remote_ids.is_empty();
                reload_eps = true;
            }
            Msg::BulkPlayed => {
                let ids: Vec<i64> = self
                    .view_ids
                    .iter()
                    .copied()
                    .filter(|id| self.selected.contains(id))
                    .collect();
                let (_, remote_ids) = self.db.partition_local_remote_ids(&ids)?;
                self.db.change_batch(&ids, "playing_status", 2)?;
                sync_needed = !remote_ids.is_empty();
                reload_all = self.podcast > 0;
                reload_eps = !reload_all;
            }
            Msg::BulkArchive => {
                let ids: Vec<i64> = self
                    .view_ids
                    .iter()
                    .copied()
                    .filter(|id| self.selected.contains(id))
                    .collect();
                let (_, remote_ids) = self.db.partition_local_remote_ids(&ids)?;
                self.db.change_batch(&ids, "archived", 1)?;
                self.selected.clear();
                self.last_selected = None;
                sync_needed = !remote_ids.is_empty();
                reload_all = self.podcast > 0;
                reload_eps = !reload_all;
            }
            Msg::MoveEdge(id, to_top) => {
                if let Some(pid) = self
                    .nav
                    .strip_prefix("playlist:")
                    .and_then(|s| s.parse::<i64>().ok())
                {
                    let mut ids = self.db.view_ids(&self.nav, "", 0)?;
                    if let Some(index) = ids.iter().position(|i| *i == id) {
                        let item = ids.remove(index);
                        if to_top {
                            ids.insert(0, item);
                        } else {
                            ids.push(item);
                        }
                        self.db.reorder_playlist(pid, &ids)?;
                        sync_needed = true;
                    }
                } else {
                    let local = self.db.episode(id)?.is_local();
                    let mut ids = self.db.queue_for(local)?;
                    let pinned_head = self
                        .current
                        .as_ref()
                        .is_some_and(|c| c.is_local() == local && ids.first() == Some(&c.id));
                    let min_idx = usize::from(pinned_head);
                    if let Some(index) = ids.iter().position(|i| *i == id)
                        && index >= min_idx
                    {
                        let item = ids.remove(index);
                        if to_top {
                            ids.insert(min_idx.min(ids.len()), item);
                        } else {
                            ids.push(item);
                        }
                        self.db.reorder_queue(&ids)?;
                        sync_needed = !local;
                    }
                }
                reload_eps = true;
            }
            Msg::Play(id) => {
                let is_local = self.db.episode(id)?.is_local();
                self.play(id)?;
                if self.local_autoqueue
                    && self.nav == "Local files"
                    && !self.queue_drawer_open
                    && is_local
                {
                    let ids = self.db.local_all_ids(&self.active_query)?;
                    if let Some(index) = ids.iter().position(|candidate| *candidate == id) {
                        self.db.set_local_queue(&ids[index..])?;
                    }
                }
                reload_eps = true;
                sync_needed = !is_local;
            }
            Msg::Pause => {
                if self.current.is_none() {
                    if let Some(id) = self.view_ids.first().copied() {
                        let is_local = self.db.episode(id)?.is_local();
                        self.play(id)?;
                        if self.local_autoqueue && self.nav == "Local files" {
                            let ids = self.db.local_all_ids(&self.active_query)?;
                            if let Some(index) = ids.iter().position(|candidate| *candidate == id) {
                                self.db.set_local_queue(&ids[index..])?;
                            }
                        }
                        self.reload()?;
                        return Ok(if is_local {
                            Task::none()
                        } else {
                            self.request_sync()
                        });
                    }
                    return Ok(Task::none());
                }
                if self
                    .player
                    .as_ref()
                    .is_none_or(|p| p.state().error.is_some())
                {
                    let cur_id = self.current.as_ref().unwrap().id;
                    let is_local = self.current.as_ref().unwrap().is_local();
                    self.play(cur_id)?;
                    self.reload()?;
                    return Ok(if is_local {
                        Task::none()
                    } else {
                        self.request_sync()
                    });
                }
                let now_paused = if let Some(p) = &mut self.player {
                    p.toggle_pause()?
                } else {
                    true
                };
                self.save()?;
                self.db.active(if !now_paused {
                    self.current.as_ref().map(|e| e.id)
                } else {
                    None
                })?;
                self.sync_ipc();
                return Ok(if self.current.as_ref().is_some_and(Episode::is_local) {
                    Task::none()
                } else {
                    self.request_sync()
                });
            }
            Msg::Seek(pos) => {
                if let Some(p) = &mut self.player {
                    p.seek(pos)?;
                } else if let Some(e) = &mut self.current {
                    e.position = (pos.max(0.0) * 1000.0) as i64;
                    self.db.progress(e.id, pos, e.duration as f64)?;
                }
                return Ok(Task::none());
            }
            Msg::Skip(delta) => {
                if let Some(p) = &mut self.player {
                    let state = p.state();
                    let target = (state.position + delta)
                        .max(0.0)
                        .min(if state.duration > 0.0 {
                            (state.duration - 0.1).max(0.0)
                        } else {
                            f64::MAX
                        });
                    p.seek(target)?;
                }
                return Ok(Task::none());
            }
            Msg::Speed(value) => {
                let rounded = ((value * 20.0).round() / 20.0).clamp(0.25, 4.0);
                self.speed = rounded;
                if let Some(p) = &mut self.player {
                    p.speed(rounded)?;
                }
                return Ok(Task::none());
            }
            Msg::StepSpeed(delta) => {
                let next = if delta == 0.0 {
                    1.0
                } else {
                    (((self.speed + delta) * 20.0).round() / 20.0).clamp(0.25, 4.0)
                };
                self.speed = next;
                self.db.set("speed", &next.to_string())?;
                if let Some(p) = &mut self.player {
                    p.speed(next)?;
                }
                return Ok(Task::none());
            }
            Msg::SaveSpeed => {
                self.db.set("speed", &self.speed.to_string())?;
                return Ok(Task::none());
            }
            Msg::Volume(value) => {
                self.volume = value;
                if let Some(p) = &mut self.player {
                    p.volume(value)?;
                }
                return Ok(Task::none());
            }
            Msg::SaveVolume => {
                self.db.set("volume", &(self.volume / 100.0).to_string())?;
                return Ok(Task::none());
            }
            Msg::Trim(value) => {
                self.save()?;
                self.trim = value;
                self.db.set("trim", &self.trim)?;
                if let Some(p) = &mut self.player
                    && p.set_trim(&self.trim).is_err()
                    && let Some(old) = self.player.take()
                {
                    let state = old.state();
                    drop(old);
                    let mut restarted = Player::start(
                        &self.trim,
                        self.speed,
                        self.volume,
                        std::env::var("DUSKY_CASTS_SILENT").as_deref() == Ok("1"),
                    )?;
                    if let Some(e) = &self.current {
                        let e = self.db.episode(e.id)?;
                        let uri = if std::path::Path::new(&e.downloaded_path).is_file() {
                            &e.downloaded_path
                        } else {
                            &e.audio_url
                        };
                        restarted.load(uri, state.position, state.paused)?;
                    }
                    self.player = Some(restarted);
                }
                return Ok(Task::none());
            }
            Msg::Star(id) => {
                let e = self.db.episode(id)?;
                let next_star = !e.starred;
                self.db.change(id, "starred", next_star as i64)?;
                self.update_cached_episode(id, |ep| ep.starred = next_star);
                sync_needed = !e.is_local();
                if self.nav == "Starred" || self.nav.starts_with("playlist:") {
                    reload_eps = true;
                }
            }
            Msg::Archive(id) => {
                let e = self.db.episode(id)?;
                let next_arch = !e.archived;
                self.db.change(id, "archived", next_arch as i64)?;
                self.update_cached_episode(id, |ep| ep.archived = next_arch);
                sync_needed = !e.is_local();
                if self.podcast > 0 {
                    reload_all = true;
                } else if matches!(
                    self.nav.as_str(),
                    "All episodes" | "In progress" | "Archived"
                ) || self.nav.starts_with("folder:")
                    || self.nav.starts_with("playlist:")
                {
                    reload_eps = true;
                } else {
                    reload_summaries = true;
                }
            }
            Msg::Played(id) => {
                let e = self.db.episode(id)?;
                let next_status = if e.playing_status == 2 { 0 } else { 2 };
                self.db.change(id, "playing_status", next_status as i64)?;
                self.update_cached_episode(id, |ep| ep.playing_status = next_status);
                sync_needed = !e.is_local();
                if self.podcast > 0 {
                    reload_all = true;
                } else if matches!(self.nav.as_str(), "In progress" | "History")
                    || self.nav.starts_with("playlist:")
                {
                    reload_eps = true;
                } else {
                    reload_summaries = true;
                }
            }
            Msg::Queue(id, action) => {
                let is_local = self.db.episode(id)?.is_local();
                self.db.edit_queue(id, action)?;
                if matches!(self.nav.as_str(), "Up Next" | "Local queue") {
                    reload_eps = true;
                } else {
                    reload_summaries = true;
                }
                if self.queue_drawer_open {
                    self.reload_drawer();
                }
                sync_needed = !is_local;
            }
            Msg::Move(id, delta) => {
                if let Some(pid) = self
                    .nav
                    .strip_prefix("playlist:")
                    .and_then(|s| s.parse::<i64>().ok())
                {
                    let mut ids = self.db.view_ids(&self.nav, "", 0)?;
                    if let Some(index) = ids.iter().position(|i| *i == id) {
                        let target = (index as i32 + delta).clamp(0, ids.len() as i32 - 1) as usize;
                        ids.swap(index, target);
                        self.db.reorder_playlist(pid, &ids)?;
                        sync_needed = true;
                    }
                } else {
                    let is_local = self.db.episode(id)?.is_local();
                    let mut ids = self.db.queue_for(is_local)?;
                    let pinned_head = self
                        .current
                        .as_ref()
                        .is_some_and(|c| c.is_local() == is_local && ids.first() == Some(&c.id));
                    let min_idx = usize::from(pinned_head);
                    if let Some(index) = ids.iter().position(|i| *i == id)
                        && index >= min_idx
                        && ids.len() > min_idx
                    {
                        let target = (index as i32 + delta)
                            .clamp(min_idx as i32, ids.len() as i32 - 1)
                            as usize;
                        ids.swap(index, target);
                        self.db.reorder_queue(&ids)?;
                        sync_needed = !is_local;
                    }
                }
                reload_eps = true;
            }
            Msg::Next => {
                let was_local = self.current.as_ref().is_some_and(Episode::is_local);
                self.save()?;
                if let Some(p) = &mut self.player {
                    p.pause(true)?;
                }
                self.next()?;
                sync_needed = !was_local;
                reload_eps = true;
            }
            Msg::Info(id) => {
                self.info = Some(self.db.episode(id)?);
                self.queue_drawer_open = false;
                self.refresh_info_playlists();
                return Ok(Task::none());
            }
            Msg::CloseInfo => {
                self.info = None;
                self.info_playlists.clear();
                return Ok(Task::none());
            }
            Msg::Email(v) => {
                self.email = v;
                return Ok(Task::none());
            }
            Msg::Password(v) => {
                self.password = v;
                return Ok(Task::none());
            }
            Msg::Login => {
                let email = self.email.trim().to_string();
                anyhow::ensure!(
                    !email.is_empty() && !self.password.is_empty(),
                    "Enter email and password"
                );
                let password = std::mem::take(&mut self.password);
                return Ok(self.task(Job::Login(email, password)));
            }
            Msg::Logout => {
                anyhow::ensure!(
                    !self.busy,
                    "Wait for the current account operation to finish"
                );
                for key in [
                    "account_token",
                    "account_uuid",
                    "account_email",
                    "sync_cursor",
                    "upnext_cursor",
                    "rust_import_complete",
                    "rust_queue_initialized",
                    "last_sync_time",
                    "stats_ack_seconds",
                    "stats_other_seconds",
                    "stats_summary_at",
                    "stats_error",
                ] {
                    self.db.set(key, "")?;
                }
                self.email.clear();
                self.password.clear();
                self.status = "Signed out; local library retained".into();
                self.status_is_error = false;
                self.pending_sync = false;
                reload_all = true;
            }
            Msg::Sync => return Ok(self.request_sync()),
            Msg::Refresh if matches!(self.nav.as_str(), "Local files" | "Local queue") => {
                return Ok(self.task(Job::LocalScan(self.local_path.clone())));
            }
            Msg::Refresh if self.podcast > 0 => {
                return Ok(self.task(Job::RefreshPodcast(self.podcast)));
            }
            Msg::Refresh => return Ok(self.task(Job::Refresh)),
            Msg::Feed(v) => {
                self.feed = v;
                return Ok(Task::none());
            }
            Msg::AddFeed => return Ok(self.task(Job::Feed(self.feed.trim().to_string()))),
            Msg::Discover(v) => {
                self.discover = v;
                return Ok(Task::none());
            }
            Msg::Search => return Ok(self.task(Job::Search(self.discover.clone()))),
            Msg::Subscribe(index) => {
                if let Some(result) = self.results.get(index) {
                    return Ok(self.task(Job::Subscribe(result.clone())));
                }
            }
            Msg::Download(id) => {
                if self.downloads.contains_key(&id) {
                    return Ok(Task::none());
                }
                anyhow::ensure!(
                    self.downloads.len() < 3,
                    "Three downloads are already running"
                );
                let control = Arc::new(library::DownloadControl::default());
                self.downloads.insert(id, control.clone());
                self.status = "Downloading episode…".into();
                self.status_is_error = false;
                let path = self.db.path.clone();
                return Ok(Task::perform(
                    run_job(path, Job::Download(id, control)),
                    move |result| Msg::Downloaded(id, result),
                ));
            }
            Msg::Downloaded(id, result) => {
                self.downloads.remove(&id);
                match result {
                    Ok(Outcome::Status { message, .. }) => {
                        self.status = message;
                        self.status_is_error = false;
                        if let Ok(fresh) = self.db.episode(id) {
                            let path = fresh.downloaded_path;
                            self.update_cached_episode(id, |ep| ep.downloaded_path = path.clone());
                        }
                    }
                    Err(error) => {
                        self.status = error;
                        self.status_is_error = true;
                    }
                    _ => {}
                }
                reload_eps = self.nav == "Downloads"
                    || (self.podcast > 0 && self.podcast_filter == "Downloaded");
            }
            Msg::CancelDownload(id) => {
                if let Some(control) = self.downloads.get(&id) {
                    control.cancel.store(true, Ordering::Relaxed);
                }
                self.status = "Cancelling download…".into();
                return Ok(Task::none());
            }
            Msg::Done(result) => {
                self.busy = false;
                let mut should_reload = false;
                match result {
                    Ok(Outcome::Status {
                        message,
                        reload_db,
                        stats_report,
                    }) => {
                        self.status = message;
                        self.status_is_error = false;
                        self.failures = 0;
                        should_reload = reload_db;
                        if let Some(report) = stats_report {
                            self.stats_report = report;
                        }
                    }
                    Ok(Outcome::Search(results)) => {
                        self.results = results;
                        self.status = format!("{} results", self.results.len());
                        self.status_is_error = false;
                        self.failures = 0;
                    }
                    Err(error) => {
                        self.status = error;
                        self.status_is_error = true;
                        self.failures = (self.failures + 1).min(6);
                        should_reload = true;
                    }
                }
                if self.status.starts_with("Local library:") {
                    self.local_path = self.db.get("local_library_path")?;
                    self.editing_local_path = false;
                }
                self.last_sync = Instant::now();
                self.next_sync = self.last_sync
                    + Duration::from_secs(if self.failures > 0 {
                        (5u64 << self.failures).min(300)
                    } else if self.player.as_ref().is_some_and(|p| !p.state().paused) {
                        20
                    } else {
                        300
                    });
                self.email = self.db.get("account_email")?;
                if should_reload {
                    self.reload()?;
                    self.validate_nav();
                }
                if let Some(current) = &self.current {
                    let exists: bool = self.db.conn.query_row(
                        "SELECT EXISTS(SELECT 1 FROM episodes WHERE id=?)",
                        [current.id],
                        |r| r.get(0),
                    )?;
                    if !exists {
                        self.player.take();
                        self.current = None;
                        self.db.active(None)?;
                    }
                }
                // A paused or unstarted player follows a newer phone position on resume.
                if self.player.as_ref().is_none_or(|p| p.state().paused)
                    && let Some(current) = &self.current
                    && !current.is_local()
                {
                    let prev_pos = current.position;
                    let e = self.db.episode(current.id)?;
                    if e.position != prev_pos
                        && let Some(p) = &mut self.player
                        && (p.state().position - e.position as f64 / 1000.0).abs() > 1.0
                    {
                        p.seek(e.position as f64 / 1000.0)?;
                    }
                    self.current = Some(e);
                }
                if self.closing.is_some() {
                    return Ok(iced::exit());
                }
                let mut tasks = Vec::new();
                if should_reload {
                    self.artwork_tried
                        .retain(|uuid| self.artwork_handles.contains_key(uuid));
                    tasks.push(self.maybe_fetch_artwork());
                }
                if let Some(job) = self.queued_job.take() {
                    tasks.push(self.task(job));
                    return Ok(Task::batch(tasks));
                }
                if self.pending_sync && self.failures == 0 {
                    tasks.push(self.request_sync());
                    return Ok(Task::batch(tasks));
                }
                if self.failures == 0
                    && self.db.conn.query_row(
                        "SELECT EXISTS(SELECT 1 FROM episodes e JOIN podcasts p ON p.id=e.podcast_id WHERE e.needs_sync=1 AND p.feed_url LIKE 'pcast://%')",
                        [],
                        |r| r.get::<_, bool>(0),
                    )?
                {
                    self.next_sync = Instant::now() + Duration::from_secs(2);
                }
                return Ok(Task::batch(tasks));
            }
            Msg::Close => {
                if self.closing.is_some() {
                    return Ok(Task::none());
                }
                self.save()?;
                self.db.set("last_nav", &self.nav)?;
                self.db.set("last_podcast", &self.podcast.to_string())?;
                self.db.set("volume", &(self.volume / 100.0).to_string())?;
                for control in self.downloads.values() {
                    control.cancel.store(true, Ordering::Relaxed);
                }
                self.db.active(None)?;
                for path in self.ipc_paths.drain(..) {
                    let _ = std::fs::remove_file(path);
                }
                self.player.take();
                let _ = self.db.optimize();
                self.closing = Some(Instant::now());
                self.quit_after = None;
                return Ok(iced::exit());
            }
            Msg::Name(v) => {
                self.name = v;
                return Ok(Task::none());
            }
            Msg::StartRename(is_folder, id, current) => {
                self.editing_item = Some((is_folder, id, current));
                self.confirm_action = None;
                return Ok(Task::none());
            }
            Msg::EditRename(draft) => {
                if let Some((_, _, text)) = &mut self.editing_item {
                    *text = draft;
                }
                return Ok(Task::none());
            }
            Msg::CancelRename => {
                self.editing_item = None;
                return Ok(Task::none());
            }
            Msg::CommitRename => {
                let Some((is_folder, id, draft)) = self.editing_item.take() else {
                    return Ok(Task::none());
                };
                let trimmed = draft.trim();
                anyhow::ensure!(!trimmed.is_empty(), "Enter a name first");
                if is_folder {
                    self.db.conn.execute(
                        "UPDATE folders SET name=?,needs_sync=1,local_modified=max(?,local_modified+1) WHERE id=?",
                        rusqlite::params![trimmed, db::now(), id],
                    )?;
                } else {
                    self.db.conn.execute(
                        "UPDATE playlists SET title=?,needs_sync=1,local_modified=max(?,local_modified+1) WHERE id=?",
                        rusqlite::params![trimmed, db::now(), id],
                    )?;
                }
                sync_needed = true;
                reload_all = true;
            }
            Msg::CreateFolder => {
                anyhow::ensure!(!self.name.trim().is_empty(), "Enter a name");
                self.db.conn.execute(
                    "INSERT INTO folders(uuid,name,added_at,local_modified,needs_sync) VALUES (?,?,?,?,1)",
                    rusqlite::params![
                        uuid::Uuid::new_v4().to_string(),
                        self.name.trim(),
                        db::now() / 1000,
                        db::now()
                    ],
                )?;
                self.name.clear();
                sync_needed = true;
                reload_all = true;
            }
            Msg::CreatePlaylist => {
                anyhow::ensure!(!self.name.trim().is_empty(), "Enter a name");
                let id = uuid::Uuid::new_v4().to_string();
                self.db.conn.execute(
                    "INSERT INTO playlists(uuid,original_uuid,title,manual,sort_type,local_modified,needs_sync) VALUES (?,?,?,1,4,?,1)",
                    rusqlite::params![id, id, self.name.trim(), db::now()],
                )?;
                let pid = self.db.conn.last_insert_rowid();
                if let Some(target_ep_id) = self.playlist_picker
                    && let Ok(e) = self.db.episode(target_ep_id)
                {
                    let _ = self.db.conn.execute(
                        "INSERT OR IGNORE INTO playlist_episodes(playlist_id,episode_uuid,podcast_uuid,title,audio_url,published_at,position,added_at) VALUES (?,?,?,?,?,?,0,?)",
                        rusqlite::params![
                            pid,
                            e.uuid,
                            e.podcast_uuid,
                            e.title,
                            e.audio_url,
                            e.published_at,
                            db::now()
                        ],
                    );
                }
                self.name.clear();
                sync_needed = true;
                reload_all = true;
            }
            Msg::DeleteFolder(id) => {
                self.db.conn.execute(
                    "UPDATE podcasts SET folder_uuid='',needs_sync=CASE WHEN feed_url LIKE 'pcast://%' THEN 1 ELSE needs_sync END WHERE folder_uuid=(SELECT uuid FROM folders WHERE id=?)",
                    [id],
                )?;
                self.db.conn.execute(
                    "UPDATE folders SET is_deleted=1,local_modified=max(?,local_modified+1),needs_sync=1 WHERE id=?",
                    rusqlite::params![db::now(), id],
                )?;
                self.confirm_action = None;
                sync_needed = true;
                reload_all = true;
            }
            Msg::DeletePlaylist(id) => {
                self.db.conn.execute(
                    "UPDATE playlists SET is_deleted=1,local_modified=max(?,local_modified+1),needs_sync=1 WHERE id=?",
                    rusqlite::params![db::now(), id],
                )?;
                self.confirm_action = None;
                sync_needed = true;
                reload_all = true;
            }
            Msg::Assign(id, folder) => {
                self.db.conn.execute(
                    "UPDATE podcasts SET folder_uuid=?,needs_sync=CASE WHEN feed_url LIKE 'pcast://%' THEN 1 ELSE needs_sync END WHERE id=?",
                    rusqlite::params![folder, id],
                )?;
                sync_needed = true;
                reload_all = true;
            }
            Msg::Unsubscribe(id) => {
                if self.current.as_ref().is_some_and(|e| {
                    self.podcasts
                        .iter()
                        .any(|p| p.id == id && p.uuid == e.podcast_uuid)
                }) {
                    self.save()?;
                    self.player.take();
                    self.current = None;
                    self.db.active(None)?;
                }
                library::unsubscribe(&mut self.db, id)?;
                self.confirm_action = None;
                if self.podcast == id {
                    self.podcast = 0;
                    self.nav = "Podcasts".into();
                }
                sync_needed = true;
                reload_all = true;
            }
            Msg::Membership(playlist, episode, add) => {
                let e = self.db.episode(episode)?;
                let tx = self.db.conn.transaction()?;
                if add {
                    tx.execute(
                        "INSERT OR IGNORE INTO playlist_episodes(playlist_id,episode_uuid,podcast_uuid,title,audio_url,published_at,position,added_at) VALUES (?,?,?,?,?,?,(SELECT coalesce(max(position),-1)+1 FROM playlist_episodes WHERE playlist_id=?),?)",
                        rusqlite::params![
                            playlist,
                            e.uuid,
                            e.podcast_uuid,
                            e.title,
                            e.audio_url,
                            e.published_at,
                            playlist,
                            db::now()
                        ],
                    )?;
                } else {
                    tx.execute(
                        "DELETE FROM playlist_episodes WHERE playlist_id=? AND episode_uuid=?",
                        rusqlite::params![playlist, e.uuid],
                    )?;
                }
                tx.execute(
                    "UPDATE playlists SET needs_sync=1,sort_type=4,local_modified=max(?,local_modified+1) WHERE id=?",
                    rusqlite::params![db::now(), playlist],
                )?;
                tx.commit()?;
                sync_needed = !e.is_local();
                reload_eps = true;
                reload_summaries = true;
            }
            Msg::TogglePlaylistPicker(target) => {
                self.playlist_picker = if self.playlist_picker == target {
                    None
                } else {
                    target
                };
                return Ok(Task::none());
            }
            Msg::PlaylistSort(pid, sort_type) => {
                self.db.conn.execute(
                    "UPDATE playlists SET sort_type=?,needs_sync=1,local_modified=max(?,local_modified+1) WHERE id=?",
                    rusqlite::params![sort_type, db::now(), pid],
                )?;
                self.playlists = self.db.rows(
                    "SELECT * FROM playlists WHERE is_deleted=0 ORDER BY sort_position,title",
                )?;
                self.reload_episodes()?;
                return Ok(self.request_sync());
            }
            Msg::PlayAll => {
                if let Some(&first) = self.view_ids.first() {
                    let ids = self.view_ids.clone();
                    let (local_ids, remote_ids) = self.db.partition_local_remote_ids(&ids)?;
                    let first_ep = self.db.episode(first)?;
                    if first_ep.is_local() {
                        self.db.set_local_queue(&local_ids)?;
                    } else {
                        self.db.reorder_queue(&remote_ids)?;
                        sync_needed = true;
                    }
                    self.play(first)?;
                    reload_eps = true;
                }
            }
            Msg::QueueAllView => {
                if !self.view_ids.is_empty() {
                    let ids = self.view_ids.clone();
                    let (local_ids, remote_ids) = self.db.partition_local_remote_ids(&ids)?;
                    if !local_ids.is_empty() {
                        let mut cur_queue = self.db.queue_for(true)?;
                        let existing: HashSet<i64> = cur_queue.iter().copied().collect();
                        for id in local_ids {
                            if !existing.contains(&id) {
                                cur_queue.push(id);
                            }
                        }
                        self.db.set_local_queue(&cur_queue)?;
                    }
                    if !remote_ids.is_empty() {
                        self.db.edit_queue_batch(&remote_ids, 3)?;
                        sync_needed = true;
                    }
                    if matches!(self.nav.as_str(), "Up Next" | "Local queue") {
                        reload_eps = true;
                    } else {
                        reload_summaries = true;
                    }
                }
            }
            Msg::ToggleAccountAllDays => {
                self.account_show_all_days = !self.account_show_all_days;
                return Ok(Task::none());
            }
            Msg::FtpStatsDir(value) => {
                self.ftp_stats_dir_input = value;
                return Ok(Task::none());
            }
            Msg::SaveFtpStatsDir => {
                let trimmed = self.ftp_stats_dir_input.trim().to_owned();
                if trimmed.is_empty() {
                    self.db.set("ftp_stats_dir", "")?;
                    self.ftp_stats_dir_input.clear();
                } else {
                    let resolved = dusky_casts::local::directory(&trimmed)?;
                    let utf8 = resolved
                        .to_str()
                        .context("FTP stats folder path must be UTF-8")?;
                    self.db.set("ftp_stats_dir", utf8)?;
                    self.ftp_stats_dir_input = utf8.to_owned();
                }
                return self.handle(Msg::SyncLocalStats);
            }
            Msg::ClearFtpStatsDir => {
                self.ftp_stats_dir_input.clear();
                self.db.set("ftp_stats_dir", "")?;
                self.db.set("discovered_ftp_stats_dir", "")?;
                return self.handle(Msg::SyncLocalStats);
            }
            Msg::SyncLocalStats => {
                self.listening.flush(&mut self.db)?;
                let report = dusky_casts::listening::sync_local_stats_files_report(&mut self.db)?;
                self.status = if report.imported_days > 0 {
                    format!(
                        "Synced {} iPhone days ({} updated)",
                        report.imported_days, report.updated_days
                    )
                } else if report.pc_exported {
                    format!(
                        "Exported PC daily stats to {} folder(s); waiting for iPhone FTP sync",
                        report.export_dirs.len()
                    )
                } else {
                    "PC daily stats up to date; no iPhone manifest found yet".into()
                };
                self.status_is_error = false;
                self.stats_report = report;
                self.refresh_account_stats();
                return Ok(Task::none());
            }
            Msg::CheckLocalDrive => {
                self.local_drive_locked = !self.db.is_local_drive_accessible(&self.local_path);
                if self.local_drive_locked {
                    self.status =
                        "Media drive is still locked or unmounted — queue retained".into();
                    self.status_is_error = true;
                    return Ok(Task::none());
                }
                self.player_error_latched = false;
                if let Some(p) = &self.player {
                    p.clear_error();
                }
                if self.player.as_ref().is_some_and(|p| !p.state().loaded) {
                    self.player.take();
                }
                if let Ok(report) =
                    dusky_casts::listening::sync_local_stats_files_report(&mut self.db)
                {
                    self.stats_report = report;
                }
                self.refresh_account_stats();
                self.status = "Media drive unlocked and ready".into();
                self.status_is_error = false;
                self.reload_episodes()?;
                return Ok(Task::none());
            }
            Msg::Sleep(minutes) => {
                self.sleep = None;
                self.sleep_eof = false;
                if minutes == 1 {
                    self.sleep_eof = true;
                } else if minutes > 1 {
                    self.sleep = Some(Instant::now() + Duration::from_secs(minutes * 60));
                }
                return Ok(Task::none());
            }
        }
        if reload_all {
            self.reload()?;
            self.validate_nav();
        } else if reload_eps {
            self.reload_episodes()?;
        } else if reload_summaries {
            self.refresh_summaries();
            self.refresh_info_playlists();
            if self.queue_drawer_open {
                self.reload_drawer();
            }
            self.sync_ipc();
        }
        Ok(if sync_needed {
            self.request_sync()
        } else {
            Task::none()
        })
    }

    fn update(&mut self, msg: Msg) -> Task<Msg> {
        match self.handle(msg) {
            Ok(task) => task,
            Err(e) => {
                self.status = format!("{e:#}");
                self.status_is_error = true;
                Task::none()
            }
        }
    }

    fn subscription(&self) -> Subscription<Msg> {
        let tick_interval = if self.screenshot.is_some() {
            Duration::from_millis(250)
        } else if self.player.as_ref().is_some_and(|p| !p.state().paused)
            || !self.downloads.is_empty()
            || self.sleep.is_some()
            || self.quit_after.is_some()
            || self.closing.is_some()
        {
            Duration::from_millis(1000)
        } else {
            Duration::from_millis(5000)
        };
        Subscription::batch([
            iced::time::every(tick_interval).map(|_| Msg::Tick),
            Subscription::run(ipc_stream),
            iced::event::listen_with(|event, status, _| match event {
                iced::Event::Window(iced::window::Event::CloseRequested) => Some(Msg::Close),
                iced::Event::Window(iced::window::Event::Focused) => Some(Msg::Focused),
                iced::Event::Window(
                    iced::window::Event::Opened { size, .. } | iced::window::Event::Resized(size),
                ) => Some(Msg::Resized(size)),
                iced::Event::Keyboard(iced::keyboard::Event::ModifiersChanged(m)) => {
                    Some(Msg::Shift(m.shift()))
                }
                iced::Event::Keyboard(iced::keyboard::Event::KeyPressed {
                    key: iced::keyboard::Key::Character(c),
                    modifiers,
                    ..
                }) if modifiers.control() => match c.as_str() {
                    "q" => Some(Msg::Close),
                    "r" => Some(Msg::Refresh),
                    "n" => Some(Msg::Nav("Discover".into())),
                    "," => Some(Msg::TogglePlaybackSettings),
                    _ => None,
                },
                iced::Event::Keyboard(iced::keyboard::Event::KeyPressed {
                    key: iced::keyboard::Key::Named(key),
                    ..
                }) => match key {
                    iced::keyboard::key::Named::Escape => Some(Msg::Escape),
                    iced::keyboard::key::Named::MediaTrackNext
                    | iced::keyboard::key::Named::MediaFastForward => Some(Msg::MediaNext),
                    iced::keyboard::key::Named::MediaTrackPrevious
                    | iced::keyboard::key::Named::MediaRewind => Some(Msg::MediaPrev),
                    iced::keyboard::key::Named::MediaPlayPause => Some(Msg::Pause),
                    iced::keyboard::key::Named::MediaPlay => Some(Msg::MediaPlay),
                    iced::keyboard::key::Named::MediaPause => Some(Msg::MediaPause),
                    iced::keyboard::key::Named::Space if status == iced::event::Status::Ignored => {
                        Some(Msg::Pause)
                    }
                    iced::keyboard::key::Named::ArrowLeft
                        if status == iced::event::Status::Ignored =>
                    {
                        Some(Msg::Skip(-15.0))
                    }
                    iced::keyboard::key::Named::ArrowRight
                        if status == iced::event::Status::Ignored =>
                    {
                        Some(Msg::Skip(30.0))
                    }
                    iced::keyboard::key::Named::PageUp
                        if status == iced::event::Status::Ignored =>
                    {
                        Some(Msg::PageScroll(-1))
                    }
                    iced::keyboard::key::Named::PageDown
                        if status == iced::event::Status::Ignored =>
                    {
                        Some(Msg::PageScroll(1))
                    }
                    iced::keyboard::key::Named::Home if status == iced::event::Status::Ignored => {
                        Some(Msg::PageScroll(-2))
                    }
                    iced::keyboard::key::Named::End if status == iced::event::Status::Ignored => {
                        Some(Msg::PageScroll(2))
                    }
                    _ => None,
                },
                _ => None,
            }),
        ])
    }
}

async fn debounce_filter(generation: u64) -> u64 {
    let (tx, rx) = tokio::sync::oneshot::channel();
    let _ = std::thread::Builder::new()
        .name("dusky_casts-debounce".into())
        .spawn(move || {
            std::thread::sleep(Duration::from_millis(SEARCH_DEBOUNCE_MS));
            let _ = tx.send(generation);
        });
    rx.await.unwrap_or(generation)
}

async fn run_artwork_job(batch: Vec<(String, String)>) -> Vec<(String, u32, u32, Vec<u8>)> {
    let (send, receive) = tokio::sync::oneshot::channel();
    let spawned = std::thread::Builder::new()
        .name("dusky_casts-artwork".into())
        .spawn(move || {
            let api = Api::new(String::new()).ok();
            let mut decoded = Vec::new();
            for (uuid, url) in batch {
                if !library::artwork_path(&uuid).is_file()
                    && !url.is_empty()
                    && let Some(api) = &api
                {
                    let _ = library::cache_artwork(api, &uuid, &url);
                }
                if let Some((w, h, pixels)) =
                    library::decode_artwork_rgba(&uuid, ARTWORK_DECODE_DIM)
                {
                    decoded.push((uuid, w, h, pixels));
                }
            }
            let _ = send.send(decoded);
        });
    if spawned.is_ok() {
        receive.await.unwrap_or_default()
    } else {
        Vec::new()
    }
}

async fn run_local_stats_job(path: PathBuf) -> std::result::Result<StatsSyncReport, String> {
    let (send, receive) = tokio::sync::oneshot::channel();
    match std::thread::Builder::new()
        .name("dusky_casts-local-stats".into())
        .spawn(move || {
            let res = Db::open(path)
                .and_then(|mut db| dusky_casts::listening::sync_local_stats_files_report(&mut db))
                .map_err(|e| format!("{e:#}"));
            let _ = send.send(res);
        }) {
        Ok(_) => receive
            .await
            .unwrap_or_else(|_| Err("Local stats worker stopped unexpectedly".into())),
        Err(error) => Err(error.to_string()),
    }
}

async fn run_job(path: PathBuf, job: Job) -> std::result::Result<Outcome, String> {
    let (send, receive) = tokio::sync::oneshot::channel();
    match std::thread::Builder::new()
        .name("dusky_casts-network".into())
        .spawn(move || {
            let _ = send.send(work(path, job).map_err(|e| format!("{e:#}")));
        }) {
        Ok(_) => receive
            .await
            .unwrap_or_else(|_| Err("Network worker stopped unexpectedly".into())),
        Err(error) => Err(error.to_string()),
    }
}

fn work(path: PathBuf, job: Job) -> Result<Outcome> {
    let account_operation = matches!(&job, Job::Sync | Job::Login(..));
    let mut db = Db::open(path)?;
    let mut api = Api::new(db.get("account_token")?)?;
    let result = (|| -> Result<Outcome> {
        let outcome = match job {
            Job::LocalScan(path) => {
                let result = dusky_casts::local::scan(&mut db, &path)?;
                let report = dusky_casts::listening::sync_local_stats_files_report(&mut db).ok();
                let message = if result.drive_locked {
                    "Encrypted media drive is locked or unmounted — library and queue retained"
                        .into()
                } else {
                    format!(
                        "Local library: {} audio files · {} inspected · {} skipped",
                        result.audio, result.probed, result.skipped
                    )
                };
                Outcome::Status {
                    message,
                    reload_db: true,
                    stats_report: report,
                }
            }
            Job::Login(email, password) => {
                let account = api.login(email, password)?;
                let tx = db.conn.transaction()?;
                for (key, value) in [
                    ("account_token", account.token.as_str()),
                    ("account_uuid", account.uuid.as_str()),
                    ("account_email", account.email.as_str()),
                    ("sync_cursor", "0"),
                    ("upnext_cursor", "0"),
                    ("rust_import_complete", ""),
                    ("rust_queue_initialized", ""),
                    ("stats_ack_seconds", ""),
                    ("stats_other_seconds", ""),
                    ("stats_summary_at", ""),
                    ("stats_error", ""),
                ] {
                    tx.execute(
                        "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        rusqlite::params![key, value],
                    )?;
                }
                tx.commit()?;
                api.token = account.token;
                sync::run(&mut db, &api)?;
                let report = dusky_casts::listening::sync_local_stats_files_report(&mut db).ok();
                Outcome::Status {
                    message: "Signed in; library imported and synced".into(),
                    reload_db: true,
                    stats_report: report,
                }
            }
            Job::Sync => {
                let report = dusky_casts::listening::sync_local_stats_files_report(&mut db).ok();
                sync::run(&mut db, &api)?;
                Outcome::Status {
                    message: "Sync complete".into(),
                    reload_db: true,
                    stats_report: report,
                }
            }
            Job::Refresh => {
                let count = library::refresh(&mut db, &api)?;
                Outcome::Status {
                    message: format!("Refreshed {count} podcasts"),
                    reload_db: true,
                    stats_report: None,
                }
            }
            Job::RefreshPodcast(id) => {
                library::refresh_one(&mut db, &api, id)?;
                Outcome::Status {
                    message: "Podcast refreshed".into(),
                    reload_db: true,
                    stats_report: None,
                }
            }
            Job::Feed(url) => {
                library::add_feed(&mut db, &api, &url)?;
                Outcome::Status {
                    message: "Feed added".into(),
                    reload_db: true,
                    stats_report: None,
                }
            }
            Job::Search(query) => Outcome::Search(library::search(&api, &query)?),
            Job::Subscribe(value) => {
                library::subscribe_search(&mut db, &api, &value)?;
                if !api.token.is_empty() {
                    sync::run(&mut db, &api)?;
                }
                Outcome::Status {
                    message: "Subscribed".into(),
                    reload_db: true,
                    stats_report: None,
                }
            }
            Job::Download(id, control) => {
                library::download_with(&db, &api, id, &control)?;
                Outcome::Status {
                    message: "Download complete".into(),
                    reload_db: true,
                    stats_report: None,
                }
            }
        };
        Ok(outcome)
    })();
    if account_operation {
        let _ = db.set(
            "last_sync_error",
            &result
                .as_ref()
                .err()
                .map(|e| format!("{e:#}"))
                .unwrap_or_default(),
        );
    }
    result
}

fn primary_drm_card() -> Option<String> {
    if let Ok(devices) = std::env::var("AQ_DRM_DEVICES")
        && let Some(card) = devices
            .split(':')
            .next()
            .and_then(|dev| std::path::Path::new(dev).file_name())
            .and_then(|n| n.to_str())
            .filter(|n| !n.is_empty())
    {
        return Some(card.to_owned());
    }
    let entries = std::fs::read_dir("/sys/class/drm").ok()?;
    let mut cards: Vec<String> = entries
        .flatten()
        .filter_map(|e| e.file_name().into_string().ok())
        .filter(|name| name.starts_with("card") && name[4..].chars().all(|c| c.is_ascii_digit()))
        .collect();
    cards.sort();
    for card in &cards {
        if let Ok(boot) = std::fs::read_to_string(format!("/sys/class/drm/{card}/device/boot_vga"))
            && boot.trim() == "1"
        {
            return Some(card.clone());
        }
    }
    cards.into_iter().next()
}

fn has_hardware_vulkan_icd(tokens: &[&str]) -> bool {
    for dir in ["/usr/share/vulkan/icd.d", "/etc/vulkan/icd.d"] {
        let Ok(entries) = std::fs::read_dir(dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_ascii_lowercase();
            if !name.ends_with(".json") || name.contains("lvp") || name.contains("lavapipe") {
                continue;
            }
            if tokens.iter().any(|tok| name.contains(tok)) {
                return true;
            }
        }
    }
    false
}

fn configure_renderer_backend() {
    if std::env::var_os("ICED_BACKEND").is_some() {
        return;
    }
    if std::env::var("DUSKY_CASTS_GPU").as_deref() == Ok("0") {
        unsafe {
            std::env::set_var("ICED_BACKEND", "tiny-skia");
        }
        return;
    }
    let forced_gpu = std::env::var("DUSKY_CASTS_GPU").as_deref() == Ok("1");
    let vendor = primary_drm_card().and_then(|card| {
        std::fs::read_to_string(format!("/sys/class/drm/{card}/device/vendor"))
            .ok()
            .map(|v| v.trim().to_owned())
    });
    let matched = vendor.as_deref().and_then(|v| match v {
        "0x8086" => Some(("*intel*,*anv*", &["intel", "anv"][..])),
        "0x1002" => Some(("*radeon*,*amd*,*radv*", &["radeon", "amd", "radv"][..])),
        "0x10de" => Some((
            "*nvidia*,*nouveau*,*nvk*",
            &["nvidia", "nouveau", "nvk"][..],
        )),
        _ => None,
    });
    if let Some((glob, tokens)) = matched
        && has_hardware_vulkan_icd(tokens)
    {
        if std::env::var_os("VK_LOADER_DRIVERS_SELECT").is_none() {
            unsafe {
                std::env::set_var("VK_LOADER_DRIVERS_SELECT", glob);
            }
        }
        if std::env::var_os("WGPU_BACKEND").is_none() {
            unsafe {
                std::env::set_var("WGPU_BACKEND", "vulkan");
            }
        }
        if std::env::var_os("WGPU_POWER_PREFERENCE").is_none() {
            unsafe {
                std::env::set_var("WGPU_POWER_PREFERENCE", "low_power");
            }
        }
        return;
    }
    if !forced_gpu {
        unsafe {
            std::env::set_var("ICED_BACKEND", "tiny-skia");
        }
    } else if std::env::var_os("WGPU_POWER_PREFERENCE").is_none() {
        unsafe {
            std::env::set_var("WGPU_POWER_PREFERENCE", "low_power");
        }
    }
}

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if let Some(command) = match args.as_slice() {
        [flag] if flag == "--media-next" => Some("next\n"),
        [flag] if flag == "--media-prev" => Some("prev\n"),
        [flag] if flag == "--play-pause" => Some("play-pause\n"),
        _ => None,
    } {
        let mut socket =
            std::os::unix::net::UnixStream::connect(db::default_path().with_extension("sock"))
                .context("Dusky Casts is not running")?;
        std::io::Write::write_all(&mut socket, command.as_bytes())?;
        return Ok(());
    }
    if args.iter().any(|s| s == "--version") {
        println!("dusky_casts {}", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }
    if args.iter().any(|s| s == "--help") {
        println!(
            "dusky_casts — Wayland podcast and local audio player\n\n--help\n--version\n--media-next\n--media-prev\n--play-pause\n--quit-after SECONDS  Close after a UI smoke test\n\nUses XDG_DATA_HOME/dusky_casts/library.db and mpv JSON IPC."
        );
        return Ok(());
    }
    let quit_after = if args.len() == 2 && args[0] == "--quit-after" {
        Some(Duration::from_secs(args[1].parse()?))
    } else {
        anyhow::ensure!(args.is_empty(), "Unknown arguments; use --help");
        None
    };
    let path = db::default_path();
    std::fs::create_dir_all(path.parent().context("Library has no parent directory")?)?;
    let lock = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(path.with_extension("lock"))?;
    lock.try_lock().context("Dusky Casts is already running")?;
    db::migrate_legacy_library(&path)?;
    configure_renderer_backend();
    let db = Db::open(path)?;
    let app = App::new(db, quit_after)?;
    let app = Mutex::new(Some(app));
    iced::application(
        move || {
            let mut booted = app
                .lock()
                .unwrap()
                .take()
                .expect("Application booted twice");
            let task = booted.boot_task();
            (booted, task)
        },
        App::update,
        App::view,
    )
    .title("Dusky Casts")
    .settings(iced::Settings {
        default_font: iced::Font::with_name("Atkinson Hyperlegible"),
        default_text_size: 14.0.into(),
        ..Default::default()
    })
    .theme(|app: &App| app.appearance.iced.clone())
    .subscription(App::subscription)
    .window(iced::window::Settings {
        size: iced::Size::new(1200.0, 850.0),
        min_size: Some(iced::Size::new(600.0, 540.0)),
        exit_on_close_request: false,
        platform_specific: iced::window::settings::PlatformSpecific {
            application_id: "dusky_casts".into(),
            ..Default::default()
        },
        ..Default::default()
    })
    .run()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use dusky_casts::db::n;

    fn app() -> (tempfile::TempDir, App) {
        let dir = tempfile::tempdir().unwrap();
        let mut db = Db::open(dir.path().join("library.db")).unwrap();
        library::import_cache(
            &mut db,
            "pod",
            &serde_json::json!({
                "uuid": "pod",
                "title": "Fixture",
                "episodes": [{
                    "uuid": "a",
                    "title": "Episode",
                    "url": "https://example.invalid/audio",
                    "duration": 100
                }]
            }),
        )
        .unwrap();
        db.set("speed", "1.75").unwrap();
        db.set("trim", "low").unwrap();
        db.set("volume", "0.5").unwrap();
        (dir, App::new(db, None).unwrap())
    }

    #[test]
    fn legacy_preferences_and_manual_playlist_order_are_preserved() {
        let (_dir, mut app) = app();
        assert_eq!(app.speed, 1.75);
        assert_eq!(app.trim, "low");
        assert_eq!(app.volume, 50.0);
        app.name = "New playlist".into();
        let _ = app.handle(Msg::CreatePlaylist).unwrap();
        assert_eq!(app.playlists[0]["sort_type"], 4);
        let id = n(&app.playlists[0], "id");
        let _ = app
            .handle(Msg::StartRename(false, id, "New playlist".into()))
            .unwrap();
        let _ = app.handle(Msg::EditRename("Renamed".into())).unwrap();
        let _ = app.handle(Msg::CommitRename).unwrap();
        assert_eq!(app.playlists[0]["title"], "Renamed");
    }

    #[test]
    fn sleep_expiry_keeps_an_already_paused_player_paused() {
        let (_dir, mut app) = app();
        app.player = Some(Player::start("off", 1.0, 0.0, true).unwrap());
        app.sleep = Some(Instant::now() - Duration::from_secs(1));
        let _ = app.handle(Msg::Tick).unwrap();
        assert!(app.sleep.is_none());
        assert_eq!(
            app.player
                .as_mut()
                .unwrap()
                .call(serde_json::json!(["get_property", "pause"]))
                .unwrap(),
            true
        );
    }

    #[test]
    fn queue_exhaustion_preserves_completed_episode_status() {
        let (_dir, mut app) = app();
        let episode = app.first_episode().unwrap();
        let id = episode.id;
        app.db.change(id, "playing_status", 2).unwrap();
        app.db.edit_queue(id, 3).unwrap();
        app.current = Some(episode);
        app.next().unwrap();
        assert!(app.current.is_none());
        assert!(app.db.queue().unwrap().is_empty());
        assert_eq!(app.db.episode(id).unwrap().playing_status, 2);
    }

    #[test]
    fn local_queue_exhaustion_does_not_start_the_phone_queue() {
        let (_dir, mut app) = app();
        let remote = app.view_ids[0];
        app.db.edit_queue(remote, 3).unwrap();
        app.db
            .conn
            .execute(
                "INSERT INTO podcasts(uuid,title,feed_url,added_at,subscribed) VALUES ('local-library','Local audio','local://library',0,0)",
                [],
            )
            .unwrap();
        app.db
            .conn
            .execute(
                "INSERT INTO episodes(uuid,podcast_id,title,audio_url,playing_status,added_at) SELECT 'local:test',id,'Local','file:///tmp/local.wav',2,0 FROM podcasts WHERE uuid='local-library'",
                [],
            )
            .unwrap();
        let id = app.db.conn.last_insert_rowid();
        app.db
            .conn
            .execute(
                "INSERT INTO local_files(path,episode_id,size,modified,available) VALUES ('file:///tmp/local.wav',?,1,'1',1)",
                [id],
            )
            .unwrap();
        app.db.edit_queue(id, 3).unwrap();
        app.current = Some(app.db.episode(id).unwrap());
        app.next().unwrap();
        assert!(app.current.is_none());
        assert!(app.db.queue_for(true).unwrap().is_empty());
        assert_eq!(app.db.queue().unwrap(), [remote]);
        assert_eq!(app.db.episode(id).unwrap().playing_status, 2);
    }

    #[test]
    fn signing_out_clears_account_stat_cache_but_keeps_pc_listening() {
        let (_dir, mut app) = app();
        app.db.set("account_token", "fixture-token").unwrap();
        app.db.set("stats_ack_seconds", "12").unwrap();
        app.db.set("stats_other_seconds", "900").unwrap();
        app.db.set("stats_summary_at", "1").unwrap();
        app.db
            .conn
            .execute(
                "INSERT INTO listening_days(day,milliseconds) VALUES ('2026-10-26',12500)",
                [],
            )
            .unwrap();
        let _ = app.handle(Msg::Logout).unwrap();
        assert!(app.db.get("account_token").unwrap().is_empty());
        assert!(app.db.get("stats_other_seconds").unwrap().is_empty());
        assert!(app.db.get("stats_summary_at").unwrap().is_empty());
        assert_eq!(dusky_casts::stats::total_ms(&app.db).unwrap(), 12500);
        assert_eq!(
            dusky_casts::stats::snapshot(&app.db)
                .unwrap()
                .unwrap()
                .seconds,
            12
        );
    }

    #[test]
    fn locked_encrypted_drive_retains_current_episode_and_local_queue() {
        let dir = tempfile::tempdir().unwrap();
        let media_dir = dir.path().join("encrypted_mount");
        std::fs::create_dir_all(&media_dir).unwrap();
        let db_path = dir.path().join("library.db");
        let mut db = Db::open(&db_path).unwrap();

        db.set("local_library_path", media_dir.to_str().unwrap())
            .unwrap();
        db.conn
            .execute(
                "INSERT INTO podcasts(uuid,title,feed_url,added_at,subscribed) VALUES ('local-library','Local audio','local://library',0,0)",
                [],
            )
            .unwrap();
        let file_url = reqwest::Url::from_file_path(media_dir.join("track01.mp3"))
            .unwrap()
            .to_string();
        db.conn
            .execute(
                "INSERT INTO episodes(uuid,podcast_id,title,description,audio_url,playing_status,position,added_at) SELECT 'local:1',id,'Track 1','track01.mp3',?,1,42000,0 FROM podcasts WHERE uuid='local-library'",
                [&file_url],
            )
            .unwrap();
        let ep_id = db.conn.last_insert_rowid();
        db.conn
            .execute(
                "INSERT INTO local_files(path,episode_id,size,modified,available,file_name,leading_num) VALUES (?,?,1024,'1:0:1:0',1,'track01.mp3',1)",
                rusqlite::params![file_url, ep_id],
            )
            .unwrap();
        db.set_local_queue(&[ep_id]).unwrap();
        db.set("last_episode_id", &ep_id.to_string()).unwrap();

        let scan_res = dusky_casts::local::scan(&mut db, media_dir.to_str().unwrap()).unwrap();
        assert!(scan_res.drive_locked);
        assert_eq!(db.queue_for(true).unwrap(), vec![ep_id]);

        let app = App::new(db, None).unwrap();
        assert!(app.local_drive_locked);
        assert_eq!(app.current.as_ref().map(|e| e.id), Some(ep_id));
        assert_eq!(app.current.as_ref().unwrap().position, 42000);
    }

    #[test]
    fn two_way_ios_and_pc_daily_stats_sync_merges_without_contaminating_cloud_total() {
        let dir = tempfile::tempdir().unwrap();
        let mut db = Db::open(dir.path().join("library.db")).unwrap();
        db.conn
            .execute(
                "INSERT INTO listening_days(day,milliseconds) VALUES ('2026-11-18', 1800000)",
                [],
            )
            .unwrap();
        let ios_manifest = serde_json::json!({
            "device_id": "iphone-uuid",
            "device_name": "iPhone",
            "updated_at": 1763450000,
            "days_seconds": {
                "2026-11-18": 1200.5,
                "2026-11-17": 600.0
            }
        });
        std::fs::write(
            dir.path().join(dusky_casts::listening::IOS_STATS_FILENAME),
            serde_json::to_vec(&ios_manifest).unwrap(),
        )
        .unwrap();

        let count = dusky_casts::listening::sync_local_stats_files(&mut db).unwrap();
        assert_eq!(count, 2);

        assert_eq!(dusky_casts::stats::total_ms(&db).unwrap(), 1_800_000);

        let ios_nov18: i64 = db
            .conn
            .query_row(
                "SELECT milliseconds FROM ios_listening_days WHERE day='2026-11-18'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(ios_nov18, 1_200_500);
        assert!(
            dir.path()
                .join(dusky_casts::listening::PC_STATS_FILENAME)
                .is_file()
        );
    }

    #[test]
    fn bulk_queue_and_partition_local_remote_ids_scale_cleanly() {
        let (_dir, mut app) = app();
        app.db
            .conn
            .execute(
                "INSERT INTO podcasts(uuid,title,feed_url,added_at,subscribed) VALUES ('local-library','Local audio','local://library',0,0)",
                [],
            )
            .unwrap();
        let pid: i64 = app
            .db
            .conn
            .query_row(
                "SELECT id FROM podcasts WHERE uuid='local-library'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let mut local_ids = Vec::new();
        for i in 1..=4 {
            let uuid = format!("local:{i}");
            let url = format!("file:///tmp/track{i}.mp3");
            app.db
                .conn
                .execute(
                    "INSERT INTO episodes(uuid,podcast_id,title,audio_url,added_at) VALUES (?,?,?,?,0)",
                    rusqlite::params![uuid, pid, format!("Track {i}"), url],
                )
                .unwrap();
            let id = app.db.conn.last_insert_rowid();
            app.db
                .conn
                .execute(
                    "INSERT INTO local_files(path,episode_id,size,modified,available) VALUES (?,?,100,'1',1)",
                    rusqlite::params![url, id],
                )
                .unwrap();
            local_ids.push(id);
        }
        let remote_id = app.view_ids[0];
        let all = [
            local_ids[0],
            remote_id,
            local_ids[1],
            local_ids[2],
            local_ids[3],
        ];
        let (loc, rem) = app.db.partition_local_remote_ids(&all).unwrap();
        assert_eq!(loc, local_ids);
        assert_eq!(rem, vec![remote_id]);

        app.db.edit_queue_batch(&local_ids[..2], 3).unwrap();
        app.db.edit_queue_batch(&local_ids[2..], 2).unwrap();
        assert_eq!(
            app.db.queue_for(true).unwrap(),
            vec![local_ids[2], local_ids[3], local_ids[0], local_ids[1]]
        );
    }

    #[test]
    fn unmounted_root_mountpoint_with_mismatched_st_dev_retains_library() {
        let dir = tempfile::tempdir().unwrap();
        let media_dir = dir.path().join("encrypted_mount");
        std::fs::create_dir_all(&media_dir).unwrap();
        let mut db = Db::open(dir.path().join("library.db")).unwrap();

        db.set("local_library_path", media_dir.to_str().unwrap())
            .unwrap();
        db.set("local_library_dev", "999999999").unwrap();
        db.conn
            .execute(
                "INSERT INTO podcasts(uuid,title,feed_url,added_at,subscribed) VALUES ('local-library','Local audio','local://library',0,0)",
                [],
            )
            .unwrap();
        let missing_url = reqwest::Url::from_file_path(media_dir.join("locked_album.flac"))
            .unwrap()
            .to_string();
        db.conn
            .execute(
                "INSERT INTO episodes(uuid,podcast_id,title,audio_url,added_at) SELECT 'local:locked',id,'Locked','file:///missing',0 FROM podcasts WHERE uuid='local-library'",
                [],
            )
            .unwrap();
        let ep_id = db.conn.last_insert_rowid();
        db.conn
            .execute(
                "INSERT INTO local_files(path,episode_id,size,modified,available) VALUES (?,?,1024,'1',1)",
                rusqlite::params![missing_url, ep_id],
            )
            .unwrap();
        db.set_local_queue(&[ep_id]).unwrap();

        std::fs::write(media_dir.join("README.txt"), b"mountpoint placeholder").unwrap();
        assert!(!db.is_local_drive_accessible(media_dir.to_str().unwrap()));
        let res = dusky_casts::local::scan(&mut db, media_dir.to_str().unwrap()).unwrap();
        assert!(res.drive_locked);
        assert_eq!(db.queue_for(true).unwrap(), vec![ep_id]);
    }

    #[test]
    fn sub_millisecond_listening_accumulator_preserves_fractional_nanoseconds() {
        let mut stats = dusky_casts::listening::Listening::default();
        let base = Instant::now();
        let wall = chrono::Local::now();
        stats.sample(base, wall, Some((1, 0, 0.0)));
        for i in 1..=10 {
            stats.sample(
                base + Duration::from_micros(i * 500_400),
                wall + chrono::Duration::microseconds((i * 500_400) as i64),
                Some((1, 0, i as f64)),
            );
        }
        assert_eq!(stats.pending().values().sum::<i64>(), 5004);
    }
}
