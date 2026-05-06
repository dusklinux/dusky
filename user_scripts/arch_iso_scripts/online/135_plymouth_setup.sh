#!/usr/bin/env bash
# Arch Linux (EFI + Btrfs root) | Dusky Graphical Boot & LUKS Setup
# CHROOT DEPLOYMENT EDITION - FORENSICALLY AUDITED (POSITION-INDEPENDENT)

set -Eeuo pipefail
export LC_ALL=C

# --- Dynamic Path Resolution ---
# This guarantees the script always finds its assets and siblings, 
# regardless of where the user or orchestrator is currently 'cd'ed into.
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

# --- Configuration ---
readonly THEME_NAME="dusky"
readonly THEME_DIR="/usr/share/plymouth/themes/${THEME_NAME}"
readonly ASSETS_DIR="${SCRIPT_DIR}/assets/plymouth"
readonly MKINITCPIO_CONF="/etc/mkinitcpio.conf.d/10-arch-btrfs-luks.conf"
readonly LIMINE_SCRIPT="${SCRIPT_DIR}/155_limine_setup.sh"

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

# --- Execution ---
info "Ensuring Plymouth is installed..."
if ! pacman -Q plymouth >/dev/null 2>&1; then
    # Strict Offline Mode: Respects the airlock established by 051_pacman_repo_switch.sh
    if ! pacman -S --needed --noconfirm plymouth; then
        printf "\n\033[1;31m========================================================================\033[0m\n" >&2
        printf "\033[1;31m[CRITICAL ARCHITECTURAL FAILURE]\033[0m\n" >&2
        printf "The offline installation of 'plymouth' failed.\n" >&2
        printf "Because the environment is locked to 'file:///offline_repo',\n" >&2
        printf "RESOLUTION: You MUST include 'plymouth' in your 070_pacstrap.sh payload.\n" >&2
        printf "\033[1;31m========================================================================\033[0m\n\n" >&2
        exit 1
    fi
fi

info "Validating Plymouth binaries..."
require_cmd plymouth-set-default-theme

info "Verifying Dusky assets directory..."
if [[ ! -d "$ASSETS_DIR" ]]; then
    fatal "Assets directory '$ASSETS_DIR' not found. Ensure the 'assets' folder is next to this script."
fi

info "Validating strictly required Plymouth graphical assets..."
declare -a required_assets=("logo.png" "lock.png" "entry.png" "bullet.png" "progress_box.png" "progress_bar.png")

for asset in "${required_assets[@]}"; do
    if [[ ! -f "$ASSETS_DIR/$asset" ]]; then
        fatal "Missing required asset: $asset in '$ASSETS_DIR'. Deployment cannot continue."
    fi
done

info "Deploying custom theme: $THEME_NAME..."
mkdir -p "$THEME_DIR"

cp "$ASSETS_DIR"/*.png "$THEME_DIR/" || fatal "Failed to copy PNG assets to $THEME_DIR."

# Generate .plymouth configuration
cat << EOF > "${THEME_DIR}/${THEME_NAME}.plymouth"
[Plymouth Theme]
Name=Dusky
Description=Dusky custom graphical LUKS prompt and splash.
ModuleName=script

[script]
ImageDir=${THEME_DIR}
ScriptFile=${THEME_DIR}/${THEME_NAME}.script
ConsoleLogBackgroundColor=0x1a1b26
MonospaceFont=Cantarell 11
Font=Cantarell 11
EOF

# Generate .script file
cat << 'EOF' > "${THEME_DIR}/${THEME_NAME}.script"
Window.SetBackgroundTopColor(0.101, 0.105, 0.149);
Window.SetBackgroundBottomColor(0.101, 0.105, 0.149);

logo.image = Image("logo.png");
logo.sprite = Sprite(logo.image);
logo.sprite.SetX (Window.GetWidth() / 2 - logo.image.GetWidth() / 2);
logo.sprite.SetY (Window.GetHeight() / 2 - logo.image.GetHeight() / 2);
logo.sprite.SetOpacity (1);

global.fake_progress_limit = 0.7; 
global.fake_progress_duration = 15.0; 
global.fake_progress = 0.0;
global.real_progress = 0.0;
global.fake_progress_active = 0;
global.animation_frame = 0;
global.password_shown = 0;
global.max_progress = 0.0;

fun update_progress_bar(progress) {
    if (progress > global.max_progress) {
        global.max_progress = progress;
        width = Math.Int(progress_bar.original_image.GetWidth() * progress);
        if (width < 1) width = 1;
        progress_bar.image = progress_bar.original_image.Scale(width, progress_bar.original_image.GetHeight());
        progress_bar.sprite.SetImage(progress_bar.image);
    }
}

fun refresh_callback () {
    global.animation_frame++;
    if (global.fake_progress_active == 1) {
        elapsed_time = global.animation_frame / 50.0;
        time_ratio = elapsed_time / global.fake_progress_duration;
        if (time_ratio > 1.0) time_ratio = 1.0;
        eased_ratio = 1 - ((1 - time_ratio) * (1 - time_ratio));
        global.fake_progress = eased_ratio * global.fake_progress_limit;
        update_progress_bar(global.fake_progress);
    }
}
Plymouth.SetRefreshFunction (refresh_callback);

fun show_progress_bar() { progress_box.sprite.SetOpacity(1); progress_bar.sprite.SetOpacity(1); }
fun hide_progress_bar() { progress_box.sprite.SetOpacity(0); progress_bar.sprite.SetOpacity(0); }
fun show_password_dialog() { lock.sprite.SetOpacity(1); entry.sprite.SetOpacity(1); }
fun hide_password_dialog() {
    lock.sprite.SetOpacity(0); entry.sprite.SetOpacity(0);
    for (index = 0; bullet.sprites[index]; index++) bullet.sprites[index].SetOpacity(0);
}

fun start_fake_progress() {
    if (global.max_progress == 0.0) { update_progress_bar(0.0); }
    global.fake_progress_active = 1;
    global.animation_frame = 0;
}
fun stop_fake_progress() { global.fake_progress_active = 0; }

lock.image = Image("lock.png");
entry.image = Image("entry.png");
bullet.image = Image("bullet.png");

entry.sprite = Sprite(entry.image);
entry.x = Window.GetWidth()/2 - entry.image.GetWidth() / 2;
entry.y = logo.sprite.GetY() + logo.image.GetHeight() + 40;
entry.sprite.SetPosition(entry.x, entry.y, 10001);
entry.sprite.SetOpacity(0);

lock_height = entry.image.GetHeight() * 0.8;
lock_scale = lock_height / 96;
lock_width = 84 * lock_scale;

scaled_lock = lock.image.Scale(lock_width, lock_height);
lock.sprite = Sprite(scaled_lock);
lock.x = entry.x - lock_width - 15;
lock.y = entry.y + entry.image.GetHeight()/2 - lock_height/2;
lock.sprite.SetPosition(lock.x, lock.y, 10001);
lock.sprite.SetOpacity(0);

bullet.sprites = [];

fun display_normal_callback () {
    hide_password_dialog();
    mode = Plymouth.GetMode();
    if ((mode == "boot" || mode == "resume") && global.password_shown == 1) {
        show_progress_bar(); start_fake_progress();
    }
}

fun display_password_callback (prompt, bullets) {
    global.password_shown = 1;
    stop_fake_progress(); hide_progress_bar();
    global.max_progress = 0.0; global.fake_progress = 0.0; global.real_progress = 0.0;
    show_password_dialog();
    
    for (index = 0; bullet.sprites[index]; index++) bullet.sprites[index].SetOpacity(0);
    
    max_bullets = 21; bullets_to_show = bullets;
    if (bullets_to_show > max_bullets) bullets_to_show = max_bullets;
    
    for (index = 0; index < bullets_to_show; index++) {
        if (!bullet.sprites[index]) {
            scaled_bullet = bullet.image.Scale(7, 7);
            bullet.sprites[index] = Sprite(scaled_bullet);
            bullet.x = entry.x + 20 + index * (7 + 5);
            bullet.y = entry.y + entry.image.GetHeight() / 2 - 3.5;
            bullet.sprites[index].SetPosition(bullet.x, bullet.y, 10002);
        }
        bullet.sprites[index].SetOpacity(1);
    }
}
Plymouth.SetDisplayNormalFunction(display_normal_callback);
Plymouth.SetDisplayPasswordFunction(display_password_callback);

progress_box.image = Image("progress_box.png");
progress_box.sprite = Sprite(progress_box.image);
progress_box.x = Window.GetWidth() / 2 - progress_box.image.GetWidth() / 2;
progress_box.y = entry.y + entry.image.GetHeight() / 2 - progress_box.image.GetHeight() / 2;
progress_box.sprite.SetPosition(progress_box.x, progress_box.y, 0);
progress_box.sprite.SetOpacity(0);

progress_bar.original_image = Image("progress_bar.png");
progress_bar.sprite = Sprite();
progress_bar.image = progress_bar.original_image.Scale(1, progress_bar.original_image.GetHeight());
progress_bar.x = Window.GetWidth() / 2 - progress_bar.original_image.GetWidth() / 2;
progress_bar.y = progress_box.y + (progress_box.image.GetHeight() - progress_bar.original_image.GetHeight()) / 2;
progress_bar.sprite.SetPosition(progress_bar.x, progress_bar.y, 1);
progress_bar.sprite.SetOpacity(0);

fun progress_callback (duration, progress) {
    global.real_progress = progress;
    if (progress > global.fake_progress_limit) { stop_fake_progress(); update_progress_bar(progress); }
}
Plymouth.SetBootProgressFunction(progress_callback);

fun quit_callback () { logo.sprite.SetOpacity (1); }
Plymouth.SetQuitFunction(quit_callback);
EOF

# Ensure permissions
chmod 0644 "${THEME_DIR}"/*

info "Setting default theme to ${THEME_NAME}..."
plymouth-set-default-theme "$THEME_NAME"

info "Patching mkinitcpio drop-in config to inject plymouth hook..."
if [[ -f "$MKINITCPIO_CONF" ]]; then
    # ARCH FIX: sd-plymouth is deprecated. The modern 'plymouth' hook automatically detects systemd environments.
    if ! grep -q "^[^#]*HOOKS=.*plymouth" "$MKINITCPIO_CONF"; then
        sed -i --follow-symlinks -E 's/^([^#]*HOOKS=\([^)]*systemd)([[:space:]]*)/\1 plymouth /' "$MKINITCPIO_CONF"
        info "Injected modern plymouth hook into $MKINITCPIO_CONF"
    else
        info "plymouth hook already present or config is commented out."
    fi
else
    warn "$MKINITCPIO_CONF not found. Ensure 120_mkintcpip_optimizer.sh is run before this script."
fi

info "Patching 155_limine_setup.sh for silent boot command line parameters..."
if [[ -f "$LIMINE_SCRIPT" ]]; then
    if ! grep -q '"splash"' "$LIMINE_SCRIPT"; then
        sed -i --follow-symlinks -E '/cmdline_parts\+=\(.*rootfstype=btrfs.*\)/a \    cmdline_parts+=("quiet" "splash" "loglevel=3" "rd.udev.log_level=3" "vt.global_cursor_default=0")' "$LIMINE_SCRIPT"
        
        if grep -q '"splash"' "$LIMINE_SCRIPT"; then
            info "Silent boot kernel parameters permanently injected into $LIMINE_SCRIPT"
        else
            warn "Failed to inject silent boot parameters. Target array signature in $LIMINE_SCRIPT was not found."
        fi
    else
        info "Silent boot parameters already present in Limine script."
    fi
else
    warn "$LIMINE_SCRIPT not found. Kernel command line parameters were not updated."
fi

info "Dusky Plymouth deployment successful."
info "Please run your 140_mkinitcpio_generation.sh and 155_limine_setup.sh scripts to finalize."
