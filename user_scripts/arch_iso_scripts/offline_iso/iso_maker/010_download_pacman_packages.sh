#!/usr/bin/env bash
# ==============================================================================
# 010_download_pacman_packages.sh  —  v6.14 (Golden Master - Patched)
#
# Factory script: resolves the FULL transitive dependency closure of all
# defined package groups, downloads them into a local directory, then builds
# a valid pacman repository database for use in an offline Arch Linux ISO.
# ==============================================================================

set -Eeuo pipefail
export LC_ALL=C
export LANG=C

# ==============================================================================
# SECTION 1 — SELF-ELEVATION (PRE-EMPTIVE)
# ==============================================================================
# Elevate immediately. This prevents interactive double-prompts and ensures
# the entire script runs contextually as root from line 1.

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

# ==============================================================================
# SECTION 2 — PACKAGE ARRAYS
# ==============================================================================

declare -ar pkgs_offline=(
  "intel-ucode" "amd-ucode" "mkinitcpio" "glaze" "python-cssselect" "base" "base-devel" "python-lxml" "python-certifi" "python-charset-normalizer" "python-idna" "python-requests" "python-urllib3" "deno" "yt-dlp" "yt-dlp-ejs" "hunspell" "xf86-input-libinput" "xorg-xauth" "boost-libs" "plymouth" "grub" "os-prober" "cryptsetup"
 )

declare -ar pkgs_graphics=(
  "intel-media-driver" "vpl-gpu-rt" "mesa" "vulkan-intel" "mesa-utils" "intel-gpu-tools" "libva" "libva-utils" "vulkan-icd-loader" "vulkan-tools" "sof-firmware" "linux-firmware" "linux-headers" "acpi_call" "kernel-modules-hook" "linux-firmware-nvidia" "linux-firmware-amdgpu" "linux-firmware-radeon" "linux-firmware-intel" "linux-firmware-mediatek" "linux-firmware-broadcom" "linux-firmware-atheros" "linux-firmware-realtek" "linux-firmware-cirrus" "linux-firmware-other" "linux-firmware-whence"
)

declare -ar pkgs_hyprland=(
  "hyprland" "xorg-xwayland" "xdg-desktop-portal-hyprland" "xdg-desktop-portal-gtk" "localsearch" "polkit" "hyprpolkitagent" "xdg-utils" "socat" "inotify-tools" "libnotify" "mako" "file"
)

declare -ar pkgs_appearance=(
  "qt5-wayland" "qt6-wayland" "gtk3" "gtk4" "nwg-look" "qt5ct" "qt6ct" "qt6-svg" "qt6-multimedia-ffmpeg" "adw-gtk-theme" "upower" "plocate" "matugen" "ttf-font-awesome" "ttf-jetbrains-mono-nerd" "otf-atkinsonhyperlegiblemono-nerd" "noto-fonts-emoji" "sassc" "python-packaging" "python" "python-evdev" "python-pyudev" "fontconfig" "papirus-icon-theme" "python-pyquery" "python-textual" "python-rich"
)

declare -ar pkgs_desktop=(
  "waybar" "awww" "hyprlock" "hypridle" "hyprsunset" "hyprpicker" "rofi" "libdbusmenu-qt5" "libdbusmenu-glib" "brightnessctl" "hyprshutdown"
)

declare -ar pkgs_audio=(
  "pipewire" "pipewire-alsa" "alsa-utils" "wireplumber" "pipewire-pulse" "playerctl" "bluez" "bluez-utils" "bluez-hid2hci" "bluez-libs" "bluez-obex" "blueman" "bluetui" "pavucontrol" "gst-plugins-base" "gst-libav" "gst-plugins-bad" "gst-plugins-good" "gst-plugins-ugly" "gst-plugin-pipewire" "libcanberra" "songrec" "sox"
)

declare -ar pkgs_filesystem=(
  "btrfs-progs" "compsize" "zram-generator" "udisks2" "udiskie" "dosfstools" "ntfs-3g" "xdg-user-dirs" "usbutils" "gnome-disk-utility" "unzip" "zip" "unrar" "7zip" "cpio" "file-roller" "rsync" "nfs-utils" "nilfs-utils" "smartmontools" "dmraid" "hdparm" "hwdetect" "lsscsi" "sg3_utils" "cpupower" "dust" "dkms"
  "thunar" "thunar-archive-plugin" "file-roller" "thunar-volman" "thunar-media-tags-plugin" "thunar-shares-plugin" "thunar-vcs-plugin" "tumbler" "ffmpegthumbnailer" "webp-pixbuf-loader" "poppler-glib" "libgsf" "libgepub" "libopenraw" "resvg" "gvfs" "gvfs-mtp" "gvfs-nfs" "gvfs-smb" "gvfs-gphoto2" "gvfs-afc" "gvfs-dnssd" "catfish" "gnome-keyring" "meld" "xreader" "imagemagick"
)

declare -ar pkgs_network=(
  "networkmanager" "wireless-regdb" "iwd" "nm-connection-editor" "inetutils" "wget" "curl" "openssh" "ufw" "vsftpd" "reflector" "bmon" "ethtool" "httrack" "wavemon" "firefox" "nss-mdns" "dnsmasq" "modemmanager" "usb_modeswitch"
)

declare -ar pkgs_terminal=(
  "kitty" "foot" "zsh" "zsh-syntax-highlighting" "starship" "fastfetch" "bat" "eza" "fd" "yazi" "gum" "tree" "fzf" "less" "ripgrep" "expac" "zsh-autosuggestions" "iperf3" "pkgstats" "libqalculate" "moreutils" "zoxide" "man-db" "lsof" "khal"
)

declare -ar pkgs_dev=(
  "neovim" "git" "git-delta" "lazygit" "meson" "cmake" "clang" "uv" "rq" "jq" "pv" "bc" "viu" "chafa" "ueberzugpp" "ccache" "mold" "shellcheck" "fd" "ripgrep" "fzf" "shfmt" "stylua" "prettier" "tree-sitter-cli" "nano" "luarocks"
)

declare -ar pkgs_multimedia=(
  "ffmpeg" "mpv" "mpv-mpris" "satty" "swayimg" "resvg" "imagemagick" "libheif" "ffmpegthumbnailer" "grim" "slurp" "wl-clipboard" "wl-clip-persist" "cliphist" "tesseract-data-eng" "gpu-screen-recorder-ui" "ddcutil"
)

declare -ar pkgs_sysadmin=(
  "btop" "htop" "dgop" "nvtop" "inxi" "sysstat" "sysbench" "logrotate" "acpid" "tlp" "tlp-rdw" "thermald" "powertop" "gdu" "iotop" "iftop" "lshw" "hwinfo" "dmidecode" "wev" "pacman-contrib" "gnome-keyring" "libsecret" "seahorse" "greetd-agreety" "greetd" "greetd-tuigreet" "yad" "dysk" "fwupd" "perl" "accountsservice" "smartmontools" "pkgfile" "rebuild-detector" "accountsservice"
)

declare -ar pkgs_gnome=(
  "snapshot" "cameractrls" "loupe" "mousepad" "gnome-calculator" "gnome-clocks"
)

declare -ar pkgs_productivity=(
  "zathura" "zathura-pdf-mupdf" "cava"
)

declare -ar pkgs_btrfs_snapshot=(
 "snapper"
)

# ==============================================================================
# SECTION 3 — CONFIGURATION & DYNAMIC VARIABLES
# ==============================================================================

if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_HOME=$(getent passwd "${SUDO_USER}" | cut -d: -f6)
else
    REAL_HOME="${HOME}"
fi

readonly EXTERNAL_PKG_LIST="${REAL_HOME}/user_scripts/arch_iso_scripts/offline_iso/assets/iso_temp_packages/packages.x86_64"

readonly REPO_NAME='archrepo'
readonly ISOLATED_DB_DIR='/tmp/offline_pacman_isolated_db'

declare -g OFFLINE_REPO_DIR=''
declare -g INTERACTIVE_MODE=1
declare -g REPO_MODE=0  # 1 = Standard Arch, 2 = CachyOS

# ==============================================================================
# SECTION 4 — COLORS & LOGGING
# ==============================================================================

_setup_colors() {
  BOLD='' GREEN='' YELLOW='' RED='' CYAN='' MAGENTA='' DIM='' RESET=''
  if [[ -z "${NO_COLOR:-}" ]] && [[ -t 1 ]] && command -v tput &>/dev/null; then
    BOLD=$(tput bold 2>/dev/null || true)
    GREEN=$(tput setaf 2 2>/dev/null || true)
    YELLOW=$(tput setaf 3 2>/dev/null || true)
    RED=$(tput setaf 1 2>/dev/null || true)
    CYAN=$(tput setaf 6 2>/dev/null || true)
    MAGENTA=$(tput setaf 5 2>/dev/null || true)
    DIM=$(tput dim 2>/dev/null || true)
    RESET=$(tput sgr0 2>/dev/null || true)
  fi
  readonly BOLD GREEN YELLOW RED CYAN MAGENTA DIM RESET
}
_setup_colors

log_info() { printf '\n%s==>%s %s\n' "${BOLD}${CYAN}" "${RESET}" "$*"; }
log_step() { printf '  %s->%s %s\n' "${BOLD}${MAGENTA}" "${RESET}" "$*"; }
log_ok()   { printf '%s[OK]%s %s\n' "${BOLD}${GREEN}" "${RESET}" "$*"; }
log_warn() { printf '%s[!!]%s %s\n' "${BOLD}${YELLOW}" "${RESET}" "$*" >&2; }
log_err()  { printf '%s[XX]%s %s\n' "${BOLD}${RED}" "${RESET}" "$*" >&2; }
log_delete(){ printf '  %s[-]%s %s\n' "${BOLD}${RED}" "${RESET}" "$*"; }
die()      { log_err "$*"; exit 1; }

_human_bytes() {
  local -i bytes=${1:-0}
  if (( bytes <= 0 )); then printf '0 B'
  elif (( bytes >= 1073741824 )); then printf '%.2f GiB' "$(bc -l <<<"scale=6; $bytes/1073741824")"
  elif (( bytes >= 1048576 )); then printf '%.2f MiB' "$(bc -l <<<"scale=6; $bytes/1048576")"
  elif (( bytes >= 1024 )); then printf '%.2f KiB' "$(bc -l <<<"scale=6; $bytes/1024")"
  else printf '%d B' "$bytes"; fi
}

# ==============================================================================
# SECTION 5 — GLOBAL TEMP-FILE REGISTRY & TRAP / CLEANUP
# ==============================================================================

declare -ga _TEMP_PATHS=()
_register_temp() { _TEMP_PATHS+=("$1"); }

_cleanup_done=0
_cleanup() {
  local rc=$?
  (( _cleanup_done )) && return 0
  _cleanup_done=1

  for p in "${_TEMP_PATHS[@]+"${_TEMP_PATHS[@]}"}"; do
    [[ -e "$p" || -L "$p" ]] && rm -rf -- "$p"
  done
  [[ -d "${ISOLATED_DB_DIR}" ]] && rm -rf -- "${ISOLATED_DB_DIR}"
  (( rc != 0 )) && log_err "Script exited with error status ${rc}."
  return 0
}

_on_err() {
  local rc=$?
  log_err "Fatal error on line ${1:-?}: command '${2:-?}' returned ${rc}."
}

trap '_on_err "$LINENO" "$BASH_COMMAND"' ERR
trap '_cleanup' EXIT

# ==============================================================================
# SECTION 6 — ARGUMENT PARSING & INTERACTIVE UI
# ==============================================================================

_print_logo() {
  printf '\n%s' "${BOLD}${CYAN}"
  printf '╔══════════════════════════════════════════════════════════════╗\n'
  printf '║      Offline Arch Linux Repository Builder  (Factory)        ║\n'
  printf '╚══════════════════════════════════════════════════════════════╝\n'
  printf '%s\n' "${RESET}"
}

_parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --arch)
        REPO_MODE=1
        shift
        ;;
      --cachyos)
        REPO_MODE=2
        shift
        ;;
      --auto)
        OFFLINE_REPO_DIR='/srv/offline-repo/official'
        INTERACTIVE_MODE=0
        shift
        ;;
      --current)
        OFFLINE_REPO_DIR="$(pwd)"
        INTERACTIVE_MODE=0
        shift
        ;;
      --path)
        [[ -z "${2:-}" ]] && die "--path requires a directory argument."
        OFFLINE_REPO_DIR="$2"
        INTERACTIVE_MODE=0
        shift 2
        ;;
      *)
        die "Unknown argument: $1\nUsage: $0 [--arch | --cachyos] [--auto | --current | --path <dir>]"
        ;;
    esac
  done
}

_prompt_build_mode() {
  (( REPO_MODE != 0 )) && return 0
  (( INTERACTIVE_MODE )) || { REPO_MODE=2; return 0; }

  printf '\n%s==>%s %sSelect Target Repository Mode%s\n' "${BOLD}${CYAN}" "${RESET}" "${BOLD}" "${RESET}"
  printf '  1) Standard Arch Linux (Pure)\n'
  printf '  2) CachyOS x86-64-v3 (Optimized + Arch Fallback)\n\n'
  
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

_prompt_repo_dir() {
  (( INTERACTIVE_MODE )) || return 0

  printf '\n%s==>%s %sSelect Offline Repository Target Location%s\n' "${BOLD}${CYAN}" "${RESET}" "${BOLD}" "${RESET}"
  printf '  1) System Default  (/srv/offline-repo/official)\n'
  printf '  2) Current working directory  (%s)\n' "$(pwd)"
  printf '  3) Custom absolute path\n\n'
  
  local choice
  while true; do
    read -r -p "  Enter choice [1-3] (default=1): " choice
    choice="${choice:-1}"
    case "$choice" in
      1) OFFLINE_REPO_DIR='/srv/offline-repo/official'; break ;;
      2) OFFLINE_REPO_DIR="$(pwd)"; break ;;
      3) 
        read -r -p "  Enter absolute path: " OFFLINE_REPO_DIR
        [[ -n "$OFFLINE_REPO_DIR" ]] && break
        ;;
      *) printf "  %sInvalid choice.%s\n" "${RED}" "${RESET}" ;;
    esac
  done
}

# ==============================================================================
# SECTION 7 — PREFLIGHT CHECKS
# ==============================================================================

_check_dependencies() {
  log_info "Checking required tools"
  local -a required=(pacman repo-add bc awk grep curl)
  local tool missing=0
  for tool in "${required[@]}"; do
    if command -v "$tool" &>/dev/null; then log_step "${tool}: $(command -v "$tool")"
    else log_err "Required tool missing: '${tool}'"; (( ++missing )) || true; fi
  done
  (( missing > 0 )) && die "${missing} required tool(s) missing — cannot continue."
  
  [[ -r /etc/arch-release ]] || die "Not running on Arch Linux."

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
          
          log_step "Fetching latest keyring package URL..."
          local keyring_pkg
          keyring_pkg=$(curl -sL https://mirror.cachyos.org/repo/x86_64/cachyos/ | grep -o 'cachyos-keyring-[0-9][^"]*\.pkg\.tar\.zst' | head -n1 || true)
          
          if [[ -n "$keyring_pkg" ]]; then
             pacman -U --noconfirm "https://mirror.cachyos.org/repo/x86_64/cachyos/${keyring_pkg}" || die "Failed to install dynamically fetched cachyos-keyring package."
          else
             log_warn "Could not scrape dynamic keyring. Falling back to hardcoded version..."
             pacman -U --noconfirm 'https://mirror.cachyos.org/repo/x86_64/cachyos/cachyos-keyring-20240331-1-any.pkg.tar.zst' || die "Failed to install fallback cachyos-keyring package."
          fi
          log_ok "CachyOS keyring successfully installed."
        else
          die "Cannot proceed without cachyos-keyring."
        fi
      else
        log_err "Please run the following on your host machine to import the keys:"
        log_err "  sudo pacman-key --recv-keys F3B607488DB35A47 --keyserver keyserver.ubuntu.com"
        log_err "  sudo pacman-key --lsign-key F3B607488DB35A47"
        log_err "  sudo pacman -U 'https://mirror.cachyos.org/repo/x86_64/cachyos/cachyos-keyring-20240331-1-any.pkg.tar.zst'"
        die "Missing cachyos-keyring on host (running non-interactively)."
      fi
    fi
  fi
  log_ok "All required tools and keys are present."
}

_check_single_instance() {
  local lock_file='/run/lock/offline_pacman_packages.lock'
  mkdir -p -- /run/lock
  exec {_LOCK_FD}>"$lock_file" || die "Cannot open lock file."
  flock -n "$_LOCK_FD" || die "Another instance is already running."
}

# ==============================================================================
# SECTION 8 — PACKAGE ARRAY DISCOVERY
# ==============================================================================

declare -ga MASTER_PKGS=()

_build_master_list() {
  log_info "Scanning for package arrays (prefix: pkgs_) and external list"
  local varname decl element
  local -A _seen=()
  local -i group_count=0 raw_count=0

  while IFS= read -r varname; do
    decl=$(declare -p "$varname" 2>/dev/null) || continue
    [[ "$decl" == 'declare -'*'a'* ]] || continue
    local -n _arr_ref="$varname"
    local -i grp_count=0

    for element in "${_arr_ref[@]}"; do
      [[ -n "$element" ]] || continue
      (( ++raw_count )) || true
      (( ++grp_count )) || true
      if [[ -z "${_seen[$element]+_}" ]]; then
        _seen[$element]=1
        MASTER_PKGS+=("$element")
      fi
    done
    log_step "${varname}  →  ${grp_count} package(s)"
    (( ++group_count )) || true
    unset -n _arr_ref
  done < <(compgen -A variable 'pkgs_' | sort)

  # --- INJECT EXTERNAL PACKAGES FILE ---
  if [[ -f "${EXTERNAL_PKG_LIST}" ]]; then
    log_step "Reading external list: ${EXTERNAL_PKG_LIST}"
    local -i ext_count=0
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%%#*}"  # Strip inline comments
      
      # Read all words/packages safely (handles multiple pkgs per line & tabs)
      for pkg in $line; do
        pkg="${pkg//$'\r'/}" # Strip Windows carriage returns if present
        [[ -z "$pkg" ]] && continue
        
        (( ++raw_count )) || true
        (( ++ext_count )) || true
        if [[ -z "${_seen[$pkg]+_}" ]]; then
          _seen[$pkg]=1
          MASTER_PKGS+=("$pkg")
        fi
      done
    done < "${EXTERNAL_PKG_LIST}"
    log_step "external_file  →  ${ext_count} package(s)"
  else
    log_warn "External package list not found at: ${EXTERNAL_PKG_LIST}"
  fi
  # -------------------------------------

  if (( REPO_MODE == 2 )); then
      log_step "Injecting CachyOS prerequisite packages (cachyos-keyring, mirrorlist, rate-mirrors)..."
      MASTER_PKGS+=("cachyos-keyring" "cachyos-mirrorlist" "cachyos-v3-mirrorlist" "cachyos-rate-mirrors")
      (( raw_count += 3 )) || true
  fi

  log_ok "${group_count} groups + list | ${raw_count} raw | ${#MASTER_PKGS[@]} unique packages."
  (( ${#MASTER_PKGS[@]} > 0 )) || die "No packages found."
}

# ==============================================================================
# SECTION 9 — ISOLATED PACMAN DATABASE & SANDBOX BYPASS
# ==============================================================================

_pacman_isolated() {
  pacman \
    --dbpath    "${ISOLATED_DB_DIR}"   \
    --gpgdir    '/etc/pacman.d/gnupg' \
    --config    "${ISOLATED_DB_DIR}/pacman.conf" \
    --disable-sandbox                 \
    --noconfirm                       \
    --color     auto                  \
    "$@"
}

_init_isolated_db() {
  local mode_name="Standard Arch"
  (( REPO_MODE == 2 )) && mode_name="CachyOS v3 Injection"
  
  log_info "Initialising isolated pacman sandbox (${mode_name})"
  [[ -d "${ISOLATED_DB_DIR}" ]] && rm -rf -- "${ISOLATED_DB_DIR}"
  mkdir -p -- "${ISOLATED_DB_DIR}/local" "${ISOLATED_DB_DIR}/sync" "${ISOLATED_DB_DIR}/pacman.d"
  chmod -R 777 "${ISOLATED_DB_DIR}"

  if (( REPO_MODE == 2 )); then
    log_step "Generating pacman.conf with CachyOS v3 prioritization & UI enhancements..."
    
    find /etc/pacman.d -maxdepth 1 -type f -exec cp {} "${ISOLATED_DB_DIR}/pacman.d/" \;

    # Precise architecture patching for the isolated sandbox to prevent 404s
    if [[ -f "${ISOLATED_DB_DIR}/pacman.d/cachyos-v3-mirrorlist" ]]; then
        sed -i 's/\$arch_v3/x86_64_v3/g' "${ISOLATED_DB_DIR}/pacman.d/cachyos-v3-mirrorlist"
        sed -i 's/\$arch/x86_64_v3/g'    "${ISOLATED_DB_DIR}/pacman.d/cachyos-v3-mirrorlist"
    else
        echo "Server = https://mirror.cachyos.org/repo/x86_64_v3/\$repo" > "${ISOLATED_DB_DIR}/pacman.d/cachyos-v3-mirrorlist"
    fi

    if [[ -f "${ISOLATED_DB_DIR}/pacman.d/cachyos-mirrorlist" ]]; then
        sed -i 's/\$arch/x86_64/g'       "${ISOLATED_DB_DIR}/pacman.d/cachyos-mirrorlist"
    else
        echo "Server = https://mirror.cachyos.org/repo/x86_64/\$repo" > "${ISOLATED_DB_DIR}/pacman.d/cachyos-mirrorlist"
    fi

    if [[ -f "${ISOLATED_DB_DIR}/pacman.d/mirrorlist" ]]; then
        sed -i 's/\$arch/x86_64/g'       "${ISOLATED_DB_DIR}/pacman.d/mirrorlist"
    fi

    awk -v sandbox="${ISOLATED_DB_DIR}" '
    /^#?VerbosePkgLists/ { print "VerbosePkgLists"; next }
    /^#?Color/ { print "Color\nILoveCandy"; next }
    /^#?ParallelDownloads/ { print "ParallelDownloads = 5"; next }
    /^\[options\]/ {
        print
        print "Architecture = x86_64_v3 x86_64"
        next
    }
    /^\s*Architecture\s*=/ {
        next
    }
    /^\[core\]/ {
        skip_cachy = 0
        print "# --- INJECTED CACHYOS v3 REPOSITORIES ---"
        print "[cachyos-v3]"
        print "Include = " sandbox "/pacman.d/cachyos-v3-mirrorlist"
        print ""
        print "[cachyos-core-v3]"
        print "Include = " sandbox "/pacman.d/cachyos-v3-mirrorlist"
        print ""
        print "[cachyos-extra-v3]"
        print "Include = " sandbox "/pacman.d/cachyos-v3-mirrorlist"
        print ""
        print "[cachyos]"
        print "Include = " sandbox "/pacman.d/cachyos-mirrorlist"
        print "# ----------------------------------------"
        print ""
        print "[core]"
        next
    }
    /^\[cachyos/ {
        skip_cachy = 1
        next
    }
    /^\[/ {
        skip_cachy = 0
    }
    skip_cachy == 1 {
        next
    }
    {
        gsub("/etc/pacman.d/", sandbox "/pacman.d/")
        if ($0 ~ /^\s*Server\s*=/) {
            gsub("\\$arch", "x86_64")
        }
        print
    }
    ' /etc/pacman.conf | grep -vE '^\s*(IgnorePkg|IgnoreGroup)\s*=' > "${ISOLATED_DB_DIR}/pacman.conf"
  else
    log_step "Generating standard pacman.conf with UI enhancements..."
    awk -v sandbox="${ISOLATED_DB_DIR}" '
    /^#?VerbosePkgLists/ { print "VerbosePkgLists"; next }
    /^#?Color/ { print "Color\nILoveCandy"; next }
    /^#?ParallelDownloads/ { print "ParallelDownloads = 5"; next }
    { print }
    ' /etc/pacman.conf | grep -vE '^\s*(IgnorePkg|IgnoreGroup)\s*=' > "${ISOLATED_DB_DIR}/pacman.conf"
  fi
  
  log_step "Downloading sync databases into sandbox..."
  
  local -i sync_retries=5
  local -i sync_attempt=1
  local -i sync_success=0

  while (( sync_attempt <= sync_retries )); do
    if _pacman_isolated -Sy; then
      sync_success=1
      break
    else
      log_warn "Sync interrupted (attempt ${sync_attempt}/${sync_retries}). Retrying in 3 seconds..."
      sleep 3
      (( sync_attempt++ )) || true
    fi
  done
  
  (( sync_success == 1 )) || die "Sync failed after ${sync_retries} attempts. (Check internet connection or host keyring)."
  
  log_ok "Sandbox ready."
}

# ==============================================================================
# SECTION 10 — OFFLINE REPO SETUP
# ==============================================================================

_setup_repo_dir() {
  log_info "Offline repository directory"
  mkdir -p -- "${OFFLINE_REPO_DIR}" || die "Cannot create repo directory."
  log_ok "Ready: ${OFFLINE_REPO_DIR}"
}

# ==============================================================================
# SECTION 11 — EXACT FILENAME WHITELIST GENERATION
# ==============================================================================

declare -ga WHITELIST_FILENAMES=()

_generate_whitelist_filenames() {
  log_info "Resolving full dependency closure (Exact Filenames)"
  
  local empty_cache tmp_out pacman_rc
  empty_cache=$(mktemp -d) || die "Cannot create temp cache."
  _register_temp "$empty_cache"

  tmp_out=$(mktemp) || die "Cannot create temp output."
  _register_temp "$tmp_out"

  set +e
  # We use %f to capture the exact, final filename (e.g. fzf-0.72.0-1.1-x86_64_v3.pkg.tar.zst)
  _pacman_isolated \
    -Sw --print --print-format '%f' \
    --cachedir "$empty_cache" \
    -- "${MASTER_PKGS[@]}" >"$tmp_out"
  pacman_rc=$?
  set -e

  rm -rf -- "$empty_cache"
  (( pacman_rc == 0 )) || die "Dependency resolution failed. Fix invalid packages."

  local -a raw_lines=()
  mapfile -t raw_lines <"$tmp_out"
  rm -f -- "$tmp_out"

  local line
  for line in "${raw_lines[@]}"; do
    [[ -n "$line" ]] || continue
    [[ "$line" == warning:* ]] && continue
    line="${line##*/}" # Strip URLs if present, isolating just the filename
    WHITELIST_FILENAMES+=("$line")
  done

  (( ${#WHITELIST_FILENAMES[@]} > 0 )) || die "Whitelist generation failed."
  log_ok "Closure resolved: ${#WHITELIST_FILENAMES[@]} active files required."
}

# ==============================================================================
# SECTION 12 — PACKAGE DOWNLOAD (SMART SYNC)
# ==============================================================================

_download_packages() {
  log_info "Downloading packages → ${OFFLINE_REPO_DIR}"
  
  local -i max_retries=15
  local -i attempt=1
  local -i success=0

  while (( attempt <= max_retries )); do
    if _pacman_isolated -Sw --cachedir "${OFFLINE_REPO_DIR}" -- "${MASTER_PKGS[@]}"; then
      local corrupt=0
      local pkg
      for pkg in "${OFFLINE_REPO_DIR}"/*.pkg.tar.*; do
        [[ -f "$pkg" ]] || continue
        [[ "$pkg" == *.sig || "$pkg" == *.part ]] && continue

        if [[ "$pkg" == *.zst ]]; then
          zstd -t -q "$pkg" </dev/null &>/dev/null || { rm -f -- "$pkg" "${pkg}.sig"; corrupt=1; log_delete "Corrupt ZST removed: ${pkg##*/}"; }
        elif [[ "$pkg" == *.xz ]]; then
          xz -t -q "$pkg" </dev/null &>/dev/null || { rm -f -- "$pkg" "${pkg}.sig"; corrupt=1; log_delete "Corrupt XZ removed: ${pkg##*/}"; }
        else
          bsdtar -tqf "$pkg" </dev/null &>/dev/null || { rm -f -- "$pkg" "${pkg}.sig"; corrupt=1; log_delete "Corrupt archive removed: ${pkg##*/}"; }
        fi
      done

      if (( corrupt == 1 )); then
        log_warn "Corrupt packages were found and removed. Resuming download..."
      else
        success=1
        break
      fi
    else
      log_warn "Download interrupted or stalled (attempt ${attempt}/${max_retries})."
    fi
    
    log_info "Retrying in 5 seconds to auto-reconnect and resume..."
    sleep 5
    (( attempt++ )) || true
  done

  (( success == 1 )) || die "Download failed after ${max_retries} attempts. Please check your connection."

  local -i pkg_count
  pkg_count=$(find "${OFFLINE_REPO_DIR}" -maxdepth 1 -name '*.pkg.tar.*' ! -name '*.sig' -type f | wc -l)
  (( pkg_count > 0 )) || die "No packages found in repo after download."
  log_ok "Download sync complete. ${pkg_count} total file(s) on disk."
}

# ==============================================================================
# SECTION 13 — STRICT FILENAME CACHE PRUNING
# ==============================================================================

_prune_unneeded_packages() {
  log_info "Pruning orphans and obsolete versions from: ${OFFLINE_REPO_DIR}"

  local -A _wl_set=()
  for fn in "${WHITELIST_FILENAMES[@]}"; do _wl_set[$fn]=1; done

  local -i del_count=0 del_bytes=0
  local -a pkg_files=()
  mapfile -t pkg_files < <(find "${OFFLINE_REPO_DIR}" -maxdepth 1 -name '*.pkg.tar.*' ! -name '*.sig' -type f)

  local filepath basename fsize
  for filepath in "${pkg_files[@]}"; do
    basename="${filepath##*/}"

    # If the exact filename isn't in the closure, it is an old version, dead architecture, or orphan. Delete it.
    if [[ -z "${_wl_set[$basename]+_}" ]]; then
      fsize=$(stat -c '%s' -- "$filepath" 2>/dev/null) || fsize=0
      log_delete "pruned unneeded package: ${basename}"
      rm -f -- "$filepath" "${filepath}.sig"
      
      (( del_bytes += fsize )) || true
      (( ++del_count )) || true
    fi
  done

  # Clean up stale signatures
  while IFS= read -r lone_sig; do
    local paired_pkg="${lone_sig%.sig}"
    if [[ ! -f "$paired_pkg" ]]; then
      log_delete "stale signature removed: ${lone_sig##*/}"
      rm -f -- "$lone_sig"
    fi
  done < <(find "${OFFLINE_REPO_DIR}" -maxdepth 1 -name '*.sig' -type f)

  if (( del_count > 0 )); then
    log_ok "Pruned ${del_count} unneeded file(s). Freed ~$( _human_bytes "$del_bytes" )."
  else
    log_ok "No unneeded files found."
  fi
}

# ==============================================================================
# SECTION 14 — REPOSITORY DATABASE GENERATION
# ==============================================================================

_generate_repo_database() {
  log_info "Generating pacman repository database"
  local db_file="${OFFLINE_REPO_DIR}/${REPO_NAME}.db.tar.gz"

  for artifact in "$db_file" "$db_file.old" "${OFFLINE_REPO_DIR}/${REPO_NAME}.db" \
                  "${OFFLINE_REPO_DIR}/${REPO_NAME}.files.tar.gz" \
                  "${OFFLINE_REPO_DIR}/${REPO_NAME}.files.tar.gz.old" \
                  "${OFFLINE_REPO_DIR}/${REPO_NAME}.files"; do
    [[ -e "$artifact" || -L "$artifact" ]] && rm -f -- "$artifact"
  done

  local -a pkg_files=()
  mapfile -t pkg_files < <(find "${OFFLINE_REPO_DIR}" -maxdepth 1 -name '*.pkg.tar.*' ! -name '*.sig' -type f | sort)
  (( ${#pkg_files[@]} > 0 )) || die "No packages to index."

  # Run repo-add strictly on a single thread to bypass any underlying CachyOS Rust rayon race conditions
  RAYON_NUM_THREADS=1 repo-add "${db_file}" "${pkg_files[@]}" >/dev/null || die "repo-add failed."
  log_ok "Database and symlinks created."
}

# ==============================================================================
# SECTION 15 — SMART PERMISSIONS RESTORATION
# ==============================================================================

_restore_permissions() {
  if [[ -n "${SUDO_UID:-}" && -n "${SUDO_GID:-}" ]]; then
    log_info "Restoring file ownership"
    log_step "Transferring files back to user: $(id -un "$SUDO_UID")"
    
    chown "${SUDO_UID}:${SUDO_GID}" "${OFFLINE_REPO_DIR}" 2>/dev/null || true
    find "${OFFLINE_REPO_DIR}" -maxdepth 1 \( -type f -o -type l \) -exec chown -h "${SUDO_UID}:${SUDO_GID}" {} +
    
    log_ok "Ownership restored successfully."
  fi
}

# ==============================================================================
# SECTION 16 — SUMMARY
# ==============================================================================

_print_summary() {
  log_info "Build complete"
  local mode_name="Standard Arch Linux"
  (( REPO_MODE == 2 )) && mode_name="CachyOS x86-64-v3"

  local repo_sz
  repo_sz=$(du -sh -- "${OFFLINE_REPO_DIR}" 2>/dev/null | awk '{print $1}') || repo_sz='unknown'
  local -i pkg_count
  pkg_count=$(find "${OFFLINE_REPO_DIR}" -maxdepth 1 -name '*.pkg.tar.*' ! -name '*.sig' -type f | wc -l)

  printf '\n'
  printf '  %s%-34s%s %s\n' "${BOLD}" "Target Architecture:"       "${RESET}" "${mode_name}"
  printf '  %s%-34s%s %s\n' "${BOLD}" "Offline repo path:"         "${RESET}" "${OFFLINE_REPO_DIR}"
  printf '  %s%-34s%s %s\n' "${BOLD}" "Repository name:"           "${RESET}" "${REPO_NAME}"
  printf '  %s%-34s%s %s\n' "${BOLD}" "Active closure requested:"  "${RESET}" "${#WHITELIST_FILENAMES[@]}"
  printf '  %s%-34s%s %d\n' "${BOLD}" "Final files on disk:"       "${RESET}" "${pkg_count}"
  printf '  %s%-34s%s %s\n' "${BOLD}" "Total repo size:"           "${RESET}" "${repo_sz}"
  printf '\n%s%s[SUCCESS]%s Repository is primed for ISO integration.\n\n' "${BOLD}" "${GREEN}" "${RESET}"
}

# ==============================================================================
# SECTION 17 — MAIN
# ==============================================================================

main() {
  _parse_args "$@"
  _print_logo
  
  _prompt_build_mode
  _prompt_repo_dir

  OFFLINE_REPO_DIR="$(realpath -m -- "${OFFLINE_REPO_DIR}")"
  [[ "$OFFLINE_REPO_DIR" == "/" ]] && die "The root directory (/) is not permitted as a repository path."

  _check_dependencies
  _check_single_instance
  _setup_repo_dir
  _build_master_list
  _init_isolated_db
  _generate_whitelist_filenames
  _download_packages
  _prune_unneeded_packages
  _generate_repo_database
  _restore_permissions
  _print_summary
}

main "$@"
