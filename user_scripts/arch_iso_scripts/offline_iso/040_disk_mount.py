#!/usr/bin/env python3
"""
Mount the storage layout selected by 030_partitioning.py for offline installation.
Creates the DUSKY Btrfs subvolumes and swapfile, and preserves existing EFI data.
"""

from __future__ import annotations
import os, sys, re, json, shlex, shutil, signal, subprocess, tempfile, argparse, getpass
from pathlib import Path

def _ensure_rich():
    import importlib.util
    if importlib.util.find_spec("rich") is None:
        print("python-rich is required in the offline ISO", file=sys.stderr)
        raise SystemExit(1)

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
    binary = isinstance(input_text, (bytes, bytearray))
    try:
        return subprocess.run(
            argv, check=check, capture_output=capture, timeout=timeout,
            env=os.environ | {"LC_ALL": "C"},
            input=bytes(input_text) if binary else input_text,
            **({} if binary else {"encoding": "utf-8", "errors": "replace"}),
        )
    except subprocess.CalledProcessError as error:
        console.print(Text(f"Failed {shlex.join(argv)}", style="red"))
        detail = error.stderr
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        if detail and detail.strip():
            console.print(Text(f"Details: {detail.strip()}", style="red"))
        raise
    except subprocess.TimeoutExpired as error:
        if check:
            raise
        detail = f"{argv[0]}: timed out after {timeout}s"
        return subprocess.CompletedProcess(
            argv, 124,
            stdout=(error.stdout or b"") if binary else (error.stdout or ""),
            stderr=detail.encode() if binary else detail,
        )
    except OSError:
        if check:
            raise
        detail = f"{argv[0]}: not available"
        return subprocess.CompletedProcess(
            argv, 127, stdout=b"" if binary else "",
            stderr=detail.encode() if binary else detail,
        )


def mountinfo_targets(text: str, prefix: str = "/mnt") -> list[str]:
    """Return a mount tree, decoding mountinfo path escapes."""
    targets = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 6 or " - " not in line:
            continue
        target = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[4])
        if target == prefix or target.startswith(prefix.rstrip("/") + "/"):
            targets.add(target)
    return sorted(targets, key=lambda path: (path.count("/"), len(path)), reverse=True)

def detect_boot_mode():
    try:
        state = json.loads(STATE_JSON.read_text())
        bm = str(state.get("boot_mode", "")).upper()
        if bm in ("UEFI", "BIOS"):
            return bm
    except Exception:
        pass
    if Path("/sys/firmware/efi").is_dir():
        return "UEFI"
    try:
        if Path("/sys/firmware/efi/fw_platform_size").read_text().strip():
            return "UEFI"
    except Exception:
        pass
    try:
        for line in Path("/proc/mounts").read_text().splitlines():
            fields = line.split()
            if len(fields) >= 3 and (fields[0] == "efivarfs" or fields[2] == "efivarfs"):
                return "UEFI"
    except Exception:
        pass
    try:
        r = run("dmesg", check=False, capture=True)
        if re.search(r"efi:.*v[0-9]", r.stdout or "", re.IGNORECASE):
            return "UEFI"
    except Exception:
        pass
    return "BIOS"

BOOT_MODE = detect_boot_mode()

def print_banner(title: str):
    txt = Text.from_markup(f"[bold cyan]{title}[/] [dim]{BOOT_MODE}[/]", justify="center")
    panel = Panel.fit(txt, box=box.ROUNDED, border_style="cyan", padding=(0,2))
    console.print(Align.center(panel))

def findmnt_json(target="/mnt"):
    try:
        r = run("findmnt","--json","--list","--submounts","--output","TARGET,SOURCE,FSTYPE,OPTIONS,ID","--mountpoint",target, check=False, capture=True)
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
            if run("umount","-R",mp, check=False, capture=True).returncode != 0:
                run("umount", "-R", "-f", "-l", mp, check=False, capture=True)
        except:
            pass
    try:
        remaining = mountinfo_targets(Path("/proc/self/mountinfo").read_text())
        for mp in sorted(remaining, key=lambda p:(p.count("/"),len(p)), reverse=True):
            if run("umount",mp, check=False, capture=True).returncode != 0:
                run("umount", "-f", "-l", mp, check=False, capture=True)
    except:
        pass

    # Clean up mount namespaces holding /mnt
    try:
        seen_ns_inodes = set()
        for proc_dir in Path("/proc").glob("[0-9]*"):
            try:
                ns_mnt = proc_dir / "ns" / "mnt"
                if not ns_mnt.exists():
                    continue
                ino = ns_mnt.stat().st_ino
                if ino in seen_ns_inodes:
                    continue
                seen_ns_inodes.add(ino)
                mi = proc_dir / "mountinfo"
                if mi.is_file():
                    text = mi.read_text(errors="ignore")
                    for target_mp in mountinfo_targets(text):
                        run("nsenter", f"--mount=/proc/{proc_dir.name}/ns/mnt", "umount", "-R", "-f", "-l", target_mp, check=False, capture=True)
            except Exception:
                pass
    except Exception:
        pass

def is_empty_dir(p: Path):
    try:
        if not p.is_dir():
            return False
        with os.scandir(p) as it:
            return next(it,None) is None
    except:
        return False

def ensure_subvolume(path: Path, nocow=False, rescue=False) -> bool:
    if path.exists():
        r = run("btrfs","subvolume","show",str(path), check=False, capture=True)
        if r.returncode!=0:
            if rescue and path.name != "@":
                console.print(f"[yellow]Rescue mode: {path.name} is not a subvolume, skipping separate mount.[/yellow]")
                return False
            console.print(f"[red]{path} exists not subvol[/red]")
            sys.exit(1)
        existed=True
    else:
        if rescue and path.name != "@":
            console.print(f"[yellow]Rescue mode: subvolume {path.name} not present on disk, skipping creation.[/yellow]")
            return False
        run("btrfs","subvolume","create","-p",str(path), capture=True)
        existed=False
    if nocow and (not existed or is_empty_dir(path)):
        # compression=none sets NOCOMPRESS (m), which conflicts with NOCOW.
        run("chattr", "-c", str(path), check=False, capture=True)
        run("chattr", "-m", str(path), check=False, capture=True)
        run("chattr", "+C", str(path), capture=True)
    return True

def load_state():
    state={}
    if STATE_JSON.exists():
        try:
            state.update(json.loads(STATE_JSON.read_text()))
        except:
            pass
    if STATE_ENV.exists():
        try:
            script=(
                f'set +u; source {shlex.quote(str(STATE_ENV))} 2>/dev/null; '
                'printf "PROVISIONED_ROOT_PART=%s\\nPROVISIONED_EFI_PART=%s\\nENCRYPT_ROOT=%s\\nPROVISIONED_DISK=%s\\nPROVISIONED_BOOT_MODE=%s\\nPROVISIONED_STRATEGY=%s\\n" '
                '"$PROVISIONED_ROOT_PART" "$PROVISIONED_EFI_PART" "$ENCRYPT_ROOT" "$PROVISIONED_DISK" "$PROVISIONED_BOOT_MODE" "$PROVISIONED_STRATEGY"'
            )
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
                elif k=="PROVISIONED_DISK":
                    state.setdefault("disk",v)
                elif k=="PROVISIONED_BOOT_MODE":
                    state.setdefault("boot_mode",v)
                elif k=="PROVISIONED_STRATEGY" and v.isdigit():
                    state.setdefault("strategy",int(v))
        except:
            pass
    return state

def get_partition_path(disk,num):
    disk=disk.rstrip("/")
    num_str=str(num)
    name=Path(disk).name
    if (Path("/sys/class/block") / name / "partition").exists():
        return disk
    if name and name[-1].isdigit():
        return f"{disk}p{num_str}"
    return f"{disk}{num_str}"

def load_root_password():
    cred_file = Path("./.arch_credentials")
    if not cred_file.exists():
        return None
    try:
        script = f'set +u; source {shlex.quote(str(cred_file))} && printf "%s" "$ROOT_PASS"'
        result = subprocess.run(["bash", "-c", script], capture_output=True, check=True, timeout=5)
        # Binary output preserves the exact key bytes, including whitespace.
        return bytearray(result.stdout) if result.stdout else None
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None

def _is_valid_block_partition(rp: Path) -> bool:
    if not rp.exists() or not rp.is_block_device():
        return False
    return (Path("/sys/class/block") / rp.name / "partition").exists()

def determine_root_partition(auto_mode):
    state=load_state()
    provisioned = state.get("root_part")
    mapped = Path("/dev/mapper/cryptroot")
    use_provisioned = bool(provisioned) and (
        auto_mode or not sys.stdin.isatty() or Confirm.ask(f"Use provisioned ROOT partition {provisioned}?", console=console, default=True)
    )
    if use_provisioned:
        root_part = Path(provisioned).resolve()
        if not root_part.exists():
            console.print(f"[red]Provisioned root {root_part} is missing.[/red]")
            sys.exit(1)
    elif auto_mode and state.get("encrypt") is True and mapped.exists():
        root_part = mapper_backing()
    elif auto_mode:
        r=run("lsblk","-pnro","NAME,FSTYPE,LABEL",check=False,capture=True)
        btrfs_parts=[]
        duskies=[]
        for line in r.stdout.splitlines():
            cols=line.split()
            if len(cols)<2:
                continue
            name, fstype = cols[0], cols[1]
            label=cols[2] if len(cols)>2 else ""
            if fstype in ("btrfs", "crypto_LUKS"):
                if label==DUSKY_ROOT_LABEL:
                    duskies.append(name)
                if fstype=="btrfs":
                    btrfs_parts.append(name)
        if len(duskies)==1:
            root_part=Path(duskies[0]).resolve()
            mapped_root=root_part
        elif len(btrfs_parts)==1:
            root_part=Path(btrfs_parts[0]).resolve()
            mapped_root=root_part
        else:
            console.print("[red]Cannot auto-detect btrfs/LUKS root[/red]")
            sys.exit(1)
    else:
        r=run("lsblk","-l","-o","NAME,SIZE,TYPE,FSTYPE,LABEL,PARTLABEL",check=False,capture=True)
        console.print(r.stdout, markup=False)
        while True:
            raw=Prompt.ask("Enter DUSKY BTRFS/LUKS root partition (e.g. vda2)",console=console)
            if not VALID_PART_RE.match(raw):
                console.print("[red]Invalid characters in path[/red]")
                continue
            name=raw.removeprefix("/dev/")
            p=Path("/dev")/name
            try:
                rp=p.resolve()
                if not _is_valid_block_partition(rp):
                    console.print(f"[red]{rp} is not an existing block partition[/red]")
                    continue
                root_part=rp
                mapped_root=rp
                break
            except Exception as e:
                console.print(Text(str(e), style="red"))
    if mapped.exists() and root_part == mapped.resolve():
        root_part = mapper_backing()
    encrypted = run("cryptsetup", "isLuks", str(root_part), check=False).returncode == 0
    if state.get("encrypt") is True and not encrypted:
        console.print(f"[red]{root_part} is not the expected LUKS root.[/red]")
        sys.exit(1)
    mapped_root = root_part
    if encrypted:
        if mapped.exists() and mapper_backing() != root_part:
            run("cryptsetup", "close", "cryptroot", check=False, capture=True)
        if not mapped.exists():
            args = ["cryptsetup", "open", "--allow-discards"]
            block = Path("/sys/class/block") / root_part.name
            if (block / "partition").exists():
                block = block.resolve().parent
            try:
                if (block / "queue/rotational").read_text().strip() == "0":
                    args += ["--perf-no_read_workqueue", "--perf-no_write_workqueue"]
            except OSError:
                pass
            unlocked = False
            password = load_root_password()
            if password:
                try:
                    r_open = run(*args, "--key-file", "-", str(root_part), "cryptroot", input_text=password, check=False)
                    unlocked = r_open.returncode == 0 and mapped.exists()
                finally:
                    password[:] = b"\0" * len(password)
            if not unlocked and sys.stdin.isatty():
                for attempt in range(1, 4):
                    try:
                        pw_str = getpass.getpass(f"Enter LUKS passphrase for {root_part} (attempt {attempt}/3): ")
                    except EOFError:
                        break
                    if not pw_str:
                        break
                    pw_ba = bytearray(pw_str.encode("utf-8"))
                    try:
                        r_open = run(*args, "--key-file", "-", str(root_part), "cryptroot", input_text=pw_ba, check=False)
                        if r_open.returncode == 0 and mapped.exists():
                            unlocked = True
                            break
                        console.print("[red]Incorrect passphrase.[/red]")
                    finally:
                        pw_ba[:] = b"\0" * len(pw_ba)
            if not unlocked or not mapped.exists() or mapper_backing() != root_part:
                console.print("[red]Cannot unlock root: passphrase missing or invalid.[/red]")
                sys.exit(1)
        mapped_root = mapped
    run("udevadm", "settle", "--timeout=5", check=False, capture=True)
    r = run("lsblk", "-ndlo", "PKNAME", str(root_part), check=False)
    parents = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    if parents:
        root_disk = Path("/dev") / parents[0]
    elif state.get("disk") and Path(state["disk"]).exists():
        root_disk = Path(state["disk"])
    else:
        root_disk = root_part
    return mapped_root, root_part, root_disk.resolve()


def mapper_backing():
    result = run("cryptsetup", "status", "cryptroot")
    for line in result.stdout.splitlines():
        if line.strip().startswith("device:"):
            return Path(line.split(":", 1)[1].strip()).resolve()
    console.print("[red]Cannot determine cryptroot's backing device.[/red]")
    sys.exit(1)

def probe_fstype(dev):
    """
    Direct blkid probe, bypassing the udev database and cache. lsblk reads fstype from
    udev's cache, which can be stale or empty for a partition that was just
    formatted this boot (or when udevd is wedged), which used to abort the
    install with a bare 'not btrfs' even though the filesystem was fine.
    """
    r=run("blkid","-p","-c","/dev/null","-o","value","-s","TYPE",str(dev),check=False,capture=True)
    val=(r.stdout or "").strip().lower()
    if val:
        return val
    r=run("blkid","-c","/dev/null","-o","value","-s","TYPE",str(dev),check=False,capture=True)
    return (r.stdout or "").strip().lower()

def validate_root_state(mapped_root):
    if not mapped_root.exists():
        console.print(f"[red]{mapped_root} not found[/red]")
        sys.exit(1)
    fstype = probe_fstype(mapped_root)
    if fstype!="btrfs":
        console.print(f"[red]{mapped_root} is '{fstype or 'no filesystem'}', expected btrfs.[/red]")
        console.print("[yellow]This usually means the partition selected as ROOT in the partitioning step was never formatted:[/yellow]")
        console.print("[yellow]- re-run the installer, pick the partitioning step again, and make sure the partition you plan to boot from is selected as ROOT[/yellow]")
        console.print("[yellow]- if you are dual-booting, ROOT is your NEW linux partition, not the Windows data (ntfs) or the EFI (vfat) partition[/yellow]")
        try:
            r2=run("lsblk","-f",str(mapped_root),check=False,capture=True)
            if r2.stdout.strip():
                console.print(Panel.fit(Text(r2.stdout), title="device details", box=box.ROUNDED, border_style="dim"))
        except Exception:
            pass
        sys.exit(1)

def validate_efi_partition(part):
    part_type = run("blkid", "-p", "-c", "/dev/null", "-o", "value", "-s", "PART_ENTRY_TYPE", str(part), check=False).stdout.strip().lower()
    if part_type not in (EFI_GPT_TYPE, "0xef") or probe_fstype(part) != "vfat":
        console.print(f"[red]{part} must be an EFI System Partition containing FAT.[/red]")
        sys.exit(1)

def is_mounted(dev):
    try:
        r=run("findmnt","-n","-o","TARGET","--source",dev,check=False,capture=True)
        return r.stdout.strip() or None
    except:
        return None

def flatten_lsblk(data):
    nodes = []
    def _walk(item_list):
        for item in item_list:
            nodes.append(item)
            if item.get("children"):
                _walk(item.get("children"))
    if isinstance(data, dict):
        _walk(data.get("blockdevices", []))
    elif isinstance(data, list):
        _walk(data)
    return nodes

def auto_detect_efi_partition(root_disk, root_part):
    try:
        result = run("lsblk", "--json", "--paths", "--tree", "-o",
                     "PATH,TYPE,PARTTYPE,PARTLABEL,LABEL", str(root_disk))
        candidates = []
        preferred = []
        for node in flatten_lsblk(json.loads(result.stdout)):
            if node.get("type") != "part" or (node.get("parttype") or "").lower() not in (EFI_GPT_TYPE, "0xef"):
                continue
            part = Path(node["path"]).resolve()
            if part == root_part.resolve():
                continue
            candidates.append(part)
            if DUSKY_EFI_LABEL in (node.get("label"), node.get("partlabel")):
                preferred.append(part)
        if len(preferred) == 1:
            return preferred[0]
        if len(candidates) == 1:
            return candidates[0]
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError):
        pass
    return None

def prompt_for_efi_partition(root_disk):
    r=run("lsblk","-l","-o","NAME,SIZE,TYPE,FSTYPE,PARTTYPE,PARTLABEL,LABEL",str(root_disk),check=False,capture=True)
    console.print(r.stdout, markup=False)
    while True:
        raw=Prompt.ask("Enter EFI partition (e.g. vda1)",console=console)
        if not VALID_PART_RE.match(raw):
            console.print("[red]Invalid[/red]")
            continue
        name=raw.removeprefix("/dev/")
        p=Path("/dev")/name
        try:
            rp=p.resolve()
            if _is_valid_block_partition(rp):
                return rp
            console.print(f"[red]{rp} is not an existing block partition[/red]")
        except Exception as e:
            console.print(Text(str(e), style="red"))

def determine_efi_partition(auto_mode,root_disk,root_part):
    if BOOT_MODE!="UEFI":
        return None
    state=load_state()
    if state.get("strategy") == 4 and "efi_part" in state and not state.get("efi_part"):
        console.print("[cyan]Rescue mode: EFI partition skipped by user selection.[/cyan]")
        return None
    prov=state.get("efi_part")
    if prov and (auto_mode or not sys.stdin.isatty() or Confirm.ask(f"Use provisioned EFI partition {prov}?", console=console, default=True)):
        if not Path(prov).exists():
            console.print(f"[red]Provisioned EFI {prov} is missing.[/red]")
            sys.exit(1)
        console.print(f"[cyan]Auto EFI {prov}[/cyan]")
        return Path(prov).resolve()
    det=auto_detect_efi_partition(root_disk,root_part)
    if det:
        console.print(f"[cyan]Auto EFI {det}[/cyan]")
        return det
    if auto_mode or not sys.stdin.isatty():
        console.print("[red]No unambiguous EFI partition found; select it in the partitioning step.[/red]")
        sys.exit(1)
    return prompt_for_efi_partition(root_disk)

def construct_subvolume_matrix(mapped_root) -> set[str]:
    state = load_state()
    rescue = state.get("strategy") == 4
    active_subvols: set[str] = set()
    console.print("[yellow]>> Constructing DUSKY Subvolume Matrix...[/yellow]")
    tmpdir=Path(tempfile.mkdtemp(prefix="dusky-btrfs-",dir="/tmp"))
    try:
        run("mount","-t","btrfs","-o","subvolid=5",str(mapped_root),str(tmpdir),capture=True)
        if rescue and not (tmpdir / "@").exists():
            # Flat Btrfs root layout without '@' subvolume
            console.print("[cyan]Rescue mode: root filesystem does not use '@' subvolume; mounting top-level subvolid=5.[/cyan]")
            return active_subvols
        for sub in STD_SUBVOLS:
            if ensure_subvolume(tmpdir/sub,nocow=False,rescue=rescue):
                active_subvols.add(sub)
        for sub in NOCOW_SUBVOLS:
            if ensure_subvolume(tmpdir/sub,nocow=True,rescue=rescue):
                active_subvols.add(sub)
        console.print("[green]>> Matrix OK[/green]")
        return active_subvols
    finally:
        run("umount",str(tmpdir),check=False,capture=True)
        try:
            tmpdir.rmdir()
        except:
            pass

def assemble_fhs(mapped_root, efi_part, active_subvols: set[str] | None = None):
    state = load_state()
    strategy = state.get("strategy", 1)
    if active_subvols is None:
        active_subvols = set(STD_SUBVOLS) | set(NOCOW_SUBVOLS)
    console.print("[yellow]>> Assembling FHS to /mnt...[/yellow]")
    Path("/mnt").mkdir(parents=True,exist_ok=True)
    if "@" in active_subvols:
        run("mount","-o",f"{BTRFS_OPTS},subvol=@",str(mapped_root),"/mnt",capture=True)
    else:
        run("mount","-o",f"{BTRFS_OPTS},subvolid=5",str(mapped_root),"/mnt",capture=True)
    
    # Removed "home/.snapshots" from this loop to prevent creating a masked directory inside the @ subvolume root
    for mp in ["home",".snapshots","var/log","var/cache","var/tmp","var/lib/machines","var/lib/portables","var/lib/libvirt","var/lib/mysql","var/lib/postgres","swap","boot","proc","sys","dev","run","etc","tmp","root","mnt","opt","srv"]:
        Path(f"/mnt/{mp}").mkdir(parents=True,exist_ok=True)
        
    mounts=[
        ("@home", f"{BTRFS_OPTS},subvol=@home","/mnt/home"),
        ("@snapshots", f"{BTRFS_OPTS},subvol=@snapshots","/mnt/.snapshots"),
        ("@var_log", f"{BTRFS_OPTS},subvol=@var_log","/mnt/var/log"),
        ("@var_cache", f"{BTRFS_OPTS},subvol=@var_cache","/mnt/var/cache"),
        ("@var_tmp", f"{BTRFS_OPTS},subvol=@var_tmp","/mnt/var/tmp"),
        ("@var_lib_machines", f"{BTRFS_OPTS},subvol=@var_lib_machines","/mnt/var/lib/machines"),
        ("@var_lib_portables", f"{BTRFS_OPTS},subvol=@var_lib_portables","/mnt/var/lib/portables"),
        ("@var_lib_libvirt", f"{BTRFS_OPTS},subvol=@var_lib_libvirt","/mnt/var/lib/libvirt"),
        ("@var_lib_mysql", f"{BTRFS_OPTS},subvol=@var_lib_mysql","/mnt/var/lib/mysql"),
        ("@var_lib_postgres", f"{BTRFS_OPTS},subvol=@var_lib_postgres","/mnt/var/lib/postgres"),
        ("@swap", f"{BTRFS_OPTS},subvol=@swap","/mnt/swap"),
    ]
    for sub_name, opts, tgt in mounts:
        if sub_name in active_subvols:
            run("mount","-o",opts,str(mapped_root),tgt,capture=True)
        
    if "@home_snapshots" in active_subvols:
        # Safely created over the active @home subvolume mount
        Path("/mnt/home/.snapshots").mkdir(parents=True,exist_ok=True)
        run("mount","-o",f"{BTRFS_OPTS},subvol=@home_snapshots",str(mapped_root),"/mnt/home/.snapshots",capture=True)
    
    if BOOT_MODE=="UEFI" and efi_part:
        console.print(f"[yellow]>> Mounting EFI {efi_part} to /mnt/boot (hardened)...[/yellow]")
        run("mount","-t","vfat","-o","fmask=0077,dmask=0077,noexec,nosuid,nodev",str(efi_part),"/mnt/boot",capture=True)
        if strategy != 4:
            sync_secondary_efi_bootloaders("/mnt/boot", str(efi_part))

    if STATE_JSON.exists() and strategy != 4:
        try:
            chroot_etc = Path("/mnt/etc")
            chroot_etc.mkdir(parents=True, exist_ok=True)
            dst_state = chroot_etc / "dusky_state.json"
            dst_state.write_text(STATE_JSON.read_text(errors="ignore"), encoding="utf-8")
            dst_state.chmod(0o600)
        except Exception:
            pass

def sync_secondary_efi_bootloaders(primary_esp_mnt: str = "/mnt/boot", primary_esp_dev: str | None = None):
    """
    Best-effort copy of vendor EFI dirs from other ESPs (dual-boot / extra USB).
    Must NEVER abort the install: missing secondary media, busy devices, or
    copy errors are warnings only. Previously a missing `import shutil` turned
    any secondary vfat into a hard Fatal.
    """
    if BOOT_MODE != "UEFI":
        return
    try:
        console.print("[yellow]>> Scanning for secondary EFI bootloaders to synchronize...[/yellow]")
        r = run("lsblk", "--json", "--paths", "--tree", "-o", "PATH,TYPE,FSTYPE,PARTTYPE,LABEL,MOUNTPOINTS", check=False, capture=True)
        if r.returncode != 0 or not r.stdout:
            return
        try:
            data = json.loads(r.stdout)
        except Exception:
            return

        esp_guid = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
        primary_esp_path = ""
        if primary_esp_dev:
            try:
                primary_esp_path = str(Path(primary_esp_dev).resolve())
            except Exception:
                pass

        if not primary_esp_path:
            try:
                r_mnt = run("findmnt", "-n", "-e", "-o", "SOURCE", primary_esp_mnt, check=False, capture=True)
                src = (r_mnt.stdout or "").strip()
                if src and src.startswith("/"):
                    primary_esp_path = str(Path(src).resolve())
            except Exception:
                pass

        target_efi_dir = Path(primary_esp_mnt) / "EFI"
        target_efi_dir.mkdir(parents=True, exist_ok=True)

        for child in flatten_lsblk(data):
            try:
                path = child.get("path") or child.get("name")
                if not path:
                    continue
                dev_type = (child.get("type") or "").lower()
                # Only real partitions. Whole disks / loops / roms are not dual-boot ESPs
                if dev_type and dev_type != "part":
                    continue

                try:
                    p_res = str(Path(path).resolve())
                except Exception:
                    p_res = path

                if primary_esp_path and p_res == primary_esp_path:
                    continue

                # Never touch live ISO / airootfs backing devices or existing /mnt mounts
                mps = child.get("mountpoints") or child.get("mountpoint") or []
                if isinstance(mps, str):
                    mps = [mps]
                if any(isinstance(m, str) and (m.startswith("/run/archiso") or m.startswith("/mnt") or m in ("/", "/boot", "/efi")) for m in mps if m):
                    continue

                lbl = (child.get("label") or "").strip().upper()
                if lbl.startswith("ARCH_") or lbl.startswith("ARCHISO"):
                    continue

                ptype = (child.get("parttype") or "").lower()
                fstype = (child.get("fstype") or "").lower()
                is_esp = ptype == esp_guid
                is_vfat = fstype in ("vfat", "fat32")
                if not (is_esp and is_vfat):
                    continue

                tmp_dir = None
                try:
                    tmp_dir = tempfile.mkdtemp(prefix="dusky_sec_esp_")
                    m_res = run("mount", "-t", "vfat", "-o", "ro,noexec,nosuid,nodev", p_res, tmp_dir, check=False, capture=True)
                    if m_res.returncode != 0:
                        continue
                    sec_efi = Path(tmp_dir) / "EFI"
                    if not sec_efi.is_dir():
                        continue
                    for vendor_dir in sec_efi.iterdir():
                        if not vendor_dir.is_dir():
                            continue
                        v_name = vendor_dir.name
                        if not v_name or v_name.startswith(".") or v_name.lower() in ("boot", "systemd"):
                            continue
                        dst_vendor = target_efi_dir / v_name
                        if dst_vendor.exists():
                            continue
                        console.print(f"[cyan]Syncing secondary EFI vendor directory '{v_name}' from {p_res} -> {dst_vendor}[/cyan]")
                        try:
                            with tempfile.TemporaryDirectory(prefix=".dusky-efi-", dir=target_efi_dir) as staging:
                                candidate = Path(staging) / v_name
                                shutil.copytree(vendor_dir, candidate, copy_function=shutil.copy2)
                                candidate.rename(dst_vendor)
                        except Exception as e:
                            console.print(f"[yellow]Warning syncing {v_name}: {e}[/yellow]")
                except Exception as e:
                    console.print(f"[yellow]Warning: secondary ESP {path}: {e}[/yellow]")
                finally:
                    if tmp_dir:
                        run("umount", tmp_dir, check=False, capture=True)
                        try:
                            Path(tmp_dir).rmdir()
                        except Exception:
                            pass
            except Exception as e:
                console.print(f"[yellow]Warning: secondary EFI candidate skipped: {e}[/yellow]")
                continue
    except Exception as e:
        console.print(f"[yellow]Warning: secondary EFI sync skipped: {e}[/yellow]")
        return

def get_free_bytes(path: Path | str) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def initialize_swapfile():
    state = load_state()
    rescue = state.get("strategy") == 4
    console.print("[yellow]>> Ensuring swapfile...[/yellow]")
    if not SWAPFILE_PATH.parent.is_dir():
        return
    # Never remove an active file when swapoff fails, and leave unrelated swap alone.
    result = run("swapon", "--show=NAME", "--raw", "--noheadings", check=False)
    for line in (result.stdout or "").splitlines():
        name = re.sub(r"\\x([0-9a-fA-F]{2})", lambda match: chr(int(match[1], 16)), line.strip())
        try:
            same_file = os.path.samefile(name, SWAPFILE_PATH)
        except OSError:
            same_file = name == str(SWAPFILE_PATH)
        if same_file:
            run("swapoff", name)

    if SWAPFILE_PATH.exists() and not SWAPFILE_PATH.is_file():
        raise RuntimeError(f"{SWAPFILE_PATH} is not a regular file")
    if rescue:
        if SWAPFILE_PATH.is_file() and run("swapon", str(SWAPFILE_PATH), check=False).returncode == 0:
            console.print("[green]>> Rescue mode: existing swapfile activated[/green]")
        else:
            console.print("[cyan]>> Rescue mode: skipping swapfile creation[/cyan]")
        return
    try:
        existing_size = SWAPFILE_PATH.stat().st_size if SWAPFILE_PATH.is_file() else 0
        available = get_free_bytes(SWAPFILE_PATH.parent) + existing_size
    except OSError as error:
        console.print(Text(f"Warning: Cannot determine swap space: {error}", style="yellow"))
        return
    minimum = 256 * 1024**2
    desired_size = min(4 * 1024**3, int(available * 0.8))
    if desired_size < minimum:
        console.print("[yellow]Warning: Insufficient space for a swapfile[/yellow]")
        return
    size_str = f"{desired_size // (1024**2)}M"
    if existing_size >= minimum and abs(existing_size - desired_size) < 512 * 1024**2:
        if run("swapon", str(SWAPFILE_PATH), check=False).returncode == 0:
            console.print("[green]>> Existing swapfile re-activated[/green]")
            return

    SWAPFILE_PATH.unlink(missing_ok=True)
    run("chattr", "+C", str(SWAPFILE_PATH.parent), check=False, capture=True)
    result = run("btrfs", "filesystem", "mkswapfile", "--size", size_str,
                 "--uuid", "clear", str(SWAPFILE_PATH), check=False)
    if result.returncode == 0 and SWAPFILE_PATH.is_file():
        if run("swapon", str(SWAPFILE_PATH), check=False).returncode == 0:
            console.print(f"[green]>> Swapfile ({size_str}) created and activated.[/green]")
            return
    detail = (result.stderr or "").strip()
    console.print(Text(f"Warning: Swapfile activation skipped. {detail}", style="yellow"))


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
            if efi_part == root_part.resolve():
                console.print("[red]ROOT and EFI cannot be the same partition.[/red]")
                sys.exit(1)
            validate_efi_partition(efi_part)
            temporary = None
            try:
                mountpoint = is_mounted(str(efi_part))
                if not mountpoint:
                    temporary = tempfile.mkdtemp(prefix="dusky_efi_check_")
                    result = run("mount", "-t", "vfat", "-o", "ro,noexec,nosuid,nodev",
                                 str(efi_part), temporary, check=False)
                    mountpoint = temporary if result.returncode == 0 else None
                if mountpoint and Path(mountpoint, "EFI", "Microsoft").is_dir():
                    console.print(Text(f"Dual-boot Windows on {efi_part}, preserving", style="cyan"))
            except (OSError, subprocess.TimeoutExpired) as error:
                console.print(Text(f"Warning: EFI inspection skipped: {error}", style="yellow"))
            finally:
                if temporary:
                    run("umount", temporary, check=False)
                    try:
                        Path(temporary).rmdir()
                    except OSError:
                        pass
    active_subvols = construct_subvolume_matrix(mapped_root)
    assemble_fhs(mapped_root, efi_part, active_subvols)
    initialize_swapfile()
    console.print(Align.center(Panel.fit("[bold green]>> DUSKY Setup Complete[/bold green]", box=box.ROUNDED, border_style="green")))
    try:
        r=run("lsblk","-l","-f",str(root_disk),check=False,capture=True)
        console.print(Align.center(Panel.fit(Text(r.stdout), title=f"lsblk {root_disk}", box=box.ROUNDED, border_style="dim")))
    except:
        pass
    try:
        r=run("findmnt","-R","/mnt",check=False,capture=True)
        console.print(Align.center(Panel.fit(Text(r.stdout), title="findmnt /mnt", box=box.ROUNDED, border_style="dim")))
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
