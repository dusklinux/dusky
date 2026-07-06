#!/usr/bin/env bash
# Requires: bash 5.0+, NetworkManager (nmcli), systemd, coreutils, iproute2
# Target: Arch Linux / Hyprland Ecosystem

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
    log_warn "This orchestration script requires an active route to the internet."
    log_warn "Please resolve your network issues and rerun the pipeline."
    exit 1
}

wait_for_nm() {
    if ! systemctl is-active --quiet NetworkManager; then
        log_warn "NetworkManager service is not active. Attempting to start it..."
        sudo systemctl start NetworkManager 2>/dev/null || true
        sleep 2
        if ! systemctl is-active --quiet NetworkManager; then
            log_error "NetworkManager service is not running and could not be started."
            exit 1
        fi
    fi

    # Wait up to 10 seconds for DBus and NM to fully initialize interfaces
    local attempt=1
    while ! nmcli -g RUNNING general 2>/dev/null | grep -q "running"; do
        if (( attempt > 10 )); then
            log_error "NetworkManager failed to reach 'running' state."
            exit 1
        fi
        sleep 1
        ((attempt++))
    done
}

flush_dns_caches() {
    log_info "Flushing local DNS caches to clear negative/stale records..."
    
    # Per dnsmasq(8) manual: SIGHUP clears the cache and re-loads hosts
    if systemctl is-active --quiet dnsmasq; then
        sudo systemctl kill -s HUP dnsmasq 2>/dev/null || sudo pkill -HUP dnsmasq 2>/dev/null || true
        log_info " -> Flushed dnsmasq cache."
    fi
    
    if systemctl is-active --quiet systemd-resolved; then
        sudo resolvectl flush-caches 2>/dev/null || sudo systemd-resolve --flush-caches 2>/dev/null || true
        log_info " -> Flushed systemd-resolved cache."
    fi
    
    # Allow DBus and network stack 2 seconds to settle routes post-flush
    sleep 2
}

check_connectivity() {
    # 1. Native NM Cached State Check
    local nm_state
    nm_state=$(nmcli -t networking connectivity 2>/dev/null || echo "unknown")
    if [[ "$nm_state" == "full" ]]; then
        return 0
    elif [[ "$nm_state" == "portal" ]]; then
        return 1 # Explicitly behind a captive portal, fail immediately
    fi

    # 2. Kernel-level Routing Check via HTTP (Strict Portal Avoidance)
    if command -v curl >/dev/null 2>&1; then
        # Test A: Arch Linux official check (Defeats redirects, supports both old and new check strings)
        if curl -s --connect-timeout 5 --max-time 5 http://ping.archlinux.org/ 2>/dev/null | grep -E -q "NetworkManager is online|captive portal detection"; then
            return 0
        fi
        
        # Test B: Global 204 No Content check (A captive portal will return 200/302, not 204)
        local http_code
        http_code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 --max-time 5 http://cpcheck.gstatic.com/generate_204 2>/dev/null || echo "000")
        if [[ "$http_code" == "204" ]]; then
            return 0
        fi
    fi

    # 3. Kernel-level Routing Check via ICMP (Fallback if HTTP is blocked)
    # Ping Cloudflare/Google directly to verify Layer 3 ICMP routing
    if ping -c 2 -W 2 1.1.1.1 >/dev/null 2>&1 || \
       ping -c 2 -W 2 8.8.8.8 >/dev/null 2>&1; then
        
        # Ensure DNS is not hijacked by verifying a non-existent domain fails to resolve.
        # (Some captive portals allow ICMP/ping but hijack DNS queries to resolve everything)
        if ! getent ahosts nonexistent-dns-test-12345.org >/dev/null 2>&1; then
            if ping -c 1 -W 2 archlinux.org >/dev/null 2>&1 || \
               ping -c 1 -W 2 google.com >/dev/null 2>&1; then
                return 0
            fi
        fi
    fi

    # 4. Active NM check (Last resort, forces DBus block and active probing)
    if [[ "$(timeout 5 nmcli -w 4 networking connectivity check 2>/dev/null || echo "unknown")" == "full" ]]; then
        return 0
    fi

    return 1
}

check_eth_carrier() {
    local dev=$1
    # LOWER_UP validates physical electrical carrier presence on the interface
    if ip link show dev "$dev" 2>/dev/null | grep -q "LOWER_UP"; then
        return 0
    fi
    return 1
}

get_eth_dev()  { nmcli -g DEVICE,TYPE dev | awk -F: '$2=="ethernet"{print $1; exit}'; }
get_wifi_dev() { nmcli -g DEVICE,TYPE dev | awk -F: '$2=="wifi"{print $1; exit}'; }

ensure_wifi_radio() {
    if ! nmcli -g WIFI radio | grep -q "enabled"; then
        log_warn "Wi-Fi radio is disabled. Attempting to power on..."
        
        # Soft-unblock in case the kernel is holding it down
        if command -v rfkill >/dev/null 2>&1; then
            sudo rfkill unblock wifi wlan >/dev/null 2>&1 || true
        fi
        
        sudo nmcli radio wifi on
        sleep 3 # Allow hardware PHY to initialize

        if ! nmcli -g WIFI radio | grep -q "enabled"; then
            log_error "Failed to enable Wi-Fi radio. A physical hardware switch may be toggled."
            fail_and_exit
        fi
    fi
}

# ==============================================================================
# Main Execution
# ==============================================================================

log_info "Initializing Network Orchestrator Phase 0..."
wait_for_nm

log_info "Verifying current routing table and internet access..."
if check_connectivity; then
    log_success "System is already connected to the internet."
    exit 0
fi

log_warn "No internet routing detected."

# ==============================================================================
# Autonomous (Headless) Fallback
# ==============================================================================
if [[ ! -t 0 ]]; then
    log_warn "Non-interactive environment detected (No TTY). Switching to autonomous mode."
    
    # Ensure Wi-Fi radio is powered on so any saved auto-connect profiles can initialize
    wifi_dev_auto=$(get_wifi_dev)
    if [[ -n "$wifi_dev_auto" ]]; then
        log_info "Wi-Fi interface detected ($wifi_dev_auto) in autonomous mode. Ensuring radio is enabled..."
        if command -v rfkill >/dev/null 2>&1; then
            sudo rfkill unblock wifi wlan >/dev/null 2>&1 || true
        fi
        sudo nmcli radio wifi on >/dev/null 2>&1 || true
        sleep 2
    fi

    log_info "Waiting up to 10s for potential NM auto-connect profiles to trigger..."
    for _ in {1..10}; do
        if check_connectivity; then
            log_success "System auto-connected autonomously. Pipeline ready."
            exit 0
        fi
        sleep 1
    done
    
    log_info "No auto-connect profiles triggered. Attempting Ethernet fallback..."
    eth_dev=$(get_eth_dev)
    
    if [[ -n "$eth_dev" ]]; then
        log_info "Primary Ethernet device detected: $eth_dev"
        # Force NM management in case it was flagged as unmanaged (e.g. by systemd-networkd)
        sudo nmcli device set "$eth_dev" managed yes >/dev/null 2>&1 || true

        if check_eth_carrier "$eth_dev"; then
            log_info "Carrier signal detected. Requesting DHCP lease..."
            timeout 15 sudo nmcli dev up "$eth_dev" >/dev/null 2>&1 || true
            
            flush_dns_caches
            
            if check_connectivity; then
                log_success "Autonomous LAN connected and internet routed."
                exit 0
            else
                log_error "Autonomous LAN connected, but routing failed (No Internet)."
            fi
        else
            log_error "No carrier detected on $eth_dev. Cable is unplugged."
        fi
    else
        log_error "No Ethernet interfaces available for autonomous connection."
    fi
    
    log_error "Autonomous connection failed. Interactive Wi-Fi setup requires a TTY."
    fail_and_exit
fi

# ==============================================================================
# Interactive Menu (TTY Mode Only)
# ==============================================================================
PS3=$(echo -e "\n${C_CYAN}Select connection interface (1/2) or Ctrl+C to abort: ${C_RESET}")

select conn_method in "LAN (Wired)" "Wi-Fi"; do
    case $conn_method in
        "LAN (Wired)")
            eth_dev=$(get_eth_dev)

            if [[ -z "$eth_dev" ]]; then
                log_error "No physical Ethernet interface detected on this system."
                fail_and_exit
            fi

            log_info "Primary Ethernet device detected: $eth_dev"
            # Force NM management in case it was flagged as unmanaged
            sudo nmcli device set "$eth_dev" managed yes >/dev/null 2>&1 || true

            echo -e "${C_YELLOW}[+] Please ensure your Ethernet cable is physically plugged in.${C_RESET}"
            read -r -p "Press Enter to verify carrier state..."

            if ! check_eth_carrier "$eth_dev"; then
                log_error "No carrier detected on $eth_dev. The cable is unplugged or the switch port is dead."
                fail_and_exit
            fi

            log_info "Carrier detected. Requesting DHCP lease..."
            if timeout 15 sudo nmcli dev up "$eth_dev" >/dev/null 2>&1; then
                
                flush_dns_caches
                
                if check_connectivity; then
                    log_success "LAN connected and internet routed."
                    exit 0
                else
                    log_error "LAN connected, but no internet access (Check DNS/Gateway)."
                    fail_and_exit
                fi
            else
                log_error "Failed to bring up $eth_dev. DHCP timeout or Layer 2 failure."
                fail_and_exit
            fi
            ;;

        "Wi-Fi")
            wifi_dev=$(get_wifi_dev)

            if [[ -z "$wifi_dev" ]]; then
                log_error "No Wi-Fi interface detected on this system."
                fail_and_exit
            fi

            ensure_wifi_radio
            
            # Force NM management in case it was flagged as unmanaged
            sudo nmcli device set "$wifi_dev" managed yes >/dev/null 2>&1 || true

            log_info "Triggering active 802.11 rescan on $wifi_dev..."
            timeout 10 sudo nmcli dev wifi rescan ifname "$wifi_dev" >/dev/null 2>&1 || true
            sleep 4 

            # Mapfile safely handles SSIDs with spaces. sort -u drops duplicated BSSIDs.
            mapfile -t networks < <(nmcli -g SSID dev wifi list ifname "$wifi_dev" 2>/dev/null | grep -v '^$' | sort -u || true)

            if [[ ${#networks[@]} -eq 0 ]]; then
                log_warn "No networks found on initial scan. Retrying scan..."
                timeout 10 sudo nmcli dev wifi rescan ifname "$wifi_dev" >/dev/null 2>&1 || true
                sleep 4
                mapfile -t networks < <(nmcli -g SSID dev wifi list ifname "$wifi_dev" 2>/dev/null | grep -v '^$' | sort -u || true)
                
                if [[ ${#networks[@]} -eq 0 ]]; then
                    log_error "No broadcasting 802.11 networks found in range."
                    fail_and_exit
                fi
            fi

            log_info "Discovered ${#networks[@]} available networks."
            PS3=$(echo -e "\n${C_CYAN}Select target SSID: ${C_RESET}")

            select ssid in "${networks[@]}"; do
                if [[ -n "$ssid" ]]; then
                    echo ""
                    read -r -s -p "Enter WPA/WEP password for '$ssid' (leave empty if open): " pass
                    echo -e "\n"
                    log_info "Negotiating handshake with '$ssid'..."

                    nm_cmd=(sudo nmcli -w 15 dev wifi connect "$ssid" ifname "$wifi_dev")
                    [[ -n "$pass" ]] && nm_cmd+=(password "$pass")

                    if timeout 30 "${nm_cmd[@]}" >/dev/null 2>&1; then
                        log_success "Layer 2 authentication successful."

                        active_con=$(nmcli -g GENERAL.CONNECTION dev show "$wifi_dev" 2>/dev/null | awk 'NR==1' || true)

                        if [[ -n "$active_con" ]]; then
                            sudo nmcli con modify "$active_con" \
                                connection.autoconnect yes \
                                connection.autoconnect-priority 99 >/dev/null 2>&1 || true
                            log_info "Profile '$active_con' hardened for future high-priority autoconnect."
                        else
                            log_warn "Could not resolve active connection profile. Autoconnect not configured."
                        fi

                        flush_dns_caches

                        if check_connectivity; then
                            log_success "Internet connectivity validated. Ready for pipeline execution."
                            exit 0
                        else
                            log_error "Connected to '$ssid', but ICMP/DNS routing failed (Possible captive portal)."
                            fail_and_exit
                        fi
                    else
                        log_error "Handshake failed. Invalid password, out of range, or AP rejected client."
                        fail_and_exit
                    fi
                else
                    log_warn "Invalid selection. Enter a number from the list."
                fi
            done
            ;;

        *)
            log_warn "Invalid input. Select 1 or 2."
            ;;
    esac
done
