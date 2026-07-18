#!/usr/bin/env bash
# DUSKY_INTERACTIVE=true
# Requires: bash 5.0+, iwd (iwctl), systemd, coreutils, iproute2
# Target: Arch Linux Live ISO environment

set -Eeuo pipefail

# Standardize environment for predictable parsing
export LC_ALL=C

# ANSI Colors for UI
readonly C_RESET='\e[0m'
readonly C_RED='\e[1;31m'
readonly C_GREEN='\e[1;32m'
readonly C_YELLOW='\e[1;33m'
readonly C_CYAN='\e[1;36m'

# ==============================================================================
# Helper Functions
# ==============================================================================

cleanup() {
    echo -e "\n${C_YELLOW}[*] Script interrupted. Exiting cleanly.${C_RESET}"
    exit 130
}
trap cleanup SIGINT SIGTERM

log_info()    { echo -e "${C_CYAN}[i] ${1}${C_RESET}"; }
log_success() { echo -e "${C_GREEN}[✓] ${1}${C_RESET}"; }
log_warn()    { echo -e "${C_YELLOW}[!] ${1}${C_RESET}"; }
log_error()   { echo -e "${C_RED}[X] ${1}${C_RESET}"; }

fail_and_exit() {
    log_error "Critical failure: No active internet connection established."
    log_warn "This installation script requires an active route to the internet in Online mode."
    exit 1
}

check_connectivity() {
    # Test L3 routing via Cloudflare/Google DNS
    if ping -c 2 -W 2 1.1.1.1 >/dev/null 2>&1 || \
       ping -c 2 -W 2 8.8.8.8 >/dev/null 2>&1; then
        
        # Test DNS resolution
        if ping -c 1 -W 2 archlinux.org >/dev/null 2>&1 || \
           ping -c 1 -W 2 google.com >/dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

check_eth_carrier() {
    local dev=$1
    if ip link show dev "$dev" 2>/dev/null | grep -q "LOWER_UP"; then
        return 0
    fi
    return 1
}

get_eth_dev() {
    # Get first ethernet device starting with e (e.g. eth0, enp3s0)
    ip link show | awk -F': ' '/^[0-9]+: e/{print $2; exit}'
}

get_wifi_dev() {
    # Find wireless interface using iwctl or /sys/class/net/
    local dev
    dev=$(iwctl device list 2>/dev/null | awk 'NR>4 {print $2}' | grep -v '^$' | head -n 1 || true)
    if [[ -z "$dev" ]]; then
        dev=$(ls /sys/class/net/ 2>/dev/null | grep -E '^w' | head -n 1 || true)
    fi
    echo "$dev"
}

# ==============================================================================
# Main Execution
# ==============================================================================

log_info "Initializing Live ISO Network Connect Wizard..."

log_info "Verifying current internet routing..."
if check_connectivity; then
    log_success "System is already connected to the internet."
    exit 0
fi

log_warn "No internet routing detected."

# Check for iwd service (Live ISO wireless daemon)
if ! systemctl is-active --quiet iwd; then
    log_info "Starting wireless daemon (iwd)..."
    systemctl start iwd 2>/dev/null || true
    sleep 1
fi

# ==============================================================================
# Interactive Menu (TTY Mode Only)
# ==============================================================================
if [[ ! -t 0 ]]; then
    log_warn "Non-interactive environment. Attempting LAN DHCP lease..."
    eth_dev=$(get_eth_dev)
    if [[ -n "$eth_dev" ]] && check_eth_carrier "$eth_dev"; then
        log_info "LAN cable connected to $eth_dev. Requesting lease..."
        dhcpcd "$eth_dev" >/dev/null 2>&1 || true
        sleep 5
        if check_connectivity; then
            log_success "LAN connected autonomous."
            exit 0
        fi
    fi
    log_error "Interactive connection requires a TTY."
    fail_and_exit
fi

PS3=$(echo -e "\n${C_CYAN}Select connection interface (1/2) or Ctrl+C to abort: ${C_RESET}")

select conn_method in "LAN (Wired)" "Wi-Fi"; do
    case $conn_method in
        "LAN (Wired)")
            eth_dev=$(get_eth_dev)

            if [[ -z "$eth_dev" ]]; then
                log_error "No physical Ethernet interface detected."
                continue
            fi

            log_info "Primary Ethernet device detected: $eth_dev"
            echo -e "${C_YELLOW}[+] Please ensure your Ethernet cable is physically plugged in.${C_RESET}"
            read -r -p "Press Enter to verify carrier state..."

            if ! check_eth_carrier "$eth_dev"; then
                log_error "No carrier detected on $eth_dev. The cable is unplugged."
                continue
            fi

            log_info "Carrier detected. Requesting DHCP lease via dhcpcd..."
            ip link set dev "$eth_dev" up || true
            if dhcpcd -k "$eth_dev" >/dev/null 2>&1 || true; dhcpcd "$eth_dev" >/dev/null 2>&1; then
                sleep 3
                if check_connectivity; then
                    log_success "LAN connected and internet routed."
                    exit 0
                else
                    log_error "LAN connected, but no internet access (Check DNS/Gateway)."
                    fail_and_exit
                fi
            else
                log_error "Failed to obtain DHCP lease on $eth_dev."
                fail_and_exit
            fi
            ;;

        "Wi-Fi")
            wifi_dev=$(get_wifi_dev)

            if [[ -z "$wifi_dev" ]]; then
                log_error "No Wi-Fi interface detected on this system."
                continue
            fi

            log_info "Wi-Fi interface detected: $wifi_dev"
            
            # Ensure interface is up
            ip link set dev "$wifi_dev" up || true
            
            log_info "Scanning for 802.11 networks using iwctl..."
            iwctl station "$wifi_dev" scan >/dev/null 2>&1 || true
            sleep 3

            # Parse SSIDs from iwctl station get-networks
            mapfile -t networks < <(iwctl station "$wifi_dev" get-networks 2>/dev/null | awk 'NR>4 {
                line = $0
                sub(/^[ \t]+/,"",line)
                if (line ~ / (psk|open|8021x) /) {
                    match(line, / (psk|open|8021x) /)
                    ssid = substr(line, 1, RSTART-1)
                    sub(/[ \t]+$/,"",ssid)
                    gsub(/\x1b\[[0-9;]*[a-zA-Z]/, "", ssid)
                    if (ssid != "") print ssid
                }
            }' | sort -u || true)

            if [[ ${#networks[@]} -eq 0 ]]; then
                log_warn "No networks found on initial scan. Retrying scan..."
                iwctl station "$wifi_dev" scan >/dev/null 2>&1 || true
                sleep 3
                mapfile -t networks < <(iwctl station "$wifi_dev" get-networks 2>/dev/null | awk 'NR>4 {
                    line = $0
                    sub(/^[ \t]+/,"",line)
                    if (line ~ / (psk|open|8021x) /) {
                        match(line, / (psk|open|8021x) /)
                        ssid = substr(line, 1, RSTART-1)
                        sub(/[ \t]+$/,"",ssid)
                        gsub(/\x1b\[[0-9;]*[a-zA-Z]/, "", ssid)
                        if (ssid != "") print ssid
                    }
                }' | sort -u || true)
                
                if [[ ${#networks[@]} -eq 0 ]]; then
                    log_error "No broadcasting 802.11 networks found in range."
                    fail_and_exit
                fi
            fi

            log_info "Discovered ${#networks[@]} networks."
            PS3=$(echo -e "\n${C_CYAN}Select target SSID: ${C_RESET}")

            select ssid in "${networks[@]}"; do
                if [[ -n "$ssid" ]]; then
                    echo ""
                    read -r -s -p "Enter WPA/WEP passphrase for '$ssid' (leave empty if open): " pass
                    echo -e "\n"
                    log_info "Connecting station to '$ssid'..."

                    if [[ -n "$pass" ]]; then
                        iwctl --passphrase "$pass" station "$wifi_dev" connect "$ssid" || { log_error "iwctl connect failed."; fail_and_exit; }
                    else
                        iwctl station "$wifi_dev" connect "$ssid" || { log_error "iwctl connect failed."; fail_and_exit; }
                    fi

                    log_info "Authenticating and waiting for IP lease..."
                    
                    # Restart dhcpcd on the interface to guarantee IP lease
                    dhcpcd -k "$wifi_dev" >/dev/null 2>&1 || true
                    dhcpcd "$wifi_dev" >/dev/null 2>&1 || true

                    connected=0
                    for ((c=1; c<=15; c++)); do
                        if check_connectivity; then
                            connected=1
                            break
                        fi
                        sleep 1
                    done

                    if [[ $connected -eq 1 ]]; then
                        log_success "Connected and internet routed."
                        exit 0
                    else
                        log_error "Connected to SSID but failed to route packets (No internet)."
                        fail_and_exit
                    fi
                else
                    log_warn "Invalid selection."
                fi
            done
            ;;

        *)
            log_warn "Invalid selection. Please choose 1 or 2."
            ;;
    esac
done
