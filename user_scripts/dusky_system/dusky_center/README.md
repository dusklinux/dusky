# Dusky Center

Rust control center for Linux, Hyprland and Wayland, built with Iced and wgpu.
The application uses a normal, undecorated xdg-toplevel window. Esc at the root
page or closing the window exits the process; there is no background UI daemon.

## Build and install

```bash
./scripts/install.sh
~/.local/bin/dusky-center
# Deliberately rebuild for this CPU:
./scripts/install.sh --force-rebuild
```

The build script verifies tmpfs and defaults to `/tmp/dusky-center-target-$UID`.
The installer delegates to `153_dusky_center_setup.py`, also included after the
tray step in the main, ISO and personal setup profiles. It never launches the
app or enables a service. It installs into `~/.local/share/dusky/dusky_center`
and creates the `~/.local/bin/dusky-center` launcher.

A matching generic ISO package is reused without compiling. With changed source,
or `./scripts/install.sh --force-rebuild`, it builds a release for the current
CPU (`target-cpu=native`, plus native C/C++ flags) on executable tmpfs. CPU,
source and binary fingerprints avoid repeated compilation and reject native
builds carried over from another CPU. If compilation fails, a previously
verified native binary or the generic package remains available. Cached Cargo
dependencies are preferred; only missing dependencies are fetched, and the
build itself is frozen/offline. The installer bounds fetch/build time and
limits jobs using available memory and the process CPU affinity. Native binaries
use the existing optimized release profile; CPU tuning does not imply a measured
application speedup.

Editable defaults are seeded at `$XDG_CONFIG_HOME/dusky/dusky_config.toml`
(normally `~/.config/dusky/dusky_config.toml`) and beside the local binary.
Existing TOML files are preserved. Defaults are published only after a complete
copy and only if the destination remains absent. Configuration edits do not
trigger a binary rebuild. Change the canonical XDG file after setup.

The ISO recipe lives at
`user_scripts/arch_iso_scripts/offline_iso/iso_maker/python/dusky_packages_compile/dusky-center`.
The factory discovers it automatically and builds one generic x86-64 pacman
package, with `/usr/bin/dusky-center`, packaged TOML defaults and a source
fingerprint. It explicitly overrides native Rust/C/C++ flags; local updates
never replace that generic system binary.

For development checks, use `./scripts/build-on-tmpfs.sh --check`, `--test`,
`--clippy` or `--release`. These use a reusable tmpfs target directory;
installation selects its own verified package/native binary. Installer tests:
`python3 -X dev -m unittest discover -s tests`.

The installer suite has 27 passing checks, including eight simultaneous CLI
runs producing one compilation, compiler failures and invalid ELF outputs,
SIGTERM cancellation while building or waiting for the lock, resistant child
processes, offline fetch failures, write failures, interrupted/default-copy
races, CPU mismatch, affinity limits, and paths containing spaces/apostrophes.
Twenty consecutive timeouts produce no unclosed-pipe resource warnings;
3 MB on each output pipe drains without deadlock. Compiler faults use isolated
homes and deterministic compiler fixtures, rather than rebuilding all Rust
dependencies for every failure. An actual generic package was separately
installed and reused offline without Cargo, preserving edited TOML. The real
native build and generic package were compiled in the preceding verification.
No full ISO installation or power-loss recovery test was performed.

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
