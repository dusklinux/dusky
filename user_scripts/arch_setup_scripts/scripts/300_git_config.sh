#!/usr/bin/env bash
#d: Configure Git's delta pager and diff display

set -euo pipefail

# Keep the existing setup target; do not modify repository-local configuration.
readonly TARGET_FILE="${HOME:?HOME must be set}/.gitconfig"

main() {
    local -a missing_packages=()
    command -v git >/dev/null 2>&1 || missing_packages+=(git)
    command -v delta >/dev/null 2>&1 || missing_packages+=(git-delta)

    # Preinstalled dependencies need neither sudo nor repository access.
    if (( ${#missing_packages[@]} )); then
        printf '[INFO] Installing missing dependencies: %s\n' "${missing_packages[*]}"
        if (( EUID == 0 )); then
            pacman -S --needed --noconfirm "${missing_packages[@]}"
        else
            sudo pacman -S --needed --noconfirm "${missing_packages[@]}"
        fi
    fi

    # Reject malformed existing configuration before changing any settings.
    if [[ -e "$TARGET_FILE" ]]; then
        git config list --file "$TARGET_FILE" --no-includes >/dev/null
    fi

    local key value current status
    local changed=0
    # Keep one display line per patch line; retain delta's long-line performance limit.
    while read -r key value; do
        # Inspect all direct values so duplicate entries are consolidated too.
        # Included files remain user-managed and are never rewritten.
        if current=$(git config get --file "$TARGET_FILE" --no-includes --all "$key"); then
            [[ "$current" == "$value" ]] && continue
        else
            status=$?
            if (( status != 1 )); then
                printf '[ERR] Cannot read %s from %s.\n' "$key" "$TARGET_FILE" >&2
                return "$status"
            fi
        fi

        # Git handles parsing, escaping, newlines, locking and atomic file writes.
        git config set --file "$TARGET_FILE" --all "$key" "$value"
        changed=$((changed + 1))
    done <<'EOF'
core.pager delta
interactive.diffFilter delta --color-only --no-gitconfig
delta.navigate true
delta.side-by-side true
delta.line-numbers true
delta.hyperlinks true
delta.tabs 2
delta.true-color always
delta.hunk-header-style omit
delta.file-style bold yellow
delta.file-decoration-style yellow box
delta.wrap-max-lines 100
EOF

    if (( changed )); then
        printf '[OK] Updated %d delta settings in %s.\n' "$changed" "$TARGET_FILE"
    else
        printf '[OK] Delta settings already configured in %s.\n' "$TARGET_FILE"
    fi
}

main "$@"
