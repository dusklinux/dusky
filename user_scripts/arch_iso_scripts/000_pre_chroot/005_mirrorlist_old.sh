#!/usr/bin/env bash
# ==============================================================================
#  005_mirrorlist.sh
#  Context: Arch ISO (Root)
#  Description: Optimizes mirrorlist using Reflector for the live environment.
#               Ensures fast downloads for pacstrap.
# ==============================================================================

# --- CONFIGURATION ---
TARGET_FILE="/etc/pacman.d/mirrorlist"
DEFAULT_COUNTRY="list"

# Preselected Indian Mirrors (Fallback)
FALLBACK_MIRRORS=(
    'Server = https://in.arch.niranjan.co/$repo/os/$arch'
    'Server = https://mirrors.saswata.cc/archlinux/$repo/os/$arch'
    'Server = https://in.mirrors.cicku.me/archlinux/$repo/os/$arch'
    'Server = https://archlinux.kushwanthreddy.com/$repo/os/$arch'
    'Server = https://mirror.del2.albony.in/archlinux/$repo/os/$arch'
    'Server = https://mirror.sahil.world/archlinux/$repo/os/$arch'
    'Server = https://mirror.maa.albony.in/archlinux/$repo/os/$arch'
    'Server = https://in-mirror.garudalinux.org/archlinux/$repo/os/$arch'
    'Server = https://mirrors.nxtgen.com/archlinux-mirror/$repo/os/$arch'
    'Server = https://mirrors.abhy.me/archlinux/$repo/os/$arch'
)

# --- UTILS ---
# Colors match your ISO Orchestra (but defined here for standalone safety)
if [[ -t 1 ]]; then
    G=$'\e[32m'; R=$'\e[31m'; Y=$'\e[33m'; B=$'\e[34m'; NC=$'\e[0m'
else
    G=""; R=""; Y=""; B=""; NC=""
fi

# --- PRE-FLIGHT CHECKS ---
# 1. Ensure script is run as root (Arch ISO default is root)
if [[ $EUID -ne 0 ]]; then
   echo -e "${R}!! This script must be run as root.${NC}" 
   exit 1
fi

# 2. Ensure Reflector is installed
# While Arch ISO usually has it, this ensures robustness if using a minimal/custom ISO.
if ! command -v reflector &> /dev/null; then
    echo -e "${Y}:: Reflector not found. Installing...${NC}"
    # Syncing DB first (-Sy) because live ISOs have empty package caches
    pacman -Sy --noconfirm --needed reflector
fi

# --- MAIN LOGIC ---
update_mirrors() {
    while true; do
        echo -e "\n${B}:: Mirrorlist Configuration (ISO Environment)${NC}"
        echo -e "   --------------------------------------------------------"
        echo -e "   ${Y}NOTE TO GLOBAL USERS:${NC}"
        echo -e "   Type ${B}'list'${NC} to view all available countries."
        echo -e "   Press ${B}[Enter]${NC} to use the default (${DEFAULT_COUNTRY})."
        echo -e "   --------------------------------------------------------"
        
        # No timeout, waits indefinitely for user input
        read -r -p ":: Enter country: " _input_country

        # 1. Check if user wants to list countries
        if [[ "${_input_country,,}" == "list" ]] || [[ -z "$_input_country" ]]; then
            echo -e "${Y}:: Retrieving country list...${NC}"
            reflector --list-countries
            echo ""
            continue
        fi

        # 2. Determine Country
        local country="${_input_country:-$DEFAULT_COUNTRY}"

        echo -e "${Y}:: Running Reflector for region: ${country}...${NC}"
        
        # 3. Run Reflector
        # --download-timeout 5 prevents hangs on bad mirrors
        if reflector --country "$country" --latest 10 --protocol https --sort rate --download-timeout 5 --save "$TARGET_FILE"; then
            echo -e "${G}:: Reflector success! Mirrors updated.${NC}"
            
            echo ":: Syncing package database..."
            pacman -Syy
            break
        else
            # 4. Reflector Failed - Error Handling Menu
            echo -e "\n${R}!! Reflector failed to update mirrors for '$country'.${NC}"
            echo "   1) Retry (Enter new country)"
            echo "   2) Use Preselected Indian Mirrors (Fallback)"
            echo "   3) Do nothing (Keep existing ISO mirrors)"
            
            read -r -p ":: Select an option [1-3]: " choice

            case "$choice" in
                1)
                    echo ":: Retrying..."
                    continue
                    ;;
                2)
                    echo -e "${Y}:: Applying fallback mirror list...${NC}"
                    printf "%s\n" "${FALLBACK_MIRRORS[@]}" > "$TARGET_FILE"
                    
                    echo -e "${G}:: Fallback mirrors applied.${NC}"
                    echo ":: Syncing package database..."
                    pacman -Syy
                    break
                    ;;
                3)
                    echo -e "${Y}:: Skipping mirror update. Keeping defaults.${NC}"
                    break
                    ;;
                *)
                    echo "!! Invalid selection."
                    ;;
            esac
        fi
    done
}

# Execute
update_mirrors
