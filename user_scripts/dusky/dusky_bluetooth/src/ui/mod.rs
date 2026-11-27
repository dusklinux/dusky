//! Root Iced 0.14 application state, subscriptions, message reducer, and shutdown wiring.

pub mod style;
pub mod views;

use crate::audio;
use crate::bluez::{BackendCommand, BackendEvent, BackendHandle, BackendStatus};
use crate::config::AppConfig;
use crate::model::{
    AdapterState, AgentReply, AudioProfileState, DeviceOp, DeviceOpOutcome, DeviceState, HideScope,
    PairingKind, PairingPrompt, RfkillState, TrafficSnapshot, VisibilityMode, normalize_mac,
};
use crate::theme::{AppTheme, ThemeStamp};
use crate::{IpcCommand, LaunchOptions};
use futures_timer::Delay;
use iced::{Subscription, Task};
use iced_futures::futures::{FutureExt, Stream, StreamExt, select_biased};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::pin::Pin;
use std::time::{Duration, Instant};

pub type Element<'a, Message> =
    iced_core::Element<'a, Message, iced_core::Theme, crate::renderer::Renderer>;

const THEME_POLL_INTERVAL: Duration = Duration::from_secs(2);
const TOAST_DURATION: Duration = Duration::from_secs(3);
const SHUTDOWN_DEADLINE: Duration = Duration::from_millis(400);
const AUDIO_RETRY_DELAY: Duration = Duration::from_millis(750);
const AUDIO_SWITCH_SETTLE_DELAY: Duration = Duration::from_millis(150);
const CLICK_COMMIT_DELAY: Duration = Duration::from_millis(200);
const DOUBLE_CLICK_WINDOW: Duration = Duration::from_millis(250);

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ClickDecision {
    PendingCommit(u64),
    DoubleClick(String),
}

#[derive(Debug, Default)]
pub struct ClickTracker {
    next_seq: u64,
    pending: Option<(String, u64, Instant)>,
}

impl ClickTracker {
    pub fn on_press(&mut self, path: String, now: Instant) -> ClickDecision {
        if let Some((prev_path, _, prev_time)) = &self.pending
            && prev_path == &path
            && now.saturating_duration_since(*prev_time) <= DOUBLE_CLICK_WINDOW
        {
            self.pending = None;
            return ClickDecision::DoubleClick(path);
        }
        self.next_seq = self.next_seq.saturating_add(1);
        let seq = self.next_seq;
        self.pending = Some((path, seq, now));
        ClickDecision::PendingCommit(seq)
    }

    pub fn on_commit(&mut self, seq: u64) -> Option<String> {
        if self.pending.as_ref().is_some_and(|(_, s, _)| *s == seq) {
            self.pending.take().map(|(path, _, _)| path)
        } else {
            None
        }
    }

    pub fn clear(&mut self) {
        self.pending = None;
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DoubleClickAction {
    Disconnect,
    Connect,
    Pair,
}

pub fn resolve_double_click_action(
    dev: &DeviceState,
    adapter_powered: bool,
) -> Option<DoubleClickAction> {
    if dev.in_flight.is_some() {
        return None;
    }
    if dev.connected {
        return Some(DoubleClickAction::Disconnect);
    }
    if !adapter_powered || dev.blocked {
        return None;
    }
    if dev.paired {
        Some(DoubleClickAction::Connect)
    } else {
        Some(DoubleClickAction::Pair)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeviceVisibility {
    Visible,
    HiddenByMac,
    HiddenUnnamed,
}

pub fn classify_device_visibility(
    dev: &DeviceState,
    hide_unnamed: bool,
    session_hidden: &HashSet<String>,
    persistent_hidden: &BTreeSet<String>,
) -> DeviceVisibility {
    if dev.connected {
        return DeviceVisibility::Visible;
    }
    let mac = normalize_mac(&dev.address);
    if !mac.is_empty() && (session_hidden.contains(&mac) || persistent_hidden.contains(&mac)) {
        return DeviceVisibility::HiddenByMac;
    }
    if hide_unnamed && dev.should_hide_when_unnamed() {
        return DeviceVisibility::HiddenUnnamed;
    }
    DeviceVisibility::Visible
}

pub fn rename_input_id() -> iced_core::widget::Id {
    iced_core::widget::Id::new("dusky-bluetooth-rename-input")
}

pub fn pairing_input_id() -> iced_core::widget::Id {
    iced_core::widget::Id::new("dusky-bluetooth-pairing-input")
}

pub fn adapter_alias_input_id() -> iced_core::widget::Id {
    iced_core::widget::Id::new("dusky-bluetooth-adapter-alias-input")
}

pub fn format_device_info_plain(dev: &DeviceState, adapter: Option<&AdapterState>) -> String {
    let mut lines = Vec::new();
    let mut push = |k: &str, v: String| lines.push(format!("{k}: {v}"));
    let yn = |b: bool| if b { "yes" } else { "no" }.to_string();

    push("Alias", dev.alias.clone());
    if let Some(name) = &dev.name {
        push("Name", name.clone());
    }
    push("Address", dev.address.clone());
    if let Some(at) = &dev.address_type {
        push("AddressType", at.clone());
    }
    push("Kind", dev.kind.label().to_string());
    if dev.class != 0 {
        let label = crate::model::decode_class(dev.class)
            .map(|d| format!("{d} (0x{:06x})", dev.class))
            .unwrap_or_else(|| format!("0x{:06x}", dev.class));
        push("Class", label);
    }
    if dev.appearance != 0 {
        let label = crate::model::decode_appearance(dev.appearance)
            .map(|d| format!("{d} (0x{:04x})", dev.appearance))
            .unwrap_or_else(|| format!("0x{:04x}", dev.appearance));
        push("Appearance", label);
    }
    if let Some(icon) = &dev.icon_hint {
        push("Icon", icon.clone());
    }
    push("Paired", yn(dev.paired));
    push("Bonded", yn(dev.bonded));
    push("Connected", yn(dev.connected));
    push("Trusted", yn(dev.trusted));
    push("Blocked", yn(dev.blocked));
    push("ServicesResolved", yn(dev.services_resolved));
    push("LegacyPairing", yn(dev.legacy_pairing));
    if let Some(pct) = dev.battery {
        push("Battery", format!("{pct}%"));
    }
    if let Some(rssi) = dev.rssi.and_then(crate::model::valid_rssi) {
        push(
            "RSSI",
            format!("{rssi} dBm ({})", crate::model::rssi_quality_label(rssi)),
        );
    }
    if let Some(txp) = dev.tx_power {
        push("TxPower", format!("{txp} dBm"));
    }
    if let Some(m) = &dev.modalias {
        push("Modalias", m.clone());
    }
    if let Some(w) = dev.wake_allowed {
        push("WakeAllowed", yn(w));
    }
    if let Some(pb) = &dev.preferred_bearer {
        push("PreferredBearer", pb.clone());
    }
    if let Some(cp) = dev.cable_pairing {
        push("CablePairing", yn(cp));
    }
    for uuid in &dev.uuids {
        push(
            &format!("UUID ({})", crate::model::service_uuid_name(uuid)),
            uuid.clone(),
        );
    }
    if !dev.advertising_flags.is_empty() {
        push(
            "AdvertisingFlags",
            crate::model::decode_advertising_flags(&dev.advertising_flags),
        );
    }
    for (company, bytes) in &dev.manufacturer_data {
        push(
            &format!("ManufacturerData (0x{company:04x})"),
            crate::model::format_hex_bytes(bytes),
        );
    }
    for (uuid, bytes) in &dev.service_data {
        push(
            &format!("ServiceData ({uuid})"),
            crate::model::format_hex_bytes(bytes),
        );
    }
    if let Some(a) = adapter {
        push("Adapter", format!("{} ({})", a.display_name(), a.short_id()));
        if !a.name.is_empty() {
            push("AdapterName", a.name.clone());
        }
        if !a.address.is_empty() {
            push("AdapterAddress", a.address.clone());
        }
    }
    lines.join("\n")
}

pub fn format_device_info_json(dev: &DeviceState, adapter: Option<&AdapterState>) -> String {
    let mut map = serde_json::Map::new();
    map.insert("Alias".into(), serde_json::Value::from(dev.alias.clone()));
    if let Some(name) = &dev.name {
        map.insert("Name".into(), serde_json::Value::from(name.clone()));
    }
    map.insert(
        "Address".into(),
        serde_json::Value::from(dev.address.clone()),
    );
    if let Some(at) = &dev.address_type {
        map.insert("AddressType".into(), serde_json::Value::from(at.clone()));
    }
    map.insert("Kind".into(), serde_json::Value::from(dev.kind.label()));
    if dev.class != 0 {
        map.insert(
            "Class".into(),
            serde_json::Value::from(format!("0x{:06x}", dev.class)),
        );
        if let Some(decoded) = crate::model::decode_class(dev.class) {
            map.insert("ClassDecoded".into(), serde_json::Value::from(decoded));
        }
    }
    if dev.appearance != 0 {
        map.insert(
            "Appearance".into(),
            serde_json::Value::from(format!("0x{:04x}", dev.appearance)),
        );
        if let Some(decoded) = crate::model::decode_appearance(dev.appearance) {
            map.insert("AppearanceDecoded".into(), serde_json::Value::from(decoded));
        }
    }
    if let Some(icon) = &dev.icon_hint {
        map.insert("Icon".into(), serde_json::Value::from(icon.clone()));
    }
    map.insert("Paired".into(), serde_json::Value::from(dev.paired));
    map.insert("Bonded".into(), serde_json::Value::from(dev.bonded));
    map.insert("Connected".into(), serde_json::Value::from(dev.connected));
    map.insert("Trusted".into(), serde_json::Value::from(dev.trusted));
    map.insert("Blocked".into(), serde_json::Value::from(dev.blocked));
    map.insert(
        "ServicesResolved".into(),
        serde_json::Value::from(dev.services_resolved),
    );
    map.insert(
        "LegacyPairing".into(),
        serde_json::Value::from(dev.legacy_pairing),
    );
    if let Some(pct) = dev.battery {
        map.insert("Battery".into(), serde_json::Value::from(pct));
    }
    if let Some(rssi) = dev.rssi.and_then(crate::model::valid_rssi) {
        map.insert("RSSI".into(), serde_json::Value::from(rssi));
    }
    if let Some(txp) = dev.tx_power {
        map.insert("TxPower".into(), serde_json::Value::from(txp));
    }
    if let Some(m) = &dev.modalias {
        map.insert("Modalias".into(), serde_json::Value::from(m.clone()));
    }
    if let Some(w) = dev.wake_allowed {
        map.insert("WakeAllowed".into(), serde_json::Value::from(w));
    }
    if let Some(pb) = &dev.preferred_bearer {
        map.insert(
            "PreferredBearer".into(),
            serde_json::Value::from(pb.clone()),
        );
    }
    if let Some(cp) = dev.cable_pairing {
        map.insert("CablePairing".into(), serde_json::Value::from(cp));
    }
    let uuids: Vec<serde_json::Value> = dev
        .uuids
        .iter()
        .map(|u| {
            serde_json::json!({
                "name": crate::model::service_uuid_name(u),
                "uuid": u,
            })
        })
        .collect();
    map.insert("UUIDs".into(), serde_json::Value::Array(uuids));
    if !dev.advertising_flags.is_empty() {
        map.insert(
            "AdvertisingFlags".into(),
            serde_json::Value::from(crate::model::decode_advertising_flags(
                &dev.advertising_flags,
            )),
        );
    }
    if !dev.manufacturer_data.is_empty() {
        let mut m = serde_json::Map::new();
        for (company, bytes) in &dev.manufacturer_data {
            m.insert(
                format!("0x{company:04x}"),
                serde_json::Value::from(crate::model::format_hex_bytes(bytes)),
            );
        }
        map.insert("ManufacturerData".into(), serde_json::Value::Object(m));
    }
    if !dev.service_data.is_empty() {
        let mut s = serde_json::Map::new();
        for (uuid, bytes) in &dev.service_data {
            s.insert(
                uuid.clone(),
                serde_json::Value::from(crate::model::format_hex_bytes(bytes)),
            );
        }
        map.insert("ServiceData".into(), serde_json::Value::Object(s));
    }
    if let Some(a) = adapter {
        map.insert(
            "Adapter".into(),
            serde_json::json!({
                "id": a.short_id(),
                "alias": a.alias,
                "name": a.name,
                "address": a.address,
                "powered": a.powered,
                "discoverable": a.discoverable,
                "discoverable_timeout": a.discoverable_timeout,
                "pairable": a.pairable,
                "pairable_timeout": a.pairable_timeout,
            }),
        );
    }
    serde_json::to_string_pretty(&serde_json::Value::Object(map)).unwrap_or_default()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ToastKind {
    Info,
    Success,
    Error,
}

#[derive(Debug, Clone)]
pub struct Toast {
    pub message: String,
    pub kind: ToastKind,
    pub seq: u64,
}

#[derive(Debug, Clone)]
pub struct RenameState {
    pub device_path: String,
    pub address: String,
    pub current_alias: String,
    pub draft: String,
}

#[derive(Debug, Clone)]
pub enum Message {
    Backend(BackendEvent),
    Ipc(IpcCommand),
    ThemeTick,
    WindowResized(f32, f32),

    // Adapter controls & Preferences modal
    CycleAdapter,
    SelectAdapter(String),
    TogglePower(bool),
    ToggleDiscoverable(bool),
    ToggleScan,
    ToggleHideUnnamed,
    OpenSettings,
    CloseSettings,
    AdapterAliasDraftChanged(String),
    AdapterAliasSave,
    AdapterAliasReset,
    SetVisibilityMode(VisibilityMode),
    SetPairable(bool),
    SetPairableTimeout(u32),
    UnhideDevice(String),
    UnhideAllDevices,

    // Device Info modal & clipboard helper
    OpenInfo(String),
    CloseInfo,
    CopyText {
        label: String,
        value: String,
    },

    // Device actions & card clicks
    CardPressed(String),
    CommitCardClick(u64),
    ToggleExpand(String),
    ConnectDevice(String),
    DisconnectDevice(String),
    PairDevice(String),
    ToggleTrust(String),
    ToggleBlock(String),
    RequestHideDevice(String),
    HideDevice(String, HideScope),
    CancelHide,
    RemoveDevice(String),
    CancelRemove,
    CopyAddress(String),

    // PipeWire audio profiles
    AudioProfilesLoaded {
        device_path: String,
        attempt: u8,
        state: AudioProfileState,
    },
    SelectAudioProfile {
        device_path: String,
        card_id: u32,
        profile_index: u32,
    },
    AudioProfileSwitched {
        device_path: String,
        result: Result<(), String>,
    },

    // Pairing modal
    PairingInputChanged(String),
    PairingToggleVisibility,
    PairingSubmit,
    PairingConfirm,
    PairingAcceptService(bool),
    PairingReject,
    PairingCancel,

    // Rename modal
    OpenRename(String),
    RenameDraftChanged(String),
    RenameReset,
    RenameSave,
    CloseRename,

    // Banners, toasts & window lifecycle
    EscapePressed,
    DismissNotice,
    ExpireToast(u64),
    RequestClose,
    ForceExit,
}

pub struct BluetoothApp {
    pub theme: AppTheme,
    theme_stamp: Option<ThemeStamp>,
    backend: BackendHandle,
    pub config: AppConfig,
    pub window_width: f32,
    pub window_height: f32,
    pub status: BackendStatus,
    pub rfkill: RfkillState,
    pub traffic: TrafficSnapshot,
    pub adapters: BTreeMap<String, AdapterState>,
    pub active_adapter: Option<String>,
    pub owned_scans: HashSet<String>,
    pub devices: BTreeMap<String, DeviceState>,
    pub session_hidden: HashSet<String>,
    pub expanded_device: Option<String>,
    pub confirm_remove: Option<String>,
    pub confirm_hide: Option<String>,
    click_tracker: ClickTracker,
    pub audio_profiles: HashMap<String, AudioProfileState>,
    pub notice: Option<(String, bool)>,
    pub toast: Option<Toast>,
    toast_seq: u64,
    pub pairing_prompt: Option<PairingPrompt>,
    pub pairing_input: String,
    pub pairing_show_text: bool,
    pub rename_modal: Option<RenameState>,
    pub info_device: Option<String>,
    pub settings_open: bool,
    pub adapter_alias_draft: String,
    exiting: bool,
}

impl BluetoothApp {
    pub fn boot(launch: LaunchOptions) -> (Self, Task<Message>) {
        let (theme, theme_stamp) = AppTheme::load_with_stamp();
        let (window_width, window_height) = launch.resolved_window_size();
        let config = AppConfig::load();
        let backend = crate::bluez::spawn_backend();

        let app = Self {
            theme,
            theme_stamp,
            backend,
            config,
            window_width,
            window_height,
            status: BackendStatus::Connecting,
            rfkill: RfkillState::default(),
            traffic: TrafficSnapshot::default(),
            adapters: BTreeMap::new(),
            active_adapter: None,
            owned_scans: HashSet::new(),
            devices: BTreeMap::new(),
            session_hidden: HashSet::new(),
            expanded_device: None,
            confirm_remove: None,
            confirm_hide: None,
            click_tracker: ClickTracker::default(),
            audio_profiles: HashMap::new(),
            notice: None,
            toast: None,
            toast_seq: 0,
            pairing_prompt: None,
            pairing_input: String::new(),
            pairing_show_text: false,
            rename_modal: None,
            info_device: None,
            settings_open: false,
            adapter_alias_draft: String::new(),
            exiting: false,
        };
        (app, Task::none())
    }

    pub fn active_adapter_state(&self) -> Option<&AdapterState> {
        let path = self.active_adapter.as_deref()?;
        self.adapters.get(path)
    }

    pub fn active_scan_owned(&self) -> bool {
        self.active_adapter
            .as_deref()
            .is_some_and(|path| self.owned_scans.contains(path))
    }

    pub fn total_hidden_macs(&self) -> usize {
        self.session_hidden
            .iter()
            .chain(self.config.hidden_devices.iter())
            .collect::<HashSet<_>>()
            .len()
    }

    fn save_config(&mut self) {
        if let Err(err) = self.config.save() {
            self.notice = Some((err, false));
        }
    }

    fn set_active_adapter(&mut self, next: Option<String>, persist: bool) {
        if self.active_adapter == next {
            return;
        }
        self.active_adapter = next.clone();
        self.backend.send(BackendCommand::SetActiveAdapter {
            adapter_path: next.clone(),
        });
        if persist
            && let Some(path) = next.as_deref()
            && let Some(adapter) = self.adapters.get(path)
        {
            self.config.last_adapter = Some(adapter.short_id().to_string());
            self.save_config();
        }
    }

    fn ensure_active_adapter(&mut self) {
        if let Some(current) = &self.active_adapter
            && self.adapters.contains_key(current)
        {
            return;
        }
        let preferred = self.config.last_adapter.as_deref().and_then(|wanted| {
            self.adapters
                .values()
                .find(|a| {
                    a.short_id().eq_ignore_ascii_case(wanted)
                        || a.address.eq_ignore_ascii_case(wanted)
                })
                .map(|a| a.path.clone())
        });
        let next = preferred.or_else(|| self.adapters.keys().next().cloned());
        self.set_active_adapter(next, false);
    }

    fn toggle_expand_path(&mut self, path: String) -> Task<Message> {
        self.confirm_remove = None;
        self.confirm_hide = None;
        if self.expanded_device.as_deref() == Some(path.as_str()) {
            self.expanded_device = None;
            Task::none()
        } else {
            self.expanded_device = Some(path.clone());
            self.fetch_audio_profiles_for(&path, true)
        }
    }

    fn show_toast(&mut self, message: impl Into<String>, kind: ToastKind) -> Task<Message> {
        self.toast_seq = self.toast_seq.saturating_add(1);
        let seq = self.toast_seq;
        self.toast = Some(Toast {
            message: message.into(),
            kind,
            seq,
        });
        Task::perform(Delay::new(TOAST_DURATION), move |_| {
            Message::ExpireToast(seq)
        })
    }

    fn fetch_audio_profiles_for(&mut self, device_path: &str, force: bool) -> Task<Message> {
        let Some(dev) = self.devices.get(device_path) else {
            return Task::none();
        };
        if !dev.connected || !dev.is_audio_device() {
            self.audio_profiles.remove(device_path);
            return Task::none();
        }
        if !force
            && matches!(
                self.audio_profiles.get(device_path),
                Some(AudioProfileState::Loading)
            )
        {
            return Task::none();
        }
        let path = dev.path.clone();
        let mac = dev.address.clone();
        self.audio_profiles
            .insert(path.clone(), AudioProfileState::Loading);
        Task::perform(audio::query_audio_profiles(mac), move |state| {
            Message::AudioProfilesLoaded {
                device_path: path,
                attempt: 0,
                state,
            }
        })
    }

    fn schedule_audio_query(
        &self,
        device_path: &str,
        delay: Duration,
        attempt: u8,
    ) -> Task<Message> {
        let Some(dev) = self.devices.get(device_path) else {
            return Task::none();
        };
        if !dev.connected || !dev.is_audio_device() {
            return Task::none();
        }
        let path = dev.path.clone();
        let mac = dev.address.clone();
        Task::perform(
            async move {
                Delay::new(delay).await;
                audio::query_audio_profiles(mac).await
            },
            move |state| Message::AudioProfilesLoaded {
                device_path: path,
                attempt,
                state,
            },
        )
    }

    fn send_pairing_reply(&mut self, reply: AgentReply) {
        if let Some(prompt) = self.pairing_prompt.take() {
            self.pairing_input.clear();
            self.pairing_show_text = false;
            self.backend.send(BackendCommand::AgentReply {
                prompt_id: prompt.prompt_id,
                reply,
            });
        }
    }

    pub fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::ThemeTick => {
                if let Some((theme, stamp)) = AppTheme::load_if_changed(self.theme_stamp.as_ref()) {
                    self.theme = theme;
                    self.theme_stamp = Some(stamp);
                }
                Task::none()
            }
            Message::WindowResized(w, h) => {
                if w.is_finite() && w > 0.0 {
                    self.window_width = w;
                }
                if h.is_finite() && h > 0.0 {
                    self.window_height = h;
                }
                Task::none()
            }
            Message::Ipc(IpcCommand::Focus) => {
                crate::focus_hyprland_window();
                Task::none()
            }
            Message::Ipc(IpcCommand::Toggle) | Message::RequestClose => self.begin_shutdown(),
            Message::ForceExit => iced::exit(),
            Message::DismissNotice => {
                self.notice = None;
                Task::none()
            }
            Message::ExpireToast(seq) => {
                if self.toast.as_ref().is_some_and(|t| t.seq == seq) {
                    self.toast = None;
                }
                Task::none()
            }
            Message::Backend(event) => self.handle_backend_event(event),

            Message::EscapePressed => {
                if self.pairing_prompt.is_some() {
                    self.send_pairing_reply(AgentReply::Canceled);
                } else if self.rename_modal.is_some() {
                    self.rename_modal = None;
                } else if self.info_device.is_some() {
                    self.info_device = None;
                } else if self.settings_open {
                    self.settings_open = false;
                } else if self.confirm_remove.is_some() || self.confirm_hide.is_some() {
                    self.confirm_remove = None;
                    self.confirm_hide = None;
                } else if self.expanded_device.is_some() {
                    self.expanded_device = None;
                }
                Task::none()
            }
            Message::CycleAdapter => {
                if self.adapters.len() > 1 {
                    let keys: Vec<String> = self.adapters.keys().cloned().collect();
                    let next_idx = self
                        .active_adapter
                        .as_deref()
                        .and_then(|cur| keys.iter().position(|k| k == cur))
                        .map(|idx| (idx + 1) % keys.len())
                        .unwrap_or(0);
                    if let Some(next) = keys.into_iter().nth(next_idx) {
                        return self.update(Message::SelectAdapter(next));
                    }
                }
                Task::none()
            }
            Message::SelectAdapter(path) => {
                if self.adapters.contains_key(&path) {
                    self.set_active_adapter(Some(path), true);
                    self.expanded_device = None;
                    self.confirm_remove = None;
                    self.confirm_hide = None;
                    self.info_device = None;
                    self.click_tracker.clear();
                    if self.settings_open {
                        self.adapter_alias_draft = self
                            .active_adapter_state()
                            .map(|a| a.alias.clone())
                            .unwrap_or_default();
                    }
                }
                Task::none()
            }
            Message::TogglePower(powered) => {
                if let Some(adapter_path) = self.active_adapter.clone() {
                    self.notice = None;
                    if !powered {
                        self.confirm_remove = None;
                        self.confirm_hide = None;
                        self.rename_modal = None;
                    }
                    self.backend.send(BackendCommand::SetAdapterPowered {
                        adapter_path,
                        powered,
                    });
                }
                Task::none()
            }
            Message::ToggleDiscoverable(discoverable) => {
                if let Some(adapter_path) = self.active_adapter.clone() {
                    self.notice = None;
                    let mode = if discoverable {
                        self.config.preferred_visible_mode()
                    } else {
                        VisibilityMode::Hidden
                    };
                    self.backend.send(BackendCommand::SetAdapterVisibility {
                        adapter_path,
                        mode,
                    });
                }
                Task::none()
            }
            Message::ToggleScan => {
                if let Some(adapter) = self.active_adapter_state().cloned()
                    && adapter.powered
                {
                    if adapter.discovering {
                        if !self.owned_scans.contains(&adapter.path) {
                            return Task::none();
                        }
                        self.notice = None;
                        self.backend.send(BackendCommand::StopDiscovery {
                            adapter_path: adapter.path,
                        });
                    } else {
                        self.notice = None;
                        self.backend.send(BackendCommand::StartDiscovery {
                            adapter_path: adapter.path,
                        });
                    }
                }
                Task::none()
            }
            Message::ToggleHideUnnamed => {
                self.config.hide_unnamed = !self.config.hide_unnamed;
                self.save_config();
                Task::none()
            }
            Message::OpenSettings => {
                self.confirm_remove = None;
                self.confirm_hide = None;
                self.adapter_alias_draft = self
                    .active_adapter_state()
                    .map(|a| a.alias.clone())
                    .unwrap_or_default();
                self.settings_open = true;
                Task::none()
            }
            Message::CloseSettings => {
                self.settings_open = false;
                Task::none()
            }
            Message::AdapterAliasDraftChanged(raw) => {
                self.adapter_alias_draft = raw.chars().take(248).collect();
                Task::none()
            }
            Message::AdapterAliasSave => {
                if let Some(adapter) = self.active_adapter_state().cloned() {
                    let trimmed = self.adapter_alias_draft.trim().to_string();
                    self.adapter_alias_draft = trimmed.clone();
                    if trimmed != adapter.alias {
                        self.backend.send(BackendCommand::SetAdapterAlias {
                            adapter_path: adapter.path,
                            alias: trimmed,
                        });
                    }
                }
                Task::none()
            }
            Message::AdapterAliasReset => {
                if let Some(adapter) = self.active_adapter_state().cloned() {
                    self.adapter_alias_draft = adapter.name.clone();
                    self.backend.send(BackendCommand::SetAdapterAlias {
                        adapter_path: adapter.path,
                        alias: String::new(),
                    });
                }
                Task::none()
            }
            Message::SetVisibilityMode(mode) => {
                self.config.record_visibility_preset(mode);
                self.save_config();
                if let Some(adapter) = self.active_adapter_state().cloned()
                    && adapter.powered
                {
                    self.notice = None;
                    self.backend.send(BackendCommand::SetAdapterVisibility {
                        adapter_path: adapter.path,
                        mode,
                    });
                }
                Task::none()
            }
            Message::SetPairable(pairable) => {
                if let Some(adapter) = self.active_adapter_state().cloned()
                    && adapter.powered
                {
                    self.notice = None;
                    self.backend.send(BackendCommand::SetAdapterPairable {
                        adapter_path: adapter.path,
                        pairable,
                    });
                }
                Task::none()
            }
            Message::SetPairableTimeout(timeout_secs) => {
                if let Some(adapter) = self.active_adapter_state().cloned()
                    && adapter.powered
                {
                    self.notice = None;
                    self.backend
                        .send(BackendCommand::SetAdapterPairableTimeout {
                            adapter_path: adapter.path,
                            timeout_secs,
                        });
                }
                Task::none()
            }
            Message::UnhideDevice(mac) => {
                let normalized = normalize_mac(&mac);
                let removed_session = self.session_hidden.remove(&normalized);
                let removed_persist = self.config.unhide_device(&normalized);
                if removed_persist {
                    self.save_config();
                }
                if removed_session || removed_persist {
                    self.show_toast("Device unhidden", ToastKind::Info)
                } else {
                    Task::none()
                }
            }
            Message::UnhideAllDevices => {
                let had_session = !self.session_hidden.is_empty();
                let had_persist = !self.config.hidden_devices.is_empty();
                self.session_hidden.clear();
                self.config.hidden_devices.clear();
                if had_persist {
                    self.save_config();
                }
                if had_session || had_persist {
                    self.show_toast("All hidden devices restored", ToastKind::Info)
                } else {
                    Task::none()
                }
            }
            Message::OpenInfo(path) => {
                self.confirm_remove = None;
                self.confirm_hide = None;
                if self.devices.contains_key(&path) {
                    self.info_device = Some(path);
                }
                Task::none()
            }
            Message::CloseInfo => {
                self.info_device = None;
                Task::none()
            }
            Message::CopyText { label, value } => {
                let toast_task = self.show_toast(format!("Copied {label}"), ToastKind::Info);
                Task::batch([iced::clipboard::write(value), toast_task])
            }

            Message::CardPressed(path) => match self.click_tracker.on_press(path, Instant::now()) {
                ClickDecision::PendingCommit(seq) => {
                    Task::perform(Delay::new(CLICK_COMMIT_DELAY), move |_| {
                        Message::CommitCardClick(seq)
                    })
                }
                ClickDecision::DoubleClick(path) => {
                    let powered = self.active_adapter_state().is_some_and(|a| a.powered);
                    let action = self
                        .devices
                        .get(&path)
                        .and_then(|dev| resolve_double_click_action(dev, powered));
                    match action {
                        Some(DoubleClickAction::Disconnect) => {
                            self.update(Message::DisconnectDevice(path))
                        }
                        Some(DoubleClickAction::Connect) => {
                            self.update(Message::ConnectDevice(path))
                        }
                        Some(DoubleClickAction::Pair) => self.update(Message::PairDevice(path)),
                        None => Task::none(),
                    }
                }
            },
            Message::CommitCardClick(seq) => {
                if let Some(path) = self.click_tracker.on_commit(seq) {
                    self.toggle_expand_path(path)
                } else {
                    Task::none()
                }
            }
            Message::ToggleExpand(path) => {
                self.click_tracker.clear();
                self.toggle_expand_path(path)
            }
            Message::RequestHideDevice(path) => {
                self.confirm_remove = None;
                self.confirm_hide = Some(path);
                Task::none()
            }
            Message::HideDevice(path, scope) => {
                self.confirm_hide = None;
                let Some(dev) = self.devices.get(&path) else {
                    return Task::none();
                };
                if dev.connected {
                    return Task::none();
                }
                let mac = normalize_mac(&dev.address);
                if mac.is_empty() {
                    return Task::none();
                }
                if self.expanded_device.as_deref() == Some(path.as_str()) {
                    self.expanded_device = None;
                }
                match scope {
                    HideScope::Session => {
                        self.session_hidden.insert(mac);
                        self.show_toast("Hidden for this session", ToastKind::Info)
                    }
                    HideScope::Persistent => {
                        self.session_hidden.remove(&mac);
                        self.config.hide_device(&mac);
                        self.save_config();
                        self.show_toast("Device hidden", ToastKind::Info)
                    }
                }
            }
            Message::CancelHide => {
                self.confirm_hide = None;
                Task::none()
            }
            Message::ConnectDevice(path) => {
                if let Some(dev) = self.devices.get_mut(&path) {
                    dev.in_flight = Some(DeviceOp::Connecting);
                    self.notice = None;
                    self.backend
                        .send(BackendCommand::Connect { device_path: path });
                }
                Task::none()
            }
            Message::DisconnectDevice(path) => {
                if let Some(dev) = self.devices.get_mut(&path) {
                    dev.in_flight = Some(DeviceOp::Disconnecting);
                    self.notice = None;
                    self.backend
                        .send(BackendCommand::Disconnect { device_path: path });
                }
                Task::none()
            }
            Message::PairDevice(path) => {
                if let Some(dev) = self.devices.get_mut(&path) {
                    if dev.paired {
                        return Task::none();
                    }
                    dev.in_flight = Some(DeviceOp::Pairing);
                    self.notice = None;
                    self.backend
                        .send(BackendCommand::Pair { device_path: path });
                }
                Task::none()
            }
            Message::ToggleTrust(path) => {
                if let Some(dev) = self.devices.get(&path) {
                    let trusted = !dev.trusted;
                    self.backend.send(BackendCommand::SetTrusted {
                        device_path: path,
                        trusted,
                    });
                }
                Task::none()
            }
            Message::ToggleBlock(path) => {
                if let Some(dev) = self.devices.get(&path) {
                    let blocked = !dev.blocked;
                    self.backend.send(BackendCommand::SetBlocked {
                        device_path: path,
                        blocked,
                    });
                }
                Task::none()
            }
            Message::RemoveDevice(path) => {
                self.confirm_hide = None;
                let needs_confirm = self
                    .devices
                    .get(&path)
                    .is_some_and(|d| d.paired)
                    && self.confirm_remove.as_deref() != Some(path.as_str());
                if needs_confirm {
                    self.confirm_remove = Some(path);
                    return Task::none();
                }
                self.confirm_remove = None;
                if let Some(dev) = self.devices.get_mut(&path) {
                    let adapter_path = dev.adapter_path.clone();
                    dev.in_flight = Some(DeviceOp::Removing);
                    self.notice = None;
                    self.backend.send(BackendCommand::RemoveDevice {
                        adapter_path,
                        device_path: path,
                    });
                }
                Task::none()
            }
            Message::CancelRemove => {
                self.confirm_remove = None;
                Task::none()
            }
            Message::CopyAddress(address) => {
                let toast_task = self.show_toast(format!("Copied {address}"), ToastKind::Info);
                Task::batch([iced::clipboard::write(address), toast_task])
            }

            Message::AudioProfilesLoaded {
                device_path,
                attempt,
                state,
            } => {
                let Some(dev) = self.devices.get(&device_path) else {
                    return Task::none();
                };
                if !dev.connected || !dev.is_audio_device() {
                    self.audio_profiles.remove(&device_path);
                    return Task::none();
                }
                if attempt == 0
                    && matches!(state, AudioProfileState::Unavailable(_))
                    && self.expanded_device.as_deref() == Some(device_path.as_str())
                {
                    self.audio_profiles
                        .insert(device_path.clone(), AudioProfileState::Loading);
                    return self.schedule_audio_query(&device_path, AUDIO_RETRY_DELAY, 1);
                }
                self.audio_profiles.insert(device_path, state);
                Task::none()
            }
            Message::SelectAudioProfile {
                device_path,
                card_id,
                profile_index,
            } => Task::perform(
                audio::set_audio_profile(card_id, profile_index),
                move |result| Message::AudioProfileSwitched {
                    device_path,
                    result,
                },
            ),
            Message::AudioProfileSwitched {
                device_path,
                result,
            } => match result {
                Ok(()) => {
                    let refresh =
                        self.schedule_audio_query(&device_path, AUDIO_SWITCH_SETTLE_DELAY, 1);
                    let toast = self.show_toast("Audio profile switched", ToastKind::Success);
                    Task::batch([refresh, toast])
                }
                Err(err) => {
                    self.notice = Some((err, true));
                    Task::none()
                }
            },

            Message::PairingInputChanged(raw) => {
                if let Some(prompt) = &self.pairing_prompt {
                    self.pairing_input = sanitize_pairing_input(&prompt.kind, &raw);
                }
                Task::none()
            }
            Message::PairingToggleVisibility => {
                self.pairing_show_text = !self.pairing_show_text;
                Task::none()
            }
            Message::PairingSubmit => {
                let Some(prompt) = &self.pairing_prompt else {
                    return Task::none();
                };
                match prompt.kind {
                    PairingKind::RequestPinCode => {
                        if !self.pairing_input.is_empty() {
                            let pin = self.pairing_input.clone();
                            self.send_pairing_reply(AgentReply::PinCode(pin));
                        }
                    }
                    PairingKind::RequestPasskey => {
                        if let Ok(passkey) = self.pairing_input.parse::<u32>() {
                            self.send_pairing_reply(AgentReply::Passkey(passkey));
                        }
                    }
                    _ => {}
                }
                Task::none()
            }
            Message::PairingConfirm => {
                self.send_pairing_reply(AgentReply::Confirm);
                Task::none()
            }
            Message::PairingAcceptService(always_trust) => {
                self.send_pairing_reply(AgentReply::AcceptService { always_trust });
                Task::none()
            }
            Message::PairingReject => {
                self.send_pairing_reply(AgentReply::Rejected);
                Task::none()
            }
            Message::PairingCancel => {
                self.send_pairing_reply(AgentReply::Canceled);
                Task::none()
            }

            Message::OpenRename(path) => {
                self.confirm_remove = None;
                if let Some(dev) = self.devices.get(&path) {
                    self.rename_modal = Some(RenameState {
                        device_path: dev.path.clone(),
                        address: dev.address.clone(),
                        current_alias: dev.alias.clone(),
                        draft: dev.alias.clone(),
                    });
                    return iced::widget::operation::focus(rename_input_id());
                }
                Task::none()
            }
            Message::RenameDraftChanged(val) => {
                if let Some(modal) = &mut self.rename_modal {
                    modal.draft = val;
                }
                Task::none()
            }
            Message::RenameReset => {
                if let Some(modal) = self.rename_modal.take() {
                    self.backend.send(BackendCommand::SetAlias {
                        device_path: modal.device_path,
                        alias: String::new(),
                    });
                }
                Task::none()
            }
            Message::RenameSave => {
                if let Some(modal) = self.rename_modal.take() {
                    let trimmed = modal.draft.trim().to_string();
                    if trimmed != modal.current_alias {
                        self.backend.send(BackendCommand::SetAlias {
                            device_path: modal.device_path,
                            alias: trimmed,
                        });
                    }
                }
                Task::none()
            }
            Message::CloseRename => {
                self.rename_modal = None;
                Task::none()
            }
        }
    }

    fn handle_backend_event(&mut self, event: BackendEvent) -> Task<Message> {
        match event {
            BackendEvent::StatusChanged(status) => {
                self.status = status;
                if status != BackendStatus::Ready {
                    self.expanded_device = None;
                    self.confirm_remove = None;
                    self.confirm_hide = None;
                    self.rename_modal = None;
                    self.info_device = None;
                    self.owned_scans.clear();
                    self.audio_profiles.clear();
                }
                Task::none()
            }
            BackendEvent::FullSnapshot { adapters, devices } => {
                self.adapters = adapters.into_iter().map(|a| (a.path.clone(), a)).collect();
                self.devices = devices.into_iter().map(|d| (d.path.clone(), d)).collect();
                self.owned_scans.clear();
                self.confirm_remove = None;
                self.confirm_hide = None;
                self.rename_modal = None;
                self.ensure_active_adapter();
                if self
                    .expanded_device
                    .as_deref()
                    .is_some_and(|p| !self.devices.contains_key(p))
                {
                    self.expanded_device = None;
                }
                if self
                    .info_device
                    .as_deref()
                    .is_some_and(|p| !self.devices.contains_key(p))
                {
                    self.info_device = None;
                }
                self.audio_profiles
                    .retain(|path, _| self.devices.contains_key(path));
                Task::none()
            }
            BackendEvent::AdapterUpserted(adapter) => {
                if !adapter.powered && self.active_adapter.as_deref() == Some(adapter.path.as_str())
                {
                    self.confirm_remove = None;
                    self.confirm_hide = None;
                    self.rename_modal = None;
                }
                self.adapters.insert(adapter.path.clone(), adapter);
                self.ensure_active_adapter();
                Task::none()
            }
            BackendEvent::AdapterRemoved(path) => {
                self.adapters.remove(&path);
                self.owned_scans.remove(&path);
                self.devices.retain(|_, d| d.adapter_path != path);
                self.audio_profiles
                    .retain(|dev_path, _| self.devices.contains_key(dev_path));
                if self
                    .confirm_remove
                    .as_deref()
                    .is_some_and(|p| !self.devices.contains_key(p))
                {
                    self.confirm_remove = None;
                }
                if self
                    .confirm_hide
                    .as_deref()
                    .is_some_and(|p| !self.devices.contains_key(p))
                {
                    self.confirm_hide = None;
                }
                if self
                    .info_device
                    .as_deref()
                    .is_some_and(|p| !self.devices.contains_key(p))
                {
                    self.info_device = None;
                }
                if self
                    .rename_modal
                    .as_ref()
                    .is_some_and(|m| !self.devices.contains_key(&m.device_path))
                {
                    self.rename_modal = None;
                }
                if self.active_adapter.as_deref() == Some(path.as_str()) {
                    self.set_active_adapter(None, false);
                    self.ensure_active_adapter();
                    self.expanded_device = None;
                    self.confirm_remove = None;
                    self.confirm_hide = None;
                    self.info_device = None;
                    self.rename_modal = None;
                    if self.settings_open {
                        self.adapter_alias_draft = self
                            .active_adapter_state()
                            .map(|a| a.alias.clone())
                            .unwrap_or_default();
                    }
                }
                Task::none()
            }
            BackendEvent::DeviceUpserted(mut device) => {
                let (prev_connected, prev_is_audio) = self
                    .devices
                    .get(&device.path)
                    .map(|d| (d.connected, d.is_audio_device()))
                    .unwrap_or((false, false));
                if let Some(existing) = self.devices.get(&device.path) {
                    device.in_flight = existing.in_flight;
                }
                let path = device.path.clone();
                let now_connected = device.connected;
                let is_audio = device.is_audio_device();
                self.devices.insert(path.clone(), device);

                if !now_connected {
                    self.audio_profiles.remove(&path);
                    return Task::none();
                }
                if is_audio
                    && (!prev_connected || !prev_is_audio)
                    && self.expanded_device.as_deref() == Some(path.as_str())
                {
                    return self.fetch_audio_profiles_for(&path, false);
                }
                Task::none()
            }
            BackendEvent::DeviceRemoved(path) => {
                self.devices.remove(&path);
                self.audio_profiles.remove(&path);
                if self.expanded_device.as_deref() == Some(path.as_str()) {
                    self.expanded_device = None;
                }
                if self.confirm_remove.as_deref() == Some(path.as_str()) {
                    self.confirm_remove = None;
                }
                if self.confirm_hide.as_deref() == Some(path.as_str()) {
                    self.confirm_hide = None;
                }
                if self.info_device.as_deref() == Some(path.as_str()) {
                    self.info_device = None;
                }
                if self
                    .rename_modal
                    .as_ref()
                    .is_some_and(|m| m.device_path == path)
                {
                    self.rename_modal = None;
                }
                Task::none()
            }
            BackendEvent::BatteryUpdated { path, percentage } => {
                if let Some(dev) = self.devices.get_mut(&path) {
                    dev.battery = percentage;
                }
                Task::none()
            }
            BackendEvent::DiscoveryOwnershipChanged {
                adapter_path,
                owned,
            } => {
                if owned {
                    self.owned_scans.insert(adapter_path);
                } else {
                    self.owned_scans.remove(&adapter_path);
                }
                Task::none()
            }
            BackendEvent::AliasUpdated { reset } => {
                if reset {
                    self.show_toast("Device alias reset", ToastKind::Info)
                } else {
                    self.show_toast("Device renamed", ToastKind::Success)
                }
            }
            BackendEvent::AdapterAliasUpdated { reset } => {
                if reset {
                    self.show_toast("Adapter alias reset", ToastKind::Info)
                } else {
                    self.show_toast("Adapter renamed", ToastKind::Success)
                }
            }
            BackendEvent::RfkillChanged(state) => {
                self.rfkill = state;
                Task::none()
            }
            BackendEvent::TrafficUpdated(snapshot) => {
                self.traffic = snapshot;
                Task::none()
            }
            BackendEvent::DeviceOpFinished { path, op, outcome } => {
                if let Some(dev) = self.devices.get_mut(&path) {
                    dev.in_flight = None;
                }
                match outcome {
                    DeviceOpOutcome::Succeeded => match op {
                        DeviceOp::Removing => self.show_toast("Device removed", ToastKind::Info),
                        DeviceOp::Pairing => self.show_toast("Device paired", ToastKind::Success),
                        DeviceOp::Connecting => {
                            let toast = self.show_toast("Connected", ToastKind::Success);
                            if self.expanded_device.as_deref() == Some(path.as_str()) {
                                let audio_task = self.fetch_audio_profiles_for(&path, false);
                                Task::batch([toast, audio_task])
                            } else {
                                toast
                            }
                        }
                        DeviceOp::Disconnecting => {
                            self.audio_profiles.remove(&path);
                            self.show_toast("Disconnected", ToastKind::Info)
                        }
                    },
                    DeviceOpOutcome::Canceled => {
                        self.show_toast("Operation canceled", ToastKind::Info)
                    }
                    DeviceOpOutcome::Failed(err) => {
                        self.notice = Some((err, true));
                        Task::none()
                    }
                }
            }
            BackendEvent::Notice { message, is_error } => {
                self.notice = Some((message, is_error));
                Task::none()
            }
            BackendEvent::AgentPrompt(prompt) => {
                let needs_focus = matches!(
                    prompt.kind,
                    PairingKind::RequestPinCode | PairingKind::RequestPasskey
                );
                let same_prompt = self
                    .pairing_prompt
                    .as_ref()
                    .is_some_and(|existing| existing.prompt_id == prompt.prompt_id);
                if !same_prompt {
                    self.pairing_input.clear();
                    self.pairing_show_text = false;
                }
                self.pairing_prompt = Some(prompt);
                if !same_prompt && needs_focus {
                    iced::widget::operation::focus(pairing_input_id())
                } else {
                    Task::none()
                }
            }
            BackendEvent::AgentPromptDismissed { prompt_id } => {
                if self
                    .pairing_prompt
                    .as_ref()
                    .is_some_and(|p| p.prompt_id == prompt_id)
                {
                    self.pairing_prompt = None;
                    self.pairing_input.clear();
                    self.pairing_show_text = false;
                }
                Task::none()
            }
            BackendEvent::Stopped => Task::none(),
        }
    }

    fn begin_shutdown(&mut self) -> Task<Message> {
        if self.exiting {
            return Task::none();
        }
        self.exiting = true;
        let ack_rx = self.backend.shutdown();
        Task::perform(
            async move {
                let mut ack = ack_rx.fuse();
                let mut deadline = Delay::new(SHUTDOWN_DEADLINE).fuse();
                select_biased! {
                    _ = ack => {}
                    _ = deadline => {}
                }
            },
            |()| Message::ForceExit,
        )
    }

    pub fn subscription(&self) -> Subscription<Message> {
        let mut subs = vec![
            Subscription::run(backend_event_stream),
            Subscription::run(ipc_event_stream),
            Subscription::run(theme_tick_stream),
            iced::window::close_requests().map(|_| Message::RequestClose),
            iced::window::resize_events().map(|(_, s)| Message::WindowResized(s.width, s.height)),
        ];
        if self.pairing_prompt.is_some()
            || self.rename_modal.is_some()
            || self.info_device.is_some()
            || self.settings_open
            || self.confirm_remove.is_some()
            || self.confirm_hide.is_some()
            || self.expanded_device.is_some()
        {
            subs.push(iced::event::listen_with(escape_key_filter));
        }
        Subscription::batch(subs)
    }

    pub fn view(&self) -> Element<'_, Message> {
        views::view_root(self)
    }
}

fn escape_key_filter(
    event: iced::Event,
    _status: iced::event::Status,
    _window: iced::window::Id,
) -> Option<Message> {
    if let iced::Event::Keyboard(iced::keyboard::Event::KeyPressed {
        key: iced::keyboard::Key::Named(iced::keyboard::key::Named::Escape),
        ..
    }) = event
    {
        Some(Message::EscapePressed)
    } else {
        None
    }
}

fn sanitize_pairing_input(kind: &PairingKind, raw: &str) -> String {
    match kind {
        PairingKind::RequestPasskey => raw.chars().filter(|c| c.is_ascii_digit()).take(6).collect(),
        PairingKind::RequestPinCode => raw.chars().take(16).collect(),
        _ => String::new(),
    }
}

fn backend_event_stream() -> Pin<Box<dyn Stream<Item = Message> + Send>> {
    if let Some(rx) = crate::bluez::take_backend_receiver() {
        Box::pin(rx.map(Message::Backend))
    } else {
        Box::pin(iced_futures::futures::stream::pending())
    }
}

fn ipc_event_stream() -> Pin<Box<dyn Stream<Item = Message> + Send>> {
    if let Some(rx) = crate::take_ipc_receiver() {
        Box::pin(rx.map(Message::Ipc))
    } else {
        Box::pin(iced_futures::futures::stream::pending())
    }
}

fn theme_tick_stream() -> Pin<Box<dyn Stream<Item = Message> + Send>> {
    Box::pin(iced_futures::futures::stream::unfold((), |()| async {
        Delay::new(THEME_POLL_INTERVAL).await;
        Some((Message::ThemeTick, ()))
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pairing_input_sanitizer_enforces_numeric_passkey_and_pin_limits() {
        assert_eq!(
            sanitize_pairing_input(&PairingKind::RequestPasskey, "12a34b5678"),
            "123456"
        );
        assert_eq!(
            sanitize_pairing_input(&PairingKind::RequestPinCode, "0123456789abcdefgh"),
            "0123456789abcdef"
        );
    }

    #[test]
    fn click_tracker_disambiguates_single_double_and_stale_commits() {
        let mut tracker = ClickTracker::default();
        let t0 = Instant::now();

        let ClickDecision::PendingCommit(seq1) = tracker.on_press("/d1".into(), t0) else {
            panic!("expected pending commit");
        };
        assert_eq!(tracker.on_commit(seq1 + 99), None);
        assert_eq!(tracker.on_commit(seq1).as_deref(), Some("/d1"));
        assert_eq!(tracker.on_commit(seq1), None);

        let ClickDecision::PendingCommit(seq2) = tracker.on_press("/d1".into(), t0) else {
            panic!("expected pending commit");
        };
        let decision = tracker.on_press("/d1".into(), t0 + Duration::from_millis(120));
        assert_eq!(decision, ClickDecision::DoubleClick("/d1".into()));
        // Stale timer firing after double click is discarded with zero drawer twitch.
        assert_eq!(tracker.on_commit(seq2), None);

        // Rapid click on a different card cancels previous card's commit.
        let ClickDecision::PendingCommit(seq3) = tracker.on_press("/d1".into(), t0) else {
            panic!("expected pending commit");
        };
        let ClickDecision::PendingCommit(seq4) =
            tracker.on_press("/d2".into(), t0 + Duration::from_millis(80))
        else {
            panic!("expected pending commit on new card");
        };
        assert_eq!(tracker.on_commit(seq3), None);
        assert_eq!(tracker.on_commit(seq4).as_deref(), Some("/d2"));
    }

    #[test]
    fn double_click_action_and_per_device_hiding_follow_precedence() {
        let mut dev = DeviceState {
            path: "/d1".into(),
            address: "AA:BB:CC:DD:EE:FF".into(),
            alias: "Headphones".into(),
            ..Default::default()
        };
        assert_eq!(
            resolve_double_click_action(&dev, true),
            Some(DoubleClickAction::Pair)
        );
        assert_eq!(resolve_double_click_action(&dev, false), None);

        dev.paired = true;
        assert_eq!(
            resolve_double_click_action(&dev, true),
            Some(DoubleClickAction::Connect)
        );

        dev.blocked = true;
        assert_eq!(resolve_double_click_action(&dev, true), None);
        dev.blocked = false;

        dev.connected = true;
        assert_eq!(
            resolve_double_click_action(&dev, true),
            Some(DoubleClickAction::Disconnect)
        );

        let mut session = HashSet::new();
        let mut persistent = BTreeSet::new();
        session.insert("AA:BB:CC:DD:EE:FF".to_string());
        // Connected device stays visible even if MAC is hidden.
        assert_eq!(
            classify_device_visibility(&dev, true, &session, &persistent),
            DeviceVisibility::Visible
        );
        dev.connected = false;
        assert_eq!(
            classify_device_visibility(&dev, true, &session, &persistent),
            DeviceVisibility::HiddenByMac
        );
        session.clear();
        persistent.insert("AA:BB:CC:DD:EE:FF".to_string());
        assert_eq!(
            classify_device_visibility(&dev, true, &session, &persistent),
            DeviceVisibility::HiddenByMac
        );
    }

    #[test]
    fn device_info_plain_and_json_formatters_include_decoded_fields_and_adapter() {
        let mut dev = DeviceState {
            path: "/org/bluez/hci0/dev_11_22_33_44_55_66".into(),
            adapter_path: "/org/bluez/hci0".into(),
            address: "11:22:33:44:55:66".into(),
            alias: "WH-1000XM5".into(),
            class: (4 << 8) | (6 << 2),
            paired: true,
            bonded: true,
            services_resolved: true,
            uuids: vec!["0000110b-0000-1000-8000-00805f9b34fb".into()],
            ..Default::default()
        };
        dev.refresh_kind();

        let adapter = AdapterState {
            path: "/org/bluez/hci0".into(),
            address: "AA:00:11:22:33:44".into(),
            name: "dusk-host".into(),
            alias: "Dusk PC".into(),
            powered: true,
            ..Default::default()
        };

        let plain = format_device_info_plain(&dev, Some(&adapter));
        assert!(plain.contains("Alias: WH-1000XM5"));
        assert!(plain.contains("Bonded: yes"));
        assert!(plain.contains("Audio/Video — Headphones"));
        assert!(plain.contains("Audio Sink (A2DP)"));
        assert!(plain.contains("Adapter: Dusk PC (hci0)"));

        let json_str = format_device_info_json(&dev, Some(&adapter));
        let parsed: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        assert_eq!(parsed["Alias"], "WH-1000XM5");
        assert_eq!(parsed["Bonded"], true);
        assert_eq!(parsed["Adapter"]["id"], "hci0");
        assert_eq!(parsed["UUIDs"][0]["name"], "Audio Sink (A2DP)");
    }
}
