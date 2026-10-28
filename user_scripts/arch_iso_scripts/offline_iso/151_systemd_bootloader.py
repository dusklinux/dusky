#!/usr/bin/env python3
"""Install systemd-boot entries for the Dusky ISO.

Baseline: systemd 262+, current mkinitcpio, Linux 7.3+.
Pipeline: 070 masks hooks; 120 configures initramfs; 150 builds; 151 deploys entries.
Microcode is embedded by the mkinitcpio microcode hook.
"""
from __future__ import annotations
import os, sys, re, json, shlex, shutil, signal, subprocess, atexit, tempfile
from pathlib import Path

_udev_mounted = False

def _cleanup_udev_mount():
    global _udev_mounted
    if _udev_mounted:
        try:
            subprocess.run(["umount", "-l", "/run/udev"], check=False, capture_output=True)
        except Exception:
            pass

atexit.register(_cleanup_udev_mount)

from rich.console import Console
from rich.panel import Panel
from rich import box

def make_console():
    term = os.environ.get("TERM", "")
    if term in ("dumb", "unknown"):
        return Console(color_system=None, force_terminal=False, no_color=True, legacy_windows=False)
    return Console(color_system="standard", legacy_windows=False, safe_box=True, highlight=False, markup=True)

console = make_console()

ESP_MNT = Path("/boot")
LOADER_CONF = ESP_MNT / "loader" / "loader.conf"
ENTRIES_DIR = ESP_MNT / "loader" / "entries"

def run(*cmd, check=True, capture=True, input_text=None, timeout=300):
    argv = [os.fspath(c) for c in cmd]
    try:
        if isinstance(input_text, (bytes, bytearray)):
            return subprocess.run(argv, check=check, text=False, capture_output=capture, input=bytes(input_text), timeout=timeout, env=os.environ | {"LC_ALL": "C"})
        elif isinstance(input_text, str):
            return subprocess.run(argv, check=check, text=True, capture_output=capture, input=input_text, timeout=timeout, env=os.environ | {"LC_ALL": "C"})
        return subprocess.run(argv, check=check, text=True, capture_output=capture, timeout=timeout, env=os.environ | {"LC_ALL": "C"})
    except subprocess.CalledProcessError as e:
        if check:
            console.print(f"[red]Failed: {shlex.join([str(x) for x in argv])}[/red]")
        raise

def write_boot_config(path: Path, content: str) -> None:
    """Publish a complete file on the ESP, preserving the previous one on failure."""
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def detect_boot_mode() -> str:
    for sp in (Path("/etc/dusky_state.json"), Path("/mnt/etc/dusky_state.json")):
        try:
            bm = str(json.loads(sp.read_text()).get("boot_mode", "")).upper()
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

def is_mountpoint(p: Path) -> bool:
    try:
        return p.is_mount()
    except Exception:
        return False

def get_parent_disk(part_path: Path) -> Path:
    part_path = part_path.resolve()
    try:
        r = run("lsblk", "-ndo", "PKNAME", str(part_path), check=False, capture=True)
        pk = r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""
        if pk:
            return Path(f"/dev/{pk}")
    except:
        pass
    # Fallback: lsblk -s inverse
    try:
        r = run("lsblk", "-nrpso", "NAME,TYPE", str(part_path), check=False, capture=True)
        for line in r.stdout.splitlines():
            if "disk" in line:
                m = re.search(r"(/dev/\S+)", line)
                if m:
                    return Path(m.group(1))
    except:
        pass
    s = str(part_path)
    m = re.match(r"^(.*?)(?:p\d+|\d+)$", s)
    if m and Path(m.group(1)).exists():
        return Path(m.group(1))
    return Path(s)

def parse_mkinicpio_hooks() -> str:
    script = """
    set +u
    source /etc/mkinitcpio.conf >/dev/null 2>&1 || true
    shopt -s nullglob
    for conf in /etc/mkinitcpio.conf.d/*.conf; do
        source "$conf" >/dev/null 2>&1 || true
    done
    echo "${HOOKS[*]:-}"
    """
    try:
        r = subprocess.run(["env", "-i", "bash", "-c", script], text=True, capture_output=True, timeout=5)
        return f" {r.stdout.strip()} "
    except:
        return " "

def get_root_topology() -> dict:
    """Read one consistent root mount record; never guess the filesystem type."""
    result = run("findmnt", "--json", "--output", "SOURCE,UUID,FSTYPE,OPTIONS", "--target", "/")
    mount = json.loads(result.stdout)["filesystems"][0]
    device = (mount.get("source") or "").split("[", 1)[0]
    uuid = mount.get("uuid")
    fstype = mount.get("fstype")
    if not device or not uuid or not fstype:
        raise RuntimeError("Cannot resolve root device, UUID, and filesystem type")
    options = mount.get("options") or ""
    subvol = next((option.removeprefix("subvol=") for option in options.split(",")
                   if option.startswith("subvol=")), "")
    return {"ROOT_BLK_DEV": device, "ROOT_UUID": uuid, "ROOT_FSTYPE": fstype,
            "ROOT_OPTS": options, "ROOT_SUBVOL": subvol}

def detect_luks(root_blk_dev: str) -> dict:
    try:
        r = run("lsblk", "-nrspo", "PATH,TYPE", "-s", "--", root_blk_dev, check=True)
        crypt_dev = ""
        for line in r.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "crypt":
                crypt_dev = parts[0]
                break
        if not crypt_dev:
            return {"found": False}
        console.print(f"[yellow]LUKS2 detected: {crypt_dev}[/yellow]")
        mapper_name = Path(crypt_dev).name
        r = run("cryptsetup", "status", mapper_name, check=True)
        backing_dev = ""
        for line in r.stdout.splitlines():
            if "device:" in line.lower():
                backing_dev = line.split("device:")[-1].strip().split()[0]
                break
        if not backing_dev:
            console.print(f"[red]Could not determine backing device for {mapper_name}[/red]"); sys.exit(1)
        r = run("blkid", "-s", "UUID", "-o", "value", backing_dev, check=True)
        luks_uuid = r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""
        if not luks_uuid:
            console.print(f"[red]Could not determine LUKS UUID for {backing_dev}[/red]"); sys.exit(1)
        return {"found": True, "CRYPT_DEV": crypt_dev, "MAPPER_NAME": mapper_name, "BACKING_DEV": backing_dev, "LUKS_UUID": luks_uuid}
    except Exception as e:
        raise RuntimeError(f"Cannot determine root encryption topology: {e}") from e

def ensure_esp():
    if not is_mountpoint(ESP_MNT):
        console.print(f"[red]{ESP_MNT} is NOT a mountpoint. Ensure FAT32 ESP is mounted.[/red]"); sys.exit(1)
    r = run("findmnt", "-n", "-e", "-o", "FSTYPE", str(ESP_MNT), check=False)
    fstype = r.stdout.strip().splitlines()[0].strip().lower() if r.stdout.strip() else ""
    if fstype not in ("vfat", "fat32", "msdos"):
        console.print(f"[red]{ESP_MNT} is {fstype}, but systemd-boot requires FAT32[/red]"); sys.exit(1)
    try:
        stat = os.statvfs(str(ESP_MNT))
        free_mb = (stat.f_bavail * stat.f_frsize) / (1024 * 1024)
        total_mb = (stat.f_blocks * stat.f_frsize) / (1024 * 1024)
        console.print(f"[cyan]ESP capacity: {total_mb:.1f} MiB total, {free_mb:.1f} MiB available[/cyan]")
        if free_mb < 150:
            console.print(f"[yellow]WARNING: Low ESP space ({free_mb:.1f} MiB available). Arch kernels & initramfs require sufficient space.[/yellow]")
    except Exception as e:
        console.print(f"[dim]Could not query ESP space: {e}[/dim]")

def get_kernels() -> list[tuple[Path, str]]:
    kernels = []
    for kdir in Path("/usr/lib/modules").glob("*"):
        if (kdir / "pkgbase").is_file() and (kdir / "vmlinuz").is_file():
            pkgbase = (kdir / "pkgbase").read_text().strip()
            if pkgbase:
                kernels.append((kdir, pkgbase))
    return kernels

def build_cmdlines(topo: dict, luks: dict, hooks_str: str) -> tuple[str, str, str]:
    """
    Build modern 2026 cmdlines:
    - base_core: root=UUID=... rootfstype=... rootflags=...
    - luks_part: rd.luks.name=... or cryptdevice=...
    - primary (with Plymouth): rw quiet splash loglevel=3 systemd.show_status=auto rd.udev.log_level=3 vt.global_cursor_default=0 <luks> <base_core>
    - fallback (no Plymouth): rw loglevel=3 <luks> <base_core>
    Order: quiet before loglevel per Arch Silent Boot wiki.
    """
    base_core = f"rootfstype={topo['ROOT_FSTYPE']}"
    # fsck.mode is global: skipping a Btrfs root must not skip FAT ESP checks.

    luks_part = ""
    if luks["found"]:
        if " sd-encrypt " in hooks_str:
            console.print("[green]Using sd-encrypt hook (systemd native)[/green]")
            sd_luks_opts = ["discard"]
            try:
                bname = Path(luks["BACKING_DEV"]).resolve().name
                rot_cand = Path(f"/sys/class/block/{bname}/queue/rotational")
                if not rot_cand.exists():
                    block = Path(f"/sys/class/block/{bname}").resolve()
                    if (block / "partition").exists():
                        block = block.parent
                    rot_cand = block / "queue/rotational"
                if rot_cand.exists() and rot_cand.read_text().strip() == "0":
                    sd_luks_opts += ["no-read-workqueue", "no-write-workqueue"]
            except Exception:
                pass
            luks_part = f"rd.luks.name={luks['LUKS_UUID']}={luks['MAPPER_NAME']} rd.luks.options={','.join(sd_luks_opts)} root=UUID={topo['ROOT_UUID']}"
        elif " encrypt " in hooks_str:
            console.print("[yellow]Using legacy encrypt hook[/yellow]")
            luks_part = f"cryptdevice=UUID={luks['LUKS_UUID']}:{luks['MAPPER_NAME']}:allow-discards root=/dev/mapper/{luks['MAPPER_NAME']}"
        else:
            console.print("[red]LUKS detected but neither sd-encrypt nor encrypt in HOOKS[/red]"); sys.exit(1)
    else:
        console.print(f"[green]Plain {topo['ROOT_FSTYPE']} detected[/green]")
        luks_part = f"root=UUID={topo['ROOT_UUID']}"

    if topo["ROOT_SUBVOL"]:
        base_core += f" rootflags=subvol={topo['ROOT_SUBVOL']}"

    # Modern Plymouth cmdline per research: quiet splash loglevel=3 systemd.show_status=auto rd.udev.log_level=3 vt.global_cursor_default=0
    # Primary: rw quiet splash loglevel=3 systemd.show_status=auto rd.udev.log_level=3 vt.global_cursor_default=0 <luks> <base_core>
    primary = f"rw quiet splash loglevel=3 systemd.show_status=auto rd.udev.log_level=3 vt.global_cursor_default=0 {luks_part} {base_core}"
    # Fallback: rw loglevel=3 <luks> <base_core> (no quiet splash, full logs)
    fallback = f"rw loglevel=3 {luks_part} {base_core}"

    return (primary.strip(), fallback.strip(), base_core)

def generate_secondary_linux_bls_entries(esp_mnt: Path):
    entries_dir = esp_mnt / "loader" / "entries"
    entries_dir.mkdir(parents=True, exist_ok=True)

    vendor_configs = [
        ("ubuntu", "Ubuntu Linux (GRUB)", "shimx64.efi"),
        ("fedora", "Fedora Linux (GRUB)", "shimx64.efi"),
        ("debian", "Debian GNU/Linux", "grubx64.efi"),
        ("pop", "Pop!_OS (GRUB)", "grubx64.efi"),
        ("opensuse", "openSUSE Linux", "grub.efi"),
    ]

    for vendor, title, efi_bin in vendor_configs:
        v_path = esp_mnt / "EFI" / vendor / efi_bin
        if v_path.is_file():
            conf_path = entries_dir / f"{vendor}-chainload.conf"
            content = f"title   {title}\nefi     /EFI/{vendor}/{efi_bin}\n"
            write_boot_config(conf_path, content)
            console.print(f"[green]Generated secondary Linux BLS entry: {conf_path}[/green]")

def ensure_udev_bind_mount_in_chroot():
    global _udev_mounted
    run_udev_data = Path("/run/udev/data")
    if not run_udev_data.exists():
        return
    if is_mountpoint(Path("/run/udev")):
        return
    chroot_udev = Path("/run/udev")
    if not chroot_udev.exists():
        try:
            chroot_udev.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
    try:
        r = run("mount", "--bind", "/run/udev", "/run/udev", check=False, capture=True)
        if r.returncode == 0:
            _udev_mounted = True
            console.print("[green]Bind-mounted /run/udev for bootctl sd-device PARTUUID resolution[/green]")
    except Exception:
        pass

def install_systemd_boot_uefi(primary_opts: str, fallback_opts: str):
    console.print(Panel(f"[bold cyan]UEFI Mode - systemd-boot - Type #1 BLS - No ucode lines[/bold cyan]", box=box.ROUNDED))
    ensure_esp()
    kernels = get_kernels()
    if not kernels:
        console.print("[red]No kernels in /usr/lib/modules[/red]"); sys.exit(1)

    # Validate before replacing entries: stale or absent initramfs files cannot boot.
    for _, pkgbase in kernels:
        image = ESP_MNT / f"initramfs-{pkgbase}.img"
        if not image.is_file() or image.stat().st_size == 0:
            raise RuntimeError(f"Required initramfs is missing or empty: {image}")

    ensure_udev_bind_mount_in_chroot()

    win_path = ESP_MNT / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi"
    if win_path.exists():
        console.print(f"[cyan]Windows detected at {win_path} -> auto-windows (no manual entry)[/cyan]")
    else:
        console.print("[dim]No Windows ESP detected[/dim]")

    # bootctl install with modern flags: --variables=yes --efi-boot-option-description-with-device=yes --graceful
    # --graceful is auto in chroot since v258 but explicit is best practice
    console.print(f"[yellow]Deploying systemd-boot to {ESP_MNT}...[/yellow]")
    is_installed = run("bootctl", "is-installed", f"--esp-path={ESP_MNT}", check=False).returncode == 0
    if is_installed:
        console.print("[cyan]Updating existing systemd-boot...[/cyan]")
        run("bootctl", "update", f"--esp-path={ESP_MNT}", "--variables=yes", "--efi-boot-option-description-with-device=yes", "--graceful")
    else:
        console.print("[cyan]Fresh install...[/cyan]")
        r = run("bootctl", "install", f"--esp-path={ESP_MNT}", "--variables=yes", "--efi-boot-option-description-with-device=yes", "--graceful", check=False)
        if r.returncode != 0:
            console.print("[yellow]bootctl install non-zero, trying fallback without EFI variable updates...[/yellow]")
            run("bootctl", "install", f"--esp-path={ESP_MNT}", "--variables=no", "--graceful")

    # Ensure systemd-bootx64.efi is present in ESP
    systemd_efi = ESP_MNT / "EFI" / "systemd" / "systemd-bootx64.efi"
    if not systemd_efi.is_file():
        systemd_src = Path("/usr/lib/systemd/boot/efi/systemd-bootx64.efi")
        if systemd_src.is_file():
            systemd_efi.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(systemd_src, systemd_efi)
            console.print(f"[green]Copied {systemd_src} -> {systemd_efi}[/green]")

    # Ensure removable media EFI fallback exists for strict/legacy laptop EFI firmware (InsydeH2O)
    fallback_efi = ESP_MNT / "EFI" / "BOOT" / "BOOTX64.EFI"
    if systemd_efi.is_file() and not fallback_efi.is_file():
        fallback_efi.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(systemd_efi, fallback_efi)
        console.print(f"[green]Copied removable fallback EFI: {fallback_efi}[/green]")

    if not systemd_efi.is_file() and not fallback_efi.is_file():
        console.print("[red]bootctl installation failed: No EFI binaries found or deployed![/red]")
        sys.exit(1)

    # Post-bootctl NVRAM sanity check & fix:
    # If bootctl created an NVRAM entry with zeroed GPT GUID (common inside chroot without udev data),
    # clean it up and register with efibootmgr using the actual partition device path.
    try:
        r_efiv = run("efibootmgr", "-v", check=False)
        if r_efiv.returncode == 0 and r_efiv.stdout:
            zero_entries = []
            for line in r_efiv.stdout.splitlines():
                if "00000000-0000-0000-0000-000000000000" in line and "\\EFI\\systemd\\systemd-boot" in line:
                    m = re.match(r"^Boot([0-9A-Fa-f]{4})", line)
                    if m:
                        zero_entries.append(m.group(1))
            for bnum in zero_entries:
                console.print(f"[yellow]Removing zeroed GUID NVRAM entry Boot{bnum}...[/yellow]")
                run("efibootmgr", "-b", bnum, "-B", check=False)

            esp_dev = run("findmnt", "-n", "-e", "-o", "SOURCE", str(ESP_MNT)).stdout.splitlines()[-1].strip()
            esp_uuid = run("blkid", "-s", "PARTUUID", "-o", "value", esp_dev).stdout.strip().lower()
            if not esp_uuid:
                raise ValueError("Cannot identify the selected ESP partition UUID")
            has_valid_entry = False
            r_efiv2 = run("efibootmgr", "-v", check=False)
            for line in r_efiv2.stdout.splitlines():
                if f"gpt,{esp_uuid}," in line.lower() and "\\efi\\systemd\\systemd-bootx64.efi" in line.lower():
                    has_valid_entry = True
                    break

            if not has_valid_entry:
                console.print("[cyan]Registering valid NVRAM entry via efibootmgr...[/cyan]")
                if esp_dev:
                    parent_disk = get_parent_disk(Path(esp_dev))
                    m_part = re.search(r"(\d+)$", esp_dev)
                    part_num = m_part.group(1) if m_part else "1"
                    run("efibootmgr", "-c", "-d", str(parent_disk), "-p", part_num, "-L", "Linux Boot Manager", "-l", r"\EFI\systemd\systemd-bootx64.efi", check=False)
    except Exception as e:
        console.print(f"[yellow]NVRAM sanity check warning: {e}[/yellow]")

    console.print("[green]systemd-boot deployed, random-seed auto-handled since systemd 257+[/green]")

    LOADER_CONF.parent.mkdir(parents=True, exist_ok=True)
    loader_conf_str = (
        "default  @saved\n"
        "timeout  2\n"
        "console-mode max\n"
        "editor   no\n"
        "auto-entries 1\n"
        "auto-firmware 1\n"
        "reboot-for-bitlocker yes\n"
    )
    write_boot_config(LOADER_CONF, loader_conf_str)
    console.print(f"[green]Wrote {LOADER_CONF} (default @saved = EFI var saved on every boot, BitLocker reboot enabled)[/green]")

    generate_secondary_linux_bls_entries(ESP_MNT)

    for kdir, pkgbase in kernels:
        src = kdir / "vmlinuz"
        dst = ESP_MNT / f"vmlinuz-{pkgbase}"
        console.print(f"[cyan]Staging {pkgbase}: {src} -> {dst} (before mkinitcpio -P)[/cyan]")
        shutil.copy2(src, dst)

    ENTRIES_DIR.mkdir(parents=True, exist_ok=True)
    for _, pkgbase in kernels:
        console.print(f"[yellow]Generating BLS Type #1 for {pkgbase} (no microcode initrd - embedded via microcode hook)[/yellow]")
        write_boot_config(ENTRIES_DIR / f"arch-{pkgbase}.conf",
            f"title   Arch Linux ({pkgbase})\n"
            f"linux   /vmlinuz-{pkgbase}\n"
            f"initrd  /initramfs-{pkgbase}.img\n"
            f"options {primary_opts}\n"
        )
        fallback_image = ESP_MNT / f"initramfs-{pkgbase}-fallback.img"
        if not fallback_image.is_file() or fallback_image.stat().st_size == 0:
            (ENTRIES_DIR / f"arch-{pkgbase}-fallback.conf").unlink(missing_ok=True)
            console.print(f"[yellow]No fallback image for {pkgbase}; skipping recovery entry.[/yellow]")
            continue
        write_boot_config(ENTRIES_DIR / f"arch-{pkgbase}-fallback.conf",
            f"title   Arch Linux ({pkgbase} - Fallback Recovery)\n"
            f"linux   /vmlinuz-{pkgbase}\n"
            f"initrd  /initramfs-{pkgbase}-fallback.img\n"
            f"options {fallback_opts}\n"
        )

    # Start the newly installed OS once; @saved remembers later user choices.
    primary_pkg = next((pkg for _, pkg in kernels if pkg == "linux"), kernels[0][1])
    first_entry = f"arch-{primary_pkg}.conf"
    selection = run("bootctl", f"--esp-path={ESP_MNT}", "set-oneshot", first_entry, check=False)
    if selection.returncode != 0:
        # Firmware with read-only variables still needs a usable initial default.
        write_boot_config(LOADER_CONF, loader_conf_str.replace("default  @saved", f"default  {first_entry}"))
        console.print("[yellow]EFI variables unavailable; using the installed kernel as the default.[/yellow]")

    run("systemctl", "enable", "systemd-boot-update.service", check=False)
    console.print(Panel(f"[bold green]UEFI Complete\nKernels: {', '.join([k[1] for k in kernels])}\nMicrocode: embedded via microcode hook (no initrd line)\nWindows: auto-windows\nrandom-seed: auto since systemd 257[/bold green]", box=box.ROUNDED))

def install_grub_bios_fallback(topo: dict, luks: dict, primary_opts: str, fallback_opts: str):
    console.print(Panel(f"[bold yellow]BIOS Mode - GRUB i386-pc Fallback[/bold yellow]", box=box.ROUNDED))
    root_dev = Path(topo["ROOT_BLK_DEV"]).resolve()
    if luks["found"]:
        root_dev = Path(luks["BACKING_DEV"]).resolve()
    disk = get_parent_disk(root_dev)
    console.print(f"[cyan]Root partition: {root_dev} -> Disk: {disk}[/cyan]")

    # Check BIOS boot partition exists on GPT - GUID 21686148-6449-6e6f-744e-656564454649 EF02
    try:
        r = run("sgdisk", "--print", str(disk), check=False)
        if "EF02" not in r.stdout and "21686148-6449-6e6f-744e-656564454649" not in r.stdout and "BIOS boot" not in r.stdout:
            console.print(Panel(f"[yellow]WARNING: No BIOS boot partition (type EF02 GUID 21686148-6449-6e6f-744e-656564454649) found on {disk}. grub-install may fail on GPT. Create 1MiB EF02 partition.[/yellow]", box=box.ROUNDED))
    except:
        console.print("[dim]sgdisk not found, skipping BIOS boot partition check[/dim]")

    console.print("[yellow]Ensuring GRUB & os-prober for BIOS mode...[/yellow]")
    run("pacman", "-S", "--needed", "--noconfirm", "grub", "os-prober", timeout=None)

    if luks["found"]:
        console.print("[yellow]Checking LUKS2 PBKDF for GRUB (only PBKDF2 supported, not argon2id)...[/yellow]")
        r = run("cryptsetup", "luksDump", luks["BACKING_DEV"], check=False)
        if "argon2i" in r.stdout or "argon2id" in r.stdout:
            console.print(Panel(
                "[bold red]GRUB BIOS cannot unlock argon2id LUKS2![/bold red]\n"
                "Arch default is argon2id. GRUB only supports pbkdf2.\n"
                "Options:\n"
                "1) Separate unencrypted /boot (Fedora model, recommended for BIOS)\n"
                "2) Convert: cryptsetup luksConvertKey --pbkdf pbkdf2 /dev/<backing>\n"
                "Proceeding with warning - boot will fail if /boot inside LUKS.",
                box=box.ROUNDED, title="GRUB Warning"
            ))

    grub_default = Path("/etc/default/grub")
    grub_default.parent.mkdir(parents=True, exist_ok=True)
    # Use primary_opts for GRUB_CMDLINE_LINUX_DEFAULT to keep quiet splash for BIOS too
    grub_cfg = f"""# Generated by 151_systemd_bootloader.py - BIOS fallback - Plymouth 26
GRUB_DEFAULT=0
GRUB_TIMEOUT=5
GRUB_DISTRIBUTOR="Arch"
GRUB_CMDLINE_LINUX_DEFAULT="{primary_opts}"
GRUB_CMDLINE_LINUX=""
GRUB_ENABLE_CRYPTODISK=y
GRUB_PRELOAD_MODULES="part_gpt part_msdos btrfs lvm cryptodisk luks2 ntfs fat ext2"
GRUB_DISABLE_OS_PROBER=false
GRUB_TIMEOUT_STYLE=menu
"""
    grub_default.write_text(grub_cfg)
    console.print(f"[green]Wrote {grub_default}[/green]")

    console.print(f"[yellow]Installing GRUB i386-pc to {disk}...[/yellow]")
    run("grub-install", "--target=i386-pc", "--boot-directory=/boot", "--recheck", str(disk))

    console.print("[yellow]Synchronizing udev before os-prober...[/yellow]")
    run("udevadm", "settle", check=False)

    console.print("[yellow]Generating /boot/grub/grub.cfg (os-prober active)...[/yellow]")
    r_mk = run("grub-mkconfig", "-o", "/boot/grub/grub.cfg")
    if "Found Windows" in (r_mk.stdout or "") or "Found Linux" in (r_mk.stdout or ""):
        console.print("[bold green]os-prober successfully detected secondary operating system(s)![/bold green]")
    console.print(Panel(f"[bold green]GRUB BIOS Complete\nDisk: {disk}\nConfig: /boot/grub/grub.cfg[/bold green]", box=box.ROUNDED))

def main():
    if os.geteuid() != 0:
        console.print("[red]Must be run as root inside arch-chroot[/red]"); sys.exit(1)

    boot_mode = detect_boot_mode()
    console.print(Panel(f"[bold]DUSKY Bootloader Orchestrator - {boot_mode} - systemd 262+ - Plymouth[/bold]", box=box.ROUNDED))

    topo = get_root_topology()
    hooks_str = parse_mkinicpio_hooks()
    luks = detect_luks(topo["ROOT_BLK_DEV"])

    primary_opts, fallback_opts, _ = build_cmdlines(topo, luks, hooks_str)

    if boot_mode == "UEFI":
        install_systemd_boot_uefi(primary_opts, fallback_opts)
    else:
        install_grub_bios_fallback(topo, luks, primary_opts, fallback_opts)

if __name__ == "__main__":
    def _h(sig, frame):
        console.print(f"\n[yellow]Signal {signal.Signals(sig).name}, exiting[/yellow]")
        sys.exit(128 + sig)
    signal.signal(signal.SIGINT, _h)
    signal.signal(signal.SIGTERM, _h)
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
