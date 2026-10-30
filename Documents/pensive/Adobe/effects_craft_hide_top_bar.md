# Creative Craft Suite: Unified Menu Bar & Chrome Toggle on Linux (Without Recompiling)

A comprehensive, self-contained implementation, architecture, and troubleshooting reference for enabling a unified keyboard shortcut (<kbd>Ctrl</kbd> + <kbd>\</kbd>) to toggle and hide the top menu bar / header chrome across the entire Storyteller "Craft" creative suite on Linux:
* **EffectCraft** (After Effects alternative — Motion graphics & VFX compositor)
* **PhotoCraft** (Photoshop alternative — Layered image editor & raster/vector retouching)
* **FilmCraft** (Premiere Pro alternative — Non-linear video, audio & color editor)

Reclaims valuable vertical screen real estate (24px–38px) on compact displays (e.g. 15.6" laptops) without recompiling any binaries or disrupting other applications.

---

## 1. Executive Summary & Core Concept

### The Problem
On smaller laptop screens, timeline tracks, property panels, and preview viewports feel cramped. On Windows and macOS, Adobe applications allow users to press <kbd>Ctrl</kbd> + <kbd>\</kbd> (<kbd>Cmd</kbd> + <kbd>\</kbd>) to collapse window chrome and menu bars.

On Linux:
1. The built-in shortcuts inside the applications either attempt desktop window maximization (which Wayland tiling compositors like Hyprland/Sway deliberately ignore or floating window managers execute without hiding the menu bar), or have no effect.
2. The graphical preferences menus offer no setting to hide the top menu bars.
3. Recompiling these massive Rust/C++ projects from source is time-consuming, requires extensive toolchains, and breaks easily during upstream AppImage updates.

### The Solution: Built-in Headless IPC Control Servers
All three applications include a built-in, lightweight JSON-RPC loopback control server (`localhost` TCP) used internally for integration testing, automation, and MCP sidecars:
* By passing standard environment variables at application startup, each app spawns its loopback control listener on a dedicated port.
* A single, dependency-free Python helper script (`craft-menu-toggle`) queries the active window via the window compositor, talks to the appropriate application socket, and flips the menu bar visibility state instantly on the live frame.
* A conditional compositor keybinding binds <kbd>Ctrl</kbd> + <kbd>\</kbd> exclusively for the Craft suite windows and passes the key through unaltered to all other programs (terminals, browsers, text editors).

---

## 2. Technical Comparison & Architecture

| Application | Role | Control Port Env Var | Default Port | Auth Token Required? | Target Control Method | Space Reclaimed |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **EffectCraft** | Motion Graphics / VFX | `EFFECTCRAFT_CONTROL_PORT` | `9876` | No (open loopback) | `ui.set {"menuBar": <bool>}` | **24px** (Top Menu Bar) |
| **PhotoCraft** | Layered Photo Editing | `PHOTOCRAFT_CONTROL_PORT` | `9878` | Yes (`PHOTOCRAFT_CONTROL_TOKEN`) | `ui.set {"panels": {"menu_bar": <bool>}}` | **24px** (Top Menu Bar) |
| **FilmCraft** | Video & Audio NLE | `FILMCRAFT_CONTROL_PORT` | `9877` | Yes (`FILMCRAFT_CONTROL_TOKEN`) | `ui.menu.invoke {"id": "window.toggleFullScreen"}` | **Full Chrome** (Header & borders) |

### System Dataflow Diagram

```
+-----------------------------------------------------------------------------------------+
|                                      USER INPUT                                         |
|                           Presses <Ctrl> + <\> on Keyboard                              |
+-----------------------------------------------------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
|                         WINDOW COMPOSITOR (Hyprland / Sway / WM)                        |
|  1. Intercepts <Ctrl> + <\>                                                             |
|  2. Inspects focused window class:                                                      |
|     - "ai.storyteller.effectcraft" \                                                    |
|     - "ai.storyteller.photocraft"   -> Executes: ~/.local/bin/craft-menu-toggle         |
|     - "ai.storyteller.filmcraft"   /                                                    |
|     - Any other application -> Pass through key event (no interruption to terminal/IDE) |
+-----------------------------------------------------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
|                      HELPER SCRIPT (~/.local/bin/craft-menu-toggle)                     |
|  1. Identifies active app from window class or port probes (9876, 9878, 9877)           |
|  2. Opens TCP connection to 127.0.0.1:<port>                                            |
|  3. Sends JSON-lines payload:                                                           |
|     - EffectCraft:  {"method": "ui.inspect"} -> {"method": "ui.set", "menuBar": bool}  |
|     - PhotoCraft:   {"method": "auth", ...} -> {"method": "ui.set", "panels": {...}}   |
|     - FilmCraft:    {"id": 1, "token": ..., "method": "ui.menu.invoke", ...}           |
+-----------------------------------------------------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
|                              TARGET RUNNING CRAFT INSTANCE                              |
|  1. Internal background control thread receives JSON frame                              |
|  2. Modifies UI frame state dynamically in memory                                       |
|  3. Immediately redraws frame: Menu bar disappears, workspace expands to the edge       |
+-----------------------------------------------------------------------------------------+
```

---

## 3. Deep Dive: Codebase Mechanisms

### 1. EffectCraft (`crates/ui-egui`)
* **Menu Rendering (`crates/ui-egui/src/app/frame.rs`)**:
  ```rust
  if self.ui.show_menu_bar {
      let mb = egui::Rect::from_min_size(full.min, egui::vec2(full.width(), 24.0));
      let mut child = ui.new_child(egui::UiBuilder::new().max_rect(mb));
      child.painter().rect_filled(mb, 0.0, t.header_bg);
      menus::menu_bar(self, &mut child);
      top = mb.max.y;
  }
  ```
  When `show_menu_bar` is set to `false`, the 24px allocation is completely skipped, and the entire workspace layout origins at `full.min.y`.
* **IPC Control (`crates/ui-egui/src/control.rs`)**:
  Directly accepts `{"method": "ui.set", "params": {"menuBar": false}}` and writes to `app.ui.show_menu_bar`.

### 2. PhotoCraft (`crates/photocraft-control` & `crates/photocraft-ui`)
* **Authentication Guard**: PhotoCraft requires a 64-character hex auth token. Initial handshake requires:
  ```json
  {"method": "auth", "params": {"token": "0000000000000000000000000000000000000000000000000000000000000000"}}
  ```
  Responding with: `{"ok": true}`.
* **Panel Visibility Control**: In PhotoCraft, the menu bar is tracked under `panels.menu_bar`:
  * Inspect: `{"method": "ui.inspect"}` &rarr; `result.panels.menu_bar`.
  * Set: `{"method": "ui.set", "params": {"panels": {"menu_bar": false}}}`.
  Setting `menu_bar: false` immediately hides the top bar and reallocates the canvas viewport.

### 3. FilmCraft (`crates/filmcraft-control` & `crates/filmcraft-ui`)
* **Frame Protocol**: FilmCraft accepts request objects with an embedded token field:
  ```json
  {"id": 1, "token": "0000000000000000000000000000000000000000000000000000000000000000", "method": "...", "params": {...}}
  ```
* **Header Bar vs. Full Screen**: In FilmCraft, the top 38px bar (`crates/filmcraft-ui/src/views/header.rs`) combines in-window menus with workspace mode tabs (`Import`, `Edit`, `Export`). Its internal `show_header` boolean is non-configurable via `ui.set`, but its menu dispatch system exposes native fullscreen toggling:
  ```json
  {"id": 1, "token": "...", "method": "ui.menu.invoke", "params": {"id": "window.toggleFullScreen"}}
  ```
  This cleanly hides the window header chrome and borders, dedicating 100% of the display to the timeline, source monitor, and preview monitor.

---

## 4. Step-by-Step Implementation Guide

Follow these steps on any Linux machine to set up the unified toggle.

### Step 1: Create the Unified Toggle Script

Create `~/.local/bin/craft-menu-toggle`. This script automatically identifies which Craft application is currently active, handles all auth tokens, and toggles the menu bar or chrome.

```bash
mkdir -p ~/.local/bin
cat << 'EOF' > ~/.local/bin/craft-menu-toggle
#!/usr/bin/env python3
"""Unified menu bar / chrome toggle for EffectCraft, PhotoCraft, and FilmCraft."""

import json
import os
import socket
import subprocess
import sys

DEFAULT_TOKEN = "0000000000000000000000000000000000000000000000000000000000000000"

APPS = {
    "effectcraft": {
        "port": int(os.environ.get("EFFECTCRAFT_CONTROL_PORT", 9876)),
        "class_match": "effectcraft",
    },
    "photocraft": {
        "port": int(os.environ.get("PHOTOCRAFT_CONTROL_PORT", 9878)),
        "token": os.environ.get("PHOTOCRAFT_CONTROL_TOKEN", DEFAULT_TOKEN),
        "class_match": "photocraft",
    },
    "filmcraft": {
        "port": int(os.environ.get("FILMCRAFT_CONTROL_PORT", 9877)),
        "token": os.environ.get("FILMCRAFT_CONTROL_TOKEN", DEFAULT_TOKEN),
        "class_match": "filmcraft",
    },
}

def get_active_window_class():
    try:
        out = subprocess.check_output(["hyprctl", "activewindow", "-j"], timeout=1.0)
        data = json.loads(out)
        return (data.get("class") or data.get("initialClass") or "").lower()
    except Exception:
        return ""

def send_recv(sock, f, msg):
    sock.sendall(json.dumps(msg).encode("utf-8") + b"\n")
    line = f.readline()
    if not line:
        return {}
    return json.loads(line)

def toggle_effectcraft(port, action="toggle"):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    s.connect(("127.0.0.1", port))
    f = s.makefile()

    if action == "toggle":
        inspect_res = send_recv(s, f, {"method": "ui.inspect"})
        current = inspect_res.get("result", {}).get("ui", {}).get("show_menu_bar", True)
        target = not current
    elif action == "hide":
        target = False
    else:
        target = True

    send_recv(s, f, {"method": "ui.set", "params": {"menuBar": target}})
    state = "hidden" if not target else "visible"
    print(f"EffectCraft menu bar is now {state}.")

def toggle_photocraft(port, token, action="toggle"):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    s.connect(("127.0.0.1", port))
    f = s.makefile()

    auth_res = send_recv(s, f, {"method": "auth", "params": {"token": token}})
    if not auth_res.get("ok"):
        raise RuntimeError(f"PhotoCraft auth failed: {auth_res.get('error')}")

    if action == "toggle":
        inspect_res = send_recv(s, f, {"method": "ui.inspect"})
        panels = inspect_res.get("result", {}).get("panels", {})
        current = panels.get("menu_bar", True)
        target = not current
    elif action == "hide":
        target = False
    else:
        target = True

    send_recv(s, f, {"method": "ui.set", "params": {"panels": {"menu_bar": target}}})
    state = "hidden" if not target else "visible"
    print(f"PhotoCraft menu bar is now {state}.")

def toggle_filmcraft(port, token, action="toggle"):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    s.connect(("127.0.0.1", port))
    f = s.makefile()

    res = send_recv(s, f, {
        "id": 1,
        "token": token,
        "method": "ui.menu.invoke",
        "params": {"id": "window.toggleFullScreen"},
    })
    fs = res.get("result", {}).get("fullScreen", "toggled")
    print(f"FilmCraft full screen toggled (state: {fs}).")

def main():
    target = None
    action = "toggle"

    # 1. Check if caller invoked via a specific name symlink (e.g., photocraft-menu-toggle)
    prog = os.path.basename(sys.argv[0]).lower()
    for name in APPS:
        if name in prog:
            target = name
            break

    # 2. Parse command-line arguments for target or action
    for arg in sys.argv[1:]:
        a = arg.lower()
        if a in APPS:
            target = a
        elif a in ("toggle", "hide", "show"):
            action = a
        elif a.isdigit() and target:
            APPS[target]["port"] = int(a)

    # 3. If target not explicitly set, detect active window class via Hyprland
    if not target:
        active_class = get_active_window_class()
        for name, cfg in APPS.items():
            if cfg["class_match"] in active_class:
                target = name
                break

    # 4. If still not detected, probe listening ports
    if not target:
        for name, cfg in APPS.items():
            try:
                s = socket.socket()
                s.settimeout(0.2)
                s.connect(("127.0.0.1", cfg["port"]))
                s.close()
                target = name
                break
            except Exception:
                continue

    if not target:
        print("No active EffectCraft, PhotoCraft, or FilmCraft instance detected.", file=sys.stderr)
        sys.exit(1)

    try:
        if target == "effectcraft":
            toggle_effectcraft(APPS["effectcraft"]["port"], action=action)
        elif target == "photocraft":
            toggle_photocraft(APPS["photocraft"]["port"], APPS["photocraft"]["token"], action=action)
        elif target == "filmcraft":
            toggle_filmcraft(APPS["filmcraft"]["port"], APPS["filmcraft"]["token"], action=action)
    except Exception as e:
        print(f"Error toggling {target}: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
EOF
chmod +x ~/.local/bin/craft-menu-toggle

# Create convenient aliases/symlinks for app-specific usage:
ln -sf craft-menu-toggle ~/.local/bin/effectcraft-menu-toggle
ln -sf craft-menu-toggle ~/.local/bin/photocraft-menu-toggle
ln -sf craft-menu-toggle ~/.local/bin/filmcraft-menu-toggle
```

---

### Step 2: Ensure Control Servers Run on Startup (AppImage In-Place Wrapping)

To ensure the control server is **always** running—regardless of whether you launch the app via a terminal, desktop menu, rofi/wofi launcher, or file manager—wrap each AppImage binary in-place.

Assuming the AppImages are located in `/path/to/apps/` (e.g. `/mnt/zram1`):

#### 1. Wrap EffectCraft AppImage:
```bash
cd /path/to/apps
mv effectcraft-0.18.0-linux-x86_64.AppImage .effectcraft-0.18.0-linux-x86_64.AppImage.real

cat << 'EOF' > effectcraft-0.18.0-linux-x86_64.AppImage
#!/usr/bin/env bash
# Transparent wrapper: auto-enable EffectCraft control server on port 9876
export EFFECTCRAFT_CONTROL_PORT="${EFFECTCRAFT_CONTROL_PORT:-9876}"
exec "$(dirname "$0")/.effectcraft-0.18.0-linux-x86_64.AppImage.real" "$@"
EOF
chmod +x effectcraft-0.18.0-linux-x86_64.AppImage
```

#### 2. Wrap PhotoCraft AppImage:
```bash
cd /path/to/apps
mv photocraft-0.17.0-linux-x86_64.AppImage .photocraft-0.17.0-linux-x86_64.AppImage.real

cat << 'EOF' > photocraft-0.17.0-linux-x86_64.AppImage
#!/usr/bin/env bash
# Transparent wrapper: auto-enable PhotoCraft control server on port 9878 with static token
export PHOTOCRAFT_CONTROL_PORT="${PHOTOCRAFT_CONTROL_PORT:-9878}"
export PHOTOCRAFT_CONTROL_TOKEN="${PHOTOCRAFT_CONTROL_TOKEN:-0000000000000000000000000000000000000000000000000000000000000000}"
exec "$(dirname "$0")/.photocraft-0.17.0-linux-x86_64.AppImage.real" "$@"
EOF
chmod +x photocraft-0.17.0-linux-x86_64.AppImage
```

#### 3. Wrap FilmCraft AppImage:
```bash
cd /path/to/apps
mv filmcraft-0.14.0-linux-x86_64.AppImage .filmcraft-0.14.0-linux-x86_64.AppImage.real

cat << 'EOF' > filmcraft-0.14.0-linux-x86_64.AppImage
#!/usr/bin/env bash
# Transparent wrapper: auto-enable FilmCraft control server on port 9877 with static token
export FILMCRAFT_CONTROL_PORT="${FILMCRAFT_CONTROL_PORT:-9877}"
export FILMCRAFT_CONTROL_TOKEN="${FILMCRAFT_CONTROL_TOKEN:-0000000000000000000000000000000000000000000000000000000000000000}"
exec "$(dirname "$0")/.filmcraft-0.14.0-linux-x86_64.AppImage.real" "$@"
EOF
chmod +x filmcraft-0.14.0-linux-x86_64.AppImage
```

---

### Step 3: Install User CLI Commands (`~/.local/bin`)

To launch the applications directly from the terminal or CLI:

```bash
# ~/.local/bin/effectcraft
cat << 'EOF' > ~/.local/bin/effectcraft
#!/usr/bin/env bash
export EFFECTCRAFT_CONTROL_PORT="${EFFECTCRAFT_CONTROL_PORT:-9876}"
exec /path/to/apps/.effectcraft-0.18.0-linux-x86_64.AppImage.real "$@"
EOF
chmod +x ~/.local/bin/effectcraft

# ~/.local/bin/photocraft
cat << 'EOF' > ~/.local/bin/photocraft
#!/usr/bin/env bash
export PHOTOCRAFT_CONTROL_PORT="${PHOTOCRAFT_CONTROL_PORT:-9878}"
export PHOTOCRAFT_CONTROL_TOKEN="${PHOTOCRAFT_CONTROL_TOKEN:-0000000000000000000000000000000000000000000000000000000000000000}"
exec /path/to/apps/.photocraft-0.17.0-linux-x86_64.AppImage.real "$@"
EOF
chmod +x ~/.local/bin/photocraft

# ~/.local/bin/filmcraft
cat << 'EOF' > ~/.local/bin/filmcraft
#!/usr/bin/env bash
export FILMCRAFT_CONTROL_PORT="${FILMCRAFT_CONTROL_PORT:-9877}"
export FILMCRAFT_CONTROL_TOKEN="${FILMCRAFT_CONTROL_TOKEN:-0000000000000000000000000000000000000000000000000000000000000000}"
exec /path/to/apps/.filmcraft-0.14.0-linux-x86_64.AppImage.real "$@"
EOF
chmod +x ~/.local/bin/filmcraft
```

---

### Step 4: Configure Desktop Launcher Entries

Ensure desktop launchers point to the wrapped AppImages so application menus work seamlessly:

#### `~/.local/share/applications/ai.storyteller.effectcraft.desktop`:
```ini
[Desktop Entry]
Type=Application
Name=EffectCraft
Comment=Motion graphics and visual effects compositor
Exec=/path/to/apps/effectcraft-0.18.0-linux-x86_64.AppImage %F
Icon=ai.storyteller.effectcraft
Terminal=false
StartupWMClass=ai.storyteller.effectcraft
```

#### `~/.local/share/applications/ai.storyteller.photocraft.desktop`:
```ini
[Desktop Entry]
Type=Application
Name=PhotoCraft
Comment=Edit photos and layered PSD documents
Exec=/path/to/apps/photocraft-0.17.0-linux-x86_64.AppImage %F
Icon=ai.storyteller.photocraft
Terminal=false
StartupWMClass=ai.storyteller.photocraft
```

#### `~/.local/share/applications/ai.storyteller.filmcraft.desktop`:
```ini
[Desktop Entry]
Type=Application
Name=FilmCraft
Comment=Edit video, color and sound
Exec=/path/to/apps/filmcraft-0.14.0-linux-x86_64.AppImage %F
Icon=ai.storyteller.filmcraft
Terminal=false
StartupWMClass=ai.storyteller.filmcraft
```

---

### Step 5: Configure Window Manager / Compositor Keybinding

The keybinding checks if the focused window belongs to any Craft suite app. If yes, it runs `craft-menu-toggle`. If not, it passes <kbd>Ctrl</kbd> + <kbd>\</kbd> directly through to the active program.

#### A. For Hyprland with Lua Config (`~/.config/hypr/source/keybinds.lua`):
```lua
-- Unified Craft Suite Menu / Chrome Toggle (Control + \) for EffectCraft, PhotoCraft, FilmCraft
hl.bind(
    "CTRL + backslash",
    function()
        local w = hl.get_active_window()
        local class = (w and (w.class or w.initialClass)) or ""
        local c = class:lower()
        if string.find(c, "effectcraft") or string.find(c, "photocraft") or string.find(c, "filmcraft") then
            hl.dispatch(hl.dsp.exec_cmd("craft-menu-toggle"))
        else
            hl.dispatch(hl.dsp.pass({ window = "activewindow" }))
        end
    end,
    { description = "Toggle Craft Suite Menu / Chrome" }
)
```
Reload Hyprland:
```bash
hyprctl reload
```

#### B. For Standard Hyprland (`~/.config/hypr/hyprland.conf`):
```conf
# Execute toggle script directly
bind = CTRL, backslash, exec, craft-menu-toggle
```

#### C. For Sway or i3 (`~/.config/sway/config` or `~/.config/i3/config`):
```conf
bindsym Control+backslash exec craft-menu-toggle
```

#### D. For GNOME / KDE Plasma:
* Go to **System Settings ▸ Keyboard ▸ Custom Shortcuts**.
* Add a shortcut:
  * **Name**: `Craft Suite Toggle Menu`
  * **Command**: `craft-menu-toggle`
  * **Shortcut**: <kbd>Ctrl</kbd> + <kbd>\</kbd>

---

## 5. Verification & Testing

### 1. Verify Sockets are Listening
With all three apps running, verify loopback socket ports are active:
```bash
ss -tulpn | grep -E "9876|9877|9878"
```
**Expected Output**:
```
tcp  LISTEN 0 16 127.0.0.1:9876  users:(("effectcraft",pid=...,fd=...))
tcp  LISTEN 0 16 127.0.0.1:9877  users:(("filmcraft",pid=...,fd=...))
tcp  LISTEN 0 16 127.0.0.1:9878  users:(("photocraft",pid=...,fd=...))
```

### 2. Manual CLI Verification
Test each application directly from a shell:
```bash
# Test EffectCraft
craft-menu-toggle effectcraft
# Output: EffectCraft menu bar is now hidden.

# Test PhotoCraft
craft-menu-toggle photocraft
# Output: PhotoCraft menu bar is now hidden.

# Test FilmCraft
craft-menu-toggle filmcraft
# Output: FilmCraft full screen toggled (state: true).
```

### 3. Keybinding Verification
1. Focus **EffectCraft** and press <kbd>Ctrl</kbd> + <kbd>\</kbd>: Menu bar disappears instantly. Press again: Reappears.
2. Focus **PhotoCraft** and press <kbd>Ctrl</kbd> + <kbd>\</kbd>: Menu bar disappears instantly. Press again: Reappears.
3. Focus **FilmCraft** and press <kbd>Ctrl</kbd> + <kbd>\</kbd>: Chrome collapses into full screen mode. Press again: Restores.
4. Focus a **terminal** or **code editor** and press <kbd>Ctrl</kbd> + <kbd>\</kbd>: The key performs its normal function (`SIGQUIT` or split pane) without interference.

---

## 6. Technical Reference & FAQ

### Q1: Can the bottom Timeline footer in EffectCraft (`Toggle Switches / Modes`, `Frame Render Time`, zoom slider) be hidden?
**No.** In `crates/ui-egui/src/panels/timeline.rs`:
* Line 1637 sets `let footer_h = 24.0;`.
* Lines 1846–1847 unconditionally invoke `footer_bar` and `footer_zoom`.
There is no configuration flag, preference setting, or IPC control method in the codebase to hide the timeline footer without editing the Rust source code and recompiling.

### Q2: What causes `Connection refused` when invoking `craft-menu-toggle`?
`Connection refused` indicates that the application process was launched without its respective control port environment variable (`EFFECTCRAFT_CONTROL_PORT`, `PHOTOCRAFT_CONTROL_PORT`, or `FILMCRAFT_CONTROL_PORT`). Ensure:
1. All old pre-wrapper instances are fully terminated: `pkill -f 'effectcraft|photocraft|filmcraft'`.
2. The application was launched using either the in-place wrapped AppImage or the `~/.local/bin/*` wrapper script.

### Q3: Why does PhotoCraft or FilmCraft report `auth failed`?
PhotoCraft and FilmCraft enforce token validation on loopback. The wrapper scripts and `craft-menu-toggle` share the static token `0000000000000000000000000000000000000000000000000000000000000000`. If you override `PHOTOCRAFT_CONTROL_TOKEN` or `FILMCRAFT_CONTROL_TOKEN` in your environment, ensure the same token is exported in your shell session.

### Q4: Does this modification survive upstream updates?
**Yes.** The JSON-RPC control server is a stable, documented feature of the Storyteller Craft suite used by their MCP plugin ecosystem and automated QA test harnesses. When updating to newer releases, simply re-apply the transparent in-place AppImage wrapper to the new AppImage binary. No recompilation will ever be required.

---

## 7. Custom Native Build from Source (With Built-in Fixes & Hardware Optimizations)

If upstream Pull Request [#2647](https://github.com/storytold/effectcraft/pull/2647) takes time to merge, or if you want maximum rendering throughput, sub-100ms cold startup, and zero FUSE overhead on modern hardware, you can compile EffectCraft directly as a host-native binary.

### 1. In-RAM Build Strategy (Zero NAND Flash Wear)
Compiling a 40+ crate Rust workspace produces 4–6 GB of intermediate target artifacts. To prevent SSD write wear and accelerate disk I/O, build directly inside the system's compressed RAM drive (`/mnt/zram1`):

```bash
# Prepare RAM workspace
mkdir -p /mnt/zram1/build
cd /mnt/zram1/build

# Clone the fork branch containing the native menu bar toggle fix (PR #2647)
git clone --depth=1 -b feat/2596-toggle-menu-bar https://github.com/dusklinux/effectcraft.git
cd effectcraft
```

### 2. Host-Native Toolchain & Optimization Flags
EffectCraft's rendering engine (`crates/raster`, `crates/path`, `tiny-skia`) and software codecs (`crates/vp9enc`, `crates/av1enc`, `filmcraft-h264enc`) heavily utilize SIMD vector operations. Generic AppImages are restricted to baseline `x86-64` (SSE2) to remain bootable on legacy processors. 

Compiling with `-C target-cpu=native` activates **AVX2**, **FMA3**, and **BMI1/2**, doubling vector throughput for software alpha compositing, color grading, and video encoding. Mirroring the system build configuration (`~/.config/pacman/makepkg.conf`):

```bash
# CPU instruction set, linker, and relocation flags
export RUSTFLAGS="-C target-cpu=native -C opt-level=3 -C link-arg=-fuse-ld=mold -C link-arg=-Wl,-z,pack-relative-relocs"
export CFLAGS="-march=native -mtune=native -O2"
export CXXFLAGS="$CFLAGS"

# Compile desktop release binary on all CPU cores
cargo build --release -p effectcraft --jobs $(nproc)
```

### 3. Surgical Code Modification Reference
If compiling clean upstream code in the future without cloning the fork, the native Menu Bar toggle requires only 5 surgical insertions:

1. **`crates/engine/src/cmd/frontend.rs`** (Add command ID to `fe!` macro):
   ```rust
   (ViewMenuBar, "view.menuBar", "Toggle Menu Bar", "Toggle Top Menu Bar"),
   ```
2. **`crates/engine/src/menus.rs`** (Add menu item under `Menu::View`):
   ```rust
   Entry::item("Menu Bar", "view.menuBar"),
   ```
3. **`crates/ui-egui/src/menus/frontend.rs`** (Dispatch handler):
   ```rust
   "view.menuBar" => toggle(&mut app.ui.show_menu_bar, &p),
   ```
4. **`crates/ui-egui/src/menus/entries.rs`** (Menu checkmark state):
   ```rust
   "view.menuBar" => Some(app.ui.show_menu_bar),
   ```
5. **`crates/ui-egui/src/menus/tests.rs`** (Unit test):
   ```rust
   #[test]
   fn view_menu_bar_toggles_show_menu_bar() {
       let mut app = dummy_app();
       assert!(app.ui.show_menu_bar);
       menus::handle_command(&mut app, "view.menuBar", &Default::default());
       assert!(!app.ui.show_menu_bar);
       menus::handle_command(&mut app, "view.menuBar", &Default::default());
       assert!(app.ui.show_menu_bar);
   }
   ```

### 4. Installation & Desktop Launcher Integration
Install the native executable into user path:

```bash
# Copy compiled ELF binary to ~/.local/bin
install -Dm755 /mnt/zram1/build/effectcraft/target/release/effectcraft ~/.local/bin/effectcraft

# Update desktop launcher (~/.local/share/applications/ai.storyteller.effectcraft.desktop)
cat << 'EOF' > ~/.local/share/applications/ai.storyteller.effectcraft.desktop
[Desktop Entry]
Type=Application
Name=EffectCraft
Comment=Motion graphics and visual effects compositor (Native Build)
Exec=/home/dusk/.local/bin/effectcraft %F
Icon=ai.storyteller.effectcraft
Terminal=false
StartupWMClass=ai.storyteller.effectcraft
Categories=Graphics;Video;AudioVideo;
EOF
```

With this binary installed, pressing <kbd>Ctrl</kbd> + <kbd>\</kbd> or toggling **View ▸ Menu Bar** operates directly inside the application process without requiring IPC sockets or wrapper scripts.

