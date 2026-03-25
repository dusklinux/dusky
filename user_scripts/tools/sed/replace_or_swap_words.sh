#!/usr/bin/env bash
# ==============================================================================
# Script:     smart-replace.sh
# Purpose:    Intelligently find and replace text across directories while
#             teaching the user the underlying commands.
# Architect:  Optimized for Arch/Wayland (Stateless, High-Performance)
# ==============================================================================

set -Eeuo pipefail

# --- Formatting & Colors (ANSI-C Quoting) ---
readonly RED=$'\033[0;31m'
readonly GREEN=$'\033[0;32m'
readonly YELLOW=$'\033[0;33m'
readonly BLUE=$'\033[0;34m'
readonly CYAN=$'\033[0;36m'
readonly BOLD=$'\033[1m'
readonly RESET=$'\033[0m'

declare -ag TEMP_FILES=()

# --- Robust Error Handling ---

die() {
    local msg="$1"
    local code="${2:-1}"
    printf "\n%s[✖] %s%s\n" "${RED}" "${msg}" "${RESET}" >&2
    exit "${code}"
}

crash_handler() {
    local line_no=$1
    local command="$2"
    local code=$3
    printf "\n%s[!] CRASH: Command '%s' failed on line %d with exit code %d.%s\n" \
        "${RED}" "${command}" "${line_no}" "${code}" "${RESET}" >&2
}

cleanup_temp_files() {
    local temp_file
    for temp_file in "${TEMP_FILES[@]}"; do
        [[ -n "${temp_file}" ]] || continue
        rm -f -- "${temp_file}" 2>/dev/null || true
    done
}

make_temp_file() {
    local -n out_ref="$1"
    out_ref="$(mktemp)" || die "Failed to create temporary file." 1
    TEMP_FILES+=("${out_ref}")
}

trap 'crash_handler ${LINENO} "$BASH_COMMAND" $?' ERR
trap 'die "Script interrupted by user." 130' INT TERM
trap 'cleanup_temp_files' EXIT

# --- Dependency & String Helpers ---
require_commands() {
    local missing=0
    local dep
    for dep in "$@"; do
        if ! command -v "${dep}" >/dev/null 2>&1; then
            printf "%s[!] Missing dependency: %s%s\n" "${RED}" "${dep}" "${RESET}" >&2
            missing=1
        fi
    done
    (( missing == 0 )) || die "Required dependencies are missing for the selected mode." 1
}

choose_delimiter() {
    local combined="${1}${2}"
    local delim
    local candidates=('|' ':' '#' '%' '@' '!' '+' ',' ';' '=' '^' '~' '_')

    for delim in "${candidates[@]}"; do
        if [[ "${combined}" != *"${delim}"* ]]; then
            printf '%s' "${delim}"
            return 0
        fi
    done
    die "Unable to safely construct a substitution command. All delimiter candidates collide." 1
}

# Vim/Neovim Ex commands use '|' as a command separator, so it must never be
# used as the :substitute delimiter here.
choose_vim_delimiter() {
    local combined="${1}${2}"
    local delim
    local candidates=('/' ':' '#' '%' '@' '!' '+' ',' ';' '=' '^' '~' '_')

    for delim in "${candidates[@]}"; do
        if [[ "${combined}" != *"${delim}"* ]]; then
            printf '%s' "${delim}"
            return 0
        fi
    done
    die "Unable to safely construct a Vim substitution command. All Vim-safe delimiter candidates collide." 1
}

escape_delimiter() {
    local value="$1"
    local delim="$2"
    printf '%s' "${value//${delim}/\\${delim}}"
}

escape_sed_replacement() {
    local value="$1"
    local delim="$2"
    value="${value//\\/\\\\}"
    value="${value//&/\\&}"
    value="${value//${delim}/\\${delim}}"
    printf '%s' "${value}"
}

# --- Help & Instructions ---
show_help() {
    printf "%s%sSmart Replace Tool%s\n\n" "${BOLD}" "${CYAN}" "${RESET}"
    printf "A high-performance, stateless text replacement tool for Arch/Hyprland ecosystems.\n"
    printf "It executes bulk edits while teaching you the raw terminal commands.\n\n"
    printf "%sUsage:%s %s [OPTIONS] <'SEARCH_TERM'> <'REPLACE_TERM'> <TARGET_DIR>\n\n" "${YELLOW}" "${RESET}" "$0"
    printf "%sArguments:%s\n" "${YELLOW}" "${RESET}"
    printf "  %s<SEARCH_TERM>%s   The text or regex to find. Syntax depends on the selected mode.\n" "${CYAN}" "${RESET}"
    printf "  %s<REPLACE_TERM>%s  The literal text to replace matches with.\n" "${CYAN}" "${RESET}"
    printf "  %s<TARGET_DIR>%s    The directory to search within.\n\n" "${CYAN}" "${RESET}"
    printf "%sExecution Modes:%s\n" "${YELLOW}" "${RESET}"
    printf "  1. %sInteractive Neovim:%s ripgrep file discovery + Vim substitution with per-match confirmation.\n" "${GREEN}" "${RESET}"
    printf "  2. %sBatch Sed (Standard):%s GNU grep/sed BRE-style replacement.\n" "${GREEN}" "${RESET}"
    printf "  3. %sBatch Perl (Advanced):%s Perl regular-expression replacement.\n" "${GREEN}" "${RESET}"
    printf "  4. %sDry Run:%s Preview matches only using ripgrep.\n\n" "${GREEN}" "${RESET}"
}

print_lesson() {
    local description="$1"
    local command="$2"
    printf "\n%s%s=== LEARN THE COMMAND ===%s\n" "${BOLD}" "${BLUE}" "${RESET}"
    printf "%s%s%s\n" "${YELLOW}" "${description}" "${RESET}"
    printf "%s$ %s%s\n\n" "${CYAN}" "${command}" "${RESET}"
}

# --- Core Logic ---
main() {
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
        show_help
        exit 0
    fi

    if [[ $# -ne 3 ]]; then
        printf "%s[!] Invalid arguments.%s\n\n" "${RED}" "${RESET}" >&2
        show_help
        die "Execution aborted due to missing arguments." 1
    fi

    local search="$1"
    local replace="$2"
    local target_dir="$3"

    [[ -d "${target_dir}" ]] || die "Target directory '${target_dir}' does not exist." 1
    [[ -n "${search}" ]] || die "Search term cannot be empty to prevent accidental overrides." 1
    if [[ "${search}" == *$'\n'* || "${replace}" == *$'\n'* ]]; then
        die "Embedded newlines in search or replacement text are not supported." 1
    fi

    printf "\n%sChoose execution method for '%s' in '%s':%s\n" "${BOLD}" "${search}" "${target_dir}" "${RESET}"
    printf "  %s[1]%s Interactive (Neovim step-by-step confirmation)\n" "${CYAN}" "${RESET}"
    printf "  %s[2]%s Batch All   (Fast 'sed' replacement)\n" "${CYAN}" "${RESET}"
    printf "  %s[3]%s Batch Perl  (Advanced regex replacement)\n" "${CYAN}" "${RESET}"
    printf "  %s[4]%s Dry Run     (Show matches only)\n" "${CYAN}" "${RESET}"
    printf "  %s[q]%s Quit\n" "${RED}" "${RESET}"

    local choice
    printf "\n> "
    read -r choice || die "No menu selection received." 1

    case "${choice}" in
        1)
            require_commands rg nvim

            local vim_delim filelist rc

            vim_delim="$(choose_vim_delimiter "${search}" "${replace}")"
            make_temp_file filelist

            if rg -l -0 -- "${search}" "${target_dir}" > "${filelist}"; then
                :
            else
                rc=$?
                case "${rc}" in
                    1)
                        printf "%s[i] No instances of '%s' found in '%s'. Exiting cleanly.%s\n" \
                            "${GREEN}" "${search}" "${target_dir}" "${RESET}"
                        exit 0
                        ;;
                    *)
                        die "ripgrep failed while collecting matching files for interactive mode." "${rc}"
                        ;;
                esac
            fi

            print_lesson \
                "Collect a null-delimited file list first, and pass patterns via environment variables so shell quoting and ARG_MAX limits are never a problem:" \
                "tmp=\$(mktemp) && rg -l -0 -- <PATTERN> <DIR> > \"\$tmp\" && SMART_REPLACE_SEARCH=\"<PATTERN>\" SMART_REPLACE_REPLACE=\"<TEXT>\" SMART_REPLACE_DELIM=\"<DELIM>\" SMART_REPLACE_FILELIST=\"\$tmp\" nvim -c '<load arglist>' -c '<safe argdo substitution>'"

            printf "%s[i] Launching Neovim...%s\n" "${GREEN}" "${RESET}"

            if SMART_REPLACE_SEARCH="${search}" \
               SMART_REPLACE_REPLACE="${replace}" \
               SMART_REPLACE_DELIM="${vim_delim}" \
               SMART_REPLACE_FILELIST="${filelist}" \
               nvim \
                   -c 'lua local f = assert(io.open(vim.env.SMART_REPLACE_FILELIST, "rb")); local data = assert(f:read("*a")); f:close(); local paths = {}; for path in data:gmatch("([^%z]+)%z") do paths[#paths + 1] = path end; assert(#paths > 0, "no files to edit"); vim.api.nvim_cmd({ cmd = "args", args = paths }, {}); vim.cmd("first")' \
                   -c 'execute "argdo %s" . $SMART_REPLACE_DELIM . escape($SMART_REPLACE_SEARCH, $SMART_REPLACE_DELIM) . $SMART_REPLACE_DELIM . escape($SMART_REPLACE_REPLACE, "\\" . $SMART_REPLACE_DELIM . "&~") . $SMART_REPLACE_DELIM . "gce | update"'; then
                printf "%s[✔] Neovim session completed.%s\n" "${GREEN}" "${RESET}"
            else
                rc=$?
                die "Neovim exited with an error." "${rc}"
            fi
            ;;
        2)
            require_commands grep sed xargs

            local sed_delim sed_search sed_replace sed_filelist rc

            sed_delim="$(choose_delimiter "${search}" "${replace}")"
            sed_search="$(escape_delimiter "${search}" "${sed_delim}")"
            sed_replace="$(escape_sed_replacement "${replace}" "${sed_delim}")"
            make_temp_file sed_filelist

            if grep -rIlZ -D skip -e "${search}" -- "${target_dir}" > "${sed_filelist}"; then
                :
            else
                rc=$?
                case "${rc}" in
                    1)
                        printf "%s[i] No instances of '%s' found in '%s'. Exiting cleanly.%s\n" \
                            "${GREEN}" "${search}" "${target_dir}" "${RESET}"
                        exit 0
                        ;;
                    *)
                        die "grep failed while collecting matching files for sed mode." "${rc}"
                        ;;
                esac
            fi

            print_lesson \
                "Use GNU grep's recursive BRE search to build a null-delimited file list, then stream it into sed safely via xargs:" \
                "grep -rIlZ -D skip -e <PATTERN> -- <DIR> | xargs -0r sed -i -e 's<DELIM><PATTERN><DELIM><TEXT><DELIM>g' --"

            printf "%s[i] Executing batch sed replacement...%s\n" "${GREEN}" "${RESET}"

            if xargs -0r sed -i -e "s${sed_delim}${sed_search}${sed_delim}${sed_replace}${sed_delim}g" -- < "${sed_filelist}"; then
                printf "%s[✔] Replacement complete.%s\n" "${GREEN}" "${RESET}"
            else
                rc=$?
                die "sed failed while applying replacements." "${rc}"
            fi
            ;;
        3)
            require_commands perl xargs

            local perl_filelist rc

            make_temp_file perl_filelist

            if SMART_REPLACE_SEARCH="${search}" perl -MFile::Find -e '
                use strict;
                use warnings;
                BEGIN { $SIG{__WARN__} = sub { die @_ } }
                binmode STDOUT;

                my $re = eval { qr/$ENV{SMART_REPLACE_SEARCH}/ } or die $@;

                File::Find::find(
                    {
                        no_chdir => 1,
                        wanted => sub {
                            return unless -f $_;
                            return if -B _;
                            open my $fh, "<", $_ or die "open($_): $!";
                            while (my $line = <$fh>) {
                                if ($line =~ /$re/) {
                                    print $File::Find::name, "\0";
                                    last;
                                }
                            }
                            close $fh or die "close($_): $!";
                        },
                    },
                    @ARGV,
                );
            ' -- "${target_dir}" > "${perl_filelist}"; then
                :
            else
                rc=$?
                die "Perl failed while collecting matching files for Perl mode." "${rc}"
            fi

            if [[ ! -s "${perl_filelist}" ]]; then
                printf "%s[i] No instances of '%s' found in '%s'. Exiting cleanly.%s\n" \
                    "${GREEN}" "${search}" "${target_dir}" "${RESET}"
                exit 0
            fi

            print_lesson \
                "Use Perl to walk the tree with Perl-regex matching, emit a null-delimited file list, then hand it to in-place Perl editing:" \
                "perl -MFile::Find -e '<walk tree and print NUL-delimited matches>' -- <DIR> | env SMART_REPLACE... xargs -0r perl -pi -e 's/\$ENV{SEARCH}/\$ENV{REPLACE}/g' --"

            printf "%s[i] Executing batch perl replacement...%s\n" "${GREEN}" "${RESET}"

            if SMART_REPLACE_SEARCH="${search}" SMART_REPLACE_REPLACE="${replace}" \
               xargs -0r perl -pi -e 'BEGIN { $search = eval { qr/$ENV{SMART_REPLACE_SEARCH}/ } or die $@; $replace = $ENV{SMART_REPLACE_REPLACE}; } s/$search/$replace/g' -- < "${perl_filelist}"; then
                printf "%s[✔] Replacement complete.%s\n" "${GREEN}" "${RESET}"
            else
                rc=$?
                die "Perl failed while applying replacements." "${rc}"
            fi
            ;;
        4)
            require_commands rg

            local rc

            print_lesson \
                "Preview matches exactly as they are without modifying the system:" \
                "rg --color=always -- <PATTERN> <TARGET_DIR>"

            printf "%s[i] Executing dry run...%s\n\n" "${GREEN}" "${RESET}"

            if rg --color=always -- "${search}" "${target_dir}"; then
                :
            else
                rc=$?
                case "${rc}" in
                    1)
                        printf "%s[i] No instances of '%s' found in '%s'. Exiting cleanly.%s\n" \
                            "${GREEN}" "${search}" "${target_dir}" "${RESET}"
                        exit 0
                        ;;
                    *)
                        die "ripgrep failed during dry run." "${rc}"
                        ;;
                esac
            fi
            ;;
        q|Q)
            printf "%s[i] Aborting as requested.%s\n" "${YELLOW}" "${RESET}"
            exit 0
            ;;
        *)
            die "Invalid choice." 1
            ;;
    esac
}

main "$@"
