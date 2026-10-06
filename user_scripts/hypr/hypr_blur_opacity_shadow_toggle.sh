#!/usr/bin/env bash
# Master visual-effects switch with selective controls. Animation control stays
# in Rofi. Restore snapshots contain only affected values, never whole configs.
set -euo pipefail

usage() {
    cat <<'HELP'
Usage: hypr_blur_opacity_shadow_toggle.sh [on|off|toggle] [--only EFFECTS]

Without --only: OFF saves your choices and disables all effects; ON restores
those choices. TOGGLE switches between these states. Repeated OFF preserves
saved choices. With no saved choices, ON enables blur/shadow/transparency and
keeps glow/wobble/motion blur as configured.

--only accepts a comma-separated list:
  blur,shadow,opacity,glow,wobble,motion-blur
Selected boolean effects toggle independently. Opacity includes window and
Mako/Rofi/Waybar transparency; its previous values are restored on ON.
Examples:
  hypr_blur_opacity_shadow_toggle.sh off
  hypr_blur_opacity_shadow_toggle.sh toggle --only wobble
  hypr_blur_opacity_shadow_toggle.sh off --only wobble,motion-blur,glow

Snapshots: ~/dusky/settings/blur_opacity_toggle/restore.json
Animations, blur variants and effect tuning are not changed.
HELP
}

action="toggle"
only=""
action_given=false
only_given=false
while (( $# )); do
    case "$1" in
        -h|--help|help) usage; exit 0 ;;
        --only)
            [[ $# -ge 2 && -n "$2" && "$only_given" == false ]] || { usage >&2; exit 1; }
            only="$2"; only_given=true; shift ;;
        --only=*)
            [[ -n "${1#*=}" && "$only_given" == false ]] || { usage >&2; exit 1; }
            only="${1#*=}"; only_given=true ;;
        on|ON|enable|1|true|yes|off|OFF|disable|0|false|no|toggle|"")
            [[ "$action_given" == false ]] || { usage >&2; exit 1; }
            case "$1" in
                on|ON|enable|1|true|yes) action="on" ;;
                off|OFF|disable|0|false|no) action="off" ;;
                *) action="toggle" ;;
            esac
            action_given=true ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; exit 1 ;;
    esac
    shift
done

config_file="$HOME/.config/hypr/edit_here/source/appearance.lua"
[[ -f "$config_file" && -r "$config_file" && -w "$config_file" ]] || {
    printf 'Appearance config is missing or not writable: %s\n' "$config_file" >&2
    exit 1
}
command -v hyprctl >/dev/null || { printf 'hyprctl is required.\n' >&2; exit 1; }
# Hold one lock through config edits, restore metadata and daemon reloads.
exec {toggle_lock_fd}>"${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}/dusky-blur-opacity-${UID}.lock"
flock "$toggle_lock_fd"
exec python - "$config_file" "$action" "$only" <<'PYTHON'
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.home() / "user_scripts" / "dusky_tui"))
from python.engines.lua import HyprlandLuaEngine

home = Path.home()
cache_path = home / "dusky/settings/blur_opacity_toggle/restore.json"
indicator_path = home / ".config/dusky/settings/opacity_blur"
groups = {
    "blur": ["decoration/blur/enabled"],
    "shadow": ["decoration/shadow/enabled"],
    "glow": ["decoration/glow/enabled"],
    "wobble": ["decoration/wobble/enabled"],
    "motion-blur": ["decoration/motion_blur/enabled"],
    "opacity": ["decoration/active_opacity", "decoration/inactive_opacity", "decoration/fullscreen_opacity",
                "window_rule/single_window_style/opacity", "window_rule/maximized_window_style/opacity",
                "window_rule/special_magic_style/opacity"],
}


def stop(signum, _frame):
    raise SystemExit(128 + signum)


for termination_signal in (signal.SIGHUP, signal.SIGTERM):
    signal.signal(termination_signal, stop)


def atomic_write(path, data):
    path = path.resolve()
    if path.is_file() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".visual_toggle.", delete=False) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644)
            output.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def alpha_pattern(kind, role):
    template = kind.endswith("template")
    color = r"\{\{[^}]*\}\}" if template else r"#[0-9a-fA-F]{6}"
    if kind.startswith("rofi"):
        return re.compile(r"(^[ \t]*surface[ \t]*:[ \t]*" + color + r")([0-9a-fA-F]{2})(;)", re.MULTILINE)
    key = role.removeprefix("base:").removeprefix("osd:")
    return re.compile(r"(^[ \t]*" + key + "=" + color + r")([0-9a-fA-F]*)([ \t]*\r?)$", re.MULTILINE)


def mako_sections(text):
    base = re.search(r"GLOBAL MATUGEN COLOR INJECTION[\s\S]*?STATE MODES", text)
    osd = re.search(r"^\[app-name=OSD\][\s\S]*?(?=^\[app-name=|\Z)", text, re.MULTILINE)
    return [(match, roles) for match, roles in [(base, ["base:background-color", "base:border-color", "base:progress-color"]),
                                              (osd, ["osd:background-color"])] if match]


def switch_markers(text):
    count = 0
    for index, line in enumerate(text.splitlines(keepends=True)):
        if "Remove this line to flip the master switch to OPAQUE" in line:
            count += 1
            yield index, line, "start" if count % 2 else "end"
        elif "WAYBAR_OPAQUE_SWITCH_START" in line:
            yield index, line, "start"
        elif "WAYBAR_OPAQUE_SWITCH_END" in line:
            yield index, line, "end"


def ui_values(text, kind):
    if kind == "waybar":
        return {"markers": [line for _, line, _ in switch_markers(text)]}
    if kind.startswith("rofi"):
        match = alpha_pattern(kind, "surface").search(text)
        return {"surface": match[2]} if match else {}
    values = {}
    for section, roles in mako_sections(text):
        for role in roles:
            match = alpha_pattern(kind, role).search(section[0])
            if match:
                values[role] = match[2]
    return values


def ui_transform(text, kind, mode, saved=None):
    if kind == "waybar":
        lines = text.splitlines(keepends=True)
        original = saved.get("markers", []) if saved is not None else []
        for ordinal, (index, line, marker) in enumerate(switch_markers(text)):
            if ordinal < len(original):
                lines[index] = original[ordinal]
            else:
                ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
                lines[index] = ("/* WAYBAR_OPAQUE_SWITCH_START" + (" */" if mode == "off" else "") if marker == "start"
                                else ("/* " if mode == "off" else "") + "WAYBAR_OPAQUE_SWITCH_END */") + ending
        return "".join(lines)
    defaults = {"surface": "66", "base:background-color": "1a", "base:border-color": "33",
                "base:progress-color": "59", "osd:background-color": "0d"}
    def change(section, role):
        alpha = (saved or {}).get(role, "ff" if mode == "off" else defaults[role])
        return alpha_pattern(kind, role).sub(lambda m: m[1] + alpha + m[3], section)
    if kind.startswith("rofi"):
        return change(text, "surface")
    for section, roles in reversed(mako_sections(text)):
        updated = section[0]
        for role in roles:
            updated = change(updated, role)
        text = text[:section.start()] + updated + text[section.end():]
    return text


def ui_files():
    seen = set()
    candidates = []
    for directory, template in [("templates", True), ("generated", False)]:
        parent = home / ".config/matugen" / directory
        suffix = "template" if template else "generated"
        candidates.extend([(parent / ("mako.ini" if template else "mako-colors.ini"), "mako-" + suffix),
                           (parent / "rofi-colors.rasi", "rofi-" + suffix)])
    candidates.extend((path, "waybar") for path in (home / ".config/waybar").rglob("style.css"))
    for path, kind in candidates:
        if not path.is_file():
            continue
        actual = path.resolve(strict=True)
        if actual in seen or not os.access(actual, os.W_OK) or not os.access(actual.parent, os.W_OK):
            continue
        seen.add(actual)
        yield path.relative_to(home).as_posix(), actual, kind, actual.read_bytes().decode("utf-8", "surrogateescape")


def opacity_active(values):
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value < 1:
                return True
        elif isinstance(value, str) and any(float(n) < 1 for n in re.findall(r"\d+(?:\.\d+)?", value)):
            return True
    return False


def ui_active(ui):
    for values in ui.values():
        for role, value in values.items():
            if role == "markers":
                if any(("WAYBAR_OPAQUE_SWITCH_START" in line or
                        ("Remove this line to flip the master switch to OPAQUE" in line and index % 2 == 0))
                       and not line.rstrip().endswith("*/") for index, line in enumerate(value)):
                    return True
            elif value and int(value, 16) < 255:
                return True
    return False


def validate_snapshot(snapshot, keys):
    if (not isinstance(snapshot, dict) or not isinstance(snapshot.get("config"), dict)
            or set(snapshot["config"]) != set(keys)):
        raise ValueError("Incomplete visual restore snapshot; keep a backup and remove restore.json to reset it.")
    for key, value in snapshot["config"].items():
        if key.endswith("/enabled"):
            valid = isinstance(value, bool)
        elif key.startswith("window_rule/") and isinstance(value, str):
            valid = bool(value)
        else:
            valid = type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
        if not valid:
            raise ValueError("Invalid saved visual value: " + key)
    if not isinstance(snapshot.get("ui"), dict):
        raise ValueError("Invalid saved UI transparency.")
    for values in snapshot["ui"].values():
        if not isinstance(values, dict):
            raise ValueError("Invalid saved UI transparency.")
        for role, value in values.items():
            if role == "markers":
                if not isinstance(value, list) or not all(isinstance(line, str) for line in value):
                    raise ValueError("Invalid saved Waybar markers.")
            elif not isinstance(value, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{2})?", value):
                raise ValueError("Invalid saved UI alpha.")


def save_cache(cache):
    if cache["master"] is None and cache["opacity"] is None:
        cache_path.unlink(missing_ok=True)
    else:
        atomic_write(cache_path, (json.dumps(cache, indent=2) + "\n").encode())


def run_optional(command):
    try:
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except FileNotFoundError:
        pass


def main():
    action, only = sys.argv[2:4]
    master = not only
    selected = list(groups) if master else list(dict.fromkeys(only.split(",")))
    if any(name not in groups for name in selected):
        raise ValueError("--only accepts: " + ",".join(groups))
    engine = HyprlandLuaEngine(sys.argv[1])
    state = engine.load_state()
    keys = [key for name in selected for key in groups[name]]
    missing = [key for key in keys if key not in state]
    if missing:
        raise ValueError("Missing appearance settings: " + ", ".join(missing))
    validate_snapshot({"config": {key: state[key] for key in keys}, "ui": {}}, keys)
    cache = {"version": 1, "master": None, "opacity": None}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())
        if not isinstance(cache, dict) or cache.get("version") != 1 or set(cache) != {"version", "master", "opacity"}:
            raise ValueError("Unsupported visual restore snapshot.")
        if cache["master"] is not None:
            validate_snapshot(cache["master"], [key for items in groups.values() for key in items])
        if cache["opacity"] is not None:
            validate_snapshot(cache["opacity"], groups["opacity"])
    files = list(ui_files()) if "opacity" in selected else []
    current_ui = {key: ui_values(text, kind) for key, _, kind, text in files}
    def capture(names):
        return {"config": {key: state[key] for name in names for key in groups[name]},
                "ui": current_ui.copy() if "opacity" in names else {}}
    active = {name: (opacity_active(state.get(key, 1) for key in groups[name]) or ui_active(current_ui)
                     if name == "opacity" else bool(state.get(groups[name][0], False))) for name in groups}
    if master and action == "toggle":
        action = "on" if cache["master"] is not None or not any(active.values()) else "off"
    desired = {}
    ui_mode, ui_saved = None, None
    if master and action == "off":
        if cache["master"] is None:
            cache["master"] = capture(list(groups))
        desired = {key: (False if key.endswith("/enabled") else 1.0) for key in keys}
        ui_mode = "off"
    elif master and cache["master"] is not None:
        desired = cache["master"]["config"].copy()
        ui_mode, ui_saved = "on", cache["master"]["ui"]
    else:
        for name in selected:
            target = ("off" if active[name] else "on") if action == "toggle" else action
            if name != "opacity":
                # Without a master snapshot, keep optional effects as configured.
                if master and name in {"glow", "wobble", "motion-blur"}:
                    desired[groups[name][0]] = state[groups[name][0]]
                else:
                    desired[groups[name][0]] = target == "on"
                continue
            ui_mode = target
            if target == "off":
                if cache["opacity"] is None or active["opacity"]:
                    cache["opacity"] = (
                        {"config": {key: cache["master"]["config"][key] for key in groups["opacity"]},
                         "ui": cache["master"]["ui"].copy()}
                        if cache["master"] is not None and not active["opacity"] else capture(["opacity"]))
                desired.update(dict.fromkeys(groups["opacity"], 1.0))
            else:
                snapshot = cache["opacity"] or (cache["master"] if not master else None)
                if snapshot is not None:
                    desired.update({key: snapshot["config"][key] for key in groups["opacity"]})
                    ui_saved = snapshot["ui"]
                else:
                    desired.update({key: state[key] if opacity_active([state[key]]) else
                                    0.85 if key in groups["opacity"][:2] else 1.0 for key in groups["opacity"]})
    edits = []
    desired_ui = {}
    for key, path, kind, text in files:
        updated = ui_transform(text, kind, ui_mode, ui_saved.get(key) if ui_saved is not None else None)
        desired_ui[key] = ui_values(updated, kind)
        if updated != text:
            edits.append((path, updated.encode("utf-8", "surrogateescape")))
    # Targeted choices made while the master is suspended become restore choices.
    if not master and cache["master"] is not None:
        cache["master"]["config"].update(desired)
        if "opacity" in selected:
            cache["master"]["ui"].update(desired_ui)
    # Save original choices before mutation; retain them if any later step fails.
    save_cache(cache)
    changes = []
    for uid, value in desired.items():
        if state[uid] != value:
            scope, key = uid.rsplit("/", 1)
            kind = "bool" if isinstance(value, bool) else "string" if isinstance(value, str) else "float"
            raw = ("true" if value else "false") if kind == "bool" else str(value)
            changes.append((key, scope, raw, kind))
    if changes:
        ok, message, _ = engine.write_batch(changes)
        if not ok or "Partial" in message:
            raise RuntimeError(message)
    for path, data in edits:
        atomic_write(path, data)
    subprocess.run(["hyprctl", "reload", "config-only"], check=True, stdout=subprocess.DEVNULL)
    errors = subprocess.check_output(["hyprctl", "configerrors"], text=True).strip()
    if errors:
        raise RuntimeError("Hyprland configuration errors: " + errors)
    state.update(desired)
    restoring_master = master and cache["master"] is not None
    if master and action == "on":
        cache["master"] = None
    if ui_mode == "on" and not restoring_master:
        cache["opacity"] = None
    save_cache(cache)
    enabled = any(bool(state.get(groups[name][0], False)) for name in groups if name != "opacity")
    enabled |= opacity_active(state.get(key, 1) for key in groups["opacity"])
    # UI alpha belongs to opacity; preserve its contribution on targeted calls.
    if "opacity" not in selected:
        desired_ui = {key: ui_values(text, kind) for key, _, kind, text in ui_files()}
    enabled |= ui_active(desired_ui)
    atomic_write(indicator_path, b"True" if enabled else b"False")
    if "opacity" in selected:
        run_optional(["makoctl", "reload"])
        run_optional(["pkill", "-SIGUSR2", "waybar"])
    label = "Visual effects " + action.upper() if master else ("Toggled " if action == "toggle" else action.capitalize() + ": ") + ", ".join(selected)
    run_optional(["notify-send", "--app-name=hypr-visuals", "--icon=display-symbolic",
                  "-h", "string:x-canonical-private-synchronous:hypr-visuals", "-t", "1500", label])


try:
    main()
except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
    print("Error: " + str(error), file=sys.stderr)
    run_optional(["notify-send", "-u", "critical", "Hyprland visual effects error", str(error)])
    sys.exit(1)
PYTHON
