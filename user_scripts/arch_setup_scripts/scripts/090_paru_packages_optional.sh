#!/usr/bin/env bash
#d: Install optional packages from the AUR

set -Eeuo pipefail
shopt -s extglob

# =============================================================================
# ▼ USER CONFIGURATION (EDIT THIS SECTION) ▼
# =============================================================================

declare -r APP_TITLE="Dusky Optional Packages"
declare -r APP_VERSION="v3.1 (Dusky TUI 5.9.1 Engine)"

# Format: Category | Package Name | Description
readonly RAW_PKG_DATA="
Tools       | pacseek-bin           | TUI for browsing Pacman/AUR databases
# Tools       | yayfzf                | TUI for browsing AUR databases
# Tools       | gnome-software        | Gnome Package installer and manager
Tools       | pamac-aur             | GUI Package installer and manager
Tools       | keypunch-git          | Gamified typing proficiency trainer
Tools       | kew-git               | Minimalist, efficient CLI music player
Tools       | youtube-dl-gui-bin    | GUI wrapper for yt-dlp
Tools       | sysmontask            | Windows-style Task Manager for Linux
Tools       | glances               | CLI curses-based monitoring tool
Tools       | lazydocker            | TUI for managing Docker containers
Tools       | kvantum               | SVG-based theme engine for Qt applications
Tools       | gparted               | GUI partition editor for disk management
Tools       | xorg-xhost            | Allow unfettered access to xorg root apps (timeshift, gparted)
Tools       | baobab                | Disk usage analyzer to visualize storage
Tools       | grsync                | GUI rsync frontend for backups
Tools       | caligula              | User-friendly, lightweight disk imager
Tools       | collision             | Verifies file hashes (MD5, SHA, etc.)
Tools       | impression            | Tool to create bootable drives from ISOs
Tools       | xembed-sni-proxy-standalone-git | Fix proton/wine apps tray icons
Tools       | showmethekey          | Screen keystroke visualizer for screencasts
Tools       | identity              | Compare images and videos side-by-side
Tools       | zellij                | Modern terminal workspace/multiplexer (Rust)
Tools       | tealdeer              | Fast tldr client (simplified man pages)
Tools       | man-db                | The standard manual pager suite
Tools       | avahi                 | Service Discovery using mDNS/DNS-SD (Bonjour compatible)
Tools       | xdg-desktop-portal-kde | KDE backend for xdg-desktop-portal (file chooser, etc.)
Tools       | evince                | Document viewer (PDF, PostScript, XPS, djvu, cbz, cbr)
Tools       | aria2                 | High-speed download utility (Pair with uget)
Tools       | uget                  | Download Manager GUI (Pair with aria2)
Tools       | libdvdcss             | Portable abstraction library for DVD decryption
Internet    | filezilla             | Fast and reliable FTP/SFTP client
Internet    | zapret2               | Deep Packet Inspection circumvention for blocked sites
Internet    | qbittorrent           | Feature-rich BitTorrent client (Qt-based)
Internet    | networkmanager-openvpn| NetworkManager VPN plugin for OpenVPN (with GUI)
Internet    | network-manager-applet| NetworkManager applet, GUI, System Tray
Internet    | vesktop               | Custom Discord client (Vencord + Electron)
Internet    | beeper-bin            | Universal chat app (Matrix bridge)
Internet    | webapp-manager        | Run websites as if they were apps
Productivity| pinta                 | Simple drawing/editing tool (Paint.NET clone)
Productivity| gimp                  | Photoshop alternative for Linux
Productivity| libreoffice-still     | Microsoft Office alternative (Stable)
# Productivity| libreoffice-fresh     | Microsoft Office alternative (latest)
Productivity| calcurse              | Text-based calendar and scheduling application
Productivity| gnome-calendar        | Simple and beautiful calendar (GNOME)
Productivity| blanket               | Ambient noise player for focus and productivity
Productivity| errands               | Simple to-do list application
Productivity| obsidian              | Markdown-based knowledge base and note taking
Productivity| xournalpp             | Handwriting notetaking software with PDF annotation support
Productivity| opencode              | CLI harness for coding
Productivity| antigravity           | Google's IDE for coding
Productivity| speech-dispatcher     | For getting speech to text for firefox to work 1 of 2
Productivity| espeakup              | For getting speech to text for firefox to work 2 of 2
Docs        | arch-wiki-lite        | Compressed Wiki reader (Pair with arch-wiki-docs)
Docs        | arch-wiki-docs        | Arch Wiki data pages (Pair with arch-wiki-lite)
Media       | pear-desktop-bin      | Youtube Music GUI
Media       | sonora-bin            | Native music client (Spotify + YouTube Music + local)
Media       | noto-fonts-cjk        | Asian fonts
Media       | noto-fonts            | Asian fonts
Media       | noto-fonts-emoji      | Google Noto Color Emoji font
Media       | cantarell-fonts       | Humanist sans serif font
Media       | ttf-bitstream-vera    | Bitstream Vera fonts
Media       | ttf-dejavu            | Font based on Bitstream Vera (wider character range)
Media       | ttf-liberation        | Metric compatible with Arial, Times New Roman, Courier New
Media       | otf-font-awesome      | Iconic font designed for Bootstrap - otf format
Media       | woff2-font-awesome    | Iconic font designed for Bootstrap - woff2 format
Media       | ttf-jetbrains-mono-nerd | Patched font JetBrains Mono from nerd fonts library
Media       | otf-atkinsonhyperlegiblemono-nerd | Atkinson Hyperlegible Mono Nerd Font
Media       | ttf-atkinson-hyperlegible | Atkinson Hyperlegible TTF Font
Media       | otf-atkinson-hyperlegible | Atkinson Hyperlegible OTF Font
Media       | awesome-terminal-fonts| Fonts/icons for powerlines
Media       | papirus-folders       | Folder color theming for Papirus (matugen Lab, stable)
Media       | ttf-opensans          | Sans-serif typeface commissioned by Google
Media       | ttf-meslo-nerd        | Patched font Meslo LG from nerd fonts library
Media       | obs-studio            | Software for video recording and live streaming
Media       | gpu-screen-recorder   | Low-load screen recorder (ShadowPlay alternative)
Media       | audacity              | Multi-track audio editor and recorder
Media       | handbrake             | Open source video transcoder
Media       | guvcview              | Simple GTK interface for capturing video from webcams
Media       | krita                 | Digital painting and sketching application
Media       | termusic              | Terminal-based music player (TUI)
Media       | vlc                   | The ultimate media player for all formats
Media       | vlc-plugins-all       | Plugins for VLC
Games       | pipes-rs-bin          | Rust port of the classic pipes screensaver
Games       | 2048.c                | The 2048 sliding tile game in C
Games       | clidle-bin            | Wordle clone for the command line
Games       | maze-tui              | Visual maze generator and solver
Games       | vitetris              | Classic Tetris clone for the terminal
Games       | ttyper                | Terminal-based typing test and practice
Security    | wdpass                | Unlock Western Digital MyPassport drives
Security    | dislocker             | FUSE driver to read BitLocker partitions
Security    | clamav                | Open source antivirus engine for detecting malware
Drivers     | b43-firmware          | Legacy Broadcom B43 wireless firmware
Drivers     | usbmuxd               | Socket daemon to multiplex connections to iOS devices
Drivers     | cuda                  | NVIDIA's parallel computing architecture toolkit
Drivers     | cudnn                 | NVIDIA CUDA Deep Neural Network library
Hardware    | asusctl               | ASUS ROG/TUF control
Hardware    | fcitx5                | For Non-English Keyboard characters
Hardware    | fcitx5-gtk            | GTK-frontend For Non-English Keyboard characters
Hardware    | fcitx5-qt             | QT-frontend For Non-English Keyboard characters
Hardware    | broadcom-wl-dkms      | Broadcom 802.11 Linux STA wireless driver
Hardware    | macbook12-spi-driver-dkms | Driver for keyboard, touchpad and touchbar on MacBooks
"

# Dimensions & Layout
declare -ri MAX_DISPLAY_ROWS=14
declare -ri BOX_INNER_WIDTH=80
declare -ri ITEM_PADDING=32

declare -ri HEADER_ROWS=4
declare -ri TAB_ROW=3
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

# Terminal Geometry Minimums
declare -ri MIN_TERM_COLS=$(( BOX_INNER_WIDTH + 2 ))
declare -ri MIN_TERM_ROWS=$(( HEADER_ROWS + MAX_DISPLAY_ROWS + 6 ))

# =============================================================================
# ▲ END OF USER CONFIGURATION ▲
# =============================================================================

# --- Pre-computed Constants ---
declare _h_line_buf
printf -v _h_line_buf '%*s' "$BOX_INNER_WIDTH" '' || true
declare -r H_LINE="${_h_line_buf// /─}"
unset _h_line_buf

# --- ANSI Constants ---
declare -r C_RESET=$'\033[0m'
declare -r C_CYAN=$'\033[1;36m'
declare -r C_GREEN=$'\033[1;32m'
declare -r C_MAGENTA=$'\033[1;35m'
declare -r C_RED=$'\033[1;31m'
declare -r C_YELLOW=$'\033[1;33m'
declare -r C_WHITE=$'\033[1;37m'
declare -r C_GREY=$'\033[1;30m'
declare -r C_INVERSE=$'\033[7m'
declare -r C_DIM=$'\033[2m'
declare -r CLR_EOL=$'\033[K'
declare -r CLR_EOS=$'\033[J'
declare -r CLR_SCREEN=$'\033[2J'
declare -r CURSOR_HOME=$'\033[H'
declare -r CURSOR_HIDE=$'\033[?25l'
declare -r CURSOR_SHOW=$'\033[?25h'
declare -r ALT_SCREEN_ON=$'\033[?1049h'
declare -r ALT_SCREEN_OFF=$'\033[?1049l'
declare -r MOUSE_ON=$'\033[?1000h\033[?1002h\033[?1006h\033[?2004h'
declare -r MOUSE_OFF=$'\033[?1000l\033[?1002l\033[?1006l\033[?2004l'

# Input Timing & Buffer Limits
declare -r ESC_READ_TIMEOUT=0.08
declare -r READ_LOOP_TIMEOUT=0.25
declare -ri MAX_ESCAPE_BYTES=64

# --- State Management ---
declare -i SELECTED_ROW=0
declare -i CURRENT_TAB=0
declare -i SCROLL_OFFSET=0
declare -a TABS=()
declare -i TAB_COUNT=0
declare -a TAB_ZONES=()
declare -i TAB_SCROLL_START=0
declare -a TAB_SAVED_ROW=()
declare -a TAB_SAVED_SCROLL=()
declare ORIGINAL_STTY=""
declare -i TUI_STARTED=0

# Runtime geometry and event flags
declare -i TERM_ROWS=0 TERM_COLS=0
declare -gi RESIZE_PENDING=0 PASTE_ACTIVE=0
declare -gi MOUSE_CLICK_PENDING=0 MOUSE_PRESS_X=0 MOUSE_PRESS_Y=0
declare PASTE_TAIL=""
declare SUDO_KEEP_ALIVE_PID=""

# Click Zones for Arrows
declare LEFT_ARROW_ZONE=""
declare RIGHT_ARROW_ZONE=""

# Selection State
declare -A SELECTIONS=()
declare -A DESCRIPTIONS=()
declare -A INSTALLED_PKGS=()

# Execution State
declare -i DO_INSTALL=0

# --- System Helpers ---

log_err() { printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2; }
log_info() { printf '%s[INFO]%s %s\n' "$C_CYAN" "$C_RESET" "$1" >&2; }

stop_sudo_keepalive() {
    if [[ -n "${SUDO_KEEP_ALIVE_PID:-}" ]]; then
        kill "$SUDO_KEEP_ALIVE_PID" 2>/dev/null || :
        wait "$SUDO_KEEP_ALIVE_PID" 2>/dev/null || :
        SUDO_KEEP_ALIVE_PID=""
    fi
}

start_sudo_keepalive() {
    log_info "Sudo privileges may be required to install packages. Authenticating..."
    if ! sudo -v; then
        log_err "Sudo authentication failed."
        return 1
    fi

    (
        set +e
        trap 'exit 0' TERM INT HUP
        while kill -0 "$$" 2>/dev/null; do
            sleep 30 &
            wait $! 2>/dev/null || true
            sudo -n -v 2>/dev/null || exit 0
        done
    ) &
    SUDO_KEEP_ALIVE_PID=$!
    return 0
}

cleanup() {
    if (( TUI_STARTED )); then
        printf '%s%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" "$ALT_SCREEN_OFF" 2>/dev/null || :
        TUI_STARTED=0
    elif [[ -n "${ORIGINAL_STTY:-}" ]]; then
        printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
    fi

    if [[ -n "${ORIGINAL_STTY:-}" ]]; then
        stty "$ORIGINAL_STTY" < /dev/tty 2>/dev/null || :
    fi

    stop_sudo_keepalive
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 131' QUIT
trap 'exit 143' TERM

suspend_ui() {
    MOUSE_CLICK_PENDING=0
    printf '%s%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" "$ALT_SCREEN_OFF" 2>/dev/null || :
    stty "$ORIGINAL_STTY" < /dev/tty 2>/dev/null || exit 1
    TUI_STARTED=0
    kill -s STOP "$$"
    stty -icanon -echo -ixon min 1 time 0 < /dev/tty 2>/dev/null || exit 1
    TUI_STARTED=1
    printf '%s%s%s%s%s' "$ALT_SCREEN_ON" "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME" 2>/dev/null || :
    RESIZE_PENDING=1
}

update_terminal_size() {
    local size
    if size=$(stty size < /dev/tty 2>/dev/null); then
        TERM_ROWS=${size%% *}
        TERM_COLS=${size##* }
    else
        TERM_ROWS=0
        TERM_COLS=0
    fi
}

terminal_size_ok() {
    (( TERM_COLS >= MIN_TERM_COLS && TERM_ROWS >= MIN_TERM_ROWS ))
}

draw_small_terminal_notice() {
    printf '%s%s' "$CURSOR_HOME" "$CLR_SCREEN" 2>/dev/null || true
    printf '%sTerminal too small%s\n' "$C_RED" "$C_RESET" 2>/dev/null || true
    printf '%sNeed at least:%s %d cols × %d rows\n' "$C_YELLOW" "$C_RESET" "$MIN_TERM_COLS" "$MIN_TERM_ROWS" 2>/dev/null || true
    printf '%sCurrent size:%s %d cols × %d rows\n' "$C_WHITE" "$C_RESET" "$TERM_COLS" "$TERM_ROWS" 2>/dev/null || true
    printf '%sResize the terminal to continue. Press q to quit.%s%s' "$C_CYAN" "$C_RESET" "$CLR_EOS" 2>/dev/null || true
}

# --- String Helpers ---

strip_ansi() {
    local v="$1"
    v="${v//$'\033'\[*([0-9;:?<=>])@([@A-Z[\\\]^_\`a-z\{\|\}~])/}"
    REPLY="$v"
}

trim() {
    local var="$1"
    var="${var#"${var%%[![:space:]]*}"}"
    var="${var%"${var##*[![:space:]]}"}"
    REPLY="$var"
}

# --- Core Logic Engine ---

parse_data() {
    # Fast bulk caching of installed packages for O(1) rendering checks
    local -a _all_installed=()
    mapfile -t _all_installed < <(pacman -Qq 2>/dev/null || true)
    local _inst_pkg
    for _inst_pkg in "${_all_installed[@]}"; do
        INSTALLED_PKGS["$_inst_pkg"]=1
    done

    local category pkg desc
    local -A category_map=()
    local -i cat_idx

    while IFS='|' read -r category pkg desc; do
        trim "$category"; category="$REPLY"
        trim "$pkg"; pkg="$REPLY"
        trim "$desc"; desc="$REPLY"

        [[ -z "$category" || "$category" == \#* ]] && continue
        [[ -z "$pkg" ]] && continue

        if [[ -z "${category_map[$category]:-}" ]]; then
            TABS+=("$category")
            TAB_SAVED_ROW+=("0")
            TAB_SAVED_SCROLL+=("0")
            category_map[$category]="$TAB_COUNT"
            declare -ga "TAB_ITEMS_${TAB_COUNT}=()"
            TAB_COUNT=$(( TAB_COUNT + 1 )) 
        fi

        cat_idx="${category_map[$category]}"
        local -n _items_ref="TAB_ITEMS_${cat_idx}"
        _items_ref+=("$pkg")
        
        DESCRIPTIONS["$pkg"]="$desc"
        SELECTIONS["$pkg"]="false"
    done <<< "$RAW_PKG_DATA"
}

toggle_selection() {
    local pkg="$1"
    local current="${SELECTIONS[$pkg]:-false}"
    if [[ "$current" == "true" ]]; then
        SELECTIONS[$pkg]="false"
    else
        SELECTIONS[$pkg]="true"
    fi
}

select_all_tab() {
    local -n _items="TAB_ITEMS_${CURRENT_TAB}"
    local pkg
    for pkg in "${_items[@]}"; do
        if [[ -z "${INSTALLED_PKGS[$pkg]:-}" ]]; then
            SELECTIONS["$pkg"]="true"
        fi
    done
}

deselect_all_tab() {
    local -n _items="TAB_ITEMS_${CURRENT_TAB}"
    local pkg
    for pkg in "${_items[@]}"; do
        SELECTIONS["$pkg"]="false"
    done
}

toggle_all_tab() {
    local -n _items="TAB_ITEMS_${CURRENT_TAB}"
    local pkg
    for pkg in "${_items[@]}"; do
        if [[ "${SELECTIONS[$pkg]:-false}" == "true" ]]; then
            SELECTIONS[$pkg]="false"
        else
            SELECTIONS[$pkg]="true"
        fi
    done
}

count_total_selected() {
    local -i count=0
    local key
    for key in "${!SELECTIONS[@]}"; do
        if [[ "${SELECTIONS[$key]}" == "true" ]]; then
            count=$(( count + 1 ))
        fi
    done
    REPLY="$count"
}

# --- UI Rendering Engine ---

compute_scroll_window() {
    local -i count=$1
    if (( count == 0 )); then
        SELECTED_ROW=0; SCROLL_OFFSET=0
        _vis_start=0; _vis_end=0
        return 0
    fi

    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= count )); then SELECTED_ROW=$(( count - 1 )); fi

    if (( SELECTED_ROW < SCROLL_OFFSET )); then
        SCROLL_OFFSET=$SELECTED_ROW
    elif (( SELECTED_ROW >= SCROLL_OFFSET + MAX_DISPLAY_ROWS )); then
        SCROLL_OFFSET=$(( SELECTED_ROW - MAX_DISPLAY_ROWS + 1 ))
    fi

    local -i max_scroll=$(( count - MAX_DISPLAY_ROWS ))
    if (( max_scroll < 0 )); then max_scroll=0; fi
    if (( SCROLL_OFFSET > max_scroll )); then SCROLL_OFFSET=$max_scroll; fi
    if (( SCROLL_OFFSET < 0 )); then SCROLL_OFFSET=0; fi

    _vis_start=$SCROLL_OFFSET
    _vis_end=$(( SCROLL_OFFSET + MAX_DISPLAY_ROWS ))
    if (( _vis_end > count )); then _vis_end=$count; fi
    return 0
}

render_scroll_indicator() {
    local -n _rsi_buf=$1
    local position="$2"
    local -i count=$3 boundary=$4

    if [[ "$position" == "above" ]]; then
        if (( SCROLL_OFFSET > 0 )); then
            _rsi_buf+="${C_GREY}    ▲ (more above)${CLR_EOL}${C_RESET}"$'\n'
        else
            _rsi_buf+="${CLR_EOL}"$'\n'
        fi
    else
        # "below"
        if (( count > MAX_DISPLAY_ROWS )); then
            local position_info="[$(( SELECTED_ROW + 1 ))/${count}]"
            if (( boundary < count )); then
                _rsi_buf+="${C_GREY}    ▼ (more below) ${position_info}${CLR_EOL}${C_RESET}"$'\n'
            else
                _rsi_buf+="${C_GREY}                   ${position_info}${CLR_EOL}${C_RESET}"$'\n'
            fi
        else
            _rsi_buf+="${CLR_EOL}"$'\n'
        fi
    fi
}

render_item_list() {
    local -n _ril_buf=$1
    local -n _ril_items=$2
    local -i _ril_vs=$3 _ril_ve=$4

    local -i ri
    local item selected desc padded_item check_mark

    for (( ri = _ril_vs; ri < _ril_ve; ri++ )); do
        item="${_ril_items[ri]}"
        selected="${SELECTIONS[$item]:-false}"
        desc="${DESCRIPTIONS[$item]:-}"

        if [[ "$selected" == "true" ]]; then
            if [[ -n "${INSTALLED_PKGS[$item]:-}" ]]; then
                check_mark="${C_CYAN}[↻]${C_RESET}"
            else
                check_mark="${C_GREEN}[]${C_RESET}"
            fi
        elif [[ -n "${INSTALLED_PKGS[$item]:-}" ]]; then
            check_mark="${C_GREEN}[✓]${C_RESET}"
        else
            check_mark="${C_GREY}[ ]${C_RESET}"
        fi

        # Truncate description if too long
        local -i max_desc_len=$(( BOX_INNER_WIDTH - ITEM_PADDING - 7 ))
        if (( ${#desc} > max_desc_len )); then
            desc="${desc:0:$(( max_desc_len - 1 ))}…"
        fi

        # Pad item name
        local -i max_item_len=$(( ITEM_PADDING - 1 ))
        if (( ${#item} > ITEM_PADDING )); then
            printf -v padded_item "%-${max_item_len}s…" "${item:0:max_item_len}"
        else
            printf -v padded_item "%-${ITEM_PADDING}s" "$item"
        fi

        if (( ri == SELECTED_ROW )); then
            _ril_buf+="${C_CYAN} ➤ ${check_mark} ${C_INVERSE}${padded_item}${C_RESET} ${C_DIM}${desc}${CLR_EOL}"$'\n'
        else
            _ril_buf+="    ${check_mark} ${padded_item} ${C_DIM}${desc}${CLR_EOL}"$'\n'
        fi
    done

    # Fill empty rows
    local -i rows_rendered=$(( _ril_ve - _ril_vs ))
    for (( ri = rows_rendered; ri < MAX_DISPLAY_ROWS; ri++ )); do
        _ril_buf+="${CLR_EOL}"$'\n'
    done
}

draw_ui() {
    if ! terminal_size_ok; then
        draw_small_terminal_notice
        return 0
    fi

    local buf="" pad_buf=""
    local -i i current_col=3 zone_start count
    local -i left_pad right_pad vis_len
    local -i _vis_start _vis_end

    buf+="${CURSOR_HOME}"
    buf+="${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'

    count_total_selected
    local sel_count="$REPLY"
    local status_txt="Selected: ${sel_count} (Tab $(( CURRENT_TAB + 1 ))/${TAB_COUNT})"
    
    strip_ansi "$APP_TITLE"; local -i t_len=${#REPLY}
    strip_ansi "$status_txt"; local -i s_len=${#REPLY}
    
    vis_len=$(( t_len + s_len + 3 ))
    left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 ))
    if (( left_pad < 0 )); then left_pad=0; fi
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad ))
    if (( right_pad < 0 )); then right_pad=0; fi

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_WHITE}${APP_TITLE}   ${C_GREEN}${status_txt}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'

    # --- Scrollable Tab Rendering (Sliding Window Engine) ---
    
    if (( TAB_SCROLL_START > CURRENT_TAB )); then
        TAB_SCROLL_START=$CURRENT_TAB
    fi
    if (( TAB_SCROLL_START < 0 )); then
        TAB_SCROLL_START=0
    fi

    local tab_line name display_name
    local -i max_tab_width=$(( BOX_INNER_WIDTH - 6 ))
    local -i total_tab_width=0
    for name in "${TABS[@]}"; do
        total_tab_width=$(( total_tab_width + ${#name} + 4 ))
    done
    total_tab_width=$(( total_tab_width - 2 ))
    if (( total_tab_width <= BOX_INNER_WIDTH - 2 )); then
        TAB_SCROLL_START=0
        max_tab_width=$BOX_INNER_WIDTH
    fi
    
    LEFT_ARROW_ZONE=""
    RIGHT_ARROW_ZONE=""
    
    while true; do
        tab_line="${C_MAGENTA}│ "
        current_col=3
        TAB_ZONES=()
        local -i used_len=0
        
        # Left Arrow
        if (( TAB_SCROLL_START > 0 )); then
            tab_line+="${C_YELLOW}«${C_RESET} "
            LEFT_ARROW_ZONE="2:$(( current_col + 1 ))" 
            used_len=$(( used_len + 2 ))
            current_col=$(( current_col + 2 ))
        else
            tab_line+="  "
            used_len=$(( used_len + 2 ))
            current_col=$(( current_col + 2 ))
        fi

        for (( i = TAB_SCROLL_START; i < TAB_COUNT; i++ )); do
            name="${TABS[i]}"
            display_name="$name"
            local -i t_len=${#name}
            local -i is_last=0
            if (( i == TAB_COUNT - 1 )); then is_last=1; fi

            local -i chunk_len=$(( t_len + 2 ))
            if (( ! is_last )); then chunk_len=$(( chunk_len + 2 )); fi
            
            local -i reserve=0
            if (( ! is_last )); then reserve=2; fi
            
            if (( used_len + chunk_len + reserve > max_tab_width )); then
                if (( i < CURRENT_TAB || (i == CURRENT_TAB && TAB_SCROLL_START < CURRENT_TAB) )); then
                    TAB_SCROLL_START=$(( TAB_SCROLL_START + 1 ))
                    continue 2
                fi
                
                # Right Arrow
                tab_line+="${C_YELLOW}» ${C_RESET}"
                RIGHT_ARROW_ZONE="$current_col:$(( BOX_INNER_WIDTH ))"
                used_len=$(( used_len + 2 ))
                break
            fi

            zone_start=$current_col
            if (( i == CURRENT_TAB )); then
                if (( is_last )); then
                    tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}"
                else
                    tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}${C_MAGENTA}│ "
                fi
            else
                if (( is_last )); then
                    tab_line+="${C_GREY} ${display_name} ${C_RESET}"
                else
                    tab_line+="${C_GREY} ${display_name} ${C_MAGENTA}│ "
                fi
            fi
            
            TAB_ZONES+=("${zone_start}:$(( zone_start + t_len + 1 ))")
            used_len=$(( used_len + chunk_len ))
            current_col=$(( current_col + chunk_len ))
        done
        
        # Center the tab group if all fit cleanly without scrolling
        if (( TAB_SCROLL_START == 0 )) && [[ -z "$RIGHT_ARROW_ZONE" ]]; then
            local -i tab_content_width=$(( used_len - 2 )) tab_shift
            left_pad=$(( (BOX_INNER_WIDTH - tab_content_width) / 2 ))
            tab_shift=$(( left_pad - 3 ))
            local tab_prefix="${C_MAGENTA}│   "
            printf -v pad_buf '%*s' "$left_pad" ''
            tab_line="${C_MAGENTA}│${pad_buf}${tab_line:${#tab_prefix}}"
            for (( i = 0; i < ${#TAB_ZONES[@]}; i++ )); do
                TAB_ZONES[i]="$(( ${TAB_ZONES[i]%%:*} + tab_shift )):$(( ${TAB_ZONES[i]##*:} + tab_shift ))"
            done
            used_len=$(( left_pad + tab_content_width - 1 ))
        fi

        local -i pad=$(( BOX_INNER_WIDTH - used_len - 1 ))
        if (( pad > 0 )); then
            printf -v pad_buf '%*s' "$pad" ''
            tab_line+="$pad_buf"
        fi
        
        tab_line+="${C_MAGENTA}│${C_RESET}"
        break
    done

    buf+="${tab_line}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'

    local items_var="TAB_ITEMS_${CURRENT_TAB}"
    local -n _draw_items_ref="$items_var"
    count=${#_draw_items_ref[@]}

    compute_scroll_window "$count"
    
    # Render Indicators and List
    render_scroll_indicator buf "above" "$count" "$_vis_start"
    render_item_list buf _draw_items_ref "$_vis_start" "$_vis_end"
    render_scroll_indicator buf "below" "$count" "$_vis_end"

    buf+=$'\n'"${C_CYAN} [Tab/h/l] Tabs  [Space] Toggle  [a] All  [d] Deselect  [Enter] Install  [q/Esc] Quit${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_GREY} ${APP_VERSION} - j/k/Arrows: navigate | Page: Ctrl+U/Ctrl+D | Mouse enabled${C_RESET}${CLR_EOL}${CLR_EOS}"
    printf '%s' "$buf"
}

# --- Input Handling Engine ---

navigate() {
    local -i dir=$1
    local -n _nav_items_ref="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#_nav_items_ref[@]}
    if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( (SELECTED_ROW + dir + count) % count ))
}

navigate_page() {
    local -i dir=$1
    local -n _navp_items_ref="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#_navp_items_ref[@]}
    if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( SELECTED_ROW + dir * MAX_DISPLAY_ROWS ))
    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= count )); then SELECTED_ROW=$(( count - 1 )); fi
}

navigate_end() {
    local -i target=$1
    local -n _nave_items_ref="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#_nave_items_ref[@]}
    if (( count == 0 )); then return 0; fi
    if (( target == 0 )); then SELECTED_ROW=0; else SELECTED_ROW=$(( count - 1 )); fi
}

switch_tab() {
    local -i dir=${1:-1}
    if (( TAB_COUNT == 0 )); then return 0; fi
    TAB_SAVED_ROW[CURRENT_TAB]=$SELECTED_ROW
    TAB_SAVED_SCROLL[CURRENT_TAB]=$SCROLL_OFFSET
    CURRENT_TAB=$(( (CURRENT_TAB + dir + TAB_COUNT) % TAB_COUNT ))
    SELECTED_ROW=${TAB_SAVED_ROW[CURRENT_TAB]:-0}
    SCROLL_OFFSET=${TAB_SAVED_SCROLL[CURRENT_TAB]:-0}
}

set_tab() {
    local -i idx=$1
    if (( idx != CURRENT_TAB && idx >= 0 && idx < TAB_COUNT )); then
        TAB_SAVED_ROW[CURRENT_TAB]=$SELECTED_ROW
        TAB_SAVED_SCROLL[CURRENT_TAB]=$SCROLL_OFFSET
        CURRENT_TAB=$idx
        SELECTED_ROW=${TAB_SAVED_ROW[CURRENT_TAB]:-0}
        SCROLL_OFFSET=${TAB_SAVED_SCROLL[CURRENT_TAB]:-0}
    fi
}

toggle_current() {
    local -n _tog_items_ref="TAB_ITEMS_${CURRENT_TAB}"
    if (( ${#_tog_items_ref[@]} == 0 )); then return 0; fi
    local item="${_tog_items_ref[SELECTED_ROW]}"
    toggle_selection "$item"
    navigate 1
}

classify_mouse_event() {
    local -i code=$1 x=$2 y=$3
    local terminator="$4"
    REPLY=$code
    if [[ "$terminator" == "m" ]]; then
        local -i pending=$MOUSE_CLICK_PENDING
        MOUSE_CLICK_PENDING=0
        if (( code == 0 && pending && x == MOUSE_PRESS_X && y == MOUSE_PRESS_Y )); then
            REPLY=0
            return 0
        fi
        return 1
    fi
    case $code in
        0)
            MOUSE_CLICK_PENDING=1
            MOUSE_PRESS_X=$x
            MOUSE_PRESS_Y=$y
            REPLY=32
            ;;
        32) MOUSE_CLICK_PENDING=0 ;;
        2|64|65) MOUSE_CLICK_PENDING=0 ;;
        *) MOUSE_CLICK_PENDING=0; return 1 ;;
    esac
    return 0
}

handle_mouse() {
    local input="$1"
    local -i button x y i start end
    local zone 

    local body="${input#'[<'}"
    if [[ "$body" == "$input" ]]; then return 0; fi
    local terminator="${body: -1}"
    if [[ "$terminator" != "M" && "$terminator" != "m" ]]; then return 0; fi
    body="${body%[Mm]}"

    local field1 field2 field3
    IFS=';' read -r field1 field2 field3 <<< "$body"
    if [[ ! "$field1" =~ ^[0-9]+$ || ! "$field2" =~ ^[0-9]+$ || ! "$field3" =~ ^[0-9]+$ ]]; then return 0; fi
    if (( ${#field1} > 3 || ${#field2} > 6 || ${#field3} > 6 )); then return 0; fi

    button=$(( 10#$field1 ))
    x=$(( 10#$field2 ))
    y=$(( 10#$field3 ))

    if (( x < 1 || x > TERM_COLS || y < 1 || y > TERM_ROWS )); then
        MOUSE_CLICK_PENDING=0
        return 0
    fi

    classify_mouse_event "$button" "$x" "$y" "$terminator" || return 0
    button=$REPLY

    # Mouse Wheel handling
    if (( button == 64 )); then
        if (( y == TAB_ROW )); then switch_tab -1; else navigate -1; fi
        return 0
    fi
    if (( button == 65 )); then
        if (( y == TAB_ROW )); then switch_tab 1; else navigate 1; fi
        return 0
    fi

    # Only process Left Click release (0) or Right Click (2)
    if (( button != 0 && button != 2 )); then return 0; fi

    # Click on Tab Row
    if (( y == TAB_ROW )); then
        if [[ -n "$LEFT_ARROW_ZONE" ]]; then
            start="${LEFT_ARROW_ZONE%%:*}"
            end="${LEFT_ARROW_ZONE##*:}"
            if (( x >= start && x <= end )); then switch_tab -1; return 0; fi
        fi

        if [[ -n "$RIGHT_ARROW_ZONE" ]]; then
            start="${RIGHT_ARROW_ZONE%%:*}"
            end="${RIGHT_ARROW_ZONE##*:}"
            if (( x >= start && x <= end )); then switch_tab 1; return 0; fi
        fi

        for (( i = 0; i < ${#TAB_ZONES[@]}; i++ )); do
            if [[ -z "${TAB_ZONES[i]:-}" ]]; then continue; fi
            zone="${TAB_ZONES[i]}"
            start="${zone%%:*}"
            end="${zone##*:}"
            if (( x >= start && x <= end )); then
                set_tab "$(( i + TAB_SCROLL_START ))"
                return 0
            fi
        done
        return 0
    fi

    # Click on Top / Bottom Scroll Indicators
    local -i top_ind_row=$ITEM_START_ROW
    local -i bot_ind_row=$(( ITEM_START_ROW + MAX_DISPLAY_ROWS + 1 ))
    if (( y == top_ind_row )); then
        navigate_page -1
        return 0
    fi
    if (( y == bot_ind_row )); then
        navigate_page 1
        return 0
    fi

    # Click on Items List
    local -i effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start + SCROLL_OFFSET ))
        local -n _mouse_items_ref="TAB_ITEMS_${CURRENT_TAB}"
        local -i count=${#_mouse_items_ref[@]}

        if (( clicked_idx >= 0 && clicked_idx < count )); then
            SELECTED_ROW=$clicked_idx
            if (( button == 0 )); then
                toggle_selection "${_mouse_items_ref[SELECTED_ROW]}"
            fi
        fi
    fi
    return 0
}

read_escape_seq() {
    local -n _esc_out=$1
    _esc_out=""
    local char
    if ! IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char < /dev/tty; then return 1; fi
    _esc_out+="$char"
    if [[ "$char" == '[' || "$char" == 'O' ]]; then
        while (( ${#_esc_out} < MAX_ESCAPE_BYTES )) && IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char < /dev/tty; do
            _esc_out+="$char"
            [[ "$char" == [@-~] ]] && break
        done
    fi
    return 0
}

consume_paste_byte() {
    PASTE_TAIL="${PASTE_TAIL}${1}"
    if (( ${#PASTE_TAIL} > 6 )); then PASTE_TAIL="${PASTE_TAIL: -6}"; fi
    if [[ "$PASTE_TAIL" == $'\e[201~' ]]; then PASTE_ACTIVE=0; PASTE_TAIL=""; fi
    return 0
}

discard_bracketed_paste() {
    local char
    PASTE_ACTIVE=1; PASTE_TAIL=""
    while (( PASTE_ACTIVE )) && IFS= read -rsn1 -t "$READ_LOOP_TIMEOUT" char < /dev/tty; do
        consume_paste_byte "$char"
    done
    return 0
}

handle_key_action() {
    local key="$1"
    case "$key" in
        '[A'|'OA')           navigate -1; return 0 ;;
        '[B'|'OB')           navigate 1; return 0 ;;
        '[C'|'OC')           switch_tab 1; return 0 ;;
        '[D'|'OD')           switch_tab -1; return 0 ;;
        '[Z')                switch_tab -1; return 0 ;;
        '[5~')               navigate_page -1; return 0 ;;
        '[6~')               navigate_page 1; return 0 ;;
        '[H'|'[1~')          navigate_end 0; return 0 ;;
        '[F'|'[4~')          navigate_end 1; return 0 ;;
        '['*'<'*[Mm])        handle_mouse "$key"; return 0 ;;
    esac

    case "$key" in
        k|K)            navigate -1 ;;
        j|J)            navigate 1 ;;
        l|L)            switch_tab 1 ;;
        h|H)            switch_tab -1 ;;
        $'\x15')        navigate_page -1 ;; # Ctrl+U
        $'\x04')        navigate_page 1 ;;  # Ctrl+D
        g)              navigate_end 0 ;;
        G)              navigate_end 1 ;;
        $'\t')          switch_tab 1 ;;
        ' ')            toggle_current ;;
        a|A)            select_all_tab ;;
        d|D)            deselect_all_tab ;;
        i|I)            toggle_all_tab ;;
        ''|$'\n')       DO_INSTALL=1; return 1 ;; # Break loop to install
        ESC|q|Q|$'\x03') DO_INSTALL=0; return 1 ;; # Break loop to exit
    esac
    return 0
}

main_loop() {
    TUI_STARTED=1
    printf '%s%s%s%s%s' "$ALT_SCREEN_ON" "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"
    
    set +e
    trap 'RESIZE_PENDING=1' WINCH CONT
    trap suspend_ui TSTP

    local key escape_seq
    local -i redraw=1 read_status
    update_terminal_size

    while true; do
        if (( RESIZE_PENDING )); then
            RESIZE_PENDING=0
            MOUSE_CLICK_PENDING=0
            update_terminal_size
            redraw=1
        fi

        if (( redraw )); then
            draw_ui
            redraw=0
        fi

        if IFS= read -rsn1 -t "$READ_LOOP_TIMEOUT" key < /dev/tty; then
            if (( RESIZE_PENDING )); then
                RESIZE_PENDING=0
                MOUSE_CLICK_PENDING=0
                update_terminal_size
            fi

            if (( PASTE_ACTIVE )); then
                consume_paste_byte "$key"
                continue
            fi

            if [[ "$key" == $'\x1b' ]]; then
                if read_escape_seq escape_seq; then
                    key="$escape_seq"
                    if [[ "$key" == "" || "$key" == $'\n' ]]; then
                        key=$'\e\n'
                    fi
                else
                    key="ESC"
                fi
            fi

            if [[ "$key" == '[200~' ]]; then
                discard_bracketed_paste
                continue
            fi

            if ! terminal_size_ok; then
                case "$key" in
                    q|Q|$'\x03') DO_INSTALL=0; break ;;
                esac
                continue
            fi

            if ! handle_key_action "$key"; then
                break
            fi
            redraw=1
        else
            read_status=$?
            # Exit loop cleanly if stdin / tty is closed (EOF)
            if (( read_status == 1 )); then
                DO_INSTALL=0
                break
            fi
        fi
    done
}

# --- Installation Logic ---

detect_aur_helper() {
    if command -v paru &>/dev/null; then printf 'paru'; return 0; fi
    if command -v yay &>/dev/null; then printf 'yay'; return 0; fi
    return 1
}

run_installer() {
    local helper="$1"
    local -a targets=()
    local key

    for key in "${!SELECTIONS[@]}"; do
        if [[ "${SELECTIONS[$key]}" == "true" ]]; then
            targets+=("$key")
        fi
    done

    if (( ${#targets[@]} == 0 )); then
        log_info "No packages selected."
        return 0
    fi

    log_info "Checking installation status for ${#targets[@]} package(s)..."

    local -a to_install=()
    mapfile -t to_install < <(pacman -T "${targets[@]}" 2>/dev/null || true)

    if (( ${#to_install[@]} == 0 )); then
        log_info "All selected packages are already installed."
        return 0
    fi

    start_sudo_keepalive || return 1

    log_info "Attempting batch installation of ${#to_install[@]} package(s)..."
    if "$helper" -S --needed --noconfirm -- "${to_install[@]}"; then
        log_info "Batch installation successful."
        stop_sudo_keepalive
        return 0
    fi

    log_err "Batch install failed. Switching to interactive granular mode..."

    local -a remaining=()
    mapfile -t remaining < <(pacman -T "${to_install[@]}" 2>/dev/null || true)
    
    local pkg choice
    for pkg in "${remaining[@]}"; do
        log_info "Processing: $pkg"
        if "$helper" -S --needed --noconfirm -- "$pkg"; then
            log_info "$pkg installed successfully."
        else
            log_err "Failed to install $pkg automatically."
            read -rp "Retry manually? [y/N]: " choice < /dev/tty || choice="n"
            if [[ "${choice,,}" == "y" ]]; then
                "$helper" -S -- "$pkg" || log_err "$pkg failed manual install."
            fi
        fi
    done

    stop_sudo_keepalive
    return 0
}

main() {
    if (( BASH_VERSINFO[0] < 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 3) )); then
        log_err "Bash 5.3+ required (found Bash $BASH_VERSION)."
        exit 1
    fi
    if (( EUID == 0 )); then
        log_err "This script must not be run as root. AUR helpers manage sudo internally."
        exit 1
    fi
    if [[ ! -t 0 || ! -t 1 ]]; then
        log_err "Interactive TTY stdin/stdout required."
        exit 1
    fi
    
    local dep
    for dep in pacman stty sudo; do
        if ! command -v "$dep" &>/dev/null; then
            log_err "Missing required dependency: $dep"
            exit 1
        fi
    done

    local helper
    if ! helper=$(detect_aur_helper); then
        log_err "No AUR helper (paru/yay) found in PATH."
        exit 1
    fi

    parse_data
    
    ORIGINAL_STTY=$(stty -g < /dev/tty 2>/dev/null) || ORIGINAL_STTY=""
    if [[ -z "$ORIGINAL_STTY" ]]; then
        log_err "Failed to read terminal settings from /dev/tty."
        exit 1
    fi
    if ! stty -icanon -echo -ixon min 1 time 0 < /dev/tty 2>/dev/null; then
        log_err "Failed to configure raw terminal mode."
        exit 1
    fi

    main_loop
    
    cleanup
    
    if (( DO_INSTALL == 1 )); then
        run_installer "$helper"
    else
        log_info "Installation cancelled by user."
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
