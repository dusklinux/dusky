#!/usr/bin/env bash
# Configures hardware for non asus devices
# ==============================================================================
# ARCH LINUX / HYPRLAND / UWSM - HARDWARE CONFIGURATION TWEAKER
# ==============================================================================
# Purpose: Manage config lines for ASUS TUF F15 vs Non-ASUS hardware.
#          - Non-ASUS: Comments out specific hardware lines.
#          - ASUS:     Uncomments/Restores those lines.
# Author:  Elite DevOps Architect
# Shell:   Bash 5.0+
# ==============================================================================

set -euo pipefail

# --- CONFIGURATION SECTION ----------------------------------------------------
# Define rules here. 
# Usage: add_rule "$FILE_VARIABLE" "Exact content of the line"

declare -a RULES_FILES
declare -a RULES_PATTERNS

add_rule() {
    RULES_FILES+=("$1")
    RULES_PATTERNS+=("$2")
}

setup_rules() {
    # --- Define Paths Here for Easy Editing ---
    local HYPR_KEYBINDS="$HOME/.config/hypr/source/keybinds.conf"
    local HYPR_INPUT="$HOME/.config/hypr/source/input.conf"

    # --- 1. Hyprland Keybindings (ASUS Specifics & AI Tools) ---
    
    # ASUS Control (bindd)
    add_rule "$HYPR_KEYBINDS" 'bindd = , XF86Launch3, ASUS Control, exec, uwsm-app -- $terminal --class asusctl.sh -e sudo $scripts/asus/asusctl.sh'
    
    # Refresh Rate Toggles (bindld)
    add_rule "$HYPR_KEYBINDS" 'bindld = ALT, 7, Set Refresh rate to 48Hz Asus Tuf, exec, hyprctl keyword monitor eDP-1,1920x1080@48,0x0,1.6 && sleep 2 && hyprctl keyword misc:vrr 0'
    add_rule "$HYPR_KEYBINDS" 'bindld = ALT, 8, Set Refresh rate to 144Hz Asus Tuf, exec, hyprctl keyword monitor eDP-1,1920x1080@144,0x0,1.6 && sleep 2 && hyprctl keyword misc:vrr 1'
    
    # AI / TTS Tools (Updated paths)
    add_rule "$HYPR_KEYBINDS" 'bindd = $mainMod, O, TTS Kokoro GPU, exec, wl-copy "$(wl-paste -p)" && uwsm-app -- $scripts/tts_stt/kokoro_gpu/speak.sh'
    add_rule "$HYPR_KEYBINDS" 'bindd = $mainMod SHIFT, O, TTS Kokoro CPU, exec, wl-copy "$(wl-paste -p)" && uwsm-app -- $scripts/tts_stt/kokoro_cpu/kokoro.sh'
    add_rule "$HYPR_KEYBINDS" 'bindd = $mainMod SHIFT, I, STT Whisper CPU, exec, uwsm-app -- $scripts/tts_stt/faster_whisper/faster_whisper_stt.sh'
    add_rule "$HYPR_KEYBINDS" 'bindd = $mainMod, I, STT Parakeet GPU, exec, uwsm-app -- $scripts/tts_stt/parakeet/parakeet.sh'

    # --- 2. Input Configuration ---
    add_rule "$HYPR_INPUT" 'left_handed = true'
}

# --- UTILITIES ----------------------------------------------------------------

# Colors
BOLD=$'\033[1m'
GREEN=$'\033[32m'
BLUE=$'\033[34m'
YELLOW=$'\033[33m'
RED=$'\033[31m'
RESET=$'\033[0m'

log_info()    { printf "${BLUE}[INFO]${RESET} %s\n" "$1"; }
log_success() { printf "${GREEN}[OK]${RESET}   %s\n" "$1"; }
log_skip()    { printf "${YELLOW}[SKIP]${RESET} %s\n" "$1"; }
log_err()     { printf "${RED}[ERR]${RESET}  %s\n" "$1" >&2; }

# Escape strings for Regex usage (handles $, *, /, etc.)
escape_regex() {
    sed 's/[^^]/[&]/g; s/\^/\\^/g' <<< "$1"
}

# --- CORE LOGIC ---------------------------------------------------------------

# Action: Comment out lines (Disable ASUS features)
apply_tweaks() {
    log_info "Applying non-ASUS optimizations (Commenting lines)..."
    
    local total=${#RULES_FILES[@]}
    
    for (( i=0; i<total; i++ )); do
        local file="${RULES_FILES[$i]}"
        local pattern="${RULES_PATTERNS[$i]}"
        
        if [[ ! -f "$file" ]]; then
            log_err "File not found: $file"
            continue
        fi

        # Escape pattern for grep/sed
        local safe_pattern
        safe_pattern=$(escape_regex "$pattern")

        # Check if line exists and is active (start -> optional space -> pattern)
        if grep -qP "^\s*${safe_pattern}" "$file"; then
            # Capture leading whitespace (\1), add # and space, then restore pattern
            sed -i -E "s|^(\s*)(${safe_pattern})|\1# \2|" "$file"
            log_success "Commented: '$pattern' in $(basename "$file")"
        elif grep -qP "^\s*#\s*${safe_pattern}" "$file"; then
            log_skip "Already commented: $(basename "$file")"
        else
            log_skip "Pattern not found in: $(basename "$file")"
        fi
    done
}

# Action: Uncomment lines (Enable ASUS features)
restore_tweaks() {
    log_info "Restoring ASUS configuration (Uncommenting lines)..."

    local total=${#RULES_FILES[@]}

    for (( i=0; i<total; i++ )); do
        local file="${RULES_FILES[$i]}"
        local pattern="${RULES_PATTERNS[$i]}"
        
        if [[ ! -f "$file" ]]; then
            log_err "File not found: $file"
            continue
        fi

        local safe_pattern
        safe_pattern=$(escape_regex "$pattern")

        # Check if line is currently commented (start -> space -> # -> space -> pattern)
        if grep -qP "^\s*#\s*${safe_pattern}" "$file"; then
            # Capture whitespace (\1), match # + space, replace with whitespace + pattern
            sed -i -E "s|^(\s*)#\s*(${safe_pattern})|\1\2|" "$file"
            log_success "Restored: '$pattern' in $(basename "$file")"
        else
            log_skip "Not commented or missing: $(basename "$file")"
        fi
    done
}

# --- MAIN EXECUTION -----------------------------------------------------------

main() {
    # Initialize the rules array
    setup_rules

    # CLI Flags for non-interactive use
    if [[ "${1:-}" == "--auto" ]]; then
        apply_tweaks
        exit 0
    elif [[ "${1:-}" == "--restore" ]] || [[ "${1:-}" == "--reset" ]]; then
        restore_tweaks
        exit 0
    fi

    # --- Interactive Mode ---
    clear
    printf "${BOLD}========================================${RESET}\n"
    printf "${BOLD}   Hardware Configuration Manager       ${RESET}\n"
    printf "${BOLD}========================================${RESET}\n\n"
    
    printf "This script helps manage configuration differences between\n"
    printf "ASUS TUF F15 laptops and other hardware.\n\n"
    
    printf "${BLUE}Question 1:${RESET}\n"
    printf "Is the machine you are currently running an ${BOLD}ASUS TUF F15${RESET}?\n"
    printf " > [y] Yes, this IS an Asus TUF F15.\n"
    printf " > [n] No, this is DIFFERENT hardware.\n"
    # Defaults to 'n' if empty
    printf -v prompt_text "\nSelection [y/N]: "
    read -r -p "$prompt_text" is_asus
    is_asus=${is_asus:-n}

    if [[ "$is_asus" =~ ^([nN][oO]|[nN])$ ]]; then
        # === PATH: NON-ASUS HARDWARE (DISABLE CONFIGS) ===
        printf "\n${YELLOW}Configuration Intent:${RESET} You have indicated this is ${BOLD}NOT${RESET} an Asus TUF.\n"
        printf "The script will now ${RED}COMMENT OUT${RESET} (disable) Asus-specific drivers and keybinds.\n"
        printf "This ensures better compatibility with your current hardware.\n\n"
        
        # Double Confirmation - UPDATED: Defaults to Y if empty
        printf "${RED}Are you absolutely sure you want to disable these configs?${RESET} [Y/n] "
        read -r confirm
        confirm=${confirm:-y}
        
        if [[ "$confirm" =~ ^([yY][eE][sS]|[yY])$ ]]; then
            printf "\n"
            apply_tweaks
            printf "\n${GREEN}Success.${RESET} Optimizations for non-Asus hardware applied.\n"
        else
            printf "\n${YELLOW}Operation Cancelled.${RESET} No changes were made.\n"
            exit 0
        fi

    elif [[ "$is_asus" =~ ^([yY][eE][sS]|[yY])$ ]]; then
        # === PATH: ASUS HARDWARE (ENABLE CONFIGS) ===
        printf "\n${GREEN}Asus TUF Detected.${RESET}\n"
        printf "Usually, no action is needed if your config is already set up.\n"
        printf "However, if you previously disabled these settings, you can restore them now.\n\n"
        
        printf "Do you want to check and ${GREEN}UNCOMMENT (Restore)${RESET} all Asus configurations? [y/N] "
        read -r restore_confirm

        if [[ "$restore_confirm" =~ ^([yY][eE][sS]|[yY])$ ]]; then
            printf "\n"
            restore_tweaks
            printf "\n${GREEN}Success.${RESET} Asus configurations have been verified/restored.\n"
        else
            printf "\n${GREEN}OK.${RESET} Keeping current configuration as is.\n"
        fi
        
    else
        printf "\n${RED}Invalid input.${RESET} Please run the script again and select y or n.\n"
        exit 1
    fi
}

main "$@"
