#!/usr/bin/env bash
# Build locked dependencies, downloading missing crates; install atomically.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
TARGET_BIN_DIR="$HOME/.local/bin"
BUILD_DIR=$(mktemp -d "${TMPDIR:-/tmp}/dusky-proximity-build.XXXXXXXX")
INSTALL_TMP=''
cleanup() {
    rm -rf -- "$BUILD_DIR"
    [[ -z "$INSTALL_TMP" ]] || rm -f -- "$INSTALL_TMP"
}
trap cleanup EXIT
BUILD_HOST=$(rustc --print host-tuple)
# Reuse Cargo's normal download cache; --locked preserves Cargo.lock versions.
CARGO_TARGET_DIR="$BUILD_DIR/target" \
    cargo build --release --locked --target "$BUILD_HOST" --manifest-path "$SCRIPT_DIR/Cargo.toml"
mkdir -p -- "$TARGET_BIN_DIR"
INSTALL_TMP=$(mktemp "$TARGET_BIN_DIR/.dusky_proximity.XXXXXXXX")
install -m 755 -- "$BUILD_DIR/target/$BUILD_HOST/release/dusky_proximity" "$INSTALL_TMP"
# HCI_CHANNEL_CONTROL requires CAP_NET_ADMIN; hooks still execute as the user.
if (( EUID == 0 )); then
    setcap cap_net_admin=ep "$INSTALL_TMP"
else
    sudo setcap cap_net_admin=ep "$INSTALL_TMP"
fi
setcap -v cap_net_admin=ep "$INSTALL_TMP"
mv -fT -- "$INSTALL_TMP" "$TARGET_BIN_DIR/dusky_proximity"
INSTALL_TMP=''
printf 'Installed executable: %s\n' "$TARGET_BIN_DIR/dusky_proximity"
