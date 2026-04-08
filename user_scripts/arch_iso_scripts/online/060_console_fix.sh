#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# MODULE: PRE-INSTALL CONFIG (VCONSOLE)
# -----------------------------------------------------------------------------
set -euo pipefail

echo ">> configuring /mnt/etc/vconsole.conf..."

# Ensure directory exists (it should from disk mount, but safety first)
mkdir -p /mnt/etc

# Write config
echo "KEYMAP=us" > /mnt/etc/vconsole.conf

# Verify
if grep -q "KEYMAP=us" /mnt/etc/vconsole.conf; then
    echo "   [OK] vconsole.conf created."
else
    echo "   [ERR] Failed to create vconsole.conf"
    exit 1
fi
