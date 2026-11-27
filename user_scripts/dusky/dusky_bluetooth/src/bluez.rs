//! Native BlueZ D-Bus backend running on a dedicated `async-io` worker thread.
//!
//! Uses a single system `zbus::Connection` that both subscribes to BlueZ signals
//! (`NameOwnerChanged`, `ObjectManager`, `PropertiesChanged`) and serves
//! `org.bluez.Agent1` at `/org/bluez/agent/dusky_bluetooth`. Outbound method
//! calls are spawned onto `conn.executor()` so signal processing and pairing
//! replies never stall or deadlock.

use crate::model::{
    AdapterState, AgentReply, AgentState, DeviceKind, DeviceOp, DeviceOpOutcome, DeviceState,
    PairingKind, PairingPrompt, is_canceled_bluez_error, service_uuid_name, translate_bluez_error,
    valid_rssi,
};
use futures_timer::Delay;
use iced_futures::futures::channel::mpsc::{self, UnboundedReceiver, UnboundedSender};
use iced_futures::futures::channel::oneshot;
use iced_futures::futures::future::Fuse;
use iced_futures::futures::{FutureExt, StreamExt, select_biased};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;
use zbus::message::Type as MessageType;
use zbus::zvariant::{ObjectPath, OwnedObjectPath, OwnedValue, Value};
use zbus::{Connection, DBusError, MatchRule, Message, MessageStream};

const DBUS_BUS_NAME: &str = "org.freedesktop.DBus";
const DBUS_PATH: &str = "/org/freedesktop/DBus";
const DBUS_IFACE: &str = "org.freedesktop.DBus";
const OBJECT_MANAGER_IFACE: &str = "org.freedesktop.DBus.ObjectManager";
const PROPERTIES_IFACE: &str = "org.freedesktop.DBus.Properties";

const BLUEZ_BUS_NAME: &str = "org.bluez";
const BLUEZ_ROOT_PATH: &str = "/";
const BLUEZ_AGENT_MANAGER_PATH: &str = "/org/bluez";
const BLUEZ_AGENT_MANAGER_IFACE: &str = "org.bluez.AgentManager1";
const BLUEZ_ADAPTER_IFACE: &str = "org.bluez.Adapter1";
const BLUEZ_DEVICE_IFACE: &str = "org.bluez.Device1";
const BLUEZ_BATTERY_IFACE: &str = "org.bluez.Battery1";

const AGENT_PATH: &str = "/org/bluez/agent/dusky_bluetooth";
const AGENT_CAPABILITY: &str = "KeyboardDisplay";

const RECONNECT_BACKOFF: Duration = Duration::from_secs(3);
const SHUTDOWN_UNREGISTER_TIMEOUT: Duration = Duration::from_millis(400);
const HYDRATE_RETRY_DELAYS: [Duration; 4] = [
    Duration::from_millis(500),
    Duration::from_secs(1),
    Duration::from_secs(2),
    Duration::from_secs(3),
];

type PropertyMap = HashMap<String, OwnedValue>;
type InterfaceMap = HashMap<String, PropertyMap>;
type ManagedObjects = HashMap<OwnedObjectPath, InterfaceMap>;

static BACKEND_RECEIVER: Mutex<Option<UnboundedReceiver<BackendEvent>>> = Mutex::new(None);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BackendStatus {
    Connecting,
    Unavailable,
    NoAdapter,
    Ready,
}

#[derive(Debug)]
pub enum BackendCommand {
    SetAdapterPowered {
        adapter_path: String,
        powered: bool,
    },
    SetAdapterDiscoverable {
        adapter_path: String,
        discoverable: bool,
    },
    StartDiscovery {
        adapter_path: String,
    },
    StopDiscovery {
        adapter_path: String,
    },
    Connect {
        device_path: String,
    },
    Disconnect {
        device_path: String,
    },
    Pair {
        device_path: String,
    },
    SetTrusted {
        device_path: String,
        trusted: bool,
    },
    SetBlocked {
        device_path: String,
        blocked: bool,
    },
    SetAlias {
        device_path: String,
        alias: String,
    },
    RemoveDevice {
        adapter_path: String,
        device_path: String,
    },
    AgentReply {
        prompt_id: u64,
        reply: AgentReply,
    },
    Shutdown(oneshot::Sender<()>),
}

#[derive(Debug, Clone)]
pub enum BackendEvent {
    StatusChanged(BackendStatus),
    FullSnapshot {
        adapters: Vec<AdapterState>,
        devices: Vec<DeviceState>,
    },
    AdapterUpserted(AdapterState),
    AdapterRemoved(String),
    DeviceUpserted(DeviceState),
    DeviceRemoved(String),
    BatteryUpdated {
        path: String,
        percentage: Option<u8>,
    },
    DiscoveryOwnershipChanged {
        adapter_path: String,
        owned: bool,
    },
    AliasUpdated {
        reset: bool,
    },
    DeviceOpFinished {
        path: String,
        op: DeviceOp,
        outcome: DeviceOpOutcome,
    },
    Notice {
        message: String,
        is_error: bool,
    },
    AgentPrompt(PairingPrompt),
    AgentPromptDismissed {
        prompt_id: u64,
    },
    Stopped,
}

#[derive(Clone)]
pub struct BackendHandle {
    tx: UnboundedSender<BackendCommand>,
}

impl BackendHandle {
    pub fn send(&self, cmd: BackendCommand) {
        let _ = self.tx.unbounded_send(cmd);
    }

    pub fn shutdown(&self) -> oneshot::Receiver<()> {
        let (ack_tx, ack_rx) = oneshot::channel();
        if self
            .tx
            .unbounded_send(BackendCommand::Shutdown(ack_tx))
            .is_err()
        {
            let (done_tx, done_rx) = oneshot::channel();
            let _ = done_tx.send(());
            return done_rx;
        }
        ack_rx
    }
}

pub fn take_backend_receiver() -> Option<UnboundedReceiver<BackendEvent>> {
    BACKEND_RECEIVER.lock().ok()?.take()
}

pub fn spawn_backend() -> BackendHandle {
    let (cmd_tx, cmd_rx) = mpsc::unbounded();
    let (evt_tx, evt_rx) = mpsc::unbounded();

    if let Ok(mut slot) = BACKEND_RECEIVER.lock() {
        *slot = Some(evt_rx);
    }

    let _ = std::thread::Builder::new()
        .name("bluetooth-dbus".into())
        .spawn(move || {
            iced_futures::futures::executor::block_on(worker_main(cmd_rx, evt_tx));
        });

    BackendHandle { tx: cmd_tx }
}

#[derive(Debug, DBusError)]
#[zbus(prefix = "org.bluez.Error")]
enum AgentError {
    #[zbus(error)]
    ZBus(zbus::Error),
    Canceled(String),
    Rejected(String),
}

struct BluezAgent {
    state: Arc<Mutex<AgentState>>,
    device_labels: Arc<Mutex<HashMap<String, (String, String)>>>,
    conn: Arc<OnceLock<Connection>>,
    evt_tx: UnboundedSender<BackendEvent>,
}

impl BluezAgent {
    fn device_meta(&self, path: &str) -> (String, String) {
        if let Ok(map) = self.device_labels.lock()
            && let Some((label, addr)) = map.get(path)
        {
            return (label.clone(), addr.clone());
        }
        let addr = mac_from_object_path(path).unwrap_or_default();
        let label = if addr.is_empty() {
            path.rsplit('/').next().unwrap_or(path).to_string()
        } else {
            addr.clone()
        };
        (label, addr)
    }

    async fn await_interactive(
        &self,
        device: OwnedObjectPath,
        kind: PairingKind,
    ) -> Result<AgentReply, AgentError> {
        let path = device.as_str().to_string();
        let (label, address) = self.device_meta(&path);

        let (maybe_prompt, rx) = {
            let mut guard = self.state.lock().unwrap_or_else(|e| e.into_inner());
            guard
                .begin_interactive(path, label, address, kind)
                .map_err(|_| AgentError::Canceled("Too many active pairing requests".into()))?
        };

        if let Some(prompt) = maybe_prompt {
            let _ = self
                .evt_tx
                .unbounded_send(BackendEvent::AgentPrompt(prompt));
        }

        match rx.await {
            Ok(reply) => Ok(reply),
            Err(_) => Err(AgentError::Canceled("Pairing canceled".into())),
        }
    }
}

fn emit_agent_transition(
    evt_tx: &UnboundedSender<BackendEvent>,
    dismissed_id: u64,
    promoted: Option<PairingPrompt>,
) {
    let _ = evt_tx.unbounded_send(BackendEvent::AgentPromptDismissed {
        prompt_id: dismissed_id,
    });
    if let Some(prompt) = promoted {
        let _ = evt_tx.unbounded_send(BackendEvent::AgentPrompt(prompt));
    }
}

#[zbus::interface(name = "org.bluez.Agent1")]
impl BluezAgent {
    fn release(&self) {
        let dismissed = {
            let mut guard = self.state.lock().unwrap_or_else(|e| e.into_inner());
            guard.release()
        };
        if let Some(prompt_id) = dismissed {
            let _ = self
                .evt_tx
                .unbounded_send(BackendEvent::AgentPromptDismissed { prompt_id });
        }
    }

    fn cancel(&self) {
        let transition = {
            let mut guard = self.state.lock().unwrap_or_else(|e| e.into_inner());
            guard.cancel()
        };
        if let Some((dismissed_id, promoted)) = transition {
            emit_agent_transition(&self.evt_tx, dismissed_id, promoted);
        }
    }

    async fn request_pin_code(&self, device: OwnedObjectPath) -> Result<String, AgentError> {
        match self
            .await_interactive(device, PairingKind::RequestPinCode)
            .await?
        {
            AgentReply::PinCode(pin) if !pin.is_empty() => Ok(pin),
            AgentReply::Rejected => Err(AgentError::Rejected("Rejected".into())),
            _ => Err(AgentError::Canceled("Canceled".into())),
        }
    }

    fn display_pin_code(&self, device: OwnedObjectPath, pincode: String) -> Result<(), AgentError> {
        let path = device.as_str().to_string();
        let (label, address) = self.device_meta(&path);
        let prompt = {
            let mut guard = self.state.lock().unwrap_or_else(|e| e.into_inner());
            guard.begin_display_pin(path, label, address, pincode)
        };
        let _ = self
            .evt_tx
            .unbounded_send(BackendEvent::AgentPrompt(prompt));
        Ok(())
    }

    async fn request_passkey(&self, device: OwnedObjectPath) -> Result<u32, AgentError> {
        match self
            .await_interactive(device, PairingKind::RequestPasskey)
            .await?
        {
            AgentReply::Passkey(key) => Ok(key),
            AgentReply::Rejected => Err(AgentError::Rejected("Rejected".into())),
            _ => Err(AgentError::Canceled("Canceled".into())),
        }
    }

    fn display_passkey(
        &self,
        device: OwnedObjectPath,
        passkey: u32,
        entered: u16,
    ) -> Result<(), AgentError> {
        let path = device.as_str().to_string();
        let (label, address) = self.device_meta(&path);
        let prompt = {
            let mut guard = self.state.lock().unwrap_or_else(|e| e.into_inner());
            guard.begin_display_passkey(path, label, address, passkey, entered)
        };
        let _ = self
            .evt_tx
            .unbounded_send(BackendEvent::AgentPrompt(prompt));
        Ok(())
    }

    async fn request_confirmation(
        &self,
        device: OwnedObjectPath,
        passkey: u32,
    ) -> Result<(), AgentError> {
        match self
            .await_interactive(
                device,
                PairingKind::RequestConfirmation {
                    passkey: Some(passkey),
                },
            )
            .await?
        {
            AgentReply::Confirm => Ok(()),
            AgentReply::Rejected => Err(AgentError::Rejected("Rejected".into())),
            _ => Err(AgentError::Canceled("Canceled".into())),
        }
    }

    async fn request_authorization(&self, device: OwnedObjectPath) -> Result<(), AgentError> {
        match self
            .await_interactive(device, PairingKind::RequestConfirmation { passkey: None })
            .await?
        {
            AgentReply::Confirm => Ok(()),
            AgentReply::Rejected => Err(AgentError::Rejected("Rejected".into())),
            _ => Err(AgentError::Canceled("Canceled".into())),
        }
    }

    async fn authorize_service(
        &self,
        device: OwnedObjectPath,
        uuid: String,
    ) -> Result<(), AgentError> {
        let path = device.as_str().to_string();
        let service_name = service_uuid_name(&uuid);
        match self
            .await_interactive(device, PairingKind::AuthorizeService { uuid, service_name })
            .await?
        {
            AgentReply::AcceptService { always_trust } => {
                if always_trust && let Some(conn) = self.conn.get() {
                    let _ = set_dbus_property(
                        conn,
                        &path,
                        BLUEZ_DEVICE_IFACE,
                        "Trusted",
                        Value::from(true),
                    )
                    .await;
                }
                Ok(())
            }
            AgentReply::Rejected => Err(AgentError::Rejected("Rejected".into())),
            _ => Err(AgentError::Canceled("Canceled".into())),
        }
    }
}

struct WorkerCache {
    adapters: BTreeMap<String, AdapterState>,
    devices: BTreeMap<String, DeviceState>,
    pending_batteries: HashMap<String, u8>,
    agent_registered: bool,
    status: BackendStatus,
}

impl WorkerCache {
    fn new() -> Self {
        Self {
            adapters: BTreeMap::new(),
            devices: BTreeMap::new(),
            pending_batteries: HashMap::new(),
            agent_registered: false,
            status: BackendStatus::Connecting,
        }
    }

    fn set_status(&mut self, status: BackendStatus, evt_tx: &UnboundedSender<BackendEvent>) {
        if self.status != status {
            self.status = status;
            let _ = evt_tx.unbounded_send(BackendEvent::StatusChanged(status));
        }
    }

    fn sync_device_labels(&self, labels: &Arc<Mutex<HashMap<String, (String, String)>>>) {
        if let Ok(mut guard) = labels.lock() {
            guard.clear();
            for dev in self.devices.values() {
                guard.insert(
                    dev.path.clone(),
                    (dev.display_name().to_string(), dev.address.clone()),
                );
            }
        }
    }

    fn upsert_device_label(
        dev: &DeviceState,
        labels: &Arc<Mutex<HashMap<String, (String, String)>>>,
    ) {
        if let Ok(mut guard) = labels.lock() {
            guard.insert(
                dev.path.clone(),
                (dev.display_name().to_string(), dev.address.clone()),
            );
        }
    }
}

async fn worker_main(
    mut cmd_rx: UnboundedReceiver<BackendCommand>,
    evt_tx: UnboundedSender<BackendEvent>,
) {
    let agent_state = Arc::new(Mutex::new(AgentState::new()));
    let device_labels: Arc<Mutex<HashMap<String, (String, String)>>> =
        Arc::new(Mutex::new(HashMap::new()));
    let discovery_started_by_us: Arc<Mutex<HashSet<String>>> = Arc::new(Mutex::new(HashSet::new()));

    let mut cache = WorkerCache::new();
    let _ = evt_tx.unbounded_send(BackendEvent::StatusChanged(BackendStatus::Connecting));

    loop {
        let agent_conn = Arc::new(OnceLock::new());
        let agent = BluezAgent {
            state: Arc::clone(&agent_state),
            device_labels: Arc::clone(&device_labels),
            conn: Arc::clone(&agent_conn),
            evt_tx: evt_tx.clone(),
        };

        let conn = match connect_system_bus(agent).await {
            Ok(conn) => {
                let _ = agent_conn.set(conn.clone());
                conn
            }
            Err(_) => {
                cache.set_status(BackendStatus::Unavailable, &evt_tx);
                if wait_backoff_or_shutdown(&mut cmd_rx, &evt_tx).await {
                    return;
                }
                continue;
            }
        };

        let streams = match subscribe_streams(&conn).await {
            Ok(streams) => streams,
            Err(_) => {
                cache.set_status(BackendStatus::Unavailable, &evt_tx);
                if wait_backoff_or_shutdown(&mut cmd_rx, &evt_tx).await {
                    return;
                }
                continue;
            }
        };

        let (owner_stream, om_stream, props_stream) = streams;
        let mut owner_stream = Box::pin(owner_stream.fuse());
        let mut om_stream = Box::pin(om_stream.fuse());
        let mut props_stream = Box::pin(props_stream.fuse());

        let mut hydrate_retry_attempt: usize = 0;
        let mut hydrate_retry_timer: Fuse<Delay> = Fuse::terminated();

        if bluez_has_owner(&conn).await {
            if !hydrate_bluez(&conn, &mut cache, &device_labels, &evt_tx).await {
                schedule_hydrate_retry(
                    &mut cache,
                    &mut hydrate_retry_attempt,
                    &mut hydrate_retry_timer,
                    &evt_tx,
                );
            }
        } else {
            clear_bluez_state(
                &mut cache,
                &agent_state,
                &device_labels,
                &discovery_started_by_us,
                &evt_tx,
            );
        }

        loop {
            select_biased! {
                cmd = cmd_rx.next() => {
                    match cmd {
                        Some(BackendCommand::Shutdown(ack_tx)) => {
                            teardown_session(
                                &conn,
                                &mut cache,
                                &agent_state,
                                &discovery_started_by_us,
                                &evt_tx,
                            ).await;
                            let _ = evt_tx.unbounded_send(BackendEvent::Stopped);
                            let _ = ack_tx.send(());
                            return;
                        }
                        Some(cmd) => {
                            handle_command(
                                &conn,
                                cmd,
                                &agent_state,
                                &discovery_started_by_us,
                                &evt_tx,
                            );
                        }
                        None => {
                            teardown_session(
                                &conn,
                                &mut cache,
                                &agent_state,
                                &discovery_started_by_us,
                                &evt_tx,
                            ).await;
                            let _ = evt_tx.unbounded_send(BackendEvent::Stopped);
                            return;
                        }
                    }
                }
                _ = hydrate_retry_timer => {
                    hydrate_retry_timer = Fuse::terminated();
                    if bluez_has_owner(&conn).await {
                        if hydrate_bluez(&conn, &mut cache, &device_labels, &evt_tx).await {
                            hydrate_retry_attempt = 0;
                        } else {
                            schedule_hydrate_retry(
                                &mut cache,
                                &mut hydrate_retry_attempt,
                                &mut hydrate_retry_timer,
                                &evt_tx,
                            );
                        }
                    } else {
                        hydrate_retry_attempt = 0;
                        clear_bluez_state(
                            &mut cache,
                            &agent_state,
                            &device_labels,
                            &discovery_started_by_us,
                            &evt_tx,
                        );
                    }
                }
                maybe_msg = owner_stream.next() => {
                    match maybe_msg {
                        Some(Ok(msg)) => {
                            handle_name_owner_changed(
                                &conn,
                                &msg,
                                &mut cache,
                                &agent_state,
                                &device_labels,
                                &discovery_started_by_us,
                                &mut hydrate_retry_attempt,
                                &mut hydrate_retry_timer,
                                &evt_tx,
                            ).await;
                        }
                        Some(Err(_)) | None => break,
                    }
                }
                maybe_msg = om_stream.next() => {
                    match maybe_msg {
                        Some(Ok(msg)) => {
                            handle_object_manager_signal(
                                &msg,
                                &mut cache,
                                &agent_state,
                                &device_labels,
                                &discovery_started_by_us,
                                &evt_tx,
                            );
                        }
                        Some(Err(_)) | None => break,
                    }
                }
                maybe_msg = props_stream.next() => {
                    match maybe_msg {
                        Some(Ok(msg)) => {
                            handle_properties_changed(
                                &msg,
                                &mut cache,
                                &agent_state,
                                &device_labels,
                                &discovery_started_by_us,
                                &evt_tx,
                            );
                        }
                        Some(Err(_)) | None => break,
                    }
                }
            }
        }

        clear_bluez_state(
            &mut cache,
            &agent_state,
            &device_labels,
            &discovery_started_by_us,
            &evt_tx,
        );
        if wait_backoff_or_shutdown(&mut cmd_rx, &evt_tx).await {
            return;
        }
    }
}

async fn connect_system_bus(agent: BluezAgent) -> zbus::Result<Connection> {
    zbus::connection::Builder::system()?
        .serve_at(AGENT_PATH, agent)?
        .build()
        .await
}

async fn subscribe_streams(
    conn: &Connection,
) -> zbus::Result<(MessageStream, MessageStream, MessageStream)> {
    let owner_rule = MatchRule::builder()
        .msg_type(MessageType::Signal)
        .sender(DBUS_BUS_NAME)?
        .interface(DBUS_IFACE)?
        .member("NameOwnerChanged")?
        .arg(0, BLUEZ_BUS_NAME)?
        .build();
    let om_rule = MatchRule::builder()
        .msg_type(MessageType::Signal)
        .sender(BLUEZ_BUS_NAME)?
        .interface(OBJECT_MANAGER_IFACE)?
        .path(BLUEZ_ROOT_PATH)?
        .build();
    let props_rule = MatchRule::builder()
        .msg_type(MessageType::Signal)
        .sender(BLUEZ_BUS_NAME)?
        .interface(PROPERTIES_IFACE)?
        .member("PropertiesChanged")?
        .build();

    let owner_stream = MessageStream::for_match_rule(owner_rule, conn, Some(32)).await?;
    let om_stream = MessageStream::for_match_rule(om_rule, conn, Some(128)).await?;
    let props_stream = MessageStream::for_match_rule(props_rule, conn, Some(256)).await?;
    Ok((owner_stream, om_stream, props_stream))
}

async fn wait_backoff_or_shutdown(
    cmd_rx: &mut UnboundedReceiver<BackendCommand>,
    evt_tx: &UnboundedSender<BackendEvent>,
) -> bool {
    let mut timer = Delay::new(RECONNECT_BACKOFF).fuse();
    loop {
        select_biased! {
            cmd = cmd_rx.next() => {
                match cmd {
                    Some(BackendCommand::Shutdown(ack_tx)) => {
                        let _ = evt_tx.unbounded_send(BackendEvent::Stopped);
                        let _ = ack_tx.send(());
                        return true;
                    }
                    None => {
                        let _ = evt_tx.unbounded_send(BackendEvent::Stopped);
                        return true;
                    }
                    Some(_) => {}
                }
            }
            _ = timer => {
                return false;
            }
        }
    }
}

async fn bluez_has_owner(conn: &Connection) -> bool {
    match conn
        .call_method(
            Some(DBUS_BUS_NAME),
            DBUS_PATH,
            Some(DBUS_IFACE),
            "GetNameOwner",
            &(BLUEZ_BUS_NAME,),
        )
        .await
    {
        Ok(reply) => reply
            .body()
            .deserialize::<String>()
            .map(|owner| !owner.is_empty())
            .unwrap_or(false),
        Err(_) => false,
    }
}

fn schedule_hydrate_retry(
    cache: &mut WorkerCache,
    attempt: &mut usize,
    timer: &mut Fuse<Delay>,
    evt_tx: &UnboundedSender<BackendEvent>,
) {
    if let Some(&delay) = HYDRATE_RETRY_DELAYS.get(*attempt) {
        *attempt += 1;
        *timer = Delay::new(delay).fuse();
        cache.set_status(BackendStatus::Connecting, evt_tx);
    } else {
        *attempt = 0;
        *timer = Fuse::terminated();
        cache.set_status(BackendStatus::Unavailable, evt_tx);
    }
}

async fn hydrate_bluez(
    conn: &Connection,
    cache: &mut WorkerCache,
    device_labels: &Arc<Mutex<HashMap<String, (String, String)>>>,
    evt_tx: &UnboundedSender<BackendEvent>,
) -> bool {
    let Ok(reply) = conn
        .call_method(
            Some(BLUEZ_BUS_NAME),
            BLUEZ_ROOT_PATH,
            Some(OBJECT_MANAGER_IFACE),
            "GetManagedObjects",
            &(),
        )
        .await
    else {
        return false;
    };

    let Ok(objects) = reply.body().deserialize::<ManagedObjects>() else {
        return false;
    };

    cache.adapters.clear();
    cache.devices.clear();
    cache.pending_batteries.clear();

    for (obj_path, ifaces) in &objects {
        let path = obj_path.as_str();
        if let Some(props) = ifaces.get(BLUEZ_ADAPTER_IFACE) {
            let adapter = decode_adapter(path, props);
            cache.adapters.insert(adapter.path.clone(), adapter);
        }
        if let Some(props) = ifaces.get(BLUEZ_DEVICE_IFACE) {
            let battery = ifaces
                .get(BLUEZ_BATTERY_IFACE)
                .and_then(|b| prop_u8(b, "Percentage"));
            let device = decode_device(path, props, battery);
            cache.devices.insert(device.path.clone(), device);
        }
    }

    cache.sync_device_labels(device_labels);

    let next_status = if cache.adapters.is_empty() {
        BackendStatus::NoAdapter
    } else {
        BackendStatus::Ready
    };

    let _ = evt_tx.unbounded_send(BackendEvent::FullSnapshot {
        adapters: cache.adapters.values().cloned().collect(),
        devices: cache.devices.values().cloned().collect(),
    });
    cache.set_status(next_status, evt_tx);

    register_agent(conn, cache, evt_tx).await;
    true
}

fn clear_bluez_state(
    cache: &mut WorkerCache,
    agent_state: &Arc<Mutex<AgentState>>,
    device_labels: &Arc<Mutex<HashMap<String, (String, String)>>>,
    discovery_started_by_us: &Arc<Mutex<HashSet<String>>>,
    evt_tx: &UnboundedSender<BackendEvent>,
) {
    cache.adapters.clear();
    cache.devices.clear();
    cache.pending_batteries.clear();
    cache.agent_registered = false;
    if let Ok(mut guard) = discovery_started_by_us.lock() {
        guard.clear();
    }
    if let Ok(mut guard) = device_labels.lock() {
        guard.clear();
    }
    let dismissed = {
        let mut guard = agent_state.lock().unwrap_or_else(|e| e.into_inner());
        guard.cancel_all()
    };
    if let Some(prompt_id) = dismissed {
        let _ = evt_tx.unbounded_send(BackendEvent::AgentPromptDismissed { prompt_id });
    }
    let _ = evt_tx.unbounded_send(BackendEvent::FullSnapshot {
        adapters: Vec::new(),
        devices: Vec::new(),
    });
    cache.set_status(BackendStatus::Unavailable, evt_tx);
}

async fn register_agent(
    conn: &Connection,
    cache: &mut WorkerCache,
    evt_tx: &UnboundedSender<BackendEvent>,
) {
    if cache.agent_registered {
        return;
    }
    let Ok(agent_path) = ObjectPath::try_from(AGENT_PATH) else {
        return;
    };

    match conn
        .call_method(
            Some(BLUEZ_BUS_NAME),
            BLUEZ_AGENT_MANAGER_PATH,
            Some(BLUEZ_AGENT_MANAGER_IFACE),
            "RegisterAgent",
            &(agent_path.clone(), AGENT_CAPABILITY),
        )
        .await
    {
        Ok(_) => {
            cache.agent_registered = true;
        }
        Err(err) if err.to_string().contains("AlreadyExists") => {
            cache.agent_registered = true;
        }
        Err(err) => {
            let _ = evt_tx.unbounded_send(BackendEvent::Notice {
                message: format!(
                    "Pairing agent could not register: {}",
                    translate_bluez_error(&err.to_string())
                ),
                is_error: false,
            });
            return;
        }
    }

    // Request default agent status; tolerate another session agent holding default.
    let _ = conn
        .call_method(
            Some(BLUEZ_BUS_NAME),
            BLUEZ_AGENT_MANAGER_PATH,
            Some(BLUEZ_AGENT_MANAGER_IFACE),
            "RequestDefaultAgent",
            &(agent_path,),
        )
        .await;
}

async fn unregister_agent(conn: &Connection) {
    if let Ok(agent_path) = ObjectPath::try_from(AGENT_PATH) {
        let _ = conn
            .call_method(
                Some(BLUEZ_BUS_NAME),
                BLUEZ_AGENT_MANAGER_PATH,
                Some(BLUEZ_AGENT_MANAGER_IFACE),
                "UnregisterAgent",
                &(agent_path,),
            )
            .await;
    }
}

async fn teardown_session(
    conn: &Connection,
    cache: &mut WorkerCache,
    agent_state: &Arc<Mutex<AgentState>>,
    discovery_started_by_us: &Arc<Mutex<HashSet<String>>>,
    evt_tx: &UnboundedSender<BackendEvent>,
) {
    let dismissed = {
        let mut guard = agent_state.lock().unwrap_or_else(|e| e.into_inner());
        guard.cancel_all()
    };
    if let Some(prompt_id) = dismissed {
        let _ = evt_tx.unbounded_send(BackendEvent::AgentPromptDismissed { prompt_id });
    }

    let active_scans: Vec<String> = discovery_started_by_us
        .lock()
        .map(|mut set| set.drain().collect())
        .unwrap_or_default();

    let registered = cache.agent_registered;
    cache.agent_registered = false;

    let cleanup = async {
        for adapter_path in active_scans {
            let _ = conn
                .call_method(
                    Some(BLUEZ_BUS_NAME),
                    adapter_path.as_str(),
                    Some(BLUEZ_ADAPTER_IFACE),
                    "StopDiscovery",
                    &(),
                )
                .await;
        }
        if registered {
            unregister_agent(conn).await;
        }
        let _ = conn
            .object_server()
            .remove::<BluezAgent, _>(AGENT_PATH)
            .await;
    };

    let mut cleanup = Box::pin(cleanup.fuse());
    let mut deadline = Delay::new(SHUTDOWN_UNREGISTER_TIMEOUT).fuse();
    select_biased! {
        _ = cleanup => {}
        _ = deadline => {}
    }
}

#[allow(clippy::too_many_arguments)]
async fn handle_name_owner_changed(
    conn: &Connection,
    msg: &Message,
    cache: &mut WorkerCache,
    agent_state: &Arc<Mutex<AgentState>>,
    device_labels: &Arc<Mutex<HashMap<String, (String, String)>>>,
    discovery_started_by_us: &Arc<Mutex<HashSet<String>>>,
    hydrate_retry_attempt: &mut usize,
    hydrate_retry_timer: &mut Fuse<Delay>,
    evt_tx: &UnboundedSender<BackendEvent>,
) {
    let Ok((name, _old_owner, new_owner)) = msg.body().deserialize::<(String, String, String)>()
    else {
        return;
    };
    if name != BLUEZ_BUS_NAME {
        return;
    }

    *hydrate_retry_attempt = 0;
    *hydrate_retry_timer = Fuse::terminated();

    if new_owner.is_empty() {
        clear_bluez_state(
            cache,
            agent_state,
            device_labels,
            discovery_started_by_us,
            evt_tx,
        );
    } else {
        cache.agent_registered = false;
        if let Ok(mut guard) = discovery_started_by_us.lock() {
            guard.clear();
        }
        if !hydrate_bluez(conn, cache, device_labels, evt_tx).await {
            schedule_hydrate_retry(cache, hydrate_retry_attempt, hydrate_retry_timer, evt_tx);
        }
    }
}

fn handle_object_manager_signal(
    msg: &Message,
    cache: &mut WorkerCache,
    agent_state: &Arc<Mutex<AgentState>>,
    device_labels: &Arc<Mutex<HashMap<String, (String, String)>>>,
    discovery_started_by_us: &Arc<Mutex<HashSet<String>>>,
    evt_tx: &UnboundedSender<BackendEvent>,
) {
    let header = msg.header();
    let Some(member) = header.member().map(|m| m.as_str()) else {
        return;
    };

    match member {
        "InterfacesAdded" => {
            let Ok((obj_path, ifaces)) =
                msg.body().deserialize::<(OwnedObjectPath, InterfaceMap)>()
            else {
                return;
            };
            let path = obj_path.as_str();

            if let Some(props) = ifaces.get(BLUEZ_ADAPTER_IFACE) {
                let adapter = decode_adapter(path, props);
                cache.adapters.insert(adapter.path.clone(), adapter.clone());
                let _ = evt_tx.unbounded_send(BackendEvent::AdapterUpserted(adapter));
                cache.set_status(BackendStatus::Ready, evt_tx);
            }

            if let Some(props) = ifaces.get(BLUEZ_DEVICE_IFACE) {
                let pending_battery = cache.pending_batteries.remove(path);
                let existing_battery = cache.devices.get(path).and_then(|d| d.battery);
                let battery = ifaces
                    .get(BLUEZ_BATTERY_IFACE)
                    .and_then(|b| prop_u8(b, "Percentage"))
                    .or(pending_battery)
                    .or(existing_battery);
                let device = decode_device(path, props, battery);
                WorkerCache::upsert_device_label(&device, device_labels);
                cache.devices.insert(device.path.clone(), device.clone());
                let _ = evt_tx.unbounded_send(BackendEvent::DeviceUpserted(device));
            } else if let Some(b_props) = ifaces.get(BLUEZ_BATTERY_IFACE) {
                let percentage = prop_u8(b_props, "Percentage");
                if let Some(dev) = cache.devices.get_mut(path) {
                    dev.battery = percentage;
                    let _ = evt_tx.unbounded_send(BackendEvent::BatteryUpdated {
                        path: path.to_string(),
                        percentage,
                    });
                } else if let Some(pct) = percentage {
                    cache.pending_batteries.insert(path.to_string(), pct);
                }
            }
        }
        "InterfacesRemoved" => {
            let Ok((obj_path, ifaces)) = msg.body().deserialize::<(OwnedObjectPath, Vec<String>)>()
            else {
                return;
            };
            let path = obj_path.as_str();

            for iface in ifaces {
                match iface.as_str() {
                    BLUEZ_BATTERY_IFACE => {
                        cache.pending_batteries.remove(path);
                        if let Some(dev) = cache.devices.get_mut(path) {
                            dev.battery = None;
                            let _ = evt_tx.unbounded_send(BackendEvent::BatteryUpdated {
                                path: path.to_string(),
                                percentage: None,
                            });
                        }
                    }
                    BLUEZ_DEVICE_IFACE => {
                        cache.pending_batteries.remove(path);
                        if cache.devices.remove(path).is_some() {
                            if let Ok(mut guard) = device_labels.lock() {
                                guard.remove(path);
                            }
                            let transition = {
                                let mut guard =
                                    agent_state.lock().unwrap_or_else(|e| e.into_inner());
                                guard.on_device_removed(path)
                            };
                            if let Some((dismissed_id, promoted)) = transition {
                                emit_agent_transition(evt_tx, dismissed_id, promoted);
                            }
                            let _ = evt_tx
                                .unbounded_send(BackendEvent::DeviceRemoved(path.to_string()));
                        }
                    }
                    BLUEZ_ADAPTER_IFACE => {
                        let adapter_prefix = format!("{path}/");
                        cache
                            .pending_batteries
                            .retain(|k, _| !k.starts_with(&adapter_prefix));
                        if cache.adapters.remove(path).is_some() {
                            if discovery_started_by_us
                                .lock()
                                .map(|mut set| set.remove(path))
                                .unwrap_or(false)
                            {
                                let _ = evt_tx.unbounded_send(
                                    BackendEvent::DiscoveryOwnershipChanged {
                                        adapter_path: path.to_string(),
                                        owned: false,
                                    },
                                );
                            }
                            let child_paths: Vec<String> = cache
                                .devices
                                .values()
                                .filter(|d| d.adapter_path == path)
                                .map(|d| d.path.clone())
                                .collect();
                            for child in child_paths {
                                cache.devices.remove(&child);
                                if let Ok(mut guard) = device_labels.lock() {
                                    guard.remove(&child);
                                }
                                let transition = {
                                    let mut guard =
                                        agent_state.lock().unwrap_or_else(|e| e.into_inner());
                                    guard.on_device_removed(&child)
                                };
                                if let Some((dismissed_id, promoted)) = transition {
                                    emit_agent_transition(evt_tx, dismissed_id, promoted);
                                }
                                let _ = evt_tx.unbounded_send(BackendEvent::DeviceRemoved(child));
                            }
                            let _ = evt_tx
                                .unbounded_send(BackendEvent::AdapterRemoved(path.to_string()));
                            if cache.adapters.is_empty() {
                                cache.set_status(BackendStatus::NoAdapter, evt_tx);
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
        _ => {}
    }
}

fn handle_properties_changed(
    msg: &Message,
    cache: &mut WorkerCache,
    agent_state: &Arc<Mutex<AgentState>>,
    device_labels: &Arc<Mutex<HashMap<String, (String, String)>>>,
    discovery_started_by_us: &Arc<Mutex<HashSet<String>>>,
    evt_tx: &UnboundedSender<BackendEvent>,
) {
    let header = msg.header();
    let Some(path) = header.path().map(|p| p.as_str()) else {
        return;
    };
    let Ok((iface, changed, invalidated)) = msg
        .body()
        .deserialize::<(String, PropertyMap, Vec<String>)>()
    else {
        return;
    };

    match iface.as_str() {
        BLUEZ_ADAPTER_IFACE => {
            if let Some(adapter) = cache.adapters.get_mut(path) {
                let powered_turned_off = prop_bool(&changed, "Powered") == Some(false);
                let discovery_stopped = prop_bool(&changed, "Discovering") == Some(false);
                apply_adapter_properties(adapter, &changed);
                if (powered_turned_off || discovery_stopped)
                    && discovery_started_by_us
                        .lock()
                        .map(|mut set| set.remove(path))
                        .unwrap_or(false)
                {
                    let _ = evt_tx.unbounded_send(BackendEvent::DiscoveryOwnershipChanged {
                        adapter_path: path.to_string(),
                        owned: false,
                    });
                }
                let _ = evt_tx.unbounded_send(BackendEvent::AdapterUpserted(adapter.clone()));
            }
        }
        BLUEZ_DEVICE_IFACE => {
            if let Some(device) = cache.devices.get_mut(path) {
                let (paired_change, connected_change) =
                    apply_device_properties(device, &changed, &invalidated);
                WorkerCache::upsert_device_label(device, device_labels);
                if paired_change.is_some() || connected_change.is_some() {
                    let transition = {
                        let mut guard = agent_state.lock().unwrap_or_else(|e| e.into_inner());
                        guard.on_device_state_changed(path, paired_change, connected_change)
                    };
                    if let Some((dismissed_id, promoted)) = transition {
                        emit_agent_transition(evt_tx, dismissed_id, promoted);
                    }
                }
                let _ = evt_tx.unbounded_send(BackendEvent::DeviceUpserted(device.clone()));
            }
        }
        BLUEZ_BATTERY_IFACE => {
            let mut updated = None;
            if let Some(pct) = prop_u8(&changed, "Percentage") {
                updated = Some(Some(pct));
            } else if invalidated.iter().any(|k| k == "Percentage") {
                updated = Some(None);
            }
            if let Some(percentage) = updated {
                if let Some(dev) = cache.devices.get_mut(path) {
                    dev.battery = percentage;
                    let _ = evt_tx.unbounded_send(BackendEvent::BatteryUpdated {
                        path: path.to_string(),
                        percentage,
                    });
                } else if let Some(pct) = percentage {
                    cache.pending_batteries.insert(path.to_string(), pct);
                } else {
                    cache.pending_batteries.remove(path);
                }
            }
        }
        _ => {}
    }
}

fn handle_command(
    conn: &Connection,
    cmd: BackendCommand,
    agent_state: &Arc<Mutex<AgentState>>,
    discovery_started_by_us: &Arc<Mutex<HashSet<String>>>,
    evt_tx: &UnboundedSender<BackendEvent>,
) {
    let executor = conn.executor();
    match cmd {
        BackendCommand::AgentReply { prompt_id, reply } => {
            let outcome = {
                let mut guard = agent_state.lock().unwrap_or_else(|e| e.into_inner());
                guard.resolve(prompt_id, reply)
            };
            if let Some(outcome) = outcome {
                emit_agent_transition(evt_tx, outcome.dismissed_id, outcome.promoted);
                if let Some(device_path) = outcome.cancel_pairing_device {
                    let conn = conn.clone();
                    executor
                        .spawn(
                            async move {
                                let _ = conn
                                    .call_method(
                                        Some(BLUEZ_BUS_NAME),
                                        device_path.as_str(),
                                        Some(BLUEZ_DEVICE_IFACE),
                                        "CancelPairing",
                                        &(),
                                    )
                                    .await;
                            },
                            "bluez-cancel-pairing",
                        )
                        .detach();
                }
            }
        }
        BackendCommand::SetAdapterPowered {
            adapter_path,
            powered,
        } => {
            let conn = conn.clone();
            let evt_tx = evt_tx.clone();
            let discovery = Arc::clone(discovery_started_by_us);
            executor
                .spawn(
                    async move {
                        if let Err(err) = set_dbus_property(
                            &conn,
                            &adapter_path,
                            BLUEZ_ADAPTER_IFACE,
                            "Powered",
                            Value::from(powered),
                        )
                        .await
                        {
                            let _ = evt_tx.unbounded_send(BackendEvent::Notice {
                                message: translate_bluez_error(&err.to_string()),
                                is_error: true,
                            });
                        } else if !powered
                            && discovery
                                .lock()
                                .map(|mut set| set.remove(&adapter_path))
                                .unwrap_or(false)
                        {
                            let _ = evt_tx.unbounded_send(
                                BackendEvent::DiscoveryOwnershipChanged {
                                    adapter_path,
                                    owned: false,
                                },
                            );
                        }
                    },
                    "bluez-set-powered",
                )
                .detach();
        }
        BackendCommand::SetAdapterDiscoverable {
            adapter_path,
            discoverable,
        } => {
            let conn = conn.clone();
            let evt_tx = evt_tx.clone();
            executor
                .spawn(
                    async move {
                        if let Err(err) = set_dbus_property(
                            &conn,
                            &adapter_path,
                            BLUEZ_ADAPTER_IFACE,
                            "Discoverable",
                            Value::from(discoverable),
                        )
                        .await
                        {
                            let _ = evt_tx.unbounded_send(BackendEvent::Notice {
                                message: translate_bluez_error(&err.to_string()),
                                is_error: true,
                            });
                        }
                    },
                    "bluez-set-discoverable",
                )
                .detach();
        }
        BackendCommand::StartDiscovery { adapter_path } => {
            let conn = conn.clone();
            let evt_tx = evt_tx.clone();
            let discovery = Arc::clone(discovery_started_by_us);
            executor
                .spawn(
                    async move {
                        match conn
                            .call_method(
                                Some(BLUEZ_BUS_NAME),
                                adapter_path.as_str(),
                                Some(BLUEZ_ADAPTER_IFACE),
                                "StartDiscovery",
                                &(),
                            )
                            .await
                        {
                            Ok(_) => {
                                if let Ok(mut set) = discovery.lock() {
                                    set.insert(adapter_path.clone());
                                }
                                let _ = evt_tx.unbounded_send(
                                    BackendEvent::DiscoveryOwnershipChanged {
                                        adapter_path,
                                        owned: true,
                                    },
                                );
                            }
                            Err(err) => {
                                let raw = err.to_string();
                                if !raw.to_ascii_lowercase().contains("inprogress") {
                                    let _ = evt_tx.unbounded_send(BackendEvent::Notice {
                                        message: translate_bluez_error(&raw),
                                        is_error: true,
                                    });
                                }
                            }
                        }
                    },
                    "bluez-start-discovery",
                )
                .detach();
        }
        BackendCommand::StopDiscovery { adapter_path } => {
            let conn = conn.clone();
            let evt_tx = evt_tx.clone();
            let discovery = Arc::clone(discovery_started_by_us);
            executor
                .spawn(
                    async move {
                        let started_by_us = discovery
                            .lock()
                            .map(|set| set.contains(&adapter_path))
                            .unwrap_or(false);
                        if !started_by_us {
                            let _ = evt_tx.unbounded_send(BackendEvent::Notice {
                                message: "Discovery was started by another application".to_string(),
                                is_error: false,
                            });
                            return;
                        }
                        match conn
                            .call_method(
                                Some(BLUEZ_BUS_NAME),
                                adapter_path.as_str(),
                                Some(BLUEZ_ADAPTER_IFACE),
                                "StopDiscovery",
                                &(),
                            )
                            .await
                        {
                            Ok(_) => {
                                if let Ok(mut set) = discovery.lock() {
                                    set.remove(&adapter_path);
                                }
                                let _ = evt_tx.unbounded_send(
                                    BackendEvent::DiscoveryOwnershipChanged {
                                        adapter_path,
                                        owned: false,
                                    },
                                );
                            }
                            Err(err) => {
                                let raw = err.to_string();
                                let external =
                                    raw.to_ascii_lowercase().contains("no discovery started");
                                if external {
                                    if let Ok(mut set) = discovery.lock() {
                                        set.remove(&adapter_path);
                                    }
                                    let _ = evt_tx.unbounded_send(
                                        BackendEvent::DiscoveryOwnershipChanged {
                                            adapter_path,
                                            owned: false,
                                        },
                                    );
                                    let _ = evt_tx.unbounded_send(BackendEvent::Notice {
                                        message: "Discovery was started by another application"
                                            .to_string(),
                                        is_error: false,
                                    });
                                } else {
                                    let _ = evt_tx.unbounded_send(BackendEvent::Notice {
                                        message: translate_bluez_error(&raw),
                                        is_error: true,
                                    });
                                }
                            }
                        }
                    },
                    "bluez-stop-discovery",
                )
                .detach();
        }
        BackendCommand::Connect { device_path } => {
            spawn_device_method(conn, device_path, "Connect", DeviceOp::Connecting, evt_tx);
        }
        BackendCommand::Disconnect { device_path } => {
            spawn_device_method(
                conn,
                device_path,
                "Disconnect",
                DeviceOp::Disconnecting,
                evt_tx,
            );
        }
        BackendCommand::Pair { device_path } => {
            spawn_device_method(conn, device_path, "Pair", DeviceOp::Pairing, evt_tx);
        }
        BackendCommand::SetTrusted {
            device_path,
            trusted,
        } => {
            let conn = conn.clone();
            let evt_tx = evt_tx.clone();
            executor
                .spawn(
                    async move {
                        if let Err(err) = set_dbus_property(
                            &conn,
                            &device_path,
                            BLUEZ_DEVICE_IFACE,
                            "Trusted",
                            Value::from(trusted),
                        )
                        .await
                        {
                            let _ = evt_tx.unbounded_send(BackendEvent::Notice {
                                message: translate_bluez_error(&err.to_string()),
                                is_error: true,
                            });
                        }
                    },
                    "bluez-set-trusted",
                )
                .detach();
        }
        BackendCommand::SetBlocked {
            device_path,
            blocked,
        } => {
            let conn = conn.clone();
            let evt_tx = evt_tx.clone();
            executor
                .spawn(
                    async move {
                        if let Err(err) = set_dbus_property(
                            &conn,
                            &device_path,
                            BLUEZ_DEVICE_IFACE,
                            "Blocked",
                            Value::from(blocked),
                        )
                        .await
                        {
                            let _ = evt_tx.unbounded_send(BackendEvent::Notice {
                                message: translate_bluez_error(&err.to_string()),
                                is_error: true,
                            });
                        }
                    },
                    "bluez-set-blocked",
                )
                .detach();
        }
        BackendCommand::SetAlias { device_path, alias } => {
            let conn = conn.clone();
            let evt_tx = evt_tx.clone();
            let reset = alias.is_empty();
            executor
                .spawn(
                    async move {
                        match set_dbus_property(
                            &conn,
                            &device_path,
                            BLUEZ_DEVICE_IFACE,
                            "Alias",
                            Value::from(alias.as_str()),
                        )
                        .await
                        {
                            Ok(()) => {
                                let _ = evt_tx
                                    .unbounded_send(BackendEvent::AliasUpdated { reset });
                            }
                            Err(err) => {
                                let _ = evt_tx.unbounded_send(BackendEvent::Notice {
                                    message: translate_bluez_error(&err.to_string()),
                                    is_error: true,
                                });
                            }
                        }
                    },
                    "bluez-set-alias",
                )
                .detach();
        }
        BackendCommand::RemoveDevice {
            adapter_path,
            device_path,
        } => {
            let conn = conn.clone();
            let evt_tx = evt_tx.clone();
            executor
                .spawn(
                    async move {
                        let outcome = match ObjectPath::try_from(device_path.as_str()) {
                            Ok(dev_obj) => match conn
                                .call_method(
                                    Some(BLUEZ_BUS_NAME),
                                    adapter_path.as_str(),
                                    Some(BLUEZ_ADAPTER_IFACE),
                                    "RemoveDevice",
                                    &(dev_obj,),
                                )
                                .await
                            {
                                Ok(_) => DeviceOpOutcome::Succeeded,
                                Err(err) => {
                                    let raw = err.to_string();
                                    if is_canceled_bluez_error(&raw) {
                                        DeviceOpOutcome::Canceled
                                    } else {
                                        DeviceOpOutcome::Failed(translate_bluez_error(&raw))
                                    }
                                }
                            },
                            Err(err) => DeviceOpOutcome::Failed(err.to_string()),
                        };
                        let _ = evt_tx.unbounded_send(BackendEvent::DeviceOpFinished {
                            path: device_path,
                            op: DeviceOp::Removing,
                            outcome,
                        });
                    },
                    "bluez-remove-device",
                )
                .detach();
        }
        BackendCommand::Shutdown(_) => {}
    }
}

fn spawn_device_method(
    conn: &Connection,
    device_path: String,
    method: &'static str,
    op: DeviceOp,
    evt_tx: &UnboundedSender<BackendEvent>,
) {
    let executor = conn.executor();
    let conn = conn.clone();
    let evt_tx = evt_tx.clone();
    executor
        .spawn(
            async move {
                let outcome = match conn
                    .call_method(
                        Some(BLUEZ_BUS_NAME),
                        device_path.as_str(),
                        Some(BLUEZ_DEVICE_IFACE),
                        method,
                        &(),
                    )
                    .await
                {
                    Ok(_) => DeviceOpOutcome::Succeeded,
                    Err(err) => {
                        let raw = err.to_string();
                        if method == "Pair" && raw.contains("AlreadyExists") {
                            DeviceOpOutcome::Succeeded
                        } else if is_canceled_bluez_error(&raw) {
                            DeviceOpOutcome::Canceled
                        } else {
                            DeviceOpOutcome::Failed(translate_bluez_error(&raw))
                        }
                    }
                };
                let _ = evt_tx.unbounded_send(BackendEvent::DeviceOpFinished {
                    path: device_path,
                    op,
                    outcome,
                });
            },
            "bluez-device-op",
        )
        .detach();
}

async fn set_dbus_property(
    conn: &Connection,
    path: &str,
    iface: &str,
    prop_name: &str,
    value: Value<'_>,
) -> zbus::Result<()> {
    conn.call_method(
        Some(BLUEZ_BUS_NAME),
        path,
        Some(PROPERTIES_IFACE),
        "Set",
        &(iface, prop_name, value),
    )
    .await
    .map(|_| ())
}

fn unwrap_value<'a>(mut value: &'a Value<'a>) -> &'a Value<'a> {
    while let Value::Value(inner) = value {
        value = inner;
    }
    value
}

fn prop_str(props: &PropertyMap, key: &str) -> Option<String> {
    match unwrap_value(props.get(key)?) {
        Value::Str(s) => Some(s.as_str().to_string()),
        Value::ObjectPath(p) => Some(p.as_str().to_string()),
        _ => None,
    }
}

fn prop_bool(props: &PropertyMap, key: &str) -> Option<bool> {
    match unwrap_value(props.get(key)?) {
        Value::Bool(b) => Some(*b),
        _ => None,
    }
}

fn prop_u8(props: &PropertyMap, key: &str) -> Option<u8> {
    match unwrap_value(props.get(key)?) {
        Value::U8(v) => Some(*v),
        _ => None,
    }
}

fn prop_i16(props: &PropertyMap, key: &str) -> Option<i16> {
    match unwrap_value(props.get(key)?) {
        Value::I16(v) => Some(*v),
        _ => None,
    }
}

fn prop_u16(props: &PropertyMap, key: &str) -> Option<u16> {
    match unwrap_value(props.get(key)?) {
        Value::U16(v) => Some(*v),
        _ => None,
    }
}

fn prop_u32(props: &PropertyMap, key: &str) -> Option<u32> {
    match unwrap_value(props.get(key)?) {
        Value::U32(v) => Some(*v),
        _ => None,
    }
}

fn prop_string_vec(props: &PropertyMap, key: &str) -> Option<Vec<String>> {
    match unwrap_value(props.get(key)?) {
        Value::Array(arr) => Some(
            arr.iter()
                .filter_map(|item| match unwrap_value(item) {
                    Value::Str(s) => Some(s.as_str().to_string()),
                    _ => None,
                })
                .collect(),
        ),
        _ => None,
    }
}

fn parent_adapter_path(device_path: &str) -> String {
    match device_path.rsplit_once('/') {
        Some((parent, _)) if !parent.is_empty() => parent.to_string(),
        _ => "/org/bluez/hci0".to_string(),
    }
}

fn mac_from_object_path(path: &str) -> Option<String> {
    let tail = path.rsplit('/').next()?;
    let raw = tail.strip_prefix("dev_")?;
    let parts: Vec<&str> = raw.split('_').collect();
    if parts.len() == 6
        && parts
            .iter()
            .all(|p| p.len() == 2 && p.bytes().all(|b| b.is_ascii_hexdigit()))
    {
        Some(parts.join(":").to_ascii_uppercase())
    } else {
        None
    }
}

fn decode_adapter(path: &str, props: &PropertyMap) -> AdapterState {
    let mut adapter = AdapterState {
        path: path.to_string(),
        address: String::new(),
        name: String::new(),
        alias: String::new(),
        powered: false,
        discoverable: false,
        discoverable_timeout: 180,
        discovering: false,
        pairable: true,
    };
    apply_adapter_properties(&mut adapter, props);
    adapter
}

fn apply_adapter_properties(adapter: &mut AdapterState, props: &PropertyMap) {
    if let Some(v) = prop_str(props, "Address") {
        adapter.address = v;
    }
    if let Some(v) = prop_str(props, "Name") {
        adapter.name = v;
    }
    if let Some(v) = prop_str(props, "Alias") {
        adapter.alias = v;
    }
    if let Some(v) = prop_bool(props, "Powered") {
        adapter.powered = v;
    }
    if let Some(v) = prop_bool(props, "Discoverable") {
        adapter.discoverable = v;
    }
    if let Some(v) = prop_u32(props, "DiscoverableTimeout") {
        adapter.discoverable_timeout = v;
    }
    if let Some(v) = prop_bool(props, "Discovering") {
        adapter.discovering = v;
    }
    if let Some(v) = prop_bool(props, "Pairable") {
        adapter.pairable = v;
    }
}

fn decode_device(path: &str, props: &PropertyMap, battery: Option<u8>) -> DeviceState {
    let mut device = DeviceState {
        path: path.to_string(),
        adapter_path: prop_str(props, "Adapter").unwrap_or_else(|| parent_adapter_path(path)),
        address: prop_str(props, "Address")
            .or_else(|| mac_from_object_path(path))
            .unwrap_or_default(),
        name: None,
        alias: String::new(),
        icon_hint: None,
        class: 0,
        appearance: 0,
        uuids: Vec::new(),
        paired: false,
        connected: false,
        trusted: false,
        blocked: false,
        battery,
        rssi: None,
        kind: DeviceKind::Generic,
        in_flight: None,
    };
    let _ = apply_device_properties(&mut device, props, &[]);
    device
}

fn apply_device_properties(
    device: &mut DeviceState,
    changed: &PropertyMap,
    invalidated: &[String],
) -> (Option<bool>, Option<bool>) {
    if let Some(v) = prop_str(changed, "Adapter") {
        device.adapter_path = v;
    }
    if let Some(v) = prop_str(changed, "Address") {
        device.address = v;
    }
    if let Some(v) = prop_str(changed, "Name") {
        device.name = (!v.trim().is_empty()).then_some(v);
    }
    if let Some(v) = prop_str(changed, "Alias") {
        device.alias = v;
    }
    if let Some(v) = prop_str(changed, "Icon") {
        device.icon_hint = (!v.trim().is_empty()).then_some(v);
    }
    if let Some(v) = prop_u32(changed, "Class") {
        device.class = v;
    }
    if let Some(v) = prop_u16(changed, "Appearance") {
        device.appearance = v;
    }
    if let Some(v) = prop_string_vec(changed, "UUIDs") {
        device.uuids = v;
    }

    let paired_change = prop_bool(changed, "Paired");
    if let Some(v) = paired_change {
        device.paired = v;
    }

    let connected_change = prop_bool(changed, "Connected");
    if let Some(v) = connected_change {
        device.connected = v;
    }

    if let Some(v) = prop_bool(changed, "Trusted") {
        device.trusted = v;
    }
    if let Some(v) = prop_bool(changed, "Blocked") {
        device.blocked = v;
    }
    if let Some(v) = prop_i16(changed, "RSSI") {
        device.rssi = valid_rssi(v);
    }

    for key in invalidated {
        match key.as_str() {
            "RSSI" => device.rssi = None,
            "Name" => device.name = None,
            "Icon" => device.icon_hint = None,
            "Class" => device.class = 0,
            "Appearance" => device.appearance = 0,
            "UUIDs" => device.uuids.clear(),
            _ => {}
        }
    }

    device.refresh_kind();
    (paired_change, connected_change)
}

#[cfg(test)]
mod tests {
    use super::*;
    use zbus::zvariant::Array;

    fn owned(val: Value<'_>) -> OwnedValue {
        val.try_into().unwrap()
    }

    #[test]
    fn mac_and_parent_adapter_extract_cleanly_from_bluez_paths() {
        let path = "/org/bluez/hci1/dev_aa_bb_cc_dd_ee_ff";
        assert_eq!(
            mac_from_object_path(path).as_deref(),
            Some("AA:BB:CC:DD:EE:FF")
        );
        assert_eq!(parent_adapter_path(path), "/org/bluez/hci1");
        assert_eq!(mac_from_object_path("/org/bluez/hci0"), None);
    }

    #[test]
    fn decode_and_invalidate_device_properties_handles_rssi_and_classification() {
        let mut props = PropertyMap::new();
        props.insert("Address".into(), owned(Value::from("11:22:33:44:55:66")));
        props.insert("Alias".into(), owned(Value::from("WH-1000XM5")));
        props.insert("Icon".into(), owned(Value::from("audio-headphones")));
        props.insert("Paired".into(), owned(Value::from(true)));
        props.insert("Connected".into(), owned(Value::from(false)));
        props.insert("RSSI".into(), owned(Value::from(-58_i16)));
        let uuids = Array::from(vec!["0000110b-0000-1000-8000-00805f9b34fb"]);
        props.insert("UUIDs".into(), owned(Value::Array(uuids)));

        let mut dev = decode_device("/org/bluez/hci0/dev_11_22_33_44_55_66", &props, Some(85));
        assert_eq!(dev.adapter_path, "/org/bluez/hci0");
        assert_eq!(dev.kind, DeviceKind::Headphones);
        assert_eq!(dev.battery, Some(85));
        assert_eq!(dev.rssi, Some(-58));
        assert!(dev.is_audio_device());

        let mut changed = PropertyMap::new();
        changed.insert("Connected".into(), owned(Value::from(true)));
        let invalidated = vec!["RSSI".to_string()];
        let (paired_chg, conn_chg) = apply_device_properties(&mut dev, &changed, &invalidated);
        assert_eq!(paired_chg, None);
        assert_eq!(conn_chg, Some(true));
        assert!(dev.connected);
        assert_eq!(dev.rssi, None);

        // Sentinel RSSI values (127 / 153) must decode to None.
        let mut bad_rssi = PropertyMap::new();
        bad_rssi.insert("RSSI".into(), owned(Value::from(127_i16)));
        let _ = apply_device_properties(&mut dev, &bad_rssi, &[]);
        assert_eq!(dev.rssi, None);

        bad_rssi.insert("RSSI".into(), owned(Value::from(153_i16)));
        let _ = apply_device_properties(&mut dev, &bad_rssi, &[]);
        assert_eq!(dev.rssi, None);
    }

    #[test]
    fn decode_adapter_reads_power_discoverable_and_scanning_flags() {
        let mut props = PropertyMap::new();
        props.insert("Address".into(), owned(Value::from("AA:00:11:22:33:44")));
        props.insert("Alias".into(), owned(Value::from("dusk-host")));
        props.insert("Powered".into(), owned(Value::from(true)));
        props.insert("Discoverable".into(), owned(Value::from(true)));
        props.insert("Discovering".into(), owned(Value::from(true)));
        props.insert("DiscoverableTimeout".into(), owned(Value::from(120_u32)));

        let adapter = decode_adapter("/org/bluez/hci0", &props);
        assert_eq!(adapter.display_name(), "dusk-host");
        assert!(adapter.powered);
        assert!(adapter.discoverable);
        assert!(adapter.discovering);
        assert_eq!(adapter.discoverable_timeout, 120);
    }
}
