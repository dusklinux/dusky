#!/usr/bin/env bash
# Downloads wayclick soundpacks and copies them to the required directory

set -euo pipefail

# --- Configuration -----------------------------------------------------------
readonly ZIP_URL="https://github.com/dusklinux/wayclick_soundpacks/archive/refs/heads/main.zip"
readonly TARGET_DIR="${HOME:?HOME not set}/.config/wayclick"
readonly CACHE_DIR="${TARGET_DIR}/.dusk-soundpacks-cache"
readonly CACHE_FILE="${CACHE_DIR}/dusk-soundpacks.zip"
readonly SETTINGS_DIR="${HOME:?HOME not set}/.config/dusky/settings"
readonly STATE_FILE="${SETTINGS_DIR}/wayclick_soundpack_git"

# --- Argument Parsing --------------------------------------------------------
AUTO_MODE=0
FORCE_MODE=0

while (($#)); do
	case "$1" in
	--auto)
		AUTO_MODE=1
		;;
	--force)
		FORCE_MODE=1
		;;
	*)
		echo "Usage: $0 [--auto] [--force]"
		exit 1
		;;
	esac
	shift
done

# --- Terminal Setup (graceful degradation) -----------------------------------
if [[ -t 1 ]]; then
	readonly RST=$'\033[0m' BOLD=$'\033[1m'
	readonly RED=$'\033[31m' GRN=$'\033[32m' YEL=$'\033[33m' BLU=$'\033[34m'
	readonly CLR=$'\033[K'
	readonly IS_TTY=1
else
	readonly RST='' BOLD='' RED='' GRN='' YEL='' BLU='' CLR=''
	readonly IS_TTY=0
fi

# --- Logging -----------------------------------------------------------------
log_info() { printf '%s[INFO]%s %s\n' "${BLU}" "${RST}" "$*"; }
log_ok() { printf '%s[ OK ]%s %s\n' "${GRN}" "${RST}" "$*"; }
log_warn() { printf '%s[WARN]%s %s\n' "${YEL}" "${RST}" "$*" >&2; }
log_error() { printf '%s[ERR ]%s %s\n' "${RED}" "${RST}" "$*" >&2; }

# --- Status Indicator --------------------------------------------------------
CURRENT_STATUS=""

status_begin() {
	CURRENT_STATUS="$1"
	if ((IS_TTY)); then
		printf '\r%s[....]%s %s%s' "${BLU}" "${RST}" "${CURRENT_STATUS}" "${CLR}"
	fi
}

status_end() {
	local -r rc=$1
	if ((IS_TTY)); then
		if ((rc == 0)); then
			printf '\r%s[ OK ]%s %s%s\n' "${GRN}" "${RST}" "${CURRENT_STATUS}" "${CLR}"
		else
			printf '\r%s[FAIL]%s %s%s\n' "${RED}" "${RST}" "${CURRENT_STATUS}" "${CLR}"
		fi
	else
		if ((rc == 0)); then
			log_ok "${CURRENT_STATUS}"
		else
			log_error "${CURRENT_STATUS}"
		fi
	fi
	CURRENT_STATUS=""
}

# --- Cleanup Trap ------------------------------------------------------------
cleanup() {
	local -r exit_code=$?
	if [[ -n "${CURRENT_STATUS}" ]]; then
		status_end 1
	fi
	if ((exit_code != 0 && exit_code != 130)); then
		log_error "Script failed (exit ${exit_code})."
		if [[ -f "${CACHE_FILE}" ]]; then
			log_warn "Partial download preserved at: ${CACHE_FILE}"
		fi
	fi
}
trap cleanup EXIT

# --- Dependency Verification -------------------------------------------------
check_deps() {
	local -a missing=()
	local dep
	for dep in curl unzip; do
		command -v "${dep}" &>/dev/null || missing+=("${dep}")
	done

	if ((${#missing[@]} > 0)); then
		log_error "Missing dependencies: ${missing[*]}"
		return 1
	fi
	return 0
}

# --- Download ----------------------------------------------------------------
download_archive() {
	if [[ -f "${CACHE_FILE}" ]]; then
		status_begin "Verifying existing cache"
		if unzip -tq "${CACHE_FILE}" &>/dev/null; then
			status_end 0
			log_ok "Valid archive found. Skipping download."
			return 0
		fi
		status_end 1
		log_warn "Existing cache is invalid. Re-downloading..."
		rm -f -- "${CACHE_FILE}"
	fi

	log_info "Downloading soundpacks (~30 MB)..."

	if ! curl -fL --retry 3 --retry-delay 5 --connect-timeout 30 \
		-o "${CACHE_FILE}" "${ZIP_URL}"; then
		log_error "Download failed."
		rm -f -- "${CACHE_FILE}"
		return 1
	fi
	log_ok "Download complete."

	status_begin "Verifying download integrity"
	if ! unzip -tq "${CACHE_FILE}" &>/dev/null; then
		status_end 1
		log_error "Download corrupted. Please check your connection."
		rm -f -- "${CACHE_FILE}"
		return 1
	fi
	status_end 0
	return 0
}

# --- Archive Extraction ------------------------------------------------------
extract_archive() {
	status_begin "Extracting soundpacks"
	if ! unzip -qo "${CACHE_FILE}" -d "${CACHE_DIR}"; then
		status_end 1
		log_error "Extraction failed."
		return 1
	fi
	status_end 0
	return 0
}

# --- Locate Extracted Directory ----------------------------------------------
find_extracted_root() {
	local -a candidates=()

	shopt -s nullglob
	candidates=("${CACHE_DIR}"/wayclick_soundpacks-*/)
	shopt -u nullglob

	if ((${#candidates[@]} == 0)); then
		log_error "Extracted folder not found in ${CACHE_DIR}."
		return 1
	fi
	printf '%s' "${candidates[0]%/}"
}

# --- Install Soundpacks ------------------------------------------------------
install_soundpacks() {
	local -r src="$1"
	local count=0

	log_info "Installing soundpacks..."

	shopt -s nullglob
	local -a soundpacks=("${src}"/*/)
	shopt -u nullglob

	if ((${#soundpacks[@]} == 0)); then
		log_error "No soundpacks found in archive."
		return 1
	fi

	for sp in "${soundpacks[@]}"; do
		local name
		name=$(basename "${sp}")
		if mv -T -- "${sp}" "${TARGET_DIR}/${name}"; then
			log_ok "Installed: ${name}"
			count=$((count + 1))
		else
			log_warn "Failed to install: ${name}"
		fi
	done

	if ((count == 0)); then
		log_error "No soundpacks were installed."
		return 1
	fi
	return 0
}

# --- Main Entry Point --------------------------------------------------------
main() {
	printf '%s:: Dusk Soundpack Installer%s\n' "${BOLD}" "${RST}"
	printf '   Download curated soundpack collection? (~30 MB)\n'

	if ((!AUTO_MODE)); then
		if [[ ! -t 0 ]]; then
			log_error "Interactive terminal required (use --auto for non-interactive mode)."
			return 1
		fi

		local response
		read -r -p "   [y/N] > " response
		case "${response,,}" in
		y | yes) ;;
		*)
			log_info "Aborted by user."
			return 0
			;;
		esac
	fi

	if ((AUTO_MODE && !FORCE_MODE)); then
		if [[ -f "${STATE_FILE}" ]]; then
			local state
			state=$(cat "${STATE_FILE}" | tr -d ' ')
			if [[ "${state}" == "1" ]]; then
				log_ok "Soundpacks already installed successfully. Skipping."
				return 0
			fi
		fi
	fi

	check_deps
	mkdir -p -- "${SETTINGS_DIR}" "${TARGET_DIR}" "${CACHE_DIR}"

	write_state() {
		printf '%s' "$1" >"${STATE_FILE}"
	}

	write_state "0"

	status_begin "Removing old soundpack directories"
	local -a old_packs=()
	shopt -s nullglob
	old_packs=("${TARGET_DIR}"/*/)
	shopt -u nullglob

	if ((${#old_packs[@]} > 0)); then
		if ! rm -rf -- "${TARGET_DIR}"/*/; then
			status_end 1
			log_error "Failed to remove old directories."
			return 1
		fi
	fi
	status_end 0

	download_archive
	extract_archive

	local extracted_root
	extracted_root=$(find_extracted_root)
	install_soundpacks "${extracted_root}"

	rm -rf -- "${CACHE_DIR}"

	write_state "1"

	log_ok "Installation complete."
	log_info "Location: ${TARGET_DIR/#"${HOME}"/\~}"
	return 0
}

main "$@"
