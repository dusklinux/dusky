# iOS RAM Optimizer - Complete Guide

## What Is This?

These scripts help you save RAM on your jailbroken iPhone by disabling unnecessary background processes (daemons).

**processkiller** = Auto-kills resource-heavy processes that keep restarting (wallpapers, Spotlight, etc.)

**daemonmanager** = Lets you disable/enable iOS daemons with full tracking and undo capability

---

## daemonmanager (v8.2) - Complete Command Reference

### Global Flags (work with any command)

| Flag | Short | Description |
|------|-------|-------------|
| `--step` | `-s` | Pause after each daemon, press Enter to continue |
| `--config <path>` | `-c <path>` | Use custom config file path |

### All Commands

```bash
# APPLY FROM CONFIG FILE
/var/jb/basebin/daemonmanager apply                       # Apply daemon.cfg (silent)
/var/jb/basebin/daemonmanager --step apply               # Step through daemon.cfg one by one
/var/jb/basebin/daemonmanager -s apply                  # Same as above (short flag)
/var/jb/basebin/daemonmanager --config /path/to/file.cfg apply  # Use custom config file
/var/jb/basebin/daemonmanager -c /path/to/file.cfg apply      # Same as above (short flag)
# Aliases: apply-file, sync (same as apply)

# SINGLE DAEMON
/var/jb/basebin/daemonmanager apply tipsd yes           # Disable tipsd
/var/jb/basebin/daemonmanager apply tipsd no            # Enable tipsd
/var/jb/basebin/daemonmanager --step apply tipsd yes     # Step through disable

# DISABLE (quick command)
/var/jb/basebin/daemonmanager disable <name>             # Disable single daemon
/var/jb/basebin/daemonmanager disable tipsd gamed       # Disable multiple daemons
/var/jb/basebin/daemonmanager --step disable tipsd gamed # Step through disabling

# ENABLE (quick command)
/var/jb/basebin/daemonmanager enable <name>             # Enable single daemon
/var/jb/basebin/daemonmanager enable tipsd gamed         # Enable multiple daemons
/var/jb/basebin/daemonmanager --step enable tipsd gamed  # Step through enabling

# RESET (undo everything)
/var/jb/basebin/daemonmanager reset                     # Re-enable all disabled daemons
/var/jb/basebin/daemonmanager --step reset              # Step through reset one by one
# Alias: undo (same as reset)

# VIEW STATUS
/var/jb/basebin/daemonmanager list                     # List all disabled daemons

# HELP
/var/jb/basebin/daemonmanager help                     # Show this help
```

### Command Examples

```bash
# Example 1: Apply your full config file, stepping through each daemon
/var/jb/basebin/daemonmanager --step apply

# Example 2: Use a specific config file
/var/jb/basebin/daemonmanager --config /var/mobile/mydaemons.cfg apply

# Example 3: Disable just one daemon
/var/jb/basebin/daemonmanager disable tipsd

# Example 4: Disable multiple daemons at once
/var/jb/basebin/daemonmanager disable tipsd gamed itunesstored bookassetd

# Example 5: Disable multiple with step mode (ask before each)
/var/jb/basebin/daemonmanager --step disable tipsd gamed itunesstored

# Example 6: Enable specific daemon
/var/jb/basebin/daemonmanager enable gamed

# Example 7: Enable multiple daemons
/var/jb/basebin/daemonmanager enable gamed tipsd itunesstored

# Example 8: Reset everything back to original state
/var/jb/basebin/daemonmanager reset

# Example 9: Step through reset (goes through each one)
/var/jb/basebin/daemonmanager --step reset

# Example 10: See what's currently disabled
/var/jb/basebin/daemonmanager list

# Example 11: Disable/enable using apply command
/var/jb/basebin/daemonmanager apply tipsd yes     # disable
/var/jb/basebin/daemonmanager apply tipsd no      # enable
```

---

## daemon.cfg Config File Format

Place this file next to daemonmanager or in /var/mobile/

```
# Format: <daemon-name> <action>
# Lines starting with # are comments

# "yes" = disable this daemon
com.apple.tipsd yes
com.apple.gamed yes

# "no" = keep enabled (ignore this daemon)
com.apple.itunesstored no

# Other accepted values for disable:
#   yes, off, disable, disabled
# Other accepted values for enable:
#   no, on, enable, enabled
```

### Config File Search Order

1. `--config <path>` flag if provided
2. `./daemon.cfg` in current directory
3. `daemon.cfg` next to the daemonmanager script

---

## processkiller Commands

```bash
/var/jb/basebin/processkiller start      # Start the killer loop
/var/jb/basebin/processkiller stop       # Stop the killer loop
/var/jb/basebin/processkiller status     # Check if running
/var/jb/basebin/processkiller list       # List processes being killed
/var/jb/basebin/processkiller install    # Auto-start on boot (IMPORTANT!)
/var/jb/basebin/processkiller uninstall  # Remove auto-start
```

---

## Complete Fresh Install Instructions

### Step 1: Install Dependencies (via Cydia/Sileo)

1. **Python 3.14+** - Required for daemonmanager
2. **gawk** - Required for processkiller

### Step 2: Copy Files to Device

```bash
# From your computer, copy all files:
scp processkiller root@YOUR_IP:/var/jb/basebin/
scp daemonmanager root@YOUR_IP:/var/jb/basebin/
scp daemons.cfg root@YOUR_IP:/var/mobile/daemon.cfg
```

### Step 3: Set Permissions

```bash
# SSH into your device:
ssh root@YOUR_IP

# Make scripts executable:
chmod +x /var/jb/basebin/processkiller
chmod +x /var/jb/basebin/daemonmanager

# Set config file permissions:
chmod 644 /var/mobile/daemon.cfg
chown mobile:mobile /var/mobile/daemon.cfg
```

### Step 4: Enable Auto-Start for processkiller

```bash
/var/jb/basebin/processkiller install
```

### Step 5: Apply Daemon Settings

```bash
# Option A: Apply everything (silent, fast)
/var/jb/basebin/daemonmanager apply

# Option B: Step through each one (recommended for testing)
/var/jb/basebin/daemonmanager --step apply
```

### Step 6: If Something Goes Wrong

```bash
# Reset everything back to original state
/var/jb/basebin/daemonmanager reset
```

---

## File Locations

| File | Purpose | Location |
|------|---------|----------|
| daemonmanager | Main script | /var/jb/basebin/ |
| daemon.cfg | Config file | /var/mobile/ or /var/jb/basebin/ |
| processkiller | Process killer | /var/jb/basebin/ |
| com.daemonmanager.state.tsv | Tracking file (auto-created) | /var/mobile/Library/Preferences/ |
| com.test.processkiller.plist | Auto-start (auto-created) | /var/jb/Library/LaunchAgents/ |

---

## Tips & Notes

- **--step mode**: After each daemon, you'll be asked "Continue to next? [Y/n]". Press Enter for yes, 'n' for no.
- **No respring needed**: Daemons start/stop immediately
- **Safe to test**: Use `reset` to undo ALL changes
- **Scoped undo**: Only undoes what THIS script changed, not other tools

---

## Tested On

- iPhone 8 Plus (iOS 16.7)
- Dopamine rootless jailbreak
- Python 3.14.3
- gawk installed
