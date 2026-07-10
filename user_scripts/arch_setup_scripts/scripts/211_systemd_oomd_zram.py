#!/usr/bin/env python3
"""
Pure Hyprland OOM configurator - final verified version
"""
from __future__ import annotations
import os, sys, subprocess, signal, tempfile, shutil, filecmp, re, pwd
from pathlib import Path
from dataclasses import dataclass
from typing import Final, NoReturn
SELF_PATH: Final[Path] = Path(__file__).resolve()
def _bootstrap_rich() -> None:
    try:
        import rich
        return
    except ImportError:
        pass
    qi = subprocess.run(["pacman","-Qi","python-rich"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if qi.returncode == 0:
        os.execv(sys.executable, [sys.executable, str(SELF_PATH), *sys.argv[1:]])
    print("[bootstrap] installing python-rich")
    try:
        subprocess.run(["sudo","pacman","-S","--needed","--noconfirm","python-rich"], check=True)
    except FileNotFoundError:
        subprocess.run(["pacman","-S","--needed","--noconfirm","python-rich"], check=True)
    os.execv(sys.executable, [sys.executable, str(SELF_PATH), *sys.argv[1:]])
_bootstrap_rich()
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box
console: Final[Console] = Console()
OOM_RULE_DIR: Final[Path] = Path("/etc/systemd/oomd/rules.d")
PRESSURE_RULE_CONTENT: Final[str] = "[Rule]\nMemoryPressureAbove=10%\nAction=kill-by-pgscan\nLastingSec=2s\n"
SWAP_RULE_CONTENT: Final[str] = "[Rule]\nSwapUsageMax=90%\nAction=kill-by-swap\nLastingSec=2s\n"
APP_SLICE_CONTENT: Final[str] = "[Slice]\nManagedOOMMemoryPressure=kill\nManagedOOMSwap=kill\nOOMRules=10-zram-desktop-pressure 10-zram-desktop-swap\n"
SESSION_SLICE_CONTENT: Final[str] = "[Slice]\nManagedOOMPreference=avoid\n"
USER_SERVICE_CONTENT: Final[str] = "[Service]\nOOMScoreAdjust=-500\n"
USER_CONF_CONTENT: Final[str] = "[Manager]\nDefaultOOMScoreAdjust=200\n"
OOM_SHIELD_CONTENT: Final[str] = "[Service]\nOOMScoreAdjust=-500\nOOMPolicy=continue\n"
CRITICAL_USER_SERVICES: Final[tuple[str, ...]] = ("wireplumber.service","pipewire.service","pipewire-pulse.service","xdg-desktop-portal.service","xdg-desktop-portal-gtk.service","xdg-desktop-portal-hyprland.service","dbus-broker.service","dbus.service","mako.service",)
CRITICAL_SYSTEM_SERVICES: Final[tuple[str, ...]] = ("systemd-logind.service","NetworkManager.service","polkit.service","systemd-resolved.service","systemd-timesyncd.service","getty@.service","udisks2.service","systemd-userdbd.service",)
@dataclass(frozen=True, slots=True, kw_only=True)
class FileSpec:
    dest: Path
    content: str
    mode: int = 0o644
    description: str
def build_specs() -> list[FileSpec]:
    specs: list[FileSpec] = [
        FileSpec(dest=OOM_RULE_DIR / "10-zram-desktop-pressure.oomrule", content=PRESSURE_RULE_CONTENT, description="ZRAM PSI pressure rule"),
        FileSpec(dest=OOM_RULE_DIR / "10-zram-desktop-swap.oomrule", content=SWAP_RULE_CONTENT, description="ZRAM swap backstop"),
        FileSpec(dest=Path("/etc/systemd/user/app.slice.d/10-oomd.conf"), content=APP_SLICE_CONTENT, description="Monitor app.slice"),
        FileSpec(dest=Path("/etc/systemd/user/session.slice.d/10-oomd-avoid.conf"), content=SESSION_SLICE_CONTENT, description="Protect session.slice"),
        FileSpec(dest=Path("/etc/systemd/system/user@.service.d/10-oom-score.conf"), content=USER_SERVICE_CONTENT, description="user@.service -500"),
        FileSpec(dest=Path("/etc/systemd/user.conf.d/10-oom-default.conf"), content=USER_CONF_CONTENT, description="DefaultOOMScoreAdjust=200"),
    ]
    for svc in CRITICAL_USER_SERVICES:
        specs.append(FileSpec(dest=Path(f"/etc/systemd/user/{svc}.d/10-oom-shield.conf"), content=OOM_SHIELD_CONTENT, description=f"Shield {svc}"))
    for svc in CRITICAL_SYSTEM_SERVICES:
        specs.append(FileSpec(dest=Path(f"/etc/systemd/system/{svc}.d/10-oom-shield.conf"), content=OOM_SHIELD_CONTENT, description=f"Shield {svc}"))
    return specs
def print_help() -> None:
    txt = Text()
    txt.append("Platinum OOM/ZRAM — Pure Hyprland 0.55.4\n", style="bold cyan")
    txt.append("Usage: 212_pure_hyprland_final.py [OPTIONS]\n", style="bold")
    txt.append("  -n, --dry-run  dry run\n  -h, --help     help\n")
    console.print(Panel(txt, title="Help", border_style="blue", box=box.ROUNDED))
def die(msg: str, code: int = 1) -> NoReturn:
    console.print(f"[bold red][ERROR][/] {msg}"); sys.exit(code)
def ensure_root(dry_run: bool) -> None:
    if dry_run or os.geteuid() == 0:
        return
    console.print("[bold blue][INFO][/] Root required — re-executing via sudo...")
    if shutil.which("sudo") is None:
        die("sudo not found")
    os.execvp("sudo", ["sudo", sys.executable, str(SELF_PATH), *sys.argv[1:]])
def atomic_install(spec: FileSpec) -> str:
    dest = spec.dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(dest.parent, 0o755)
    except PermissionError:
        pass
    fd, tmp_str = tempfile.mkstemp(dir=str(dest.parent))
    tmp_path = Path(tmp_str)
    try:
        tmp_path.write_text(spec.content, encoding="utf-8")
        os.chmod(tmp_path, spec.mode)
        if dest.exists() and filecmp.cmp(str(tmp_path), str(dest), shallow=False):
            return "up-to-date"
        shutil.move(str(tmp_path), str(dest))
        os.chmod(dest, spec.mode)
        return "updated"
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        try:
            os.close(fd)
        except Exception:
            pass
def dry_run_display(specs: list[FileSpec]) -> None:
    console.rule("[bold cyan]DRY RUN", style="cyan")
    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("Destination", style="cyan")
    table.add_column("Description")
    for s in specs:
        table.add_row(str(s.dest), s.description)
    console.print(table)
    for core in specs[:4]:
        console.print(Panel(core.content, title=str(core.dest), border_style="green"))
def enable_oomd() -> None:
    steps = [(["systemctl","unmask","systemd-oomd.service","systemd-oomd.socket"],"Unmask"),(["systemctl","enable","systemd-oomd.service","systemd-oomd.socket"],"Enable"),(["systemctl","restart","systemd-oomd.service","systemd-oomd.socket"],"Restart"),(["systemctl","daemon-reload"],"Reload")]
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console, transient=True) as prog:
        for cmd, desc in steps:
            t = prog.add_task(desc)
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                console.print(f"[bold green][OK][/] {desc}")
            except subprocess.CalledProcessError as e:
                console.print(f"[bold yellow][WARN][/] {desc}: {e}")
            prog.remove_task(t)
def reload_user_managers() -> None:
    try:
        r = subprocess.run(["systemctl","list-units","--type=service","--state=active","--plain","user@*.service"], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        return
    pat = re.compile(r"user@(\d+)\.service")
    for line in r.stdout.splitlines():
        if m := pat.search(line):
            uid = int(m.group(1))
            try:
                user = pwd.getpwuid(uid).pw_name
            except KeyError:
                continue
            try:
                subprocess.run(["systemctl","--user","-M",f"{user}@", "daemon-reload"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                console.print(f"[bold green][OK][/] Reloaded {user}")
            except Exception:
                pass
def handle_signals() -> None:
    def _h(s,_f): console.print(f"\n[bold red]Interrupted {s}[/]"); sys.exit(130)
    signal.signal(signal.SIGINT, _h); signal.signal(signal.SIGTERM, _h)
def main() -> None:
    handle_signals()
    dry_run=False
    for arg in sys.argv[1:]:
        match arg:
            case "--dry-run" | "-n": dry_run=True
            case "--help" | "-h": print_help(); sys.exit(0)
            case _ if arg.startswith("-"): console.print(f"[yellow]Ignoring {arg}[/]")
    ensure_root(dry_run)
    console.print(Panel.fit("[bold cyan]Platinum systemd-oomd 261 — Pure Hyprland 0.55.4[/]", border_style="cyan", box=box.DOUBLE))
    specs=build_specs()
    if dry_run:
        dry_run_display(specs); return
    updated=up_to_date=0
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as prog:
        t=prog.add_task(f"Installing {len(specs)}", total=len(specs))
        for s in specs:
            st=atomic_install(s)
            match st:
                case "updated": console.print(f"[green][UPDATED][/] {s.dest}"); updated+=1
                case "up-to-date": console.print(f"[dim][OK][/] {s.dest} up-to-date"); up_to_date+=1
            prog.advance(t)
    console.print(Panel(f"{updated} updated {up_to_date} up-to-date total {len(specs)}", title="Summary", border_style="green"))
    enable_oomd(); reload_user_managers()
    console.print(Panel.fit("[bold green]✔ Deployed — Pure Hyprland (no UWSM)[/]", border_style="green"))
if __name__=="__main__": main()
