#!/usr/bin/env bash
set -euo pipefail

AGS_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$AGS_ROOT/../.." && pwd)"
ORIGINAL_PATH="$PATH"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

assert_file() {
    [[ -f "$1" ]] || fail "missing file: $1"
}

assert_not_file() {
    [[ ! -f "$1" ]] || fail "unexpected file: $1"
}

assert_log_contains() {
    local needle="$1"
    grep -F -- "$needle" "$CALL_LOG" >/dev/null || {
        printf 'call log:\n' >&2
        sed 's/^/  /' "$CALL_LOG" >&2 || true
        fail "expected call log to contain: $needle"
    }
}

assert_log_not_contains() {
    local needle="$1"
    if grep -F -- "$needle" "$CALL_LOG" >/dev/null; then
        printf 'call log:\n' >&2
        sed 's/^/  /' "$CALL_LOG" >&2 || true
        fail "expected call log not to contain: $needle"
    fi
}

write_gjs_stub() {
    cat > "$TEST_ROOT/bin/gjs" <<'STUB'
#!/usr/bin/env bash
printf 'gjs %s\n' "$*" >> "$CALL_LOG"
[[ -f "$TEST_ROOT/gi-ok" ]]
STUB
    chmod +x "$TEST_ROOT/bin/gjs"
}

write_ags_stub() {
    cat > "$TEST_ROOT/bin/ags" <<'STUB'
#!/usr/bin/env bash
printf 'ags %s\n' "$*" >> "$CALL_LOG"
exit 0
STUB
    chmod +x "$TEST_ROOT/bin/ags"
}

setup_fake_home() {
    TEST_ROOT="$(mktemp -d)"
    export TEST_ROOT
    export HOME="$TEST_ROOT/home"
    export XDG_RUNTIME_DIR="$TEST_ROOT/run"
    export PATH="$TEST_ROOT/bin:$ORIGINAL_PATH"
    export CALL_LOG="$TEST_ROOT/calls.log"

    mkdir -p \
        "$HOME/.config" \
        "$HOME/.config/waybar" \
        "$HOME/user_scripts/bar" \
        "$HOME/user_scripts/hypr/defaults/edit_here" \
        "$TEST_ROOT/bin" \
        "$XDG_RUNTIME_DIR"

    : > "$CALL_LOG"
    printf 'waybar-sentinel\n' > "$HOME/.config/waybar/KEEP_ME"

    cat > "$HOME/user_scripts/bar/bar_switch.sh" <<'STUB'
#!/usr/bin/env bash
printf 'bar_switch %s\n' "$*" >> "$CALL_LOG"
mkdir -p "$HOME/.config/dusky/settings"
if [[ "${1:-}" == "adaptive-glass" ]]; then
    printf 'adaptive-glass\n' > "$HOME/.config/dusky/settings/active_bar"
elif [[ "${1:-}" == "waybar" ]]; then
    printf 'waybar\n' > "$HOME/.config/dusky/settings/active_bar"
fi
exit 0
STUB
    chmod +x "$HOME/user_scripts/bar/bar_switch.sh"

    cat > "$HOME/user_scripts/hypr/defaults/edit_here/autostart.lua" <<'STUB'
hl.on("hyprland.start", function()
    hl.exec_cmd("uwsm-app -- $HOME/user_scripts/bar/bar_switch.sh start")
end)
STUB

    cat > "$TEST_ROOT/bin/hyprctl" <<'STUB'
#!/usr/bin/env bash
printf 'hyprctl %s\n' "$*" >> "$CALL_LOG"
exit 0
STUB
    cat > "$TEST_ROOT/bin/grim" <<'STUB'
#!/usr/bin/env bash
printf 'grim %s\n' "$*" >> "$CALL_LOG"
exit 0
STUB
    cat > "$TEST_ROOT/bin/paru" <<'STUB'
#!/usr/bin/env bash
printf 'paru %s\n' "$*" >> "$CALL_LOG"
attempt_file="$TEST_ROOT/paru-attempts"
attempt=0
[[ -f "$attempt_file" ]] && attempt="$(cat "$attempt_file")"
attempt=$((attempt + 1))
printf '%s\n' "$attempt" > "$attempt_file"
if (( attempt >= 2 )); then
    cat > "$TEST_ROOT/bin/ags" <<'AGS'
#!/usr/bin/env bash
printf 'ags %s\n' "$*" >> "$CALL_LOG"
exit 0
AGS
    chmod +x "$TEST_ROOT/bin/ags"
    : > "$TEST_ROOT/gi-ok"
fi
exit 0
STUB
    cat > "$TEST_ROOT/bin/notify-send" <<'STUB'
#!/usr/bin/env bash
printf 'notify-send %s\n' "$*" >> "$CALL_LOG"
exit 0
STUB
    chmod +x "$TEST_ROOT/bin/"*

    write_gjs_stub
}

cleanup_fake_home() {
    rm -rf "${TEST_ROOT:-}"
}

run_install() {
    timeout 15 bash "$AGS_ROOT/install.sh" "$@"
}

test_check_mode_stops_when_required_dependencies_are_missing() {
    setup_fake_home
    trap cleanup_fake_home RETURN

    if run_install --check >"$TEST_ROOT/stdout" 2>"$TEST_ROOT/stderr"; then
        fail "--check should fail when required dependencies are missing"
    fi

    grep -F "Missing required Adaptive Glass dependencies" "$TEST_ROOT/stderr" >/dev/null \
        || fail "--check did not explain missing dependencies"
    assert_not_file "$HOME/.config/ags/.adaptive-glass-managed"
    assert_log_not_contains "paru"
}

test_auto_mode_retries_until_dependencies_verify_and_then_activates() {
    setup_fake_home
    trap cleanup_fake_home RETURN

    if ! run_install --auto --activate --max-retries 2 >"$TEST_ROOT/stdout" 2>"$TEST_ROOT/stderr"; then
        sed 's/^/stderr: /' "$TEST_ROOT/stderr" >&2 || true
        fail "--auto --activate should retry dependency installation and then succeed"
    fi

    assert_file "$HOME/.config/ags/.adaptive-glass-managed"
    assert_file "$HOME/.config/ags/app.tsx"
    assert_file "$HOME/.config/waybar/KEEP_ME"
    assert_log_contains "paru -S --needed --noconfirm"
    [[ "$(cat "$TEST_ROOT/paru-attempts")" == "2" ]] || fail "expected two paru attempts"
    assert_log_contains "ags types -u -d $HOME/.config/ags"
    assert_log_contains "bar_switch adaptive-glass"
    [[ "$(cat "$HOME/.config/dusky/settings/active_bar")" == "adaptive-glass" ]] \
        || fail "activate should persist adaptive-glass active_bar"
}

test_skip_deps_plain_install_copies_without_activating() {
    setup_fake_home
    trap cleanup_fake_home RETURN

    mkdir -p "$HOME/.config/ags"
    printf 'old ags config\n' > "$HOME/.config/ags/original.txt"

    if ! run_install --skip-deps --no-activate >"$TEST_ROOT/stdout" 2>"$TEST_ROOT/stderr"; then
        sed 's/^/stderr: /' "$TEST_ROOT/stderr" >&2 || true
        fail "--skip-deps --no-activate should copy without switching bars"
    fi

    assert_file "$HOME/.config/ags/.adaptive-glass-managed"
    assert_file "$HOME/.config/ags/app.tsx"
    assert_file "$HOME/.config/waybar/KEEP_ME"
    backup="$(find "$HOME/.config" -maxdepth 1 -type d -name 'ags.backup-*' -print -quit)"
    [[ -n "$backup" ]] || fail "unmanaged AGS config should be backed up"
    assert_file "$backup/original.txt"
    assert_log_not_contains "bar_switch"
    assert_not_file "$HOME/.config/dusky/settings/active_bar"
}

test_check_mode_stops_when_required_dependencies_are_missing
test_auto_mode_retries_until_dependencies_verify_and_then_activates
test_skip_deps_plain_install_copies_without_activating

printf 'adaptive glass public installer tests: PASS\n'
