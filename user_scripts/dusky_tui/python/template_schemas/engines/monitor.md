# Engine: `monitor`

- **Class:** `MonitorLuaEngine` — `engines/monitor_engine.py`
- **Engine types:** `monitor`
- **Default target:** `~/Documents/monitors.lua`

## What it does

Extends the `lua` AST engine for Hyprland 0.55+ monitor and workspace configuration:
- Monitor blocks (`hl.monitor({ output = "eDP-1", ... })`)
- Workspace rule bindings (`hl.workspace_rule({ workspace = "1", monitor = "eDP-1", default = true })`)
- Global render and power settings (`hl.config({ misc = ..., debug = ..., render = ... })`)

Queries live hardware state via `hyprctl -j monitors all`, injects virtual state so defaults never show `[Missing]`, enforces Wayland 1/120 quantum fractional scaling (`wp_fractional_scale_v1`), calculates relative spatial positioning geometry, and auto-injects missing monitor, workspace rule, and global blocks.

## Scope / key mapping

### 1. Per-monitor — `scope="monitor/<name>"` (e.g. `monitor/eDP-1`)
The engine maps UI names to AST names (including `desc:` identifiers) automatically.

| key | type | notes |
|---|---|---|
| `output` | string | monitor identifier (port name or `desc:...`) |
| `enabled` | bool | virtualized toggle; bi-directionally mapped to `disabled` in Lua |
| `disabled` | bool | native Hyprland property |
| `mode` | string | `preferred` / `highres` / `highrr` / `maxwidth` / `WxH@Hz` |
| `position` | string | `auto` keywords or exact `X,Y` relative coordinates |
| `scale` | float/str | fractional scaling; snapped to Hyprland's 1/120 quantum grid |
| `transform` | int | 0–7 rotation/flip |
| `vrr` | int | 0 (Off) / 1 (On) / 2 (Fullscreen Only) |
| `bitdepth` | int | 8 / 10 / 16 (HDR float) |
| `cm` | string | color management preset (`auto`, `srgb`, `hdr`, etc.) |
| `sdr_eotf` | string | `default` / `srgb` / `gamma22` |
| `sdrbrightness` | float | SDR brightness in HDR mode (0.1–3.0) |
| `sdrsaturation` | float | SDR saturation in HDR mode (0.1–2.0) |
| `mirror` | string | mirrored output name or empty |
| `icc` | string | absolute path to ICC profile or empty |
| `reserved_area` | int | reserved margin on all edges (pixels) |

Additional read-only capability keys virtualized per monitor:
`supports_wide_color`, `supports_hdr`, `sdr_min_luminance`, `sdr_max_luminance`, `min_luminance`, `max_luminance`, `max_avg_luminance`.

### 2. Workspace rules — `scope="workspace_rule/<1-10>"` (e.g. `workspace_rule/1`)
Maps to `hl.workspace_rule({ workspace = "<id>", monitor = "<mon>", default = <bool>, persistent = <bool> })`.

| key | type | notes |
|---|---|---|
| `monitor` | string | target monitor identifier |
| `default` | bool | focus automatically when monitor connects |
| `persistent` | bool | keep workspace visible in the bar even when empty |

### 3. Global render/power — scopes `misc`, `debug`, `render` (deep-merged via `hl.config`)
- `misc/vrr` (int: 0, 1, 2)
- `debug/vfr` (bool: variable frame rate)
- `debug/overlay` (bool: FPS and damage region overlay)
- `render/cm_sdr_eotf` (string: `auto`, `srgb`, `gamma22`)
- `render/direct_scanout` (int: 0 = off, 1 = always on, 2 = auto for games)
- `render/cm_auto_hdr` (int: 0 = off, 1 = auto HDR, 2 = auto HDR EDID)

## Quirks

- `scale` values are validated and coerced to Hyprland's **1/120 quantum grid** (`clean_scale` and `closest_sharp`), ensuring $(120 \times W) \pmod n == 0$ and $(120 \times H) \pmod n == 0$ to eliminate subpixel yellow warning banners.
- When mutating a key on a monitor or workspace block that does not yet declare that property, `MonitorLuaEngine` safely injects the property into the active block before AST mutation.
- Entirely missing monitor blocks, workspace rule bindings, and global config sections are auto-injected before writing.

## Example items

```python
ConfigItem(label="Scale", key="scale", scope="monitor/eDP-1", type_="picker",
           default="1", options=["auto", "1", "1.25", "1.6", "2"], group="Core"),
ConfigItem(label="WS 1 Monitor", key="monitor", scope="workspace_rule/1", type_="picker",
           default="eDP-1", options=["eDP-1", "DP-1"], group="Workspaces"),
ConfigItem(label="VFR", key="vfr", scope="debug", type_="bool", default=True, group="Global"),
```
