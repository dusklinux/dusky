#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
035_configure_hyprland_gpu.py
Arch / Hyprland GPU Configurator -> ~/.config/hypr/gpu.lua
v2026.07-Final | Python 3.14.6 | systemd 261 | Hyprland 0.55.4+

Rewrites 035_configure_uwsm_gpu.sh (bash) to robust Python.
- Topology: /sys/class/drm/card* + pyudev bridge + boot_vga
- Vendors: 0x8086 Intel, 0x1002 AMD, 0x10de NVIDIA + VM vendors
- VA-API probe: /usr/lib/dri/*_drv_video.so
- Output: pure Lua hl.env() with dynamic by-path resolve_card() helper
- Atomic: temp in same dir, 0644, identical check, fsync, chown fix for sudo
- UI: Rich beautiful TUI, manual menu + --auto fallback
- Deps: auto-installs python-rich, python-pyudev, pciutils via pacman -S --needed
"""

# ── 1. Rich bootstrap (like systemd-oomd script) ──
import os, sys, shutil, subprocess
try:
    import rich # noqa: F401
except ImportError:
    pm = shutil.which("pacman")
    if not pm:
        print("pacman not found, install python-rich manually"); sys.exit(1)
    cmd = [pm, "-S", "--needed", "--noconfirm", "python-rich"]
    if os.geteuid()!= 0 and shutil.which("sudo"):
        subprocess.run(["sudo", "-v"], check=False) # prompts password
        cmd = ["sudo"] + cmd
    print(f"[BOOTSTRAP] Installing python-rich: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    os.execv(sys.executable, [sys.executable] + sys.argv)

import argparse, glob, pwd, re, tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()
try:
    import pyudev
    HAS_PYUDEV = True
except ImportError:
    HAS_PYUDEV = False

# ── 2. Privilege & package helpers ──
def real_home() -> Path:
    if "SUDO_USER" in os.environ:
        try: return Path(pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir)
        except: return Path(f"/home/{os.environ['SUDO_USER']}")
    return Path.home()

XDG = Path(os.environ.get("XDG_CONFIG_HOME") or real_home() / ".config")
OUT_DEFAULT = XDG / "hypr" / "gpu.lua"
DRI_DIRS = [Path("/usr/lib/dri"), Path("/usr/lib64/dri")]

def ensure_sudo() -> bool:
    if os.geteuid() == 0: return True
    if not shutil.which("sudo"): return False
    try:
        subprocess.run(["sudo", "-v"], check=True)
        return True
    except subprocess.CalledProcessError:
        return False

def pacman_install(pkgs: List[str]) -> bool:
    if not pkgs: return True
    pm = shutil.which("pacman")
    if not pm:
        console.print("[red]pacman not found[/]"); return False
    if os.geteuid()!= 0:
        if not ensure_sudo(): return False
        cmd = ["sudo", pm, "-S", "--needed", "--noconfirm"] + pkgs
    else:
        cmd = [pm, "-S", "--needed", "--noconfirm"] + pkgs
    console.print(f"[yellow]Installing {', '.join(pkgs)}...[/]")
    try: subprocess.run(cmd, check=True); return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]pacman failed: {e}[/]"); return False

def ensure_bin(bin_name: str, pkg: str) -> bool:
    if shutil.which(bin_name): return True
    return pacman_install([pkg])

def ensure_py_module(mod: str, pkg: str) -> bool:
    try: __import__(mod); return True
    except ImportError:
        if pacman_install([pkg]):
            try: __import__(mod); return True
            except ImportError: return False
        return False

def fix_ownership(p: Path):
    if os.geteuid()==0 and "SUDO_USER" in os.environ:
        try:
            u=pwd.getpwnam(os.environ["SUDO_USER"])
            os.chown(p, u.pw_uid, u.pw_gid)
        except Exception: pass

# ── 3. Data model ──
VENDOR_MAP = {
    "0x8086":"Intel","0x1002":"AMD","0x10de":"NVIDIA",
    "0x1af4":"RedHat VirtIO","0x15ad":"VMware","0x80ee":"VirtualBox",
    "0x1234":"QEMU Bochs","0x1414":"Hyper-V","0x1b36":"RedHat QXL","0x1013":"Cirrus",
}
@dataclass(slots=True)
class Gpu:
    dev_node: str; pci: str; vendor_id: str; vendor_label: str
    name: str; boot_vga: int; driver: str; by_path: str; is_real: bool

def sh(cmd: List[str]) -> str:
    try: return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except: return ""

def vendor_label(v: str) -> str:
    return VENDOR_MAP.get(v.lower(), f"Vendor {v}")

def pci_name(pci: str) -> str:
    if shutil.which("lspci"):
        out=sh(["lspci","-s",pci])
        if out:
            m=re.match(r"^[0-9a-fA-F:.]+ [^:]+: (.+)$", out)
            return m.group(1) if m else out
    # fallback: /sys label
    lp=Path(f"/sys/bus/pci/devices/{pci}/label")
    if lp.exists():
        try: return lp.read_text().strip()
        except: pass
    return "Unknown PCI Device"

def find_vendor_dir(start: Path) -> Optional[Path]:
    cur=start.resolve()
    for _ in range(10):
        if (cur/"vendor").exists(): return cur
        if cur==cur.parent: break
        cur=cur.parent
    return None

# ── 4. Topology discovery ──
def detect() -> List[Gpu]:
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as prog:
        prog.add_task("Scanning /sys/class/drm/card* + udev", total=None)
        raw=[]
        for s in glob.glob("/sys/class/drm/card[0-9]*"):
            p=Path(s)
            if not re.fullmatch(r"card\d+", p.name): continue
            dev=f"/dev/dri/{p.name}"
            if not Path(dev).exists(): continue
            try: sys_dev=Path(os.path.realpath(p/"device"))
            except: continue
            vdir=find_vendor_dir(sys_dev)
            if not vdir:
                drv="unknown"
                try: drv=Path(os.path.realpath(sys_dev/"driver")).name
                except: pass
                raw.append(Gpu(dev,f"platform:{p.name}","0x0000","Platform",f"Platform {p.name}",0,drv,"unavailable",drv!="simpledrm"))
                continue
            try: vid=vdir.joinpath("vendor").read_text().strip().lower()
            except: continue
            pci=vdir.name
            boot=0
            for bp in [vdir/"boot_vga", sys_dev/"boot_vga"]:
                if bp.exists():
                    try: boot=int(bp.read_text().strip())
                    except: boot=0
                    break
            drv="unknown"
            for d in [vdir/"driver", sys_dev/"driver"]:
                if d.exists():
                    try: drv=Path(os.path.realpath(d)).name
                    except: pass
                    break
            by="unavailable"
            for l in glob.glob(f"/dev/dri/by-path/pci-{pci}*card"):
                if Path(l).exists(): by=l; break
            raw.append(Gpu(dev,pci,vid,vendor_label(vid),pci_name(pci),boot,drv,by,drv!="simpledrm"))

    if not raw:
        console.print("[red]No DRM nodes - check KMS or run after first boot[/]"); raise SystemExit(1)

    # pyudev enrichment for ID_PATH -> more stable by-path
    if HAS_PYUDEV:
        try:
            ctx=pyudev.Context()
            for d in ctx.list_devices(subsystem='drm', DEVTYPE='drm_minor'):
                if not d.sys_name.startswith("card"): continue
                node=d.device_node
                idp=d.get("ID_PATH")
                if idp and node:
                    cand=f"/dev/dri/by-path/{idp}-card"
                    for c in raw:
                        if c.dev_node==node and Path(cand).exists(): c.by_path=cand
        except Exception: pass

    real=[c for c in raw if c.is_real]
    return sorted(real if real else raw, key=lambda c: c.pci)

def default_gpu(cards: List[Gpu]):
    boots=[c for c in cards if c.boot_vga==1]
    if not boots: return cards[0], "No boot_vga, lowest PCI"
    if len(boots)==1: return boots[0], "boot_vga"
    return sorted(boots, key=lambda c: c.pci)[0], "Multiple boot_vga, lowest PCI"

def select_gpu(cards: List[Gpu], auto: bool):
    def_card, reason = default_gpu(cards)
    tbl=Table(title=f"GPU Topology - default {def_card.dev_node} ({reason})", show_header=True, header_style="bold magenta")
    for h in ["#","Node","Vendor","Name","PCI","Driver","Flags"]: tbl.add_column(h)
    for i,c in enumerate(cards,1):
        flags=[]
        if c.boot_vga: flags.append("[yellow]boot_vga[/]")
        if c.dev_node==def_card.dev_node: flags.append("[green]default[/]")
        if not c.is_real: flags.append("[dim]simpledrm/virt[/]")
        tbl.add_row(str(i),c.dev_node,c.vendor_label,c.name[:50],c.pci,c.driver," ".join(flags))
    console.print(tbl)
    if len(cards)==1 or auto or not os.isatty(0):
        return def_card, "auto" if auto else "single"
    idx=str(cards.index(def_card)+1)
    ans=Prompt.ask("Primary GPU", choices=[str(i) for i in range(1,len(cards)+1)], default=idx)
    try: return cards[int(ans)-1], "manual"
    except: return def_card, "manual"

# ── 5. VA-API probe ──
def probe_drivers():
    found={}
    for k in ["nvidia","nouveau","iHD","i965","radeonsi"]:
        name = f"{k}_drv_video.so" if k in ("iHD","i965") else f"{k}_drv_video.so" if k!="radeonsi" else "radeonsi_drv_video.so"
        if k=="radeonsi": name="radeonsi_drv_video.so"
        found[k]=any((d/name).exists() for d in DRI_DIRS)
    return found

# ── 6. Lua generation ──
def gen_lua(primary: Gpu, ordered: List[Gpu], mode: str) -> str:
    pr=probe_drivers()
    L=[]
    L.append("-- -----------------------------------------------------------------")
    L.append(f"-- Hyprland GPU | Mode: {mode.upper()} | Primary: {primary.dev_node}")
    L.append(f"-- GPU: {primary.vendor_label} | {primary.name} | {primary.pci} | drv:{primary.driver}")
    L.append("-- Gen: Python 3.14.6 + Rich + pyudev | systemd 261 | Hyprland 0.55.4+ Lua")
    L.append("-- DRM nodes shift per reboot - resolved via stable by-path")
    L.append("-- -----------------------------------------------------------------")
    # Required helper from prompt - robust for-loop version
    L.append("local function resolve_card(pci_address, fallback)")
    L.append(" local cmd = \"for d in /dev/dri/by-path/pci-\"..pci_address..\"*card; do [ -e \\\"$d\\\" ] && readlink -f \\\"$d\\\" 2>/dev/null && break; done\"")
    L.append(" local h = io.popen(cmd)")
    L.append(" if h then")
    L.append(" local path = h:read(\"*l\")")
    L.append(" h:close()")
    L.append(" if path and path ~= \"\" then return path end")
    L.append(" end")
    L.append(" return fallback")
    L.append("end")
    L.append("")
    parts=[]
    for c in ordered:
        if c.pci.startswith("platform:"): parts.append(f'"{c.dev_node}"')
        else: parts.append(f'resolve_card("{c.pci}", "{c.dev_node}")')
    L.append(f'hl.env("AQ_DRM_DEVICES", { ".. \":\".. ".join(parts) })')
    L.append("")
    vid=primary.vendor_id.lower(); drv=primary.driver.lower(); vlabel=primary.vendor_label.lower()
    match vid:
        case "0x8086" | _ if "intel" in vlabel:
            L.append("-- Intel")
            if pr["iHD"]: L.append('hl.env("LIBVA_DRIVER_NAME", "iHD")')
            elif pr["i965"]: L.append('hl.env("LIBVA_DRIVER_NAME", "i965")')
        case "0x1002" | _ if "amd" in vlabel or "radeon" in primary.name.lower() or "amd" in primary.name.lower():
            L.append("-- AMD")
            if pr["radeonsi"]: L.append('hl.env("LIBVA_DRIVER_NAME", "radeonsi")')
        case "0x10de":
            tgt=drv
            if tgt not in ("nvidia","nouveau") and Path("/usr/lib/gbm/nvidia-drm_gbm.so").exists():
                tgt="nvidia"
            if tgt=="nvidia":
                L.append("-- NVIDIA Proprietary")
                L.append('hl.env("GBM_BACKEND", "nvidia-drm")')
                L.append('hl.env("__GLX_VENDOR_LIBRARY_NAME", "nvidia")')
                if pr["nvidia"]: L.append('hl.env("LIBVA_DRIVER_NAME", "nvidia")')
            elif tgt=="nouveau":
                L.append("-- NVIDIA Nouveau")
                L.append('hl.env("MESA_LOADER_DRIVER_OVERRIDE", "nouveau")')
                if pr["nouveau"]: L.append('hl.env("LIBVA_DRIVER_NAME", "nouveau")')
            else:
                L.append(f"-- NVIDIA unknown driver {drv}")
        case _:
            if pr["radeonsi"] and "amd" in primary.name.lower():
                L.append('hl.env("LIBVA_DRIVER_NAME", "radeonsi")')
            L.append(f"-- Generic/VM ({primary.vendor_label}) - by-path only")
    L.append("")
    return "\n".join(L)

def atomic_write(p: Path, data: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        try:
            if p.read_text(encoding="utf-8")==data:
                console.print(f"[green][OK] Up to date: {p}[/]"); return
        except: pass
    fd,tmp=tempfile.mkstemp(dir=str(p.parent), prefix=".gpu.")
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp,0o644)
        Path(tmp).replace(p)
        fix_ownership(p)
        console.print(f"[green][OK] Atomically written {p}[/]")
    finally:
        try: Path(tmp).unlink(missing_ok=True)
        except: pass

def main():
    ap=argparse.ArgumentParser(description="Generate ~/.config/hypr/gpu.lua")
    ap.add_argument("--auto", action="store_true", help="Non-interactive")
    ap.add_argument("--output", type=Path, default=None)
    args=ap.parse_args()

    # Ensure binary + python deps (auto-elevates with sudo -v prompt)
    ensure_bin("lspci","pciutils")
    if not HAS_PYUDEV:
        ensure_py_module("pyudev","python-pyudev")

    out = args.output or (real_home() / ".config" / "hypr" / "gpu.lua")
    console.print(Panel.fit(f"Hyprland GPU Configurator\nPython {'.'.join(map(str, sys.version_info[:3]))} | {Path('/etc/arch-release').exists() and 'Arch' or 'Linux'} | Hyprland 0.55.4+ | systemd 261+", style="bold cyan"))

    cards=detect()
    primary,mode=select_gpu(cards, args.auto)
    ordered=[primary]+[c for c in cards if c.dev_node!=primary.dev_node]
    lua=gen_lua(primary, ordered, mode)
    atomic_write(out, lua)

    console.print("\n[bold]Preview:[/]")
    for l in lua.splitlines():
        if any(k in l for k in ("AQ_DRM","LIBVA","GBM_","__GLX","MESA_")):
            console.print(f" {l}")
    console.print(Panel('pcall(require, "gpu")', title="Add to hyprland.lua", border_style="green"))
    console.print(f"File: [cyan]{out}[/] -> [bold]hyprctl reload[/] or relogin")

if __name__=="__main__":
    try: main()
    except KeyboardInterrupt:
        console.print("\n[red]Aborted[/]"); raise SystemExit(130)
