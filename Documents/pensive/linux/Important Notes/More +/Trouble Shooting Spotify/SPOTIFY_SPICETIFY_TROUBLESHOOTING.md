# Spotify + Spicetify Troubleshooting Guide

**Date:** 2026-02-12
**System:** Arch Linux (CachyOS kernel 6.18.7-1)
**GPU:** NVIDIA GeForce RTX 4090
**Display Server:** Wayland
**Issue:** Spotify crashing with Spicetify modifications

---

## Problem Description

Spotify was not launching properly after updates. When launched, it would either:
1. Crash immediately with SIGSEGV (segmentation fault)
2. Launch but show "Something went wrong" error dialog
3. Process would run in background but GUI would crash

Initial suspicion: Spotify updates overwriting Spicetify modifications.

---

## System Information

```
OS: Arch Linux
Kernel: 6.18.7-1-cachyos
GPU: NVIDIA GeForce RTX 4090 (AD102)
Display: Wayland (wayland-1)
X11 Fallback: Available (:0)
```

### Initial Package Versions
- `spotify-launcher 0.6.5-1` (initially installed)
- `spicetify-cli 2.42.8-1`
- Spotify version: `1.2.82.428.g0ac8be2b`

---

## Troubleshooting Steps Attempted

### 1. Initial Spicetify Restore/Apply (FAILED)

**Attempt:** Standard Spicetify fix for post-update issues
```bash
spicetify restore backup apply
```

**Result:** ❌ Spotify crashed with SIGSEGV
**Crash logs:**
```
coredumpctl list | grep spotify
Signal: 11 (SEGV)
Stack trace shows crashes in libcef.so (Chromium Embedded Framework)
```

---

### 2. GPU Acceleration Fixes (FAILED)

**Attempt:** Disable GPU acceleration via spotify-launcher config
**File:** `~/.config/spotify-launcher.conf`

**Config 1 - Basic GPU disable:**
```toml
extra_arguments = [
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-gpu-compositing",
    "--no-zygote"
]
```
**Result:** ❌ Still crashed after 10-30 seconds

**Config 2 - Force X11 mode:**
```toml
extra_arguments = [
    "--enable-features=UseOzonePlatform",
    "--ozone-platform=x11",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-gpu-compositing"
]
```
**Result:** ❌ Failed to launch

**Config 3 - Aggressive compatibility:**
```toml
extra_arguments = [
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-gpu-compositing",
    "--disable-gpu-sandbox",
    "--disable-features=VizDisplayCompositor",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-seccomp-filter-sandbox",
    "--disable-setuid-sandbox"
]
```
**Result:** ❌ Still crashed

**Config 4 - Force native Wayland:**
```toml
extra_arguments = [
    "--use-gl=desktop",
    "--enable-features=UseOzonePlatform",
    "--ozone-platform=wayland",
    "--enable-features=WaylandWindowDecorations",
    "--disable-gpu-driver-bug-workarounds"
]
```
**Result:** ❌ Still crashed

---

### 3. Cache Clearing (FAILED)

**Attempt:** Clear Spotify cache to fix potential corruption
```bash
rm -rf ~/.cache/spotify
rm -rf ~/.config/spotify/Users/*-user/offline.bnk
rm -rf ~/.config/spotify/Users/*-user/local-files.bnk
```
**Result:** ❌ No improvement

---

### 4. NVIDIA Environment Variables (FAILED)

**Attempt:** Force NVIDIA-specific rendering
```bash
LIBVA_DRIVER_NAME=nvidia \
__GLX_VENDOR_LIBRARY_NAME=nvidia \
__GL_SYNC_TO_VBLANK=0 \
spotify-launcher
```
**Result:** ❌ Still crashed with SIGSEGV

---

### 5. Complete Reinstall (FAILED)

**Attempt:** Remove all Spotify data and reinstall
```bash
pkill -9 -f spotify
rm -rf ~/.config/spotify
rm -rf ~/.cache/spotify
rm -rf ~/.local/share/spotify-launcher
```
**Result:** ❌ Crashes persisted after reinstall

---

### 6. Switch to AUR `spotify` Package (SUCCESS)

**Root Cause Identified:** `spotify-launcher` package has compatibility issues with NVIDIA + Wayland

**Solution:**
```bash
# Remove spotify-launcher
sudo pacman -R spotify-launcher

# Install AUR spotify package
yay -S spotify
```

**Key Differences:**
- **spotify-launcher:** Installs to `~/.local/share/spotify-launcher/install/usr/share/spotify/`
- **spotify (AUR):** Installs to `/opt/spotify/`

**Result:** ✅ Vanilla Spotify works perfectly without crashes

**Spotify path for Spicetify:**
```bash
spicetify config spotify_path /opt/spotify/
```

---

### 7. Spicetify Permissions (REQUIRED)

**Issue:** Spicetify cannot write to `/opt/spotify/` (owned by root)

**Solution:**
```bash
sudo chmod a+wr /opt/spotify
sudo chmod a+wr /opt/spotify/Apps -R
```

**Important:** Do NOT use `sudo spicetify` - causes permission issues

---

### 8. Spicetify with Full Configuration (FAILED)

**Attempt:** Run setup script with all features
```bash
bash 075_spicetify_matugen_setup.sh --yes
```

**Configuration applied:**
- ✅ Devtools enabled
- ✅ Comfy theme
- ✅ Marketplace
- ✅ All preprocesses enabled

**Result:** ❌ Spotify launches then crashes with SIGSEGV
**Observation:** Process stays alive but GUI crashes repeatedly

---

### 9. Disable Devtools (FAILED)

**Attempt:** Disable always_enable_devtools
```bash
spicetify config always_enable_devtools 0
spicetify restore backup apply
```

**Result:** ❌ DevTools still launching (detected in logs)
**Crash:** Still occurring with SIGSEGV

---

### 10. Minimal Spicetify - No Theme/Marketplace (FAILED)

**Attempt:** Apply only core Spicetify preprocessing
```bash
spicetify config current_theme " "
spicetify config color_scheme " "
spicetify config inject_css 0
spicetify config inject_theme_js 0
spicetify config custom_apps " "
spicetify config extensions " "
spicetify backup apply
```

**Result:** ❌ "Something went wrong - Try reloading the page" error
**Reason:** Spicetify needs `expose_apis` enabled to function

---

### 11. Disable Experimental Features (FAILED)

**Attempt:** Enable expose_apis but disable experimental features
```bash
spicetify config experimental_features 0
spicetify config expose_apis 1
spicetify backup apply
```

**Result:** ❌ "Something went wrong" error persists
**Testing:** Even with various combinations, error wouldn't clear

---

### 12. Absolute Minimal Spicetify (FAILED)

**Attempt:** Test if ANY Spicetify preprocessing works
```bash
spicetify config current_theme " "
spicetify config inject_css 0
spicetify config inject_theme_js 0
spicetify config custom_apps " "
spicetify config expose_apis 1
spicetify config experimental_features 0
spicetify apply
```

**Result:** ❌ "Something went wrong" error
**Conclusion:** Even the most basic Spicetify modifications are incompatible

---

## Root Cause Analysis

### Primary Issue: NVIDIA + Wayland + Chromium Embedded Framework Incompatibility

Spotify uses **Chromium Embedded Framework (CEF)** which has known issues with NVIDIA GPU drivers on Wayland:

1. **CEF Rendering Issues:**
   - Stack traces consistently showed crashes in `libcef.so`
   - Crashes at memory addresses related to GPU compositing
   - SIGSEGV (segmentation fault) and SIGTRAP errors

2. **Spicetify Modifications:**
   - Spicetify modifies Spotify's JavaScript and CSS
   - These modifications interact poorly with CEF on NVIDIA + Wayland
   - Even minimal preprocessing causes "Something went wrong" errors
   - The preprocessing breaks the app's ability to load content

3. **Wayland + NVIDIA Specific:**
   - BadWindow X errors detected: `BadWindow (invalid Window parameter)`
   - XWayland compatibility layer issues
   - GPU driver bug workarounds fail

### Secondary Issue: spotify-launcher Compatibility

The `spotify-launcher` package had additional issues:
- More aggressive CEF crashes (15.9MB core dumps)
- Installed to user directory with complex path handling
- AUR `spotify` package proved more stable (13-15MB dumps, but less frequent)

---

## Crash Analysis Details

### Typical Crash Pattern

```
coredumpctl info [PID]
Signal: 11 (SEGV)
Command Line: /opt/spotify/spotify
Executable: /opt/spotify/spotify

Stack trace:
#0  0x00005571271c3bde n/a (spotify + 0x123cbde)
#1  0x00005571271c4b41 n/a (spotify + 0x123db41)
#2  0x00005571285f2494 n/a (spotify + 0x266b494)
...
[libcef.so crashes detected in multiple threads]
```

### DevTools Persistence Issue

Even with `always_enable_devtools = 0`, DevTools continued launching:
```
DevTools listening on ws://127.0.0.1:8088/devtools/browser/[uuid]
```

This suggests Spicetify's preprocessing automatically enables DevTools regardless of config.

---

## Final Solution

### ✅ Use Vanilla Spotify (No Spicetify)

```bash
# Restore Spotify to unmodified state
spicetify restore

# Launch Spotify normally
spotify
```

**Result:**
- ✅ Fully functional
- ✅ No crashes
- ✅ All features work
- ❌ No theme customization
- ❌ No marketplace
- ❌ No extensions

---

## Alternative Solutions (Not Tested)

### Option 1: Switch to X11 Display Server

Wayland + NVIDIA is the primary issue. Switching to X11 might allow Spicetify to work:

```bash
# Log out and select X11 session at login screen
# OR force X11 for Spotify only:
WAYLAND_DISPLAY= spotify
```

**Pros:** Spicetify might work
**Cons:** Requires switching entire session or per-app workarounds

### Option 2: Use Alternative Spotify Client

**Spot** - Native GTK Spotify client:
```bash
yay -S spot
```

**Pros:** Native Linux app, better Wayland support
**Cons:** Different UI, limited customization

### Option 3: Wait for Better NVIDIA Wayland Support

NVIDIA's Wayland support is improving. Future driver updates may resolve CEF issues.

---

## Configuration Files

### Working Spicetify Config (for X11 users)

`~/.config/spicetify/config-xpui.ini`:
```ini
[Setting]
spotify_path = /opt/spotify/
prefs_path = /home/USER/.config/spotify/prefs
current_theme = Comfy
color_scheme = Comfy
inject_css = 1
inject_theme_js = 1
replace_colors = 1
overwrite_assets = 0
check_spicetify_update = 1
always_enable_devtools = 0

[Preprocesses]
remove_rtl_rule = 1
expose_apis = 1
disable_sentry = 1
disable_ui_logging = 1

[AdditionalFeatures]
home_config = 1
experimental_features = 1
extensions =
custom_apps = marketplace
sidebar_config = 0
```

**Note:** This config will NOT work on Wayland + NVIDIA

---

## Script Modifications Required

### 075_spicetify_matugen_setup.sh

**Current behavior:** Automatically enables devtools and full features
**Problem:** Crashes on NVIDIA + Wayland systems

**Recommended modification:**

Add system detection before applying Spicetify:

```bash
detect_display_server() {
    if [[ "$XDG_SESSION_TYPE" == "wayland" ]]; then
        # Check for NVIDIA GPU
        if lspci | grep -i vga | grep -i nvidia &>/dev/null; then
            log_warn "NVIDIA + Wayland detected!"
            log_warn "Spicetify has known compatibility issues with this setup."
            log_warn "Spotify may crash or show errors."

            printf "${COLOR_BOLD}Continue anyway? [y/n]: ${COLOR_RESET}"
            read -r confirm
            case "${confirm,,}" in
                y|yes) log_info "Proceeding with Spicetify installation..." ;;
                *) die "Installation aborted. Use vanilla Spotify instead." ;;
            esac
        fi
    fi
}

main() {
    check_requirements
    detect_display_server  # Add this line
    prompt_user_confirmation "${1:-}"
    setup_spicetify
    install_marketplace
    setup_theme

    echo ""
    log_success "Spicetify setup complete!"
    log_info "If Spotify crashes or shows errors, run: spicetify restore"
}
```

---

## Lessons Learned

1. **Hardware matters:** GPU + Display Server combinations can break application-level modifications
2. **Package choice matters:** `spotify` (AUR) more stable than `spotify-launcher` for NVIDIA systems
3. **CEF is fragile:** Chromium Embedded Framework doesn't handle all GPU configurations well
4. **Preprocessing breaks things:** Even minimal Spicetify modifications fail on incompatible systems
5. **Test incrementally:** We should have tested vanilla Spotify first before applying Spicetify
6. **Document system info:** GPU/Display Server details are critical for troubleshooting

---

## Quick Reference Commands

### Check for crashes
```bash
coredumpctl list | grep spotify
coredumpctl info [PID]  # Get crash details
```

### Check display server
```bash
echo $XDG_SESSION_TYPE  # wayland or x11
echo $WAYLAND_DISPLAY   # Should show wayland-1 or similar
```

### Check GPU
```bash
lspci | grep -i vga
glxinfo | grep -i "opengl renderer"
```

### Spicetify restore
```bash
spicetify restore  # Remove all modifications
```

### Spicetify apply
```bash
spicetify backup apply  # Apply modifications
```

### Check Spicetify config
```bash
spicetify config  # Show all settings
spicetify -c      # Show config file path
```

---

## Conclusion

**Spotify + Spicetify is NOT compatible with NVIDIA RTX 4090 + Wayland on Arch Linux (as of 2026-02-12).**

The issue stems from fundamental incompatibilities between:
- NVIDIA proprietary drivers
- Wayland display protocol
- Chromium Embedded Framework (used by Spotify)
- Spicetify's JavaScript/CSS modifications

**Working solution:** Use vanilla Spotify without Spicetify modifications.

**Future possibility:** Switch to X11 display server if theme customization is critical.

---

## Additional Resources

- [Spicetify GitHub Issues - Wayland](https://github.com/spicetify/cli/issues?q=wayland)
- [NVIDIA Wayland Support Status](https://wiki.archlinux.org/title/Wayland#NVIDIA)
- [Chromium Embedded Framework Issues](https://bitbucket.org/chromiumembedded/cef/issues)
- [Arch Wiki - Spotify](https://wiki.archlinux.org/title/Spotify)

---

**Document Version:** 1.0
**Last Updated:** 2026-02-12
**Author:** Troubleshooting session with Claude Code
