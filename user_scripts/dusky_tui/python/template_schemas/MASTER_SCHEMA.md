# Dusky TUI — Master Schema Reference

This is the **single canonical reference** for writing a Dusky TUI schema file (a
`.py` module the router imports). Read this file first. When you know which
engine your target config needs, also read the matching per-engine doc in
[`engines/`](./engines/) — each one documents that engine's exact `scope`/`key`
semantics, key catalog, and quirks, so you do **not** need to read the engine
source code.

- **Frontend contract:** `python/frontend/core_types.py` (ConfigItem), `python/frontend/ui.py` (rendering)
- **Shared color handling:** `python/frontend/colors.py` (literal parsing, hue adjustment)
- **Router:** `python/main/main.py` (loading, routing, CLI)
- **File I/O shared by the audited engines:** `python/shared/config_io.py`
- **Engine list:** see the [Engine Routing Table](#5-engine-routing-table) below

---

## 1. How a schema is loaded

`main.py <schema>` resolves the schema module by:

1. Direct path (`python main.py ~/user_scripts/hypr/input/tui_input.py`)
2. Dot-notation relative to the search paths: `~/user_scripts`, `~/.config/dusky_schema`, `~/Documents/schemas` (`main.py hypr.input.tui_input` → `~/user_scripts/hypr/input/tui_input.py`)
3. Real schemas live in `~/user_scripts/**/tui_*.py` and are executable on their own (they re-invoke the router via a `__main__` block).

> The router's own `--help` epilog still shows the stale example
> `hypr.input_tui`, which does **not** resolve — use the direct path or
> `hypr.input.tui_input`.

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
| `DEFERRED_LOAD` | `None` | Callable run in a background thread after first paint and again on F5. Return the tab indices to populate; optionally return `(indices, new_items)` to replace `SCHEMA[tab]`, or `(indices, new_items, default_engine_state)` if discovery already read the default engine state. `new_items` may be `None`. The UI re-reads state if a setting changed during discovery. Used for slow/dynamic tabs (systemd services, network scans). In headless mode the router calls it for side-effects. |
| `HIDE_MISSING_ITEMS` | `False` | Opt in to hiding items absent from their engine state and `menu` folders with no visible children. Group headings are generated from visible rows, so empty groups also disappear. The schema stays intact; tab labels remain. Use only when an absent state key means the item is unavailable, including for every routed engine in a mixed-engine schema. Hidden items are excluded from edits and resets. |
| `REQUIRE_ROOT` | `False` | Re-executes the whole router via `sudo`/`su` with real-user `HOME`/`XDG_*` reconstruction. |
| `CUSTOM_VIEWS` | `None` | `{tab_index_or_name: view_spec}` — replaces that tab's `ConfigOptionList` with a custom renderable. `view_spec` may be a `Widget` subclass, a `Widget` instance, a callable `(app) -> renderable`, or `{"view": <above>, "interval": float_seconds}` for auto-refresh. `CustomRichTabWidget` handles `refresh_interval` and scroll bindings. F5 reruns callable views; custom `Widget` implementations can expose a no-argument `update_content()` method to reload their data, otherwise F5 repaints them. See `tui_dusky_network.py` `render_network_dashboard_view`. |

After the first tab paints, the frontend prepares other ready option lists one
per refresh. Tabs populated by `DEFERRED_LOAD` are prepared after that function
returns. Their availability still depends on how long the schema's discovery
work takes.

F5 reloads engine state for every schema and refreshes custom tab views.
Schemas with `DEFERRED_LOAD` rerun discovery first, so dynamic rows are updated
as well. Unsaved edits remain pending; F5 does not discard them.

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
| `min_val` / `max_val` / `step` | `float\|None` | | Numeric bounds / arrow-key step for `int`/`float`. For `color` without options, `step` is the hue adjustment in degrees (default 15); min/max do not apply. |
| `group` | `str\|None` | | Renders a section header (uppercased). Same-group items MUST be contiguous. **Reserved: `"User Presets"`** — do not use it in your schema. |
| `extended_help` | `str\|None` | | Markdown shown in the `?` help panel. Include it on every item. |
| `preset_payload` | `dict\|None` | | For `preset`. Keys are exact item UIDs (`scope.key`, dots even when scope has slashes) with **Python-native values** matching the target item's `type_` (e.g. `True` not `"true"`, `5` not `"5"`); unlisted keys are FORCED back to their `default` on apply. `{"__ALL_DEFAULTS__": True}` resets everything. |
| `is_parent` | `bool` | | Turns ANY item into an expandable folder (hybrid menu). Folders are ONE level deep — never nest a folder inside a folder. |
| `parent_ref` | `str\|None` | | Must exactly equal the parent's UID (`scope.key`). Children render only when the parent is expanded. A dangling ref silently becomes a root row — always verify. |
| `expanded` | `bool` | | Default open/closed state for a parent folder. |
| `warning_msg` | `str\|None` | | ⚠ marker in the row + warning block in the help panel. |
| `popup_message` | `str\|None` | | 'OK' alert shown AFTER a value is applied (skipped on undo and in batch mode). |
| `confirm_message` | `str\|None` | | Yes/No dialog BEFORE mutating (works on items, actions, presets). |
| `target_file_override` | `str\|None` | | Routes this item to a different file (registers a second engine instance). |
| `engine_type_override` | `str\|None` | | Routes this item to a different engine. |
| `force_interactive` | `bool\|None` | | `action` only: `True` forces suspend-TTY execution, `False` forces async non-interactive — overrides command sniffing. |
| `read_only` | `bool` | | Default `False`. Displays observed state but blocks edits, action activation, presets, and resets for this item. |

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
| `color` | Literal colors render as swatches; unresolved variables remain text and serialize with `__VAR__`. Arrows cycle `options`, or adjust hue when no options are supplied. | `str` | options (hybrid), step (hue degrees) |
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

### Color previews and editing

The shared frontend automatically replaces recognized literal color values
with a colored `⬤` in value rows, picker/hybrid options, and the change preview.
Trigger/copy labels such as `options=["trigger:#FF0055"]` also get a swatch.
Schemas do not need their own preview code. Stored values, serialization,
picker selections, and editable input text retain the actual value.

Recognized explicit formats include `#RGB`, `#RGBA`, `#RRGGBB`, `#RRGGBBAA`,
`0xRRGGBB`, `0xAARRGGBB`, Hyprland `rgb(RRGGBB)` / `rgba(RRGGBBAA)`,
comma-separated RGB/RGBA and HSL/HSLA (for example `rgb(255, 0, 85)` and
`hsla(340, 100%, 50%, .5)`), and OKLCH (for example
`oklch(60% 0.2 30deg / 50%)`). The preview shows RGB; opacity remains in the
stored value. Unsupported or malformed values remain visible as text.

Bare hex and CSS color names are recognized in `type_="color"` value rows.
Other row types and picker/hybrid option lists require explicit color syntax,
so ordinary strings such as `fade`, `deadbeef`, or `Red` remain text there.
Unresolved variables such as `$primary`, `rgba($primary)`, `var(--primary)`,
and `{{colors.primary.default.hex}}` display their original text.

For `color` rows, schema `options` take precedence when using arrow keys.
Without options, arrows rotate the current hue by `step` degrees (default 15),
preserving opacity and the color format family. HSL/OKLCH retain their
saturation/chroma and lightness; named colors become hex, and short hex may
expand. Achromatic RGB/hex values and unresolved variables stay unchanged.
Parsing is lazy and cached; plain TUIs do not load the shared color module.

### Info/label rows

For read-only, engine-updated rows use `read_only=True`, `type_="action"`, and
`default=":"` (a no-op shell command) — the engine rewrites `item.label` at
runtime (see the network engine's status/speed-test/hotspot tabs).

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
   over a complete fallback palette derived from Textual's built-in theme.
   No fixed hex fallback palette is maintained in the frontend. A missing or
   unreadable generated file leaves that palette usable at startup. At startup,
   missing keys in a partial theme retain their fallback values. Theme updates are
   watched automatically. If the file disappears or contains malformed JSON,
   the current palette stays active; a later valid file is loaded normally.
4. **Contiguity** — items sharing a `group` must be adjacent; a parent and all
   its `parent_ref` children must form one unbroken block.
5. **Hybrid folders** — `is_parent=True` works on any real type (the header
   holds a value, e.g. a master toggle). Pure `menu` folders are the
   value-less variant. **One level deep only.**
6. **Presets are strict snapshots of the default engine target** — every key
   there that you don't list is reset to its default on apply. List all keys you
   care about, or use `__ALL_DEFAULTS__`. User-profile saves and preset application
   exclude separately routed engine/file overrides. Headless `--default` resets
   those targets separately as well.
7. **Reserved names** — `group="User Presets"` is managed by the TUI itself
   (dynamic user profiles); never emit it. `key` prefixes like `action_`,
   `preset_`, `__user_preset_`, `__save_new_preset`, `__import_new_preset` are
   convention; the two `__` keys above are special-cased by the UI.
8. **Missing values** — by default, an absent engine state key uses the
   item's schema default when one is provided; items with `default="nil"`
   remain visible as `[Missing]`. With `HIDE_MISSING_ITEMS = True`, absent
   items and empty `menu` folders are hidden instead. The engine must omit
   only genuinely unavailable items for that schema. Engines that bridge
   state (bridged_ini, cmdline, systemd_boot) or virtualize defaults
   (trackpad, monitor, autostart) have their own state-key behavior.
9. **Root privileges** — an engine can return `AUTH_REQUIRED` when a protected
   write needs authorization; the TUI handles the existing password flow.
   The INI engines use the sudo credential cache to run the shared atomic save
   helper. Other engines have their own privilege handling; follow their docs
   or set `REQUIRE_ROOT = True` to re-execute the router with the real user's
   environment reconstructed.

### File-engine reads and writes

The public engine contract remains `target_path`, `load_state()`,
`write_value(key, scope, serialized_value, item_type)`, and
`write_batch([(key, scope, serialized_value, item_type), ...])`. Writes return
`(success, message, debug)`. Always inspect `success`; debug output can report
that configuration was saved but a subsequent service reload failed.

INI, Bridged INI, FlatDotConfig, Matugen, and TOML use the shared
`python/shared/config_io.py` read/commit helpers. Lua uses its snapshot helpers and
separate staging for multiple files. Schemas do not import this helper or
implement their own save routines; the router supplies the engine instances.
Other engines retain their own implementations and guarantees.

- The audited engines detect changes to writable targets since state was loaded
  and refuse stale writes with a reload-required result. Refresh state before
  retrying; F5 preserves pending UI edits.
- File replacement is atomic per file. Shared commits preserve existing
  ownership and mode, sync the file and directory, and clean up staged files.
  Lua prepares all replacements before committing, but an error during the
  replacement phase can leave some files saved; its error reports that count.
- A schema default supplies a displayed value for an absent key; it does not
  guarantee that the engine can create that binding. Lua requires every batch
  target to exist at an executed, unambiguous writable call site. Repeated
  calls and several calls to the same method on one source line can be
  ambiguous. Missing or ambiguous targets abort the batch before replacement.
- Matugen toggles existing template blocks. Missing or duplicate template names
  abort the batch; the resulting active TOML is validated before saving.
- INI and FlatDotConfig values cannot contain physical newlines or NUL.
  FlatDotConfig's `:N` suffix selects an existing duplicate or the next
  appendable occurrence; gaps fail before saving.
- TOML regenerates the document and discards comments. Deleting an absent
  nested key does not create empty tables. Slash-separated paths preserve
  literal dots in their individual components; see the per-engine docs for
  exact path semantics.

Lua resolves local modules from the target directory first. Targets under
`$XDG_CONFIG_HOME/hypr` (default `~/.config/hypr`) also resolve `require` modules
from that configuration root. Only loaded `.lua` files within the target
configuration directory are eligible for writes. See [engines/lua.md](./engines/lua.md).

---

## 5. Engine Routing Table

`ENGINE_TYPE` is case-insensitive. The Doc column links each engine's reference;
TLP currently links its implementation because it has no separate engine doc.

| ENGINE_TYPE | Doc | Class | Default target | Scope semantics (summary) |
|---|---|---|---|---|
| `ini` | [engines/ini.md](./engines/ini.md) | `IniConfigEngine` | `/etc/pacman.conf` | `[section]` → scope; root keys → `DEFAULT`; valueless flags |
| `tlp` | [source](../engines/tlp.py) | `TlpConfigEngine` | `/etc/tlp.conf` | scope `DEFAULT`; explicit root `KEY=VALUE` / `KEY+=VALUE` settings; TLP 1.11+ profile keys |
| `bridged_ini` | [engines/bridged_ini.md](./engines/bridged_ini.md) | `BridgedIniEngine` | any INI | like ini, but commented-out defaults are read too |
| `lua` | [engines/lua.md](./engines/lua.md) | `HyprlandLuaEngine` | `~/Documents/hyprland.lua` | `hl.config` table path (`a/b`); `hl.method` → `method/<id>` |
| `neovim` | [engines/neovim.md](./engines/neovim.md) | `NeovimEngine` | deployed `~/.config/nvim/lua/config/options.lua` | native `options`/`globals` assignments and literal plugin `opts/...` fields |
| `autostart` | [engines/autostart.md](./engines/autostart.md) | `AutostartLuaEngine` | hyprland Lua | scope `autostart`, fixed key catalog |
| `trackpad` | [engines/trackpad.md](./engines/trackpad.md) | `TrackpadLuaEngine` | hyprland Lua | scope `gestures`; gestures via `gesture/<f>/<dir>` |
| `monitor` | [engines/monitor.md](./engines/monitor.md) | `MonitorLuaEngine` | `~/Documents/monitors.lua` | scope `monitor/<name>`; `workspace_rule/<1-10>`; globals `misc`/`debug`/`render` |
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
| `matugen_presets` / `matugen_color` / `color_presets` / `theme_presets` | [engines/matugen_presets.md](./engines/matugen_presets.md) | `MatugenPresetsEngine` | `~/.config/dusky/settings/dusky_theme/state.conf` | scope `DEFAULT`; theme state, color presets, favorites, and theme-controller actions |
| `fontconfig` | [engines/fontconfig.md](./engines/fontconfig.md) | `FontconfigEngine` | `~/.config/fontconfig/conf.d/99-dusky-fonts.conf` | family aliases + render props |
| `toml` / `toml_engine` | [engines/toml.md](./engines/toml.md) | `TomlEngine` | any TOML | dotted table path, deep nesting |
| `kokoro` / `dusky_kokoro` | [engines/kokoro.md](./engines/kokoro.md) | `KokoroEngine` | `~/.config/dusky-kokoro/config.toml` | TOML scopes plus virtual voice-blend controls and daemon telemetry; inherits `TomlEngine` |
| `systemd_dns` | [engines/systemd_dns.md](./engines/systemd_dns.md) | `SystemdDnsEngine` | `/etc/systemd/resolved.conf.d/99-dns-tui.conf` | fixed `[Resolve]` keys |
| `starship` | [engines/starship.md](./engines/starship.md) | `StarshipEngine` | `~/.config/starship.toml` | scope ignored; keys `active_prompt` (preset selector), `custom_prompt_name` (string), `action_save_custom` (trigger) — atomic whole-file TOML swap, hash-matched state file |
| `hyprlock` | [engines/hyprlock.md](./engines/hyprlock.md) | `HyprlockEngine` | `~/.config/hypr/hyprlock.conf` | scope ignored; keys `hyprlock`/`active_theme_number` (int), `active_theme_folder` (str), `active_theme_name` (str), `toggle_forward`/`toggle_backward` (triggers) |
| `systemd_power` / `systemd_logind` / `power_engine` | [engines/systemd_power.md](./engines/systemd_power.md) | `SystemdPowerEngine` | `/etc/systemd/logind.conf.d/99-power.conf` | scope `Login`; systemd-logind drop-in with base bridging, defaults virtualization, daemon reload |
| `ufw` / `ufw_firewall` | [engines/ufw.md](./engines/ufw.md) | `UfwEngine` | `/etc/default/ufw` | scopes `status`, `builder`, `domains`, `framework`, `actions`, `reports`; complete rule lifecycle, DNS domain whitelisting/lockdown, NAT, Docker mitigation |
| `gsettings` / `dconf` | [engines/gsettings.md](./engines/gsettings.md) | `GSettingsEngine` | `~/.config/dconf/user` | GSettings schema ID → scope; schema key → `key`; PyGObject in-process Gio with CLI fallback; live Hyprland cursor sync |

> `engines/rich_speedtest.py` is **not** an engine — it is a Rich-based speed
> test UI helper invoked by the `network` engine.

---

## 6. Headless CLI (useful for testing a schema)

```bash
python main.py hypr.input.tui_input              # launch TUI (or pass the path)
python main.py ~/user_scripts/hypr/input/tui_input.py --set sensitivity=0.5  # headless set (scope.key)
python main.py hypr.input.tui_input --set key=value       # unambiguous bare key ok
python main.py hypr.input.tui_input --reset-key <key>     # reset one key to default
python main.py hypr.input.tui_input --backup --default    # back up, then reset writable settings
python main.py hypr.input.tui_input --export-state        # dump engine state JSON
python main.py hypr.input.tui_input --export-docs         # markdown docs from schema
python main.py hypr.input.tui_input --backup              # back up, then launch TUI
python main.py hypr.input.tui_input --restore             # restore the latest backup and exit
```

Ambiguous bare keys (same key in multiple scopes) require `scope.key`.
`--default` alone does not create a backup; add `--backup` when one is needed.
Different routed engines can complete independently during a reset, so check
its exit status and output rather than assuming a transaction across targets.

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
    default        = False,                # native Python type MUST match type_
                                           # action -> shell command string
                                           # menu / preset -> None
    options        = [],                   # required for cycle/picker; hybrid dropdown for int/float/string/color
    hints          = [],                   # picker subtitles (positionally aligned with options)
    min_val        = None,                 # int/float lower bound
    max_val        = None,                 # int/float upper bound
    step           = None,                 # int/float arrow step; color hue degrees (default 15)
    group          = "OneWord",            # section header; contiguous blocks
    extended_help  = "**Help**\n\nMarkdown explaining the setting.",
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
    read_only      = False,                # True -> display only; block writes/actions/resets
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
TARGET_FILE = "~/.config/dusky/settings/example/config.ini"
APP_TITLE = "Dusky Config"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Profiles"
# HIDE_MISSING_ITEMS = True  # Opt in only if absent engine keys mean unavailable items.

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
