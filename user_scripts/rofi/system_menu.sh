#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# ARCH / HYPRLAND ROFI MENU SYSTEM
# Dependencies: rofi-wayland, uwsm, kitty, fd, file, xdg-utils
# -----------------------------------------------------------------------------

set -uo pipefail

declare -gr SCRIPTS_DIR="${HOME}/user_scripts"
declare -gr HYPR_CONF="${HOME}/.config/hypr"
declare -gr HYPR_SOURCE="${HYPR_CONF}/source"
declare -gr SEARCH_DIR="${HOME}/Documents/pensive/linux"

declare -gr TERMINAL="kitty"

declare -ga EDITOR_CMD=()
read -r -a EDITOR_CMD <<< "${EDITOR:-nvim}"
readonly -a EDITOR_CMD

declare -gr ROFI_THEME_MAIN='window {width: 25%;} listview {lines: 12;}'
declare -gr ROFI_THEME_SEARCH='window {width: 80%;}'
declare -agr ROFI_MENU_CMD=(rofi -dmenu -i -no-custom -theme-str "$ROFI_THEME_MAIN")
declare -agr ROFI_SEARCH_CMD=(rofi -dmenu -i -no-custom -theme-str "$ROFI_THEME_SEARCH")

declare -agr MAIN_MENU=(
    '🔍  Search Notes'
    '󰀻  Apps'
    '󰧑  Learn/Help'
    '󱓞  Utils'
    '󱚤  AI & Voice'
    '󰹑  Visuals & Display'
    '󰇅  System & Drives'
    '󱐋  Performance'
    '󰂄  Power & Battery'
    '󰛳  Networking'
    '  Configs'
    '󰐉  Power'
)

declare -agr LEARN_MENU=(
    '󰌌  Keybindings (List)'
    '󰣇  Arch Wiki'
    '  Hyprland Wiki'
)

declare -agr AI_MENU=(
    '󰔊  TTS - Kokoro (GPU)'
    '󰔊  TTS - Kokoro (CPU)'
    '󰍬  STT - Faster Whisper'
    '󰍬  STT - Parakeet (GPU)'
    '󰍉  OCR Selection'
)

declare -agr UTILS_MENU=(
    '  Dusky Control Center'
    '󰖩  Wi-Fi (TUI)'
    '󰂯  Bluetooth'
    '󰕾  Audio Mixer'
    '󰞅  Emoji Picker'
    '  Screenshot (Swappy)'
    '󰅇  Clipboard Persistence'
    '󰉋  File Manager Switch'
    '󰍽  Mouse Handedness'
    '󰌌  Wayclick (Key Sounds)'
)

declare -agr VISUALS_MENU=(
    '󰸌  Cycle Matugen Theme'
    '󰸌  Matugen Config'
    '󰸉  Wallpaper App'
    '󰸉  Rofi Wallpaper'
    '󱐋  Animations'
    '󰃜  Shaders'
    '󰖨  Hyprsunset Slider'
    '󰖳  Blur/Opacity/Shadow'
    '󰍜  Waybar Config'
    '󰶡  Rotate Screen (CW)'
    '󰶣  Rotate Screen (CCW)'
    '󰐕  Scale Up (+)'
    '󰐖  Scale Down (-)'
)

declare -agr SYSTEM_MENU=(
    '  Fastfetch'
    '󰋊  Dysk (Disk Space)'
    '󱂵  Disk IO Monitor'
    '󰗮  BTRFS Compression Stats'
)

declare -agr PERFORMANCE_MENU=(
    '󰓅  Sysbench Benchmark'
    '󰃢  Cache Purge'
    '󰿅  Process Terminator'
)

declare -agr POWER_BATTERY_MENU=(
    '󰶐  Hypridle Timeout'
    '󰂄  Battery Notification Config'
    '  Power Saver Mode'
)

declare -agr NETWORKING_MENU=(
    '󰖂  Warp VPN Toggle'
    '󰣀  OpenSSH Setup'
    '󰖩  WiFi Testing (Airmon)'
)

declare -agr CONFIG_MENU=(
    '  Hyprland Main'
    '󰌌  Keybinds'
    '󱐋  Animations'
    '󰖲  Input'
    '󰍹  Monitors'
    '  Window Rules'
    '󰍜  Waybar'
    '󰒲  Hypridle'
    '󰌾  Hyprlock'
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
    local -n options="$array_name"
    local -a cmd=("${ROFI_MENU_CMD[@]}" -p "$prompt")

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
    spawn uwsm-app -- "$@"
    exit 0
}

run_term() {
    local class="$1"
    shift

    validate_launch_target "$1" || return 0
    spawn uwsm-app -- "$TERMINAL" --class "$class" -e "$@"
    exit 0
}

run_term_hold() {
    local class="$1"
    shift

    validate_launch_target "$1" || return 0
    spawn uwsm-app -- "$TERMINAL" --hold --class "$class" -e "$@"
    exit 0
}

run_rofi_mode() {
    local mode="$1"
    local script="$2"

    require_executable_file "$script" || return 0
    run_app rofi -show "$mode" -modi "$mode:$script"
}

open_editor() {
    local file="$1"

    validate_launch_target "${EDITOR_CMD[0]}" || return 0
    spawn uwsm-app -- "$TERMINAL" --class "nvim_config" -e "${EDITOR_CMD[@]}" "$file"
    exit 0
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
    selected_relative=$(printf '%s\n' "${results[@]}" | "${ROFI_SEARCH_CMD[@]}" -p "Search") || return 0
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
            open_editor "$resolved_path"
            ;;
        *)
            run_app xdg-open "$resolved_path"
            ;;
    esac
}

show_learn_menu() {
    local choice

    while :; do
        choice=$(menu_select "Learn" LEARN_MENU) || return 0

        case "$choice" in
            '󰌌  Keybindings (List)')
                run_app "$SCRIPTS_DIR/rofi/keybindings.sh"
                ;;
            '󰣇  Arch Wiki')
                run_app xdg-open "https://wiki.archlinux.org/"
                ;;
            '  Hyprland Wiki')
                run_app xdg-open "https://wiki.hypr.land/"
                ;;
            *)
                return 0
                ;;
        esac
    done
}

show_ai_menu() {
    local choice
    local region

    while :; do
        choice=$(menu_select "AI Tools" AI_MENU) || return 0

        case "$choice" in
            '󰔊  TTS - Kokoro (GPU)')
                run_app "$SCRIPTS_DIR/tts_stt/kokoro_gpu/speak.sh"
                ;;
            '󰔊  TTS - Kokoro (CPU)')
                run_app "$SCRIPTS_DIR/tts_stt/kokoro_cpu/kokoro.sh"
                ;;
            '󰍬  STT - Faster Whisper')
                run_app "$SCRIPTS_DIR/tts_stt/faster_whisper/faster_whisper_stt.sh"
                ;;
            '󰍬  STT - Parakeet (GPU)')
                run_app "$SCRIPTS_DIR/tts_stt/parakeet/parakeet.sh"
                ;;
            '󰍉  OCR Selection')
                require_commands slurp grim tesseract wl-copy || continue
                region=$(slurp) || exit 0
                [[ -n "$region" ]] || exit 0

                if ! grim -g "$region" - | tesseract stdin stdout -l eng 2>/dev/null | wl-copy; then
                    error_dialog "OCR failed."
                fi
                exit 0
                ;;
            *)
                return 0
                ;;
        esac
    done
}

show_utils_menu() {
    local choice

    while :; do
        choice=$(menu_select "Utils" UTILS_MENU) || return 0

        case "$choice" in
            '  Dusky Control Center')
                run_app "$SCRIPTS_DIR/dusky_system/dusky_control_center.py"
                ;;
            '󰖩  Wi-Fi (TUI)')
                run_term "wifitui" wifitui
                ;;
            '󰂯  Bluetooth')
                run_app blueman-manager
                ;;
            '󰕾  Audio Mixer')
                run_app pavucontrol
                ;;
            '󰞅  Emoji Picker')
                run_app "$SCRIPTS_DIR/rofi/emoji.sh"
                ;;
            '  Screenshot (Swappy)')
                require_commands slurp grim swappy || continue
                spawn "$BASH" -lc 'region=$(slurp) || exit 0; grim -g "$region" - | uwsm-app -- swappy -f -'
                exit 0
                ;;
            '󰅇  Clipboard Persistence')
                run_term_hold "clipboard_persistance.sh" "$SCRIPTS_DIR/desktop_apps/clipboard_persistance.sh"
                ;;
            '󰉋  File Manager Switch')
                run_term_hold "file_manager_switch.sh" "$SCRIPTS_DIR/desktop_apps/file_manager_switch.sh"
                ;;
            '󰍽  Mouse Handedness')
                run_term_hold "mouse_button_reverse.sh" "$SCRIPTS_DIR/desktop_apps/mouse_button_reverse.sh"
                ;;
            '󰌌  Wayclick (Key Sounds)')
                run_app "$SCRIPTS_DIR/wayclick/wayclick.sh"
                ;;
            *)
                return 0
                ;;
        esac
    done
}

show_visuals_menu() {
    local choice

    while :; do
        choice=$(menu_select "Visuals & Display" VISUALS_MENU) || return 0

        case "$choice" in
            '󰸌  Cycle Matugen Theme')
                run_app "$SCRIPTS_DIR/theme_matugen/theme_ctl.sh" random
                ;;
            '󰸌  Matugen Config')
                run_app "$SCRIPTS_DIR/rofi/rofi_theme.sh"
                ;;
            '󰸉  Wallpaper App')
                run_app waypaper
                ;;
            '󰸉  Rofi Wallpaper')
                run_app "$SCRIPTS_DIR/rofi/rofi_wallpaper_selctor.sh"
                ;;
            '󱐋  Animations')
                run_rofi_mode "animations" "$SCRIPTS_DIR/rofi/hypr_anim.sh"
                ;;
            '󰃜  Shaders')
                run_app "$SCRIPTS_DIR/rofi/shader_menu.sh"
                ;;
            '󰖨  Hyprsunset Slider')
                run_app "$SCRIPTS_DIR/sliders/hyprsunset_slider.sh"
                ;;
            '󰖳  Blur/Opacity/Shadow')
                run_app "$SCRIPTS_DIR/hypr/hypr_blur_opacity_shadow_toggle.sh"
                ;;
            '󰍜  Waybar Config')
                run_term "waybar_swap_config.sh" "$SCRIPTS_DIR/waybar/waybar_swap_config.sh"
                ;;
            '󰶡  Rotate Screen (CW)')
                run_app "$SCRIPTS_DIR/hypr/screen_rotate.sh" -90
                ;;
            '󰶣  Rotate Screen (CCW)')
                run_app "$SCRIPTS_DIR/hypr/screen_rotate.sh" +90
                ;;
            '󰐕  Scale Up (+)')
                run_app "$SCRIPTS_DIR/hypr/adjust_scale.sh" +
                ;;
            '󰐖  Scale Down (-)')
                run_app "$SCRIPTS_DIR/hypr/adjust_scale.sh" -
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
        choice=$(menu_select "System & Drives" SYSTEM_MENU) || return 0

        case "$choice" in
            '  Fastfetch')
                run_term_hold "fastfetch" fastfetch
                ;;
            '󰋊  Dysk (Disk Space)')
                run_term_hold "dysk" dysk
                ;;
            '󱂵  Disk IO Monitor')
                run_term "io_monitor.sh" "$SCRIPTS_DIR/drives/io_monitor.sh"
                ;;
            '󰗮  BTRFS Compression Stats')
                run_term_hold "btrfs_zstd_compression_stats.sh" "$SCRIPTS_DIR/drives/btrfs_zstd_compression_stats.sh"
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
        choice=$(menu_select "Performance" PERFORMANCE_MENU) || return 0

        case "$choice" in
            '󰓅  Sysbench Benchmark')
                run_term_hold "sysbench_benchmark.sh" "$SCRIPTS_DIR/performance/sysbench_benchmark.sh"
                ;;
            '󰃢  Cache Purge')
                run_term_hold "cache_purge.sh" "$SCRIPTS_DIR/desktop_apps/cache_purge.sh"
                ;;
            '󰿅  Process Terminator')
                run_term_hold "performance.sh" "$SCRIPTS_DIR/performance/services_and_process_terminator.sh"
                ;;
            *)
                return 0
                ;;
        esac
    done
}

show_power_battery_menu() {
    local choice

    while :; do
        choice=$(menu_select "Power & Battery" POWER_BATTERY_MENU) || return 0

        case "$choice" in
            '󰶐  Hypridle Timeout')
                run_term "timeout.sh" "$SCRIPTS_DIR/hypridle/timeout.sh"
                ;;
            '󰂄  Battery Notification Config')
                run_term "config_bat_notify.sh" "$SCRIPTS_DIR/battery/notify/config_bat_notify.sh"
                ;;
            '  Power Saver Mode')
                run_term_hold "power_saver.sh" "$SCRIPTS_DIR/battery/power_saver.sh"
                ;;
            *)
                return 0
                ;;
        esac
    done
}

show_networking_menu() {
    local choice

    while :; do
        choice=$(menu_select "Networking" NETWORKING_MENU) || return 0

        case "$choice" in
            '󰖂  Warp VPN Toggle')
                run_app "$SCRIPTS_DIR/networking/warp_toggle.sh"
                ;;
            '󰣀  OpenSSH Setup')
                require_executable_file "$SCRIPTS_DIR/networking/02_openssh_setup.sh" || continue
                run_term_hold "wifi_testing" sudo "$SCRIPTS_DIR/networking/02_openssh_setup.sh"
                ;;
            '󰖩  WiFi Testing (Airmon)')
                require_executable_file "$SCRIPTS_DIR/networking/ax201_wifi_testing.sh" || continue
                run_term_hold "wifi_testing" sudo "$SCRIPTS_DIR/networking/ax201_wifi_testing.sh"
                ;;
            *)
                return 0
                ;;
        esac
    done
}

show_config_menu() {
    local choice

    while :; do
        choice=$(menu_select "Edit Configs" CONFIG_MENU) || return 0

        case "$choice" in
            '  Hyprland Main')
                open_editor "$HYPR_CONF/hyprland.conf"
                ;;
            '󰌌  Keybinds')
                open_editor "$HYPR_SOURCE/keybinds.conf"
                ;;
            '󱐋  Animations')
                open_editor "$HYPR_SOURCE/animations/active/active.conf"
                ;;
            '󰖲  Input')
                open_editor "$HYPR_SOURCE/input.conf"
                ;;
            '󰍹  Monitors')
                open_editor "$HYPR_SOURCE/monitors.conf"
                ;;
            '  Window Rules')
                open_editor "$HYPR_SOURCE/window_rules.conf"
                ;;
            '󰍜  Waybar')
                open_editor "$HOME/.config/waybar/config.jsonc"
                ;;
            '󰒲  Hypridle')
                open_editor "$HYPR_CONF/hypridle.conf"
                ;;
            '󰌾  Hyprlock')
                open_editor "$HYPR_CONF/hyprlock.conf"
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
        '🔍  Search Notes')
            perform_global_search
            ;;
        '󰀻  Apps')
            run_app rofi -show drun -run-command 'uwsm app -- {cmd}'
            ;;
        '󰧑  Learn/Help')
            show_learn_menu
            ;;
        '󱓞  Utils')
            show_utils_menu
            ;;
        '󱚤  AI & Voice')
            show_ai_menu
            ;;
        '󰹑  Visuals & Display')
            show_visuals_menu
            ;;
        '󰇅  System & Drives')
            show_system_menu
            ;;
        '󱐋  Performance')
            show_performance_menu
            ;;
        '󰂄  Power & Battery')
            show_power_battery_menu
            ;;
        '󰛳  Networking')
            show_networking_menu
            ;;
        '  Configs')
            show_config_menu
            ;;
        '󰐉  Power')
            run_rofi_mode "power-menu" "$SCRIPTS_DIR/rofi/powermenu.sh"
            ;;
        *)
            case "${choice,,}" in
                search|search-notes|notes)
                    perform_global_search
                    ;;
                apps|app)
                    run_app rofi -show drun -run-command 'uwsm app -- {cmd}'
                    ;;
                learn|help|learn/help)
                    show_learn_menu
                    ;;
                utils|utilities)
                    show_utils_menu
                    ;;
                ai|voice|ai-voice)
                    show_ai_menu
                    ;;
                visuals|display|visuals-display)
                    show_visuals_menu
                    ;;
                system|drives|system-drives)
                    show_system_menu
                    ;;
                performance)
                    show_performance_menu
                    ;;
                battery|power-battery)
                    show_power_battery_menu
                    ;;
                networking|network|net)
                    show_networking_menu
                    ;;
                configs|config)
                    show_config_menu
                    ;;
                power)
                    run_rofi_mode "power-menu" "$SCRIPTS_DIR/rofi/powermenu.sh"
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
        choice=$(menu_select "Main" MAIN_MENU) || exit 0
        route_selection "$choice" || continue
    done
}

validate_startup() {
    require_commands rofi uwsm-app uwsm fd file realpath xdg-open "$TERMINAL" "${EDITOR_CMD[0]}" || exit 1

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
