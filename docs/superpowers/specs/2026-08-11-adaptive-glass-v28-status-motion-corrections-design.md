# Adaptive Glass v2.8 Status And Motion Corrections Design

## Goal

Fix the v2.7 motion regressions and add premium status-aware icon behavior to the top bar without changing Dusky integration points or the Waybar switch path.

## Problems To Correct

- The casino clock reel does not visibly roll because the current implementation updates the same label in place and only applies a one-time CSS animation.
- Popup windows visually expand sideways because the reveal animation scales the popup frame.
- Active workspace uses a static dot; the requested active indicator is a Pac-Man-style icon that changes color as the active workspace changes.
- Wi-Fi icon color does not communicate signal strength.
- Power trigger is visually under-arranged and should read as a deliberate control, not a loose glyph plus dot.
- Battery percentage is static; it should communicate charge level and warn with a short pulse when entering low/critical states.

## Design

### Clock Reel

The clock will render five stable slots for `HH:MM`. Digit slots will use a stacked old/current label pair and a per-tick animation class, so changed digits roll visibly every time. Colon stays static. Slots keep fixed dimensions to avoid clock pill jitter.

### Popup Stability

Popup frame animation will remove `scale(...)` completely. Popups can fade and slide a few pixels vertically, but width, sides, and borders must stay geometrically stable on hover/open.

### Workspace Active Indicator

The active workspace will replace the dot with a Pac-Man glyph label. The button gets `workspace-id-N` classes, and CSS assigns each active workspace a distinct Pac-Man color. Inactive workspace numbers remain visible as before.

### Network Signal Color

The network trigger and network panel icons get class names derived from signal strength:

- `wifi-signal-offline`: no connection or zero strength.
- `wifi-signal-weak`: 1-39%.
- `wifi-signal-ok`: 40-69%.
- `wifi-signal-strong`: 70-100%.

The icon shape remains native Astal/GTK symbolic Wi-Fi, while color and subtle glow show signal strength.

### Power Trigger

The power trigger becomes a compact, centered icon shell with a separate caffeine status dot. This keeps the glyph aligned and makes the control read as intentional in the right cluster.

### Battery Warning

Battery card gets level classes:

- `battery-level-full`: 80-100%.
- `battery-level-good`: 45-79%.
- `battery-level-low`: 20-44%.
- `battery-level-warning`: 10-19%.
- `battery-level-critical`: 0-9%.
- `charging`: while charging.

Warning and critical states use a CSS pulse animation with three iterations, then keep the warning/critical color. The icon and percentage both reflect the level.

## Testing

Add v2.8 tests that fail on the current code and pass only when:

- Clock uses old/current reel faces and alternating tick classes.
- Popup reveal keyframes contain no `scale(` and popup frames do not transition transform.
- Workspace active indicator uses a Pac-Man label and per-workspace classes.
- Network trigger/panel use signal-strength class helpers.
- Power trigger has shell/content classes for proper arrangement.
- Battery card computes class names from percentage/charging and CSS defines warning pulses.

Run the existing AGS suite, shell checks, AGS bundle smoke, then reinstall and live-smoke the running `$HOME` config.
