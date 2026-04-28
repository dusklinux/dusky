#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: 405_spicetify_matugen_setup.sh
# Description: "Golden State" Spicetify Setup.
#              - Resurrection: Detects & fixes deleted/phantom installs.
#              - Warm-Up: Robust monitor for 'prefs' AND 'offline.bnk'.
#              - Auto-Heals: Segfaults, Version Mismatches, and Permissions.
#              - Matugen: Autonomously uncomments TOML block via robust AWK.
# -----------------------------------------------------------------------------

# Strict Mode
set -Eeuo pipefail

# --- Configuration ---
readonly REQUIRED_BASH_MAJOR=5
readonly REQUIRED_BASH_MINOR=3
readonly SPICETIFY_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/spicetify"
readonly SPOTIFY_PREFS_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/spotify"
readonly SPOTIFY_AUR_KEY="931FF8E79F0876134EDDBDCCA87FF9DF48BF1C90"

# --- Visual Feedback ---
if [[ -t 1 ]]; then
    readonly C_RESET=$'\033[0m'
    readonly C_INFO=$'\033[1;34m'    # Blue
    readonly C_SUCCESS=$'\033[1;32m' # Green
    readonly C_WARN=$'\033[1;33m'    # Yellow
    readonly C_ERR=$'\033[1;31m'     # Red
    readonly C_MAG=$'\033[1;35m'     # Magenta
else
    readonly C_RESET='' C_INFO='' C_SUCCESS='' C_WARN='' C_ERR='' C_MAG=''
fi

log_info()    { printf '%s[INFO]%s %s\n' "${C_INFO}" "${C_RESET}" "$*"; }
log_success() { printf '%s[OK]%s %s\n' "${C_SUCCESS}" "${C_RESET}" "$*"; }
log_warn()    { printf '%s[WARN]%s %s\n' "${C_WARN}" "${C_RESET}" "$*" >&2; }
log_heal()    { printf '%s[HEAL]%s %s\n' "${C_MAG}" "${C_RESET}" "$*"; }
die()         { printf '%s[FATAL]%s %s\n' "${C_ERR}" "${C_RESET}" "$*" >&2; exit 1; }

# --- 1. Guard Rails & Dependencies ---
check_system() {
    if [[ $EUID -eq 0 ]]; then die "Do not run as root."; fi
    
    if ((BASH_VERSINFO[0] < REQUIRED_BASH_MAJOR)) || \
       ((BASH_VERSINFO[0] == REQUIRED_BASH_MAJOR && BASH_VERSINFO[1] < REQUIRED_BASH_MINOR)); then
        die "Bash 5.3+ required. Current: ${BASH_VERSION}"
    fi

    local missing=()
    for cmd in curl sudo pkill awk; do
        if ! command -v "$cmd" &>/dev/null; then missing+=("$cmd"); fi
    done
    
    if [[ ${#missing[@]} -gt 0 ]]; then die "Missing dependencies: ${missing[*]}"; fi
}

# --- 2. Smart Install (Phantom Detection) ---
ensure_packages_updated() {
    log_info "Checking Spotify installation integrity..."
    
    local helper
    if command -v paru &>/dev/null; then helper="paru"
    elif command -v yay &>/dev/null; then helper="yay"
    else die "No AUR helper found."; fi

    if ! gpg --list-keys "$SPOTIFY_AUR_KEY" &>/dev/null; then
        log_info "Importing Spotify GPG key..."
        if ! gpg --keyserver keyserver.ubuntu.com --recv-keys "$SPOTIFY_AUR_KEY"; then
             log_warn "GPG import failed. Installation might fail if key is missing."
        fi
    fi

    # PHANTOM CHECK: Package installed but directory gone?
    local phantom_install=false
    if pacman -Qi spotify &>/dev/null; then
        if [[ ! -d "/opt/spotify" && ! -d "/usr/share/spotify" ]]; then
            phantom_install=true
        fi
    fi

    if [[ "$phantom_install" == "true" ]]; then
        log_heal "Phantom install detected. Forcing Reinstall..."
        "$helper" -S --noconfirm spotify spicetify-cli
    else
        "$helper" -S --needed --noconfirm spotify spicetify-cli
    fi
    
    log_success "Packages are current."
}

# --- 3. Path & Permissions ---
fix_spotify_permissions() {
    log_info "Locating Spotify..."
    
    local spotify_path
    spotify_path=${| 
        if [[ -d "/opt/spotify" ]]; then REPLY="/opt/spotify"
        elif [[ -d "$HOME/.local/share/spotify-launcher/install/usr/share/spotify" ]]; then
            REPLY="$HOME/.local/share/spotify-launcher/install/usr/share/spotify"
        elif [[ -d "/usr/share/spotify" ]]; then REPLY="/usr/share/spotify"
        else REPLY=""; fi
    }

    if [[ -z "$spotify_path" ]]; then die "Could not locate Spotify directory."; fi

    if [[ -w "$spotify_path" && -w "$spotify_path/Apps" ]]; then
        log_success "Permissions OK ($spotify_path)."
    else
        log_warn "Fixing permissions for $spotify_path..."
        if sudo chmod a+wr "${spotify_path}" && \
           sudo chmod -R a+wr "${spotify_path}/Apps"; then
            log_success "Permissions granted."
        else
            die "Failed to grant permissions."
        fi
    fi
    REPLY="$spotify_path"
}

# --- 4. The Warm Up (Monitored Initialization) ---
warm_up_spotify() {
    local prefs_file="${SPOTIFY_PREFS_DIR}/prefs"
    
    # Verify BOTH prefs and offline.bnk exist
    if [[ -f "$prefs_file" ]] && find "$HOME/.cache/spotify" -name "offline.bnk" 2>/dev/null | grep -q "offline.bnk"; then
        log_success "Core files ('prefs' and 'offline.bnk') found. Spotify is ready."
        return 0
    fi

    log_warn "Spotify is missing required init files ('prefs' or 'offline.bnk')."
    log_warn "=========================================================="
    log_warn " Spotify is launching..."
    log_warn " PLEASE LOG IN or KEEP WINDOW OPEN to generate cache."
    log_warn "=========================================================="
    
    # Launch in background and allow kernel scheduling buffer
    nohup spotify >/dev/null 2>&1 &
    sleep 3
    
    log_info "Waiting for initialization..."
    
    local wait_time=0
    local max_wait=300 # 5 minute hard timeout
    
    while [[ ! -f "$prefs_file" ]] || ! find "$HOME/.cache/spotify" -name "offline.bnk" 2>/dev/null | grep -q "offline.bnk"; do
        # Defend against premature closure (user closing window, crash)
        if ! pgrep -u "$EUID" -x spotify >/dev/null; then
            log_warn "Spotify process died prematurely. Relaunching..."
            nohup spotify >/dev/null 2>&1 &
            sleep 3
        fi
        
        sleep 2
        ((wait_time+=2))
        
        if ((wait_time >= max_wait)); then
            kill_spotify_hard
            die "Timeout: 5 minutes elapsed waiting for Spotify initialization. Aborting."
        fi
    done
    
    log_success "Initialization complete! All core files generated."
    
    # Allow Spotify daemon a moment to flush local configs to disk before killing
    sleep 3
    kill_spotify_hard
}

# --- 5. Configuration & Assets ---
configure_spicetify() {
    local install_path="$1"
    log_info "Configuring Spicetify Paths..."
    
    # Ensure Spicetify generates default configs
    if [[ ! -f "$SPICETIFY_CONFIG_DIR/config-xpui.ini" ]]; then
        spicetify config >/dev/null 2>&1 || true
    fi

    spicetify config \
        spotify_path "${install_path}" \
        prefs_path "${SPOTIFY_PREFS_DIR}/prefs" \
        current_theme Comfy \
        color_scheme Comfy \
        inject_css 1 \
        replace_colors 1 \
        overwrite_assets 1 \
        inject_theme_js 1 \
        extensions adblock.js \
        custom_apps marketplace > /dev/null
}

prepare_assets() {
    log_info "Downloading assets..."
    
    # Marketplace (Using || true to prevent early exit if it attempts to apply and fails)
    local mk_dir="${SPICETIFY_CONFIG_DIR}/CustomApps/marketplace"
    if [[ ! -d "$mk_dir" ]]; then
        log_info "Installing Marketplace..."
        curl -fsSL "https://raw.githubusercontent.com/spicetify/spicetify-marketplace/main/resources/install.sh" | bash || true
    fi

    # Adblock
    local ext_dir="${SPICETIFY_CONFIG_DIR}/Extensions"
    mkdir -p "$ext_dir"
    if [[ ! -f "$ext_dir/adblock.js" ]]; then
        log_info "Installing Adblock extension..."
        curl -fsSL "https://raw.githubusercontent.com/rxri/spicetify-extensions/main/adblock/adblock.js" -o "$ext_dir/adblock.js" || true
    fi

    # Native Comfy Theme Setup (Bypasses the rogue install.sh entirely)
    local comfy_dir="${SPICETIFY_CONFIG_DIR}/Themes/Comfy"
    mkdir -p "$comfy_dir"
    
    log_info "Installing Comfy Theme natively..."
    curl -fsSL --output "$comfy_dir/user.css" "https://raw.githubusercontent.com/Comfy-Themes/Spicetify/main/Comfy/user.css"
    curl -fsSL --output "$comfy_dir/theme.js" "https://raw.githubusercontent.com/Comfy-Themes/Spicetify/main/Comfy/theme.js"

    # Matugen Protection: Do not overwrite if the orchestrator has already created the symlink
    if [[ ! -L "$comfy_dir/color.ini" ]]; then
        curl -fsSL --output "$comfy_dir/color.ini" "https://raw.githubusercontent.com/Comfy-Themes/Spicetify/main/Comfy/color.ini"
    else
        log_success "Matugen symlink detected for color.ini. Preserving."
    fi
}

# --- 6. Process Management ---
kill_spotify_hard() {
    if pgrep -u "$EUID" -x spotify >/dev/null; then
        log_info "Closing Spotify..."
        pkill -u "$EUID" -x spotify || true
        
        local retries=50
        while ((retries > 0)); do
            if ! pgrep -u "$EUID" -x spotify >/dev/null; then return 0; fi
            sleep 0.1
            ((retries--))
        done
        
        log_warn "Forcing shutdown..."
        pkill -9 -u "$EUID" -x spotify || true
    fi
}

# --- 7. The Nuclear Heal ---
nuke_cache_and_heal() {
    log_heal "Performing NUCLEAR cleanup..."
    kill_spotify_hard
    
    rm -f "${SPICETIFY_CONFIG_DIR}/backup.json"
    
    local helper
    if command -v paru &>/dev/null; then helper="paru"
    elif command -v yay &>/dev/null; then helper="yay"
    else die "No AUR helper found for reinstall."; fi
    
    log_heal "Reinstalling binary..."
    "$helper" -S --noconfirm spotify

    # Scrub volatile caching while preserving user prefs
    rm -rf "$HOME/.cache/spotify"
    rm -rf "$HOME/.config/spotify/Users" 
    rm -rf "$HOME/.config/spotify/GPUCache"
    log_success "Cache nuked."

    local path="$1"
    if [[ -d "${path}" ]]; then
        sudo chmod a+wr "${path}"
        sudo chmod -R a+wr "${path}/Apps"
    fi
    
    # Validation check to regenerate 'offline.bnk' missing after cache wipe
    warm_up_spotify
}

apply_changes() {
    local install_path="$1"
    
    log_info "Applying patches..."
    kill_spotify_hard

    # Primary vector
    if spicetify backup apply enable-devtools; then
        log_success "Spicetify applied successfully."
        return 0
    fi

    # Fallback vector
    log_heal "Patch failed. Initiating Nuclear Protocol."
    nuke_cache_and_heal "$install_path"
    
    # State reset
    fix_spotify_permissions
    install_path="$REPLY"
    configure_spicetify "$install_path"

    log_heal "Retrying injection..."
    if spicetify backup apply enable-devtools; then
        log_success "System healed and patched."
    else
        die "Spicetify failed. Please check logs."
    fi
}

# --- 8. Matugen Integration ---
uncomment_matugen_template() {
    local matugen_conf="${XDG_CONFIG_HOME:-$HOME/.config}/matugen/config.toml"
    
    if [[ -f "$matugen_conf" ]]; then
        log_info "Enabling Spicetify template in Matugen config..."
        local tmp_conf
        tmp_conf=$(mktemp)
        
        # Robust AWK engine adapted from Dusky TUI to safely navigate multiline strings
        TARGET_KEY="spicetify" NEW_VALUE="true" \
        LC_ALL=C awk '
        function is_blank(line) { return line ~ /^[[:space:]]*$/ }
        function is_comment(line) { return line ~ /^[[:space:]]*#/ }
        function strip_comment_prefix(line,    t) {
            t = line
            sub(/^[[:space:]]*#[[:space:]]?/, "", t)
            return t
        }
        function is_toml_header(line,    t) {
            t = line
            sub(/^[[:space:]]*#?[[:space:]]*/, "", t)
            return t ~ /^\[.*\][[:space:]]*(#.*)?$/
        }
        function template_name(line,    t) {
            t = line
            sub(/^[[:space:]]*#?[[:space:]]*/, "", t)
            if (t !~ /^\[templates\.[^]]+\][[:space:]]*(#.*)?$/) { return "" }
            sub(/^\[templates\./, "", t)
            sub(/\][[:space:]]*(#.*)?$/, "", t)
            return t
        }
        function count_token(str, tok,    n, p, step, rest) {
            n = 0
            rest = str
            step = length(tok)
            p = index(rest, tok)
            while (p) {
                n++
                rest = substr(rest, p + step)
                p = index(rest, tok)
            }
            return n
        }
        function update_multiline_state(line,    s, c) {
            s = strip_comment_prefix(line)
            if (!in_multiline) {
                c = count_token(s, triple_sq)
                if (c % 2 == 1) {
                    in_multiline = 1
                    multiline_token = triple_sq
                    return
                }
                c = count_token(s, triple_dq)
                if (c % 2 == 1) {
                    in_multiline = 1
                    multiline_token = triple_dq
                }
                return
            }
            c = count_token(s, multiline_token)
            if (c % 2 == 1) {
                in_multiline = 0
                multiline_token = ""
            }
        }
        {
            lines[++line_count] = $0
        }
        END {
            triple_sq = sprintf("%c%c%c", 39, 39, 39)
            triple_dq = "\"\"\""
            start = 0
            end = line_count
            in_multiline = 0
            multiline_token = ""
            for (i = 1; i <= line_count; i++) {
                if (template_name(lines[i]) == ENVIRON["TARGET_KEY"]) {
                    start = i
                    break
                }
            }
            if (!start) exit 1
            for (i = start + 1; i <= line_count; i++) {
                if (!in_multiline && is_toml_header(lines[i])) {
                    end = i - 1
                    break
                }
                update_multiline_state(lines[i])
            }
            for (i = 1; i <= line_count; i++) {
                line = lines[i]
                if (i >= start && i <= end) {
                    if (ENVIRON["NEW_VALUE"] == "true") {
                        sub(/^#[[:space:]]?/, "", line)
                    }
                }
                print line
            }
        }
        ' "$matugen_conf" > "$tmp_conf" || true
        
        if [[ -s "$tmp_conf" ]]; then
            mv -f "$tmp_conf" "$matugen_conf"
            log_success "Matugen Spicetify template activated."
        else
            rm -f "$tmp_conf"
            log_warn "Spicetify block not found in Matugen config."
        fi
    fi
}

# --- Main Runtime ---
main() {
    check_system
    ensure_packages_updated
    
    local detected_path
    fix_spotify_permissions
    detected_path="$REPLY"

    # Normalize environment
    kill_spotify_hard
    
    # State generation & path mapping
    warm_up_spotify
    configure_spicetify "$detected_path"
    
    prepare_assets
    apply_changes "$detected_path"

    # Autonomously enable Matugen theming since Spicetify succeeded
    uncomment_matugen_template

    echo ""
    log_success "Setup Complete."
    
    if ! pgrep -u "$EUID" -x spotify >/dev/null; then
        nohup spotify >/dev/null 2>&1 &
        disown # Detaches the process so the shell doesn't output "Killed" on exit
    fi
}

main "$@"
