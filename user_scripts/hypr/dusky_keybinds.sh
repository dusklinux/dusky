#!/usr/bin/env bash
# ==============================================================================
# Description: Advanced TUI for Hyprland Keybinds
#              - Unified View: Source and Custom binds together.
#              - Smart Grouping: Overrides appear next to originals.
#              - Debloating: Replaces old edits instead of appending.
#              - Interactive Auto-Correction with smart heuristics.
#              - Unbind Deduplication via associative array.
#              - Conflict resolution with proper line removal.
#              - Delete / Unbind workflow.
#              - File locking to prevent concurrent corruption.
#              - Atomic writes via rename(2) with symlink resolution.
#              - Submap-aware display and override detection.
#              - Runtime dispatcher validation via hyprctl.
#              - --view, --dry-run, --help modes.
#              - Structured origin detection (no string-matching ambiguity).
#
# Note:        Unbind-only entries in CUSTOM_CONF are not shown in the UI.
#              Creating a new bind on the same key will replace them.
#
# Assumption:  $mainMod is assumed to resolve to SUPER for comparison
#              purposes. If your $mainMod is different, override detection
#              may not work correctly for binds using $mainMod.
#
# Version:     v28.0
# Engine Sync: Hardened with patterns from Dusky TUI Engine v3.9.1
# ==============================================================================

set -euo pipefail

if (( BASH_VERSINFO[0] < 5 )); then
    printf 'FATAL: This script requires Bash 5.0 or newer.\n' >&2
    exit 1
fi

# --- ANSI Colors ---
readonly BLUE=$'\033[0;34m'
readonly GREEN=$'\033[0;32m'
readonly YELLOW=$'\033[0;33m'
readonly RED=$'\033[0;31m'
readonly CYAN=$'\033[0;36m'
readonly PURPLE=$'\033[0;35m'
readonly GREY=$'\033[0;90m'
readonly BOLD=$'\033[1m'
readonly RESET=$'\033[0m'
readonly BRIGHT_WHITE=$'\033[0;97m'
readonly DIM=$'\033[2m'

# --- Paths ---
readonly SOURCE_CONF="${HOME}/.config/hypr/source/keybinds.conf"
readonly CUSTOM_CONF="${HOME}/.config/hypr/edit_here/source/keybinds.conf"
readonly LOCK_FILE="${CUSTOM_CONF}.lock"

# --- Sentinels ---
readonly CREATE_MARKER_ID="__CREATE_NEW_BIND__"
readonly SOURCE_INFO_MARKER="__SOURCE_DIRECTIVE_INFO__"
readonly EMPTY_BIND_TEMPLATE="bindd = "

# --- Dispatchers (populated at runtime if possible, otherwise fallback) ---
declare -a KNOWN_DISPATCHERS=(
    exec killactive closewindow workspace movetoworkspace
    movetoworkspacesilent togglefloating fullscreen fakefullscreen
    pin togglesplit togglegroup changegroupactive moveintogroup
    moveoutofgroup movewindoworgroup lockgroups lockactivegroup
    movegroupwindow movewindow moveactive resizeactive
    resizewindowpixel centerwindow focuswindow cyclenext
    focuscurrentorlast swapnext focusurgentorlast
    focusworkspaceoncurrentmonitor submap pass sendshortcut
    movetosilent global dpms exit forcerendererreload
    movecursortocorner movecursor renameworkspace animationstyle
    swapwindow bringactivetotop alterzorder togglespecialworkspace
    focusmonitor movecurrentworkspacetomonitor moveworkspacetomonitor
    splitratio layoutmsg toggleopaque setfloating seterror setprop
    plugin event
)

# --- Globals ---
declare -a TEMP_FILES=()
declare LOCK_FD=""
declare -i IN_ALT_SCREEN=0
declare -i DRY_RUN=0
declare -i VIEW_ONLY=0

# ==============================================================================
# Cleanup & Signals
# ==============================================================================

cleanup_temp_files() {
    local f
    for f in "${TEMP_FILES[@]}"; do
        rm -f -- "$f" 2>/dev/null || :
    done
    TEMP_FILES=()
}

cleanup() {
    if (( IN_ALT_SCREEN )); then
        tput rmcup 2>/dev/null || :
    fi
    cleanup_temp_files
    if [[ -n "$LOCK_FD" ]]; then
        eval "exec ${LOCK_FD}>&-" 2>/dev/null || :
        rm -f -- "$LOCK_FILE" 2>/dev/null || :
    fi
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# ==============================================================================
# Utility Functions
# ==============================================================================

enter_alt_screen() {
    tput smcup 2>/dev/null || :
    IN_ALT_SCREEN=1
}

leave_alt_screen() {
    if (( IN_ALT_SCREEN )); then
        tput rmcup 2>/dev/null || :
        IN_ALT_SCREEN=0
    fi
}

make_temp() {
    local -n _ref="$1"
    local template="${2:-${TMPDIR:-/tmp}/hyprbinds.XXXXXX}"
    _ref="$(mktemp "$template")" || exit 1
    TEMP_FILES+=("$_ref")
}

die() {
    printf '%s[FATAL]%s %s\n' "$RED" "$RESET" "$1" >&2
    exit 1
}

acquire_lock() {
    local lock_dir="${LOCK_FILE%/*}"
    mkdir -p "$lock_dir"
    exec {LOCK_FD}>"${LOCK_FILE}"
    if ! flock -n "$LOCK_FD"; then
        die "Another instance is already running (lock: ${LOCK_FILE})."
    fi
}

_trim() {
    local -n _out="$1"
    _out="${2#"${2%%[![:space:]]*}"}"
    _out="${_out%"${_out##*[![:space:]]}"}"
}

_normalize_bind_parts() {
    local -n _mods="$1"
    local -n _key="$2"
    _mods="${_mods,,}"
    _mods="${_mods//\$mainmod/super}"
    _key="${_key,,}"
}

_is_comment_or_blank() {
    [[ -z "$1" || "$1" =~ ^[[:space:]]*# ]]
}

_is_bind_directive() {
    [[ "$1" =~ ^[[:space:]]*(bind[a-z]*|unbind)[[:space:]]*= ]]
}

_is_unbind() {
    [[ "$1" =~ ^[[:space:]]*unbind[[:space:]]*= ]]
}

_is_submap_directive() {
    [[ "$1" =~ ^[[:space:]]*submap[[:space:]]*= ]]
}

_extract_submap_value() {
    local line="$1"
    local val="${line#*=}"
    _trim val "$val"
    if [[ "${val,,}" == "reset" ]]; then
        printf ''
    else
        printf '%s' "$val"
    fi
}

# Extract MODS and KEY from content after '='.
# Only splits on the first two comma-delimited fields so commas
# inside dispatcher arguments never cause misparsing.
_extract_mods_key() {
    local -n _emk_mods="$1"
    local -n _emk_key="$2"
    local content="$3"

    _emk_mods="${content%%,*}"
    local remainder="${content#*,}"

    if [[ "$remainder" == "$content" ]]; then
        _emk_key=""
    else
        _emk_key="${remainder%%,*}"
    fi

    _trim _emk_mods "$_emk_mods"
    _trim _emk_key "$_emk_key"
}

# Extract the Nth comma-delimited field (0-indexed) from content.
_extract_field() {
    local content="$1"
    local target_idx="$2"
    local tmp="$content"
    local field=""
    local i=0

    while (( i <= target_idx )) && [[ -n "$tmp" ]]; do
        if [[ "$tmp" == *,* ]]; then
            field="${tmp%%,*}"
            tmp="${tmp#*,}"
        else
            field="$tmp"
            tmp=""
        fi
        if (( i == target_idx )); then
            _trim field "$field"
            printf '%s' "$field"
            return
        fi
        (( i++ )) || :
    done
}

# Extract dispatcher name, accounting for 'd' flag shifting field positions.
_extract_dispatcher() {
    local bind_type="$1"
    local content="$2"
    local flags="${bind_type#bind}"

    local dispatcher_idx=2
    if [[ "$flags" == *d* ]]; then
        dispatcher_idx=3
    fi

    _extract_field "$content" "$dispatcher_idx"
}

_validate_dispatcher() {
    local dispatcher="$1"
    [[ -z "$dispatcher" ]] && return 0

    local norm="${dispatcher,,}"
    local d
    for d in "${KNOWN_DISPATCHERS[@]}"; do
        if [[ "$d" == "$norm" ]]; then
            return 0
        fi
    done
    return 1
}

# Build the submap|MODS|KEY override key.
_make_override_key() {
    local submap="$1" mods="$2" key="$3"
    printf '%s|%s|%s' "$submap" "$mods" "$key"
}

_exists_in_source() {
    local check_mods="$1" check_key="$2" check_submap="${3:-}"
    _normalize_bind_parts check_mods check_key

    [[ ! -f "$SOURCE_CONF" ]] && return 1

    local line content l_mods l_key current_submap=""
    while IFS= read -r line || [[ -n "$line" ]]; do
        if _is_submap_directive "$line"; then
            current_submap="$(_extract_submap_value "$line")"
            continue
        fi

        _is_comment_or_blank "$line" && continue
        _is_bind_directive "$line" || continue
        _is_unbind "$line" && continue

        content="${line#*=}"
        _extract_mods_key l_mods l_key "$content"
        _normalize_bind_parts l_mods l_key

        if [[ "$l_mods" == "$check_mods" && "$l_key" == "$check_key" && "$current_submap" == "$check_submap" ]]; then
            return 0
        fi
    done < "$SOURCE_CONF"
    return 1
}

# Check whether CUSTOM_CONF already contains an unbind for a given key+submap.
_is_already_unbound_in_custom() {
    local check_mods="$1" check_key="$2" check_submap="${3:-}"
    _normalize_bind_parts check_mods check_key

    [[ ! -f "$CUSTOM_CONF" ]] && return 1

    local line content l_mods l_key current_submap=""
    while IFS= read -r line || [[ -n "$line" ]]; do
        if _is_submap_directive "$line"; then
            current_submap="$(_extract_submap_value "$line")"
            continue
        fi
        _is_unbind "$line" || continue
        content="${line#*=}"
        _extract_mods_key l_mods l_key "$content"
        _normalize_bind_parts l_mods l_key
        if [[ "$l_mods" == "$check_mods" && "$l_key" == "$check_key" && "$current_submap" == "$check_submap" ]]; then
            return 0
        fi
    done < "$CUSTOM_CONF"
    return 1
}

# Build associative array of keys overridden in CUSTOM_CONF (bind or unbind).
# Keys are submap|MODS|KEY (all normalized).
_build_override_set() {
    local -n _oset="$1"
    [[ ! -f "$CUSTOM_CONF" ]] && return

    local line content l_mods l_key current_submap=""
    while IFS= read -r line || [[ -n "$line" ]]; do
        if _is_submap_directive "$line"; then
            current_submap="$(_extract_submap_value "$line")"
            continue
        fi

        _is_comment_or_blank "$line" && continue
        _is_bind_directive "$line" || continue

        content="${line#*=}"
        _extract_mods_key l_mods l_key "$content"
        _normalize_bind_parts l_mods l_key
        local okey
        okey="$(_make_override_key "$current_submap" "$l_mods" "$l_key")"
        _oset["$okey"]=1
    done < "$CUSTOM_CONF"
}

# Populate KNOWN_DISPATCHERS from running Hyprland if available.
build_dispatcher_list() {
    [[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]] || return 0
    command -v hyprctl &>/dev/null || return 0

    local output
    output="$(hyprctl dispatchers 2>/dev/null)" || return 0

    local -a runtime_list=()
    local dline
    while IFS= read -r dline; do
        _trim dline "$dline"
        if [[ "$dline" =~ ^[a-z][a-z0-9_]*$ ]]; then
            runtime_list+=("$dline")
        fi
    done <<< "$output"

    if (( ${#runtime_list[@]} > 0 )); then
        KNOWN_DISPATCHERS=("${runtime_list[@]}")
    fi
}

# Count comma-separated fields in a string.
_count_fields() {
    local str="$1"
    local count=1
    local tmp="$str"
    while [[ "$tmp" == *,* ]]; do
        tmp="${tmp#*,}"
        (( count++ ))
    done
    printf '%d' "$count"
}

# ==============================================================================
# Core Logic
# ==============================================================================

# Filter stdin, removing bind/unbind lines matching target SUBMAP+MODS+KEY and
# their directly preceding comment block. Blank lines act as flush boundaries
# so section-header comments separated by blank lines are preserved.
#
# Submap tracking: the filter tracks submap = directives as it reads. A bind
# line only matches if the current submap context equals the target submap.
# Submap directives themselves are always passed through.
filter_out_bind() {
    local t_mods="$1" t_key="$2" t_submap="${3:-}"
    _normalize_bind_parts t_mods t_key

    local line content l_mods l_key
    local -a pending_comments=()
    local current_submap=""

    while IFS= read -r line || [[ -n "$line" ]]; do
        # Track submap context (always pass through submap directives)
        if _is_submap_directive "$line"; then
            local c
            for c in "${pending_comments[@]}"; do
                printf '%s\n' "$c"
            done
            pending_comments=()
            current_submap="$(_extract_submap_value "$line")"
            printf '%s\n' "$line"
            continue
        fi

        # Blank / whitespace-only lines: flush accumulated comments, pass through
        if [[ "$line" =~ ^[[:space:]]*$ ]]; then
            local c
            for c in "${pending_comments[@]}"; do
                printf '%s\n' "$c"
            done
            pending_comments=()
            printf '%s\n' "$line"
            continue
        fi

        # Pure comment lines: accumulate
        if [[ "$line" =~ ^[[:space:]]*# ]]; then
            pending_comments+=("$line")
            continue
        fi

        # Bind/unbind directive: check for match within correct submap
        if _is_bind_directive "$line"; then
            content="${line#*=}"
            _extract_mods_key l_mods l_key "$content"
            _normalize_bind_parts l_mods l_key

            if [[ "$l_mods" == "$t_mods" && "$l_key" == "$t_key" && "$current_submap" == "$t_submap" ]]; then
                pending_comments=()
                continue
            fi
        fi

        # Non-matching: flush pending comments, output line
        local c
        for c in "${pending_comments[@]}"; do
            printf '%s\n' "$c"
        done
        pending_comments=()
        printf '%s\n' "$line"
    done

    # Flush trailing comments
    local c
    for c in "${pending_comments[@]}"; do
        printf '%s\n' "$c"
    done
}

# Generate the fzf display list.
# Output format per line: display\traw_line\torigin_tag\tsubmap
generate_bind_list() {
    local list_out="$1"
    local file tag color
    local line content l_mods l_key sort_key
    local current_submap=""
    local submap_display=""
    local has_source_directives=0

    local -A override_set=()
    _build_override_set override_set

    {
        for file in "$SOURCE_CONF" "$CUSTOM_CONF"; do
            [[ -f "$file" ]] || continue

            local origin_tag
            if [[ "$file" == "$SOURCE_CONF" ]]; then
                tag="[SRC] "
                color="$BLUE"
                origin_tag="SRC"
            else
                tag="[CUST]"
                color="$GREEN"
                origin_tag="CUST"
            fi

            current_submap=""

            while IFS= read -r line || [[ -n "$line" ]]; do
                # Track submap context
                if _is_submap_directive "$line"; then
                    current_submap="$(_extract_submap_value "$line")"
                    continue
                fi

                # Detect source directives
                if [[ "$line" =~ ^[[:space:]]*source[[:space:]]*= ]]; then
                    has_source_directives=1
                    continue
                fi

                _is_comment_or_blank "$line" && continue
                _is_bind_directive "$line" || continue
                _is_unbind "$line" && continue

                content="${line#*=}"
                _extract_mods_key l_mods l_key "$content"

                local norm_mods="$l_mods" norm_key="$l_key"
                _normalize_bind_parts norm_mods norm_key
                sort_key="${norm_mods}|${norm_key}"

                # Hide SRC binds that have a custom override (submap-aware)
                if [[ "$file" == "$SOURCE_CONF" ]]; then
                    local okey
                    okey="$(_make_override_key "$current_submap" "$norm_mods" "$norm_key")"
                    if [[ -n "${override_set["$okey"]+_}" ]]; then
                        continue
                    fi
                fi

                submap_display=""
                if [[ -n "$current_submap" ]]; then
                    submap_display="${PURPLE}[${current_submap}]${RESET} "
                fi

                printf '%s\t%s%s%s %s%s\t%s\t%s\t%s\n' \
                    "$sort_key" \
                    "$color" "$tag" "$RESET" \
                    "$submap_display" "$line" \
                    "$line" \
                    "$origin_tag" \
                    "$current_submap"
            done < "$file"
        done

        if (( has_source_directives )); then
            printf '%s\t%s%s Note: Some keybinds may be in source-included files %s\t%s\t%s\t%s\n' \
                "zzz_info" "$DIM" "ℹ" "$RESET" "$SOURCE_INFO_MARKER" "INFO" ""
        fi
    } | LC_ALL=C sort -t$'\t' -k1,1 | cut -f2- > "$list_out"
}

# Check for keybind conflicts in a file (submap-aware).
check_conflict() {
    local check_mods="$1" check_key="$2" file="$3" check_submap="${4:-}"
    local ignore_mods="${5:-}" ignore_key="${6:-}" ignore_submap="${7:-}"

    _trim check_mods "$check_mods"
    _trim check_key "$check_key"
    [[ -z "$check_key" ]] && return 1
    [[ ! -f "$file" ]] && return 1

    _normalize_bind_parts check_mods check_key

    local has_ignore=0
    local norm_ignore_mods="" norm_ignore_key=""
    if [[ -n "$ignore_mods" || -n "$ignore_key" ]]; then
        has_ignore=1
        norm_ignore_mods="$ignore_mods"
        norm_ignore_key="$ignore_key"
        _normalize_bind_parts norm_ignore_mods norm_ignore_key
    fi

    local line content l_mods l_key
    local last_match=""
    local current_submap=""

    while IFS= read -r line || [[ -n "$line" ]]; do
        if _is_submap_directive "$line"; then
            current_submap="$(_extract_submap_value "$line")"
            continue
        fi

        _is_comment_or_blank "$line" && continue
        _is_bind_directive "$line" || continue
        _is_unbind "$line" && continue

        content="${line#*=}"
        _extract_mods_key l_mods l_key "$content"
        _normalize_bind_parts l_mods l_key

        if [[ "$l_mods" == "$check_mods" && "$l_key" == "$check_key" && "$current_submap" == "$check_submap" ]]; then
            if (( has_ignore )) && \
               [[ "$l_mods" == "$norm_ignore_mods" && "$l_key" == "$norm_ignore_key" && "$current_submap" == "$ignore_submap" ]]; then
                continue
            fi
            last_match="$line"
        fi
    done < "$file"

    if [[ -n "$last_match" ]]; then
        printf '%s' "$last_match"
        return 0
    fi
    return 1
}

# Atomically write temp_file to CUSTOM_CONF, resolving symlinks.
atomic_write() {
    local temp_file="$1"

    local real_path
    real_path="$(readlink -f "$CUSTOM_CONF")"
    local real_dir="${real_path%/*}"

    local final_temp
    final_temp="$(mktemp "${real_dir}/.keybinds_write.XXXXXX")" || \
        die "Failed to create temp file in ${real_dir}"
    TEMP_FILES+=("$final_temp")

    cat -- "$temp_file" > "$final_temp"
    chmod --reference="$real_path" "$final_temp" 2>/dev/null || chmod 644 "$final_temp"
    mv -f -- "$final_temp" "$real_path"
}

reload_hyprland() {
    if [[ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
        printf '%sNot running under Hyprland; skipping reload.%s\n' "$DIM" "$RESET"
        return
    fi
    command -v hyprctl &>/dev/null || return

    local output
    if output="$(hyprctl reload 2>&1)"; then
        printf '%sHyprland configuration reloaded.%s\n' "$GREEN" "$RESET"
    else
        printf '%s[WARNING]%s Hyprland reload reported an issue:%s\n' "$YELLOW" "$RESET" "$RESET"
        printf '  %s\n' "$output"
        printf 'Your keybind was saved. Reload manually or restart Hyprland.\n'
    fi
}

show_help() {
    printf '%sINSTRUCTIONS:%s\n' "$CYAN" "$RESET"
    printf ' - Edit the line below directly. Keep the commas!\n'
    printf ' - Default: %sbindd = MODS, KEY, DESC, DISPATCHER, ARG%s\n' "$GREEN" "$RESET"
    printf ' - %sNOTE:%s Keys are CASE SENSITIVE in Hyprland!\n' "$YELLOW" "$RESET"
    printf '         (e.g. "S" is Shift+s, "s" is just s)\n'

    printf '\n %sEXAMPLES:%s\n' "$BOLD" "$RESET"
    printf '   1. bindd = $mainMod, Q, Launch Terminal, exec, uwsm-app -- kitty\n'
    printf '   2. bindd = $mainMod, C, Close Window, killactive,\n'
    printf '   3. binded = $mainMod SHIFT, L, Move Right, movewindow, r\n'
    printf '   4. bindd = $mainMod ALT, M, Music, exec, ~/scripts/music.sh\n'
    printf '   5. bindd = $mainMod, S, Screenshot, exec, slurp | grim -g - - | wl-copy\n'

    printf '\n%sFLAGS (append to bind, e.g. binddl, binddel):%s\n' "$PURPLE" "$RESET"
    printf '  %sd%s  description   %s(Human-readable label)%s\n' "$BOLD" "$RESET" "$BRIGHT_WHITE" "$RESET"
    printf '  %sl%s  locked        %s(Works over lockscreen)%s\n' "$BOLD" "$RESET" "$BRIGHT_WHITE" "$RESET"
    printf '  %se%s  repeat        %s(Repeats when held)%s\n' "$BOLD" "$RESET" "$BRIGHT_WHITE" "$RESET"
    printf '  %so%s  long press    %s(Triggers on hold)%s\n' "$BOLD" "$RESET" "$BRIGHT_WHITE" "$RESET"
    printf '  %sm%s  mouse         %s(For mouse clicks)%s\n' "$BOLD" "$RESET" "$BRIGHT_WHITE" "$RESET"
}

# Display confirmation screen. unbind_lines is newline-delimited.
show_confirmation() {
    local action="$1"
    local origin="$2"
    local raw_line="$3"
    local user_line="$4"
    local unbind_lines="$5"
    local bind_submap="${6:-}"

    printf '\n%s┌──────────────────────────────────────────────┐%s\n' "$CYAN" "$RESET"
    printf '%s│              CONFIRM CHANGES                 │%s\n' "$CYAN" "$RESET"
    printf '%s└──────────────────────────────────────────────┘%s\n' "$CYAN" "$RESET"

    if [[ -n "$bind_submap" ]]; then
        printf '  %sSubmap:%s %s\n' "$PURPLE" "$RESET" "$bind_submap"
    fi

    if [[ "$action" == "DELETE" ]]; then
        printf '\n  %sAction:%s  DELETE / UNBIND\n' "$BOLD" "$RESET"
        printf '  %sTarget:%s  %s\n' "$RED" "$RESET" "$raw_line"
        if [[ "$origin" == "CUST" ]]; then
            printf '\n  %sEffect:%s Custom override will be removed.\n' "$YELLOW" "$RESET"
            local del_content="${raw_line#*=}"
            local del_m del_k
            _extract_mods_key del_m del_k "$del_content"
            if _exists_in_source "$del_m" "$del_k" "$bind_submap"; then
                printf '          Original source bind will be %srestored%s.\n' "$GREEN" "$RESET"
            fi
        else
            printf '\n  %sEffect:%s An unbind directive will disable this bind.\n' "$YELLOW" "$RESET"
        fi
    else
        printf '\n  %sAction:%s  SAVE\n' "$BOLD" "$RESET"
        if [[ "$origin" != "NEW" && -n "$raw_line" && "$raw_line" != "$EMPTY_BIND_TEMPLATE" ]]; then
            printf '  %sOLD:%s %s%s%s\n' "$RED" "$RESET" "$DIM" "$raw_line" "$RESET"
        fi
        printf '  %sNEW:%s %s\n' "$GREEN" "$RESET" "$user_line"
        if [[ -n "$unbind_lines" ]]; then
            local ub
            while IFS= read -r ub; do
                [[ -n "$ub" ]] && printf '  %s+ %s%s\n' "$GREY" "$ub" "$RESET"
            done <<< "$unbind_lines"
        fi
    fi

    printf '\n%s[y] Confirm  [n] Go Back%s\n' "$YELLOW" "$RESET"
    local confirm
    read -r -p "Select > " confirm
    case "${confirm,,}" in
        y*) return 0 ;;
        *)  return 1 ;;
    esac
}

show_usage() {
    cat <<'EOF'
Usage: hyprland-keybinds [OPTIONS]

Options:
  --view      Browse keybinds without editing.
  --dry-run   Show what would be written without modifying files.
  --help      Show this help message.

Interactive commands (during editing):
  b           Go back to the selection list.
  q           Quit the program.
  ?           Toggle the help/instructions display.
EOF
}

# ==============================================================================
# Main
# ==============================================================================
main() {
    # --- Parse CLI ---
    while (( $# > 0 )); do
        case "$1" in
            --view)     VIEW_ONLY=1; shift ;;
            --dry-run)  DRY_RUN=1;   shift ;;
            --help|-h)  show_usage;  exit 0 ;;
            *)
                printf 'Unknown option: %s\n' "$1" >&2
                show_usage >&2
                exit 1
                ;;
        esac
    done

    command -v fzf &>/dev/null || die "'fzf' is required but not installed."
    [[ -f "$SOURCE_CONF" ]] || die "Source config missing: $SOURCE_CONF"

    local custom_dir="${CUSTOM_CONF%/*}"
    mkdir -p "$custom_dir"
    [[ -f "$CUSTOM_CONF" ]] || : > "$CUSTOM_CONF"

    if (( ! VIEW_ONLY )); then
        if [[ ! -w "$custom_dir" ]]; then
            die "No write permission to directory: $custom_dir"
        fi
        if [[ -f "$CUSTOM_CONF" && ! -w "$CUSTOM_CONF" ]]; then
            die "No write permission to file: $CUSTOM_CONF"
        fi
        acquire_lock
    fi

    build_dispatcher_list

    # === Outer loop: return to selection after each operation ===
    while true; do
        leave_alt_screen
        cleanup_temp_files

        # 1. Generate bind list
        local list_file
        make_temp list_file
        generate_bind_list "$list_file"

        # 2. fzf selection
        local selected_entry
        local fzf_header
        fzf_header=$'SELECT KEYBIND  │  SRC = Original  │  CUST = Your Override\n  Type to search. Enter = select. Esc = quit.'

        if (( VIEW_ONLY )); then
            if ! selected_entry=$(
                cat -- "$list_file" \
                | fzf --ansi --delimiter=$'\t' --with-nth=1 \
                    --header="[VIEW MODE] ${fzf_header}" \
                    --info=inline --layout=reverse --border \
                    --prompt="Search > "
            ); then
                exit 0
            fi

            local view_raw view_origin_field
            IFS=$'\t' read -r _ view_raw view_origin_field _ <<< "$selected_entry"
            if [[ "$view_raw" == "$SOURCE_INFO_MARKER" ]]; then
                printf '\n%sNote:%s Some keybinds may be defined in source-included files.\n' "$YELLOW" "$RESET"
                printf 'These files are not parsed by this tool.\n'
            else
                printf '\n%s%s%s\n' "$BOLD" "$view_raw" "$RESET"
            fi
            read -r -p "Press Enter to continue..."
            continue
        fi

        # Edit mode: include [+] Create
        local create_display="${BOLD}[+] Create New Keybind${RESET}"

        if ! selected_entry=$(
            {
                printf '%s\t%s\t%s\t%s\n' "$create_display" "$CREATE_MARKER_ID" "NEW" ""
                cat -- "$list_file"
            } | fzf --ansi --delimiter=$'\t' --with-nth=1 \
                --header="$fzf_header" \
                --info=inline --layout=reverse --border \
                --prompt="Search > "
        ); then
            exit 0
        fi

        # 3. Parse selection using structured fields
        local _display raw_line origin_field bind_submap
        IFS=$'\t' read -r _display raw_line origin_field bind_submap <<< "$selected_entry"

        local origin=""

        if [[ "$raw_line" == "$CREATE_MARKER_ID" ]]; then
            origin="NEW"
            raw_line="$EMPTY_BIND_TEMPLATE"
            bind_submap=""
        elif [[ "$raw_line" == "$SOURCE_INFO_MARKER" ]]; then
            printf '\n%sNote:%s Some keybinds may be defined in source-included files.\n' "$YELLOW" "$RESET"
            printf 'These files are not parsed by this tool.\n'
            read -r -p "Press Enter to continue..."
            continue
        elif [[ "$origin_field" == "CUST" ]]; then
            origin="CUST"
        else
            origin="SRC"
        fi

        # 4. Action choice for existing binds
        if [[ "$origin" != "NEW" ]]; then
            printf '\n%sSelected:%s %s\n' "$BOLD" "$RESET" "$raw_line"
            if [[ -n "$bind_submap" ]]; then
                printf '%sSubmap:%s %s\n' "$PURPLE" "$RESET" "$bind_submap"
            fi
            printf '\n%s[e] Edit  [d] Delete / Unbind  [b] Back to list  [q] Quit%s\n' "$YELLOW" "$RESET"
            local action_choice
            read -r -p "Select > " action_choice

            case "${action_choice,,}" in
                d*)
                    # ═══ Delete / Unbind Flow ═══
                    local del_content="${raw_line#*=}"
                    local del_mods del_key
                    _extract_mods_key del_mods del_key "$del_content"

                    if ! show_confirmation "DELETE" "$origin" "$raw_line" "" "" "$bind_submap"; then
                        continue
                    fi

                    if (( DRY_RUN )); then
                        printf '\n%s[DRY RUN]%s Would delete/unbind: %s\n' "$CYAN" "$RESET" "$raw_line"
                        if [[ "$origin" == "CUST" ]]; then
                            printf '  Remove custom entry from %s\n' "$CUSTOM_CONF"
                        else
                            printf '  Append "unbind = %s, %s" to %s\n' "$del_mods" "$del_key" "$CUSTOM_CONF"
                        fi
                        read -r -p "Press Enter to continue..."
                        continue
                    fi

                    local temp_del
                    make_temp temp_del "${CUSTOM_CONF}.XXXXXX"

                    if [[ "$origin" == "CUST" ]]; then
                        filter_out_bind "$del_mods" "$del_key" "$bind_submap" < "$CUSTOM_CONF" > "$temp_del"
                        atomic_write "$temp_del"
                    else
                        # SRC bind: check if already unbound
                        local already_unbound=0
                        local n_del_m="$del_mods" n_del_k="$del_key"
                        _normalize_bind_parts n_del_m n_del_k

                        local chk_line chk_content chk_m chk_k chk_submap=""
                        while IFS= read -r chk_line || [[ -n "$chk_line" ]]; do
                            if _is_submap_directive "$chk_line"; then
                                chk_submap="$(_extract_submap_value "$chk_line")"
                                continue
                            fi
                            _is_unbind "$chk_line" || continue
                            chk_content="${chk_line#*=}"
                            _extract_mods_key chk_m chk_k "$chk_content"
                            _normalize_bind_parts chk_m chk_k
                            if [[ "$chk_m" == "$n_del_m" && "$chk_k" == "$n_del_k" && "$chk_submap" == "$bind_submap" ]]; then
                                already_unbound=1
                                break
                            fi
                        done < "$CUSTOM_CONF"

                        if (( already_unbound )); then
                            printf '\n%sThis bind is already unbound in your custom config.%s\n' "$YELLOW" "$RESET"
                            read -r -p "Press Enter to continue..."
                            continue
                        fi

                        # Filter stale entries, then append unbind
                        filter_out_bind "$del_mods" "$del_key" "$bind_submap" < "$CUSTOM_CONF" > "$temp_del"

                        local timestamp
                        printf -v timestamp '%(%Y-%m-%d %H:%M)T' -1
                        {
                            printf '\n# [%s] UNBIND\n' "$timestamp"
                            if [[ -n "$bind_submap" ]]; then
                                printf 'submap = %s\n' "$bind_submap"
                            fi
                            printf 'unbind = %s, %s\n' "$del_mods" "$del_key"
                            if [[ -n "$bind_submap" ]]; then
                                printf 'submap = reset\n'
                            fi
                        } >> "$temp_del"

                        atomic_write "$temp_del"
                    fi

                    cleanup_temp_files

                    printf '\n%s[SUCCESS]%s Keybind removed.\n' "$GREEN" "$RESET"
                    reload_hyprland
                    read -r -p "Press Enter to continue..."
                    continue
                    ;;
                b*) continue ;;
                q*) exit 0 ;;
                e*) ;; # Fall through to edit
                *)  continue ;;
            esac
        fi

        # 5. Setup editing context
        local orig_mods="" orig_key=""
        local current_input="$raw_line"

        if [[ "$origin" != "NEW" ]]; then
            local parse_content="${raw_line#*=}"
            _extract_mods_key orig_mods orig_key "$parse_content"
        fi

        # 6. Interactive edit loop
        local user_line=""
        local show_help_text=1
        local go_back=0

        enter_alt_screen

        while true; do
            tput clear 2>/dev/null || clear

            printf '%s┌──────────────────────────────────────────────┐%s\n' "$BLUE" "$RESET"
            printf '%s│   MODE: %-37s│%s\n' "$YELLOW" "${origin} EDIT" "$RESET"
            printf '%s└──────────────────────────────────────────────┘%s\n' "$BLUE" "$RESET"

            if [[ "$origin" != "NEW" ]]; then
                printf ' %sTarget:%s %s\n' "$GREY" "$RESET" "$raw_line"
                if [[ -n "$bind_submap" ]]; then
                    printf ' %sSubmap:%s %s\n' "$PURPLE" "$RESET" "$bind_submap"
                fi
                printf '\n'
            fi

            if (( show_help_text )); then
                show_help
                printf '\n %s(Press ? to hide help)%s\n' "$DIM" "$RESET"
            else
                printf ' %s(Press ? to show help)%s\n' "$DIM" "$RESET"
            fi

            printf '\n%sEnter keybind ("b" = back, "q" = quit):%s\n' "$BOLD" "$RESET"
            local prompt=$'\001'"$PURPLE"$'\002''> '$'\001'"$RESET"$'\002'

            if ! IFS= read -e -r -p "$prompt" -i "$current_input" user_line; then
                go_back=1
                break
            fi

            # Navigation
            if [[ "$user_line" == "b" || "$user_line" == "B" ]]; then
                go_back=1
                break
            fi
            if [[ "$user_line" == "q" || "$user_line" == "Q" ]]; then
                leave_alt_screen
                exit 0
            fi
            if [[ "$user_line" == "?" ]]; then
                show_help_text=$(( ! show_help_text ))
                continue
            fi

            if [[ -z "$user_line" || "$user_line" == "$EMPTY_BIND_TEMPLATE" ]]; then
                continue
            fi

            # ──── Parse & Validate ────
            local bind_type="${user_line%%=*}"
            _trim bind_type "$bind_type"
            local edit_content="${user_line#*=}"
            _trim edit_content "$edit_content"

            if [[ "$bind_type" != bind* ]]; then
                printf '\n%sError:%s Line must start with a bind directive (bind, bindd, bindl, ...).\n' "$RED" "$RESET"
                read -r -p "Press Enter to continue..."
                current_input="$user_line"
                continue
            fi

            local new_mods="" new_key=""
            _extract_mods_key new_mods new_key "$edit_content"

            if [[ -z "$new_key" ]]; then
                printf '\n%sError:%s Key is required (second comma-separated field).\n' "$RED" "$RESET"
                read -r -p "Press Enter to continue..."
                current_input="$user_line"
                continue
            fi

            # ──── Auto-Fix: missing 'd' flag ────
            local flags="${bind_type#bind}"
            local field_count
            field_count="$(_count_fields "$edit_content")"

            if (( field_count >= 5 )) && [[ "$flags" != *d* ]]; then
                # Check if field index 2 is a known dispatcher.
                # If it IS a dispatcher, the extra commas are likely from args,
                # not from a description field being present.
                local field2
                field2="$(_extract_field "$edit_content" 2)"

                if ! _validate_dispatcher "$field2"; then
                    local fixed_type="bindd${flags}"
                    printf '\n%s[AUTO-FIX]%s Missing "d" flag: "%s" → "%s"\n' \
                        "$CYAN" "$RESET" "$bind_type" "$fixed_type"
                    printf '           %s[Enter]%s Accept  %s[e]%s Edit  %s[w]%s Write as-is\n' \
                        "$BOLD" "$RESET" "$YELLOW" "$RESET" "$RED" "$RESET"

                    local fix_choice
                    read -r -p "Select > " fix_choice
                    case "${fix_choice,,}" in
                        e*)
                            current_input="$user_line"
                            continue
                            ;;
                        w*)
                            : # Write as-is
                            ;;
                        *)
                            bind_type="$fixed_type"
                            user_line="${fixed_type} = ${edit_content}"
                            flags="${bind_type#bind}"
                            ;;
                    esac
                fi
            fi

            # ──── Dispatcher Validation ────
            local dispatcher
            dispatcher="$(_extract_dispatcher "$bind_type" "$edit_content")"
            if [[ -n "$dispatcher" ]] && ! _validate_dispatcher "$dispatcher"; then
                printf '\n%s[WARNING]%s Unrecognized dispatcher: "%s"\n' "$YELLOW" "$RESET" "$dispatcher"
                printf '         Known: exec, killactive, workspace, movewindow, ...\n'
                printf '         %s[Enter]%s Continue anyway  %s[e]%s Edit\n' \
                    "$BOLD" "$RESET" "$YELLOW" "$RESET"

                local disp_choice
                read -r -p "Select > " disp_choice
                case "${disp_choice,,}" in
                    e*)
                        current_input="$user_line"
                        continue
                        ;;
                    *)  : ;;
                esac
            fi

            # ──── Conflict Detection (submap-aware) ────
            printf '\n%sChecking for conflicts...%s ' "$CYAN" "$RESET"

            local conflict_line=""

            if conflict_line="$(check_conflict "$new_mods" "$new_key" "$CUSTOM_CONF" "$bind_submap" "$orig_mods" "$orig_key" "$bind_submap")"; then
                printf '%sCONFLICT (Custom)!%s\n  %s\n' "$RED" "$RESET" "$conflict_line"
            elif ! _is_already_unbound_in_custom "$new_mods" "$new_key" "$bind_submap" && \
                 conflict_line="$(check_conflict "$new_mods" "$new_key" "$SOURCE_CONF" "$bind_submap" "$orig_mods" "$orig_key" "$bind_submap")"; then
                printf '%sCONFLICT (Source)!%s\n  %s\n' "$RED" "$RESET" "$conflict_line"
            else
                printf '%sNone%s\n' "$GREEN" "$RESET"
                conflict_line=""
            fi

            if [[ -n "$conflict_line" ]]; then
                printf '\n%s[y] Overwrite  [n] Retry  [b] Back to list%s\n' "$YELLOW" "$RESET"
                local choice
                read -r -p "Select > " choice

                case "${choice,,}" in
                    y*)
                        break # Proceed to write
                        ;;
                    b*)
                        go_back=1
                        break
                        ;;
                    *)
                        current_input="$user_line"
                        continue
                        ;;
                esac
            fi

            break # No conflict, proceed to write
        done

        leave_alt_screen

        if (( go_back )); then
            continue
        fi

        # 7. Compute unbind commands
        declare -A unbind_map=()

        # SRC origin: unbind the original source key being replaced
        if [[ "$origin" == "SRC" && ( -n "$orig_mods" || -n "$orig_key" ) ]]; then
            if _exists_in_source "$orig_mods" "$orig_key" "$bind_submap"; then
                local nk_m="$orig_mods" nk_k="$orig_key"
                _normalize_bind_parts nk_m nk_k
                unbind_map["${nk_m}|${nk_k}"]="unbind = ${orig_mods}, ${orig_key}"
            fi
        fi

        # Any origin: unbind the new key if it exists in source (same submap)
        if _exists_in_source "$new_mods" "$new_key" "$bind_submap"; then
            local nk_m="$new_mods" nk_k="$new_key"
            _normalize_bind_parts nk_m nk_k
            unbind_map["${nk_m}|${nk_k}"]="unbind = ${new_mods}, ${new_key}"
        fi

        # Build newline-delimited string for display
        local unbind_lines=""
        local _ub_val
        for _ub_val in "${unbind_map[@]}"; do
            if [[ -n "$unbind_lines" ]]; then
                unbind_lines+=$'\n'"$_ub_val"
            else
                unbind_lines="$_ub_val"
            fi
        done

        # 8. Confirmation
        if ! show_confirmation "SAVE" "$origin" "$raw_line" "$user_line" "$unbind_lines" "$bind_submap"; then
            continue
        fi

        # 9. Dry-run gate
        if (( DRY_RUN )); then
            printf '\n%s[DRY RUN]%s Would write to %s:\n' "$CYAN" "$RESET" "$CUSTOM_CONF"
            if [[ -n "$bind_submap" ]]; then
                printf '  submap = %s\n' "$bind_submap"
            fi
            if [[ -n "$unbind_lines" ]]; then
                local ub
                while IFS= read -r ub; do
                    [[ -n "$ub" ]] && printf '  %s\n' "$ub"
                done <<< "$unbind_lines"
            fi
            printf '  %s\n' "$user_line"
            if [[ -n "$bind_submap" ]]; then
                printf '  submap = reset\n'
            fi
            printf '\nNo changes were made.\n'
            read -r -p "Press Enter to continue..."
            continue
        fi

        # 10. Write changes
        local timestamp
        printf -v timestamp '%(%Y-%m-%d %H:%M)T' -1

        local temp_write
        make_temp temp_write "${CUSTOM_CONF}.XXXXXX"

        # Start from current custom config
        cat -- "$CUSTOM_CONF" > "$temp_write"

        # Filter out the OLD key (the bind being replaced) in the correct submap
        if [[ -n "$orig_mods" || -n "$orig_key" ]]; then
            local temp_f1
            make_temp temp_f1 "${CUSTOM_CONF}.XXXXXX"
            filter_out_bind "$orig_mods" "$orig_key" "$bind_submap" < "$temp_write" > "$temp_f1"
            cat -- "$temp_f1" > "$temp_write"
        fi

        # Filter out the NEW key if different from old (existing bind on target key)
        local norm_new_m="$new_mods" norm_new_k="$new_key"
        _normalize_bind_parts norm_new_m norm_new_k
        local norm_orig_m="${orig_mods}" norm_orig_k="${orig_key}"
        if [[ -n "$orig_mods" || -n "$orig_key" ]]; then
            _normalize_bind_parts norm_orig_m norm_orig_k
        fi

        if [[ "$norm_new_m" != "$norm_orig_m" || "$norm_new_k" != "$norm_orig_k" ]]; then
            local temp_f2
            make_temp temp_f2 "${CUSTOM_CONF}.XXXXXX"
            filter_out_bind "$new_mods" "$new_key" "$bind_submap" < "$temp_write" > "$temp_f2"
            cat -- "$temp_f2" > "$temp_write"
        fi

        # Filter out conflict key if it differs from both old and new
        if [[ -n "$conflict_line" ]]; then
            local c_content="${conflict_line#*=}"
            local c_m c_k
            _extract_mods_key c_m c_k "$c_content"
            local norm_c_m="$c_m" norm_c_k="$c_k"
            _normalize_bind_parts norm_c_m norm_c_k

            local conflict_already_filtered=0
            if [[ "$norm_c_m" == "$norm_orig_m" && "$norm_c_k" == "$norm_orig_k" ]]; then
                conflict_already_filtered=1
            fi
            if [[ "$norm_c_m" == "$norm_new_m" && "$norm_c_k" == "$norm_new_k" ]]; then
                conflict_already_filtered=1
            fi

            if (( ! conflict_already_filtered )); then
                local temp_f3
                make_temp temp_f3 "${CUSTOM_CONF}.XXXXXX"
                filter_out_bind "$c_m" "$c_k" "$bind_submap" < "$temp_write" > "$temp_f3"
                cat -- "$temp_f3" > "$temp_write"
            fi
        fi

        # Append new bind block (wrapped in submap context if needed)
        {
            printf '\n# [%s] %s\n' "$timestamp" "$origin"

            if [[ -n "$bind_submap" ]]; then
                printf 'submap = %s\n' "$bind_submap"
            fi

            local _ub_v
            for _ub_v in "${unbind_map[@]}"; do
                printf '%s\n' "$_ub_v"
            done

            printf '%s\n' "$user_line"

            if [[ -n "$bind_submap" ]]; then
                printf 'submap = reset\n'
            fi
        } >> "$temp_write"

        atomic_write "$temp_write"
        cleanup_temp_files

        printf '\n%s[SUCCESS]%s Saved to %s\n' "$GREEN" "$RESET" "$CUSTOM_CONF"
        reload_hyprland

        printf '\n%s[Enter] Edit another  [q] Quit%s\n' "$YELLOW" "$RESET"
        local end_choice
        read -r -p "Select > " end_choice
        case "${end_choice,,}" in
            q*) exit 0 ;;
            *)  continue ;;
        esac
    done
}

main "$@"
