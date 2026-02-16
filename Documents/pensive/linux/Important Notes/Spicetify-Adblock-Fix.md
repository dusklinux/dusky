# Spicetify Adblock Troubleshooting Guide

**Date:** February 16, 2026
**Issue:** Ads still playing in Spotify despite having adblock extension installed
**Status:** ✅ RESOLVED

---

## Problem Description

Spotify was still playing audio ads between songs even though Spicetify was installed with the adblock.js extension configured.

### Symptoms
- ✅ Spicetify installed (v2.42.10)
- ✅ adblock.js extension present in config
- ✅ Extension listed in `config-xpui.ini`
- ❌ Ads still playing during music playback

---

## Root Cause

Spicetify was configured to modify Spotify at an incorrect path:

```ini
# WRONG PATH (old configuration)
spotify_path = /home/coops/.local/share/spotify-launcher/install/usr/share/spotify/

# CORRECT PATH (actual Spotify installation)
spotify_path = /opt/spotify/
```

**The Issue:**
- Spotify was actually installed at `/opt/spotify/` (from Arch package)
- Spicetify was configured to modify files at a different location
- The `xpui.spa` file was never extracted and modified
- Extensions were never injected into the running Spotify instance

---

## The Fix

### Step 1: Update Spicetify Configuration

```bash
# Update the spotify_path to the correct location
spicetify config spotify_path /opt/spotify/
```

### Step 2: Fix Permissions

Since `/opt/spotify/` is owned by root, we need to either:

**Option A: Run with sudo (one-time)**
```bash
sudo /home/coops/.spicetify/spicetify backup apply
```

**Option B: Change permissions (better for future updates)**
```bash
sudo chmod a+wr /opt/spotify
sudo chmod a+wr /opt/spotify/Apps -R
spicetify backup apply
```

### Step 3: Restart Spotify

```bash
pkill spotify
spotify &
```

---

## Verification Steps

After applying the fix, verify everything is working:

### 1. Check xpui Extraction
```bash
ls /opt/spotify/Apps/
# Should show: login/ xpui/ (folders, not .spa files)
```

### 2. Verify Extension Loading
```bash
grep -l "adblock" /opt/spotify/Apps/xpui/index.html
# Should return: /opt/spotify/Apps/xpui/index.html
```

### 3. Check Extension File
```bash
ls /opt/spotify/Apps/xpui/extensions/
# Should show: adblock.js marketplace/
```

### 4. Test in Spotify
- Play music and check for audio ads (should be none)
- Look for "Upgrade" button in top-right (should be hidden)
- Check for premium promotional banners (should be gone)

---

## Configuration Details

### File Locations

**Spicetify Config:**
- Main config: `~/.config/spicetify/config-xpui.ini`
- Extensions: `~/.config/spicetify/Extensions/`
- Themes: `~/.config/spicetify/Themes/`
- Custom Apps: `~/.config/spicetify/CustomApps/`

**Spotify Installation:**
- Binary: `/opt/spotify/spotify`
- Apps: `/opt/spotify/Apps/`
- Modified xpui: `/opt/spotify/Apps/xpui/` (folder, not .spa)

### Current Configuration

```ini
[Setting]
spotify_path           = /opt/spotify/
prefs_path             = /home/coops/.config/spotify/prefs
current_theme          = Comfy
inject_css             = 1

[Preprocesses]
expose_apis        = 1
disable_sentry     = 1
disable_ui_logging = 1

[AdditionalOptions]
extensions            = adblock.js
custom_apps           = marketplace
```

---

## How Adblock Works

The adblock.js extension blocks ads by:

1. **Disabling Ad Managers:** Disables audio, billboard, leaderboard, and sponsored playlist ads
2. **Clearing Ad Slots:** Removes all ad slot subscriptions
3. **Blocking Ad Endpoints:** Redirects ad server requests to `http://localhost/no/thanks`
4. **Spoofing Premium Status:** Sets product state to `"premium"`
5. **Hiding UI Elements:** CSS rules hide upgrade buttons and promotional banners
6. **Experimental Features:** Enables flags like `hideUpgradeCTA` and disables `enableInAppMessaging`

---

## Troubleshooting

### If Ads Still Appear After Fix

**Clear Spotify Cache:**
```bash
rm -rf ~/.cache/spotify/*
pkill spotify
spotify &
```

**Check for Console Errors:**
1. Open Spotify
2. Press `Ctrl+Shift+I` to open DevTools
3. Check Console tab for "adblockify" errors

**Verify Spicetify is Applied:**
```bash
# Check if xpui is extracted (not archived)
file /opt/spotify/Apps/xpui
# Should say: directory

# Check index.html for Spicetify markers
grep "Spicetify.Config" /opt/spotify/Apps/xpui/index.html
# Should show Spicetify configuration
```

### After Spotify Updates

When Spotify updates, you'll need to reapply Spicetify:

```bash
# Backup and apply Spicetify to new version
spicetify backup apply

# Or if permissions needed
sudo chmod a+wr /opt/spotify/Apps -R
spicetify backup apply

# Restart Spotify
pkill spotify
spotify &
```

---

## Related Files in Dusky Repo

The Spicetify configuration is tracked in the dusky repo at:
```
~/git/dusky/.config/spicetify/
├── config-xpui.ini          # Main configuration
├── Extensions/
│   └── adblock.js           # Adblock extension
├── Themes/
│   └── Comfy/               # Comfy theme
└── CustomApps/
    └── marketplace/         # Marketplace app
```

---

## Key Learnings

1. **Always verify paths** - Use `which spotify` and `pacman -Ql spotify` to find actual installation location
2. **Check extraction** - Spicetify must extract `.spa` files to folders to work
3. **Permissions matter** - System-installed apps need proper write permissions
4. **Verify after updates** - Spotify updates may require reapplying Spicetify

---

## References

- Spicetify Version: 2.42.10
- Spotify Version: 1.2.79.427.g80eb4a07
- Adblock Extension: adblockify (injected via spicetify-marketplace)
- Theme: Comfy

---

**Last Updated:** 2026-02-16
**Tested On:** Arch Linux (CachyOS), Spotify 1.2.79
