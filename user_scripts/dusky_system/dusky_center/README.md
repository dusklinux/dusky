# Dusky Center

Rust control center for Linux, Hyprland and Wayland, built with Iced and wgpu.
The application uses a normal, undecorated xdg-toplevel window. Esc at the root
page or closing the window exits the process; there is no background UI daemon.

## Build and install

```bash
./scripts/build-on-tmpfs.sh --check
./scripts/build-on-tmpfs.sh --test
./scripts/build-on-tmpfs.sh --clippy
./scripts/build-on-tmpfs.sh --release
./scripts/install.sh
~/.local/bin/dusky-center
```

The build script verifies tmpfs and defaults to `/tmp/dusky-center-target-$UID`.
The installer copies the release binary into `~/.local/share/dusky/dusky_center`
and creates the `~/.local/bin/dusky-center` launcher.

## Configuration and colors

TOML is resolved in this order:

1. `dusky_config.toml` in the current working directory.
2. `$XDG_CONFIG_HOME/dusky/dusky_config.toml` (normally `~/.config/dusky/`).
3. `$HOME/user_scripts/dusky_system/dusky_center/dusky_config.toml`.
4. `dusky_config.toml` beside the running executable.

Ctrl+R reloads configuration and colors. Ctrl+F opens search. All 18 pages are
parsed from TOML; supported widgets are rendered by `src/ui.rs`. See
[REVIEW.md](REVIEW.md) for migration gaps; parsing a property does not mean its
behavior has been implemented.

Home uses `type = "controls"` sections named Audio and Display. Audio contains
output volume and microphone input level (0–100%, targeting WirePlumber's
default sink/source). Display contains brightness and Night Light. Night Light
appears while `hyprsunset.service` is active. Configure slider ranges, steps,
icons and command actions in TOML. Pointer release commits immediately; a value
that stops changing for 100 ms also commits, supporting arrow keys and Ctrl+wheel.

Matugen colors come from `$XDG_CONFIG_HOME/matugen/generated/dusky_center.json`,
falling back to `dusky_tray.json`, then the built-in palette. Rust widget styles
use these colors; `dusky_style.css` is retained GTK legacy material and is not
loaded by the Rust UI. The current Matugen template explicitly uses dark roles.

## Controls and styling

Toggle state sources, service runtime/startup queries and toggle writes run on
executor workers. Services use enable/disable with `--now`; failed operations
restore the previous state and show the error. Setting-file toggles support
app-owned persistence. State refreshes on page entry, after operations and on
the existing three-second cycle.

Paired `properties.buttons` render as joined actions in their TOML order.
Explicit `style = "suggested"` or `"destructive"` determines their appearance;
labels do not implicitly select styles. Matugen supplies error-container colors
for destructive actions and close-button hover. Subtle surfaces/text use opaque
sRGB mixes to avoid the GPU alpha blending brightening them.

Entries initialize from `value_command`, keep unsaved edits while polling, and
submit through `on_action` with argument-safe value substitution. Structured
argv actions keep each argument intact.
