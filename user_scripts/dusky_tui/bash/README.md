# Reusable Dusky Bash TUI

The standalone `generic_template/dusky_tui_5.9.1.sh` is preserved. Future
applications can share these modules instead of copying the entire template.

```text
bash/
├── frontend/
│   ├── ui.sh           Terminal UI, input, navigation, lifecycle
│   └── core_types.sh   Registration, validation, shared value helpers
├── engines/
│   ├── kv.sh           Flat / sectioned key-value files
│   └── memory.sh       Alternative engine for session-only prototypes
├── schemas/
│   └── demo.sh         Application settings, layout, actions and save hook
├── apps/
│   └── demo.sh         Small launcher selecting frontend, engine and schema
└── tests/
    ├── test_framework.sh
    ├── test_template_parity.sh
    └── test_terminal.py
```

From this directory:

```bash
./apps/demo.sh --check
./apps/demo.sh
./apps/demo.sh --config /path/to/settings.conf
DUSKY_DEMO_ENGINE=memory ./apps/demo.sh
```

The file-backed demo defaults to `${XDG_CONFIG_HOME:-$HOME/.config}/dusky_tui/demo.conf`;
`DUSKY_CONFIG_FILE` overrides that default. Opening a new target creates an empty
file. Registered defaults are written when an item is adjusted or reset, rather
than automatically during startup. The memory engine never writes a file, and
its values disappear on exit. The demo actions only show messages.

`--check` validates registrations, engine function availability, and command
dependencies without opening the target or taking over a terminal. It does not
test file permissions or an engine's actual load/write behavior.

## Create a future application

1. Copy `schemas/demo.sh` to `schemas/myapp.sh`. Set `CONFIG_FILE`, `APP_TITLE`,
   `APP_VERSION`, and `TABS`; replace `register_items()` and application actions.
2. Copy `apps/demo.sh` to `apps/myapp.sh`. Change its schema source to
   `schemas/myapp.sh` and select the engine source you want.
3. Run `./apps/myapp.sh --check`, then launch it in a terminal.

The launcher resolves the framework relative to its own directory. Keep it in
`apps/`, or adjust the relative root when moving it. All sources must execute at
the launcher's top level; Bash declarations sourced inside a function can become
local. A launcher starts one application with one selected engine per process.

Minimal launcher body (after `#!/usr/bin/env bash`):

```bash
set -Eeuo pipefail
BASH_TUI_ROOT=${BASH_SOURCE[0]}
if [[ $BASH_TUI_ROOT == */* ]]; then BASH_TUI_ROOT=${BASH_TUI_ROOT%/*}; else BASH_TUI_ROOT=.; fi
BASH_TUI_ROOT=$(cd -- "$BASH_TUI_ROOT/.." && pwd -P)
source "$BASH_TUI_ROOT/frontend/ui.sh"
source "$BASH_TUI_ROOT/engines/kv.sh"
source "$BASH_TUI_ROOT/schemas/myapp.sh"
tui_main "$@"
```

Modules are sourced once. Normal UI and engine calls run in the same Bash
process, including their caches; there is no per-event plugin subprocess or
`eval`. Sourcing the frontend installs no signal traps and opens no target or
TTY. `tui_main` initializes the UI, registers the schema, starts the engine,
then manages the terminal and cleanup. The event loop explicitly handles
expected failures and disables `errexit`, retaining `nounset` and `pipefail`.

## Schema interface

Schemas are Bash files using the existing registration syntax:

```bash
declare CONFIG_FILE="${XDG_CONFIG_HOME:-${HOME}/.config}/myapp/settings.conf"
declare APP_TITLE="My Application" APP_VERSION="v1.0"
declare -a TABS=("General" "Display")

register_items() {
    register 0 "Enabled" 'enabled|bool||||' true
    register 0 "Timeout" 'timeout|int||0|1000|50' 100
    register 0 "Optional Text" 'text|string||||'
    register 1 "Style" 'style|cycle|display|compact,wide||' compact
}
```

`register TAB LABEL 'key|type|scope|min|max|step' [DEFAULT]` accepts `bool`, `int`,
`float`, `string`, `cycle`, `action`, and `menu`. For cycles, `min` contains
comma-separated choices. Integers support up to 18 decimal digits; floats use
AWK's finite floating-point arithmetic. Omit the default to make Reset delete
a setting; pass `""` for an explicit empty default. Blank string input deletes
the setting. Multiline values are not supported.

Register a menu before `register_child MENU_ID LABEL SPEC [DEFAULT]`. Menus
support one level. Reset All affects the current tab or submenu. Each item is
identified in the frontend by its context and label; each stored setting is
identified by its key and scope. Labels must be unique within their context.
Separators `|` in keys/scopes and `,` in cycle choices are reserved.

An action with key `refresh` calls `action_refresh()`. Actions can use
`set_status`, `prompt_line_input`, `acquire_sudo`, and the picker fields shown in
`schemas/demo.sh`. Define `post_write_action()` for an application reload after
a changed save; Reset All calls it once if any writes changed. Callbacks should
handle command failures and report them with `set_status`.

Override `MAX_DISPLAY_ROWS`, `BOX_INNER_WIDTH`, `ADJUST_THRESHOLD`, or
`ITEM_PADDING` in the schema to customize the existing fixed layout.

## Engine interface

Source the frontend first, then exactly one engine. `engines/memory.sh` is a
small working example of the contract for new engines.

| Function | Contract |
|---|---|
| `engine_init TARGET` | Prepare a target and publish `ENGINE_TARGET` for the footer. |
| `engine_load` | Publish a complete `ENGINE_STATE` snapshot on success; retain the last complete snapshot on failure. |
| `engine_write KEY VALUE [SCOPE] [OP] [EXPECTED_PRESENT] [EXPECTED_VALUE] [ITEM_TYPE]` | Set/delete a value, updating `ENGINE_STATE` on success. `OP` defaults to `set`; the other operation is `delete`. |
| `engine_cleanup` | Release temporary resources; callable even after partial initialization. |

Every engine declares `ENGINE_DEPENDENCIES` as a Bash indexed array, including
an empty array when it needs no extra commands. The shared result fields are:

- `ENGINE_STATE`: associative array indexed by `"${key}|${scope}"`. A missing
  entry means unset; an existing entry with `""` means an explicit empty value.
- `ENGINE_TARGET`: display path or description of the active target.
- `ENGINE_MESSAGE`: diagnostic for a failed operation; the frontend displays it.
- `ENGINE_CHANGED`: reset to `0` at the start of every write; set to `1` only
  when the stored state actually changed.

Return `0` on success and nonzero on failure. Clear `ENGINE_MESSAGE` before each
operation. Engines should not print to the terminal, draw UI, install traps,
exit the process, or call frontend status functions. Call engine functions
directly, never through command substitution: their shared state must survive.

When argument 5 is nonempty, compare the current setting's presence (`0` or
`1`) and raw value with arguments 5/6 before writing. This protects relative adjustments
from stale reads. Reject a mismatch, refresh available state, and provide a
diagnostic. Explicit absolute saves and resets pass empty arguments 5/6.

Argument 7 is the schema type (`bool`, `int`, `float`, `string`, or `cycle`) and
defaults to `string` for direct calls that omit it. Every frontend setting write
passes its type, including resets and string edits. A future JSON/TOML/command
engine can distinguish a boolean `false` from string text `"false"` without
inspecting frontend arrays. The key-value and memory engines store literal text
and ignore this metadata. For example:

```bash
tui_write enabled false '' set '' '' bool        # absolute boolean save
tui_write count 150 '' set 1 100 int             # relative save expecting 100
```

This interface supports swapping file formats or backing commands while sharing
the frontend. It does not implement the Python frontend's simultaneous engine
pool, per-item file overrides, undo/redo, presets, or batch commit mode. Those
can be added later when an application needs them.

## Key-value engine behavior

`kv.sh` retains the standalone template's parser/writer behavior: global or
`[section]` settings, `key=value` or `key value`, whole-line `#`/`;` comments,
optional outer quotes, and literal values without escape or inline-comment
interpretation. Duplicate keys load the last occurrence; saving collapses
duplicates for that setting, and deleting removes all of them. Existing
assignment spacing and CRLF line endings are preserved.

Writes coordinate through a nonblocking `flock`, check file signatures for
external changes, stage a single replacement beside the target, preserve
owner/group/mode, and rename atomically. Fresh-cache no-ops leave the file
untouched. F5 reloads on demand; there is no config polling while idle.
The new engine also scopes lock-close redirections so stderr remains available.
Backend command failures are reported through `ENGINE_MESSAGE`, keeping raw
command errors from interrupting the terminal layout.

Atomic replacement requires write access to the target directory as well as
the file. It follows the target resolved at startup for symlinks. Hard-link
identity, ACLs, and extended attributes require an application-specific writer.
There is no power-loss durability guarantee, and signature checks cannot
eliminate every race with writers that ignore the lock. Reset All performs
individual writes and may partially succeed; it is not a transaction.

## Requirements and verification

Target Bash 5.3.20+ on Linux. The UI requires a controlling terminal, `stty`, and
`awk`; the key-value engine additionally requires GNU coreutils and util-linux
commands listed in `ENGINE_DEPENDENCIES`. No Python, fzf, Xorg, or XWayland
dependency is introduced for running the TUI.

Verified on Bash 5.3.20, GNU coreutils 9.12 (`mv --no-copy` available),
util-linux 2.42.4, and GNU Awk 5.4.1. Recheck the final ISO's command versions
and features before release; that image is not available here.

```bash
bash tests/test_framework.sh
bash tests/test_template_parity.sh
python3 -X dev tests/test_terminal.py
```

These integration tests use temporary fixtures to exercise the file engine,
an alternative engine, and shared frontend operations. Syntax and ShellCheck
checks apply to the new modules; the original script is not modified.
Python is only needed for the optional terminal regression test. That test
exercises real terminal input, paste suppression, resize handling, signal exits,
idle redraw behavior, and terminal restoration. It also builds a different
application in a temporary directory and tests its custom layout, string edits,
actions, suspend/resume, and persisted settings without changing the frontend.

The parity test accounts for all 78 original functions: 55 are identical after
interface name substitutions, 22 have reviewed adaptations, and the unused
identity-only `normalize_target` helper was removed. All renderers, navigation,
mouse and escape parsing, paste handling, line input, and sudo UI are retained.
It also compares five rendered views byte for byte against the original:
the main list, scrolling, submenu, picker, and small-terminal notice.

The Python architecture contributes the engine contract and type metadata;
the terminal UI comes from the Bash template. Reset failures retain the engine
diagnostic and refresh displayed state, and failed writes cannot trigger the
changed-save hook.
