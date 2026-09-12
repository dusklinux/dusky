#!/usr/bin/env bash
#d: Apply Intel Mac hardware quirks (Wi-Fi, keyboard, sensors)

# -----------------------------------------------------------------------------
# Script: 409_intel_mac_quirks.sh
# Purpose: Intel Macs (2012-2020) are ordinary x86_64 PCs -- Arch and dusky
#          boot on them out of the box -- but three areas want tweaks:
#
#            1. Broadcom Wi-Fi (14e4:43xx, common in 2012-2015 models)
#               -> broadcom-wl-dkms from AUR (wl driver) or b43 for older chips
#            2. Apple keyboard fn-mode -- /etc/modprobe.d/hid_apple.conf
#               fnmode=2 makes F1-F12 act as function keys by default
#            3. lm_sensors + applesmc so `sensors` reports Mac fan/temp data
#
#          Registered commented-out in profiles/01_main.toml -- enable it on
#          Apple hardware. Apple Silicon (M1-M4) is out of scope: it needs
#          the Asahi/ARM port and has no CUDA/ROCm (see ML_STACK.md).
#
# Flags:   --auto      no prompts (still hardware-gated)
#          --no-wifi   skip the Broadcom driver installation
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/gpu_detect.sh
source "${SCRIPT_DIR}/lib/gpu_detect.sh"

AUTO_MODE=0
NO_WIFI=0
for arg in "$@"; do
    case "$arg" in
        --auto)   AUTO_MODE=1 ;;
        --no-wifi) NO_WIFI=1 ;;
        *) die "Unknown argument '$arg' (supported: --auto, --no-wifi)" 2 ;;
    esac
done

# --- privilege escalation (381 pattern) ---------------------------------------------
if [[ $EUID -ne 0 ]]; then
    printf "[\033[0;33mINFO\033[0m] Escalating permissions to root...\n"
    exec sudo "$0" "$@"
fi

HID_APPLE_CONF="/etc/modprobe.d/hid_apple.conf"

is_apple_hardware() {
    local vendor
    vendor=$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null) || true
    [[ "$vendor" == "Apple Inc." || "$vendor" == "Apple Computer, Inc." ]]
}

has_broadcom_wifi() {
    # Broadcom wireless: vendor 14e4, class 0280 (network controller)
    command -v lspci &>/dev/null && lspci -d 14e4::0280 2>/dev/null | grep -q .
}

# --- Broadcom Wi-Fi -------------------------------------------------------------------
install_broadcom_wifi() {
    has_broadcom_wifi || { log_info "No Broadcom Wi-Fi detected; skipping."; return; }

    local chip
    chip=$(lspci -d 14e4::0280 2>/dev/null | head -n1)
    log_info "Broadcom Wi-Fi found: ${chip}"

    # b43-supported legacy chips (BCM4331 etc.) use linux-firmware, no AUR needed.
    if grep -qE 'BCM43(31|60)' <<<"$chip"; then
        log_info "Legacy b43 chip: ensuring linux-firmware is installed."
        gpu_pacman_install "linux-firmware"
        log_warn "If Wi-Fi does not appear, check: https://wiki.archlinux.org/title/broadcom_wireless"
        return
    fi

    # Everything else (4360/4352/4331 on newer kernels) -> wl driver.
    # broadcom-wl-dkms moved from AUR into [extra].
    log_info "Installing broadcom-wl-dkms (needs kernel headers present)."
    gpu_pacman_install "broadcom-wl-dkms"
    log_warn "If the wl module blacklists conflict, follow the b43/wl table on the Arch wiki."
}

# --- Apple keyboard --------------------------------------------------------------------
configure_hid_apple() {
    modinfo hid_apple &>/dev/null || { log_info "hid_apple module not present; skipping."; return; }

    if [[ -f "$HID_APPLE_CONF" ]] && grep -q 'fnmode' "$HID_APPLE_CONF"; then
        log_ok "hid_apple already configured."
        return
    fi

    gpu_root_write "$HID_APPLE_CONF" "$(printf '# Managed by dusky 409_intel_mac_quirks.sh\n# F1-F12 behave as function keys; hold Fn for media keys.\noptions hid_apple fnmode=2\n')"
    log_ok "Wrote ${HID_APPLE_CONF} (fnmode=2)."
    log_info "Apply immediately for this session:  echo 2 | sudo tee /sys/module/hid_apple/parameters/fnmode"

    # ISO keyboards (EU Macs) may also want iso_layout=0; leave a pointer only.
    log_info "EU keyboards: add 'options hid_apple iso_layout=0' if ~ and § are swapped."
}

# --- sensors ------------------------------------------------------------------------------
install_sensors() {
    gpu_pacman_install "lm_sensors"
    if command -v sensors &>/dev/null && ! sensors 2>/dev/null | grep -q applesmc; then
        log_info "Running sensors-detect so applesmc (fans/temps) gets picked up..."
        sensors-detect --auto &>/dev/null || log_warn "sensors-detect failed; run it manually."
    fi
    if command -v sensors &>/dev/null && sensors 2>/dev/null | grep -q applesmc; then
        log_ok "applesmc sensors reporting."
    else
        log_warn "applesmc not detected (desktop Macs often lack it)."
    fi
}

# --- main ------------------------------------------------------------------------------------
main() {
    if ! is_apple_hardware; then
        log_info "Not Apple hardware (DMI vendor: $(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || echo 'unknown')); skipping."
        exit 0
    fi
    log_info "Intel Mac detected."
    log_info "Scope note: Apple Silicon (M1-M4) needs the Asahi/ARM port -- out of scope."
    gpu_confirm "  Apply Intel Mac quirks?" "$AUTO_MODE" || { log_warn "Skipping."; exit 0; }

    if [[ "$NO_WIFI" -eq 0 ]]; then
        install_broadcom_wifi
    fi
    configure_hid_apple
    install_sensors

    echo ""
    log_ok "Intel Mac quirks applied. Reboot to load Wi-Fi/keyboard changes."
    log_info "Further reading: https://wiki.archlinux.org/title/Mac"
}

main "$@"