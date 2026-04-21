#!/usr/bin/env bash
# Execution constraints for ultimate reliability and safety

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
            printf "Usage: %s [--pacman] [--aur] [--dusky]\n" "${0##*/}"
            exit 0
            ;;
    esac
done

mkdir -p "$STATE_DIR"

# ---------------------------------------------------------
# Fail-Fast Dependency & Network Verification
# ---------------------------------------------------------
if ! command -v jq >/dev/null 2>&1; then
    err_json='{"text":"err","tooltip":"jq dependency missing","class":"critical"}\n'
    printf '%s' "$err_json" > "$STATE_DIR/waybar_update_counter_h"
    printf '%s' "$err_json" > "$STATE_DIR/waybar_update_counter_v"
    exit 1
fi

# Hard timeout prevents DNS resolution blackholes from bypassing the ping reply timeout
if ! timeout 3 ping -q -c 1 -W 2 archlinux.org >/dev/null 2>&1; then
    off_json='{"text":"󰸞 ?","tooltip":"Offline. Last state unknown.","class":"updated"}\n'
    printf '%s' "$off_json" > "$STATE_DIR/waybar_update_counter_h"
    printf '%s' "$off_json" > "$STATE_DIR/waybar_update_counter_v"
    exit 0
fi

# ---------------------------------------------------------
# Secure Ephemeral Storage & Bulletproof Trap Handling
# ---------------------------------------------------------
TMP_DIR=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/dusky_updates.XXXXXX")

trap 'set +e; pids=$(jobs -p); [[ -n "$pids" ]] && kill $pids 2>/dev/null; wait 2>/dev/null; [[ -n "${TMP_DIR:-}" ]] && rm -rf "$TMP_DIR"; rm -f "$STATE_DIR"/waybar_update_counter_*.tmp 2>/dev/null' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# ---------------------------------------------------------
# Concurrent, Sandboxed Data Fetching
# ---------------------------------------------------------
if (( SHOW_PACMAN )); then
    (
        if command -v checkupdates >/dev/null 2>&1; then
            # Capture gracefully to prevent pipefail from causing redundant file writes
            count=$(timeout -k 3 "$TIMEOUT_SEC" checkupdates 2>/dev/null | wc -l || true)
            echo "${count:-0}" > "$TMP_DIR/pac"
        else
            echo "0" > "$TMP_DIR/pac"
        fi
    ) &
fi

if (( SHOW_AUR )); then
    (
        if command -v paru >/dev/null 2>&1; then
            # Unified optimization: Capture gracefully to prevent duplicate writes
            count=$(timeout -k 3 "$TIMEOUT_SEC" paru -Qua 2>/dev/null | wc -l || true)
            echo "${count:-0}" > "$TMP_DIR/aur"
        else
            echo "0" > "$TMP_DIR/aur"
        fi
    ) &
fi

if (( SHOW_DUSKY )); then
    (
        DSK_FILE="$STATE_DIR/dusky_update_behind_commit"
        if [[ -r "$DSK_FILE" && -f "$DSK_FILE" && ! -p "$DSK_FILE" ]]; then
            val=""
            # Execute read and ignore EOF errors to safely handle files missing a trailing newline
            read -t 1 -r val < "$DSK_FILE" 2>/dev/null || true
            if [[ -n "${val:-}" ]]; then
                echo "$val" > "$TMP_DIR/dsk"
            else
                echo "0" > "$TMP_DIR/dsk"
            fi
        else
            echo "0" > "$TMP_DIR/dsk"
        fi
    ) &
fi

wait

# ---------------------------------------------------------
# Data Sanitization
# ---------------------------------------------------------
sanitize_count() {
    local file="$1"
    local -n ref_var="$2"
    ref_var="0"
    
    if [[ -s "$file" ]]; then
        local raw=""
        read -r raw < "$file" || true
        if [[ "$raw" =~ ^[0-9]+$ ]]; then
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
        
        "󰣇" as $pac_icon | "󰏔" as $aur_icon | "" as $dsk_icon | "󰸞" as $check_icon |

        ($pac_c + $aur_c + $dsk_c) as $total |

        if $total == 0 then
            {
                "text": (if $mode == "vertical" then ("0" | pad3) + "\n" + ($check_icon | pad3) else "\($check_icon) 0" end),
                "tooltip": "System is completely up to date.",
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
                "tooltip": "Pending System Updates (Total: \($total))\n────────────────────────────\n\($tooltip_details)",
                "class": "pending"
            }
        end
    ' > "${file}.tmp"

    mv "${file}.tmp" "$file"
}

# Generate both axes simultaneously
render_axis "horizontal" "h"
render_axis "vertical" "v"
