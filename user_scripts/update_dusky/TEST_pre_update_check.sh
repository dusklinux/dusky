#!/usr/bin/env bash
# ==============================================================================
# PRE-UPDATE HELPER: Check and backup local changes before dusky update
# ==============================================================================
# Usage: Run this BEFORE ~/user_scripts/update_dusky/update_dusky.sh
# ==============================================================================

set -euo pipefail

# ANSI Colors
readonly C_RED=$'\e[31m'
readonly C_GREEN=$'\e[32m'
readonly C_YELLOW=$'\e[33m'
readonly C_BLUE=$'\e[34m'
readonly C_CYAN=$'\e[36m'
readonly C_BOLD=$'\e[1m'
readonly C_RESET=$'\e[0m'

# Paths
readonly GIT_DIR="${HOME}/dusky"
readonly WORK_TREE="${HOME}"
readonly BACKUP_BASE="${HOME}/Documents/dusky_update_backups"
readonly TIMESTAMP=$(date +%Y%m%d_%H%M%S)
readonly BACKUP_DIR="${BACKUP_BASE}/${TIMESTAMP}"

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

print_header "DUSKY PRE-UPDATE CHECK - ${TIMESTAMP}"

# Check if we're in a dusky repo
if [[ ! -d "$GIT_DIR" ]]; then
    printf '%s[ERROR]%s Dusky git directory not found: %s\n' "$C_RED" "$C_RESET" "$GIT_DIR" >&2
    exit 1
fi

# Get list of modified files
print_section "Checking for local changes..."

MODIFIED_FILES=$("${GIT_CMD[@]}" diff --name-only 2>/dev/null || true)

if [[ -z "$MODIFIED_FILES" ]]; then
    printf '%s✓ No local changes detected%s\n' "$C_GREEN" "$C_RESET"
    printf 'You can safely run the dusky update.\n'
    exit 0
fi

# Count files
FILE_COUNT=$(echo "$MODIFIED_FILES" | wc -l)
printf '%s⚠ Found %d modified file(s)%s\n\n' "$C_YELLOW" "$FILE_COUNT" "$C_RESET"

# Show modified files by category
print_section "Modified Files by Category:"

# Categorize files
declare -A categories
while IFS= read -r file; do
    if [[ "$file" =~ ^\.config/hypr/ ]]; then
        categories["Hyprland Config"]+="$file"$'\n'
    elif [[ "$file" =~ ^\.config/ ]]; then
        categories["Other Config"]+="$file"$'\n'
    elif [[ "$file" =~ ^user_scripts/ ]]; then
        categories["User Scripts"]+="$file"$'\n'
    elif [[ "$file" =~ ^\.local/share/applications/ ]]; then
        categories["Desktop Files"]+="$file"$'\n'
    else
        categories["Other"]+="$file"$'\n'
    fi
done <<< "$MODIFIED_FILES"

# Print categorized files
for category in "${!categories[@]}"; do
    printf '\n%s%s:%s\n' "$C_BLUE" "$category" "$C_RESET"
    echo "${categories[$category]}" | while IFS= read -r file; do
        [[ -z "$file" ]] && continue
        printf '  • %s\n' "$file"
    done
done

# Ask if user wants to see detailed diffs
printf '\n%sWould you like to see detailed diffs? [y/N]%s ' "$C_YELLOW" "$C_RESET"
read -r show_diff

if [[ "$show_diff" =~ ^[Yy]$ ]]; then
    print_section "Detailed Changes:"

    while IFS= read -r file; do
        [[ -z "$file" ]] && continue
        printf '\n%s━━━ %s ━━━%s\n' "$C_CYAN" "$file" "$C_RESET"
        "${GIT_CMD[@]}" diff --color=always "$file" 2>/dev/null || printf '%s[ERROR reading diff]%s\n' "$C_RED" "$C_RESET"
    done <<< "$MODIFIED_FILES"
fi

# Create backup
print_section "Creating Backup..."

if mkdir -p "$BACKUP_DIR" 2>/dev/null; then
    # Save file list
    echo "$MODIFIED_FILES" > "${BACKUP_DIR}/modified_files.txt"

    # Backup each file
    backup_count=0
    while IFS= read -r file; do
        [[ -z "$file" ]] && continue

        src="${WORK_TREE}/${file}"
        dest="${BACKUP_DIR}/${file}"

        if [[ -f "$src" ]]; then
            mkdir -p "$(dirname "$dest")" 2>/dev/null || true
            if cp -a "$src" "$dest" 2>/dev/null; then
                ((backup_count++))
            fi
        fi
    done <<< "$MODIFIED_FILES"

    # Save full diff
    "${GIT_CMD[@]}" diff > "${BACKUP_DIR}/full_diff.patch" 2>/dev/null || true

    printf '%s✓ Backed up %d file(s) to:%s\n' "$C_GREEN" "$backup_count" "$C_RESET"
    printf '  %s\n' "$BACKUP_DIR"
else
    printf '%s[ERROR]%s Failed to create backup directory\n' "$C_RED" "$C_RESET" >&2
    exit 1
fi

# Summary
print_header "SUMMARY"

printf '%s• Modified files:%s %d\n' "$C_YELLOW" "$C_RESET" "$FILE_COUNT"
printf '%s• Backup location:%s %s\n' "$C_GREEN" "$C_RESET" "$BACKUP_DIR"

printf '\n%sNext Steps:%s\n' "$C_BOLD" "$C_RESET"
printf '1. Review the changes above\n'
printf '2. Run the dusky update: %s~/user_scripts/update_dusky/update_dusky.sh%s\n' "$C_CYAN" "$C_RESET"
printf '3. After update, run: %s~/user_scripts/update_dusky/post_update_merge.sh%s\n' "$C_CYAN" "$C_RESET"

# Create metadata for post-update script
cat > "${BACKUP_DIR}/metadata.sh" <<EOF
# Metadata for post-update merge
TIMESTAMP="${TIMESTAMP}"
FILE_COUNT="${FILE_COUNT}"
BACKUP_DIR="${BACKUP_DIR}"
EOF

printf '\n'
