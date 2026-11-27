//! Lightweight PipeWire / WirePlumber Bluetooth audio profile bridge.
//!
//! Queries `bluez_card.<MAC_UNDERSCORED>` via `pw-dump` and applies profile
//! changes via `wpctl set-profile` using `gpu::inherit_session_environment()`
//! and bounded timeouts, degrading cleanly to `AudioProfileState::Unavailable`.

use crate::gpu;
use crate::model::{AudioProfile, AudioProfileState};
use iced_futures::futures::channel::oneshot;
use std::io::Read;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const PW_DUMP_TIMEOUT: Duration = Duration::from_millis(1500);
const WPCTL_TIMEOUT: Duration = Duration::from_millis(2000);

fn run_with_timeout(
    command: &mut Command,
    timeout: Duration,
    capture_stdout: bool,
) -> Result<(i32, String), String> {
    gpu::inherit_session_environment(command);
    command.stdin(Stdio::null());
    if capture_stdout {
        command.stdout(Stdio::piped()).stderr(Stdio::null());
    } else {
        command.stdout(Stdio::null()).stderr(Stdio::piped());
    }

    let mut child = command.spawn().map_err(|err| err.to_string())?;
    let reader = if capture_stdout {
        let mut pipe = child.stdout.take();
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            if let Some(ref mut p) = pipe {
                let _ = p.read_to_end(&mut buf);
            }
            buf
        })
    } else {
        let mut pipe = child.stderr.take();
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            if let Some(ref mut p) = pipe {
                let _ = p.read_to_end(&mut buf);
            }
            buf
        })
    };

    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let bytes = reader.join().unwrap_or_default();
                let output = String::from_utf8_lossy(&bytes).into_owned();
                return Ok((status.code().unwrap_or(-1), output));
            }
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    let _ = reader.join();
                    return Err("Timed out waiting for PipeWire".to_string());
                }
                std::thread::sleep(Duration::from_millis(15));
            }
            Err(err) => {
                let _ = child.kill();
                let _ = child.wait();
                let _ = reader.join();
                return Err(err.to_string());
            }
        }
    }
}

/// Pure JSON parser locating `bluez_card.<AA_BB_CC_DD_EE_FF>` (or matching
/// `api.bluez5.address`) in `pw-dump` output.
pub fn parse_pw_dump_profiles(json_raw: &str, mac: &str) -> AudioProfileState {
    let trimmed_mac = mac.trim();
    if trimmed_mac.is_empty() {
        return AudioProfileState::Unavailable("Audio profiles unavailable".to_string());
    }

    let Ok(root) = serde_json::from_str::<serde_json::Value>(json_raw) else {
        return AudioProfileState::Unavailable("Audio profiles unavailable".to_string());
    };
    let Some(objects) = root.as_array() else {
        return AudioProfileState::Unavailable("Audio profiles unavailable".to_string());
    };

    let expected_card = format!(
        "bluez_card.{}",
        trimmed_mac.replace([':', '-'], "_").to_ascii_uppercase()
    );

    let card = objects.iter().find(|obj| {
        if obj.get("type").and_then(|v| v.as_str()) != Some("PipeWire:Interface:Device") {
            return false;
        }
        let Some(props) = obj.pointer("/info/props") else {
            return false;
        };
        let by_name = props
            .get("device.name")
            .and_then(|v| v.as_str())
            .is_some_and(|name| name.eq_ignore_ascii_case(&expected_card));
        let by_addr = props
            .get("api.bluez5.address")
            .and_then(|v| v.as_str())
            .is_some_and(|addr| addr.eq_ignore_ascii_case(trimmed_mac));
        by_name || by_addr
    });

    let Some(card) = card else {
        return AudioProfileState::Unavailable("No PipeWire audio card found".to_string());
    };

    let Some(card_id) = card
        .get("id")
        .and_then(|v| v.as_u64())
        .and_then(|id| u32::try_from(id).ok())
    else {
        return AudioProfileState::Unavailable("Invalid PipeWire card ID".to_string());
    };

    let active_index = card
        .pointer("/info/params/Profile")
        .and_then(|v| v.as_array())
        .and_then(|arr| arr.first())
        .and_then(|entry| entry.get("index"))
        .and_then(|v| v.as_u64())
        .and_then(|idx| u32::try_from(idx).ok());

    let Some(enum_profiles) = card
        .pointer("/info/params/EnumProfile")
        .and_then(|v| v.as_array())
    else {
        return AudioProfileState::Unavailable("No audio profiles exposed".to_string());
    };

    let mut profiles: Vec<AudioProfile> = enum_profiles
        .iter()
        .filter_map(|entry| {
            let index = entry
                .get("index")
                .and_then(|v| v.as_u64())
                .and_then(|v| u32::try_from(v).ok())?;
            let name = entry.get("name").and_then(|v| v.as_str())?.trim();
            if name.is_empty() {
                return None;
            }
            let available = entry
                .get("available")
                .and_then(|v| v.as_str())
                .unwrap_or("yes");
            if available.eq_ignore_ascii_case("no") && Some(index) != active_index {
                return None;
            }
            let description = entry
                .get("description")
                .and_then(|v| v.as_str())
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .unwrap_or(name)
                .to_string();
            let priority = entry
                .get("priority")
                .and_then(|v| v.as_i64())
                .and_then(|v| i32::try_from(v).ok())
                .unwrap_or(0);
            Some(AudioProfile {
                index,
                name: name.to_string(),
                description,
                priority,
            })
        })
        .collect();

    if profiles.is_empty() {
        return AudioProfileState::Unavailable("No switchable audio profiles".to_string());
    }

    profiles.sort_by(|a, b| {
        a.name
            .eq_ignore_ascii_case("off")
            .cmp(&b.name.eq_ignore_ascii_case("off"))
            .then_with(|| b.priority.cmp(&a.priority))
            .then_with(|| a.index.cmp(&b.index))
    });

    AudioProfileState::Available {
        card_id,
        profiles,
        active_index,
    }
}

fn query_audio_profiles_blocking(mac: &str) -> AudioProfileState {
    let mut cmd = Command::new("pw-dump");
    match run_with_timeout(&mut cmd, PW_DUMP_TIMEOUT, true) {
        Ok((0, stdout)) => parse_pw_dump_profiles(&stdout, mac),
        Ok(_) => AudioProfileState::Unavailable("Audio profiles unavailable".to_string()),
        Err(_) => AudioProfileState::Unavailable("Audio profiles unavailable".to_string()),
    }
}

fn set_audio_profile_blocking(card_id: u32, profile_index: u32) -> Result<(), String> {
    let mut cmd = Command::new("wpctl");
    cmd.args([
        "set-profile",
        &card_id.to_string(),
        &profile_index.to_string(),
    ]);
    match run_with_timeout(&mut cmd, WPCTL_TIMEOUT, false) {
        Ok((0, _)) => Ok(()),
        Ok((code, stderr)) => {
            let detail = stderr.trim();
            if detail.is_empty() {
                Err(format!("wpctl set-profile failed (exit {code})"))
            } else {
                Err(format!("Could not switch audio profile: {detail}"))
            }
        }
        Err(err) => Err(format!("Could not run wpctl: {err}")),
    }
}

/// Inspect PipeWire Bluetooth audio profiles for `mac` on a dedicated OS thread
/// so blocking subprocess I/O never starves Iced's 2-thread background executor.
pub async fn query_audio_profiles(mac: String) -> AudioProfileState {
    let (tx, rx) = oneshot::channel();
    let spawned = std::thread::Builder::new()
        .name("bluetooth-audio".into())
        .spawn(move || {
            let _ = tx.send(query_audio_profiles_blocking(&mac));
        });
    if spawned.is_err() {
        return AudioProfileState::Unavailable("Audio profiles unavailable".to_string());
    }
    rx.await
        .unwrap_or_else(|_| AudioProfileState::Unavailable("Audio profiles unavailable".to_string()))
}

/// Switch a PipeWire Bluetooth card profile via `wpctl set-profile` on a
/// dedicated OS thread so Iced's background executor stays responsive.
pub async fn set_audio_profile(card_id: u32, profile_index: u32) -> Result<(), String> {
    let (tx, rx) = oneshot::channel();
    let spawned = std::thread::Builder::new()
        .name("bluetooth-audio".into())
        .spawn(move || {
            let _ = tx.send(set_audio_profile_blocking(card_id, profile_index));
        });
    if let Err(err) = spawned {
        return Err(format!("Could not start audio profile worker: {err}"));
    }
    rx.await
        .unwrap_or_else(|_| Err("Audio profile worker terminated unexpectedly".to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE_PW_DUMP: &str = r#"[
      {
        "id": 41,
        "type": "PipeWire:Interface:Node",
        "info": { "props": { "node.name": "alsa_output.pci" } }
      },
      {
        "id": 68,
        "type": "PipeWire:Interface:Device",
        "info": {
          "props": {
            "device.name": "bluez_card.AA_BB_CC_DD_EE_FF",
            "api.bluez5.address": "AA:BB:CC:DD:EE:FF"
          },
          "params": {
            "Profile": [
              { "index": 2, "name": "a2dp-sink", "description": "High Fidelity Playback (A2DP Sink)" }
            ],
            "EnumProfile": [
              { "index": 0, "name": "off", "description": "Off", "priority": 0, "available": "yes" },
              { "index": 3, "name": "headset-head-unit-msbc", "description": "Handsfree (mSBC)", "priority": 2, "available": "yes" },
              { "index": 2, "name": "a2dp-sink", "description": "High Fidelity Playback (A2DP Sink)", "priority": 16, "available": "yes" },
              { "index": 4, "name": "a2dp-sink-ldac", "description": "High Fidelity Playback (LDAC)", "priority": 18, "available": "no" }
            ]
          }
        }
      }
    ]"#;

    #[test]
    fn parses_valid_bluez_card_filters_unavailable_keeps_off_last_and_tracks_active_option() {
        let state = parse_pw_dump_profiles(SAMPLE_PW_DUMP, "aa:bb:cc:dd:ee:ff");
        match state {
            AudioProfileState::Available {
                card_id,
                profiles,
                active_index,
            } => {
                assert_eq!(card_id, 68);
                assert_eq!(active_index, Some(2));
                let names: Vec<&str> = profiles.iter().map(|p| p.name.as_str()).collect();
                assert_eq!(names, vec!["a2dp-sink", "headset-head-unit-msbc", "off"]);
                assert_eq!(profiles[0].short_label(), "A2DP Sink");
                assert_eq!(profiles[1].short_label(), "Handsfree (mSBC)");
                assert_eq!(profiles[2].short_label(), "Off");
            }
            other => panic!("expected Available, got {other:?}"),
        }

        // Missing /info/params/Profile yields active_index == None (never falsely selects "off").
        let no_active = r#"[
          {
            "id": 70,
            "type": "PipeWire:Interface:Device",
            "info": {
              "props": { "api.bluez5.address": "AA:BB:CC:DD:EE:FF" },
              "params": {
                "EnumProfile": [
                  { "index": 0, "name": "off", "description": "Off", "priority": 100, "available": "yes" },
                  { "index": 1, "name": "bap-sink", "description": "LE Audio", "priority": 20, "available": "yes" }
                ]
              }
            }
          }
        ]"#;
        match parse_pw_dump_profiles(no_active, "AA:BB:CC:DD:EE:FF") {
            AudioProfileState::Available {
                card_id,
                profiles,
                active_index,
            } => {
                assert_eq!(card_id, 70);
                assert_eq!(active_index, None);
                let names: Vec<&str> = profiles.iter().map(|p| p.name.as_str()).collect();
                assert_eq!(names, vec!["bap-sink", "off"]);
                assert_eq!(profiles[0].short_label(), "LE Audio (Sink)");
            }
            other => panic!("expected Available, got {other:?}"),
        }
    }

    #[test]
    fn returns_unavailable_for_missing_card_and_malformed_json() {
        assert!(matches!(
            parse_pw_dump_profiles(SAMPLE_PW_DUMP, "11:22:33:44:55:66"),
            AudioProfileState::Unavailable(_)
        ));
        assert!(matches!(
            parse_pw_dump_profiles("{not valid json", "AA:BB:CC:DD:EE:FF"),
            AudioProfileState::Unavailable(_)
        ));
        assert!(matches!(
            parse_pw_dump_profiles("{}", "AA:BB:CC:DD:EE:FF"),
            AudioProfileState::Unavailable(_)
        ));
    }
}
