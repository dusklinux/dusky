#!/usr/bin/env python3
# DUSKY_INTERACTIVE=true
"""
Prepare disk partitions and filesystems for the offline DUSKY installer.
Supports whole-disk installation, existing partitions, manual editing, and rescue.
Writes the selected storage layout for 040_disk_mount.py to consume.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import getpass
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from typing import Literal


def _ensure_rich() -> None:
    import importlib.util

    if importlib.util.find_spec("rich") is None:
        print("python-rich is required in the offline ISO", file=sys.stderr)
        raise SystemExit(1)


_ensure_rich()
from rich import box
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text


def make_console() -> Console:
    term = os.environ.get("TERM", "")
    if term in ("dumb", "unknown", ""):
        os.environ["TERM"] = "linux"
        term = "linux"
    is_basic_vt = term in ("linux", "dumb", "vt100")
    return Console(
        color_system="standard" if is_basic_vt else "auto",
        force_terminal=None,
        legacy_windows=False,
        safe_box=is_basic_vt,
        highlight=False,
        markup=True,
    )


console = make_console()


def refresh_console() -> None:
    global console
    console = make_console()


def restore_terminal() -> None:
    """Ensure cursor-key/mouse modes are off, cursor is visible, and termios is sane."""
    try:
        sys.stdout.write(
            "\x1b[?1049l\x1b[?1l\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?2004l\x1b[?25h\x1b[0m"
        )
        sys.stdout.flush()
    except Exception:
        pass
    try:
        if sys.stdin.isatty():
            subprocess.run(
                ["stty", "sane"],
                check=False,
                stdin=sys.stdin,
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
    except Exception:
        pass
    refresh_console()


import platform

TARGET_CRYPT_NAME = "cryptroot"
EFI_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
BIOS_GUID = "21686148-6449-6e6f-744e-656564454649"
LINUX_ROOT_X86_64_GUID = "4f68bce3-e8cd-4db1-96e7-fbcaf984b709"
LINUX_ROOT_AARCH64_GUID = "b921b045-1df0-41c3-af44-4c6f280d3fae"
LINUX_GENERIC_DATA_GUID = "0fc63daf-8483-4772-8e79-3d69d8477de4"


def get_linux_root_guid() -> str:
    arch = platform.machine().lower()
    if arch == "x86_64":
        return LINUX_ROOT_X86_64_GUID
    if arch == "aarch64":
        return LINUX_ROOT_AARCH64_GUID
    return LINUX_GENERIC_DATA_GUID


LINUX_ROOT_GUID = get_linux_root_guid()
LUKS_GUID = "ca7d7ccb-63ed-4c53-861c-1742536059cc"
DUSKY_EFI_LABEL = "DUSKY_EFI"
DUSKY_ROOT_LABEL = "DUSKY_ROOT"
DUSKY_BIOS_LABEL = "DUSKY_BIOS"
DUSKY_EFI_PARTNAME = "DUSKY_EFI"
DUSKY_ROOT_PARTNAME = "DUSKY_ROOT"
STATE_ENV = Path("/tmp/arch_install_state.env")
STATE_JSON = Path("/tmp/dusky_state.json")
STORAGE_STATE_JSON = Path("/root/arch_install/storage_state.json")
VALID_PART_RE = re.compile(r"^[a-zA-Z0-9_./-]+$")
BTRFS_CSUM = "blake2"

STRATEGY_LABELS: dict[int, str] = {
    1: "Wipe Entire Drive (Default)",
    2: "Select Existing (Dual Boot)",
    3: "Manual Partitioning (cfdisk)",
    4: "Rescue / Chroot (Mount Only)",
}


@dataclass
class PartitionPlan:
    disk: str
    boot_mode: Literal["UEFI", "BIOS"]
    strategy: int
    efi_size: str = "1.3G"
    root_part: str = ""
    efi_part: str = ""
    format_efi: bool = True
    encrypt: bool = False
    luks_ba: bytearray | None = None
    has_win: bool = False
    win_esp: str | None = None


def password_error(value: str) -> str:
    if not value:
        return "Enter a passphrase."
    if any(character in value for character in "\n\r\x00"):
        return "Passphrases must be a single line without NUL characters."
    return ""


def run(*cmd, check=True, capture=True, input_text=None, timeout=300):
    argv = [os.fspath(c) for c in cmd]
    binary = isinstance(input_text, (bytes, bytearray))
    try:
        return subprocess.run(
            argv,
            check=check,
            capture_output=capture,
            timeout=timeout,
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
            argv,
            124,
            stdout=(error.stdout or b"") if binary else (error.stdout or ""),
            stderr=detail.encode() if binary else detail,
        )
    except OSError:
        if check:
            raise
        detail = f"{argv[0]}: not available"
        return subprocess.CompletedProcess(
            argv,
            127,
            stdout=b"" if binary else "",
            stderr=detail.encode() if binary else detail,
        )


def mountinfo_targets(text: str, prefix: str = "/mnt") -> list[str]:
    """Return a mount tree, decoding mountinfo path escapes."""
    targets = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 6 or " - " not in line:
            continue
        target = re.sub(
            r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[4]
        )
        if target == prefix or target.startswith(prefix.rstrip("/") + "/"):
            targets.add(target)
    return sorted(
        targets, key=lambda path: (path.count("/"), len(path)), reverse=True
    )


def print_banner(boot_mode: str) -> None:
    txt = Text.from_markup(
        f"[bold cyan]DUSKY[/] Partitioning [dim]{boot_mode}[/]",
        justify="center",
    )
    panel = Panel.fit(txt, box=box.ROUNDED, border_style="cyan", padding=(0, 2))
    console.print(Align.center(panel))


def get_partition_path(disk: str, num: int | str) -> str:
    disk = disk.rstrip("/")
    num_str = str(num)
    name = Path(disk).name
    if (Path("/sys/class/block") / name / "partition").exists():
        return disk
    if name and name[-1].isdigit():
        return f"{disk}p{num_str}"
    return f"{disk}{num_str}"


def detect_boot_mode() -> Literal["UEFI", "BIOS"]:
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
            if len(fields) >= 3 and (
                fields[0] == "efivarfs" or fields[2] == "efivarfs"
            ):
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


def parse_credentials(path: str = "./.arch_credentials") -> dict[str, str]:
    out: dict[str, str] = {}
    candidates = [Path(path), Path("/root/arch_install/.arch_credentials")]
    cred = next((p for p in candidates if p.is_file()), None)
    if cred is None:
        return out
    wanted = {"TARGET_USER", "ENCRYPT_ROOT", "ROOT_PASS"}
    try:
        for raw_line in cred.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            k, raw_v = line.split("=", 1)
            k = k.strip()
            if k not in wanted:
                continue
            tokens = shlex.split(raw_v, posix=True)
            out[k] = tokens[0] if tokens else ""
    except Exception:
        pass
    return out


def udev_sync(timeout: int = 10) -> None:
    """Synchronize with udev event queue before querying block metadata."""
    run("udevadm", "settle", f"--timeout={timeout}", check=False, capture=True)


def lsblk_all() -> dict:
    udev_sync(5)
    r = run(
        "lsblk",
        "--json",
        "--bytes",
        "--paths",
        "--tree",
        "-o",
        "NAME,PATH,KNAME,PKNAME,TYPE,RO,FSTYPE,PARTTYPE,PARTLABEL,LABEL,SIZE,MODEL,MOUNTPOINTS",
        check=False,
        capture=True,
    )
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"blockdevices": []}


def _is_live_iso_disk(dev: dict) -> bool:
    """Return True if the disk is read-only or backs the running Arch ISO (including copytoram)."""
    if str(dev.get("ro", "0")).strip() in ("1", "true", "True"):
        return True
    iso_labels: set[str] = set()
    iso_devs: set[str] = set()
    try:
        cmdline = Path("/proc/cmdline").read_text(errors="ignore")
        for key in ("archisolabel", "img_label"):
            m = re.search(rf"(?:^|\s){key}=([^\s]+)", cmdline)
            if m:
                iso_labels.add(m.group(1).strip().upper())
        m_dev = re.search(r"(?:^|\s)img_dev=([^\s]+)", cmdline)
        if m_dev and Path(m_dev.group(1)).exists():
            iso_devs.add(str(Path(m_dev.group(1)).resolve()))
    except OSError:
        pass
    nodes = [dev]
    while nodes:
        node = nodes.pop()
        fstype = str(node.get("fstype") or "").lower()
        if fstype in ("iso9660", "udf", "squashfs", "erofs"):
            return True
        for lbl_key in ("label", "partlabel"):
            lbl = str(node.get(lbl_key) or "").strip().upper()
            if lbl and (
                lbl in iso_labels
                or lbl.startswith("ARCHISO")
                or bool(re.fullmatch(r"ARCH_\d{6}", lbl))
            ):
                return True
        npath = node.get("path") or node.get("name")
        if npath and iso_devs:
            try:
                if str(Path(npath).resolve()) in iso_devs:
                    return True
            except OSError:
                pass
        mps = node.get("mountpoints") or node.get("mountpoint") or []
        if isinstance(mps, str):
            mps = [mps]
        for mp in mps:
            if isinstance(mp, str) and (
                mp.startswith("/run/archiso")
                or mp in ("/", "/airootfs")
            ):
                return True
        nodes.extend(node.get("children") or [])
    return False


def list_disks() -> list[dict]:
    data = lsblk_all()
    disks = []
    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk":
            continue
        name = dev.get("name", "")
        if (
            name.startswith("/dev/loop")
            or name.startswith("/dev/zram")
            or name.startswith("/dev/ram")
            or name.startswith("/dev/sr")
        ):
            continue
        if _is_live_iso_disk(dev):
            continue
        try:
            if int(dev.get("size", 0)) < 1 * 1024 * 1024 * 1024:
                continue
        except Exception:
            continue
        disks.append(dev)
    return disks


def device_is_on_disk(node: str, disk: str, max_depth: int = 32) -> bool:
    """Walk every sysfs backing device, including multi-device LVM/DM stacks."""
    target = Path(disk).resolve().name
    pending = [(Path(node).resolve().name, 0)]
    seen = set()
    while pending:
        name, depth = pending.pop()
        if name in seen or depth >= max_depth:
            continue
        seen.add(name)
        if name == target:
            return True
        block = Path("/sys/class/block") / name
        try:
            if (block / "partition").exists():
                pending.append((block.resolve().parent.name, depth + 1))
            slaves = block / "slaves"
            if slaves.is_dir():
                pending.extend(
                    (slave.name, depth + 1) for slave in slaves.iterdir()
                )
        except OSError:
            continue
    return False


def findmnt_targets(prefix: str = "/mnt") -> list[str]:
    try:
        r = run(
            "findmnt",
            "--json",
            "--list",
            "--submounts",
            "--output",
            "TARGET",
            "--mountpoint",
            prefix,
            check=False,
            capture=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            t = [
                fs.get("target", "")
                for fs in data.get("filesystems", [])
                if fs.get("target", "") == prefix
                or fs.get("target", "").startswith(prefix.rstrip("/") + "/")
            ]
            return sorted(
                set(t), key=lambda p: (p.count("/"), len(p)), reverse=True
            )
    except Exception:
        pass
    try:
        text = Path("/proc/self/mountinfo").read_text()
        targets = mountinfo_targets(text, prefix)
        return sorted(
            targets, key=lambda p: (p.count("/"), len(p)), reverse=True
        )
    except Exception:
        return []


def wait_for_dev(path: str, timeout: int = 10) -> bool:
    p = Path(path)
    try:
        r = run(
            "udevadm",
            "wait",
            "--timeout",
            str(timeout),
            "--settle",
            str(p),
            check=False,
            capture=True,
            timeout=timeout + 2,
        )
        if r.returncode == 0 and p.exists():
            return True
    except Exception:
        pass
    for _ in range(timeout * 10):
        if p.exists():
            return True
        time.sleep(0.1)
    return p.exists()


def prompt_luks_password(confirm: bool = True) -> bytearray | None:
    while True:
        try:
            pw = getpass.getpass("Enter LUKS passphrase (empty to abort): ")
        except EOFError:
            console.print("[red]No TTY[/red]")
            sys.exit(1)
        if not pw:
            return None
        err = password_error(pw)
        if err:
            console.print(f"[red]{err}[/red]")
            continue
        if not confirm:
            return bytearray(pw.encode("utf-8"))
        try:
            pw2 = getpass.getpass("Verify: ")
        except EOFError:
            sys.exit(1)
        if pw != pw2:
            console.print("[red]Mismatch[/red]")
            continue
        return bytearray(pw.encode("utf-8"))


def is_mounted(dev: str) -> str | None:
    try:
        r = run(
            "findmnt",
            "-n",
            "-o",
            "TARGET",
            "--source",
            dev,
            check=False,
            capture=True,
        )
        return r.stdout.strip() or None
    except Exception:
        return None


def detect_windows_esp(disk: str) -> tuple[bool, str | None]:
    data = lsblk_all()
    target_resolved = str(Path(disk).resolve())
    for dev in data.get("blockdevices", []):
        dp = dev.get("path") or dev.get("name")
        try:
            dev_resolved = str(Path(dp).resolve()) if dp else ""
        except Exception:
            dev_resolved = dp
        if dev_resolved != target_resolved and dp != disk:
            if (
                f"/dev/{dev.get('name')}" != disk
                and dev.get("name") != Path(disk).name
            ):
                continue
        for child in dev.get("children", []) or []:
            fstype = (child.get("fstype") or "").lower()
            ptype = (child.get("parttype") or "").lower()
            path = child.get("path") or child.get("name")
            if not path:
                continue
            if ptype != EFI_GUID and fstype not in ("vfat", "fat32"):
                continue
            mnt = is_mounted(path)
            temporary = None
            try:
                if not mnt:
                    temporary = tempfile.mkdtemp(prefix="dusky_efi_check_")
                    result = run(
                        "mount",
                        "-t",
                        "vfat",
                        "-o",
                        "ro,noexec,nosuid,nodev",
                        path,
                        temporary,
                        check=False,
                    )
                    if result.returncode != 0:
                        continue
                    mnt = temporary
                if Path(mnt, "EFI", "Microsoft").is_dir():
                    return True, path
            except (OSError, subprocess.TimeoutExpired):
                continue
            finally:
                if temporary:
                    run("umount", temporary, check=False)
                    try:
                        Path(temporary).rmdir()
                    except OSError:
                        pass
    return False, None


def safe_deactivate_swaps_for_device(target_dev: str) -> None:
    wanted = {"/mnt/swap/swapfile", "/swap/swapfile"}
    candidates = set()
    try:
        r = run(
            "swapon",
            "--show=NAME",
            "--raw",
            "--noheadings",
            check=False,
            capture=True,
        )
        candidates.update(
            l.strip() for l in r.stdout.splitlines() if l.strip()
        )
    except Exception:
        pass
    try:
        for line in Path("/proc/swaps").read_text().splitlines()[1:]:
            name = line.split()[0].strip()
            if name:
                candidates.add(name)
    except Exception:
        pass
    for name in candidates:
        try:
            if (
                device_is_on_disk(name, target_dev)
                or name in wanted
                or (name.startswith("/mnt/") and name.endswith("swapfile"))
                or (
                    Path(name).name == "swapfile"
                    and (name.startswith("/mnt/") or name == "/swap/swapfile")
                )
            ):
                if target_dev:
                    check_src = name
                    if Path(name).is_file():
                        try:
                            r2 = run(
                                "findmnt",
                                "-rn",
                                "-T",
                                name,
                                "-o",
                                "SOURCE",
                                check=False,
                                capture=True,
                            )
                            check_src = (
                                r2.stdout.strip().split("[", 1)[0] or name
                            )
                        except Exception:
                            pass
                    if (
                        device_is_on_disk(check_src, target_dev)
                        or name in wanted
                    ):
                        run("swapoff", name, check=False, capture=True)
                else:
                    run("swapoff", name, check=False, capture=True)
        except Exception:
            pass
    for p in wanted:
        run("swapoff", p, check=False, capture=True)


def teardown_target_storage(target_dev: str, max_retries: int = 4) -> None:
    """
    Robust inverted storage teardown: VFS -> Swap -> DM -> Block Cache.
    Flushes all page cache buffers and ensures exclusive block device access before wipefs/sfdisk.
    """
    resolved_disk = str(Path(target_dev).resolve())
    console.print(
        f"[yellow]>> Initiating absolute storage teardown for {resolved_disk}...[/yellow]"
    )

    for attempt in range(1, max_retries + 1):
        # 1. Global Sync
        run("sync", check=False, capture=True)

        # 2. Aggressive Swap Deactivation
        try:
            safe_deactivate_swaps_for_device(resolved_disk)
        except Exception:
            pass

        # 3. Recursive Mount Detachment (Standard, Lazy, and Process Mount Namespace Cleanup)
        try:
            mounts = findmnt_targets("/mnt")
            for mp in mounts:
                if (
                    run("umount", "-R", mp, check=False, capture=True).returncode
                    != 0
                ):
                    run(
                        "umount",
                        "-R",
                        "-f",
                        "-l",
                        mp,
                        check=False,
                        capture=True,
                    )

            r = run(
                "findmnt",
                "-rn",
                "-o",
                "TARGET,SOURCE",
                check=False,
                capture=True,
            )
            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    tgt, src = parts[0], parts[1].split("[", 1)[0]
                    if Path(src).exists() and device_is_on_disk(
                        src, resolved_disk
                    ):
                        if (
                            run(
                                "umount", "-R", tgt, check=False, capture=True
                            ).returncode
                            != 0
                        ):
                            run(
                                "umount",
                                "-R",
                                "-f",
                                "-l",
                                tgt,
                                check=False,
                                capture=True,
                            )

            # Mount namespace cleanup
            seen_ns_inodes = set()
            try:
                seen_ns_inodes.add(Path("/proc/self/ns/mnt").stat().st_ino)
            except OSError:
                pass
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
                        targets = set(mountinfo_targets(text))
                        for line in text.splitlines():
                            fields, separator, filesystem = line.partition(
                                " - "
                            )
                            before, after = fields.split(), filesystem.split()
                            if (
                                separator
                                and len(before) >= 5
                                and len(after) >= 2
                            ):
                                source = re.sub(
                                    r"\\([0-7]{3})",
                                    lambda match: chr(int(match[1], 8)),
                                    after[1],
                                )
                                if device_is_on_disk(source, resolved_disk):
                                    targets.add(
                                        re.sub(
                                            r"\\([0-7]{3})",
                                            lambda match: chr(int(match[1], 8)),
                                            before[4],
                                        )
                                    )
                        for target_mp in sorted(
                            targets,
                            key=lambda path: (path.count("/"), len(path)),
                            reverse=True,
                        ):
                            run(
                                "nsenter",
                                f"--mount=/proc/{proc_dir.name}/ns/mnt",
                                "umount",
                                "-R",
                                "-f",
                                "-l",
                                target_mp,
                                check=False,
                                capture=True,
                            )
                except Exception:
                    pass
        except Exception:
            pass

        # 4. Device Mapper / LUKS Closure (Bottom-up leaf-first order)
        try:
            r = run("lsblk", "-pnro", "NAME,TYPE", check=False, capture=True)
            for line in reversed(r.stdout.splitlines()):
                p = line.split()
                if len(p) >= 2:
                    name, typ = p[0], p[1]
                    if typ in ("crypt", "mpath", "lvm") and device_is_on_disk(
                        name, resolved_disk
                    ):
                        dm_name = Path(name).name
                        if name.startswith("/dev/mapper/"):
                            dm_name = name.split("/")[-1]
                        if (
                            run(
                                "cryptsetup",
                                "close",
                                dm_name,
                                check=False,
                                capture=True,
                            ).returncode
                            != 0
                        ):
                            run(
                                "dmsetup",
                                "remove",
                                "--force",
                                "--retry",
                                dm_name,
                                check=False,
                                capture=True,
                            )
        except Exception:
            pass

        # 5. Flush Dirty Page Buffers
        run("blockdev", "--flushbufs", resolved_disk, check=False, capture=True)

        # 5.5. Deregister Btrfs kernel device references scoped strictly to resolved_disk.
        try:
            for btrfs_uuid_dir in Path("/sys/fs/btrfs").iterdir():
                if btrfs_uuid_dir.name == "features":
                    continue
                devices_dir = btrfs_uuid_dir / "devices"
                if devices_dir.is_dir():
                    for dev_link in devices_dir.iterdir():
                        part_dev = f"/dev/{dev_link.name}"
                        if Path(part_dev).exists() and device_is_on_disk(
                            part_dev, resolved_disk
                        ):
                            console.print(
                                f"[yellow]>> Deregistering stale Btrfs reference on {part_dev} (UUID: {btrfs_uuid_dir.name})[/yellow]"
                            )
                            run(
                                "btrfs",
                                "device",
                                "scan",
                                "--forget",
                                part_dev,
                                check=False,
                                capture=True,
                            )
        except Exception:
            pass

        # 6. Settle udev events
        run("udevadm", "settle", "--timeout=5", check=False, capture=True)

        # 7. VALIDATION PHASE: Ensure NO active VFS mounts AND NO kernel holders exist
        busy_found = False

        r = run("findmnt", "-rn", "-o", "SOURCE", check=False, capture=True)
        for line in r.stdout.splitlines():
            src = line.split("[", 1)[0].strip()
            if device_is_on_disk(src, resolved_disk):
                busy_found = True
                break

        block_paths = []
        if not busy_found:
            disk_path = (
                Path("/sys/class/block") / Path(resolved_disk).name
            ).resolve()
            block_paths = [disk_path]
            block_paths.extend(
                path
                for path in disk_path.iterdir()
                if (path / "partition").exists()
            )
            for block_path in block_paths:
                holders_path = block_path / "holders"
                if holders_path.is_dir() and any(holders_path.iterdir()):
                    busy_found = True
                    break

        # Also check for stale btrfs kernel claims via O_EXCL test
        if not busy_found:
            for block_path in block_paths:
                part_dev = f"/dev/{block_path.name}"
                if Path(part_dev).exists():
                    try:
                        fd = os.open(part_dev, os.O_RDONLY | os.O_EXCL)
                        os.close(fd)
                    except OSError:
                        console.print(
                            f"[yellow]>> {part_dev} still has kernel claim, forcing btrfs forget...[/yellow]"
                        )
                        run(
                            "btrfs",
                            "device",
                            "scan",
                            "--forget",
                            part_dev,
                            check=False,
                            capture=True,
                        )
                        time.sleep(1)
                        busy_found = True
                        break

        if not busy_found:
            console.print(
                f"[green]>> Teardown clean on attempt {attempt}. Block device and partitions are free.[/green]"
            )
            return

        time.sleep(1.5)

    raise RuntimeError(
        f"Storage on {resolved_disk} is still busy after {max_retries} teardown attempts"
    )


teardown_device = teardown_target_storage


def ensure_mapper_free(
    target_dev: str | None = None, keep_backing: str | None = None
) -> None:
    mp = Path(f"/dev/mapper/{TARGET_CRYPT_NAME}")
    if not mp.exists():
        return
    if keep_backing:
        try:
            r = run(
                "cryptsetup",
                "status",
                TARGET_CRYPT_NAME,
                check=False,
                capture=True,
            )
            for line in r.stdout.splitlines():
                if line.strip().lower().startswith("device:"):
                    backing = line.split(":", 1)[1].strip()
                    if backing and _same_dev(backing, keep_backing):
                        return
                    break
        except Exception:
            pass
    mnt = is_mounted(str(mp))
    if mnt:
        run("umount", "-R", "-f", "-l", mnt, check=False, capture=True)
    if (
        run(
            "cryptsetup", "close", TARGET_CRYPT_NAME, check=False, capture=True
        ).returncode
        != 0
    ):
        run(
            "dmsetup",
            "remove",
            "--force",
            "--retry",
            TARGET_CRYPT_NAME,
            check=False,
            capture=True,
        )
    run("udevadm", "settle", "--timeout=5", check=False, capture=True)


def get_ram_kb() -> int:
    phys_kb = 4 * 1024 * 1024
    try:
        phys_kb = (
            os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        ) // 1024
    except Exception:
        pass
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                avail_kb = int(line.split()[1])
                return min(phys_kb, avail_kb)
    except Exception:
        pass
    return phys_kb


def _parse_efi_size_mib(efi_size: str) -> int:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(M|MiB|G|GiB)", efi_size.strip(), re.I)
    if not m:
        raise ValueError(f"Invalid EFI size: {efi_size}")
    val = float(m.group(1))
    unit = m.group(2).lower()
    mib = int(round(val * 1024)) if unit in ("g", "gib") else int(round(val))
    if mib < 64:
        raise ValueError(f"EFI size {efi_size} ({mib} MiB) is below the 64 MiB FAT32 minimum.")
    return mib


def write_gpt_sfdisk(
    disk: str, boot_mode: str, encrypt: bool, efi_size: str = "1.3G"
) -> tuple[str, str | None]:
    # DPS v1.0/v2.0: keep architecture-specific Linux Root GUID even when encrypted with LUKS2.
    root_type = LINUX_ROOT_GUID
    r_bytes = run("blockdev", "--getsize64", disk, check=False, capture=True)
    disk_mib = (
        int(r_bytes.stdout.strip()) // (1024 * 1024)
        if r_bytes.returncode == 0 and r_bytes.stdout.strip().isdigit()
        else 0
    )
    sfdisk_input = "label: gpt\n\n"
    if boot_mode == "UEFI":
        efi_mib = _parse_efi_size_mib(efi_size)
        if disk_mib and (efi_mib + 1024 > disk_mib):
            raise ValueError(
                f"Disk {disk} ({disk_mib} MiB) is too small for {efi_mib} MiB EFI + root partition."
            )
        aligned_efi = f"{efi_mib}MiB"
        sfdisk_input += (
            f'start=1MiB, size={aligned_efi}, type={EFI_GUID}, name="{DUSKY_EFI_PARTNAME}"\n'
        )
        sfdisk_input += (
            f'size=+, type={root_type}, name="{DUSKY_ROOT_PARTNAME}"\n'
        )
    else:
        aligned_efi = "2MiB"
        sfdisk_input += (
            f'start=1MiB, size=2MiB, type={BIOS_GUID}, name="{DUSKY_BIOS_LABEL}"\n'
        )
        sfdisk_input += (
            f'size=+, type={root_type}, name="{DUSKY_ROOT_PARTNAME}"\n'
        )
    console.print(f"[cyan]Writing GPT to {disk} (wipe) EFI={aligned_efi}[/cyan]")

    # 0. Pre-wipe existing child partition superblocks/LUKS headers (archinstall wipe_dev pattern)
    for existing_part in get_partitions_list(disk):
        p_path = existing_part.get("path")
        if p_path and Path(p_path).exists():
            if is_luks_device(p_path):
                run("cryptsetup", "--batch-mode", "erase", p_path, check=False, capture=True)
            run("wipefs", "--all", "--force", p_path, check=False, capture=True)

    # 1. Annihilate existing GPT structures (primary and backup header/table) and MBR
    console.print(
        f"[cyan]Destroying all GPT/MBR structures and backup tables on {disk}...[/cyan]"
    )
    run("blkdiscard", "--force", disk, check=False, capture=True, timeout=120)
    run("sgdisk", "--zap-all", disk, check=False, capture=True)
    run("wipefs", "--all", "--force", disk, check=False, capture=True)
    try:
        run(
            "dd",
            "if=/dev/zero",
            f"of={disk}",
            "bs=512",
            "count=20480",
            "conv=fsync",
            check=False,
            capture=True,
        )
        r_sz = run("blockdev", "--getsz", disk, check=False, capture=True)
        if r_sz.returncode == 0 and r_sz.stdout.strip().isdigit():
            total_sectors = int(r_sz.stdout.strip())
            if total_sectors > 20480:
                run(
                    "dd",
                    "if=/dev/zero",
                    f"of={disk}",
                    "bs=512",
                    f"seek={total_sectors - 20480}",
                    "count=20480",
                    "conv=fsync",
                    check=False,
                    capture=True,
                )
    except Exception:
        pass
    run("blockdev", "--rereadpt", disk, check=False, capture=True)
    run("udevadm", "settle", "--timeout=5", check=False, capture=True)

    # 2. Write new GPT partition table
    run("udevadm", "control", "--stop-exec-queue", check=False, capture=True)
    try:
        run("wipefs", "--all", "--force", disk, check=False, capture=True)
        try:
            run(
                "sfdisk",
                "--force",
                "--wipe",
                "always",
                "--wipe-partitions",
                "always",
                "--label",
                "gpt",
                "--lock=yes",
                disk,
                input_text=sfdisk_input,
                capture=True,
            )
        except subprocess.CalledProcessError:
            console.print("[yellow]sfdisk --lock failed, retry without[/yellow]")
            run(
                "sfdisk",
                "--force",
                "--wipe",
                "always",
                "--wipe-partitions",
                "always",
                "--label",
                "gpt",
                disk,
                input_text=sfdisk_input,
                capture=True,
            )
        run("blockdev", "--rereadpt", disk, check=False, capture=True)
        run("partx", "-u", disk, check=False, capture=True)
    finally:
        run(
            "udevadm",
            "control",
            "--start-exec-queue",
            check=False,
            capture=True,
        )
    try:
        run(
            "udevadm",
            "trigger",
            f"--parent-match={disk}",
            "--settle",
            check=False,
            capture=True,
        )
        run("udevadm", "settle", "--timeout=10", check=False, capture=True)
    except Exception:
        pass
    try:
        run(
            "udevadm",
            "wait",
            "--timeout=10",
            "--initialized=yes",
            "--settle",
            f"{get_partition_path(disk, 1)}",
            f"{get_partition_path(disk, 2)}",
            check=False,
            capture=True,
        )
    except Exception:
        pass
    efi_part = get_partition_path(disk, 1) if boot_mode == "UEFI" else None
    root_part = get_partition_path(disk, 2)
    if not wait_for_dev(root_part, 10):
        raise RuntimeError(f"{root_part} missing")
    if efi_part and not wait_for_dev(efi_part, 10):
        raise RuntimeError(f"{efi_part} missing")
    return root_part, efi_part


def show_disks_table(disks: list[dict]) -> None:
    table = Table(
        title="Disks", box=box.ROUNDED, show_lines=False, expand=False
    )
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Device", style="bold")
    table.add_column("Size", justify="right")
    table.add_column("Model", style="dim")
    for i, d in enumerate(disks, 1):
        try:
            sz = f"{int(d.get('size', 0)) / 1024**3:.1f}G"
        except Exception:
            sz = str(d.get("size", ""))
        table.add_row(
            str(i),
            d.get("path") or d.get("name"),
            sz,
            Text((d.get("model") or "")[:30]),
        )
    console.print(Align.center(table))


def validate_part_input(raw: str) -> Path:
    if not raw or not VALID_PART_RE.match(raw):
        raise ValueError("Invalid characters in partition path.")
    name = raw.removeprefix("/dev/")
    p = Path("/dev") / name
    try:
        rp = p.resolve()
    except Exception:
        rp = p
    try:
        if not rp.is_relative_to(Path("/dev")):
            raise ValueError("Partition path must be under /dev.")
    except AttributeError:
        if "/dev" not in str(rp):
            raise ValueError("Partition path must be under /dev.")
    if not rp.exists():
        raise ValueError(f"{rp} does not exist.")
    if not rp.is_block_device():
        raise ValueError(f"{rp} is not a block special device.")
    if not (Path("/sys/class/block") / rp.name / "partition").exists():
        raise ValueError(f"{rp} is not a partition (whole disks or virtual nodes cannot be used directly).")
    if not any(
        device_is_on_disk(str(rp), d.get("path") or d.get("name"))
        for d in list_disks()
    ):
        raise ValueError(f"{rp} does not reside on an eligible installation disk.")
    return rp


def flatten_lsblk(data: dict | list) -> list[dict]:
    nodes: list[dict] = []

    def _walk(item_list: list[dict]) -> None:
        for item in item_list:
            nodes.append(item)
            if item.get("children"):
                _walk(item.get("children"))

    if isinstance(data, dict):
        _walk(data.get("blockdevices", []))
    elif isinstance(data, list):
        _walk(data)
    return nodes


def get_partitions_list(disk: str) -> list[dict]:
    try:
        r = run(
            "lsblk",
            "--json",
            "--paths",
            "--tree",
            "-o",
            "PATH,TYPE,SIZE,FSTYPE,PARTTYPE,PARTLABEL,LABEL",
            disk,
            check=False,
            capture=True,
        )
        data = json.loads(r.stdout)
        parts = []
        for dev in flatten_lsblk(data):
            if dev.get("type") == "part":
                parts.append(dev)
        return parts
    except Exception:
        return []


def _same_dev(a: str | Path | None, b: str | Path | None) -> bool:
    if not a or not b:
        return False
    try:
        return Path(a).resolve() == Path(b).resolve()
    except Exception:
        return str(a) == str(b)


def probe_esp_valid(part_path: str) -> bool:
    r = run(
        "blkid",
        "-p",
        "-c",
        "/dev/null",
        "-o",
        "value",
        "-s",
        "PART_ENTRY_TYPE",
        part_path,
        check=False,
        capture=True,
    )
    return (r.stdout or "").strip().lower() in (EFI_GUID, "0xef")


def probe_vfat_valid(part_path: str) -> bool:
    r = run(
        "blkid",
        "-p",
        "-c",
        "/dev/null",
        "-o",
        "value",
        "-s",
        "TYPE",
        part_path,
        check=False,
        capture=True,
    )
    return (r.stdout or "").strip().lower() == "vfat"


def is_luks_device(part_path: str) -> bool:
    return (
        run(
            "cryptsetup", "isLuks", part_path, check=False, capture=True
        ).returncode
        == 0
    )


def test_luks_passphrase(
    part_path: str, pass_bytes: bytes | bytearray
) -> bool:
    r = run(
        "cryptsetup",
        "open",
        "--test-passphrase",
        "--key-file",
        "-",
        part_path,
        input_text=pass_bytes,
        check=False,
        capture=True,
    )
    return r.returncode == 0


def rescan_disk_partitions(target_dev: str) -> None:
    run("blockdev", "--rereadpt", target_dev, check=False, capture=True)
    run("partx", "-u", target_dev, check=False, capture=True)
    run(
        "udevadm",
        "trigger",
        "--settle",
        "-w",
        "--timeout=10",
        target_dev,
        check=False,
        capture=True,
    )
    run("udevadm", "settle", "--timeout=10", check=False, capture=True)
    time.sleep(0.3)


def format_partitions_summary(
    disk: str, parts: list[dict], has_win: bool, win_esp: str | None
) -> str:
    lines = [f"Disk: {disk}"]
    if has_win and win_esp:
        lines.append(f"Notice: Windows ESP detected on {win_esp}")
    if not parts:
        lines.append("Partitions: (none found)")
        return "\n".join(lines)
    lines.append(
        f"{'Partition':<18} {'Size':>8}  {'Filesystem':<10} {'Label / Partlabel'}"
    )
    for p in parts:
        path = str(p.get("path") or "")
        size = str(p.get("size") or "")
        fstype = str(p.get("fstype") or "no-fs")
        label = str(p.get("label") or p.get("partlabel") or "")
        if has_win and win_esp and _same_dev(path, win_esp):
            label = f"{label} [Windows ESP]".strip()
        lines.append(f"{path:<18} {size:>8}  {fstype:<10} {label[:26]}")
    return "\n".join(lines)


def _atomic_write_0600(destination: Path, content: str) -> None:
    fd, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        temporary.chmod(0o600)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _is_non_rotational(dev_path: str) -> bool:
    pending = [Path(dev_path).resolve().name]
    seen: set[str] = set()
    found_any = False
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        block = Path("/sys/class/block") / name
        try:
            if (block / "partition").exists():
                pending.append(block.resolve().parent.name)
                continue
            slaves = block / "slaves"
            if slaves.is_dir() and any(slaves.iterdir()):
                pending.extend(s.name for s in slaves.iterdir())
                continue
            rot = block / "queue" / "rotational"
            if rot.is_file():
                found_any = True
                if rot.read_text().strip() != "0":
                    return False
        except OSError:
            return False
    return found_any


def _blkid_tag(dev: str | None, tag: str) -> str | None:
    if not dev or not Path(dev).exists():
        return None
    r = run(
        "blkid",
        "-p",
        "-c",
        "/dev/null",
        "-o",
        "value",
        "-s",
        tag,
        dev,
        check=False,
        capture=True,
    )
    val = (r.stdout or "").strip()
    return val or None


def write_provisioned_state(
    root_part: str,
    efi_part: str | None,
    encrypt: bool,
    disk: str,
    boot_mode: str,
    strategy: int,
) -> None:
    mapped_root = f"/dev/mapper/{TARGET_CRYPT_NAME}" if encrypt else root_part
    luks_uuid = _blkid_tag(root_part, "UUID") if encrypt else None
    root_fs_uuid = _blkid_tag(mapped_root, "UUID")
    root_partuuid = _blkid_tag(root_part, "PART_ENTRY_UUID")
    efi_uuid = _blkid_tag(efi_part, "UUID") if efi_part else None
    payload = {
        "root_part": root_part,
        "efi_part": efi_part or None,
        "encrypt": bool(encrypt),
        "disk": disk,
        "boot_mode": boot_mode,
        "strategy": int(strategy),
        "crypt_name": TARGET_CRYPT_NAME if encrypt else None,
        "mapped_root": mapped_root,
        "luks_uuid": luks_uuid,
        "root_fs_uuid": root_fs_uuid,
        "root_partuuid": root_partuuid,
        "efi_uuid": efi_uuid,
        "btrfs_csum": BTRFS_CSUM if int(strategy) != 4 else None,
    }
    env = (
        f'PROVISIONED_ROOT_PART="{root_part}"\n'
        + (f'PROVISIONED_EFI_PART="{efi_part}"\n' if efi_part else "")
        + f'ENCRYPT_ROOT="{1 if encrypt else 0}"\n'
        + f'PROVISIONED_DISK="{disk}"\n'
        + f'PROVISIONED_BOOT_MODE="{boot_mode}"\n'
        + f'PROVISIONED_STRATEGY="{int(strategy)}"\n'
        + (f'LUKS_UUID="{luks_uuid}"\n' if luks_uuid else "")
        + (f'ROOT_FS_UUID="{root_fs_uuid}"\n' if root_fs_uuid else "")
        + (f'EFI_UUID="{efi_uuid}"\n' if efi_uuid else "")
    )
    _atomic_write_0600(STATE_ENV, env)
    serialized = json.dumps(payload, indent=2) + "\n"
    _atomic_write_0600(STATE_JSON, serialized)
    try:
        STORAGE_STATE_JSON.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        STORAGE_STATE_JSON.parent.chmod(0o700)
        _atomic_write_0600(STORAGE_STATE_JSON, serialized)
    except Exception:
        pass


def format_root_and_efi(
    root_part: str,
    efi_part: str | None,
    format_efi: bool,
    do_encrypt: bool,
    boot_mode: str,
    has_win: bool,
    win_esp: str | None,
    creds: dict[str, str],
    luks_ba: bytearray | None = None,
) -> str:
    # Validate the shared ESP before modifying ROOT or its encryption header.
    if boot_mode == "UEFI":
        if not efi_part or _same_dev(root_part, efi_part):
            raise ValueError("UEFI requires a separate EFI partition")
        if not probe_esp_valid(efi_part):
            raise ValueError(
                "Select a partition marked as EFI System Partition"
            )
        if not format_efi and not probe_vfat_valid(efi_part):
            raise ValueError("The preserved EFI partition must contain FAT")
    if do_encrypt and not luks_ba:
        raise ValueError("Encryption requires a nonempty passphrase")
    if do_encrypt and Path(f"/dev/mapper/{TARGET_CRYPT_NAME}").exists():
        raise ValueError(
            "cryptroot must be closed before formatting encrypted ROOT"
        )
    if is_luks_device(root_part):
        console.print(
            f"[yellow]Existing LUKS on {root_part}, erasing header[/yellow]"
        )
        run(
            "cryptsetup",
            "--batch-mode",
            "erase",
            root_part,
            check=False,
            capture=True,
        )
    for attempt in range(1, 4):
        try:
            run(
                "wipefs",
                "--all",
                "--force",
                "--lock=yes",
                root_part,
                capture=True,
            )
            break
        except subprocess.CalledProcessError:
            if attempt == 3:
                run(
                    "wipefs",
                    "--all",
                    "--force",
                    root_part,
                    check=False,
                    capture=True,
                )
            time.sleep(1)

    run("udevadm", "settle", "--timeout=5", check=False, capture=True)
    btrfs_target = root_part

    if do_encrypt and luks_ba is not None:
        mem_kb = get_ram_kb()
        max_safe_kb = max(65536, int(mem_kb * 0.35))
        tiers: list[list[str]] = []
        if max_safe_kb >= 1_048_576:
            tiers.append(["--pbkdf-memory", "1048576"])
        if max_safe_kb >= 524_288:
            tiers.append(["--pbkdf-memory", "524288"])
        if max_safe_kb >= 262_144:
            tiers.append(["--pbkdf-memory", "262144", "--pbkdf-parallel", "1"])
        tiers.append(["--pbkdf-memory", "131072", "--pbkdf-parallel", "1"])
        tiers.append(["--pbkdf-memory", "65536", "--pbkdf-parallel", "1"])
        base_luks_fmt = [
            "cryptsetup",
            "--batch-mode",
            "--type",
            "luks2",
            "--cipher",
            "aes-xts-plain64",
            "--pbkdf",
            "argon2id",
            "--hash",
            "sha512",
            "--key-size",
            "512",
            "--sector-size",
            "4096",
            "--label",
            DUSKY_ROOT_LABEL,
        ]
        try:
            r = None
            for pbkdf_opts in tiers:
                fmt = (
                    base_luks_fmt
                    + pbkdf_opts
                    + ["luksFormat", "--key-file", "-", root_part]
                )
                r = run(*fmt, input_text=luks_ba, check=False, capture=True)
                if r.returncode == 0:
                    break
                if r.returncode not in (3, -9, 137):
                    break
            if r is None or r.returncode != 0:
                detail = ((r.stderr if r else b"") or b"").decode("utf-8", "replace")
                raise RuntimeError(
                    f"luksFormat failed (rc={r.returncode if r else -1}): {detail.strip()}"
                )
            crypt_open_args = [
                "cryptsetup",
                "open",
                "--type",
                "luks2",
                "--allow-discards",
            ]
            if _is_non_rotational(root_part):
                crypt_open_args += [
                    "--perf-no_read_workqueue",
                    "--perf-no_write_workqueue",
                ]
            crypt_open_args += [
                "--key-file",
                "-",
                root_part,
                TARGET_CRYPT_NAME,
            ]
            ro = run(
                *crypt_open_args, input_text=luks_ba, check=False, capture=True
            )
            if ro.returncode != 0:
                detail = (ro.stderr or b"").decode("utf-8", "replace")
                raise RuntimeError(f"cryptsetup open failed: {detail.strip()}")
        finally:
            if luks_ba:
                for i in range(len(luks_ba)):
                    luks_ba[i] = 0
        btrfs_target = f"/dev/mapper/{TARGET_CRYPT_NAME}"
        if not wait_for_dev(btrfs_target, 10):
            raise RuntimeError(f"{btrfs_target} did not appear after cryptsetup open")

    mkfs_cmd = ["mkfs.btrfs", "-f"]
    if BTRFS_CSUM != "crc32c":
        mkfs_cmd += ["--checksum", BTRFS_CSUM]
    mkfs_cmd += ["-L", DUSKY_ROOT_LABEL, btrfs_target]

    for mkfs_attempt in range(1, 6):
        try:
            run(*mkfs_cmd, capture=True)
            break
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or e.stdout or str(e)).strip()
            if "busy" in detail.lower() and mkfs_attempt < 5:
                console.print(
                    f"[yellow]Failed mkfs.btrfs {btrfs_target} (attempt {mkfs_attempt}/5, device busy). Retrying...[/yellow]"
                )
                run("udevadm", "settle", "--timeout=5", check=False, capture=True)
                time.sleep(2)
            else:
                console.print(
                    f"[red]Failed mkfs.btrfs -f -L {DUSKY_ROOT_LABEL} {btrfs_target}[/red]"
                )
                console.print(Text(f"Details: {detail}", style="red"))
                raise

    if boot_mode == "UEFI" and efi_part and format_efi:
        efi_mnt = is_mounted(efi_part)
        if efi_mnt:
            run("umount", "-R", "-f", "-l", efi_mnt, check=False, capture=True)
        for attempt in range(1, 4):
            try:
                run("wipefs", "--all", "--force", "--lock=yes", efi_part, capture=True)
                run("mkfs.fat", "-F", "32", "-n", DUSKY_EFI_LABEL, efi_part, capture=True)
                break
            except subprocess.CalledProcessError:
                if attempt == 3:
                    run("wipefs", "--all", "--force", efi_part, check=False, capture=True)
                    run("mkfs.fat", "-F", "32", "-n", DUSKY_EFI_LABEL, efi_part, capture=True)
                else:
                    run("udevadm", "settle", "--timeout=5", check=False, capture=True)
                    time.sleep(1)
    run("udevadm", "settle", "--timeout=5", check=False, capture=True)
    return btrfs_target


def parse_strategy_cli(args: argparse.Namespace) -> int | None:
    if args.rescue:
        return 4
    if args.strategy:
        s = args.strategy.lower()
        if s in ("1", "wipe", "entire", "wipe_entire", "wipeentire", "erase"):
            return 1
        if s in (
            "2",
            "existing",
            "select",
            "dual",
            "select_existing",
            "dualboot",
        ):
            return 2
        if s in ("3", "manual", "cfdisk", "advanced"):
            return 3
        if s in ("4", "rescue", "chroot", "mount", "mountonly", "mount_only"):
            return 4
    if args.auto:
        return 1
    return None


def run_tui_app(cmd_list: list[str]) -> int:
    try:
        console.show_cursor(True)
    except Exception:
        pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    try:
        sys.stdout.write(
            "\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?2004l\x1b[?25h"
        )
        sys.stdout.flush()
    except Exception:
        pass

    old_int = signal.getsignal(signal.SIGINT)
    old_term = signal.getsignal(signal.SIGTERM)
    old_tstp = None
    try:
        if hasattr(signal, "SIGTSTP"):
            old_tstp = signal.getsignal(signal.SIGTSTP)
    except Exception:
        pass

    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if old_tstp is not None:
            signal.signal(signal.SIGTSTP, signal.SIG_IGN)
    except Exception:
        pass

    term = os.environ.get("TERM", "")
    if term in ("", "dumb", "unknown"):
        os.environ["TERM"] = "linux"

    def _preexec_restore() -> None:
        try:
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            if hasattr(signal, "SIGTSTP"):
                signal.signal(signal.SIGTSTP, signal.SIG_DFL)
        except Exception:
            pass

    rc = 127
    try:
        try:
            with open("/dev/tty", "r") as tty_in, open(
                "/dev/tty", "w"
            ) as tty_out:
                proc = subprocess.run(
                    cmd_list,
                    stdin=tty_in,
                    stdout=tty_out,
                    stderr=tty_out,
                    check=False,
                    preexec_fn=_preexec_restore,
                )
                rc = proc.returncode
        except Exception:
            proc = subprocess.run(
                cmd_list,
                stdin=sys.stdin,
                stdout=sys.stdout,
                stderr=sys.stderr,
                check=False,
                preexec_fn=_preexec_restore,
            )
            rc = proc.returncode
    except FileNotFoundError:
        raise
    finally:
        try:
            signal.signal(signal.SIGINT, old_int)
            signal.signal(signal.SIGTERM, old_term)
            if old_tstp is not None:
                signal.signal(signal.SIGTSTP, old_tstp)
        except Exception:
            pass
        restore_terminal()

    return rc


def launch_partition_editor(target_dev: str) -> int:
    cmds = [
        ["cfdisk", "--color=always", "--lock=yes", target_dev],
        ["cfdisk", "--color=always", "--lock=no", target_dev],
        ["cfdisk", target_dev],
    ]
    last_rc = 127
    for cmd in cmds:
        try:
            t0 = time.monotonic()
            rc = run_tui_app(cmd)
            last_rc = rc
            if rc == 0 or (time.monotonic() - t0) > 1.0:
                return rc
        except FileNotFoundError:
            continue
    if last_rc != 127:
        return last_rc
    try:
        console.print("[yellow]cfdisk not found, falling back to fdisk[/yellow]")
        return run_tui_app(["fdisk", target_dev])
    except FileNotFoundError:
        console.print("[red]Neither cfdisk nor fdisk found![/red]")
        return 127


def resolve_luks_ba(
    do_encrypt: bool,
    creds: dict[str, str],
    plan_luks_ba: bytearray | None,
    confirm: bool = True,
) -> bytearray | None:
    if not do_encrypt:
        return None
    if plan_luks_ba:
        return plan_luks_ba
    if creds.get("ROOT_PASS"):
        return bytearray(creds["ROOT_PASS"].encode("utf-8"))
    return prompt_luks_password(confirm=confirm)


def execute_strategy_wipe(
    plan: PartitionPlan, creds: dict[str, str]
) -> bool:
    panel = Panel.fit(
        f"[bold red]Strategy 1: Wipe Entire {plan.disk} EFI={plan.efi_size}[/bold red]",
        box=box.ROUNDED,
        border_style="red",
    )
    console.print(Align.center(panel))

    luks_ba = resolve_luks_ba(plan.encrypt, creds, plan.luks_ba, confirm=True)
    if plan.encrypt and not luks_ba:
        console.print("[yellow]Aborted LUKS setup.[/yellow]")
        return False

    try:
        teardown_device(plan.disk)
        ensure_mapper_free(plan.disk)
        root_part, efi_part = write_gpt_sfdisk(
            plan.disk, plan.boot_mode, plan.encrypt, plan.efi_size
        )

        for part in (p for p in (root_part, efi_part) if p):
            for _settle in range(5):
                run("udevadm", "settle", "--timeout=5", check=False, capture=True)
                try:
                    fd = os.open(part, os.O_RDONLY | os.O_EXCL)
                    os.close(fd)
                    break
                except OSError:
                    console.print(
                        f"[yellow]Partition {part} still busy, waiting... ({_settle + 1}/5)[/yellow]"
                    )
                    time.sleep(2)
            else:
                raise RuntimeError(
                    f"Partition {part} is still held exclusively busy after 5 settle attempts."
                )

        format_root_and_efi(
            root_part,
            efi_part,
            True,
            plan.encrypt,
            plan.boot_mode,
            False,
            None,
            creds,
            luks_ba,
        )
    finally:
        if luks_ba:
            for i in range(len(luks_ba)):
                luks_ba[i] = 0

    write_provisioned_state(
        root_part=root_part,
        efi_part=efi_part,
        encrypt=plan.encrypt,
        disk=plan.disk,
        boot_mode=plan.boot_mode,
        strategy=1,
    )
    console.print(
        Panel.fit(
            f"[green]Wipe Complete Root={root_part} EFI={efi_part or 'N/A'}[/green]",
            box=box.ROUNDED,
            border_style="green",
        )
    )
    return True


def execute_strategy_existing_or_manual(
    plan: PartitionPlan, creds: dict[str, str]
) -> bool:
    title = (
        f"Strategy 2: Select Existing on {plan.disk} (Dual Boot)"
        if plan.strategy == 2
        else f"Strategy 3: Manual Partitioning on {plan.disk}"
    )
    color = "cyan" if plan.strategy == 2 else "magenta"
    console.print(
        Align.center(
            Panel.fit(
                f"[bold {color}]{title}[/bold {color}]",
                box=box.ROUNDED,
                border_style=color,
            )
        )
    )

    luks_ba = resolve_luks_ba(plan.encrypt, creds, plan.luks_ba, confirm=True)
    if plan.encrypt and not luks_ba:
        console.print("[yellow]Aborted LUKS setup.[/yellow]")
        return False

    efi_target = plan.efi_part if plan.boot_mode == "UEFI" else None
    try:
        teardown_device(plan.disk)
        ensure_mapper_free(plan.disk)
        format_root_and_efi(
            plan.root_part,
            efi_target,
            plan.format_efi,
            plan.encrypt,
            plan.boot_mode,
            plan.has_win,
            plan.win_esp,
            creds,
            luks_ba,
        )
    finally:
        if luks_ba:
            for i in range(len(luks_ba)):
                luks_ba[i] = 0

    write_provisioned_state(
        root_part=plan.root_part,
        efi_part=efi_target,
        encrypt=plan.encrypt,
        disk=plan.disk,
        boot_mode=plan.boot_mode,
        strategy=plan.strategy,
    )
    label = "Select Existing" if plan.strategy == 2 else "Manual"
    console.print(
        Panel.fit(
            f"[green]{label} Complete Root={plan.root_part} EFI={efi_target or 'N/A'}[/green]",
            box=box.ROUNDED,
            border_style="green",
        )
    )
    return True


def execute_strategy_rescue(
    plan: PartitionPlan, creds: dict[str, str]
) -> bool:
    panel = Panel.fit(
        f"[bold green]Strategy 4: Rescue / Chroot (Mount Only) on {plan.disk}[/bold green]",
        box=box.ROUNDED,
        border_style="green",
    )
    console.print(Align.center(panel))

    root_part = plan.root_part
    efi_part = plan.efi_part if plan.boot_mode == "UEFI" else ""
    is_luks = is_luks_device(root_part)
    encrypt_flag = False

    try:
        for mp in findmnt_targets("/mnt"):
            if run("umount", "-R", mp, check=False, capture=True).returncode != 0:
                run("umount", "-R", "-f", "-l", mp, check=False, capture=True)
        if is_luks:
            console.print(
                f"[yellow]{root_part} is LUKS, unlocking without formatting...[/yellow]"
            )
            ensure_mapper_free(plan.disk, keep_backing=root_part)
            if Path(f"/dev/mapper/{TARGET_CRYPT_NAME}").exists():
                console.print(
                    f"[yellow]Mapper {TARGET_CRYPT_NAME} already unlocked for {root_part}, reusing it[/yellow]"
                )
            else:
                open_cmd = ["cryptsetup", "open", "--allow-discards"]
                if _is_non_rotational(root_part):
                    open_cmd += [
                        "--perf-no_read_workqueue",
                        "--perf-no_write_workqueue",
                    ]
                open_cmd += ["--key-file", "-", root_part, TARGET_CRYPT_NAME]

                pw = plan.luks_ba
                unlocked = False
                if pw is not None:
                    r = run(*open_cmd, input_text=pw, check=False, capture=True)
                    unlocked = (
                        r.returncode == 0
                        and Path(f"/dev/mapper/{TARGET_CRYPT_NAME}").exists()
                    )
                if not unlocked and creds.get("ROOT_PASS"):
                    cred_pw = bytearray(creds["ROOT_PASS"].encode("utf-8"))
                    try:
                        r = run(
                            *open_cmd,
                            input_text=cred_pw,
                            check=False,
                            capture=True,
                        )
                        unlocked = (
                            r.returncode == 0
                            and Path(f"/dev/mapper/{TARGET_CRYPT_NAME}").exists()
                        )
                    finally:
                        for i in range(len(cred_pw)):
                            cred_pw[i] = 0
                for attempt in range(1, 4):
                    if unlocked:
                        break
                    console.print(
                        f"[yellow]Prompting for existing LUKS passphrase (attempt {attempt}/3)...[/yellow]"
                    )
                    manual_pw = prompt_luks_password(confirm=False)
                    if manual_pw is None:
                        console.print("[yellow]Aborted unlocking.[/yellow]")
                        return False
                    try:
                        r = run(
                            *open_cmd,
                            input_text=manual_pw,
                            check=False,
                            capture=True,
                        )
                        unlocked = (
                            r.returncode == 0
                            and Path(f"/dev/mapper/{TARGET_CRYPT_NAME}").exists()
                        )
                        if not unlocked:
                            console.print("[red]Incorrect passphrase.[/red]")
                    finally:
                        for i in range(len(manual_pw)):
                            manual_pw[i] = 0
                if not unlocked:
                    console.print("[red]Failed to unlock LUKS partition.[/red]")
                    return False
            encrypt_flag = True
        else:
            ensure_mapper_free(plan.disk)
            console.print(f"[cyan]{root_part} is not LUKS, using directly[/cyan]")
            r = run("lsblk", "-ndlo", "FSTYPE", root_part, check=False, capture=True)
            fstype = r.stdout.strip().lower()
            if fstype != "btrfs":
                console.print(
                    f"[yellow]Warning: {root_part} fstype is {fstype}, expected btrfs. Continuing anyway for rescue.[/yellow]"
                )
    finally:
        if plan.luks_ba:
            for i in range(len(plan.luks_ba)):
                plan.luks_ba[i] = 0

    write_provisioned_state(
        root_part=root_part,
        efi_part=efi_part or None,
        encrypt=encrypt_flag,
        disk=plan.disk,
        boot_mode=plan.boot_mode,
        strategy=4,
    )
    console.print(
        Panel.fit(
            f"[green]Rescue state written. No formatting done.\nRoot={root_part} EFI={efi_part or 'N/A'} Encrypt={int(encrypt_flag)}\nRun 040 to mount.[/green]",
            box=box.ROUNDED,
            border_style="green",
        )
    )
    return True


def execute_partition_plan(
    plan: PartitionPlan, creds: dict[str, str]
) -> bool:
    if plan.strategy == 1:
        return execute_strategy_wipe(plan, creds)
    if plan.strategy in (2, 3):
        return execute_strategy_existing_or_manual(plan, creds)
    if plan.strategy == 4:
        return execute_strategy_rescue(plan, creds)
    raise ValueError(f"Invalid strategy: {plan.strategy}")


def valid_efi_size(s: str) -> str:
    s_clean = s.strip()
    try:
        _parse_efi_size_mib(s_clean)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return s_clean.replace(" ", "")


def create_partition_wizard(
    disks: list[dict],
    boot_mode: Literal["UEFI", "BIOS"],
    creds: dict[str, str],
    preset_disk: str | None = None,
    preset_strategy: int | None = None,
    preset_encrypt: bool | None = None,
    default_efi_size: str = "1.3G",
    allow_bios: bool = False,
):
    # Unattended use needs only Python's standard library + Rich.
    from textual import events
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.widgets import (
        Button,
        Input,
        OptionList,
        RadioButton,
        RadioSet,
        Static,
    )
    from textual.widgets.option_list import Option

    STEP_BIOS = "bios"
    STEP_DISK = "disk"
    STEP_STRATEGY = "strategy"
    STEP_EFI_SIZE = "efi_size"
    STEP_CFDISK = "cfdisk"
    STEP_ROOT = "root"
    STEP_EFI = "efi"
    STEP_FORMAT_EFI = "format_efi"
    STEP_ENCRYPT = "encrypt"
    STEP_LUKS_PASS = "luks_pass"
    STEP_LUKS_CONFIRM = "luks_confirm"
    STEP_REVIEW = "review"

    class PartitionWizard(App[PartitionPlan | None]):
        ENABLE_COMMAND_PALETTE = False
        BINDINGS = [
            Binding("ctrl+c", "cancel", "Cancel", priority=True),
            Binding("escape", "back", "Back", priority=True),
        ]
        CSS = """
        Screen { background: $background; color: $foreground; }
        #masthead { height: auto; padding: 1 2; color: $accent; text-style: bold; }
        #viewport { height: 1fr; }
        #center { height: auto; align-horizontal: center; padding: 0 2; }
        #card { width: 74; max-width: 100%; height: auto; margin-top: 1;
                padding: 1 2; border: double $accent; }
        #heading { height: auto; text-style: bold; margin-bottom: 1; }
        #progress { height: auto; color: $accent; margin-bottom: 1; }
        #context { height: auto; margin-bottom: 1; color: $foreground; text-opacity: 85%; }
        #label { height: auto; text-style: bold; }
        OptionList { height: auto; max-height: 10; border: solid $foreground; padding: 0; background: $background; }
        OptionList:focus { border: solid $accent; }
        Input { height: 3; border: solid $foreground; }
        Input:focus { border: solid $accent; }
        #hint { height: auto; color: $foreground; text-opacity: 70%; margin-top: 1; }
        #error { height: auto; min-height: 1; color: $error; }
        #actions { height: 3; align-horizontal: center; }
        Button { width: auto; min-width: 20; border: solid $foreground; }
        Button:focus { border: solid $accent; }
        #continue { margin-left: 2; }
        #defaults { height: auto; margin-top: 1; color: $foreground; text-opacity: 70%; }
        #review { height: auto; margin-bottom: 1; }
        RadioSet { height: auto; border: none; padding: 0; }
        #controls { height: auto; padding: 0 1; background: $panel; }
        .small #card { margin-top: 0; padding: 0 1; }
        """

        def __init__(self) -> None:
            super().__init__(ansi_color=True)
            self.theme = "ansi-dark"
            self.disks = disks
            self.boot_mode: Literal["UEFI", "BIOS"] = boot_mode
            self.creds = creds
            self.preset_disk = preset_disk
            self.preset_strategy = preset_strategy
            self.preset_encrypt = preset_encrypt
            self.allow_bios = allow_bios

            self.disk: str = preset_disk or ""
            self.strategy: int = preset_strategy or 1
            self.efi_size: str = default_efi_size
            self.root_part: str = ""
            self.efi_part: str = ""
            self.format_efi: bool = self.strategy == 1
            self.encrypt: bool = bool(preset_encrypt)
            self.luks_pass: str = ""
            self.luks_confirm: str = ""

            self.has_win: bool = False
            self.win_esp: str | None = None
            self.partitions: list[dict] = []
            self.cfdisk_ran: bool = False
            self.manual_input_mode: bool = False
            self.root_is_luks: bool = False
            self.needs_rescue_pass: bool = False
            self.step_index: int = 0

            if self.disk:
                self._refresh_disk_metadata(self.disk)

        def _refresh_disk_metadata(self, disk_path: str) -> None:
            self.has_win, self.win_esp = detect_windows_esp(disk_path)
            self.partitions = get_partitions_list(disk_path)

        def compute_steps(self) -> list[str]:
            steps: list[str] = []
            if self.boot_mode == "BIOS" and not self.allow_bios:
                steps.append(STEP_BIOS)
            if not self.preset_disk:
                steps.append(STEP_DISK)
            if not self.preset_strategy:
                steps.append(STEP_STRATEGY)

            if self.strategy == 1:
                if self.boot_mode == "UEFI":
                    steps.append(STEP_EFI_SIZE)
            elif self.strategy == 2:
                steps.append(STEP_ROOT)
                if self.boot_mode == "UEFI":
                    steps.append(STEP_EFI)
                    steps.append(STEP_FORMAT_EFI)
            elif self.strategy == 3:
                steps.append(STEP_CFDISK)
                steps.append(STEP_ROOT)
                if self.boot_mode == "UEFI":
                    steps.append(STEP_EFI)
                    steps.append(STEP_FORMAT_EFI)
            elif self.strategy == 4:
                steps.append(STEP_ROOT)
                if self.boot_mode == "UEFI":
                    steps.append(STEP_EFI)

            if self.strategy in (1, 2, 3):
                if self.preset_encrypt is None:
                    steps.append(STEP_ENCRYPT)
                if self.encrypt and not self.creds.get("ROOT_PASS"):
                    steps.append(STEP_LUKS_PASS)
                    steps.append(STEP_LUKS_CONFIRM)
            elif self.strategy == 4:
                if self.root_is_luks and self.needs_rescue_pass:
                    steps.append(STEP_LUKS_PASS)

            steps.append(STEP_REVIEW)
            return steps

        @property
        def current_step_id(self) -> str:
            steps = self.compute_steps()
            self.step_index = max(0, min(self.step_index, len(steps) - 1))
            return steps[self.step_index]

        def compose(self) -> ComposeResult:
            yield Static(
                f"DUSKY / PARTITIONING ({self.boot_mode})", id="masthead"
            )
            with VerticalScroll(id="viewport"):
                with Horizontal(id="center"):
                    with Vertical(id="card"):
                        yield Static("Configure storage", id="heading")
                        yield Static("", id="progress")
                        yield Static("", id="context", markup=False)
                        yield Static("", id="label")
                        yield OptionList(id="options")
                        yield Input(id="answer", select_on_focus=False)
                        yield RadioSet(
                            RadioButton("No / plain Btrfs", value=True),
                            RadioButton("Yes / LUKS2 encryption"),
                            id="encryption",
                        )
                        yield Static("", id="review", markup=False)
                        yield Static("", id="hint", markup=False)
                        yield Static("", id="error", markup=False)
                        with Horizontal(id="actions"):
                            yield Button("Back", id="back")
                            yield Button("Continue", id="continue")
                        yield Static("", id="defaults", markup=False)
            yield Static(
                "Enter: continue   Up/Down: choose   Tab: move   Esc: back   Ctrl+C: cancel",
                id="controls",
            )

        def on_mount(self) -> None:
            self.set_class(self.size.height < 25, "small")
            self.render_step()

        def on_resize(self, event: events.Resize) -> None:
            self.set_class(event.size.height < 25, "small")

        def _update_defaults_bar(self) -> None:
            parts = [f"Boot: {self.boot_mode}"]
            if self.disk:
                parts.append(f"Disk: {self.disk}")
            if self.strategy in (1, 2, 3):
                enc_str = "on / LUKS2" if self.encrypt else "off / plain Btrfs"
                parts.append(f"Encryption: {enc_str}")
            elif self.strategy == 4:
                parts.append("Mode: Rescue (Mount Only)")
            self.query_one("#defaults", Static).update("   |   ".join(parts))

        def render_step(self) -> None:
            steps = self.compute_steps()
            self.step_index = max(0, min(self.step_index, len(steps) - 1))
            step_id = steps[self.step_index]
            total_NonReview = max(1, len(steps) - 1)

            options_w = self.query_one("#options", OptionList)
            answer_w = self.query_one("#answer", Input)
            enc_w = self.query_one("#encryption", RadioSet)
            review_w = self.query_one("#review", Static)
            context_w = self.query_one("#context", Static)
            label_w = self.query_one("#label", Static)
            hint_w = self.query_one("#hint", Static)
            error_w = self.query_one("#error", Static)
            progress_w = self.query_one("#progress", Static)
            back_btn = self.query_one("#back", Button)
            cont_btn = self.query_one("#continue", Button)

            error_w.update("")
            context_w.display = False
            options_w.display = False
            answer_w.display = False
            enc_w.display = False
            review_w.display = False
            label_w.display = step_id != STEP_REVIEW

            can_go_back = self.step_index > 0 or (
                self.manual_input_mode and len(self.partitions) > 0
            )
            back_btn.display = can_go_back
            cont_btn.styles.margin = (0, 0, 0, 2 if can_go_back else 0)
            cont_btn.label = "Confirm" if step_id == STEP_REVIEW else "Continue"
            self._update_defaults_bar()

            step_num = self.step_index + 1

            if step_id == STEP_BIOS:
                progress_w.update(
                    f"Step {step_num} of {total_NonReview} / Firmware Mode"
                )
                label_w.update("Legacy BIOS mode detected. Continue?")
                options_w.clear_options()
                options_w.add_options(
                    [
                        Option(
                            "Continue installation in Legacy BIOS mode",
                            id="continue",
                        ),
                        Option("Cancel and exit", id="cancel"),
                    ]
                )
                options_w.highlighted = 0
                options_w.display = True
                hint_w.update(
                    "UEFI mode is recommended. Select Continue to proceed with BIOS GPT (2M BIOS boot partition)."
                )
                options_w.focus()

            elif step_id == STEP_DISK:
                progress_w.update(
                    f"Step {step_num} of {total_NonReview} / Target Disk"
                )
                label_w.update("Select installation disk")
                options_w.clear_options()
                default_idx = 0
                for idx, d in enumerate(self.disks):
                    path = str(d.get("path") or d.get("name") or "")
                    try:
                        sz = f"{int(d.get('size', 0)) / 1024**3:.1f} GiB"
                    except Exception:
                        sz = str(d.get("size", ""))
                    model = str(d.get("model") or "").strip()[:32]
                    text = f"{path:<16} {sz:>10}   {model}".rstrip()
                    options_w.add_options([Option(text, id=path)])
                    if self.disk and _same_dev(path, self.disk):
                        default_idx = idx
                options_w.highlighted = default_idx
                options_w.display = True
                hint_w.update(
                    "Up/Down: choose disk   Enter: select and continue"
                )
                options_w.focus()

            elif step_id == STEP_STRATEGY:
                progress_w.update(
                    f"Step {step_num} of {total_NonReview} / Partitioning Strategy"
                )
                if self.has_win and self.win_esp:
                    context_w.update(
                        f"Dual-Boot Notice: Windows ESP detected at {self.win_esp} on {self.disk}.\n"
                        "Choose Strategy 2 or 3 to preserve Windows."
                    )
                    context_w.display = True
                label_w.update(f"Choose partitioning strategy for {self.disk}")
                options_w.clear_options()
                strat_items = [
                    Option(
                        "1) Wipe Entire Drive — Erase disk and create fresh GPT layout",
                        id="1",
                    ),
                    Option(
                        "2) Select Existing (Dual Boot) — Use existing partitions on disk",
                        id="2",
                    ),
                    Option(
                        "3) Manual Partitioning (cfdisk) — Edit partition table interactively",
                        id="3",
                    ),
                    Option(
                        "4) Rescue / Chroot (Mount Only) — Mount existing install without formatting",
                        id="4",
                    ),
                ]
                options_w.add_options(strat_items)
                options_w.highlighted = max(0, min(self.strategy - 1, 3))
                options_w.display = True
                hint_w.update(
                    "Up/Down: choose strategy   Enter: select and continue"
                )
                options_w.focus()

            elif step_id == STEP_EFI_SIZE:
                progress_w.update(
                    f"Step {step_num} of {total_NonReview} / EFI Partition Size"
                )
                label_w.update("EFI System Partition size")
                answer_w.password = False
                answer_w.placeholder = "e.g. 1.3G, 2G, 1024M"
                answer_w.value = self.efi_size
                answer_w.display = True
                hint_w.update(
                    "Default is 1.3G. Use M/MiB or G/GiB suffix. Remainder of disk is allocated to ROOT."
                )
                answer_w.focus()
                answer_w.cursor_position = len(answer_w.value)

            elif step_id == STEP_CFDISK:
                progress_w.update(
                    f"Step {step_num} of {total_NonReview} / Manual Partition Editor"
                )
                context_w.update(
                    format_partitions_summary(
                        self.disk, self.partitions, self.has_win, self.win_esp
                    )
                )
                context_w.display = True
                label_w.update(f"Partition editor for {self.disk}")
                options_w.clear_options()
                cf_opts = [
                    Option(
                        f"Launch cfdisk on {self.disk} (unmounts active partitions first)",
                        id="launch",
                    )
                ]
                if self.partitions:
                    cf_opts.append(
                        Option(
                            f"Continue to partition selection ({len(self.partitions)} partition(s) detected)",
                            id="continue",
                        )
                    )
                options_w.add_options(cf_opts)
                options_w.highlighted = (
                    1 if (self.cfdisk_ran and len(cf_opts) > 1) else 0
                )
                options_w.display = True
                hint_w.update(
                    "In cfdisk, create your ROOT (and EFI) partitions, select [Write], then [Quit]."
                )
                options_w.focus()

            elif step_id == STEP_ROOT:
                progress_w.update(
                    f"Step {step_num} of {total_NonReview} / ROOT Partition"
                )
                context_w.update(
                    format_partitions_summary(
                        self.disk, self.partitions, self.has_win, self.win_esp
                    )
                )
                context_w.display = True
                action_note = (
                    "(mount only, will NOT be formatted)"
                    if self.strategy == 4
                    else "(WILL BE FORMATTED as DUSKY Btrfs)"
                )
                label_w.update(f"Select ROOT partition {action_note}")

                if not self.partitions or self.manual_input_mode:
                    answer_w.password = False
                    answer_w.placeholder = "e.g. /dev/nvme0n1p2 or vda2"
                    answer_w.value = self.root_part
                    answer_w.display = True
                    hint_w.update(
                        "Enter full /dev/... path or device name under /dev."
                    )
                    answer_w.focus()
                    answer_w.cursor_position = len(answer_w.value)
                else:
                    options_w.clear_options()
                    default_idx = len(self.partitions) - 1
                    for idx, p in enumerate(self.partitions):
                        p_path = str(p.get("path") or "")
                        p_sz = str(p.get("size") or "")
                        p_fs = str(p.get("fstype") or "no-fs")
                        p_lbl = str(
                            p.get("label") or p.get("partlabel") or ""
                        ).strip()
                        text = f"{p_path:<18} {p_sz:>8}   {p_fs:<10} {p_lbl}".rstrip()
                        options_w.add_options([Option(text, id=p_path)])
                        if self.root_part and _same_dev(p_path, self.root_part):
                            default_idx = idx
                    options_w.add_options(
                        [
                            Option(
                                "Specify ROOT partition path manually...",
                                id="__manual__",
                            )
                        ]
                    )
                    options_w.highlighted = max(0, default_idx)
                    options_w.display = True
                    hint_w.update(
                        "Up/Down: choose ROOT partition   Enter: select"
                    )
                    options_w.focus()

            elif step_id == STEP_EFI:
                progress_w.update(
                    f"Step {step_num} of {total_NonReview} / EFI System Partition"
                )
                context_w.update(
                    format_partitions_summary(
                        self.disk, self.partitions, self.has_win, self.win_esp
                    )
                )
                context_w.display = True
                label_w.update("Select EFI System Partition (ESP)")

                if not self.partitions or self.manual_input_mode:
                    answer_w.password = False
                    answer_w.placeholder = "e.g. /dev/nvme0n1p1"
                    answer_w.value = (
                        self.efi_part
                        if not _same_dev(self.efi_part, self.root_part)
                        else ""
                    )
                    answer_w.display = True
                    hint_w.update(
                        "Enter EFI partition path (must be distinct from ROOT)."
                    )
                    answer_w.focus()
                    answer_w.cursor_position = len(answer_w.value)
                else:
                    options_w.clear_options()
                    best_idx: int | None = None
                    first_valid_idx: int | None = None
                    for idx, p in enumerate(self.partitions):
                        p_path = str(p.get("path") or "")
                        p_sz = str(p.get("size") or "")
                        p_fs = str(p.get("fstype") or "no-fs")
                        p_lbl = str(
                            p.get("label") or p.get("partlabel") or ""
                        ).strip()
                        same_as_root = _same_dev(p_path, self.root_part)
                        suffix = "  [SELECTED AS ROOT]" if same_as_root else ""
                        if (
                            not same_as_root
                            and self.has_win
                            and self.win_esp
                            and _same_dev(p_path, self.win_esp)
                        ):
                            suffix = "  [Windows ESP]"
                        text = f"{p_path:<18} {p_sz:>8}   {p_fs:<10} {p_lbl}{suffix}".rstrip()
                        options_w.add_options(
                            [Option(text, id=p_path, disabled=same_as_root)]
                        )
                        if not same_as_root:
                            if first_valid_idx is None:
                                first_valid_idx = idx
                            if self.efi_part and _same_dev(
                                p_path, self.efi_part
                            ):
                                best_idx = idx
                            elif best_idx is None and (
                                p_fs.lower() in ("vfat", "fat32")
                                or (
                                    self.has_win
                                    and self.win_esp
                                    and _same_dev(p_path, self.win_esp)
                                )
                            ):
                                best_idx = idx
                    options_w.add_options(
                        [
                            Option(
                                "Specify EFI partition path manually (e.g. another disk)...",
                                id="__manual__",
                            )
                        ]
                    )
                    manual_idx = len(self.partitions)
                    if self.strategy == 4:
                        options_w.add_options(
                            [
                                Option(
                                    "Skip / No EFI partition",
                                    id="__skip__",
                                )
                            ]
                        )
                    target_highlight = (
                        best_idx
                        if best_idx is not None
                        else (
                            first_valid_idx
                            if first_valid_idx is not None
                            else manual_idx
                        )
                    )
                    options_w.highlighted = target_highlight
                    options_w.display = True
                    hint_w.update(
                        "ROOT partition is disabled to prevent accidental overwrite."
                    )
                    options_w.focus()

            elif step_id == STEP_FORMAT_EFI:
                progress_w.update(
                    f"Step {step_num} of {total_NonReview} / EFI Formatting"
                )
                is_win_esp = bool(
                    self.has_win
                    and self.win_esp
                    and _same_dev(self.efi_part, self.win_esp)
                )
                if is_win_esp:
                    context_w.update(
                        f"Warning: {self.efi_part} contains a Windows Boot Manager (EFI/Microsoft).\n"
                        "Preserving it is strongly recommended to keep Windows bootable."
                    )
                    context_w.display = True
                label_w.update(f"How should EFI partition {self.efi_part} be handled?")
                options_w.clear_options()
                options_w.add_options(
                    [
                        Option(
                            "Keep existing FAT32 filesystem (Recommended for Dual Boot)",
                            id="preserve",
                        ),
                        Option(
                            f"Format {self.efi_part} as fresh FAT32 ({DUSKY_EFI_LABEL})",
                            id="format",
                        ),
                    ]
                )
                options_w.highlighted = 1 if (self.format_efi and not is_win_esp) else 0
                options_w.display = True
                hint_w.update(
                    "Keeping existing EFI preserves other OS bootloaders on the ESP."
                )
                options_w.focus()

            elif step_id == STEP_ENCRYPT:
                progress_w.update(
                    f"Step {step_num} of {total_NonReview} / Encryption"
                )
                label_w.update("Encrypt ROOT partition with LUKS2 (argon2id)?")
                buttons = list(enc_w.query(RadioButton))
                if len(buttons) == 2:
                    buttons[0].value = not self.encrypt
                    buttons[1].value = self.encrypt
                enc_w.display = True
                hint_w.update(
                    "Up/Down: choose   Space: select   Tab: move to Continue"
                )
                enc_w.focus()

            elif step_id == STEP_LUKS_PASS:
                progress_w.update(
                    f"Step {step_num} of {total_NonReview} / LUKS2 Passphrase"
                )
                if self.strategy == 4:
                    label_w.update(
                        f"Enter existing LUKS passphrase to unlock {self.root_part}"
                    )
                    hint_w.update(
                        "Passphrase will be verified immediately against the LUKS header."
                    )
                else:
                    label_w.update(
                        "Enter LUKS2 passphrase for ROOT encryption"
                    )
                    hint_w.update(
                        "Used to unlock your drive at boot. Single line without NUL characters."
                    )
                answer_w.password = True
                answer_w.placeholder = ""
                answer_w.value = self.luks_pass
                answer_w.display = True
                answer_w.focus()
                answer_w.cursor_position = len(answer_w.value)

            elif step_id == STEP_LUKS_CONFIRM:
                progress_w.update(
                    f"Step {step_num} of {total_NonReview} / Confirm LUKS2 Passphrase"
                )
                label_w.update("Confirm LUKS2 passphrase")
                answer_w.password = True
                answer_w.placeholder = ""
                answer_w.value = self.luks_confirm
                answer_w.display = True
                hint_w.update("Enter the exact same LUKS2 passphrase again.")
                answer_w.focus()
                answer_w.cursor_position = len(answer_w.value)

            elif step_id == STEP_REVIEW:
                progress_w.update("Ready / Review storage plan")
                review_w.update(self._build_review_text())
                review_w.display = True
                if self.strategy == 1:
                    hint_w.update(
                        f"WARNING: Confirming will permanently ERASE ALL DATA on {self.disk}."
                    )
                elif self.strategy in (2, 3):
                    hint_w.update(
                        f"WARNING: Confirming will format {self.root_part}"
                        + (
                            f" and {self.efi_part}."
                            if (self.boot_mode == "UEFI" and self.format_efi)
                            else "."
                        )
                    )
                else:
                    hint_w.update(
                        "No partitions will be formatted. Use Back to edit, or Confirm to write rescue state."
                    )
                cont_btn.focus()

        def _build_review_text(self) -> str:
            strat_name = STRATEGY_LABELS.get(self.strategy, str(self.strategy))
            if self.strategy == 1:
                root_preview = f"{get_partition_path(self.disk, 2)} (new Btrfs {DUSKY_ROOT_LABEL})"
                efi_preview = (
                    f"{get_partition_path(self.disk, 1)} (new FAT32 {self.efi_size} {DUSKY_EFI_LABEL})"
                    if self.boot_mode == "UEFI"
                    else f"{get_partition_path(self.disk, 1)} (2M BIOS boot partition)"
                )
                enc_preview = (
                    "on / LUKS2 (argon2id)"
                    if self.encrypt
                    else "off / plain Btrfs"
                )
            elif self.strategy in (2, 3):
                root_preview = f"{self.root_part} (FORMAT as Btrfs {DUSKY_ROOT_LABEL})"
                if self.boot_mode == "UEFI":
                    act = (
                        f"FORMAT as FAT32 {DUSKY_EFI_LABEL}"
                        if self.format_efi
                        else "PRESERVE existing FAT32"
                    )
                    efi_preview = f"{self.efi_part} ({act})"
                else:
                    efi_preview = "N/A (Legacy BIOS mode)"
                enc_preview = (
                    "on / LUKS2 (argon2id)"
                    if self.encrypt
                    else "off / plain Btrfs"
                )
            else:
                root_preview = f"{self.root_part} (MOUNT ONLY / preserve data)"
                efi_preview = (
                    f"{self.efi_part} (MOUNT ONLY)"
                    if (self.boot_mode == "UEFI" and self.efi_part)
                    else "None / skipped"
                )
                enc_preview = (
                    "existing LUKS container (unlock only)"
                    if self.root_is_luks
                    else "unencrypted partition"
                )

            return (
                f"Target Disk:  {self.disk}\n"
                f"Boot Mode:    {self.boot_mode}\n"
                f"Strategy:     {self.strategy} - {strat_name}\n"
                f"ROOT Target:  {root_preview}\n"
                f"EFI / Boot:   {efi_preview}\n"
                f"Encryption:   {enc_preview}"
            )

        def _run_cfdisk_suspended(self) -> str:
            err_msg = ""
            with self.suspend():
                try:
                    teardown_device(self.disk)
                    ensure_mapper_free(self.disk)
                    launch_partition_editor(self.disk)
                    rescan_disk_partitions(self.disk)
                except Exception as exc:
                    err_msg = str(exc)
            self._refresh_disk_metadata(self.disk)
            valid_paths = [str(p.get("path") or "") for p in self.partitions]
            if self.root_part and not any(_same_dev(self.root_part, vp) for vp in valid_paths):
                self.root_part = ""
            if self.efi_part and not any(_same_dev(self.efi_part, vp) for vp in valid_paths):
                self.efi_part = ""
            self.cfdisk_ran = True
            return err_msg

        def advance(self) -> None:
            step_id = self.current_step_id
            options_w = self.query_one("#options", OptionList)
            answer_w = self.query_one("#answer", Input)
            error_w = self.query_one("#error", Static)

            if step_id == STEP_BIOS:
                idx = options_w.highlighted
                if idx is None:
                    error_w.update("Select an option.")
                    return
                opt = options_w.get_option_at_index(idx)
                if opt.id == "cancel":
                    self.action_cancel()
                    return
                # Setting allow_bios removes STEP_BIOS from compute_steps(); do not increment step_index.
                self.allow_bios = True
                self.render_step()
                return

            elif step_id == STEP_DISK:
                idx = options_w.highlighted
                if idx is None:
                    error_w.update("Select a target disk.")
                    return
                chosen = str(options_w.get_option_at_index(idx).id or "")
                if not chosen:
                    error_w.update("Invalid disk selection.")
                    return
                if not _same_dev(chosen, self.disk):
                    self.disk = chosen
                    self._refresh_disk_metadata(self.disk)
                    self.root_part = ""
                    self.efi_part = ""
                    self.format_efi = self.strategy == 1
                    self.cfdisk_ran = False
                    self.manual_input_mode = False
                    self.root_is_luks = False
                    self.needs_rescue_pass = False

            elif step_id == STEP_STRATEGY:
                idx = options_w.highlighted
                if idx is None:
                    error_w.update("Select a partitioning strategy.")
                    return
                chosen_strat = int(
                    str(options_w.get_option_at_index(idx).id or "1")
                )
                if chosen_strat != self.strategy:
                    self.strategy = chosen_strat
                    self.root_part = ""
                    self.efi_part = ""
                    self.format_efi = chosen_strat == 1
                    self.manual_input_mode = False
                    self.root_is_luks = False
                    self.needs_rescue_pass = False

            elif step_id == STEP_EFI_SIZE:
                raw_sz = answer_w.value.strip()
                try:
                    self.efi_size = valid_efi_size(raw_sz)
                except argparse.ArgumentTypeError as exc:
                    error_w.update(str(exc))
                    answer_w.focus()
                    return

            elif step_id == STEP_CFDISK:
                idx = options_w.highlighted
                if idx is None:
                    error_w.update("Select an action.")
                    return
                action_id = str(options_w.get_option_at_index(idx).id or "")
                if action_id == "launch":
                    err_msg = self._run_cfdisk_suspended()
                    if err_msg:
                        self.render_step()
                        self.query_one("#error", Static).update(err_msg)
                        return
                    if not self.partitions:
                        self.render_step()
                        self.query_one("#error", Static).update(
                            f"No partitions found on {self.disk}. Launch cfdisk to create partitions."
                        )
                        return
                elif action_id == "continue":
                    if not self.partitions:
                        error_w.update(
                            f"No partitions found on {self.disk}. Launch cfdisk first."
                        )
                        return

            elif step_id == STEP_ROOT:
                if not self.partitions or self.manual_input_mode:
                    try:
                        rp = validate_part_input(answer_w.value.strip())
                    except ValueError as exc:
                        error_w.update(str(exc))
                        answer_w.focus()
                        return
                    chosen_root = str(rp)
                    self.manual_input_mode = False
                else:
                    idx = options_w.highlighted
                    if idx is None:
                        error_w.update("Select a ROOT partition.")
                        return
                    opt = options_w.get_option_at_index(idx)
                    if opt.id == "__manual__":
                        self.manual_input_mode = True
                        self.render_step()
                        return
                    chosen_root = str(opt.id or "")

                if not _same_dev(chosen_root, self.root_part):
                    self.root_part = chosen_root
                    if _same_dev(self.root_part, self.efi_part):
                        self.efi_part = ""
                if self.strategy == 4:
                    self.root_is_luks = is_luks_device(self.root_part)
                    if self.root_is_luks:
                        already_open = False
                        if Path(f"/dev/mapper/{TARGET_CRYPT_NAME}").exists():
                            rs = run(
                                "cryptsetup",
                                "status",
                                TARGET_CRYPT_NAME,
                                check=False,
                                capture=True,
                            )
                            for ln in rs.stdout.splitlines():
                                if ln.strip().lower().startswith("device:"):
                                    if _same_dev(
                                        ln.split(":", 1)[1].strip(),
                                        self.root_part,
                                    ):
                                        already_open = True
                                    break
                        cred_pw = self.creds.get("ROOT_PASS", "")
                        if already_open or (
                            cred_pw
                            and test_luks_passphrase(
                                self.root_part, cred_pw.encode("utf-8")
                            )
                        ):
                            self.needs_rescue_pass = False
                        else:
                            self.needs_rescue_pass = True
                    else:
                        self.needs_rescue_pass = False
                        self.luks_pass = ""

            elif step_id == STEP_EFI:
                if not self.partitions or self.manual_input_mode:
                    try:
                        ep = validate_part_input(answer_w.value.strip())
                    except ValueError as exc:
                        error_w.update(str(exc))
                        answer_w.focus()
                        return
                    chosen_efi = str(ep)
                else:
                    idx = options_w.highlighted
                    if idx is None:
                        error_w.update("Select an EFI partition.")
                        return
                    opt = options_w.get_option_at_index(idx)
                    if opt.disabled:
                        error_w.update(
                            "EFI and ROOT cannot be the same partition."
                        )
                        return
                    if opt.id == "__manual__":
                        self.manual_input_mode = True
                        self.render_step()
                        return
                    if opt.id == "__skip__":
                        chosen_efi = ""
                    else:
                        chosen_efi = str(opt.id or "")

                if chosen_efi and _same_dev(chosen_efi, self.root_part):
                    error_w.update(
                        "EFI and ROOT cannot be the same partition."
                    )
                    return
                if self.strategy in (2, 3):
                    if not chosen_efi:
                        error_w.update(
                            "UEFI mode requires an EFI System Partition."
                        )
                        return
                    if not probe_esp_valid(chosen_efi):
                        error_w.update(
                            f"{chosen_efi} is not marked with the EFI System Partition GUID."
                        )
                        return
                self.efi_part = chosen_efi
                self.manual_input_mode = False

            elif step_id == STEP_FORMAT_EFI:
                idx = options_w.highlighted
                if idx is None:
                    error_w.update("Select an EFI formatting option.")
                    return
                choice = str(options_w.get_option_at_index(idx).id or "preserve")
                want_format = choice == "format"
                if not want_format and not probe_vfat_valid(self.efi_part):
                    error_w.update(
                        f"Preserved EFI partition {self.efi_part} does not contain FAT32 (vfat). Choose Format instead."
                    )
                    return
                self.format_efi = want_format

            elif step_id == STEP_ENCRYPT:
                self.encrypt = (
                    self.query_one("#encryption", RadioSet).pressed_index == 1
                )
                if not self.encrypt:
                    self.luks_pass = ""
                    self.luks_confirm = ""

            elif step_id == STEP_LUKS_PASS:
                val = answer_w.value
                err = password_error(val)
                if err:
                    error_w.update(err)
                    answer_w.focus()
                    return
                if self.strategy == 4:
                    if not test_luks_passphrase(
                        self.root_part, val.encode("utf-8")
                    ):
                        error_w.update(
                            f"Passphrase did not unlock {self.root_part}. Try again."
                        )
                        answer_w.focus()
                        return
                if val != self.luks_pass:
                    self.luks_confirm = ""
                self.luks_pass = val

            elif step_id == STEP_LUKS_CONFIRM:
                val = answer_w.value
                if val != self.luks_pass:
                    error_w.update("Passphrases do not match.")
                    answer_w.focus()
                    return
                self.luks_confirm = val

            elif step_id == STEP_REVIEW:
                luks_ba: bytearray | None = None
                if self.strategy in (1, 2, 3) and self.encrypt:
                    raw_pw = self.luks_pass or self.creds.get("ROOT_PASS", "")
                    if raw_pw:
                        luks_ba = bytearray(raw_pw.encode("utf-8"))
                elif self.strategy == 4 and self.root_is_luks:
                    raw_pw = self.luks_pass or self.creds.get("ROOT_PASS", "")
                    if raw_pw:
                        luks_ba = bytearray(raw_pw.encode("utf-8"))

                plan = PartitionPlan(
                    disk=self.disk,
                    boot_mode=self.boot_mode,
                    strategy=self.strategy,
                    efi_size=self.efi_size,
                    root_part=self.root_part if self.strategy in (2, 3, 4) else "",
                    efi_part=(
                        self.efi_part
                        if (self.boot_mode == "UEFI" and self.strategy in (2, 3, 4))
                        else ""
                    ),
                    format_efi=(
                        True
                        if self.strategy == 1
                        else (self.format_efi if self.strategy in (2, 3) else False)
                    ),
                    encrypt=(
                        self.encrypt
                        if self.strategy in (1, 2, 3)
                        else self.root_is_luks
                    ),
                    luks_ba=luks_ba,
                    has_win=self.has_win,
                    win_esp=self.win_esp,
                )
                self.luks_pass = ""
                self.luks_confirm = ""
                answer_w.value = ""
                self.exit(plan)
                return

            self.step_index += 1
            self.render_step()

        def on_option_list_option_selected(
            self, event: OptionList.OptionSelected
        ) -> None:
            event.option_list.highlighted = event.option_index
            self.advance()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            self.advance()

        def on_input_changed(self, event: Input.Changed) -> None:
            self.query_one("#error", Static).update("")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "back":
                self.action_back()
            elif event.button.id == "continue":
                self.advance()

        def action_back(self) -> None:
            if self.manual_input_mode and len(self.partitions) > 0:
                self.manual_input_mode = False
                self.render_step()
                return
            if self.step_index == 0:
                return
            step_id = self.current_step_id
            answer_w = self.query_one("#answer", Input)
            if step_id == STEP_LUKS_PASS:
                self.luks_pass = answer_w.value
            elif step_id == STEP_LUKS_CONFIRM:
                self.luks_confirm = answer_w.value
            elif step_id == STEP_EFI_SIZE:
                self.efi_size = answer_w.value
            self.step_index -= 1
            self.render_step()

        def action_cancel(self) -> None:
            self.luks_pass = ""
            self.luks_confirm = ""
            self.query_one("#answer", Input).value = ""
            self.exit(None)

    return PartitionWizard()


def prompt_headless_root_and_efi(
    target_dev: str,
    boot_mode: str,
    has_win: bool,
    win_esp: str | None,
    rescue: bool = False,
) -> tuple[str | None, str | None, bool]:
    r = run(
        "lsblk",
        "-l",
        "-o",
        "NAME,SIZE,TYPE,FSTYPE,PARTLABEL,LABEL",
        target_dev,
        check=False,
        capture=True,
    )
    console.print(
        Panel.fit(
            Text(r.stdout),
            title=f"Partitions on {target_dev}",
            box=box.ROUNDED,
            border_style="yellow",
        )
    )
    while True:
        try:
            raw = Prompt.ask(
                "Enter ROOT partition (q to abort)", console=console
            )
            if raw.lower() in ("q", "quit"):
                return None, None, False
            root = str(validate_part_input(raw))
            break
        except Exception as e:
            console.print(Text(str(e), style="red"))

    efi = ""
    format_efi = False
    if boot_mode == "UEFI":
        while True:
            try:
                prompt_lbl = (
                    "Enter existing EFI partition (empty to skip, q to abort)"
                    if rescue
                    else "Enter EFI partition (q to abort)"
                )
                raw = Prompt.ask(
                    prompt_lbl,
                    console=console,
                    default="" if rescue else ...,
                )
                if raw.lower() in ("q", "quit"):
                    return None, None, False
                if rescue and not raw:
                    efi = ""
                    break
                ep = str(validate_part_input(raw))
                if _same_dev(ep, root):
                    console.print(
                        "[red]EFI and ROOT cannot be the same partition.[/red]"
                    )
                    continue
                if not rescue and not probe_esp_valid(ep):
                    console.print(
                        f"[red]{ep} is not marked with the EFI System Partition GUID.[/red]"
                    )
                    continue
                efi = ep
                break
            except Exception as e:
                console.print(Text(str(e), style="red"))
        if not rescue and efi:
            if has_win and win_esp and _same_dev(efi, win_esp):
                format_efi = Confirm.ask(
                    f"EFI {efi} looks like Windows ESP {win_esp}. Format? [red]NO keeps Windows[/red]",
                    console=console,
                    default=False,
                )
            else:
                ans = Prompt.ask(
                    f"Format EFI {efi} as {DUSKY_EFI_LABEL}? (y/n/q)",
                    choices=["y", "n", "q"],
                    default="n",
                    show_choices=False,
                    console=console,
                )
                if ans == "q":
                    return None, None, False
                format_efi = ans == "y"
            if not format_efi and not probe_vfat_valid(efi):
                console.print(
                    f"[red]{efi} does not contain FAT32 (vfat) and formatting was declined. Aborting to prevent unintended data loss.[/red]"
                )
                return None, None, False
    return root, efi, format_efi


def build_headless_plan(
    args: argparse.Namespace,
    creds: dict[str, str],
    boot_mode: Literal["UEFI", "BIOS"],
    disks: list[dict],
    preset_disk: str | None,
    preset_strategy: int | None,
    preset_encrypt: bool | None,
) -> PartitionPlan | None:
    if boot_mode == "BIOS" and not args.allow_bios:
        console.print(
            "[yellow]BIOS detected, UEFI recommended. Use --allow-bios to continue[/yellow]"
        )
        if args.auto or not sys.stdin.isatty() or not Confirm.ask(
            "Continue BIOS?", console=console, default=False
        ):
            return None

    target_dev = preset_disk
    if not target_dev:
        if len(disks) == 1:
            target_dev = str(
                Path(disks[0].get("path") or disks[0].get("name")).resolve()
            )
            console.print(f"[green]Only one disk found: {target_dev}[/green]")
        else:
            if args.auto or not sys.stdin.isatty():
                console.print(
                    "[red]Multiple eligible disks detected; specify --disk /dev/... for unattended execution.[/red]"
                )
                return None
            show_disks_table(disks)
            ans = Prompt.ask(
                f"Select disk (1-{len(disks)} or q to abort)",
                console=console,
                default="1",
            )
            if ans.lower() in ("q", "quit"):
                return None
            if ans.isdigit() and 1 <= int(ans) <= len(disks):
                d = disks[int(ans) - 1]
                target_dev = str(
                    Path(d.get("path") or d.get("name")).resolve()
                )
            else:
                raise ValueError(f"Invalid disk choice: {ans}")

    has_win, win_esp = detect_windows_esp(target_dev)
    strategy = preset_strategy or 1
    do_encrypt = bool(preset_encrypt) if preset_encrypt is not None else False

    if strategy == 1:
        if not args.auto:
            if not Confirm.ask(
                f"[red]Confirm WIPE ENTIRE {target_dev}? All data will be erased![/red]",
                console=console,
                default=False,
            ):
                return None
        return PartitionPlan(
            disk=target_dev,
            boot_mode=boot_mode,
            strategy=1,
            efi_size=args.efi_size,
            format_efi=True,
            encrypt=do_encrypt,
            has_win=has_win,
            win_esp=win_esp,
        )

    if strategy == 3:
        teardown_device(target_dev)
        ensure_mapper_free(target_dev)
        launch_partition_editor(target_dev)
        rescan_disk_partitions(target_dev)

    rescue = strategy == 4
    root_part, efi_part, format_efi = prompt_headless_root_and_efi(
        target_dev, boot_mode, has_win, win_esp, rescue=rescue
    )
    if not root_part:
        return None
    return PartitionPlan(
        disk=target_dev,
        boot_mode=boot_mode,
        strategy=strategy,
        efi_size=args.efi_size,
        root_part=root_part,
        efi_part=efi_part or "",
        format_efi=format_efi,
        encrypt=do_encrypt if not rescue else is_luks_device(root_part),
        has_win=has_win,
        win_esp=win_esp,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DUSKY 030 Partitioning - 4 Strategies - 1.3G EFI default"
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Non-interactive, default to Wipe Entire Drive",
    )
    parser.add_argument("--disk", type=str, help="Target disk /dev/...")
    enc_group = parser.add_mutually_exclusive_group()
    enc_group.add_argument(
        "--encrypt", action="store_true", help="Enable LUKS2"
    )
    enc_group.add_argument(
        "--no-encrypt", action="store_true", help="Disable LUKS2"
    )
    parser.add_argument(
        "--efi-size",
        type=valid_efi_size,
        default="1.3G",
        help="EFI size for wipe (default 1.3G)",
    )
    parser.add_argument(
        "--allow-bios", action="store_true", help="Allow BIOS mode"
    )
    parser.add_argument(
        "--strategy",
        type=str,
        default=None,
        help="Partitioning strategy: wipe|existing|manual|rescue (1-4)",
    )
    parser.add_argument(
        "--rescue", action="store_true", help="Shortcut for --strategy rescue"
    )
    parser.add_argument(
        "--csum",
        choices=["blake2", "crc32c", "xxhash", "sha256"],
        default="blake2",
        help="BTRFS checksum algorithm (default: blake2)",
    )
    args = parser.parse_args()

    global BTRFS_CSUM
    if args.csum:
        BTRFS_CSUM = args.csum

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        console.print("[red]Need root[/red]")
        return 1

    is_tty = sys.stdin.isatty() and sys.stdout.isatty()
    preset_strategy = parse_strategy_cli(args)
    if not is_tty and (not args.auto or (preset_strategy and preset_strategy != 1)):
        console.print(
            "[red]Non-TTY unattended execution requires --auto with Strategy 1 (wipe). Strategies 2-4 require an interactive TTY.[/red]"
        )
        return 1

    boot_mode = detect_boot_mode()
    creds = parse_credentials()

    preset_encrypt: bool | None = None
    if args.encrypt:
        preset_encrypt = True
    elif args.no_encrypt:
        preset_encrypt = False
    elif creds.get("ENCRYPT_ROOT") in ("1", "0"):
        preset_encrypt = creds["ENCRYPT_ROOT"] == "1"

    disks = list_disks()
    if not disks:
        console.print("[red]No disks >=1G found[/red]")
        return 1

    preset_disk: str | None = None
    if args.disk:
        resolved = str(Path(args.disk).resolve())
        if not any(
            _same_dev(resolved, d.get("path") or d.get("name")) for d in disks
        ):
            console.print(
                Text(
                    f"{resolved} is not an available whole disk of at least 1 GiB",
                    style="red",
                )
            )
            return 1
        preset_disk = resolved

    if args.auto or not is_tty:
        print_banner(boot_mode)
        plan = build_headless_plan(
            args=args,
            creds=creds,
            boot_mode=boot_mode,
            disks=disks,
            preset_disk=preset_disk,
            preset_strategy=preset_strategy,
            preset_encrypt=preset_encrypt,
        )
        if plan is None:
            console.print("[yellow]Partitioning cancelled.[/yellow]")
            return 130
    else:
        try:
            wizard = create_partition_wizard(
                disks=disks,
                boot_mode=boot_mode,
                creds=creds,
                preset_disk=preset_disk,
                preset_strategy=preset_strategy,
                preset_encrypt=preset_encrypt,
                default_efi_size=args.efi_size,
                allow_bios=args.allow_bios,
            )
            plan = wizard.run()
        finally:
            restore_terminal()

        if plan is None:
            print(
                "[INFO] Partitioning cancelled; no changes were made to disks."
            )
            return 130
        print_banner(boot_mode)

    success = execute_partition_plan(plan, creds)
    if not success:
        return 130
    return 0


if __name__ == "__main__":

    def _h(s: int, f) -> None:
        restore_terminal()
        try:
            run("udevadm", "control", "--start-exec-queue", check=False, capture=True)
            run("udevadm", "settle", "--timeout=2", check=False, capture=True)
        except Exception:
            pass
        sys.exit(128 + s)

    signal.signal(signal.SIGINT, _h)
    signal.signal(signal.SIGTERM, _h)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        restore_terminal()
        sys.exit(130)
