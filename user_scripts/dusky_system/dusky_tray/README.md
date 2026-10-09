# Dusky Tray

An on-demand Rust control center for Linux / Hyprland / Wayland, using Iced
and the standard Wayland window backend. Escape or a click outside the panel
exits the process. Nothing is kept running to prewarm the panel. No GTK GUI
dependency is used.

Sources stay in this directory. The executable lives at
`~/.local/share/dusky/dusky_tray/dusky-tray`, with a launcher symlink at
`~/.local/bin/dusky-tray`, matching Dusky Papers. GTK launchers and desktop
keybinds are not replaced.

## Run and rebuild

```sh
python3 ~/user_scripts/arch_setup_scripts/scripts/152_dusky_tray_setup.py
./scripts/reload_tray.sh
~/.local/bin/dusky-tray

./scripts/build-on-tmpfs.sh --force-rebuild
./scripts/build-on-tmpfs.sh --test
./scripts/build-on-tmpfs.sh --clippy
```

`--release` (the default) and `--install` both run setup. Native release builds
use `-C target-cpu=native` and native C/C++ flags. The manifest records source
and binary SHA256 values, version, target, and CPU identity. Unchanged builds
are reused; changed sources (even without a version bump), or a different CPU,
trigger compilation. All three setup profiles run this after Dusky Papers.

Native compiler output and temporary files live on executable `/tmp` or
`/dev/shm` tmpfs and are removed afterward. There is no disk build fallback.
Dependencies are checked offline first; missing crates may be fetched with a
bounded timeout unless `CARGO_NET_OFFLINE=true`. Compilation is frozen/offline.
The smoke-tested binary atomically replaces the old executable, followed by
its manifest and launcher. Failed builds retain a verified older native binary,
or use the generic `/usr/bin/dusky-tray` package. Rerun setup to retry a build.

Editable appearance and ignored-app defaults are copied beside the installed
binary only when absent, preserving user edits. The obsolete source-directory
executable is removed after successful installation.

The separate ISO recipe lives at
`user_scripts/arch_iso_scripts/offline_iso/iso_maker/python/dusky_packages_compile/dusky-tray/`.
The factory discovers it automatically. It builds a generic x86-64 pacman
package with `target-cpu=x86-64` and generic C/C++ flags, regardless of the
builder's CPU. Compiler output stays on `/tmp` tmpfs; the package contains the
executable and defaults. Installed systems prefer native compilation even when
the generic package matches their sources.

Check modes use `/tmp/dusky-tray-target-$UID`, verify tmpfs and reject output
inside the project. Ordinary Cargo commands run from this project default to
`/tmp/dusky-tray-target`; use the wrapper for mount verification. Neither creates
a source-directory `target/` tree. The Cargo registry cache is separate.

The reload script restarts this user's installed tray, including an old inode
after an atomic update. A per-user lock prevents concurrent panels.
`service/dusky_tray.service` optionally launches `~/.local/bin/dusky-tray` through
systemd; it has no login autostart section and does not restart a closed panel.
The notification timestamp daemon runs independently.

## Appearance and controls

- A truly centered clock, with weather and power aligned with its first line.
- A consistent 24 × 24 vector icon grid, embedded in Rust. No Nerd Font or GTK
  icon theme lookup is required for controls.
- The GTK panel's compact 320 px width and 12 px insets, with dark translucent
  surfaces. A dedicated Matugen palette supplies the lowest dark surface,
  foreground, primary/secondary/tertiary tones, lighter hover accent, and
  harmonized green/blue/red power-profile colors.
- The Animations quick button disables all animations and restores the last enabled
  Rofi preset, including its orientation. A plain play icon distinguishes it from
  Visuals. Its highlight follows Hyprland's live state.
  Visuals retains its blur/shadow toggle; right-click opens the shader picker.
  Hypridle right-click opens its configuration TUI with app ID `dusky_tui`.
  Each mouse action occupies its own tooltip line.
- Five evenly spaced quick controls per row; additional configured controls
  wrap. Left, middle, and right mouse commands are supported.
- Compact 22 × 44 px Wi-Fi / Bluetooth switches and power profile controls retain
  the existing desktop integrations. Click the Wi-Fi icon to open the network TUI,
  or the Bluetooth icon to open Blueman, independently of their radio switches.
  Unknown radio states are unavailable rather than
  being reported as successful changes.
- Volume, brightness, and night-light sliders use 12 px rails and live values.
  Rails use primary, secondary, and tertiary tones without value-dependent
  saturation changes, boosted by 30% over the palette saturation. Icons and values increase
  linearly from 40% saturation at 0 to 140% at 100.
  Saturation is capped at the color gamut; the minimum has no colored rail fill.
  Hue and maximum-channel brightness stay fixed. White handles remain visible
  without hovering, with 3 px clearance at both ends. Volume zero shows a
  crossed-out speaker; brightness at its safe minimum (1) shows a dim sun.
  The night-light scale is
  0–100 without a percent sign: 0 disables the filter; 100 is warmest (1000 K).
  The three rails share the same width and a compact three-digit value column.
  Slider icons and numbers have a soft glyph-edge halo whose strength increases
  linearly from no glow at 0 to a moderate glow at 100, reacting during dragging.
  The clock, date, weather, and power button have a subtle fixed glow. Gaussian
  masks give text and icons a continuous falloff without displaced glyph copies.
  The renderer caches shaped text masks and filtered SVGs; each halo needs one
  extra draw, with no GPU offscreen pass or extra animation timer. Cache hits
  verify raster glyph identities, positions, and alpha so changing equal-width
  digits cannot retain an earlier number’s halo.
  Click the volume icon for pavucontrol; right-click selects playback output in Rofi.
  At most one apply operation and one latest pending value exist per slider; older
  drag positions are discarded. Stale polling results cannot roll back a drag.
- Notification timestamps use the original GTK timestamp daemon, copied unchanged
  to `service/notification_time_service/`. It tracks first-observed times in
  `$XDG_RUNTIME_DIR/dusky_notif_times.json` every two seconds and preserves them
  across daemon restarts. Times appear beside individual notifications, including
  expanded groups. To install it independently of the old GTK directory:

  ```sh
  install -Dm644 service/notification_time_service/dusky_notif_time.service \
    "$HOME/.config/systemd/user/dusky_notif_time.service"
  systemctl --user daemon-reload
  systemctl --user enable --now dusky_notif_time.service
  ```

- Notification hover covers the whole rounded card, including its independent
  close button, which adds a red hover background. A filled triangle indicates
  expansion, with approximately 30% less space to the close action. Groups expand independently. DND, item dismissal, group dismissal,
  and clear-all use Mako. Historical items resolve desktop entries and use
  `gio launch`; active items invoke their Mako action.
- One scroll area is capped to 85% of the output height. Its viewport reaches
  the top and bottom panel edges; vertical content padding scrolls with the list,
  while the 12 px side insets remain fixed. No bottom overlay obscures content.
  Escape has keyboard focus immediately after opening.
- Power saver's tooltip uses two lines: `Power saver` and `RMB: Powertop auto-tune`.
- The power button uses muted red (`#812824`) regardless of the wallpaper accent.
  Network metrics use the whole module width without an up/down icon.
- The panel appears on the first rendered frame with its current clock and
  available controls; hardware and notification queries never gate visibility.
  The clock updates on each two-second tick independently of hardware refreshes.
  Loading and empty notification labels share a fixed 56 px slot, so an empty
  result only changes the label. Sliders still appear only when available.
  The `dusky_tray_window` window rule in Hyprland's `window_rules.lua`
  uses `slide bottom`, independently of the app's live blur rule. Panel
  height changes resize the compact window and keep its bottom edge anchored.
  Hyprland handles window animations; the application sends geometry changes
  only when the measured content size changes. No prewarm process or new service
  is used.

## Opacity, blur, and live themes

Edit `appearance.toml` next to this project's README:

```toml
opacity = 0.94 # scales panel rendering; 0.0 = invisible, 1.0 = opaque
blur = true   # enables this panel's Hyprland window blur
```

Both settings reload within two seconds while the panel is open. Tooltips
always have opaque backgrounds and are drawn outside the panel-opacity wrapper.
For an installed binary, create `$XDG_CONFIG_HOME/dusky/tray/appearance.toml`
(or `~/.config/dusky/tray/appearance.toml`). That file takes precedence
over the project copy. Invalid or incomplete edits retain the last valid settings.

The app uses the standard `iced_winit` Wayland backend, without a copied
layer-shell library. Hyprland's `dusky_tray_window` rule makes the window
floating, compact, transparent, borderless, and animated with `slide bottom`.
The panel remains inset 20 logical pixels from the bottom right and follows its
content height, capped to the monitor's available area. Dusky's shared
`click_away_to_dismiss/dusky.c` helper uses Hyprland's focus-grab protocol:
pointer movement outside keeps it open, while an outside click dismisses it.
The helper is compiled into the executable, so no separate `.so` is needed at
runtime. Its native entry point borrows the window's Wayland display and surface;
the event thread only enqueues dismissal, and joins before the window is released.
This is a regular window (listed by `hyprctl clients`), not a fullscreen state
or a layer surface. Normal workspace, focus, and fullscreen policies apply.

The app applies the named `dusky_tray_blur` window rule at launch and when
appearance changes. It sets `no_blur` from the appearance setting; there is
no background-effect protocol request. Hyprland's global
`decoration:blur:enabled` setting must also be enabled. The panel draws no
outer shadow and leaves its surrounding desktop pixels transparent.

Matugen's template is `~/.config/matugen/templates/dusky_tray.json`,
registered in `~/.config/matugen/config.toml`. It generates
`$XDG_CONFIG_HOME/matugen/generated/dusky_tray.json` in the normal theme
workflow (the configured output is `~/.config/matugen/generated/` here).
The template explicitly uses dark roles even when the desktop selects light mode.
Only semantic profile hues have seed colors; Matugen blends their hues towards
the current primary. The panel reads a complete palette in one step on open and
every two seconds; a missing/partially written file cannot replace a valid palette.
It no longer mixes a GTK surface file with a separately generated TUI accent.
Template generation and filters were exercised with installed Matugen 4.2.0.

Weather reads the existing Dusky weather cache and does not start a Python
weather updater. Clicking it opens the existing `wthr` terminal forecast using
`foot --app-id=dusky_tui --hold`; the report remains visible when the query ends.
Network rates come directly from physical Linux interfaces,
including virtio devices, avoiding duplicate VPN/bridge accounting and a
Waybar network daemon dependency. The first rate sample is zero; subsequent
samples use elapsed time. Update counts use the existing package cache plus
`dusky_update_behind_commit`.

Clock, weather, power, memory/CPU, radio-manager, volume, update, and audio-studio launchers close
the panel as they hand off to the configured application. Idle and visual
left-click controls remain open for immediate feedback; their TUI/shader launchers close the panel. Those external configured
applications can still use Python or GTK; the panel itself does not.

## Configuration and runtime

Configuration is read from `$XDG_CONFIG_HOME/dusky/tray/config.toml`, or
`$HOME/.config/dusky/tray/config.toml`. Existing GTK toggle commands and
layout keys are reused. An absent file receives embedded defaults. Empty
`toggles=[]` is respected. `show_media` remains a reserved, unimplemented key.

Runtime data normally lives in `$XDG_RUNTIME_DIR/dusky-tray`, with
per-user runtime fallbacks. Mako's blacklist and timestamp files are read from
the shared runtime root so GTK, Rofi, and Rust agree on dismissed notifications.
Queries and slider commands use nonblocking captured pipes, timeouts, and
Linux parent-death cleanup. Application launchers use separate systemd scopes.

Backlight devices and Bluetooth adapters are discovered at runtime. Local
brightness uses writable sysfs, then logind SetBrightness, then brightnessctl.
The logind fallback uses its automatic session object, so it works without
`XDG_SESSION_ID` and avoids a separate session lookup. Panel clipping passes
the visible viewport to children, avoiding drawing below the panel height; the
tray retains its existing backlight ranking and DDC support. External
DDC brightness discovery is cached for 45 seconds; brightness is scaled using
that monitor's actual VCP maximum. The running Hyprsunset temperature is queried
on open rather than trusting a stale panel-only cache.

## Dependencies and verification

The installed environment was checked: Rust 1.99, Bash 5.3.20, systemd 262,
Hyprland-git 0.56, WirePlumber 0.5.18, and Hyprsunset 0.4. These checks describe
the development machine, not the final unshipped ISO package manifest.

Iced widget 0.14.2 and Iced core/renderer 0.14 are current stable releases.
The regular-window runner is `iced_winit` 0.14.1, with only its Wayland
feature enabled. Iced's renderer requires wgpu 27; Vulkan-first / Wayland
EGL fallback and explicit GPU environment overrides are retained.
See [Iced's published API](https://docs.rs/iced_widget/0.14.2/iced_widget/)
and [the window runner](https://docs.rs/iced_winit/0.14.1/iced_winit/).

Run the checks without retaining generated screenshots or logs:

```sh
./scripts/build-on-tmpfs.sh --test
./scripts/build-on-tmpfs.sh --clippy
cargo fmt --check
```

GPU-dependent tests are ignored by default. On a machine with working adapters,
also exercise rendering, fractional scaling, and wheel scrolling on both backends:

```sh
WGPU_BACKEND=vulkan cargo test --locked --offline -- --include-ignored
WGPU_BACKEND=gl cargo test --locked --offline -- --ignored
```

For a first-frame submission timestamp without verbose Wayland logging:

```sh
DUSKY_TRAY_TRACE=1 ~/.local/bin/dusky-tray
```

The first submitted frame now contains the visible panel. Query completion
phases identify slow background work separately from `first-present`.
This measures GPU frame submission, not the time at which the display scans
out a frame. No result here promises identical latency on different hardware
or after a disk-cold boot.
