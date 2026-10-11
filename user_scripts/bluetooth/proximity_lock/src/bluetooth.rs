use crate::config::{Config, mac_to_dbus_suffix};
use futures_util::{
    StreamExt,
    stream::{Select, select},
};
use std::{collections::HashMap, time::Duration};
use zbus::{
    MatchRule, MessageStream,
    connection::Builder,
    proxy::{Builder as ProxyBuilder, CacheProperties, Proxy},
    zvariant::{OwnedObjectPath, OwnedValue, Value},
};

pub type Properties = HashMap<String, OwnedValue>;
type Interfaces = HashMap<String, Properties>;
type ManagedObjects = HashMap<OwnedObjectPath, Interfaces>;

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
    if config.has_proximity_devices() {
        candidates.retain(|(_, powered, _)| *powered);
    }
    candidates.sort_by(|a, b| {
        b.2.cmp(&a.2)
            .then_with(|| b.1.cmp(&a.1))
            .then_with(|| a.0.cmp(&b.0))
    });
    candidates
        .into_iter()
        .next()
        .map(|(p, _, _)| p)
        .ok_or_else(|| "no powered Bluetooth adapter; enable Bluetooth first".into())
}
impl BluetoothWatcher {
    pub async fn new(config: &Config) -> Result<Self, Box<dyn std::error::Error>> {
        let conn = Builder::system()?
            .method_timeout(Duration::from_secs(3))
            .build()
            .await?;
        Self::with_connection(conn, config).await
    }
    async fn with_connection(
        conn: zbus::Connection,
        config: &Config,
    ) -> Result<Self, Box<dyn std::error::Error>> {
        let adapter_path = select_adapter(&objects(&conn).await?, config)?;
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
        let initial_connections = objects(&conn)
            .await?
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
        if discovery_enabled {
            let adapter = Proxy::new(
                &conn,
                "org.bluez",
                adapter_path.as_str(),
                "org.bluez.Adapter1",
            )
            .await?;
            let mut filter = HashMap::from([
                ("DuplicateData", Value::from(true)),
                ("Transport", Value::from(config.transport.as_str())),
            ]);
            let targets: Vec<_> = config
                .active_devices()
                .into_iter()
                .filter(|d| d.proximity)
                .collect();
            // BlueZ may merge other clients' filters; still filter exact paths in our event loop.
            if targets.len() == 1 {
                filter.insert("Pattern", Value::from(targets[0].mac.to_ascii_uppercase()));
            }
            adapter
                .call::<_, _, ()>("SetDiscoveryFilter", &(filter,))
                .await?;
            adapter.call::<_, _, ()>("StartDiscovery", &()).await?;
        }
        Ok(Self {
            conn,
            adapter_path,
            stream,
            discovery_enabled,
            initial_connections,
        })
    }
    pub async fn next_message(&mut self) -> Result<zbus::Message, Box<dyn std::error::Error>> {
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
        Ok(msg)
    }
    /// Detect daemon/controller loss as an observation failure, not device departure.
    /// True means discovery was interrupted/pending: discard stale measurement evidence.
    pub async fn ensure_discovery(&self) -> Result<bool, Box<dyn std::error::Error>> {
        let adapter: Proxy<'_> = ProxyBuilder::new(&self.conn)
            .destination("org.bluez")?
            .path(self.adapter_path.as_str())?
            .interface("org.bluez.Adapter1")?
            .cache_properties(CacheProperties::No)
            .build()
            .await?;
        if !self.discovery_enabled {
            return Ok(false);
        }
        if !adapter.get_property::<bool>("Powered").await? {
            return Ok(true);
        }
        if !adapter.get_property::<bool>("Discovering").await? {
            match adapter.call::<_, _, ()>("StartDiscovery", &()).await {
                Ok(()) => {
                    eprintln!("[bluetooth] Discovery resumed");
                    return Ok(true);
                }
                // BlueZ still owns our session but scanning is inactive. Release and
                // reacquire only our session; other clients' discovery is unaffected.
                Err(zbus::Error::MethodError(name, _, _))
                    if name.as_str() == "org.bluez.Error.InProgress" =>
                {
                    adapter.call::<_, _, ()>("StopDiscovery", &()).await?;
                    adapter.call::<_, _, ()>("StartDiscovery", &()).await?;
                    eprintln!("[bluetooth] Reacquired inactive discovery session");
                    return Ok(true);
                }
                Err(e) => return Err(e.into()),
            }
        }
        Ok(false)
    }
    pub async fn stop_discovery(&self) {
        if !self.discovery_enabled {
            return;
        }
        if let Ok(adapter) = Proxy::new(
            &self.conn,
            "org.bluez",
            self.adapter_path.as_str(),
            "org.bluez.Adapter1",
        )
        .await
            && let Err(e) = adapter.call::<_, _, ()>("StopDiscovery", &()).await
        {
            eprintln!("[bluetooth] StopDiscovery: {e}");
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
    }
    struct Adapter(Arc<ScanState>);
    #[derive(Debug, zbus::DBusError)]
    #[zbus(prefix = "org.bluez.Error")]
    enum ScanError {
        InProgress(String),
    }
    #[zbus::interface(name = "org.bluez.Adapter1")]
    impl Adapter {
        fn set_discovery_filter(&self, _filter: Properties) {}
        fn start_discovery(&self) -> Result<(), ScanError> {
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
            self.0.powered.load(Ordering::SeqCst)
        }
        #[zbus(property)]
        fn discovering(&self) -> bool {
            self.0.discovering.load(Ordering::SeqCst)
        }
    }
    struct Device;
    #[zbus::interface(name = "org.bluez.Device1")]
    impl Device {
        #[zbus(property)]
        fn connected(&self) -> bool {
            true
        }
        #[zbus(property)]
        fn rssi(&self) -> i16 {
            -40
        }
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
                .serve_at(path, Device)
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
        tokio::time::timeout(Duration::from_secs(10), async {
            let state = Arc::new(ScanState::default());
            state.powered.store(true, Ordering::SeqCst);
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
                .method_timeout(Duration::from_secs(2))
                .build()
                .await
                .unwrap();
            let mut watcher = BluetoothWatcher::with_connection(client, &Config::default())
                .await
                .unwrap();
            assert_eq!(state.starts.load(Ordering::SeqCst), 1);
            assert!(!watcher.ensure_discovery().await.unwrap());
            // Reproduce the observed Discovering=false / StartDiscovery=InProgress state.
            state.discovering.store(false, Ordering::SeqCst);
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
            watcher.stop_discovery().await;
            assert_eq!(state.stops.load(Ordering::SeqCst), 2);
            server.release_name("org.bluez").await.unwrap();
            assert!(watcher.next_message().await.is_err());
        })
        .await
        .expect("private D-Bus test timed out");
    }
}
