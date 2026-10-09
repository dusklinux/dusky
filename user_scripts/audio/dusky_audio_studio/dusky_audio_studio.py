#!/usr/bin/env python3
"""
Dusky Audio Studio & Voice DSP — Bleeding-Edge Audio Engine & GTK3 Control Studio
Target Specification: Parstix Linux (Kernel 7.3+, Python 3.15+)
Pure bleeding-edge Linux audio architecture with zero legacy shims.

Features:
- RNNoise Recurrent Neural Network Noise Suppression & Hysteresis Gate
- Granular Doppler Pitch Shifter (-24 to +24 semitones)
- 16-Band Formant Filterbank Robot Vocoder with Voice Pitch Tracking & Matrix Timbre Morphing
- Chromatic Autotune & Monotone Pitch Snapping (DECtalk / T-Pain)
- Lo-Fi Vintage Bitcrusher (Bit depth & Sample-and-Hold downsampling)
- Vocal Bandpass Shaping (Telephone, Helmet Resonance, Tinny Radio)
- Rhythmic Stutter Gate Amplitude Chopper (Cylon / Battlestar Galactica)
- Tape Delay & Echo Tank (0 to 1000 ms)
- 4-Comb + 2-Allpass Schroeder Reverb
- 9-Band Studio Parametric Equalizer with Uniform Post-Gain Translation
- Real-Time Hardware Microphone Auto-Discovery (Anti-Loopback / Anti-Deadlock)
- Live Binary Frame Telemetry & GTK3 Meters (VAD %, Denoiser Signal Change, Input/Output Level)
- Unified UNIX Domain Socket IPC Server (multi-client)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
lazy from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Final
import json
import fcntl
import os
lazy import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import weakref

# --- Constants & Paths ---
APP_ID: Final[str] = "org.dusky.audio-studio"
HOME_DIR: Final[Path] = Path.home()
STATE_DIR: Final[Path] = Path(os.environ.get("XDG_CONFIG_HOME") or HOME_DIR / ".config") / "dusky" / "settings" / "dusky_studio"
CACHE_DIR: Final[Path] = Path(os.environ.get("XDG_CACHE_HOME") or HOME_DIR / ".cache") / "dusky_studio"
CONFIG_FILE: Final[Path] = STATE_DIR / "config.json"
SOCK_PATH: Final[Path] = STATE_DIR / "dusky_audio.sock"
PID_FILE: Final[Path] = STATE_DIR / "daemon.pid"
GUI_PID_FILE: Final[Path] = STATE_DIR / "gui.pid"

# Frame Protocol v4 specification
FRAME_SIZE: Final[int] = 36
MAGIC: Final[int] = 0x47484146  # "GHAF"
PROTOCOL_VERSION: Final[int] = 4
HEADER_STRUCT: Final[struct.Struct] = struct.Struct("<IIIIfffff")  # 36 bytes
NO_HARDWARE_TARGET: Final[str] = "dusky-no-hardware-device"
APP_NODE_NAMES: Final[frozenset[str]] = frozenset({
    "ghelper-audio", "ghelper-audio-sink", "ghelper-audio-capture",
    "ghelper-audio-sink-out", "ghelper-audio-monitor",
})
EQ_BANDS: Final[tuple[tuple[str, int, int, int], ...]] = (
    ("80 Hz (Sub Bass)", 0, 80, 707),
    ("120 Hz (Warmth Lowshelf)", 1, 120, 707),
    ("250 Hz (Low Mid Clean)", 0, 250, 1000),
    ("400 Hz (Boxiness Mud Cut)", 0, 400, 1000),
    ("1.5 kHz (Vocal Body)", 0, 1500, 1000),
    ("3.5 kHz (Presence & Clarity)", 0, 3500, 700),
    ("6.0 kHz (Vocal Detail)", 0, 6000, 1000),
    ("9.0 kHz (Air & Sheen Highshelf)", 2, 9000, 700),
    ("12.0 kHz (Brilliance)", 0, 12000, 1000),
)

# Command locale for predictable command output
COMMAND_ENV: Final[dict[str, str]] = os.environ.copy()
COMMAND_ENV["LC_ALL"] = "C.UTF-8"
COMMAND_ENV["LANG"] = "C.UTF-8"

# Dynamic Material You / Matugen GTK3 CSS Theme
DUSKY_CSS: Final[str] = """
window.panel-window {
    background-color: @theme_bg_color;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.5);
}

.header-title {
    font-size: 16px;
    font-weight: 800;
    letter-spacing: -0.5px;
    color: @theme_fg_color;
}

.header-subtitle-active {
    font-size: 12px;
    font-weight: 600;
    color: @theme_selected_bg_color;
}

.header-subtitle-inactive {
    font-size: 12px;
    font-weight: 500;
    color: alpha(@theme_fg_color, 0.5);
}

.section-label {
    font-size: 12px;
    font-weight: 700;
    color: alpha(@theme_fg_color, 0.9);
}

.value-label {
    font-size: 11px;
    font-weight: 600;
    color: @theme_selected_bg_color;
}

.meter-label {
    font-size: 10px;
    font-weight: 600;
    color: alpha(@theme_fg_color, 0.6);
}

.meter-val {
    font-size: 10px;
    font-weight: 700;
    color: @theme_selected_bg_color;
}

.device-combo {
    background-color: alpha(@theme_base_color, 0.6);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 8px;
    padding: 2px 6px;
    color: @theme_fg_color;
}

.preset-btn {
    border-radius: 8px;
    padding: 4px 10px;
    font-weight: 600;
    font-size: 11px;
    background-color: alpha(@theme_fg_color, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.05);
    color: @theme_fg_color;
    transition: all 150ms ease;
}

.preset-btn:hover {
    background-color: alpha(@theme_selected_bg_color, 0.25);
    color: @theme_selected_bg_color;
}

.preset-btn.active-preset {
    background-color: @theme_selected_bg_color;
    color: @theme_selected_fg_color;
    border-color: @theme_selected_bg_color;
    font-weight: 700;
}

.reset-btn {
    border-radius: 8px;
    padding: 3px 10px;
    font-weight: 600;
    font-size: 11px;
    background-color: alpha(@theme_fg_color, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.06);
    color: alpha(@theme_fg_color, 0.8);
    transition: all 150ms ease;
}

.reset-btn:hover {
    background-color: alpha(@theme_selected_bg_color, 0.22);
    color: @theme_selected_bg_color;
    border-color: alpha(@theme_selected_bg_color, 0.45);
}

.segmented-group {
    background-color: alpha(@theme_fg_color, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 10px;
    padding: 3px;
}

.segmented-btn {
    border-radius: 7px;
    padding: 6px 16px;
    font-weight: 700;
    font-size: 11px;
    background-color: transparent;
    border: 1px solid transparent;
    color: alpha(@theme_fg_color, 0.7);
    transition: all 150ms ease;
}

.segmented-btn:hover {
    background-color: alpha(@theme_selected_bg_color, 0.15);
    color: @theme_selected_bg_color;
}

.segmented-btn.active-preset {
    background-color: @theme_selected_bg_color;
    color: @theme_selected_fg_color;
    border-color: @theme_selected_bg_color;
    font-weight: 800;
}

.preset-chip {
    border-radius: 6px;
    padding: 3px 8px;
    font-weight: 600;
    font-size: 10px;
    background-color: alpha(@theme_fg_color, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.04);
    color: alpha(@theme_fg_color, 0.85);
    transition: all 120ms ease;
}

.preset-chip:hover {
    background-color: alpha(@theme_selected_bg_color, 0.2);
    color: @theme_selected_bg_color;
}

.preset-chip.active-preset {
    background-color: @theme_selected_bg_color;
    color: @theme_selected_fg_color;
    border-color: @theme_selected_bg_color;
    font-weight: 700;
}

notebook header {
    background-color: transparent;
    border-bottom: 1px solid alpha(@theme_fg_color, 0.08);
}

notebook tab {
    padding: 8px 14px;
    font-weight: 700;
    font-size: 12px;
    border-bottom: 2px solid transparent;
    transition: all 150ms ease;
}

notebook tab:checked {
    border-bottom: 2px solid @theme_selected_bg_color;
    color: @theme_selected_bg_color;
    background-color: alpha(@theme_selected_bg_color, 0.06);
}

notebook tab:hover:not(:checked) {
    background-color: alpha(@theme_fg_color, 0.04);
}

scale trough {
    min-height: 5px;
    border-radius: 3px;
    background-color: alpha(@theme_fg_color, 0.12);
}

scale highlight {
    border-radius: 3px;
    background-color: @theme_selected_bg_color;
}

scale slider {
    min-width: 14px;
    min-height: 14px;
    border-radius: 7px;
    background-color: @theme_selected_bg_color;
}

progressbar trough {
    min-height: 8px;
    border-radius: 4px;
    background-color: alpha(@theme_fg_color, 0.10);
    border: none;
}

progressbar progress {
    border-radius: 4px;
    background-color: @theme_selected_bg_color;
}

switch image,
switch image.on,
switch image.off,
switch image:first-child,
switch image:last-child {
    -gtk-icon-source: none;
    -gtk-icon-transform: scale(0);
    opacity: 0;
    min-width: 0px;
    min-height: 0px;
    margin: 0px;
    padding: 0px;
    color: transparent;
}

switch,
switch:checked,
switch:not(:checked),
switch:hover,
switch:hover:not(:checked),
switch:checked:hover,
switch:checked:hover:active,
switch:checked:active,
switch:checked:disabled,
switch:disabled,
switch:focus,
switch.compact-switch,
switch.compact-switch:checked,
switch.compact-switch:not(:checked),
switch.compact-switch:hover,
switch.compact-switch:active,
switch.compact-switch:disabled {
    color: transparent;
    font-size: 0px;
    text-shadow: none;
    -gtk-icon-source: none;
    -gtk-icon-shadow: none;
    background-image: none;
    box-shadow: none;
}

switch label {
    color: transparent;
    font-size: 0px;
    text-shadow: none;
    -gtk-icon-source: none;
    -gtk-icon-shadow: none;
    background-image: none;
    opacity: 0;
    min-width: 0px;
    min-height: 0px;
    margin: 0px;
    padding: 0px;
}

switch.compact-switch {
    min-width: 44px;
    min-height: 24px;
    border-radius: 12px;
    background-color: alpha(@theme_fg_color, 0.18);
    border: none;
    box-shadow: none;
    color: transparent;
}

switch.compact-switch:checked {
    background-color: @theme_selected_bg_color;
    border: none;
    box-shadow: none;
    color: transparent;
}

switch.compact-switch slider {
    min-width: 18px;
    min-height: 18px;
    border-radius: 9px;
    border: none;
    box-shadow: none;
    outline: none;
    margin: 3px;
    background-color: @theme_bg_color;
}

switch.compact-switch:checked slider {
    background-color: @theme_base_color;
}

.footer-info {
    font-size: 11px;
    font-weight: 500;
    color: alpha(@theme_fg_color, 0.7);
}

.warning-banner {
    background-color: alpha(@theme_selected_bg_color, 0.12);
    border: 1px solid alpha(@theme_selected_bg_color, 0.35);
    border-radius: 8px;
    padding: 8px 12px;
}

.warning-text {
    font-size: 12px;
    font-weight: 600;
    color: @theme_selected_bg_color;
}

.telemetry-card {
    background-color: alpha(@theme_base_color, 0.4);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 8px;
    padding: 8px 10px;
}
"""


@dataclass(slots=True, kw_only=True, weakref_slot=True)
class AudioConfig:
    # Master (Disabled by default, opt-in only)
    enabled: bool = False
    source: str = "default"
    sink: str = "default"
    volume: int = 100  # 0..200%
    monitor: bool = False

    # Saved Physical Hardware Defaults for Seamless Restore on Disable/Reboot
    pre_source: str = ""
    pre_sink: str = ""
    pre_configured_source: str = ""
    pre_configured_sink: str = ""

    # Noise Suppression - Input / Microphone (Enabled by default on fresh install)
    rnnoise_on: bool = True
    aggressiveness: int = 100  # 0..100%

    # Noise Suppression - Output / Speaker & Headphone (Two-Way, OFF by default)
    out_rnnoise_on: bool = False
    out_aggressiveness: int = 70  # 0..100%

    # Vocoder & Voice Character Stack
    vocoder_on: bool = False
    vocoder_mix: int = 0  # 0..100%
    vocoder_carrier_hz: int = 110  # 50..880 Hz
    vocoder_attack_ms: int = 5  # 1..100 ms
    vocoder_release_ms: int = 30  # 5..500 ms
    vocoder_detune: int = 20  # 0..200 per-mille
    vocoder_follow: bool = True
    vocoder_pitch_shift: int = 0  # -24..+24 semitones
    vocoder_matrix: int = 0  # 0..100%

    # Pitch & Modulation
    pitch_shift: int = 0  # -2400..+2400 centisemitones (-24..+24 st)
    autotune_on: bool = False
    autotune_target_hz: int = 0  # 0=chromatic snap, >0=monotone target
    bitcrush_bits: int = 0  # 0=bypass, 1..15
    bitcrush_downsample: int = 1  # 1..64
    bandpass_hpf_hz: int = 0  # 0..2000 Hz
    bandpass_lpf_hz: int = 0  # 0..20000 Hz
    stutter_hz: int = 0  # 0..40 Hz

    # Delay / Echo
    delay_on: bool = False
    delay_ms: int = 250  # 10..1000 ms
    delay_feedback: int = 35  # 0..95%
    delay_mix: int = 30  # 0..100%

    # Reverb
    reverb_on: bool = False
    reverb_room: int = 70  # 0..100%
    reverb_damp: int = 50  # 0..100%
    reverb_width: int = 80  # 0..100%
    reverb_mix: int = 35  # 0..100%

    # Microphone 9-Band EQ gains (-1200..+1200 centi-dB -> -12dB..+12dB)
    eq_on: bool = False
    eq_post_gain: int = 0  # -3600..+3600 centi-dB (±36 dB line translation)
    eq_gains: list[int] = field(
        default_factory=lambda: [0, 0, 0, 0, 0, 0, 0, 0, 0]
    )

    # Playback / Output 9-Band Stereo EQ gains
    out_eq_on: bool = False
    out_eq_post_gain: int = 0  # -3600..+3600 centi-dB
    out_eq_gains: list[int] = field(
        default_factory=lambda: [0, 0, 0, 0, 0, 0, 0, 0, 0]
    )

    # Playback / Output Vocoder & Carrier
    out_vocoder_on: bool = False
    out_vocoder_mix: int = 70  # 0..100%
    out_vocoder_carrier_hz: int = 110  # 50..880 Hz
    out_vocoder_attack_ms: int = 5  # 1..100 ms
    out_vocoder_release_ms: int = 30  # 5..500 ms
    out_vocoder_detune: int = 20  # 0..200 per-mille
    out_vocoder_follow: bool = True
    out_vocoder_pitch_shift: int = 0  # -24..+24 semitones
    out_vocoder_matrix: int = 0  # 0..100%

    # Playback / Output Pitch & Modulation
    out_pitch_shift: int = 0  # -2400..+2400 centisemitones
    out_autotune_on: bool = False
    out_autotune_target_hz: int = 0
    out_bitcrush_bits: int = 0
    out_bitcrush_downsample: int = 1
    out_bandpass_hpf_hz: int = 0
    out_bandpass_lpf_hz: int = 0
    out_stutter_hz: int = 0

    # Playback / Output Delay & Reverb
    out_delay_on: bool = False
    out_delay_ms: int = 250
    out_delay_feedback: int = 35
    out_delay_mix: int = 30
    out_reverb_on: bool = False
    out_reverb_room: int = 70
    out_reverb_damp: int = 50
    out_reverb_width: int = 80
    out_reverb_mix: int = 35


@dataclass(slots=True)
class AudioTelemetry:
    seq: int = 0
    flags: int = 0
    vad_prob: float = 0.0
    rms_in_db: float = -80.0
    rms_out_db: float = -80.0
    processing_delta_dbfs: float = -80.0
    tracked_pitch_hz: float = 0.0


CONFIG_BASELINES: dict[int, tuple[weakref.ReferenceType[AudioConfig], dict[str, Any]]] = {}


def remember_config_baseline(cfg: AudioConfig) -> None:
    key = id(cfg)
    CONFIG_BASELINES[key] = (
        weakref.ref(cfg, lambda _: CONFIG_BASELINES.pop(key, None)), asdict(cfg))


def validate_config(cfg: AudioConfig) -> None:
    defaults = AudioConfig()
    for entry in fields(cfg):
        name = entry.name
        value = getattr(cfg, name)
        expected = getattr(defaults, name)
        if isinstance(expected, bool):
            valid = type(value) is bool
        elif isinstance(expected, int):
            valid = type(value) is int
        elif isinstance(expected, str):
            valid = (isinstance(value, str) and len(value.encode("utf-8")) <= 255
                     and not any(c in value for c in "\n\r\0"))
        else:
            valid = (isinstance(value, list) and len(value) == 9
                     and all(type(x) is int and -1200 <= x <= 1200 for x in value))
        if not valid:
            raise ValueError(f"invalid configuration field: {name}")
        if isinstance(expected, int) and not isinstance(expected, bool):
            low, high = 0, 100
            stem = name.removeprefix("out_")
            if stem == "volume": low, high = 0, 200
            elif stem == "vocoder_carrier_hz": low, high = 50, 880
            elif stem == "vocoder_attack_ms": low, high = 1, 200
            elif stem == "vocoder_release_ms": low, high = 5, 500
            elif stem == "vocoder_detune": low, high = 0, 200
            elif stem == "vocoder_pitch_shift": low, high = -24, 24
            elif stem == "pitch_shift": low, high = -2400, 2400
            elif stem == "autotune_target_hz": low, high = 0, 1000
            elif stem == "bitcrush_bits": low, high = 0, 15
            elif stem == "bitcrush_downsample": low, high = 1, 64
            elif stem == "bandpass_hpf_hz": low, high = 0, 2000
            elif stem == "bandpass_lpf_hz": low, high = 0, 20000
            elif stem == "stutter_hz": low, high = 0, 40
            elif stem == "delay_ms": low, high = 0, 1000
            elif stem == "delay_feedback": low, high = 0, 95
            elif stem == "eq_post_gain": low, high = -3600, 3600
            if not low <= value <= high:
                raise ValueError(f"out-of-range configuration field: {name}")


# Microphone / Input Equalizer Presets
INPUT_EQ_PRESETS: Final[dict[str, dict[str, Any]]] = {
    "Flat (0 dB)": {
        "post_gain": 0,
        "gains": [0, 0, 0, 0, 0, 0, 0, 0, 0],
    },
    "Broadcast Warmth": {
        "post_gain": 0,
        "gains": [0, 300, 0, -200, 0, 300, 0, 200, 0],
    },
    "Vocal Presence": {
        "post_gain": 0,
        "gains": [-300, 0, 0, -100, 200, 400, 300, 200, 100],
    },
    "Crisp & Clean": {
        "post_gain": 0,
        "gains": [-600, -200, -100, -200, 100, 300, 400, 500, 300],
    },
}


# Output / Playback Equalizer Presets
OUTPUT_EQ_PRESETS: Final[dict[str, dict[str, Any]]] = {
    "Flat (0 dB)": {
        "post_gain": 0,
        "gains": [0, 0, 0, 0, 0, 0, 0, 0, 0],
    },
    "Bass Boost": {
        "post_gain": 0,
        "gains": [600, 500, 300, 100, 0, 0, 0, 0, 0],
    },
    "V-Shape (Loudness)": {
        "post_gain": 0,
        "gains": [500, 400, 200, -100, -200, 100, 300, 400, 500],
    },
    "Vocal Clarity": {
        "post_gain": 0,
        "gains": [-300, -100, 0, 100, 400, 500, 300, 100, 0],
    },
    "Treble Boost": {
        "post_gain": 0,
        "gains": [-200, -100, 0, 0, 100, 300, 500, 600, 600],
    },
    "Gaming (Footsteps)": {
        "post_gain": 0,
        "gains": [-400, -200, 100, 200, 300, 600, 500, 200, -100],
    },
}


# Comprehensive Voice Presets Palette (Aligned with C# Ground Truth)
PRESETS: Final[dict[str, dict[str, Any]]] = {
    "Natural Clean": {
        "vocoder_on": False,
        "vocoder_mix": 0,
        "pitch_shift": 0,
        "autotune_on": False,
        "autotune_target_hz": 0,
        "bitcrush_bits": 0,
        "bitcrush_downsample": 1,
        "bandpass_hpf_hz": 0,
        "bandpass_lpf_hz": 0,
        "stutter_hz": 0,
        "vocoder_matrix": 0,
    },
    "Daft Punk": {
        "vocoder_on": True,
        "vocoder_mix": 90,
        "vocoder_carrier_hz": 110,
        "vocoder_detune": 50,
        "vocoder_attack_ms": 2,
        "vocoder_release_ms": 12,
        "vocoder_follow": True,
        "vocoder_pitch_shift": 0,
        "pitch_shift": 0,
        "autotune_on": False,
        "autotune_target_hz": 0,
        "bitcrush_bits": 0,
        "bitcrush_downsample": 1,
        "bandpass_hpf_hz": 0,
        "bandpass_lpf_hz": 0,
        "stutter_hz": 0,
        "vocoder_matrix": 15,
    },
    "Darth Vader": {
        "vocoder_on": True,       # Keeps C voice-effects pipeline active
        "vocoder_mix": 0,          # 0% vocoder synth carrier = passes dry pitch-shifted voice
        "vocoder_carrier_hz": 110,
        "vocoder_detune": 0,
        "vocoder_attack_ms": 5,
        "vocoder_release_ms": 30,
        "vocoder_follow": False,
        "vocoder_pitch_shift": 0,
        "pitch_shift": -500,       # -5 semitones down
        "autotune_on": False,
        "autotune_target_hz": 0,
        "bitcrush_bits": 0,
        "bitcrush_downsample": 1,
        "bandpass_hpf_hz": 80,     # Low-cut handling rumble
        "bandpass_lpf_hz": 2500,   # Helmet resonant acoustic muffle
        "stutter_hz": 0,
        "vocoder_matrix": 0,
    },
    "Chipmunk": {
        "vocoder_on": True,
        "vocoder_mix": 0,
        "vocoder_carrier_hz": 110,
        "vocoder_detune": 0,
        "vocoder_attack_ms": 2,
        "vocoder_release_ms": 15,
        "vocoder_follow": False,
        "vocoder_pitch_shift": 0,
        "pitch_shift": 1200,       # +12 semitones (1 full octave up)
        "autotune_on": False,
        "autotune_target_hz": 0,
        "bitcrush_bits": 0,
        "bitcrush_downsample": 1,
        "bandpass_hpf_hz": 150,
        "bandpass_lpf_hz": 0,
        "stutter_hz": 0,
        "vocoder_matrix": 0,
    },
    "Cylon Robot": {
        "vocoder_on": True,
        "vocoder_mix": 90,
        "vocoder_carrier_hz": 90,
        "vocoder_detune": 160,
        "vocoder_attack_ms": 10,
        "vocoder_release_ms": 70,
        "vocoder_follow": False,
        "vocoder_pitch_shift": 0,
        "pitch_shift": 0,
        "autotune_on": False,
        "autotune_target_hz": 0,
        "bitcrush_bits": 0,
        "bitcrush_downsample": 1,
        "bandpass_hpf_hz": 0,
        "bandpass_lpf_hz": 0,
        "stutter_hz": 6,           # 6 Hz rhythmic "By Your Command" chopping
        "vocoder_matrix": 45,
    },
    "Kraftwerk": {
        "vocoder_on": True,
        "vocoder_mix": 95,
        "vocoder_carrier_hz": 140,
        "vocoder_detune": 10,
        "vocoder_attack_ms": 3,
        "vocoder_release_ms": 15,
        "vocoder_follow": False,
        "vocoder_pitch_shift": 0,
        "pitch_shift": 0,
        "autotune_on": False,
        "autotune_target_hz": 0,
        "bitcrush_bits": 0,
        "bitcrush_downsample": 1,
        "bandpass_hpf_hz": 0,
        "bandpass_lpf_hz": 0,
        "stutter_hz": 0,
        "vocoder_matrix": 0,       # Clean pure-analog saw+square
    },
    "Matrix Agent": {
        "vocoder_on": True,
        "vocoder_mix": 90,
        "vocoder_carrier_hz": 70,
        "vocoder_detune": 90,
        "vocoder_attack_ms": 10,
        "vocoder_release_ms": 55,
        "vocoder_follow": False,
        "vocoder_pitch_shift": 0,
        "pitch_shift": -200,       # -2 semitones
        "autotune_on": False,
        "autotune_target_hz": 0,
        "bitcrush_bits": 0,
        "bitcrush_downsample": 1,
        "bandpass_hpf_hz": 300,
        "bandpass_lpf_hz": 3400,
        "stutter_hz": 0,
        "vocoder_matrix": 100,     # Full Sentinel 35 Hz ring mod + drive
    },
    "Robot Phone": {
        "vocoder_on": True,
        "vocoder_mix": 0,
        "vocoder_carrier_hz": 110,
        "vocoder_detune": 0,
        "vocoder_attack_ms": 5,
        "vocoder_release_ms": 25,
        "vocoder_follow": False,
        "vocoder_pitch_shift": 0,
        "pitch_shift": 0,
        "autotune_on": False,
        "autotune_target_hz": 0,
        "bitcrush_bits": 8,        # 8-bit staircase quantisation
        "bitcrush_downsample": 2,  # 2x sample-and-hold
        "bandpass_hpf_hz": 300,    # 300-3400 Hz standard telephone band
        "bandpass_lpf_hz": 3400,
        "stutter_hz": 0,
        "vocoder_matrix": 0,
    },
    "Sci-Fi Alien": {
        "vocoder_on": True,
        "vocoder_mix": 50,
        "vocoder_carrier_hz": 110,
        "vocoder_detune": 130,
        "vocoder_attack_ms": 2,
        "vocoder_release_ms": 10,
        "vocoder_follow": False,
        "vocoder_pitch_shift": 0,
        "pitch_shift": 700,        # +7 semitones (perfect fifth)
        "autotune_on": False,
        "autotune_target_hz": 0,
        "bitcrush_bits": 0,
        "bitcrush_downsample": 1,
        "bandpass_hpf_hz": 700,
        "bandpass_lpf_hz": 3200,
        "stutter_hz": 0,
        "vocoder_matrix": 70,
    },
    "T-Pain Autotune": {
        "vocoder_on": True,
        "vocoder_mix": 0,
        "vocoder_carrier_hz": 110,
        "vocoder_detune": 0,
        "vocoder_attack_ms": 5,
        "vocoder_release_ms": 30,
        "vocoder_follow": False,
        "vocoder_pitch_shift": 0,
        "pitch_shift": 0,
        "autotune_on": True,       # Chromatic snap
        "autotune_target_hz": 0,   # 0 = chromatic equal temperament
        "bitcrush_bits": 0,
        "bitcrush_downsample": 1,
        "bandpass_hpf_hz": 0,
        "bandpass_lpf_hz": 0,
        "stutter_hz": 0,
        "vocoder_matrix": 0,
    },
    "Stephen Hawking": {
        "vocoder_on": True,
        "vocoder_mix": 0,
        "vocoder_carrier_hz": 110,
        "vocoder_detune": 0,
        "vocoder_attack_ms": 5,
        "vocoder_release_ms": 30,
        "vocoder_follow": False,
        "vocoder_pitch_shift": 0,
        "pitch_shift": 0,
        "autotune_on": True,       # Monotone snap
        "autotune_target_hz": 120, # Fixed 120 Hz monotone synth
        "bitcrush_bits": 0,
        "bitcrush_downsample": 1,
        "bandpass_hpf_hz": 0,
        "bandpass_lpf_hz": 0,
        "stutter_hz": 0,
        "vocoder_matrix": 0,
    },
    "Megatron": {
        "vocoder_on": True,
        "vocoder_mix": 65,
        "vocoder_carrier_hz": 80,
        "vocoder_detune": 80,
        "vocoder_attack_ms": 4,
        "vocoder_release_ms": 40,
        "vocoder_follow": False,
        "vocoder_pitch_shift": 0,
        "pitch_shift": -700,        # -7 semitones (deep metallic robotic growl)
        "autotune_on": False,
        "autotune_target_hz": 0,
        "bitcrush_bits": 0,
        "bitcrush_downsample": 1,
        "bandpass_hpf_hz": 90,      # Low-cut handling sub-rumble
        "bandpass_lpf_hz": 4000,    # Resonant metallic presence
        "stutter_hz": 0,
        "vocoder_matrix": 65,       # 65% metallic ring modulation + tanh saturation
    },
}


VOICE_FIELDS: Final[tuple[str, ...]] = (
    "vocoder_on", "vocoder_mix", "vocoder_carrier_hz", "vocoder_attack_ms",
    "vocoder_release_ms", "vocoder_detune", "vocoder_follow",
    "vocoder_pitch_shift", "vocoder_matrix", "pitch_shift", "autotune_on",
    "autotune_target_hz", "bitcrush_bits", "bitcrush_downsample",
    "bandpass_hpf_hz", "bandpass_lpf_hz", "stutter_hz",
)


def apply_voice_preset(cfg: AudioConfig, name: str, target: str) -> None:
    preset = PRESETS[name]
    defaults = AudioConfig()
    prefix = "out_" if target == "out" else ""
    for name_part in VOICE_FIELDS:
        setattr(cfg, prefix + name_part,
                preset.get(name_part, getattr(defaults, prefix + name_part)))


def find_helper_binary() -> Path | None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "helper-build.lock", "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _find_helper_binary_unlocked()


def _find_helper_binary_unlocked() -> Path | None:
    script_dir = Path(__file__).resolve().parent
    helper_dir = script_dir / "audio-helper"
    local_bin = helper_dir / "dusky_audio_dsp"
    sources = [helper_dir / name for name in ("main.c", "protocol.h", "Makefile")]
    if (local_bin.is_file() and os.access(local_bin, os.X_OK)
            and all(src.is_file() for src in sources)
            and all(local_bin.stat().st_mtime_ns >= src.stat().st_mtime_ns for src in sources)
            and helper_protocol_matches(local_bin)):
        return local_bin

    if shutil.which("make") and all(src.is_file() for src in sources):
        try:
            subprocess.run(["make", "-B", "-C", str(helper_dir)], check=True,
                           capture_output=True, text=True, timeout=120)
            if local_bin.is_file() and os.access(local_bin, os.X_OK) and helper_protocol_matches(local_bin):
                return local_bin
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            print(f"[DuskyAudio] Helper build failed:\n{detail}", file=sys.stderr)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"[DuskyAudio] Helper build failed: {exc}", file=sys.stderr)
    return None


def helper_protocol_matches(path: Path) -> bool:
    try:
        result = subprocess.run([str(path), "--protocol-version"],
                                capture_output=True, text=True, timeout=2,
                                check=True)
        return result.stdout.strip() == str(PROTOCOL_VERSION)
    except (OSError, subprocess.SubprocessError):
        return False


def send_desktop_notification(
    title: str, message: str, urgency: str = "normal"
) -> None:
    try:
        subprocess.run(
            [
                "notify-send",
                "-a",
                "Dusky Audio Studio",
                "-u",
                urgency,
                "-i",
                "audio-volume-high",
                title,
                message,
            ],
            stderr=subprocess.DEVNULL,
            env=COMMAND_ENV,
        )
    except Exception:
        pass


def check_system_dependencies() -> list[str]:
    missing: list[str] = []
    missing_pkgs: list[str] = []

    if (not shutil.which("pw-cli") or not shutil.which("pw-dump")
            or not shutil.which("pw-metadata") or not shutil.which("wpctl")):
        missing_pkgs.extend(["pipewire", "wireplumber"])

    bin_path = find_helper_binary()
    if not bin_path:
        if not shutil.which("gcc") and not shutil.which("clang") and not shutil.which("cc"):
            missing_pkgs.append("gcc")
        if not shutil.which("make"):
            missing_pkgs.append("make")
        try:
            res = subprocess.run(["pkg-config", "--exists", "rnnoise"], capture_output=True)
            if res.returncode != 0:
                missing_pkgs.append("rnnoise")
        except Exception:
            pass

        unique_pkgs = list(dict.fromkeys(missing_pkgs))
        if unique_pkgs:
            missing.append(f"Install required packages: sudo pacman -S {' '.join(unique_pkgs)}")
        else:
            missing.append(f"Native Audio DSP engine failed to compile ({Path(__file__).resolve().parent / 'audio-helper'})")
    elif missing_pkgs:
        unique_pkgs = list(dict.fromkeys(missing_pkgs))
        missing.append(f"Install required packages: sudo pacman -S {' '.join(unique_pkgs)}")

    return missing


def load_config() -> AudioConfig:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = AudioConfig(**{k: v for k, v in data.items() if k in AudioConfig.__dataclass_fields__})
            validate_config(cfg)
            remember_config_baseline(cfg)
            return cfg
        except (OSError, ValueError, TypeError, AttributeError) as e:
            print(f"[DuskyAudio] Invalid configuration: {e}", file=sys.stderr)

    cfg = AudioConfig()
    remember_config_baseline(cfg)
    return cfg


def save_config(cfg: AudioConfig) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    validate_config(cfg)
    lock_path = CONFIG_FILE.with_suffix(".lock")
    try:
        with open(lock_path, "a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = asdict(cfg)
            baseline = CONFIG_BASELINES.get(id(cfg))
            if baseline and baseline[0]() is cfg and CONFIG_FILE.exists():
                try:
                    latest_data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                    latest = AudioConfig(**{k: v for k, v in latest_data.items()
                                            if k in AudioConfig.__dataclass_fields__})
                    validate_config(latest)
                    merged = asdict(latest)
                    for name, value in current.items():
                        if value != baseline[1][name]:
                            merged[name] = value
                    current = merged
                except (OSError, ValueError, TypeError, AttributeError):
                    pass
            tmp = CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(current, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                tmp.replace(CONFIG_FILE)
                for name, value in current.items():
                    setattr(cfg, name, value)
                remember_config_baseline(cfg)
            finally:
                tmp.unlink(missing_ok=True)
    except OSError as e:
        print(f"[DuskyAudio] Failed to save configuration: {e}", file=sys.stderr)


def pid_is_dusky_audio(pid: int) -> bool:
    """True only if /proc/<pid>/cmdline belongs to this application.

    PID files can outlive their process; once the kernel recycles the PID it
    may belong to any unrelated program. Every consumer of PID_FILE /
    GUI_PID_FILE checks the script name here before signalling, rejecting
    ordinary stale PID files that now refer to a different application."""
    try:
        if pid <= 0:
            return False
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return any(Path(os.fsdecode(arg)).name == Path(__file__).name
               for arg in raw.split(b"\0")[1:] if arg)


def get_daemon_pid() -> int | None:
    if PID_FILE.exists():
        try:
            with open(PID_FILE, "r", encoding="utf-8") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            if not pid_is_dusky_audio(pid):
                raise ValueError("pid recycled by another process")
            return pid
        except (OSError, ValueError):
            PID_FILE.unlink(missing_ok=True)
    return None


def pipewire_audio_snapshot() -> tuple[dict[str, dict[str, Any]], dict[str, str]] | None:
    """Return live hardware nodes and default metadata from one graph dump."""
    try:
        objects = json.loads(subprocess.check_output(
            ["pw-dump", "--no-colors"], text=True, stderr=subprocess.DEVNULL,
            env=COMMAND_ENV, timeout=2,
        ))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    nodes: dict[str, dict[str, Any]] = {}
    defaults: dict[str, str] = {}
    for obj in objects:
        props = obj.get("info", {}).get("props", {})
        name = props.get("node.name", "")
        if (props.get("media.class") in ("Audio/Source", "Audio/Sink")
                and isinstance(name, str) and name
                and props.get("device.id") is not None
                and str(props.get("node.virtual", "false")).lower() != "true"
                and name not in APP_NODE_NAMES
                and not name.endswith(".monitor")):
            nodes[name] = {**props, "id": obj.get("id")}
        if obj.get("props", {}).get("metadata.name") == "default":
            for item in obj.get("metadata", []):
                if item.get("subject") != 0:
                    continue
                value = item.get("value")
                candidate = value.get("name") if isinstance(value, dict) else value
                if isinstance(candidate, str):
                    defaults[item.get("key", "")] = candidate
    return nodes, defaults


def enumerate_devices() -> tuple[list[tuple[str, str]], list[tuple[str, str]]] | None:
    sources = [("default", "Default System Microphone (Auto)")]
    sinks = [("default", "Default System Output (Auto)")]
    snapshot = pipewire_audio_snapshot()
    if snapshot is None:
        return None
    nodes, _ = snapshot
    for name, props in sorted(nodes.items(), key=lambda item: item[1].get("node.description") or item[0]):
        entry = (name, props.get("node.description") or name)
        if props.get("media.class") == "Audio/Source":
            sources.append(entry)
        else:
            sinks.append(entry)
    return sources, sinks


def resolve_hardware_source(requested: str = "default", fallback_node: str = "") -> str:
    return resolve_hardware_node("Audio/Source", "source", requested, fallback_node)


def resolve_hardware_sink(requested: str = "default", fallback_node: str = "") -> str:
    return resolve_hardware_node("Audio/Sink", "sink", requested, fallback_node)


def resolve_hardware_node(media_class: str, direction: str, requested: str,
                          fallback_node: str = "",
                          snapshot: tuple[dict[str, dict[str, Any]], dict[str, str]] | None = None) -> str:
    """Resolve against live nodes. The active default precedes saved choices."""
    snapshot = snapshot if snapshot is not None else pipewire_audio_snapshot()
    if snapshot is None:
        return NO_HARDWARE_TARGET
    all_nodes, defaults = snapshot
    nodes = {name: props for name, props in all_nodes.items()
             if props.get("media.class") == media_class}

    if requested and requested != "default" and requested in nodes:
        return requested

    for candidate in (defaults.get(f"default.audio.{direction}"),
                      defaults.get(f"default.configured.audio.{direction}"),
                      fallback_node):
        if candidate in nodes:
            return candidate
    return next(iter(nodes), NO_HARDWARE_TARGET)


def save_previous_default_devices(cfg: AudioConfig) -> None:
    """Remember active hardware and configured preferences independently."""
    snapshot = pipewire_audio_snapshot()
    if snapshot is None:
        return
    _, defaults = snapshot
    source = resolve_hardware_node("Audio/Source", "source", "default", cfg.pre_source, snapshot)
    sink = resolve_hardware_node("Audio/Sink", "sink", "default", cfg.pre_sink, snapshot)
    if source != NO_HARDWARE_TARGET:
        cfg.pre_source = source
    if sink != NO_HARDWARE_TARGET:
        cfg.pre_sink = sink
    for direction in ("source", "sink"):
        configured = defaults.get(f"default.configured.audio.{direction}", "")
        if configured not in APP_NODE_NAMES:
            setattr(cfg, f"pre_configured_{direction}", configured)
    save_config(cfg)


def restore_previous_default_devices(cfg: AudioConfig | None = None,
                                     directions: set[str] | None = None) -> None:
    """Restore saved hardware defaults, using live physical devices if absent."""
    if not shutil.which("wpctl"):
        return
    if cfg is None:
        cfg = load_config()
    snapshot = pipewire_audio_snapshot()
    if snapshot is None:
        return
    nodes, defaults = snapshot
    for media_class, direction, preferred, configured in (
        ("Audio/Source", "source", cfg.pre_source, cfg.pre_configured_source),
        ("Audio/Sink", "sink", cfg.pre_sink, cfg.pre_configured_sink),
    ):
        if directions is not None and direction not in directions:
            continue
        selected = None
        for name in (preferred, defaults.get(f"default.audio.{direction}"),
                     defaults.get(f"default.configured.audio.{direction}"),
                     *nodes.keys()):
            props = nodes.get(name or "")
            if props and props.get("media.class") == media_class:
                selected = props.get("id")
                break
        if selected is not None:
            try:
                result = subprocess.run(["wpctl", "set-default", str(selected)],
                                        capture_output=True, text=True, env=COMMAND_ENV, timeout=2)
                if result.returncode:
                    print(f"[DuskyAudio] Could not restore {direction}: {result.stderr.strip()}",
                          file=sys.stderr)
                    continue
                if configured and shutil.which("pw-metadata"):
                    result = subprocess.run(
                        ["pw-metadata", "-n", "default", "0",
                         f"default.configured.audio.{direction}",
                         json.dumps({"name": configured}), "Spa:String:JSON"],
                        capture_output=True, text=True, env=COMMAND_ENV, timeout=2)
                    if result.returncode:
                        print(f"[DuskyAudio] Could not restore configured {direction}: {result.stderr.strip()}",
                              file=sys.stderr)
                elif not configured:
                    result = subprocess.run(
                        ["wpctl", "clear-default", "1" if direction == "source" else "0"],
                        capture_output=True, text=True, env=COMMAND_ENV, timeout=2)
                    if result.returncode:
                        print(f"[DuskyAudio] Could not clear configured {direction}: {result.stderr.strip()}",
                              file=sys.stderr)
            except (OSError, subprocess.SubprocessError) as exc:
                print(f"[DuskyAudio] Could not restore {direction}: {exc}", file=sys.stderr)


def set_dusky_devices_as_default() -> bool:
    """
    Automatically sets Dusky Mic (Source) and Dusky Audio (Sink) as the system's
    active default audio devices in PipeWire / WirePlumber upon engine startup.
    Allows manual override at any time via pavucontrol, wpctl, or desktop applets.
    """
    if not shutil.which("wpctl") or not shutil.which("pw-dump"):
        return False
    cfg = load_config()
    snapshot = pipewire_audio_snapshot()
    if snapshot is None:
        return False
    source_target = resolve_hardware_node("Audio/Source", "source", cfg.source,
                                          cfg.pre_source, snapshot)
    sink_target = resolve_hardware_node("Audio/Sink", "sink", cfg.sink,
                                        cfg.pre_sink, snapshot)

    last_error = "audio nodes or links did not become ready"
    for _ in range(40):
        try:
            graph = json.loads(subprocess.check_output(
                ["pw-dump", "--no-colors"], text=True, stderr=subprocess.DEVNULL,
                env=COMMAND_ENV, timeout=2))
            expected_classes = {
                "ghelper-audio": "Audio/Source",
                "ghelper-audio-sink": "Audio/Sink",
                "ghelper-audio-capture": "Stream/Input/Audio",
                "ghelper-audio-sink-out": "Stream/Output/Audio",
            }
            node_ids = {
                props.get("node.name"): obj.get("id")
                for obj in graph if obj.get("type", "").endswith(":Node")
                for props in (obj.get("info", {}).get("props", {}),)
                if props.get("node.name") and (
                    props.get("node.name") not in expected_classes or
                    props.get("media.class") == expected_classes[props["node.name"]])
            }
            links = {
                (obj.get("info", {}).get("props", {}).get("link.output.node"),
                 obj.get("info", {}).get("props", {}).get("link.input.node"))
                for obj in graph if obj.get("type", "").endswith(":Link")
            }
            mic_id = node_ids.get("ghelper-audio")
            virtual_sink_id = node_ids.get("ghelper-audio-sink")
            capture_id = node_ids.get("ghelper-audio-capture")
            playback_id = node_ids.get("ghelper-audio-sink-out")
            if None in (mic_id, virtual_sink_id, capture_id, playback_id):
                time.sleep(0.05)
                continue
            app_ids = {mic_id, virtual_sink_id, capture_id, playback_id}
            if any(src in app_ids and dst in app_ids for src, dst in links):
                last_error = "audio graph contains a self-route"
                break
            if (source_target != NO_HARDWARE_TARGET and
                    (node_ids.get(source_target), capture_id) not in links):
                time.sleep(0.05)
                continue
            if (sink_target != NO_HARDWARE_TARGET and
                    (playback_id, node_ids.get(sink_target)) not in links):
                time.sleep(0.05)
                continue
            for available, node_id in ((source_target != NO_HARDWARE_TARGET, mic_id),
                                       (sink_target != NO_HARDWARE_TARGET, virtual_sink_id)):
                if available:
                    subprocess.run(["wpctl", "set-default", str(node_id)],
                                   check=True, capture_output=True, text=True,
                                   env=COMMAND_ENV, timeout=2)
            return True
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            last_error = str(e)
        time.sleep(0.05)
    print(f"[DuskyAudio] Could not establish audio routing: {last_error}", file=sys.stderr)
    return False


# -----------------------------------------------------------------------------
#   UNIX Domain Socket IPC & Direct Subprocess Daemon Server
# -----------------------------------------------------------------------------
class AudioDspServer:
    def __init__(self, bin_path: Path) -> None:
        self.bin_path = bin_path
        self.proc: subprocess.Popen[bytes] | None = None
        self.sock: socket.socket | None = None
        self._owns_socket = False
        self.running = False
        self.telemetry = AudioTelemetry()
        self._lock = threading.Lock()
        self._command_lock = threading.RLock()
        self.config: AudioConfig | None = None

    def start(self) -> bool:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if SOCK_PATH.exists():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(0.2)
                    client.connect(str(SOCK_PATH))
                    print("[DuskyAudioServer] Another server is running", file=sys.stderr)
                    return False
            except OSError:
                SOCK_PATH.unlink(missing_ok=True)

        try:
            with open(CACHE_DIR / "engine.log", "a", encoding="utf-8") as log:
                self.proc = subprocess.Popen(
                    [str(self.bin_path)], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=log, bufsize=0,
                )
        except Exception as e:
            print(f"[DuskyAudioServer] Failed to launch {self.bin_path}: {e}", file=sys.stderr)
            return False

        os.set_blocking(self.proc.stdin.fileno(), False)
        self.running = True
        threading.Thread(target=self._telemetry_reader, daemon=True).start()

        # Create UNIX domain socket
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.bind(str(SOCK_PATH))
            self._owns_socket = True
        except OSError as e:
            print(f"[DuskyAudioServer] Socket startup failed: {e}", file=sys.stderr)
            self.stop()
            return False
        # Owner-only: the socket accepts arbitrary DSP commands and must not
        # be reachable by other local users regardless of umask.
        os.chmod(SOCK_PATH, 0o600)
        self.sock.listen(10)
        self.sock.settimeout(0.5)

        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))

        # Immediately restore all persisted audio settings (input/output EQ, vocoder, spatial DSP, denoise)
        try:
            init_cfg = load_config()
            self._apply_full_config(init_cfg)
        except Exception as e:
            print(f"[DuskyAudioServer] Configuration failed: {e}", file=sys.stderr)
            self.stop()
            return False

        threading.Thread(target=self._watch_devices, daemon=True).start()

        return True

    def _watch_devices(self) -> None:
        while self.running:
            time.sleep(2.0)
            with self._command_lock:
                cfg = self.config
                if not self.running or cfg is None:
                    continue
                snapshot = pipewire_audio_snapshot()
                if snapshot is None:
                    continue
                source = resolve_hardware_node("Audio/Source", "source", cfg.source,
                                               cfg.pre_source, snapshot)
                sink = resolve_hardware_node("Audio/Sink", "sink", cfg.sink,
                                             cfg.pre_sink, snapshot)
                # The helper ignores unchanged healthy routes and recreates failed
                # streams, including devices that return between polling intervals.
                try:
                    self.send_cmd(f"SRC {source}")
                    self.send_cmd(f"SINK_TGT {sink}")
                except RuntimeError as exc:
                    print(f"[DuskyAudioServer] Device routing failed: {exc}", file=sys.stderr)

    def _telemetry_reader(self) -> None:
        if not self.proc or not self.proc.stdout:
            return

        stdout_fd = self.proc.stdout.fileno()
        buf = bytearray()
        magic_bytes: Final[bytes] = struct.pack("<I", MAGIC)

        while self.running and self.proc.poll() is None:
            try:
                chunk = os.read(stdout_fd, 4096)
                if not chunk:
                    break
                buf.extend(chunk)
                while len(buf) >= FRAME_SIZE:
                    (
                        magic,
                        ver,
                        seq,
                        flags,
                        vad_prob,
                        rms_in_db,
                        rms_out_db,
                        noise_red_db,
                        pitch_hz,
                    ) = HEADER_STRUCT.unpack_from(buf, 0)
                    if magic == MAGIC and ver == PROTOCOL_VERSION:
                        del buf[:FRAME_SIZE]
                        with self._lock:
                            self.telemetry = AudioTelemetry(
                                seq=seq,
                                flags=flags,
                                vad_prob=vad_prob,
                                rms_in_db=rms_in_db,
                                rms_out_db=rms_out_db,
                                processing_delta_dbfs=noise_red_db,
                                tracked_pitch_hz=pitch_hz,
                            )
                    else:
                        idx = buf.find(magic_bytes, 1)
                        if idx != -1:
                            del buf[:idx]
                        else:
                            # Preserve a possible magic prefix split across reads.
                            del buf[:-(len(magic_bytes) - 1)]
                            break
            except Exception:
                break

    def send_cmd(self, line: str) -> None:
        with self._command_lock:
            if not self.proc or not self.proc.stdin or self.proc.poll() is not None:
                raise RuntimeError("DSP helper is not running")
            try:
                payload = (line.strip() + "\n").encode("utf-8")
                # Match the helper's line buffer and Linux PIPE_BUF. Nonblocking
                # writes up to this size are atomic, so a timeout leaves no fragment.
                if len(payload) > 4096:
                    raise RuntimeError("DSP command exceeds 4095 UTF-8 bytes")
                fd = self.proc.stdin.fileno()
                deadline = time.monotonic() + 0.5
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([], [fd], [], remaining)[1]:
                        raise TimeoutError("DSP command pipe is stalled")
                    try:
                        os.write(fd, payload)
                    except BlockingIOError:
                        continue
                    break
            except OSError as e:
                raise RuntimeError(f"DSP command failed: {e}") from e

    def stop(self) -> None:
        self.running = False
        try:
            self.send_cmd("QUIT")
        except RuntimeError:
            pass
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait()
        elif self.proc:
            self.proc.wait()

        if self.proc:
            for pipe in (self.proc.stdin, self.proc.stdout):
                if pipe:
                    pipe.close()

        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

        if self._owns_socket:
            SOCK_PATH.unlink(missing_ok=True)
            PID_FILE.unlink(missing_ok=True)
            self._owns_socket = False

    def serve_forever(self) -> None:
        try:
            self._serve_clients()
        finally:
            self.stop()
            if self.config is not None:
                snapshot = pipewire_audio_snapshot()
                if snapshot is not None:
                    defaults = snapshot[1]
                    directions = {
                        direction for direction, virtual_name in (
                            ("source", "ghelper-audio"),
                            ("sink", "ghelper-audio-sink"),
                        ) if any(defaults.get(f"default.{prefix}audio.{direction}") == virtual_name
                                 for prefix in ("", "configured."))
                    }
                    if directions:
                        restore_previous_default_devices(self.config, directions)

    def _serve_clients(self) -> None:
        while self.running:
            try:
                conn, _ = self.sock.accept()  # type: ignore
            except (socket.timeout, OSError):
                if not self.running or (self.proc and self.proc.poll() is not None):
                    break
                continue

            threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()

    def _handle_client(self, conn: socket.socket) -> None:
        conn.settimeout(5.0)
        try:
            with conn:
                buf = bytearray()
                while self.running:
                    try:
                        chunk = conn.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buf.extend(chunk)
                    if len(buf) > 65536:
                        conn.sendall(b"ERROR oversized command\n")
                        return
                    while (end := buf.find(b"\n")) != -1:
                        line = buf.take_bytes(end + 1)
                        try:
                            cmd = line.decode("utf-8").strip()
                        except UnicodeDecodeError:
                            conn.sendall(b"ERROR invalid UTF-8\n")
                            continue
                        if not cmd:
                            continue

                        if cmd == "GET_TELEMETRY":
                            with self._lock:
                                resp = json.dumps(asdict(self.telemetry)) + "\n"
                            conn.sendall(resp.encode("utf-8"))
                        elif cmd == "PING":
                            conn.sendall(b"PONG\n" if self.proc and self.proc.poll() is None else b"ERROR\n")
                        elif cmd == "QUIT":
                            self.running = False
                            conn.sendall(b"OK\n")
                            return
                        elif cmd.startswith("CMD "):
                            try:
                                self.send_cmd(cmd[4:])
                                conn.sendall(b"OK\n")
                            except RuntimeError as e:
                                conn.sendall(f"ERROR {e}\n".encode())
                        elif cmd.startswith("CONFIG_SYNC "):
                            try:
                                cfg_dict = json.loads(cmd[12:])
                                cfg = AudioConfig(**cfg_dict)
                                validate_config(cfg)
                                with self._command_lock:
                                    self._apply_full_config(cfg)
                                conn.sendall(b"OK\n")
                            except (ValueError, TypeError, RuntimeError) as e:
                                conn.sendall(f"ERROR {e}\n".encode())
        except OSError as e:
            print(f"[DuskyAudioServer] Client connection failed: {e}", file=sys.stderr)

    def _apply_full_config(self, cfg: AudioConfig) -> None:
        validate_config(cfg)
        self.config = cfg
        # Hardware source & sink resolution (eliminates feedback loops)
        snapshot = pipewire_audio_snapshot()
        if snapshot is None:
            target_src = target_sink = NO_HARDWARE_TARGET
        else:
            target_src = resolve_hardware_node("Audio/Source", "source", cfg.source,
                                               cfg.pre_source, snapshot)
            target_sink = resolve_hardware_node("Audio/Sink", "sink", cfg.sink,
                                                cfg.pre_sink, snapshot)
        self.send_cmd(f"SRC {target_src}")
        self.send_cmd(f"SINK_TGT {target_sink}")
        self.send_cmd(f"VOL {cfg.volume * 10}")
        self.send_cmd(f"MON {1 if cfg.monitor else 0}")

        # RNNoise Suppression - Input / Microphone
        self.send_cmd(f"RNN {1 if (cfg.enabled and cfg.rnnoise_on) else 0}")
        self.send_cmd(f"AGG {cfg.aggressiveness * 10}")

        # RNNoise Suppression - Output / Speaker & Headphone (Two-Way)
        self.send_cmd(f"OUT_NOISE {1 if (cfg.enabled and cfg.out_rnnoise_on) else 0}")
        self.send_cmd(f"OUT_AGG {cfg.out_aggressiveness * 10}")

        # Vocoder & Voice Transformers
        self.send_cmd(f"VOC {1 if (cfg.enabled and cfg.vocoder_on) else 0}")
        self.send_cmd(
            f"VOP {cfg.vocoder_mix * 10} {cfg.vocoder_carrier_hz} {cfg.vocoder_attack_ms} {cfg.vocoder_release_ms} {cfg.vocoder_detune} {1 if cfg.vocoder_follow else 0} {cfg.vocoder_pitch_shift}"
        )
        self.send_cmd(f"MTX {cfg.vocoder_matrix * 10}")
        self.send_cmd(f"PSH {cfg.pitch_shift if cfg.enabled else 0}")
        self.send_cmd(f"ATN {1 if (cfg.enabled and cfg.autotune_on) else 0}")
        self.send_cmd(f"ATT {cfg.autotune_target_hz if cfg.enabled else 0}")
        self.send_cmd(f"BCR {cfg.bitcrush_bits if cfg.enabled else 0} {cfg.bitcrush_downsample}")
        self.send_cmd(f"BPF {cfg.bandpass_hpf_hz if cfg.enabled else 0} {cfg.bandpass_lpf_hz if cfg.enabled else 0}")
        self.send_cmd(f"STT {cfg.stutter_hz if cfg.enabled else 0} 500")

        # Delay
        self.send_cmd(f"DLY {1 if (cfg.enabled and cfg.delay_on) else 0}")
        self.send_cmd(f"DLP {cfg.delay_ms} {cfg.delay_feedback * 10} {cfg.delay_mix * 10}")

        # Reverb
        self.send_cmd(f"RVB {1 if (cfg.enabled and cfg.reverb_on) else 0}")
        self.send_cmd(f"RVP {cfg.reverb_room * 10} {cfg.reverb_damp * 10} {cfg.reverb_width * 10} {cfg.reverb_mix * 10}")

        # 9-Band EQ & Uniform Post-Gain (Microphone Input)
        self.send_cmd(f"EQ {1 if (cfg.enabled and cfg.eq_on) else 0}")
        self.send_cmd(f"EGN {cfg.eq_post_gain}")
        for idx, gain in enumerate(cfg.eq_gains):
            _, kind, hz, q = EQ_BANDS[idx]
            self.send_cmd(f"EQB {idx} {kind} {hz} {q} {gain}")

        # Stereo 9-Band EQ & Uniform Post-Gain (Playback Output)
        self.send_cmd(f"OUT_EQ {1 if (cfg.enabled and cfg.out_eq_on) else 0}")
        self.send_cmd(f"OUT_EGN {cfg.out_eq_post_gain}")
        for idx, gain in enumerate(cfg.out_eq_gains):
            _, kind, hz, q = EQ_BANDS[idx]
            self.send_cmd(f"OUT_EQB {idx} {kind} {hz} {q} {gain}")

        # Output Playback Stereo Voice Transformers
        self.send_cmd(f"OUT_VOC {1 if (cfg.enabled and cfg.out_vocoder_on) else 0}")
        self.send_cmd(
            f"OUT_VOP {cfg.out_vocoder_mix * 10} {cfg.out_vocoder_carrier_hz} {cfg.out_vocoder_attack_ms} {cfg.out_vocoder_release_ms} {cfg.out_vocoder_detune} {1 if cfg.out_vocoder_follow else 0} {cfg.out_vocoder_pitch_shift}"
        )
        self.send_cmd(f"OUT_MTX {cfg.out_vocoder_matrix * 10}")
        self.send_cmd(f"OUT_PSH {cfg.out_pitch_shift if cfg.enabled else 0}")
        self.send_cmd(f"OUT_ATN {1 if (cfg.enabled and cfg.out_autotune_on) else 0}")
        self.send_cmd(f"OUT_ATT {cfg.out_autotune_target_hz if cfg.enabled else 0}")
        self.send_cmd(f"OUT_BCR {cfg.out_bitcrush_bits if cfg.enabled else 0} {cfg.out_bitcrush_downsample}")
        self.send_cmd(f"OUT_BPF {cfg.out_bandpass_hpf_hz if cfg.enabled else 0} {cfg.out_bandpass_lpf_hz if cfg.enabled else 0}")
        self.send_cmd(f"OUT_STT {cfg.out_stutter_hz if cfg.enabled else 0} 500")

        # Output Playback Stereo Delay
        self.send_cmd(f"OUT_DLY {1 if (cfg.enabled and cfg.out_delay_on) else 0}")
        self.send_cmd(f"OUT_DLP {cfg.out_delay_ms} {cfg.out_delay_feedback * 10} {cfg.out_delay_mix * 10}")

        # Output Playback Stereo Reverb
        self.send_cmd(f"OUT_RVB {1 if (cfg.enabled and cfg.out_reverb_on) else 0}")
        self.send_cmd(f"OUT_RVP {cfg.out_reverb_room * 10} {cfg.out_reverb_damp * 10} {cfg.out_reverb_width * 10} {cfg.out_reverb_mix * 10}")


# --- Client Communication Helpers ---
def send_daemon_cmd(cmd_str: str) -> bool:
    if not SOCK_PATH.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.5)
            client.connect(str(SOCK_PATH))
            client.sendall(f"CMD {cmd_str.strip()}\n".encode("utf-8"))
            with client.makefile("rb") as response:
                resp = response.readline(4096)
            return resp == b"OK\n"
    except Exception:
        return False


def daemon_responds() -> bool:
    if not get_daemon_pid() or not SOCK_PATH.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.3)
            client.connect(str(SOCK_PATH))
            client.sendall(b"PING\n")
            with client.makefile("rb") as response:
                return response.readline(16) == b"PONG\n"
    except OSError:
        return False


def sync_config_to_daemon(cfg: AudioConfig) -> bool:
    if not SOCK_PATH.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            client.connect(str(SOCK_PATH))
            payload = json.dumps(asdict(cfg))
            client.sendall(f"CONFIG_SYNC {payload}\n".encode("utf-8"))
            with client.makefile("rb") as response:
                resp = response.readline(4096)
            return resp == b"OK\n"
    except Exception:
        return False


_telemetry_client: socket.socket | None = None
_telemetry_buffer = b""


def fetch_telemetry_from_daemon() -> AudioTelemetry | None:
    global _telemetry_client, _telemetry_buffer
    if not SOCK_PATH.exists():
        if _telemetry_client:
            _telemetry_client.close()
            _telemetry_client = None
            _telemetry_buffer = b""
        return None
    try:
        if _telemetry_client is None:
            _telemetry_client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            _telemetry_client.settimeout(0.03)
            _telemetry_client.connect(str(SOCK_PATH))
        _telemetry_client.sendall(b"GET_TELEMETRY\n")
        while b"\n" not in _telemetry_buffer:
            chunk = _telemetry_client.recv(1024)
            if not chunk:
                raise ConnectionError("telemetry connection closed")
            _telemetry_buffer += chunk
            if len(_telemetry_buffer) > 4096:
                raise ValueError("oversized telemetry")
        raw, _telemetry_buffer = _telemetry_buffer.split(b"\n", 1)
        return AudioTelemetry(**json.loads(raw))
    except (OSError, ValueError, TypeError):
        if _telemetry_client:
            _telemetry_client.close()
            _telemetry_client = None
            _telemetry_buffer = b""
    return None


def start_daemon(cfg: AudioConfig | None = None) -> bool:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "startup.lock", "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if cfg is not None:
            save_config(cfg)
        current = load_config()
        ok = _start_daemon_locked(current)
        if not ok:
            current.enabled = False
            save_config(current)
        return ok


def _start_daemon_locked(cfg: AudioConfig) -> bool:
    existing_pid = get_daemon_pid()
    if not existing_pid:
        save_previous_default_devices(cfg)
    cfg.enabled = True
    save_config(cfg)

    pid = existing_pid
    if pid and SOCK_PATH.exists():
        if sync_config_to_daemon(cfg) and set_dusky_devices_as_default():
            return True

    if pid:
        _stop_daemon_unlocked(restore_defaults=False)
    else:
        SOCK_PATH.unlink(missing_ok=True)
        PID_FILE.unlink(missing_ok=True)

    missing = check_system_dependencies()
    if missing:
        err_msg = "Dusky Audio Studio cannot start due to missing dependencies:\n\n" + "\n".join(f"• {m}" for m in missing)
        print(f"\n[Dusky Audio Error]\n{err_msg}\n", file=sys.stderr)
        send_desktop_notification("Dusky Audio Studio — Missing Dependency", "\n".join(f"• {m}" for m in missing), "critical")
        return False

    bin_path = find_helper_binary()
    if not bin_path:
        return False

    # Spawn daemon server in background subprocess
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(CACHE_DIR / "server.log", "a", encoding="utf-8") as log:
            child = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--serve", str(bin_path)], stdout=log, stderr=log,
                stdin=subprocess.DEVNULL, cwd=str(STATE_DIR),
                start_new_session=True, env=COMMAND_ENV,
            )
    except OSError as exc:
        print(f"[DuskyAudio] Server launch failed: {exc}", file=sys.stderr)
        return False

    server_ready = False
    for _ in range(40):
        time.sleep(0.04)
        if child.poll() is not None:
            break
        if SOCK_PATH.exists():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(0.2)
                    s.connect(str(SOCK_PATH))
                    s.sendall(b"PING\n")
                    if s.recv(16).startswith(b"PONG"):
                        server_ready = True
                        break
            except Exception:
                pass

    if server_ready and sync_config_to_daemon(cfg) and set_dusky_devices_as_default():
        return True

    print(f"[DuskyAudio] Server startup failed; see {CACHE_DIR / 'server.log'}", file=sys.stderr)
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    _stop_daemon_unlocked(restore_defaults=True, cfg=cfg)
    return False


def stop_daemon(restore_defaults: bool = True, cfg: AudioConfig | None = None) -> bool:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "startup.lock", "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _stop_daemon_unlocked(restore_defaults, cfg)


def _stop_daemon_unlocked(restore_defaults: bool = True, cfg: AudioConfig | None = None) -> bool:
    directions: set[str] = set()
    if restore_defaults:
        snapshot = pipewire_audio_snapshot()
        if snapshot is not None:
            defaults = snapshot[1]
            for direction, virtual_name in (("source", "ghelper-audio"),
                                             ("sink", "ghelper-audio-sink")):
                if any(defaults.get(f"default.{prefix}audio.{direction}") == virtual_name
                       for prefix in ("", "configured.")):
                    directions.add(direction)
    pid = get_daemon_pid()
    if SOCK_PATH.exists():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                s.connect(str(SOCK_PATH))
                s.sendall(b"QUIT\n")
                s.recv(16)
        except Exception:
            pass

    if pid:
        for _ in range(30):
            time.sleep(0.05)
            if not pid_is_dusky_audio(pid):
                break
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            for _ in range(20):
                time.sleep(0.05)
                if not pid_is_dusky_audio(pid):
                    break
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    PID_FILE.unlink(missing_ok=True)
    SOCK_PATH.unlink(missing_ok=True)

    if directions:
        restore_previous_default_devices(cfg, directions)

    return True


# -----------------------------------------------------------------------------
#   GTK3 Interface & Reactive Studio Studio
# -----------------------------------------------------------------------------
def run_gtk_app(*, open_only: bool = False) -> None:
    os.environ["GDK_BACKEND"] = "wayland"
    if GUI_PID_FILE.exists():
        try:
            with open(GUI_PID_FILE, "r", encoding="utf-8") as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            if not pid_is_dusky_audio(old_pid):
                raise ValueError("pid recycled by another process")
            os.kill(old_pid, signal.SIGTERM)
            GUI_PID_FILE.unlink(missing_ok=True)
            return
        except (OSError, ValueError):
            GUI_PID_FILE.unlink(missing_ok=True)

    with open(GUI_PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    try:
        GLib.set_prgname("dusky_audio_studio.py")
        GLib.set_application_name("Dusky Audio Studio")
    except Exception:
        pass

    cfg = load_config()
    if open_only:
        # Opening settings must reflect the running DSP without starting it.
        cfg.enabled = bool(get_daemon_pid())

    provider = Gtk.CssProvider()
    provider.load_from_data(DUSKY_CSS.encode("utf-8"))
    screen = Gdk.Screen.get_default()
    if screen:
        Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

    class AudioStudioWindow(Gtk.Window):
        def __init__(self) -> None:
            super().__init__(title="Dusky Audio Studio & Voice DSP")
            self.set_default_size(630, 640)
            self.set_border_width(16)
            self.set_position(Gtk.WindowPosition.CENTER)
            self.get_style_context().add_class("panel-window")

            self.cfg = cfg
            self._last_status_check = 0.0
            self._save_timer = 0
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dusky-ui-control")
            self._command_lock = threading.Lock()
            self._command_epoch = 0
            self._command_serial: dict[str, int] = {}
            self._engine_target = cfg.enabled
            self._closed = False
            self._telemetry_stop = threading.Event()
            self._latest_telemetry: AudioTelemetry | None = None
            self._device_refresh_pending = False
            self.sources = [("default", "Default System Microphone (Auto)")]
            self.sinks = [("default", "Default System Output (Auto)")]
            self._updating_ui = False
            self.preset_buttons: dict[str, Gtk.Button] = {}
            self.current_voice_target = "mic"
            self.current_spatial_target = "mic"
            self.current_eq_target = "mic"
            self.eq_preset_buttons: dict[str, Gtk.Button] = {}

            main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            self.add(main_vbox)

            # --- Header: Title + Master Switch ---
            header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            left_spacer = Gtk.Box()
            left_spacer.set_size_request(44, -1)
            header_box.pack_start(left_spacer, False, False, 0)

            title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            title_box.set_halign(Gtk.Align.CENTER)
            title_box.set_hexpand(True)

            title_lbl = Gtk.Label(label="Dusky Audio Studio", xalign=0.5)
            title_lbl.get_style_context().add_class("header-title")

            self.status_lbl = Gtk.Label(xalign=0.5)
            self.update_status_label()

            title_box.pack_start(title_lbl, False, False, 0)
            title_box.pack_start(self.status_lbl, False, False, 0)
            header_box.pack_start(title_box, True, True, 0)

            self.master_switch = Gtk.Switch()
            self.master_switch.set_valign(Gtk.Align.CENTER)
            self.master_switch.set_halign(Gtk.Align.END)
            self.master_switch.get_style_context().add_class("compact-switch")
            self.master_switch.set_active(self.cfg.enabled)
            self.master_switch.connect("notify::active", self.on_master_toggled)
            header_box.pack_end(self.master_switch, False, False, 0)
            main_vbox.pack_start(header_box, False, False, 0)

            # --- Missing Dependencies Warning ---
            self.warning_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            main_vbox.pack_start(self.warning_container, False, False, 0)

            # --- Top Control Strip: Microphone Device, Monitor & Master Reset ---
            top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            self.src_combo = Gtk.ComboBoxText()
            self.src_combo.get_style_context().add_class("device-combo")
            self._set_device_combo(self.src_combo, self.sources, self.cfg.source)
            self.src_combo.connect("changed", self.on_source_changed)
            top_bar.pack_start(self.src_combo, True, True, 0)

            self.mon_btn = Gtk.CheckButton(label="Hear Voice")
            self.mon_btn.set_active(self.cfg.monitor)
            self.mon_btn.connect("toggled", self.on_monitor_toggled)
            top_bar.pack_start(self.mon_btn, False, False, 0)

            btn_reset_all = self.create_icon_button(
                "view-refresh-symbolic",
                "Reset All Defaults",
                "Reset all noise suppression, voice transformations, spatial effects, and EQ to clean factory defaults",
                "reset-btn",
                self.reset_all_defaults,
            )
            top_bar.pack_end(btn_reset_all, False, False, 0)

            main_vbox.pack_start(top_bar, False, False, 0)

            main_vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

            # --- Notebook Tabs ---
            self.notebook = Gtk.Notebook()
            self.notebook.set_scrollable(True)
            main_vbox.pack_start(self.notebook, True, True, 0)

            self.build_tab_noise()
            self.build_tab_voice_fx()
            self.build_tab_spatial_dsp()
            self.build_tab_equalizer()

            # --- Footer ---
            footer_lbl = Gtk.Label(
                label="Virtual Audio Device: Dusky Mic & Dusky Audio (PipeWire RT Low-Latency DSP)",
                xalign=0.5,
            )
            footer_lbl.get_style_context().add_class("footer-info")
            main_vbox.pack_end(footer_lbl, False, False, 0)

            if self.cfg.enabled and not open_only:
                self._queue_engine_state(True)
            else:
                threading.Thread(target=self._check_dependencies, daemon=True).start()

            # Start 30 Hz Telemetry Polling Timer
            threading.Thread(target=self._telemetry_loop, daemon=True).start()
            GLib.timeout_add(33, self.poll_telemetry)
            self.refresh_device_lists()
            GLib.timeout_add_seconds(5, self.refresh_device_lists)

        def _check_dependencies(self) -> None:
            GLib.idle_add(self._show_dependency_warning, check_system_dependencies())

        def _show_dependency_warning(self, missing: list[str]) -> bool:
            if self._closed:
                return False
            for child in self.warning_container.get_children():
                self.warning_container.remove(child)
            if missing:
                warn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                warn_box.get_style_context().add_class("warning-banner")
                warn_title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                warn_icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic", Gtk.IconSize.MENU)
                warn_title = Gtk.Label(label="Missing System Audio Dependencies:", xalign=0)
                warn_title.get_style_context().add_class("warning-text")
                warn_title_box.pack_start(warn_icon, False, False, 0)
                warn_title_box.pack_start(warn_title, False, False, 0)
                warn_box.pack_start(warn_title_box, False, False, 0)
                for item in missing:
                    item_lbl = Gtk.Label(label=f"  • {item}", xalign=0)
                    item_lbl.get_style_context().add_class("footer-info")
                    warn_box.pack_start(item_lbl, False, False, 0)
                self.warning_container.pack_start(warn_box, False, False, 0)
                self.warning_container.show_all()
            return False

        def _queue_command(self, command: str) -> None:
            verb, _, arguments = command.partition(" ")
            key = (f"{verb}:{arguments.partition(' ')[0]}"
                   if verb in ("EQB", "OUT_EQB") else verb)
            with self._command_lock:
                serial = self._command_serial.get(key, 0) + 1
                self._command_serial[key] = serial
                epoch = self._command_epoch

            def send_latest() -> None:
                with self._command_lock:
                    if (self._command_epoch != epoch or
                            self._command_serial.get(key) != serial):
                        return
                send_daemon_cmd(command)

            self._executor.submit(send_latest)

        def _queue_config_sync(self) -> None:
            with self._command_lock:
                self._command_epoch += 1
            self._executor.submit(lambda: sync_config_to_daemon(load_config()))

        def _queue_engine_state(self, active: bool) -> None:
            with self._command_lock:
                self._command_epoch += 1
            self._engine_target = active
            self._executor.submit(self._apply_engine_state, active)

        def _apply_engine_state(self, active: bool) -> None:
            if self._engine_target != active:
                return
            try:
                current = load_config()
                current.enabled = active
                if active:
                    ok = start_daemon(current)
                    if ok:
                        sync_config_to_daemon(load_config())
                else:
                    ok = stop_daemon(restore_defaults=True, cfg=current)
                    save_config(current)
            except Exception as exc:
                print(f"[DuskyAudio] Engine state change failed: {exc}", file=sys.stderr)
                ok = False
            GLib.idle_add(self._engine_state_finished, active, ok)

        def _engine_state_finished(self, active: bool, ok: bool) -> bool:
            if self._closed or self._engine_target != active:
                return False
            if active and not ok:
                self._updating_ui = True
                try:
                    self.master_switch.set_active(False)
                finally:
                    self._updating_ui = False
                self.cfg.enabled = False
                save_config(self.cfg)
                threading.Thread(target=self._check_dependencies, daemon=True).start()
            self.update_status_label()
            return False

        def _telemetry_loop(self) -> None:
            while not self._telemetry_stop.is_set():
                self._latest_telemetry = fetch_telemetry_from_daemon()
                self._telemetry_stop.wait(0.033)

        def _set_device_combo(self, combo: Gtk.ComboBoxText,
                              devices: list[tuple[str, str]], selected: str) -> None:
            combo.remove_all()
            for node, description in devices:
                combo.append(node, description)
            if selected not in {node for node, _ in devices}:
                combo.append(selected, "Unavailable; using fallback: " + selected[:32])
                combo.set_tooltip_text("The selected device is unavailable. Audio uses an available physical device until it returns.\n" + selected)
            else:
                combo.set_tooltip_text(None)
            combo.set_active_id(selected)

        def refresh_device_lists(self) -> bool:
            if self._device_refresh_pending or self._closed:
                return True
            self._device_refresh_pending = True
            threading.Thread(target=self._load_device_lists, daemon=True).start()
            return True

        def _load_device_lists(self) -> None:
            GLib.idle_add(self._apply_device_lists, enumerate_devices())

        def _apply_device_lists(self, devices: tuple[list[tuple[str, str]],
                                                   list[tuple[str, str]]] | None) -> bool:
            self._device_refresh_pending = False
            if self._closed or devices is None:
                return False
            sources, sinks = devices
            if sources != self.sources or sinks != self.sinks:
                previous = self._updating_ui
                self._updating_ui = True
                try:
                    self.sources, self.sinks = sources, sinks
                    self._set_device_combo(self.src_combo, sources, self.cfg.source)
                    self._set_device_combo(self.sink_combo, sinks, self.cfg.sink)
                finally:
                    self._updating_ui = previous
            return False

        def persist_config(self) -> None:
            if self._save_timer:
                GLib.source_remove(self._save_timer)
            self._save_timer = GLib.timeout_add(180, self.flush_config)

        def flush_config(self) -> bool:
            self._save_timer = 0
            save_config(self.cfg)
            return False

        def create_tab_label(self, icon_name: str, label_text: str) -> Gtk.Box:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.set_halign(Gtk.Align.CENTER)
            box.set_valign(Gtk.Align.CENTER)
            box.set_hexpand(True)
            icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
            lbl = Gtk.Label(label=label_text)
            box.pack_start(icon, False, False, 0)
            box.pack_start(lbl, False, False, 0)
            box.show_all()
            return box

        def add_notebook_tab(self, page: Gtk.Widget, icon_name: str, label_text: str) -> None:
            tab_box = self.create_tab_label(icon_name, label_text)
            self.notebook.append_page(page, tab_box)
            self.notebook.child_set_property(page, "tab-expand", True)
            self.notebook.child_set_property(page, "tab-fill", True)

        def create_icon_button(
            self,
            icon_name: str,
            label_text: str,
            tooltip: str = "",
            css_class: str = "",
            callback: Any = None,
        ) -> Gtk.Button:
            btn = Gtk.Button()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
            lbl = Gtk.Label(label=label_text)
            box.pack_start(icon, False, False, 0)
            box.pack_start(lbl, False, False, 0)
            btn.add(box)
            if css_class:
                btn.get_style_context().add_class(css_class)
            if tooltip:
                btn.set_tooltip_text(tooltip)
            if callback:
                btn.connect("clicked", callback)
            return btn

        # ---------------------------------------------------------------------
        # Tab 1: Noise & Telemetry
        # ---------------------------------------------------------------------
        def build_tab_noise(self) -> None:
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            vbox.set_border_width(12)

            # Master Output Volume
            self.vol_row = self.create_slider_row("Microphone Output Gain", self.cfg.volume, 0, 200, "%", self.on_volume_changed)
            self.vol_row.set_tooltip_text("Boosts above 100% may clip in recording apps or at the output device.")
            vbox.pack_start(self.vol_row, False, False, 0)

            # RNNoise Neural Toggle
            rnn_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            rnn_lbl = Gtk.Label(label="RNNoise Neural Suppression", xalign=0)
            rnn_lbl.get_style_context().add_class("section-label")
            self.rnn_switch = Gtk.Switch()
            self.rnn_switch.get_style_context().add_class("compact-switch")
            self.rnn_switch.set_active(self.cfg.rnnoise_on)
            self.rnn_switch.connect("notify::active", self.on_rnnoise_toggled)
            rnn_hdr.pack_start(rnn_lbl, True, True, 0)
            rnn_hdr.pack_end(self.rnn_switch, False, False, 0)
            vbox.pack_start(rnn_hdr, False, False, 0)

            # Aggressiveness
            self.agg_row = self.create_slider_row(
                "Noise Gate Aggressiveness (Silence Attenuation)",
                self.cfg.aggressiveness,
                0,
                100,
                "%",
                self.on_agg_changed,
            )
            vbox.pack_start(self.agg_row, False, False, 0)

            vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

            # Output (Two-Way) Noise Cancellation - Speakers / Headphones
            out_section_lbl = Gtk.Label(
                label="Output Noise Cancellation (Incoming Audio)",
                xalign=0,
            )
            out_section_lbl.get_style_context().add_class("section-label")
            vbox.pack_start(out_section_lbl, False, False, 0)

            out_desc = Gtk.Label(
                label=(
                    "Filters background noise from other people's microphones "
                    "in Discord, Zoom, and browser calls before it reaches "
                    "your speakers or headphones."
                ),
                xalign=0,
                wrap=True,
            )
            out_desc.get_style_context().add_class("dim-label")
            vbox.pack_start(out_desc, False, False, 0)

            # Output Device Combo
            sink_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            sink_lbl = Gtk.Label(label="Physical Playback Device:", xalign=0)
            sink_lbl.get_style_context().add_class("section-label")
            self.sink_combo = Gtk.ComboBoxText()
            self.sink_combo.get_style_context().add_class("device-combo")
            self._set_device_combo(self.sink_combo, self.sinks, self.cfg.sink)
            self.sink_combo.connect("changed", self.on_sink_changed)
            sink_box.pack_start(sink_lbl, False, False, 0)
            sink_box.pack_start(self.sink_combo, True, True, 0)
            vbox.pack_start(sink_box, False, False, 0)

            out_rnn_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            out_rnn_lbl = Gtk.Label(label="Output RNNoise Suppression", xalign=0)
            out_rnn_lbl.get_style_context().add_class("section-label")
            self.out_rnn_switch = Gtk.Switch()
            self.out_rnn_switch.get_style_context().add_class("compact-switch")
            self.out_rnn_switch.set_active(self.cfg.out_rnnoise_on)
            self.out_rnn_switch.connect("notify::active", self.on_out_rnnoise_toggled)
            out_rnn_hdr.pack_start(out_rnn_lbl, True, True, 0)
            out_rnn_hdr.pack_end(self.out_rnn_switch, False, False, 0)
            vbox.pack_start(out_rnn_hdr, False, False, 0)

            self.out_agg_row = self.create_slider_row(
                "Output Noise Gate Aggressiveness",
                self.cfg.out_aggressiveness,
                0,
                100,
                "%",
                self.on_out_agg_changed,
            )
            vbox.pack_start(self.out_agg_row, False, False, 0)

            vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

            # Live Telemetry Visualizer Card
            tele_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            tele_box.get_style_context().add_class("telemetry-card")

            tele_title = Gtk.Label(label="Live Signal Telemetry", xalign=0)
            tele_title.get_style_context().add_class("section-label")
            tele_box.pack_start(tele_title, False, False, 0)

            grid = Gtk.Grid()
            grid.set_column_spacing(12)
            grid.set_row_spacing(6)
            grid.set_hexpand(True)

            # Voice Activity Probability
            vad_lbl = Gtk.Label(label="Voice Activity", xalign=0)
            vad_lbl.get_style_context().add_class("meter-label")
            self.vad_val_lbl = Gtk.Label(label="0%", xalign=1)
            self.vad_val_lbl.get_style_context().add_class("meter-val")
            self.vad_bar = Gtk.ProgressBar()
            grid.attach(vad_lbl, 0, 0, 1, 1)
            grid.attach(self.vad_bar, 1, 0, 1, 1)
            grid.attach(self.vad_val_lbl, 2, 0, 1, 1)

            # Denoiser signal-change level
            red_lbl = Gtk.Label(label="Denoiser Signal Change", xalign=0)
            red_lbl.set_tooltip_text("Level of the difference between aligned dry input and the final denoiser blend; this is not a measure of removed background noise.")
            red_lbl.get_style_context().add_class("meter-label")
            self.red_val_lbl = Gtk.Label(label="-∞ dBFS", xalign=1)
            self.red_val_lbl.get_style_context().add_class("meter-val")
            self.red_bar = Gtk.ProgressBar()
            grid.attach(red_lbl, 0, 1, 1, 1)
            grid.attach(self.red_bar, 1, 1, 1, 1)
            grid.attach(self.red_val_lbl, 2, 1, 1, 1)

            # Input RMS Level
            in_lbl = Gtk.Label(label="Input Level", xalign=0)
            in_lbl.get_style_context().add_class("meter-label")
            self.in_val_lbl = Gtk.Label(label="-inf dB", xalign=1)
            self.in_val_lbl.get_style_context().add_class("meter-val")
            self.in_bar = Gtk.ProgressBar()
            grid.attach(in_lbl, 0, 2, 1, 1)
            grid.attach(self.in_bar, 1, 2, 1, 1)
            grid.attach(self.in_val_lbl, 2, 2, 1, 1)

            # Output RMS Level
            out_lbl = Gtk.Label(label="Output Level", xalign=0)
            out_lbl.get_style_context().add_class("meter-label")
            self.out_val_lbl = Gtk.Label(label="-inf dB", xalign=1)
            self.out_val_lbl.get_style_context().add_class("meter-val")
            self.out_bar = Gtk.ProgressBar()
            grid.attach(out_lbl, 0, 3, 1, 1)
            grid.attach(self.out_bar, 1, 3, 1, 1)
            grid.attach(self.out_val_lbl, 2, 3, 1, 1)

            self.vad_bar.set_hexpand(True)
            self.red_bar.set_hexpand(True)
            self.in_bar.set_hexpand(True)
            self.out_bar.set_hexpand(True)

            tele_box.pack_start(grid, True, True, 0)
            vbox.pack_start(tele_box, False, False, 0)

            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled.add(vbox)
            self.add_notebook_tab(scrolled, "audio-input-microphone-symbolic", "Noise & Level")

        # ---------------------------------------------------------------------
        # ---------------------------------------------------------------------
        # Tab 2: Voice FX & Transformers (Microphone & Playback)
        # ---------------------------------------------------------------------
        def build_tab_voice_fx(self) -> None:
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            vbox.set_border_width(12)

            # Target Mode Switcher: Input (Mic) vs Output (Playback)
            target_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            target_box.set_halign(Gtk.Align.CENTER)
            target_box.get_style_context().add_class("segmented-group")

            self.btn_voice_target_mic = Gtk.Button(label="Microphone Voice FX")
            self.btn_voice_target_mic.get_style_context().add_class("segmented-btn")
            self.btn_voice_target_mic.get_style_context().add_class("active-preset")
            self.btn_voice_target_mic.connect("clicked", lambda _: self.set_voice_target("mic"))

            self.btn_voice_target_out = Gtk.Button(label="Playback Voice FX")
            self.btn_voice_target_out.get_style_context().add_class("segmented-btn")
            self.btn_voice_target_out.connect("clicked", lambda _: self.set_voice_target("out"))

            target_box.pack_start(self.btn_voice_target_mic, True, True, 0)
            target_box.pack_start(self.btn_voice_target_out, True, True, 0)
            vbox.pack_start(target_box, False, False, 2)

            preset_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.voice_hdr_lbl = Gtk.Label(label="Voice FX Character Presets (Microphone Input)", xalign=0)
            self.voice_hdr_lbl.get_style_context().add_class("section-label")
            btn_reset_voice = self.create_icon_button(
                "edit-undo-symbolic",
                "Reset Voice (Clean)",
                "Reset voice effects for the selected microphone or playback target to Natural Clean",
                "reset-btn",
                self.reset_voice_fx,
            )
            preset_hdr.pack_start(self.voice_hdr_lbl, True, True, 0)
            preset_hdr.pack_end(btn_reset_voice, False, False, 0)
            vbox.pack_start(preset_hdr, False, False, 0)

            flowbox = Gtk.FlowBox()
            flowbox.set_valign(Gtk.Align.START)
            flowbox.set_max_children_per_line(4)
            flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
            flowbox.set_row_spacing(6)
            flowbox.set_column_spacing(6)

            for name in PRESETS:
                btn = Gtk.Button(label=name)
                btn.get_style_context().add_class("preset-btn")
                btn.connect("clicked", lambda _, n=name: self.apply_preset_by_name(n))
                self.preset_buttons[name] = btn
                flowbox.add(btn)
            vbox.pack_start(flowbox, False, False, 0)

            vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

            # Granular Pitch Shifter
            self.pitch_row = self.create_slider_row(
                "Granular Pitch Shifter",
                int(self.cfg.pitch_shift / 100),
                -24,
                24,
                " st",
                self.on_pitch_changed,
            )
            vbox.pack_start(self.pitch_row, False, False, 0)

            # 16-Band Vocoder Header + Switch
            voc_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            voc_lbl = Gtk.Label(label="16-Band Robot Vocoder & Carrier Stack", xalign=0)
            voc_lbl.get_style_context().add_class("section-label")
            self.voc_switch = Gtk.Switch()
            self.voc_switch.get_style_context().add_class("compact-switch")
            self.voc_switch.set_active(self.cfg.vocoder_on)
            self.voc_switch.connect("notify::active", self.on_vocoder_toggled)
            voc_hdr.pack_start(voc_lbl, True, True, 0)
            voc_hdr.pack_end(self.voc_switch, False, False, 0)
            vbox.pack_start(voc_hdr, False, False, 0)

            # Vocoder Follow Voice Pitch + Tracked Pitch Indicator
            follow_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.check_follow = Gtk.CheckButton(label="Follow Voice Pitch (Adaptive Formant Tracking)")
            self.check_follow.set_active(self.cfg.vocoder_follow)
            self.check_follow.connect("toggled", self.on_follow_toggled)
            self.lbl_pitch_track = Gtk.Label(label="Tracked Pitch: —", xalign=1)
            self.lbl_pitch_track.get_style_context().add_class("value-label")
            follow_box.pack_start(self.check_follow, True, True, 0)
            follow_box.pack_end(self.lbl_pitch_track, False, False, 0)
            vbox.pack_start(follow_box, False, False, 0)

            # Carrier Pitch Slider (Adaptive Transpose or Hz)
            carrier_title = "Carrier Pitch Transposition" if self.cfg.vocoder_follow else "Carrier Frequency"
            carrier_val = self.cfg.vocoder_pitch_shift if self.cfg.vocoder_follow else self.cfg.vocoder_carrier_hz
            carrier_min = -24 if self.cfg.vocoder_follow else 50
            carrier_max = 24 if self.cfg.vocoder_follow else 880
            carrier_unit = " st" if self.cfg.vocoder_follow else " Hz"
            self.carrier_row = self.create_slider_row(
                carrier_title,
                carrier_val,
                carrier_min,
                carrier_max,
                carrier_unit,
                self.on_carrier_changed,
            )
            vbox.pack_start(self.carrier_row, False, False, 0)

            # Vocoder Mix & Matrix Timbre
            self.voc_mix_row = self.create_slider_row(
                "Vocoder Dry/Wet Mix", self.cfg.vocoder_mix, 0, 100, "%", self.on_voc_mix_changed
            )
            vbox.pack_start(self.voc_mix_row, False, False, 0)

            self.matrix_row = self.create_slider_row(
                "Matrix / Sentinel Timbre (Ring Mod + Saturation)",
                self.cfg.vocoder_matrix,
                0,
                100,
                "%",
                self.on_matrix_changed,
            )
            vbox.pack_start(self.matrix_row, False, False, 0)

            # Autotune Switch
            atn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            atn_lbl = Gtk.Label(label="Autotune (Chromatic or Fixed Target)", xalign=0)
            atn_lbl.get_style_context().add_class("section-label")
            self.atn_switch = Gtk.Switch()
            self.atn_switch.get_style_context().add_class("compact-switch")
            self.atn_switch.set_active(self.cfg.autotune_on)
            self.atn_switch.connect("notify::active", self.on_autotune_toggled)
            atn_box.pack_start(atn_lbl, True, True, 0)
            atn_box.pack_end(self.atn_switch, False, False, 0)
            vbox.pack_start(atn_box, False, False, 0)

            self.autotune_target_row = self.create_slider_row(
                "Autotune Target (0 = Chromatic)", self.cfg.autotune_target_hz,
                0, 1000, " Hz", self.on_autotune_target_changed,
            )
            self.autotune_target_row._formatter = lambda v: "Chromatic" if v == 0 else f"{v} Hz"  # type: ignore[attr-defined]
            if self.cfg.autotune_target_hz == 0:
                self.autotune_target_row._val_lbl.set_text("Chromatic")  # type: ignore[attr-defined]
            vbox.pack_start(self.autotune_target_row, False, False, 0)

            # Bitcrusher (0..15 bits)
            self.bitcrush_row = self.create_slider_row(
                "Lo-Fi Bitcrusher (Quantisation Depth)",
                self.cfg.bitcrush_bits,
                0,
                15,
                " bits",
                self.on_bitcrush_changed,
            )
            self.bitcrush_row._formatter = lambda v: "Quantization off" if v == 0 else f"{v} bits"  # type: ignore[attr-defined]
            if self.cfg.bitcrush_bits == 0:
                self.bitcrush_row._val_lbl.set_text("Quantization off")  # type: ignore[attr-defined]
            vbox.pack_start(self.bitcrush_row, False, False, 0)

            self.bitcrush_hold_row = self.create_slider_row(
                "Sample Hold Factor", self.cfg.bitcrush_downsample,
                1, 64, "×", self.on_bitcrush_hold_changed,
            )
            self.bitcrush_hold_row.set_tooltip_text("1× leaves the sample rate unchanged; higher values hold each sample longer.")
            vbox.pack_start(self.bitcrush_hold_row, False, False, 0)

            # Stutter Chopper Gate
            self.stutter_row = self.create_slider_row(
                "Stutter Chopper Gate (Rhythmic Machine Voice)",
                self.cfg.stutter_hz,
                0,
                20,
                " Hz",
                self.on_stutter_changed,
            )
            vbox.pack_start(self.stutter_row, False, False, 0)

            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled.add(vbox)
            self.add_notebook_tab(scrolled, "applications-multimedia-symbolic", "Voice FX")

        # ---------------------------------------------------------------------
        # Tab 3: Spatial DSP (Microphone & Playback)
        # ---------------------------------------------------------------------
        def build_tab_spatial_dsp(self) -> None:
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            vbox.set_border_width(12)

            # Target Mode Switcher: Input (Mic) vs Output (Playback)
            target_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            target_box.set_halign(Gtk.Align.CENTER)
            target_box.get_style_context().add_class("segmented-group")

            self.btn_spatial_target_mic = Gtk.Button(label="Microphone Spatial FX")
            self.btn_spatial_target_mic.get_style_context().add_class("segmented-btn")
            self.btn_spatial_target_mic.get_style_context().add_class("active-preset")
            self.btn_spatial_target_mic.connect("clicked", lambda _: self.set_spatial_target("mic"))

            self.btn_spatial_target_out = Gtk.Button(label="Playback Spatial FX")
            self.btn_spatial_target_out.get_style_context().add_class("segmented-btn")
            self.btn_spatial_target_out.connect("clicked", lambda _: self.set_spatial_target("out"))

            target_box.pack_start(self.btn_spatial_target_mic, True, True, 0)
            target_box.pack_start(self.btn_spatial_target_out, True, True, 0)
            vbox.pack_start(target_box, False, False, 2)

            # Header + Reset Spatial FX Button
            spat_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.spatial_hdr_lbl = Gtk.Label(label="Spatial FX (Tape Delay & Algorithmic Reverb)", xalign=0)
            self.spatial_hdr_lbl.get_style_context().add_class("section-label")
            btn_reset_spatial = self.create_icon_button(
                "edit-undo-symbolic",
                "Reset Delay & Reverb",
                "Disable and reset delay and reverb for the selected target",
                "reset-btn",
                self.reset_spatial_dsp,
            )
            spat_top.pack_start(self.spatial_hdr_lbl, True, True, 0)
            spat_top.pack_end(btn_reset_spatial, False, False, 0)
            vbox.pack_start(spat_top, False, False, 0)

            # Delay Header
            dly_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.spatial_dly_lbl = Gtk.Label(label="Tape Echo / Delay (Microphone Input)", xalign=0)
            self.spatial_dly_lbl.get_style_context().add_class("section-label")
            self.dly_switch = Gtk.Switch()
            self.dly_switch.get_style_context().add_class("compact-switch")
            self.dly_switch.set_active(self.cfg.delay_on)
            self.dly_switch.connect("notify::active", self.on_delay_toggled)
            dly_hdr.pack_start(self.spatial_dly_lbl, True, True, 0)
            dly_hdr.pack_end(self.dly_switch, False, False, 0)
            vbox.pack_start(dly_hdr, False, False, 0)

            self.dly_time_row = self.create_slider_row("Delay Time", self.cfg.delay_ms, 10, 1000, " ms", self.on_delay_time_changed)
            self.dly_fb_row = self.create_slider_row("Delay Feedback", self.cfg.delay_feedback, 0, 95, "%", self.on_delay_fb_changed)
            self.dly_mix_row = self.create_slider_row("Delay Wet/Dry Mix", self.cfg.delay_mix, 0, 100, "%", self.on_delay_mix_changed)

            vbox.pack_start(self.dly_time_row, False, False, 0)
            vbox.pack_start(self.dly_fb_row, False, False, 0)
            vbox.pack_start(self.dly_mix_row, False, False, 0)

            vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

            # Reverb Header
            rvb_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.spatial_rvb_lbl = Gtk.Label(label="Schroeder Algorithmic Reverb Tank (Microphone Input)", xalign=0)
            self.spatial_rvb_lbl.get_style_context().add_class("section-label")
            self.rvb_switch = Gtk.Switch()
            self.rvb_switch.get_style_context().add_class("compact-switch")
            self.rvb_switch.set_active(self.cfg.reverb_on)
            self.rvb_switch.connect("notify::active", self.on_reverb_toggled)
            rvb_hdr.pack_start(self.spatial_rvb_lbl, True, True, 0)
            rvb_hdr.pack_end(self.rvb_switch, False, False, 0)
            vbox.pack_start(rvb_hdr, False, False, 0)

            self.rvb_room_row = self.create_slider_row("Reverb Room Size", self.cfg.reverb_room, 0, 100, "%", self.on_reverb_room_changed)
            self.rvb_damp_row = self.create_slider_row("Reverb Dampening", self.cfg.reverb_damp, 0, 100, "%", self.on_reverb_damp_changed)
            self.rvb_width_row = self.create_slider_row("Reverb Tail Level", self.cfg.reverb_width, 0, 100, "%", self.on_reverb_width_changed)
            self.rvb_mix_row = self.create_slider_row("Reverb Wet/Dry Mix", self.cfg.reverb_mix, 0, 100, "%", self.on_reverb_mix_changed)

            vbox.pack_start(self.rvb_room_row, False, False, 0)
            vbox.pack_start(self.rvb_damp_row, False, False, 0)
            vbox.pack_start(self.rvb_width_row, False, False, 0)
            vbox.pack_start(self.rvb_mix_row, False, False, 0)

            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled.add(vbox)
            self.add_notebook_tab(scrolled, "audio-speakers-symbolic", "Delay & Reverb")

        # ---------------------------------------------------------------------
        # Tab 4: 9-Band Studio EQ (Microphone & Playback)
        # ---------------------------------------------------------------------
        def build_tab_equalizer(self) -> None:
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            vbox.set_border_width(12)

            # Target Mode Switcher: Input (Mic) vs Output (Playback)
            target_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            target_box.set_halign(Gtk.Align.CENTER)
            target_box.get_style_context().add_class("segmented-group")

            self.btn_eq_target_mic = Gtk.Button(label="Microphone EQ")
            self.btn_eq_target_mic.get_style_context().add_class("segmented-btn")
            self.btn_eq_target_mic.get_style_context().add_class("active-preset")
            self.btn_eq_target_mic.connect("clicked", lambda _: self.set_eq_target("mic"))

            self.btn_eq_target_out = Gtk.Button(label="Playback EQ")
            self.btn_eq_target_out.get_style_context().add_class("segmented-btn")
            self.btn_eq_target_out.connect("clicked", lambda _: self.set_eq_target("out"))

            target_box.pack_start(self.btn_eq_target_mic, True, True, 0)
            target_box.pack_start(self.btn_eq_target_out, True, True, 0)
            vbox.pack_start(target_box, False, False, 2)

            # EQ Header (Label, Reset Button, Master Toggle)
            eq_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.eq_lbl = Gtk.Label(label="9-Band Studio Parametric EQ (Microphone Input)", xalign=0)
            self.eq_lbl.get_style_context().add_class("section-label")
            self.eq_switch = Gtk.Switch()
            self.eq_switch.get_style_context().add_class("compact-switch")
            self.eq_switch.set_active(self.cfg.eq_on)
            self.eq_switch.connect("notify::active", self.on_eq_toggled)

            btn_reset_eq = self.create_icon_button(
                "edit-undo-symbolic",
                "Reset EQ (Flat 0 dB)",
                "Set the selected target's EQ bands and post gain to 0 dB",
                "reset-btn",
                self.reset_eq_flat,
            )

            eq_hdr.pack_start(self.eq_lbl, True, True, 0)
            eq_hdr.pack_end(btn_reset_eq, False, False, 0)
            eq_hdr.pack_end(self.eq_switch, False, False, 0)
            vbox.pack_start(eq_hdr, False, False, 0)

            # Presets Chips Row
            self.eq_presets_box = Gtk.FlowBox()
            self.eq_presets_box.set_selection_mode(Gtk.SelectionMode.NONE)
            self.eq_presets_box.set_max_children_per_line(6)
            self.eq_presets_box.set_row_spacing(4)
            self.eq_presets_box.set_column_spacing(6)
            self._populate_eq_presets_chips()
            vbox.pack_start(self.eq_presets_box, False, False, 2)

            # Uniform Post-EQ Line Translation Gain
            self.eq_post_row = self.create_slider_row(
                "Uniform Post-EQ Gain Offset",
                int(self.cfg.eq_post_gain / 100),
                -36,
                36,
                " dB",
                self.on_eq_post_gain_changed,
            )
            self.eq_post_row.set_tooltip_text("Large boosts may exceed digital full scale and clip downstream.")
            vbox.pack_start(self.eq_post_row, False, False, 0)

            vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)

            self.eq_band_rows = []
            for idx, (name, _, _, _) in enumerate(EQ_BANDS):
                val = self.cfg.eq_gains[idx] / 100 if idx < len(self.cfg.eq_gains) else 0
                row = self.create_slider_row(name, int(val), -12, 12, " dB", lambda s, i=idx: self.on_eq_band_changed(i, s))
                self.eq_band_rows.append(row)
                vbox.pack_start(row, False, False, 0)

            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled.add(vbox)
            self.add_notebook_tab(scrolled, "multimedia-volume-control-symbolic", "9-Band EQ")

        # ---------------------------------------------------------------------
        # Helper: Create Slider Row
        # ---------------------------------------------------------------------
        def create_slider_row(
            self,
            title: str,
            val: int,
            min_v: int,
            max_v: int,
            unit: str,
            callback: Any,
        ) -> Gtk.Box:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label=title, xalign=0)
            lbl.get_style_context().add_class("section-label")
            val_lbl = Gtk.Label(label=f"{val:+d}{unit}" if min_v < 0 else f"{val}{unit}", xalign=1)
            val_lbl.get_style_context().add_class("value-label")
            hdr.pack_start(lbl, True, True, 0)
            hdr.pack_end(val_lbl, False, False, 0)
            box.pack_start(hdr, False, False, 0)

            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, min_v, max_v, 1)
            scale.set_draw_value(False)
            scale.set_value(val)

            box._unit = unit  # type: ignore[attr-defined]
            box._signed = min_v < 0  # type: ignore[attr-defined]

            def on_val(s: Gtk.Scale) -> None:
                v = int(s.get_value())
                formatter = getattr(box, "_formatter", None)
                val_lbl.set_text(formatter(v) if formatter else
                                 (f"{v:+d}{box._unit}" if box._signed else f"{v}{box._unit}"))
                if not self._updating_ui:
                    callback(s)

            def on_scroll(w: Gtk.Widget, event: Gdk.EventScroll) -> bool:
                # Forward mouse wheel scroll event to parent ScrolledWindow so it scrolls the page
                # rather than accidentally adjusting the slider.
                parent = w.get_parent()
                while parent:
                    if isinstance(parent, Gtk.ScrolledWindow):
                        adj = parent.get_vadjustment()
                        if adj:
                            step = adj.get_step_increment() or 20.0
                            if event.direction == Gdk.ScrollDirection.UP:
                                adj.set_value(max(adj.get_lower(), adj.get_value() - step * 2))
                            elif event.direction == Gdk.ScrollDirection.DOWN:
                                adj.set_value(min(adj.get_upper() - adj.get_page_size(), adj.get_value() + step * 2))
                            elif event.direction == Gdk.ScrollDirection.SMOOTH:
                                _, _, dy = event.get_scroll_deltas()
                                adj.set_value(max(adj.get_lower(), min(adj.get_upper() - adj.get_page_size(), adj.get_value() + dy * step * 2)))
                        return True
                    parent = parent.get_parent()
                return True

            scale.connect("scroll-event", on_scroll)
            scale.connect("value-changed", on_val)
            box.pack_start(scale, False, False, 0)
            box._scale = scale  # type: ignore
            box._val_lbl = val_lbl  # type: ignore
            box._title_lbl = lbl  # type: ignore
            return box

        # ---------------------------------------------------------------------
        # Real-Time Telemetry Polling
        # ---------------------------------------------------------------------
        def poll_telemetry(self) -> bool:
            now = time.monotonic()
            if now - self._last_status_check >= 1.0:
                self._last_status_check = now
                self.update_status_label()
            tele = self._latest_telemetry
            if tele:
                # VAD %
                vad_pct = int(tele.vad_prob * 100)
                self.vad_bar.set_fraction(max(0.0, min(1.0, tele.vad_prob)))
                self.vad_val_lbl.set_text(f"{vad_pct}%")

                # Level of the signal changed by RNNoise, in dBFS.
                red_frac = max(0.0, min(1.0, (tele.processing_delta_dbfs + 80.0) / 80.0))
                self.red_bar.set_fraction(red_frac)
                self.red_val_lbl.set_text(f"{tele.processing_delta_dbfs:.1f} dBFS" if red_frac else "-∞ dBFS")

                # Input RMS (-80..0 dBFS)
                in_frac = max(0.0, min(1.0, (tele.rms_in_db + 80.0) / 80.0))
                self.in_bar.set_fraction(in_frac)
                self.in_val_lbl.set_text(f"{tele.rms_in_db:.1f} dB" if tele.rms_in_db > -79.0 else "-inf dB")

                # Output RMS (-80..0 dBFS)
                out_frac = max(0.0, min(1.0, (tele.rms_out_db + 80.0) / 80.0))
                self.out_bar.set_fraction(out_frac)
                self.out_val_lbl.set_text(f"{tele.rms_out_db:.1f} dB" if tele.rms_out_db > -79.0 else "-inf dB")

                # Tracked Pitch Hz
                if self.current_voice_target == "out":
                    self.lbl_pitch_track.set_text("Playback pitch: unavailable")
                elif tele.tracked_pitch_hz > 1.0:
                    self.lbl_pitch_track.set_text(f"Tracked: ~{tele.tracked_pitch_hz:.0f} Hz")
                else:
                    self.lbl_pitch_track.set_text("Tracked: —")
            else:
                self.vad_bar.set_fraction(0.0)
                self.red_bar.set_fraction(0.0)
                self.red_val_lbl.set_text("-∞ dBFS")
                self.in_bar.set_fraction(0.0)
                self.out_bar.set_fraction(0.0)
                self.vad_val_lbl.set_text("—")
                self.in_val_lbl.set_text("—")
                self.out_val_lbl.set_text("—")
                self.lbl_pitch_track.set_text("Tracked: —")

            return True

        # ---------------------------------------------------------------------
        # Event Handlers & State Synchronization
        # ---------------------------------------------------------------------
        def update_status_label(self) -> None:
            running = bool(get_daemon_pid() and SOCK_PATH.exists())
            if self.cfg.enabled and running and len(self.sources) > 1 and len(self.sinks) > 1:
                self.status_lbl.set_text("Active (PipeWire RT Low-Latency DSP ON)")
                self.status_lbl.get_style_context().remove_class("header-subtitle-inactive")
                self.status_lbl.get_style_context().add_class("header-subtitle-active")
            elif self.cfg.enabled and running:
                self.status_lbl.set_text("Running; physical microphone or output unavailable")
                self.status_lbl.get_style_context().remove_class("header-subtitle-active")
                self.status_lbl.get_style_context().add_class("header-subtitle-inactive")
            elif self.cfg.enabled:
                self.status_lbl.set_text("Engine stopped — check audio devices or engine log")
                self.status_lbl.get_style_context().remove_class("header-subtitle-active")
                self.status_lbl.get_style_context().add_class("header-subtitle-inactive")
            else:
                self.status_lbl.set_text("Disabled (Direct Hardware Bypass)")
                self.status_lbl.get_style_context().remove_class("header-subtitle-active")
                self.status_lbl.get_style_context().add_class("header-subtitle-inactive")

        def on_master_toggled(self, switch: Gtk.Switch, _gparam: Any) -> None:
            if self._updating_ui:
                return
            active = switch.get_active()
            self.cfg.enabled = active
            if self._save_timer:
                GLib.source_remove(self._save_timer)
                self.flush_config()
            else:
                save_config(self.cfg)
            self.update_status_label()

            if active:
                self._queue_engine_state(True)
            else:
                self._queue_engine_state(False)

        def on_source_changed(self, combo: Gtk.ComboBoxText) -> None:
            if self._updating_ui:
                return
            node = combo.get_active_id()
            if node:
                self.cfg.source = node
                save_config(self.cfg)
                self._queue_config_sync()

        def on_sink_changed(self, combo: Gtk.ComboBoxText) -> None:
            if self._updating_ui:
                return
            node = combo.get_active_id()
            if node:
                self.cfg.sink = node
                save_config(self.cfg)
                self._queue_config_sync()

        def on_volume_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value())
            self.cfg.volume = val
            self.persist_config()
            self._queue_command(f"VOL {val * 10}")

        def on_rnnoise_toggled(self, switch: Gtk.Switch, _g: Any) -> None:
            if self._updating_ui:
                return
            active = switch.get_active()
            self.cfg.rnnoise_on = active
            save_config(self.cfg)
            self._queue_command(f"RNN {1 if (self.cfg.enabled and active) else 0}")

        def on_agg_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value())
            self.cfg.aggressiveness = val
            self.persist_config()
            self._queue_command(f"AGG {val * 10}")

        def on_out_rnnoise_toggled(self, switch: Gtk.Switch, _g: Any) -> None:
            if self._updating_ui:
                return
            active = switch.get_active()
            self.cfg.out_rnnoise_on = active
            save_config(self.cfg)
            self._queue_command(f"OUT_NOISE {1 if active else 0}")

        def on_out_agg_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value())
            self.cfg.out_aggressiveness = val
            self.persist_config()
            self._queue_command(f"OUT_AGG {val * 10}")

        def set_voice_target(self, target: str) -> None:
            if self.current_voice_target == target:
                return
            self.current_voice_target = target
            if target == "mic":
                self.btn_voice_target_mic.get_style_context().add_class("active-preset")
                self.btn_voice_target_out.get_style_context().remove_class("active-preset")
                self.voice_hdr_lbl.set_text("Voice FX Character Presets (Microphone Input)")
            else:
                self.btn_voice_target_out.get_style_context().add_class("active-preset")
                self.btn_voice_target_mic.get_style_context().remove_class("active-preset")
                self.voice_hdr_lbl.set_text("Voice FX Character Presets (Playback Output)")
            self._refresh_voice_ui()

        def set_spatial_target(self, target: str) -> None:
            if self.current_spatial_target == target:
                return
            self.current_spatial_target = target
            if target == "mic":
                self.btn_spatial_target_mic.get_style_context().add_class("active-preset")
                self.btn_spatial_target_out.get_style_context().remove_class("active-preset")
                self.spatial_dly_lbl.set_text("Tape Echo / Delay (Microphone Input)")
                self.spatial_rvb_lbl.set_text("Schroeder Reverb (Microphone Input)")
            else:
                self.btn_spatial_target_out.get_style_context().add_class("active-preset")
                self.btn_spatial_target_mic.get_style_context().remove_class("active-preset")
                self.spatial_dly_lbl.set_text("Stereo Tape Echo / Delay (Playback Output)")
                self.spatial_rvb_lbl.set_text("Stereo Schroeder Reverb (Playback Output)")
            self._refresh_spatial_ui()

        def on_pitch_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value())
            if self.current_voice_target == "mic":
                self.cfg.pitch_shift = val * 100
                self._queue_command(f"PSH {self.cfg.pitch_shift if self.cfg.enabled else 0}")
            else:
                self.cfg.out_pitch_shift = val * 100
                self._queue_command(f"OUT_PSH {self.cfg.out_pitch_shift if self.cfg.enabled else 0}")
            self.persist_config()
            self._clear_active_preset_highlight()

        def on_vocoder_toggled(self, switch: Gtk.Switch, _g: Any) -> None:
            if self._updating_ui:
                return
            active = switch.get_active()
            if self.current_voice_target == "mic":
                self.cfg.vocoder_on = active
                self._queue_command(f"VOC {1 if (self.cfg.enabled and active) else 0}")
            else:
                self.cfg.out_vocoder_on = active
                self._queue_command(f"OUT_VOC {1 if (self.cfg.enabled and active) else 0}")
            save_config(self.cfg)
            self._clear_active_preset_highlight()

        def on_follow_toggled(self, check: Gtk.CheckButton) -> None:
            if self._updating_ui:
                return
            active = check.get_active()
            is_mic = (self.current_voice_target == "mic")
            if is_mic:
                self.cfg.vocoder_follow = active
                c_shift = self.cfg.vocoder_pitch_shift
                c_hz = self.cfg.vocoder_carrier_hz
                mix = self.cfg.vocoder_mix
                atk = self.cfg.vocoder_attack_ms
                rel = self.cfg.vocoder_release_ms
                det = self.cfg.vocoder_detune
            else:
                self.cfg.out_vocoder_follow = active
                c_shift = self.cfg.out_vocoder_pitch_shift
                c_hz = self.cfg.out_vocoder_carrier_hz
                mix = self.cfg.out_vocoder_mix
                atk = self.cfg.out_vocoder_attack_ms
                rel = self.cfg.out_vocoder_release_ms
                det = self.cfg.out_vocoder_detune

            save_config(self.cfg)
            self._clear_active_preset_highlight()

            previous_updating = self._updating_ui
            self._updating_ui = True
            if active:
                self.carrier_row._unit = " st"  # type: ignore[attr-defined]
                self.carrier_row._signed = True  # type: ignore[attr-defined]
                self.carrier_row._scale.set_range(-24, 24)  # type: ignore
                self.carrier_row._scale.set_value(c_shift)  # type: ignore
                self.carrier_row._title_lbl.set_text("Carrier Pitch Transposition")  # type: ignore
                self.carrier_row._val_lbl.set_text(f"{c_shift:+d} st")  # type: ignore
            else:
                self.carrier_row._unit = " Hz"  # type: ignore[attr-defined]
                self.carrier_row._signed = False  # type: ignore[attr-defined]
                self.carrier_row._scale.set_range(50, 880)  # type: ignore
                self.carrier_row._scale.set_value(c_hz)  # type: ignore
                self.carrier_row._title_lbl.set_text("Carrier Frequency")  # type: ignore
                self.carrier_row._val_lbl.set_text(f"{c_hz} Hz")  # type: ignore
            self._updating_ui = previous_updating

            prefix = "VOP" if is_mic else "OUT_VOP"
            self._queue_command(f"{prefix} {mix * 10} {c_hz} {atk} {rel} {det} {1 if active else 0} {c_shift}")

        def on_carrier_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value())
            is_mic = (self.current_voice_target == "mic")
            if is_mic:
                if self.cfg.vocoder_follow:
                    self.cfg.vocoder_pitch_shift = val
                else:
                    self.cfg.vocoder_carrier_hz = val
                c_shift = self.cfg.vocoder_pitch_shift
                c_hz = self.cfg.vocoder_carrier_hz
                follow = self.cfg.vocoder_follow
                mix = self.cfg.vocoder_mix
                atk = self.cfg.vocoder_attack_ms
                rel = self.cfg.vocoder_release_ms
                det = self.cfg.vocoder_detune
            else:
                if self.cfg.out_vocoder_follow:
                    self.cfg.out_vocoder_pitch_shift = val
                else:
                    self.cfg.out_vocoder_carrier_hz = val
                c_shift = self.cfg.out_vocoder_pitch_shift
                c_hz = self.cfg.out_vocoder_carrier_hz
                follow = self.cfg.out_vocoder_follow
                mix = self.cfg.out_vocoder_mix
                atk = self.cfg.out_vocoder_attack_ms
                rel = self.cfg.out_vocoder_release_ms
                det = self.cfg.out_vocoder_detune

            self.persist_config()
            self._clear_active_preset_highlight()
            prefix = "VOP" if is_mic else "OUT_VOP"
            self._queue_command(f"{prefix} {mix * 10} {c_hz} {atk} {rel} {det} {1 if follow else 0} {c_shift}")

        def on_voc_mix_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value())
            is_mic = (self.current_voice_target == "mic")
            if is_mic:
                self.cfg.vocoder_mix = val
                c_shift = self.cfg.vocoder_pitch_shift
                c_hz = self.cfg.vocoder_carrier_hz
                follow = self.cfg.vocoder_follow
                atk = self.cfg.vocoder_attack_ms
                rel = self.cfg.vocoder_release_ms
                det = self.cfg.vocoder_detune
            else:
                self.cfg.out_vocoder_mix = val
                c_shift = self.cfg.out_vocoder_pitch_shift
                c_hz = self.cfg.out_vocoder_carrier_hz
                follow = self.cfg.out_vocoder_follow
                atk = self.cfg.out_vocoder_attack_ms
                rel = self.cfg.out_vocoder_release_ms
                det = self.cfg.out_vocoder_detune

            self.persist_config()
            self._clear_active_preset_highlight()
            prefix = "VOP" if is_mic else "OUT_VOP"
            self._queue_command(f"{prefix} {val * 10} {c_hz} {atk} {rel} {det} {1 if follow else 0} {c_shift}")

        def on_matrix_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value())
            if self.current_voice_target == "mic":
                self.cfg.vocoder_matrix = val
                self._queue_command(f"MTX {val * 10}")
            else:
                self.cfg.out_vocoder_matrix = val
                self._queue_command(f"OUT_MTX {val * 10}")
            self.persist_config()
            self._clear_active_preset_highlight()

        def on_autotune_toggled(self, switch: Gtk.Switch, _g: Any) -> None:
            if self._updating_ui:
                return
            active = switch.get_active()
            if self.current_voice_target == "mic":
                self.cfg.autotune_on = active
                self._queue_command(f"ATN {1 if (self.cfg.enabled and active) else 0}")
                self._queue_command(f"ATT {self.cfg.autotune_target_hz if self.cfg.enabled else 0}")
            else:
                self.cfg.out_autotune_on = active
                self._queue_command(f"OUT_ATN {1 if (self.cfg.enabled and active) else 0}")
                self._queue_command(f"OUT_ATT {self.cfg.out_autotune_target_hz if self.cfg.enabled else 0}")
            save_config(self.cfg)
            self._clear_active_preset_highlight()

        def on_autotune_target_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            value = int(scale.get_value())
            if self.current_voice_target == "mic":
                self.cfg.autotune_target_hz = value
                self._queue_command(f"ATT {value}")
            else:
                self.cfg.out_autotune_target_hz = value
                self._queue_command(f"OUT_ATT {value}")
            self.persist_config()
            self._clear_active_preset_highlight()

        def on_bitcrush_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value())
            if self.current_voice_target == "mic":
                self.cfg.bitcrush_bits = val
                self._queue_command(f"BCR {val if self.cfg.enabled else 0} {self.cfg.bitcrush_downsample}")
            else:
                self.cfg.out_bitcrush_bits = val
                self._queue_command(f"OUT_BCR {val if self.cfg.enabled else 0} {self.cfg.out_bitcrush_downsample}")
            self.persist_config()
            self._clear_active_preset_highlight()

        def on_bitcrush_hold_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            value = int(scale.get_value())
            if self.current_voice_target == "mic":
                self.cfg.bitcrush_downsample = value
                self._queue_command(f"BCR {self.cfg.bitcrush_bits if self.cfg.enabled else 0} {value}")
            else:
                self.cfg.out_bitcrush_downsample = value
                self._queue_command(f"OUT_BCR {self.cfg.out_bitcrush_bits if self.cfg.enabled else 0} {value}")
            self.persist_config()
            self._clear_active_preset_highlight()

        def on_stutter_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value())
            if self.current_voice_target == "mic":
                self.cfg.stutter_hz = val
                self._queue_command(f"STT {val if self.cfg.enabled else 0} 500")
            else:
                self.cfg.out_stutter_hz = val
                self._queue_command(f"OUT_STT {val if self.cfg.enabled else 0} 500")
            self.persist_config()
            self._clear_active_preset_highlight()

        def on_delay_toggled(self, switch: Gtk.Switch, _g: Any) -> None:
            if self._updating_ui:
                return
            active = switch.get_active()
            if self.current_spatial_target == "mic":
                self.cfg.delay_on = active
                self._queue_command(f"DLY {1 if (self.cfg.enabled and active) else 0}")
            else:
                self.cfg.out_delay_on = active
                self._queue_command(f"OUT_DLY {1 if (self.cfg.enabled and active) else 0}")
            save_config(self.cfg)

        def on_delay_time_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value())
            if self.current_spatial_target == "mic":
                self.cfg.delay_ms = val
                self._queue_command(f"DLP {self.cfg.delay_ms} {self.cfg.delay_feedback * 10} {self.cfg.delay_mix * 10}")
            else:
                self.cfg.out_delay_ms = val
                self._queue_command(f"OUT_DLP {self.cfg.out_delay_ms} {self.cfg.out_delay_feedback * 10} {self.cfg.out_delay_mix * 10}")
            self.persist_config()

        def on_delay_fb_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value())
            if self.current_spatial_target == "mic":
                self.cfg.delay_feedback = val
                self._queue_command(f"DLP {self.cfg.delay_ms} {self.cfg.delay_feedback * 10} {self.cfg.delay_mix * 10}")
            else:
                self.cfg.out_delay_feedback = val
                self._queue_command(f"OUT_DLP {self.cfg.out_delay_ms} {self.cfg.out_delay_feedback * 10} {self.cfg.out_delay_mix * 10}")
            self.persist_config()

        def on_delay_mix_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value())
            if self.current_spatial_target == "mic":
                self.cfg.delay_mix = val
                self._queue_command(f"DLP {self.cfg.delay_ms} {self.cfg.delay_feedback * 10} {self.cfg.delay_mix * 10}")
            else:
                self.cfg.out_delay_mix = val
                self._queue_command(f"OUT_DLP {self.cfg.out_delay_ms} {self.cfg.out_delay_feedback * 10} {self.cfg.out_delay_mix * 10}")
            self.persist_config()

        def on_reverb_toggled(self, switch: Gtk.Switch, _g: Any) -> None:
            if self._updating_ui:
                return
            active = switch.get_active()
            if self.current_spatial_target == "mic":
                self.cfg.reverb_on = active
                self._queue_command(f"RVB {1 if (self.cfg.enabled and active) else 0}")
            else:
                self.cfg.out_reverb_on = active
                self._queue_command(f"OUT_RVB {1 if (self.cfg.enabled and active) else 0}")
            save_config(self.cfg)

        def on_reverb_room_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value())
            if self.current_spatial_target == "mic":
                self.cfg.reverb_room = val
                self._queue_command(f"RVP {self.cfg.reverb_room * 10} {self.cfg.reverb_damp * 10} {self.cfg.reverb_width * 10} {self.cfg.reverb_mix * 10}")
            else:
                self.cfg.out_reverb_room = val
                self._queue_command(f"OUT_RVP {self.cfg.out_reverb_room * 10} {self.cfg.out_reverb_damp * 10} {self.cfg.out_reverb_width * 10} {self.cfg.out_reverb_mix * 10}")
            self.persist_config()

        def on_reverb_damp_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value())
            if self.current_spatial_target == "mic":
                self.cfg.reverb_damp = val
                self._queue_command(f"RVP {self.cfg.reverb_room * 10} {self.cfg.reverb_damp * 10} {self.cfg.reverb_width * 10} {self.cfg.reverb_mix * 10}")
            else:
                self.cfg.out_reverb_damp = val
                self._queue_command(f"OUT_RVP {self.cfg.out_reverb_room * 10} {self.cfg.out_reverb_damp * 10} {self.cfg.out_reverb_width * 10} {self.cfg.out_reverb_mix * 10}")
            self.persist_config()

        def on_reverb_width_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value())
            if self.current_spatial_target == "mic":
                self.cfg.reverb_width = val
                self._queue_command(f"RVP {self.cfg.reverb_room * 10} {self.cfg.reverb_damp * 10} {self.cfg.reverb_width * 10} {self.cfg.reverb_mix * 10}")
            else:
                self.cfg.out_reverb_width = val
                self._queue_command(f"OUT_RVP {self.cfg.out_reverb_room * 10} {self.cfg.out_reverb_damp * 10} {self.cfg.out_reverb_width * 10} {self.cfg.out_reverb_mix * 10}")
            self.persist_config()

        def on_reverb_mix_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value())
            if self.current_spatial_target == "mic":
                self.cfg.reverb_mix = val
                self._queue_command(f"RVP {self.cfg.reverb_room * 10} {self.cfg.reverb_damp * 10} {self.cfg.reverb_width * 10} {self.cfg.reverb_mix * 10}")
            else:
                self.cfg.out_reverb_mix = val
                self._queue_command(f"OUT_RVP {self.cfg.out_reverb_room * 10} {self.cfg.out_reverb_damp * 10} {self.cfg.out_reverb_width * 10} {self.cfg.out_reverb_mix * 10}")
            self.persist_config()

        def set_eq_target(self, target: str) -> None:
            if self.current_eq_target == target:
                return
            self.current_eq_target = target
            if target == "mic":
                self.btn_eq_target_mic.get_style_context().add_class("active-preset")
                self.btn_eq_target_out.get_style_context().remove_class("active-preset")
                self.eq_lbl.set_text("9-Band Studio Parametric EQ (Microphone Input)")
            else:
                self.btn_eq_target_out.get_style_context().add_class("active-preset")
                self.btn_eq_target_mic.get_style_context().remove_class("active-preset")
                self.eq_lbl.set_text("9-Band Stereo Parametric EQ (Playback & Speakers)")
            self._populate_eq_presets_chips()
            self._refresh_eq_ui()

        def _populate_eq_presets_chips(self) -> None:
            for child in self.eq_presets_box.get_children():
                self.eq_presets_box.remove(child)
            self.eq_preset_buttons.clear()

            presets_dict = INPUT_EQ_PRESETS if self.current_eq_target == "mic" else OUTPUT_EQ_PRESETS
            for name, data in presets_dict.items():
                btn = Gtk.Button(label=name)
                btn.get_style_context().add_class("preset-chip")
                btn.connect("clicked", lambda _, n=name, d=data: self.apply_eq_preset(n, d))
                self.eq_presets_box.add(btn)
                self.eq_preset_buttons[name] = btn
            self.eq_presets_box.show_all()

        def apply_eq_preset(self, name: str, data: dict[str, Any]) -> None:
            previous_updating = self._updating_ui
            self._updating_ui = True
            post_gain = data.get("post_gain", 0)
            gains = list(data.get("gains", [0] * 9))

            if self.current_eq_target == "mic":
                self.cfg.eq_post_gain = post_gain
                self.cfg.eq_gains = gains
                self._queue_command(f"EGN {post_gain}")
                for idx, g in enumerate(gains):
                    _, kind, hz, q = EQ_BANDS[idx]
                    self._queue_command(f"EQB {idx} {kind} {hz} {q} {g}")
            else:
                self.cfg.out_eq_post_gain = post_gain
                self.cfg.out_eq_gains = gains
                self._queue_command(f"OUT_EGN {post_gain}")
                for idx, g in enumerate(gains):
                    _, kind, hz, q = EQ_BANDS[idx]
                    self._queue_command(f"OUT_EQB {idx} {kind} {hz} {q} {g}")
            save_config(self.cfg)
            self._refresh_eq_ui()
            self._updating_ui = previous_updating
            for btn_name, btn in self.eq_preset_buttons.items():
                if btn_name == name:
                    btn.get_style_context().add_class("active-preset")
                else:
                    btn.get_style_context().remove_class("active-preset")

        def _clear_active_eq_preset_highlight(self) -> None:
            for btn in self.eq_preset_buttons.values():
                btn.get_style_context().remove_class("active-preset")

        def on_eq_toggled(self, switch: Gtk.Switch, _g: Any) -> None:
            if self._updating_ui:
                return
            active = switch.get_active()
            if self.current_eq_target == "mic":
                self.cfg.eq_on = active
                self._queue_command(f"EQ {1 if (self.cfg.enabled and active) else 0}")
            else:
                self.cfg.out_eq_on = active
                self._queue_command(f"OUT_EQ {1 if (self.cfg.enabled and active) else 0}")
            save_config(self.cfg)

        def on_eq_post_gain_changed(self, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value()) * 100
            if self.current_eq_target == "mic":
                self.cfg.eq_post_gain = val
                self._queue_command(f"EGN {val}")
            else:
                self.cfg.out_eq_post_gain = val
                self._queue_command(f"OUT_EGN {val}")
            self.persist_config()
            self._clear_active_eq_preset_highlight()

        def on_eq_band_changed(self, idx: int, scale: Gtk.Scale) -> None:
            if self._updating_ui:
                return
            val = int(scale.get_value()) * 100
            _, kind, hz, q = EQ_BANDS[idx]

            if self.current_eq_target == "mic":
                if idx < len(self.cfg.eq_gains):
                    self.cfg.eq_gains[idx] = val
                    self.persist_config()
                    self._queue_command(f"EQB {idx} {kind} {hz} {q} {val}")
            else:
                if idx < len(self.cfg.out_eq_gains):
                    self.cfg.out_eq_gains[idx] = val
                    self.persist_config()
                    self._queue_command(f"OUT_EQB {idx} {kind} {hz} {q} {val}")
            self._clear_active_eq_preset_highlight()

        def on_monitor_toggled(self, check: Gtk.CheckButton) -> None:
            if self._updating_ui:
                return
            active = check.get_active()
            self.cfg.monitor = active
            save_config(self.cfg)
            self._queue_command(f"MON {1 if active else 0}")

        # ---------------------------------------------------------------------
        # Reset Routines
        # ---------------------------------------------------------------------
        def reset_all_defaults(self, *_: Any) -> None:
            previous_updating = self._updating_ui
            self._updating_ui = True
            defaults = AudioConfig(enabled=self.cfg.enabled,
                                   source=self.cfg.source, sink=self.cfg.sink,
                                   pre_source=self.cfg.pre_source,
                                   pre_sink=self.cfg.pre_sink,
                                   pre_configured_source=self.cfg.pre_configured_source,
                                   pre_configured_sink=self.cfg.pre_configured_sink)
            for entry in fields(defaults):
                setattr(self.cfg, entry.name, getattr(defaults, entry.name))
            save_config(self.cfg)
            self._queue_config_sync()
            self._refresh_all_ui()
            self._updating_ui = previous_updating
            send_desktop_notification("Dusky Audio Studio", "All audio processing reset to clean factory defaults.")

        def reset_voice_fx(self, *_: Any) -> None:
            self.apply_preset_by_name("Natural Clean")

        def reset_spatial_dsp(self, *_: Any) -> None:
            previous_updating = self._updating_ui
            self._updating_ui = True
            if self.current_spatial_target == "mic":
                self.cfg.delay_on = False
                self.cfg.delay_ms = 250
                self.cfg.delay_feedback = 35
                self.cfg.delay_mix = 30
                self.cfg.reverb_on = False
                self.cfg.reverb_room = 70
                self.cfg.reverb_damp = 50
                self.cfg.reverb_width = 80
                self.cfg.reverb_mix = 35
            else:
                self.cfg.out_delay_on = False
                self.cfg.out_delay_ms = 250
                self.cfg.out_delay_feedback = 35
                self.cfg.out_delay_mix = 30
                self.cfg.out_reverb_on = False
                self.cfg.out_reverb_room = 70
                self.cfg.out_reverb_damp = 50
                self.cfg.out_reverb_width = 80
                self.cfg.out_reverb_mix = 35
            save_config(self.cfg)
            self._queue_config_sync()
            self._refresh_spatial_ui()
            self._updating_ui = previous_updating

        def reset_eq_flat(self, *_: Any) -> None:
            previous_updating = self._updating_ui
            self._updating_ui = True
            if self.current_eq_target == "mic":
                self.cfg.eq_on = False
                self.cfg.eq_post_gain = 0
                self.cfg.eq_gains = [0, 0, 0, 0, 0, 0, 0, 0, 0]
            else:
                self.cfg.out_eq_on = False
                self.cfg.out_eq_post_gain = 0
                self.cfg.out_eq_gains = [0, 0, 0, 0, 0, 0, 0, 0, 0]
            save_config(self.cfg)
            self._queue_config_sync()
            self._refresh_eq_ui()
            self._clear_active_eq_preset_highlight()
            self._updating_ui = previous_updating

        def _refresh_all_ui(self) -> None:
            previous_updating = self._updating_ui
            self._updating_ui = True
            try:
                self.vol_row._scale.set_value(self.cfg.volume)  # type: ignore
                self.rnn_switch.set_active(self.cfg.rnnoise_on)
                self.agg_row._scale.set_value(self.cfg.aggressiveness)  # type: ignore
                self.out_rnn_switch.set_active(self.cfg.out_rnnoise_on)
                self.out_agg_row._scale.set_value(self.cfg.out_aggressiveness)  # type: ignore
                self._set_device_combo(self.src_combo, self.sources, self.cfg.source)
                self._set_device_combo(self.sink_combo, self.sinks, self.cfg.sink)
                self.mon_btn.set_active(self.cfg.monitor)
                self._refresh_voice_ui()
                self._refresh_spatial_ui()
                self._refresh_eq_ui()
            finally:
                self._updating_ui = previous_updating

        def _refresh_voice_ui(self) -> None:
            previous_updating = self._updating_ui
            self._updating_ui = True
            try:
                is_mic = (self.current_voice_target == "mic")
                voc_on = self.cfg.vocoder_on if is_mic else self.cfg.out_vocoder_on
                atn_on = self.cfg.autotune_on if is_mic else self.cfg.out_autotune_on
                atn_target = self.cfg.autotune_target_hz if is_mic else self.cfg.out_autotune_target_hz
                follow = self.cfg.vocoder_follow if is_mic else self.cfg.out_vocoder_follow
                pshift = self.cfg.pitch_shift if is_mic else self.cfg.out_pitch_shift
                c_shift = self.cfg.vocoder_pitch_shift if is_mic else self.cfg.out_vocoder_pitch_shift
                c_hz = self.cfg.vocoder_carrier_hz if is_mic else self.cfg.out_vocoder_carrier_hz
                matrix = self.cfg.vocoder_matrix if is_mic else self.cfg.out_vocoder_matrix
                mix = self.cfg.vocoder_mix if is_mic else self.cfg.out_vocoder_mix
                bc_bits = self.cfg.bitcrush_bits if is_mic else self.cfg.out_bitcrush_bits
                bc_hold = self.cfg.bitcrush_downsample if is_mic else self.cfg.out_bitcrush_downsample
                st_hz = self.cfg.stutter_hz if is_mic else self.cfg.out_stutter_hz

                self.voc_switch.set_active(voc_on)
                self.atn_switch.set_active(atn_on)
                self.autotune_target_row._scale.set_value(atn_target)  # type: ignore[attr-defined]
                self.check_follow.set_active(follow)
                if follow:
                    self.carrier_row._unit = " st"  # type: ignore[attr-defined]
                    self.carrier_row._signed = True  # type: ignore[attr-defined]
                    self.carrier_row._scale.set_range(-24, 24)  # type: ignore
                    self.carrier_row._scale.set_value(c_shift)  # type: ignore
                    self.carrier_row._title_lbl.set_text("Carrier Pitch Transposition")  # type: ignore
                    self.carrier_row._val_lbl.set_text(f"{c_shift:+d} st")  # type: ignore
                else:
                    self.carrier_row._unit = " Hz"  # type: ignore[attr-defined]
                    self.carrier_row._signed = False  # type: ignore[attr-defined]
                    self.carrier_row._scale.set_range(50, 880)  # type: ignore
                    self.carrier_row._scale.set_value(c_hz)  # type: ignore
                    self.carrier_row._title_lbl.set_text("Carrier Frequency")  # type: ignore
                    self.carrier_row._val_lbl.set_text(f"{c_hz} Hz")  # type: ignore
                self.pitch_row._scale.set_value(int(pshift / 100))  # type: ignore
                self.matrix_row._scale.set_value(matrix)  # type: ignore
                self.voc_mix_row._scale.set_value(mix)  # type: ignore
                self.bitcrush_row._scale.set_value(bc_bits)  # type: ignore
                self.bitcrush_hold_row._scale.set_value(bc_hold)  # type: ignore[attr-defined]
                self.stutter_row._scale.set_value(st_hz)  # type: ignore
                self._clear_active_preset_highlight()

                for p_name, p_data in PRESETS.items():
                    match = True
                    for k, v in p_data.items():
                        val = getattr(self.cfg, k if is_mic else f"out_{k}", None)
                        if val != v:
                            match = False
                            break
                    if match:
                        if p_name in self.preset_buttons:
                            self.preset_buttons[p_name].get_style_context().add_class("active-preset")
                        break
            finally:
                self._updating_ui = previous_updating

        def _refresh_spatial_ui(self) -> None:
            previous_updating = self._updating_ui
            self._updating_ui = True
            try:
                is_mic = (self.current_spatial_target == "mic")
                d_on = self.cfg.delay_on if is_mic else self.cfg.out_delay_on
                d_ms = self.cfg.delay_ms if is_mic else self.cfg.out_delay_ms
                d_fb = self.cfg.delay_feedback if is_mic else self.cfg.out_delay_feedback
                d_mix = self.cfg.delay_mix if is_mic else self.cfg.out_delay_mix

                r_on = self.cfg.reverb_on if is_mic else self.cfg.out_reverb_on
                r_room = self.cfg.reverb_room if is_mic else self.cfg.out_reverb_room
                r_damp = self.cfg.reverb_damp if is_mic else self.cfg.out_reverb_damp
                r_width = self.cfg.reverb_width if is_mic else self.cfg.out_reverb_width
                r_mix = self.cfg.reverb_mix if is_mic else self.cfg.out_reverb_mix

                self.dly_switch.set_active(d_on)
                self.dly_time_row._scale.set_value(d_ms)  # type: ignore
                self.dly_fb_row._scale.set_value(d_fb)  # type: ignore
                self.dly_mix_row._scale.set_value(d_mix)  # type: ignore

                self.rvb_switch.set_active(r_on)
                self.rvb_room_row._scale.set_value(r_room)  # type: ignore
                self.rvb_damp_row._scale.set_value(r_damp)  # type: ignore
                self.rvb_width_row._scale.set_value(r_width)  # type: ignore
                self.rvb_mix_row._scale.set_value(r_mix)  # type: ignore
            finally:
                self._updating_ui = previous_updating

        def _refresh_eq_ui(self) -> None:
            previous_updating = self._updating_ui
            self._updating_ui = True
            try:
                if self.current_eq_target == "mic":
                    self.eq_lbl.set_text("9-Band Studio Parametric EQ (Microphone Input)")
                    self.eq_switch.set_active(self.cfg.eq_on)
                    self.eq_post_row._scale.set_value(int(self.cfg.eq_post_gain / 100))  # type: ignore
                    for idx, row in enumerate(self.eq_band_rows):
                        val = int(self.cfg.eq_gains[idx] / 100) if idx < len(self.cfg.eq_gains) else 0
                        row._scale.set_value(val)  # type: ignore
                else:
                    self.eq_lbl.set_text("9-Band Stereo Parametric EQ (Playback & Speakers)")
                    self.eq_switch.set_active(self.cfg.out_eq_on)
                    self.eq_post_row._scale.set_value(int(self.cfg.out_eq_post_gain / 100))  # type: ignore
                    for idx, row in enumerate(self.eq_band_rows):
                        val = int(self.cfg.out_eq_gains[idx] / 100) if idx < len(self.cfg.out_eq_gains) else 0
                        row._scale.set_value(val)  # type: ignore
            finally:
                self._updating_ui = previous_updating

        def _clear_active_preset_highlight(self) -> None:
            for btn in self.preset_buttons.values():
                btn.get_style_context().remove_class("active-preset")

        def apply_preset_by_name(self, name: str) -> None:
            if name not in PRESETS:
                return
            previous = self._updating_ui
            self._updating_ui = True
            try:
                apply_voice_preset(self.cfg, name, self.current_voice_target)
                save_config(self.cfg)
                self._queue_config_sync()
                self._refresh_voice_ui()
                self._clear_active_preset_highlight()
                self.preset_buttons[name].get_style_context().add_class("active-preset")
            finally:
                self._updating_ui = previous

    win = AudioStudioWindow()

    def on_destroy(*_: Any) -> None:
        win._closed = True
        win._telemetry_stop.set()
        if win._save_timer:
            GLib.source_remove(win._save_timer)
            win.flush_config()
        if get_daemon_pid() or win._engine_target:
            win._queue_config_sync()
        win._executor.shutdown(wait=False)
        GUI_PID_FILE.unlink(missing_ok=True)
        Gtk.main_quit()

    win.connect("destroy", on_destroy)
    win.show_all()
    Gtk.main()
    GUI_PID_FILE.unlink(missing_ok=True)


# -----------------------------------------------------------------------------
#   CLI Parser
# -----------------------------------------------------------------------------
def main() -> None:
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--serve":
        server = AudioDspServer(Path(args[1]))

        def request_stop(_signal: int, _frame: Any) -> None:
            # Unwind startup as well as the serving loop through their cleanup.
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        try:
            if not server.start():
                raise SystemExit(1)
            server.serve_forever()
        finally:
            server.stop()
        return
    cfg = load_config()

    if not args or args[0] in ("--gui", "-g", "--gui-only"):
        run_gtk_app(open_only=bool(args and args[0] == "--gui-only"))
        return

    match args[0].lower():
        case "--autostart":
            if cfg.enabled:
                if start_daemon(cfg):
                    print("Dusky Audio DSP autostarted from persisted state (ON).")
                else:
                    raise SystemExit(1)
            else:
                print("Dusky Audio DSP persisted state is OFF (skipping autostart).")
        case "--on" | "-1" | "on":
            if not start_daemon(cfg):
                raise SystemExit(1)
            send_desktop_notification("Dusky Audio Studio", "Voice DSP & Noise Cancellation turned ON.")
            print("Dusky Audio DSP turned ON (PipeWire RT Low-Latency).")
        case "--off" | "-0" | "off":
            cfg.enabled = False
            save_config(cfg)
            stop_daemon(restore_defaults=True, cfg=cfg)
            send_desktop_notification("Dusky Audio Studio", "Voice DSP turned OFF (Direct Hardware Bypass).")
            print("Dusky Audio DSP turned OFF (Direct Hardware Bypass).")
        case "--toggle" | "-t" | "toggle":
            is_on = bool(get_daemon_pid())
            if is_on:
                cfg.enabled = False
                save_config(cfg)
                stop_daemon(restore_defaults=True, cfg=cfg)
                send_desktop_notification("Dusky Audio Studio", "Voice DSP turned OFF (Direct Hardware Bypass).")
                print("Dusky Audio DSP turned OFF.")
            else:
                if not start_daemon(cfg):
                    raise SystemExit(1)
                send_desktop_notification("Dusky Audio Studio", "Voice DSP & Noise Cancellation turned ON.")
                print("Dusky Audio DSP turned ON.")
        case "--reset" | "--reset-all" | "-r":
            cfg = AudioConfig(enabled=cfg.enabled, source=cfg.source, sink=cfg.sink,
                              pre_source=cfg.pre_source, pre_sink=cfg.pre_sink,
                              pre_configured_source=cfg.pre_configured_source,
                              pre_configured_sink=cfg.pre_configured_sink)
            save_config(cfg)
            sync_config_to_daemon(cfg)
            print("All audio DSP settings reset to factory defaults.")
        case "--reset-voice":
            apply_voice_preset(cfg, "Natural Clean", "mic")
            apply_voice_preset(cfg, "Natural Clean", "out")
            save_config(cfg)
            sync_config_to_daemon(cfg)
            print("Voice FX reset to Natural Clean.")
        case "--reset-eq":
            cfg.eq_on = False
            cfg.eq_post_gain = 0
            cfg.eq_gains = [0, 0, 0, 0, 0, 0, 0, 0, 0]
            cfg.out_eq_on = False
            cfg.out_eq_post_gain = 0
            cfg.out_eq_gains = [0, 0, 0, 0, 0, 0, 0, 0, 0]
            save_config(cfg)
            sync_config_to_daemon(cfg)
            print("Microphone & Playback Parametric EQ reset to Flat 0 dB.")
        case "--out-eq" if len(args) > 1:
            action = args[1].lower()
            if action == "on":
                cfg.out_eq_on = True
                save_config(cfg)
                send_daemon_cmd("OUT_EQ 1")
                print("Playback Parametric EQ: ON")
            elif action == "off":
                cfg.out_eq_on = False
                save_config(cfg)
                send_daemon_cmd("OUT_EQ 0")
                print("Playback Parametric EQ: OFF")
            elif action == "toggle":
                cfg.out_eq_on = not cfg.out_eq_on
                save_config(cfg)
                send_daemon_cmd(f"OUT_EQ {1 if cfg.out_eq_on else 0}")
                print(f"Playback Parametric EQ: {'ON' if cfg.out_eq_on else 'OFF'}")
            else:
                print("Usage: --out-eq <on|off|toggle>", file=sys.stderr)
                raise SystemExit(2)
        case "--reset-spatial":
            defaults = AudioConfig()
            for prefix in ("", "out_"):
                for stem in ("delay_on", "delay_ms", "delay_feedback", "delay_mix",
                             "reverb_on", "reverb_room", "reverb_damp",
                             "reverb_width", "reverb_mix"):
                    name = prefix + stem
                    setattr(cfg, name, getattr(defaults, name))
            save_config(cfg)
            sync_config_to_daemon(cfg)
            print("Delay and Reverb reset to default bypass.")
        case "--status" | "-s" | "status":
            pid = get_daemon_pid()
            if pid and daemon_responds():
                tele = fetch_telemetry_from_daemon()
                snapshot = pipewire_audio_snapshot()
                missing = []
                if snapshot is not None:
                    if resolve_hardware_node("Audio/Source", "source", cfg.source,
                                             cfg.pre_source, snapshot) == NO_HARDWARE_TARGET:
                        missing.append("microphone")
                    if resolve_hardware_node("Audio/Sink", "sink", cfg.sink,
                                             cfg.pre_sink, snapshot) == NO_HARDWARE_TARGET:
                        missing.append("output")
                tele_str = f", VAD: {int(tele.vad_prob * 100)}%, Denoiser change: {tele.processing_delta_dbfs:.1f} dBFS" if tele else ""
                condition = "DEGRADED, missing " + ", ".join(missing) if missing else "ON"
                print(f"{condition} (PID {pid}, Suppression: {cfg.aggressiveness}%, Volume: {cfg.volume}%, Vocoder: {'ON' if cfg.vocoder_on else 'OFF'}{tele_str})")
            elif pid:
                print(f"ERROR (server PID {pid}, helper not ready)")
                raise SystemExit(1)
            else:
                print("OFF")
        case ("--preset" | "-p") if len(args) > 1:
            p_name = " ".join(args[1:])
            match_key = None
            for k in PRESETS:
                if k.lower() == p_name.lower():
                    match_key = k
                    break
            if match_key:
                apply_voice_preset(cfg, match_key, "mic")
                save_config(cfg)
                sync_config_to_daemon(cfg)
                print(f"Applied Character Preset: {match_key}")
            else:
                print(f"Preset '{p_name}' not found. Available: {', '.join(PRESETS.keys())}", file=sys.stderr)
                raise SystemExit(2)
        case ("--set-source" | "--source" | "--src") if len(args) > 1:
            val = " ".join(args[1:])
            cfg.source = val
            save_config(cfg)
            target = resolve_hardware_source(val)
            sync_config_to_daemon(cfg)
            print(f"Set Input Microphone Source to '{val}' (target: {target})")
        case ("--set-sink" | "--sink") if len(args) > 1:
            val = " ".join(args[1:])
            cfg.sink = val
            save_config(cfg)
            target = resolve_hardware_sink(val)
            sync_config_to_daemon(cfg)
            print(f"Set Output Playback Device to '{val}' (target: {target})")
        case ("--set-agg" | "--agg") if len(args) > 1:
            try:
                val = int(args[1])
                if not 0 <= val <= 100:
                    raise ValueError(val)
                cfg.aggressiveness = val
                save_config(cfg)
                send_daemon_cmd(f"AGG {val * 10}")
                print(f"Set Input Noise Reduction Aggressiveness to {val}%")
            except ValueError:
                print("Invalid value for aggressiveness (0-100).", file=sys.stderr)
                raise SystemExit(2)
        case ("--set-vol" | "--vol") if len(args) > 1:
            try:
                val = int(args[1])
                if not 0 <= val <= 200:
                    raise ValueError(val)
                cfg.volume = val
                save_config(cfg)
                send_daemon_cmd(f"VOL {val * 10}")
                print(f"Set Output Gain to {val}%")
            except ValueError:
                print("Invalid value for volume (0-200).", file=sys.stderr)
                raise SystemExit(2)
        case "--noise" if len(args) > 1:
            action = args[1].lower()
            if action == "on":
                cfg.rnnoise_on = True
                save_config(cfg)
                send_daemon_cmd("RNN 1")
                print("Input Noise Cancellation: ON")
            elif action == "off":
                cfg.rnnoise_on = False
                save_config(cfg)
                send_daemon_cmd("RNN 0")
                print("Input Noise Cancellation: OFF")
            elif action == "toggle":
                cfg.rnnoise_on = not cfg.rnnoise_on
                save_config(cfg)
                send_daemon_cmd(f"RNN {1 if cfg.rnnoise_on else 0}")
                print(f"Input Noise Cancellation: {'ON' if cfg.rnnoise_on else 'OFF'}")
            else:
                print("Usage: --noise <on|off|toggle>", file=sys.stderr)
                raise SystemExit(2)
        case "--noise-state" | "--noise-status":
            print("yes" if cfg.rnnoise_on else "no")
        case "--out-noise-state" | "--out-noise-status":
            print("yes" if cfg.out_rnnoise_on else "no")
        case "--get-agg":
            print(cfg.aggressiveness)
        case "--get-out-agg":
            print(cfg.out_aggressiveness)
        case "--out-noise" if len(args) > 1:
            action = args[1].lower()
            if action == "on":
                cfg.out_rnnoise_on = True
                save_config(cfg)
                send_daemon_cmd("OUT_NOISE 1")
                print("Output Noise Cancellation: ON")
            elif action == "off":
                cfg.out_rnnoise_on = False
                save_config(cfg)
                send_daemon_cmd("OUT_NOISE 0")
                print("Output Noise Cancellation: OFF")
            elif action == "toggle":
                cfg.out_rnnoise_on = not cfg.out_rnnoise_on
                save_config(cfg)
                send_daemon_cmd(f"OUT_NOISE {1 if cfg.out_rnnoise_on else 0}")
                print(f"Output Noise Cancellation: {'ON' if cfg.out_rnnoise_on else 'OFF'}")
            else:
                print("Usage: --out-noise <on|off|toggle>", file=sys.stderr)
                raise SystemExit(2)
        case ("--set-out-agg" | "--out-agg") if len(args) > 1:
            try:
                val = int(args[1])
                if not 0 <= val <= 100:
                    raise ValueError(val)
                cfg.out_aggressiveness = val
                save_config(cfg)
                send_daemon_cmd(f"OUT_AGG {val * 10}")
                print(f"Set Output Noise Reduction Aggressiveness to {val}%")
            except ValueError:
                print("Invalid value for output aggressiveness (0-100).", file=sys.stderr)
                raise SystemExit(2)
        case "--help" | "-h":
            print(
                """Usage: dusky_audio_studio.py [COMMAND]

Commands:
  --gui, -g                 Launch complete GTK3 Audio Studio window (default)
  --gui-only               Open settings without starting persisted DSP
  --autostart              Start DSP only when the saved setting is ON
  --toggle, -t              Toggle Audio DSP / Noise Cancellation ON / OFF
  --on                      Turn Audio DSP ON
  --off                     Turn Audio DSP OFF
  --reset, -r               Reset all audio settings to clean factory defaults
  --reset-voice             Reset microphone and playback voice effects
  --reset-eq                Reset 9-Band EQ to Flat 0 dB
  --reset-spatial           Reset microphone and playback Delay & Reverb
  --status, -s              Print current status and live telemetry
  --preset, -p <name>       Apply microphone voice preset
  --set-source <node>       Set hardware capture microphone source
  --set-sink <node>         Set physical playback output device (speakers/headphones)
  --set-agg <0-100>         Set input RNNoise suppression aggressiveness (0 to 100%)
  --set-vol <0-200>         Set microphone volume/gain (0 to 200%)
  --noise <on|off|toggle>   Toggle microphone RNNoise
  --out-eq <on|off|toggle>  Toggle playback EQ
  --out-noise <on|off|toggle>  Toggle output noise cancellation (Two-Way)
  --set-out-agg <0-100>     Set output RNNoise suppression aggressiveness (0 to 100%)
  --noise-state, --out-noise-state  Show saved noise toggles
  --get-agg, --get-out-agg  Show saved aggressiveness values
  --help, -h                Show this help message

Available Voice Character Presets:
  """
                + ", ".join(f'"{k}"' for k in PRESETS.keys())
            )
        case _:
            print(f"Unknown command: {args[0]}. Run with --help for usage.", file=sys.stderr)
            raise SystemExit(2)


if __name__ == "__main__":
    main()
