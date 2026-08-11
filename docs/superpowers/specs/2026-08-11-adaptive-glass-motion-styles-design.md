# Adaptive Glass Motion Styles Design

Date: 2026-08-11

## Goal

Add a premium motion-style preference for Adaptive Glass, controlled from Dusky Control Center and consumed by the AGS shell at runtime.

The first polish pass focuses on the workspace rail and workspace preview because they are the shell's signature interaction. The design should feel advanced and animated, while staying clean, legible, and useful.

## User-Facing Behavior

Dusky Control Center gets an Adaptive Glass motion selector with two choices:

- Soft Magnetic
- Precise Futuristic

Soft Magnetic is the default. It should feel like glass responding to touch: elastic hover expansion, gentle snap-back, soft glow, and smooth easing.

Precise Futuristic should feel sharper and more technical: tighter timing, cleaner focus rings, less bounce, and quicker hover response.

The setting should persist across logouts and reboots.

## Settings Model

Store the selected motion style at:

`~/.config/dusky/settings/ags/adaptive-glass-motion`

Allowed values:

- `soft-magnetic`
- `precise-futuristic`

If the file is missing, empty, or invalid, AGS falls back to `soft-magnetic`.

## Dusky Control Center

Rename or broaden the existing Status Bar area so it is not Waybar-only. A suitable label is:

`Status Bar`

Add a selection row for Adaptive Glass motion:

- Title: `Adaptive Glass Motion`
- Description: `Workspace and panel animation style`
- Options: `Soft Magnetic`, `Precise Futuristic`
- Setting key: `ags/adaptive-glass-motion`
- Options map:
  - `soft-magnetic` -> `Soft Magnetic`
  - `precise-futuristic` -> `Precise Futuristic`

This should use the existing Control Center `selection` row pattern and settings-file persistence rather than a custom settings UI.

## AGS Runtime

Add a small motion-state module next to the existing theme state code. It should:

- read `~/.config/dusky/settings/ags/adaptive-glass-motion`
- expose a typed motion mode signal
- validate saved values
- fall back to `soft-magnetic`
- monitor the settings file or parent settings directory so Control Center changes can apply without restarting AGS

The shell should add a root class based on the selected mode:

- `motion-soft-magnetic`
- `motion-precise-futuristic`

The class should be applied high enough in the AGS window tree that bar widgets and popup widgets can share the same motion system.

## Visual Design

Soft Magnetic should use:

- slightly longer hover transitions
- elastic workspace expansion
- subtle glow on the magnetic shell
- softer preview entrance
- calm focus emphasis

Precise Futuristic should use:

- shorter transition durations
- reduced overshoot
- sharper borders and focus rings
- snappier preview entrance
- tighter hover feedback

Both modes must remain clean:

- no cluttered HUD styling
- no loud global gradients
- no distracting looping animation
- no layout shift from hover labels, icons, or previews
- no text overlap in compact popups

## Scope For First Implementation

Apply the first pass to:

- workspace rail hover and active states
- workspace magnetic snap pulse
- workspace preview popup entrance and selected-window emphasis
- shared popup transition timing where it is low risk

Do not redesign every module in the first pass. Network, audio, display, media, and power panels can adopt the motion classes later after the workspace interaction feels right.

## Error Handling

If the motion settings file cannot be read, AGS should log a concise warning and use `soft-magnetic`.

If the settings file changes to an invalid value while AGS is running, AGS should ignore it, keep or return to `soft-magnetic`, and avoid crashing.

If Control Center writes the setting while AGS is not running, AGS should pick it up on next launch.

## Testing

Add focused tests that verify:

- the Control Center config exposes the Adaptive Glass motion selection
- the persisted key is `ags/adaptive-glass-motion`
- AGS accepts only `soft-magnetic` and `precise-futuristic`
- invalid or missing motion state falls back to `soft-magnetic`
- the AGS root window receives a motion class
- CSS contains scoped rules for both motion classes
- workspace magnetic timing remains non-sizing and does not introduce layout expansion regressions

Existing AGS contract tests and the bar switcher tests should continue to pass.

## Non-Goals

- Do not add a separate AGS settings popup in this pass.
- Do not remove Waybar fallback.
- Do not redesign the full Control Center page beyond the status-bar section naming needed for clarity.
- Do not make motion style depend on wallpaper, theme mode, or monitor count.
- Do not implement reduced motion yet; leave it as a future setting.

## Future Follow-Up

After this pass, the same settings pattern can support:

- active bar selection
- glass intensity
- panel density
- workspace preview style
- reduced motion
- per-module enable/disable toggles
