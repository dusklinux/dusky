#!/usr/bin/env bash
# Requires: bash 5.0+, NetworkManager (nmcli), systemd
# Target: Arch Linux / Hyprland Ecosystem

set -euo pipefail

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
# FIX #1: Trap SIGTERM in addition to SIGINT.
# SIGTERM is sent by systemctl, kill, and orchestration tooling — not trapping
# it left the script unable to clean up in those contexts.
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

check_nm_health() {
    if ! systemctl is-active --quiet NetworkManager; then
        log_error "NetworkManager service is not running."
        exit 1
    fi
}

check_connectivity() {
    # 1. Primary Layer 7 Probe: Query NetworkManager's internal state
    local nm_status
    nm_status=$(nmcli -w 5 networking connectivity check 2>/dev/null || echo "unknown")
    
    if [[ "$nm_status" == "full" ]]; then
        return 0
    fi

    # 2. Secondary Layer 3/4 Probe: Fallback for Arch Linux where NM connectivity URIs 
    # might not be configured, causing NM to report 'unknown' despite functional routing.
    
    # Check raw IPv4 or IPv6 routing (Cloudflare DNS edges)
    if ping -c 1 -W 2 1.1.1.1 >/dev/null 2>&1 || ping -c 1 -W 2 2606:4700:4700::1111 >/dev/null 2>&1; then
        
        # 3. DNS Validation: Ensure we aren't stuck behind a captive portal or dead resolver
        if ping -c 1 -W 2 archlinux.org >/dev/null 2>&1; then
            # We have ICMP routing AND valid DNS resolution. We are online.
            return 0
        fi
    fi

    # If all layers fail, we are genuinely offline.
    return 1
}

ensure_wifi_radio() {
    local radio_state
    # -g extracts exact value without headers
    radio_state=$(nmcli -g WIFI radio)

    if [[ "$radio_state" != "enabled" ]]; then
        log_warn "Wi-Fi radio is currently disabled (possible rfkill block)."
        log_info "Attempting to unblock and power on Wi-Fi radio..."
        nmcli radio wifi on
        sleep 3 # Allow hardware PHY to initialize

        # Re-verify
        if [[ $(nmcli -g WIFI radio) != "enabled" ]]; then
            log_error "Failed to enable Wi-Fi radio. Check hardware switches."
            fail_and_exit
        fi
    fi
}

# ==============================================================================
# Main Execution
# ==============================================================================

log_info "Initializing Network Orchestrator Phase 0..."
check_nm_health

log_info "Verifying current routing table and internet access..."
if check_connectivity; then
    log_success "System is already connected to the internet."
    exit 0
fi

log_warn "No internet routing detected."

# Build interactive menu
PS3=$(echo -e "\n${C_CYAN}Select connection interface (1/2): ${C_RESET}")

select conn_method in "LAN (Wired)" "Wi-Fi"; do
    case $conn_method in
        "LAN (Wired)")
            # 1. Identify primary ethernet device
            eth_dev=$(nmcli -g DEVICE,TYPE dev | awk -F: '$2=="ethernet"{print $1}' | head -n1)

            if [[ -z "$eth_dev" ]]; then
                log_error "No physical Ethernet interface detected on this system."
                fail_and_exit
            fi

            log_info "Primary Ethernet device detected: $eth_dev"
            echo -e "${C_YELLOW}[+] Please ensure your Ethernet cable is physically plugged in.${C_RESET}"
            read -r -p "Press Enter to verify carrier state..."

            # 2. Check hardware carrier state (prevent hanging on unplugged cables)
            # FIX #2: Use a glob match on the semantic token "unavailable" instead of
            # an exact string match on "20 (unavailable)". The -g terse output format
            # for GENERAL.STATE is implementation-defined across nmcli versions and has
            # historically varied. The "unavailable" token is stable across all versions
            # and is unique to state 20 — no other NM device state contains this string.
            dev_state=$(nmcli -g GENERAL.STATE dev show "$eth_dev" | head -n1)
            if [[ "$dev_state" == *"unavailable"* ]]; then
                log_error "No carrier detected on $eth_dev. The cable is unplugged or the switch port is dead."
                fail_and_exit
            fi

            log_info "Carrier detected. Requesting DHCP lease..."
            # -w 15 ensures we don't hang forever if DHCP server is unresponsive
            if nmcli -w 15 dev up "$eth_dev" >/dev/null 2>&1; then
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
            ensure_wifi_radio

            # FIX #3: Resolve the active Wi-Fi device explicitly, mirroring the eth_dev
            # pattern in the LAN branch. This ensures all subsequent Wi-Fi operations
            # (rescan, list, connect) are targeted at a specific interface rather than
            # relying on NM's implicit device selection — critical on systems with
            # multiple Wi-Fi adapters (e.g., internal + USB dongle).
            wifi_dev=$(nmcli -g DEVICE,TYPE dev | awk -F: '$2=="wifi"{print $1}' | head -n1)

            if [[ -z "$wifi_dev" ]]; then
                log_error "No Wi-Fi interface detected on this system."
                fail_and_exit
            fi

            log_info "Triggering active 802.11 rescan on $wifi_dev..."
            # Suppress error if NM complains about scanning too frequently
            nmcli dev wifi rescan ifname "$wifi_dev" >/dev/null 2>&1 || true
            sleep 4 # Allow BSSID population in D-Bus

            # 3. Safely map SSIDs to array, ignoring empty (hidden) networks.
            # The '|| true' prevents pipefail from crashing the script if grep finds nothing.
            mapfile -t networks < <(nmcli -g SSID dev wifi list ifname "$wifi_dev" | grep -v '^$' | sort -u || true)

            if [[ ${#networks[@]} -eq 0 ]]; then
                log_error "No broadcasting 802.11 networks found in range."
                fail_and_exit
            fi

            log_info "Discovered ${#networks[@]} available networks."
            PS3=$(echo -e "\n${C_CYAN}Select target SSID: ${C_RESET}")

            select ssid in "${networks[@]}"; do
                if [[ -n "$ssid" ]]; then
                    echo ""
                    # 4. Handle authentication securely
                    read -r -s -p "Enter WPA/WEP password for '$ssid' (leave empty if open): " pass
                    echo -e "\n"
                    log_info "Negotiating handshake with '$ssid'..."

                    # Build command dynamically to avoid empty password parameter errors.
                    # ifname is specified explicitly to target the resolved Wi-Fi device.
                    nm_cmd=(nmcli -w 15 dev wifi connect "$ssid" ifname "$wifi_dev")
                    [[ -n "$pass" ]] && nm_cmd+=(password "$pass")

                    if "${nm_cmd[@]}" >/dev/null 2>&1; then
                        log_success "Layer 2 authentication successful."

                        # 5. DevOps Standards: High priority, persistent autoconnect.
                        # FIX #4: Resolve the actual NM connection profile name from the
                        # active device state instead of assuming the profile is named
                        # after the SSID. A mismatch (e.g., user renamed the profile)
                        # would cause 'nmcli con modify "$ssid"' to fail under set -e,
                        # crashing the script after a successful connection. Using the
                        # device's active connection name is always authoritative.
                        active_con=$(nmcli -g GENERAL.CONNECTION dev show "$wifi_dev" 2>/dev/null | head -n1 || true)

                        if [[ -n "$active_con" ]]; then
                            nmcli con modify "$active_con" \
                                connection.autoconnect yes \
                                connection.autoconnect-priority 99 || true
                            log_info "Profile '$active_con' hardened for future high-priority autoconnect."
                        else
                            log_warn "Could not resolve active connection profile. Autoconnect not configured."
                        fi

                        # 6. Verify Layer 3 routing
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
