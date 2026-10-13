#!/usr/bin/env bash
#d: Install the Paru (or Yay) AUR helper

set -euo pipefail
shopt -s nullglob

# --- Configuration ---
readonly PARU_URL="https://aur.archlinux.org/paru.git"
readonly YAY_URL="https://aur.archlinux.org/yay.git"
# Bootstrap toolchains; makepkg --syncdeps resolves the current PKGBUILD
# dependencies. base-devel is a meta-package, not a package group.
# shellcheck disable=SC2034
readonly PARU_DEPS=("base-devel" "git" "rust")
# shellcheck disable=SC2034
readonly YAY_DEPS=("base-devel" "git" "go")
PACMAN_LOCK=""
readonly PACMAN_LOCK_TIMEOUT=300
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
            rm -rf -- "${BUILD_DIR}" || log_warn "Could not remove build directory: $BUILD_DIR"
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
Usage: ${0##*/} [OPTIONS]

Installs the Paru AUR helper (with automatic fallback to Yay), or Yay directly.

Options:
  -p, --paru    Install Paru (with fallback to Yay if Paru installation fails)
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
    [[ "$pw_uid" =~ ^[0-9]+$ ]] || return 1

    if [[ "$user" == "root" || "$user" == "nobody" ]]; then return 1; fi
    if (( pw_uid < 1000 || pw_uid == 65534 )); then return 1; fi
    if [[ "$user" == systemd-* ]]; then return 1; fi
    [[ -n "$pw_home" && -d "$pw_home" ]] || return 1
    [[ "$pw_shell" != *"nologin" && "$pw_shell" != *"false" ]] || return 1
    return 0
}

get_real_user() {
    local candidate
    # Honor the orchestrator and an explicit override before sudo identity.
    for candidate in "${TARGET_USER:-}" "${DUSKY_TARGET_USER:-}" "${DUSKY_USER:-}" "${SUDO_USER:-}" "${USER:-}"; do
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
    log_error "(Run this script via 'sudo ${0##*/} --paru' as that user.)"
    return 1
}

# --- Pacman Lock & Dependency Management ---
wait_for_pacman_lock() {
    local deadline=$((SECONDS + PACMAN_LOCK_TIMEOUT))
    if [[ -e "$PACMAN_LOCK" ]]; then
        log_info "Waiting for pacman lock: $PACMAN_LOCK"
    fi
    while [[ -e "$PACMAN_LOCK" ]]; do
        if (( SECONDS >= deadline )); then
            log_error "Timed out waiting for pacman lock: $PACMAN_LOCK"
            return 1
        fi
        sleep 2 || return 1
    done
    # Pacman acquires its own lock atomically; this wait does not reserve it.
}

# Collect known alternate linkers from the configuration files makepkg uses.
# Read text rather than executing user configuration as root. Ignore comments;
# complex computed flags are left to makepkg and the system-config retry.
collect_linker_deps() {
    local r_user="$1" pw_line build_home cfg line name
    pw_line=$(getent passwd "$r_user") || return 1
    IFS=: read -r _ _ _ _ _ build_home _ <<< "$pw_line"
    local -a cfg_files=(/etc/makepkg.conf /etc/makepkg.conf.d/*.conf)
    local user_cfg="${XDG_CONFIG_HOME:-$build_home/.config}/pacman/makepkg.conf"
    if [[ -r "$user_cfg" ]]; then
        cfg_files+=("$user_cfg")
    elif [[ -r "$build_home/.makepkg.conf" ]]; then
        cfg_files+=("$build_home/.makepkg.conf")
    fi
    local -A seen=()
    for cfg in "${cfg_files[@]}"; do
        [[ -r "$cfg" ]] || continue
        while IFS= read -r line || [[ -n "$line" ]]; do
            line=${line%%#*}
            while [[ "$line" =~ -fuse-ld=([A-Za-z0-9_.-]+) ]]; do
                name=${BASH_REMATCH[1]}
                line=${line#*"-fuse-ld=$name"}
                case "$name" in
                    mold|lld)
                        if [[ ! -v seen[$name] ]] && ! command -v "ld.$name" &>/dev/null; then
                            BUILD_DEPS+=("$name")
                            seen[$name]=1
                        fi
                        ;;
                    bfd|gold) ;;
                    *) log_warn "Configured linker '$name' is not managed by this installer." ;;
                esac
            done
        done < "$cfg"
    done
}

ensure_build_deps() {
    local -n deps_ref=$1
    local -a BUILD_DEPS=("${deps_ref[@]}")
    collect_linker_deps "$2" || return 1

    # One transaction for the toolchain and linkers; no bare -Sy partial upgrade.
    local dep_status=0
    pacman -T "${BUILD_DEPS[@]}" &>/dev/null || dep_status=$?
    if (( dep_status != 0 && dep_status != 127 )); then
        log_error "Pacman dependency check failed (exit $dep_status)."
        return "$dep_status"
    fi
    if (( dep_status == 127 )); then
        log_info "Installing build dependencies: ${BUILD_DEPS[*]}..."
        wait_for_pacman_lock || return 1
        if pacman -Syu --needed --noconfirm -- "${BUILD_DEPS[@]}"; then
            :
        else
            dep_status=$?
            log_error "Failed to install build dependencies."
            return "$dep_status"
        fi
    fi
    command -v git &>/dev/null || { log_error "git is missing."; return 1; }
    command -v makepkg &>/dev/null || { log_error "makepkg is missing."; return 1; }
}

helper_works() {
    command -v "$1" &>/dev/null && timeout --kill-after=2s 10s "$1" --version &>/dev/null
}

# --- Build Engine ---
build_helper() {
    local r_user="$1"
    local url="$2"
    local pkg_name="$3"

    log_info "Starting build for: $pkg_name"
    if [[ -n "$BUILD_DIR" ]]; then
        rm -rf -- "$BUILD_DIR" || return 1
        BUILD_DIR=""
    fi

    # Prefer disk-backed /var/tmp: Rust builds can exhaust a tmpfs /tmp.
    # --target also checks an inherited mount when the directory is not a mountpoint.
    local base_tmp mount_options
    for base_tmp in /var/tmp /tmp; do
        [[ -d "$base_tmp" ]] || continue
        mount_options=$(findmnt --target "$base_tmp" --noheadings --output OPTIONS) || continue
        [[ ",$mount_options," != *,noexec,* && ",$mount_options," != *,ro,* ]] || continue
        if BUILD_DIR=$(mktemp --directory --tmpdir="$base_tmp" aur-build-XXXXXXXX); then
            break
        fi
    done
    if [[ -z "$BUILD_DIR" ]]; then
        log_error "No writable, executable temporary build location is available."
        return 1
    fi

    local r_uid r_gid
    r_uid=$(id -u "$r_user") || return 1
    r_gid=$(id -g "$r_user") || return 1
    chown "$r_uid:$r_gid" "$BUILD_DIR" || return 1
    # mktemp permissions are affected by umask; guarantee user access.
    chmod 700 "$BUILD_DIR" || return 1

    # Build as the target user. Explicit output paths override makepkg.conf,
    # keep artifacts in our workspace, and avoid an external noexec BUILDDIR.
    local build_status
    if sudo -H -u "$r_user" --preserve-env=XDG_CONFIG_HOME -- bash -c '
        set -euo pipefail
        umask 022
        shopt -s nullglob
        unset CDPATH MAKEPKG_CONF
        cd -- "$1"
        for ((attempt = 1; attempt <= 3; attempt++)); do
            if git clone --depth 1 -- "$2" "$3"; then
                break
            else
                clone_status=$?
                case "$clone_status" in
                    130|143) exit "$clone_status" ;;
                esac
            fi
            # A failed clone can leave a partial checkout behind.
            rm -rf -- "$3"
            if (( attempt == 3 )); then
                printf "Failed to clone %s after three attempts.\n" "$2" >&2
                exit 1
            fi
            sleep 2
        done
        cd -- "$3"
        export PKGDEST="$PWD" BUILDDIR="$PWD" SRCDEST="$PWD"
        export SRCPKGDEST="$PWD" LOGDEST="$PWD"
        config=/etc/makepkg.conf
        if makepkg --config "$config" --syncdeps --noconfirm --clean --force; then
            :
        else
            build_status=$?
            case "$build_status" in
                130|143) exit "$build_status" ;;
            esac
            if [[ ! -r "${XDG_CONFIG_HOME:-$HOME/.config}/pacman/makepkg.conf" && ! -r "$HOME/.makepkg.conf" ]]; then
                exit 1
            fi
            printf "Build failed; retrying with system makepkg configuration.\n" >&2
            config="$1/system-makepkg.conf"
            # An alternate --config suppresses both user config locations.
            # Retain system drop-ins, including the Rust compiler settings.
            cat > "$config" << "CONFIG"
source /etc/makepkg.conf
for cfg in /etc/makepkg.conf.d/*.conf; do
    if [[ -r "$cfg" ]]; then
        source "$cfg"
    fi
done
CONFIG
            makepkg --config "$config" --syncdeps --noconfirm --clean --cleanbuild --force
        fi
        makepkg --config "$config" --packagelist > "$1/package-list"
    ' -- "$BUILD_DIR" "$url" "$pkg_name"; then
        :
    else
        build_status=$?
        log_error "Build or package discovery for $pkg_name failed."
        return "$build_status"
    fi

    # Use makepkg output and package metadata, not assumptions about versions,
    # compression extensions or split/debug package filenames.
    local main_pkg="" file archive_name
    local -a pkg_files=()
    mapfile -t pkg_files < "$BUILD_DIR/package-list" || return 1
    for file in "${pkg_files[@]}"; do
        [[ -f "$file" ]] || continue
        archive_name=$(pacman -Qpq -- "$file") || return 1
        [[ "$archive_name" == "$pkg_name" ]] || continue
        if [[ -n "$main_pkg" ]]; then
            log_error "Multiple archives claim to be $pkg_name."
            return 1
        fi
        main_pkg=$file
    done
    if [[ -z "$main_pkg" ]]; then
        log_error "Build finished but no archive for $pkg_name was found."
        return 1
    fi

    log_info "Installing ${main_pkg}..."
    wait_for_pacman_lock || return 1
    # No --overwrite: conflicts must fail loudly (handled by the fallback path),
    # never silently clobber files owned by other packages.
    if pacman -U --noconfirm -- "$main_pkg"; then
        :
    else
        build_status=$?
        log_error "Failed to install package ${main_pkg}."
        return "$build_status"
    fi

    # Flush bash lookup cache to guarantee immediate visibility
    hash -r

    # Confirm the helper really works
    if helper_works "$pkg_name"; then
        return 0
    fi

    log_error "Installed $main_pkg but '$pkg_name --version' still fails."
    return 1
}

# --- Install Targets ---
try_install_helper() {
    local r_user="$1" pkg_name="$2" url deps_name
    case "$pkg_name" in
        paru) url=$PARU_URL; deps_name=PARU_DEPS ;;
        yay) url=$YAY_URL; deps_name=YAY_DEPS ;;
        *) log_error "Unknown helper: $pkg_name"; return 1 ;;
    esac

    if helper_works "$pkg_name"; then
        log_success "$pkg_name is already installed and functional."
        return 0
    fi
    log_info "Attempting to install $pkg_name..."
    ensure_build_deps "$deps_name" "$r_user" || return $?
    build_helper "$r_user" "$url" "$pkg_name" || return $?
    log_success "$pkg_name successfully installed."
}

# --- Main Entrypoint ---
main() {
    local choice="" arg
    for arg in "$@"; do
        case "$arg" in
            -h|--help) show_help; return 0 ;;
            -p|--paru) choice="P" ;;
            -y|--yay) choice="y" ;;
            *) log_error "Unknown argument: $arg"; return 2 ;;
        esac
    done

    # Self-elevation preserving arguments and TARGET_USER
    if [[ $EUID -ne 0 ]]; then
        command -v sudo &>/dev/null || {
            log_error "sudo is not installed (minimal system). Install it first: pacman -S sudo"
            exit 1
        }
        log_info "Privilege escalation required. Elevating..."
        local script_path
        script_path=$(realpath -- "$0") || return 1
        exec sudo --preserve-env=TARGET_USER,DUSKY_TARGET_USER,DUSKY_USER,XDG_CONFIG_HOME -- bash "$script_path" "$@"
    fi

    command -v pacman &>/dev/null || { log_error "pacman not found. This script is Arch-only."; exit 1; }

    local cmd db_path
    for cmd in sudo getent timeout findmnt mktemp chown chmod id pacman-conf; do
        command -v "$cmd" &>/dev/null || { log_error "Required command is missing: $cmd"; return 1; }
    done
    db_path=$(pacman-conf DBPath) || return 1
    [[ -n "$db_path" ]] || { log_error "Pacman DBPath is empty."; return 1; }
    PACMAN_LOCK="${db_path%/}/db.lck"

    local r_user
    if ! r_user=$(get_real_user); then
        exit 1
    fi
    log_info "Target User: $r_user"

    # Prompt only in a terminal; automation selects a helper with flags.
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
            log_info "Non-interactive session detected. Defaulting to Paru."
            choice="P"
        fi
    fi

    local install_status
    if [[ "$choice" =~ ^[yY] ]]; then
        log_info "Selection: Yay"
        if try_install_helper "$r_user" yay; then
            return 0
        else
            install_status=$?
        fi
        log_error "Yay installation failed."
        return "$install_status"
    fi

    log_info "Selection: Paru (with fallback)"
    if try_install_helper "$r_user" paru; then
        return 0
    else
        install_status=$?
        case "$install_status" in
            130|143) return "$install_status" ;;
        esac
    fi
    log_warn "Paru installation failed; falling back to Yay."
    if try_install_helper "$r_user" yay; then
        log_success "Fallback complete: Yay installed successfully."
        return 0
    else
        install_status=$?
    fi
    log_error "Both Paru and Yay installations failed."
    return "$install_status"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
