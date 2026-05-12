#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# setup.sh — Install the sddm-sync wallpaper sync service.
#
# What this does:
#   - Copies sddm-sync script to /usr/local/bin/
#   - Copies systemd units to /etc/systemd/system/
#   - Enables and starts sddm-sync.path so wallpaper syncs on every
#     theme change (triggered by Matugen's post_hook)
#
# Requires: sudo, systemd, dusky SDDM theme installed
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

(( EUID == 0 )) || { echo "Run with sudo." >&2; exit 1; }

echo "[1/4] Installing sddm-sync script..."
install -Dm755 "${SCRIPT_DIR}/sddm-sync" /usr/local/bin/sddm-sync

echo "[2/4] Installing systemd units..."
install -Dm644 "${SCRIPT_DIR}/sddm-sync.path"    /etc/systemd/system/sddm-sync.path
install -Dm644 "${SCRIPT_DIR}/sddm-sync.service" /etc/systemd/system/sddm-sync.service

echo "[3/4] Reloading systemd daemon..."
systemctl daemon-reload

echo "[4/4] Enabling and starting sddm-sync.path..."
systemctl enable --now sddm-sync.path

echo "Done. SDDM wallpaper will now sync automatically on every theme change."
