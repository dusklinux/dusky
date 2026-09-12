#!/usr/bin/env python3
# =============================================================================
#  Dusky RAM Analyzer & Balloon Benchmark  -  v2.7 (Arch Linux Bleeding-Edge)
#  Target : Arch Linux (rolling) | Python 3.14+ | Linux 7.x | Textual 8.x+
#  Scope  : Interactive mouse-driven & keyboard-driven TUI for ZRAM / Memory
#           forensics and multi-category synthetic memory pressure ballooning:
#           - [1] Dormant Anon (cold swap candidate, direct MADV_PAGEOUT to ZRAM)
#           - [2] Active Anon (foreground persistent memory, periodic page touch)
#           - [3] Clean Page Cache (reclaimable file cache, disk-backed fsync)
#           - [4] Dirty Page Cache (unflushed disk writes, writeback testing)
#           - [5] Shmem / Tmpfs (/dev/shm shared memory)
# =============================================================================

import argparse
import atexit
import ctypes
import ctypes.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

try:
    import mmap
    from rich.markup import escape
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from textual import events, on
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import Button, Label, Static
except ImportError as exc:
    raise SystemExit(
        f"[fatal] missing dependency: {exc.name}\n"
        "        sudo pacman -S --needed python-textual python-rich"
    )

PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")
MIB = 1048576
MISSING = "\x00missing"      # sentinel: absent / unreadable
DENIED = "\x00denied"        # sentinel: exists but needs root

# C library bindings for kernel memory advice
libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
libc.madvise.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
libc.madvise.restype = ctypes.c_int

MADV_COLD = 20
MADV_PAGEOUT = 21


# ============================================================================
#  Dynamic Matugen Theme Compiler
# ============================================================================
def load_theme() -> dict[str, str]:
    """Loads the user's Matugen-generated theme with bulletproof fallback mechanisms."""
    path = Path.home() / ".config" / "matugen" / "generated" / "dusky_tui.json"
    defaults: dict[str, str] = {
        "bg": "#191113",
        "fg": "#efdfe1",
        "accent": "#ffb1c8",
        "error": "#ffb4ab",
        "warning": "#e3bdc6",
        "success": "#efbd94",
        "muted": "#514347",
    }
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                user_theme = json.load(f)
                return {k: str(user_theme.get(k, defaults[k])) for k in defaults}
        except Exception:
            return defaults
    return defaults


THEME = load_theme()
BG = THEME["bg"]
FG = THEME["fg"]
ACCENT = THEME["accent"]
ERROR = THEME["error"]
WARNING = THEME["warning"]
SUCCESS = THEME["success"]
MUTED = THEME["muted"]


# --------------------------------------------------------------------------- #
#  privilege escalation (opt-in via --root)                                   #
# --------------------------------------------------------------------------- #
def elevate() -> None:
    """Opt-in privilege escalation. Preserves full terminal color environment."""
    if os.geteuid() == 0:
        return
    sudo = shutil.which("sudo")
    if sudo is None:
        print("[warn] sudo not found; continuing unprivileged.", file=sys.stderr)
        return
    # Preserve environment variables via child env wrapper so COLORTERM is retained
    keep = [f"{k}={v}" for k in ("TERM", "COLORTERM", "TERMINFO", "LANG", "PATH")
            if (v := os.environ.get(k))]
    argv = [sudo, "--", "/usr/bin/env", *keep,
            sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]]
    try:
        os.execv(sudo, argv)
    except OSError as exc:
        print(f"[warn] escalation failed ({exc}); continuing unprivileged.", file=sys.stderr)


# --------------------------------------------------------------------------- #
#  low level readers & formatters                                             #
# --------------------------------------------------------------------------- #
def read_str(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except PermissionError:
        return DENIED
    except OSError:
        return MISSING


def read_int(path: str) -> int | None:
    try:
        val = read_str(path)
        return int(val) if val not in (MISSING, DENIED) else None
    except (ValueError, TypeError):
        return None


def read_selected(path: str) -> str:
    """'lzo [zstd] lz4' -> 'zstd'. Strips Rich brackets."""
    raw = read_str(path)
    if raw in (MISSING, DENIED):
        return raw
    m = re.search(r"\[([^\]]+)\]", raw)
    return m.group(1) if m else raw


def fmt_bytes(num: float | int | None) -> str:
    if num is None:
        return "n/a"
    val = float(num)
    for suffix in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(val) < 1024.0:
            return f"{val:.0f} {suffix}" if suffix == "B" else f"{val:.2f} {suffix}"
        val /= 1024.0
    return f"{val:.2f} PiB"


def plain(value: str) -> str:
    if value == MISSING:
        return "n/a"
    if value == DENIED:
        return "root"
    return escape(value)


def cell(value: str, style: str = "") -> str:
    if value == MISSING:
        return "[dim]n/a[/dim]"
    if value == DENIED:
        return "[dim]root only[/dim]"
    text = escape(value)
    return f"[{style}]{text}[/{style}]" if style else text


def bar(fraction: float, width: int = 14) -> str:
    fraction = min(max(fraction, 0.0), 1.0)
    filled = int(round(fraction * width))
    colour = SUCCESS if fraction < 0.70 else WARNING if fraction < 0.90 else ERROR
    return f"[{colour}]{'#' * filled}[/{colour}][{MUTED}]{'-' * (width - filled)}[/{MUTED}]"


def new_table() -> Table:
    table = Table(expand=True, box=None, show_header=False, pad_edge=False)
    table.add_column("k", style=f"bold {FG}", ratio=1)
    table.add_column("v", justify="right", style=f"{SUCCESS}", ratio=1, no_wrap=True)
    return table


# --------------------------------------------------------------------------- #
#  collectors & parsers                                                       #
# --------------------------------------------------------------------------- #
def get_meminfo() -> dict[str, int]:
    """All values in bytes. Unitless counters (HugePages_*) stay raw counts."""
    data: dict[str, int] = {}
    try:
        text = Path("/proc/meminfo").read_text()
    except OSError:
        return data
    for line in text.splitlines():
        key, sep, rest = line.partition(":")
        if not sep:
            continue
        fields = rest.split()
        if not fields:
            continue
        try:
            val = int(fields[0])
        except ValueError:
            continue
        data[key] = val * 1024 if fields[-1] == "kB" else val
    return data


def get_swaps() -> dict[str, int]:
    stats = {"zram_total": 0, "zram_used": 0, "zram_devs": 0,
             "disk_total": 0, "disk_used": 0, "disk_devs": 0}
    try:
        lines = Path("/proc/swaps").read_text().splitlines()[1:]
    except OSError:
        return stats
    for line in lines:
        fields = line.rsplit(None, 4)
        if len(fields) != 5:
            continue
        try:
            size = int(fields[2]) * 1024
            used = int(fields[3]) * 1024
        except ValueError:
            continue
        prefix = "zram" if fields[0].startswith("/dev/zram") else "disk"
        stats[f"{prefix}_total"] += size
        stats[f"{prefix}_used"] += used
        stats[f"{prefix}_devs"] += 1
    return stats


def get_mount_map() -> dict[str, str]:
    mounts: dict[str, str] = {}
    try:
        for line in Path("/proc/mounts").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith("/dev/zram"):
                mounts[parts[0]] = parts[1]
    except OSError:
        pass
    return mounts


def get_zram_devices() -> list[Path]:
    try:
        devices = [p for p in Path("/sys/block").glob("zram[0-9]*")
                   if (p / "mm_stat").is_file()]
    except OSError:
        return []
    return sorted(devices, key=lambda p: int(p.name[4:]))


def get_zram() -> dict:
    """Detailed statistics per ZRAM device plus aggregated totals."""
    devices_data: list[dict] = []
    mount_map = get_mount_map()
    swaps_text = read_str("/proc/swaps")

    agg = {
        "devices": devices_data,
        "count": 0,
        "orig_total": 0,
        "compr_total": 0,
        "used_total": 0,
        "used_max_total": 0,
        "disksize_total": 0,
        "same_total": 0,
        "huge_total": 0,
    }

    for dev in get_zram_devices():
        raw_stat = read_str(str(dev / "mm_stat"))
        if raw_stat in (MISSING, DENIED):
            continue
        try:
            vals = [int(x) for x in raw_stat.split()]
        except ValueError:
            continue
        vals += [0] * (9 - len(vals))

        dev_path = f"/dev/{dev.name}"
        role = mount_map.get(dev_path)
        if not role:
            if dev_path in swaps_text:
                role = "[SWAP]"
            else:
                role = "[idle/unmounted]"

        disksize = read_int(str(dev / "disksize")) or 0
        algo = read_selected(str(dev / "comp_algorithm"))

        d_orig = vals[0]
        d_compr = vals[1]
        d_used = vals[2]
        d_limit = vals[3]
        d_max = vals[4]
        d_same = vals[5] * PAGE_SIZE
        d_compacted = vals[6] * PAGE_SIZE
        d_huge = vals[7] * PAGE_SIZE

        codec_ratio = (d_orig / d_compr) if d_compr > 0 else 0.0
        eff_ratio = (d_orig / d_used) if d_used > 0 else 0.0
        saved = max(d_orig - d_used, 0)

        dev_info = {
            "name": dev.name,
            "role": role,
            "algo": algo,
            "disksize": disksize,
            "orig": d_orig,
            "compr": d_compr,
            "used": d_used,
            "used_max": d_max,
            "limit": d_limit,
            "same": d_same,
            "huge": d_huge,
            "compacted": d_compacted,
            "codec_ratio": codec_ratio,
            "eff_ratio": eff_ratio,
            "saved": saved,
        }
        devices_data.append(dev_info)

        agg["orig_total"] += d_orig
        agg["compr_total"] += d_compr
        agg["used_total"] += d_used
        agg["used_max_total"] += d_max
        agg["disksize_total"] += disksize
        agg["same_total"] += d_same
        agg["huge_total"] += d_huge

    agg["count"] = len(devices_data)
    agg["codec_ratio"] = (agg["orig_total"] / agg["compr_total"]) if agg["compr_total"] > 0 else 0.0
    agg["eff_ratio"] = (agg["orig_total"] / agg["used_total"]) if agg["used_total"] > 0 else 0.0
    agg["saved_total"] = max(agg["orig_total"] - agg["used_total"], 0)
    return agg


def get_psi() -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        text = Path("/proc/pressure/memory").read_text()
    except OSError:
        return out
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        for item in parts[1:]:
            key, sep, val = item.partition("=")
            if sep:
                try:
                    out[f"{parts[0]}.{key}"] = float(val)
                except ValueError:
                    pass
    return out


VMSTAT_KEYS = ("pswpin", "pswpout", "pgmajfault", "oom_kill")


def get_vmstat() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        text = Path("/proc/vmstat").read_text()
    except OSError:
        return out
    for line in text.splitlines():
        key, sep, val = line.partition(" ")
        if sep and key in VMSTAT_KEYS:
            try:
                out[key] = int(val)
            except ValueError:
                pass
    return out


class RateMeter:
    """Tracks per-second throughput rates of monotonic kernel counters."""

    def __init__(self) -> None:
        self._prev: dict[str, int] = {}
        self._stamp = time.monotonic()

    def update(self, sample: dict[str, int]) -> dict[str, float]:
        now = time.monotonic()
        span = now - self._stamp
        rates: dict[str, float] = {}
        if self._prev and span > 0.0:
            for key, val in sample.items():
                prev_val = self._prev.get(key)
                if prev_val is not None and val >= prev_val:
                    rates[key] = (val - prev_val) / span
        self._prev = sample
        self._stamp = now
        return rates


def scan_processes(limit: int = 7) -> list[tuple[int, int, int, str]]:
    """Inspects top memory consumers directly from /proc with zero external forks."""
    rows: list[tuple[int, int, int, str]] = []
    try:
        entries = list(os.scandir("/proc"))
    except OSError:
        return rows
    for entry in entries:
        if not entry.name.isdigit():
            continue
        name = ""
        rss = -1
        swap = 0
        try:
            with open(f"/proc/{entry.name}/status", "r") as handle:
                for line in handle:
                    if line.startswith("Name:"):
                        name = line.partition(":")[2].strip()
                    elif line.startswith("VmRSS:"):
                        rss = int(line.split()[1]) * 1024
                    elif line.startswith("VmSwap:"):
                        swap = int(line.split()[1]) * 1024
                        break
        except (OSError, ValueError):
            continue
        if rss < 0:
            continue
        rows.append((rss, swap, int(entry.name), name))
    rows.sort(reverse=True)
    return rows[:limit]


def collect() -> dict:
    """Coherent snapshot collected in a single tick."""
    return {
        "mem": get_meminfo(),
        "swaps": get_swaps(),
        "zram": get_zram(),
        "psi": get_psi(),
        "vmstat": get_vmstat(),
    }


# --------------------------------------------------------------------------- #
#  RAM Ballooning & Synthetic Stress Engine                                   #
# --------------------------------------------------------------------------- #
class BalloonManager:
    """Multi-category synthetic memory generator for empirical kernel testing:

    1. DORMANT ANON: Cold anonymous memory (MADV_COLD), prime swap candidate.
                     Forces immediately into ZRAM via MADV_PAGEOUT on demand.
    2. ACTIVE ANON:  Foreground persistent memory. Daemon thread touches 1 byte
                     per page every 1.5s to keep it young in MGLRU.
    3. CLEAN CACHE:  File-backed page cache (/var/tmp), fsynced clean.
                     Instantly dropped by the kernel under memory pressure.
    4. DIRTY CACHE:  File-backed unflushed writes (/var/tmp), periodically redirtied.
                     Tests background writeback (dirty_background_bytes).
    5. SHMEM / TMPFS:Shared memory (/dev/shm). Must be swapped out to ZRAM.
    """

    PATTERN = b"DUSKY_ZRAM_"

    def __init__(self, chunk_mb: int = 250) -> None:
        self.chunk_mb = chunk_mb

        self.dormant_blocks: list[mmap.mmap] = []
        self.active_blocks: list[bytearray] = []
        self.clean_files: list[tuple[tempfile._TemporaryFileWrapper, int]] = []
        self.dirty_files: list[tuple[tempfile._TemporaryFileWrapper, mmap.mmap]] = []
        # Store (path, file_obj, mmap_buf) for shmem to ensure robust unlinking
        self.shmem_files: list[tuple[str, object, mmap.mmap]] = []

        self._touch_running = True
        self._touch_thread = threading.Thread(target=self._touch_loop, daemon=True)
        self._touch_thread.start()
        atexit.register(self.cleanup)

    def _touch_loop(self) -> None:
        while self._touch_running:
            time.sleep(1.5)
            # Actively touch 1 byte per page of active anonymous blocks
            for block in list(self.active_blocks):
                try:
                    for off in range(0, len(block), 4096):
                        block[off] ^= 1
                except Exception:
                    pass
            # Periodically re-dirty dirty page cache blocks
            for _, buf in list(self.dirty_files):
                try:
                    for off in range(0, len(buf), 4096):
                        buf[off] = (buf[off] + 1) % 256
                except Exception:
                    pass

    def _template(self) -> bytes:
        """1 MiB block: 1/3 random entropy + 2/3 repeating pattern (~3:1 ratio)."""
        ent = MIB // 3
        body = MIB - ent
        filler = (self.PATTERN * (body // len(self.PATTERN) + 1))[:body]
        return os.urandom(ent) + filler

    # --- Add Chunks ---
    def add_dormant(self) -> bool:
        size = self.chunk_mb * MIB
        try:
            buf = mmap.mmap(-1, size, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
            tmpl = self._template()
            for off in range(0, size, MIB):
                buf.write(tmpl)
            addr = ctypes.addressof(ctypes.c_char.from_buffer(buf))
            libc.madvise(addr, size, MADV_COLD)
            self.dormant_blocks.append(buf)
            return True
        except (MemoryError, OSError):
            return False

    def add_active(self) -> bool:
        size = self.chunk_mb * MIB
        try:
            block = bytearray(size)
            tmpl = self._template()
            for off in range(0, size, MIB):
                block[off:off + MIB] = tmpl
            self.active_blocks.append(block)
            return True
        except MemoryError:
            return False

    def add_clean(self) -> bool:
        size = self.chunk_mb * MIB
        try:
            target_dir = "/var/tmp" if os.path.exists("/var/tmp") else "/tmp"
            tf = tempfile.NamedTemporaryFile(dir=target_dir, prefix="zram_balloon_clean_", delete=False)
            tmpl = self._template()
            for _ in range(0, size, MIB):
                tf.write(tmpl)
            tf.flush()
            os.fsync(tf.fileno())
            self.clean_files.append((tf, size))
            return True
        except (MemoryError, OSError):
            return False

    def add_dirty(self) -> bool:
        size = self.chunk_mb * MIB
        try:
            target_dir = "/var/tmp" if os.path.exists("/var/tmp") else "/tmp"
            tf = tempfile.NamedTemporaryFile(dir=target_dir, prefix="zram_balloon_dirty_", delete=False)
            tf.truncate(size)
            buf = mmap.mmap(tf.fileno(), size, mmap.MAP_SHARED, mmap.PROT_WRITE)
            tmpl = self._template()
            for off in range(0, size, MIB):
                buf[off:off + MIB] = tmpl
            self.dirty_files.append((tf, buf))
            return True
        except (MemoryError, OSError):
            return False

    def add_shmem(self) -> bool:
        size = self.chunk_mb * MIB
        target_dir = "/dev/shm" if os.path.exists("/dev/shm") else "/tmp"
        path = f"{target_dir}/zram_balloon_shm_{os.getpid()}_{time.time_ns()}"
        try:
            f = open(path, "wb+")
            f.truncate(size)
            buf = mmap.mmap(f.fileno(), size, mmap.MAP_SHARED, mmap.PROT_WRITE)
            tmpl = self._template()
            for off in range(0, size, MIB):
                buf[off:off + MIB] = tmpl
            self.shmem_files.append((path, f, buf))
            return True
        except (MemoryError, OSError):
            return False

    # --- Free Chunks ---
    def free_dormant(self) -> bool:
        if not self.dormant_blocks:
            return False
        b = self.dormant_blocks.pop()
        try:
            b.close()
        except Exception:
            pass
        return True

    def free_active(self) -> bool:
        if not self.active_blocks:
            return False
        self.active_blocks.pop()
        return True

    def free_clean(self) -> bool:
        if not self.clean_files:
            return False
        tf, _ = self.clean_files.pop()
        path = tf.name
        try:
            tf.close()
        except Exception:
            pass
        try:
            if os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass
        return True

    def free_dirty(self) -> bool:
        if not self.dirty_files:
            return False
        tf, buf = self.dirty_files.pop()
        path = tf.name
        try:
            buf.close()
        except Exception:
            pass
        try:
            tf.close()
        except Exception:
            pass
        try:
            if os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass
        return True

    def free_shmem(self) -> bool:
        if not self.shmem_files:
            return False
        path, f, buf = self.shmem_files.pop()
        try:
            buf.close()
        except Exception:
            pass
        try:
            f.close()
        except Exception:
            pass
        try:
            if os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass
        return True

    # --- Actions ---
    def pageout_dormant(self) -> int:
        """Invokes MADV_PAGEOUT on dormant anonymous blocks to immediately push into ZRAM."""
        size = self.chunk_mb * MIB
        pushed = 0
        for buf in self.dormant_blocks:
            try:
                addr = ctypes.addressof(ctypes.c_char.from_buffer(buf))
                if libc.madvise(addr, size, MADV_PAGEOUT) == 0:
                    pushed += self.chunk_mb
            except Exception:
                pass
        return pushed

    def sync_dirty(self) -> int:
        """Flushes dirty page cache to disk."""
        synced = 0
        for tf, buf in self.dirty_files:
            try:
                buf.flush()
                os.fdatasync(tf.fileno())
                synced += self.chunk_mb
            except Exception:
                pass
        return synced

    def drop_caches(self) -> tuple[bool, str]:
        """Flushes and drops clean caches."""
        if os.geteuid() == 0:
            try:
                os.sync()
                Path("/proc/sys/vm/drop_caches").write_text("3\n")
                return True, "Caches dropped (kernel drop_caches=3)"
            except Exception as exc:
                return False, f"drop_caches failed: {exc}"
        try:
            proc = subprocess.run(
                ["sudo", "-S", "bash", "-c", "sync && echo 3 > /proc/sys/vm/drop_caches"],
                input="2345\n",
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            if proc.returncode == 0:
                return True, "Caches dropped via sudo"
            return False, "sudo drop_caches failed"
        except Exception as exc:
            return False, f"drop_caches error: {exc}"

    def compact_zram(self) -> tuple[int, str]:
        """Triggers memory compaction on all active ZRAM devices via sysfs."""
        compacted = 0
        for p in Path("/sys/block").glob("zram[0-9]*"):
            c_file = p / "compact"
            if c_file.is_file():
                if os.geteuid() == 0:
                    try:
                        c_file.write_text("1\n")
                        compacted += 1
                    except Exception:
                        pass
                else:
                    try:
                        proc = subprocess.run(
                            ["sudo", "-S", "bash", "-c", f"echo 1 > {c_file}"],
                            input="2345\n",
                            capture_output=True,
                            text=True,
                            timeout=2.0,
                        )
                        if proc.returncode == 0:
                            compacted += 1
                    except Exception:
                        pass
        if compacted > 0:
            return compacted, f"Compacted {compacted} ZRAM device(s)"
        return 0, "ZRAM compaction unavailable or permission denied"

    def clear(self, cat: str | None = None) -> None:
        if cat in (None, "dormant"):
            while self.free_dormant():
                pass
        if cat in (None, "active"):
            while self.free_active():
                pass
        if cat in (None, "clean"):
            while self.free_clean():
                pass
        if cat in (None, "dirty"):
            while self.free_dirty():
                pass
        if cat in (None, "shmem"):
            while self.free_shmem():
                pass

    def cleanup(self) -> None:
        self._touch_running = False
        self.clear(None)

    @property
    def total_mb(self) -> int:
        return (
            len(self.dormant_blocks)
            + len(self.active_blocks)
            + len(self.clean_files)
            + len(self.dirty_files)
            + len(self.shmem_files)
        ) * self.chunk_mb

    @property
    def counts(self) -> dict[str, int]:
        return {
            "dormant": len(self.dormant_blocks),
            "active": len(self.active_blocks),
            "clean": len(self.clean_files),
            "dirty": len(self.dirty_files),
            "shmem": len(self.shmem_files),
        }


# --------------------------------------------------------------------------- #
#  UI Panel Builders                                                          #
# --------------------------------------------------------------------------- #
def panel_memory(snap: dict) -> Panel:
    mem = snap["mem"]
    if not mem:
        return Panel("[red]/proc/meminfo unreadable[/red]", title="[bold cyan]System Memory Topology", border_style="red")

    swaps = snap["swaps"]
    total = mem.get("MemTotal", 0)
    available = mem.get("MemAvailable", 0)
    free = mem.get("MemFree", 0)
    used = max(total - available, 0)
    used_frac = used / total if total else 0.0

    anon = mem.get("AnonPages", 0)
    shmem = mem.get("Shmem", 0)
    cached = mem.get("Cached", 0)
    file_cache = max(cached - shmem, 0)
    buffers = mem.get("Buffers", 0)
    s_recl = mem.get("SReclaimable", 0)
    dirty = mem.get("Dirty", 0)
    unevictable = mem.get("Unevictable", 0)

    swap_total = mem.get("SwapTotal", 0)
    swap_free = mem.get("SwapFree", 0)
    swap_used = max(swap_total - swap_free, 0)
    swap_frac = swap_used / swap_total if swap_total else 0.0

    table = new_table()
    table.add_row("Total Physical RAM", f"[bold white]{fmt_bytes(total)}[/bold white]")
    table.add_row("Used RAM [dim](Total - Avail)[/dim]", f"{fmt_bytes(used)} [dim]({used_frac * 100:.1f}%)[/dim]")
    table.add_row("", bar(used_frac))
    table.add_row("Available [dim](Reclaim Buffer)[/dim]", f"[bold green]{fmt_bytes(available)}[/bold green]")
    table.add_row("Free RAM [dim](Strict)[/dim]", fmt_bytes(free))
    table.add_row("  ↳ AnonPages [dim](Apps)[/dim]", fmt_bytes(anon))
    table.add_row("  ↳ Clean Page Cache", fmt_bytes(file_cache))
    table.add_row("  ↳ Shmem / Tmpfs [dim](Shared)[/dim]", fmt_bytes(shmem))
    table.add_row("  ↳ Buffers + Reclaimable Slab", fmt_bytes(buffers + s_recl))
    table.add_row("  ↳ Dirty Pages [dim](Unwritten)[/dim]", f"[yellow]{fmt_bytes(dirty)}[/yellow]" if dirty > 10 * MIB else fmt_bytes(dirty))
    table.add_row("  ↳ Unevictable [dim](Pinned)[/dim]", fmt_bytes(unevictable))
    table.add_row("", "")
    table.add_row("[bold]Total Swap Space[/bold]", f"[bold white]{fmt_bytes(swap_total)}[/bold white]")
    table.add_row("Total Swap Used", f"{fmt_bytes(swap_used)} [dim]({swap_frac * 100:.1f}%)[/dim]")
    table.add_row("", bar(swap_frac))
    table.add_row(
        "  ↳ [magenta]ZRAM Swap Pool[/magenta]",
        f"[magenta]{fmt_bytes(swaps['zram_used'])} / {fmt_bytes(swaps['zram_total'])}[/magenta]",
    )
    table.add_row(
        "  ↳ [red]Disk Swap Spillover[/red]",
        f"[red]{fmt_bytes(swaps['disk_used'])} / {fmt_bytes(swaps['disk_total'])}[/red]",
    )
    table.add_row("  ↳ SwapCached", fmt_bytes(mem.get("SwapCached", 0)))

    return Panel(table, title=f"[bold {ACCENT}]System Memory Topology", border_style=ACCENT, padding=(0, 1))


def panel_pressure(snap: dict) -> Panel:
    psi = snap["psi"]
    rates = snap.get("rates", {})
    table = new_table()

    if psi:
        some10 = psi.get("some.avg10", 0.0)
        some60 = psi.get("some.avg60", 0.0)
        some300 = psi.get("some.avg300", 0.0)
        full10 = psi.get("full.avg10", 0.0)
        full60 = psi.get("full.avg60", 0.0)
        full300 = psi.get("full.avg300", 0.0)

        some_st = SUCCESS if some10 < 5.0 else WARNING if some10 < 20.0 else f"bold {ERROR}"
        full_st = SUCCESS if full10 < 1.0 else WARNING if full10 < 10.0 else f"bold {ERROR}"

        table.add_row("PSI Some [dim]10s/60s/300s[/dim]", f"[{some_st}]{some10:.2f}[/{some_st}] / {some60:.2f} / {some300:.2f}")
        table.add_row("PSI Full [dim]10s/60s/300s[/dim]", f"[{full_st}]{full10:.2f}[/{full_st}] / {full60:.2f} / {full300:.2f}")
    else:
        table.add_row("PSI Pressure", "[dim]n/a[/dim]")

    table.add_row("", "")
    swap_in = rates.get("pswpin", 0.0) * PAGE_SIZE
    swap_out = rates.get("pswpout", 0.0) * PAGE_SIZE
    table.add_row("Swap In Rate [dim](Decompr)[/dim]", f"{fmt_bytes(swap_in)}/s")
    table.add_row("Swap Out Rate [dim](Compress)[/dim]", f"[bold {ACCENT}]{fmt_bytes(swap_out)}/s[/bold {ACCENT}]")
    table.add_row("Major Page Faults", f"{rates.get('pgmajfault', 0.0):.0f}/s")

    oom_kills = snap["vmstat"].get("oom_kill")
    table.add_row(
        "OOM Kills [dim](Since Boot)[/dim]",
        "[dim]n/a[/dim]" if oom_kills is None else (f"[bold {ERROR}]{oom_kills}[/bold {ERROR}]" if oom_kills else "0"),
    )

    return Panel(table, title=f"[bold {WARNING}]Memory Pressure (PSI) & Swap I/O Rates", border_style=WARNING, padding=(0, 1))


def panel_processes(procs: list[tuple[int, int, int, str]], total_ram: int) -> Panel:
    table = Table(expand=True, box=None, pad_edge=False, header_style="bold dim", show_edge=False)
    table.add_column("PID", style=f"dim {ACCENT}", ratio=1)
    table.add_column("PROCESS", style=f"bold {FG}", ratio=3)
    table.add_column("RSS", justify="right", style=f"{WARNING}", ratio=2)
    table.add_column("SWAP", justify="right", style=f"{ACCENT}", ratio=2)
    table.add_column("%", justify="right", style=f"{ERROR}", ratio=1)

    if not procs:
        table.add_row("", "[dim]scanning /proc...[/dim]", "", "", "")
    else:
        for rss, swap, pid, name in procs:
            share = (rss / total_ram * 100.0) if total_ram else 0.0
            swp_str = f"[bold {ACCENT}]{fmt_bytes(swap)}[/bold {ACCENT}]" if swap > 0 else "[dim]-[/dim]"
            table.add_row(str(pid), escape(name[:18]), fmt_bytes(rss), swp_str, f"{share:.1f}")

    return Panel(table, title=f"[bold {ACCENT}]Top Memory Consumers [dim](RSS & VmSwap)[/dim]", border_style=MUTED, padding=(0, 1))


def panel_zram(snap: dict) -> Panel:
    zdata = snap["zram"]
    devices = zdata["devices"]

    if not devices:
        return Panel(
            "[dim]No ZRAM devices detected on system.\n"
            "Enable via: systemctl start systemd-zram-setup@zram0.service[/dim]",
            title=f"[bold {ACCENT}]ZRAM Multi-Device Diagnostics",
            border_style=ACCENT,
        )

    # 1:1 ratio guarantees balanced columns without horizontal text clipping
    table = Table(expand=True, box=None, pad_edge=False, show_header=False)
    table.add_column("k", style=f"bold {FG}", ratio=1)
    table.add_column("v", justify="right", style=f"{SUCCESS}", ratio=1)

    for i, d in enumerate(devices):
        if i > 0:
            table.add_section()
        role_label = escape(d["role"])
        table.add_row(f"[bold {ACCENT}]/dev/{d['name']}[/bold {ACCENT}] [{WARNING}]{role_label}[/{WARNING}]", f"[dim]{escape(d['algo'])} • {fmt_bytes(d['disksize'])}[/dim]")
        codec_str = f"{d['codec_ratio']:.1f}x" if d["orig"] > 0 else "idle"
        table.add_row("  Data → Compr", f"{fmt_bytes(d['orig'])} → {fmt_bytes(d['compr'])} [dim]({codec_str})[/dim]")
        eff_str = f"{d['eff_ratio']:.2f}x" if d["orig"] > 0 else "idle"
        table.add_row("  RAM Actually Used", f"{fmt_bytes(d['used'])} [dim]({eff_str} eff)[/dim]")
        saved_str = fmt_bytes(d["saved"]) if d["orig"] > 0 else "0 B"
        extra = f" [dim]({fmt_bytes(d['same'])} dedup)[/dim]" if d["same"] > 0 else ""
        table.add_row("  RAM Saved / Dedup", f"[bold {SUCCESS}]+{saved_str}[/bold {SUCCESS}]{extra}")

    # Total Pool Summary
    if len(devices) > 1:
        table.add_section()
        table.add_row("[bold]Total ZRAM Pool[/bold]", f"[bold {FG}]{fmt_bytes(zdata['disksize_total'])} cap[/bold {FG}]")
        table.add_row("  Total Stored → Compr", f"{fmt_bytes(zdata['orig_total'])} → {fmt_bytes(zdata['compr_total'])}")
        table.add_row("  Total RAM Consumed", f"[bold]{fmt_bytes(zdata['used_total'])}[/bold]")
        tot_eff_str = f"{zdata['eff_ratio']:.2f}x" if zdata["orig_total"] > 0 else "idle"
        table.add_row("  Effective Ratio / Saved", f"[bold {SUCCESS}]{tot_eff_str}[/bold {SUCCESS}] ([bold {ACCENT}]+{fmt_bytes(zdata['saved_total'])}[/bold {ACCENT}])")

    return Panel(table, title=f"[bold {ACCENT}]ZRAM Multi-Device Diagnostics", border_style=ACCENT, padding=(0, 1))


def panel_vm(snap: dict) -> Panel:
    table = new_table()

    swappiness = read_str("/proc/sys/vm/swappiness")
    page_cluster = read_str("/proc/sys/vm/page-cluster")
    watermark = read_str("/proc/sys/vm/watermark_scale_factor")
    vfs_pressure = read_str("/proc/sys/vm/vfs_cache_pressure")
    proactiveness = read_str("/proc/sys/vm/compaction_proactiveness")
    min_free = read_int("/proc/sys/vm/min_free_kbytes")
    overcommit = read_str("/proc/sys/vm/overcommit_memory")

    table.add_row("vm.swappiness", cell(swappiness))
    table.add_row(
        "vm.page-cluster [dim](0=opt)[/dim]",
        cell(page_cluster, f"bold {SUCCESS}" if page_cluster == "0" else f"bold {WARNING}"),
    )
    table.add_row("vm.watermark_scale", cell(watermark))
    table.add_row("vm.vfs_cache_pressure", cell(vfs_pressure))
    table.add_row("vm.compaction_proact", cell(proactiveness))
    table.add_row("vm.min_free_kbytes", fmt_bytes(min_free * 1024) if min_free is not None else "[dim]n/a[/dim]")
    table.add_row("vm.overcommit_memory", cell(overcommit))

    dirty_bg = read_int("/proc/sys/vm/dirty_background_bytes")
    dirty_limit = read_int("/proc/sys/vm/dirty_bytes")
    if dirty_bg:
        table.add_row("vm.dirty_background_bytes", fmt_bytes(dirty_bg))
    if dirty_limit:
        table.add_row("vm.dirty_bytes [dim](Throttle)[/dim]", fmt_bytes(dirty_limit))

    table.add_row("", "")
    lru_en = read_str("/sys/kernel/mm/lru_gen/enabled")
    table.add_row("mglru.enabled", cell(lru_en, f"{SUCCESS}" if lru_en != MISSING else ""))
    table.add_row("mglru.min_ttl_ms", cell(read_str("/sys/kernel/mm/lru_gen/min_ttl_ms")))

    thp_en = read_selected("/sys/kernel/mm/transparent_hugepage/enabled")
    table.add_row("thp.enabled", cell(thp_en))

    zswap = read_str("/sys/module/zswap/parameters/enabled")
    if zswap not in (MISSING, DENIED) and zswap.upper().startswith("Y"):
        table.add_row("zswap", f"[bold {WARNING}]on[/bold {WARNING}] [bold {ERROR}](conflicts with ZRAM!)[/bold {ERROR}]")
    else:
        table.add_row("zswap", cell(zswap) if zswap in (MISSING, DENIED) else "off [dim](clean)[/dim]")

    return Panel(table, title=f"[bold {SUCCESS}]Live Kernel VM Policies & Tunables", border_style=SUCCESS, padding=(0, 1))


def panel_balloon(mgr: BalloonManager, active_category: str) -> Panel:
    counts = mgr.counts
    chunk = mgr.chunk_mb
    table = Table(expand=True, box=None, pad_edge=False, show_header=False)
    table.add_column("cat", style=f"bold {FG}", ratio=1)
    table.add_column("stat", justify="right", style=f"{WARNING}", ratio=1)

    categories = [
        ("dormant", "[1] Dormant Anon", "Cold/Swap Candidate"),
        ("active", "[2] Active Anon", "Foreground Active"),
        ("clean", "[3] Clean Cache", "Reclaimable File Cache"),
        ("dirty", "[4] Dirty Cache", "Unwritten Disk Writes"),
        ("shmem", "[5] Shmem Tmpfs", "/dev/shm Swappable"),
    ]

    for key, name, desc in categories:
        cnt = counts[key]
        mb = cnt * chunk
        is_sel = (key == active_category)
        prefix = "▶ " if is_sel else "  "
        title_style = f"bold {SUCCESS}" if is_sel else f"{FG}"
        stat_str = f"{cnt} blocks [bold {WARNING}]({mb} MiB)[/bold {WARNING}]" if cnt > 0 else f"0 blocks [dim](0 MiB)[/dim]"
        table.add_row(f"{prefix}[{title_style}]{name}[/{title_style}] [dim]({desc})[/dim]", stat_str)

    table.add_section()
    tot_cnt = sum(counts.values())
    tot_mb = mgr.total_mb
    table.add_row(
        "[bold]Total Injected Load[/bold]",
        f"[bold {WARNING}]{tot_mb} MiB[/bold {WARNING}] [dim]({tot_cnt} blocks)[/dim]",
    )
    table.add_row(
        "[dim]Quick Actions:[/dim]",
        "[dim]p=PageOut s=Sync d=Drop k=Compact[/dim]",
    )

    return Panel(
        table,
        title=f"[bold {SUCCESS}]RAM Ballooning Engine [dim](Selected: [{active_category.upper()}])[/dim]",
        border_style=SUCCESS,
        padding=(0, 1),
    )


# ============================================================================
#  Shortcuts & Help Modal Dialog
# ============================================================================
class ShortcutsScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss", "Dismiss", priority=True),
        Binding("f1", "dismiss", "Dismiss", priority=True),
        Binding("question_mark", "dismiss", "Dismiss", priority=True),
        Binding("q", "dismiss", "Dismiss", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="help_dialog"):
            yield Static("󰌌 Dusky RAM Analyzer & ZRAM Benchmark Shortcuts", id="modal-title")

            text = Text()
            text.append("Synthetic Balloon Categories\n", style=f"bold {ACCENT}")
            text.append("  1: Dormant Anon (Cold Swap)       2: Active Anon (Foreground LRU)\n")
            text.append("  3: Clean Cache (Reclaimable)      4: Dirty Cache (Unwritten Disk)\n")
            text.append("  5: Shmem / Tmpfs (/dev/shm)\n\n")

            text.append("Memory Pressure Actions\n", style=f"bold {ACCENT}")
            text.append("  + / =  Allocate +1 chunk          - / _  Free -1 chunk\n")
            text.append("  p      PageOut (MADV_PAGEOUT)     s      Sync dirty cache to disk\n")
            text.append("  d      Drop caches (root)         k      Compact ZRAM (root)\n")
            text.append("  c      Clear all balloon memory\n\n")

            text.append("Navigation & Controls\n", style=f"bold {ACCENT}")
            text.append("  r      Immediate telemetry poll   F1 / ? Toggle this help modal\n")
            text.append("  q/Esc  Clean up memory and quit")

            yield Static(text, id="modal-text")

            with Horizontal(id="modal_btn_container"):
                yield Button("Close [F1 / Esc]", id="btn_modal_close")

    def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        if key in ("escape", "f1", "question_mark", "q", "enter", "space", "?") or event.character in ("?", "q"):
            self.dismiss(None)
            event.stop()

    @on(Button.Pressed, "#btn_modal_close")
    def on_close_click(self) -> None:
        self.dismiss(None)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)

    def action_dismiss(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------------- #
#  Textual TUI Application                                                    #
# --------------------------------------------------------------------------- #
class DuskyRAMAnalyzer(App):
    TITLE = "DUSKY RAM ANALYZER & BALLOON BENCHMARK"
    ENABLE_COMMAND_PALETTE = False

    CSS = f"""
    Screen {{
        layout: vertical;
        background: {BG};
    }}

    #main_container {{
        layout: horizontal;
        height: 1fr;
        padding: 0 1;
    }}

    .column {{
        width: 1fr;
        height: 100%;
        padding: 0 1;
        scrollbar-size: 1 1;
        scrollbar-background: {BG};
        scrollbar-color: {MUTED};
        scrollbar-color-hover: {ACCENT};
    }}

    .panel {{
        height: auto;
        margin-bottom: 1;
    }}

    #controls_container {{
        dock: bottom;
        layout: vertical;
        height: auto;
        background: {BG};
        padding: 0;
        margin: 0;
    }}

    .btn-row {{
        layout: horizontal;
        width: 100%;
        height: 1;
        margin: 0;
        padding: 0 1;
    }}

    .spacer {{
        width: 1fr;
        height: 1;
    }}

    /* Tight modern buttons: zero extra padding, auto-fit width, cohesive theme hover */
    Button {{
        height: 1;
        min-height: 1;
        min-width: 0;
        width: auto;
        border: none;
        padding: 0;
        margin: 0;
        text-style: bold;
    }}

    /* Type selection buttons */
    .type-btn {{
        background: #2b1f24;
        color: #bbaab0;
    }}
    .type-btn:hover, .type-btn:focus {{
        background: {ACCENT};
        color: {BG};
    }}
    .type-btn.active-type {{
        background: {ACCENT};
        color: {BG};
        text-style: bold;
    }}
    .type-btn.active-type:hover, .type-btn.active-type:focus {{
        background: {SUCCESS};
        color: {BG};
    }}

    /* Action buttons */
    #btn_help {{
        background: {ACCENT};
        color: {BG};
    }}
    #btn_help:hover, #btn_help:focus {{
        background: {SUCCESS};
        color: {BG};
    }}

    #btn_add {{
        background: #1b4332;
        color: #d1fae5;
    }}
    #btn_add:hover, #btn_add:focus {{
        background: #22c55e;
        color: #000000;
    }}

    #btn_free {{
        background: #78350f;
        color: #fef3c7;
    }}
    #btn_free:hover, #btn_free:focus {{
        background: #f59e0b;
        color: #000000;
    }}

    #btn_pageout {{
        background: #581c87;
        color: #f3e8ff;
    }}
    #btn_pageout:hover, #btn_pageout:focus {{
        background: #a855f7;
        color: #ffffff;
    }}

    #btn_sync {{
        background: #0369a1;
        color: #e0f2fe;
    }}
    #btn_sync:hover, #btn_sync:focus {{
        background: #38bdf8;
        color: #000000;
    }}

    #btn_drop {{
        background: #9a3412;
        color: #ffedd5;
    }}
    #btn_drop:hover, #btn_drop:focus {{
        background: #f97316;
        color: #000000;
    }}

    #btn_compact {{
        background: #065f46;
        color: #d1fae5;
    }}
    #btn_compact:hover, #btn_compact:focus {{
        background: #10b981;
        color: #000000;
    }}

    #btn_clear {{
        background: #7f1d1d;
        color: #fee2e2;
    }}
    #btn_clear:hover, #btn_clear:focus {{
        background: #ef4444;
        color: #ffffff;
    }}

    #btn_refresh {{
        background: #374151;
        color: #f3f4f6;
    }}
    #btn_refresh:hover, #btn_refresh:focus {{
        background: #94a3b8;
        color: #000000;
    }}

    #btn_quit {{
        background: {MUTED};
        color: {ERROR};
    }}
    #btn_quit:hover, #btn_quit:focus {{
        background: {ERROR};
        color: {BG};
    }}


    /* Shortcuts modal dialog styling */
    ShortcutsScreen {{
        align: center middle;
    }}

    #help_dialog {{
        width: 76;
        height: auto;
        max-height: 95%;
        overflow-y: auto;
        background: {BG};
        border: heavy {ACCENT};
        padding: 0 1;
    }}

    #modal-title {{
        color: {ACCENT};
        text-style: bold;
        text-align: center;
        margin: 0;
    }}

    #modal-text {{
        color: {FG};
        margin: 0;
    }}

    #modal_btn_container {{
        height: 1;
        align-horizontal: center;
        margin-top: 1;
    }}

    Button#btn_modal_close {{
        height: 1;
        width: auto;
        min-width: 0;
        border: none;
        background: {ACCENT};
        color: {BG};
        text-style: bold;
        padding: 0 1;
        margin: 0;
    }}

    Button#btn_modal_close:hover, Button#btn_modal_close:focus {{
        background: {SUCCESS};
        color: {BG};
    }}
    """

    BINDINGS = [
        Binding("f1", "help", "Help", priority=True),
        Binding("question_mark", "help", "Help", priority=True),
        ("1", "set_type('dormant')", "Dormant"),
        ("2", "set_type('active')", "Active"),
        ("3", "set_type('clean')", "Clean"),
        ("4", "set_type('dirty')", "Dirty"),
        ("5", "set_type('shmem')", "Shmem"),
        ("+", "add_chunk", "Add"),
        ("=", "add_chunk", "Add"),
        ("-", "free_chunk", "Free"),
        ("_", "free_chunk", "Free"),
        ("p", "pageout_zram", "PageOut"),
        ("s", "sync_dirty", "Sync"),
        ("d", "drop_caches", "Drop"),
        ("k", "compact_zram", "Compact"),
        ("c", "clear_all", "Clear"),
        ("r", "refresh_now", "Refresh"),
        ("q", "bail_out", "Quit"),
        Binding("escape", "bail_out", "Quit"),
    ]

    def __init__(self, interval: float = 1.0, chunk_mb: int = 250) -> None:
        super().__init__()
        self.interval = interval
        self.balloon = BalloonManager(chunk_mb=chunk_mb)
        self.active_category = "dormant"
        self.rates = RateMeter()
        self.procs: list[tuple[int, int, int, str]] = []

        self._proc_busy = False
        self._add_queue: list[str] = []
        self._worker_running = False

    def notify_status(self, message: str, severity: str = "information", timeout: float = 2.5) -> None:
        """Non-intrusive floating toast notification; uses zero permanent screen space."""
        clean = re.sub(r"\[/?.*?\]", "", message).strip()
        if not clean:
            return
        if threading.current_thread() is threading.main_thread():
            self.notify(clean, severity=severity, timeout=timeout)
        else:
            self.call_from_thread(self.notify, clean, severity=severity, timeout=timeout)

    def action_help(self) -> None:
        """Toggles the shortcuts and help modal dialog."""
        if isinstance(self.screen, ModalScreen):
            self.screen.dismiss(None)
        else:
            self.push_screen(ShortcutsScreen())

    def compose(self) -> ComposeResult:
        with Horizontal(id="main_container"):
            with VerticalScroll(classes="column"):
                yield Static(id="p_mem", classes="panel")
                yield Static(id="p_psi", classes="panel")
                yield Static(id="p_proc", classes="panel")
            with VerticalScroll(classes="column"):
                yield Static(id="p_zram", classes="panel")
                yield Static(id="p_vm", classes="panel")
                yield Static(id="p_balloon", classes="panel")

        chunk = self.balloon.chunk_mb
        with Static(id="controls_container"):
            # Row 1: Balloon Type Selection + F1 Help + Quit (spread evenly across row)
            with Horizontal(classes="btn-row"):
                yield Button("1:Dormant", id="type_dormant", classes="type-btn active-type")
                yield Static(classes="spacer")
                yield Button("2:Active", id="type_active", classes="type-btn")
                yield Static(classes="spacer")
                yield Button("3:Clean", id="type_clean", classes="type-btn")
                yield Static(classes="spacer")
                yield Button("4:Dirty", id="type_dirty", classes="type-btn")
                yield Static(classes="spacer")
                yield Button("5:Shmem", id="type_shmem", classes="type-btn")
                yield Static(classes="spacer")
                yield Button("󰌌 F1 Help", id="btn_help")
                yield Static(classes="spacer")
                yield Button("q Quit", id="btn_quit")

            # Row 2: Actions (spread evenly across row)
            with Horizontal(classes="btn-row"):
                yield Button(f"+ {chunk}M", id="btn_add")
                yield Static(classes="spacer")
                yield Button(f"- {chunk}M", id="btn_free")
                yield Static(classes="spacer")
                yield Button("p PageOut", id="btn_pageout")
                yield Static(classes="spacer")
                yield Button("s Sync", id="btn_sync")
                yield Static(classes="spacer")
                yield Button("d Drop", id="btn_drop")
                yield Static(classes="spacer")
                yield Button("k Compact", id="btn_compact")
                yield Static(classes="spacer")
                yield Button("c Clear", id="btn_clear")
                yield Static(classes="spacer")
                yield Button("r Ref", id="btn_refresh")

    def on_mount(self) -> None:
        self.w_mem = self.query_one("#p_mem", Static)
        self.w_psi = self.query_one("#p_psi", Static)
        self.w_proc = self.query_one("#p_proc", Static)
        self.w_zram = self.query_one("#p_zram", Static)
        self.w_vm = self.query_one("#p_vm", Static)
        self.w_balloon = self.query_one("#p_balloon", Static)

        self.refresh_dashboards()
        self.scan_now()
        self.set_interval(self.interval, self.refresh_dashboards)
        self.set_interval(max(self.interval, 2.0), self.scan_now)

    # -- Rendering & Refresh -------------------------------------------------
    def refresh_dashboards(self) -> None:
        try:
            snap = collect()
            snap["rates"] = self.rates.update(snap["vmstat"])
            tot_ram = snap["mem"].get("MemTotal", 0)

            self.w_mem.update(panel_memory(snap))
            self.w_psi.update(panel_pressure(snap))
            self.w_proc.update(panel_processes(self.procs, tot_ram))
            self.w_zram.update(panel_zram(snap))
            self.w_vm.update(panel_vm(snap))
            self.w_balloon.update(panel_balloon(self.balloon, self.active_category))
        except Exception as exc:
            self.notify_status(f"Refresh error: {exc}", severity="error")

    # -- Background Workers --------------------------------------------------
    def scan_now(self) -> None:
        if self._proc_busy:
            return
        self._proc_busy = True
        self.run_worker(self._scan_worker, thread=True, exit_on_error=False, group="procs")

    def _scan_worker(self) -> None:
        try:
            self.procs = scan_processes()
        except Exception:
            pass
        finally:
            self._proc_busy = False

    def action_set_type(self, cat: str) -> None:
        self.active_category = cat
        for c in ("dormant", "active", "clean", "dirty", "shmem"):
            try:
                btn = self.query_one(f"#type_{c}", Button)
                if c == cat:
                    btn.add_class("active-type")
                else:
                    btn.remove_class("active-type")
            except Exception:
                pass
        self.refresh_dashboards()
        self.notify_status(f"Category: {cat.upper()}", timeout=1.5)

    # -- Queued Balloon Actions (Background Threads) -------------------------
    def action_add_chunk(self) -> None:
        cat = self.active_category
        self._add_queue.append(cat)
        self.notify_status(f"Queued +{self.balloon.chunk_mb} MiB {cat.upper()}...", severity="warning", timeout=1.5)
        self.refresh_dashboards()

        if not self._worker_running:
            self._worker_running = True
            self.run_worker(self._worker_add_loop, thread=True, exit_on_error=False, group="balloon")

    def _worker_add_loop(self) -> None:
        try:
            while self._add_queue:
                cat = self._add_queue.pop(0)
                fn = {
                    "dormant": self.balloon.add_dormant,
                    "active": self.balloon.add_active,
                    "clean": self.balloon.add_clean,
                    "dirty": self.balloon.add_dirty,
                    "shmem": self.balloon.add_shmem,
                }.get(cat)
                success = fn() if fn else False
                if success:
                    rem = len(self._add_queue)
                    rem_str = f" (remaining: {rem})" if rem > 0 else ""
                    self.notify_status(f"Added +{self.balloon.chunk_mb} MiB {cat.upper()}{rem_str} (Total: {self.balloon.total_mb} MiB)")
                else:
                    self._add_queue.clear()
                    self.notify_status(f"Allocation refused for {cat.upper()} (MemoryError/OSError)", severity="error", timeout=4.0)
                    break
                self.call_from_thread(self.refresh_dashboards)
        finally:
            self._worker_running = False
            self.call_from_thread(self.refresh_dashboards)

    def action_free_chunk(self) -> None:
        cat = self.active_category
        fn = {
            "dormant": self.balloon.free_dormant,
            "active": self.balloon.free_active,
            "clean": self.balloon.free_clean,
            "dirty": self.balloon.free_dirty,
            "shmem": self.balloon.free_shmem,
        }.get(cat)
        if fn and fn():
            self.notify_status(f"Freed -{self.balloon.chunk_mb} MiB {cat.upper()} (Total: {self.balloon.total_mb} MiB)")
        else:
            self.notify_status(f"No {cat.upper()} blocks to free.", severity="warning")
        self.refresh_dashboards()

    def action_pageout_zram(self) -> None:
        self.notify_status("Paging out dormant memory to ZRAM (MADV_PAGEOUT)...", timeout=2.0)
        self.refresh_dashboards()
        self.run_worker(self._worker_pageout, thread=True, exit_on_error=False, group="balloon")

    def _worker_pageout(self) -> None:
        try:
            mb = self.balloon.pageout_dormant()
            self.notify_status(f"Pushed {mb} MiB dormant memory into ZRAM!")
        finally:
            self.call_from_thread(self.refresh_dashboards)

    def action_sync_dirty(self) -> None:
        mb = self.balloon.sync_dirty()
        self.notify_status(f"Synced {mb} MiB dirty cache to disk.")
        self.refresh_dashboards()

    def action_drop_caches(self) -> None:
        self.notify_status("Dropping caches...", severity="warning", timeout=2.0)
        self.refresh_dashboards()
        self.run_worker(self._worker_drop, thread=True, exit_on_error=False, group="balloon")

    def _worker_drop(self) -> None:
        try:
            ok, msg = self.balloon.drop_caches()
            self.notify_status(msg, severity="information" if ok else "error", timeout=3.5)
        finally:
            self.call_from_thread(self.refresh_dashboards)

    def action_compact_zram(self) -> None:
        self.notify_status("Compacting ZRAM memory...", severity="warning", timeout=2.0)
        self.refresh_dashboards()
        self.run_worker(self._worker_compact, thread=True, exit_on_error=False, group="balloon")

    def _worker_compact(self) -> None:
        try:
            cnt, msg = self.balloon.compact_zram()
            self.notify_status(msg, severity="information" if cnt > 0 else "error", timeout=3.5)
        finally:
            self.call_from_thread(self.refresh_dashboards)

    def action_clear_all(self) -> None:
        self._add_queue.clear()
        self.balloon.clear()
        self.notify_status("All synthetic balloon blocks cleared.", severity="warning")
        self.refresh_dashboards()

    def action_refresh_now(self) -> None:
        self.refresh_dashboards()
        self.scan_now()

    def action_bail_out(self) -> None:
        self.balloon.cleanup()
        self.exit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid and bid.startswith("type_"):
            self.action_set_type(bid[5:])
            return
        match bid:
            case "btn_help":
                self.action_help()
            case "btn_quit":
                self.action_bail_out()
            case "btn_add":
                self.action_add_chunk()
            case "btn_free":
                self.action_free_chunk()
            case "btn_pageout":
                self.action_pageout_zram()
            case "btn_sync":
                self.action_sync_dirty()
            case "btn_drop":
                self.action_drop_caches()
            case "btn_compact":
                self.action_compact_zram()
            case "btn_clear":
                self.action_clear_all()
            case "btn_refresh":
                self.action_refresh_now()


# --------------------------------------------------------------------------- #
#  CLI Entry Point                                                            #
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(
        prog="zram_test",
        description="Dusky RAM Analyzer & Synthetic Memory Balloon Benchmark TUI.",
    )
    parser.add_argument(
        "-i", "--interval", type=float, default=1.0,
        help="Dashboard refresh interval in seconds (default: 1.0)"
    )
    parser.add_argument(
        "-c", "--chunk", type=int, default=250,
        help="MiB allocated per balloon chunk (default: 250)"
    )
    parser.add_argument(
        "-r", "--root", action="store_true",
        help="Opt-in privilege escalation via sudo (preserves terminal env)"
    )
    args = parser.parse_args()

    if args.root:
        elevate()

    app = DuskyRAMAnalyzer(
        interval=max(0.25, args.interval),
        chunk_mb=max(1, args.chunk),
    )
    app.run()
    print("\n[ok] Dusky RAM Analyzer terminated. All synthetic balloon blocks cleaned up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
