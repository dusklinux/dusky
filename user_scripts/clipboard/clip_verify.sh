#!/usr/bin/env bash
set -euo pipefail
LOG=/tmp/clip_verify.log
# FIXED 2026-07-11: tee to terminal AND log, was `exec >"$LOG" 2>&1` which hid output
exec > >(tee "$LOG") 2>&1

RAM_DB="${XDG_RUNTIME_DIR:-/run/user/${UID}}/cliphist.db"
DISK_DB="${XDG_CACHE_HOME:-$HOME/.cache}/cliphist/db"
SCRIPT="$HOME/user_scripts/arch_setup_scripts/scripts/390_clipboard_persistance.py"
CLIP_UI="$HOME/user_scripts/clipboard/terminal_clipboard.sh"
PASS=0
FAIL=0

# Clear databases before running tests to prevent false matches from old runs
rm -f "$RAM_DB" "$DISK_DB"

ok()   { echo "  PASS: $*"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $*"; FAIL=$((FAIL+1)); }
say()  { echo; echo "=== $* ==="; }

say "0: service architecture validation"
systemctl --user is-enabled dusky_clipboard.service >/dev/null && ok "dusky_clipboard.service is enabled" || bad "dusky_clipboard.service not enabled"
systemctl --user is-active dusky_clipboard.service >/dev/null && ok "dusky_clipboard.service is active" || bad "dusky_clipboard.service not active"
systemctl --user is-active cliphist.service >/dev/null 2>&1 && bad "old cliphist.service still running" || ok "old cliphist.service decommissioned"
systemctl --user is-active cliphist-image.service >/dev/null 2>&1 && bad "old cliphist-image.service still running" || ok "old cliphist-image.service decommissioned"
systemctl --user is-active wl-clip-persist.service >/dev/null 2>&1 && bad "old wl-clip-persist.service still running" || ok "old wl-clip-persist.service decommissioned"

watch_env() {
  local p c
  for p in $(pgrep -x wl-paste || true); do
    c=$(tr '\0' '\n' < /proc/$p/environ 2>/dev/null | grep '^CLIPHIST_DB_PATH=' || echo missing)
    echo "  watcher $p $c"
  done
  echo "  file: $(cat "$HOME/.config/dusky/settings/cliphist_db_env")"
  echo "  state: $(cat "$HOME/.config/dusky/settings/clipboard_persistance")"
  echo "  systemd: $(systemctl --user show-environment | grep CLIPHIST || echo none)"
}

say "A: switch to RAM, env consistency"
python3 "$SCRIPT" --ram
sleep 0.3
watch_env
FILE_PATH=$(grep -o '"[^"]*"' "$HOME/.config/dusky/settings/cliphist_db_env" | tr -d '"')
[[ "$FILE_PATH" == "$RAM_DB" ]] && ok "env file points to RAM" || bad "env file=$FILE_PATH"
SYS=$(systemctl --user show-environment | sed -n 's/^CLIPHIST_DB_PATH=//p')
[[ "$SYS" == "$RAM_DB" ]] && ok "systemd has RAM path" || bad "systemd=$SYS"
WOK=1
for p in $(pgrep -x wl-paste || true); do
  c=$(tr '\0' '\n' < /proc/$p/environ | grep '^CLIPHIST_DB_PATH=' | cut -d= -f2-)
  [[ "$c" == "$RAM_DB" ]] || WOK=0
done
[[ $WOK -eq 1 ]] && ok "all watchers on RAM" || bad "watcher env mismatch"
[[ "$(cat "$HOME/.config/dusky/settings/clipboard_persistance")" == "false" ]] && ok "state=false" || bad "state wrong"

say "B: isolation via UI list"
MR="VERIFY_RAM_ONLY_$(date +%s)_$RANDOM"
MD="VERIFY_DISK_ONLY_$(date +%s)_$RANDOM"

printf '%s' "$MR" | CLIPHIST_DB_PATH="$RAM_DB" cliphist store
UI=$($CLIP_UI --list || true)
# FIX: Replaced `grep -Fq` with `grep -F ... >/dev/null`
echo "$UI" | grep -F "$MR" >/dev/null && ok "UI(RAM mode) shows RAM marker" || bad "UI missing RAM marker"
echo "$UI" | grep -F "$MD" >/dev/null && bad "UI shows disk marker while in RAM" || ok "UI(RAM) hides disk marker"

python3 "$SCRIPT" --disk --quiet
sleep 0.4
printf '%s' "$MD" | CLIPHIST_DB_PATH="$DISK_DB" cliphist store
UI=$($CLIP_UI --list || true)
echo "$UI" | grep -F "$MD" >/dev/null && ok "UI(DISK mode) shows DISK marker" || bad "UI missing DISK marker"
echo "$UI" | grep -F "$MR" >/dev/null && bad "UI shows RAM marker while in DISK" || ok "UI(DISK) hides RAM marker"

say "C: wl-copy restorage leak regression (the main bug)"
python3 "$SCRIPT" --ram --quiet
sleep 0.4
M_WL_RAM="VERIFY_WL_RAM_$(date +%s)_$RANDOM"
printf '%s' "$M_WL_RAM" | wl-copy
sleep 1.2
# FIX: Replaced `grep -Fq` to prevent `SIGPIPE` crashing the pipeline under `set -e pipefail`
CLIPHIST_DB_PATH=$RAM_DB cliphist list | grep -F "$M_WL_RAM" >/dev/null && ok "wl-copy landed in RAM" || bad "wl-copy not in RAM"
CLIPHIST_DB_PATH=$DISK_DB cliphist list | grep -F "$M_WL_RAM" >/dev/null && bad "wl-copy already in DISK before switch" || ok "not in DISK yet"

python3 "$SCRIPT" --disk --quiet
sleep 1.0
if CLIPHIST_DB_PATH=$DISK_DB cliphist list | grep -F "$M_WL_RAM" >/dev/null; then
  bad "LEAK: RAM wl-copy still in DISK after switch"
else
  ok "no leak: RAM wl-copy absent from DISK after switch"
fi
CLIPHIST_DB_PATH=$RAM_DB cliphist list | grep -F "$M_WL_RAM" >/dev/null && ok "still in RAM history" || bad "lost from RAM history"
UI=$($CLIP_UI --list || true)
echo "$UI" | grep -F "$M_WL_RAM" >/dev/null && bad "UI(DISK) shows leaked RAM wl-copy" || ok "UI(DISK) clean of RAM wl-copy"
PASTE=$(wl-paste -n 2>/dev/null || true)
[[ "$PASTE" == "$M_WL_RAM" ]] && ok "OS clipboard preserved after switch" || bad "OS clipboard changed (got: ${PASTE:0:40})"

M_WL_DISK="VERIFY_WL_DISK_$(date +%s)_$RANDOM"
printf '%s' "$M_WL_DISK" | wl-copy
echo "  debug paste=$(wl-paste -n 2>/dev/null | head -c 60)"
echo "  debug watchers=$(pgrep -x wl-paste | tr '\n' ' ')"
sleep 1.5
echo "  debug list top: $(CLIPHIST_DB_PATH=$DISK_DB cliphist list 2>&1 | head -2 | tr '\n' ' | ')"
echo "  debug grep: $(CLIPHIST_DB_PATH=$DISK_DB cliphist list 2>&1 | grep -F "$M_WL_DISK" | head -1 || echo NONE)"
CLIPHIST_DB_PATH=$DISK_DB cliphist list | grep -F "$M_WL_DISK" >/dev/null && ok "wl-copy landed in DISK" || bad "wl-copy not in DISK (md=$M_WL_DISK)"

python3 "$SCRIPT" --ram --quiet
sleep 1.0
if CLIPHIST_DB_PATH=$RAM_DB cliphist list | grep -F "$M_WL_DISK" >/dev/null; then
  bad "LEAK: DISK wl-copy still in RAM after switch"
else
  ok "no leak: DISK wl-copy absent from RAM after switch"
fi
UI=$($CLIP_UI --list || true)
echo "$UI" | grep -F "$M_WL_DISK" >/dev/null && bad "UI(RAM) shows leaked DISK wl-copy" || ok "UI(RAM) clean of DISK wl-copy"
echo "$UI" | grep -F "$M_WL_RAM" >/dev/null && ok "UI(RAM) still shows original RAM marker" || bad "lost RAM history in UI"

say "D: rapid double switch"
python3 "$SCRIPT" --disk --quiet; sleep 0.3
python3 "$SCRIPT" --ram --quiet; sleep 0.3
python3 "$SCRIPT" --disk --quiet; sleep 0.5
N=$(pgrep -x wl-paste | wc -l)
[[ "$N" -eq 2 ]] && ok "exactly 2 watchers after rapid switch (got $N)" || bad "watcher count=$N want 2"
SYS=$(systemctl --user show-environment | sed -n 's/^CLIPHIST_DB_PATH=//p')
[[ "$SYS" == "$DISK_DB" ]] && ok "systemd ends on DISK" || bad "systemd=$SYS"
WOK=1
for p in $(pgrep -x wl-paste || true); do
  c=$(tr '\0' '\n' < /proc/$p/environ | grep '^CLIPHIST_DB_PATH=' | cut -d= -f2-)
  [[ "$c" == "$DISK_DB" ]] || WOK=0
done
[[ $WOK -eq 1 ]] && ok "all watchers on DISK after rapid" || bad "watcher mismatch after rapid"

say "E: post-switch copy only hits active mode"
python3 "$SCRIPT" --ram --quiet
sleep 0.4
M_NEW="VERIFY_POST_$(date +%s)_$RANDOM"
printf '%s' "$M_NEW" | wl-copy
sleep 1.2
CLIPHIST_DB_PATH=$RAM_DB cliphist list | grep -F "$M_NEW" >/dev/null && ok "new copy in RAM" || bad "new copy missing from RAM"
CLIPHIST_DB_PATH=$DISK_DB cliphist list | grep -F "$M_NEW" >/dev/null && bad "new copy polluted DISK" || ok "new copy not in DISK"

say "F: round-trip content preservation (dedup regression)"
python3 "$SCRIPT" --disk --quiet; sleep 0.4
RT_DISK="RT_DISK_$(date +%s)_$RANDOM"
printf '%s' "$RT_DISK" | CLIPHIST_DB_PATH="$DISK_DB" cliphist store
sleep 0.3
CLIPHIST_DB_PATH=$DISK_DB cliphist list | grep -F "$RT_DISK" >/dev/null && ok "DISK has marker before round-trip" || bad "DISK missing marker before round-trip"

python3 "$SCRIPT" --ram --quiet; sleep 0.4
CLIPHIST_DB_PATH=$DISK_DB cliphist list | grep -F "$RT_DISK" >/dev/null && ok "DISK still has marker after RAM switch" || bad "DISK lost marker after RAM switch"

python3 "$SCRIPT" --disk --quiet; sleep 0.5
CLIPHIST_DB_PATH=$DISK_DB cliphist list | grep -F "$RT_DISK" >/dev/null && ok "DISK has marker after round-trip" || bad "DISK lost marker after round-trip (dedup regression)"

python3 "$SCRIPT" --ram --quiet; sleep 0.4
RT_RAM="RT_RAM_$(date +%s)_$RANDOM"
printf '%s' "$RT_RAM" | CLIPHIST_DB_PATH="$RAM_DB" cliphist store
sleep 0.3
CLIPHIST_DB_PATH=$RAM_DB cliphist list | grep -F "$RT_RAM" >/dev/null && ok "RAM has marker before round-trip" || bad "RAM missing marker before round-trip"

python3 "$SCRIPT" --disk --quiet; sleep 0.4
CLIPHIST_DB_PATH=$RAM_DB cliphist list | grep -F "$RT_RAM" >/dev/null && ok "RAM still has marker after DISK switch" || bad "RAM lost marker after DISK switch"

python3 "$SCRIPT" --ram --quiet; sleep 0.5
CLIPHIST_DB_PATH=$RAM_DB cliphist list | grep -F "$RT_RAM" >/dev/null && ok "RAM has marker after round-trip" || bad "RAM lost marker after round-trip (dedup regression)"

RT_WL="RT_WL_$(date +%s)_$RANDOM"
printf '%s' "$RT_WL" | CLIPHIST_DB_PATH="$DISK_DB" cliphist store
sleep 0.3
python3 "$SCRIPT" --ram --quiet; sleep 0.4
python3 "$SCRIPT" --disk --quiet; sleep 0.5
CLIPHIST_DB_PATH=$DISK_DB cliphist list | grep -F "$RT_WL" >/dev/null && ok "wl-copy round-trip preserves DISK entry" || bad "wl-copy round-trip lost DISK entry"
say "G: clipboard copy-on-close text persistence (Wayland test)"
M_PERSIST="VERIFY_PERSIST_$(date +%s)_$RANDOM"
wl-copy --foreground "$M_PERSIST" &
WL_PID=$!
sleep 0.8
kill "$WL_PID" 2>/dev/null || true
wait "$WL_PID" 2>/dev/null || true
sleep 0.2
PASTED=$(wl-paste -n 2>/dev/null || true)
if [[ "$PASTED" == "$M_PERSIST" ]]; then
  ok "clipboard selection persisted after source closed"
else
  bad "clipboard selection lost after source closed (persistence failed, got: ${PASTED:0:40})"
fi

say "H: clipboard copy-on-close image persistence (Wayland test)"
M_IMG_PERSIST="DUMMY_IMAGE_DATA_$(date +%s)_$RANDOM"
printf '%s' "$M_IMG_PERSIST" | wl-copy --type image/png --foreground &
WL_IMG_PID=$!
sleep 0.8
kill "$WL_IMG_PID" 2>/dev/null || true
wait "$WL_IMG_PID" 2>/dev/null || true
sleep 0.2
PASTED_IMG=$(wl-paste --type image/png 2>/dev/null || true)
if [[ "$PASTED_IMG" == "$M_IMG_PERSIST" ]]; then
  ok "clipboard image selection persisted after source closed"
else
  bad "clipboard image selection lost after source closed (persistence failed, got: ${PASTED_IMG:0:40})"
fi

say "J: dusky_clipboard.service toggle on/off validation"
systemctl --user stop dusky_clipboard.service
sleep 0.5
pgrep -x wl-paste >/dev/null && bad "wl-paste still running after service stop" || ok "watchers cleanly stopped on service stop"
pgrep -x wl-clip-persist >/dev/null && bad "wl-clip-persist still running after service stop" || ok "persistence cleanly stopped on service stop"
systemctl --user start dusky_clipboard.service
sleep 0.8
systemctl --user is-active dusky_clipboard.service >/dev/null && ok "dusky_clipboard.service revived on start" || bad "service failed to start"
TOGGLE_TEST="TOGGLE_TEST_$(date +%s)"
wl-copy "$TOGGLE_TEST"
sleep 0.8
ACTIVE_DB=$(grep -o '"[^"]*"' "$HOME/.config/dusky/settings/cliphist_db_env" | tr -d '"')
CLIPHIST_DB_PATH="$ACTIVE_DB" cliphist list | grep -F "$TOGGLE_TEST" >/dev/null && ok "copy works after service toggle cycle" || bad "copy dropped after toggle cycle"

say "K: dusky_clipboard.service violent crash recovery"
DAEMON_PID=$(systemctl --user show -p MainPID --value dusky_clipboard.service)
kill -9 "$DAEMON_PID" 2>/dev/null || true
sleep 1.8
systemctl --user is-active dusky_clipboard.service >/dev/null && ok "dusky_clipboard.service auto-restarted after SIGKILL" || bad "service did not restart"
CRASH_TEST="CRASH_RECOVERY_$(date +%s)"
wl-copy "$CRASH_TEST"
sleep 0.8
ACTIVE_DB=$(grep -o '"[^"]*"' "$HOME/.config/dusky/settings/cliphist_db_env" | tr -d '"')
CLIPHIST_DB_PATH="$ACTIVE_DB" cliphist list | grep -F "$CRASH_TEST" >/dev/null && ok "copy captured immediately after crash recovery" || bad "copy dropped after crash recovery"

say "I: harness still alive"
ok "test harness completed without being killed"

say "SUMMARY"
echo "PASS=$PASS FAIL=$FAIL"
if [[ $FAIL -eq 0 ]]; then
  echo "GREEN LIGHT"
  exit 0
else
  echo "RED — failures remain"
  exit 1
fi
