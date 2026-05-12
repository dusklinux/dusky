#!/usr/bin/env bash
# Generate Xsetup for SDDM based on Hyprland monitor configuration
# Converts Wayland monitor names to X11 names for xrandr

set -euo pipefail

# --- Colors ---
readonly RED=$'\033[0;31m'
readonly GREEN=$'\033[0;32m'
readonly YELLOW=$'\033[1;33m'
readonly CYAN=$'\033[0;36m'
readonly RESET=$'\033[0m'

# --- Privilege Escalation ---
if [[ $EUID -ne 0 ]]; then
	exec sudo "$0" "$@"
fi

# --- Constants ---
MONITORS_CONF="${HOME}/.config/hypr/edit_here/source/monitors.conf"
XSETUP_FILE="/usr/share/sddm/scripts/Xsetup"
SDDM_CONF="/etc/sddm.conf.d/10-dusky-theme.conf"

# --- Cleanup ---
cleanup() { return 0; }
trap cleanup EXIT

# --- Logging ---
log_info() { printf '%b[INFO]%b %s\n' "${CYAN}" "${RESET}" "$1"; }
log_success() { printf '%b[OK]%b %s\n' "${GREEN}" "${RESET}" "$1"; }
log_warn() { printf '%b[WARN]%b %s\n' "${YELLOW}" "${RESET}" "$1" >&2; }

# --- Validation ---
[[ -f "${MONITORS_CONF}" ]] || {
	printf '%bError:%b monitors.conf not found at %s\n' "${RED}" "${RESET}" "${MONITORS_CONF}" >&2
	exit 1
}

log_info "Generating Xsetup from ${MONITORS_CONF}..."

# --- Map Wayland to X11 names ---
get_x11_name() {
	local wl_name="$1"
	case "${wl_name}" in
	DP-[0-9]*) printf "DisplayPort-%d" $((10#${wl_name#DP-} - 1)) ;;
	HDMI-A-[0-9]*) printf "HDMI-%d" $((10#${wl_name#HDMI-A-} - 1)) ;;
	HDMI-[0-9]*) printf "HDMI-%d" $((10#${wl_name#HDMI-} - 1)) ;;
	eDP-[0-9]*) printf "eDP-%d" $((10#${wl_name#eDP-} - 1)) ;;
	*) printf '%s' "$wl_name" ;;
	esac
}

# --- Generate xrandr command ---
XRANDR_CMD="xrandr"
PRIMARY_SET=false

while IFS= read -r line; do
	[[ "$line" =~ ^monitor[[:space:]]*= ]] || continue

	if [[ "$line" =~ transform ]]; then
		WL_NAME=$(printf '%s' "$line" | sed 's/monitor = //' | awk -F', *' '{print $1}')
		TRANSFORM=$(printf '%s' "$line" | sed -n 's/.*transform, *\([0-9]*\),.*/\1/p')
		XL_NAME=$(get_x11_name "$WL_NAME")

		case "$TRANSFORM" in
		0) ROTATE="normal" ;;
		1) ROTATE="left" ;;
		2) ROTATE="inverted" ;;
		3) ROTATE="right" ;;
		*) ROTATE="normal" ;;
		esac

		XRANDR_CMD="${XRANDR_CMD} --output ${XL_NAME} --rotate ${ROTATE}"
	else
		PARSED=$(printf '%s' "$line" | sed 's/monitor = //' | awk -F', *' '{print $1, $2, $3}')
		WL_NAME=$(printf '%s' "$PARSED" | awk '{print $1}')
		RES_RATE=$(printf '%s' "$PARSED" | awk '{print $2}')
		POS=$(printf '%s' "$PARSED" | awk '{print $3}')

		RES=${RES_RATE%%@*}
		RATE=${RES_RATE##*@}

		XL_NAME=$(get_x11_name "$WL_NAME")
		XRANDR_CMD="${XRANDR_CMD} --output ${XL_NAME} --mode ${RES} --rate ${RATE} --pos ${POS}"

		if [[ "$POS" == "0x0" && "$PRIMARY_SET" == "false" ]]; then
			XRANDR_CMD="${XRANDR_CMD} --primary"
			PRIMARY_SET=true
		fi
	fi
done <"${MONITORS_CONF}"

# --- Write Xsetup ---
OUTPUT="#!/bin/sh
# Xsetup - generated from Hyprland monitors.conf
export DISPLAY=:0
xrandr > /tmp/xsetup.log 2>&1
${XRANDR_CMD}
"

printf '%s' "$OUTPUT" | tee "${XSETUP_FILE}" >/dev/null
chmod 755 "${XSETUP_FILE}"

# --- Update SDDM config ---
update_sddm_conf() {
	local section="$1"
	local key="$2"
	local value="$3"

	if grep -q "^\[${section}\]" "${SDDM_CONF}" 2>/dev/null; then
		grep -q "^${key}=" "${SDDM_CONF}" 2>/dev/null || printf '%s=%s\n' "$key" "$value" >>"${SDDM_CONF}"
	else
		printf '\n[%s]\n%s=%s\n' "$section" "$key" "$value" >>"${SDDM_CONF}"
	fi
}

update_sddm_conf "X11" "DisplayCommand" "${XSETUP_FILE}"

log_success "Xsetup generated at ${XSETUP_FILE}"
