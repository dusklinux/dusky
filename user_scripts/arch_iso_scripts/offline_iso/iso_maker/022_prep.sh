#!/usr/bin/env bash

# Enforce strict error handling, uninitialized variable detection, and pipeline safety
set -euo pipefail

# --- Constants & Configuration ---
readonly WORKSPACE="/mnt/zram1/dusky_iso"
readonly OFFLINE_REPO="/srv/offline-repo"
readonly SOURCE_DIR="${HOME}/user_scripts/arch_iso_scripts/offline_iso"
# Chained command for precise, one-click execution from any working directory
readonly RUN_CMD="cd \"${WORKSPACE}\" && sudo ./030_build_iso.sh"

echo -e "\n[>>>] INITIATING DUSKY ISO STAGING SEQUENCE [<<<]\n"

# --- Phase 0: Forensic Path Safety & Sanitization ---
# Critical safeguard: Prevent catastrophic rm -rf on empty or root variables
if [[ -z "${WORKSPACE}" || "${WORKSPACE}" == "/" ]]; then
    echo "[!] FATAL: Workspace variable is unsafe (${WORKSPACE}). Aborting."
    exit 1
fi

if [[ -d "${WORKSPACE}" ]]; then
    echo "[!] Existing workspace detected at ${WORKSPACE}."
    echo "[*] Executing elevated purge for guaranteed idempotency..."
    sudo rm -rf "${WORKSPACE}"
fi

echo "[*] Verifying payload integrity in offline repositories..."
if [[ ! -d "${OFFLINE_REPO}/official" ]] || [[ ! -d "${OFFLINE_REPO}/aur" ]]; then
    echo "[!] FATAL: Offline repository directories missing at ${OFFLINE_REPO}."
    exit 1
fi

echo "      Official directory object count: $(ls -lah "${OFFLINE_REPO}/official/" | wc -l)"
echo "      AUR directory object count:      $(ls -lah "${OFFLINE_REPO}/aur/" | wc -l)"

# --- Phase 1: Dependency Resolution ---
echo -e "\n[*] Enforcing archiso dependency..."
sudo pacman -Sy --needed --noconfirm archiso

# --- Phase 2: ZRAM Clean Room Setup ---
echo "[*] Constructing ZRAM workspace architecture..."
mkdir -p "${WORKSPACE}"

echo "[*] Cloning 'releng' blueprint..."
cp -r /usr/share/archiso/configs/releng "${WORKSPACE}/profile"

echo "[*] Injecting airootfs staging directory..."
mkdir -p "${WORKSPACE}/profile/airootfs/root/arch_install"

# --- Phase 3: Payload Delivery ---
echo "[*] Staging orchestration scripts and configurations..."
# Temporarily enable dotglob to capture dotfiles cleanly
shopt -s dotglob nullglob
cp -a "${SOURCE_DIR}/"* "${WORKSPACE}/profile/airootfs/root/arch_install/"
shopt -u dotglob nullglob

echo "[*] Positioning master factory script..."
cp -a "${SOURCE_DIR}/iso_maker/030_build_iso.sh" "${WORKSPACE}/"

# --- Phase 4: Execution Deferral & Clipboard Integration ---
echo -e "\n[*] Preparing master script for execution..."
chmod +x "${WORKSPACE}/030_build_iso.sh"

echo -e "\n[!] STAGING COMPLETE. Execution deferred to manual intervention."
echo "[*] Pushing chained execution command to clipboard..."

if command -v wl-copy >/dev/null 2>&1; then
    echo -n "${RUN_CMD}" | wl-copy
    echo "[+] Command copied via wl-copy (Wayland natively supported)."
elif command -v xclip >/dev/null 2>&1; then
    echo -n "${RUN_CMD}" | xclip -selection clipboard
    echo "[+] Command copied via xclip."
else
    echo "[!] Clipboard utility not found. Manual copy required."
fi

echo -e "\n[>] Paste and execute the following chained command when ready:\n"
echo -e "    ${RUN_CMD}\n"
