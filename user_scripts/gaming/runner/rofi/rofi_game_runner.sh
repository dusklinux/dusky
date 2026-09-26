#!/usr/bin/env bash
# ==============================================================================
# ARCH LINUX :: UWSM :: MATUGEN & ROFI GAME RUNNER
# ==============================================================================
# Description: Rofi frontend for Master Game Runner Engine
#              - Fuzzy, icon-aware game picker with mount awareness
#              - Launch / Mount / Unmount via custom keybinds
#              - Locking, state memory, and strict error handling
#              - Supports both dmenu and rofi custom modi (-modi games:...)
#
# Engine:      ~/user_scripts/gaming/runner/master_runner.py
# Profiles:    ~/user_scripts/gaming/runner/profiles/*.toml
#
# Usage:
#   ./rofi_game_runner.sh                 # dmenu (installed only)
#   ./rofi_game_runner.sh --all           # show all profiles
#   ./rofi_game_runner.sh --help          # help
#
# Modi:
#   rofi -show games -modi "games:$HOME/user_scripts/gaming/runner/rofi/rofi_game_runner.sh --rofi-mode" \
#        -kb-custom-1 "Alt+m" -kb-custom-2 "Alt+u"
#
# Hyprland (edit_here/source/keybinds.lua):
#   hl.bind("SUPER + G",
#       hl.dsp.exec_cmd("pkill rofi; $HOME/user_scripts/gaming/runner/rofi/rofi_game_runner.sh"),
#       { description = "Game Launcher" })
#   hl.bind("SUPER + SHIFT + G",
#       hl.dsp.exec_cmd("pkill rofi; $HOME/user_scripts/gaming/runner/rofi/rofi_game_runner.sh --all"),
#       { description = "Game Launcher (all)" })
# ==============================================================================

set -Eeuo pipefail
shopt -s inherit_errexit
shopt -s nullglob

SCRIPT_DIR="$(dirname -- "$(realpath -- "${BASH_SOURCE[0]}")")"
readonly SCRIPT_DIR
readonly DEFAULT_MASTER="$SCRIPT_DIR/../master_runner.py"

# ------------------------------------------------------------------------------
# Rofi custom modi provider (must run BEFORE locking)
# Protocol: ROFI_RETV == 0 → initial list, 1 → selection (ROFI_INFO=pid),
#           10/11 → Alt+m/u if caller passes -kb-custom-*
# This is invoked as: rofi -show games -modi "games:rofi_game_runner.sh --rofi-mode"
# ------------------------------------------------------------------------------
if [[ "${1:-}" == "--rofi-mode" ]]; then
    _MASTER_MODI="${MASTER_RUNNER:-$DEFAULT_MASTER}"
    if [[ ! -f "$_MASTER_MODI" ]]; then
        echo -en "\0message\x1f<span color='#cc6666'>master_runner not found: $_MASTER_MODI</span>\n"
        exit 0
    fi
    _python_modi() {
        python3 - "$_MASTER_MODI" <<'PY' 2>/dev/null || true
import sys, importlib.util
from pathlib import Path
master_path = Path(sys.argv[1]).expanduser()
spec = importlib.util.spec_from_file_location("master_runner", str(master_path))
try:
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
except SystemExit:
    sys.exit(1)
except Exception as e:
    print(f"import failed: {e}", file=sys.stderr)
    sys.exit(1)
mgr = mod.ProfileManager()
profs = mod.catalogue(mgr, show_all=False)
if not profs:
    profs = mod.catalogue(mgr, show_all=True)
for p in profs:
    try:
        paths = mod.resolve_paths(p)
        st = mod.MountEngine.status(paths)
        mstate = str(st.state)
    except Exception:
        mstate = "unknown"
    icon = p.icon or "applications-games"
    def esc(s):
        return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    name = esc(p.name)
    detail = esc(f"{p.runtime} • {p.get('graphics.gpu','auto')} • {mstate}")
    if p.get("meta.genre"):
        detail = esc(f"{p.runtime} • {p.get('meta.genre','')}")
    display = f"{name} <span size='small' alpha='55%'> {detail}</span>"
    sys.stdout.write(f"{display}\0icon\x1f{icon}\x1finfo\x1f{p.pid}\n")
PY
    }
    case "${ROFI_RETV:-0}" in
        0)
            echo -en "\0prompt\x1f🎮 Games\n"
            echo -en "\0message\x1f<span alpha='70%'>Enter</span> launch  <span alpha='40%'>•</span>  <span alpha='70%'>Alt+m</span> mount  <span alpha='70%'>Alt+u</span> unmount\n"
            echo -en "\0markup-rows\x1ftrue\n"
            echo -en "\0use-hot-keys\x1ftrue\n"
            _python_modi
            ;;
        1|10|11)
            pid="${ROFI_INFO:-}"
            [[ -z "$pid" ]] && exit 0
            action="launch"
            [[ "${ROFI_RETV}" == "10" ]] && action="mount"
            [[ "${ROFI_RETV}" == "11" ]] && action="unmount"
            # minimal notify + detach (no lock needed for this ephemeral call)
            if command -v notify-send >/dev/null 2>&1; then
                case "$action" in
                    launch)  notify-send -a "Game Runner" -i "input-gaming" "Launching $pid" >/dev/null 2>&1 || true ;;
                    mount)   notify-send -a "Game Runner" -i "drive-harddisk" "Mounting $pid" >/dev/null 2>&1 || true ;;
                    unmount) notify-send -a "Game Runner" -i "media-eject" "Unmounting $pid" >/dev/null 2>&1 || true ;;
                esac
            fi
            case "$action" in
                launch)
                    if command -v dusky-run >/dev/null 2>&1; then
                        dusky-run python3 "$_MASTER_MODI" run "$pid" >/dev/null 2>&1 &
                    elif command -v hyprctl >/dev/null 2>&1 && [[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
                        _cmd=$(printf 'python3 %q run %q' "$_MASTER_MODI" "$pid")
                        hyprctl dispatch exec -- "$_cmd" >/dev/null 2>&1 &
                    else
                        nohup python3 "$_MASTER_MODI" run "$pid" >/dev/null 2>&1 &
                    fi
                    disown 2>/dev/null || true
                    ;;
                mount)
                    if command -v dusky-run >/dev/null 2>&1; then
                        dusky-run python3 "$_MASTER_MODI" mount "$pid" >/dev/null 2>&1 &
                    else
                        nohup python3 "$_MASTER_MODI" mount "$pid" >/dev/null 2>&1 &
                    fi
                    disown 2>/dev/null || true
                    ;;
                unmount)
                    if command -v dusky-run >/dev/null 2>&1; then
                        dusky-run python3 "$_MASTER_MODI" unmount "$pid" >/dev/null 2>&1 &
                    else
                        nohup python3 "$_MASTER_MODI" unmount "$pid" >/dev/null 2>&1 &
                    fi
                    disown 2>/dev/null || true
                    ;;
            esac
            ;;
        *) exit 0 ;;
    esac
    exit 0
fi

# --- LOCKING TO PREVENT CONCURRENT DMENU INSTANCES ---
# FD 9 holds the lock; must be CLOEXEC so games launched via dusky-run/systemd-run don't inherit it
# (otherwise the lock stays held while the game runs and the keybind appears dead)
readonly LOCK_FILE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/rofi_game_runner.lock"
umask 077
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    exit 0
fi
# Ensure lock is released on exit and not inherited by game children
release_lock() { exec 9>&- 2>/dev/null || true; }
trap 'release_lock' EXIT

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------
readonly MASTER_RUNNER="${MASTER_RUNNER:-$DEFAULT_MASTER}"
readonly ROFI_CONFIG="${ROFI_CONFIG:-$HOME/.config/rofi/config.rasi}"
readonly MEMORY_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/dusky/settings/rofi_game_runner/memory"
readonly APP_NAME="game-runner"
# Fixed height: 10 visible rows, scrollable + searchable (fuzzy). Prevents tall overflow beyond screen.
readonly ROFI_THEME_STR='window { width: 720px; height: 500px; } listview { lines: 10; fixed-height: true; scrollbar: true; } element-text { markup: true; }'

readonly -a REQUIRED_CMDS=(rofi python3)

# ------------------------------------------------------------------------------
# Error handling & logging (mirrors rofi_theme.sh)
# ------------------------------------------------------------------------------
have_cmd() { command -v "$1" >/dev/null 2>&1; }

log_info()  { have_cmd logger && logger -p user.info  -t "$APP_NAME" -- "$1" || true; }
log_error() { have_cmd logger && logger -p user.err   -t "$APP_NAME" -- "$1" || true; }

notify() {
    local urgency="$1" title="$2" body="${3:-}"
    if have_cmd notify-send; then
        notify-send -u "$urgency" -a "Game Runner" -- "$title" "$body" >/dev/null 2>&1 || true
    fi
}

fatal() {
    local msg="$1" body="${2:-$1}"
    log_error "$msg"
    notify critical "Game Runner — Error" "$body"
    printf 'x %s\n' "$msg" >&2
    exit 1
}

on_unexpected_error() {
    local code=$1 line=$2
    log_error "Unhandled error at line $line (exit $code)."
    notify critical "Game Runner — Error" "Unexpected failure at line $line."
    exit "$code"
}
trap 'on_unexpected_error $? $LINENO' ERR

require_commands() {
    local c
    for c in "${REQUIRED_CMDS[@]}"; do
        have_cmd "$c" || fatal "Missing required command: $c" "Missing dependency: $c (pacman -S $c)"
    done
    [[ -f "$MASTER_RUNNER" && -r "$MASTER_RUNNER" ]] || fatal "Controller missing: $MASTER_RUNNER" "master_runner.py not found at $MASTER_RUNNER"
}

# ------------------------------------------------------------------------------
# Memory persistence (last selected game)
# ------------------------------------------------------------------------------
ensure_memory_file() {
    if [[ ! -f "$MEMORY_FILE" ]]; then
        mkdir -p -- "$(dirname "$MEMORY_FILE")"
        touch -- "$MEMORY_FILE"
    fi
}

read_memory() {
    local key="$1"
    [[ -f "$MEMORY_FILE" ]] || return 0
    grep -E "^${key}=" "$MEMORY_FILE" 2>/dev/null | tail -n 1 | cut -d'=' -f2- || true
}

write_memory() {
    local key="$1" val="$2"
    ensure_memory_file
    # shellcheck disable=SC2016
    sed -i "/^${key}=/d" "$MEMORY_FILE" 2>/dev/null || true
    printf '%s=%s\n' "$key" "$val" >> "$MEMORY_FILE"
}

# ------------------------------------------------------------------------------
# Rofi helpers (mirrors rofi_theme.sh)
# ------------------------------------------------------------------------------
is_rofi_abort_exit() {
    local code=$1
    [[ $code -eq 1 || $code -eq 130 || $code -eq 143 ]] && return 0
    (( code >= 10 && code <= 28 )) && return 1
    return 1
    # 1/130/143 are user aborts; 10-28 are custom keybinds which we handle as success
}

# Generic string menu (for main options) — strict index matching like rofi_theme.sh
run_menu() {
    local prompt="$1" allow_custom="$2" default_selection="${3:-}"
    shift 3
    local options=("$@")
    local selected exit_code=0

    local -a rofi_cmd=(rofi -dmenu -i -p "$prompt" -theme-str "$ROFI_THEME_STR" -format s)
    [[ "$allow_custom" == "false" ]] && rofi_cmd+=(-no-custom)
    if [[ -f "$ROFI_CONFIG" ]]; then
        rofi_cmd+=(-theme "$ROFI_CONFIG")
    fi

    if (( ${#options[@]} > 0 )); then
        local sel_row=0
        if [[ -n "$default_selection" ]]; then
            for i in "${!options[@]}"; do
                if [[ "${options[$i]}" == "$default_selection" ]]; then
                    sel_row=$i
                    break
                fi
            done
        fi
        rofi_cmd+=(-selected-row "$sel_row")
        selected=$(printf '%s\n' "${options[@]}" | "${rofi_cmd[@]}") || exit_code=$?
    else
        selected=$("${rofi_cmd[@]}" </dev/null) || exit_code=$?
    fi

    if [[ $exit_code -eq 0 ]]; then
        printf '%s' "$selected"
        return 0
    fi
    if [[ $exit_code -eq 1 || $exit_code -eq 130 || $exit_code -eq 143 ]]; then
        return 1
    fi
    if (( exit_code >= 10 && exit_code <= 28 )); then
        # custom key — treat as abort for generic menu; caller should use game picker for customs
        return 1
    fi
    fatal "Rofi failed at '$prompt' (exit $exit_code)"
}

escape_pango() {
    local s="$1"
    s="${s//&/&amp;}"
    s="${s//</&lt;}"
    s="${s//>/&gt;}"
    s="${s//\"/&quot;}"
    s="${s//\'/&apos;}"
    printf '%s' "$s"
}

usage() {
    cat <<'EOF'
rofi_game_runner.sh — Rofi menu for Master Game Runner

USAGE:
  rofi_game_runner.sh [OPTIONS]

OPTIONS:
  --all, -a          Show all profiles (default: installed only)
  --no-toggle        Don't kill existing rofi (disable toggle behaviour)
  --rofi-mode        Internal: rofi custom modi provider
  --wizard           Show main wizard (Launch / Mount / Doctor ...)
  -h, --help         Show help

KEYS (game picker — installed only, searchable):
  Enter              Launch selected game
  Type               Fuzzy filter (fzf, case-insensitive)
  ↑↓ / Tab           Navigate (scrollable, 10 visible)
  Alt+m              Mount game data (DwarFS + overlay)
  Alt+u              Unmount game data
  Esc                Cancel

ENV:
  MASTER_RUNNER      Override path to master_runner.py
  ROFI_CONFIG        Override rofi theme path

EXAMPLES:
  ./rofi_game_runner.sh
  ./rofi_game_runner.sh --all
  ./rofi_game_runner.sh --wizard
  rofi -show games -modi "games:./rofi_game_runner.sh --rofi-mode"

EOF
    exit 0
}

# ------------------------------------------------------------------------------
# Profile catalogue (python, no subprocess forks)
# ------------------------------------------------------------------------------
generate_raw() {
    local show_all="$1"
    python3 - "$show_all" "$MASTER_RUNNER" <<'PY' 2>/dev/null
import sys, importlib.util
from pathlib import Path
show_all = sys.argv[1] == "true"
master_path = Path(sys.argv[2]).expanduser()
spec = importlib.util.spec_from_file_location("master_runner", str(master_path))
try:
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
except SystemExit as e:
    print(f"ERR SystemExit: {e}", file=sys.stderr)
    sys.exit(2)
except Exception as e:
    print(f"ERR import: {e}", file=sys.stderr)
    sys.exit(2)
mgr = mod.ProfileManager()
profs = mod.catalogue(mgr, show_all=show_all)
for p in profs:
    paths = None
    try:
        paths = mod.resolve_paths(p)
        st = mod.MountEngine.status(paths)
        mstate = str(st.state)
    except Exception:
        mstate = "unknown"
    raw_icon = (p.icon or "").strip() or "applications-games"
    # Intelligent icon fallback — dynamic, not hardcoded
    icon = raw_icon
    if icon.startswith("/") and not Path(icon).is_file():
        # Probe game_dir for a usable icon (fast, 2-level bounded search, no deep walk)
        found = None
        try:
            # Prefer the resolved game_dir if we have it
            search_roots = []
            if paths is not None and hasattr(paths, 'game_dir'):
                search_roots.append(Path(paths.game_dir))
                # also check one level up for cases where icon was expected at root but lives in subdir
                search_roots.append(Path(paths.game_dir) / "BeamNG.drive")
            # Fallback: raw game_dir from profile
            raw_gd = str(p.get("paths.game_dir","")).strip()
            if raw_gd:
                gd = Path(raw_gd).expanduser()
                if not gd.is_absolute():
                    gd = (p.path.parent / gd).resolve() if hasattr(p, 'path') else gd
                if gd.is_dir() and gd not in search_roots:
                    search_roots.append(gd)
            for root in search_roots:
                if not root.is_dir():
                    continue
                # common names first (fast path)
                for cand in [root / "icon-beamng.ico", root / "icon.png", root / "icon.ico", root / "steam.png"]:
                    if cand.is_file():
                        found = cand
                        break
                if found:
                    break
                # bounded glob: top level + one subdir, small & fast
                for pat in ["*.ico", "*.png", "*.jpg"]:
                    for m in sorted(root.glob(pat)):
                        if m.is_file():
                            found = m
                            break
                    if found:
                        break
                    for sub in root.iterdir():
                        if sub.is_dir() and not sub.is_symlink():
                            # only check immediate subdir, no recursion
                            try:
                                for m in sorted(sub.glob(pat)):
                                    if m.is_file():
                                        found = m
                                        break
                                if found:
                                    break
                            except OSError:
                                continue
                    if found:
                        break
                if found:
                    break
        except Exception:
            found = None
        if found and Path(found).is_file():
            icon = str(found)
        else:
            icon = "applications-games"  # theme fallback, always available via Papirus-Dark
    runtime = p.runtime or "native"
    gpu = str(p.get("graphics.gpu","auto") or "auto")
    genre = str(p.get("meta.genre","") or "")
    def clean(s):
        return str(s).replace("\n"," ").replace("\r"," ").replace("\x1f"," ").replace("\t"," ").strip()
    pid = clean(p.pid)
    name = clean(p.name)
    icon = clean(icon)
    # Final safety: if still absolute and missing, force theme fallback
    if icon.startswith("/") and not Path(icon).is_file():
        icon = "applications-games"
    sys.stdout.write(f"{pid}\x1f{name}\x1f{icon}\x1f{runtime}\x1f{gpu}\x1f{genre}\x1f{mstate}\x1f{1 if mod.profile_installed(p) else 0}\n")
PY
}

# ------------------------------------------------------------------------------
# Game picker — icon-aware, markup, fuzzy, with memory & custom keybinds
# Returns selected pid via stdout and exit code indicates action:
#   0 → launch, 10 → mount, 11 → unmount, 12 → toggle filter, 1 → abort
# ------------------------------------------------------------------------------
pick_game() {
    local show_all="$1" default_action="${2:-launch}"  # "true"/"false"
    local tmp_raw
    tmp_raw=$(mktemp)
    # shellcheck disable=SC2064
    trap "rm -f -- $tmp_raw 2>/dev/null" RETURN

    if ! generate_raw "$show_all" > "$tmp_raw"; then
        # Fallback via list --json (rare)
        rm -f "$tmp_raw"; tmp_raw=$(mktemp)
        # shellcheck disable=SC2064
        trap "rm -f -- $tmp_raw 2>/dev/null" RETURN
        local json
        local -a list_args=(list --json)
        [[ "$show_all" == "true" ]] && list_args+=(--all)
        json=$(python3 "$MASTER_RUNNER" "${list_args[@]}" 2>/dev/null || echo "[]")
        if [[ -z "$json" || "$json" == "[]" ]]; then
            fatal "No profiles found" "No profiles discovered via $MASTER_RUNNER"
        fi
        if have_cmd jq; then
            echo "$json" | jq -r '.[] | [.id, .name, "applications-games", .runtime, "auto", "", "unknown", (if .installed then "1" else "0" end)] | join("\u001f")' > "$tmp_raw" || fatal "Failed to parse profile list"
        else
            echo "$json" | python3 -c '
import json, sys
for p in json.load(sys.stdin):
    print("\x1f".join((str(p.get("id", "")), str(p.get("name", "")), "applications-games", str(p.get("runtime", "native")), "auto", "", "unknown", "1" if p.get("installed") else "0")))
' > "$tmp_raw" || fatal "Failed to generate fallback list"
        fi
    fi

    if [[ ! -s "$tmp_raw" ]]; then
        if [[ "$show_all" == "false" ]]; then
            notify normal "No installed games" "Showing all profiles…"
            pick_game "true" "$default_action"
            return $?
        fi
        fatal "No displayable profiles" "No profiles in $(dirname "$MASTER_RUNNER")/profiles"
    fi

    # total/hidden kept for debug; installed-only mode uses pids count
    # shellcheck disable=SC2034
    local total installed_count hidden_count
    total=$(wc -l < "$tmp_raw" | tr -d ' ')
    installed_count=$(grep -c $'\x1f1$' "$tmp_raw" 2>/dev/null || echo "$total")
    # shellcheck disable=SC2034
    hidden_count=$((total - installed_count))

    # Build arrays with markup
    local -a pids=() displays=() icons=() names=() runtimes=()
    local idx=0
    # shellcheck disable=SC2034
    local -a mounts=()  # retained for debugging
    # shellcheck disable=SC2034
    while IFS=$'\x1f' read -r pid name icon runtime gpu genre mstate installed; do
        [[ -z "$pid" ]] && continue
        pids[idx]="$pid"
        names[idx]="$name"
        runtimes[idx]="$runtime"
        mounts[idx]="$mstate"
        icons[idx]="${icon:-applications-games}"

        local esc_name esc_runtime esc_gpu esc_genre esc_state detail display state_color
        esc_name=$(escape_pango "$name")
        esc_runtime=$(escape_pango "$runtime")
        esc_gpu=$(escape_pango "$gpu")
        esc_genre=$(escape_pango "$genre")
        esc_state=$(escape_pango "$mstate")
        case "$mstate" in
            mounted) state_color="#a6e3a1" ;;
            stale)   state_color="#f38ba8" ;;
            partial) state_color="#f9e2af" ;;
            *)       state_color="#9399b2" ;;
        esac
        if [[ -n "$esc_genre" ]]; then
            if (( ${#esc_genre} > 28 )); then esc_genre="${esc_genre:0:25}…"; fi
            detail="${esc_runtime} <span alpha='40%'>•</span> ${esc_genre}"
        else
            detail="${esc_runtime} <span alpha='40%'>•</span> ${esc_gpu}"
        fi
        detail="${detail} <span alpha='30%'>•</span> <span color='${state_color}'>${esc_state}</span>"
        display="<b>${esc_name}</b>  <span size='small' alpha='55%'>${detail}</span>"
        displays[idx]="$display"
        idx=$((idx+1))
    done < "$tmp_raw"

    if (( ${#pids[@]} == 0 )); then
        fatal "No displayable profiles" "Profile list empty after filtering"
    fi

    # Memory: last selected pid → row index
    local last_pid last_idx=0
    last_pid=$(read_memory "last_game")
    if [[ -n "$last_pid" ]]; then
        for i in "${!pids[@]}"; do
            if [[ "${pids[$i]}" == "$last_pid" ]]; then
                last_idx=$i
                break
            fi
        done
    fi

    # Prepare rofi input with NUL icon metadata (bash cannot store \0, so use file)
    local rofi_input rofi_out
    rofi_input=$(mktemp)
    rofi_out=$(mktemp)
    # shellcheck disable=SC2064
    trap "rm -f -- $rofi_input $rofi_out $tmp_raw 2>/dev/null" RETURN

    for i in "${!displays[@]}"; do
        printf '%s\0icon\x1f%s\n' "${displays[i]}" "${icons[i]}" >> "$rofi_input"
    done

    local prompt mesg add_theme placeholder
    if [[ "$show_all" == "true" ]]; then
        prompt="🎮 Games"
        placeholder="${#pids[@]} installed — all"
    else
        prompt="🎮 Games"
        placeholder="${#pids[@]} installed"
    fi
    # Minimal mesg: only mount/unmount (Enter/navigate/Esc are obvious, placeholder replaces filter hint)
    mesg="<span alpha='70%'>Alt+m</span> mount  <span alpha='40%'>•</span>  <span alpha='70%'>Alt+u</span> unmount"
    # Placeholder appears ghosted in entry, disappears on typing; list limited to 10 visible, scrollable
    add_theme="entry { placeholder: \"$placeholder\"; } element-text { markup: true; } window { width: 720px; height: 500px; } listview { lines: 10; fixed-height: true; scrollbar: true; }"

    local rofi_args=(
        -dmenu -i -p "$prompt" -mesg "$mesg" -markup-rows
        -matching fuzzy -sort -sorting-method fzf -no-custom -format i
        -lines 10
        -kb-custom-1 "Alt+m" -kb-custom-2 "Alt+u"
        -selected-row "$last_idx"
    )
    if [[ -f "$ROFI_CONFIG" ]]; then
        rofi_args+=(-theme "$ROFI_CONFIG")
    fi
    rofi_args+=(-theme-str "$ROFI_THEME_STR $add_theme")

    local exit_code=0
    set +e
    cat "$rofi_input" | rofi "${rofi_args[@]}" > "$rofi_out" || exit_code=$?
    set -e

    if [[ $exit_code -eq 1 || $exit_code -eq 130 || $exit_code -eq 143 ]]; then
        return 1
    fi

    local sel_idx
    sel_idx=$(head -n 1 "$rofi_out" 2>/dev/null | tr -d '[:space:]')
    if [[ -z "$sel_idx" ]]; then
        return 1
    fi
    if ! [[ "$sel_idx" =~ ^[0-9]+$ ]] || (( sel_idx < 0 || sel_idx >= ${#pids[@]} )); then
        log_error "Invalid selection index: $sel_idx"
        return 1
    fi

    local sel_pid="${pids[sel_idx]}"
    local sel_name="${names[sel_idx]}"
    write_memory "last_game" "$sel_pid"

    # Dispatch based on exit code (10/11 for mount/unmount)
    case "$exit_code" in
        10)  _do_mount "$sel_pid" "$sel_name" "${icons[sel_idx]}" ;;
        11)  _do_unmount "$sel_pid" "$sel_name" "${icons[sel_idx]}" ;;
        0)
            case "$default_action" in
                mount)   _do_mount "$sel_pid" "$sel_name" "${icons[sel_idx]}" ;;
                unmount) _do_unmount "$sel_pid" "$sel_name" "${icons[sel_idx]}" ;;
                *)       _do_launch "$sel_pid" "$sel_name" "${icons[sel_idx]}" "${runtimes[sel_idx]}" ;;
            esac ;;
        *) return 1 ;;
    esac
    return 0
}

_do_launch() {
    local pid="$1" name="$2" icon="$3" runtime="${4:-}"
    release_lock
    log_info "Launching $name ($pid)"
    notify normal "Launching $name" "$pid • $runtime"
    if have_cmd dusky-run; then
        setsid dusky-run python3 "$MASTER_RUNNER" run "$pid" >/dev/null 2>&1 & disown 2>/dev/null || true
    elif have_cmd hyprctl && [[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
        local cmd
        cmd=$(printf 'python3 %q run %q' "$MASTER_RUNNER" "$pid")
        hyprctl dispatch exec -- "$cmd" >/dev/null 2>&1 || nohup python3 "$MASTER_RUNNER" run "$pid" >/dev/null 2>&1 & disown 2>/dev/null || true
    else
        nohup python3 "$MASTER_RUNNER" run "$pid" >/dev/null 2>&1 & disown 2>/dev/null || true
    fi
    printf ':: launching %s (%s) via %s run %s\n' "$name" "$pid" "$MASTER_RUNNER" "$pid" >&2
}

_do_mount() {
    local pid="$1" name="$2" icon="$3"
    release_lock
    log_info "Mounting $name ($pid)"
    notify normal "Mounting $name" "$pid"
    if have_cmd dusky-run; then
        setsid dusky-run python3 "$MASTER_RUNNER" mount "$pid" >/dev/null 2>&1 & disown 2>/dev/null || true
    else
        nohup python3 "$MASTER_RUNNER" mount "$pid" >/dev/null 2>&1 & disown 2>/dev/null || true
    fi
    printf ':: mounting %s (%s)\n' "$name" "$pid" >&2
}

_do_unmount() {
    local pid="$1" name="$2" icon="$3"
    release_lock
    log_info "Unmounting $name ($pid)"
    notify normal "Unmounting $name" "$pid"
    if have_cmd dusky-run; then
        setsid dusky-run python3 "$MASTER_RUNNER" unmount "$pid" >/dev/null 2>&1 & disown 2>/dev/null || true
    else
        nohup python3 "$MASTER_RUNNER" unmount "$pid" >/dev/null 2>&1 & disown 2>/dev/null || true
    fi
    printf ':: unmounting %s (%s)\n' "$name" "$pid" >&2
}

# ------------------------------------------------------------------------------
# Main wizard (optional) — mirrors rofi_theme.sh structure
# ------------------------------------------------------------------------------
wizard_main() {
    local choice last_main
    local -a opts=(
        "🎮  Launch Game"
        "💾  Mount Game Data"
        "📤  Unmount Game Data"
        "🔍  Toggle Filter (installed / all)"
        "🩺  Doctor (system diagnostics)"
        "✅  Validate Profiles"
        "📂  Open Profiles Folder"
        "🚪  Exit"
    )
    while true; do
        last_main=$(read_memory "wizard_main")
        choice=$(run_menu "Game Runner" false "$last_main" "${opts[@]}") || return 0
        [[ -n "$choice" && "$choice" != "🚪  Exit" ]] && write_memory "wizard_main" "$choice"
        case "$choice" in
            "🎮  Launch Game")
                SHOW_ALL="false" pick_game "false" || true
                ;;
            "💾  Mount Game Data")
                pick_game "false" "mount" || true
                ;;
            "📤  Unmount Game Data")
                pick_game "true" "unmount" || true
                ;;
            "🔍  Toggle Filter"*)
                if [[ "$SHOW_ALL" == "true" ]]; then SHOW_ALL="false"; else SHOW_ALL="true"; fi
                write_memory "show_all" "$SHOW_ALL"
                notify normal "Filter toggled" "$SHOW_ALL"
                ;;
            "🩺  Doctor"*)
                if have_cmd foot; then
                    foot --app-id=game-doctor -e bash -c "python3 \"$MASTER_RUNNER\" doctor; echo \"[doctor] done — press Enter\"; read" &
                else
                    python3 "$MASTER_RUNNER" doctor 2>&1 | head -n 100
                    notify normal "Doctor" "Check terminal for details"
                fi
                ;;
            "✅  Validate"*)
                if have_cmd foot; then
                    foot --app-id=game-validate -e bash -c "python3 \"$MASTER_RUNNER\" validate --all; echo \"[validate] done — press Enter\"; read" &
                else
                    python3 "$MASTER_RUNNER" validate --all 2>&1 | head -n 100
                fi
                ;;
            "📂  Open Profiles"*)
                have_cmd xdg-open && xdg-open "$SCRIPT_DIR/../profiles" >/dev/null 2>&1 & disown || true
                ;;
            "🚪  Exit"* ) return 0 ;;
            *) return 0 ;;
        esac
    done
}

# ------------------------------------------------------------------------------
# CLI & entry
# ------------------------------------------------------------------------------
SHOW_ALL="false"
SHOW_WIZARD="false"
NO_TOGGLE="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --all|-a)       SHOW_ALL="true"; shift ;;
        --no-toggle)    NO_TOGGLE="true"; shift ;;
        --wizard|-w)    SHOW_WIZARD="true"; shift ;;
        --help|-h)      usage ;;
        --rofi-mode)    shift ;; # already handled, ignore
        --) shift; break ;;
        -*)  printf 'unknown option: %s\n' "$1" >&2; usage ;;
        *) break ;;
    esac
done

main() {
    require_commands

    # Restore last filter from memory if not explicitly set
    if [[ "$SHOW_ALL" == "false" ]]; then
        local mem_filter
        mem_filter=$(read_memory "show_all")
        if [[ "$mem_filter" == "true" ]]; then
            SHOW_ALL="true"
        fi
    fi

    # Toggle behaviour: second press kills rofi
    if [[ "$NO_TOGGLE" == "false" ]] && pgrep -x rofi >/dev/null 2>&1; then
        pkill rofi 2>/dev/null || true
        exit 0
    fi

    if [[ "$SHOW_WIZARD" == "true" ]]; then
        wizard_main
        exit 0
    fi

    # Direct picker (default) — single shot, respects custom keybinds inside pick_game
    if ! pick_game "$SHOW_ALL"; then
        # Abort or toggle already handled; exit cleanly
        exit 0
    fi
}

main "$@"
