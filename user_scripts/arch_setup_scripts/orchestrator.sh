#!/usr/bin/env bash
# ==============================================================================
#  ARCH LINUX MASTER ORCHESTRATOR WRAPPER
# ==============================================================================
# This script handles network connectivity and Python/UI dependencies before
# handing off execution to the Python-based Textual Orchestrator UI.
# ==============================================================================

set -o errexit
set -o nounset
set -o pipefail

# --- Constants & Colors ---
readonly ORCHESTRATOR_PY="$(dirname "$(realpath "${BASH_SOURCE[0]}")")/orchestrator.py"
readonly NETWORK_SCRIPT="$(dirname "$(realpath "${BASH_SOURCE[0]}")")/scripts/003_network_connect.sh"

declare -g RED="" GREEN="" YELLOW="" BLUE="" BOLD="" RESET=""
if [[ -t 1 ]]; then
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

# --- 1. Internet Connectivity Check ---
check_internet() {
    # Try reaching Arch Linux servers or Cloudflare DNS
    if ping -q -c 1 -W 2 archlinux.org >/dev/null 2>&1 || ping -q -c 1 -W 2 1.1.1.1 >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

if ! check_internet; then
    log "WARN" "No internet connection detected."
    
    if [[ -x "$NETWORK_SCRIPT" ]]; then
        log "RUN" "Launching network configuration script..."
        "$NETWORK_SCRIPT"
        
        # Verify again after the script completes
        if ! check_internet; then
            log "ERROR" "Still no internet connection after network configuration. Orchestrator requires internet to fetch packages."
            log "ERROR" "Please connect to the internet manually and rerun."
            exit 1
        fi
        log "SUCCESS" "Internet connection established."
    else
        log "ERROR" "Network script not found at: $NETWORK_SCRIPT"
        log "ERROR" "Please connect to the internet manually and rerun."
        exit 1
    fi
else
    log "SUCCESS" "Internet connection verified."
fi

# --- 2. Python Core Check ---
needs_sudo=0
if ! command -v python >/dev/null 2>&1; then
    log "WARN" "Python interpreter not found."
    needs_sudo=1
fi

# --- 3. Python UI Dependencies Check (Idempotent) ---
declare -a missing_pkgs=()

# Function to check if a python module is importable
has_python_module() {
    local module="$1"
    # If python is missing completely, we automatically fail the module check
    if ! command -v python >/dev/null 2>&1; then
        return 1
    fi
    python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('${module}') else 1)" 2>/dev/null
}

if ! has_python_module "textual"; then
    missing_pkgs+=("python-textual")
fi

if ! has_python_module "rich"; then
    missing_pkgs+=("python-rich")
fi

if (( ${#missing_pkgs[@]} > 0 )); then
    needs_sudo=1
fi

# --- 4. Bootstrap Missing Dependencies ---
if [[ $needs_sudo -eq 1 ]]; then
    # We only prompt for sudo if we actually need to install something
    log "INFO" "Administrative privileges required to install missing dependencies."
    if ! sudo -v; then
        log "ERROR" "Sudo authentication failed. Cannot install dependencies."
        exit 1
    fi

    # Install Python if missing
    if ! command -v python >/dev/null 2>&1; then
        log "RUN" "Installing Python..."
        sudo pacman -S python --noconfirm --needed || { log "ERROR" "Failed to install Python."; exit 1; }
    fi

    # Install Python modules if missing
    if (( ${#missing_pkgs[@]} > 0 )); then
        log "RUN" "Installing Python dependencies: ${missing_pkgs[*]}"
        sudo pacman -S "${missing_pkgs[@]}" --noconfirm --needed || { log "ERROR" "Failed to install python dependencies."; exit 1; }
    fi
    
    log "SUCCESS" "All dependencies satisfied."
fi

# --- 5. Handoff to Python Orchestrator ---
if [[ ! -f "$ORCHESTRATOR_PY" ]]; then
    log "ERROR" "Cannot find python orchestrator at: $ORCHESTRATOR_PY"
    exit 1
fi

# Replace current shell process with the python orchestrator
exec python "$ORCHESTRATOR_PY" "$@"
