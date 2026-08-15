#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.config/ags"
STAMP="$(date +%Y%m%d-%H%M%S)"
SRC_REAL="$(cd "$SRC_DIR" && pwd -P)"

MODE="interactive"
ACTIVATE=false
SKIP_DEPS=false
CHECK_ONLY=false
MAX_RETRIES=3

ADAPTIVE_SWITCH="${HOME}/user_scripts/bar/bar_switch.sh"
ADAPTIVE_STATE_DIR="${HOME}/.config/dusky/settings"
AUTOSTART_CANDIDATES=(
  "${HOME}/user_scripts/hypr/defaults/edit_here/autostart.lua"
  "${HOME}/.config/hypr/source/autostart.lua"
)

REQUIRED_COMMANDS=(ags gjs hyprctl grim)
GI_PROBE='imports.gi.versions.Astal="4.0"; const mods=["Astal","AstalHyprland","AstalApps","AstalMpris","AstalNetwork","AstalBluetooth","AstalWp","AstalBattery","AstalPowerProfiles"]; for (const name of mods) imports.gi[name];'
ADAPTIVE_PACKAGES=(
  gjs
  gtk4
  grim
  hyprland
  aylurs-gtk-shell-git
  libastal-git
  libastal-4-git
  libastal-apps-git
  libastal-battery-git
  libastal-bluetooth-git
  libastal-hyprland-git
  libastal-mpris-git
  libastal-network-git
  libastal-powerprofiles-git
  libastal-wireplumber-git
)

if [[ -t 2 ]]; then
  C_INFO='\033[0;34m'; C_OK='\033[0;32m'; C_WARN='\033[0;33m'
  C_ERR='\033[0;31m'; C_RST='\033[0m'; C_BOLD='\033[1m'
else
  C_INFO=''; C_OK=''; C_WARN=''; C_ERR=''; C_RST=''; C_BOLD=''
fi

log_info() { printf "${C_INFO}[INFO]${C_RST} %s\n" "$*" >&2; }
log_ok() { printf "${C_OK}[OK]${C_RST} %s\n" "$*" >&2; }
log_warn() { printf "${C_WARN}[WARN]${C_RST} %s\n" "$*" >&2; }
log_err() { printf "${C_ERR}[ERROR]${C_RST} %s\n" "$*" >&2; }

usage() {
  cat <<EOF
${C_BOLD}Adaptive Glass installer${C_RST}

Usage:
  install.sh [--interactive|--auto] [--activate|--no-activate] [options]

Options:
  --interactive       Ask before installing missing dependencies (default)
  --auto              Install missing dependencies without prompts
  --check             Only check dependencies and persistence; do not copy or activate
  --activate          Switch to Adaptive Glass after a successful install
  --no-activate       Install/repair files but keep the current bar active (default)
  --skip-deps         Skip dependency installation and checks
  --max-retries N     Retry dependency installation up to N times (default: 3)
  -h, --help          Show this message
EOF
}

parse_args() {
  while (($#)); do
    case "$1" in
      --interactive)
        MODE="interactive"
        ;;
      --auto|--autonomous)
        MODE="auto"
        ;;
      --check)
        CHECK_ONLY=true
        ;;
      --activate)
        ACTIVATE=true
        ;;
      --no-activate)
        ACTIVATE=false
        ;;
      --skip-deps)
        SKIP_DEPS=true
        ;;
      --max-retries)
        shift
        [[ "${1:-}" =~ ^[1-9][0-9]*$ ]] || {
          log_err "--max-retries requires a positive integer."
          exit 2
        }
        MAX_RETRIES="$1"
        ;;
      -h|--help|help)
        usage
        exit 0
        ;;
      *)
        log_err "Unknown argument: $1"
        usage >&2
        exit 2
        ;;
    esac
    shift
  done
}

missing_dependencies() {
  local missing=()
  local cmd

  for cmd in "${REQUIRED_COMMANDS[@]}"; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("command:$cmd")
  done

  if command -v gjs >/dev/null 2>&1; then
    gjs -c "$GI_PROBE" >/dev/null 2>&1 || missing+=("gi:astal-modules")
  else
    missing+=("gi:astal-modules")
  fi

  ((${#missing[@]} > 0)) && printf '%s\n' "${missing[@]}"
}

print_missing_dependencies() {
  local -a missing=("$@")

  log_err "Missing required Adaptive Glass dependencies:"
  printf '  - %s\n' "${missing[@]}" >&2
}

confirm_dependency_install() {
  local reply

  if [[ "$MODE" == "auto" ]]; then
    return 0
  fi

  printf 'Adaptive Glass needs missing packages before it can run.\n' >&2
  printf 'Install/repair dependencies now? [y/N] ' >&2
  read -r reply || return 1
  case "$reply" in
    y|Y|yes|YES)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

install_with_helper() {
  local helper="$1"
  shift

  if [[ "$MODE" == "auto" ]]; then
    "$helper" -S --needed --noconfirm "$@"
  else
    "$helper" -S --needed "$@"
  fi
}

install_dependencies_once() {
  if command -v paru >/dev/null 2>&1; then
    install_with_helper paru "${ADAPTIVE_PACKAGES[@]}"
    return $?
  fi

  if command -v yay >/dev/null 2>&1; then
    install_with_helper yay "${ADAPTIVE_PACKAGES[@]}"
    return $?
  fi

  log_err "No AUR helper found. Install paru or yay, then run this installer again."
  return 1
}

ensure_dependencies() {
  local -a missing=()
  local attempt

  if [[ "$SKIP_DEPS" == true ]]; then
    log_warn "Skipping dependency checks by request."
    return 0
  fi

  mapfile -t missing < <(missing_dependencies)
  if ((${#missing[@]} == 0)); then
    log_ok "Adaptive Glass dependencies are present."
    return 0
  fi

  if [[ "$CHECK_ONLY" == true ]]; then
    print_missing_dependencies "${missing[@]}"
    return 1
  fi

  print_missing_dependencies "${missing[@]}"
  confirm_dependency_install || {
    log_err "Dependency installation was not approved. Aborting."
    return 1
  }

  for ((attempt = 1; attempt <= MAX_RETRIES; attempt++)); do
    log_info "Installing Adaptive Glass dependencies (attempt ${attempt}/${MAX_RETRIES})..."
    if ! install_dependencies_once; then
      log_warn "Package transaction failed."
    fi

    mapfile -t missing < <(missing_dependencies)
    if ((${#missing[@]} == 0)); then
      log_ok "Dependencies verified after attempt ${attempt}."
      return 0
    fi

    print_missing_dependencies "${missing[@]}"
  done

  log_err "Dependencies still missing after ${MAX_RETRIES} attempt(s). Aborting."
  return 1
}

ensure_persistence() {
  local candidate
  local verified=false

  mkdir -p "$ADAPTIVE_STATE_DIR"

  if [[ ! -x "$ADAPTIVE_SWITCH" ]]; then
    log_err "Bar switcher is missing or not executable: $ADAPTIVE_SWITCH"
    return 1
  fi

  for candidate in "${AUTOSTART_CANDIDATES[@]}"; do
    if [[ -f "$candidate" ]] && grep -F 'bar_switch.sh start' "$candidate" >/dev/null 2>&1; then
      verified=true
      break
    fi
  done

  if [[ "$verified" == true ]]; then
    log_ok "Startup restore path already uses bar_switch.sh start."
    return 0
  fi

  candidate="${AUTOSTART_CANDIDATES[0]}"
  mkdir -p "$(dirname -- "$candidate")"
  cat >> "$candidate" <<'EOF'

-- Adaptive Glass persistent bar restore
hl.on("hyprland.start", function()
    hl.exec_cmd("uwsm-app -- $HOME/user_scripts/bar/bar_switch.sh start")
end)
EOF

  log_ok "Added persistent bar restore hook to: $candidate"
}

copy_sources() {
  local dest_real=""
  local in_place=false
  local backup=""

  mkdir -p "$HOME/.config"

  if [[ -d "$DEST" ]]; then
    dest_real="$(cd "$DEST" && pwd -P)"
  fi

  if [[ "$dest_real" == "$SRC_REAL" ]]; then
    in_place=true
  fi

  if [[ "$in_place" == false && -e "$DEST" && ! -f "$DEST/.adaptive-glass-managed" ]]; then
    backup="$HOME/.config/ags.backup-$STAMP"
    log_warn "Existing AGS configuration detected; moving it to: $backup"
    mv "$DEST" "$backup"
  fi

  if [[ "$in_place" == false ]]; then
    mkdir -p "$DEST"
    find "$DEST" -mindepth 1 -maxdepth 1 ! -name '.adaptive-glass-managed' -exec rm -rf {} +
    cp -a \
      "$SRC_DIR/app.tsx" \
      "$SRC_DIR/style.css" \
      "$SRC_DIR/README.md" \
      "$SRC_DIR/components" \
      "$SRC_DIR/lib" \
      "$SRC_DIR/styles" \
      "$SRC_DIR/scripts" \
      "$DEST/"
  fi

  touch "$DEST/.adaptive-glass-managed"
  chmod +x "$DEST/app.tsx"
  chmod +x "$DEST/scripts/"*.sh
  log_ok "Adaptive Glass files installed at: $DEST"
}

generate_types() {
  if [[ "$SKIP_DEPS" == true ]]; then
    log_warn "Skipping AGS type generation because dependency checks were skipped."
    return 0
  fi

  if command -v ags >/dev/null 2>&1; then
    log_info "Generating AGS/GI type definitions..."
    if ags types -u -d "$DEST"; then
      log_ok "AGS type definitions generated."
    else
      log_warn "Type generation failed; AGS can still attempt to run the config."
    fi
  fi
}

activate_adaptive_glass() {
  if [[ "$ACTIVATE" != true ]]; then
    log_info "Not switching bars. Use Control Center or bar_switch.sh when ready."
    return 0
  fi

  log_info "Switching to Adaptive Glass..."
  "$ADAPTIVE_SWITCH" adaptive-glass
}

main() {
  parse_args "$@"

  ensure_dependencies

  if [[ "$CHECK_ONLY" == true ]]; then
    ensure_persistence
    log_ok "Adaptive Glass dependency and persistence checks passed."
    return 0
  fi

  copy_sources
  ensure_persistence
  generate_types
  activate_adaptive_glass

  cat <<MSG

Adaptive Glass installed.

Waybar was not changed or removed.
Managed AGS marker:
  $DEST/.adaptive-glass-managed

Switch any time with:
  $ADAPTIVE_SWITCH toggle

Direct launch command:
  ags run "$HOME/.config/ags/app.tsx"

MSG
}

main "$@"
