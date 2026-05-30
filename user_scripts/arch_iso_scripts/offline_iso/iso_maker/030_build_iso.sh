#!/usr/bin/env bash
# ==============================================================================
# 030_build_iso.sh - THE MASTER FACTORY ISO GENERATOR (Dual-Arch Edition)
# Description: Orchestrates ZRAM setup, dotfile injection, and offline ISO build.
# Payload: Configures Live Environment, Overrides dots, merges Offline Repos.
# ==============================================================================
set -euo pipefail
export LC_ALL=C
export LANG=C

# ==============================================================================
# SECTION 1 — PRIVILEGE ESCALATION & PATH RESOLUTION
# ==============================================================================
if [[ "${EUID}" -ne 0 ]]; then
  _SELF="$(readlink -f "${BASH_SOURCE[0]}")"
  if command -v sudo &>/dev/null; then
    printf "\n\033[1;33m[!!]\033[0m Not running as root — elevating...\n"
    exec sudo --preserve-env=TERM,NO_COLOR -- bash -- "${_SELF}" "$@"
  else
    printf "\n\033[1;31m[XX]\033[0m Must run as root; 'sudo' not found.\n" >&2
    exit 1
  fi
fi

if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_HOME=$(getent passwd "${SUDO_USER}" | cut -d: -f6)
else
    REAL_HOME="${HOME}"
fi

# ==============================================================================
# SECTION 2 — CONFIGURATION & DYNAMIC VARIABLES
# ==============================================================================
readonly FINAL_DEST_DIR="/mnt/zram1"
readonly SOURCE_DIR="${REAL_HOME}/user_scripts/arch_iso_scripts/offline_iso"
readonly WORKSPACE="${FINAL_DEST_DIR}/dusky_iso"
readonly PROFILE_DIR="${WORKSPACE}/profile"
readonly WORK_DIR="${WORKSPACE}/work"
readonly OUT_DIR="${WORKSPACE}/out"

readonly OFFLINE_REPO_BASE="/srv/offline-repo"
readonly OFFLINE_REPO_OFFICIAL="${OFFLINE_REPO_BASE}/official"
readonly OFFLINE_REPO_AUR="${OFFLINE_REPO_BASE}/aur"

readonly MKARCHISO_CUSTOM="${WORKSPACE}/mkarchiso_dusky"
readonly PATCH_FILE="${WORKSPACE}/repo_inject.patch"
readonly FINAL_ISO_NAME="dusky_$(date +%m_%y).iso"

declare -g INTERACTIVE_MODE=1
declare -g REPO_MODE=0  # 1 = Standard Arch, 2 = CachyOS

# ==============================================================================
# SECTION 3 — COLORS & LOGGING
# ==============================================================================
_setup_colors() {
  BOLD='' GREEN='' YELLOW='' RED='' CYAN='' MAGENTA='' DIM='' RESET=''
  if [[ -z "${NO_COLOR:-}" ]] && [[ -t 1 ]] && command -v tput &>/dev/null; then
    BOLD=$(tput bold)
    GREEN=$(tput setaf 2)
    YELLOW=$(tput setaf 3)
    RED=$(tput setaf 1)
    CYAN=$(tput setaf 6)
    MAGENTA=$(tput setaf 5)
    DIM=$(tput dim 2>/dev/null || true)
    RESET=$(tput sgr0)
  fi
  readonly BOLD GREEN YELLOW RED CYAN MAGENTA DIM RESET
}
_setup_colors

log_info() { printf '\n%s==>%s %s\n' "${BOLD}${CYAN}" "${RESET}" "$*"; }
log_step() { printf '  %s->%s %s\n' "${BOLD}${MAGENTA}" "${RESET}" "$*"; }
log_ok()   { printf '%s[OK]%s %s\n' "${BOLD}${GREEN}" "${RESET}" "$*"; }
log_warn() { printf '%s[!!]%s %s\n' "${BOLD}${YELLOW}" "${RESET}" "$*" >&2; }
log_err()  { printf '%s[XX]%s %s\n' "${BOLD}${RED}" "${RESET}" "$*" >&2; }
die()      { log_err "$*"; exit 1; }

# ==============================================================================
# SECTION 4 — ARGUMENT PARSING & INTERACTIVE UI
# ==============================================================================
_print_logo() {
  printf '\n%s' "${BOLD}${CYAN}"
  printf '╔══════════════════════════════════════════════════════════════╗\n'
  printf '║       Dusky Arch ISO Factory Builder  (Dual-Arch)            ║\n'
  printf '╚══════════════════════════════════════════════════════════════╝\n'
  printf '%s\n' "${RESET}"
}

_parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --arch) REPO_MODE=1; shift ;;
      --cachyos) REPO_MODE=2; shift ;;
      --auto) INTERACTIVE_MODE=0; shift ;;
      *) die "Unknown argument: $1\nUsage: $0 [--arch | --cachyos] [--auto]" ;;
    esac
  done
}

_prompt_build_mode() {
  (( REPO_MODE != 0 )) && return 0
  (( INTERACTIVE_MODE )) || { REPO_MODE=2; return 0; }

  printf '\n%s==>%s %sSelect Live ISO Target Architecture%s\n' "${BOLD}${CYAN}" "${RESET}" "${BOLD}" "${RESET}"
  printf '  1) Standard Arch Linux (Maximum Boot Compatibility)\n'
  printf '  2) CachyOS x86-64-v3 (Optimized Live Environment)\n\n'
  
  local choice
  while true; do
    read -r -p "  Enter choice [1-2] (default=2): " choice
    choice="${choice:-2}"
    case "$choice" in
      1) REPO_MODE=1; break ;;
      2) REPO_MODE=2; break ;;
      *) printf "  %sInvalid choice.%s\n" "${RED}" "${RESET}" ;;
    esac
  done
}

# ==============================================================================
# SECTION 5 — PREFLIGHT FORENSICS & KEYS
# ==============================================================================
_preflight_checks() {
  log_info "Running preflight forensics"
  
  [[ -z "${WORKSPACE}" || "${WORKSPACE}" == "/" ]] && die "Workspace variable is unsafe (${WORKSPACE}). Aborting."
  [[ ! -d "${OFFLINE_REPO_OFFICIAL}" ]] && die "Official offline repository missing at ${OFFLINE_REPO_OFFICIAL}."
  
  if [[ ! -d "${OFFLINE_REPO_AUR}" ]]; then
      log_warn "AUR offline repository NOT found at ${OFFLINE_REPO_AUR}. Proceeding without AUR packages."
  else
      log_step "AUR cache object count:      $(ls -lah "${OFFLINE_REPO_AUR}/" | wc -l)"
  fi
  
  if ! command -v git &>/dev/null; then die "git is required but not installed."; fi
  if ! grep -q '^_build_iso_image() {' /usr/bin/mkarchiso; then die "Could not locate '_build_iso_image() {' in /usr/bin/mkarchiso."; fi

  log_step "Official cache object count: $(ls -lah "${OFFLINE_REPO_OFFICIAL}/" | wc -l)"

  if (( REPO_MODE == 2 )); then
    if ! pacman -Q cachyos-keyring &>/dev/null; then
      log_warn "CachyOS mode requires 'cachyos-keyring' installed on the HOST build system."
      if (( INTERACTIVE_MODE )); then
        local install_keys
        read -r -p "  Would you like to automatically install the CachyOS keyring now? [y/N]: " install_keys
        if [[ "${install_keys}" =~ ^[Yy]$ ]]; then
          log_info "Fetching and installing CachyOS keyring..."
          pacman-key --recv-keys F3B607488DB35A47 --keyserver keyserver.ubuntu.com || die "Failed to receive CachyOS key."
          pacman-key --lsign-key F3B607488DB35A47 || die "Failed to locally sign CachyOS key."
          
          local keyring_pkg
          keyring_pkg=$(curl -sL https://mirror.cachyos.org/repo/x86_64/cachyos/ | grep -o 'cachyos-keyring-[0-9][^"]*\.pkg\.tar\.zst' | head -n1 || true)
          if [[ -n "$keyring_pkg" ]]; then
             pacman -U --noconfirm "https://mirror.cachyos.org/repo/x86_64/cachyos/${keyring_pkg}" || die "Failed to install cachyos-keyring."
          else
             pacman -U --noconfirm 'https://mirror.cachyos.org/repo/x86_64/cachyos/cachyos-keyring-20240331-1-any.pkg.tar.zst' || die "Failed fallback install."
          fi
          log_ok "CachyOS keyring successfully installed."
        else
          die "Cannot proceed without cachyos-keyring."
        fi
      else
        die "Missing cachyos-keyring on host (running non-interactively)."
      fi
    fi
  fi
  log_ok "Preflight complete. Ready to build."
}

# ==============================================================================
# SECTION 6 — ZRAM CLEAN ROOM & ARCHISO SETUP
# ==============================================================================
_setup_clean_room() {
  log_info "Configuring Archiso Clean Room"
  log_step "Enforcing archiso dependency..."
  pacman -S --needed --noconfirm archiso >/dev/null

  [[ -d "${WORKSPACE}" ]] && rm -rf "${WORKSPACE}"
  rm -f "${FINAL_DEST_DIR}/dusky_"*.iso

  mkdir -p "${WORKSPACE}"
  log_step "Cloning 'releng' blueprint to workspace..."
  cp -a /usr/share/archiso/configs/releng "${PROFILE_DIR}"
  log_ok "Clean room established."
}

# ==============================================================================
# SECTION 7 — STAGING ORCHESTRATION PAYLOADS & SYSTEMD KEYS
# ==============================================================================
_stage_payloads() {
  log_info "Injecting Airootfs Staging Payloads"
  mkdir -p "${PROFILE_DIR}/airootfs/root/arch_install"

  shopt -s dotglob nullglob
  cp -a "${SOURCE_DIR}/"* "${PROFILE_DIR}/airootfs/root/arch_install/"
  shopt -u dotglob nullglob

  log_step "Injecting predefined packages.x86_64 asset..."
  cp -a "${SOURCE_DIR}/assets/iso_temp_packages/packages.x86_64" "${PROFILE_DIR}/packages.x86_64"

  if (( REPO_MODE == 2 )); then
      log_step "Appending CachyOS prerequisites to Live ISO package list..."
      echo "cachyos-keyring" >> "${PROFILE_DIR}/packages.x86_64"
      echo "cachyos-mirrorlist" >> "${PROFILE_DIR}/packages.x86_64"
      echo "cachyos-v3-mirrorlist" >> "${PROFILE_DIR}/packages.x86_64"
      echo "cachyos-rate-mirrors" >> "${PROFILE_DIR}/packages.x86_64"

      log_step "Injecting Systemd drop-in to populate CachyOS cryptographic keys on Live ISO boot..."
      mkdir -p "${PROFILE_DIR}/airootfs/etc/systemd/system/pacman-init.service.d/"
      cat << 'EOF' > "${PROFILE_DIR}/airootfs/etc/systemd/system/pacman-init.service.d/cachyos.conf"
[Service]
ExecStart=/usr/bin/pacman-key --populate cachyos
EOF
  fi

  log_ok "Payloads staged."
}

# ==============================================================================
# SECTION 8 — LIVE ENVIRONMENT HOOKS (Auto-Start)
# ==============================================================================
_configure_live_hooks() {
  log_info "Configuring Auto-Start Hooks"
  cat << 'EOF' > "${PROFILE_DIR}/airootfs/root/.automated_script.sh"
#!/usr/bin/env bash
if [[ "$(tty)" == "/dev/tty1" ]]; then
    echo "root:0000" | chpasswd
    echo -e "\e[1;32m[INFO]\e[0m Root password set to 0000. SSH is available."
    echo -e "\e[1;34m[INFO]\e[0m Bootstrapping environment..."
    systemctl is-system-running >/dev/null 2>&1 || true

    chmod -R +x /root/arch_install/
    clear
    cd /root/arch_install/
    ./000_dusky_arch_install.sh
fi
EOF
  chmod +x "${PROFILE_DIR}/airootfs/root/.automated_script.sh"
  log_ok "Live hooks configured."
}

# ==============================================================================
# SECTION 9 — SKELETON DIRECTORY & DOTFILES
# ==============================================================================
_inject_dotfiles() {
  log_info "Fetching and Injecting GitHub Dotfiles"
  local SKEL_DIR="${PROFILE_DIR}/airootfs/etc/skel"
  rm -rf "${SKEL_DIR}" && mkdir -p "${SKEL_DIR}"

  sed -i '/^# --- DUSKY PERMISSIONS START ---/,/^# --- DUSKY PERMISSIONS END ---/d' "${PROFILE_DIR}/profiledef.sh"
  sed -i '/^grml-zsh-config$/d' "${PROFILE_DIR}/packages.x86_64" || true

  rm -rf "/tmp/dusky_dots" 
  local attempt
  for attempt in {1..3}; do
      if git clone --depth 1 "https://github.com/dusklinux/dusky" "/tmp/dusky_dots" 2>/dev/null; then
          log_step "Clone successful."
          break
      fi
      if (( attempt == 3 )); then die "Git clone failed."; fi
      log_warn "Git clone failed. Retrying in 5s..."
      rm -rf "/tmp/dusky_dots"
      sleep 5
  done

  cp -a /tmp/dusky_dots/. "${SKEL_DIR}/"
  rm -rf "${SKEL_DIR}/.git" "/tmp/dusky_dots"

  log_step "Injecting local hyprland.lua override..."
  mkdir -p "${SKEL_DIR}/.config/hypr"
  cp -a "${SOURCE_DIR}/assets/hyprland/hyprland.lua" "${SKEL_DIR}/.config/hypr/hyprland.lua"

  log_step "Locking executable permissions in profiledef.sh..."
  echo "# --- DUSKY PERMISSIONS START ---" >> "${PROFILE_DIR}/profiledef.sh"
  while IFS= read -r -d '' exec_file; do
      local rel_path="/${exec_file#${PROFILE_DIR}/airootfs/}"
      echo "file_permissions+=([\"${rel_path}\"]=\"0:0:0755\")" >> "${PROFILE_DIR}/profiledef.sh"
  done < <(find "${SKEL_DIR}" -type f -executable -print0)
  echo "# --- DUSKY PERMISSIONS END ---" >> "${PROFILE_DIR}/profiledef.sh"
  
  log_ok "Dotfiles secured."
}

# ==============================================================================
# SECTION 10 — DYNAMIC MKARCHISO PATCHING (The Core Magic)
# ==============================================================================
_patch_mkarchiso() {
  log_info "Dynamically Patching Build Configurations"
  
  local aur_cache=""
  if [[ -d "${OFFLINE_REPO_AUR}" ]]; then
      aur_cache="CacheDir = ${OFFLINE_REPO_AUR}"
  fi

  if (( REPO_MODE == 2 )); then
    log_step "Routing pacman.conf for CachyOS v3 and injecting UI enhancements..."
    
    # 1. Create a safe sandbox for mirrorlists to avoid touching host files
    local build_pacman_d="${PROFILE_DIR}/build_pacman.d"
    mkdir -p "${build_pacman_d}"
    
    # 2. Copy mirrorlists from host and explicitly hardcode x86_64 to prevent 404s on Arch mirrors
    find /etc/pacman.d -maxdepth 1 -type f -exec cp {} "${build_pacman_d}/" \;
    find "${build_pacman_d}" -type f -exec sed -i 's/\$arch/x86_64/g' {} +
    
    # 3. Patch pacman.conf to accept v3 architecture, inject UI, and reroute Includes to the sandbox
    awk -v off="CacheDir = ${OFFLINE_REPO_OFFICIAL}" \
        -v aur="${aur_cache}" \
        -v sandbox="${build_pacman_d}" '
    /^#?VerbosePkgLists/ { next }
    /^#?Color/ { next }
    /^#?ILoveCandy/ { next }
    /^#?ParallelDownloads/ { next }
    /^\s*Architecture\s*=/ { next }
    /^\[options\]/ {
        print
        print "Color"
        print "ILoveCandy"
        print "VerbosePkgLists"
        print "ParallelDownloads = 10"
        print off
        if (aur != "") { print aur }
        print "CacheDir = /var/cache/pacman/pkg"
        print "Architecture = x86_64_v3 x86_64"
        next
    }
    /^\[core\]/ {
        print "# --- INJECTED CACHYOS v3 REPOSITORIES ---"
        print "[cachyos-v3]"
        print "Server = https://mirror.cachyos.org/repo/x86_64_v3/$repo"
        print ""
        print "[cachyos-core-v3]"
        print "Server = https://mirror.cachyos.org/repo/x86_64_v3/$repo"
        print ""
        print "[cachyos-extra-v3]"
        print "Server = https://mirror.cachyos.org/repo/x86_64_v3/$repo"
        print ""
        print "[cachyos]"
        print "Server = https://mirror.cachyos.org/repo/x86_64/$repo"
        print "# ----------------------------------------"
        print ""
        print "[core]"
        next
    }
    /^\[cachyos/ { skip_cachy = 1; next }
    /^\[/ { skip_cachy = 0 }
    skip_cachy == 1 { next }
    {
        # Redirect Include statements to our modified sandbox mirrorlists
        gsub("/etc/pacman.d/", sandbox "/")
        
        # Hardcode any inline Server definitions to x86_64
        if ($0 ~ /^\s*Server\s*=/) {
            gsub("\\$arch", "x86_64")
        }
        print
    }
    ' "${PROFILE_DIR}/pacman.conf" > "${PROFILE_DIR}/pacman.conf.tmp" && mv "${PROFILE_DIR}/pacman.conf.tmp" "${PROFILE_DIR}/pacman.conf"
  else
    log_step "Routing pacman.conf for Standard Arch Offline Cache & UI enhancements..."
    awk -v off="CacheDir = ${OFFLINE_REPO_OFFICIAL}" -v aur="${aur_cache}" '
    /^#?VerbosePkgLists/ { next }
    /^#?Color/ { next }
    /^#?ILoveCandy/ { next }
    /^#?ParallelDownloads/ { next }
    /^\[options\]/ {
        print
        print "Color"
        print "ILoveCandy"
        print "VerbosePkgLists"
        print "ParallelDownloads = 10"
        print off
        if (aur != "") { print aur }
        print "CacheDir = /var/cache/pacman/pkg"
        next
    }
    {print}
    ' "${PROFILE_DIR}/pacman.conf" > "${PROFILE_DIR}/pacman.conf.tmp" && mv "${PROFILE_DIR}/pacman.conf.tmp" "${PROFILE_DIR}/pacman.conf"
  fi

  log_step "Generating injection patch for offline repository merging..."
  cp /usr/bin/mkarchiso "$MKARCHISO_CUSTOM"
  chmod +x "$MKARCHISO_CUSTOM"

  # The AUR directory is conditionally copied into the ISO structure only if it exists.
  cat << EOF > "$PATCH_FILE"
    _msg_info ">>> INJECTING & MERGING REPOSITORIES DIRECTLY INTO ISO <<<"
    local repo_target="\${isofs_dir}/\${install_dir}/repo"
    mkdir -p "\${repo_target}"
    cp -a "${OFFLINE_REPO_OFFICIAL}/." "\${repo_target}/"
    
    if [[ -d "${OFFLINE_REPO_AUR}" ]]; then 
        cp -a "${OFFLINE_REPO_AUR}/." "\${repo_target}/"
    fi
    
    rm -f "\${repo_target}/archrepo.db"* "\${repo_target}/archrepo.files"*
    
    local _nullglob_state; shopt -q nullglob && _nullglob_state=1 || _nullglob_state=0
    shopt -s nullglob
    local all_files=("\${repo_target}/"*.pkg.tar.*)
    local pkg_files=()
    for f in "\${all_files[@]}"; do [[ "\$f" == *.sig ]] && continue; pkg_files+=("\$f"); done
    (( _nullglob_state )) || shopt -u nullglob
    
    if (( \${#pkg_files[@]} > 0 )); then
        repo-add -q "\${repo_target}/archrepo.db.tar.gz" "\${pkg_files[@]}"
    else
        echo "[ERR] No packages found to merge inside ISO!" >&2; return 1
    fi
EOF

  sed -i '/^_build_iso_image() {/r '"$PATCH_FILE"'' "$MKARCHISO_CUSTOM"

  if ! grep -q 'INJECTING & MERGING REPOSITORIES DIRECTLY INTO ISO' "$MKARCHISO_CUSTOM"; then
      die "Patch was NOT injected — the sed pattern failed to match."
  fi
  rm -f "$PATCH_FILE"
  log_ok "mkarchiso successfully patched."
}

# ==============================================================================
# SECTION 11 — ISO GENERATION & CLEANUP
# ==============================================================================
_build_iso() {
  printf '\n%s==>%s %sSTARTING ARCHISO BUILD PROCESS%s\n\n' "${BOLD}${GREEN}" "${RESET}" "${BOLD}" "${RESET}"
  
  rm -rf "$WORK_DIR" "$OUT_DIR"
  "$MKARCHISO_CUSTOM" -v -m iso -w "$WORK_DIR" -o "$OUT_DIR" "$PROFILE_DIR"

  log_info "Build finalization"
  log_step "Relocating final ISO to root of ZRAM drive (${FINAL_DEST_DIR}/)..."
  mv "${OUT_DIR}"/*.iso "${FINAL_DEST_DIR}/${FINAL_ISO_NAME}"

  if [[ -n "${SUDO_UID:-}" && -n "${SUDO_GID:-}" ]]; then
      log_step "Restoring ownership of the final ISO to original user ID..."
      chown "${SUDO_UID}:${SUDO_GID}" "${FINAL_DEST_DIR}/${FINAL_ISO_NAME}"
  fi

  printf '\n%s%s[SUCCESS]%s Repository is primed for ISO integration.\n' "${BOLD}" "${GREEN}" "${RESET}"
  printf 'Bootable ISO located at: %s\n\n' "${FINAL_DEST_DIR}/${FINAL_ISO_NAME}"
}

# ==============================================================================
# SECTION 12 — MAIN ORCHESTRATOR
# ==============================================================================
main() {
  _parse_args "$@"
  _print_logo
  
  _prompt_build_mode
  _preflight_checks
  _setup_clean_room
  _stage_payloads
  _configure_live_hooks
  _inject_dotfiles
  _patch_mkarchiso
  _build_iso
}

main "$@"
