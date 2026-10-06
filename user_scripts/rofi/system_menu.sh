#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# DUSKY ROFI SYSTEM MENU
# Target: rofi 2.x (Wayland) + Hyprland + Arch Linux
# Dependencies: rofi-wayland, kitty, dusky-run, fd, file, xdg-utils, python3
#
# Conventions:
#   - All dmenu calls go through menu_select() using -theme-str overrides
#     (the canonical rofi 2.x theming method) instead of legacy CLI flags.
#     Global behaviour (fuzzy matching, fzf sorting, single-click accept)
#     is inherited from ~/.config/rofi/config.rasi and is not re-specified.
#   - Common flags: -dmenu -i -no-custom -sync -p <prompt>
#     -window-title <title> -mesg <hint>.
#   - Every launch is wrapped in dusky-run (systemd scope + OOM policy).
#   - Terminal holds mirror the Dusky Control Center: interactive TUIs run
#     without --hold only where upstream runs without it; one-shot
#     reporters/installers always use --hold so output stays readable.
#   - Config files are opened with dusky_text_editor.py so the user's
#     configured editor from default_apps.lua is respected, with a
#     $EDITOR-in-kitty fallback.
#   - Labels use Nerd Font glyphs only. No emojis.
# -----------------------------------------------------------------------------
set -uo pipefail

declare -gr SCRIPTS_DIR="${HOME}/user_scripts"
declare -gr CC_DIR="${SCRIPTS_DIR}/dusky_system/control_center"
declare -gr TEXT_EDITOR_BIN="${CC_DIR}/dusky_text_editor.py"
declare -gr HYPR_EDIT_DIR="${HOME}/.config/hypr/edit_here"
declare -gr SEARCH_DIR="${HOME}/Documents/pensive/linux"

declare -gr TERMINAL="kitty"

declare -ga EDITOR_CMD=()
read -r -a EDITOR_CMD <<< "${EDITOR:-nvim}"
readonly -a EDITOR_CMD

# Rofi 2.x: size overrides via -theme-str (merged into the active theme).
declare -gr ROFI_THEME_MAIN='window {width: 28%;} listview {lines: 14;}'
declare -gr ROFI_THEME_SEARCH='window {width: 80%;} listview {lines: 12;}'
declare -agr ROFI_BASE_CMD=(rofi -dmenu -i -no-custom -sync)
declare -gr MAIN_HINT="Type to filter - Enter to open - Esc to quit"
declare -gr SUB_HINT="Type to filter - Enter to run - Esc to go back"

declare -agr MAIN_MENU=(
    '  Search Notes'
    '  Apps'
    '  Display & Theme'
    '  Audio & Media'
    '  Network & Remote'
    '  System & Storage'
    '  Performance & Memory'
    '  Power & Devices'
    '  Configs & Services'
    '  Tools & AI'
    '  Help & Keys'
    '  Session Power'
)

declare -agr DISPLAY_MENU=(
    '  Next Theme'
    '  Theme Presets'
    '  Theme Settings'
    '  Dark Mode'
    '  Light Mode'
    '  Fix KDE Theming'
    '  Wallpaper App'
    '  Rofi Wallpaper'
    '  Appearance'
    '  Animations'
    '  Shaders'
    '  Blur Toggle'
    '  Night Light'
    '  Waybar Select'
    '  Monitor Wizard'
    '  Scale Up'
    '  Scale Down'
    '  Rotate CW'
    '  Rotate CCW'
)

declare -agr AUDIO_MENU=(
    '  Audio Studio'
    '  Audio Toggle'
    '  Audio Mixer'
    '  Output Toggle'
    '  Input Toggle'
    '  Mono Audio'
    '  Shazam Song'
    '  Screen Recorder'
    '  GIF Converter'
    '  Wayclick Toggle'
    '  Wayclick Setup'
)

declare -agr NETWORK_MENU=(
    '  Network Manager'
    '  DNS Config'
    '  Warp Toggle'
    '  Tailscale Setup'
    '  OpenSSH Setup'
    '  SSHFS Mounter'
    '  VNC Remote'
    '  WireGuard New'
    '  WireGuard Status'
    '  WireGuard Setup'
    '  ARP Scan'
    '  WiFi Audit'
    '  Block Distractions'
    '  FTP Setup'
)

declare -agr SYSTEM_MENU=(
    '  Dusky Updater'
    '  Arch Updater'
    '  Fastfetch'
    '  Dysk Usage'
    '  Disk I/O Monitor'
    '  BTRFS Stats'
    '  Drive Health'
    '  Disk Formatter'
    '  NTFS Fix'
    '  Snapshots'
    '  View Backups'
    '  Send Logs'
    '  Cache Purge'
)

declare -agr PERFORMANCE_MENU=(
    '  System Monitor'
    '  Benchmark'
    '  Process Terminator'
    '  Screentime Stats'
    '  Autostart Manager'
    '  ZRAM Status'
    '  Memory Sweep'
    '  ZRAM Diagnostics'
    '  Kernel Compiler'
    '  Boot Manager'
)

declare -agr POWER_MENU=(
    '  Profile Balanced'
    '  Profile Performance'
    '  Profile Power Saver'
    '  Saver On'
    '  Saver Off'
    '  Power Manager'
    '  Battery Config'
    '  Idle Settings'
    '  Lock Screen Select'
    '  Idle Toggle'
    '  Trackpad Config'
    '  Mouse Left-Handed'
    '  Mouse Right-Handed'
    '  Bluetooth Manager'
)

declare -agr CONFIGS_MENU=(
    '  Control Center'
    '  Hypr Reload'
    '  Main Config'
    '  Keybinds'
    '  Input'
    '  Monitors'
    '  Window Rules'
    '  Autostart'
    '  File Manager Default'
    '  Browser Default'
    '  Editor Default'
    '  Terminal Default'
    '  Window Rules Generator'
    '  Service Manager'
    '  Font Manager'
    '  Prompt Style'
    '  Notifications Config'
    '  GTK Settings'
)

declare -agr TOOLS_MENU=(
    '  Voice Dictation'
    '  Voice Install'
    '  OCR Selection'
    '  LLM Panel'
    '  Lens Search'
    '  TTS Install'
    '  TTS Engine'
    '  Emoji Picker'
    '  Screenshot'
    '  Calculator'
    '  Clipboard History'
    '  Firefox Tune'
    '  Git Config'
    '  Git New Repo'
    '  Git Relink Repo'
    '  Git Time Machine'
    '  Neovim Manager'
)

declare -agr HELP_MENU=(
    '  Keybindings List'
    '  Keybind Cheatsheet'
    '  Edit Keybinds'
    '  Arch Wiki'
    '  Hyprland Wiki'
)

error_dialog() {
    local message="$1"
    rofi -e "$message" >/dev/null 2>&1 || printf '%s\n' "$message" >&2
}

require_commands() {
    local -a missing=()
    local cmd

    for cmd in "$@"; do
        command -v -- "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done

    if ((${#missing[@]})); then
        error_dialog "Missing command(s): ${missing[*]}"
        return 1
    fi
}

require_executable_file() {
    local path="$1"

    if [[ ! -x "$path" ]]; then
        error_dialog "Not executable: $path"
        return 1
    fi
}

validate_launch_target() {
    local target="$1"

    if [[ "$target" == */* ]]; then
        require_executable_file "$target"
    else
        command -v -- "$target" >/dev/null 2>&1 || {
            error_dialog "Command not found: $target"
            return 1
        }
    fi
}

spawn() {
    "$@" </dev/null >/dev/null 2>&1 &
    disown "$!" 2>/dev/null || true
}

menu_select() {
    local prompt="$1"
    local array_name="$2"
    local preselect="${3-}"
    local hint="${4-}"
    local -n options="$array_name"
    local -a cmd=("${ROFI_BASE_CMD[@]}" -theme-str "$ROFI_THEME_MAIN" -p "$prompt" -window-title "Dusky - $prompt")

    if [[ -n "$hint" ]]; then
        cmd+=(-mesg "$hint")
    fi

    if ((${#options[@]} == 0)); then
        error_dialog "No entries available for $prompt."
        return 1
    fi

    if [[ -n "$preselect" ]]; then
        local i
        for i in "${!options[@]}"; do
            if [[ "${options[i]}" == "$preselect" ]]; then
                cmd+=(-selected-row "$i")
                break
            fi
        done
    fi

    printf '%s\n' "${options[@]}" | "${cmd[@]}"
}

path_is_within() {
    local base="$1"
    local target="$2"

    [[ "$target" == "$base" || "$target" == "$base/"* ]]
}

run_app() {
    validate_launch_target "$1" || return 0
    spawn dusky-run -- "$@"
    exit 0
}

run_term() {
    local class="$1"
    shift

    validate_launch_target "$1" || return 0
    spawn dusky-run -- "$TERMINAL" --class "$class" --title "$class" -e "$@"
    exit 0
}

run_term_hold() {
    local class="$1"
    shift

    validate_launch_target "$1" || return 0
    spawn dusky-run -- "$TERMINAL" --hold --class "$class" --title "$class" -e "$@"
    exit 0
}

run_rofi_mode() {
    local mode="$1"
    local script="$2"
    shift 2

    require_executable_file "$script" || return 0
    run_app rofi -show "$mode" -modi "$mode:$script" "$@"
}

open_editor() {
    local file="$1"

    validate_launch_target "${EDITOR_CMD[0]}" || return 0
    spawn dusky-run -- "$TERMINAL" --class "nvim_config" --title "nvim_config" -e "${EDITOR_CMD[@]}" "$file"
    exit 0
}

edit_file() {
    local file="$1"

    if [[ -x "$TEXT_EDITOR_BIN" ]]; then
        spawn dusky-run -- "$TEXT_EDITOR_BIN" "$file"
        exit 0
    fi
    open_editor "$file"
}

perform_global_search() {
    local search_root
    search_root=$(realpath -e -- "$SEARCH_DIR") || {
        error_dialog "Search directory not found: $SEARCH_DIR"
        return 0
    }

    local search_output
    search_output=$(cd -- "$search_root" && fd --type f --hidden --exclude .git .) || {
        error_dialog "Failed to read search directory: $search_root"
        return 0
    }

    if [[ -z "$search_output" ]]; then
        error_dialog "No files found in $search_root."
        return 0
    fi

    local -a results=()
    mapfile -t results <<< "$search_output"

    local selected_relative
    selected_relative=$(printf '%s\n' "${results[@]}" | "${ROFI_BASE_CMD[@]}" -theme-str "$ROFI_THEME_SEARCH" -p "Search" -window-title "Dusky - Search") || return 0
    [[ -n "$selected_relative" ]] || return 0

    local resolved_path
    resolved_path=$(realpath -e -- "${search_root}/${selected_relative}") || {
        error_dialog "Selected file no longer exists."
        return 0
    }

    if ! path_is_within "$search_root" "$resolved_path"; then
        error_dialog "Refusing to open a path outside $search_root."
        return 0
    fi

    local mime_type
    mime_type=$(file --mime-type -b -- "$resolved_path") || {
        error_dialog "Failed to detect file type."
        return 0
    }

    case "$mime_type" in
        text/*|inode/x-empty|application/json|application/*xml|application/toml|application/x-toml|application/yaml|application/x-yaml|application/x-shellscript|application/x-conf|application/x-config)
            edit_file "$resolved_path"
            ;;
        *)
            run_app xdg-open "$resolved_path"
            ;;
    esac
}

show_display_menu() {
    local choice

    while :; do
        choice=$(menu_select "Display" DISPLAY_MENU "" "$SUB_HINT") || return 0

        case "$choice" in
            '  Next Theme')
                run_app "$SCRIPTS_DIR/theme_matugen/theme_ctl.sh" next
                ;;
            '  Theme Presets')
                run_term "dusky_matugen_presets.sh" "$SCRIPTS_DIR/theme_matugen/dusky_matugen_presets.sh"
                ;;
            '  Theme Settings')
                run_app "$SCRIPTS_DIR/rofi/rofi_theme.sh"
                ;;
            '  Dark Mode')
                run_app "$SCRIPTS_DIR/theme_matugen/theme_ctl.sh" set --mode dark
                ;;
            '  Light Mode')
                run_app "$SCRIPTS_DIR/theme_matugen/theme_ctl.sh" set --mode light
                ;;
            '  Fix KDE Theming')
                run_term_hold "kde_theming_fix" bash -c "python3 '$SCRIPTS_DIR/arch_setup_scripts/scripts/331_sync_kde_apps.py' && notify-send 'KDE Theming' 'KDE applications successfully themed!'"
                ;;
            '  Wallpaper App')
                run_app waypaper
                ;;
            '  Rofi Wallpaper')
                run_app "$SCRIPTS_DIR/rofi/rofi_wallpaper_selctor.sh"
                ;;
            '  Appearance')
                run_term_hold "dusky_tui" python3 "$SCRIPTS_DIR/dusky_tui/python/main/main.py" "$SCRIPTS_DIR/hypr/visual/tui_appearance.py"
                ;;
            '  Animations')
                run_rofi_mode "animations" "$SCRIPTS_DIR/rofi/hypr_anim.sh"
                ;;
            '  Shaders')
                run_app "$SCRIPTS_DIR/rofi/shader_menu.sh"
                ;;
            '  Blur Toggle')
                run_app "$SCRIPTS_DIR/hypr/hypr_blur_opacity_shadow_toggle.sh"
                ;;
            '  Night Light')
                run_app "$SCRIPTS_DIR/dusky_system/quickpanal/dusky_quickpanal.py"
                ;;
            '  Waybar Select')
                run_term "waybar_tui" python3 "$SCRIPTS_DIR/waybar/tui_waybars.py"
                ;;
            '  Monitor Wizard')
                run_term_hold "dusky_tui" python3 "$SCRIPTS_DIR/hypr/monitor/tui_monitor_wizard.py"
                ;;
            '  Scale Up')
                run_app python3 "$SCRIPTS_DIR/hypr/monitor/adjust_scale.py" +
                ;;
            '  Scale Down')
                run_app python3 "$SCRIPTS_DIR/hypr/monitor/adjust_scale.py" -
                ;;
            '  Rotate CW')
                run_app python3 "$SCRIPTS_DIR/hypr/monitor/screen_rotate.py" -90
                ;;
            '  Rotate CCW')
                run_app python3 "$SCRIPTS_DIR/hypr/monitor/screen_rotate.py" +90
                ;;
            *)
                return 0
                ;;
        esac
    done
}

show_audio_menu() {
    local choice

    while :; do
        choice=$(menu_select "Audio" AUDIO_MENU "" "$SUB_HINT") || return 0

        case "$choice" in
            '  Audio Studio')
                run_app python3 "$SCRIPTS_DIR/audio/dusky_audio_studio/dusky_audio_studio.py" --gui
                ;;
            '  Audio Toggle')
                run_app python3 "$SCRIPTS_DIR/audio/dusky_audio_studio/dusky_audio_studio.py" --toggle
                ;;
            '  Audio Mixer')
                run_app pavucontrol
                ;;
            '  Output Toggle')
                run_app "$SCRIPTS_DIR/audio/dusky_in_out_source.sh" --output
                ;;
            '  Input Toggle')
                run_app "$SCRIPTS_DIR/audio/dusky_in_out_source.sh" --input
                ;;
            '  Mono Audio')
                run_app python3 "$SCRIPTS_DIR/audio/mono_audio_pipewire.py"
                ;;
            '  Shazam Song')
                run_app python3 "$SCRIPTS_DIR/music/music_recognition.py"
                ;;
            '  Screen Recorder')
                run_term_hold "dusky_tui" python3 "$SCRIPTS_DIR/dusky_recorder/tui_dusky_recorder.py"
                ;;
            '  GIF Converter')
                run_term_hold "hypergif" "$SCRIPTS_DIR/media_converter/video_to_gif_converter.sh"
                ;;
            '  Wayclick Toggle')
                run_app "$SCRIPTS_DIR/wayclick/dusky_wayclick.sh"
                ;;
            '  Wayclick Setup')
                run_term_hold "dusky_tui_wayclick.sh" "$SCRIPTS_DIR/wayclick/dusky_tui_wayclick.sh"
                ;;
            *)
                return 0
                ;;
        esac
    done
}

show_network_menu() {
    local choice

    while :; do
        choice=$(menu_select "Network" NETWORK_MENU "" "$SUB_HINT") || return 0

        case "$choice" in
            '  Network Manager')
                run_term_hold "dusky_tui" python3 "$SCRIPTS_DIR/network_manager/tui_dusky_network.py"
                ;;
            '  DNS Config')
                run_term_hold "dusky_tui" python3 "$SCRIPTS_DIR/network_manager/tui_dns.py"
                ;;
            '  Warp Toggle')
                run_app "$SCRIPTS_DIR/networking/warp_toggle.py"
                ;;
            '  Tailscale Setup')
                run_term_hold "tailscale_setup" "$SCRIPTS_DIR/networking/01_tailscale_setup.sh"
                ;;
            '  OpenSSH Setup')
                run_term_hold "openssh_setup" "$SCRIPTS_DIR/networking/02_openssh_setup.py"
                ;;
            '  SSHFS Mounter')
                run_term_hold "dusky_sshfs" python3 "$SCRIPTS_DIR/networking/dusky_ssh_filesystem.py"
                ;;
            '  VNC Remote')
                run_term_hold "vnc_setup" python3 "$SCRIPTS_DIR/networking/vnc/vnc_setup.py"
                ;;
            '  WireGuard New')
                run_term_hold "dusky_wg_new" "$SCRIPTS_DIR/networking/dusky_wireguard_new.sh"
                ;;
            '  WireGuard Status')
                require_commands wg || continue
                run_term_hold "dusky_wg_status" bash -c "sudo wg show all; echo; printf 'Press Enter to close...'; read -r _"
                ;;
            '  WireGuard Setup')
                run_term_hold "dusky_wg_setup" "$SCRIPTS_DIR/networking/dusky_wireguard_setup.sh"
                ;;
            '  ARP Scan')
                run_term_hold "arp_scan" "$SCRIPTS_DIR/networking/arp_scan.sh"
                ;;
            '  WiFi Audit')
                run_term_hold "airmon_ng" python3 "$SCRIPTS_DIR/networking/airmon_ng_gpu.py"
                ;;
            '  Block Distractions')
                run_term_hold "hosts_block" "$SCRIPTS_DIR/arch_setup_scripts/scripts/325_hosts_files_block.sh"
                ;;
            '  FTP Setup')
                run_term_hold "ftp_setup" "$SCRIPTS_DIR/arch_setup_scripts/scripts/250_ftp_arch.sh"
                ;;
            *)
                return 0
                ;;
        esac
    done
}

show_system_menu() {
    local choice

    while :; do
        choice=$(menu_select "System" SYSTEM_MENU "" "$SUB_HINT") || return 0

        case "$choice" in
            '  Dusky Updater')
                run_term_hold "update_dusky" bash -c "'$SCRIPTS_DIR/update_dusky/python/update_dusky_supervisor.py'"
                ;;
            '  Arch Updater')
                run_term_hold "arch_update" bash -c "paru -Syu"
                ;;
            '  Fastfetch')
                run_term_hold "fastfetch" fastfetch
                ;;
            '  Dysk Usage')
                run_term_hold "dysk" dysk
                ;;
            '  Disk I/O Monitor')
                run_term_hold "dusky_disk_monitor_io" "$SCRIPTS_DIR/drives/dusky_disk_monitor_io.py"
                ;;
            '  BTRFS Stats')
                run_term_hold "btrfs_stats" "$SCRIPTS_DIR/drives/btrfs_zstd_compression_stats.sh"
                ;;
            '  Drive Health')
                run_term_hold "dusky_drive_health" python3 "$SCRIPTS_DIR/drives/drive_health/dusky_drive_health.py"
                ;;
            '  Disk Formatter')
                run_term_hold "dusky_formatter" sudo python3 "$SCRIPTS_DIR/drives/format/dusky_formater.py"
                ;;
            '  NTFS Fix')
                run_term_hold "ntfs_fix" "$SCRIPTS_DIR/drives/ntfs_fix.sh"
                ;;
            '  Snapshots')
                run_term_hold "dusky_snapshots" sudo python3 "$SCRIPTS_DIR/btrfs_snapshots/cc/dusky_snapshot_manager.py"
                ;;
            '  View Backups')
                run_term_hold "backup_viewer" bash -c "yazi '$HOME/Documents/dusky_backups/'"
                ;;
            '  Send Logs')
                run_term_hold "send_logs" "$SCRIPTS_DIR/arch_setup_scripts/send_logs.sh"
                ;;
            '  Cache Purge')
                run_term_hold "cache_purge" "$SCRIPTS_DIR/arch_setup_scripts/scripts/365_cache_purge.sh"
                ;;
            *)
                return 0
                ;;
        esac
    done
}

show_performance_menu() {
    local choice

    while :; do
        choice=$(menu_select "Performance" PERFORMANCE_MENU "" "$SUB_HINT") || return 0

        case "$choice" in
            '  System Monitor')
                run_term_hold "dusky_monitor" python3 "$SCRIPTS_DIR/performance/dusky_monitor/dusky_monitor.py"
                ;;
            '  Benchmark')
                run_term_hold "sysbench_benchmark" "$SCRIPTS_DIR/performance/sysbench_benchmark.py"
                ;;
            '  Process Terminator')
                run_term_hold "process_terminator" python3 "$SCRIPTS_DIR/performance/services_and_process_terminator/terminator.py"
                ;;
            '  Screentime Stats')
                run_term_hold "dusky_screentime" python3 "$SCRIPTS_DIR/screentime/screentime_tui.py"
                ;;
            '  Autostart Manager')
                run_term_hold "dusky_tui" python3 "$SCRIPTS_DIR/hypr/misc/tui_autostart.py"
                ;;
            '  ZRAM Status')
                run_term_hold "zram_status" python3 "$SCRIPTS_DIR/performance/zram/zram_swap_manager.py" --status
                ;;
            '  Memory Sweep')
                run_term_hold "zram_sweep" sudo python3 "$SCRIPTS_DIR/performance/zram/dusky_pro_active_swap_modifier_cc.py" --run-now --force
                ;;
            '  ZRAM Diagnostics')
                run_term_hold "zram_diag" "$SCRIPTS_DIR/arch_setup_scripts/scripts/208_check_zram0_zram1_setup.py"
                ;;
            '  Kernel Compiler')
                run_term_hold "dusky_kernel_compile" python3 "$SCRIPTS_DIR/kernel/dusky_kernel_compiler/dusky_kernal_compile.py"
                ;;
            '  Boot Manager')
                run_term_hold "dusky_tui" python3 "$SCRIPTS_DIR/kernel/tui_kernal_systemd_boot.py"
                ;;
            *)
                return 0
                ;;
        esac
    done
}

show_power_menu() {
    local choice

    while :; do
        choice=$(menu_select "Power" POWER_MENU "" "$SUB_HINT") || return 0

        case "$choice" in
            '  Profile Balanced')
                run_app "$SCRIPTS_DIR/battery/tlp/tlp_mode_toggle.sh" balanced
                ;;
            '  Profile Performance')
                run_app "$SCRIPTS_DIR/battery/tlp/tlp_mode_toggle.sh" performance
                ;;
            '  Profile Power Saver')
                run_app "$SCRIPTS_DIR/battery/tlp/tlp_mode_toggle.sh" power-saver
                ;;
            '  Saver On')
                run_term_hold "power_saver_on" "$SCRIPTS_DIR/battery/power_saver.sh" -e
                ;;
            '  Saver Off')
                run_term_hold "power_saver_off" "$SCRIPTS_DIR/battery/power_saver.sh" -d
                ;;
            '  Power Manager')
                run_term_hold "dusky_tui" python3 "$SCRIPTS_DIR/power/tui_power.py"
                ;;
            '  Battery Config')
                run_term_hold "bat_notify_config" "$SCRIPTS_DIR/arch_setup_scripts/scripts/440_config_bat_notify.sh"
                ;;
            '  Idle Settings')
                run_term_hold "dusky_tui" python3 "$SCRIPTS_DIR/hypridle/tui_dusky_hypridle.py"
                ;;
            '  Lock Screen Select')
                run_term_hold "dusky_tui" python3 "$SCRIPTS_DIR/hyprlock/tui_hyprlock.py"
                ;;
            '  Idle Toggle')
                run_app "$SCRIPTS_DIR/waybar/toggle_hypridle.sh"
                ;;
            '  Trackpad Config')
                run_term_hold "dusky_tui" python3 "$SCRIPTS_DIR/hypr/input/tui_trackpad.py"
                ;;
            '  Mouse Left-Handed')
                run_term_hold "mouse_handedness" "$SCRIPTS_DIR/arch_setup_scripts/scripts/265_mouse_button_reverse.sh" --left
                ;;
            '  Mouse Right-Handed')
                run_term_hold "mouse_handedness" "$SCRIPTS_DIR/arch_setup_scripts/scripts/265_mouse_button_reverse.sh" --right
                ;;
            '  Bluetooth Manager')
                run_app blueman-manager
                ;;
            *)
                return 0
                ;;
        esac
    done
}

show_configs_menu() {
    local choice

    while :; do
        choice=$(menu_select "Configs" CONFIGS_MENU "" "$SUB_HINT") || return 0

        case "$choice" in
            '  Control Center')
                run_app python3 "$CC_DIR/dusky_control_center.py"
                ;;
            '  Hypr Reload')
                run_app hyprctl reload
                ;;
            '  Main Config')
                edit_file "$HYPR_EDIT_DIR/hyprland.lua"
                ;;
            '  Keybinds')
                edit_file "$HYPR_EDIT_DIR/source/keybinds.lua"
                ;;
            '  Input')
                edit_file "$HYPR_EDIT_DIR/source/input.lua"
                ;;
            '  Monitors')
                edit_file "$HYPR_EDIT_DIR/source/monitors.lua"
                ;;
            '  Window Rules')
                edit_file "$HYPR_EDIT_DIR/source/window_rules.lua"
                ;;
            '  Autostart')
                edit_file "$HYPR_EDIT_DIR/source/autostart.lua"
                ;;
            '  File Manager Default')
                run_term_hold "235_default_apps.py" "$SCRIPTS_DIR/arch_setup_scripts/scripts/235_default_apps.py" --file-manager
                ;;
            '  Browser Default')
                run_term_hold "235_default_apps.py" "$SCRIPTS_DIR/arch_setup_scripts/scripts/235_default_apps.py" --browser
                ;;
            '  Editor Default')
                run_term_hold "235_default_apps.py" "$SCRIPTS_DIR/arch_setup_scripts/scripts/235_default_apps.py" --text-editor
                ;;
            '  Terminal Default')
                run_term_hold "235_default_apps.py" "$SCRIPTS_DIR/arch_setup_scripts/scripts/235_default_apps.py" --terminal
                ;;
            '  Window Rules Generator')
                run_term_hold "dusky_window_rules" "$SCRIPTS_DIR/hypr/rules/window_rules_generator.py"
                ;;
            '  Service Manager')
                run_term_hold "dusky_service_manager" "$SCRIPTS_DIR/services/dusky_service_manager.sh"
                ;;
            '  Font Manager')
                run_term_hold "dusky_tui" python3 "$SCRIPTS_DIR/fonts/tui_fonts.py"
                ;;
            '  Prompt Style')
                run_term_hold "dusky_tui" python3 "$SCRIPTS_DIR/starship/tui_starship.py"
                ;;
            '  Notifications Config')
                run_term_hold "dusky_tui" python3 "$SCRIPTS_DIR/mako_osd/mako_tui/tui_mako.py"
                ;;
            '  GTK Settings')
                run_term_hold "dusky_gsettings" "$SCRIPTS_DIR/gtk/dusky_gsettings.sh"
                ;;
            *)
                return 0
                ;;
        esac
    done
}

show_tools_menu() {
    local choice
    local region

    while :; do
        choice=$(menu_select "Tools" TOOLS_MENU "" "$SUB_HINT") || return 0

        case "$choice" in
            '  Voice Dictation')
                run_app dusky_trigger
                ;;
            '  Voice Install')
                run_term_hold "dusky_stt_install" python3 "$SCRIPTS_DIR/tts_stt/dusky_parakeet/dusky_installer.py"
                ;;
            '  OCR Selection')
                require_commands slurp grim tesseract wl-copy || continue
                region=$(slurp) || return 0
                [[ -n "$region" ]] || return 0

                if ! grim -g "$region" - | tesseract stdin stdout -l eng 2>/dev/null | wl-copy; then
                    error_dialog "OCR failed."
                fi
                return 0
                ;;
            '  LLM Panel')
                run_app "$SCRIPTS_DIR/llm/llm_side_panal/toggle_llm_side_panal.sh"
                ;;
            '  Lens Search')
                run_app "$SCRIPTS_DIR/google_image_search/google_image_search.sh"
                ;;
            '  TTS Install')
                run_term_hold "kokoro_installer" "$SCRIPTS_DIR/tts_stt/dusky_kokoro/kokoro_installer.sh"
                ;;
            '  TTS Engine')
                run_term_hold "dusky_kokoro_tui" "$SCRIPTS_DIR/tts_stt/dusky_kokoro/tui/kokoro_tui.sh"
                ;;
            '  Emoji Picker')
                run_app "$SCRIPTS_DIR/rofi/emoji.sh"
                ;;
            '  Screenshot')
                run_app "$SCRIPTS_DIR/rofi/dusky_rofi_screenshot.sh"
                ;;
            '  Calculator')
                run_app "$SCRIPTS_DIR/rofi/calculator.sh"
                ;;
            '  Clipboard History')
                run_app "$SCRIPTS_DIR/rofi/rofi_clipboard.sh"
                ;;
            '  Firefox Tune')
                run_term_hold "dusky_firefox_opt" python3 "$SCRIPTS_DIR/firefox/optimize_firefox.py"
                ;;
            '  Git Config')
                run_term_hold "git_config" "$SCRIPTS_DIR/arch_setup_scripts/scripts/300_git_config.sh"
                ;;
            '  Git New Repo')
                run_term_hold "dusky_backup_new" "$SCRIPTS_DIR/git/dusky_backup_manager.py" --new
                ;;
            '  Git Relink Repo')
                run_term_hold "dusky_backup_relink" "$SCRIPTS_DIR/git/dusky_backup_manager.py" --relink
                ;;
            '  Git Time Machine')
                run_term_hold "dusky_time_machine" "$SCRIPTS_DIR/git/time_machine/dusky_time_machine_tui.sh"
                ;;
            '  Neovim Manager')
                run_term_hold "dusky_nvim_manager" "$SCRIPTS_DIR/nvim/dusky_neovim_manager.sh"
                ;;
            *)
                return 0
                ;;
        esac
    done
}

show_help_menu() {
    local choice

    while :; do
        choice=$(menu_select "Help" HELP_MENU "" "$SUB_HINT") || return 0

        case "$choice" in
            '  Keybindings List')
                run_app "$SCRIPTS_DIR/hypr/input/rofi_keybinds/keybindings.sh"
                ;;
            '  Keybind Cheatsheet')
                run_term "DuskyKeybindsCheatsheet" python3.14 "$SCRIPTS_DIR/hypr/input/keybinds_cheatsheet.py"
                ;;
            '  Edit Keybinds')
                run_term_hold "dusky_keybinds" "$SCRIPTS_DIR/hypr/input/dusky_keybinds.py"
                ;;
            '  Arch Wiki')
                run_app xdg-open "https://wiki.archlinux.org/"
                ;;
            '  Hyprland Wiki')
                run_app xdg-open "https://wiki.hypr.land/"
                ;;
            *)
                return 0
                ;;
        esac
    done
}

route_selection() {
    local choice="$1"

    case "$choice" in
        '  Search Notes')
            perform_global_search
            ;;
        '  Apps')
            run_app rofi -show drun -run-command 'dusky-run -- {cmd}'
            ;;
        '  Display & Theme')
            show_display_menu
            ;;
        '  Audio & Media')
            show_audio_menu
            ;;
        '  Network & Remote')
            show_network_menu
            ;;
        '  System & Storage')
            show_system_menu
            ;;
        '  Performance & Memory')
            show_performance_menu
            ;;
        '  Power & Devices')
            show_power_menu
            ;;
        '  Configs & Services')
            show_configs_menu
            ;;
        '  Tools & AI')
            show_tools_menu
            ;;
        '  Help & Keys')
            show_help_menu
            ;;
        '  Session Power')
            run_rofi_mode "power-menu" "$SCRIPTS_DIR/rofi/powermenu.sh" -no-fixed-num-lines -i
            ;;
        *)
            case "${choice,,}" in
                search|search-notes|notes)
                    perform_global_search
                    ;;
                apps|app|drun)
                    run_app rofi -show drun -run-command 'dusky-run -- {cmd}'
                    ;;
                display|theme|visuals|visuals-display)
                    show_display_menu
                    ;;
                audio|sound|media|audio-media)
                    show_audio_menu
                    ;;
                network|networking|net|remote|network-remote)
                    show_network_menu
                    ;;
                system|storage|drives|disks|system-storage)
                    show_system_menu
                    ;;
                performance|memory|perf|performance-memory)
                    show_performance_menu
                    ;;
                power|devices|battery|hardware|power-devices)
                    show_power_menu
                    ;;
                configs|config|services|cc|control-center|configs-services)
                    show_configs_menu
                    ;;
                tools|ai|utils|utilities|tools-ai)
                    show_tools_menu
                    ;;
                help|keys|learn|learn-help|keybinds)
                    show_help_menu
                    ;;
                session|session-power|powermenu|logout)
                    run_rofi_mode "power-menu" "$SCRIPTS_DIR/rofi/powermenu.sh" -no-fixed-num-lines -i
                    ;;
                *)
                    return 1
                    ;;
            esac
            ;;
    esac
}

show_main_menu() {
    local choice

    while :; do
        choice=$(menu_select "Main" MAIN_MENU "" "$MAIN_HINT") || exit 0
        route_selection "$choice" || continue
    done
}

validate_startup() {
    require_commands rofi dusky-run fd file realpath xdg-open python3 "$TERMINAL" || exit 1

    [[ -d "$SCRIPTS_DIR" ]] || {
        error_dialog "Scripts directory not found: $SCRIPTS_DIR"
        exit 1
    }
}

validate_startup

if [[ -n "${1:-}" ]]; then
    route_selection "$1" || exit 0
else
    show_main_menu
fi
