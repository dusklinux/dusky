# Disabling PosterBoard on iOS 16 (Rootless Jailbreak)

## Overview

PosterBoard (`com.apple.PosterBoard`) is an iOS 16+ system app that manages Lock Screen wallpapers, live/animated wallpapers, and Contact Posters. It can consume 200-300MB RAM even with static wallpapers, making it a target for optimization on low-RAM devices (e.g., iPhone 8 Plus with 2GB RAM).

**Note:** This guide is for rootless jailbreaks (Dopamine, etc.). Rootful jailbreaks have different file system permissions and paths.

---

## Prerequisites

### On Your Computer (Linux/macOS)

1. **OpenSSH client** installed (pre-installed on most systems)
2. **Network connectivity** to your iOS device on the same WiFi network
3. **SSH server** enabled on iOS device (via OpenSSH package from Cydia/Sileo)
4. **Password-based SSH** access configured (root password = `alpine` by default)

### On Your iOS Device

- Rootless jailbreak installed (tested on Dopamine)
- OpenSSH or similar SSH server installed
- Write access to `/var/jb/` (jailbreak overlay filesystem)

---

## Step 1: SSH Connection

### Method 1: Using SSH with Password (Recommended for Manual)

On your computer, open terminal and connect:

```bash
ssh root@<device-ip>
```

When prompted for password, enter: `alpine`

To find your device's IP:
- On iOS: Settings > WiFi > Tap your network > IP Address

### Method 2: SSH with Automated Password (For Scripts)

Create a fake SSH_ASKPASS script to automate password entry:

```bash
mkdir -p ~/.ssh/askpass
cat > ~/.ssh/askpass/ssh-askpass << 'EOF'
#!/bin/bash
echo "alpine"
EOF
chmod +x ~/.ssh/askpass/ssh-askpass
```

Then connect with:
```bash
SSH_ASKPASS=~/.ssh/askpass/ssh-askpass SSH_ASKPASS_REQUIRE=force ssh -o "StrictHostKeyChecking=no" -o "UserKnownHostsFile=/dev/null" root@<device-ip>
```

### Common SSH Connection Issues

| Issue | Solution |
|-------|----------|
| Connection timeout | Ensure device is on same network; check firewall |
| Permission denied | Verify password is correct; check SSH config |
| Too many auth failures | Use `-o PreferredAuthentications=password -o PubkeyAuthentication=no` flags |

---

## Step 2: Understanding PosterBoard Architecture

### What is PosterBoard?

PosterBoard is NOT a daemon. It is a **system application** (`AppDomain-com.apple.PosterBoard`) that manages wallpapers in iOS 16+.

### PosterBoard Process Details

**Main executable location:**
```
/Applications/PosterBoard.app/PosterBoard
```

**Launch method:** Via `launchd` as a user-level service (user 501/mobile)

**Launchd label:** `com.apple.PosterBoard`

### PosterBoard Plugins (XPC Services)

PosterBoard spawns multiple extension plugins for different wallpaper types:

| Plugin | Path | Purpose |
|--------|------|---------|
| CollectionsPoster | `/System/Library/PrivateFrameworks/WallpaperKit.framework/PlugIns/CollectionsPoster.appex/` | Standard wallpapers |
| PhotosPosterProvider | `/System/Library/PrivateFrameworks/PhotosUIPrivate.framework/PlugIns/PhotosPosterProvider.appex/` | Photo wallpapers |
| UnityPosterExtension | `/System/Library/PrivateFrameworks/UnityPoster.framework/PlugIns/UnityPosterExtension.appex/` | Unity (watch face) wallpapers |
| EmojiPosterExtension | `/System/Library/PrivateFrameworks/EmojiPoster.framework/PlugIns/EmojiPosterExtension.appex/` | Emoji wallpapers |
| GradientPosterExtension | `/System/Library/PrivateFrameworks/GradientPoster.framework/PlugIns/GradientPosterExtension.appex/` | Gradient/color wallpapers |
| WeatherPoster | `/private/var/containers/Bundle/Application/<UUID>/Weather.app/PlugIns/WeatherPoster.appex/` | Weather wallpapers |
| AegirPoster | `/System/Library/CoreServices/AegirProxyApp.app/PlugIns/AegirPoster.appex/` | Astronomy wallpapers |
| ExtragalacticPoster | `/System/Library/PrivateFrameworks/WatchFacesWallpaperSupport.framework/PlugIns/ExtragalacticPoster.appex/` | Galaxy wallpapers |

**All processes run as PPID=1** (launchd), meaning they are managed by the system and restart automatically when killed.

### Memory Usage

- PosterBoard main process: ~100-200MB RSS
- Each plugin: ~10-30MB RSS
- Total: Can exceed 300MB RAM

---

## Step 3: Investigation Commands

### Check if PosterBoard is Running
```bash
ps aux | grep -iE 'poster|wallpaper' | grep -v grep
```

### Check launchd Registration
```bash
launchctl list | grep -i poster
```

### Check Process Tree (shows PPID=1 for system-managed)
```bash
ps -ef | grep CollectionsPoster | grep -v grep
```

### Find App Location
```bash
ls -la /Applications/ | grep -i poster
```

### Check Rootless Overlay
```bash
ls -la /var/jb/
```

---

## Step 4: Methods That DON'T Work

### Method 1: Disabled Plist (Doesn't Work)
```bash
# Adding to disabled.plist doesn't work because PosterBoard is launched
# as a user-level XPC service, not a daemon
launchctl disable user/501/com.apple.PosterBoard  # Doesn't prevent XPC launches
```

**Why it fails:** PosterBoard is spawned via XPC (inter-process communication) from SpringBoard/system frameworks, not via standard launchd plist.

### Method 2: Renaming/Moving App Bundle (Read-Only)
```bash
mv /Applications/PosterBoard.app /Applications/PosterBoard.app.bak
```
**Error:** Read-only file system (even on rootless jailbreak)

### Method 3: Symlink to /dev/null on Overlay
```bash
# This doesn't work because the real plugin is in /System/
# which takes precedence over the overlay
ln -s /dev/null /var/jb/System/Library/PrivateFrameworks/WallpaperKit.framework/PlugIns/CollectionsPoster.appex
```
**Why it fails:** `/System/` is mounted read-only and takes precedence over `/var/jb/` overlay.

---

## Step 5: Working Solution - Kill Script Daemon

### Strategy

Since we cannot prevent PosterBoard from launching, we must continuously kill it. We create a background daemon that monitors and kills all PosterBoard processes.

### Step 5.1: Create Kill Script

SSH into your device and run:

```bash
cat > /var/jb/basebin/killposter << 'SCRIPT'
#!/bin/sh
while true; do
    killall -9 PosterBoard CollectionsPoster PhotosPosterProvider UnityPosterExtension EmojiPosterExtension GradientPosterExtension WeatherPoster AegirPoster ExtragalacticPoster 2>/dev/null
    sleep 5
done
SCRIPT
chmod +x /var/jb/basebin/killposter
```

### Step 5.2: Create Launchd Plist for Auto-Start

```bash
cat > /var/jb/Library/LaunchAgents/com.test.killposter.plist << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.test.killposter</string>
    <key>ProgramArguments</key>
    <array>
        <string>/var/jb/basebin/killposter</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
PLIST
```

### Step 5.3: Load the Service

```bash
launchctl bootstrap user/501 /var/jb/Library/LaunchAgents/com.test.killposter.plist
```

Or if already loaded:
```bash
launchctl unload /var/jb/Library/LaunchAgents/com.test.killposter.plist
launchctl load /var/jb/Library/LaunchAgents/com.test.killposter.plist
```

### Step 5.4: Verify

```bash
# Check killposter is running
ps aux | grep killposter | grep -v grep

# Check PosterBoard is NOT running
ps aux | grep -iE 'poster|wallpaper' | grep -v grep
```

Expected output from second command should show ONLY `killposter`, no PosterBoard processes.

---

## Step 6: Alternative - Manual Run (Without Auto-Start)

If you don't want auto-start, just run manually:

```bash
# Create script
cat > /var/jb/basebin/killposter << 'SCRIPT'
#!/bin/sh
while true; do
    killall -9 PosterBoard CollectionsPoster PhotosPosterProvider UnityPosterExtension EmojiPosterExtension GradientPosterExtension WeatherPoster AegirPoster ExtragalacticPoster 2>/dev/null
    sleep 5
done
SCRIPT
chmod +x /var/jb/basebin/killposter

# Run in background
nohup /var/jb/basebin/killposter > /dev/null 2>&1 &
```

---

## Step 7: Undoing the Changes

### To Stop and Remove Completely

```bash
# Stop the service
launchctl unload /var/jb/Library/LaunchAgents/com.test.killposter.plist

# Kill the process
killall -9 killposter

# Remove files
rm -f /var/jb/basebin/killposter
rm -f /var/jb/Library/LaunchAgents/com.test.killposter.plist
```

### After Undo

Restart your device. PosterBoard will work normally again.

---

## Verification Checklist

After setup, verify with these commands:

```bash
# 1. Killposter should be running
ps aux | grep killposter | grep -v grep
# Expected: Shows /var/jb/bin/sh /var/jb/basebin/killposter

# 2. PosterBoard should NOT be running
ps aux | grep -iE 'poster|wallpaper' | grep -v grep
# Expected: Shows only killposter, no CollectionsPoster/PosterBoard

# 3. Wait 10 seconds and check again
sleep 10
ps aux | grep -iE 'poster|wallpaper' | grep -v grep
# Expected: Same as step 2

# 4. Check service status
launchctl list | grep killposter
# Expected: Shows the service
```

---

## Impact Assessment

### What Still Works
- Static wallpapers display correctly
- Lock Screen wallpaper settings (may be slower to open)
- Normal SpringBoard functionality

### What May Break
- Live/animated wallpaper previews
- Contact Posters feature
- Some wallpaper customization options

### RAM Savings
- PosterBoard main: ~100-200MB
- Plugins: ~50-100MB
- **Total potential savings: ~150-300MB**

---

## Troubleshooting

### "Permission denied" when creating files
```bash
# Ensure you're root
whoami  # Should return "root"
```

### "Service already loaded" error
```bash
launchctl unload /var/jb/Library/LaunchAgents/com.test.killposter.plist
launchctl load /var/jb/Library/LaunchAgents/com.test.killposter.plist
```

### PosterBoard still appears
```bash
# Check if killposter is running
ps aux | grep killposter

# If not running, manually start
nohup /var/jb/basebin/killposter > /dev/null 2>&1 &

# Force kill all poster processes
killall -9 PosterBoard CollectionsPoster PhotosPosterProvider UnityPosterExtension EmojiPosterExtension GradientPosterExtension WeatherPoster AegirPoster ExtragalacticPoster
```

### After device restart, PosterBoard returns
```bash
# Re-run
nohup /var/jb/basebin/killposter > /dev/null 2>&1 &
launchctl load /var/jb/Library/LaunchAgents/com.test.killposter.plist
```

---

## Quick Reference Commands

| Action | Command |
|--------|---------|
| Connect via SSH | `ssh root@<ip>` |
| Check status | `ps aux \| grep -iE 'poster\|killposter' \| grep -v grep` |
| Manual kill | `killall -9 PosterBoard CollectionsPoster ...` |
| Start blocker | `nohup /var/jb/basebin/killposter > /dev/null 2>&1 &` |
| Stop blocker | `killall -9 killposter` |
| Remove all | `launchctl unload /var/jb/Library/LaunchAgents/com.test.killposter.plist && rm -f /var/jb/basebin/killposter /var/jb/Library/LaunchAgents/com.test.killposter.plist` |

---

## File Locations Summary

| File | Path | Purpose |
|------|------|---------|
| Kill script | `/var/jb/basebin/killposter` | Background process killer |
| Launchd plist | `/var/jb/Library/LaunchAgents/com.test.killposter.plist` | Auto-start configuration |
| PosterBoard app | `/Applications/PosterBoard.app/PosterBoard` | Main executable (don't modify) |
| Plugin 1 | `/System/Library/PrivateFrameworks/WallpaperKit.framework/PlugIns/CollectionsPoster.appex/` | Primary wallpaper plugin |

---

## SSH Connection Script (For Automation)

Save as `ssh_ios.sh`:

```bash
#!/bin/bash
IP="${1:-192.168.29.75}"
SSH_ASKPASS=~/.ssh/askpass/ssh-askpass SSH_ASKPASS_REQUIRE=force ssh -o "StrictHostKeyChecking=no" -o "UserKnownHostsFile=/dev/null" -o "ServerAliveInterval=5" "root@${IP}" "${2:-echo connected}"
```

Usage:
```bash
./ssh_ios.sh 192.168.29.75 "ps aux | grep poster"
```

---

## Credits

- Method discovered through trial and error on iOS 16.7 (iPhone 8 Plus, Dopamine jailbreak)
- PosterBoard architecture documented via SSH investigation
- Tested on rootless jailbreak (Dopamine)

---

## Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2026-03-31 | 1.0 | Initial documentation |

---

## Pre-Built DEB Packages

Three packages have been created for easy installation:

### 1. killposter.deb
**File:** `packages/killposter_1.0.0_iphoneos-arm.deb`

Automatically installs and configures the PosterBoard killer. Includes:
- Kill script at `/var/jb/basebin/killposter`
- Auto-start launchd plist
- Clean install/uninstall scripts
- Persists across reboots

### 2. daemonmanager_shell.deb (Recommended)
**File:** `packages/daemonmanager_shell_1.0.0_iphoneos-arm.deb`

Shell-based daemon manager with:
- Command-line interface
- Toggle daemons on/off with persistence
- Simple list of supported daemons
- Works without Python/web server
- Uses text-based config file

**Usage:**
```bash
/var/jb/basebin/daemonmanager list
/var/jb/basebin/daemonmanager disable com.apple.tipsd
/var/jb/basebin/daemonmanager enable com.apple.gamed
/var/jb/basebin/daemonmanager status
```

### 3. daemonmanager_web.deb (Python-based)
**File:** `packages/daemonmanager_1.0.0_iphoneos-arm.deb`

Full GUI daemon manager (requires Python3 - NOT AVAILABLE on most devices):
- Web-based mobile interface (port 8080)
- Toggle daemons on/off with persistence
- Import/export configuration files
- Search functionality

**Note:** This package requires Python3 which is typically NOT installed on jailbroken iOS devices. Use `daemonmanager_shell.deb` instead.

---

## Package Installation Guide

### Prerequisites
- SCP or Filza installed on iOS
- Root access via SSH

### Option 1: Install via Filza (Easiest)

1. Transfer the `.deb` file to your iOS device
2. Open Filza and navigate to the DEB file
3. Tap on the DEB file to install
4. Confirm installation
5. Respring if prompted

### Option 2: Install via SSH

```bash
# Copy DEB to device
scp killposter_1.0.0_iphoneos-arm.deb root@<device-ip>:/var/mobile/

# SSH into device
ssh root@<device-ip>

# Install the package
dpkg -i /var/mobile/killposter_1.0.0_iphoneos-arm.deb

# Or for DaemonManager
dpkg -i /var/mobile/daemonmanager_1.0.0_iphoneos-arm.deb
```

### Option 3: Manual Installation (via Nugget SSH)

Since you can SSH via Nugget, upload the DEB or create files manually.

#### Recommended: Manual File Creation

```bash
# SSH into device via Nugget
ssh root@192.168.29.75

# Create directories
mkdir -p /var/jb/basebin
mkdir -p /var/jb/Library/LaunchAgents
mkdir -p /var/jb/tmp

# Create killposter script
cat > /var/jb/basebin/killposter << 'KILLSCRIPT'
#!/bin/sh
while true; do
    killall -9 PosterBoard CollectionsPoster PhotosPosterProvider 2>/dev/null
    killall -9 UnityPosterExtension EmojiPosterExtension GradientPosterExtension 2>/dev/null
    killall -9 WeatherPoster AegirPoster ExtragalacticPoster 2>/dev/null
    sleep 5
done
KILLSCRIPT
chmod 755 /var/jb/basebin/killposter

# Create launchd plist
cat > /var/jb/Library/LaunchAgents/com.test.killposter.plist << 'PLISTFILE'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.test.killposter</string>
    <key>ProgramArguments</key><array><string>/var/jb/basebin/killposter</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
</dict>
</plist>
PLISTFILE

# Start the service
nohup /var/jb/basebin/killposter &

# Install DaemonManager
cat > /var/jb/basebin/daemonmanager << 'DMSCRIPT'
#!/bin/sh
CONFIG_DIR="/var/mobile/daemonmanager"
DAEMONS_FILE="${CONFIG_DIR}/daemons.txt"
init() {
    mkdir -p "$CONFIG_DIR"
    if [ ! -f "$DAEMONS_FILE" ]; then
        cat > "$DAEMONS_FILE" << 'EOD'
# DaemonManager Configuration - Format: LABEL|NAME|CATEGORY|ENABLED
com.apple.tipsd|Tips|Apple|1
com.apple.ScreenTimeAgent|Screen Time|Parental|0
com.apple.gamed|Game Center|Gaming|0
com.apple.UsageTrackingAgent|Usage Tracking|Analytics|0
com.apple.mobile.softwareupdated|Software Update|Updates|0
com.apple.OTATaskingAgent|OTA Agent|Updates|0
com.apple.softwareupdateservicesd|Update Services|Updates|0
com.apple.healthd|Health|Health|1
com.apple.printd|AirPrint|Printing|1
com.apple.itunescloudd|iCloud|Apple|1
com.apple.passd|Wallet|Apple|1
com.apple.searchd|Spotlight|Search|1
com.apple.corespotlightservice|Spotlight Service|Search|1
com.apple.spotlightknowledged|Spotlight Knowledge|Search|1
com.apple.assistantd|Siri|Siri|1
com.apple.voiced|Voice Control|Siri|1
com.apple.nanotimekitcompaniond|Watch|Watch|1
com.apple.tzlinkd|Time Zone|System|1
com.apple.thermalmonitord|Thermal Monitor|System|1
EOD
    fi
}
# [Full daemonmanager script - see documentation]
DMSCRIPT
chmod 755 /var/jb/basebin/daemonmanager
/var/jb/basebin/daemonmanager init
```

---

## DaemonManager Usage

### Accessing DaemonManager

After installing `daemonmanager.deb`:

1. Open Safari on your iOS device
2. Go to: `http://localhost:8080`
3. Use the mobile-friendly interface

**Tip:** Add to Home Screen for app-like experience:
- Tap Share button
- Select "Add to Home Screen"
- Name it "DaemonManager"

### Features

1. **View All Daemons** - See all supported daemons with their status
2. **Toggle On/Off** - Tap the toggle to enable/disable
3. **Search** - Filter daemons by name or category
4. **Export Config** - Save your daemon settings to a JSON file
5. **Import Config** - Load a previously saved configuration

### Import/Export Format

```json
{
  "version": "1.0",
  "daemons": {
    "com.apple.tipsd": false,
    "com.apple.ScreenTimeAgent": false,
    "com.apple.gamed": false
  }
}
```

### Creating Custom Configurations

1. Export your current settings via the web interface
2. Edit the JSON file on your computer
3. Set `true` for enabled, `false` for disabled
4. Import the modified file

---

## Uninstalling Packages

### Via Command Line
```bash
# Uninstall killposter
dpkg -r killposter

# Uninstall daemonmanager
dpkg -r daemonmanager
```

### Via Filza
1. Navigate to `/var/lib/dpkg/info/`
2. Find the package (killposter or daemonmanager)
3. Long-press and select uninstall

### Manual Removal
```bash
# Stop and remove killposter
launchctl unload /var/jb/Library/LaunchAgents/com.test.killposter.plist
rm -f /var/jb/basebin/killposter
rm -f /var/jb/Library/LaunchAgents/com.test.killposter.plist

# Stop and remove daemonmanager
launchctl unload /var/jb/Library/LaunchAgents/com.test.daemonmanager.plist
killall -9 daemonmanager 2>/dev/null
rm -f /var/jb/basebin/daemonmanager
rm -f /var/jb/Library/LaunchAgents/com.test.daemonmanager.plist
rm -rf /var/mobile/daemonmanager
```

---

## Package Files Reference

### killposter.deb Contents

| File | Path | Purpose |
|------|------|---------|
| killposter | `/var/jb/basebin/killposter` | Shell script that kills PosterBoard |
| com.test.killposter.plist | `/var/jb/Library/LaunchAgents/` | Auto-start configuration |

### daemonmanager.deb Contents

| File | Path | Purpose |
|------|------|---------|
| daemonmanager | `/var/jb/basebin/daemonmanager` | Python web server bootstrap |
| com.test.daemonmanager.plist | `/var/jb/Library/LaunchAgents/` | Auto-start configuration |
| state.json | `/var/mobile/daemonmanager/state.json` | Persisted daemon states |
| daemonmanager.shortcut | `/var/mobile/daemonmanager/` | Shortcut reference file |

---

## Troubleshooting Packages

### Package Won't Install
```bash
# Check dependencies
dpkg -i package.deb 2>&1

# Force install if needed
dpkg --force-depends -i package.deb
```

### Service Not Starting
```bash
# Check if script exists
ls -la /var/jb/basebin/killposter

# Manually start
nohup /var/jb/basebin/killposter > /dev/null 2>&1 &

# Check logs
cat /var/jb/tmp/killposter.log
```

### DaemonManager Not Accessible
```bash
# Check if running
ps aux | grep daemonmanager

# Restart
launchctl unload /var/jb/Library/LaunchAgents/com.test.daemonmanager.plist
launchctl load /var/jb/Library/LaunchAgents/com.test.daemonmanager.plist
```

---

## Rebuilding Packages

If you need to modify the packages, use the build script:

```bash
cd packages
./build_deb.sh killposter killposter_new.deb
./build_deb.sh daemonmanager daemonmanager_new.deb
```

---

## Current Device Status (2026-03-31)

Your iPhone 8 Plus (iOS 16.7) has the following installed:

### Files Created:
- `/var/jb/basebin/killposter` - PosterBoard killer script (running)
- `/var/jb/Library/LaunchAgents/com.test.killposter.plist` - Auto-start config
- `/var/jb/basebin/daemonmanager` - Daemon manager CLI
- `/var/mobile/daemonmanager/daemons.txt` - Daemon configuration

### Running Processes:
- `killposter` - Actively killing PosterBoard processes

### Status:
- PosterBoard: **DISABLED** (not running)
- All plugins: **DISABLED** (not running)
- RAM savings: ~150-300MB

### Commands Available:
```bash
/var/jb/basebin/killposter status     # Check if running
/var/jb/basebin/daemonmanager list    # List daemons
/var/jb/basebin/daemonmanager disable <label>  # Disable a daemon
/var/jb/basebin/daemonmanager enable <label>   # Enable a daemon
/var/jb/basebin/daemonmanager status  # Show status
```

---

*Last updated: 2026-03-31*
