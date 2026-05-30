#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-/sys/kernel/mm/transparent_hugepage}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPORT_FILE="${REPORT_FILE:-$SCRIPT_DIR/report.txt}"

if [[ ! -d "$ROOT" ]]; then
  printf 'Error: %s does not exist\n' "$ROOT" >&2
  exit 1
fi

if [[ ! -r "$ROOT" ]]; then
  printf 'Error: %s is not readable\n' "$ROOT" >&2
  exit 1
fi

mkdir -p -- "$(dirname -- "$REPORT_FILE")"
: > "$REPORT_FILE"
exec 3>>"$REPORT_FILE"

if [[ -t 1 ]] && command -v tput >/dev/null 2>&1; then
  BOLD="$(tput bold)"
  DIM="$(tput dim)"
  RED="$(tput setaf 1)"
  GREEN="$(tput setaf 2)"
  YELLOW="$(tput setaf 3)"
  CYAN="$(tput setaf 6)"
  RESET="$(tput sgr0)"
else
  BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; CYAN=""; RESET=""
fi

trap 'printf "%sError:%s line %d: command failed.\n" "$RED" "$RESET" "$LINENO" >&2' ERR

width() {
  printf '%*s\n' "${COLUMNS:-88}" '' | tr ' ' '═'
}

say() {
  printf '%s\n' "$1"
  printf '%s\n' "$1" >&3
}

say2() {
  printf '%b\n' "$1"
  printf '%s\n' "$2" >&3
}

section() {
  say2 "\n${BOLD}${CYAN}$1${RESET}" "$1"
  local w
  w="$(width)"
  say "$w"
}

highlight_active() {
  sed -E "s/\[([^]]+)\]/${GREEN}[\1]${RESET}/g"
}

plain_active() {
  sed -E 's/\[([^]]+)\]/[\1]/g'
}

relpath() {
  printf '%s' "${1#$ROOT/}"
}

dir_label() {
  local dir="$1"
  if [[ "$dir" == "$ROOT" ]]; then
    printf 'root'
  else
    printf '%s' "${dir#$ROOT/}"
  fi
}

read_value() {
  local file="$1" value
  IFS= read -r value < "$file" || return 1
  value="${value//$'\r'/}"
  value="${value//$'\n'/ }"
  printf '%s' "$value"
}

extract_active() {
  local s="$1"
  local out=()
  while [[ "$s" =~ \[([^]]+)\] ]]; do
    out+=("${BASH_REMATCH[1]}")
    s="${s#*\[${BASH_REMATCH[1]}\]}"
  done
  ((${#out[@]})) || return 1
  local IFS=', '
  printf '%s' "${out[*]}"
}

show_summary() {
  section "System summary"
  local file_count
  file_count=$(find "$ROOT" -type f | wc -l | tr -d " ")
  say "Root:   $ROOT"
  say "Files:  $file_count"
  say "Host:   $(hostname 2>/dev/null || printf unknown)"
  say "Kernel: $(uname -r)"
  say "Bash:   $BASH_VERSION"
  say "Time:   $(date '+%Y-%m-%d %H:%M:%S %Z')"
  say "User:   $(id -un 2>/dev/null || printf unknown)"
}

show_tree() {
  section "Directory layout"
  while IFS= read -r -d '' path; do
    say "  $(relpath "$path")"
  done < <(find "$ROOT" \( -type d -o -type f \) -print0 | sort -z)
}

show_files_grouped() {
  section "Transparent Huge Pages values"

  mapfile -d '' -t files < <(find "$ROOT" -type f -print0 | sort -z)
  local current_dir=""
  local file rel dir value active

  for file in "${files[@]}"; do
    dir="$(dirname -- "$file")"
    rel="$(relpath "$file")"

    if [[ "$dir" != "$current_dir" ]]; then
      current_dir="$dir"
      local label
      label="$(dir_label "$current_dir")"
      say2 "\n${BOLD}${YELLOW}${label}${RESET}" "$label"
      say "$(width)"
    fi

    value="$(read_value "$file")"
    say2 "  ▸ $rel" "  ▸ $rel"
    say2 "    $(printf '%s' "$value" | highlight_active)" "    $(printf '%s' "$value" | plain_active)"

    if active="$(extract_active "$value" 2>/dev/null)"; then
      say2 "    active: $active" "    active: $active"
    fi
    say ""
  done
}

show_focus() {
  section "Quick focus"
  local paths=(
    "$ROOT/enabled"
    "$ROOT/defrag"
    "$ROOT/shmem_enabled"
    "$ROOT/use_zero_page"
    "$ROOT/hpage_pmd_size"
    "$ROOT/khugepaged/defrag"
    "$ROOT/khugepaged/alloc_sleep_millisecs"
    "$ROOT/khugepaged/scan_sleep_millisecs"
    "$ROOT/khugepaged/pages_to_scan"
    "$ROOT/khugepaged/max_ptes_none"
    "$ROOT/khugepaged/max_ptes_swap"
    "$ROOT/khugepaged/max_ptes_shared"
    "$ROOT/khugepaged/full_scans"
    "$ROOT/khugepaged/pages_collapsed"
    "$ROOT/khugepaged/pages_skipped"
    "$ROOT/khugepaged/pages_shared"
  )

  local p value
  for p in "${paths[@]}"; do
    [[ -r "$p" ]] || continue
    value="$(read_value "$p")"
    say "$(relpath "$p") = $value"
  done
}

show_footer() {
  section "Saved report"
  say "$REPORT_FILE"
}

main() {
  show_summary
  show_tree
  show_files_grouped
  show_focus
  show_footer
}

main "$@"
