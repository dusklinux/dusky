#!/usr/bin/env bash
# =============================================================================
# MatugenFox – Autonomous Setup & Provisioning Script
# Version: 3.6.0 (Golden Copy: Cutting-Edge Bash Optimized)
# Target:  Linux (Arch, Fedora, Debian, NixOS, etc.) + macOS
# Purpose: Zero-touch detection of every installed Firefox-family browser,
#          profile resolution, native messaging host installation, config
#          initialization, and autonomous extension deployment.
#          *ORCHESTRATOR SAFE*: Always exits 0 to prevent pipeline breakage.
# =============================================================================

set -euo pipefail
shopt -s extglob # Required for robust whitespace trimming during INI parsing

# Guarantee a 0 exit code even if an unexpected command failure occurs
trap 'exit_code=$?; log_err "Unexpected failure at line $LINENO (code $exit_code)."; log_warn "Exiting gracefully (0) to protect parent orchestrator."; exit 0' ERR

# =============================================================================
# ▼ CONSTANTS ▼
# =============================================================================

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly HOST_DIR="$HOME/user_scripts/theme_matugen/firefox"
readonly HOST_SCRIPT="$HOST_DIR/matugenfox_host.py"
readonly REFRESH_SCRIPT="$HOME/user_scripts/theme_matugen/theme_ctl.sh"
readonly MANIFEST_NAME="matugenfox.json"
readonly CONFIG_FILE="$HOST_DIR/config.json"
readonly EXTENSION_ID="matugenfox@ubaid.com"
readonly XPI_URL="https://addons.mozilla.org/firefox/downloads/latest/matugenfox/latest.xpi"
readonly VERSION="3.6.0"

# =============================================================================
# ▼ VISUAL STYLING ▼
# =============================================================================

if [[ -t 1 ]] && command -v tput &>/dev/null && (( $(tput colors 2>/dev/null || echo 0) >= 8 )); then
    readonly C_RESET=$'\033[0m'
    readonly C_BOLD=$'\033[1m'
    readonly C_CYAN=$'\033[38;5;45m'
    readonly C_GREEN=$'\033[38;5;46m'
    readonly C_MAGENTA=$'\033[38;5;177m'
    readonly C_YELLOW=$'\033[38;5;214m'
    readonly C_RED=$'\033[38;5;196m'
    readonly C_DIM=$'\033[2m'
else
    readonly C_RESET='' C_BOLD='' C_CYAN='' C_GREEN=''
    readonly C_MAGENTA='' C_YELLOW='' C_RED='' C_DIM=''
fi

log_info()    { printf '%b[INFO]%b    %s\n' "$C_CYAN"    "$C_RESET" "$1"; }
log_success() { printf '%b[SUCCESS]%b %s\n' "$C_GREEN"   "$C_RESET" "$1"; }
log_warn()    { printf '%b[WARNING]%b %s\n' "$C_YELLOW"  "$C_RESET" "$1" >&2; }
log_err()     { printf '%b[ERROR]%b   %s\n' "$C_RED"     "$C_RESET" "$1" >&2; }
die()         { log_err "$1"; log_warn "Bailing out, but exiting safely (0) for orchestrator."; exit 0; }

# =============================================================================
# ▼ HELPERS ▼
# =============================================================================

# Hardened sudo check: Prevents orchestrator hangs if password is required 
# and the shell is running non-interactively.
can_use_sudo() {
    if ! command -v sudo &>/dev/null; then return 1; fi
    # If we have a TTY, we can safely prompt for a password
    if [[ -t 0 ]] && sudo -v < /dev/null &>/dev/null; then return 0; fi
    # If we are non-interactive, check if passwordless sudo is allowed
    if sudo -n true &>/dev/null; then return 0; fi
    return 1
}

# =============================================================================
# ▼ REGISTRY ▼
# =============================================================================

declare -A BROWSER_DIRS=()
declare -a BROWSER_ORDER=()
declare -A BROWSER_NMH_RESOLVED=()
declare -A BROWSER_POLICY_RESOLVED=()

# Global array for resolved profile paths
declare -ga RESOLVED_PROFILES=()

declare -A BROWSER_LABEL=(
    ["firefox"]="Firefox"
    ["librewolf"]="LibreWolf"
    ["zen"]="Zen Browser"
    ["waterfox"]="Waterfox"
    ["floorp"]="Floorp"
    ["firedragon"]="FireDragon"
)

declare -A BROWSER_BINARIES=(
    ["firefox"]="firefox"
    ["librewolf"]="librewolf"
    ["zen"]="zen-browser"
    ["waterfox"]="waterfox"
    ["floorp"]="floorp"
    ["firedragon"]="firedragon"
)

declare -A BROWSER_PROFILE_CANDIDATES=()
declare -A BROWSER_NMH_CANDIDATES=()
declare -A BROWSER_POLICY_DIRS=()

declare -ra SCAN_ORDER=("firefox" "librewolf" "zen" "waterfox" "floorp" "firedragon")

init_platform_paths() {
    if [[ "${OSTYPE:-}" == "darwin"* ]]; then
        BROWSER_PROFILE_CANDIDATES=(
            ["firefox"]="$HOME/Library/Application Support/Firefox/Profiles"
            ["librewolf"]="$HOME/Library/Application Support/LibreWolf/Profiles"
            ["zen"]="$HOME/Library/Application Support/Zen/Profiles"
            ["waterfox"]="$HOME/Library/Application Support/Waterfox/Profiles"
            ["floorp"]="$HOME/Library/Application Support/Floorp/Profiles"
        )
        BROWSER_NMH_CANDIDATES=(
            ["firefox"]="$HOME/Library/Application Support/Mozilla/NativeMessagingHosts"
            ["librewolf"]="$HOME/Library/Application Support/LibreWolf/NativeMessagingHosts"
            ["zen"]="$HOME/Library/Application Support/Mozilla/NativeMessagingHosts"
            ["waterfox"]="$HOME/Library/Application Support/Waterfox/NativeMessagingHosts"
            ["floorp"]="$HOME/Library/Application Support/Floorp/NativeMessagingHosts"
        )
        BROWSER_POLICY_DIRS=(
            ["firefox"]="/Applications/Firefox.app/Contents/Resources/distribution"
            ["librewolf"]="/Applications/LibreWolf.app/Contents/Resources/distribution"
            ["zen"]="/Applications/Zen.app/Contents/Resources/distribution"
            ["waterfox"]="/Applications/Waterfox.app/Contents/Resources/distribution"
            ["floorp"]="/Applications/Floorp.app/Contents/Resources/distribution"
        )
    else
        BROWSER_PROFILE_CANDIDATES=(
            ["firefox"]="$HOME/.mozilla/firefox $HOME/.config/mozilla/firefox $HOME/.var/app/org.mozilla.firefox/.mozilla/firefox"
            ["librewolf"]="$HOME/.librewolf $HOME/.var/app/io.gitlab.librewolf-community/.librewolf"
            ["zen"]="$HOME/.zen $HOME/.config/zen"
            ["waterfox"]="$HOME/.waterfox"
            ["floorp"]="$HOME/.floorp"
            ["firedragon"]="$HOME/.firedragon"
        )
        BROWSER_NMH_CANDIDATES=(
            ["firefox"]="$HOME/.mozilla/native-messaging-hosts $HOME/.var/app/org.mozilla.firefox/.mozilla/native-messaging-hosts"
            ["librewolf"]="$HOME/.librewolf/native-messaging-hosts $HOME/.var/app/io.gitlab.librewolf-community/.librewolf/native-messaging-hosts"
            ["zen"]="$HOME/.zen/native-messaging-hosts $HOME/.config/zen/native-messaging-hosts"
            ["waterfox"]="$HOME/.waterfox/native-messaging-hosts"
            ["floorp"]="$HOME/.floorp/native-messaging-hosts"
            ["firedragon"]="$HOME/.firedragon/native-messaging-hosts"
        )
        BROWSER_POLICY_DIRS=(
            ["firefox"]="/usr/lib/firefox/distribution /usr/lib64/firefox/distribution /etc/firefox/policies"
            ["librewolf"]="/usr/lib/librewolf/distribution /usr/lib64/librewolf/distribution /etc/librewolf/policies"
            ["zen"]="/usr/lib/zen/distribution /etc/zen/policies"
            ["waterfox"]="/usr/lib/waterfox/distribution /etc/waterfox/policies"
            ["floorp"]="/usr/lib/floorp/distribution /etc/floorp/policies"
            ["firedragon"]="/usr/lib/firedragon/distribution /etc/firedragon/policies"
        )
    fi
}

browser_is_real() {
    local browser_id="$1" base_dir="$2"
    local bin="${BROWSER_BINARIES[$browser_id]:-}"
    if [[ -n "$bin" ]] && command -v "$bin" &>/dev/null; then return 0; fi
    if [[ -f "$base_dir/profiles.ini" ]]; then return 0; fi
    if find "$base_dir" -maxdepth 2 -type f -name "prefs.js" -print -quit 2>/dev/null | grep -q .; then return 0; fi
    return 1
}

resolve_profiles() {
    local base_dir="$1"
    RESOLVED_PROFILES=()
    local -A seen=()

    local ini="$base_dir/profiles.ini"
    if [[ -f "$ini" ]]; then
        local default_rel=""
        while IFS='=' read -r key val; do
            # Safely trim trailing and leading spaces via extglob
            key="${key##*( )}"; key="${key%%*( )}"
            val="${val##*( )}"; val="${val%%*( )}"
            
            if [[ "$key" == "Default" && -n "$val" ]]; then
                [[ "$val" == /* ]] && default_rel="$val" || default_rel="$base_dir/$val"
                if [[ -d "$default_rel" && -z "${seen[$default_rel]:-}" ]]; then
                    RESOLVED_PROFILES+=("$default_rel")
                    seen["$default_rel"]=1
                fi
            fi
        done < <(grep -A1 '^\[Install' "$ini" 2>/dev/null | grep -i '^Default' || true)
    fi

    # Modern Bash array mapping for null-delimited 'find' output 
    local -a dirs
    local pattern
    for pattern in "*.default-release" "*.default" "*.Default*"; do
        readarray -d '' dirs < <(find "$base_dir" -maxdepth 1 -type d -name "$pattern" -print0 2>/dev/null | sort -z)
        for dir in "${dirs[@]}"; do
            [[ -z "$dir" ]] && continue
            if [[ -z "${seen[$dir]:-}" ]]; then
                RESOLVED_PROFILES+=("$dir")
                seen["$dir"]=1
            fi
        done
    done

    # Fallback checking prefs.js to catch exceptionally named profiles
    local -a pref_files
    readarray -d '' pref_files < <(find "$base_dir" -mindepth 2 -maxdepth 2 -type f -name "prefs.js" -print0 2>/dev/null | sort -z)
    for prefs_file in "${pref_files[@]}"; do
        [[ -z "$prefs_file" ]] && continue
        local p_dir
        p_dir="$(dirname "$prefs_file")"
        [[ "$p_dir" == "$base_dir" ]] && continue
        if [[ -z "${seen[$p_dir]:-}" ]]; then
            RESOLVED_PROFILES+=("$p_dir")
            seen["$p_dir"]=1
        fi
    done
}

# =============================================================================
# ▼ PHASE 1: DEPENDENCY CHECK ▼
# =============================================================================

check_dependencies() {
    log_info "Checking system dependencies..."
    local missing=()
    for dep in matugen python3 jq awk sed; do
        if ! command -v "$dep" &>/dev/null; then missing+=("$dep"); fi
    done

    if (( ${#missing[@]} > 0 )); then
        if command -v pacman &>/dev/null; then
            log_info "Arch Linux detected. Attempting to install missing dependencies: ${missing[*]}"
            if can_use_sudo; then
                sudo pacman -S --needed --noconfirm "${missing[@]}" || die "Failed to install dependencies via pacman."
                log_success "Dependencies installed successfully."
            else
                die "Missing dependencies: ${missing[*]}. Interactive 'sudo' is required, but unavailable or disabled."
            fi
        else
            log_warn "Missing dependencies: ${missing[*]}"
            log_warn "Please install them manually using your system's package manager (apt, dnf, brew) before continuing."
            log_warn "Setup will proceed, but features may be broken."
            sleep 3
        fi
    else
        log_success "All dependencies present."
    fi
}

# =============================================================================
# ▼ PHASE 2: DISCOVER ▼
# =============================================================================

discover_browsers() {
    log_info "Scanning for installed Firefox-family browsers..."
    for browser_id in "${SCAN_ORDER[@]}"; do
        local candidates="${BROWSER_PROFILE_CANDIDATES[$browser_id]:-}"
        [[ -z "$candidates" ]] && continue
        for candidate in $candidates; do
            if [[ -d "$candidate" ]] && browser_is_real "$browser_id" "$candidate"; then
                BROWSER_DIRS["$browser_id"]="$candidate"
                BROWSER_ORDER+=("$browser_id")
                log_success "Found ${BROWSER_LABEL[$browser_id]:-$browser_id} → $candidate"
                break
            fi
        done
    done

    if [[ ${#BROWSER_ORDER[@]} -eq 0 ]]; then
        die "No supported Firefox-based browser detected. Install one first."
    fi
    log_info "Discovered ${#BROWSER_ORDER[@]} browser(s)."
}

# =============================================================================
# ▼ PHASE 3: NATIVE HOST ▼
# =============================================================================

install_native_host() {
    log_info "Installing native messaging host manifest..."
    if [[ ! -f "$HOST_SCRIPT" ]]; then die "Host script not found at $HOST_SCRIPT."; fi
    chmod +x "$HOST_SCRIPT"

    local -i installed=0
    for browser_id in "${BROWSER_ORDER[@]}"; do
        local nmh_candidates="${BROWSER_NMH_CANDIDATES[$browser_id]:-}"
        [[ -z "$nmh_candidates" ]] && continue
        for nmh_dir in $nmh_candidates; do
            local nmh_parent="${nmh_dir%/*}"
            if [[ -d "$nmh_parent" ]]; then
                mkdir -p "$nmh_dir"
                cat > "$nmh_dir/$MANIFEST_NAME" <<MANIFEST
{
  "name": "matugenfox",
  "description": "MatugenFox Native Messaging Host",
  "path": "$HOST_SCRIPT",
  "type": "stdio",
  "allowed_extensions": [
    "$EXTENSION_ID"
  ]
}
MANIFEST
                BROWSER_NMH_RESOLVED["$browser_id"]="$nmh_dir"
                installed=$((installed + 1))
                log_success "Manifest → $nmh_dir"

                if [[ "$nmh_dir" == *".var/app/"* ]] && command -v flatpak &>/dev/null; then
                    local app_id="${nmh_dir#*.var/app/}"
                    app_id="${app_id%%/*}"
                    log_info "Applying Flatpak sandbox filesystem overrides for $app_id..."
                    flatpak override --user --filesystem="$HOST_DIR" --filesystem="$HOME/.config/matugen" --filesystem="$HOME/.config/dusky_sites" "$app_id" || log_warn "Failed to apply Flatpak override."
                fi

                break
            fi
        done
    done

    if (( installed == 0 )); then log_warn "Could not install NMH manifest. Parent dirs missing."
    else log_info "Installed NMH manifest into $installed browser(s)."
    fi
}

# =============================================================================
# ▼ PHASE 4: EXTENSION POLICY ▼
# =============================================================================

deploy_extension_policy() {
    log_info "Deploying Enterprise Policy for automatic extension installation..."
    if ! command -v jq &>/dev/null; then
        log_warn "jq not found. Cannot safely inject policies. Skipping extension auto-install."
        return
    fi

    local -i deployed=0
    local tmp_policy
    tmp_policy=$(mktemp)

    cat > "$tmp_policy" <<EOF
{
  "policies": {
    "ExtensionSettings": {
      "*": { "installation_mode": "allowed" },
      "${EXTENSION_ID}": {
        "installation_mode": "normal_installed",
        "install_url": "${XPI_URL}"
      }
    }
  }
}
EOF

    for browser_id in "${BROWSER_ORDER[@]}"; do
        local policy_candidates="${BROWSER_POLICY_DIRS[$browser_id]:-}"
        [[ -z "$policy_candidates" ]] && continue

        for p_dir in $policy_candidates; do
            if [[ -d "${p_dir%/*}" || "$p_dir" == /etc/* ]]; then
                local target="$p_dir/policies.json"
                local write_cmd="cp"
                local mkdir_cmd="mkdir -p"
                
                if [[ ! -w "${p_dir%/*}" && ! -w "$p_dir" ]]; then
                    if can_use_sudo; then
                        write_cmd="sudo cp"
                        mkdir_cmd="sudo mkdir -p"
                    else
                        log_warn "Need sudo to write to $p_dir, but sudo is unavailable or denied. Skipping."
                        continue
                    fi
                fi

                if ! $mkdir_cmd "$p_dir" 2>/dev/null; then
                    log_warn "Failed to create directory $p_dir. Skipping."
                    continue
                fi

                if [[ -f "$target" ]]; then
                    log_info "Merging policy into existing $target..."
                    local merged_tmp
                    merged_tmp=$(mktemp)
                    
                    if jq --arg ext "$EXTENSION_ID" --arg url "$XPI_URL" \
                       '.policies //= {} | .policies.ExtensionSettings //= {} | .policies.ExtensionSettings[$ext] = {"installation_mode": "normal_installed", "install_url": $url} | if .policies.ExtensionSettings["*"] == null then .policies.ExtensionSettings["*"] = {"installation_mode": "allowed"} else . end' \
                       "$target" > "$merged_tmp"; then
                        if ! $write_cmd "$merged_tmp" "$target" 2>/dev/null; then
                            log_warn "Failed to write merged policy to $target."
                        fi
                        rm -f "$merged_tmp"
                    else
                        log_warn "Failed to merge policy for $target. Skipping to prevent corruption."
                        rm -f "$merged_tmp"
                        continue
                    fi
                else
                    if ! $write_cmd "$tmp_policy" "$target" 2>/dev/null; then
                        log_warn "Failed to write policy to $target."
                        continue
                    fi
                fi
                
                if [[ "$write_cmd" == "sudo cp" ]]; then
                    sudo chmod 644 "$target" 2>/dev/null || true
                else
                    chmod 644 "$target" 2>/dev/null || true
                fi

                BROWSER_POLICY_RESOLVED["$browser_id"]="$target"
                log_success "Policy deployed → $target"
                deployed=$((deployed + 1))
                break
            fi
        done
    done

    rm -f "$tmp_policy"

    if (( deployed == 0 )); then
        log_warn "Could not deploy enterprise policy. You will need to install the extension manually."
    fi
}

# =============================================================================
# ▼ PHASE 5: BOOTSTRAP ▼
# =============================================================================

bootstrap_profiles() {
    log_info "Bootstrapping browser profiles..."
    local -i total_profiles=0
    for browser_id in "${BROWSER_ORDER[@]}"; do
        local base="${BROWSER_DIRS[$browser_id]}"
        local label="${BROWSER_LABEL[$browser_id]:-$browser_id}"

        resolve_profiles "$base"
        for profile_path in "${RESOLVED_PROFILES[@]}"; do
            local profile_name="${profile_path##*/}"
            mkdir -p "$profile_path/chrome"
            
            local user_js="$profile_path/user.js"
            if ! grep -q "toolkit.legacyUserProfileCustomizations.stylesheets" "$user_js" 2>/dev/null; then
                printf '\nuser_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);\n' >> "$user_js"
                log_success "$label/$profile_name: Enabled custom CSS loading"
            fi
            total_profiles=$((total_profiles + 1))
        done
    done
    log_info "Bootstrapped $total_profiles profile(s)."
}

# =============================================================================
# ▼ PHASE 6: CONFIG ▼
# =============================================================================

init_config() {
    local force_run=$1
    
    mkdir -p "${CONFIG_FILE%/*}" || true

    if [[ -f "$CONFIG_FILE" ]] && [[ -s "$CONFIG_FILE" ]] && python3 -c "import json; json.load(open('$CONFIG_FILE'))" 2>/dev/null; then
        if (( ! force_run )); then
            log_info "config.json already exists. Skipping."
            return 0
        else
            log_info "config.json already exists, but --force passed. Overwriting."
        fi
    fi
    log_info "Initializing default config.json..."
    
    cat > "$CONFIG_FILE" <<'CONFIG'
{
  "smoothTransitions": false,
  "ecoMode": true,
  "showSyncIndicator": true,
  "transitionMs": 300,
  "autoDisableDarkSites": false,
  "nakedMode": false,
  "paletteShortcut": "ctrl+alt+c",
  "colorsPath": "~/.config/matugen/generated/firefox_websites.css",
  "websitesDir": "~/.config/dusky_sites",
  "presets": [],
  "blocklist": []
}
CONFIG
    log_success "Default config.json created."
}

# =============================================================================
# ▼ PHASE 7: MATUGEN TOML INTEGRATION ▼
# =============================================================================

update_matugen_toml() {
    local toml_file="$HOME/.config/matugen/config.toml"
    
    if [[ ! -f "$toml_file" ]]; then
        log_warn "Matugen config not found at $toml_file. Skipping TOML integration."
        return 0
    fi

    log_info "Updating Matugen TOML with detected Firefox profiles..."
    
    local hook_cmds=""
    for browser_id in "${BROWSER_ORDER[@]}"; do
        local base="${BROWSER_DIRS[$browser_id]}"
        resolve_profiles "$base"
        for profile_path in "${RESOLVED_PROFILES[@]}"; do
            hook_cmds+="    ln -nfs \"\$HOME/.config/matugen/generated/firefox_websites.css\" \"$profile_path/chrome/colors.css\" || :"$'\n'
        done
    done

    if [[ -z "$hook_cmds" ]]; then
        log_warn "No profiles found for TOML integration."
        return 0
    fi

    local tmp_toml
    tmp_toml=$(mktemp)
    
    awk '
    /^[ \t]*\[[ \t]*templates\.firefox_websites[ \t]*\]/ { skip = 1; next }
    /^[ \t]*\[{1,2}[a-zA-Z0-9_.-]+(\.[a-zA-Z0-9_.-]+)*\]{1,2}[ \t]*$/ && skip { skip = 0 }
    !skip { print }
    ' "$toml_file" | awk 'NF > 0 {last = NR} {lines[NR] = $0} END {for (i = 1; i <= last; i++) print lines[i]}' > "$tmp_toml"

    echo "" >> "$tmp_toml"

    cat <<EOF >> "$tmp_toml"
[templates.firefox_websites]
input_path = '~/.config/matugen/templates/firefox_websites.css'
output_path = '~/.config/matugen/generated/firefox_websites.css'
post_hook = '''
$hook_cmds'''
EOF

    cp "$tmp_toml" "$toml_file"
    rm -f "$tmp_toml"
    
    log_success "Matugen TOML updated securely (old configuration overwritten cleanly)."
}

# =============================================================================
# ▼ PHASE 8: THEME REFRESH ▼
# =============================================================================

run_theme_refresh() {
    if [[ -x "$REFRESH_SCRIPT" ]]; then
        log_info "Running theme_ctl.sh refresh to generate Matugen colors..."
        "$REFRESH_SCRIPT" refresh || log_warn "theme_ctl.sh encountered an error."
    elif [[ -f "$REFRESH_SCRIPT" ]]; then
        log_info "Running theme_ctl.sh refresh via bash..."
        bash "$REFRESH_SCRIPT" refresh || log_warn "theme_ctl.sh encountered an error."
    else
        log_warn "Refresh script not found at $REFRESH_SCRIPT. Skipping color generation."
    fi
}

# =============================================================================
# ▼ UNINSTALLATION ▼
# =============================================================================

perform_uninstall() {
    log_info "Initiating MatugenFox uninstallation sequence..."
    
    discover_browsers

    # 1. Remove NMH Manifests & Flatpak overrides
    log_info "Removing Native Messaging Host manifests..."
    for browser_id in "${BROWSER_ORDER[@]}"; do
        local nmh_candidates="${BROWSER_NMH_CANDIDATES[$browser_id]:-}"
        for nmh_dir in $nmh_candidates; do
            if [[ -f "$nmh_dir/$MANIFEST_NAME" ]]; then
                rm -f "$nmh_dir/$MANIFEST_NAME"
                log_success "Removed manifest from $nmh_dir"
            fi
            
            if [[ "$nmh_dir" == *".var/app/"* ]] && command -v flatpak &>/dev/null; then
                local app_id="${nmh_dir#*.var/app/}"
                app_id="${app_id%%/*}"
                log_info "Reverting Flatpak filesystem overrides for $app_id..."
                flatpak override --user --nofilesystem="$HOST_DIR" --nofilesystem="$HOME/.config/matugen" --nofilesystem="$HOME/.config/dusky_sites" "$app_id" || true
            fi
        done
    done

    # 2. Revert Enterprise Policies (Cleaner jq payload to remove dangling keys)
    log_info "Reverting Enterprise Policies..."
    if command -v jq &>/dev/null; then
        for browser_id in "${BROWSER_ORDER[@]}"; do
            local policy_candidates="${BROWSER_POLICY_DIRS[$browser_id]:-}"
            for p_dir in $policy_candidates; do
                local target="$p_dir/policies.json"
                if [[ -f "$target" ]]; then
                    local write_cmd="cp"
                    if [[ ! -w "$target" ]] && can_use_sudo; then write_cmd="sudo cp"; fi
                    
                    local merged_tmp
                    merged_tmp=$(mktemp)
                    
                    if jq --arg ext "$EXTENSION_ID" 'del(.policies.ExtensionSettings[$ext]) | if .policies.ExtensionSettings == {} then del(.policies.ExtensionSettings) else . end | if .policies == {} then {} else . end' "$target" > "$merged_tmp"; then
                        $write_cmd "$merged_tmp" "$target" 2>/dev/null || log_warn "Failed to revert policy at $target"
                        log_success "Cleaned policy at $target"
                    fi
                    rm -f "$merged_tmp"
                fi
            done
        done
    else
        log_warn "jq not found. Cannot safely revert policies.json automatically."
    fi

    # 3. Clean Browser Profiles (Safe awk swap, avoids macOS `sed -i` quirks)
    log_info "Cleaning browser profiles..."
    for browser_id in "${BROWSER_ORDER[@]}"; do
        local base="${BROWSER_DIRS[$browser_id]}"
        resolve_profiles "$base"
        for profile_path in "${RESOLVED_PROFILES[@]}"; do
            local user_js="$profile_path/user.js"
            if [[ -f "$user_js" ]]; then
                # Safe fallback using awk to strip the specific line, preserving the rest of the file
                awk '!/toolkit\.legacyUserProfileCustomizations\.stylesheets/' "$user_js" > "$user_js.tmp" || true
                if [[ -s "$user_js.tmp" ]]; then
                    mv "$user_js.tmp" "$user_js" 2>/dev/null || rm -f "$user_js.tmp"
                else
                    rm -f "$user_js.tmp" 2>/dev/null || true
                fi
                log_success "Cleaned user.js in ${profile_path##*/}"
            fi
            
            if [[ -L "$profile_path/chrome/colors.css" ]]; then
                rm -f "$profile_path/chrome/colors.css"
                log_success "Removed colors.css symlink in ${profile_path##*/}"
            fi
        done
    done

    # 4. Remove Config File
    if [[ -f "$CONFIG_FILE" ]]; then
        rm -f "$CONFIG_FILE"
        log_success "Removed config.json"
    fi

    # 5. Remove Matugen TOML Block
    local toml_file="$HOME/.config/matugen/config.toml"
    if [[ -f "$toml_file" ]] && command -v awk &>/dev/null; then
        log_info "Removing Firefox block from Matugen TOML..."
        local tmp_toml
        tmp_toml=$(mktemp)
        awk '
        /^[ \t]*\[[ \t]*templates\.firefox_websites[ \t]*\]/ { skip = 1; next }
        /^[ \t]*\[{1,2}[a-zA-Z0-9_.-]+(\.[a-zA-Z0-9_.-]+)*\]{1,2}[ \t]*$/ && skip { skip = 0 }
        !skip { print }
        ' "$toml_file" | awk 'NF > 0 {last = NR} {lines[NR] = $0} END {for (i = 1; i <= last; i++) print lines[i]}' > "$tmp_toml"
        cp "$tmp_toml" "$toml_file"
        rm -f "$tmp_toml"
        log_success "Cleaned Matugen TOML."
    fi

    echo ""
    log_success "MatugenFox uninstallation complete."
    exit 0
}

# =============================================================================
# ▼ SUMMARY ▼
# =============================================================================

print_report() {
    echo ""
    printf '%b%b' "$C_BOLD" "$C_CYAN"
    cat <<'BANNER'
  ╔══════════════════════════════════════════╗
  ║        MATUGENFOX SETUP COMPLETE         ║
  ╚══════════════════════════════════════════╝
BANNER
    printf '%b\n' "$C_RESET"

    for browser_id in "${BROWSER_ORDER[@]}"; do
        local label="${BROWSER_LABEL[$browser_id]:-$browser_id}"
        local base="${BROWSER_DIRS[$browser_id]}"
        local nmh="${BROWSER_NMH_RESOLVED[$browser_id]:-not installed}"
        local policy="${BROWSER_POLICY_RESOLVED[$browser_id]:-manual install needed}"

        echo "  ┌─ ${C_BOLD}${label}${C_RESET}"
        echo "  │  Profiles: ${base}"
        echo "  │  NMH:      ${nmh}"
        echo "  │  Policy:   ${policy}"

        resolve_profiles "$base"
        for profile_path in "${RESOLVED_PROFILES[@]}"; do
            local pname="${profile_path##*/}"
            local chrome_status="${C_RED}✗${C_RESET}"
            [[ -d "$profile_path/chrome" ]] && chrome_status="${C_GREEN}✓${C_RESET}"
            local userjs_status="${C_RED}✗${C_RESET}"
            grep -q "legacyUserProfileCustomizations" "$profile_path/user.js" 2>/dev/null && userjs_status="${C_GREEN}✓${C_RESET}"
            echo "  │  ${chrome_status} chrome/  ${userjs_status} user.js  ${C_DIM}${pname}${C_RESET}"
        done
        echo "  └──────────────────────────────────────"
    done

    echo ""
    echo "  ${C_BOLD}Next steps:${C_RESET}"
    echo "  1. Restart your browser(s) to apply the new enterprise policy and Matugen colors."
    echo ""
}

# =============================================================================
# ▼ HELP ▼
# =============================================================================

show_help() {
    cat <<EOF
${C_BOLD}MatugenFox Setup v${VERSION}${C_RESET}
Autonomous detection and provisioning for all Firefox-family browsers.

${C_BOLD}Usage:${C_RESET} $(basename "$0") [OPTIONS]

${C_BOLD}Options:${C_RESET}
  -h, --help           Show this help message and exit.
  -f, --force          Force run execution (overwrites skip conditions/idempotency checks).
  -u, --uninstall      Remove configurations, overrides, and policies added by this script.
  --detect-only        Only detect browsers and profiles; don't install anything.
  --skip-dependencies  Skip automatic package manager dependency installation.
  --skip-extension     Skip deploying the enterprise policy for auto-install.
  --skip-bootstrap     Skip profile bootstrapping (chrome/ dir, user.js injection).
  --skip-config        Skip config.json initialization.
EOF
}

# =============================================================================
# ▼ ENTRYPOINT ▼
# =============================================================================

main() {
    local detect_only=0
    local skip_deps=0
    local skip_ext=0
    local skip_bootstrap=0
    local skip_config=0
    local force_run=0
    local uninstall=0

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)           show_help; exit 0 ;;
            -f|--force)          force_run=1 ;;
            -u|--uninstall)      uninstall=1 ;;
            --detect-only)       detect_only=1 ;;
            --skip-dependencies) skip_deps=1 ;;
            --skip-extension)    skip_ext=1 ;;
            --skip-bootstrap)    skip_bootstrap=1 ;;
            --skip-config)       skip_config=1 ;;
            *) log_warn "Unknown option: $1"; show_help; exit 0 ;;
        esac
        shift
    done

    if (( EUID == 0 )); then die "Do not run as root. Run as your normal user."; fi

    echo ""
    printf '%b>>> MatugenFox Setup v%s%b\n\n' "$C_CYAN" "$VERSION" "$C_RESET"

    init_platform_paths

    if (( uninstall )); then
        perform_uninstall
    fi

    if (( ! skip_deps )); then check_dependencies; fi

    discover_browsers

    if (( detect_only )); then
        for browser_id in "${BROWSER_ORDER[@]}"; do
            echo "  ┌─ ${BROWSER_LABEL[$browser_id]:-$browser_id}"
            resolve_profiles "${BROWSER_DIRS[$browser_id]}"
            for profile_path in "${RESOLVED_PROFILES[@]}"; do
                echo "  │  ✓  ${profile_path##*/}"
            done
            echo "  └──────────────────────────────────"
        done
        exit 0
    fi

    install_native_host
    if (( ! skip_ext )); then deploy_extension_policy; fi
    if (( ! skip_bootstrap )); then bootstrap_profiles; fi
    if (( ! skip_config )); then init_config "$force_run"; fi
    
    update_matugen_toml
    run_theme_refresh

    print_report
}

main "$@"
