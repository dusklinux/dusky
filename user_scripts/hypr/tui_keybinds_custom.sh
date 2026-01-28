#!/usr/bin/env bash
# ==============================================================================
# Description: Advanced TUI for Hyprland Keybinds.
#              - Create New Binds
#              - Single-Line "Power Edit" Mode.
#              - Auto-correction of bind/bindd based on comma count.
#              - Stacked Conflict Resolution (Edit chains).
#              - Auto-Reloads Hyprland on success.
# Author:      Dusk
# Version:     v18.9 (Real-World Config Examples)
# Reference:   https://wiki.hypr.land/Configuring/Binds/
# ==============================================================================

# --- Strict Mode & Version Check ---
set -euo pipefail
shopt -s extglob # Required for *([[:space:]]) extended glob patterns

# Enforce minimum Bash version (4.3+) for namerefs and strict error handling
if (( BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 3) )); then
    printf 'FATAL: This script requires Bash 4.3 or newer.\n' >&2
    exit 1
fi

# --- ANSI Colors (readonly for immutability) ---
readonly BLUE=$'\033[0;34m'
readonly GREEN=$'\033[0;32m'
readonly YELLOW=$'\033[0;33m'
readonly RED=$'\033[0;31m'
readonly CYAN=$'\033[0;36m'
readonly PURPLE=$'\033[0;35m'
readonly BOLD=$'\033[1m'
readonly RESET=$'\033[0m'
readonly BRIGHT_WHITE=$'\033[0;97m'

# --- Paths ---
readonly SOURCE_CONF="${HOME}/.config/hypr/source/keybinds.conf"
readonly CUSTOM_CONF="${HOME}/.config/hypr/edit_here/source/keybinds.conf"

# --- Globals ---
TEMP_FILE=""
PENDING_CONTENT="" # Stores stashed edits during conflict resolution

# ==============================================================================
# Helpers
# ==============================================================================

# Cleanup function for temp files. 
# Uses explicit 'if' to avoid masking errors in 'set -e' mode.
cleanup() {
    if [[ -n "${TEMP_FILE:-}" && -f "$TEMP_FILE" ]]; then
        rm -f -- "$TEMP_FILE"
    fi
}
trap cleanup EXIT INT TERM HUP

# Prints a fatal error to stderr and exits.
die() {
    printf '%s[FATAL]%s %s\n' "${RED}" "${RESET}" "$1" >&2
    exit 1
}

# Trims leading and trailing whitespace from a string.
# Uses a nameref to modify the caller's variable in-place.
# Args: $1 = variable name (by reference), $2 = value to trim
_trim() {
    local -n _ref="$1"
    _ref="$2"
    _ref="${_ref#"${_ref%%[![:space:]]*}"}"
    _ref="${_ref%"${_ref##*[![:space:]]}"}"
}

# --- Conflict Detection ---
# Checks if a given mods+key combo already exists in a file.
# Prints the conflicting line to stdout and returns 0 on conflict.
# Returns 1 if no conflict is found.
# Args: $1 = mods_raw, $2 = key_raw, $3 = file_path
check_conflict() {
    local check_mods_raw="$1"
    local check_key_raw="$2"
    local file="$3"

    local check_mods check_key
    _trim check_mods "$check_mods_raw"
    _trim check_key "$check_key_raw"
    check_mods="${check_mods,,}"
    check_key="${check_key,,}"

    # Ignore checks against empty keys (from incomplete input)
    [[ -z "$check_key" ]] && return 1

    local line
    local last_match="" # Store the latest match found

    while IFS= read -r line || [[ -n "$line" ]]; do
        # Skip empty lines
        [[ -z "$line" ]] && continue
        # Skip comment lines (optional whitespace followed by #)
        [[ "$line" == *([[:space:]])"#"* ]] && continue
        # Skip lines that don't start with a bind keyword
        [[ "$line" != *([[:space:]])bind* ]] && continue

        local after_equals="${line#*=}"
        local part0 part1

        IFS=',' read -r part0 part1 _ <<< "$after_equals"

        local line_mods line_key
        _trim line_mods "$part0"
        _trim line_key "$part1"
        
        # Save raw values for stack check and unbind extraction
        local raw_mods="$line_mods"
        local raw_key="$line_key"

        line_mods="${line_mods,,}"
        line_key="${line_key,,}"

        # Normalize '$mainmod' to 'super'
        local norm_check_mods="${check_mods//\$mainmod/super}"
        local norm_line_mods="${line_mods//\$mainmod/super}"

        if [[ "$norm_line_mods" == "$norm_check_mods" && "$line_key" == "$check_key" ]]; then
            # Stack Awareness Check
            local unbind_sig="unbind = ${raw_mods}, ${raw_key}"
            if [[ "$PENDING_CONTENT" == *"$unbind_sig"* ]]; then
                 continue 
            fi

            # Keep updating last_match to find the LATEST conflict in the file
            last_match="$line"
        fi
    done < "$file"
    
    # If we found at least one match, return the LAST one found.
    if [[ -n "$last_match" ]]; then
        printf '%s' "$last_match"
        return 0
    fi
    
    return 1
}

# ==============================================================================
# Main Application Logic
# ==============================================================================
main() {
    command -v fzf &>/dev/null || die "'fzf' is required but not found in PATH."
    [[ -f "$SOURCE_CONF" ]] || die "Source config missing: $SOURCE_CONF"

    local custom_dir="${CUSTOM_CONF%/*}"
    mkdir -p "$custom_dir" || die "Failed to create directory: $custom_dir"
    [[ -f "$CUSTOM_CONF" ]] || : > "$CUSTOM_CONF" || die "Cannot create file: $CUSTOM_CONF"

    # 1. Select Original Bind OR Create New
    local create_marker="[+] Create New Keybind"
    local selected_line

    if ! selected_line=$(
        {
            printf '%s\n' "$create_marker"
            grep -E '^\s*bind[a-z]*\s*=' "$SOURCE_CONF"
        } | \
        fzf --header="SELECT BIND TO EDIT OR CREATE NEW" \
            --info=inline --layout=reverse --border --prompt="Select > "
    ); then
        exit 0 # User cancelled fzf
    fi
    [[ -z "$selected_line" ]] && exit 0

    # 2. Extract Original Mods/Key OR Initialize New
    local orig_mods=""
    local orig_key=""
    local current_input=""
    local is_new=0 # Integer boolean: 0=False, 1=True

    if [[ "$selected_line" == "$create_marker" ]]; then
        is_new=1
        selected_line="<New Keybind>"
        current_input="bindd = "
    else
        local orig_content="${selected_line#*=}"
        local orig_part0 orig_part1
        IFS=',' read -r orig_part0 orig_part1 _ <<< "$orig_content"

        _trim orig_mods "$orig_part0"
        _trim orig_key "$orig_part1"
        current_input="$selected_line"
    fi

    # 3. Edit Loop
    local conflict_unbind_cmd=""
    local user_line=""

    while true; do
        clear
        printf '%s┌──────────────────────────────────────────────┐%s\n' "$BLUE" "$RESET"
        if (( is_new )); then
            printf '%s│ %sCREATING NEW KEYBIND%s                         │%s\n' "$BLUE" "$GREEN" "$BLUE" "$RESET"
        else
            printf '%s│ %sEDITING KEYBIND (One-Line)%s                   │%s\n' "$BLUE" "$CYAN" "$BLUE" "$RESET"
        fi
        printf '%s└──────────────────────────────────────────────┘%s\n' "$BLUE" "$RESET"

        if (( ! is_new )); then
            printf ' %sOriginal:%s %s\n\n' "$YELLOW" "$RESET" "$selected_line"
        fi

        if [[ -n "$PENDING_CONTENT" ]]; then
            printf '%s[INFO]%s You have pending edits that will be saved after this.\n\n' "$PURPLE" "$RESET"
        fi

        printf '%sINSTRUCTIONS:%s\n' "$CYAN" "$RESET"
        printf ' - Edit the line below directly. Keep the commas!\n'
        printf ' - Default Format: %sbindd = MODS, KEY, DESC, DISPATCHER, ARG%s\n' "$GREEN" "$RESET"
        
        # --- Warnings and Examples ---
        printf ' - %sNOTE:%s Keys are CASE SENSITIVE! (e.g. "S" is Shift+s, "s" is just s)\n' "$YELLOW" "$RESET"
        printf '\n %sEXAMPLES:%s\n' "$BOLD" "$RESET"
        printf '   1. bindd = $mainMod, Q, Launch Terminal, exec, uwsm-app -- kitty\n'
        printf '   2. bindd = $mainMod, C, Close Window, killactive,\n'
        printf '   3. binded = $mainMod SHIFT, L, Move Right, movewindow, r\n'
        printf '   4. bindeld = , XF86AudioRaiseVolume, Vol Up, exec, swayosd-client --output-volume raise\n'
        printf '   5. bindd = $mainMod, S, Screenshot, exec, slurp | grim -g - - | wl-copy\n'

        printf '\n%sFLAGS REFERENCE (Append to bind, e.g. binddl, binddel):%s\n' "$PURPLE" "$RESET"
        printf '  %sd%s  has description  %s(Easier for discerning what the keybind does)%s\n' "$BOLD" "$RESET" "$BRIGHT_WHITE" "$RESET"
        printf '  %sl%s  locked           %s(Works over lockscreen)%s\n' "$BOLD" "$RESET" "$BRIGHT_WHITE" "$RESET"
        printf '  %se%s  repeat           %s(Repeats when held)%s\n' "$BOLD" "$RESET" "$BRIGHT_WHITE" "$RESET"
        printf '  %so%s  long press       %s(Triggers on hold)%s\n' "$BOLD" "$RESET" "$BRIGHT_WHITE" "$RESET"
        printf '  %sm%s  mouse            %s(For mouse clicks)%s\n\n' "$BOLD" "$RESET" "$BRIGHT_WHITE" "$RESET"

        # --- The Power Edit Prompt ---
        if ! IFS= read -e -r -p "${PURPLE}> ${RESET}" -i "$current_input" user_line; then
            printf '\n%s[INFO]%s Edit cancelled.\n' "$YELLOW" "$RESET"
            exit 0
        fi

        if [[ -z "$user_line" || "$user_line" == "bindd = " ]]; then
            printf '\n%s[WARN]%s Input invalid or unchanged template. Press Enter...\n' "$YELLOW" "$RESET"
            read -r
            continue
        fi

        # 4. Analyze User Input
        local type="${user_line%%=*}"
        _trim type "$type"

        local content="${user_line#*=}"
        local -a parts
        
        # NOTE: The trailing space in "$content " is a DELIBERATE HACK.
        IFS=',' read -ra parts <<< "$content "
        local part_count="${#parts[@]}"

        local new_mods new_key
        _trim new_mods "${parts[0]:-}"
        _trim new_key "${parts[1]:-}"

        if [[ -z "$new_key" || "$new_key" == "KEY" ]]; then
            printf '\n%s[ERR]%s Invalid Key defined.\n' "$RED" "$RESET"
            read -r
            current_input="$user_line"
            continue
        fi

        # --- Smart Type Correction ---
        local base_keyword="bind"
        local flags="${type#bind}"
        local fixed_type="$type"
        local type_was_corrected=0

        if (( part_count >= 5 )); then
            if [[ "$flags" != *d* ]]; then
                fixed_type="${base_keyword}${flags}d"
                type_was_corrected=1
            fi
        elif (( part_count == 4 )); then
            if [[ "$flags" == *d* && "$type" != "bindm" ]]; then
                flags="${flags//d/}"
                fixed_type="${base_keyword}${flags}"
                type_was_corrected=1
            fi
        fi

        if (( type_was_corrected )); then
            printf '\n%s[AUTO-FIX]%s Bind type corrected: "%s" → "%s"\n' "$CYAN" "$RESET" "$type" "$fixed_type"
            current_input="${fixed_type} = ${content}"
            user_line="${fixed_type} = ${content}"
            printf '           Press Enter to continue with the corrected line...\n'
            read -r
        fi

        # 5. Conflict Check
        printf '\n%sChecking for conflicts...%s ' "$CYAN" "$RESET"
        local conflict_line=""
        local conflict_source=""

        if (( is_new )) || [[ "${new_mods,,}" != "${orig_mods,,}" || "${new_key,,}" != "${orig_key,,}" ]]; then
            if conflict_line="$(check_conflict "$new_mods" "$new_key" "$CUSTOM_CONF")"; then
                conflict_source="CUSTOM"
            elif conflict_line="$(check_conflict "$new_mods" "$new_key" "$SOURCE_CONF")"; then
                conflict_source="SOURCE"
            fi
        fi

        if [[ -n "$conflict_line" ]]; then
            printf '%sFOUND!%s\n' "$RED" "$RESET"
            printf '  [%s] %s\n' "$conflict_source" "$conflict_line"
            printf '\n%sOPTIONS:%s\n' "$BOLD" "$RESET"
            printf '  %s[y]%s Overwrite conflict (Unbind it)\n' "$RED" "$RESET"
            printf '  %s[e]%s Edit the conflicting line instead (Saves current edit to stack)\n' "$YELLOW" "$RESET"
            printf '  %s[n]%s Edit my line again\n' "$GREEN" "$RESET"

            local choice
            read -r -p "Select > " choice

            if [[ "${choice,,}" == y* ]]; then
                # Extract exact keys from the file for unbind
                local c_content="${conflict_line#*=}"
                local c_part0 c_part1
                IFS=',' read -r c_part0 c_part1 _ <<< "$c_content"
                
                local c_mods c_key
                _trim c_mods "$c_part0"
                _trim c_key "$c_part1"
                
                conflict_unbind_cmd="unbind = ${c_mods}, ${c_key}"
                break

            elif [[ "${choice,,}" == e* ]]; then
                # --- STACKED EDIT LOGIC ---
                local p_timestamp
                printf -v p_timestamp '%(%Y-%m-%d %H:%M)T' -1

                local current_step_block
                current_step_block=$(
                    if (( is_new )); then
                        printf '\n# [%s] Stacked Create (Saved from conflict)\n' "$p_timestamp"
                    else
                        printf '\n# [%s] Stacked Edit (Saved from conflict)\n' "$p_timestamp"
                        printf '# Original: %s\n' "$selected_line"
                        printf 'unbind = %s, %s\n' "$orig_mods" "$orig_key"
                    fi
                    printf '%s\n' "$user_line"
                )

                if [[ -z "$PENDING_CONTENT" ]]; then
                    PENDING_CONTENT="$current_step_block"
                else
                    PENDING_CONTENT="${current_step_block}"$'\n'"${PENDING_CONTENT}"
                fi

                selected_line="$conflict_line"
                current_input="$conflict_line"
                is_new=0

                local c_content="${selected_line#*=}"
                local c_part0 c_part1
                IFS=',' read -r c_part0 c_part1 _ <<< "$c_content"
                _trim orig_mods "$c_part0"
                _trim orig_key "$c_part1"

                continue
            else
                current_input="$user_line"
                continue
            fi
        else
            printf '%sOK%s\n' "$GREEN" "$RESET"
            break
        fi
    done

    # 6. Final Write (Atomic)
    local timestamp
    printf -v timestamp '%(%Y-%m-%d %H:%M)T' -1

    TEMP_FILE="$(mktemp "${CUSTOM_CONF}.XXXXXX")" || die "Failed to create temp file."

    {
        [[ -s "$CUSTOM_CONF" ]] && cat -- "$CUSTOM_CONF"

        printf '\n# [%s] %s\n' "$timestamp" "$( (( is_new )) && printf 'Create' || printf 'Edit')"

        if (( ! is_new )); then
            printf '# Original: %s\n' "$selected_line"
            printf 'unbind = %s, %s\n' "$orig_mods" "$orig_key"
        fi

        if [[ -n "$conflict_unbind_cmd" ]]; then
            printf '# Resolving Conflict:\n%s\n' "$conflict_unbind_cmd"
        fi
        printf '%s\n' "$user_line"

        if [[ -n "${PENDING_CONTENT:-}" ]]; then
            printf '%s\n' "$PENDING_CONTENT"
        fi

    } > "$TEMP_FILE" || die "Failed to write to temp file."

    mv -f -- "$TEMP_FILE" "$CUSTOM_CONF" || die "Failed to finalize config file."
    TEMP_FILE="" 

    printf '\n%s[SUCCESS]%s Saved to %s\n' "$GREEN" "$RESET" "$CUSTOM_CONF"
    if [[ -n "$PENDING_CONTENT" ]]; then
        printf '%s[NOTE]%s Stacked edits were also applied.\n' "$PURPLE" "$RESET"
    fi

    # 7. Auto-Reload
    if command -v hyprctl &>/dev/null; then
        printf '%sReloading Hyprland...%s ' "$BLUE" "$RESET"
        
        if reload_out=$(hyprctl reload 2>&1); then
            printf '%sDONE%s\n' "$GREEN" "$RESET"
        else
            printf '%sFAILED%s\n' "$RED" "$RESET"
            printf '%s[ERROR DETAILS]%s\n%s\n' "$RED" "$RESET" "$reload_out"
        fi
    else
        printf 'Run %shyprctl reload%s to apply changes.\n' "$BOLD" "$RESET"
    fi
}

main "$@"
