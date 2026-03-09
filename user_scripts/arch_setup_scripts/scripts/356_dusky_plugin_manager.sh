#!/usr/bin/env bash
# ==============================================================================
# ELITE HYPRLAND PLUGIN MANAGER
# Target: Arch Linux / Hyprland / UWSM / Wayland (Bash 5.3.9+)
# ==============================================================================
# ARCHITECTURE & SAFETY PROTOCOLS:
# 1. State Lifecycle: Uses BEGIN/END markers for perfectly idempotent 
#    installations and surgical uninstallation without breaking user configs.
# 2. Credential Heartbeat: Acquires `sudo` once and maintains a background 
#    keep-alive loop to prevent multi-prompting during long compiles.
# 3. Headless Mode: Native CLI flag parsing for automated dotfile bootstrapping.
# 4. Atomic I/O: Guarantees zero file corruption via strictly synced tmp moves.
# ==============================================================================

set -euo pipefail

if (( BASH_VERSINFO[0] < 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 3) )); then
    printf '\033[1;33m[WARN]\033[0m This script is optimized for Bash 5.3+. Proceed with caution.\n' >&2
fi

# ==============================================================================
# ▼ USER CONFIGURATION: PATHS & PLUGINS ▼
# ==============================================================================

readonly PLUGINS_CONF="${HOME}/.config/hypr/edit_here/source/plugins.conf"
readonly KEYBINDS_CONF="${HOME}/.config/hypr/edit_here/source/keybinds.conf"

declare -a PLUGIN_IDS=()
declare -A PLUGIN_REPO=()
declare -A PLUGIN_NAME=()
declare -A PLUGIN_DEPS=()
declare -A PLUGIN_CONF=()
declare -A PLUGIN_CONF_REGEX=()
declare -A PLUGIN_BINDS=()
declare -A PLUGIN_BIND_REGEX=()

# Helper to register plugins
register_plugin() {
    local id="$1" repo="$2" name="$3" deps="$4"
    PLUGIN_IDS+=("$id")
    PLUGIN_REPO["$id"]="$repo"
    PLUGIN_NAME["$id"]="$name"
    PLUGIN_DEPS["$id"]="$deps"
}

# ------------------------------------------------------------------------------
# 1. Hyprscrolling
register_plugin "scrolling" "https://github.com/hyprwm/hyprland-plugins" "hyprscrolling" "cmake meson cpio pkgconf"
read -r -d '' PLUGIN_CONF["scrolling"] << 'EOF' || true
plugin {
    hyprscrolling {
        column_width = 0.5
        focus_fit_method = 0
    }
}
EOF
PLUGIN_CONF_REGEX["scrolling"]="hyprscrolling[[:space:]]*\{"

read -r -d '' PLUGIN_BINDS["scrolling"] << 'EOF' || true
bindd = $mainMod ALT, l, hyprscrolling layoutmsg +, layoutmsg, move +col
bindd = $mainMod ALT, h, hyprscrolling layoutmsg -, layoutmsg, move -col

# eg, for reference:
# bind = $mainMod, period, layoutmsg, move +col
# bind = $mainMod, comma, layoutmsg, move -col
# bind = $mainMod SHIFT, period, layoutmsg, movewindowto r
# bind = $mainMod SHIFT, comma, layoutmsg, movewindowto l
# bind = $mainMod SHIFT, up, layoutmsg, movewindowto u
# bind = $mainMod SHIFT, down, layoutmsg, movewindowto d
EOF
PLUGIN_BIND_REGEX["scrolling"]="layoutmsg,.*move.*col"

# ------------------------------------------------------------------------------
# 2. Hyprexpo
register_plugin "expo" "https://github.com/hyprwm/hyprland-plugins" "hyprexpo" "cmake meson cpio pkgconf"
read -r -d '' PLUGIN_CONF["expo"] << 'EOF' || true
plugin {
    hyprexpo {
        columns = 3
        gap_size = 5
        bg_col = rgb(111111)
        workspace_method = center current
    }
}
EOF
PLUGIN_CONF_REGEX["expo"]="hyprexpo[[:space:]]*\{"

read -r -d '' PLUGIN_BINDS["expo"] << 'EOF' || true
bind = ALT, TAB, hyprexpo:expo, toggle
EOF
PLUGIN_BIND_REGEX["expo"]="hyprexpo:expo,.*toggle"

# ------------------------------------------------------------------------------
# 3. Dynamic Cursors
register_plugin "cursors" "https://github.com/VirtCode/hypr-dynamic-cursors" "hypr-dynamic-cursors" "cmake meson cpio pkgconf"
read -r -d '' PLUGIN_CONF["cursors"] << 'EOF' || true
plugin:dynamic-cursors {
    enabled = true
    mode = tilt
    tilt {

        # sets the cursor behaviour, supports these values:
        # tilt    - tilt the cursor based on x-velocity
        # rotate  - rotate the cursor based on movement direction
        # stretch - stretch the cursor shape based on direction and velocity
        # none    - do not change the cursors behaviour
        limit = 5000

        # relationship between speed and tilt, supports these values:
        # linear             - a linear function is used
        # quadratic          - a quadratic function is used (most realistic to actual air drag)
        # negative_quadratic - negative version of the quadratic one, feels more aggressive
        # see `activation` in `src/mode/utils.cpp` for how exactly the calculation is done
        function = linear
    }
    shake {
        enabled = true
    }
}
EOF
PLUGIN_CONF_REGEX["cursors"]="plugin:dynamic-cursors[[:space:]]*\{"
PLUGIN_BINDS["cursors"]=""
PLUGIN_BIND_REGEX["cursors"]=""

# ------------------------------------------------------------------------------
# 4. Hypr-Hot-Edge
register_plugin "hotedge" "https://github.com/claychinasky/hypr-hot-edge" "hypr-hot-edge" "cmake meson cpio pkgconf"
read -r -d '' PLUGIN_CONF["hotedge"] << 'EOF' || true
plugin {
    hot-edge {
        edge1 {
            enabled = 1
            side = right
            trigger_width = 15
            dwell_time = 150
            special_workspace = right_panel
            target_monitor = "*"
        }
    }
}
EOF
PLUGIN_CONF_REGEX["hotedge"]="hot-edge[[:space:]]*\{"

read -r -d '' PLUGIN_BINDS["hotedge"] << 'EOF' || true
bindd = SUPER CTRL, H, hypr-hot-edge toggle right, hotedge:toggle, right
bindd = SUPER CTRL, B, hypr-hot-edge toggle bottom, hotedge:toggle, bottom
EOF
PLUGIN_BIND_REGEX["hotedge"]="hotedge:toggle"

# ------------------------------------------------------------------------------
# 5. Hyprgrass
register_plugin "grass" "https://github.com/horriblename/hyprgrass" "hyprgrass" "cmake meson cpio pkgconf glm ninja"
read -r -d '' PLUGIN_CONF["grass"] << 'EOF' || true
plugin {
    touch_gestures {
        workspace_swipe_fingers = 3
        workspace_swipe_cancel_ratio = 0.15
        long_press_delay = 400
    }
}
EOF
PLUGIN_CONF_REGEX["grass"]="touch_gestures[[:space:]]*\{"

read -r -d '' PLUGIN_BINDS["grass"] << 'EOF' || true
bind = , edge:r:l, workspace, +1
bind = , edge:l:r, workspace, -1
bind = , swipe:3:u, hyprexpo:expo, toggle
EOF
PLUGIN_BIND_REGEX["grass"]="swipe:.*|edge:.*"

# ------------------------------------------------------------------------------
# 6. Hyprbars (hyprwm/hyprland-plugins)
# ------------------------------------------------------------------------------
register_plugin "bars" "https://github.com/hyprwm/hyprland-plugins" "hyprbars" "cmake meson cpio pkgconf"
read -r -d '' PLUGIN_CONF["bars"] << 'EOF' || true
plugin {
    hyprbars {
        bar_height = 20
        bar_color = rgb(2a2a2a)
        col.text = rgb(eeeeee)
        bar_text_size = 10
        bar_text_font = Sans
        bar_padding = 7
        bar_button_padding = 5
        hyprbars-button = rgb(ff4040), 12, 󰖭, hyprctl dispatch killactive
        hyprbars-button = rgb(eeee11), 12, , hyprctl dispatch fullscreen 1
    }
}
EOF
PLUGIN_CONF_REGEX["bars"]="hyprbars[[:space:]]*\{"

# Hyprbars relies on mouse-interaction with the bars themselves; no external keybinds required.
PLUGIN_BINDS["bars"]=""
PLUGIN_BIND_REGEX["bars"]=""

# ------------------------------------------------------------------------------
# 7. hy3 (outfoxxed/hy3) - i3/Sway manual tiling engine
# ------------------------------------------------------------------------------
register_plugin "hy3" "https://github.com/outfoxxed/hy3" "hy3" "cmake meson cpio pkgconf"
read -r -d '' PLUGIN_CONF["hy3"] << 'EOF' || true
plugin {
    hy3 {
        tabs {
            height = 15
            padding = 5
            render_text = true
        }
        autotile {
            enable = true
            ephemeral_groups = false
        }
    }
}
EOF
PLUGIN_CONF_REGEX["hy3"]="hy3[[:space:]]*\{"

read -r -d '' PLUGIN_BINDS["hy3"] << 'EOF' || true
# Auto-generated by Plugin Manager: hy3 (i3-like manipulation)
bind = $mainMod, H, hy3:movefocus, l
bind = $mainMod, L, hy3:movefocus, r
bind = $mainMod, K, hy3:movefocus, u
bind = $mainMod, J, hy3:movefocus, d
bind = $mainMod SHIFT, H, hy3:movewindow, l
bind = $mainMod SHIFT, L, hy3:movewindow, r
bind = $mainMod SHIFT, K, hy3:movewindow, u
bind = $mainMod SHIFT, J, hy3:movewindow, d
bind = $mainMod, V, hy3:makegroup, v
bind = $mainMod, B, hy3:makegroup, h
bind = $mainMod, T, hy3:changegroup, toggletab
EOF
PLUGIN_BIND_REGEX["hy3"]="hy3:movefocus|hy3:movewindow|hy3:makegroup|hy3:changegroup"

# ------------------------------------------------------------------------------
# 8. Hyprspace (KZDKM/Hyprspace) - Workspace Overview
# ------------------------------------------------------------------------------
register_plugin "space" "https://github.com/KZDKM/Hyprspace" "Hyprspace" "cmake meson cpio pkgconf"
read -r -d '' PLUGIN_CONF["space"] << 'EOF' || true
plugin {
    overview {
        centerAligned = true
        hideTopLayers = true
        hideOverlayLayers = true
        showNewWorkspace = true
        exitOnClick = true
        exitOnSwitch = true
        drawActiveWorkspace = true
        reverseSwipe = false
    }
}
EOF
PLUGIN_CONF_REGEX["space"]="overview[[:space:]]*\{"

read -r -d '' PLUGIN_BINDS["space"] << 'EOF' || true
# Auto-generated by Plugin Manager: Hyprspace Overview
bindd = $mainMod SHIFT, v, Toggle hyprspace plugin, overview:toggle, all
EOF
PLUGIN_BIND_REGEX["space"]="overview:toggle"

# ------------------------------------------------------------------------------
# 9. Hyprglass (hyprnux/hyprglass)
# ------------------------------------------------------------------------------
register_plugin "glass" "https://github.com/hyprnux/hyprglass" "hyprglass" "cmake meson cpio pkgconf"
read -r -d '' PLUGIN_CONF["glass"] << 'EOF' || true
plugin {
    hyprglass {
        enabled = true
    }
}
EOF
PLUGIN_CONF_REGEX["glass"]="hyprglass[[:space:]]*\{"

PLUGIN_BINDS["glass"]=""
PLUGIN_BIND_REGEX["glass"]=""

# ------------------------------------------------------------------------------
# 10. Hypr-DarkWindow (micha4w/Hypr-DarkWindow)
# ------------------------------------------------------------------------------
register_plugin "darkwindow" "https://github.com/micha4w/Hypr-DarkWindow" "Hypr-DarkWindow" "cmake meson cpio pkgconf"
read -r -d '' PLUGIN_CONF["darkwindow"] << 'EOF' || true
plugin {
    darkwindow {
        load_shaders = invert,tint
    }
}
EOF
PLUGIN_CONF_REGEX["darkwindow"]="darkwindow[[:space:]]*\{"

read -r -d '' PLUGIN_BINDS["darkwindow"] << 'EOF' || true
# Auto-generated by Plugin Manager: Hypr-DarkWindow
bindd = $mainMod CTRL, I, hyprdarkwindow/invert plugin, darkwindow:shadeactive, invert
bindd = $mainMod SHIFT, I, hyprdarkwindow/tint plugin, darkwindow:shadeactive, tint
EOF
PLUGIN_BIND_REGEX["darkwindow"]="darkwindow:shadeactive"

# ==============================================================================
# ▲ END OF USER CONFIGURATION ▲
# ==============================================================================

# --- ANSI Formatting & Globals ---
readonly C_RESET=$'\033[0m'
readonly C_CYAN=$'\033[1;36m'
readonly C_GREEN=$'\033[1;32m'
readonly C_RED=$'\033[1;31m'
readonly C_YELLOW=$'\033[1;33m'

log_info()    { printf '%s[INFO]%s %s\n' "$C_CYAN" "$C_RESET" "$1"; }
log_success() { printf '%s[SUCCESS]%s %s\n' "$C_GREEN" "$C_RESET" "$1"; }
log_warn()    { printf '%s[WARN]%s %s\n' "$C_YELLOW" "$C_RESET" "$1"; }
log_err()     { printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2; }

declare _TMPFILE=""
declare _SUDO_PID=""
declare -A _ADDED_REPOS=()

cleanup() {
    [[ -n "${_TMPFILE:-}" && -f "$_TMPFILE" ]] && rm -f "$_TMPFILE" 2>/dev/null || true
    [[ -n "${_SUDO_PID:-}" ]] && kill "$_SUDO_PID" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# --- Sudo Heartbeat Engine ---
authenticate_sudo() {
    if ! sudo -n true 2>/dev/null; then
        log_info "Elevated privileges required (pacman dependencies / hyprpm headers)."
        sudo -v || { log_err "Sudo authentication failed."; exit 1; }
    fi
    
    if [[ -z "${_SUDO_PID:-}" ]]; then
        (
            while kill -0 "$$" 2>/dev/null; do
                sudo -n true 2>/dev/null || true
                sleep 60
            done
        ) &
        _SUDO_PID=$!
    fi
}

# --- Core Injection Engine (Install) ---
inject_block() {
    local target_file="$1" id="$2" pattern="$3" block="$4"
    if [[ -z "$block" ]]; then return 0; fi

    local target_dir="${target_file%/*}"
    [[ -d "$target_dir" ]] || mkdir -p "$target_dir"
    [[ -f "$target_file" ]] || touch "$target_file"

    local lines=()
    mapfile -t lines < "$target_file"

    # Strict intent respect: Guarded against empty regex latent bug
    for line in "${lines[@]}"; do
        if { [[ -n "$pattern" ]] && [[ "$line" =~ $pattern ]]; } || [[ "$line" == "# --- BEGIN PLUGIN: ${id} ---" ]]; then
            log_info "Configuration for '${id}' already exists in $(basename "$target_file"). Intact."
            return 0
        fi
    done

    _TMPFILE=$(mktemp "${target_file}.tmp.XXXXXXXXXX") || return 1
    chmod --reference="$target_file" "$_TMPFILE" 2>/dev/null || chmod 0644 "$_TMPFILE"

    if ! {
        if (( ${#lines[@]} > 0 )); then
            printf '%s\n' "${lines[@]}"
            printf '\n'
        fi
        printf '# --- BEGIN PLUGIN: %s ---\n' "$id"
        # Strip trailing heredoc newline to prevent blank-line bleed
        printf '%s\n' "${block%$'\n'}"
        printf '# --- END PLUGIN: %s ---\n' "$id"
    } > "$_TMPFILE"; then
        log_err "I/O failure during buffer flush."
        rm -f "$_TMPFILE" 2>/dev/null || true; _TMPFILE=""; return 1
    fi

    if ! { sync "$_TMPFILE" && mv -f "$_TMPFILE" "$target_file"; }; then
        log_err "Atomic write failed."
        rm -f "$_TMPFILE" 2>/dev/null || true; _TMPFILE=""; return 1
    fi

    _TMPFILE=""
    log_success "Injected config block into $(basename "$target_file")"
    return 0
}

# --- Core Removal Engine (Uninstall) ---
remove_block() {
    local target_file="$1" id="$2"
    if [[ ! -f "$target_file" ]]; then return 0; fi

    local lines=()
    mapfile -t lines < "$target_file"

    local output=()
    local in_block=0 found=0

    # Perfectly surgical removal matching, inclusive of whitespace artifact cleanup
    for line in "${lines[@]}"; do
        if [[ "$line" == "# --- BEGIN PLUGIN: ${id} ---" ]]; then
            # Snip out the orphaned blank separator line left from install
            if (( ${#output[@]} > 0 )) && [[ -z "${output[-1]}" ]]; then
                unset 'output[-1]'
            fi
            in_block=1
            found=1
            continue
        fi
        if [[ "$line" == "# --- END PLUGIN: ${id} ---" ]]; then
            in_block=0
            continue
        fi
        if (( in_block == 0 )); then
            output+=("$line")
        fi
    done

    if (( found == 0 )); then
        log_info "No managed block found for '${id}' in $(basename "$target_file")."
        return 0
    fi

    _TMPFILE=$(mktemp "${target_file}.tmp.XXXXXXXXXX") || return 1
    chmod --reference="$target_file" "$_TMPFILE" 2>/dev/null || chmod 0644 "$_TMPFILE"

    if ! {
        if (( ${#output[@]} > 0 )); then
            printf '%s\n' "${output[@]}"
        else
            printf ''
        fi
    } > "$_TMPFILE"; then
        rm -f "$_TMPFILE" 2>/dev/null || true; _TMPFILE=""; return 1
    fi

    if ! { sync "$_TMPFILE" && mv -f "$_TMPFILE" "$target_file"; }; then
        rm -f "$_TMPFILE" 2>/dev/null || true; _TMPFILE=""; return 1
    fi

    _TMPFILE=""
    log_success "Removed config block from $(basename "$target_file")"
    return 0
}

# --- Plugin Operations ---
install_plugin() {
    local id="$1"
    local repo="${PLUGIN_REPO[$id]}" name="${PLUGIN_NAME[$id]}" deps="${PLUGIN_DEPS[$id]}"
    local rc=0

    printf '\n%s>>> Installing: %s <<<%s\n' "$C_GREEN" "$name" "$C_RESET"

    if [[ -n "$deps" ]]; then
        log_info "Installing dependencies via pacman..."
        sudo pacman -S --needed --noconfirm base-devel $deps || log_warn "Assuming dependencies are met."
    fi

    if [[ -z "${_ADDED_REPOS[$repo]:-}" ]]; then
        log_info "Synchronizing repository in hyprpm..."
        set +o pipefail; yes | hyprpm add "$repo" || true; set -o pipefail
        _ADDED_REPOS["$repo"]=1
    fi

    log_info "Enabling plugin via hyprpm..."
    if hyprpm enable "$name"; then
        log_success "Hyprland Plugin Engine enabled: $name"
    else
        log_err "hyprpm failed to enable $name."
        return 1
    fi

    log_info "Checking target configurations..."
    inject_block "$PLUGINS_CONF" "$id" "${PLUGIN_CONF_REGEX[$id]}" "${PLUGIN_CONF[$id]}" || rc=1
    inject_block "$KEYBINDS_CONF" "$id" "${PLUGIN_BIND_REGEX[$id]}" "${PLUGIN_BINDS[$id]}" || rc=1
    return "$rc"
}

uninstall_plugin() {
    local id="$1"
    local name="${PLUGIN_NAME[$id]}"
    local rc=0

    printf '\n%s>>> Uninstalling: %s <<<%s\n' "$C_YELLOW" "$name" "$C_RESET"

    log_info "Disabling plugin via hyprpm..."
    # Disabling avoids destructive repo purges that would break peer plugins
    if hyprpm disable "$name" 2>/dev/null; then
        log_success "Disabled hyprpm plugin: $name"
    else
        log_info "Plugin already disabled or not loaded."
    fi

    log_info "Purging target configurations..."
    remove_block "$PLUGINS_CONF" "$id" || rc=1
    remove_block "$KEYBINDS_CONF" "$id" || rc=1
    return "$rc"
}

update_headers() {
    log_info "Updating hyprpm headers..."
    hyprpm update || { log_err "Failed to update hyprpm headers."; exit 1; }
}

# --- CLI Router ---
show_help() {
    printf "Usage: %s [OPTIONS]\n\n" "${0##*/}"
    printf "Install Flags:\n"
    for id in "${PLUGIN_IDS[@]}"; do printf "  --%-20s Install %s\n" "$id" "${PLUGIN_NAME[$id]}"; done
    printf "  --%-20s Install all plugins\n\n" "all"
    
    printf "Uninstall Flags:\n"
    for id in "${PLUGIN_IDS[@]}"; do printf "  --uninstall-%-20s Uninstall %s\n" "$id" "${PLUGIN_NAME[$id]}"; done
    printf "  --uninstall-%-20s Uninstall all plugins\n\n" "all"
}

main() {
    local failed=0
    local cli_mode=0
    declare -a q_installs=()
    declare -a q_uninstalls=()

    # 1. Parse Arguments
    while [[ $# -gt 0 ]]; do
        cli_mode=1
        case "$1" in
            --help|-h) show_help; exit 0 ;;
            --all) q_installs=("${PLUGIN_IDS[@]}") ;;
            --uninstall-all) q_uninstalls=("${PLUGIN_IDS[@]}") ;;
            --*)
                local opt="${1#--}"
                if [[ "$opt" == uninstall-* ]]; then
                    local pid="${opt#uninstall-}"
                    if [[ -n "${PLUGIN_NAME[$pid]:-}" ]]; then q_uninstalls+=("$pid")
                    else log_err "Unknown plugin: $pid"; exit 1; fi
                else
                    if [[ -n "${PLUGIN_NAME[$opt]:-}" ]]; then q_installs+=("$opt")
                    else log_err "Unknown plugin: $opt"; exit 1; fi
                fi
                ;;
            *) log_err "Unknown argument: $1"; exit 1 ;;
        esac
        shift
    done

    # 2. Interactive TUI (if no args)
    if (( cli_mode == 0 )); then
        {
            printf '\n%s======================================================%s\n' "$C_CYAN" "$C_RESET"
            printf '%s      ELITE HYPRLAND PLUGIN MANAGER (v2026.03)      %s\n' "$C_CYAN" "$C_RESET"
            printf '%s======================================================%s\n\n' "$C_CYAN" "$C_RESET"
            
            local i=1
            for id in "${PLUGIN_IDS[@]}"; do
                printf '  %s[%d]%s Install   %s[u%d]%s Uninstall : %s\n' \
                    "$C_GREEN" "$i" "$C_RESET" "$C_YELLOW" "$i" "$C_RESET" "${PLUGIN_NAME[$id]}"
                ((i++))
            done
            printf '\n  %s[A]%s Install All    %s[UA]%s Uninstall All\n' "$C_GREEN" "$C_RESET" "$C_YELLOW" "$C_RESET"
            printf '  %s[Q]%s Quit\n\n' "$C_CYAN" "$C_RESET"
            printf 'Select an option: '
        } >/dev/tty

        read -r choice < /dev/tty
        choice="${choice,,}" # Pre-process input reliably for case-sensitive strip operations
        
        case "$choice" in
            a|all) q_installs=("${PLUGIN_IDS[@]}") ;;
            ua|uninstall-all) q_uninstalls=("${PLUGIN_IDS[@]}") ;;
            q|quit) exit 0 ;;
            u*)
                local num="${choice#u}"
                if [[ "$num" =~ ^[0-9]+$ ]] && (( 10#$num > 0 && 10#$num <= ${#PLUGIN_IDS[@]} )); then
                    q_uninstalls+=("${PLUGIN_IDS[$(( 10#$num - 1 ))]}")
                else log_err "Invalid selection."; exit 1; fi
                ;;
            *)
                if [[ "$choice" =~ ^[0-9]+$ ]] && (( 10#$choice > 0 && 10#$choice <= ${#PLUGIN_IDS[@]} )); then
                    q_installs+=("${PLUGIN_IDS[$(( 10#$choice - 1 ))]}")
                else log_err "Invalid selection."; exit 1; fi
                ;;
        esac
    fi

    # 3. Execution Pipeline
    if (( ${#q_installs[@]} > 0 || ${#q_uninstalls[@]} > 0 )); then
        authenticate_sudo
    fi

    for pid in "${q_uninstalls[@]}"; do
        if ! uninstall_plugin "$pid"; then ((++failed)); fi
    done

    if (( ${#q_installs[@]} > 0 )); then
        update_headers
        for pid in "${q_installs[@]}"; do
            if ! install_plugin "$pid"; then ((++failed)); fi
        done
    fi

    if (( ${#q_installs[@]} > 0 || ${#q_uninstalls[@]} > 0 )); then
        printf '\n'
        log_info "Reloading Hyprland IPC..."
        hyprctl reload >/dev/null || true

        if (( failed > 0 )); then log_warn "Sequence completed with $failed failure(s)."
        else log_success "Sequence Complete."
        fi
    fi
}

main "$@"
