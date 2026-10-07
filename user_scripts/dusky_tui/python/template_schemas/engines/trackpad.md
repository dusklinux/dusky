# Engine: `trackpad`

- **Class:** `TrackpadLuaEngine` — `engines/trackpad.py`
- **Engine types:** `trackpad`
- **Default target:** inherits the `lua` engine's constructor default `~/Documents/hyprland.lua` — always set `TARGET_FILE` (real schema: `~/.config/hypr/input/trackpad.lua`)

## What it does

Extends the `lua` AST engine with Hyprland 0.55 gesture blocks
(`hl.gesture({ fingers = N, direction = "...", action = ... })`) plus
virtualized physics defaults so the UI never shows `[Missing]`.

## Scope / key mapping

**Standard physics variables** — `scope="gestures"`:

| key | type | default |
|---|---|---|
| `workspace_swipe_distance` | int | 300 |
| `workspace_swipe_touch` | bool | false |
| `workspace_swipe_invert` | bool | true |
| `workspace_swipe_touch_invert` | bool | false |
| `workspace_swipe_min_speed_to_force` | int | 30 |
| `workspace_swipe_cancel_ratio` | float | 0.5 |
| `workspace_swipe_create_new` | bool | true |
| `workspace_swipe_direction_lock` | bool | true |
| `workspace_swipe_direction_lock_threshold` | int | 10 |
| `workspace_swipe_forever` | bool | false |
| `workspace_swipe_use_r` | bool | false |
| `close_max_timeout` | int | 1000 |

**Gesture bindings** — `scope="gesture/<fingers>/<direction>"`, `key="action"`,
a `cycle` whose options must be exactly the labels below (they map to canned
Lua action blocks; `"Disabled / Unbound"` deletes the block):

```
Native Workspace Swipe
Open Dusky Tray
Toggle Waybar
Toggle Blur & Opacity
Media: Play / Pause
Media: Volume Up (+10%)
Media: Volume Down (-10%)
Screen: Brightness Up (+10%)
Screen: Brightness Down (-10%)
Disabled / Unbound
```

`<fingers>` ∈ {3, 4}; `<direction>` ∈ {horizontal, left, right, up, down}.

## Types & value handling

- Physics vars go through the standard `lua` write path.
- Gesture writes do block-level structural replacement; disabled gestures are
  cleanly removed from the file, missing ones are appended.

## Example items

```python
ConfigItem(label="Swipe Distance", key="workspace_swipe_distance", scope="gestures",
           type_="int", default=300, min_val=0, max_val=2000, step=50, group="Swipe"),
ConfigItem(label="3-Finger Left Action", key="action", scope="gesture/3/left",
           type_="cycle", default="Toggle Waybar",
           options=["Native Workspace Swipe", "Open Dusky Tray", "Toggle Waybar",
                    "Toggle Blur & Opacity", "Media: Play / Pause", "Media: Volume Up (+10%)",
                    "Media: Volume Down (-10%)", "Screen: Brightness Up (+10%)",
                    "Screen: Brightness Down (-10%)", "Disabled / Unbound"],
           group="Gestures"),
```
