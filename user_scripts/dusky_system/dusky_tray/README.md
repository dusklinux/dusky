# Dusky Tray

An on-demand Rust control center for Linux / Hyprland / Wayland, using Iced
and layer-shell. Escape or a click outside the panel exits the process. Nothing
is kept running to prewarm the panel. No GTK GUI dependency is used.

The project and runnable test binary stay in this directory. The GTK scripts,
installed launcher and desktop keybinds are not replaced. The notification-time
service can run independently of the GTK panel.

## Run and rebuild

```sh
./scripts/reload_tray.sh

# Offline builds; compiler intermediates stay on RAM.
./scripts/build-on-tmpfs.sh --release
./scripts/build-on-tmpfs.sh --check
./scripts/build-on-tmpfs.sh --test

# The binary can also be invoked directly.
./bin/dusky-tray
```

The build script uses `/tmp/dusky-tray-target-$UID` for compiler artifacts,
intermediates, and temporary files. It checks that output lives on tmpfs and
outside this project, including `CARGO_TARGET_DIR` overrides. If `/tmp` is not
tmpfs on a target system, select a verified RAM mount explicitly, for example
`CARGO_TARGET_DIR=/dev/shm/dusky-tray-target-$UID ./scripts/build-on-tmpfs.sh`.
Only the final executable is copied to `bin/`. `--install` also copies it to
`$HOME/.local/bin`; installation remains an explicit step.

Project `.cargo/config.toml` directs ordinary Cargo commands run **from this
project** to `/tmp/dusky-tray-target`. The build script adds per-user cache
isolation and mount verification, and works from any working directory. Use it
for builds; a Cargo `--manifest-path` argument alone does not load the project's
configuration when invoked from elsewhere. Cargo flags/environment can override
its defaults. No source-directory `target/` tree is needed.

The reload script replaces only a running binary from this project's `bin/`
directory, including a replaced executable still running after a rebuild.
Direct invocation is protected by a per-user file lock. Other builds of this
revision share that lock. The optional service file is on-demand, has no login
autostart section, and does not restart the panel after it closes.
`service/dusky_tray.service` is an optional systemd launcher for the installed
`$HOME/.local/bin/dusky-tray`; direct launches and the reload script do not need it.

The Cargo registry sources and lockfile must already be present for an offline
build. Compilation on RAM does not make the compiler or the registry cache
independent of disk.

## Appearance and controls

- A truly centered clock, with weather and power aligned with its first line.
- A consistent 24 × 24 vector icon grid, embedded in Rust. No Nerd Font or GTK
  icon theme lookup is required for controls.
- The GTK panel's compact 320 px width and 12 px insets, with dark translucent
  surfaces. A dedicated Matugen palette supplies the lowest dark surface,
  foreground, primary/secondary/tertiary tones, lighter hover accent, and
  harmonized green/blue/red power-profile colors.
- The Wi-Fi quick button highlights only an active Wi-Fi connection, independently
  of the radio switch, and continues to open the configured network TUI.
- Five evenly spaced quick controls per row; additional configured controls
  wrap. Left, middle, and right mouse commands are supported.
- Compact 22 × 44 px Wi-Fi / Bluetooth switches and power profile controls retain
  the existing desktop integrations. Unknown radio states are unavailable rather than
  being reported as successful changes.
- Volume, brightness, and night-light sliders use 12 px rails and live values.
  Rails use primary, secondary, and tertiary tones without value-dependent
  saturation changes, boosted by 30% over the palette saturation. Icons increase
  linearly from 40% at 0 to 168% at 100; values retain their 40–140% range.
  Saturation is capped at the color gamut; the minimum has no colored rail fill.
  Hue and maximum-channel brightness stay fixed. White handles remain visible
  without hovering, with 3 px clearance at both ends. Volume zero shows a
  crossed-out speaker; brightness at its safe minimum (1) shows a dim sun.
  The night-light scale is
  0–100 without a percent sign: 0 disables the filter; 100 is warmest (1000 K).
  The three rails share the same width and a compact three-digit value column.
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
- One scroll area is capped to 85% of the output height. A 64 px bottom fade
  hides clipped content, with an 8 px feather into the bottom inset to avoid
  a rectangular opacity seam. It disappears at the end of the list and allows
  pointer/scroll events through.
  Escape has keyboard focus immediately after opening.
- Power saver's tooltip uses two lines: `Power saver` and `RMB: Powertop auto-tune`.
- The power button uses muted red (`#812824`) regardless of the wallpaper accent.
  Network metrics use the whole module width without an up/down icon.
- Initial data loads before the panel appears; the complete, stable layout
  fades in over 180 ms using compositor frame events. The frame subscription
  stops when the reveal finishes; subsequent polling remains every two seconds.

## Opacity, blur, and live themes

Edit `appearance.toml` next to this project's README:

```toml
opacity = 0.94 # scales panel rendering; 0.0 = invisible, 1.0 = opaque
blur = true   # enables this panel's Hyprland layer blur
```

Both settings reload within two seconds while the panel is open. Tooltips
always have opaque backgrounds and are drawn outside the panel-opacity wrapper.
For an installed binary, create `$XDG_CONFIG_HOME/dusky/tray/appearance.toml`
(or `~/.config/dusky/tray/appearance.toml`). That file takes precedence
over the project copy. Invalid or incomplete edits retain the last valid settings.

The app requests blur through the Wayland background-effect protocol and
applies a named Hyprland Lua layer rule at launch and when blur or
palette changes. Its alpha mask excludes the transparent click-away region;
fully transparent desktop pixels are excluded from blur. The panel draws no
outer shadow, keeping its blur mask inside the rounded border. The rule is
runtime configuration, so there is no permanent Hyprland config edit or
background helper. Hyprland's global
`decoration:blur:enabled` setting must be enabled, as it is on this machine.

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
weather updater. Network rates come directly from physical Linux interfaces,
including virtio devices, avoiding duplicate VPN/bridge accounting and a
Waybar network daemon dependency. The first rate sample is zero; subsequent
samples use elapsed time. Update counts use the existing package cache plus
`dusky_update_behind_commit`.

Clock, power, memory/CPU, Wi-Fi-manager, update, and audio-studio launchers close
the panel as they hand off to the configured application. Idle and visual
controls remain open for immediate feedback. Those external configured
applications can still use Python or GTK; the panel itself does not.

## Configuration and runtime

Configuration is read from `$XDG_CONFIG_HOME/dusky/quickpanal/config.toml`, or
`$HOME/.config/dusky/quickpanal/config.toml`. Existing GTK toggle commands and
layout keys are reused. An absent file receives embedded defaults. Empty
`toggles=[]` is respected. `show_media` remains a reserved, unimplemented key.

Runtime data normally lives in `$XDG_RUNTIME_DIR/dusky-tray`, with
per-user runtime fallbacks. Mako's blacklist and timestamp files are read from
the shared runtime root so GTK, Rofi, and Rust agree on dismissed notifications.
Queries and slider commands use nonblocking captured pipes, timeouts, and
Linux parent-death cleanup. Application launchers use separate systemd scopes.

Backlight devices and Bluetooth adapters are discovered at runtime. Local
brightness uses writable sysfs, then logind SetBrightness, then brightnessctl.
External
DDC brightness discovery is cached for 45 seconds; brightness is scaled using
that monitor's actual VCP maximum. The running Hyprsunset temperature is queried
on open rather than trusting a stale panel-only cache.

## Dependencies and verification

The installed environment was checked: Rust 1.99, Bash 5.3.20, systemd 262,
Hyprland-git 0.56, WirePlumber 0.5.18, and Hyprsunset 0.4. These checks describe
the development machine, not the final unshipped ISO package manifest.

Iced widget 0.14.2 and Iced core/renderer 0.14 are current stable releases.
`iced_exwlshell` 0.20.1 is the stable layer-shell release; its newer 0.21 release
candidate is not used. Iced's renderer requires wgpu 27, so independently
swapping in wgpu 30 would break the renderer interface. Vulkan-first / Wayland
EGL fallback and explicit GPU environment overrides are retained.
See [Iced's published API](https://docs.rs/iced_widget/0.14.2/iced_widget/)
and [layer-shell releases](https://crates.io/crates/iced_exwlshell).

Run the checks without retaining generated screenshots or logs:

```sh
./scripts/build-on-tmpfs.sh --test
./scripts/build-on-tmpfs.sh --clippy
cargo fmt --check
```

For a first-frame submission timestamp without verbose Wayland logging:

```sh
DUSKY_TRAY_TRACE=1 ./bin/dusky-tray
```

This measures GPU frame submission, not the time at which the display scans
out a frame. No result here promises identical latency on different hardware
or after a disk-cold boot.
