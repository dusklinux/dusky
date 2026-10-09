#!/usr/bin/env python3
"""Control client for Dusky STT (stdlib only).

Defaults to toggling record-then-transcribe dictation with zero arguments (hotkey friendly).
Validates socket ownership/modes before connecting; never trusts permissions alone.
"""

import argparse
import fcntl
import json
import math
import os
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

MAX_PACKET = 65536
DEFAULT_TIMEOUT = 10.0
WAIT_DEADLINE_S = 4 * 3600  # --wait cap for very long files (Ctrl-C aborts client only)

type JsonObject = dict[str, Any]


def app_config_path() -> Path:
    return Path(os.environ.get("DUSKY_CONFIG", str(Path(os.environ.get("DUSKY_APP_DIR", Path.home() / "contained_apps/uv/dusky_stt")) / "config.json"))).expanduser()


def transcripts_dir() -> Path:
    try:
        cfg = json.loads(app_config_path().read_text(encoding="utf-8"))
        state = str(cfg.get("state_dir", "~/.local/state/dusky-stt"))
    except (OSError, ValueError):
        state = "~/.local/state/dusky-stt"
    return Path(state).expanduser() / "transcripts"


def newest_transcript(after: float | None = None) -> Path | None:
    d = transcripts_dir()
    try:
        cands = [p for p in d.glob("capture-*.txt") if p.is_file()]
    except OSError:
        return None
    if after is not None:
        cands = [p for p in cands if p.stat().st_mtime > after]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def control_path() -> Path:
    rt = os.environ.get("XDG_RUNTIME_DIR")
    if not rt:
        raise RuntimeError("XDG_RUNTIME_DIR is unset.")
    return Path(rt) / "dusky-stt" / "control.sock"


def is_socket_secure(p: Path) -> bool:
    try:
        d_st = p.parent.lstat()
        f_st = p.lstat()
    except OSError:
        return False
    return (d_st.st_uid == os.getuid() and stat.S_IMODE(d_st.st_mode) == 0o700
            and stat.S_ISSOCK(f_st.st_mode) and f_st.st_uid == os.getuid()
            and stat.S_IMODE(f_st.st_mode) == 0o600)


def runtime_available() -> bool:
    p = control_path()
    if not is_socket_secure(p):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC) as sock:
            sock.settimeout(1.0)
            sock.connect(str(p))
            sock.sendmsg([b'{"command":"status"}'])
            response = json.loads(sock.recv(MAX_PACKET))
        return response.get("ok") is True and response.get("state") != "stopping"
    except (OSError, ValueError):
        return False


def runtime_log() -> Path:
    cfg = json.loads(app_config_path().read_text(encoding="utf-8"))
    return Path(cfg.get("state_dir", "~/.local/state/dusky-stt")).expanduser() / "runtime.log"


def ensure_running() -> None:
    if runtime_available():
        return
    directory = control_path().parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    deadline = time.monotonic() + 20.0
    # Serialize concurrent hotkey clients and wait for an exiting predecessor.
    with open(directory / "launch.lock", "a+b") as launch_lock:
        fcntl.flock(launch_lock, fcntl.LOCK_EX)
        child = None
        while time.monotonic() < deadline:
            if runtime_available():
                return
            with open(directory / "instance.lock", "a+b") as instance_lock:
                try:
                    fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    time.sleep(0.02)
                    continue
                if child is None:
                    config = app_config_path()
                    app = Path(os.environ.get("DUSKY_APP_DIR", Path.home() / "contained_apps/uv/dusky_stt")).expanduser()
                    log = runtime_log()
                    log.parent.mkdir(parents=True, exist_ok=True)
                    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
                    env.pop("NOTIFY_SOCKET", None)
                    env.pop("WATCHDOG_USEC", None)
                    with log.open("ab") as output:
                        child = subprocess.Popen(
                            [str(app / ".venv/bin/python"), str(app / "dusky_main.py"), "--config", str(config)],
                            cwd=app, env=env, stdin=subprocess.DEVNULL,
                            stdout=output, stderr=output, start_new_session=True)
            if child is not None and child.poll() is not None:
                raise RuntimeError(f"Recording process exited ({child.returncode}); see {runtime_log()}")
            time.sleep(0.02)
    raise TimeoutError(f"Recording process did not become ready; see {runtime_log()}")


def stop_running(timeout: float = 20.0) -> None:
    try:
        send_command({"command": "shutdown"}, start_process=False)
    except (FileNotFoundError, ConnectionRefusedError):
        return
    deadline = time.monotonic() + timeout
    with open(control_path().parent / "instance.lock", "a+b") as instance_lock:
        while True:
            try:
                fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Recording process did not exit")
                time.sleep(0.02)


def send_command(payload: JsonObject, timeout: float = DEFAULT_TIMEOUT, *, start_process: bool = True, _retries: int = 2) -> JsonObject:
    if start_process:
        ensure_running()
    p = control_path()
    if not is_socket_secure(p):
        if start_process and _retries and not p.exists():
            return send_command(payload, timeout=timeout, start_process=True, _retries=_retries - 1)
        raise RuntimeError(f"Control socket missing/insecure: {p}")
    blob = json.dumps(payload).encode()
    if len(blob) > MAX_PACKET:
        raise ValueError("Request too large for SEQPACKET")
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC) as s:
        s.settimeout(timeout)
        try:
            s.connect(str(p))
        except (FileNotFoundError, ConnectionRefusedError):
            # No command was delivered, so retrying a toggle is unambiguous.
            if start_process and _retries:
                return send_command(payload, timeout=timeout, start_process=True, _retries=_retries - 1)
            raise
        s.sendmsg([blob])
        data, _, flags, _ = s.recvmsg(MAX_PACKET)
        if flags & getattr(socket, "MSG_TRUNC", 0x20):
            raise RuntimeError("Response truncated")
        if not data:
            raise RuntimeError("Daemon closed connection")
    response = json.loads(data.decode())
    if not isinstance(response, dict):
        raise ValueError("Daemon response must be an object")
    if response.get("retry") and start_process and _retries:
        time.sleep(0.1)
        return send_command(payload, timeout=timeout, start_process=True, _retries=_retries - 1)
    return response


def wait_for_transcript(baseline: float, job: str | None = None) -> JsonObject:
    """Poll until the daemon returns to idle, then print the transcript.

    Ctrl-C aborts only this client; the daemon keeps transcribing.
    In on-demand mode the recording process exits after the job: a dead
    socket then counts as done, and the transcript file is authoritative.
    """
    deadline = time.monotonic() + WAIT_DEADLINE_S
    missed = 0
    try:
        while time.monotonic() < deadline:
            if job:
                result_file = transcripts_dir().parent / "jobs" / f"{job}.json"
                try:
                    result = json.loads(result_file.read_text())
                except FileNotFoundError:
                    result = None
                if result is not None:
                    if result.get("ok") is not True:
                        return result
                    path = Path(result["path"])
                    text = path.read_text(encoding="utf-8")
                    print(text, end="" if text.endswith("\n") else "\n")
                    return {**result, "chars": len(text), "words": len(text.split())}
            try:
                st = send_command({"command": "status"}, timeout=DEFAULT_TIMEOUT, start_process=False)
                missed = 0
            except (OSError, ValueError, RuntimeError):
                st = None
                missed += 1
            if st is None:
                # Daemon unreachable: either still booting (keep waiting) or
                # exited after finishing (transcript decides below).
                # A crash mid-job looks the same, so give up after ~30 s of
                # continuous silence with no transcript to show for it.
                if not job and newest_transcript(after=baseline) is not None:
                    break
                if missed >= 15:
                    return {"ok": False, "error": "daemon unreachable for 30s (crashed mid-job?)"}
                time.sleep(2.0)
                continue
            if not job and st.get("state", "idle") == "idle":
                break
            time.sleep(2.0)
        else:
            return {"ok": False, "error": f"timed out after {WAIT_DEADLINE_S // 3600}h waiting for transcription"}
    except KeyboardInterrupt:
        return {"ok": False, "error": "wait interrupted (transcription continues in background)"}
    path = newest_transcript(after=baseline)
    if path is None:
        return {"ok": False, "error": "transcription finished but no transcript file appeared"}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"cannot read {path}: {exc}"}
    print(text, end="" if text.endswith("\n") else "\n")
    return {"ok": True, "event": "transcribed", "path": str(path),
            "chars": len(text), "words": len(text.split())}


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="dusky_trigger",
        usage="%(prog)s [OPTIONS]", add_help=False)
    ap.add_argument("-h", "--help", action="store_true")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--start", action="store_true")
    g.add_argument("--stop", action="store_true")
    g.add_argument("--pause", action="store_true", help="Pause / resume the current capture")
    g.add_argument("--toggle", action="store_true")
    g.add_argument("--status", action="store_true")
    g.add_argument("--file", type=Path, default=None, help="Transcribe audio/video file")
    g.add_argument("--unload", action="store_true", help="Unload the ASR worker now (free VRAM/RAM)")
    g.add_argument("--kill", action="store_true")
    g.add_argument("--logs", action="store_true")
    g.add_argument("--backend", choices=("auto", "cpu", "nvidia"), help="Select an installed backend while idle")
    m = ap.add_mutually_exclusive_group()
    m.add_argument("--realtime", action="store_true", help=argparse.SUPPRESS)
    m.add_argument("--push", action="store_true", default=False)
    ap.add_argument("--wait", action="store_true",
                    help="With --file: wait for completion, then print the transcript to stdout")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = ap.parse_args()
    if args.help:
        print("""Dusky · English clipboard dictation

  Usage  dusky_trigger [OPTIONS]
  Default: record; press again to stop, transcribe and copy

Capture
  --start               Record, then transcribe to clipboard
  --push                Accepted for existing hotkeys
  --toggle              Start or stop; default hotkey action
  --stop / --pause       Finalize, or toggle pause

Files
  --file PATH --wait     Transcribe and print the exact job's result
  --file PATH            Transcribe in the background

Control
  --status / --unload    Show state or release the ASR worker
  --backend MODE        auto / cpu / nvidia; switch while idle
  --kill                Cancel and stop the recording process
  --logs                Follow the recording log
  --json                Print structured responses
  --timeout SECONDS     Control request timeout
  -h, --help            Show this help

  Hotkey  bind = SUPER, I, exec, dusky_trigger
  Each invocation loads during capture and releases the model afterward.
""", end="")
        return 0
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        ap.error("--timeout must be finite and positive")

    if args.wait and args.file is None:
        print("--wait needs --file", file=sys.stderr)
        return 2

    if args.logs:
        log = runtime_log()
        log.parent.mkdir(parents=True, exist_ok=True)
        log.touch(exist_ok=True)
        os.execvp("tail", ["tail", "-n", "50", "-F", str(log)])
    if args.kill:
        if control_path().exists():
            stop_running()
        return 0

    if args.realtime:
        ap.error("Live typing has been removed; use record-then-copy dictation")
    mode = "push"
    if args.status:
        try:
            resp = send_command({"command": "status"}, timeout=args.timeout, start_process=False)
        except (OSError, RuntimeError) as exc:
            resp = {"ok": True, "state": "idle", "running": False}
    elif args.backend:
        resp = send_command({"command": "backend", "backend": args.backend}, timeout=max(args.timeout, 30.0))
    elif args.start:
        resp = send_command({"command": "start", "mode": mode}, timeout=args.timeout)
    elif args.stop:
        resp = send_command({"command": "stop"}, timeout=max(args.timeout, 180.0), start_process=False)
    elif args.pause:
        resp = send_command({"command": "pause"}, timeout=args.timeout, start_process=False)
    elif args.file is not None:
        src = args.file.expanduser()
        if not src.is_file():
            print(f"File not found: {src}", file=sys.stderr)
            return 2
        baseline = time.time()
        resp = send_command({"command": "file", "path": str(src.resolve())}, timeout=max(args.timeout, 300.0))
        if args.wait and resp.get("ok"):
            resp = wait_for_transcript(baseline, resp.get("job"))
    elif args.unload:
        resp = send_command({"command": "unload"}, timeout=args.timeout, start_process=False)
    else:  # default: toggle (bare hotkey invocation)
        resp = send_command({"command": "toggle", "mode": mode}, timeout=max(args.timeout, 180.0))

    if args.wait and resp.get("ok") and "path" in resp:
        # Transcript body already went to stdout; keep it script-clean by
        # sending the metadata to stderr.
        print(f"transcribed: {resp['path']} ({resp.get('words', 0)} words)", file=sys.stderr)
        return 0
    if args.json:
        print(json.dumps(resp, indent=2))
    else:
        for k, v in resp.items():
            print(f"{k:15}: {v}")
    return 0 if resp.get("ok") else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"dusky_trigger: {exc}", file=sys.stderr)
        sys.exit(1)
