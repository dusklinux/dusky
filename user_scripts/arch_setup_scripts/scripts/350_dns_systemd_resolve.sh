#!/usr/bin/env bash
# =============================================================================
# ARCH LINUX systemd-resolved CONFIGURATOR
# =============================================================================
# Purpose:
#   - Configure systemd-resolved via a drop-in under /etc/systemd/resolved.conf.d
#   - Use explicit public DNS servers with authenticated DoT server names
#   - Clear fallback DNS so distro/default fallback resolvers are not used
#   - Disable LLMNR
#   - Disable systemd-resolved mDNS handling so another mDNS stack
#     (commonly Avahi, often alongside nss-mdns in NSS) can own UDP/5353
#   - Select the correct /etc/resolv.conf target for stub vs non-stub mode
#
# Notes:
#   - Installed packages alone do not determine runtime behavior.
#   - nss-mdns only affects glibc/NSS lookups if /etc/nsswitch.conf contains
#     an mdns* entry in the hosts: line.
#   - If another local DNS service owns a conflicting port-53 listener,
#     this script will switch systemd-resolved to non-stub mode automatically.
#   - In non-stub mode, glibc applications only keep using systemd-resolved if
#     /etc/nsswitch.conf contains 'resolve' in the hosts: line.
#   - This script does not try to override per-link DNS supplied by DHCP,
#     NetworkManager, systemd-networkd, Tailscale, or other VPN software.
# =============================================================================

set -Eeuo pipefail
shopt -s inherit_errexit 2>/dev/null || true

readonly SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}" 2>/dev/null || printf '%s\n' "${BASH_SOURCE[0]}")"

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

declare -a TEMP_FILES=()
STUB_CONFLICT_LINE=""
SELECTED_STUB_MODE=""

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
    local f
    for f in "${TEMP_FILES[@]:-}"; do
        [[ -n "${f:-}" ]] && rm -f -- "$f"
    done
}

error_handler() {
    local rc=$?
    log_error "Failed at line ${BASH_LINENO[0]} while running: ${BASH_COMMAND} (exit ${rc})"
    exit "$rc"
}

trap cleanup EXIT
trap error_handler ERR

# ------------------------------ Root escalation -------------------------------
if (( EUID != 0 )); then
    if ! command -v sudo >/dev/null 2>&1; then
        log_error "Root privileges are required, but 'sudo' is not available."
        log_error "Re-run this script as root."
        exit 1
    fi

    log_info "Root privileges required. Elevating..."
    exec sudo -- "$SCRIPT_PATH" "$@"
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

    stamp="$(date +%Y%m%d-%H%M%S)"
    backup="${path}.bak.${stamp}"

    cp -a -- "$path" "$backup"
    log_info "Backed up $path -> $backup"
}

wait_for_path() {
    local path=$1
    local timeout_s=${2:-5}
    local max_tries=$(( timeout_s * 10 ))
    local i

    for (( i = 0; i < max_tries; i++ )); do
        [[ -e "$path" ]] && return 0
        sleep 0.1
    done

    return 1
}

systemd_resolved_available() {
    local load_state
    load_state="$(systemctl show --property=LoadState --value systemd-resolved.service 2>/dev/null || true)"
    [[ -n "$load_state" && "$load_state" != "not-found" ]]
}

nsswitch_uses_resolve() {
    grep -Eq '^[[:space:]]*hosts:.*[[:space:]]resolve([[:space:]]|$)' /etc/nsswitch.conf 2>/dev/null
}

nsswitch_uses_mdns() {
    grep -Eq '^[[:space:]]*hosts:.*[[:space:]]mdns[^[:space:]]*([[:space:]]|$)' /etc/nsswitch.conf 2>/dev/null
}

detect_stub_conflict() {
    local line

    command -v ss >/dev/null 2>&1 || return 1
    STUB_CONFLICT_LINE=""

    while IFS= read -r line; do
        [[ -n "$line" ]] || continue

        # Ignore systemd-resolved's own listeners.
        if [[ "$line" == *"systemd-resolve"* || "$line" == *"systemd-resolved"* ]]; then
            continue
        fi

        # Treat these listeners as conflicting with the stub:
        #   - wildcard :53
        #   - 127.0.0.53:53 or 127.0.0.54:53
        #
        # 127.0.0.1:53 alone is not considered a hard collision with 127.0.0.53:53.
        if [[ "$line" =~ [[:space:]](0\.0\.0\.0:53|\*:53|\[::\]:53|\[::ffff:0\.0\.0\.0\]:53|127\.0\.0\.5[34](%[^[:space:]]*)?:53)([[:space:]]|$) ]]; then
            STUB_CONFLICT_LINE="$line"
            return 0
        fi
    done < <(ss -H -ltnup 2>/dev/null | awk '$5 ~ /:53$/')

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
            if command -v ss >/dev/null 2>&1 && detect_stub_conflict; then
                SELECTED_STUB_MODE="no"
                log_warn "Detected a conflicting local DNS listener; using non-stub mode."
                printf '  %s\n' "$STUB_CONFLICT_LINE" >&2
            else
                SELECTED_STUB_MODE="yes"
                if ! command -v ss >/dev/null 2>&1; then
                    log_warn "'ss' not found; assuming no local port-53 conflict and using stub mode."
                fi
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
    local tmp stub_comment

    tmp="$(mktemp)"
    TEMP_FILES+=("$tmp")

    if [[ "$stub_mode" == "yes" ]]; then
        stub_comment="# Use the local systemd-resolved DNS stub."
    else
        stub_comment="# Disable the local stub because another local DNS listener is in use."
    fi

    cat > "$tmp" <<EOF
# Managed locally. Re-run the configurator script to update this file.
[Resolve]
# Public DNS with authenticated DoT server names.
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

    mkdir -p -- "$DROPIN_DIR"

    if [[ -f "$DROPIN_FILE" ]] && cmp -s "$tmp" "$DROPIN_FILE"; then
        log_info "Resolved drop-in already up to date."
        return 0
    fi

    if [[ -e "$DROPIN_FILE" || -L "$DROPIN_FILE" ]]; then
        backup_path "$DROPIN_FILE"
        rm -f -- "$DROPIN_FILE"
    fi

    install -m 0644 -- "$tmp" "$DROPIN_FILE"
    log_success "Wrote $DROPIN_FILE"
}

ensure_resolv_conf_symlink() {
    local target=$1
    local current=''

    if [[ -L "$ETC_RESOLV" ]]; then
        current="$(readlink -- "$ETC_RESOLV" || true)"
    fi

    if [[ "$current" == "$target" ]]; then
        log_info "$ETC_RESOLV already points to $target"
        return 0
    fi

    if [[ -e "$ETC_RESOLV" || -L "$ETC_RESOLV" ]]; then
        backup_path "$ETC_RESOLV"
        rm -f -- "$ETC_RESOLV"
    fi

    ln -s -- "$target" "$ETC_RESOLV"
    log_success "Linked $ETC_RESOLV -> $target"
}

post_checks() {
    local stub_mode=$1

    if [[ "$stub_mode" == "no" ]]; then
        log_info "Non-stub mode is active."
        log_info "glibc/NSS lookups will continue to use systemd-resolved if 'resolve' is present in /etc/nsswitch.conf."
        log_info "Programs that read resolv.conf directly will use the nameservers listed in $DIRECT_RESOLV."
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
    require_cmd install
    require_cmd cmp
    require_cmd awk
    require_cmd grep
    require_cmd ln
    require_cmd cp
    require_cmd rm
    require_cmd date

    if ! systemd_resolved_available; then
        log_error "systemd-resolved.service was not found on this system."
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
        log_error "Stub mode must be disabled because a conflicting local DNS listener owns port 53, but /etc/nsswitch.conf does not contain 'resolve'."
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
    systemctl unmask systemd-resolved.service >/dev/null 2>&1 || true
    systemctl enable --now systemd-resolved.service >/dev/null
    systemctl restart systemd-resolved.service

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

    ensure_resolv_conf_symlink "$resolv_target"
    post_checks "$stub_mode"

    printf "\n%s---[ systemd-resolved status ]---%s\n" "$C_BLUE" "$C_RESET"
    if command -v resolvectl >/dev/null 2>&1; then
        resolvectl status
    else
        log_warn "resolvectl not found; skipping status dump."
    fi
}

main "$@"
