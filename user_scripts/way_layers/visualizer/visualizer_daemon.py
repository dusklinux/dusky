#!/usr/bin/env python3
"""
Dusky Wayland Audio Visualizer Daemon

Target:
- Arch Linux
- Latest Python
- GTK 3
- gtk-layer-shell
- Cava + PipeWire
- Optional OpenGL acceleration

Design goals:
- single-process, single-threaded GLib event loop
- robust config normalization and validation
- atomic config saves
- safe Cava lifecycle management
- safe FIFO IPC
- OpenGL fallback to Cairo
- Hyprland blur integration
- modern Python typing and enums
"""

from __future__ import annotations

import ctypes
import fcntl
import json
import logging
import math
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, fields, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

# -----------------------------------------------------------------------------
# Hard dependency bootstrap
# -----------------------------------------------------------------------------

DEPENDENCY_HINT = """
Missing required dependencies.

On Arch Linux, install at least:

    python
    python-gobject
    python-cairo
    gtk3
    gtk-layer-shell
    glib2
    cairo
    pango
    gdk-pixbuf2
    libepoxy
    wayland
    wayland-protocols
    cava
    pipewire
    wireplumber
    pipewire-alsa
    pipewire-pulse

For GPU acceleration also install:

    python-opengl
    mesa

or the appropriate OpenGL 3.3+ driver for your GPU.
"""

def notify_user(title: str, body: str, urgency: str = "critical") -> None:
    """Send a desktop notification using notify-send if available."""
    notify_bin = shutil.which("notify-send")
    if notify_bin:
        try:
            subprocess.run(
                [notify_bin, "-u", urgency, "-a", "Dusky Visualizer", title, body],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


try:
    import gi
except ImportError:
    msg = "python-gobject is missing. Install with: sudo pacman -S python-gobject"
    sys.stderr.write(f"{msg}\n")
    sys.stderr.write(DEPENDENCY_HINT)
    notify_user("Dusky Visualizer Error", msg)
    raise SystemExit(1)

try:
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GtkLayerShell", "0.1")
    gi.require_version("Gio", "2.0")
except ValueError as exc:
    msg = f"GTK / Layer Shell typelib missing ({exc}). Install with: sudo pacman -S gtk3 gtk-layer-shell"
    sys.stderr.write(f"{msg}\n")
    sys.stderr.write(DEPENDENCY_HINT)
    notify_user("Dusky Visualizer Error", msg)
    raise SystemExit(1)

try:
    import cairo
except ImportError:
    msg = "python-cairo is missing. Install with: sudo pacman -S python-cairo"
    sys.stderr.write(f"{msg}\n")
    sys.stderr.write(DEPENDENCY_HINT)
    notify_user("Dusky Visualizer Error", msg)
    raise SystemExit(1)

try:
    logging.getLogger("OpenGL.plugins").setLevel(logging.ERROR)
    import OpenGL.GL as GL

    HAS_OPENGL = True
except ImportError:
    GL = None
    HAS_OPENGL = False

from gi.repository import Gdk, Gio, GLib, Gtk, GtkLayerShell

try:
    from gi.repository import GLibUnix
except ImportError:
    GLibUnix = None

# -----------------------------------------------------------------------------
# Constants and XDG paths
# -----------------------------------------------------------------------------

APP_NAME = "dusky_visualizer"
LAYER_NAMESPACE = "dusky-visualizer"
MAX_BARS = 256
RAMP_LEN = 6


def _runtime_dir() -> Path:
    try:
        p = Path(GLib.get_user_runtime_dir())
        if p.is_dir():
            return p
    except Exception:
        pass
    return Path(tempfile.gettempdir())


CONFIG_DIR = Path(GLib.get_user_config_dir()) / "dusky" / "settings" / "way_layers" / "visualizer"
CONFIG_FILE = CONFIG_DIR / "visualizer.json"
CTL_FILE = CONFIG_DIR / "visualizer.ctl"

CACHE_DIR = Path(GLib.get_user_cache_dir()) / "dusky"
CAVA_CONF_FILE = CACHE_DIR / "cava_visualizer.conf"

COLORS_FILE = Path(GLib.get_user_config_dir()) / "matugen" / "generated" / "dusky_visualizer_colors.json"
LOCK_FILE = _runtime_dir() / f"{APP_NAME}-{os.getuid()}.lock"


# -----------------------------------------------------------------------------
# Modern enum schema
# -----------------------------------------------------------------------------


class Position(StrEnum):
    TOP = "top"
    CENTER = "center"
    BOTTOM = "bottom"


class Style(StrEnum):
    BARS = "bars"
    SEGMENTS = "segments"
    DOTS = "dots"
    WAVE = "wave"
    LINE = "line"
    MONITOR = "monitor"
    RADIAL = "radial"
    CIRCLE = "circle"
    SPECTRUM = "spectrum"
    AURORA = "aurora"
    PSYCHEDELIC = "psychedelic"
    KALEIDOSCOPE = "kaleidoscope"
    LIGHTNING = "lightning"
    PERIMETER = "perimeter"


class FadeDirection(StrEnum):
    FADE_TO_BASE = "fade_to_base"
    FADE_TO_TIP = "fade_to_tip"
    SOLID = "solid"


# -----------------------------------------------------------------------------
# Coercion / validation helpers
# -----------------------------------------------------------------------------


def clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        v = int(float(value))
    except Exception:
        return default
    return max(lo, min(hi, v))


def clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    if not math.isfinite(v):
        return default
    return max(lo, min(hi, v))


def coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"1", "true", "yes", "on"}:
            return True
        if s in {"0", "false", "no", "off"}:
            return False
    return default


def coerce_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def coerce_float(value: Any, default: float) -> float:
    try:
        v = float(value)
        if math.isfinite(v):
            return v
    except Exception:
        pass
    return default


def coerce_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return default
    return str(value).strip()


def valid_hex_color(value: Any, default: str) -> str:
    if not isinstance(value, str):
        return default

    s = value.strip().lower().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)

    if len(s) not in (6, 8):
        return default

    if all(c in "0123456789abcdef" for c in s):
        return f"#{s}"

    return default


def hex_to_rgba(hex_str: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    s = valid_hex_color(hex_str, "#ffffffff").lstrip("#")

    try:
        if len(s) == 6:
            r, g, b = (int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
            return (r, g, b, alpha)

        if len(s) == 8:
            r, g, b, a = (int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4, 6))
            return (r, g, b, a * alpha)
    except Exception:
        pass

    return (1.0, 1.0, 1.0, alpha)


def interpolate_color(ramp: tuple[tuple[float, float, float, float], ...], t: float) -> tuple[float, float, float, float]:
    if not ramp:
        return (1.0, 1.0, 1.0, 1.0)
    if len(ramp) == 1:
        return ramp[0]

    t = max(0.0, min(0.999999, t))
    x = t * (len(ramp) - 1)
    i = int(math.floor(x))
    f = x - i

    a = ramp[i]
    b = ramp[i + 1]

    return (
        a[0] + (b[0] - a[0]) * f,
        a[1] + (b[1] - a[1]) * f,
        a[2] + (b[2] - a[2]) * f,
        a[3] + (b[3] - a[3]) * f,
    )


# -----------------------------------------------------------------------------
# Config and color models
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class Config:
    version: int = 1

    enabled: bool = True
    position: Position = Position.TOP
    style: Style = Style.BARS

    bars: int = 72
    fps: int = 60
    height_pct: float = 0.50

    smoothing: float = 0.50
    gain: float = 1.00
    mirror: bool = False
    shape_rounded: bool = False
    thickness: float = 0.80
    bloom: float = 1.00
    inner_glow: float = 0.70
    specular_shine: float = 0.30
    stardust: float = 0.10
    idle_wave: bool = True

    fade_direction: FadeDirection = FadeDirection.FADE_TO_BASE
    fade_amount: float = 0.80

    glass_blur: bool = True
    segments_count: int = 18

    cava_noise_reduction: float = 0.77
    cava_lower_freq: int = 50
    cava_upper_freq: int = 10_000
    cava_source: str = ""

    gpu_acceleration: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Config:
        defaults = cls()
        kwargs: dict[str, Any] = {}

        bool_keys = {
            "enabled",
            "mirror",
            "shape_rounded",
            "idle_wave",
            "glass_blur",
            "gpu_acceleration",
        }

        int_keys = {
            "version",
            "bars",
            "fps",
            "segments_count",
            "cava_lower_freq",
            "cava_upper_freq",
        }

        float_keys = {
            "height_pct",
            "smoothing",
            "gain",
            "thickness",
            "bloom",
            "inner_glow",
            "specular_shine",
            "stardust",
            "fade_amount",
            "cava_noise_reduction",
        }

        string_keys = {
            "cava_source",
        }

        for raw_key, raw_value in raw.items():
            key = str(raw_key).strip()
            value = raw_value.strip() if isinstance(raw_value, str) else raw_value

            try:
                if key in bool_keys:
                    kwargs[key] = coerce_bool(value, getattr(defaults, key))

                elif key in int_keys:
                    kwargs[key] = coerce_int(value, getattr(defaults, key))

                elif key in float_keys:
                    kwargs[key] = coerce_float(value, getattr(defaults, key))

                elif key in string_keys:
                    kwargs[key] = coerce_str(value, getattr(defaults, key))

                elif key == "position":
                    kwargs[key] = Position(str(value).strip().lower())

                elif key == "style":
                    kwargs[key] = Style(str(value).strip().lower())

                elif key == "fade_direction":
                    kwargs[key] = FadeDirection(str(value).strip().lower())

            except Exception:
                # Ignore invalid enum/value and keep default.
                pass

        obj = cls(**kwargs)
        obj.normalize()
        return obj

    def normalize(self) -> None:
        self.version = clamp_int(self.version, 1, 1000, 1)

        self.bars = clamp_int(self.bars, 16, MAX_BARS, 64)
        if self.mirror and self.bars % 2 != 0:
            self.bars = max(16, self.bars - 1)

        self.fps = clamp_int(self.fps, 1, 240, 60)
        self.height_pct = clamp_float(self.height_pct, 0.05, 1.0, 0.20)

        self.smoothing = clamp_float(self.smoothing, 0.0, 0.99, 0.50)
        self.gain = clamp_float(self.gain, 0.0, 10.0, 1.5)
        self.thickness = clamp_float(self.thickness, 0.05, 1.0, 0.50)
        self.bloom = clamp_float(self.bloom, 0.0, 1.0, 0.20)
        self.fade_amount = clamp_float(self.fade_amount, 0.0, 1.0, 1.0)

        self.segments_count = clamp_int(self.segments_count, 1, 128, 16)

        self.cava_noise_reduction = clamp_float(self.cava_noise_reduction, 0.0, 1.0, 0.77)

        self.cava_lower_freq = clamp_int(self.cava_lower_freq, 20, 19_999, 50)
        self.cava_upper_freq = clamp_int(
            self.cava_upper_freq,
            self.cava_lower_freq + 1,
            20_000,
            min(20_000, self.cava_lower_freq + 100),
        )

        self.cava_source = coerce_str(self.cava_source, "")

        if not isinstance(self.position, Position):
            self.position = Position.TOP
        if not isinstance(self.style, Style):
            self.style = Style.BARS
        if not isinstance(self.fade_direction, FadeDirection):
            self.fade_direction = FadeDirection.FADE_TO_BASE

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, StrEnum):
                value = value.value
            out[f.name] = value
        return out


@dataclass(slots=True)
class Colors:
    c1: str = "#ffb4ac"
    c2: str = "#f5b9a1"
    c3: str = "#fbb983"
    c4: str = "#93000e"
    c5: str = "#663c2a"
    c6: str = "#693c10"
    accent: str = "#ffb4ac"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Colors:
        defaults = cls()
        obj = cls()

        for name in ("c1", "c2", "c3", "c4", "c5", "c6", "accent"):
            value = raw.get(name, getattr(defaults, name))
            setattr(obj, name, valid_hex_color(value, getattr(defaults, name)))

        return obj

    def ramp(self) -> tuple[tuple[float, float, float, float], ...]:
        # Intentionally preserves the original artistic ramp order.
        return (
            hex_to_rgba(self.c1),
            hex_to_rgba(self.c3),
            hex_to_rgba(self.c2),
            hex_to_rgba(self.c6),
            hex_to_rgba(self.c4),
            hex_to_rgba(self.c5),
        )


# -----------------------------------------------------------------------------
# Atomic file IO
# -----------------------------------------------------------------------------


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())

        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)

    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(data, indent=4, ensure_ascii=False) + "\n")


def load_json_dict(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            logging.error("JSON file %s is not an object; ignoring.", path)
            return {}

        normalized: dict[str, Any] = {}
        for k, v in data.items():
            key = str(k).strip()
            normalized[key] = v.strip() if isinstance(v, str) else v

        return normalized

    except (OSError, json.JSONDecodeError) as exc:
        logging.error("Failed loading %s: %s", path, exc)
        return {}


# -----------------------------------------------------------------------------
# OpenGL shaders
# -----------------------------------------------------------------------------

VERTEX_SHADER = """
#version 330 core

layout (location = 0) in vec2 aPos;
out vec2 uv;

void main() {
    uv = vec2(aPos.x * 0.5 + 0.5, 1.0 - (aPos.y * 0.5 + 0.5));
    gl_Position = vec4(aPos, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """
#version 330 core

in vec2 uv;
out vec4 FragColor;

uniform int u_style;
uniform int u_bars;
uniform int u_segments_count;
uniform float u_thickness;
uniform bool u_shape_rounded;
uniform int u_position;
uniform int u_fade_direction;
uniform float u_fade_amount;
uniform float u_bloom;
uniform float u_inner_glow;
uniform float u_specular_shine;
uniform float u_stardust;
uniform float u_idle_time;

uniform float u_data[256];
uniform vec4 u_ramp[6];
uniform int u_ramp_len;

uniform float u_resolution_x;
uniform float u_resolution_y;
uniform float u_content_height;

#define PI 3.14159265359

float sdRoundedBox(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + r;
    return min(max(q.x, q.y), 0.0) + length(max(q, 0.0)) - r;
}

vec4 finalize(vec4 c) {
    c.a = clamp(c.a, 0.0, 1.0);
    c.rgb *= c.a;
    return c;
}

float calc_specular(float x, float norm_y) {
    if (u_specular_shine <= 0.0) return 0.0;
    float sweep = sin(x * 6.0 + u_idle_time * 1.8) * 0.5 + 0.5;
    float glint = pow(sweep, 8.0) * u_specular_shine;
    return glint * (1.0 - norm_y * 0.6);
}

float calc_stardust(vec2 st_pos) {
    if (u_stardust <= 0.0) return 0.0;
    vec2 st = vec2(st_pos.x * 45.0, (st_pos.y - u_idle_time * 0.08) * 35.0);
    vec2 ipos = floor(st);
    vec2 fpos = fract(st);
    float rnd = fract(sin(dot(ipos, vec2(12.9898, 78.233))) * 43758.5453);
    float dist = length(fpos - vec2(0.5));
    return (1.0 - smoothstep(0.0, 0.16, dist)) * step(0.91, rnd) * u_stardust;
}

float aa_edge(float d, float w) {
    float e0 = max(0.0, w - 1.5);
    float e1 = max(e0 + 0.0001, w);
    return 1.0 - smoothstep(e0, e1, d);
}

float aa_signed(float d) {
    return 1.0 - smoothstep(-0.5, 0.5, d);
}

vec4 sample_ramp(float t) {
    if (u_ramp_len <= 1) {
        return u_ramp[0];
    }
    t = clamp(t, 0.0, 0.999999);
    float x = t * float(u_ramp_len - 1);
    int idx = int(floor(x));
    float f = x - float(idx);
    return mix(u_ramp[idx], u_ramp[idx + 1], f);
}

vec4 get_ramp_color(float t) {
    return sample_ramp(t);
}

vec4 apply_grad(vec4 base_col, float norm_y) {
    norm_y = clamp(norm_y, 0.0, 1.0);
    float alpha_faded = base_col.a * (1.0 - u_fade_amount);

    if (u_fade_direction == 2 || u_fade_amount == 0.0) {
        return vec4(base_col.rgb, base_col.a);
    }

    if (u_fade_direction == 0) {
        if (norm_y < 0.45) {
            float t = norm_y / 0.45;
            return vec4(base_col.rgb, mix(alpha_faded, base_col.a * 0.8, t));
        }

        float t = (norm_y - 0.45) / 0.55;
        return vec4(base_col.rgb, mix(base_col.a * 0.8, base_col.a, t));
    }

    if (norm_y < 0.55) {
        float t = norm_y / 0.55;
        return vec4(base_col.rgb, mix(base_col.a, base_col.a * 0.8, t));
    }

    float t = (norm_y - 0.55) / 0.45;
    return vec4(base_col.rgb, mix(base_col.a * 0.8, alpha_faded, t));
}

float get_val(int i) {
    if (u_style == 5 || u_style == 6) {
        return u_data[(i % u_bars + u_bars) % u_bars];
    }

    if (i < 0) {
        return u_data[0];
    }

    if (i >= u_bars) {
        return u_data[u_bars - 1];
    }

    return u_data[i];
}

float get_smooth_val(float x_norm) {
    float f_idx = x_norm * float(u_bars);
    int i = int(floor(f_idx));
    float t = f_idx - float(i);

    float p0 = get_val(i - 1);
    float p1 = get_val(i);
    float p2 = get_val(i + 1);
    float p3 = get_val(i + 2);

    return 0.5 * (
        (2.0 * p1) +
        (-p0 + p2) * t +
        (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t * t +
        (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t * t * t
    );
}

void main() {
    vec2 pos = vec2(uv.x * u_resolution_x, uv.y * u_resolution_y);

    // -----------------------------------------------------------------
    // Wave / Line / Monitor
    // -----------------------------------------------------------------
    if (u_style == 3 || u_style == 4) {
        float val = max(0.0, get_smooth_val(uv.x));
        vec4 color = sample_ramp(uv.x);

        float curve_y;
        float norm_y;

        if (u_position == 0) {
            curve_y = val * u_content_height;
            norm_y = clamp(pos.y / max(1.0, curve_y), 0.0, 1.0);
        } else if (u_position == 2) {
            curve_y = u_resolution_y - val * u_content_height;
            norm_y = clamp((u_resolution_y - pos.y) / max(1.0, curve_y), 0.0, 1.0);
        } else {
            curve_y = u_resolution_y * 0.5 - val * u_content_height * 0.5;
            norm_y = clamp(abs(pos.y - u_resolution_y * 0.5) / max(1.0, val * u_content_height * 0.5), 0.0, 1.0);
        }

        if (u_style == 3) {
            float d_outside = (u_position == 0) ? (pos.y - curve_y)
                            : (u_position == 2) ? (curve_y - pos.y)
                            : (abs(pos.y - u_resolution_y * 0.5) - val * u_content_height * 0.5);

            float glow_margin = 28.0 * u_bloom;
            if (d_outside > glow_margin) {
                discard;
            }

            if (d_outside > 0.0) {
                float halo = exp(-d_outside / (7.0 * u_bloom + 0.1)) * u_bloom * 0.95;
                vec4 glow_col = color * (1.0 + 0.5 * u_bloom);
                glow_col.a = color.a * halo;
                FragColor = finalize(glow_col);
                return;
            }

            vec4 out_color = apply_grad(color, norm_y);

            if (u_inner_glow > 0.0) {
                float edge_dist = -d_outside;
                float inner_rim = exp(-edge_dist / (10.0 * u_inner_glow + 0.1)) * u_inner_glow;
                out_color.rgb = mix(out_color.rgb, min(vec3(1.0), out_color.rgb * 2.2 + vec3(0.3)), inner_rim * 0.9);
            }

            if (u_specular_shine > 0.0) {
                float glint = calc_specular(uv.x, norm_y);
                out_color.rgb += vec3(0.95, 0.98, 1.0) * glint;
            }

            if (u_stardust > 0.0) {
                float spark = calc_stardust(uv);
                out_color.rgb += vec3(0.9, 0.96, 1.0) * spark;
            }

            FragColor = finalize(out_color);
            return;
        }

        // Line style (u_style == 4)
        float lw = u_thickness * 5.0 + 1.0;
        float d = abs(pos.y - curve_y);
        if (d > lw) {
            discard;
        }

        float alpha = color.a * aa_edge(d, lw);
        FragColor = finalize(vec4(color.rgb, alpha));
        return;
    }

    // -----------------------------------------------------------------
    // Radial / Circle
    // -----------------------------------------------------------------
    if (u_style == 5 || u_style == 6) {
        float min_dim = min(u_resolution_x, u_resolution_y);

        float max_len = u_content_height * 0.45;
        float ring_r = min(min_dim * 0.12, max_len * 0.6);

        vec2 center = vec2(u_resolution_x * 0.5, u_resolution_y * 0.5);

        vec2 d_vec = pos - center;
        float dist = length(d_vec);

        float ang = mod(atan(d_vec.y, d_vec.x) + 2.0 * PI, 2.0 * PI);
        float norm_idx = mod((ang - PI * 0.5 + 2.0 * PI) / (2.0 * PI), 1.0);

        int bar_idx = min(int(floor(norm_idx * float(u_bars))), u_bars - 1);
        float val = max(0.0, u_data[bar_idx]);

        vec4 base_color = get_ramp_color(float(bar_idx) / max(1.0, float(u_bars - 1)));

        if (u_style == 5) {
            float avg_beat = (u_data[0] + u_data[1] + u_data[2] + u_data[3]) * 0.25;
            ring_r *= 1.0 + 0.25 * avg_beat;

            float arc_full_w = (2.0 * PI * ring_r) / float(u_bars);
            float gap = arc_full_w * (1.0 - u_thickness);
            float bar_w = max(0.0, arc_full_w - gap);
            float bar_h = max(2.0, max_len * val);

            float ang_bar_center = ((float(bar_idx) + 0.5) / float(u_bars)) * 2.0 * PI + PI * 0.5;
            vec2 dir = vec2(cos(ang_bar_center), sin(ang_bar_center));

            float radial_y = dot(d_vec, dir) - ring_r;
            float tangent_x = dot(d_vec, vec2(-dir.y, dir.x));

            bool in_bar = !(abs(tangent_x) > bar_w * 0.5 || radial_y < 0.0 || radial_y > bar_h);

            if (in_bar && u_shape_rounded) {
                float r = min(bar_w * 0.5, bar_h * 0.5);

                if (radial_y < r && length(vec2(tangent_x, radial_y - r)) > r) {
                    in_bar = false;
                } else if (radial_y > bar_h - r && length(vec2(tangent_x, radial_y - (bar_h - r))) > r) {
                    in_bar = false;
                }
            }

            float ring_d = abs(dist - ring_r);

            if (!in_bar && ring_d > 2.0) {
                discard;
            }

            if (in_bar) {
                vec4 out_color = apply_grad(base_color, clamp(radial_y / max(1.0, bar_h), 0.0, 1.0));
                float ring_aa = 1.0 - smoothstep(0.0, 2.0, ring_d);

                if (ring_d <= 2.0) {
                    out_color.rgb = mix(out_color.rgb, u_ramp[0].rgb, ring_aa * 0.6);
                }

                FragColor = finalize(out_color);
                return;
            }

            float ring_aa = 1.0 - smoothstep(0.0, 2.0, ring_d);
            FragColor = finalize(vec4(u_ramp[0].rgb, u_ramp[0].a * ring_aa * 0.6));
            return;

        } else {
            float target_r = ring_r + max_len * max(0.0, get_smooth_val(norm_idx));

            if (dist > target_r) {
                discard;
            }

            float span = ring_r + max_len;
            vec4 smooth_color = get_ramp_color(
                clamp((pos.x - (center.x - span)) / max(1.0, 2.0 * span), 0.0, 1.0)
            );

            vec4 out_color = apply_grad(
                smooth_color,
                clamp((dist - ring_r) / max(1.0, target_r - ring_r), 0.0, 1.0)
            );

            float edge_d = target_r - dist;
            float glow = (1.0 - smoothstep(0.0, 3.0, edge_d)) * (0.25 + 0.75 * u_bloom);
            out_color.rgb = mix(out_color.rgb, min(vec3(1.0), smooth_color.rgb * (1.0 + 0.5 * u_bloom)), glow);

            float edge_aa = smoothstep(0.0, 1.5, edge_d);
            FragColor = finalize(vec4(out_color.rgb, out_color.a * edge_aa));
            return;
        }
    }

    // -----------------------------------------------------------------
    // Spectrum Oscilloscope Beam (Style 7)
    // -----------------------------------------------------------------
    if (u_style == 7) {
        float val1 = max(0.0, get_smooth_val(uv.x));
        float val2 = max(0.0, get_smooth_val(uv.x + 0.04 * sin(u_idle_time * 2.0)));
        vec4 color = sample_ramp(uv.x);

        float curve_y1 = (u_position == 0) ? val1 * u_content_height
                       : (u_position == 2) ? u_resolution_y - val1 * u_content_height
                       : u_resolution_y * 0.5 - val1 * u_content_height * 0.5;

        float curve_y2 = (u_position == 0) ? val2 * u_content_height * 0.85
                       : (u_position == 2) ? u_resolution_y - val2 * u_content_height * 0.85
                       : u_resolution_y * 0.5 + val2 * u_content_height * 0.5;

        float d1 = abs(pos.y - curve_y1);
        float d2 = abs(pos.y - curve_y2);

        float beam1 = exp(-d1 / 4.0);
        float beam2 = exp(-d2 / 4.0);
        float interference = beam1 * beam2 * 2.5;

        vec3 rgb = color.rgb * (beam1 + beam2) + vec3(0.3, 0.8, 1.0) * interference;
        float alpha = clamp(beam1 + beam2 + interference, 0.0, 1.0) * color.a;

        if (alpha < 0.01) discard;

        FragColor = finalize(vec4(rgb, alpha));
        return;
    }

    // -----------------------------------------------------------------
    // Aurora Plasma Ribbon (Style 8)
    // -----------------------------------------------------------------
    if (u_style == 8) {
        float val = max(0.0, get_smooth_val(uv.x));
        vec4 color = sample_ramp(uv.x);

        float wave1 = sin(uv.x * 12.0 + u_idle_time * 2.5) * 15.0 * val;
        float wave2 = cos(uv.x * 20.0 - u_idle_time * 1.8) * 10.0 * val;

        float target_y = (u_position == 0) ? val * u_content_height + wave1
                       : (u_position == 2) ? u_resolution_y - val * u_content_height - wave1
                       : u_resolution_y * 0.5 + wave1 + wave2;

        float dist = abs(pos.y - target_y);
        float ribbon_width = 18.0 * (1.0 + val);

        if (dist > ribbon_width * 2.0) discard;

        float plasma = exp(-dist / (ribbon_width * 0.5)) * (0.8 + 0.4 * sin(uv.x * 30.0 + u_idle_time * 4.0));
        vec3 aurora_rgb = mix(color.rgb, vec3(0.3, 1.0, 0.8), sin(uv.x * 6.0 + u_idle_time) * 0.5 + 0.5);

        vec4 out_col = vec4(aurora_rgb * (1.0 + plasma), color.a * plasma);
        FragColor = finalize(out_col);
        return;
    }

    // -----------------------------------------------------------------
    // Ultra-Psychedelic Infinite Wormhole (Style 9)
    // -----------------------------------------------------------------
    if (u_style == 9) {
        vec2 c = vec2(u_resolution_x * 0.5, u_resolution_y * 0.5);
        vec2 d = (pos - c) / min(u_resolution_x, u_resolution_y);
        float dist = length(d);
        float angle = atan(d.y, d.x);

        float max_radius = (u_content_height / u_resolution_y) * 0.55;
        if (dist > max_radius || dist < 0.001) discard;

        float norm_d = dist / max_radius;

        // Tunnel Z-depth (infinite zoom effect)
        float z = 0.3 / (norm_d + 0.04) + u_idle_time * 1.5;

        // Polar spectrum sampling
        float norm_angle = mod((angle + PI) / (2.0 * PI), 1.0);
        int bar_idx = min(int(floor(norm_angle * float(u_bars))), u_bars - 1);
        float val = max(0.0, u_data[bar_idx]);

        // Audio twist & spiral distortion
        float twist = angle + sin(z * 2.0 + val * 4.0) * 0.5;
        float pattern = sin(twist * 8.0) * sin(z * 10.0 - val * 8.0);
        float rings = sin(z * 18.0);

        float trip = clamp((pattern + rings) * 0.5 + val * 0.7, 0.0, 1.0);

        // Electric psychedelic RGB palette
        vec3 col1 = 0.5 + 0.5 * cos(z * 0.8 + vec3(0.0, 2.0, 4.0));
        vec3 col2 = 0.5 + 0.5 * sin(angle * 3.0 + u_idle_time * 2.0 + vec3(4.0, 2.0, 0.0));
        vec3 trippy_rgb = mix(col1, col2, 0.5) * (1.2 + trip * 1.5);

        float alpha = smoothstep(0.1, 0.4, trip) * (1.0 - smoothstep(0.75, 1.0, norm_d)) * sample_ramp(norm_angle).a;

        FragColor = finalize(vec4(trippy_rgb, alpha));
        return;
    }

    // -----------------------------------------------------------------
    // Smooth Liquid Glass Kaleidoscope (Style 10)
    // -----------------------------------------------------------------
    if (u_style == 10) {
        vec2 c = vec2(u_resolution_x * 0.5, u_resolution_y * 0.5);
        vec2 d = (pos - c) / min(u_resolution_x, u_resolution_y);
        float dist = length(d);
        float angle = atan(d.y, d.x);

        float max_radius = (u_content_height / u_resolution_y) * 0.55;
        if (dist > max_radius || dist < 0.001) discard;

        float norm_d = dist / max_radius;

        // Elegant 8-fold polar symmetry with slow, smooth rotation
        float folds = 8.0;
        float segment_angle = (2.0 * PI) / folds;
        float rot_angle = angle + u_idle_time * 0.15;
        float k_angle = mod(rot_angle, segment_angle);
        k_angle = abs(k_angle - segment_angle * 0.5);

        // Folded 2D UV coordinates
        vec2 k_uv = vec2(cos(k_angle), sin(k_angle)) * norm_d;

        // Sample audio spectrum
        float norm_idx = mod((atan(k_uv.y, k_uv.x) + PI) / (2.0 * PI), 1.0);
        int bar_idx = min(int(floor(norm_idx * float(u_bars))), u_bars - 1);
        float val = max(0.0, u_data[bar_idx]);

        // Smooth liquid glass lattice & audio ripple
        float spoke = sin(k_angle * 10.0 + u_idle_time * 0.8);
        float ring = sin(norm_d * 18.0 - u_idle_time * 1.2 - val * 6.0 + spoke * 0.5);
        float mandala = clamp(ring * 0.5 + 0.5 + val * 0.5, 0.0, 1.0);

        vec4 base_color = sample_ramp(norm_idx);
        vec3 final_rgb = mix(base_color.rgb, vec3(1.0, 0.95, 0.8), mandala * 0.4) * (0.8 + mandala * 0.7);

        float alpha = smoothstep(0.15, 0.4, mandala) * (1.0 - smoothstep(0.8, 1.0, norm_d)) * base_color.a;

        FragColor = finalize(vec4(final_rgb, alpha));
        return;
    }

    // -----------------------------------------------------------------
    // 4-Side Screen Perimeter Frame (Style 13)
    // -----------------------------------------------------------------
    if (u_style == 13) {
        float d_top = pos.y;
        float d_bottom = u_resolution_y - pos.y;
        float d_left = pos.x;
        float d_right = u_resolution_x - pos.x;

        float min_edge_dist = min(min(d_top, d_bottom), min(d_left, d_right));

        float perim_t;
        if (min_edge_dist == d_top) {
            perim_t = pos.x / u_resolution_x * 0.25;
        } else if (min_edge_dist == d_right) {
            perim_t = 0.25 + (pos.y / u_resolution_y) * 0.25;
        } else if (min_edge_dist == d_bottom) {
            perim_t = 0.50 + ((u_resolution_x - pos.x) / u_resolution_x) * 0.25;
        } else {
            perim_t = 0.75 + ((u_resolution_y - pos.y) / u_resolution_y) * 0.25;
        }

        float val = max(0.0, get_smooth_val(perim_t));
        vec4 color = sample_ramp(perim_t);

        float max_depth = u_content_height * 0.45 * val + 4.0;
        float glow_margin = 28.0 * u_bloom;

        if (min_edge_dist > max_depth + glow_margin) {
            discard;
        }

        if (min_edge_dist > max_depth) {
            float halo_dist = min_edge_dist - max_depth;
            float halo = exp(-halo_dist / (7.0 * u_bloom + 0.1)) * u_bloom * 0.95;
            vec4 glow_col = color * (1.0 + 0.5 * u_bloom);
            glow_col.a = color.a * halo;
            FragColor = finalize(glow_col);
            return;
        }

        float norm_y = clamp(min_edge_dist / max(1.0, max_depth), 0.0, 1.0);
        vec4 out_color = apply_grad(color, norm_y);

        if (u_inner_glow > 0.0) {
            float edge_dist = max_depth - min_edge_dist;
            float inner_rim = exp(-edge_dist / (10.0 * u_inner_glow + 0.1)) * u_inner_glow;
            out_color.rgb = mix(out_color.rgb, min(vec3(1.0), out_color.rgb * 2.2 + vec3(0.3)), inner_rim * 0.9);
        }

        if (u_specular_shine > 0.0) {
            float glint = calc_specular(perim_t, norm_y);
            out_color.rgb += vec3(0.95, 0.98, 1.0) * glint;
        }

        if (u_stardust > 0.0) {
            float spark = calc_stardust(uv);
            out_color.rgb += vec3(0.9, 0.96, 1.0) * spark;
        }

        FragColor = finalize(out_color);
        return;
    }

    // -----------------------------------------------------------------
    // Electric Lightning & Thunder Arcs (Style 12)
    // -----------------------------------------------------------------
    if (u_style == 12) {
        float val = max(0.0, get_smooth_val(uv.x));
        vec4 color = sample_ramp(uv.x);

        // High-frequency procedural electric jaggedness
        float hash1 = fract(sin(uv.x * 147.3 + floor(u_idle_time * 24.0)) * 43758.5453) - 0.5;
        float hash2 = sin(uv.x * 80.0 + u_idle_time * 35.0);
        float jagged = (hash1 * 28.0 + hash2 * 12.0) * (0.2 + 0.8 * val);

        float curve_y = (u_position == 0) ? val * u_content_height + jagged
                       : (u_position == 2) ? u_resolution_y - val * u_content_height - jagged
                       : u_resolution_y * 0.5 + jagged;

        float dist = abs(pos.y - curve_y);

        // Hot electric core & plasma aura
        float lw = u_thickness * 3.0 + 1.0;
        float core = exp(-dist / lw);
        float aura = exp(-dist / (14.0 + 18.0 * val)) * (0.6 + 0.4 * sin(u_idle_time * 25.0 + uv.x * 10.0));

        // Secondary electric discharge branches on audio spikes
        float branch_hash = fract(sin(uv.x * 215.1 + floor(u_idle_time * 18.0)) * 39142.1) - 0.5;
        float branch_dist = abs(pos.y - (curve_y + branch_hash * 60.0 * val));
        float branch = exp(-branch_dist / 2.5) * step(0.4, val) * 0.6;

        float total_energy = clamp(core * 1.4 + aura * 0.8 + branch * 1.0, 0.0, 1.0);
        if (total_energy < 0.01) discard;

        // Electric cyan/violet thunder color mix
        vec3 electric_rgb = mix(color.rgb, vec3(0.4, 0.8, 1.0), 0.6);
        electric_rgb = mix(electric_rgb, vec3(1.0, 1.0, 1.0), core * 0.8); // White hot core

        float alpha = total_energy * color.a;
        FragColor = finalize(vec4(electric_rgb, alpha));
        return;
    }

    // -----------------------------------------------------------------
    // Bar-domain styles: dots / segments / bars
    // -----------------------------------------------------------------
    float bar_full_w = u_resolution_x / float(u_bars);
    int bar_idx = int(floor(pos.x / bar_full_w));

    if (bar_idx < 0 || bar_idx >= u_bars) {
        discard;
    }

    float gap = bar_full_w * (1.0 - u_thickness);
    float bar_w = max(0.0, bar_full_w - gap);
    float local_x = mod(pos.x, bar_full_w);

    if (local_x < gap * 0.5 || local_x > gap * 0.5 + bar_w) {
        discard;
    }

    float val = max(0.0, u_data[bar_idx]);
    if (val <= 0.001) {
        discard;
    }

    vec4 base_color = get_ramp_color(float(bar_idx) / max(1.0, float(u_bars - 1)));

    // Dots
    if (u_style == 2) {
        float center_x = (float(bar_idx) * bar_full_w) + gap * 0.5 + bar_w * 0.5;

        float center_y;
        if (u_position == 0) {
            center_y = val * u_content_height;
        } else if (u_position == 2) {
            center_y = u_resolution_y - val * u_content_height;
        } else {
            center_y = u_resolution_y * 0.5 - val * u_content_height * 0.5;
        }

        float radius = max(0.5, bar_w * 0.5);
        float d = length(pos - vec2(center_x, center_y));

        if (d > radius) {
            discard;
        }

        float alpha = base_color.a * aa_edge(d, radius);
        FragColor = finalize(vec4(base_color.rgb, alpha));
        return;
    }

    // Segments
    if (u_style == 1) {
        float pitch = u_content_height / float(u_segments_count);
        float seg_gap = max(1.5, pitch * 0.26);

        int lit_cells = int(ceil(val * float(u_segments_count)));
        int cell_idx;
        float local_y;

        if (u_position == 0) {
            cell_idx = int(floor(pos.y / pitch));
            local_y = mod(pos.y, pitch);
        } else if (u_position == 2) {
            cell_idx = int(floor((u_resolution_y - pos.y) / pitch));
            local_y = mod((u_resolution_y - pos.y), pitch);
        } else {
            cell_idx = int(floor(abs(pos.y - u_resolution_y * 0.5) * 2.0 / pitch));
            local_y = mod(abs(pos.y - u_resolution_y * 0.5) * 2.0, pitch);
        }

        if (cell_idx < 0 || cell_idx >= lit_cells) {
            discard;
        }

        float cell_h = max(2.0, pitch - seg_gap);

        if (local_y < seg_gap * 0.5 || local_y > seg_gap * 0.5 + cell_h) {
            discard;
        }

        float intensity = 0.4 + 0.6 * (float(cell_idx) / max(1.0, float(lit_cells - 1)));
        vec4 color = vec4(base_color.rgb, base_color.a * intensity);

        if (u_shape_rounded) {
            vec2 center = vec2(gap * 0.5 + bar_w * 0.5, seg_gap * 0.5 + cell_h * 0.5);
            vec2 box = vec2(max(0.5, bar_w * 0.5 - 0.5), max(0.5, cell_h * 0.5 - 0.5));
            float radius = min(cell_h, bar_w) * 0.35;
            radius = min(radius, min(box.x, box.y));

            float d = sdRoundedBox(vec2(local_x, local_y) - center, box, radius);

            if (d > 0.5) {
                discard;
            }

            if (d > -0.5) {
                color.a *= aa_signed(d);
            }
        }

        FragColor = finalize(color);
        return;
    }

    // Bars
    float norm_y = 0.0;
    float h_val = val * u_content_height;
    float glow_margin = 22.0 * u_bloom;

    float dist_outside_y = 0.0;

    if (u_position == 0) {
        if (pos.y > h_val) {
            dist_outside_y = pos.y - h_val;
        }
        norm_y = clamp(pos.y / max(1.0, h_val), 0.0, 1.0);
    } else if (u_position == 2) {
        if (pos.y < u_resolution_y - h_val) {
            dist_outside_y = (u_resolution_y - h_val) - pos.y;
        }
        norm_y = clamp((u_resolution_y - pos.y) / max(1.0, h_val), 0.0, 1.0);
    } else {
        if (abs(pos.y - u_resolution_y * 0.5) > h_val * 0.5) {
            dist_outside_y = abs(pos.y - u_resolution_y * 0.5) - h_val * 0.5;
        }
        norm_y = clamp(abs(pos.y - u_resolution_y * 0.5) / max(1.0, h_val * 0.5), 0.0, 1.0);
    }

    float dist_outside_x = max(0.0, max(gap * 0.5 - local_x, local_x - (gap * 0.5 + bar_w)));
    float dist_outside = length(vec2(dist_outside_x, dist_outside_y));

    if (dist_outside > glow_margin) {
        discard;
    }

    vec4 color = apply_grad(base_color, norm_y);

    if (u_shape_rounded) {
        float r = bar_w * 0.5;
        float center_y_tip;
        float d_tip = -1.0;

        if (u_position == 0) {
            center_y_tip = max(0.0, h_val - r);
            if (pos.y > center_y_tip) {
                d_tip = length(vec2(local_x - (gap * 0.5 + r), pos.y - center_y_tip)) - r;
            }
        } else if (u_position == 2) {
            center_y_tip = min(u_resolution_y, u_resolution_y - h_val + r);
            if (pos.y < center_y_tip) {
                d_tip = length(vec2(local_x - (gap * 0.5 + r), pos.y - center_y_tip)) - r;
            }
        }

        if (d_tip > glow_margin) {
            discard;
        }

        if (d_tip > 0.0) {
            float halo = exp(-d_tip / (5.5 * u_bloom + 0.1)) * u_bloom * 0.95;
            color = base_color * (1.0 + 0.5 * u_bloom);
            color.a = base_color.a * halo;
        } else {
            if (d_tip > -0.5) {
                color.a *= aa_signed(d_tip);
            }
            if (u_inner_glow > 0.0) {
                float inner_dist = abs(d_tip > -1.0 ? d_tip : 0.0);
                float inner_rim = exp(-inner_dist / (8.0 * u_inner_glow + 0.1)) * u_inner_glow;
                color.rgb = mix(color.rgb, min(vec3(1.0), color.rgb * 2.2 + vec3(0.3)), inner_rim * 0.9);
            }
        }
    } else {
        if (dist_outside > 0.0) {
            float halo = exp(-dist_outside / (5.5 * u_bloom + 0.1)) * u_bloom * 0.95;
            color = base_color * (1.0 + 0.5 * u_bloom);
            color.a = base_color.a * halo;
        } else if (u_inner_glow > 0.0) {
            float inner_dist = dist_outside_y;
            float inner_rim = exp(-inner_dist / (8.0 * u_inner_glow + 0.1)) * u_inner_glow;
            color.rgb = mix(color.rgb, min(vec3(1.0), color.rgb * 2.2 + vec3(0.3)), inner_rim * 0.9);
        }
    }

    FragColor = finalize(color);
}
"""


# -----------------------------------------------------------------------------
# Daemon
# -----------------------------------------------------------------------------


class Visualizer:
    def __init__(self) -> None:
        self.log = logging.getLogger(APP_NAME)

        self.config = Config()
        self.colors = Colors()

        self.cava_shared_data: list[float] = []
        self.smoothed_data: list[float] = []

        self.is_overlay = False

        self.window: Gtk.Window | None = None
        self.widget: Gtk.Widget | None = None
        self.use_gl = False
        self.content_height = 0.0
        self.gl_failed = False
        self.fallback_pending = False

        self.has_rendered_idle_clear = False
        self.idle_time = 0.0
        self.last_frame_time = GLib.get_monotonic_time()

        self.cava_proc: subprocess.Popen[bytes] | None = None
        self.cava_watch: int | None = None
        self.cava_buffer = b""
        self.cava_fail_count = 0
        self.cava_restart_source: int | None = None
        self.cava_available = shutil.which("cava") is not None

        self.fifo_fd: int | None = None
        self.fifo_watch: int | None = None

        self.lock_fd: int | None = None

        self.tick_source: int | None = None

        self.config_monitor: Gio.FileMonitor | None = None
        self.colors_monitor: Gio.FileMonitor | None = None
        self.colors_retry_source: int | None = None
        self.reload_debounce: int | None = None
        self.monitor_suppress_until = 0

        self.css_added = False

        self.gl_program: int | None = None
        self.gl_vao: int | None = None
        self.gl_vbo: int | None = None
        self.gl_uniforms: dict[str, int] = {}
        self.gl_last_bars: int | None = None

        self.ramp_dirty = True

        if HAS_OPENGL:
            self.gl_data_array = (GL.GLfloat * MAX_BARS)()
            self.gl_ramp_array = (GL.GLfloat * (RAMP_LEN * 4))()
        else:
            self.gl_data_array = None
            self.gl_ramp_array = None

        self.reload_events = {
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
            Gio.FileMonitorEvent.CREATED,
        }
        if hasattr(Gio.FileMonitorEvent, "MOVED"):
            self.reload_events.add(Gio.FileMonitorEvent.MOVED)

        self.monitor_flags = Gio.FileMonitorFlags.NONE
        if hasattr(Gio.FileMonitorFlags, "WATCH_MOVES"):
            self.monitor_flags = Gio.FileMonitorFlags.WATCH_MOVES

    # -------------------------------------------------------------------------
    # Lifecycle / locking / environment
    # -------------------------------------------------------------------------

    def acquire_lock(self) -> None:
        try:
            self.lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            self.log.warning("Could not open lock file %s: %s", LOCK_FILE, exc)
            return

        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(self.lock_fd, 0)
            os.write(self.lock_fd, f"{os.getpid()}\n".encode())
        except BlockingIOError:
            self.log.error("Another instance is already running.")
            raise SystemExit(1)
        except OSError as exc:
            self.log.warning("Could not lock %s: %s", LOCK_FILE, exc)

    def release_lock(self) -> None:
        if self.lock_fd is None:
            return

        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass

        try:
            os.close(self.lock_fd)
        except OSError:
            pass

        try:
            LOCK_FILE.unlink(missing_ok=True)
        except OSError:
            pass

        self.lock_fd = None

    def check_display(self) -> bool:
        try:
            res = Gtk.init_check([])
            ok = res[0] if isinstance(res, tuple) else bool(res)
        except Exception:
            ok = False

        if not ok:
            self.log.error(
                "Cannot open a graphical Wayland display. "
                "Start this daemon inside a Wayland session."
            )
            return False

        display = Gdk.Display.get_default()
        if display is None:
            self.log.error("Gdk.Display.get_default() returned None.")
            return False

        return True

    def dependency_report(self) -> None:
        if not self.cava_available:
            self.log.error(
                "cava binary not found in PATH. Install the 'cava' package. "
                "The daemon will continue without audio data."
            )

        if not HAS_OPENGL:
            self.log.info("PyOpenGL not available; GPU acceleration disabled.")

    # -------------------------------------------------------------------------
    # Config / colors
    # -------------------------------------------------------------------------

    def load_initial(self) -> None:
        if not CONFIG_FILE.exists():
            self.save_config()

        raw_config = load_json_dict(CONFIG_FILE)
        raw_colors = load_json_dict(COLORS_FILE)

        self.config = Config.from_dict(raw_config)
        self.colors = Colors.from_dict(raw_colors)

        self.ramp_dirty = True
        self.ensure_data_arrays()

    def save_config(self) -> None:
        try:
            self.monitor_suppress_until = GLib.get_monotonic_time() + 750_000
            atomic_write_json(CONFIG_FILE, self.config.to_dict())
        except OSError as exc:
            self.log.error("Failed saving config: %s", exc)

    def ensure_data_arrays(self) -> None:
        n = self.config.bars

        if len(self.cava_shared_data) != n:
            self.cava_shared_data = [0.0] * n

        if len(self.smoothed_data) != n:
            self.smoothed_data = [0.0] * n

    def queue_reload(self) -> None:
        if self.reload_debounce is not None:
            self.remove_glib_source(self.reload_debounce)
        self.reload_debounce = GLib.timeout_add(120, self.execute_reload)

    def execute_reload(self) -> bool:
        self.reload_debounce = None

        try:
            old_config = self.config

            raw_config = load_json_dict(CONFIG_FILE)
            raw_colors = load_json_dict(COLORS_FILE)

            self.config = Config.from_dict(raw_config)
            self.colors = Colors.from_dict(raw_colors)

            self.ramp_dirty = True
            self.has_rendered_idle_clear = False

            self.ensure_data_arrays()
            self.apply_config_changes(old_config)

        except Exception:
            self.log.exception("Failed to reload configuration.")

        return False

    # -------------------------------------------------------------------------
    # Config change application
    # -------------------------------------------------------------------------

    def apply_config_changes(self, old_config: Config | None) -> None:
        new_config = self.config

        if old_config is None:
            if new_config.enabled:
                self.setup_window()
                self.start_cava()
                self.ensure_tick()
            else:
                self.stop_cava()
                self.remove_tick()
            return

        # Enabled state changes dominate everything else.
        if old_config.enabled != new_config.enabled:
            if new_config.enabled:
                self.has_rendered_idle_clear = False
                self.setup_window()
                self.start_cava()
                self.ensure_tick()
            else:
                self.stop_cava()
                self.remove_tick()
                self.destroy_window()
            return

        if not new_config.enabled:
            return

        # If the user explicitly toggled GPU acceleration, give GL another chance.
        if old_config.gpu_acceleration != new_config.gpu_acceleration:
            self.gl_failed = False

        # FPS affects both tick and Cava.
        if old_config.fps != new_config.fps:
            self.reschedule_tick()

        # Cava-affecting settings.
        cava_changed = any(
            (
                old_config.bars != new_config.bars,
                old_config.fps != new_config.fps,
                old_config.cava_lower_freq != new_config.cava_lower_freq,
                old_config.cava_upper_freq != new_config.cava_upper_freq,
                old_config.cava_noise_reduction != new_config.cava_noise_reduction,
                old_config.cava_source != new_config.cava_source,
            )
        )

        if cava_changed:
            self.start_cava()

        # Window geometry / renderer / blur-affecting settings.
        window_changed = any(
            (
                old_config.position != new_config.position,
                old_config.height_pct != new_config.height_pct,
                old_config.style != new_config.style,
                old_config.gpu_acceleration != new_config.gpu_acceleration,
                old_config.glass_blur != new_config.glass_blur,
            )
        )

        if window_changed:
            self.setup_window()
            self.ensure_tick()

        # Anything else may still require a redraw.
        self.has_rendered_idle_clear = False
        self.ensure_tick()

    # -------------------------------------------------------------------------
    # File monitors
    # -------------------------------------------------------------------------

    def init_file_monitors(self) -> None:
        try:
            self.config_monitor = Gio.File.new_for_path(str(CONFIG_DIR)).monitor_directory(
                self.monitor_flags,
                None,
            )
            self.config_monitor.connect("changed", self.on_dir_changed)
        except Exception as exc:
            self.log.error("Could not monitor config directory: %s", exc)

        # If this returns True, it means the colors monitor is not available yet,
        # so keep retrying every 2 seconds.
        if self.ensure_colors_monitor():
            self.colors_retry_source = GLib.timeout_add(2000, self.ensure_colors_monitor)

    def ensure_colors_monitor(self) -> bool:
        """
        Return True to keep retrying.
        Return False once monitoring is established.
        """
        if self.colors_monitor is not None:
            self.colors_retry_source = None
            return False

        parent = COLORS_FILE.parent
        if not parent.exists():
            return True

        try:
            self.colors_monitor = Gio.File.new_for_path(str(parent)).monitor_directory(
                self.monitor_flags,
                None,
            )
            self.colors_monitor.connect("changed", self.on_dir_changed)
            self.colors_retry_source = None
            return False

        except Exception as exc:
            self.log.warning("Could not monitor colors directory: %s", exc)
            return True

    def on_dir_changed(
        self,
        monitor: Gio.FileMonitor,
        file: Gio.File,
        other_file: Gio.File | None,
        event_type: Gio.FileMonitorEvent,
    ) -> None:
        try:
            now = GLib.get_monotonic_time()
            if now < self.monitor_suppress_until:
                return

            if event_type not in self.reload_events:
                return

            paths: list[Path] = []

            for f in (file, other_file):
                if f is None:
                    continue
                p = f.get_path()
                if p:
                    paths.append(Path(p))

            if any(p == CONFIG_FILE or p == COLORS_FILE for p in paths):
                self.queue_reload()

        except Exception:
            self.log.exception("File monitor handler failed.")

    # -------------------------------------------------------------------------
    # FIFO IPC
    # -------------------------------------------------------------------------

    def init_fifo_ipc(self) -> None:
        try:
            CTL_FILE.parent.mkdir(parents=True, exist_ok=True)

            if CTL_FILE.exists() and not stat.S_ISFIFO(CTL_FILE.stat().st_mode):
                CTL_FILE.unlink()

            if not CTL_FILE.exists():
                os.mkfifo(CTL_FILE, 0o600)

            self.fifo_fd = os.open(CTL_FILE, os.O_RDWR | os.O_NONBLOCK)
            self.fifo_watch = GLib.io_add_watch(
                self.fifo_fd,
                GLib.PRIORITY_DEFAULT,
                GLib.IOCondition.IN,
                self.on_fifo_read,
            )

        except Exception as exc:
            self.log.error("Failed to initialize FIFO IPC: %s", exc)

    def on_fifo_read(self, fd: int, condition: GLib.IOCondition) -> bool:
        try:
            raw = os.read(fd, 4096)
            if not raw:
                return True

            text = raw.decode("utf-8", "ignore").strip()
            if not text:
                return True

            for line in text.splitlines():
                cmd = line.strip().lower()

                if cmd == "toggle":
                    self.toggle_enabled()
                elif cmd == "overlay":
                    self.toggle_overlay()

        except BlockingIOError:
            pass
        except Exception:
            self.log.exception("FIFO read handler failed.")

        return True

    def toggle_enabled(self) -> None:
        old_config = self.config
        new_config = replace(self.config, enabled=not self.config.enabled)
        new_config.normalize()

        self.config = new_config
        self.save_config()
        self.apply_config_changes(old_config)

    def toggle_overlay(self) -> None:
        self.is_overlay = not self.is_overlay
        self.has_rendered_idle_clear = False

        if self.config.enabled:
            self.setup_window()
            self.ensure_tick()

    # -------------------------------------------------------------------------
    # Hyprland IPC
    # -------------------------------------------------------------------------

    def find_hyprland_socket(self) -> Path | None:
        roots: list[Path] = [Path("/tmp/hypr")]

        xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
        if xdg_runtime:
            roots.append(Path(xdg_runtime) / "hypr")

        sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        if sig:
            for root in roots:
                sock = root / sig / ".socket.sock"
                if sock.exists():
                    return sock

        uid = os.getuid()
        candidates: list[Path] = []

        for root in roots:
            if not root.is_dir():
                continue

            try:
                for child in root.iterdir():
                    if not child.is_dir():
                        continue

                    sock = child / ".socket.sock"

                    try:
                        if (
                            sock.exists()
                            and child.stat().st_uid == uid
                            and os.access(sock, os.W_OK)
                        ):
                            candidates.append(sock)
                    except OSError:
                        pass
            except OSError:
                pass

        if not candidates:
            return None

        try:
            candidates.sort(key=lambda p: p.parent.stat().st_mtime, reverse=True)
        except OSError:
            pass

        return candidates[0]

    def send_hyprland_command(self, cmd: str) -> bool:
        sock_path = self.find_hyprland_socket()
        if sock_path is None:
            return False

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.25)
                sock.connect(str(sock_path))
                sock.sendall(cmd.encode())

                try:
                    sock.recv(1024)
                except Exception:
                    pass

            return True

        except Exception as exc:
            self.log.debug("Hyprland IPC failed: %s", exc)
            return False

    def apply_hyprland_rules(self) -> None:
        ns = LAYER_NAMESPACE

        if self.config.glass_blur:
            self.send_hyprland_command(f'eval hl.layer_rule({{ name = "dusky_visualizer_blur", match = {{ namespace = "{ns}" }}, blur = true, ignore_alpha = 0.0 }})')
        else:
            self.send_hyprland_command(f'eval hl.layer_rule({{ name = "dusky_visualizer_blur", match = {{ namespace = "{ns}" }}, blur = false }})')

    # -------------------------------------------------------------------------
    # Cava
    # -------------------------------------------------------------------------

    def generate_cava_config(self) -> Path:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        noise = int(round(self.config.cava_noise_reduction * 100.0))
        noise = max(0, min(100, noise))

        source_line = ""
        if self.config.cava_source:
            source_line = f"source = {self.config.cava_source}\n"

        text = f"""
[general]
framerate = {self.config.fps}
bars = {self.config.bars}
lower_cutoff_freq = {self.config.cava_lower_freq}
higher_cutoff_freq = {self.config.cava_upper_freq}

[input]
method = pipewire
{source_line}
[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = 1000

[smoothing]
integral = {noise}
monstercat = 1
gravity = 100
ignore = 0
noise_reduction = {noise}
"""

        atomic_write_text(CAVA_CONF_FILE, text)
        return CAVA_CONF_FILE

    def start_cava(self) -> bool:
        if not self.config.enabled:
            return False

        cava_path = shutil.which("cava")
        self.cava_available = cava_path is not None

        if not self.cava_available or cava_path is None:
            msg = "cava package is missing. Install with: sudo pacman -S cava"
            self.log.error(msg)
            notify_user("Dusky Visualizer Warning", msg, urgency="normal")
            return False

        if self.cava_restart_source is not None:
            self.remove_glib_source(self.cava_restart_source)
            self.cava_restart_source = None

        self.stop_cava()

        try:
            conf_path = self.generate_cava_config()
        except Exception as exc:
            self.log.error("Failed generating Cava config: %s", exc)
            self.schedule_cava_restart()
            return False

        try:
            self.cava_proc = subprocess.Popen(
                [cava_path, "-p", str(conf_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                text=False,
                bufsize=0,
            )

            assert self.cava_proc.stdout is not None

            fd = self.cava_proc.stdout.fileno()
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            self.cava_buffer = b""
            self.cava_watch = GLib.io_add_watch(
                fd,
                GLib.PRIORITY_HIGH,
                GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
                self.on_cava_stdout,
            )

            self.log.info("Cava started.")
            return True

        except FileNotFoundError:
            self.log.error("cava binary disappeared.")
            self.cava_available = False
            return False

        except Exception as exc:
            self.log.error("Failed starting Cava: %s", exc)
            self.schedule_cava_restart()
            return False

    def stop_cava(self) -> None:
        if self.cava_restart_source is not None:
            self.remove_glib_source(self.cava_restart_source)
            self.cava_restart_source = None

        if self.cava_watch is not None:
            self.remove_glib_source(self.cava_watch)
            self.cava_watch = None

        if self.cava_proc is not None:
            try:
                self.cava_proc.terminate()
                self.cava_proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    self.cava_proc.kill()
                except Exception:
                    pass
            except Exception:
                pass

            try:
                if self.cava_proc.stdout is not None:
                    self.cava_proc.stdout.close()
            except Exception:
                pass

            self.cava_proc = None

        self.cava_buffer = b""

    def schedule_cava_restart(self) -> None:
        if not self.config.enabled:
            return

        if not self.cava_available:
            return

        if self.cava_restart_source is not None:
            self.remove_glib_source(self.cava_restart_source)

        delay = min(30.0, 0.25 * (2 ** min(6, self.cava_fail_count)))
        self.cava_restart_source = GLib.timeout_add(
            int(delay * 1000),
            self.on_cava_restart_timeout,
        )

    def on_cava_restart_timeout(self) -> bool:
        self.cava_restart_source = None

        if self.config.enabled:
            self.start_cava()

        return False

    def on_cava_stdout(self, fd: int, condition: GLib.IOCondition) -> bool:
        try:
            if condition & (GLib.IOCondition.HUP | GLib.IOCondition.ERR):
                self.cava_watch = None
                self.cava_fail_count += 1
                self.schedule_cava_restart()
                return False

            data = os.read(fd, 65536)
            if not data:
                self.cava_watch = None
                self.cava_fail_count += 1
                self.schedule_cava_restart()
                return False

            self.cava_buffer += data

            if len(self.cava_buffer) > 1_048_576:
                self.cava_buffer = b""

            if b"\n" not in self.cava_buffer:
                return True

            *lines, remainder = self.cava_buffer.split(b"\n")
            self.cava_buffer = remainder

            if not lines:
                return True

            # Use only the newest complete frame.
            line = lines[-1].strip()
            if not line:
                return True

            parts = line.split(b";")
            if parts and parts[-1] == b"":
                parts.pop()

            bars = self.config.bars
            if len(parts) != bars:
                return True

            gain = self.config.gain
            out = self.cava_shared_data

            if len(out) != bars:
                self.ensure_data_arrays()
                out = self.cava_shared_data

            active = False

            for i, part in enumerate(parts):
                value = 0.0

                if part:
                    try:
                        value = (int(part) / 1000.0) * gain
                    except ValueError:
                        value = 0.0

                if value < 0.0:
                    value = 0.0

                out[i] = value

                if value > 0.001:
                    active = True

            self.cava_fail_count = 0

            if active:
                self.has_rendered_idle_clear = False
                self.ensure_tick()

        except BlockingIOError:
            pass
        except Exception:
            self.log.exception("Cava stdout handler failed.")
            self.cava_watch = None
            self.cava_fail_count += 1
            self.schedule_cava_restart()
            return False

        return True

    # -------------------------------------------------------------------------
    # Render timing / data preparation
    # -------------------------------------------------------------------------

    def update_frame_dt(self) -> float:
        now = GLib.get_monotonic_time()
        dt = (now - self.last_frame_time) / 1_000_000.0
        self.last_frame_time = now

        if dt < 0.0:
            dt = 0.0
        if dt > 0.1:
            dt = 0.1

        return dt

    def update_smoothing(self) -> None:
        n = self.config.bars

        if len(self.smoothed_data) != n or len(self.cava_shared_data) != n:
            self.ensure_data_arrays()

        alpha = self.config.smoothing
        factor = 1.0 - alpha

        smooth = self.smoothed_data
        target = self.cava_shared_data

        for i in range(n):
            smooth[i] += (target[i] - smooth[i]) * factor

    def prepare_render_data(self) -> list[float] | None:
        n = self.config.bars
        if n <= 0:
            return None

        self.update_smoothing()

        dt = self.update_frame_dt()
        idle = not any(v > 0.01 for v in self.cava_shared_data)

        self.idle_time += dt
        if self.idle_time > 86400.0:
            self.idle_time = 0.0

        if idle and self.config.idle_wave:
            t = self.idle_time
            is_circular = self.config.style in (Style.RADIAL, Style.CIRCLE)
            render_data = []

            for i in range(n):
                x = i / max(1, n)
                if is_circular:
                    w1 = math.sin(t * 0.5 + x * math.pi * 4.0)
                    w2 = math.sin(t * 0.8 - x * math.pi * 6.0) * 0.35
                    w3 = math.sin(t * 0.3 + x * math.pi * 2.0) * 0.25
                    edge_fade = 1.0
                else:
                    w1 = math.sin(t * 0.5 + x * math.pi * 3.0)
                    w2 = math.sin(t * 0.8 - x * math.pi * 5.0) * 0.35
                    w3 = math.sin(t * 0.3 + x * math.pi * 1.5) * 0.25
                    edge_fade = math.sin(x * math.pi)

                combined = (w1 + w2 + w3) / 1.6
                amp = (math.sin(t * 0.4) * 0.025 + 0.045) * edge_fade
                val = max(0.005, (combined * 0.5 + 0.5) * amp)
                render_data.append(val)

        elif idle and not self.config.idle_wave:
            if not any(v > 0.0001 for v in self.smoothed_data):
                self.has_rendered_idle_clear = True
                return None
            render_data = self.smoothed_data[:]

        else:
            render_data = self.smoothed_data[:]

        if self.config.mirror:
            half = n // 2
            if half > 0:
                if n % 2 == 0:
                    render_data[half:] = render_data[:half][::-1]
                else:
                    render_data[half + 1 :] = render_data[:half][::-1]



        return render_data

    # -------------------------------------------------------------------------
    # Tick loop
    # -------------------------------------------------------------------------

    def ensure_tick(self) -> None:
        if not self.config.enabled:
            return

        if self.widget is None:
            return

        if self.tick_source is not None:
            return

        interval = max(1, int(1000 / self.config.fps))
        self.tick_source = GLib.timeout_add(interval, self.tick)

    def remove_tick(self) -> None:
        if self.tick_source is not None:
            self.remove_glib_source(self.tick_source)
        self.tick_source = None

    def reschedule_tick(self) -> None:
        self.remove_tick()
        self.ensure_tick()

    def tick(self) -> bool:
        if not self.config.enabled or self.widget is None:
            self.tick_source = None
            return False

        active = (
            any(v > 0.001 for v in self.cava_shared_data)
            or any(v > 0.0001 for v in self.smoothed_data)
        )

        if active:
            self.has_rendered_idle_clear = False

        should_render = active or self.config.idle_wave or not self.has_rendered_idle_clear

        if should_render:
            try:
                if self.use_gl:
                    self.widget.queue_render()
                else:
                    self.widget.queue_draw()
            except Exception:
                self.log.exception("Failed to queue render; clearing widget state.")
                self.widget = None
                self.tick_source = None
                return False

            return True

        self.tick_source = None
        return False

    # -------------------------------------------------------------------------
    # Color helpers
    # -------------------------------------------------------------------------

    def get_color_ramp(self) -> tuple[tuple[float, float, float, float], ...]:
        return self.colors.ramp()

    def color_at(self, i: int, n: int, ramp: tuple[tuple[float, float, float, float], ...]) -> tuple[float, float, float, float]:
        if n <= 1:
            return interpolate_color(ramp, 0.0)
        return interpolate_color(ramp, i / (n - 1))

    def apply_gradient(
        self,
        cr: cairo.Context,
        color: tuple[float, float, float, float],
        x_base: float,
        y_base: float,
        x_tip: float,
        y_tip: float,
    ) -> None:
        r, g, b, a = color

        fade = self.config.fade_direction
        amt = max(0.0, min(1.0, self.config.fade_amount))
        bloom_factor = 1.0 + 0.5 * self.config.bloom

        lr = min(1.0, r * bloom_factor)
        lg = min(1.0, g * bloom_factor)
        lb = min(1.0, b * bloom_factor)

        alpha_faded = a * (1.0 - amt)

        if x_base == x_tip and y_base == y_tip:
            cr.set_source_rgba(r, g, b, a)
            return

        pat = cairo.LinearGradient(x_base, y_base, x_tip, y_tip)

        if fade == FadeDirection.SOLID or amt == 0.0:
            pat.add_color_stop_rgba(0.0, r, g, b, a)
            pat.add_color_stop_rgba(1.0, lr, lg, lb, a)

        elif fade == FadeDirection.FADE_TO_BASE:
            pat.add_color_stop_rgba(0.0, r, g, b, alpha_faded)
            pat.add_color_stop_rgba(0.45, r, g, b, a * 0.8)
            pat.add_color_stop_rgba(1.0, lr, lg, lb, a)

        else:  # FADE_TO_TIP
            pat.add_color_stop_rgba(0.0, lr, lg, lb, a)
            pat.add_color_stop_rgba(0.55, r, g, b, a * 0.8)
            pat.add_color_stop_rgba(1.0, r, g, b, alpha_faded)

        cr.set_source(pat)

    # -------------------------------------------------------------------------
    # Cairo renderer
    # -------------------------------------------------------------------------

    @staticmethod
    def draw_rounded_rect(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
        if w <= 0.0 or h <= 0.0:
            return

        r = max(0.0, r)
        r = min(r, w / 2.0, h / 2.0)

        if r <= 0.0:
            cr.rectangle(x, y, w, h)
            return

        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2.0, 0.0)
        cr.arc(x + w - r, y + h - r, r, 0.0, math.pi / 2.0)
        cr.arc(x + r, y + h - r, r, math.pi / 2.0, math.pi)
        cr.arc(x + r, y + r, r, math.pi, math.pi * 1.5)
        cr.close_path()

    def on_draw(self, widget: Gtk.Widget, cr: cairo.Context) -> bool:
        try:
            w = float(widget.get_allocated_width())
            h = float(widget.get_allocated_height())

            cr.set_operator(cairo.OPERATOR_CLEAR)
            cr.paint()
            cr.set_operator(cairo.OPERATOR_OVER)

            if not self.config.enabled:
                return False

            if w <= 1.0 or h <= 1.0:
                return False

            data = self.prepare_render_data()
            if data is None:
                return False

            self.draw_cairo(cr, w, h, data)

        except Exception:
            self.log.exception("Cairo draw failed.")

        return False

    def draw_cairo(self, cr: cairo.Context, w: float, h: float, data: list[float]) -> None:
        n = self.config.bars
        if n <= 0:
            return

        ramp = self.get_color_ramp()
        pos = self.config.position

        bar_full_w = w / n
        gap = bar_full_w * (1.0 - self.config.thickness)
        bar_w = max(0.0, bar_full_w - gap)

        match self.config.style:
            case Style.BARS:
                for i in range(n):
                    val = max(0.0, data[i]) * h
                    if val <= 0.0:
                        continue

                    x = i * (bar_w + gap) + gap / 2.0

                    if pos == Position.TOP:
                        y = 0.0
                        height = val
                        y_base = 0.0
                        y_tip = val
                    elif pos == Position.BOTTOM:
                        y = h - val
                        height = val
                        y_base = h
                        y_tip = h - val
                    else:
                        y = h / 2.0 - val / 2.0
                        height = val
                        y_base = h / 2.0 + val / 2.0
                        y_tip = h / 2.0 - val / 2.0

                    color = self.color_at(i, n, ramp)

                    if self.config.shape_rounded:
                        radius = min(bar_w / 2.0, height / 2.0)
                        self.draw_rounded_rect(cr, x, y, bar_w, height, radius)
                    else:
                        cr.rectangle(x, y, bar_w, height)

                    self.apply_gradient(cr, color, x, y_base, x, y_tip)
                    cr.fill()

            case Style.SEGMENTS:
                seg_n = self.config.segments_count
                pitch = h / seg_n
                seg_gap = max(1.5, pitch * 0.26)
                cell_h = max(1.0, pitch - seg_gap)

                for i in range(n):
                    val = max(0.0, min(1.0, data[i]))
                    lit = int(math.ceil(val * seg_n))
                    lit = max(0, min(seg_n, lit))

                    if lit <= 0:
                        continue

                    x = i * (bar_w + gap) + gap / 2.0
                    color = self.color_at(i, n, ramp)

                    for cell in range(lit):
                        if pos == Position.TOP:
                            y = cell * pitch + seg_gap / 2.0
                        elif pos == Position.BOTTOM:
                            y = h - (cell + 1) * pitch + seg_gap / 2.0
                        else:
                            y = h / 2.0 - lit * pitch / 2.0 + cell * pitch + seg_gap / 2.0

                        alpha_factor = 0.4 + 0.6 * (cell / max(1, lit - 1))
                        cr.set_source_rgba(color[0], color[1], color[2], color[3] * alpha_factor)

                        if self.config.shape_rounded:
                            radius = min(cell_h, bar_w) * 0.35
                            self.draw_rounded_rect(cr, x, y, bar_w, cell_h, radius)
                        else:
                            cr.rectangle(x, y, bar_w, cell_h)

                        cr.fill()

            case Style.WAVE:
                points: list[tuple[float, float]] = []

                for i in range(n):
                    val = max(0.0, data[i]) * h
                    x = i * (w / n) + (w / n) / 2.0

                    if pos == Position.TOP:
                        y = val
                    elif pos == Position.BOTTOM:
                        y = h - val
                    else:
                        y = h / 2.0 - val / 2.0

                    points.append((x, y))

                if len(points) >= 2:
                    if pos == Position.TOP:
                        base_y = 0.0
                        grad_y = h
                    elif pos == Position.BOTTOM:
                        base_y = h
                        grad_y = 0.0
                    else:
                        base_y = h / 2.0
                        grad_y = 0.0

                    cr.move_to(0.0, base_y)
                    cr.line_to(0.0, points[0][1])
                    cr.line_to(points[0][0], points[0][1])

                    for i in range(len(points) - 1):
                        cx = (points[i][0] + points[i + 1][0]) / 2.0
                        cr.curve_to(cx, points[i][1], cx, points[i + 1][1], points[i + 1][0], points[i + 1][1])

                    cr.line_to(w, points[-1][1])
                    cr.line_to(w, base_y)
                    cr.close_path()

                    amt = max(0.0, min(1.0, self.config.fade_amount))
                    pat = cairo.LinearGradient(w / 2.0, base_y, w / 2.0, grad_y)

                    for i, c in enumerate(ramp):
                        alpha = max(0.0, c[3] * (1.0 - amt * 0.8))
                        pat.add_color_stop_rgba(i / (len(ramp) - 1), c[0], c[1], c[2], alpha)

                    cr.set_source(pat)
                    cr.fill()

            case Style.LINE | Style.MONITOR:
                points = []

                for i in range(n):
                    val = max(0.0, data[i]) * h
                    x = i * (w / n) + (w / n) / 2.0

                    if pos == Position.TOP:
                        y = val
                    elif pos == Position.BOTTOM:
                        y = h - val
                    else:
                        y = h / 2.0 - val / 2.0

                    points.append((x, y))

                if len(points) >= 2:
                    cr.set_line_width(self.config.thickness * 5.0 + 1.0)
                    cr.set_line_join(cairo.LINE_JOIN_ROUND)
                    cr.set_line_cap(cairo.LINE_CAP_ROUND)

                    cr.move_to(points[0][0], points[0][1])

                    for i in range(len(points) - 1):
                        cx = (points[i][0] + points[i + 1][0]) / 2.0
                        cr.curve_to(cx, points[i][1], cx, points[i + 1][1], points[i + 1][0], points[i + 1][1])

                    pat = cairo.LinearGradient(0.0, 0.0, w, 0.0)
                    for i, c in enumerate(ramp):
                        pat.add_color_stop_rgba(i / (len(ramp) - 1), c[0], c[1], c[2], c[3])

                    cr.set_source(pat)
                    cr.stroke()

            case Style.DOTS:
                radius = max(0.5, bar_w / 2.0)

                for i in range(n):
                    val = max(0.0, data[i]) * h
                    if val <= 0.0:
                        continue

                    x = i * (bar_w + gap) + gap / 2.0 + bar_w / 2.0

                    if pos == Position.TOP:
                        y = val
                    elif pos == Position.BOTTOM:
                        y = h - val
                    else:
                        y = h / 2.0 - val / 2.0

                    cr.set_source_rgba(*self.color_at(i, n, ramp))
                    cr.arc(x, y, radius, 0.0, math.pi * 2.0)
                    cr.fill()

            case Style.RADIAL | Style.CIRCLE:
                cx = w / 2.0
                cy = h / 2.0
                min_dim = min(w, h)

                ring_r = min_dim * 0.15
                max_len = min_dim * 0.3

                if self.config.style == Style.RADIAL:
                    quarter = max(1, n // 4)
                    avg = sum(data[:quarter]) / quarter
                    ring_r *= 1.0 + 0.25 * max(0.0, avg)

                    arc_full = (2.0 * math.pi * ring_r) / n
                    arc_gap = arc_full * (1.0 - self.config.thickness)
                    arc_w = max(1.0, arc_full - arc_gap)

                    for i in range(n):
                        val = max(2.0, max_len * max(0.0, data[i]))
                        color = self.color_at(i, n, ramp)

                        cr.save()
                        cr.translate(cx, cy)
                        cr.rotate((i / n) * math.pi * 2.0 + math.pi / 2.0)

                        if self.config.shape_rounded:
                            radius = min(arc_w / 2.0, val / 2.0)
                            self.draw_rounded_rect(cr, -arc_w / 2.0, ring_r, arc_w, val, radius)
                        else:
                            cr.rectangle(-arc_w / 2.0, ring_r, arc_w, val)

                        self.apply_gradient(cr, color, 0.0, ring_r, 0.0, ring_r + val)
                        cr.fill()
                        cr.restore()

                    cr.set_line_width(2.0)
                    cr.set_source_rgba(ramp[0][0], ramp[0][1], ramp[0][2], 0.4)
                    cr.arc(cx, cy, ring_r, 0.0, math.pi * 2.0)
                    cr.stroke()

                else:
                    px: list[float] = []
                    py: list[float] = []

                    for i in range(n):
                        val = max(0.0, data[i])
                        angle = (i / n) * math.pi * 2.0 + math.pi / 2.0
                        radius = ring_r + max_len * val

                        px.append(cx + math.cos(angle) * radius)
                        py.append(cy + math.sin(angle) * radius)

                    if len(px) >= 2:
                        cr.move_to((px[-1] + px[0]) / 2.0, (py[-1] + py[0]) / 2.0)

                        for k in range(n):
                            nx = (k + 1) % n
                            cr.curve_to(
                                px[k],
                                py[k],
                                px[k],
                                py[k],
                                (px[k] + px[nx]) / 2.0,
                                (py[k] + py[nx]) / 2.0,
                            )

                        cr.close_path()

                        amt = max(0.0, min(1.0, self.config.fade_amount))
                        pat = cairo.LinearGradient(cx - ring_r - max_len, 0.0, cx + ring_r + max_len, 0.0)

                        for i, c in enumerate(ramp):
                            alpha = max(0.0, c[3] * (1.0 - amt * 0.6))
                            pat.add_color_stop_rgba(i / (len(ramp) - 1), c[0], c[1], c[2], alpha)

                        cr.set_source(pat)
                        cr.fill_preserve()

                        cr.set_line_width(3.0)
                        cr.set_source_rgba(ramp[-1][0], ramp[-1][1], ramp[-1][2], 0.8)
                        cr.stroke()

    # -------------------------------------------------------------------------
    # OpenGL renderer
    # -------------------------------------------------------------------------

    def compile_shader(self, source: str, shader_type: int) -> int:
        shader = GL.glCreateShader(shader_type)
        GL.glShaderSource(shader, source)
        GL.glCompileShader(shader)

        if not GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
            log = GL.glGetShaderInfoLog(shader)
            if isinstance(log, bytes):
                log = log.decode("utf-8", "replace")

            GL.glDeleteShader(shader)
            raise RuntimeError(f"Shader compile failed: {log}")

        return shader

    def delete_gl_resources(self) -> None:
        if not HAS_OPENGL:
            return

        try:
            if self.gl_program is not None:
                GL.glDeleteProgram(self.gl_program)
        except Exception:
            pass

        try:
            if self.gl_vbo is not None:
                GL.glDeleteBuffers(1, [self.gl_vbo])
        except Exception:
            pass

        try:
            if self.gl_vao is not None:
                GL.glDeleteVertexArrays(1, [self.gl_vao])
        except Exception:
            pass

        self.gl_program = None
        self.gl_vao = None
        self.gl_vbo = None
        self.gl_uniforms = {}
        self.gl_last_bars = None

    def on_gl_realize(self, widget: Gtk.GLArea) -> None:
        try:
            widget.make_current()

            if widget.get_error():
                raise RuntimeError("GTK GLArea reported a context error.")

            self.delete_gl_resources()

            vs = self.compile_shader(VERTEX_SHADER, GL.GL_VERTEX_SHADER)
            fs = self.compile_shader(FRAGMENT_SHADER, GL.GL_FRAGMENT_SHADER)

            program = GL.glCreateProgram()
            GL.glAttachShader(program, vs)
            GL.glAttachShader(program, fs)
            GL.glLinkProgram(program)

            if not GL.glGetProgramiv(program, GL.GL_LINK_STATUS):
                log = GL.glGetProgramInfoLog(program)
                if isinstance(log, bytes):
                    log = log.decode("utf-8", "replace")

                GL.glDeleteProgram(program)
                raise RuntimeError(f"Shader link failed: {log}")

            GL.glDeleteShader(vs)
            GL.glDeleteShader(fs)

            vao = GL.glGenVertexArrays(1)
            GL.glBindVertexArray(vao)

            vbo = GL.glGenBuffers(1)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)

            vertices = [
                -1.0, -1.0,
                1.0, -1.0,
                -1.0, 1.0,
                -1.0, 1.0,
                1.0, -1.0,
                1.0, 1.0,
            ]

            data = (GL.GLfloat * len(vertices))(*vertices)
            GL.glBufferData(GL.GL_ARRAY_BUFFER, ctypes.sizeof(data), data, GL.GL_STATIC_DRAW)

            GL.glVertexAttribPointer(
                0,
                2,
                GL.GL_FLOAT,
                GL.GL_FALSE,
                2 * ctypes.sizeof(GL.GLfloat),
                ctypes.c_void_p(0),
            )
            GL.glEnableVertexAttribArray(0)

            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)

            self.gl_program = program
            self.gl_vao = vao
            self.gl_vbo = vbo

            uniform_names = (
                "u_style",
                "u_bars",
                "u_segments_count",
                "u_thickness",
                "u_shape_rounded",
                "u_position",
                "u_fade_direction",
                "u_fade_amount",
                "u_bloom",
                "u_inner_glow",
                "u_specular_shine",
                "u_stardust",
                "u_idle_time",
                "u_data",
                "u_ramp",
                "u_ramp_len",
                "u_resolution_x",
                "u_resolution_y",
                "u_content_height",
            )

            self.gl_uniforms = {
                name: GL.glGetUniformLocation(program, name)
                for name in uniform_names
            }

            self.gl_last_bars = None
            self.ramp_dirty = True

            self.log.info("OpenGL renderer realized.")

        except Exception:
            self.log.exception("OpenGL realization failed; falling back to Cairo.")
            self.gl_failed = True
            self.schedule_cairo_fallback()

    def schedule_cairo_fallback(self) -> None:
        if self.fallback_pending:
            return

        self.fallback_pending = True
        GLib.idle_add(self.fallback_to_cairo)

    def fallback_to_cairo(self) -> bool:
        self.fallback_pending = False
        self.setup_window(force_cairo=True)
        self.ensure_tick()
        return False

    def upload_gl_data(self, data: list[float], n: int) -> None:
        if self.gl_data_array is None:
            return

        n = min(MAX_BARS, n)

        for i in range(n):
            self.gl_data_array[i] = float(data[i])

        if self.gl_last_bars != n:
            for i in range(n, MAX_BARS):
                self.gl_data_array[i] = 0.0
            self.gl_last_bars = n

    def upload_gl_ramp(self) -> None:
        if self.gl_ramp_array is None:
            return

        ramp = self.get_color_ramp()
        flat: list[float] = []

        for color in ramp[:RAMP_LEN]:
            flat.extend(color)

        while len(flat) < RAMP_LEN * 4:
            flat.append(1.0)

        for i, value in enumerate(flat[: RAMP_LEN * 4]):
            self.gl_ramp_array[i] = float(value)

        self.ramp_dirty = False

    def set_uniform_int(self, name: str, value: int) -> None:
        loc = self.gl_uniforms.get(name, -1)
        if loc != -1:
            GL.glUniform1i(loc, int(value))

    def set_uniform_float(self, name: str, value: float) -> None:
        loc = self.gl_uniforms.get(name, -1)
        if loc != -1:
            GL.glUniform1f(loc, float(value))

    def on_gl_render(self, widget: Gtk.GLArea, context: Any) -> bool:
        try:
            widget.make_current()

            GL.glClearColor(0.0, 0.0, 0.0, 0.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)

            if not self.config.enabled:
                return True

            if self.gl_failed or self.gl_program is None:
                return True

            w = float(widget.get_allocated_width())
            h = float(widget.get_allocated_height())

            if w <= 1.0 or h <= 1.0:
                return True

            data = self.prepare_render_data()
            if data is None:
                return True

            n = min(MAX_BARS, self.config.bars)

            GL.glUseProgram(self.gl_program)

            self.upload_gl_data(data, n)

            if self.ramp_dirty:
                self.upload_gl_ramp()

            style_map = {
                Style.BARS: 0,
                Style.SEGMENTS: 1,
                Style.DOTS: 2,
                Style.WAVE: 3,
                Style.LINE: 4,
                Style.MONITOR: 4,
                Style.RADIAL: 5,
                Style.CIRCLE: 6,
                Style.SPECTRUM: 7,
                Style.AURORA: 8,
                Style.PSYCHEDELIC: 9,
                Style.KALEIDOSCOPE: 10,
                Style.LIGHTNING: 12,
                Style.PERIMETER: 13,
            }

            pos_map = {
                Position.TOP: 0,
                Position.CENTER: 1,
                Position.BOTTOM: 2,
            }

            fade_map = {
                FadeDirection.FADE_TO_BASE: 0,
                FadeDirection.FADE_TO_TIP: 1,
                FadeDirection.SOLID: 2,
            }

            self.set_uniform_int("u_style", style_map.get(self.config.style, 0))
            self.set_uniform_int("u_bars", n)
            self.set_uniform_int("u_segments_count", self.config.segments_count)
            self.set_uniform_float("u_thickness", self.config.thickness)
            self.set_uniform_int("u_shape_rounded", 1 if self.config.shape_rounded else 0)
            self.set_uniform_int("u_position", pos_map.get(self.config.position, 0))
            self.set_uniform_int("u_fade_direction", fade_map.get(self.config.fade_direction, 0))
            self.set_uniform_float("u_fade_amount", self.config.fade_amount)
            self.set_uniform_float("u_bloom", self.config.bloom)
            self.set_uniform_float("u_inner_glow", self.config.inner_glow)
            self.set_uniform_float("u_specular_shine", self.config.specular_shine)
            self.set_uniform_float("u_stardust", self.config.stardust)
            self.set_uniform_float("u_idle_time", float(self.idle_time))
            self.set_uniform_float("u_resolution_x", w)
            self.set_uniform_float("u_resolution_y", h)
            self.set_uniform_float("u_content_height", float(self.content_height) if self.content_height > 0 else h)
            self.set_uniform_int("u_ramp_len", RAMP_LEN)

            data_loc = self.gl_uniforms.get("u_data", -1)
            if data_loc != -1 and self.gl_data_array is not None:
                GL.glUniform1fv(data_loc, MAX_BARS, self.gl_data_array)

            ramp_loc = self.gl_uniforms.get("u_ramp", -1)
            if ramp_loc != -1 and self.gl_ramp_array is not None:
                GL.glUniform4fv(ramp_loc, RAMP_LEN, self.gl_ramp_array)

            GL.glBindVertexArray(self.gl_vao)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)

        except Exception:
            self.log.exception("OpenGL render failed; falling back to Cairo.")
            self.gl_failed = True
            self.schedule_cairo_fallback()

        return True

    # -------------------------------------------------------------------------
    # Window management
    # -------------------------------------------------------------------------

    def apply_css(self) -> None:
        if self.css_added:
            return

        try:
            provider = Gtk.CssProvider()
            provider.load_from_data(
                b"window, drawingarea, glarea { background-color: transparent; background: transparent; }"
            )

            screen = Gdk.Screen.get_default()
            if screen is not None:
                Gtk.StyleContext.add_provider_for_screen(
                    screen,
                    provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )
                self.css_added = True

        except Exception as exc:
            self.log.warning("Could not apply CSS: %s", exc)

    def destroy_window(self) -> None:
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass

        self.window = None
        self.widget = None
        self.use_gl = False

    def setup_window(self, force_cairo: bool = False) -> Gtk.Widget | None:
        self.destroy_window()

        if not self.config.enabled:
            return None

        try:
            self.apply_css()

            display = Gdk.Display.get_default()
            if display is None:
                self.log.error("No Gdk display available.")
                return None

            monitor = display.get_primary_monitor() or display.get_monitor(0)
            if monitor is None:
                self.log.error("No monitor available.")
                return None

            geom = monitor.get_geometry()

            window = Gtk.Window()
            self.window = window

            window.set_app_paintable(True)
            window.set_can_focus(False)
            window.set_accept_focus(False)
            window.set_decorated(False)
            window.set_resizable(False)

            screen = window.get_screen()
            if screen is not None:
                visual = screen.get_rgba_visual()
                if visual is not None:
                    window.set_visual(visual)

            GtkLayerShell.init_for_window(window)
            GtkLayerShell.set_namespace(window, LAYER_NAMESPACE)

            self.apply_hyprland_rules()

            GtkLayerShell.set_layer(
                window,
                GtkLayerShell.Layer.TOP if self.is_overlay else GtkLayerShell.Layer.BOTTOM,
            )

            GtkLayerShell.set_exclusive_zone(window, -1)

            GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.LEFT, True)
            GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.RIGHT, True)

            style = self.config.style
            pos = self.config.position

            self.content_height = max(1, int(geom.height * self.config.height_pct))
            win_w = geom.width
            win_h = geom.height
            is_fullscreen_style = style in (Style.RADIAL, Style.CIRCLE, Style.PSYCHEDELIC, Style.KALEIDOSCOPE, Style.PERIMETER)

            if is_fullscreen_style:
                GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP, True)
                GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.BOTTOM, False)
                GtkLayerShell.set_margin(window, GtkLayerShell.Edge.TOP, 0)

            else:

                if pos == Position.TOP:
                    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP, True)
                    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.BOTTOM, False)
                    GtkLayerShell.set_margin(window, GtkLayerShell.Edge.TOP, 0)

                elif pos == Position.BOTTOM:
                    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP, False)
                    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.BOTTOM, True)
                    GtkLayerShell.set_margin(window, GtkLayerShell.Edge.BOTTOM, 0)

                else:
                    margin_top = max(0, (geom.height - win_h) // 2)
                    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP, True)
                    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.BOTTOM, False)
                    GtkLayerShell.set_margin(window, GtkLayerShell.Edge.TOP, margin_top)

            window.input_shape_combine_region(cairo.Region())
            window.set_size_request(win_w, win_h)

            if not HAS_OPENGL and self.config.gpu_acceleration and not force_cairo:
                self.log.warning("python-opengl is missing. Falling back to Cairo CPU rendering.")
                notify_user(
                    "Dusky Visualizer Notice",
                    "python-opengl is missing. GPU acceleration disabled, using Cairo CPU rendering. Install with: sudo pacman -S python-opengl",
                    urgency="normal",
                )

            use_gl = (
                HAS_OPENGL
                and self.config.gpu_acceleration
                and not force_cairo
                and not self.gl_failed
            )

            if use_gl:
                try:
                    area = Gtk.GLArea()
                    area.set_required_version(3, 3)
                    area.set_has_alpha(True)

                    area.connect("realize", self.on_gl_realize)
                    area.connect("render", self.on_gl_render)

                    window.add(area)
                    window.show_all()

                    self.widget = area
                    self.use_gl = True

                    self.log.info("GPU OpenGL renderer attached.")
                    return area

                except Exception:
                    self.log.exception("GLArea initialization failed; falling back to Cairo.")
                    self.gl_failed = True

                    try:
                        for child in window.get_children():
                            window.remove(child)
                    except Exception:
                        pass

            da = Gtk.DrawingArea()
            da.connect("draw", self.on_draw)

            window.add(da)
            window.show_all()

            self.widget = da
            self.use_gl = False

            self.log.info("Software Cairo renderer attached.")
            return da

        except Exception:
            self.log.exception("Window setup failed.")
            self.destroy_window()
            return None

    # -------------------------------------------------------------------------
    # Signals
    # -------------------------------------------------------------------------

    def install_signal_handlers(self) -> None:
        try:
            if GLibUnix is not None:
                signal_add = GLibUnix.signal_add
            else:
                signal_add = GLib.unix_signal_add

            signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.on_quit_signal)
            signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.on_quit_signal)
            signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self.on_reload_signal)
        except Exception:
            self.log.exception("Failed installing UNIX signal handlers.")

    def on_quit_signal(self, *args: Any) -> bool:
        self.log.info("Received quit signal.")
        Gtk.main_quit()
        return False

    def on_reload_signal(self, *args: Any) -> bool:
        self.log.info("Received SIGUSR1; reloading.")
        self.queue_reload()
        return True

    def remove_glib_source(self, source_id: int | None) -> None:
        if source_id is None:
            return

        try:
            ctx = GLib.MainContext.default()
            if ctx.find_source_by_id(source_id) is not None:
                GLib.source_remove(source_id)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Shutdown
    # -------------------------------------------------------------------------

    def shutdown(self) -> None:
        self.log.info("Shutting down.")

        self.stop_cava()
        self.remove_tick()

        if self.config_monitor is not None:
            try:
                self.config_monitor.cancel()
            except Exception:
                pass
            self.config_monitor = None

        if self.colors_monitor is not None:
            try:
                self.colors_monitor.cancel()
            except Exception:
                pass
            self.colors_monitor = None

        if self.colors_retry_source is not None:
            self.remove_glib_source(self.colors_retry_source)
            self.colors_retry_source = None

        if self.reload_debounce is not None:
            self.remove_glib_source(self.reload_debounce)
            self.reload_debounce = None

        if self.fifo_watch is not None:
            self.remove_glib_source(self.fifo_watch)
            self.fifo_watch = None

        if self.fifo_fd is not None:
            try:
                os.close(self.fifo_fd)
            except OSError:
                pass
            self.fifo_fd = None

        self.delete_gl_resources()
        self.destroy_window()
        self.release_lock()


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    app = Visualizer()

    app.acquire_lock()
    app.dependency_report()

    if not app.check_display():
        app.shutdown()
        return 1

    app.load_initial()
    app.init_fifo_ipc()
    app.init_file_monitors()
    app.apply_config_changes(None)
    app.install_signal_handlers()
    app.ensure_tick()

    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
