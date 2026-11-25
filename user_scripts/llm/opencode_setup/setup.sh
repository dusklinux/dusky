#!/usr/bin/env bash
# One-shot, idempotent OpenCode V2 CLI installer and configurator for Arch Linux (Wayland/Hyprland).
# Zero hardcoded usernames/paths, zero secrets, CLI-only (no Electron/Desktop/Xorg/XWayland).

set -euo pipefail
IFS=$'\n\t'

# ------------------------------------------------------------------------------
# Tunable Defaults
# ------------------------------------------------------------------------------
readonly OPENCODE_INSTALL_URL="https://opencode.ai/v2/install"

readonly LMSTUDIO_BASE_URL="http://127.0.0.1:1234/v1"
readonly LMSTUDIO_CTX="65536"
readonly LMSTUDIO_OUT="16384"

readonly INKLING_BASE_URL="http://127.0.0.1:8080/v1"
readonly INKLING_MODEL_ID="inkling"
readonly INKLING_CTX="32768"
readonly INKLING_OUT="8192"

readonly PATH_BLOCK_START="# >>> opencode-setup PATH >>>"
readonly PATH_BLOCK_END="# <<< opencode-setup PATH <<<"

# ------------------------------------------------------------------------------
# Runtime State & Trap Cleanup
# ------------------------------------------------------------------------------
RESET_STATE=0
PINNED_VERSION=""
VERIFICATION_ACTIVE=0
declare -a TMP_FILES=()

log() {
    printf '[opencode-setup] %s\n' "$*"
}

warn() {
    printf '[opencode-setup] WARN: %s\n' "$*" >&2
}

die() {
    printf '[opencode-setup] ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: setup.sh [OPTIONS]

Idempotently installs the latest OpenCode V2 CLI via the official V2 installer,
wires pure Wayland/Hyprland + shell PATH entries, removes legacy V1/experimental
artifacts, and deploys a native V2 opencode.json for LM Studio and Inkling.

Options:
  --version <ver>   Pin a specific OpenCode V2 version (e.g., 2.4.1). Default: latest V2.
  --reset-state     Also wipe service.json, OpenCode SQLite DB/data dir, and cache dir
                    so they regenerate cleanly on next launch (useful on dirty migrations).
  -h, --help        Show this help message and exit.
EOF
}

parse_args() {
    while (( $# > 0 )); do
        case "$1" in
            --reset-state)
                RESET_STATE=1
                shift
                ;;
            --version)
                (( $# >= 2 )) || die "--version requires an argument (e.g., --version 2.4.1)"
                PINNED_VERSION="$2"
                shift 2
                ;;
            --version=*)
                PINNED_VERSION="${1#--version=}"
                [[ -n "$PINNED_VERSION" ]] || die "--version= requires a non-empty value"
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                die "Unknown option: $1 (run with --help for usage)"
                ;;
        esac
    done

    if [[ -n "$PINNED_VERSION" ]]; then
        [[ "$PINNED_VERSION" =~ ^v?2\.[0-9]+\.[0-9]+([.-][0-9A-Za-z._-]+)?$ ]] \
            || die "Pinned version must be OpenCode V2 (2.x.y); got: $PINNED_VERSION"
    fi
}

stop_opencode_service() {
    local candidate=""
    local bin=""

    for candidate in \
        "${OPENCODE_HOME_BIN:-}" \
        "${LOCAL_BIN:-}/opencode" \
        "$(command -v opencode 2>/dev/null || true)"; do
        if [[ -n "$candidate" && -x "$candidate" ]]; then
            bin="$candidate"
            break
        fi
    done

    if [[ -n "$bin" ]]; then
        "$bin" service stop >/dev/null 2>&1 || true
    fi

    if command -v pgrep >/dev/null 2>&1; then
        local attempts=0
        while pgrep -u "$EUID" -f 'opencode serve --service' >/dev/null 2>&1; do
            attempts=$((attempts + 1))
            if (( attempts > 15 )); then
                warn "Background 'opencode serve --service' did not exit after 3s; continuing."
                break
            fi
            sleep 0.2
        done
    fi
}

cleanup() {
    local f
    for f in "${TMP_FILES[@]}"; do
        if [[ -n "$f" && -e "$f" ]]; then
            rm -f -- "$f"
        fi
    done
    if (( VERIFICATION_ACTIVE == 1 )); then
        stop_opencode_service
    fi
}
trap cleanup EXIT

atomic_write_if_changed() {
    local target="$1"
    local content="$2"
    local do_backup="${3:-0}"
    local parent_dir tmp_file backup_file timestamp

    parent_dir="$(dirname -- "$target")"
    mkdir -p -- "$parent_dir"

    tmp_file="$(mktemp "${target}.tmp.XXXXXX")"
    TMP_FILES+=("$tmp_file")
    printf '%s' "$content" > "$tmp_file"

    if [[ -f "$target" ]] && cmp -s -- "$tmp_file" "$target"; then
        rm -f -- "$tmp_file"
        log "Unchanged (no-op): $target"
        return 0
    fi

    if [[ -f "$target" && "$do_backup" == "1" ]]; then
        timestamp="$(date +%Y%m%d-%H%M%S)"
        backup_file="${target}.bak.${timestamp}"
        cp -p -- "$target" "$backup_file"
        log "Backed up existing file to: $backup_file"
    fi

    mv -f -- "$tmp_file" "$target"
    log "Wrote: $target"
}

ensure_shell_rc_path_block() {
    local rc_file="$1"
    local tmp_file

    if [[ -f "$rc_file" ]] && grep -Fq -- "$PATH_BLOCK_START" "$rc_file"; then
        log "PATH block already present (no-op): $rc_file"
        return 0
    fi

    mkdir -p -- "$(dirname -- "$rc_file")"
    tmp_file="$(mktemp "${rc_file}.tmp.XXXXXX")"
    TMP_FILES+=("$tmp_file")

    if [[ -f "$rc_file" ]]; then
        cat -- "$rc_file" > "$tmp_file"
        if [[ -s "$tmp_file" ]] && [[ "$(tail -c 1 "$tmp_file" | wc -l)" -eq 0 ]]; then
            printf '\n' >> "$tmp_file"
        fi
        printf '\n' >> "$tmp_file"
    fi

    cat >> "$tmp_file" <<EOF
${PATH_BLOCK_START}
case ":\$PATH:" in
    *":\$HOME/.local/bin:"*) ;;
    *) export PATH="\$HOME/.local/bin:\$PATH" ;;
esac
${PATH_BLOCK_END}
EOF

    mv -f -- "$tmp_file" "$rc_file"
    log "Appended guarded PATH block to: $rc_file"
}

# ------------------------------------------------------------------------------
# Phase 1: Runtime Discovery & Preflight
# ------------------------------------------------------------------------------
phase_preflight() {
    log "Phase 1/8: Runtime discovery and preflight checks..."

    (( EUID != 0 )) || die "Do not run as root. OpenCode V2 binaries, config, SQLite DB, and service are strictly per-user."

    [[ -n "${HOME:-}" && -d "$HOME" ]] || die "HOME is unset or does not exist."

    local arch
    arch="$(uname -m)"
    [[ "$arch" == "x86_64" ]] || die "Unsupported architecture '$arch' (expected x86_64)."

    command -v pacman >/dev/null 2>&1 || die "pacman not found; this script targets Arch Linux."

    XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
    XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
    XDG_STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
    XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"

    LOCAL_BIN="$HOME/.local/bin"
    OPENCODE_HOME_BIN="$HOME/.opencode/bin/opencode"
    OPENCODE_CONFIG_DIR_CANONICAL="$XDG_CONFIG_HOME/opencode"
    OPENCODE_DATA_DIR_CANONICAL="$XDG_DATA_HOME/opencode"
    OPENCODE_STATE_DIR_CANONICAL="$XDG_STATE_HOME/opencode"
    OPENCODE_CACHE_DIR_CANONICAL="$XDG_CACHE_HOME/opencode"
    OPENCODE_ENV_D_DIR="$XDG_CONFIG_HOME/environment.d"

    # Prevent ambient OpenCode override env vars from hijacking installation or verification.
    unset OPENCODE_CONFIG OPENCODE_CONFIG_DIR OPENCODE_CONFIG_CONTENT OPENCODE_INSTALL_DIR XDG_BIN_DIR

    log "Resolved HOME=$HOME | CONFIG=$OPENCODE_CONFIG_DIR_CANONICAL | BIN=$LOCAL_BIN"
}

# ------------------------------------------------------------------------------
# Phase 2: Conflict Guard (Single-Source V2 Enforcement)
# ------------------------------------------------------------------------------
phase_conflict_guard() {
    log "Phase 2/8: Checking for conflicting OpenCode installations..."

    local -a conflicts=()
    local pkg

    for pkg in opencode opencode-bin opencode-beta; do
        if pacman -Q "$pkg" >/dev/null 2>&1; then
            conflicts+=("pacman package '$pkg' installed -> remove with: sudo pacman -Rns$pkg")
        fi
    done

    local sys_bin
    for sys_bin in /usr/bin/opencode /usr/local/bin/opencode; do
        if [[ -e "$sys_bin" || -L "$sys_bin" ]]; then
            conflicts+=("system binary/symlink present at '$sys_bin' -> remove it before running this script")
        fi
    done

    if command -v npm >/dev/null 2>&1; then
        local npm_root
        npm_root="$(npm root -g 2>/dev/null || true)"
        if [[ -n "$npm_root" ]]; then
            if [[ -d "$npm_root/opencode-ai" ]]; then
                conflicts+=("global npm package 'opencode-ai' (V1) detected -> remove with: npm uninstall -g opencode-ai")
            fi
            if [[ -d "$npm_root/@opencode/cli" ]]; then
                conflicts+=("global npm package '@opencode/cli' detected -> remove with: npm uninstall -g @opencode/cli")
            fi
        fi
    fi

    if (( ${#conflicts[@]} > 0 )); then
        printf '[opencode-setup] ERROR: Conflicting OpenCode installation(s) detected:\n' >&2
        local item
        for item in "${conflicts[@]}"; do
            printf '  - %s\n' "$item" >&2
        done
        exit 1
    fi

    log "No conflicting installations found."
}

# ------------------------------------------------------------------------------
# Phase 3: Minimal Arch Helper Sync (Zero-Sudo When Satisfied)
# ------------------------------------------------------------------------------
phase_arch_helpers() {
    log "Phase 3/8: Checking Arch Linux runtime helpers..."

    local -a required_pkgs=(curl tar git ripgrep wl-clipboard)
    if ! command -v cmp >/dev/null 2>&1; then
        required_pkgs+=(diffutils)
    fi

    local -a missing_pkgs=()
    local pkg
    for pkg in "${required_pkgs[@]}"; do
        if ! pacman -Q "$pkg" >/dev/null 2>&1; then
            missing_pkgs+=("$pkg")
        fi
    done

    if (( ${#missing_pkgs[@]} == 0 )); then
        log "All required Arch helpers already installed (skipping pacman/sudo)."
        return 0
    fi

    log "Installing missing Arch helpers via full rolling sync: ${missing_pkgs[*]}"
    sudo pacman -Syu --needed --noconfirm "${missing_pkgs[@]}"
}

# ------------------------------------------------------------------------------
# Phase 4: Daemon Quiesce & Targeted Legacy Artifact Purge
# ------------------------------------------------------------------------------
phase_quiesce_and_purge() {
    log "Phase 4/8: Quiescing background daemon and purging legacy V1/experimental artifacts..."

    stop_opencode_service

    local -a legacy_paths=(
        "$HOME/.opencode/bin/opencode2"
        "$OPENCODE_CONFIG_DIR_CANONICAL/package.json"
        "$OPENCODE_CONFIG_DIR_CANONICAL/package-lock.json"
        "$OPENCODE_CONFIG_DIR_CANONICAL/bun.lock"
        "$OPENCODE_CONFIG_DIR_CANONICAL/bun.lockb"
        "$OPENCODE_CONFIG_DIR_CANONICAL/node_modules"
        "$OPENCODE_CONFIG_DIR_CANONICAL/identity.json"
        "$OPENCODE_CONFIG_DIR_CANONICAL/unlimited-identity.json"
    )

    local p
    for p in "${legacy_paths[@]}"; do
        if [[ -e "$p" || -L "$p" ]]; then
            rm -rf -- "$p"
            log "Removed legacy artifact: $p"
        fi
    done

    # Purge any machine-specific bridge/unlimited files directly in the config directory.
    if [[ -d "$OPENCODE_CONFIG_DIR_CANONICAL" ]]; then
        local restore_nullglob
        restore_nullglob="$(shopt -p nullglob || true)"
        shopt -s nullglob

        local match base
        for match in \
            "$OPENCODE_CONFIG_DIR_CANONICAL"/*bridge* \
            "$OPENCODE_CONFIG_DIR_CANONICAL"/*unlimited*; do
            base="$(basename -- "$match")"
            case "$base" in
                opencode.json|opencode.jsonc|cli.json|service.json) continue ;;
            esac
            rm -rf -- "$match"
            log "Removed experimental artifact: $match"
        done

        eval "$restore_nullglob"
    fi

    if (( RESET_STATE == 1 )); then
        log "--reset-state specified: wiping service.json, OpenCode data dir, and cache dir for clean regeneration..."
        local -a state_paths=(
            "$OPENCODE_CONFIG_DIR_CANONICAL/service.json"
            "$OPENCODE_STATE_DIR_CANONICAL"
            "$OPENCODE_DATA_DIR_CANONICAL"
            "$OPENCODE_CACHE_DIR_CANONICAL"
        )
        for p in "${state_paths[@]}"; do
            if [[ -e "$p" || -L "$p" ]]; then
                rm -rf -- "$p"
                log "Reset state path: $p"
            fi
        done
    fi
}

# ------------------------------------------------------------------------------
# Phase 5: Official V2 Binary Install & Canonical Symlink
# ------------------------------------------------------------------------------
phase_install_v2() {
    log "Phase 5/8: Installing OpenCode V2 via official installer ($OPENCODE_INSTALL_URL)..."

    local -a installer_args=(--no-modify-path)
    if [[ -n "$PINNED_VERSION" ]]; then
        installer_args+=(--version "$PINNED_VERSION")
        log "Pinning OpenCode V2 version: $PINNED_VERSION"
    fi

    # Intentionally do NOT set OPENCODE_INSTALL_DIR or XDG_BIN_DIR so the V2 installer
    # uses its native $HOME/.opencode/bin/opencode target and preserves `opencode upgrade`.
    curl -fsSL "$OPENCODE_INSTALL_URL" \
        | env -u OPENCODE_INSTALL_DIR -u XDG_BIN_DIR bash -s -- "${installer_args[@]}"

    [[ -x "$OPENCODE_HOME_BIN" ]] \
        || die "Expected OpenCode V2 binary not found or not executable at: $OPENCODE_HOME_BIN"

    local raw_version
    raw_version="$("$OPENCODE_HOME_BIN" --version 2>&1)"
    [[ "$raw_version" =~ (^|[^0-9])v?2\.[0-9]+\.[0-9]+ ]] \
        || die "Installed binary is not OpenCode V2 (reported: $raw_version)"

    mkdir -p -- "$LOCAL_BIN"
    ln -sfn -- "$OPENCODE_HOME_BIN" "$LOCAL_BIN/opencode"
    log "Installed $raw_version -> symlinked $LOCAL_BIN/opencode -> $OPENCODE_HOME_BIN"
}

# ------------------------------------------------------------------------------
# Phase 6: Pure Wayland/Hyprland & Shell PATH Wiring
# ------------------------------------------------------------------------------
phase_wire_path() {
    log "Phase 6/8: Wiring Wayland/Hyprland environment.d and shell PATH..."

    local env_d_file="$OPENCODE_ENV_D_DIR/50-opencode-path.conf"
    local env_d_content
    env_d_content="$(printf '# Managed by opencode_setup/setup.sh\nPATH=${HOME}/.local/bin:${PATH}\n')"
    atomic_write_if_changed "$env_d_file" "$env_d_content" 0

    if command -v bash >/dev/null 2>&1 && { [[ -f "$HOME/.bashrc" ]] || [[ "${SHELL:-}" == */bash ]]; }; then
        ensure_shell_rc_path_block "$HOME/.bashrc"
    fi

    local zshrc_path="${ZDOTDIR:-$HOME}/.zshrc"
    if command -v zsh >/dev/null 2>&1 && { [[ -f "$zshrc_path" ]] || [[ "${SHELL:-}" == */zsh ]]; }; then
        ensure_shell_rc_path_block "$zshrc_path"
    fi

    if command -v fish >/dev/null 2>&1; then
        local fish_conf="$XDG_CONFIG_HOME/fish/conf.d/50-opencode-path.fish"
        local fish_content
        fish_content="$(printf '# Managed by opencode_setup/setup.sh\nfish_add_path -g "$HOME/.local/bin"\n')"
        atomic_write_if_changed "$fish_conf" "$fish_content" 0
    fi
}

# ------------------------------------------------------------------------------
# Phase 7: Atomic Native V2 opencode.json Convergence
# ------------------------------------------------------------------------------
phase_deploy_config() {
    log "Phase 7/8: Converging native V2 opencode.json (no cli.json generated)..."

    mkdir -p -- "$OPENCODE_CONFIG_DIR_CANONICAL"
    local config_file="$OPENCODE_CONFIG_DIR_CANONICAL/opencode.json"

    local config_content
    read -r -d '' config_content <<EOF || true
{
  "\$schema": "https://opencode.ai/config.json",
  "providers": {
    "lmstudio": {
      "name": "LM Studio",
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": {
        "baseURL": "${LMSTUDIO_BASE_URL}"
      },
      "models": {
        "nanbeige4.1-3b": {
          "name": "Nanbeige 4.1 3B",
          "limit": {
            "context": ${LMSTUDIO_CTX},
            "output": ${LMSTUDIO_OUT}
          }
        },
        "glm-4.7-flash": {
          "name": "GLM 4.7 Flash",
          "limit": {
            "context": ${LMSTUDIO_CTX},
            "output": ${LMSTUDIO_OUT}
          }
        }
      }
    },
    "inkling": {
      "name": "Inkling (llama-server)",
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": {
        "baseURL": "${INKLING_BASE_URL}"
      },
      "models": {
        "${INKLING_MODEL_ID}": {
          "name": "Inkling 2 Mini",
          "limit": {
            "context": ${INKLING_CTX},
            "output": ${INKLING_OUT}
          }
        }
      }
    }
  },
  "permissions": [
    {
      "action": "shell",
      "resource": "*",
      "effect": "allow"
    },
    {
      "action": "edit",
      "resource": "*",
      "effect": "allow"
    },
    {
      "action": "read",
      "resource": "*",
      "effect": "allow"
    },
    {
      "action": "external_directory",
      "resource": "*",
      "effect": "allow"
    },
    {
      "action": "doom_loop",
      "resource": "*",
      "effect": "allow"
    }
  ]
}
EOF

    # Append trailing newline for POSIX-clean JSON file.
    config_content="${config_content}"$'\n'
    atomic_write_if_changed "$config_file" "$config_content" 1
}

# ------------------------------------------------------------------------------
# Phase 8: Post-Install Self-Verification & Clean Daemon Shutdown
# ------------------------------------------------------------------------------
phase_verify() {
    log "Phase 8/8: Verifying OpenCode V2 binary, PATH resolution, and normalized config..."

    export PATH="$LOCAL_BIN:$PATH"
    hash -r

    local resolved_bin canonical_target
    resolved_bin="$(command -v opencode)" \
        || die "opencode not found on PATH after wiring $LOCAL_BIN"
    [[ "$resolved_bin" == "$LOCAL_BIN/opencode" ]] \
        || die "command -v opencode resolved to '$resolved_bin' instead of '$LOCAL_BIN/opencode'"

    canonical_target="$(readlink -f -- "$OPENCODE_HOME_BIN")"
    [[ "$(readlink -f -- "$resolved_bin")" == "$canonical_target" ]] \
        || die "$LOCAL_BIN/opencode does not resolve to $canonical_target"

    local -a all_path_bins=()
    mapfile -t all_path_bins < <(type -aP opencode)
    local b
    for b in "${all_path_bins[@]}"; do
        if [[ "$(readlink -f -- "$b")" != "$canonical_target" ]]; then
            die "Shadowed or duplicate opencode binary detected on PATH: $b"
        fi
    done

    local version_out
    version_out="$(opencode --version 2>&1)"
    [[ "$version_out" =~ (^|[^0-9])v?2\.[0-9]+\.[0-9]+ ]] \
        || die "Unexpected version from 'opencode --version': $version_out"
    log "Verified version: $version_out"

    VERIFICATION_ACTIVE=1
    local debug_cfg
    debug_cfg="$(opencode debug config 2>&1)" \
        || die "'opencode debug config' failed:\n$debug_cfg"

    # Assert neither provider nor custom model was silently dropped by V2 schema normalization.
    local -a expected_tokens=(
        '"lmstudio"'
        '"nanbeige4.1-3b"'
        '"glm-4.7-flash"'
        '"inkling"'
        "$INKLING_BASE_URL"
        "$LMSTUDIO_BASE_URL"
    )
    local token
    for token in "${expected_tokens[@]}"; do
        if ! grep -Fq -- "$token" <<< "$debug_cfg"; then
            printf '%s\n' "$debug_cfg" >&2
            die "Normalized 'opencode debug config' is missing expected token: $token (provider may have been silently dropped)"
        fi
    done

    # Quiesce any daemon spawned by `opencode debug config` so the user's first
    # interactive Hyprland session starts the service with the real Wayland env.
    stop_opencode_service
    VERIFICATION_ACTIVE=0

    log "Verified normalized providers (lmstudio + inkling) and stopped temporary daemon."
    log "Done. Open a new terminal (or re-login for environment.d) and run 'opencode'."
}

main() {
    parse_args "$@"
    phase_preflight
    phase_conflict_guard
    phase_arch_helpers
    phase_quiesce_and_purge
    phase_install_v2
    phase_wire_path
    phase_deploy_config
    phase_verify
}

main "$@"
