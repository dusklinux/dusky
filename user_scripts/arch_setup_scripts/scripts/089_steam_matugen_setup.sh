#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: steam-matugen-setup.sh
# Description: Automated Steam theme matugen setup for Dusky Dotfiles.
#              Handles Decky Loader installation, CSS Loader setup, and
#              matugen color integration while respecting UWSM environments.
# Author: Dusky Dotfiles Automation
# License: MIT
# -----------------------------------------------------------------------------

# Strict Mode:
# -e: Exit on error
# -u: Exit on unset variable
# -o pipefail: Exit if any command in a pipe fails
# -E: Inherit ERR traps in functions
set -Eeuo pipefail

# --- Configuration ---
readonly SCRIPT_NAME="${0##*/}"
readonly REQUIRED_BASH_VERSION=5
# Temporary file tracker
declare -a TEMP_FILES=()

# --- Visual Feedback (ANSI C Quoting) ---
if [[ -t 1 ]]; then
	readonly COLOR_RESET=$'\033[0m'
	readonly COLOR_INFO=$'\033[1;34m'    # Blue
	readonly COLOR_SUCCESS=$'\033[1;32m' # Green
	readonly COLOR_WARN=$'\033[1;33m'    # Yellow
	readonly COLOR_ERR=$'\033[1;31m'     # Red
	readonly COLOR_BOLD=$'\033[1m'
else
	readonly COLOR_RESET=''
	readonly COLOR_INFO=''
	readonly COLOR_SUCCESS=''
	readonly COLOR_WARN=''
	readonly COLOR_ERR=''
	readonly COLOR_BOLD=''
fi

# --- Logging Functions ---
log_info() { printf '%s[INFO]%s %s\n' "${COLOR_INFO}" "${COLOR_RESET}" "$*"; }
log_success() { printf '%s[OK]%s %s\n' "${COLOR_SUCCESS}" "${COLOR_RESET}" "$*"; }
log_warn() { printf '%s[WARN]%s %s\n' "${COLOR_WARN}" "${COLOR_RESET}" "$*" >&2; }
log_err() { printf '%s[ERROR]%s %s\n' "${COLOR_ERR}" "${COLOR_RESET}" "$*" >&2; }
die() {
	log_err "$*"
	exit 1
}

# --- Cleanup Handler ---
cleanup() {
	local exit_code=$?
	trap - EXIT INT TERM

	# Remove temp files
	for file in "${TEMP_FILES[@]}"; do
		if [[ -f "$file" ]]; then
			rm -f "$file"
		fi
	done

	if [[ $exit_code -ne 0 ]]; then
		log_err "Script failed with exit code $exit_code"
	fi
	exit "$exit_code"
}
trap cleanup EXIT INT TERM

# --- Utility Functions ---
command_exists() {
	command -v "$1" >/dev/null 2>&1
}

# --- Main Logic ---

check_requirements() {
	# 1. Bash Version Check
	if ((BASH_VERSINFO[0] < REQUIRED_BASH_VERSION)); then
		die "Bash 5.0+ required. Current: $BASH_VERSION"
	fi

	# 2. Check for Steam
	if command -v steam &>/dev/null; then
		log_success "Steam binary detected."
	elif [[ -d "$HOME/.steam/steam" ]]; then
		log_success "Steam installation detected."
	else
		die "Steam is not installed! Please install Steam first."
	fi

	# 3. Check for matugen
	if command -v matugen &>/dev/null; then
		log_success "Matugen binary detected."
	else
		log_warn "Matugen not found. Install it first for color generation."
	fi
}

install_decky_loader() {
	log_info "Setting up Decky Loader..."

	# Check if ~/homebrew exists
	if [[ -d "$HOME/homebrew" ]]; then
		log_success "~/homebrew directory already exists. Decky Loader is likely already installed."
		return 0
	fi

	log_info "Installing Decky Loader using official installer..."

	if ! curl -fsSL https://github.com/SteamDeckHomebrew/decky-installer/releases/latest/download/install_release.sh | sh; then
		die "Decky Loader installation failed."
	fi

	log_success "Decky Loader installed successfully!"
}

install_css_loader() {
	log_info "Installing CSS Loader..."

	# Create plugins directory if it doesn't exist
	mkdir -p "$HOME/homebrew/plugins"

	# Check if CSS Loader plugin already exists
	if [[ -d "$HOME/homebrew/plugins/SDH-CssLoader" ]]; then
		log_success "SDH-CSSLoader plugin already exists. Skipping installation."
		return 0
	fi

	# Download and install SDH-CSSLoader plugin
	local css_zip="$HOME/SDH-CSSLoader-Decky.zip"
	log_info "Downloading SDH-CSSLoader..."

	if ! curl -fsSL "https://github.com/DeckThemes/SDH-CssLoader/releases/latest/download/SDH-CSSLoader-Decky.zip" -o "$css_zip"; then
		die "Failed to download SDH-CSSLoader"
	fi

	log_info "Extracting SDH-CSSLoader..."
	if ! unzip -q "$css_zip" -d "$HOME/"; then
		rm -f "$css_zip"
		die "Failed to extract SDH-CSSLoader"
	fi

	log_info "Moving SDH-CssLoader to plugins directory..."
	if ! sudo mv "$HOME/SDH-CssLoader" "$HOME/homebrew/plugins/"; then
		rm -f "$css_zip"
		die "Failed to move SDH-CssLoader to plugins directory"
	fi

	# Clean up the zip file
	rm -f "$css_zip"

	log_success "SDH-CSSLoader installed successfully!"
	log_info "Plugin location: ~/homebrew/plugins/SDH-CssLoader"
}

setup_matugen_themes() {
	local themes_dir="$HOME/homebrew/themes"
	mkdir -p "$themes_dir"

	log_info "Setting up Matugen-compatible themes..."

	# Check for existing matugen configuration
	local matugen_config_dir="$HOME/.config/matugen"
	local steam_colors_file="$matugen_config_dir/steam.css"

	if [[ -f "$steam_colors_file" ]]; then
		log_success "Matugen Steam CSS configuration detected."
		log_info "Skipping theme downloads to protect your generated colors."

		# Create symlink for CSS Loader if needed
		local css_loader_theme_dir="$HOME/homebrew/themes/Matugen"
		mkdir -p "$css_loader_theme_dir"

		if [[ ! -L "$css_loader_theme_dir/theme.css" ]]; then
			ln -sf "$steam_colors_file" "$css_loader_theme_dir/theme.css"
			log_success "Created symlink for Matugen colors in CSS Loader."
		fi
	else
		log_info "No Matugen Steam colors found. Installing default themes..."

		# Download Color Master theme
		if [[ ! -d "$themes_dir/Color Master" ]]; then
			local color_master_zip="162299bf-8027-43ee-ba02-a6cd3a79fb1b.zip"
			log_info "Downloading Color Master theme..."

			if ! wget -q "https://api.deckthemes.com/blobs/162299bf-8027-43ee-ba02-a6cd3a79fb1b" -O "$color_master_zip"; then
				log_warn "Failed to download Color Master theme."
			else
				log_info "Extracting Color Master theme..."
				if unzip -q "$color_master_zip" && [[ -d "Color Master" ]]; then
					mv "Color Master" "$themes_dir/"
					log_success "Color Master theme installed."
				fi
				rm -f "$color_master_zip"
			fi
		else
			log_success "Color Master theme already exists. Skipping download."
		fi

		# Download Desktop Colored Toggles theme
		if [[ ! -d "$themes_dir/Desktop Colored Toggles" ]]; then
			local desktop_toggles_zip="01923bd4-078c-4453-85c7-9e9d34156589.zip"
			log_info "Downloading Desktop Colored Toggles theme..."

			if ! wget -q "https://api.deckthemes.com/blobs/01923bd4-078c-4453-85c7-9e9d34156589" -O "$desktop_toggles_zip"; then
				log_warn "Failed to download Desktop Colored Toggles theme."
			else
				log_info "Extracting Desktop Colored Toggles theme..."
				if unzip -q "$desktop_toggles_zip" && [[ -d "Desktop Colored Toggles" ]]; then
					mv "Desktop Colored Toggles" "$themes_dir/"
					log_success "Desktop Colored Toggles theme installed."
				fi
				rm -f "$desktop_toggles_zip"
			fi
		else
			log_success "Desktop Colored Toggles theme already exists. Skipping download."
		fi

		log_info "Run 'matugen' to generate Steam colors for automatic theming."
	fi

	log_success "Theme setup complete!"
	log_info "Themes location: $themes_dir/"
}

configure_sudoers() {
	log_info "Configuring sudoers for plugin_loader..."

	local rule="ALL ALL=(ALL) NOPASSWD: /bin/systemctl restart plugin_loader"
	local sudoers_dropin="/etc/sudoers.d/decky-plugin-loader"

	# Check if rule already exists
	if sudo grep -Fq "$rule" "$sudoers_dropin" 2>/dev/null; then
		log_success "Sudoers rule already exists"
		return 0
	fi

	log_info "Adding sudoers rule..."

	echo "$rule" | sudo tee "$sudoers_dropin" >/dev/null

	# Fix permissions (sudo requires 0440)
	sudo chmod 0440 "$sudoers_dropin"

	# Validate sudoers config
	if ! sudo visudo -cf "$sudoers_dropin"; then
		sudo rm -f "$sudoers_dropin"
		die "Invalid sudoers configuration — rule reverted"
	fi

	log_success "Sudoers rule added successfully"

	# Restart plugin_loader if it exists
	if sudo systemctl restart plugin_loader 2>/dev/null; then
		log_success "Plugin loader service restarted"
	else
		log_warn "plugin_loader service not found or not running yet"
	fi
}

# --- Execution ---

main() {
	check_requirements
	install_decky_loader
	install_css_loader
	setup_matugen_themes
	configure_sudoers

	echo ""
	log_success "Steam Matugen setup complete!"
	log_info "If colors are missing, run 'matugen' to generate them."
	echo ""
	log_info "Installation Summary:"
	log_info "✅ Decky Loader: ~/homebrew/"
	log_info "✅ CSS Loader: ~/homebrew/plugins/SDH-CssLoader"
	log_info "✅ Themes: ~/homebrew/themes/"
	log_info "✅ Sudoers: Passwordless plugin_loader restart configured"
	echo ""
	log_info "🔄 Please restart Steam to see all changes in the Steam UI"
	log_info "🎨 Configure themes using the CSS Loader plugin in Decky Loader"
}

main "$@"
