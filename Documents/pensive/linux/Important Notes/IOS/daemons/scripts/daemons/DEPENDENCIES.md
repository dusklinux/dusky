# Dependencies for Nugget Scripts (Dopamine Jailbreak)

## Required

### Python 3.14+
- Required by: daemonmanager (plist modifications)
- Install via: Cydia/Sileo package manager
- Note: The script auto-detects Python paths on Dopamine

### gawk (GNU AWK)
- Required by: processkiller
- Install via: Cydia/Sileo (search "gawk")
- Note: iOS doesn't include awk by default

## Optional (for manual testing)

### pgrep / pkill
- Alternative to: ps + grep, killall
- Install via:adv-cmds package in Cydia
- Note: Scripts work without these, they just make manual testing easier

## Installation Order (Fresh Install)

1. Install Python 3.14+ from package manager
2. Install gawk from package manager
3. Transfer scripts to device:
   - /var/jb/basebin/processkiller
   - /var/jb/basebin/daemonmanager
4. Make executable: chmod +x /var/jb/basebin/processkiller /var/jb/basebin/daemonmanager
5. Run: processkiller install (enables auto-start after reboot)

## Verified Working On

- iOS 16.7
- Dopamine rootless jailbreak
- Python 3.14.3
- gawk installed
