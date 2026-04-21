#!/usr/bin/env bash
# ==============================================================================
# 030_build_iso.sh - THE FACTORY ISO GENERATOR
# Architecture: Bypasses airootfs RAM exhaustion via dynamic mkarchiso patching.
# Payload: Injects and maps executable dotfiles directly into /etc/skel.
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

if ! grep -q '^_build_iso_image() {' /usr/bin/mkarchiso; then
    echo "[ERR] Could not locate '_build_iso_image() {' in /usr/bin/mkarchiso." >&2
    exit 1
fi

echo -e "\n\e[1;34m==>\e[0m \e[1mINITIATING DUSKY ARCH ISO FACTORY BUILD\e[0m\n"

# --- 3. LIVE ENVIRONMENT HOOKS (Auto-Start & SSH) ---
echo "  -> Configuring Auto-Start Payload and SSH Access..."
cat << 'EOF' > "${PROFILE_DIR}/airootfs/root/.automated_script.sh"
#!/usr/bin/env bash

if [[ "$(tty)" == "/dev/tty1" ]]; then
    echo "root:0000" | chpasswd
    echo -e "\e[1;32m[INFO]\e[0m Root password set to 0000. SSH is available."

    echo -e "\e[1;34m[INFO]\e[0m Waiting for background services to initialize..."
    systemctl is-system-running --wait >/dev/null 2>&1 || true

    chmod -R +x /root/arch_install/

    clear
    cd /root/arch_install/
    ./000_dusky_arch_install.sh
fi
EOF

chmod +x "${PROFILE_DIR}/airootfs/root/.automated_script.sh"

# --- 4. SKELETON DIRECTORY PAYLOAD (Dotfiles) ---
echo "  -> Preparing workspace for dotfiles (Enforcing Idempotency)..."
SKEL_DIR="${PROFILE_DIR}/airootfs/etc/skel"

# 1. Wipe the existing directory to prevent git 'already exists' fatal errors on rebuilds.
rm -rf "${SKEL_DIR}"
mkdir -p "${SKEL_DIR}"

# 2. Wipe previous permission injections to prevent profiledef.sh from bloating indefinitely.
sed -i '/^# --- DUSKY PERMISSIONS START ---/,/^# --- DUSKY PERMISSIONS END ---/d' "${PROFILE_DIR}/profiledef.sh"

# 3. Resolve Pacman Conflict: grml-zsh-config provides a default .zshrc that fatally 
# collides with the dusky git checkout. We strip it from the build list.
echo "  -> Pruning conflicting Archiso baseline packages..."
sed -i '/^grml-zsh-config$/d' "${PROFILE_DIR}/packages.x86_64" || true

echo "  -> Fetching and staging dotfiles payload into /etc/skel..."
# 4. Clone the bare repository natively into the skeleton dir.
git clone --bare --depth 1 "https://github.com/dusklinux/dusky" "${SKEL_DIR}/dusky"

# 5. Force checkout directly into /etc/skel so subdirectories manifest in the ISO.
git --git-dir="${SKEL_DIR}/dusky/" --work-tree="${SKEL_DIR}" checkout -f

# 6. Preserve specific executable permissions dynamically.
echo "  -> Locking in executable permissions for /etc/skel scripts..."
echo "# --- DUSKY PERMISSIONS START ---" >> "${PROFILE_DIR}/profiledef.sh"
while IFS= read -r -d '' exec_file; do
    rel_path="/${exec_file#${PROFILE_DIR}/airootfs/}"
    echo "file_permissions+=([\"${rel_path}\"]=\"0:0:0755\")" >> "${PROFILE_DIR}/profiledef.sh"
done < <(find "${SKEL_DIR}" -path "${SKEL_DIR}/dusky" -prune -o -type f -executable -print0)
echo "# --- DUSKY PERMISSIONS END ---" >> "${PROFILE_DIR}/profiledef.sh"


# --- 5. DYNAMIC MKARCHISO PATCHING (The payload) ---
echo "  -> Cloning official mkarchiso..."
cp /usr/bin/mkarchiso "$MKARCHISO_CUSTOM"
chmod +x "$MKARCHISO_CUSTOM"

echo "  -> Generating injection patch..."
cat << EOF > "$PATCH_FILE"
    _msg_info ">>> INJECTING & MERGING REPOSITORIES DIRECTLY INTO ISO <<<"
    local repo_target="\${isofs_dir}/\${install_dir}/repo"
    mkdir -p "\${repo_target}"
    
    cp -a "${OFFLINE_REPO_OFFICIAL}/." "\${repo_target}/"
    if [[ -d "${OFFLINE_REPO_AUR}" ]]; then
        cp -a "${OFFLINE_REPO_AUR}/." "\${repo_target}/"
    fi
    
    rm -f "\${repo_target}/archrepo.db"*
    rm -f "\${repo_target}/archrepo.files"*
    
    _msg_info ">>> GENERATING MASTER DATABASE INSIDE ISO <<<"
    local _nullglob_state; shopt -q nullglob && _nullglob_state=1 || _nullglob_state=0
    shopt -s nullglob
    local all_files=("\${repo_target}/"*.pkg.tar.*)
    local pkg_files=()
    for f in "\${all_files[@]}"; do
        [[ "\$f" == *.sig ]] && continue
        pkg_files+=("\$f")
    done
    (( _nullglob_state )) || shopt -u nullglob
    
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

rm -f "$PATCH_FILE"

# --- 6. ISO GENERATION ---
echo "  -> Cleaning previous build artifacts..."
rm -rf "$WORK_DIR" "$OUT_DIR"

echo -e "\n\e[1;32m==>\e[0m \e[1mSTARTING BUILD PROCESS\e[0m"
"$MKARCHISO_CUSTOM" -v -m iso -w "$WORK_DIR" -o "$OUT_DIR" "$PROFILE_DIR"

# --- 7. ARTIFACT RENAMING ---
echo "  -> Renaming output to ${FINAL_ISO_NAME}..."
mv "${OUT_DIR}"/*.iso "${OUT_DIR}/${FINAL_ISO_NAME}"

# --- 8. PERMISSIONS RESTORATION ---
if [[ -n "${SUDO_USER:-}" ]]; then
    echo "  -> Restoring ownership of the output directory to user: $SUDO_USER..."
    chown -R "$SUDO_USER:$SUDO_USER" "$OUT_DIR"
fi

echo -e "\n\e[1;32m[SUCCESS]\e[0m \e[1mISO generation complete!\e[0m"
echo "Your bootable ISO is located at: ${OUT_DIR}/${FINAL_ISO_NAME}"
