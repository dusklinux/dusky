#!/usr/bin/env bash
# =============================================================================
# MatugenFox – Autonomous Setup & Provisioning Script
# Version: 3.3.0
# Target:  Linux (Arch, Fedora, Debian, NixOS, etc.) + macOS
# Purpose: Zero-touch detection of every installed Firefox-family browser,
#          profile resolution, native messaging host installation, config
#          initialization, and autonomous extension deployment.
#          *ORCHESTRATOR SAFE*: Always exits 0 to prevent pipeline breakage.
# =============================================================================

set -euo pipefail

# Guarantee a 0 exit code even if an unexpected command failure occurs
trap 'exit_code=$?; log_err "Unexpected failure at line $LINENO (code $exit_code)."; log_warn "Exiting gracefully (0) to protect parent orchestrator."; exit 0' ERR

# =============================================================================
# ▼ CONSTANTS ▼
# =============================================================================

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly HOST_SCRIPT="$HOME/user_scripts/theme_matugen/firefox/matugenfox_host.py"
readonly REFRESH_SCRIPT="$HOME/user_scripts/theme_matugen/theme_ctl.sh"
readonly MANIFEST_NAME="matugenfox.json"
readonly CONFIG_FILE="$SCRIPT_DIR/config.json"
readonly EXTENSION_ID="matugenfox@ubaid.com"
readonly XPI_URL="https://addons.mozilla.org/firefox/downloads/latest/matugenfox/latest.xpi"
readonly VERSION="3.3.0"

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
# ▼ REGISTRY ▼
# =============================================================================

declare -A BROWSER_DIRS=()
declare -a BROWSER_ORDER=()
declare -A BROWSER_NMH_RESOLVED=()
declare -A BROWSER_POLICY_RESOLVED=()

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

declare -ga RESOLVED_PROFILES=()
resolve_profiles() {
    local base_dir="$1"
    RESOLVED_PROFILES=()
    local -A seen=()

    local ini="$base_dir/profiles.ini"
    if [[ -f "$ini" ]]; then
        local default_rel=""
        while IFS='=' read -r key val; do
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

    local pattern dir
    for pattern in "*.default-release" "*.default" "*.Default*"; do
        while IFS= read -r -d '' dir; do
            [[ -z "$dir" ]] && continue
            if [[ -z "${seen[$dir]:-}" ]]; then
                RESOLVED_PROFILES+=("$dir")
                seen["$dir"]=1
            fi
        done < <(find "$base_dir" -maxdepth 1 -type d -name "$pattern" -print0 2>/dev/null | sort -z)
    done

    local prefs_file
    while IFS= read -r -d '' prefs_file; do
        dir="$(dirname "$prefs_file")"
        [[ -z "$dir" || "$dir" == "$base_dir" ]] && continue
        if [[ -z "${seen[$dir]:-}" ]]; then
            RESOLVED_PROFILES+=("$dir")
            seen["$dir"]=1
        fi
    done < <(find "$base_dir" -mindepth 2 -maxdepth 2 -type f -name "prefs.js" -print0 2>/dev/null | sort -z)
}

# =============================================================================
# ▼ PHASE 1: DEPENDENCY CHECK ▼
# =============================================================================

check_dependencies() {
    log_info "Checking system dependencies..."
    local missing=()
    for dep in matugen python3 jq awk; do
        if ! command -v "$dep" &>/dev/null; then missing+=("$dep"); fi
    done

    if (( ${#missing[@]} > 0 )); then
        if command -v pacman &>/dev/null; then
            log_info "Arch Linux detected. Attempting to install missing dependencies: ${missing[*]}"
            if command -v sudo &>/dev/null; then
                sudo pacman -S --needed --noconfirm "${missing[@]}" || die "Failed to install dependencies via pacman."
                log_success "Dependencies installed successfully."
            else
                die "Missing dependencies: ${missing[*]}. 'sudo' is required to run pacman."
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

    # Base payload
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
                    if command -v sudo &>/dev/null && sudo -v &>/dev/null; then
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
                    # Merge using jq to avoid breaking existing policies like Pywalfox
                    log_info "Merging policy into existing $target..."
                    local merged_tmp
                    merged_tmp=$(mktemp)
                    
                    if jq --arg ext "$EXTENSION_ID" --arg url "$XPI_URL" \
                       '.policies.ExtensionSettings[$ext] = {"installation_mode": "normal_installed", "install_url": $url} | if .policies.ExtensionSettings["*"] == null then .policies.ExtensionSettings["*"] = {"installation_mode": "allowed"} else . end' \
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
                echo 'user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);' >> "$user_js"
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
    
    # 1. Safely remove existing [templates.firefox_websites] block via robust awk pattern
    # 2. Pipe through a second awk block to strip out any trailing empty lines at the EOF
    awk '
    /^[ \t]*\[[ \t]*templates\.firefox_websites[ \t]*\]/ { skip = 1; next }
    /^[ \t]*\[/ && skip { skip = 0 }
    !skip { print }
    ' "$toml_file" | awk 'NF > 0 {last = NR} {lines[NR] = $0} END {for (i = 1; i <= last; i++) print lines[i]}' > "$tmp_toml"

    # Add a clean buffer newline before appending
    echo "" >> "$tmp_toml"

    # Append the freshly generated block dynamically without duplication
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

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)           show_help; exit 0 ;;
            -f|--force)          force_run=1 ;;
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
    if (( BASH_VERSINFO[0] < 4 )); then die "Bash 4.0+ required (you have ${BASH_VERSION})."; fi

    echo ""
    printf '%b>>> MatugenFox Setup v%s%b\n\n' "$C_CYAN" "$VERSION" "$C_RESET"

    init_platform_paths

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
