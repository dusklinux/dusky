#!/usr/bin/env bash
# ==============================================================================
# ARCH LINUX MASTER ORCHESTRATOR WRAPPER
# ==============================================================================
# Bleeding-edge Arch bootstrap wrapper.
# Installs only missing dependencies, then hands off to the Python orchestrator.
# ==============================================================================
set -Eeuo pipefail
shopt -s inherit_errexit nullglob

SCRIPT_DIR="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
readonly SCRIPT_DIR
readonly ORCHESTRATOR_PY="${SCRIPT_DIR}/orchestrator.py"
readonly NETWORK_SCRIPT="${SCRIPT_DIR}/scripts/003_network_connect.sh"

declare -g RED="" GREEN="" YELLOW="" BLUE="" BOLD="" RESET=""
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    RED=$'\e[1;31m'
    GREEN=$'\e[1;32m'
    YELLOW=$'\e[1;33m'
    BLUE=$'\e[1;34m'
    BOLD=$'\e[1m'
    RESET=$'\e[0m'
fi

log() {
    local level="$1"
    local msg="$2"
    local color=""
    case "$level" in
        INFO)    color="$BLUE" ;;
        SUCCESS) color="$GREEN" ;;
        WARN)    color="$YELLOW" ;;
        ERROR)   color="$RED" ;;
        RUN)     color="$BOLD" ;;
    esac
    printf "%s[%s]%s %s\n" "${color}" "${level}" "${RESET}" "${msg}"
}

trap 'rc=$?; log ERROR "Wrapper failed at line ${LINENO} (command: ${BASH_COMMAND:-unknown}, exit ${rc})."; exit "${rc}"' ERR

bootstrap_packages() {
    local line
    if [[ -f "$ORCHESTRATOR_PY" ]] && line="$(grep -m1 '^# DUSKY_BOOTSTRAP_PACKAGES:' "$ORCHESTRATOR_PY" 2>/dev/null)"; then
        local -a pkgs=()
        read -r -a pkgs <<< "${line#*:}"
        if (( ${#pkgs[@]} > 0 )); then
            printf '%s\n' "${pkgs[@]}"
            return 0
        fi
    fi
    printf '%s\n' python python-textual python-rich git
}

check_internet() {
    local url
    local -a urls=(
        "https://archlinux.org"
        "https://geo.mirror.pkgbuild.com"
        "https://mirror.pkgbuild.com"
    )

    if command -v curl >/dev/null 2>&1; then
        for url in "${urls[@]}"; do
            if curl -fsS --max-time 6 "${url}" >/dev/null 2>&1; then
                return 0
            fi
        done
    fi

    if command -v getent >/dev/null 2>&1; then
        if getent hosts archlinux.org >/dev/null 2>&1; then
            return 0
        fi
    fi

    if command -v resolvectl >/dev/null 2>&1; then
        if resolvectl query archlinux.org >/dev/null 2>&1; then
            return 0
        fi
    fi

    if command -v ping >/dev/null 2>&1; then
        if ping -n -q -c 1 -W 2 1.1.1.1 >/dev/null 2>&1; then
            return 0
        fi
    fi

    return 1
}

require_internet() {
    if check_internet; then
        log SUCCESS "Internet connection verified."
        return 0
    fi

    log WARN "No internet connection detected."
    if [[ -x "$NETWORK_SCRIPT" ]]; then
        log RUN "Launching network configuration script..."
        "$NETWORK_SCRIPT" || true
        if check_internet; then
            log SUCCESS "Internet connection established."
            return 0
        fi
    fi

    log ERROR "Internet is required to install missing dependencies."
    exit 1
}

python_ok() {
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)' >/dev/null 2>&1
}

choose_python() {
    local candidate
    if [[ -x /usr/bin/python ]] && python_ok /usr/bin/python; then
        printf "/usr/bin/python\n"
        return 0
    fi

    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && python_ok "$(command -v "$candidate")"; then
            command -v "$candidate"
            return 0
        fi
    done

    return 1
}

pkg_installed() {
    pacman -Qq "$1" >/dev/null 2>&1
}

main() {
    if [[ ! -f "$ORCHESTRATOR_PY" ]]; then
        log ERROR "Cannot find Python orchestrator at: $ORCHESTRATOR_PY"
        exit 1
    fi

    local -a sudo_cmd=()
    if (( EUID != 0 )); then
        if ! command -v sudo >/dev/null 2>&1; then
            log ERROR "sudo is required to bootstrap dependencies."
            exit 1
        fi
        sudo_cmd=(sudo)
    fi

    local -a bootstrap_pkgs=()
    mapfile -t bootstrap_pkgs < <(bootstrap_packages)

    local -a missing_pkgs=()
    local pkg
    for pkg in "${bootstrap_pkgs[@]}"; do
        pkg_installed "$pkg" || missing_pkgs+=("$pkg")
    done

    if (( EUID == 0 )) && ! pkg_installed sudo; then
        missing_pkgs+=("sudo")
    fi

    if (( ${#missing_pkgs[@]} > 0 )); then
        require_internet

        if (( ${#sudo_cmd[@]} > 0 )); then
            log INFO "Administrative privileges required to install missing dependencies."
            if ! sudo -v; then
                log ERROR "Sudo authentication failed. Cannot install dependencies."
                exit 1
            fi
        fi

        if [[ -f /var/lib/pacman/db.lck ]]; then
            if command -v pgrep >/dev/null 2>&1 && pgrep -x pacman >/dev/null 2>&1; then
                log ERROR "Another pacman process is currently running."
                exit 1
            fi
            log WARN "Removing stale pacman lock file: /var/lib/pacman/db.lck"
            "${sudo_cmd[@]}" rm -f /var/lib/pacman/db.lck
        fi

        log RUN "Installing missing packages: ${missing_pkgs[*]}"
        if ! "${sudo_cmd[@]}" pacman -Syu --needed --noconfirm "${missing_pkgs[@]}"; then
            log WARN "Initial pacman transaction failed. Attempting keyring recovery and retry..."
            "${sudo_cmd[@]}" pacman -Sy --needed --noconfirm archlinux-keyring || true
            "${sudo_cmd[@]}" pacman-key --init || true
            "${sudo_cmd[@]}" pacman-key --populate archlinux || true
            "${sudo_cmd[@]}" pacman -Syu --needed --noconfirm "${missing_pkgs[@]}"
        fi

        log SUCCESS "All dependencies satisfied."
    else
        log SUCCESS "All dependencies already satisfied."
    fi

    local PYTHON_BIN
    if ! PYTHON_BIN="$(choose_python)"; then
        log ERROR "Python 3.14+ interpreter not found after dependency bootstrap."
        exit 1
    fi

    if ! "$PYTHON_BIN" -c 'import textual, rich, tomllib' >/dev/null 2>&1; then
        log WARN "Python runtime imports failed. Attempting package refresh..."
        if (( ${#sudo_cmd[@]} > 0 )); then
            sudo -v || true
        fi
        require_internet
        "${sudo_cmd[@]}" pacman -Syu --needed --noconfirm python-textual python-rich || true
        if ! "$PYTHON_BIN" -c 'import textual, rich, tomllib' >/dev/null 2>&1; then
            log ERROR "Python dependencies are still unusable."
            exit 1
        fi
    fi

    unset -v \
        LD_PRELOAD LD_AUDIT LD_DEBUG LD_LIBRARY_PATH LD_ORIGIN_PATH \
        LD_PROFILE LD_SHOW_AUXV LD_USE_LOAD_BIAS PYTHONSTARTUP PYTHONHOME \
        PYTHONPATH PERL5LIB RUBYLIB NODE_OPTIONS 2>/dev/null || true

    local has_allow_root=0
    local arg
    for arg in "$@"; do
        if [[ "$arg" == "--allow-root" ]]; then
            has_allow_root=1
            break
        fi
    done

    if (( EUID == 0 )) && [[ -n "${SUDO_USER:-}" ]] && (( has_allow_root == 0 )); then
        log INFO "Dropping privileges to ${SUDO_USER}..."

        local target_home target_shell
        target_home="$(getent passwd "$SUDO_USER" | cut -d: -f6 || true)"
        target_shell="$(getent passwd "$SUDO_USER" | cut -d: -f7 || true)"
        [[ -n "$target_home" ]] || target_home="/home/${SUDO_USER}"
        [[ -n "$target_shell" ]] || target_shell="/bin/bash"

        cd "$SCRIPT_DIR"
        exec sudo -u "$SUDO_USER" -- env \
            HOME="$target_home" \
            USER="$SUDO_USER" \
            LOGNAME="$SUDO_USER" \
            SHELL="$target_shell" \
            PYTHONUNBUFFERED=1 \
            PYTHONUTF8=1 \
            PYTHONDONTWRITEBYTECODE=1 \
            "$PYTHON_BIN" "$ORCHESTRATOR_PY" "$@"
    fi

    log RUN "Launching Dusky Orchestrator..."
    exec env \
        PYTHONUNBUFFERED=1 \
        PYTHONUTF8=1 \
        PYTHONDONTWRITEBYTECODE=1 \
        "$PYTHON_BIN" "$ORCHESTRATOR_PY" "$@"
}

main "$@"
