#!/usr/bin/env python3
# DUSKY_INTERACTIVE=true
"""
030_partitioning.py - DUSKY Final Fixed - Python 3.14.6 + Rich 15.0.0 + systemd 261 + util-linux 2.42.1
All fixes integrated July 2026:
 [1] sfdisk --lock=yes single token
 [2] removeprefix("/dev/") not lstrip
 [3] BTRFS NOCOW: chattr +C alone
 [4] swapoff safe for /mnt/swap/swapfile
 [5] mkfs.btrfs crc32c modern defaults
 [6] EFI hardened fmask=0177,dmask=0077,noexec,nosuid,nodev
 [7] Panel width: Panel.fit + Align.center + safe_box=False fixes +---+ ASCII
 [8] Arrow menus: termios raw with Rich fallback, no extra deps, handles CSI + SS3
 [9] cfdisk TTY wrapper: /dev/tty passthrough, TERM sanitization, stty sane, returncode check, fdisk fallback
 [10] EFI default 1.3G decimal support
 [11] Fixed SyntaxError global console
 [12] Preserved cfdisk -> fdisk fallback
 [13] Fixed or True EFI filter bug
 [14] Fixed force_terminal ANSI log pollution -> auto-detect None
 [15] Fixed Ctrl+C terminal destruction -> SIG_IGN in parent + DFL in child via preexec
 [16] Fixed abort trap -> q/Esc exits cleanly
 [17] Fixed 10-item number lockout -> multi-digit buffer + Enter
 [18] Fixed dumb TERM setdefault -> direct assignment
 [19] FIXED duplicate numbering in arrow_menu fallback
 [20] ADDED recursive loop structure enabling safe return / back operations
"""

from __future__ import annotations
import os, sys, re, json, time, shlex, shutil, getpass, signal, argparse, subprocess, tempfile, select
from pathlib import Path
from typing import Literal, List, Tuple, Optional

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
    try:
        du = shutil.disk_usage("/run/archiso/cowspace")
        if du.free < 250*1024*1024:
            subprocess.run(["mount","-o","remount,size=2G","/run/archiso/cowspace"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except:
        pass
    print(">> Installing python-rich...", file=sys.stderr)
    subprocess.run(["pacman","-Sy","--needed","--noconfirm","python-rich"], stdout=sys.stderr, stderr=sys.stderr)

_ensure_rich()
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.align import Align
from rich.text import Text
from rich.prompt import Confirm, Prompt
from rich import box

def make_console():
    term = os.environ.get("TERM","")
    if term in ("dumb","unknown",""):
        os.environ["TERM"] = "linux"
        return Console(color_system="truecolor", force_terminal=None, legacy_windows=False, safe_box=False, highlight=False, markup=True)
    return Console(color_system="auto", force_terminal=None, legacy_windows=False, safe_box=False, highlight=False, markup=True)

console = make_console()

def refresh_console():
    global console
    console = make_console()

TARGET_CRYPT_NAME = "cryptroot"
EFI_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
BIOS_GUID = "21686148-6449-6e6f-744e-656564454649"
LINUX_ROOT_GUID = "0fc63daf-8483-4772-8e79-3d69d8477de4"
LUKS_GUID = "ca7d7ccb-63ed-4c53-861c-1742536059cc"
DUSKY_EFI_LABEL = "DUSKY_EFI"
DUSKY_ROOT_LABEL = "DUSKY_ROOT"
DUSKY_BIOS_LABEL = "DUSKY_BIOS"
DUSKY_EFI_PARTNAME = "DUSKY_EFI"
DUSKY_ROOT_PARTNAME = "DUSKY_ROOT"
STATE_ENV = Path("/tmp/arch_install_state.env")
STATE_JSON = Path("/tmp/dusky_state.json")
VALID_PART_RE = re.compile(r"^[a-zA-Z0-9_./-]+$")
BTRFS_CSUM = "crc32c"

def run(*cmd, check=True, capture=True, input_text=None, timeout=300):
    argv = [os.fspath(c) for c in cmd]
    try:
        if isinstance(input_text, (bytes, bytearray)):
            return subprocess.run(argv, check=check, text=False, capture_output=capture, input=bytes(input_text), timeout=timeout)
        elif isinstance(input_text, str):
            return subprocess.run(argv, check=check, text=True, capture_output=capture, input=input_text, timeout=timeout)
        return subprocess.run(argv, check=check, text=True, capture_output=capture, timeout=timeout)
    except subprocess.CalledProcessError:
        if check:
            console.print(f"[red]Failed {shlex.join([str(x) for x in argv])}[/red]")
        raise

def print_banner(boot_mode: str):
    txt = Text.from_markup(f"[bold cyan]DUSKY[/] Partitioning [dim]{boot_mode}[/]", justify="center")
    panel = Panel.fit(txt, box=box.ROUNDED, border_style="cyan", padding=(0,2))
    console.print(Align.center(panel))

def arrow_menu(title: str, options: List[Tuple[str,str]], default_idx: int = 0) -> Optional[str]:
    """
    Returns value or None if aborted via q/Esc/Ctrl-C.
    - TTY: raw arrow navigation, numbers 1-9 quick select, multi-digit + Enter for >=10
    - Non-TTY: fallback to numbered Prompt
    """
    if not options:
        return None

    try:
        is_tty = sys.stdin.isatty() and sys.stdout.isatty()
    except:
        is_tty = False

    if not is_tty:
        console.print(Panel.fit(f"[bold]{title}[/]", box=box.ROUNDED, border_style="white"))
        for i,(label,_) in enumerate(options,1):
            console.print(f"[cyan]{i})[/] {label}")
        while True:
            ans = Prompt.ask(f"{title} (1-{len(options)} or q to abort)", console=console, default=str(default_idx+1))
            if ans.lower() in ("q","quit","exit"):
                return None
            if ans.isdigit():
                idx = int(ans)-1
                if 0 <= idx < len(options):
                    return options[idx][1]
            for lab,val in options:
                if ans.strip() == val:
                    return val
            console.print("[red]Invalid[/red]")

    try:
        import termios, tty
    except ImportError:
        console.print(Panel.fit(f"[bold]{title}[/]", box=box.ROUNDED))
        for i,(label,_) in enumerate(options,1):
            console.print(f"[cyan]{i})[/] {label}")
        ans = Prompt.ask(f"{title} (1-{len(options)} or q to abort)", console=console, default=str(default_idx+1))
        if ans.lower() in ("q","quit"):
            return None
        if ans.isdigit():
            idx=int(ans)-1
            if 0 <= idx < len(options):
                return options[idx][1]
        return None

    idx = max(0, min(default_idx, len(options)-1))
    number_buffer = ""

    def render_menu():
        console.print("\x1b[2J\x1b[H", end="")
        banner_txt = Text.from_markup(f"[bold]{title}[/]", justify="center")
        banner_panel = Panel.fit(banner_txt, box=box.ROUNDED, border_style="magenta", padding=(0,2))
        console.print(Align.center(banner_panel))
        console.print()
        for i,(label,_) in enumerate(options):
            disp = f"{i+1}) {label}"
            if i==idx:
                console.print(f"[reverse][bold cyan] > {disp} [/][/]", highlight=False)
            else:
                console.print(f"   {disp}", highlight=False)
        console.print()
        if number_buffer:
            console.print(f"[yellow]Typed: {number_buffer} (Enter to confirm)[/yellow]")
        console.print("[dim]↑/↓ Navigate  Enter Select  q/Esc Abort  1-9 Quick  Type number + Enter for 10+[/dim]")

    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except Exception:
        console.print(Panel.fit(f"[bold]{title}[/]", box=box.ROUNDED))
        for i,(label,_) in enumerate(options,1):
            console.print(f"[cyan]{i})[/] {label}")
        ans = Prompt.ask(f"{title} (1-{len(options)} or q to abort)", console=console, default=str(default_idx+1))
        if ans.lower() in ("q","quit"):
            return None
        if ans.isdigit():
            idx=int(ans)-1
            if 0 <= idx < len(options):
                return options[idx][1]
        return None

    try:
        tty.setcbreak(fd)
        render_menu()
        while True:
            ch = os.read(fd, 1)
            if not ch:
                continue
            if ch == b'\x1b':
                if select.select([fd], [], [], 0.05)[0]:
                    ch2 = os.read(fd, 1)
                    if ch2 == b'[':
                        if select.select([fd], [], [], 0.05)[0]:
                            ch3 = os.read(fd, 1)
                            if ch3 == b'A':
                                idx = (idx-1) % len(options)
                                render_menu()
                            elif ch3 == b'B':
                                idx = (idx+1) % len(options)
                                render_menu()
                    elif ch2 == b'O':
                        if select.select([fd], [], [], 0.05)[0]:
                            ch3 = os.read(fd, 1)
                            if ch3 == b'A':
                                idx = (idx-1) % len(options)
                                render_menu()
                            elif ch3 == b'B':
                                idx = (idx+1) % len(options)
                                render_menu()
                else:
                    return None
            elif ch in (b'\r', b'\n'):
                if number_buffer:
                    try:
                        num = int(number_buffer)
                        if 1 <= num <= len(options):
                            idx = num-1
                            break
                    except:
                        pass
                    number_buffer = ""
                    render_menu()
                else:
                    break
            elif ch == b'q' or ch == b'\x03':
                return None
            elif ch.isdigit():
                number_buffer += ch.decode()
                if len(options) <= 9 and len(number_buffer) == 1:
                    num = int(number_buffer)
                    if 1 <= num <= len(options):
                        idx = num-1
                        break
                render_menu()
            elif ch in (b'\x7f', b'\x08'):
                if number_buffer:
                    number_buffer = number_buffer[:-1]
                    render_menu()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?2004l\x1b[?25h\x1b[0m")
        sys.stdout.flush()
        refresh_console()
        console.print()

    if 0 <= idx < len(options):
        return options[idx][1]
    return None

def get_partition_path(disk, num):
    disk = disk.rstrip("/")
    if re.search(rf"p{num}$", disk):
        return disk
    name = Path(disk).name
    if re.search(r"(?:nvme\d+n\d+|mmcblk\d+|loop\d+|nbd\d+|pmem\d+)$", name) or (disk and disk[-1].isdigit()):
        return f"{disk}p{num}"
    return f"{disk}{num}"

def detect_boot_mode() -> Literal["UEFI","BIOS"]:
    return "UEFI" if Path("/sys/firmware/efi/efivars").is_dir() else "BIOS"

def parse_credentials(path="./.arch_credentials"):
    out = {}
    cred = Path(path)
    if not cred.exists():
        return out
    try:
        script = f'set +u; source {shlex.quote(str(cred))} 2>/dev/null; printf "TARGET_USER=%s\\nENCRYPT_ROOT=%s\\nROOT_PASS=%s\\n" "$TARGET_USER" "$ENCRYPT_ROOT" "$ROOT_PASS"'
        r = subprocess.run(["bash","-c",script], text=True, capture_output=True, check=False, timeout=5)
        for line in r.stdout.splitlines():
            if "=" not in line:
                continue
            k,v = line.split("=",1)
            out[k]=v
    except:
        pass
    return out

def lsblk_all():
    r = run("lsblk","--json","--bytes","--paths","--tree","-o","NAME,PATH,KNAME,PKNAME,TYPE,FSTYPE,PARTTYPE,PARTLABEL,LABEL,SIZE,MODEL,MOUNTPOINTS", check=False, capture=True)
    try:
        return json.loads(r.stdout)
    except:
        return {"blockdevices":[]}

def list_disks():
    data = lsblk_all()
    disks = []
    for dev in data.get("blockdevices",[]):
        if dev.get("type") != "disk":
            continue
        name = dev.get("name","")
        if name.startswith("/dev/loop") or name.startswith("/dev/zram") or name.startswith("/dev/ram") or name.startswith("/dev/sr"):
            continue
        try:
            if int(dev.get("size",0)) < 1*1024*1024*1024:
                continue
        except:
            continue
        disks.append(dev)
    return disks

def get_pkname(dev_path):
    try:
        r = run("lsblk","-ndlo","PKNAME",dev_path, check=False, capture=True)
        pk = r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""
        if pk:
            return f"/dev/{pk}"
    except:
        pass
    return ""

def get_immediate_backing(dev):
    try:
        real = str(Path(dev).resolve())
        if dev.startswith("/dev/mapper/") or real.startswith("/dev/dm-"):
            mapper = Path(dev).name if dev.startswith("/dev/mapper/") else None
            if not mapper:
                try:
                    p = Path(f"/sys/class/block/{Path(real).name}/dm/name")
                    if p.exists():
                        mapper = p.read_text().strip()
                except:
                    mapper = None
            if mapper:
                try:
                    rr = run("cryptsetup","status",mapper, check=False, capture=True)
                    for line in rr.stdout.splitlines():
                        if line.strip().lower().startswith("device:"):
                            backing = line.split(":",1)[1].strip()
                            if backing and Path(backing).exists():
                                return str(Path(backing).resolve())
                except:
                    pass
            try:
                dmk = Path(real).name
                sd = Path(f"/sys/class/block/{dmk}/slaves")
                if sd.is_dir():
                    for c in sd.iterdir():
                        return f"/dev/{c.name}"
            except:
                pass
        pk = get_pkname(dev)
        if pk:
            return pk
        try:
            k = Path(dev).name
            sl = Path(f"/sys/class/block/{k}/slaves")
            if sl.is_dir():
                for s in sl.iterdir():
                    return f"/dev/{s.name}"
        except:
            pass
    except:
        pass
    return None

def device_is_on_disk(node, disk, max_depth=32):
    try:
        cur = str(Path(node).resolve())
        target = str(Path(disk).resolve())
        depth=0
        while depth < max_depth:
            if cur == target:
                return True
            nxt = get_immediate_backing(cur)
            if not nxt:
                pk = get_pkname(cur)
                if not pk:
                    return False
                cur = str(Path(pk).resolve())
            else:
                cur = str(Path(nxt).resolve())
            depth += 1
        return False
    except:
        return False

def findmnt_targets(prefix="/mnt"):
    try:
        r = run("findmnt","--json","--list","--submounts","--output","TARGET","--target",prefix, check=False, capture=True)
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            t = [fs.get("target","") for fs in data.get("filesystems",[]) if fs.get("target","").startswith(prefix)]
            return sorted(set(t), key=lambda p:(p.count("/"),len(p)), reverse=True)
    except:
        pass
    try:
        r = run("findmnt","-rn","-o","TARGET", check=False, capture=True)
        targets = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith(prefix)]
        return sorted(targets, key=lambda p:(p.count("/"),len(p)), reverse=True)
    except:
        return []

def wait_for_dev(path, timeout=10):
    p = Path(path)
    try:
        r = run("udevadm","wait","--timeout",str(timeout),"--settle",str(p), check=False, capture=True, timeout=timeout+2)
        if r.returncode == 0 and p.exists():
            return True
    except:
        pass
    for _ in range(timeout*10):
        if p.exists():
            return True
        time.sleep(0.1)
    return p.exists()

def prompt_luks_password(confirm=True):
    while True:
        try:
            pw = getpass.getpass("Enter LUKS passphrase (empty to abort): ")
        except EOFError:
            console.print("[red]No TTY[/red]")
            sys.exit(1)
        if not pw:
            return None
        if not confirm:
            return bytearray(pw.encode())
        try:
            pw2 = getpass.getpass("Verify: ")
        except EOFError:
            sys.exit(1)
        if pw != pw2:
            console.print("[red]Mismatch[/red]")
            continue
        return bytearray(pw.encode())

def is_mounted(dev):
    try:
        r = run("findmnt","-n","-o","TARGET","--source",dev, check=False, capture=True)
        return r.stdout.strip() or None
    except:
        return None

def detect_windows_esp(disk):
    data = lsblk_all()
    target_resolved = str(Path(disk).resolve())
    for dev in data.get("blockdevices",[]):
        dp = dev.get("path") or dev.get("name")
        try:
            dev_resolved = str(Path(dp).resolve()) if dp else ""
        except:
            dev_resolved = dp
        if dev_resolved != target_resolved and dp != disk:
            if f"/dev/{dev.get('name')}" != disk and dev.get('name') != Path(disk).name:
                continue
        for child in dev.get("children",[]) or []:
            fstype = (child.get("fstype") or "").lower()
            ptype = (child.get("parttype") or "").lower()
            path = child.get("path") or child.get("name")
            if not path:
                continue
            if ptype != EFI_GUID and fstype not in ("vfat","fat32"):
                continue
            mnt = is_mounted(path)
            tmp_obj = None
            tmp_path = mnt
            try:
                if not mnt:
                    tmp_obj = tempfile.TemporaryDirectory(prefix="dusky_efi_check_")
                    tmp_path = tmp_obj.name
                    run("mount","--mkdir","-t","vfat","-o","ro,noexec,nosuid,nodev",path,tmp_path, check=False, capture=True)
                if Path(tmp_path,"EFI","Microsoft","Boot","bootmgfw.efi").exists() or Path(tmp_path,"EFI","Microsoft").is_dir():
                    if tmp_obj:
                        run("umount",tmp_path, check=False, capture=True)
                        tmp_obj.cleanup()
                    return True, path
                if tmp_obj:
                    run("umount",tmp_path, check=False, capture=True)
            except:
                try:
                    if tmp_obj:
                        run("umount",tmp_path, check=False, capture=True)
                        tmp_obj.cleanup()
                except:
                    pass
    return False, None

def safe_deactivate_swaps_for_device(target_dev):
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
        try:
            if name in wanted or (name.startswith("/mnt/") and name.endswith("swapfile")) or (Path(name).name == "swapfile" and (name.startswith("/mnt/") or name == "/swap/swapfile")):
                if target_dev:
                    check_src = name
                    if Path(name).is_file():
                        try:
                            r2 = run("findmnt","-rn","-T",name,"-o","SOURCE", check=False, capture=True)
                            check_src = r2.stdout.strip().split("[",1)[0] or name
                        except:
                            pass
                    if device_is_on_disk(check_src, target_dev) or name in wanted:
                        run("swapoff",name, check=False, capture=True)
                else:
                    run("swapoff",name, check=False, capture=True)
        except:
            pass
    for p in wanted:
        run("swapoff",p, check=False, capture=True)

def teardown_device(target_dev):
    console.print(f"[yellow]>> Tearing down {target_dev}...[/yellow]")
    safe_deactivate_swaps_for_device(target_dev)
    for mp in findmnt_targets("/mnt"):
        try:
            run("umount","-R",mp, check=False, capture=True)
        except:
            pass
    try:
        r = run("findmnt","-rn","-o","TARGET,SOURCE", check=False, capture=True)
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            tgt,src = parts[0],parts[1].split("[",1)[0]
            try:
                if Path(src).exists() and device_is_on_disk(src,target_dev):
                    run("umount","-R",tgt, check=False, capture=True)
            except:
                pass
    except:
        pass
    try:
        r = run("lsblk","-pnro","NAME,TYPE", check=False, capture=True)
        for line in r.stdout.splitlines():
            p = line.split()
            if len(p) < 2:
                continue
            name,typ = p[0],p[1]
            if typ == "crypt" and device_is_on_disk(name,target_dev):
                dm = Path(name).name
                if name.startswith("/dev/mapper/"):
                    dm = name.split("/")[-1]
                else:
                    try:
                        dm = Path(f"/sys/class/block/{Path(name).name}/dm/name").read_text().strip()
                    except:
                        pass
                run("cryptsetup","close",dm, check=False, capture=True)
    except:
        pass
    run("udevadm","settle","--timeout=10", check=False, capture=True)

def ensure_mapper_free(target_dev=None):
    mp = Path(f"/dev/mapper/{TARGET_CRYPT_NAME}")
    if not mp.exists():
        return
    if target_dev:
        try:
            r = run("cryptsetup","status",TARGET_CRYPT_NAME, check=False, capture=True)
            backing = ""
            for line in r.stdout.splitlines():
                if line.strip().lower().startswith("device:"):
                    backing = line.split(":",1)[1].strip()
                    break
            if backing and not device_is_on_disk(backing,target_dev):
                return
        except:
            pass
    run("cryptsetup","close",TARGET_CRYPT_NAME, check=False, capture=True)
    run("udevadm","settle","--timeout=5", check=False, capture=True)

def get_ram_kb():
    try:
        return (os.sysconf("SC_PHYS_PAGES")*os.sysconf("SC_PAGE_SIZE"))//1024
    except:
        return 4*1024*1024

def write_gpt_sfdisk(disk, boot_mode, encrypt, efi_size="1.3G"):
    root_type = LUKS_GUID if encrypt else LINUX_ROOT_GUID
    sfdisk_input = "label: gpt\n\n"
    if boot_mode == "UEFI":
        sfdisk_input += f'size={efi_size}, type={EFI_GUID}, name="{DUSKY_EFI_PARTNAME}"\n'
        sfdisk_input += f'size=+, type={root_type}, name="{DUSKY_ROOT_PARTNAME}"\n'
    else:
        sfdisk_input += f'size=2M, type={BIOS_GUID}, name="{DUSKY_BIOS_LABEL}"\n'
        sfdisk_input += f'size=+, type={root_type}, name="{DUSKY_ROOT_PARTNAME}"\n'
    console.print(f"[cyan]Writing GPT to {disk} (wipe) EFI={efi_size}[/cyan]")
    run("wipefs","--all","--force","--lock=yes",disk, check=False, capture=True)
    try:
        run("sfdisk","--force","--wipe","always","--wipe-partitions","always","--label","gpt","--lock=yes",disk, input_text=sfdisk_input, capture=True)
    except subprocess.CalledProcessError:
        console.print("[yellow]sfdisk --lock failed, retry without[/yellow]")
        run("sfdisk","--force","--wipe","always","--wipe-partitions","always","--label","gpt",disk, input_text=sfdisk_input, capture=True)
    run("blockdev","--rereadpt",disk, check=False, capture=True)
    try:
        run("udevadm","trigger","--settle","-w","--timeout=10",disk, check=False, capture=True)
    except:
        run("udevadm","settle","--timeout=10", check=False, capture=True)
    try:
        run("udevadm","wait","--timeout=10","--settle",f"{get_partition_path(disk,1)}",f"{get_partition_path(disk,2)}", check=False, capture=True)
    except:
        pass
    efi_part = get_partition_path(disk,1) if boot_mode=="UEFI" else None
    root_part = get_partition_path(disk,2)
    if not wait_for_dev(root_part,10):
        raise RuntimeError(f"{root_part} missing")
    if efi_part and not wait_for_dev(efi_part,10):
        raise RuntimeError(f"{efi_part} missing")
    return root_part, efi_part

def show_disks_table(disks):
    table = Table(title="Disks", box=box.ROUNDED, show_lines=False, expand=False)
    table.add_column("#",justify="right",style="cyan")
    table.add_column("Device",style="bold")
    table.add_column("Size",justify="right")
    table.add_column("Model",style="dim")
    for i,d in enumerate(disks,1):
        try:
            sz = f"{int(d.get('size',0))/1024**3:.1f}G"
        except:
            sz = str(d.get("size",""))
        table.add_row(str(i),d.get("path") or d.get("name"),sz,(d.get("model") or "")[:30])
    console.print(Align.center(table))

def validate_part_input(raw):
    if not raw or not VALID_PART_RE.match(raw):
        raise ValueError("Invalid chars")
    name = raw.removeprefix("/dev/")
    p = Path("/dev") / name
    try:
        rp = p.resolve()
    except:
        rp = p
    try:
        if not rp.is_relative_to(Path("/dev")):
            raise ValueError("Must be under /dev")
    except AttributeError:
        if "/dev" not in str(rp):
            raise ValueError("Must be under /dev")
    if not rp.exists():
        raise ValueError(f"{rp} does not exist")
    return rp

def choose_disk(disks):
    if len(disks)==1:
        only = disks[0].get("path") or disks[0].get("name")
        console.print(f"[green]Only one disk found: {only}[/green]")
        show_disks_table(disks)
        return only
    show_disks_table(disks)
    opts = []
    for i,d in enumerate(disks,1):
        path = d.get("path") or d.get("name")
        try:
            sz = f"{int(d.get('size',0))/1024**3:.1f}G"
        except:
            sz = ""
        model = (d.get("model") or "")[:30]
        label = f"{path} {sz} {model}".strip()
        opts.append((label, path))
    selected = arrow_menu("Select Disk", opts, default_idx=0)
    if selected is None:
        return None
    return selected

def show_partitions_table(disk):
    r = run("lsblk","-l","-o","NAME,SIZE,TYPE,FSTYPE,PARTTYPE,PARTLABEL,LABEL,MOUNTPOINTS",disk, check=False, capture=True)
    panel = Panel.fit(r.stdout, title=f"Partitions on {disk}", box=box.ROUNDED, border_style="yellow")
    console.print(Align.center(panel))

def get_partitions_list(disk):
    try:
        r = run("lsblk","--json","--paths","-o","PATH,TYPE,SIZE,FSTYPE,PARTLABEL",disk, check=False, capture=True)
        data = json.loads(r.stdout)
        parts=[]
        for dev in data.get("blockdevices",[]):
            for child in dev.get("children",[]) or []:
                if child.get("type")=="part":
                    parts.append(child)
        return parts
    except:
        return []

def prompt_root_and_efi(target_dev, boot_mode, has_win, win_esp):
    show_partitions_table(target_dev)
    parts = get_partitions_list(target_dev)
    if parts:
        root_opts = []
        for p in parts:
            path = p.get("path","")
            sz = p.get("size","")
            fstype = p.get("fstype","") or "no-fs"
            label = f"{path} {sz} {fstype} {p.get('partlabel','')}".strip()
            root_opts.append((label, path))
        root_sel = arrow_menu(f"Select ROOT on {target_dev}", root_opts, default_idx=len(root_opts)-1 if root_opts else 0)
        if root_sel is None:
            return None, None, None
        root = root_sel
    else:
        while True:
            try:
                raw = Prompt.ask(f"Enter ROOT partition (q to abort)", console=console)
                if raw.lower() in ("q","quit"):
                    return None, None, None
                rp = validate_part_input(raw)
                root = str(rp)
                break
            except Exception as e:
                console.print(f"[red]{e}[/red]")
    efi = ""
    format_efi = True
    if boot_mode == "UEFI":
        if parts:
            efi_opts = []
            for p in parts:
                path = p.get("path","")
                sz = p.get("size","")
                fstype = p.get("fstype","") or "no-fs"
                label = f"{path} {sz} {fstype}".strip()
                efi_opts.append((label, path))
            if efi_opts:
                efi_sel = arrow_menu(f"Select EFI on {target_dev}", efi_opts, default_idx=0)
                if efi_sel is None:
                    return None, None, None
                efi = efi_sel
            else:
                while True:
                    try:
                        raw = Prompt.ask(f"Enter EFI partition (q to abort)", console=console)
                        if raw.lower() in ("q","quit"):
                            return None, None, None
                        ep = validate_part_input(raw)
                        efi = str(ep)
                        break
                    except Exception as e:
                        console.print(f"[red]{e}[/red]")
        else:
            while True:
                try:
                    raw = Prompt.ask(f"Enter EFI partition (q to abort)", console=console)
                    if raw.lower() in ("q","quit"):
                        return None, None, None
                    ep = validate_part_input(raw)
                    efi = str(ep)
                    break
                except Exception as e:
                    console.print(f"[red]{e}[/red]")
        if has_win and efi == win_esp:
            format_efi = Confirm.ask(f"EFI {efi} looks like Windows ESP {win_esp}. Format? [red]NO keeps Windows[/red]", console=console, default=False)
        else:
            ans = Prompt.ask(f"Format EFI {efi} as {DUSKY_EFI_LABEL}? (y/n/q)", choices=["y","n","q"], default="y", show_choices=False, console=console)
            if ans == "q":
                return None, None, None
            format_efi = (ans == "y")
    return root, efi, format_efi

def format_root_and_efi(root_part, efi_part, format_efi, do_encrypt, boot_mode, has_win, win_esp, creds, luks_ba=None):
    if run("cryptsetup","isLuks",root_part, check=False, capture=True).returncode == 0:
        console.print(f"[yellow]Existing LUKS on {root_part}, erasing header[/yellow]")
        run("cryptsetup","--batch-mode","erase",root_part, check=False, capture=True)
    run("wipefs","--all","--force","--lock=yes",root_part, check=False, capture=True)
    btrfs_target = root_part
    
    if do_encrypt:
        mem_kb = get_ram_kb()
        pbkdf = []
        if mem_kb < 3_000_000:
            pbkdf = ["--pbkdf-memory","256","--pbkdf-parallel","1"]
        elif mem_kb < 4_200_000:
            pbkdf = ["--pbkdf-memory","512"]
        try:
            fmt = ["cryptsetup","--batch-mode","--type","luks2","--pbkdf","argon2id","--label",DUSKY_ROOT_LABEL] + pbkdf + ["luksFormat","--key-file","-",root_part]
            r = run(*fmt, input_text=luks_ba, check=False, capture=True)
            if r.returncode == 3:
                fmt = ["cryptsetup","--batch-mode","--type","luks2","--pbkdf","argon2id","--label",DUSKY_ROOT_LABEL,"--pbkdf-memory","256","--pbkdf-parallel","1","luksFormat","--key-file","-",root_part]
                r = run(*fmt, input_text=luks_ba, check=False, capture=True)
            if r.returncode != 0:
                console.print(f"[red]luksFormat fail {r.returncode}[/red]")
                sys.exit(1)
            ro = run("cryptsetup","open","--type","luks2","--allow-discards","--key-file","-",root_part,TARGET_CRYPT_NAME, input_text=luks_ba, check=False, capture=True)
            if ro.returncode != 0:
                console.print("[red]cryptsetup open fail[/red]")
                sys.exit(1)
        finally:
            if luks_ba:
                for i in range(len(luks_ba)):
                    luks_ba[i]=0
        btrfs_target = f"/dev/mapper/{TARGET_CRYPT_NAME}"
        
    if BTRFS_CSUM == "blake2":
        run("mkfs.btrfs","-f","--csum","blake2","-O","no-holes","-L",DUSKY_ROOT_LABEL,btrfs_target, capture=True)
    else:
        run("mkfs.btrfs","-f","-L",DUSKY_ROOT_LABEL,btrfs_target, capture=True)
        
    if boot_mode == "UEFI" and efi_part:
        if format_efi:
            if has_win and efi_part == win_esp:
                console.print("[yellow]Preserving Windows ESP[/yellow]")
            else:
                run("wipefs","--all","--force","--lock=yes",efi_part, check=False, capture=True)
                run("mkfs.fat","-F","32","-n",DUSKY_EFI_LABEL,efi_part, capture=True)
        else:
            if not has_win:
                try:
                    run("fatlabel",efi_part,DUSKY_EFI_LABEL, check=False, capture=True)
                except:
                    pass
    return btrfs_target

def choose_strategy_interactive():
    opts = [
        ("Wipe Entire Drive (Default)", "1"),
        ("Select Existing (Dual Boot)", "2"),
        ("Manual Partitioning (cfdisk)", "3"),
        ("Rescue / Chroot (Mount Only)", "4"),
    ]
    sel = arrow_menu("Choose Strategy", opts, default_idx=0)
    if sel is None:
        return None
    return int(sel)

def parse_strategy_cli(args, raw_argv):
    argv_lower = [a.lower() for a in raw_argv]
    if any("rescue" in a for a in argv_lower):
        return 4
    if args.rescue:
        return 4
    if args.strategy:
        s = args.strategy.lower()
        if s in ("1","wipe","entire","wipe_entire","wipeentire","erase"):
            return 1
        if s in ("2","existing","select","dual","select_existing","dualboot"):
            return 2
        if s in ("3","manual","cfdisk","advanced"):
            return 3
        if s in ("4","rescue","chroot","mount","mountonly","mount_only"):
            return 4
    if args.auto:
        return 1
    return None

def run_tui_app(cmd_list):
    try:
        console.show_cursor(True)
    except:
        pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except:
        pass
    try:
        sys.stdout.write("\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?2004l\x1b[?25h")
        sys.stdout.flush()
    except:
        pass

    old_int = signal.getsignal(signal.SIGINT)
    old_term = signal.getsignal(signal.SIGTERM)
    old_tstp = None
    try:
        if hasattr(signal, 'SIGTSTP'):
            old_tstp = signal.getsignal(signal.SIGTSTP)
    except:
        pass

    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if old_tstp is not None:
            signal.signal(signal.SIGTSTP, signal.SIG_IGN)
    except:
        pass

    term = os.environ.get("TERM","")
    if term in ("","dumb","unknown"):
        os.environ["TERM"] = "linux"

    def _preexec_restore():
        try:
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            if hasattr(signal, 'SIGTSTP'):
                signal.signal(signal.SIGTSTP, signal.SIG_DFL)
        except:
            pass

    rc = 127
    try:
        try:
            with open("/dev/tty","r") as tty_in, open("/dev/tty","w") as tty_out:
                proc = subprocess.run(cmd_list, stdin=tty_in, stdout=tty_out, stderr=tty_out, check=False, preexec_fn=_preexec_restore)
                rc = proc.returncode
        except Exception:
            proc = subprocess.run(cmd_list, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, check=False, preexec_fn=_preexec_restore)
            rc = proc.returncode
    except FileNotFoundError:
        raise
    finally:
        try:
            signal.signal(signal.SIGINT, old_int)
            signal.signal(signal.SIGTERM, old_term)
            if old_tstp is not None:
                signal.signal(signal.SIGTSTP, old_tstp)
        except:
            pass
        try:
            subprocess.run(["stty","sane"], check=False, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
        except:
            pass
        try:
            sys.stdout.write("\x1b[?1l\x1b[?25h\x1b[0m")
            sys.stdout.flush()
        except:
            pass
        refresh_console()

    return rc

def launch_partition_editor(target_dev):
    cmds = [
        ["cfdisk","--color=always","--lock=yes", target_dev],
        ["cfdisk","--color=always","--lock=no", target_dev],
        ["cfdisk", target_dev],
    ]
    for cmd in cmds:
        try:
            rc = run_tui_app(cmd)
            return rc
        except FileNotFoundError:
            continue
    try:
        console.print("[yellow]cfdisk not found, falling back to fdisk[/yellow]")
        rc = run_tui_app(["fdisk", target_dev])
        return rc
    except FileNotFoundError:
        console.print("[red]Neither cfdisk nor fdisk found![/red]")
        return 127

def strategy_wipe(target_dev, boot_mode, do_encrypt, efi_size, has_win, win_esp, creds, auto_mode=False):
    panel = Panel.fit(f"[bold red]Strategy 1: Wipe Entire {target_dev} EFI={efi_size}[/bold red]", box=box.ROUNDED, border_style="red")
    console.print(Align.center(panel))
    
    if not auto_mode:
        if not Confirm.ask(f"[red]Confirm WIPE ENTIRE {target_dev}? All data will be erased![/red]", console=console, default=False):
            console.print("[yellow]Going back...[/yellow]")
            return False

    luks_ba = None
    if do_encrypt:
        if creds.get("ROOT_PASS"):
            luks_ba = bytearray(creds["ROOT_PASS"].encode())
        else:
            luks_ba = prompt_luks_password()
            if luks_ba is None:
                console.print("[yellow]Aborted LUKS setup. Going back...[/yellow]")
                return False

    teardown_device(target_dev)
    ensure_mapper_free(target_dev)
    root_part, efi_part = write_gpt_sfdisk(target_dev, boot_mode, do_encrypt, efi_size)
    
    format_root_and_efi(root_part, efi_part, True, do_encrypt, boot_mode, has_win, win_esp, creds, luks_ba)

    env=f'PROVISIONED_ROOT_PART="{root_part}"\n'
    if efi_part:
        env+=f'PROVISIONED_EFI_PART="{efi_part}"\n'
    env+=f'ENCRYPT_ROOT="{1 if do_encrypt else 0}"\n'
    STATE_ENV.write_text(env)
    STATE_ENV.chmod(0o600)
    STATE_JSON.write_text(json.dumps({"root_part":root_part,"efi_part":efi_part,"encrypt":bool(do_encrypt),"disk":target_dev,"boot_mode":boot_mode,"strategy":1},indent=2))
    STATE_JSON.chmod(0o600)
    console.print(Panel.fit(f"[green]Wipe Complete Root={root_part} EFI={efi_part or 'N/A'}[/green]",box=box.ROUNDED, border_style="green"))
    return True

def strategy_select_existing(target_dev, boot_mode, do_encrypt, creds, has_win, win_esp):
    panel = Panel.fit(f"[bold cyan]Strategy 2: Select Existing on {target_dev} (Dual Boot)[/bold cyan]", box=box.ROUNDED, border_style="cyan")
    console.print(Align.center(panel))
    
    root_part, efi_part, format_efi = prompt_root_and_efi(target_dev, boot_mode, has_win, win_esp)
    if not root_part:
        return False
        
    console.print(Panel.fit(f"Will format ROOT={root_part} as BTRFS (LUKS={bool(do_encrypt)}) EFI={efi_part or 'N/A'} format_efi={format_efi}", box=box.ROUNDED))
    if not Confirm.ask("[yellow]Proceed with formatting selected partitions?[/yellow]", console=console, default=True):
        console.print("[yellow]Going back...[/yellow]")
        return False

    luks_ba = None
    if do_encrypt:
        if creds.get("ROOT_PASS"):
            luks_ba = bytearray(creds["ROOT_PASS"].encode())
        else:
            luks_ba = prompt_luks_password()
            if luks_ba is None:
                console.print("[yellow]Aborted LUKS setup. Going back...[/yellow]")
                return False

    teardown_device(target_dev)
    ensure_mapper_free(target_dev)
    
    format_root_and_efi(root_part, efi_part, format_efi, do_encrypt, boot_mode, has_win, win_esp, creds, luks_ba)
    
    env=f'PROVISIONED_ROOT_PART="{root_part}"\n'
    if efi_part:
        env+=f'PROVISIONED_EFI_PART="{efi_part}"\n'
    env+=f'ENCRYPT_ROOT="{1 if do_encrypt else 0}"\n'
    STATE_ENV.write_text(env)
    STATE_ENV.chmod(0o600)
    STATE_JSON.write_text(json.dumps({"root_part":root_part,"efi_part":efi_part,"encrypt":bool(do_encrypt),"disk":target_dev,"boot_mode":boot_mode,"strategy":2},indent=2))
    STATE_JSON.chmod(0o600)
    console.print(Panel.fit(f"[green]Select Existing Complete Root={root_part} EFI={efi_part or 'N/A'}[/green]",box=box.ROUNDED, border_style="green"))
    return True

def strategy_manual(target_dev, boot_mode, do_encrypt, creds, has_win, win_esp):
    panel = Panel.fit(f"[bold magenta]Strategy 3: Manual Partitioning via cfdisk on {target_dev}[/bold magenta]", box=box.ROUNDED, border_style="magenta")
    console.print(Align.center(panel))
    
    console.print("[yellow]Tearing down mounts before cfdisk...[/yellow]")
    teardown_device(target_dev)
    ensure_mapper_free(target_dev)
    
    console.print(f"[cyan]Launching cfdisk {target_dev} - use arrow keys, then Write and Quit[/cyan]")
    rc = launch_partition_editor(target_dev)
    if rc != 0:
        console.print(f"[yellow]Partition editor exited with code {rc}. Checking if table was written...[/yellow]")
        if not Confirm.ask("Continue to partition selection? (No will re-launch editor)", console=console, default=True):
            rc2 = launch_partition_editor(target_dev)
            if rc2 != 0:
                console.print("[yellow]Editor exited again, proceeding anyway...[/yellow]")
                
    console.print("[yellow]Re-reading partition table...[/yellow]")
    run("blockdev","--rereadpt",target_dev, check=False, capture=True)
    try:
        run("partx","-u",target_dev, check=False, capture=True)
    except:
        pass
    try:
        run("udevadm","trigger","--settle","-w","--timeout=10",target_dev, check=False, capture=True)
        run("udevadm","wait","--timeout=10","--settle",f"{get_partition_path(target_dev,1)}",f"{get_partition_path(target_dev,2)}", check=False, capture=True)
    except:
        run("udevadm","settle","--timeout=10", check=False, capture=True)
    time.sleep(1)
    
    parts = get_partitions_list(target_dev)
    if not parts:
        console.print(f"[red]No partitions found on {target_dev} after editing![/red]")
        if Confirm.ask("Re-launch editor?", console=console, default=True):
            launch_partition_editor(target_dev)
            run("blockdev","--rereadpt",target_dev, check=False, capture=True)
            run("udevadm","settle","--timeout=10", check=False, capture=True)
            time.sleep(1)
        else:
            return False

    root_part, efi_part, format_efi = prompt_root_and_efi(target_dev, boot_mode, has_win, win_esp)
    if not root_part:
        return False
        
    console.print(Panel.fit(f"Will format ROOT={root_part} EFI={efi_part or 'N/A'} format_efi={format_efi}", box=box.ROUNDED))
    if not Confirm.ask("[yellow]Proceed with formatting after manual edit?[/yellow]", console=console, default=True):
        console.print("[yellow]Going back...[/yellow]")
        return False

    luks_ba = None
    if do_encrypt:
        if creds.get("ROOT_PASS"):
            luks_ba = bytearray(creds["ROOT_PASS"].encode())
        else:
            luks_ba = prompt_luks_password()
            if luks_ba is None:
                console.print("[yellow]Aborted LUKS setup. Going back...[/yellow]")
                return False

    teardown_device(target_dev)
    ensure_mapper_free(target_dev)
    
    format_root_and_efi(root_part, efi_part, format_efi, do_encrypt, boot_mode, has_win, win_esp, creds, luks_ba)
    
    env=f'PROVISIONED_ROOT_PART="{root_part}"\n'
    if efi_part:
        env+=f'PROVISIONED_EFI_PART="{efi_part}"\n'
    env+=f'ENCRYPT_ROOT="{1 if do_encrypt else 0}"\n'
    STATE_ENV.write_text(env)
    STATE_ENV.chmod(0o600)
    STATE_JSON.write_text(json.dumps({"root_part":root_part,"efi_part":efi_part,"encrypt":bool(do_encrypt),"disk":target_dev,"boot_mode":boot_mode,"strategy":3},indent=2))
    STATE_JSON.chmod(0o600)
    console.print(Panel.fit(f"[green]Manual Complete Root={root_part} EFI={efi_part or 'N/A'}[/green]",box=box.ROUNDED, border_style="green"))
    return True

def strategy_rescue(target_dev, boot_mode, creds, has_win, win_esp):
    panel = Panel.fit(f"[bold green]Strategy 4: Rescue / Chroot (Mount Only) on {target_dev}[/bold green]", box=box.ROUNDED, border_style="green")
    console.print(Align.center(panel))
    
    show_partitions_table(target_dev)
    parts = get_partitions_list(target_dev)
    if parts:
        opts = [(f"{p.get('path','')} {p.get('size','')} {p.get('fstype','')}".strip(), p.get('path','')) for p in parts]
        sel = arrow_menu("Select existing ROOT partition", opts, default_idx=len(opts)-1)
        if sel is None:
            console.print("[yellow]Going back...[/yellow]")
            return False
        root_part = sel
    else:
        while True:
            try:
                raw = Prompt.ask("Enter existing ROOT partition (e.g. vda2) or q to abort", console=console)
                if raw.lower() in ("q","quit"):
                    return False
                rp = validate_part_input(raw)
                root_part = str(rp)
                break
            except Exception as e:
                console.print(f"[red]{e}[/red]")
                
    efi_part = ""
    if boot_mode == "UEFI":
        if parts:
            opts = [(f"{p.get('path','')} {p.get('size','')} {p.get('fstype','')}".strip(), p.get('path','')) for p in parts]
            opts.append(("Skip / No EFI", ""))
            sel = arrow_menu("Select existing EFI partition (optional)", opts, default_idx=0)
            if sel is None:
                return False
            efi_part = sel or ""
            
        if not efi_part:
            while True:
                try:
                    raw = Prompt.ask("Enter existing EFI partition (or empty to skip, q to abort)", console=console, default="")
                    if raw.lower() in ("q","quit"):
                        return False
                    if not raw:
                        break
                    ep = validate_part_input(raw)
                    efi_part = str(ep)
                    break
                except Exception as e:
                    console.print(f"[red]{e}[/red]")
                    
    is_luks = run("cryptsetup","isLuks",root_part, check=False, capture=True).returncode == 0
    encrypt_flag = 0
    if is_luks:
        console.print(f"[yellow]{root_part} is LUKS, unlocking without formatting...[/yellow]")
        ensure_mapper_free(target_dev)
        if Path(f"/dev/mapper/{TARGET_CRYPT_NAME}").exists():
            console.print(f"[yellow]Mapper {TARGET_CRYPT_NAME} already exists, using it[/yellow]")
        else:
            if creds.get("ROOT_PASS"):
                pw = bytearray(creds["ROOT_PASS"].encode())
                r = run("cryptsetup","open","--allow-discards","--key-file","-",root_part,TARGET_CRYPT_NAME, input_text=pw, check=False, capture=True)
                for i in range(len(pw)):
                    pw[i]=0
                if r.returncode != 0:
                    console.print("[red]Failed to unlock with credentials, prompting[/red]")
                    pw = prompt_luks_password(False)
                    if pw is None:
                        console.print("[yellow]Aborted unlocking. Going back...[/yellow]")
                        return False
                    run("cryptsetup","open","--allow-discards","--key-file","-",root_part,TARGET_CRYPT_NAME, input_text=pw, capture=False)
                    for i in range(len(pw)):
                        pw[i]=0
            else:
                pw = prompt_luks_password(False)
                if pw is None:
                    console.print("[yellow]Aborted unlocking. Going back...[/yellow]")
                    return False
                run("cryptsetup","open","--allow-discards","--key-file","-",root_part,TARGET_CRYPT_NAME, input_text=pw, capture=False)
                for i in range(len(pw)):
                    pw[i]=0
        encrypt_flag = 1
    else:
        console.print(f"[cyan]{root_part} is not LUKS, using directly[/cyan]")
        r = run("lsblk","-ndlo","FSTYPE",root_part, check=False, capture=True)
        fstype = r.stdout.strip().lower()
        if fstype != "btrfs":
            console.print(f"[yellow]Warning: {root_part} fstype is {fstype}, expected btrfs. Continuing anyway for rescue.[/yellow]")
            
    env = f'PROVISIONED_ROOT_PART="{root_part}"\n'
    if efi_part:
        env += f'PROVISIONED_EFI_PART="{efi_part}"\n'
    env += f'ENCRYPT_ROOT="{encrypt_flag}"\n'
    STATE_ENV.write_text(env)
    STATE_ENV.chmod(0o600)
    STATE_JSON.write_text(json.dumps({"root_part":root_part,"efi_part":efi_part,"encrypt":bool(encrypt_flag),"disk":target_dev,"boot_mode":boot_mode,"strategy":4},indent=2))
    STATE_JSON.chmod(0o600)
    console.print(Panel.fit(f"[green]Rescue state written. No formatting done.\nRoot={root_part} EFI={efi_part or 'N/A'} Encrypt={encrypt_flag}\nRun 040 to mount.[/green]", box=box.ROUNDED, border_style="green"))
    return True

def valid_efi_size(s: str) -> str:
    if re.match(r'^\d+(\.\d+)?\s*(M|MiB|G|GiB)\s*$', s, re.I):
        return s.strip().replace(" ","")
    raise argparse.ArgumentTypeError(f"Invalid EFI size {s}, use like 1.3G, 1331M, 2G")

def main():
    parser = argparse.ArgumentParser(description="DUSKY 030 Partitioning - 4 Strategies - 1.3G EFI default")
    parser.add_argument("--auto", action="store_true", help="Non-interactive, default to Wipe Entire Drive")
    parser.add_argument("--disk", type=str, help="Target disk /dev/...")
    parser.add_argument("--encrypt", action="store_true", help="Enable LUKS2")
    parser.add_argument("--no-encrypt", action="store_true", help="Disable LUKS2")
    parser.add_argument("--efi-size", type=valid_efi_size, default="1.3G", help="EFI size for wipe (default 1.3G)")
    parser.add_argument("--allow-bios", action="store_true", help="Allow BIOS mode")
    parser.add_argument("--strategy", type=str, default=None, help="Partitioning strategy: wipe|existing|manual|rescue (1-4)")
    parser.add_argument("--rescue", action="store_true", help="Shortcut for --strategy rescue")
    args = parser.parse_args()

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        console.print("[red]Need root[/red]")
        sys.exit(1)
    if not args.auto and not sys.stdin.isatty() and not args.strategy and not args.rescue:
        console.print("[red]Need TTY or --auto/--strategy[/red]")
        sys.exit(1)

    boot_mode = detect_boot_mode()
    if boot_mode == "BIOS" and not args.allow_bios:
        console.print("[yellow]BIOS detected, UEFI recommended. Use --allow-bios to continue[/yellow]")
        if not args.auto and not Confirm.ask("Continue BIOS?", console=console, default=False):
            sys.exit(0)

    print_banner(boot_mode)

    creds = parse_credentials()
    preset = None
    if args.encrypt:
        preset = 1
    elif args.no_encrypt:
        preset = 0
    elif creds.get("ENCRYPT_ROOT") in ("1","0"):
        try:
            preset = int(creds["ENCRYPT_ROOT"])
        except:
            pass

    disks = list_disks()
    if not disks:
        console.print("[red]No disks >=1G found[/red]")
        sys.exit(1)

    while True:
        target_dev = args.disk
        if not target_dev:
            if (args.auto or args.strategy) and len(disks) == 1:
                target_dev = disks[0].get("path") or disks[0].get("name")
            else:
                target_dev = choose_disk(disks)
                if target_dev is None:
                    console.print("[yellow]Exiting...[/yellow]")
                    sys.exit(0)
                    
        target_dev = str(Path(target_dev).resolve())

        has_win, win_esp = detect_windows_esp(target_dev)
        if has_win:
            console.print(Align.center(Panel.fit(f"[yellow]Windows ESP {win_esp} detected - will preserve unless you format[/yellow]", title="Dual-Boot", box=box.ROUNDED, border_style="yellow")))

        forced_strategy = parse_strategy_cli(args, sys.argv)
        go_back_to_disk = False
        
        while True:
            if forced_strategy is not None:
                strategy = forced_strategy
            else:
                strategy = choose_strategy_interactive()
                if strategy is None:
                    go_back_to_disk = True
                    break

            console.print(f"[cyan]Selected strategy {strategy} - EFI size {args.efi_size}[/cyan]")

            do_encrypt = False
            if strategy in (1,2,3):
                if preset is not None:
                    do_encrypt = bool(preset)
                elif args.auto:
                    do_encrypt = False
                else:
                    ans = Prompt.ask("Encrypt ROOT with LUKS2 (argon2id)? (y/n/q)", choices=["y", "n", "q"], default="n", show_choices=False, console=console)
                    if ans == "q":
                        if forced_strategy is not None:
                            console.print("[yellow]Aborted forced CLI strategy. Exiting...[/yellow]")
                            sys.exit(0)
                        continue
                    do_encrypt = (ans == "y")

            success = False
            if strategy == 1:
                success = strategy_wipe(target_dev, boot_mode, do_encrypt, args.efi_size, has_win, win_esp, creds, auto_mode=args.auto)
            elif strategy == 2:
                success = strategy_select_existing(target_dev, boot_mode, do_encrypt, creds, has_win, win_esp)
            elif strategy == 3:
                success = strategy_manual(target_dev, boot_mode, do_encrypt, creds, has_win, win_esp)
            elif strategy == 4:
                success = strategy_rescue(target_dev, boot_mode, creds, has_win, win_esp)
            else:
                console.print(f"[red]Invalid strategy {strategy}[/red]")
                sys.exit(1)

            if success is not False:
                return # We are successfully done.

            if forced_strategy is not None:
                console.print("[yellow]Aborted. Exiting since strategy is provided by CLI...[/yellow]")
                sys.exit(0)
                
        # If we broke out of the strategy loop by hitting 'q'
        if go_back_to_disk:
            if args.disk or ((args.auto or args.strategy) and len(disks) == 1):
                console.print("[yellow]Exiting...[/yellow]")
                sys.exit(0)
            if len(disks) == 1:
                console.print("[yellow]Exiting...[/yellow]")
                sys.exit(0)
            # Loop goes back to disk selection menu!

if __name__ == "__main__":
    def _h(s,f):
        try:
            run("udevadm","settle","--timeout=2", check=False, capture=True)
        except:
            pass
        sys.exit(128+s)
    signal.signal(signal.SIGINT, _h)
    signal.signal(signal.SIGTERM, _h)
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
