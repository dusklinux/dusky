#!/usr/bin/env python3
import os
import sys
import subprocess
import shutil
import getpass
import re
import grp
import signal

# ==============================================================================
# STRICT RUNTIME ENFORCEMENT
# ==============================================================================
if sys.version_info < (3, 14):
    sys.exit("Fatal: This daemon requires Python 3.14+ (Arch Linux rolling).")

# ==============================================================================
# CONFIGURATION
# ==============================================================================
FINGERS: str = "3"
SENSITIVITY: float = 0.003  # Lower = slower glide.
MAX_VOLUME: float = 1.5     # 150% maximum volume

def bootstrap_environment():
    """Validates Wayland dependencies and kernel group permissions."""
    print("Checking system dependencies...")
    needs_relogin = False
    user = getpass.getuser()

    # 1. Dependency Check
    if not shutil.which('libinput'):
        print("Missing 'libinput-tools'. Invoking pacman...")
        try:
            subprocess.run(
                ['sudo', 'pacman', '-S', '--needed', '--noconfirm', 'libinput', 'libinput-tools'],
                check=True
            )
        except subprocess.CalledProcessError:
            sys.exit("Fatal: pacman failed to install libinput-tools.")

    # 2. Kernel Group Validation
    try:
        if 'input' not in subprocess.check_output(['groups', user], text=True):
            print(f"Adding {user} to the 'input' group...")
            subprocess.run(['sudo', 'usermod', '-aG', 'input', user], check=True)
            needs_relogin = True
    except subprocess.CalledProcessError:
        sys.exit("Fatal: Failed to verify or modify group permissions.")

    # 3. Active Session Check
    try:
        if grp.getgrnam('input').gr_gid not in os.getgroups() and not needs_relogin:
            needs_relogin = True
    except KeyError:
        pass 

    if needs_relogin:
        sys.exit(
            "\n🛑 KERNEL PERMISSION REQUIREMENT 🛑\n"
            "You were added to the 'input' group. Linux requires a fresh session.\n"
            "LOG OUT of Hyprland and LOG BACK IN to apply hardware access."
        )

    # 4. Dynamic Import Validation
    try:
        import pulsectl
        return pulsectl
    except ImportError:
        sys.exit(
            "\nFatal: Missing 'pulsectl' Python module.\n"
            "Install via AUR: yay -S python-pulsectl"
        )

def main():
    pulsectl = bootstrap_environment()

    # Initialize PipeWire connection
    pulse = pulsectl.Pulse('libinput-volume-glide')
    
    # Graceful exit handler for systemd/uwsm stops
    def handle_sigterm(*args):
        print("\nCaught SIGTERM. Closing PipeWire connection cleanly...")
        pulse.close()
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm) # Handle Ctrl+C identically

    try:
        default_sink_name = pulse.server_info().default_sink_name
        sink = next(s for s in pulse.sink_list() if s.name == default_sink_name)
    except Exception as e:
        sys.exit(f"Fatal: PipeWire sink connection failed: {e}")

    print(f"Tracking {FINGERS}-finger vertical swipe events on: {sink.description}")

    update_pattern = re.compile(rf"GESTURE_SWIPE_UPDATE.*?\s+{FINGERS}\s+([-\d\.]+)/([-\d\.]+)")
    cmd = ['stdbuf', '-oL', 'libinput', 'debug-events']

    # Modern Context Manager: Guarantees libinput subprocess is killed if Python crashes
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=sys.stderr, text=True) as process:
        # High-performance event loop leveraging Python 3.8+ Walrus Operator (:=)
        # We process stdout dynamically as libinput streams it.
        for line in process.stdout:
            if match := update_pattern.search(line):
                try:
                    dy = float(match.group(2))
                except ValueError:
                    continue
                
                volume_delta = -(dy * SENSITIVITY)
                
                # Fetch state dynamically to account for external volume changes
                current_sink = pulse.sink_info(sink.index)
                current_vol = pulse.volume_get_all_chans(current_sink)
                
                # Clamp volume between 0 and MAX_VOLUME
                new_vol = max(0.0, min(MAX_VOLUME, current_vol + volume_delta))
                pulse.volume_set_all_chans(current_sink, new_vol)

if __name__ == '__main__':
    main()
