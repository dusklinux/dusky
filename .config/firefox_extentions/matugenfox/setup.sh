#!/usr/bin/env bash

# =====================================================================
# 🦊 MatugenFox Native Host Setup
# =====================================================================
# Automatically detects all supported Firefox-based browsers and installs
# the native messaging host manifest into each.
# =====================================================================

set -e

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# --- Globals ---
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
HOST_PATH="$SCRIPT_DIR/matugenfox_host.py"
MANIFEST_NAME="matugenfox.json"
INSTALLED=0

# --- Functions ---
print_step() { echo -e "${BLUE}==>${NC} ${1}"; }
print_success() { echo -e "${GREEN}✓${NC} ${1}"; }
print_warning() { echo -e "${YELLOW}⚠${NC} ${1}"; }
print_error() { echo -e "${RED}❌ Error:${NC} ${1}"; exit 1; }

echo -e "\n${CYAN}🦊 MatugenFox Setup script${NC}\n"

# 1. Pre-flight checks
print_step "Performing system checks..."

if ! command -v python3 >/dev/null 2>&1; then
    print_error "Python 3 is required but not found. Please install Python 3."
fi

if [ ! -f "$HOST_PATH" ]; then
    print_error "Host script not found at $HOST_PATH"
fi

print_success "System dependencies met."

# 2. Make host executable
print_step "Securing host script permissions..."
chmod +x "$HOST_PATH"
print_success "Host script is now executable."

# 3. Detect browsers
print_step "Detecting supported Firefox-based browsers..."
TARGETS=()

if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    [ -d "$HOME/.mozilla" ] && TARGETS+=("$HOME/.mozilla/native-messaging-hosts")
    [ -d "$HOME/.librewolf" ] && TARGETS+=("$HOME/.librewolf/native-messaging-hosts")
    [ -d "$HOME/.var/app/org.mozilla.firefox/.mozilla" ] && TARGETS+=("$HOME/.var/app/org.mozilla.firefox/.mozilla/native-messaging-hosts")
    [ -d "$HOME/.var/app/io.gitlab.librewolf-community/.librewolf" ] && TARGETS+=("$HOME/.var/app/io.gitlab.librewolf-community/.librewolf/native-messaging-hosts")
    [ -d "$HOME/.waterfox" ] && TARGETS+=("$HOME/.waterfox/native-messaging-hosts")
    [ -d "$HOME/.floorp" ] && TARGETS+=("$HOME/.floorp/native-messaging-hosts")
    [ -d "$HOME/.zen" ] && TARGETS+=("$HOME/.zen/native-messaging-hosts")
    [ -d "$HOME/.firedragon" ] && TARGETS+=("$HOME/.firedragon/native-messaging-hosts")
elif [[ "$OSTYPE" == "darwin"* ]]; then
    [ -d "$HOME/Library/Application Support/Mozilla" ] && TARGETS+=("$HOME/Library/Application Support/Mozilla/NativeMessagingHosts")
    [ -d "$HOME/Library/Application Support/LibreWolf" ] && TARGETS+=("$HOME/Library/Application Support/LibreWolf/NativeMessagingHosts")
else
    print_error "Unsupported OS: $OSTYPE"
fi

if [ ${#TARGETS[@]} -eq 0 ]; then
    print_error "No supported browsers detected. Please install Firefox or LibreWolf."
fi

# 4. Install manifest
print_step "Installing native messaging manifests..."
for TARGET_DIR in "${TARGETS[@]}"; do
    mkdir -p "$TARGET_DIR"
    
    cat <<EOF > "$TARGET_DIR/$MANIFEST_NAME"
{
  "name": "matugenfox",
  "description": "MatugenFox Native Messaging Host",
  "path": "$HOST_PATH",
  "type": "stdio",
  "allowed_extensions": [
    "matugenfox@ubaid.com"
  ]
}
EOF
    print_success "Manifest installed in: $TARGET_DIR"
    INSTALLED=$((INSTALLED + 1))
done



echo -e "\n${GREEN}✅ Setup Complete! MatugenFox was installed into $INSTALLED browser(s).${NC}"
echo -e "------------------------------------------------------------------"
echo -e "${CYAN}1.${NC} Load or Restart the extension in Firefox (about:debugging)."
echo -e "${CYAN}2.${NC} Make sure Matugen is configured to output to firefox_websites.css"
echo -e "${CYAN}3.${NC} Open the MatugenFox popup and click 'Fetch Colors'."
echo -e "------------------------------------------------------------------\n"
