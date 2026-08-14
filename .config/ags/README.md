# Adaptive Glass AGS for Dusky

Base shell: Adaptive Glass AGS v2.10

Adaptive Glass is the AGS v3 / GTK4 shell being developed as a polished Dusky bar option while the existing Waybar setup remains untouched as a fallback.

## Dusky integration

- Source lives in `.config/ags` and is launched through `user_scripts/bar/bar_switch.sh`.
- Waybar remains the fallback. Use `SUPER + ALT + G` or `bar_switch.sh toggle` to switch between Waybar and Adaptive Glass.
- Startup uses `bar_switch.sh start`, so the last saved bar choice is restored on login.
- NovaBar is not part of the active integration. A legacy saved `novabar` state is normalized to `adaptive-glass`.
- Dusky Control Center exposes Adaptive Glass preferences under `Status Bar`.

## Preferences

Adaptive Glass defaults to the full opinionated experience.

- Motion: `Soft Magnetic` by default, or `Precise Futuristic`.
- Clock: optional `24-hour Clock` mode hides AM/PM and uses `HH:MM`.
- Feature toggles: Workspace Preview, Media Island, Weather, Notifications.
- Settings are stored under `~/.config/dusky/settings/ags`.
- Feature flags live under `~/.config/dusky/settings/ags/features`.

Disabled optional entrypoints are not mounted by the bar, so Weather, Notifications, and Media Island avoid their component polling when disabled. Workspace Preview is also blocked at the preview state layer so hover cannot trigger captures while it is off.

## Review checks

From the Dusky repository root:

```bash
bash tests/test_bar_switch_adaptive.sh
bash tests/test_adaptive_glass_source.sh
bash .config/ags/tests/test_capture_helper.sh
bash .config/ags/tests/test_install_smoke.sh
PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests
```



## v2.10 — Dynamic Rail and Clock Tab Refinement

- Returns the workspace rail to five visual slots while the last slot dynamically becomes the active workspace number above 5.
- Keeps workspace numbers neutral and moves the bright recycled 10-color palette onto Pac-Man only.
- Adds a live Dusky Control Center `24-hour Clock` toggle backed by `~/.config/dusky/settings/ags/clock-24h`.
- Refines the clock as a flatter top-attached tab matching the workspace pill surface, with one Matugen-tinted top line that glows on hour changes.
- Replaces the battery shell with a compact Waybar `05_circular_h`-style glass module and state coloring.
- Normalizes the power trigger to the same control-leader surface as audio and simplifies the launcher to a bare modern Arch symbol.

## v2.9 — Waybar 05 Glass Polish

- Expands the workspace rail to 10 visible slots and keeps the active workspace mapped into the pill for higher workspace numbers.
- Keeps inactive workspace numbers neutral while moving the bright per-workspace Pac-Man colors into the final cascade layer for all 10 active states.
- Refines the workspace active pill toward the Waybar `05_circular_h` liquid capsule style with stronger inset glow and a more visible recoil.
- Slows the clock reel to make digit changes easier to see and reshapes the clock as a screen-attached top tab with dual glow rails.
- Replaces the battery icon with a horizontal battery shell that carries the percentage inside, including a visible zero-percent glow state.
- Adds a signal-change pulse to Wi-Fi strength colors and centers the right-side power control inside its own orb shell.
- Gives the left launcher icon a matching luminous glass treatment.

## v2.8 — Status Motion Corrections

- Replaces the single-face clock animation with old/new reel faces so changed digits replay a vertical casino motion every minute.
- Removes scale from popup reveal motion and overrides motion-scoped popup transitions so calendar and control popups no longer stretch sideways while appearing.
- Swaps the active workspace dot for a color-indexed Pac-Man-style glyph and lengthens workspace recoil timing so the snap is more visible.
- Adds Wi-Fi signal color states, a centered power trigger shell, and battery level classes with three-pulse warning/critical feedback.

## v2.6.4 — Explicit Preview Bounds

- Replaces the v2.6.3 natural-size reset with explicit top-level GTK popup sizes so source screenshot dimensions cannot inflate the workspace preview.
- Uses three bounded states: 308 × 230 for one window, 356 × 320 for 2–4 windows, and 356 × 365 for 5+ windows.
- Empty workspaces still open no preview at all and cancel any pending resize from a previous workspace.
- Makes `Gtk.Picture` shrinkability explicit while retaining `Gtk.ContentFit.CONTAIN`; capture and selected-window behavior are unchanged.
- Preserves the 24 px icons, approximately 42 px multi-window rows, 4 × 2 pagination, active-workspace suppression, rail-to-preview ownership, and elastic/magnetic rail.

## v2.6.3 — Natural Preview Resize

- Fixes stale popup dimensions when moving between multi-window and single-window workspaces by resetting the visible GTK workspace window to its natural size after each non-empty workspace snapshot change.
- Empty workspaces no longer open any workspace preview popup at all; the rail hover interaction still responds normally.
- Single-window previews keep the compact v2.6.2 layout and now reliably shrink after a larger multi-window preview without restarting AGS.
- Multi-window pages still retain every client and page 8 at a time in a 4 × 2 grid, but icons are reduced to 24 px with approximately 42 px rows for a shorter navigator.
- The large native per-window tooltip remains removed; the selected-window identity strip remains the app/title reference.
- Preserves active-workspace preview suppression, rail-to-preview ownership, exact-window focus, capture behavior, and the elastic/magnetic workspace rail.

## v2.6.2 — Compact Preview States

- Empty workspaces now use a tiny `No preview available` state instead of an oversized blank preview surface.
- Single-window workspaces shrink to a dedicated compact preview footprint and omit the redundant WINDOWS grid.
- Multi-window pages still keep all clients and page 8 at a time, but each 4-column row now fills the available width evenly instead of leaving a dead strip on the right.
- Removed the large native per-window tooltip; the selected-window identity strip remains the readable app/title reference.
- Preserves active-workspace preview suppression, rail-to-preview ownership, exact-window focus, capture behavior, and the v2.5.7 elastic/magnetic rail.

## v2.6.1 — Compact Workspace Navigator

- Keeps every visible window but shows 8 per page in a 4 × 2 grid.
- Reduces the hero from 390 × 219 to 320 × 160 while preserving `Gtk.ContentFit.CONTAIN`.
- Narrows the navigator to a 340 px layout target and tightens header, identity, grid, and outer spacing.
- Preserves active-workspace preview suppression, pagination, exact-window focus, and rail-to-preview ownership.

## v2.6 — Workspace Navigator

- Suppresses the workspace preview for the currently active workspace; the popup is now only for inspecting non-active workspaces.
- Removes the eight-window truncation and retains every mapped visible client on the target workspace.
- Pages windows 12 at a time in a compact 6 × 2 icon grid with bounded previous/next controls and a page indicator.
- Adds a prominent selected-window identity strip beneath the hero with app name, title, and app icon.
- Forces the 390 × 219 hero picture to fill its viewport allocation with `Gtk.ContentFit.CONTAIN`, scaling small/narrow windows as far as possible without cropping or changing aspect ratio.
- Tightens header-to-hero spacing while preserving v2.5.7 rail geometry, magnetic shell, shared rail-to-preview ownership, and exact-window focus.

## v2.5.7 — Overlay Magnetic Shell

- Repairs the v2.5.6 stretched workspace rail by restoring the proven compact button geometry.
- Keeps the button as the only measured elastic layout surface and moves the delayed magnetic pulse to a non-sizing `Gtk.Overlay` child.
- The overlay shell cannot request horizontal expansion, does not receive pointer input, and remains centered over the workspace segment.
- Preserves the 190 ms normal elongation, then runs a separate 510 ms visual pulse: 1.00 → 1.20 overshoot → 0.96 recoil → 1.00 settle.
- Preserves rail-to-preview shared interaction ownership, active dot/ring state, exact workspace/window focus, and stable Weather spacing.


## v2.5.3 — Elastic Workspace Rail

- Replaces the moving overlay plate with separated elastic workspace segments.
- The focused workspace is represented by a centered glowing dot and persistent accent ring instead of its number.
- When the pointer is outside the rail, the focused workspace owns the elongated state.
- Hovering another workspace transfers the elongated state to that segment while the focused workspace contracts but keeps its dot and ring.
- Leaving the rail restores elongation to the focused workspace; clicking transfers focus and persistent active ownership.
- Rail width remains stable because exactly one segment owns the extra width at a time.
- Existing workspace preview and exact workspace/window focus behavior are preserved.

## v2.5.1 — Micro-polish

- Replaces per-workspace active fills with one reactive selection plate that slides across the five workspace positions and follows the hovered preview target.
- Softens the workspace navigator perimeter and hero depth while preserving the 390×219 live capture.
- Enlarges icon-grid tiles and window icons, keeps icon-only rows, and raises the WINDOWS heading for readability.
- Strengthens the center clock with larger time digits, clearer AM/PM contrast, and a restrained Matugen accent marker.
- Normalizes visible top-bar pills to a shared 28 px height, 10 px radius, 1 px border, and restrained shadow language while retaining semantic launcher/power/media colors.
- Shrinks bar media artwork to 24 px so the media island follows the same vertical rhythm without changing MPRIS behavior.

## v2.5 — Intensive visible-bar polish

- Calms the workspace strip: restrained active/preview states, softer borders, no neon bloom, and 180ms hover/activation motion.
- Compresses the workspace preview identity into one compact header line (`Workspace N` + window count).
- Reduces popup/preview glow while preserving the 390×219 live preview.
- Replaces the tall open-window list with a two-row, four-column icon grid (up to eight windows); hover previews the exact window and shows app/title in a tooltip, click focuses it.
- Redesigns the center time pill into a structured clock anchor with accent dot, separated time/meridiem typography, and restrained hover depth.
- Keeps exact-window capture/focus behavior unchanged and includes matching Frosted Mist styling.


## v2.0 — Network Dashboard

This revision replaces the prototype Network popup with the approved compact Network Dashboard:

- Active SSID is shown once in the connection header with current signal quality.
- Live download and upload rates update every second.
- Session data shows total, downloaded, and uploaded bytes plus a `Since` timestamp.
- Session totals are preserved across AGS restarts in the user runtime directory and continue accumulating when the default interface changes.
- Wi-Fi and Bluetooth actions use symbolic icons without decorative icon bubbles.
- `Manage` still opens the existing Dusky Network Manager.
- `Devices` still opens the Bluetooth manager.
- Nearby networks, saved networks, hotspot controls, graphs, IP/MAC addresses, and long-term usage history remain outside this popup.

## Existing polished components

The workspace navigator keeps real Option-A window snapshots, hover-to-preview, workspace switching, and exact-window focus. The centered clock keeps the minimal floating calendar with `Today` and `Clocks` actions. Their popup behavior is unchanged in v2.0.

Matugen is read from `~/.config/matugen/generated/waybar-colors.css`; `styles/fallback.css` provides fallback tokens.

## Install

```bash
chmod +x install.sh
./install.sh
```

The installer writes only to `~/.config/ags`. An unrelated existing AGS configuration is moved to a timestamped backup first. Waybar is not edited or deleted.

## Restart

```bash
ags quit --instance dusky-adaptive-glass 2>/dev/null || true
ags run "$HOME/.config/ags/app.tsx"
```

For this revision, open the Network popup and verify the active SSID, signal percentage, live RX/TX rates, session usage totals, `Since` time, and Wi-Fi/Bluetooth action rows.


## v2.0.3

- Final Network density refinement: ~295 px shell, tighter section gaps, shorter traffic/session/action cards.
- Replaced homogeneous three-column metric rows with two flexible metrics separated by a true 1 px divider.
- Preserved Network session measurement, persistence, popup behavior, icon sizes, and primary typography.


## v2.1.1 — Audio alignment polish

- Replaced device-name selector UX with explicit OUTPUT and INPUT sections.
- Device names are now passive context labels; advanced switching remains available through Mixer.
- Aligned mute icon, thick slider, and percentage on one control axis.
- Added subtle hover, press, focus, and muted feedback to output/input icon buttons.
- Removed the inline endpoint picker from the compact popup.

## v2.2.1 — Adaptive Glass Light / Dark theme

- Replaced the two Display theme buttons with one compact side-to-side toggle.
- Added a shared reactive Adaptive Glass theme state persisted at `~/.config/dusky/settings/ags/adaptive-glass-theme`.
- Bar and popup windows now expose reactive `theme-light` / `theme-dark` root classes.
- Added a true frosted-light appearance for the bar and popup surfaces while preserving Matugen accent colors.
- The existing Dusky `theme_ctl.sh` remains the system-theme backend.
- After Dusky updates Matugen, AGS re-applies its stylesheet so regenerated accents can be picked up without a shell restart.

## v2.2.2 — Frosted Mist

- Replaces the custom theme knob with a native GTK4 switch so the thumb physically tracks Light (left/off) and Dark (right/on).
- Keeps the existing persistent Adaptive Glass theme state and Dusky/Matugen backend.
- Reworks Light mode into layered cool mist, pearl-grey, and slate glass surfaces instead of near-white cards.
- Preserves Matugen accents and leaves Dark mode styling unchanged.
