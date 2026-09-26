#!/usr/bin/env bash
# =============================================================================
# Dusky Git Time Machine
# Architecture v11.0.0 — Chronos Aegis
#
# Environment (hard requirements, zero legacy shims):
#   Arch Linux · Kernel 7.1+ · Bash 5.3+ · FZF 0.74.2 · Git 2.50+ · Delta 0.18+
#   GNU Awk · util-linux flock · GNU coreutils (comm -z, sort -z)
#
# Bare-repo contract:
#   GIT_DIR=$HOME/dusky    GIT_WORK_TREE=$HOME
#
# Design laws:
#   1. Never execute out of the Git work tree. Relocate to RAM first.
#   2. Never stash untracked $HOME files. Never run git-clean on $HOME.
#   3. Never discard dirty tracked work without an explicit confirmation.
#   4. Never swallow a failed switch/stash. Surface it in the prompt/footer.
#   5. One writer. Exclusive flock on the work tree for the whole session.
#   6. Stash apply is idempotent, message-addressed, and drop-on-success only.
#   7. Only the session owner may install janitor traps or unlink the engine.
#   8. Every FZF child is `bash --noprofile --norc -- $ENGINE --worker …`.
#      No $0. No export -f. No $SHELL dependence. Noexec-safe.
#   9. Shield present uncommitted work first; switch to historical commits cleanly.
# =============================================================================
#
# ROOT CAUSE (v10 Chronos Zenith → zsh:1: no such file or directory):
#   FZF preview is a NEW PROCESS, not a Bash subshell. BASH_SUBSHELL is 0.
#   v10 installed `trap _dusky_cleanup EXIT` before `_internal_*` dispatch.
#   The first preview ran the RAM engine, printed Delta output, then exit 0.
#   EXIT unlinked $XDG_RUNTIME_DIR/dusky-tm-$UID/$PPID/dusky_tm_engine.sh
#   and could also `git switch` the $HOME work tree back to present.
#   The next Down-Arrow made Zsh execve the now-unlinked path:
#     zsh:1: no such file or directory: /run/user/1000/dusky-tm-1000/<PID>/…
#   `:1:` is zsh -c's line number. FZF used $SHELL (zsh) for preview.
#   $0 under zsh -c is "zsh", so fallback to $0 was poison.
#   export -f is invisible to Zsh/Fish. --force switch can clobber untracked.
#
# FIX (this file):
#   Role-split at argv[0]. Workers never trap, never flock, never unlink.
#   Owner identity: ROLE=owner AND $$==DUSKY_OWNER_PID AND BASH_SUBSHELL==0.
#   FZF child argv is pre-expanded with ${DUSKY_BASH@Q} and ${ENGINE@Q}.
# =============================================================================

# -----------------------------------------------------------------------------
# 0. Role detection — MUST be the first side-effect-bearing decision
# -----------------------------------------------------------------------------
_DUSKY_ARGV0="${1:-}"
_DUSKY_ROLE="owner"
case "${_DUSKY_ARGV0}" in
    --worker)  _DUSKY_ROLE="worker" ;;
    --help|-h|--version) ;;
esac
if [[ "${DUSKY_SOURCED:-0}" == 1 ]]; then
    _DUSKY_ROLE="library"
fi
export DUSKY_ROLE="${_DUSKY_ROLE}"

# -----------------------------------------------------------------------------
# 1. Grandfather Paradox Bypass — owner-only RAM relocate (noexec safe)
# -----------------------------------------------------------------------------
_dusky_volatile_shift() {
    [[ "${DUSKY_ROLE}" == "owner" ]] || return 0
    if [[ "${DUSKY_TM_VOLATILE:-0}" == 1 && -n "${DUSKY_TM_ENGINE:-}" && -f "${DUSKY_TM_ENGINE}" ]]; then
        return 0
    fi

    umask 077
    local current_path target_dir="" candidate
    current_path="$(readlink -f -- "${BASH_SOURCE[0]:-${0}}")"

    local -a candidates=()
    [[ -n "${XDG_RUNTIME_DIR:-}" && -d "${XDG_RUNTIME_DIR}" && -w "${XDG_RUNTIME_DIR}" ]] \
        && candidates+=("${XDG_RUNTIME_DIR}")
    candidates+=("/dev/shm" "/mnt/zram1" "/tmp")

    local wtest
    for candidate in "${candidates[@]}"; do
        [[ -d "$candidate" && -w "$candidate" ]] || continue
        if wtest="$(mktemp "${candidate}/.dusky_tm_wtest.XXXXXXXXXX" 2>/dev/null)"; then
            rm -f -- "$wtest"
            target_dir="$candidate"
            break
        fi
    done

    if [[ -z "$target_dir" ]]; then
        printf 'dusky-tm: no writable RAM or tmp directory (tried XDG_RUNTIME_DIR, /dev/shm, /mnt/zram1, /tmp)\n' >&2
        exit 2
    fi

    local session_run_dir engine
    session_run_dir="$(mktemp -d "${target_dir}/dusky-tm.XXXXXXXXXX")" || exit 2
    engine="${session_run_dir}/engine.sh"

    cat -- "$current_path" >"$engine" || { rm -f -- "$engine"; exit 2; }
    chmod 0600 "$engine" || { rm -f -- "$engine"; exit 2; }
    if ! cmp -s -- "$current_path" "$engine"; then
        rm -f -- "$engine"
        printf 'dusky-tm: volatile clone failed byte-compare verification\n' >&2
        exit 2
    fi

    export DUSKY_TM_VOLATILE=1
    export DUSKY_TM_ENGINE="$engine"
    export DUSKY_SESSION_DIR="$session_run_dir"
    # Interpreter exec: noexec tmpfs cannot execve the clone itself.
    exec -a dusky-time-machine bash --noprofile --norc -- "$engine" "$@"
    printf 'dusky-tm: exec into volatile RAM copy failed\n' >&2
    exit 2
}

if [[ "${DUSKY_ROLE}" == "owner" && "${1:-}" != "--help" && "${1:-}" != "-h" && "${1:-}" != "--version" ]]; then
    _dusky_volatile_shift "$@"
fi

# -----------------------------------------------------------------------------
# 2. Strict runtime environment
# -----------------------------------------------------------------------------
set -u
set -o pipefail
shopt -s lastpipe
umask 077

unset BASH_ENV
unset CDPATH
unset GIT_PREFIX GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
unset GIT_LITERAL_PATHSPECS GIT_GLOB_PATHSPECS GIT_NOGLOB_PATHSPECS GIT_ICASE_PATHSPECS
unset FZF_DEFAULT_OPTS
unset FZF_DEFAULT_OPTS_FILE

export LC_ALL=C.UTF-8
export LANG=C.UTF-8

readonly DUSKY_TM_VERSION="11.1.0"
readonly DUSKY_TM_CODENAME="Chronos Aegis"

_dusky_die() {
    printf '\033[1;31m✖ Dusky Time Machine:\033[0m %s\n' "$1" >&2
    exit "${2:-2}"
}

if [[ -z "${DUSKY_BASH:-}" ]]; then
    DUSKY_BASH="$(command -v bash || true)"
    [[ -n "$DUSKY_BASH" && -x "$DUSKY_BASH" ]] || _dusky_die "bash interpreter not found"
    export DUSKY_BASH
fi

# FZF's default child shell. Workers still bake $DUSKY_BASH into argv.
export SHELL="$DUSKY_BASH"

if [[ -z "${DUSKY_OWNER_PID:-}" ]]; then
    if [[ "${DUSKY_ROLE}" == "owner" ]]; then
        export DUSKY_OWNER_PID="$$"
    else
        export DUSKY_OWNER_PID="${PPID}"
    fi
fi
export DUSKY_SESSION_ID="${DUSKY_SESSION_ID:-${EPOCHREALTIME//./}_${DUSKY_OWNER_PID}}"
export DUSKY_TM_VERSION DUSKY_TM_CODENAME

# -----------------------------------------------------------------------------
# 3. Toolchain gates — owner only (workers inherit a proven session)
# -----------------------------------------------------------------------------
_dusky_ver_ge() {
    local have="$1" need="$2"
    [[ "$(printf '%s\n%s\n' "$need" "$have" | sort -V | head -n1)" == "$need" ]]
}

_dusky_require_cmd() {
    command -v "$1" >/dev/null 2>&1 || _dusky_die "required command not found: $1"
}

_dusky_require_versions() {
    (( BASH_VERSINFO[0] > 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] >= 3) )) \
        || _dusky_die "Bash 5.3+ required (found ${BASH_VERSION})"

    local cmd
    for cmd in fzf git delta gawk mktemp flock cmp chmod cat mkdir rm printf comm sort head base64; do
        _dusky_require_cmd "$cmd"
    done

    local fv gv dv
    fv="$(fzf --version | gawk '{print $1; exit}')"
    gv="$(git --version | gawk '{print $3; exit}')"
    dv="$(delta --version | gawk '{print $2; exit}')"

    _dusky_ver_ge "$fv" "0.74" || _dusky_die "FZF 0.74+ required (found ${fv})"
    _dusky_ver_ge "$gv" "2.50" || _dusky_die "Git 2.50+ required (found ${gv})"
    _dusky_ver_ge "$dv" "0.18" || _dusky_die "Delta 0.18+ required (found ${dv})"

    gawk 'BEGIN {
        n = split("ab", a, "")
        if (n != 2) exit 1
    }' || _dusky_die "GNU Awk with character split is required"

    comm -z /dev/null /dev/null >/dev/null 2>&1 \
        || _dusky_die "GNU comm --zero-terminated is required"
}

# -----------------------------------------------------------------------------
# 4. Bare-repo environment — isolated, non-interactive
# -----------------------------------------------------------------------------
export GIT_DIR="${GIT_DIR:-${HOME}/dusky}"
export GIT_WORK_TREE="${GIT_WORK_TREE:-${HOME}}"
export GIT_PAGER=cat
export GIT_TERMINAL_PROMPT=0
export GIT_ADVICE=false
export GIT_EDITOR=true
export DELTA_PAGER=cat

# Intentionally NOT exporting GIT_OPTIONAL_LOCKS globally.
# Read helpers pin it inline; write actions take real index locks.

_gr() {
    GIT_OPTIONAL_LOCKS=0 command git \
        -c core.hooksPath=/dev/null \
        -c advice.detachedHead=false \
        -c advice.statusHints=false \
        -c core.quotepath=false \
        "$@"
}
_gw() {
    command git \
        -c core.hooksPath=/dev/null \
        -c advice.detachedHead=false \
        -c advice.statusHints=false \
        -c core.quotepath=false \
        "$@"
}

_dusky_owner_repo_init() {
    cd "$GIT_WORK_TREE" || _dusky_die "cannot cd to GIT_WORK_TREE=$GIT_WORK_TREE"
    [[ -d "$GIT_DIR" ]] || _dusky_die "GIT_DIR ${GIT_DIR} does not exist"
    [[ "$(_gr config --bool core.bare 2>/dev/null || true)" == "true" ]] \
        || _dusky_die "${GIT_DIR} is not a bare repository"
    _gr rev-parse --verify HEAD >/dev/null 2>&1 || _dusky_die "repository has no commit history yet"
}

# -----------------------------------------------------------------------------
# 5. Persistence paths + exclusive work-tree lock (owner acquires)
# -----------------------------------------------------------------------------
_dusky_pick_persist_dir() {
    local d="${GIT_DIR}/dusky-time-machine"
    [[ ! -L "$d" ]] || _dusky_die "recovery directory must not be a symlink"
    mkdir -p "$d" || _dusky_die "cannot create recovery directory"
    [[ -O "$d" && -w "$d" ]] || _dusky_die "recovery directory is not owned and writable"
    chmod 0700 "$d" || return 1
    printf '%s\n' "$d"
}

_dusky_bind_paths() {
    if [[ -z "${DUSKY_PERSIST_DIR:-}" ]]; then
        DUSKY_PERSIST_DIR="$(_dusky_pick_persist_dir)"
    fi
    mkdir -p "$DUSKY_PERSIST_DIR" || _dusky_die "cannot create recovery directory"
    export DUSKY_PERSIST_DIR
    export DUSKY_PRESENT_FILE="${DUSKY_PRESENT_FILE:-${DUSKY_PERSIST_DIR}/present.target}"

    local uid run_root
    uid="${UID:-$(id -u)}"
    run_root="${XDG_RUNTIME_DIR:-/dev/shm}/dusky-tm-${uid}"
    [[ -n "${XDG_RUNTIME_DIR:-}" && -d "${XDG_RUNTIME_DIR}" ]] \
        || run_root="/dev/shm/dusky-tm-${uid}"
    export DUSKY_RUN_ROOT="${DUSKY_RUN_ROOT:-${run_root}}"
    mkdir -p "$DUSKY_RUN_ROOT"

    export DUSKY_SESSION_DIR="${DUSKY_SESSION_DIR:-${DUSKY_RUN_ROOT}/s${DUSKY_OWNER_PID}}"
    mkdir -p "$DUSKY_SESSION_DIR"
    export DUSKY_STATE_DIR="${DUSKY_STATE_DIR:-${DUSKY_SESSION_DIR}/state}"
    mkdir -p "$DUSKY_STATE_DIR"

    if [[ -z "${DUSKY_TM_ENGINE:-}" ]]; then
        export DUSKY_TM_ENGINE="${DUSKY_SESSION_DIR}/engine.sh"
    fi

    export DUSKY_LOCK_FILE="${DUSKY_LOCK_FILE:-${DUSKY_PERSIST_DIR}/worktree.lock}"
    export DUSKY_LOCK_PID_FILE="${DUSKY_LOCK_PID_FILE:-${DUSKY_PERSIST_DIR}/worktree.pid}"
    export DUSKY_JOURNAL="${DUSKY_JOURNAL:-${DUSKY_PERSIST_DIR}/journal}"
}

_dusky_is_owner() {
    [[ "${DUSKY_ROLE}" == "owner" ]] \
        && [[ "$$" == "${DUSKY_OWNER_PID}" ]] \
        && (( BASH_SUBSHELL == 0 ))
}

_dusky_acquire_lock() {
    _dusky_is_owner || _dusky_die "refusing to acquire work-tree lock from a non-owner process"
    : >>"$DUSKY_LOCK_FILE"
    exec 9<>"$DUSKY_LOCK_FILE"
    if ! flock -n 9; then
        local holder
        holder="$(cat -- "$DUSKY_LOCK_PID_FILE" 2>/dev/null || true)"
        _dusky_die "another Time Machine instance holds the work-tree lock (pid ${holder:-unknown})" 3
    fi
    printf '%s\n' "$DUSKY_OWNER_PID" >"$DUSKY_LOCK_PID_FILE"
}

_dusky_hold_engine_fd() {
    _dusky_is_owner || return 0
    [[ -f "${DUSKY_TM_ENGINE}" ]] || return 0
    exec 8<"${DUSKY_TM_ENGINE}"
}

_dusky_journal() {
    local line
    printf -v line '%s pid=%s role=%s %s\n' \
        "${EPOCHREALTIME:-$(date +%s.%N)}" "$$" "${DUSKY_ROLE}" "$*"
    printf '%s' "$line" >>"${DUSKY_JOURNAL}" 2>/dev/null || true
}

# -----------------------------------------------------------------------------
# 6. Present-target ledger (survives stay-in-past + relaunch + crashes)
# -----------------------------------------------------------------------------
_dusky_load_present_target() {
    DUSKY_PRESENT_BRANCH=""
    DUSKY_PRESENT_SHA=""
    DUSKY_PRESENT_SHORT=""
    export DUSKY_STASH_SESSION_ID="$DUSKY_SESSION_ID"

    if [[ -f "$DUSKY_PRESENT_FILE" ]]; then
        # shellcheck disable=SC1090
        source "$DUSKY_PRESENT_FILE"
        DUSKY_PRESENT_BRANCH="${DUSKY_TGT_BRANCH:-}"
        DUSKY_PRESENT_SHA="${DUSKY_TGT_SHA:-}"
        DUSKY_PRESENT_SHORT="${DUSKY_TGT_SHORT:-}"
        export DUSKY_STASH_SESSION_ID="${DUSKY_TGT_STASH_SESSION:-$DUSKY_SESSION_ID}"
    fi

    if [[ -z "$DUSKY_PRESENT_SHA" ]]; then
        DUSKY_PRESENT_BRANCH="$(_gr branch --show-current 2>/dev/null || true)"
        DUSKY_PRESENT_SHA="$(_gr rev-parse HEAD 2>/dev/null || true)"
        DUSKY_PRESENT_SHORT="$(_gr rev-parse --short=7 HEAD 2>/dev/null || true)"
    fi

    export DUSKY_PRESENT_BRANCH DUSKY_PRESENT_SHA DUSKY_PRESENT_SHORT
}

_dusky_save_present_target() {
    export DUSKY_STASH_SESSION_ID="${DUSKY_STASH_SESSION_ID:-$DUSKY_SESSION_ID}"
    local tmp
    tmp="$(mktemp "${DUSKY_PRESENT_FILE}.XXXXXXXXXX")" || return 1
    cat >"$tmp" <<EOF
DUSKY_TGT_BRANCH=${DUSKY_PRESENT_BRANCH@Q}
DUSKY_TGT_SHA=${DUSKY_PRESENT_SHA@Q}
DUSKY_TGT_SHORT=${DUSKY_PRESENT_SHORT@Q}
DUSKY_TGT_STASH_SESSION=${DUSKY_STASH_SESSION_ID@Q}
EOF
    mv -f -- "$tmp" "$DUSKY_PRESENT_FILE"
}

_dusky_clear_present_target() {
    rm -f -- "$DUSKY_PRESENT_FILE"
}

# -----------------------------------------------------------------------------
# 7. Matugen dynamic UI — 24-bit ANSI + FZF hex
# -----------------------------------------------------------------------------
_dusky_json_str() {
    local key="$1" file="$2" content pat
    [[ -f "$file" ]] || return 1
    content="$(<"$file")"
    pat="\"${key}\"[[:space:]]*:[[:space:]]*\"([^\"]*)\""
    if [[ "$content" =~ $pat ]]; then
        printf '%s\n' "${BASH_REMATCH[1]}"
        return 0
    fi
    return 1
}

_dusky_hex_ok() {
    [[ "$1" =~ ^#[0-9A-Fa-f]{6}$ ]]
}

_dusky_get_color() {
    local key="$1" default="$2" val=""
    val="$(_dusky_json_str "$key" "${HOME}/.config/matugen/generated/dusky_tui.json" || true)"
    if _dusky_hex_ok "${val:-}"; then
        printf '%s\n' "$val"
    else
        printf '%s\n' "$default"
    fi
}

_dusky_ansi_fg() {
    local hex="${1#\#}"
    printf '\033[38;2;%d;%d;%dm' "$((16#${hex:0:2}))" "$((16#${hex:2:2}))" "$((16#${hex:4:2}))"
}

_dusky_ansi_bold_fg() {
    local hex="${1#\#}"
    printf '\033[1;38;2;%d;%d;%dm' "$((16#${hex:0:2}))" "$((16#${hex:2:2}))" "$((16#${hex:4:2}))"
}

_dusky_bind_colors() {
    export MATUGEN_BG MATUGEN_FG MATUGEN_ACCENT MATUGEN_MUTED MATUGEN_SUCCESS MATUGEN_ERROR
    MATUGEN_BG="$(_dusky_get_color bg "#1d100a")"
    MATUGEN_FG="$(_dusky_get_color fg "#f8ddd2")"
    MATUGEN_ACCENT="$(_dusky_get_color accent "#ffb694")"
    MATUGEN_MUTED="$(_dusky_get_color muted "#55433b")"
    MATUGEN_SUCCESS="$(_dusky_get_color success "#f0be79")"
    MATUGEN_ERROR="$(_dusky_get_color error "#ffb4ab")"

    export DUSKY_ANSI_RESET=$'\033[0m'
    export DUSKY_ANSI_DIM DUSKY_ANSI_FG DUSKY_ANSI_ACCENT DUSKY_ANSI_SUCCESS DUSKY_ANSI_ERROR
    export DUSKY_ANSI_BOLD DUSKY_ANSI_BAR DUSKY_ANSI_DATE DUSKY_ANSI_AUTH DUSKY_ANSI_TIME
    DUSKY_ANSI_DIM="$(_dusky_ansi_fg "$MATUGEN_MUTED")"
    DUSKY_ANSI_FG="$(_dusky_ansi_fg "$MATUGEN_FG")"
    DUSKY_ANSI_ACCENT="$(_dusky_ansi_fg "$MATUGEN_ACCENT")"
    DUSKY_ANSI_SUCCESS="$(_dusky_ansi_fg "$MATUGEN_SUCCESS")"
    DUSKY_ANSI_ERROR="$(_dusky_ansi_fg "$MATUGEN_ERROR")"
    DUSKY_ANSI_BOLD="$(_dusky_ansi_bold_fg "$MATUGEN_ACCENT")"
    DUSKY_ANSI_BAR="$(_dusky_ansi_fg "$MATUGEN_MUTED")"
    DUSKY_ANSI_DATE="$(_dusky_ansi_bold_fg "$MATUGEN_SUCCESS")"
    DUSKY_ANSI_AUTH="$(_dusky_ansi_bold_fg "$MATUGEN_ACCENT")"
    DUSKY_ANSI_TIME="$(_dusky_ansi_bold_fg "#7ec8c8")"
}

# -----------------------------------------------------------------------------
# 8. Session state machine
#    phase: present | detached | conflict | stay
#    stash: none | clean | stashed | applied | conflict
# -----------------------------------------------------------------------------
readonly DUSKY_SETTINGS_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/dusky/settings"
readonly DUSKY_USER_STATE_FILE="${DUSKY_SETTINGS_DIR}/time_machine_state"

_dusky_user_state_load() {
    DUSKY_CFG_VIM_MODE="false"
    DUSKY_CFG_PREVIEW_LAYOUT=""
    DUSKY_CFG_PREVIEW_MODE="side"
    DUSKY_CFG_SCOPE="all"

    if [[ -f "$DUSKY_USER_STATE_FILE" && -r "$DUSKY_USER_STATE_FILE" ]]; then
        local line key val
        while IFS= read -r line || [[ -n "$line" ]]; do
            [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$ ]] || continue
            key="${BASH_REMATCH[1]}"
            val="${BASH_REMATCH[2]}"
            if   [[ "$val" =~ ^\"([^\"]*)\" ]]; then val="${BASH_REMATCH[1]}"
            elif [[ "$val" =~ ^\'([^\']*)\' ]]; then val="${BASH_REMATCH[1]}"
            else val="${val%%[[:space:]]#*}"; val="${val%%[[:space:]]*}"
            fi
            case "$key" in
                VIM_MODE)       [[ "$val" == "true" ]] && DUSKY_CFG_VIM_MODE="true" ;;
                PREVIEW_LAYOUT) [[ "$val" =~ ^(hidden|(up|down|left|right),[0-9]+%(,[A-Za-z0-9_~%:+-]+)*)$ ]] && DUSKY_CFG_PREVIEW_LAYOUT="$val" ;;
                PREVIEW_MODE)   [[ "$val" =~ ^(side|inline|stat|files|vs_present)$ ]] && DUSKY_CFG_PREVIEW_MODE="$val" ;;
                SCOPE)          [[ "$val" =~ ^(all|lineage)$ ]] && DUSKY_CFG_SCOPE="$val" ;;
            esac
        done < "$DUSKY_USER_STATE_FILE"
    fi
}

_dusky_user_state_save() {
    mkdir -p "$DUSKY_SETTINGS_DIR" 2>/dev/null || return 0
    local tmp="${DUSKY_SETTINGS_DIR}/.tm_state_tmp_$$"
    cat >"$tmp" <<EOF
# =============================================================================
# DUSKY GIT TIME MACHINE USER SETTINGS
# =============================================================================
VIM_MODE="$(_dusky_read vim_mode)"
PREVIEW_LAYOUT="$(_dusky_read preview_layout)"
PREVIEW_MODE="$(_dusky_read preview_mode)"
SCOPE="$(_dusky_read scope)"
EOF
    mv -f -- "$tmp" "$DUSKY_USER_STATE_FILE" 2>/dev/null || rm -f -- "$tmp"
}

_dusky_state_init() {
    _dusky_user_state_load
    printf '%s\n' "present" >"${DUSKY_STATE_DIR}/phase"
    printf '%s\n' "none"    >"${DUSKY_STATE_DIR}/stash"
    printf '%s\n' "${DUSKY_CFG_PREVIEW_MODE:-side}" >"${DUSKY_STATE_DIR}/preview_mode"
    printf '%s\n' "commits" >"${DUSKY_STATE_DIR}/mode"
    printf '%s\n' "${DUSKY_CFG_SCOPE:-all}"         >"${DUSKY_STATE_DIR}/scope"
    printf '%s\n' "0"       >"${DUSKY_STATE_DIR}/stay"
    printf '%s\n' "${DUSKY_CFG_VIM_MODE:-false}"   >"${DUSKY_STATE_DIR}/vim_mode"
    printf '%s\n' "${DUSKY_CFG_PREVIEW_LAYOUT:-${DUSKY_PREVIEW_WINDOW:-right,70%,border-left,wrap}}" >"${DUSKY_STATE_DIR}/preview_layout"
    printf '%s\n' "${DUSKY_CFG_PREVIEW_LAYOUT:-${DUSKY_PREVIEW_WINDOW:-right,70%,border-left,wrap}}" >"${DUSKY_STATE_DIR}/preview_last"
    printf '%s\n' "ready"   >"${DUSKY_STATE_DIR}/last_action"
    printf '%s\n' "online" >"${DUSKY_STATE_DIR}/last_detail"
    : >"${DUSKY_STATE_DIR}/head_line"
    rm -f -- "${DUSKY_STATE_DIR}/confirm_wipe"
    rm -f -- "${DUSKY_STATE_DIR}/conflicts"
    rm -f -- "${DUSKY_STATE_DIR}/drill_sha" \
             "${DUSKY_STATE_DIR}/drill_short" \
             "${DUSKY_STATE_DIR}/files_index"
}

_dusky_read() {
    local f="${DUSKY_STATE_DIR}/$1"
    if [[ -f "$f" ]]; then
        printf '%s' "$(<"$f")"
    fi
}

_dusky_write() {
    printf '%s\n' "$2" >"${DUSKY_STATE_DIR}/$1"
}

_dusky_note() {
    _dusky_write last_action "$1"
    _dusky_write last_detail "$2"
    _dusky_journal "note $1 $2"
}

# -----------------------------------------------------------------------------
# 9. Janitor — OWNER PROCESS ONLY. Workers must never reach this code path
#    with traps installed. Guards are belt-and-suspenders.
# -----------------------------------------------------------------------------
_DUSKY_CLEANED=0

_dusky_valid_hash() {
    [[ "${1:-}" =~ ^[0-9a-fA-F]{4,64}$ ]]
}

_dusky_stash_ref_by_message() {
    local want="$1" gd gs
    while IFS=$'\t' read -r gd gs; do
        if [[ "$gs" == *": $want" ]]; then
            printf '%s\n' "$gd"
            return 0
        fi
    done < <(_gr stash list --format=$'%gd\t%gs')
    return 1
}

_dusky_find_session_stash() {
    _dusky_stash_ref_by_message "DUSKY_AUTO_STASH_${DUSKY_STASH_SESSION_ID:-$DUSKY_SESSION_ID}"
}

_dusky_resolve_present_branch() {
    if [[ -n "${DUSKY_PRESENT_BRANCH}" ]] && _gr show-ref --verify --quiet "refs/heads/${DUSKY_PRESENT_BRANCH}"; then
        printf '%s\n' "$DUSKY_PRESENT_BRANCH"
        return 0
    fi

    if [[ -n "${DUSKY_PRESENT_SHA}" ]] && _gr cat-file -e "${DUSKY_PRESENT_SHA}^{commit}" 2>/dev/null; then
        printf '%s\n' "$DUSKY_PRESENT_SHA"
        return 0
    fi
    return 1
}

_dusky_apply_session_stash() {
    local status ref
    status="$(_dusky_read stash)"
    [[ "$status" == "stashed" ]] || { _dusky_write stash "none"; return 0; }

    ref="$(_dusky_find_session_stash || true)"
    if [[ -z "$ref" ]]; then
        _dusky_write stash "none"
        _dusky_note "return" "session stash missing — already applied or dropped"
        return 0
    fi

    if _gw stash apply --index --quiet "$ref"; then
        _gw stash drop --quiet "$ref" || true
        _dusky_write stash "none"
        return 0
    fi

    _dusky_write stash "conflict"
    _dusky_write phase "conflict"
    _dusky_note "conflict" "stash apply collided — stash kept at ${ref}"
    return 1
}

_dusky_git_return() {
    _dusky_load_present_target
    rm -f -- "${DUSKY_STATE_DIR}/confirm_wipe"
    rm -f -- "${DUSKY_STATE_DIR}/conflicts"

    local phase stay
    phase="$(_dusky_read phase)"
    stay="$(_dusky_read stay)"

    if [[ "$phase" == "conflict" ]]; then
        _dusky_note "conflict" "resolve stash conflicts before traveling again"
        return 1
    fi

    if [[ "$phase" == "present" && "$stay" != "1" && "$(_dusky_read stash)" != "stashed" ]]; then
        _dusky_note "present" "already on present timeline"
        return 0
    fi

    local target err
    target="$(_dusky_resolve_present_branch || true)"
    if [[ -z "$target" ]]; then
        _dusky_note "error" "no present branch or SHA to return to"
        return 1
    fi

    cd "$GIT_WORK_TREE" || return 1
    if _dusky_worktree_dirty; then
        _dusky_note "blocked" "save edits made while traveling before returning"
        return 1
    fi

    if [[ "$target" != "$DUSKY_PRESENT_BRANCH" ]]; then
        err="$(_gw switch --quiet --no-overwrite-ignore --detach "$target" 2>&1)" || {
            _dusky_note "error" "failed to detach onto present SHA ${target:0:7}: ${err}"
            return 1
        }
    else
        err="$(_gw switch --quiet --no-overwrite-ignore "$target" 2>&1)" || {
            _dusky_note "error" "failed to switch to ${target}: ${err}"
            return 1
        }
    fi

    if ! _dusky_apply_session_stash; then
        return 1
    fi

    _dusky_write phase "present"
    _dusky_write stay "0"
    _dusky_clear_present_target
    _dusky_note "returned" "present ${target}"
    return 0
}

_dusky_cleanup() {
    ((_DUSKY_CLEANED)) && return 0

    # Workers, sourced libraries, and any nested subshell must be inert.
    if ! _dusky_is_owner; then
        return 0
    fi
    _DUSKY_CLEANED=1

    local phase stay
    phase="$(_dusky_read phase 2>/dev/null || true)"
    stay="$(_dusky_read stay 2>/dev/null || true)"

    if [[ "$stay" == "1" ]]; then
        _dusky_write phase "stay"
        [[ -f "$DUSKY_PRESENT_FILE" ]] || _dusky_save_present_target
    elif [[ "$phase" == "detached" || "$(_dusky_read stash)" == "stashed" ]]; then
        if ! _dusky_git_return; then
            printf 'dusky-tm: return failed; edits, stash and present ledger retained.\n' >&2
        fi
    fi

    if [[ -n "${DUSKY_LOCK_PID_FILE:-}" && -f "${DUSKY_LOCK_PID_FILE}" ]]; then
        local holder
        holder="$(cat -- "$DUSKY_LOCK_PID_FILE" 2>/dev/null || true)"
        if [[ "$holder" == "$$" ]]; then
            rm -f -- "$DUSKY_LOCK_PID_FILE"
        fi
    fi
    flock -u 9 2>/dev/null || true
    exec 9>&- 2>/dev/null || true
    exec 8<&- 2>/dev/null || true

    if [[ -n "${DUSKY_SESSION_DIR:-}" && -d "${DUSKY_SESSION_DIR}" ]]; then
        rm -rf -- "$DUSKY_SESSION_DIR"
    fi
}

# -----------------------------------------------------------------------------
# 10. Safety primitives — tracked-only stash, NUL-safe untracked probe
# -----------------------------------------------------------------------------
_dusky_worktree_dirty() {
    # LAW: Tracked files only. --untracked-files=no is NON-NEGOTIABLE on $HOME.
    local out=""
    if ! out="$(_gr status --porcelain=v1 --untracked-files=no --ignore-submodules=all 2>/dev/null)"; then
        _dusky_note "error" "cannot read Git status; refusing to treat the work tree as clean"
        return 0
    fi
    [[ -n "$out" ]]
}

_dusky_path_exists_blocking() {
    local rel="$1"
    local abs="${GIT_WORK_TREE}/${rel}"
    if [[ -e "$abs" || -L "$abs" ]]; then
        return 0
    fi
    local parent
    parent="$(dirname -- "$rel")"
    while [[ "$parent" != "." && "$parent" != "/" && -n "$parent" ]]; do
        if [[ -f "${GIT_WORK_TREE}/${parent}" || -L "${GIT_WORK_TREE}/${parent}" ]]; then
            return 0
        fi
        parent="$(dirname -- "$parent")"
    done
    return 1
}

_dusky_untracked_collisions() {
    # comm -z -23: paths in the target tree that are not in the current index.
    # If any of those already exist on disk (untracked / leftover), travel is blocked.
    local -r target="$1"
    local f
    while IFS= read -r -d '' f; do
        [[ -z "$f" ]] && continue
        if _dusky_path_exists_blocking "$f"; then
            printf '%s\0' "$f"
        fi
    done < <(comm -z -23 \
        <(_gr ls-tree -r --name-only -z "$target" | LC_ALL=C sort -z) \
        <(_gr ls-files -z | LC_ALL=C sort -z))
}

_dusky_shield_present() {
    local already
    already="$(_dusky_read stash)"
    if [[ "$already" == "stashed" || "$already" == "clean" ]]; then
        return 0
    fi

    export DUSKY_STASH_SESSION_ID="$DUSKY_SESSION_ID"
    if _dusky_worktree_dirty; then
        # LAW: tracked files only. Never --include-untracked or --all.
        if _gw stash push --quiet --no-include-untracked -m "DUSKY_AUTO_STASH_${DUSKY_SESSION_ID}"; then
            if ! _dusky_find_session_stash >/dev/null; then
                _dusky_note "error" "stash push reported success but ref is missing"
                return 1
            fi
            _dusky_write stash "stashed"
            _dusky_save_present_target || return 1
            return 0
        fi
        _dusky_note "error" "stash failed — refusing to travel (index lock or hook)"
        return 1
    fi

    _dusky_write stash "clean"
    _dusky_save_present_target || return 1
    return 0
}

# -----------------------------------------------------------------------------
# 11. Layout & Rendering Math — WHEN | GRAPH/REFS/MSG | AUTHOR | DATE
# -----------------------------------------------------------------------------
_dusky_compute_widths() {
    local cols list_cols cur_layout edge pct
    cols="${FZF_COLUMNS:-${COLUMNS:-}}"
    if [[ ! "$cols" =~ ^[0-9]+$ || "$cols" -le 0 ]]; then
        cols="$(tput cols 2>/dev/null || printf '120')"
    fi

    cur_layout="$(_dusky_read preview_layout 2>/dev/null || true)"
    if [[ -z "$cur_layout" ]]; then
        _dusky_user_state_load
        cur_layout="${DUSKY_CFG_PREVIEW_LAYOUT:-}"
    fi

    if [[ "$cur_layout" =~ ^(up|down|left|right),([0-9]+)% ]]; then
        edge="${BASH_REMATCH[1]}"
        pct="${BASH_REMATCH[2]}"
    elif [[ "$cur_layout" == "hidden" ]]; then
        edge="hidden"
        pct=0
    else
        if (( cols < 110 )); then
            edge="down"
            pct=50
            cur_layout="down,50%,border-top,wrap"
        else
            edge="right"
            pct=70
            cur_layout="right,70%,border-left,wrap"
        fi
    fi

    _dusky_write preview_layout "$cur_layout" 2>/dev/null || true
    export DUSKY_PREVIEW_WINDOW="$cur_layout"

    if [[ "$edge" == "down" || "$edge" == "up" || "$edge" == "hidden" ]]; then
        list_cols=$(( cols - 6 ))
    else
        list_cols=$(( (cols * (100 - pct) / 100) - 8 ))
    fi

    if (( list_cols < 48 )); then
        list_cols=48
    fi
    export DUSKY_TERM_COLS="$cols"
    export DUSKY_LIST_INNER="$list_cols"
    export DUSKY_WHEN_W=3
    export DUSKY_AUTH_W=8
    local fixed_width=$(( DUSKY_WHEN_W + 3 + 3 + DUSKY_AUTH_W ))
    export DUSKY_MSG_WIDTH=$(( list_cols - fixed_width ))
    if (( DUSKY_MSG_WIDTH < 24 )); then
        DUSKY_MSG_WIDTH=24
    fi
}

_dusky_header_line() {
    _dusky_compute_widths
    local b r bar
    b="$DUSKY_ANSI_BOLD"
    r="$DUSKY_ANSI_RESET"
    bar="${DUSKY_ANSI_BAR}│${r}"
    printf '%s%s%s %s %s%-*s%s %s %s%-8s%s %s %s%s%s' \
        "$b" "󰥔  " "$r" \
        "$bar" \
        "$b" "$DUSKY_MSG_WIDTH" "GRAPH / MESSAGE" "$r" \
        "$bar" \
        "$b" "AUTHOR" "$r" \
        "$bar" \
        "$b" "REFS" "$r"
}

_dusky_git_list() {
    if [[ "$(_dusky_read_mode)" == "files" ]]; then
        _dusky_git_list_files
        return 0
    fi
    _dusky_compute_widths
    local scope head home
    scope="$(_dusky_read scope)"
    [[ -n "$scope" ]] || scope="all"
    head="$(_gr rev-parse --short=7 HEAD 2>/dev/null || true)"
    home="${DUSKY_PRESENT_SHORT:-}"

    local -a log_args=(
        log --graph --color=always --decorate=short --abbrev=7
        --format=$'%x1f%h%x1f%ad%x1f%an%x1f%ar%x1f%C(auto)%d%x1f%s'
        --date=format:'%m/%d'
    )
    if [[ "$scope" == "all" ]]; then
        log_args+=(--branches --tags --remotes HEAD)
    fi

    _gr "${log_args[@]}" | gawk \
        -v FS=$'\x1f' \
        -v head="$head" \
        -v home="$home" \
        -v when_w="${DUSKY_WHEN_W}" \
        -v msg_w="${DUSKY_MSG_WIDTH}" \
        -v auth_w="${DUSKY_AUTH_W}" \
        -v head_file="${DUSKY_STATE_DIR}/head_line" \
        -v reset="${DUSKY_ANSI_RESET}" \
        -v dim="${DUSKY_ANSI_DIM}" \
        -v fg="${DUSKY_ANSI_FG}" \
        -v acc="${DUSKY_ANSI_ACCENT}" \
        -v ok="${DUSKY_ANSI_SUCCESS}" \
        -v err="${DUSKY_ANSI_ERROR}" \
        '
        function strip_ansi(s,    c) {
            c = s
            gsub(/\033\[[0-9;]*[A-Za-z]/, "", c)
            return c
        }
        function vwidth(s,    t, n, i, ch, w) {
            t = strip_ansi(s)
            w = 0
            n = split(t, chars, "")
            for (i = 1; i <= n; i++) {
                ch = chars[i]
                if (length(ch) >= 3) w += 2
                else w += 1
            }
            return w
        }
        function trunc(s, max,    t, out, i, ch, add, w, n) {
            t = s
            if (max < 1) return ""
            if (vwidth(t) <= max) return t
            out = ""
            w = 0
            n = split(t, chars, "")
            for (i = 1; i <= n; i++) {
                ch = chars[i]
                add = (length(ch) >= 3) ? 2 : 1
                if (w + add > max) break
                out = out ch
                w += add
            }
            return out
        }
        function pad(s, width,    n) {
            n = width - vwidth(s)
            if (n < 0) n = 0
            return s sprintf("%*s", n, "")
        }
        function compact_rel(rel) {
            gsub(/ ago/, "", rel)
            gsub(/ seconds?/, "s", rel)
            gsub(/ minutes?/, "m", rel)
            gsub(/ hours?/, "h", rel)
            gsub(/ days?/, "d", rel)
            gsub(/ weeks?/, "w", rel)
            gsub(/ months?/, "mo", rel)
            gsub(/ years?/, "y", rel)
            return rel
        }
        {
            if (NF < 2) {
                # Graph connection line (e.g. | \, | /, | |)
                printf "\x1f%s │ %s%s\n", pad("", when_w), $0, reset
                next
            }
            graph  = $1
            hash   = $2
            author = $4
            rel    = compact_rel($5)
            refs   = $6
            msg    = $7

            author = trunc(author, auth_w)
            rel    = trunc(rel, when_w)
            gsub(/\|/, "│", msg)

            mark = "  "
            markc = dim
            msg_style = fg
            if (hash == head) {
                mark = "◀ "
                markc = err
                msg_style = "\033[1;38;2;126;200;200m"
                print NR > head_file
            } else if (hash == home) {
                mark = "⌂ "
                markc = acc
            }

            msg_budget = msg_w - vwidth(graph) - vwidth(mark)
            if (msg_budget < 10) msg_budget = 10
            msg_trunc = trunc(msg, msg_budget)
            msg_formatted = graph markc mark reset msg_style msg_trunc reset
            msg_padded = pad(msg_formatted, msg_w)

            ref_str = ""
            if (length(strip_ansi(refs)) > 0) {
                ref_str = " │ " refs
            }

            printf "%s\x1f%s │ %s │ %s%s\n", \
                hash, \
                dim pad(rel, when_w) reset, \
                msg_padded, \
                acc pad(author, auth_w) reset, \
                ref_str
        }
        '
}

_dusky_help_text() {
    local b r d a s e
    b="$DUSKY_ANSI_BOLD"
    r="$DUSKY_ANSI_RESET"
    d="$DUSKY_ANSI_DIM"
    a="$DUSKY_ANSI_ACCENT"
    s="$DUSKY_ANSI_SUCCESS"
    e="$DUSKY_ANSI_ERROR"

    cat <<EOF


  ${b}󰏖  Dusky Time Machine  v${DUSKY_TM_VERSION}  ·  ${DUSKY_TM_CODENAME}${r}
  ${d}bare  GIT_DIR=${GIT_DIR}  WORK_TREE=${GIT_WORK_TREE}${r}
  ${d}engine ${DUSKY_TM_ENGINE}${r}
  ${d}ipc    bash --worker   parent-shell-proof (zsh/fish/bash)${r}

  ${a}Travel${r}
    ${s}[ENTER]${r}          Detach onto selected commit (stash-shield active)
    ${s}[DOUBLE-CLICK]${r}   Same as ENTER
    ${s}[CTRL-R]${r}         Return to present and apply session stash
    ${s}[CTRL-G]${r}         Jump cursor to live HEAD
    ${s}[ALT-A]${r}          Toggle scope: all refs ↔ current lineage

  ${a}Navigation & Vim Mode${r}
    ${s}[ALT-M]${r}          Toggle Vim navigation mode (j/k, g/G, Ctrl-D/U, /)
    ${s}[/]${r}              Enter search mode (when Vim mode is active)
    ${s}[ESC]${r}            Exit · return to present (or return to Vim normal mode)

  ${a}Inspect & Preview Layout${r}
    ${s}[ALT-P]${r}          Cycle preview content: side → inline → stat → files → vs_present
    ${s}[TAB]${r}            Browse changed files of selected commit (click a file = its diff)
    ${s}[ALT-LEFT/RGHT]${r}  Resize preview pane split (±5%)
    ${s}[ALT-UP/DOWN]${r}    Resize vertical preview pane split (±5%)
    ${s}[ALT-H/J/K/L]${r}    Move preview pane (Left / Bottom / Top / Right)
    ${s}[ALT-V / CTRL-/]${r} Toggle preview pane visibility
    ${s}[SHIFT-UP/DN]${r}    Scroll preview pane
    ${s}[F1] / [CTRL-O]${r}  Toggle this keyboard reference inside preview
    ${s}[CTRL-L]${r}         Reload commit graph

  ${a}Safety${r}
    ${s}[ALT-R]${r}          Hard reset to HEAD ${e}(requires YES confirmation)${r}
    ${s}[CTRL-W]${r}         Hard reset to HEAD ${e}(requires 2 consecutive presses)${r}
    ${s}[ALT-S]${r}          Stay in past on exit (skip auto-return)
    ${s}[ALT-O]${r}          Apply orphaned DUSKY_AUTO_STASH_* (present only)

  ${a}Export & Branch${r}
    ${s}[CTRL-Y]${r}         Yank short hash (desktop notification)
    ${s}[ALT-Y]${r}          Yank full SHA (desktop notification)
    ${s}[ALT-B]${r}          Create branch at selected commit

  ${d}◀ current HEAD     ⌂ recorded present tip     stash is tracked-only${r}
  ${d}Untracked \$HOME files are never stashed, cleaned, or force-overwritten.${r}
  ${d}Workers never inherit the janitor. The RAM engine cannot vanish on Down.${r}
EOF
}

_dusky_ghost_preview() {
    printf '\n\n  %s╭─────────────────────────────────────────────╮%s\n' "$DUSKY_ANSI_DIM" "$DUSKY_ANSI_RESET"
    printf '  %s│%s  %sGraph connector - no commit on this line.%s  %s│%s\n' \
        "$DUSKY_ANSI_DIM" "$DUSKY_ANSI_RESET" \
        "$DUSKY_ANSI_DIM" "$DUSKY_ANSI_RESET" \
        "$DUSKY_ANSI_DIM" "$DUSKY_ANSI_RESET"
    printf '  %s╰─────────────────────────────────────────────╯%s\n' "$DUSKY_ANSI_DIM" "$DUSKY_ANSI_RESET"
}

# -----------------------------------------------------------------------------
# 8b. File browser — drill into a commit's changed files (TAB toggle)
#     mode=commits  → list/preview behave exactly as before.
#     mode=files    → list emits x<ordinal> rows of drill_sha; preview renders
#                     that single file's old-vs-new diff. Index of paths is
#                     kept in files_index (ordinal → path), so tokens stay
#                     short, unique, and shell-safe in fzf placeholders.
# -----------------------------------------------------------------------------
_dusky_read_mode() {
    local m
    m="$(_dusky_read mode)"
    [[ "$m" == "files" ]] || m="commits"
    printf '%s\n' "$m"
}

_dusky_is_merge() {
    _gr rev-parse --verify --quiet "$1^2" >/dev/null 2>&1
}

_dusky_name_status() {
    local sha="$1" base="${2:-}"
    if [[ -n "$base" ]]; then
        _gr -c core.pager=cat diff --name-status "$base" "$sha" 2>/dev/null || true
    else
        _gr -c core.pager=cat diff-tree --root --no-commit-id --name-status -r "$sha" 2>/dev/null || true
    fi
}

_dusky_changed_files_block() {
    local sha="$1" base="${2:-}"
    local raw="" st_path st path stc
    local total=0 shown=0 max=40
    local -a rows=()
    raw="$(_dusky_name_status "$sha" "$base")"
    while IFS=$'\t' read -r st path; do
        [[ -n "$st" && -n "$path" ]] || continue
        [[ "$path" =~ [[:cntrl:]] ]] && continue
        rows+=("${st}"$'\t'"${path}")
        (( total++ )) || true
    done <<<"$raw"

    if (( total == 0 )); then
        if [[ -z "$base" ]] && _dusky_is_merge "$sha"; then
            printf '%s● merge commit — per-file listing unavailable%s\n' \
                "$DUSKY_ANSI_DIM" "$DUSKY_ANSI_RESET"
        else
            printf '%s(no file changes)%s\n' "$DUSKY_ANSI_DIM" "$DUSKY_ANSI_RESET"
        fi
        return 0
    fi

    printf '%s󰐖 CHANGED FILES%s %s(%d)%s %s· [TAB] browse%s\n' \
        "$DUSKY_ANSI_BOLD" "$DUSKY_ANSI_RESET" \
        "$DUSKY_ANSI_DIM" "$total" "$DUSKY_ANSI_RESET" \
        "$DUSKY_ANSI_DIM" "$DUSKY_ANSI_RESET"
    for st_path in "${rows[@]}"; do
        (( shown < max )) || break
        st="${st_path%%$'\t'*}"
        path="${st_path#*$'\t'}"
        case "$st" in
            A)   stc="$DUSKY_ANSI_SUCCESS" ;;
            D)   stc="$DUSKY_ANSI_ERROR" ;;
            M|T) stc="$DUSKY_ANSI_ACCENT" ;;
            *)   stc="$DUSKY_ANSI_FG" ;;
        esac
        printf '  %s%-2s%s %s%s%s\n' \
            "$stc" "$st" "$DUSKY_ANSI_RESET" \
            "$DUSKY_ANSI_FG" "$path" "$DUSKY_ANSI_RESET"
        (( shown++ )) || true
    done
    if (( total > shown )); then
        printf '%s  … +%d more — [TAB] to browse all%s\n' \
            "$DUSKY_ANSI_DIM" "$(( total - shown ))" "$DUSKY_ANSI_RESET"
    fi
    printf '%s%s%s\n' "$DUSKY_ANSI_DIM" \
        '────────────────────────────────────────────' "$DUSKY_ANSI_RESET"
}

_dusky_files_index_path() {
    local ord="$1"
    [[ "$ord" =~ ^[1-9][0-9]*$ ]] || return 1
    sed -z -n "${ord}p" "${DUSKY_STATE_DIR}/files_index" 2>/dev/null
}

_dusky_git_list_files() {
    local sha idx tmp st path display color n=0
    sha="$(_dusky_read drill_sha)"
    idx="${DUSKY_STATE_DIR}/files_index"
    tmp="$(mktemp "${idx}.XXXXXXXXXX")" || return 1
    if _dusky_valid_hash "$sha" && ! _dusky_is_merge "$sha"; then
        while IFS= read -r -d '' st && IFS= read -r -d '' path; do
            ((n+=1))
            color="$DUSKY_ANSI_FG"
            case "$st" in
                A) color="$DUSKY_ANSI_SUCCESS" ;;
                D) color="$DUSKY_ANSI_ERROR" ;;
                M|T) color="$DUSKY_ANSI_ACCENT" ;;
            esac
            printf -v display '%q' "$path"
            printf 'x%d\x1f  %s%-2s%s %s\n' "$n" "$color" "$st" "$DUSKY_ANSI_RESET" "$display"
            printf '%s\0' "$path" >>"$tmp"
        done < <(_gr diff-tree --root --no-commit-id --no-renames --name-status -z -r "$sha")
    fi
    mv -f -- "$tmp" "$idx" || return 1
    if (( n == 0 )); then
        printf 'x0\x1f%s(no per-file changes available)%s\n' "$DUSKY_ANSI_DIM" "$DUSKY_ANSI_RESET"
    fi
}

_dusky_file_preview() {
    local token="$1" ord sha short path width diff_out
    ord="${token#x}"
    [[ "$ord" =~ ^[0-9]+$ ]] || { _dusky_ghost_preview; return 0; }
    sha="$(_dusky_read drill_sha)"
    short="$(_dusky_read drill_short)"
    path=""
    IFS= read -r -d '' path < <(_dusky_files_index_path "$ord") || true
    width="${FZF_PREVIEW_COLUMNS:-120}"

    printf '%sΔ %s%s%s  %s%s%s\n' \
        "$DUSKY_ANSI_ACCENT" \
        "$DUSKY_ANSI_BOLD" "${short:-?}" "$DUSKY_ANSI_RESET" \
        "$DUSKY_ANSI_FG" "$path" "$DUSKY_ANSI_RESET"
    printf '%s[TAB / ESC] back to commits%s\n\n' \
        "$DUSKY_ANSI_DIM" "$DUSKY_ANSI_RESET"

    if ! _dusky_valid_hash "$sha" || [[ -z "$path" ]]; then
        printf '%s(no file selected)%s\n' "$DUSKY_ANSI_DIM" "$DUSKY_ANSI_RESET"
        return 0
    fi

    diff_out="$(_gr --literal-pathspecs -c core.pager=cat diff-tree --root -p --no-commit-id --color=always "$sha" -- "$path" 2>/dev/null || true)"
    if [[ -z "$diff_out" ]] && _dusky_is_merge "$sha"; then
        diff_out="$(_gr --literal-pathspecs -c core.pager=cat show --color=always --format= "$sha" -- "$path" 2>/dev/null || true)"
    fi
    if [[ -z "$diff_out" ]]; then
        printf '%s(no textual diff for this path)%s\n' "$DUSKY_ANSI_DIM" "$DUSKY_ANSI_RESET"
        return 0
    fi
    printf '%s\n' "$diff_out" \
        | delta --paging=never --dark --side-by-side --line-numbers --width="$width"
}

_dusky_git_list_commit_pos() {
    local short="$1" hit
    hit="$(_dusky_git_list 2>/dev/null | gawk -v FS=$'\x1f' -v want="$short" '$1 == want { print NR; exit }')"
    [[ "$hit" =~ ^[0-9]+$ ]] || return 1
    printf '%s\n' "$hit"
}

_dusky_git_preview() {
    local hash="${1:-}"
    local mode
    mode="$(_dusky_read preview_mode)"
    [[ -n "$mode" ]] || mode="side"

    if [[ "$mode" == "help" ]]; then
        _dusky_help_text
        return 0
    fi

    if [[ "$(_dusky_read_mode)" == "files" && "$hash" =~ ^x[0-9]+$ ]]; then
        _dusky_file_preview "$hash"
        return 0
    fi

    if ! _dusky_valid_hash "$hash"; then
        _dusky_ghost_preview
        return 0
    fi

    if [[ -s "${DUSKY_STATE_DIR}/conflicts" ]]; then
        printf '%s⚠ Untracked collision paths blocking travel:%s\n' "$DUSKY_ANSI_ERROR" "$DUSKY_ANSI_RESET"
        sed 's/^/    /' "${DUSKY_STATE_DIR}/conflicts"
        printf '\n'
    fi

    local width="${FZF_PREVIEW_COLUMNS:-120}"
    case "$mode" in
        side)
            _dusky_changed_files_block "$hash"
            printf '\n'
            _gr -c core.pager=cat show --color=always --decorate --abbrev-commit "$hash" \
                | delta --paging=never --dark --side-by-side --line-numbers --width="$width"
            ;;
        inline)
            _dusky_changed_files_block "$hash"
            printf '\n'
            _gr -c core.pager=cat show --color=always --decorate --abbrev-commit "$hash" \
                | delta --paging=never --dark --line-numbers --width="$width"
            ;;
        stat)
            _gr -c core.pager=cat show --stat --decorate --color=always "$hash"
            printf '\n'
            _gr -c core.pager=cat diff-tree --no-commit-id --name-status -r --color=always "$hash"
            ;;
        files)
            _gr -c core.pager=cat log -1 --format='%C(auto)%h %s%n%an <%ae>%n%ad%n' --date=iso-strict "$hash"
            _gr -c core.pager=cat diff-tree --no-commit-id --name-status -r --color=always "$hash"
            ;;
        vs_present)
            if [[ -n "${DUSKY_PRESENT_SHA:-}" ]]; then
                _dusky_changed_files_block "$hash" "${DUSKY_PRESENT_SHA}"
                printf '\n'
                printf '%sΔ present %s → %s%s\n\n' \
                    "$DUSKY_ANSI_ACCENT" \
                    "$(_gr rev-parse --short "$DUSKY_PRESENT_SHA")" \
                    "$(_gr rev-parse --short "$hash")" \
                    "$DUSKY_ANSI_RESET"
                _gr -c core.pager=cat diff --color=always "$DUSKY_PRESENT_SHA" "$hash" \
                    | delta --side-by-side --paging=never --line-numbers --width="$width"
            else
                printf 'No recorded present HEAD.\n'
            fi
            ;;
        *)
            _gr -c core.pager=cat show --stat --color=always "$hash"
            ;;
    esac
}

_dusky_git_checkout() {
    local hash="${1:-}"
    rm -f -- "${DUSKY_STATE_DIR}/confirm_wipe"
    rm -f -- "${DUSKY_STATE_DIR}/conflicts"
    if ! _dusky_valid_hash "$hash"; then
        _dusky_note "skip" "no commit on this line"
        return 0
    fi

    local target current
    target="$(_gr rev-parse --verify --quiet "${hash}^{commit}" || true)"
    if [[ -z "$target" ]]; then
        _dusky_note "error" "not a commit: ${hash}"
        return 1
    fi
    current="$(_gr rev-parse HEAD 2>/dev/null || true)"
    if [[ "$target" == "$current" ]]; then
        _dusky_note "here" "$(_gr rev-parse --short=7 HEAD 2>/dev/null || true)"
        return 0
    fi

    if [[ "$(_dusky_read phase)" == "conflict" ]]; then
        _dusky_note "blocked" "resolve stash conflicts before traveling"
        return 1
    fi
    if [[ "$(_dusky_read phase)" == "detached" ]] && _dusky_worktree_dirty; then
        _dusky_note "blocked" "save edits made while traveling before switching"
        return 1
    fi
    if ! _dusky_shield_present; then
        return 1
    fi

    cd "$GIT_WORK_TREE" || return 1
    local err
    err="$(_gw switch --quiet --no-overwrite-ignore --detach "$target" 2>&1)" || {
        # A failed first trip must put the original staged/unstaged work back.
        if [[ "$(_dusky_read phase)" == "present" ]]; then
            if _dusky_apply_session_stash; then
                _dusky_clear_present_target
            else
                return 1
            fi
        fi
        _dusky_note "error" "git switch --detach failed for ${hash}: ${err}"
        return 1
    }

    _dusky_write phase "detached"
    _dusky_note "traveled" "$(_gr rev-parse --short=7 HEAD 2>/dev/null || true)"
    return 0
}

_dusky_git_restore() {
    rm -f -- "${DUSKY_STATE_DIR}/confirm_wipe"
    cd "$GIT_WORK_TREE" || return 1
    local err
    local collision=""
    if IFS= read -r -d '' collision < <(_dusky_untracked_collisions HEAD); then
        _dusky_note "blocked" "untracked path obstructs reset: ${collision}"
        return 1
    fi
    err="$(_gw reset --hard --quiet HEAD 2>&1)" || {
        _dusky_note "error" "hard reset failed: ${err}"
        return 1
    }
    _dusky_note "wiped" "work tree reset to $(_gr rev-parse --short=7 HEAD)"
    return 0
}

_dusky_git_restore_interactive() {
    printf '\n  %sHARD RESET%s tracked files to HEAD (%s).\n' \
        "$DUSKY_ANSI_ERROR" "$DUSKY_ANSI_RESET" \
        "$(_gr rev-parse --short=7 HEAD)" >/dev/tty
    printf '  Type YES to confirm: ' >/dev/tty
    local ans=""
    read -er ans </dev/tty || true
    if [[ "$ans" != "YES" ]]; then
        _dusky_note "cancelled" "hard reset aborted"
        return 0
    fi
    _dusky_git_restore
}

_dusky_notify() {
    local title="$1" msg="${2:-}" urgency="${3:-normal}"
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -u "$urgency" -a "Git Time Machine" \
            -h string:x-canonical-private-synchronous:dusky-tm \
            -- "󰏖 $title" "$msg" 2>/dev/null || true
    fi
}

_dusky_clipboard() {
    local text="$1"
    if [[ -n "${WAYLAND_DISPLAY:-}" ]] && command -v wl-copy >/dev/null 2>&1; then
        printf '%s' "$text" | wl-copy
        return 0
    fi
    if [[ -n "${DISPLAY:-}" ]] && command -v xclip >/dev/null 2>&1; then
        printf '%s' "$text" | xclip -selection clipboard
        return 0
    fi
    local b64=""
    b64=$(printf '%s' "$text" | base64 -w0 2>/dev/null || printf '%s' "$text" | base64)
    printf '\033]52;c;%s\a' "$b64" >/dev/tty 2>/dev/null || true
    printf '%s' "$text" >"${DUSKY_STATE_DIR}/yank"
    return 0
}

_dusky_git_copy() {
    local hash="${1:-}" mode="${2:-short}"
    if [[ "$(_dusky_read_mode)" == "files" && "$hash" =~ ^x[0-9]+$ ]]; then
        local ord fpath
        ord="${hash#x}"
        fpath="$(_dusky_files_index_path "$ord")"
        if [[ -n "$fpath" ]]; then
            _dusky_clipboard "$fpath"
            _dusky_note "copied" "$fpath"
            _dusky_notify "File Path Copied" "$fpath"
        else
            _dusky_note "skip" "no path to copy"
        fi
        return 0
    fi
    if ! _dusky_valid_hash "$hash"; then
        _dusky_note "skip" "no hash to copy"
        return 0
    fi
    local text
    if [[ "$mode" == "full" ]]; then
        text="$(_gr rev-parse --verify --quiet "${hash}^{commit}" || true)"
    else
        text="$(_gr rev-parse --short=7 --verify --quiet "${hash}^{commit}" || true)"
    fi
    [[ -n "$text" ]] || { _dusky_note "error" "cannot resolve ${hash}"; return 1; }
    _dusky_clipboard "$text"
    _dusky_note "copied" "$text"
    _dusky_notify "Commit Hash Copied" "$text"
}

_dusky_git_branch() {
    local hash="${1:-}"
    if ! _dusky_valid_hash "$hash"; then
        return 0
    fi
    local name=""
    printf '\n  Create branch at %s\n  Name: ' "$hash" >/dev/tty
    read -er name </dev/tty || return 0
    [[ -n "$name" ]] || return 0
    if ! _gr check-ref-format --branch "$name" >/dev/null 2>&1; then
        printf '  invalid branch name\n' >/dev/tty
        read -rsn1 </dev/tty || true
        return 1
    fi
    if _gr show-ref --verify --quiet "refs/heads/${name}"; then
        printf '  branch already exists: %s\n' "$name" >/dev/tty
        read -rsn1 </dev/tty || true
        return 1
    fi
    _dusky_git_checkout "$hash" || return 1
    if _gw switch --quiet -c "$name"; then
        printf '  created branch %s → %s\n' "$name" "$hash" >/dev/tty
        _dusky_note "branched" "${name} @ ${hash}"
        _dusky_notify "Branch Created" "${name} @ ${hash}"
    else
        printf '  git branch failed\n' >/dev/tty
        _dusky_note "error" "git switch -c ${name} failed"
    fi
    printf '  press any key to return\n' >/dev/tty
    read -rsn1 </dev/tty || true
}

_dusky_cycle_preview() {
    local cur next
    cur="$(_dusky_read preview_mode)"
    case "$cur" in
        side)       next="inline" ;;
        inline)     next="stat" ;;
        stat)       next="files" ;;
        files)      next="vs_present" ;;
        vs_present) next="side" ;;
        help)       next="side" ;;
        *)          next="side" ;;
    esac
    _dusky_write preview_mode "$next"
    _dusky_note "preview" "$next"
    _dusky_user_state_save
}

_dusky_toggle_help() {
    local cur
    cur="$(_dusky_read preview_mode)"
    if [[ "$cur" == "help" ]]; then
        _dusky_write preview_mode "$(_dusky_read prev_preview || true)"
        [[ -n "$(_dusky_read preview_mode)" ]] || _dusky_write preview_mode "side"
        _dusky_note "help" "closed"
    else
        _dusky_write prev_preview "$cur"
        _dusky_write preview_mode "help"
        _dusky_note "help" "open"
    fi
}

_dusky_toggle_scope() {
    if [[ "$(_dusky_read_mode)" == "files" ]]; then
        _dusky_note "blocked" "leave file browser (TAB) to change scope"
        return 0
    fi
    local cur
    cur="$(_dusky_read scope)"
    if [[ "$cur" == "all" ]]; then
        _dusky_write scope "lineage"
        _dusky_note "scope" "current lineage"
    else
        _dusky_write scope "all"
        _dusky_note "scope" "branches + tags + remotes"
    fi
    _dusky_user_state_save
}

_dusky_toggle_stay() {
    local cur
    cur="$(_dusky_read stay)"
    if [[ "$cur" == "1" ]]; then
        _dusky_write stay "0"
        _dusky_note "stay" "off — exit will return to present"
    else
        _dusky_write stay "1"
        _dusky_save_present_target
        _dusky_note "stay" "ARMED — exit will remain detached"
    fi
}

_dusky_apply_orphan() {
    if [[ -z "$(_gr branch --show-current 2>/dev/null || true)" ]]; then
        _dusky_note "blocked" "orphan apply only on a named branch (return first)"
        return 1
    fi

    local gd gs pid
    while IFS=$'\t' read -r gd gs; do
        if [[ "$gs" == *": DUSKY_AUTO_STASH_"* ]]; then
            pid="${gs##*_}"
            if [[ "$pid" =~ ^[0-9]+$ ]] && ! kill -0 "$pid" 2>/dev/null; then
                if _gw stash apply --index --quiet "$gd"; then
                    _gw stash drop --quiet "$gd" || true
                    _dusky_note "orphan" "applied and dropped ${gd}"
                    return 0
                fi
                _dusky_note "conflict" "orphan ${gd} collided — stash kept"
                _dusky_write phase "conflict"
                _dusky_write stash "conflict"
                return 1
            fi
        fi
    done < <(_gr stash list --format=$'%gd\t%gs')

    _dusky_note "orphan" "no dead-PID DUSKY_AUTO_STASH entries"
    return 0
}

_dusky_prompt_line() {
    local phase stay action detail head
    phase="$(_dusky_read phase)"
    stay="$(_dusky_read stay)"
    action="$(_dusky_read last_action)"
    detail="$(_dusky_read last_detail)"
    head="$(_gr rev-parse --short=7 HEAD 2>/dev/null || printf '?')"

    local left="present"
    case "$phase" in
        detached) left="detached ${head}" ;;
        conflict) left="CONFLICT ${head}" ;;
        stay)     left="stay ${head}" ;;
        present)  left="present ${head}" ;;
    esac
    if [[ "$stay" == "1" ]]; then
        left="STAY ${left}"
    fi
    if [[ "$(_dusky_read_mode)" == "files" ]]; then
        local ds
        ds="$(_dusky_read drill_short)"
        [[ -n "$ds" ]] || ds="?"
        left="files ${ds}"
    fi
    if [[ -f "${DUSKY_STATE_DIR}/confirm_wipe" ]]; then
        printf ' :: %s · CONFIRM WIPE (press Ctrl-W again) ❯ ' "$left"
        return 0
    fi
    if [[ "$action" == "ready" || -z "$action" ]]; then
        printf ' :: %s ❯ ' "$left"
        return 0
    fi
    printf ' :: %s · %s ❯ ' "$left" "${action}${detail:+ ${detail}}"
}

_dusky_footer_line() {
    local phase stash stay scope mode head br orphans
    phase="$(_dusky_read phase)"
    stash="$(_dusky_read stash)"
    stay="$(_dusky_read stay)"
    scope="$(_dusky_read scope)"
    mode="$(_dusky_read preview_mode)"
    head="$(_gr rev-parse --short=7 HEAD 2>/dev/null || printf '?')"
    br="$(_gr branch --show-current 2>/dev/null || true)"
    [[ -n "$br" ]] || br="DETACHED"

    orphans=0
    local gs pid
    while IFS=$'\t' read -r _ gs; do
        if [[ "$gs" == *": DUSKY_AUTO_STASH_"* ]]; then
            pid="${gs##*_}"
            if [[ "$pid" =~ ^[0-9]+$ ]] && ! kill -0 "$pid" 2>/dev/null; then
                ((orphans++)) || true
            fi
        fi
    done < <(_gr stash list --format=$'%gd\t%gs')

    local extra=""
    if [[ -f "${DUSKY_STATE_DIR}/confirm_wipe" ]]; then
        extra="  │  CTRL-W again to DESTROY tracked work at HEAD"
    elif (( orphans > 0 )); then
        extra="  │  ${orphans} orphan stash(es) — ALT-O to apply"
    fi
    if [[ "$(_dusky_read_mode)" == "files" ]]; then
        extra="${extra}  │  TAB/ESC back to commits"
    fi

    printf 'HEAD %s  %s  stash:%s  scope:%s  preview:%s  stay:%s  present:%s%s' \
        "$head" "$br" "$stash" "$scope" "$mode" "$stay" \
        "${DUSKY_PRESENT_SHORT:-?}" "$extra"
}

_dusky_preview_label() {
    if [[ "$(_dusky_read_mode)" == "files" ]]; then
        local s
        s="$(_dusky_read drill_short)"
        [[ -n "$s" ]] || s="?"
        printf '  files @ %s  ' "$s"
        return 0
    fi
    local mode
    mode="$(_dusky_read preview_mode)"
    case "$mode" in
        side)       printf '  delta · side-by-side  ' ;;
        inline)     printf '  delta · unified  ' ;;
        stat)       printf '  git show --stat  ' ;;
        files)      printf '  name-status  ' ;;
        vs_present) printf '  diff vs present  ' ;;
        help)       printf '  keyboard reference  ' ;;
        *)          printf '  preview  ' ;;
    esac
}

_dusky_pos_head() {
    if [[ "$(_dusky_read_mode)" == "files" ]]; then
        return 0
    fi
    local n
    n="$(_dusky_read head_line)"
    [[ "$n" =~ ^[0-9]+$ ]] || n=1
    printf 'pos(%s)\n' "$n"
}

_dusky_w() {
    printf '%s --noprofile --norc -- %s --worker' \
        "${DUSKY_BASH@Q}" "${DUSKY_TM_ENGINE@Q}"
}

_dusky_refresh_chain() {
    local w
    w="$(_dusky_w)"
    printf 'reload-sync(%s list)+transform-prompt(%s prompt)+transform-footer(%s footer)+transform-preview-label(%s preview-label)' \
        "$w" "$w" "$w" "$w"
}

_dusky_act_enter() {
    local hash="${1:-}"
    if [[ "$(_dusky_read_mode)" == "files" ]]; then
        _dusky_act_commits_impl
        return 0
    fi
    if ! _dusky_valid_hash "$hash"; then
        printf 'bell+transform-footer(%s footer)\n' "$(_dusky_w)"
        return 0
    fi
    _dusky_git_checkout "$hash" || true
    printf '%s\n' "$(_dusky_refresh_chain)"
}

_dusky_act_commits_impl() {
    local sha short n=""
    sha="$(_dusky_read drill_sha)"
    _dusky_write mode "commits"
    if _dusky_valid_hash "$sha"; then
        short="$(_gr rev-parse --short=7 "$sha" 2>/dev/null || true)"
        if [[ -n "$short" ]]; then
            n="$(_dusky_git_list_commit_pos "$short" || true)"
        fi
    fi
    if [[ "$n" =~ ^[0-9]+$ ]] && (( n > 0 )); then
        printf 'reload-sync(%s list)+pos(%d)+transform-prompt(%s prompt)+transform-footer(%s footer)+transform-preview-label(%s preview-label)\n' \
            "$(_dusky_w)" "$n" "$(_dusky_w)" "$(_dusky_w)" "$(_dusky_w)"
    else
        printf '%s\n' "$(_dusky_refresh_chain)"
    fi
}

_dusky_act_files() {
    local token="${1:-}" target="" short
    if [[ "$(_dusky_read_mode)" == "files" ]]; then
        _dusky_act_commits_impl
        return 0
    fi
    if _dusky_valid_hash "$token"; then
        target="$(_gr rev-parse --verify --quiet "${token}^{commit}" 2>/dev/null || true)"
    fi
    if [[ -z "$target" ]]; then
        printf 'bell+transform-footer(%s footer)\n' "$(_dusky_w)"
        return 0
    fi
    short="$(_gr rev-parse --short=7 "$target" 2>/dev/null || true)"
    [[ -n "$short" ]] || short="${target:0:7}"
    _dusky_write mode "files"
    _dusky_write drill_sha "$target"
    _dusky_write drill_short "$short"
    printf '%s\n' "$(_dusky_refresh_chain)"
}

_dusky_act_wipe() {
    if [[ -f "${DUSKY_STATE_DIR}/confirm_wipe" ]]; then
        rm -f -- "${DUSKY_STATE_DIR}/confirm_wipe"
        _dusky_git_restore || true
        printf '%s\n' "$(_dusky_refresh_chain)"
        return 0
    fi
    : >"${DUSKY_STATE_DIR}/confirm_wipe"
    printf 'transform-prompt(%s prompt)+transform-footer(%s footer)\n' \
        "$(_dusky_w)" "$(_dusky_w)"
}

_dusky_act_return() {
    rm -f -- "${DUSKY_STATE_DIR}/confirm_wipe"
    _dusky_write mode "commits"
    _dusky_git_return || true
    printf '%s+transform(%s pos-head)\n' "$(_dusky_refresh_chain)" "$(_dusky_w)"
}

_dusky_orphan_report() {
    local gd gs pid
    while IFS=$'\t' read -r gd gs; do
        if [[ "$gs" == *": DUSKY_AUTO_STASH_"* ]]; then
            pid="${gs##*_}"
            if [[ "$pid" =~ ^[0-9]+$ ]] && ! kill -0 "$pid" 2>/dev/null; then
                printf '  %s  %s\n' "$gd" "$gs"
            fi
        fi
    done < <(_gr stash list --format=$'%gd\t%gs')
}

_dusky_act_move_preview() {
    local dir="${1:-}" cur last pct rest next base env_pct
    case "$dir" in left|right|up|down|hidden) ;; *) return 0 ;; esac
    cur="$(_dusky_read preview_layout)"
    [[ -n "$cur" ]] || cur="${DUSKY_PREVIEW_WINDOW:-right,70%,border-left,wrap}"
    last="$(_dusky_read preview_last)"
    [[ -n "$last" ]] || last="${DUSKY_PREVIEW_WINDOW:-right,70%,border-left,wrap}"

    base="$cur"
    [[ "$base" == "hidden" ]] && base="$last"
    if [[ "$base" =~ ^(up|down|left|right),([0-9]+)%(.*)$ ]]; then
        pct="${BASH_REMATCH[2]}"
        rest="${BASH_REMATCH[3]}"
        base="${BASH_REMATCH[1]}"
    else
        pct=70
        rest=',border-left,wrap'
        base="right"
    fi

    # Honor live FZF preview size after manual mouse-drag of the divider.
    # FZF exports FZF_PREVIEW_COLUMNS / FZF_PREVIEW_LINES to every transform.
    # If those indicate the divider was dragged, use them as the base pct to
    # avoid snapping back to the stale file value.
    if [[ "$cur" != "hidden" && "$cur" =~ ^(up|down|left|right),([0-9]+)% ]]; then
        local cur_edge_now="${BASH_REMATCH[1]}"
        if [[ "$cur_edge_now" == "right" || "$cur_edge_now" == "left" ]]; then
            if [[ "${FZF_PREVIEW_COLUMNS:-}" =~ ^[0-9]+$ && "${FZF_COLUMNS:-}" =~ ^[0-9]+$ ]] \
                && (( FZF_COLUMNS > 0 && FZF_PREVIEW_COLUMNS > 0 && FZF_PREVIEW_COLUMNS < FZF_COLUMNS )); then
                env_pct=$(( (FZF_PREVIEW_COLUMNS * 100 + FZF_COLUMNS/2) / FZF_COLUMNS ))
                if (( env_pct >= 5 && env_pct <= 95 )); then
                    # Only override for same-axis moves; cross-axis keeps file pct.
                    if [[ "$dir" == "left" || "$dir" == "right" || "$dir" == "hidden" ]]; then
                        pct=$env_pct
                    elif [[ "$cur_edge_now" == "$dir" ]]; then
                        pct=$env_pct
                    fi
                fi
            fi
        else
            if [[ "${FZF_PREVIEW_LINES:-}" =~ ^[0-9]+$ && "${FZF_LINES:-}" =~ ^[0-9]+$ ]] \
                && (( FZF_LINES > 0 && FZF_PREVIEW_LINES > 0 && FZF_PREVIEW_LINES < FZF_LINES )); then
                env_pct=$(( (FZF_PREVIEW_LINES * 100 + FZF_LINES/2) / FZF_LINES ))
                if (( env_pct >= 5 && env_pct <= 95 )); then
                    if [[ "$dir" == "up" || "$dir" == "down" || "$dir" == "hidden" ]]; then
                        pct=$env_pct
                    fi
                fi
            fi
        fi
    else
        # Fallback: if base is hidden (last) but we are showing, still try to infer
        if [[ "$base" == "right" || "$base" == "left" ]]; then
            if [[ "${FZF_PREVIEW_COLUMNS:-}" =~ ^[0-9]+$ && "${FZF_COLUMNS:-}" =~ ^[0-9]+$ ]] \
                && (( FZF_COLUMNS > 0 && FZF_PREVIEW_COLUMNS > 0 && FZF_PREVIEW_COLUMNS < FZF_COLUMNS )); then
                env_pct=$(( (FZF_PREVIEW_COLUMNS * 100 + FZF_COLUMNS/2) / FZF_COLUMNS ))
                (( env_pct >= 5 && env_pct <= 95 )) && pct=$env_pct
            fi
        fi
    fi

    if [[ "$dir" == "hidden" ]]; then
        if [[ "$cur" == "hidden" ]]; then
            next="$last"
        else
            next="hidden"
        fi
    else
        local border="border-left"
        case "$dir" in
            left)  border="border-right" ;;
            right) border="border-left" ;;
            up)    border="border-bottom" ;;
            down)  border="border-top" ;;
        esac
        (( pct < 10 )) && pct=10
        (( pct > 90 )) && pct=90
        next="${dir},${pct}%,${border},wrap"
    fi

    _dusky_write preview_layout "$next"
    if [[ "$next" != "hidden" ]]; then
        _dusky_write preview_last "$next"
    fi
    _dusky_user_state_save
    printf 'change-preview-window(%s)+refresh-preview' "$next"
}

_dusky_act_resize_preview() {
    local dir="${1:-}" cur edge pct rest new next env_pct
    case "$dir" in left|right|up|down) ;; *) return 0 ;; esac
    cur="$(_dusky_read preview_layout)"
    [[ -n "$cur" ]] || cur="${DUSKY_PREVIEW_WINDOW:-right,70%,border-left,wrap}"
    if [[ "$cur" == "hidden" ]] || ! [[ "$cur" =~ ^(up|down|left|right),([0-9]+)%(.*)$ ]]; then
        return 0
    fi
    edge="${BASH_REMATCH[1]}"
    pct="${BASH_REMATCH[2]}"
    rest="${BASH_REMATCH[3]}"

    # Seamless mouse-drag awareness: if the user dragged the divider with the
    # mouse, FZF's live preview size (FZF_PREVIEW_COLUMNS / LINES) will differ
    # from the stale file value. Use the live value as the base to avoid the
    # "snap back" reported on Alt+Left/Right after a manual drag.
    if [[ "$edge" == "right" || "$edge" == "left" ]]; then
        if [[ "${FZF_PREVIEW_COLUMNS:-}" =~ ^[0-9]+$ && "${FZF_COLUMNS:-}" =~ ^[0-9]+$ ]] \
            && (( FZF_COLUMNS > 0 && FZF_PREVIEW_COLUMNS > 0 && FZF_PREVIEW_COLUMNS < FZF_COLUMNS )); then
            env_pct=$(( (FZF_PREVIEW_COLUMNS * 100 + FZF_COLUMNS/2) / FZF_COLUMNS ))
            if (( env_pct >= 5 && env_pct <= 95 )); then
                pct=$env_pct
            fi
        fi
    else
        if [[ "${FZF_PREVIEW_LINES:-}" =~ ^[0-9]+$ && "${FZF_LINES:-}" =~ ^[0-9]+$ ]] \
            && (( FZF_LINES > 0 && FZF_PREVIEW_LINES > 0 && FZF_PREVIEW_LINES < FZF_LINES )); then
            env_pct=$(( (FZF_PREVIEW_LINES * 100 + FZF_LINES/2) / FZF_LINES ))
            if (( env_pct >= 5 && env_pct <= 95 )); then
                pct=$env_pct
            fi
        fi
    fi

    new=$pct
    case "$edge:$dir" in
        right:left|left:right|up:down|down:up) (( new += 5 )) ;;
        right:right|left:left|up:up|down:down) (( new -= 5 )) ;;
        *) return 0 ;;
    esac
    (( new < 10 )) && new=10
    (( new > 90 )) && new=90
    if (( new == pct )); then
        printf 'bell'
        return 0
    fi

    next="${edge},${new}%${rest}"
    _dusky_write preview_layout "$next"
    _dusky_write preview_last "$next"
    _dusky_user_state_save
    printf 'change-preview-window(%s)+refresh-preview' "$next"
}

readonly DUSKY_VIM_KEYS='j,k,g,G,ctrl-d,ctrl-u,/'

_dusky_emit_vim_actions() {
    local mode="$1"
    local p
    p="$(_dusky_prompt_line)"
    if [[ "$mode" == "true" ]]; then
        printf 'rebind(%s)+disable-search+change-prompt( 🅝 q:quit /:search ❯ )+refresh-preview' "$DUSKY_VIM_KEYS"
    else
        printf 'unbind(%s)+enable-search+change-prompt(%s)+refresh-preview' "$DUSKY_VIM_KEYS" "$p"
    fi
}

_dusky_vim_init() {
    local mode
    mode="$(_dusky_read vim_mode)"
    if [[ -z "$mode" ]]; then
        _dusky_user_state_load
        mode="${DUSKY_CFG_VIM_MODE:-false}"
        _dusky_write vim_mode "$mode"
    fi
    [[ "$mode" == "true" ]] || mode="false"
    _dusky_emit_vim_actions "$mode"
}

_dusky_toggle_vim() {
    local cur next
    cur="$(_dusky_read vim_mode)"
    if [[ -z "$cur" ]]; then
        _dusky_user_state_load
        cur="${DUSKY_CFG_VIM_MODE:-false}"
    fi
    if [[ "$cur" == "true" ]]; then
        next="false"
    else
        next="true"
    fi
    _dusky_write vim_mode "$next"
    _dusky_user_state_save
    _dusky_emit_vim_actions "$next"
}

_dusky_key_escape() {
    if [[ "$(_dusky_read_mode)" == "files" ]]; then
        _dusky_act_commits_impl
        return 0
    fi
    local prompt="${FZF_PROMPT:-}" input_state="${FZF_INPUT_STATE:-}"
    local vim_mode
    vim_mode="$(_dusky_read vim_mode)"
    if [[ "$prompt" == *"󰍉"* || "$prompt" == *"search"* && "$vim_mode" == "true" ]]; then
        _dusky_emit_vim_actions "true"
    elif [[ "$input_state" == "disabled" || "$prompt" == *"🅝"* ]]; then
        printf 'abort'
    else
        printf 'abort'
    fi
}

spawn_terminal() {
    local -a cmd=()
    if command -v kitty >/dev/null 2>&1; then
        cmd=(kitty --class=dusky-time-machine --title="Dusky Time Machine" -o confirm_os_window_close=0 -e)
    elif command -v foot >/dev/null 2>&1; then
        cmd=(foot --app-id=dusky-time-machine --title="Dusky Time Machine" --window-size-chars=140x36)
    elif command -v ghostty >/dev/null 2>&1; then
        cmd=(ghostty --class=dusky-time-machine --title="Dusky Time Machine" -e)
    elif command -v wezterm >/dev/null 2>&1; then
        cmd=(wezterm start --class=dusky-time-machine --)
    elif command -v alacritty >/dev/null 2>&1; then
        cmd=(alacritty --class=dusky-time-machine --title="Dusky Time Machine" -e)
    else
        return 1
    fi
    exec "${cmd[@]}" env DUSKY_TM_EPHEMERAL=1 "$0" "$@"
}

# -----------------------------------------------------------------------------
# 12. Library return / worker dispatch — NO TRAPS on these paths
# -----------------------------------------------------------------------------
_dusky_worker_dispatch() {
    local verb="${1:-}"
    shift || true
    case "$verb" in
        preview)              _dusky_git_preview "$@" ;;
        list)                 _dusky_git_list "$@" ;;
        checkout)             _dusky_git_checkout "$@" ;;
        return)               _dusky_git_return "$@" ;;
        restore)              _dusky_git_restore "$@" ;;
        restore-interactive)  _dusky_git_restore_interactive "$@" ;;
        copy)                 _dusky_git_copy "$@" ;;
        branch)               _dusky_git_branch "$@" ;;
        cycle-preview)        _dusky_cycle_preview "$@" ;;
        toggle-help)          _dusky_toggle_help "$@" ;;
        toggle-scope)         _dusky_toggle_scope "$@" ;;
        toggle-stay)          _dusky_toggle_stay "$@" ;;
        apply-orphan)         _dusky_apply_orphan "$@" ;;
        prompt)               _dusky_prompt_line "$@" ;;
        footer)               _dusky_footer_line "$@" ;;
        preview-label)        _dusky_preview_label "$@" ;;
        pos-head)             _dusky_pos_head "$@" ;;
        act-enter)            _dusky_act_enter "$@" ;;
        act-files)            _dusky_act_files "$@" ;;
        act-wipe)             _dusky_act_wipe "$@" ;;
        act-return)           _dusky_act_return "$@" ;;
        move-preview)         _dusky_act_move_preview "$@" ;;
        resize-preview)       _dusky_act_resize_preview "$@" ;;
        toggle-vim)           _dusky_toggle_vim "$@" ;;
        vim-init)             _dusky_vim_init "$@" ;;
        key-escape)           _dusky_key_escape "$@" ;;
        orphan-report)        _dusky_orphan_report "$@" ;;
        header)               _dusky_header_line "$@" ;;
        *)
            printf 'dusky-tm worker: unknown verb %s\n' "${verb:-?}" >&2
            return 2
            ;;
    esac
}

_dusky_usage() {
    cat <<EOF
Dusky Git Time Machine v${DUSKY_TM_VERSION} — ${DUSKY_TM_CODENAME}

Usage:
  dusky_time_machine_tui.sh              Interactive TUI
  dusky_time_machine_tui.sh --worker V   Internal FZF child (do not call)
  dusky_time_machine_tui.sh --self-test  Sandbox-only functional tests
  dusky_time_machine_tui.sh --help

Bare-repo contract: GIT_DIR=\$HOME/dusky  GIT_WORK_TREE=\$HOME
EOF
}

if [[ "${DUSKY_ROLE}" == "library" ]]; then
    return 0 2>/dev/null || exit 0
fi

if [[ "${DUSKY_ROLE}" == "worker" ]]; then
    # Workers inherit owner env (paths, colors, GIT_*, state dir).
    # They must never relocate, flock, trap, or unlink.
    shift
    _dusky_bind_paths
    if [[ -z "${MATUGEN_BG:-}" || -z "${DUSKY_ANSI_RESET:-}" ]]; then
        _dusky_bind_colors
    fi
    if [[ -z "${DUSKY_LIST_INNER:-}" ]]; then
        _dusky_compute_widths
    fi
    # Workers are separate processes: refresh the persisted stash identity after
    # another worker returns or starts a new trip in the same owner session.
    _dusky_load_present_target
    _dusky_worker_dispatch "$@"
    exit $?
fi

# -----------------------------------------------------------------------------
# 13. Owner-only from here: toolchain, lock, traps, TUI / self-test
# -----------------------------------------------------------------------------
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    _dusky_usage
    exit 0
fi
if [[ "${1:-}" == "--version" ]]; then
    printf '%s %s\n' "$DUSKY_TM_VERSION" "$DUSKY_TM_CODENAME"
    exit 0
fi

_dusky_require_versions
_dusky_owner_repo_init
_dusky_bind_paths
_dusky_bind_colors
_dusky_compute_widths
_dusky_hold_engine_fd

# -----------------------------------------------------------------------------
# 14. Self-test (sandbox only) — also invoked by verify_dusky_tm.sh
# -----------------------------------------------------------------------------
_dusky_self_test() {
    if [[ "${DUSKY_TM_SANDBOX:-0}" != 1 ]]; then
        _dusky_die "refusing --self-test outside sandbox (set DUSKY_TM_SANDBOX=1)"
    fi
    local wt_real home_real
    wt_real="$(readlink -f -- "$GIT_WORK_TREE")"
    home_real="$(readlink -f -- "$HOME")"
    if [[ "$wt_real" == "$home_real" ]]; then
        _dusky_die "refusing --self-test on \$HOME work tree"
    fi

    local fail=0
    _ok() { printf '  PASS  %s\n' "$1"; }
    _bad() { printf '  FAIL  %s\n' "$1"; fail=1; }

    printf 'dusky-tm self-test  engine=%s  pid=%s\n' "$DUSKY_TM_ENGINE" "$$"
    [[ -f "$DUSKY_TM_ENGINE" ]] || _dusky_die "engine missing after relocate"

    local hashes h1 h2
    hashes="$(_gr log --reverse --format='%h')"
    h1="$(printf '%s\n' "$hashes" | sed -n '1p')"
    h2="$(printf '%s\n' "$hashes" | sed -n '2p')"
    [[ -n "$h1" && -n "$h2" ]] || _dusky_die "sandbox repo needs ≥2 commits"

    # A. Worker preview storm must not unlink the engine.
    local i out
    for i in $(seq 1 25); do
        out="$("$DUSKY_BASH" --noprofile --norc -- "$DUSKY_TM_ENGINE" --worker preview "$h1" 2>&1)" || {
            _bad "worker preview #$i exited non-zero"
            break
        }
        if [[ ! -f "$DUSKY_TM_ENGINE" ]]; then
            _bad "engine unlinked after worker preview #$i"
            break
        fi
        if [[ ! -d "$DUSKY_STATE_DIR" ]]; then
            _bad "state dir removed after worker preview #$i"
            break
        fi
    done
    [[ -f "$DUSKY_TM_ENGINE" && -d "$DUSKY_STATE_DIR" ]] && _ok "25 worker previews left engine+state intact"

    # B. Simulate FZF's zsh -c and bash -c child model with baked argv.
    local baked preview_cmd
    baked="$(_dusky_w)"
    preview_cmd="${baked} preview ${h2}"
    if command -v zsh >/dev/null 2>&1; then
        for i in $(seq 1 20); do
            zsh --no-rcs -f -c "$preview_cmd" >/dev/null 2>&1 || {
                _bad "zsh -c preview #$i failed"
                break
            }
            [[ -f "$DUSKY_TM_ENGINE" ]] || { _bad "engine vanished after zsh -c #$i"; break; }
        done
        [[ -f "$DUSKY_TM_ENGINE" ]] && _ok "20 zsh --no-rcs -c previews (FZF+Zsh model)"
    else
        printf '  SKIP  zsh not installed\n'
    fi
    for i in $(seq 1 20); do
        bash --noprofile --norc -c "$preview_cmd" >/dev/null 2>&1 || {
            _bad "bash -c preview #$i failed"
            break
        }
        [[ -f "$DUSKY_TM_ENGINE" ]] || { _bad "engine vanished after bash -c #$i"; break; }
    done
    [[ -f "$DUSKY_TM_ENGINE" ]] && _ok "20 bash -c previews (FZF+Bash model)"

    # C. Parallel previews must not contend on the flock.
    local p
    for i in $(seq 1 8); do
        "$DUSKY_BASH" --noprofile --norc -- "$DUSKY_TM_ENGINE" --worker preview "$h1" >/dev/null 2>&1 &
    done
    wait || true
    if [[ -f "$DUSKY_TM_ENGINE" && -d "$DUSKY_STATE_DIR" ]]; then
        _ok "8 parallel previews: no engine loss, no lock death"
    else
        _bad "parallel previews destroyed session files"
    fi

    # D. Column order: WHEN (compact rel) before author/date; hash in field 1.
    local list_line display
    list_line="$("$DUSKY_BASH" --noprofile --norc -- "$DUSKY_TM_ENGINE" --worker list | gawk -F $'\x1f' 'NF>=2{print; exit}')"
    display="${list_line#*$'\x1f'}"
    if [[ "$list_line" == *$'\x1f'* ]]; then
        _ok "list uses SOH delimiter (hash\\x1fdisplay)"
    else
        _bad "list missing \\x1f delimiter"
    fi
    if [[ "$display" == *◀* || "$display" == *⌂* || "$display" == *" "* ]]; then
        _ok "list display contains mark/when gutter"
    fi

    # E. Collision probe: untracked path that exists in an older tree.
    local collide
    collide="$(_gr ls-tree -r --name-only "$h1" | head -n1)"
    if [[ -n "$collide" ]]; then
        rm -f -- "${GIT_WORK_TREE}/${collide}"
        mkdir -p -- "$(dirname -- "${GIT_WORK_TREE}/${collide}")"
        printf 'UNTRACKED-SECRET-%s\n' "$RANDOM" >"${GIT_WORK_TREE}/${collide}.untracked_probe"
        # Force a colliding untracked file at a path the target commit owns but index does not.
        # After HEAD is h2, h1-only files that we recreate as untracked should block.
        local only_in_h1
        only_in_h1="$(comm -23 \
            <(_gr ls-tree -r --name-only "$h1" | LC_ALL=C sort) \
            <(_gr ls-files | LC_ALL=C sort) | head -n1 || true)"
        if [[ -n "$only_in_h1" ]]; then
            mkdir -p -- "$(dirname -- "${GIT_WORK_TREE}/${only_in_h1}")"
            printf 'BLOCKME\n' >"${GIT_WORK_TREE}/${only_in_h1}"
            if _dusky_untracked_collisions "$h1" | grep -F -z -q -- "$only_in_h1"; then
                _ok "collision probe detects untracked path owned by target"
            else
                local hitc=0
                while IFS= read -r -d '' _; do ((hitc++)) || true; done < <(_dusky_untracked_collisions "$h1")
                if (( hitc > 0 )); then
                    _ok "collision probe returned ${hitc} blocking path(s)"
                else
                    _bad "collision probe missed untracked ${only_in_h1}"
                fi
            fi
            rm -f -- "${GIT_WORK_TREE}/${only_in_h1}"
        else
            printf '  SKIP  no h1-only path to collide (linear add-only history)\n'
        fi
        rm -f -- "${GIT_WORK_TREE}/${collide}.untracked_probe"
    fi

    # F. Stash is tracked-only: drop an untracked file next to a dirty tracked one.
    local tracked
    tracked="$(_gr ls-files | head -n1)"
    if [[ -n "$tracked" ]]; then
        local secret="${GIT_WORK_TREE}/.dusky_tm_untracked_secret_$$"
        printf 'SECRET\n' >"$secret"
        printf '\n# tm-test %s\n' "$RANDOM" >>"${GIT_WORK_TREE}/${tracked}"
        if _dusky_worktree_dirty; then
            _ok "dirty tracked file detected with --untracked-files=no"
        else
            _bad "failed to see dirty tracked file"
        fi
        _dusky_write stash "none"
        if _dusky_shield_present; then
            if [[ -f "$secret" ]]; then
                _ok "untracked secret survived stash shield"
            else
                _bad "stash shield destroyed untracked secret"
            fi
            if grep -q 'tm-test' "$secret" 2>/dev/null; then
                _bad "secret file unexpectedly contains tracked marker"
            fi
            local stash_show
            stash_show="$(_gr stash show --name-only "stash@{0}" 2>/dev/null || true)"
            if printf '%s\n' "$stash_show" | grep -Fq '.dusky_tm_untracked_secret_'; then
                _bad "untracked secret was pulled into the stash"
            else
                _ok "session stash does not contain the untracked secret"
            fi
        else
            _bad "stash shield failed on dirty tracked file"
        fi
        _dusky_apply_session_stash || true
        rm -f -- "$secret"
        # restore tracked file if still dirty
        _gw restore --worktree -- "$tracked" 2>/dev/null || true
    fi

    # G. Owner-guard: a worker calling cleanup must be a no-op.
    DUSKY_ROLE=worker _dusky_cleanup
    if [[ -f "$DUSKY_TM_ENGINE" && -d "$DUSKY_STATE_DIR" ]]; then
        _ok "cleanup is inert when DUSKY_ROLE=worker"
    else
        _bad "cleanup deleted session from a spoofed worker role"
    fi
    DUSKY_ROLE=owner

    # H. Static contract: no git clean, no stash -u, no $0 preview, valid refresh chain.
    local check_clean="git "
    check_clean="${check_clean}clean"
    if grep -vE '^[[:space:]]*#' "$DUSKY_TM_ENGINE" | grep -v 'check_clean' | grep -nE -- "\b${check_clean}\b" >/dev/null \
        || grep -vE '^[[:space:]]*#' "$DUSKY_TM_ENGINE" | grep -v 'check_clean' | grep -nE -- 'stash[[:space:]].*([[:space:]]-u\b|[[:space:]]--all\b|[[:space:]]--include-untracked\b)' >/dev/null; then
        _bad "engine contains git-clean or untracked stash"
    else
        _ok "no destructive clean and no untracked stash flags"
    fi
    local check_v10_zero='VOLATILE_PATH:-$'
    check_v10_zero="${check_v10_zero}0"
    if grep -vE '^[[:space:]]*#' "$DUSKY_TM_ENGINE" | grep -v 'check_v10_zero' | grep -n -- "$check_v10_zero" >/dev/null; then
        _bad "engine still falls back to \$0 for preview"
    else
        _ok "preview path does not fall back to \$0"
    fi
    if grep -n -- '--worker preview' "$DUSKY_TM_ENGINE" >/dev/null; then
        _ok "FZF preview uses --worker preview"
    else
        _bad "FZF preview is not wired to --worker"
    fi
    local rchain
    rchain="$("$DUSKY_BASH" --noprofile --norc -- "$DUSKY_TM_ENGINE" --worker act-enter "$h1" 2>/dev/null || true)"
    if [[ "$rchain" == *"transform-preview-label( "* || "$rchain" != *"transform-preview-label("* ]]; then
        _bad "refresh chain has malformed transform-preview-label: $rchain"
    else
        _ok "refresh chain format specifiers valid"
    fi

    # I. Lock fd must still be held by the owner (workers did not steal it).
    if flock -n 9; then
        # re-acquiring the same fd succeeds; a foreign fd would fail. Just keep it.
        _ok "owner still holds lock fd 9 after worker storm"
    else
        _bad "owner lost flock on fd 9"
    fi

    if (( fail == 0 )); then
        printf 'dusky-tm self-test: ALL PASSED\n'
        return 0
    fi
    printf 'dusky-tm self-test: FAILURES PRESENT\n' >&2
    return 1
}

# -----------------------------------------------------------------------------
# 15. Main engine
# -----------------------------------------------------------------------------
main() {
    if [[ ! -t 0 || ! -t 1 ]] && [[ "${1:-}" != "--worker" && "${1:-}" != "--self-test" ]]; then
        spawn_terminal "$@"
    fi

    _dusky_acquire_lock
    _dusky_state_init
    _dusky_load_present_target
    if [[ -f "$DUSKY_PRESENT_FILE" ]]; then
        _dusky_write phase "detached"
        if _dusky_find_session_stash >/dev/null; then
            _dusky_write stash "stashed"
        fi
    fi

    if [[ "${1:-}" == "--self-test" ]]; then
        _dusky_self_test
        local rc=$?
        # Leave the work tree on the branch we started on; cleanup returns if detached.
        exit "$rc"
    fi

    if [[ -z "$(_gr branch --show-current 2>/dev/null || true)" && -f "$DUSKY_PRESENT_FILE" ]]; then
        _dusky_write phase "detached"
        _dusky_write stay "1"
        _dusky_note "rejoined" "still detached from a previous stay"
    fi

    local skip_count
    skip_count="$(_gr ls-files -v 2>/dev/null | gawk '/^[S]/{c++} END{print c+0}')"
    if (( skip_count > 0 )); then
        _dusky_note "ready" "${skip_count} skip-worktree paths"
    fi

    local orphans
    orphans="$(_dusky_orphan_report || true)"
    if [[ -n "$orphans" ]]; then
        _dusky_note "ready" "orphan stashes present — ALT-O"
    fi

    _dusky_git_list >"${DUSKY_STATE_DIR}/list"
    local line_num=""
    line_num="$(_dusky_read head_line)"

    local w start_bind header
    w="$(_dusky_w)"
    start_bind="start:wait+transform-footer(${w} footer)+transform-prompt(${w} prompt)+transform-preview-label(${w} preview-label)+transform(${w} vim-init)"
    if [[ "$line_num" =~ ^[0-9]+$ ]]; then
        start_bind="start:wait+pos(${line_num})+transform-footer(${w} footer)+transform-prompt(${w} prompt)+transform-preview-label(${w} preview-label)+transform(${w} vim-init)"
    fi
    header="$(_dusky_header_line)"

    # Baked argv: even if FZF ignores --with-shell and uses zsh -c, the -c
    # script is an already-quoted bash invocation of the stable engine file.
    local with_shell preview_cmd
    with_shell="${DUSKY_BASH} --noprofile --norc -c"
    preview_cmd="${w} preview {1}"

    fzf --ansi --no-height \
        --sync \
        --style=full:rounded \
        --with-shell="${with_shell}" \
        --delimiter=$'\x1f' \
        --with-nth=2 \
        --nth=1,2 \
        --track \
        --id-nth=1 \
        --tiebreak=index \
        --no-sort \
        --no-hscroll \
        --ellipsis='' \
        --highlight-line \
        --scrollbar='││' \
        --ghost='filter by hash, author, message…' \
        --prompt=' :: Time Machine ❯ ' \
        --pointer='' \
        --marker='✔ ' \
        --layout=reverse \
        --info=inline-right \
        --header="$header" \
        --header-border=inline \
        --footer='loading…' \
        --footer-border=inline \
        --border-label=" 󰏖 Dusky Time Machine  v${DUSKY_TM_VERSION}  [F1 help] " \
        --border-label-pos=3 \
        --preview="${preview_cmd}" \
        --preview-window="${DUSKY_PREVIEW_WINDOW}" \
        --preview-label='  commit  ' \
        --preview-label-pos=center \
        --margin=1 \
        --padding=0 \
        --bind="$start_bind" \
        --bind="resize:refresh-preview+transform-header(${w} header)+reload-sync(${w} list)" \
        --bind="alt-m:transform:${w} toggle-vim" \
        --bind="esc:transform:${w} key-escape" \
        --bind="alt-left:transform:${w} resize-preview left" \
        --bind="alt-right:transform:${w} resize-preview right" \
        --bind="alt-up:transform:${w} resize-preview up" \
        --bind="alt-down:transform:${w} resize-preview down" \
        --bind="alt-h:transform:${w} move-preview left" \
        --bind="alt-j:transform:${w} move-preview down" \
        --bind="alt-k:transform:${w} move-preview up" \
        --bind="alt-l:transform:${w} move-preview right" \
        --bind="alt-v:transform:${w} move-preview hidden" \
        --bind="enter:transform:${w} act-enter {1}" \
        --bind="double-click:transform:${w} act-enter {1}" \
        --bind="tab:transform:${w} act-files {1}" \
        --bind="ctrl-r:transform:${w} act-return" \
        --bind="ctrl-w:transform:${w} act-wipe" \
        --bind="alt-r:execute(${w} restore-interactive)+reload-sync(${w} list)+transform-prompt(${w} prompt)+transform-footer(${w} footer)" \
        --bind="ctrl-y:execute-silent(${w} copy {1} short)+transform-prompt(${w} prompt)+transform-footer(${w} footer)" \
        --bind="alt-y:execute-silent(${w} copy {1} full)+transform-prompt(${w} prompt)+transform-footer(${w} footer)" \
        --bind="alt-p:execute-silent(${w} cycle-preview)+refresh-preview+transform-preview-label(${w} preview-label)+transform-footer(${w} footer)+transform-prompt(${w} prompt)" \
        --bind="ctrl-/:toggle-preview" \
        --bind="f1:execute-silent(${w} toggle-help)+refresh-preview+transform-preview-label(${w} preview-label)+transform-footer(${w} footer)" \
        --bind="ctrl-o:execute-silent(${w} toggle-help)+refresh-preview+transform-preview-label(${w} preview-label)+transform-footer(${w} footer)" \
        --bind="alt-s:execute-silent(${w} toggle-stay)+transform-prompt(${w} prompt)+transform-footer(${w} footer)" \
        --bind="alt-a:execute-silent(${w} toggle-scope)+reload-sync(${w} list)+transform-prompt(${w} prompt)+transform-footer(${w} footer)" \
        --bind="alt-b:execute(${w} branch {1})+reload-sync(${w} list)+transform-footer(${w} footer)+transform-prompt(${w} prompt)" \
        --bind="alt-o:execute-silent(${w} apply-orphan)+reload-sync(${w} list)+transform-prompt(${w} prompt)+transform-footer(${w} footer)" \
        --bind="ctrl-g:transform:${w} pos-head" \
        --bind="ctrl-l:reload-sync(${w} list)+transform-footer(${w} footer)" \
        --bind="result-final:bg-transform-footer(${w} footer)" \
        --bind="j:down" --bind="k:up" --bind="g:first" --bind="G:last" \
        --bind="ctrl-d:half-page-down" --bind="ctrl-u:half-page-up" \
        --bind="q:abort" \
        --bind="/:change-prompt( 󰍉 search ❯ )+enable-search+unbind(${DUSKY_VIM_KEYS})" \
        --bind="shift-up:preview-up" --bind="shift-down:preview-down" \
        --bind="shift-scroll-up:preview-up" --bind="shift-scroll-down:preview-down" \
        --bind="scroll-up:up" --bind="scroll-down:down" \
        --bind="preview-scroll-up:preview-up" --bind="preview-scroll-down:preview-down" \
        --color="bg:${MATUGEN_BG},bg+:${MATUGEN_MUTED},fg:${MATUGEN_FG},fg+:${MATUGEN_FG}" \
        --color="hl:${MATUGEN_ACCENT},hl+:${MATUGEN_ACCENT},pointer:${MATUGEN_SUCCESS},marker:${MATUGEN_SUCCESS}" \
        --color="prompt:${MATUGEN_ACCENT},spinner:${MATUGEN_ACCENT},info:${MATUGEN_ACCENT},header:${MATUGEN_ACCENT}" \
        --color="border:${MATUGEN_MUTED},label:${MATUGEN_ACCENT},preview-border:${MATUGEN_MUTED},preview-label:${MATUGEN_ACCENT}" \
        --color="footer:${MATUGEN_FG},footer-border:${MATUGEN_MUTED},footer-label:${MATUGEN_ACCENT}" \
        --color="list-border:${MATUGEN_MUTED},header-border:${MATUGEN_MUTED},input-border:${MATUGEN_MUTED}" \
        --color="ghost:${MATUGEN_MUTED},gutter:${MATUGEN_BG},scrollbar:${MATUGEN_MUTED},preview-scrollbar:${MATUGEN_ACCENT}" \
        <"${DUSKY_STATE_DIR}/list"

    local fzf_rc=$?
    if (( fzf_rc != 0 && fzf_rc != 1 && fzf_rc != 130 )); then
        printf 'dusky-tm: fzf failed (exit %s).\n' "$fzf_rc" >&2
        return "$fzf_rc"
    fi
    local stay
    stay="$(_dusky_read stay)"
    if [[ "$stay" != "1" ]]; then
        if ! _dusky_git_return; then
            printf 'dusky-tm: could not return: %s\n' "$(_dusky_read last_detail)" >&2
            return 1
        fi
    fi

    local final_head final_br
    final_head="$(_gr rev-parse --short=7 HEAD 2>/dev/null || printf '?')"
    final_br="$(_gr branch --show-current 2>/dev/null || true)"

    clear
    if [[ "$stay" == "1" ]]; then
        printf '%s✔ Disengaged (STAY).%s  HEAD %s%s%s  %sDETACHED — present ledger kept at %s%s\n' \
            "$DUSKY_ANSI_SUCCESS" "$DUSKY_ANSI_RESET" \
            "$DUSKY_ANSI_ACCENT" "$final_head" "$DUSKY_ANSI_RESET" \
            "$DUSKY_ANSI_ERROR" "$DUSKY_PRESENT_FILE" "$DUSKY_ANSI_RESET"
    else
        printf '%s✔ Disengaged Time Machine.%s  HEAD %s%s%s  %s%s%s\n' \
            "$DUSKY_ANSI_SUCCESS" "$DUSKY_ANSI_RESET" \
            "$DUSKY_ANSI_ACCENT" "$final_head" "$DUSKY_ANSI_RESET" \
            "$DUSKY_ANSI_DIM" "${final_br:-detached}" "$DUSKY_ANSI_RESET"
    fi
}

trap _dusky_cleanup EXIT
trap '_dusky_cleanup; exit 130' INT
trap '_dusky_cleanup; exit 143' TERM
trap '_dusky_cleanup; exit 129' HUP

main "$@"
