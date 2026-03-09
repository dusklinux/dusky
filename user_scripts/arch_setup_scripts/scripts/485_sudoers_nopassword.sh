#!/usr/bin/env bash
# ==============================================================================
#  SUDOERS NOPASSWD AUTOMATOR
#  Target: Arch Linux / Bash 5.3.9+
# ==============================================================================

set -o errexit
set -o nounset
set -o pipefail
shopt -s nullglob inherit_errexit

# ==============================================================================
#  USER CONFIGURATION
#  Add absolute paths to binaries you want to process automatically.
# ==============================================================================
declare -ar DEFAULT_BINARIES=(

#    "/usr/bin/powertop"
    "/usr/bin/papirus-folders"
)

declare -r SCRIPT_PATH="${BASH_SOURCE[0]}"
declare -r SCRIPT_NAME="${SCRIPT_PATH##*/}"
declare -r SUDOERS_DIR='/etc/sudoers.d'
declare -r LOCK_FILE="${SUDOERS_DIR}/.sudoers-nopasswd-automator.lock"
declare -r MANAGED_MARKER='# Managed by sudoers-nopasswd-automator'
declare -r SUDOERS_RUNAS='ALL:ALL'      # Preserves current behavior.
declare -r DROPIN_PREFIX='zzzz-nopasswd-'
declare -ri RC_SKIP=10

declare -ag TEMP_FILES=()
declare -gi LOCK_FD=-1

declare -g REQUESTED_USER=''
declare -ag CLI_BINARIES=()

if [[ -t 1 ]]; then
    declare -r RED=$'\e[1;31m'
    declare -r GREEN=$'\e[1;32m'
    declare -r YELLOW=$'\e[1;33m'
    declare -r BLUE=$'\e[1;34m'
    declare -r RESET=$'\e[0m'
else
    declare -r RED=''
    declare -r GREEN=''
    declare -r YELLOW=''
    declare -r BLUE=''
    declare -r RESET=''
fi

log_info()    { printf '%s[INFO]%s %s\n'    "${BLUE}"   "${RESET}" "$1"; }
log_success() { printf '%s[SUCCESS]%s %s\n' "${GREEN}"  "${RESET}" "$1"; }
log_warn()    { printf '%s[WARN]%s %s\n'    "${YELLOW}" "${RESET}" "$1"; }
log_error()   { printf '%s[ERROR]%s %s\n'   "${RED}"    "${RESET}" "$1" >&2; }

fail() {
    log_error "$1"
    exit "${2:-1}"
}

cleanup() {
    local tmp
    for tmp in "${TEMP_FILES[@]}"; do
        [[ -e "${tmp}" ]] || continue
        rm -f -- "${tmp}" || true
    done
}
trap cleanup EXIT

usage() {
    printf 'Usage: %s [--user USER] [/absolute/path/to/binary ...]\n' "${SCRIPT_NAME}"
    printf '       Or add binaries to DEFAULT_BINARIES inside the script.\n'
}

parse_cli() {
    REQUESTED_USER=''
    CLI_BINARIES=()

    while (( $# > 0 )); do
        case "$1" in
            -h|--help)
                usage
                exit 0
                ;;
            -u|--user)
                (( $# >= 2 )) || fail "Missing argument for $1"
                REQUESTED_USER="$2"
                shift 2
                ;;
            --)
                shift
                CLI_BINARIES+=("$@")
                break
                ;;
            -*)
                fail "Unknown option: $1"
                ;;
            *)
                CLI_BINARIES+=("$1")
                shift
                ;;
        esac
    done
}

auto_elevate() {
    (( EUID == 0 )) && return 0

    command -v sudo >/dev/null 2>&1 || {
        printf '[ERROR] sudo is required but not installed.\n' >&2
        exit 1
    }

    exec sudo -- "${BASH}" "${SCRIPT_PATH}" "$@"
    fail "Unable to elevate with sudo."
}

require_commands() {
    local cmd
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || fail "Required command not found: ${cmd}"
    done
}

acquire_lock() {
    exec {LOCK_FD}> "${LOCK_FILE}" || fail "Unable to open lock file: ${LOCK_FILE}"
    flock -x "${LOCK_FD}" || fail "Unable to acquire lock on: ${LOCK_FILE}"
}

resolve_target_user() {
    local requested_user="${1:-}"

    if [[ -n "${requested_user}" ]]; then
        printf '%s\n' "${requested_user}"
        return 0
    fi

    if [[ -n "${SUDO_USER-}" && "${SUDO_USER}" != 'root' ]]; then
        printf '%s\n' "${SUDO_USER}"
        return 0
    fi

    fail "Could not determine the target user automatically. Re-run with --user USER."
}

validate_target_user() {
    local target_user="$1"

    [[ -n "${target_user}" ]] || fail "Target user is empty."
    [[ "${target_user}" != *$'\n'* && "${target_user}" != *$'\r'* ]] || fail "Target user contains invalid characters."
    [[ "${target_user}" =~ ^[[:alnum:]_.-]+[$]?$ ]] || fail "Target user contains unsupported characters for a literal sudoers entry."
    id -u -- "${target_user}" >/dev/null 2>&1 || fail "Target user does not exist: ${target_user}"
}

is_active_dropin_name() {
    local base_name="$1"
    [[ "${base_name}" != *.* && "${base_name}" != *~ ]]
}

path_exists_or_symlink() {
    [[ -e "$1" || -L "$1" ]]
}

escape_sudoers_command() {
    local input="$1"
    local output=''
    local ch
    local i

    [[ "${input}" != *$'\n'* && "${input}" != *$'\r'* ]] || return 1

    for (( i = 0; i < ${#input}; i++ )); do
        ch="${input:i:1}"
        case "${ch}" in
            [[:alnum:]_./+-])
                output+="${ch}"
                ;;
            *)
                output+="\\${ch}"
                ;;
        esac
    done

    printf '%s\n' "${output}"
}

stable_hash() {
    local target_user="$1"
    local target_bin="$2"
    local hash_output

    hash_output="$(printf '%s\0%s' "${target_user}" "${target_bin}" | sha256sum)" || return 1
    printf '%s\n' "${hash_output%% *}"
}

sanitize_filename_fragment() {
    local input="$1"
    local output=''
    local ch=''
    local i
    local prev_dash=0

    for (( i = 0; i < ${#input}; i++ )); do
        ch="${input:i:1}"
        case "${ch}" in
            [[:alnum:]_])
                output+="${ch}"
                prev_dash=0
                ;;
            -)
                if (( ! prev_dash )); then
                    output+='-'
                    prev_dash=1
                fi
                ;;
            *)
                if (( ! prev_dash )); then
                    output+='-'
                    prev_dash=1
                fi
                ;;
        esac
    done

    while [[ "${output}" == -* ]]; do
        output="${output#-}"
    done
    while [[ "${output}" == *- ]]; do
        output="${output%-}"
    done

    [[ -n "${output}" ]] || output='item'
    printf '%s\n' "${output}"
}

build_simple_stem() {
    local target_user="$1"
    local target_bin="$2"
    local safe_user
    local safe_name

    safe_user="$(sanitize_filename_fragment "${target_user}")"
    safe_name="$(sanitize_filename_fragment "${target_bin##*/}")"

    printf '%s-%s\n' "${safe_user}" "${safe_name}"
}

build_extended_stem() {
    local target_user="$1"
    local target_bin="$2"
    local safe_user
    local safe_path

    safe_user="$(sanitize_filename_fragment "${target_user}")"
    safe_path="$(sanitize_filename_fragment "${target_bin#/}")"

    printf '%s-%s\n' "${safe_user}" "${safe_path}"
}

compose_basename() {
    local prefix="$1"
    local stem="$2"
    local suffix="$3"
    local -i max_len=240
    local -i keep_len=$(( max_len - ${#prefix} - ${#suffix} ))

    (( keep_len > 0 )) || keep_len=1

    printf '%s%s%s\n' "${prefix}" "${stem:0:keep_len}" "${suffix}"
}

file_is_managed() {
    local file_path="$1"
    local first_line=''

    IFS= read -r first_line < "${file_path}" || true
    [[ "${first_line}" == "${MANAGED_MARKER}" ]]
}

managed_file_matches_target() {
    local file_path="$1"
    local target_user="$2"
    local target_bin="$3"
    local line1=''
    local line2=''
    local line3=''

    {
        IFS= read -r line1 || true
        IFS= read -r line2 || true
        IFS= read -r line3 || true
    } < "${file_path}"

    [[ "${line1}" == "${MANAGED_MARKER}" &&
       "${line2}" == "# user=${target_user}" &&
       "${line3}" == "# command=${target_bin}" ]]
}

find_managed_matches() {
    local target_user="$1"
    local target_bin="$2"
    local -n out_matches="$3"
    local file
    local base_name

    out_matches=()

    for file in "${SUDOERS_DIR}"/*; do
        [[ -f "${file}" && ! -L "${file}" ]] || continue
        base_name="${file##*/}"
        is_active_dropin_name "${base_name}" || continue

        if managed_file_matches_target "${file}" "${target_user}" "${target_bin}"; then
            out_matches+=("${file}")
        fi
    done
}

find_exact_rule_locations() {
    local rule_line="$1"
    local -n out_locations="$2"
    local file
    local base_name

    out_locations=()

    if [[ -f /etc/sudoers ]] && grep -Fqx -- "${rule_line}" /etc/sudoers; then
        out_locations+=('/etc/sudoers')
    fi

    for file in "${SUDOERS_DIR}"/*; do
        [[ -f "${file}" && ! -L "${file}" ]] || continue
        base_name="${file##*/}"
        is_active_dropin_name "${base_name}" || continue

        if grep -Fqx -- "${rule_line}" "${file}"; then
            out_locations+=("${file}")
        fi
    done
}

allocate_dropin_file() {
    local target_user="$1"
    local target_bin="$2"
    local ignore_file="${3-}"

    local simple_stem
    local extended_stem
    local hash
    local short_hash
    local suffix
    local candidate
    local base_name
    local i

    simple_stem="$(build_simple_stem "${target_user}" "${target_bin}")"
    extended_stem="$(build_extended_stem "${target_user}" "${target_bin}")"
    hash="$(stable_hash "${target_user}" "${target_bin}")" || return 1
    short_hash="${hash:0:10}"

    base_name="$(compose_basename "${DROPIN_PREFIX}" "${simple_stem}" '')"
    candidate="${SUDOERS_DIR}/${base_name}"
    if [[ "${candidate}" == "${ignore_file}" ]] || ! path_exists_or_symlink "${candidate}"; then
        printf '%s\n' "${candidate}"
        return 0
    fi

    if [[ "${extended_stem}" != "${simple_stem}" ]]; then
        base_name="$(compose_basename "${DROPIN_PREFIX}" "${extended_stem}" '')"
        candidate="${SUDOERS_DIR}/${base_name}"
        if [[ "${candidate}" == "${ignore_file}" ]] || ! path_exists_or_symlink "${candidate}"; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    fi

    suffix="-${short_hash}"
    base_name="$(compose_basename "${DROPIN_PREFIX}" "${simple_stem}" "${suffix}")"
    candidate="${SUDOERS_DIR}/${base_name}"
    if [[ "${candidate}" == "${ignore_file}" ]] || ! path_exists_or_symlink "${candidate}"; then
        printf '%s\n' "${candidate}"
        return 0
    fi

    if [[ "${extended_stem}" != "${simple_stem}" ]]; then
        base_name="$(compose_basename "${DROPIN_PREFIX}" "${extended_stem}" "${suffix}")"
        candidate="${SUDOERS_DIR}/${base_name}"
        if [[ "${candidate}" == "${ignore_file}" ]] || ! path_exists_or_symlink "${candidate}"; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    fi

    for (( i = 1; i <= 99; i++ )); do
        printf -v suffix -- '-%s-%02d' "${short_hash}" "${i}"

        base_name="$(compose_basename "${DROPIN_PREFIX}" "${extended_stem}" "${suffix}")"
        candidate="${SUDOERS_DIR}/${base_name}"
        if [[ "${candidate}" == "${ignore_file}" ]] || ! path_exists_or_symlink "${candidate}"; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done

    return 1
}

process_binary() {
    local target_bin="$1"
    local target_user="$2"

    printf '%s\n' '--------------------------------------------------------------------------------'
    log_info "Processing: ${target_bin}"

    if [[ "${target_bin}" != /* ]]; then
        log_error "SECURITY VIOLATION: Path must be absolute. Skipping '${target_bin}'."
        return 1
    fi

    if [[ ! -e "${target_bin}" && ! -L "${target_bin}" ]]; then
        log_warn "NOT INSTALLED: The target binary does not exist. Skipping '${target_bin}'."
        return "${RC_SKIP}"
    fi

    if [[ -L "${target_bin}" && ! -e "${target_bin}" ]]; then
        log_error "BROKEN SYMLINK: The target path exists but points nowhere. Refusing '${target_bin}'."
        return 1
    fi

    if [[ ! -f "${target_bin}" ]]; then
        log_error "INVALID TARGET: The target is not a regular file. Refusing '${target_bin}'."
        return 1
    fi

    if [[ ! -x "${target_bin}" ]]; then
        log_error "NOT EXECUTABLE: The target lacks execute permissions. Refusing '${target_bin}'."
        return 1
    fi

    if [[ "${target_bin}" == *$'\n'* || "${target_bin}" == *$'\r'* ]]; then
        log_error "INVALID PATH: Newlines are not supported in sudoers command paths. Skipping '${target_bin}'."
        return 1
    fi

    local escaped_bin
    escaped_bin="$(escape_sudoers_command "${target_bin}")" || {
        log_error "FAILED: Could not safely encode the sudoers command path. Skipping '${target_bin}'."
        return 1
    }

    local rule_line
    printf -v rule_line '%s ALL=(%s) NOPASSWD: %s' "${target_user}" "${SUDOERS_RUNAS}" "${escaped_bin}"

    local desired_content
    printf -v desired_content '%s\n# user=%s\n# command=%s\n%s\n' \
        "${MANAGED_MARKER}" \
        "${target_user}" \
        "${target_bin}" \
        "${rule_line}"

    local -a managed_matches=()
    find_managed_matches "${target_user}" "${target_bin}" managed_matches

    local drop_in_file=''
    local current_managed_file=''
    local preferred_file=''
    local old_file_to_remove=''

    case "${#managed_matches[@]}" in
        0)
            local -a exact_rule_locations=()
            find_exact_rule_locations "${rule_line}" exact_rule_locations

            if (( ${#exact_rule_locations[@]} > 0 )); then
                if (( ${#exact_rule_locations[@]} == 1 )); then
                    log_success "Rule already present in ${exact_rule_locations[0]##*/}. Leaving unchanged."
                else
                    log_warn "Rule already present in multiple locations. Leaving unchanged."
                fi
                return 0
            fi

            drop_in_file="$(allocate_dropin_file "${target_user}" "${target_bin}")" || {
                log_error "FAILED: Could not allocate a safe sudoers drop-in filename."
                return 1
            }

            log_info "Allocating file: ${drop_in_file##*/}"
            ;;
        1)
            current_managed_file="${managed_matches[0]}"

            preferred_file="$(allocate_dropin_file "${target_user}" "${target_bin}" "${current_managed_file}")" || {
                log_error "FAILED: Could not allocate a safe sudoers drop-in filename."
                return 1
            }

            if [[ "${preferred_file}" != "${current_managed_file}" ]]; then
                drop_in_file="${preferred_file}"
                old_file_to_remove="${current_managed_file}"
                log_info "Migrating managed rule to ${drop_in_file##*/}"
            else
                drop_in_file="${current_managed_file}"
                log_info "Idempotency trigger: Existing managed rule found at ${drop_in_file##*/}"
            fi
            ;;
        *)
            log_error "CONFLICT: Multiple managed files match the same user and command."
            local conflicting_file
            for conflicting_file in "${managed_matches[@]}"; do
                log_error "Conflicting file: ${conflicting_file}"
            done
            return 1
            ;;
    esac

    if [[ -L "${drop_in_file}" || ( -e "${drop_in_file}" && ! -f "${drop_in_file}" ) ]]; then
        log_error "REFUSING TO WRITE: Target path is not a regular non-symlink file: ${drop_in_file}"
        return 1
    fi

    local tmp_file
    tmp_file="$(mktemp --tmpdir="${SUDOERS_DIR}" ".${SCRIPT_NAME}.XXXXXX")" || {
        log_error "FAILED: Could not create a staging file in ${SUDOERS_DIR}."
        return 1
    }
    TEMP_FILES+=("${tmp_file}")

    printf '%s' "${desired_content}" > "${tmp_file}" || {
        log_error "FAILED: Could not write the staging sudoers file."
        return 1
    }

    log_info "Verifying syntax via visudo..."
    visudo -cf "${tmp_file}" >/dev/null 2>&1 || {
        log_error "SYNTAX CHECK FAILED: visudo rejected the generated rule. Skipping."
        return 1
    }

    if [[ -e "${drop_in_file}" ]]; then
        local cmp_status=0

        if cmp -s -- "${tmp_file}" "${drop_in_file}"; then
            log_success "Rule already up to date for ${target_bin##*/}."
            return 0
        else
            cmp_status=$?
            if (( cmp_status > 1 )); then
                log_error "FAILED: Could not compare staged content with ${drop_in_file}."
                return 1
            fi
        fi

        if ! file_is_managed "${drop_in_file}"; then
            log_error "REFUSING TO OVERWRITE: ${drop_in_file##*/} exists but is not managed by this script."
            return 1
        fi

        log_info "Updating managed rule: ${drop_in_file##*/}"
    else
        log_info "Deploying new managed rule: ${drop_in_file##*/}"
    fi

    chown root:root "${tmp_file}" || {
        log_error "FAILED: Could not set owner on the staging sudoers file."
        return 1
    }

    chmod 0440 "${tmp_file}" || {
        log_error "FAILED: Could not set permissions on the staging sudoers file."
        return 1
    }

    mv -fT -- "${tmp_file}" "${drop_in_file}" || {
        log_error "FAILED: Could not atomically deploy ${drop_in_file##*/}."
        return 1
    }

    if [[ -n "${old_file_to_remove}" && "${old_file_to_remove}" != "${drop_in_file}" ]]; then
        rm -f -- "${old_file_to_remove}" || log_warn "Could not remove old managed file: ${old_file_to_remove##*/}"
    fi

    log_success "Successfully deployed NOPASSWD for ${target_bin##*/}."
}

main() {
    parse_cli "$@"
    auto_elevate "$@"

    require_commands visudo sha256sum mktemp flock mv chmod chown cmp id grep rm

    [[ -d "${SUDOERS_DIR}" ]] || fail "CRITICAL: ${SUDOERS_DIR} does not exist."
    [[ -w "${SUDOERS_DIR}" ]] || fail "CRITICAL: ${SUDOERS_DIR} is not writable."

    acquire_lock

    local target_user
    target_user="$(resolve_target_user "${REQUESTED_USER}")"
    validate_target_user "${target_user}"

    local -a execution_queue=("${DEFAULT_BINARIES[@]}")
    if (( ${#CLI_BINARIES[@]} > 0 )); then
        execution_queue+=("${CLI_BINARIES[@]}")
    fi

    if (( ${#execution_queue[@]} == 0 )); then
        usage
        exit 1
    fi

    local -A seen=()
    local -a unique_queue=()
    local bin

    for bin in "${execution_queue[@]}"; do
        if [[ -z "${seen[$bin]:-}" ]]; then
            unique_queue+=("${bin}")
            seen["$bin"]=1
        fi
    done

    local rc=0
    local -i failure_count=0
    local -i skipped_count=0

    for bin in "${unique_queue[@]}"; do
        if process_binary "${bin}" "${target_user}"; then
            :
        else
            rc=$?
            case "${rc}" in
                "${RC_SKIP}")
                    (( skipped_count++ )) || true
                    ;;
                *)
                    (( failure_count++ )) || true
                    ;;
            esac
        fi
    done

    printf '%s\n' '--------------------------------------------------------------------------------'
    if (( failure_count == 0 )); then
        if (( skipped_count == 0 )); then
            log_success "All binaries processed successfully."
        else
            log_warn "Completed successfully. ${skipped_count} missing binaries were skipped."
        fi
        exit 0
    fi

    log_warn "Processing completed with ${failure_count} error(s) and ${skipped_count} skipped binary/binaries."
    exit 1
}

main "$@"
