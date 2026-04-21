#!/usr/bin/env bash
# Removes installation scripts (*.sh) and the git repository from root (/) after using dusky arch iso installation method
# ==============================================================================
#  ARCH LINUX POST-INSTALL CLEANUP (ROOT ARTIFACTS)
# ==============================================================================
#  Removes installation scripts (*.sh) and the git repository from the root (/)
#  directory after a successful install.
#
#  Features:
#  - Smart detection of *.sh files at /
#  - Configurable repository target
#  - Interactive safeguards (Batch delete, Selective, or Abort)
#  - Autonomous execution via '--auto' flag
# ==============================================================================

# --- CONFIGURATION ---
# Change this variable if your git repo name changes in the future
readonly TARGET_REPO_NAME="dusky"
readonly ROOT_PATH="/"

# 1. Safety & Environment
set -o errexit   # Exit on error
set -o nounset   # Error on undefined vars
set -o pipefail  # Error if pipe fails

# 2. Colors (Matches Orchestra style)
if [[ -t 1 ]] && command -v tput &>/dev/null; then
    RED=$(tput setaf 1)
    GREEN=$(tput setaf 2)
    YELLOW=$(tput setaf 3)
    BLUE=$(tput setaf 4)
    BOLD=$(tput bold)
    RESET=$(tput sgr0)
else
    RED="" GREEN="" YELLOW="" BLUE="" BOLD="" RESET=""
fi

# 3. Argument Parsing
AUTO_MODE=false
for arg in "$@"; do
    if [[ "$arg" == "--auto" ]]; then
        AUTO_MODE=true
    fi
done

# 4. Sanity & Privilege Checks
if [[ $EUID -ne 0 ]]; then
   printf "%s[INFO] Root privileges required. Escalating via sudo...%s\n" "$YELLOW" "$RESET"
   exec sudo bash "$0" "$@"
fi

# Critical safety net: Prevent accidental root wipe if variable is empty or spaces
if [[ -z "${TARGET_REPO_NAME// /}" ]]; then
    printf "%sERROR: TARGET_REPO_NAME cannot be empty.%s\n" "$RED" "$RESET" >&2
    exit 1
fi

readonly REPO_PATH="${ROOT_PATH}${TARGET_REPO_NAME}"

main() {
    local artifacts=()

    printf "\n%s>>> POST-INSTALL CLEANUP SCANNER <<<%s\n" "$BOLD" "$RESET"

    # --- SCANNING PHASE ---
    printf "%s[INFO] Scanning root directory (/) for installation artifacts...%s\n" "$BLUE" "$RESET"

    # 1. Find all .sh files at exactly / depth (excluding directories)
    # Optimized using modern Bash mapfile instead of a while-read loop for better performance
    mapfile -d '' artifacts < <(find "$ROOT_PATH" -maxdepth 1 -type f -name "*.sh" -print0 | sort -z)

    # 2. Check for the specific git repository directory
    local repo_found=false
    if [[ -d "$REPO_PATH" ]]; then
        repo_found=true
    fi

    # --- REPORTING PHASE ---
    if [[ ${#artifacts[@]} -eq 0 ]] && [[ "$repo_found" == "false" ]]; then
        printf "%s[SUCCESS] No scripts or repository found at root. System is clean.%s\n" "$GREEN" "$RESET"
        exit 0
    fi

    printf "\n%sDETECTED ARTIFACTS:%s\n" "$YELLOW" "$RESET"
    
    # List Repo
    if [[ "$repo_found" == "true" ]]; then
        printf "  %s[DIR]  %s%s\n" "$RED" "$REPO_PATH" "$RESET"
    fi

    # List Scripts
    for item in "${artifacts[@]}"; do
        printf "  %s[FILE] %s%s\n" "$RED" "$item" "$RESET"
    done

    local user_choice=""

    # --- DECISION PHASE ---
    if [[ "$AUTO_MODE" == "true" ]]; then
        printf "\n%s[INFO] '--auto' flag detected. Bypassing interactive prompt.%s\n" "$YELLOW" "$RESET"
        user_choice="1"
    else
        printf "\n%s----------------------------------------------------------%s\n" "$BOLD" "$RESET"
        printf "Select an action:\n"
        printf "  %s[ENTER]%s Delete EVERYTHING listed above (Batch Clean)\n" "$BOLD" "$RESET"
        printf "  %s[2]%s     Select items one by one (Interactive)\n" "$BOLD" "$RESET"
        printf "  %s[3]%s     NO, keep everything (Exit)\n" "$BOLD" "$RESET"
        printf "%s----------------------------------------------------------%s\n" "$BOLD" "$RESET"

        read -r -p "Your choice: " user_choice
    fi

    # --- EXECUTION PHASE ---
    case "$user_choice" in
        ""|"1")
            # DELETE ALL
            printf "\n%s[RUN] Deleting all artifacts...%s\n" "$BOLD" "$RESET"
            
            # Remove Repo
            if [[ "$repo_found" == "true" ]]; then
                rm -rf -- "$REPO_PATH"
                printf "%s  -> Deleted: %s%s\n" "$GREEN" "$REPO_PATH" "$RESET"
            fi
            
            # Remove Scripts
            for item in "${artifacts[@]}"; do
                rm -f -- "$item"
                printf "%s  -> Deleted: %s%s\n" "$GREEN" "$item" "$RESET"
            done
            ;;

        "2")
            # SELECTIVE DELETE
            printf "\n%s[RUN] Interactive Mode:%s\n" "$BOLD" "$RESET"
            
            # Ask for Repo
            if [[ "$repo_found" == "true" ]]; then
                read -r -p "Delete directory '${REPO_PATH}'? [y/N]: " sub_choice
                if [[ "${sub_choice,,}" == "y" ]]; then
                    rm -rf -- "$REPO_PATH"
                    printf "%s  -> Deleted: %s%s\n" "$GREEN" "$REPO_PATH" "$RESET"
                else
                    printf "%s  -> Skipped: %s%s\n" "$YELLOW" "$REPO_PATH" "$RESET"
                fi
            fi

            # Ask for Scripts
            for item in "${artifacts[@]}"; do
                read -r -p "Delete file '${item}'? [y/N]: " sub_choice
                if [[ "${sub_choice,,}" == "y" ]]; then
                    rm -f -- "$item"
                    printf "%s  -> Deleted: %s%s\n" "$GREEN" "$item" "$RESET"
                else
                    printf "%s  -> Skipped: %s%s\n" "$YELLOW" "$item" "$RESET"
                fi
            done
            ;;

        *)
            # CANCEL
            printf "\n%s[INFO] No changes made. Exiting.%s\n" "$YELLOW" "$RESET"
            exit 0
            ;;
    esac

    printf "\n%s[SUCCESS] Cleanup operations completed.%s\n" "$GREEN" "$RESET"
}

main "$@"
