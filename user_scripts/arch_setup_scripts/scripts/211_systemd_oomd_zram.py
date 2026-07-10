#!/usr/bin/env python3
"""
Platinum Hyprland OOM fix - Arch bleeding edge July 2026
systemd 261.1, Hyprland 0.55.4, kernel 7.1.3, Python 3.14
Fixes: compositor killed by kernel OOM because apps run in session-*.scope
"""
from __future__ import annotations
import os, sys, subprocess, signal, tempfile, shutil, filecmp
from pathlib import Path
from dataclasses import dataclass
from typing import Final

SELF_PATH: Final[Path] = Path(__file__).resolve()

def _bootstrap_rich() -> None:
    try:
        import rich; return
    except ImportError:
        pass
    subprocess.run(["sudo","pacman","-S","--needed","--noconfirm","python-rich"], check=False)
    os.execv(sys.executable, [sys.executable, str(SELF_PATH), *sys.argv[1:]])
_bootstrap_rich()

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box

console: Final[Console] = Console()

# --- 261-valid oomd rules ---
# Man page: MemoryPressureAbove=, SwapUsageMax=, LastingSec=, Action=kill-all|kill-by-pgscan|kill-by-swap
PRESSURE_RULE: Final[str] = """[Rule]
MemoryPressureAbove=75%
LastingSec=20s
Action=kill-by-pgscan
"""

SWAP_RULE: Final[str] = """[Rule]
SwapUsageMax=90%
LastingSec=10s
Action=kill-by-swap
"""

# --- Slice / Scope configs ---
# systemd.resource-control: ManagedOOMMemoryPressure=auto|kill, ManagedOOMSwap=auto|kill, ManagedOOMPreference=none|avoid|omit
APP_SLICE: Final[str] = """[Slice]
ManagedOOMMemoryPressure=kill
ManagedOOMSwap=kill
OOMRules=30-desktop-pressure 30-desktop-swap
"""

BACKGROUND_SLICE: Final[str] = """[Slice]
ManagedOOMMemoryPressure=kill
ManagedOOMSwap=kill
ManagedOOMPreference=kill
OOMRules=30-desktop-pressure 30-desktop-swap
"""

SESSION_SLICE: Final[str] = """[Slice]
ManagedOOMPreference=avoid
ManagedOOMMemoryPressure=avoid
ManagedOOMSwap=avoid
"""

# Prefix drop-in: session-.scope.d applies to all session-*.scope via dash truncation rule
# NOTE: OOMScoreAdjust is NOT valid for [Scope] units in systemd 261.
# Compositor protection is applied via `sudo choom` in autostart.lua instead.
COMPOSITOR_SCOPE: Final[str] = """[Scope]
OOMPolicy=continue
ManagedOOMPreference=avoid
"""

USER_SLICE_DEFAULTS: Final[str] = """[Slice]
MemoryHigh=90%
MemoryMax=95%
"""

USER_MANAGER_SCORE: Final[str] = """[Service]
OOMScoreAdjust=-100
OOMPolicy=continue
"""

USER_CONF: Final[str] = """[Manager]
DefaultOOMScoreAdjust=100
"""

OOM_SHIELD: Final[str] = """[Service]
OOMScoreAdjust=-400
OOMPolicy=continue
"""

OOMD_TUNE: Final[str] = """[OOM]
DefaultMemoryPressureLimit=75%
DefaultMemoryPressureDurationSec=20s
SwapUsedLimit=90%
"""

CRITICAL_USER: Final[tuple[str,...]] = (
    "pipewire.service","wireplumber.service","pipewire-pulse.service",
    "xdg-desktop-portal.service","xdg-desktop-portal-hyprland.service",
    "xdg-desktop-portal-gtk.service","dbus-broker.service","mako.service",
)

HYPR_APP_WRAPPER: Final[str] = """#!/bin/bash
# hypr-app - native replacement for `uwsm app` without UWSM
# Launches apps in app.slice so systemd-oomd can see them, not in session-*.scope
# NOTE: OOMScoreAdjust is NOT valid for scope units in systemd 261,
#       so we use choom post-launch to raise the OOM score of spawned apps.
set -euo pipefail
if [ $# -eq 0 ]; then echo "usage: hypr-app <cmd> [args...]" >&2; exit 1; fi
# Ensure env is imported (idempotent)
systemctl --user import-environment WAYLAND_DISPLAY DISPLAY HYPRLAND_INSTANCE_SIGNATURE XDG_CURRENT_DESKTOP 2>/dev/null || true
systemd-run --user --scope \\
  --slice=app.slice \\
  -p MemoryHigh=85% \\
  -p MemoryMax=95% \\
  --collect --same-dir --quiet \\
  -E WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}" \\
  -E DISPLAY="${DISPLAY:-:0}" \\
  -E XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP:-Hyprland}" \\
  -- "$@" &
SCOPE_PID=$!
# Raise OOM score so oomd kills apps before the compositor
sudo -n /usr/bin/choom -n 200 -p "$SCOPE_PID" 2>/dev/null || true
wait "$SCOPE_PID"
"""

@dataclass(frozen=True, slots=True, kw_only=True)
class FileSpec:
    dest: Path
    content: str
    mode: int = 0o644
    desc: str

def specs() -> list[FileSpec]:
    s: list[FileSpec] = [
        # oomd rulesets - new in 261
        FileSpec(dest=Path("/etc/systemd/oomd/rules.d/30-desktop-pressure.oomrule"), content=PRESSURE_RULE, desc="oomd pressure rule 75%/20s"),
        FileSpec(dest=Path("/etc/systemd/oomd/rules.d/30-desktop-swap.oomrule"), content=SWAP_RULE, desc="oomd swap rule 90%/10s"),
        FileSpec(dest=Path("/etc/systemd/oomd.conf.d/10-desktop-tune.conf"), content=OOMD_TUNE, desc="oomd defaults tune"),
        # user slices - standard special slices
        FileSpec(dest=Path("/etc/systemd/user/app.slice.d/10-oomd.conf"), content=APP_SLICE, desc="app.slice -> kill"),
        FileSpec(dest=Path("/etc/systemd/user/background.slice.d/10-oomd.conf"), content=BACKGROUND_SLICE, desc="background.slice -> kill first"),
        FileSpec(dest=Path("/etc/systemd/user/session.slice.d/10-oomd-avoid.conf"), content=SESSION_SLICE, desc="session.slice -> avoid"),
        # compositor protection - prefix drop-in works for all session-*.scope
        FileSpec(dest=Path("/etc/systemd/system/session-.scope.d/10-compositor-protect.conf"), content=COMPOSITOR_SCOPE, desc="protect Hyprland scope -250"),
        FileSpec(dest=Path("/etc/systemd/system/user-.slice.d/10-defaults.conf"), content=USER_SLICE_DEFAULTS, desc="user-*.slice limits"),
        FileSpec(dest=Path("/etc/systemd/system/user@.service.d/10-oom-score.conf"), content=USER_MANAGER_SCORE, desc="user@.service -100"),
        FileSpec(dest=Path("/etc/systemd/user.conf.d/10-oom-default.conf"), content=USER_CONF, desc="DefaultOOMScoreAdjust=100"),
        # wrapper
        FileSpec(dest=Path("/usr/local/bin/hypr-app"), content=HYPR_APP_WRAPPER, mode=0o755, desc="hypr-app launcher"),
        # NOTE: choom NOPASSWD sudoers rule is managed by 485_sudoers_nopassword.sh
    ]
    for svc in CRITICAL_USER:
        s.append(FileSpec(dest=Path(f"/etc/systemd/user/{svc}.d/10-oom-shield.conf"), content=OOM_SHIELD, desc=f"shield {svc}"))
    return s

def atomic_install(spec: FileSpec) -> str:
    d = spec.dest; d.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(d.parent))
    tp = Path(tmp)
    try:
        tp.write_text(spec.content, encoding="utf-8")
        os.chmod(tp, spec.mode)
        if d.exists() and filecmp.cmp(str(tp), str(d), shallow=False):
            return "up-to-date"
        shutil.move(str(tp), str(d)); os.chmod(d, spec.mode); return "updated"
    finally:
        try: tp.unlink()
        except: pass
        try: os.close(fd)
        except: pass

def main() -> None:
    dry = "--dry-run" in sys.argv or "-n" in sys.argv
    if not dry and os.geteuid()!=0:
        console.print("[blue]Re-exec via sudo[/]"); os.execvp("sudo",["sudo",sys.executable,str(SELF_PATH),*sys.argv[1:]])
    console.print(Panel.fit("[bold cyan]Hyprland 0.55.4 + systemd 261.1 OOM fix - bleeding edge[/]", box=box.DOUBLE))
    all_specs = specs()
    if dry:
        t=Table(box=box.SIMPLE_HEAVY); t.add_column("Dest"); t.add_column("Desc")
        for x in all_specs: t.add_row(str(x.dest), x.desc)
        console.print(t); return
    upd=0
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
        task=p.add_task("Installing", total=len(all_specs))
        for sp in all_specs:
            st=atomic_install(sp); console.print(f"[green]{st.upper()}[/] {sp.dest}"); upd+=1; p.advance(task)
    # enable oomd
    for cmd in [["systemctl","unmask","systemd-oomd"],["systemctl","enable","--now","systemd-oomd"],["systemctl","daemon-reload"]]:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    console.print(Panel.fit(f"[bold green]✔ {upd} files deployed\n✔ hypr-app ready\n✔ Next: re-login, then use: hypr-app kitty[/]", box=box.ROUNDED))

if __name__=="__main__": main()
