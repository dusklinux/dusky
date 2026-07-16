#!/usr/bin/env python3
"""
040_disk_mount.py - DUSKY Final Fixed - Python 3.14.6 + Rich 15.0.0
Fixes:
 [2] removeprefix not lstrip - vda -> a bug
 [3] NOCOW: chattr +C alone, clear stale m flag then +C, no btrfs property compression none before +C
 [4] swapoff safe: scan /proc/swaps + swapon --raw, match /mnt/swap/swapfile and /swap/swapfile and basename swapfile under /mnt
 [6] EFI kept hardened fmask=0177,dmask=0077,noexec,nosuid,nodev
 [7] Panel width: Panel.fit + Align.center + safe_box=False fixes full-width +---+ ASCII
 [8] make_console: direct assignment os.environ["TERM"]="linux" not setdefault
 [9] Removed unreachable duplicate return
 [10] Tight centered banners for AUTONOMOUS / INTERACTIVE
 [11] Surgically fixed hidden directory shadowing on @home/.snapshots
 [12] Augmented run() wrapper to expose stderr on CalledProcessError
"""

from __future__ import annotations
import os, sys, re, json, shlex, signal, subprocess, tempfile, argparse
from pathlib import Path

def _ensure_rich():
    import importlib.util
    try:
        if importlib.util.find_spec("rich") is not None:
            return
    except ModuleNotFoundError:
        pass
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        print("python-rich missing", file=sys.stderr)
        sys.exit(1)
    print(">> Installing python-rich...", file=sys.stderr)
    subprocess.run(["pacman","-Sy","--needed","--noconfirm","python-rich"], stdout=sys.stderr, stderr=sys.stderr)

_ensure_rich()
from rich.console import Console
from rich.panel import Panel
from rich.align import Align
from rich.text import Text
from rich.prompt import Confirm, Prompt
from rich import box

def make_console():
    term = os.environ.get("TERM","")
    if term in ("dumb","unknown",""):
        os.environ["TERM"] = "linux"
        return Console(color_system=None, force_terminal=False, no_color=True, legacy_windows=False, safe_box=False)
    return Console(color_system="auto", force_terminal=None, legacy_windows=False, safe_box=False, highlight=False, markup=True)

def refresh_console():
    global console
    console = make_console()

console = make_console()

EFI_GPT_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
BTRFS_OPTS = "rw,noatime,compress=zstd:3,discard=async"
STATE_ENV = Path("/tmp/arch_install_state.env")
STATE_JSON = Path("/tmp/dusky_state.json")
DUSKY_EFI_LABEL = "DUSKY_EFI"
DUSKY_ROOT_LABEL = "DUSKY_ROOT"
SWAPFILE_PATH = Path("/mnt/swap/swapfile")
STD_SUBVOLS = ["@", "@home", "@snapshots", "@home_snapshots", "@var_log", "@var_cache", "@var_tmp", "@var_lib_machines", "@var_lib_portables"]
NOCOW_SUBVOLS = ["@var_lib_libvirt", "@var_lib_mysql", "@var_lib_postgres", "@swap"]
VALID_PART_RE = re.compile(r"^[a-zA-Z0-9_./-]+$")

def run(*cmd, check=True, capture=True, input_text=None, timeout=300):
    argv = [os.fspath(c) for c in cmd]
    try:
        if isinstance(input_text, (bytes, bytearray)):
            return subprocess.run(argv, check=check, text=False, capture_output=capture, input=bytes(input_text), timeout=timeout)
        elif isinstance(input_text, str):
            return subprocess.run(argv, check=check, text=True, capture_output=capture, input=input_text, timeout=timeout)
        return subprocess.run(argv, check=check, text=True, capture_output=capture, timeout=timeout)
    except subprocess.CalledProcessError as e:
        if check:
            console.print(f"[red]Failed {shlex.join([str(x) for x in argv])}[/red]")
            err = getattr(e, 'stderr', None)
            if err:
                if isinstance(err, bytes): err = err.decode('utf-8', 'replace')
                err = err.strip()
                if err: console.print(f"[red]Details: {err}[/red]")
        raise

def detect_boot_mode():
    return "UEFI" if Path("/sys/firmware/efi/efivars").is_dir() else "BIOS"

BOOT_MODE = detect_boot_mode()

def print_banner(title: str):
    txt = Text.from_markup(f"[bold cyan]{title}[/] [dim]{BOOT_MODE}[/]", justify="center")
    panel = Panel.fit(txt, box=box.ROUNDED, border_style="cyan", padding=(0,2))
    console.print(Align.center(panel))

def findmnt_json(target="/mnt"):
    try:
        r = run("findmnt","--json","--list","--submounts","--output","TARGET,SOURCE,FSTYPE,OPTIONS,ID","--target",target, check=False, capture=True)
        if r.returncode==0 and r.stdout.strip():
            return json.loads(r.stdout).get("filesystems",[])
    except:
        pass
    return []

def safe_deactivate_swaps():
    wanted = {"/mnt/swap/swapfile","/swap/swapfile"}
    candidates = set()
    try:
        r = run("swapon","--show=NAME","--raw","--noheadings", check=False, capture=True)
        candidates.update(l.strip() for l in r.stdout.splitlines() if l.strip())
    except:
        pass
    try:
        for line in Path("/proc/swaps").read_text().splitlines()[1:]:
            name = line.split()[0].strip()
            if name:
                candidates.add(name)
    except:
        pass
    for name in candidates:
        if name in wanted or (name.startswith("/mnt/") and name.endswith("swapfile")) or (Path(name).name=="swapfile" and (name.startswith("/mnt/") or name=="/swap/swapfile")):
            run("swapoff",name, check=False, capture=True)
    for p in wanted:
        run("swapoff",p, check=False, capture=True)

def unmount_mount_tree():
    try:
        r = run("swapon","--show=NAME","--raw","--noheadings", check=False, capture=True)
        for line in r.stdout.splitlines():
            n = line.strip()
            if not n:
                continue
            if Path(n).name=="swapfile" and (n in ("/mnt/swap/swapfile","/swap/swapfile") or n.startswith("/mnt/")):
                run("swapoff",n, check=False, capture=True)
        safe_deactivate_swaps()
    except:
        pass
    mnts = findmnt_json("/mnt")
    targets = []
    for fs in mnts:
        t = fs.get("target","")
        if t=="/mnt" or t.startswith("/mnt/"):
            targets.append(t)
    for mp in sorted(set(targets), key=lambda p:(p.count("/"),len(p)), reverse=True):
        try:
            run("umount","-R",mp, check=False, capture=True)
        except:
            pass
    try:
        r = run("findmnt","-rn","-o","TARGET", check=False, capture=True)
        remaining = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("/mnt")]
        for mp in sorted(remaining, key=lambda p:p.count("/"), reverse=True):
            run("umount",mp, check=False, capture=True)
    except:
        pass

def is_empty_dir(p: Path):
    try:
        if not p.is_dir():
            return False
        with os.scandir(p) as it:
            return next(it,None) is None
    except:
        return False

def ensure_subvolume(path: Path, nocow=False):
    if path.exists():
        r = run("btrfs","subvolume","show",str(path), check=False, capture=True)
        if r.returncode!=0:
            console.print(f"[red]{path} exists not subvol[/red]")
            sys.exit(1)
        existed=True
    else:
        run("btrfs","subvolume","create",str(path), capture=True)
        existed=False
    if nocow:
        try:
            run("chattr","-m",str(path), check=False, capture=True)
            run("btrfs","property","set",str(path),"compression","", check=False, capture=True)
        except:
            pass
        if not existed:
            run("chattr","+C",str(path), check=False, capture=True)
        elif is_empty_dir(path):
            run("chattr","+C",str(path), check=False, capture=True)

def load_state():
    state={}
    if STATE_JSON.exists():
        try:
            state.update(json.loads(STATE_JSON.read_text()))
        except:
            pass
    if STATE_ENV.exists():
        try:
            script=f'set +u; source {shlex.quote(str(STATE_ENV))} 2>/dev/null; printf "PROVISIONED_ROOT_PART=%s\\nPROVISIONED_EFI_PART=%s\\nENCRYPT_ROOT=%s\\n" "$PROVISIONED_ROOT_PART" "$PROVISIONED_EFI_PART" "$ENCRYPT_ROOT"'
            r=subprocess.run(["bash","-c",script],text=True,capture_output=True,check=False,timeout=5)
            for line in r.stdout.splitlines():
                if "=" not in line:
                    continue
                k,v=line.split("=",1)
                if not v:
                    continue
                if k=="PROVISIONED_ROOT_PART":
                    state.setdefault("root_part",v)
                elif k=="PROVISIONED_EFI_PART":
                    state.setdefault("efi_part",v)
                elif k=="ENCRYPT_ROOT":
                    state.setdefault("encrypt",v=="1")
        except:
            pass
    return state

def get_partition_path(disk,num):
    disk=disk.rstrip("/")
    if re.search(rf"p{num}$",disk):
        return disk
    name=Path(disk).name
    if re.search(r"(?:nvme\d+n\d+|mmcblk\d+|loop\d+|nbd\d+|pmem\d+)$",name) or (disk and disk[-1].isdigit()):
        return f"{disk}p{num}"
    return f"{disk}{num}"

def determine_root_partition(auto_mode):
    state=load_state()
    encrypt_hint=state.get("encrypt")
    has_mapper=Path("/dev/mapper/cryptroot").exists()
    use_crypt=False
    if isinstance(encrypt_hint,bool):
        use_crypt=encrypt_hint
    elif has_mapper:
        use_crypt=True
    if use_crypt:
        mapped=Path("/dev/mapper/cryptroot")
        if not mapped.exists():
            console.print("[red]LUKS expected no mapper[/red]")
            sys.exit(1)
        backing=""
        try:
            for dm in Path("/sys/class/block").iterdir():
                if not dm.name.startswith("dm-"):
                    continue
                try:
                    if (dm/"dm"/"name").read_text().strip()=="cryptroot":
                        slaves=list((dm/"slaves").iterdir())
                        if slaves:
                            backing=f"/dev/{slaves[0].name}"
                            break
                except:
                    continue
        except:
            pass
        if not backing:
            r=run("cryptsetup","status","cryptroot",check=False,capture=True)
            for line in r.stdout.splitlines():
                if line.strip().lower().startswith("device:"):
                    backing=line.split(":",1)[1].strip()
                    break
        if not backing:
            console.print("[red]No backing[/red]")
            sys.exit(1)
        root_part=Path(backing).resolve()
        mapped_root=mapped
    else:
        if auto_mode:
            prov=state.get("root_part")
            if prov and Path(prov).exists():
                root_part=Path(prov).resolve()
                mapped_root=root_part
            else:
                r=run("lsblk","-pnro","NAME,FSTYPE,LABEL",check=False,capture=True)
                btrfs_parts=[]
                duskies=[]
                for line in r.stdout.splitlines():
                    cols=line.split()
                    if len(cols)<2:
                        continue
                    name=cols[0]
                    fstype=cols[1]
                    label=cols[2] if len(cols)>2 else ""
                    if fstype=="btrfs":
                        if label==DUSKY_ROOT_LABEL:
                            duskies.append(name)
                        btrfs_parts.append(name)
                if len(duskies)==1:
                    root_part=Path(duskies[0]).resolve()
                    mapped_root=root_part
                elif len(btrfs_parts)==1:
                    root_part=Path(btrfs_parts[0]).resolve()
                    mapped_root=root_part
                else:
                    console.print("[red]Cannot auto-detect btrfs root[/red]")
                    sys.exit(1)
        else:
            r=run("lsblk","-l","-o","NAME,SIZE,TYPE,FSTYPE,LABEL,PARTLABEL",check=False,capture=True)
            console.print(r.stdout)
            while True:
                raw=Prompt.ask("Enter DUSKY BTRFS root (e.g. vda2)",console=console)
                if not VALID_PART_RE.match(raw):
                    console.print("[red]Invalid[/red]")
                    continue
                name=raw.removeprefix("/dev/")
                p=Path("/dev")/name
                try:
                    rp=p.resolve()
                    if not rp.exists():
                        console.print(f"[red]{rp} no exist[/red]")
                        continue
                    root_part=rp
                    mapped_root=rp
                    break
                except Exception as e:
                    console.print(f"[red]{e}[/red]")
    if not root_part.exists():
        console.print(f"[red]{root_part} invalid[/red]")
        sys.exit(1)
    try:
        r=run("lsblk","-ndlo","PKNAME",str(root_part),check=False,capture=True)
        pk=r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""
        if pk:
            root_disk=Path(f"/dev/{pk}").resolve()
        else:
            raise ValueError
    except:
        m=re.match(r"^(.*?)(?:p?\d+)$",root_part.name)
        if m:
            root_disk=Path(f"/dev/{m.group(1)}").resolve()
        else:
            console.print(f"[red]Failed parent disk[/red]")
            sys.exit(1)
    return mapped_root, root_part, root_disk

def validate_root_state(mapped_root):
    if not mapped_root.exists():
        console.print(f"[red]{mapped_root} not found[/red]")
        sys.exit(1)
    r=run("lsblk","-ndlo","FSTYPE",str(mapped_root),check=False,capture=True)
    if r.stdout.strip()!="btrfs":
        console.print(f"[red]{mapped_root} not btrfs[/red]")
        sys.exit(1)

def validate_efi_partition(part):
    r=run("lsblk","-ndlo","FSTYPE,PARTTYPE",str(part),check=False,capture=True)
    out=r.stdout.lower()
    if EFI_GPT_TYPE not in out and "vfat" not in out and "fat32" not in out:
        console.print(f"[red]{part} not ESP[/red]")
        sys.exit(1)

def is_mounted(dev):
    try:
        r=run("findmnt","-n","-o","TARGET","--source",dev,check=False,capture=True)
        return r.stdout.strip() or None
    except:
        return None

def auto_detect_efi_partition(root_disk,root_part):
    try:
        r=run("lsblk","--json","--paths","-o","NAME,TYPE,PARTTYPE,FSTYPE,PARTLABEL,LABEL",str(root_disk),check=False,capture=True)
        data=json.loads(r.stdout)
        bdevs=data.get("blockdevices",[])
        if not bdevs:
            return None
        children=bdevs[0].get("children",[]) if bdevs[0].get("type")=="disk" else bdevs
        guid=[]
        dusky=[]
        labelm=[]
        vfat=[]
        non_root=[]
        for ch in children:
            ptype=(ch.get("parttype") or "").lower()
            fstype=(ch.get("fstype") or "").lower()
            partlabel=ch.get("partlabel") or ""
            label=ch.get("label") or ""
            name=ch.get("path") or ch.get("name")
            if not name:
                continue
            pp=Path(name).resolve()
            if pp==root_part.resolve():
                continue
            if ch.get("type")!="part":
                continue
            non_root.append(pp)
            if ptype==EFI_GPT_TYPE:
                guid.append(pp)
                if label==DUSKY_EFI_LABEL or partlabel==DUSKY_EFI_LABEL:
                    dusky.append(pp)
            if "efi" in partlabel.lower():
                labelm.append(pp)
            if fstype in ("vfat","fat32"):
                vfat.append(pp)
        if len(dusky)==1:
            return dusky[0]
        if len(guid)==1:
            return guid[0]
        if len(labelm)==1:
            return labelm[0]
        if len(vfat)==1:
            return vfat[0]
        if len(non_root)==1:
            return non_root[0]
    except:
        pass
    return None

def prompt_for_efi_partition(root_disk):
    r=run("lsblk","-l","-o","NAME,SIZE,TYPE,FSTYPE,PARTTYPE,PARTLABEL,LABEL",str(root_disk),check=False,capture=True)
    console.print(r.stdout)
    while True:
        raw=Prompt.ask("Enter EFI partition (e.g. vda1)",console=console)
        if not VALID_PART_RE.match(raw):
            console.print("[red]Invalid[/red]")
            continue
        name=raw.removeprefix("/dev/")
        p=Path("/dev")/name
        try:
            rp=p.resolve()
            if rp.exists():
                return rp
            console.print(f"[red]{rp} no exist[/red]")
        except Exception as e:
            console.print(f"[red]{e}[/red]")

def determine_efi_partition(auto_mode,root_disk,root_part):
    if BOOT_MODE!="UEFI":
        return None
    state=load_state()
    if auto_mode:
        prov=state.get("efi_part")
        if prov and Path(prov).exists():
            console.print(f"[cyan]Auto EFI {prov}[/cyan]")
            return Path(prov).resolve()
        det=auto_detect_efi_partition(root_disk,root_part)
        if det:
            console.print(f"[cyan]Auto EFI {det}[/cyan]")
            return det
        console.print("[yellow]Cannot auto-detect EFI, prompting[/yellow]")
        return prompt_for_efi_partition(root_disk)
    else:
        return prompt_for_efi_partition(root_disk)

def construct_subvolume_matrix(mapped_root):
    console.print("[yellow]>> Constructing DUSKY Subvolume Matrix...[/yellow]")
    tmpdir=Path(tempfile.mkdtemp(prefix="dusky-btrfs-",dir="/tmp"))
    try:
        run("mount","-t","btrfs","-o","subvolid=5",str(mapped_root),str(tmpdir),capture=True)
        for sub in STD_SUBVOLS:
            ensure_subvolume(tmpdir/sub,nocow=False)
        for sub in NOCOW_SUBVOLS:
            ensure_subvolume(tmpdir/sub,nocow=True)
        console.print("[green]>> Matrix OK[/green]")
    finally:
        run("umount",str(tmpdir),check=False,capture=True)
        try:
            tmpdir.rmdir()
        except:
            pass

def assemble_fhs(mapped_root,efi_part):
    console.print("[yellow]>> Assembling FHS to /mnt...[/yellow]")
    Path("/mnt").mkdir(parents=True,exist_ok=True)
    run("mount","-o",f"{BTRFS_OPTS},subvol=@",str(mapped_root),"/mnt",capture=True)
    
    # Removed "home/.snapshots" from this loop to prevent creating a masked directory inside the @ subvolume root
    for mp in ["home",".snapshots","var/log","var/cache","var/tmp","var/lib/machines","var/lib/portables","var/lib/libvirt","var/lib/mysql","var/lib/postgres","swap","boot"]:
        Path(f"/mnt/{mp}").mkdir(parents=True,exist_ok=True)
        
    mounts=[
        (f"{BTRFS_OPTS},subvol=@home","/mnt/home"),
        (f"{BTRFS_OPTS},subvol=@snapshots","/mnt/.snapshots"),
        (f"{BTRFS_OPTS},subvol=@var_log","/mnt/var/log"),
        (f"{BTRFS_OPTS},subvol=@var_cache","/mnt/var/cache"),
        (f"{BTRFS_OPTS},subvol=@var_tmp","/mnt/var/tmp"),
        (f"{BTRFS_OPTS},subvol=@var_lib_machines","/mnt/var/lib/machines"),
        (f"{BTRFS_OPTS},subvol=@var_lib_portables","/mnt/var/lib/portables"),
        (f"{BTRFS_OPTS},subvol=@var_lib_libvirt","/mnt/var/lib/libvirt"),
        (f"{BTRFS_OPTS},subvol=@var_lib_mysql","/mnt/var/lib/mysql"),
        (f"{BTRFS_OPTS},subvol=@var_lib_postgres","/mnt/var/lib/postgres"),
        (f"{BTRFS_OPTS},subvol=@swap","/mnt/swap"),
    ]
    for opts,tgt in mounts:
        run("mount","-o",opts,str(mapped_root),tgt,capture=True)
        
    # Safely created over the active @home subvolume mount
    Path("/mnt/home/.snapshots").mkdir(parents=True,exist_ok=True)
    run("mount","-o",f"{BTRFS_OPTS},subvol=@home_snapshots",str(mapped_root),"/mnt/home/.snapshots",capture=True)
    
    if BOOT_MODE=="UEFI" and efi_part:
        console.print(f"[yellow]>> Mounting EFI {efi_part} to /mnt/boot (hardened)...[/yellow]")
        run("mount","-t","vfat","-o","fmask=0177,dmask=0077,noexec,nosuid,nodev",str(efi_part),"/mnt/boot",capture=True)

def initialize_swapfile():
    console.print("[yellow]>> Ensuring 4GB swapfile...[/yellow]")
    try:
        r=run("swapon","--show=NAME","--raw","--noheadings",check=False,capture=True)
        for line in r.stdout.splitlines():
            n=line.strip()
            if not n:
                continue
            if Path(n).name=="swapfile" and (n in ("/mnt/swap/swapfile","/swap/swapfile") or n.startswith("/mnt/")):
                run("swapoff",n,check=False,capture=True)
        try:
            swaps=Path("/proc/swaps").read_text()
            for line in swaps.splitlines()[1:]:
                name=line.split()[0]
                if Path(name).name=="swapfile":
                    run("swapoff",name,check=False,capture=True)
        except:
            pass
    except:
        pass
    if SWAPFILE_PATH.exists() and not SWAPFILE_PATH.is_file():
        console.print(f"[red]{SWAPFILE_PATH} not regular file[/red]")
        sys.exit(1)
    if SWAPFILE_PATH.is_file():
        try:
            if SWAPFILE_PATH.stat().st_size==4*1024**3:
                if run("swapon",str(SWAPFILE_PATH),check=False,capture=True).returncode==0:
                    console.print("[green]>> Swap re-activated[/green]")
                    return
        except:
            pass
        try:
            SWAPFILE_PATH.unlink()
        except:
            pass
    run("btrfs","filesystem","mkswapfile","--size","4G","--uuid","clear",str(SWAPFILE_PATH),capture=True)
    run("swapon",str(SWAPFILE_PATH),capture=True)

def teardown_state():
    try:
        safe_deactivate_swaps()
    except:
        pass
    unmount_mount_tree()

def run_common(auto_mode):
    teardown_state()
    mapped_root,root_part,root_disk=determine_root_partition(auto_mode)
    validate_root_state(mapped_root)
    efi_part=None
    if BOOT_MODE=="UEFI":
        efi_part=determine_efi_partition(auto_mode,root_disk,root_part)
        if efi_part:
            efi_part=efi_part.resolve()
            validate_efi_partition(efi_part)
            tmp_obj=None
            try:
                mnt=is_mounted(str(efi_part))
                tp=mnt
                if not mnt:
                    tmp_obj=tempfile.TemporaryDirectory(prefix="dusky_efi_check_")
                    tp=tmp_obj.name
                    run("mount","--mkdir","-t","vfat","-o","ro,noexec,nosuid,nodev",str(efi_part),tp,check=False,capture=True)
                if tp and Path(tp,"EFI","Microsoft").is_dir():
                    console.print(Align.center(Panel.fit(f"[cyan]Dual-boot Windows on {efi_part}, preserving[/cyan]", box=box.ROUNDED, border_style="cyan")))
                if tmp_obj:
                    run("umount",tp,check=False,capture=True)
            except:
                try:
                    if tmp_obj:
                        run("umount",tp,check=False,capture=True)
                except:
                    pass
            finally:
                try:
                    if tmp_obj:
                        tmp_obj.cleanup()
                except:
                    pass
    construct_subvolume_matrix(mapped_root)
    assemble_fhs(mapped_root,efi_part)
    initialize_swapfile()
    console.print(Align.center(Panel.fit("[bold green]>> DUSKY Setup Complete[/bold green]", box=box.ROUNDED, border_style="green")))
    try:
        r=run("lsblk","-l","-f",str(root_disk),check=False,capture=True)
        console.print(Align.center(Panel.fit(r.stdout, title=f"lsblk {root_disk}", box=box.ROUNDED, border_style="dim")))
    except:
        pass
    try:
        r=run("findmnt","-R","/mnt",check=False,capture=True)
        console.print(Align.center(Panel.fit(r.stdout, title="findmnt /mnt", box=box.ROUNDED, border_style="dim")))
    except:
        pass

def run_auto_mode():
    print_banner("AUTONOMOUS DUSKY BTRFS MOUNT")
    run_common(True)

def run_interactive_mode():
    print_banner("INTERACTIVE DUSKY BTRFS MOUNT")
    run_common(False)

def main():
    parser=argparse.ArgumentParser(description="DUSKY 040 - BTRFS Mount")
    parser.add_argument("--auto",action="store_true")
    args=parser.parse_args()
    if hasattr(os,"geteuid") and os.geteuid()!=0:
        console.print("[red]Need root[/red]")
        sys.exit(1)
    if not args.auto and not sys.stdin.isatty():
        console.print("[red]Need TTY or --auto[/red]")
        sys.exit(1)
    def _h(sig,frame):
        console.print(f"\n[yellow]Signal {signal.Signals(sig).name}[/yellow]")
        teardown_state()
        sys.exit(128+sig)
    signal.signal(signal.SIGINT,_h)
    signal.signal(signal.SIGTERM,_h)
    try:
        if args.auto:
            run_auto_mode()
        else:
            if Confirm.ask("Run AUTONOMOUS?",console=console,default=True):
                run_auto_mode()
            else:
                run_interactive_mode()
    except KeyboardInterrupt:
        console.print("[red]Interrupted[/red]")
        teardown_state()
        sys.exit(130)
    except Exception as e:
        console.print(f"[red]Fatal {e}[/red]")
        import traceback
        traceback.print_exc()
        teardown_state()
        sys.exit(1)

if __name__=="__main__":
    main()
