# Engine: `lua` (Hyprland Lua AST)

- **Class:** `HyprlandLuaEngine` — `engines/lua.py`
- **Engine types:** `lua`
- **Default target:** `~/Documents/hyprland.lua` (real schemas point at e.g. `~/.config/hypr/edit_here/source/input.lua`)
- **Requires:** a `lua` 5.4+ interpreter on PATH (used to evaluate the AST).

## Target format

Lua scripts built from `hl.*` API calls. The engine sandbox-executes the file
and maps the resulting table tree back onto the source text by tokenizing and
rewriting the exact ranges.

```lua
hl.config({
    input = {
        sensitivity = 0.0,
        touchpad = {
            natural_scroll = true,
        },
    },
})
hl.window_rule({ name = "my_rule", rounding = 0 })
hl.workspace_rule({ workspace = "w[tv1]", border_size = 2 })
hl.bind("SUPER", "Q", { run = "killactive" })
```

## Scope / key mapping

- `hl.config({ ... })` tables: `scope` = the dotted path inside the table,
  written with `/` separators. `input.touchpad.natural_scroll` → `scope="input/touchpad"`,
  `key="natural_scroll"`.
- Any other `hl.<method>({ ... })` call: `scope = "<method>/<id>"` where `id`
  is the value of the `name`, `output`, or `workspace` field inside the table.
  - `hl.window_rule({ name = "my_rule", ... })` → `scope="window_rule/my_rule"`
  - `hl.workspace_rule({ workspace = "w[tv1]", ... })` → `scope="workspace_rule/w[tv1]"`
  - `hl.monitor({ output = "eDP-1", ... })` → `scope="monitor/eDP-1"`
  - `hl.bind("SUPER", "Q", {...})` → `scope="bind/SUPER"` (first argument only; flags table keys like `run` become the item key)
  - If no identifier field exists, the engine uses its runtime insertion index.
- Legacy `tui_*_data` tables (`tui_window_data`, `tui_workspace_data`,
  `tui_layer_data`) map to `window_rule`, `workspace_rule`, `layer_rule`.
- Direct writes target table fields; arbitrary top-level variable assignments
  are not writable bindings.
- **Never inject artificial `name` keys into `workspace_rule` blocks** — the
  compositor rejects them; the engine keys off the native `workspace` string.

## Types & value handling

- `bool` → `true`/`false`; `int`/`float` → raw numbers; `string` → quoted.
- Hex values (`0x...`) are written raw for numeric/non-string item types.
- Strings use Lua byte escapes, including control characters and Unicode.
- `hl.env(name, value)` exposes `key` and `value` under `scope="env/<index>"`.
- `"__DELETE__"` → `nil` (the key is removed from the table).
- Color/theme variables arrive as `__VAR__...` and are re-emitted verbatim.

## Quirks

- Relative includes resolve from the configuration directory. For targets under
  `$XDG_CONFIG_HOME/hypr` (default `~/.config/hypr`), `require` also resolves
  root-relative modules there, after trying the target directory. `dofile` returns
  the included chunk result. Only `.lua` files inside the configuration directory
  are eligible for writes. Loaded-file snapshots detect external changes.
- The mutator preserves comments, whitespace, and inline formatting; it aborts
  the whole batch if a target is missing or a complex expression cannot be
  rewritten. Generated Lua is syntax-checked before any replacement.
- Only executed call sites are writable. Repeated calls from a loop or several
  calls to the same method on one source line are rejected when the target cannot
  be identified uniquely. Put independently editable calls on separate lines.
- Successful writes re-evaluate the state so the cache follows Lua's numeric
  semantics and runtime insertion order.
- Each file replacement is atomic and synced. Multi-file batches are staged
  together but cannot guarantee all-or-nothing replacement across files; a commit
  failure reports how many files were replaced and requires a reload.
- Multiple schema items can target the same key with different scopes; scopes
  use `/` — remember preset payloads and `parent_ref` still use the **dot**
  UID form (`input/touchpad.natural_scroll`).

## Example items

```python
ConfigItem(label="Sensitivity", key="sensitivity", scope="input", type_="float",
           default=0.0, min_val=-1.0, max_val=1.0, step=0.1, group="Sensor"),
ConfigItem(label="Natural Scroll", key="natural_scroll", scope="input/touchpad",
           type_="bool", default=True, group="Touchpad"),
ConfigItem(
    label="Focus", key="follow_mouse", scope="input", type_="int", default=1,
    options=[0, 1, 2, 3], is_parent=True, expanded=False, group="Focus"),
ConfigItem(
    label="Focus Shrink", key="follow_mouse_shrink", scope="input", type_="int",
    default=0, min_val=0, max_val=50, step=1, parent_ref="input.follow_mouse"),
```
