#!/usr/bin/env bash
# ==============================================================================
# VS Code DMS Theme Installer
# ==============================================================================
# Description: Installs the DankMaterialShell VS Code theme extension
#              and ensures matugen templates are properly configured.
#
# Usage:
#   ./setup_dms_theme.sh
# ==============================================================================

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log()     { printf "${BLUE}::${NC} %s\n" "$*"; }
success() { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn()    { printf "${YELLOW}⚠${NC} %s\n" "$*" >&2; }
die()     { printf "${RED}✗${NC} %s\n" "$*" >&2; exit 1; }

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXTENSION_SRC="${SCRIPT_DIR}/dms-theme"
EXTENSION_DST="${HOME}/.vscode/extensions/danklinux.dms-theme-0.0.3"
MATUGEN_TEMPLATES="${HOME}/.config/matugen/templates"

log "Installing DMS Theme for VS Code..."

# Check if source extension exists
if [[ ! -d "$EXTENSION_SRC" ]]; then
    die "Extension source not found at: $EXTENSION_SRC"
fi

# Create VS Code extensions directory if needed
mkdir -p "${HOME}/.vscode/extensions"

# Remove existing symlink or directory
if [[ -L "$EXTENSION_DST" ]]; then
    log "Removing existing symlink..."
    rm -f "$EXTENSION_DST"
elif [[ -d "$EXTENSION_DST" ]]; then
    warn "Extension directory already exists at $EXTENSION_DST"
    warn "Backing up to ${EXTENSION_DST}.backup"
    mv "$EXTENSION_DST" "${EXTENSION_DST}.backup"
fi

# Create symlink
log "Creating symlink: $EXTENSION_SRC → $EXTENSION_DST"
ln -nfs "$EXTENSION_SRC" "$EXTENSION_DST"
success "Extension symlink created"

# Verify matugen templates exist
if [[ ! -d "$MATUGEN_TEMPLATES" ]]; then
    die "Matugen templates directory not found: $MATUGEN_TEMPLATES"
fi

# Check if vscode theme templates exist
if [[ -f "$MATUGEN_TEMPLATES/vscode-theme-dark.json" ]]; then
    success "Dark theme template found"
else
    warn "Dark theme template not found - will be created on first matugen run"
fi

if [[ -f "$MATUGEN_TEMPLATES/vscode-theme-light.json" ]]; then
    success "Light theme template found"
else
    warn "Light theme template not found - will be created on first matugen run"
fi

# Check if matugen config has vscode entries
if grep -q "vscode-theme-dark" "${HOME}/.config/matugen/config.toml"; then
    success "Matugen config already configured for VS Code themes"
else
    warn "Matugen config not configured for VS Code - please run theme_ctl or matugen"
fi

log "Checking if VS Code is installed..."
if command -v code &>/dev/null; then
    success "VS Code is installed"
    
    # Optional: reload vs code if running
    if pgrep -x "code" &>/dev/null; then
        log "VS Code is running - it will auto-detect the extension"
        log "Themes will auto-reload when you switch themes or restart VS Code"
    fi
else
    warn "VS Code not found in PATH - install it and run this script again"
fi

echo ""
success "DMS Theme installation complete!"
echo ""
echo "Next steps:"
echo "  1. If VS Code is running, go to: Settings → Workbench: Color Theme"
echo "  2. Select 'Dynamic Base16 DankShell (Dark)' or '(Light)'"
echo "  3. Run: matugen --mode dark image ~/Pictures/wallpapers/active_theme/wallpaper.png"
echo "  4. Theme will auto-update when colors change!"
echo ""
