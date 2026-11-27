//! Root Iced 0.14 application state, subscriptions, message reducer, and shutdown wiring.

pub mod style;
pub mod views;

use crate::audio;
use crate::bluez::{BackendCommand, BackendEvent, BackendHandle, BackendStatus};
use crate::model::{
    AdapterState, AgentReply, AudioProfileState, DeviceOp, DeviceOpOutcome, DeviceState,
    PairingKind, PairingPrompt,
};
use crate::theme::{AppTheme, ThemeStamp};
use crate::{IpcCommand, LaunchOptions};
use futures_timer::Delay;
use iced::{Subscription, Task};
use iced_futures::futures::{FutureExt, Stream, StreamExt, select_biased};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::pin::Pin;
use std::time::Duration;

pub type Element<'a, Message> =
    iced_core::Element<'a, Message, iced_core::Theme, crate::renderer::Renderer>;

const THEME_POLL_INTERVAL: Duration = Duration::from_secs(2);
const TOAST_DURATION: Duration = Duration::from_secs(3);
const SHUTDOWN_DEADLINE: Duration = Duration::from_millis(400);
const AUDIO_RETRY_DELAY: Duration = Duration::from_millis(750);
const AUDIO_SWITCH_SETTLE_DELAY: Duration = Duration::from_millis(150);

pub fn rename_input_id() -> iced_core::widget::Id {
    iced_core::widget::Id::new("dusky-bluetooth-rename-input")
}

pub fn pairing_input_id() -> iced_core::widget::Id {
    iced_core::widget::Id::new("dusky-bluetooth-pairing-input")
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

    // Adapter controls
    CycleAdapter,
    TogglePower(bool),
    ToggleDiscoverable(bool),
    ToggleScan,
    ToggleHideUnnamed,

    // Device actions
    ToggleExpand(String),
    ConnectDevice(String),
    DisconnectDevice(String),
    PairDevice(String),
    ToggleTrust(String),
    ToggleBlock(String),
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
    pub status: BackendStatus,
    pub adapters: BTreeMap<String, AdapterState>,
    pub active_adapter: Option<String>,
    pub owned_scans: HashSet<String>,
    pub devices: BTreeMap<String, DeviceState>,
    pub expanded_device: Option<String>,
    pub confirm_remove: Option<String>,
    pub audio_profiles: HashMap<String, AudioProfileState>,
    pub hide_unnamed: bool,
    pub notice: Option<(String, bool)>,
    pub toast: Option<Toast>,
    toast_seq: u64,
    pub pairing_prompt: Option<PairingPrompt>,
    pub pairing_input: String,
    pub pairing_show_text: bool,
    pub rename_modal: Option<RenameState>,
    exiting: bool,
}

impl BluetoothApp {
    pub fn boot(_launch: LaunchOptions) -> (Self, Task<Message>) {
        let (theme, theme_stamp) = AppTheme::load_with_stamp();
        let backend = crate::bluez::spawn_backend();

        let app = Self {
            theme,
            theme_stamp,
            backend,
            status: BackendStatus::Connecting,
            adapters: BTreeMap::new(),
            active_adapter: None,
            owned_scans: HashSet::new(),
            devices: BTreeMap::new(),
            expanded_device: None,
            confirm_remove: None,
            audio_profiles: HashMap::new(),
            hide_unnamed: true,
            notice: None,
            toast: None,
            toast_seq: 0,
            pairing_prompt: None,
            pairing_input: String::new(),
            pairing_show_text: false,
            rename_modal: None,
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

    fn ensure_active_adapter(&mut self) {
        if let Some(current) = &self.active_adapter
            && self.adapters.contains_key(current)
        {
            return;
        }
        self.active_adapter = self.adapters.keys().next().cloned();
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
                } else if self.confirm_remove.is_some() {
                    self.confirm_remove = None;
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
                    self.active_adapter = keys.into_iter().nth(next_idx);
                    self.expanded_device = None;
                    self.confirm_remove = None;
                }
                Task::none()
            }
            Message::TogglePower(powered) => {
                if let Some(adapter_path) = self.active_adapter.clone() {
                    self.notice = None;
                    if !powered {
                        self.confirm_remove = None;
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
                    self.backend.send(BackendCommand::SetAdapterDiscoverable {
                        adapter_path,
                        discoverable,
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
                self.hide_unnamed = !self.hide_unnamed;
                Task::none()
            }

            Message::ToggleExpand(path) => {
                self.confirm_remove = None;
                if self.expanded_device.as_deref() == Some(path.as_str()) {
                    self.expanded_device = None;
                    Task::none()
                } else {
                    self.expanded_device = Some(path.clone());
                    self.fetch_audio_profiles_for(&path, true)
                }
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
                    self.rename_modal = None;
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
                self.rename_modal = None;
                self.ensure_active_adapter();
                if self
                    .expanded_device
                    .as_deref()
                    .is_some_and(|p| !self.devices.contains_key(p))
                {
                    self.expanded_device = None;
                }
                self.audio_profiles
                    .retain(|path, _| self.devices.contains_key(path));
                Task::none()
            }
            BackendEvent::AdapterUpserted(adapter) => {
                if !adapter.powered && self.active_adapter.as_deref() == Some(adapter.path.as_str())
                {
                    self.confirm_remove = None;
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
                    .rename_modal
                    .as_ref()
                    .is_some_and(|m| !self.devices.contains_key(&m.device_path))
                {
                    self.rename_modal = None;
                }
                if self.active_adapter.as_deref() == Some(path.as_str()) {
                    self.active_adapter = None;
                    self.ensure_active_adapter();
                    self.expanded_device = None;
                    self.confirm_remove = None;
                    self.rename_modal = None;
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
        ];
        if self.pairing_prompt.is_some()
            || self.rename_modal.is_some()
            || self.confirm_remove.is_some()
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
}
