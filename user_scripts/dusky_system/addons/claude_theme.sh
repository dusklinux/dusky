#!/usr/bin/env bash
set -euo pipefail

C_RESET='\033[0m'
C_INFO='\033[1;34m'
C_SUCCESS='\033[1;32m'
C_ERROR='\033[1;31m'

log_info() { printf "${C_INFO}[INFO]${C_RESET} %s\n" "$1"; }
log_success() { printf "${C_SUCCESS}[OK]${C_RESET} %s\n" "$1"; }
log_error() { printf "${C_ERROR}[ERR]${C_RESET} %s\n" "$1"; }

SITES_DIR="${HOME}/.config/dusky_sites"
CLAUDE_CSS="${SITES_DIR}/claude.css"
SOURCE_CSS="$(dirname "$0")/../../.config/dusky_sites/claude.css"

# find absolute path if running from somewhere else
if [ ! -f "$SOURCE_CSS" ]; then
    SOURCE_CSS="${HOME}/.config/dusky_sites/claude.css"
fi

mkdir -p "$SITES_DIR"

if [ -f "$CLAUDE_CSS" ]; then
    log_info "claude.css already installed."
else
    if [ -f "$SOURCE_CSS" ]; then
        cp "$SOURCE_CSS" "$CLAUDE_CSS"
    else
        log_error "can't find claude.css source"
        exit 1
    fi
    log_success "claude.css installed."
fi

# run the firefox tui to deploy
FIREFOX_TUI="${HOME}/user_scripts/theme_matugen/firefox/dusky_firefox_tui.sh"
if [ -f "$FIREFOX_TUI" ]; then
    log_info "deploying firefox theme..."
    bash "$FIREFOX_TUI" --auto
else
    log_info "firefox tui not found, just the css is in place."
    log_info "run dusky_firefox_tui.sh --auto to activate it."
fi

log_success "done. claude.ai will theme on next matugen refresh."
