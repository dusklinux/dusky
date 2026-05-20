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

mkdir -p "${SITES_DIR}"

tmpf=$(mktemp)
trap 'rm -f "$tmpf"' EXIT

cat > "$tmpf" << 'CLAUDE_EOF'
@-moz-document domain("claude.ai") {
  html { color-scheme: dark !important; }

  :root {
    --bg-000: var(--surface_container_low) !important;
    --bg-100: var(--surface) !important;
    --bg-200: var(--surface_container) !important;
    --bg-300: var(--surface_container_high) !important;
    --bg-400: var(--surface_container_highest) !important;
    --text-000: var(--on_primary) !important;
    --text-100: var(--on_surface) !important;
    --text-200: var(--on_surface_variant) !important;
    --text-300: var(--outline) !important;
    --text-400: var(--on_surface_variant) !important;
    --text-500: var(--on_surface_variant) !important;
    --border-100: var(--surface_container_high) !important;
    --border-200: var(--outline_variant_rgb) !important;
    --border-300: var(--surface_container_high_rgb) !important;
    --accent-brand: var(--primary) !important;
    --accent-100: var(--primary) !important;
    --accent-200: var(--primary_container) !important;
    --accent-300: var(--secondary) !important;
    --danger-000: var(--error) !important;
    --danger-100: var(--error_container) !important;
    --danger-200: var(--error) !important;
    --warning-000: var(--tertiary) !important;
    --warning-100: var(--tertiary_container) !important;
    --warning-200: var(--tertiary) !important;
    --brand-000: var(--primary) !important;
    --brand-100: var(--primary_container) !important;
    --brand-200: var(--primary) !important;
    --oncolor-100: var(--on_primary) !important;
    --always-black: 0 0 0 !important;
  }

  [data-mode="dark"] .cds-root,
  .cds-root[data-mode="dark"] {
    --cds-surface-3: var(--surface_container_high) !important;
    --cds-surface-popover: var(--surface_container_high) !important;
    --cds-surface-panel: var(--surface_container) !important;
    --cds-text-primary: var(--on_surface) !important;
    --cds-text-secondary: var(--on_surface_variant) !important;
    --cds-text-muted: var(--outline) !important;
    --cds-text-danger: var(--error) !important;
    --cds-border: var(--outline_variant) !important;
    --cds-border-strong: var(--outline) !important;
    --cds-fill-ghost-hover: var(--surface_container) !important;
    --cds-fill-danger: var(--error) !important;
    --cds-fill-danger-hover: var(--error_container) !important;
    --cds-fill-primary-hover: var(--surface_container_high) !important;
    --cds-fill-secondary: var(--surface_container) !important;
    --cds-bg-accent: var(--primary_container) !important;
    --cds-text-accent: var(--primary) !important;
    --cds-on-danger: var(--on_error) !important;
    --cds-bg-danger: var(--error_container) !important;
  }

  [class*="bg-bg-000"] { background-color: var(--surface_container_low) !important; }
  [class*="bg-bg-100"] { background-color: var(--surface) !important; }
  [class*="bg-bg-200"] { background-color: var(--surface_container) !important; }
  [class*="bg-bg-300"] { background-color: var(--surface_container_high) !important; }
  [class*="bg-bg-400"] { background-color: var(--surface_container_highest) !important; }

  [class*="text-text-100"] { color: var(--on_surface) !important; }
  [class*="text-text-200"] { color: var(--on_surface_variant) !important; }
  [class*="text-text-300"] { color: var(--outline) !important; }
  [class*="text-text-400"] { color: var(--on_surface_variant) !important; }
  [class*="text-text-500"] { color: var(--on_surface_variant) !important; }

  [class*="border-border-100"] { border-color: var(--surface_container_high) !important; }
  [class*="border-border-200"] { border-color: var(--outline_variant) !important; }
  [class*="border-border-300"] { border-color: var(--surface_container_high) !important; }

  body {
    background-color: var(--surface) !important;
    color: var(--on_surface);
  }

  a, a:visited {
    color: var(--primary) !important;
  }

  ::selection {
    background-color: var(--primary_container) !important;
    color: var(--on_primary_container) !important;
  }

  div[data-user-message-bubble="true"] {
    background-color: var(--primary_container) !important;
    color: var(--on_primary_container) !important;
  }

  [class*="font-claude-response"],
  [class*="font-claude-response-body"],
  [class*="standard-markdown"] {
    background: transparent !important;
  }

  [class*="group-hover/btn:text-text-100"]:hover,
  [class*="group-hover/btn:text-text-100"]:focus-visible {
    color: var(--primary) !important;
  }

  [class*="text-accent-brand"] {
    color: var(--primary) !important;
  }
}
CLAUDE_EOF

mv "$tmpf" "$CLAUDE_CSS"

log_success "claude.css deployed."

FIREFOX_TUI="${HOME}/user_scripts/theme_matugen/firefox/dusky_firefox_tui.sh"
if [ -f "$FIREFOX_TUI" ]; then
    log_info "activating..."
    bash "$FIREFOX_TUI" --auto
else
    log_warn "firefox tui not found."
    log_info "run dusky_firefox_tui.sh --auto to enable it."
fi

RESTART="${HOME}/user_scripts/theme_matugen/firefox/restart_browser.sh"
if [ -f "$RESTART" ]; then
    bash "$RESTART" &>/dev/null &
fi

log_success "claude.ai theme ready."
