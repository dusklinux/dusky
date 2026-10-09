#!/usr/bin/env bash
# Read-only comparison with the tried-and-tested standalone reference.
set -Eeuo pipefail
ROOT=${BASH_SOURCE[0]}
if [[ $ROOT == */* ]]; then ROOT=${ROOT%/*}; else ROOT=.; fi
ROOT=$(cd -- "$ROOT/.." && pwd -P)
TEST_DIR=$(mktemp -d)
trap 'rm -rf -- "$TEST_DIR"' EXIT
mkdir -- "$TEST_DIR/original" "$TEST_DIR/modular"

# Ask Bash to parse and canonicalize functions, including one-line definitions.
for variant in original modular; do
    bash -c '
        if [[ $2 == original ]]; then
            source "$1/generic_template/dusky_tui_5.9.1.sh"
        else
            source "$1/frontend/ui.sh"
            source "$1/engines/kv.sh"
            source "$1/schemas/demo.sh"
        fi
        compgen -V names -A function
        for name in "${names[@]}"; do declare -f "$name" > "$3/$name"; done
    ' parity "$ROOT" "$variant" "$TEST_DIR/$variant"
done

declare -i identical=0 adapted=0 removed=0
for path in "$TEST_DIR/original/"*; do
    name=${path##*/}
    destination=$name
    case $name in
        # Identity-only helper was removed; keys/scopes are used directly.
        normalize_target) removed+=1; continue ;;
        action_demo_sudo) destination=action_demo_service ;;
        main) destination=tui_main ;;
        resolve_write_target) destination=engine_init ;;
        populate_config_cache) destination=engine_load ;;
        write_value_to_file) destination=engine_write ;;
        register_temp|forget_temp|remove_temp|file_signature|release_lock_fd|create_tmpfile_for_target|commit_tmpfile_to_target)
            destination=kv_$name ;;
    esac
    if [[ ! -f $TEST_DIR/modular/$destination ]]; then
        printf 'FAIL: missing template function %s (expected %s)\n' "$name" "$destination" >&2
        exit 1
    fi
    # Explicitly reviewed adaptations: engine boundaries, diagnostics, typed
    # writes, lifecycle, demo actions, and builtin callback lookup/quoted args.
    case $name in
        activate_item|cleanup|handle_key_picker|load_active_values|modify_value|parse_args|picker_confirm|register_items|reset_current_item|set_absolute_value)
            adapted+=1; continue ;;
        action_demo_sudo|main|resolve_write_target|populate_config_cache|write_value_to_file|register_temp|forget_temp|remove_temp|file_signature|release_lock_fd|create_tmpfile_for_target|commit_tmpfile_to_target)
            adapted+=1; continue ;;
    esac
    body=$(< "$path")
    body=${body//CONFIG_CACHE/ENGINE_STATE}
    body=${body//WRITE_TARGET/ENGINE_TARGET}
    body=${body//LAST_WRITE_CHANGED/ENGINE_CHANGED}
    body=${body//write_value_to_file/tui_write}
    body=${body//populate_config_cache/tui_load}
    current=$(< "$TEST_DIR/modular/$destination")
    if [[ $body != "$current" ]]; then
        printf 'FAIL: unexpected change in template function %s\n' "$name" >&2
        exit 1
    fi
    identical+=1
done

# Render the same fixture with each implementation and compare terminal bytes.
cat > "$TEST_DIR/render.sh" <<'FIXTURE'
set -Eeuo pipefail
if [[ $2 == original ]]; then
    source "$1/generic_template/dusky_tui_5.9.1.sh"
    WRITE_TARGET=/fixture/settings.conf
    CONFIG_CACHE=(['enabled|']=true ['count|']=150 ['text|']='literal text' ['ratio|']=0.6 ['choice|']=two)
else
    source "$1/frontend/ui.sh"
    source "$1/engines/kv.sh"
    declare APP_TITLE='Generic System Config Editor' APP_VERSION=v5.9.1
    declare -a TABS=(General Network Display System)
    tui_init
    ENGINE_TARGET=/fixture/settings.conf
    ENGINE_STATE=(['enabled|']=true ['count|']=150 ['text|']='literal text' ['ratio|']=0.6 ['choice|']=two)
fi
register 0 Enabled 'enabled|bool||||' true
register 0 Count 'count|int||0|1000|50' 100
register 0 Text 'text|string||||'
register 0 Ratio 'ratio|float||0|1|0.1' 0.5
register 0 Choice 'choice|cycle||one,two,three||' one
register 0 Tools 'tools|menu||||'
register_child tools Child 'child|bool||||' false
register 0 Action 'action|action||||'
for ((i=0; i<24; i++)); do register 0 "Extra $i" "extra_$i|string||||"; done
TERM_ROWS=40; TERM_COLS=110
load_active_values
draw_ui > "$3/main"
SELECTED_ROW=25
draw_ui > "$3/scrolled"
CURRENT_VIEW=1; CURRENT_MENU_ID=tools; SELECTED_ROW=0; SCROLL_OFFSET=0
load_active_values
draw_ui > "$3/menu"
CURRENT_VIEW=2
PICKER_TITLE='Fixture Picker'
PICKER_ITEMS=(One Two Three); PICKER_HINTS=(First Second Third)
PICKER_SELECTED=1
draw_ui > "$3/picker"
TERM_ROWS=10; TERM_COLS=40
draw_ui > "$3/small"
FIXTURE

for variant in original modular; do
    bash "$TEST_DIR/render.sh" "$ROOT" "$variant" "$TEST_DIR/$variant"
done
for view in main scrolled menu picker small; do
    if ! cmp -s -- "$TEST_DIR/original/$view" "$TEST_DIR/modular/$view"; then
        printf 'FAIL: rendering differs in %s view\n' "$view" >&2
        exit 1
    fi
done
printf 'PASS: %d identical functions, %d reviewed adaptations, %d identity helper removed; 5 byte-identical rendered views\n' "$identical" "$adapted" "$removed"
