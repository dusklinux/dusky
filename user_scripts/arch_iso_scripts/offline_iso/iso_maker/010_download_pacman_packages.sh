#!/usr/bin/env bash
# ==============================================================================
# offline_pacman_packages.sh  —  v6.1 (Dynamic Pathing & Smart Permissions)
#
# Factory script: resolves the FULL transitive dependency closure of all
# defined package groups, downloads them into a local directory, then builds
# a valid pacman repository database for use in an offline Arch Linux ISO.
# ==============================================================================

# ==============================================================================
# SECTION 1 — PACKAGE ARRAYS
# ==============================================================================

declare -ar pkgs_offline=(
  "intel-ucode" "amd-ucode" "mkinitcpio" "gradle" "glaze" "python-cssselect" "gradle" "base" "base-devel" "python-lxml" "python-certifi" "python-charset-normalizer" "python-idna" "python-requests" "python-urllib3" "deno" "yt-dlp" "yt-dlp-ejs" "hunspell" "xf86-input-libinput" "xorg-xauth" "boost-libs"
 )

# Group 1: Graphics & Drivers
declare -ar pkgs_graphics=(
  "intel-media-driver" "vpl-gpu-rt" "mesa" "vulkan-intel" "mesa-utils" "intel-gpu-tools" "libva" "libva-utils" "vulkan-icd-loader" "vulkan-tools" "sof-firmware" "linux-firmware" "linux-headers" "acpi_call"
)

# Group 2: Hyprland Core
declare -ar pkgs_hyprland=(
  "hyprland" "uwsm" "xorg-xwayland" "xdg-desktop-portal-hyprland" "xdg-desktop-portal-gtk" "polkit" "hyprpolkitagent" "xdg-utils" "socat" "inotify-tools" "libnotify" "mako" "file"
)

# Group 3: GUI, Toolkits & Fonts
declare -ar pkgs_appearance=(
  "qt5-wayland" "qt6-wayland" "gtk3" "gtk4" "nwg-look" "qt5ct" "qt6ct" "qt6-svg" "qt6-multimedia-ffmpeg" "adw-gtk-theme" "upower" "plocate" "matugen" "ttf-font-awesome" "ttf-jetbrains-mono-nerd" "noto-fonts-emoji" "sassc" "python-packaging" "python" "python-evdev" "python-pyudev" "fontconfig" "papirus-icon-theme" "python-pyquery"
)

# Group 4: Desktop Experience
declare -ar pkgs_desktop=(
  "waybar" "awww" "hyprlock" "hypridle" "hyprsunset" "hyprpicker" "rofi" "libdbusmenu-qt5" "libdbusmenu-glib" "brightnessctl"
)

# Group 5: Audio & Bluetooth
declare -ar pkgs_audio=(
  "pipewire" "pipewire-alsa" "alsa-utils" "wireplumber" "pipewire-pulse" "playerctl" "bluez" "bluez-utils" "bluez-hid2hci" "bluez-libs" "bluez-obex" "blueman" "bluetui" "pavucontrol" "gst-plugins-base" "gst-libav" "gst-plugins-bad" "gst-plugins-good" "gst-plugins-ugly" "gst-plugin-pipewire" "libcanberra" "songrec" "sox"
)

# Group 6: Filesystem & Archives
declare -ar pkgs_filesystem=(
  "btrfs-progs" "compsize" "zram-generator" "udisks2" "udiskie" "dosfstools" "ntfs-3g" "xdg-user-dirs" "usbutils" "gnome-disk-utility" "unzip" "zip" "unrar" "7zip" "cpio" "file-roller" "rsync" "nfs-utils" "nilfs-utils" "smartmontools" "dmraid" "hdparm" "hwdetect" "lsscsi" "sg3_utils" "cpupower" "dust" "dkms"

  # thunar
  # "thunar" "thunar-archive-plugin" "thunar-volman" "tumbler" "ffmpegthumbnailer" "webp-pixbuf-loader" "poppler-glib" "gvfs" "gvfs-mtp" "gvfs-nfs" "gvfs-smb"

  # nemo
  "nemo" "nemo-fileroller" "file-roller" "gvfs" "gvfs-smb" "gvfs-mtp" "gvfs-gphoto2" "gvfs-nfs" "gvfs-afc" "gvfs-dnssd" "ffmpegthumbnailer" "webp-pixbuf-loader" "poppler-glib" "libgsf" "gnome-epub-thumbnailer" "resvg" "nemo-terminal" "nemo-python" "nemo-compare" "meld" "nemo-media-columns" "nemo-audio-tab" "nemo-image-converter" "nemo-emblems" "nemo-repairer" "nemo-share" "python-gobject" "dconf-editor" "xreader" "nemo-pastebin"
)

# Group 7: Network & Internet
declare -ar pkgs_network=(
  "networkmanager" "wireless-regdb" "iwd" "nm-connection-editor" "inetutils" "wget" "curl" "openssh" "firewalld" "vsftpd" "reflector" "bmon" "ethtool" "httrack" "wavemon" "firefox" "nss-mdns" "dnsmasq" "modemmanager" "usb_modeswitch"
)

# Group 8: Terminal & Shell
declare -ar pkgs_terminal=(
  "kitty" "foot" "zsh" "zsh-syntax-highlighting" "starship" "fastfetch" "bat" "eza" "fd" "yazi" "gum" "tree" "fzf" "less" "ripgrep" "expac" "zsh-autosuggestions" "iperf3" "pkgstats" "libqalculate" "moreutils" "zoxide" "opencode"
)

# Group 9: Development
declare -ar pkgs_dev=(
  "neovim" "git" "git-delta" "lazygit" "meson" "cmake" "clang" "uv" "rq" "jq" "pv" "bc" "viu" "chafa" "ueberzugpp" "ccache" "mold" "shellcheck" "fd" "ripgrep" "fzf" "shfmt" "stylua" "prettier" "tree-sitter-cli" "nano" "luarocks"
)

# Group 10: Multimedia
declare -ar pkgs_multimedia=(
  "ffmpeg" "mpv" "mpv-mpris" "satty" "swayimg" "resvg" "imagemagick" "libheif" "ffmpegthumbnailer" "grim" "slurp" "wl-clipboard" "wl-clip-persist" "cliphist" "tesseract-data-eng" "gpu-screen-recorder-ui" "ddcutil"
)

# Group 11: Sys Admin
declare -ar pkgs_sysadmin=(
  "btop" "htop" "dgop" "nvtop" "inxi" "sysstat" "sysbench" "logrotate" "acpid" "tlp" "tlp-pd" "tlp-rdw" "thermald" "powertop" "gdu" "iotop" "iftop" "lshw" "hwinfo" "dmidecode" "wev" "pacman-contrib" "gnome-keyring" "libsecret" "seahorse" "yad" "dysk" "fwupd" "perl" "accountsservice" "smartmontools" "pkgfile" "rebuild-detector" "accountsservice"
)

# Group 12: Gnome Utilities
declare -ar pkgs_gnome=(
  "snapshot" "cameractrls" "loupe" "gnome-text-editor" "gnome-calculator" "gnome-clocks"
)

# Group 13: Productivity
declare -ar pkgs_productivity=(
  "zathura" "zathura-pdf-mupdf" "cava"
)

# Group 14: Limine and snapshot
declare -ar pkgs_btrfs_snapshot=(
  "limine" "efibootmgr" "efitools" "kernel-modules-hook" "btrfs-progs" "snapper" "snap-pac" "jdk-openjdk" "mtools"
)

# ==============================================================================
# SECTION 2 — CONFIGURATION & DYNAMIC VARIABLES
# ==============================================================================

readonly REPO_NAME='archrepo'
readonly ISOLATED_DB_DIR='/tmp/offline_pacman_isolated_db'
readonly PACCACHE_KEEP=1

declare -g OFFLINE_REPO_DIR=''
declare -g INTERACTIVE_MODE=1
declare -g IS_ELEVATED=0

# ==============================================================================
# SECTION 3 — STRICT MODE
# ==============================================================================

set -Eeuo pipefail
shopt -s inherit_errexit

# ==============================================================================
# SECTION 4 — COLORS & LOGGING
# ==============================================================================

_setup_colors() {
  BOLD='' GREEN='' YELLOW='' RED='' CYAN='' MAGENTA='' DIM='' RESET=''
  if [[ -z "${NO_COLOR-}" ]] && [[ -t 1 ]] && command -v tput &>/dev/null; then
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
# SECTION 6 — ARGUMENT PARSING, INTERACTIVE UI, & ELEVATION
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
        [[ -z "${2-}" ]] && die "--path requires a directory argument."
        OFFLINE_REPO_DIR="$2"
        INTERACTIVE_MODE=0
        shift 2
        ;;
      --elevated)
        IS_ELEVATED=1
        shift
        ;;
      *)
        die "Unknown argument: $1\nUsage: $0 [--auto | --current | --path <dir>]"
        ;;
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

_ensure_root() {
  (( EUID == 0 )) && return 0
  command -v sudo &>/dev/null || die "Must run as root; 'sudo' not found."
  
  local script_path
  script_path=$(realpath -- "${BASH_SOURCE[0]}") || die "Cannot resolve script path."
  
  log_warn "Not running as root — elevating to execute in target directory..."
  exec sudo --preserve-env=TERM,NO_COLOR -- bash -- "$script_path" --elevated --path "${OFFLINE_REPO_DIR}"
}

# ==============================================================================
# SECTION 7 — PREFLIGHT CHECKS
# ==============================================================================

_check_dependencies() {
  log_info "Checking required tools"
  local -a required=(pacman repo-add paccache bc)
  local tool missing=0
  for tool in "${required[@]}"; do
    if command -v "$tool" &>/dev/null; then log_step "${tool}: $(command -v "$tool")"
    else log_err "Required tool missing: '${tool}'"; (( ++missing )) || true; fi
  done
  (( missing > 0 )) && die "${missing} required tool(s) missing — cannot continue."
  [[ -r /etc/arch-release ]] || die "Not running on Arch Linux."
  log_ok "All required tools are present."
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
  log_info "Scanning for package arrays (prefix: pkgs_)"
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

  log_ok "${group_count} groups | ${raw_count} raw | ${#MASTER_PKGS[@]} unique packages."
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
  log_info "Initialising isolated pacman sandbox"
  [[ -d "${ISOLATED_DB_DIR}" ]] && rm -rf -- "${ISOLATED_DB_DIR}"
  mkdir -p -- "${ISOLATED_DB_DIR}/local" "${ISOLATED_DB_DIR}/sync"

  grep -vE '^\s*(IgnorePkg|IgnoreGroup)\s*=' /etc/pacman.conf > "${ISOLATED_DB_DIR}/pacman.conf"
  
  log_step "Downloading sync databases into sandbox..."
  _pacman_isolated -Sy || die "Failed to sync package databases."
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
# SECTION 11 — WHITELIST GENERATION (BASE PACKAGE NAMES)
# ==============================================================================

declare -ga WHITELIST_PKGNAMES=()

_generate_whitelist_pkgnames() {
  log_info "Resolving full dependency closure (Whitelist)"
  
  local empty_cache tmp_out pacman_rc
  empty_cache=$(mktemp -d) || die "Cannot create temp cache."
  _register_temp "$empty_cache"

  tmp_out=$(mktemp) || die "Cannot create temp output."
  _register_temp "$tmp_out"

  set +e
  _pacman_isolated \
    -Sw --print --print-format '%n' \
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
    WHITELIST_PKGNAMES+=("$line")
  done

  (( ${#WHITELIST_PKGNAMES[@]} > 0 )) || die "Whitelist generation failed."
  log_ok "Closure resolved: ${#WHITELIST_PKGNAMES[@]} active base packages required."
}

# ==============================================================================
# SECTION 12 — PACKAGE DOWNLOAD (SMART SYNC)
# ==============================================================================

_download_packages() {
  log_info "Downloading packages → ${OFFLINE_REPO_DIR}"
  
  _pacman_isolated \
    -Sw --cachedir "${OFFLINE_REPO_DIR}" -- "${MASTER_PKGS[@]}" \
    || die "Download failed."

  local -i pkg_count
  pkg_count=$(find "${OFFLINE_REPO_DIR}" -maxdepth 1 -name '*.pkg.tar.*' ! -name '*.sig' -type f | wc -l)
  (( pkg_count > 0 )) || die "No packages found in repo after download."
  log_ok "Download sync complete. ${pkg_count} total file(s) on disk."
}

# ==============================================================================
# SECTION 13 — ORPHAN PRUNING (STRING DECONSTRUCTION)
# ==============================================================================

_prune_orphans() {
  log_info "Pruning true orphans from: ${OFFLINE_REPO_DIR}"

  local -A _wl_set=()
  for pn in "${WHITELIST_PKGNAMES[@]}"; do _wl_set[$pn]=1; done

  local -i del_count=0 del_bytes=0
  local -a pkg_files=()
  mapfile -t pkg_files < <(find "${OFFLINE_REPO_DIR}" -maxdepth 1 -name '*.pkg.tar.*' ! -name '*.sig' -type f)

  local filepath basename pkgname rest fsize
  for filepath in "${pkg_files[@]}"; do
    basename="${filepath##*/}"
    rest="${basename%%.pkg.tar.*}" 
    rest="${rest%-*}"              
    rest="${rest%-*}"              
    pkgname="${rest%-*}"           

    if [[ -z "${_wl_set[$pkgname]+_}" ]]; then
      fsize=$(stat -c '%s' -- "$filepath" 2>/dev/null) || fsize=0
      log_delete "orphan removed: ${pkgname}  (${basename})"
      rm -f -- "$filepath" "${filepath}.sig"
      
      (( del_bytes += fsize )) || true
      (( ++del_count )) || true
    fi
  done

  while IFS= read -r lone_sig; do
    local paired_pkg="${lone_sig%.sig}"
    if [[ ! -f "$paired_pkg" ]]; then
      log_delete "stale signature removed: ${lone_sig##*/}"
      rm -f -- "$lone_sig"
    fi
  done < <(find "${OFFLINE_REPO_DIR}" -maxdepth 1 -name '*.sig' -type f)

  if (( del_count > 0 )); then
    log_ok "Pruned ${del_count} orphaned file(s). Freed ~${del_bytes} bytes."
  else
    log_ok "No orphans found."
  fi
}

# ==============================================================================
# SECTION 14 — OLD-VERSION CACHE MANAGEMENT (PACCACHE)
# ==============================================================================

_prune_old_versions() {
  log_info "Removing old package versions (keeping ${PACCACHE_KEEP})"
  
  echo y | paccache \
    -r -k "${PACCACHE_KEEP}" -c "${OFFLINE_REPO_DIR}" \
    || die "paccache failed."
    
  log_ok "Cache pruned successfully."
}

# ==============================================================================
# SECTION 15 — REPOSITORY DATABASE GENERATION
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

  repo-add "${db_file}" "${pkg_files[@]}" >/dev/null || die "repo-add failed."
  log_ok "Database and symlinks created."
}

# ==============================================================================
# SECTION 16 — SMART PERMISSIONS RESTORATION
# ==============================================================================

_restore_permissions() {
  if [[ -n "${SUDO_UID-}" && -n "${SUDO_GID-}" ]]; then
    log_info "Restoring file ownership"
    log_step "Transferring files back to user: $(id -un "$SUDO_UID")"
    
    chown "${SUDO_UID}:${SUDO_GID}" "${OFFLINE_REPO_DIR}" 2>/dev/null || true
    
    # Use \( -type f -o -type l \) to catch regular files AND symlinks.
    # Pass -h to chown so it changes the ownership of the symlinks themselves.
    find "${OFFLINE_REPO_DIR}" -maxdepth 1 \( -type f -o -type l \) -exec chown -h "${SUDO_UID}:${SUDO_GID}" {} +
    
    log_ok "Ownership restored successfully."
  fi
}

# ==============================================================================
# SECTION 17 — SUMMARY
# ==============================================================================

_print_summary() {
  log_info "Build complete"
  local repo_sz
  repo_sz=$(du -sh -- "${OFFLINE_REPO_DIR}" 2>/dev/null | awk '{print $1}') || repo_sz='unknown'
  local -i pkg_count
  pkg_count=$(find "${OFFLINE_REPO_DIR}" -maxdepth 1 -name '*.pkg.tar.*' ! -name '*.sig' -type f | wc -l)

  printf '\n'
  printf '  %s%-34s%s %s\n' "${BOLD}" "Offline repo path:"         "${RESET}" "${OFFLINE_REPO_DIR}"
  printf '  %s%-34s%s %s\n' "${BOLD}" "Repository name:"           "${RESET}" "${REPO_NAME}"
  printf '  %s%-34s%s %s\n' "${BOLD}" "Active closure requested:"  "${RESET}" "${#WHITELIST_PKGNAMES[@]}"
  printf '  %s%-34s%s %d\n' "${BOLD}" "Final files on disk:"       "${RESET}" "${pkg_count}"
  printf '  %s%-34s%s %s\n' "${BOLD}" "Total repo size:"           "${RESET}" "${repo_sz}"
  printf '\n%s%s[SUCCESS]%s Repository is primed for ISO integration.\n\n' "${BOLD}" "${GREEN}" "${RESET}"
}

# ==============================================================================
# SECTION 18 — MAIN
# ==============================================================================

main() {
  _parse_args "$@"
  
  (( IS_ELEVATED )) || _print_logo
  
  _prompt_repo_dir

  OFFLINE_REPO_DIR="$(realpath -m -- "${OFFLINE_REPO_DIR}")"
  [[ "$OFFLINE_REPO_DIR" == "/" ]] && die "The root directory (/) is not permitted as a repository path."

  _ensure_root "$@"

  _check_dependencies
  _check_single_instance
  _setup_repo_dir
  _build_master_list
  _init_isolated_db
  _generate_whitelist_pkgnames
  _download_packages
  _prune_orphans
  _prune_old_versions
  _generate_repo_database
  _restore_permissions
  _print_summary
}

main "$@"
