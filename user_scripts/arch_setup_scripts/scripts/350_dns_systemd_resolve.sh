#!/usr/bin/env bash
#d: Configure systemd-resolved DNS

set -Eeuo pipefail
shopt -s inherit_errexit

# ----------------------------- User-tunable defaults --------------------------
# Stub mode:
#   auto : use systemd-resolved's local stub unless a conflicting local port-53
#          listener is detected
#   yes  : force stub mode (/etc/resolv.conf -> stub-resolv.conf)
#   no   : force non-stub mode (/etc/resolv.conf -> resolv.conf)
readonly STUB_MODE_PREFERENCE="auto"   # auto | yes | no

declare -ra DNS_SERVERS=(
  "9.9.9.9#dns.quad9.net"
  "149.112.112.112#dns.quad9.net"
  "2620:fe::fe#dns.quad9.net"
  "2620:fe::9#dns.quad9.net"
  "1.1.1.1#cloudflare-dns.com"
  "1.0.0.1#cloudflare-dns.com"
  "2606:4700:4700::1111#cloudflare-dns.com"
  "2606:4700:4700::1001#cloudflare-dns.com"
)

# Compatibility-first defaults.
readonly DNSSEC_MODE="no"
readonly DNS_OVER_TLS_MODE="opportunistic"
readonly LLMNR_MODE="no"
readonly MULTICAST_DNS_MODE="no"

readonly DROPIN_DIR="/etc/systemd/resolved.conf.d"
readonly DROPIN_FILE="${DROPIN_DIR}/99-custom-dns.conf"
readonly STUB_RESOLV="/run/systemd/resolve/stub-resolv.conf"
readonly DIRECT_RESOLV="/run/systemd/resolve/resolv.conf"
readonly ETC_RESOLV="/etc/resolv.conf"

declare -a TEMP_DIRS=()
STUB_CONFLICT_LINE=""
SELECTED_STUB_MODE=""
DNS_LISTENERS=""
RESOLVED_PID=""

# ---------------------------------- Colors -----------------------------------
if [[ -t 1 ]]; then
    readonly C_RESET=$'\e[0m'
    readonly C_GREEN=$'\e[1;32m'
    readonly C_BLUE=$'\e[1;34m'
    readonly C_RED=$'\e[1;31m'
    readonly C_YELLOW=$'\e[1;33m'
else
    readonly C_RESET=''
    readonly C_GREEN=''
    readonly C_BLUE=''
    readonly C_RED=''
    readonly C_YELLOW=''
fi

# --------------------------------- Logging -----------------------------------
log_info()    { printf "%s[INFO]%s %s\n"  "$C_BLUE"   "$C_RESET" "${1:-}"; }
log_success() { printf "%s[OK]%s %s\n"    "$C_GREEN"  "$C_RESET" "${1:-}"; }
log_warn()    { printf "%s[WARN]%s %s\n"  "$C_YELLOW" "$C_RESET" "${1:-}" >&2; }
log_error()   { printf "%s[ERROR]%s %s\n" "$C_RED"    "$C_RESET" "${1:-}" >&2; }

cleanup() {
    local dir
    for dir in "${TEMP_DIRS[@]}"; do
        rm -rf -- "$dir" || log_warn "Could not remove temporary directory: $dir"
    done
}

error_handler() {
    local rc=$1 line=$2 command=$3
    log_error "Failed at line ${line} while running: ${command} (exit ${rc})"
    exit "$rc"
}

trap cleanup EXIT
trap 'error_handler "$?" "$LINENO" "$BASH_COMMAND"' ERR

# ------------------------------ Root escalation -------------------------------
if (( EUID != 0 )); then
    if ! command -v sudo >/dev/null 2>&1; then
        log_error "Root privileges are required, but 'sudo' is not available."
        log_error "Re-run this script as root."
        exit 1
    fi

    log_info "Root privileges required. Elevating..."
    SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")
    exec sudo -- bash "$SCRIPT_PATH" "$@"
fi

# -------------------------------- Utilities ----------------------------------
require_cmd() {
    local cmd=$1
    command -v "$cmd" >/dev/null 2>&1 || {
        log_error "Required command not found: $cmd"
        exit 1
    }
}

join_by() {
    local IFS=$1
    shift
    printf '%s' "$*"
}

backup_path() {
    local path=$1
    local stamp backup

    stamp="$(date +%Y%m%d-%H%M%S.%N)"
    backup="${path}.bak.${stamp}"

    cp -aT --backup=numbered -- "$path" "$backup"
    log_info "Backed up $path -> $backup"
}

wait_for_path() {
    local path=$1
    local timeout_s=${2:-5}
    local max_tries=$(( timeout_s * 10 ))
    local i

    for (( i = 0; i < max_tries; i++ )); do
        [[ -f "$path" && -s "$path" && -r "$path" ]] && return 0
        sleep 0.1
    done

    return 1
}

systemd_resolved_available() {
    local load_state
    load_state=$(systemctl show --property=LoadState --value systemd-resolved.service) || return 1
    [[ "$load_state" == loaded || "$load_state" == masked ]]
}

nsswitch_uses_resolve() {
    grep -Eq '^[[:space:]]*hosts:[^#]*[[:space:]]resolve([[:space:]#]|$)' /etc/nsswitch.conf 2>/dev/null
}

nsswitch_uses_mdns() {
    grep -Eq '^[[:space:]]*hosts:[^#]*[[:space:]]mdns[^[:space:]#]*([[:space:]#]|$)' /etc/nsswitch.conf 2>/dev/null
}

read_dns_listeners() {
    # Capture the exit status directly: process substitution would hide ss failures.
    DNS_LISTENERS=$(ss -H -ltnup 'sport = :53') || {
        log_error "Could not inspect local DNS sockets."
        exit 1
    }
    RESOLVED_PID=$(systemctl show --property=MainPID --value systemd-resolved.service) || {
        log_error "Could not inspect systemd-resolved's process ID."
        exit 1
    }
    [[ "$RESOLVED_PID" =~ ^[0-9]+$ ]] || {
        log_error "Could not determine systemd-resolved's process ID."
        exit 1
    }
}

detect_stub_conflict() {
    local line _netid _state _recvq _sendq address _peer _owner

    read_dns_listeners
    STUB_CONFLICT_LINE=""

    while IFS= read -r line; do
        [[ -n "$line" ]] || continue

        # Process names may be truncated; match the service's actual PID.
        if [[ "$RESOLVED_PID" != 0 && "$line" == *"pid=${RESOLVED_PID},"* ]]; then
            continue
        fi

        read -r _netid _state _recvq _sendq address _peer _owner <<< "$line"

        # Treat these listeners as conflicting with the stub:
        #   - wildcard :53
        #   - 127.0.0.53:53 or 127.0.0.54:53
        #
        # 127.0.0.1:53 alone is not considered a hard collision with 127.0.0.53:53.
        if [[ "$address" =~ ^(0\.0\.0\.0|\*|\[::\]|\[::ffff:0\.0\.0\.0\]|127\.0\.0\.5[34])(%[^[:space:]]*)?:53$ ]]; then
            STUB_CONFLICT_LINE="$line"
            return 0
        fi
    done <<< "$DNS_LISTENERS"

    return 1
}

select_stub_mode() {
    SELECTED_STUB_MODE=""
    STUB_CONFLICT_LINE=""

    case "$STUB_MODE_PREFERENCE" in
        yes|no)
            SELECTED_STUB_MODE="$STUB_MODE_PREFERENCE"
            ;;
        auto)
            if detect_stub_conflict; then
                SELECTED_STUB_MODE="no"
                log_warn "Detected a conflicting local DNS listener; using non-stub mode."
                printf '  %s\n' "$STUB_CONFLICT_LINE" >&2
            else
                SELECTED_STUB_MODE="yes"
            fi
            ;;
        *)
            log_error "Invalid STUB_MODE_PREFERENCE: $STUB_MODE_PREFERENCE"
            exit 1
            ;;
    esac
}

write_dropin() {
    local stub_mode=$1
    local tmp_dir tmp stub_comment

    install -d -m 0755 -- "$DROPIN_DIR"
    tmp_dir=$(mktemp -d "${DROPIN_DIR}/.dns-config.XXXXXXXXXX")
    TEMP_DIRS+=("$tmp_dir")
    tmp="${tmp_dir}/resolved.conf"

    if [[ "$stub_mode" == "yes" ]]; then
        stub_comment="# Use the local systemd-resolved DNS stub."
    else
        stub_comment="# Use direct resolv.conf mode; NSS clients should use nss-resolve."
    fi

    cat > "$tmp" <<EOF
# Managed locally. Re-run the configurator script to update this file.
[Resolve]
# Replace earlier global DNS lists. Per-link DNS and VPN routing remain in effect.
DNS=
# Public DNS with DoT server names; opportunistic TLS does not authenticate them.
DNS=$(join_by ' ' "${DNS_SERVERS[@]}")

# Clear any distro/default fallback resolver list.
FallbackDNS=

# Compatibility-first defaults.
DNSSEC=${DNSSEC_MODE}
DNSOverTLS=${DNS_OVER_TLS_MODE}

# LLMNR disabled.
LLMNR=${LLMNR_MODE}

# systemd-resolved mDNS handling is disabled intentionally.
# This avoids UDP/5353 contention if another mDNS responder is used
# (commonly avahi-daemon in setups that also use nss-mdns).
MulticastDNS=${MULTICAST_DNS_MODE}

${stub_comment}
DNSStubListener=${stub_mode}
EOF

    if [[ -f "$DROPIN_FILE" && ! -L "$DROPIN_FILE" ]] && cmp -s "$tmp" "$DROPIN_FILE"; then
        # Content equality alone does not guarantee the daemon can read the file.
        chmod 0644 -- "$DROPIN_FILE"
        log_info "Resolved drop-in already up to date."
        return 0
    fi

    if [[ -e "$DROPIN_FILE" || -L "$DROPIN_FILE" ]]; then
        backup_path "$DROPIN_FILE"
    fi

    chmod 0644 -- "$tmp"
    # Same-filesystem rename preserves the old configuration on write failure.
    mv -fT -- "$tmp" "$DROPIN_FILE"
    log_success "Wrote $DROPIN_FILE"
}

ensure_resolv_conf_symlink() {
    local target=$1
    local current=''
    local tmp_dir

    if [[ -L "$ETC_RESOLV" ]]; then
        # Compare the link itself: resolved can retarget stub-resolv.conf when
        # the stub is disabled, making canonical paths misleading here.
        current="$(readlink -- "$ETC_RESOLV" || true)"
    fi

    if [[ "$current" == "$target" ]]; then
        log_info "$ETC_RESOLV already points to $target"
        return 0
    fi

    # Do not follow a symlink and alter the immutable flag on its target.
    if [[ -f "$ETC_RESOLV" && ! -L "$ETC_RESOLV" ]] && command -v chattr >/dev/null 2>&1; then
        chattr -i "$ETC_RESOLV" 2>/dev/null || true
    fi

    if [[ -e "$ETC_RESOLV" || -L "$ETC_RESOLV" ]]; then
        backup_path "$ETC_RESOLV"
    fi

    tmp_dir=$(mktemp -d "${ETC_RESOLV%/*}/.dns-link.XXXXXXXXXX")
    TEMP_DIRS+=("$tmp_dir")
    ln -s -- "$target" "${tmp_dir}/resolv.conf"
    mv -fT -- "${tmp_dir}/resolv.conf" "$ETC_RESOLV"
    log_success "Linked $ETC_RESOLV -> $target"
}

verify_stub_listener() {
    local netid _state _recvq _sendq address _peer owner
    local udp=no tcp=no

    read_dns_listeners
    while read -r netid _state _recvq _sendq address _peer owner; do
        [[ "$address" == 127.0.0.53:53 && "$RESOLVED_PID" != 0 &&
           "$owner" == *"pid=${RESOLVED_PID},"* ]] || continue
        case "$netid" in
            udp) udp=yes ;;
            tcp) tcp=yes ;;
        esac
    done <<< "$DNS_LISTENERS"

    if [[ "$udp" != yes || "$tcp" != yes ]]; then
        log_error "systemd-resolved does not own both UDP and TCP listeners on 127.0.0.53:53."
        exit 1
    fi
}

post_checks() {
    local stub_mode=$1

    if [[ "$stub_mode" == "no" ]]; then
        log_info "Non-stub mode is active."
        log_info "NSS clients can use systemd-resolved through the 'resolve' entry in /etc/nsswitch.conf."
        log_info "Programs that read resolv.conf directly will use the nameservers listed in $DIRECT_RESOLV."
        log_info "Those programs bypass resolved's cache, DNS routing, and DNS-over-TLS."
    fi

    if [[ "$MULTICAST_DNS_MODE" == "no" ]] && ! nsswitch_uses_mdns; then
        log_info "If you expect .local hostname resolution, ensure /etc/nsswitch.conf contains an mdns* entry in the hosts: line and that an mDNS responder such as avahi-daemon is installed/running."
    fi
}

# ----------------------------------- Main ------------------------------------
main() {
    local stub_mode resolv_target

    require_cmd systemctl
    require_cmd readlink
    require_cmd mktemp
    require_cmd chmod
    require_cmd install
    require_cmd mv
    require_cmd cmp
    require_cmd ss
    require_cmd grep
    require_cmd ln
    require_cmd cp
    require_cmd rm
    require_cmd date
    require_cmd sleep
    require_cmd cat

    if ! systemd_resolved_available; then
        log_error "systemd-resolved.service is unavailable or could not be inspected."
        exit 1
    fi

    select_stub_mode
    stub_mode="$SELECTED_STUB_MODE"

    if [[ "$stub_mode" == "yes" ]]; then
        resolv_target="$STUB_RESOLV"
    else
        resolv_target="$DIRECT_RESOLV"
    fi

    # In non-stub mode, requiring 'resolve' in NSS avoids a setup where many
    # glibc applications silently bypass systemd-resolved entirely.
    if [[ "$stub_mode" == "no" ]] && ! nsswitch_uses_resolve; then
        log_error "Non-stub mode requires 'resolve' in the hosts: line of /etc/nsswitch.conf."
        log_error "In that state, many glibc applications would bypass systemd-resolved."
        if [[ -n "$STUB_CONFLICT_LINE" ]]; then
            printf '  Conflicting listener: %s\n' "$STUB_CONFLICT_LINE" >&2
        fi
        log_error "Either stop/reconfigure the conflicting DNS service, add 'resolve' to the hosts: line in /etc/nsswitch.conf, or use a different DNS design."
        exit 1
    fi

    log_info "Selected DNS stub mode: $stub_mode"
    write_dropin "$stub_mode"

    log_info "Enabling and restarting systemd-resolved..."
    systemctl unmask systemd-resolved.service >/dev/null
    systemctl enable systemd-resolved.service >/dev/null
    if ! systemctl restart systemd-resolved.service; then
        log_error "Could not start systemd-resolved. Configuration backups were retained."
        systemctl status systemd-resolved.service --no-pager -n 20 || true
        exit 1
    fi

    if ! systemctl is-active --quiet systemd-resolved.service; then
        log_error "systemd-resolved is not active after restart."
        systemctl status systemd-resolved.service --no-pager -n 20 || true
        exit 1
    fi
    log_success "systemd-resolved is active."

    log_info "Waiting for resolver file: $resolv_target"
    if ! wait_for_path "$resolv_target" 5; then
        log_error "Expected resolver file was not created: $resolv_target"
        systemctl status systemd-resolved.service --no-pager -n 20 || true
        exit 1
    fi

    if [[ "$stub_mode" == yes ]]; then
        verify_stub_listener
    fi
    ensure_resolv_conf_symlink "$resolv_target"
    post_checks "$stub_mode"

    printf "\n%s---[ systemd-resolved status ]---%s\n" "$C_BLUE" "$C_RESET"
    if command -v resolvectl >/dev/null 2>&1; then
        resolvectl --no-pager status || log_warn "Could not display resolver status."
    else
        log_warn "resolvectl not found; skipping status dump."
    fi
}

main "$@"
