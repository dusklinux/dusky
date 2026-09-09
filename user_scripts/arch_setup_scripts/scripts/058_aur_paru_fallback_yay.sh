#!/usr/bin/env bash
#d: Install the Paru (or Yay) AUR helper

set -euo pipefail
shopt -s nullglob

# --- Configuration ---
readonly PARU_URL="https://aur.archlinux.org/paru.git"
readonly YAY_URL="https://aur.archlinux.org/yay.git"
# NOTE (Sep 2026): `rust` provides `cargo` (no standalone cargo pkg);
# paru MakeDepends=cargo, yay MakeDepends=go>=1.24. base-devel is a
# meta-package (not a group) and pulls sudo/fakeroot/makepkg toolchain.
# shellcheck disable=SC2034
readonly PARU_DEPS=("base-devel" "git" "rust")
# shellcheck disable=SC2034
readonly YAY_DEPS=("base-devel" "git" "go")
readonly PACMAN_DB="/var/lib/pacman/local"
readonly PACMAN_LOCK="/var/lib/pacman/db.lck"
readonly PACMAN_LOCK_TIMEOUT=300
readonly VALID_PKG_RE='^[a-z0-9@._+-]+$'
readonly VALID_USER_RE='^[a-zA-Z0-9_.][a-zA-Z0-9_.-]*\$?$'

# --- Formatting & Logs ---
if [[ -t 1 ]]; then
    readonly BLUE=$'\033[0;34m'
    readonly GREEN=$'\033[0;32m'
    readonly NC=$'\033[0m'
else
    readonly BLUE="" GREEN="" NC=""
fi

if [[ -t 2 ]]; then
    readonly YELLOW=$'\033[1;33m'
    readonly RED=$'\033[0;31m'
    readonly ERR_NC=$'\033[0m'
else
    readonly YELLOW="" RED="" ERR_NC=""
fi

log_info()    { printf "%s[INFO]%s %s\n" "${BLUE}" "${NC}" "$*"; }
log_success() { printf "%s[SUCCESS]%s %s\n" "${GREEN}" "${NC}" "$*"; }
log_warn()    { printf "%s[WARN]%s %s\n" "${YELLOW}" "${ERR_NC}" "$*" >&2; }
log_error()   { printf "%s[ERROR]%s %s\n" "${RED}" "${ERR_NC}" "$*" >&2; }

# --- Cleanup ---
BUILD_DIR=""
# shellcheck disable=SC2329
cleanup() {
    local exit_code=$?

    # Clean build dir (only under /tmp or /var/tmp to avoid catastrophic deletes)
    if [[ -n "${BUILD_DIR:-}" && -d "${BUILD_DIR}" ]]; then
        if [[ "$BUILD_DIR" == /tmp/* || "$BUILD_DIR" == /var/tmp/* ]]; then
            log_info "Cleaning up temporary build context..."
            rm -rf -- "${BUILD_DIR}"
        fi
    fi

    if [[ $exit_code -ne 0 ]]; then
        log_warn "Script exited with code $exit_code"
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- Usage & Help ---
show_help() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS]

Installs the Paru AUR helper (with automatic fallback to Yay), or Yay directly.

Options:
  -p, --paru    Install Paru (with fallback to Yay if Paru build fails)
  -y, --yay     Install Yay directly
  -h, --help    Show this help message and exit

If no option is provided, the script runs interactively in a terminal,
or defaults to Paru in non-interactive/automated sessions.
EOF
}

# --- User Resolution ---
_is_eligible_user() {
    local user="$1"
    local pw_line pw_uid pw_home pw_shell
    pw_line=$(getent passwd "$user" 2>/dev/null) || return 1
    [[ -n "$pw_line" ]] || return 1

    IFS=: read -r _ _ pw_uid _ _ pw_home pw_shell _ <<< "$pw_line"
    [[ -n "$pw_uid" ]] || return 1

    if [[ "$user" == "root" || "$user" == "nobody" ]]; then return 1; fi
    if (( pw_uid < 1000 )); then return 1; fi
    if [[ "$user" == systemd-* ]]; then return 1; fi
    [[ -n "$pw_home" && -d "$pw_home" ]] || return 1
    [[ "$pw_shell" != *"nologin" && "$pw_shell" != *"false" ]] || return 1
    return 0
}

get_real_user() {
    local candidate
    # Orchestrator/sudo propagate TARGET_USER or SUDO_USER; fall back to $USER.
    for candidate in "${TARGET_USER:-}" "${SUDO_USER:-}" "${USER:-}"; do
        candidate="${candidate%% *}"
        if [[ -n "$candidate" && "$candidate" =~ $VALID_USER_RE ]] && _is_eligible_user "$candidate"; then
            echo "$candidate"
            return 0
        fi
    done

    # Last resort: first eligible UID>=1000 user (minimal installs, arch-chroot).
    local pw_user
    while IFS=: read -r pw_user _ _ _ _ _ _ || [[ -n "$pw_user" ]]; do
        if [[ "$pw_user" =~ $VALID_USER_RE ]] && _is_eligible_user "$pw_user"; then
            echo "$pw_user"
            return 0
        fi
    done < /etc/passwd

    log_error "No non-root user found. Create one first: useradd -m -G wheel <user> && passwd <user>"
    log_error "(Run this script via 'sudo $(basename "$0") --paru' as that user.)"
    return 1
}

# --- Pacman Lock & Dependency Management ---
wait_for_pacman_lock() {
    local waited=0
    while [[ -e "$PACMAN_LOCK" ]] && (( waited < PACMAN_LOCK_TIMEOUT )); do
        local is_active=0
        if command -v fuser &>/dev/null; then
            if fuser "$PACMAN_LOCK" &>/dev/null; then
                is_active=1
            fi
        elif command -v pgrep &>/dev/null; then
            if pgrep -x pacman &>/dev/null || pgrep -x paru &>/dev/null || pgrep -x yay &>/dev/null; then
                is_active=1
            fi
        fi

        if (( is_active == 0 )); then
            # Brief pause to verify lock wasn't caught in a brief creation race
            sleep 1
            if command -v fuser &>/dev/null; then
                fuser "$PACMAN_LOCK" &>/dev/null && is_active=1
            elif command -v pgrep &>/dev/null; then
                (pgrep -x pacman &>/dev/null || pgrep -x paru &>/dev/null || pgrep -x yay &>/dev/null) && is_active=1
            fi

            if (( is_active == 0 )) && [[ -e "$PACMAN_LOCK" ]]; then
                log_warn "Removing stale pacman lock: $PACMAN_LOCK"
                if rm -f -- "$PACMAN_LOCK" 2>/dev/null; then
                    break
                fi
                log_error "Cannot remove stale lock (insufficient privileges?): $PACMAN_LOCK"
                return 1
            fi
        fi

        sleep 2
        (( waited += 2 )) || true
    done

    if [[ -e "$PACMAN_LOCK" ]]; then
        log_error "Timed out waiting for pacman lock: $PACMAN_LOCK"
        return 1
    fi
    return 0
}

# Map an alternate linker (requested via -fuse-ld=<name> in makepkg configs)
# to the package that provides it. binutils linkers (bfd/gold) are already
# covered by base-devel.
_linker_pkg() {
    case "$1" in
        mold) printf 'mold' ;;
        lld) printf 'lld' ;;
        *) return 1 ;;
    esac
}

# Install any alternate linker demanded by makepkg configs that is not yet
# available. Only runs on the build path (callers short-circuit when the
# helper is already installed), so a healthy system is never touched.
ensure_configured_linker() {
    local r_user="${1:-}"
    local home=""
    if [[ -n "$r_user" ]]; then
        home=$(getent passwd "$r_user" 2>/dev/null | cut -d: -f6) || home=""
    fi

    local cfg_files=(/etc/makepkg.conf /etc/makepkg.conf.d/*.conf)
    if [[ -n "$home" ]]; then
        cfg_files+=("$home/.config/pacman/makepkg.conf")
    fi
    if (( ${#cfg_files[@]} == 0 )); then
        return 0
    fi

    local names=""
    names=$(grep -rhoE -- '-fuse-ld=[A-Za-z0-9_.-]+' "${cfg_files[@]}" 2>/dev/null | sed 's/.*=//' | sort -u) || names=""
    [[ -n "$names" ]] || return 0

    local missing_pkgs=()
    local name pkg
    while IFS= read -r name; do
        [[ -n "$name" ]] || continue
        # A manually installed linker is fine too; only missing ones need pacman.
        if command -v "ld.$name" &>/dev/null || command -v "$name" &>/dev/null; then
            continue
        fi
        if pkg=$(_linker_pkg "$name"); then
            if ! pacman -Qq "$pkg" &>/dev/null; then
                missing_pkgs+=("$pkg")
            fi
        else
            log_warn "makepkg configs request unknown linker '$name'; hoping it exists at build time."
        fi
    done <<< "$names"

    if (( ${#missing_pkgs[@]} == 0 )); then
        return 0
    fi

    log_info "Installing linkers required by makepkg configs: ${missing_pkgs[*]}..."
    wait_for_pacman_lock || return 1
    # -Syu (never bare -Sy) so a stale sync DB cannot partial-upgrade.
    if ! pacman -Syu --needed --noconfirm -- "${missing_pkgs[@]}"; then
        log_error "Failed to install linkers: ${missing_pkgs[*]}"
        return 1
    fi
    return 0
}

ensure_build_deps() {
    # $1 = name of deps array (nameref), $2 = build username.
    local -n _deps_ref=$1
    local _r_user="${2:-}"

    # Alternate linkers demanded by makepkg configs (e.g. mold) must exist
    # before anything tries to link. No-op when configs want stock ld.
    ensure_configured_linker "$_r_user" || return 1

    # If all dependencies are already satisfied, avoid touching the pacman DB.
    if pacman -T "${_deps_ref[@]}" &>/dev/null; then
        return 0
    fi

    log_info "Installing missing build dependencies: ${_deps_ref[*]}..."
    wait_for_pacman_lock || return 1
    # Uses -Syu (never bare -Sy) so a fresh minimal install cannot partial-upgrade.
    if ! pacman -Syu --needed --noconfirm -- "${_deps_ref[@]}"; then
        log_error "Failed to install build dependencies: ${_deps_ref[*]}"
        return 1
    fi

    command -v git &>/dev/null || { log_error "git still missing after dependency install."; return 1; }
    command -v makepkg &>/dev/null || { log_error "makepkg still missing (pacman package broken?)."; return 1; }
    return 0
}

# --- Ghost Package Sanitizer ---
sanitize_target() {
    local target="${1:-}"

    # Validate: never allow empty/absolute/path-traversal targets here.
    if [[ ! "$target" =~ $VALID_PKG_RE ]]; then
        log_error "Refusing to sanitize invalid package name: '${target}'"
        return 1
    fi

    # 1. Check if binary works
    if command -v "$target" &>/dev/null; then
        if timeout 10 "$target" --version &>/dev/null; then
            return 0 # Healthy
        else
            log_warn "Binary '$target' exists but is SEGFAULTING/BROKEN."
        fi
    fi

    # 2. Check for shadowed broken binaries in /usr/local/bin
    if [[ -f "/usr/local/bin/$target" ]] && ! timeout 10 "/usr/local/bin/$target" --version &>/dev/null; then
        log_warn "Removing broken unmanaged binary at /usr/local/bin/$target"
        rm -f -- "/usr/local/bin/$target" 2>/dev/null || true
    fi

    # 3. Check Pacman DB for the EXACT package only.
    # Entries are "$pkgname-$pkgver-$pkgrel", so require the dash: this
    # matches `paru-2.1.0-2` but never `paru-bin` / `paru-git`.
    local -a db_entries=("$PACMAN_DB/$target-"*/)
    local -a remaining_entries=()

    if [[ ${#db_entries[@]} -gt 0 ]]; then
        log_warn "Ghost package detected in Pacman DB: $target"

        # Serialize with any live pacman run first
        wait_for_pacman_lock || true

        if pacman -Qq "$target" &>/dev/null; then
             pacman -Rns --noconfirm -- "$target" || true
        fi

        # NUCLEAR OPTION (scoped): only exact "$target-*" leftovers, re-globbed
        # after the pacman removal above. Never touches sibling packages.
        remaining_entries=("$PACMAN_DB/$target-"*/)
        local entry
        for entry in "${remaining_entries[@]}"; do
            if [[ -d "$entry" ]]; then
                log_warn "Force removing corrupted DB entry: $entry"
                rm -rf -- "$entry"
            fi
        done
    fi

    return 1
}

# --- Build Engine ---
build_helper() {
    local r_user="$1"
    local url="$2"
    local pkg_name="$3"

    if [[ ! "$pkg_name" =~ $VALID_PKG_RE ]]; then
        log_error "Invalid package name: '$pkg_name'"
        return 1
    fi

    log_info "Starting build for: $pkg_name"

    # Drop any previous build dir (paru->yay fallback reuses this function).
    if [[ -n "${BUILD_DIR:-}" && -d "${BUILD_DIR}" && ( "$BUILD_DIR" == /tmp/* || "$BUILD_DIR" == /var/tmp/* ) ]]; then
        rm -rf -- "${BUILD_DIR}"
    fi

    # Respect noexec on /tmp (hardened setups) by falling back to /var/tmp
    local base_tmp="/tmp"
    if findmnt -no OPTIONS /tmp 2>/dev/null | grep -qw noexec; then
        base_tmp="/var/tmp"
    fi

    BUILD_DIR=$(mktemp -d -p "$base_tmp" aur-build-XXXXXXXX) || {
        log_error "Failed to create temporary build directory."
        return 1
    }
    [[ -n "$BUILD_DIR" && -d "$BUILD_DIR" ]] || {
        log_error "Build directory is invalid: '$BUILD_DIR'"
        return 1
    }

    local r_group
    r_group=$(id -gn "$r_user" 2>/dev/null) || r_group="$r_user"
    chown -R "$r_user:$r_group" "$BUILD_DIR" || {
        log_error "Failed to set ownership on $BUILD_DIR"
        return 1
    }
    chmod 700 "$BUILD_DIR" || {
        log_error "Failed to set permissions on $BUILD_DIR"
        return 1
    }

    # -s/--syncdeps covers future makedepends drift (no-op when pre-installed).
    # Export PKGDEST to ensure artifacts stay in the build folder regardless of user/system makepkg.conf.
    # Attempt 1 honors the user's makepkg.conf (its linker demands were already
    # installed by ensure_configured_linker). Attempt 2 neutralizes per-user
    # makepkg configs via an empty XDG_CONFIG_HOME (stock /etc/makepkg.conf),
    # so a broken custom config can never kill the install.
    # (cargo/go read HOME-based paths, so they are unaffected either way.)
    # Never run makepkg as root: AUR builds must run as the unprivileged user.
    if ! sudo -H -u "$r_user" bash -c '
        set -euo pipefail
        # NOTE: callers must pass args explicitly (clone_pkg "$1" "$2" "$3"):
        # a function called with no arguments sees EMPTY positionals, it does
        # NOT inherit the caller positionals.
        clone_pkg() {
            local _dest="${1:?clone_pkg: missing dest}" _repo="${2:?clone_pkg: missing repo}" _dir="${3:?clone_pkg: missing dir}"
            local tries=0
            while (( ++tries <= 3 )); do
                if git clone --depth 1 "$_repo" "$_dir"; then
                    return 0
                fi
                if (( tries == 3 )); then
                    echo "Failed to clone repository $_repo after 3 attempts." >&2
                    return 1
                fi
                sleep 2
            done
        }
        cd "$1" || exit 1
        clone_pkg "$1" "$2" "$3" || exit 1
        cd "$3" || exit 1
        export PKGDEST="$PWD"
        if makepkg -s --noconfirm -cf; then
            exit 0
        fi
        echo "Build with user makepkg.conf failed; retrying with stock /etc/makepkg.conf..." >&2
        cd "$1" || exit 1
        rm -rf -- "$3"
        clone_pkg "$1" "$2" "$3" || exit 1
        cd "$3" || exit 1
        export PKGDEST="$PWD"
        export XDG_CONFIG_HOME="$PWD/.xdg-empty"
        mkdir -p "$XDG_CONFIG_HOME"
        makepkg -s --noconfirm -cf || exit 1
    ' -- "$BUILD_DIR" "$url" "$pkg_name"; then
        log_error "Compilation of $pkg_name failed."
        return 1
    fi

    log_info "Locating package archive..."
    local -a pkg_files=("$BUILD_DIR/$pkg_name"/*.pkg.tar.*)
    local main_pkg=""

    # makepkg.conf enables `debug` by default, emitting a `-debug` split package.
    # Match only the primary package archive, ignoring debug archives and .sig signatures.
    local f
    for f in "${pkg_files[@]}"; do
        if [[ "$f" =~ ^.*/${pkg_name}-[0-9].*\.pkg\.tar\.[a-z0-9]+$ && "$f" != *-debug-* ]]; then
            main_pkg="$f"
            break
        fi
    done

    if [[ -z "$main_pkg" || ! -f "$main_pkg" ]]; then
        log_error "Build finished but no valid package archive (.pkg.tar.*) found."
        return 1
    fi

    log_info "Installing ${main_pkg}..."
    wait_for_pacman_lock || return 1
    # No --overwrite: conflicts must fail loudly (handled by the fallback path),
    # never silently clobber files owned by other packages.
    if ! pacman -U --needed --noconfirm -- "$main_pkg"; then
        log_error "Failed to install package ${main_pkg}."
        return 1
    fi

    # Flush bash lookup cache to guarantee immediate visibility
    hash -r 2>/dev/null || true

    # Confirm the helper really works
    if command -v "$pkg_name" &>/dev/null && timeout 10 "$pkg_name" --version &>/dev/null; then
        return 0
    fi

    log_error "Installed $main_pkg but '$pkg_name --version' still fails."
    return 1
}

# --- Install Targets ---
try_install_paru() {
    local r_user="$1"

    if sanitize_target "paru"; then
        log_success "Paru is already installed and functional."
        return 0
    fi

    log_info "Attempting to install Paru..."
    ensure_build_deps PARU_DEPS "$r_user" || return 1

    if build_helper "$r_user" "$PARU_URL" "paru"; then
        log_success "Paru successfully installed."
        return 0
    fi

    return 1
}

try_install_yay() {
    local r_user="$1"

    if sanitize_target "yay"; then
        log_success "Yay is already installed and functional."
        return 0
    fi

    log_info "Attempting to install Yay..."
    ensure_build_deps YAY_DEPS "$r_user" || return 1

    if build_helper "$r_user" "$YAY_URL" "yay"; then
        log_success "Yay successfully installed."
        return 0
    fi

    return 1
}

# --- Main Entrypoint ---
main() {
    # Check help flags BEFORE privilege escalation so users never need sudo just for --help
    for arg in "$@"; do
        case "$arg" in
            -h|--help)
                show_help
                exit 0
                ;;
        esac
    done

    # Self-elevation preserving arguments and TARGET_USER
    if [[ $EUID -ne 0 ]]; then
        command -v sudo &>/dev/null || {
            log_error "sudo is not installed (minimal system). Install it first: pacman -S sudo"
            exit 1
        }
        log_info "Privilege escalation required. Elevating..."
        exec sudo --preserve-env=TARGET_USER -- "$(realpath "$0")" "$@"
    fi

    command -v pacman &>/dev/null || { log_error "pacman not found. This script is Arch-only."; exit 1; }

    local r_user
    if ! r_user=$(get_real_user); then
        exit 1
    fi
    log_info "Target User: $r_user"

    # --- Argument Parsing & Interactive Prompt ---
    local choice=""

    # 1. Parse Flags
    for arg in "$@"; do
        case "$arg" in
            -y|--yay)
                choice="y"
                log_info "Autonomous mode: Force Yay"
                ;;
            -p|--paru)
                choice="P"
                log_info "Autonomous mode: Force Paru"
                ;;
            *)
                # Silently ignore unknown flags or parameters
                ;;
        esac
    done

    # 2. Interactive & Non-interactive Fallback (Only if no explicit flags set)
    if [[ -z "$choice" ]]; then
        if [[ -t 0 ]]; then
            echo ""
            log_info "Select AUR Helper to install:"
            printf "  %s[P]%saru (Default) - Rust-based, feature-rich, recommended.\n" "${GREEN}" "${NC}"
            printf "  %s[y]%say            - Go-based, classic, reliable.\n" "${YELLOW}" "${NC}"

            local user_input=""
            read -r -t 30 -p "Enter selection [P/y]: " user_input || true
            choice="${user_input:-P}"
        else
            # Non-interactive session: try to read piped selection; if none, default to Paru
            local user_input=""
            if read -r -t 1 user_input; then
                choice="${user_input:-P}"
                # Never log the value: stdin may carry a piped sudo password.
                log_info "Read selection from input stream."
            else
                log_info "Non-interactive session detected. Defaulting to Paru."
                choice="P"
            fi
        fi
    fi

    if [[ "$choice" =~ ^[yY] ]]; then
        # ---------------------------------------------------------
        # Path A: User specifically requested Yay
        # ---------------------------------------------------------
        log_info "Selection: Yay"
        if try_install_yay "$r_user"; then
            exit 0
        else
            log_error "Yay installation failed."
            exit 1
        fi
    else
        # ---------------------------------------------------------
        # Path B: User selected Paru (or Default) -> Fallback to Yay
        # ---------------------------------------------------------
        log_info "Selection: Paru (with fallback)"

        if try_install_paru "$r_user"; then
            exit 0
        fi

        # PARU FAILED - TRIGGER FALLBACK
        log_error "Paru installation failed."
        log_info ">>> INITIATING FALLBACK PROTOCOL: YAY <<<"

        # Clean up any partial Paru mess before starting Yay
        sanitize_target "paru" || true

        if try_install_yay "$r_user"; then
            log_success "Fallback Complete: Yay installed successfully."
            exit 0
        else
            log_error "CRITICAL FAILURE: Both Paru and Yay failed to build."
            exit 1
        fi
    fi
}

main "$@"
