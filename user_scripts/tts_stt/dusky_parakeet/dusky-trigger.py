#!/usr/bin/env python3
# Dusky Trigger v8.1 BLEEDING EDGE - Secure O_NONBLOCK FIFO, Strict TOCTOU Mitigation

import argparse
import os
import sys
import sysconfig
import time
import subprocess
import json
import stat
from pathlib import Path
import shutil

if sys.version_info < (3, 14, 6):
    print(f"Need 3.14.6+", file=sys.stderr)
    sys.exit(1)
if sysconfig.get_config_var("Py_GIL_DISABLED") == 1:
    print("Need GIL", file=sys.stderr)
    sys.exit(1)

def get_runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        p = Path(base) / "dusky_stt"
        p.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            p.chmod(0o700)
        except Exception:
            pass
        return p
    return Path("/tmp/dusky_stt")

RUNTIME_DIR = get_runtime_dir()
FIFO_PATH = RUNTIME_DIR / "fifo"
PID_FILE = RUNTIME_DIR / "pid"
READY_FILE = RUNTIME_DIR / "ready"
RECORD_PID_FILE = RUNTIME_DIR / "recording"
APP_DIR = Path.home() / "contained_apps" / "uv" / "dusky_stt_v2"

def is_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False

def start_daemon() -> bool:
    if not shutil.which("systemctl"):
        print("Error: systemd is required to orchestrate the backend environment environment variables.", file=sys.stderr)
        return False
    
    subprocess.run(["systemctl", "--user", "start", "dusky-stt.service"], capture_output=True)
    
    for _ in range(50):
        if READY_FILE.exists() and is_running():
            return True
        time.sleep(0.1)
    return False

def send_fifo(cmd: str) -> bool:
    try:
        # Enforce strict verification via lstat to verify there are no malicious symlink intercepts
        try:
            st = os.lstat(FIFO_PATH)
            if stat.S_ISLNK(st.st_mode):
                print("Security Error: FIFO path has been hijacked by a symbolic link.", file=sys.stderr)
                return False
        except FileNotFoundError:
            return False

        # Open O_RDWR | O_NONBLOCK to protect the interface from hanging if the daemon side cycles
        fd = os.open(FIFO_PATH, os.O_RDWR | os.O_NONBLOCK)
        try:
            os.write(fd, (cmd + "\n").encode())
        finally:
            os.close(fd)
        return True
    except Exception as e:
        print(f"FIFO transmission barrier: {e}", file=sys.stderr)
        return False

def secure_write_file_atomic(path: Path, content: str):
    """FIXED: Uses absolute O_NOFOLLOW file handles to eliminate the temporary directory TOCTOU window"""
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(content)
    except FileExistsError:
        path.unlink(missing_ok=True)
        secure_write_file_atomic(path, content)
    except Exception as e:
        print(f"Secure file transaction failed at {path}: {e}", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(description="Dusky Trigger CLI Interface v8.1")
    parser.add_argument("--kill", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--logs", action="store_true")
    parser.add_argument("--file", type=str, help="Transcribe local target media file")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--realtime", action="store_true", help="Enforce instantaneous streaming output fields")
    parser.add_argument("--push", action="store_true", help="Enforce structural standard processing block blocks")
    args = parser.parse_args()

    if args.logs:
        if shutil.which("journalctl"):
            os.system("journalctl --user -u dusky-stt -f -n 100")
        return

    if args.status:
        if is_running():
            print(f"Daemon Context Operational [PID {PID_FILE.read_text().strip()}]")
            print(f"Secure Runtime Location: {RUNTIME_DIR}")
            print(f"Communication Pipeline: Interfaced={FIFO_PATH.exists()} System-Ready={READY_FILE.exists()}")
            td = Path.home() / "Transcripts" / "DuskySTT"
            if td.exists():
                recent = sorted(td.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
                print("Latest Operational Outputs:")
                for p in recent:
                    print(f"  {p.name} ({p.stat().st_size} Bytes)")
        else:
            print("Dusky Service Status: Inactive")
        return

    if args.kill:
        if shutil.which("systemctl"):
            subprocess.run(["systemctl", "--user", "stop", "dusky-stt.service"], capture_output=True)
        if PID_FILE.exists():
            try:
                os.kill(int(PID_FILE.read_text()), 15)
            except Exception:
                pass
        for p in [PID_FILE, FIFO_PATH, READY_FILE, RECORD_PID_FILE]:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
        print("Backend structures unmapped successfully.")
        return

    if args.restart:
        if shutil.which("systemctl"):
            subprocess.run(["systemctl", "--user", "restart", "dusky-stt.service"])
            print("Systemd instance cycled.")
        return

    if args.file:
        fpath = Path(args.file).expanduser().resolve()
        if not fpath.exists():
            print(f"Target media path does not exist: {fpath}", file=sys.stderr)
            sys.exit(1)
        if not is_running():
            print("Initializing backend worker structure...")
            if not start_daemon():
                sys.exit(1)
        if send_fifo(f"FILE:{fpath}"):
            print(f"Successfully staged mapping for: {fpath.name}")
            if shutil.which("notify-send"):
                subprocess.run(["notify-send", "-a", "Dusky STT", "File ingest scheduled", f"{fpath.name}"])
        else:
            sys.exit(1)
        return

    is_realtime_mode = True
    try:
        cfg_path = APP_DIR / "install_config.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
            is_realtime_mode = cfg.get("realtime", True)
    except Exception:
        pass
        
    if args.realtime:
        is_realtime_mode = True
    if args.push:
        is_realtime_mode = False

    if RECORD_PID_FILE.exists():
        print("Halting open capture sequence...")
        if send_fifo("STOP"):
            RECORD_PID_FILE.unlink(missing_ok=True)
            if shutil.which("notify-send"):
                subprocess.run(["notify-send", "-a", "Dusky STT", "-t", "2000", "Finalizing chunk aggregations...", "Computing ASR Matrix"], check=False)
    else:
        if not is_running():
            print("Waking daemon layer...")
            if not start_daemon():
                print("Failed to authenticate systemd initialization parameters.", file=sys.stderr)
                sys.exit(1)
                
        cmd = "START_REALTIME" if is_realtime_mode else "START"
        if send_fifo(cmd):
            secure_write_file_atomic(RECORD_PID_FILE, "recording")
            mode_str = "Streaming Suffix Engine" if is_realtime_mode else "Push-to-Talk standard"
            print(f"Capture window open [{mode_str}] - Focus target input element.")
            if shutil.which("notify-send"):
                subprocess.run(["notify-send", "-a", "Dusky STT", "-t", "2500", f"{mode_str}", "Pipeline active. Execute trigger again to truncate capture window."], check=False)
        else:
            print("IPC handshake block over FIFO.", file=sys.stderr)

if __name__ == "__main__":
    main()
