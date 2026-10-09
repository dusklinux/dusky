#!/usr/bin/env python3
"""Dusky Template Generator — installer (Python 3.15+, Linux only).

Registers host/dusky_template_host.py as the native messaging host
"dusky_template_generator" for every Gecko browser profile root found, creates
$XDG_CONFIG_HOME/dusky_sites, and runs the host's own selftest.

    python3 setup.py            install / repair
    python3 setup.py --check    report status only
    python3 setup.py --remove   uninstall the manifests
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

HOST_NAME: Final = "dusky_template_generator"
EXT_ID: Final = "dusky_template_generator@dusk.com"
HERE: Final = Path(__file__).resolve().parent
HOST_PY: Final = HERE / "host" / "dusky_template_host.py"

# Every Gecko family that shares the WebExtensions native-messaging layout.
ROOTS: Final = [
    Path.home() / ".mozilla",
    Path.home() / ".librewolf",
    Path.home() / ".waterfox",
    Path.home() / ".var/app/org.mozilla.firefox/.mozilla",
    Path.home() / ".var/app/io.gitlab.librewolf-community/.librewolf",
]


def manifest() -> dict[str, object]:
    return {
        "name": HOST_NAME,
        "description": "Dusky Template Generator native host",
        "path": str(HOST_PY),
        "type": "stdio",
        "allowed_extensions": [EXT_ID],
    }


def targets(existing_only: bool) -> list[Path]:
    out = []
    for root in ROOTS:
        if root == ROOTS[0] or root.is_dir() or not existing_only:
            out.append(root / "native-messaging-hosts" / f"{HOST_NAME}.json")
    return out


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "")
    if not base or not os.path.isabs(base):
        base = str(Path.home() / ".config")
    return Path(base) / "dusky_sites"


def install() -> int:
    if not HOST_PY.is_file():
        print(f"!! missing {HOST_PY}", file=sys.stderr)
        return 1
    HOST_PY.chmod(0o755)

    payload = json.dumps(manifest(), indent=2) + "\n"
    for dest in targets(existing_only=True):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(payload, encoding="utf-8")
        dest.chmod(0o644)
        print(f"   registered {dest}")

    store = config_dir()
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f"   templates  {store}")

    print("   running host selftest …")
    rc = subprocess.run([sys.executable, str(HOST_PY), "--selftest"], check=False).returncode
    if rc:
        print("!! selftest FAILED", file=sys.stderr)
        return rc
    print("\nDone. Reload the extension in about:debugging (or restart the browser).")
    return 0


def remove() -> int:
    for dest in targets(existing_only=False):
        if dest.exists():
            dest.unlink()
            print(f"   removed {dest}")
    print("Templates in", config_dir(), "were left untouched.")
    return 0


def check() -> int:
    bad = 0
    for dest in targets(existing_only=True):
        if not dest.is_file():
            print(f"   MISSING  {dest}")
            bad = 1
            continue
        try:
            data = json.loads(dest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"   INVALID  {dest}: {exc}")
            bad = 1
            continue
        expected = manifest()
        good = isinstance(data, dict) and all(
            data.get(key) == expected[key]
            for key in ("name", "path", "type", "allowed_extensions")
        )
        print(f"   {'OK      ' if good else 'STALE   '} {dest}")
        bad |= 0 if good else 1
    print(f"   host     {HOST_PY} {'(executable)' if os.access(HOST_PY, os.X_OK) else '(NOT executable)'}")
    print(f"   store    {config_dir()} {'exists' if config_dir().is_dir() else 'MISSING'}")
    return bad or int(not HOST_PY.is_file() or not os.access(HOST_PY, os.X_OK))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report status only")
    mode.add_argument("--remove", action="store_true", help="uninstall host manifests")
    args = parser.parse_args()
    raise SystemExit(remove() if args.remove else check() if args.check else install())
