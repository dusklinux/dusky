#!/usr/bin/env bash
# Cached Pacman, AUR and Dusky counts for Waybar and the quick panel.

# FOR Horizontal WAYBARS

# "custom/updates": {
#     "exec": "tail -F ~/.config/dusky/settings/waybar_update_counter_h 2>/dev/null",
#     "return-type": "json",
#     "format": "{}",
#     "tooltip": true
# }

# FOR Vertical WAYBARS

# "custom/updates": {
#     "exec": "tail -F ~/.config/dusky/settings/waybar_update_counter_v 2>/dev/null",
#     "return-type": "json",
#     "format": "{}",
#     "tooltip": true
# }

set -euo pipefail

# ---------------------------------------------------------
# Configuration & Defaults
# ---------------------------------------------------------
SHOW_PACMAN=0
SHOW_AUR=0
SHOW_DUSKY=0
MODULE_ORDER=() 
TIMEOUT_SEC=15
STATE_DIR="$HOME/.config/dusky/settings"

# Parse Arguments
for arg in "$@"; do
    case "$arg" in
        --pacman) 
            [[ $SHOW_PACMAN -eq 0 ]] && MODULE_ORDER+=("pacman")
            SHOW_PACMAN=1 
            ;;
        --aur) 
            [[ $SHOW_AUR -eq 0 ]] && MODULE_ORDER+=("aur")
            SHOW_AUR=1 
            ;;
        --dusky) 
            [[ $SHOW_DUSKY -eq 0 ]] && MODULE_ORDER+=("dusky")
            SHOW_DUSKY=1 
            ;;
        -h|--help)
            printf "Usage: %s [--pacman] [--aur] [--dusky] (default: all)\n" "${0##*/}"
            exit 0
            ;;
    esac
done

# The updater invokes this without flags; check all categories by default.
if (( ${#MODULE_ORDER[@]} == 0 )); then
    SHOW_PACMAN=1 SHOW_AUR=1 SHOW_DUSKY=1
    MODULE_ORDER=(pacman aur dusky)
fi

mkdir -p -- "$STATE_DIR"
# Missing dependencies must not replace a useful cache with malformed JSON.
command -v jq >/dev/null 2>&1 || exit 0
exec {lock_fd}> "$STATE_DIR/.waybar_update_counter.lock"
flock --nonblocking "$lock_fd" || exit 0
TMP_DIR=$(mktemp -d "$STATE_DIR/.dusky_updates.XXXXXX")
cleanup() {
    local -a pids=()
    mapfile -t pids < <(jobs -pr)
    if (( ${#pids[@]} )); then
        kill -- "${pids[@]}" 2>/dev/null || true
        wait "${pids[@]}" 2>/dev/null || true
    fi
    rm -rf -- "$TMP_DIR"
}
trap cleanup EXIT
trap 'exit 0' HUP INT TERM

# Query the real services, without unrelated ICMP probes. A failed/partial
# query is unknown, never evidence of zero pending packages.
fetch_package_count() {
    local name=$1 rc=0
    shift
    if timeout --kill-after=3 "$TIMEOUT_SEC" "$@" > "$TMP_DIR/$name.output" 2> "$TMP_DIR/$name.error"; then
        wc -l < "$TMP_DIR/$name.output" > "$TMP_DIR/$name"
    else
        rc=$?
        if [[ $name == pac && $rc == 2 ]] ||
           [[ $name == aur && $rc == 1 && ! -s $TMP_DIR/$name.output && ! -s $TMP_DIR/$name.error ]]; then
            # checkupdates uses 2 for no updates; paru -Qua uses 1 with no
            # output. A reported error or partial output remains unknown.
            printf '0\n' > "$TMP_DIR/$name"
        else
            printf 'null\n' > "$TMP_DIR/$name"
        fi
    fi
}

if (( SHOW_PACMAN )); then
    fetch_package_count pac checkupdates --nocolor &
fi
if (( SHOW_AUR )); then
    fetch_package_count aur paru -Qua &
fi
wait

# Read the commit snapshot after package queries so resets/checks completed
# during network I/O are reflected in the cache we are about to publish.
if (( SHOW_DUSKY )); then
    val=''
    if [[ -f "$STATE_DIR/dusky_update_behind_commit" ]]; then
        IFS= read -r val < "$STATE_DIR/dusky_update_behind_commit" || true
    fi
    printf '%s\n' "$val" > "$TMP_DIR/dsk"
fi

# ---------------------------------------------------------
# Data Sanitization
# ---------------------------------------------------------
sanitize_count() {
    local file="$1"
    local -n ref_var="$2"
    ref_var="null"
    
    if [[ -s "$file" ]]; then
        local raw=""
        read -r raw < "$file" || true
        if [[ "$raw" =~ ^[0-9]{1,18}$ ]]; then
            # shellcheck disable=SC2034 # Writes through the caller's nameref.
            ref_var=$(( 10#$raw ))
        fi
    fi
}

declare PAC_COUNT AUR_COUNT DSK_COUNT
sanitize_count "$TMP_DIR/pac" PAC_COUNT
sanitize_count "$TMP_DIR/aur" AUR_COUNT
sanitize_count "$TMP_DIR/dsk" DSK_COUNT

# ---------------------------------------------------------
# Dual-Axis JSON Rendering
# ---------------------------------------------------------
render_axis() {
    local axis="$1"
    local suffix="$2"
    local file="$STATE_DIR/waybar_update_counter_${suffix}"

    jq -c -n \
        --arg mode "$axis" \
        --arg order "${MODULE_ORDER[*]:-}" \
        --argjson pac_c "$PAC_COUNT" \
        --argjson aur_c "$AUR_COUNT" \
        --argjson dsk_c "$DSK_COUNT" '

        def clamp: if . > 999 then 999 else . end;
        
        def pad3:
            tostring |
            length as $l |
            if $l >= 3 then .
            elif $l == 2 then "\u2005" + . + "\u2005"
            elif $l == 1 then " " + . + " "
            else "   " end;
        
        "󰣇" as $pac_icon | "󰏔" as $aur_icon | "D" as $dsk_icon | "󰸞" as $check_icon |

        ($order | split(" ")) as $selected |
        {pacman: $pac_c, aur: $aur_c, dusky: $dsk_c} |
        with_entries(.key as $key | select($selected | index($key))) as $counts |
        ([$counts[] | . // 0] | add) as $total |
        ([$counts | to_entries[] | select(.value == null) | .key] | join(", ")) as $unknown |

        if $total == 0 and $unknown != "" then
            {
                "text": "󰸞 ?",
                "tooltip": "Update counts unavailable: \($unknown).",
                "class": "unknown"
            }
        elif $total == 0 then
            {
                "text": (if $mode == "vertical" then ("0" | pad3) + "\n" + ($check_icon | pad3) else "\($check_icon) 0" end),
                "tooltip": "No pending updates in the checked categories.",
                "class": "updated"
            }
        else
            ($order | split(" ") | map(
                if . == "pacman" and $pac_c > 0 then 
                    { c: ($pac_c | clamp), i: $pac_icon, name: "Pacman", desc: "Official Arch Linux Packages" } 
                elif . == "aur" and $aur_c > 0 then 
                    { c: ($aur_c | clamp), i: $aur_icon, name: "AUR", desc: "Arch User Repository Packages" }
                elif . == "dusky" and $dsk_c > 0 then 
                    { c: ($dsk_c | clamp), i: $dsk_icon, name: "Dusky", desc: "Dusky Github Commits" }
                else empty end
            )) as $items |

            (if $mode == "vertical" then
                ($items | map("\( .c | pad3 )\n\( .i | pad3 )") | join("\n\n"))
            else
                ($items | map("\(.i) \(.c)") | join("  "))
            end) as $text |

            ($items | map("• \(.name): \(.c)\n  └ \(.desc)") | join("\n\n")) as $tooltip_details |

            {
                "text": $text,
                "tooltip": ("Pending System Updates (Total: \($total))\n────────────────────────────\n\($tooltip_details)" + if $unknown != "" then "\n\nCounts unavailable: \($unknown)." else "" end),
                "class": "pending"
            }
        end | . + {counts: $counts}
    ' > "$TMP_DIR/axis_${suffix}"

    mv --force --no-target-directory -- "$TMP_DIR/axis_${suffix}" "$file"
}

# Publish each axis with an atomic rename
render_axis "horizontal" "h"
render_axis "vertical" "v"
