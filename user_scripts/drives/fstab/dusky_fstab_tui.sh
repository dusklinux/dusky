#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Dusky FSTAB Generator TUI - Master v4.4.2
# -----------------------------------------------------------------------------
# Target: Arch Linux / Hyprland / UWSM / Wayland
# Features: Atomic config, FSTAB Analyzer, Dynamic lsblk, Schema Self-Healing
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s extglob

# =============================================================================
# ▼ USER CONFIGURATION ▼
# =============================================================================

declare -r CONFIG_DIR="${HOME}/.config/fstab-tui"
declare -r CONFIG_FILE="${CONFIG_DIR}/profile.conf"

declare -r OUTPUT_FILE="${PWD}/generated_fstab_entry.txt"
declare -r FSTAB_BACKUP="${PWD}/fstab.backup.$(date +%Y%m%d_%H%M%S)"

declare -r APP_TITLE="FSTAB Orchestrator TUI"
declare -r APP_VERSION="v4.4.2 (Architect)"

declare -ri MAX_DISPLAY_ROWS=14
declare -ri BOX_INNER_WIDTH=90
declare -ri ADJUST_THRESHOLD=45
declare -ri ITEM_PADDING=38

declare -ri HEADER_ROWS=4
declare -ri TAB_ROW=3
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

declare -ra TABS=("Mount Info" "Filesystem" "BTRFS Ops" "System Flags")
declare -r UUID_PLACEHOLDER='0000-0000-0000-0000'

bootstrap_config() {
    if [[ ! -d "$CONFIG_DIR" ]]; then
        mkdir -p "$CONFIG_DIR"
    fi
    if [[ ! -f "$CONFIG_FILE" ]]; then
        cat > "$CONFIG_FILE" << 'EOF'
# FSTAB Staging Profile
mount_info {
    uuid = 0000-0000-0000-0000
    mount_point = /
}
filesystem {
    fs_type = btrfs
    drive_type = ssd
}
btrfs_ops {
    subvol = @
    cow_enabled = true
}
system_flags {
    auto_mount = true
}
EOF
    fi
}

normalize_fs_for_profile() {
    local fs="${1,,}"
    case "$fs" in
        btrfs|vfat|ext2|ext3|ext4|swap) REPLY="$fs"; return 0 ;;
        ntfs|ntfs3)                     REPLY="ntfs3"; return 0 ;;
        *)                              REPLY=""; return 1 ;;
    esac
}

refresh_detected_targets() {
    DETECTED_UUIDS=()
    UUID_FS_MAP=()
    local fstype uuid normalized_fs

    while read -r fstype uuid; do
        [[ -n "${uuid:-}" ]] || continue
        if normalize_fs_for_profile "${fstype:-}"; then
            normalized_fs="$REPLY"
            if [[ -z "${UUID_FS_MAP["$uuid"]+_}" ]]; then
                UUID_FS_MAP["$uuid"]="$normalized_fs"
                DETECTED_UUIDS+=("$uuid")
            fi
        fi
    done < <(LC_ALL=C lsblk -pnro FSTYPE,UUID 2>/dev/null)
}

register_items() {
    local uuid_choices="$UUID_PLACEHOLDER"
    if (( ${#DETECTED_UUIDS[@]} > 0 )); then
        local IFS=,
        uuid_choices="${DETECTED_UUIDS[*]}"
    fi

    register 0 "Target ID (UUID/Path)"  "uuid|cycle|mount_info|${uuid_choices}||" "$UUID_PLACEHOLDER"
    register 0 "Target Mount Point"     'mount_point|cycle|mount_info|none,/,/home,/.snapshots,/home/.snapshots,/var/log,/var/cache,/var/tmp,/var/lib/libvirt,/swap,/boot,/boot/efi,/mnt/data||' "/"

    register 1 "Filesystem Type"        'fs_type|cycle|filesystem|btrfs,vfat,ntfs3,ext4,ext3,ext2,swap||' "btrfs"
    register 1 "Drive Architecture"     'drive_type|cycle|filesystem|ssd,hdd||' "ssd"

    register 2 "BTRFS Subvolume"        'subvol|cycle|btrfs_ops|@,@home,@snapshots,@home_snapshots,@var_log,@var_cache,@var_tmp,@var_lib_libvirt,@swap||' "@"
    register 2 "Copy-on-Write (CoW)"    'cow_enabled|bool|btrfs_ops|||' "true"

    register 3 "Mount at Boot (auto)"   'auto_mount|bool|system_flags|||' "true"
}

analyze_fstab() {
    printf '%s%s' "$CURSOR_HOME" "$CLR_SCREEN"
    printf '%s┌────────────────────────────────────────────────────────────────────────────────────────┐%s\n' "$C_MAGENTA" "$C_RESET"
    printf '%s│ %s LIVE /etc/fstab ANALYSIS %s                                                           %s│%s\n' "$C_MAGENTA" "$C_WHITE" "$C_MAGENTA" "$C_MAGENTA" "$C_RESET"
    printf '%s└────────────────────────────────────────────────────────────────────────────────────────┘%s\n\n' "$C_MAGENTA" "$C_RESET"

    if [[ ! -r "/etc/fstab" ]]; then
        printf " %s[!] Cannot read /etc/fstab - Permission denied.%s\n" "$C_RED" "$C_RESET"
    else
        printf " %s%-42s %-18s %-10s %s\n" "$C_CYAN" "TARGET (UUID/DEV)" "MOUNT POINT" "FS TYPE" "BTRFS SUBVOL"
        printf " %s%-42s %-18s %-10s %s%s\n" "$C_GREY" "------------------------------------------" "------------------" "----------" "------------" "$C_RESET"

        while read -r target mp fs flags dump pass; do
            [[ "$target" =~ ^#.*$ || -z "$target" ]] && continue
            local subvol="-"
            if [[ "$fs" == "btrfs" && "$flags" =~ subvol=([^,]+) ]]; then
                subvol="${BASH_REMATCH[1]}"
            fi
            local disp_target="${target:0:40}"
            local disp_mp="${mp:0:17}"
            printf " %-42s %-18s %-10s %s\n" "$disp_target" "$disp_mp" "$fs" "$subvol"
        done < /etc/fstab
    fi
    printf '\n %s[ Press any key to return to the Orchestrator ]%s' "$C_CYAN" "$C_RESET"
    read -rsn1
    printf '%s' "$CLR_SCREEN"
}

invalidate_generated_output() {
    rm -f -- "$OUTPUT_FILE" 2>/dev/null || :
}

post_write_action() {
    local uuid mp fs drive btrfs_subvol btrfs_cow auto_mnt uid gid
    local options="" dump_pass="0 0" auto_flag="noauto,nofail"
    local fstab_line="" detected_fs=""

    uuid="${CONFIG_CACHE["uuid|mount_info"]:-}"
    mp="${CONFIG_CACHE["mount_point|mount_info"]:-}"
    fs="${CONFIG_CACHE["fs_type|filesystem"]:-}"
    drive="${CONFIG_CACHE["drive_type|filesystem"]:-}"
    auto_mnt="${CONFIG_CACHE["auto_mount|system_flags"]:-}"

    if [[ -z "$uuid" || "$uuid" == "$UUID_PLACEHOLDER" ]]; then
        invalidate_generated_output
        set_status "Select a valid target or press [e] to enter manually."
        return 0
    fi

    if [[ -z "$mp" || -z "$fs" || -z "$drive" || -z "$auto_mnt" ]]; then
        invalidate_generated_output
        set_status "Cannot generate fstab: required fields are unset."
        return 0
    fi

    local target_prefix=""
    if [[ ! "$uuid" =~ ^/dev/ ]] && [[ ! "$uuid" =~ ^LABEL= ]] && [[ ! "$uuid" =~ ^PARTUUID= ]]; then
        target_prefix="UUID="
    fi

    mp="${mp// /\\040}"
    [[ "$auto_mnt" == "true" ]] && auto_flag="auto,nofail"

    case "$fs" in
        btrfs)
            btrfs_subvol="${CONFIG_CACHE["subvol|btrfs_ops"]:-}"
            btrfs_cow="${CONFIG_CACHE["cow_enabled|btrfs_ops"]:-}"
            if [[ -z "$btrfs_subvol" || -z "$btrfs_cow" ]]; then
                invalidate_generated_output
                set_status "Cannot generate fstab: BTRFS settings are unset."
                return 0
            fi

            options="defaults,noatime,space_cache=v2"
            [[ "$drive" == "ssd" ]] && options+=",ssd,discard=async" || options+=",autodefrag"
            
            if [[ "$btrfs_cow" == "false" || "$btrfs_subvol" == "@swap" ]]; then
                options+=",nodatacow"
            else
                options+=",compress=zstd:3"
            fi
            options+=",subvol=${btrfs_subvol},${auto_flag},comment=x-gvfs-show"
            ;;
        vfat)
            options="rw,relatime,fmask=0022,dmask=0022,codepage=437,iocharset=ascii,shortname=mixed,utf8,errors=remount-ro,${auto_flag}"
            dump_pass="0 2"
            ;;
        ntfs3)
            if ! uid=$(id -u 2>/dev/null) || ! gid=$(id -g 2>/dev/null); then
                invalidate_generated_output
                set_status "Unable to determine current UID/GID."
                return 0
            fi
            options="defaults,noatime,uid=${uid},gid=${gid},umask=002,windows_names,${auto_flag},comment=x-gvfs-show"
            ;;
        ext2|ext3|ext4)
            options="defaults,noatime,${auto_flag}"
            [[ "$drive" == "ssd" && "$fs" == "ext4" ]] && options+=",discard"
            dump_pass="0 2"
            ;;
        swap)
            mp="none"
            options="defaults"
            dump_pass="0 0"
            ;;
        *)
            invalidate_generated_output
            set_status "Unsupported filesystem type: ${fs}"
            return 0
            ;;
    esac

    printf -v fstab_line '%s%s\t%s\t%s\t%s\t%s\n' \
        "$target_prefix" "$uuid" "$mp" "$fs" "$options" "$dump_pass"

    if ! printf '%s' "$fstab_line" > "$OUTPUT_FILE"; then
        invalidate_generated_output
        set_status "Failed to write generated fstab to ${OUTPUT_FILE}."
        return 0
    fi

    set_status "Local File Updated -> ${OUTPUT_FILE}"
    return 0
}

# =============================================================================
# ▲ END OF USER CONFIGURATION ▲
# =============================================================================

declare _h_line_buf
printf -v _h_line_buf '%*s' "$BOX_INNER_WIDTH" ''
declare -r H_LINE="${_h_line_buf// /─}"
unset _h_line_buf

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
declare -r MOUSE_ON=$'\033[?1000h\033[?1002h\033[?1006h'
declare -r MOUSE_OFF=$'\033[?1000l\033[?1002l\033[?1006l'
declare -r ESC_READ_TIMEOUT=0.10
declare -r UNSET_MARKER='«unset»'

declare -i SELECTED_ROW=0
declare -i CURRENT_TAB=0
declare -i SCROLL_OFFSET=0
declare -ri TAB_COUNT=${#TABS[@]}
declare -a TAB_ZONES=()
declare -i TAB_SCROLL_START=0
declare ORIGINAL_STTY=""

declare -i CURRENT_VIEW=0
declare CURRENT_MENU_ID=""
declare _TMPFILE=""
declare _TMPMODE=""
declare WRITE_TARGET=""

declare -i TERM_ROWS=0
declare -i TERM_COLS=0
declare -ri MIN_TERM_COLS=$(( BOX_INNER_WIDTH + 2 ))
declare -ri MIN_TERM_ROWS=$(( HEADER_ROWS + MAX_DISPLAY_ROWS + 5 ))

declare -gi LAST_WRITE_CHANGED=0
declare STATUS_MESSAGE=""

declare LEFT_ARROW_ZONE=""
declare RIGHT_ARROW_ZONE=""

declare -A ITEM_MAP=()
declare -A VALUE_CACHE=()
declare -A CONFIG_CACHE=()
declare -A DEFAULTS=()
declare -a DETECTED_UUIDS=()
declare -A UUID_FS_MAP=()

for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do declare -ga "TAB_ITEMS_${_ti}=()"; done; unset _ti

log_err() { printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2; }
set_status() { declare -g STATUS_MESSAGE="$1"; }
clear_status() { declare -g STATUS_MESSAGE=""; }

cleanup() {
    printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
    [[ -n "${ORIGINAL_STTY:-}" ]] && stty "$ORIGINAL_STTY" 2>/dev/null || :
    [[ -n "${_TMPFILE:-}" && -f "$_TMPFILE" ]] && rm -f "$_TMPFILE" 2>/dev/null || :
    _TMPFILE=""; _TMPMODE=""; printf '\n' 2>/dev/null || :
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

resolve_write_target() { WRITE_TARGET=$(realpath -m -- "$CONFIG_FILE"); }

create_tmpfile() {
    local target_dir target_base
    [[ -n "${_TMPFILE:-}" && -f "${_TMPFILE:-}" ]] && rm -f -- "$_TMPFILE" 2>/dev/null || :
    _TMPFILE=""; _TMPMODE=""
    target_dir=$(dirname -- "$WRITE_TARGET")
    target_base=$(basename -- "$WRITE_TARGET")
    if ! _TMPFILE=$(mktemp --tmpdir="$target_dir" ".${target_base}.tmp.XXXXXXXXXX" 2>/dev/null); then return 1; fi
    _TMPMODE="atomic"; return 0
}

commit_tmpfile() {
    [[ -n "${_TMPFILE:-}" && -f "$_TMPFILE" && "${_TMPMODE:-}" == "atomic" ]] || return 1
    [[ -e "$WRITE_TARGET" ]] && chmod --reference="$WRITE_TARGET" -- "$_TMPFILE" 2>/dev/null || :
    if ! mv -f -- "$_TMPFILE" "$WRITE_TARGET"; then
        rm -f -- "$_TMPFILE" 2>/dev/null || :
        _TMPFILE=""; _TMPMODE=""; return 1
    fi
    _TMPFILE=""; _TMPMODE=""; return 0
}

update_terminal_size() {
    local size
    if size=$(stty size < /dev/tty 2>/dev/null); then
        TERM_ROWS=${size%% *}; TERM_COLS=${size##* }
    else
        TERM_ROWS=0; TERM_COLS=0
    fi
}

terminal_size_ok() { (( TERM_COLS >= MIN_TERM_COLS && TERM_ROWS >= MIN_TERM_ROWS )); }

draw_small_terminal_notice() {
    printf '%s%s' "$CURSOR_HOME" "$CLR_SCREEN"
    printf '%sTerminal too small%s\n' "$C_RED" "$C_RESET"
    printf '%sNeed at least:%s %d cols × %d rows\n' "$C_YELLOW" "$C_RESET" "$MIN_TERM_COLS" "$MIN_TERM_ROWS"
    printf '%sCurrent size:%s %d cols × %d rows\n' "$C_WHITE" "$C_RESET" "$TERM_COLS" "$TERM_ROWS"
    printf '%sResize the terminal, then continue. Press q to quit.%s%s' "$C_CYAN" "$C_RESET" "$CLR_EOS"
}

strip_ansi() {
    local v="$1"
    v="${v//$'\033'\[*([0-9;:?<=>])@([@A-Z\[\\\]^_\`a-z\{|\}~])/}"
    REPLY="$v"
}

register() {
    local -i tab_idx=$1; local label="$2" config="$3"; local default_val=""; local -i has_default=0
    local key type block min max step
    if (( $# >= 4 )); then default_val=$4; has_default=1; fi
    IFS='|' read -r key type block min max step <<< "$config"
    if (( tab_idx < 0 || tab_idx >= TAB_COUNT )); then log_err "Register Error: Tab index out of range"; exit 1; fi
    case "$type" in bool|int|float|cycle|menu|string) ;; *) log_err "Invalid type"; exit 1 ;; esac
    ITEM_MAP["${tab_idx}::${label}"]="$config"
    (( has_default )) && DEFAULTS["${tab_idx}::${label}"]="$default_val"
    local -n _reg_tab_ref="TAB_ITEMS_${tab_idx}"
    _reg_tab_ref+=("$label")
}

populate_config_cache() {
    CONFIG_CACHE=()
    local key_part value_part
    while IFS='=' read -r key_part value_part || [[ -n "${key_part:-}" ]]; do
        [[ -n "${key_part:-}" ]] || continue
        CONFIG_CACHE["$key_part"]="$value_part"
    done < <(LC_ALL=C awk '
        function trim(s) { sub(/^[[:space:]]+/, "", s); sub(/[[:space:]]+$/, "", s); return s }
        function current_scope(    i, out) {
            out = ""; for (i = 1; i <= depth; i++) { out = out ((i > 1) ? "/" : "") block_stack[i] }; return out
        }
        function push_block(name) { depth++; block_stack[depth] = name }
        function pop_block() { if (depth > 0) { delete block_stack[depth]; depth-- } }
        function consume_leading_structure(s,    token, block_str) {
            while (1) {
                if (match(s, /^[[:space:]]*\}/)) { pop_block(); s = substr(s, RSTART + RLENGTH); continue }
                if (match(s, /^[[:space:]]*[a-zA-Z0-9_.:-]+[[:space:]]*\{/)) {
                    token = substr(s, RSTART, RLENGTH); block_str = token
                    sub(/^[[:space:]]*/, "", block_str); sub(/[[:space:]]*\{$/, "", block_str)
                    push_block(trim(block_str)); s = substr(s, RSTART + RLENGTH); continue
                }
                break
            }
            return s
        }
        function consume_trailing_closes(s) {
            while (match(s, /[[:space:]]*\}[[:space:]]*$/)) { sub(/[[:space:]]*\}[[:space:]]*$/, "", s); pop_block() }
            return s
        }
        BEGIN { depth = 0 }
        {
            clean = $0; sub(/^[[:space:]]*#.*/, "", clean); sub(/[[:space:]]+#.*$/, "", clean); clean = trim(clean)
            if (clean == "") next
            rest = consume_leading_structure(clean); rest = trim(rest)
            if (rest == "") next
            if (rest ~ /=/) {
                eq_pos = index(rest, "=")
                if (eq_pos > 0) {
                    key = trim(substr(rest, 1, eq_pos - 1)); val = trim(substr(rest, eq_pos + 1))
                    scope = current_scope(); val = trim(consume_trailing_closes(val))
                    if (key != "") print key "|" scope "=" val
                }
                next
            }
        }
    ' "$CONFIG_FILE")
}

repair_profile_schema() {
    local -a required_keys=("uuid|mount_info" "mount_point|mount_info" "fs_type|filesystem" "drive_type|filesystem" "subvol|btrfs_ops" "cow_enabled|btrfs_ops" "auto_mount|system_flags")
    local key; local -i needs_repair=0
    for key in "${required_keys[@]}"; do
        if [[ -z "${CONFIG_CACHE["$key"]+_}" ]]; then needs_repair=1; break; fi
    done
    (( needs_repair )) || return 0

    create_tmpfile || { set_status "Atomic save unavailable."; return 1; }
    {
        printf '%s\n' '# FSTAB Staging Profile'
        printf '%s\n' 'mount_info {'
        printf '    uuid = %s\n' "${CONFIG_CACHE["uuid|mount_info"]:-0000-0000-0000-0000}"
        printf '    mount_point = %s\n' "${CONFIG_CACHE["mount_point|mount_info"]:-/}"
        printf '%s\n' '}'
        printf '%s\n' 'filesystem {'
        printf '    fs_type = %s\n' "${CONFIG_CACHE["fs_type|filesystem"]:-btrfs}"
        printf '    drive_type = %s\n' "${CONFIG_CACHE["drive_type|filesystem"]:-ssd}"
        printf '%s\n' '}'
        printf '%s\n' 'btrfs_ops {'
        printf '    subvol = %s\n' "${CONFIG_CACHE["subvol|btrfs_ops"]:-@}"
        printf '    cow_enabled = %s\n' "${CONFIG_CACHE["cow_enabled|btrfs_ops"]:-true}"
        printf '%s\n' '}'
        printf '%s\n' 'system_flags {'
        printf '    auto_mount = %s\n' "${CONFIG_CACHE["auto_mount|system_flags"]:-true}"
        printf '%s\n' '}'
    } > "$_TMPFILE" || {
        rm -f -- "$_TMPFILE" 2>/dev/null || :; _TMPFILE=""; _TMPMODE=""; return 1
    }
    if ! commit_tmpfile; then return 1; fi
    populate_config_cache
    return 0
}

write_value_to_file() {
    local key="$1" new_val="$2" block="${3:-}"
    local cache_key="${key}|${block}"
    local current_val="${CONFIG_CACHE["$cache_key"]:-}"
    LAST_WRITE_CHANGED=0
    if [[ -n "${CONFIG_CACHE["$cache_key"]+_}" && "$current_val" == "$new_val" ]]; then return 0; fi

    create_tmpfile || { set_status "Atomic save unavailable."; return 1; }

    TARGET_SCOPE="$block" TARGET_KEY="$key" NEW_VALUE="$new_val" \
    LC_ALL=C awk '
    function trim(s) { sub(/^[[:space:]]+/, "", s); sub(/[[:space:]]+$/, "", s); return s }
    function current_scope(    i, out) {
        out = ""; for (i = 1; i <= depth; i++) { out = out ((i > 1) ? "/" : "") block_stack[i] }; return out
    }
    function push_block(name) { depth++; block_stack[depth] = name }
    function pop_block() { if (depth > 0) { delete block_stack[depth]; depth-- } }
    function consume_leading_structure(s,    token, block_str) {
        while (1) {
            if (match(s, /^[[:space:]]*\}/)) { pop_block(); s = substr(s, RSTART + RLENGTH); continue }
            if (match(s, /^[[:space:]]*[a-zA-Z0-9_.:-]+[[:space:]]*\{/)) {
                token = substr(s, RSTART, RLENGTH); block_str = token
                sub(/^[[:space:]]*/, "", block_str); sub(/[[:space:]]*\{$/, "", block_str)
                push_block(trim(block_str)); s = substr(s, RSTART + RLENGTH); continue
            }
            break
        }
        return s
    }
    function consume_trailing_closes(s) {
        while (match(s, /[[:space:]]*\}[[:space:]]*$/)) { sub(/[[:space:]]*\}[[:space:]]*$/, "", s); pop_block() }
        return s
    }
    function replace_line(line,    eq, before_eq, rest, space_after, value_and_tail, value_no_comment, comment, trailing_closes) {
        eq = index(line, "="); before_eq = substr(line, 1, eq); rest = substr(line, eq + 1)
        match(rest, /^[[:space:]]*/); space_after = substr(rest, RSTART, RLENGTH); value_and_tail = substr(rest, RLENGTH + 1)
        comment = ""
        if (match(value_and_tail, /[[:space:]]+#.*$/)) {
            comment = substr(value_and_tail, RSTART); value_no_comment = substr(value_and_tail, 1, RSTART - 1)
        } else { value_no_comment = value_and_tail }
        trailing_closes = ""
        if (match(value_no_comment, /([[:space:]]*\})+[[:space:]]*$/)) { trailing_closes = substr(value_no_comment, RSTART) }
        return before_eq space_after ENVIRON["NEW_VALUE"] trailing_closes comment
    }
    BEGIN { depth = 0; target_nr = 0 }
    {
        lines[NR] = $0; clean = $0; sub(/^[[:space:]]*#.*/, "", clean); sub(/[[:space:]]+#.*$/, "", clean); clean = trim(clean)
        if (clean == "") next
        rest = consume_leading_structure(clean); rest = trim(rest)
        if (rest == "") next
        if (rest ~ /=/) {
            eq_pos = index(rest, "=")
            if (eq_pos > 0) {
                k = trim(substr(rest, 1, eq_pos - 1)); v = trim(substr(rest, eq_pos + 1)); assignment_scope = current_scope()
                if (k == ENVIRON["TARGET_KEY"] && assignment_scope == ENVIRON["TARGET_SCOPE"]) { target_nr = NR }
                v = consume_trailing_closes(v)
            }
            next
        }
    }
    END {
        if (target_nr) {
            for (i = 1; i <= NR; i++) { if (i == target_nr) { print replace_line(lines[i]) } else { print lines[i] } }
            exit 0
        }
        exit 1
    }
    ' "$CONFIG_FILE" > "$_TMPFILE" || { rm -f -- "$_TMPFILE" 2>/dev/null || :; _TMPFILE=""; _TMPMODE=""; set_status "Key not found: ${key}"; return 1; }

    if ! commit_tmpfile; then set_status "Failed to save config atomically."; return 1; fi
    CONFIG_CACHE["$cache_key"]="$new_val"; LAST_WRITE_CHANGED=1
    return 0
}

sync_detected_fs_type() {
    local target="$1"
    local detected_fs="${UUID_FS_MAP["$target"]:-}"
    [[ -n "$detected_fs" ]] || return 0
    [[ "${CONFIG_CACHE["fs_type|filesystem"]:-}" == "$detected_fs" ]] && return 0
    write_value_to_file "fs_type" "$detected_fs" "filesystem"
    CONFIG_CACHE["fs_type|filesystem"]="$detected_fs"
}

get_active_context() { REPLY_CTX="${CURRENT_TAB}"; REPLY_REF="TAB_ITEMS_${CURRENT_TAB}"; }

load_active_values() {
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _lav_items_ref="$REPLY_REF"
    local item key type block cache_key value

    for item in "${_lav_items_ref[@]}"; do
        IFS='|' read -r key type block _ _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${item}"]}"
        cache_key="${key}|${block}"
        if [[ -n "${CONFIG_CACHE["$cache_key"]+_}" ]]; then
            value="${CONFIG_CACHE["$cache_key"]}"
            if [[ -z "$value" || ( "$key" == "uuid" && "$value" == "$UUID_PLACEHOLDER" ) ]]; then
                VALUE_CACHE["${REPLY_CTX}::${item}"]="$UNSET_MARKER"
            else
                VALUE_CACHE["${REPLY_CTX}::${item}"]="$value"
            fi
        else
            VALUE_CACHE["${REPLY_CTX}::${item}"]="$UNSET_MARKER"
        fi
    done
}

modify_value() {
    local label="$1"; local -i direction=$2
    local REPLY_REF REPLY_CTX
    get_active_context

    local key type block min max step current new_val; local -i any_written=0
    IFS='|' read -r key type block min max step <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    current="${VALUE_CACHE["${REPLY_CTX}::${label}"]:-}"

    [[ "$current" == "$UNSET_MARKER" || ( "$key" == "uuid" && "$current" == "$UUID_PLACEHOLDER" ) ]] && current=""

    case "$type" in
        bool)
            [[ -z "$current" && -n "${DEFAULTS["${REPLY_CTX}::${label}"]+_}" ]] && current="${DEFAULTS["${REPLY_CTX}::${label}"]}"
            [[ "$current" == "true" ]] && new_val="false" || new_val="true"
            ;;
        cycle)
            [[ -n "$min" ]] || return 0
            local -a opts=(); local -i count=0 idx=-1 i=0
            IFS=',' read -r -a opts <<< "$min"
            count=${#opts[@]}; (( count > 0 )) || return 0

            if [[ -z "$current" ]]; then
                (( direction >= 0 )) && new_val="${opts[0]}" || new_val="${opts[count - 1]}"
            else
                for (( i = 0; i < count; i++ )); do
                    if [[ "${opts[i]}" == "$current" ]]; then idx=$i; break; fi
                done
                if (( idx < 0 )); then
                    (( direction >= 0 )) && new_val="${opts[0]}" || new_val="${opts[count - 1]}"
                else
                    idx=$(( (idx + direction + count) % count )); new_val="${opts[idx]}"
                fi
            fi
            ;;
        *) return 0 ;;
    esac

    if write_value_to_file "$key" "$new_val" "$block"; then
        any_written=$LAST_WRITE_CHANGED
        VALUE_CACHE["${REPLY_CTX}::${label}"]="$new_val"
        if [[ "$key" == "uuid" && "$block" == "mount_info" ]]; then
            sync_detected_fs_type "$new_val" || :
            (( LAST_WRITE_CHANGED )) && any_written=1
            load_active_values 
        fi
        clear_status
        (( any_written )) && post_write_action
    fi
}

edit_value() {
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _edit_items_ref="$REPLY_REF"
    if (( ${#_edit_items_ref[@]} == 0 )); then return 0; fi

    local label="${_edit_items_ref[SELECTED_ROW]}"
    local config="${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    local key type block; local -i any_written=0
    IFS='|' read -r key type block _ <<< "$config"

    if [[ "$type" == "bool" ]]; then set_status "Cannot manually edit bool fields."; return 0; fi

    local current="${VALUE_CACHE["${REPLY_CTX}::${label}"]:-}"
    [[ "$current" == "$UNSET_MARKER" ]] && current=""

    printf '%s%s' "$CURSOR_SHOW" "$MOUSE_OFF"
    local -i prompt_row=$(( TERM_ROWS ))
    printf '\033[%d;1H%s' "$prompt_row" "$CLR_EOL"
    printf '%s[ EDIT ]%s %s : ' "$C_MAGENTA" "$C_RESET" "$label"

    [[ -n "${ORIGINAL_STTY:-}" ]] && stty "$ORIGINAL_STTY" 2>/dev/null || stty sane

    local new_val
    if read -r -e -i "$current" new_val < /dev/tty; then
        if write_value_to_file "$key" "$new_val" "$block"; then
            any_written=$LAST_WRITE_CHANGED
            if [[ -z "$new_val" || ( "$key" == "uuid" && "$new_val" == "$UUID_PLACEHOLDER" ) ]]; then
                VALUE_CACHE["${REPLY_CTX}::${label}"]="$UNSET_MARKER"
            else
                VALUE_CACHE["${REPLY_CTX}::${label}"]="$new_val"
            fi

            if [[ "$key" == "uuid" && "$block" == "mount_info" ]]; then
                sync_detected_fs_type "$new_val" || :
                (( LAST_WRITE_CHANGED )) && any_written=1
                load_active_values
            fi

            clear_status
            (( any_written )) && post_write_action
        fi
    fi

    stty -icanon -echo min 1 time 0 2>/dev/null
    printf '%s%s' "$CURSOR_HIDE" "$MOUSE_ON"
    printf '%s' "$CLR_SCREEN"
}

set_absolute_value() {
    local label="$1" new_val="$2"
    local REPLY_REF REPLY_CTX
    get_active_context
    local key type block
    IFS='|' read -r key type block _ _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    if write_value_to_file "$key" "$new_val" "$block"; then
        if [[ -z "$new_val" || ( "$key" == "uuid" && "$new_val" == "$UUID_PLACEHOLDER" ) ]]; then
            VALUE_CACHE["${REPLY_CTX}::${label}"]="$UNSET_MARKER"
        else
            VALUE_CACHE["${REPLY_CTX}::${label}"]="$new_val"
        fi
        return 0
    fi
    return 1
}

reset_defaults() {
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _rd_items_ref="$REPLY_REF"
    local item def_val; local -i any_written=0 any_failed=0

    for item in "${_rd_items_ref[@]}"; do
        if [[ -n "${DEFAULTS["${REPLY_CTX}::${item}"]+_}" ]]; then
            def_val="${DEFAULTS["${REPLY_CTX}::${item}"]}"
            if set_absolute_value "$item" "$def_val"; then
                (( LAST_WRITE_CHANGED )) && any_written=1
            else
                any_failed=1
            fi
        fi
    done
    (( any_written )) && post_write_action
    if (( any_failed )); then set_status "Some defaults were not written."; else clear_status; fi
    return 0
}

compute_scroll_window() {
    local -i count=$1
    if (( count == 0 )); then SELECTED_ROW=0; SCROLL_OFFSET=0; _vis_start=0; _vis_end=0; return; fi
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
    local -n _rsi_buf=$1; local position="$2"; local -i count=$3 boundary=$4
    if [[ "$position" == "above" ]]; then
        if (( SCROLL_OFFSET > 0 )); then _rsi_buf+="${C_GREY}    ▲ (more above)${CLR_EOL}${C_RESET}"$'\n'
        else _rsi_buf+="${CLR_EOL}"$'\n'; fi
    else
        if (( count > MAX_DISPLAY_ROWS )); then
            local position_info="[$(( SELECTED_ROW + 1 ))/${count}]"
            if (( boundary < count )); then _rsi_buf+="${C_GREY}    ▼ (more below) ${position_info}${CLR_EOL}${C_RESET}"$'\n'
            else _rsi_buf+="${C_GREY}                   ${position_info}${CLR_EOL}${C_RESET}"$'\n'; fi
        else _rsi_buf+="${CLR_EOL}"$'\n'; fi
    fi
}

render_item_list() {
    local -n _ril_buf=$1; local -n _ril_items=$2; local _ril_ctx="$3"; local -i _ril_vs=$4 _ril_ve=$5
    local -i ri; local item val display type config padded_item
    for (( ri = _ril_vs; ri < _ril_ve; ri++ )); do
        item="${_ril_items[ri]}"
        val="${VALUE_CACHE["${_ril_ctx}::${item}"]:-${UNSET_MARKER}}"
        config="${ITEM_MAP["${_ril_ctx}::${item}"]}"
        IFS='|' read -r _ type _ _ _ _ <<< "$config"

        case "$val" in
            true)            display="${C_GREEN}ON${C_RESET}" ;;
            false)           display="${C_RED}OFF${C_RESET}" ;;
            "$UNSET_MARKER") display="${C_YELLOW}⚠ UNSET${C_RESET}" ;;
            *)               display="${C_WHITE}${val}${C_RESET}" ;;
        esac

        local -i max_len=$(( ITEM_PADDING - 1 ))
        if (( ${#item} > ITEM_PADDING )); then printf -v padded_item "%-${max_len}s…" "${item:0:max_len}"
        else printf -v padded_item "%-${ITEM_PADDING}s" "$item"; fi

        if (( ri == SELECTED_ROW )); then _ril_buf+="${C_CYAN} ➤ ${C_INVERSE}${padded_item}${C_RESET} : ${display}${CLR_EOL}"$'\n'
        else _ril_buf+="    ${padded_item} : ${display}${CLR_EOL}"$'\n'; fi
    done
    local -i rows_rendered=$(( _ril_ve - _ril_vs ))
    for (( ri = rows_rendered; ri < MAX_DISPLAY_ROWS; ri++ )); do _ril_buf+="${CLR_EOL}"$'\n'; done
}

draw_main_view() {
    local buf="" pad_buf=""; local -i i current_col=3 zone_start count left_pad right_pad vis_len _vis_start _vis_end
    buf+="${CURSOR_HOME}${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'
    strip_ansi "$APP_TITLE"; local -i t_len=${#REPLY}
    strip_ansi "$APP_VERSION"; local -i v_len=${#REPLY}
    vis_len=$(( t_len + v_len + 1 )); left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 )); right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad ))
    printf -v pad_buf '%*s' "$left_pad" ''; buf+="${C_MAGENTA}│${pad_buf}${C_WHITE}${APP_TITLE} ${C_CYAN}${APP_VERSION}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''; buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'

    if (( TAB_SCROLL_START > CURRENT_TAB )); then TAB_SCROLL_START=$CURRENT_TAB; fi
    if (( TAB_SCROLL_START < 0 )); then TAB_SCROLL_START=0; fi
    local tab_line; local -i max_tab_width=$(( BOX_INNER_WIDTH - 6 ))
    LEFT_ARROW_ZONE=""; RIGHT_ARROW_ZONE=""

    while true; do
        tab_line="${C_MAGENTA}│ "; current_col=3; TAB_ZONES=(); local -i used_len=0
        if (( TAB_SCROLL_START > 0 )); then
            tab_line+="${C_YELLOW}«${C_RESET} "; LEFT_ARROW_ZONE="$current_col:$(( current_col + 1 ))"
            used_len=$(( used_len + 2 )); current_col=$(( current_col + 2 ))
        else tab_line+="  "; used_len=$(( used_len + 2 )); current_col=$(( current_col + 2 )); fi

        for (( i = TAB_SCROLL_START; i < TAB_COUNT; i++ )); do
            local name="${TABS[i]}"; local display_name="$name"; local -i tab_name_len=${#name}; local -i chunk_len=$(( tab_name_len + 4 )); local -i reserve=0
            if (( i < TAB_COUNT - 1 )); then reserve=2; fi

            if (( used_len + chunk_len + reserve > max_tab_width )); then
                if (( i < CURRENT_TAB || (i == CURRENT_TAB && TAB_SCROLL_START < CURRENT_TAB) )); then
                    TAB_SCROLL_START=$(( TAB_SCROLL_START + 1 )); continue 2
                fi
                if (( i == CURRENT_TAB )); then
                    local -i avail_label=$(( max_tab_width - used_len - reserve - 4 ))
                    if (( avail_label < 1 )); then avail_label=1; fi
                    if (( tab_name_len > avail_label )); then
                        if (( avail_label == 1 )); then display_name="…"
                        else display_name="${name:0:avail_label-1}…"; fi
                        tab_name_len=${#display_name}; chunk_len=$(( tab_name_len + 4 ))
                    fi
                    zone_start=$current_col
                    tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}${C_MAGENTA}│ "
                    TAB_ZONES+=("${zone_start}:$(( zone_start + tab_name_len + 1 ))")
                    used_len=$(( used_len + chunk_len )); current_col=$(( current_col + chunk_len ))
                    if (( i < TAB_COUNT - 1 )); then
                        tab_line+="${C_YELLOW}» ${C_RESET}"; RIGHT_ARROW_ZONE="$current_col:$(( current_col + 1 ))"; used_len=$(( used_len + 2 ))
                    fi
                    break
                fi
                tab_line+="${C_YELLOW}» ${C_RESET}"; RIGHT_ARROW_ZONE="$current_col:$(( current_col + 1 ))"; used_len=$(( used_len + 2 ))
                break
            fi

            zone_start=$current_col
            if (( i == CURRENT_TAB )); then tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}${C_MAGENTA}│ "
            else tab_line+="${C_GREY} ${display_name} ${C_MAGENTA}│ "; fi
            TAB_ZONES+=("${zone_start}:$(( zone_start + tab_name_len + 1 ))")
            used_len=$(( used_len + chunk_len )); current_col=$(( current_col + chunk_len ))
        done
        local -i pad=$(( BOX_INNER_WIDTH - used_len - 1 ))
        if (( pad > 0 )); then printf -v pad_buf '%*s' "$pad" ''; tab_line+="$pad_buf"; fi
        tab_line+="${C_MAGENTA}│${C_RESET}"; break
    done

    buf+="${tab_line}${CLR_EOL}"$'\n'"${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'
    local items_var="TAB_ITEMS_${CURRENT_TAB}"; local -n _draw_items_ref="$items_var"; count=${#_draw_items_ref[@]}

    compute_scroll_window "$count"
    render_scroll_indicator buf "above" "$count" "$_vis_start"
    render_item_list buf _draw_items_ref "${CURRENT_TAB}" "$_vis_start" "$_vis_end"
    render_scroll_indicator buf "below" "$count" "$_vis_end"

    buf+=$'\n'"${C_CYAN} [Tab] Category  [r] Reset  [e] Edit Text  [a] Analyze fstab  [←/→] Cycle  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    if [[ -n "$STATUS_MESSAGE" ]]; then buf+="${C_CYAN} Status: ${C_RED}${STATUS_MESSAGE}${C_RESET}${CLR_EOL}${CLR_EOS}"
    else buf+="${C_CYAN} Output: ${C_WHITE}${OUTPUT_FILE}${C_RESET}${CLR_EOL}${CLR_EOS}"; fi
    printf '%s' "$buf"
}

draw_ui() { update_terminal_size; if ! terminal_size_ok; then draw_small_terminal_notice; return; fi; draw_main_view; }

navigate() {
    local -i dir=$1; local REPLY_REF REPLY_CTX; get_active_context; local -n _nav_items_ref="$REPLY_REF"
    local -i count=${#_nav_items_ref[@]}; if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( (SELECTED_ROW + dir + count) % count ))
}
navigate_page() {
    local -i dir=$1; local REPLY_REF REPLY_CTX; get_active_context; local -n _navp_items_ref="$REPLY_REF"
    local -i count=${#_navp_items_ref[@]}; if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( SELECTED_ROW + dir * MAX_DISPLAY_ROWS ))
    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= count )); then SELECTED_ROW=$(( count - 1 )); fi
}
navigate_end() {
    local -i target=$1; local REPLY_REF REPLY_CTX; get_active_context; local -n _nave_items_ref="$REPLY_REF"
    local -i count=${#_nave_items_ref[@]}; if (( count == 0 )); then return 0; fi
    if (( target == 0 )); then SELECTED_ROW=0; else SELECTED_ROW=$(( count - 1 )); fi
}
adjust() {
    local -i dir=$1; local REPLY_REF REPLY_CTX; get_active_context; local -n _adj_items_ref="$REPLY_REF"
    if (( ${#_adj_items_ref[@]} == 0 )); then return 0; fi; modify_value "${_adj_items_ref[SELECTED_ROW]}" "$dir"
}
switch_tab() {
    local -i dir=${1:-1}; CURRENT_TAB=$(( (CURRENT_TAB + dir + TAB_COUNT) % TAB_COUNT ))
    SELECTED_ROW=0; SCROLL_OFFSET=0; load_active_values
}
set_tab() {
    local -i idx=$1
    if (( idx != CURRENT_TAB && idx >= 0 && idx < TAB_COUNT )); then
        CURRENT_TAB=$idx; SELECTED_ROW=0; SCROLL_OFFSET=0; load_active_values
    fi
}

handle_mouse() {
    local input="$1"; local -i button x y i start end; local zone; local body="${input#'[<'}"
    if [[ "$body" == "$input" ]]; then return 0; fi
    local terminator="${body: -1}"; if [[ "$terminator" != "M" && "$terminator" != "m" ]]; then return 0; fi
    body="${body%[Mm]}"; local field1 field2 field3; IFS=';' read -r field1 field2 field3 <<< "$body"
    if [[ ! "$field1" =~ ^[0-9]+$ || ! "$field2" =~ ^[0-9]+$ || ! "$field3" =~ ^[0-9]+$ ]]; then return 0; fi
    button=$field1; x=$field2; y=$field3

    if (( button == 64 )); then navigate -1; return 0; fi
    if (( button == 65 )); then navigate 1; return 0; fi
    if [[ "$terminator" != "M" ]]; then return 0; fi

    if (( y == TAB_ROW && CURRENT_VIEW == 0 )); then
        if [[ -n "$LEFT_ARROW_ZONE" ]]; then
            start="${LEFT_ARROW_ZONE%%:*}"; end="${LEFT_ARROW_ZONE##*:}"
            if (( x >= start && x <= end )); then switch_tab -1; return 0; fi
        fi
        if [[ -n "$RIGHT_ARROW_ZONE" ]]; then
            start="${RIGHT_ARROW_ZONE%%:*}"; end="${RIGHT_ARROW_ZONE##*:}"
            if (( x >= start && x <= end )); then switch_tab 1; return 0; fi
        fi
        for (( i = 0; i < TAB_COUNT; i++ )); do
            if [[ -z "${TAB_ZONES[i]:-}" ]]; then continue; fi
            zone="${TAB_ZONES[i]}"; start="${zone%%:*}"; end="${zone##*:}"
            if (( x >= start && x <= end )); then set_tab "$(( i + TAB_SCROLL_START ))"; return 0; fi
        done
    fi

    local -i effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start + SCROLL_OFFSET ))
        local _target_var_name="TAB_ITEMS_${CURRENT_TAB}"; local -n _mouse_items_ref="$_target_var_name"
        local -i count=${#_mouse_items_ref[@]}
        if (( clicked_idx >= 0 && clicked_idx < count )); then
            SELECTED_ROW=$clicked_idx
            if (( x > ADJUST_THRESHOLD )); then
                if (( button == 0 )); then adjust 1; else adjust -1; fi
            fi
        fi
    fi
    return 0
}

read_escape_seq() {
    local -n _esc_out=$1; _esc_out=""; local char
    if ! IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; then return 1; fi
    _esc_out+="$char"
    if [[ "$char" == '[' || "$char" == 'O' ]]; then
        while IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; do
            _esc_out+="$char"; if [[ "$char" =~ [a-zA-Z~] ]]; then break; fi
        done
    fi
    return 0
}

handle_key_main() {
    local key="$1"
    case "$key" in
        '[Z')                switch_tab -1; return ;;
        '[A'|'OA')           navigate -1; return ;;
        '[B'|'OB')           navigate 1; return ;;
        '[C'|'OC')           adjust 1; return ;;
        '[D'|'OD')           adjust -1; return ;;
        '[5~')               navigate_page -1; return ;;
        '[6~')               navigate_page 1; return ;;
        '[H'|'[1~')          navigate_end 0; return ;;
        '[F'|'[4~')          navigate_end 1; return ;;
        '['*'<'*[Mm])        handle_mouse "$key"; return ;;
    esac

    case "$key" in
        k|K)               navigate -1 ;;
        j|J)               navigate 1 ;;
        l|L)               adjust 1 ;;
        h|H)               adjust -1 ;;
        g)                 navigate_end 0 ;;
        G)                 navigate_end 1 ;;
        e|E)               edit_value ;;
        a|A)               analyze_fstab ;;
        $'\t')             switch_tab 1 ;;
        r|R)               reset_defaults ;;
        ''|$'\n')          adjust 1 ;;
        $'\x7f'|$'\x08'|$'\e\n') adjust -1 ;;
        q|Q|$'\x03')       exit 0 ;;
    esac
}

handle_input_router() {
    local key="$1"; local escape_seq=""
    if [[ "$key" == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key="$escape_seq"; if [[ "$key" == "" || "$key" == $'\n' ]]; then key=$'\e\n'; fi
        else key="ESC"; fi
    fi
    if ! terminal_size_ok; then case "$key" in q|Q|$'\x03') exit 0 ;; esac; return 0; fi
    handle_key_main "$key"
}

main() {
    if (( BASH_VERSINFO[0] < 5 )); then log_err "Bash 5.0+ required"; exit 1; fi
    if [[ ! -t 0 || ! -t 1 ]]; then log_err "Interactive TTY required on stdin/stdout"; exit 1; fi

    local _dep; for _dep in awk realpath lsblk; do
        if ! command -v "$_dep" &>/dev/null; then log_err "Missing dependency: ${_dep}"; exit 1; fi
    done

    resolve_write_target
    if [[ ! -w "$WRITE_TARGET" ]]; then log_err "Config not writable: $CONFIG_FILE"; exit 1; fi

    if [[ ! -f "$FSTAB_BACKUP" ]]; then
        cp /etc/fstab "$FSTAB_BACKUP" 2>/dev/null || log_err "Could not backup /etc/fstab"
    fi

    refresh_detected_targets
    register_items
    populate_config_cache
    repair_profile_schema

    if [[ -n "${CONFIG_CACHE["uuid|mount_info"]:-}" && "${CONFIG_CACHE["uuid|mount_info"]:-}" != "$UUID_PLACEHOLDER" ]]; then
        sync_detected_fs_type "${CONFIG_CACHE["uuid|mount_info"]}" || :
    fi

    post_write_action

    ORIGINAL_STTY=$(stty -g 2>/dev/null) || ORIGINAL_STTY=""
    stty -icanon -echo min 1 time 0 2>/dev/null

    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"
    load_active_values
    trap 'draw_ui' WINCH

    local key
    while true; do
        draw_ui
        if ! IFS= read -rsn1 key; then continue; fi
        handle_input_router "$key"
    done
}

main "$@"
