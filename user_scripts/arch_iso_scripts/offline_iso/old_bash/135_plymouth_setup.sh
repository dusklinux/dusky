#!/usr/bin/env bash
# Arch Linux (EFI + Btrfs root) | Dusky Graphical Boot & LUKS Setup
# CHROOT DEPLOYMENT EDITION - 100% SELF-CONTAINED (SYNTHETIC GEOMETRY)
# ORCHESTRATOR ALIGNED: Defers initramfs generation to Phase 158.

set -Eeuo pipefail
export LC_ALL=C

# --- Dynamic Path Resolution ---
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

# --- Configuration ---
readonly THEME_NAME="dusky"
readonly THEME_DIR="/usr/share/plymouth/themes/${THEME_NAME}"
readonly MKINITCPIO_CONF="/etc/mkinitcpio.conf.d/10-arch-btrfs-luks.conf"

# --- Helpers ---
fatal() { printf '\033[1;31m[FATAL]\033[0m %s\n' "$1" >&2; exit 1; }
info() { printf '\033[1;32m[INFO]\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$1" >&2; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || fatal "Required command not found: $1"
}

# --- Pre-flight Checks ---
if (( EUID != 0 )); then
    fatal "Deployment halted: Root privileges are strictly required to modify system paths and mkinitcpio hooks."
fi

info "Validating base dependencies..."
require_cmd pacman
require_cmd sed
require_cmd grep
require_cmd base64

# --- Execution ---
info "Ensuring Plymouth is installed..."
if ! pacman -Q plymouth >/dev/null 2>&1; then
    # Strict Offline Mode: Respects the airlock established by orchestrator
    if ! pacman -S --needed --noconfirm plymouth; then
        printf "\n\033[1;31m========================================================================\033[0m\n" >&2
        printf "\033[1;31m[CRITICAL ARCHITECTURAL FAILURE]\033[0m\n" >&2
        printf "The offline installation of 'plymouth' failed.\n" >&2
        printf "RESOLUTION: You MUST include 'plymouth' in your 070_pacstrap payload.\n" >&2
        printf "\033[1;31m========================================================================\033[0m\n\n" >&2
        exit 1
    fi
fi

info "Validating Plymouth binaries..."
require_cmd plymouth-set-default-theme

info "Deploying custom self-contained theme: $THEME_NAME..."
mkdir -p "$THEME_DIR"

# Auto-generate a 1x1 white pixel using base64. 
# We use this single pixel to synthesize the entire progress bar geometry.
base64 -d > "$THEME_DIR/fill.png" << 'EOF'
iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=
EOF

# Generate .plymouth configuration
cat << EOF > "${THEME_DIR}/${THEME_NAME}.plymouth"
[Plymouth Theme]
Name=Dusky
Description=Dusky elegant synthetic graphical LUKS prompt and splash.
ModuleName=script

[script]
ImageDir=${THEME_DIR}
ScriptFile=${THEME_DIR}/${THEME_NAME}.script
ConsoleLogBackgroundColor=0x000000
EOF

# Generate the flawless synthetic Plymouth script logic
cat << 'EOF' > "${THEME_DIR}/${THEME_NAME}.script"
// --- Window Background (Pitch Black) ---
Window.SetBackgroundTopColor(0.0, 0.0, 0.0);
Window.SetBackgroundBottomColor(0.0, 0.0, 0.0);

global.password_mode = 0;
screen_w = Window.GetWidth();
screen_h = Window.GetHeight();

// FAILSAFE: Prevent division by zero if DRM is exceptionally slow to report geometry during early boot
if (screen_w == 0) screen_w = 1920;
if (screen_h == 0) screen_h = 1080;

// --- DUSKY Text Logo (Lowercase, Smaller, Thinner) ---
logo.image = Image.Text("dusky", 0.9, 0.9, 0.9, 1.0, "Sans Light 32");
logo.sprite = Sprite(logo.image);
logo.x = screen_w / 2 - logo.image.GetWidth() / 2;
logo.y = screen_h / 2 - logo.image.GetHeight() / 2 - 40;
logo.sprite.SetPosition(logo.x, logo.y, 10);

// =========================================================================
// SYNTHETIC PROGRESS BAR (No external assets required)
// =========================================================================
global.bar_width = 150;
global.bar_height = 4; // Extremely sleek and minimal 4-pixel height

// --- Background Track (Stretched pixel with 20% opacity) ---
track.image = Image("fill.png").Scale(global.bar_width, global.bar_height);
track.sprite = Sprite(track.image);
track.x = screen_w / 2 - global.bar_width / 2;
track.y = logo.y + logo.image.GetHeight() + 50; 
track.sprite.SetPosition(track.x, track.y, 10);
track.sprite.SetOpacity(0.2); // Creates a subtle dark grey line

// --- Foreground Fill (Dynamic stretch) ---
fill.original_image = Image("fill.png");
fill.sprite = Sprite();
fill.sprite.SetPosition(track.x, track.y, 11);
fill.sprite.SetOpacity(0.9);

global.current_progress = 0.0;
global.target_progress = 0.0;
global.last_fill_w = 0; // Optimization tracker

// --- Smooth Progress Bar Animation Loop ---
fun refresh_callback () {
    // Smoothly ease the bar towards the target progress
    if (global.current_progress < global.target_progress) {
        global.current_progress += 0.005; // Minimum guaranteed speed
        global.current_progress += (global.target_progress - global.current_progress) * 0.1; // Ease in
    }
    if (global.current_progress > 1.0) global.current_progress = 1.0;

    if (global.password_mode == 0) {
        fill_w = Math.Int(global.bar_width * global.current_progress);
        if (fill_w < 1) fill_w = 1;

        // OPTIMIZATION: Only hit the rendering engine to scale if the width actually changed
        if (global.last_fill_w != fill_w) {
            fill_img = fill.original_image.Scale(fill_w, global.bar_height);
            fill.sprite.SetImage(fill_img);
            global.last_fill_w = fill_w;
        }
        
        // Exact mathematical overlay on top of the track
        fill.sprite.SetPosition(track.x, track.y, 11);
    }
}
Plymouth.SetRefreshFunction(refresh_callback);

fun progress_callback(duration, progress) {
    if (progress > global.target_progress) {
        global.target_progress = progress;
    }
}
Plymouth.SetBootProgressFunction(progress_callback);

// --- Systemd Live Logs (One line at a time) ---
status_sprite = Sprite();
fun status_callback(status) {
    if (global.password_mode == 0) {
        status_img = Image.Text(status, 0.4, 0.4, 0.4, 1.0, "Monospace 10"); 
        status_sprite.SetImage(status_img);
        status_sprite.SetX(screen_w / 2 - status_img.GetWidth() / 2);
        status_sprite.SetY(screen_h * 0.90);
        status_sprite.SetOpacity(1);
    }
}
Plymouth.SetUpdateStatusFunction(status_callback);

// --- Password Prompt ---
prompt_sprite = Sprite();
bullets_sprite = Sprite();

fun display_password_callback(prompt_ignored, bullets) {
    global.password_mode = 1;

    // Fade out progress bar and logs while asking for password
    fill.sprite.SetOpacity(0);
    track.sprite.SetOpacity(0);
    status_sprite.SetOpacity(0);

    // Hardcode "unlock" 
    prompt_img = Image.Text("unlock", 0.7, 0.7, 0.7, 1.0, "Sans Light 16");
    prompt_sprite.SetImage(prompt_img);
    prompt_sprite.SetX(screen_w / 2 - prompt_img.GetWidth() / 2);
    prompt_sprite.SetY(logo.y + logo.image.GetHeight() + 40);
    prompt_sprite.SetOpacity(1);

    bullets_str = "";
    for (i = 0; i < bullets; i++) {
        bullets_str += "*"; // Standard asterisks
    }
    if (bullets == 0) bullets_str = " "; 

    // Reduced asterisk size to match the sleek design
    bullets_img = Image.Text(bullets_str, 1.0, 1.0, 1.0, 1.0, "Monospace 16");
    bullets_sprite.SetImage(bullets_img);
    bullets_sprite.SetX(screen_w / 2 - bullets_img.GetWidth() / 2);
    
    // Tightened spacing between text and asterisks
    bullets_sprite.SetY(prompt_sprite.GetY() + 25);
    bullets_sprite.SetOpacity(1);
}

fun display_normal_callback() {
    global.password_mode = 0;
    prompt_sprite.SetOpacity(0);
    bullets_sprite.SetOpacity(0);

    // Restore elements
    track.sprite.SetOpacity(0.2);
    fill.sprite.SetOpacity(0.9);
    status_sprite.SetOpacity(1);
}

Plymouth.SetDisplayPasswordFunction(display_password_callback);
Plymouth.SetDisplayNormalFunction(display_normal_callback);
EOF

# Ensure permissions are strictly locked down for initramfs packaging
chmod 0644 "${THEME_DIR}"/*

info "Setting default theme to ${THEME_NAME}..."
plymouth-set-default-theme "$THEME_NAME"

info "Patching mkinitcpio drop-in config to inject plymouth hook..."
if [[ -f "$MKINITCPIO_CONF" ]]; then
    # ARCH FIX: Ensure idempotency and robust injection regardless of systemd or udev hooks
    if ! grep -q "^[^#]*HOOKS=.*plymouth" "$MKINITCPIO_CONF"; then
        if grep -q "^[^#]*HOOKS=.*systemd" "$MKINITCPIO_CONF"; then
            sed -i --follow-symlinks -E 's/^([^#]*HOOKS=\([^)]*systemd)([[:space:]]*)/\1 plymouth /' "$MKINITCPIO_CONF"
            info "Injected modern plymouth hook (after systemd) into $MKINITCPIO_CONF"
        elif grep -q "^[^#]*HOOKS=.*udev" "$MKINITCPIO_CONF"; then
            sed -i --follow-symlinks -E 's/^([^#]*HOOKS=\([^)]*udev)([[:space:]]*)/\1 plymouth /' "$MKINITCPIO_CONF"
            info "Injected modern plymouth hook (after udev) into $MKINITCPIO_CONF"
        else
            warn "Could not find 'systemd' or 'udev' in HOOKS. Please add 'plymouth' manually."
        fi
    else
        info "plymouth hook already present or config is commented out."
    fi
else
    warn "$MKINITCPIO_CONF not found. Ensure 120_mkintcpip_optimizer.sh is run before this script."
fi

info "Dusky Plymouth deployment successful."
info "Initramfs hooks are configured. Generation is deferred to 158_mkinitcpio_restore_and_generate.sh."
