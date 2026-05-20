#!/bin/bash

MATUGEN_CONF="$HOME/.config/matugen/config.toml"

echo "Enabling Spicetify template in Matugen config..."
python3 << 'PYEOF'
import re, os

conf_path = os.path.expanduser("~/.config/matugen/config.toml")
with open(conf_path) as f:
    content = f.read()

content = re.sub(
    r'(?m)^# \[templates\.spicetify\]\n(^#[^\n]*\n?)*',
    lambda m: re.sub(r'(?m)^# ?', '', m.group(0)),
    content
)

with open(conf_path, 'w') as f:
    f.write(content)
PYEOF

echo "Regenerating Matugen theme..."
THEME_CTL="$HOME/user_scripts/theme_matugen/theme_ctl.sh"
if [[ -f "$THEME_CTL" ]]; then
    "$THEME_CTL" refresh
else
    echo "theme_ctl.sh not found. Trying direct matugen invocation..."
    if command -v awww &>/dev/null; then
        img=$(awww query 2>/dev/null | grep 'currently displaying: image:' | sed 's/.*image: //')
        if [[ -n "$img" && -f "$img" ]]; then
            matugen image "$img"
        fi
    fi
fi

echo "Done. Spotify colors should update on next launch."
