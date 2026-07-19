#!/usr/bin/env python3
import ctypes
import json
import math
import os
import signal
import subprocess
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
import cairo
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell, Pango

# Optional PyOpenGL dependency check for GPU acceleration.
# If python-opengl is installed, GL commands are available and HAS_OPENGL = True.
# Otherwise, the application safely falls back to the Cairo software renderer.
try:
    import OpenGL.GL as GL

    HAS_OPENGL = True
except ImportError:
    HAS_OPENGL = False

CONFIG_FILE = os.path.expanduser(
    "~/.config/dusky/settings/way_layers/visualizer/visualizer.json"
)
COLORS_FILE = os.path.expanduser(
    "~/.config/matugen/generated/dusky_visualizer_colors.json"
)
CTL_FILE = os.path.expanduser(
    "~/.config/dusky/settings/way_layers/visualizer/visualizer.ctl"
)

# Default config
config = {
    "enabled": True,
    "position": "top",
    "style": "bars",
    "bars": 64,
    "fps": 60,
    "height_pct": 0.20,
    "smoothing": 0.5,
    "gain": 1.5,
    "mirror": False,
    "shape_rounded": True,
    "thickness": 0.5,
    "bloom": 0.2,
    "reflection": 0.0,
    "idle_wave": True,
    "fade_direction": "fade_to_base",
    "fade_amount": 1.0,
    "glass_blur": True,
    "segments_count": 16,
    "cava_noise_reduction": 0.77,
    "cava_lower_freq": 50,
    "cava_upper_freq": 10000,
    "gpu_acceleration": True,
}

colors = {
    "c1": "#ffb4ac",
    "c2": "#f5b9a1",
    "c3": "#fbb983",
    "c4": "#93000e",
    "c5": "#663c2a",
    "c6": "#693c10",
    "accent": "#ffb4ac",
}

cava_proc = None
cava_data = []
smoothed_data = []
is_overlay = False
window = None
has_rendered_idle_clear = False
use_gl_renderer = False

# OpenGL objects
gl_shader_program = None
gl_quad_vao = None
gl_quad_vbo = None


def load_json(path, default_dict):
    """
    Safely load a JSON configuration file into a dictionary.
    Only keys that already exist in default_dict will be updated, preserving schema defaults.
    """
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
                for k, v in data.items():
                    if k in default_dict:
                        default_dict[k] = v
    except Exception as e:
        print(f"Error loading json {path}: {e}")


def save_json(path, data_dict):
    """
    Serialize and save a dictionary to disk as JSON, creating intermediate directories if needed.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data_dict, f, indent=4)
    except:
        pass


def load_config():
    """
    Load both the primary visualizer configuration and external color definitions from disk.
    Ensures internal CAVA data buffers match the configured number of frequency bars.
    """
    global config, colors, has_rendered_idle_clear
    load_json(CONFIG_FILE, config)
    load_json(COLORS_FILE, colors)
    if "bars" not in config:
        config["bars"] = 64
    global cava_data, smoothed_data
    if len(cava_data) != config["bars"]:
        cava_data = [0.0] * config["bars"]
        smoothed_data = [0.0] * config["bars"]
    has_rendered_idle_clear = False


def generate_cava_config():
    """
    Generate a temporary CAVA configuration file tailored to our current framerate,
    bar count, cutoff frequencies, and noise reduction settings.
    Returns the file path to the generated configuration.
    """
    conf_dir = os.path.expanduser("~/.cache/dusky")
    os.makedirs(conf_dir, exist_ok=True)
    conf_path = os.path.join(conf_dir, "cava_visualizer.conf")
    with open(conf_path, "w") as f:
        f.write(f"""
[general]
framerate = {config["fps"]}
bars = {config["bars"]}
lower_cutoff_freq = {config["cava_lower_freq"]}
higher_cutoff_freq = {config["cava_upper_freq"]}

[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = 1000

[smoothing]
integral = {config["cava_noise_reduction"]}
monstercat = 1
gravity = 100
ignore = 0
noise_reduction = {config["cava_noise_reduction"]}
        """)
    return conf_path


def start_cava():
    """
    Terminate any existing CAVA process, generate a new configuration file, and spawn
    a new background CAVA process. A daemon thread (`cava_reader`) continually reads raw
    ASCII frequency bars from stdout and queues UI updates on the main GTK loop.
    """
    global cava_proc
    if cava_proc:
        try:
            cava_proc.terminate()
            cava_proc.wait(timeout=1)
        except:
            try:
                cava_proc.kill()
            except:
                pass

    conf_path = generate_cava_config()
    cava_proc = subprocess.Popen(
        ["cava", "-p", conf_path], stdout=subprocess.PIPE, text=True, bufsize=1
    )

    def cava_reader():
        while cava_proc and cava_proc.poll() is None:
            line = cava_proc.stdout.readline()
            if not line:
                break
            parts = line.strip().split(";")[:-1]
            if len(parts) == config["bars"]:
                new_data = []
                for p in parts:
                    try:
                        val = int(p) / 1000.0
                        new_data.append(min(1.0, val * config["gain"]))
                    except:
                        new_data.append(0.0)
                # Skip triggering UI redraws if audio is completely idle and already cleared
                if not any(v > 0.001 for v in new_data) and has_rendered_idle_clear:
                    continue
                GLib.idle_add(update_cava_data, new_data)

    threading.Thread(target=cava_reader, daemon=True).start()


def update_cava_data(new_data):
    """
    GTK main loop idle callback triggered by `cava_reader`.
    Updates the global `cava_data` array and requests a render/draw pass.
    """
    global cava_data, has_rendered_idle_clear
    cava_data = new_data
    if any(v > 0.001 for v in new_data) and has_rendered_idle_clear:
        has_rendered_idle_clear = False
        if da_widget and config["enabled"]:
            if use_gl_renderer:
                da_widget.queue_render()
            else:
                da_widget.queue_draw()
    return False


def hex_to_rgba(hex_str, alpha=1.0):
    """
    Convert a hex color string (#RRGGBB or #RRGGBBAA) into an (R, G, B, A) tuple
    normalized to the range [0.0, 1.0] for Cairo/OpenGL rendering.
    """
    hex_str = hex_str.lstrip("#")
    if len(hex_str) == 6:
        r, g, b = tuple(int(hex_str[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
        return (r, g, b, alpha)
    elif len(hex_str) == 8:
        r, g, b, a = tuple(int(hex_str[i : i + 2], 16) / 255.0 for i in (0, 2, 4, 6))
        return (r, g, b, a * alpha)
    return (1.0, 1.0, 1.0, alpha)


def get_color_ramp():
    """
    Retrieve an ordered list of RGBA color stops from the loaded color palette,
    used to create smooth multi-color gradients across visualizer bars/waves.
    """
    return [
        hex_to_rgba(colors.get("c1", "#ffb4ac")),
        hex_to_rgba(colors.get("c3", "#fbb983")),
        hex_to_rgba(colors.get("c2", "#f5b9a1")),
        hex_to_rgba(colors.get("c6", "#693c10")),
        hex_to_rgba(colors.get("c4", "#93000e")),
        hex_to_rgba(colors.get("c5", "#663c2a")),
    ]


def interpolate_color(ramp, t):
    """
    Linearly interpolate between color stops in `ramp` given a normalized position `t` [0.0, 1.0].
    """
    if len(ramp) == 0:
        return (1, 1, 1, 1)
    if len(ramp) == 1:
        return ramp[0]
    n = len(ramp)
    t = max(0.0, min(0.999999, t))
    x = t * (n - 1)
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


def apply_gradient(cr, color, x_base, y_base, x_tip, y_tip):
    """
    Create and apply a Cairo LinearGradient from base to tip using the specified base `color`
    and configured fade direction/amount.
    """
    fade = config.get("fade_direction", "fade_to_base")
    amt = config.get("fade_amount", 1.0)

    r, g, b, a = color
    lr, lg, lb = min(1.0, r * 1.3), min(1.0, g * 1.3), min(1.0, b * 1.3)
    alpha_faded = a * (1.0 - amt)

    pat = cairo.LinearGradient(x_base, y_base, x_tip, y_tip)

    if fade == "solid" or amt == 0.0:
        pat.add_color_stop_rgba(0, r, g, b, a)
        pat.add_color_stop_rgba(1, lr, lg, lb, a)
    else:
        if fade == "fade_to_base":
            pat.add_color_stop_rgba(0.0, r, g, b, alpha_faded)
            pat.add_color_stop_rgba(0.45, r, g, b, a * 0.8)
            pat.add_color_stop_rgba(1.0, lr, lg, lb, a)
        elif fade == "fade_to_tip":
            pat.add_color_stop_rgba(0.0, lr, lg, lb, a)
            pat.add_color_stop_rgba(0.55, r, g, b, a * 0.8)
            pat.add_color_stop_rgba(1.0, r, g, b, alpha_faded)

    cr.set_source(pat)


idle_time = 0


# ==================== Cairo Renderer (Fallback) ====================
def on_draw(widget, cr):
    """
    GTK DrawingArea callback for software rendering using Cairo.
    Applies temporal exponential smoothing to raw audio data, computes optional
    idle wave animations during silence, and delegates shape rendering to `draw_style()`.
    """
    global idle_time, has_rendered_idle_clear
    w = widget.get_allocated_width()
    h = widget.get_allocated_height()

    cr.set_operator(cairo.OPERATOR_CLEAR)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)

    if not config["enabled"]:
        return False

    ramp = get_color_ramp()
    n = config["bars"]
    if n == 0:
        return False

    is_idle = True
    alpha = config["smoothing"]
    for i in range(n):
        target = cava_data[i] if i < len(cava_data) else 0.0
        if target > 0.01:
            is_idle = False
        smoothed_data[i] = smoothed_data[i] * alpha + target * (1.0 - alpha)

    if is_idle:
        idle_time += 1.0 / config["fps"]
    else:
        idle_time = 0

    render_data = smoothed_data[:]

    if is_idle and config["idle_wave"]:
        for i in range(n):
            wave = math.sin(idle_time * 2.0 + (i / n) * math.pi * 4.0) * 0.5 + 0.5
            render_data[i] = wave * 0.15
    elif is_idle and not config["idle_wave"]:
        if not any(v > 0.0001 for v in render_data):
            has_rendered_idle_clear = True
            return False

    if config["mirror"]:
        half = n // 2
        for i in range(half):
            render_data[n - 1 - i] = render_data[i]

    draw_style(cr, w, h, render_data, n, ramp)
    return False


def draw_style(cr, w, h, data, n, ramp):
    style = config.get("style", "bars")
    bar_w = w / n
    gap = bar_w * (1.0 - config["thickness"])
    bar_w -= gap

    pos = config.get("position", "top")

    if style == "bars":
        for i in range(n):
            val = data[i] * h
            x = i * (bar_w + gap) + gap / 2

            if pos == "top":
                y_base, y_tip = 0, val
            elif pos == "center":
                y_base, y_tip = h / 2, h / 2 - val / 2
            else:
                y_base, y_tip = h, h - val

            color = interpolate_color(ramp, i / (n - 1))

            cr.new_sub_path()
            if config["shape_rounded"]:
                radius = bar_w / 2
                if val < radius * 2:
                    val = radius * 2
                if pos == "center":
                    cr.arc(x + radius, y_base + val / 2 - radius, radius, 0, math.pi)
                    cr.arc(x + radius, y_tip + radius, radius, math.pi, math.pi * 2)
                else:
                    cr.arc(
                        x + radius,
                        y_base + (radius if pos == "top" else -radius),
                        radius,
                        math.pi,
                        math.pi * 2,
                    )
                    cr.arc(
                        x + radius,
                        y_tip + (radius if pos == "bottom" else -radius),
                        radius,
                        0,
                        math.pi,
                    )
            else:
                y_rect = y_base if pos == "top" else y_tip
                rect_h = val if pos != "center" else val / 2
                cr.rectangle(x, y_rect, bar_w, val)
            cr.close_path()

            apply_gradient(cr, color, x, y_base, x, y_tip)
            cr.fill()

    elif style == "segments":
        seg_n = config.get("segments_count", 16)
        seg_gap = max(1.5, h / seg_n * 0.26)
        pitch = h / seg_n

        for i in range(n):
            val = data[i] * h
            lit = int(math.ceil(val / max(1, h) * seg_n))
            x = i * (bar_w + gap) + gap / 2
            color = interpolate_color(ramp, i / (n - 1))

            for cell in range(lit):
                cell_h = max(2, pitch - seg_gap)

                if pos == "top":
                    y = cell * pitch + seg_gap / 2
                elif pos == "center":
                    y = h / 2 - lit * pitch / 2 + cell * pitch + seg_gap / 2
                else:
                    y = h - (cell + 1) * pitch + seg_gap / 2

                cr.set_source_rgba(
                    color[0],
                    color[1],
                    color[2],
                    color[3] * (0.4 + 0.6 * (cell / max(1, lit - 1))),
                )
                if config["shape_rounded"]:
                    radius = min(cell_h, bar_w) * 0.35
                    cr.arc(x + radius, y + radius, radius, math.pi, math.pi * 1.5)
                    cr.arc(
                        x + bar_w - radius,
                        y + radius,
                        radius,
                        math.pi * 1.5,
                        math.pi * 2,
                    )
                    cr.arc(
                        x + bar_w - radius,
                        y + cell_h - radius,
                        radius,
                        0,
                        math.pi * 0.5,
                    )
                    cr.arc(
                        x + radius, y + cell_h - radius, radius, math.pi * 0.5, math.pi
                    )
                    cr.fill()
                else:
                    cr.rectangle(x, y, bar_w, cell_h)
                    cr.fill()

    elif style == "wave":
        points = []
        for i in range(n):
            val = data[i] * h
            x = i * (w / n) + (w / n) / 2
            y_tip = (
                val
                if pos == "top"
                else (h / 2 - val / 2 if pos == "center" else h - val)
            )
            points.append((x, y_tip))

        if len(points) >= 2:
            base_y = 0 if pos == "top" else (h / 2 if pos == "center" else h)

            cr.move_to(0, base_y)
            cr.line_to(0, points[0][1])
            cr.line_to(points[0][0], points[0][1])

            for i in range(len(points) - 1):
                x0, y0 = points[i]
                x1, y1 = points[i + 1]
                cx = (x0 + x1) / 2
                cr.curve_to(cx, y0, cx, y1, x1, y1)

            cr.line_to(w, points[-1][1])
            cr.line_to(w, base_y)
            cr.close_path()

            pat = cairo.LinearGradient(
                w / 2,
                base_y,
                w / 2,
                h / 2 if pos == "center" else (h if pos == "top" else 0),
            )
            amt = config.get("fade_amount", 1.0)
            for i, c in enumerate(ramp):
                pat.add_color_stop_rgba(
                    i / (len(ramp) - 1), c[0], c[1], c[2], c[3] * (1.0 - amt * 0.8)
                )
            cr.set_source(pat)
            cr.fill()

    elif style == "line" or style == "monitor":
        cr.set_line_width(config["thickness"] * 5 + 1)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        points = []
        for i in range(n):
            val = data[i] * h
            x = i * (w / n) + (w / n) / 2
            y = (
                val
                if pos == "top"
                else (h / 2 - val / 2 if pos == "center" else h - val)
            )
            points.append((x, y))

        if len(points) >= 2:
            cr.move_to(points[0][0], points[0][1])
            for i in range(len(points) - 1):
                x0, y0 = points[i]
                x1, y1 = points[i + 1]
                cx = (x0 + x1) / 2
                cr.curve_to(cx, y0, cx, y1, x1, y1)

            pat = cairo.LinearGradient(0, 0, w, 0)
            for i, c in enumerate(ramp):
                pat.add_color_stop_rgba(i / (len(ramp) - 1), *c)
            cr.set_source(pat)
            cr.stroke()

    elif style == "dots":
        for i in range(n):
            val = data[i] * h
            x = i * (bar_w + gap) + gap / 2 + bar_w / 2
            y = (
                val
                if pos == "top"
                else (
                    h / 2 - val / 2 + (val / 2 if val > 0 else 0)
                    if pos == "center"
                    else h - val
                )
            )
            cr.set_source_rgba(*interpolate_color(ramp, i / (n - 1)))
            radius = bar_w / 2
            cr.arc(x, y, radius, 0, math.pi * 2)
            cr.fill()

    elif style in ["radial", "circle"]:
        cx, cy = w / 2, h / 2
        min_dim = min(w, h)
        ring_r = min_dim * 0.15
        max_len = min_dim * 0.3

        if style == "radial":
            avg_beat = sum(data[: max(1, n // 4)]) / max(1, n // 4) if n > 0 else 0
            ring_r = min_dim * 0.15 * (1 + 0.25 * avg_beat)
            arc_w = max(2, (2 * math.pi * ring_r / max(1, n)) * config["thickness"])
            for i in range(n):
                val = max(2, max_len * data[i])
                ang = (i / n) * math.pi * 2 + math.pi / 2

                cr.save()
                cr.translate(cx, cy)
                cr.rotate(ang)

                color = interpolate_color(ramp, i / (n - 1))

                if config["shape_rounded"]:
                    radius = arc_w / 2
                    cr.arc(
                        -arc_w / 2 + radius,
                        ring_r + radius,
                        radius,
                        math.pi,
                        math.pi * 1.5,
                    )
                    cr.arc(
                        arc_w / 2 - radius,
                        ring_r + radius,
                        radius,
                        math.pi * 1.5,
                        math.pi * 2,
                    )
                    cr.arc(
                        arc_w / 2 - radius,
                        ring_r + val - radius,
                        radius,
                        0,
                        math.pi * 0.5,
                    )
                    cr.arc(
                        -arc_w / 2 + radius,
                        ring_r + val - radius,
                        radius,
                        math.pi * 0.5,
                        math.pi,
                    )
                else:
                    cr.rectangle(-arc_w / 2, ring_r, arc_w, val)

                apply_gradient(cr, color, 0, ring_r, 0, ring_r + val)
                cr.fill()
                cr.restore()

            cr.set_line_width(2)
            cr.set_source_rgba(ramp[0][0], ramp[0][1], ramp[0][2], 0.4)
            cr.arc(cx, cy, ring_r, 0, math.pi * 2)
            cr.stroke()

        elif style == "circle":
            px, py = [], []
            for i in range(n):
                ang = (i / n) * math.pi * 2 + math.pi / 2
                r = ring_r + max_len * data[i]
                px.append(cx + math.cos(ang) * r)
                py.append(cy + math.sin(ang) * r)

            if len(px) >= 2:
                cr.move_to((px[-1] + px[0]) / 2, (py[-1] + py[0]) / 2)
                for k in range(n):
                    nx = (k + 1) % n
                    ctrl_x, ctrl_y = px[k], py[k]
                    end_x, end_y = (px[k] + px[nx]) / 2, (py[k] + py[nx]) / 2
                    cr.curve_to(ctrl_x, ctrl_y, ctrl_x, ctrl_y, end_x, end_y)
                cr.close_path()

                cr.set_line_width(3)
                pat = cairo.LinearGradient(
                    cx - ring_r - max_len, 0, cx + ring_r + max_len, 0
                )
                amt = config.get("fade_amount", 1.0)
                for i, c in enumerate(ramp):
                    pat.add_color_stop_rgba(
                        i / (len(ramp) - 1), c[0], c[1], c[2], c[3] * (1.0 - amt * 0.6)
                    )
                cr.set_source(pat)
                cr.fill_preserve()

                cr.set_source_rgba(ramp[-1][0], ramp[-1][1], ramp[-1][2], 0.8)
                cr.stroke()


# ==================== GPU OpenGL Renderer ====================
vertex_shader_code = """
#version 330 core
layout (location = 0) in vec2 aPos;
out vec2 uv;
void main() {
    uv = vec2(aPos.x * 0.5 + 0.5, 1.0 - (aPos.y * 0.5 + 0.5));
    gl_Position = vec4(aPos, 0.0, 1.0);
}
"""

fragment_shader_code = """
#version 330 core
in vec2 uv;
out vec4 FragColor;

uniform int u_style; // 0: bars, 1: segments, 2: dots, 3: wave, 4: line, 5: radial, 6: circle
uniform int u_bars;
uniform int u_segments_count;
uniform float u_thickness;
uniform bool u_shape_rounded;
uniform int u_position; // 0: top, 1: center, 2: bottom
uniform int u_fade_direction; // 0: fade_to_base, 1: fade_to_tip, 2: solid
uniform float u_fade_amount;
uniform float u_data[64];
uniform vec4 u_ramp[6];
uniform int u_ramp_len;
uniform float u_resolution_x;
uniform float u_resolution_y;

#define PI 3.14159265359

float sdRoundedBox(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + r;
    return min(max(q.x, q.y), 0.0) + length(max(q, 0.0)) - r;
}

vec4 get_ramp_color(float t) {
    if (u_ramp_len <= 1) return u_ramp[0];
    t = clamp(t, 0.0, 0.999999);
    float x = t * float(u_ramp_len - 1);
    int idx = int(floor(x));
    float f = x - float(idx);
    return mix(u_ramp[idx], u_ramp[idx + 1], f);
}

vec4 apply_grad(vec4 base_col, float norm_y) {
    norm_y = clamp(norm_y, 0.0, 1.0);
    vec3 tip_rgb = min(vec3(1.0), base_col.rgb * 1.3);
    float alpha_faded = base_col.a * (1.0 - u_fade_amount);

    if (u_fade_direction == 2 || u_fade_amount == 0.0) {
        return mix(base_col, vec4(tip_rgb, base_col.a), norm_y);
    } else if (u_fade_direction == 0) { // fade_to_base
        if (norm_y < 0.45) {
            return mix(vec4(base_col.rgb, alpha_faded), vec4(base_col.rgb, base_col.a * 0.8), norm_y / 0.45);
        } else {
            return mix(vec4(base_col.rgb, base_col.a * 0.8), vec4(tip_rgb, base_col.a), (norm_y - 0.45) / 0.55);
        }
    } else { // fade_to_tip
        if (norm_y < 0.55) {
            return mix(vec4(tip_rgb, base_col.a), vec4(base_col.rgb, base_col.a * 0.8), norm_y / 0.55);
        } else {
            return mix(vec4(base_col.rgb, base_col.a * 0.8), vec4(base_col.rgb, alpha_faded), (norm_y - 0.55) / 0.45);
        }
    }
}

float get_val(int i) {
    if (u_style == 5 || u_style == 6) {
        int idx = (i % u_bars + u_bars) % u_bars;
        return u_data[idx];
    }
    if (i < 0) return u_data[0];
    if (i >= u_bars) return u_data[u_bars - 1];
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
    return 0.5 * ((2.0 * p1) + (-p0 + p2) * t + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t * t + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t * t * t);
}

void main() {
    vec2 pos = vec2(uv.x * u_resolution_x, uv.y * u_resolution_y);

    if (u_style == 3 || u_style == 4) { // wave or line
        float val = max(0.0, get_smooth_val(uv.x));
        vec4 color = get_ramp_color(uv.x);

        float curve_y, norm_y;
        if (u_position == 0) { // top
            curve_y = val * u_resolution_y;
            norm_y = clamp(pos.y / max(1.0, curve_y), 0.0, 1.0);
            if (u_style == 3) {
                if (pos.y > curve_y) discard;
                FragColor = apply_grad(color, norm_y);
                float edge_d = curve_y - pos.y;
                if (edge_d <= 3.0) {
                    vec3 edge_rgb = min(vec3(1.0), color.rgb * 1.25);
                    float alpha = smoothstep(3.0, 0.0, edge_d);
                    FragColor.rgb = mix(FragColor.rgb, edge_rgb, alpha);
                }
            } else {
                float lw = u_thickness * 5.0 + 1.0;
                float d = abs(pos.y - curve_y);
                if (d > lw) discard;
                FragColor = vec4(color.rgb, color.a * smoothstep(lw, lw - 1.5, d));
            }
        } else if (u_position == 2) { // bottom
            curve_y = u_resolution_y - val * u_resolution_y;
            norm_y = clamp((u_resolution_y - pos.y) / max(1.0, val * u_resolution_y), 0.0, 1.0);
            if (u_style == 3) {
                if (pos.y < curve_y) discard;
                FragColor = apply_grad(color, norm_y);
                float edge_d = pos.y - curve_y;
                if (edge_d <= 3.0) {
                    vec3 edge_rgb = min(vec3(1.0), color.rgb * 1.25);
                    float alpha = smoothstep(3.0, 0.0, edge_d);
                    FragColor.rgb = mix(FragColor.rgb, edge_rgb, alpha);
                }
            } else {
                float lw = u_thickness * 5.0 + 1.0;
                float d = abs(pos.y - curve_y);
                if (d > lw) discard;
                FragColor = vec4(color.rgb, color.a * smoothstep(lw, lw - 1.5, d));
            }
        } else { // center
            float half_h = val * u_resolution_y * 0.5;
            norm_y = clamp(abs(pos.y - u_resolution_y * 0.5) / max(1.0, half_h), 0.0, 1.0);
            if (u_style == 3) {
                if (abs(pos.y - u_resolution_y * 0.5) > half_h) discard;
                FragColor = apply_grad(color, norm_y);
                float edge_d = half_h - abs(pos.y - u_resolution_y * 0.5);
                if (edge_d <= 3.0) {
                    vec3 edge_rgb = min(vec3(1.0), color.rgb * 1.25);
                    float alpha = smoothstep(3.0, 0.0, edge_d);
                    FragColor.rgb = mix(FragColor.rgb, edge_rgb, alpha);
                }
            } else {
                float lw = u_thickness * 5.0 + 1.0;
                float d = abs(abs(pos.y - u_resolution_y * 0.5) - half_h);
                if (d > lw) discard;
                FragColor = vec4(color.rgb, color.a * smoothstep(lw, lw - 1.5, d));
            }
        }
        return;
    }

    if (u_style == 5 || u_style == 6) { // radial or circle
        vec2 center = vec2(u_resolution_x * 0.5, u_resolution_y * 0.5);
        float min_dim = min(u_resolution_x, u_resolution_y);
        float ring_r = min_dim * 0.15;
        float max_len = min_dim * 0.3;
        vec2 d_vec = pos - center;
        float dist = length(d_vec);
        float ang = atan(d_vec.y, d_vec.x);
        if (ang < 0.0) ang += 2.0 * PI;

        float norm_idx = mod((ang - PI * 0.5 + 2.0 * PI) / (2.0 * PI), 1.0);
        int bar_idx = int(floor(norm_idx * float(u_bars)));
        if (bar_idx >= u_bars) bar_idx = u_bars - 1;
        float val = u_data[bar_idx];
        vec4 base_color = get_ramp_color(float(bar_idx) / max(1.0, float(u_bars - 1)));

        if (u_style == 5) { // radial
            float avg_beat = (u_data[0] + u_data[1] + u_data[2] + u_data[3]) * 0.25;
            ring_r = ring_r * (1.0 + 0.25 * avg_beat);

            float arc_full_w = (2.0 * PI * ring_r) / float(u_bars);
            float gap = arc_full_w * (1.0 - u_thickness);
            float bar_w = arc_full_w - gap;
            float bar_h = max(2.0, max_len * val);

            float ang_bar_center = ((float(bar_idx) + 0.5) / float(u_bars)) * 2.0 * PI + PI * 0.5;
            vec2 dir = vec2(cos(ang_bar_center), sin(ang_bar_center));
            float radial_y = dot(d_vec, dir) - ring_r;
            float tangent_x = dot(d_vec, vec2(-dir.y, dir.x));

            bool in_bar = !(abs(tangent_x) > bar_w * 0.5 || radial_y < 0.0 || radial_y > bar_h);
            if (in_bar && u_shape_rounded) {
                float r = min(bar_w * 0.5, bar_h * 0.5);
                if (radial_y < r) {
                    if (length(vec2(tangent_x, radial_y - r)) > r) in_bar = false;
                } else if (radial_y > bar_h - r) {
                    if (length(vec2(tangent_x, radial_y - (bar_h - r))) > r) in_bar = false;
                }
            }

            float ring_d = abs(dist - ring_r);
            if (!in_bar && ring_d > 2.0) discard;

            if (in_bar) {
                float norm_r = clamp(radial_y / max(1.0, bar_h), 0.0, 1.0);
                vec4 color = apply_grad(base_color, norm_r);
                if (ring_d <= 2.0) {
                    float alpha = smoothstep(2.0, 0.0, ring_d) * 0.6;
                    color.rgb = mix(color.rgb, u_ramp[0].rgb, alpha);
                }
                FragColor = color;
            } else {
                float alpha = smoothstep(2.0, 0.0, ring_d) * 0.6;
                FragColor = vec4(u_ramp[0].rgb, u_ramp[0].a * alpha);
            }
            return;
        } else { // circle
            float target_r = ring_r + max_len * get_smooth_val(norm_idx);
            if (dist > target_r) discard;
            float span = ring_r + max_len;
            float t = clamp((pos.x - (center.x - span)) / max(1.0, 2.0 * span), 0.0, 1.0);
            vec4 smooth_color = get_ramp_color(t);
            float norm_r = clamp((dist - ring_r) / max(1.0, target_r - ring_r), 0.0, 1.0);
            vec4 color = apply_grad(smooth_color, norm_r);
            float edge_d = target_r - dist;
            if (edge_d <= 3.0) {
                vec3 edge_rgb = min(vec3(1.0), smooth_color.rgb * 1.25);
                float alpha = smoothstep(3.0, 0.0, edge_d);
                color.rgb = mix(color.rgb, edge_rgb, alpha);
            }
            FragColor = vec4(color.rgb, color.a * smoothstep(0.0, 1.5, edge_d));
            return;
        }
    }

    float bar_full_w = u_resolution_x / float(u_bars);
    int bar_idx = int(floor(pos.x / bar_full_w));
    if (bar_idx >= u_bars || bar_idx < 0) discard;

    float gap = bar_full_w * (1.0 - u_thickness);
    float bar_w = bar_full_w - gap;
    float local_x = mod(pos.x, bar_full_w);
    if (local_x < gap * 0.5 || local_x > gap * 0.5 + bar_w) discard;

    float val = u_data[bar_idx];
    if (val <= 0.001) discard;

    vec4 base_color = get_ramp_color(float(bar_idx) / max(1.0, float(u_bars - 1)));

    if (u_style == 2) { // dots
        float center_x = (float(bar_idx) * bar_full_w) + gap * 0.5 + bar_w * 0.5;
        float center_y;
        if (u_position == 0) center_y = val * u_resolution_y;
        else if (u_position == 2) center_y = u_resolution_y - val * u_resolution_y;
        else center_y = u_resolution_y * 0.5;
        float radius = bar_w * 0.5;
        float d = length(pos - vec2(center_x, center_y));
        if (d > radius) discard;
        FragColor = vec4(base_color.rgb, base_color.a * smoothstep(radius, radius - 1.0, d));
        return;
    }

    if (u_style == 1) { // segments
        float pitch = u_resolution_y / float(u_segments_count);
        float seg_gap = max(1.5, pitch * 0.26);
        int lit_cells = int(ceil(val * float(u_segments_count)));

        int cell_idx;
        float local_y;
        if (u_position == 0) { // top
            cell_idx = int(floor(pos.y / pitch));
            local_y = mod(pos.y, pitch);
        } else if (u_position == 2) { // bottom
            cell_idx = int(floor((u_resolution_y - pos.y) / pitch));
            local_y = mod((u_resolution_y - pos.y), pitch);
        } else { // center
            cell_idx = int(floor(abs(pos.y - u_resolution_y * 0.5) * 2.0 / pitch));
            local_y = mod(abs(pos.y - u_resolution_y * 0.5) * 2.0, pitch);
        }

        if (cell_idx >= lit_cells || cell_idx < 0) discard;
        float cell_h = max(2.0, pitch - seg_gap);
        if (local_y < seg_gap * 0.5 || local_y > seg_gap * 0.5 + cell_h) discard;

        float alpha_mul = 0.4 + 0.6 * (float(cell_idx) / max(1.0, float(lit_cells - 1)));
        vec4 color = vec4(base_color.rgb, base_color.a * alpha_mul);

        if (u_shape_rounded) {
            float r = min(cell_h, bar_w) * 0.35;
            vec2 p = vec2(local_x, local_y) - vec2(gap * 0.5 + bar_w * 0.5, seg_gap * 0.5 + cell_h * 0.5);
            float d = sdRoundedBox(p, vec2(bar_w * 0.5 - 0.5, cell_h * 0.5 - 0.5), r);
            if (d > 0.5) discard;
            if (d > -0.5) color.a *= smoothstep(0.5, -0.5, d);
        }
        FragColor = color;
        return;
    }

    // Bars style (u_style == 0)
    float norm_y = 0.0;
    if (u_position == 0) { // top
        if (pos.y > val * u_resolution_y) discard;
        norm_y = pos.y / max(1.0, val * u_resolution_y);
    } else if (u_position == 2) { // bottom
        if (pos.y < u_resolution_y - val * u_resolution_y) discard;
        norm_y = (u_resolution_y - pos.y) / max(1.0, val * u_resolution_y);
    } else { // center
        if (abs(pos.y - u_resolution_y * 0.5) > val * u_resolution_y * 0.5) discard;
        norm_y = abs(pos.y - u_resolution_y * 0.5) / max(1.0, val * u_resolution_y * 0.5);
    }

    vec4 color = apply_grad(base_color, norm_y);
    if (u_shape_rounded) {
        float r = bar_w * 0.5;
        float h_val = val * u_resolution_y;
        if (h_val < r * 2.0) r = h_val * 0.5;
        float d_tip = -1.0;
        if (u_position == 0 && pos.y > h_val - r) {
            d_tip = length(vec2(local_x - (gap * 0.5 + r), pos.y - (h_val - r))) - r;
        } else if (u_position == 2 && pos.y < u_resolution_y - h_val + r) {
            d_tip = length(vec2(local_x - (gap * 0.5 + r), pos.y - (u_resolution_y - h_val + r))) - r;
        }
        if (d_tip > 0.5) discard;
        if (d_tip > -0.5) color.a *= smoothstep(0.5, -0.5, d_tip);
    }
    FragColor = color;
}
"""


def compile_gl_shader(source, shader_type):
    """
    Compile an individual OpenGL shader (vertex or fragment) from source string.
    Raises RuntimeError containing the shader info log upon compilation failure.
    """
    shader = GL.glCreateShader(shader_type)
    GL.glShaderSource(shader, source)
    GL.glCompileShader(shader)
    if not GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
        raise RuntimeError(GL.glGetShaderInfoLog(shader).decode("utf-8"))
    return shader


def on_gl_realize(widget):
    """
    GTK GLArea realize callback.
    Initializes the OpenGL context, compiles and links vertex/fragment shaders,
    and sets up VAO/VBO for rendering a full-screen quad [-1, 1].
    """
    global gl_shader_program, gl_quad_vao, gl_quad_vbo
    widget.make_current()
    if widget.get_error():
        print("GLArea realize error:", widget.get_error())
        return
    vs = compile_gl_shader(vertex_shader_code, GL.GL_VERTEX_SHADER)
    fs = compile_gl_shader(fragment_shader_code, GL.GL_FRAGMENT_SHADER)
    gl_shader_program = GL.glCreateProgram()
    GL.glAttachShader(gl_shader_program, vs)
    GL.glAttachShader(gl_shader_program, fs)
    GL.glLinkProgram(gl_shader_program)
    if not GL.glGetProgramiv(gl_shader_program, GL.GL_LINK_STATUS):
        raise RuntimeError(GL.glGetProgramInfoLog(gl_shader_program).decode("utf-8"))

    gl_quad_vao = GL.glGenVertexArrays(1)
    GL.glBindVertexArray(gl_quad_vao)
    gl_quad_vbo = GL.glGenBuffers(1)
    GL.glBindBuffer(GL.GL_ARRAY_BUFFER, gl_quad_vbo)
    vertices = [-1.0, -1.0, 1.0, -1.0, -1.0, 1.0, -1.0, 1.0, 1.0, -1.0, 1.0, 1.0]
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


def on_gl_render(widget, context):
    """
    GTK GLArea render callback for hardware-accelerated GPU visualization.
    Clears the color buffer, applies temporal smoothing/idle wave calculations,
    binds uniforms (colors, dimensions, visualizer style parameters), and draws the quad.
    """
    global idle_time, has_rendered_idle_clear
    widget.make_current()
    GL.glClearColor(0.0, 0.0, 0.0, 0.0)
    GL.glClear(GL.GL_COLOR_BUFFER_BIT)

    if not config["enabled"] or not gl_shader_program:
        return True

    n = config["bars"]
    if n == 0:
        return True

    is_idle = True
    alpha = config["smoothing"]
    for i in range(n):
        target = cava_data[i] if i < len(cava_data) else 0.0
        if target > 0.01:
            is_idle = False
        smoothed_data[i] = smoothed_data[i] * alpha + target * (1.0 - alpha)

    if is_idle:
        idle_time += 1.0 / config["fps"]
    else:
        idle_time = 0

    render_data = smoothed_data[:]

    if is_idle and config["idle_wave"]:
        for i in range(n):
            wave = math.sin(idle_time * 2.0 + (i / n) * math.pi * 4.0) * 0.5 + 0.5
            render_data[i] = wave * 0.15
    elif is_idle and not config["idle_wave"]:
        if not any(v > 0.0001 for v in render_data):
            has_rendered_idle_clear = True
            return True

    if config["mirror"]:
        half = n // 2
        for i in range(half):
            render_data[n - 1 - i] = render_data[i]

    GL.glUseProgram(gl_shader_program)

    style_str = config.get("style", "bars")
    style_map = {
        "bars": 0,
        "segments": 1,
        "dots": 2,
        "wave": 3,
        "line": 4,
        "monitor": 4,
        "radial": 5,
        "circle": 6,
    }
    GL.glUniform1i(
        GL.glGetUniformLocation(gl_shader_program, "u_style"),
        style_map.get(style_str, 0),
    )
    GL.glUniform1i(GL.glGetUniformLocation(gl_shader_program, "u_bars"), min(64, n))
    GL.glUniform1i(
        GL.glGetUniformLocation(gl_shader_program, "u_segments_count"),
        config.get("segments_count", 16),
    )
    GL.glUniform1f(
        GL.glGetUniformLocation(gl_shader_program, "u_thickness"),
        config.get("thickness", 0.5),
    )
    GL.glUniform1i(
        GL.glGetUniformLocation(gl_shader_program, "u_shape_rounded"),
        1 if config.get("shape_rounded", True) else 0,
    )

    pos_str = config.get("position", "top")
    pos_map = {"top": 0, "center": 1, "bottom": 2}
    GL.glUniform1i(
        GL.glGetUniformLocation(gl_shader_program, "u_position"),
        pos_map.get(pos_str, 0),
    )

    fade_str = config.get("fade_direction", "fade_to_base")
    fade_map = {"fade_to_base": 0, "fade_to_tip": 1, "solid": 2}
    GL.glUniform1i(
        GL.glGetUniformLocation(gl_shader_program, "u_fade_direction"),
        fade_map.get(fade_str, 0),
    )
    GL.glUniform1f(
        GL.glGetUniformLocation(gl_shader_program, "u_fade_amount"),
        config.get("fade_amount", 1.0),
    )
    GL.glUniform1f(
        GL.glGetUniformLocation(gl_shader_program, "u_resolution_x"),
        float(widget.get_allocated_width()),
    )
    GL.glUniform1f(
        GL.glGetUniformLocation(gl_shader_program, "u_resolution_y"),
        float(widget.get_allocated_height()),
    )

    u_data_loc = GL.glGetUniformLocation(gl_shader_program, "u_data")
    padded_data = (render_data[:64] + [0.0] * 64)[:64]
    data_arr = (GL.GLfloat * 64)(*padded_data)
    GL.glUniform1fv(u_data_loc, 64, data_arr)

    ramp = get_color_ramp()
    u_ramp_len_loc = GL.glGetUniformLocation(gl_shader_program, "u_ramp_len")
    GL.glUniform1i(u_ramp_len_loc, len(ramp))
    u_ramp_loc = GL.glGetUniformLocation(gl_shader_program, "u_ramp")
    ramp_flat = []
    for c in ramp[:6]:
        ramp_flat.extend(c)
    while len(ramp_flat) < 24:
        ramp_flat.extend([1.0, 1.0, 1.0, 1.0])
    ramp_arr = (GL.GLfloat * 24)(*ramp_flat)
    GL.glUniform4fv(u_ramp_loc, 6, ramp_arr)

    GL.glBindVertexArray(gl_quad_vao)
    GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)
    return True


def apply_css():
    """
    Load and attach transparent CSS styling across all window and drawing/GL area widgets
    to ensure seamless integration with the desktop wallpaper/background.
    """
    provider = Gtk.CssProvider()
    provider.load_from_data(b"""
        window, drawingarea, glarea {
            background-color: transparent;
            background: transparent;
        }
    """)
    screen = Gdk.Screen.get_default()
    if screen:
        Gtk.StyleContext.add_provider_for_screen(
            screen,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


def setup_window():
    """
    Initialize and configure the top-level Wayland layer shell window (`GtkLayerShell`).
    Sets layer anchor positions, window dimensions, input pass-through regions, and selects
    between the hardware OpenGL (`Gtk.GLArea`) or Cairo (`Gtk.DrawingArea`) renderer.
    """
    global window, use_gl_renderer
    if window:
        window.destroy()

    if not config["enabled"]:
        return None

    apply_css()
    window = Gtk.Window()
    window.set_app_paintable(True)
    screen = window.get_screen()
    visual = screen.get_rgba_visual()
    if visual:
        window.set_visual(visual)

    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_namespace(window, "dusky-visualizer")

    if config.get("glass_blur", True):
        os.system(
            "hyprctl eval \"hl.layer_rule({ match = { namespace = 'dusky-visualizer' }, blur = true, ignore_alpha = 0.5 })\" >/dev/null 2>&1 &"
        )
    else:
        os.system(
            "hyprctl eval \"hl.layer_rule({ match = { namespace = 'dusky-visualizer' }, blur = false })\" >/dev/null 2>&1 &"
        )

    if is_overlay:
        GtkLayerShell.set_layer(window, GtkLayerShell.Layer.TOP)
    else:
        GtkLayerShell.set_layer(window, GtkLayerShell.Layer.BOTTOM)

    GtkLayerShell.set_exclusive_zone(window, -1)

    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.LEFT, True)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.RIGHT, True)

    if config["position"] == "top":
        GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.BOTTOM, False)
    elif config["position"] == "bottom":
        GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP, False)
        GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.BOTTOM, True)
    else:
        GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP, False)
        GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.BOTTOM, False)

    reg = cairo.Region()
    window.input_shape_combine_region(reg)

    display = Gdk.Display.get_default()
    monitor = display.get_primary_monitor()
    if monitor is None:
        monitor = display.get_monitor(0)

    geom = monitor.get_geometry()

    h = int(geom.height * config["height_pct"])
    if config.get("style", "bars") in ["radial", "circle"]:
        window.set_size_request(geom.width, geom.height)
    else:
        window.set_size_request(geom.width, h)

    if HAS_OPENGL and config.get("gpu_acceleration", True):
        try:
            area = Gtk.GLArea()
            area.set_has_alpha(True)
            area.connect("realize", on_gl_realize)
            area.connect("render", on_gl_render)
            window.add(area)
            window.show_all()
            use_gl_renderer = True
            print("Using GPU OpenGL Renderer (Gtk.GLArea)")
            return area
        except Exception as e:
            print("GLArea initialization failed, falling back to Cairo:", e)
            use_gl_renderer = False

    da = Gtk.DrawingArea()
    da.connect("draw", on_draw)
    window.add(da)
    window.show_all()
    use_gl_renderer = False
    print("Using Software Cairo Renderer (Gtk.DrawingArea)")
    return da


def read_ctl_file():
    """
    Check for commands written to `visualizer.ctl` (`toggle` or `overlay`).
    Allows instant control via external shell scripts (`visualizer_toggle.sh`).
    """
    global is_overlay, has_rendered_idle_clear
    try:
        if os.path.exists(CTL_FILE):
            with open(CTL_FILE, "r") as f:
                cmd = f.read().strip()
            os.remove(CTL_FILE)
            if cmd == "toggle":
                config["enabled"] = not config["enabled"]
                save_json(CONFIG_FILE, config)
                has_rendered_idle_clear = False
                apply_config()
            elif cmd == "overlay":
                is_overlay = not is_overlay
                has_rendered_idle_clear = False
                apply_config()
    except Exception as e:
        print(f"Ctl read error: {e}")
    return True


last_config_mtime = 0
last_colors_mtime = 0
da_widget = None


def check_files():
    """
    Periodic timer check (`GLib.timeout_add`) to detect changes in configuration or color JSON files.
    Automatically reloads configuration, restarts CAVA if bar counts/fps changed, or rebuilds UI.
    """
    global last_config_mtime, last_colors_mtime, da_widget, has_rendered_idle_clear
    try:
        mt1 = os.path.getmtime(CONFIG_FILE) if os.path.exists(CONFIG_FILE) else 0
        mt2 = os.path.getmtime(COLORS_FILE) if os.path.exists(COLORS_FILE) else 0

        changed = False
        if mt1 > last_config_mtime:
            last_config_mtime = mt1
            changed = True
        if mt2 > last_colors_mtime:
            last_colors_mtime = mt2
            changed = True

        if changed:
            old_bars = config.get("bars", 64)
            old_fps = config.get("fps", 60)
            old_style = config.get("style", "bars")
            load_config()
            has_rendered_idle_clear = False
            if old_bars != config["bars"] or old_fps != config["fps"]:
                start_cava()
            if (old_style in ["radial", "circle"]) != (
                config.get("style", "bars") in ["radial", "circle"]
            ):
                da_widget = setup_window()
            else:
                da_widget = setup_window()
    except Exception:
        pass
    return True


def apply_config():
    """
    Rebuild and show the visualizer window (`setup_window`) after configuration changes.
    """
    global da_widget
    da_widget = setup_window()


def tick():
    """
    High-frequency animation tick driven by `GLib.timeout_add` at `config["fps"]`.
    Queues redraw/render passes whenever audio is active or idle wave animations are enabled.
    """
    if not da_widget or not config["enabled"]:
        return True

    is_active = any(v > 0.001 for v in cava_data) or any(
        v > 0.0001 for v in smoothed_data
    )
    if is_active or config.get("idle_wave", True) or not has_rendered_idle_clear:
        if use_gl_renderer:
            da_widget.queue_render()
        else:
            da_widget.queue_draw()

    return True


def on_sigusr1(sig, frame):
    """
    Signal handler for SIGUSR1 to force immediate file check/reload.
    """
    check_files()


def main():
    """
    Application entrypoint.
    Initializes configuration files, starts the CAVA background audio reader, sets up the
    Wayland layer window, registers signal/timer loops, and enters the GTK main loop.
    """
    global da_widget
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    if not os.path.exists(CONFIG_FILE):
        save_json(CONFIG_FILE, config)

    load_config()
    start_cava()
    da_widget = setup_window()

    signal.signal(signal.SIGUSR1, on_sigusr1)

    GLib.timeout_add(1000 // config["fps"], tick)
    GLib.timeout_add(1000, check_files)
    GLib.timeout_add(100, read_ctl_file)

    Gtk.main()


if __name__ == "__main__":
    main()
