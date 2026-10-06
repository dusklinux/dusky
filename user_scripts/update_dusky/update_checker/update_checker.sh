#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Dusky Git Checker & TUI Viewer
# -----------------------------------------------------------------------------
# Target: Arch Linux (latest) / Bash 5.3.20+ / Bare Git Repo
# Requires: git, coreutils, util-linux (setsid, flock, stty); openssh for SSH remotes
# Optional: notify-send
# -----------------------------------------------------------------------------

set -euo pipefail
export LC_NUMERIC=C LC_COLLATE=C

# =============================================================================
# CONFIGURATION
# =============================================================================

declare -r GIT_DIR="${HOME}/dusky"
declare -r WORK_TREE="${HOME}"
declare -r STATE_FILE="${HOME}/.config/dusky/settings/dusky_update_behind_commit"
declare -r STATE_DIR="${STATE_FILE%/*}"

declare -ri NOTIFY_THRESHOLD=30
declare -ri TIMEOUT_SEC=30
declare -ri TIMEOUT_KILL_SEC=2
# One deadline covers the entire background worker, including Git helpers.
declare -r LOCK_FILE="${STATE_DIR}/.dusky_update_check.lock"
declare -r STATE_LOCK_FILE="${STATE_DIR}/.dusky_update_state.lock"
declare -r GENERATION_FILE="${STATE_DIR}/.dusky_update_generation"
# A private ref avoids interfering with remote tracking or the updater's refs.
declare -r UPSTREAM_REF='refs/dusky-checker/upstream/main'
declare -r FETCH_REFSPEC="+refs/heads/main:${UPSTREAM_REF}"
declare MODE=background WAIT=0 WORKER=0

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
if [[ $_debug_env =~ ^[1-9][0-9]*$ ]]; then
    DEBUG=1
fi
unset _debug_env

declare -ra GIT_CMD=(/usr/bin/git --git-dir="$GIT_DIR" --work-tree="$WORK_TREE")

# ANSI ESCAPE CODES
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

# TUI STATE
declare -i SELECTED_ROW=0 SCROLL_OFFSET=0
declare -i TOTAL_COMMITS=0 BEHIND_COUNT=0 LOCAL_REV=0 REMOTE_REV=0
declare -i TERM_ROWS=0 TERM_COLS=0
declare -i TUI_ACTIVE=0
declare -i GLOBAL_LOCK_FD=-1
declare -a COMMIT_HASHES=() COMMIT_MSGS=()
declare ORIGINAL_STTY="" FETCH_STATUS="OK" FETCH_INFO=""

# =============================================================================
# CLEANUP & TRAPS (Registered early so ANY failure is caught and exits 0)
# =============================================================================

# shellcheck disable=SC2329 # Invoked by EXIT/signal traps.
cleanup() {
    # Disable traps to avoid recursion during shutdown
    trap - ERR EXIT INT TERM HUP 2>/dev/null || true

    if (( TUI_ACTIVE )); then
        printf '%s%s%s\n' "$MOUSE_OFF" "$CUR_SHOW" "$C_RESET" 2>/dev/null || true
    fi
    if [[ -n ${ORIGINAL_STTY:-} ]]; then
        /usr/bin/stty "$ORIGINAL_STTY" 2>/dev/null || true
    fi
    if (( GLOBAL_LOCK_FD >= 0 )); then
        exec {GLOBAL_LOCK_FD}>&- 2>/dev/null || true
        GLOBAL_LOCK_FD=-1
    fi
    exit 0
}

trap 'cleanup' EXIT
trap 'cleanup' INT TERM HUP
trap 'cleanup' ERR
trap 'true' WINCH

# =============================================================================
# UTILITIES
# =============================================================================

_debug() {
    (( DEBUG )) || return 0
    printf '[DEBUG] %s\n' "$*" >&2
    return 0
}

_sleep() {
    /usr/bin/sleep "${1:-1}" 2>/dev/null || true
    return 0
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
    return 0
}

_sanitize_terminal_text() {
    local stripped=''
    local -n _out_ref=$2

    _strip_ansi "$1" stripped
    stripped=${stripped//[[:cntrl:]]/ }
    _out_ref=$stripped
    return 0
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
    return 0
}

git_fetch() {
    local source=''
    source=$("${GIT_CMD[@]}" remote get-url origin 2>/dev/null) || return 1
    # Public GitHub reads need no SSH key, agent, host prompt, or fallback retry.
    case $source in
        git@github.com:*) source="https://github.com/${source#git@github.com:}" ;;
        ssh://git@github.com/*) source="https://github.com/${source#ssh://git@github.com/}" ;;
    esac
    local -a deadline=()
    if [[ $MODE == tui ]]; then
        deadline=(/usr/bin/timeout --kill-after="$TIMEOUT_KILL_SEC" 15)
    fi
    GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/usr/bin/false SSH_ASKPASS=/usr/bin/false \
    GIT_SSH_COMMAND='/usr/bin/ssh -oBatchMode=yes -oConnectTimeout=10' \
        "${deadline[@]}" "${GIT_CMD[@]}" -c credential.interactive=false \
        fetch --quiet --no-tags --no-prune --no-prune-tags --refmap= \
        --no-recurse-submodules --no-write-fetch-head --no-auto-maintenance \
        --no-write-commit-graph -- "$source" "$FETCH_REFSPEC" 2>/dev/null
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
    return 0
}

write_state_file() {
    local value=$1
    local tmp=''

    [[ -d "$STATE_DIR" ]] || mkdir -p "$STATE_DIR" 2>/dev/null || true

    if tmp=$(/usr/bin/mktemp --tmpdir="$STATE_DIR" '.dusky_update_behind_commit.XXXXXX' 2>/dev/null); then
        if printf '%s\n' "$value" > "$tmp" 2>/dev/null && /usr/bin/mv --force --no-target-directory -- "$tmp" "$STATE_FILE" 2>/dev/null; then
            return 0
        fi
        /usr/bin/rm -f -- "$tmp" 2>/dev/null || true
    fi

    return 1
}

read_state_value() {
    local value=''

    [[ -r "$STATE_FILE" ]] || return 1
    IFS= read -r value < "$STATE_FILE" 2>/dev/null || return 1
    [[ $value =~ ^(0|[1-9][0-9]{0,17})$ ]] || return 1

    printf '%s' "$value"
    return 0
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
    if [[ ! -x /usr/bin/stty ]]; then
        printf 'ERROR: Required command not found: stty\n' >&2
        return 1
    fi

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

robust_fetch() {
    mkdir -p -- "$STATE_DIR" 2>/dev/null || return 1
    exec {GLOBAL_LOCK_FD}> "$LOCK_FILE" || return 1
    if ! /usr/bin/flock --nonblocking "$GLOBAL_LOCK_FD"; then
        FETCH_INFO='Another update check is running'
        return 1
    fi
    if git_fetch; then
        FETCH_INFO='Fetched main'
        return 0
    fi
    FETCH_INFO='Fetch failed; keeping the last known count'
    return 1
}

# State locks are held only for local reads/writes, never during network I/O.
# The generation token stops an older worker from undoing an updater reset.
read_generation() {
    local value=''
    if [[ -f $GENERATION_FILE ]]; then
        IFS= read -r value < "$GENERATION_FILE" || return 1
    fi
    printf '%s' "$value"
}

reset_commits() {
    local fd
    mkdir -p -- "$STATE_DIR" || return 0
    exec {fd}> "$STATE_LOCK_FILE" || return 0
    /usr/bin/flock --wait 1 "$fd" || return 0
    printf '%s\n' "$EPOCHREALTIME-$BASHPID-$RANDOM" > "$GENERATION_FILE" || return 0
    write_state_file 0 || true
    _debug 'Commit count reset without network access'
    exec {fd}>&-
}

run_background_check() {
    local generation='' current_generation='' previous_state='' fd
    local -i count=0
    mkdir -p -- "$STATE_DIR" || return 0
    exec {fd}> "$STATE_LOCK_FILE" || return 0
    /usr/bin/flock --wait 1 "$fd" || return 0
    generation=$(read_generation) || return 0
    exec {fd}>&-

    validate_environment || { _debug 'Invalid repository; preserving state'; return 0; }
    robust_fetch || { _debug "$FETCH_INFO"; return 0; }
    # Compare local main even when a developer has another branch checked out.
    _git_rev_count count "refs/heads/main..${UPSTREAM_REF}" || {
        _debug 'Cannot compare local main; preserving state'
        return 0
    }

    exec {fd}> "$STATE_LOCK_FILE" || return 0
    /usr/bin/flock --wait 1 "$fd" || return 0
    current_generation=$(read_generation) || return 0
    [[ $generation == "$current_generation" ]] || {
        _debug 'Reset superseded this check; discarding result'
        return 0
    }
    previous_state=$(read_state_value) || previous_state=0
    write_state_file "$count" || return 0
    _debug "main is $count commits behind"
    exec {fd}>&-

    if (( count >= NOTIFY_THRESHOLD && previous_state < NOTIFY_THRESHOLD )) &&
       [[ -x /usr/bin/notify-send ]]; then
        /usr/bin/notify-send -u normal -t 5000 -i software-update-available \
            'Dusky Dotfiles' "Update available: main is ${count} commits behind." \
            >/dev/null 2>&1 || true
    fi
}

parse_arguments() {
    local arg
    for arg in "$@"; do
        case $arg in
            --num) ;; # Compatibility alias for the default background check.
            --reset-commits) MODE=reset ;;
            --tui) MODE=tui ;;
            --wait) WAIT=1 ;;
            --worker) WORKER=1 ;; # Internal entry point, supervised by timeout.
            --debug) DEBUG=1 ;;
            --help|-h)
                printf '%s\n' \
                    "Usage: ${0##*/} [--num | --reset-commits | --tui] [--wait] [--debug]" \
                    'Default / --num: detach and check origin/main; exit 0 immediately.' \
                    '--reset-commits: detach and atomically write 0; no Git/network calls.' \
                    '--tui: open the interactive main-branch commit viewer.' \
                    '--wait: wait for the bounded worker (for systemd ordering).' \
                    '--debug: print diagnostics with --wait or --tui.' \
                    'Background workers have a 30-second hard deadline; failures preserve state.'
                exit 0 ;;
            *) printf 'Unknown option: %s\n' "$arg" >&2; exit 0 ;;
        esac
    done
}

launch_worker() {
    local -a args=(--worker)
    [[ $MODE == reset ]] && args+=(--reset-commits)
    (( DEBUG )) && args+=(--debug)
    if (( WAIT )); then
        /usr/bin/timeout --signal=KILL "$TIMEOUT_SEC" \
            /usr/bin/bash -- "${BASH_SOURCE[0]}" "${args[@]}" </dev/null || true
    else
        (
            trap '' HUP
            # Do not keep callers' pipes, flock descriptors, or PTYs alive.
            local path fd
            for path in /proc/"$BASHPID"/fd/*; do
                fd=${path##*/}
                if (( fd > 2 && fd != 255 )); then
                    exec {fd}>&- || true
                fi
            done
            exec /usr/bin/setsid --fork /usr/bin/timeout --signal=KILL "$TIMEOUT_SEC" \
                /usr/bin/bash -- "${BASH_SOURCE[0]}" "${args[@]}"
        ) </dev/null >/dev/null 2>&1 &
    fi
    return 0
}

# =============================================================================
# DATA LOADING
# =============================================================================

load_commits() {
    COMMIT_HASHES=()
    COMMIT_MSGS=()
    TOTAL_COMMITS=0
    BEHIND_COUNT=0
    LOCAL_REV=0
    REMOTE_REV=0

    if ! _git_rev_count LOCAL_REV refs/heads/main; then
        COMMIT_HASHES=("ERR")
        COMMIT_MSGS=("Failed to read local revision count")
        TOTAL_COMMITS=1
        FETCH_STATUS="GIT_ERROR"
        LOCAL_REV=0
        REMOTE_REV=0
        return 0
    fi

    if [[ $FETCH_STATUS == FAIL ]]; then
        REMOTE_REV=0
        COMMIT_HASHES=("ERR")
        COMMIT_MSGS=("Fetch failed - cannot verify remote status")
        TOTAL_COMMITS=1
        return 0
    fi

    local upstream=$UPSTREAM_REF

    if ! _git_rev_count REMOTE_REV "$upstream"; then
        COMMIT_HASHES=("ERR")
        COMMIT_MSGS=("Failed to read upstream revision count")
        TOTAL_COMMITS=1
        FETCH_STATUS="GIT_ERROR"
        REMOTE_REV=0
        return 0
    fi

    local -i count=0
    if ! _git_rev_count count "refs/heads/main..${upstream}"; then
        COMMIT_HASHES=("ERR")
        COMMIT_MSGS=("Failed to compare local main against ${upstream}")
        TOTAL_COMMITS=1
        FETCH_STATUS="GIT_ERROR"
        return 0
    fi

    _debug "load_commits: HEAD=$LOCAL_REV, upstream=$REMOTE_REV, behind=$count"
    BEHIND_COUNT=$count

    if (( count == 0 )); then
        COMMIT_HASHES=("HEAD")
        COMMIT_MSGS=("Dusky is up to date!")
        TOTAL_COMMITS=1
        return 0
    fi

    local -ri max_len=$(( BOX_INNER_WIDTH - ITEM_PADDING - 6 ))
    local -a raw_commits=()
    local line='' hash='' msg='' safe_msg=''

    # Fetch up to 500 behind commits so scrolling works smoothly without clipping at 14
    mapfile -t raw_commits < <(
        "${GIT_CMD[@]}" --no-pager log --max-count=500 "refs/heads/main..${upstream}" \
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

    if (( ${#COMMIT_HASHES[@]} == 0 )); then
        COMMIT_HASHES=("WARN")
        COMMIT_MSGS=("Detected $count updates but log was empty")
        TOTAL_COMMITS=1
    else
        # TOTAL_COMMITS matches the exact number of entries in COMMIT_HASHES,
        # ensuring array indexing in draw_ui and nav functions never goes out of bounds.
        TOTAL_COMMITS=${#COMMIT_HASHES[@]}
    fi

    return 0
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
    return 0
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
                    local -i display_count=${BEHIND_COUNT:-$TOTAL_COMMITS}
                    stats="${C_YELLOW}Commits Behind: ${display_count}${C_RESET}"
                    plain_stats="Commits Behind: ${display_count}"
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
        h="${COMMIT_HASHES[i]:-}"
        m="${COMMIT_MSGS[i]:-}"
        [[ -n "$h" ]] || continue
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
    return 0
}

# =============================================================================
# NAVIGATION
# =============================================================================

nav_step() {
    local -i d=$1
    (( TOTAL_COMMITS == 0 )) && return 0
    SELECTED_ROW=$(( (SELECTED_ROW + d + TOTAL_COMMITS) % TOTAL_COMMITS ))
    return 0
}

nav_page() {
    local -i d=$1
    (( TOTAL_COMMITS == 0 )) && return 0
    SELECTED_ROW=$(( SELECTED_ROW + d * MAX_DISPLAY_ROWS ))
    if (( SELECTED_ROW < 0 )); then
        SELECTED_ROW=0
    elif (( SELECTED_ROW >= TOTAL_COMMITS )); then
        SELECTED_ROW=$(( TOTAL_COMMITS - 1 ))
    fi
    return 0
}

nav_edge() {
    (( TOTAL_COMMITS == 0 )) && return 0
    case ${1:-} in
        home) SELECTED_ROW=0 ;;
        end)  SELECTED_ROW=$(( TOTAL_COMMITS - 1 )) ;;
    esac
    return 0
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
    return 0
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    parse_arguments "$@"
    if (( WORKER )); then
        if [[ $MODE == reset ]]; then
            reset_commits
        else
            run_background_check
        fi
        exit 0
    fi
    if [[ $MODE != tui ]]; then
        launch_worker
        exit 0
    fi

    if ! validate_environment; then
        _debug "validate_environment failed in main"
        exit 0
    fi

    if ! validate_terminal; then
        _debug "validate_terminal failed in main (non-interactive)"
        exit 0
    fi

    printf '\n%sFetching updates...%s\n' "$C_CYAN" "$C_RESET"

    if ! robust_fetch; then
        printf '%s[WARNING] Fetch failed: %s%s\n' "$C_YELLOW" "$FETCH_INFO" "$C_RESET"
        FETCH_STATUS="FAIL"
        _sleep 0.5
    else
        printf '%s[OK] %s%s\n' "$C_GREEN" "$FETCH_INFO" "$C_RESET"
        _sleep 0.25
    fi

    load_commits
    if (( GLOBAL_LOCK_FD >= 0 )); then
        exec {GLOBAL_LOCK_FD}>&-
        GLOBAL_LOCK_FD=-1
    fi

    ORIGINAL_STTY=$(/usr/bin/stty -g 2>/dev/null) || exit 0
    /usr/bin/stty -icanon -echo || exit 0
    printf '%s%s%s%s' "$MOUSE_ON" "$CUR_HIDE" "$CLR_SCREEN" "$CUR_HOME"
    TUI_ACTIVE=1

    local key='' seq='' ch=''
    local -i ui_ok=0
    local -i read_rc=0

    while true; do
        if terminal_fits_ui; then
            ui_ok=1
            draw_ui
        else
            ui_ok=0
            draw_terminal_too_small
        fi

        key=''
        read_rc=0
        IFS= read -rsn1 -t 1 key || read_rc=$?
        if (( read_rc > 128 )); then
            continue
        elif (( read_rc != 0 )); then
            break
        fi

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
                    '['*'<'*[Mm])
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

    exit 0
}

main "$@"
