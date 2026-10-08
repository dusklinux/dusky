# Dusky Center

A blazing-fast, pure Rust system control center for Linux / Hyprland / Wayland.
Built with **Iced** and **layer-shell**, driven declaratively by `dusky_config.toml`.

- **On-Demand & Zero Background RAM**: Esc or clicking outside the window immediately exits the process, freeing all GPU and CPU memory (no background daemon needed).
- **100% Configurable**: Reads `dusky_config.toml` verbatim with Rust `serde`, parsing all 18 pages and 250+ controls in under 2ms.
- **RAM-Only Compiles**: Intermediates and compiler output stay exclusively on `/tmp` tmpfs (`target-dir = "/tmp/dusky-center-target-$UID"`), protecting SSDs from write amplification.

---

## Quick Start & Commands

```bash
# Rebuild & test exclusively on RAM (tmpfs)
./scripts/build-on-tmpfs.sh --check
./scripts/build-on-tmpfs.sh --test
./scripts/build-on-tmpfs.sh --release

# Install binary to ~/.local/share/dusky/dusky_center and link to ~/.local/bin/dusky-center
./scripts/install.sh

# Run
~/.local/bin/dusky-center
```

---

## Features & Controls

- **18 Category Pages**: Home, System, Memory, Disk & Files, Network, Hardware, Display, Audio, Visuals, Components, Services, Configs, Keybinds, Tools & AI, Setup, Troubleshoot, Keylogger, and About.
- **Instant Search**: Top search bar filters across all 18 pages in real time.
- **Card Grids & Action Rows**: Interactive Buttons, Toggle switches, Sliders, and Service managers with live status indicators.
- **Hot-Reload**: Press **Ctrl+R** anytime to reload `dusky_config.toml` on the fly without closing the panel.
- **Native Wayland Layer-Shell**: Click anywhere outside the panel or press **Escape** to instantly dismiss.
