#!/usr/bin/env python3
import os
import sys
import json
import time
import math
import subprocess
import threading
import signal
import gi
import fcntl

gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GLib, GtkLayerShell, Pango
import cairo

CONFIG_FILE = os.path.expanduser("~/.config/dusky/visualizer.json")
COLORS_FILE = os.path.expanduser("~/.config/matugen/generated/dusky_visualizer_colors.json")
CTL_FILE = os.path.expanduser("~/.cache/dusky/visualizer.ctl")

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
    "cava_upper_freq": 10000
}

colors = {
    "c1": "#ffb4ac",
    "c2": "#f5b9a1",
    "c3": "#fbb983",
    "c4": "#93000e",
    "c5": "#663c2a",
    "c6": "#693c10",
    "accent": "#ffb4ac"
}

cava_proc = None
cava_data = []
smoothed_data = []
is_overlay = False
window = None

def load_json(path, default_dict):
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
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data_dict, f, indent=4)
    except:
        pass

def load_config():
    global config, colors
    load_json(CONFIG_FILE, config)
    load_json(COLORS_FILE, colors)
    if "bars" not in config: config["bars"] = 64
    global cava_data, smoothed_data
    if len(cava_data) != config["bars"]:
        cava_data = [0.0] * config["bars"]
        smoothed_data = [0.0] * config["bars"]

def generate_cava_config():
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
    global cava_proc
    if cava_proc:
        try:
            cava_proc.terminate()
            cava_proc.wait(timeout=1)
        except:
            try: cava_proc.kill()
            except: pass
    
    conf_path = generate_cava_config()
    cava_proc = subprocess.Popen(["cava", "-p", conf_path], stdout=subprocess.PIPE, text=True, bufsize=1)
    
    def cava_reader():
        while cava_proc and cava_proc.poll() is None:
            line = cava_proc.stdout.readline()
            if not line: break
            parts = line.strip().split(";")[:-1]
            if len(parts) == config["bars"]:
                new_data = []
                for p in parts:
                    try:
                        val = int(p) / 1000.0
                        new_data.append(min(1.0, val * config["gain"]))
                    except:
                        new_data.append(0.0)
                GLib.idle_add(update_cava_data, new_data)
                
    threading.Thread(target=cava_reader, daemon=True).start()

def update_cava_data(new_data):
    global cava_data
    cava_data = new_data
    return False

def hex_to_rgba(hex_str, alpha=1.0):
    hex_str = hex_str.lstrip('#')
    if len(hex_str) == 6:
        r, g, b = tuple(int(hex_str[i:i+2], 16)/255.0 for i in (0, 2, 4))
        return (r, g, b, alpha)
    elif len(hex_str) == 8:
        r, g, b, a = tuple(int(hex_str[i:i+2], 16)/255.0 for i in (0, 2, 4, 6))
        return (r, g, b, a * alpha)
    return (1, 1, 1, alpha)

def get_color_ramp():
    return [
        hex_to_rgba(colors.get("c1", "#ffb4ac")),
        hex_to_rgba(colors.get("c3", "#fbb983")),
        hex_to_rgba(colors.get("c2", "#f5b9a1")),
        hex_to_rgba(colors.get("c6", "#693c10")),
        hex_to_rgba(colors.get("c4", "#93000e")),
        hex_to_rgba(colors.get("c5", "#663c2a"))
    ]

def interpolate_color(ramp, t):
    if len(ramp) == 0: return (1, 1, 1, 1)
    if len(ramp) == 1: return ramp[0]
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
        a[3] + (b[3] - a[3]) * f
    )

def apply_gradient(cr, color, x_base, y_base, x_tip, y_tip):
    fade = config.get("fade_direction", "fade_to_base")
    amt = config.get("fade_amount", 1.0)
    
    r, g, b, a = color
    # Lighter color for the glowing tip
    lr, lg, lb = min(1.0, r * 1.3), min(1.0, g * 1.3), min(1.0, b * 1.3)
    alpha_faded = a * (1.0 - amt)
    
    pat = cairo.LinearGradient(x_base, y_base, x_tip, y_tip)
    
    if fade == "solid" or amt == 0.0:
        pat.add_color_stop_rgba(0, r, g, b, a)
        pat.add_color_stop_rgba(1, lr, lg, lb, a) # Tip is still slightly lit
    else:
        if fade == "fade_to_base":
            # 0 is base (transparent), 1 is tip (bright)
            pat.add_color_stop_rgba(0.0, r, g, b, alpha_faded)
            pat.add_color_stop_rgba(0.45, r, g, b, a * 0.8)
            pat.add_color_stop_rgba(1.0, lr, lg, lb, a)
        elif fade == "fade_to_tip":
            # 0 is base (bright), 1 is tip (transparent)
            pat.add_color_stop_rgba(0.0, lr, lg, lb, a)
            pat.add_color_stop_rgba(0.55, r, g, b, a * 0.8)
            pat.add_color_stop_rgba(1.0, r, g, b, alpha_faded)
            
    cr.set_source(pat)

idle_time = 0

def on_draw(widget, cr):
    global idle_time
    w = widget.get_allocated_width()
    h = widget.get_allocated_height()
    
    cr.set_operator(cairo.OPERATOR_CLEAR)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)
    
    if not config["enabled"]:
        return False
        
    ramp = get_color_ramp()
    n = config["bars"]
    if n == 0: return False
    
    is_idle = True
    alpha = config["smoothing"]
    for i in range(n):
        target = cava_data[i]
        if target > 0.01: is_idle = False
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
            x = i * (bar_w + gap) + gap/2
            
            if pos == "top": y_base, y_tip = 0, val
            elif pos == "center": y_base, y_tip = h/2, h/2 - val/2
            else: y_base, y_tip = h, h - val

            color = interpolate_color(ramp, i / (n - 1))
            
            cr.new_sub_path()
            if config["shape_rounded"]:
                radius = bar_w / 2
                if val < radius * 2: val = radius * 2
                if pos == "center":
                    cr.arc(x + radius, y_base + val/2 - radius, radius, 0, math.pi)
                    cr.arc(x + radius, y_tip + radius, radius, math.pi, math.pi * 2)
                else:
                    cr.arc(x + radius, y_base + (radius if pos=="top" else -radius), radius, math.pi, math.pi * 2)
                    cr.arc(x + radius, y_tip + (radius if pos=="bottom" else -radius), radius, 0, math.pi)
            else:
                y_rect = y_base if pos == "top" else y_tip
                rect_h = val if pos != "center" else val/2
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
            x = i * (bar_w + gap) + gap/2
            color = interpolate_color(ramp, i / (n - 1))
            
            for cell in range(lit):
                cell_h = max(2, pitch - seg_gap)
                
                if pos == "top": y = cell * pitch + seg_gap / 2
                elif pos == "center": y = h/2 - lit * pitch / 2 + cell * pitch + seg_gap / 2
                else: y = h - (cell + 1) * pitch + seg_gap / 2
                
                cr.set_source_rgba(color[0], color[1], color[2], color[3] * (0.4 + 0.6 * (cell/max(1, lit-1))))
                if config["shape_rounded"]:
                    radius = min(cell_h, bar_w) * 0.35
                    cr.arc(x + radius, y + radius, radius, math.pi, math.pi*1.5)
                    cr.arc(x + bar_w - radius, y + radius, radius, math.pi*1.5, math.pi*2)
                    cr.arc(x + bar_w - radius, y + cell_h - radius, radius, 0, math.pi*0.5)
                    cr.arc(x + radius, y + cell_h - radius, radius, math.pi*0.5, math.pi)
                    cr.fill()
                else:
                    cr.rectangle(x, y, bar_w, cell_h)
                    cr.fill()

    elif style == "wave":
        points = []
        for i in range(n):
            val = data[i] * h
            x = i * (w / n) + (w / n)/2
            y_tip = val if pos == "top" else (h/2 - val/2 if pos == "center" else h - val)
            points.append((x, y_tip))
            
        if len(points) >= 2:
            base_y = 0 if pos == "top" else (h/2 if pos == "center" else h)
            
            cr.move_to(0, base_y)
            cr.line_to(0, points[0][1])
            cr.line_to(points[0][0], points[0][1])
            
            for i in range(len(points) - 1):
                x0, y0 = points[i]
                x1, y1 = points[i+1]
                cx = (x0 + x1) / 2
                cr.curve_to(cx, y0, cx, y1, x1, y1)
                
            cr.line_to(w, points[-1][1])
            cr.line_to(w, base_y)
            cr.close_path()

            pat = cairo.LinearGradient(w/2, base_y, w/2, h/2 if pos == "center" else (h if pos == "top" else 0))
            amt = config.get("fade_amount", 1.0)
            for i, c in enumerate(ramp):
                pat.add_color_stop_rgba(i / (len(ramp)-1), c[0], c[1], c[2], c[3] * (1.0 - amt * 0.8))
            cr.set_source(pat)
            cr.fill()

    elif style == "line" or style == "monitor":
        cr.set_line_width(config["thickness"] * 5 + 1)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        points = []
        for i in range(n):
            val = data[i] * h
            x = i * (w / n) + (w / n)/2
            y = val if pos == "top" else (h/2 - val/2 if pos == "center" else h - val)
            points.append((x, y))
            
        if len(points) >= 2:
            cr.move_to(points[0][0], points[0][1])
            for i in range(len(points) - 1):
                x0, y0 = points[i]
                x1, y1 = points[i+1]
                cx = (x0 + x1) / 2
                cr.curve_to(cx, y0, cx, y1, x1, y1)
                
            pat = cairo.LinearGradient(0, 0, w, 0)
            for i, c in enumerate(ramp):
                pat.add_color_stop_rgba(i / (len(ramp)-1), *c)
            cr.set_source(pat)
            cr.stroke()

    elif style == "dots":
        for i in range(n):
            val = data[i] * h
            x = i * (bar_w + gap) + gap/2 + bar_w/2
            y = val if pos == "top" else (h/2 - val/2 + (val/2 if val>0 else 0) if pos == "center" else h - val)
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
            arc_w = max(2, (2 * math.pi * ring_r / max(1, n)) * config["thickness"])
            for i in range(n):
                val = max(2, max_len * data[i])
                ang = (i / n) * math.pi * 2
                
                cr.save()
                cr.translate(cx, cy)
                cr.rotate(ang)
                
                color = interpolate_color(ramp, i / (n - 1))
                
                if config["shape_rounded"]:
                    radius = arc_w / 2
                    cr.arc(-arc_w/2 + radius, ring_r + radius, radius, math.pi, math.pi*1.5)
                    cr.arc(arc_w/2 - radius, ring_r + radius, radius, math.pi*1.5, math.pi*2)
                    cr.arc(arc_w/2 - radius, ring_r + val - radius, radius, 0, math.pi*0.5)
                    cr.arc(-arc_w/2 + radius, ring_r + val - radius, radius, math.pi*0.5, math.pi)
                else:
                    cr.rectangle(-arc_w/2, ring_r, arc_w, val)
                
                apply_gradient(cr, color, 0, ring_r, 0, ring_r + val)
                cr.fill()
                cr.restore()
                
            # Draw center pulsing ring
            cr.set_line_width(2)
            cr.set_source_rgba(ramp[0][0], ramp[0][1], ramp[0][2], 0.4)
            avg_pulse = sum(data) / n if n > 0 else 0
            cr.arc(cx, cy, ring_r * (1 + 0.12 * avg_pulse), 0, math.pi * 2)
            cr.stroke()

        elif style == "circle":
            px, py = [], []
            for i in range(n):
                ang = (i / n) * math.pi * 2 - math.pi / 2
                r = ring_r + max_len * data[i]
                px.append(cx + math.cos(ang) * r)
                py.append(cy + math.sin(ang) * r)
                
            if len(px) >= 2:
                cr.move_to((px[-1] + px[0])/2, (py[-1] + py[0])/2)
                for k in range(n):
                    nx = (k + 1) % n
                    ctrl_x, ctrl_y = px[k], py[k]
                    end_x, end_y = (px[k] + px[nx])/2, (py[k] + py[nx])/2
                    # Quadratic bezier via cubic approximation
                    cr.curve_to(
                        ctrl_x, ctrl_y,
                        ctrl_x, ctrl_y,
                        end_x, end_y
                    )
                cr.close_path()
                
                cr.set_line_width(3)
                pat = cairo.LinearGradient(cx - ring_r - max_len, 0, cx + ring_r + max_len, 0)
                amt = config.get("fade_amount", 1.0)
                for i, c in enumerate(ramp):
                    pat.add_color_stop_rgba(i / (len(ramp)-1), c[0], c[1], c[2], c[3] * (1.0 - amt * 0.6))
                cr.set_source(pat)
                cr.fill_preserve()
                
                cr.set_source_rgba(ramp[-1][0], ramp[-1][1], ramp[-1][2], 0.8)
                cr.stroke()

def setup_window():
    global window
    if window:
        window.destroy()
        
    if not config["enabled"]:
        return None
        
    window = Gtk.Window()
    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_namespace(window, "dusky-visualizer")
    
    # Trigger Hyprland blur dynamically
    if config.get("glass_blur", True):
        os.system("hyprctl keyword layerrule 'blur, dusky-visualizer' >/dev/null 2>&1 &")
        os.system("hyprctl keyword layerrule 'ignorealpha 0.01, dusky-visualizer' >/dev/null 2>&1 &")
    else:
        os.system("hyprctl keyword layerrule 'unset, dusky-visualizer' >/dev/null 2>&1 &")
    
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
    
    screen = window.get_screen()
    visual = screen.get_rgba_visual()
    if visual and screen.is_composited():
        window.set_visual(visual)
    
    display = Gdk.Display.get_default()
    monitor = display.get_primary_monitor()
    if monitor is None:
        monitor = display.get_monitor(0)
    
    geom = monitor.get_geometry()
    
    h = int(geom.height * config["height_pct"])
    if config.get("style", "bars") in ["radial", "circle"]:
        # Polar styles need a square area based on height
        window.set_size_request(geom.width, geom.height)
    else:
        window.set_size_request(geom.width, h)
    
    da = Gtk.DrawingArea()
    da.connect("draw", on_draw)
    window.add(da)
    window.show_all()
    return da

def read_ctl_file():
    global is_overlay
    try:
        if os.path.exists(CTL_FILE):
            with open(CTL_FILE, "r") as f:
                cmd = f.read().strip()
            os.remove(CTL_FILE)
            if cmd == "toggle":
                config["enabled"] = not config["enabled"]
                save_json(CONFIG_FILE, config)
                apply_config()
            elif cmd == "overlay":
                is_overlay = not is_overlay
                apply_config()
    except Exception as e:
        print(f"Ctl read error: {e}")
    return True

last_config_mtime = 0
last_colors_mtime = 0
da_widget = None

def check_files():
    global last_config_mtime, last_colors_mtime, da_widget
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
            if old_bars != config["bars"] or old_fps != config["fps"]:
                start_cava()
            # Recreate window if style changed to/from polar styles (requires full screen bounds)
            if (old_style in ["radial", "circle"]) != (config.get("style", "bars") in ["radial", "circle"]):
                da_widget = setup_window()
            else:
                # Normal hot reload
                da_widget = setup_window()
    except Exception:
        pass
    return True

def apply_config():
    global da_widget
    da_widget = setup_window()

def tick():
    if da_widget and config["enabled"]:
        da_widget.queue_draw()
    return True

def on_sigusr1(sig, frame):
    check_files()

def main():
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
