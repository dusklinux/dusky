# Adaptive Glass AGS v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first runnable AGS v3/GTK4 version of Dusky Adaptive Glass that establishes the final shell architecture and visually follows the approved concept poster.

**Architecture:** AGS owns a thin exclusive top bar while Gtk.Popover surfaces hold rich controls below the bar so expansion never competes for horizontal bar allocation. Astal supplies native Hyprland, MPRIS, NetworkManager, Bluetooth, WirePlumber, Battery and Brightness state; Dusky-specific actions remain bridges to existing scripts. Waybar is neither killed nor modified.

**Tech Stack:** AGS v3, Gnim JSX, GTK4, Astal GTK4, AstalHyprland, AstalMpris, AstalNetwork, AstalBluetooth, AstalWp, AstalBattery, AstalBrightness, Matugen CSS, existing Dusky scripts.

## Global Constraints

- Keep the existing Dusky Waybar installation untouched and available as fallback.
- Install the prototype at `$HOME/.config/ags` only after the user explicitly runs `install.sh`.
- Use `~/.config/matugen/generated/waybar-colors.css` when available, with local fallback colors.
- Use five clickable numbered Hyprland workspace buttons.
- Clock remains centered and independent from media; its popover contains a real `Gtk.Calendar`.
- Media transport is fixed Previous / Play-Pause / Next and has no scroll-to-change-track behavior.
- Network, Audio, Display and Power controls use floating popovers, not inline expanding bar groups.
- Preserve existing Mako rather than starting AstalNotifd as a competing notification daemon.
- All Dusky command bridges are asynchronous and fail without crashing the bar.

---

### Task 1: Application shell and theme loader

**Files:** `app.tsx`, `styles/fallback.css`, `style.css`, `components/Bar.tsx`

- [ ] Start a GTK4 AGS application using `app.start`, one bar per monitor, and Adwaita as GTK baseline.
- [ ] Load fallback color tokens, then Matugen generated tokens when present, then shell CSS.
- [ ] Create an exclusive top anchored bar with left, center, and right zones.
- [ ] Run `pytest -q tests/test_contract.py` and verify shell-related contracts pass.

### Task 2: Primary bar modules

**Files:** `components/Launcher.tsx`, `components/Workspaces.tsx`, `components/Weather.tsx`, `components/Notification.tsx`, `components/LeftCluster.tsx`, `lib/dusky.ts`

- [ ] Wire launcher/weather/Mako to existing Dusky tooling through safe async helpers.
- [ ] Render workspaces 1-5 and dispatch native Hyprland `workspace` actions on click.
- [ ] Track active workspace reactively.
- [ ] Run contract tests.

### Task 3: Center clock and floating calendar

**Files:** `components/HoverPopover.tsx`, `components/ClockCard.tsx`

- [ ] Implement a reusable menu-button/popover component with pointer-enter opening.
- [ ] Keep popover alive for pointer transition and let GTK autohide/click-away close it.
- [ ] Render live local time and a `Gtk.Calendar` in the clock surface.
- [ ] Run contract tests.

### Task 4: Media controller

**Files:** `components/MediaCard.tsx`

- [ ] Bind to AstalMpris players.
- [ ] Render source/cover artwork, title, artist and fixed previous/play-pause/next controls.
- [ ] Keep media separate from the center clock.
- [ ] Run contract tests.

### Task 5: Floating control surfaces

**Files:** `components/NetworkControl.tsx`, `components/AudioControl.tsx`, `components/DisplayControl.tsx`, `components/PowerControl.tsx`, `components/Battery.tsx`, `components/RightCluster.tsx`

- [ ] Network popover shows SSID, signal strength, live Dusky traffic text and Bluetooth summary.
- [ ] Audio popover provides native WirePlumber volume slider and percentage.
- [ ] Display popover provides native brightness slider and Dusky Theme / Wallpaper / Night actions.
- [ ] Power popover provides labeled Power / Idle / Lock / Logout actions.
- [ ] Battery is compact in the bar and reactive through AstalBattery.
- [ ] Run contract tests.

### Task 6: Concept styling and packaging

**Files:** `style.css`, `install.sh`, `README.md`

- [ ] Implement glass cards, Matugen gradients, restrained shadows, compact default geometry and rich popover surfaces matching the poster hierarchy.
- [ ] Add a non-destructive installer that backs up an unrelated existing AGS config, copies the prototype, refreshes AGS types, and prints the manual run command.
- [ ] Document live test procedure and known v1 scope.
- [ ] Run full tests, shell syntax validation and package the directory as `adaptive-glass-ags-v1.zip`.
