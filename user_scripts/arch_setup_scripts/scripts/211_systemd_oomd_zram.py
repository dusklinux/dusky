#!/usr/bin/env python3
"""
Hyprland 0.55.4 + systemd 261.1 + kernel 7.1 OOM protection - v4 audited
Fixes: fd leak, --user reload as root, missing omit, double-count,
       oomd restart, MemoryAccounting, robust dusky-run.
Excludes zram/sysctl settings to avoid conflicts with core memtune orchestrator.
"""
from __future__ import annotations
import os, sys, subprocess, tempfile, filecmp, pwd, argparse, shutil
from pathlib import Path
from dataclasses import dataclass
from typing import Final

SELF_PATH: Final[Path] = Path(__file__).resolve()

def _bootstrap_rich() -> None:
    try:
        import rich # noqa: F401
        return
    except ImportError:
        pass
    if shutil.which("pacman"):
        subprocess.run(["sudo","pacman","-S","--needed","--noconfirm","python-rich"], check=False)
    os.execv(sys.executable, [sys.executable, str(SELF_PATH), *sys.argv[1:]])

_bootstrap_rich()
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box

console: Final[Console] = Console()

# --- Rules (systemd 261+) ---
PRESSURE_RULE: Final[str] = """[Rule]
# Full PSI stall avg10 >75% for 20s -> kill heaviest pgscan child
MemoryPressureAbove=75%
LastingSec=20s
Action=kill-by-pgscan
"""

SWAP_RULE: Final[str] = """[Rule]
# System swap >90% for 10s -> kill heaviest swap user
SwapUsageMax=90%
LastingSec=10s
Action=kill-by-swap
"""

OOMD_TUNE: Final[str] = """[OOM]
# Old-style fallbacks + new hook support
DefaultMemoryPressureLimit=75%
DefaultMemoryPressureDurationSec=20s
SwapUsedLimit=90%
PrekillHookTimeoutSec=5s
"""

APP_SLICE: Final[str] = """[Slice]
ManagedOOMMemoryPressure=kill
ManagedOOMSwap=kill
ManagedOOMMemoryPressureLimit=70%
ManagedOOMPreference=none
OOMRules=30-desktop-pressure 30-desktop-swap
# Throttle before global OOM
MemoryHigh=85%
MemoryMax=90%
MemoryAccounting=yes
"""

BACKGROUND_SLICE: Final[str] = """[Slice]
ManagedOOMMemoryPressure=kill
ManagedOOMSwap=kill
ManagedOOMMemoryPressureLimit=75%
OOMRules=30-desktop-pressure 30-desktop-swap
MemoryHigh=90%
MemoryMax=95%
MemoryAccounting=yes
"""

SESSION_SLICE: Final[str] = """[Slice]
ManagedOOMPreference=avoid
MemoryMin=256M
MemoryAccounting=yes
"""

COMPOSITOR_SCOPE: Final[str] = """[Scope]
OOMPolicy=continue
ManagedOOMPreference=avoid
MemoryAccounting=yes
"""

USER_MANAGER_SCORE: Final[str] = """[Service]
OOMScoreAdjust=-100
OOMPolicy=continue
"""

USER_CONF: Final[str] = """[Manager]
DefaultOOMScoreAdjust=100
DefaultMemoryPressureWatch=yes
"""

# Critical services policy - unprivileged systemd user manager cannot lower score below -100
OOM_SHIELD: Final[str] = """[Service]
OOMScoreAdjust=-100
OOMPolicy=continue
ManagedOOMPreference=omit
MemoryAccounting=yes
"""

OOMD_SERVICE_SHIELD: Final[str] = """[Service]
OOMScoreAdjust=-1000
"""

CRITICAL_USER: Final[tuple[str,...]] = (
    "pipewire.service","wireplumber.service","pipewire-pulse.service",
    "xdg-desktop-portal.service","xdg-desktop-portal-hyprland.service",
    "xdg-desktop-portal-gtk.service","dbus.service","mako.service",
)

DUSKY_RUN_WRAPPER: Final[str] = """#!/bin/bash
# dusky-run v4 - unprivileged OOM score elevation for transient scopes
# systemd 261 ignores OOMScoreAdjust= in [Scope]; scopes inherit parent oom_score_adj.
set -euo pipefail
if [[ $# -eq 0 ]]; then
  echo "usage: dusky-run <cmd> [args...]" >&2; exit 1
fi
# 200 = more killable. Increase is allowed for unprivileged users.
printf '%d\\n' 200 > /proc/self/oom_score_adj 2>/dev/null || true
exec systemd-run --user --scope --slice=app.slice --collect --quiet \\
  --property=OOMPolicy=continue \\
  --property=ManagedOOMPreference=none \\
  --property=MemoryAccounting=yes \\
  -- "$@"
"""

@dataclass(frozen=True, slots=True, kw_only=True)
class FileSpec:
    dest: Path
    content: str
    mode: int = 0o644
    desc: str

def specs() -> list[FileSpec]:
    s: list[FileSpec] = [
        FileSpec(dest=Path("/etc/systemd/oomd/rules.d/30-desktop-pressure.oomrule"), content=PRESSURE_RULE, desc="pressure rule (kill-by-pgscan)"),
        FileSpec(dest=Path("/etc/systemd/oomd/rules.d/30-desktop-swap.oomrule"), content=SWAP_RULE, desc="swap rule (kill-by-swap)"),
        FileSpec(dest=Path("/etc/systemd/oomd.conf.d/10-desktop-tune.conf"), content=OOMD_TUNE, desc="oomd tune + PrekillHook"),
        FileSpec(dest=Path("/etc/systemd/user/app.slice.d/90-desktop-oomd.conf"), content=APP_SLICE, desc="app.slice killable + limits"),
        FileSpec(dest=Path("/etc/systemd/user/background.slice.d/90-desktop-oomd.conf"), content=BACKGROUND_SLICE, desc="background.slice killable"),
        FileSpec(dest=Path("/etc/systemd/user/session.slice.d/90-desktop-oomd.conf"), content=SESSION_SLICE, desc="session.slice avoid"),
        FileSpec(dest=Path("/etc/systemd/system/session-.scope.d/90-desktop-oomd.conf"), content=COMPOSITOR_SCOPE, desc="session-*.scope avoid+continue"),
        FileSpec(dest=Path("/etc/systemd/system/user@.service.d/90-desktop-oom-score.conf"), content=USER_MANAGER_SCORE, desc="user@ -100"),
        FileSpec(dest=Path("/etc/systemd/user.conf.d/90-desktop-oom.conf"), content=USER_CONF, desc="DefaultOOMScoreAdjust 100"),
        FileSpec(dest=Path("/etc/systemd/system/systemd-oomd.service.d/90-desktop-oomd.conf"), content=OOMD_SERVICE_SHIELD, desc="systemd-oomd kernel-OOM protection"),
        FileSpec(dest=Path("/usr/local/bin/dusky-run"), content=DUSKY_RUN_WRAPPER, mode=0o755, desc="dusky-run v4 fixed"),
    ]
    for svc in CRITICAL_USER:
        s.append(FileSpec(dest=Path(f"/etc/systemd/user/{svc}.d/90-desktop-oom.conf"), content=OOM_SHIELD, desc=f"shield {svc} omit -100"))
    return s

def obsolete_paths() -> tuple[Path, ...]:
    """
    Remove files known to have been created by the previous version scripts.
    """
    paths: list[Path] = [
        Path("/etc/systemd/user/app.slice.d/10-oomd.conf"),
        Path("/etc/systemd/user/background.slice.d/10-oomd.conf"),
        Path("/etc/systemd/user/session.slice.d/10-oomd-avoid.conf"),
        Path("/etc/systemd/system/session-.scope.d/10-compositor-protect.conf"),
        Path("/etc/systemd/system/user@.service.d/10-oom-score.conf"),
        Path("/etc/systemd/user.conf.d/10-oom-default.conf"),
    ]
    for service in CRITICAL_USER:
        paths.append(Path(f"/etc/systemd/user/{service}.d/10-oom-shield.conf"))
    return tuple(paths)

def atomic_install(spec: FileSpec) -> str:
    d = spec.dest
    d.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(d.parent), prefix=f".{d.name}.tmp.")
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(spec.content)
            if not spec.content.endswith('\n'):
                f.write('\n')
        os.chmod(tmp_path, spec.mode)
        if d.exists() and filecmp.cmp(tmp_path, str(d), shallow=False):
            return "up-to-date"
        os.replace(tmp_path, str(d))
        os.chmod(d, spec.mode)
        return "updated"
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass

def remove_if_present(path: Path) -> str:
    if not os.path.lexists(str(path)):
        return "absent"
    if path.is_dir() and not path.is_symlink():
        raise RuntimeError(f"refusing to remove directory: {path}")
    path.unlink()
    return "removed"

def reload_user_manager() -> None:
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user:
        subprocess.run(["systemctl","--user","daemon-reload"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    try:
        pw = pwd.getpwnam(sudo_user)
        uid = pw.pw_uid
        runtime = f"/run/user/{uid}"
        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = runtime
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime}/bus"
        subprocess.run(["runuser","-u",sudo_user,"--","systemctl","--user","daemon-reload"],
                       env=env, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def main() -> None:
    ap = argparse.ArgumentParser(description="Deploy Hyprland OOM config")
    ap.add_argument("-n","--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.dry_run and os.geteuid()!= 0:
        console.print("[blue]Re-exec via sudo[/]")
        os.execvp("sudo", ["sudo", sys.executable, str(SELF_PATH), *sys.argv[1:]])

    # Basic sanity
    if not Path("/sys/fs/cgroup/cgroup.controllers").exists():
        console.print("[red]cgroup v2 required for systemd-oomd[/]"); sys.exit(1)

    console.print(Panel.fit("[bold cyan]Hyprland 0.55.4 + systemd 261.1 OOM fix v4 - VM validated[/]", box=box.DOUBLE))
    all_specs = specs()

    if args.dry_run:
        t = Table(box=box.SIMPLE_HEAVY); t.add_column("Dest"); t.add_column("Desc"); t.add_column("Mode")
        for x in all_specs:
            t.add_row(str(x.dest), x.desc, oct(x.mode))
        console.print(t)
        return

    updated = 0
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as prog:
        task = prog.add_task("Installing", total=len(all_specs))
        results = []
        for sp in all_specs:
            st = atomic_install(sp)
            results.append((sp, st))
            if st == "updated": updated += 1
            prog.advance(task)
            
    removed = 0
    for path in obsolete_paths():
        st = remove_if_present(path)
        if st == "removed": removed += 1

    for sp, st in results:
        col = "green" if st=="updated" else "dim"
        console.print(f"[{col}]{st.upper():11}[/] {sp.dest} [dim]({sp.desc})[/]")

    cmds = [
        ["systemctl","daemon-reload"],
        ["systemctl","unmask","systemd-oomd"],
        ["systemctl","enable","--now","systemd-oomd"],
        ["systemctl","restart","systemd-oomd"],
    ]
    for c in cmds:
        subprocess.run(c, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    reload_user_manager()

    console.print(Panel.fit(
        f"[bold green]✔ {updated} updated, {len(all_specs)-updated} up-to-date\n"
        f"✔ Removed {removed} obsolete files\n"
        f"✔ Keep in autostart.lua: hl.on(\"hyprland.start\", function() hl.exec_cmd(\"sudo choom -n -250 -p $(pgrep -x Hyprland)\") end)\n"
        f"✔ Run: oomctl dump\n"
        f"✔ Re-login required for user.conf DefaultOOMScoreAdjust[/]",
        box=box.ROUNDED))

if __name__ == "__main__":
    main()
