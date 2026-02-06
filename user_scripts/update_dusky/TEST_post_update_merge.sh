#!/usr/bin/env bash
# ==============================================================================
# POST-UPDATE HELPER: Compare and merge local changes after dusky update
# ==============================================================================
# Usage: Run this AFTER ~/user_scripts/update_dusky/update_dusky.sh
# ==============================================================================

set -euo pipefail

# ANSI Colors
readonly C_RED=$'\e[31m'
readonly C_GREEN=$'\e[32m'
readonly C_YELLOW=$'\e[33m'
readonly C_BLUE=$'\e[34m'
readonly C_CYAN=$'\e[36m'
readonly C_MAGENTA=$'\e[35m'
readonly C_BOLD=$'\e[1m'
readonly C_RESET=$'\e[0m'

# Paths
readonly GIT_DIR="${HOME}/dusky"
readonly WORK_TREE="${HOME}"
readonly BACKUP_BASE="${HOME}/Documents/dusky_update_backups"

# Git command
GIT_CMD=(git --git-dir="$GIT_DIR" --work-tree="$WORK_TREE")

# ==============================================================================
# FUNCTIONS
# ==============================================================================

print_header() {
    printf '\n%s%s%s\n' "$C_CYAN" "$1" "$C_RESET"
    printf '%s\n' "$(printf '=%.0s' {1..80})"
}

print_section() {
    printf '\n%s%s%s\n' "$C_BOLD" "$1" "$C_RESET"
}

# ==============================================================================
# MAIN
# ==============================================================================

print_header "DUSKY POST-UPDATE MERGE HELPER"

# Find the most recent backup
if [[ ! -d "$BACKUP_BASE" ]]; then
    printf '%s[ERROR]%s No backups found. Did you run pre_update_check.sh first?\n' "$C_RED" "$C_RESET" >&2
    exit 1
fi

LATEST_BACKUP=$(find "$BACKUP_BASE" -maxdepth 1 -type d -name "2*" 2>/dev/null | sort -r | head -n1)

if [[ -z "$LATEST_BACKUP" ]]; then
    printf '%s[ERROR]%s No backup directories found in %s\n' "$C_RED" "$C_RESET" "$BACKUP_BASE" >&2
    exit 1
fi

printf 'Using backup from: %s%s%s\n' "$C_BLUE" "$LATEST_BACKUP" "$C_RESET"

# Load metadata
if [[ -f "${LATEST_BACKUP}/metadata.sh" ]]; then
    source "${LATEST_BACKUP}/metadata.sh"
    printf 'Backed up %s%d%s files at %s%s%s\n' "$C_YELLOW" "$FILE_COUNT" "$C_RESET" "$C_CYAN" "$TIMESTAMP" "$C_RESET"
fi

# Read modified files list
if [[ ! -f "${LATEST_BACKUP}/modified_files.txt" ]]; then
    printf '%s[ERROR]%s Modified files list not found in backup\n' "$C_RED" "$C_RESET" >&2
    exit 1
fi

mapfile -t MODIFIED_FILES < "${LATEST_BACKUP}/modified_files.txt"

# Analyze each file
print_section "Analyzing Changes..."

declare -a unchanged_files=()
declare -a upstream_changed=()
declare -a user_only_changed=()
declare -a both_changed=()

for file in "${MODIFIED_FILES[@]}"; do
    [[ -z "$file" ]] && continue

    backup_file="${LATEST_BACKUP}/${file}"
    current_file="${WORK_TREE}/${file}"

    # Check if file exists in backup
    if [[ ! -f "$backup_file" ]]; then
        continue
    fi

    # Check if current file exists
    if [[ ! -f "$current_file" ]]; then
        user_only_changed+=("$file [DELETED UPSTREAM]")
        continue
    fi

    # Compare backup with current
    if diff -q "$backup_file" "$current_file" > /dev/null 2>&1; then
        # Files are identical - your changes were preserved or upstream didn't change
        unchanged_files+=("$file")
    else
        # Files differ - check if upstream changed
        # Get the version from before the update (from git history)
        old_upstream=$("${GIT_CMD[@]}" show "HEAD@{1}:${file}" 2>/dev/null || echo "")
        new_upstream=$("${GIT_CMD[@]}" show "HEAD:${file}" 2>/dev/null || echo "")

        if [[ "$old_upstream" != "$new_upstream" ]]; then
            # Upstream changed
            both_changed+=("$file")
        else
            # Only user changed (stash was applied successfully)
            user_only_changed+=("$file")
        fi
    fi
done

# Display results
print_header "ANALYSIS RESULTS"

if [[ ${#unchanged_files[@]} -gt 0 ]]; then
    printf '\n%s✓ Unchanged (%d files):%s\n' "$C_GREEN" "${#unchanged_files[@]}" "$C_RESET"
    printf '%sYour changes were preserved (or upstream didn't change these files)%s\n' "$C_GREEN" "$C_RESET"
    for file in "${unchanged_files[@]}"; do
        printf '  • %s\n' "$file"
    done
fi

if [[ ${#user_only_changed[@]} -gt 0 ]]; then
    printf '\n%s⚠ Changed After Update (%d files):%s\n' "$C_YELLOW" "${#user_only_changed[@]}" "$C_RESET"
    printf '%sThese files changed during the update (likely your stash was applied)%s\n' "$C_YELLOW" "$C_RESET"
    for file in "${user_only_changed[@]}"; do
        printf '  • %s\n' "$file"
    done
fi

if [[ ${#both_changed[@]} -gt 0 ]]; then
    printf '\n%s⚠ CONFLICTS (%d files):%s\n' "$C_RED" "${#both_changed[@]}" "$C_RESET"
    printf '%sBoth you AND upstream modified these files - may need manual merge%s\n' "$C_RED" "$C_RESET"
    for file in "${both_changed[@]}"; do
        printf '  • %s\n' "$file"
    done
fi

# Interactive merge for conflicting files
if [[ ${#both_changed[@]} -gt 0 ]]; then
    print_section "Conflict Resolution"

    for file in "${both_changed[@]}"; do
        printf '\n%s━━━ %s ━━━%s\n' "$C_MAGENTA" "$file" "$C_RESET"

        backup_file="${LATEST_BACKUP}/${file}"
        current_file="${WORK_TREE}/${file}"

        printf '\n%sWhat changed upstream:%s\n' "$C_YELLOW" "$C_RESET"
        "${GIT_CMD[@]}" diff "HEAD@{1}:${file}" "HEAD:${file}" 2>/dev/null || printf '%s[Unable to show upstream diff]%s\n' "$C_RED" "$C_RESET"

        printf '\n%sYour changes (backed up version vs current):%s\n' "$C_YELLOW" "$C_RESET"
        diff -u "$backup_file" "$current_file" 2>/dev/null || printf '%s[Files are different]%s\n' "$C_RED" "$C_RESET"

        printf '\n%sOptions:%s\n' "$C_BOLD" "$C_RESET"
        printf '  1. Keep current (from update, your stash may have been applied)\n'
        printf '  2. Restore your version (overwrite with backup)\n'
        printf '  3. Open 3-way merge editor (if available)\n'
        printf '  4. Skip (decide later)\n'
        printf '\nChoice [1-4, default: 4]: '

        read -r choice
        choice="${choice:-4}"

        case "$choice" in
            1)
                printf '%s✓ Keeping current version%s\n' "$C_GREEN" "$C_RESET"
                ;;
            2)
                cp "$backup_file" "$current_file"
                printf '%s✓ Restored your version from backup%s\n' "$C_GREEN" "$C_RESET"
                ;;
            3)
                if command -v vimdiff &>/dev/null; then
                    upstream_old=$(mktemp)
                    upstream_new=$(mktemp)
                    "${GIT_CMD[@]}" show "HEAD@{1}:${file}" > "$upstream_old" 2>/dev/null || true
                    "${GIT_CMD[@]}" show "HEAD:${file}" > "$upstream_new" 2>/dev/null || true

                    printf '%sOpening vimdiff...%s\n' "$C_CYAN" "$C_RESET"
                    vimdiff "$backup_file" "$current_file" "$upstream_new"

                    rm -f "$upstream_old" "$upstream_new"
                else
                    printf '%s[ERROR]%s vimdiff not found. Install vim for 3-way merge.%s\n' "$C_RED" "$C_RESET"
                fi
                ;;
            4)
                printf '%sSkipped - backup available at:%s\n' "$C_YELLOW" "$C_RESET"
                printf '  %s\n' "$backup_file"
                ;;
            *)
                printf '%s[Invalid choice]%s Skipping...\n' "$C_RED" "$C_RESET"
                ;;
        esac
    done
fi

# Final summary
print_header "SUMMARY"

printf '\n%sBackup location:%s %s\n' "$C_BLUE" "$C_RESET" "$LATEST_BACKUP"
printf '\n%sYou can manually compare files using:%s\n' "$C_CYAN" "$C_RESET"
printf '  diff -u %s/<file> ~/<file>\n' "$LATEST_BACKUP"

printf '\n%sTo restore any file from backup:%s\n' "$C_CYAN" "$C_RESET"
printf '  cp %s/<file> ~/<file>\n' "$LATEST_BACKUP"

printf '\n%sDone!%s\n\n' "$C_GREEN" "$C_RESET"
