#!/usr/bin/env bash
# ==============================================================================
# dusky_wireguard_new.sh
# ==============================================================================
# Interactive wizard to create a new wg-quick tunnel config.
# The resulting file is written as root:root 600 — private keys never land
# on disk with loose permissions.
#
# Usage: run from the Dusky Control Center WireGuard page.
# ==============================================================================
set -euo pipefail

readonly CLR_GRN=$'\e[1;32m'
readonly CLR_YLW=$'\e[1;33m'
readonly CLR_RED=$'\e[1;31m'
readonly CLR_BLU=$'\e[1;34m'
readonly CLR_CYN=$'\e[1;36m'
readonly CLR_RST=$'\e[0m'
readonly CLR_DIM=$'\e[2m'

ok()     { printf '%s[OK]%s    %s\n' "$CLR_GRN" "$CLR_RST" "$1"; }
info()   { printf '%s[INFO]%s  %s\n' "$CLR_BLU" "$CLR_RST" "$1"; }
warn()   { printf '%s[WARN]%s  %s\n' "$CLR_YLW" "$CLR_RST" "$1"; }
err()    { printf '%s[ERR]%s   %s\n' "$CLR_RED" "$CLR_RST" "$1" >&2; }
prompt() { printf '%s%s%s ' "$CLR_CYN" "$1" "$CLR_RST"; }
header() { printf '\n%s══ %s ══%s\n\n' "$CLR_BLU" "$1" "$CLR_RST"; }

# ── Dependency check ──────────────────────────────────────────────────────────
if ! command -v wg &>/dev/null; then
    err "wireguard-tools not installed. Run: sudo pacman -S wireguard-tools"
    exit 1
fi

header "Dusky WireGuard — New Tunnel Wizard"
info "Config is written directly to /etc/wireguard/<name>.conf as root:root 600."
info "The private key never touches a user-writable path."
echo

# ── Interface name ─────────────────────────────────────────────────────────────
while true; do
    prompt "Interface name (e.g. wg0, vpn1, work):"
    read -r IFACE
    IFACE="${IFACE//[^a-zA-Z0-9_-]/}"      # strip unsafe chars
    if [[ -z "$IFACE" ]]; then
        warn "Name cannot be empty"; continue
    fi
    if [[ -f "/etc/wireguard/${IFACE}.conf" ]]; then
        warn "/etc/wireguard/${IFACE}.conf already exists — choose a different name"
        continue
    fi
    break
done

# ── Key generation ─────────────────────────────────────────────────────────────
info "Generating keypair ..."
PRIVATE_KEY="$(wg genkey)"
PUBLIC_KEY="$(printf '%s' "$PRIVATE_KEY" | wg pubkey)"
ok "Keys generated (private key stays in memory only)"
printf '  %sPublic key:%s  %s\n' "$CLR_DIM" "$CLR_RST" "$PUBLIC_KEY"
echo

# ── Interface address ──────────────────────────────────────────────────────────
prompt "Tunnel IP address (e.g. 10.0.0.2/24):"
read -r IFACE_ADDR

# ── DNS (optional) ─────────────────────────────────────────────────────────────
prompt "DNS server (leave blank to skip, e.g. 1.1.1.1):"
read -r DNS

# ── Peer configuration ─────────────────────────────────────────────────────────
header "Peer (Server) Configuration"
prompt "Peer public key:"
read -r PEER_PUBKEY

prompt "Peer endpoint (host:port, e.g. vpn.example.com:51820):"
read -r PEER_ENDPOINT

prompt "Allowed IPs (e.g. 0.0.0.0/0 for full-tunnel, or 10.0.0.0/24):"
read -r ALLOWED_IPS
ALLOWED_IPS="${ALLOWED_IPS:-0.0.0.0/0, ::/0}"

prompt "Persistent keepalive in seconds (leave blank to skip, e.g. 25):"
read -r KEEPALIVE

# ── Build config content ───────────────────────────────────────────────────────
CONFIG_CONTENT="[Interface]
PrivateKey = ${PRIVATE_KEY}
Address = ${IFACE_ADDR}"

if [[ -n "$DNS" ]]; then
    CONFIG_CONTENT+="
DNS = ${DNS}"
fi

CONFIG_CONTENT+="

[Peer]
PublicKey = ${PEER_PUBKEY}
Endpoint = ${PEER_ENDPOINT}
AllowedIPs = ${ALLOWED_IPS}"

if [[ -n "$KEEPALIVE" && "$KEEPALIVE" =~ ^[0-9]+$ ]]; then
    CONFIG_CONTENT+="
PersistentKeepalive = ${KEEPALIVE}"
fi

# ── Review ─────────────────────────────────────────────────────────────────────
header "Review"
printf '%s%s%s\n' "$CLR_DIM" "$CONFIG_CONTENT" "$CLR_RST"
echo
prompt "Write to /etc/wireguard/${IFACE}.conf? [y/N]:"
read -r CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    warn "Aborted — no file written"
    exit 0
fi

# ── Write as root ─────────────────────────────────────────────────────────────
DEST="/etc/wireguard/${IFACE}.conf"
printf '%s\n' "$CONFIG_CONTENT" | sudo tee "$DEST" > /dev/null
sudo chown root:root "$DEST"
sudo chmod 600 "$DEST"

ok "Config written: $DEST (root:root 600)"
printf '  %sPublic key:%s  %s\n' "$CLR_DIM" "$CLR_RST" "$PUBLIC_KEY"
echo
info "Reload the Dusky Control Center (Ctrl+R) to see the new tunnel."
echo
printf 'Press Enter to close...'
read -r
