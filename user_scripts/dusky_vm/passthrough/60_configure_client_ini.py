#!/usr/bin/env python3
import os
import sys
from pathlib import Path

def get_caller_identity():
    """Resolves the real user when running under sudo or standard shell."""
    sudo_user = os.environ.get("SUDO_USER") or os.environ.get("DOAS_USER")
    if sudo_user and sudo_user != "root":
        import pwd
        try:
            user_info = pwd.getpwnam(sudo_user)
            return Path(user_info.pw_dir), user_info.pw_uid, user_info.pw_gid
        except KeyError:
            pass
            
    # Fallback to current process owner
    return Path.home(), os.getuid(), os.getgid()

def main():
    home_dir, uid, gid = get_caller_identity()
    config_dir = home_dir / ".config" / "looking-glass"
    config_file = config_dir / "client.ini"

    # Create config directory with proper ownership
    config_dir.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        try:
            os.chown(config_dir, uid, gid)
        except Exception as e:
            print(f"Warning: Failed to set directory ownership: {e}")

    default_config = """; Looking Glass Client Configuration
; Tailored for Hyprland / Wayland / Kernel 7.x

[app]
shmFile=/dev/shm/looking-glass
allowDMA=yes
renderer=opengl

[opengl]
vsync=no
preventBuffer=yes
mipmap=yes
amdPinnedMem=yes

[wayland]
fractionScale=no
warpSupport=yes

[win]
autoResize=yes
keepAspect=yes
dontUpscale=yes
noScreensaver=yes
borderless=yes

[input]
escapeKey=64
rawMouse=yes
hideCursor=yes

[spice]
enable=yes
clipboard=yes
"""

    if not config_file.exists():
        print(f"Creating new Looking Glass client configuration at {config_file}...")
        config_file.write_text(default_config, encoding="utf-8")
        if os.geteuid() == 0:
            try:
                os.chown(config_file, uid, gid)
            except Exception as e:
                print(f"Warning: Failed to set file ownership: {e}")
        print("Configuration file created successfully with SPICE clipboard enabled.")
    else:
        print(f"Existing configuration found at {config_file}. Merging/checking SPICE configuration...")
        content = config_file.read_text(encoding="utf-8")
        
        lines = content.splitlines()
        has_spice_section = False
        has_enable = False
        has_clipboard = False
        
        current_section = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                current_section = stripped[1:-1].lower()
                if current_section == "spice":
                    has_spice_section = True
            elif current_section == "spice" and "=" in stripped:
                parts = stripped.split("=", 1)
                key = parts[0].strip().lower()
                val = parts[1].strip().lower() if len(parts) > 1 else ""
                if key == "enable" and val in ("yes", "true", "1"):
                    has_enable = True
                elif key == "clipboard" and val in ("yes", "true", "1"):
                    has_clipboard = True

        updated = False
        if not has_spice_section:
            print("Adding [spice] section to client.ini...")
            lines.append("")
            lines.append("[spice]")
            lines.append("enable=yes")
            lines.append("clipboard=yes")
            updated = True
        else:
            if not has_enable or not has_clipboard:
                print("Updating parameters in [spice] section...")
                new_lines = []
                in_spice = False
                injected_enable = False
                injected_clipboard = False
                
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("[") and stripped.endswith("]"):
                        if in_spice:
                            if not has_enable and not injected_enable:
                                new_lines.append("enable=yes")
                            if not has_clipboard and not injected_clipboard:
                                new_lines.append("clipboard=yes")
                            in_spice = False
                        if stripped[1:-1].lower() == "spice":
                            in_spice = True
                    elif in_spice and "=" in stripped:
                        parts = stripped.split("=", 1)
                        key = parts[0].strip().lower()
                        if key == "enable" and not has_enable:
                            line = "enable=yes"
                            injected_enable = True
                        elif key == "clipboard" and not has_clipboard:
                            line = "clipboard=yes"
                            injected_clipboard = True
                    new_lines.append(line)
                
                if in_spice:
                    if not has_enable and not injected_enable:
                        new_lines.append("enable=yes")
                    if not has_clipboard and not injected_clipboard:
                        new_lines.append("clipboard=yes")
                lines = new_lines
                updated = True

        if updated:
            config_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            if os.geteuid() == 0:
                try:
                    os.chown(config_file, uid, gid)
                except Exception as e:
                    print(f"Warning: Failed to set file ownership: {e}")
            print("Configuration updated successfully with SPICE clipboard settings.")
        else:
            print("SPICE clipboard settings are already correctly configured in client.ini.")

if __name__ == "__main__":
    main()
