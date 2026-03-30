#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Dusky Git Checker & TUI Viewer
# -----------------------------------------------------------------------------
# Target: Arch Linux (latest) / Bash 5.3.9 / Bare Git Repo
# Requires: git, coreutils (sleep, timeout, mktemp, mv, rm), util-linux (flock, stty), openssh
# Optional: notify-send
# -----------------------------------------------------------------------------

set -euo pipefail
export LC_NUMERIC=C LC_COLLATE=C
shopt -s extglob

# =============================================================================
# CONFIGURATION
# =============================================================================

declare -r GIT_DIR="${HOME}/dusky/"
declare -r WORK_TREE="${HOME}"
declare -r STATE_FILE="${HOME}/.config/dusky/settings/dusky_update_behind_commit"
declare -r STATE_DIR="${STATE_FILE%/*}"

declare -ri NOTIFY_THRESHOLD=30
declare -ri TIMEOUT_SEC=15
declare -ri TIMEOUT_KILL_SEC=2
# Covers the worst-case lock hold time: primary fetch + HTTPS fallback fetch.
declare -ri LOCK_WAIT_SEC=$(( (TIMEOUT_SEC + TIMEOUT_KILL_SEC) * 2 + 1 ))
declare -r LOCK_BASENAME="dusky_git_fetch.${UID}.lock"

# TUI settings
declare -r APP_TITLE="Dusky Updates"
declare -ri MAX_DISPLAY_ROWS=14
declare -ri BOX_INNER_WIDTH=76
declare -ri ITEM_PADDING=14
# Row immediately above the first commit row (1-indexed terminal row).
declare -ri ITEM_START_ROW=5
declare -ri MIN_TERM_COLS=$(( BOX_INNER_WIDTH + 2 ))
declare -ri MIN_TERM_ROWS=$(( MAX_DISPLAY_ROWS + 9 ))

# Debug mode
declare _debug_env="${DEBUG:-0}"
declare -i DEBUG=0
[[ $_debug_env =~ ^[1-9][0-9]*$ ]] && DEBUG=$_debug_env
unset _debug_env

# Default refspec for --fix-config
declare -r FETCH_REFSPEC='+refs/heads/*:refs/remotes/origin/*'

# Git command
declare -ra GIT_CMD=(/usr/bin/git --git-dir="$GIT_DIR" --work-tree="$WORK_TREE")

# =============================================================================
# UTILITIES
# =============================================================================

_debug() {
    (( DEBUG )) || return 0
    printf '[DEBUG] %s\n' "$*" >&2
}

_sleep() {
    /usr/bin/sleep "${1:-1}"
}

_strip_ansi() {
    local str=$1
    local -n _out_ref=$2
    local ansi_re=$'^([^\e]*)\e\\[[0-9;]*m(.*)$'

    _out_ref=''
    while [[ $str =~ $ansi_re ]]; do
        _out_ref+="${BASH_REMATCH[1]}"
        str="${BASH_REMATCH[2]}"
    done
    _out_ref+="$str"
}

_sanitize_terminal_text() {
    local stripped=''
    local -n _out_ref=$2

    _strip_ansi "$1" stripped
    stripped=${stripped//[[:cntrl:]]/ }
    _out_ref=$stripped
}

_ellipsize() {
    local text=$1
    local -i max_len=$2
    local -n _out_ref=$3

    _out_ref=$text
    (( max_len < 1 )) && { _out_ref=''; return 0; }

    if (( ${#_out_ref} > max_len )); then
        if (( max_len == 1 )); then
            _out_ref='…'
        else
            _out_ref="${_out_ref:0:max_len-1}…"
        fi
    fi
}

_redact_url() {
    local url=$1
    local -n _out_ref=$2

    _out_ref=$url
    if [[ $url =~ ^([[:alpha:]][[:alnum:]+.-]*://)[^/@]+@(.+)$ ]]; then
        _out_ref="${BASH_REMATCH[1]}***@${BASH_REMATCH[2]}"
    elif [[ $url =~ ^[^/@]+@([^:]+:.+)$ ]]; then
        _out_ref="***@${BASH_REMATCH[1]}"
    fi
}

origin_to_https_url() {
    local origin_url=$1
    local -n _out_ref=$2

    _out_ref=''

    if [[ $origin_url =~ ^https://.+$ ]]; then
        _out_ref=$origin_url
        return 0
    fi

    if [[ $origin_url =~ ^git://([^/]+)/(.+)$ ]]; then
        _out_ref="https://${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
        return 0
    fi

    if [[ $origin_url =~ ^ssh://([^@/]+@)?([^/:]+)(:[0-9]+)?/(.+)$ ]]; then
        _out_ref="https://${BASH_REMATCH[2]}/${BASH_REMATCH[4]}"
        return 0
    fi

    if [[ $origin_url =~ ^([^@/]+@)?([^:]+):(.+)$ ]]; then
        _out_ref="https://${BASH_REMATCH[2]}/${BASH_REMATCH[3]}"
        return 0
    fi

    return 1
}

get_lock_file() {
    local lock_dir=''

    if [[ -n ${XDG_RUNTIME_DIR:-} && -d ${XDG_RUNTIME_DIR:-} && -w ${XDG_RUNTIME_DIR:-} ]]; then
        lock_dir=$XDG_RUNTIME_DIR
    else
        lock_dir=$STATE_DIR
        if [[ -e $lock_dir && ! -d $lock_dir ]]; then
            return 1
        fi
        [[ -d $lock_dir ]] || mkdir -p -m 700 -- "$lock_dir"
    fi

    printf '%s/%s' "$lock_dir" "$LOCK_BASENAME"
}

git_fetch() {
    local ssh_cmd="/usr/bin/ssh -oBatchMode=yes -oStrictHostKeyChecking=yes -oConnectTimeout=${TIMEOUT_SEC}"

    GIT_TERMINAL_PROMPT=0 \
    GIT_ASKPASS=/usr/bin/false \
    SSH_ASKPASS=/usr/bin/false \
    GIT_SSH_COMMAND="$ssh_cmd" \
        /usr/bin/timeout --kill-after="$TIMEOUT_KILL_SEC" "$TIMEOUT_SEC" \
        "${GIT_CMD[@]}" \
        -c credential.interactive=never \
        fetch \
        --atomic \
        --quiet \
        --prune \
        --no-write-fetch-head \
        --no-auto-gc \
        "$@" \
        2>/dev/null
}

_git_rev_count() {
    local -n _out_ref=$1
    local revspec=$2
    local _raw_count=''

    if ! _raw_count=$("${GIT_CMD[@]}" rev-list --count "$revspec" 2>/dev/null); then
        return 1
    fi

    [[ $_raw_count =~ ^[0-9]+$ ]] || return 1
    _out_ref=$_raw_count
}

write_state_file() {
    local value=$1
    local tmp=''

    [[ -d "$STATE_DIR" ]] || mkdir -p "$STATE_DIR"

    tmp=$(/usr/bin/mktemp --tmpdir="$STATE_DIR" '.dusky_update_behind_commit.XXXXXX') || return 1

    if ! printf '%s\n' "$value" > "$tmp"; then
        /usr/bin/rm -f -- "$tmp" || true
        return 1
    fi

    if ! /usr/bin/mv -f -- "$tmp" "$STATE_FILE"; then
        /usr/bin/rm -f -- "$tmp" || true
        return 1
    fi
}

read_state_value() {
    local value=''

    [[ -r "$STATE_FILE" ]] || return 1
    IFS= read -r value < "$STATE_FILE" || return 1
    [[ $value =~ ^-?[0-9]+$ ]] || return 1

    printf '%s' "$value"
}

get_terminal_size() {
    local -n _rows_ref=$1 _cols_ref=$2

    if ! IFS=' ' read -r _rows_ref _cols_ref < <(/usr/bin/stty size 2>/dev/null); then
        return 1
    fi

    [[ $_rows_ref =~ ^[0-9]+$ && $_cols_ref =~ ^[0-9]+$ ]]
}

terminal_fits_ui() {
    if ! get_terminal_size TERM_ROWS TERM_COLS; then
        TERM_ROWS=0
        TERM_COLS=0
        return 1
    fi

    (( TERM_COLS >= MIN_TERM_COLS && TERM_ROWS >= MIN_TERM_ROWS ))
}

# =============================================================================
# VALIDATION
# =============================================================================

validate_environment() {
    local cmd=''

    if (( BASH_VERSINFO[0] < 5 )); then
        printf 'ERROR: Bash 5.0+ required (found %s)\n' "$BASH_VERSION" >&2
        return 1
    fi

    for cmd in /usr/bin/git /usr/bin/timeout /usr/bin/flock /usr/bin/mktemp /usr/bin/mv /usr/bin/rm /usr/bin/sleep /usr/bin/ssh; do
        [[ -x $cmd ]] || {
            printf 'ERROR: Required command not found: %s\n' "$cmd" >&2
            return 1
        }
    done

    [[ -d "$WORK_TREE" ]] || {
        printf 'ERROR: Work tree not found: %s\n' "$WORK_TREE" >&2
        return 1
    }

    [[ -d "$GIT_DIR" ]] || {
        printf 'ERROR: Git directory not found: %s\n' "$GIT_DIR" >&2
        return 1
    }

    [[ -f "${GIT_DIR}/HEAD" ]] || {
        printf 'ERROR: Not a valid git directory: %s\n' "$GIT_DIR" >&2
        return 1
    }

    if ! "${GIT_CMD[@]}" rev-parse --git-dir &>/dev/null; then
        printf 'ERROR: Not a valid git directory: %s\n' "$GIT_DIR" >&2
        return 1
    fi

    return 0
}

validate_terminal() {
    [[ -x /usr/bin/stty ]] || {
        printf 'ERROR: Required command not found: /usr/bin/stty\n' >&2
        return 1
    }

    [[ -t 0 && -t 1 ]] || {
        printf 'ERROR: Interactive mode requires a terminal.\n' >&2
        return 1
    }

    case ${TERM:-} in
        ''|dumb)
            printf 'ERROR: TERM is not suitable for the TUI.\n' >&2
            return 1
            ;;
    esac

    return 0
}

# =============================================================================
# ROBUST FETCH LOGIC
# =============================================================================

declare FETCH_INFO=""

get_fetch_remote() {
    local head_branch='' remote=''

    if head_branch=$("${GIT_CMD[@]}" symbolic-ref --quiet --short HEAD 2>/dev/null) &&
       [[ -n $head_branch ]] &&
       remote=$("${GIT_CMD[@]}" config --get "branch.${head_branch}.remote" 2>/dev/null) &&
       [[ -n $remote ]]; then
        printf '%s' "$remote"
        return 0
    fi

    if "${GIT_CMD[@]}" remote get-url origin &>/dev/null; then
        printf 'origin'
        return 0
    fi

    return 1
}

robust_fetch() {
    FETCH_INFO=""

    local lock_file='' remote_url='' https_url='' redacted_url='' fetch_remote='' fetch_refspec=''
    local lock_fd=-1
    local -i rc=1

    if ! fetch_remote=$(get_fetch_remote); then
        FETCH_INFO="No suitable remote configured"
        _debug "No fetch remote found"
        return 1
    fi

    fetch_refspec="+refs/heads/*:refs/remotes/${fetch_remote}/*"

    if ! remote_url=$("${GIT_CMD[@]}" remote get-url "$fetch_remote" 2>/dev/null); then
        FETCH_INFO="Remote '${fetch_remote}' is not configured"
        _debug "Failed to resolve remote URL for: $fetch_remote"
        return 1
    fi

    _redact_url "$remote_url" redacted_url
    _debug "Fetch remote: $fetch_remote"
    _debug "Remote URL: $redacted_url"

    if ! lock_file=$(get_lock_file); then
        FETCH_INFO="Cannot determine fetch lock file"
        _debug "Failed to determine fetch lock file"
        return 1
    fi

    if ! exec {lock_fd}> "$lock_file"; then
        FETCH_INFO="Cannot open fetch lock file"
        _debug "Failed to open fetch lock: $lock_file"
        return 1
    fi

    if ! /usr/bin/flock -w "$LOCK_WAIT_SEC" "$lock_fd"; then
        FETCH_INFO="Another update check is already running"
        _debug "Could not acquire fetch lock: $lock_file"
        exec {lock_fd}>&-
        return 1
    fi

    _debug "Trying: git fetch ${fetch_remote} with refspec ${fetch_refspec}"
    if git_fetch "$fetch_remote" "$fetch_refspec"; then
        FETCH_INFO="Fetched via ${fetch_remote}"
        _debug "Fetch succeeded"
        rc=0
    else
        _debug "Primary fetch failed, attempting HTTPS fallback"
        if ! origin_to_https_url "$remote_url" https_url; then
            FETCH_INFO="Primary fetch failed and no HTTPS fallback is available"
            _debug "URL format not recognized"
        else
            _redact_url "$https_url" redacted_url
            _debug "HTTPS URL: $redacted_url"
            if git_fetch "$https_url" "$fetch_refspec"; then
                FETCH_INFO="Fetched via HTTPS fallback"
                _debug "HTTPS fetch succeeded"
                rc=0
            else
                FETCH_INFO="All fetch methods failed"
                _debug "All fetch attempts exhausted"
            fi
        fi
    fi

    exec {lock_fd}>&-
    return "$rc"
}

# =============================================================================
# UPSTREAM DETECTION
# =============================================================================

get_upstream_ref() {
    local tracking=''

    if tracking=$("${GIT_CMD[@]}" rev-parse --abbrev-ref '@{upstream}' 2>/dev/null) &&
       [[ -n $tracking ]]; then
        printf '%s' "$tracking"
        return 0
    fi

    if tracking=$("${GIT_CMD[@]}" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null) &&
       [[ -n $tracking ]]; then
        printf '%s' "$tracking"
        return 0
    fi

    local ref=''
    for ref in origin/main origin/master; do
        if "${GIT_CMD[@]}" rev-parse --verify --quiet "$ref" &>/dev/null; then
            printf '%s' "$ref"
            return 0
        fi
    done

    return 1
}

# =============================================================================
# BACKGROUND MODE (--num)
# =============================================================================

run_background_check() {
    local previous_state=''
    local -i have_previous_state=0
    local -i previous_count=-2147483648
    local upstream=''
    local -i count=0

    if previous_state=$(read_state_value 2>/dev/null); then
        previous_count=$previous_state
        have_previous_state=1
    fi

    if ! validate_environment; then
        write_state_file -1
        exit 0
    fi

    if ! robust_fetch; then
        _debug "Fetch failed: $FETCH_INFO"
        if [[ $FETCH_INFO == "Another update check is already running" ]]; then
            _debug "Leaving existing state file unchanged"
            if (( ! have_previous_state )); then
                write_state_file -1
            fi
            exit 0
        fi
        write_state_file -1
        exit 0
    fi

    if ! upstream=$(get_upstream_ref); then
        _debug "No upstream found, writing -2"
        write_state_file -2
        exit 0
    fi
    _debug "Upstream: $upstream"

    if ! _git_rev_count count "HEAD..${upstream}"; then
        _debug "Failed to count commits behind, writing -1"
        write_state_file -1
        exit 0
    fi
    _debug "Commits behind: $count"

    write_state_file "$count"

    if (( count >= NOTIFY_THRESHOLD )) &&
       (( ! have_previous_state || (previous_count >= 0 && previous_count < NOTIFY_THRESHOLD) )) &&
       [[ -x /usr/bin/notify-send ]]; then
        /usr/bin/timeout --kill-after=1 2 \
            /usr/bin/notify-send -u normal -t 5000 -i software-update-available \
            "Dusky Dotfiles" \
            "Update Available: Your system is ${count} commits behind." \
            >/dev/null 2>&1 || _debug "notify-send failed"
    fi

    exit 0
}

# =============================================================================
# ARGUMENT PARSING
# =============================================================================

parse_arguments() {
    while (( $# > 0 )); do
        case "$1" in
            --num)
                run_background_check
                ;;
            --debug)
                DEBUG=1
                _debug "Debug mode enabled"
                shift
                ;;
            --fix-config)
                validate_environment || exit 1
                printf 'Setting fetch refspec in git config...\n'
                "${GIT_CMD[@]}" config --replace-all remote.origin.fetch "$FETCH_REFSPEC"
                printf 'Done. Current value:\n'
                "${GIT_CMD[@]}" config --get-all remote.origin.fetch
                exit 0
                ;;
            --help|-h)
                printf 'Usage: %s [OPTIONS]\n\n' "${0##*/}"
                printf 'Options:\n'
                printf '  --num        Output commit count to state file (for Waybar)\n'
                printf '  --debug      Enable debug output\n'
                printf '  --fix-config Set the fetch refspec in git config\n'
                exit 0
                ;;
            *)
                printf 'Unknown option: %s\n' "$1" >&2
                exit 1
                ;;
        esac
    done
}

parse_arguments "$@"

# =============================================================================
# ANSI ESCAPE CODES
# =============================================================================

declare _hbuf=''
printf -v _hbuf '%*s' "$BOX_INNER_WIDTH" ''
declare -r H_LINE="${_hbuf// /─}"
unset _hbuf

declare -r C_RESET=$'\e[0m'     C_CYAN=$'\e[1;36m'    C_GREEN=$'\e[1;32m'
declare -r C_YELLOW=$'\e[1;33m' C_MAGENTA=$'\e[1;35m' C_WHITE=$'\e[1;37m'
declare -r C_GREY=$'\e[1;30m'   C_RED=$'\e[1;31m'     C_INVERSE=$'\e[7m'

declare -r CLR_EOL=$'\e[K'      CLR_EOS=$'\e[J'       CLR_SCREEN=$'\e[2J'
declare -r CUR_HOME=$'\e[H'     CUR_HIDE=$'\e[?25l'   CUR_SHOW=$'\e[?25h'
declare -r MOUSE_ON=$'\e[?1000h\e[?1002h\e[?1006h'
declare -r MOUSE_OFF=$'\e[?1000l\e[?1002l\e[?1006l'

# =============================================================================
# TUI STATE
# =============================================================================

declare -i SELECTED_ROW=0 SCROLL_OFFSET=0
declare -i TOTAL_COMMITS=0 LOCAL_REV=0 REMOTE_REV=0
declare -i TERM_ROWS=0 TERM_COLS=0
declare -i TUI_ACTIVE=0
declare -a COMMIT_HASHES=() COMMIT_MSGS=()
declare ORIGINAL_STTY="" FETCH_STATUS="OK"

cleanup() {
    if (( TUI_ACTIVE )); then
        printf '%s%s%s\n' "$MOUSE_OFF" "$CUR_SHOW" "$C_RESET" || true
    fi
    [[ -n ${ORIGINAL_STTY:-} ]] && /usr/bin/stty "$ORIGINAL_STTY" 2>/dev/null || true
}

trap cleanup EXIT
trap 'exit 130' INT TERM HUP

# =============================================================================
# DATA LOADING
# =============================================================================

load_commits() {
    COMMIT_HASHES=()
    COMMIT_MSGS=()

    if ! _git_rev_count LOCAL_REV HEAD; then
        COMMIT_HASHES=("ERR")
        COMMIT_MSGS=("Failed to read local revision count")
        TOTAL_COMMITS=1
        FETCH_STATUS="GIT_ERROR"
        LOCAL_REV=0
        REMOTE_REV=0
        return
    fi

    if [[ $FETCH_STATUS == FAIL ]]; then
        REMOTE_REV=0
        COMMIT_HASHES=("ERR")
        COMMIT_MSGS=("Fetch failed - cannot verify remote status")
        TOTAL_COMMITS=1
        return
    fi

    local upstream=''
    if ! upstream=$(get_upstream_ref); then
        COMMIT_HASHES=("ERR")
        COMMIT_MSGS=("No upstream branch found (try: git branch -u origin/main)")
        TOTAL_COMMITS=1
        FETCH_STATUS="NO_UPSTREAM"
        REMOTE_REV=0
        return
    fi

    if ! _git_rev_count REMOTE_REV "$upstream"; then
        COMMIT_HASHES=("ERR")
        COMMIT_MSGS=("Failed to read upstream revision count")
        TOTAL_COMMITS=1
        FETCH_STATUS="GIT_ERROR"
        REMOTE_REV=0
        return
    fi

    local -i count=0
    if ! _git_rev_count count "HEAD..${upstream}"; then
        COMMIT_HASHES=("ERR")
        COMMIT_MSGS=("Failed to compare HEAD against ${upstream}")
        TOTAL_COMMITS=1
        FETCH_STATUS="GIT_ERROR"
        return
    fi

    _debug "load_commits: HEAD=$LOCAL_REV, upstream=$REMOTE_REV, behind=$count"

    if (( count == 0 )); then
        COMMIT_HASHES=("HEAD")
        COMMIT_MSGS=("Dusky is up to date!")
        TOTAL_COMMITS=1
        return
    fi

    local -ri max_len=$(( BOX_INNER_WIDTH - ITEM_PADDING - 6 ))
    local -a raw_commits=()
    local line='' hash='' msg='' safe_msg=''

    mapfile -t raw_commits < <(
        "${GIT_CMD[@]}" --no-pager log "HEAD..${upstream}" \
            --no-color --pretty=format:'%h|%s' 2>/dev/null
    ) || true

    for line in "${raw_commits[@]}"; do
        hash=${line%%|*}
        msg=${line#*|}
        [[ -n $hash ]] || continue

        _sanitize_terminal_text "$msg" safe_msg
        msg=$safe_msg
        _ellipsize "$msg" "$max_len" msg

        COMMIT_HASHES+=("$hash")
        COMMIT_MSGS+=("$msg")
    done

    TOTAL_COMMITS=${#COMMIT_HASHES[@]}

    if (( TOTAL_COMMITS == 0 )); then
        COMMIT_HASHES=("WARN")
        COMMIT_MSGS=("Detected $count updates but log was empty")
        TOTAL_COMMITS=1
    fi
}

# =============================================================================
# UI ENGINE
# =============================================================================

draw_terminal_too_small() {
    printf '%s%s%sTerminal too small. Need at least %dx%d, current %dx%d.%s\n' \
        "$CUR_HOME" "$CLR_SCREEN" "$C_RED" \
        "$MIN_TERM_COLS" "$MIN_TERM_ROWS" "$TERM_COLS" "$TERM_ROWS" "$C_RESET"
    printf '%sResize the terminal or press q to quit.%s%s' \
        "$C_CYAN" "$C_RESET" "$CLR_EOS"
}

draw_ui() {
    local buf='' pad_buf='' repo_display=''
    local plain_title='' stats='' plain_stats='' pos=''
    local h='' m='' ph=''
    local -i visible_len=0 left_pad=0 right_pad=0
    local -i vstart=0 vend=0 i=0 footer_max=0

    buf+="$CUR_HOME"
    buf+="${C_MAGENTA}┌${H_LINE}┐${C_RESET}"$'\n'

    plain_title="${APP_TITLE} Local: #${LOCAL_REV} vs Remote: #${REMOTE_REV}"
    visible_len=${#plain_title}

    left_pad=$(( (BOX_INNER_WIDTH - visible_len) / 2 ))
    (( left_pad < 0 )) && left_pad=0

    right_pad=$(( BOX_INNER_WIDTH - visible_len - left_pad ))
    (( right_pad < 0 )) && right_pad=0

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_WHITE}${APP_TITLE} ${C_GREY}Local: #${LOCAL_REV} vs Remote: #${REMOTE_REV}"

    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}${C_MAGENTA}│${C_RESET}"$'\n'

    case "$FETCH_STATUS" in
        FAIL)
            stats="${C_RED}Fetch Failed: ${FETCH_INFO:0:45}${C_RESET}"
            plain_stats="Fetch Failed: ${FETCH_INFO:0:45}"
            ;;
        NO_UPSTREAM)
            stats="${C_RED}Status: No Upstream Branch${C_RESET}"
            plain_stats="Status: No Upstream Branch"
            ;;
        GIT_ERROR)
            stats="${C_RED}Status: Git Error${C_RESET}"
            plain_stats="Status: Git Error"
            ;;
        *)
            case "${COMMIT_HASHES[0]:-}" in
                HEAD)
                    stats="${C_GREEN}Status: Up to date${C_RESET}"
                    plain_stats="Status: Up to date"
                    ;;
                WARN)
                    stats="${C_YELLOW}Status: Log Error${C_RESET}"
                    plain_stats="Status: Log Error"
                    ;;
                ERR)
                    stats="${C_RED}Status: Error${C_RESET}"
                    plain_stats="Status: Error"
                    ;;
                *)
                    stats="${C_YELLOW}Commits Behind: ${TOTAL_COMMITS}${C_RESET}"
                    plain_stats="Commits Behind: ${TOTAL_COMMITS}"
                    ;;
            esac
            ;;
    esac

    visible_len=$(( ${#plain_stats} + 1 ))
    right_pad=$(( BOX_INNER_WIDTH - visible_len ))
    (( right_pad < 0 )) && right_pad=0

    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${C_MAGENTA}│ ${stats}${pad_buf}${C_MAGENTA}│${C_RESET}"$'\n'
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}"$'\n'

    if (( TOTAL_COMMITS > 0 )); then
        (( SELECTED_ROW < 0 )) && SELECTED_ROW=0
        (( SELECTED_ROW >= TOTAL_COMMITS )) && SELECTED_ROW=$(( TOTAL_COMMITS - 1 ))
        (( SELECTED_ROW < SCROLL_OFFSET )) && SCROLL_OFFSET=$SELECTED_ROW
        (( SELECTED_ROW >= SCROLL_OFFSET + MAX_DISPLAY_ROWS )) && \
            SCROLL_OFFSET=$(( SELECTED_ROW - MAX_DISPLAY_ROWS + 1 ))
    else
        SELECTED_ROW=0
        SCROLL_OFFSET=0
    fi

    vstart=$SCROLL_OFFSET
    vend=$(( SCROLL_OFFSET + MAX_DISPLAY_ROWS ))
    (( vend > TOTAL_COMMITS )) && vend=$TOTAL_COMMITS

    if (( SCROLL_OFFSET > 0 )); then
        buf+="${C_GREY}    ▲ (more above)${CLR_EOL}${C_RESET}"$'\n'
    else
        buf+="${CLR_EOL}"$'\n'
    fi

    for (( i = vstart; i < vend; i++ )); do
        h=${COMMIT_HASHES[i]}
        m=${COMMIT_MSGS[i]}
        printf -v ph "%-${ITEM_PADDING}s" "$h"

        if (( i == SELECTED_ROW )); then
            buf+="${C_CYAN} ➤ ${C_INVERSE}${ph}${C_RESET} : ${C_WHITE}${m}${C_RESET}${CLR_EOL}"$'\n'
        else
            buf+="    ${C_GREY}${ph}${C_RESET} : ${C_GREY}${m}${C_RESET}${CLR_EOL}"$'\n'
        fi
    done

    for (( i = vend - vstart; i < MAX_DISPLAY_ROWS; i++ )); do
        buf+="${CLR_EOL}"$'\n'
    done

    if (( TOTAL_COMMITS > MAX_DISPLAY_ROWS )); then
        pos="[$(( SELECTED_ROW + 1 ))/${TOTAL_COMMITS}]"
        if (( vend < TOTAL_COMMITS )); then
            buf+="${C_GREY}    ▼ (more below) ${pos}${CLR_EOL}${C_RESET}"$'\n'
        else
            buf+="${C_GREY}                   ${pos}${CLR_EOL}${C_RESET}"$'\n'
        fi
    else
        buf+="${CLR_EOL}"$'\n'
    fi

    repo_display=$GIT_DIR
    footer_max=$(( TERM_COLS - 8 ))
    (( footer_max < 1 )) && footer_max=1
    _ellipsize "$repo_display" "$footer_max" repo_display

    buf+=$'\n'"${C_CYAN} [↑↓/jk] Move  [PgUp/Dn] Page  [g/G] Start/End  [q] Quit${C_RESET}"$'\n'
    buf+="${C_CYAN} Repo: ${C_WHITE}${repo_display}${C_RESET}${CLR_EOL}${CLR_EOS}"

    printf '%s' "$buf"
}

# =============================================================================
# NAVIGATION
# =============================================================================

nav_step() {
    local -i d=$1
    (( TOTAL_COMMITS == 0 )) && return
    SELECTED_ROW=$(( (SELECTED_ROW + d + TOTAL_COMMITS) % TOTAL_COMMITS ))
}

nav_page() {
    local -i d=$1
    (( TOTAL_COMMITS == 0 )) && return
    SELECTED_ROW=$(( SELECTED_ROW + d * MAX_DISPLAY_ROWS ))
    (( SELECTED_ROW < 0 )) && SELECTED_ROW=0
    (( SELECTED_ROW >= TOTAL_COMMITS )) && SELECTED_ROW=$(( TOTAL_COMMITS - 1 ))
}

nav_edge() {
    (( TOTAL_COMMITS == 0 )) && return
    case $1 in
        home) SELECTED_ROW=0 ;;
        end)  SELECTED_ROW=$(( TOTAL_COMMITS - 1 )) ;;
    esac
}

handle_mouse() {
    local seq=$1

    if [[ $seq =~ ^\[\<([0-9]+)\;([0-9]+)\;([0-9]+)([Mm])$ ]]; then
        local -i btn=${BASH_REMATCH[1]}
        local -i row=${BASH_REMATCH[3]}
        local act=${BASH_REMATCH[4]}

        if [[ $act == M ]]; then
            case $btn in
                0)
                    local -i idx=$(( SCROLL_OFFSET + row - ITEM_START_ROW - 1 ))
                    if (( idx >= 0 && idx < TOTAL_COMMITS )); then
                        SELECTED_ROW=$idx
                    fi
                    ;;
                64)
                    nav_step -1
                    ;;
                65)
                    nav_step 1
                    ;;
            esac
        fi
    fi
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    validate_environment || exit 1
    validate_terminal || exit 1

    printf '\n%sFetching updates...%s\n' "$C_CYAN" "$C_RESET"

    if ! robust_fetch; then
        printf '%s[WARNING] Fetch failed: %s%s\n' "$C_YELLOW" "$FETCH_INFO" "$C_RESET"
        FETCH_STATUS="FAIL"
        _sleep 2
    else
        printf '%s[OK] %s%s\n' "$C_GREEN" "$FETCH_INFO" "$C_RESET"
        _sleep 1
    fi

    load_commits

    ORIGINAL_STTY=$(/usr/bin/stty -g 2>/dev/null) || true
    printf '%s%s%s%s' "$MOUSE_ON" "$CUR_HIDE" "$CLR_SCREEN" "$CUR_HOME"
    TUI_ACTIVE=1

    local key='' seq='' ch=''
    local -i ui_ok=0

    while true; do
        if terminal_fits_ui; then
            ui_ok=1
            draw_ui
        else
            ui_ok=0
            draw_terminal_too_small
        fi

        IFS= read -rsn1 key || break

        if (( ! ui_ok )); then
            case "$key" in
                q|Q|$'\x03') break ;;
                *) continue ;;
            esac
        fi

        if [[ "$key" == $'\e' ]]; then
            seq=''
            while IFS= read -rsn1 -t 0.05 ch; do
                seq+="$ch"
            done

            if [[ -z "$seq" ]]; then
                key="ESC"
            else
                case "$seq" in
                    '[A'|OA)     nav_step -1 ;;
                    '[B'|OB)     nav_step 1 ;;
                    '[5~')       nav_page -1 ;;
                    '[6~')       nav_page 1 ;;
                    '[H'|'[1~')  nav_edge home ;;
                    '[F'|'[4~')  nav_edge end ;;
                    '['*'<'+([0-9])';'+([0-9])';'+([0-9])+([Mm]))
                        handle_mouse "$seq"
                        ;;
                    *)
                        continue
                        ;;
                esac
                continue
            fi
        fi

        case "$key" in
            q|Q|$'\x03'|ESC)
                break
                ;;
            k|K)
                nav_step -1
                ;;
            j|J)
                nav_step 1
                ;;
            g)
                nav_edge home
                ;;
            G)
                nav_edge end
                ;;
            $'\n')
                ;;
        esac
    done
}
main "$@"
