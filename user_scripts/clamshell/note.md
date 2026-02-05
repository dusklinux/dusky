
When connecting an external monitor to a laptop, automatically enable clamshell mode similar to MacBooks:

- Turn off laptop screen
- External monitor becomes primary display
- Switch to performance power profile

This should work when connecting:
- Just a monitor
- Full dock setup (monitor + keyboard + mouse)

**My current setup:**

I have a rudimentary setup that works, used it for a couple of months on a different system but I think it's a very bad way to do it. Here's what I'm currently using:

**1. Power Profile Script** (`/usr/local/bin/power-profile-auto.sh`):
```bash
#!/usr/bin/env bash
AC_PATH="/sys/class/power_supply/AC0/online"

set_profile() {
    case "$1" in
        "1") powerprofilesctl set performance ;;
        "0") powerprofilesctl set power-saver ;;
    esac
}

if [[ -f "$AC_PATH" ]]; then
    AC_STATE=$(cat "$AC_PATH")
    set_profile "$AC_STATE"
fi
```

**Commands:**
```bash
sudo nvim /usr/local/bin/power-profile-auto.sh
sudo chmod +x /usr/local/bin/power-profile-auto.sh
```

**Service file** (`/etc/systemd/system/power-profile-auto.service`):
```ini
[Unit]
Description=Set power profile based on AC state at boot
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/power-profile-auto.sh

[Install]
WantedBy=multi-user.target
```

**Commands:**
```bash
sudo nvim /etc/systemd/system/power-profile-auto.service
sudo systemctl enable power-profile-auto.service
```

---

**2. Clamshell Mode Script** (`~/scripts/clamshell-mode.sh`):
```bash
#!/bin/bash
INTERNAL="eDP-1"
EXTERNAL="DP-1"

lid_state=$(awk '{print $2}' /proc/acpi/button/lid/LID/state)

if [[ "$lid_state" == "closed" ]]; then
    hyprctl keyword monitor "$INTERNAL,disable"
    if hyprctl monitors | grep -q "$EXTERNAL"; then
        hyprctl keyword monitor "$EXTERNAL,preferred,auto,1"
    fi
else
    hyprctl keyword monitor "$INTERNAL,preferred,auto,1.6"
fi
```

**Commands:**
```bash
nvim ~/scripts/clamshell-mode.sh
chmod +x ~/scripts/clamshell-mode.sh
```

---

**3. Polling Service** (`~/.config/systemd/user/clamshell.service`):
```ini
[Unit]
Description=Clamshell mode monitor

[Service]
Type=simple
ExecStart=/bin/bash -c 'while true; do /home/nextprogram/scripts/clamshell-mode.sh; sleep 2; done'
Restart=always

[Install]
WantedBy=default.target
```

**Commands:**
```bash
nvim ~/.config/systemd/user/clamshell.service
systemctl --user enable --now clamshell.service
```

**Add to Hyprland config** (`~/.config/hypr/autostart.conf`):
```
exec-once = systemctl --user start clamshell.service
```