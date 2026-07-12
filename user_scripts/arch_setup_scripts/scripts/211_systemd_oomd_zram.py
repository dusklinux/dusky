#!/usr/bin/env python3
"""
Hyprland 0.55.4 + systemd 261.1 + kernel 7.1 OOM protection - v5 actual fixed
Arch Linux latest only (July 2026). systemd 261, Python 3.14.6.
"""
import os
import sys
import subprocess
import tempfile
import filecmp
import pwd
import argparse
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import Final

SELF_PATH: Final[Path] = Path(__file__).resolve()

def _bootstrap_rich() -> None:
    try:
        import rich  # noqa: F401
        return
    except ImportError:
        pass
    # Arch only per spec
    if not shutil.which("pacman"):
        print("python-rich not found and pacman missing, please install python-rich", file=sys.stderr)
        sys.exit(1)
    is_root = os.geteuid() == 0
    cmd = ["pacman", "-S", "--needed", "--noconfirm", "python-rich"] if is_root else ["sudo", "pacman", "-S", "--needed", "--noconfirm", "python-rich"]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print("Failed to install python-rich, please install manually", file=sys.stderr)
        sys.exit(1)
    # re-exec to pick up newly installed module
    os.execv(sys.executable, [sys.executable, str(SELF_PATH), *sys.argv[1:]])

_bootstrap_rich()
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich import box

console: Final[Console] = Console()

# --- Rules (systemd 261+) ---
# Validated: .oomrule files loaded from /etc/systemd/oomd/rules.d, units opt in via OOMRules=
# Action values mandatory and must be kill-all|kill-by-pgscan|kill-by-swap in 261
PRESSURE_RULE: Final[str] = """[Rule]
# PSI full avg10 >75% for 20s -> kill heaviest pgscan child
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
# Old-style fallbacks still valid in 261, new hook support added in 260
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
# % is relative to installed RAM, converted to bytes by systemd
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
# 256M hard protection - keep low to avoid pinning too much on 4GB boxes
MemoryMin=256M
MemoryAccounting=yes
"""

COMPOSITOR_SCOPE: Final[str] = """[Scope]
# session-.scope.d is valid via truncated prefix logic: foo-bar-baz searches foo-.d
# Scope units have no exec, OOMScoreAdjust defaults to inherit for non-service units
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
# yes forces accounting, auto would let systemd decide. Keep yes for reliable pressure watch on Arch desktop.
DefaultMemoryPressureWatch=yes
"""

# ManagedOOMPreference=omit requires xattr and same UID owner for effective avoidance when parent is root-owned.
# For user services (UID 1000) under user@.service, root slice -.slice will ignore xattrs, but user-1000.slice calculation respects them.
OOM_SHIELD: Final[str] = """[Service]
OOMScoreAdjust=-100
OOMPolicy=continue
ManagedOOMPreference=omit
MemoryAccounting=yes
"""

OOMD_SERVICE_SHIELD: Final[str] = """[Service]
# Upstream oomd already ships -1000, this drop-in enforces it if vendor changes
OOMScoreAdjust=-1000
"""

CRITICAL_USER: Final[tuple[str, ...]] = (
    "pipewire.service", "wireplumber.service", "pipewire-pulse.service",
    "xdg-desktop-portal.service", "xdg-desktop-portal-hyprland.service",
    "xdg-desktop-portal-gtk.service", "dbus.service", "mako.service",
)

DUSKY_RUN_WRAPPER: Final[str] = """#!/bin/bash
# dusky-run v5 - unprivileged OOM score elevation for transient scopes
# systemd 261: OOMScoreAdjust in [Scope] defaults to inherit for scopes, so we set parent adj
# New process inherits parent's oom_score_adj (kernel proc docs). Unprivileged may only increase.
set -euo pipefail
if [[ $# -eq 0 ]]; then
  echo "usage: dusky-run <cmd> [args...]" >&2; exit 1
fi
# 200 = more killable. Increase allowed for unprivileged.
if ! printf '%d\\n' 200 > /proc/self/oom_score_adj 2>/dev/null; then
  echo "dusky-run: warning: cannot set oom_score_adj" >&2
fi
exec systemd-run --user --scope --slice=app.slice --collect \\
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
        FileSpec(dest=Path("/usr/local/bin/dusky-run"), content=DUSKY_RUN_WRAPPER, mode=0o755, desc="dusky-run v5 fixed"),
    ]
    for svc in CRITICAL_USER:
        s.append(FileSpec(dest=Path(f"/etc/systemd/user/{svc}.d/90-desktop-oom.conf"), content=OOM_SHIELD, desc=f"shield {svc} omit -100"))
    return s

def obsolete_paths() -> tuple[Path, ...]:
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
    fd, tmp_path_str = tempfile.mkstemp(dir=str(d.parent), prefix=f".{d.name}.tmp.")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(spec.content)
            if not spec.content.endswith('\n'):
                f.write('\n')
        os.chmod(tmp_path_str, spec.mode)
        content_equal = d.exists() and filecmp.cmp(tmp_path_str, str(d), shallow=False)
        mode_equal = d.exists() and (d.stat().st_mode & 0o777) == spec.mode
        if content_equal and mode_equal:
            return "up-to-date"
        if content_equal and not mode_equal:
            d.chmod(spec.mode)
            return "updated"
        os.replace(tmp_path_str, str(d))
        return "updated"
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
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
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    try:
        pw = pwd.getpwnam(sudo_user)
        uid = pw.pw_uid
        runtime = f"/run/user/{uid}"
        if not Path(runtime).is_dir():
            console.print(f"[yellow]No runtime dir {runtime}, skip user reload[/]")
            return
        env = {
            "XDG_RUNTIME_DIR": runtime,
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
        }
        # Prefer run0 (systemd 261) over runuser
        if shutil.which("run0"):
            cmd = ["run0", f"--user={sudo_user}", "systemctl", "--user", "daemon-reload"]
            result = subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                console.print(f"[yellow]run0 reload failed: {result.stderr.strip()}[/]")
            return
        # Fallback runuser - preserve env with -w
        subprocess.run(["runuser", "-u", sudo_user, "-w", "XDG_RUNTIME_DIR,DBUS_SESSION_BUS_ADDRESS", "--",
                        "systemctl", "--user", "daemon-reload"],
                       env={**os.environ, **env}, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        console.print(f"[yellow]user manager reload skipped: {e}[/]")

def main() -> None:
    ap = argparse.ArgumentParser(description="Deploy Hyprland OOM config (Arch latest, systemd 261+)")
    ap.add_argument("-n", "--dry-run", action="store_true", help="show what would change")
    args = ap.parse_args()

    if not args.dry_run and os.geteuid() != 0:
        console.print("[blue]Re-exec via sudo[/]")
        os.execvp("sudo", ["sudo", sys.executable, str(SELF_PATH), *sys.argv[1:]])

    if not Path("/sys/fs/cgroup/cgroup.controllers").exists():
        console.print("[red]cgroup v2 required for systemd-oomd[/]")
        sys.exit(1)

    console.print(Panel.fit("[bold cyan]Hyprland 0.55.4 + systemd 261.1 OOM fix v5 - audited[/]", box=box.DOUBLE))
    all_specs = specs()
    obsoletes = obsolete_paths()

    if args.dry_run:
        t = Table(box=box.SIMPLE_HEAVY)
        t.add_column("Action"); t.add_column("Dest"); t.add_column("Desc"); t.add_column("Mode")
        for x in all_specs:
            t.add_row("install", str(x.dest), x.desc, oct(x.mode))
        for p in obsoletes:
            if os.path.lexists(str(p)):
                t.add_row("remove", str(p), "obsolete", "-")
        console.print(t)
        return

    updated = 0
    with Progress(SpinnerColumn(), BarColumn(), TextColumn("{task.description}"), console=console) as prog:
        task = prog.add_task("Installing", total=len(all_specs))
        results: list[tuple[FileSpec, str]] = []
        for sp in all_specs:
            st = atomic_install(sp)
            results.append((sp, st))
            if st == "updated":
                updated += 1
            prog.advance(task)

    removed = 0
    for path in obsoletes:
        st = remove_if_present(path)
        if st == "removed":
            removed += 1

    for sp, st in results:
        col = "green" if st == "updated" else "dim"
        console.print(f"[{col}]{st.upper():11}[/] {sp.dest} [dim]({sp.desc})[/]")

    cmds = [
        ["systemctl", "daemon-reload"],
        ["systemctl", "unmask", "systemd-oomd"],
        ["systemctl", "enable", "--now", "systemd-oomd"],
        ["systemctl", "try-restart", "systemd-oomd"],
    ]
    for c in cmds:
        subprocess.run(c, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    reload_user_manager()

    console.print(Panel.fit(
        f"[bold green]✔ {updated} updated, {len(all_specs)-updated} up-to-date\n"
        f"✔ Removed {removed} obsolete files\n"
        f"✔ Keep in autostart: hyprland exec for choom if desired\n"
        f"✔ Verify: oomctl dump && systemctl status systemd-oomd\n"
        f"✔ Re-login required for DefaultOOMScoreAdjust to apply[/]",
        box=box.ROUNDED))

if __name__ == "__main__":
    main()
