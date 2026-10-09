"""TUI maintenance commands using the same configuration as the collector."""

import argparse
import grp
import os
import pwd
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from dusky_keylogger.daemon import get_data_dir, get_transcript_dir, load_config
    from dusky_keylogger.storage import KeyStore
else:
    from .daemon import get_data_dir, get_transcript_dir, load_config
    from .storage import KeyStore


def ensure_input_group() -> bool:
    """Add missing account membership. Return whether this process has access."""
    record = pwd.getpwuid(os.getuid())
    group = grp.getgrnam("input")
    current = group.gr_gid in {os.getgid(), *os.getgroups()}
    member = group.gr_gid in os.getgrouplist(record.pw_name, record.pw_gid)
    if not member:
        command = ["usermod", "-aG", "input", record.pw_name]
        if os.geteuid() != 0:
            command.insert(0, "sudo")
        subprocess.run(command, check=True)
        print(f"Added {record.pw_name} to input. Log out/in for foreground collection.")
    elif current:
        print(f"{record.pw_name} is in the input group.")
    else:
        print("Input membership is configured; log out/in for foreground collection.")
    return current


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=[
        "ensure-data", "ensure-transcripts", "purge", "clear-transcripts", "input-group",
    ])
    args = parser.parse_args(argv)
    try:
        if args.command == "input-group":
            ensure_input_group()
            return 0
        config = load_config()
        data_dir = get_data_dir(config)
        transcript_dir = get_transcript_dir(config)
        if args.command in {"ensure-data", "ensure-transcripts"}:
            directory = data_dir if args.command == "ensure-data" else transcript_dir
            directory.mkdir(parents=True, exist_ok=True, mode=0o700, parent_mode=0o700)
            print(f"Directory ready: {directory} (mode {directory.stat().st_mode & 0o7777:04o})")
        elif args.command == "purge":
            # Removing live WAL/SHM files can corrupt the store. Stop its system
            # owner first; the TUI's existing confirmation covers this action.
            subprocess.run(["sudo", "systemctl", "stop", "dusky_keylogger.service"], check=True)
            with KeyStore(data_dir / 'keys.db').collector_lock():
                for suffix in ("", "-wal", "-shm"):
                    (data_dir / f"keys.db{suffix}").unlink(missing_ok=True)
            print(f"Deleted database in {data_dir}; service remains stopped.")
        else:
            for path in transcript_dir.glob("dusky-typed-*"):
                if path.is_file() and path.stat().st_uid == os.getuid():
                    path.unlink()
                    print(f"Removed {path}")
        return 0
    except (OSError, KeyError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Maintenance failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
