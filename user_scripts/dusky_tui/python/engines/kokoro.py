#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: DUSKY KOKORO TTS CONFIGURATION ENGINE
===============================================================================
Engine Type: "kokoro" (alias: "dusky_kokoro")
Target: ~/.config/dusky-kokoro/config.toml (v5.2.0 schema, Python 3.15+)
===============================================================================
Inherits from TomlEngine, adding:
- Bi-directional multi-voice blending and weight normalization
- Automatic segment-budget invariant preservation (max >= target)
- Virgin v5.2.0 config.toml auto-creation matching dusky_main.py
- Zero-wake daemon liveness detection (fcntl.flock on daemon.lock)
- Live Unix socket IPC hot-reload and status telemetry without waking idle units
===============================================================================
"""

lazy import fcntl
lazy import json
lazy import math
import os
lazy import socket
lazy import subprocess
lazy from pathlib import Path
lazy from typing import Any

from python.engines.toml import TomlEngine

_PROVIDER_VENDORS = frozendict({
    "cuda": "0x10de",
    "tensorrt": "0x10de",
    "migraphx": "0x1002",
    "openvino": "0x8086",
})
_DISPLAY_CLASSES = ("0x0300", "0x0302", "0x0380")
_VOICE_PART_KEYS = frozenset({"blend", "voice_1", "weight_1", "voice_2", "weight_2", "voice_3", "weight_3"})


def _get_default_config_path() -> Path:
    if env_cfg := os.environ.get("DUSKY_CONFIG"):
        return Path(os.path.expandvars(env_cfg)).expanduser().resolve()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return (base / "dusky-kokoro" / "config.toml").resolve()


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except TypeError, ValueError:
        return default
    return parsed if math.isfinite(parsed) else default


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except TypeError, ValueError:
        return default


DEFAULT_CONFIG_TEMPLATE = """# Dusky Kokoro TTS - configuration (TOML)
# Location: ~/.config/dusky-kokoro/config.toml   (override with DUSKY_CONFIG)
# Every key is optional; omitted keys use the built-in defaults shown here.
# Apply changes with:  trigger.sh --reload   (or: systemctl --user restart dusky_kokoro)

[engine]
provider = "auto"          # auto | cuda | tensorrt | migraphx | openvino | cpu
precision = "auto"         # auto | f32 | fp16 | fp16-gpu | int8  (auto: tested fp16-gpu on CPU/CUDA; f32 on OpenVINO)
models_dir = ""            # "" = <install dir>/models
voices_file = ""           # "" = <models_dir>/voices-v1.0.bin
device_id = 0
gpu_mem_limit_mb = 2048    # provider arena cap, NOT total VRAM (0 = unlimited)
cudnn_conv_use_max_workspace = false # cap cuDNN workspace to 32 MiB; true may need several GiB
worker_start_timeout_s = 120.0
arena_extend_strategy = "kSameAsRequested"   # or kNextPowerOfTwo
cudnn_conv_algo_search = "HEURISTIC"         # HEURISTIC | DEFAULT | EXHAUSTIVE
cuda_lib_dirs = []         # explicit dirs for onnxruntime.preload_dlls(); [] = NVIDIA pip wheels inside the venv
openvino_device = "GPU"    # GPU | GPU.1 | NPU | CPU | AUTO:GPU,CPU
openvino_precision = "FP16"
openvino_cache_dir = ""    # "" = ~/.cache/dusky-kokoro/openvino
tensorrt_cache_dir = ""    # "" = ~/.cache/dusky-kokoro/tensorrt
tensorrt_profile_min = "input_ids:1x2,style:1x256,speed:1"
tensorrt_profile_opt = "input_ids:1x160,style:1x256,speed:1"
tensorrt_profile_max = "input_ids:1x512,style:1x256,speed:1"
intra_op_threads = 0       # 0 = up to 8 available CPUs; GPU providers use 2
allow_spinning = true
graph_optimization = "all" # all | extended | basic | disabled
require_accelerator = false   # true: refuse to run on the CPU when a GPU provider was requested
warmup = false             # false avoids adding a throwaway synthesis pass onto cold first-request latency
model_idle_timeout_s = 30.0   # unload the model after this much inactivity (0.0 = immediately after synthesis)
profiling = false          # write ONNX Runtime JSON profiles to the cache dir

[engine.env]               # extra environment for the inference runtime, applied before ONNX Runtime loads
# HSA_OVERRIDE_GFX_VERSION = "10.3.0"   # ROCm on consumer RDNA2 (gfx103x)
# MIOPEN_FIND_MODE = "FAST"

[voice]
spec = "af_heart:0.4,af_bella:0.6"   # "name" or "name:weight,name:weight" ("" auto-compiles from voice_1..3)
blend = true                         # true | false
voice_1 = "af_heart"
weight_1 = 0.4
voice_2 = "af_bella"
weight_2 = 0.6
voice_3 = "none"
weight_3 = 0.0
lang = "auto"                        # auto = from the first voice prefix (a en-us, b en-gb, j ja, z cmn, e es, f fr-fr, h hi, i it, p pt-br)
speed = 1.0                          # Kokoro duration-model speed, 0.5 - 2.0

[text]
max_chars = 10000000       # 10M chars (~2,500 pages); 0 = unlimited
url_mode = "domain"        # domain | placeholder | omit
url_placeholder = "link"
emoji_mode = "strip"       # strip | name
read_code_blocks = false
strip_citations = true     # remove [12], [4, 5], [1-3], [^3] style markers
target_segment_chars = 220
max_segment_chars = 320
min_segment_chars = 24
first_segment_max_chars = 110   # keep the first segment short for fast time-to-first-audio
max_phonemes = 480         # hard token guard (model context is 512 incl. padding)
sentence_pause_ms = 140
paragraph_pause_ms = 380
trim_silence = true

[playback]
mpv_binary = "mpv"
mpv_speed = 1.0            # second-stage tempo (scaletempo2, pitch-preserving)
window = true              # show the small mpv window (space = pause, q = stop)
window_geometry = "420x96"
window_title = "Kokoro TTS"
audio_device = ""          # mpv --audio-device (list with: mpv --audio-device=help)
volume = 100
cache_max_mb = 512         # mpv demuxer cache: generation may run this far ahead of playback
use_user_mpv_config = false   # false (--no-config) avoids video scripts/keep-open conflicts and /tmp/mpv.sock collisions
extra_args = []
prefetch_segments = 4      # synthesized segments buffered ahead of the player
write_stall_timeout_s = 0.0   # abort if mpv stops consuming audio for this long (0 = never)

[archive]
enabled = true
dir = ""                   # "" = ~/.cache/dusky-kokoro/audio
max_files = 32
bit_depth = 16             # 16 | 24 (also sets worker -> daemon -> mpv wire PCM bit depth)

[daemon]
socket_path = ""           # "" = $XDG_RUNTIME_DIR/dusky-kokoro/control.sock
default_mode = "interrupt" # interrupt | enqueue
dedup_window_s = 2.0
max_queue = 8
process_idle_timeout_s = 30.0
exit_when_idle = true      # exit after idling: releases the CUDA/HIP driver context entirely; relaunched on demand
desktop_notifications = true
request_timeout_s = 10.0
max_request_bytes = 67108864

[logging]
level = "INFO"             # DEBUG | INFO | WARNING | ERROR
file = ""                  # optional rotating log file (journald already captures stderr)
file_max_mb = 5
ort_verbose = false        # ONNX Runtime verbose session logs
"""


class KokoroEngine(TomlEngine):
    """
    Dusky Kokoro Neural TTS Configuration Engine (v5.2.0).

    Features:
    - Pure TOML backend with atomic disk commits and mtime conflict detection.
    - Bi-directional sync between individual voice controls (V1/V2/V3) and `voice.spec`.
    - Zero-wake liveness check via fcntl.flock on daemon.lock (never cold-boots idle socket units).
    - Live Unix socket IPC hot-reload and live status / GPU D3cold telemetry.
    """

    def __init__(self, config_path: str = "") -> None:
        target = Path(config_path).expanduser().resolve() if config_path else _get_default_config_path()
        super().__init__(config_path=str(target))

    def _cached(self, section: str, key: str, default: Any = None) -> Any:
        dot = f"{section}.{key}"
        if dot in self.cache:
            return self.cache[dot]
        slash = f"{section}/{key}"
        if slash in self.cache:
            return self.cache[slash]
        return default

    def _ensure_virgin_config(self) -> None:
        """Auto-creates the v5.2.0 default config.toml if it does not exist."""
        if not self.config_path.exists():
            try:
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                self.config_path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
            except OSError as exc:
                print(f"[KokoroEngine] Warning: Could not create virgin config: {exc}")

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
        """Calculates a normalized voice spec string (e.g. 'af_heart:0.4,af_bella:0.6')."""
        v1 = voice_1.strip().strip('"\'') or "af_heart"
        v2 = voice_2.strip().strip('"\'') or "af_bella"
        v3 = voice_3.strip().strip('"\'') or "none"

        if not blend:
            return v1

        merged: dict[str, float] = {}
        for voice, weight in ((v1, weight_1), (v2, weight_2), (v3, weight_3)):
            if voice and voice.lower() != "none" and math.isfinite(weight) and weight > 0:
                merged[voice] = merged.get(voice, 0.0) + weight

        if not merged:
            return v1
        if len(merged) == 1:
            return next(iter(merged))

        entries = list(merged.items())
        scale = max(w for _, w in entries)
        total = sum(w / scale for _, w in entries)
        return ",".join(f"{v}:{round((w / scale) / total, 4):g}" for v, w in entries)

    @staticmethod
    def parse_voice_spec(spec: str) -> dict[str, Any]:
        """Decomposes a Kokoro spec string into normalized UI voice blend controls."""
        clean = spec.strip().strip('"\'')
        parsed: list[tuple[str, float]] = []
        for raw in clean.split(","):
            raw = raw.strip()
            if not raw:
                continue
            name, sep, raw_w = raw.partition(":")
            name = name.strip()
            if not name or name.lower() == "none":
                continue
            w = _as_float(raw_w.strip(), 1.0) if sep and raw_w.strip() else 1.0
            if w <= 0:
                continue
            parsed.append((name, w))

        if not parsed:
            return {
                "blend": True,
                "voice_1": "af_heart",
                "weight_1": 0.4,
                "voice_2": "af_bella",
                "weight_2": 0.6,
                "voice_3": "none",
                "weight_3": 0.0,
            }

        if len(parsed) == 1:
            return {
                "blend": False,
                "voice_1": parsed[0][0],
                "weight_1": 1.0,
                "voice_2": "af_bella",
                "weight_2": 0.0,
                "voice_3": "none",
                "weight_3": 0.0,
            }

        top = parsed[:3]
        scale = max(w for _, w in top)
        total = sum(w / scale for _, w in top)
        norm = [(v, round((w / scale) / total, 2)) for v, w in top]

        v1, w1 = norm[0]
        v2, w2 = norm[1]
        v3, w3 = norm[2] if len(norm) == 3 else ("none", 0.0)
        return {
            "blend": True,
            "voice_1": v1,
            "weight_1": w1,
            "voice_2": v2,
            "weight_2": w2,
            "voice_3": v3,
            "weight_3": w3,
        }

    def _socket_path(self) -> Path:
        configured = self._cached("daemon", "socket_path")
        value = os.environ.get("DUSKY_SOCKET") or configured
        if value:
            return Path(os.path.expandvars(str(value))).expanduser()
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        return Path(runtime) / "dusky-kokoro" / "control.sock"

    @staticmethod
    def _daemon_lock_pid(sock_path: Path) -> int | None:
        """Zero-wake kernel check: returns daemon PID (>0 or 0) iff daemon.lock is held."""
        lock_path = sock_path.with_name("daemon.lock")
        try:
            fd = os.open(lock_path, os.O_RDONLY | os.O_CLOEXEC)
        except OSError:
            return None
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raw = os.read(fd, 32).decode("ascii", "ignore").strip()
                try:
                    return int(raw)
                except ValueError:
                    return 0
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None
        except OSError:
            return None
        finally:
            os.close(fd)

    @staticmethod
    def _uds_request(sock_path: Path, payload: dict[str, Any], timeout: float = 0.8) -> dict[str, Any] | None:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect(str(sock_path))
                sock.sendall(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
                sock.shutdown(socket.SHUT_WR)
                with sock.makefile("rb") as stream:
                    line = stream.readline()
                if not line:
                    return None
                reply = json.loads(line)
                return reply if isinstance(reply, dict) else None
        except OSError, ValueError, TimeoutError:
            return None

    def _trigger_reload(self) -> tuple[bool, dict[str, Any] | None]:
        """Hot-reloads the daemon over UDS ONLY if it is already running (zero-wake)."""
        sock_path = self._socket_path()
        if self._daemon_lock_pid(sock_path) is None:
            return False, None
        return True, self._uds_request(sock_path, {"cmd": "reload", "client": "tui"}, timeout=1.5)

    def _gpu_power_state(self, provider: str) -> str:
        if provider == "cpu":
            return "Not used (CPU)"
        vendor = _PROVIDER_VENDORS.get(provider)
        states: list[str] = []
        drm_root = Path("/sys/class/drm")
        if drm_root.is_dir():
            for device in sorted(drm_root.glob("card[0-9]*/device")):
                card_name = device.parent.name
                if not (card_name.startswith("card") and card_name[4:].isdigit()):
                    continue
                try:
                    pci_class = (device / "class").read_text().strip()
                    if not pci_class.startswith(_DISPLAY_CLASSES):
                        continue
                    if vendor and (device / "vendor").read_text().strip() != vendor:
                        continue
                    power = (device / "power_state").read_text().strip()
                    states.append(f"{card_name}: {power}")
                except OSError:
                    continue
        return "; ".join(states) or "Unknown"

    def load_state(self) -> dict[str, Any]:
        self._ensure_virgin_config()
        super().load_state()

        # Fill missing voice UI keys from voice.spec without clobbering stored secondary voices
        spec_val = self._cached("voice", "spec") or self.cache.get("spec")
        if spec_val and isinstance(spec_val, str):
            decomposed = self.parse_voice_spec(spec_val)
            for key, val in decomposed.items():
                for cache_key in (f"voice.{key}", f"voice/{key}", key):
                    if cache_key not in self.cache:
                        self.cache[cache_key] = val

        # Zero-wake daemon status telemetry
        sock_path = self._socket_path()
        pid = self._daemon_lock_pid(sock_path)
        is_running = False
        status_str = "STOPPED"

        if pid is not None:
            is_running = True
            st = self._uds_request(sock_path, {"cmd": "status", "client": "tui"}, timeout=0.35)
            if st and st.get("ok"):
                live_pid = st.get("pid") or pid
                eng = st.get("engine") if isinstance(st.get("engine"), dict) else {}
                mode = eng.get("active_kind") if eng.get("loaded") else "idle"
                status_str = f"RUNNING (PID {live_pid} · {mode})"
            else:
                status_str = f"RUNNING (PID {pid})" if pid > 0 else "RUNNING"
        else:
            try:
                res = subprocess.run(
                    ["systemctl", "--user", "is-active", "dusky_kokoro.service", "dusky_kokoro.socket"],
                    capture_output=True,
                    text=True,
                    timeout=0.8,
                )
                lines = res.stdout.splitlines()
                svc_state = lines[0].strip() if len(lines) >= 1 else ""
                sock_state = lines[1].strip() if len(lines) >= 2 else ""
                if svc_state == "active":
                    is_running = True
                    status_str = "RUNNING (systemd)"
                elif svc_state == "activating":
                    is_running = True
                    status_str = "STARTING (systemd)"
                elif sock_state == "active":
                    status_str = "STANDBY (Socket)"
            except OSError, subprocess.SubprocessError:
                pass

        provider = str(self._cached("engine", "provider", "auto"))
        gpu_state = self._gpu_power_state(provider)

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

        has_voice_parts = False
        explicit_spec: str | None = None

        v_dict: dict[str, Any] = {
            "blend": _as_bool(self._cached("voice", "blend", True), True),
            "voice_1": str(self._cached("voice", "voice_1", "af_heart")),
            "weight_1": _as_float(self._cached("voice", "weight_1", 0.4), 0.4),
            "voice_2": str(self._cached("voice", "voice_2", "af_bella")),
            "weight_2": _as_float(self._cached("voice", "weight_2", 0.6), 0.6),
            "voice_3": str(self._cached("voice", "voice_3", "none")),
            "weight_3": _as_float(self._cached("voice", "weight_3", 0.0), 0.0),
        }

        target_seg = _as_int(self._cached("text", "target_segment_chars", 220), 220)
        max_seg = _as_int(self._cached("text", "max_segment_chars", 320), 320)
        edited_target = False
        edited_max = False

        for key, scope, val, _itype in changes:
            if scope in {"voice", "DEFAULT"}:
                if key in _VOICE_PART_KEYS:
                    has_voice_parts = True
                    if key == "blend":
                        v_dict["blend"] = _as_bool(val, v_dict["blend"])
                    elif key in {"weight_1", "weight_2", "weight_3"}:
                        v_dict[key] = _as_float(val, v_dict[key])
                    else:
                        v_dict[key] = str(val).strip().strip('"\'')
                elif key == "spec":
                    explicit_spec = str(val).strip().strip('"\'')
            elif scope in {"text", "DEFAULT"}:
                if key == "target_segment_chars":
                    target_seg = _as_int(val, target_seg)
                    edited_target = True
                elif key == "max_segment_chars":
                    max_seg = _as_int(val, max_seg)
                    edited_max = True

        updated_changes = list(changes)

        if has_voice_parts:
            new_spec = self.compute_voice_spec(
                blend=v_dict["blend"],
                voice_1=v_dict["voice_1"],
                weight_1=v_dict["weight_1"],
                voice_2=v_dict["voice_2"],
                weight_2=v_dict["weight_2"],
                voice_3=v_dict["voice_3"],
                weight_3=v_dict["weight_3"],
            )
            updated_changes = [c for c in updated_changes if not (c[0] == "spec" and c[1] in {"voice", "DEFAULT"})]
            updated_changes.append(("spec", "voice", new_spec, "string"))
        elif explicit_spec is not None:
            if not explicit_spec:
                recompiled = self.compute_voice_spec(
                    blend=v_dict["blend"],
                    voice_1=v_dict["voice_1"],
                    weight_1=v_dict["weight_1"],
                    voice_2=v_dict["voice_2"],
                    weight_2=v_dict["weight_2"],
                    voice_3=v_dict["voice_3"],
                    weight_3=v_dict["weight_3"],
                )
                updated_changes = [c for c in updated_changes if not (c[0] == "spec" and c[1] in {"voice", "DEFAULT"})]
                updated_changes.append(("spec", "voice", recompiled, "string"))
            else:
                decomposed = self.parse_voice_spec(explicit_spec)
                normalized_spec = self.compute_voice_spec(**decomposed)
                updated_changes = [
                    c for c in updated_changes
                    if not (c[1] in {"voice", "DEFAULT"} and (c[0] == "spec" or c[0] in _VOICE_PART_KEYS))
                ]
                updated_changes.extend([
                    ("blend", "voice", decomposed["blend"], "bool"),
                    ("voice_1", "voice", decomposed["voice_1"], "picker"),
                    ("weight_1", "voice", decomposed["weight_1"], "float"),
                    ("voice_2", "voice", decomposed["voice_2"], "picker"),
                    ("weight_2", "voice", decomposed["weight_2"], "float"),
                    ("voice_3", "voice", decomposed["voice_3"], "picker"),
                    ("weight_3", "voice", decomposed["weight_3"], "float"),
                    ("spec", "voice", normalized_spec, "string"),
                ])

        # Preserve dusky_main.py invariant: max_segment_chars >= target_segment_chars
        if (edited_target or edited_max) and max_seg < target_seg:
            if edited_max and not edited_target:
                updated_changes = [c for c in updated_changes if not (c[0] == "target_segment_chars" and c[1] in {"text", "DEFAULT"})]
                updated_changes.append(("target_segment_chars", "text", max_seg, "int"))
            else:
                updated_changes = [c for c in updated_changes if not (c[0] == "max_segment_chars" and c[1] in {"text", "DEFAULT"})]
                updated_changes.append(("max_segment_chars", "text", target_seg, "int"))

        ok, msg, debug = super().write_batch(updated_changes)
        if not ok:
            return ok, msg, debug

        was_running, reply = self._trigger_reload()
        if not was_running:
            return True, f"{msg} (Saved; applies on next start)", debug
        if reply is None:
            return True, f"{msg} (Saved; daemon reload timed out)", debug
        if not reply.get("ok"):
            return True, f"{msg} (Saved; reload error: {reply.get('error', 'unknown')})", debug
        if reply.get("engine_changed"):
            return True, f"{msg} (Daemon reloaded; engine updated)", debug
        return True, f"{msg} (Daemon hot-reloaded)", debug
