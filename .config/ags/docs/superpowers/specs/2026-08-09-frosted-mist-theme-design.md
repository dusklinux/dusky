# Adaptive Glass Frosted Mist Theme Design

## Goal

Correct the Light/Dark theme control and replace the overly white light appearance with a layered Frosted Mist glass palette while preserving Adaptive Glass dark mode and all existing component behavior.

## Interaction

- Use one native `Gtk.Switch` for theme selection.
- Light maps to the switch's left/off position; Dark maps to the right/on position.
- The switch visibly moves when clicked and remains synchronized with persisted `themeMode` state.
- The existing `setAdaptiveTheme()` path remains the single state/backend entry point.
- The Theme subtitle continues to show `Light` or `Dark`.

## Frosted Mist appearance

- Avoid large near-white surfaces.
- Shell capsules use cool mist-blue/grey glass with moderate translucency.
- Popup frames use a slightly deeper frosted blue-grey foundation than inner cards.
- Inner cards use a lighter pearl/mist layer so depth remains visible.
- Text is dark charcoal-blue; secondary text is muted slate-blue.
- Matugen primary/secondary/tertiary colors remain the accent source for active workspaces, slider highlights, selected controls, and interactive emphasis.
- Keep subtle pale highlights only for glass edge reflection, not as the primary fill.

## Scope

- Change the Display theme control implementation and theme-specific CSS only.
- Preserve brightness, wallpaper, night controls, audio, network, workspace, calendar, media, popup state, and Dusky/Matugen backend behavior.
- Preserve dark-mode appearance.
