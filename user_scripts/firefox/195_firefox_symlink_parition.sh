#!/bin/bash
# Firefox Data Migration Utility for browser directory, so data is encrypted
#
# ==============================================================================
# Firefox Data Migration Utility
# ==============================================================================

# ------------------------------------------------------------------------------
# PRE-FLIGHT CHECKS & USER DETECTION
# ------------------------------------------------------------------------------

if [ "$EUID" -ne 0 ]; then
  echo "Error: Please run this script with sudo."
  exit 1
fi

if [ -z "$SUDO_USER" ]; then
    echo "Error: Could not detect the actual user. Do not run as root directly."
    exit 1
fi

REAL_USER="$SUDO_USER"
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
REAL_GROUP=$(id -gn "$REAL_USER")

# Visual formatting (ANSI-C Quoting)
RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'
NC=$'\033[0m'

echo -e "${YELLOW}:: Firefox Data Migration Tool initialized.${NC}"
echo -e "Target User: ${GREEN}$REAL_USER${NC}"
echo -e "Target Home: ${GREEN}$REAL_HOME${NC}"

# ------------------------------------------------------------------------------
# DESCRIPTION & CONTEXT
# ------------------------------------------------------------------------------
echo -e "\n${BLUE}=== PURPOSE ===${NC}"
echo -e "This utility migrates your Firefox data to a separate dedicated partition."
echo -e "Recommended Setup: A ~1GB partition mounted at /mnt/browser."
echo -e "${YELLOW}Security Benefit:${NC} This partition should be encrypted (LUKS) to protect"
echo -e "cookies and passwords even if the computer is stolen or logged in."

echo -e "\n${BLUE}=== HOW TO MANAGE THE DRIVE ===${NC}"
echo -e "1. ${GREEN}Auto-Mounting:${NC} Add the partition's UUID to ${YELLOW}/etc/fstab${NC} so it"
echo -e "   automatically mounts to ${YELLOW}/mnt/browser${NC} immediately upon unlocking."

echo -e "2. ${GREEN}Easy Unlocking:${NC} A powerful helper script is located at:"
echo -e "   ${YELLOW}nvim ~/user_scripts/drives/drive_manager.sh${NC}"
echo -e "   You must configure this file by removing the example UUIDs and adding"
echo -e "   your own (both the 'Locked' outer UUID and 'Unlocked' inner UUID)."

echo -e "3. ${GREEN}Usage:${NC} Once configured, you can simply run the alias:"
echo -e "   ${GREEN}unlock browser${NC}"
echo -e "   This works for BTRFS, NTFS, or EXT4 and handles all mounting logic.\n"

# ------------------------------------------------------------------------------
# STEP 1: Interactive Prompts (Fixed for Orchestra Integration)
# ------------------------------------------------------------------------------

# FORCE read from /dev/tty to bypass Orchestra logging pipes
# Defaults to No (N) if user presses Enter

read -p "Do you have a dedicated partition for browser files mounted at /mnt/browser? (y/N): " partition_confirm < /dev/tty
partition_confirm=${partition_confirm:-N} # Set default to N

if [[ ! "$partition_confirm" =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}:: User declined (Default: No). SKIPPING Firefox migration.${NC}"
    # Exit 0 ensures ORCHESTRA continues to script #032
    exit 0
fi

# Check for directory existence
if [ ! -d "/mnt/browser" ]; then
    echo -e "${RED}Error: /mnt/browser directory not found.${NC}"
    echo -e "${YELLOW}:: SKIPPING Firefox migration to prevent errors.${NC}"
    exit 0
fi

# Check if it is actually a mountpoint
if ! mountpoint -q /mnt/browser; then
    echo -e "${RED}Error: /mnt/browser exists but is NOT a mounted partition.${NC}"
    echo -e "${YELLOW}:: Please mount your encrypted partition first. SKIPPING migration.${NC}"
    exit 0
fi

# Automatically check for existing data
echo -e "${YELLOW}:: Checking for existing data in /mnt/browser...${NC}"
if [ -d "/mnt/browser/.mozilla" ]; then
    echo -e "${GREEN}:: Found existing .mozilla directory. Linking to existing data.${NC}"
else
    echo -e "${GREEN}:: No data found. Will create new directory structure.${NC}"
fi

echo -e "${RED}WARNING: Starting destructive operations on $REAL_HOME/.mozilla...${NC}"
read -p "Press [Enter] to execute or Ctrl+C to cancel." < /dev/tty

# ------------------------------------------------------------------------------
# STEP 2: Execution
# ------------------------------------------------------------------------------

# 1. Wipe local data
echo -e "${YELLOW}:: Wiping local Firefox data...${NC}"
rm -rf "$REAL_HOME/.mozilla" "$REAL_HOME/.cache/mozilla"

# 2. Create/Ensure target directory on mount
echo -e "${YELLOW}:: Ensuring target directory exists on mount...${NC}"
mkdir -p /mnt/browser/.mozilla

# 3. Fix Ownership (Recursive)
echo -e "${YELLOW}:: Setting ownership permissions on /mnt/browser/.mozilla...${NC}"
chown -R "$REAL_USER":"$REAL_GROUP" /mnt/browser/.mozilla

# 4. Create the symbolic link
echo -e "${YELLOW}:: Linking /mnt/browser/.mozilla to $REAL_HOME/.mozilla...${NC}"
ln -nfs /mnt/browser/.mozilla "$REAL_HOME/.mozilla"

# 5. Fix Symlink Ownership
chown -h "$REAL_USER":"$REAL_GROUP" "$REAL_HOME/.mozilla"

# ------------------------------------------------------------------------------
# Completion
# ------------------------------------------------------------------------------
echo -e "${GREEN}:: Firefox migration complete.${NC}"
