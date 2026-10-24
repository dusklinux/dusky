# Engine: `matugen_presets`

- **Class:** `MatugenPresetsEngine` — `engines/matugen_presets.py`
- **Engine types:** `matugen_presets, matugen_color, color_presets, theme_presets`
- **Default target:** `~/.config/dusky/settings/dusky_theme/state.conf` (expanded + resolved).

## Target format & architecture

The `matugen_presets` engine manages the desktop theme and solid color palettes in the Dusky ecosystem:
1. `~/.config/dusky/settings/dusky_theme/state.conf`: Tracks active settings and `LAST_APPLIED_HEX`.
2. `~/.config/dusky/settings/dusky_theme/theme_preset_fav`: Stores user favorite colors in `Label|#HEX` format.
3. `~/.config/dusky/settings/dusky_theme/exact_color_presets.json`: Stores curated color presets with saved schemes, modes, and contrast. These generate Material You tones; output can differ from the seed HEX.
4. `~/.config/dusky/settings/dusky_theme/vivid_favorites.json`: Stores Vivid favorites as a JSON list of `{ "id", "label", "hex" }` objects. The pack ID controls application; the label and hex are display metadata. This separate file preserves compatibility with the legacy seed-favorites script.
5. `~/user_scripts/theme_matugen/theme_ctl.sh`: Invoked to apply settings and trigger Matugen template generation.

## Scope / key mapping

- `scope="DEFAULT"`
- **Active Color**: `last_applied_hex` (or `active_color`) — `type_="color"`.
- **Palette Presets**: `color_<tab>_<name>__<hex_without_hash>` or items with `options=["trigger:#HEX"]`.
- **Exact Presets**: `exact_preset_<id>` with hardcoded scheme configurations (`scheme-fidelity`, `scheme-vibrant`, etc.).
- **Exact Strategy**: `exact_scheme_override` (`per-preset`, `scheme-fidelity`, `scheme-vibrant`, etc.).
- **Exact Preset Management**: `new_exact_hex`, `new_exact_scheme`, `action_add_exact_preset`, `action_del_exact_<id>`.
- **Vivid Presets**: `vivid_apply_<id>` (momentary bool triggers applying immutable pre-baked saturated theme packs), `action_resume_wallpaper` (resumes live wallpaper theming), `vivid_active_hex` (read-only), `vivid_active_id` (read-only).
- **Custom Input**: `custom_hex`, `custom_rgb`, `custom_hsl`, `action_regen`.
- **Theme Settings**: `mode`, `type`, `contrast`, `index`, `base16`, `action_apply_theme`.
- **Animation Settings**: `t_type`, `t_dur`, `t_fps`, `t_bez`, `t_ang`, `t_pos`, `action_apply_anim`.
- **Favorites**: `action_add_favorite` and `action_remove_favorite` use the active source: a Vivid pack is saved/removed by ID; other sources use the active seed HEX. `fav_apply_<index>` / `fav_remove_<index>` manage seed favorites; `fav_vivid_apply_<id>` / `fav_vivid_remove_<id>` manage Vivid favorites. False bool trigger writes are no-ops. Press F5 to refresh dynamic favorite rows after changes. Missing Vivid definitions report an error without HEX fallback.

## Engine Methods

- `apply_color(hex_code, scheme_override=None, contrast_override=None, mode_override=None)`: Synchronizes state and triggers `theme_ctl.sh`.
- `list_vivid_presets()`: Returns list of pre-baked vivid color presets from catalog.
- `apply_vivid(preset_id)`: Applies a pre-baked vivid pack instantly via atomic copies and toolkit hooks.
- `resume_wallpaper()`: Restores desktop back to dynamic wallpaper theming and reconciles mode.
- `get_palette_preview(hex_code, scheme, mode, contrast)`: Material You token preview via `matugen --dry-run --json hex --contrast=<value>`.
- `add_favorite(hex_code, label=None)` / `remove_favorite(hex_or_label)`: Atomic seed-favorites file writes; failed writes restore the in-memory list.
- `add_active_favorite()` / `remove_active_favorite()`: Dispatch using the active source.
- `add_vivid_favorite(preset_id)` / `remove_vivid_favorite(preset_id)`: Persist pack identities, deduplicated by ID rather than accent color.
- `add_exact_preset(...)` / `delete_exact_preset(preset_id)`: Curated and user-custom hardcoded preset management.
- `_save_state_file()`: Preserves external unmanaged keys (e.g. `LIGHT_WAL`) and file comments in place.

Animation settings are saved for the next wallpaper transition without regenerating colors. Explicitly applying Theme settings while Vivid is active generates from its accent and clears Vivid provenance.

## Standalone CLI Interface

The schema script `tui_color_presets.py` can be launched directly:
```bash
python3 tui_color_presets.py                           # Launch interactive TUI
python3 tui_color_presets.py --color '#FF0000'         # Apply hex color directly
python3 tui_color_presets.py --rgb 255 0 128           # Apply RGB color
python3 tui_color_presets.py --hsl '340 100% 50%'      # Apply HSL color
python3 tui_color_presets.py --exact vivid_red         # Apply exact hardcoded preset
python3 tui_color_presets.py --vivid toxic_lime        # Apply pre-baked vivid preset
python3 tui_color_presets.py --list-vivid              # List all pre-baked vivid presets
python3 tui_color_presets.py --resume-wallpaper        # Return to live wallpaper theme
python3 tui_color_presets.py --preview '#FF0000'       # In-memory Material You preview
python3 tui_color_presets.py --list-exact              # List all hardcoded presets
python3 tui_color_presets.py --add-exact '#00FF55' scheme-vibrant "Cyber Green"
python3 tui_color_presets.py --del-exact <ID>          # Delete exact preset
python3 tui_color_presets.py --favorite-active        # Save active Vivid pack or seed
python3 tui_color_presets.py --add-vivid-fav toxic_lime # Save a complete Vivid pack
python3 tui_color_presets.py --del-vivid-fav toxic_lime # Remove a Vivid favorite
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
