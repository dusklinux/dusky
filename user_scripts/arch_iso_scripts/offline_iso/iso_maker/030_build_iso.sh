#!/usr/bin/env bash
# ==============================================================================
# 030_build_iso.sh - THE FACTORY ISO GENERATOR
# Architecture: Bypasses airootfs RAM exhaustion via dynamic mkarchiso patching.
# ==============================================================================
set -euo pipefail

# --- 1. CONFIGURATION ---
readonly ZRAM_DIR="/mnt/zram1/dusky_iso"
readonly PROFILE_DIR="${ZRAM_DIR}/profile"
readonly WORK_DIR="${ZRAM_DIR}/work"
readonly OUT_DIR="${ZRAM_DIR}/out"

# Repo Merge Paths
readonly OFFLINE_REPO_BASE="/srv/offline-repo"
readonly OFFLINE_REPO_OFFICIAL="${OFFLINE_REPO_BASE}/official"
readonly OFFLINE_REPO_AUR="${OFFLINE_REPO_BASE}/aur"

readonly MKARCHISO_CUSTOM="${ZRAM_DIR}/mkarchiso_dusky"
readonly PATCH_FILE="${ZRAM_DIR}/repo_inject.patch"

# Output Naming (Format: dusky_MM_YY.iso)
readonly FINAL_ISO_NAME="dusky_$(date +%m_%y).iso"

# --- 2. PRE-FLIGHT CHECKS ---
if (( EUID != 0 )); then
    echo "[INFO] Root required — re-launching under sudo..."
    exec sudo "$0" "$@"
fi

if [[ ! -d "${OFFLINE_REPO_OFFICIAL}" ]]; then
    echo "[ERR] Official offline repository not found at ${OFFLINE_REPO_OFFICIAL}!" >&2
    exit 1
fi

# Verify the injection point exists in the installed mkarchiso before we touch
# anything. This catches archiso upgrades that rename or refactor the function.
if ! grep -q '^_build_iso_image() {' /usr/bin/mkarchiso; then
    echo "[ERR] Could not locate '_build_iso_image() {' in /usr/bin/mkarchiso." >&2
    echo "[ERR] The archiso package may have been updated and renamed this function." >&2
    exit 1
fi

echo -e "\n\e[1;34m==>\e[0m \e[1mINITIATING DUSKY ARCH ISO FACTORY BUILD\e[0m\n"

# --- 3. DYNAMIC MKARCHISO PATCHING (The payload) ---
echo "  -> Cloning official mkarchiso..."
cp /usr/bin/mkarchiso "$MKARCHISO_CUSTOM"
chmod +x "$MKARCHISO_CUSTOM"

echo "  -> Generating injection patch..."
# We create a patch file to inject the repositories directly into the ISO staging
# area. This ensures the host system's /srv/offline-repo remains untouched.
cat << EOF > "$PATCH_FILE"
    _msg_info ">>> INJECTING & MERGING REPOSITORIES DIRECTLY INTO ISO <<<"
    local repo_target="\${isofs_dir}/\${install_dir}/repo"
    mkdir -p "\${repo_target}"
    
    # 1. Copy both repositories straight into the ISO's staging area (in ZRAM)
    cp -a "${OFFLINE_REPO_OFFICIAL}/." "\${repo_target}/"
    if [[ -d "${OFFLINE_REPO_AUR}" ]]; then
        cp -a "${OFFLINE_REPO_AUR}/." "\${repo_target}/"
    fi
    
    # 2. Clean out the individual databases that were just copied over
    rm -f "\${repo_target}/archrepo.db"*
    rm -f "\${repo_target}/archrepo.files"*
    
    _msg_info ">>> GENERATING MASTER DATABASE INSIDE ISO <<<"
    # 3. Filter out .sig files and generate the unified database
    shopt -s nullglob
    local all_files=("\${repo_target}/"*.pkg.tar.*)
    local pkg_files=()
    for f in "\${all_files[@]}"; do
        [[ "\$f" == *.sig ]] && continue
        pkg_files+=("\$f")
    done
    shopt -u nullglob
    
    if (( \${#pkg_files[@]} > 0 )); then
        repo-add -q "\${repo_target}/archrepo.db.tar.gz" "\${pkg_files[@]}"
    else
        echo "[ERR] No packages found to merge inside ISO!" >&2
        return 1
    fi
    
    _msg_info ">>> INJECTION COMPLETE <<<"
EOF

echo "  -> Splicing hook into mkarchiso pipeline..."
sed -i '/^_build_iso_image() {/r '"$PATCH_FILE"'' "$MKARCHISO_CUSTOM"

if ! grep -q 'INJECTING & MERGING REPOSITORIES DIRECTLY INTO ISO' "$MKARCHISO_CUSTOM"; then
    echo "[ERR] Patch was NOT injected — the sed pattern failed to match." >&2
    exit 1
fi
echo "  -> Patch verified successfully."

# --- 4. ISO GENERATION ---
echo "  -> Cleaning previous build artifacts..."
rm -rf "$WORK_DIR" "$OUT_DIR"

echo -e "\n\e[1;32m==>\e[0m \e[1mSTARTING BUILD PROCESS\e[0m"
"$MKARCHISO_CUSTOM" -v -m iso -w "$WORK_DIR" -o "$OUT_DIR" "$PROFILE_DIR"

# --- 5. ARTIFACT RENAMING ---
echo "  -> Renaming output to ${FINAL_ISO_NAME}..."
# mkarchiso generates exactly one .iso file in the clean output directory
mv "${OUT_DIR}"/*.iso "${OUT_DIR}/${FINAL_ISO_NAME}"

# --- 6. PERMISSIONS RESTORATION ---
# mkarchiso runs as root, resulting in root ownership of the output folder.
# We hand ownership back to the standard user who invoked sudo.
if [[ -n "${SUDO_USER:-}" ]]; then
    echo "  -> Restoring ownership of the output directory to user: $SUDO_USER..."
    chown -R "$SUDO_USER:$SUDO_USER" "$OUT_DIR"
fi

echo -e "\n\e[1;32m[SUCCESS]\e[0m \e[1mISO generation complete!\e[0m"
echo "Your bootable ISO is located at: ${OUT_DIR}/${FINAL_ISO_NAME}"
