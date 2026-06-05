#!/usr/bin/env bash
# ==============================================================================
# 020_build_aur_packages.sh  —  v3.0  (Dual-Arch CachyOS/Arch Factory Edition)
#
# Factory script: Builds AUR packages into an offline pacman repository for
# use in an offline Arch Linux ISO. Resolves transitive closures, downloads
# dependencies, and performs deep pruning/cleanup matching the official script.
# ==============================================================================

set -Eeuo pipefail
shopt -s inherit_errexit

# Enforce strict POSIX locale to guarantee predictable pacman string parsing
export LC_ALL=C
export LANG=C

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
    'peaclock'
    'tray-tui'
    'fzf-git'
    'xdg-terminal-exec'
    'paru'
)

# ==============================================================================
# SECTION 2 — CONFIGURATION
# ==============================================================================

readonly REPO_NAME='archrepo'
readonly ISOLATED_DB_DIR="/tmp/aur_factory_isolated_db_$$"
readonly AUR_RPC_BASE_URL='https://aur.archlinux.org/rpc/v5/info'

# Cleanup configuration (matching official pacman script)
readonly PACCACHE_KEEP=1

# Build timeouts
readonly BUILD_TIMEOUT_SEC=3600
declare -ir MAX_ATTEMPTS=6
declare -ir TIMEOUT_SEC=5

declare -g  OFFLINE_REPO_DIR=''
declare -g  OFFICIAL_REPO_DIR='/srv/offline-repo/official'
declare -g  INTERACTIVE_MODE=1
declare -g  REPO_MODE=0  # 1 = Standard Arch, 2 = CachyOS
declare -g  CLONE_BASE_DIR=''
declare -gi _LAST_PKG_SKIPPED=0
declare -gi _ORPHANS_PRUNED=0

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

log_info()   { printf '\n%s==>%s %s\n'     "${BOLD}${CYAN}"    "${RESET}" "$*";     }
log_step()   { printf '  %s->%s %s\n'      "${BOLD}${MAGENTA}" "${RESET}" "$*";     }
log_ok()     { printf '%s[OK]%s %s\n'      "${BOLD}${GREEN}"   "${RESET}" "$*";     }
log_warn()   { printf '%s[!!]%s %s\n'      "${BOLD}${YELLOW}"  "${RESET}" "$*" >&2; }
log_err()    { printf '%s[XX]%s %s\n'      "${BOLD}${RED}"     "${RESET}" "$*" >&2; }
log_task()   { printf '\n%s:: %s%s\n'      "${BOLD}${CYAN}"    "$*" "${RESET}";     }
log_skip()   { printf '  %s[SKIP]%s %s\n'  "${DIM}"            "${RESET}" "$*";     }
log_delete() { printf '  %s[-]%s %s\n'     "${BOLD}${RED}"     "${RESET}" "$*";     }
die()        { log_err "$*"; exit 1; }

_human_bytes() {
    local -i bytes=${1:-0}
    if (( bytes <= 0 )); then printf '0 B'
    elif (( bytes >= 1073741824 )); then printf '%.2f GiB' "$(bc -l <<<"scale=6; $bytes/1073741824")"
    elif (( bytes >= 1048576 )); then printf '%.2f MiB' "$(bc -l <<<"scale=6; $bytes/1048576")"
    elif (( bytes >= 1024 )); then printf '%.2f KiB' "$(bc -l <<<"scale=6; $bytes/1024")"
    else printf '%d B' "$bytes"; fi
}

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
            --arch)
                REPO_MODE=1
                shift
                ;;
            --cachyos)
                REPO_MODE=2
                shift
                ;;
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
                die "Unknown argument: '$1'"$'\n'"Usage: $0 [--arch | --cachyos] [--auto | --current | --path <dir>]"
                ;;
        esac
    done
}

_prompt_build_mode() {
  (( REPO_MODE != 0 )) && return 0
  (( INTERACTIVE_MODE )) || { REPO_MODE=2; return 0; }

  printf '\n%s==>%s %sSelect Target Repository Mode%s\n' "${BOLD}${CYAN}" "${RESET}" "${BOLD}" "${RESET}"
  printf '  1) Standard Arch Linux (Pure)\n'
  printf '  2) CachyOS x86-64-v3 (Optimized + Arch Fallback)\n\n'
  
  local choice
  while true; do
    read -r -p "  Enter choice [1-2] (default=2): " choice
    choice="${choice:-2}"
    case "$choice" in
      1) REPO_MODE=1; break ;;
      2) REPO_MODE=2; break ;;
      *) printf "  %sInvalid choice.%s\n" "${RED}" "${RESET}" ;;
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
        exit 1
    fi
}

_check_sudo_access() {
    log_step "Verifying sudo access..."
    if ! sudo -n true 2>/dev/null; then
        log_warn "sudo credentials not cached. Please enter your password once:"
        sudo true || die "sudo access is required. Cannot continue."
    fi
    log_ok "sudo access confirmed."
}

_check_dependencies() {
    log_info "Checking required tools"
    local -a required=(python3 bsdtar repo-add pacman git timeout bc paru)
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

    if (( REPO_MODE == 2 )); then
        if ! pacman -Q cachyos-keyring &>/dev/null; then
            log_err "CachyOS mode requires 'cachyos-keyring' installed on the HOST build system."
            die "Please run script 010 interactively first to install the keyring, or install it manually."
        fi
    fi

    log_ok "All required tools and keys are present."
}

_setup_dirs() {
    log_info "Setting up build environment"

    if [[ ! -d "$OFFLINE_REPO_DIR" ]]; then
        log_step "Creating: ${OFFLINE_REPO_DIR}"
        mkdir -p -- "$OFFLINE_REPO_DIR" 2>/dev/null \
            || sudo mkdir -p -- "$OFFLINE_REPO_DIR" \
            || die "Cannot create OFFLINE_REPO_DIR: ${OFFLINE_REPO_DIR}"
    fi

    if [[ ! -w "$OFFLINE_REPO_DIR" ]]; then
        log_step "Adjusting ownership: ${OFFLINE_REPO_DIR} → ${USER}"
        sudo chown "${USER}:" -- "$OFFLINE_REPO_DIR" \
            || die "Cannot make OFFLINE_REPO_DIR writable by '${USER}'."
    fi

    CLONE_BASE_DIR=$(mktemp -d /tmp/aur_factory_builds.XXXXXX) \
        || die "Cannot create temporary build directory."
    _register_temp "$CLONE_BASE_DIR"

    log_ok "Offline repo dir : ${OFFLINE_REPO_DIR}"
    log_ok "Build temp dir   : ${CLONE_BASE_DIR}"
}

# ==============================================================================
# SECTION 7 — ISOLATED PACMAN SANDBOX
# ==============================================================================

_pacman_query() {
    pacman \
        --dbpath "${ISOLATED_DB_DIR}"             \
        --config "${ISOLATED_DB_DIR}/pacman.conf" \
        "$@"
}

_pacman_isolated() {
    # --disable-sandbox explicitly resolves Pacman 7.1.0 filesystem/syscall blocks.
    # --disable-download-timeout prevents mirror trickle speeds from aborting closure syncs.
    sudo pacman \
        --dbpath  "${ISOLATED_DB_DIR}"             \
        --gpgdir  '/etc/pacman.d/gnupg'            \
        --config  "${ISOLATED_DB_DIR}/pacman.conf" \
        --disable-sandbox                          \
        --disable-download-timeout                 \
        --noconfirm                                \
        --color   auto                             \
        "$@"
}

_init_isolated_db() {
    local mode_name="Standard Arch"
    (( REPO_MODE == 2 )) && mode_name="CachyOS v3 Injection"

    log_info "Initialising isolated pacman sandbox (${mode_name})"

    [[ -d "${ISOLATED_DB_DIR}" ]] && rm -rf -- "${ISOLATED_DB_DIR}"
    mkdir -p -- "${ISOLATED_DB_DIR}/local" "${ISOLATED_DB_DIR}/sync" "${ISOLATED_DB_DIR}/pacman.d"

    if (( REPO_MODE == 2 )); then
        log_step "Generating pacman.conf with CachyOS v3 prioritization..."
        
        find /etc/pacman.d -maxdepth 1 -type f -exec cp {} "${ISOLATED_DB_DIR}/pacman.d/" \;

        # Precise architecture patching synchronized directly from 010 script logic
        if [[ -f "${ISOLATED_DB_DIR}/pacman.d/cachyos-v3-mirrorlist" ]]; then
            sed -i 's/\$arch_v3/x86_64_v3/g' "${ISOLATED_DB_DIR}/pacman.d/cachyos-v3-mirrorlist"
            sed -i 's/\$arch/x86_64_v3/g'    "${ISOLATED_DB_DIR}/pacman.d/cachyos-v3-mirrorlist"
        fi
        if [[ -f "${ISOLATED_DB_DIR}/pacman.d/cachyos-mirrorlist" ]]; then
            sed -i 's/\$arch/x86_64/g'       "${ISOLATED_DB_DIR}/pacman.d/cachyos-mirrorlist"
        fi
        if [[ -f "${ISOLATED_DB_DIR}/pacman.d/mirrorlist" ]]; then
            sed -i 's/\$arch/x86_64/g'       "${ISOLATED_DB_DIR}/pacman.d/mirrorlist"
        fi

        awk -v sandbox="${ISOLATED_DB_DIR}" '
        /^#?VerbosePkgLists/ { print "VerbosePkgLists"; next }
        /^#?Color/ { print "Color\nILoveCandy"; next }
        /^#?ParallelDownloads/ { print "ParallelDownloads = 10"; next }
        /^\[options\]/ {
            print
            print "Architecture = x86_64_v3 x86_64"
            next
        }
        /^\s*Architecture\s*=/ { next }
        /^\[core\]/ {
            print "# --- INJECTED CACHYOS v3 REPOSITORIES ---"
            print "[cachyos-v3]"
            print "Server = https://mirror.cachyos.org/repo/x86_64_v3/$repo"
            print ""
            print "[cachyos-core-v3]"
            print "Server = https://mirror.cachyos.org/repo/x86_64_v3/$repo"
            print ""
            print "[cachyos-extra-v3]"
            print "Server = https://mirror.cachyos.org/repo/x86_64_v3/$repo"
            print ""
            print "[cachyos]"
            print "Server = https://mirror.cachyos.org/repo/x86_64/$repo"
            print "# ----------------------------------------"
            print ""
            print "[core]"
            next
        }
        /^\[cachyos/ { skip_cachy = 1; next }
        /^\[/ { skip_cachy = 0 }
        skip_cachy == 1 { next }
        {
            gsub("/etc/pacman.d/", sandbox "/pacman.d/")
            if ($0 ~ /^\s*Server\s*=/) { gsub("\\$arch", "x86_64") }
            print
        }
        ' /etc/pacman.conf | grep -vE '^\s*(IgnorePkg|IgnoreGroup)\s*=' > "${ISOLATED_DB_DIR}/pacman.conf"
    else
        grep -vE '^\s*(IgnorePkg|IgnoreGroup)\s*=' /etc/pacman.conf > "${ISOLATED_DB_DIR}/pacman.conf"
    fi

    log_step "Syncing package databases into sandbox..."
    _pacman_isolated -Sy || die "Failed to sync package databases into isolated sandbox."

    log_ok "Isolated sandbox ready."
}

# ==============================================================================
# SECTION 8 — AUR RPC & CORE BUILD LOGIC
# ==============================================================================

_aur_get_version() {
    local pkg="$1"
    local version

    # Backported: Pure Python request prevents the bash pipe (|) from hanging indefinitely
    # when the network drops (like with a VPN), handles URL encoding automatically, 
    # and avoids curl's dangerous globbing behavior with '[]' characters.
    # Restored: 3-retry loop with a 2-second delay to mimic previous curl resilience.
    version=$(
        python3 -c "
import sys, json, urllib.request, urllib.parse, time

try:
    pkg_name = urllib.parse.quote(sys.argv[1])
    base_url = sys.argv[2]
    url = f'{base_url}?arg[]={pkg_name}'
    
    # Arch AUR rate-limits generic curl/python User-Agents; a custom UA ensures stability
    req = urllib.request.Request(url, headers={'User-Agent': 'DuskyISO-Builder/3.0'})
    
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode('utf-8'))
                    for r in data.get('results', []):
                        if r.get('Name') == sys.argv[1]:
                            print(r['Version'])
                            sys.exit(0)
                    sys.exit(1)
        except Exception:
            if attempt < 3:
                time.sleep(2)
            else:
                sys.exit(1)
    sys.exit(1)
except Exception:
    sys.exit(1)
" "$pkg" "$AUR_RPC_BASE_URL"
    ) || return 1

    [[ -n "$version" ]] || return 1
    printf '%s' "$version"
}

_package_is_current() {
    local pkg="$1"
    local ver_no_epoch="$2"
    local found
    found=$(find "$OFFLINE_REPO_DIR" -maxdepth 1 \
        -name "${pkg}-${ver_no_epoch}-*.pkg.tar.*" \
        ! -name '*.sig' -type f 2>/dev/null | head -n 1)
    [[ -n "$found" ]]
}

_extract_runtime_deps() {
    local pkgfile="$1"
    bsdtar -xOf "$pkgfile" .PKGINFO 2>/dev/null \
    | grep '^depend = ' | sed 's/^depend = //' | sed 's/[><=].*//' \
    | sed 's/[[:space:]]*$//' | grep -v '^$' | grep -v '^so:' | grep -v '^pkgconfig(' | grep -v '\.so$' \
    || true
}

_download_official_deps() {
    local pkg="$1"
    shift
    local -a all_deps=("$@")

    if (( ${#all_deps[@]} == 0 )); then
        return 0
    fi

    local -a official_deps=()
    local dep
    for dep in "${all_deps[@]}"; do
        if _pacman_query -Si -- "$dep" &>/dev/null; then
            official_deps+=("$dep")
            log_step "  [official] ${dep}"
        else
            local already_queued=0 existing
            for existing in "${AUR_PACKAGES[@]}"; do
                if [[ "$existing" == "$dep" ]]; then
                    already_queued=1; break
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

    if (( ${#official_deps[@]} == 0 )); then return 0; fi

    local -a cache_args=( "--cachedir" "${OFFLINE_REPO_DIR}" )
    [[ -d "${OFFICIAL_REPO_DIR}" ]] && cache_args+=( "--cachedir" "${OFFICIAL_REPO_DIR}" )

    local -i attempt
    for (( attempt = 1; attempt <= MAX_ATTEMPTS; attempt++ )); do
        if _pacman_isolated -Sw "${cache_args[@]}" -- "${official_deps[@]}"; then
            log_ok "Runtime deps downloaded/verified for '${pkg}'."
            return 0
        fi
        sleep "${TIMEOUT_SEC}"
    done
    log_warn "Failed to download all official deps for '${pkg}'."
    return 1
}

_build_aur_package() {
    local pkg="$1"
    _LAST_PKG_SKIPPED=0

    log_task "Processing AUR package: ${pkg}"

    local aur_version
    if ! aur_version=$(_aur_get_version "$pkg"); then
        if _pacman_query -Si -- "$pkg" &>/dev/null; then
            log_skip "'${pkg}' is in official repos (handled by pacman script). Skipping."
            _LAST_PKG_SKIPPED=1
            return 0
        fi
        log_err "'${pkg}' not found on AUR or in official repos. Skipping."
        return 1
    fi
    
    local ver_no_epoch="${aur_version##*:}"

    if _package_is_current "$pkg" "$ver_no_epoch"; then
        log_skip "'${pkg}-${aur_version}' already present in repo. Nothing to do."
        _LAST_PKG_SKIPPED=1
        return 0
    fi

    local pkg_clone_root="${CLONE_BASE_DIR}/clone_${pkg}"
    rm -rf -- "$pkg_clone_root"
    mkdir -p -- "$pkg_clone_root"

    log_step "Fetching PKGBUILD for '${pkg}'..."
    local -i attempt
    for (( attempt = 1; attempt <= MAX_ATTEMPTS; attempt++ )); do
        if ( cd "$pkg_clone_root" && paru -G --skipreview --noprogressbar --noconfirm "$pkg" ) 2>&1; then
            break
        fi
        if (( attempt == MAX_ATTEMPTS )); then return 1; fi
        sleep "${TIMEOUT_SEC}"
    done

    local pkgbuild_file
    pkgbuild_file=$(find "$pkg_clone_root" -maxdepth 2 -name 'PKGBUILD' -type f | head -n 1) || true
    [[ -z "$pkgbuild_file" ]] && return 1

    local pkgbuild_dir="${pkgbuild_file%/*}"

    # Gradle deadlock prevention
    if grep -qiE 'gradle|gradlew' "${pkgbuild_dir}/PKGBUILD"; then
        sed -i '1i export GRADLE_OPTS="-Dorg.gradle.daemon=false -Dorg.gradle.console=plain"' "${pkgbuild_dir}/PKGBUILD"
        awk '/^build\(\)/ { in_build=1 } in_build && /^}/ { print "    /usr/bin/gradle --stop 2>/dev/null || true"; print "    ./gradlew --stop 2>/dev/null || true"; in_build=0 } { print }' "${pkgbuild_dir}/PKGBUILD" > "${pkgbuild_dir}/PKGBUILD.tmp" && mv "${pkgbuild_dir}/PKGBUILD.tmp" "${pkgbuild_dir}/PKGBUILD"
    fi

    local -a pre_build_pkgs=()
    mapfile -t pre_build_pkgs < <(find "$OFFLINE_REPO_DIR" -maxdepth 1 -name '*.pkg.tar.*' ! -name '*.sig' -type f 2>/dev/null | sort)

    local build_work_dir="${CLONE_BASE_DIR}/work_${pkg}"
    local temp_pkgdest="${build_work_dir}/pkgdest"
    mkdir -p -- "${build_work_dir}" "${build_work_dir}/src" "${temp_pkgdest}"

    log_step "Building '${pkg}' → PKGDEST=${OFFLINE_REPO_DIR}"

    for (( attempt = 1; attempt <= MAX_ATTEMPTS; attempt++ )); do
        local build_rc=0
        
        PKGDEST="${temp_pkgdest}" BUILDDIR="${build_work_dir}" SRCDEST="${build_work_dir}/src" \
        timeout "${BUILD_TIMEOUT_SEC}" paru -B "$pkgbuild_dir" \
            --noconfirm \
            --noprogressbar \
            --sudoloop \
            --nocheck \
            --nopgpfetch \
            --mflags "--nocheck" \
            --mflags "--skippgpcheck" \
            < /dev/null 2>&1 || build_rc=$?

        if (( build_rc == 0 )); then
            for built_pkg in "$temp_pkgdest"/*.pkg.tar.*; do
                [[ -f "$built_pkg" ]] && mv -- "$built_pkg" "$OFFLINE_REPO_DIR/"
            done
            break
        fi

        if (( build_rc == 124 )); then log_err "Build timed out for '${pkg}'."; return 1; fi
        if (( attempt == MAX_ATTEMPTS )); then return 1; fi
        sleep "${TIMEOUT_SEC}"
    done

    local -a post_build_pkgs=()
    mapfile -t post_build_pkgs < <(find "$OFFLINE_REPO_DIR" -maxdepth 1 -name '*.pkg.tar.*' ! -name '*.sig' -type f 2>/dev/null | sort)

    local -a new_pkg_files=()
    local f b found_in_pre
    for f in "${post_build_pkgs[@]}"; do
        found_in_pre=0
        for b in "${pre_build_pkgs[@]+"${pre_build_pkgs[@]}"}"; do
            [[ "$f" == "$b" ]] && { found_in_pre=1; break; }
        done
        (( found_in_pre )) || new_pkg_files+=("$f")
    done

    if (( ${#new_pkg_files[@]} == 0 )); then
        mapfile -t new_pkg_files < <(find "$OFFLINE_REPO_DIR" -maxdepth 1 -name "${pkg}-${ver_no_epoch}-*.pkg.tar.*" ! -name '*.sig' -type f 2>/dev/null)
        if (( ${#new_pkg_files[@]} == 0 )); then return 1; fi
    fi

    for nf in "${new_pkg_files[@]}"; do log_ok "Built: ${nf##*/}"; done

    local -A seen_deps=()
    local -a unique_deps=()
    local pkgfile dep

    for pkgfile in "${new_pkg_files[@]}"; do
        local -a raw_deps=()
        mapfile -t raw_deps < <(_extract_runtime_deps "$pkgfile")
        for dep in "${raw_deps[@]+"${raw_deps[@]}"}"; do
            [[ -n "$dep" ]] || continue
            if [[ -z "${seen_deps[$dep]+_}" ]]; then
                seen_deps[$dep]=1; unique_deps+=("$dep")
            fi
        done
    done

    if (( ${#unique_deps[@]} > 0 )); then
        _download_official_deps "$pkg" "${unique_deps[@]}" || true
    fi

    rm -rf -- "${pkg_clone_root}" "${build_work_dir}" 2>/dev/null || true
    log_ok "Package '${pkg}' successfully processed."
    return 0
}

# ==============================================================================
# SECTION 9 — PRUNING, CLEANUP & DATABASE GENERATION
# ==============================================================================

_restore_permissions() {
    log_info "Restoring file ownership in repo"
    log_step "Transferring any root-owned files back to user: ${USER}"
    
    sudo chown "${USER}:" "${OFFLINE_REPO_DIR}" 2>/dev/null || true
    find "${OFFLINE_REPO_DIR}" -maxdepth 1 \( -type f -o -type l \) -exec sudo chown -h "${USER}:" {} +
    log_ok "Ownership normalized successfully."
}

_prune_old_versions() {
    log_info "Removing old package versions (keeping ${PACCACHE_KEEP})"
    
    if ! command -v paccache &>/dev/null; then
        log_warn "paccache not found (install pacman-contrib). Skipping cache prune."
        return 0
    fi
    
    echo y | paccache -r -k "${PACCACHE_KEEP}" -c "${OFFLINE_REPO_DIR}" >/dev/null 2>&1 || true
    log_ok "Cache pruned successfully."
}

_update_repo_database() {
    log_info "Generating/Updating pacman repository database"
    local db_file="${OFFLINE_REPO_DIR}/${REPO_NAME}.db.tar.gz"

    for artifact in "$db_file" "$db_file.old" "${OFFLINE_REPO_DIR}/${REPO_NAME}.db" \
                    "${OFFLINE_REPO_DIR}/${REPO_NAME}.files.tar.gz" \
                    "${OFFLINE_REPO_DIR}/${REPO_NAME}.files.tar.gz.old" \
                    "${OFFLINE_REPO_DIR}/${REPO_NAME}.files"; do
        [[ -e "$artifact" || -L "$artifact" ]] && rm -f -- "$artifact"
    done

    local -a pkg_files=()
    mapfile -t pkg_files < <(find "${OFFLINE_REPO_DIR}" -maxdepth 1 -name '*.pkg.tar.*' ! -name '*.sig' -type f | sort)
    
    if (( ${#pkg_files[@]} == 0 )); then
        log_warn "No package files found. Nothing to index."
        return 0
    fi

    log_step "Indexing ${#pkg_files[@]} package file(s) into: ${db_file}"
    repo-add "${db_file}" "${pkg_files[@]}" >/dev/null || die "repo-add failed."
    log_ok "Database updated."
}

declare -ga WHITELIST_PKGNAMES=()

_generate_whitelist_pkgnames() {
    log_info "Resolving full dependency closure (Whitelist Gen)"

    # Inject our newly built local repo into the isolated sandbox
    if ! grep -q "^\[${REPO_NAME}\]" "${ISOLATED_DB_DIR}/pacman.conf"; then
        cat <<EOF >> "${ISOLATED_DB_DIR}/pacman.conf"

[${REPO_NAME}]
SigLevel = Optional TrustAll
Server = file://${OFFLINE_REPO_DIR}
EOF
    fi

    _pacman_isolated -Sy || {
        log_warn "Failed to sync sandbox databases. Skipping orphan prune."
        return 1
    }

    local -a valid_targets=()
    local pkg
    for pkg in "${AUR_PACKAGES[@]}"; do
        if _pacman_query -Si -- "$pkg" &>/dev/null; then
            valid_targets+=("$pkg")
        fi
    done

    if (( ${#valid_targets[@]} == 0 )); then
        log_warn "No valid targets found in DB. Skipping orphan prune."
        return 1
    fi

    local empty_cache tmp_out pacman_rc
    empty_cache=$(mktemp -d)
    _register_temp "$empty_cache"
    tmp_out=$(mktemp)
    _register_temp "$tmp_out"

    set +e
    _pacman_isolated -Sw --print --print-format '%n' --cachedir "$empty_cache" -- "${valid_targets[@]}" >"$tmp_out"
    pacman_rc=$?
    set -e

    rm -rf -- "$empty_cache"

    if (( pacman_rc != 0 )); then
        log_warn "Dependency resolution threw warnings/errors. Skipping prune to ensure safety."
        rm -f -- "$tmp_out"
        return 1
    fi

    local -a raw_lines=()
    mapfile -t raw_lines <"$tmp_out"
    rm -f -- "$tmp_out"

    local line
    for line in "${raw_lines[@]}"; do
        [[ -n "$line" ]] || continue
        [[ "$line" == warning:* ]] && continue
        WHITELIST_PKGNAMES+=("$line")
    done

    if (( ${#WHITELIST_PKGNAMES[@]} == 0 )); then
        log_warn "Whitelist is empty. Skipping orphan prune."
        return 1
    fi

    log_ok "Closure resolved: ${#WHITELIST_PKGNAMES[@]} active base packages required."
    return 0
}

_prune_orphans() {
    _ORPHANS_PRUNED=0
    log_info "Pruning true orphans from: ${OFFLINE_REPO_DIR}"

    local -A _wl_set=()
    local pn
    for pn in "${WHITELIST_PKGNAMES[@]}"; do _wl_set[$pn]=1; done

    local -i del_count=0 del_bytes=0
    local -a pkg_files=()
    mapfile -t pkg_files < <(find "${OFFLINE_REPO_DIR}" -maxdepth 1 -name '*.pkg.tar.*' ! -name '*.sig' -type f)

    local filepath basename pkgname rest fsize
    for filepath in "${pkg_files[@]}"; do
        basename="${filepath##*/}"
        rest="${basename%%.pkg.tar.*}" 
        rest="${rest%-*}"              
        rest="${rest%-*}"              
        pkgname="${rest%-*}"           

        if [[ -z "${_wl_set[$pkgname]+_}" ]]; then
            fsize=$(stat -c '%s' -- "$filepath" 2>/dev/null) || fsize=0
            log_delete "orphan removed: ${pkgname}  (${basename})"
            rm -f -- "$filepath" "${filepath}.sig"
            (( del_bytes += fsize )) || true
            (( ++del_count )) || true
        fi
    done

    # Clean stale signatures left behind by paccache or pruning
    while IFS= read -r lone_sig; do
        local paired_pkg="${lone_sig%.sig}"
        if [[ ! -f "$paired_pkg" ]]; then
            log_delete "stale signature removed: ${lone_sig##*/}"
            rm -f -- "$lone_sig"
        fi
    done < <(find "${OFFLINE_REPO_DIR}" -maxdepth 1 -name '*.sig' -type f)

    if (( del_count > 0 )); then
        log_ok "Pruned ${del_count} orphaned file(s). Freed ~$( _human_bytes "$del_bytes" )."
        _ORPHANS_PRUNED=1
    else
        log_ok "No orphans found."
    fi
}

# ==============================================================================
# SECTION 10 — FINAL SUMMARY
# ==============================================================================

_print_summary() {
    local -i success_count="$1" skip_count="$2" fail_count="$3"
    shift 3
    local -a failed_list=("$@")

    local mode_name="Standard Arch Linux"
    (( REPO_MODE == 2 )) && mode_name="CachyOS x86-64-v3"

    log_info "Build Summary"

    local repo_sz
    repo_sz=$(du -sh -- "${OFFLINE_REPO_DIR}" 2>/dev/null | awk '{print $1}') || repo_sz='unknown'
    local -i total_pkg_count
    total_pkg_count=$(find "${OFFLINE_REPO_DIR}" -maxdepth 1 -name '*.pkg.tar.*' ! -name '*.sig' -type f 2>/dev/null | wc -l)

    printf '\n'
    printf '  %s%-42s%s %s\n'  "${BOLD}" "Target Architecture:"                    "${RESET}" "${mode_name}"
    printf '  %s%-42s%s %s\n'  "${BOLD}" "Offline repo path:"                      "${RESET}" "${OFFLINE_REPO_DIR}"
    printf '  %s%-42s%s %s\n'  "${BOLD}" "Repository name:"                        "${RESET}" "${REPO_NAME}"
    printf '  %s%-42s%s %d\n'  "${BOLD}" "Packages built:"                         "${RESET}" "${success_count}"
    printf '  %s%-42s%s %d\n'  "${BOLD}" "Packages skipped (up-to-date/official):" "${RESET}" "${skip_count}"
    printf '  %s%-42s%s %d\n'  "${BOLD}" "Packages failed:"                        "${RESET}" "${fail_count}"
    printf '  %s%-42s%s %d\n'  "${BOLD}" "Total files in repo:"                    "${RESET}" "${total_pkg_count}"
    printf '  %s%-42s%s %s\n'  "${BOLD}" "Total repo size:"                        "${RESET}" "${repo_sz}"

    if (( fail_count > 0 )); then
        printf '\n%s%s[FAILED]%s The following packages did not build:\n' "${BOLD}" "${RED}" "${RESET}"
        for f in "${failed_list[@]}"; do printf '  %s- %s%s\n' "${RED}" "$f" "${RESET}"; done
        printf '\n'
        return 1
    fi

    printf '\n%s%s[SUCCESS]%s AUR repository is pristine and ready for ISO integration.\n\n' "${BOLD}" "${GREEN}" "${RESET}"
}

# ==============================================================================
# SECTION 11 — MAIN
# ==============================================================================

main() {
    _parse_args "$@"
    _print_logo

    _check_not_root
    _prompt_build_mode
    _prompt_repo_dir

    OFFLINE_REPO_DIR="$(realpath -m -- "${OFFLINE_REPO_DIR}")"
    [[ "$OFFLINE_REPO_DIR" == "/" ]] && die "Root directory (/) is not a valid repository path."

    _check_sudo_access
    _check_dependencies
    _setup_dirs
    _init_isolated_db

    # ── 1. Main build loop ────────────────────────────────────────────────────
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
            log_warn "Continuing with remaining packages despite failure on '${pkg}'.."
        fi
        i=$(( i + 1 ))
    done

    # ── 2. Forensic Cleanup & Finalization ────────────────────────────────────
    _restore_permissions           # Normalize ownership from sudo pacman downloads
    _prune_old_versions            # Keep only the newest versions using paccache
    _update_repo_database          # Generate the initial database with latest files

    # Generate exact closure whitelist and prune dead weight
    if _generate_whitelist_pkgnames; then
        _prune_orphans
        if (( _ORPHANS_PRUNED )); then
            _update_repo_database  # Re-run repo-add if we actually deleted orphans
        fi
    fi

    _restore_permissions           # Final pass ensuring DB files aren't root-owned

    # ── 3. Summary ────────────────────────────────────────────────────────────
    _print_summary "$built_count" "$skip_count" "$fail_count" "${failed_pkgs[@]+"${failed_pkgs[@]}"}"
}

main "$@"
