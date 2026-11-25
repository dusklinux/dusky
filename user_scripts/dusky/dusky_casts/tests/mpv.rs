use dusky_casts::player::{Player, TRIMS};
use serde_json::json;
use std::{
    io::Write,
    time::{Duration, Instant},
};

fn wave(path: &std::path::Path) {
    let rate = 48000u32;
    let samples = rate * 20;
    let size = samples * 2;
    let mut f = std::fs::File::create(path).unwrap();
    f.write_all(b"RIFF").unwrap();
    f.write_all(&(size + 36).to_le_bytes()).unwrap();
    f.write_all(b"WAVEfmt ").unwrap();
    f.write_all(&16u32.to_le_bytes()).unwrap();
    f.write_all(&1u16.to_le_bytes()).unwrap();
    f.write_all(&1u16.to_le_bytes()).unwrap();
    f.write_all(&rate.to_le_bytes()).unwrap();
    f.write_all(&(rate * 2).to_le_bytes()).unwrap();
    f.write_all(&2u16.to_le_bytes()).unwrap();
    f.write_all(&16u16.to_le_bytes()).unwrap();
    f.write_all(b"data").unwrap();
    f.write_all(&size.to_le_bytes()).unwrap();
    let mut bytes = Vec::with_capacity(size as usize);
    for index in 0..samples {
        let t = index as f64 / rate as f64;
        let value = if (t % 3.0) > 1.0 {
            0
        } else {
            ((t * 440.0 * std::f64::consts::TAU).sin() * 10000.0) as i16
        };
        bytes.extend_from_slice(&value.to_le_bytes());
    }
    f.write_all(&bytes).unwrap();
}
fn wait(p: &Player, predicate: impl Fn(&dusky_casts::player::State) -> bool) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        let state = p.state();
        if predicate(&state) {
            return;
        }
        if let Some(e) = state.error {
            panic!("{e}")
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    panic!("mpv timed out: {:?}", p.state())
}
#[test]
fn real_mpv_trim_seek_speed_replacement_and_cleanup() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("tone.wav");
    wave(&path);
    for trim in TRIMS {
        let mut p = Player::start(trim, 1.0, 0.0, true).unwrap();
        p.load(path.to_str().unwrap(), 2.0, false).unwrap();
        wait(&p, |s| s.loaded && s.position >= 2.0);
        let deadline = Instant::now() + Duration::from_secs(5);
        let af = loop {
            let af = p.call(json!(["get_property", "af"])).unwrap();
            // Lua and IPC receive file-loaded independently. Wait for the
            // actual filter rather than assuming both clients run in order.
            if trim == "off"
                || af
                    .as_array()
                    .is_some_and(|filters| filters.iter().any(|f| f["label"] == "dusky_silence"))
            {
                break af;
            }
            assert!(Instant::now() < deadline, "Silence filter missing: {af}");
            std::thread::sleep(Duration::from_millis(20));
        };
        if trim == "off" {
            assert!(!af.to_string().contains("silenceremove"));
        } else {
            let af = af
                .as_array()
                .unwrap()
                .iter()
                .find(|v| v["label"] == "dusky_silence")
                .unwrap();
            let graph = af["params"]["graph"].as_str().unwrap();
            assert!(graph.contains("timestamp=copy"));
            assert!(graph.contains("lt(t,14.000000)"));
            assert_eq!(af["params"]["fix-pts"], "no");
            let (threshold, stop_duration) = match trim {
                "low" => ("0.00511000", "0.552000000"),
                "medium" => ("0.00500000", "0.072000000"),
                "max" => ("0.00500000", "0.000000000"),
                other => panic!("Unexpected trim level: {other}"),
            };
            assert!(
                graph.contains(&format!("stop_threshold={threshold}")),
                "{graph}"
            );
            assert!(
                graph.contains(&format!("stop_duration={stop_duration}")),
                "{graph}"
            );
        }
        p.pause(true).unwrap();
        wait(&p, |s| s.paused);
        p.seek(10.0).unwrap();
        wait(&p, |s| (s.position - 10.0).abs() < 0.5);
        p.speed(1.75).unwrap();
        assert_eq!(p.call(json!(["get_property", "speed"])).unwrap(), 1.75);
        // Replacing files repeatedly must not mistake the previous file's
        // end-file event for EOF on the new episode.
        for index in 0..12 {
            p.load(path.to_str().unwrap(), (index % 5) as f64, true)
                .unwrap();
            wait(&p, |s| s.loaded);
            assert!(!p.state().eof);
        }
        p.load(path.to_str().unwrap(), 18.5, false).unwrap();
        p.speed(4.0).unwrap();
        wait(&p, |s| s.eof);
        let pid = p.process_id();
        drop(p);
        assert!(
            !std::path::Path::new(&format!("/proc/{pid}")).exists(),
            "mpv was not reaped"
        );
    }
}

#[test]
fn subprocess_player_helper() {
    if std::env::var("DUSKY_CASTS_MPV_TEST_CHILD").as_deref() != Ok("1") {
        return;
    }
    let p = Player::start("off", 1.0, 0.0, true).unwrap();
    println!("MPV_PID={}", p.process_id());
    std::io::stdout().flush().unwrap();
    std::thread::sleep(Duration::from_secs(30));
    drop(p);
}

#[test]
fn killing_parent_also_terminates_mpv() {
    use std::io::BufRead;
    let dir = tempfile::tempdir().unwrap();
    let mut child = std::process::Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "subprocess_player_helper", "--nocapture"])
        .env("DUSKY_CASTS_MPV_TEST_CHILD", "1")
        .env("TMPDIR", dir.path())
        .stdout(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    let mut pid = None;
    for line in std::io::BufReader::new(child.stdout.take().unwrap()).lines() {
        let line = line.unwrap();
        if let Some((_, number)) = line.split_once("MPV_PID=") {
            pid = Some(number.trim().parse::<u32>().unwrap());
            break;
        }
    }
    let pid = pid.expect("Helper did not start mpv");
    child.kill().unwrap();
    child.wait().unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        let status = std::fs::read_to_string(format!("/proc/{pid}/status"));
        if status.as_ref().is_err()
            || status.as_ref().is_ok_and(|s| {
                s.lines()
                    .any(|l| l.starts_with("State:") && l.contains('Z'))
            })
        {
            break;
        }
        assert!(Instant::now() < deadline, "mpv survived parent death");
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[test]
fn live_trim_switching_updates_filter_without_restarting_mpv() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("tone.wav");
    wave(&path);
    let mut p = Player::start("off", 1.0, 0.0, true).unwrap();
    let initial_pid = p.process_id();
    p.load(path.to_str().unwrap(), 1.0, false).unwrap();
    wait(&p, |s| s.loaded && s.position >= 1.0);
    let initial_af = p.call(json!(["get_property", "af"])).unwrap();
    assert!(!initial_af.to_string().contains("silenceremove"));

    for level in ["low", "medium", "max", "off"] {
        p.set_trim(level).unwrap();
        assert_eq!(p.process_id(), initial_pid);
        assert!(p.is_running());
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            let af = p.call(json!(["get_property", "af"])).unwrap();
            if level == "off" {
                if !af.to_string().contains("silenceremove") {
                    break;
                }
            } else if let Some(filter) = af
                .as_array()
                .and_then(|filters| filters.iter().find(|f| f["label"] == "dusky_silence"))
            {
                let graph = filter["params"]["graph"].as_str().unwrap_or("");
                let (threshold, stop_duration) = match level {
                    "low" => ("0.00511000", "0.552000000"),
                    "medium" => ("0.00500000", "0.072000000"),
                    "max" => ("0.00500000", "0.000000000"),
                    other => panic!("Unexpected trim level: {other}"),
                };
                if graph.contains(&format!("stop_threshold={threshold}"))
                    && graph.contains(&format!("stop_duration={stop_duration}"))
                {
                    break;
                }
            }
            assert!(
                Instant::now() < deadline,
                "Live trim level {level} did not take effect: {af}"
            );
            std::thread::sleep(Duration::from_millis(20));
        }
    }
    assert_eq!(p.process_id(), initial_pid);
    drop(p);
    assert!(
        !std::path::Path::new(&format!("/proc/{initial_pid}")).exists(),
        "mpv was not reaped"
    );
}
