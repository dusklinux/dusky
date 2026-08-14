#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_UNDER_TEST="${BAR_SWITCH_SCRIPT:-$REPO_ROOT/user_scripts/bar/bar_switch.sh}"
ORIGINAL_PATH="$PATH"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

assert_eq() {
    local want="$1"
    local got="$2"
    local label="$3"

    [[ "$got" == "$want" ]] || fail "$label: expected '$want', got '$got'"
}

assert_log_contains() {
    local needle="$1"
    local i

    for ((i = 0; i < 40; i++)); do
        grep -F -- "$needle" "$CALL_LOG" >/dev/null && return 0
        /usr/bin/sleep 0.05
    done

    printf 'call log:\n' >&2
    sed 's/^/  /' "$CALL_LOG" >&2 || true
    fail "expected call log to contain: $needle"
}

assert_log_not_contains() {
    local needle="$1"

    if grep -F -- "$needle" "$CALL_LOG" >/dev/null; then
        printf 'call log:\n' >&2
        sed 's/^/  /' "$CALL_LOG" >&2 || true
        fail "expected call log not to contain: $needle"
    fi
}

setup_fake_home() {
    unset AGS_LIST_RUNNING AGS_RUN_SLEEP
    unset PGREP_STATUS PGREP_STATUSES PGREP_WAYBAR_AFTER_DIRECT PGREP_WAYBAR_AFTER_SYSTEMD
    unset SYSTEMD_RUN_STATUS

    TEST_ROOT="$(mktemp -d)"
    export TEST_ROOT
    export HOME="$TEST_ROOT/home"
    export XDG_RUNTIME_DIR="$TEST_ROOT/run"
    export CALL_LOG="$TEST_ROOT/calls.log"
    export PATH="$TEST_ROOT/bin:$ORIGINAL_PATH"

    mkdir -p "$HOME/.config/ags" \
        "$HOME/.config/dusky/settings" \
        "$HOME/user_scripts/waybar" \
        "$XDG_RUNTIME_DIR" \
        "$TEST_ROOT/bin"

    : > "$CALL_LOG"
    : > "$HOME/.config/ags/app.tsx"

    cat > "$TEST_ROOT/bin/ags" <<'STUB'
#!/usr/bin/env bash
printf 'ags %s\n' "$*" >> "$CALL_LOG"
if [[ "${1:-}" == "list" ]]; then
    [[ "${AGS_LIST_RUNNING:-0}" == "1" ]] && printf 'dusky-adaptive-glass\n'
    exit 0
fi
if [[ "${1:-}" == "quit" ]]; then
    exit 0
fi
if [[ "${1:-}" == "run" && -n "${AGS_RUN_SLEEP:-}" ]]; then
    /usr/bin/sleep "$AGS_RUN_SLEEP"
fi
exit 0
STUB

    cat > "$TEST_ROOT/bin/pgrep" <<'STUB'
#!/usr/bin/env bash
printf 'pgrep %s\n' "$*" >> "$CALL_LOG"
if [[ " $* " == *" waybar "* && "${PGREP_WAYBAR_AFTER_DIRECT:-0}" == "1" && -f "$TEST_ROOT/waybar-direct-started" ]]; then
    exit 0
fi
if [[ " $* " == *" waybar "* && "${PGREP_WAYBAR_AFTER_SYSTEMD:-0}" == "1" && -f "$TEST_ROOT/waybar-systemd-started" ]]; then
    exit 0
fi
if [[ -n "${PGREP_STATUSES:-}" ]]; then
    seq_file="$TEST_ROOT/pgrep-statuses"
    if ! [[ -f "$seq_file" ]]; then
        printf '%s\n' "$PGREP_STATUSES" > "$seq_file"
    fi
    seq="$(cat "$seq_file")"
    status="${seq%%,*}"
    if [[ "$seq" == *","* ]]; then
        printf '%s\n' "${seq#*,}" > "$seq_file"
    else
        : > "$seq_file"
    fi
    exit "$status"
fi
exit "${PGREP_STATUS:-1}"
STUB

    cat > "$TEST_ROOT/bin/pkill" <<'STUB'
#!/usr/bin/env bash
printf 'pkill %s\n' "$*" >> "$CALL_LOG"
exit 0
STUB

    cat > "$TEST_ROOT/bin/setsid" <<'STUB'
#!/usr/bin/env bash
printf 'setsid %s\n' "$*" >> "$CALL_LOG"
exec "$@"
STUB

    cat > "$TEST_ROOT/bin/waybar" <<'STUB'
#!/usr/bin/env bash
printf 'waybar %s\n' "$*" >> "$CALL_LOG"
touch "$TEST_ROOT/waybar-direct-started"
exit 0
STUB

    cat > "$TEST_ROOT/bin/systemd-run" <<'STUB'
#!/usr/bin/env bash
printf 'systemd-run %s\n' "$*" >> "$CALL_LOG"
if [[ " $* " == *" waybar"* ]]; then
    touch "$TEST_ROOT/waybar-systemd-started"
fi
exit "${SYSTEMD_RUN_STATUS:-0}"
STUB

    cat > "$TEST_ROOT/bin/notify-send" <<'STUB'
#!/usr/bin/env bash
printf 'notify-send %s\n' "$*" >> "$CALL_LOG"
exit 0
STUB

    cat > "$TEST_ROOT/bin/sleep" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB

    cat > "$HOME/user_scripts/waybar/waybar_toggle.sh" <<'STUB'
#!/usr/bin/env bash
printf 'waybar_toggle %s\n' "$*" >> "$CALL_LOG"
exit 0
STUB

    chmod +x "$TEST_ROOT/bin/"* "$HOME/user_scripts/waybar/waybar_toggle.sh"
}

cleanup_fake_home() {
    rm -rf "${TEST_ROOT:-}"
}

run_switch() {
    local status

    if ! [[ -x "$SCRIPT_UNDER_TEST" ]]; then
        printf 'bar switch script missing or not executable: %s\n' "$SCRIPT_UNDER_TEST" >&2
        return 127
    fi

    bash "$SCRIPT_UNDER_TEST" "$@"
    status=$?
    /usr/bin/sleep 0.05
    return "$status"
}

test_toggle_from_waybar_starts_adaptive_glass() {
    setup_fake_home
    trap cleanup_fake_home RETURN

    if ! run_switch toggle >"$TEST_ROOT/stdout" 2>"$TEST_ROOT/stderr"; then
        sed 's/^/stderr: /' "$TEST_ROOT/stderr" >&2 || true
        fail "toggle from waybar should start adaptive-glass"
    fi

    assert_eq "adaptive-glass" "$(cat "$HOME/.config/dusky/settings/active_bar")" "toggle state"
    assert_log_contains "ags run $HOME/.config/ags/app.tsx"
    assert_log_not_contains "novabar"
}

test_legacy_novabar_state_normalizes_to_adaptive_glass() {
    setup_fake_home
    trap cleanup_fake_home RETURN
    printf 'novabar\n' > "$HOME/.config/dusky/settings/active_bar"

    local output
    output="$(run_switch status)"

    assert_eq "adaptive-glass" "$(printf '%s\n' "$output" | sed -n '1p')" "status output"
    assert_eq "adaptive-glass" "$(cat "$HOME/.config/dusky/settings/active_bar")" "migrated state"
}

test_novabar_argument_is_rejected() {
    setup_fake_home
    trap cleanup_fake_home RETURN

    local stderr="$TEST_ROOT/stderr"
    if run_switch novabar >"$TEST_ROOT/stdout" 2>"$stderr"; then
        fail "novabar argument unexpectedly succeeded"
    fi

    grep -F "Unknown argument: 'novabar'" "$stderr" >/dev/null || fail "novabar rejection did not explain the unknown argument"
}

test_start_uses_saved_adaptive_glass_state() {
    setup_fake_home
    trap cleanup_fake_home RETURN
    printf 'adaptive-glass\n' > "$HOME/.config/dusky/settings/active_bar"

    if ! run_switch start >"$TEST_ROOT/stdout" 2>"$TEST_ROOT/stderr"; then
        sed 's/^/stderr: /' "$TEST_ROOT/stderr" >&2 || true
        fail "start should restore saved adaptive-glass state"
    fi

    assert_eq "adaptive-glass" "$(cat "$HOME/.config/dusky/settings/active_bar")" "start state"
    assert_log_contains "ags run $HOME/.config/ags/app.tsx"
}

test_adaptive_launch_does_not_leave_switch_lock_held() {
    setup_fake_home
    trap cleanup_fake_home RETURN
    export AGS_RUN_SLEEP=10

    if ! run_switch adaptive-glass >"$TEST_ROOT/start-stdout" 2>"$TEST_ROOT/start-stderr"; then
        sed 's/^/stderr: /' "$TEST_ROOT/start-stderr" >&2 || true
        fail "adaptive-glass launch should succeed"
    fi

    if ! run_switch status >"$TEST_ROOT/status-stdout" 2>"$TEST_ROOT/status-stderr"; then
        sed 's/^/stderr: /' "$TEST_ROOT/status-stderr" >&2 || true
        fail "status should not be blocked by the launched adaptive-glass process"
    fi
}

test_waybar_switch_stops_adaptive_glass() {
    setup_fake_home
    trap cleanup_fake_home RETURN
    printf 'adaptive-glass\n' > "$HOME/.config/dusky/settings/active_bar"
    export PGREP_WAYBAR_AFTER_SYSTEMD=1

    if ! run_switch waybar >"$TEST_ROOT/stdout" 2>"$TEST_ROOT/stderr"; then
        sed 's/^/stderr: /' "$TEST_ROOT/stderr" >&2 || true
        fail "switching to waybar should stop adaptive-glass"
    fi

    assert_eq "waybar" "$(cat "$HOME/.config/dusky/settings/active_bar")" "waybar state"
    assert_log_contains "ags quit --instance dusky-adaptive-glass"
    assert_log_contains "systemd-run --user --quiet --collect --unit=waybar-adaptive-glass -- waybar"
    assert_log_not_contains "waybar_toggle --on"
}

test_waybar_switch_falls_back_when_toggle_exits_without_a_waybar_process() {
    setup_fake_home
    trap cleanup_fake_home RETURN
    printf 'adaptive-glass\n' > "$HOME/.config/dusky/settings/active_bar"
    export SYSTEMD_RUN_STATUS=1
    export PGREP_WAYBAR_AFTER_DIRECT=1

    if ! run_switch waybar >"$TEST_ROOT/stdout" 2>"$TEST_ROOT/stderr"; then
        sed 's/^/stderr: /' "$TEST_ROOT/stderr" >&2 || true
        fail "switching to waybar should fall back to a direct launch"
    fi

    assert_eq "waybar" "$(cat "$HOME/.config/dusky/settings/active_bar")" "waybar state"
    assert_log_contains "waybar_toggle --on"
    assert_log_contains "setsid waybar"
}

test_waybar_switch_falls_back_when_toggle_process_dies_during_settle() {
    setup_fake_home
    trap cleanup_fake_home RETURN
    printf 'adaptive-glass\n' > "$HOME/.config/dusky/settings/active_bar"
    export SYSTEMD_RUN_STATUS=1
    export PGREP_STATUSES="1,1,0,1"
    export PGREP_WAYBAR_AFTER_DIRECT=1

    if ! run_switch waybar >"$TEST_ROOT/stdout" 2>"$TEST_ROOT/stderr"; then
        sed 's/^/stderr: /' "$TEST_ROOT/stderr" >&2 || true
        fail "switching to waybar should recover when the first process dies"
    fi

    assert_eq "waybar" "$(cat "$HOME/.config/dusky/settings/active_bar")" "waybar state"
    assert_log_contains "waybar_toggle --on"
    assert_log_contains "setsid waybar"
}

test_toggle_from_waybar_starts_adaptive_glass
test_legacy_novabar_state_normalizes_to_adaptive_glass
test_novabar_argument_is_rejected
test_start_uses_saved_adaptive_glass_state
test_adaptive_launch_does_not_leave_switch_lock_held
test_waybar_switch_stops_adaptive_glass
test_waybar_switch_falls_back_when_toggle_exits_without_a_waybar_process
test_waybar_switch_falls_back_when_toggle_process_dies_during_settle

printf 'bar switch adaptive-glass tests: PASS\n'
