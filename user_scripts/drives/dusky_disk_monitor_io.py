#!/usr/bin/env python3

"""
Dusky Disk I/O Monitor for Linux.

One-second sysfs I/O samples, background SMART polling, Matugen colors,
circular drive navigation, and asynchronous filesystem sync.
"""

import atexit
import asyncio
lazy from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass

# ============================================================================
# 1. DEPENDENCIES & AUTHENTICATION
# ============================================================================
def ensure_dependencies() -> None:
    """Report missing offline-installation dependencies before importing the UI."""
    missing: list[str] = []
    for module, package in (("textual", "python-textual"), ("rich", "python-rich")):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    for command, package in (("lsblk", "util-linux"), ("nvme", "nvme-cli"),
                             ("smartctl", "smartmontools")):
        if shutil.which(command) is None:
            missing.append(package)
    if missing:
        print(f"Missing dependencies: {', '.join(missing)}. "
              "Install them before launching the monitor.", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    ensure_dependencies()


_smart_access = os.geteuid() == 0
_sudo_keepalive_stop = threading.Event()
atexit.register(_sudo_keepalive_stop.set)

def _sudo_keepalive_worker() -> None:
    """Refreshes the sudo timestamp in the background so telemetry continues uninterrupted."""
    while not _sudo_keepalive_stop.is_set():
        try:
            subprocess.run(["sudo", "-n", "-v"], capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        _sudo_keepalive_stop.wait(45.0)


def _run_privileged(cmd: list[str], timeout: float = 3.0) -> subprocess.CompletedProcess[str]:
    """Executes a command with root privileges, bypassing sudo overhead when already root."""
    prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
    return subprocess.run([*prefix, *cmd], capture_output=True, text=True, timeout=timeout)


def ensure_smart_access() -> None:
    """Authenticate once; use noninteractive sudo only during telemetry polling."""
    global _smart_access
    if os.geteuid() == 0:
        _smart_access = True
        return
    if shutil.which("sudo") is None:
        return
    try:
        result = subprocess.run(["sudo", "-n", "-v"], capture_output=True, timeout=5)
        if result.returncode != 0:
            if not sys.stdin.isatty():
                return
            print("SMART telemetry requires administrative privileges. Authenticate with sudo:")
            result = subprocess.run(["sudo", "-v"])
        _smart_access = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        _smart_access = False
    except KeyboardInterrupt:
        raise SystemExit(0) from None
    if _smart_access:
        threading.Thread(target=_sudo_keepalive_worker, daemon=True,
                         name="SudoKeepAlive").start()
    else:
        print("SMART access unavailable; I/O monitoring remains active.")


from rich.markup import escape
from rich.table import Table
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.color import Color, ColorParseError
from textual.containers import Container, Horizontal, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Static
from textual.worker import Worker, get_current_worker

# ============================================================================
# 2. DYNAMIC MATUGEN THEME COMPILER
# ============================================================================
def load_theme() -> dict[str, str]:
    """Load valid theme colors, falling back individually for malformed values."""
    path = Path.home() / ".config" / "matugen" / "generated" / "dusky_tui.json"
    defaults: dict[str, str] = {
        "bg": "#0e1416",
        "fg": "#dee3e5",
        "accent": "#82d3e2",
        "error": "#ffb4ab",
        "warning": "#b1cbd0",
        "success": "#bbc5ea",
        "muted": "#3f484a",
    }
    try:
        user_theme = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults
    if isinstance(user_theme, dict):
        for key in defaults:
            value = user_theme.get(key)
            if isinstance(value, str):
                try:
                    defaults[key] = Color.parse(value).hex6
                except ColorParseError:
                    pass
    return defaults


THEME = frozendict(load_theme())
BG = THEME["bg"]
FG = THEME["fg"]
ACCENT = THEME["accent"]
ERROR = THEME["error"]
WARNING = THEME["warning"]
SUCCESS = THEME["success"]
MUTED = THEME["muted"]

# High-contrast readable palette for diagnostic labels and sub-grids
LABEL_COL = "#8fa7ab"
DIVIDER_COL = "#486368"
SPARK_BASE_COL = "#223c40"
TEMP_COL = "#fcd34d"


# ============================================================================
# 3. CORE SYSTEM METRICS & FORMATTING ENGINE
# ============================================================================
def format_bytes(bytes_val: float) -> str:
    """Formats bytes into human-readable B, KiB, MiB, GiB, TiB, or PiB string."""
    if bytes_val < 1024:
        return f"{bytes_val:.0f} B"
    if bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KiB"
    if bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.1f} MiB"
    if bytes_val < 1024 * 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024 * 1024):.1f} GiB"
    if bytes_val < 1024 * 1024 * 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024 * 1024 * 1024):.2f} TiB"
    return f"{bytes_val / (1024 * 1024 * 1024 * 1024 * 1024):.2f} PiB"


def format_rate(rate_bytes_per_sec: float) -> str:
    """Formats transfer rate into human-readable B/s, KiB/s, MiB/s, or GiB/s."""
    if rate_bytes_per_sec <= 0.0:
        return "0.00 MiB/s"
    mb_s = rate_bytes_per_sec / (1024 * 1024)
    if mb_s >= 1024.0:
        return f"{mb_s / 1024.0:.2f} GiB/s"
    if mb_s >= 100.0:
        return f"{mb_s:.1f} MiB/s"
    if mb_s >= 1.0:
        return f"{mb_s:.2f} MiB/s"
    if rate_bytes_per_sec >= 1024.0:
        return f"{rate_bytes_per_sec / 1024.0:.1f} KiB/s"
    return f"{rate_bytes_per_sec:.0f} B/s"


def format_nvme_units(units: int | float | str) -> str:
    """Formats NVMe data units (1 unit = 1,000 * 512 bytes = 512 KB) into human-readable SI string matching nvme-cli."""
    try:
        bytes_val = int(units) * 512_000
        if bytes_val < 0:
            return "N/A"
        if bytes_val < 1e6:
            return f"{bytes_val / 1e3:.1f} KB"
        if bytes_val < 1e9:
            return f"{bytes_val / 1e6:.1f} MB"
        if bytes_val < 1e12:
            return f"{bytes_val / 1e9:.1f} GB"
        if bytes_val < 1e15:
            return f"{bytes_val / 1e12:.2f} TB"
        return f"{bytes_val / 1e15:.2f} PB"
    except (ValueError, TypeError, OverflowError):
        return "N/A"


def format_temperatures(temps: list) -> str:
    unique = dict.fromkeys(temp for temp in temps if isinstance(temp, (int, float)))
    return " │ ".join(f"{temp:g}°C" for temp in list(unique)[:3]) or "N/A"


@dataclass(slots=True, frozen=True)
class BlockStats:
    timestamp: float
    read_ios: int
    read_sectors: int
    read_ticks: int
    write_ios: int
    write_sectors: int
    write_ticks: int
    in_flight: int
    io_ticks: int
    time_in_queue: int
    discard_ios: int = 0
    discard_sectors: int = 0
    discard_ticks: int = 0
    flush_ios: int = 0
    flush_ticks: int = 0


@dataclass(slots=True, frozen=True)
class SmartInfo:
    temp: str = "N/A"
    tbr: str = "N/A"
    tbw: str = "N/A"
    health: str = "N/A"
    power_cycles: str = "N/A"
    power_on_hours: str = "N/A"
    unsafe_shutdowns: str = "N/A"
    media_errors: str = "N/A"
    critical_warning: str = "N/A"
    therm_t1: str = "N/A"


class SysStatParser:
    @staticmethod
    def get_block_stats(device: str) -> BlockStats | None:
        path = Path(f"/sys/block/{device}/stat")
        try:
            with open(path, "r", encoding="utf-8") as f:
                fields = f.read().split()
            if len(fields) < 17:
                return None
            return BlockStats(
                timestamp=time.perf_counter(),
                read_ios=int(fields[0]),
                read_sectors=int(fields[2]),
                read_ticks=int(fields[3]),
                write_ios=int(fields[4]),
                write_sectors=int(fields[6]),
                write_ticks=int(fields[7]),
                in_flight=int(fields[8]),
                io_ticks=int(fields[9]),
                time_in_queue=int(fields[10]),
                discard_ios=int(fields[11]),
                discard_sectors=int(fields[13]),
                discard_ticks=int(fields[14]),
                flush_ios=int(fields[15]),
                flush_ticks=int(fields[16]),
            )
        except (IndexError, ValueError, OSError):
            return None

    @staticmethod
    def _get_smartctl_data(device: str) -> SmartInfo:
        # Query only the logs consumed here; JSON works for USB SAT bridges too.
        # Device statistics have standardized units, unlike vendor ATA attributes.
        try:
            res = _run_privileged(
                ["smartctl", "-j", "-H", "-A", "-i", "-l", "devstat", f"/dev/{device}"],
                timeout=3.0,
            )
            # smartctl uses a bitmask exit status: failing SMART can still supply data.
            data = json.loads(res.stdout)
            if not isinstance(data, dict):
                return SmartInfo()
            return SysStatParser.parse_smartctl(data)
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
            return SmartInfo()

    @staticmethod
    def parse_smartctl(data: dict) -> SmartInfo:
        """Interpret smartmontools' documented JSON without guessing vendor units."""
        values: dict[str, str] = {}
        nvme = data.get("nvme_smart_health_information_log", {})
        if nvme:
            pct = nvme.get("percentage_used")
            if isinstance(pct, int) and pct >= 0:
                values["health"] = f"{max(0, 100 - pct)}%"
            for key, field in (("tbr", "data_units_read"), ("tbw", "data_units_written")):
                if field in nvme:
                    values[key] = format_nvme_units(nvme[field])
            for key in ("power_cycles", "power_on_hours", "unsafe_shutdowns",
                        "media_errors", "critical_warning"):
                if nvme.get(key) is not None:
                    values[key] = str(nvme[key])
            # These values are already Celsius; missing sensors may be JSON null.
            temps = [nvme.get("temperature"), *nvme.get("temperature_sensors", [])]
            values["temp"] = format_temperatures(temps)
            t1 = nvme.get("thermal_mgmt_temperature_1_total_time")
            if isinstance(t1, int):
                values["therm_t1"] = f"{t1}s"

        stats = {
            (page.get("number"), item.get("offset")): item["value"]
            for page in data.get("ata_device_statistics", {}).get("pages", [])
            for item in page.get("table", [])
            if item.get("flags", {}).get("valid") is True
            and isinstance(item.get("value"), int)
        }
        sector_size = data.get("logical_block_size")
        if isinstance(sector_size, int) and sector_size > 0:
            for key, offset in (("tbw", 0x18), ("tbr", 0x28)):
                if (1, offset) in stats:
                    values[key] = format_bytes(stats[1, offset] * sector_size)
        if (7, 8) in stats and stats[7, 8] >= 0:
            values["health"] = f"{max(0, 100 - stats[7, 8])}%"
        for key, offset in (("power_cycles", 8), ("power_on_hours", 16)):
            if (1, offset) in stats:
                values[key] = str(stats[1, offset])

        units = {
            "Lifetime_Writes_GiB": ("tbw", 1024 ** 3),
            "Lifetime_Reads_GiB": ("tbr", 1024 ** 3),
            "Host_Writes_32MiB": ("tbw", 32 * 1024 ** 2),
            "Host_Reads_32MiB": ("tbr", 32 * 1024 ** 2),
        }
        for attr in data.get("ata_smart_attributes", {}).get("table", []):
            name = attr.get("name", "")
            raw = attr.get("raw", {}).get("value")
            if not isinstance(raw, int) or raw < 0:
                continue
            # Names specifying a byte unit are usable; Total_LBAs_* is vendor-specific.
            if name in units:
                key, multiplier = units[name]
                values.setdefault(key, format_bytes(raw * multiplier))
            # Remaining-life attributes use the normalized value, never raw erase counts.
            if name in ("SSD_Life_Left", "Percent_Lifetime_Remain"):
                remaining = attr.get("value")
                if isinstance(remaining, int) and 0 <= remaining <= 100:
                    values.setdefault("health", f"{remaining}%")
            if name in ("Unsafe_Shutdown_Count", "Unexpect_Power_Loss_Ct"):
                values.setdefault("unsafe_shutdowns", str(raw))
            if name == "Reported_Uncorrect":
                values.setdefault("media_errors", str(raw))

        current_temp = data.get("temperature", {}).get("current", stats.get((5, 8)))
        if values.get("temp", "N/A") == "N/A":
            values["temp"] = format_temperatures([current_temp])
        passed = data.get("smart_status", {}).get("passed")
        if passed is False:
            values["health"] = "FAILED"
        elif passed is True:
            values.setdefault("health", "PASSED")
        for key, value in (("power_cycles", data.get("power_cycle_count")),
                           ("power_on_hours", data.get("power_on_time", {}).get("hours"))):
            if value is not None:
                values.setdefault(key, str(value))
        return SmartInfo(**values)

    @staticmethod
    def get_smart_data(device: str) -> SmartInfo:
        # Instant return for non-SMART block devices (ZRAM, loopbacks, ramdisks, devmapper)
        if device.startswith(("zram", "loop", "ram", "dm", "sr", "fd", "nbd")):
            return SmartInfo()

        if not _smart_access:
            return SmartInfo()

        # Use the namespace path itself, including native NVMe multipath names.
        if device.startswith("nvme"):
            dev_target = f"/dev/{device}"
            try:
                cmd = [
                    "nvme", "log", "smart", dev_target,
                    "-o", "json",
                    "--output-format-version=2",
                    "--timeout=1500",
                    "--no-retries",
                ]
                res = _run_privileged(cmd, timeout=2.0)
                if res.returncode == 0 and res.stdout:
                    data = json.loads(res.stdout)
                    if isinstance(data, dict):
                        # nvme-cli JSON temperatures are always Kelvin (zero = unavailable).
                        temps = [data.get("temperature"),
                                 *(data.get(f"temperature_sensor_{i}") for i in range(1, 9))]
                        temp_str = format_temperatures([
                            temp - 273 for temp in temps
                            if isinstance(temp, (int, float)) and temp > 0
                        ])

                        # Drive Health (Percentage Used)
                        health = "N/A"
                        pct_used = data.get("percent_used", data.get("percentage_used"))
                        if pct_used is not None:
                            try:
                                health = f"{min(100, max(0, 100 - int(pct_used)))}%"
                            except (ValueError, TypeError):
                                pass

                        # TBR / TBW (Data Units Read/Written scaled to SI standard)
                        dur = data.get("data_units_read")
                        tbr = format_nvme_units(dur) if dur is not None else "N/A"
                        duw = data.get("data_units_written")
                        tbw = format_nvme_units(duw) if duw is not None else "N/A"

                        # Hardware Lifecycle & Media Reliability Counters
                        power_cycles = str(data.get("power_cycles", "N/A"))
                        power_on_hours = str(data.get("power_on_hours", "N/A"))
                        unsafe_shutdowns = str(data.get("unsafe_shutdowns", "N/A"))
                        media_errors = str(data.get("media_errors", "N/A"))

                        # Critical Warning (numeric or structured mask)
                        cw = data.get("critical_warning", "N/A")
                        if isinstance(cw, dict):
                            cw = cw.get("value", "N/A")
                        critical_warning = str(cw) if cw is not None else "N/A"

                        # Thermal Throttling T1 Time
                        t1 = data.get("thm_temp1_total_time")
                        therm_t1 = f"{t1}s" if t1 is not None and str(t1).isdigit() else "N/A"

                        return SmartInfo(
                            temp=temp_str,
                            tbr=tbr,
                            tbw=tbw,
                            health=health,
                            power_cycles=power_cycles,
                            power_on_hours=power_on_hours,
                            unsafe_shutdowns=unsafe_shutdowns,
                            media_errors=media_errors,
                            critical_warning=critical_warning,
                            therm_t1=therm_t1,
                        )
            except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
                pass

        # Fallback for SATA SSD, HDD, USB drives (or when nvme CLI is not authorized)
        return SysStatParser._get_smartctl_data(device)

    @staticmethod
    def get_ram_buffers() -> tuple[float, float]:
        dirty = writeback = 0.0
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("Dirty:"):
                        dirty = float(line.split()[1]) / 1024.0
                    elif line.startswith("Writeback:"):
                        writeback = float(line.split()[1]) / 1024.0
        except (OSError, IndexError, ValueError):
            pass
        return dirty, writeback

    @staticmethod
    def is_zram_active(dev_name: str) -> bool:
        """Verifies that a ZRAM device is actively engaged in swap or mounted to a filesystem."""
        try:
            sz_p = Path(f"/sys/block/{dev_name}/size")
            if not sz_p.exists() or int(sz_p.read_text().strip()) == 0:
                return False
            if Path("/proc/swaps").exists():
                swaps = Path("/proc/swaps").read_text()
                if f"/dev/{dev_name}" in swaps or dev_name in swaps:
                    return True
            if Path("/proc/mounts").exists():
                with open("/proc/mounts", "r", encoding="utf-8") as f:
                    for line in f:
                        src = line.split()[0] if line.split() else ""
                        if src == f"/dev/{dev_name}" or src.endswith(f"/{dev_name}"):
                            return True
        except Exception:
            pass
        return False

    @staticmethod
    def get_basic_metadata() -> dict[str, dict] | None:
        """Read topology once; None distinguishes a failed query from no devices."""
        try:
            res = subprocess.run(
                ["lsblk", "--json", "--nodeps", "--output", "NAME,SIZE,TYPE,MODEL,ROTA,TRAN"],
                capture_output=True, text=True, check=True, timeout=3.0,
            )
            data = json.loads(res.stdout)
            results: dict[str, dict] = {}
            for dev in data.get("blockdevices", []):
                name = dev.get("name")
                if not name or name.startswith(("loop", "sr", "ram", "dm", "fd", "nbd")):
                    continue
                if name.startswith("zram") and not SysStatParser.is_zram_active(name):
                    continue
                results[name] = {
                    "size": str(dev.get("size") or "?").strip(),
                    "type": str(dev.get("tran") or ("ZRAM" if name.startswith("zram")
                                else "NVME" if name.startswith("nvme")
                                else dev.get("type") or "DISK")).upper().strip(),
                    "model": str(dev.get("model") or ("Compressed RAM" if name.startswith("zram")
                                 else "N/A")).strip(),
                    "rota": dev.get("rota") in (True, 1, "1", "true", "True"),
                    "smart": SmartInfo(),
                }
            return results
        except (OSError, subprocess.SubprocessError, ValueError, TypeError):
            return None

    @staticmethod
    def get_device_metadata(basic_meta: dict[str, dict] | None = None) -> dict[str, dict] | None:
        results = basic_meta if basic_meta is not None else SysStatParser.get_basic_metadata()
        if results and _smart_access:
            with ThreadPoolExecutor(max_workers=min(8, len(results))) as executor:
                for name, smart in zip(results, executor.map(SysStatParser.get_smart_data, results), strict=True):
                    results[name]["smart"] = smart
        return results


# ============================================================================
# 4. TEXTUAL WIDGETS & UI
# ============================================================================

class DriveWidget(Static, can_focus=True):
    DEFAULT_CSS = f"""
    DriveWidget {{
        border: solid {MUTED};
        background: {BG};
        height: auto;
        margin: 0 0 1 0;
        padding: 0 1;
        transition: border 150ms;
    }}
    DriveWidget:focus {{
        border: solid {ACCENT};
        background: {BG};
    }}
    """

    def __init__(self, dev_name: str, **kwargs):
        super().__init__(**kwargs)
        self.dev_name = dev_name
        self.history_read: deque[float] = deque([0.0] * 16, maxlen=16)
        self.history_write: deque[float] = deque([0.0] * 16, maxlen=16)
        self.peak_read: float = 10.0
        self.peak_write: float = 10.0
        self.prev_stats: BlockStats | None = None
        self._title_key: tuple[str, str, str] | None = None

    def generate_sparkline(self, data: deque[float], current_peak: float, width: int = 16,
                           color_hex: str = ACCENT) -> tuple[Text, float]:
        """Build a fixed-width styled sparkline without parsing markup every sample."""
        ticks = " ▂▃▄▅▆▇█"
        visible = list(data)[-width:]
        line = Text(" " * (width - len(visible)), style=SPARK_BASE_COL, overflow="crop")
        if not visible:
            return line, 10.0
        # Smooth peak decay keeps old bursts from abruptly changing the scale.
        new_peak = max(max(visible), current_peak * 0.90, 10.0)
        for value in visible:
            if value <= 0.01:
                line.append(" ", style=SPARK_BASE_COL)
            else:
                index = int(min(value / new_peak, 1.0) ** 0.6 * (len(ticks) - 1))
                line.append(ticks[index], style=color_hex)
        return line, new_peak

    def tick_update(self, curr: BlockStats, meta_info: dict) -> None:
        size = meta_info.get("size", "?")
        dtype = meta_info.get("type", "DISK")
        model = meta_info.get("model", "N/A")

        is_hdd = meta_info.get("rota", False)
        is_zram = self.dev_name.startswith("zram")
        is_compact = is_hdd or is_zram

        smart: SmartInfo = meta_info.get("smart", SmartInfo())

        title_key = (size, dtype, model)
        if title_key != self._title_key:
            self.border_title = (
                f"[bold {FG}]/dev/{self.dev_name}[/]  [{MUTED}]│[/]  "
                f"[{ACCENT}]{size}[/]  [{MUTED}]│[/]  [{SUCCESS}]{dtype}[/]  [{MUTED}]│[/]  [{WARNING}]{escape(model)}[/]"
            )
            self._title_key = title_key

        counters = ("read_ios", "read_sectors", "read_ticks", "write_ios", "write_sectors",
                    "write_ticks", "io_ticks", "discard_ios", "discard_ticks", "flush_ios", "flush_ticks")
        reset = self.prev_stats is not None and any(
            getattr(curr, field) < getattr(self.prev_stats, field) for field in counters
        )
        if reset:
            self.history_read.clear()
            self.history_write.clear()
            self.peak_read = self.peak_write = 10.0
        if self.prev_stats is None or reset:
            self.prev_stats = curr
            r_mb_s = w_mb_s = r_iops = w_iops = await_ms = util_pct = 0.0
        else:
            prev = self.prev_stats
            dt = curr.timestamp - prev.timestamp
            if dt > 0:
                r_mb_s = max(0.0, ((curr.read_sectors - prev.read_sectors) * 512) / dt / 1048576)
                w_mb_s = max(0.0, ((curr.write_sectors - prev.write_sectors) * 512) / dt / 1048576)
                r_iops = max(0.0, (curr.read_ios - prev.read_ios) / dt)
                w_iops = max(0.0, (curr.write_ios - prev.write_ios) / dt)

                total_ios_delta = (
                    max(0, curr.read_ios - prev.read_ios)
                    + max(0, curr.write_ios - prev.write_ios)
                    + max(0, curr.discard_ios - prev.discard_ios)
                    + max(0, curr.flush_ios - prev.flush_ios)
                )
                total_ticks_delta = (
                    max(0, curr.read_ticks - prev.read_ticks)
                    + max(0, curr.write_ticks - prev.write_ticks)
                    + max(0, curr.discard_ticks - prev.discard_ticks)
                    + max(0, curr.flush_ticks - prev.flush_ticks)
                )

                util_pct = max(0.0, min(((curr.io_ticks - prev.io_ticks) / 1000.0) / dt * 100.0, 100.0))
                await_ms = (total_ticks_delta / total_ios_delta) if total_ios_delta > 0 else 0.0

                self.history_read.append(r_mb_s)
                self.history_write.append(w_mb_s)
                self.prev_stats = curr
            else:
                r_mb_s = w_mb_s = r_iops = w_iops = await_ms = util_pct = 0.0

        read_total_str = format_bytes(curr.read_sectors * 512)
        write_total_str = format_bytes(curr.write_sectors * 512)

        # ====================================================================
        # ROCK-SOLID JITTER-FREE FLUID GRID (Fixed Column Metric Anchoring)
        # ====================================================================
        table = Table.grid(padding=(0, 1), expand=True)

        table.add_column("C1_L", justify="left", no_wrap=True, width=10)
        table.add_column("C1_V", justify="left", no_wrap=True, width=10)
        table.add_column("F1", ratio=1)
        table.add_column("C2", justify="left", no_wrap=True, width=25)
        table.add_column("F2", ratio=1)
        table.add_column("C3", justify="left", no_wrap=True, width=16)
        table.add_column("F3", ratio=1)
        table.add_column("C4", justify="left", no_wrap=True, width=17)

        r_spark, self.peak_read = self.generate_sparkline(self.history_read, self.peak_read, width=16, color_hex=SUCCESS)
        w_spark, self.peak_write = self.generate_sparkline(self.history_write, self.peak_write, width=16, color_hex=ACCENT)

        # Diagnostics Color Evaluation (Eliminating False Alarms on Healthy Drives & N/A)
        m_str = str(smart.media_errors).strip()
        if m_str in ("N/A", "?", ""):
            err_col = MUTED
        elif m_str in ("0", "0x0", "0x00") or smart.media_errors == 0:
            err_col = SUCCESS
        else:
            err_col = ERROR

        c_str = str(smart.critical_warning).strip()
        if c_str in ("N/A", "?", ""):
            crit_col = MUTED
        elif c_str in ("0", "0x0", "0x00") or smart.critical_warning == 0:
            crit_col = SUCCESS
        else:
            crit_col = ERROR

        h_str = str(smart.health).strip()
        if h_str == "FAILED":
            health_col = ERROR
        elif h_str in ("N/A", "?", ""):
            health_col = MUTED
        elif h_str.endswith("%"):
            try:
                h_val = int(h_str.rstrip("%"))
                health_col = ERROR if h_val < 30 else (WARNING if h_val < 70 else ACCENT)
            except ValueError:
                health_col = ACCENT
        else:
            health_col = SUCCESS

        u_str = str(smart.unsafe_shutdowns).strip()
        if u_str in ("N/A", "?", ""):
            pwr_cut_col = MUTED
        elif u_str in ("0", "0x0") or smart.unsafe_shutdowns == 0:
            pwr_cut_col = SUCCESS
        else:
            pwr_cut_col = WARNING

        t1_str = str(smart.therm_t1).strip()
        if t1_str in ("N/A", "?", ""):
            t1_col = MUTED
        elif t1_str in ("0s", "0"):
            t1_col = SUCCESS
        else:
            t1_col = WARNING

        util_col = ERROR if util_pct >= 85.0 else (WARNING if util_pct >= 50.0 else FG)
        lat_col = ERROR if await_ms >= 50.0 else (WARNING if await_ms >= 15.0 else FG)

        r_spd = format_rate(r_mb_s * 1048576)
        w_spd = format_rate(w_mb_s * 1048576)
        r_iops_str = f"{r_iops:.1f} IOPS"
        w_iops_str = f"{w_iops:.1f} IOPS"

        r_c4 = (
            f"[{SUCCESS}]{r_iops_str}[/] [{TEMP_COL}]{smart.temp}[/]"
            if (is_compact and smart.temp != "N/A")
            else f"[{SUCCESS}]{r_iops_str:>11}[/]"
        )
        w_c4 = (
            f"[{ACCENT}]{w_iops_str}[/] [bold {lat_col}]{await_ms:.2f} ms[/]"
            if is_compact
            else f"[{ACCENT}]{w_iops_str:>11}[/]"
        )

        # ROW 1 (Read Activity)
        table.add_row(
            f"[{WARNING}]Read:[/]",
            f"[bold {SUCCESS}]{read_total_str}[/]",
            "",
            Text.assemble(Text("READ ", style=f"bold {SUCCESS}"), r_spark),
            "",
            f"[bold {FG}]{r_spd:>10}[/]",
            "",
            r_c4,
        )

        # ROW 2 (Write Activity)
        table.add_row(
            f"[{WARNING}]Write:[/]",
            f"[bold {ACCENT}]{write_total_str}[/]",
            "",
            Text.assemble(Text("WRITE ", style=f"bold {ACCENT}"), w_spark),
            "",
            f"[bold {FG}]{w_spd:>10}[/]",
            "",
            w_c4,
        )

        if not is_compact:
            # ROW 3 (Utilization / Critical / Power Cycles)
            table.add_row(
                f"[{WARNING}]Latency:[/]",
                f"[bold {lat_col}]{await_ms:.2f} ms[/]",
                "",
                f"[{LABEL_COL}]BUSY    [{DIVIDER_COL}]│[/][/] [bold {util_col}]{util_pct:>5.1f}%[/]",
                "",
                f"[{LABEL_COL}]CRITICAL [{DIVIDER_COL}]│[/][/] [bold {crit_col}]{smart.critical_warning:>4}[/]",
                "",
                f"[{LABEL_COL}]PWR CYC [{DIVIDER_COL}]│[/][/] [bold {FG}]{smart.power_cycles:>6}[/]",
            )

            # ROW 4 (Health / Errors / Power Hours)
            table.add_row(
                f"[{SUCCESS}]Total Rd:[/]",
                f"[bold {SUCCESS}]{smart.tbr}[/]",
                "",
                f"[{LABEL_COL}]HEALTH  [{DIVIDER_COL}]│[/][/] [bold {health_col}]{smart.health:>5}[/]",
                "",
                f"[{LABEL_COL}]ERRORS   [{DIVIDER_COL}]│[/][/] [bold {err_col}]{smart.media_errors:>4}[/]",
                "",
                f"[{LABEL_COL}]PWR HRS [{DIVIDER_COL}]│[/][/] [bold {FG}]{smart.power_on_hours:>6}[/]",
            )

            # ROW 5 (Temperature / Thermal Throttle / Power Cuts)
            table.add_row(
                f"[{ACCENT}]Total Wr:[/]",
                f"[bold {ACCENT}]{smart.tbw}[/]",
                "",
                f"[{LABEL_COL}]TEMP    [{DIVIDER_COL}]│[/][/] [bold {TEMP_COL}]{smart.temp:>5}[/]",
                "",
                f"[{LABEL_COL}]T1 TIME  [{DIVIDER_COL}]│[/][/] [bold {t1_col}]{smart.therm_t1:>4}[/]",
                "",
                f"[{LABEL_COL}]PWR CUT [{DIVIDER_COL}]│[/][/] [bold {pwr_cut_col}]{smart.unsafe_shutdowns:>6}[/]",
            )

        self.update(table)


# ============================================================================
# 5. SHORTCUTS & HELP MODAL DIALOG
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
            yield Static("󰌌 Dusky Disk Monitor Shortcuts", id="modal-title")

            text = Text()
            text.append("Drive Navigation (Vim & Keys)\n", style=f"bold {ACCENT}")
            text.append("  j / Down       Select next drive\n")
            text.append("  k / Up         Select previous drive\n")
            text.append("  g / Home       Jump to first drive\n")
            text.append("  G / End        Jump to last drive\n\n")

            text.append("Card Reordering\n", style=f"bold {ACCENT}")
            text.append("  J / Shift+Down Move selected drive down\n")
            text.append("  K / Shift+Up   Move selected drive up\n\n")

            text.append("Actions & Controls\n", style=f"bold {ACCENT}")
            text.append("  s / Sync Btn   Flush dirty page cache to disks (sync)\n")
            text.append("  F1 / ?         Open / close this shortcuts modal\n")
            text.append("  q / Ctrl+C     Quit monitor\n\n")
            text.append("BUSY is kernel active time, not SSD bandwidth saturation.\n")
            text.append("Health % estimates remaining endurance, not overall reliability.\n")
            text.append("Unsupported or ambiguous SMART metrics show N/A.\n")

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


class MetadataLoaded(Message):
    """Transfer a worker-owned metadata snapshot to the UI queue."""

    def __init__(self, metadata: dict[str, dict], *, preserve_smart: bool = False) -> None:
        super().__init__()
        self.metadata = metadata
        self.preserve_smart = preserve_smart


class SyncFinished(Message):
    """Deliver sync completion without waiting for the UI thread."""

    def __init__(self, state: str) -> None:
        super().__init__()
        self.state = state


class IOMonitorApp(App):
    """Dusky Disk I/O Monitor"""
    ENABLE_COMMAND_PALETTE = False

    CSS = f"""
    Screen {{
        background: {BG};
        layout: vertical;
    }}

    #ram_bar {{
        height: 1;
        background: {BG};
        color: {FG};
        padding: 0 1;
    }}

    Button#btn_help {{
        height: 1;
        min-width: 0;
        width: 8;
        border: none;
        background: {ACCENT};
        color: {BG};
        text-style: bold;
        padding: 0;
        margin: 0;
    }}

    Button#btn_help:hover, Button#btn_help:focus {{
        background: {SUCCESS};
        color: {BG};
    }}

    #ram_txt {{
        width: 1fr;
        height: 1;
        text-align: center;
    }}

    Button#btn_sync {{
        height: 1;
        min-width: 0;
        width: 8;
        border: none;
        background: {ACCENT};
        color: {BG};
        text-style: bold;
        padding: 0;
        margin: 0;
    }}

    Button#btn_sync:hover {{
        background: {SUCCESS};
        color: {BG};
    }}

    Button#btn_sync:focus {{
        background: {SUCCESS};
        color: {BG};
    }}

    Button#btn_sync.-syncing {{
        background: {WARNING};
        color: {BG};
    }}

    Button#btn_sync.-synced {{
        background: {SUCCESS};
        color: {BG};
    }}

    ShortcutsScreen {{
        align: center middle;
    }}

    #help_dialog {{
        width: 66;
        height: auto;
        max-height: 85%;
        background: {BG};
        border: heavy {ACCENT};
        padding: 1 2;
    }}

    #modal-title {{
        color: {ACCENT};
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
    }}

    #modal-text {{
        color: {FG};
        margin-bottom: 1;
    }}

    #modal_btn_container {{
        height: 1;
        align-horizontal: center;
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

    #main_scroll {{
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
        scrollbar-size: 1 1; 
        scrollbar-background: {BG};
        scrollbar-color: {MUTED};
        scrollbar-color-hover: {ACCENT};
    }}
    """

    BINDINGS = [
        # Essential keyboard shortcuts
        Binding("f1", "help", "Help", priority=True),
        Binding("question_mark", "help", "Help", priority=True),
        Binding("j", "next_drive", "Select"),
        Binding("k", "prev_drive", "Prev Drive"),
        Binding("s", "sync", "Sync"),
        Binding("q", "quit", "Quit"),

        # Vim / Arrow / Navigation bindings
        Binding("down", "next_drive", "Next Drive", priority=True),
        Binding("up", "prev_drive", "Prev Drive", priority=True),
        Binding("J", "move_down", "Move Down"),
        Binding("K", "move_up", "Move Up"),
        Binding("shift+down", "move_down", "Move Down", priority=True),
        Binding("shift+up", "move_up", "Move Up", priority=True),
        Binding("g", "first_drive", "First Drive"),
        Binding("home", "first_drive", "First Drive", priority=True),
        Binding("G", "last_drive", "Last Drive"),
        Binding("end", "last_drive", "Last Drive", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, dict] = {}
        self.drive_widgets: dict[str, DriveWidget] = {}
        self._metadata_worker: Worker | None = None
        self._syncing = False
        self._tick_lock = asyncio.Lock()
        self.sync_button = Button("󰚰 Sync", id="btn_sync")
        self.ram_text = Static(id="ram_txt")
        self.drive_scroll = VerticalScroll(id="main_scroll")

    def compose(self) -> ComposeResult:
        self.title = "Dusky Disk"
        with Horizontal(id="ram_bar"):
            yield Button("󰌌 F1", id="btn_help")
            yield self.ram_text
            yield self.sync_button
        yield self.drive_scroll

    async def on_mount(self) -> None:
        self.refresh_metadata_worker()
        await self.tick()
        self.set_interval(1.0, self.tick)
        self.set_interval(5.0, self.refresh_metadata_worker)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_sync":
            self.action_sync()
        elif event.button.id == "btn_help":
            self.action_help()

    def action_sync(self) -> None:
        if self._syncing:
            return
        self._syncing = True
        self._set_sync_state("syncing")
        self.do_sync()

    @work(thread=True, group="sync")
    def do_sync(self) -> None:
        """Flush dirty pages without blocking the UI or claiming success on failure."""
        worker = get_current_worker()
        try:
            os.sync()
        except OSError:
            state = "failed"
        else:
            state = "synced"
        if not worker.is_cancelled:
            self.post_message(SyncFinished(state))

    def on_sync_finished(self, event: SyncFinished) -> None:
        self._finish_sync(event.state)

    def _finish_sync(self, state: str) -> None:
        self._set_sync_state(state)
        self.set_timer(1.8, lambda: self._set_sync_state("idle"))

    def _set_sync_state(self, state: str) -> None:
        btn = self.sync_button
        btn.set_class(state == "syncing", "-syncing")
        btn.set_class(state == "synced", "-synced")
        btn.disabled = state != "idle"
        btn.label = {"syncing": "󱑂 Syncing", "synced": "󰄬 Synced!",
                     "failed": "Failed!", "idle": "󰚰 Sync"}[state]
        if state == "idle":
            self._syncing = False

    def refresh_metadata_worker(self) -> None:
        # Cancelling a Textual thread worker does not stop its blocking subprocesses.
        # Skip a timer tick while a query is running, rather than overlap old/new work.
        if self._metadata_worker is None or self._metadata_worker.is_finished:
            self._metadata_worker = self._fetch_metadata()

    @work(thread=True, group="metadata")
    def _fetch_metadata(self) -> None:
        worker = get_current_worker()
        basic_meta = SysStatParser.get_basic_metadata()
        if basic_meta is None or worker.is_cancelled:
            return
        # Publish topology before slow SMART commands finish. Copy the mutable
        # records so the worker never mutates a dictionary being read by the UI.
        snapshot = {name: info.copy() for name, info in basic_meta.items()}
        if not self.post_message(MetadataLoaded(snapshot, preserve_smart=True)):
            return
        if worker.is_cancelled:
            return
        new_meta = SysStatParser.get_device_metadata(basic_meta)
        if new_meta is not None and not worker.is_cancelled:
            self.post_message(MetadataLoaded(new_meta))

    def on_metadata_loaded(self, event: MetadataLoaded) -> None:
        self._update_meta(event.metadata, event.preserve_smart)

    def _update_meta(self, new_meta: dict[str, dict] | None, preserve_smart: bool = False) -> None:
        if new_meta is not None:
            if preserve_smart:
                for name, info in new_meta.items():
                    previous = self.meta.get(name, {})
                    if previous.get("model") == info.get("model"):
                        info["smart"] = previous.get("smart", SmartInfo())
            self.meta = new_meta

    # ========================================================================
    # CIRCULAR NAVIGATION (Loops seamlessly top-to-bottom and bottom-to-top)
    # ========================================================================
    def action_next_drive(self) -> None:
        drives = list(self.drive_scroll.query(DriveWidget))
        if not drives:
            return
        focused = self.focused
        if focused in drives:
            idx = drives.index(focused)
            next_idx = (idx + 1) % len(drives)
            target = drives[next_idx]
        else:
            target = drives[0]
        target.focus()
        target.scroll_visible()

    def action_prev_drive(self) -> None:
        drives = list(self.drive_scroll.query(DriveWidget))
        if not drives:
            return
        focused = self.focused
        if focused in drives:
            idx = drives.index(focused)
            prev_idx = (idx - 1 + len(drives)) % len(drives)
            target = drives[prev_idx]
        else:
            target = drives[-1]
        target.focus()
        target.scroll_visible()

    def action_first_drive(self) -> None:
        drives = list(self.drive_scroll.query(DriveWidget))
        if drives:
            drives[0].focus()
            drives[0].scroll_visible()

    def action_last_drive(self) -> None:
        drives = list(self.drive_scroll.query(DriveWidget))
        if drives:
            drives[-1].focus()
            drives[-1].scroll_visible()

    # ========================================================================
    # CARD REORDERING (Move up/down with circular wrapping)
    # ========================================================================
    def action_move_down(self) -> None:
        focused = self.focused
        if isinstance(focused, DriveWidget):
            scroll = self.drive_scroll
            children = [c for c in scroll.children if isinstance(c, DriveWidget)]
            if len(children) > 1:
                idx = children.index(focused)
                if idx < len(children) - 1:
                    scroll.move_child(focused, after=children[idx + 1])
                else:
                    scroll.move_child(focused, before=children[0])
                focused.scroll_visible()

    def action_move_up(self) -> None:
        focused = self.focused
        if isinstance(focused, DriveWidget):
            scroll = self.drive_scroll
            children = [c for c in scroll.children if isinstance(c, DriveWidget)]
            if len(children) > 1:
                idx = children.index(focused)
                if idx > 0:
                    scroll.move_child(focused, before=children[idx - 1])
                else:
                    scroll.move_child(focused, after=children[-1])
                focused.scroll_visible()

    def action_help(self) -> None:
        """Toggles the shortcuts and help modal dialog."""
        if isinstance(self.screen, ModalScreen):
            self.screen.dismiss(None)
        else:
            self.push_screen(ShortcutsScreen())

    async def tick(self) -> None:
        async with self._tick_lock:
            dirty, wb = SysStatParser.get_ram_buffers()
            wb_col = ERROR if wb > 50.0 else (WARNING if wb > 0.0 else SUCCESS)
            dirty_col = WARNING if dirty > 500.0 else ACCENT
            self.ram_text.update(Text.from_markup(
                f"[{LABEL_COL}]Dirty:[/] [bold {dirty_col}]{dirty:.1f} MiB[/]    "
                f"[bold {BG} on {SUCCESS}] Dusky Disk [/]    "
                f"[{LABEL_COL}]Writeback:[/] [bold {wb_col}]{wb:.1f} MiB[/]"
            ))
            try:
                current_drives = sorted(
                    dev for dev in os.listdir("/sys/block")
                    if not dev.startswith(("loop", "sr", "ram", "dm", "fd", "nbd"))
                    and (not dev.startswith("zram") or SysStatParser.is_zram_active(dev))
                )
            except OSError:
                return  # A transient discovery failure is not a mass disconnect.

            is_initial = not self.drive_widgets
            for dev in self.drive_widgets.keys() - set(current_drives):
                await self.drive_widgets.pop(dev).remove()
                self.meta.pop(dev, None)

            new_drives_added = False
            for dev in current_drives:
                if dev not in self.drive_widgets:
                    # IDs are opaque and collision-free even for unusual kernel device names.
                    widget = DriveWidget(id=f"drive_{dev.encode().hex()}", dev_name=dev)
                    await self.drive_scroll.mount(widget)
                    self.drive_widgets[dev] = widget
                    new_drives_added = True
            if new_drives_added:
                self.refresh_metadata_worker()
            if is_initial and self.drive_widgets:
                next(iter(self.drive_widgets.values())).focus()

            for dev, widget in self.drive_widgets.items():
                if (curr := SysStatParser.get_block_stats(dev)) is not None:
                    widget.tick_update(curr, self.meta.get(dev, {}))


if __name__ == "__main__":
    ensure_smart_access()
    app = IOMonitorApp()
    try:
        app.run()
    finally:
        _sudo_keepalive_stop.set()
