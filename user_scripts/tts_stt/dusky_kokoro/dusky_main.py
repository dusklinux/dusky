#!/usr/bin/env python3
# =============================================================================
#  dusky_main.py - Dusky Kokoro TTS  (daemon + control client, single file)
#  Target: Arch Linux rolling (2026+), CPython 3.14+, uv-managed virtualenv.
#  License: MIT
# =============================================================================
"""
Dusky Kokoro TTS v5.2

Process model
-------------
* One asyncio event loop owns ALL mutable state (jobs, player, timers, sockets)
  and stays pure-stdlib (zero numpy/onnxruntime imports in the daemon process).
* A separate synthesis process (`synth-worker`) owns ONNX Runtime, numpy,
  kokoro-onnx, and espeak-ng. It quantizes float32 waveforms once into s16le or
  s24le PCM so both mpv playback and WAV archiving consume the exact same bytes.
  Interrupting an idle worker leaves it warm in memory; interrupting an active
  segment signals a lightweight abort pipe so ORT aborts in-flight via
  RunOptions.terminate without forcing a cold reload. Unloading or idle exit
  terminates the worker process to release GPU driver contexts completely.
* mpv is a per-utterance child process: raw s16le/s24le PCM goes in through
  stdin, control (pause / quit / observe) goes through a socketpair passed as
  --input-ipc-client=fd://N. mpv exits automatically when that socket closes.
* Control plane: newline-delimited JSON over a Unix domain socket inside
  XDG_RUNTIME_DIR with systemd socket activation (sd_listen_fds).

A SyntaxError on import means the interpreter is older than 3.14: PEP 758
except-clauses without parentheses are used deliberately.
"""

import argparse
import asyncio
import contextlib
import dataclasses
import fcntl
import gc
import hashlib
import html
import json
import logging
import logging.handlers
import math
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unicodedata
import wave
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Final, NoReturn, get_args, get_origin, get_type_hints
from urllib.parse import urlsplit

VERSION: Final = "5.2.0"
PROTOCOL: Final = 1
APP_NAME: Final = "dusky-kokoro"
APP_DIR: Final = Path(__file__).resolve().parent
SAMPLE_RATE: Final = 24_000
TERMINAL_EVENTS: Final = frozenset({"finished", "cancelled", "error", "deduplicated"})
CLIENT_ENV_KEYS: Final = (
    "WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
    "DBUS_SESSION_BUS_ADDRESS", "PULSE_SERVER", "PIPEWIRE_REMOTE",
)
MODEL_FILES: Final[dict[str, str]] = {
    "f32": "kokoro-v1.0.onnx",
    "fp16": "kokoro-v1.0.fp16.onnx",
    "fp16-gpu": "kokoro-v1.0.fp16-gpu.onnx",
    "int8": "kokoro-v1.0.int8.onnx",
}
VOICES_FILENAME: Final = "voices-v1.0.bin"
EP_NAMES: Final[dict[str, str]] = {
    "cuda": "CUDAExecutionProvider",
    "tensorrt": "TensorrtExecutionProvider",
    "migraphx": "MIGraphXExecutionProvider",
    "openvino": "OpenVINOExecutionProvider",
    "cpu": "CPUExecutionProvider",
}
GPU_KINDS: Final = frozenset({"cuda", "tensorrt", "migraphx"})
# Kokoro voice prefix -> espeak-ng language code used by kokoro-onnx (Mandarin is "cmn", not "zh").
LANG_BY_PREFIX: Final[dict[str, str]] = {
    "a": "en-us", "b": "en-gb", "j": "ja", "z": "cmn", "e": "es",
    "f": "fr-fr", "h": "hi", "i": "it", "p": "pt-br",
}

log = logging.getLogger("dusky")


def sample_width_for(bit_depth: int) -> int:
    return 3 if bit_depth == 24 else 2


def pcm_format_for(bit_depth: int) -> str:
    return "s24le" if bit_depth == 24 else "s16le"


# =============================================================================
#  Errors
# =============================================================================
class DuskyError(Exception):
    """Base class for user-facing errors (message is safe to show verbatim)."""


class ConfigError(DuskyError):
    pass


class EngineError(DuskyError):
    pass


class InferenceError(EngineError):
    """The model returned unusable audio."""


class VoiceError(DuskyError):
    pass


class PlayerClosed(DuskyError):
    """mpv went away (user closed the window / pressed q)."""


class PlayerError(DuskyError):
    """mpv failed (no audio device, bad option, ...)."""


class ClientError(DuskyError):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# =============================================================================
#  XDG paths
# =============================================================================
def xdg_runtime_dir() -> Path:
    if value := os.environ.get("XDG_RUNTIME_DIR"):
        return Path(value)
    return Path(f"/run/user/{os.getuid()}")


def xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def xdg_cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")


def default_socket_path() -> Path:
    if value := os.environ.get("DUSKY_SOCKET"):
        return Path(value)
    return xdg_runtime_dir() / APP_NAME / "control.sock"


def default_config_path() -> Path:
    if value := os.environ.get("DUSKY_CONFIG"):
        return Path(value)
    return xdg_config_home() / APP_NAME / "config.toml"


# =============================================================================
#  Configuration (TOML -> frozen dataclasses; unknown keys are hard errors)
# =============================================================================
@dataclass(slots=True, frozen=True, kw_only=True)
class EngineConfig:
    provider: str = "auto"                 # auto | cuda | tensorrt | migraphx | openvino | cpu
    precision: str = "auto"                # auto | f32 | fp16 | fp16-gpu | int8
    models_dir: str = ""
    voices_file: str = ""
    device_id: int = 0
    gpu_mem_limit_mb: int = 2048
    cudnn_conv_use_max_workspace: bool = False
    worker_start_timeout_s: float = 120.0
    arena_extend_strategy: str = "kSameAsRequested"
    cudnn_conv_algo_search: str = "HEURISTIC"
    cuda_lib_dirs: tuple[str, ...] = ()
    openvino_device: str = "GPU"
    openvino_precision: str = "FP16"
    openvino_cache_dir: str = ""
    tensorrt_cache_dir: str = ""
    tensorrt_profile_min: str = "input_ids:1x2,style:1x256,speed:1"
    tensorrt_profile_opt: str = "input_ids:1x160,style:1x256,speed:1"
    tensorrt_profile_max: str = "input_ids:1x512,style:1x256,speed:1"
    intra_op_threads: int = 0
    allow_spinning: bool = True
    graph_optimization: str = "all"        # all | extended | basic | disabled
    require_accelerator: bool = False
    warmup: bool = False
    model_idle_timeout_s: float = 30.0
    profiling: bool = False
    env: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True, frozen=True, kw_only=True)
class VoiceConfig:
    spec: str = "af_heart:0.4,af_bella:0.6"
    lang: str = "auto"
    speed: float = 1.0
    blend: bool = True
    voice_1: str = "af_heart"
    weight_1: float = 0.4
    voice_2: str = "af_bella"
    weight_2: float = 0.6
    voice_3: str = "none"
    weight_3: float = 0.0


@dataclass(slots=True, frozen=True, kw_only=True)
class TextConfig:
    max_chars: int = 10_000_000            # 10 million chars (~2,500 pages); 0 = unlimited
    url_mode: str = "domain"               # domain | placeholder | omit
    url_placeholder: str = "link"
    emoji_mode: str = "strip"              # strip | name
    read_code_blocks: bool = False
    strip_citations: bool = True
    target_segment_chars: int = 220
    max_segment_chars: int = 320
    min_segment_chars: int = 24
    first_segment_max_chars: int = 110
    max_phonemes: int = 480
    sentence_pause_ms: int = 140
    paragraph_pause_ms: int = 380
    trim_silence: bool = True


@dataclass(slots=True, frozen=True, kw_only=True)
class PlaybackConfig:
    mpv_binary: str = "mpv"
    mpv_speed: float = 1.0
    window: bool = True
    window_geometry: str = "420x96"
    window_title: str = "Kokoro TTS"
    audio_device: str = ""
    volume: int = 100
    cache_max_mb: int = 512
    use_user_mpv_config: bool = False
    extra_args: tuple[str, ...] = ()
    prefetch_segments: int = 4
    write_stall_timeout_s: float = 0.0


@dataclass(slots=True, frozen=True, kw_only=True)
class ArchiveConfig:
    enabled: bool = True
    dir: str = ""
    max_files: int = 32
    bit_depth: int = 16


@dataclass(slots=True, frozen=True, kw_only=True)
class DaemonConfig:
    socket_path: str = ""
    default_mode: str = "interrupt"        # interrupt | enqueue
    dedup_window_s: float = 2.0
    max_queue: int = 8
    process_idle_timeout_s: float = 30.0
    exit_when_idle: bool = True
    desktop_notifications: bool = True
    request_timeout_s: float = 10.0
    max_request_bytes: int = 64 * 1024 * 1024


@dataclass(slots=True, frozen=True, kw_only=True)
class LoggingConfig:
    level: str = "INFO"
    file: str = ""
    file_max_mb: int = 5
    ort_verbose: bool = False


@dataclass(slots=True, frozen=True, kw_only=True)
class Config:
    engine: EngineConfig = field(default_factory=EngineConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    text: TextConfig = field(default_factory=TextConfig)
    playback: PlaybackConfig = field(default_factory=PlaybackConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


_SECTIONS: Final[dict[str, type]] = {
    "engine": EngineConfig, "voice": VoiceConfig, "text": TextConfig, "playback": PlaybackConfig,
    "archive": ArchiveConfig, "daemon": DaemonConfig, "logging": LoggingConfig,
}
_CHOICES: Final[dict[tuple[str, str], tuple[Any, ...]]] = {
    ("engine", "provider"): ("auto", "cuda", "tensorrt", "migraphx", "openvino", "cpu"),
    ("engine", "precision"): ("auto", *MODEL_FILES),
    ("engine", "arena_extend_strategy"): ("kSameAsRequested", "kNextPowerOfTwo"),
    ("engine", "cudnn_conv_algo_search"): ("HEURISTIC", "DEFAULT", "EXHAUSTIVE"),
    ("engine", "openvino_precision"): ("FP16", "FP32", "ACCURACY"),
    ("engine", "graph_optimization"): ("all", "extended", "basic", "disabled"),
    ("text", "url_mode"): ("domain", "placeholder", "omit"),
    ("text", "emoji_mode"): ("strip", "name"),
    ("archive", "bit_depth"): (16, 24),
    ("daemon", "default_mode"): ("interrupt", "enqueue"),
    ("logging", "level"): ("DEBUG", "INFO", "WARNING", "ERROR"),
}

DEFAULT_CONFIG_TOML: Final = """# Dusky Kokoro TTS - configuration (TOML)
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


def _compile_voice_spec(vc: VoiceConfig) -> str:
    if not vc.blend:
        return vc.voice_1.strip()
    entries: list[tuple[str, float]] = []
    for name, weight in ((vc.voice_1, vc.weight_1), (vc.voice_2, vc.weight_2), (vc.voice_3, vc.weight_3)):
        cleaned = name.strip()
        if cleaned and cleaned.lower() != "none" and weight > 0:
            entries.append((cleaned, weight))
    if not entries:
        return vc.voice_1.strip()
    return ",".join(f"{name}:{weight:g}" for name, weight in entries)


def _build_section(cls: type, data: dict[str, Any], where: str) -> Any:
    valid = {f.name: f for f in fields(cls)}
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in valid:
            raise ConfigError(f"{where}: unknown key '{key}' (valid keys: {', '.join(sorted(valid))})")
        if isinstance(value, list):
            value = tuple(value)
        hint = hints[key]
        origin = get_origin(hint)
        expected = origin or hint
        valid_type = (type(value) in (int, float) and math.isfinite(value)) if expected is float else isinstance(value, expected)
        if expected is int:
            valid_type = type(value) is int
        if not valid_type:
            raise ConfigError(f"{where}: {key} must be {expected.__name__}")
        if origin is tuple:
            element_type = get_args(hint)[0]
            if not all(isinstance(v, element_type) for v in value):
                raise ConfigError(f"{where}: {key} must contain {element_type.__name__} values")
        if origin is dict and not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            raise ConfigError(f"{where}: {key} must map strings to strings")
        kwargs[key] = value
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ConfigError(f"{where}: {exc}") from exc


def _validate(cfg: Config) -> None:
    for section in fields(cfg):
        obj = getattr(cfg, section.name)
        for f in fields(obj):
            value = getattr(obj, f.name)
            if type(value) in (int, float) and (not math.isfinite(value) or value < 0):
                raise ConfigError(f"[{section.name}] {f.name} must be finite and >= 0")
    for key in ("target_segment_chars", "max_segment_chars", "first_segment_max_chars"):
        if getattr(cfg.text, key) < 3:
            raise ConfigError(f"[text] {key} must be >= 3")
    if cfg.engine.worker_start_timeout_s <= 0 or cfg.daemon.request_timeout_s <= 0:
        raise ConfigError("worker_start_timeout_s and request_timeout_s must be > 0")
    if cfg.daemon.max_request_bytes < 1024 or cfg.playback.cache_max_mb < 1:
        raise ConfigError("max_request_bytes must be >= 1024 and cache_max_mb must be >= 1")
    for (section, key), allowed in _CHOICES.items():
        value = getattr(getattr(cfg, section), key)
        if value not in allowed:
            raise ConfigError(f"[{section}] {key} = {value!r} is invalid (allowed: {allowed})")
    if not 0.5 <= cfg.voice.speed <= 2.0:
        raise ConfigError("[voice] speed must be within 0.5 .. 2.0 (Kokoro duration model limit)")
    if cfg.playback.mpv_speed <= 0:
        raise ConfigError("[playback] mpv_speed must be > 0")
    if cfg.text.max_phonemes > 500 or cfg.text.max_phonemes < 32:
        raise ConfigError("[text] max_phonemes must be within 32 .. 500")
    if cfg.text.max_segment_chars < cfg.text.target_segment_chars:
        raise ConfigError("[text] max_segment_chars must be >= target_segment_chars")
    if cfg.daemon.max_queue < 1 or cfg.playback.prefetch_segments < 1:
        raise ConfigError("[daemon] max_queue and [playback] prefetch_segments must be >= 1")
    try:
        VoiceBank.parse_spec(cfg.voice.spec)
    except VoiceError as exc:
        raise ConfigError(f"[voice] {exc}") from exc


def load_config(path: Path) -> Config:
    """Defaults <- TOML file <- DUSKY_* environment overrides."""
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            raise ConfigError(f"{path}: {exc}") from exc

    sections: dict[str, Any] = {}
    for name, value in data.items():
        if name not in _SECTIONS:
            raise ConfigError(f"{path}: unknown section [{name}] (valid: {', '.join(_SECTIONS)})")
        if not isinstance(value, dict):
            raise ConfigError(f"{path}: [{name}] must be a table")
        sections[name] = _build_section(_SECTIONS[name], value, f"{path} [{name}]")
    cfg = Config(**sections)

    raw_voice = data.get("voice") if isinstance(data.get("voice"), dict) else {}
    explicit_spec = raw_voice.get("spec")
    if not isinstance(explicit_spec, str) or not explicit_spec.strip():
        cfg = dataclasses.replace(cfg, voice=dataclasses.replace(cfg.voice, spec=_compile_voice_spec(cfg.voice)))

    env = os.environ
    if v := env.get("DUSKY_PROVIDER"):
        cfg = dataclasses.replace(cfg, engine=dataclasses.replace(cfg.engine, provider=v))
    if v := env.get("DUSKY_PRECISION"):
        cfg = dataclasses.replace(cfg, engine=dataclasses.replace(cfg.engine, precision=v))
    if v := env.get("DUSKY_VOICE"):
        cfg = dataclasses.replace(cfg, voice=dataclasses.replace(cfg.voice, spec=v))
    if v := env.get("DUSKY_SPEED"):
        try:
            cfg = dataclasses.replace(cfg, voice=dataclasses.replace(cfg.voice, speed=float(v)))
        except ValueError as exc:
            raise ConfigError(f"DUSKY_SPEED={v!r} is not a number") from exc
    if v := env.get("DUSKY_LOG_LEVEL"):
        cfg = dataclasses.replace(cfg, logging=dataclasses.replace(cfg.logging, level=v.upper()))
    _validate(cfg)
    return cfg


@dataclass(slots=True, frozen=True)
class Paths:
    config_file: Path
    socket: Path
    lock: Path
    models_dir: Path
    voices_file: Path
    archive_dir: Path
    cache_dir: Path
    openvino_cache: Path
    tensorrt_cache: Path


def resolve_paths(cfg: Config, config_file: Path, socket_override: str | None = None) -> Paths:
    def expand(value: str) -> Path:
        return Path(os.path.expandvars(value)).expanduser()

    models_dir = expand(cfg.engine.models_dir) if cfg.engine.models_dir else APP_DIR / "models"
    voices = expand(cfg.engine.voices_file) if cfg.engine.voices_file else models_dir / VOICES_FILENAME
    cache = xdg_cache_home() / APP_NAME
    sock = Path(socket_override) if socket_override else (
        expand(cfg.daemon.socket_path) if cfg.daemon.socket_path else default_socket_path())
    archive_dir = expand(cfg.archive.dir) if cfg.archive.dir else cache / "audio"

    return Paths(
        config_file=config_file,
        socket=sock,
        lock=sock.with_name("daemon.lock"),
        models_dir=models_dir,
        voices_file=voices,
        archive_dir=archive_dir,
        cache_dir=cache,
        openvino_cache=expand(cfg.engine.openvino_cache_dir) if cfg.engine.openvino_cache_dir else cache / "openvino",
        tensorrt_cache=expand(cfg.engine.tensorrt_cache_dir) if cfg.engine.tensorrt_cache_dir else cache / "tensorrt",
    )


def setup_logging(cfg: LoggingConfig, level_override: str | None = None) -> None:
    level = (level_override or cfg.level).upper()
    under_journal = "JOURNAL_STREAM" in os.environ
    fmt = "%(levelname)s %(name)s: %(message)s" if under_journal else "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if cfg.file:
        path = Path(os.path.expandvars(cfg.file)).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            path, maxBytes=max(1, cfg.file_max_mb) * 1024 * 1024, backupCount=2, encoding="utf-8"))
    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        name = args.thread.name if args.thread else "?"
        log.critical("uncaught exception in thread %s", name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = _thread_hook


# =============================================================================
#  sd_notify (Type=notify) - 15 lines, no dependency
# =============================================================================
def sd_notify(state: str) -> None:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(state.encode())
    except OSError as exc:
        log.debug("sd_notify failed: %s", exc)


# =============================================================================
#  Text normalisation  (markdown / URLs / structure -> paragraphs of units)
# =============================================================================
_ZERO_WIDTH: Final = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)
_TYPOGRAPHY: Final = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u00ab": '"', "\u00bb": '"',
    "\u300c": '"', "\u300d": '"', "\u300e": '"', "\u300f": '"',
    "\u2013": "-", "\u2014": ", ", "\u2015": ", ", "\u2212": "-", "\u2026": "...",
    "\u00a0": " ", "\u2009": " ", "\u202f": " ", "\u3000": " ", "\t": " ",
})
_SYMBOL_WORDS_EN: Final = {
    "\u00d7": " times ", "\u00f7": " divided by ", "\u00b1": " plus or minus ", "\u2248": " approximately ",
    "\u2265": " greater than or equal to ", "\u2264": " less than or equal to ", "\u2260": " not equal to ",
    "\u2192": " to ", "\u2190": " from ", "\u221e": " infinity ", "\u2122": " trademark ", "\u00a9": " copyright ",
    "\u00ae": " registered ", "<": " less than ", ">": " greater than ", "~": " about ", "@": " at ",
}
_KEEP_PUNCT: Final = frozenset(".,!?;:'\"()-/%&+=\u00b0\u00bf\u00a1\u3002\uff0c\uff01\uff1f\uff1b\uff1a\u3001\uff05")

_HTML_TAG_NAMES: Final = (
    "a|abbr|address|article|aside|audio|b|bdi|bdo|blockquote|body|br|button|caption|cite|code|"
    "col|colgroup|data|dd|del|details|dfn|dialog|div|dl|dt|em|figcaption|figure|footer|form|"
    "h[1-6]|head|header|hr|html|i|iframe|img|input|ins|kbd|label|legend|li|link|main|mark|meta|"
    "nav|noscript|ol|option|p|picture|pre|q|rp|rt|ruby|s|samp|script|section|select|small|source|"
    "span|strong|style|sub|summary|sup|svg|table|tbody|td|template|textarea|tfoot|th|thead|time|"
    "title|tr|u|ul|var|video|wbr"
)

_RE_FENCE_LINE = re.compile(r"^[ \t]{0,3}(\x60{3,}|~{3,})([^\n]*)$")
_RE_INLINE_CODE = re.compile(r"\x60([^\x60\n]{1,300})\x60")
_RE_IMAGE = re.compile(r"!\[([^\[\]]*)\]\([^)]*\)")
_RE_LINK = re.compile(r"\[([^\[\]]+)\]\((?:[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_RE_REF_LINK = re.compile(r"\[([^\[\]]+)\]\[[^\[\]]*\]")
_RE_AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
_RE_HTML_TAG = re.compile(
    rf"<!--[\s\S]{{0,2000}}?-->|"
    rf"</[A-Za-z][A-Za-z0-9:-]*\s*>|"
    rf"<(?:{_HTML_TAG_NAMES})\b[^<>]{{0,160}}>|"
    rf"<[A-Za-z][A-Za-z0-9:-]*\s+[A-Za-z_:][A-Za-z0-9_.:-]*\s*=[^<>]{{0,160}}>|"
    rf"<[A-Za-z][A-Za-z0-9:-]*\s*/>",
    re.I,
)
_RE_HEADER = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t#]*$", re.M)
_RE_HR = re.compile(r"^[ \t]{0,3}(?:(?:[-*_=])[ \t]*){3,}$", re.M)
_RE_BLOCKQUOTE = re.compile(r"^[ \t]{0,3}(?:>[ \t]?)+", re.M)
_RE_BULLET = re.compile(r"^[ \t]*(?:[-*+\u2022\u2023\u25e6\u2043\u2219\u25aa\u25cf]|\d{1,3}[.)]|\(\d{1,3}\))[ \t]+(?=\S)")
_RE_TASK = re.compile(r"^\[[ xX]\][ \t]+")
_RE_STRONG = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.S)
_RE_EMPH = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])|(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")
_RE_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.S)
_RE_TABLE_SEP = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$", re.M)
_RE_TABLE_ROW = re.compile(r"^[ \t]*\|(.+)\|[ \t]*$", re.M)
_RE_CITATION = re.compile(r"\[\^?\d{1,3}(?:\s*[-–,]\s*\d{1,3})*\]")
_RE_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.I)
_RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_RE_SPACES = re.compile(r"[ \t]+")
_RE_SLUG = re.compile(r"[^\w\s-]")

_RE_HTML_STRIP_BLOCK = re.compile(r"<(style|script|head)\b[^>]*>[\s\S]*?</\1>", re.I)
_RE_HTML_COMMENT = re.compile(r"<!--[\s\S]*?-->")
_RE_HTML_PARA_BREAK = re.compile(
    r"</?(?:p|div|section|article|aside|header|footer|main|nav|blockquote|pre|h[1-6]|hr)\b[^>]*>", re.I
)
_RE_HTML_LINE_BREAK = re.compile(r"<(?:br|/li|/tr|/dt|/dd)\b[^>]*>", re.I)
_RE_HTML_CELL_BREAK = re.compile(r"</t[dh]\b[^>]*>", re.I)
_RE_ANY_TAG = re.compile(r"<[^>]+>")
_RE_PDF_MID_FORMFEED = re.compile(r"(?<![.!?:;\n])\s*\x0c\s*(?=[a-z0-9,(])")


def _url_replacement(match: re.Match[str], cfg: TextConfig) -> str:
    url = match.group(0)
    tail = ""
    while url and url[-1] in ".,;:!?":
        tail = url[-1] + tail
        url = url[:-1]
    match cfg.url_mode:
        case "omit":
            return " " + tail
        case "placeholder":
            return f" {cfg.url_placeholder} {tail}"
        case _:
            host = urlsplit(url if "://" in url else "https://" + url).hostname or ""
            host = host.removeprefix("www.")
            return f" {host or cfg.url_placeholder} {tail}"


def _email_replacement(cfg: TextConfig, lang: str) -> str:
    if cfg.url_mode == "omit":
        return " "
    return " email address " if lang.startswith("en") else " email "


def _filter_chars(text: str, cfg: TextConfig, lang: str) -> str:
    english = lang.startswith("en")
    out: list[str] = []
    for ch in text:
        if ch == " " or ch in _KEEP_PUNCT:
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat[0] in "LMN":
            out.append(ch)
        elif cat == "Zs":
            out.append(" ")
        elif english and ch in _SYMBOL_WORDS_EN:
            out.append(_SYMBOL_WORDS_EN[ch])
        elif cat == "Sc":
            out.append(ch)  # currency symbols: espeak-ng / kokoro normaliser verbalise these
        elif cat == "So" and cfg.emoji_mode == "name" and (name := unicodedata.name(ch, "")):
            out.append(f" {name.lower()} ")
        elif ch == "[":
            out.append("(")
        elif ch == "]":
            out.append(")")
        else:
            out.append(" ")
    return _RE_SPACES.sub(" ", "".join(out)).strip()


def _ensure_terminal(unit: str) -> str:
    return unit if unit[-1] in ".!?:;\u3002\uff01\uff1f\uff1b\uff1a" else unit + "."


def strip_fenced_code(text: str, read_code: bool) -> str:
    # A line scan handles unmatched fences in linear time. A closing fence may
    # be longer than its opener; an unclosed block extends to the document end.
    out: list[str] = []
    opener = ""
    for line in text.splitlines(keepends=True):
        match = _RE_FENCE_LINE.match(line.rstrip("\n"))
        if not opener:
            if match and not (match[1][0] == "`" and "`" in match[2]):
                opener = match[1]
                out.append("\n\n")
            else:
                out.append(line)
        elif match and match[1][0] == opener[0] and len(match[1]) >= len(opener) and not match[2].strip():
            opener = ""
            out.append("\n\n")
        elif read_code:
            out.append(line)
    return "".join(out)


def normalize_text(text: str, cfg: TextConfig, lang: str = "en-us") -> list[list[str]]:
    """Return paragraphs, each a list of 'units' (line-level sentence groups)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", text).translate(_ZERO_WIDTH)
    text = html.unescape(text)
    text = strip_fenced_code(text, cfg.read_code_blocks)
    text = _RE_TABLE_SEP.sub("", text)
    text = _RE_TABLE_ROW.sub(lambda m: ", ".join(c.strip() for c in m.group(1).split("|") if c.strip()), text)
    text = _RE_HEADER.sub(lambda m: "\n\n" + m.group(1) + "\n\n", text)
    text = _RE_HR.sub("\n\n", text)
    text = _RE_BLOCKQUOTE.sub("", text)
    text = _RE_IMAGE.sub(r"\1", text)
    text = _RE_LINK.sub(r"\1", text)
    text = _RE_REF_LINK.sub(r"\1", text)
    text = _RE_AUTOLINK.sub(r"\1", text)
    text = _RE_INLINE_CODE.sub(r"\1", text)
    text = _RE_STRONG.sub(r"\2", text)
    text = _RE_EMPH.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _RE_STRIKE.sub(r"\1", text)
    if cfg.strip_citations:
        text = _RE_CITATION.sub("", text)
    text = _RE_HTML_TAG.sub(" ", text)
    text = _RE_URL.sub(lambda m: _url_replacement(m, cfg), text)
    text = _RE_EMAIL.sub(_email_replacement(cfg, lang), text)
    text = text.translate(_TYPOGRAPHY)

    paragraphs: list[list[str]] = []
    units: list[str] = []
    buf: list[str] = []
    last_was_item = False

    def flush_buf() -> None:
        if buf:
            units.append(" ".join(buf))
            buf.clear()

    def flush_paragraph() -> None:
        flush_buf()
        if units:
            paragraphs.append(units.copy())
            units.clear()

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            flush_paragraph()
            last_was_item = False
            continue
        if m := _RE_BULLET.match(raw):
            flush_buf()
            item = _RE_TASK.sub("", raw[m.end():].strip())
            if item:
                units.append(_ensure_terminal(item))
            last_was_item = True
            continue
        if last_was_item and raw[:1] in (" ", "\t") and units:
            units[-1] = _ensure_terminal(units[-1].rstrip(".") + " " + line)
            continue
        last_was_item = False
        if buf and buf[-1].endswith("-") and line[:1].islower():
            buf[-1] = buf[-1][:-1] + line  # hyphenation across a hard wrap
        else:
            buf.append(line)
    flush_paragraph()

    cleaned: list[list[str]] = []
    for group in paragraphs:
        kept = [u for u in (_filter_chars(unit, cfg, lang) for unit in group) if u]
        if kept:
            cleaned.append(kept)
    return cleaned


def flatten_paragraphs(paragraphs: Iterable[Iterable[str]]) -> str:
    return "\n\n".join("\n".join(units) for units in paragraphs)


def make_slug(paragraphs: list[list[str]]) -> str:
    first = paragraphs[0][0] if paragraphs and paragraphs[0] else "speech"
    slug = "_".join(_RE_SLUG.sub("", first).split()[:6]).lower()
    return slug[:48] or "speech"


# =============================================================================
#  Sentence segmentation (Unicode aware, abbreviation safe, weighted budgets)
# =============================================================================
@dataclass(slots=True, frozen=True)
class Segment:
    text: str
    pause_ms: int


_ABBREV: Final = frozenset("""
mr mrs ms dr prof sr jr st vs etc e.g i.e no fig figs approx inc ltd co corp mt ft gen col lt sgt capt rev hon
pres gov sen rep dept univ assn bros ph.d u.s u.k u.n a.m p.m jan feb mar apr jun jul aug sep sept oct nov dec
mon tue wed thu fri sat sun vol ch pp ed eds al cf ca sq oz lb lbs pkg est min max misc dist ave blvd rd
""".split())
_ABBREV_NODOT: Final = frozenset(a.replace(".", "") for a in _ABBREV if "." in a)
_RE_SENT_CANDIDATE = re.compile(r"[.!?]+[\"')]*\s+|[\u3002\uff01\uff1f]+[\"')]*")
_RE_CLAUSE_CUT = re.compile(r"[,;:\u3001\uff0c\uff1b]\s*")
_RE_SPACE_CUT = re.compile(r"\s+")


def _weight(ch: str) -> int:
    return 3 if unicodedata.east_asian_width(ch) in "WF" else 1


def wlen(text: str) -> int:
    return sum(_weight(ch) for ch in text)


def _is_sentence_boundary(text: str, p_start: int, p_end: int) -> bool:
    j = p_start
    while j > 0 and not text[j - 1].isspace():
        j -= 1
    word = text[j:p_start]
    lowered = word.lower().rstrip(".")
    if not lowered:
        return True
    if lowered in _ABBREV or lowered.replace(".", "") in _ABBREV_NODOT:
        return False
    if len(lowered) == 1 and lowered.isalpha() and word[:1].isupper():
        return False  # initials: "J. K. Rowling"
    run = text[p_start:p_end].strip()
    nxt = text[p_end:p_end + 1]
    if run.startswith("...") and nxt.islower():
        return False  # trailing-off ellipsis continues the sentence
    return True


def split_sentences(text: str) -> list[str]:
    out: list[str] = []
    start = 0
    for m in _RE_SENT_CANDIDATE.finditer(text):
        if m.end() >= len(text):
            break
        if m.group(0).lstrip().startswith(".") and not _is_sentence_boundary(text, m.start(), m.end()):
            continue
        piece = text[start:m.end()].strip()
        if piece:
            out.append(piece)
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def _find_cut(text: str, limit: int) -> int:
    acc = 0
    idx = len(text)
    for i, ch in enumerate(text):
        acc += _weight(ch)
        if acc > limit:
            idx = i
            break
    window = text[:idx]
    floor = int(idx * 0.4)
    for pattern in (_RE_CLAUSE_CUT, _RE_SPACE_CUT):
        best = None
        for m in pattern.finditer(window):
            if m.end() >= floor:
                best = m.end()
        if best:
            return best
    return max(idx, 1)


def split_long(text: str, limit: int) -> list[str]:
    # Scan each character once; repeatedly measuring/slicing the entire tail is
    # quadratic for books with long paragraphs or no sentence punctuation.
    parts: list[str] = []
    start = 0
    while start < len(text):
        end, weight = start, 0
        while end < len(text) and weight + _weight(text[end]) <= limit:
            weight += _weight(text[end])
            end += 1
        if end < len(text):
            cut = _find_cut(text[start:end], limit)
            end = start + cut
        head = text[start:end].strip()
        if head:
            parts.append(head)
        start = end
    return parts


def pack_sentences(sentences: Sequence[str], cfg: TextConfig) -> list[str]:
    packed: list[str] = []
    current = ""
    for sentence in sentences:
        if current and (wlen(current) + 1 + wlen(sentence) <= cfg.target_segment_chars or wlen(current) < cfg.min_segment_chars):
            current = f"{current} {sentence}"
        else:
            if current:
                packed.append(current)
            current = sentence
    if current:
        packed.append(current)
    out: list[str] = []
    for seg in packed:
        out.extend(split_long(seg, cfg.max_segment_chars))
    return out


def segment_text(paragraphs: list[list[str]], cfg: TextConfig) -> list[Segment]:
    segments: list[Segment] = []
    for units in paragraphs:
        sentences: list[str] = []
        for unit in units:
            sentences.extend(split_sentences(unit))
        packed = pack_sentences(sentences, cfg)
        for i, seg in enumerate(packed):
            pause = cfg.paragraph_pause_ms if i == len(packed) - 1 else cfg.sentence_pause_ms
            segments.append(Segment(seg, pause))
    if segments and wlen(segments[0].text) > cfg.first_segment_max_chars:
        first = segments[0]
        pieces = split_long(first.text, cfg.first_segment_max_chars)
        if len(pieces) > 1:
            head, rest = pieces[0], " ".join(pieces[1:])
            segments[0:1] = [Segment(head, 60), Segment(rest, first.pause_ms)]
    return segments


def prepare_text(text: str, cfg: TextConfig, lang: str) -> tuple[list[list[str]], list[Segment]]:
    paragraphs = normalize_text(text, cfg, lang)
    return paragraphs, segment_text(paragraphs, cfg)


def split_phonemes(phonemes: str, limit: int) -> list[str]:
    """Exact token guard: split an espeak phoneme string at punctuation/space boundaries."""
    pieces: list[str] = []
    text = phonemes.strip()
    while len(text) > limit:
        window = text[:limit]
        cut = -1
        for sep in (".", "!", "?", ";", ":", ",", " "):
            pos = window.rfind(sep)
            if pos > limit * 0.4:
                cut = pos + 1
                break
        if cut <= 0:
            cut = limit
        head, text = text[:cut].strip(), text[cut:].strip()
        if head:
            pieces.append(head)
    if text:
        pieces.append(text)
    return pieces


def _html_to_text(raw: str) -> str:
    cleaned = _RE_HTML_STRIP_BLOCK.sub(" ", raw)
    cleaned = _RE_HTML_COMMENT.sub(" ", cleaned)
    cleaned = _RE_HTML_PARA_BREAK.sub("\n\n", cleaned)
    cleaned = _RE_HTML_LINE_BREAK.sub("\n", cleaned)
    cleaned = _RE_HTML_CELL_BREAK.sub(", ", cleaned)
    cleaned = _RE_ANY_TAG.sub("", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _RE_SPACES.sub(" ", cleaned)
    cleaned = re.sub(r"[ \t]*\n[ \t]*", "\n", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _normalize_pdf_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _RE_PDF_MID_FORMFEED.sub(" ", text)
    return text.replace("\x0c", "\n\n").strip()


def extract_text_from_file(file_path: Path | str) -> str:
    """
    Extracts readable text from documents, books, and code files.
    Supports:
      - Plain text, Markdown, RST, Org, CSV, JSON, and source code files
      - PDF files (via system pdftotext or pypdf fallback)
      - EPUB ebooks (native zipfile extraction following OPF spine order)
      - HTML/XML documents (block-preserving tag stripping and entity decoding)
    """
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"Target is a directory, not a file: {path}")
    if not os.access(path, os.R_OK):
        raise PermissionError(f"Permission denied reading: {path}")

    suffix = path.suffix.lower()

    # 1. PDF Documents
    if suffix == ".pdf":
        if shutil.which("pdftotext"):
            try:
                res = subprocess.run(
                    ["pdftotext", str(path), "-"],
                    capture_output=True,
                    text=True,
                    check=True,
                    errors="replace",
                )
                text = _normalize_pdf_text(res.stdout)
                if text:
                    return text
            except Exception as exc:
                log.warning("pdftotext failed (%s), attempting fallback", exc)
        try:
            import pypdf  # type: ignore
            reader = pypdf.PdfReader(str(path))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = _normalize_pdf_text("\n\n".join(p.strip() for p in pages if p.strip()))
            if text:
                return text
        except Exception:
            pass
        raise ValueError(f"Could not extract text from PDF: {path.name} (ensure pdftotext/poppler is installed)")

    # 2. EPUB Ebooks (zero-dependency native zipfile extraction)
    if suffix == ".epub":
        import posixpath
        import xml.etree.ElementTree as ET
        import zipfile
        from urllib.parse import unquote
        try:
            with zipfile.ZipFile(path, "r") as z:
                container = ET.fromstring(z.read("META-INF/container.xml"))
                rootfile = container.find(".//{*}rootfile")
                if rootfile is None:
                    raise ValueError("EPUB has no package document")
                opf_name = rootfile.attrib["full-path"]
                package = ET.fromstring(z.read(opf_name))
                manifest = {
                    item.attrib["id"]: item.attrib["href"]
                    for item in package.findall("./{*}manifest/{*}item")
                    if item.attrib.get("media-type") in ("application/xhtml+xml", "text/html")
                }
                files_to_read = [
                    posixpath.normpath(
                        posixpath.join(
                            posixpath.dirname(opf_name),
                            unquote(manifest[item.attrib["idref"]].split("#", 1)[0]),
                        )
                    )
                    for item in package.findall("./{*}spine/{*}itemref")
                    if item.attrib.get("linear", "yes") != "no" and item.attrib.get("idref") in manifest
                ]

                parts: list[str] = []
                for name in files_to_read:
                    try:
                        raw = z.read(name).decode("utf-8", "replace")
                        cleaned = _html_to_text(raw)
                        if cleaned:
                            parts.append(cleaned)
                    except (KeyError, OSError) as exc:
                        raise ValueError(f"Could not read chapter {name}: {exc}") from exc
                text = "\n\n".join(parts).strip()
                if text:
                    return text
        except Exception as exc:
            raise ValueError(f"Could not read EPUB {path.name}: {exc}") from exc

    # 3. HTML Documents
    if suffix in (".html", ".htm", ".xhtml"):
        return _html_to_text(path.read_text(encoding="utf-8", errors="replace"))

    # 4. Standard text, Markdown, RST, Org, and source code files
    return path.read_text(encoding="utf-8", errors="replace")


# =============================================================================
#  Voices (voices-v1.0.bin is an NPZ: name -> float32[510, 1, 256])
# =============================================================================
_RE_VOICE_NAME = re.compile(r"^[a-z]{2}_[a-z0-9]+$")


class VoiceBank:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._npz: Any = None
        self._cache: dict[str, Any] = {}

    def _open(self) -> Any:
        if self._npz is None:
            import numpy as np
            if not self.path.is_file():
                raise VoiceError(f"voices file missing: {self.path} (re-run the installer)")
            self._npz = np.load(self.path, allow_pickle=False)
        return self._npz

    def names(self) -> list[str]:
        return sorted(self._open().files)

    def reset(self, path: Path) -> None:
        self.path = path
        if self._npz is not None:
            self._npz.close()
        self._npz = None
        self._cache.clear()

    @staticmethod
    def parse_spec(spec: str) -> list[tuple[str, float]]:
        parts: list[tuple[str, float]] = []
        for raw in spec.split(","):
            raw = raw.strip()
            if not raw:
                continue
            name, _, weight = raw.partition(":")
            name = name.strip()
            if not _RE_VOICE_NAME.match(name):
                raise VoiceError(f"invalid voice name '{name}' in spec '{spec}'")
            try:
                w = float(weight) if weight.strip() else 1.0
            except ValueError as exc:
                raise VoiceError(f"invalid weight '{weight}' for voice '{name}'") from exc
            if not math.isfinite(w) or w <= 0:
                raise VoiceError(f"weight for voice '{name}' must be > 0")
            parts.append((name, w))
        if not parts:
            raise VoiceError("empty voice spec")
        scale = max(w for _, w in parts)
        total = sum(w / scale for _, w in parts)
        return [(n, (w / scale) / total) for n, w in parts]

    @staticmethod
    def lang_for(spec: str, configured: str = "auto") -> str:
        if configured and configured != "auto":
            return configured
        first = VoiceBank.parse_spec(spec)[0][0]
        return LANG_BY_PREFIX.get(first[0], "en-us")

    def resolve(self, spec: str) -> Any:
        """Weighted linear blend of style tensors (worker process only)."""
        import numpy as np
        parts = self.parse_spec(spec)
        key = ",".join(f"{n}:{w!r}" for n, w in parts)
        if key in self._cache:
            return self._cache[key]
        npz = self._open()
        blended = None
        for name, weight in parts:
            if name not in npz.files:
                raise VoiceError(f"voice '{name}' not found; available: {', '.join(self.names())}")
            vec = np.asarray(npz[name], dtype=np.float32) * np.float32(weight)
            blended = vec if blended is None else blended + vec
        result = np.ascontiguousarray(blended, dtype=np.float32)
        if len(self._cache) >= 16:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = result
        return result


# =============================================================================
#  Inference engine (ONNX Runtime + kokoro-onnx)
# =============================================================================
class _AbortedError(Exception):
    """Internal worker sentinel for an intentionally aborted segment."""


class _SessionProxy:
    """Thin forwarding wrapper handed to Kokoro.from_session().

    Injects RunOptions so an in-flight ONNX run can be aborted from the worker's
    abort watcher thread (RunOptions.terminate = True) without killing the
    process or discarding the warm session.
    """

    __slots__ = ("_inner", "run_options")

    def __init__(self, inner: Any, run_options: Any) -> None:
        self._inner = inner
        self.run_options = run_options

    def run(self, output_names: Any, input_feed: Any, run_options: Any = None) -> Any:
        outputs = self._inner.run(output_names, input_feed, run_options or self.run_options)
        if output_names is None:
            import numpy as np
            if not outputs or np.asarray(outputs[0]).size == 0 or not np.isfinite(outputs[0]).all():
                raise InferenceError("model produced empty or non-finite audio")
        return outputs

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _quantize_pcm(samples: Any, bit_depth: int) -> bytes:
    """Convert float32 samples in [-1, 1] to packed little-endian s16le or s24le."""
    import numpy as np
    if bit_depth == 24:
        ints = np.clip(samples * 8388607.0, -8388608.0, 8388607.0).astype("<i4")
        return ints.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    return np.clip(samples * 32767.0, -32768.0, 32767.0).astype("<i2").tobytes()


@dataclass(slots=True)
class EngineStats:
    loads: int = 0
    last_load_s: float = 0.0
    segments: int = 0
    synth_s: float = 0.0
    audio_s: float = 0.0

    @property
    def rtf(self) -> float | None:
        return (self.synth_s / self.audio_s) if self.audio_s > 0 else None


def provider_chain(requested: str, available: Sequence[str]) -> list[str]:
    if requested == "auto":
        return [k for k in ("cuda", "migraphx", "openvino") if EP_NAMES[k] in available] + ["cpu"]
    if requested == "cpu":
        return ["cpu"]
    if EP_NAMES[requested] not in available:
        return ["cpu"]
    return [requested, "cpu"]


def choose_model(precision: str, kind: str, models_dir: Path) -> tuple[str, Path]:
    if precision != "auto":
        path = models_dir / MODEL_FILES[precision]
        if not path.is_file():
            if precision == "fp16" and (models_dir / MODEL_FILES["fp16-gpu"]).is_file():
                return "fp16-gpu", models_dir / MODEL_FILES["fp16-gpu"]
            if precision == "fp16-gpu" and (models_dir / MODEL_FILES["fp16"]).is_file():
                return "fp16", models_dir / MODEL_FILES["fp16"]
            present = [k for k, f in MODEL_FILES.items() if (models_dir / f).is_file()]
            raise EngineError(f"model for precision '{precision}' missing at {path}; present: {present or 'none'}")
        return precision, path
    order = ("fp16-gpu", "f32", "int8", "fp16") if kind in GPU_KINDS or kind == "cpu" else ("f32", "fp16-gpu", "int8", "fp16")
    for candidate in order:
        path = models_dir / MODEL_FILES[candidate]
        if path.is_file():
            return candidate, path
    raise EngineError(f"no Kokoro model found in {models_dir} (run kokoro_installer.sh)")


class Engine:
    _ABORT_DRAIN_TIMEOUT_S: Final = 1.5

    def __init__(self, cfg: Config, paths: Paths, voices: VoiceBank, is_worker: bool = False) -> None:
        self.cfg = cfg
        self.paths = paths
        self.voices = voices
        self.is_worker = is_worker
        self._worker_proc: asyncio.subprocess.Process | None = None
        self._abort_w: int | None = None
        self._loaded = False
        self._kokoro: Any = None
        self._session: Any = None
        self._run_options: Any = None
        self._cuda_preloaded = False
        self._load_lock = asyncio.Lock()
        self._io_lock = asyncio.Lock()
        self._req_id = 0
        self._in_flight_req_id: int | None = None
        self._aborted_req_id = 0
        self._drain_task: asyncio.Task[None] | None = None
        self._reaping: set[asyncio.Task[Any]] = set()
        self.reload_pending = False
        self.last_used = time.monotonic()
        self.active_kind = "none"
        self.active_providers: list[str] = []
        self.model_precision = "none"
        self.model_path: Path | None = None
        self.degraded = False
        self.supported_langs: set[str] = set()
        self.warnings: list[str] = []
        self.stats = EngineStats()

    # ---- async facade (daemon side) -----------------------------------------
    @property
    def loaded(self) -> bool:
        if self.is_worker:
            return self._kokoro is not None
        return self._loaded and self._worker_proc is not None and self._worker_proc.returncode is None

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def _close_abort_pipe(self) -> None:
        fd, self._abort_w = self._abort_w, None
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)

    def _signal_abort(self, req_id: int) -> None:
        if req_id <= self._aborted_req_id:
            return
        self._aborted_req_id = req_id
        fd = self._abort_w
        if fd is not None:
            with contextlib.suppress(OSError):
                os.write(fd, struct.pack("<I", req_id))

    async def _await_drain(self) -> None:
        task = self._drain_task
        if task is not None:
            await asyncio.shield(task)

    async def ensure_loaded(self) -> None:
        await self._await_drain()
        async with self._load_lock:
            if self.is_worker:
                if self.reload_pending and self.loaded:
                    self._unload_sync("configuration reload")
                self.reload_pending = False
                if not self.loaded:
                    self._load_sync()
            else:
                if self.reload_pending and self.loaded:
                    await self._unload_worker("configuration reload")
                self.reload_pending = False
                if not self.loaded:
                    await self._unload_worker("restart failed worker")
                    script_path = str(Path(__file__).resolve())
                    await asyncio.gather(*self._reaping, return_exceptions=True)
                    abort_r, abort_w = os.pipe()
                    os.set_blocking(abort_w, False)
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            sys.executable, "-X", "utf8", "-u", script_path, "synth-worker",
                            "--config", str(self.paths.config_file),
                            "--abort-fd", str(abort_r),
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=None,
                            pass_fds=(abort_r,),
                        )
                    except BaseException:
                        os.close(abort_w)
                        raise
                    finally:
                        os.close(abort_r)
                    self._worker_proc = proc
                    self._abort_w = abort_w
                    self._req_id = 0
                    self._aborted_req_id = 0
                    try:
                        async with asyncio.timeout(self.cfg.engine.worker_start_timeout_s):
                            assert proc.stdin is not None and proc.stdout is not None
                            proc.stdin.write(json.dumps({
                                "config": dataclasses.asdict(self.cfg),
                                "models_dir": str(self.paths.models_dir),
                                "voices_file": str(self.paths.voices_file),
                            }).encode() + b"\n")
                            await proc.stdin.drain()
                            line = await proc.stdout.readline()
                            if not line:
                                raise EngineError("synthesis worker failed to start (see daemon logs)")
                            try:
                                ready = json.loads(line)
                            except (ValueError, UnicodeError) as exc:
                                raise EngineError(f"malformed ready signal from synthesis worker: {exc}") from exc
                            if not isinstance(ready, dict) or not ready.get("ok"):
                                error = ready.get("error") if isinstance(ready, dict) else "invalid ready object"
                                raise EngineError(f"synthesis worker error: {error}")
                    except BaseException as exc:
                        await self._unload_worker("worker startup failed")
                        if isinstance(exc, (ConnectionError, TimeoutError)):
                            raise EngineError(f"synthesis worker startup failed: {type(exc).__name__}") from exc
                        raise
                    self.active_providers = ready.get("providers", [])
                    self.active_kind = ready.get("kind", "none")
                    self.model_precision = ready.get("precision", "none")
                    self.model_path = Path(ready["model"]) if ready.get("model") else None
                    self.degraded = ready.get("degraded", False)
                    self.warnings.extend(ready.get("warnings", []))
                    self.stats.loads += 1
                    self.stats.last_load_s = ready.get("load_s", 0.0)
                    self._loaded = True
                    log.info("Synthesis worker ready (pid %d); engine=%s; providers=%s",
                             proc.pid, self.active_kind, self.active_providers)
        self.touch()

    async def _terminate_proc(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stdin is not None:
            proc.stdin.close()
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
        try:
            async with asyncio.timeout(2.0):
                await proc.wait()
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()

    async def _unload_worker(self, reason: str) -> None:
        drain, self._drain_task = self._drain_task, None
        if drain is not None and not drain.done():
            drain.cancel()
            with contextlib.suppress(BaseException):
                await drain
        self._close_abort_pipe()
        proc, self._worker_proc = self._worker_proc, None
        self._loaded = False
        self._in_flight_req_id = None
        if proc is not None:
            await self._terminate_proc(proc)
            log.info("Synthesis worker exited (%s)", reason)
        self.active_kind = "none"
        self.active_providers = []

    async def _kill_if_current(self, proc: asyncio.subprocess.Process, reason: str) -> None:
        if self._worker_proc is proc:
            self._close_abort_pipe()
            self._worker_proc = None
            self._loaded = False
            self.active_kind = "none"
            self.active_providers = []
        await self._terminate_proc(proc)
        log.info("Synthesis worker terminated (%s)", reason)

    async def unload(self, reason: str) -> None:
        async with self._load_lock:
            async with self._io_lock:
                if self.is_worker:
                    if self._kokoro is not None:
                        self._unload_sync(reason)
                else:
                    await self._unload_worker(reason)

    async def _rpc_synth(self, proc: asyncio.subprocess.Process, payload: bytes) -> tuple[dict[str, Any], bytes]:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(payload)
        await proc.stdin.drain()
        (length,) = struct.unpack("<I", await proc.stdout.readexactly(4))
        state = json.loads(await proc.stdout.readexactly(length))
        pcm = await proc.stdout.readexactly(state["pcm_bytes"])
        return state, pcm

    async def _finish_aborted_rpc(
        self,
        rpc: asyncio.Task[tuple[dict[str, Any], bytes]],
        proc: asyncio.subprocess.Process,
    ) -> None:
        try:
            async with asyncio.timeout(self._ABORT_DRAIN_TIMEOUT_S):
                state, _pcm = await rpc
                self._apply_worker_state(state)
        except TimeoutError:
            rpc.cancel()
            with contextlib.suppress(BaseException):
                await rpc
            await self._kill_if_current(proc, "abort drain timeout")
        except (ConnectionError, asyncio.IncompleteReadError):
            await self._kill_if_current(proc, "worker pipe closed during abort")
        except asyncio.CancelledError:
            rpc.cancel()
            with contextlib.suppress(BaseException):
                await rpc
            raise
        except Exception as exc:
            log.debug("aborted worker RPC ended with error: %s", exc)
            await self._kill_if_current(proc, "worker error during abort")
        finally:
            if self._drain_task is asyncio.current_task():
                self._drain_task = None

    def _apply_worker_state(self, state: dict[str, Any]) -> None:
        self.active_kind = state["kind"]
        self.active_providers = state["providers"]
        self.degraded = state["degraded"]
        self.model_precision = state["precision"]
        self.model_path = Path(state["model"]) if state.get("model") else None

    async def synthesize(
        self,
        text: str,
        voice_spec: Any,
        speed: float,
        lang: str,
        pause_ms: int,
        *,
        bit_depth: int = 16,
        max_phonemes: int | None = None,
        trim_silence: bool | None = None,
    ) -> bytes:
        if self.is_worker:
            vec = self.voices.resolve(voice_spec) if isinstance(voice_spec, str) else voice_spec
            return self._synth_sync(
                text, vec, speed, lang, pause_ms,
                bit_depth=bit_depth,
                max_phonemes=max_phonemes,
                trim_silence=trim_silence,
            )

        while True:
            await self._await_drain()
            if not self.loaded:
                await self.ensure_loaded()
            async with self._io_lock:
                if self._drain_task is not None or not self.loaded:
                    continue
                proc = self._worker_proc
                if proc is None or proc.stdin is None or proc.stdout is None:
                    continue
                self._req_id += 1
                req_id = self._req_id
                payload = (json.dumps({
                    "cmd": "synth",
                    "req_id": req_id,
                    "text": text,
                    "voice": voice_spec,
                    "speed": speed,
                    "lang": lang,
                    "pause_ms": pause_ms,
                    "bit_depth": bit_depth,
                    "max_phonemes": self.cfg.text.max_phonemes if max_phonemes is None else max_phonemes,
                    "trim_silence": self.cfg.text.trim_silence if trim_silence is None else trim_silence,
                }) + "\n").encode("utf-8")
                t0 = time.perf_counter()
                rpc = asyncio.create_task(self._rpc_synth(proc, payload), name=f"worker-rpc-{req_id}")
                self._in_flight_req_id = req_id
                try:
                    state, pcm = await asyncio.shield(rpc)
                except asyncio.CancelledError:
                    self._signal_abort(req_id)
                    self._drain_task = asyncio.create_task(
                        self._finish_aborted_rpc(rpc, proc), name=f"worker-drain-{req_id}"
                    )
                    raise
                except (ConnectionError, asyncio.IncompleteReadError) as exc:
                    await self._kill_if_current(proc, "worker pipe closed")
                    raise EngineError("synthesis worker exited during synthesis (see daemon logs)") from exc
                finally:
                    if self._in_flight_req_id == req_id:
                        self._in_flight_req_id = None

                self._apply_worker_state(state)
                if state.get("aborted"):
                    raise asyncio.CancelledError("synthesis aborted")
                if state.get("error"):
                    raise EngineError(state["error"])
                elapsed = time.perf_counter() - t0
                sample_width = sample_width_for(bit_depth)
                self.stats.segments += 1
                self.stats.synth_s += elapsed
                self.stats.audio_s += len(pcm) / (sample_width * SAMPLE_RATE)
                self.touch()
                return pcm

    def interrupt(self) -> None:
        """Abort an in-flight ONNX run while keeping an idle worker warm."""
        if self.is_worker:
            if self._run_options is not None:
                self._run_options.terminate = True
            return
        req_id = self._in_flight_req_id
        if req_id is not None:
            self._signal_abort(req_id)

    def pop_warnings(self) -> list[str]:
        out, self.warnings = self.warnings, []
        return out

    async def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            await self.unload("shutdown")
        await asyncio.gather(*self._reaping, return_exceptions=True)

    # ---- synchronous internals (worker process / offline bench) -------------
    def _preload_cuda_libs(self, ort: Any) -> None:
        if self._cuda_preloaded:
            return
        preload = getattr(ort, "preload_dlls", None)
        if preload is None:
            raise EngineError("onnxruntime-gpu >= 1.21 is required (onnxruntime.preload_dlls missing)")
        dirs: tuple[str | None, ...] = tuple(os.path.expanduser(d) for d in self.cfg.engine.cuda_lib_dirs) or (None,)
        for directory in dirs:
            preload(cuda=True, cudnn=True, msvc=False, directory=directory)
        self._cuda_preloaded = True

    def _providers_for(self, kind: str) -> list[Any]:
        e = self.cfg.engine
        limit = str(e.gpu_mem_limit_mb * 1024 * 1024) if e.gpu_mem_limit_mb > 0 else None
        cuda_opts: dict[str, Any] = {
            "arena_extend_strategy": e.arena_extend_strategy,
            "cudnn_conv_algo_search": e.cudnn_conv_algo_search,
            "cudnn_conv_use_max_workspace": "1" if e.cudnn_conv_use_max_workspace else "0",
            "do_copy_in_default_stream": "1",
        }
        if e.device_id != 0:
            cuda_opts["device_id"] = e.device_id
        if limit:
            cuda_opts["gpu_mem_limit"] = limit
        match kind:
            case "cuda":
                return [("CUDAExecutionProvider", cuda_opts), "CPUExecutionProvider"]
            case "tensorrt":
                cache = self.paths.tensorrt_cache
                cache.mkdir(parents=True, exist_ok=True)
                trt_opts = {
                    "device_id": str(e.device_id),
                    "trt_fp16_enable": "1",
                    "trt_engine_cache_enable": "1",
                    "trt_engine_cache_path": str(cache),
                    "trt_timing_cache_enable": "1",
                    "trt_timing_cache_path": str(cache),
                    "trt_max_workspace_size": str(min(e.gpu_mem_limit_mb or 2048, 512) << 20),
                    "trt_profile_min_shapes": e.tensorrt_profile_min,
                    "trt_profile_opt_shapes": e.tensorrt_profile_opt,
                    "trt_profile_max_shapes": e.tensorrt_profile_max,
                }
                return [("TensorrtExecutionProvider", trt_opts), ("CUDAExecutionProvider", cuda_opts), "CPUExecutionProvider"]
            case "migraphx":
                opts = {"device_id": str(e.device_id), "migraphx_fp16_enable": "1"}
                if limit:
                    opts["migraphx_mem_limit"] = limit
                return [("MIGraphXExecutionProvider", opts), "CPUExecutionProvider"]
            case "openvino":
                cache = self.paths.openvino_cache
                cache.mkdir(parents=True, exist_ok=True)
                device = e.openvino_device.split(":", 1)[0].split(".", 1)[0]
                props = {"CACHE_DIR": str(cache), "PERFORMANCE_HINT": "LATENCY"}
                if e.openvino_precision == "ACCURACY":
                    props["EXECUTION_MODE_HINT"] = "ACCURACY"
                elif device in ("GPU", "NPU", "CPU"):
                    props["INFERENCE_PRECISION_HINT"] = "f32" if device == "CPU" or e.openvino_precision == "FP32" else "f16"
                if device == "CPU":
                    props["INFERENCE_NUM_THREADS"] = str(self._cpu_threads())
                ov_opts = {"device_type": e.openvino_device, "load_config": json.dumps({device: props})}
                return [("OpenVINOExecutionProvider", ov_opts), "CPUExecutionProvider"]
            case _:
                return ["CPUExecutionProvider"]

    def _cpu_threads(self) -> int:
        configured = self.cfg.engine.intra_op_threads
        return configured if configured > 0 else min(8, os.process_cpu_count() or 4)

    def _session_options(self, kind: str, ort: Any) -> Any:
        e = self.cfg.engine
        so = ort.SessionOptions()
        so.graph_optimization_level = {
            "all": ort.GraphOptimizationLevel.ORT_ENABLE_ALL,
            "extended": ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED,
            "basic": ort.GraphOptimizationLevel.ORT_ENABLE_BASIC,
            "disabled": ort.GraphOptimizationLevel.ORT_DISABLE_ALL,
        }[e.graph_optimization]
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.enable_mem_pattern = False   # avoid retaining allocation patterns for many dynamic token lengths
        so.enable_cpu_mem_arena = True
        so.intra_op_num_threads = min(2, os.process_cpu_count() or 1) if kind in GPU_KINDS else self._cpu_threads()
        so.inter_op_num_threads = 1
        so.log_severity_level = 0 if self.cfg.logging.ort_verbose else 3
        so.add_session_config_entry("session.intra_op.allow_spinning", "1" if e.allow_spinning else "0")
        if kind == "cpu":
            so.add_session_config_entry("session.set_denormal_as_zero", "1")
        if e.profiling:
            self.paths.cache_dir.mkdir(parents=True, exist_ok=True)
            so.enable_profiling = True
            so.profile_file_prefix = str(self.paths.cache_dir / "ort-profile")
        return so

    def _load_sync(self) -> None:
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        if not hasattr(Kokoro, "from_session"):
            raise EngineError("kokoro-onnx is too old: Kokoro.from_session() is missing (need >= 0.4)")
        if self._kokoro is not None:
            return
        e = self.cfg.engine
        t0 = time.perf_counter()
        available = list(ort.get_available_providers())
        if e.require_accelerator and (
            e.provider == "cpu"
            or (e.provider != "auto" and EP_NAMES[e.provider] not in available)
            or (e.provider == "auto" and not any(EP_NAMES[k] in available for k in ("cuda", "migraphx", "openvino")))
        ):
            raise EngineError("requested accelerator is unavailable in this ONNX Runtime build")
        chain = provider_chain(e.provider, available)
        log.info("Loading Kokoro via %s (onnxruntime %s, available: %s)", " > ".join(chain), ort.__version__, available)

        session = None
        kind = "cpu"
        precision = "none"
        model_path: Path | None = None
        self.degraded = e.provider not in ("cpu", "auto") and chain == ["cpu"]
        if self.degraded:
            self.warnings.append(f"{EP_NAMES[e.provider]} unavailable; using CPU")
        last_error: Exception | None = None
        for kind in chain:
            try:
                precision, model_path = choose_model(e.precision, kind, self.paths.models_dir)
                log.info("Trying %s with %s [%s]", EP_NAMES[kind], model_path.name, precision)
                if kind in ("cuda", "tensorrt"):
                    self._preload_cuda_libs(ort)
                session = ort.InferenceSession(
                    str(model_path),
                    sess_options=self._session_options(kind, ort),
                    providers=self._providers_for(kind),
                )
                active = session.get_providers()
                if active[0] != EP_NAMES[kind]:
                    msg = f"{EP_NAMES[kind]} was requested but ONNX Runtime activated {active[0]} (missing runtime libraries?)"
                    if e.require_accelerator:
                        raise EngineError(msg + " - refusing to run degraded (engine.require_accelerator = true)")
                    log.warning("%s - continuing on %s", msg, active[0])
                    self.warnings.append(msg)
                    self.degraded = True
                    session = None
                    continue
                break
            except Exception as exc:
                last_error = exc
                if kind == "cpu":
                    raise EngineError(f"CPUExecutionProvider failed: {exc}") from exc
                msg = f"{EP_NAMES[kind]} unavailable: {exc}"
                if e.require_accelerator:
                    raise EngineError(msg) from exc
                log.warning("%s - falling back to the next provider", msg)
                self.warnings.append(msg)
                self.degraded = True
        if session is None or model_path is None:
            raise EngineError(f"no execution provider could be initialised: {last_error}")

        self._run_options = ort.RunOptions()
        proxy = _SessionProxy(session, self._run_options)
        kokoro = Kokoro.from_session(proxy, str(self.paths.voices_file))
        languages = getattr(kokoro, "get_languages", None)
        self.supported_langs = set(languages()) if callable(languages) else set()
        self._session = session
        self._kokoro = kokoro
        self.active_kind = kind
        self.active_providers = list(session.get_providers())
        self.model_precision = precision
        self.model_path = model_path
        self.stats.loads += 1
        self.stats.last_load_s = time.perf_counter() - t0
        log.info("Model ready in %.2fs; providers=%s; threads=%d", self.stats.last_load_s,
                 self.active_providers, self._cpu_threads())
        session.disable_fallback()
        if e.warmup:
            try:
                vec = self.voices.resolve(self.cfg.voice.spec)
                t1 = time.perf_counter()
                self._synth_sync("Ready.", vec, 1.0, VoiceBank.lang_for(self.cfg.voice.spec, self.cfg.voice.lang), 0)
                log.info("Warm-up run took %.0f ms", (time.perf_counter() - t1) * 1000)
            except Exception as exc:
                if kind != "cpu" and not e.require_accelerator:
                    self.fallback_to_cpu(exc)
                else:
                    self._unload_sync("warmup failed")
                    raise EngineError(f"warmup failed: {exc}") from exc
        self.touch()

    def fallback_to_cpu(self, error: Exception) -> None:
        if self.active_kind == "cpu" or self.cfg.engine.require_accelerator:
            raise error
        msg = f"accelerator synthesis failed ({error}); continuing on CPU"
        log.warning("%s", msg)
        self._unload_sync("CPU fallback")
        self.cfg = dataclasses.replace(self.cfg, engine=dataclasses.replace(
            self.cfg.engine, provider="cpu", precision="auto", warmup=False))
        self._load_sync()
        self.degraded = True
        self.warnings.append(msg)

    def _unload_sync(self, reason: str) -> None:
        if self._kokoro is not None:
            self._kokoro.voices.close()
        self.voices.reset(self.voices.path)
        self._kokoro = None
        self._session = None
        self._run_options = None
        self.active_providers = []
        self.active_kind = "none"
        gc.collect()
        log.info("Model unloaded (%s). Device arena released; CUDA/HIP driver context is freed on worker exit.", reason)

    def _synth_sync(
        self,
        text: str,
        voice_vec: Any,
        speed: float,
        lang: str,
        pause_ms: int,
        *,
        bit_depth: int = 16,
        max_phonemes: int | None = None,
        trim_silence: bool | None = None,
        abort_event: threading.Event | None = None,
    ) -> bytes:
        import numpy as np

        kokoro = self._kokoro
        if kokoro is None:
            raise EngineError("engine not loaded")
        if self.supported_langs and lang not in self.supported_langs:
            raise EngineError(f"language '{lang}' is not supported by kokoro-onnx (supported: {sorted(self.supported_langs)})")
        if abort_event is not None and abort_event.is_set():
            raise _AbortedError()
        speed = min(2.0, max(0.5, float(speed)))
        eff_max_phonemes = self.cfg.text.max_phonemes if max_phonemes is None else max_phonemes
        eff_trim = self.cfg.text.trim_silence if trim_silence is None else trim_silence
        t0 = time.perf_counter()
        phonemes = kokoro.tokenizer.phonemize(text, lang)
        if abort_event is not None and abort_event.is_set():
            raise _AbortedError()
        parts: list[Any] = []
        for piece in split_phonemes(phonemes, eff_max_phonemes):
            if abort_event is not None and abort_event.is_set():
                raise _AbortedError()
            try:
                audio, sr = kokoro.create(
                    piece, voice=voice_vec, speed=speed, lang=lang,
                    is_phonemes=True, trim=eff_trim,
                )
            except Exception:
                if abort_event is not None and abort_event.is_set():
                    raise _AbortedError() from None
                raise
            if abort_event is not None and abort_event.is_set():
                raise _AbortedError()
            if sr != SAMPLE_RATE:
                raise EngineError(f"unexpected sample rate {sr}")
            samples = np.asarray(audio, dtype=np.float32).reshape(-1)
            if samples.size == 0 or not np.isfinite(samples).all():
                raise InferenceError("model produced empty or non-finite audio")
            parts.append(samples)
        if pause_ms > 0:
            parts.append(np.zeros(SAMPLE_RATE * pause_ms // 1000, dtype=np.float32))
        pcm_f32 = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
        pcm_bytes = _quantize_pcm(pcm_f32, bit_depth)
        elapsed = time.perf_counter() - t0
        self.stats.segments += 1
        self.stats.synth_s += elapsed
        self.stats.audio_s += len(pcm_f32) / SAMPLE_RATE
        self.touch()
        log.debug("segment: %d chars -> %d phonemes -> %.2fs audio in %.0f ms", len(text), len(phonemes),
                  len(pcm_f32) / SAMPLE_RATE, elapsed * 1000)
        return pcm_bytes


# =============================================================================
#  WAV archive (pure stdlib wave module; accepts pre-quantized s16le/s24le PCM)
# =============================================================================
class ArchiveWriter:
    def __init__(self, path: Path, bit_depth: int) -> None:
        self.path = path
        self.bit_depth = bit_depth
        self._sampwidth = sample_width_for(bit_depth)
        self.frames = 0
        self._wf = wave.open(str(path), "wb")
        self._wf.setnchannels(1)
        self._wf.setsampwidth(self._sampwidth)
        self._wf.setframerate(SAMPLE_RATE)

    @classmethod
    def create(cls, directory: Path, slug: str, bit_depth: int) -> "ArchiveWriter":
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        fd, name = tempfile.mkstemp(prefix=f"{stamp}_{slug}_", suffix=".wav", dir=directory)
        os.close(fd)
        return cls(Path(name), bit_depth)

    def write(self, pcm: bytes) -> None:
        self._wf.writeframes(pcm)
        self.frames += len(pcm) // self._sampwidth

    def close(self, discard: bool = False, max_files: int = 0) -> None:
        try:
            self._wf.close()
        except (OSError, wave.Error, struct.error):
            with contextlib.suppress(OSError):
                self.path.unlink(missing_ok=True)
            raise
        if discard or self.frames == 0:
            self.path.unlink(missing_ok=True)
            return
        if max_files > 0:
            files = sorted(self.path.parent.glob("*.wav"), key=lambda p: p.name)
            for old in files[:-max_files]:
                old.unlink(missing_ok=True)


# =============================================================================
#  mpv player (one process per utterance; stdin = s16le/s24le, socketpair = IPC)
# =============================================================================
class MpvPlayer:
    def __init__(self, cfg: PlaybackConfig, env: dict[str, str], title: str, bit_depth: int = 16) -> None:
        self.cfg = cfg
        self.env = env
        self.title = title
        self.bit_depth = bit_depth
        self.proc: asyncio.subprocess.Process | None = None
        self.paused = False
        self.end_reason: str | None = None
        self.stderr_tail: deque[str] = deque(maxlen=8)
        self.eof_reached = False
        self._ipc_reader: asyncio.StreamReader | None = None
        self._ipc_writer: asyncio.StreamWriter | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._tasks: list[asyncio.Task[None]] = []
        self._input_ended = False
        self._stopped = False

    def _command_line(self, ipc_fd: int) -> list[str]:
        c = self.cfg
        cmd = [c.mpv_binary]
        if not c.use_user_mpv_config:
            cmd.append("--no-config")
        cmd += [
            "--no-terminal", "--msg-level=all=warn", "--idle=no", "--keep-open=no", "--loop-file=no",
            "--pause=no", "--ytdl=no", "--no-resume-playback", "--no-save-position-on-quit", "--no-sub",
            "--input-ipc-server=", "--load-auto-profiles=no", "--video-sync=audio", "--interpolation=no",
            f"--input-ipc-client=fd://{ipc_fd}",
            "--demuxer=rawaudio", f"--demuxer-rawaudio-rate={SAMPLE_RATE}",
            "--demuxer-rawaudio-channels=1", f"--demuxer-rawaudio-format={pcm_format_for(self.bit_depth)}",
            "--cache=yes", f"--demuxer-max-bytes={c.cache_max_mb}MiB", "--demuxer-max-back-bytes=8MiB",
            "--demuxer-readahead-secs=36000",
            "--cache-secs=36000",
            "--cache-pause=no", "--cache-pause-initial=no",
            f"--speed={c.mpv_speed}", f"--volume={c.volume}", "--audio-pitch-correction=yes",
            f"--force-media-title={self.title}",
        ]
        if c.window:
            cmd += ["--force-window=yes", f"--geometry={c.window_geometry}", f"--title={c.window_title}: {self.title}",
                    "--wayland-app-id=kokoro", "--osd-level=1", "--gpu-context=wayland"]
        else:
            cmd += ["--force-window=no", "--vo=null"]
        if c.audio_device:
            cmd.append(f"--audio-device={c.audio_device}")
        cmd += list(c.extra_args)
        cmd.append("-")
        return cmd

    async def start(self) -> None:
        ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self._command_line(theirs.fileno()),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                env=self.env, pass_fds=(theirs.fileno(),))
        except BaseException:
            ours.close()
            raise
        finally:
            theirs.close()
        self._ipc_reader, self._ipc_writer = await asyncio.open_unix_connection(sock=ours)
        self._tasks = [
            asyncio.create_task(self._ipc_loop(), name="mpv-ipc"),
            asyncio.create_task(self._stderr_loop(), name="mpv-stderr"),
        ]
        with contextlib.suppress(Exception):
            await self.command("observe_property", 1, "pause")
            await self.command("observe_property", 2, "eof-reached")
        log.info("mpv started (pid %d, %s)", self.proc.pid, pcm_format_for(self.bit_depth))

    def _send_nowait(self, *args: Any) -> None:
        writer = self._ipc_writer
        if writer is not None and not writer.is_closing():
            with contextlib.suppress(OSError):
                writer.write(json.dumps({"command": list(args)}).encode() + b"\n")

    async def is_eof(self) -> bool:
        with contextlib.suppress(Exception):
            return bool(await self.command("get_property", "eof-reached", timeout=0.5))
        return False

    async def command(self, *args: Any, timeout: float = 2.0) -> Any:
        writer = self._ipc_writer
        if writer is None or writer.is_closing():
            raise PlayerClosed("mpv IPC closed")
        rid = self._next_id
        self._next_id += 1
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            writer.write(json.dumps({"command": list(args), "request_id": rid}).encode() + b"\n")
            await writer.drain()
            async with asyncio.timeout(timeout):
                reply = await fut
        except (ConnectionError, BrokenPipeError) as exc:
            raise PlayerClosed("mpv IPC closed") from exc
        finally:
            self._pending.pop(rid, None)
        if reply.get("error") != "success":
            raise PlayerError(f"mpv command {args[0]!r} failed: {reply.get('error')}")
        return reply.get("data")

    async def _ipc_loop(self) -> None:
        reader = self._ipc_reader
        assert reader is not None
        try:
            while line := await reader.readline():
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if (rid := msg.get("request_id")) is not None:
                    if (fut := self._pending.get(rid)) is not None and not fut.done():
                        fut.set_result(msg)
                    continue
                match msg.get("event"):
                    case "property-change" if msg.get("name") == "pause":
                        self.paused = bool(msg.get("data"))
                    case "property-change" if msg.get("name") == "eof-reached":
                        if msg.get("data") is True:
                            self.eof_reached = True
                            if self._input_ended:
                                self._send_nowait("quit")
                    case "end-file":
                        self.end_reason = msg.get("reason")
                    case _:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("mpv IPC loop ended: %s", exc)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(PlayerClosed("mpv IPC connection closed"))

    async def _stderr_loop(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while line := await self.proc.stderr.readline():
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                self.stderr_tail.append(text)
                log.warning("mpv: %s", text)

    def _raise_closed(self) -> NoReturn:
        rc = self.proc.returncode if self.proc else None
        if self.end_reason == "error" or (rc not in (None, 0) and self.end_reason != "quit"):
            detail = " | ".join(self.stderr_tail) or f"exit code {rc}"
            raise PlayerError(f"mpv failed: {detail}")
        raise PlayerClosed(self.end_reason or "closed")

    async def _await_exit_context(self) -> None:
        """Wait briefly for mpv exit code and stderr flush before classifying a broken pipe."""
        proc = self.proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(0.25):
                    await proc.wait()
        if self._tasks:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(0.1):
                    await asyncio.gather(*self._tasks, return_exceptions=True)

    async def write(self, pcm: bytes) -> None:
        proc = self.proc
        assert proc is not None and proc.stdin is not None
        if proc.returncode is not None or proc.stdin.is_closing():
            await self._await_exit_context()
            self._raise_closed()
        try:
            proc.stdin.write(pcm)
            if self.cfg.write_stall_timeout_s > 0:
                async with asyncio.timeout(self.cfg.write_stall_timeout_s):
                    await proc.stdin.drain()
            else:
                await proc.stdin.drain()
        except TimeoutError as exc:
            raise PlayerError(f"mpv stopped consuming audio for {self.cfg.write_stall_timeout_s:.0f}s") from exc
        except ConnectionError, BrokenPipeError:  # PEP 758
            await self._await_exit_context()
            self._raise_closed()

    async def end_input(self) -> None:
        self._input_ended = True
        proc = self.proc
        if proc is not None and proc.stdin is not None and not proc.stdin.is_closing():
            proc.stdin.close()
            with contextlib.suppress(Exception):
                await proc.stdin.wait_closed()

    async def wait(self) -> int:
        assert self.proc is not None
        return await self.proc.wait()

    async def toggle_pause(self) -> bool:
        await self.command("cycle", "pause")
        return bool(await self.command("get_property", "pause"))

    async def time_pos(self) -> float | None:
        with contextlib.suppress(Exception):
            value = await self.command("get_property", "time-pos", timeout=0.5)
            return float(value) if value is not None else None
        return None

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        proc = self.proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(Exception):
                await self.command("quit", timeout=0.4)
            try:
                async with asyncio.timeout(1.0):
                    await proc.wait()
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    async with asyncio.timeout(1.0):
                        await proc.wait()
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
        await self.end_input()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._ipc_writer is not None:
            self._ipc_writer.close()
            with contextlib.suppress(Exception):
                await self._ipc_writer.wait_closed()


# =============================================================================
#  Jobs
# =============================================================================
@dataclass(slots=True, eq=False)
class Job:
    id: str
    preview: str
    title: str
    digest: str
    segments: list[Segment]
    chars: int
    voice_spec: str
    speed: float
    lang: str
    mode: str
    env: dict[str, str]
    client: str
    bit_depth: int = 16
    truncated: bool = False
    created: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    state: str = "queued"
    segments_done: int = 0
    audio_s: float = 0.0
    error: str | None = None
    cancel_reason: str | None = None
    cancelling: bool = False
    player: MpvPlayer | None = None
    subscribers: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list)

    @property
    def ttfa_ms(self) -> int | None:
        return round((self.started_at - self.created) * 1000) if self.started_at else None

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id, "state": self.state, "preview": self.preview, "chars": self.chars,
            "segments": len(self.segments), "segments_done": self.segments_done, "audio_s": round(self.audio_s, 2),
            "ttfa_ms": self.ttfa_ms, "voice": self.voice_spec, "speed": self.speed, "lang": self.lang, "mode": self.mode,
            "error": self.error, "cancel_reason": self.cancel_reason, "truncated": self.truncated,
        }


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            self._fd = None


def activation_socket() -> socket.socket | None:
    """systemd socket activation (sd_listen_fds semantics, fd 3)."""
    if os.environ.get("LISTEN_PID") != str(os.getpid()):
        return None
    count = int(os.environ.get("LISTEN_FDS", "0") or 0)
    if count < 1:
        return None
    if count > 1:
        raise DuskyError(f"expected exactly one activation socket, got {count}")
    sock = socket.socket(fileno=3)
    if sock.family != socket.AF_UNIX or sock.type != socket.SOCK_STREAM:
        raise DuskyError("activation fd 3 is not an AF_UNIX/SOCK_STREAM socket")
    for key in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        os.environ.pop(key, None)
    return sock


# =============================================================================
#  Daemon
# =============================================================================
class Daemon:
    def __init__(self, cfg: Config, paths: Paths) -> None:
        self.cfg = cfg
        self.paths = paths
        self.voices = VoiceBank(paths.voices_file)
        self.engine = Engine(cfg, paths, self.voices)
        self.jobs: asyncio.Queue[Job] = asyncio.Queue()
        self.server: asyncio.base_events.Server | None = None
        self.current: Job | None = None
        self.last_job: Job | None = None
        self.clients = 0
        self.started = time.monotonic()
        self.last_activity = time.monotonic()
        self.jobs_total = 0
        self._job_task: asyncio.Task[None] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._idle_wakeup = asyncio.Event()
        self._client_tasks: set[asyncio.Task[Any]] = set()
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        self._shutdown = asyncio.Event()
        self._shutdown_reason = ""
        self._owns_socket = False
        self._last_raw_digest = ""
        self._last_digest = ""
        self._last_digest_at = 0.0
        self._notified_degraded = False
        self._job_counter = 0
        self._prepare_generation = 0
        self.is_synthesizing = False

    def _poke_idle(self) -> None:
        self._idle_wakeup.set()

    # ---- lifecycle ----------------------------------------------------------
    async def serve(self) -> int:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(self._loop_exception_handler)
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.request_shutdown, sig.name)
        loop.add_signal_handler(signal.SIGHUP, lambda: self._spawn(self._reload_config(), "reload"))

        listen = activation_socket()
        if listen is None:
            listen = self._bind_socket()
            log.info("Listening on %s (standalone)", self.paths.socket)
        else:
            log.info("Listening on inherited socket %s (systemd socket activation)", listen.getsockname())
        self.server = await asyncio.start_unix_server(
            self._handle_client, sock=listen,
            limit=self.cfg.daemon.max_request_bytes,
            cleanup_socket=self._owns_socket,
        )
        self._worker_task = asyncio.create_task(self._worker(), name="worker")
        self._idle_task = asyncio.create_task(self._idle_ticker(), name="idle-ticker")
        with contextlib.suppress(OSError):
            self.paths.socket.with_name("daemon.pid").write_text(f"{os.getpid()}\n")
        sd_notify("READY=1\nSTATUS=Idle (model not loaded)")
        log.info("Dusky Kokoro %s ready (pid %d, python %s, free-threading=%s)", VERSION, os.getpid(),
                 sys.version.split()[0], "on" if not sys._is_gil_enabled() else "off")
        await self._shutdown.wait()
        log.info("Shutting down (%s)", self._shutdown_reason)
        await self._graceful_shutdown()
        return 0

    def _bind_socket(self) -> socket.socket:
        path = self.paths.socket
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(path.parent, 0o700)
        if path.is_socket() or path.exists():
            path.unlink()  # safe: the instance lock proves no live daemon owns it
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(path))
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        sock.listen(32)
        self._owns_socket = True
        return sock

    def request_shutdown(self, reason: str) -> None:
        if not self._shutdown.is_set():
            self._shutdown_reason = reason
            self._shutdown.set()
            self._poke_idle()

    async def _graceful_shutdown(self) -> None:
        sd_notify("STOPPING=1")
        with contextlib.suppress(OSError):
            self.paths.socket.with_name("daemon.pid").unlink(missing_ok=True)
        if self.server is not None:
            self.server.close()
        for task in list(self._client_tasks):
            task.cancel()
        self.stop_all("shutdown")
        self.jobs.shutdown(immediate=True)
        if self._idle_task is not None:
            self._idle_task.cancel()
        pending = [t for t in (self._worker_task, self._idle_task, self._job_task) if t is not None]
        pending += list(self._client_tasks) + list(self._bg_tasks)
        with contextlib.suppress(Exception):
            async with asyncio.timeout(5.0):
                await asyncio.gather(*pending, return_exceptions=True)
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
        if self.server is not None:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(2.0):
                    await self.server.wait_closed()
        await self.engine.shutdown()
        if self._owns_socket:
            self.paths.socket.unlink(missing_ok=True)

    def _loop_exception_handler(self, loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        log.error("asyncio: %s", context.get("message"), exc_info=exc)

    def _spawn(self, coro: Any, name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _set_status(self, text: str) -> None:
        sd_notify(f"STATUS={text}")

    # ---- desktop notifications ----------------------------------------------
    def notify(self, summary: str, body: str, urgency: str = "normal", env: dict[str, str] | None = None) -> None:
        if not self.cfg.daemon.desktop_notifications or shutil.which("notify-send") is None:
            return

        async def _run() -> None:
            with contextlib.suppress(Exception):
                proc = await asyncio.create_subprocess_exec(
                    "notify-send", "-a", "Dusky Kokoro", "-u", urgency, "-t", "5000",
                    "-h", "string:x-canonical-private-synchronous:dusky-kokoro", summary, body,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    env=self._player_env(env or {}))
                await proc.wait()

        self._spawn(_run(), "notify")

    def _player_env(self, job_env: dict[str, str]) -> dict[str, str]:
        env = os.environ.copy()
        env.update({k: v for k, v in job_env.items() if k in CLIENT_ENV_KEYS and isinstance(v, str)})
        return env

    # ---- control connections ------------------------------------------------
    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._client_tasks.add(task)
        self.clients += 1
        try:
            while not self._shutdown.is_set():
                try:
                    async with asyncio.timeout(self.cfg.daemon.request_timeout_s):
                        line = await reader.readline()
                except TimeoutError:
                    break
                except (ValueError, asyncio.LimitOverrunError):  # PEP 758: oversized line
                    await self._send(writer, {"ok": False, "error": "request exceeds daemon.max_request_bytes"})
                    break
                if not line:
                    break
                try:
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        raise ValueError("request must be a JSON object")
                except ValueError as exc:
                    await self._send(writer, {"ok": False, "error": f"invalid JSON request: {exc}"})
                    continue
                await self._dispatch(request, writer)
        except (ConnectionError, BrokenPipeError, asyncio.CancelledError):  # PEP 758
            pass
        finally:
            self.clients -= 1
            if task is not None:
                self._client_tasks.discard(task)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            self._poke_idle()

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
        writer.write(json.dumps(message, ensure_ascii=False).encode() + b"\n")
        await writer.drain()

    async def _dispatch(self, req: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        cmd = req.get("cmd")
        if cmd not in ("ping", "status"):
            self.last_activity = time.monotonic()
        match cmd:
            case "ping":
                await self._send(writer, {"ok": True, "event": "pong", "version": VERSION, "protocol": PROTOCOL, "pid": os.getpid()})
            case "status":
                await self._send(writer, await self._status())
            case "speak":
                await self._cmd_speak(req, writer)
            case "stop":
                had_job = self.current is not None
                flushed = self.stop_all("stop command")
                await self._send(writer, {"ok": True, "event": "stopped", "was_playing": had_job, "flushed": flushed})
            case "pause":
                await self._send(writer, await self._cmd_pause())
            case "unload":
                self.stop_all("unload command")
                if self._job_task is not None:
                    await asyncio.gather(self._job_task, return_exceptions=True)
                await self.engine.unload("unload command")
                self._set_status("Idle (model not loaded)")
                self._poke_idle()
                await self._send(writer, {"ok": True, "event": "unloaded"})
            case "reload":
                await self._send(writer, await self._reload_config())
            case "loglevel":
                level = str(req.get("level", "INFO")).upper()
                if level not in _CHOICES[("logging", "level")]:
                    await self._send(writer, {"ok": False, "error": f"invalid level {level}"})
                else:
                    logging.getLogger().setLevel(level)
                    await self._send(writer, {"ok": True, "event": "loglevel", "level": level})
            case "shutdown":
                await self._send(writer, {"ok": True, "event": "shutting_down"})
                self.request_shutdown("shutdown command")
            case _:
                await self._send(writer, {"ok": False, "error": f"unknown command {cmd!r}"})

    async def _cmd_pause(self) -> dict[str, Any]:
        job = self.current
        if job is None or job.player is None or job.player.proc is None:
            return {"ok": True, "event": "pause", "paused": None, "note": "nothing is playing"}
        try:
            paused = await job.player.toggle_pause()
        except DuskyError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "event": "pause", "paused": paused, "job": job.id}

    async def _cmd_speak(self, req: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        cfg = self.cfg
        text = req.get("text")
        if not isinstance(text, str) or not text.strip():
            await self._send(writer, {"ok": False, "error": "empty text"})
            return
        truncated = False
        if cfg.text.max_chars > 0 and len(text) > cfg.text.max_chars:
            text, truncated = text[:cfg.text.max_chars], True
        mode = str(req.get("mode") or cfg.daemon.default_mode)
        if mode not in ("interrupt", "enqueue"):
            await self._send(writer, {"ok": False, "error": f"invalid mode {mode!r}"})
            return
        if mode == "enqueue" and self.jobs.qsize() >= cfg.daemon.max_queue:
            await self._send(writer, {"ok": False, "error": f"queue full ({cfg.daemon.max_queue})"})
            return
        voice_spec = str(req.get("voice") or cfg.voice.spec)
        try:
            VoiceBank.parse_spec(voice_spec)
            raw_speed = req.get("speed")
            if isinstance(raw_speed, bool):
                raise ValueError("speed must be a number")
            speed = cfg.voice.speed if raw_speed is None else float(raw_speed)
        except (VoiceError, ValueError, TypeError) as exc:
            await self._send(writer, {"ok": False, "error": str(exc)})
            return
        if not 0.5 <= speed <= 2.0:
            await self._send(writer, {"ok": False, "error": "speed must be within 0.5 .. 2.0"})
            return
        lang = str(req.get("lang") or VoiceBank.lang_for(voice_spec, cfg.voice.lang))
        wait = str(req.get("wait") or "accepted")
        raw_env = req.get("env") if isinstance(req.get("env"), dict) else {}

        # Fast pre-normalization dedup check prevents accidental hotkey double-taps
        # from interrupting active playback or doing redundant text prep.
        now = time.monotonic()
        raw_digest = hashlib.blake2b(
            json.dumps([text, voice_spec, speed, lang], ensure_ascii=False).encode(), digest_size=16
        ).hexdigest()
        if raw_digest == self._last_raw_digest and now - self._last_digest_at < cfg.daemon.dedup_window_s:
            self._last_digest_at = now
            await self._send(writer, {"ok": True, "event": "deduplicated", "window_s": cfg.daemon.dedup_window_s})
            return

        if mode == "interrupt":
            # Stop prior playback & invalidate any older in-flight preparations immediately.
            self.stop_all("superseded by a new request")

        generation = self._prepare_generation
        paragraphs, segments = await asyncio.to_thread(prepare_text, text, cfg.text, lang)
        if generation != self._prepare_generation:
            await self._send(writer, {"ok": True, "event": "cancelled", "reason": "superseded during text preparation"})
            return
        if not segments:
            await self._send(writer, {"ok": False, "error": "nothing readable after normalisation"})
            return
        flat = flatten_paragraphs(paragraphs)
        digest = hashlib.blake2b(
            json.dumps([flat, voice_spec, speed, lang], ensure_ascii=False).encode(), digest_size=16
        ).hexdigest()
        now = time.monotonic()
        if digest == self._last_digest and now - self._last_digest_at < cfg.daemon.dedup_window_s:
            self._last_digest_at = now
            await self._send(writer, {"ok": True, "event": "deduplicated", "window_s": cfg.daemon.dedup_window_s})
            return
        if self.jobs.qsize() >= cfg.daemon.max_queue:
            await self._send(writer, {"ok": False, "error": f"queue full ({cfg.daemon.max_queue})"})
            return

        self._job_counter += 1
        job = Job(
            id=f"{self._job_counter:04d}-{digest[:6]}", preview=flat[:80].replace("\n", " "), title=make_slug(paragraphs),
            digest=digest, segments=segments, chars=len(flat), voice_spec=voice_spec, speed=speed, lang=lang,
            mode=mode, env={k: str(v) for k, v in raw_env.items() if k in CLIENT_ENV_KEYS},
            client=str(req.get("client") or "unknown"), bit_depth=cfg.archive.bit_depth, truncated=truncated,
        )
        events: asyncio.Queue[dict[str, Any]] | None = None
        if wait == "done":
            events = asyncio.Queue()
            job.subscribers.append(events)
        queued_ahead = self.jobs.qsize() + (1 if self.current else 0)
        self.jobs.put_nowait(job)
        self._last_raw_digest = raw_digest
        self._last_digest, self._last_digest_at = digest, now
        self.jobs_total += 1
        self.last_activity = now
        self._poke_idle()
        log.info("job %s accepted: %d chars, %d segments, voice=%s, lang=%s, mode=%s, client=%s",
                 job.id, job.chars, len(segments), voice_spec, lang, mode, job.client)
        await self._send(writer, {
            "ok": True, "event": "accepted", "job": job.id, "chars": job.chars, "segments": len(segments),
            "paragraphs": len(paragraphs), "queued_ahead": queued_ahead, "mode": mode, "voice": voice_spec,
            "lang": lang, "speed": speed, "truncated": truncated, "engine_loaded": self.engine.loaded,
        })
        if events is None:
            return
        try:
            while True:
                message = await events.get()
                await self._send(writer, message)
                if message.get("event") in TERMINAL_EVENTS:
                    break
        finally:
            with contextlib.suppress(ValueError):
                job.subscribers.remove(events)

    async def _status(self) -> dict[str, Any]:
        job = self.current
        current: dict[str, Any] | None = None
        if job is not None:
            current = job.summary()
            if job.player is not None:
                current["paused"] = job.player.paused
                current["time_pos"] = await job.player.time_pos()
        st = self.engine.stats
        return {
            "ok": True, "event": "status", "version": VERSION, "protocol": PROTOCOL, "pid": os.getpid(),
            "uptime_s": round(time.monotonic() - self.started, 1),
            "python": sys.version.split()[0], "free_threading": not sys._is_gil_enabled(),
            "socket": str(self.paths.socket), "socket_activated": not self._owns_socket,
            "config": str(self.paths.config_file),
            "engine": {
                "loaded": self.engine.loaded, "requested_provider": self.cfg.engine.provider,
                "active_kind": self.engine.active_kind, "active_providers": self.engine.active_providers,
                "degraded": self.engine.degraded, "model": self.engine.model_path.name if self.engine.model_path else None,
                "precision": self.engine.model_precision, "idle_s": round(time.monotonic() - self.engine.last_used, 1),
                "model_idle_timeout_s": self.cfg.engine.model_idle_timeout_s,
                "stats": {"loads": st.loads, "last_load_s": round(st.last_load_s, 3), "segments": st.segments,
                          "synth_s": round(st.synth_s, 3), "audio_s": round(st.audio_s, 3),
                          "rtf": round(st.rtf, 4) if st.rtf is not None else None},
            },
            "voice": {"spec": self.cfg.voice.spec, "lang": VoiceBank.lang_for(self.cfg.voice.spec, self.cfg.voice.lang),
                      "speed": self.cfg.voice.speed},
            "queue": {"depth": self.jobs.qsize(), "max": self.cfg.daemon.max_queue, "total_jobs": self.jobs_total},
            "current": current,
            "last_job": self.last_job.summary() if self.last_job else None,
            "clients": self.clients,
            "rss_mb": round(_rss_bytes() / (1024 * 1024), 1),
        }

    async def _reload_config(self) -> dict[str, Any]:
        try:
            new_cfg = load_config(self.paths.config_file)
        except ConfigError as exc:
            log.error("config reload failed: %s", exc)
            return {"ok": False, "error": str(exc)}
        # Voice, text, playback, archive, and daemon settings are applied dynamically
        # per job/segment so only actual engine or ORT logging changes require reloading the worker.
        engine_changed = (
            new_cfg.engine != self.cfg.engine
            or new_cfg.logging.ort_verbose != self.cfg.logging.ort_verbose
        )
        self.cfg = new_cfg
        self.paths = resolve_paths(new_cfg, self.paths.config_file, str(self.paths.socket))
        self.engine.cfg = new_cfg
        self.engine.paths = self.paths
        if self.paths.voices_file != self.voices.path:
            self.voices.reset(self.paths.voices_file)
        logging.getLogger().setLevel(new_cfg.logging.level)
        if engine_changed:
            self._notified_degraded = False
            self.engine.reload_pending = True
            if self.current is None:
                await self.engine.unload("configuration reload")
        self._poke_idle()
        log.info("configuration reloaded from %s (engine_changed=%s)", self.paths.config_file, engine_changed)
        return {"ok": True, "event": "reloaded", "engine_changed": engine_changed}

    # ---- job control --------------------------------------------------------
    def stop_all(self, reason: str) -> int:
        self._prepare_generation += 1
        flushed = self._clear_queue(reason)
        job = self.current
        if job is not None and self._job_task is not None and not self._job_task.done() and not job.cancelling:
            # exactly one cancel(): a second CancelledError would land inside the job's cleanup finally-block
            job.cancelling = True
            job.cancel_reason = reason
            self.engine.interrupt()
            self._job_task.cancel()
        return flushed

    def _clear_queue(self, reason: str) -> int:
        flushed = 0
        while True:
            try:
                job = self.jobs.get_nowait()
            except asyncio.QueueEmpty, asyncio.QueueShutDown:  # PEP 758
                break
            job.cancel_reason = reason
            job.state = "cancelled"
            self._emit(job, "cancelled", reason=reason)
            self.jobs.task_done()
            flushed += 1
        return flushed

    def _emit(self, job: Job, event: str, **fields_: Any) -> None:
        message = {"ok": event != "error", "event": event, "job": job.id, **fields_}
        for queue in job.subscribers:
            queue.put_nowait(message)
        if event != "started":
            log.info("job %s %s %s", job.id, event, json.dumps(fields_, ensure_ascii=False) if fields_ else "")

    async def _worker(self) -> None:
        while True:
            try:
                job = await self.jobs.get()
            except asyncio.QueueShutDown:
                return
            if job.cancel_reason:
                self._emit(job, "cancelled", reason=job.cancel_reason)
                self.jobs.task_done()
                continue
            self.current = job
            self._job_task = asyncio.create_task(self._run_job(job), name=f"job-{job.id}")
            try:
                await asyncio.wait({self._job_task})
                if not self._job_task.cancelled() and (exc := self._job_task.exception()) is not None:
                    log.error("job %s cleanup raised", job.id, exc_info=exc)
            finally:
                self.current = None
                self._job_task = None
                self.last_job = job
                self.jobs.task_done()
                self.last_activity = time.monotonic()
                self._set_status("Idle (model loaded)" if self.engine.loaded else "Idle (model not loaded)")
                self._poke_idle()

    async def _produce(self, job: Job, chunks: asyncio.Queue[bytes | None]) -> None:
        """Synthesis producer. End-of-stream handoff rules:
        * normal / error paths: the consumer is alive and draining, so a blocking put(None) always completes;
        * cancellation: the consumer may already be gone, so never block - a lost sentinel is harmless then."""
        sample_width = sample_width_for(job.bit_depth)
        max_phonemes = self.cfg.text.max_phonemes
        trim_silence = self.cfg.text.trim_silence
        try:
            for index, segment in enumerate(job.segments):
                pcm = await self.engine.synthesize(
                    segment.text, job.voice_spec, job.speed, job.lang, segment.pause_ms,
                    bit_depth=job.bit_depth,
                    max_phonemes=max_phonemes,
                    trim_silence=trim_silence,
                )
                job.segments_done = index + 1
                job.audio_s += len(pcm) / (sample_width * SAMPLE_RATE)
                await chunks.put(pcm)
        except asyncio.CancelledError:
            with contextlib.suppress(asyncio.QueueFull):
                chunks.put_nowait(None)
            raise
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
            await chunks.put(None)
            raise
        else:
            await chunks.put(None)

    async def _run_job(self, job: Job) -> None:
        cfg = self.cfg
        player: MpvPlayer | None = None
        producer: asyncio.Task[None] | None = None
        archive: ArchiveWriter | None = None
        outcome = "error"
        detail: dict[str, Any] = {}
        try:
            job.state = "loading"
            self._set_status(f"Loading model ({job.id})")
            await self.engine.ensure_loaded()
            for warning in self.engine.pop_warnings():
                if not self._notified_degraded:
                    self._notified_degraded = True
                    self.notify("Kokoro TTS: running degraded", warning, "normal", job.env)
            job.state = "synthesizing"
            player = MpvPlayer(cfg.playback, self._player_env(job.env), job.title, bit_depth=job.bit_depth)
            job.player = player
            await player.start()
            if cfg.archive.enabled:
                try:
                    archive = await asyncio.to_thread(ArchiveWriter.create, self.paths.archive_dir, job.title, job.bit_depth)
                except OSError as exc:
                    log.warning("archive disabled for this job: %s", exc)
            chunks: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=cfg.playback.prefetch_segments)
            self.is_synthesizing = True
            producer = asyncio.create_task(self._produce(job, chunks), name=f"synth-{job.id}")
            first = True
            try:
                while (pcm := await chunks.get()) is not None:
                    if first:
                        first = False
                        job.started_at = time.monotonic()
                        job.state = "playing"
                        self._emit(job, "started", ttfa_ms=job.ttfa_ms, engine=self.engine.active_kind)
                        self._set_status(f"Speaking ({job.id}, {len(job.segments)} segments)")
                        log.info("job %s first audio after %d ms", job.id, job.ttfa_ms or 0)
                    await player.write(pcm)
                    if archive is not None:
                        try:
                            await asyncio.to_thread(archive.write, pcm)
                        except (OSError, wave.Error, struct.error) as exc:
                            log.warning("archive failed; continuing playback: %s", exc)
                            with contextlib.suppress(OSError, wave.Error, struct.error):
                                await asyncio.to_thread(archive.close, True)
                            archive = None
                await producer
            finally:
                self.is_synthesizing = False
                self.engine.touch()
                self._poke_idle()

            if first:
                raise EngineError("no audio produced")

            if self.jobs.empty() and self.cfg.engine.model_idle_timeout_s == 0.0:
                await self.engine.unload("synthesis complete (model_idle_timeout_s = 0)")

            await player.end_input()
            if cfg.playback.use_user_mpv_config:
                with contextlib.suppress(Exception):
                    await player.command("set_property", "keep-open", "no", timeout=0.5)
                if player.eof_reached or await player.is_eof():
                    player.eof_reached = True
                    player._send_nowait("quit")
            rc = await player.wait()
            if rc != 0 or player.end_reason == "error":
                player._raise_closed()
            if player.end_reason == "quit" and not player.eof_reached:
                outcome = "cancelled"
                job.cancel_reason = "player closed by user"
                self._clear_queue("player closed by user")
            else:
                outcome = "finished"
        except asyncio.CancelledError:
            outcome = "cancelled"
            job.cancel_reason = job.cancel_reason or "interrupted"
            raise
        except PlayerClosed as exc:
            outcome = "cancelled"
            job.cancel_reason = f"player closed ({exc})"
            self._clear_queue("player closed by user")
        except Exception as exc:
            outcome = "error"
            job.error = job.error or f"{type(exc).__name__}: {exc}"
            log.error("job %s failed: %s", job.id, job.error, exc_info=not isinstance(exc, DuskyError))
            self.notify("Kokoro TTS error", job.error, "critical", job.env)
        finally:
            if producer is not None and not producer.done():
                self.engine.interrupt()
                producer.cancel()
                with contextlib.suppress(BaseException):
                    await producer
            if player is not None:
                await player.stop()
            if archive is not None:
                try:
                    await asyncio.to_thread(archive.close, outcome == "error", cfg.archive.max_files)
                except (OSError, wave.Error, struct.error) as exc:
                    log.warning("archive finalization failed: %s", exc)
                    archive = None
            job.finished_at = time.monotonic()
            job.state = outcome
            job.player = None
            match outcome:
                case "finished":
                    detail = {"audio_s": round(job.audio_s, 2), "segments": job.segments_done, "ttfa_ms": job.ttfa_ms,
                              "wall_s": round(job.finished_at - job.created, 2),
                              "archive": str(archive.path) if archive is not None else None}
                case "cancelled":
                    detail = {"reason": job.cancel_reason, "segments_done": job.segments_done}
                case _:
                    detail = {"error": job.error}
            self._emit(job, outcome, **detail)
            self.engine.touch()
            self._poke_idle()

    async def _idle_ticker(self) -> None:
        """Event-driven idle manager: zero timer wakeups while active or permanently idle."""
        while not self._shutdown.is_set():
            self._idle_wakeup.clear()
            now = time.monotonic()
            engine_busy = (
                (self.current is not None and self.current.state in ("loading", "synthesizing"))
                or self.is_synthesizing
                or not self.jobs.empty()
            )
            if self.engine.loaded and not engine_busy and now - self.engine.last_used >= self.cfg.engine.model_idle_timeout_s:
                await self.engine.unload(f"idle for {self.cfg.engine.model_idle_timeout_s:.0f}s")
                if self.current is not None:
                    self._set_status(f"Speaking (model unloaded: {self.current.id})")
                else:
                    self._set_status("Idle (model not loaded)")
                continue

            busy = self.current is not None or not self.jobs.empty()
            if (
                not busy
                and self.cfg.daemon.exit_when_idle
                and not self.engine.loaded
                and self.clients == 0
                and now - self.last_activity >= self.cfg.daemon.process_idle_timeout_s
            ):
                log.info("Process idle for %.0fs - exiting; socket activation / trigger.sh relaunches on demand",
                         self.cfg.daemon.process_idle_timeout_s)
                self.request_shutdown("idle timeout")
                return

            wait_s: float | None = None
            if self.engine.loaded and not engine_busy:
                wait_s = max(0.05, self.cfg.engine.model_idle_timeout_s - (now - self.engine.last_used))
            if not busy and self.cfg.daemon.exit_when_idle and not self.engine.loaded and self.clients == 0:
                proc_rem = max(0.05, self.cfg.daemon.process_idle_timeout_s - (now - self.last_activity))
                wait_s = proc_rem if wait_s is None else min(wait_s, proc_rem)

            if wait_s is None:
                await self._idle_wakeup.wait()
            else:
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(wait_s):
                        await self._idle_wakeup.wait()


def _rss_bytes() -> int:
    try:
        with open("/proc/self/statm", "rb") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except OSError, ValueError, IndexError:  # PEP 758
        return 0


# =============================================================================
#  Control client (stdlib only)
# =============================================================================
def client_request(socket_path: Path, payload: dict[str, Any], *, timeout: float, stream: bool,
                   on_message: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(min(timeout, 5.0))
            sock.connect(str(socket_path))
            sock.settimeout(timeout)
            sock.sendall(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
            sock.shutdown(socket.SHUT_WR)
            last: dict[str, Any] | None = None
            with sock.makefile("rb") as stream_in:
                for raw in stream_in:
                    try:
                        message = json.loads(raw)
                    except ValueError as exc:
                        raise ClientError(f"malformed reply from daemon: {exc}", 3) from exc
                    on_message(message)
                    last = message
                    if stream:
                        sock.settimeout(None)
                    if not stream or message.get("event") in TERMINAL_EVENTS or not message.get("ok", True):
                        break
            if last is None:
                raise ClientError("daemon closed the connection without replying", 3)
            return last
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        raise ClientError(f"daemon not reachable at {socket_path} ({exc.strerror or exc})", 2) from exc
    except (BrokenPipeError, ConnectionResetError) as exc:
        raise ClientError(f"daemon connection closed: {exc}", 3) from exc
    except TimeoutError as exc:
        raise ClientError(f"timed out after {timeout:.0f}s waiting for the daemon", 4) from exc


def _kv_lines(message: dict[str, Any], prefix: str = "") -> Iterable[str]:
    for key, value in message.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            yield from _kv_lines(value, name + ".")
        elif isinstance(value, bool):
            yield f"{name}={'true' if value else 'false'}"
        elif value is None:
            yield f"{name}="
        elif isinstance(value, (list, tuple)):
            yield f"{name}={json.dumps(value, ensure_ascii=False)}"
        else:
            yield f"{name}={str(value).replace(chr(10), ' ')}"


def _print_message(message: dict[str, Any], fmt: str) -> None:
    if fmt == "kv":
        for line in _kv_lines(message):
            print(line)
    elif fmt == "pretty":
        print(json.dumps(message, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(message, ensure_ascii=False))
    sys.stdout.flush()


def run_client(args: argparse.Namespace) -> int:
    override = args.socket or os.environ.get("DUSKY_SOCKET")
    if override:
        socket_path = Path(os.path.expandvars(override)).expanduser()
    else:
        config_file = Path(args.config) if args.config else default_config_path()
        # Control commands need only the transport path so reload/stop still work
        # even when other settings in config.toml fail validation.
        try:
            data = tomllib.loads(config_file.read_text(encoding="utf-8")) if config_file.is_file() else {}
            configured = data.get("daemon", {}).get("socket_path", "")
            socket_path = Path(os.path.expandvars(configured)).expanduser() if configured else default_socket_path()
        except (OSError, tomllib.TOMLDecodeError, TypeError, AttributeError) as exc:
            raise ConfigError(f"{config_file}: cannot resolve control socket: {exc}") from exc
    payload: dict[str, Any] = {"cmd": args.command, "client": "cli", "protocol": PROTOCOL}
    stream = False
    match args.command:
        case "speak":
            if args.file:
                try:
                    text = extract_text_from_file(args.file)
                except Exception as exc:
                    print(f"speak: error reading {args.file}: {exc}", file=sys.stderr)
                    return 66
            elif (args.text is not None or getattr(args, "text_flag", None) is not None) and not args.stdin:
                candidate = args.text if args.text is not None else args.text_flag
                cand_path = Path(candidate).expanduser() if candidate and args.text is not None else None
                try:
                    is_file = cand_path is not None and cand_path.is_file()
                except OSError:
                    is_file = False
                if is_file:
                    try:
                        text = extract_text_from_file(cand_path)
                    except Exception as exc:
                        print(f"speak: error reading {candidate}: {exc}", file=sys.stderr)
                        return 66
                else:
                    text = candidate
            else:
                if sys.stdin.isatty() and not args.stdin:
                    print("speak: provide TEXT, [FILE], --file PATH or --stdin", file=sys.stderr)
                    return 64
                text = sys.stdin.buffer.read().decode("utf-8", "replace")

            if not text or not text.strip():
                print("speak: text/file is empty (nothing to speak)", file=sys.stderr)
                return 0

            payload.update({
                "text": text, "mode": args.mode, "voice": args.voice, "speed": args.speed, "lang": args.lang,
                "wait": "done" if args.wait else "accepted",
                "env": {k: os.environ[k] for k in CLIENT_ENV_KEYS if k in os.environ},
                "client": args.client,
            })
            stream = bool(args.wait)
        case "loglevel":
            payload["level"] = args.level
        case _:
            pass
    try:
        last = client_request(socket_path, payload, timeout=args.timeout, stream=stream,
                              on_message=lambda m: _print_message(m, args.format))
    except ClientError as exc:
        _print_message({"ok": False, "error": str(exc), "exit_code": exc.exit_code}, args.format)
        return exc.exit_code
    return 0 if last.get("ok", False) else 1


# =============================================================================
#  Diagnostics: doctor / synth / voices / normalize / config
# =============================================================================
def _cli_config(args: argparse.Namespace) -> tuple[Config, Path]:
    config_file = Path(args.config) if getattr(args, "config", None) else default_config_path()
    return load_config(config_file), config_file


def _apply_engine_env(cfg: Config) -> None:
    for key, value in cfg.engine.env.items():
        os.environ[key] = str(value)


def run_doctor(args: argparse.Namespace) -> int:
    cfg, config_file = _cli_config(args)
    paths = resolve_paths(cfg, config_file, args.socket)
    _apply_engine_env(cfg)
    ok = True

    def row(label: str, value: Any) -> None:
        print(f"  {label:<18} {value}")

    print(f"Dusky Kokoro {VERSION} - doctor")
    row("python", f"{sys.version.split()[0]}  ({sys.executable})  free-threading={'on' if not sys._is_gil_enabled() else 'off'}")
    row("config", f"{config_file}  ({'found' if config_file.is_file() else 'defaults'})")
    row("socket", f"{paths.socket}  ({'present' if paths.socket.exists() else 'absent'})")
    row("models dir", paths.models_dir)
    for key, name in MODEL_FILES.items():
        path = paths.models_dir / name
        row(f"  model {key}", f"{'ok' if path.is_file() else 'missing'}  {path.stat().st_size if path.is_file() else 0:,} bytes")
    if paths.voices_file.is_file():
        try:
            names = VoiceBank(paths.voices_file).names()
            row("voices", f"{len(names)} styles ({paths.voices_file.name})")
        except Exception as exc:
            ok = False
            row("voices", f"UNREADABLE: {exc}")
    else:
        ok = False
        row("voices", f"missing: {paths.voices_file}")
    for tool in (cfg.playback.mpv_binary, "notify-send", "wl-paste", "pdftotext", "systemctl"):
        row(f"tool {tool}", shutil.which(tool) or "-")
    for key in ("HSA_OVERRIDE_GFX_VERSION", "MIOPEN_FIND_MODE", "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "ONNX_PROVIDER"):
        if key in os.environ:
            row(f"env {key}", os.environ[key])
    try:
        from importlib.metadata import version as pkg_version
        import numpy
        import onnxruntime as ort
        row("numpy", numpy.__version__)
        row("onnxruntime", f"{ort.__version__}  device={ort.get_device()}")
        row("kokoro-onnx", pkg_version("kokoro-onnx"))
        available = ort.get_available_providers()
        row("providers", ", ".join(available))
        chain = provider_chain(cfg.engine.provider, available)
        row("provider chain", " > ".join(chain) + f"  (requested: {cfg.engine.provider})")
        precision, model = choose_model(cfg.engine.precision, chain[0], paths.models_dir)
        row("model choice", f"{precision} -> {model.name}")
        if chain[0] in ("cuda", "tensorrt"):
            preload = getattr(ort, "preload_dlls", None)
            if preload is None:
                ok = False
                row("cuda preload", "onnxruntime.preload_dlls missing (need onnxruntime-gpu >= 1.21)")
            else:
                dirs: tuple[str | None, ...] = tuple(os.path.expanduser(d) for d in cfg.engine.cuda_lib_dirs) or (None,)
                try:
                    for directory in dirs:
                        preload(cuda=True, cudnn=True, msvc=False, directory=directory)
                    source_desc = ", ".join(d for d in dirs if d) or "NVIDIA pip wheels"
                    row("cuda preload", f"ok ({source_desc})")
                except Exception as exc:
                    ok = False
                    row("cuda preload", f"FAILED: {exc}")
                if hasattr(ort, "print_debug_info"):
                    print("  --- onnxruntime.print_debug_info() ---")
                    ort.print_debug_info()
    except Exception as exc:
        ok = False
        row("runtime", f"IMPORT FAILED: {type(exc).__name__}: {exc}")
    if args.synth:
        synth_result = run_synth(args, cfg=cfg, config_file=config_file)
        return synth_result if ok else 1
    print("  result             " + ("OK" if ok else "PROBLEMS FOUND"))
    return 0 if ok else 1


def run_synth(args: argparse.Namespace, cfg: Config | None = None, config_file: Path | None = None) -> int:
    if cfg is None or config_file is None:
        cfg, config_file = _cli_config(args)
    paths = resolve_paths(cfg, config_file, args.socket)
    _apply_engine_env(cfg)
    text = args.text or ("The quick brown fox jumps over the lazy dog. Dusky Kokoro is ready, "
                         "and this sentence exists to measure the real-time factor of the engine.")
    voice_spec = args.voice or cfg.voice.spec
    lang = args.lang or VoiceBank.lang_for(voice_spec, cfg.voice.lang)
    speed = cfg.voice.speed if args.speed is None else args.speed
    if not 0.5 <= speed <= 2.0:
        raise ConfigError("speed must be within 0.5 .. 2.0")
    bit_depth = cfg.archive.bit_depth
    sample_width = sample_width_for(bit_depth)
    engine = Engine(cfg, paths, VoiceBank(paths.voices_file), is_worker=True)
    report: dict[str, Any] = {"provider_requested": cfg.engine.provider, "voice": voice_spec, "lang": lang, "speed": speed}
    writer: ArchiveWriter | None = None
    writer_closed = False
    try:
        t0 = time.perf_counter()
        engine._load_sync()
        report["load_s"] = round(time.perf_counter() - t0, 3)
        report["active_providers"] = engine.active_providers
        report["model"] = engine.model_path.name if engine.model_path else None
        report["degraded"] = engine.degraded
        vec = engine.voices.resolve(voice_spec)
        segments = segment_text(normalize_text(text, cfg.text, lang), cfg.text)
        report["segments"] = len(segments)
        out = Path(args.out) if args.out else Path(tempfile.gettempdir()) / f"dusky-kokoro-synth-{os.getpid()}.wav"
        writer = ArchiveWriter(out, bit_depth)
        audio_bytes = 0
        t1 = time.perf_counter()
        first_ms: float | None = None
        for seg in segments:
            pcm = engine._synth_sync(seg.text, vec, speed, lang, seg.pause_ms, bit_depth=bit_depth)
            writer.write(pcm)
            audio_bytes += len(pcm)
            if first_ms is None:
                first_ms = (time.perf_counter() - t1) * 1000
        synth_s = time.perf_counter() - t1
        audio_s = audio_bytes / (sample_width * SAMPLE_RATE)
        report.update({"first_segment_ms": round(first_ms or 0, 1), "synth_s": round(synth_s, 3),
                       "audio_s": round(audio_s, 3), "rtf": round(synth_s / audio_s, 4) if audio_s else None})
        writer.close()
        writer_closed = True
        report["wav"] = str(out)
        a = engine._synth_sync("Testing the playback speed control of this engine.", vec, 1.0, lang, 0, bit_depth=bit_depth)
        b = engine._synth_sync("Testing the playback speed control of this engine.", vec, 1.5, lang, 0, bit_depth=bit_depth)
        ratio = len(a) / len(b) if len(b) else 0.0
        report["model_speed_ratio_1p0_over_1p5"] = round(ratio, 3)
        report["model_speed_effective"] = ratio > 1.25
        for warning in engine.pop_warnings():
            report.setdefault("warnings", []).append(warning)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(report, indent=2))
        return 1
    finally:
        with contextlib.suppress(Exception):
            engine._unload_sync("synth done")
        if writer is not None and not writer_closed:
            with contextlib.suppress(OSError, wave.Error, struct.error):
                writer.close(discard="error" in report)
    print(json.dumps(report, indent=2))
    return 0 if report.get("model_speed_effective") and report.get("audio_s", 0) > 0 else 1


def run_voices(args: argparse.Namespace) -> int:
    cfg, config_file = _cli_config(args)
    paths = resolve_paths(cfg, config_file, None)
    names = VoiceBank(paths.voices_file).names()
    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(LANG_BY_PREFIX.get(name[0], "?"), []).append(name)
    for lang, members in sorted(groups.items()):
        print(f"{lang:<6} {' '.join(members)}")
    print(f"# {len(names)} voices; blend with e.g. --voice 'af_heart:0.4,af_bella:0.6'")
    return 0


def run_normalize(args: argparse.Namespace) -> int:
    cfg, _ = _cli_config(args)
    text = (args.text if args.text is not None else getattr(args, "text_flag", None))
    if text is None:
        text = sys.stdin.buffer.read().decode("utf-8", "replace")
    lang = args.lang or VoiceBank.lang_for(cfg.voice.spec, cfg.voice.lang)
    paragraphs = normalize_text(text, cfg.text, lang)
    segments = segment_text(paragraphs, cfg.text)
    print(f"# {len(paragraphs)} paragraphs, {len(segments)} segments, {len(flatten_paragraphs(paragraphs))} chars")
    for i, seg in enumerate(segments, 1):
        print(f"[{i:03d}] w={wlen(seg.text):<4} pause={seg.pause_ms:<4} {seg.text}")
    return 0


def run_config(args: argparse.Namespace) -> int:
    if args.write:
        target = Path(args.write)
        if target.exists() and not args.force:
            print(f"refusing to overwrite {target} (use --force)", file=sys.stderr)
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        print(f"wrote {target}")
        return 0
    cfg, config_file = _cli_config(args)
    paths = resolve_paths(cfg, config_file, args.socket)
    print(json.dumps({"config_file": str(config_file), "paths": {f.name: str(getattr(paths, f.name)) for f in fields(paths)},
                      "config": dataclasses.asdict(cfg)}, indent=2, default=str))
    return 0


# =============================================================================
#  Daemon & worker entry points
# =============================================================================
def run_daemon(args: argparse.Namespace) -> int:
    config_file = Path(args.config) if args.config else default_config_path()
    try:
        cfg = load_config(config_file)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 78
    setup_logging(cfg.logging, args.log_level)
    log.info("Dusky Kokoro %s starting; config=%s (%s)", VERSION, config_file, "loaded" if config_file.is_file() else "defaults")
    _apply_engine_env(cfg)
    for key, value in cfg.engine.env.items():
        log.info("engine.env %s=%s", key, value)
    paths = resolve_paths(cfg, config_file, args.socket)
    paths.socket.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(paths.socket.parent, 0o700)
    lock = InstanceLock(paths.lock)
    if not lock.acquire():
        log.error("another daemon instance holds %s - exiting", paths.lock)
        return 3
    try:
        daemon = Daemon(cfg, paths)
        return asyncio.run(daemon.serve())
    except DuskyError as exc:
        log.critical("%s", exc)
        return 1
    finally:
        lock.release()


def run_synth_worker(args: argparse.Namespace) -> int:
    # stdout is a framed binary transport; all library logs belong on stderr.
    raw_initial = sys.stdin.buffer.readline()
    if not raw_initial:
        return 1
    initial = json.loads(raw_initial)
    cfg = Config(**{name: _build_section(_SECTIONS[name], data, name)
                    for name, data in initial["config"].items()})
    _validate(cfg)
    setup_logging(cfg.logging, os.environ.get("DUSKY_LOG_LEVEL"))
    cfg = dataclasses.replace(cfg, engine=dataclasses.replace(
        cfg.engine, models_dir=initial["models_dir"], voices_file=initial["voices_file"]))
    _apply_engine_env(cfg)
    config_file = Path(args.config) if args.config else default_config_path()
    paths = resolve_paths(cfg, config_file)
    voices = VoiceBank(paths.voices_file)
    engine = Engine(cfg, paths, voices, is_worker=True)

    abort_lock = threading.Lock()
    abort_event = threading.Event()
    current_req_id = 0
    aborted_req_id = 0

    def _watch_abort(fd: int) -> None:
        nonlocal aborted_req_id
        buf = bytearray()
        try:
            while chunk := os.read(fd, 64):
                buf.extend(chunk)
                while len(buf) >= 4:
                    (target_id,) = struct.unpack("<I", buf[:4])
                    del buf[:4]
                    with abort_lock:
                        if target_id > aborted_req_id:
                            aborted_req_id = target_id
                        if target_id == current_req_id:
                            abort_event.set()
                            ro = engine._run_options
                            if ro is not None:
                                ro.terminate = True
        except OSError:
            pass
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)

    if args.abort_fd is not None:
        threading.Thread(target=_watch_abort, args=(args.abort_fd,), name="abort-watcher", daemon=True).start()

    try:
        engine._load_sync()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), flush=True)
        return 1
    print(json.dumps({
        "ok": True,
        "providers": engine.active_providers,
        "kind": engine.active_kind,
        "precision": engine.model_precision,
        "model": str(engine.model_path),
        "degraded": engine.degraded,
        "warnings": engine.pop_warnings(),
        "load_s": engine.stats.last_load_s,
    }), flush=True)

    try:
        while line := sys.stdin.buffer.readline():
            req = json.loads(line)
            if req.get("cmd") == "quit":
                break
            req_id = int(req.get("req_id", 0))
            with abort_lock:
                current_req_id = req_id
                already_aborted = req_id > 0 and aborted_req_id >= req_id
                if already_aborted:
                    abort_event.set()
                    if engine._run_options is not None:
                        engine._run_options.terminate = True
                else:
                    abort_event.clear()
                    if engine._run_options is not None:
                        engine._run_options.terminate = False

            pcm = b""
            error: str | None = None
            aborted = False
            bit_depth = int(req.get("bit_depth", cfg.archive.bit_depth))
            max_phonemes = int(req.get("max_phonemes", cfg.text.max_phonemes))
            trim_silence = bool(req.get("trim_silence", cfg.text.trim_silence))
            try:
                if already_aborted:
                    raise _AbortedError()
                vec = voices.resolve(req["voice"])
                try:
                    pcm = engine._synth_sync(
                        req["text"], vec, req["speed"], req["lang"], req["pause_ms"],
                        bit_depth=bit_depth,
                        max_phonemes=max_phonemes,
                        trim_silence=trim_silence,
                        abort_event=abort_event,
                    )
                except _AbortedError:
                    raise
                except Exception as exc:
                    if abort_event.is_set():
                        raise _AbortedError() from None
                    if (
                        engine.active_kind == "cpu"
                        or cfg.engine.require_accelerator
                        or not (type(exc).__module__.startswith("onnxruntime") or isinstance(exc, InferenceError))
                    ):
                        raise
                    # Retry on CPU once for eligible accelerator inference failures.
                    engine.fallback_to_cpu(exc)
                    if abort_event.is_set():
                        raise _AbortedError() from None
                    vec = voices.resolve(req["voice"])
                    pcm = engine._synth_sync(
                        req["text"], vec, req["speed"], req["lang"], req["pause_ms"],
                        bit_depth=bit_depth,
                        max_phonemes=max_phonemes,
                        trim_silence=trim_silence,
                        abort_event=abort_event,
                    )
                if not pcm:
                    raise EngineError("no audio produced for segment")
            except _AbortedError:
                aborted = True
                pcm = b""
            except Exception as exc:
                log.exception("worker synthesis failed")
                error = f"{type(exc).__name__}: {exc}"
            finally:
                with abort_lock:
                    current_req_id = 0
                    abort_event.clear()
                    if engine._run_options is not None:
                        engine._run_options.terminate = False

            state = json.dumps({
                "req_id": req_id,
                "pcm_bytes": len(pcm),
                "aborted": aborted,
                "error": error,
                "kind": engine.active_kind,
                "providers": engine.active_providers,
                "degraded": engine.degraded,
                "precision": engine.model_precision,
                "model": str(engine.model_path) if engine.model_path else None,
            }).encode()
            sys.stdout.buffer.write(struct.pack("<I", len(state)))
            sys.stdout.buffer.write(state)
            if pcm:
                sys.stdout.buffer.write(pcm)
            sys.stdout.buffer.flush()
    finally:
        engine._unload_sync("worker shutdown")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dusky_main.py", description=f"Dusky Kokoro TTS {VERSION}")
    parser.add_argument("--config", help="config.toml path (default: XDG_CONFIG_HOME/dusky-kokoro/config.toml)")
    parser.add_argument("--socket", help="control socket path (default: XDG_RUNTIME_DIR/dusky-kokoro/control.sock)")
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("daemon", help="run the daemon in the foreground (systemd / trigger.sh supervise it)")
    d.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"))

    def client_parser(name: str, help_: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--format", choices=("json", "kv", "pretty"), default="json")
        p.add_argument("--timeout", type=float, default=90.0, help="seconds to wait for the reply (covers cold start)")
        return p

    s = client_parser("speak", "send text or a book/document to be spoken aloud")
    s.add_argument("text", nargs="?", help="text to speak, or path to a file (txt, md, pdf, epub)")
    s.add_argument("--text", dest="text_flag", help="text to speak (alternative to positional text)")
    s.add_argument("--stdin", action="store_true", help="read text from standard input")
    s.add_argument("--file", help="path to a text, markdown, epub, or pdf file to speak")
    s.add_argument("--mode", choices=("interrupt", "enqueue"), help="interrupt playback or queue behind existing jobs")
    s.add_argument("--voice", help="voice spec, e.g. af_heart or af_heart:0.4,af_bella:0.6")
    s.add_argument("--speed", type=float, help="Kokoro generation speed (0.5 to 2.0)")
    s.add_argument("--lang", help="espeak-ng language code (default: derived from the voice prefix)")
    s.add_argument("--wait", action="store_true", help="stream started/finished/cancelled/error events")
    s.add_argument("--client", default="cli")
    for name, help_ in (("stop", "stop playback and flush the queue"), ("pause", "toggle pause"),
                        ("status", "daemon status"), ("ping", "liveness probe"), ("unload", "unload the model now"),
                        ("reload", "re-read config.toml"), ("shutdown", "stop the daemon")):
        client_parser(name, help_)
    ll = client_parser("loglevel", "change the daemon log level at runtime")
    ll.add_argument("level", choices=("DEBUG", "INFO", "WARNING", "ERROR"))

    doc = sub.add_parser("doctor", help="environment / provider / model diagnostics")
    doc.add_argument("--synth", action="store_true", help="also run an offline synthesis benchmark")
    for p in (doc, sub.add_parser("synth", help="offline synthesis benchmark (no mpv, writes a WAV)")):
        p.add_argument("--text")
        p.add_argument("--voice")
        p.add_argument("--lang")
        p.add_argument("--speed", type=float)
        p.add_argument("--out")
    sub.add_parser("voices", help="list voice styles in voices-v1.0.bin")
    n = sub.add_parser("normalize", help="show how text is normalised and segmented")
    n.add_argument("text", nargs="?")
    n.add_argument("--text", dest="text_flag", help="text to normalise")
    n.add_argument("--lang")
    c = sub.add_parser("config", help="print effective configuration or write the default template")
    c.add_argument("--write", metavar="PATH")
    c.add_argument("--force", action="store_true")
    w = sub.add_parser("synth-worker", help="internal synthesis worker subprocess")
    w.add_argument("--config", help="path to config.toml")
    w.add_argument("--abort-fd", type=int, default=None, help="inherited pipe fd for in-flight abort tokens")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        match args.command:
            case "daemon":
                return run_daemon(args)
            case "synth-worker":
                return run_synth_worker(args)
            case "doctor":
                return run_doctor(args)
            case "synth":
                return run_synth(args)
            case "voices":
                return run_voices(args)
            case "normalize":
                return run_normalize(args)
            case "config":
                return run_config(args)
            case _:
                return run_client(args)
    except DuskyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
