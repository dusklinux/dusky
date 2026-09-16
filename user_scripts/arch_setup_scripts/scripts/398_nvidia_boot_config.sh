#!/usr/bin/env bash
#d: Apply NVIDIA boot requirements (mkinitcpio modules + nvidia_drm.modeset)

# -----------------------------------------------------------------------------
# Script: 398_nvidia_boot_config.sh
# Purpose: Automates the post-install steps 380_nvidia_open_source.sh used to
#          only print, which are required for a reliable NVIDIA Wayland/Hyprland
#          boot (and that 383_configure_hyprland_gpu.py warns about when
#          missing -- "black screen" risk):
#
#            1. add nvidia nvidia_modeset nvidia_uvm nvidia_drm to the
#               MODULES array of /etc/mkinitcpio.conf
#            2. regenerate all initramfs images (mkinitcpio -P)
#            3. enforce nvidia_drm.modeset=1 on the kernel command line of
#               BOTH supported bootloaders:
#                 - systemd-boot: /boot/loader/entries/*.conf (skips -fallback)
#                 - GRUB:         /etc/default/grub + grub-mkconfig
#
#          Everything is idempotent: re-running changes nothing once applied.
#
# Flags:   --auto        no prompts (still hardware-gated)
#          --dry-run     show planned changes without writing
#          --no-rebuild  edit configs but skip mkinitcpio -P
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/gpu_detect.sh
source "${SCRIPT_DIR}/lib/gpu_detect.sh"

AUTO_MODE=0
DRY_RUN=0
REBUILD=1
for arg in "$@"; do
    case "$arg" in
        --auto)       AUTO_MODE=1 ;;
        --dry-run)    DRY_RUN=1 ;;
        --no-rebuild) REBUILD=0 ;;
        *) die "Unknown argument '$arg' (supported: --auto, --dry-run, --no-rebuild)" 2 ;;
    esac
done

MKINITCPIO_CONF="/etc/mkinitcpio.conf"
NVIDIA_MODULES=(nvidia nvidia_modeset nvidia_uvm nvidia_drm)
MODESET_PARAM="nvidia_drm.modeset=1"

# --- privilege escalation (381 pattern) ------------------------------------------
if [[ $EUID -ne 0 ]]; then
    printf "[\033[0;33mINFO\033[0m] Escalating permissions to root...\n"
    exec sudo "$0" "$@"
fi

# --- 1. mkinitcpio MODULES ---------------------------------------------------------
# Rewrites the active MODULES=(...) line adding only the missing modules in a
# single awk pass; appends a fresh line when no active MODULES exists.
# Keeps every other line byte-identical; backs up once to .bak.dusky.
patch_mkinitcpio() {
    [[ -f "$MKINITCPIO_CONF" ]] || { log_warn "$MKINITCPIO_CONF not found; skipping."; return; }

    local missing=() m
    for m in "${NVIDIA_MODULES[@]}"; do
        grep -qE "^MODULES=.*\b${m}\b" "$MKINITCPIO_CONF" || missing+=("$m")
    done

    if [[ ${#missing[@]} -eq 0 ]]; then
        log_ok "mkinitcpio MODULES already contain the NVIDIA set."
        return
    fi
    log_info "Adding to MODULES: ${missing[*]}"

    [[ "$DRY_RUN" -eq 1 ]] && { log_info "[dry-run] would edit $MKINITCPIO_CONF"; return; }

    cp -n "$MKINITCPIO_CONF" "${MKINITCPIO_CONF}.bak.dusky"
    local tmp
    tmp=$(mktemp) || die "mktemp failed"
    awk -v extra="${missing[*]}" '
        /^MODULES=\(/ && !done {
            line=$0
            sub(/\)$/, " " extra ")", line)
            print line
            done=1
            next
        }
        { print }
        END { if (!done) print "MODULES=(" extra ")" }
    ' "$MKINITCPIO_CONF" >"$tmp" && cat "$tmp" >"$MKINITCPIO_CONF"
    rm -f "$tmp"
    log_ok "MODULES updated in $MKINITCPIO_CONF (backup: ${MKINITCPIO_CONF}.bak.dusky)."
}

# --- 2. kernel command line ---------------------------------------------------------

# Ensures $MODESET_PARAM on every options line of an entry file.
patch_systemd_boot_entry() {
    local entry="$1" line out="" changed=0
    while IFS= read -r line; do
        if [[ "$line" =~ ^[[:space:]]*options ]]; then
            if grep -qE 'nvidia_drm\.modeset=' <<<"$line"; then
                if [[ "$line" != *"$MODESET_PARAM"* ]]; then
                    line=$(sed -E 's/nvidia_drm\.modeset=[^ ]+/'"$MODESET_PARAM"'/' <<<"$line")
                    changed=1
                fi
            else
                line="${line} ${MODESET_PARAM}"
                changed=1
            fi
        fi
        out+="${line}"$'\n'
    done <"$entry"

    if [[ $changed -eq 1 ]]; then
        [[ "$DRY_RUN" -eq 1 ]] && { log_info "[dry-run] would edit $entry"; return; }
        cp -n "$entry" "${entry}.bak.dusky" 2>/dev/null || true   # FAT32 may refuse
        printf '%s' "$out" >"$entry"
        log_ok "Enforced $MODESET_PARAM in $(basename "$entry")."
    fi
}

patch_systemd_boot() {
    local entries_dir="/boot/loader/entries"
    [[ -d "$entries_dir" ]] || { log_info "No systemd-boot entries at $entries_dir."; return; }
    local entry found=0
    for entry in "$entries_dir"/*.conf; do
        [[ "$entry" == *-fallback* ]] && continue   # primary kernels only (320 policy)
        found=1
        patch_systemd_boot_entry "$entry"
    done
    [[ $found -eq 0 ]] && log_info "No primary systemd-boot entries found."
}

patch_grub() {
    local grub_default="/etc/default/grub"
    [[ -f "$grub_default" ]] || { log_info "GRUB not installed (/etc/default/grub missing)."; return; }

    if grep -qE '^GRUB_CMDLINE_LINUX(_DEFAULT)?=.*nvidia_drm\.modeset=' "$grub_default"; then
        log_ok "GRUB command line already sets nvidia_drm.modeset."
    else
        log_info "Appending $MODESET_PARAM to GRUB_CMDLINE_LINUX_DEFAULT."
        if [[ "$DRY_RUN" -ne 1 ]]; then
            cp -n "$grub_default" "${grub_default}.bak.dusky"
            sed -E -i 's|^(GRUB_CMDLINE_LINUX_DEFAULT=")(.*)(")$|\1\2 '"$MODESET_PARAM"'\3|' "$grub_default"
            log_ok "Updated $grub_default."
            if command -v grub-mkconfig &>/dev/null && [[ -d /boot/grub ]]; then
                if grub-mkconfig -o /boot/grub/grub.cfg; then
                    log_ok "Regenerated /boot/grub/grub.cfg."
                else
                    log_warn "grub-mkconfig failed; /etc/default/grub was edited, regenerate grub.cfg manually."
                fi
            else
                log_warn "grub-mkconfig unavailable; regenerate grub.cfg manually."
            fi
        fi
    fi
}

# --- main ------------------------------------------------------------------------------
main() {
    gpu_detect_topology

    if [[ "$GPU_HAS_NVIDIA" -ne 1 ]]; then
        log_info "No NVIDIA GPU detected -- nothing to configure."
        exit 0
    fi

    if ! pacman -Qq nvidia-utils &>/dev/null && ! lsmod 2>/dev/null | grep -q '^nvidia '; then
        log_warn "NVIDIA hardware present but the proprietary driver is not installed."
        log_warn "Run 380_nvidia_open_source.sh first; aborting to avoid an unbootable initramfs."
        exit 0
    fi

    log_info "NVIDIA detected. This script applies the boot requirements for"
    log_info "Wayland/Hyprland (early KMS): $MODESET_PARAM + initramfs modules."
    gpu_confirm "  Apply NVIDIA boot configuration?" "$AUTO_MODE" || { log_warn "Skipping."; exit 0; }

    patch_mkinitcpio

    if [[ "$REBUILD" -eq 1 && "$DRY_RUN" -ne 1 ]]; then
        if command -v mkinitcpio &>/dev/null; then
            log_info "Rebuilding initramfs images (mkinitcpio -P)..."
            if ! mkinitcpio -P; then
                # A failed rebuild after the MODULES edit would leave the
                # system with a modified config and no fresh images -- roll
                # the config back and stop loudly instead of crashing.
                if [[ -f "${MKINITCPIO_CONF}.bak.dusky" ]]; then
                    cp -f "${MKINITCPIO_CONF}.bak.dusky" "$MKINITCPIO_CONF"
                    log_warn "mkinitcpio failed; restored ${MKINITCPIO_CONF} from backup."
                else
                    log_warn "mkinitcpio failed; no backup to restore -- edit ${MKINITCPIO_CONF} by hand."
                fi
                log_err "Initramfs rebuild failed (often a missing module or kernel headers)."
                log_err "Fix the cause and re-run; nothing else was changed."
                exit 1
            fi
            log_ok "Initramfs rebuilt."
        else
            log_warn "mkinitcpio not found; initramfs not regenerated."
        fi
    fi

    patch_systemd_boot
    patch_grub

    echo ""
    log_ok "NVIDIA boot configuration complete. Reboot to activate."
}

main "$@"