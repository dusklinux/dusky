#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: DUSKY KOKORO TTS CONFIGURATION ENGINE
===============================================================================
Engine Type: "kokoro" (alias: "dusky_kokoro")
Target: ~/.config/dusky-kokoro/config.toml
===============================================================================
Inherits from TomlEngine, adding:
- Dynamic multi-voice blending and mathematical weight normalization
- Reverse decomposition of voice specs into UI controls
- Virgin configuration file auto-creation with full annotations
- Instant Unix socket IPC hot-reload of the running daemon
- Zero hardcoded usernames (fully portable across systems and users)
===============================================================================
"""

lazy import math
import os
import re
lazy import socket
lazy import shutil
lazy import subprocess
lazy from pathlib import Path
lazy from typing import Any

from python.engines.toml import TomlEngine


def _get_default_config_path() -> Path:
    if "DUSKY_CONFIG" in os.environ:
        return Path(os.environ["DUSKY_CONFIG"]).expanduser().resolve()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return (base / "dusky-kokoro" / "config.toml").resolve()


DEFAULT_CONFIG_TEMPLATE = """# Dusky Kokoro TTS - configuration (TOML)
# Location: ~/.config/dusky-kokoro/config.toml
# Apply changes with: trigger.sh --reload (or dusky-kokoro-tui)

[voice]
spec = "af_heart:0.40,af_bella:0.60"   # "name" or "name:weight,name:weight"
blend = true                          # true | false
voice_1 = "af_heart"
weight_1 = 0.40
voice_2 = "af_bella"
weight_2 = 0.60
voice_3 = "none"
weight_3 = 0.00
lang = "auto"                         # auto = from voice prefix (a, b, j, z, e, f, h, i, p)
speed = 1.00                          # Kokoro duration-model speed, 0.50 - 2.00

[playback]
mpv_binary = "mpv"
mpv_speed = 1.00                      # second-stage tempo (scaletempo2, pitch-preserving)
volume = 100                          # 0 - 150
window = true                         # show small mpv window (space=pause, q=stop)
window_geometry = "420x96"
window_title = "Kokoro TTS"
audio_device = ""
cache_max_mb = 512
use_user_mpv_config = true
extra_args = []
prefetch_segments = 4
write_stall_timeout_s = 0.0

[archive]
enabled = true
dir = ""                              # "" = ~/.cache/dusky-kokoro/audio
max_files = 32
bit_depth = 16                        # 16 | 24

[engine]
provider = "auto"                     # auto | cuda | tensorrt | migraphx | openvino | cpu
precision = "auto"                    # auto | f32 | fp16 | fp16-gpu | int8
models_dir = ""
voices_file = ""
device_id = 0
gpu_mem_limit_mb = 2048               # CUDA arena budget, not total VRAM (0 = unlimited)
cudnn_conv_use_max_workspace = false
arena_extend_strategy = "kSameAsRequested"
cudnn_conv_algo_search = "HEURISTIC"
cuda_lib_dirs = []
openvino_device = "GPU"
openvino_precision = "FP16"
openvino_cache_dir = ""
tensorrt_cache_dir = ""
intra_op_threads = 0
allow_spinning = true
graph_optimization = "all"
require_accelerator = false
warmup = true
model_idle_timeout_s = 30.0           # unload model after inactivity (frees GPU VRAM)
profiling = false

[engine.env]

[daemon]
socket_path = ""                      # "" = $XDG_RUNTIME_DIR/dusky-kokoro/control.sock
default_mode = "interrupt"            # interrupt | enqueue
dedup_window_s = 2.0
max_queue = 8
process_idle_timeout_s = 30.0         # exit after idling: allows laptop GPU D3cold sleep
exit_when_idle = true
desktop_notifications = true
request_timeout_s = 10.0
max_request_bytes = 67108864

[text]
max_chars = 10000000                  # 10M chars (~2,500 pages); 0 = unlimited
url_mode = "domain"                   # domain | placeholder | omit
url_placeholder = "link"
emoji_mode = "strip"                  # strip | name
read_code_blocks = false
strip_citations = true                # remove [12] / [^3] citations
target_segment_chars = 220
max_segment_chars = 320
min_segment_chars = 24
first_segment_max_chars = 140
max_phonemes = 480
sentence_pause_ms = 140
paragraph_pause_ms = 380
trim_silence = true

[logging]
level = "INFO"
file = ""
file_max_mb = 5
ort_verbose = false
"""


class KokoroEngine(TomlEngine):
    """
    Dusky Kokoro Neural TTS Configuration Engine.

    Features:
    - Pure TOML backend with atomic disk commits and mtime conflict detection.
    - Automatic voice blending with multi-speaker normalized weights (Voice 1, 2, 3).
    - Bi-directional synchronization between individual voice controls and Kokoro `spec`.
    - Live daemon socket IPC reload on every applied mutation.
    - Status telemetry injection for active PID, systemd socket standby, and GPU D3 sleep.
    """

    def __init__(self, config_path: str = "") -> None:
        target = Path(config_path).expanduser().resolve() if config_path else _get_default_config_path()
        super().__init__(config_path=str(target))

    def _ensure_virgin_config(self) -> None:
        """Auto-creates the default config.toml if it does not exist, and symlinks contained_apps."""
        if not self.config_path.exists():
            try:
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                self.config_path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
            except OSError as e:
                print(f"[KokoroEngine] Warning: Could not create virgin config: {e}")

        # Ensure convenience symlink in contained_apps if directory exists
        contained_dir = Path.home() / "contained_apps" / "uv" / "dusky_kokoro"
        if contained_dir.exists():
            contained_cfg = contained_dir / "config.toml"
            if not contained_cfg.exists():
                try:
                    contained_cfg.symlink_to(self.config_path)
                except OSError:
                    pass

    @staticmethod
    def compute_voice_spec(
        blend: bool,
        voice_1: str,
        weight_1: float,
        voice_2: str,
        weight_2: float,
        voice_3: str,
        weight_3: float,
    ) -> str:
        """Calculates mathematically normalized voice spec string (e.g. 'af_heart:0.40,af_bella:0.60')."""
        v1 = voice_1.strip().strip('"\'') or "af_heart"
        v2 = voice_2.strip().strip('"\'') or "af_bella"
        v3 = voice_3.strip().strip('"\'') or "none"

        if not blend:
            return v1
        entries = [(v, w) for v, w in ((v1, weight_1), (v2, weight_2), (v3, weight_3))
                   if v != "none" and math.isfinite(w) and w > 0]
        if not entries:
            return v1
        if len(entries) == 1:
            return entries[0][0]
        scale = max(w for _, w in entries)
        total = sum(w / scale for _, w in entries)
        return ",".join(f"{v}:{w / scale / total:.8g}" for v, w in entries)

    @staticmethod
    def parse_voice_spec(spec: str) -> dict[str, Any]:
        """Decomposes a Kokoro spec string into individual voice blend controls."""
        clean = spec.strip().strip('"\'')
        parts = [p.strip() for p in clean.split(",") if p.strip()]

        if len(parts) == 1:
            v = parts[0].split(":")[0]
            return {
                "blend": False,
                "voice_1": v,
                "weight_1": 1.00,
                "voice_2": "af_bella",
                "weight_2": 0.00,
                "voice_3": "none",
                "weight_3": 0.00,
            }
        elif len(parts) == 2:
            p0, p1 = parts[0], parts[1]
            v0 = p0.split(":")[0]
            try:
                w0 = float(p0.split(":")[1]) if ":" in p0 else 0.50
            except ValueError:
                w0 = 0.50
            v1 = p1.split(":")[0]
            try:
                w1 = float(p1.split(":")[1]) if ":" in p1 else 0.50
            except ValueError:
                w1 = 0.50
            return {
                "blend": True,
                "voice_1": v0,
                "weight_1": w0,
                "voice_2": v1,
                "weight_2": w1,
                "voice_3": "none",
                "weight_3": 0.00,
            }
        elif len(parts) >= 3:
            p0, p1, p2 = parts[0], parts[1], parts[2]
            v0 = p0.split(":")[0]
            try:
                w0 = float(p0.split(":")[1]) if ":" in p0 else 0.40
            except ValueError:
                w0 = 0.40
            v1 = p1.split(":")[0]
            try:
                w1 = float(p1.split(":")[1]) if ":" in p1 else 0.40
            except ValueError:
                w1 = 0.40
            v2 = p2.split(":")[0]
            try:
                w2 = float(p2.split(":")[1]) if ":" in p2 else 0.20
            except ValueError:
                w2 = 0.20
            return {
                "blend": True,
                "voice_1": v0,
                "weight_1": w0,
                "voice_2": v1,
                "weight_2": w1,
                "voice_3": v2,
                "weight_3": w2,
            }

        return {
            "blend": True,
            "voice_1": "af_heart",
            "weight_1": 0.40,
            "voice_2": "af_bella",
            "weight_2": 0.60,
            "voice_3": "none",
            "weight_3": 0.00,
        }

    def _socket_path(self) -> Path:
        configured = self.cache.get("daemon.socket_path") or self.cache.get("daemon/socket_path")
        value = os.environ.get("DUSKY_SOCKET") or configured
        if value:
            return Path(os.path.expandvars(str(value))).expanduser()
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        return Path(runtime) / "dusky-kokoro/control.sock"

    def _trigger_reload(self) -> None:
        """Attempts live socket IPC reload, then falls back to trigger.sh --reload."""
        sock_path = self._socket_path()
        if sock_path.exists():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(0.4)
                    s.connect(str(sock_path))
                    s.sendall(b'{"cmd": "reload"}\n')
                    s.recv(1024)
                    return
            except Exception:
                pass

        trigger_sh = Path(shutil.which("dusky-kokoro") or
                          Path.home() / "user_scripts" / "tts_stt" / "dusky_kokoro" / "trigger.sh")
        if trigger_sh.exists() and os.access(trigger_sh, os.X_OK):
            try:
                subprocess.Popen(
                    [str(trigger_sh), "--reload"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception:
                pass

    def load_state(self) -> dict[str, Any]:
        self._ensure_virgin_config()
        state = super().load_state()

        # If spec exists, decompose it into individual voice controls if missing
        spec_val = self.cache.get("voice.spec") or self.cache.get("voice/spec") or self.cache.get("spec")
        if spec_val and isinstance(spec_val, str):
            decomposed = self.parse_voice_spec(spec_val)
            for k, val in decomposed.items():
                full_dot = f"voice.{k}"
                full_slash = f"voice/{k}"
                if full_dot not in self.cache:
                    self.cache[full_dot] = val
                if full_slash not in self.cache:
                    self.cache[full_slash] = val
                if k not in self.cache:
                    self.cache[k] = val

        # Status telemetry injection
        pid_file = self._socket_path().with_name("daemon.pid")
        is_running = False
        status_str = "STOPPED"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)
                is_running = True
                status_str = f"RUNNING (PID {pid})"
            except (ValueError, OSError):
                pass

        if not is_running:
            try:
                res = subprocess.run(
                    ["systemctl", "--user", "is-active", "dusky_kokoro.service"],
                    capture_output=True,
                    text=True,
                    timeout=1.0,
                )
                if res.returncode == 0:
                    is_running = True
                    status_str = "RUNNING (systemd)"
                else:
                    res_sock = subprocess.run(
                        ["systemctl", "--user", "is-active", "dusky_kokoro.socket"],
                        capture_output=True,
                        text=True,
                        timeout=1.0,
                    )
                    if res_sock.returncode == 0:
                        status_str = "STANDBY (Socket)"
            except Exception:
                pass

        provider = str(self.cache.get("engine.provider", "auto"))
        vendor = {"cuda": "0x10de", "tensorrt": "0x10de", "migraphx": "0x1002", "openvino": "0x8086"}.get(provider)
        states = []
        if provider != "cpu":
            for device in sorted(Path("/sys/class/drm").glob("card[0-9]*/device")):
                if not re.fullmatch(r"card\d+", device.parent.name):
                    continue
                try:
                    if vendor and (device / "vendor").read_text().strip() != vendor:
                        continue
                    state = (device / "power_state").read_text().strip()
                    states.append(f"{device.parent.name}: {state}")
                except OSError:
                    pass
        gpu_state = "; ".join(states) or ("Not used (CPU)" if provider == "cpu" else "Unknown")

        self.cache["daemon.status"] = status_str
        self.cache["daemon/status"] = status_str
        self.cache["daemon.is_running"] = is_running
        self.cache["daemon/is_running"] = is_running
        self.cache["daemon.gpu_power_state"] = gpu_state
        self.cache["daemon/gpu_power_state"] = gpu_state

        return self.cache

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No changes.", ""

        # Check if any voice blend parameters are changing
        voice_keys = {"blend", "voice_1", "weight_1", "voice_2", "weight_2", "voice_3", "weight_3"}
        has_voice_change = False

        v_dict = {
            "blend": bool(self.cache.get("voice.blend", True)),
            "voice_1": str(self.cache.get("voice.voice_1", "af_heart")),
            "weight_1": float(self.cache.get("voice.weight_1", 0.40)),
            "voice_2": str(self.cache.get("voice.voice_2", "af_bella")),
            "weight_2": float(self.cache.get("voice.weight_2", 0.60)),
            "voice_3": str(self.cache.get("voice.voice_3", "none")),
            "weight_3": float(self.cache.get("voice.weight_3", 0.00)),
        }

        for key, scope, val, itype in changes:
            if key in voice_keys and (scope == "voice" or scope == "DEFAULT"):
                has_voice_change = True
                if key == "blend":
                    v_dict["blend"] = str(val).lower() in {"true", "1", "yes", "on"} if isinstance(val, str) else bool(val)
                elif key in {"weight_1", "weight_2", "weight_3"}:
                    try:
                        v_dict[key] = float(val)
                    except (ValueError, TypeError):
                        pass
                else:
                    v_dict[key] = str(val).strip().strip('"\'')

        updated_changes = list(changes)
        if has_voice_change:
            new_spec = self.compute_voice_spec(
                blend=v_dict["blend"],
                voice_1=v_dict["voice_1"],
                weight_1=v_dict["weight_1"],
                voice_2=v_dict["voice_2"],
                weight_2=v_dict["weight_2"],
                voice_3=v_dict["voice_3"],
                weight_3=v_dict["weight_3"],
            )
            # Ensure spec is updated in the batch write
            updated_changes = [c for c in updated_changes if not (c[0] == "spec" and c[1] == "voice")]
            updated_changes.append(("spec", "voice", new_spec, "string"))

        ok, msg, debug = super().write_batch(updated_changes)
        if ok:
            self._trigger_reload()
            return True, f"{msg} (Daemon hot-reloaded)", debug

        return ok, msg, debug
