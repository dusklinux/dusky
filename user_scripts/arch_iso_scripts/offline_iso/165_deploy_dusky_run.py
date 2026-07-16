#!/usr/bin/env python3
"""
165_deploy_dusky_run.py - DUSKY - Python 3.14.6 + Rich 15.0.0
Pre-deploys the dusky-run wrapper during the chroot install phase.
This guarantees that keybinds relying on dusky-run work immediately on first boot.
"""
from __future__ import annotations
import os, sys, shutil, subprocess, tempfile
from pathlib import Path

def _ensure_rich():
    import importlib.util
    if importlib.util.find_spec("rich") is None:
        subprocess.run(["pacman", "-Sy", "--needed", "--noconfirm", "python-rich"], check=False)
_ensure_rich()
from rich.console import Console
from rich.panel import Panel
from rich import box

def make_console():
    term = os.environ.get("TERM", "")
    if term in ("dumb", "unknown"):
        return Console(color_system=None, force_terminal=False, no_color=True, legacy_windows=False)
    return Console(color_system="standard", legacy_windows=False, safe_box=True, highlight=False, markup=True)

console = make_console()

DUSKY_RUN_WRAPPER = """#!/bin/bash
# dusky-run v6 - unprivileged OOM score elevation for transient scopes
# systemd 261: Scope units do NOT support OOMScoreAdjust property -> Unknown assignment
# So we set parent's oom_score_adj and let systemd-run --scope inherit it
# Kernel: new process inherits parent's oom_score_adj, unprivileged may increase
set -euo pipefail
if [[ $# -eq 0 ]]; then
  echo "usage: dusky-run <cmd> [args...]" >&2; exit 1
fi
if ! printf '%d\\n' 200 > /proc/self/oom_score_adj 2>/dev/null; then
  echo "dusky-run: warning: cannot set oom_score_adj" >&2
fi
exec systemd-run --user --scope --slice=app.slice --collect \\
  --property=OOMPolicy=continue \\
  --property=ManagedOOMPreference=none \\
  --property=MemoryAccounting=yes \\
  -- "$@"
"""

def ensure_root():
    if os.geteuid() != 0:
        console.print("[red][FATAL] Root required[/red]")
        sys.exit(1)

def main():
    ensure_root()
    dest = Path("/usr/local/bin/dusky-run")
    console.print(f"[blue][INFO][/blue] Deploying dusky-run wrapper to {dest}...")
    
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Write atomically
        fd, tmp_path_str = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.tmp.")
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
                f.write(DUSKY_RUN_WRAPPER)
            os.chmod(tmp_path_str, 0o755)
            os.replace(tmp_path_str, str(dest))
        finally:
            Path(tmp_path_str).unlink(missing_ok=True)
            
        console.print("[green][SUCCESS][/green] dusky-run wrapper pre-deployed successfully.")
    except Exception as e:
        console.print(Panel(f"[red]CRITICAL: Failed to deploy dusky-run wrapper: {e}[/red]", box=box.ROUNDED))
        sys.exit(1)

if __name__ == "__main__":
    main()
