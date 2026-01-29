#!/usr/bin/env bash
# ==============================================================================
#  DUSKY GIT RECOVERY TOOL
#  Description: Forces a resync with GitHub to fix missing files/ghost states.
#               Safely stashes user changes before resetting.
#  Context:     Arch Linux / Bash 5+
# ==============================================================================

set -euo pipefail

# ------------------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------------------
readonly GIT_BIN="/usr/bin/git"
readonly DOTFILES_GIT_DIR="${HOME}/dusky"
readonly WORK_TREE="${HOME}"
readonly BACKUP_NAME="recovery-backup-$(date +%s)"
readonly BRANCH="main"
# This is the Source of Truth. We enforce this URL.
readonly REPO_URL="https://github.com/T3rr0or/dusky"

# ------------------------------------------------------------------------------
# VISUALS & LOGGING
# ------------------------------------------------------------------------------
if [[ -t 1 ]]; then
    readonly CLR_RED=$'\e[1;31m'
    readonly CLR_GRN=$'\e[1;32m'
    readonly CLR_YLW=$'\e[1;33m'
    readonly CLR_BLU=$'\e[1;34m'
    readonly CLR_RST=$'\e[0m'
else
    readonly CLR_RED='' CLR_GRN='' CLR_YLW='' CLR_BLU='' CLR_RST=''
fi

log()  { printf '%s[%s]%s %s\n' "$CLR_BLU" "$1" "$CLR_RST" "$2"; }
warn() { printf '%s[WARN]%s %s\n' "$CLR_YLW" "$CLR_RST" "$1" >&2; }
err()  { printf '%s[ERR]%s  %s\n' "$CLR_RED" "$CLR_RST" "$1" >&2; }

# ------------------------------------------------------------------------------
# GIT WRAPPER & STATE
# ------------------------------------------------------------------------------
dotgit() {
    "$GIT_BIN" --git-dir="$DOTFILES_GIT_DIR" --work-tree="$WORK_TREE" "$@"
}

STASH_NEEDED=false

# ------------------------------------------------------------------------------
# SAFETY TRAP
# ------------------------------------------------------------------------------
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 && "$STASH_NEEDED" == true ]]; then
        printf '\n' >&2
        warn "Script exited unexpectedly!"
        warn "Your local changes are safely saved in the stash."
        warn "To see them:  $GIT_BIN --git-dir='$DOTFILES_GIT_DIR' --work-tree='$WORK_TREE' stash list"
        warn "To restore:   $GIT_BIN --git-dir='$DOTFILES_GIT_DIR' --work-tree='$WORK_TREE' stash pop"
    fi
}
trap cleanup EXIT

# ------------------------------------------------------------------------------
# PRE-FLIGHT CHECKS
# ------------------------------------------------------------------------------

# 0. Environment Sanity
if [[ -z "${HOME:-}" ]]; then
    err "HOME environment variable is not set."
    exit 1
fi

# 1. Git Binary Check
if [[ ! -x "$GIT_BIN" ]]; then
    err "Git not found or not executable at: $GIT_BIN"
    exit 1
fi

# 2. Interactive Check
if [[ ! -t 0 ]]; then
    err "This script requires an interactive terminal."
    exit 1
fi

# 3. Repo Existence Check
if [[ ! -d "$DOTFILES_GIT_DIR" ]]; then
    err "Repository directory not found at: $DOTFILES_GIT_DIR"
    exit 1
fi

# 4. Valid Repo Check (Look for HEAD)
if [[ ! -f "${DOTFILES_GIT_DIR}/HEAD" ]]; then
    err "Directory exists but is not a valid bare repository: $DOTFILES_GIT_DIR"
    exit 1
fi

# 5. Git Lock Check (Crucial for recovery)
if [[ -f "${DOTFILES_GIT_DIR}/index.lock" ]]; then
    err "A git process appears to be locked/hung (index.lock exists)."
    err "Run this command to clear it, then try again:"
    err "rm -f ${DOTFILES_GIT_DIR}/index.lock"
    exit 1
fi

clear
printf '%sDUSKY RECOVERY TOOL%s\n' "$CLR_BLU" "$CLR_RST"
printf 'This will force your system to sync with the latest GitHub version.\n'
printf 'Your local edits (keybinds, configs) will be saved and restored.\n\n'
read -r -p "Press [Enter] to start repair..." _

# ------------------------------------------------------------------------------
# STEP 1: SAFEGUARD USER DATA
# ------------------------------------------------------------------------------
log "STEP 1" "Checking for local changes..."

if ! dotgit diff-index --quiet HEAD --; then
    warn "Local changes detected. Stashing them now..."
    if dotgit stash push -m "$BACKUP_NAME"; then
        STASH_NEEDED=true
        log "OK" "Changes saved as: $BACKUP_NAME"
    else
        err "Could not stash changes. Aborting to prevent data loss."
        exit 1
    fi
else
    log "OK" "Working directory is clean. No backup needed."
fi

# ------------------------------------------------------------------------------
# STEP 2: REPAIR CONFIGURATION
# ------------------------------------------------------------------------------
log "STEP 2" "Applying git configuration fixes..."

# Fix 1: Silence the noisy log output
dotgit config status.showUntrackedFiles no

# Fix 2: Handle Sparse Checkout (CRITICAL FIX FOR set -e)
SC_STATUS=$(dotgit config --get core.sparseCheckout || echo "false")

if [[ "$SC_STATUS" == "true" ]]; then
    log "INFO" "Sparse-checkout detected. Whitelisting user_scripts..."
    if ! dotgit sparse-checkout add user_scripts 2>/dev/null; then
        warn "Could not update sparse-checkout config. Continuing anyway..."
    fi
else
    log "OK" "Standard checkout detected (safe)."
fi

# Fix 3: Ensure Remote URL is correct (The Fork Fix)
CURRENT_URL=$(dotgit remote get-url origin 2>/dev/null || echo "")
# Remove trailing .git for comparison if needed, but simple string check is usually enough
if [[ "$CURRENT_URL" != "$REPO_URL" ]]; then
    warn "Remote URL mismatch detected!"
    warn "Current: $CURRENT_URL"
    warn "Target:  $REPO_URL"
    log "FIX" "Updating remote 'origin' to official repository..."
    
    if dotgit remote set-url origin "$REPO_URL"; then
        log "OK" "Remote updated successfully."
    else
        err "Failed to update remote URL."
        exit 1
    fi
fi

# ------------------------------------------------------------------------------
# STEP 3: FORCE SYNCHRONIZATION
# ------------------------------------------------------------------------------
log "STEP 3" "Fetching latest data from GitHub..."

# We fetch explicitly to FETCH_HEAD to avoid ambiguity with local branch mappings
if ! dotgit fetch origin "$BRANCH"; then
    err "Failed to fetch from GitHub. Check your internet connection."
    exit 1
fi

log "STEP 3" "Forcing repository reset (Repairing missing files)..."

# CRITICAL FIX: Reset to FETCH_HEAD instead of origin/main
# origin/main might not exist if the refspec mapping is broken, but FETCH_HEAD
# is guaranteed to exist after a successful fetch.
if dotgit reset --hard FETCH_HEAD; then
    log "OK" "Repository is now identical to GitHub."
else
    err "Reset failed. Check file permissions in $WORK_TREE"
    exit 1
fi

# ------------------------------------------------------------------------------
# STEP 4: RESTORE USER DATA
# ------------------------------------------------------------------------------
if [[ "$STASH_NEEDED" == true ]]; then
    log "STEP 4" "Restoring your personal edits..."

    if dotgit stash pop; then
        log "SUCCESS" "Your changes have been reapplied!"
    else
        warn "Could not automatically restore changes (conflict or stash error)."
        warn "Your changes are NOT lost. They remain in the stash."
        warn "View changes: $GIT_BIN --git-dir='$DOTFILES_GIT_DIR' --work-tree='$WORK_TREE' stash show -p"
        warn "Apply later:  $GIT_BIN --git-dir='$DOTFILES_GIT_DIR' --work-tree='$WORK_TREE' stash pop"
        
        # We set this to false so the trap doesn't warn again on exit,
        # since we have already warned the user here.
        STASH_NEEDED=false
    fi
fi

printf '\n%s[DONE]%s Repair complete. You can now run the update script normally.\n' "$CLR_GRN" "$CLR_RST"
