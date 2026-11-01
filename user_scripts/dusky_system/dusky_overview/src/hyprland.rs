use serde::Deserialize;
use std::collections::BTreeMap;
use std::env;
use std::fmt;
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::Duration;

#[derive(Debug)]
pub enum HyprError {
    MissingEnv(&'static str),
    Io(std::io::Error),
    Json(serde_json::Error),
    DispatchFailed(String),
}

impl fmt::Display for HyprError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MissingEnv(var) => write!(f, "Missing environment variable: {var}"),
            Self::Io(err) => write!(f, "Hyprland IPC I/O error: {err}"),
            Self::Json(err) => write!(f, "Hyprland JSON parse error: {err}"),
            Self::DispatchFailed(resp) => write!(f, "Hyprland Lua dispatch error: {resp}"),
        }
    }
}

impl std::error::Error for HyprError {}

impl From<std::io::Error> for HyprError {
    fn from(err: std::io::Error) -> Self {
        Self::Io(err)
    }
}

impl From<serde_json::Error> for HyprError {
    fn from(err: serde_json::Error) -> Self {
        Self::Json(err)
    }
}

/// Canonical identifier for a Hyprland 0.57 workspace.
/// Normal workspaces have a positive numeric `id`,
/// whereas special workspaces have negative IDs (e.g. `-99`) or names starting with `"special:"`.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum WorkspaceKey {
    Normal(i32),
    Named(String),
}

impl WorkspaceKey {
    pub fn from_raw(id: Option<i32>, name: &str, ws_type: &str) -> Self {
        let trimmed = name.trim();
        if ws_type == "special"
            || trimmed == "special"
            || trimmed.starts_with("special:")
            || id.is_some_and(|i| i < 0)
        {
            let canonical = if trimmed.starts_with("special:") {
                trimmed.to_owned()
            } else if trimmed == "special" || trimmed.is_empty() {
                "special:magic".to_owned()
            } else {
                format!("special:{trimmed}")
            };
            return Self::Named(canonical);
        }
        if let Some(id) = id
            && id > 0
        {
            return Self::Normal(id);
        }
        if let Ok(parsed_id) = trimmed.parse::<i32>()
            && parsed_id > 0
        {
            return Self::Normal(parsed_id);
        }
        Self::Named(trimmed.to_owned())
    }

    pub fn badge_label(&self) -> String {
        match self {
            Self::Normal(id) => id.to_string(),
            Self::Named(name) => {
                if let Some(stripped) = name.strip_prefix("special:") {
                    if stripped.is_empty() {
                        "✦ SPECIAL".to_owned()
                    } else {
                        format!("✦ {}", stripped.to_ascii_uppercase())
                    }
                } else {
                    name.to_ascii_uppercase()
                }
            }
        }
    }
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct WorkspaceRef {
    #[serde(default)]
    pub id: Option<i32>,
    #[serde(default)]
    pub name: String,
    #[serde(rename = "type", default)]
    pub ws_type: String,
}

impl WorkspaceRef {
    pub fn key(&self) -> WorkspaceKey {
        WorkspaceKey::from_raw(self.id, &self.name, &self.ws_type)
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct Client {
    pub address: String,
    #[serde(default)]
    pub mapped: bool,
    #[serde(default)]
    pub hidden: bool,
    pub at: [i32; 2],
    pub size: [i32; 2],
    pub workspace: WorkspaceRef,
    #[serde(default)]
    pub floating: bool,
    #[serde(default)]
    pub pinned: bool,
    #[serde(rename = "focusHistoryID", default)]
    pub focus_history_id: i32,
    #[serde(default)]
    pub title: String,
    #[serde(rename = "initialTitle", default)]
    #[allow(dead_code)]
    pub initial_title: String,
    #[serde(default)]
    pub class: String,
    #[serde(rename = "initialClass", default)]
    #[allow(dead_code)]
    pub initial_class: String,
    #[serde(default)]
    pub monitor: Option<i32>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Workspace {
    #[serde(default)]
    pub id: Option<i32>,
    #[serde(default)]
    pub name: String,
    #[serde(rename = "type", default)]
    pub ws_type: String,
    #[serde(default)]
    pub monitor: String,
    #[serde(rename = "monitorID", default)]
    pub monitor_id: Option<i32>,
    #[serde(default)]
    #[allow(dead_code)]
    pub windows: usize,
}

impl Workspace {
    pub fn key(&self) -> WorkspaceKey {
        WorkspaceKey::from_raw(self.id, &self.name, &self.ws_type)
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct Monitor {
    pub id: i32,
    pub name: String,
    #[serde(default)]
    pub x: i32,
    #[serde(default)]
    pub y: i32,
    pub width: i32,
    pub height: i32,
    #[serde(default = "default_scale")]
    pub scale: f64,
    #[serde(default)]
    pub transform: u8,
    #[serde(default)]
    pub focused: bool,
    #[serde(rename = "activeWorkspace")]
    pub active_workspace: WorkspaceRef,
    #[serde(rename = "specialWorkspace", default)]
    pub special_workspace: WorkspaceRef,
}

fn default_scale() -> f64 {
    1.0
}

impl Monitor {
    pub fn logical_width(&self) -> f32 {
        let s = if self.scale > 0.0 { self.scale } else { 1.0 };
        let raw = if self.transform % 2 == 1 {
            self.height
        } else {
            self.width
        };
        (raw as f64 / s).round().max(1.0) as f32
    }

    pub fn logical_height(&self) -> f32 {
        let s = if self.scale > 0.0 { self.scale } else { 1.0 };
        let raw = if self.transform % 2 == 1 {
            self.width
        } else {
            self.height
        };
        (raw as f64 / s).round().max(1.0) as f32
    }

    pub fn active_special_key(&self) -> Option<WorkspaceKey> {
        let has_name = !self.special_workspace.name.trim().is_empty();
        let has_id = self.special_workspace.id.is_some_and(|id| id != 0);
        if has_name || has_id {
            Some(WorkspaceKey::from_raw(
                self.special_workspace.id,
                &self.special_workspace.name,
                "special",
            ))
        } else {
            None
        }
    }

    /// Returns the special workspace key if one is currently open on this monitor,
    /// otherwise returns the monitor's active normal workspace key.
    pub fn effective_active_key(&self) -> WorkspaceKey {
        self.active_special_key()
            .unwrap_or_else(|| self.active_workspace.key())
    }
}

/// Pre-processed workspace entry ready for rendering in the UI.
#[derive(Debug, Clone)]
pub struct WorkspaceView {
    pub key: WorkspaceKey,
    pub title: String,
    pub is_active: bool,
    pub is_special: bool,
    pub monitor_x: f32,
    pub monitor_y: f32,
    pub monitor_w: f32,
    pub monitor_h: f32,
    pub clients: Vec<Client>,
}

#[derive(Debug, Clone)]
pub struct OverviewSnapshot {
    pub workspaces: Vec<WorkspaceView>,
    pub focused_address: Option<String>,
}

static SOCKET_PATH: OnceLock<PathBuf> = OnceLock::new();
static OPEN_SPECIAL_WORKSPACES: OnceLock<Vec<String>> = OnceLock::new();

pub fn socket_path() -> Result<&'static Path, HyprError> {
    if let Some(cached) = SOCKET_PATH.get() {
        return Ok(cached.as_path());
    }

    let instance = env::var("HYPRLAND_INSTANCE_SIGNATURE")
        .map_err(|_| HyprError::MissingEnv("HYPRLAND_INSTANCE_SIGNATURE"))?;

    if let Ok(runtime_dir) = env::var("XDG_RUNTIME_DIR") {
        let candidate = PathBuf::from(runtime_dir)
            .join("hypr")
            .join(&instance)
            .join(".socket.sock");
        if candidate.exists() {
            let _ = SOCKET_PATH.set(candidate);
            return Ok(SOCKET_PATH.get().unwrap().as_path());
        }
    }

    let fallback = PathBuf::from("/tmp/hypr")
        .join(instance)
        .join(".socket.sock");
    if fallback.exists() {
        let _ = SOCKET_PATH.set(fallback);
        return Ok(SOCKET_PATH.get().unwrap().as_path());
    }

    Err(HyprError::MissingEnv("XDG_RUNTIME_DIR"))
}

pub fn event_socket_path() -> Option<PathBuf> {
    let instance = env::var("HYPRLAND_INSTANCE_SIGNATURE").ok()?;
    if let Ok(runtime_dir) = env::var("XDG_RUNTIME_DIR") {
        let candidate = PathBuf::from(runtime_dir)
            .join("hypr")
            .join(&instance)
            .join(".socket2.sock");
        if candidate.exists() {
            return Some(candidate);
        }
    }
    let fallback = PathBuf::from("/tmp/hypr")
        .join(instance)
        .join(".socket2.sock");
    fallback.exists().then_some(fallback)
}

pub fn is_snap_picker_mapped() -> bool {
    fetch_clients().is_ok_and(|clients| {
        clients
            .iter()
            .any(|c| c.mapped && !c.hidden && c.class == "dusky-snap-pick")
    })
}

pub fn focus_snap_picker() -> Result<(), HyprError> {
    let cmd = "eval for _, w in ipairs(hl.get_windows()) do if w.class == \"dusky-snap-pick\" then hl.dispatch(hl.dsp.focus({ window = w })) hl.dispatch(hl.dsp.window.bring_to_top()) break end end";
    let _ = send_socket_command(cmd)?;
    Ok(())
}

fn send_socket_command(command: &str) -> Result<String, HyprError> {
    let path = socket_path()?;
    let mut stream = UnixStream::connect(path)?;
    stream.set_read_timeout(Some(Duration::from_millis(500)))?;
    stream.set_write_timeout(Some(Duration::from_millis(500)))?;

    stream.write_all(command.as_bytes())?;
    stream.shutdown(std::net::Shutdown::Write)?;

    let mut response = String::with_capacity(8192);
    stream.read_to_string(&mut response)?;
    Ok(response)
}

pub fn fetch_clients() -> Result<Vec<Client>, HyprError> {
    let raw = send_socket_command("j/clients")?;
    Ok(serde_json::from_str(&raw)?)
}

pub fn fetch_workspaces() -> Result<Vec<Workspace>, HyprError> {
    let raw = send_socket_command("j/workspaces")?;
    Ok(serde_json::from_str(&raw)?)
}

pub fn fetch_monitors() -> Result<Vec<Monitor>, HyprError> {
    let raw = send_socket_command("j/monitors")?;
    Ok(serde_json::from_str(&raw)?)
}

fn fetch_all_batched() -> Result<(Vec<Monitor>, Vec<Workspace>, Vec<Client>), HyprError> {
    let raw = send_socket_command("[[BATCH]]j/monitors;j/workspaces;j/clients")?;
    let mut de = serde_json::Deserializer::from_str(&raw);
    let monitors = Vec::<Monitor>::deserialize(&mut de)?;
    let workspaces = Vec::<Workspace>::deserialize(&mut de)?;
    let clients = Vec::<Client>::deserialize(&mut de)?;
    Ok((monitors, workspaces, clients))
}

/// Queries `j/monitors`, `j/workspaces`, and `j/clients` in a single atomic batch
/// and builds the filtered, sorted snapshot of active workspaces and their visible windows.
pub fn fetch_snapshot() -> Result<OverviewSnapshot, HyprError> {
    let (monitors, workspaces, all_clients) = match fetch_all_batched() {
        Ok(batch) => batch,
        Err(_) => (fetch_monitors()?, fetch_workspaces()?, fetch_clients()?),
    };

    let primary_monitor = monitors
        .iter()
        .find(|m| m.focused)
        .or_else(|| monitors.first())
        .cloned();

    let active_ws_key = primary_monitor
        .as_ref()
        .map(Monitor::effective_active_key);

    let mut open_specials = Vec::new();
    for mon in &monitors {
        if let Some(WorkspaceKey::Named(sp_name)) = mon.active_special_key() {
            let stripped = sp_name.strip_prefix("special:").unwrap_or(&sp_name).to_owned();
            if !open_specials.contains(&stripped) {
                open_specials.push(stripped);
            }
        }
    }
    let _ = OPEN_SPECIAL_WORKSPACES.set(open_specials);

    // Keep only mapped, non-hidden, non-pinned workspace windows with valid positive geometry.
    // Pinned windows (e.g. Picture-in-Picture overlays and dusky-snap-pick) float across all workspaces
    // and otherwise collide with the bottom-right workspace badge on Workspace 1.
    let valid_clients: Vec<Client> = all_clients
        .into_iter()
        .filter(|c| {
            c.mapped
                && !c.hidden
                && !c.pinned
                && c.class != "dusky-snap-pick"
                && c.size[0] > 0
                && c.size[1] > 0
        })
        .collect();

    // Prefer the most recently focused window on the currently active workspace (e.g. open special workspace)
    // before falling back to global focusHistoryID == 0.
    let focused_address = active_ws_key
        .as_ref()
        .and_then(|active_key| {
            valid_clients
                .iter()
                .filter(|c| &c.workspace.key() == active_key)
                .min_by_key(|c| c.focus_history_id)
                .map(|c| c.address.clone())
        })
        .or_else(|| {
            valid_clients
                .iter()
                .find(|c| c.focus_history_id == 0)
                .map(|c| c.address.clone())
        });

    let mut grouped: BTreeMap<WorkspaceKey, Vec<Client>> = BTreeMap::new();
    for client in valid_clients {
        grouped
            .entry(client.workspace.key())
            .or_default()
            .push(client);
    }

    // Ensure the currently active workspace is always included, even if empty.
    if let Some(ref active_key) = active_ws_key {
        grouped.entry(active_key.clone()).or_default();
    }

    // Also include active normal and open special workspaces across all monitors.
    for mon in &monitors {
        grouped.entry(mon.active_workspace.key()).or_default();
        if let Some(sp_key) = mon.active_special_key() {
            grouped.entry(sp_key).or_default();
        }
    }

    let mut views = Vec::with_capacity(grouped.len());

    for (key, mut ws_clients) in grouped {
        // A workspace is active ONLY if it is the top-most visible workspace on a monitor
        // (i.e. if a special workspace is open on that monitor, the underlying normal workspace is not active).
        let is_active = active_ws_key.as_ref() == Some(&key)
            || monitors.iter().any(|m| m.effective_active_key() == key);

        let is_monitor_underlay = monitors
            .iter()
            .any(|m| m.active_workspace.key() == key);

        if ws_clients.is_empty() && !is_active && !is_monitor_underlay {
            continue;
        }

        // Resolve which monitor owns this workspace for accurate relative geometry.
        let ws_meta = workspaces.iter().find(|w| w.key() == key);
        let monitor_for_ws = ws_meta
            .and_then(|w| {
                w.monitor_id
                    .and_then(|id| monitors.iter().find(|m| m.id == id))
                    .or_else(|| monitors.iter().find(|m| m.name == w.monitor))
            })
            .or_else(|| {
                monitors
                    .iter()
                    .find(|m| m.active_special_key().as_ref() == Some(&key))
            })
            .or_else(|| {
                ws_clients
                    .first()
                    .and_then(|c| c.monitor)
                    .and_then(|id| monitors.iter().find(|m| m.id == id))
            })
            .or(primary_monitor.as_ref());

        let (mon_x, mon_y, mon_w, mon_h) = match monitor_for_ws {
            Some(m) => (
                m.x as f32,
                m.y as f32,
                m.logical_width(),
                m.logical_height(),
            ),
            None => (0.0, 0.0, 1920.0, 1080.0),
        };

        // Normalize clients that report coordinates on a secondary monitor into this card's monitor space.
        for client in &mut ws_clients {
            if let Some(c_mon_id) = client.monitor
                && let Some(cm) = monitors.iter().find(|m| m.id == c_mon_id)
            {
                let cm_x = cm.x as f32;
                let cm_y = cm.y as f32;
                if (cm_x - mon_x).abs() > 0.5 || (cm_y - mon_y).abs() > 0.5 {
                    let cm_w = cm.logical_width();
                    let cm_h = cm.logical_height();
                    let rel_x = (client.at[0] as f32 - cm_x) / cm_w;
                    let rel_y = (client.at[1] as f32 - cm_y) / cm_h;
                    let rel_w = client.size[0] as f32 / cm_w;
                    let rel_h = client.size[1] as f32 / cm_h;
                    client.at[0] = (mon_x + rel_x * mon_w).round() as i32;
                    client.at[1] = (mon_y + rel_y * mon_h).round() as i32;
                    client.size[0] = (rel_w * mon_w).round().max(1.0) as i32;
                    client.size[1] = (rel_h * mon_h).round().max(1.0) as i32;
                }
            }
        }

        // Sort windows back-to-front: tiled before floating, older focus before newer focus.
        ws_clients.sort_by(|a, b| {
            a.floating
                .cmp(&b.floating)
                .then_with(|| b.focus_history_id.cmp(&a.focus_history_id))
        });

        let is_special = matches!(&key, WorkspaceKey::Named(n) if n.starts_with("special:"));
        let title = key.badge_label();

        views.push(WorkspaceView {
            key,
            title,
            is_active,
            is_special,
            monitor_x: mon_x,
            monitor_y: mon_y,
            monitor_w: mon_w,
            monitor_h: mon_h,
            clients: ws_clients,
        });
    }

    Ok(OverviewSnapshot {
        workspaces: views,
        focused_address,
    })
}

fn escape_lua_string(input: &str) -> String {
    let mut out = String::with_capacity(input.len());
    for ch in input.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\0' => out.push_str("\\0"),
            _ => out.push(ch),
        }
    }
    out
}

/// Switches to the target workspace, closes any covering special workspace if needed,
/// and focuses + elevates the target window reliably in Hyprland 0.57.
pub fn focus_window(
    workspace: &WorkspaceKey,
    address: &str,
    bring_to_top: bool,
) -> Result<(), HyprError> {
    let trimmed = address.trim();
    let hex_with_prefix = if trimmed.starts_with("0x") || trimmed.starts_with("0X") {
        trimmed.to_ascii_lowercase()
    } else {
        format!("0x{}", trimmed.to_ascii_lowercase())
    };
    let safe_addr = escape_lua_string(&hex_with_prefix);

    let mut prep_stmts = String::new();
    match workspace {
        WorkspaceKey::Normal(id) => {
            if let Some(open_list) = OPEN_SPECIAL_WORKSPACES.get() {
                for sp in open_list {
                    let safe_sp = escape_lua_string(sp);
                    prep_stmts.push_str(&format!(
                        "if hl.dsp.workspace and hl.dsp.workspace.toggle_special then hl.dispatch(hl.dsp.workspace.toggle_special(\"{safe_sp}\")) end "
                    ));
                }
            }
            prep_stmts.push_str(&format!("hl.dispatch(hl.dsp.focus({{ workspace = {id} }})) "));
        }
        WorkspaceKey::Named(name) => {
            if let Some(special_part) = name.strip_prefix("special:") {
                let is_already_open = OPEN_SPECIAL_WORKSPACES
                    .get()
                    .is_some_and(|list| list.iter().any(|s| s == special_part));
                if !is_already_open {
                    let safe_sp = escape_lua_string(special_part);
                    prep_stmts.push_str(&format!(
                        "if hl.dsp.workspace and hl.dsp.workspace.toggle_special then hl.dispatch(hl.dsp.workspace.toggle_special(\"{safe_sp}\")) end "
                    ));
                }
            } else {
                let safe_name = escape_lua_string(name);
                prep_stmts.push_str(&format!(
                    "hl.dispatch(hl.dsp.focus({{ workspace = \"{safe_name}\" }})) "
                ));
            }
        }
    }

    let top_stmt = if bring_to_top {
        " hl.dispatch(hl.dsp.window.bring_to_top())"
    } else {
        ""
    };

    // If another window on the target workspace is currently fullscreen, exit fullscreen first
    // so the newly selected window on that workspace is not trapped behind it.
    let cmd = format!(
        "eval {prep_stmts}local target_num = tonumber(\"{safe_addr}\") for _, w in ipairs(hl.get_windows()) do local wa = tostring(w.address) if string.lower(wa) == \"{safe_addr}\" or (target_num and tonumber(wa) == target_num) then if not w.fullscreen then for _, other in ipairs(hl.get_windows()) do if other ~= w and other.workspace == w.workspace and other.fullscreen then hl.dispatch(hl.dsp.focus({{ window = other }})) if hl.dsp.window and hl.dsp.window.fullscreen then hl.dispatch(hl.dsp.window.fullscreen()) end break end end end hl.dispatch(hl.dsp.focus({{ window = w }})){top_stmt} break end end"
    );
    let resp = send_socket_command(&cmd)?;

    // Fallback to direct socket dispatcher if the window still needs focus activation
    let _ = send_socket_command(&format!("dispatch focuswindow address:{safe_addr}"));
    if bring_to_top {
        let _ = send_socket_command(&format!("dispatch alterzorder top,address:{safe_addr}"));
    }

    if resp.trim() != "ok" && !resp.trim().is_empty() {
        return Err(HyprError::DispatchFailed(resp));
    }
    Ok(())
}

/// Brings the currently focused window to the top of the z-order via Hyprland 0.57 Lua IPC.
#[allow(dead_code)]
pub fn bring_window_to_top() -> Result<(), HyprError> {
    let resp = send_socket_command("eval hl.dispatch(hl.dsp.window.bring_to_top())")?;
    if resp.trim() != "ok" && !resp.trim().is_empty() {
        return Err(HyprError::DispatchFailed(resp));
    }
    Ok(())
}

/// Dispatches workspace focus or special workspace toggle in Hyprland 0.57 using Lua `eval` IPC syntax.
pub fn focus_workspace(target: &WorkspaceKey) -> Result<(), HyprError> {
    let cmd = match target {
        WorkspaceKey::Normal(id) => {
            let mut close_special_stmts = String::new();
            if let Some(open_list) = OPEN_SPECIAL_WORKSPACES.get() {
                for sp in open_list {
                    let safe_sp = escape_lua_string(sp);
                    close_special_stmts.push_str(&format!(
                        "if hl.dsp.workspace and hl.dsp.workspace.toggle_special then hl.dispatch(hl.dsp.workspace.toggle_special(\"{safe_sp}\")) end "
                    ));
                }
            }
            format!("eval {close_special_stmts}hl.dispatch(hl.dsp.focus({{ workspace = {id} }}))")
        }
        WorkspaceKey::Named(name) => {
            if let Some(special_part) = name.strip_prefix("special:") {
                let safe_special = escape_lua_string(special_part);
                let safe_full = escape_lua_string(name);
                format!(
                    "eval if hl.dsp.workspace and hl.dsp.workspace.toggle_special then hl.dispatch(hl.dsp.workspace.toggle_special(\"{safe_special}\")) else hl.dispatch(hl.dsp.focus({{ workspace = \"{safe_full}\" }})) end"
                )
            } else {
                let safe_name = escape_lua_string(name);
                format!("eval hl.dispatch(hl.dsp.focus({{ workspace = \"{safe_name}\" }}))")
            }
        }
    };

    let resp = send_socket_command(&cmd)?;
    if resp.trim() != "ok" && !resp.trim().is_empty() {
        return Err(HyprError::DispatchFailed(resp));
    }
    Ok(())
}
