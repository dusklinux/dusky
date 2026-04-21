#!/usr/bin/env bash
# MpV player configurator

# ==============================================================================
# Title:        Arch Linux MPV + UOSC + Thumbfast Auto-Config
# Description:  Automated, idempotent setup for MPV on Hyprland/Wayland.
# Version:      11.1 (Offline-Capable, Hardened)
# ==============================================================================

# Strict Mode
set -euo pipefail
shopt -s nullglob  # Globs that match nothing expand to nothing

# --- Configuration ---
readonly XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
readonly MPV_CONFIG_DIR="$XDG_CONFIG_HOME/mpv"
readonly SCRIPTS_DIR="$MPV_CONFIG_DIR/scripts"

readonly UOSC_URL="https://github.com/tomasklaen/uosc/releases/latest/download/uosc.zip"
readonly THUMBFAST_REPO="https://github.com/po5/thumbfast.git"

readonly DEPENDENCIES=(mpv unzip git curl yt-dlp mpv-mpris)

# --- Colors ---
if [[ -t 1 ]]; then
    readonly C_RESET=$'\033[0m'
    readonly C_GREEN=$'\033[1;32m'
    readonly C_BLUE=$'\033[1;34m'
    readonly C_RED=$'\033[1;31m'
    readonly C_YELLOW=$'\033[1;33m'
else
    readonly C_RESET='' C_GREEN='' C_BLUE='' C_RED='' C_YELLOW=''
fi

# --- Logging ---
log_info()    { printf "${C_BLUE}[INFO]${C_RESET} %s\n" "$1"; }
log_success() { printf "${C_GREEN}[OK]${C_RESET} %s\n" "$1"; }
log_warn()    { printf "${C_YELLOW}[WARN]${C_RESET} %s\n" "$1" >&2; }
log_error()   { printf "${C_RED}[ERROR]${C_RESET} %s\n" "$1" >&2; }

# --- Cleanup Trap ---
TEMP_DIR=""
cleanup() {
    if [[ -n "${TEMP_DIR:-}" && -d "$TEMP_DIR" ]]; then
        rm -rf -- "$TEMP_DIR"
    fi
}
trap cleanup EXIT

# --- Function: Smart File Update ---
install_config_file() {
    local target_path="$1"
    local new_content="$2"
    local temp_file="$TEMP_DIR/$(basename "$target_path").tmp"

    printf "%s\n" "$new_content" > "$temp_file"

    if [[ -f "$target_path" ]]; then
        if cmp -s -- "$target_path" "$temp_file"; then
            log_info "Configuration for $(basename "$target_path") is up to date."
            return
        fi
        local timestamp
        timestamp=$(date +%Y%m%d_%H%M%S)
        local backup="${target_path}.bak.${timestamp}"
        cp -- "$target_path" "$backup"
        log_warn "Changes detected. Backed up $(basename "$target_path") -> $(basename "$backup")"
    fi

    mv -- "$temp_file" "$target_path"
    log_success "Updated $(basename "$target_path")."
}

# ==============================================================================
# Main Execution
# ==============================================================================

log_info "Starting MPV Setup..."
TEMP_DIR=$(mktemp -d)

# --- Network Detection ---
NETWORK_AVAILABLE=true
# Fast, dependency-free Bash native TCP check to ensure connectivity
if ! timeout 2 bash -c '</dev/tcp/github.com/443' 2>/dev/null; then
    NETWORK_AVAILABLE=false
    log_warn "No internet connection detected. Operating in offline mode."
fi

# ------------------------------------------------------------------------------
# Step 1: Smart Dependency Check
# ------------------------------------------------------------------------------
log_info "Checking installed packages..."
MISSING_PKGS=()
for pkg in "${DEPENDENCIES[@]}"; do
    if ! pacman -Qi "$pkg" &>/dev/null; then
        MISSING_PKGS+=("$pkg")
    fi
done

if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
    log_warn "Missing packages detected: ${MISSING_PKGS[*]}"
    if [[ "$NETWORK_AVAILABLE" == false ]]; then
        log_warn "Cannot install packages in offline mode. Exiting gracefully."
        exit 0
    fi
    if ! sudo pacman -S --needed --noconfirm "${MISSING_PKGS[@]}"; then
        log_error "Failed to install packages."
        exit 1
    fi
    log_success "Dependencies installed."
else
    log_success "All dependencies already installed."
fi

# ------------------------------------------------------------------------------
# Step 2: Directory Setup
# ------------------------------------------------------------------------------
mkdir -p "$SCRIPTS_DIR"

# ------------------------------------------------------------------------------
# Step 3: Install UOSC
# ------------------------------------------------------------------------------
if [[ -d "$MPV_CONFIG_DIR/scripts/uosc" && -f "$MPV_CONFIG_DIR/script-opts/uosc.conf" ]]; then
    log_info "UOSC appears to be installed."
else
    if [[ "$NETWORK_AVAILABLE" == false ]]; then
        log_warn "Cannot download UOSC in offline mode. Exiting gracefully."
        exit 0
    fi
    if ! curl -fsSL --connect-timeout 30 --retry 3 --retry-delay 2 "$UOSC_URL" -o "$TEMP_DIR/uosc.zip"; then
        log_error "Failed to download UOSC."
        exit 1
    fi
    if ! unzip -qo "$TEMP_DIR/uosc.zip" -d "$MPV_CONFIG_DIR"; then
        log_error "Failed to unzip UOSC."
        exit 1
    fi
    log_success "UOSC installed."
fi

# ------------------------------------------------------------------------------
# Step 4: Install Thumbfast
# ------------------------------------------------------------------------------
log_info "Checking Thumbfast..."
TARGET_THUMBFAST_DIR="$SCRIPTS_DIR/thumbfast_repo"
TARGET_THUMBFAST_LINK="$SCRIPTS_DIR/thumbfast.lua"

if [[ -d "$TARGET_THUMBFAST_DIR/.git" ]]; then
    if [[ "$NETWORK_AVAILABLE" == true ]]; then
        if git -C "$TARGET_THUMBFAST_DIR" pull --quiet; then
            log_success "Thumbfast repo updated."
        else
            log_warn "Thumbfast update failed."
        fi
    else
        log_info "Offline mode: Skipping Thumbfast repository pull."
    fi
else
    if [[ "$NETWORK_AVAILABLE" == false ]]; then
        log_warn "Cannot clone Thumbfast in offline mode. Exiting gracefully."
        exit 0
    fi
    rm -rf -- "$TARGET_THUMBFAST_DIR"
    if ! git clone --quiet --depth 1 "$THUMBFAST_REPO" "$TARGET_THUMBFAST_DIR"; then
        log_error "Failed to clone Thumbfast."
        exit 1
    fi
    log_success "Thumbfast cloned."
fi

if [[ -f "$TARGET_THUMBFAST_DIR/thumbfast.lua" ]]; then
    if [[ -L "$TARGET_THUMBFAST_LINK" && "$(readlink -f "$TARGET_THUMBFAST_LINK")" == "$(readlink -f "$TARGET_THUMBFAST_DIR/thumbfast.lua")" ]]; then
        log_success "Thumbfast link is correct."
    else
        ln -sf -- "$TARGET_THUMBFAST_DIR/thumbfast.lua" "$TARGET_THUMBFAST_LINK"
        log_success "Thumbfast linked."
    fi
else
    log_error "Missing thumbfast.lua in repo."
    exit 1
fi

# ------------------------------------------------------------------------------
# Step 5: Intelligent Hardware Detection
# ------------------------------------------------------------------------------
log_info "Detecting Graphics Hardware..."

GPU_CONFIG=""
SELECTED_RENDER_NODE=""

# --- Logic: Find the target Render Node ---
ENV_DRM_DEVICE="${AQ_DRM_DEVICES:-}"
ENV_DRM_DEVICE="${ENV_DRM_DEVICE%%:*}"

if [[ -n "$ENV_DRM_DEVICE" && -e "$ENV_DRM_DEVICE" ]]; then
    log_info "Environment preference detected: $ENV_DRM_DEVICE"
    
    if [[ -L "/sys/class/drm/$(basename "$ENV_DRM_DEVICE")/device" ]]; then
        PREFERRED_PHYS_PATH=$(readlink -f "/sys/class/drm/$(basename "$ENV_DRM_DEVICE")/device")
        
        for dev in /dev/dri/renderD*; do
            if [[ ! -e "$dev" ]]; then continue; fi
            DEV_PHYS_PATH=$(readlink -f "/sys/class/drm/$(basename "$dev")/device")
            
            if [[ "$PREFERRED_PHYS_PATH" == "$DEV_PHYS_PATH" ]]; then
                SELECTED_RENDER_NODE="$dev"
                log_success "Mapped environment $ENV_DRM_DEVICE -> $SELECTED_RENDER_NODE"
                break
            fi
        done
    fi
fi

if [[ -z "$SELECTED_RENDER_NODE" ]]; then
    log_info "No environment preference found. Scanning for primary GPU..."
    for dev in /dev/dri/renderD*; do
        if [[ ! -e "$dev" ]]; then continue; fi
        
        sys_path="/sys/class/drm/$(basename "$dev")/device/driver"
        if [[ -L "$sys_path" ]]; then
            driver=$(basename "$(readlink -f "$sys_path")")
            if [[ "$driver" == "vfio-pci" ]]; then
                log_warn "Skipping VFIO device: $dev"
                continue
            fi
        fi
        
        SELECTED_RENDER_NODE="$dev"
        break
    done
fi

# --- Logic: Generate Config for Selected Node ---
if [[ -n "$SELECTED_RENDER_NODE" ]]; then
    sys_path="/sys/class/drm/$(basename "$SELECTED_RENDER_NODE")/device/driver"
    if [[ -L "$sys_path" ]]; then
        driver_name=$(basename "$(readlink -f "$sys_path")")
    else
        driver_name="unknown"
    fi

    log_info "Configuring MPV for: $SELECTED_RENDER_NODE ($driver_name)"

    if [[ "$driver_name" == "nvidia" ]]; then
        log_success "NVIDIA Driver detected."
        GPU_CONFIG="hwdec=auto"
    elif [[ "$driver_name" == "i915" || "$driver_name" == "amdgpu" || "$driver_name" == "xe" || "$driver_name" == "radeon" ]]; then
        log_success "Mesa/Legacy Driver ($driver_name) detected."
        GPU_CONFIG="hwdec=vaapi
vaapi-device=$SELECTED_RENDER_NODE"
    else
        log_info "Generic/Unknown Driver ($driver_name). Using safe defaults."
        GPU_CONFIG="hwdec=auto-safe"
    fi
else
    log_warn "No suitable GPU found (All VFIO?). Falling back to Software."
    GPU_CONFIG="hwdec=no"
fi

# ------------------------------------------------------------------------------
# Step 6: Generate mpv.conf
# ------------------------------------------------------------------------------
read -r -d '' MPV_CONF_CONTENT <<EOF || true
# --- General ---
keep-open=yes
save-position-on-quit=yes
autofit-larger=90%x90%

# --- UI / UOSC Requirements ---
osc=no
osd-bar=no
border=no

# --- Video / Wayland Optimization ---
vo=gpu
gpu-context=wayland

# --- Hardware Decoding (Auto-Generated) ---
$GPU_CONFIG

# --- Quality ---
scale=spline36
cscale=spline36
dscale=mitchell
correct-downscaling=yes
linear-downscaling=yes
dither-depth=auto

# --- Screenshots ---
screenshot-format=png
screenshot-directory=~/Pictures/Screenshots

# --- Thumbfast Worker Profile ---
[thumbfast]
network=no
audio=no
sub=no
video=no
hwdec=no 
profile=fast
EOF

install_config_file "$MPV_CONFIG_DIR/mpv.conf" "$MPV_CONF_CONTENT"

# ------------------------------------------------------------------------------
# Step 7: Generate input.conf
# ------------------------------------------------------------------------------
read -r -d '' INPUT_CONF_CONTENT <<'EOF' || true
# --- UOSC Bindings ---
SPACE        cycle pause; script-binding uosc/flash-pause-indicator
m            no-osd cycle mute; script-binding uosc/flash-volume
RIGHT        seek  5
LEFT         seek -5
Shift+RIGHT  seek  30; script-binding uosc/flash-timeline
Shift+LEFT   seek -30; script-binding uosc/flash-timeline
MENU         script-binding uosc/menu
MBTN_RIGHT   script-binding uosc/menu
TAB          script-binding uosc/toggle-ui
Ctrl+o       script-binding uosc/open-file

# --- Extra Utils ---
s            screenshot
EOF

install_config_file "$MPV_CONFIG_DIR/input.conf" "$INPUT_CONF_CONTENT"

# ------------------------------------------------------------------------------
# Completion
# ------------------------------------------------------------------------------
printf '\n%s====================================================%s\n' "$C_GREEN" "$C_RESET"
printf '%s   MPV Setup Complete!                             %s\n' "$C_GREEN" "$C_RESET"
printf '%s====================================================%s\n' "$C_GREEN" "$C_RESET"
log_info "Configuration finished."
