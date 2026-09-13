# Dusky TUI — Master Schema Reference

This is the **single canonical reference** for writing a Dusky TUI schema file (a
`.py` module the router imports). Read this file first. When you know which
engine your target config needs, also read the matching per-engine doc in
[`engines/`](./engines/) — each one documents that engine's exact `scope`/`key`
semantics, key catalog, and quirks, so you do **not** need to read the engine
source code.

- **Frontend contract:** `python/frontend/core_types.py` (ConfigItem), `python/frontend/ui.py` (rendering)
- **Router:** `python/main/main.py` (loading, routing, CLI)
- **Engine list:** see the [Engine Routing Table](#engine-routing-table) below

---

## 1. How a schema is loaded

`main.py <schema>` resolves the schema module by:

1. Direct path (`python main.py ~/user_scripts/hypr/input/tui_input.py`)
2. Dot-notation relative to the search paths: `~/user_scripts`, `~/.config/dusky_schema`, `~/Documents/schemas` (`main.py hypr.input.tui_input` → `~/user_scripts/hypr/input/tui_input.py`)

> The router's own `--help` epilog still shows the stale example
> `hypr.input_tui`, which does **not** resolve — use the direct path or
> `hypr.input.tui_input`.
3. Real schemas live in `~/user_scripts/**/tui_*.py` and are executable on their own (they re-invoke the router via a `__main__` block).

Required module attributes (missing one → fatal error):

| Attribute | Type | Required | Notes |
|---|---|---|---|
| `ENGINE_TYPE` | `str` | ✅ | Lowercased by the router. Must be one of the engine names below. |
| `TARGET_FILE` | `str` | ✅ | Expanded + resolved. Ignored by file-less engines (systemd, network, cpu_core, pkg_throttle) but must still be set. |
| `SCHEMA` | `dict[int, list[ConfigItem]]` | ✅ | Tab index → items. |
| `TABS` | `list[str] \| dict[int, str]` | ✅ | Index-aligned with `SCHEMA` keys. Router normalizes a `list` via `dict(enumerate(...))`; a `dict` may use sparse indices. |

Optional module attributes:

| Attribute | Default | Notes |
|---|---|---|
| `APP_TITLE` | `"Dusky Configurator"` | Shown in the TUI border. |
| `DEFAULT_MODE` | `"auto"` | `"auto"` = instant save; anything else = batch (Ctrl+S). |
| `THEME_FILE` | `None` | Matugen JSON path. The ecosystem convention is **exactly** `~/.config/matugen/generated/dusky_tui.json`. Do not alter this string. |
| `ENABLE_USER_PRESETS` | `True` | Enables Ctrl+P save / D delete of user profiles. |
| `USER_PRESETS_TAB` | auto-detected | Must exactly match a tab name. Auto-falls back to a tab named `presets`/`theme`/`themes`/`appearance`/`profiles` (case-insensitive). |
| `GLOBAL_POPUP` | `None` | `{"title": str, "message": str, "level": "info"\|"warning"\|"danger"\|"success", "require_confirm": bool (default False), "cancel_quits": bool (default False), "btn_text": str}` — shown once after first render; if `require_confirm` the Yes/No dialog quits when cancelled and `cancel_quits` is set. |
| `TAB_NOTICES` | `None` | `{tab_index: {"level": "info"\|"warning"\|"danger"\|"success", "message": str, "position": "top"\|"bottom" (default top)}}` or `{tab_index: [{...}, ...]}` — persistent `NoticeBox` banner(s) rendered above/below the option list for that tab. |
| `DEFERRED_LOAD` | `None` | Callable `() -> list[int] \| tuple[list[int], dict[int, list[ConfigItem]]]` run in a background thread after first paint. Return the tab indices to populate; optionally return `(indices, new_items)` to replace `SCHEMA[tab]` before state is re-loaded. Used for slow/dynamic tabs (systemd services, network scans). In headless mode the router just calls it for side-effects. |
| `REQUIRE_ROOT` | `False` | Re-executes the whole router via `sudo`/`su` with real-user `HOME`/`XDG_*` reconstruction and `XDG_CONFIG_HOME` chown fix. |
| `CUSTOM_VIEWS` | `None` | `{tab_index_or_name: view_spec}` — replaces that tab's `ConfigOptionList` with a custom renderable. `view_spec` may be a `Widget` subclass, a `Widget` instance, a callable `(app) -> renderable`, or `{"view": <above>, "interval": float_seconds}` for auto-refresh. `CustomRichTabWidget` handles `refresh_interval` and scroll bindings. See `tui_dusky_network.py` `render_network_dashboard_view`. |
| `APPLY_COMMAND` | `None` | Shell command string run by the `[A] Apply` footer button and the `A` key. Shown only when set. Runs through the `action` path with status messages. Example: `APPLY_COMMAND = "python3 ~/user_scripts/cursor/color/dusky_cursor.py --apply"`. |
| `ROW_GATE` | `None` | `{"watch_key": str, "allow": [values], "gated": [keys], "message": str}` or a list of such dicts. While the watched item holds a value outside `allow`, the gated rows render disabled and `message` shows once in the status bar. Silent when allowed. A rule without `message` gates silently. A rule with `"hide": true` removes the row instead of disabling it. |

---

## 2. ConfigItem — complete field reference

Source of truth: `frontend/core_types.py`. All fields except `label`, `key`,
`type_`, `default` are optional (kw_only dataclass).

| Field | Type | Required | Notes |
|---|---|---|---|
| `label` | `str` | ✅ | Display text. Keep succinct (one short phrase); the engine often overwrites it for live-updating info rows. |
| `key` | `str` | ✅ | Backend key. Must be unique per scope (see UID rule). |
| `type_` | `str` | ✅ | One of: `bool`, `int`, `float`, `string`, `cycle`, `picker`, `color`, `menu`, `action`, `preset`. |
| `default` | `Any` | ✅ | Python-native type MUST match `type_` (`True` not `"true"`, `10` not `"10"`). For `action` this is the shell command string; for `menu`/`preset` it must be `None`. |
| `scope` | `str` | | `"DEFAULT"` for root/global keys. Engine-specific otherwise (section name, block name, dotted path, `/`-joined path…). See per-engine docs. |
| `options` | `list` | | `cycle`/`picker` require it. On `int`/`float`/`string`/`color` it turns the row into a hybrid dropdown. Elements may be non-strings (ints are fine). |
| `hints` | `list[str]` | | Subtitles for `picker` modals, positionally aligned with `options` (missing entries render without a subtitle; extras are ignored). |
| `min_val` / `max_val` / `step` | `float\|None` | | Numeric bounds / arrow-key step for `int`/`float`. |
| `group` | `str\|None` | | Renders a section header (uppercased). Same-group items MUST be contiguous. **Reserved: `"User Presets"`** — do not use it in your schema. |
| `extended_help` | `str\|None` | | Markdown shown in the `?` help panel. Include it on every item. |
| `preset_payload` | `dict\|None` | | For `preset`. Keys are exact item UIDs (`scope.key`, dots even when scope has slashes) with **Python-native values** matching the target item's `type_` (e.g. `True` not `"true"`, `5` not `"5"`); unlisted keys are FORCED back to their `default` on apply. `{"__ALL_DEFAULTS__": True}` resets everything. |
| `is_parent` | `bool` | | Turns ANY item into an expandable folder (hybrid menu). Folders are ONE level deep — never nest a folder inside a folder. |
| `parent_ref` | `str\|None` | | Must exactly equal the parent's UID (`scope.key`). Children render only when the parent is expanded. A dangling ref silently becomes a root row — always verify. |
| `expanded` | `bool` | | Default open/closed state for a parent folder. |
| `warning_msg` | `str\|None` | | ⚠ marker in the row + warning block in the help panel. |
| `popup_message` | `str\|None` | | 'OK' alert shown AFTER a value is applied (skipped on undo and in batch mode). |
| `confirm_message` | `str\|None` | | Yes/No dialog BEFORE mutating (works on items, actions, presets). |
| `tooltip` | `str\|None` | | Short hint shown in the bottom status bar while hovering the row. |
| `target_file_override` | `str\|None` | | Routes this item to a different file (registers a second engine instance). |
| `engine_type_override` | `str\|None` | | Routes this item to a different engine. |
| `force_interactive` | `bool\|None` | | `action` only: `True` forces suspend-TTY execution, `False` forces async non-interactive — overrides command sniffing. |

Internal fields (do not set in a schema): `value`, `exists_in_target`,
`initial_value`, `_initial_loaded`, `_ratio_cache`.

**`uid` property** (used for `parent_ref`, `preset_payload`, headless `--set`):
`f"{scope}.{key}"` when `scope != "DEFAULT"`, else just `key`.

**State lookup** (`_lookup_state`) tries, in order: `scope/key`, `scope.key`,
`DEFAULT/key`, `DEFAULT.key`, bare `key`. Engines populate state with `/`
separators (`input/touchpad/natural_scroll`).

**Serialization** (`serialize`): bool → `"true"`/`"false"`; color values that are
theme variables (`$x`, `@x`, `var(...)`, `{{...}}`, or any bare non-color
identifier) → prefixed `__VAR__<val>`; `None` → `"nil"`. `deserialize` strips
`__VAR__` and surrounding quotes.

---

## 3. The ten item types

| type_ | Behavior | default | extra fields |
|---|---|---|---|
| `bool` | Instant toggle. **Trigger**: if `options` starts with `"trigger"` or `"copy"` the row becomes a momentary push button (runs an action, resets to False) — the standard way to build buttons. | `True`/`False` | `options=["trigger"]` |
| `int` | Integer; arrows adjust by `step`, clamped to min/max. `options` → hybrid dropdown. | `int` | min/max/step/options |
| `float` | Decimal; same mechanics as int. | `float` | min/max/step/options |
| `string` | Free text input; `options` → hybrid dropdown. | `str` | options |
| `cycle` | Left/right instant cycling through `options`. | `str` | options (required) |
| `picker` | Fullscreen modal list over `options` with `hints` subtitles (arrow-key navigation; not searchable — that's the global Ctrl+F `SearchScreen`). | `str` | options (required), hints |
| `color` | Hex/RGB/HSL/oklch/CSS-named/theme-variable. Theme variables serialize with `__VAR__`. | `str` | options (hybrid) |
| `menu` | Pure UI folder, no backend value. | `None` | `is_parent=True` (required) |
| `action` | Runs `default` as a shell command. Interactive commands (vim, ssh, tui…) run with TTY suspended; set `force_interactive` to override. 15s timeout for non-interactive. | shell command string | confirm_message, force_interactive, popup_message |
| `preset` | Applies a strict state snapshot from `preset_payload`; omitted keys revert to defaults. | `None` | preset_payload, confirm_message |

### Trigger buttons (`bool` + trigger options)

`is_trigger_item()`: a `bool` item whose first option is `"trigger"`, `"copy"`,
or starts with `"trigger:"`/`"copy:"` (the suffix becomes the button label, e.g.
`options=["trigger:Restart"]` renders a `Restart` button). Trigger items:
- never persist a value — after the write, the value resets to `default`
  (keep `default=False`),
- run synchronously through the engine `write_value` path,
- render as a button (Apply / Copy / custom label) instead of a toggle.

`copy` triggers are a convention for engine-updated status rows: the engine
(e.g. `network`'s `clipboard` scope) reads the row label and copies its value
part to the clipboard — the copy itself is engine-side, not generic UI.

### Info/label rows

For read-only, engine-updated rows use `type_="action"` with `default=":"` (a
no-op shell command) — the engine rewrites `item.label` at runtime (see the
network engine's status/speed-test/hotspot tabs).

> ⚠️ `default="nil"` is the **serialized form of `None`**, not a shell command.
> If an `action` row with that default is activated, the TUI executes the
> literal command `nil` (fails). Use `":"` for no-op label rows, and always
> give real actions a real command in `default`.

---

## 4. Rules (critical)

1. **UID rule** — root-level keys: `scope="DEFAULT"`, UID = `key`. Scoped keys:
   UID = `scope.key`. Use the exact UID in `parent_ref` and `preset_payload`.
2. **Naming** — keep `label` short. `group`/tab names render uppercased in a
   fixed bar; prefer short single words, but multi-word labels work fine
   (several real schemas use them). Use descriptive, semantic backend `key`s.
3. **Theme file axiom** — `THEME_FILE` must stay
   `~/.config/matugen/generated/dusky_tui.json`; the TUI reads keys like
   `bg`, `fg`, `accent`, `error`, `warning`, `success`, `muted`, `info` from it
   (with hardcoded fallbacks).
4. **Contiguity** — items sharing a `group` must be adjacent; a parent and all
   its `parent_ref` children must form one unbroken block.
5. **Hybrid folders** — `is_parent=True` works on any real type (the header
   holds a value, e.g. a master toggle). Pure `menu` folders are the
   value-less variant. **One level deep only.**
6. **Presets are strict snapshots** — every key you don't list is reset to its
   default on apply. List all keys you care about, or use `__ALL_DEFAULTS__`.
7. **Reserved names** — `group="User Presets"` is managed by the TUI itself
   (dynamic user profiles); never emit it. `key` prefixes like `action_`,
   `preset_`, `__user_preset_`, `__save_new_preset`, `__import_new_preset` are
   convention; the two `__` keys above are special-cased by the UI.
8. **Missing values** — an item whose key is absent from the engine state is
   rendered struck-through as `[Missing]` unless the engine bridges state
   (bridged_ini, cmdline, systemd_boot) or virtualizes defaults (trackpad,
   monitor, autostart). Write schemas against the engine's documented state
   keys so items resolve.
9. **Root privileges** — engines touching system files either fall back to
   `sudo -n tee` internally (returning `AUTH_REQUIRED`, which the TUI turns
   into a password prompt) or you set `REQUIRE_ROOT = True` on the schema.
   `REQUIRE_ROOT` re-executes with the real user's environment reconstructed.

---

## 5. Engine Routing Table

`ENGINE_TYPE` is case-insensitive. `engines/<name>.md` is the per-engine doc.

| ENGINE_TYPE | Doc | Class | Default target | Scope semantics (summary) |
|---|---|---|---|---|
| `ini` | [engines/ini.md](./engines/ini.md) | `IniConfigEngine` | `/etc/pacman.conf` | `[section]` → scope; root keys → `DEFAULT`; valueless flags |
| `bridged_ini` | [engines/bridged_ini.md](./engines/bridged_ini.md) | `BridgedIniEngine` | any INI | like ini, but commented-out defaults are read too |
| `lua` | [engines/lua.md](./engines/lua.md) | `HyprlandLuaEngine` | `~/Documents/hyprland.lua` | `hl.config` table path (`a/b`); `hl.method` → `method/<id>` |
| `autostart` | [engines/autostart.md](./engines/autostart.md) | `AutostartLuaEngine` | hyprland Lua | scope `autostart`, fixed key catalog |
| `trackpad` | [engines/trackpad.md](./engines/trackpad.md) | `TrackpadLuaEngine` | hyprland Lua | scope `gestures`; gestures via `gesture/<f>/<dir>` |
| `monitor` | [engines/monitor.md](./engines/monitor.md) | `MonitorLuaEngine` | `~/Documents/monitors.lua` | scope `monitor/<name>`; globals `misc`/`debug`/`render` |
| `systemd` | [engines/systemd.md](./engines/systemd.md) | `SystemdEngine` | (none) | scope `user`/`system`; key = unit name |
| `hyprlang` | [engines/hyprlang.md](./engines/hyprlang.md) | `HyprlangEngine` | hypr*.conf | block name → scope; `cat:key` inline; `$var` → `DEFAULT` |
| `cmdline` | [engines/cmdline.md](./engines/cmdline.md) | `CmdlineEngine` | `/etc/kernel/cmdline` | scope `DEFAULT`; kernel params, `:N` dupes |
| `systemd_boot` | [engines/systemd_boot.md](./engines/systemd_boot.md) | `SystemdBootEngine` | `/boot/loader/entries/arch-linux.conf` | scope `DEFAULT`; `options` line tokens |
| `flatdotconfig` | [engines/flatdotconfig.md](./engines/flatdotconfig.md) | `FlatDotConfigEngine` | `~/.config/gpu-screen-recorder/config_ui` | dot-notation key → `scope`/`key`, `:N` dupes |
| `env` | [engines/env.md](./engines/env.md) | `ShellEnvEngine` | `/etc/locale.conf` | scope `DEFAULT`; `KEY=VALUE`, export-aware |
| `shell_fallback` | [engines/shell_fallback.md](./engines/shell_fallback.md) | `ShellFallbackEngine` | any | `readonly KEY="${KEY:-VAL}"` |
| `waybar` | [engines/waybar.md](./engines/waybar.md) | `WaybarEngine` | `~/.config/waybar` | theme switching keys |
| `network` | [engines/network.md](./engines/network.md) | `NetworkManagerEngine` | (nmcli) | fixed scopes + dynamic per-SSID keys |
| `pkg_throttle` | [engines/pkg_throttle.md](./engines/pkg_throttle.md) | `PkgThrottleEngine` | (RAPL sysfs) | scope `DEFAULT`; `pl1`/`pl2`/`pl4`/`pl*_time` |
| `cpu_core` | [engines/cpu_core.md](./engines/cpu_core.md) | `CpuCoreEngine` | (sysfs) | scope `DEFAULT`; `cpu<N>` bools |
| `fstab` | [engines/fstab.md](./engines/fstab.md) | `FstabEngine` | `/etc/fstab` | fixed scopes `mount_info`/`filesystem`/`btrfs_ops`/`system_flags` |
| `json` | [engines/json.md](./engines/json.md) | `JsonEngine` | any JSON/JSONC | dotted scope path, deep nesting |
| `dusky_sites` | [engines/dusky_sites.md](./engines/dusky_sites.md) | `DuskySitesEngine` | `~/.config/dusky/settings/dusky_sites/config.json` | fixed keys + `site_*`/`domain_*` |
| `locale_gen` | [engines/locale_gen.md](./engines/locale_gen.md) | `LocaleGenEngine` | `/etc/locale.gen` | locale codes as bools + actions |
| `matugen` / `matugen_toml` | [engines/matugen.md](./engines/matugen.md) | `MatugenEngine` | `~/.config/matugen/config.toml` | template names as bools |
| `fontconfig` | [engines/fontconfig.md](./engines/fontconfig.md) | `FontconfigEngine` | `~/.config/fontconfig/conf.d/99-dusky-fonts.conf` | family aliases + render props |
| `toml` / `toml_engine` | [engines/toml.md](./engines/toml.md) | `TomlEngine` | any TOML | dotted table path, deep nesting |
| `systemd_dns` | [engines/systemd_dns.md](./engines/systemd_dns.md) | `SystemdDnsEngine` | `/etc/systemd/resolved.conf.d/99-dns-tui.conf` | fixed `[Resolve]` keys |
| `starship` | [engines/starship.md](./engines/starship.md) | `StarshipEngine` | `~/.config/starship.toml` | scope ignored; keys `active_prompt` (preset selector), `custom_prompt_name` (string), `action_save_custom` (trigger) — atomic whole-file TOML swap, hash-matched state file |
| `hyprlock` | [engines/hyprlock.md](./engines/hyprlock.md) | `HyprlockEngine` | `~/.config/hypr/hyprlock.conf` | scope ignored; keys `hyprlock`/`active_theme_number` (int), `active_theme_folder` (str), `active_theme_name` (str), `toggle_forward`/`toggle_backward` (triggers) |
| `systemd_power` | [engines/systemd_power.md](./engines/systemd_power.md) | `SystemdPowerEngine` | `/etc/systemd/logind.conf.d/99-power.conf` | scope `Login`; systemd-logind drop-in with base bridging, defaults virtualization, daemon reload |

> `engines/rich_speedtest.py` is **not** an engine — it is a Rich-based speed
> test UI helper invoked by the `network` engine.

---

## 6. Headless CLI (useful for testing a schema)

```bash
python main.py hypr.input.tui_input              # launch TUI (or pass the path)
python main.py ~/user_scripts/hypr/input/tui_input.py --set sensitivity=0.5  # headless set (scope.key)
python main.py hypr.input.tui_input --set key=value       # unambiguous bare key ok
python main.py hypr.input.tui_input --reset-key <key>     # reset one key to default
python main.py hypr.input.tui_input --default             # reset everything (backs up)
python main.py hypr.input.tui_input --export-state        # dump engine state JSON
python main.py hypr.input.tui_input --export-docs         # markdown docs from schema
python main.py hypr.input.tui_input --backup / --restore  # atomic file backups
```

Ambiguous bare keys (same key in multiple scopes) require `scope.key`.

---

## 7. Quick-reference cheat sheet

Copy this block when building new items:

```python
from python.frontend.core_types import ConfigItem

ConfigItem(
    label          = "ShortName",          # succinct display text
    key            = "backend_key",        # unique within its scope
    scope          = "DEFAULT",            # engine-specific section/block/path or DEFAULT
    type_          = "bool",               # bool|int|float|string|cycle|picker|color|menu|action|preset
    default        = None,                 # native Python type MUST match type_
                                           # action -> shell command string
                                           # menu / preset -> None
    options        = [],                   # required for cycle/picker; hybrid dropdown for int/float/string/color
    hints          = [],                   # picker subtitles (positionally aligned with options)
    min_val        = None,                 # int/float lower bound
    max_val        = None,                 # int/float upper bound
    step           = None,                 # int/float arrow step
    group          = "OneWord",            # section header; contiguous blocks
    extended_help  = "**Help**\\n\\nMarkdown explaining the setting.",
    is_parent      = False,                # True -> expandable hybrid folder (any type)
    parent_ref     = None,                 # exact parent UID "scope.key" (or "key" if DEFAULT)
    expanded       = False,                # parent folder default state
    preset_payload = None,                 # preset only: {"scope.key": value} or {"__ALL_DEFAULTS__": True}
    warning_msg    = None,                 # ⚠ marker + help-panel warning
    popup_message  = None,                 # OK alert AFTER applying
    confirm_message= None,                 # Yes/No dialog BEFORE mutating
    target_file_override = None,           # route to another file
    engine_type_override = None,           # route to another engine
    force_interactive = None,              # action only: force TTY vs async run
)

# Trigger button (momentary action):
ConfigItem(label="Rescan", key="rescan", scope="network", type_="bool",
           default=False, options=["trigger"], group="Actions")
```

## 8. Minimal full schema (template)

```python
#!/usr/bin/env python3
import sys
from pathlib import Path

_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

ENGINE_TYPE = "ini"
TARGET_FILE = "~/.config/mako/config"
APP_TITLE = "Dusky Config"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Profiles"

TABS = ["Core", "Profiles"]

SCHEMA = {
    0: [
        ConfigItem(
            label="Logging",
            key="log_level",
            scope="DEFAULT",
            type_="cycle",
            default="info",
            options=["debug", "info", "warning", "error"],
            group="System",
            extended_help="**Logging**\n\nSets the verbosity of internal logs.",
        ),
        ConfigItem(
            label="Concurrency",
            key="max_threads",
            scope="DEFAULT",
            type_="int",
            default=4,
            min_val=1,
            max_val=32,
            step=1,
            group="System",
            extended_help="**Thread Allocation**\n\nMaximum simultaneous background threads.",
        ),
    ],
    1: [
        ConfigItem(
            label="Reset",
            key="preset_factory_reset",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="System",
            confirm_message="Reset everything to defaults?",
            preset_payload={"__ALL_DEFAULTS__": True},
            extended_help="**Factory Reset**\n\nReverts all items to their defaults.",
        ),
    ],
}

if __name__ == "__main__":
    import subprocess
    script_path = Path(__file__).resolve()
    main_router = _DUSKY_TUI_ROOT / "python" / "main" / "main.py"
    sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
```
