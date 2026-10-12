use crate::config::{Config, mac_to_dbus_suffix};
use futures_util::{
    StreamExt,
    stream::{Select, select},
};
use std::{collections::HashMap, fs::File, io, path::PathBuf, time::Duration};
use zbus::{
    MatchRule, MessageStream,
    connection::Builder,
    proxy::{Builder as ProxyBuilder, CacheProperties, Proxy},
    zvariant::{OwnedObjectPath, OwnedValue, Value},
};

pub type Properties = HashMap<String, OwnedValue>;
type Interfaces = HashMap<String, Properties>;
type ManagedObjects = HashMap<OwnedObjectPath, Interfaces>;
// Discovery replies include controller work; allow BlueZ's five-second scan
// transition to finish without imposing this delay on successful calls.
const BLUEZ_METHOD_TIMEOUT: Duration = Duration::from_secs(10);

// BlueZ 5.87 keeps a single pending discovery reply per adapter. Serialize our
// filter/start/stop transactions across daemon, scan and test processes.
async fn discovery_lock() -> io::Result<File> {
    let directory = std::env::var_os("XDG_RUNTIME_DIR")
        .filter(|p| !p.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    // SAFETY: geteuid has no arguments or preconditions.
    let uid = unsafe { libc::geteuid() };
    lock_file(directory.join(format!("dusky-proximity-discovery-{uid}.lock"))).await
}

async fn lock_file(path: PathBuf) -> io::Result<File> {
    let file = File::options()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(path)?;
    tokio::time::timeout(Duration::from_secs(30), async {
        loop {
            match file.try_lock() {
                Ok(()) => return Ok(file),
                Err(std::fs::TryLockError::WouldBlock) => {
                    tokio::time::sleep(Duration::from_millis(25)).await;
                }
                Err(std::fs::TryLockError::Error(e)) => return Err(e),
            }
        }
    })
    .await
    .map_err(|_| {
        io::Error::new(
            io::ErrorKind::TimedOut,
            "waiting for Bluetooth discovery transaction",
        )
    })?
}

#[derive(Debug)]
pub struct DiscoveredDevice {
    pub mac: String,
    pub name: String,
    pub rssi: Option<i16>,
    pub paired: bool,
    pub connected: bool,
}
pub struct BluetoothWatcher {
    conn: zbus::Connection,
    pub adapter_path: String,
    stream: Select<MessageStream, MessageStream>,
    discovery_enabled: bool,
    pub powered: bool,
    discovery_filter: HashMap<&'static str, OwnedValue>,
    discovery_owned: bool,
    discovery_inactive_since: Option<tokio::time::Instant>,
    baseline_position: zbus::message::Sequence,
    pub initial_connections: HashMap<String, bool>,
}
async fn objects(conn: &zbus::Connection) -> Result<ManagedObjects, zbus::Error> {
    Proxy::new(conn, "org.bluez", "/", "org.freedesktop.DBus.ObjectManager")
        .await?
        .call("GetManagedObjects", &())
        .await
}
fn select_adapter(
    managed: &ManagedObjects,
    config: &Config,
) -> Result<String, Box<dyn std::error::Error>> {
    let targets = config.active_devices();
    let mut candidates: Vec<_> = managed
        .iter()
        .filter_map(|(path, ifaces)| {
            let props = ifaces.get("org.bluez.Adapter1")?;
            let powered = props
                .get("Powered")
                .and_then(|v| bool::try_from(v).ok())
                .unwrap_or(false);
            let known = targets.iter().any(|d| {
                managed.contains_key(
                    &OwnedObjectPath::try_from(format!("{path}/{}", mac_to_dbus_suffix(&d.mac)))
                        .expect("valid generated device path"),
                )
            });
            Some((path.to_string(), powered, known))
        })
        .collect();
    if !config.adapter_path.is_empty() {
        return candidates
            .into_iter()
            .find(|(p, _, _)| p == &config.adapter_path)
            .map(|(p, _, _)| p)
            .ok_or_else(|| format!("Bluetooth adapter not found: {}", config.adapter_path).into());
    }
    let prefer_powered = config.has_proximity_devices() || targets.is_empty();
    candidates.sort_by(|a, b| {
        let priority = if prefer_powered {
            b.1.cmp(&a.1).then_with(|| b.2.cmp(&a.2))
        } else {
            b.2.cmp(&a.2).then_with(|| b.1.cmp(&a.1))
        };
        priority.then_with(|| a.0.cmp(&b.0))
    });
    candidates
        .into_iter()
        .next()
        .map(|(p, _, _)| p)
        .ok_or_else(|| "no Bluetooth adapter found".into())
}
impl BluetoothWatcher {
    pub async fn new(config: &Config) -> Result<Self, Box<dyn std::error::Error>> {
        let conn = Builder::system()?
            .method_timeout(BLUEZ_METHOD_TIMEOUT)
            .build()
            .await?;
        Self::with_connection(conn, config).await
    }
    async fn with_connection(
        conn: zbus::Connection,
        config: &Config,
    ) -> Result<Self, Box<dyn std::error::Error>> {
        let adapter_path = select_adapter(
            &objects(&conn)
                .await
                .map_err(|e| format!("Reading Bluetooth adapters: {e}"))?,
            config,
        )?;
        // Subscribe before discovery starts; capture InterfacesAdded as well as PropertiesChanged.
        let rule = MatchRule::builder()
            .msg_type(zbus::message::Type::Signal)
            .sender("org.bluez")?
            .build();
        let updates = MessageStream::for_match_rule(rule, &conn, Some(1024)).await?;
        let owner_rule = MatchRule::builder()
            .msg_type(zbus::message::Type::Signal)
            .sender("org.freedesktop.DBus")?
            .interface("org.freedesktop.DBus")?
            .member("NameOwnerChanged")?
            .add_arg("org.bluez")?
            .build();
        let owners = MessageStream::for_match_rule(owner_rule, &conn, Some(8)).await?;
        let stream = select(updates, owners);
        let snapshot = conn
            .call_method(
                Some("org.bluez"),
                "/",
                Some("org.freedesktop.DBus.ObjectManager"),
                "GetManagedObjects",
                &(),
            )
            .await
            .map_err(|e| format!("Reading Bluetooth connection baseline: {e}"))?;
        let baseline_position = snapshot.recv_position();
        let initial_connections = snapshot
            .body()
            .deserialize::<ManagedObjects>()?
            .into_iter()
            .filter_map(|(path, ifaces)| {
                let key = path
                    .as_str()
                    .strip_prefix(&format!("{adapter_path}/"))?
                    .to_owned();
                let connected = ifaces
                    .get("org.bluez.Device1")?
                    .get("Connected")
                    .and_then(|v| bool::try_from(v).ok())?;
                Some((key, connected))
            })
            .collect();
        let discovery_enabled = config.has_proximity_devices()
            || (config.proximity_enabled && config.active_devices().is_empty());
        let mut filter = HashMap::new();
        if discovery_enabled {
            filter = HashMap::from([
                ("DuplicateData", OwnedValue::from(true)),
                (
                    "Transport",
                    Value::from(config.transport.as_str()).try_to_owned()?,
                ),
            ]);
            let targets: Vec<_> = config
                .active_devices()
                .into_iter()
                .filter(|d| d.proximity)
                .collect();
            // BlueZ may merge other clients' filters; still filter exact paths in our event loop.
            if targets.len() == 1 {
                filter.insert(
                    "Pattern",
                    Value::from(targets[0].mac.to_ascii_uppercase()).try_to_owned()?,
                );
            }
        }
        let mut watcher = Self {
            conn,
            adapter_path,
            stream,
            discovery_enabled,
            powered: false,
            discovery_filter: filter,
            discovery_owned: false,
            discovery_inactive_since: None,
            baseline_position,
            initial_connections,
        };
        watcher.ensure_discovery().await?;
        Ok(watcher)
    }
    pub async fn next_message(&mut self) -> Result<zbus::Message, Box<dyn std::error::Error>> {
        loop {
            let msg = self
                .stream
                .next()
                .await
                .ok_or("Bluetooth D-Bus stream closed")??;
            if msg
                .header()
                .interface()
                .is_some_and(|i| i.as_str() == "org.freedesktop.DBus")
            {
                return Err("BlueZ owner changed; reconnecting observation session".into());
            }
            // The snapshot already includes earlier changes. Replaying them would
            // manufacture connection events and treat startup RSSI as fresh reception.
            if msg.recv_position() <= self.baseline_position {
                continue;
            }
            if msg
                .header()
                .member()
                .is_some_and(|m| m.as_str() == "InterfacesRemoved")
                && let Ok((path, ifaces)) =
                    msg.body().deserialize::<(OwnedObjectPath, Vec<String>)>()
                && path.as_str() == self.adapter_path
                && ifaces.iter().any(|i| i == "org.bluez.Adapter1")
            {
                return Err("Bluetooth adapter removed; reconnecting observation session".into());
            }
            return Ok(msg);
        }
    }
    /// Detect daemon/controller loss as an observation failure, not device departure.
    /// True means discovery was interrupted/pending: discard stale measurement evidence.
    pub async fn ensure_discovery(&mut self) -> Result<bool, Box<dyn std::error::Error>> {
        let adapter: Proxy<'_> = ProxyBuilder::new(&self.conn)
            .destination("org.bluez")?
            .path(self.adapter_path.as_str())?
            .interface("org.bluez.Adapter1")?
            .cache_properties(CacheProperties::No)
            .build()
            .await?;
        let powered = adapter
            .get_property::<bool>("Powered")
            .await
            .map_err(|e| format!("Reading Bluetooth adapter power: {e}"))?;
        let changed = self.powered != powered;
        self.powered = powered;
        if !powered {
            self.discovery_owned = false;
            self.discovery_inactive_since = None;
            return Ok(changed && self.discovery_enabled);
        }
        if !self.discovery_enabled {
            return Ok(false);
        }
        let discovering = adapter
            .get_property::<bool>("Discovering")
            .await
            .map_err(|e| format!("Reading Bluetooth discovery state: {e}"))?;
        if self.discovery_owned && !discovering {
            // Other clients' filter changes can temporarily stop the controller.
            // Let BlueZ finish its pending operation before forcing reacquisition.
            let since = self
                .discovery_inactive_since
                .get_or_insert_with(tokio::time::Instant::now);
            if since.elapsed() < Duration::from_secs(5) {
                return Ok(true);
            }
        } else {
            self.discovery_inactive_since = None;
        }
        if !self.discovery_owned || !discovering {
            let _transaction = discovery_lock().await?;
            adapter
                .call::<_, _, ()>("SetDiscoveryFilter", &(&self.discovery_filter,))
                .await
                .map_err(|e| format!("Setting Bluetooth discovery filter: {e}"))?;
            match adapter.call::<_, _, ()>("StartDiscovery", &()).await {
                Ok(()) => {
                    eprintln!("[bluetooth] Discovery resumed");
                    self.discovery_owned = true;
                    self.discovery_inactive_since = None;
                    return Ok(true);
                }
                // BlueZ still owns our session but scanning is inactive. Release and
                // reacquire only our session; other clients' discovery is unaffected.
                Err(zbus::Error::MethodError(name, _, _))
                    if name.as_str() == "org.bluez.Error.InProgress" =>
                {
                    adapter
                        .call::<_, _, ()>("StopDiscovery", &())
                        .await
                        .map_err(|e| format!("Releasing inactive Bluetooth discovery: {e}"))?;
                    adapter
                        .call::<_, _, ()>("StartDiscovery", &())
                        .await
                        .map_err(|e| format!("Reacquiring Bluetooth discovery: {e}"))?;
                    eprintln!("[bluetooth] Reacquired inactive discovery session");
                    self.discovery_owned = true;
                    self.discovery_inactive_since = None;
                    return Ok(true);
                }
                Err(e) => return Err(format!("Starting Bluetooth discovery: {e}").into()),
            }
        }
        Ok(false)
    }
    pub async fn stop_discovery(&mut self) {
        if !self.discovery_owned {
            return;
        }
        let _transaction = match discovery_lock().await {
            Ok(lock) => lock,
            Err(e) => {
                eprintln!("[bluetooth] Discovery shutdown lock: {e}");
                return;
            }
        };
        if let Ok(adapter) = Proxy::new(
            &self.conn,
            "org.bluez",
            self.adapter_path.as_str(),
            "org.bluez.Adapter1",
        )
        .await
        {
            match adapter.call::<_, _, ()>("StopDiscovery", &()).await {
                Ok(()) => {
                    self.discovery_owned = false;
                    self.discovery_inactive_since = None;
                }
                Err(e) => eprintln!("[bluetooth] StopDiscovery: {e}"),
            }
        }
    }
}
/// Decode only the documented signal shapes. New device objects contain initial live RSSI.
pub fn device_update(msg: &zbus::Message) -> Option<(String, String, Properties)> {
    let header = msg.header();
    match (header.interface()?.as_str(), header.member()?.as_str()) {
        ("org.freedesktop.DBus.Properties", "PropertiesChanged") => {
            let (iface, props, _): (String, Properties, Vec<String>) =
                msg.body().deserialize().ok()?;
            Some((header.path()?.to_string(), iface, props))
        }
        ("org.freedesktop.DBus.ObjectManager", "InterfacesAdded") => {
            let (path, mut ifaces): (OwnedObjectPath, Interfaces) =
                msg.body().deserialize().ok()?;
            Some((
                path.to_string(),
                "org.bluez.Device1".into(),
                ifaces.remove("org.bluez.Device1")?,
            ))
        }
        ("org.freedesktop.DBus.ObjectManager", "InterfacesRemoved") => {
            let (path, ifaces): (OwnedObjectPath, Vec<String>) = msg.body().deserialize().ok()?;
            if !ifaces.iter().any(|i| i == "org.bluez.Device1") {
                return None;
            }
            Some((
                path.to_string(),
                "org.bluez.Device1".into(),
                HashMap::from([("Connected".into(), OwnedValue::from(false))]),
            ))
        }
        _ => None,
    }
}
pub async fn scan_available_devices(
    config: &Config,
    duration: Duration,
) -> Result<Vec<DiscoveredDevice>, Box<dyn std::error::Error>> {
    let mut watcher = BluetoothWatcher::new(config).await?;
    if !watcher.powered {
        return Err("Bluetooth radio is off; power it on before scanning".into());
    }
    let deadline = tokio::time::Instant::now() + duration;
    let mut live_rssi = HashMap::new();
    while let Ok(msg) = tokio::time::timeout_at(deadline, watcher.next_message()).await {
        let msg = msg?;
        if let Some((path, iface, props)) = device_update(&msg)
            && iface == "org.bluez.Device1"
            && path.starts_with(&format!("{}/dev_", watcher.adapter_path))
            && let Some(rssi) = props.get("RSSI").and_then(|v| i16::try_from(v).ok())
            && (-127..=20).contains(&rssi)
        {
            live_rssi.insert(path, rssi);
        }
    }
    let snapshot = objects(&watcher.conn).await;
    watcher.stop_discovery().await;
    let mut result = Vec::new();
    for (path, mut ifaces) in snapshot? {
        if !path
            .as_str()
            .starts_with(&format!("{}/dev_", watcher.adapter_path))
        {
            continue;
        }
        let Some(props) = ifaces.remove("org.bluez.Device1") else {
            continue;
        };
        let string = |key| {
            props
                .get(key)
                .and_then(|v| <&str>::try_from(v).ok())
                .map(str::to_owned)
        };
        if let Some(mac) = string("Address") {
            result.push(DiscoveredDevice {
                mac,
                name: string("Alias")
                    .or_else(|| string("Name"))
                    .unwrap_or_else(|| "Unknown".into()),
                rssi: live_rssi.get(path.as_str()).copied(),
                paired: props
                    .get("Paired")
                    .and_then(|v| bool::try_from(v).ok())
                    .unwrap_or(false),
                connected: props
                    .get("Connected")
                    .and_then(|v| bool::try_from(v).ok())
                    .unwrap_or(false),
            });
        }
    }
    result.sort_by(|a, b| {
        b.paired
            .cmp(&a.paired)
            .then_with(|| b.rssi.cmp(&a.rssi))
            .then_with(|| a.mac.cmp(&b.mac))
    });
    Ok(result)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn discovery_transactions_wait_and_release_on_drop() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("discovery.lock");
        let first = lock_file(path.clone()).await.unwrap();
        assert!(
            tokio::time::timeout(Duration::from_millis(100), lock_file(path.clone()))
                .await
                .is_err()
        );
        drop(first);
        let second = lock_file(path.clone()).await.unwrap();
        drop(second);
        // Cancellation of a waiter must not remove the shared inode or retain a lock.
        assert!(path.exists());
        assert!(lock_file(path).await.is_ok());
    }
    #[test]
    fn adapter_selection_uses_power_and_known_targets() {
        let c = Config {
            device_mac: "AA:BB:CC:DD:EE:FF".into(),
            ..Config::default()
        };
        let mut objects = ManagedObjects::new();
        for (p, powered) in [
            ("/org/bluez/hci0", false),
            ("/org/bluez/hci1", true),
            ("/org/bluez/hci2", true),
        ] {
            objects.insert(
                OwnedObjectPath::try_from(p).unwrap(),
                HashMap::from([(
                    "org.bluez.Adapter1".into(),
                    HashMap::from([("Powered".into(), OwnedValue::from(powered))]),
                )]),
            );
        }
        objects.insert(
            OwnedObjectPath::try_from("/org/bluez/hci2/dev_AA_BB_CC_DD_EE_FF").unwrap(),
            HashMap::new(),
        );
        assert_eq!(select_adapter(&objects, &c).unwrap(), "/org/bluez/hci2");
    }
}

#[cfg(test)]
mod integration_tests {
    use super::*;
    use std::{
        io::BufRead,
        process::{Child, Command, Stdio},
        sync::{
            Arc,
            atomic::{AtomicBool, AtomicUsize, Ordering},
        },
    };
    struct PrivateBus(Child);
    impl Drop for PrivateBus {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }
    #[derive(Default)]
    struct ScanState {
        powered: AtomicBool,
        discovering: AtomicBool,
        owned: AtomicBool,
        starts: AtomicUsize,
        stops: AtomicUsize,
        power_reads: AtomicUsize,
        filters: AtomicUsize,
        start_delay_ms: AtomicUsize,
    }
    struct Adapter(Arc<ScanState>);
    #[derive(Debug, zbus::DBusError)]
    #[zbus(prefix = "org.bluez.Error")]
    enum ScanError {
        InProgress(String),
    }
    #[zbus::interface(name = "org.bluez.Adapter1")]
    impl Adapter {
        fn set_discovery_filter(&self, _filter: Properties) {
            self.0.filters.fetch_add(1, Ordering::SeqCst);
        }
        async fn start_discovery(&self) -> Result<(), ScanError> {
            tokio::time::sleep(Duration::from_millis(
                self.0.start_delay_ms.load(Ordering::SeqCst) as u64,
            ))
            .await;
            if self.0.owned.swap(true, Ordering::SeqCst) {
                return Err(ScanError::InProgress("already owned".into()));
            }
            self.0.starts.fetch_add(1, Ordering::SeqCst);
            self.0.discovering.store(true, Ordering::SeqCst);
            Ok(())
        }
        fn stop_discovery(&self) {
            self.0.owned.store(false, Ordering::SeqCst);
            self.0.discovering.store(false, Ordering::SeqCst);
            self.0.stops.fetch_add(1, Ordering::SeqCst);
        }
        #[zbus(property)]
        fn powered(&self) -> bool {
            self.0.power_reads.fetch_add(1, Ordering::SeqCst);
            self.0.powered.load(Ordering::SeqCst)
        }
        #[zbus(property)]
        fn discovering(&self) -> bool {
            self.0.discovering.load(Ordering::SeqCst)
        }
    }
    struct Device(AtomicUsize);
    #[zbus::interface(name = "org.bluez.Device1")]
    impl Device {
        #[zbus(property)]
        async fn connected(&self, #[zbus(connection)] conn: &zbus::Connection) -> bool {
            if self.0.fetch_add(1, Ordering::SeqCst) == 1 {
                // Arrives after subscription but before the second snapshot reply.
                // The final snapshot already records the newer connected baseline.
                conn.emit_signal(
                    None::<&str>,
                    "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
                    "org.freedesktop.DBus.Properties",
                    "PropertiesChanged",
                    &(
                        "org.bluez.Device1",
                        HashMap::from([("Connected", OwnedValue::from(false))]),
                        Vec::<String>::new(),
                    ),
                )
                .await
                .unwrap();
            }
            true
        }
        #[zbus(property)]
        fn rssi(&self) -> i16 {
            -40
        }
    }
    #[tokio::test]
    async fn radio_off_sleeps_without_polling_and_recovers_on_power_event() {
        let mut child = Command::new("dbus-daemon")
            .args(["--session", "--nofork", "--print-address=1", "--nosyslog"])
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        let mut address = String::new();
        std::io::BufReader::new(child.stdout.take().unwrap())
            .read_line(&mut address)
            .unwrap();
        let _bus = PrivateBus(child);
        tokio::time::timeout(Duration::from_secs(20), async {
            let state = Arc::new(ScanState::default());
            let server = Builder::address(address.trim())
                .unwrap()
                .name("org.bluez")
                .unwrap()
                .serve_at("/", zbus::fdo::ObjectManager)
                .unwrap()
                .serve_at("/org/bluez/hci0", Adapter(state.clone()))
                .unwrap()
                .serve_at(
                    "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
                    Device(AtomicUsize::new(0)),
                )
                .unwrap()
                .build()
                .await
                .unwrap();
            let client = Builder::address(address.trim())
                .unwrap()
                .build()
                .await
                .unwrap();
            let tmp = tempfile::tempdir().unwrap();
            let marker = tmp.path().join("unexpected-departure");
            let disconnected = tmp.path().join("disconnected");
            let c = Config {
                device_mac: "AA:BB:CC:DD:EE:FF".into(),
                notifications: false,
                signal_timeout_secs: 1,
                lock_hold_secs: 0,
                startup_grace_secs: 0,
                disconnect_hold_secs: 1,
                on_disconnect: crate::config::CommandList::Single(format!(
                    "touch '{}'",
                    disconnected.display()
                )),
                on_lock: crate::config::CommandList::Single(format!(
                    "touch '{}'",
                    marker.display()
                )),
                ..Config::default()
            };
            let watcher = BluetoothWatcher::with_connection(client, &c).await.unwrap();
            assert!(!watcher.powered);
            assert_eq!(state.starts.load(Ordering::SeqCst), 0);
            assert_eq!(state.filters.load(Ordering::SeqCst), 0);
            let reads = state.power_reads.load(Ordering::SeqCst);
            let control = async {
                // Longer than the health interval and the configured silence timeout.
                tokio::time::sleep(Duration::from_secs(6)).await;
                assert_eq!(state.power_reads.load(Ordering::SeqCst), reads);
                assert!(
                    !marker.exists(),
                    "radio-off silence must never run departure hooks"
                );
                server
                    .emit_signal(
                        None::<&str>,
                        "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
                        "org.freedesktop.DBus.Properties",
                        "PropertiesChanged",
                        &(
                            "org.bluez.Device1",
                            HashMap::from([("Connected", OwnedValue::from(false))]),
                            Vec::<String>::new(),
                        ),
                    )
                    .await
                    .unwrap();
                tokio::time::timeout(Duration::from_secs(3), async {
                    while !disconnected.exists() {
                        tokio::time::sleep(Duration::from_millis(5)).await;
                    }
                })
                .await
                .unwrap();
                assert_eq!(state.power_reads.load(Ordering::SeqCst), reads);
                state.powered.store(true, Ordering::SeqCst);
                server
                    .emit_signal(
                        None::<&str>,
                        "/org/bluez/hci0",
                        "org.freedesktop.DBus.Properties",
                        "PropertiesChanged",
                        &(
                            "org.bluez.Adapter1",
                            HashMap::from([("Powered", OwnedValue::from(true))]),
                            Vec::<String>::new(),
                        ),
                    )
                    .await
                    .unwrap();
                tokio::time::timeout(Duration::from_secs(2), async {
                    while state.starts.load(Ordering::SeqCst) == 0 {
                        tokio::time::sleep(Duration::from_millis(5)).await;
                    }
                })
                .await
                .unwrap();
                assert_eq!(state.filters.load(Ordering::SeqCst), 1);
                // Exercise more updates than fit in the signal queue while the
                // observer's health checks and timer branches are also active.
                for _ in 0..4096 {
                    server
                        .emit_signal(
                            None::<&str>,
                            "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
                            "org.freedesktop.DBus.Properties",
                            "PropertiesChanged",
                            &(
                                "org.bluez.Device1",
                                HashMap::from([("RSSI", OwnedValue::from(-40i16))]),
                                Vec::<String>::new(),
                            ),
                        )
                        .await
                        .unwrap();
                }
                state.powered.store(false, Ordering::SeqCst);
                server
                    .emit_signal(
                        None::<&str>,
                        "/org/bluez/hci0",
                        "org.freedesktop.DBus.Properties",
                        "PropertiesChanged",
                        &(
                            "org.bluez.Adapter1",
                            HashMap::from([("Powered", OwnedValue::from(false))]),
                            Vec::<String>::new(),
                        ),
                    )
                    .await
                    .unwrap();
                // Allow the power-off event and one already-armed timer to settle.
                tokio::time::sleep(Duration::from_millis(50)).await;
                let sleeping_reads = state.power_reads.load(Ordering::SeqCst);
                tokio::time::sleep(Duration::from_secs(6)).await;
                assert_eq!(state.power_reads.load(Ordering::SeqCst), sleeping_reads);
                assert!(!marker.exists());
                // Removal terminates observation so systemd can select a replacement.
                server
                    .emit_signal(
                        None::<&str>,
                        "/",
                        "org.freedesktop.DBus.ObjectManager",
                        "InterfacesRemoved",
                        &(
                            OwnedObjectPath::try_from("/org/bluez/hci0").unwrap(),
                            vec!["org.bluez.Adapter1"],
                        ),
                    )
                    .await
                    .unwrap();
            };
            let (result, ()) = tokio::join!(crate::observe(watcher, &c, false), control);
            assert!(result.unwrap_err().to_string().contains("adapter removed"));
            assert!(!marker.exists());
        })
        .await
        .unwrap();
    }
    #[tokio::test]
    async fn connection_only_observation_does_not_scan_or_treat_cache_as_live() {
        let mut child = Command::new("dbus-daemon")
            .args(["--session", "--nofork", "--print-address=1", "--nosyslog"])
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        let mut address = String::new();
        std::io::BufReader::new(child.stdout.take().unwrap())
            .read_line(&mut address)
            .unwrap();
        let _bus = PrivateBus(child);
        tokio::time::timeout(Duration::from_secs(10), async {
            let state = Arc::new(ScanState::default());
            state.powered.store(true, Ordering::SeqCst);
            let path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF";
            let server = Builder::address(address.trim())
                .unwrap()
                .name("org.bluez")
                .unwrap()
                .serve_at("/", zbus::fdo::ObjectManager)
                .unwrap()
                .serve_at("/org/bluez/hci0", Adapter(state.clone()))
                .unwrap()
                .serve_at(path, Device(AtomicUsize::new(0)))
                .unwrap()
                .build()
                .await
                .unwrap();
            let client = Builder::address(address.trim())
                .unwrap()
                .build()
                .await
                .unwrap();
            let c = Config {
                device_mac: "AA:BB:CC:DD:EE:FF".into(),
                proximity_enabled: false,
                ..Config::default()
            };
            let mut watcher = BluetoothWatcher::with_connection(client, &c).await.unwrap();
            let key = "dev_AA_BB_CC_DD_EE_FF";
            assert!(watcher.initial_connections[key]);
            assert_eq!(state.starts.load(Ordering::SeqCst), 0);
            assert!(!watcher.ensure_discovery().await.unwrap());
            let mut tracker = crate::state::StateTracker::new(&c.active_devices());
            tracker.seed_connections(&watcher.initial_connections, &c);
            assert!(tracker.devices[key].last_seen.is_none());
            assert!(tracker.connection_actions(&c).is_empty());
            // Only the synthetic pre-snapshot update is queued. It must be
            // discarded rather than replayed as a new physical disconnection.
            assert!(
                tokio::time::timeout(Duration::from_millis(30), watcher.next_message())
                    .await
                    .is_err()
            );
            server
                .emit_signal(
                    None::<&str>,
                    path,
                    "org.freedesktop.DBus.Properties",
                    "PropertiesChanged",
                    &(
                        "org.bluez.Device1",
                        HashMap::from([("Connected".to_string(), OwnedValue::from(false))]),
                        Vec::<String>::new(),
                    ),
                )
                .await
                .unwrap();
            loop {
                let msg = watcher.next_message().await.unwrap();
                if let Some((p, _, props)) = device_update(&msg)
                    && p == path
                    && props.contains_key("Connected")
                {
                    assert!(!bool::try_from(&props["Connected"]).unwrap());
                    break;
                }
            }
            server
                .emit_signal(
                    None::<&str>,
                    "/",
                    "org.freedesktop.DBus.ObjectManager",
                    "InterfacesRemoved",
                    &(
                        OwnedObjectPath::try_from(path).unwrap(),
                        vec!["org.bluez.Device1".to_string()],
                    ),
                )
                .await
                .unwrap();
            loop {
                let msg = watcher.next_message().await.unwrap();
                if let Some((p, _, props)) = device_update(&msg)
                    && p == path
                {
                    assert!(!bool::try_from(&props["Connected"]).unwrap());
                    break;
                }
            }
            state.powered.store(false, Ordering::SeqCst);
            assert!(!watcher.ensure_discovery().await.unwrap());
            watcher.stop_discovery().await;
            assert_eq!(state.stops.load(Ordering::SeqCst), 0);
        })
        .await
        .unwrap();
    }
    // ObjectManager is provided automatically by zbus when registered.
    #[tokio::test]
    async fn real_dbus_discovery_recovery_updates_and_owner_loss() {
        let mut child = Command::new("dbus-daemon")
            .args(["--session", "--nofork", "--print-address=1", "--nosyslog"])
            .stdout(Stdio::piped())
            .spawn()
            .expect("dbus-daemon is required for integration tests");
        let mut address = String::new();
        std::io::BufReader::new(child.stdout.take().unwrap())
            .read_line(&mut address)
            .unwrap();
        let _bus = PrivateBus(child);
        tokio::time::timeout(Duration::from_secs(20), async {
            let state = Arc::new(ScanState::default());
            state.powered.store(true, Ordering::SeqCst);
            state.start_delay_ms.store(4000, Ordering::SeqCst);
            let server = Builder::address(address.trim())
                .unwrap()
                .name("org.bluez")
                .unwrap()
                .serve_at("/", zbus::fdo::ObjectManager)
                .unwrap()
                .serve_at("/org/bluez/hci0", Adapter(state.clone()))
                .unwrap()
                .build()
                .await
                .unwrap();
            let client = Builder::address(address.trim())
                .unwrap()
                .method_timeout(BLUEZ_METHOD_TIMEOUT)
                .build()
                .await
                .unwrap();
            let mut watcher = BluetoothWatcher::with_connection(client, &Config::default())
                .await
                .unwrap();
            state.start_delay_ms.store(0, Ordering::SeqCst);
            assert_eq!(state.starts.load(Ordering::SeqCst), 1);
            assert!(!watcher.ensure_discovery().await.unwrap());
            // Reproduce the observed Discovering=false / StartDiscovery=InProgress state.
            state.discovering.store(false, Ordering::SeqCst);
            assert!(watcher.ensure_discovery().await.unwrap());
            assert_eq!(state.stops.load(Ordering::SeqCst), 0);
            assert_eq!(state.starts.load(Ordering::SeqCst), 1);
            state.discovering.store(true, Ordering::SeqCst);
            assert!(!watcher.ensure_discovery().await.unwrap());
            assert!(watcher.discovery_inactive_since.is_none());
            assert_eq!(state.stops.load(Ordering::SeqCst), 0);
            state.discovering.store(false, Ordering::SeqCst);
            assert!(watcher.ensure_discovery().await.unwrap());
            watcher.discovery_inactive_since =
                Some(tokio::time::Instant::now() - Duration::from_secs(5));
            assert!(watcher.ensure_discovery().await.unwrap());
            assert_eq!(state.stops.load(Ordering::SeqCst), 1);
            assert_eq!(state.starts.load(Ordering::SeqCst), 2);
            assert!(state.discovering.load(Ordering::SeqCst));
            let path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF";
            let props = HashMap::from([("RSSI".to_string(), OwnedValue::from(-44i16))]);
            server
                .emit_signal(
                    None::<&str>,
                    path,
                    "org.freedesktop.DBus.Properties",
                    "PropertiesChanged",
                    &("org.bluez.Device1", props, Vec::<String>::new()),
                )
                .await
                .unwrap();
            loop {
                let msg = watcher.next_message().await.unwrap();
                if let Some((p, iface, props)) = device_update(&msg) {
                    assert_eq!(p, path);
                    assert_eq!(iface, "org.bluez.Device1");
                    assert_eq!(i16::try_from(&props["RSSI"]).unwrap(), -44);
                    break;
                }
            }
            let props = HashMap::from([("RSSI".to_string(), OwnedValue::from(-55i16))]);
            server
                .emit_signal(
                    None::<&str>,
                    "/",
                    "org.freedesktop.DBus.ObjectManager",
                    "InterfacesAdded",
                    &(
                        OwnedObjectPath::try_from(path).unwrap(),
                        HashMap::from([("org.bluez.Device1".to_string(), props)]),
                    ),
                )
                .await
                .unwrap();
            loop {
                let msg = watcher.next_message().await.unwrap();
                if let Some((p, _, props)) = device_update(&msg) {
                    assert_eq!(p, path);
                    assert_eq!(i16::try_from(&props["RSSI"]).unwrap(), -55);
                    break;
                }
            }
            state.powered.store(false, Ordering::SeqCst);
            assert!(watcher.ensure_discovery().await.unwrap());
            state.powered.store(true, Ordering::SeqCst);
            assert!(watcher.ensure_discovery().await.unwrap());
            watcher.stop_discovery().await;
            assert_eq!(state.stops.load(Ordering::SeqCst), 3);
            let starts = state.starts.load(Ordering::SeqCst);
            let stops = state.stops.load(Ordering::SeqCst);
            for cycle in 0..128 {
                state.powered.store(false, Ordering::SeqCst);
                assert!(watcher.ensure_discovery().await.unwrap());
                assert!(!watcher.ensure_discovery().await.unwrap());
                // Cover both released sessions and controllers retaining the
                // session while another client keeps global discovery active.
                state.owned.store(cycle % 2 == 0, Ordering::SeqCst);
                state.discovering.store(true, Ordering::SeqCst);
                state.powered.store(true, Ordering::SeqCst);
                assert!(watcher.ensure_discovery().await.unwrap());
                assert!(!watcher.ensure_discovery().await.unwrap());
                assert_eq!(state.starts.load(Ordering::SeqCst), starts + cycle + 1);
            }
            watcher.stop_discovery().await;
            watcher.stop_discovery().await;
            assert_eq!(state.stops.load(Ordering::SeqCst), stops + 64 + 1);
            server.release_name("org.bluez").await.unwrap();
            assert!(watcher.next_message().await.is_err());
        })
        .await
        .expect("private D-Bus test timed out");
    }
}
