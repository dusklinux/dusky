#!/usr/bin/env python3
"""
Elite ZRAM & Swap Subsystem Manager
Provides a robust, unified management backend for:
- ZRAM0 (Compressed RAM Swap @ Priority 32767)
- ZRAM1 (/mnt/zram1 Ephemeral RAM Disk - Native High-Performance Tmpfs)
- Disk Swap (/swap/swapfile @ Lowest Priority -1)
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn

# --- ANSI Formatting ---
class C:
    RED = "\033[1;31m"
    GRN = "\033[1;32m"
    YLW = "\033[1;33m"
    BLU = "\033[1;34m"
    CYN = "\033[1;36m"
    BOLD = "\033[1m"
    RST = "\033[0m"

def info(msg: str) -> None: print(f"{C.BLU}[INFO]{C.RST} {msg}")
def ok(msg: str) -> None: print(f"{C.GRN}[ OK ]{C.RST} {msg}")
def warn(msg: str) -> None: print(f"{C.YLW}[WARN]{C.RST} {msg}")
def err(msg: str) -> None: print(f"{C.RED}[FAIL]{C.RST} {msg}", file=sys.stderr)
def die(msg: str, code: int = 1) -> NoReturn:
    err(msg)
    sys.exit(code)

# --- Core Paths & Constants ---
ZRAM_CONF_DIR = Path("/etc/systemd/zram-generator.conf.d")
ZRAM0_CONF = ZRAM_CONF_DIR / "99-zram0.conf"
LEGACY_ZRAM0_CONFS = [
    ZRAM_CONF_DIR / "99-elite-zram.conf",
    ZRAM_CONF_DIR / "99-elite-zram0.conf",
    ZRAM_CONF_DIR / "99-memtune.conf",
]
ZRAM1_CONF = ZRAM_CONF_DIR / "99-zram1.conf"
LEGACY_ZRAM1_CONFS = [
    ZRAM_CONF_DIR / "99-elite-zram1.conf",
]
SWAPFILE_PATH = Path("/swap/swapfile")
FSTAB_PATH = Path("/etc/fstab")
MOUNT_POINT = Path("/mnt/zram1")
BASE_MOUNT = Path("/mnt")
TMPFILES_CONF = Path("/etc/tmpfiles.d/zram-mounts.conf")
OVERRIDE_DIR = Path("/etc/systemd/system/systemd-zram-setup@zram1.service.d")
OVERRIDE_CONF = OVERRIDE_DIR / "override.conf"
LEGACY_MAKEFS_OVERRIDE_DIR = Path("/etc/systemd/system/systemd-makefs@dev-zram1.service.d")
LEGACY_PERMS_SERVICE = Path("/etc/systemd/system/mnt-zram1-permissions.service")
LEGACY_PERMS_WANTS = Path("/etc/systemd/system/mnt-zram1.mount.wants/mnt-zram1-permissions.service")
LEGACY_PERMS_WANTS_DIR = Path("/etc/systemd/system/mnt-zram1.mount.wants")
TMPFS_MOUNT_UNIT = Path("/etc/systemd/system/mnt-zram1.mount")

def write_file_atomic(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == content:
        return
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

def get_real_user_info() -> tuple[str, int, int, Path]:
    sudo_user = os.environ.get("SUDO_USER")
    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if sudo_user and sudo_uid and int(sudo_uid) != 0:
        try:
            import pwd
            pw = pwd.getpwnam(sudo_user)
            return sudo_user, int(sudo_uid), int(sudo_gid or pw.pw_gid), Path(pw.pw_dir)
        except Exception:
            pass
    try:
        import pwd
        loginuid_path = Path("/proc/self/loginuid")
        if loginuid_path.exists():
            l_uid = int(loginuid_path.read_text().strip())
            if 0 < l_uid < 65534:
                pw = pwd.getpwuid(l_uid)
                return pw.pw_name, pw.pw_uid, pw.pw_gid, Path(pw.pw_dir)
        for pw in pwd.getpwall():
            if 1000 <= pw.pw_uid < 60000 and pw.pw_name != "nobody":
                return pw.pw_name, pw.pw_uid, pw.pw_gid, Path(pw.pw_dir)
    except Exception:
        pass
    return "root", 0, 0, Path.home()

def notify(title: str, message: str, urgency: str = "normal") -> None:
    """Dispatches a desktop notification dynamically to the active user."""
    if shutil.which("notify-send"):
        try:
            user, uid, _, _ = get_real_user_info()
            if user != "root" and os.geteuid() == 0:
                runtime_dir = f"/run/user/{uid}"
                env = os.environ.copy()
                env["XDG_RUNTIME_DIR"] = runtime_dir
                subprocess.run(
                    ["sudo", "-u", user, "notify-send", "-a", "Dusky Swap Manager", "-u", urgency, title, message],
                    env=env,
                    check=False,
                    timeout=2
                )
            else:
                subprocess.run(["notify-send", "-a", "Dusky Swap Manager", "-u", urgency, title, message], check=False, timeout=2)
        except Exception:
            pass

def escalate_root_if_needed() -> None:
    if os.geteuid() != 0:
        if shutil.which("sudo"):
            os.execvp("sudo", ["sudo", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
        elif shutil.which("pkexec"):
            os.execvp("pkexec", ["pkexec", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
        else:
            die("Root privileges required (sudo or pkexec not available).")

def run_cmd(cmd: list[str], check: bool = True) -> str:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=check)
        return res.stdout.strip()
    except subprocess.CalledProcessError as e:
        if check:
            die(f"Command failed: {' '.join(cmd)}\n{e.stderr.strip()}")
        return e.stdout.strip() + e.stderr.strip()

# =============================================================================
# PERMISSION & DIRECTORY LIFECYCLE MANAGEMENT
# =============================================================================

def fix_mount_permissions() -> None:
    """
    Guarantees user-writable, unrestricted access to /mnt and /mnt/zram1:
    - Normalizes /mnt to 0755 root:root and removes any restrictive POSIX ACLs.
    - Sets /mnt/zram1 to mode 1777 (sticky bit world-writable, like /tmp).
    - Installs /etc/tmpfiles.d/zram-mounts.conf for systemd-tmpfiles boot enforcement.
    - Purges obsolete tune2fs service overrides and legacy permissions units.
    """
    # 1. Base /mnt access
    if not BASE_MOUNT.exists():
        try:
            BASE_MOUNT.mkdir(parents=True, mode=0o755)
        except Exception:
            pass
    try:
        os.chmod(BASE_MOUNT, 0o755)
        if shutil.which("setfacl"):
            subprocess.run(["setfacl", "-b", "/mnt"], capture_output=True, check=False)
    except Exception:
        pass

    # 2. Ephemeral RAM Disk /mnt/zram1
    if not MOUNT_POINT.exists():
        try:
            MOUNT_POINT.mkdir(parents=True, mode=0o755)
        except Exception:
            pass
    try:
        os.chmod(MOUNT_POINT, 0o1777)
        if shutil.which("setfacl"):
            subprocess.run(["setfacl", "-b", str(MOUNT_POINT)], capture_output=True, check=False)
    except Exception:
        pass

    # 3. Systemd tmpfiles.d declarative config
    tmpfiles_content = """# Managed by Dusky Memory & Swap Subsystem
d /mnt 0755 root root -
d /mnt/zram1 1777 root root -
z /mnt 0755 root root -
z /mnt/zram1 1777 root root -
"""
    try:
        write_file_atomic(TMPFILES_CONF, tmpfiles_content)
        if shutil.which("systemd-tmpfiles"):
            subprocess.run(["systemd-tmpfiles", "--create", str(TMPFILES_CONF)], capture_output=True, check=False)
    except Exception:
        pass

    # 4. Purge obsolete tune2fs service overrides and legacy permission units
    # Mode 1777 is declaratively enforced by X-mount.mode=1777 and tmpfiles.d.
    # Native ext2 has zero filesystem journaling by design (no tune2fs hack needed).
    if OVERRIDE_DIR.exists():
        shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
    if LEGACY_MAKEFS_OVERRIDE_DIR.exists():
        shutil.rmtree(LEGACY_MAKEFS_OVERRIDE_DIR, ignore_errors=True)
    if LEGACY_PERMS_SERVICE.exists():
        LEGACY_PERMS_SERVICE.unlink(missing_ok=True)
    if LEGACY_PERMS_WANTS.exists() or LEGACY_PERMS_WANTS.is_symlink():
        LEGACY_PERMS_WANTS.unlink(missing_ok=True)
    if LEGACY_PERMS_WANTS_DIR.exists():
        shutil.rmtree(LEGACY_PERMS_WANTS_DIR, ignore_errors=True)

# =============================================================================
# STATUS QUERIES & FORMATTING (Non-Root Safe)
# =============================================================================

def get_total_ram_gb() -> float:
    try:
        meminfo = Path("/proc/meminfo").read_text()
        m = re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo)
        if m:
            return int(m.group(1)) / (1024 * 1024)
    except Exception:
        pass
    return 8.0

def format_clean_size(expr_or_size: str) -> str:
    s = expr_or_size.strip()
    total_ram = get_total_ram_gb()
    
    m = re.search(r"ram\s*\*\s*([0-9.]+)", s)
    if m:
        factor = float(m.group(1))
        gb = total_ram * factor
        return f"{gb:.1f} GB" if not gb.is_integer() else f"{gb:.0f} GB"
    
    m_div = re.search(r"ram\s*/\s*([0-9.]+)", s)
    if m_div:
        denom = float(m_div.group(1))
        factor = 1.0 / denom
        gb = total_ram * factor
        return f"{gb:.1f} GB" if not gb.is_integer() else f"{gb:.0f} GB"

    if s.lower() == "ram":
        return f"{total_ram:.1f} GB" if not total_ram.is_integer() else f"{total_ram:.0f} GB"
    
    m_num = re.match(r"^([0-9.]+)\s*([KMGT]?B?)$", s, re.IGNORECASE)
    if m_num:
        val = float(m_num.group(1))
        unit = m_num.group(2).upper()
        if unit.startswith("G"):
            return f"{val:.1f} GB" if not val.is_integer() else f"{val:.0f} GB"
        elif unit.startswith("M"):
            return f"{val:.0f} MB"
        elif not unit:
            gb = val / 1024 if val >= 512 else val
            return f"{gb:.1f} GB" if not gb.is_integer() else f"{gb:.0f} GB"
            
    return s

def get_zram0_status() -> str:
    try:
        swaps = Path("/proc/swaps").read_text() if Path("/proc/swaps").exists() else ""
        if "/dev/zram0" in swaps:
            zram_out = run_cmd(["zramctl", "--output", "DISKSIZE", "--noheadings", "/dev/zram0"], check=False)
            size = zram_out.strip() if zram_out else ""
            if size:
                return format_clean_size(size)
            return "Active"
        return "Disabled"
    except Exception:
        return "Disabled"

def get_zram0_size() -> str:
    for conf in [ZRAM0_CONF] + LEGACY_ZRAM0_CONFS:
        if conf.exists():
            match = re.search(r"zram-size\s*=\s*(.+)", conf.read_text())
            if match:
                raw = match.group(1).strip()
                m = re.search(r"ram\s*\*\s*([0-9.]+)", raw)
                if m:
                    factor = float(m.group(1))
                    return f"{int(round(factor * 100))}%"
                m_div = re.search(r"ram\s*/\s*([0-9.]+)", raw)
                if m_div:
                    factor = 1.0 / float(m_div.group(1))
                    return f"{int(round(factor * 100))}%"
                if raw == "ram":
                    return "100%"
                return format_clean_size(raw)
    total_ram = get_total_ram_gb()
    return "150%" if total_ram < 28.0 else "50%"

def get_zram1_status() -> str:
    try:
        source = run_cmd(["findmnt", "-rn", "-o", "SOURCE", "--mountpoint", "/mnt/zram1"], check=False)
        if source:
            size_out = run_cmd(["findmnt", "-rn", "-o", "SIZE", "--mountpoint", "/mnt/zram1"], check=False)
            return format_clean_size(size_out) if size_out else "Active"
        return "Disabled"
    except Exception:
        return "Disabled"

def get_zram1_backend() -> str:
    """Returns 'Ext4', 'Tmpfs', or 'Disabled' based on active mount topology."""
    try:
        source = run_cmd(["findmnt", "-rn", "-o", "SOURCE", "--mountpoint", "/mnt/zram1"], check=False)
        fstype = run_cmd(["findmnt", "-rn", "-o", "FSTYPE", "--mountpoint", "/mnt/zram1"], check=False)
        if source in ("/dev/zram1", "zram1") or source.startswith("/dev/zram1") or fstype == "ext4":
            return "Ext4"
        elif source == "tmpfs" or fstype == "tmpfs":
            return "Tmpfs"
        return "Disabled"
    except Exception:
        return "Disabled"

def get_zram1_size() -> str:
    if TMPFS_MOUNT_UNIT.exists():
        match = re.search(r"size=([0-9]+%|[0-9]+[gmkGMKbB]*)", TMPFS_MOUNT_UNIT.read_text())
        if match:
            return match.group(1)
    for conf in [ZRAM1_CONF] + LEGACY_ZRAM1_CONFS:
        if conf.exists():
            match = re.search(r"zram-size\s*=\s*(.+)", conf.read_text())
            if match:
                raw = match.group(1).strip()
                m = re.search(r"ram\s*\*\s*([0-9.]+)", raw)
                if m:
                    return f"{int(round(float(m.group(1)) * 100))}%"
                if raw == "ram":
                    return "100%"
                return format_clean_size(raw)
    return "200%"

def get_disk_swap_status() -> str:
    try:
        swaps = Path("/proc/swaps").read_text() if Path("/proc/swaps").exists() else ""
        if "/swap/swapfile" in swaps or "swapfile" in swaps:
            if SWAPFILE_PATH.exists():
                size_gb = SWAPFILE_PATH.stat().st_size / (1024**3)
                return f"{size_gb:.1f} GB" if not size_gb.is_integer() else f"{size_gb:.0f} GB"
            return "Active"
        return "Disabled"
    except Exception:
        return "Disabled"

def get_disk_swap_size() -> str:
    if SWAPFILE_PATH.exists():
        try:
            size_bytes = SWAPFILE_PATH.stat().st_size
            gb = size_bytes / (1024**3)
            if gb >= 1.0:
                return f"{gb:.0f} GB" if gb.is_integer() else f"{gb:.1f} GB"
            mb = size_bytes / (1024**2)
            return f"{mb:.0f} MB"
        except Exception:
            pass
    return "4 GB"

def get_proactive_swap_status() -> str:
    try:
        res = run_cmd(["systemctl", "is-active", "dusky_pro_active_zram_swap.timer"], check=False)
        if res == "active":
            return "Active (Timer Scheduled)"
        srv = run_cmd(["systemctl", "is-active", "dusky_pro_active_zram_swap.service"], check=False)
        if srv == "active":
            return "Active (Running)"
        return "Disabled"
    except Exception:
        return "Disabled"

def print_full_status() -> None:
    backend = get_zram1_backend()
    backend_label = f" ({backend})" if backend != "Disabled" else ""
    print(f"\n{C.BOLD}=== DUSKY MEMORY & SWAP TOPOLOGY STATUS ==={C.RST}\n")
    print(f"  {C.CYN}• ZRAM0 (Compressed Swap):{C.RST}     {get_zram0_status()} [Configured: {get_zram0_size()}]")
    print(f"  {C.CYN}• ZRAM1 (/mnt/zram1{backend_label}):{C.RST}    {get_zram1_status()}")
    print(f"  {C.CYN}• Disk Swap (/swap/swapfile):{C.RST}  {get_disk_swap_status()}")
    print(f"  {C.CYN}• Proactive ZRAM Swap:{C.RST}       {get_proactive_swap_status()}\n")
    
    if Path("/proc/swaps").exists():
        print(f"{C.BOLD}[ Active Kernel Swaps (/proc/swaps) ]{C.RST}")
        print(Path("/proc/swaps").read_text().strip())
        print()

# =============================================================================
# ZRAM0 CONFIGURATION
# =============================================================================

def ensure_zram_device(dev_name: str = "zram0") -> None:
    dev_path = Path(f"/dev/{dev_name}")
    if not dev_path.exists():
        hot_add = Path("/sys/class/zram-control/hot_add")
        if hot_add.exists():
            try:
                hot_add.read_text()
            except Exception:
                pass
        if not dev_path.exists():
            run_cmd(["modprobe", "zram"], check=False)

def parse_size_input(raw: str) -> str:
    val = raw.strip()
    total_ram = get_total_ram_gb()
    default_expr = "ram * 1.5" if total_ram < 28.0 else "ram / 2"
    if not val:
        return default_expr
    
    val_clean = val.lower().replace(" ", "").replace("gib", "g").replace("mib", "m").replace("kib", "k")
    
    if val_clean in ("auto", "default"):
        return default_expr

    # Handle percentage: "80%" -> "ram * 0.8"
    if val_clean.endswith("%"):
        try:
            pct = float(val_clean[:-1])
            if pct == 100.0:
                return "ram"
            return f"ram * {pct / 100.0:.2f}".rstrip("0").rstrip(".")
        except ValueError:
            pass
            
    # Handle pure integers from 1 to 100: assume percentage (e.g. 80 -> ram * 0.8)
    try:
        n = float(val_clean)
        if 0.0 < n <= 2.0:
            return f"ram * {n:.2f}".rstrip("0").rstrip(".")
        elif 3.0 <= n <= 100.0 and n.is_integer():
            pct = n / 100.0
            return "ram" if pct == 1.0 else f"ram * {pct:.2f}".rstrip("0").rstrip(".")
    except ValueError:
        pass

    # Handle explicit GB/MB fixed sizes (e.g. "4g", "4gb", "2048m", "512mb") -> convert to MiB integer for zram-generator
    m_size = re.match(r"^([0-9.]+)\s*([gmk]b?)$", val_clean)
    if m_size:
        num = float(m_size.group(1))
        unit = m_size.group(2)
        if unit.startswith("g"):
            return str(int(num * 1024))
        elif unit.startswith("m"):
            return str(int(num))
        elif unit.startswith("k"):
            return str(int(num / 1024))

    # Format standard expressions cleanly (e.g. ram * 0.5)
    val_formatted = re.sub(r"\s*([*/+-])\s*", r" \1 ", val_clean)
    return val_formatted


def parse_tmpfs_size_expression(raw: str, default: str = "200%") -> str:
    s = raw.strip().lower().replace("gib", "g").replace("mib", "m").replace("kib", "k").replace("x", "")
    if not s or s in ("auto", "default"):
        return default
    if s.endswith("%") or s.endswith("g") or s.endswith("m") or s.endswith("k"):
        return s
    if s == "ram":
        return "100%"
    elif s in ("ram * 2", "ram*2", "2"):
        return "200%"
    elif s in ("ram / 2", "ram/2", "0.5"):
        return "50%"
    elif s in ("ram / 4", "ram/4", "0.25"):
        return "25%"
    elif s.startswith("ram * ") or s.startswith("ram*"):
        try:
            val = float(s.replace("ram * ", "").replace("ram*", "").strip())
            return f"{int(round(val * 100))}%"
        except ValueError:
            pass
    elif s.startswith("ram / ") or s.startswith("ram/"):
        try:
            val = float(s.replace("ram / ", "").replace("ram/", "").strip())
            if val > 0:
                return f"{int(round((1.0 / val) * 100))}%"
        except ValueError:
            pass
    try:
        f = float(s)
        if 0.0 < f <= 1.0:
            return f"{int(round(f * 100))}%"
        elif 1.0 < f <= 10.0:
            return f"{int(round(f * 100))}%"
        elif 10.0 < f <= 1000.0 and f.is_integer():
            return f"{int(f)}%"
    except ValueError:
        pass
    return default


def set_zram0_size(size_raw: str) -> None:
    escalate_root_if_needed()
    size_expr = parse_size_input(size_raw)
    info(f"Configuring ZRAM0 size expression: {C.BOLD}{size_expr}{C.RST}")

    # Determine intelligent resident limit (aligned with commit 37fe210e / 205_zram_configuration.sh)
    # Unlimited resident cap (0) allows dynamic zRAM compression without artificial early choking
    res_limit = "0"

    ZRAM_CONF_DIR.mkdir(parents=True, exist_ok=True)
    content = f"""# Managed by Dusky Memory & Swap Manager
[zram0]
zram-size = {size_expr}
zram-resident-limit = {res_limit}
compression-algorithm = zstd(level=2)
swap-priority = 32767
options = discard
"""
    write_file_atomic(ZRAM0_CONF, content)
    for legacy in LEGACY_ZRAM0_CONFS:
        legacy.unlink(missing_ok=True)
    
    # 1. Stop existing swap and setup units
    run_cmd(["swapoff", "/dev/zram0"], check=False)
    run_cmd(["systemctl", "stop", "dev-zram0.swap"], check=False)
    run_cmd(["systemctl", "stop", "systemd-zram-setup@zram0.service"], check=False)
    if Path("/sys/block/zram0/reset").exists():
        try:
            Path("/sys/block/zram0/reset").write_text("1")
        except Exception:
            pass
    run_cmd(["zramctl", "--reset", "/dev/zram0"], check=False)
    
    # 2. Ensure device exists and reload systemd daemon
    ensure_zram_device("zram0")
    run_cmd(["systemctl", "daemon-reload"])
    
    # 3. Bring up new zram device and activate swap
    run_cmd(["systemctl", "restart", "systemd-zram-setup@zram0.service"], check=False)
    run_cmd(["systemctl", "restart", "dev-zram0.swap"], check=False)
    run_cmd(["swapon", "-p", "32767", "/dev/zram0"], check=False)
    
    fix_mount_permissions()
    ok(f"ZRAM0 configured successfully to: {size_expr} (Priority: 32767)")
    notify("ZRAM0 Updated", f"ZRAM0 swap configured to {size_expr} @ Max Priority 32767")

def disable_zram0() -> None:
    escalate_root_if_needed()
    info("Disabling ZRAM0 compressed swap...")
    run_cmd(["swapoff", "/dev/zram0"], check=False)
    run_cmd(["systemctl", "stop", "dev-zram0.swap"], check=False)
    run_cmd(["systemctl", "stop", "systemd-zram-setup@zram0.service"], check=False)
    if Path("/sys/block/zram0/reset").exists():
        try:
            Path("/sys/block/zram0/reset").write_text("1")
        except Exception:
            pass
    run_cmd(["zramctl", "--reset", "/dev/zram0"], check=False)
    if ZRAM0_CONF.exists():
        ZRAM0_CONF.unlink()
    for legacy in LEGACY_ZRAM0_CONFS:
        legacy.unlink(missing_ok=True)
    run_cmd(["systemctl", "daemon-reload"])
    ok("ZRAM0 compressed swap disabled completely.")
    notify("ZRAM0 Disabled", "ZRAM0 swap has been disabled (Zero RAM table overhead).")

def enable_zram0() -> None:
    escalate_root_if_needed()
    set_zram0_size("auto")

# =============================================================================
# ZRAM1 CONFIGURATION & DATA MIGRATION ENGINE
# =============================================================================

def safely_unmount_and_stage(target_backend: str, mount_point: Path = MOUNT_POINT) -> Path | None:
    """
    Intelligently unmounts mount_point and stages files for seamless migration:
    - Releasing active processes holding the mountpoint (SIGTERM -> SIGKILL) to quiesce filesystem.
    - Syncs dirty buffers to prevent data loss.
    - Accurately checks allocated disk block size (sparse-aware).
    - Stages files using rsync with sparse and metadata preservation (fallback to cp -a).
    - Cleans systemd failed states and performs unmount with lazy unmount fallback.
    """
    stage_dir: Path | None = None

    src = run_cmd(["findmnt", "-rn", "-o", "SOURCE", "--mountpoint", str(mount_point)], check=False)
    if src:
        # 1. Quiesce filesystem by releasing holding processes
        info(f"Releasing active processes holding {mount_point}...")
        if shutil.which("fuser"):
            subprocess.run(["fuser", "-km", "-TERM", str(mount_point)], capture_output=True, check=False)
            time.sleep(0.3)
            subprocess.run(["fuser", "-km", "-KILL", str(mount_point)], capture_output=True, check=False)
            time.sleep(0.2)

        try:
            subprocess.run(["sync", "-f", str(mount_point)], capture_output=True, check=False)
        except Exception:
            pass

        # 2. Check for user files to migrate
        try:
            items = [p for p in mount_point.iterdir() if p.name not in ("lost+found", ".Trash-1000")]
            if items:
                info(f"Detected {len(items)} file(s)/folder(s) on {mount_point}. Staging for migration...")
                # Get accurate allocated disk usage in bytes
                du_out = run_cmd(["du", "-s", "-B1", "--exclude=lost+found", "--exclude=.Trash-1000", str(mount_point)], check=False)
                allocated_bytes = int(du_out.split()[0]) if du_out and du_out.split()[0].isdigit() else 1024 * 1024 * 1024

                candidate_dirs = [Path("/var/tmp"), Path("/tmp")]
                for cand in candidate_dirs:
                    try:
                        st = os.statvfs(str(cand))
                        free_bytes = st.f_bavail * st.f_frsize
                        if free_bytes > (allocated_bytes + 1024 * 1024 * 200):  # +200MB margin
                            s_dir = cand / f".zram1_migration_{os.getpid()}_{int(time.time())}"
                            s_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
                            
                            # Prefer rsync with sparse and full Unix metadata preservation
                            if shutil.which("rsync"):
                                res = subprocess.run(
                                    ["rsync", "-aHAX", "--sparse", "--exclude=lost+found", "--exclude=.Trash-1000", f"{mount_point}/", f"{s_dir}/"],
                                    capture_output=True, text=True, check=False
                                )
                                if res.returncode == 0:
                                    stage_dir = s_dir
                            
                            # Fallback to cp -a --sparse=always if rsync not available or errored
                            if not stage_dir:
                                for it in items:
                                    if it.is_dir():
                                        shutil.copytree(it, s_dir / it.name, symlinks=True, dirs_exist_ok=True)
                                    else:
                                        subprocess.run(["cp", "-a", "--sparse=always", str(it), str(s_dir / it.name)], capture_output=True, check=False)
                                stage_dir = s_dir

                            if stage_dir:
                                ok(f"Successfully staged {len(items)} item(s) to {stage_dir}.")
                                break
                    except Exception as e:
                        warn(f"Staging to {cand} failed: {e}. Trying next candidate...")
                
                if not stage_dir:
                    warn("Could not stage files (insufficient space or permission). Discarding files as requested.")
        except Exception as e:
            warn(f"Error inspecting files in {mount_point}: {e}. Proceeding with discard.")

        # 3. Stop systemd mount / generator services
        run_cmd(["systemctl", "stop", "mnt-zram1.mount"], check=False)
        run_cmd(["systemctl", "stop", "systemd-zram-setup@zram1.service"], check=False)

        # 4. Standard unmount -> force lazy unmount fallback
        run_cmd(["umount", "-q", str(mount_point)], check=False)
        cur_src = run_cmd(["findmnt", "-rn", "-o", "SOURCE", "--mountpoint", str(mount_point)], check=False)
        if cur_src:
            warn(f"{mount_point} still busy. Performing lazy unmount (umount -f -l)...")
            run_cmd(["umount", "-f", "-l", str(mount_point)], check=False)
            time.sleep(0.3)

    # Reset zram1 block device if existing and clear systemd failure states
    run_cmd(["zramctl", "--reset", "/dev/zram1"], check=False)
    run_cmd(["systemctl", "reset-failed", "mnt-zram1.mount", "systemd-zram-setup@zram1.service"], check=False)
    return stage_dir


def restore_staged_files(stage_dir: Path | None, mount_point: Path = MOUNT_POINT) -> None:
    """Restores previously staged files back to mount_point after the new filesystem is mounted."""
    if not stage_dir or not stage_dir.exists():
        return
    try:
        info(f"Restoring migrated data to {mount_point}...")
        if shutil.which("rsync"):
            res = subprocess.run(
                ["rsync", "-aHAX", "--sparse", f"{stage_dir}/", f"{mount_point}/"],
                capture_output=True, text=True, check=False
            )
            if res.returncode == 0:
                shutil.rmtree(stage_dir, ignore_errors=True)
                ok(f"Restored migrated data to {mount_point} successfully.")
                return
        
        # Fallback copy
        items = list(stage_dir.iterdir())
        for it in items:
            dest = mount_point / it.name
            if it.is_dir():
                shutil.copytree(it, dest, symlinks=True, dirs_exist_ok=True)
            else:
                subprocess.run(["cp", "-a", "--sparse=always", str(it), str(dest)], capture_output=True, check=False)
        shutil.rmtree(stage_dir, ignore_errors=True)
        ok(f"Restored {len(items)} item(s) to {mount_point} successfully.")
    except Exception as e:
        warn(f"Failed to restore some files from {stage_dir}: {e}")


def set_zram1_tmpfs(size_raw: str = "") -> None:
    """Configures high-performance Tmpfs mount on /mnt/zram1 with the specified size ceiling."""
    escalate_root_if_needed()
    raw = size_raw or get_zram1_size() or "200%"
    size_expr = parse_tmpfs_size_expression(raw)
    info(f"Configuring /mnt/zram1 Tmpfs size ceiling: {C.BOLD}{size_expr}{C.RST}")

    stage_dir = safely_unmount_and_stage("tmpfs")

    # Purge any block device generator configs and overrides
    for conf in [ZRAM1_CONF] + LEGACY_ZRAM1_CONFS:
        conf.unlink(missing_ok=True)
    if OVERRIDE_DIR.exists():
        shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
    if LEGACY_MAKEFS_OVERRIDE_DIR.exists():
        shutil.rmtree(LEGACY_MAKEFS_OVERRIDE_DIR, ignore_errors=True)
    if LEGACY_PERMS_SERVICE.exists():
        LEGACY_PERMS_SERVICE.unlink(missing_ok=True)
    if LEGACY_PERMS_WANTS.exists() or LEGACY_PERMS_WANTS.is_symlink():
        LEGACY_PERMS_WANTS.unlink(missing_ok=True)
    if LEGACY_PERMS_WANTS_DIR.exists():
        shutil.rmtree(LEGACY_PERMS_WANTS_DIR, ignore_errors=True)
    if Path("/sys/block/zram1/reset").exists():
        try:
            Path("/sys/block/zram1/reset").write_text("1")
        except Exception:
            pass
    run_cmd(["zramctl", "--reset", "/dev/zram1"], check=False)

    tmpfs_content = f"""# Managed by Dusky Memory & Swap Manager
[Unit]
Description=High-Performance Native tmpfs on /mnt/zram1
Before=local-fs.target
ConditionPathExists=/mnt/zram1

[Mount]
What=tmpfs
Where=/mnt/zram1
Type=tmpfs
Options=rw,nosuid,nodev,noatime,size={size_expr},mode=1777

[Install]
WantedBy=local-fs.target
"""
    write_file_atomic(TMPFS_MOUNT_UNIT, tmpfs_content)
    run_cmd(["systemctl", "daemon-reload"])
    run_cmd(["systemctl", "enable", "--now", "mnt-zram1.mount"], check=False)

    # Verify mount with direct fallback
    for _ in range(8):
        if run_cmd(["findmnt", "-rn", "-o", "SOURCE", "--mountpoint", "/mnt/zram1"], check=False) == "tmpfs":
            break
        time.sleep(0.3)

    if run_cmd(["findmnt", "-rn", "-o", "SOURCE", "--mountpoint", "/mnt/zram1"], check=False) != "tmpfs":
        run_cmd(["mount", "-t", "tmpfs", "-o", f"rw,nosuid,nodev,noatime,size={size_expr},mode=1777", "tmpfs", "/mnt/zram1"], check=False)

    fix_mount_permissions()
    restore_staged_files(stage_dir)
    fix_mount_permissions()
    ok(f"/mnt/zram1 configured as Native Tmpfs RAM disk (Ceiling: {size_expr}).")
    notify("Tmpfs Attached", f"/mnt/zram1 configured as Native Tmpfs RAM disk ({size_expr}).")


def set_zram1_ext4(size_raw: str = "") -> None:
    """Configures Ext4 compressed ZRAM block device on /mnt/zram1."""
    escalate_root_if_needed()
    raw = size_raw or get_zram1_size() or "100%"
    size_expr = parse_size_input(raw)
    info(f"Configuring ZRAM1 Ext4 disk size expression: {C.BOLD}{size_expr}{C.RST}")

    stage_dir = safely_unmount_and_stage("ext4")

    # If tmpfs mount unit was enabled, disable and remove it cleanly
    if TMPFS_MOUNT_UNIT.exists():
        run_cmd(["systemctl", "disable", "--now", "mnt-zram1.mount"], check=False)
        TMPFS_MOUNT_UNIT.unlink(missing_ok=True)

    for conf in [ZRAM1_CONF] + LEGACY_ZRAM1_CONFS:
        conf.unlink(missing_ok=True)

    content = f"""# Managed by Dusky Memory & Swap Manager
[zram1]
zram-size = {size_expr}
zram-resident-limit = ram * 4 / 5
fs-type = ext4
mount-point = /mnt/zram1
compression-algorithm = zstd(level=2)
options = rw,nosuid,nodev,discard,noatime,lazytime,X-mount.mode=1777
"""
    write_file_atomic(ZRAM1_CONF, content)
    fix_mount_permissions()

    ensure_zram_device("zram1")
    run_cmd(["systemctl", "daemon-reload"])
    run_cmd(["systemctl", "restart", "systemd-zram-setup@zram1.service"], check=False)
    run_cmd(["systemctl", "restart", "mnt-zram1.mount"], check=False)
    run_cmd(["mount", "/mnt/zram1"], check=False)

    # Wait for mount
    for _ in range(10):
        src = run_cmd(["findmnt", "-rn", "-o", "SOURCE", "--mountpoint", "/mnt/zram1"], check=False)
        if src in ("/dev/zram1", "zram1") or src.startswith("/dev/zram1"):
            break
        time.sleep(0.3)

    fix_mount_permissions()
    restore_staged_files(stage_dir)
    fix_mount_permissions()
    ok(f"/mnt/zram1 configured as Ext4 Compressed RAM Block (Size: {size_expr}).")
    notify("ZRAM1 Attached", f"/mnt/zram1 configured as Ext4 ZRAM Block device ({size_expr}).")


def set_zram1_size(size_raw: str) -> None:
    """Updates the size of /mnt/zram1 according to its active backend."""
    current_backend = get_zram1_backend()
    if current_backend == "Ext4":
        set_zram1_ext4(size_raw)
    else:
        set_zram1_tmpfs(size_raw)


def set_zram1_backend(backend: str) -> None:
    """Switches the /mnt/zram1 backend mode (Ext4 ZRAM vs Native Tmpfs vs Disabled)."""
    escalate_root_if_needed()
    mode = backend.lower().strip()
    match mode:
        case "zram" | "ext4":
            info("Switching /mnt/zram1 to Ext4 ZRAM Block Device...")
            current_size = get_zram1_size()
            set_zram1_ext4(current_size)
        case "tmpfs" | "ram":
            info("Switching /mnt/zram1 to Pure Tmpfs RAM Mount...")
            current_size = get_zram1_size()
            set_zram1_tmpfs(current_size)
        case "disable" | "disabled" | "none" | "off":
            disable_zram1()
        case _:
            die(f"Invalid backend: '{backend}'. Choose 'ext4', 'tmpfs', or 'disable'.")


def enable_zram1(size_raw: str = "") -> None:
    """Autonomously enables RAM disk on /mnt/zram1."""
    escalate_root_if_needed()
    current_backend = get_zram1_backend()
    if current_backend == "Ext4":
        set_zram1_ext4(size_raw)
    else:
        size = size_raw or get_zram1_size() or "200%"
        set_zram1_tmpfs(size)


def disable_zram1() -> None:
    """Safely unmounts and dismantles /mnt/zram1."""
    escalate_root_if_needed()
    info("Dismantling /mnt/zram1 RAM disk...")
    safely_unmount_and_stage("none")
    for leg in [ZRAM1_CONF] + LEGACY_ZRAM1_CONFS:
        leg.unlink(missing_ok=True)
    if TMPFS_MOUNT_UNIT.exists():
        run_cmd(["systemctl", "disable", "--now", "mnt-zram1.mount"], check=False)
        TMPFS_MOUNT_UNIT.unlink(missing_ok=True)
    if OVERRIDE_DIR.exists():
        shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
    if LEGACY_MAKEFS_OVERRIDE_DIR.exists():
        shutil.rmtree(LEGACY_MAKEFS_OVERRIDE_DIR, ignore_errors=True)
    if LEGACY_PERMS_SERVICE.exists():
        LEGACY_PERMS_SERVICE.unlink(missing_ok=True)
    if LEGACY_PERMS_WANTS.exists() or LEGACY_PERMS_WANTS.is_symlink():
        LEGACY_PERMS_WANTS.unlink(missing_ok=True)
    if LEGACY_PERMS_WANTS_DIR.exists():
        shutil.rmtree(LEGACY_PERMS_WANTS_DIR, ignore_errors=True)
    if Path("/sys/block/zram1/reset").exists():
        try:
            Path("/sys/block/zram1/reset").write_text("1")
        except Exception:
            pass
    run_cmd(["zramctl", "--reset", "/dev/zram1"], check=False)
    run_cmd(["systemctl", "daemon-reload"])
    ok("/mnt/zram1 disabled cleanly (zero memory overhead).")
    notify("ZRAM1 Disabled", "RAM disk /mnt/zram1 has been dismantled.")

# =============================================================================
# DISK SWAP CONFIGURATION (/swap/swapfile)
# =============================================================================

def parse_disk_swap_bytes(raw: str) -> int:
    s = raw.strip().upper()
    match = re.match(r"^([0-9.]+)\s*([KMGT]I?B?|B)?$", s)
    if not match:
        die(f"Invalid disk swap size format: '{raw}'. Examples: '4G', '8G', '512M'.")
    
    val = float(match.group(1))
    if val <= 0:
        die(f"Invalid disk swap size: '{raw}'. Size must be greater than 0.")
        
    unit = match.group(2) or ""
    
    if unit in ("G", "GB", "GIB"):
        return int(val * 1024 * 1024 * 1024)
    elif unit in ("M", "MB", "MIB"):
        return int(val * 1024 * 1024)
    elif unit in ("K", "KB", "KIB"):
        return int(val * 1024)
    elif unit in ("T", "TB", "TIB"):
        return int(val * 1024 * 1024 * 1024 * 1024)
    else:
        return int(val * 1024 * 1024 * 1024) if val < 128 else int(val * 1024 * 1024)

def set_disk_swap_size(size_raw: str) -> None:
    escalate_root_if_needed()
    target_bytes = parse_disk_swap_bytes(size_raw)
    size_mb = int(target_bytes / (1024 * 1024))
    info(f"Allocating disk swapfile of {size_mb} MB at {SWAPFILE_PATH} (Priority: -1)...")

    # 1. Turn off active swapfile
    run_cmd(["swapoff", str(SWAPFILE_PATH)], check=False)

    # 2. Ensure parent directory exists
    SWAPFILE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 3. Detect filesystem type for BTRFS vs Ext4/XFS
    fs_type = run_cmd(["findmnt", "-rn", "-o", "FSTYPE", "-T", str(SWAPFILE_PATH.parent)], check=False)
    
    if SWAPFILE_PATH.exists():
        SWAPFILE_PATH.unlink()

    if fs_type == "btrfs":
        info("BTRFS filesystem detected: Using native btrfs mkswapfile...")
        run_cmd(["btrfs", "filesystem", "mkswapfile", "-s", f"{size_mb}M", str(SWAPFILE_PATH)], check=False)
        if not SWAPFILE_PATH.exists() or SWAPFILE_PATH.stat().st_size == 0:
            # Fallback
            run_cmd(["touch", str(SWAPFILE_PATH)])
            run_cmd(["chattr", "+C", str(SWAPFILE_PATH)], check=False)
            run_cmd(["dd", "if=/dev/zero", f"of={SWAPFILE_PATH}", "bs=1M", f"count={size_mb}", "status=none"])
            run_cmd(["chmod", "0600", str(SWAPFILE_PATH)])
            run_cmd(["mkswap", "-U", "clear", str(SWAPFILE_PATH)])
    else:
        info(f"{fs_type or 'Standard'} filesystem detected: Allocating swapfile...")
        try:
            run_cmd(["fallocate", "-l", f"{size_mb}M", str(SWAPFILE_PATH)])
        except Exception:
            run_cmd(["dd", "if=/dev/zero", f"of={SWAPFILE_PATH}", "bs=1M", f"count={size_mb}", "status=none"])
        run_cmd(["chmod", "0600", str(SWAPFILE_PATH)])
        run_cmd(["mkswap", "-U", "clear", str(SWAPFILE_PATH)])

    # 4. Activate with lowest priority (-1)
    run_cmd(["swapon", "-p", "-1", str(SWAPFILE_PATH)])

    # 5. Update /etc/fstab atomically
    if FSTAB_PATH.exists():
        lines = FSTAB_PATH.read_text().splitlines()
        new_lines = []
        found = False
        for line in lines:
            if re.search(r"^\s*#?\s*/swap/swapfile\b", line):
                new_lines.append(f"{SWAPFILE_PATH} none swap defaults,pri=-1 0 0")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{SWAPFILE_PATH} none swap defaults,pri=-1 0 0")
        write_file_atomic(FSTAB_PATH, "\n".join(new_lines) + "\n")

    ok(f"Disk swap active at {SWAPFILE_PATH} ({size_mb} MB, Priority: -1).")
    notify("Disk Swap Allocated", f"Disk swapfile created: {size_mb} MB @ Lowest Priority (-1)")

def disable_disk_swap() -> None:
    escalate_root_if_needed()
    info("Disabling disk swapfile...")
    run_cmd(["swapoff", str(SWAPFILE_PATH)], check=False)
    
    if FSTAB_PATH.exists():
        lines = FSTAB_PATH.read_text().splitlines()
        new_lines = []
        for line in lines:
            if re.search(r"^\s*/swap/swapfile\b", line):
                new_lines.append(f"# {line.strip()}")
            else:
                new_lines.append(line)
        write_file_atomic(FSTAB_PATH, "\n".join(new_lines) + "\n")
    
    ok("Disk swap disabled and commented out in /etc/fstab.")
    notify("Disk Swap Disabled", "Disk swap has been turned off.")

def enable_disk_swap() -> None:
    escalate_root_if_needed()
    if not SWAPFILE_PATH.exists():
        set_disk_swap_size("4G")
        return
        
    run_cmd(["swapon", "-p", "-1", str(SWAPFILE_PATH)], check=False)
    if FSTAB_PATH.exists():
        lines = FSTAB_PATH.read_text().splitlines()
        new_lines = []
        for line in lines:
            if re.search(r"^\s*#\s*/swap/swapfile\b", line):
                new_lines.append(re.sub(r"^\s*#\s*", "", line))
            else:
                new_lines.append(line)
        write_file_atomic(FSTAB_PATH, "\n".join(new_lines) + "\n")
    ok("Disk swap enabled @ Priority -1.")
    notify("Disk Swap Enabled", "Disk swap activated @ Lowest Priority (-1).")

# =============================================================================
# CLI ENTRY POINT
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Elite ZRAM & Disk Swap Subsystem Manager")
    
    # Status Queries
    parser.add_argument("--status", action="store_true", help="Print complete status overview")
    parser.add_argument("--zram0-status", action="store_true", help="Print ZRAM0 status string")
    parser.add_argument("--zram0-size", action="store_true", help="Print ZRAM0 size expression")
    parser.add_argument("--zram1-status", action="store_true", help="Print ZRAM1 status string")
    parser.add_argument("--zram1-size", action="store_true", help="Print ZRAM1 size expression")
    parser.add_argument("--zram1-backend", action="store_true", help="Print ZRAM1 backend mode (Ext4, Tmpfs, or Disabled)")
    parser.add_argument("--disk-swap-status", action="store_true", help="Print Disk Swap status string")
    parser.add_argument("--disk-swap-size", action="store_true", help="Print Disk Swap size string")
    
    # Mutators
    parser.add_argument("--set-zram0-size", metavar="EXPR", help="Set ZRAM0 size expression (e.g. 'ram', '4G')")
    parser.add_argument("--disable-zram0", action="store_true", help="Disable ZRAM0 compressed swap completely")
    parser.add_argument("--enable-zram0", action="store_true", help="Enable ZRAM0 compressed swap")
    
    parser.add_argument("--enable-zram1", action="store_true", help="Autonomously deploy native high-performance Tmpfs on /mnt/zram1")
    parser.add_argument("--disable-zram1", action="store_true", help="Disable /mnt/zram1 Tmpfs mount")
    parser.add_argument("--set-zram1-size", metavar="EXPR", help="Set /mnt/zram1 Tmpfs size ceiling (e.g. '100%%', '50%%', '4G')")
    parser.add_argument("--set-zram1-backend", choices=["zram", "tmpfs", "disable", "disabled", "ext4", "ram", "none", "off"], help="Switch /mnt/zram1 backend mode")
    
    parser.add_argument("--set-disk-swap-size", metavar="SIZE", help="Resize/create disk swapfile (e.g. '4G', '8G')")
    parser.add_argument("--disable-disk-swap", action="store_true", help="Disable disk swapfile")
    parser.add_argument("--enable-disk-swap", action="store_true", help="Enable disk swapfile")
    parser.add_argument("--fix-permissions", action="store_true", help="Fix /mnt and /mnt/zram1 permissions and deploy tmpfiles rule")
    
    args = parser.parse_args()

    # Query Handlers
    if args.zram0_status:
        print(get_zram0_status())
        return
    if args.zram0_size:
        print(get_zram0_size())
        return
    if args.zram1_status:
        print(get_zram1_status())
        return
    if args.zram1_size:
        print(get_zram1_size())
        return
    if args.zram1_backend:
        print(get_zram1_backend())
        return
    if args.disk_swap_status:
        print(get_disk_swap_status())
        return
    if args.disk_swap_size:
        print(get_disk_swap_size())
        return
    if args.status:
        print_full_status()
        return

    # Mutation Handlers
    if args.fix_permissions:
        escalate_root_if_needed()
        fix_mount_permissions()
        ok("/mnt and /mnt/zram1 permissions and tmpfiles rules updated successfully.")
    elif args.set_zram0_size:
        set_zram0_size(args.set_zram0_size)
    elif args.disable_zram0:
        disable_zram0()
    elif args.enable_zram0:
        enable_zram0()
    elif args.enable_zram1:
        enable_zram1()
    elif args.set_zram1_size:
        set_zram1_size(args.set_zram1_size)
    elif args.set_zram1_backend:
        set_zram1_backend(args.set_zram1_backend)
    elif args.disable_zram1:
        disable_zram1()
    elif args.set_disk_swap_size:
        set_disk_swap_size(args.set_disk_swap_size)
    elif args.disable_disk_swap:
        disable_disk_swap()
    elif args.enable_disk_swap:
        enable_disk_swap()
    else:
        print_full_status()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.YLW}Operation cancelled by user.{C.RST}")
        sys.exit(130)
