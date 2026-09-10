#!/usr/bin/env bash
#==============================================================================
# FZF CLIPBOARD MANAGER — v4.0 "Bleeding Edge"            (Wayland / Hyprland)
#==============================================================================
# HARD target stack. No legacy fallbacks, no shims, no X11, no version probes
# for anything older than the following:
#
#   Arch Linux rolling · kernel 7.1+ · systemd 257+
#   bash      5.3+     (SRANDOM, ${var@Q}, printf -v, {fd} auto-alloc, nameref,
#                       globskipdots, assoc arrays, ${var@U}, wait -p)
#   fzf       0.73.1+  (transform / bg-transform, reload-sync, change-query,
#                       change-preview[-label], change-header, --id-nth,
#                       --track, --scheme=history, wrap-word, disable-search,
#                       FZF_PROMPT / FZF_PREVIEW_LABEL / FZF_INPUT_STATE)
#   cliphist  0.6+     (-preview-width / CLIPHIST_PREVIEW_WIDTH, multi-line
#                       stdin for `delete`)
#   wl-clipboard latest · Hyprland latest · coreutils 9.x · util-linux (flock)
#   file · gawk 5.4+ · bat · chafa 1.14+ · kitten (kitty 0.32+) · b2sum
#
# Invocation interface (drop-in superset of v3.0 — every old mode preserved):
#   <no args>           interactive menu
#   --list              emit the fzf item stream
#   --preview T ID      render the preview pane
#   --help-pane         render the help overlay          (change-preview target)
#   --toggle-help       emit fzf actions toggling help              (transform)
#   --toggle-vim        flip VIM_MODE + emit fzf actions            (transform)
#   --vim-init          emit fzf actions for the current mode       (transform)
#   --key-escape        context sensitive Esc handler               (transform)
#   --confirm-wipe      two stage wipe confirmation                 (transform)
#   --capture-size      persist current preview geometry
#   --move-preview D    left|right|up|down|hidden                   (transform)
#   --resize-preview D  left|right|up|down                          (transform)
#   --batch-pin FILE    --batch-delete FILE   --wipe   --prune-cache
#   --doctor            environment diagnostics
#   --version   --help
#
# WHY NO `errexit`: `return 1` is used pervasively as ordinary control flow and
# errexit's inheritance rules inside command substitution and pipelines would
# silently truncate preview output. `nounset` + `pipefail` + explicit status
# checks are used instead.
#==============================================================================

set -o nounset -o pipefail
shopt -s nullglob extglob globskipdots
umask 077

# Deterministic byte semantics everywhere; individual call sites narrow this
# further only where UTF-8 character (not byte) semantics are required.
export LC_ALL=C.UTF-8

: "${HOME:?HOME is not set}"

#==============================================================================
# CONSTANTS / PATHS
#==============================================================================
readonly VERSION='4.0'

readonly XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
readonly XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
readonly XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

readonly SETTINGS_DIR="$XDG_CONFIG_HOME/dusky/settings"
readonly USER_STATE_FILE="$SETTINGS_DIR/clipboard_state"
readonly STATE_LOCK_FILE="$SETTINGS_DIR/.clipboard_state.lock"
readonly PERSIST_STATE_FILE="$SETTINGS_DIR/clipboard_persistance"
readonly DB_ENV_FILE="$SETTINGS_DIR/cliphist_db_env"
readonly PINS_DIR="$XDG_DATA_HOME/rofi-cliphist/pins"

# Volatile store. XDG_RUNTIME_DIR is the canonical systemd managed per-user
# tmpfs (0700, wiped on logout) — strictly better than /dev/shm, which is world
# traversable. /dev/shm is used only when there is no session runtime dir at
# all (bare TTY); this is a capability branch, not a legacy shim.
if [[ -n ${XDG_RUNTIME_DIR:-} && -d ${XDG_RUNTIME_DIR:-} && -w ${XDG_RUNTIME_DIR:-} ]]; then
    readonly CACHE_DIR="$XDG_RUNTIME_DIR/cliphist-fzf"
elif [[ -d /dev/shm && -w /dev/shm ]]; then
    readonly CACHE_DIR="/dev/shm/cliphist-fzf-$UID"
else
    readonly CACHE_DIR="$XDG_CACHE_HOME/rofi-cliphist/images"
fi

readonly SEP=$'\x1f'                 # ASCII US: stripped from every payload
readonly TAB=$'\t'
readonly TMP_PREFIX='.clipfzf'       # single prefix => single cleanup glob

readonly ICON_PIN='󰐃'
readonly ICON_IMG='󰋩'
readonly ICON_BIN='󰏖'

# cliphist truncates `list` previews to -preview-width characters (default 100)
# BEFORE we ever see them, so the search index can never be wider than this
# number no matter what we do downstream. v3.0 indexed 100 chars while claiming
# 4096. Raise it deliberately and keep the display truncation identical so the
# two can never drift apart again.
readonly CLIP_PREVIEW_WIDTH=480
readonly LIST_TRUNC=480
readonly PREVIEW_TEXT_LIMIT=50000
readonly CACHE_TTL_MIN=1440
readonly WIPE_ARM_SECONDS=5

readonly MARK_VIM='🅝'
readonly MARK_SEARCH='󰍉'
readonly LABEL_PREVIEW=' 󰈙 Preview '
readonly LABEL_HELP=' 󰌵 Help '
readonly PROMPT_NORMAL='  '
readonly PROMPT_VIM=" $MARK_VIM q:quit /:search > "
readonly PROMPT_SEARCH=" $MARK_SEARCH > "
readonly HEADER_WIPE=' 󰀦  Alt-W again within 5s to WIPE THE ENTIRE HISTORY '

# Keys owned by vim normal mode. Declared in exactly ONE place so the
# bind / unbind / rebind sets can never drift out of sync.
readonly VIM_KEYS='j,k,g,G,J,K,v,V,q,ctrl-a,ctrl-d,ctrl-u,/'

SELF=$(realpath -e -- "${BASH_SOURCE[0]}") || { printf 'cannot resolve self\n' >&2; exit 1; }
readonly SELF
readonly SCRIPT_NAME="${SELF##*/}"

# fzf runs preview/execute/transform commands through `$SHELL -c`, which is NOT
# guaranteed to be bash. v3.0 embedded "${SELF@Q}" — *bash* quoting, which emits
# $'...' for exotic paths (dash/fish cannot parse that) and can emit a ']' that
# terminates fzf's own bracket-delimited action argument. Exporting the path and
# referencing it as a plain "$VAR" makes every binding string pure 7-bit ASCII
# with zero metacharacters, and is correct in every POSIX-ish shell.
export CLIPFZF_SELF="$SELF"
readonly SELF_REF='"$CLIPFZF_SELF"'

readonly MODE="${1:-__main__}"

declare -a _TMPFILES=()
declare -A STATE=()
declare -A _UPDATES=()
_STATE_FD=''
readonly SCRIPT_PID="$BASHPID"
SESSION_OWNED=0

# parse_item outputs. Globals (not namerefs) to avoid both the circular
# reference hazard of `local -n x=$1` and a subshell per item.
P_TYPE=''
P_ID=''

#==============================================================================
# DEFAULTS + VALIDATION TABLE
#==============================================================================
declare -rA STATE_DEFAULTS=(
    [PREVIEW_LAYOUT]='right,45%,wrap-word'
    [PREVIEW_LAST]='right,45%,wrap-word'
    [VIM_MODE]='false'
    [MAX_CLIP_ITEMS]='5000'
    [MAX_CLIP_AGE_DAYS]='7'
)

readonly LAYOUT_RE='^(hidden|(up|down|left|right),([0-9]{1,2})%(,[A-Za-z0-9_~%:+-]+)*)$'
readonly VISIBLE_LAYOUT_RE='^(up|down|left|right),([0-9]{1,2})%(.*)$'

#==============================================================================
# TINY HELPERS (fork-free wherever physically possible)
#==============================================================================
have() { command -v -- "$1" &>/dev/null; }              # builtin: zero forks
log_err() { printf '\e[31m[ERROR]\e[0m %s\n' "$*" >&2; }
is_uint() { [[ ${1:-} == +([0-9]) ]]; }                 # extglob, no regex engine
is_pin_hash() { [[ ${1:-} == +([[:xdigit:]]) && ${#1} -eq 16 ]]; }
is_kitty() { [[ -n ${KITTY_PID:-}${KITTY_WINDOW_ID:-} || ${TERM:-} == *kitty* ]]; }
kitty_purge() { printf '\e_Ga=d,d=A\e\\'; }

notify() {
    local title="$1" msg="${2:-}" urgency="${3:-normal}"
    have notify-send && notify-send -u "$urgency" -a Clipboard \
        -h string:x-canonical-private-synchronous:cliphist-fzf \
        -- "󰅍 $title" "$msg" 2>/dev/null
    [[ $urgency == critical ]] && log_err "$title${msg:+: $msg}"
    return 0
}

die() { notify "$1" "${2:-}" critical; exit 1; }

# Pure-bash semantic version >=. v3.0 used ${1//[!0-9.]/} which silently fused
# "0.73.1-2" into "0.73.12"; anchoring the strip at the first non-version byte
# is the only correct reading of `fzf --version` style output.
version_ge() {
    local -a a=() b=()
    local i x y
    IFS='.' read -r -a a <<< "${1%%[!0-9.]*}"
    IFS='.' read -r -a b <<< "${2%%[!0-9.]*}"
    for ((i = 0; i < 3; i++)); do
        x="${a[i]:-0}"; y="${b[i]:-0}"
        is_uint "$x" || x=0
        is_uint "$y" || y=0
        (( 10#$x > 10#$y )) && return 0
        (( 10#$x < 10#$y )) && return 1
    done
    return 0
}

#==============================================================================
# SESSION / TEMP LIFECYCLE
#==============================================================================
# Every interactive run owns "$CACHE_DIR/session.<pid>" and exports it to all
# children through CLIPFZF_SESSION. Nothing session scoped is written to a
# shared path, so concurrent invocations cannot clobber each other.
SESSION_DIR="${CLIPFZF_SESSION:-}"

ensure_private_dir() {
    [[ -d $1 && ! -L $1 && -O $1 ]] && return 0
    # Refuse to touch anything that exists but is not a private directory we
    # own (symlink / foreign-owned dir / regular file) — closes the classic
    # /tmp style symlink redirection attack even in $XDG_RUNTIME_DIR.
    [[ -e $1 || -L $1 ]] && return 1
    mkdir -p -m 700 -- "$1" 2>/dev/null || return 1
    [[ -d $1 && ! -L $1 && -O $1 ]]
}

setup_dirs() { ensure_private_dir "$PINS_DIR" && ensure_private_dir "$CACHE_DIR"; }

# Only accept session directories created by this script's current naming scheme.
if [[ -n $SESSION_DIR ]]; then
    [[ $SESSION_DIR == "$CACHE_DIR"/session.+([0-9]).+([[:alnum:]]) \
       && -d $SESSION_DIR && ! -L $SESSION_DIR && -O $SESSION_DIR ]] ||
        SESSION_DIR=''
fi

# new_tmp DIR TAG  ->  $REPLY
# Fork-free replacement for mktemp: `set -C` makes the redirection use
# O_EXCL|O_CREAT, SRANDOM supplies 32 bits of kernel CSPRNG entropy, and umask
# 077 fixes the mode. Saves one exec + one subshell per temp file, which used
# to happen on *every* preview render.
new_tmp() {
    local dir="$1" tag="$2" i
    ensure_private_dir "$dir" || return 1
    for ((i = 0; i < 16; i++)); do
        REPLY="$dir/$TMP_PREFIX-$tag-$BASHPID-$SRANDOM"
        set -C
        if : 2>/dev/null >"$REPLY"; then
            set +C
            _TMPFILES+=("$REPLY")
            return 0
        fi
        set +C
    done
    REPLY=''
    return 1
}

untrack_tmpfile() {
    local i
    for i in "${!_TMPFILES[@]}"; do
        [[ ${_TMPFILES[i]} == "$1" ]] && { unset '_TMPFILES[i]'; return 0; }
    done
    return 0
}

remove_tmpfile() {
    [[ -n ${1:-} ]] || return 0
    rm -f -- "$1" 2>/dev/null
    untrack_tmpfile "$1"
}

cleanup() {
    # A pipeline or command substitution must never clean up its parent's
    # temporary files or interactive session.
    [[ $BASHPID == "$SCRIPT_PID" ]] || return 0

    local tmp
    for tmp in "${_TMPFILES[@]}"; do
        [[ -n $tmp ]] && rm -f -- "$tmp" 2>/dev/null
    done

    if (( SESSION_OWNED )); then
        is_kitty && kitty_purge
        [[ -n $SESSION_DIR && -d $SESSION_DIR ]] &&
            rm -rf -- "$SESSION_DIR" 2>/dev/null
    fi
    return 0
}
# EXIT covers INT/TERM as well, because those handlers `exit`, re-entering EXIT.
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap '' HUP          # a closing ephemeral terminal must not kill a mid-write

#==============================================================================
# STATE FILE I/O
#==============================================================================
# Concurrency model
# -----------------
#   readers : lock-free. `mv -f` (rename(2)) is atomic within a directory, so a
#             reader either sees the whole old file or the whole new file.
#   writers : MUST hold an exclusive flock for the *entire* read-modify-write.
#             v3.0 read outside the lock and only wrote inside it, which is a
#             textbook lost-update race: two concurrent bg-transform callbacks
#             could each read 45%, each compute 40%, and one write is dropped.
#==============================================================================
state_lock() {
    [[ -n $_STATE_FD ]] && return 0
    ensure_private_dir "$SETTINGS_DIR" || return 1
    exec {_STATE_FD}<>"$STATE_LOCK_FILE" || { _STATE_FD=''; return 1; }
    flock -x -w 3 "$_STATE_FD" && return 0
    exec {_STATE_FD}>&-
    _STATE_FD=''
    return 1
}

state_unlock() {
    [[ -n $_STATE_FD ]] || return 0
    exec {_STATE_FD}>&-
    _STATE_FD=''
    return 0
}

# Populate STATE[] from defaults + file, validating every value. The settings
# file is parsed, never sourced: a hostile or corrupted file can therefore not
# execute code, and unknown keys are dropped instead of being resurrected.
state_load() {
    local key line val
    for key in "${!STATE_DEFAULTS[@]}"; do STATE["$key"]="${STATE_DEFAULTS[$key]}"; done
    if [[ -f $USER_STATE_FILE && ! -L $USER_STATE_FILE && -r $USER_STATE_FILE ]]; then
        while IFS= read -r line || [[ -n $line ]]; do
            [[ $line =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$ ]] || continue
            key="${BASH_REMATCH[1]}"
            [[ -v STATE_DEFAULTS[$key] ]] || continue
            val="${BASH_REMATCH[2]}"
            if   [[ $val =~ ^\"([^\"]*)\" ]]; then val="${BASH_REMATCH[1]}"
            elif [[ $val =~ ^\'([^\']*)\' ]]; then val="${BASH_REMATCH[1]}"
            else val="${val%%[[:space:]]#*}"; val="${val%%[[:space:]]*}"
            fi
            STATE["$key"]="$val"
        done < "$USER_STATE_FILE"
    fi

    local layout edge pct rest

    for key in PREVIEW_LAYOUT PREVIEW_LAST; do
        layout="${STATE[$key]}"

        if [[ $key == PREVIEW_LAYOUT && $layout == hidden ]]; then
            continue
        fi

        if [[ $layout =~ $LAYOUT_RE && $layout =~ $VISIBLE_LAYOUT_RE ]]; then
            edge="${BASH_REMATCH[1]}"
            pct=$((10#${BASH_REMATCH[2]}))
            rest="${BASH_REMATCH[3]}"

            (( pct < 10 )) && pct=10
            (( pct > 90 )) && pct=90
            STATE["$key"]="$edge,$pct%$rest"
        else
            STATE["$key"]="${STATE_DEFAULTS[$key]}"
        fi
    done
    [[ ${STATE[VIM_MODE]} == true ]] || STATE[VIM_MODE]=false
    is_uint "${STATE[MAX_CLIP_ITEMS]}"    || STATE[MAX_CLIP_ITEMS]="${STATE_DEFAULTS[MAX_CLIP_ITEMS]}"
    is_uint "${STATE[MAX_CLIP_AGE_DAYS]}" || STATE[MAX_CLIP_AGE_DAYS]="${STATE_DEFAULTS[MAX_CLIP_AGE_DAYS]}"
    return 0
}

# Stage KEY VALUE pairs. Values are whitelisted rather than blacklisted: only
# bytes that can appear in a layout string, a boolean or an integer survive, so
# nothing can break the KEY="value" grammar or be re-interpreted if the user
# sources the settings file from their own shell.
state_stage() {
    local v
    while (( $# >= 2 )); do
        v="${2//[^[:alnum:]_,.:%~+ -]/}"
        _UPDATES["$1"]="$v"
        STATE["$1"]="$v"
        shift 2
    done
    return 0
}

# Rewrite the settings file preserving comments and key order. MUST be called
# with the state lock held.
state_flush() {
    (( ${#_UPDATES[@]} )) || return 0
    local tmp line key
    local -A seen=()
    new_tmp "$SETTINGS_DIR" state || return 1
    tmp="$REPLY"
    {
        if [[ -f $USER_STATE_FILE && ! -L $USER_STATE_FILE ]]; then
            while IFS= read -r line || [[ -n $line ]]; do
                if [[ $line =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*= ]]; then
                    key="${BASH_REMATCH[1]}"
                    if [[ -v _UPDATES[$key] ]]; then
                        # Collapse duplicate definitions of an updated key.
                        # v3.0 left the 2nd copy behind, and since the last
                        # assignment wins on reload the update was reverted.
                        [[ -v seen[$key] ]] && continue
                        seen["$key"]=1
                        printf '%s="%s"\n' "$key" "${_UPDATES[$key]}"
                        continue
                    fi
                fi
                printf '%s\n' "$line"
            done < "$USER_STATE_FILE"
        fi
        for key in "${!_UPDATES[@]}"; do
            [[ -v seen[$key] ]] || printf '%s="%s"\n' "$key" "${_UPDATES[$key]}"
        done
    } >"$tmp" || { remove_tmpfile "$tmp"; return 1; }

    if mv -f -- "$tmp" "$USER_STATE_FILE" 2>/dev/null; then
        untrack_tmpfile "$tmp"
        _UPDATES=()
        return 0
    fi
    remove_tmpfile "$tmp"
    return 1
}

# Convenience wrapper: one complete transaction.
state_set() {
    local rc=0
    state_lock || return 1
    state_load
    state_stage "$@"
    state_flush || rc=1
    state_unlock
    return $rc
}

seed_state_file() {
    [[ -e $USER_STATE_FILE || -L $USER_STATE_FILE ]] && return 0
    ensure_private_dir "$SETTINGS_DIR" || return 1
    local tmp
    new_tmp "$SETTINGS_DIR" seed || return 1
    tmp="$REPLY"
    {
        printf '%s\n' \
            '# =============================================================================' \
            '# CLIPBOARD MANAGER USER SETTINGS  (managed by terminal_clipboard.sh)' \
            '# =============================================================================' \
            '# fzf preview window layout: hidden | <right|left|up|down>,<10-90>%,<opts...>' \
            "PREVIEW_LAYOUT=\"${STATE_DEFAULTS[PREVIEW_LAYOUT]}\"" \
            '' \
            '# Last *visible* layout, restored when the pane is un-hidden' \
            "PREVIEW_LAST=\"${STATE_DEFAULTS[PREVIEW_LAST]}\"" \
            '' \
            '# Keybinding mode: "false" = standard, "true" = vim normal mode' \
            "VIM_MODE=\"${STATE_DEFAULTS[VIM_MODE]}\"" \
            '' \
            '# Consumed by external pruning units (see clipboard-prune.timer)' \
            "MAX_CLIP_ITEMS=\"${STATE_DEFAULTS[MAX_CLIP_ITEMS]}\"" \
            "MAX_CLIP_AGE_DAYS=\"${STATE_DEFAULTS[MAX_CLIP_AGE_DAYS]}\""
    } >"$tmp" || { remove_tmpfile "$tmp"; return 1; }
    # link(2) is O_EXCL by definition: never clobber a file another process
    # created between our -e test and now.
    local rc=0
    if ! ln -- "$tmp" "$USER_STATE_FILE" 2>/dev/null; then
        # Another process creating the state file is a successful outcome.
        [[ -f $USER_STATE_FILE && ! -L $USER_STATE_FILE ]] || rc=1
    fi
    remove_tmpfile "$tmp"
    return "$rc"
}

#==============================================================================
# CLIPHIST BACKEND BINDING
#==============================================================================
init_backend_env() {
    # Hyprland / systemd may export a stale CLIPHIST_DB_PATH from a previous
    # persistence mode; the env file written by 390_clipboard_persistance.py is
    # authoritative. It is parsed, never sourced.
    unset -v CLIPHIST_DB_PATH
    local line val
    if [[ -f $DB_ENV_FILE && ! -L $DB_ENV_FILE && -r $DB_ENV_FILE ]]; then
        while IFS= read -r line || [[ -n $line ]]; do
            [[ $line =~ ^[[:space:]]*(export[[:space:]]+)?CLIPHIST_DB_PATH[[:space:]]*=[[:space:]]*(.*)$ ]] || continue
            val="${BASH_REMATCH[2]}"
            if   [[ $val =~ ^\"([^\"]*)\" ]]; then val="${BASH_REMATCH[1]}"
            elif [[ $val =~ ^\'([^\']*)\' ]]; then val="${BASH_REMATCH[1]}"
            else val="${val%%[[:space:]]#*}"; val="${val%%[[:space:]]*}"
            fi
            # Absolute paths only: a relative path would resolve against
            # whatever cwd the compositor happened to hand us.
            [[ $val == /?* ]] && CLIPHIST_DB_PATH="$val"
        done < "$DB_ENV_FILE"
    fi
    export CLIPHIST_DB_PATH="${CLIPHIST_DB_PATH:-$XDG_CACHE_HOME/cliphist/db}"
    # Widen the search index (cliphist truncates list previews itself).
    export CLIPHIST_PREVIEW_WIDTH="$CLIP_PREVIEW_WIDTH"
}

# cliphist's stdin protocol is "<id>\t<anything>". A here-string costs zero
# forks, unlike `printf ... | cliphist` (fork + pipe on every preview render).
cliphist_decode() { cliphist decode <<< "$1$TAB"; }

cliphist_decode_to_file() {
    is_uint "$1" || return 1
    cliphist_decode "$1" >"$2" 2>/dev/null
}

# decode_entry_to_tmp ID DIR TAG -> $REPLY
decode_entry_to_tmp() {
    local id="$1" dir="$2" tag="${3:-dec}" tmp

    new_tmp "$dir" "$tag" || return 1
    tmp="$REPLY"

    if cliphist_decode_to_file "$id" "$tmp"; then
        REPLY="$tmp"
        return 0
    fi

    remove_tmpfile "$tmp"
    REPLY=''
    return 1
}

mime_from_file() { file --mime-type -b -- "$1" 2>/dev/null; }
describe_file()  { file -b -- "$1" 2>/dev/null; }
mime_is_image()  { [[ ${1:-} == image/* ]]; }

generate_hash_file() {
    local line
    line=$(b2sum -- "$1" 2>/dev/null) || return 1        # BLAKE2b, no md5
    line="${line%% *}"
    printf '%s' "${line:0:16}"
}

# Split an fzf line into P_TYPE / P_ID without a subshell.
# Contract: BOTH globals are reset on entry, so a failed parse can never leave
# a previous item's values visible to the caller (the re-entrancy hazard of the
# global-output design). Every call site tests the return value.
parse_item() {
    local rest
    P_TYPE=''
    P_ID=''
    rest="${1#*"$SEP"}"
    [[ $rest == "$1" ]] && return 1            # no separator => not our item
    P_TYPE="${rest%%"$SEP"*}"
    P_ID="${rest#*"$SEP"}"
    P_ID="${P_ID%%"$SEP"*}"
    case $P_TYPE in
        empty|error) return 0 ;;
        pin) is_pin_hash "$P_ID" || { P_TYPE=''; P_ID=''; return 1; } ;;
        txt|img|bin) is_uint "$P_ID" || { P_TYPE=''; P_ID=''; return 1; } ;;
        *) P_TYPE=''; P_ID=''; return 1 ;;
    esac
    return 0
}

#==============================================================================
# GEOMETRY CAPTURE
#==============================================================================
write_preview_size() {
    [[ -n $SESSION_DIR && -d $SESSION_DIR ]] || return 0
    [[ ! -e $SESSION_DIR/geom_disarm ]] || return 0

    local pc="${FZF_PREVIEW_COLUMNS:-0}" tc="${FZF_COLUMNS:-0}"
    local pl="${FZF_PREVIEW_LINES:-0}" tl="${FZF_LINES:-0}"
    local tmp

    is_uint "$pc" && is_uint "$tc" &&
        is_uint "$pl" && is_uint "$tl" || return 0

    pc=$((10#$pc))
    tc=$((10#$tc))
    pl=$((10#$pl))
    tl=$((10#$tl))

    (( tc > 0 && tl > 0 && pc > 0 && pl > 0 )) || return 0

    new_tmp "$SESSION_DIR" geometry || return 0
    tmp="$REPLY"

    if ! printf '%s %s %s %s\n' "$pc" "$tc" "$pl" "$tl" >"$tmp"; then
        remove_tmpfile "$tmp"
        return 0
    fi

    if [[ -e $SESSION_DIR/geom_disarm ]]; then
        remove_tmpfile "$tmp"
        return 0
    fi

    # Publish the first complete sample only if no first sample exists.
    ln -- "$tmp" "$SESSION_DIR/preview_first" 2>/dev/null || :

    # Publish the latest complete sample by atomic rename.
    if mv -f -- "$tmp" "$SESSION_DIR/preview_size" 2>/dev/null; then
        untrack_tmpfile "$tmp"
    else
        remove_tmpfile "$tmp"
    fi
    return 0
}

disarm_geometry() {
    [[ -n $SESSION_DIR && -d $SESSION_DIR ]] || return 0

    # Publish disarm first. A concurrent geometry writer may still finish,
    # but persist_drag_resize will ignore its samples.
    : >"$SESSION_DIR/geom_disarm" 2>/dev/null || return 0
    rm -f -- "$SESSION_DIR/preview_first" \
        "$SESSION_DIR/preview_size" 2>/dev/null
    return 0
}

#==============================================================================
# TEXT PREVIEW
#==============================================================================
safe_print_text_file() {
    # Perl's core Encode module provides UTF-8 decoding without Python startup.
    # Bound raw input before decoding, normalization, and regex processing.
    perl - "$1" "${2:-0}" <<'PERL'
use strict;
use warnings;
use Encode qw(decode FB_DEFAULT);
use Errno qw(EINTR);

my ($path, $limit_arg) = @ARGV;
defined($limit_arg) && $limit_arg =~ /\A[0-9]+\z/
    or die "Invalid preview limit\n";

my $limit = 0 + $limit_arg;

open my $fh, '<:raw', $path
    or die "Cannot read text preview: $!\n";

# A UTF-8 character occupies at most four bytes. CRLF normalization uses at
# most two bytes per resulting character. Read enough to detect truncation
# without ever reading an unbounded line.
my $remaining = $limit ? 4 * ($limit + 1) : undef;
my $bytes = '';

while (!defined($remaining) || $remaining > 0) {
    my $want = 65536;
    $want = $remaining
        if defined($remaining) && $remaining < $want;

    my $chunk = '';
    my $count = sysread($fh, $chunk, $want);

    if (!defined($count)) {
        next if $! == EINTR;
        die "Cannot read text preview: $!\n";
    }
    last if $count == 0;

    $bytes .= $chunk;
    $remaining -= $count if defined($remaining);
}

close $fh or die "Cannot close text preview: $!\n";

# Invalid UTF-8 is replaced for display only; copying retains original bytes.
my $text = decode('UTF-8', $bytes, FB_DEFAULT);
$text =~ s/\r\n?/\n/g;

my $truncated = $limit && length($text) > $limit;
$text = substr($text, 0, $limit) if $truncated;

$text =~ s/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)//g;
$text =~ s/\x1bP[^\x1b]*\x1b\\//g;
$text =~ s/\x1b\[[0-?]*[ -\/]*[@-~]//g;
$text =~ s/\x1b[ -~]//g;
$text =~ s/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]/ /g;

$text .= "\n" unless $text =~ /\n\z/;

binmode STDOUT, ':encoding(UTF-8)'
    or die "Cannot configure preview output: $!\n";
print STDOUT $text or die "Cannot write text preview: $!\n";
close STDOUT or die "Cannot finish text preview: $!\n";

exit($truncated ? 10 : 0);
PERL
}

# bat cannot sniff a language from an extension-less temp file, so v3.0 pinned
# --language=txt and silently disabled the syntax highlighting it advertised.
# A four-byte peek recovers it for the formats that actually land in a
# clipboard. -> $REPLY
guess_language() {
    local head="${1#"${1%%[![:space:]]*}"}"       # left-trim, fork-free
    case $head in
        '#!'*bash*|'#!'*/sh*|'#!'*zsh*|'#!'*dash*) REPLY=bash ;;
        '#!'*python*)                              REPLY=python ;;
        '{'*|'['{*|'['\"*)                         REPLY=json ;;
        '<?xml'*|'<!DOCTYPE'*|'<html'*|'<svg'*)    REPLY=xml ;;
        'diff --git'*|'--- '*|'+++ '*|'@@ '*)      REPLY=diff ;;
        '---'|'---'[[:space:]]*)                   REPLY=yaml ;;
        '['*']')                                   REPLY=ini ;;
        'SELECT '*|'select '*|'INSERT '*)          REPLY=sql ;;
        *)                                         REPLY=txt ;;
    esac
    return 0
}

get_target_preview_width() {
    local fzf_width="${FZF_PREVIEW_COLUMNS:-0}" total_cols="${FZF_COLUMNS:-0}"
    is_uint "$fzf_width" || fzf_width=0
    is_uint "$total_cols" || total_cols=0

    if (( fzf_width > 4 )); then
        REPLY="$fzf_width"
        return 0
    fi

    state_load
    local layout="${STATE[PREVIEW_LAYOUT]:-}" calc=0

    if [[ $layout =~ (left|right),([0-9]+)% ]] && (( total_cols > 10 )); then
        local pct="${BASH_REMATCH[2]}"
        (( calc = (total_cols * pct / 100) - 2 ))
    elif [[ $layout =~ (up|down),([0-9]+)% ]] && (( total_cols > 10 )); then
        (( calc = total_cols - 2 ))
    fi

    if (( calc > 4 )); then
        REPLY="$calc"
    else
        REPLY=0
    fi
}

render_text_preview() {
    local path="$1" max="${2:-0}" tmp status head='' language rc=0 width=0
    local -a san_status=()

    new_tmp "$CACHE_DIR" textpreview || return 1
    tmp="$REPLY"

    get_target_preview_width
    width="$REPLY"

    if (( width > 4 )) && have fold; then
        # Word-fold the sanitized plain text before highlighting so fzf never
        # has to character-wrap a long line (fzf marks such wraps with `↳`).
        # Folding pre-highlighting keeps the width math exact: bat only adds
        # zero-width ANSI codes afterwards.
        safe_print_text_file "$path" "$max" 2>/dev/null |
            fold -s -w "$((width - 2))" >"$tmp" 2>/dev/null
        san_status=("${PIPESTATUS[@]}")
        status=${san_status[0]}
        if (( san_status[1] != 0 )); then
            safe_print_text_file "$path" "$max" >"$tmp"
            status=$?
        fi
    else
        safe_print_text_file "$path" "$max" >"$tmp"
        status=$?
    fi

    if (( status != 0 && status != 10 )); then
        remove_tmpfile "$tmp"
        return 1
    fi

    if have bat; then
        # Language detection only needs a small ASCII-oriented header peek.
        LC_ALL=C IFS= read -r -N 256 head <"$tmp" 2>/dev/null || :
        head="${head%%$'\n'*}"
        guess_language "$head"
        language="$REPLY"

        # Text is already word-folded to the pane width above; keep bat from
        # re-wrapping. Disable user bat configuration so it cannot introduce
        # a pager, decorations, or incompatible wrapping settings.
        bat --no-config --style=plain --color=always --paging=never \
            --wrap=never --language="$language" -- "$tmp" 2>/dev/null ||
            cat -- "$tmp" || rc=1
    else
        cat -- "$tmp" || rc=1
    fi

    remove_tmpfile "$tmp"

    if (( status == 10 )); then
        printf '\n\e[2m[… truncated …]\e[0m\n'
    fi
    return "$rc"
}

#==============================================================================
# IMAGE HANDLING
#==============================================================================
# Include the database path and observable file generation in the cache key.
# A bare cliphist ID is not globally unique: IDs can be reused after a wipe,
# and different databases can contain different payloads under the same ID.
image_cache_key() {
    local metadata line

    metadata=$(stat -L --printf='%d:%i:%s:%y:%z' \
        -- "$CLIPHIST_DB_PATH" 2>/dev/null) || return 1

    line=$(
        printf '%s\0%s\0' "$CLIPHIST_DB_PATH" "$metadata" |
            b2sum --length=256
    ) || return 1

    REPLY="${line%% *}"
    [[ $REPLY == +([[:xdigit:]]) && ${#REPLY} -eq 64 ]]
}

find_cached_image() {
    local path="$CACHE_DIR/img-$1-$2.img"
    if [[ -f $path && ! -L $path && -s $path ]]; then
        REPLY="$path"
        return 0
    fi
    REPLY=''
    return 1
}

remove_cached_files() {
    is_uint "$1" || return 1
    rm -f -- \
        "$CACHE_DIR/$1.img" \
        "$CACHE_DIR/$1.png" \
        "$CACHE_DIR"/img-*-"$1".img 2>/dev/null
}

# cache_image ID -> $REPLY
# If the database changes during decoding, return the valid temporary decode
# without publishing it under an obsolete cache key.
cache_image() {
    local id="$1" before='' after='' tmp mime path

    is_uint "$id" || return 1

    if image_cache_key; then
        before="$REPLY"
        find_cached_image "$before" "$id" && return 0
    fi

    decode_entry_to_tmp "$id" "$CACHE_DIR" img || return 1
    tmp="$REPLY"

    mime=$(mime_from_file "$tmp") || {
        remove_tmpfile "$tmp"
        REPLY=''
        return 1
    }
    mime_is_image "$mime" || {
        remove_tmpfile "$tmp"
        REPLY=''
        return 1
    }

    if [[ -n $before ]] && image_cache_key; then
        after="$REPLY"
        if [[ $before == "$after" ]]; then
            path="$CACHE_DIR/img-$before-$id.img"
            if mv -f -- "$tmp" "$path" 2>/dev/null; then
                untrack_tmpfile "$tmp"
                REPLY="$path"
                return 0
            fi
        fi
    fi

    # Still useful even when caching is unavailable. It remains tracked and
    # will be removed by this process's EXIT cleanup.
    REPLY="$tmp"
    return 0
}

#------------------------------------------------------------------------------
# display_image IMG
#------------------------------------------------------------------------------
# WHY EVERY CAPABILITY HANDSHAKE IS FORBIDDEN HERE
#   fzf runs the preview command with stdout on a PIPE (it reads the bytes and
#   repaints them into the pane), so isatty(1) is false. Consequences, all
#   verified against upstream docs rather than assumed:
#
#   * chafa(1): "-f, --format ... one of [iterm, kitty, sixels, symbols]. The
#     default is iterm, kitty or sixels IF THE CONNECTED TERMINAL SUPPORTS one
#     of these, falling back to symbols otherwise."  With a piped stdout chafa
#     cannot confirm the terminal, so auto-detection degrades to `symbols`.
#     Reproduced upstream from file managers (lf #2574: "chafa also fails to
#     detect sixel support from the terminal and falls back to symbols").
#   * `-f auto` IS NOT A VALID FORMAT — the enum has no `auto` member, and the
#     sixel member is spelled `sixels` (plural). v4.0 passed `-f auto`, so chafa
#     exited non-zero before emitting one byte and `2>/dev/null || return 1`
#     swallowed the diagnostic. THAT is the blank pane in foot.
#   * chafa 1.16+ `--probe=[auto|on|off]` with `--probe-mode=[any|ctty|stdio]`;
#     `ctty` is documented as probing /dev/tty "useful when chafa is part of a
#     pipeline". In an fzf preview that reads the DA/XTSMGRAPHICS reply out of
#     the terminal behind fzf's back and corrupts fzf's own input stream. We
#     pin `--probe off`: deterministic, and nothing can race fzf for /dev/tty.
#   * `--polite` was NOT the problem: chafa(1) says it merely "inhibits escape
#     sequences that on rare occasions may confuse the terminal", and it has
#     defaulted to OFF since 1.14. We still force it ON, because smcup/rmcup and
#     cursor-visibility games are exactly what must not leak out of a preview.
#   * `kitten icat --scale-up=no` is malformed: `--scale-up` is a bool-set FLAG,
#     not a valued option, so icat aborted on the command line. That is why the
#     kitty path rendered nothing either — independent of --unicode-placeholder.
#
# SO: decide the protocol from the environment, then emit exactly one hard-coded
# protocol, then degrade through a static chain. Never negotiate, never query.
#------------------------------------------------------------------------------
display_image() {
    local img="$1"
    local cols="${FZF_PREVIEW_COLUMNS:-40}" rows="${FZF_PREVIEW_LINES:-20}"
    local top="${FZF_PREVIEW_TOP:-0}" left="${FZF_PREVIEW_LEFT:-0}"
    local want="${CLIPFZF_IMAGE_BACKEND:-auto}"
    local term="${TERM:-}" tprog="${TERM_PROGRAM:-}"
    local family='' proto pad comm stat pid depth=0 off
    local -a chain=() cmd=()

    [[ -f $img && -s $img ]] || { printf '\e[31mImage not available\e[0m\n'; return 1; }

    # cmd_preview emits one description line and one blank line.
    off="${CLIPFZF_IMAGE_ROW_OFFSET:-2}"
    is_uint "$off"  || off=2
    is_uint "$top"  || top=0
    is_uint "$left" || left=0
    is_uint "$cols" || cols=40
    is_uint "$rows" || rows=20

    # Normalize before arithmetic; leading zeroes must not imply octal.
    off=$((10#$off))
    top=$((10#$top))
    left=$((10#$left))
    cols=$((10#$cols))
    rows=$((10#$rows))
    (( rows = rows - off - 2 ))            # 2 lines of bottom slack
    (( rows < 2 )) && rows=2
    (( cols = cols > 4 ? cols - 4 : 2 ))

    #--- 1. terminal family — environment markers only, zero escape sequences --
    if [[ $want == auto ]]; then
        if   [[ -n ${KITTY_WINDOW_ID:-}${KITTY_PID:-} || $term == *kitty* ]]; then family=kitty
        elif [[ $tprog == ghostty || -n ${GHOSTTY_RESOURCES_DIR:-}${GHOSTTY_BIN_DIR:-} \
                || $term == *ghostty* ]];                                     then family=ghostty
        elif [[ $tprog == WezTerm || -n ${WEZTERM_PANE:-}${WEZTERM_EXECUTABLE:-} ]]; then family=wezterm
        elif [[ $term == foot* || $tprog == foot ]];                          then family=foot
        elif [[ -n ${KONSOLE_VERSION:-}${KONSOLE_DBUS_SESSION:-} ]];          then family=konsole
        elif [[ $tprog == iTerm.app || ${LC_TERMINAL:-} == iTerm2 ]];         then family=iterm
        elif [[ -n ${ALACRITTY_WINDOW_ID:-}${ALACRITTY_SOCKET:-} || $term == alacritty* ]]; then family=alacritty
        elif [[ ${TERMINAL_NAME:-} == contour || -n ${CONTOUR_VERSION:-} ]];  then family=contour
        elif [[ $tprog == vscode ]];                                          then family=vscode
        elif [[ $tprog == mintty || $term == mintty* ]];                      then family=mintty
        elif [[ -n ${WT_SESSION:-} ]];                                        then family=wt
        elif [[ $term == *sixel* || $term == mlterm* || $term == yaft* ]];    then family=sixelterm
        fi

        # TERM lies constantly (TERM=xterm-256color is epidemic, and fzf's
        # preview child inherits it verbatim). When no marker matched, identify
        # the real emulator from /proc: it is ALWAYS an ancestor of the preview
        # process (preview sh -> fzf -> this script -> emulator). Pure reads,
        # zero forks, zero escape sequences — the only query-free ground truth.
        if [[ -z $family ]]; then
            pid="$PPID"
            while is_uint "$pid" && (( pid > 1 && depth++ < 12 )); do
                [[ -r /proc/$pid/comm ]] || break
                IFS= read -r comm < "/proc/$pid/comm" || break
                case $comm in
                    kitty)               family=kitty ;;
                    ghostty)             family=ghostty ;;
                    wezterm-gui|wezterm) family=wezterm ;;
                    foot|footclient)     family=foot ;;
                    konsole)             family=konsole ;;
                    alacritty)           family=alacritty ;;
                    contour)             family=contour ;;
                    mlterm|yaft)         family=sixelterm ;;
                esac
                [[ -n $family ]] && break
                [[ -r /proc/$pid/stat ]] || break
                IFS= read -r stat < "/proc/$pid/stat" || break
                stat="${stat##*) }"; stat="${stat#* }"; stat="${stat%% *}"
                is_uint "$stat" || break
                pid="$stat"
            done
        fi

        case $family in
            kitty)                     want=icat   ;;  # kitten, absolute place
            ghostty)                   want=kitty  ;;  # kitty proto; NO sixel,
                                                       # NO iterm (ghostty#3054)
            wezterm|iterm)             want=iterm  ;;  # iTerm2 inline images
            foot|konsole|contour|alacritty|vscode|mintty|wt|sixelterm)
                                       want=sixels ;;
            *)                         want=symbols ;;  # universal ANSI art
        esac
    fi

    #--- 2. normalise the override vocabulary ---------------------------------
    case $want in
        icat|kitty-icat)              want=icat ;;
        placeholder|icat-placeholder) want=placeholder ;;
        kitty|kitty-chafa)            want=kitty ;;
        iterm|iterm2)                 want=iterm ;;
        sixel|sixels)                 want=sixels ;;
        symbols|ansi|blocks|chafa)    want=symbols ;;
        none|off|disabled)            want=none ;;
        *)                            want=symbols ;;
    esac

    # Absolute cell placement is meaningless inside a multiplexer pane, so route
    # icat through chafa's relative kitty output, which honours --passthrough.
    [[ -n ${TMUX:-}${STY:-} && ( $want == icat || $want == placeholder ) ]] && want=kitty

    #--- 3. static degradation chain ------------------------------------------
    case $want in
        icat)        chain=(icat kitty symbols) ;;
        placeholder) chain=(placeholder kitty symbols) ;;
        kitty)       chain=(kitty symbols) ;;
        iterm)       chain=(iterm symbols) ;;
        sixels)      chain=(sixels symbols) ;;
        symbols)     chain=(symbols) ;;
        none)        chain=() ;;
    esac

    printf -v pad '%*s' "$rows" ''
    pad="${pad// /$'\n'}"                   # exactly $rows newlines, fork-free

    for proto in "${chain[@]}"; do
        case $proto in
            icat|placeholder)
                have kitten || continue
                # Retire images from the previous render. Sent unconditionally
                # because cmd_preview's kitty_purge is gated on is_kitty(),
                # which is false in every other kitty-protocol terminal.
                printf '\e_Ga=d,d=A\e\\'
                # NOTE: no --scale-up. It is a bool-set flag; `--scale-up=no`
                # made icat reject the command line outright in v4.0.
                cmd=(kitten icat --clear --stdin=no --transfer-mode=memory)
                if [[ $proto == placeholder ]]; then
                    # fzf's own bin/fzf-preview.sh recipe: the image is anchored
                    # to text cells fzf itself positions. Requires the pane to
                    # NOT re-wrap them, so it is opt-in — this script ships
                    # `wrap-word` in PREVIEW_LAYOUT by default, which shreds the
                    # placeholder row/column diacritics.
                    cmd+=(--unicode-placeholder --place="${cols}x${rows}@0x0")
                else
                    # Absolute placement, but computed from the documented
                    # FZF_PREVIEW_TOP / FZF_PREVIEW_LEFT exports instead of the
                    # hard-coded @0x1 of v2.5 — correct for every layout, and
                    # immune to preview-pane word wrapping.
                    cmd+=(--place="${cols}x${rows}@${left}x$((top + off))")
                fi
                "${cmd[@]}" -- "$img" 2>/dev/null || continue
                # --place leaves the cursor untouched, so reserve the rows by
                # hand or fzf believes the preview is empty.
                [[ $proto == icat ]] && printf '%s' "$pad"
                return 0 ;;

            kitty|iterm|sixels|symbols)
                have chafa || continue
                # Format is HARD-CODED. --probe off: never touch /dev/tty from
                # inside a preview. --relative off: rows separated by newlines
                # so fzf can count them (chafa(1) recommends this for pagers).
                cmd=(chafa --format "$proto" --size "${cols}x${rows}"
                     --animate off --polite on --probe off --relative off)
                if   [[ -n ${TMUX:-} ]]; then cmd+=(--passthrough tmux)
                elif [[ -n ${STY:-} ]];  then cmd+=(--passthrough screen)
                fi
                case $proto in
                    kitty)   printf '\e_Ga=d,d=A\e\\' ;;
                    symbols) case ${COLORTERM:-} in
                                 truecolor|24bit) cmd+=(--colors full) ;;
                                 *)               cmd+=(--colors 256)  ;;
                             esac ;;
                esac
                "${cmd[@]}" -- "$img" 2>/dev/null || continue
                printf '\n'          # close the graphics block for fzf
                return 0 ;;
        esac
    done

    [[ $want == none ]] && return 0
    printf '\e[33mNo usable image backend (install chafa).\e[0m\n'
    printf '\e[2mDetected: %s · tried: %s\e[0m\n' "${family:-unknown}" "${chain[*]:-none}"
    printf '\e[2mOverride: CLIPFZF_IMAGE_BACKEND=icat|placeholder|kitty|iterm|sixels|symbols|none\e[0m\n'
    return 1
}

#==============================================================================
# COPY PATHS
#==============================================================================
# The ordinary clipboard is required. PRIMARY is best-effort because not every
# compositor/session supports that selection protocol.
wl_put() {
    local mime="$1" path="$2"

    wl-copy --type "$mime" <"$path" || return 1

    if ! wl-copy --primary --type "$mime" <"$path" 2>/dev/null; then
        notify 'Copied to clipboard' 'PRIMARY selection could not be updated.'
    fi
    return 0
}

copy_text_entry() {
    local tmp rc

    decode_entry_to_tmp "$1" "$CACHE_DIR" cp || return 1
    tmp="$REPLY"

    wl_put 'text/plain;charset=utf-8' "$tmp"
    rc=$?
    remove_tmpfile "$tmp"
    return "$rc"
}

copy_binary_entry() {
    local tmp mime rc

    decode_entry_to_tmp "$1" "$CACHE_DIR" cp || return 1
    tmp="$REPLY"

    mime=$(mime_from_file "$tmp") || {
        remove_tmpfile "$tmp"
        return 1
    }

    wl_put "${mime:-application/octet-stream}" "$tmp"
    rc=$?
    remove_tmpfile "$tmp"
    return "$rc"
}

cmd_copy_single() {
    local path

    case $1 in
        pin)
            is_pin_hash "$2" || return 1
            path="$PINS_DIR/$2.pin"
            [[ -f $path && ! -L $path && -r $path ]] || return 1
            wl_put 'text/plain;charset=utf-8' "$path"
            ;;
        img|bin)
            # The list's image classification is heuristic. Determine the MIME
            # type from the actual payload when copying either binary class.
            copy_binary_entry "$2"
            ;;
        txt)
            copy_text_entry "$2"
            ;;
        *)
            return 1
            ;;
    esac
}

cmd_batch_copy() {
    local item tmp='' part='' last_byte rc=0
    local n_text=0 n_other=0 last_t='' last_i=''

    new_tmp "$CACHE_DIR" batch || return 1
    tmp="$REPLY"

    for item in "$@"; do
        parse_item "$item" || continue

        case $P_TYPE in
            txt|pin)
                # Prepare each complete payload separately. A failed decode
                # must never leave partial bytes in the combined clipboard.
                if [[ $P_TYPE == txt ]]; then
                    if ! decode_entry_to_tmp "$P_ID" "$CACHE_DIR" part; then
                        rc=1
                        break
                    fi
                    part="$REPLY"
                else
                    if [[ ! -f $PINS_DIR/$P_ID.pin ||
                          -L $PINS_DIR/$P_ID.pin ||
                          ! -r $PINS_DIR/$P_ID.pin ]]; then
                        rc=1
                        break
                    fi

                    if ! new_tmp "$CACHE_DIR" part; then
                        rc=1
                        break
                    fi
                    part="$REPLY"

                    if ! cp -- "$PINS_DIR/$P_ID.pin" "$part"; then
                        rc=1
                        break
                    fi
                fi

                if [[ -s $part ]]; then
                    if [[ -s $tmp ]]; then
                        last_byte=$(tail -c 1 -- "$tmp") || {
                            rc=1
                            break
                        }
                        if [[ -n $last_byte ]]; then
                            printf '\n' >>"$tmp" || {
                                rc=1
                                break
                            }
                        fi
                    fi

                    cat -- "$part" >>"$tmp" || {
                        rc=1
                        break
                    }
                fi

                remove_tmpfile "$part"
                part=''
                (( ++n_text ))
                ;;
            img|bin)
                last_t="$P_TYPE"
                last_i="$P_ID"
                (( ++n_other ))
                ;;
            *)
                continue
                ;;
        esac
    done

    [[ -z $part ]] || remove_tmpfile "$part"

    if (( rc != 0 )); then
        remove_tmpfile "$tmp"
        notify 'Copy failed' \
            'A selected entry could not be read completely; nothing was copied.' critical
        return 1
    fi

    if (( n_text > 0 )); then
        wl_put 'text/plain;charset=utf-8' "$tmp"
        rc=$?
        if (( rc == 0 && n_other > 0 )); then
            notify 'Mixed selection' \
                'Images/binaries skipped — copied concatenated text.'
        fi
    elif (( n_other > 0 )); then
        cmd_copy_single "$last_t" "$last_i"
        rc=$?
        if (( rc == 0 && n_other > 1 )); then
            notify 'Multiple binaries' \
                'Cannot merge — copied the last selected item.'
        fi
    fi

    remove_tmpfile "$tmp"

    if (( rc != 0 )); then
        notify 'Copy failed' 'The clipboard could not be updated.' critical
    fi
    return "$rc"
}

#==============================================================================
# LIST GENERATION
#==============================================================================
cmd_list() {
    local n=0 preview pins_tmp pins_rc pin hash content mtime
    local -a st=()
    local -a pin_paths=("$PINS_DIR"/*.pin)

    # Empty pins directory: no interpreter, sorting, or temporary-file work.
    if (( ${#pin_paths[@]} )); then
        new_tmp "$CACHE_DIR" pinlist || {
            printf '  (cannot prepare clipboard list)%s%s%s\n' \
                "$SEP" error "$SEP"
            return 0
        }
        pins_tmp="$REPLY"

        # Bash performs bounded reads only. All content substitutions happen
        # in one gawk process: never use Bash's pathological //+( )/ pattern.
        {
            while IFS="$TAB" read -r -d '' mtime pin; do
                [[ -f $pin && ! -L $pin && -r $pin ]] || continue

                hash="${pin##*/}"
                hash="${hash%.pin}"
                is_pin_hash "$hash" || continue

                content=''
                # A short read returns nonzero at EOF but still supplies the
                # complete short payload. Pins are text; Bash cannot hold NUL.
                {
                    IFS= read -r -d '' -N "$LIST_TRUNC" content <"$pin"
                } 2>/dev/null || :

                printf '%s%s%s\0' "$hash" "$SEP" "$content" || exit 1
            done < <(
                find "$PINS_DIR" -maxdepth 1 -type f -name '*.pin' \
                    -printf '%T@\t%p\0' 2>/dev/null |
                    sort -z -rn
            )
        } | LC_ALL=C.UTF-8 gawk \
            -v sep="$SEP" -v icon_pin="$ICON_PIN" '
            BEGIN { RS = "\000" }
            {
                p = index($0, sep)
                if (!p) next

                hash = substr($0, 1, p - 1)
                preview = substr($0, p + 1)

                gsub(/[[:cntrl:]]/, " ", preview)
                gsub(/ +/, " ", preview)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", preview)

                if (preview == "") preview = "[Whitespace]"

                printf "%s %s%s%s%s%s\n", \
                    icon_pin, preview, sep, "pin", sep, hash
            }
        ' >"$pins_tmp"
        pins_rc=$?

        if (( pins_rc == 0 )); then
            while IFS= read -r preview; do
                printf '%d %s\n' "$((++n))" "$preview"
            done <"$pins_tmp"
        else
            printf '%d (pinned entries unavailable)%s%s%s\n' \
                "$((++n))" "$SEP" error "$SEP"
        fi

        remove_tmpfile "$pins_tmp"
    fi

    # Used by the initial fzf load event to skip the pinned rows.
    if [[ -n $SESSION_DIR && -d $SESSION_DIR ]]; then
        printf '%d\n' "$n" >"$SESSION_DIR/initial-pin-count"
    fi

    cliphist list 2>/dev/null | LC_ALL=C.UTF-8 gawk \
        -v pin_count="$n" -v icon_img="$ICON_IMG" -v icon_bin="$ICON_BIN" \
        -v sep="$SEP" -v max_len="$LIST_TRUNC" '
        BEGIN { FS = "\t"; n = 0 }
        NF < 2 { next }
        $1 !~ /^[0-9]+$/ { next }
        {
            id = $1
            content = $0
            sub(/^[^\t]*\t/, "", content)
            idx = ++n + pin_count

            if (content ~ /^\[\[[[:space:]]*binary data/) {
                lc = tolower(content); dims = ""; fmt = ""
                if (match(content, /[0-9]+[xX][0-9]+/)) {
                    dims = substr(content, RSTART, RLENGTH)
                    gsub(/[xX]/, "×", dims)
                }
                nk = split("png:PNG jpeg:JPG jpg:JPG gif:GIF webp:WebP bmp:BMP " \
                           "tiff:TIFF svg:SVG avif:AVIF heic:HEIF heif:HEIF " \
                           "jxl:JXL ico:ICO pnm:PNM ppm:PNM pgm:PNM pbm:PNM " \
                           "tga:TGA qoi:QOI", kv, " ")
                for (i = 1; i <= nk; i++) {
                    p = index(kv[i], ":")
                    if (index(lc, substr(kv[i], 1, p - 1))) {
                        fmt = substr(kv[i], p + 1)
                        break
                    }
                }
                if (dims != "" || fmt != "") {
                    info = (dims != "" && fmt != "") \
                        ? dims " " fmt : (dims != "" ? dims : fmt)
                    printf "%d \033[36m%s %s\033[0m%s%s%s%s\n", \
                        idx, icon_img, info, sep, "img", sep, id
                    next
                }
                info = content
                sub(/^\[\[[[:space:]]*binary data[[:space:]]*/, "", info)
                sub(/[[:space:]]*\]\]$/, "", info)
                gsub(/[[:cntrl:]]/, " ", info)
                gsub(/  +/, " ", info)
                gsub(/^ +| +$/, "", info)
                if (info == "") info = "Binary"
                if (length(info) > max_len) info = substr(info, 1, max_len)
                printf "%d %s %s%s%s%s%s\n", \
                    idx, icon_bin, info, sep, "bin", sep, id
                next
            }

            gsub(/[[:cntrl:]]/, " ", content)
            gsub(/  +/, " ", content)
            gsub(/^ +| +$/, "", content)
            if (content == "") content = "[Whitespace]"
            if (length(content) > max_len) content = substr(content, 1, max_len)
            printf "%d %s%s%s%s%s\n", idx, content, sep, "txt", sep, id
        }
        END { exit (n > 0 ? 0 : 20) }'
    st=("${PIPESTATUS[@]}")

    if (( st[0] != 0 )); then
        if [[ ! -e $CLIPHIST_DB_PATH && ! -L $CLIPHIST_DB_PATH ]] &&
            have cliphist; then
            (( n > 0 )) ||
                printf '  (clipboard empty)%s%s%s\n' "$SEP" empty "$SEP"
        else
            printf '  (clipboard backend unavailable)%s%s%s\n' \
                "$SEP" error "$SEP"
        fi
    elif (( st[1] != 0 && st[1] != 20 )); then
        printf '  (clipboard list processing failed)%s%s%s\n' \
            "$SEP" error "$SEP"
    elif (( st[1] == 20 && n == 0 )); then
        printf '  (clipboard empty)%s%s%s\n' "$SEP" empty "$SEP"
    fi
    return 0
}

#==============================================================================
# LAYOUT MUTATION  (synchronous transform callbacks)
#==============================================================================
# These are bound with `transform`, NOT `bg-transform`. bg-transform runs the
# child asynchronously, so holding Alt-Left interleaves N read-modify-write
# cycles whose completion order fzf does not guarantee: increments are lost and
# change-preview-window actions can be applied out of order. A synchronous
# transform serialises them by construction. The whole read-modify-write also
# happens inside ONE flock, closing the cross-process race as well.
cmd_move_preview() {
    local dir="${1:-}" cur last base pct rest next

    case $dir in
        left|right|up|down|hidden) ;;
        *) return 0 ;;
    esac

    state_lock || {
        printf 'bell'
        return 0
    }
    state_load

    cur="${STATE[PREVIEW_LAYOUT]}"
    last="${STATE[PREVIEW_LAST]}"
    base="$cur"
    [[ $base == hidden ]] && base="$last"

    if [[ $base =~ $VISIBLE_LAYOUT_RE ]]; then
        pct="${BASH_REMATCH[2]}"
        rest="${BASH_REMATCH[3]}"
    else
        pct=45
        rest=',wrap-word'
    fi

    if [[ $dir == hidden ]]; then
        if [[ $cur == hidden ]]; then
            next="$last"
        else
            next=hidden
        fi
    else
        next="$dir,$pct%$rest"
    fi

    state_stage PREVIEW_LAYOUT "$next"
    [[ $next == hidden ]] || state_stage PREVIEW_LAST "$next"

    if ! state_flush; then
        state_unlock
        printf 'bell'
        return 0
    fi

    state_unlock
    disarm_geometry
    printf 'change-preview-window(%s)+refresh-preview' "$next"
}

cmd_resize_preview() {
    local dir="${1:-}" cur pct rest edge new next

    case $dir in
        left|right|up|down) ;;
        *) return 0 ;;
    esac

    state_lock || {
        printf 'bell'
        return 0
    }
    state_load
    cur="${STATE[PREVIEW_LAYOUT]}"

    if [[ $cur == hidden ]] || ! [[ $cur =~ $VISIBLE_LAYOUT_RE ]]; then
        state_unlock
        return 0
    fi

    edge="${BASH_REMATCH[1]}"
    pct="${BASH_REMATCH[2]}"
    rest="${BASH_REMATCH[3]}"
    new=$pct

    case "$edge:$dir" in
        right:left|left:right|up:down|down:up) (( new += 5 )) ;;
        right:right|left:left|up:up|down:down) (( new -= 5 )) ;;
        *)
            state_unlock
            return 0
            ;;
    esac

    (( new < 10 )) && new=10
    (( new > 90 )) && new=90

    if (( new == pct )); then
        state_unlock
        printf 'bell'
        return 0
    fi

    next="$edge,$new%$rest"
    state_stage PREVIEW_LAYOUT "$next" PREVIEW_LAST "$next"

    if ! state_flush; then
        state_unlock
        printf 'bell'
        return 0
    fi

    state_unlock
    disarm_geometry
    printf 'change-preview-window(%s)+refresh-preview' "$next"
}

#==============================================================================
# MODE TOGGLES — emitted as fzf action strings (transform)
#==============================================================================
# Single fzf process, single binding table. No abort/respawn loop, no query
# round-trip through a temp file, no lost multi-selection, no flicker.
emit_vim_actions() {
    if [[ $1 == true ]]; then
        printf 'rebind(%s)+disable-search+change-prompt(%s)+refresh-preview' "$VIM_KEYS" "$PROMPT_VIM"
    else
        printf 'unbind(%s)+enable-search+change-prompt(%s)+refresh-preview' "$VIM_KEYS" "$PROMPT_NORMAL"
    fi
}

cmd_vim_init() { state_load; emit_vim_actions "${STATE[VIM_MODE]}"; }

cmd_toggle_vim() {
    local next=true

    state_lock || {
        printf 'bell'
        return 0
    }
    state_load

    [[ ${STATE[VIM_MODE]} == true ]] && next=false
    state_stage VIM_MODE "$next"

    if ! state_flush; then
        state_unlock
        printf 'bell'
        return 0
    fi

    state_unlock
    emit_vim_actions "$next"
}

# Esc is the one key whose meaning depends on three different fzf states, so it
# is resolved from state that lives INSIDE fzf and is exported to every child
# (fzf(1), "ENVIRONMENT VARIABLES EXPORTED TO CHILD PROCESSES"):
#   FZF_PROMPT       current prompt string
#   FZF_INPUT_STATE  enabled | disabled | hidden
# v3.0 hard-bound esc to abort, so the documented "Esc leaves search mode" was
# a lie: it quit the picker instead.
cmd_key_escape() {
    if [[ ${FZF_PROMPT-} == *"$MARK_SEARCH"* ]]; then
        emit_vim_actions true                       # search -> vim normal
    elif [[ ${FZF_INPUT_STATE-} == disabled || ${FZF_PROMPT-} == *"$MARK_VIM"* ]]; then
        printf 'ignore'                             # vim normal: Esc is a no-op
    else
        printf 'abort'
    fi
}

# Help overlay state lives in fzf itself (FZF_PREVIEW_LABEL), never in a
# sentinel file, so a crashed session cannot leave the next run stuck in help.
cmd_toggle_help() {
    if [[ ${FZF_PREVIEW_LABEL-} == *Help* ]]; then
        printf 'change-preview(%s --preview {2} {3})+change-preview-label(%s)' "$SELF_REF" "$LABEL_PREVIEW"
    else
        printf 'change-preview(%s --help-pane)+change-preview-label(%s)' "$SELF_REF" "$LABEL_HELP"
    fi
}

# Destroying the entire history on a single unconfirmed keypress was the
# sharpest edge in the whole UI. Two-stage arm/fire, with the armed state in
# the per-session directory so it cannot outlive the process.
cmd_confirm_wipe() {
    local f armed=0 now t
    printf -v now '%(%s)T' -1
    if [[ -n $SESSION_DIR ]]; then
        f="$SESSION_DIR/wipe_armed"
        if [[ -f $f ]] && read -r t <"$f" 2>/dev/null && is_uint "$t" \
           && (( now >= t && now - t <= WIPE_ARM_SECONDS )); then
            armed=1
        fi
    fi
    if (( armed )); then
        rm -f -- "$f" 2>/dev/null
        printf 'execute-silent(%s --wipe)+reload-sync(%s --list)+clear-multi+change-border-label(%s)' \
               "$SELF_REF" "$SELF_REF" "$CLIPFZF_BORDER_MAIN"
    else
        [[ -n $SESSION_DIR ]] && printf '%s\n' "$now" >"$f" 2>/dev/null
        printf 'change-border-label(%s)+bell' "$HEADER_WIPE"
    fi
}

cmd_help_pane() {
    write_preview_size
    state_load
    local k=$'\e[33m' c=$'\e[36m' r=$'\e[0m' mode=STANDARD
    [[ ${STATE[VIM_MODE]} == true ]] && mode=VIM
    printf '\e[1;36m━━━ 󰌵 KEYBINDINGS (%s) ━━━%s\n\n' "$mode" "$r"
    printf '  %sF1%s          : Toggle help pane\n' "$k" "$r"
    printf '  %sAlt-M%s       : Toggle Vim keys\n\n' "$k" "$r"
    if [[ $mode == VIM ]]; then
        printf '  %s[ MOVEMENT & SEARCH ]%s\n' "$c" "$r"
        printf '  %sj / k%s       : Down / Up\n' "$k" "$r"
        printf '  %sg / G%s       : Top / Bottom\n' "$k" "$r"
        printf '  %sCtrl-D/U%s    : Half page down / up\n' "$k" "$r"
        printf '  %s/%s           : Enter search mode\n' "$k" "$r"
        printf '  %sEsc%s         : Leave search mode\n\n' "$k" "$r"
        printf '  %s[ SELECTION ]%s\n' "$c" "$r"
        printf '  %sv / V%s       : Toggle selection\n' "$k" "$r"
        printf '  %sJ / K%s       : Toggle + move down / up\n' "$k" "$r"
        printf '  %sCtrl-A%s      : Select all\n\n' "$k" "$r"
    fi
    printf '  %s[ PREVIEW ]%s\n' "$c" "$r"
    printf '  %sAlt-H/J/K/L%s : Move pane\n' "$k" "$r"
    printf '  %sAlt-Arrows%s  : Resize pane\n' "$k" "$r"
    printf '  %sAlt-V%s       : Toggle pane\n' "$k" "$r"
    printf '  %sShift-Up/Dn%s : Scroll preview\n\n' "$k" "$r"
    printf '  %s[ FILTERS ]%s\n' "$c" "$r"
    printf '  %sAlt-T%s       : Filter text\n' "$k" "$r"
    printf '  %sAlt-I%s       : Filter images\n' "$k" "$r"
    printf '  %sAlt-P%s       : Filter pins\n' "$k" "$r"
    printf '  %sAlt-B%s       : Filter binaries\n' "$k" "$r"
    printf '  %sAlt-X%s       : Clear filter\n\n' "$k" "$r"
    printf '  %s[ ACTIONS ]%s\n' "$c" "$r"
    printf '  %sAlt-A%s       : Pin / unpin selection\n' "$k" "$r"
    printf '  %sAlt-D%s       : Delete selection\n' "$k" "$r"
    printf '  %sAlt-W%s       : Wipe history\n' "$k" "$r"
    printf '  %sCtrl-R%s      : Reload list\n' "$k" "$r"
    printf '  %sTab%s         : Multi-select\n' "$k" "$r"
    printf '  %sEnter%s       : Copy selection and exit\n' "$k" "$r"
    if [[ $mode == VIM ]]; then
        printf '  %sq / Ctrl-C%s  : Quit\n' "$k" "$r"
    else
        printf '  %sEsc / Ctrl-C%s: Quit\n' "$k" "$r"
    fi
    return 0
}

#==============================================================================
# PREVIEW RENDERER
#==============================================================================
format_ts() {
    local ts="${1:-}" now week day time date_s
    is_uint "$ts" || { printf '[ 󰥔 Unknown ]'; return 1; }
    printf -v now '%(%s)T' -1
    (( week = now - 604800 ))
    printf -v day  '%(%a)T' "$ts"
    printf -v time '%(%-I:%M %p)T' "$ts"
    if (( ts >= week )); then
        printf '[ 󰥔 %s %s ]' "${day@U}" "$time"
    else
        printf -v date_s '%(%m/%d)T' "$ts"
        printf '[ 󰥔 %s %s %s ]' "$date_s" "${day@U}" "$time"
    fi
}

cmd_preview() {
    local type="${1:-}" id="${2:-}" pin_file img info tmp mtime

    # Pins arrive before history. Skip their initial preview until the
    # one-time load action has finished positioning the cursor.
    # Non-pinned entries and standalone previews are unaffected.
    if [[ $type == pin && -n $SESSION_DIR &&
          ! -e $SESSION_DIR/initial-focus-ready ]]; then
        return 0
    fi

    write_preview_size
    is_kitty && kitty_purge

    [[ -n $type ]] || { printf '\e[2mNo selection.\e[0m\n'; return 0; }

    case $type in
        empty)
            printf '\n\e[2mClipboard is empty.\nCopy something to get started.\e[0m\n' ;;
        error)
            printf '\n\e[31mClipboard backend unavailable.\e[0m\n'
            printf '\e[2mCheck: cliphist list | head\nCLIPHIST_DB_PATH=%s\e[0m\n' "${CLIPHIST_DB_PATH:-unset}" ;;
        pin)
            is_pin_hash "$id" || { printf '\e[31mInvalid pin id.\e[0m\n'; return 1; }
            pin_file="${PINS_DIR:?}/$id.pin"
            if [[ -f $pin_file && ! -L $pin_file ]]; then
                render_text_preview "$pin_file" "$PREVIEW_TEXT_LIMIT" ||
                    printf '\n\e[31mFailed to render pin.\e[0m\n'
            else
                printf '\e[31mPin file missing.\e[0m\n'
            fi ;;
        img)
            is_uint "$id" || { printf '\e[31mInvalid image id.\e[0m\n'; return 1; }
            if cache_image "$id"; then
                img="$REPLY"
                info=$(describe_file "$img")
                printf '\e[36m%s\e[0m\n\n' "${info:0:120}"
                display_image "$img"
            else
                printf '\n\e[31mFailed to decode image.\e[0m\n'
            fi ;;
        bin)
            is_uint "$id" || { printf '\e[31mInvalid binary id.\e[0m\n'; return 1; }
            decode_entry_to_tmp "$id" "$CACHE_DIR" bin || { printf '\e[31mDecode failed.\e[0m\n'; return 1; }
            tmp="$REPLY"
            info=$(describe_file "$tmp")
            printf '\e[36m%s\e[0m\n\n' "${info:0:120}"
            if mime_is_image "$(mime_from_file "$tmp")"; then
                display_image "$tmp"
            else
                printf '\e[2mNo visual preview for this type.\e[0m\n'
            fi
            remove_tmpfile "$tmp" ;;
        txt)
            is_uint "$id" || { printf '\e[31mInvalid text id.\e[0m\n'; return 1; }
            decode_entry_to_tmp "$id" "$CACHE_DIR" txt || { printf '\e[31mDecode failed.\e[0m\n'; return 1; }
            tmp="$REPLY"
            render_text_preview "$tmp" "$PREVIEW_TEXT_LIMIT" ||
                printf '\n\e[31mFailed to render text.\e[0m\n'
            remove_tmpfile "$tmp" ;;
        *)
            printf '\e[31mUnknown type: %q\e[0m\n' "$type"; return 1 ;;
    esac
    return 0
}

#==============================================================================
# BATCH ACTIONS
#==============================================================================
cmd_batch_pin() {
    local file="${1:-}" line tmp hash target
    local pinned=0 unpinned=0 failed=0

    [[ -f $file && -r $file ]] || return 1

    while IFS= read -r line || [[ -n $line ]]; do
        parse_item "$line" || continue

        case $P_TYPE in
            pin)
                target="$PINS_DIR/$P_ID.pin"
                [[ -e $target || -L $target ]] || continue
                if rm -f -- "$target" 2>/dev/null; then
                    (( ++unpinned ))
                else
                    (( ++failed ))
                fi
                ;;
            txt)
                if ! decode_entry_to_tmp "$P_ID" "$PINS_DIR" pin; then
                    (( ++failed ))
                    continue
                fi
                tmp="$REPLY"

                if ! hash=$(generate_hash_file "$tmp"); then
                    remove_tmpfile "$tmp"
                    (( ++failed ))
                    continue
                fi

                target="$PINS_DIR/$hash.pin"
                if mv -f -- "$tmp" "$target" 2>/dev/null; then
                    untrack_tmpfile "$tmp"
                    (( ++pinned ))
                else
                    remove_tmpfile "$tmp"
                    (( ++failed ))
                fi
                ;;
        esac
    done <"$file"

    if (( failed )); then
        notify 'Some pin operations failed' \
            "Pinned: $pinned; unpinned: $unpinned; failed: $failed." critical
        return 1
    fi

    (( pinned || unpinned )) &&
        notify 'Pins updated' "＋$pinned  −$unpinned"
    return 0
}

cmd_batch_delete() {
    local file="${1:-}" line target
    local removed=0 failed=0
    local -A seen=()

    [[ -f $file && -r $file ]] || return 1

    while IFS= read -r line || [[ -n $line ]]; do
        parse_item "$line" || continue

        case $P_TYPE in
            pin)
                [[ ! -v seen[pin:$P_ID] ]] || continue
                seen["pin:$P_ID"]=1

                target="$PINS_DIR/$P_ID.pin"
                [[ -e $target || -L $target ]] || continue
                if rm -f -- "$target" 2>/dev/null; then
                    (( ++removed ))
                else
                    (( ++failed ))
                fi
                ;;
            txt|img|bin)
                [[ ! -v seen[hist:$P_ID] ]] || continue
                seen["hist:$P_ID"]=1

                if cliphist delete <<<"$P_ID$TAB" 2>/dev/null; then
                    (( ++removed ))
                    remove_cached_files "$P_ID" || :
                else
                    (( ++failed ))
                fi
                ;;
        esac
    done <"$file"

    if (( failed )); then
        notify 'Some deletions failed' \
            "Deleted: $removed; failed: $failed." critical
        return 1
    fi

    (( removed )) && notify 'Deleted' "$removed item(s)"
    return 0
}

cmd_wipe() {
    local status

    cliphist wipe 2>/dev/null
    status=$?

    if (( status != 0 )); then
        notify 'Wipe failed' 'The history could not be cleared.' critical
        return "$status"
    fi

    # Do not delete .clipfzf-* files: another preview/copy/pin operation may
    # currently own one. Cache-key generation also invalidates old DB data.
    rm -f -- "$CACHE_DIR"/*.img "$CACHE_DIR"/*.png 2>/dev/null ||
        log_err 'History cleared, but some cached images could not be removed'

    notify 'Clipboard wiped' 'History cleared (pins kept).'
    return 0
}

cmd_prune_cache() {
    local -A live=()
    local ids='' have_ids=0 id path base rest dir pid

    if ids=$(
        CLIPHIST_PREVIEW_WIDTH=1 cliphist list 2>/dev/null |
            gawk -F '\t' '$1 ~ /^[0-9]+$/ { print $1 }'
    ); then
        have_ids=1
        while IFS= read -r id; do
            is_uint "$id" && live["$id"]=1
        done <<<"$ids"
    fi

    # Only prune by live IDs when the database read actually succeeded.
    if (( have_ids )); then
        for path in "$CACHE_DIR"/*.img "$CACHE_DIR"/*.png; do
            [[ -L $path ]] && {
                rm -f -- "$path"
                continue
            }
            [[ -f $path ]] || continue

            base="${path##*/}"
            id="${base%.*}"
            id="${id##*-}"

            if ! is_uint "$id" || [[ ! -v live[$id] ]]; then
                rm -f -- "$path"
            fi
        done
    fi

    # Reap temporary files only when their creator no longer exists.
    for path in \
        "$CACHE_DIR/$TMP_PREFIX"-* \
        "$PINS_DIR/$TMP_PREFIX"-* \
        "$SETTINGS_DIR/$TMP_PREFIX"-*
    do
        [[ -f $path ]] || continue

        base="${path##*/}"
        rest="${base#"$TMP_PREFIX"-}"
        rest="${rest#*-}"
        pid="${rest%%-*}"

        is_uint "$pid" && [[ -d /proc/$pid ]] && continue
        rm -f -- "$path"
    done

    # TTL applies to image-cache entries, never arbitrary files or active temps.
    find "$CACHE_DIR" -maxdepth 1 -type f \
        \( -name '*.img' -o -name '*.png' \) \
        -mmin "+$CACHE_TTL_MIN" -delete 2>/dev/null

    for dir in "$CACHE_DIR"/session.*; do
        [[ -d $dir && ! -L $dir ]] || continue

        base="${dir##*/}"
        pid="${base#session.}"
        pid="${pid%%.*}"

        is_uint "$pid" && [[ -d /proc/$pid ]] && continue
        rm -rf -- "$dir" 2>/dev/null
    done
    return 0
}

#==============================================================================
# DRAG-RESIZE PERSISTENCE
#==============================================================================
# Absolute reconstruction of the layout percentage from FZF_PREVIEW_COLUMNS
# requires knowing the exact pane chrome (outer border, preview border,
# scrollbar, padding) — v3.0 guessed "+3 / -2" and was wrong whenever any of
# those changed, which is why it needed a 5-point dead-band to stay harmless.
#
# The delta form needs none of that: chrome is a constant that cancels out.
#     pane   = inner + chrome
#     pct    = pane * 100 / span
#     Δpct   = Δinner * 100 / span
# Only `span` (terminal extent minus the outer border) survives, and an error
# there scales the delta instead of the absolute value.
#
# Guards: skipped entirely if the terminal itself was resized between the two
# samples (ambiguous), or if an explicit layout keybind already persisted an
# exact percentage this session (disarm_geometry).
persist_drag_resize() {
    [[ -n $SESSION_DIR ]] || return 0
    [[ -e $SESSION_DIR/geom_disarm ]] && return 0
    local f0="$SESSION_DIR/preview_first" f1="$SESSION_DIR/preview_size"
    [[ -f $f0 && -f $f1 ]] || return 0

    local p0c t0c p0l t0l p1c t1c p1l t1l v
    read -r p0c t0c p0l t0l <"$f0" || return 0
    read -r p1c t1c p1l t1l <"$f1" || return 0
    rm -f -- "$f0" "$f1" 2>/dev/null

    for v in "$p0c" "$t0c" "$p0l" "$t0l" "$p1c" "$t1c" "$p1l" "$t1l"; do
        is_uint "$v" || return 0
    done
    (( t0c == t1c && t0l == t1l )) || return 0     # terminal resized: ambiguous

    state_lock || return 0
    state_load
    local layout="${STATE[PREVIEW_LAYOUT]}" edge old rest delta span new next
    if [[ $layout != hidden && $layout =~ $VISIBLE_LAYOUT_RE ]]; then
        edge="${BASH_REMATCH[1]}"; old="${BASH_REMATCH[2]}"; rest="${BASH_REMATCH[3]}"
        if [[ $edge == left || $edge == right ]]; then
            (( delta = p1c - p0c, span = t1c - 2 ))
        else
            (( delta = p1l - p0l, span = t1l - 2 ))
        fi
        # ±1 cell is rounding noise, not intent.
        if (( span > 0 && (delta >= 2 || delta <= -2) )); then
            (( new = old + (delta * 100 + (delta > 0 ? span / 2 : -(span / 2))) / span ))
            (( new < 10 )) && new=10
            (( new > 90 )) && new=90
            if (( new != old )); then
                next="$edge,$new%$rest"
                [[ $next =~ $LAYOUT_RE ]] && { state_stage PREVIEW_LAYOUT "$next" PREVIEW_LAST "$next"; state_flush; }
            fi
        fi
    fi
    state_unlock
    return 0
}

#==============================================================================
# INTERACTIVE MENU
#==============================================================================
spawn_terminal() {
    local -a cmd
    if   have kitty;     then cmd=(kitty --class=cliphist-fzf --title=Clipboard -o confirm_os_window_close=0 -e)
    elif have foot;      then cmd=(foot --app-id=cliphist-fzf --title=Clipboard --window-size-chars=110x28)
    elif have ghostty;   then cmd=(ghostty --class=cliphist-fzf --title=Clipboard -e)
    elif have wezterm;   then cmd=(wezterm start --class=cliphist-fzf --)
    elif have alacritty; then cmd=(alacritty --class=cliphist-fzf --title=Clipboard \
                                   -o 'window.dimensions.columns=110' -o 'window.dimensions.lines=28' -e)
    else die 'No terminal emulator found' 'Install kitty, foot, ghostty, wezterm or alacritty.'
    fi
    exec "${cmd[@]}" env CLIPBOARD_FZF_EPHEMERAL=1 "$SELF"
}

hex_to_ansi() {
    local hex="${1#\#}" bold="${2:-0}"
    if [[ $hex =~ ^[0-9A-Fa-f]{6}$ ]]; then
        local r g b b_str=''
        (( bold )) && b_str='1;'
        printf -v r '%d' "0x${hex:0:2}"
        printf -v g '%d' "0x${hex:2:2}"
        printf -v b '%d' "0x${hex:4:2}"
        REPLY=$'\e['"${b_str}38;2;${r};${g};${b}m"
    else
        (( bold )) && REPLY=$'\e[1;33m' || REPLY=$'\e[0m'
    fi
}

load_matugen_fzf_theme() {
    local theme_file="$XDG_CONFIG_HOME/matugen/generated/dusky_tui.json"
    local key val

    MATUGEN_BG="#1d100a"
    MATUGEN_FG="#f8ddd2"
    MATUGEN_ACCENT="#ffb694"
    MATUGEN_ERROR="#ffb4ab"
    MATUGEN_WARNING="#efbc94"
    MATUGEN_SUCCESS="#f0be79"
    MATUGEN_MUTED="#55433b"

    if [[ -f $theme_file && -r $theme_file ]]; then
        while IFS="$TAB" read -r key val; do
            case $key in
                bg)      MATUGEN_BG="$val" ;;
                fg)      MATUGEN_FG="$val" ;;
                accent)  MATUGEN_ACCENT="$val" ;;
                error)   MATUGEN_ERROR="$val" ;;
                warning) MATUGEN_WARNING="$val" ;;
                success) MATUGEN_SUCCESS="$val" ;;
                muted)   MATUGEN_MUTED="$val" ;;
            esac
        done < <(
            jq -r '
                if type != "object" then
                    error("clipboard theme must be a JSON object")
                else
                    to_entries[]
                    | select(.key | IN(
                        "bg", "fg", "accent", "error",
                        "warning", "success", "muted"
                    ))
                    | select(.value | type == "string")
                    | select(.value | test("^#[0-9A-Fa-f]{6}$"))
                    | [.key, .value]
                    | @tsv
                end
            ' -- "$theme_file"
        )
    fi

    export DUSKY_FZF_COLORS="bg+:${MATUGEN_MUTED},bg:${MATUGEN_BG},spinner:${MATUGEN_ACCENT},fg:${MATUGEN_FG},fg+:${MATUGEN_FG},header:${MATUGEN_ACCENT},info:${MATUGEN_WARNING},pointer:${MATUGEN_SUCCESS},marker:${MATUGEN_SUCCESS},prompt:${MATUGEN_ACCENT},hl:${MATUGEN_ERROR},hl+:${MATUGEN_ERROR},border:${MATUGEN_MUTED},label:${MATUGEN_ACCENT}"

    unset FZF_DEFAULT_OPTS_FILE
    export FZF_DEFAULT_OPTS="--color=$DUSKY_FZF_COLORS"
}

show_menu() {
    [[ -t 0 && -t 1 ]] || spawn_terminal

    load_matugen_fzf_theme
    state_load

    SESSION_DIR=$(mktemp -d -- "$CACHE_DIR/session.$SCRIPT_PID.XXXXXXXX") ||
        die 'Cannot create session directory' "$CACHE_DIR"
    SESSION_OWNED=1
    export CLIPFZF_SESSION="$SESSION_DIR"

    local mode_label=DISK p_state
    if [[ -f $PERSIST_STATE_FILE && -r $PERSIST_STATE_FILE ]] && read -r p_state <"$PERSIST_STATE_FILE"; then
        [[ $p_state == false ]] && mode_label=RAM
    fi

    local c_key c_desc c_sep c_mode c_rst=$'\e[0m'
    hex_to_ansi "$MATUGEN_ACCENT" 1; c_key="$REPLY"
    hex_to_ansi "$MATUGEN_FG" 0;     c_desc="$REPLY"
    hex_to_ansi "$MATUGEN_MUTED" 0;  c_sep="$REPLY"
    hex_to_ansi "$MATUGEN_WARNING" 1; c_mode="$REPLY"

    local border_main=" ${c_mode}[$mode_label]${c_rst} ${c_key}F1${c_rst} ${c_desc}help${c_rst} ${c_sep}·${c_rst} ${c_key}Alt-M${c_rst} ${c_desc}vim${c_rst} ${c_sep}·${c_rst} ${c_key}Alt-V${c_rst} ${c_desc}view${c_rst} ${c_sep}·${c_rst} ${c_key}Alt-A${c_rst} ${c_desc}pin${c_rst} ${c_sep}·${c_rst} ${c_key}Alt-D${c_rst} ${c_desc}del${c_rst} ${c_sep}·${c_rst} ${c_key}Alt-W${c_rst} ${c_desc}wipe${c_rst} "
    export CLIPFZF_BORDER_MAIN="$border_main"

    # Every command string below is pure ASCII and free of fzf's action-argument
    # metacharacters; the script path travels in $CLIPFZF_SELF instead.
    local -a args=(
        --multi --ansi --no-sort --exact --cycle --layout=reverse --scheme=history
        --with-shell='bash -c'
        --margin=0 --padding=0 --highlight-line --ellipsis=''
        --scrollbar='│┃'
        --border=rounded --border-label="$border_main" --border-label-pos=bottom:3
        --info=hidden
        --pointer='▌' --marker='┃'
        --delimiter="$SEP" --with-nth=1 --nth=1
        --track --id-nth=2,3
        --preview="$SELF_REF --preview {2} {3}"
        --preview-window="${STATE[PREVIEW_LAYOUT]}"
        --preview-label="$LABEL_PREVIEW" --preview-label-pos=3
        --color="label:bold,preview-scrollbar:${MATUGEN_ACCENT}"

        # Mode bootstrap: one transform at `start` decides prompt + search
        # state + keymap, so vim and standard mode share ONE fzf process.
        --bind="start:transform:$SELF_REF --vim-init"
        # Position once, then enable pinned previews.
        # If the user already typed a query, preserve their filtered position:
        # the full-list pin count is not a valid offset into filtered results.
        --bind='load:transform:
            printf "unbind(load)"
            n=0
            if ! IFS= read -r n <"$CLIPFZF_SESSION/initial-pin-count" 2>/dev/null ||
               [[ ! $n =~ ^[0-9]+$ ]]; then
                n=0
            fi

            if [[ -z ${FZF_QUERY:-} ]] &&
               (( n > 0 && n < ${FZF_MATCH_COUNT:-0} )); then
                printf "+pos(%d)" "$((n + 1))"
            fi

            printf "%s" "+execute-silent(: > \"\$CLIPFZF_SESSION/initial-focus-ready\")"

            if (( n > 0 )); then
                printf "+refresh-preview"
            fi'
        --bind="alt-m:transform:$SELF_REF --toggle-vim"
        --bind="f1:transform:$SELF_REF --toggle-help"
        --bind="esc:transform:$SELF_REF --key-escape"
        --bind="alt-w:transform:$SELF_REF --confirm-wipe"

        # The preview process records the geometry itself, so `resize` no longer
        # needs its own execute-silent fork.
        --bind='resize:refresh-preview'

        --bind="alt-h:transform:$SELF_REF --move-preview left"
        --bind="alt-j:transform:$SELF_REF --move-preview down"
        --bind="alt-k:transform:$SELF_REF --move-preview up"
        --bind="alt-l:transform:$SELF_REF --move-preview right"
        --bind="alt-v:transform:$SELF_REF --move-preview hidden"
        --bind="alt-left:transform:$SELF_REF --resize-preview left"
        --bind="alt-right:transform:$SELF_REF --resize-preview right"
        --bind="alt-up:transform:$SELF_REF --resize-preview up"
        --bind="alt-down:transform:$SELF_REF --resize-preview down"

        --bind="alt-t:change-query:!$ICON_IMG !$ICON_PIN !$ICON_BIN "
        --bind="alt-i:change-query:$ICON_IMG "
        --bind="alt-p:change-query:$ICON_PIN "
        --bind="alt-b:change-query:$ICON_BIN "
        --bind='alt-x:clear-query'

        # clear-multi, NOT clear-selection: the latter is not an fzf action and
        # made fzf abort at startup with "unknown action".
        --bind="alt-a:execute-silent($SELF_REF --batch-pin {+f})+reload-sync($SELF_REF --list)+clear-multi"
        --bind="alt-d:execute-silent($SELF_REF --batch-delete {+f})+reload-sync($SELF_REF --list)+clear-multi"
        --bind="ctrl-r:reload-sync($SELF_REF --list)"

        # Vim normal-mode keys are declared unconditionally and unbound at
        # `start` when not in vim mode; `rebind` restores them verbatim. With
        # search disabled fzf ignores every unbound printable key, so nothing
        # can leak into the query buffer and no ignore-list is needed.
        --bind='j:down' --bind='k:up' --bind='g:first' --bind='G:last'
        --bind='J:toggle+down' --bind='K:toggle+up'
        --bind='v:toggle' --bind='V:toggle' --bind='ctrl-a:select-all'
        --bind='ctrl-d:half-page-down' --bind='ctrl-u:half-page-up'
        --bind='q:abort'
        --bind="/:change-prompt($PROMPT_SEARCH)+enable-search+unbind($VIM_KEYS)"
    )

    local output status=0

    # Run the list function in the pipeline's existing subshell, without
    # starting and reparsing another copy of the script.
    # Give that producer its own temp tracking and EXIT cleanup; it must
    # never clean up the parent session or the parent's temporary files.
    output=$(
        (
            _TMPFILES=()

            trap '
                for producer_tmp in "${_TMPFILES[@]}"; do
                    [[ -n $producer_tmp ]] &&
                        rm -f -- "$producer_tmp" 2>/dev/null
                done
                :
            ' EXIT
            trap 'exit 130' INT
            trap 'exit 143' TERM

            cmd_list
        ) | fzf "${args[@]}"

        exit "${PIPESTATUS[1]}"
    ) || status=$?

    persist_drag_resize

    case $status in
        0) ;;
        1|130)
            return 0
            ;;
        *)
            log_err "fzf exited with status $status"
            return "$status"
            ;;
    esac

    [[ -n $output ]] || return 0

    local -a lines=()
    readarray -t lines <<<"$output"
    (( ${#lines[@]} )) || return 0

    cmd_batch_copy "${lines[@]}"
}

#==============================================================================
# ENTRY POINT
#==============================================================================
require_stack() {
    (( BASH_VERSINFO[0] > 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] >= 3) )) ||
        die 'Bash 5.3+ required' "found $BASH_VERSION"
    local v tool
    for tool in \
        fzf cliphist wl-copy gawk perl jq file flock b2sum find sort \
        stat mktemp realpath mkdir rm mv ln cp cat tail
    do
        have "$tool" || die "$tool not found" 'see --doctor'
    done
    v=$(fzf --version); v="${v%% *}"
    version_ge "$v" 0.73.1 || die 'fzf 0.73.1+ required' "found $v"
    [[ -n ${WAYLAND_DISPLAY:-} ]] || log_err 'WAYLAND_DISPLAY unset — wl-clipboard may fail'
    return 0
}

cmd_doctor() {
    local tool v ok
    printf '\e[1m%s %s\e[0m\n' "$SCRIPT_NAME" "$VERSION"
    printf '  bash            : %s\n' "$BASH_VERSION"
    v=$(fzf --version 2>/dev/null) || v='(missing)'
    printf '  fzf             : %s\n' "$v"
    for tool in cliphist wl-copy wl-paste gawk perl jq file flock b2sum \
        sort stat mktemp bat chafa kitten notify-send; do
        if have "$tool"; then ok='✔'; else ok='✘'; fi
        printf '  %-15s : %s %s\n' "$tool" "$ok" "$(command -v -- "$tool" 2>/dev/null)"
    done
    printf '  WAYLAND_DISPLAY : %s\n' "${WAYLAND_DISPLAY:-<unset>}"
    printf '  HYPRLAND_SIG    : %s\n' "${HYPRLAND_INSTANCE_SIGNATURE:-<unset>}"
    printf '  CLIPHIST_DB_PATH: %s\n' "${CLIPHIST_DB_PATH:-<unset>}"
    printf '  cache dir       : %s\n' "$CACHE_DIR"
    printf '  pins dir        : %s\n' "$PINS_DIR"
    printf '  settings        : %s\n' "$USER_STATE_FILE"
    state_load
    for tool in "${!STATE_DEFAULTS[@]}"; do
        printf '  state[%-16s]: %s\n' "$tool" "${STATE[$tool]}"
    done
    printf '  history items   : %s\n' "$(cliphist list 2>/dev/null | wc -l)"
    printf '  pinned items    : %s\n' "$(find "$PINS_DIR" -maxdepth 1 -type f -name '*.pin' 2>/dev/null | wc -l)"
    return 0
}

main() {
    init_backend_env

    case "${1:-}" in
        --list)            setup_dirs && cmd_list ;;
        --preview)         (( $# >= 2 )) || exit 1; cmd_preview "${2:-}" "${3:-}" ;;
        --help-pane)       cmd_help_pane ;;
        --toggle-help)     cmd_toggle_help ;;
        --toggle-vim)      cmd_toggle_vim ;;
        --vim-init)        cmd_vim_init ;;
        --key-escape)      cmd_key_escape ;;
        --confirm-wipe)    cmd_confirm_wipe ;;
        --capture-size)    write_preview_size ;;
        --move-preview)    (( $# >= 2 )) || exit 1; cmd_move_preview "$2" ;;
        --resize-preview)  (( $# >= 2 )) || exit 1; cmd_resize_preview "$2" ;;
        --batch-pin)       setup_dirs && cmd_batch_pin "${2:-}" ;;
        --batch-delete)    setup_dirs && cmd_batch_delete "${2:-}" ;;
        --wipe)            setup_dirs && cmd_wipe ;;
        --prune-cache)     setup_dirs && cmd_prune_cache ;;
        --doctor)          cmd_doctor ;;
        --version)         printf '%s %s\n' "$SCRIPT_NAME" "$VERSION" ;;
        --help|-h)
            printf 'Usage: %s [--list|--prune-cache|--wipe|--doctor|--version]\n' "$SCRIPT_NAME"
            printf 'Run with no arguments to open the clipboard menu.\n' ;;
        '')
            require_stack
            seed_state_file ||
                die 'Cannot initialize clipboard settings' "$USER_STATE_FILE"
            setup_dirs || die 'Failed to create required directories'
            show_menu ;;
        *)
            log_err "Unknown argument: $1"
            exit 1 ;;
    esac
}

main "$@"
