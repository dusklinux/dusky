# Adaptive Glass AGS v1.3 Design

## Goal
Move the live AGS bar from functional prototype toward the approved Adaptive Glass concept while preserving Dusky integration and Waybar as an untouched fallback.

## Interaction model
All rich control surfaces share one popup state machine:
- Hovering a trigger opens a temporary preview.
- Moving from the trigger into its panel keeps the preview open.
- Leaving both trigger and panel closes the preview after about 220 ms.
- Clicking Clock, Network, Audio, Display, or Power pins that panel open.
- A pinned panel remains open when the pointer leaves and closes on the same trigger or an outside click.
- Opening a different Adaptive Glass panel closes the previously open panel.

Workspace buttons are the exception: click always switches workspace through Dusky's `multi_monitor_workspace.sh`; it does not pin the preview. Hover opens a workspace activity card and leaving closes it.

## Workspace preview
Hovering workspace 1–5 shows a glass activity card containing:
- Workspace number and number of visible windows.
- A lightweight schematic mini-layout representing up to four windows.
- An app list with desktop icon, application/window class, and current title.
- An explicit empty-state when no windows exist.

The preview uses AstalHyprland's live client objects and AstalApps icon lookup. It does not capture screenshots and does not poll `hyprctl` for window lists.

Dusky's monitor-banked workspace behavior is mirrored for previews: local workspace buttons 1–5 map to the focused monitor's 10-workspace bank, while clicking remains delegated to Dusky's existing dispatcher script.

## Visual polish
- Keep the AGS layer transparent; components float directly on the wallpaper.
- Increase separation between major left/center/right islands without making the bar taller than necessary.
- Give the active workspace a stronger luminous accent and clearer depth.
- Reduce the uniform "same pill everywhere" appearance: launcher, workspace deck, clock, media card, and utility leaders get distinct visual roles.
- Refine popup depth with a stronger header/body hierarchy, subtle inner surfaces, and more deliberate spacing.
- Preserve the compact media controller and Matugen palette integration.

## Reliability constraints
- Do not kill or modify Waybar from the AGS package.
- Do not add new runtime dependencies for workspace preview.
- Keep brightness on `brightnessctl`.
- Keep workspace switching on Dusky's Lua-compatible dispatcher.
- Make optional launcher commands degrade safely when a preferred terminal application is absent.
