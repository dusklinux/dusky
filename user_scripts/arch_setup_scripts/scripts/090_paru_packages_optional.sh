#!/usr/bin/env bash
# Dusky Optional Package Installer

set -euo pipefail
shopt -s extglob

# =============================================================================
# ▼ USER CONFIGURATION (EDIT THIS SECTION) ▼
# =============================================================================

declare -r APP_TITLE="Dusky Optional Packages"
declare -r APP_VERSION="v3.0 (Template Engine)"

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
Tools       | xorg-xhost            | to allow unfettered access to xorg root apps, like timeshfit, gparted etc
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
Tools       | avahi                 | Service Discovery using mDNS/DNS-SD (compatible with Bonjour)
Tools       | evince                | Document viewer (PDF, PostScript, XPS, djvu, dvi, tiff, cbr, cbz, cb7, cbt)
Tools       | aria2                 | High-speed download utility (Pair with uget)
Tools       | uget                  | Download Manager GUI (Pair with aria2)
Tools       | libdvdcss             | Portable abstraction library for DVD decryption
Internet    | filezilla             | Fast and reliable FTP/SFTP client
Internet    | zapret2               | Deep Packet Inspection circumvetntion for blocked sites
Internet    | qbittorrent           | Feature-rich BitTorrent client (Qt-based)
Internet    | networkmanager-openvpn| NetworkManager VPN plugin for OpenVPN (with GUI)
Internet    | network-manager-applet| NetworkManager applet, GUI, System Tray
Internet    | vesktop               | Custom Discord client (Vencord + Electron)
Internet    | beeper-v4-bin         | Universal chat app (Matrix bridge)
Productivity| pinta                 | Simple drawing/editing tool (Paint.NET clone)
Productivity| gimp                  | Photoshop alternative for Linux
Productivity| libreoffice-still     | Microsoft Office alternative (Stable)
# Productivity| libreoffice-fresh     | Microsoft Office alternative (latest)
Productivity| calcurse              | Text-based calendar and scheduling application
Productivity| blanket               | Ambient noise player for focus and productivity
Productivity| errands               | Simple to-do list application
Productivity| obsidian              | Markdown-based knowledge base and note taking
Productivity| xournalpp             | Handwriting notetaking software with PDF annotation support
Docs        | arch-wiki-lite        | Compressed Wiki reader (Pair with arch-wiki-docs)
Docs        | arch-wiki-docs        | Arch Wiki data pages (Pair with arch-wiki-lite)
Media       | pear-desktop-bin      | Youtube Music GUI
Media       | noto-fonts-cjk        | Asian fonts
Media       | noto-fonts            | Asian fonts
Media       | noto-fonts-emoji      | Google Noto Color Emoji font
Media       | cantarell-fonts       | Humanist sans serif font
Media       | ttf-bitstream-vera    | Bitstream Vera fonts
Media       | ttf-dejavu            | Font based on Bitstream Vera (wider range of characters)
Media       | ttf-liberation        | Font family metric compatibility with Arial, Times New Roman, and Courier New
Media       | ttf-font-awesome      | Iconic font designed for Bootstrap - woff2 format
Media       | woff2-font-awesome    | Iconic font designed for Bootstrap - woff2 format
Media       | ttf-jetbrains-mono-nerd | Patched font JetBrains Mono from nerd fonts library
Media       | awesome-terminal-fonts| fonts/icons for powerlines
Media       | papirus-folders-git   | folder color themeing for file manager
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
Games       | edex-ui-bin           | Sci-Fi/Tron-inspired terminal emulator
Games       | clidle-bin            | Wordle clone for the command line
Games       | maze-tui              | Visual maze generator and solver
Games       | vitetris              | Classic Tetris clone for the terminal
Games       | ttyper                | Terminal-based typing test and practice
Security    | wdpass                | Unlock Western Digital MyPassport drives
Security    | dislocker             | FUSE driver to read BitLocker partitions
Drivers     | b43-firmware          | Legacy Broadcom B43 wireless firmware
Drivers     | usbmuxd               | Socket daemon to multiplex connections to iOS devices
Drivers     | cuda                  | NVIDIA's parallel computing architecture toolkit
Drivers     | cudnn                 | NVIDIA CUDA Deep Neural Network library
Hardware    | asusctl               | ASUS ROG/TUF control
Hardware    | broadcom-wl-dkms      | Broadcom 802.11 Linux STA wireless driver
Hardware    | macbook12-spi-driver-dkms | Driver for the keyboard, touchpad and touchbar found in newer MacBook (Pro) models
"

# Dimensions & Layout
declare -ri MAX_DISPLAY_ROWS=14
declare -ri BOX_INNER_WIDTH=80
declare -ri ITEM_PADDING=28

declare -ri HEADER_ROWS=4
declare -ri TAB_ROW=3
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

# =============================================================================
# ▲ END OF USER CONFIGURATION ▲
# =============================================================================

# --- Pre-computed Constants ---
declare _h_line_buf
printf -v _h_line_buf '%*s' "$BOX_INNER_WIDTH" ''
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
declare -r MOUSE_ON=$'\033[?1000h\033[?1002h\033[?1006h'
declare -r MOUSE_OFF=$'\033[?1000l\033[?1002l\033[?1006l'

# Input Timeout (Critical for keybinds)
declare -r ESC_READ_TIMEOUT=0.10

# --- State Management ---
declare -i SELECTED_ROW=0
declare -i CURRENT_TAB=0
declare -i SCROLL_OFFSET=0
declare -a TABS=()
declare -i TAB_COUNT=0
declare -a TAB_ZONES=()
declare -i TAB_SCROLL_START=0
declare ORIGINAL_STTY=""

# Click Zones for Arrows
declare LEFT_ARROW_ZONE=""
declare RIGHT_ARROW_ZONE=""

# Selection State
declare -A SELECTIONS=()
declare -A DESCRIPTIONS=()

# Execution State
declare -i DO_INSTALL=0

# --- System Helpers ---

log_err() { printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2; }
log_info() { printf '%s[INFO]%s %s\n' "$C_CYAN" "$C_RESET" "$1" >&2; }

cleanup() {
    printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
    if [[ -n "${ORIGINAL_STTY:-}" ]]; then
        stty "$ORIGINAL_STTY" 2>/dev/null || :
    fi
    printf '\n' 2>/dev/null || :
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- String Helpers ---

strip_ansi() {
    local v="$1"
    v="${v//$'\033'\[*([0-9;:?<=>])@([@A-Z\[\\\]^_\`a-z\{|\}~])/}"
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
    local line category pkg desc
    local -A category_map
    local -i cat_idx

    while IFS='|' read -r category pkg desc; do
        trim "$category"; category="$REPLY"
        trim "$pkg"; pkg="$REPLY"
        trim "$desc"; desc="$REPLY"

        [[ -z "$category" || "$category" == \#* ]] && continue
        [[ -z "$pkg" ]] && continue

        if [[ -z "${category_map[$category]:-}" ]]; then
            TABS+=("$category")
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

count_total_selected() {
    local count=0
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
        return
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

    _vis_start=$SCROLL_OFFSET
    _vis_end=$(( SCROLL_OFFSET + MAX_DISPLAY_ROWS ))
    if (( _vis_end > count )); then _vis_end=$count; fi
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
            check_mark="${C_GREEN}[x]${C_RESET}"
        else
            check_mark="${C_GREY}[ ]${C_RESET}"
        fi

        # Truncate description if too long (Ellipsis logic from Template)
        local max_desc_len=$(( BOX_INNER_WIDTH - ITEM_PADDING - 7 ))
        if (( ${#desc} > max_desc_len )); then
            desc="${desc:0:$((max_desc_len-1))}…"
        fi

        # Pad item name (Ellipsis logic applied to item name as well for safety)
        local max_item_len=$(( ITEM_PADDING - 1 ))
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
    local buf="" pad_buf=""
    local -i i current_col=3 zone_start len count pad_needed
    local -i left_pad right_pad vis_len
    local -i _vis_start _vis_end

    buf+="${CURSOR_HOME}"
    buf+="${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'

    count_total_selected
    local sel_count="$REPLY"
    local status_txt="Selected: ${sel_count}"
    
    strip_ansi "$APP_TITLE"; local -i t_len=${#REPLY}
    strip_ansi "$status_txt"; local -i s_len=${#REPLY}
    
    vis_len=$(( t_len + s_len + 3 ))
    left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad ))

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_WHITE}${APP_TITLE}   ${C_GREEN}${status_txt}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'

    # --- Scrollable Tab Rendering (Sliding Window from Template) ---
    
    if (( TAB_SCROLL_START > CURRENT_TAB )); then
        TAB_SCROLL_START=$CURRENT_TAB
    fi

    local tab_line
    # Use config width minus borders (2) and margins (4 approx)
    local -i max_tab_width=$(( BOX_INNER_WIDTH - 6 ))
    
    # Reset arrow zones
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
            LEFT_ARROW_ZONE="$current_col:$((current_col+1))" 
            used_len=$(( used_len + 2 ))
            current_col=$(( current_col + 2 ))
        else
            tab_line+="  "
            used_len=$(( used_len + 2 ))
            current_col=$(( current_col + 2 ))
        fi

        for (( i = TAB_SCROLL_START; i < TAB_COUNT; i++ )); do
            local name="${TABS[i]}"
            local t_len=${#name}
            # Visual chars: Space + Name + Space + Pipe + Space = NameLen + 4
            local chunk_len=$(( t_len + 4 ))
            
            local reserve=0
            if (( i < TAB_COUNT - 1 )); then reserve=2; fi
            
            if (( used_len + chunk_len + reserve > max_tab_width )); then
                if (( i <= CURRENT_TAB )); then
                    TAB_SCROLL_START=$(( TAB_SCROLL_START + 1 ))
                    continue 2
                fi
                
                # Right Arrow
                tab_line+="${C_YELLOW}» ${C_RESET}"
                RIGHT_ARROW_ZONE="$current_col:$((current_col+1))"
                used_len=$(( used_len + 2 ))
                break
            fi

            zone_start=$current_col
            if (( i == CURRENT_TAB )); then
                tab_line+="${C_CYAN}${C_INVERSE} ${name} ${C_RESET}${C_MAGENTA}│ "
            else
                tab_line+="${C_GREY} ${name} ${C_MAGENTA}│ "
            fi
            
            TAB_ZONES+=("${zone_start}:$(( zone_start + t_len + 1 ))")
            used_len=$(( used_len + chunk_len ))
            current_col=$(( current_col + chunk_len ))
        done
        
        local pad=$(( BOX_INNER_WIDTH - used_len - 1 ))
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

    buf+=$'\n'"${C_CYAN} [Tab] Next Tab  [Sh+Tab] Prev Tab  [Space] Toggle  [Enter] Install  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_GREY} ${APP_VERSION} - Use j/k/Arrows to navigate${C_RESET}${CLR_EOL}${CLR_EOS}"
    printf '%s' "$buf"
}

# --- Input Handling (Template Robustness) ---

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
    CURRENT_TAB=$(( (CURRENT_TAB + dir + TAB_COUNT) % TAB_COUNT ))
    SELECTED_ROW=0
    SCROLL_OFFSET=0
}

set_tab() {
    local -i idx=$1
    if (( idx != CURRENT_TAB && idx >= 0 && idx < TAB_COUNT )); then
        CURRENT_TAB=$idx
        SELECTED_ROW=0
        SCROLL_OFFSET=0
    fi
}

toggle_current() {
    local -n _tog_items_ref="TAB_ITEMS_${CURRENT_TAB}"
    if (( ${#_tog_items_ref[@]} == 0 )); then return 0; fi
    local item="${_tog_items_ref[SELECTED_ROW]}"
    toggle_selection "$item"
    navigate 1
}

handle_mouse() {
    local input="$1"
    local -i button x y i start end
    local zone 

    local body="${input#'[<'}"
    if [[ "$body" == "$input" ]]; then return 0; fi
    local terminator="${body: -1}"
    body="${body%[Mm]}"
    IFS=';' read -r button x y <<< "$body"
    
    if [[ "$terminator" != "M" && "$terminator" != "m" ]]; then return 0; fi

    if (( button == 64 )); then navigate -1; return 0; fi
    if (( button == 65 )); then navigate 1; return 0; fi
    if [[ "$terminator" != "M" ]]; then return 0; fi

    if (( y == TAB_ROW )); then
        # Check Left Arrow
        if [[ -n "$LEFT_ARROW_ZONE" ]]; then
            start="${LEFT_ARROW_ZONE%%:*}"
            end="${LEFT_ARROW_ZONE##*:}"
            if (( x >= start && x <= end )); then switch_tab -1; return 0; fi
        fi

        # Check Right Arrow
        if [[ -n "$RIGHT_ARROW_ZONE" ]]; then
            start="${RIGHT_ARROW_ZONE%%:*}"
            end="${RIGHT_ARROW_ZONE##*:}"
            if (( x >= start && x <= end )); then switch_tab 1; return 0; fi
        fi

        # Check Tabs (Corrected offset logic from template)
        for (( i = 0; i < TAB_COUNT; i++ )); do
            if [[ -z "${TAB_ZONES[i]:-}" ]]; then continue; fi
            zone="${TAB_ZONES[i]}"
            start="${zone%%:*}"
            end="${zone##*:}"
            # Check click against visible zones
            if (( x >= start && x <= end )); then set_tab "$(( i + TAB_SCROLL_START ))"; return 0; fi
        done
    fi

    local -i effective_start=$(( ITEM_START_ROW + 1 )) # +1 for top scroll indicator
    
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
}

read_escape_seq() {
    local -n _esc_out=$1
    _esc_out=""
    local char
    if ! IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; then return 1; fi
    _esc_out+="$char"
    if [[ "$char" == '[' || "$char" == 'O' ]]; then
        while IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; do
            _esc_out+="$char"
            if [[ "$char" =~ [a-zA-Z~] ]]; then break; fi
        done
    fi
    return 0
}

handle_key_action() {
    local key="$1"
    case "$key" in
        '[A'|'OA')           navigate -1; return ;;
        '[B'|'OB')           navigate 1; return ;;
        '[C'|'OC')           switch_tab 1; return ;;
        '[D'|'OD')           switch_tab -1; return ;;
        '[Z')                switch_tab -1; return ;;
        '[5~')               navigate_page -1; return ;;
        '[6~')               navigate_page 1; return ;;
        '[H'|'[1~')          navigate_end 0; return ;;
        '[F'|'[4~')          navigate_end 1; return ;;
        '['*'<'*[Mm])        handle_mouse "$key"; return ;;
    esac

    case "$key" in
        k|K)            navigate -1 ;;
        j|J)            navigate 1 ;;
        l|L)            switch_tab 1 ;;
        h|H)            switch_tab -1 ;;
        g)              navigate_end 0 ;;
        G)              navigate_end 1 ;;
        $'\t')          switch_tab 1 ;;
        ' ')            toggle_current ;;
        ''|$'\n')       DO_INSTALL=1; return 1 ;; # Break loop to install
        q|Q|$'\x03')    DO_INSTALL=0; return 1 ;; # Break loop to exit
    esac
    return 0
}

main_loop() {
    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"
    
    local key escape_seq
    while true; do
        draw_ui
        IFS= read -rsn1 key || break

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

        if ! handle_key_action "$key"; then
            break
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

    printf '%s' "$CURSOR_SHOW"

    log_info "Checking installation status for ${#targets[@]} packages..."

    local -a to_install
    if ! mapfile -t to_install < <(pacman -T "${targets[@]}" 2>/dev/null || true); then
        log_err "Failed to check package status."
        return 1
    fi

    if (( ${#to_install[@]} == 0 )); then
        log_info "All selected packages are already installed."
        return 0
    fi

    log_info "Attempting Batch Installation..."
    if "$helper" -S --needed --noconfirm "${to_install[@]}"; then
        log_info "Batch installation successful."
        return 0
    fi

    log_err "Batch install failed. Switching to Interactive Granular Mode."

    local -a remaining
    mapfile -t remaining < <(pacman -T "${to_install[@]}" 2>/dev/null || true)
    
    local pkg
    for pkg in "${remaining[@]}"; do
        log_info "Processing: $pkg"
        if "$helper" -S --needed --noconfirm "$pkg"; then
            log_info "$pkg installed."
        else
            log_err "Failed to install $pkg automatically."
            read -rp "Retry manually? [y/N]: " choice
            if [[ "${choice,,}" == "y" ]]; then
                "$helper" -S "$pkg" || log_err "$pkg failed manual install."
            fi
        fi
    done
}

main() {
    if (( BASH_VERSINFO[0] < 5 )); then log_err "Bash 5.0+ required"; exit 1; fi
    if [[ ! -t 0 ]]; then log_err "TTY required"; exit 1; fi
    
    local helper
    if ! helper=$(detect_aur_helper); then
        log_err "No AUR helper (paru/yay) found."
        exit 1
    fi

    parse_data
    
    ORIGINAL_STTY=$(stty -g 2>/dev/null) || ORIGINAL_STTY=""
    stty -icanon -echo min 1 time 0 2>/dev/null

    main_loop
    
    cleanup
    
    if (( DO_INSTALL == 1 )); then
        run_installer "$helper"
    else
        log_info "Installation cancelled by user."
    fi
}

main "$@"
