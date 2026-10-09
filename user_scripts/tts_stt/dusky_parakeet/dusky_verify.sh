#!/usr/bin/env bash
# Static checks never start recording. `live` explicitly records three seconds.
set -uo pipefail
APP_DIR="${DUSKY_APP_DIR:-$HOME/contained_apps/uv/dusky_stt}"
CONFIG="${DUSKY_CONFIG:-$APP_DIR/config.json}"
TRIGGER="${DUSKY_TRIGGER:-$HOME/.local/bin/dusky_trigger}"
PY="$APP_DIR/.venv/bin/python"
PASSED=0 FAILED=0
check() {
    local label=$1
    shift
    if "$@"; then PASSED=$((PASSED+1)); printf '✓ %s\n' "$label"
    else FAILED=$((FAILED+1)); printf '✗ %s\n' "$label"; fi
}
static_checks() {
    check 'Python 3.15+ with GIL' "$PY" -c 'import sys; assert sys.version_info >= (3,15) and sys._is_gil_enabled()'
    check 'Dependencies consistent' uv pip check --python "$PY"
    check 'CPU capture process' "$PY" "$APP_DIR/dusky_main.py" --config "$CONFIG" --check-cpu-isolation
    check 'English model loads; silence produces no text' "$PY" "$APP_DIR/dusky_worker.py" --config "$CONFIG" --self-test
    check 'Native Rust UI installed' test -x "$APP_DIR/dusky-rec-indicator"
    if "$PY" -c 'import json,sys; sys.exit(not bool(json.load(open(sys.argv[1])).get("parakeet")))' "$CONFIG"; then
        check 'GPU Python 3.14 with GIL' "$APP_DIR/.venv-gpu/bin/python" -c 'import sys; assert sys.version_info[:2] == (3,14) and sys._is_gil_enabled()'
        check 'GPU dependencies consistent' uv pip check --python "$APP_DIR/.venv-gpu/bin/python"
        check 'Parakeet executes CUDA nodes' "$APP_DIR/.venv-gpu/bin/python" "$APP_DIR/dusky_worker.py" --config "$CONFIG" --backend nvidia --self-test
    fi
    check 'Status (no recording process is valid)' "$TRIGGER" --status --json
}
live_checks() {
    local response job result state_dir
    if ! response=$("$TRIGGER" --start --json); then
        FAILED=$((FAILED+1)); printf '✗ Capture start failed\n'; return
    fi
    job=$("$PY" -c 'import json,sys; print(json.load(sys.stdin)["job"])' <<< "$response") || return 1
    state_dir=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["state_dir"])' "$CONFIG") || return 1
    result="$state_dir/jobs/$job.json"
    sleep 3
    if ! "$TRIGGER" --stop; then
        FAILED=$((FAILED+1)); printf '✗ Capture stop failed\n'; return
    fi
    for _ in {1..240}; do
        if [[ -f $result ]]; then
            check 'Exact microphone job succeeded' "$PY" -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["ok"], r' "$result"
            return
        fi
        sleep 0.5
    done
    FAILED=$((FAILED+1)); printf '✗ Capture did not finalize\n'
}
case "${1:-static}" in
    static) static_checks ;;
    live) live_checks ;;
    all) static_checks; live_checks ;;
    -h|--help) printf 'Usage: dusky_verify [static|live|all]\n'; exit 0 ;;
    *) printf 'Usage: dusky_verify [static|live|all]\n' >&2; exit 2 ;;
esac
printf '%d passed · %d failed\n' "$PASSED" "$FAILED"
[[ $FAILED -eq 0 ]]
