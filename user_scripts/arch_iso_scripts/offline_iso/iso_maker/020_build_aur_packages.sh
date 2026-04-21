#!/usr/bin/env bash
# ==============================================================================
# 011_build_aur_packages.sh  —  v1.5  (AUR Factory Builder w/ Smart Deduplication)
#
# Factory script: Builds AUR packages into an offline pacman repository for
# use in an offline Arch Linux ISO. Does NOT install the built packages onto
# the host machine.
#
# Flow per package:
#   1. Query AUR RPC for latest version  →  skip if already in repo (idempotent)
#   2. paru -G  →  fetch PKGBUILD to a temp directory
#   3. PKGDEST="$OFFLINE_REPO_DIR" paru -B  →  build, output pkg, no install
#   4. Parse .PKGINFO of built pkg  →  extract runtime deps
#   5. sudo pacman (isolated sandbox)  →  download official runtime deps
#                                         (pacman -Sw resolves full closure)
#   6. repo-add  →  update local pacman repository database
#
# Usage:
#   ./011_build_aur_packages.sh [--auto | --current | --path <dir> | --official-path <dir>]
#
# Requirements:
#   • Run as NON-ROOT user with sudo access
#   • paru installed in PATH  (yay is NOT supported — paru -G/B flags required)
#   • curl, python3, bsdtar, repo-add, pacman, git, timeout in PATH
#   • Internet access (for AUR RPC, PKGBUILD cloning, source tarballs)
# ==============================================================================

set -Eeuo pipefail
shopt -s inherit_errexit

# ==============================================================================
# SECTION 1 — AUR PACKAGE LIST
# ==============================================================================

declare -a AUR_PACKAGES=(
    'wlogout'
    'adwaita-qt6'
    'adwaita-qt5'
    'adwsteamgtk'
    'otf-atkinson-hyperlegible-next'
    'python-pywalfox'
    'hyprshade'
    'hyprshutdown'
    'waypaper'
    'peaclock'
    'tray-tui'
    'wifitui-bin'
    'xdg-terminal-exec'
    'paru'
    'limine-mkinitcpio-hook'
    'limine-snapper-sync'
)

# ==============================================================================
# SECTION 2 — CONFIGURATION
# ==============================================================================

readonly REPO_NAME='archrepo'

# PID-suffixed path prevents sandbox collision if two instances run concurrently.
readonly ISOLATED_DB_DIR="/tmp/aur_factory_isolated_db_$$"

readonly AUR_RPC_BASE_URL='https://aur.archlinux.org/rpc/v5/info'

# Per-build timeout for paru -B (seconds).  3600 = 1 hour.
# Increase this value if any of your packages have very long compile times.
readonly BUILD_TIMEOUT_SEC=3600

# Retry policy
declare -ir MAX_ATTEMPTS=6
declare -ir TIMEOUT_SEC=5

declare -g  OFFLINE_REPO_DIR=''
declare -g  OFFICIAL_REPO_DIR='/srv/offline-repo/official'
declare -g  INTERACTIVE_MODE=1
declare -g  CLONE_BASE_DIR=''

# Signals whether _build_aur_package skipped vs actually built.
# Both cases return exit code 0 from _build_aur_package.
declare -gi _LAST_PKG_SKIPPED=0

# ==============================================================================
# SECTION 3 — COLORS & LOGGING
# ==============================================================================

_setup_colors() {
    BOLD='' GREEN='' YELLOW='' RED='' CYAN='' MAGENTA='' DIM='' RESET=''
    if [[ -z "${NO_COLOR-}" ]] && [[ -t 1 ]] && command -v tput &>/dev/null; then
        BOLD=$(tput bold)
        GREEN=$(tput setaf 2)
        YELLOW=$(tput setaf 3)
        RED=$(tput setaf 1)
        CYAN=$(tput setaf 6)
        MAGENTA=$(tput setaf 5)
        DIM=$(tput dim 2>/dev/null || true)
        RESET=$(tput sgr0)
    fi
    readonly BOLD GREEN YELLOW RED CYAN MAGENTA DIM RESET
}
_setup_colors

log_info()  { printf '\n%s==>%s %s\n'     "${BOLD}${CYAN}"    "${RESET}" "$*";     }
log_step()  { printf '  %s->%s %s\n'     "${BOLD}${MAGENTA}" "${RESET}" "$*";     }
log_ok()    { printf '%s[OK]%s %s\n'     "${BOLD}${GREEN}"   "${RESET}" "$*";     }
log_warn()  { printf '%s[!!]%s %s\n'     "${BOLD}${YELLOW}"  "${RESET}" "$*" >&2; }
log_err()   { printf '%s[XX]%s %s\n'     "${BOLD}${RED}"     "${RESET}" "$*" >&2; }
log_task()  { printf '\n%s:: %s%s\n'     "${BOLD}${CYAN}"    "$*" "${RESET}";     }
log_skip()  { printf '  %s[SKIP]%s %s\n' "${DIM}"            "${RESET}" "$*";     }
die()       { log_err "$*"; exit 1; }

# ==============================================================================
# SECTION 4 — TEMP REGISTRY & TRAPS
# ==============================================================================

declare -ga _TEMP_PATHS=()
_register_temp() { _TEMP_PATHS+=("$1"); }

_cleanup_done=0
_cleanup() {
    local rc=$?
    (( _cleanup_done )) && return 0
    _cleanup_done=1

    for p in "${_TEMP_PATHS[@]+"${_TEMP_PATHS[@]}"}"; do
        [[ -e "$p" || -L "$p" ]] && rm -rf -- "$p"
    done
    # ISOLATED_DB_DIR is user-owned (created by non-root); files inside that
    # are written by sudo pacman are still removable because deletion requires
    # write permission on the parent directory, not on the file itself.
    [[ -d "${ISOLATED_DB_DIR}" ]] && rm -rf -- "${ISOLATED_DB_DIR}" 2>/dev/null || true

    (( rc != 0 )) && log_err "Script exited with error status ${rc}."
    return 0
}

_on_err() {
    local rc=$?
    log_err "Fatal error on line ${1:-?}: command '${2:-?}' returned ${rc}."
}

trap '_on_err "$LINENO" "$BASH_COMMAND"' ERR
trap '_cleanup' EXIT

# ==============================================================================
# SECTION 5 — ARGUMENT PARSING & INTERACTIVE PROMPT
# ==============================================================================

_print_logo() {
    printf '\n%s' "${BOLD}${CYAN}"
    printf '╔══════════════════════════════════════════════════════════════╗\n'
    printf '║       AUR Package Builder for Offline ISO  (Factory)         ║\n'
    printf '╚══════════════════════════════════════════════════════════════╝\n'
    printf '%s\n' "${RESET}"
}

_parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --auto)
                OFFLINE_REPO_DIR='/srv/offline-repo/aur'
                OFFICIAL_REPO_DIR='/srv/offline-repo/official'
                INTERACTIVE_MODE=0
                shift
                ;;
            --current)
                OFFLINE_REPO_DIR="$(pwd)"
                INTERACTIVE_MODE=0
                shift
                ;;
            --path)
                [[ -z "${2-}" ]] && die "--path requires a directory argument."
                OFFLINE_REPO_DIR="$2"
                INTERACTIVE_MODE=0
                shift 2
                ;;
            --official-path)
                [[ -z "${2-}" ]] && die "--official-path requires a directory argument."
                OFFICIAL_REPO_DIR="$2"
                shift 2
                ;;
            *)
                die "Unknown argument: '$1'"$'\n'"Usage: $0 [--auto | --current | --path <dir> | --official-path <dir>]"
                ;;
        esac
    done
}

_prompt_repo_dir() {
    (( INTERACTIVE_MODE )) || return 0

    printf '\n%s==>%s %sSelect Offline Repository Target Location%s\n' \
        "${BOLD}${CYAN}" "${RESET}" "${BOLD}" "${RESET}"
    printf '  1) System Default  (/srv/offline-repo/aur)\n'
    printf '  2) Current working directory  (%s)\n' "$(pwd)"
    printf '  3) Custom absolute path\n\n'

    local choice
    while true; do
        read -r -p "  Enter choice [1-3] (default=1): " choice
        choice="${choice:-1}"
        case "$choice" in
            1) OFFLINE_REPO_DIR='/srv/offline-repo/aur'; break ;;
            2) OFFLINE_REPO_DIR="$(pwd)";             break ;;
            3)
                read -r -p "  Enter absolute path: " OFFLINE_REPO_DIR
                [[ -n "$OFFLINE_REPO_DIR" ]] && break
                ;;
            *) printf '  %sInvalid choice.%s\n' "${RED}" "${RESET}" ;;
        esac
    done
}

# ==============================================================================
# SECTION 6 — PRE-FLIGHT CHECKS
# ==============================================================================

_check_not_root() {
    if (( EUID == 0 )); then
        log_err "This script must NOT be run as root."
        log_err "paru and makepkg refuse to run as root — this is intentional."
        log_err "Run as a normal user with sudo access."
        log_err "sudo will be invoked automatically for pacman operations only."
        exit 1
    fi
}

_check_sudo_access() {
    log_step "Verifying sudo access..."
    # Cache credentials upfront to avoid mid-build password prompts.
    if ! sudo -n true 2>/dev/null; then
        log_warn "sudo credentials not cached. Please enter your password once:"
        sudo true || die "sudo access is required. Cannot continue."
    fi
    log_ok "sudo access confirmed."
}

_check_paru() {
    log_step "Checking for paru..."
    if command -v paru &>/dev/null; then
        log_ok "paru found: $(command -v paru)"
    else
        die "paru not found. This script requires paru (not yay)."$'\n'"Install paru from the AUR, then re-run this script."
    fi
}

_check_dependencies() {
    log_info "Checking required tools"
    local -a required=(curl python3 bsdtar repo-add pacman git timeout)
    local tool
    local -i missing=0

    for tool in "${required[@]}"; do
        if command -v "$tool" &>/dev/null; then
            log_step "${tool}: $(command -v "$tool")"
        else
            log_err "Required tool missing: '${tool}'"
            missing=$(( missing + 1 ))
        fi
    done

    (( missing > 0 )) && die "${missing} required tool(s) missing. Cannot continue."
    [[ -r /etc/arch-release ]] || die "Not running on Arch Linux."
    log_ok "All required tools are present."
}

_setup_dirs() {
    log_info "Setting up build environment"

    # Create OFFLINE_REPO_DIR if needed, using sudo if the parent is restricted.
    if [[ ! -d "$OFFLINE_REPO_DIR" ]]; then
        log_step "Creating: ${OFFLINE_REPO_DIR}"
        mkdir -p -- "$OFFLINE_REPO_DIR" 2>/dev/null \
            || sudo mkdir -p -- "$OFFLINE_REPO_DIR" \
            || die "Cannot create OFFLINE_REPO_DIR: ${OFFLINE_REPO_DIR}"
    fi

    # Ensure the directory is writable by the current user.
    if [[ ! -w "$OFFLINE_REPO_DIR" ]]; then
        log_step "Adjusting ownership: ${OFFLINE_REPO_DIR} → ${USER}"
        # Trailing colon form: chown uses the user's primary login group,
        # avoiding any assumption that the group name matches ${USER}.
        sudo chown "${USER}:" -- "$OFFLINE_REPO_DIR" \
            || die "Cannot make OFFLINE_REPO_DIR writable by '${USER}'."
    fi

    # Per-run temp dir — registered for automatic cleanup on EXIT.
    CLONE_BASE_DIR=$(mktemp -d /tmp/aur_factory_builds.XXXXXX) \
        || die "Cannot create temporary build directory."
    _register_temp "$CLONE_BASE_DIR"

    log_ok "Offline repo dir : ${OFFLINE_REPO_DIR}"
    log_ok "Build temp dir   : ${CLONE_BASE_DIR}"
}

# ==============================================================================
# SECTION 7 — ISOLATED PACMAN SANDBOX
# ==============================================================================

# Read-only pacman queries (no root needed; sync DB files are world-readable).
_pacman_query() {
    pacman \
        --dbpath "${ISOLATED_DB_DIR}"             \
        --config "${ISOLATED_DB_DIR}/pacman.conf" \
        "$@"
}

# Write operations: DB sync (-Sy) and package downloads (-Sw).
_pacman_isolated() {
    sudo pacman \
        --dbpath  "${ISOLATED_DB_DIR}"             \
        --gpgdir  '/etc/pacman.d/gnupg'            \
        --config  "${ISOLATED_DB_DIR}/pacman.conf" \
        --disable-sandbox                          \
        --noconfirm                                \
        --color   auto                             \
        "$@"
}

_init_isolated_db() {
    log_info "Initialising isolated pacman sandbox"

    # Start fresh every run to guarantee current package data.
    [[ -d "${ISOLATED_DB_DIR}" ]] && rm -rf -- "${ISOLATED_DB_DIR}"
    mkdir -p -- "${ISOLATED_DB_DIR}/local" "${ISOLATED_DB_DIR}/sync"

    # Copy system pacman.conf but strip IgnorePkg / IgnoreGroup so every
    # package is resolvable in the sandbox without interference.
    grep -vE '^\s*(IgnorePkg|IgnoreGroup)\s*=' /etc/pacman.conf \
        > "${ISOLATED_DB_DIR}/pacman.conf"

    log_step "Syncing package databases into sandbox..."
    _pacman_isolated -Sy || die "Failed to sync package databases into isolated sandbox."

    log_ok "Isolated sandbox ready."
}

# ==============================================================================
# SECTION 8 — AUR RPC VERSION QUERY
# ==============================================================================

# Prints the latest AUR version string (e.g. "2:1.0-1" or "1.0-1") to stdout.
# Returns 1 if the package is not found on the AUR or if the network fails.
_aur_get_version() {
    local pkg="$1"
    local version

    version=$(
        curl -fsSL \
            --retry 3 \
            --retry-delay 2 \
            --retry-all-errors \
            --max-time 15 \
            "${AUR_RPC_BASE_URL}?arg[]=${pkg}" 2>/dev/null \
        | python3 -c "
import sys, json
target = sys.argv[1]
try:
    data = json.load(sys.stdin)
    for r in data.get('results', []):
        if r.get('Name') == target:
            print(r['Version'])
            sys.exit(0)
    sys.exit(1)
except Exception:
    sys.exit(1)
" "$pkg"
    ) || return 1

    [[ -n "$version" ]] || return 1
    printf '%s' "$version"
}

# ==============================================================================
# SECTION 9 — IDEMPOTENCY CHECK
# ==============================================================================

# Returns 0 if a package at the given version is already present in OFFLINE_REPO_DIR.
# Returns 1 if the package needs to be built.
#
# $2 must be the epoch-stripped version string (pkgver-pkgrel).
# Uses *.pkg.tar.* to handle any PKGEXT (zst, xz, lz4, etc.).
_package_is_current() {
    local pkg="$1"
    local ver_no_epoch="$2"

    local found
    found=$(find "$OFFLINE_REPO_DIR" -maxdepth 1 \
        -name "${pkg}-${ver_no_epoch}-*.pkg.tar.*" \
        ! -name '*.sig' \
        -type f 2>/dev/null \
        | head -n 1)

    [[ -n "$found" ]]
}

# ==============================================================================
# SECTION 10 — DEPENDENCY EXTRACTION & DOWNLOAD
# ==============================================================================

# Extracts runtime dependency names from a built package's embedded .PKGINFO.
# Prints one clean dependency name per line (version constraints stripped).
# Filters out virtual soname deps (so:...) and pkgconfig deps (pkgconfig(...))
# which are not installable by name and would generate spurious warnings.
_extract_runtime_deps() {
    local pkgfile="$1"

    bsdtar -xOf "$pkgfile" .PKGINFO 2>/dev/null \
    | grep '^depend = '       \
    | sed 's/^depend = //'   \
    | sed 's/[><=].*//'      \
    | sed 's/[[:space:]]*$//' \
    | grep -v '^$'            \
    | grep -v '^so:'          \
    | grep -v '^pkgconfig('   \
    || true
}

# Downloads all official-repo runtime deps (and their full transitive closure,
# which pacman -Sw resolves automatically) into OFFLINE_REPO_DIR.
_download_official_deps() {
    local pkg="$1"
    shift
    local -a all_deps=("$@")

    if (( ${#all_deps[@]} == 0 )); then
        log_step "No runtime dependencies to download for '${pkg}'."
        return 0
    fi

    log_step "Classifying ${#all_deps[@]} runtime dep(s) for '${pkg}'..."

    local -a official_deps=()
    local dep

    for dep in "${all_deps[@]}"; do
        # Read-only query against the isolated sandbox (sync DBs are world-readable).
        if _pacman_query -Si -- "$dep" &>/dev/null; then
            official_deps+=("$dep")
            log_step "  [official] ${dep}"
        else
            # Not found in official repos — it is an AUR runtime dependency.
            # Automatically inject it into the build queue if not already present.
            local already_queued=0
            local existing
            for existing in "${AUR_PACKAGES[@]}"; do
                if [[ "$existing" == "$dep" ]]; then
                    already_queued=1
                    break
                fi
            done

            if (( already_queued )); then
                log_step "  [aur] ${dep} (already queued for build)"
            else
                log_step "  [aur] ${dep} (auto-queuing missing dependency)"
                AUR_PACKAGES+=("$dep")
            fi
        fi
    done

    if (( ${#official_deps[@]} == 0 )); then
        log_step "No official-repo deps require downloading for '${pkg}'."
        return 0
    fi

    log_step "Downloading ${#official_deps[@]} official dep(s) + full closure for '${pkg}'..."

    # Build cache arguments dynamically for native deduplication
    local -a cache_args=( "--cachedir" "${OFFLINE_REPO_DIR}" )
    
    if [[ -d "${OFFICIAL_REPO_DIR}" ]]; then
        cache_args+=( "--cachedir" "${OFFICIAL_REPO_DIR}" )
    fi

    local -i attempt
    for (( attempt = 1; attempt <= MAX_ATTEMPTS; attempt++ )); do
        if _pacman_isolated \
            -Sw "${cache_args[@]}" \
            -- "${official_deps[@]}"; then
            log_ok "Runtime deps downloaded/verified for '${pkg}'."
            return 0
        fi

        if (( attempt < MAX_ATTEMPTS )); then
            log_warn "Dep download failed (attempt ${attempt}/${MAX_ATTEMPTS}). Retrying in ${TIMEOUT_SEC}s..."
            sleep "${TIMEOUT_SEC}"
        fi
    done

    log_warn "Failed to download all official deps for '${pkg}' after ${MAX_ATTEMPTS} attempts."
    log_warn "The offline repo may be incomplete for this package. Continuing."
    return 1
}

# ==============================================================================
# SECTION 11 — BUILD A SINGLE AUR PACKAGE
# ==============================================================================

_build_aur_package() {
    local pkg="$1"
    _LAST_PKG_SKIPPED=0

    log_task "Processing AUR package: ${pkg}"

    # ── Step A: Verify package exists on AUR & get its version ────────────────
    local aur_version
    if ! aur_version=$(_aur_get_version "$pkg"); then
        # Not on AUR — check if it's in official repos (graceful skip).
        if _pacman_query -Si -- "$pkg" &>/dev/null; then
            log_skip "'${pkg}' is in official repos (handled by pacman script). Skipping."
            _LAST_PKG_SKIPPED=1
            return 0
        fi
        log_err "'${pkg}' not found on AUR or in official repos. Skipping."
        return 1
    fi
    log_step "AUR version found: ${aur_version}"

    # Strip epoch prefix (e.g. "2:1.0-1" → "1.0-1"; "1.0-1" stays "1.0-1").
    # Computed once here and reused throughout this function.
    local ver_no_epoch="${aur_version##*:}"

    # ── Step B: Idempotency — skip if this version is already built ───────────
    if _package_is_current "$pkg" "$ver_no_epoch"; then
        log_skip "'${pkg}-${aur_version}' already present in repo. Nothing to do."
        _LAST_PKG_SKIPPED=1
        return 0
    fi

    # ── Step C: Prepare a clean clone directory for this package ──────────────
    local pkg_clone_root="${CLONE_BASE_DIR}/clone_${pkg}"
    rm -rf -- "$pkg_clone_root"
    mkdir -p -- "$pkg_clone_root"

    # ── Step D: Fetch PKGBUILD with paru -G ───────────────────────────────────
    log_step "Fetching PKGBUILD for '${pkg}'..."

    local -i attempt
    for (( attempt = 1; attempt <= MAX_ATTEMPTS; attempt++ )); do
        # Wrap in a subshell to cd into the target directory before fetching,
        # as paru -G always extracts into the current working directory.
        if ( cd "$pkg_clone_root" && paru -G \
            --skipreview \
            --noprogressbar \
            --noconfirm  \
            "$pkg" ) 2>&1; then
            break
        fi
        if (( attempt == MAX_ATTEMPTS )); then
            log_err "PKGBUILD fetch failed for '${pkg}' after ${MAX_ATTEMPTS} attempts."
            return 1
        fi
        log_warn "PKGBUILD fetch failed (attempt ${attempt}/${MAX_ATTEMPTS}). Retrying in ${TIMEOUT_SEC}s..."
        sleep "${TIMEOUT_SEC}"
    done

    # Locate the PKGBUILD directory.
    # paru -G clones to $clonedir/$pkgbase/PKGBUILD.  pkgbase may differ from
    # pkgname for split packages, so we search rather than assume the path.
    local pkgbuild_file
    pkgbuild_file=$(
        find "$pkg_clone_root" -maxdepth 2 -name 'PKGBUILD' -type f \
        | head -n 1
    ) || true

    if [[ -z "$pkgbuild_file" ]]; then
        log_err "PKGBUILD not found under '${pkg_clone_root}' after clone."
        return 1
    fi

    # Strip filename to get directory using bash parameter expansion.
    # Avoids a dirname subprocess and xargs pipeline.
    local pkgbuild_dir="${pkgbuild_file%/*}"

    if [[ ! -f "${pkgbuild_dir}/PKGBUILD" ]]; then
        log_err "PKGBUILD path resolution failed for '${pkg_clone_root}'."
        return 1
    fi
    log_step "PKGBUILD located at: ${pkgbuild_dir}"

    # ── Step E: Neutralize Gradle Daemon Deadlocks ────────────────────────────
    # Even with --no-daemon, Gradle >= 3.0 forks a "single-use daemon" if JVM args
    # mismatch. This daemon inherits stdout/stderr pipes, causing paru's PTY to
    # hang forever at "> IDLE". We must force plain console and explicitly order
    # the daemon to suicide before the build() function exits.
    if grep -qiE 'gradle|gradlew' "${pkgbuild_dir}/PKGBUILD"; then
        log_step "Patching PKGBUILD to prevent Gradle/Java deadlocks..."
        
        # 1. Force plain console (disables the rich UI that deadlocks)
        sed -i '1i export GRADLE_OPTS="-Dorg.gradle.daemon=false -Dorg.gradle.console=plain"' "${pkgbuild_dir}/PKGBUILD"
        
        # 2. Inject daemon assassination commands at the end of the build() function
        awk '
        /^build\(\)/ { in_build=1 }
        in_build && /^}/ {
            print "    /usr/bin/gradle --stop 2>/dev/null || true"
            print "    ./gradlew --stop 2>/dev/null || true"
            in_build=0
        }
        { print }
        ' "${pkgbuild_dir}/PKGBUILD" > "${pkgbuild_dir}/PKGBUILD.tmp" && mv "${pkgbuild_dir}/PKGBUILD.tmp" "${pkgbuild_dir}/PKGBUILD"
    fi

    # ── Step F: Snapshot repo contents before build ───────────────────────────
    local -a pre_build_pkgs=()
    mapfile -t pre_build_pkgs < <(
        find "$OFFLINE_REPO_DIR" -maxdepth 1 \
            -name '*.pkg.tar.*' ! -name '*.sig' \
            -type f 2>/dev/null | sort
    )

    # ── Step G: Build — paru -B (no install) ─────────────────────────────────
    # PKGDEST  → temporary dir to prevent paru panics on root-owned directories 
    #            like lost+found in the target repo. Moved after success.
    # BUILDDIR → makepkg src/pkg work tree (inside our temp dir, auto-cleaned).
    # SRCDEST  → downloaded source tarballs (inside our temp dir, auto-cleaned).
    local build_work_dir="${CLONE_BASE_DIR}/work_${pkg}"
    local temp_pkgdest="${build_work_dir}/pkgdest"
    mkdir -p -- "${build_work_dir}" "${build_work_dir}/src" "${temp_pkgdest}"

    log_step "Building '${pkg}' → PKGDEST=${OFFLINE_REPO_DIR}"
    log_step "Build timeout: ${BUILD_TIMEOUT_SEC}s per attempt"

    for (( attempt = 1; attempt <= MAX_ATTEMPTS; attempt++ )); do
        # Use "|| build_rc=$?" rather than capturing $? after an if/fi block.
        # When an if-condition is false and no else branch exists, bash sets the
        # if compound's exit status to 0 — meaning $? after "fi" is always 0
        # regardless of what the condition command actually returned.  The ||
        # form correctly captures the real non-zero exit code while remaining
        # safe under set -e (the || suppresses errexit on the left-hand side).
        local build_rc=0
        PKGDEST="${temp_pkgdest}"       \
        BUILDDIR="${build_work_dir}"    \
        SRCDEST="${build_work_dir}/src" \
        timeout "${BUILD_TIMEOUT_SEC}"  \
        paru -B "$pkgbuild_dir"         \
            --noconfirm                 \
            --noprogressbar             \
            --sudoloop                  \
            --mflags "--nocheck"        \
            --mflags "--skippgpcheck"   \
            < /dev/null 2>&1 || build_rc=$?

        if (( build_rc == 0 )); then
            # Move built packages to the final destination so Step G detects them.
            for built_pkg in "$temp_pkgdest"/*.pkg.tar.*; do
                [[ -f "$built_pkg" ]] && mv -- "$built_pkg" "$OFFLINE_REPO_DIR/"
            done
            break
        fi

        if (( build_rc == 124 )); then
            log_err "Build timed out after ${BUILD_TIMEOUT_SEC}s for '${pkg}'."
            return 1
        fi
        if (( attempt == MAX_ATTEMPTS )); then
            log_err "Build failed for '${pkg}' after ${MAX_ATTEMPTS} attempts (exit ${build_rc})."
            return 1
        fi
        log_warn "Build failed (attempt ${attempt}/${MAX_ATTEMPTS}, exit ${build_rc}). Retrying in ${TIMEOUT_SEC}s..."
        sleep "${TIMEOUT_SEC}"
    done

    # ── Step H: Identify newly produced package file(s) ───────────────────────
    local -a post_build_pkgs=()
    mapfile -t post_build_pkgs < <(
        find "$OFFLINE_REPO_DIR" -maxdepth 1 \
            -name '*.pkg.tar.*' ! -name '*.sig' \
            -type f 2>/dev/null | sort
    )

    # New files = present in post-build snapshot but absent from pre-build snapshot.
    local -a new_pkg_files=()
    local f b found_in_pre

    for f in "${post_build_pkgs[@]}"; do
        found_in_pre=0
        for b in "${pre_build_pkgs[@]+"${pre_build_pkgs[@]}"}"; do
            [[ "$f" == "$b" ]] && { found_in_pre=1; break; }
        done
        (( found_in_pre )) || new_pkg_files+=("$f")
    done

    # Fallback: if snapshot diff found nothing, search by package name + version.
    # This handles the edge case where paru -B detected an existing output file
    # and skipped re-copying it (pre == post, but the file is valid).
    if (( ${#new_pkg_files[@]} == 0 )); then
        mapfile -t new_pkg_files < <(
            find "$OFFLINE_REPO_DIR" -maxdepth 1 \
                -name "${pkg}-${ver_no_epoch}-*.pkg.tar.*" \
                ! -name '*.sig' \
                -type f 2>/dev/null
        )
        if (( ${#new_pkg_files[@]} > 0 )); then
            log_warn "No new file detected by snapshot diff; found existing version-matched file(s)."
        else
            log_err "Build appeared to succeed but no package file found in '${OFFLINE_REPO_DIR}'."
            log_err "Verify that PKGDEST is honoured by paru -B for this package."
            return 1
        fi
    fi

    local nf
    for nf in "${new_pkg_files[@]}"; do
        log_ok "Built: ${nf##*/}"
    done

    # ── Step I: Extract runtime deps & download official ones ─────────────────
    log_step "Extracting runtime dependencies from ${#new_pkg_files[@]} built package(s)..."

    local -A seen_deps=()
    local -a unique_deps=()
    local pkgfile dep

    for pkgfile in "${new_pkg_files[@]}"; do
        local -a raw_deps=()
        mapfile -t raw_deps < <(_extract_runtime_deps "$pkgfile")

        for dep in "${raw_deps[@]+"${raw_deps[@]}"}"; do
            [[ -n "$dep" ]] || continue
            if [[ -z "${seen_deps[$dep]+_}" ]]; then
                seen_deps[$dep]=1
                unique_deps+=("$dep")
            fi
        done
    done

    log_step "Found ${#unique_deps[@]} unique runtime dep(s) across all built packages."

    if (( ${#unique_deps[@]} > 0 )); then
        _download_official_deps "$pkg" "${unique_deps[@]}" || {
            log_warn "Dep download had errors for '${pkg}'. The repo may be incomplete."
        }
    fi

    # ── Step J: Clean up per-package build and clone directories ──────────────
    rm -rf -- "${pkg_clone_root}" "${build_work_dir}" 2>/dev/null || true

    log_ok "Package '${pkg}' successfully processed."
    return 0
}

# ==============================================================================
# SECTION 12 — REPOSITORY DATABASE UPDATE
# ==============================================================================

_update_repo_database() {
    log_info "Updating pacman repository database"

    local db_path="${OFFLINE_REPO_DIR}/${REPO_NAME}.db.tar.gz"

    local -a pkg_files=()
    mapfile -t pkg_files < <(
        find "${OFFLINE_REPO_DIR}" -maxdepth 1 \
            -name '*.pkg.tar.*' ! -name '*.sig' \
            -type f 2>/dev/null | sort
    )

    if (( ${#pkg_files[@]} == 0 )); then
        log_warn "No package files found in '${OFFLINE_REPO_DIR}'. Nothing to index."
        return 0
    fi

    log_step "Indexing ${#pkg_files[@]} package file(s) into: ${db_path}"

    repo-add "$db_path" "${pkg_files[@]}" \
        || die "repo-add failed. Repository database was NOT updated."

    log_ok "Repository database updated: ${db_path}"
}

# ==============================================================================
# SECTION 13 — FINAL SUMMARY
# ==============================================================================

_print_summary() {
    local -i success_count="$1"
    local -i skip_count="$2"
    local -i fail_count="$3"
    shift 3
    local -a failed_list=("$@")

    log_info "Build Summary"

    local repo_sz
    repo_sz=$(du -sh -- "${OFFLINE_REPO_DIR}" 2>/dev/null | awk '{print $1}') \
        || repo_sz='unknown'

    local -i total_pkg_count
    total_pkg_count=$(
        find "${OFFLINE_REPO_DIR}" -maxdepth 1 \
            -name '*.pkg.tar.*' ! -name '*.sig' \
            -type f 2>/dev/null | wc -l
    )

    printf '\n'
    printf '  %s%-42s%s %s\n'  "${BOLD}" "Offline repo path:"                      "${RESET}" "${OFFLINE_REPO_DIR}"
    printf '  %s%-42s%s %s\n'  "${BOLD}" "Repository name:"                        "${RESET}" "${REPO_NAME}"
    printf '  %s%-42s%s %d\n'  "${BOLD}" "Packages built:"                         "${RESET}" "${success_count}"
    printf '  %s%-42s%s %d\n'  "${BOLD}" "Packages skipped (up-to-date/official):" "${RESET}" "${skip_count}"
    printf '  %s%-42s%s %d\n'  "${BOLD}" "Packages failed:"                        "${RESET}" "${fail_count}"
    printf '  %s%-42s%s %d\n'  "${BOLD}" "Total files in repo:"                    "${RESET}" "${total_pkg_count}"
    printf '  %s%-42s%s %s\n'  "${BOLD}" "Total repo size:"                        "${RESET}" "${repo_sz}"

    if (( fail_count > 0 )); then
        printf '\n%s%s[FAILED]%s The following packages did not build:\n' \
            "${BOLD}" "${RED}" "${RESET}"
        local f
        for f in "${failed_list[@]}"; do
            printf '  %s- %s%s\n' "${RED}" "$f" "${RESET}"
        done
        printf '\n'
        return 1
    fi

    printf '\n%s%s[SUCCESS]%s AUR repository is ready for offline ISO integration.\n\n' \
        "${BOLD}" "${GREEN}" "${RESET}"
}

# ==============================================================================
# SECTION 14 — MAIN
# ==============================================================================

main() {
    _parse_args "$@"
    _print_logo

    # ── Pre-flight ────────────────────────────────────────────────────────────
    _check_not_root
    _prompt_repo_dir

    OFFLINE_REPO_DIR="$(realpath -m -- "${OFFLINE_REPO_DIR}")"
    [[ "$OFFLINE_REPO_DIR" == "/" ]] \
        && die "Root directory (/) is not a valid repository path."

    _check_dependencies
    _check_paru
    _check_sudo_access
    _setup_dirs
    _init_isolated_db

    # ── Main build loop ───────────────────────────────────────────────────────
    log_info "Starting compilation of AUR packages"

    local -i built_count=0 skip_count=0 fail_count=0
    local -a failed_pkgs=()
    local pkg

    local -i i=0
    while (( i < ${#AUR_PACKAGES[@]} )); do
        pkg="${AUR_PACKAGES[i]}"
        if _build_aur_package "$pkg"; then
            if (( _LAST_PKG_SKIPPED )); then
                skip_count=$(( skip_count + 1 ))
            else
                built_count=$(( built_count + 1 ))
            fi
        else
            fail_count=$(( fail_count + 1 ))
            failed_pkgs+=("$pkg")
            log_warn "Continuing with remaining packages despite failure on '${pkg}'."
        fi
        i=$(( i + 1 ))
    done

    # ── Finalize repository ───────────────────────────────────────────────────
    _update_repo_database

    # ── Report ────────────────────────────────────────────────────────────────
    _print_summary \
        "$built_count" \
        "$skip_count"  \
        "$fail_count"  \
        "${failed_pkgs[@]+"${failed_pkgs[@]}"}"
}

main "$@"
