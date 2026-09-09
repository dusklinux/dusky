#!/usr/bin/env bash
# =============================================================================
# Dusky Package Atlas
#
# Requirements:
#   pacman, expac, gawk, GNU coreutils
#   fzf for interactive mode
#   wl-copy optionally, for Wayland clipboard integration
#
# Without count: interactive browser; default is newest first.
# With count:    CLI listing; default is largest first.
#
# Interactive stdout contains only the selected package name.
# Diagnostics and desktop feedback go to stderr.
#
# The package list is a startup snapshot. Relaunch to refresh it.
# Previews query current installed-package metadata.
# =============================================================================

_pkg_help() {
    cat <<'EOF'
Dusky Package Atlas

Usage:
  pkg [target] [metric] [count] [--desktop]

Arguments may appear in any order.
Repeated targets, metrics, and counts use the last supplied value.

Targets:
  all                     All installed packages (default)
  explicit, user          Explicitly installed packages

Metrics:
  hogs, size, big, fat,
  massive, huge, giant    Largest first

  tiny, small, micro,
  mini, little            Smallest first

  new, recent, latest     Newest installation first
  old, ancient            Oldest installation first
  alpha, name             Alphabetical order

Modes:
  Without count           Interactive browser; default: newest first
  With positive count     CLI listing; default: largest first

Options:
  --desktop               Show selection/clipboard feedback on stderr
  -h, --help, help        Show this help

Examples:
  pkg
  pkg explicit hogs
  pkg 50 size
  pkg old 20
  pkg explicit alpha 100

Interactive shortcuts:
  Ctrl-S                  Largest first
  Alt-S                   Smallest first
  Ctrl-D                  Newest first
  Alt-D                   Oldest first
  Ctrl-R                  Alphabetical order
  Ctrl-P                  Show/hide right-hand details
  Ctrl-L                  Refresh current package details
  Page Up / Page Down     Scroll details by a page
  Shift-Up / Shift-Down   Scroll details by a line
  Alt-C                   Copy the complete details report
  Enter                   Print and attempt to copy the package name
  F1                      Show keyboard help
  Esc                     Exit without selecting

Notes:
  Searching matches package names and the displayed date/size.
  Descriptions and versions are shown in the details pane.

  Searching preserves the selected sort order.

  The package list is a startup snapshot. Relaunch to refresh it.
  Ctrl-L refreshes details only, not the package list.

  Dates and installed sizes come from pacman's database.
  Dates are not necessarily first-ever installation dates.
  Sizes are not measurements of actual filesystem allocation.

  "user" means explicitly installed, not installed by the current Unix user.

  Integration sections show package-owned paths. They do not indicate
  whether a service is enabled/running or whether a file still exists.

  Command paths cover standard bin directories. Libexec paths are listed
  separately. This is not a scan of every executable file in the package.

  Enter replaces the clipboard with the package name.
  To retain details copied with Alt-C, exit with Escape instead.

  Clipboard support requires wl-copy and a working Wayland connection.

  --desktop does not create a terminal. A desktop launcher must use
  Terminal=true or explicitly run this script in a terminal emulator.

  CLI output omits colors when redirected, when TERM=dumb,
  or when NO_COLOR is set.
EOF
}

_pkg_keys() {
    printf '\033[2J\033[H'

    cat <<'EOF'

  DUSKY PACKAGE ATLAS
  ────────────────────────────────────────────────────────

  SORT
    Ctrl-S       Largest first
    Alt-S        Smallest first
    Ctrl-D       Newest first
    Alt-D        Oldest first
    Ctrl-R       Alphabetical order

  DETAILS
    Ctrl-P       Show / hide right-hand details
    Ctrl-L       Refresh current package details
    Page Up      Scroll details upward
    Page Down    Scroll details downward
    Shift-Up     Scroll details upward one line
    Shift-Down   Scroll details downward one line
    Alt-C        Copy the complete details report

  SELECT
    Enter        Print and attempt to copy package name
    Esc          Exit without selecting

  Searching preserves the selected sort order.
  Relaunch to refresh the installed-package list.

  Services and timers are listed near the top of the details.
  Full package metadata is available farther down.

  Enter replaces the clipboard with the package name.
  After Alt-C, use Esc if you want to retain copied details.

EOF

    printf '  Press any key to return...'
    IFS= read -r -s -n 1 < /dev/tty
    printf '\n'
}

_pkg_require() {
    local dependency
    local missing=0

    for dependency in "$@"; do
        if ! command -v "$dependency" >/dev/null 2>&1; then
            printf 'Error: required command not found: %s\n' \
                "$dependency" >&2
            missing=1
        fi
    done

    (( missing == 0 ))
}

_pkg_fetch() (
    set -o pipefail
    export LC_ALL=C

    local target="$1"
    local names

    # Raw fields: package name | version | install timestamp | bytes.
    if [[ "$target" == explicit ]]; then
        # Check pacman separately so expac cannot hide its failure.
        names=$(pacman -Qeq) || return 1

        # Do not pass an empty target stream to expac.
        [[ -n "$names" ]] || return 0

        printf '%s\n' "$names" |
            expac -Q --timefmt='%s' '%n|%v|%l|%m' -
    else
        expac -Q --timefmt='%s' '%n|%v|%l|%m'
    fi
)

_pkg_sort() {
    local mode="$1"

    # Bound numeric keys explicitly and break ties by package name.
    case "$mode" in
        size_desc)
            LC_ALL=C sort -t '|' -k4,4nr -k1,1
            ;;
        size_asc)
            LC_ALL=C sort -t '|' -k4,4n -k1,1
            ;;
        date_desc)
            LC_ALL=C sort -t '|' -k3,3nr -k1,1
            ;;
        date_asc)
            LC_ALL=C sort -t '|' -k3,3n -k1,1
            ;;
        alpha)
            LC_ALL=C sort -t '|' -k1,1
            ;;
        *)
            printf 'Error: invalid internal sort mode: %s\n' \
                "$mode" >&2
            return 2
            ;;
    esac
}

_pkg_theme() {
    local theme_file="$HOME/.config/matugen/generated/dusky_tui.json"
    local values key hex rgb

    _pkg_require python || return 1

    if ! values=$(
        python - "$theme_file" <<'PY'
import json
import re
import sys
from pathlib import Path

keys = ("bg", "fg", "accent", "error", "warning", "success", "muted")
path = Path(sys.argv[1])

try:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")

    for key in keys:
        value = data.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError(f"{key!r} must contain a color formatted as #RRGGBB")

    for key in keys:
        print(key.upper(), data[key])
except (OSError, UnicodeError, ValueError) as exc:
    print(f"Error: cannot load theme {path}: {exc}", file=sys.stderr)
    sys.exit(1)
PY
    ); then
        return 1
    fi

    while read -r key hex; do
        export "DUSKY_THEME_${key}=$hex"

        printf -v rgb '\033[38;2;%d;%d;%dm' \
            "$((16#${hex:1:2}))" \
            "$((16#${hex:3:2}))" \
            "$((16#${hex:5:2}))"

        export "DUSKY_COLOR_${key}=$rgb"
    done <<< "$values"
}

_pkg_list() (
    set -o pipefail

    _pkg_sort "$1" < "$DUSKY_PKG_DATA" |
        gawk -F '|' '
            BEGIN {
                success = ENVIRON["DUSKY_COLOR_SUCCESS"]
                warning = ENVIRON["DUSKY_COLOR_WARNING"]
                accent = ENVIRON["DUSKY_COLOR_ACCENT"]
                reset = "\033[0m"
            }

            function human_size(bytes) {
                if (bytes >= 1099511627776)
                    return sprintf("%.2f TiB", bytes / 1099511627776)
                if (bytes >= 1073741824)
                    return sprintf("%.2f GiB", bytes / 1073741824)
                if (bytes >= 1048576)
                    return sprintf("%.2f MiB", bytes / 1048576)
                if (bytes >= 1024)
                    return sprintf("%.2f KiB", bytes / 1024)
                return sprintf("%.0f B", bytes)
            }

            {
                printf "%s\t", $1
                printf "%s%s%s", success, strftime("%Y-%m-%d", $3), reset
                printf " %s%10s%s", warning, human_size($4), reset
                printf " \033[1m%s%s%s\n", accent, $1, reset
            }
        '
)

_pkg_integration() {
    gawk '
        function section(title, text, count) {
            printf ":: %s (%d)\n", title, count
            printf "%s\n", text != "" ? text : "  (None)\n"
        }

        function unit_entry(path, name) {
            name = path
            sub(/^.*\//, "", name)
            return "  " name "\n    " path "\n"
        }

        {
            path = $0

            # Directory entries are not command files or units.
            if (path == "" || path ~ /\/$/)
                next

            is_unit_root = (path ~ /^(\/usr\/lib|\/usr\/local\/lib|\/usr\/share|\/etc)\/systemd\/(system|user)\//)
            is_unit = (path ~ /\.(service|socket|device|mount|automount|swap|target|path|timer|slice|scope)$/)
            is_command = (path ~ /^\/(usr\/(local\/)?)?s?bin\/[^/]+$/)
            is_helper = (path ~ /^\/usr\/(local\/)?libexec\//)
            is_desktop = (path ~ /^\/usr\/(local\/)?share\/applications\/.*\.desktop$/)

            if (is_unit_root && is_unit) {
                if (path ~ /\/systemd\/system\//) {
                    system_units = system_units unit_entry(path)
                    system_count++
                } else {
                    user_units = user_units unit_entry(path)
                    user_count++
                }
            } else if (is_unit_root && path ~ /\.conf$/) {
                configs = configs "  " path "\n"
                config_count++
            } else if (is_command) {
                commands = commands "  " path "\n"
                command_count++
            } else if (is_helper) {
                helpers = helpers "  " path "\n"
                helper_count++
            } else if (is_desktop) {
                desktops = desktops "  " path "\n"
                desktop_count++
            }
        }

        END {
            section("System units", system_units, system_count)
            section("User units", user_units, user_count)
            section("Systemd configuration / drop-ins", configs, config_count)
            section("Command paths", commands, command_count)
            section("Libexec helper paths", helpers, helper_count)
            section("Desktop entries", desktops, desktop_count)
        }
    '
}

_pkg_details() (
    set -o pipefail

    local pkg="$1"
    local info
    local paths
    local summary
    local integration

    if [[ -z "$pkg" ]]; then
        printf 'Error: no package name supplied.\n' >&2
        return 1
    fi

    if ! info=$(LC_ALL=C pacman --color never -Qi -- "$pkg"); then
        printf 'Error: pacman could not read installed metadata for %s.\n' \
            "$pkg" >&2
        return 1
    fi

    if ! paths=$(LC_ALL=C pacman -Qql -- "$pkg"); then
        printf 'Error: pacman could not read the file list for %s.\n' \
            "$pkg" >&2
        return 1
    fi

    # Overview first, integration second, complete metadata last.
    # Preserve continuation lines belonging to overview fields.
    if ! summary=$(
        gawk '
            /^[^[:space:]][^:]*:/ {
                key = $0
                sub(/:.*/, "", key)
                sub(/[[:space:]]+$/, "", key)

                keep = (key == "Name" || key == "Version" || key == "Description" || key == "Installed Size" || key == "Install Date" || key == "Install Reason")
            }

            keep {
                print
            }
        ' <<< "$info"
    ); then
        printf 'Error: could not format the package overview.\n' >&2
        return 1
    fi

    if ! integration=$(_pkg_integration <<< "$paths"); then
        printf 'Error: could not format package integration details.\n' >&2
        return 1
    fi

    # Emit only after all required queries and formatting succeed.
    # Do not infer AUR/repository origin from a package-name lookup.
    printf '%s\n' \
        ":: Package: $pkg" \
        "$summary" \
        '' \
        "$integration" \
        ':: Complete installed-package metadata' \
        "$info" \
        '' \
        ':: Interpretation' \
        'Integration sections list package-owned paths, not service state.' \
        'Unit counts count paths, including aliases or enablement links.' \
        'Command paths cover standard bin directories; helpers cover libexec.' \
        'Installed size is database metadata, not measured disk allocation.'
)

_pkg_preview() (
    set -o pipefail

    local details
    local status

    if details=$(_pkg_details "$1" 2>&1); then
        :
    else
        status=$?
        printf '%s\033[1mCould not generate details for: %s\033[0m\n\n' \
            "$DUSKY_COLOR_ERROR" "$1"
        printf '%s\n' "$details"
        return "$status"
    fi

    gawk '
        BEGIN {
            accent = ENVIRON["DUSKY_COLOR_ACCENT"]
            foreground = ENVIRON["DUSKY_COLOR_FG"]
            warning = ENVIRON["DUSKY_COLOR_WARNING"]
            success = ENVIRON["DUSKY_COLOR_SUCCESS"]
            muted = ENVIRON["DUSKY_COLOR_MUTED"]
            reset = "\033[0m"
        }

        /^:: / {
            printf "\033[1m%s%s%s\n", accent, $0, reset
            next
        }

        /^[^[:space:]][^:]*:/ {
            separator = index($0, ":")
            key = substr($0, 1, separator - 1)
            value = substr($0, separator + 1)

            printf "\033[1m%s%s%s:", accent, key, reset
            printf "%s%s%s\n", foreground, value, reset
            next
        }

        /^  [^ /].*\.(service|socket|device|mount|automount|swap|target|path|timer|slice|scope)$/ {
            printf "\033[1m%s%s%s\n", warning, $0, reset
            next
        }

        /^    \// {
            printf "%s%s%s\n", foreground, $0, reset
            next
        }

        /^  \// {
            printf "%s%s%s\n", success, $0, reset
            next
        }

        /^  \(None\)$/ {
            printf "%s%s%s\n", muted, $0, reset
            next
        }

        {
            printf "%s%s%s\n", foreground, $0, reset
        }
    ' <<< "$details"
)

_pkg_copy_details() (
    set -o pipefail

    local details

    command -v wl-copy >/dev/null 2>&1 || return 1
    details=$(_pkg_details "$1") || return 1

    # Copy the complete plain-text report, not just the visible preview.
    printf '%s\n' "$details" | wl-copy --type text/plain
)

_pkg_interactive() {
    local mode="$1"
    local target="$2"
    local desktop="$3"
    local prompt columns header choice status pkg
    local copied=0

    _pkg_theme || return 1

    case "$mode" in
        size_desc) prompt=' Largest › ' ;;
        size_asc)  prompt=' Smallest › ' ;;
        date_desc) prompt=' Newest › ' ;;
        date_asc)  prompt=' Oldest › ' ;;
        alpha)     prompt=' Alphabetical › ' ;;
        *)
            printf 'Error: invalid interactive sort mode: %s\n' \
                "$mode" >&2
            return 2
            ;;
    esac

    printf -v columns '%-10s %10s %s' INSTALLED SIZE PACKAGE
    printf -v header '%s\n%s\n\n%s' \
        'Enter: select · Alt-C: copy details' \
        'PgUp/PgDn: scroll details · F1: help' \
        "$columns"

    export -f _pkg_sort _pkg_list _pkg_integration _pkg_details
    export -f _pkg_preview _pkg_copy_details _pkg_keys

    if ! _pkg_list "$mode" > "$DUSKY_PKG_DATA.initial"; then
        printf 'Error: could not prepare the interactive package list.\n' >&2
        return 1
    fi

    choice=$(
        FZF_DEFAULT_OPTS= FZF_DEFAULT_OPTS_FILE=/dev/null \
        fzf \
            --with-shell='bash -c' \
            --ansi \
            --no-multi \
            --no-sort \
            --delimiter=$'\t' \
            --with-nth=2.. \
            --no-hscroll \
            --ellipsis='…' \
            --highlight-line \
            --prompt="$prompt" \
            --pointer='▌' \
            --layout=reverse \
            --border=rounded \
            --border-label=" Dusky Package Atlas · $target " \
            --border-label-pos=3 \
            --info=inline \
            --header="$header" \
            --header-first \
            --preview-label=' Details · PgUp/PgDn: scroll ' \
            --bind='ctrl-s:reload-sync(_pkg_list size_desc)+change-prompt( Largest › )' \
            --bind='alt-s:reload-sync(_pkg_list size_asc)+change-prompt( Smallest › )' \
            --bind='ctrl-d:reload-sync(_pkg_list date_desc)+change-prompt( Newest › )' \
            --bind='alt-d:reload-sync(_pkg_list date_asc)+change-prompt( Oldest › )' \
            --bind='ctrl-r:reload-sync(_pkg_list alpha)+change-prompt( Alphabetical › )' \
            --bind='ctrl-p:toggle-preview' \
            --bind='ctrl-l:refresh-preview' \
            --bind='pgup:preview-page-up' \
            --bind='pgdn:preview-page-down' \
            --bind='shift-up:preview-up' \
            --bind='shift-down:preview-down' \
            --bind='f1:execute(_pkg_keys)' \
            --bind="alt-c:transform(if _pkg_copy_details {1} 2>/dev/null; then printf '%s' 'change-border-label( Details copied · Esc keeps details · Enter copies name )'; else printf '%s' 'change-border-label( Copy failed · Check wl-copy, Wayland, or package details )'; fi)" \
            --bind="focus:change-border-label( Dusky Package Atlas · $target )" \
            --bind='esc:abort' \
            --bind='enter:accept' \
            --color="bg:$DUSKY_THEME_BG,bg+:$DUSKY_THEME_MUTED" \
            --color="fg:$DUSKY_THEME_FG,fg+:$DUSKY_THEME_FG" \
            --color="hl:$DUSKY_THEME_ACCENT,hl+:$DUSKY_THEME_ACCENT" \
            --color="header:$DUSKY_THEME_ACCENT,info:$DUSKY_THEME_FG" \
            --color="prompt:$DUSKY_THEME_ACCENT,pointer:$DUSKY_THEME_SUCCESS" \
            --color="marker:$DUSKY_THEME_SUCCESS,spinner:$DUSKY_THEME_WARNING" \
            --color="border:$DUSKY_THEME_MUTED,label:$DUSKY_THEME_ACCENT" \
            --color="gutter:$DUSKY_THEME_BG,separator:$DUSKY_THEME_MUTED" \
            --color="scrollbar:$DUSKY_THEME_MUTED" \
            --color="preview-bg:$DUSKY_THEME_BG,preview-fg:$DUSKY_THEME_FG" \
            --color="preview-border:$DUSKY_THEME_MUTED,preview-label:$DUSKY_THEME_ACCENT" \
            --color="preview-scrollbar:$DUSKY_THEME_MUTED" \
            --preview='_pkg_preview {1}' \
            --preview-window='right,55%,border-left,wrap' \
            < "$DUSKY_PKG_DATA.initial"
    )
    status=$?

    if (( status != 0 )); then
        case "$status" in
            1|130) ;;
            *)
                printf 'Error: fzf exited with status %s.\n' \
                    "$status" >&2
                ;;
        esac
        return "$status"
    fi

    if [[ -z "$choice" || "$choice" != *$'\t'* || "$choice" == *$'\n'* ]]; then
        printf 'Error: fzf returned an invalid selection.\n' >&2
        return 1
    fi

    pkg=${choice%%$'\t'*}

    if [[ -z "$pkg" ]]; then
        printf 'Error: fzf returned an empty package name.\n' >&2
        return 1
    fi

    printf '%s\n' "$pkg" || return 1

    if command -v wl-copy >/dev/null 2>&1; then
        if printf '%s' "$pkg" | wl-copy --type text/plain; then
            copied=1
        else
            printf 'Warning: selected %s, but clipboard copying failed.\n' \
                "$pkg" >&2
        fi
    fi

    if (( desktop )); then
        if (( copied )); then
            printf '\nSelected %s and copied its name to the clipboard.\n' \
                "$pkg" >&2
        else
            printf '\nSelected %s; its name was not copied to the clipboard.\n' \
                "$pkg" >&2
        fi
        sleep 1.5
    fi

    return 0
}

_pkg_cli() (
    set -o pipefail

    local mode="$1"
    local count="$2"
    local target="$3"
    local title
    local color=0

    case "$mode" in
        size_desc) title='Largest' ;;
        size_asc)  title='Smallest' ;;
        date_desc) title='Newest' ;;
        date_asc)  title='Oldest' ;;
        alpha)     title='Alphabetical' ;;
        *)
            printf 'Error: invalid CLI sort mode: %s\n' "$mode" >&2
            return 2
            ;;
    esac

    if [[ "$target" == explicit ]]; then
        title+=' explicitly installed packages'
    else
        title+=' installed packages'
    fi

    if [[ -t 1 && ${TERM:-dumb} != dumb && ! -v NO_COLOR ]]; then
        _pkg_theme || return 1
        color=1
    fi

    if (( color )); then
        printf '\n%s::\033[0m %s\033[1m%s\033[0m (Top %s)\n' \
            "$DUSKY_COLOR_ACCENT" "$DUSKY_COLOR_FG" \
            "$title" "$count" || return 1

        printf '%s%-10s %10s %s\033[0m\n' \
            "$DUSKY_COLOR_ACCENT" INSTALLED SIZE PACKAGE || return 1

        printf '%s%s\033[0m\n' "$DUSKY_COLOR_MUTED" \
            '------------------------------------------------------------' ||
            return 1
    else
        printf '\n:: %s (Top %s)\n' "$title" "$count" || return 1
        printf '%-10s %10s %s\n' INSTALLED SIZE PACKAGE || return 1
        printf '%s\n' \
            '------------------------------------------------------------' ||
            return 1
    fi

    if ! _pkg_sort "$mode" < "$DUSKY_PKG_DATA" |
        gawk -F '|' -v limit="$count" '
            BEGIN {
                limit += 0
            }

            NR <= limit {
                printf "%s|%s|%s\n", $3, $4, $1
            }
        ' |
        numfmt \
            --to=iec-i \
            --suffix=B \
            --field=2 \
            --delimiter='|' \
            --padding=10 |
        gawk -F '|' -v color="$color" '
            BEGIN {
                success = ENVIRON["DUSKY_COLOR_SUCCESS"]
                warning = ENVIRON["DUSKY_COLOR_WARNING"]
                accent = ENVIRON["DUSKY_COLOR_ACCENT"]
                reset = "\033[0m"
            }

            {
                date = strftime("%Y-%m-%d", $1)

                if (color) {
                    printf "%s%s%s ", success, date, reset
                    printf "%s%10s%s ", warning, $2, reset
                    printf "\033[1m%s%s%s\n", accent, $3, reset
                } else {
                    printf "%s %10s %s\n", date, $2, $3
                }
            }
        '
    then
        printf 'Error: could not produce the CLI package list.\n' >&2
        return 1
    fi

    printf '\n'
)

main() (
    # Confine environment changes, exported helpers, and traps.
    # Expected nonzero statuses are handled explicitly; no errexit.
    set -o pipefail
    export LC_ALL=C.UTF-8

    local target=all
    local metric=''
    local count=''
    local desktop=0
    local arg
    local tmpdir

    # Help takes precedence and does not require pacman, expac, or fzf.
    for arg in "$@"; do
        case "${arg,,}" in
            help|-h|--help)
                _pkg_help
                return
                ;;
        esac
    done

    for arg in "$@"; do
        case "${arg,,}" in
            --desktop)
                desktop=1
                ;;
            explicit|user)
                target=explicit
                ;;
            all)
                target=all
                ;;
            hogs|size|big|fat|massive|huge|giant)
                metric=size_desc
                ;;
            tiny|small|micro|mini|little)
                metric=size_asc
                ;;
            new|recent|latest)
                metric=date_desc
                ;;
            old|ancient)
                metric=date_asc
                ;;
            alpha|name)
                metric=alpha
                ;;
            *)
                if [[ "$arg" =~ ^[1-9][0-9]*$ ]]; then
                    # Keep as text to avoid Bash integer overflow.
                    count=$arg
                else
                    printf 'Error: unknown argument: %s\n' "$arg" >&2
                    printf 'Use --help for usage.\n' >&2
                    return 2
                fi
                ;;
        esac
    done

    _pkg_require pacman expac gawk sort mktemp rm || return 1

    if [[ -z "$count" ]]; then
        _pkg_require fzf bash || return 1

        # stdout may legitimately be piped. Check the controlling
        # terminal instead of requiring stdout to be a terminal.
        if ! { : <> /dev/tty; } 2>/dev/null; then
            printf 'Error: interactive mode requires a controlling terminal.\n' >&2
            printf 'For CLI output, supply a count: pkg 20 hogs\n' >&2
            printf 'For a desktop launcher, use Terminal=true.\n' >&2
            return 1
        fi

        metric=${metric:-date_desc}

        if (( desktop )); then
            _pkg_require sleep || return 1
        fi
    else
        _pkg_require numfmt || return 1
        metric=${metric:-size_desc}
    fi

    tmpdir=$(mktemp -d) || {
        printf 'Error: could not create a temporary directory.\n' >&2
        return 1
    }

    # Both the raw snapshot and initial fzf input live here.
    trap 'rm -rf -- "$tmpdir"' EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM

    export DUSKY_PKG_DATA="$tmpdir/packages"

    if ! _pkg_fetch "$target" > "$DUSKY_PKG_DATA"; then
        printf 'Error: could not query installed packages.\n' >&2
        return 1
    fi

    if [[ ! -s "$DUSKY_PKG_DATA" ]]; then
        printf 'No installed packages matched target: %s\n' "$target" >&2
        return 1
    fi

    if [[ -z "$count" ]]; then
        _pkg_interactive "$metric" "$target" "$desktop"
    else
        _pkg_cli "$metric" "$count" "$target"
    fi
)

main "$@"
