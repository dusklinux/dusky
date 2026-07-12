#!/usr/bin/env bash
# Captures a screen region and searches it with Google Lens.
# Optimized for UWSM (Universal Wayland Session Manager) environments.


# --- [ CONFIGURATION ] --------------------------------------------------------
# true  = Upload to uguu.se (public URL, no manual paste)
# false = Copy to clipboard + open Lens (private, requires Ctrl+V)
readonly USE_UPLOAD_SERVICE="true"

# --- [ STRICT MODE ] ----------------------------------------------------------
set -euo pipefail

# --- [ DEPENDENCY MANAGER ] ---------------------------------------------------

ensure_dependency() {
    local cmd="$1"
    local package="$2"

    if command -v "$cmd" &>/dev/null; then
        return 0
    fi

    printf '📦 Dependency "%s" missing. Installing package "%s"...\n' "$cmd" "$package"

    if sudo pacman -S --needed --noconfirm "$package"; then
        printf '✅ Installed %s.\n' "$package"
    else
        printf '❌ Failed to install %s. Check sudo privileges.\n' "$package" >&2
        exit 1
    fi
}

# --- Core Dependencies ---
ensure_dependency "grim"        "grim"
ensure_dependency "slurp"       "slurp"
ensure_dependency "xdg-open"    "xdg-utils"
ensure_dependency "notify-send" "libnotify"

# --- Mode-Specific Dependencies ---
if [[ "${USE_UPLOAD_SERVICE}" == "true" ]]; then
    ensure_dependency "curl" "curl"
    ensure_dependency "jq"   "jq"
else
    ensure_dependency "wl-copy" "wl-clipboard"
fi

# --- [ HELPER FUNCTIONS ] -----------------------------------------------------

notify() {
    notify-send -a "Google Lens" "$1" "$2"
}

open_url() {
    dusky-run -- xdg-open "$1" &
    disown
}

die() {
    printf '❌ %s\n' "$1" >&2
    notify "Error" "$1"
    exit 1
}

# --- [ MAIN LOGIC ] -----------------------------------------------------------

printf '📷 Select region...\n'

# 1. Capture Geometry
if ! geometry=$(slurp 2>/dev/null); then
    printf '🚫 Selection cancelled.\n'
    exit 0
fi

# 2. Validate Geometry (Security & Sanity Check)
if [[ ! "${geometry}" =~ ^[0-9]+,[0-9]+\ [0-9]+x[0-9]+$ ]]; then
    die "Invalid selection geometry received."
fi

# -----------------------------------------------------------------------------
# UPLOAD MODE: Screenshot → uguu.se → Google Lens via URL
# -----------------------------------------------------------------------------
if [[ "${USE_UPLOAD_SERVICE}" == "true" ]]; then

    tmp_file=$(mktemp /tmp/lens-XXXXXX.png)
    trap 'rm -f "${tmp_file}"' EXIT

    grim -g "${geometry}" "${tmp_file}"
    notify "Uploading..." "Sending image to secure host"

    # curl flags: -s (silent), -S (show error on fail), -f (fail fast on HTTP error)
    if ! response=$(curl -sSf -F "files[]=@${tmp_file}" 'https://uguu.se/upload'); then
        die "Upload connection failed."
    fi

    # Optimization: Use Bash here-string (<<<) instead of piping echo
    url=$(jq -r '.files[0].url // empty' <<< "${response}")

    if [[ -z "${url}" ]]; then
        printf 'Debug: Raw response was: %s\n' "${response}" >&2
        die "Upload succeeded but URL parsing failed."
    fi

    open_url "https://lens.google.com/uploadbyurl?url=${url}"

# -----------------------------------------------------------------------------
# CLIPBOARD MODE: Screenshot → Clipboard → Manual Paste
# -----------------------------------------------------------------------------
else

    # Pipeline: grim -> stdout -> wl-copy
    if grim -g "${geometry}" - | wl-copy; then
        notify "Ready" "Screenshot copied. Paste (Ctrl+V) in browser."
        open_url "https://lens.google.com/"
    else
        die "Failed to capture or copy to clipboard."
    fi

fi
