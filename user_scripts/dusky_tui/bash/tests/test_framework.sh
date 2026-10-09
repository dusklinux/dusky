#!/usr/bin/env bash
# Focused engine/frontend integration tests; all writes use a temporary directory.
set -Eeuo pipefail
ROOT=${BASH_SOURCE[0]}
if [[ $ROOT == */* ]]; then ROOT=${ROOT%/*}; else ROOT=.; fi
ROOT=$(cd -- "$ROOT/.." && pwd -P)
TEST_DIR=$(mktemp -d)
trap 'rm -rf -- "$TEST_DIR"' EXIT
export XDG_RUNTIME_DIR="$TEST_DIR"
# shellcheck source=../frontend/ui.sh
source "$ROOT/frontend/ui.sh"
# shellcheck source=../engines/kv.sh
source "$ROOT/engines/kv.sh"
# shellcheck source=../schemas/demo.sh
source "$ROOT/schemas/demo.sh"
tui_init
register_items
declare -i assertions=0
assert_eq() {
    if [[ $1 != "$2" ]]; then
        printf 'FAIL: %s; expected %q, got %q\n' "$3" "$2" "$1" >&2
        exit 1
    fi
    assertions+=1
}

CONFIG_FILE="$TEST_DIR/settings with spaces.conf"
cat > "$CONFIG_FILE" <<'CONFIG'
# Preserve this comment
service_enabled=yes
timeout = 100
log_prefix=""
duplicate=first
duplicate=last
[network]
protocol tcp
[display]
border_size=2
blur_enabled=true
CONFIG
chmod 640 "$CONFIG_FILE"
before=$(kv_file_signature "$CONFIG_FILE")
before_stderr=$(readlink -- "/proc/$$/fd/2")
engine_init "$CONFIG_FILE"
assert_eq "$(kv_file_signature "$CONFIG_FILE")" "$before" 'init preserves existing file'
tui_load
assert_eq "${ENGINE_STATE[protocol|network]}" tcp 'section and space separator'
assert_eq "${ENGINE_STATE[log_prefix|]}" '' 'empty value stays present'
assert_eq "${ENGINE_STATE[duplicate|]}" last 'last duplicate loads'
load_active_values
modify_value 'Enable Service' 1
assert_eq "${ENGINE_STATE[service_enabled|]}" false 'frontend bool adjustment'
modify_value 'Timeout (ms)' 1
assert_eq "${ENGINE_STATE[timeout|]}" 150 'frontend int adjustment'
assert_eq "$(stat -c %a -- "$CONFIG_FILE")" 640 'save preserves mode'
assert_eq "$(readlink -- "/proc/$$/fd/2")" "$before_stderr" 'lock cleanup preserves stderr'

literal=$'  C:\\temp\\thing $(literal) "quotes" | and ; #  '
tui_write literal "$literal" network
tui_load
assert_eq "${ENGINE_STATE[literal|network]}" "$literal" 'literal round trip'
tui_write quoted '"outer quotes"'
tui_load
assert_eq "${ENGINE_STATE[quoted|]}" '"outer quotes"' 'outer quotes round trip'
before=$(kv_file_signature "$CONFIG_FILE")
tui_write quoted '"outer quotes"'
assert_eq "$ENGINE_CHANGED" 0 'unchanged save reports no change'
assert_eq "$(kv_file_signature "$CONFIG_FILE")" "$before" 'no-op leaves inode and timestamps'
tui_write duplicate '' '' delete
tui_load
assert_eq "${ENGINE_STATE[duplicate|]+present}" '' 'delete removes all duplicates'
tui_write absent '' '' delete
assert_eq "$ENGINE_CHANGED" 0 'deleting missing value is a no-op'
if tui_write literal $'bad\nvalue'; then printf 'FAIL: multiline accepted\n' >&2; exit 1; fi
assert_eq "$ENGINE_CHANGED" 0 'invalid write reports no change'
assert_eq "$STATUS_MESSAGE" 'Invalid key, operation, or multiline value.' 'engine error reaches status'

# A stale relative edit must refresh state and leave the external edit intact.
printf 'timeout=300\n' > "$CONFIG_FILE"
if tui_write timeout 200 '' set 1 150; then printf 'FAIL: stale edit accepted\n' >&2; exit 1; fi
assert_eq "${ENGINE_STATE[timeout|]}" 300 'conflict refreshes cache'
tui_load
assert_eq "${ENGINE_STATE[timeout|]}" 300 'conflict preserves external value'

exec {held_lock}>>"$KV_LOCK_TARGET"
flock -x -n "$held_lock"
if tui_write timeout 400; then printf 'FAIL: locked write accepted\n' >&2; exit 1; fi
assert_eq "$ENGINE_MESSAGE" 'Config file is locked by another process.' 'lock conflict diagnostic'
flock -u "$held_lock"
exec {held_lock}>&-

# Reset All invokes the application hook once; Reset without a default deletes.
declare -i hooks=0
post_write_action() { hooks+=1; }
reset_defaults
assert_eq "$hooks" 1 'one hook for reset-all changes'
assert_eq "${ENGINE_STATE[timeout|]}" 100 'reset restores schema default'
assert_eq "${ENGINE_STATE[log_prefix|]}" myapp_ 'reset restores string default'
reset_defaults
assert_eq "$hooks" 1 'no hooks for unchanged reset'
register 0 'Optional' 'optional|string||||'
tui_write optional enabled
SELECTED_ROW=3
reset_current_item
assert_eq "${ENGINE_STATE[optional|]+present}" '' 'missing default resets to unset'

# Engine load failures retain the previously published cache.
mv -- "$CONFIG_FILE" "$TEST_DIR/hidden.conf"
if tui_write timeout 600 2>"$TEST_DIR/write-errors"; then printf 'FAIL: missing target written\n' >&2; exit 1; fi
assert_eq "$(cat "$TEST_DIR/write-errors")" '' 'backend failure does not paint raw stderr into UI'
assert_eq "$STATUS_MESSAGE" 'Config is missing or unreadable.' 'missing-target diagnostic reaches UI'
if tui_load 2>/dev/null; then printf 'FAIL: missing file loaded\n' >&2; exit 1; fi
assert_eq "${ENGINE_STATE[timeout|]}" 100 'failed load retains state'
mv -- "$TEST_DIR/hidden.conf" "$CONFIG_FILE"
ln -s -- "$CONFIG_FILE" "$TEST_DIR/link.conf"
engine_init "$TEST_DIR/link.conf"
tui_load
tui_write timeout 250
assert_eq "$(readlink -- "$TEST_DIR/link.conf")" "$CONFIG_FILE" 'save retains symlink'
assert_eq "${ENGINE_STATE[timeout|]}" 250 'save follows symlink target'

printf '[network]\r\nprotocol tcp\r\n' > "$TEST_DIR/crlf.conf"
engine_init "$TEST_DIR/crlf.conf"
tui_load
tui_write protocol udp network
assert_eq "$(cat "$TEST_DIR/crlf.conf")" $'[network]\r\nprotocol udp\r' 'CRLF and separator preserved'
kv_create_tmpfile_for_target "$ENGINE_TARGET"
staged=$KV_TMPFILE
engine_cleanup
assert_eq "${#KV_TEMP_PATHS[@]}" 0 'cleanup forgets stages'
if [[ -e $staged ]]; then printf 'FAIL: leaked stage\n' >&2; exit 1; fi

# Replacing only the engine preserves schema registration and frontend behavior.
# shellcheck source=../engines/memory.sh
source "$ROOT/engines/memory.sh"
engine_init ''
tui_load
SELECTED_ROW=0
load_active_values
modify_value 'Timeout (ms)' 1
assert_eq "${ENGINE_STATE[timeout|]}" 150 'same frontend with alternative engine'
switch_tab 1
modify_value Protocol 1
assert_eq "${ENGINE_STATE[protocol|network]}" udp 'cycle with alternative engine'
switch_tab 2
activate_item
assert_eq "$CURRENT_VIEW" 1 'submenu opens'
load_active_values
modify_value 'Max Retries' 1
assert_eq "${ENGINE_STATE[max_retries|system]}" 4 'submenu schema with alternative engine'
go_back
SELECTED_ROW=3
activate_item
assert_eq "$CURRENT_VIEW" 2 'custom picker callback opens view'
picker_navigate 1
picker_confirm
assert_eq "$STATUS_MESSAGE" 'Selected Theme: Nord' 'picker callback runs'
assert_eq "$CURRENT_VIEW" 0 'picker restores parent view'

register 3 'Float' 'float|float||0|1|0.1' 0.5
register 3 'Large Integer' 'large|int||999999999999999990|999999999999999999|1' 999999999999999998
modify_value Float 1
assert_eq "${ENGINE_STATE[float|]}" 0.6 'float adjustment'
tui_write float 1
modify_value Float 1
assert_eq "${ENGINE_STATE[float|]}" 1 'float upper bound'
modify_value 'Large Integer' 1
assert_eq "${ENGINE_STATE[large|]}" 999999999999999999 'large integer precision'
modify_value 'Large Integer' 1
assert_eq "$ENGINE_CHANGED" 0 'bound produces unchanged write'

TERM_ROWS=40; TERM_COLS=110
classify_mouse_event 0 50 6 M
assert_eq "$REPLY" 32 'mouse press selects without activating'
classify_mouse_event 0 50 6 m
assert_eq "$REPLY" 0 'matching release clicks'
PASTE_ACTIVE=1
handle_input_router R
assert_eq "$PASTE_ACTIVE" 1 'paste byte does not become reset shortcut'
for byte in $'\e' '[' '2' '0' '1' '~'; do consume_paste_byte "$byte"; done
assert_eq "$PASTE_ACTIVE" 0 'paste terminator restores routing'

# A new engine can serialize by schema type without consulting UI data structures.
engine_write() {
    RECEIVED_TYPE=${7:-string}
    RECEIVED_OPERATION=${4:-set}
    ENGINE_CHANGED=0
    ENGINE_MESSAGE=""
}
CURRENT_TAB=0; CURRENT_VIEW=0; SELECTED_ROW=0
modify_value 'Enable Service' 1
assert_eq "$RECEIVED_TYPE" bool 'relative writes forward schema type'
reset_current_item
assert_eq "$RECEIVED_TYPE" bool 'reset forwards schema type'
set_absolute_value 'Timeout (ms)' 250
assert_eq "$RECEIVED_TYPE" int 'absolute writes forward schema type'
SELECTED_ROW=3
reset_current_item
assert_eq "$RECEIVED_TYPE" string 'unset reset forwards schema type'
assert_eq "$RECEIVED_OPERATION" delete 'unset reset retains delete operation'

# Failed operations must retain diagnostics and never look like changed saves.
engine_write() {
    ENGINE_CHANGED=1
    ENGINE_MESSAGE='Backend is unavailable.'
    ENGINE_STATE['timeout|']=700
    return 1
}
SELECTED_ROW=1
reset_current_item
assert_eq "$STATUS_MESSAGE" "Failed to reset 'Timeout (ms)': Backend is unavailable." 'reset retains engine diagnostic'
assert_eq "${VALUE_CACHE['0::Timeout (ms)']}" 700 'failed reset displays observed state'
assert_eq "$ENGINE_CHANGED" 0 'failed write cannot trigger changed-save hook'

printf 'PASS: %d assertions (file engine, alternate engine, and frontend integration)\n' "$assertions"
