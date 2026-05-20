#!/usr/bin/env bash
set -euo pipefail

C_RESET='\033[0m'; C_RED='\033[1;31m'; C_GREEN='\033[1;32m'; C_YELLOW='\033[1;33m'; C_CYAN='\033[1;36m'
log() { printf "${C_CYAN}::${C_RESET} %s\n" "$1"; }
ok()  { printf "${C_GREEN}ok${C_RESET}  %s\n" "$1"; }
warn() { printf "${C_YELLOW}warn${C_RESET} %s\n" "$1"; }
err() { printf "${C_RED}err${C_RESET}  %s\n" "$1"; }

REPO="$HOME/dusky"

# --- Detect ---
detect() {
    if [[ ! -d "$REPO" ]]; then
        echo "none"
        return
    fi
    local remote
    remote=$(git --git-dir="$REPO" remote get-url origin 2>/dev/null || true)
    case "$remote" in
        *dusker*)   echo "dusker" ;;
        *dusky*)    echo "dusky" ;;
        *)          echo "unknown" ;;
    esac
}

NAME=$(detect)
case "$NAME" in
    none)    echo "No dusky/dusker installation found."; exit 0 ;;
    unknown) echo "Found a bare repo but couldn't detect variant."; NAME="dusky/dusker" ;;
esac

echo ""
echo "  ┌────────────────────────────────────────────┐"
echo "  │      $NAME Uninstaller              │"
echo "  └────────────────────────────────────────────┘"
echo ""
echo "This will:"
echo "  · stop/disable systemd services"
echo "  · remove dbus activation files"
echo "  · remove desktop entries"
echo "  · remove the bare git repo ($REPO)"
echo "  · remove cache and state directories"
echo "  · list tracked files so you can clean up manually"
echo ""
echo "Your personal files outside the tracked list are NOT touched."
read -rp "Continue? [y/N] " confirm
[[ "$confirm" == [yY] ]] || exit 1

BACKUP="$HOME/dusker_uninstall_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP"

# --- 1. List tracked files ---
log "Saving list of tracked files..."
git --git-dir="$REPO" --work-tree="$HOME" ls-files > "$BACKUP/tracked_files.txt"
ok "saved to $BACKUP/tracked_files.txt"

# --- 2. Services ---
log "Stopping services..."
systemctl --user disable --now dusky.service 2>/dev/null && ok "dusky.service stopped" || warn "dusky.service not running"
systemctl --user disable --now com.github.dusky.controlcenter 2>/dev/null && ok "dusky control center stopped" || true
sudo systemctl disable --now input-remapper.service 2>/dev/null && ok "input-remapper.service stopped" || warn "input-remapper not found"

log "Removing service files..."
rm -f "$HOME/.config/systemd/user/dusky.service" && ok "service file removed" || true
rm -f "$HOME/.local/share/dbus-1/services/com.github.dusky.controlcenter.service" && ok "dbus service removed" || true

# --- 3. Desktop files ---
log "Removing desktop entries..."
count=0
for f in "$HOME/.local/share/applications/dusky_"*.desktop; do
    [[ -f "$f" ]] || continue
    rm -f "$f" && count=$((count + 1))
done
[[ "$count" -gt 0 ]] && ok "removed $count desktop entries" || warn "no dusky desktop entries found"

# --- 4. Config & cache ---
log "Removing config and cache..."
rm -rf "$HOME/.config/dusky"          && ok "state dir removed"   || true
rm -rf "$HOME/.config/dusky_sites"    && ok "dusky sites removed" || true
rm -rf "$HOME/.cache/duskycc"         && ok "cache removed"      || true
rm -rf "$HOME/.config/dusky_system"   && ok "system config removed" || true

# --- 5. Bare repo ---
log "Removing bare git repo..."
rm -rf "$REPO" && ok "repo removed" || err "could not remove $REPO"

# --- 6. Reload systemd ---
systemctl --user daemon-reload 2>/dev/null || true

echo ""
echo "  ┌────────────────────────────────────────────┐"
echo "  │  $NAME removed.                   │"
echo "  └────────────────────────────────────────────┘"
echo ""
echo "Tracked files were NOT deleted from your home."
echo "Review $BACKUP/tracked_files.txt and remove"
echo "any config files under ~/.config/ that you no longer need."
echo ""
echo "You may also want to delete ~/user_scripts/ if you"
echo "no longer need the scripts."
