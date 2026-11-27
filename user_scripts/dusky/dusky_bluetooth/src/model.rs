//! Pure, D-Bus-agnostic Bluetooth domain types, device classification, error
//! translation, deterministic sorting, and the pairing-agent state machine.

use iced_futures::futures::channel::oneshot;
use std::cmp::Reverse;
use std::collections::{BTreeMap, VecDeque};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VisibilityMode {
    Hidden,
    AlwaysVisible,
    Temporary { timeout_secs: u32 },
}

impl VisibilityMode {
    pub const MIN_TIMEOUT_SECS: u32 = 30;
    pub const MAX_TIMEOUT_SECS: u32 = 1800;

    pub fn clamp_timeout(secs: u32) -> u32 {
        secs.clamp(Self::MIN_TIMEOUT_SECS, Self::MAX_TIMEOUT_SECS)
    }

    pub fn from_adapter_props(discoverable: bool, discoverable_timeout: u32) -> Self {
        if !discoverable {
            Self::Hidden
        } else if discoverable_timeout == 0 {
            Self::AlwaysVisible
        } else {
            Self::Temporary {
                timeout_secs: discoverable_timeout,
            }
        }
    }

    /// Returns `(discoverable, optional_timeout_to_write_before_enabling)`.
    pub fn to_bluez_props(self) -> (bool, Option<u32>) {
        match self {
            Self::Hidden => (false, None),
            Self::AlwaysVisible => (true, Some(0)),
            Self::Temporary { timeout_secs } => (true, Some(Self::clamp_timeout(timeout_secs))),
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RfkillState {
    pub soft_blocked: bool,
    pub hard_blocked: bool,
    pub label: Option<String>,
}

impl RfkillState {
    pub fn is_blocked(&self) -> bool {
        self.soft_blocked || self.hard_blocked
    }

    pub fn short_label(&self) -> &'static str {
        if self.hard_blocked {
            "Hard RF-kill"
        } else if self.soft_blocked {
            "Soft RF-kill"
        } else {
            "Unblocked"
        }
    }

    pub fn detail(&self) -> Option<String> {
        if !self.is_blocked() {
            return None;
        }
        let kind = if self.hard_blocked && self.soft_blocked {
            "hardware and software RF-kill switches"
        } else if self.hard_blocked {
            "a hardware RF-kill switch"
        } else {
            "a software RF-kill switch"
        };
        match self.label.as_deref().filter(|s| !s.is_empty()) {
            Some(name) => Some(format!("Bluetooth is blocked by {kind} ({name}).")),
            None => Some(format!("Bluetooth is blocked by {kind}.")),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct PanTraffic {
    pub iface: String,
    pub total_tx: u64,
    pub total_rx: u64,
    pub tx_rate_bps: f64,
    pub rx_rate_bps: f64,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct TrafficSnapshot {
    pub adapter_short_id: String,
    pub total_tx: u64,
    pub total_rx: u64,
    pub tx_rate_bps: f64,
    pub rx_rate_bps: f64,
    pub active_tx: bool,
    pub active_rx: bool,
    pub available: bool,
    pub pan: Option<PanTraffic>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HideScope {
    Session,
    Persistent,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct AdapterState {
    pub path: String,
    pub address: String,
    pub name: String,
    pub alias: String,
    pub powered: bool,
    pub discoverable: bool,
    pub discoverable_timeout: u32,
    pub discovering: bool,
    pub pairable: bool,
    pub pairable_timeout: u32,
    pub class: u32,
    pub modalias: Option<String>,
    pub address_type: Option<String>,
    pub power_state: Option<String>,
}

impl AdapterState {
    pub fn display_name(&self) -> &str {
        let alias = self.alias.trim();
        if !alias.is_empty() {
            return alias;
        }
        let name = self.name.trim();
        if !name.is_empty() {
            return name;
        }
        let address = self.address.trim();
        if !address.is_empty() {
            return address;
        }
        self.path.rsplit('/').next().unwrap_or(&self.path)
    }

    pub fn short_id(&self) -> &str {
        self.path.rsplit('/').next().unwrap_or(&self.path)
    }

    pub fn visibility_mode(&self) -> VisibilityMode {
        VisibilityMode::from_adapter_props(self.discoverable, self.discoverable_timeout)
    }

    pub fn is_rfkill_blocked(&self) -> bool {
        self.power_state
            .as_deref()
            .is_some_and(|s| s.eq_ignore_ascii_case("off-blocked"))
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Hash)]
pub enum DeviceKind {
    Headphones,
    Speaker,
    Keyboard,
    Mouse,
    Phone,
    Controller,
    Computer,
    #[default]
    Generic,
}

impl DeviceKind {
    pub fn icon_name(self) -> &'static str {
        match self {
            Self::Headphones => "headphones",
            Self::Speaker => "speaker",
            Self::Keyboard => "keyboard",
            Self::Mouse => "mouse",
            Self::Phone => "phone",
            Self::Controller => "controller",
            Self::Computer => "computer",
            Self::Generic => "bluetooth",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Headphones => "Headphones",
            Self::Speaker => "Speaker",
            Self::Keyboard => "Keyboard",
            Self::Mouse => "Mouse",
            Self::Phone => "Phone",
            Self::Controller => "Controller",
            Self::Computer => "Computer",
            Self::Generic => "Bluetooth device",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeviceOp {
    Connecting,
    Disconnecting,
    Pairing,
    Removing,
}

impl DeviceOp {
    pub fn label(self) -> &'static str {
        match self {
            Self::Connecting => "Connecting…",
            Self::Disconnecting => "Disconnecting…",
            Self::Pairing => "Pairing…",
            Self::Removing => "Removing…",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DeviceOpOutcome {
    Succeeded,
    Canceled,
    Failed(String),
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DeviceState {
    pub path: String,
    pub adapter_path: String,
    pub address: String,
    pub address_type: Option<String>,
    pub name: Option<String>,
    pub alias: String,
    pub icon_hint: Option<String>,
    pub class: u32,
    pub appearance: u16,
    pub uuids: Vec<String>,
    pub paired: bool,
    pub bonded: bool,
    pub connected: bool,
    pub trusted: bool,
    pub blocked: bool,
    pub services_resolved: bool,
    pub legacy_pairing: bool,
    pub battery: Option<u8>,
    pub rssi: Option<i16>,
    pub tx_power: Option<i16>,
    pub modalias: Option<String>,
    pub wake_allowed: Option<bool>,
    pub preferred_bearer: Option<String>,
    pub cable_pairing: Option<bool>,
    pub manufacturer_data: BTreeMap<u16, Vec<u8>>,
    pub service_data: BTreeMap<String, Vec<u8>>,
    pub advertising_flags: Vec<u8>,
    pub kind: DeviceKind,
    pub in_flight: Option<DeviceOp>,
}

impl DeviceState {
    pub fn refresh_kind(&mut self) {
        self.kind = classify_device(
            self.icon_hint.as_deref(),
            self.class,
            self.appearance,
            &self.uuids,
        );
    }

    pub fn is_unnamed(&self) -> bool {
        is_unnamed_device(&self.alias, self.name.as_deref(), &self.address)
    }

    pub fn should_hide_when_unnamed(&self) -> bool {
        !self.paired
            && !self.connected
            && self.is_unnamed()
            && !is_keyboard_or_combo(self.class, self.appearance, self.kind)
    }

    pub fn display_name(&self) -> &str {
        let alias = self.alias.trim();
        if !alias.is_empty() && !matches_mac(alias, &self.address) {
            return alias;
        }
        if let Some(name) = self.name.as_deref().map(str::trim)
            && !name.is_empty()
            && !matches_mac(name, &self.address)
        {
            return name;
        }
        "Unnamed device"
    }

    pub fn is_audio_device(&self) -> bool {
        if matches!(self.kind, DeviceKind::Headphones | DeviceKind::Speaker) {
            return true;
        }
        self.uuids.iter().any(|uuid| {
            matches!(
                short_uuid(uuid),
                Some(0x1108 | 0x110A | 0x110B | 0x111E | 0x111F)
            )
        })
    }

    pub fn supports_generic_connect(&self) -> bool {
        if self.uuids.is_empty() {
            // LE devices often omit UUIDs until their first connection.
            return true;
        }
        self.uuids.iter().any(|uuid| {
            matches!(
                short_uuid(uuid),
                Some(0x1108 | 0x110A | 0x110B | 0x111E | 0x111F | 0x1124 | 0x1812)
            ) || uuid.eq_ignore_ascii_case("03b80e5a-ede8-4b33-a751-6ce34ec4c700")
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AudioProfile {
    pub index: u32,
    pub name: String,
    pub description: String,
    pub priority: i32,
}

impl AudioProfile {
    pub fn short_label(&self) -> &str {
        let fallback = if !self.description.is_empty() {
            self.description.as_str()
        } else {
            self.name.as_str()
        };
        let lower = self.name.to_ascii_lowercase();
        if lower == "a2dp-sink" {
            return "A2DP Sink";
        }
        if let Some(codec) = lower.strip_prefix("a2dp-sink-") {
            return match codec {
                "sbc" => "A2DP (SBC)",
                "sbc_xq" => "A2DP (SBC-XQ)",
                "aac" => "A2DP (AAC)",
                "ldac" => "A2DP (LDAC)",
                "aptx" => "A2DP (aptX)",
                "aptx_hd" => "A2DP (aptX HD)",
                "aptx_ll" => "A2DP (aptX LL)",
                "aptx_ll_duplex" => "A2DP (aptX LL Duplex)",
                "faststream" => "A2DP (FastStream)",
                "faststream_duplex" => "A2DP (FastStream Duplex)",
                "lc3plus_hr" => "A2DP (LC3plus HR)",
                "opus_05" => "A2DP (Opus)",
                "opus_05_duplex" => "A2DP (Opus Duplex)",
                "opus_05_pro" => "A2DP (Opus Pro)",
                "opus_05_51" => "A2DP (Opus 5.1)",
                "opus_05_71" => "A2DP (Opus 7.1)",
                "opus_g" => "A2DP (Opus-G)",
                _ if codec.starts_with("opus_") => "A2DP (Opus)",
                _ if codec.starts_with("faststream") => "A2DP (FastStream)",
                _ => fallback,
            };
        }
        if lower == "a2dp-source" {
            return "A2DP Source";
        }
        if let Some(codec) = lower.strip_prefix("a2dp-source-") {
            return match codec {
                "sbc" => "A2DP Source (SBC)",
                "sbc_xq" => "A2DP Source (SBC-XQ)",
                "aac" => "A2DP Source (AAC)",
                "ldac" => "A2DP Source (LDAC)",
                "aptx" => "A2DP Source (aptX)",
                "aptx_hd" => "A2DP Source (aptX HD)",
                _ if codec.starts_with("opus_") => "A2DP Source (Opus)",
                _ => fallback,
            };
        }
        if lower == "bap-sink" || lower.starts_with("bap-sink-") {
            return "LE Audio (Sink)";
        }
        if lower == "bap-source" || lower.starts_with("bap-source-") {
            return "LE Audio (Source)";
        }
        if lower == "bap-duplex" || lower.starts_with("bap-duplex-") {
            return "LE Audio (Duplex)";
        }
        if lower.starts_with("headset-head-unit") {
            if lower.contains("lc3_swb") || lower.contains("lc3-swb") {
                return "Handsfree (LC3-SWB)";
            }
            if lower.contains("msbc") {
                return "Handsfree (mSBC)";
            }
            if lower.contains("cvsd") {
                return "Handsfree (CVSD)";
            }
            return "Handsfree (HFP/HSP)";
        }
        if lower == "off" {
            return "Off";
        }
        fallback
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AudioProfileState {
    Loading,
    Available {
        card_id: u32,
        profiles: Vec<AudioProfile>,
        active_index: Option<u32>,
    },
    Unavailable(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PairingKind {
    RequestPinCode,
    RequestPasskey,
    RequestConfirmation { passkey: Option<u32> },
    DisplayPinCode { pin: String },
    DisplayPasskey { passkey: u32, entered: u16 },
    AuthorizeService { uuid: String, service_name: String },
}

impl PairingKind {
    pub fn is_display_only(&self) -> bool {
        matches!(
            self,
            Self::DisplayPinCode { .. } | Self::DisplayPasskey { .. }
        )
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PairingPrompt {
    pub prompt_id: u64,
    pub device_path: String,
    pub device_label: String,
    pub address: String,
    pub kind: PairingKind,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AgentReply {
    PinCode(String),
    Passkey(u32),
    Confirm,
    AcceptService { always_trust: bool },
    Rejected,
    Canceled,
}

const MAX_QUEUED_PROMPTS: usize = 8;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolveOutcome {
    pub dismissed_id: u64,
    pub promoted: Option<PairingPrompt>,
    pub cancel_pairing_device: Option<String>,
}

struct ActivePrompt {
    prompt: PairingPrompt,
    reply_tx: Option<oneshot::Sender<AgentReply>>,
}

/// Pure pairing-agent state machine tracking an active prompt plus a bounded
/// FIFO queue for overlapping interactive requests (e.g. concurrent
/// `AuthorizeService` calls), monotonic IDs, and display-only auto-dismissal.
#[derive(Default)]
pub struct AgentState {
    next_id: u64,
    active: Option<ActivePrompt>,
    queue: VecDeque<ActivePrompt>,
}

impl AgentState {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn active_prompt(&self) -> Option<&PairingPrompt> {
        self.active.as_ref().map(|entry| &entry.prompt)
    }

    fn alloc_id(&mut self) -> u64 {
        self.next_id = self.next_id.saturating_add(1);
        self.next_id
    }

    fn promote_next(&mut self) -> Option<PairingPrompt> {
        if self.active.is_none() {
            self.active = self.queue.pop_front();
        }
        self.active.as_ref().map(|entry| entry.prompt.clone())
    }

    /// Starts or enqueues an interactive prompt (`RequestPinCode`,
    /// `RequestPasskey`, `RequestConfirmation`, `RequestAuthorization`,
    /// `AuthorizeService`). Returns `Some(prompt)` only when it becomes active
    /// immediately; returns `None` when queued behind an active prompt.
    pub fn begin_interactive(
        &mut self,
        device_path: String,
        device_label: String,
        address: String,
        kind: PairingKind,
    ) -> Result<(Option<PairingPrompt>, oneshot::Receiver<AgentReply>), &'static str> {
        if self.active.is_some() && self.queue.len() >= MAX_QUEUED_PROMPTS {
            return Err("canceled");
        }

        let prompt = PairingPrompt {
            prompt_id: self.alloc_id(),
            device_path,
            device_label,
            address,
            kind,
        };
        let (tx, rx) = oneshot::channel();
        let entry = ActivePrompt {
            prompt: prompt.clone(),
            reply_tx: Some(tx),
        };

        if self.active.is_none() {
            self.active = Some(entry);
            Ok((Some(prompt), rx))
        } else {
            self.queue.push_back(entry);
            Ok((None, rx))
        }
    }

    /// Shows a non-blocking PIN display prompt, preserving any active
    /// interactive prompt at the front of the queue.
    pub fn begin_display_pin(
        &mut self,
        device_path: String,
        device_label: String,
        address: String,
        pin: String,
    ) -> PairingPrompt {
        if let Some(prev) = self.active.take()
            && prev.reply_tx.is_some()
        {
            self.queue.push_front(prev);
        }
        let prompt = PairingPrompt {
            prompt_id: self.alloc_id(),
            device_path,
            device_label,
            address,
            kind: PairingKind::DisplayPinCode { pin },
        };
        self.active = Some(ActivePrompt {
            prompt: prompt.clone(),
            reply_tx: None,
        });
        prompt
    }

    /// Shows a non-blocking passkey display prompt, updating `entered` in place
    /// when BlueZ sends progressive keystroke updates for the same device/passkey.
    pub fn begin_display_passkey(
        &mut self,
        device_path: String,
        device_label: String,
        address: String,
        passkey: u32,
        entered: u16,
    ) -> PairingPrompt {
        if let Some(active) = self.active.as_mut()
            && active.prompt.device_path == device_path
            && let PairingKind::DisplayPasskey {
                passkey: existing_passkey,
                entered: existing_entered,
            } = &mut active.prompt.kind
            && *existing_passkey == passkey
        {
            *existing_entered = entered;
            return active.prompt.clone();
        }

        if let Some(prev) = self.active.take()
            && prev.reply_tx.is_some()
        {
            self.queue.push_front(prev);
        }
        let prompt = PairingPrompt {
            prompt_id: self.alloc_id(),
            device_path,
            device_label,
            address,
            kind: PairingKind::DisplayPasskey { passkey, entered },
        };
        self.active = Some(ActivePrompt {
            prompt: prompt.clone(),
            reply_tx: None,
        });
        prompt
    }

    /// Resolves the active prompt if `prompt_id` matches; ignores stale IDs.
    /// On `AcceptService { always_trust: true }`, auto-accepts any queued
    /// `AuthorizeService` prompts targeting the same device.
    pub fn resolve(&mut self, prompt_id: u64, reply: AgentReply) -> Option<ResolveOutcome> {
        if self
            .active
            .as_ref()
            .is_none_or(|entry| entry.prompt.prompt_id != prompt_id)
        {
            return None;
        }
        let entry = self.active.take()?;
        let dismissed_id = entry.prompt.prompt_id;
        let cancel_pairing_device = (entry.prompt.kind.is_display_only()
            && reply == AgentReply::Canceled)
            .then(|| entry.prompt.device_path.clone());
        let always_trust_device = matches!(
            reply,
            AgentReply::AcceptService { always_trust: true }
        )
        .then(|| entry.prompt.device_path.clone());

        if let Some(tx) = entry.reply_tx {
            let _ = tx.send(reply);
        }

        if let Some(dev_path) = always_trust_device {
            let mut remaining = VecDeque::with_capacity(self.queue.len());
            while let Some(queued) = self.queue.pop_front() {
                let same_service = queued.prompt.device_path == dev_path
                    && matches!(queued.prompt.kind, PairingKind::AuthorizeService { .. });
                if same_service {
                    if let Some(tx) = queued.reply_tx {
                        let _ = tx.send(AgentReply::AcceptService {
                            always_trust: false,
                        });
                    }
                } else {
                    remaining.push_back(queued);
                }
            }
            self.queue = remaining;
        }

        let promoted = self.promote_next();
        Some(ResolveOutcome {
            dismissed_id,
            promoted,
            cancel_pairing_device,
        })
    }

    /// Cancels only the active prompt (`Agent1.Cancel`) and promotes the next
    /// queued prompt if one is waiting.
    pub fn cancel(&mut self) -> Option<(u64, Option<PairingPrompt>)> {
        let entry = self.active.take()?;
        let id = entry.prompt.prompt_id;
        if let Some(tx) = entry.reply_tx {
            let _ = tx.send(AgentReply::Canceled);
        }
        let promoted = self.promote_next();
        Some((id, promoted))
    }

    /// Cancels both the active prompt and every queued prompt (used on
    /// `Agent1.Release`, BlueZ disconnect, and shutdown teardown).
    pub fn cancel_all(&mut self) -> Option<u64> {
        let active_id = self.active.take().map(|entry| {
            let id = entry.prompt.prompt_id;
            if let Some(tx) = entry.reply_tx {
                let _ = tx.send(AgentReply::Canceled);
            }
            id
        });
        while let Some(entry) = self.queue.pop_front() {
            if let Some(tx) = entry.reply_tx {
                let _ = tx.send(AgentReply::Canceled);
            }
        }
        active_id
    }

    /// Handles `Agent1.Release` by canceling all active and queued prompts.
    pub fn release(&mut self) -> Option<u64> {
        self.cancel_all()
    }

    /// Workaround for BlueZ omitting `Agent1.Cancel()` after `DisplayPasskey`
    /// or `DisplayPinCode`: auto-dismiss display-only prompts when the device
    /// finishes pairing (`Paired = true`) or drops (`Connected = false`).
    pub fn on_device_state_changed(
        &mut self,
        device_path: &str,
        paired: Option<bool>,
        connected: Option<bool>,
    ) -> Option<(u64, Option<PairingPrompt>)> {
        let should_dismiss = self.active.as_ref().is_some_and(|entry| {
            entry.prompt.device_path == device_path
                && entry.prompt.kind.is_display_only()
                && (paired == Some(true) || connected == Some(false))
        });
        if should_dismiss {
            let id = self.active.take()?.prompt.prompt_id;
            let promoted = self.promote_next();
            Some((id, promoted))
        } else {
            None
        }
    }

    /// Dismisses any active or queued prompts targeting a device removed from BlueZ.
    pub fn on_device_removed(&mut self, device_path: &str) -> Option<(u64, Option<PairingPrompt>)> {
        let mut remaining = VecDeque::with_capacity(self.queue.len());
        while let Some(queued) = self.queue.pop_front() {
            if queued.prompt.device_path == device_path {
                if let Some(tx) = queued.reply_tx {
                    let _ = tx.send(AgentReply::Canceled);
                }
            } else {
                remaining.push_back(queued);
            }
        }
        self.queue = remaining;

        if self
            .active
            .as_ref()
            .is_some_and(|entry| entry.prompt.device_path == device_path)
        {
            self.cancel()
        } else {
            None
        }
    }
}

/// Extract a 16-bit Bluetooth SIG short UUID from a full 128-bit or 4-hex-digit string.
pub fn short_uuid(uuid: &str) -> Option<u16> {
    let trimmed = uuid.trim();
    if trimmed.len() == 4 && trimmed.bytes().all(|b| b.is_ascii_hexdigit()) {
        return u16::from_str_radix(trimmed, 16).ok();
    }
    if trimmed.len() == 36 {
        let lower = trimmed.to_ascii_lowercase();
        if lower.starts_with("0000") && lower.ends_with("-0000-1000-8000-00805f9b34fb") {
            return u16::from_str_radix(&lower[4..8], 16).ok();
        }
    }
    None
}

pub fn normalize_mac(raw: &str) -> String {
    raw.trim().replace('-', ":").to_ascii_uppercase()
}

pub fn format_hex_bytes(bytes: &[u8]) -> String {
    bytes
        .iter()
        .map(|b| format!("{b:02X}"))
        .collect::<Vec<_>>()
        .join(" ")
}

pub fn service_uuid_name(uuid: &str) -> String {
    let trimmed = uuid.trim();
    if let Some(short) = short_uuid(trimmed) {
        let name = match short {
            0x1000 => "Service Discovery Server",
            0x1001 => "Browse Group Descriptor",
            0x1101 => "Serial Port (SPP)",
            0x1102 => "LAN Access Using PPP",
            0x1103 => "Dialup Networking (DUN)",
            0x1104 => "IrMC Sync",
            0x1105 => "OBEX Object Push",
            0x1106 => "OBEX File Transfer",
            0x1108 => "Headset (HSP)",
            0x110A => "Audio Source (A2DP)",
            0x110B => "Audio Sink (A2DP)",
            0x110C => "A/V Remote Control Target",
            0x110D => "Advanced Audio Distribution",
            0x110E => "A/V Remote Control",
            0x110F => "A/V Remote Control Controller",
            0x1112 => "Headset Audio Gateway",
            0x1115 => "Personal Area Network (PANU)",
            0x1116 => "Network Access Point (NAP)",
            0x1117 => "Group Ad-hoc Network (GN)",
            0x111E => "Handsfree (HFP)",
            0x111F => "Handsfree Audio Gateway",
            0x1124 => "Human Interface Device (HID)",
            0x112D => "SIM Access (SAP)",
            0x112E => "Phonebook Access Client",
            0x112F => "Phonebook Access Server",
            0x1130 => "Phonebook Access",
            0x1131 => "Headset HS",
            0x1132 => "Message Access Server",
            0x1133 => "Message Notification Server",
            0x1134 => "Message Access Profile",
            0x1200 => "PnP Information (DIP)",
            0x1203 => "Generic Audio",
            0x1400 => "Health Device Profile",
            0x1800 => "Generic Access (GAP)",
            0x1801 => "Generic Attribute (GATT)",
            0x1802 => "Immediate Alert",
            0x1803 => "Link Loss",
            0x1804 => "Tx Power",
            0x1805 => "Current Time",
            0x180A => "Device Information",
            0x180D => "Heart Rate",
            0x180F => "Battery Service",
            0x1812 => "HID over GATT (HOGP)",
            0x1813 => "Scan Parameters",
            0x1816 => "Cycling Speed and Cadence",
            0x1818 => "Cycling Power",
            0x1819 => "Location and Navigation",
            0x181A => "Environmental Sensing",
            0x181C => "User Data",
            0x1843 => "Audio Input Control",
            0x1844 => "Volume Control (VCS)",
            0x1845 => "Volume Offset Control",
            0x1846 => "Coordinated Set Identification (CSIS)",
            0x1848 => "Media Control",
            0x1849 => "Generic Media Control",
            0x184D => "Microphone Control",
            0x184E => "Audio Stream Control (ASCS)",
            0x184F => "Broadcast Audio Scan (BASS)",
            0x1850 => "Published Audio Capabilities (PACS)",
            0x1851 => "Basic Audio Announcement",
            0x1852 => "Broadcast Audio Announcement",
            0x1853 => "Common Audio (CAS)",
            0x1854 => "Hearing Access (HAS)",
            0x1855 => "Telephony and Media Audio (TMAS)",
            0xFE2C => "Google Fast Pair",
            0xFE95 => "Xiaomi Inc.",
            0xFEAA => "Eddystone",
            _ => return format!("Bluetooth Service (0x{short:04x})"),
        };
        return name.to_string();
    }

    match trimmed.to_ascii_lowercase().as_str() {
        "03b80e5a-ede8-4b33-a751-6ce34ec4c700" => "Bluetooth LE MIDI".to_string(),
        "7905f431-b5ce-4e99-a40f-4b1e122d00d0" => "Apple Notification Center (ANCS)".to_string(),
        "89d3502b-0f36-433a-8ef4-c502ad55f8dc" => "Apple Media Service (AMS)".to_string(),
        "6e400001-b5a3-f393-e0a9-e50e24dcca9e" => "Nordic UART Service".to_string(),
        _ => trimmed.to_string(),
    }
}

/// Decode a 24-bit Bluetooth Class of Device (CoD) into human-readable major, minor, and service classes.
pub fn decode_class(class: u32) -> Option<String> {
    if class == 0 {
        return None;
    }
    let major_bits = (class >> 8) & 0x1F;
    let minor_bits = (class >> 2) & 0x3F;
    let service_bits = (class >> 13) & 0x7FF;

    let major = match major_bits {
        0 => "Miscellaneous",
        1 => "Computer",
        2 => "Phone",
        3 => "LAN / Network",
        4 => "Audio/Video",
        5 => "Peripheral",
        6 => "Imaging",
        7 => "Wearable",
        8 => "Toy",
        9 => "Health",
        31 => "Uncategorized",
        _ => "Unknown",
    };

    let minor = match major_bits {
        1 => match minor_bits {
            1 => Some("Desktop"),
            2 => Some("Server"),
            3 => Some("Laptop"),
            4 => Some("Handheld"),
            5 => Some("Palm-size"),
            6 => Some("Wearable"),
            7 => Some("Tablet"),
            _ => None,
        },
        2 => match minor_bits {
            1 => Some("Cellular"),
            2 => Some("Cordless"),
            3 => Some("Smartphone"),
            4 => Some("Wired Modem / Gateway"),
            5 => Some("ISDN"),
            _ => None,
        },
        4 => match minor_bits {
            1 => Some("Wearable Headset"),
            2 => Some("Hands-free"),
            4 => Some("Microphone"),
            5 => Some("Loudspeaker"),
            6 => Some("Headphones"),
            7 => Some("Portable Audio"),
            8 => Some("Car Audio"),
            9 => Some("Set-top Box"),
            10 => Some("HiFi Audio"),
            11 => Some("VCR"),
            12 => Some("Video Camera"),
            13 => Some("Camcorder"),
            14 => Some("Video Monitor"),
            15 => Some("Video Display & Loudspeaker"),
            16 => Some("Video Conferencing"),
            18 => Some("Gaming / Toy"),
            _ => None,
        },
        5 => {
            let sub = minor_bits & 0x0F;
            let hi = (minor_bits >> 4) & 0x03;
            match (hi, sub) {
                (0, 1) => Some("Joystick"),
                (0, 2) => Some("Gamepad"),
                (0, 3) => Some("Remote Control"),
                (0, 4) => Some("Sensing Device"),
                (0, 5) => Some("Digitizer Tablet"),
                (0, 6) => Some("Card Reader"),
                (0, 7) => Some("Digital Pen"),
                (1, _) => Some("Keyboard"),
                (2, _) => Some("Pointing Device"),
                (3, _) => Some("Combo Keyboard/Pointing"),
                _ => None,
            }
        }
        6 => {
            let mut parts = Vec::new();
            if (minor_bits & 0x04) != 0 {
                parts.push("Display");
            }
            if (minor_bits & 0x08) != 0 {
                parts.push("Camera");
            }
            if (minor_bits & 0x10) != 0 {
                parts.push("Scanner");
            }
            if (minor_bits & 0x20) != 0 {
                parts.push("Printer");
            }
            if parts.is_empty() {
                None
            } else {
                // Handled below via owned format if multiple; keep simple single label for 1 match
                match parts.len() {
                    1 => Some(parts[0]),
                    _ => Some("Multi-function"),
                }
            }
        }
        7 => match minor_bits {
            1 => Some("Wristwatch"),
            2 => Some("Pager"),
            3 => Some("Jacket"),
            4 => Some("Helmet"),
            5 => Some("Glasses"),
            _ => None,
        },
        9 => match minor_bits {
            1 => Some("Blood Pressure"),
            2 => Some("Thermometer"),
            3 => Some("Weighing Scale"),
            4 => Some("Glucose Meter"),
            5 => Some("Pulse Oximeter"),
            6 => Some("Heart Rate Monitor"),
            _ => None,
        },
        _ => None,
    };

    let mut services = Vec::new();
    if (service_bits & 0x001) != 0 {
        services.push("Limited Discoverable");
    }
    if (service_bits & 0x002) != 0 {
        services.push("LE Audio");
    }
    if (service_bits & 0x008) != 0 {
        services.push("Positioning");
    }
    if (service_bits & 0x010) != 0 {
        services.push("Networking");
    }
    if (service_bits & 0x020) != 0 {
        services.push("Rendering");
    }
    if (service_bits & 0x040) != 0 {
        services.push("Capturing");
    }
    if (service_bits & 0x080) != 0 {
        services.push("Object Transfer");
    }
    if (service_bits & 0x100) != 0 {
        services.push("Audio");
    }
    if (service_bits & 0x200) != 0 {
        services.push("Telephony");
    }
    if (service_bits & 0x400) != 0 {
        services.push("Information");
    }

    let base = match minor {
        Some(m) => format!("{major} — {m}"),
        None => major.to_string(),
    };
    if services.is_empty() {
        Some(base)
    } else {
        Some(format!("{base} ({})", services.join(", ")))
    }
}

/// Decode a 16-bit GATT Appearance value into a human-readable Bluetooth SIG description.
pub fn decode_appearance(appearance: u16) -> Option<String> {
    if appearance == 0 {
        return None;
    }
    let exact = match appearance {
        0x0040 => Some("Phone"),
        0x0080 => Some("Computer"),
        0x0081 => Some("Workstation"),
        0x0082 => Some("Laptop"),
        0x0083 => Some("Desktop"),
        0x0084 => Some("Server"),
        0x0085 => Some("Tablet"),
        0x00C0 => Some("Watch"),
        0x00C1 => Some("Sports Watch"),
        0x00C2 => Some("Smartwatch"),
        0x0100 => Some("Clock"),
        0x0140 => Some("Display"),
        0x0180 => Some("Remote Control"),
        0x01C0 => Some("Eye-glasses"),
        0x0200 => Some("Tag"),
        0x0240 => Some("Keyring"),
        0x0280 => Some("Media Player"),
        0x0340 => Some("Heart Rate Sensor"),
        0x03C0 => Some("Human Interface Device"),
        0x03C1 => Some("Keyboard"),
        0x03C2 => Some("Mouse"),
        0x03C3 => Some("Joystick"),
        0x03C4 => Some("Gamepad"),
        0x03C5 => Some("Digitizer Tablet"),
        0x03C6 => Some("Card Reader"),
        0x03C7 => Some("Digital Pen"),
        0x03C8 => Some("Barcode Scanner"),
        0x03C9 => Some("Touchpad"),
        0x0840 => Some("Audio Sink"),
        0x0841 => Some("Standalone Speaker"),
        0x0842 => Some("Soundbar"),
        0x0880 => Some("Audio Source"),
        0x0881 => Some("Microphone"),
        0x0940 => Some("Wearable Audio Device"),
        0x0941 => Some("Earbud"),
        0x0942 => Some("Headset"),
        0x0943 => Some("Headphones"),
        0x0944 => Some("Neck Band"),
        0x0A40 => Some("Hearing Aid"),
        _ => None,
    };
    if let Some(name) = exact {
        return Some(name.to_string());
    }

    let category = match appearance >> 6 {
        0x001 => "Phone",
        0x002 => "Computer",
        0x003 => "Watch",
        0x004 => "Clock",
        0x005 => "Display",
        0x006 => "Remote Control",
        0x00D => "Heart Rate Sensor",
        0x00F => "Human Interface Device",
        0x014 => "Running / Walking Sensor",
        0x015 => "Cycling",
        0x021 => "Audio Sink",
        0x022 => "Audio Source",
        0x025 => "Wearable Audio Device",
        0x029 => "Hearing Aid",
        _ => "Unknown Appearance",
    };
    Some(category.to_string())
}

/// Decode Bluetooth Core Spec Advertising Flags byte(s) into readable names.
pub fn decode_advertising_flags(flags: &[u8]) -> String {
    if flags.is_empty() {
        return String::new();
    }
    let first = flags[0];
    let mut parts = Vec::new();
    if (first & 0x01) != 0 {
        parts.push("LE Limited Discoverable");
    }
    if (first & 0x02) != 0 {
        parts.push("LE General Discoverable");
    }
    if (first & 0x04) != 0 {
        parts.push("BR/EDR Not Supported");
    }
    if (first & 0x08) != 0 {
        parts.push("Simultaneous LE & BR/EDR (Controller)");
    }
    if (first & 0x10) != 0 {
        parts.push("Simultaneous LE & BR/EDR (Host)");
    }
    let hex = format_hex_bytes(flags);
    if parts.is_empty() {
        format!("0x{hex}")
    } else {
        format!("{} (0x{hex})", parts.join(", "))
    }
}

pub fn format_bytes_full(bytes: u64) -> String {
    const KB: f64 = 1024.0;
    const MB: f64 = KB * 1024.0;
    const GB: f64 = MB * 1024.0;
    const TB: f64 = GB * 1024.0;

    let b = bytes as f64;
    if b >= TB {
        format!("{:.2} TB", b / TB)
    } else if b >= GB {
        format!("{:.2} GB", b / GB)
    } else if b >= MB {
        format!("{:.2} MB", b / MB)
    } else if b >= KB {
        format!("{:.2} KB", b / KB)
    } else {
        format!("{bytes} B")
    }
}

pub fn format_rate_full(bps: f64) -> String {
    const KB: f64 = 1024.0;
    const MB: f64 = KB * 1024.0;
    const GB: f64 = MB * 1024.0;

    let v = if bps.is_finite() && bps > 0.0 {
        bps
    } else {
        0.0
    };
    if v >= GB {
        format!("{:.2} GB/s", v / GB)
    } else if v >= MB {
        format!("{:.2} MB/s", v / MB)
    } else if v >= KB {
        format!("{:.2} KB/s", v / KB)
    } else {
        format!("{:.0} B/s", v)
    }
}

/// Ultra-compact single-letter byte formatter (`0B`, `512B`, `8.5K`, `122M`, `1.4G`)
/// bounded to <= 5 characters so `↑122M ↓8.5M` stays <= 11 chars at 360px width.
pub fn format_bytes_compact(bytes: u64) -> String {
    const KB: f64 = 1024.0;
    const MB: f64 = KB * 1024.0;
    const GB: f64 = MB * 1024.0;
    const TB: f64 = GB * 1024.0;

    let fmt_unit = |val: f64, suffix: char| {
        if val >= 9.95 {
            format!("{:.0}{suffix}", val)
        } else {
            format!("{:.1}{suffix}", val)
        }
    };

    let b = bytes as f64;
    if b >= TB {
        fmt_unit(b / TB, 'T')
    } else if b >= GB {
        fmt_unit(b / GB, 'G')
    } else if b >= MB {
        fmt_unit(b / MB, 'M')
    } else if b >= KB {
        fmt_unit(b / KB, 'K')
    } else {
        format!("{bytes}B")
    }
}

pub fn format_rate_compact(bps: f64) -> String {
    const KB: f64 = 1024.0;
    const MB: f64 = KB * 1024.0;
    const GB: f64 = MB * 1024.0;

    let v = if bps.is_finite() && bps > 0.0 {
        bps
    } else {
        0.0
    };
    let fmt_unit = |val: f64, suffix: char| {
        if val >= 9.95 {
            format!("{:.0}{suffix}/s", val)
        } else {
            format!("{:.1}{suffix}/s", val)
        }
    };
    if v >= GB {
        fmt_unit(v / GB, 'G')
    } else if v >= MB {
        fmt_unit(v / MB, 'M')
    } else if v >= KB {
        fmt_unit(v / KB, 'K')
    } else {
        format!("{:.0}B/s", v)
    }
}

/// Classify a BlueZ device by priority: `Icon` hint -> `Class` -> GATT `Appearance` -> `UUIDs` -> `Generic`.
pub fn classify_device(
    icon_hint: Option<&str>,
    class: u32,
    appearance: u16,
    uuids: &[String],
) -> DeviceKind {
    if let Some(icon) = icon_hint.map(str::trim).filter(|s| !s.is_empty()) {
        let lower = icon.to_ascii_lowercase();
        if let Some(kind) = match lower.as_str() {
            "audio-headphones" | "audio-headset" => Some(DeviceKind::Headphones),
            "audio-card" | "audio-speakers" | "multimedia-player" => Some(DeviceKind::Speaker),
            "input-keyboard" => Some(DeviceKind::Keyboard),
            "input-mouse" | "input-tablet" => Some(DeviceKind::Mouse),
            "phone" | "modem" => Some(DeviceKind::Phone),
            "input-gaming" => Some(DeviceKind::Controller),
            "computer" | "network-wireless" => Some(DeviceKind::Computer),
            _ => None,
        } {
            return kind;
        }
    }

    if class != 0 {
        let major = (class >> 8) & 0x1F;
        let minor = (class >> 2) & 0x3F;
        match major {
            1 => return DeviceKind::Computer,
            2 => return DeviceKind::Phone,
            4 => {
                // Audio/Video major class.
                return match minor {
                    1 | 2 | 6 => DeviceKind::Headphones,
                    4 | 5 | 8 | 9 | 10 => DeviceKind::Speaker,
                    _ => DeviceKind::Speaker,
                };
            }
            5 => {
                // Peripheral major class.
                let sub_low = minor & 0x0F;
                if matches!(sub_low, 1 | 2) {
                    return DeviceKind::Controller;
                }
                let hi = (minor >> 4) & 0x03;
                match hi {
                    1 | 3 => return DeviceKind::Keyboard,
                    2 => return DeviceKind::Mouse,
                    _ => {}
                }
            }
            _ => {}
        }
    }

    if appearance != 0 {
        match appearance {
            0x03C1 => return DeviceKind::Keyboard,
            0x03C2 => return DeviceKind::Mouse,
            0x03C3 | 0x03C4 => return DeviceKind::Controller,
            0x0941 | 0x0942 => return DeviceKind::Headphones,
            _ => {
                let category = appearance >> 6;
                match category {
                    0x001 => return DeviceKind::Phone,
                    0x002 => return DeviceKind::Computer,
                    0x021 => return DeviceKind::Speaker,
                    0x025 => return DeviceKind::Headphones,
                    _ => {}
                }
            }
        }
    }

    for uuid in uuids {
        match short_uuid(uuid) {
            Some(0x1108 | 0x111E | 0x111F | 0x110B) => return DeviceKind::Headphones,
            _ => {}
        }
    }

    DeviceKind::Generic
}

pub fn is_keyboard_or_combo(class: u32, appearance: u16, kind: DeviceKind) -> bool {
    if kind == DeviceKind::Keyboard || appearance == 0x03C1 {
        return true;
    }
    if class != 0 {
        let major = (class >> 8) & 0x1F;
        let minor = (class >> 2) & 0x3F;
        if major == 5 && (minor & 0x10) != 0 {
            return true;
        }
    }
    false
}

fn matches_mac(candidate: &str, address: &str) -> bool {
    let addr = address.trim();
    if addr.is_empty() {
        return false;
    }
    let normalized = candidate.trim().replace('-', ":");
    normalized.eq_ignore_ascii_case(addr)
}

/// Detect devices that only expose a MAC address or blank alias/name.
pub fn is_unnamed_device(alias: &str, name: Option<&str>, address: &str) -> bool {
    let alias_t = alias.trim();
    if !alias_t.is_empty() && !matches_mac(alias_t, address) {
        return false;
    }
    if let Some(name_t) = name.map(str::trim)
        && !name_t.is_empty()
        && !matches_mac(name_t, address)
    {
        return false;
    }
    true
}

/// Bluetooth Core Spec valid RSSI range is -127..=20 dBm; reject sentinel
/// values such as 127 (0x7F unavailable), 153 (0x99 legacy invalid), and -128.
pub fn valid_rssi(rssi: i16) -> Option<i16> {
    (-127..=20).contains(&rssi).then_some(rssi)
}

/// Map a real BlueZ RSSI reading (in dBm) to a 1..=4 signal bar tier.
pub fn rssi_signal_tier(rssi: i16) -> u8 {
    if rssi >= -55 {
        4
    } else if rssi >= -70 {
        3
    } else if rssi >= -82 {
        2
    } else {
        1
    }
}

pub fn rssi_icon_name(rssi: i16) -> &'static str {
    match rssi_signal_tier(rssi) {
        4 => "signal_4",
        3 => "signal_3",
        2 => "signal_2",
        _ => "signal_1",
    }
}

pub fn rssi_quality_label(rssi: i16) -> &'static str {
    match rssi_signal_tier(rssi) {
        4 => "Excellent",
        3 => "Good",
        2 => "Fair",
        _ => "Weak",
    }
}

/// Detect user-canceled BlueZ/D-Bus operation errors so the UI avoids red error banners.
pub fn is_canceled_bluez_error(raw: &str) -> bool {
    let lower = raw.to_ascii_lowercase();
    lower.contains("br-connection-canceled")
        || lower.contains("authenticationcanceled")
        || lower.contains("org.bluez.error.canceled")
        || lower.contains("cancelled")
        || lower.contains("canceled")
}

/// Translate BlueZ 5.62+ error tokens, RFKill failures, and legacy errno strings
/// into concise user-facing text.
pub fn translate_bluez_error(raw: &str) -> String {
    let lower = raw.to_ascii_lowercase();
    if lower.contains("rf-kill") || lower.contains("rfkill") {
        return "Bluetooth is blocked by RF-kill (hardware or software switch)".to_string();
    }
    if lower.contains("br-connection-profile-unavailable")
        || lower.contains("protocol not available")
    {
        return "No matching audio or profile endpoint registered (check PipeWire/WirePlumber)"
            .to_string();
    }
    if lower.contains("br-connection-key-missing") {
        return "Pairing key mismatch (remove device and pair again)".to_string();
    }
    if lower.contains("br-connection-adapter-not-powered") {
        return "Bluetooth adapter is turned off".to_string();
    }
    if lower.contains("br-connection-page-timeout") || lower.contains("host is down") {
        return "Device did not respond (out of range or powered off)".to_string();
    }
    if lower.contains("le-connection-abort-by-local") {
        return "Bluetooth LE connection was aborted locally".to_string();
    }
    if lower.contains("br-connection-create-socket")
        || lower.contains("le-connection-create-socket")
        || lower.contains("input/output error")
        || lower.contains("i/o error")
    {
        return "Bluetooth connection I/O error".to_string();
    }
    if is_canceled_bluez_error(&lower) {
        return "Operation canceled".to_string();
    }
    if lower.contains("authenticationrejected") || lower.contains("org.bluez.error.rejected") {
        return "Pairing rejected".to_string();
    }
    if lower.contains("authenticationfailed") {
        return "Authentication failed (check PIN or passkey)".to_string();
    }
    if lower.contains("authenticationtimeout") {
        return "Pairing timed out".to_string();
    }
    if lower.contains("br-connection-refused") {
        return "Connection refused by remote device".to_string();
    }
    if lower.contains("br-connection-busy") || lower.contains("inprogress") {
        return "Operation already in progress".to_string();
    }
    if lower.contains("br-connection-already-connected") || lower.contains("alreadyconnected") {
        return "Device is already connected".to_string();
    }
    if lower.contains("br-connection-not-supported")
        || lower.contains("org.bluez.error.notsupported")
    {
        return "Operation not supported by this device".to_string();
    }
    if lower.contains("org.bluez.error.notavailable") {
        return "Requested Bluetooth service is not available".to_string();
    }
    if lower.contains("org.bluez.error.doesnotexist") {
        return "Device or adapter no longer exists".to_string();
    }
    if lower.contains("org.freedesktop.dbus.error.accessdenied") {
        return "Permission denied by D-Bus Bluetooth policy".to_string();
    }
    if lower.contains("notready") || lower.contains("resource not ready") {
        return "Bluetooth adapter is not ready".to_string();
    }
    if lower.contains("no discovery started") {
        return "Discovery was started by another application".to_string();
    }
    if lower.contains("br-connection-unknown") {
        return "Connection failed with an unknown Bluetooth error".to_string();
    }

    let cleaned = raw
        .rsplit_once(": ")
        .map(|(_, tail)| tail)
        .unwrap_or(raw)
        .trim();
    if cleaned.is_empty() {
        "Bluetooth operation failed".to_string()
    } else {
        cleaned.to_string()
    }
}

/// Deterministic device ordering: connected first, then paired, then nearby
/// sorted by strongest RSSI, then case-insensitive display name, then MAC address.
pub fn sort_devices(devices: &mut [&DeviceState]) {
    fn sort_key(d: &DeviceState) -> (bool, bool, Reverse<i16>, String, &str) {
        let rssi_rank = if d.paired || d.connected {
            Reverse(0_i16)
        } else {
            Reverse(d.rssi.and_then(valid_rssi).unwrap_or(i16::MIN))
        };
        (
            !d.connected,
            !d.paired,
            rssi_rank,
            d.display_name().to_ascii_lowercase(),
            d.address.as_str(),
        )
    }

    devices.sort_by(|a, b| sort_key(a).cmp(&sort_key(b)));
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_device(path: &str, addr: &str, alias: &str) -> DeviceState {
        let mut dev = DeviceState {
            path: path.to_string(),
            adapter_path: "/org/bluez/hci0".to_string(),
            address: addr.to_string(),
            alias: alias.to_string(),
            ..Default::default()
        };
        dev.refresh_kind();
        dev
    }

    #[test]
    fn decoders_cover_class_appearance_uuids_adv_flags_visibility_and_bytes() {
        assert_eq!(decode_class(0), None);
        // Audio/Video (4) Headphones (6) + Audio(0x100) + Rendering(0x020)
        let hp_class = (0x120 << 13) | (4 << 8) | (6 << 2);
        let decoded = decode_class(hp_class).unwrap();
        assert!(decoded.contains("Audio/Video — Headphones"));
        assert!(decoded.contains("Rendering"));
        assert!(decoded.contains("Audio"));

        let kbd_class = (5 << 8) | (0x10 << 2);
        assert_eq!(
            decode_class(kbd_class).as_deref(),
            Some("Peripheral — Keyboard")
        );

        assert_eq!(decode_appearance(0), None);
        assert_eq!(decode_appearance(0x03C1).as_deref(), Some("Keyboard"));
        assert_eq!(decode_appearance(0x0941).as_deref(), Some("Earbud"));
        assert_eq!(
            decode_appearance(0x0949).as_deref(),
            Some("Wearable Audio Device")
        );

        assert_eq!(
            service_uuid_name("0000184e-0000-1000-8000-00805f9b34fb"),
            "Audio Stream Control (ASCS)"
        );
        assert_eq!(
            service_uuid_name("03b80e5a-ede8-4b33-a751-6ce34ec4c700"),
            "Bluetooth LE MIDI"
        );
        assert_eq!(
            service_uuid_name("0000fe2c-0000-1000-8000-00805f9b34fb"),
            "Google Fast Pair"
        );

        let flags = decode_advertising_flags(&[0x06]);
        assert!(flags.contains("LE General Discoverable"));
        assert!(flags.contains("BR/EDR Not Supported"));

        assert_eq!(
            VisibilityMode::from_adapter_props(false, 180),
            VisibilityMode::Hidden
        );
        assert_eq!(
            VisibilityMode::from_adapter_props(true, 0),
            VisibilityMode::AlwaysVisible
        );
        assert_eq!(
            VisibilityMode::from_adapter_props(true, 120),
            VisibilityMode::Temporary { timeout_secs: 120 }
        );
        assert_eq!(
            VisibilityMode::Temporary { timeout_secs: 5 }.to_bluez_props(),
            (true, Some(30))
        );
        assert_eq!(VisibilityMode::AlwaysVisible.to_bluez_props(), (true, Some(0)));
        assert_eq!(VisibilityMode::Hidden.to_bluez_props(), (false, None));

        assert_eq!(format_bytes_compact(0), "0B");
        assert_eq!(format_bytes_compact(8_960_000), "8.5M");
        assert_eq!(format_bytes_compact(128_000_000), "122M");
        assert_eq!(format_bytes_full(128_492_000), "122.54 MB");
        assert_eq!(format_rate_compact(2900.0), "2.8K/s");
        assert_eq!(format_rate_full(2900.0), "2.83 KB/s");
    }

    #[test]
    fn device_classification_respects_icon_class_appearance_uuid_priority() {
        assert_eq!(
            classify_device(Some("audio-headphones"), 0, 0, &[]),
            DeviceKind::Headphones
        );
        assert_eq!(
            classify_device(Some("audio-speakers"), 0, 0, &[]),
            DeviceKind::Speaker
        );
        assert_eq!(
            classify_device(Some("input-gaming"), 0, 0, &[]),
            DeviceKind::Controller
        );

        // Class: Peripheral (major=5) Keyboard (minor=0x10 -> class = (5<<8)|(0x10<<2))
        let kbd_class = (5 << 8) | (0x10 << 2);
        assert_eq!(
            classify_device(None, kbd_class, 0, &[]),
            DeviceKind::Keyboard
        );

        // Class: Peripheral (major=5) Mouse (minor=0x20)
        let mouse_class = (5 << 8) | (0x20 << 2);
        assert_eq!(
            classify_device(None, mouse_class, 0, &[]),
            DeviceKind::Mouse
        );

        // Class: Peripheral (major=5) Gamepad (minor=0x02)
        let pad_class = (5 << 8) | (0x02 << 2);
        assert_eq!(
            classify_device(None, pad_class, 0, &[]),
            DeviceKind::Controller
        );

        // GATT Appearance fallback for BLE devices with Class == 0
        assert_eq!(classify_device(None, 0, 0x03C2, &[]), DeviceKind::Mouse);
        assert_eq!(classify_device(None, 0, 0x03C1, &[]), DeviceKind::Keyboard);
        assert_eq!(classify_device(None, 0, 0x0040, &[]), DeviceKind::Phone);

        // UUID fallback for A2DP sink
        let uuids = vec!["0000110b-0000-1000-8000-00805f9b34fb".to_string()];
        assert_eq!(classify_device(None, 0, 0, &uuids), DeviceKind::Headphones);

        assert_eq!(classify_device(None, 0, 0, &[]), DeviceKind::Generic);
    }

    #[test]
    fn unnamed_detection_and_keyboard_preservation() {
        let mut unnamed = sample_device(
            "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
            "AA:BB:CC:DD:EE:FF",
            "AA-BB-CC-DD-EE-FF",
        );
        assert!(unnamed.is_unnamed());
        assert_eq!(unnamed.display_name(), "Unnamed device");
        assert!(unnamed.should_hide_when_unnamed());

        // Unnamed keyboard must NOT be hidden when "Hide unnamed" is active.
        unnamed.appearance = 0x03C1;
        unnamed.refresh_kind();
        assert!(unnamed.is_unnamed());
        assert!(!unnamed.should_hide_when_unnamed());

        // Blank alias with real name is named.
        let mut named_via_name = sample_device(
            "/org/bluez/hci0/dev_11_22_33_44_55_66",
            "11:22:33:44:55:66",
            "   ",
        );
        named_via_name.name = Some("WH-1000XM5".to_string());
        assert!(!named_via_name.is_unnamed());
        assert_eq!(named_via_name.display_name(), "WH-1000XM5");
    }

    #[test]
    fn rssi_bounds_tiers_and_error_translation_work() {
        assert_eq!(valid_rssi(127), None);
        assert_eq!(valid_rssi(153), None);
        assert_eq!(valid_rssi(-128), None);
        assert_eq!(valid_rssi(21), None);
        assert_eq!(valid_rssi(20), Some(20));
        assert_eq!(valid_rssi(0), Some(0));
        assert_eq!(valid_rssi(-103), Some(-103));
        assert_eq!(valid_rssi(-127), Some(-127));

        assert_eq!(rssi_signal_tier(-45), 4);
        assert_eq!(rssi_signal_tier(-65), 3);
        assert_eq!(rssi_signal_tier(-78), 2);
        assert_eq!(rssi_signal_tier(-95), 1);

        assert!(is_canceled_bluez_error(
            "org.bluez.Error.AuthenticationCanceled"
        ));
        assert!(is_canceled_bluez_error(
            "org.bluez.Error.Failed: br-connection-canceled"
        ));
        assert!(!is_canceled_bluez_error(
            "org.bluez.Error.AuthenticationFailed"
        ));

        assert!(
            translate_bluez_error("org.bluez.Error.Failed: Operation not possible due to RF-kill")
                .contains("RF-kill")
        );
        assert!(
            translate_bluez_error("org.bluez.Error.Failed: br-connection-profile-unavailable")
                .contains("PipeWire")
        );
        assert!(
            translate_bluez_error("org.bluez.Error.Failed: br-connection-key-missing")
                .contains("key mismatch")
        );
        assert!(
            translate_bluez_error("org.bluez.Error.Failed: br-connection-adapter-not-powered")
                .contains("turned off")
        );
        assert!(
            translate_bluez_error("org.bluez.Error.Failed: le-connection-abort-by-local")
                .contains("aborted locally")
        );
        assert!(
            translate_bluez_error("org.bluez.Error.NotSupported").contains("not supported")
        );
        assert!(
            translate_bluez_error("org.bluez.Error.Failed: br-connection-not-supported")
                .contains("not supported")
        );
        assert!(
            translate_bluez_error("org.bluez.Error.NotAvailable").contains("not available")
        );
        assert!(
            translate_bluez_error("org.bluez.Error.DoesNotExist").contains("no longer exists")
        );
        assert!(
            translate_bluez_error("org.freedesktop.DBus.Error.AccessDenied")
                .contains("Permission denied")
        );
        assert!(
            translate_bluez_error("org.bluez.Error.Failed: br-connection-page-timeout")
                .contains("did not respond")
        );
        assert_eq!(
            translate_bluez_error("org.bluez.Error.AuthenticationFailed"),
            "Authentication failed (check PIN or passkey)"
        );
    }

    #[test]
    fn audio_profile_short_labels_cover_le_audio_opus_faststream_and_source() {
        let label = |name: &str| {
            AudioProfile {
                index: 1,
                name: name.to_string(),
                description: format!("Desc {name}"),
                priority: 10,
            }
            .short_label()
            .to_string()
        };
        assert_eq!(label("bap-sink"), "LE Audio (Sink)");
        assert_eq!(label("bap-duplex"), "LE Audio (Duplex)");
        assert_eq!(label("bap-source"), "LE Audio (Source)");
        assert_eq!(label("a2dp-sink-opus_05"), "A2DP (Opus)");
        assert_eq!(label("a2dp-sink-opus_05_duplex"), "A2DP (Opus Duplex)");
        assert_eq!(label("a2dp-sink-faststream"), "A2DP (FastStream)");
        assert_eq!(label("a2dp-sink-lc3plus_hr"), "A2DP (LC3plus HR)");
        assert_eq!(label("a2dp-source"), "A2DP Source");
        assert_eq!(label("a2dp-source-aac"), "A2DP Source (AAC)");
        assert_eq!(label("headset-head-unit-lc3_swb"), "Handsfree (LC3-SWB)");
        assert_eq!(label("off"), "Off");
    }

    #[test]
    fn deterministic_sorting_orders_connected_paired_and_rssi() {
        let mut connected = sample_device("/d1", "00:00:00:00:00:01", "Zeta");
        connected.connected = true;
        connected.paired = true;

        let mut paired_a = sample_device("/d2", "00:00:00:00:00:02", "Alpha");
        paired_a.paired = true;

        let mut paired_b = sample_device("/d3", "00:00:00:00:00:03", "Beta");
        paired_b.paired = true;

        let mut near_strong = sample_device("/d4", "00:00:00:00:00:04", "Nearby Weak Name");
        near_strong.rssi = Some(-52);

        let mut near_weak = sample_device("/d5", "00:00:00:00:00:05", "AAA Nearby");
        near_weak.rssi = Some(-85);

        let mut refs = [&near_weak, &paired_b, &near_strong, &connected, &paired_a];
        sort_devices(&mut refs);
        let order: Vec<&str> = refs.iter().map(|d| d.path.as_str()).collect();
        assert_eq!(order, vec!["/d1", "/d2", "/d3", "/d4", "/d5"]);
    }

    #[test]
    fn agent_state_machine_queues_fifo_drains_always_accept_and_cancels_cleanly() {
        let mut state = AgentState::new();

        // First interactive request becomes active immediately.
        let (p1, mut rx1) = state
            .begin_interactive(
                "/org/bluez/hci0/dev_1".into(),
                "Headphones".into(),
                "11:22:33:44:55:66".into(),
                PairingKind::AuthorizeService {
                    uuid: "0000110b-0000-1000-8000-00805f9b34fb".into(),
                    service_name: "Audio Sink (A2DP)".into(),
                },
            )
            .unwrap();
        let p1 = p1.unwrap();
        assert_eq!(p1.prompt_id, 1);

        // Concurrent AuthorizeService for same device is queued (returns None).
        let (p2, mut rx2) = state
            .begin_interactive(
                "/org/bluez/hci0/dev_1".into(),
                "Headphones".into(),
                "11:22:33:44:55:66".into(),
                PairingKind::AuthorizeService {
                    uuid: "0000111e-0000-1000-8000-00805f9b34fb".into(),
                    service_name: "Handsfree (HFP)".into(),
                },
            )
            .unwrap();
        assert!(p2.is_none());

        // Different device queued behind it.
        let (p3, mut rx3) = state
            .begin_interactive(
                "/org/bluez/hci0/dev_2".into(),
                "Mouse".into(),
                "AA:BB:CC:DD:EE:FF".into(),
                PairingKind::RequestPinCode,
            )
            .unwrap();
        assert!(p3.is_none());

        // Stale prompt ID is ignored.
        assert!(state.resolve(999, AgentReply::Confirm).is_none());

        // AlwaysAccept drains same-device AuthorizeService (with always_trust=false)
        // and promotes the next queued device prompt.
        let outcome = state
            .resolve(
                p1.prompt_id,
                AgentReply::AcceptService { always_trust: true },
            )
            .unwrap();
        assert_eq!(outcome.dismissed_id, p1.prompt_id);
        assert_eq!(outcome.cancel_pairing_device, None);
        assert_eq!(
            rx1.try_recv().unwrap(),
            Some(AgentReply::AcceptService { always_trust: true })
        );
        assert_eq!(
            rx2.try_recv().unwrap(),
            Some(AgentReply::AcceptService {
                always_trust: false
            })
        );
        let promoted = outcome.promoted.unwrap();
        assert_eq!(promoted.device_path, "/org/bluez/hci0/dev_2");

        // Enqueue one more behind dev_2; cancel() cancels ONLY active and promotes next.
        let (p4, mut rx4) = state
            .begin_interactive(
                "/org/bluez/hci0/dev_3".into(),
                "Speaker".into(),
                "33:44:55:66:77:88".into(),
                PairingKind::RequestPasskey,
            )
            .unwrap();
        assert!(p4.is_none());

        let (canceled_id, promoted_speaker) = state.cancel().unwrap();
        assert_eq!(canceled_id, promoted.prompt_id);
        assert_eq!(rx3.try_recv().unwrap(), Some(AgentReply::Canceled));
        let speaker_prompt = promoted_speaker.unwrap();
        assert_eq!(speaker_prompt.device_path, "/org/bluez/hci0/dev_3");

        // DisplayPasskey updates `entered` in place and preserves active interactive prompt.
        let dp1 = state.begin_display_passkey(
            "/org/bluez/hci0/dev_kbd".into(),
            "Keychron".into(),
            "22:33:44:55:66:77".into(),
            482910,
            0,
        );
        let dp2 = state.begin_display_passkey(
            "/org/bluez/hci0/dev_kbd".into(),
            "Keychron".into(),
            "22:33:44:55:66:77".into(),
            482910,
            3,
        );
        assert_eq!(dp1.prompt_id, dp2.prompt_id);
        assert_eq!(
            dp2.kind,
            PairingKind::DisplayPasskey {
                passkey: 482910,
                entered: 3
            }
        );

        // Dismissing a display-only prompt requests CancelPairing and restores queued prompt.
        let dismiss = state.resolve(dp1.prompt_id, AgentReply::Canceled).unwrap();
        assert_eq!(
            dismiss.cancel_pairing_device.as_deref(),
            Some("/org/bluez/hci0/dev_kbd")
        );
        assert_eq!(
            dismiss.promoted.as_ref().map(|p| p.prompt_id),
            Some(speaker_prompt.prompt_id)
        );

        // Paired=true auto-dismisses display-only prompt (BlueZ missing-Cancel workaround).
        let dp3 = state.begin_display_pin(
            "/org/bluez/hci0/dev_kbd".into(),
            "Keychron".into(),
            "22:33:44:55:66:77".into(),
            "1234".into(),
        );
        let (dismissed_dp3, restored) = state
            .on_device_state_changed("/org/bluez/hci0/dev_kbd", Some(true), None)
            .unwrap();
        assert_eq!(dismissed_dp3, dp3.prompt_id);
        assert_eq!(
            restored.as_ref().map(|p| p.prompt_id),
            Some(speaker_prompt.prompt_id)
        );

        // Release cancels active and all queued prompts.
        assert_eq!(state.release(), Some(speaker_prompt.prompt_id));
        assert_eq!(rx4.try_recv().unwrap(), Some(AgentReply::Canceled));
        assert!(state.active_prompt().is_none());
    }
}
