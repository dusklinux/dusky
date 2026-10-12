#!/usr/bin/env bash
# Install defaults and hooks on a fresh system; preserve every existing config/hook.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BIN_PATH="$HOME/.local/bin/dusky_proximity"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/dusky/settings/bluetooth_proximity"
USER_SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
TARGET_CONFIG="$CONFIG_DIR/config.toml"

"$SCRIPT_DIR/build.sh"
"$BIN_PATH" --config "$TARGET_CONFIG" --init-config
"$BIN_PATH" --config "$TARGET_CONFIG" --check-config
mkdir -p -- "$USER_SERVICE_DIR"
# %h and environment-based config lookup avoid hardcoded user/device paths.
install -m 644 -- "$SCRIPT_DIR/dusky_proximity.service" "$USER_SERVICE_DIR/dusky_proximity.service"
# Use the same config base in the manager as in this setup invocation.
if [[ -v XDG_CONFIG_HOME ]]; then
    systemctl --user import-environment XDG_CONFIG_HOME
else
    systemctl --user unset-environment XDG_CONFIG_HOME
fi
systemctl --user daemon-reload
systemctl --user enable dusky_proximity.service
systemctl --user restart dusky_proximity.service
printf 'Configuration: %s\nExecutable: %s\n' "$TARGET_CONFIG" "$BIN_PATH"
printf 'Fresh defaults are inactive until a device MAC is filled in and enabled in TOML.\n'
