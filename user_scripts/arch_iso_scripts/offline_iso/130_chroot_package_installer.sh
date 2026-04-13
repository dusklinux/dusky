#!/usr/bin/env bash
# This script installs ALL PACKAGES from the Offline Repository. Inspect it manually to remove/add anything you want.
# It installs packages only. It does not enable systemd services automatically.
# ------------------------------------------------------------------------------
# Arch Linux / Hyprland / UWSM - Elite System Installer (v3.4 - Hardened Offline)
# ------------------------------------------------------------------------------

# --- 1. CONFIGURATION ---

declare -ar pkgs_offline=(
  "intel-ucode" "amd-ucode" "mkinitcpio" "gradle" "glaze" "python-cssselect" "gradle" "base" "base-devel" "python-lxml" "python-certifi" "python-charset-normalizer" "python-idna" "python-requests" "python-urllib3" "deno" "yt-dlp" "yt-dlp-ejs" "hunspell" "xf86-input-libinput" "xorg-server" "xorg-xauth" "boost-libs"
 )

# Group 1: Graphics & Drivers
declare -ar pkgs_graphics=(
  "intel-media-driver" "vpl-gpu-rt" "mesa" "vulkan-intel" "mesa-utils" "intel-gpu-tools" "libva" "libva-utils" "vulkan-icd-loader" "vulkan-tools" "sof-firmware" "linux-firmware" "linux-headers" "acpi_call"
)

# Group 2: Hyprland Core
declare -ar pkgs_hyprland=(
  "hyprland" "uwsm" "xorg-xwayland" "xdg-desktop-portal-hyprland" "xdg-desktop-portal-gtk" "xorg-xhost" "polkit" "hyprpolkitagent" "xdg-utils" "socat" "inotify-tools" "libnotify" "file"
)

# Group 3: GUI, Toolkits & Fonts
declare -ar pkgs_appearance=(
  "qt5-wayland" "qt6-wayland" "gtk3" "gtk4" "nwg-look" "qt5ct" "qt6ct" "qt6-svg" "qt6-multimedia-ffmpeg" "adw-gtk-theme" "upower" "plocate" "matugen" "ttf-font-awesome" "ttf-jetbrains-mono-nerd" "noto-fonts-emoji" "sassc" "python-packaging" "python" "fontconfig" "papirus-icon-theme"
)

# Group 4: Desktop Experience
declare -ar pkgs_desktop=(
  "waybar" "awww" "hyprlock" "hypridle" "hyprsunset" "hyprpicker" "swaync" "swayosd" "rofi" "libdbusmenu-qt5" "libdbusmenu-glib" "brightnessctl"
)

# Group 5: Audio & Bluetooth
declare -ar pkgs_audio=(
  "pipewire" "pipewire-alsa" "alsa-utils" "wireplumber" "pipewire-pulse" "playerctl" "bluez" "bluez-utils" "bluez-hid2hci" "bluez-libs" "bluez-obex" "blueman" "bluetui" "pavucontrol" "gst-plugins-base" "gst-libav" "gst-plugins-bad" "gst-plugins-good" "gst-plugins-ugly" "gst-plugin-pipewire" "libcanberra" "songrec" "sox"
)

# Group 6: Filesystem & Archives
declare -ar pkgs_filesystem=(
  "btrfs-progs" "compsize" "zram-generator" "udisks2" "udiskie" "dosfstools" "ntfs-3g" "xdg-user-dirs" "usbutils" "gnome-disk-utility" "unzip" "zip" "unrar" "7zip" "cpio" "file-roller" "rsync" "nfs-utils" "nilfs-utils" "smartmontools" "dmraid" "hdparm" "hwdetect" "lsscsi" "sg3_utils" "cpupower" "dust" "fcitx5" "fcitx5-gtk" "fcitx5-qt" "dkms"
  "nemo" "nemo-fileroller" "file-roller" "gvfs" "gvfs-smb" "gvfs-mtp" "gvfs-gphoto2" "gvfs-nfs" "gvfs-afc" "gvfs-dnssd" "ffmpegthumbnailer" "webp-pixbuf-loader" "poppler-glib" "libgsf" "gnome-epub-thumbnailer" "resvg" "nemo-terminal" "nemo-python" "nemo-compare" "meld" "nemo-media-columns" "nemo-audio-tab" "nemo-image-converter" "nemo-emblems" "nemo-repairer" "nemo-share" "python-gobject" "dconf-editor" "xreader" "nemo-pastebin"
)

# Group 7: Network & Internet
declare -ar pkgs_network=(
  "networkmanager" "wireless-regdb" "iwd" "nm-connection-editor" "inetutils" "wget" "curl" "openssh" "firewalld" "vsftpd" "reflector" "bmon" "ethtool" "httrack" "wavemon" "firefox" "network-manager-applet" "nss-mdns" "dnsmasq" "modemmanager" "usb_modeswitch"
)

# Group 8: Terminal & Shell
declare -ar pkgs_terminal=(
  "kitty" "foot" "zsh" "zsh-syntax-highlighting" "starship" "fastfetch" "bat" "eza" "fd" "yazi" "gum" "tree" "fzf" "less" "ripgrep" "expac" "zsh-autosuggestions" "iperf3" "pkgstats" "libqalculate" "moreutils" "zoxide"
)

# Group 9: Development
declare -ar pkgs_dev=(
  "neovim" "git" "git-delta" "lazygit" "meson" "cmake" "clang" "uv" "rq" "jq" "pv" "bc" "viu" "chafa" "ueberzugpp" "ccache" "mold" "shellcheck" "fd" "ripgrep" "fzf" "shfmt" "stylua" "prettier" "tree-sitter-cli" "nano" "luarocks"
)

# Group 10: Multimedia
declare -ar pkgs_multimedia=(
  "ffmpeg" "mpv" "mpv-mpris" "satty" "swayimg" "resvg" "imagemagick" "libheif" "ffmpegthumbnailer" "grim" "slurp" "wl-clipboard" "wl-clip-persist" "cliphist" "tesseract-data-eng" "gpu-screen-recorder-ui"
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

declare -ar GROUP_LABELS=(
  "Graphics & Drivers"
  "Hyprland Core"
  "GUI Appearance"
  "Desktop Experience"
  "Audio & Bluetooth"
  "Filesystem Tools"
  "Networking"
  "Terminal & CLI"
  "Development"
  "Multimedia"
  "System Admin"
  "Gnome Utilities"
  "Productivity"
  "Boot Loader & Snapshot"
)

declare -ar GROUP_ARRAYS=(
  pkgs_offline
  pkgs_graphics
  pkgs_hyprland
  pkgs_appearance
  pkgs_desktop
  pkgs_audio
  pkgs_filesystem
  pkgs_network
  pkgs_terminal
  pkgs_dev
  pkgs_multimedia
  pkgs_sysadmin
  pkgs_gnome
  pkgs_productivity
  pkgs_btrfs_snapshot
)

# --- 2. EARLY ROOT CHECK ---

if (( EUID != 0 )); then
  sudo_bin=$(command -v sudo) || {
    printf 'sudo is required to elevate privileges.\n' >&2
    exit 1
  }

  realpath_bin=$(command -v realpath) || {
    printf 'realpath is required to resolve the script path.\n' >&2
    exit 1
  }

  script_source=${BASH_SOURCE[0]-}
  if [[ -z $script_source || ! -r $script_source ]]; then
    printf 'Unable to resolve the script path. Run this script from a regular file.\n' >&2
    exit 1
  fi

  script_path=$("$realpath_bin" -- "$script_source") || {
    printf 'Unable to resolve the script path.\n' >&2
    exit 1
  }

  printf 'Elevating privileges...\n'
  exec "$sudo_bin" --preserve-env=TERM,NO_COLOR -- bash -- "$script_path" "$@"
fi

# --- 3. SAFETY ---

set -Eeuo pipefail
shopt -s inherit_errexit

# --- 4. UI ---

BOLD=''
GREEN=''
YELLOW=''
RED=''
CYAN=''
RESET=''

if [[ -z ${NO_COLOR-} ]] && [[ -n ${TERM-} ]] && [[ -t 1 ]] && command -v tput >/dev/null 2>&1 && tput colors >/dev/null 2>&1; then
  BOLD=$(tput bold)
  GREEN=$(tput setaf 2)
  YELLOW=$(tput setaf 3)
  RED=$(tput setaf 1)
  CYAN=$(tput setaf 6)
  RESET=$(tput sgr0)
fi

readonly BOLD GREEN YELLOW RED CYAN RESET

HAS_TTY=0
if [[ -t 0 && -t 1 ]]; then
  HAS_TTY=1
fi
readonly HAS_TTY

readonly PACMAN_DB_LOCK='/var/lib/pacman/db.lck'
readonly PACMAN_LOCK_TIMEOUT=300
readonly SCRIPT_LOCK_FILE='/run/lock/elite-system-installer.lock'

declare -gi SCRIPT_LOCK_FD=-1
declare -ga FAILED_GROUPS=()
declare -ga FAILED_PACKAGES=()

print_info() {
  local msg="$*"
  printf '\n%s:: %s%s\n' "${BOLD}${CYAN}" "$msg" "$RESET"
}

print_ok() {
  local msg="$*"
  printf '%s[OK] %s%s\n' "$GREEN" "$msg" "$RESET"
}

print_warn() {
  local msg="$*"
  printf '%s[!] %s%s\n' "$YELLOW" "$msg" "$RESET"
}

print_error() {
  local msg="$*"
  printf '%s[X] %s%s\n' "$RED" "$msg" "$RESET" >&2
}

die() {
  print_error "$*"
  exit 1
}

on_err() {
  local rc=$?
  local line="${1:-?}"
  local cmd="${2:-?}"
  print_error "Unexpected error at line ${line}: ${cmd}"
  exit "$rc"
}

trap 'on_err "$LINENO" "$BASH_COMMAND"' ERR

# --- 5. CORE HELPERS ---

ensure_arch_environment() {
  [[ -r /etc/arch-release ]] || die "This script is for Arch Linux only."
  command -v pacman >/dev/null 2>&1 || die "pacman is required."
  command -v pacman-key >/dev/null 2>&1 || die "pacman-key is required."
  command -v flock >/dev/null 2>&1 || die "flock is required."
  command -v mktemp >/dev/null 2>&1 || die "mktemp is required."
  command -v mkfifo >/dev/null 2>&1 || die "mkfifo is required."
  command -v tee >/dev/null 2>&1 || die "tee is required."
}

validate_group_configuration() {
  local labels_count arrays_count array_name

  labels_count=${#GROUP_LABELS[@]}
  arrays_count=${#GROUP_ARRAYS[@]}

  (( labels_count == arrays_count )) || die "GROUP_LABELS and GROUP_ARRAYS must have the same number of entries."

  for array_name in "${GROUP_ARRAYS[@]}"; do
    declare -p "$array_name" >/dev/null 2>&1 || die "Package array not found: ${array_name}"
  done
}

acquire_script_lock() {
  mkdir -p -- /run/lock
  exec {SCRIPT_LOCK_FD}>"$SCRIPT_LOCK_FILE"
  flock -n "$SCRIPT_LOCK_FD" || die "Another instance of this script is already running."
}

run_pacman() {
  local start_time=$SECONDS
  local warned=0
  local rc=0
  local tee_pid=0
  local temp_dir=''
  local stderr_file=''
  local stderr_pipe=''

  while :; do
    temp_dir=$(mktemp -d) || die "Failed to create a temporary directory."
    stderr_file="${temp_dir}/stderr.log"
    stderr_pipe="${temp_dir}/stderr.pipe"

    mkfifo -- "$stderr_pipe" || {
      rm -rf -- "$temp_dir"
      die "Failed to create a temporary pipe."
    }

    tee -- "$stderr_file" <"$stderr_pipe" >&2 &
    tee_pid=$!

    if command env LC_ALL=C pacman "$@" 2>"$stderr_pipe"; then
      rc=0
    else
      rc=$?
    fi

    rm -f -- "$stderr_pipe"
    wait "$tee_pid" || true

    case "$rc" in
      0)
        rm -rf -- "$temp_dir"
        return 0
        ;;
      130|143)
        rm -rf -- "$temp_dir"
        print_error "Pacman operation interrupted."
        exit "$rc"
        ;;
    esac

    if grep -Fqs 'unable to lock database' -- "$stderr_file"; then
      rm -rf -- "$temp_dir"

      if (( warned == 0 )); then
        print_warn "Pacman database is locked. Waiting up to ${PACMAN_LOCK_TIMEOUT}s..."
        warned=1
      fi

      if (( SECONDS - start_time >= PACMAN_LOCK_TIMEOUT )); then
        die "Timed out waiting for pacman database lock: ${PACMAN_DB_LOCK}"
      fi

      sleep 2
      continue
    fi

    rm -rf -- "$temp_dir"
    return "$rc"
  done
}

ensure_keyring() {
  local keyring_dir='/etc/pacman.d/gnupg'

  print_info "Checking Arch keyring"

  if [[ -s ${keyring_dir}/trustdb.gpg ]] && { [[ -s ${keyring_dir}/pubring.kbx ]] || [[ -s ${keyring_dir}/pubring.gpg ]]; }; then
    print_ok "Arch keyring already initialized."
    return 0
  fi

  print_warn "Pacman keyring is not initialized. Initializing now..."
  pacman-key --init
  pacman-key --populate archlinux
  print_ok "Arch keyring initialized."
}

install_group() {
  local group_name="$1"
  local array_name="$2"
  local -n pkgs_ref="$array_name"

  local -a pkgs=()
  local -A seen=()
  local pkg
  local fail_count=0

  for pkg in "${pkgs_ref[@]}"; do
    [[ -n $pkg ]] || continue

    if [[ -n ${seen[$pkg]+_} ]]; then
      continue
    fi

    seen[$pkg]=1
    pkgs+=("$pkg")
  done

  (( ${#pkgs[@]} > 0 )) || return 0

  printf '\n%s:: Processing Group: %s%s\n' "${BOLD}${CYAN}" "$group_name" "$RESET"

  if run_pacman --sync --needed --noconfirm -- "${pkgs[@]}"; then
    print_ok "Batch installation successful."
    return 0
  fi

  print_warn "Batch transaction failed. Retrying individually..."

  for pkg in "${pkgs[@]}"; do
    if pacman -Qq -- "$pkg" >/dev/null 2>&1; then
      printf '  %s[=] Already installed:%s %s\n' "$CYAN" "$RESET" "$pkg"
      continue
    fi

    if (( HAS_TTY )); then
      if run_pacman --sync --needed --noconfirm -- "$pkg" >/dev/null 2>&1; then
        printf '  %s[+] Installed:%s %s\n' "$GREEN" "$RESET" "$pkg"
        continue
      fi

      printf '  %s[?] Intervention needed:%s %s\n' "$YELLOW" "$RESET" "$pkg"
      if run_pacman --sync --needed -- "$pkg"; then
        printf '  %s[+] Installed (manual):%s %s\n' "$GREEN" "$RESET" "$pkg"
        continue
      fi
    else
      if run_pacman --sync --needed --noconfirm -- "$pkg"; then
        printf '  %s[+] Installed:%s %s\n' "$GREEN" "$RESET" "$pkg"
        continue
      fi

      printf '  %s[?] No TTY available for interactive retry:%s %s\n' "$YELLOW" "$RESET" "$pkg"
    fi

    printf '  %s[X] Failed:%s %s\n' "$RED" "$RESET" "$pkg" >&2
    FAILED_PACKAGES+=("${group_name} :: ${pkg}")
    (( ++fail_count ))
  done

  if (( fail_count > 0 )); then
    FAILED_GROUPS+=("$group_name")
    print_warn "Group completed with ${fail_count} failure(s)."
  else
    print_ok "Recovery successful. All packages installed."
  fi
}

print_summary() {
  local group item

  if (( ${#FAILED_PACKAGES[@]} == 0 )); then
    printf '\n%s%s:: INSTALLATION COMPLETE ::%s\n' "$BOLD" "$GREEN" "$RESET"
    printf 'Reboot is recommended to load newly installed drivers.\n'
    printf 'This script installs packages only; it does not enable services automatically.\n'
    return 0
  fi

  printf '\n%s%s:: INSTALLATION FINISHED WITH FAILURES ::%s\n' "$BOLD" "$YELLOW" "$RESET"
  printf 'Failed groups: %d\n' "${#FAILED_GROUPS[@]}"
  printf 'Failed packages: %d\n' "${#FAILED_PACKAGES[@]}"

  if (( ${#FAILED_GROUPS[@]} > 0 )); then
    printf '\n%sGroups with failures:%s\n' "$BOLD" "$RESET"
    for group in "${FAILED_GROUPS[@]}"; do
      printf '  %s\n' "$group"
    done
  fi

  printf '\n%sFailed packages:%s\n' "$BOLD" "$RESET"
  for item in "${FAILED_PACKAGES[@]}"; do
    printf '  %s\n' "$item"
  done

  return 1
}

main() {
  local i

  ensure_arch_environment
  validate_group_configuration
  acquire_script_lock
  ensure_keyring

  for i in "${!GROUP_LABELS[@]}"; do
    install_group "${GROUP_LABELS[i]}" "${GROUP_ARRAYS[i]}"
  done

  if print_summary; then
    exit 0
  else
    exit 1
  fi
}

main "$@"
