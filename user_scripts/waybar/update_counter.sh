#!/usr/bin/env bash
# Execution constraints for ultimate reliability and safety
set -euo pipefail

# ---------------------------------------------------------
# Fail-Fast Dependency Check
# ---------------------------------------------------------
# Guarantees the status bar receives valid JSON even if jq is missing
if ! command -v jq >/dev/null 2>&1; then
    printf '{"text":"err","tooltip":"jq dependency missing","class":"critical"}\n'
    exit 1
fi

# ---------------------------------------------------------
# Configuration & Defaults
# ---------------------------------------------------------
MODE="horizontal"
SHOW_PACMAN=0
SHOW_AUR=0
SHOW_DUSKY=0
MODULE_ORDER=() # Tracks the exact CLI argument sequence
TIMEOUT_SEC=15  # Hard kill any network fetch after 15 seconds

# Parse Arguments (Preserves user-defined order)
for arg in "$@"; do
    case "$arg" in
        --vertical) MODE="vertical" ;;
        --horizontal) MODE="horizontal" ;;
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
            printf "Usage: %s [--horizontal|--vertical] [--pacman] [--aur] [--dusky]\n" "${0##*/}"
            exit 0
            ;;
    esac
done

# ---------------------------------------------------------
# Secure Ephemeral Storage & Bulletproof Trap Handling
# ---------------------------------------------------------
TMP_DIR=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/dusky_updates.XXXXXX")

# The Trap: 
# 1. Disable 'set -e' (+e) to guarantee cleanup completion even if 'kill' fails.
# 2. Gracefully terminate child processes via job PIDs.
# 3. Wait for reaping to prevent zombies.
# 4. Strictly verify TMP_DIR is not empty before removal.
# 5. Explicitly exit on termination signals to prevent silent state resumption.
trap 'set +e; pids=$(jobs -p); [[ -n "$pids" ]] && kill $pids 2>/dev/null; wait 2>/dev/null; [[ -n "${TMP_DIR:-}" ]] && rm -rf "$TMP_DIR"' EXIT

# Map standard signals to explicit exits to reliably trigger the EXIT trap
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# ---------------------------------------------------------
# Concurrent, Sandboxed Data Fetching
# ---------------------------------------------------------

if (( SHOW_PACMAN )); then
    (
        if command -v checkupdates >/dev/null 2>&1; then
            # -k 3 guarantees a SIGKILL if the process ignores SIGTERM
            timeout -k 3 "$TIMEOUT_SEC" checkupdates 2>/dev/null | wc -l > "$TMP_DIR/pac" || echo "0" > "$TMP_DIR/pac"
        else
            echo "0" > "$TMP_DIR/pac"
        fi
    ) &
fi

if (( SHOW_AUR )); then
    (
        if command -v paru >/dev/null 2>&1; then
            timeout -k 3 "$TIMEOUT_SEC" paru -Qua 2>/dev/null | wc -l > "$TMP_DIR/aur" || echo "0" > "$TMP_DIR/aur"
        else
            echo "0" > "$TMP_DIR/aur"
        fi
    ) &
fi

if (( SHOW_DUSKY )); then
    (
        DSK_FILE="$HOME/.config/dusky/settings/dusky_update_behind_commit"
        # Ensure it is readable and explicitly a regular file (not a blocking FIFO/pipe)
        if [[ -r "$DSK_FILE" && -f "$DSK_FILE" && ! -p "$DSK_FILE" ]]; then
            # Standard read (<) safely supports read-only and symlinked config files
            if read -t 1 -r val < "$DSK_FILE" 2>/dev/null || true; then
                # Only write out if the value isn't purely empty
                [[ -n "${val:-}" ]] && echo "$val" > "$TMP_DIR/dsk" || echo "0" > "$TMP_DIR/dsk"
            else
                echo "0" > "$TMP_DIR/dsk"
            fi
        else
            echo "0" > "$TMP_DIR/dsk"
        fi
    ) &
fi

# Synchronize background tasks
wait

# ---------------------------------------------------------
# Data Sanitization (Zero-Fork Nameref Optimization)
# ---------------------------------------------------------
# Utilizes Bash 5+ namerefs (local -n) to pass variables by reference.
sanitize_count() {
    local file="$1"
    local -n ref_var="$2"
    ref_var="0" # Default fallback
    
    if [[ -s "$file" ]]; then
        local raw=""
        read -r raw < "$file" || true
        
        # Strict validation: Only accept pure digit payloads
        if [[ "$raw" =~ ^[0-9]+$ ]]; then
            # Base-10 coercion (10#) strips leading zeros in pure Bash
            ref_var=$(( 10#$raw ))
        fi
    fi
}

# Execute parsing directly into variables (Zero subshells spawned)
declare PAC_COUNT AUR_COUNT DSK_COUNT
sanitize_count "$TMP_DIR/pac" PAC_COUNT
sanitize_count "$TMP_DIR/aur" AUR_COUNT
sanitize_count "$TMP_DIR/dsk" DSK_COUNT

# ---------------------------------------------------------
# Unified JSON Processing via jq
# ---------------------------------------------------------
jq -c -n \
    --arg mode "$MODE" \
    --arg order "${MODULE_ORDER[*]:-}" \
    --argjson pac_c "$PAC_COUNT" \
    --argjson aur_c "$AUR_COUNT" \
    --argjson dsk_c "$DSK_COUNT" '

    # Define strict 3-char clamping function
    def clamp: if . > 999 then 999 else . end;
    
    # Advanced Typographic Alignment Function
    # U+2005 (Four-per-em space) is exactly 0.5 character widths.
    # U+0020 (Standard space) is exactly 1.0 character widths.
    # This mathematically guarantees sub-character center alignment in GTK.
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
        # Iterate strictly over the CLI argument order
        ($order | split(" ") | map(
            if . == "pacman" and $pac_c > 0 then 
                { c: ($pac_c | clamp), i: $pac_icon, name: "Pacman", desc: "Official Arch Linux Packages" } 
            elif . == "aur" and $aur_c > 0 then 
                { c: ($aur_c | clamp), i: $aur_icon, name: "AUR", desc: "Arch User Repository Packages" }
            elif . == "dusky" and $dsk_c > 0 then 
                { c: ($dsk_c | clamp), i: $dsk_icon, name: "Dusky", desc: "Custom Environment Commits" }
            else empty end
        )) as $items |

        # Handle structural rendering based on requested axis
        # Note: pad3 is ONLY applied vertically to preserve horizontal flow.
        (if $mode == "vertical" then
            ($items | map("\( .c | pad3 )\n\( .i | pad3 )") | join("\n\n"))
        else
            ($items | map("\(.i) \(.c)") | join("  "))
        end) as $text |

        # Build intuitive, structural tooltip
        ($items | map("• \(.name): \(.c)\n  └ \(.desc)") | join("\n\n")) as $tooltip_details |

        {
            "text": $text,
            "tooltip": "Pending System Updates (Total: \($total))\n────────────────────────────\n\($tooltip_details)",
            "class": "pending"
        }
    end
'
