# Engine: `matugen_presets`

- **Class:** `MatugenPresetsEngine` — `engines/matugen_presets.py`
- **Engine types:** `matugen_presets, matugen_color, color_presets, theme_presets`
- **Default target:** `~/.config/dusky/settings/dusky_theme/state.conf` (expanded + resolved).

## Target format & architecture

The `matugen_presets` engine manages the desktop theme and solid color palettes in the Dusky ecosystem:
1. `~/.config/dusky/settings/dusky_theme/state.conf`: Tracks active settings and `LAST_APPLIED_HEX`.
2. `~/.config/dusky/settings/dusky_theme/theme_preset_fav`: Stores user favorite colors in `Label|#HEX` format.
3. `~/.config/dusky/settings/dusky_theme/exact_color_presets.json`: Stores curated color presets with hardcoded optimal schemes (`scheme-fidelity`, `scheme-vibrant`, etc.) to guarantee vivid colors without desaturation.
4. `~/user_scripts/theme_matugen/theme_ctl.sh`: Invoked to apply settings and trigger Matugen template generation.

## Scope / key mapping

- `scope="DEFAULT"`
- **Active Color**: `last_applied_hex` (or `active_color`) — `type_="color"`.
- **Palette Presets**: `color_<tab>_<name>__<hex_without_hash>` or items with `options=["trigger:#HEX"]`.
- **Exact Presets**: `exact_preset_<id>` with hardcoded scheme configurations (`scheme-fidelity`, `scheme-vibrant`, etc.).
- **Exact Strategy**: `exact_scheme_override` (`per-preset`, `scheme-fidelity`, `scheme-vibrant`, etc.).
- **Exact Preset Management**: `new_exact_hex`, `new_exact_scheme`, `action_add_exact_preset`, `action_del_exact_<id>`.
- **Custom Input**: `custom_hex`, `custom_rgb`, `custom_hsl`, `action_regen`.
- **Theme Settings**: `mode`, `type`, `contrast`, `index`, `base16`, `action_apply_theme`.
- **Animation Settings**: `t_type`, `t_dur`, `t_fps`, `t_bez`, `t_ang`, `t_pos`, `action_apply_anim`.
- **Favorites**: `action_add_favorite`, `action_remove_favorite`, `fav_apply_<index>`.

## Engine Methods

- `apply_color(hex_code, scheme_override=None, contrast_override=None, mode_override=None)`: Synchronizes state and triggers `theme_ctl.sh`.
- `get_palette_preview(hex_code, scheme, mode, contrast)`: Instant in-memory Material You token preview via `matugen --dry-run --json hex` (~8ms).
- `add_favorite(hex_code, label=None)` / `remove_favorite(hex_or_label)`: Thread-safe, atomic favorites list mutations.
- `add_exact_preset(...)` / `delete_exact_preset(preset_id)`: Curated and user-custom hardcoded preset management.
- `_save_state_file()`: Preserves external unmanaged keys (e.g. `LIGHT_WAL`) and file comments in place.

## Standalone CLI Interface

The schema script `tui_color_presets.py` can be launched directly:
```bash
python3 tui_color_presets.py                           # Launch interactive TUI
python3 tui_color_presets.py --color '#FF0000'         # Apply hex color directly
python3 tui_color_presets.py --rgb 255 0 128           # Apply RGB color
python3 tui_color_presets.py --hsl '340 100% 50%'      # Apply HSL color
python3 tui_color_presets.py --exact vivid_red         # Apply exact hardcoded preset
python3 tui_color_presets.py --preview '#FF0000'       # In-memory Material You preview
python3 tui_color_presets.py --list-exact              # List all hardcoded presets
python3 tui_color_presets.py --add-exact '#00FF55' scheme-vibrant "Cyber Green"
python3 tui_color_presets.py --del-exact <ID>          # Delete exact preset
python3 tui_color_presets.py --list-fav                # List saved favorites
python3 tui_color_presets.py --add-fav '#FF0055'       # Add favorite color
python3 tui_color_presets.py --del-fav '#FF0055'       # Delete favorite color
python3 tui_color_presets.py --apply-settings          # Apply cached theme/anim settings
```

## Example items

```python
# Active Color indicator & direct hex editor
ConfigItem(
    label="Active Color",
    key="last_applied_hex",
    scope="DEFAULT",
    type_="color",
    default="#0000FF",
    group="Palette Controls",
)

# Preset color trigger
ConfigItem(
    label="Hyper Red",
    key="color_vib_hyper_red__FF0000",
    scope="DEFAULT",
    type_="bool",
    default=False,
    options=["trigger:#FF0000"],
    group="Vibrant Presets",
)

# Exact hardcoded scheme preset
ConfigItem(
    label="Pure Vivid Red [fidelity]",
    key="exact_preset_vivid_red",
    scope="DEFAULT",
    type_="bool",
    default=False,
    options=["trigger:#FF0000"],
    group="Exact Presets",
)

# Custom HSL Input
ConfigItem(
    label="Input HSL Values",
    key="custom_hsl",
    scope="DEFAULT",
    type_="string",
    default="240 100% 50%",
    group="Custom Input",
)
```
