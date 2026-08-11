#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGS_DIR="$ROOT/.config/ags"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

assert_file() {
    [[ -f "$1" ]] || fail "missing file: ${1#$ROOT/}"
}

assert_dir() {
    [[ -d "$1" ]] || fail "missing directory: ${1#$ROOT/}"
}

assert_absent() {
    [[ ! -e "$1" ]] || fail "generated artifact should not be tracked: ${1#$ROOT/}"
}

assert_file "$AGS_DIR/app.tsx"
assert_file "$AGS_DIR/style.css"
assert_file "$AGS_DIR/install.sh"
assert_file "$AGS_DIR/README.md"
assert_file "$AGS_DIR/components/PopupWindows.tsx"
assert_file "$AGS_DIR/components/Workspaces.tsx"
assert_file "$AGS_DIR/lib/dusky.ts"
assert_file "$AGS_DIR/lib/workspacePreviewState.ts"
assert_file "$AGS_DIR/scripts/capture_window_preview.sh"
assert_file "$AGS_DIR/styles/fallback.css"
assert_file "$AGS_DIR/tests/test_contract.py"

[[ -x "$AGS_DIR/app.tsx" ]] || fail ".config/ags/app.tsx must be executable"
[[ -x "$AGS_DIR/install.sh" ]] || fail ".config/ags/install.sh must be executable"
[[ -x "$AGS_DIR/scripts/capture_window_preview.sh" ]] || fail ".config/ags/scripts/capture_window_preview.sh must be executable"

assert_absent "$AGS_DIR/@girs"
assert_absent "$AGS_DIR/node_modules"
assert_absent "$AGS_DIR/.pytest_cache"
assert_absent "$AGS_DIR/tests/__pycache__"
assert_absent "$AGS_DIR/docs/superpowers"

grep -F 'ADAPTIVE_ENTRY="${HOME}/.config/ags/app.tsx"' "$ROOT/user_scripts/bar/bar_switch.sh" >/dev/null \
    || fail "bar switcher must launch the repo-owned AGS entry path"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/home/.config"
cp -a "$AGS_DIR" "$TMP/home/.config/ags"

cat > "$TMP/ags" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
chmod +x "$TMP/ags"

HOME="$TMP/home" PATH="$TMP:$PATH" "$TMP/home/.config/ags/install.sh" >/dev/null
test -f "$TMP/home/.config/ags/app.tsx"
test -f "$TMP/home/.config/ags/install.sh"
test -f "$TMP/home/.config/ags/.adaptive-glass-managed"
test -x "$TMP/home/.config/ags/scripts/capture_window_preview.sh"

printf 'adaptive glass source ownership: PASS\n'
