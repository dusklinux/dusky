#!/usr/bin/env bash
# vesktop setup , a better discord client

# ==============================================================================
#  Vesktop & Matugen Automation Suite
#  Target: Arch Linux (Hyprland) | Deps: yay/paru, jq
# ==============================================================================

# --- Strict Mode ---
set -euo pipefail
IFS=$'\n\t'

# --- Formatting (detect color support on stderr, where all logs go) ---
if [[ -t 2 ]]; then
    readonly RED=$'\033[0;31m'
    readonly GREEN=$'\033[0;32m'
    readonly BLUE=$'\033[0;34m'
    readonly YELLOW=$'\033[1;33m'
    readonly NC=$'\033[0m'
else
    readonly RED='' GREEN='' BLUE='' YELLOW='' NC=''
fi

# --- Cleanup Trap & Temp Management ---
_tmp=""
cleanup() {
    local exit_code=$?
    # Clean up temp file if it exists
    [[ -n "$_tmp" ]] && rm -f -- "$_tmp"
    
    if [[ $exit_code -ne 0 ]]; then
        printf '%s[!] Script failed with exit code %d%s\n' "$RED" "$exit_code" "$NC" >&2
    fi
}
trap cleanup EXIT

# --- Helper Functions (all diagnostic output to stderr) ---
log_info()    { printf '%s[INFO]%s %s\n'  "$BLUE"   "$NC" "$1" >&2; }
log_success() { printf '%s[OK]%s %s\n'    "$GREEN"  "$NC" "$1" >&2; }
log_warn()    { printf '%s[WARN]%s %s\n'  "$YELLOW" "$NC" "$1" >&2; }
log_error()   { printf '%s[ERROR]%s %s\n' "$RED"    "$NC" "$1" >&2; exit 1; }

# --- Privilege Check ---
if [[ $EUID -eq 0 ]]; then
    log_error "This script must NOT be run as root. It modifies user configs and uses AUR helpers."
fi

# ==============================================================================
#  Phase 0: User Consent & State Logic
# ==============================================================================

# 1. Check for --auto flag
_auto_mode="false"
for _arg in "$@"; do
    if [[ "$_arg" == "--auto" ]]; then
        _auto_mode="true"
        break
    fi
done

# 2. Define State File
readonly STATE_FILE="$HOME/.config/dusky/settings/vesktop_matugen"

# 3. Logic Flow
if [[ "$_auto_mode" == "false" ]]; then
    if [[ -f "$STATE_FILE" ]]; then
        _state=$(<"$STATE_FILE")
        if [[ "$_state" == "declined" ]]; then
            log_info "Vesktop installation previously declined (found 'declined' in $STATE_FILE). Skipping."
            exit 0
        fi
        # If 'accepted', proceed normally
    else
        # Prompt the user
        printf '%s[?]%s Do you want to install Vesktop? (A Discord client with beautiful Matugen theming and a lot better than the official Discord client with additional features) [Y/n] ' "$YELLOW" "$NC"
        read -r -n 1 _response
        printf '\n' # Fix formatting after read

        mkdir -p -- "${STATE_FILE%/*}"

        if [[ "$_response" =~ ^[Nn]$ ]]; then
            printf 'declined' > "$STATE_FILE"
            log_info "Vesktop installation declined. State saved. Exiting."
            exit 0
        else
            printf 'accepted' > "$STATE_FILE"
            log_success "Vesktop installation accepted. Proceeding..."
        fi
    fi
fi

# --- Detect AUR Helper ---
AUR_HELPER=""
if command -v paru &>/dev/null; then
    AUR_HELPER="paru"
elif command -v yay &>/dev/null; then
    AUR_HELPER="yay"
else
    log_error "Neither 'paru' nor 'yay' found. Please install an AUR helper."
fi
readonly AUR_HELPER

# ==============================================================================
#  Phase 1: Installation
# ==============================================================================
log_info "Phase 1: Package Management..."

# Ensure jq is installed (vital for JSON editing in Phase 4)
if ! command -v jq &>/dev/null; then
    log_info "Installing dependency: jq..."
    sudo pacman -S --needed --noconfirm jq
fi

# Warn early if matugen is missing (config is written regardless)
if ! command -v matugen &>/dev/null; then
    log_warn "'matugen' is not installed. Theme generation will not work until it is."
fi

# Install vesktop-bin
if pacman -Qi vesktop-bin &>/dev/null; then
    log_success "vesktop-bin is already installed."
else
    log_info "Installing vesktop-bin via ${AUR_HELPER}..."
    "$AUR_HELPER" -S --needed --noconfirm vesktop-bin
fi

# ==============================================================================
#  Phase 2: Matugen Configuration
# ==============================================================================
log_info "Phase 2: Configuring Matugen..."

readonly MATUGEN_CONFIG="$HOME/.config/matugen/config.toml"
mkdir -p -- "${MATUGEN_CONFIG%/*}"

# Quoted heredoc ('TOML') prevents Bash expansion; $HOME is expanded later
# by the shell when matugen executes the post_hook.
read -r -d '' VESKTOP_BLOCK << 'TOML' || true
[templates.vesktop]
input_path  = '~/.config/matugen/templates/midnight-discord.css'
output_path = '~/.config/matugen/generated/midnight-discord.css'
post_hook   = '''
mkdir -p -- "$HOME/.config/vesktop/themes/"
ln -nfs -- "$HOME/.config/matugen/generated/midnight-discord.css" "$HOME/.config/vesktop/themes/midnight-discord.css"
'''
TOML
readonly VESKTOP_BLOCK

if [[ ! -f "$MATUGEN_CONFIG" ]]; then
    log_info "Matugen config missing. Creating..."
    printf '%s\n' "$VESKTOP_BLOCK" > "$MATUGEN_CONFIG"
    log_success "Created matugen config."
elif grep -qE '^[[:space:]]*\[templates\.vesktop\]' "$MATUGEN_CONFIG"; then
    log_success "Vesktop template already active in Matugen config."
elif grep -qE '^[[:space:]]*#.*\[templates\.vesktop\]' "$MATUGEN_CONFIG"; then
    log_info "Vesktop template exists but is commented out. Uncommenting autonomously..."
    
    _tmp=$(mktemp -p "${MATUGEN_CONFIG%/*}" .matugen.XXXXXXXXXX)
    
    # 1:1 Robust AWK engine ported from Dusky TUI
    TARGET_KEY="vesktop" NEW_VALUE="true" \
    LC_ALL=C awk '
    function is_blank(line) {
        return line ~ /^[[:space:]]*$/
    }

    function is_comment(line) {
        return line ~ /^[[:space:]]*#/
    }

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
        if (t !~ /^\[templates\.[^]]+\][[:space:]]*(#.*)?$/) {
            return ""
        }
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

        if (!start) {
            exit 1
        }

        for (i = start + 1; i <= line_count; i++) {
            if (!in_multiline && is_toml_header(lines[i])) {
                end = i - 1
                break
            }

            if (!in_multiline && is_blank(lines[i]) && i + 3 <= line_count) {
                c1 = lines[i + 1]
                c2 = lines[i + 2]
                c3 = lines[i + 3]

                s1 = strip_comment_prefix(c1)
                s2 = strip_comment_prefix(c2)
                s3 = strip_comment_prefix(c3)

                if (is_comment(c1) && is_comment(c2) && is_comment(c3) &&
                    s1 ~ /^-{3,}$/ &&
                    s2 !~ /^[[:space:]]*$/ &&
                    s2 !~ /^[[:space:]]*\[/ &&
                    s2 !~ /=/ &&
                    s3 ~ /^-{3,}$/) {

                    j = i + 4
                    while (j <= line_count && is_blank(lines[j])) {
                        j++
                    }

                    if (j > line_count || is_toml_header(lines[j])) {
                        end = i - 1
                        break
                    }
                }
            }

            update_multiline_state(lines[i])
        }

        for (i = 1; i <= line_count; i++) {
            line = lines[i]

            if (i >= start && i <= end) {
                if (ENVIRON["NEW_VALUE"] == "true") {
                    sub(/^#[[:space:]]?/, "", line)
                } else if (ENVIRON["NEW_VALUE"] == "false") {
                    if (line !~ /^#/ && line !~ /^[[:space:]]*$/) {
                        line = "# " line
                    }
                }
            }

            print line
        }
    }
    ' "$MATUGEN_CONFIG" > "$_tmp" || true
    
    if [[ -s "$_tmp" ]]; then
        mv -f -- "$_tmp" "$MATUGEN_CONFIG"
        _tmp="" # Clear so trap doesn't attempt to delete the now-renamed file
        log_success "Matugen Vesktop template activated."
    else
        rm -f -- "$_tmp"
        _tmp=""
        log_warn "Failed to uncomment Vesktop template in Matugen config."
    fi
else
    log_info "Appending Vesktop template to Matugen config..."
    printf '\n%s\n' "$VESKTOP_BLOCK" >> "$MATUGEN_CONFIG"
    log_success "Appended Vesktop template."
fi

# ==============================================================================
#  Phase 3: Pre-emptively Create Theme Symlink
# ==============================================================================
log_info "Phase 3: Enforcing Theme Files..."

readonly SOURCE_CSS="$HOME/.config/matugen/generated/midnight-discord.css"
readonly TARGET_LINK="$HOME/.config/vesktop/themes/midnight-discord.css"

mkdir -p -- "${SOURCE_CSS%/*}" "${TARGET_LINK%/*}"

if [[ ! -f "$SOURCE_CSS" ]]; then
    log_warn "Generated CSS not found. Creating placeholder to allow Vesktop to load..."
    : > "$SOURCE_CSS"
fi

ln -nfs -- "$SOURCE_CSS" "$TARGET_LINK"
log_success "Symlinked midnight-discord.css to Vesktop themes."

# ==============================================================================
#  Phase 4: Vesktop Settings Injection
# ==============================================================================
log_info "Phase 4: Injecting Vesktop Settings..."

readonly THEME_NAME="midnight-discord.css"
readonly SETTINGS_FILE="$HOME/.config/vesktop/settings/settings.json"
mkdir -p -- "${SETTINGS_FILE%/*}"

if [[ ! -f "$SETTINGS_FILE" ]]; then
    log_info "Settings file missing. Creating minimal configuration..."
    # Minimal valid JSON; Vesktop merges with its internal defaults at runtime.
    printf '{"enabledThemes": ["%s"]}\n' "$THEME_NAME" > "$SETTINGS_FILE"
    log_success "Created settings.json with '${THEME_NAME}' enabled."
else
    log_info "Settings file exists. Patching..."

    # Pre-flight: fail fast with a clear message on corrupt JSON.
    if ! jq empty "$SETTINGS_FILE" 2>/dev/null; then
        log_error "Settings file contains invalid JSON: $SETTINGS_FILE"
    fi

    # Create temp file on the SAME filesystem for atomic mv (rename, not copy).
    _tmp=$(mktemp -p "${SETTINGS_FILE%/*}" .settings.XXXXXXXXXX)
    
    # Safely use JQ with a temp file and trap cleanup
    jq --arg theme "$THEME_NAME" '
        if .enabledThemes == null then
            .enabledThemes = [$theme]
        elif (.enabledThemes | index($theme)) == null then
            .enabledThemes += [$theme]
        else
            .
        end
    ' "$SETTINGS_FILE" > "$_tmp"
    
    mv -- "$_tmp" "$SETTINGS_FILE"
    _tmp="" # Clear so trap doesn't attempt to delete the now-renamed file
    log_success "Patched settings.json: '${THEME_NAME}' is enabled."
fi

log_success "Automation Complete. Vesktop is ready."
