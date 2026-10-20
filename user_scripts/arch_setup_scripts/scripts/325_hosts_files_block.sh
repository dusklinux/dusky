#!/usr/bin/env bash
#d: Block distracting websites via /etc/hosts

set -euo pipefail
shopt -s extglob

# =============================================================================
# ▼ CONFIGURATION ▼
# =============================================================================

declare HOSTS_FILE="/etc/hosts"
declare -r BACKUP_FILE="/etc/hosts.backup.focusforge"
declare -r APP_TITLE="Dusky Site Blocker"
declare -r APP_VERSION="v2.5.0"

# Dimensions & Layout
declare -ri MAX_DISPLAY_ROWS=14
declare -ri BOX_INNER_WIDTH=76
declare -ri ITEM_PADDING=42

declare -ri HEADER_ROWS=4
declare -ri TAB_ROW=3
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

# Categories (Tabs)
declare -ra TABS=("Social" "Video" "News" "Gambling" "Custom")

# Domain Database (Category Index | Domain Name)
declare -ra DOMAIN_DB=(
    # 0: Social
    "0|facebook.com" "0|twitter.com" "0|x.com" "0|instagram.com"
    "0|reddit.com" "0|linkedin.com" "0|tiktok.com" "0|pinterest.com"
    "0|tumblr.com" "0|snapchat.com" "0|threads.net" "0|discord.com"

    # 1: Video / Stream
    "1|youtube.com" "1|twitch.tv" "1|netflix.com" "1|hulu.com"
    "1|disneyplus.com" "1|primevideo.com" "1|kick.com" "1|vimeo.com"

    # 2: News / Tabloids
    "2|cnn.com" "2|foxnews.com" "2|bbc.com" "2|nytimes.com"
    "2|buzzfeed.com" "2|dailymail.co.uk" "2|tmz.com" "2|forbes.com"

    # 3: Gambling / Trading
    "3|bet365.com" "3|draftkings.com" "3|pokerstars.com" "3|roobet.com"
    "3|binance.com" "3|coinbase.com" "3|robinhood.com" "3|tradingview.com"
)

# Special Button Constants
declare -r ADD_BUTTON_LABEL="[+] Add New Domain"
declare -r IMPORT_BUTTON_LABEL="[⬇] Import Host File"
declare -r CLEAR_BUTTON_LABEL="[X] Clear All Blocks"

# =============================================================================
# ▼ CORE ENGINE ▼
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

declare -r ESC_READ_TIMEOUT=0.08
declare -r READ_LOOP_TIMEOUT=0.25
declare -ri MAX_ESCAPE_BYTES=64
declare -ri MIN_TERM_COLS=$(( BOX_INNER_WIDTH + 2 ))
declare -ri MIN_TERM_ROWS=$(( HEADER_ROWS + MAX_DISPLAY_ROWS + 6 ))
declare -i TERM_ROWS=0 TERM_COLS=0 RESIZE_PENDING=0 TUI_STARTED=0
declare -i PASTE_ACTIVE=0 MOUSE_CLICK_PENDING=0 MOUSE_PRESS_X=0 MOUSE_PRESS_Y=0
declare MOUSE_PRESS_CONTEXT="" PASTE_TAIL=""
declare -a TAB_SAVED_ROW=() TAB_SAVED_SCROLL=()

# --- State Management ---
declare -i SELECTED_ROW=0
declare -i CURRENT_TAB=0
declare -i SCROLL_OFFSET=0
declare -ri TAB_COUNT=${#TABS[@]}
declare -a TAB_ZONES=()
declare -i TAB_SCROLL_START=0

# View State
declare -i CURRENT_VIEW=0      # 0=Main List, 1=Input Modal
declare INPUT_BUFFER=""        # For custom domain input

# Temp file global
declare _TMPFILE=""
declare ORIGINAL_STTY=""
declare STATUS_MESSAGE=""
declare IMPORT_HOME="${HOME}"

# --- Click Zones ---
declare LEFT_ARROW_ZONE=""
declare RIGHT_ARROW_ZONE=""

# --- Data Structures ---
for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do
    declare -ga "TAB_ITEMS_${_ti}=()"
done
unset _ti

declare -A IS_BLOCKED=()

# Pre-built lookup: domain -> which standard tab it belongs to
declare -A KNOWN_DOMAIN_TAB=()

# --- System Helpers ---

log_err() {
    printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2
}

cleanup() {
    if [[ -n "$ORIGINAL_STTY" ]]; then
        printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
        if (( TUI_STARTED )); then printf '%s' "$ALT_SCREEN_OFF" 2>/dev/null || :; fi
        stty "$ORIGINAL_STTY" 2>/dev/null || :
    fi
    if [[ -n "$_TMPFILE" ]]; then
        rm -f -- "$_TMPFILE" 2>/dev/null || :
    fi
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 131' QUIT
trap 'exit 129' HUP

update_terminal_size() {
    local size
    size=$(stty size) || return 1
    TERM_ROWS=${size%% *}
    TERM_COLS=${size##* }
}

terminal_size_ok() {
    (( TERM_COLS >= MIN_TERM_COLS && TERM_ROWS >= MIN_TERM_ROWS ))
}

draw_small_terminal_notice() {
    printf '%s%s%sTerminal too small%s\n' "$CURSOR_HOME" "$CLR_SCREEN" "$C_RED" "$C_RESET"
    printf 'Need %d columns × %d rows; current size is %d × %d.\n' \
        "$MIN_TERM_COLS" "$MIN_TERM_ROWS" "$TERM_COLS" "$TERM_ROWS"
    printf '%sResize to continue, or press q to quit.%s%s' "$C_CYAN" "$C_RESET" "$CLR_EOS"
}

strip_ansi() {
    local v="$1"
    v="${v//$'\033'\[*([0-9;:?<=>])@([@A-Z\[\\\]^_\`a-z\{|\}~])/}"
    REPLY="$v"
}

# =============================================================================
# ▼ LOGIC ENGINE ▼
# =============================================================================

init_db() {
    local entry cat domain
    for entry in "${DOMAIN_DB[@]}"; do
        IFS='|' read -r cat domain <<< "$entry"
        local -n _tab_ref="TAB_ITEMS_${cat}"
        _tab_ref+=("$domain")
        KNOWN_DOMAIN_TAB["$domain"]="$cat"
    done

    # Initialize Custom Tab with buttons pinned at top
    TAB_ITEMS_4=("$ADD_BUTTON_LABEL" "$IMPORT_BUTTON_LABEL" "$CLEAR_BUTTON_LABEL")
}

# Canonicalize DNS input; NSS matches dotted and undotted hosts aliases separately.
# Keep local host aliases out of imports and the block list.
declare -r HOSTS_AWK='
function normalize(d) { d = tolower(d); sub(/\r$/, "", d); sub(/\.$/, "", d); return d }
function valid(d,    n, labels, i) {
    if (length(d) > 253 || d !~ /\./ || d ~ /^[0-9.]+$/ ||
        d == "localhost.localdomain" || d ~ /^ip6-/) return 0
    n = split(d, labels, ".")
    for (i = 1; i <= n; i++)
        if (length(labels[i]) > 63 ||
            labels[i] !~ /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/) return 0
    return 1
}
function sink(ip) { return ip == "0.0.0.0" || ip == "::" }
'

refresh_state() {
    local output status domain
    # Stream known/listed names over stdin: large imports must not hit ARG_MAX.
    # Capture errors explicitly: unreadable input must never mean "all allowed".
    output=$(printf '%s\n' "${!KNOWN_DOMAIN_TAB[@]}" "${TAB_ITEMS_4[@]:3}" |
        LC_ALL=C gawk -- "$HOSTS_AWK"'
        ARGIND == 1 { listed[$0] = 1; next }
        { sub(/#.*/, "") }
        sink($1) {
            for (i = 2; i <= NF; i++) {
                # A dotted-only alias does not block the undotted name in NSS.
                if ($i ~ /\.\r?$/) continue
                d = normalize($i)
                if (valid(d) && !seen[d]++) ordered[++count] = d
            }
        }
        END {
            for (i = 1; i <= count; i++) {
                d = ordered[i]
                base = d
                sub(/^www\./, "", base)
                hidden = d != base && (base in seen)
                print ((d in listed) || hidden ? "B" : "N") "\t" d
            }
        }
    ' - "$HOSTS_FILE") || return 1

    IS_BLOCKED=()
    local -a new_customs=()
    while IFS=$'\t' read -r status domain; do
        [[ -n "$domain" ]] || continue
        IS_BLOCKED["$domain"]=1
        if [[ "$status" == N ]]; then new_customs+=("$domain"); fi
    done <<< "$output"
    TAB_ITEMS_4+=("${new_customs[@]}")
}

# All mutations produce one complete replacement beside the resolved hosts file.
# Rename leaves the original intact if generation or metadata copying fails.
# The session lock in main serializes instances of this application.
rewrite_hosts() {
    local action=$1 domain=${2:-} source=${3:-/dev/null}
    _TMPFILE=$(mktemp -- "${HOSTS_FILE}.dusky.XXXXXXXXXX") || return 1
    if ! LC_ALL=C gawk -v action="$action" -v domain="$domain" -- "$HOSTS_AWK"'
        BEGIN {
            if (action == "block" || action == "unblock") {
                wanted[domain] = 1
                if (domain !~ /^www\./ && length(domain) <= 249) wanted["www." domain] = 1
            }
        }
        ARGIND == 1 {
            sub(/#.*/, "")
            # Import sink/loopback blocklists, never normal host address books.
            if (!(sink($1) || $1 == "127.0.0.1" || $1 == "::1")) next
            for (i = 2; i <= NF && $i !~ /^#/; i++) {
                d = normalize($i)
                if (valid(d)) wanted[d] = 1
            }
            next
        }
        {
            if (action == "import" && !length(wanted)) { print; next }
            original = $0
            comment = ""
            pos = index($0, "#")
            if (pos) { comment = substr($0, pos); $0 = substr($0, 1, pos - 1) }
            address = $1
            line = address
            removed = 0
            kept = 0
            for (i = 2; i <= NF; i++) {
                d = normalize($i)
                drop = action == "clear" ? sink(address) && valid(d) :
                    (d in wanted) && (action != "unblock" || sink(address))
                if (drop) removed = 1
                else { line = line " " $i; kept++ }
            }
            if (!removed) print original
            else if (kept) print line (comment != "" ? " " comment : "")
            else if (comment != "") print comment
        }
        END {
            if (action == "import" && !length(wanted)) exit 2
            if (action == "block" || action == "import")
                for (d in wanted) print "0.0.0.0 " d " " d "."
        }
    ' "$source" "$HOSTS_FILE" > "$_TMPFILE"; then
        rm -f -- "$_TMPFILE"
        _TMPFILE=""
        STATUS_MESSAGE="Update failed: unreadable input or no valid import domains."
        return 1
    fi
    if ! cp --attributes-only --preserve=mode,ownership -- "$HOSTS_FILE" "$_TMPFILE" ||
       ! mv -fT -- "$_TMPFILE" "$HOSTS_FILE"; then
        rm -f -- "$_TMPFILE"
        _TMPFILE=""
        STATUS_MESSAGE="Could not replace hosts file; original retained."
        return 1
    fi
    _TMPFILE=""
    refresh_state
}

toggle_domain() {
    local domain=$1 action=block
    refresh_state || return 1
    [[ -v IS_BLOCKED["$domain"] ]] && action=unblock
    rewrite_hosts "$action" "$domain"
}

add_custom_domain() {
    local domain=$1
    domain=${domain#"${domain%%[![:space:]]*}"}
    domain=${domain%"${domain##*[![:space:]]}"}
    domain=${domain,,}
    domain=${domain%.}
    if ! HOSTS_DOMAIN="$domain" LC_ALL=C gawk -- "$HOSTS_AWK"'BEGIN { exit !valid(ENVIRON["HOSTS_DOMAIN"]) }'; then
        STATUS_MESSAGE="Enter a valid DNS name, such as example.com (ASCII/punycode)."
        return 1
    fi

    refresh_state || return 1
    if [[ ! -v IS_BLOCKED["$domain"] ]]; then
        rewrite_hosts block "$domain" || return 1
    fi
    set_tab "${KNOWN_DOMAIN_TAB["$domain"]:-4}"
    local -n items="TAB_ITEMS_${CURRENT_TAB}"
    local -i i
    for (( i = 0; i < ${#items[@]}; i++ )); do
        if [[ "${items[i]}" == "$domain" ]]; then SELECTED_ROW=$i; break; fi
    done
    # An explicitly requested www alias may have been hidden beside its base.
    if (( i == ${#items[@]} )); then items+=("$domain"); SELECTED_ROW=$i; fi
    compute_scroll_window "${#items[@]}"
}

import_file_process() {
    local fpath=$1
    if [[ "$fpath" == '~' ]]; then fpath=$IMPORT_HOME
    elif [[ "$fpath" == \~/* ]]; then fpath="$IMPORT_HOME/${fpath:2}"; fi
    if [[ ! -f "$fpath" || ! -r "$fpath" ]]; then
        STATUS_MESSAGE="Import requires a readable hosts file."
        return 1
    fi
    fpath=$(readlink -e -- "$fpath") || return 1
    rewrite_hosts import "" "$fpath"
}

clear_all_blocks() {
    rewrite_hosts clear || return 1
    TAB_ITEMS_4=("$ADD_BUTTON_LABEL" "$IMPORT_BUTTON_LABEL" "$CLEAR_BUTTON_LABEL")
}

perform_blocking_import() {
    printf '%s%s%s%s' "$MOUSE_OFF" "$CLR_SCREEN" "$CURSOR_HOME" "$CURSOR_SHOW"
    stty "$ORIGINAL_STTY"
    printf '%s\n' "${C_WHITE} IMPORT HOSTS FILE${C_RESET}" \
        "${C_GREY} Tab completes paths; Ctrl-D cancels. Imports sink/loopback entries.${C_RESET}"
    local file_path=""
    # Readline completion is filename based; preserve literal backslashes/spaces.
    IFS= read -er -p ' > ' file_path || file_path=""
    stty -icanon -echo -ixon min 1 time 0
    printf '%s%s' "$MOUSE_ON" "$CURSOR_HIDE"
    if [[ -n "$file_path" ]]; then
        printf '%s\n' 'Importing...'
        import_file_process "$file_path" || :
    fi
    printf '%s%s' "$CLR_SCREEN" "$CURSOR_HOME"
}

# =============================================================================
# ▼ RENDER ENGINE ▼
# =============================================================================

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
    if (( SCROLL_OFFSET < 0 )); then SCROLL_OFFSET=0; fi
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

draw_main_view() {
    local buf="" pad_buf=""
    local -i i current_col=3 zone_start count
    local -i left_pad right_pad vis_len
    local -i _vis_start _vis_end

    buf+="${CURSOR_HOME}"
    buf+="${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'

    strip_ansi "$APP_TITLE"; local -i t_len=${#REPLY}
    strip_ansi "$APP_VERSION"; local -i v_len=${#REPLY}
    vis_len=$(( t_len + v_len + 1 ))
    left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad ))

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_WHITE}${APP_TITLE} ${C_CYAN}${APP_VERSION}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'

    # --- Scrollable Tab Rendering ---
    if (( TAB_SCROLL_START > CURRENT_TAB )); then TAB_SCROLL_START=$CURRENT_TAB; fi

    local tab_line
    local -i max_tab_width=$(( BOX_INNER_WIDTH - 6 ))

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

    # --- Items ---
    local items_var="TAB_ITEMS_${CURRENT_TAB}"
    local -n _draw_items_ref="$items_var"
    count=${#_draw_items_ref[@]}

    compute_scroll_window "$count"
    render_scroll_indicator buf "above" "$count" "$_vis_start"

    # List
    local ri item state display padded_item
    for (( ri = _vis_start; ri < _vis_end; ri++ )); do
        item="${_draw_items_ref[ri]}"

        # Check for Special Buttons
        if [[ "$item" == "$ADD_BUTTON_LABEL" || "$item" == "$IMPORT_BUTTON_LABEL" || "$item" == "$CLEAR_BUTTON_LABEL" ]]; then
            display="" # No blocked status for button

            # Special Rendering for Button
            if (( ri == SELECTED_ROW )); then
                # Selected Button
                buf+="${C_CYAN} ➤ ${C_INVERSE}${C_YELLOW}${item}${C_RESET}   ${CLR_EOL}"$'\n'
            else
                # Unselected Button
                buf+="    ${C_YELLOW}${item}${C_RESET}${CLR_EOL}"$'\n'
            fi
            continue
        fi

        state="${IS_BLOCKED["$item"]:-0}"

        if (( state == 1 )); then
            display="${C_RED}BLOCKED ⛔${C_RESET}"
        else
            display="${C_GREEN}ALLOWED ✅${C_RESET}"
        fi

        local max_len=$(( ITEM_PADDING - 1 ))
        if (( ${#item} > ITEM_PADDING )); then
            printf -v padded_item "%-${max_len}s…" "${item:0:max_len}"
        else
            printf -v padded_item "%-${ITEM_PADDING}s" "$item"
        fi

        if (( ri == SELECTED_ROW )); then
            buf+="${C_CYAN} ➤ ${C_INVERSE}${padded_item}${C_RESET} : ${display}${CLR_EOL}"$'\n'
        else
            buf+="    ${padded_item} : ${display}${CLR_EOL}"$'\n'
        fi
    done

    # Fill
    local -i rows_rendered=$(( _vis_end - _vis_start ))
    for (( ri = rows_rendered; ri < MAX_DISPLAY_ROWS; ri++ )); do
        buf+="${CLR_EOL}"$'\n'
    done

    render_scroll_indicator buf "below" "$count" "$_vis_end"

    buf+=$'\n'"${C_CYAN} [Tab] Category  [Space/Enter] Toggle  [PgUp/PgDn] Page  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    local system_line=" System: ${HOSTS_FILE}  [F5] Reload"
    system_line=${system_line//[[:cntrl:]]/?}
    if (( ${#system_line} >= TERM_COLS )); then system_line="${system_line:0:TERM_COLS-2}…"; fi
    buf+="${C_CYAN}${system_line}${C_RESET}${CLR_EOL}"$'\n'
    local footer=${STATUS_MESSAGE//[[:cntrl:]]/?}
    if (( ${#footer} >= TERM_COLS )); then footer="${footer:0:TERM_COLS-2}…"; fi
    buf+="${C_YELLOW}${footer}${C_RESET}${CLR_EOL}${CLR_EOS}"

    printf '%s' "$buf"
}

draw_input_modal() {
    # DNS input is ASCII/punycode; keep invalid pasted glyphs within the box.
    local LC_ALL=C
    local shown_input=${INPUT_BUFFER//[! -~]/?}
    if (( ${#shown_input} > 44 )); then shown_input=${shown_input: -44}; fi
    printf '%s\n\n\n\n\n\n\n\n' "$CURSOR_HOME"
    printf '%s\n' \
        "${C_MAGENTA}          ┌──────────────────────────────────────────────────┐${C_RESET}" \
        "${C_MAGENTA}          │ ${C_WHITE}ADD CUSTOM DOMAIN TO BLOCK LIST                  ${C_MAGENTA}│${C_RESET}" \
        "${C_MAGENTA}          │                                                  │${C_RESET}" \
        "${C_MAGENTA}          │ ${C_CYAN}> ${shown_input}_${C_RESET}${CLR_EOL}" \
        "${C_MAGENTA}          │                                                  │${C_RESET}" \
        "${C_MAGENTA}          └──────────────────────────────────────────────────┘${C_RESET}"
    printf '%s' "$CLR_EOS"
}

draw_ui() {
    if ! terminal_size_ok; then draw_small_terminal_notice; return; fi
    if (( CURRENT_VIEW == 0 )); then
        draw_main_view
    elif (( CURRENT_VIEW == 1 )); then
        draw_input_modal
    fi
}

# =============================================================================
# ▼ INPUT HANDLING ▼
# =============================================================================

navigate() {
    local -i dir=$1
    local -n _nav_items_ref="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#_nav_items_ref[@]}
    if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( (SELECTED_ROW + dir + count) % count ))
}

navigate_end() {
    local -i target=$1
    local -n _nave_items_ref="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#_nave_items_ref[@]}
    if (( count == 0 )); then return 0; fi
    if (( target == 0 )); then SELECTED_ROW=0; else SELECTED_ROW=$(( count - 1 )); fi
}

navigate_page() {
    local -i dir=$1
    local -n page_items="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#page_items[@]}
    (( count > 0 )) || return 0
    SELECTED_ROW=$(( SELECTED_ROW + dir * MAX_DISPLAY_ROWS ))
    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= count )); then SELECTED_ROW=$(( count - 1 )); fi
}

switch_tab() {
    set_tab "$(( (CURRENT_TAB + ${1:-1} + TAB_COUNT) % TAB_COUNT ))"
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
    if (( ${#_tog_items_ref[@]} == 0 )); then return; fi
    local item="${_tog_items_ref[SELECTED_ROW]}"

    # CHECK FOR BUTTON CLICK
    if [[ "$item" == "$ADD_BUTTON_LABEL" ]]; then
        CURRENT_VIEW=1
        INPUT_BUFFER=""
        return
    fi

    if [[ "$item" == "$IMPORT_BUTTON_LABEL" ]]; then
        perform_blocking_import
        CURRENT_VIEW=0
        return
    fi

    if [[ "$item" == "$CLEAR_BUTTON_LABEL" ]]; then
        clear_all_blocks || STATUS_MESSAGE="Could not clear blocks."
        return
    fi

    toggle_domain "$item" || STATUS_MESSAGE="Could not update hosts file."
}

handle_mouse() {
    local input=$1 context="${CURRENT_VIEW}:${CURRENT_TAB}"
    local -i button x y i start end activate=0
    local zone

    [[ $input =~ ^\[\<([0-9]{1,5})\;([0-9]{1,5})\;([0-9]{1,5})[Mm]$ ]] || return 0
    button=$(( 10#${BASH_REMATCH[1]} ))
    x=$(( 10#${BASH_REMATCH[2]} ))
    y=$(( 10#${BASH_REMATCH[3]} ))
    if (( CURRENT_VIEW != 0 || x < 1 || x > MIN_TERM_COLS || y < 1 || y > TERM_ROWS )); then
        MOUSE_CLICK_PENDING=0
        return 0
    fi

    if [[ ${input: -1} == m ]]; then
        if (( button == 0 && MOUSE_CLICK_PENDING && x == MOUSE_PRESS_X && y == MOUSE_PRESS_Y )) &&
           [[ $context == "$MOUSE_PRESS_CONTEXT" ]]; then activate=1; fi
        MOUSE_CLICK_PENDING=0
        (( activate )) || return 0
    else
        case $button in
            0)
                MOUSE_CLICK_PENDING=1
                MOUSE_PRESS_X=$x; MOUSE_PRESS_Y=$y; MOUSE_PRESS_CONTEXT=$context
                ;;
            32) MOUSE_CLICK_PENDING=0 ;;
            64|65)
                MOUSE_CLICK_PENDING=0
                local -i direction=$(( button == 64 ? -1 : 1 ))
                if (( y == TAB_ROW )); then switch_tab "$direction"; else navigate "$direction"; fi
                return 0
                ;;
            *) MOUSE_CLICK_PENDING=0; return 0 ;;
        esac
    fi

    # Tabs follow left press/motion; website actions still require a click.
    if (( y == TAB_ROW )); then
        if [[ -n $LEFT_ARROW_ZONE ]]; then
            start=${LEFT_ARROW_ZONE%%:*}; end=${LEFT_ARROW_ZONE##*:}
            if (( x >= start && x <= end )); then switch_tab -1; return 0; fi
        fi
        if [[ -n $RIGHT_ARROW_ZONE ]]; then
            start=${RIGHT_ARROW_ZONE%%:*}; end=${RIGHT_ARROW_ZONE##*:}
            if (( x >= start && x <= end )); then switch_tab 1; return 0; fi
        fi
        for (( i = 0; i < ${#TAB_ZONES[@]}; i++ )); do
            zone=${TAB_ZONES[i]}
            start=${zone%%:*}; end=${zone##*:}
            if (( x >= start && x <= end )); then set_tab "$(( i + TAB_SCROLL_START ))"; return 0; fi
        done
    fi

    local -i effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start + SCROLL_OFFSET ))
        local -n mouse_items="TAB_ITEMS_${CURRENT_TAB}"
        if (( clicked_idx < ${#mouse_items[@]} )); then
            SELECTED_ROW=$clicked_idx
            if (( activate )); then toggle_current; fi
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
        while (( ${#_esc_out} < MAX_ESCAPE_BYTES )) && IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; do
            _esc_out+="$char"
            if [[ $char == [@-~] ]]; then return 0; fi
        done
        return 1
    fi
    # Preserve the Escape prefix so Alt+Enter cannot become a bare Enter.
    _esc_out=$'\e'"$char"
    return 0
}

# --- Input Router ---
handle_key_main() {
    local key="$1"
    case "$key" in
        '[Z')                switch_tab -1; return ;;
        '[A'|'OA')           navigate -1; return ;;
        '[B'|'OB')           navigate 1; return ;;
        '[C'|'OC')           switch_tab 1; return ;;
        '[D'|'OD')           switch_tab -1; return ;;
        '[5~')              navigate_page -1; return ;;
        '[6~')              navigate_page 1; return ;;
        '[H'|'[1~')          navigate_end 0; return ;;
        '[F'|'[4~')          navigate_end 1; return ;;
        '['*'<'*[Mm])        handle_mouse "$key"; return ;;
    esac

    case "$key" in
        k|K)            navigate -1 ;;
        j|J)            navigate 1 ;;
        l|L|$'\t')      switch_tab 1 ;;
        h|H)            switch_tab -1 ;;
        g)              navigate_end 0 ;;
        G)              navigate_end 1 ;;
        " "|'')         toggle_current ;; # Space/Enter
        a|A)            CURRENT_VIEW=1; INPUT_BUFFER="" ;;
        q|Q|$'\x03')    exit 0 ;;
    esac
}

handle_key_modal() {
    local key="$1"
    if [[ "$key" == $'\x7f' || "$key" == $'\x08' ]]; then
        INPUT_BUFFER="${INPUT_BUFFER%?}"
    elif [[ "$key" == "" || "$key" == $'\n' ]]; then
        add_custom_domain "$INPUT_BUFFER" || :
        CURRENT_VIEW=0
    elif [[ "$key" == "ESC" || "$key" == $'\x1b' ]]; then
        CURRENT_VIEW=0
    else
        if [[ "$key" =~ [a-zA-Z0-9.-] ]] && [[ ${#key} -eq 1 && ${#INPUT_BUFFER} -lt 254 ]]; then
            INPUT_BUFFER+="$key"
        fi
    fi
}

# Bracketed paste is text only: newline/shortcut bytes never trigger actions.
# Keep the delimiter tail across read timeouts and do not buffer an entire paste.
consume_paste_byte() {
    local incoming=$1
    # read -n1 reports a newline delimiter as an empty string.
    if [[ -z $incoming ]]; then incoming=$'\n'; fi
    PASTE_TAIL+=$incoming
    if (( ${#PASTE_TAIL} > 6 )); then
        local char=${PASTE_TAIL:0:1}
        PASTE_TAIL=${PASTE_TAIL:1}
        if [[ $char == $'\n' || $char == $'\r' || $char == $'\t' ]]; then char=' '; fi
        if (( CURRENT_VIEW == 1 && ${#INPUT_BUFFER} < 254 )) && [[ $char == [[:print:]] ]]; then
            INPUT_BUFFER+=$char
        fi
    fi
    if [[ $PASTE_TAIL == $'\e[201~' ]]; then PASTE_ACTIVE=0; PASTE_TAIL=""; fi
}

handle_input_router() {
    local key=$1 escape_seq=""
    if (( PASTE_ACTIVE )); then consume_paste_byte "$key"; return 0; fi
    if [[ $key == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then key=$escape_seq; else key=ESC; fi
    fi
    if [[ $key == '[200~' ]]; then
        PASTE_ACTIVE=1; PASTE_TAIL=""; MOUSE_CLICK_PENDING=0
        return 0
    fi
    if ! terminal_size_ok; then
        MOUSE_CLICK_PENDING=0
        case $key in q|Q|$'\x03') exit 0 ;; esac
        return 0
    fi
    # Mouse press/release pairs survive only within the same input context.
    if [[ $key != '[<'* ]]; then MOUSE_CLICK_PENDING=0; fi
    STATUS_MESSAGE=""
    if [[ $key == '[15~' && $CURRENT_VIEW == 0 ]]; then
        if refresh_state; then STATUS_MESSAGE="Hosts file refreshed."
        else STATUS_MESSAGE="Could not read hosts file; previous state retained."; fi
        return 0
    fi
    if (( CURRENT_VIEW == 0 )); then handle_key_main "$key"
    else handle_key_modal "$key"; fi
}

# =============================================================================
# ▼ MAIN ▼
# =============================================================================

main() {
    if [[ ! -t 0 || ! -t 1 || ${TERM:-dumb} == dumb ]]; then
        log_err "Run this interactive program in a terminal."
        return 1
    fi

    if (( EUID != 0 )); then
        local script_path
        local -a bash_opts=()
        script_path=$(readlink -e -- "${BASH_SOURCE[0]}")
        if [[ $- == *x* ]]; then bash_opts=(-x); fi
        # Pass the invoking home explicitly so ~/ imports survive sudo HOME changes.
        exec sudo -- "$BASH" "${bash_opts[@]}" "$script_path" --import-home "$HOME"
    fi
    if [[ ${1:-} == --import-home ]]; then IMPORT_HOME=$2; fi

    local dep lock_fd
    for dep in gawk flock mktemp cp chmod mv rm stty readlink; do
        if ! command -v "$dep" &>/dev/null; then
            log_err "Missing dependency: $dep"
            return 1
        fi
    done

    exec {lock_fd}> /run/dusky-site-blocker.lock
    if ! flock -n "$lock_fd"; then
        log_err "Another site blocker instance is running."
        return 1
    fi

    if [[ ! -e "$HOSTS_FILE" ]]; then
        printf '127.0.0.1 localhost\n::1 localhost\n' > "$HOSTS_FILE"
        chmod 644 -- "$HOSTS_FILE"
    fi
    HOSTS_FILE=$(readlink -e -- "$HOSTS_FILE")
    if [[ ! -f "$BACKUP_FILE" ]]; then
        # A failed/interrupted copy must not leave a partial permanent backup.
        _TMPFILE=$(mktemp -- "${BACKUP_FILE}.dusky.XXXXXXXXXX")
        cp -- "$HOSTS_FILE" "$_TMPFILE"
        chmod 644 -- "$_TMPFILE"
        mv -fT -- "$_TMPFILE" "$BACKUP_FILE"
        _TMPFILE=""
    fi

    init_db
    refresh_state

    ORIGINAL_STTY=$(stty -g)
    stty -icanon -echo -ixon min 1 time 0
    TUI_STARTED=1
    printf '%s%s%s%s%s' "$ALT_SCREEN_ON" "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"
    trap 'RESIZE_PENDING=1' WINCH CONT
    update_terminal_size

    local key read_status
    local -i redraw=1
    while true; do
        if (( RESIZE_PENDING )); then
            RESIZE_PENDING=0; MOUSE_CLICK_PENDING=0
            update_terminal_size
            printf '%s' "$CLR_SCREEN"
            redraw=1
        fi
        if (( redraw )); then draw_ui; redraw=0; fi
        if IFS= read -rsn1 -t "$READ_LOOP_TIMEOUT" key; then
            # A resize may race the read; do not apply input to stale geometry.
            if (( RESIZE_PENDING )); then
                RESIZE_PENDING=0; MOUSE_CLICK_PENDING=0
                update_terminal_size
                printf '%s' "$CLR_SCREEN"
            fi
            handle_input_router "$key"
            # No frames while a paste is in flight; draw the completed input once.
            if (( ! PASTE_ACTIVE )); then redraw=1; fi
        else
            read_status=$?
            if (( read_status == 1 )); then break; fi
        fi
    done
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
