#!/usr/bin/env python3
# =============================================================================
# Target: Arch Linux (Bleeding Edge), Hyprland, Python 3
# Description: Set the wallpaper reliably under Hyprland using awww.
# =============================================================================

import os
import sys
import glob
import stat
import time
import subprocess
import shutil
import re

def log_info(msg):
    print(f"\033[1;34m[INFO]\033[0m {msg}", file=sys.stdout, flush=True)

def log_warn(msg):
    print(f"\033[1;33m[WARN]\033[0m {msg}", file=sys.stdout, flush=True)

def log_error(msg):
    print(f"\033[1;31m[ERROR]\033[0m {msg}", file=sys.stdout, flush=True)

def run_cmd(args, env=None, timeout=5):
    try:
        res = subprocess.run(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout expired"
    except Exception as e:
        return -1, "", str(e)

def setup_environment():
    uid = os.getuid()
    if "XDG_RUNTIME_DIR" not in os.environ:
        os.environ["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
    
    runtime_dir = os.environ["XDG_RUNTIME_DIR"]
    
    # 1. Detect WAYLAND_DISPLAY
    if "WAYLAND_DISPLAY" not in os.environ:
        sockets = glob.glob(os.path.join(runtime_dir, "wayland-*"))
        detected_display = None
        for s in sockets:
            name = os.path.basename(s)
            # Only match wayland-<digits>, avoiding sockets like wayland-1-awww-daemon.sock
            if re.match(r"^wayland-\d+$", name):
                try:
                    if stat.S_ISSOCK(os.stat(s).st_mode):
                        detected_display = name
                        break
                except Exception:
                    continue
        if detected_display:
            os.environ["WAYLAND_DISPLAY"] = detected_display
            log_info(f"Detected WAYLAND_DISPLAY: {detected_display}")
        else:
            log_warn("No Wayland socket detected in runtime directory.")
    
    # 2. Detect HYPRLAND_INSTANCE_SIGNATURE
    if "HYPRLAND_INSTANCE_SIGNATURE" not in os.environ:
        hypr_dir = os.path.join(runtime_dir, "hypr")
        if os.path.isdir(hypr_dir):
            subdirs = []
            for entry in os.listdir(hypr_dir):
                full_path = os.path.join(hypr_dir, entry)
                if os.path.isdir(full_path):
                    try:
                        mtime = os.path.getmtime(full_path)
                        subdirs.append((mtime, entry))
                    except Exception:
                        continue
            if subdirs:
                subdirs.sort(key=lambda x: x[0], reverse=True)
                latest_sig = subdirs[0][1]
                os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = latest_sig
                log_info(f"Detected HYPRLAND_INSTANCE_SIGNATURE: {latest_sig}")
            else:
                log_warn("No Hyprland instance directories found.")
        else:
            log_warn("Hyprland runtime directory not found.")

def is_hyprland_running():
    code, _, _ = run_cmd(["pgrep", "-x", "Hyprland"])
    return code == 0

def kill_daemon():
    log_info("Stopping any running awww-daemon instance...")
    run_cmd(["pkill", "-u", str(os.getuid()), "-x", "awww-daemon"])
    time.sleep(0.5)

def start_daemon():
    log_info("Starting awww-daemon...")
    try:
        subprocess.Popen(
            ["awww-daemon"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=os.environ
        )
    except Exception as e:
        log_error(f"Failed to start awww-daemon: {e}")
        return False
    return True

def wait_for_daemon(timeout=5):
    start_time = time.time()
    while time.time() - start_time < timeout:
        code, _, _ = run_cmd(["awww", "query"], env=os.environ, timeout=2)
        if code == 0:
            return True
        time.sleep(0.5)
    return False

def try_set_wallpaper(wallpaper_path):
    for attempt in range(1, 4):
        log_info(f"Wallpaper application attempt {attempt}/3...")
        
        # Check if awww-daemon is responding
        code, _, _ = run_cmd(["awww", "query"], env=os.environ, timeout=2)
        daemon_ok = (code == 0)
        
        if not daemon_ok:
            log_warn("awww-daemon is not responsive or not running. Attempting to start/restart...")
            kill_daemon()
            if not start_daemon():
                log_warn("Failed to launch awww-daemon. Retrying next attempt...")
                time.sleep(1)
                continue
            
            log_info("Waiting for awww-daemon to initialize...")
            if not wait_for_daemon(5):
                log_warn("awww-daemon failed to initialize in time. Retrying next attempt...")
                continue
            log_info("awww-daemon initialized successfully.")
        
        # Now set the wallpaper
        code, out, err = run_cmd(["awww", "img", wallpaper_path], env=os.environ, timeout=5)
        if code == 0:
            log_info("Wallpaper applied successfully!")
            return True
        else:
            log_warn(f"Failed to apply wallpaper: {err} {out} (code: {code})")
            kill_daemon()
            time.sleep(1)
            
    return False

def main():
    wallpaper_path = os.path.expanduser("~/Pictures/wallpapers/dusk_default.jpg")
    if not os.path.isfile(wallpaper_path):
        log_error(f"Wallpaper not found at {wallpaper_path}. Cannot proceed.")
        sys.exit(0)
        
    for tool in ["awww", "awww-daemon", "pgrep"]:
        if shutil.which(tool) is None:
            log_error(f"Missing required tool: {tool}. Skipping setup.")
            sys.exit(0)
            
    if not is_hyprland_running():
        log_warn("Hyprland is not running. Skipping wallpaper setup.")
        sys.exit(0)
        
    setup_environment()
    
    success = try_set_wallpaper(wallpaper_path)
    if success:
        log_info("Wallpaper configuration complete.")
    else:
        log_error("Failed to set wallpaper after 3 attempts. Moving on to protect orchestrator.")
        
    sys.exit(0)

if __name__ == "__main__":
    main()
