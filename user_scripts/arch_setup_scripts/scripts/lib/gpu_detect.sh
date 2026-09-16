#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Library: gpu_detect.sh
# Purpose:  Shared GPU topology detection + install helpers for the dusky
#           ML/GPU setup scripts (396-402, 409). Source this file; never
#           execute it directly.
#
#           Reuses the detection contract of 380_nvidia_open_source.sh
#           (sysfs first, strict lspci fallback, virtualization guard) so
#           every ML script agrees with the driver installer on what
#           hardware is present.
#
# Provides (after gpu_detect_topology):
#   GPU_HAS_INTEL / GPU_HAS_AMD / GPU_HAS_NVIDIA   (0|1)
#   GPU_IS_VM                                       (0|1, VirtIO/VMware/VBox)
#   GPU_PRIMARY_VENDOR                              ("nvidia"|"amd"|"intel"|"vm"|"")
#
# Optional probes (call individually):
#   gpu_nvidia_driver_major    -> echoes driver major (580) or nothing
#   gpu_rocm_release           -> echoes "6.4" from /opt/rocm or nothing
#   gpu_rocm_gfx               -> echoes gfx target (gfx1101) or nothing
#   gpu_hsa_override_for_gfx   -> echoes override ("11.0.0") or nothing
# -----------------------------------------------------------------------------

# Guard: this is a library, not an entry point.
[[ "${BASH_SOURCE[0]}" != "$0" ]] || { echo "gpu_detect.sh is a library; source it from a setup script." >&2; exit 1; }

# --- Logging (define only if the caller has not brought its own) -------------
if ! command -v log_info &>/dev/null; then
    log_info() { printf "[\033[1;34mINFO\033[0m] %s\n" "$*"; }
    log_ok()   { printf "[\033[1;32m OK \033[0m] %s\n" "$*"; }
    log_warn() { printf "[\033[0;33mWARN\033[0m] %s\n" "$*" >&2; }
    log_err()  { printf "[\033[1;31mERR \033[0m] %s\n" "$*" >&2; }
    die()      { log_err "$1"; exit "${2:-1}"; }
fi

# --- Topology detection -------------------------------------------------------
GPU_HAS_INTEL=0
GPU_HAS_AMD=0
GPU_HAS_NVIDIA=0
GPU_IS_VM=0
GPU_PRIMARY_VENDOR=""

gpu_detect_topology() {
    local vendor_id

    # Phase 1: sysfs (preferred -- active GPUs)
    local card_path
    for card_path in /sys/class/drm/card[0-9]*; do
        [[ -r "$card_path/device/vendor" ]] || continue
        vendor_id=$(<"$card_path/device/vendor")
        vendor_id=${vendor_id,,}
        case "$vendor_id" in
            "0x8086") GPU_HAS_INTEL=1 ;;
            "0x1002") GPU_HAS_AMD=1 ;;
            "0x10de") GPU_HAS_NVIDIA=1 ;;
        esac
    done

    # Phase 2: lspci fallback (VGA 0300 / 3D 0302 / Display 0380)
    if command -v lspci &>/dev/null; then
        [[ "$GPU_HAS_INTEL" -eq 0 ]]  && { lspci -n -d 8086::0300; lspci -n -d 8086::0380; } 2>/dev/null | grep -q . && GPU_HAS_INTEL=1
        [[ "$GPU_HAS_AMD" -eq 0 ]]    && { lspci -n -d 1002::0300; lspci -n -d 1002::0380; } 2>/dev/null | grep -q . && GPU_HAS_AMD=1
        [[ "$GPU_HAS_NVIDIA" -eq 0 ]] && { lspci -n -d 10de::0300; lspci -n -d 10de::0302; } 2>/dev/null | grep -q . && GPU_HAS_NVIDIA=1
    fi

    # Phase 3: virtualization guard (VirtIO 1af4 / VMware 15ad / VirtualBox 80ee)
    if [[ $((GPU_HAS_INTEL + GPU_HAS_AMD + GPU_HAS_NVIDIA)) -eq 0 ]]; then
        if command -v lspci &>/dev/null && { lspci -d 1af4::0300; lspci -d 15ad::0300; lspci -d 80ee::0300; } 2>/dev/null | grep -q .; then
            GPU_IS_VM=1
            GPU_PRIMARY_VENDOR="vm"
        fi
    fi

    # Primary vendor for ML backend choice: NVIDIA > AMD > Intel.
    if [[ "$GPU_HAS_NVIDIA" -eq 1 ]]; then
        GPU_PRIMARY_VENDOR="nvidia"
    elif [[ "$GPU_HAS_AMD" -eq 1 ]]; then
        GPU_PRIMARY_VENDOR="amd"
    elif [[ "$GPU_HAS_INTEL" -eq 1 ]]; then
        GPU_PRIMARY_VENDOR="intel"
    fi
    # These five are the library's public outputs, consumed by the sourcing
    # setup scripts (396-402, 409); referenced here so shellcheck sees the use.
    : "${GPU_HAS_INTEL}" "${GPU_HAS_AMD}" "${GPU_HAS_NVIDIA}" "${GPU_IS_VM}" "${GPU_PRIMARY_VENDOR}"
}

# --- NVIDIA runtime probes -----------------------------------------------------

# Echoes the loaded driver major version (e.g. 580), or nothing when the
# proprietary driver is absent/not talking. Never fails.
gpu_nvidia_driver_major() {
    local ver
    ver=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n1 | cut -d. -f1) || true
    [[ "$ver" =~ ^[0-9]+$ ]] && echo "$ver" || true
}

# --- AMD ROCm runtime probes ---------------------------------------------------

# Echoes the installed ROCm release ("6.4"), or nothing.
gpu_rocm_release() {
    local ver
    ver=$(cut -d. -f1,2 /opt/rocm/.info/version 2>/dev/null | tr -dc '0-9.') || true
    [[ -n "$ver" ]] && echo "$ver" || true
}

# Echoes the first gfx target reported by rocminfo (e.g. gfx1101), or nothing.
gpu_rocm_gfx() {
    local gfx
    gfx=$(rocminfo 2>/dev/null | grep -m1 -oE 'gfx[0-9a-f]+') || true
    [[ -n "$gfx" ]] && echo "$gfx" || true
}

# Maps consumer gfx targets that lack native MIOpen/rocBLAS kernels onto the
# nearest supported architecture. Mirrors the mapping proven by the Kokoro
# TTS installer (user_scripts/tts_stt/dusky_kokoro/kokoro_installer.sh).
gpu_hsa_override_for_gfx() {
    local gfx="${1:-}"
    case "$gfx" in
        gfx1031|gfx1032|gfx1033|gfx1034|gfx1035|gfx1036) echo "10.3.0" ;;
        gfx1010|gfx1011|gfx1012)                          echo "10.3.0" ;;
        gfx1101|gfx1102|gfx1103)                          echo "11.0.0" ;;
        *)                                                 true ;;
    esac
}

# True (0) when a usable ROCm userspace exists on this machine.
gpu_rocm_available() {
    [[ -d /opt/rocm ]] || command -v rocm-smi &>/dev/null || [[ -e /dev/kfd ]]
}

# --- Install / write helpers ----------------------------------------------------

# y/N prompt honouring --auto: auto=1 accepts, auto=0 asks.
gpu_confirm() {
    local prompt="$1" auto="${2:-0}"
    [[ "$auto" -eq 1 ]] && return 0
    local choice
    read -rp "$prompt [y/N] " choice || true
    [[ "${choice:-N}" =~ ^[yY] ]]
}

# Atomic write for files the current user owns (temp + fsync + rename).
gpu_atomic_write() {
    local file="$1" content="$2" tmp
    tmp=$(mktemp "${file}.XXXXXX") || return 1
    printf '%s' "$content" >"$tmp" || { rm -f "$tmp"; return 1; }
    sync "$tmp" 2>/dev/null || true
    mv -f "$tmp" "$file"
}

# Root-owned atomic write (self-escalating, safe under `set -e`). Uses plain
# sudo (not -n): this runs from attended installers where a password prompt
# is fine, whereas `sudo -n` would hard-fail under `set -e` the moment sudo
# wants a password.
gpu_root_write() {
    local file="$1" content="$2" tmp
    tmp=$(mktemp) || return 1
    printf '%s' "$content" >"$tmp" || { rm -f "$tmp"; return 1; }
    if [[ $EUID -eq 0 ]]; then
        install -D -m 0644 "$tmp" "$file"; local rc=$?
    else
        sudo install -D -m 0644 "$tmp" "$file"; local rc=$?
    fi
    rm -f "$tmp"
    return "$rc"
}

# pacman wrapper: skips cleanly when pacman is missing (non-Arch CI).
gpu_pacman_install() {
    command -v pacman &>/dev/null || { log_warn "pacman not found; skipping: $*"; return 0; }
    if [[ $EUID -eq 0 ]]; then
        pacman -S --needed --noconfirm "$@"
    else
        sudo pacman -S --needed --noconfirm "$@"
    fi
}

# paru wrapper for AUR packages. MUST run as the invoking user (paru refuses
# root); when elevated, de-escalates through SUDO_USER. Skips when paru is
# absent so ML scripts stay optional rather than fatal.
gpu_aur_install() {
    # Flags verified against a real paru binary: --needed/--noconfirm come
    # from pacman, --noredownload is paru's (skip source re-download).
    local paru_cmd=(paru -S --needed --noconfirm --noredownload)
    if ! command -v paru &>/dev/null; then
        log_warn "paru not found; skipping AUR packages: $*"
        log_warn "install them manually once paru is available."
        return 0
    fi
    if [[ $EUID -eq 0 && -n "${SUDO_USER:-}" ]]; then
        sudo -u "$SUDO_USER" "${paru_cmd[@]}" "$@"
    else
        "${paru_cmd[@]}" "$@"
    fi
}