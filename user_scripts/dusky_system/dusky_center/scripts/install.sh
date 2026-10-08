#!/usr/bin/env bash
# Install dusky-center atomically to ~/.local/share/dusky/dusky_center and ~/.local/bin/dusky-center.
set -euo pipefail

readonly PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly TARGET_BIN="${CARGO_TARGET_DIR:-/tmp/dusky-center-target-$UID}/release/dusky-center"
readonly INSTALL_DIR="$HOME/.local/share/dusky/dusky_center"
readonly LAUNCHER_DIR="$HOME/.local/bin"

if [[ ! -f "$TARGET_BIN" ]]; then
    printf 'Binary not found at %s. Running tmpfs release build first...\n' "$TARGET_BIN"
    "$PROJECT_DIR/scripts/build-on-tmpfs.sh" --release
fi

mkdir -p "$INSTALL_DIR" "$LAUNCHER_DIR"

# Install binary atomically
install -Dm755 "$TARGET_BIN" "$INSTALL_DIR/dusky-center.tmp"
mv -f "$INSTALL_DIR/dusky-center.tmp" "$INSTALL_DIR/dusky-center"

# Ensure config exists beside binary
if [[ ! -f "$INSTALL_DIR/dusky_config.toml" ]]; then
    cp "$PROJECT_DIR/dusky_config.toml" "$INSTALL_DIR/dusky_config.toml"
fi

# Create launcher symlink
ln -sf "$INSTALL_DIR/dusky-center" "$LAUNCHER_DIR/dusky-center"

printf 'Installed dusky-center successfully:\n'
printf '  Binary:   %s/dusky-center\n' "$INSTALL_DIR"
printf '  Launcher: %s/dusky-center\n' "$LAUNCHER_DIR"
