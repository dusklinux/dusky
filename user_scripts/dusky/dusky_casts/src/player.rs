use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Write},
    os::{
        fd::AsRawFd,
        unix::{net::UnixStream, process::CommandExt},
    },
    process::{Child, Command, Stdio},
    sync::{Arc, Condvar, Mutex, mpsc},
    thread,
    time::{Duration, Instant},
};

pub const TRIMS: [&str; 4] = ["off", "low", "medium", "max"];
#[derive(Clone, Debug)]
pub struct State {
    pub position: f64,
    pub duration: f64,
    pub paused: bool,
    pub buffering: bool,
    pub loaded: bool,
    pub eof: bool,
    pub stopped: bool,
    pub error: Option<String>,
    pub generation: u64,
    entry: Option<i64>,
}
impl Default for State {
    fn default() -> Self {
        Self {
            position: 0.0,
            duration: 0.0,
            paused: true,
            buffering: false,
            loaded: false,
            eof: false,
            stopped: false,
            error: None,
            generation: 0,
            entry: None,
        }
    }
}
type Waiters = Arc<Mutex<HashMap<u64, mpsc::Sender<Value>>>>;
struct StartupChild(Option<Child>);
impl Drop for StartupChild {
    fn drop(&mut self) {
        if let Some(child) = &mut self.0 {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}
pub struct Player {
    child: Child,
    socket: UnixStream,
    state: Arc<Mutex<State>>,
    wake: Arc<Condvar>,
    reader: Option<thread::JoinHandle<()>>,
    pending: Waiters,
    request: u64,
    _assets: tempfile::TempDir,
}
pub fn default_trim() -> String {
    let config =
        crate::db::xdg("XDG_CONFIG_HOME", ".config").join("mpv/script-opts/dusky_silence.conf");
    let mut level = "max".to_owned();
    let mut enabled = true;
    if let Ok(text) = std::fs::read_to_string(config) {
        for line in text.lines() {
            let line = line.split('#').next().unwrap_or("").trim();
            if let Some((key, value)) = line.split_once('=') {
                match key.trim() {
                    "enabled" => enabled = !matches!(value.trim(), "no" | "false" | "0"),
                    "level" if matches!(value.trim(), "low" | "medium" | "max") => {
                        level = value.trim().into()
                    }
                    _ => {}
                }
            }
        }
    }
    if enabled { level } else { "off".into() }
}
impl Player {
    pub fn start(trim: &str, speed: f64, volume: f64, silent: bool) -> Result<Self> {
        anyhow::ensure!(TRIMS.contains(&trim), "Unknown trim level");
        let assets = tempfile::tempdir()?;
        let installed =
            crate::db::xdg("XDG_CONFIG_HOME", ".config").join("mpv/scripts/dusky_silence.lua");
        let script = if let Some(path) = std::env::var_os("DUSKY_CASTS_SILENCE_SCRIPT") {
            let path = std::path::PathBuf::from(path);
            anyhow::ensure!(path.is_file(), "DUSKY_CASTS_SILENCE_SCRIPT does not exist");
            path
        } else if installed.is_file() {
            installed
        } else {
            let path = assets.path().join("dusky_silence.lua");
            std::fs::write(&path, include_str!("../assets/dusky_silence.lua"))?;
            path
        };
        let (socket, child_socket) = UnixStream::pair()?;
        socket.set_write_timeout(Some(Duration::from_secs(2)))?;
        let fd = child_socket.as_raw_fd();
        let parent = std::process::id() as libc::pid_t;
        let mut command = Command::new("mpv");
        command
            .args([
                "--no-config",
                "--idle=yes",
                "--pause=yes",
                "--no-video",
                "--force-window=no",
                "--no-osc",
                "--no-osd-bar",
                "--no-terminal",
                "--audio-pitch-correction=yes",
                "--gapless-audio=yes",
                "--cache=yes",
                "--demuxer-max-bytes=64MiB",
            ])
            .arg(format!("--input-ipc-client=fd://{fd}"))
            .arg(format!("--volume={}", volume.clamp(0.0, 150.0)))
            .arg(format!("--speed={}", speed.clamp(0.25, 4.0)))
            .arg(format!("--script={}", script.display()))
            .arg(format!(
                "--script-opts=dusky_silence-enabled={},dusky_silence-level={}",
                if trim == "off" { "no" } else { "yes" },
                if trim == "off" { "max" } else { trim }
            ))
            .stdin(Stdio::null())
            .stdout(Stdio::null());
        if silent {
            command.arg("--ao=null");
        }
        let cache = if silent {
            assets.path().to_path_buf()
        } else {
            crate::db::xdg("XDG_CACHE_HOME", ".cache").join("dusky_casts")
        };
        std::fs::create_dir_all(&cache)?;
        command.stderr(std::fs::File::create(cache.join("mpv.log"))?);
        // Only async-signal-safe calls in the child of this multithreaded app.
        // Checking the parent after prctl closes the parent-death race.
        unsafe {
            command.pre_exec(move || {
                if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0 {
                    return Err(std::io::Error::last_os_error());
                }
                if libc::getppid() != parent {
                    libc::_exit(1)
                }
                if libc::fcntl(fd, libc::F_SETFD, 0) == -1 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let child = command.spawn().context("Starting mpv")?;
        let mut child = StartupChild(Some(child));
        drop(child_socket);
        let state = Arc::new(Mutex::new(State::default()));
        let wake = Arc::new(Condvar::new());
        let pending: Waiters = Arc::new(Mutex::new(HashMap::new()));
        let read_socket = socket.try_clone()?;
        let read_state = state.clone();
        let read_wake = wake.clone();
        let read_pending = pending.clone();
        let reader = thread::Builder::new()
            .name("mpv-ipc".into())
            .spawn(move || {
                let mut reader = BufReader::new(read_socket);
                let mut line = Vec::new();
                loop {
                    line.clear();
                    match reader.read_until(b'\n', &mut line) {
                        Ok(0) => break,
                        Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                        Err(_) => break,
                        Ok(_) => {}
                    }
                    let Ok(v) = serde_json::from_str::<Value>(&String::from_utf8_lossy(&line))
                    else {
                        continue;
                    };
                    if let Some(id) = v["request_id"].as_u64() {
                        if let Some(waiter) = read_pending.lock().unwrap().remove(&id) {
                            let _ = waiter.send(v);
                        }
                        continue;
                    }
                    let mut s = read_state.lock().unwrap();
                    match v["event"].as_str().unwrap_or("") {
                        "property-change" => match v["name"].as_str().unwrap_or("") {
                            "time-pos" if s.loaded => {
                                if let Some(t) = v["data"].as_f64() {
                                    s.position = t.max(0.0);
                                }
                            }
                            "duration" if s.loaded => {
                                if let Some(t) = v["data"].as_f64() {
                                    s.duration = t
                                }
                            }
                            "paused-for-cache" => {
                                s.buffering = v["data"].as_bool().unwrap_or(false)
                            }
                            "pause" => {
                                if let Some(p) = v["data"].as_bool() {
                                    s.paused = p;
                                    read_wake.notify_all();
                                }
                            }
                            _ => {}
                        },
                        "start-file" => {
                            s.loaded = false;
                            s.eof = false;
                            s.stopped = false;
                            s.entry = v["playlist_entry_id"].as_i64();
                        }
                        "file-loaded" => {
                            s.loaded = true;
                            s.stopped = false;
                            s.error = None;
                            read_wake.notify_all();
                        }
                        "end-file" if s.entry == v["playlist_entry_id"].as_i64() => {
                            match v["reason"].as_str().unwrap_or("") {
                                "eof" => {
                                    s.eof = true;
                                    s.loaded = false;
                                    s.paused = true;
                                }
                                "error" => {
                                    s.loaded = false;
                                    s.paused = true;
                                    s.error = Some(format!(
                                        "mpv playback failed: {}",
                                        v["file_error"].as_str().unwrap_or("unknown error")
                                    ));
                                }
                                "stop" | "quit" | "redirect" => {
                                    s.stopped = true;
                                    s.loaded = false;
                                }
                                _ => {}
                            }
                            read_wake.notify_all();
                        }
                        _ => {}
                    }
                }
                let mut s = read_state.lock().unwrap();
                s.loaded = false;
                s.paused = true;
                if s.error.is_none() {
                    s.error = Some("mpv IPC disconnected".into())
                }
                read_wake.notify_all();
                read_pending.lock().unwrap().clear();
            })?;
        let mut p = Self {
            child: child.0.take().expect("mpv child present"),
            socket,
            state,
            wake,
            reader: Some(reader),
            pending,
            request: 0,
            _assets: assets,
        };
        // A real round trip verifies inherited IPC and process startup.
        p.call(json!(["get_property", "mpv-version"]))?;
        for (id, name) in ["duration", "pause", "paused-for-cache", "time-pos"]
            .into_iter()
            .enumerate()
        {
            p.send(json!(["observe_property", id + 1, name]))?;
        }
        Ok(p)
    }
    pub fn state(&self) -> State {
        self.state.lock().unwrap().clone()
    }
    pub fn process_id(&self) -> u32 {
        self.child.id()
    }
    pub fn is_running(&mut self) -> bool {
        self.child.try_wait().is_ok_and(|status| status.is_none())
    }
    pub fn send(&mut self, args: Value) -> Result<()> {
        self.request += 1;
        self.write(json!({"command":args,"request_id":self.request}))
    }
    fn write(&mut self, v: Value) -> Result<()> {
        let mut bytes = serde_json::to_vec(&v)?;
        bytes.push(b'\n');
        self.socket.write_all(&bytes)?;
        Ok(())
    }
    pub fn call(&mut self, args: Value) -> Result<Value> {
        self.request += 1;
        let id = self.request;
        let (tx, rx) = mpsc::channel();
        self.pending.lock().unwrap().insert(id, tx);
        if let Err(e) = self.write(json!({"command":args,"request_id":id})) {
            self.pending.lock().unwrap().remove(&id);
            return Err(e);
        }
        let result = rx.recv_timeout(Duration::from_secs(4));
        self.pending.lock().unwrap().remove(&id);
        let reply = result.context("mpv did not respond")?;
        if reply["error"] != "success" {
            bail!("mpv: {}", reply["error"])
        }
        Ok(reply["data"].clone())
    }
    pub fn wait_for_load_or_end(&self, deadline: Instant) -> State {
        let mut s = self.state.lock().unwrap();
        while !s.loaded && !s.eof && !s.stopped && s.error.is_none() {
            let now = Instant::now();
            if now >= deadline {
                break;
            }
            let (guard, timeout) = self.wake.wait_timeout(s, deadline - now).unwrap();
            s = guard;
            if timeout.timed_out() {
                break;
            }
        }
        s.clone()
    }
    pub fn clear_error(&self) {
        self.state.lock().unwrap().error = None;
    }
    pub fn load(&mut self, url: &str, position: f64, paused: bool) -> Result<()> {
        self.call(json!(["set_property", "pause", true]))?;
        {
            let mut s = self.state.lock().unwrap();
            s.generation += 1;
            s.position = position.max(0.0);
            s.duration = 0.0;
            s.paused = paused;
            s.eof = false;
            s.stopped = false;
            s.loaded = false;
            s.error = None;
            s.entry = None;
        }
        self.call(json!([
            "loadfile",
            url,
            "replace",
            -1,
            {
                "start": format!("{:.3}", position.max(0.0)),
                "pause": if paused { "yes" } else { "no" }
            }
        ]))?;
        self.call(json!(["set_property", "pause", paused]))?;
        self.state.lock().unwrap().paused = paused;
        Ok(())
    }
    pub fn pause(&mut self, paused: bool) -> Result<()> {
        self.send(json!(["set_property", "pause", paused]))?;
        let mut s = self.state.lock().unwrap();
        s.paused = paused;
        self.wake.notify_all();
        Ok(())
    }
    pub fn toggle_pause(&mut self) -> Result<bool> {
        let next = !self.state.lock().unwrap().paused;
        self.pause(next)?;
        Ok(next)
    }
    pub fn seek(&mut self, seconds: f64) -> Result<()> {
        let target = seconds.max(0.0);
        self.state.lock().unwrap().position = target;
        self.send(json!(["seek", target, "absolute+exact"]))
    }
    pub fn speed(&mut self, speed: f64) -> Result<()> {
        self.send(json!(["set_property", "speed", speed.clamp(0.25, 4.0)]))
    }
    pub fn volume(&mut self, volume: f64) -> Result<()> {
        self.send(json!(["set_property", "volume", volume.clamp(0.0, 150.0)]))
    }
    pub fn set_trim(&mut self, trim: &str) -> Result<()> {
        anyhow::ensure!(TRIMS.contains(&trim), "Unknown trim level");
        if trim == "off" {
            self.call(json!([
                "change-list",
                "script-opts",
                "append",
                "dusky_silence-enabled=no"
            ]))?;
        } else {
            self.call(json!([
                "change-list",
                "script-opts",
                "append",
                format!("dusky_silence-level={trim}")
            ]))?;
            self.call(json!([
                "change-list",
                "script-opts",
                "append",
                "dusky_silence-enabled=yes"
            ]))?;
        }
        Ok(())
    }
}
impl Drop for Player {
    fn drop(&mut self) {
        let _ = self.send(json!(["quit"]));
        // Never leave audio or a reader thread behind, even on mpv failure.
        for _ in 0..20 {
            if self.child.try_wait().ok().flatten().is_some() {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
        let _ = self.socket.shutdown(std::net::Shutdown::Both);
        if let Some(reader) = self.reader.take() {
            let _ = reader.join();
        }
    }
}
