#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$ROOT/user_scripts/dusky_system/control_center/dusky_config.toml"
README="$ROOT/.config/ags/README.md"
NOTES="$ROOT/docs/adaptive-glass/REVIEW_NOTES.md"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

contains() {
    local file="$1"
    local needle="$2"
    grep -F -- "$needle" "$file" >/dev/null || fail "missing in ${file#$ROOT/}: $needle"
}

python - "$CONFIG" <<'PY'
from pathlib import Path
import sys
import tomllib

config = tomllib.loads(Path(sys.argv[1]).read_text())

def walk(items):
    for item in items or []:
        yield item
        yield from walk(item.get("layout"))
        yield from walk(item.get("items"))

pages = config.get("pages", [])

status_nodes = [
    item
    for page in pages
    for item in walk(page.get("layout"))
    if item.get("type") == "navigation"
    and item.get("properties", {}).get("title") == "Status Bar"
]
assert status_nodes, "missing dedicated Status Bar navigation"
all_items = list(walk(status_nodes[0].get("layout")))
titles = {item.get("properties", {}).get("title") for item in all_items}
assert "Use Adaptive Bar" in titles, "missing Use Adaptive Bar control"
assert "Use Waybar" in titles, "missing Use Waybar control"
assert "Check Adaptive Dependencies" in titles, "missing dependency check control"
commands = [
    action.get("command", "")
    for item in all_items
    for action in [item.get("on_press") or {}]
]
assert any(".config/ags/install.sh --interactive --activate" in command for command in commands), "adaptive control must run interactive installer and activate"
assert any("bar_switch.sh waybar" in command for command in commands), "waybar fallback control must switch to waybar"
assert any(".config/ags/install.sh --check" in command for command in commands), "dependency check control must run install --check"
PY

contains "$CONFIG" 'title = "Use Adaptive Bar"'
contains "$CONFIG" 'command = "kitty --class adaptive_glass_install --title \"Adaptive Glass Installer\" --hold sh -c \"$HOME/.config/ags/install.sh --interactive --activate\""'
contains "$CONFIG" 'command = "$HOME/user_scripts/bar/bar_switch.sh waybar"'
contains "$CONFIG" 'command = "kitty --class adaptive_glass_check --title \"Adaptive Glass Dependency Check\" --hold sh -c \"$HOME/.config/ags/install.sh --check\""'
contains "$README" 'Charles Hangoma <charleshangoma7@gmail.com>'
contains "$README" '--interactive'
contains "$README" '--auto'
contains "$README" '--activate'
contains "$README" '.adaptive-glass-managed'
contains "$NOTES" 'Charles Hangoma <charleshangoma7@gmail.com>'
contains "$NOTES" 'Use Adaptive Bar'

if grep -RIn 'novar' "$CONFIG" "$README" "$NOTES" >/dev/null; then
    fail "found misspelled novar reference"
fi

printf 'adaptive glass control center tests: PASS\n'
