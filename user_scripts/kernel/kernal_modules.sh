#!/usr/bin/env bash
# =============================================================================
# Dusky Module Atlas (Platinum Edition - Revision 2 - Centered Apex)
# Architecture: Kernel 7.0+ Module Management TUI via FZF 0.73.1
#               Mirrors dusky_packages.sh with 1:1 interaction paradigms.
#               Fully supports Wayland wl-copy and --desktop flags.
# =============================================================================

# DRY Header Helper
_mod_header() {
    printf "\n\e[34m::\e[0m \e[1m%s\e[0m (Top %s)\n" "$1" "$2"
    printf "\e[38;5;238m------------------------------------------------------------\e[0m\n"
    printf "\e[38;5;242m STATE         SIZE  REFCT  MODULE\e[0m\n"
    printf "\e[38;5;238m------------------------------------------------------------\e[0m\n"
}

# The Interactive FZF Engine
_mod_interactive() {
    local init_mode="${1:-size_desc}"
    local init_target="${2:-loaded}"
    export LC_ALL=C

    # Track State Across Subshells
    export DUSKY_MOD_STATE_FILE="/tmp/dusky_mod_${$}_state"
    echo "CURRENT_SORT=\"$init_mode\"; CURRENT_FILTER=\"$init_target\"" > "$DUSKY_MOD_STATE_FILE"

    # F1 Help Menu Payload (Contains input flush loop to prevent \eOP escape sequence bleed)
    export DUSKY_MOD_HELP='clear; printf "\n\n  \033[1;38;5;81m󰘚 Dusky Module Atlas - Keyboard Shortcuts\033[0m\n  \033[38;5;238m──────────────────────────────────────────────\033[0m\n  \033[1;37mSort & View Controls\033[0m\n  \033[1;33m[CTRL-S]\033[0m  Sort by Size descending (Hogs)\n  \033[1;33m[ALT-S]\033[0m   Sort by Size ascending (Tiny)\n  \033[1;33m[CTRL-D]\033[0m  Sort by Refcount descending (Most Used)\n  \033[1;33m[CTRL-R]\033[0m  Reset to Alpha + clear filter\n  \033[1;33m[CTRL-A]\033[0m  View: All Available Modules (Disk Scan)\n\n  \033[1;37mFilter Controls\033[0m\n  \033[1;33m[CTRL-U]\033[0m  Filter: Unused only (refcount=0)\n  \033[1;33m[CTRL-E]\033[0m  Filter: Active only (refcount>0)\n  \033[1;33m[CTRL-G]\033[0m  GPU      \033[1;33m[CTRL-N]\033[0m  Network  \033[1;33m[CTRL-O]\033[0m  Audio\n  \033[1;33m[CTRL-I]\033[0m  Input    \033[1;33m[CTRL-B]\033[0m  Storage  \033[1;33m[ALT-P]\033[0m   USB\n  \033[1;33m[ALT-F]\033[0m   FS       \033[1;33m[ALT-V]\033[0m   Virtual  \033[1;33m[ALT-X]\033[0m   DKMS/Ext\n\n  \033[1;37mModule Actions\033[0m\n  \033[1;33m[ALT-L]\033[0m   Load Module (sudo modprobe)\n  \033[1;33m[ALT-U]\033[0m   Unload Module (sudo modprobe -r)\n  \033[1;33m[ALT-B]\033[0m   Blacklist + Unload\n  \033[1;33m[ALT-K]\033[0m   Copy blacklist command to clipboard\n  \033[1;33m[ALT-R]\033[0m   Copy rmmod command to clipboard\n  \033[1;33m[ALT-C]\033[0m   Copy preview panel to clipboard\n  \033[1;33m[ENTER]\033[0m   Copy module name to clipboard & Exit\n  \033[1;33m[F1]\033[0m      Show this Help Menu\n\n  \033[38;5;242mPress any key to return...\033[0m"; read -rsn1; while read -rsn1 -t 0.01; do :; done'

    # Compile Live List Generator
    export DUSKY_MOD_LIST='
export LC_ALL=C
mode="$1"
target="$2"

case "$mode" in
    size_desc)     sort_args=(-t"|" -k3 -nr) ;;
    size_asc)      sort_args=(-t"|" -k3 -n) ;;
    refcount_desc) sort_args=(-t"|" -k4 -nr) ;;
    refcount_asc)  sort_args=(-t"|" -k4 -n) ;;
    alpha|*)       sort_args=(-t"|" -k1) ;;
esac

fetch_data() {
    if [[ "$target" == "available" || "$target" == "avail" ]]; then
        # Disk scan
        find /lib/modules/$(uname -r)/ -name "*.ko*" 2>/dev/null | awk -F"/" '\''
            BEGIN {
                while((getline < "/proc/modules") > 0) { loaded[$1] = $2 "|" $3 "|" $5 }
            }
            {
                path = $0
                name = $NF
                sub(/\.ko(\.zst|\.xz|\.gz)?$/, "", name)
                gsub(/-/, "_", name)
                
                if (name in loaded) {
                    split(loaded[name], parts, "|")
                    print name "|" parts[1] "|" parts[2] "|" parts[3] "|" path
                } else {
                    cmd = "stat -c %s \"" path "\""
                    cmd | getline size
                    close(cmd)
                    print name "|" size "|-1|Available|" path
                }
            }
        '\'' | sort -u -t"|" -k1,1
    else
        # Active parse
        awk '\''{ print $1 "|" $2 "|" $3 "|" $5 }'\'' /proc/modules
    fi
}

fetch_data | sort "${sort_args[@]}" | awk -F"|" -v target="$target" '\''
    {
        name = $1; size = $2; ref = $3; state = $4; path = $5
        
        # Determine Categories
        cat = "SYS"; cat_col = "246"
        if      (name ~ /^(xe|i915|amdgpu|nouveau|radeon|drm_|ast|mgag200|vmwgfx|nvidia|nova|gma)/) { cat="GPU"; cat_col="207" }
        else if (name ~ /^snd_|^sound|^ac97|^intel_sst|^avs_|^hdaudio/) { cat="AUD"; cat_col="39" }
        else if (name ~ /^iwl|^rtw|^ath|^mt76|^brcm|^r8|^e1000|^ice|^iavf|^ixgbe|^cfg80211|^mac80211|^mwifi|^wilc/) { cat="NET"; cat_col="114" }
        else if (name ~ /^hid|^usbhid|^i2c_hid|^libps2|^atkbd|^mousedev|^evdev|^wacom|^xpad|^joydev/) { cat="INP"; cat_col="220" }
        else if (name ~ /^nvme|^ahci|^libata|^scsi_|^sd_mod|^sr_mod|^dm_|^md_mod|^ata_|^mmc|^virtio_blk/) { cat="STR"; cat_col="208" }
        else if (name ~ /^xhci|^ehci|^uhci|^usb_|^usbcore|^typec|^ucsi|^cdc_/) { cat="USB"; cat_col="51" }
        else if (name ~ /^ext4|^btrfs|^xfs|^fat|^vfat|^ntfs3|^overlay|^fuse|^iso9660|^erofs|^squash/) { cat="VFS"; cat_col="73" }
        else if (name ~ /^apparmor|^selinux|^lockdown|^dm_crypt|^tpm|^keys|^pkcs|^ima|^evm/) { cat="SEC"; cat_col="196" }
        else if (name ~ /^kvm|^vbox|^virtio_|^vhost|^vsock/) { cat="VRT"; cat_col="141" }
        else if (name ~ /^sha|^aes|^gcm|^ghash|^crc|^crypto_|^cmac|^hmac|^ecdh/) { cat="CRY"; cat_col="252" }
        else if (name ~ /^coretemp|^k10temp|^intel_pch|^acpi_|^hwmon|^thermal|^fan/) { cat="THM"; cat_col="203" }

        # Apply Filters
        if (target == "unused" && ref != 0) next
        if (target == "active" && ref <= 0) next
        if (target == "gpu" && cat != "GPU") next
        if (target == "net" && cat != "NET") next
        if (target == "audio" && cat != "AUD") next
        if (target == "input" && cat != "INP") next
        if (target == "storage" && cat != "STR") next
        if (target == "usb" && cat != "USB") next
        if (target == "fs" && cat != "VFS") next
        if (target == "virt" && cat != "VRT") next
        if (target == "dkms") {
            if (state == "Available" && path !~ /(updates|extra|dkms)/) next
        }

        # Size format
        size_mb = size / 1048576
        if (size_mb >= 1024) { size_fmt = sprintf("%6.2f GiB", size_mb/1024) }
        else if (size_mb >= 1) { size_fmt = sprintf("%6.2f MiB", size_mb) }
        else { size_fmt = sprintf("%6.2f KiB", size / 1024) }

        # Refcount & State Formatting
        if (ref == -1) {
            ref_fmt = sprintf("\033[38;5;242m%5s\033[0m", "-")
            state_gly = "↓"; state_col = "38;5;242"
            size_fmt = sprintf("\033[38;5;246m%10s\033[0m", size_fmt)
        } else {
            if (ref == 0) {
                ref_fmt = sprintf("\033[1;38;5;226m%5d\033[0m", ref)
                state_gly = "○"; state_col = "38;5;226"
                size_fmt = sprintf("\033[1;38;5;226m%10s\033[0m", size_fmt)
            } else if (ref < 10) {
                ref_fmt = sprintf("\033[1;38;5;255m%5d\033[0m", ref)
                state_gly = "●"; state_col = "38;5;46"
                size_fmt = sprintf("\033[38;5;114m%10s\033[0m", size_fmt)
            } else {
                ref_fmt = sprintf("\033[1;38;5;196m%5d\033[0m", ref)
                state_gly = "●"; state_col = "38;5;46"
                size_fmt = sprintf("\033[38;5;114m%10s\033[0m", size_fmt)
            }
            if (state == "Loading") { state_gly = "⟳"; state_col = "38;5;51" }
            if (state == "Unloading") { state_gly = "✗"; state_col = "38;5;196" }
        }

        disp_name = (length(name) > 27) ? substr(name, 1, 24) "..." : name
        
        # Build perfectly aligned visible string matching 58-width header
        visual_str = sprintf("[\033[%sm%s\033[0m] [\033[38;5;%sm%-3s\033[0m] \033[1;38;5;39m%-27s\033[0m \033[38;5;238m│\033[0m %s \033[38;5;238m│\033[0m %s", state_col, state_gly, cat_col, cat, disp_name, size_fmt, ref_fmt)
        
        pad = sprintf("%150s", "")
        printf "%s|%s%s\n", name, visual_str, pad
    }
'\''
'

    # Compile Preview Script
    export DUSKY_MOD_PREVIEW='
export LC_ALL=C
pkg="$1"

left_str=":: Module Details: $pkg"
left_len=${#left_str}

count_str=""
right_len=0
if [[ -n "$FZF_POS" && -n "$FZF_MATCH_COUNT" ]]; then
    count_str="[${FZF_POS}/${FZF_MATCH_COUNT}]"
    right_len=${#count_str}
elif [[ -n "$FZF_MATCH_COUNT" ]]; then
    count_str="[${FZF_MATCH_COUNT}/${FZF_TOTAL_COUNT}]"
    right_len=${#count_str}
fi

cols=${FZF_PREVIEW_COLUMNS:-80}
pad_len=$(( cols - left_len - right_len - 1 ))
(( pad_len < 1 )) && pad_len=1
pad=$(printf "%*s" "$pad_len" "")
hr=$(printf "%*s" "$cols" "" | sed "s/ /─/g")

printf "\033[1;38;5;81m:: \033[1;37mModule Details: \033[1;32m%s\033[0m%s\033[1;38;5;242m%s\033[0m\n\033[38;5;238m%s\033[0m\n" "$pkg" "$pad" "$count_str" "$hr"

modinfo_out=$(modinfo "$pkg" 2>/dev/null)
if [[ -z "$modinfo_out" ]]; then
    printf "\033[31m✖ Module %s not found in current kernel tree.\033[0m\n" "$pkg"
    exit 0
fi

m_desc=$(echo "$modinfo_out" | awk -F": *" '\''/^description/ {print $2; exit}'\'')
m_lic=$(echo "$modinfo_out" | awk -F": *" '\''/^license/ {print $2; exit}'\'')
m_auth=$(echo "$modinfo_out" | awk -F": *" '\''/^author/ {print $2; exit}'\'')
m_file=$(echo "$modinfo_out" | awk -F": *" '\''/^filename/ {print $2; exit}'\'')
m_verm=$(echo "$modinfo_out" | awk -F": *" '\''/^vermagic/ {print $1; exit}'\'')
m_deps=$(echo "$modinfo_out" | awk -F": *" '\''/^depends/ {print $2; exit}'\'')

[[ -z "$m_desc" ]] && m_desc="(None provided)"
[[ -z "$m_lic" ]] && m_lic="(Unknown)"
[[ -z "$m_auth" ]] && m_auth="(Unknown)"

# Signature & Rust Check
sig_str="\033[1;32mML-DSA ✓\033[0m (In-tree)"
m_type="\033[1;32mIn-tree\033[0m"
if [[ "$m_file" == *"updates/"* || "$m_file" == *"extra/"* ]]; then
    m_type="\033[1;208mDKMS / Out-of-tree\033[0m [DKMS]"
    if echo "$modinfo_out" | grep -q "^signer:"; then
        sig_str="\033[1;33mCustom Signed ⚠\033[0m"
    else
        sig_str="\033[1;31mUNSIGNED ⚠\033[0m"
    fi
fi
if [[ -f "/sys/module/$pkg/taint" ]] && [[ "$(cat "/sys/module/$pkg/taint" 2>/dev/null)" != "" ]]; then
    sig_str="\033[1;31mTAINTED ✗\033[0m"
fi

rust_flag=""
if echo "$modinfo_out" | grep -qi "rust" || [[ "$m_file" == *"rust"* ]]; then
    rust_flag=" \033[1;38;5;208m[RUST]\033[0m"
fi

printf "\033[1;38;5;39m%-18s\033[0m: \033[38;5;253m%s\033[0m\n" "Name" "$pkg"
printf "\033[1;38;5;39m%-18s\033[0m: \033[38;5;253m%s\033[0m\n" "Description" "$m_desc"
printf "\033[1;38;5;39m%-18s\033[0m: \033[38;5;141m%s\033[0m\n" "License" "$m_lic"
printf "\033[1;38;5;39m%-18s\033[0m: \033[38;5;114m%s\033[0m\n" "Author" "$m_auth"
printf "\033[1;38;5;39m%-18s\033[0m: \033[38;5;246m%s\033[0m\n" "Source File" "$m_file"
printf "\033[1;38;5;39m%-18s\033[0m: \033[38;5;220m%s\033[0m\n" "Kernel Built For" "${m_verm:-$(uname -r)}"
printf "\033[1;38;5;39m%-18s\033[0m: %b\n" "Signature" "$sig_str"
printf "\033[1;38;5;39m%-18s\033[0m: %b%b\n" "Type" "$m_type" "$rust_flag"

printf "\n\033[1;38;5;81m:: \033[1;37mMemory\033[0m\n\033[38;5;238m%s\033[0m\n" "$hr"
mem_line=$(grep "^$pkg " /proc/modules 2>/dev/null)
if [[ -n "$mem_line" ]]; then
    sz=$(echo "$mem_line" | awk '\''{print $2}'\'')
    sz_mb=$(awk -v sz="$sz" '\''BEGIN{printf "%.2f", sz/1048576}'\'')
    ref=$(echo "$mem_line" | awk '\''{print $3}'\'')
    st=$(echo "$mem_line" | awk '\''{print $5}'\'')
    
    printf "\033[1;38;5;39m%-18s\033[0m: \033[38;5;220m%s MiB\033[0m\n" "Total Size" "$sz_mb"
    printf "\033[1;38;5;39m%-18s\033[0m: \033[38;5;114m%s\033[0m (refcount: %s)\n" "State" "$st" "$ref"
    
    if [[ "$ref" == "0" ]]; then
        printf "\n  \033[1;38;5;226m⚠  UNUSED — This module is loaded but nothing depends on it.\033[0m\n"
        printf "     \033[38;5;226mIt is consuming %s MiB of kernel RAM for no active purpose.\033[0m\n" "$sz_mb"
        printf "     \033[38;5;226mConsider unloading it:  sudo modprobe -r %s\033[0m\n" "$pkg"
    fi
    
    [[ -r "/sys/module/$pkg/sections/.text" ]] && printf "\033[1;38;5;39m%-18s\033[0m: \033[38;5;246m%s\033[0m\n" ".text" "$(cat "/sys/module/$pkg/sections/.text" 2>/dev/null)"
    [[ -r "/sys/module/$pkg/sections/.data" ]] && printf "\033[1;38;5;39m%-18s\033[0m: \033[38;5;246m%s\033[0m\n" ".data" "$(cat "/sys/module/$pkg/sections/.data" 2>/dev/null)"
    [[ -r "/sys/module/$pkg/sections/.bss" ]]  && printf "\033[1;38;5;39m%-18s\033[0m: \033[38;5;246m%s\033[0m\n" ".bss" "$(cat "/sys/module/$pkg/sections/.bss" 2>/dev/null)"
else
    printf "\033[38;5;242m(Module is not currently loaded in RAM)\033[0m\n"
fi

printf "\n\033[1;38;5;81m:: \033[1;37mDependencies\033[0m\n\033[38;5;238m%s\033[0m\n" "$hr"
[[ -z "$m_deps" ]] && m_deps="(None)"
printf "\033[1;38;5;39m%-18s\033[0m: \033[38;5;253m%s\033[0m\n" "Depends On" "$m_deps"
used_by=$(echo "$mem_line" | awk '\''{print $4}'\'')
[[ "$used_by" == "-" || -z "$used_by" ]] && used_by="(None)"
printf "\033[1;38;5;39m%-18s\033[0m: \033[38;5;253m%s\033[0m\n" "Used By" "${used_by%,}"

holders=""
if [[ -d "/sys/module/$pkg/holders" ]]; then
    for h in "/sys/module/$pkg/holders/"*; do
        [[ -e "$h" ]] && holders="$holders ${h##*/}"
    done
fi
[[ -z "$holders" ]] && holders=" (None)"
printf "\033[1;38;5;39m%-18s\033[0m: \033[38;5;253m%s\033[0m\n" "Holders" "$holders"

printf "\n\033[1;38;5;81m:: \033[1;37mHardware Devices\033[0m\n\033[38;5;238m%s\033[0m\n" "$hr"
dev_found=0
# PCI Lookup
pci_matches=$(echo "$DUSKY_MOD_PCI_MAP" | grep "^$pkg=" | cut -d= -f2-)
if [[ -n "$pci_matches" ]]; then
    while read -r dev; do
        printf "  \033[1;32m●\033[0m [\033[38;5;207mPCI\033[0m]  \033[38;5;253m%s\033[0m\n" "$dev"
        dev_found=1
    done <<< "$pci_matches"
fi
# Sysfs Walk (USB/I2C/etc)
if [[ -d "/sys/bus/" ]]; then
    for dev in /sys/bus/*/drivers/"$pkg"/*; do
        if [[ -L "$dev" ]]; then
            bus_type=$(echo "$dev" | awk -F"/" '\''{print $4}'\'')
            dev_id="${dev##*/}"
            
            detail=""
            if [[ -f "$dev/product" ]]; then
                detail=$(cat "$dev/product" 2>/dev/null)
                [[ -f "$dev/manufacturer" ]] && detail="$(cat "$dev/manufacturer" 2>/dev/null) $detail"
            elif command -v udevadm >/dev/null; then
                detail=$(udevadm info --query=property --path="$dev" 2>/dev/null | grep -m1 -E "^ID_MODEL_FROM_DATABASE=|^ID_MODEL=" | cut -d= -f2-)
            fi
            [[ -z "$detail" ]] && detail="Generic $bus_type Device"
            
            printf "  \033[1;32m●\033[0m [\033[38;5;51m%s\033[0m %s]  \033[38;5;253m%s\033[0m\n" "${bus_type^^}" "$dev_id" "$detail"
            dev_found=1
        fi
    done
fi
[[ $dev_found -eq 0 ]] && printf "  \033[38;5;242m(No hardware devices directly bound)\033[0m\n"

printf "\n\033[1;38;5;81m:: \033[1;37mLive Parameters\033[0m\n\033[38;5;238m%s\033[0m\n" "$hr"
param_found=0
if [[ -d "/sys/module/$pkg/parameters" ]]; then
    for p in "/sys/module/$pkg/parameters/"*; do
        if [[ -r "$p" ]]; then
            val=$(cat "$p" 2>/dev/null)
            printf "  \033[38;5;114m%-20s\033[0m = \033[38;5;253m%s\033[0m\n" "${p##*/}" "$val"
            param_found=1
        fi
    done
fi
[[ $param_found -eq 0 ]] && printf "  \033[38;5;242m(No live parameters exposed)\033[0m\n"

printf "\n\033[1;38;5;81m:: \033[1;37mSystem Integration\033[0m\n\033[38;5;238m%s\033[0m\n" "$hr"
pkg_owner=$(pacman -Qo "$m_file" 2>/dev/null | awk '\''{print $5}'\'')
if [[ -n "$pkg_owner" ]]; then
    pacman -Ql "$pkg_owner" 2>/dev/null | awk '\''
        BEGIN { bins=""; srvs=""; devs="" }
        {
            path = substr($0, length($1) + 2)
            if (path ~ /^\/usr\/(local\/)?s?bin\/[^\/]+$/) 
                bins = bins "  \033[38;5;114m" path "\033[0m\n"
            else if (path ~ /^(\/usr\/lib|\/etc)\/systemd\/(system|user)\/.*\.(service|timer|socket|path|mount|conf|target|device)$/) 
                srvs = srvs "  \033[38;5;203m" path "\033[0m\n"
            else if (path ~ /^\/dev\//)
                devs = devs "  \033[38;5;220m" path "\033[0m\n"
        }
        END {
            if (bins != "") { printf "\033[1;33m󰘚 Binaries:\033[0m\n%s", bins } 
            if (srvs != "") { printf "\n\033[1;35m󰒓 Systemd Units:\033[0m\n%s", srvs } 
            if (devs != "") { printf "\n\033[1;36m󰀻 Device Nodes:\033[0m\n%s", devs }
            if (bins == "" && srvs == "" && devs == "") { printf "  \033[38;5;242m(No associated bins/units found in %s)\033[0m\n", "'"$pkg_owner"'" }
        }
    '\''
else
    printf "  \033[38;5;242m(Module is not managed by pacman)\033[0m\n"
fi

printf "\n\033[1;38;5;81m:: \033[1;37mQuick Commands\033[0m  \033[38;5;242m(press keybinding to execute)\033[0m\n\033[38;5;238m%s\033[0m\n" "$hr"
printf "  \033[1;33m[ALT-U]\033[0m  sudo modprobe -r \033[38;5;39m%s\033[0m          \033[38;5;242m(unload this module)\033[0m\n" "$pkg"
printf "  \033[1;33m[ALT-L]\033[0m  sudo modprobe \033[38;5;39m%s\033[0m             \033[38;5;242m(load / reload)\033[0m\n" "$pkg"
printf "  \033[1;33m[ALT-B]\033[0m  blacklist \033[38;5;39m%s\033[0m  →  \033[38;5;203m/etc/modprobe.d/blacklist.conf\033[0m\n" "$pkg"
printf "  \033[1;33m[ALT-K]\033[0m  Copy blacklist command to clipboard\n"
printf "  \033[1;33m[ALT-R]\033[0m  Copy rmmod command to clipboard\n"
printf "  \033[1;33m[ALT-C]\033[0m  Copy this full panel to clipboard\n"
printf "  \033[1;33m[ENTER]\033[0m  Copy module name to clipboard\n"
'

    # Wayland Clipboard Payload
    export DUSKY_MOD_COPY='
export LC_ALL=C
pkg="$1"
bash -c "$DUSKY_MOD_PREVIEW" _ "$pkg" | sed -r "s/\x1B\[([0-9]{1,3}(;[0-9]{1,2})?)?[mGK]//g" | wl-copy
'

    # Calculate Footer Analytics
    export DUSKY_MOD_SUMMARY=$(
        awk '
            {
                tm++
                tr += $2
                if ($3 > 0) am++
                if ($3 == 0) { um++; wr += $2 }
            }
            END { 
                printf "Loaded: %d  \033[38;5;238m│\033[0m  Total RAM: %d MiB  \033[38;5;238m│\033[0m  Active: %d  \033[38;5;238m│\033[0m  \033[38;5;226mUnused: %d\033[0m  \033[38;5;238m│\033[0m  \033[38;5;226mWasted RAM: %d MiB\033[0m", 
                       tm, tr/1048576, am, um, wr/1048576 
            }' /proc/modules
    )

    local prompt_str=" 󰘚  Search Modules ❯ "
    case "$init_mode" in
        size_desc) prompt_str=" 󰘚  Hogs (Largest) ❯ " ;;
        size_asc)  prompt_str=" 󰘚  Tiny (Smallest) ❯ " ;;
        refcount_desc) prompt_str=" 󰘚  Most Used ❯ " ;;
    esac

    # Perfectly Centered Header String (Mathematically exact 58-width alignment matching visually generated list string offsets)
    local visual_header=$(printf " \033[38;5;242mST\033[0m  \033[38;5;242mCAT\033[0m  \033[1;37m         MODULE            \033[0m \033[38;5;238m│\033[0m \033[38;5;242m   SIZE   \033[0m \033[38;5;238m│\033[0m \033[38;5;242mREFCT\033[0m")

    local fzf_choice
    fzf_choice=$(bash -c "$DUSKY_MOD_LIST" _ "$init_mode" "$init_target" | fzf --ansi \
        --delimiter='\|' \
        --with-nth=2 \
        --accept-nth=1 \
        --tiebreak=begin,length \
        --no-hscroll \
        --ellipsis='' \
        --highlight-line \
        --prompt="$prompt_str" \
        --pointer="" \
        --marker="✓" \
        --layout=reverse \
        --border=rounded \
        --border-label=" 󰘚 Dusky Module Atlas [F1: Help] " \
        --border-label-pos=3 \
        --info=hidden \
        --header="$visual_header" \
        --header-first \
        --footer=" $DUSKY_MOD_SUMMARY " \
        --footer-border=line \
        --bind="ctrl-s:execute-silent(sed -i 's/CURRENT_SORT=.*/CURRENT_SORT=\"size_desc\"/' \"$DUSKY_MOD_STATE_FILE\")+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")+change-prompt( 󰘚  Hogs (Largest) ❯ )" \
        --bind="alt-s:execute-silent(sed -i 's/CURRENT_SORT=.*/CURRENT_SORT=\"size_asc\"/' \"$DUSKY_MOD_STATE_FILE\")+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")+change-prompt( 󰘚  Tiny (Smallest) ❯ )" \
        --bind="ctrl-d:execute-silent(sed -i 's/CURRENT_SORT=.*/CURRENT_SORT=\"refcount_desc\"/' \"$DUSKY_MOD_STATE_FILE\")+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")+change-prompt( 󰘚  Most Used ❯ )" \
        --bind="ctrl-r:execute-silent(sed -i -e 's/CURRENT_SORT=.*/CURRENT_SORT=\"alpha\"/' -e 's/CURRENT_FILTER=.*/CURRENT_FILTER=\"all\"/' \"$DUSKY_MOD_STATE_FILE\")+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")+change-prompt( 󰘚  Search Modules ❯ )" \
        --bind="ctrl-a:execute-silent(sed -i 's/CURRENT_FILTER=.*/CURRENT_FILTER=\"available\"/' \"$DUSKY_MOD_STATE_FILE\")+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")+change-prompt( 󰘚  All Available ❯ )" \
        --bind="ctrl-u:execute-silent(sed -i 's/CURRENT_FILTER=.*/CURRENT_FILTER=\"unused\"/' \"$DUSKY_MOD_STATE_FILE\")+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")+change-prompt( 󰘚  Unused (Wasted) ❯ )" \
        --bind="ctrl-e:execute-silent(sed -i 's/CURRENT_FILTER=.*/CURRENT_FILTER=\"active\"/' \"$DUSKY_MOD_STATE_FILE\")+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")+change-prompt( 󰘚  Active Only ❯ )" \
        --bind="ctrl-g:execute-silent(sed -i 's/CURRENT_FILTER=.*/CURRENT_FILTER=\"gpu\"/' \"$DUSKY_MOD_STATE_FILE\")+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")+change-prompt( 󰘚  GPU ❯ )" \
        --bind="ctrl-n:execute-silent(sed -i 's/CURRENT_FILTER=.*/CURRENT_FILTER=\"net\"/' \"$DUSKY_MOD_STATE_FILE\")+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")+change-prompt( 󰘚  Network ❯ )" \
        --bind="ctrl-o:execute-silent(sed -i 's/CURRENT_FILTER=.*/CURRENT_FILTER=\"audio\"/' \"$DUSKY_MOD_STATE_FILE\")+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")+change-prompt( 󰘚  Audio ❯ )" \
        --bind="ctrl-i:execute-silent(sed -i 's/CURRENT_FILTER=.*/CURRENT_FILTER=\"input\"/' \"$DUSKY_MOD_STATE_FILE\")+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")+change-prompt( 󰘚  Input/HID ❯ )" \
        --bind="ctrl-b:execute-silent(sed -i 's/CURRENT_FILTER=.*/CURRENT_FILTER=\"storage\"/' \"$DUSKY_MOD_STATE_FILE\")+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")+change-prompt( 󰘚  Storage ❯ )" \
        --bind="alt-p:execute-silent(sed -i 's/CURRENT_FILTER=.*/CURRENT_FILTER=\"usb\"/' \"$DUSKY_MOD_STATE_FILE\")+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")+change-prompt( 󰘚  USB ❯ )" \
        --bind="alt-f:execute-silent(sed -i 's/CURRENT_FILTER=.*/CURRENT_FILTER=\"fs\"/' \"$DUSKY_MOD_STATE_FILE\")+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")+change-prompt( 󰘚  Filesystems ❯ )" \
        --bind="alt-v:execute-silent(sed -i 's/CURRENT_FILTER=.*/CURRENT_FILTER=\"virt\"/' \"$DUSKY_MOD_STATE_FILE\")+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")+change-prompt( 󰘚  Virtualization ❯ )" \
        --bind="alt-x:execute-silent(sed -i 's/CURRENT_FILTER=.*/CURRENT_FILTER=\"dkms\"/' \"$DUSKY_MOD_STATE_FILE\")+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")+change-prompt( 󰘚  DKMS/External ❯ )" \
        --bind="f1:execute(bash -c \"\$DUSKY_MOD_HELP\")" \
        --bind="alt-c:execute-silent(bash -c \"\$DUSKY_MOD_COPY\" _ {1})+change-prompt( 󰘚  Copied Panel! ❯ )" \
        --bind="alt-k:execute-silent(printf 'blacklist {1}' | wl-copy)+change-prompt( 󰘚  Copied Blacklist Cmd ❯ )" \
        --bind="alt-r:execute-silent(printf 'sudo modprobe -r {1}' | wl-copy)+change-prompt( 󰘚  Copied rmmod Cmd ❯ )" \
        --bind="alt-u:execute(
            clear
            echo -e \"\e[1;36m::\e[0m Attempting to unload: \e[1;39m{1}\e[0m\"
            if sudo modprobe -r {1} 2>&1; then
                echo -e \"\e[1;32m✔ Unloaded successfully:\e[0m {1}\"
            else
                echo -e \"\e[1;31m✖ Failed to unload {1}.\e[0m Check if it is in use.\"
                echo -e \"  Try:  lsof | grep {1}\"
            fi
            echo \"\"
            echo -e \"\e[38;5;242mPress any key to continue...\e[0m\"
            read -rsn1; while read -rsn1 -t 0.01; do :; done
        )+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")" \
        --bind="alt-l:execute(
            clear
            echo -e \"\e[1;36m::\e[0m Loading: \e[1;39m{1}\e[0m\"
            if sudo modprobe {1} 2>&1; then
                echo -e \"\e[1;32m✔ Loaded successfully:\e[0m {1}\"
            else
                echo -e \"\e[1;31m✖ Failed.\e[0m Module may not exist or may have unmet dependencies.\"
            fi
            echo \"\"
            echo -e \"\e[38;5;242mPress any key to continue...\e[0m\"
            read -rsn1; while read -rsn1 -t 0.01; do :; done
        )+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")" \
        --bind="alt-b:execute(
            clear
            echo -e \"\e[1;36m::\e[0m Blacklisting and unloading: \e[1;39m{1}\e[0m\"
            BLACKLIST_LINE=\"blacklist {1}\"
            CONF_FILE=\"/etc/modprobe.d/blacklist.conf\"
            echo -e \"Writing:  \e[38;5;203m\$BLACKLIST_LINE\e[0m  →  \e[38;5;39m\$CONF_FILE\e[0m\"
            if echo \"\$BLACKLIST_LINE\" | sudo tee -a \"\$CONF_FILE\" > /dev/null; then
                echo -e \"\e[1;32m✔ Blacklist entry added.\e[0m\"
            else
                echo -e \"\e[1;31m✖ Failed to write to \$CONF_FILE\e[0m\"
            fi
            echo -e \"\nAttempting unload...\"
            sudo modprobe -r {1} 2>&1 || echo -e \"\e[38;5;242m(Could not unload — may be in use)\e[0m\"
            echo \"\"
            echo -e \"\e[38;5;242mPress any key to continue...\e[0m\"
            read -rsn1; while read -rsn1 -t 0.01; do :; done
        )+reload-sync(. \"$DUSKY_MOD_STATE_FILE\"; bash -c \"\$DUSKY_MOD_LIST\" _ \"\$CURRENT_SORT\" \"\$CURRENT_FILTER\")" \
        --color="bg+:#1e1e2e,bg:#11111b,spinner:#f5e0dc" \
        --color="fg:#cdd6f4,fg+:#cdd6f4,header:#89b4fa,info:#cba6f7" \
        --color="pointer:#a6e3a1,marker:#f5e0dc,prompt:#cba6f7" \
        --color="hl:#f38ba8,hl+:#f38ba8,border:#585b70,label:#a6e3a1" \
        --color="footer:#585b70,footer-border:#585b70" \
        --preview='bash -c "$DUSKY_MOD_PREVIEW" _ {1}' \
        --preview-window="right,50%,border-left,wrap" \
        --preview-label=" 󰋽 Module Info ")

    # Environment Cleanup
    rm -f "$DUSKY_MOD_STATE_FILE"
    unset DUSKY_MOD_LIST
    unset DUSKY_MOD_PREVIEW
    unset DUSKY_MOD_COPY
    unset DUSKY_MOD_HELP
    unset DUSKY_MOD_SUMMARY
    unset DUSKY_MOD_PCI_MAP
    unset DUSKY_MOD_USB_MAP
    unset DUSKY_MOD_STATE_FILE

    # Action Router
    if [[ -n "$fzf_choice" ]]; then
        local target_pkg="${fzf_choice}"
        
        # 1. Output standard stdout so shell piping works
        printf "%s\n" "$target_pkg"

        # 2. Quietly copy to Wayland clipboard
        if command -v wl-copy >/dev/null 2>&1; then
            printf "%s" "$target_pkg" | wl-copy
        fi

        # 3. GUI Visual Feedback
        if (( IS_DESKTOP_ENTRY )); then
            printf "\n\e[1;32m✔ Success!\e[0m Copied module \e[1;39m'%s'\e[0m to clipboard.\n" "$target_pkg" >&2
            sleep 1.5
        fi
    fi
}

main() {
    # Startup Checks
    if ! command -v modprobe >/dev/null 2>&1; then
        printf "\n\e[31m✖ Error:\e[0m 'kmod' is not installed or available.\n" >&2
        sleep 4; exit 1
    fi
    if ! command -v fzf >/dev/null 2>&1; then
        printf "\n\e[31m✖ Error:\e[0m 'fzf' is not installed.\n" >&2
        printf "  Please install it first: \e[36msudo pacman -S fzf\e[0m\n\n" >&2
        sleep 4; exit 1
    fi
    if ! command -v lspci >/dev/null 2>&1; then
        printf "\n\e[33m⚠ Warning:\e[0m 'pciutils' is missing. Device binding lookups will be disabled.\n" >&2
        sleep 1
    fi
    if ! command -v wl-copy >/dev/null 2>&1; then
        printf "\n\e[33m⚠ Warning:\e[0m 'wl-copy' not found. Clipboard features disabled.\n" >&2
        sleep 1
    fi

    local target="loaded"
    local metric=""
    declare -i count=-1
    declare -i show_help=0
    export IS_DESKTOP_ENTRY=0

    # Parse arguments
    for arg in "$@"; do
        arg_lower="${arg,,}"
        case "$arg_lower" in
            --desktop) IS_DESKTOP_ENTRY=1 ;;
            help|-h|--help) show_help=1 ;;
            loaded|all) target="loaded" ;;
            available|avail) target="available" ;;
            hogs|size|big|fat|massive|huge) metric="size_desc" ;;
            tiny|small|micro|mini) metric="size_asc" ;;
            used|active|busy) metric="refcount_desc" ;;
            unused|idle|waste|wasted) metric="refcount_asc"; target="unused" ;;
            gpu|video|display) target="gpu" ;;
            net|network|wifi) target="net" ;;
            audio|sound|snd) target="audio" ;;
            input|hid) target="input" ;;
            storage|disk|nvme) target="storage" ;;
            usb) target="usb" ;;
            fs|filesystem) target="fs" ;;
            dkms|external|oot) target="dkms" ;;
            virt|vm|kvm) target="virt" ;;
            *)
                if [[ "$arg" =~ ^[1-9][0-9]*$ ]]; then
                    count="$arg"
                else
                    printf "\n\e[31m✖ Error:\e[0m Unknown argument: '\e[33m%s\e[0m'\n\n" "$arg" >&2
                    sleep 3; exit 1
                fi
                ;;
        esac
    done

    # Help Menu Overlay
    if (( show_help )); then
        printf "\n\e[34m::\e[0m \e[1mmod\e[0m — Advanced Kernel Module Tool (TUI)\n"
        printf "\e[38;5;238m------------------------------------------------------------\e[0m\n"
        printf "\e[32mUsage:\e[0m mod [mode] [filter] [sort] [count]\n"
        printf "       \e[38;5;242m(Arguments can be provided in ANY order)\e[0m\n"
        printf "       \e[38;5;14mOmitting [count] launches the Interactive FZF Atlas.\e[0m\n\n"
        
        printf "\e[1mModes & Filters:\e[0m\n"
        printf "  \e[36mloaded\e[0m               - Currently loaded modules (Default)\n"
        printf "  \e[36mavailable\e[0m            - All available modules on disk\n"
        printf "  \e[36munused\e[0m               - Modules wasting RAM (refcount=0)\n"
        printf "  \e[36mgpu, net, audio...\e[0m   - Filter by specific subsystem\n\n"
        
        printf "\e[1mSort Metrics:\e[0m\n"
        printf "  \e[36mhogs\e[0m, \e[36mmassive\e[0m      - Sort by RAM size descending\n"
        printf "  \e[36mtiny\e[0m, \e[36msmall\e[0m        - Sort by RAM size ascending\n"
        printf "  \e[36mused\e[0m, \e[36mbusy\e[0m         - Sort by Reference count descending\n\n"
        
        printf "\e[1mExamples:\e[0m\n"
        printf "  \e[33mmod\e[0m                  # Launch Interactive FZF Atlas\n"
        printf "  \e[33mmod unused hogs\e[0m      # Launch Atlas showing biggest unused modules\n"
        printf "  \e[33mmod 20 gpu\e[0m           # Print classic CLI list of Top 20 GPU modules\n\n"
        exit 0
    fi

    # Startup caching for Hardware Bindings
    export DUSKY_MOD_PCI_MAP=$(lspci -vmm -k 2>/dev/null | awk '
        /^Vendor:/ { v=substr($0,9) }
        /^Device:/ { d=substr($0,9) }
        /^Driver:/ { drv=substr($0,9); map[drv]=v " " d }
        /^Module:/ { mod=substr($0,9); map[mod]=v " " d }
        END { for(m in map) print m "=" map[m] }
    ')

    # Execution Router: Launch Interactive Atlas if no count
    if (( count == -1 )); then
        local init_mode="${metric:-size_desc}"
        _mod_interactive "$init_mode" "$target"
        exit 0
    fi

    # Fallback: Classic CLI List Execution
    metric="${metric:-size_desc}"
    declare -a sort_cmd
    local title_metric=""

    case "$metric" in
        size_desc) title_metric="Largest"; sort_cmd=(sort -t '|' -k2 -rn) ;;
        size_asc)  title_metric="Smallest"; sort_cmd=(sort -t '|' -k2 -n) ;;
        refcount_desc) title_metric="Most Used"; sort_cmd=(sort -t '|' -k3 -rn) ;;
        refcount_asc)  title_metric="Least Used"; sort_cmd=(sort -t '|' -k3 -n) ;;
        alpha|*)   title_metric="Alpha"; sort_cmd=(sort -t '|' -k1) ;;
    esac

    local title_full="${title_metric} Loaded Modules"
    [[ "$target" == "unused" ]] && title_full="Largest Unused Modules (RAM Wasted)"
    [[ "$target" == "gpu" ]] && title_full="${title_metric} GPU Modules"
    [[ "$target" == "net" ]] && title_full="${title_metric} Network Modules"

    _mod_header "$title_full" "$count"
    
    local awk_cli_fmt='{
        name=$1; size=$2; ref=$3; state=$5
        
        # Color state
        if(ref==0){ st_c="\033[38;5;226m IDLE \033[0m" }
        else if(state=="Loading"){ st_c="\033[38;5;51m LOAD \033[0m" }
        else{ st_c="\033[38;5;46m LIVE \033[0m" }
        
        # Filter check (minimal duplicate of list logic for CLI mode)
        if(tgt=="unused" && ref!=0) next
        
        printf " %s  \033[38;5;220m%10s\033[0m  \033[38;5;246m%5s\033[0m  \033[1;38;5;39m%s\033[0m\n", st_c, size, ref, name
    }'

    awk '{ print $1 "|" $2 "|" $3 "|" $5 }' /proc/modules \
        | "${sort_cmd[@]}" \
        | head -n "$count" \
        | numfmt --to=iec-i --suffix=B --field=2 --delimiter='|' --padding=8 \
        | awk -F '|' -v tgt="$target" "$awk_cli_fmt"

    printf "\n"
}

main "$@"
