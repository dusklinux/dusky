#!/usr/bin/env bash
# ==============================================================================
# Dusky Power Management Orchestrator (Elite Edition)
# A monolithic, intelligent power state manager for Arch/Hyprland ecosystems.
# Optimized for zero-fork I/O, strict UWSM session isolation, Async DDC/CI,
# highly resilient process termination, and 100% modular configuration.
# ==============================================================================

set -euo pipefail

# --- BASH 5.0+ CHECK ---
if ((BASH_VERSINFO[0] < 5)); then
    printf '\033[1;31m[ERROR]\033[0m Bash 5.0+ is required for optimal performance.\n' >&2
    exit 1
fi

# --- CONSTANTS & PATHS ---
readonly STATE_DIR="${HOME}/.config/dusky/settings/power_saver"
readonly GUI_STATE_FILE="${HOME}/.config/dusky/settings/power_saver_state"

# Hardware Limits
readonly BRIGHTNESS_PS_LEVEL="1%"
readonly VOLUME_CAP=50

# Integrations & Peripherals
readonly THEME_SCRIPT="${HOME}/user_scripts/theme_matugen/theme_ctl.sh"
readonly VISUALS_SCRIPT="${HOME}/user_scripts/hypr/hypr_blur_opacity_shadow_toggle.sh"
readonly ANIM_SCRIPT="${HOME}/user_scripts/rofi/hypr_anim.sh"
readonly DDC_VCP_BRIGHTNESS_CODE="10"
readonly WP_AUDIO_SINK="@DEFAULT_AUDIO_SINK@"

# Target resources to manage during power transitions
# Note: Process names must not exceed 15 characters due to kernel TASK_COMM_LEN limits.
readonly -a TARGET_PROCESSES=("btop" "nvtop" "hyprsunset" "awww-daemon" "waybar" "blueman-manager")

# Target scripts (Safely matched via args parsing to prevent killing text editors)
readonly -a TARGET_SCRIPTS=("dusky_main.py" "dusky_stt_main.py") 

readonly -a TARGET_SYSTEM_SERVICES=("firewalld" "vsftpd" "waydroid-container" "logrotate.timer" "sshd" "ufw")

# Note: 'hypridle' explicitly removed to preserve Wayland DPMS idle power-saving management.
readonly -a TARGET_USER_SERVICES=("battery_notify" "update_checker.timer" "osd_lock" "blueman-applet" "gvfs-daemon" "gvfs-metadata" "network_meter" "dusky_quickpanal" "dusky")

# ==============================================================================
#  USER CONFIGURATION AREA — Custom Workflows
# ==============================================================================
# Add custom commands to execute during the power transition lifecycles.
# Commands are evaluated safely in an isolated subshell to prevent crashes.
# *ENABLE_COMMANDS is for when enabling power saver
# *DISABLE_COMMANDS is for when disableing power saver

readonly -a PRE_ENABLE_COMMANDS=(
    # e.g., "killall some_custom_app"
    "command -v warp-cli &>/dev/null && warp-cli disconnect &>/dev/null || true"
)

readonly -a POST_ENABLE_COMMANDS=(
    # e.g., "notify-send 'Power Saver Mode Enabled'"
    "sudo systemctl stop warp-svc.service &>/dev/null || true"
)

readonly -a PRE_DISABLE_COMMANDS=(
    # e.g., "warp-cli connect"
)

readonly -a POST_DISABLE_COMMANDS=(
    # e.g., "notify-send 'Performance Mode Restored'"

)
# ==============================================================================

# --- INITIALIZATION ---
mkdir -p "${STATE_DIR}"
mkdir -p "$(dirname "${GUI_STATE_FILE}")"

# --- UTILITY: LOGGING ---
log_info()  { printf '\033[1;34m::\033[0m %s\n' "$*"; }
log_step()  { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
log_warn()  { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }
log_error() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; }

has_cmd() { command -v "$1" &>/dev/null; }

# --- UTILITY: STATE IO ---
save_state() {
    local key="$1"
    local value="$2"
    printf "%s" "$value" > "${STATE_DIR}/${key}.state"
}

get_state() {
    local key="$1"
    local fallback="${2:-}"
    local state_file="${STATE_DIR}/${key}.state"
    if [[ -f "${state_file}" ]]; then
        # Use Bash built-in redirection, bypassing external binary execution
        printf "%s" "$(<"${state_file}")"
    else
        printf "%s" "$fallback"
    fi
}

clear_state() {
    local key="$1"
    rm -f "${STATE_DIR}/${key}.state"
}

# --- UTILITY: ROOT ESCALATION ---
ensure_root() {
    if ! sudo -n true 2>/dev/null; then
        log_info "Privilege escalation required for hardware/service modules."
        sudo -v || { log_error "Sudo authentication failed."; exit 1; }
    fi
}

# --- UTILITY: CUSTOM COMMAND EXECUTION ---
# Safely executes arbitrary string commands from the user arrays.
# Borrows logic from Dusky Fleet Patcher for high resilience.
execute_custom_commands() {
    local -n cmd_array=$1
    if ((${#cmd_array[@]} == 0)); then return 0; fi

    local cmd result
    for cmd in "${cmd_array[@]}"; do
        # Skip empty strings or comment-only strings
        [[ "$cmd" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${cmd//[[:space:]]/}" ]] && continue

        log_info "Executing hook: $cmd"
        result=0
        # Safe execution isolated from the main orchestrator thread
        # Passed via $1 to prevent quote injection vulnerabilities
        bash -c 'set -eo pipefail; eval "$1"' _ "$cmd" || result=$?
        
        if (( result != 0 )); then
            log_warn "Hook command failed (exit $result): $cmd"
        fi
    done
}

# --- UTILITY: PROCESS MANAGEMENT ---

# Safely identifies script PIDs without collateral damage to text editors or pagers
get_script_pids() {
    local target="$1"
    local mypid="$$"
    ps -ww -eo pid=,comm=,args= | awk -v tgt="$target" -v me="$mypid" '
        {
            pid  = $1
            comm = $2
            args = $0
            sub(/^[[:space:]]*[0-9]+[[:space:]]+[^[:space:]]+[[:space:]]+/, "", args)
        }
        pid == me { next }
        comm ~ /^(nano|vim|vi|nvim|emacs|emacsclient|kate|gedit|code|codium|micro|helix|hx|cat|bat|less|more|tail|head|grep|rg|awk|sed|file|stat|ls|find|fzf|tee|xargs)$/ { next }
        {
            n = split(args, parts, /[[:space:]]+/)
            for (i = 1; i <= n; i++) {
                token = parts[i]
                sub(/.*\//, "", token)
                if (token == tgt) { print pid; next }
            }
        }
    '
}

# Unified PID tracking. Solves respawn race conditions using native kill -0 syscalls.
terminate_pids() {
    local -n target_pids=$1
    if ((${#target_pids[@]} == 0)); then return 0; fi

    kill -- "${target_pids[@]}" 2>/dev/null || true

    local i p
    local -a remaining_pids

    for i in {1..20}; do
        remaining_pids=()
        for p in "${target_pids[@]}"; do
            if kill -0 "$p" 2>/dev/null; then
                remaining_pids+=("$p")
            fi
        done

        ((${#remaining_pids[@]} == 0)) && return 0
        sleep 0.1
    done

    # Escalation: Force kill stubborn survivors
    kill -9 -- "${remaining_pids[@]}" 2>/dev/null || true
    sleep 0.3

    for p in "${remaining_pids[@]}"; do
        if kill -0 "$p" 2>/dev/null; then return 1; fi
    done
    return 0
}

# --- MODULE: HARDWARE & PROFILES ---
set_hardware_profiles() {
    local mode="$1"

    if [[ "$mode" == "enable" ]]; then
        log_step "Applying Hardware Power Saving Profiles..."

        # 1. STATE CAPTURE (Must occur before daemons cross-communicate)
        
        # TLP Tracker
        if [[ -x "${HOME}/user_scripts/battery/tlp/tlp_mode_toggle.sh" ]]; then
            if [[ ! -f "${STATE_DIR}/tlp_profile.state" ]]; then
                local tlp_out
                tlp_out=$("${HOME}/user_scripts/battery/tlp/tlp_mode_toggle.sh" status 2>/dev/null || echo "performance")
                save_state "tlp_profile" "$tlp_out"
            fi
        fi

        # Asus Tracker
        if has_cmd asusctl; then
            if [[ ! -f "${STATE_DIR}/asus_profile.state" ]]; then
                local asus_out
                asus_out=$(asusctl profile get 2>/dev/null || true)
                # Zero-fork Bash regex to extract the Active Profile name
                if [[ "$asus_out" =~ Active[[:space:]]profile:[[:space:]]*([A-Za-z]+) ]]; then
                    # Force Title Case via native Bash parameter expansion
                    local matched_prof="${BASH_REMATCH[1]^}"
                    save_state "asus_profile" "$matched_prof"
                fi
            fi
        fi
        
        # 2. APPLY TLP
        if [[ -x "${HOME}/user_scripts/battery/tlp/tlp_mode_toggle.sh" ]]; then
            log_info "Setting TLP to power-saver..."
            "${HOME}/user_scripts/battery/tlp/tlp_mode_toggle.sh" power-saver || true
        elif has_cmd tlp; then
            sudo tlp bat || true
            printf "power-saver" > "${HOME}/.config/dusky/settings/tlp_state"
        fi

        # 3. APPLY ASUSCTL
        if has_cmd asusctl; then
            log_info "Setting asusctl profile to Quiet..."
            asusctl profile set Quiet || true
        fi

        # 4. INTERNAL DISPLAY BRIGHTNESS
        if has_cmd brightnessctl; then
            # Protect against state clobbering on sequential --enable runs
            if [[ ! -f "${STATE_DIR}/brightness.state" ]]; then
                local current_bright
                current_bright=$(brightnessctl get 2>/dev/null || echo "100")
                save_state "brightness" "$current_bright"
            fi
            brightnessctl set "${BRIGHTNESS_PS_LEVEL}" -q
            log_info "Internal brightness locked to ${BRIGHTNESS_PS_LEVEL}."
        fi

        # 5. KEYBOARD BACKLIGHT
        if has_cmd brightnessctl && brightnessctl -d '*kbd_backlight*' info &>/dev/null; then
            if [[ ! -f "${STATE_DIR}/kbd_brightness.state" ]]; then
                local current_kbd
                current_kbd=$(brightnessctl -d '*kbd_backlight*' get 2>/dev/null || echo "0")
                save_state "kbd_brightness" "$current_kbd"
            fi
            brightnessctl -d '*kbd_backlight*' set 0 -q 2>/dev/null || true
            log_info "Keyboard backlight disabled."
        fi

        # 6. EXTERNAL DISPLAY BRIGHTNESS (Async DDC/CI)
        if has_cmd ddcutil; then
            log_info "Dispatching external monitor brightness lock (async)..."
            # Prevent race conditions on double-clicks by claiming the state file in the main thread instantly
            if [[ ! -f "${STATE_DIR}/ddc_brightness.state" ]]; then
                printf "FETCHING" > "${STATE_DIR}/ddc_brightness.state"
                
                nohup bash -c '
                    state_file="$1"
                    vcp_code="$2"
                    level="$3"
                    
                    ddc_out=$(ddcutil getvcp "$vcp_code" --terse 2>/dev/null || true)
                    if [[ "$ddc_out" =~ VCP[[:space:]]+${vcp_code}[[:space:]]+C[[:space:]]+([0-9]+) ]]; then
                        printf "%s" "${BASH_REMATCH[1]}" > "$state_file"
                    else
                        # Remove lock if fetch failed so it can be retried safely later
                        rm -f "$state_file"
                    fi
                    
                    ddcutil setvcp "$vcp_code" "$level" >/dev/null 2>&1 || true
                ' _ "${STATE_DIR}/ddc_brightness.state" "${DDC_VCP_BRIGHTNESS_CODE}" "${BRIGHTNESS_PS_LEVEL%\%}" </dev/null >/dev/null 2>&1 &
            else
                # State is already saved (or being fetched), just enforce the brightness limit
                nohup bash -c '
                    vcp_code="$1"
                    level="$2"
                    ddcutil setvcp "$vcp_code" "$level" >/dev/null 2>&1 || true
                ' _ "${DDC_VCP_BRIGHTNESS_CODE}" "${BRIGHTNESS_PS_LEVEL%\%}" </dev/null >/dev/null 2>&1 &
            fi
        fi

        # 7. AUDIO VOLUME CAP
        if has_cmd wpctl; then
            local raw_output
            raw_output=$(wpctl get-volume "${WP_AUDIO_SINK}" 2>/dev/null || true)
            
            # Matches: "Volume: 0.45" or "Volume: 0.45 [MUTED]"
            if [[ "$raw_output" =~ Volume:[[:space:]]*([0-9]+)\.([0-9]+)([[:space:]]*\[MUTED\])? ]]; then
                # Pad fraction to 2 digits, force base-10 arithmetic to avoid octal errors
                local frac="${BASH_REMATCH[2]}00"
                local current_vol=$(( 10#${BASH_REMATCH[1]} * 100 + 10#${frac:0:2} ))
                
                if (( current_vol > VOLUME_CAP )); then
                    save_state "volume" "$current_vol"
                    [[ -n "${BASH_REMATCH[3]:-}" ]] && save_state "volume_muted" "true"
                    
                    wpctl set-volume "${WP_AUDIO_SINK}" "${VOLUME_CAP}%"
                    log_info "Volume capped at ${VOLUME_CAP}%."
                fi
            fi
        fi

    else
        log_step "Restoring Hardware Performance Profiles..."

        # TLP (Stateful Restore + GUI Sync)
        if [[ -x "${HOME}/user_scripts/battery/tlp/tlp_mode_toggle.sh" ]]; then
            local prev_tlp
            prev_tlp=$(get_state "tlp_profile")
            if [[ -n "$prev_tlp" ]]; then
                log_info "Restoring TLP to ${prev_tlp}..."
                "${HOME}/user_scripts/battery/tlp/tlp_mode_toggle.sh" "$prev_tlp" || true
                clear_state "tlp_profile"
            else
                log_info "Setting TLP to performance..."
                "${HOME}/user_scripts/battery/tlp/tlp_mode_toggle.sh" performance || true
            fi
        elif has_cmd tlp; then
            sudo tlp ac || true
            printf "performance" > "${HOME}/.config/dusky/settings/tlp_state"
        fi

        # SYNCHRONIZATION BARRIER:
        # Give TLP and asusd 1 full second to finish broadcasting and processing 
        # ACPI/D-Bus power-state events before we explicitly override the asusctl profile.
        sleep 1

        # ASUSCTL (Stateful Restore)
        if has_cmd asusctl; then
            local prev_asus
            prev_asus=$(get_state "asus_profile")
            if [[ -n "$prev_asus" ]]; then
                # Strictly enforce Title Case for asusctl compliance (Quiet, Balanced, Performance)
                prev_asus="${prev_asus^}"
                log_info "Restoring asusctl profile to ${prev_asus}..."
                asusctl profile set "$prev_asus" || true
                clear_state "asus_profile"
            else
                # Safe Fallback if state file was manually deleted
                log_info "Setting asusctl profile to Performance..."
                asusctl profile set Performance || true
            fi
        fi

        # BRIGHTNESS (Internal)
        if has_cmd brightnessctl; then
            local prev_bright
            prev_bright=$(get_state "brightness")
            if [[ -n "$prev_bright" ]]; then
                brightnessctl set "${prev_bright}" -q
                clear_state "brightness"
                log_info "Internal brightness restored."
            fi
        fi

        # KEYBOARD BACKLIGHT
        if has_cmd brightnessctl && brightnessctl -d '*kbd_backlight*' info &>/dev/null; then
            local prev_kbd
            prev_kbd=$(get_state "kbd_brightness")
            if [[ -n "$prev_kbd" ]]; then
                brightnessctl -d '*kbd_backlight*' set "${prev_kbd}" -q 2>/dev/null || true
                clear_state "kbd_brightness"
                log_info "Keyboard backlight restored."
            fi
        fi

        # DDC/CI EXTERNAL BRIGHTNESS (Async)
        if has_cmd ddcutil; then
            log_info "Dispatching external monitor brightness restore (async)..."
            # Detach completely and safely wait if a fetch is currently in progress
            nohup bash -c '
                state_file="$1"
                vcp_code="$2"
                
                if [[ -f "$state_file" ]]; then
                    prev_ddc=$(<"$state_file")
                    
                    if [[ "$prev_ddc" == "FETCHING" ]]; then
                        # Wait up to 3 seconds for the rapid enable-task to finish probing the monitor
                        for _ in {1..30}; do
                            sleep 0.1
                            prev_ddc=$(<"$state_file" 2>/dev/null || echo "")
                            if [[ "$prev_ddc" =~ ^[0-9]+$ ]]; then break; fi
                        done
                    fi

                    if [[ "$prev_ddc" =~ ^[0-9]+$ ]]; then
                        ddcutil setvcp "$vcp_code" "$prev_ddc" >/dev/null 2>&1 || true
                        rm -f "$state_file"
                    fi
                fi
            ' _ "${STATE_DIR}/ddc_brightness.state" "${DDC_VCP_BRIGHTNESS_CODE}" </dev/null >/dev/null 2>&1 &
        fi

        # VOLUME & MUTE STATE
        if has_cmd wpctl; then
            local prev_vol
            prev_vol=$(get_state "volume")
            if [[ -n "$prev_vol" ]]; then
                wpctl set-volume "${WP_AUDIO_SINK}" "${prev_vol}%"
                
                if [[ "$(get_state "volume_muted")" == "true" ]]; then
                    wpctl set-mute "${WP_AUDIO_SINK}" 1
                    clear_state "volume_muted"
                fi
                
                clear_state "volume"
                log_info "Volume and mute state restored."
            fi
        fi
    fi
}

# --- MODULE: NETWORK/RFKILL ---
set_network_radios() {
    local mode="$1"
    local disable_wifi="$2"

    if [[ "$mode" == "enable" ]]; then
        log_step "Managing Radios..."
        if has_cmd rfkill; then
            # Prevent clobbering: Only block and save state if currently unblocked
            if rfkill list bluetooth | grep -q "Soft blocked: no"; then
                save_state "bt_blocked_by_us" "true"
                sudo rfkill block bluetooth
                log_info "Bluetooth disabled."
            fi

            if [[ "$disable_wifi" == "true" ]] && rfkill list wifi | grep -q "Soft blocked: no"; then
                save_state "wifi_blocked_by_us" "true"
                sudo rfkill block wifi
                log_info "Wi-Fi disabled."
            fi
        fi
    else
        log_step "Restoring Radios..."
        if has_cmd rfkill; then
            if [[ "$(get_state "bt_blocked_by_us")" == "true" ]]; then
                sudo rfkill unblock bluetooth
                clear_state "bt_blocked_by_us"
                log_info "Bluetooth restored."
            fi
            if [[ "$(get_state "wifi_blocked_by_us")" == "true" ]]; then
                sudo rfkill unblock wifi
                clear_state "wifi_blocked_by_us"
                log_info "Wi-Fi restored."
            fi
        fi
    fi
}

# --- MODULE: SERVICES & PROCESSES ---
manage_services() {
    local mode="$1"

    if [[ "$mode" == "enable" ]]; then
        log_step "Terminating power-hungry processes & services..."
        
        # Simple processes (Targeted from array with strict exit verification)
        for proc in "${TARGET_PROCESSES[@]}"; do
            local -a pids=()
            mapfile -t pids < <(pgrep -x "$proc" 2>/dev/null || true)
            if ((${#pids[@]} > 0)); then
                # Securely capture the exact NUL-delimited argument array directly from the kernel
                if [[ -r "/proc/${pids[0]}/cmdline" ]]; then
                    cp "/proc/${pids[0]}/cmdline" "${STATE_DIR}/proc_cmd_${proc}.state" 2>/dev/null || true
                fi
                save_state "proc_active_${proc}" "true"
                terminate_pids pids
            fi
        done

        # Background Scripts (Safely parsed from array)
        for script in "${TARGET_SCRIPTS[@]}"; do
            local -a script_pids=()
            mapfile -t script_pids < <(get_script_pids "$script")
            if ((${#script_pids[@]} > 0)); then
                # Securely capture the exact NUL-delimited argument array directly from the kernel
                if [[ -r "/proc/${script_pids[0]}/cmdline" ]]; then
                    cp "/proc/${script_pids[0]}/cmdline" "${STATE_DIR}/script_cmd_${script}.state" 2>/dev/null || true
                fi
                save_state "script_active_${script}" "true"
                terminate_pids script_pids
            fi
        done

        if has_cmd playerctl; then playerctl -a pause || true; fi

        # System Services
        for svc in "${TARGET_SYSTEM_SERVICES[@]}"; do
            if systemctl is-active --quiet "$svc" 2>/dev/null; then
                save_state "sys_svc_${svc}" "active"
                sudo systemctl stop "$svc" || true
            fi
        done

        # User Services (UWSM graphical targets managed here exclusively)
        for svc in "${TARGET_USER_SERVICES[@]}"; do
            if systemctl --user is-active --quiet "$svc" 2>/dev/null; then
                save_state "usr_svc_${svc}" "active"
                systemctl --user stop "$svc" || true
            fi
        done

    else
        log_step "Restoring previously active services & processes..."

        # System Services
        for svc in "${TARGET_SYSTEM_SERVICES[@]}"; do
            if [[ "$(get_state "sys_svc_${svc}")" == "active" ]]; then
                sudo systemctl start "$svc" || true
                clear_state "sys_svc_${svc}"
            fi
        done

        # User Services
        for svc in "${TARGET_USER_SERVICES[@]}"; do
            if [[ "$(get_state "usr_svc_${svc}")" == "active" ]]; then
                systemctl --user start "$svc" || true
                clear_state "usr_svc_${svc}"
            fi
        done
        
        # Simple processes (Safely restored using exact NUL-delimited kernel arguments)
        for proc in "${TARGET_PROCESSES[@]}"; do
            if [[ "$(get_state "proc_active_${proc}")" == "true" ]]; then
                local cmd_state_file="${STATE_DIR}/proc_cmd_${proc}.state"
                
                if [[ -f "$cmd_state_file" ]]; then
                    local -a cmd_array=()
                    while IFS= read -r -d $'\0' arg; do
                        cmd_array+=("$arg")
                    done < "$cmd_state_file"
                    
                    if ((${#cmd_array[@]} > 0)); then
                        nohup dusky-run -- "${cmd_array[@]}" </dev/null >/dev/null 2>&1 &
                    else
                        # Fallback if array was empty
                        nohup dusky-run -- "$proc" </dev/null >/dev/null 2>&1 &
                    fi
                    rm -f "$cmd_state_file"
                else
                    # Fallback if no command context was caught
                    nohup dusky-run -- "$proc" </dev/null >/dev/null 2>&1 &
                fi
                disown "$!" 2>/dev/null || true
                clear_state "proc_active_${proc}"
            fi
        done
        
        # Background Scripts (Safely restored using exact NUL-delimited kernel arguments)
        for script in "${TARGET_SCRIPTS[@]}"; do
            if [[ "$(get_state "script_active_${script}")" == "true" ]]; then
                local cmd_state_file="${STATE_DIR}/script_cmd_${script}.state"
                
                if [[ -f "$cmd_state_file" ]]; then
                    local -a cmd_array=()
                    while IFS= read -r -d $'\0' arg; do
                        cmd_array+=("$arg")
                    done < "$cmd_state_file"
                    
                    if ((${#cmd_array[@]} > 0)); then
                        nohup dusky-run -- "${cmd_array[@]}" </dev/null >/dev/null 2>&1 &
                    else
                        nohup dusky-run -- "$script" </dev/null >/dev/null 2>&1 &
                    fi
                    rm -f "$cmd_state_file"
                else
                    nohup dusky-run -- "$script" </dev/null >/dev/null 2>&1 &
                fi
                disown "$!" 2>/dev/null || true
                clear_state "script_active_${script}"
            fi
        done
    fi
}

# --- MODULE: HYPRLAND ANIMATIONS ---
manage_animations() {
    local mode="$1"
    
    log_step "Configuring Hyprland visual states..."
    
    # 1. UI Theme/Blur External Script Integration
    if [[ -x "${VISUALS_SCRIPT}" ]]; then
        if [[ "$mode" == "enable" ]]; then
            # Protect state so sequential executions don't lock visuals off
            if [[ ! -f "${STATE_DIR}/visuals.state" ]]; then
                local current_visuals="False"
                # Check the exact file where the toggle script saves its state
                if [[ -f "${HOME}/.config/dusky/settings/opacity_blur" ]]; then
                    current_visuals=$(<"${HOME}/.config/dusky/settings/opacity_blur")
                fi
                save_state "visuals" "$current_visuals"
            fi
            # Execute the toggle script to disable blur/shadows and edit config files (severing stdin)
            "${VISUALS_SCRIPT}" off </dev/null >/dev/null 2>&1 || true
            
        elif [[ "$mode" == "disable" ]]; then
            local prev_visuals
            prev_visuals=$(get_state "visuals")
            # Only restore visuals if they were actually ON before power saver started
            if [[ "$prev_visuals" == "True" ]]; then
                "${VISUALS_SCRIPT}" on </dev/null >/dev/null 2>&1 || true
            fi
            clear_state "visuals"
        fi
    else
        log_warn "Visuals script not found or not executable at: ${VISUALS_SCRIPT}"
    fi

    # 2. Hyprshade
    if has_cmd hyprshade; then
        if [[ "$mode" == "enable" ]]; then
            local current_shader
            current_shader=$(dusky-run -- hyprshade current 2>/dev/null || true)
            if [[ -n "$current_shader" ]]; then
                save_state "active_hyprshade" "$current_shader"
                dusky-run -- hyprshade off || true
            fi
        elif [[ "$mode" == "disable" ]]; then
            local prev_shader
            prev_shader=$(get_state "active_hyprshade")
            if [[ -n "$prev_shader" ]]; then
                dusky-run -- hyprshade on "$prev_shader" || true
                clear_state "active_hyprshade"
            fi
        fi
    fi

    # 3. Core Animations (IPC / Rofi Delegation)
    if [[ "$mode" == "enable" ]]; then
        if has_cmd hyprctl; then
            dusky-run -- hyprctl keyword animations:enabled 0 </dev/null >/dev/null 2>&1 || log_warn "IPC signal dropped: animations"
        fi
    elif [[ "$mode" == "disable" ]]; then
        if [[ -x "${ANIM_SCRIPT}" ]]; then
            dusky-run -- "${ANIM_SCRIPT}" --current </dev/null >/dev/null 2>&1 || true
        elif has_cmd hyprctl; then
            dusky-run -- hyprctl keyword animations:enabled 1 </dev/null >/dev/null 2>&1 || log_warn "IPC signal dropped: animations"
        fi
    fi
}

# --- ORCHESTRATORS ---
enable_power_saver() {
    local theme="$1"
    local wifi="$2"

    ensure_root
    save_state "power_saver_active" "true"

    execute_custom_commands PRE_ENABLE_COMMANDS

    manage_animations "enable"
    manage_services "enable"
    set_hardware_profiles "enable"
    set_network_radios "enable" "$wifi"

    if [[ "$theme" == "true" ]]; then
        log_info "Applying Light Theme for backlight optimization..."
        dusky-run -- "${THEME_SCRIPT}" set --mode light </dev/null >/dev/null 2>&1 || true
    fi

    # Update global GUI state
    printf "true" > "${GUI_STATE_FILE}"
    
    execute_custom_commands POST_ENABLE_COMMANDS
    
    log_step "POWER SAVING ENABLED. System optimized."
}

disable_power_saver() {
    local theme="$1"

    if [[ "$(get_state "power_saver_active")" != "true" ]]; then
        log_warn "Power saver does not appear to be active. Proceeding with restore anyway."
    fi

    ensure_root

    execute_custom_commands PRE_DISABLE_COMMANDS

    manage_animations "disable"
    set_hardware_profiles "disable"
    set_network_radios "disable" "false"
    manage_services "disable"

    if [[ "$theme" == "true" ]]; then
        log_info "Restoring Dark Theme..."
        dusky-run -- "${THEME_SCRIPT}" set --mode dark </dev/null >/dev/null 2>&1 || true
    fi

    clear_state "power_saver_active"
    
    # Update global GUI state
    printf "false" > "${GUI_STATE_FILE}"
    
    execute_custom_commands POST_DISABLE_COMMANDS
    
    log_step "PERFORMANCE MODE RESTORED. Constraints lifted."
}

# --- CLI PARSER ---
main() {
    local mode=""
    local do_theme="false"
    local do_wifi="false"
    local interactive="false"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -e|--enable) mode="enable" ;;
            -d|--disable) mode="disable" ;;
            -t|--theme) do_theme="true" ;;
            -w|--wifi) do_wifi="true" ;;
            -i|--interactive) interactive="true" ;;
            -h|--help)
                echo "Usage: $0 [OPTIONS]"
                echo "  -e, --enable       Turn ON power saving mode."
                echo "  -d, --disable      Turn OFF power saving mode (Restore)."
                echo "  -t, --theme        Toggle theme (Light on Enable / Dark on Disable)."
                echo "  -w, --wifi         Block Wi-Fi when enabling power saver."
                echo "  -i, --interactive  Launch interactive TUI prompts."
                exit 0
                ;;
            *) log_error "Unknown flag: $1"; exit 1 ;;
        esac
        shift
    done

    # Interactive Mode (Gum)
    if [[ "$interactive" == "true" ]]; then
        if ! has_cmd gum; then
            log_error "Interactive mode requires 'gum'. Run with raw flags instead."
            exit 1
        fi
        clear
        gum style --border double --margin "1" --padding "1 2" --border-foreground 212 --foreground 212 "SYSTEM POWER MANAGER"
        
        # Prevent set -e from killing the script on user cancellation (Exit Code 130)
        mode=$(gum choose "Enable Power Saver" "Restore Performance" || true)
        
        if [[ "$mode" == "Enable Power Saver" ]]; then
            mode="enable"
            gum confirm "Switch to Light Mode? (Lower backlight)" && do_theme="true" || true
            gum confirm "Turn off Wi-Fi?" && do_wifi="true" || true
        elif [[ "$mode" == "Restore Performance" ]]; then
            mode="disable"
            gum confirm "Restore Dark Mode?" && do_theme="true" || true
        else
            # Graceful fallback if prompt was canceled (ESC/Ctrl+C)
            mode=""
        fi
    fi

    if [[ -z "$mode" ]]; then
        log_error "No action specified. Use --enable, --disable, or --interactive."
        exit 1
    fi

    if [[ "$mode" == "enable" ]]; then
        enable_power_saver "$do_theme" "$do_wifi"
    elif [[ "$mode" == "disable" ]]; then
        disable_power_saver "$do_theme"
    fi
}

main "$@"
