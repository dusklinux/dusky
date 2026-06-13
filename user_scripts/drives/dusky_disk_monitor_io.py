#!/usr/bin/env python3

"""
Dusky Disk Real-Time System I/O Monitor (Hyper-Sleek Final Edition)
Zero-stutter background polling, solid sleek borders, Matugen theme integration,
and strictly aligned dense NVMe SMART diagnostics.
"""

import os
import sys
import time
import json
import subprocess
import shutil
import re
from pathlib import Path
from collections import deque
from dataclasses import dataclass

# ============================================================================
# 1. AGGRESSIVE DEPENDENCY MANAGEMENT & AUTHENTICATION
# ============================================================================
def ensure_dependencies():
    """Checks for required Python libraries and installs natively via Pacman."""
    missing = []
    try: import textual
    except ImportError: missing.append("python-textual")
    try: import rich
    except ImportError: missing.append("python-rich")
        
    if shutil.which("lsblk") is None: missing.append("util-linux")
    if shutil.which("nvme") is None: missing.append("nvme-cli")

    if missing:
        print(f"\n[!] Missing absolute dependencies: {', '.join(missing)}")
        print("[*] Escalating privileges to install via pacman (requires sudo password)...\n")
        cmd = ["sudo", "pacman", "-S", "--needed", "--noconfirm"] + missing
        try:
            subprocess.run(cmd, check=True)
            print("\n[*] Dependencies installed successfully. Initializing engine...\n")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except subprocess.CalledProcessError as e:
            print(f"\n[!] Critical Failure: Dependency installation aborted. (Code: {e.returncode})", file=sys.stderr)
            sys.exit(1)

def ensure_smart_access():
    """Prompts for sudo upfront so background NVMe queries run seamlessly."""
    if os.geteuid() != 0:
        if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
            print("\n[!] Advanced NVMe SMART diagnostics require administrative privileges.")
            print("[*] Please authenticate to enable full telemetry (Temp, TBW, Health, etc):\n")
            try:
                subprocess.run(["sudo", "-v"], check=True)
                print("\n[*] Diagnostics unlocked. Engaging monitors...\n")
            except subprocess.CalledProcessError:
                print("\n[!] Warning: Authentication skipped. SMART metrics will show N/A.")
                time.sleep(2)
            except KeyboardInterrupt:
                print("\n[!] Authentication cancelled. Exiting.")
                sys.exit(0)

ensure_dependencies()
ensure_smart_access()

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Header, Footer, Static
from textual import work
from rich.table import Table
from rich.text import Text

# ============================================================================
# 2. DYNAMIC MATUGEN THEME COMPILER
# ============================================================================
def load_theme() -> dict:
    """Loads the user's Matugen-generated theme with bulletproof fallback mechanisms."""
    path = Path("~/.config/matugen/generated/dusky_tui.json").expanduser()
    defaults = {
        "bg": "#0e1416",
        "fg": "#dee3e5",
        "accent": "#82d3e2",
        "error": "#ffb4ab",
        "warning": "#b1cbd0",
        "success": "#bbc5ea",
        "muted": "#3f484a"
    }
    if path.exists():
        try:
            with open(path, "r") as f:
                user_theme = json.load(f)
                for k, v in defaults.items():
                    if k not in user_theme:
                        user_theme[k] = v
                return user_theme
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

# ============================================================================
# 3. CORE SYSTEM METRICS ENGINE
# ============================================================================
@dataclass
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
    discard_ios: int
    discard_sectors: int
    discard_ticks: int

@dataclass
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
        if not path.exists(): return None
        try:
            with open(path, "r") as f: fields = f.read().split()
            return BlockStats(
                timestamp=time.perf_counter(),
                read_ios=int(fields[0]), read_sectors=int(fields[2]), read_ticks=int(fields[3]),
                write_ios=int(fields[4]), write_sectors=int(fields[6]), write_ticks=int(fields[7]),
                in_flight=int(fields[8]), io_ticks=int(fields[9]), time_in_queue=int(fields[10]),
                discard_ios=int(fields[11]) if len(fields) > 11 else 0,
                discard_sectors=int(fields[13]) if len(fields) > 13 else 0,
                discard_ticks=int(fields[14]) if len(fields) > 14 else 0,
            )
        except (IndexError, ValueError, IOError): return None

    @staticmethod
    def _get_smartctl_data(device: str) -> SmartInfo:
        info = SmartInfo()
        try:
            cmd = ["sudo", "-n", "smartctl", "-A", f"/dev/{device}"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if res.returncode == 0 or res.returncode == 32:
                for line in res.stdout.splitlines():
                    if "Temperature_Celsius" in line:
                        parts = line.split()
                        if parts:
                            raw = parts[-1]
                            if raw.isdigit() and 0 < int(raw) < 200:
                                info.temp = f"{raw}°"
                    elif "Reallocated_Sector_Ct" in line:
                        parts = line.split()
                        if parts:
                            info.media_errors = parts[-1]

            cmd_h = ["sudo", "-n", "smartctl", "-H", f"/dev/{device}"]
            res_h = subprocess.run(cmd_h, capture_output=True, text=True, timeout=3)
            out_h = res_h.stdout
            if "PASSED" in out_h:
                info.health = "PASSED"
            elif "FAILED" in out_h:
                info.health = "FAILED"
        except Exception:
            pass
        return info

    @staticmethod
    def get_smart_data(device: str) -> SmartInfo:
        info = SmartInfo()
        # Parse standard NVMe namespace (e.g. nvme0n1) directly to controller (nvme0)
        match = re.match(r"(nvme\d+)", device)
        if match:
            ctrl = match.group(1)
            try:
                cmd = ["sudo", "-n", "nvme", "smart-log", f"/dev/{ctrl}"]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
                
                if res.returncode == 0:
                    temp_base = "N/A"
                    t_sensors = []
                    for line in res.stdout.splitlines():
                        line = line.strip()
                        if not line or ":" not in line: continue
                        
                        key, val = [p.strip() for p in line.split(":", 1)]
                        
                        if key == "temperature":
                            temp_base = val.split("(")[0].strip().replace(" ", "")
                        elif key.startswith("Temperature Sensor"):
                            t_sensors.append(val.split("(")[0].strip().replace(" ", ""))
                        elif key == "percentage_used":
                            clean_val = val.replace("%", "").strip()
                            try: info.health = f"{100 - int(clean_val)}%"
                            except ValueError: pass
                        elif key == "Data Units Read":
                            if "(" in val: info.tbr = val.split("(")[1].replace(")", "").strip()
                            else: info.tbr = val
                        elif key == "Data Units Written":
                            if "(" in val: info.tbw = val.split("(")[1].replace(")", "").strip()
                            else: info.tbw = val
                        elif key == "power_cycles":
                            info.power_cycles = val
                        elif key == "power_on_hours":
                            info.power_on_hours = val
                        elif key == "unsafe_shutdowns":
                            info.unsafe_shutdowns = val
                        elif key == "media_errors":
                            info.media_errors = val
                        elif key == "critical_warning":
                            info.critical_warning = val
                        elif key == "Thermal Management T1 Total Time":
                            info.therm_t1 = val
                            
                    temps = []
                    if temp_base != "N/A":
                        temps.append(temp_base)
                    temps.extend(t_sensors[:2])
                    
                    if temps:
                        info.temp = "│".join(temps)
                    else:
                        info.temp = "N/A"
            except Exception: pass
        else:
            info = SysStatParser._get_smartctl_data(device)
            
        return info

    @staticmethod
    def get_ram_buffers() -> tuple[float, float]:
        dirty = writeback = 0.0
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("Dirty:"): dirty = float(line.split()[1]) / 1024.0
                    elif line.startswith("Writeback:"): writeback = float(line.split()[1]) / 1024.0
        except IOError: pass
        return dirty, writeback

    @staticmethod
    def get_device_metadata() -> dict:
        try:
            # Added ROTA to positively identify rotational hard drives
            res = subprocess.run(["lsblk", "-J", "-d", "-o", "NAME,SIZE,TYPE,MODEL,ROTA"], capture_output=True, text=True, check=True)
            data = json.loads(res.stdout)
            meta = {}
            for dev in data.get("blockdevices", []):
                name = dev.get("name")
                if not name or name.startswith(("loop", "sr", "ram", "dm", "fd")): 
                    continue
                
                model = dev.get("model")
                clean_model = str(model).strip() if model else "N/A"
                
                rota_val = dev.get("rota")
                is_hdd = str(rota_val).strip() in ("1", "true", "True") if rota_val is not None else False
                
                meta[name] = {
                    "size": dev.get("size", "?").strip(),
                    "type": dev.get("type", "?").upper().strip(),
                    "model": clean_model,
                    "rota": is_hdd,
                    "smart": SysStatParser.get_smart_data(name)
                }
            return meta
        except Exception: return {}


# ============================================================================
# 4. TEXTUAL WIDGETS & UI
# ============================================================================

class DriveWidget(Static, can_focus=True):
    
    # Height set to auto to dynamically collapse for HDDs and ZRAMs
    DEFAULT_CSS = f"""
    DriveWidget {{
        border: solid {MUTED};
        background: {BG};
        height: auto;
        margin: 0 1 1 1;
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
        self.history_read = deque([0.0] * 16, maxlen=16)
        self.history_write = deque([0.0] * 16, maxlen=16)
        self.prev_stats = None

    def on_mount(self):
        self.border_title = f"[bold {FG}]/dev/{self.dev_name}[/]"
        
    def generate_sparkline(self, data: deque, width: int = 16, color_hex: str = ACCENT) -> str:
        ticks = " ▂▃▄▅▆▇█"
        valid_data = list(data)
        
        if not valid_data: 
            return f"[{MUTED}]" + "_" * width + "[/]"
        
        max_val = max(valid_data)
        line = ""
        for v in valid_data[-width:]:
            if v <= 0.01:
                line += f"[{MUTED}]_[/]"
            else:
                idx = int((v / max_val) * (len(ticks) - 1))
                idx = max(0, min(idx, len(ticks) - 1))
                line += f"[{color_hex}]{ticks[idx]}[/]"
        return line

    def tick_update(self, curr: BlockStats, meta_info: dict):
        size = meta_info.get('size', '?')
        dtype = meta_info.get('type', '?')
        model = meta_info.get('model', 'N/A')
        
        # Determine if view should be compact (for HDDs or ZRAMs)
        is_hdd = meta_info.get('rota', False)
        is_zram = self.dev_name.startswith("zram")
        is_compact = is_hdd or is_zram
        
        smart = meta_info.get('smart', SmartInfo())
        
        self.border_title = f"[bold {FG}]/dev/{self.dev_name}[/]  [{MUTED}]│[/]  [{ACCENT}]{size}[/]  [{MUTED}]│[/]  [{SUCCESS}]{dtype}[/]  [{MUTED}]│[/]  [{WARNING}]{model}[/]"

        if not self.prev_stats:
            self.prev_stats = curr
            return
            
        prev = self.prev_stats
        dt = curr.timestamp - prev.timestamp
        if dt <= 0: return
        
        # Metrics Calculations
        r_mb_s = ((curr.read_sectors - prev.read_sectors) * 512) / dt / 1048576
        w_mb_s = ((curr.write_sectors - prev.write_sectors) * 512) / dt / 1048576
        r_iops = (curr.read_ios - prev.read_ios) / dt
        w_iops = (curr.write_ios - prev.write_ios) / dt
        
        total_ios_delta = (curr.read_ios - prev.read_ios) + (curr.write_ios - prev.write_ios) + (curr.discard_ios - prev.discard_ios)
        total_ticks_delta = (curr.read_ticks - prev.read_ticks) + (curr.write_ticks - prev.write_ticks) + (curr.discard_ticks - prev.discard_ticks)
        
        util_pct = min(((curr.io_ticks - prev.io_ticks) / 1000.0) / dt * 100.0, 100.0)
        await_ms = (total_ticks_delta / total_ios_delta) if total_ios_delta > 0 else 0.0

        self.history_read.append(r_mb_s)
        self.history_write.append(w_mb_s)
        self.prev_stats = curr

        read_mb = (curr.read_sectors * 512) / 1048576
        write_mb = (curr.write_sectors * 512) / 1048576

        # Enforced fixed widths and no_wrap=True completely prevent UI shifting/wrapping
        # Columns recalibrated to precisely match the 46-char optical width of the original script.
        table = Table.grid(expand=True, padding=(0, 1))
        table.add_column("LeftLabel", width=10, no_wrap=True)
        table.add_column("LeftValue", width=11, justify="right", no_wrap=True)
        table.add_column("Pad", ratio=1)
        table.add_column("R1_Lbl", width=8, justify="right", no_wrap=True)
        table.add_column("R1_Val", width=17, justify="left", no_wrap=True)
        table.add_column("R2_Lbl", width=12, justify="right", no_wrap=True)
        table.add_column("R2_Val", width=9, justify="left", no_wrap=True)
        table.add_column("R3_Lbl", width=12, justify="right", no_wrap=True)
        table.add_column("R3_Val", width=9, justify="left", no_wrap=True)

        r_spark = self.generate_sparkline(self.history_read, width=16, color_hex=ACCENT)
        w_spark = self.generate_sparkline(self.history_write, width=16, color_hex=SUCCESS)

        err_col = SUCCESS if str(smart.media_errors) == "0" else ERROR
        crit_col = SUCCESS if str(smart.critical_warning) == "0" else ERROR

        r_spd = f"{r_mb_s:.2f} MB/s"
        w_spd = f"{w_mb_s:.2f} MB/s"
        r_iops_str = f"{r_iops:.1f} IOPS"
        w_iops_str = f"{w_iops:.1f} IOPS"

        # Tucked latency specifically for HDDs and ZRAMs to keep UI slim
        latency_val = f"[bold {ERROR}]{await_ms:>5.2f} ms[/]" if is_compact else ""

        # Compact temp: show on Read row (blank space above latency)
        compact_temp = ""
        if is_compact and smart.temp != "N/A":
            compact_temp = f"[{WARNING}]{smart.temp}[/]"

        # Row 1: Active Read Stats (Speeds shifted left into R2/R3 labels to close gap)
        table.add_row(
            f"[{WARNING}]Read:[/]", f"[bold {SUCCESS}]{read_mb:>8.1f} MB[/]", "",
            f"[bold {ACCENT}]READ[/]", f"{r_spark}",
            f"[bold {FG}]{r_spd}[/]", "",
            f"{r_iops_str}", f"{compact_temp}"
        )
        
        # Row 2: Active Write Stats
        table.add_row(
            f"[{WARNING}]Write:[/]", f"[bold {SUCCESS}]{write_mb:>8.1f} MB[/]", "",
            f"[bold {SUCCESS}]WRITE[/]", f"{w_spark}",
            f"[bold {FG}]{w_spd}[/]", "",
            f"{w_iops_str}", latency_val
        )
        
        # Exclude remaining SMART fields for HDDs and ZRAMs entirely
        if not is_compact:
            # Row 3: Latency, Utilization %, Critical, Power Cycles
            table.add_row(
                f"[{WARNING}]Latency:[/]", f"[bold {ERROR}]{await_ms:>5.2f} ms[/]", "",
                f"[{MUTED}]UTIL[/]", f"[{MUTED}]│[/] [bold {ERROR}]{util_pct:.1f}%[/]",
                f"[{MUTED}]CRITICAL[/]", f"[{MUTED}]│[/] [bold {crit_col}]{smart.critical_warning}[/]",
                f"[{MUTED}]PWR CYC[/]", f"[{MUTED}]│[/] [{FG}]{smart.power_cycles}[/]"
            )

            # Row 4: Total Read, Health %, Errors, Power Hours
            table.add_row(
                f"[{WARNING}]Total Rd:[/]", f"[bold {ACCENT}]{smart.tbr}[/]", "",
                f"[{MUTED}]HEALTH[/]", f"[{MUTED}]│[/] [bold {SUCCESS}]{smart.health}[/]",
                f"[{MUTED}]ERRORS[/]", f"[{MUTED}]│[/] [bold {err_col}]{smart.media_errors}[/]",
                f"[{MUTED}]PWR HRS[/]", f"[{MUTED}]│[/] [{FG}]{smart.power_on_hours}[/]"
            )

            # Row 5: Total Write, Temp(s), T1 Time, Power Cuts
            table.add_row(
                f"[{WARNING}]Total Wr:[/]", f"[bold {ACCENT}]{smart.tbw}[/]", "",
                f"[{MUTED}]TEMP[/]", f"[{MUTED}]│[/] [{WARNING}]{smart.temp}[/]",
                f"[{MUTED}]T1 TIME[/]", f"[{MUTED}]│[/] [{WARNING}]{smart.therm_t1}[/]",
                f"[{MUTED}]PWR CUT[/]", f"[{MUTED}]│[/] [{ERROR}]{smart.unsafe_shutdowns}[/]"
            )

        self.update(table)


class IOMonitorApp(App):
    """Dusky Disk I/O Monitor"""

    # Natively disables the ^P palette menu, the left icon, and theming capabilities completely.
    ENABLE_COMMAND_PALETTE = False

    CSS = f"""
    Screen {{
        background: {BG};
        layout: vertical;
    }}
    
    #custom_header {{
        height: 1;
        background: {MUTED};
        color: {FG};
        text-align: center;
    }}

    #ram_bar {{
        height: 1;
        background: {MUTED};
        color: {FG};
        padding: 0 2;
    }}

    #main_scroll {{
        height: 1fr;
        padding: 1 1;
        overflow-y: auto;
        scrollbar-size: 1 1; 
        scrollbar-background: {BG};
        scrollbar-color: {MUTED};
        scrollbar-color-hover: {ACCENT};
    }}

    Footer {{
        background: {BG};
        color: {FG};
    }}

    Footer > .footer--key {{
        color: {ACCENT};
    }}

    Footer > .footer--highlight {{
        background: {ACCENT};
        color: {BG};
    }}
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("j", "focus_next", "Focus Next"),
        ("k", "focus_previous", "Focus Prev"),
        ("J", "move_down", "Move Drive Down"),
        ("K", "move_up", "Move Drive Up"),
    ]

    def __init__(self):
        super().__init__()
        self.meta = {}
        self.mounted_drives = set()
        
        try: self.kernel = os.uname().release
        except Exception: self.kernel = "Rolling"

    def compose(self) -> ComposeResult:
        self.title = "Dusky Disk I/O Monitor"
        # Using a flat static component ensures exactly 1 line of height with zero interactivity
        yield Static("Dusky Disk I/O Monitor", id="custom_header")
        yield Static(id="ram_bar")
        yield VerticalScroll(id="main_scroll")
        yield Footer()

    def on_mount(self):
        self.refresh_metadata_worker() # Fire instantly on load
        self.set_interval(1.0, self.tick)
        self.set_interval(5.0, self.refresh_metadata_worker)

    @work(thread=True, exclusive=True)
    def refresh_metadata_worker(self):
        """Asynchronous worker to prevent UI stutters when calling lsblk."""
        new_meta = SysStatParser.get_device_metadata()
        self.call_from_thread(self._update_meta, new_meta)
        
    def _update_meta(self, new_meta):
        self.meta = new_meta

    def action_move_down(self) -> None:
        focused = self.focused
        if isinstance(focused, DriveWidget):
            parent = focused.parent
            children = list(parent.children)
            idx = children.index(focused)
            if idx < len(children) - 1:
                parent.move_child(focused, after=children[idx+1])
                focused.scroll_visible()

    def action_move_up(self) -> None:
        focused = self.focused
        if isinstance(focused, DriveWidget):
            parent = focused.parent
            children = list(parent.children)
            idx = children.index(focused)
            if idx > 0:
                parent.move_child(focused, before=children[idx-1])
                focused.scroll_visible()

    def tick(self):
        # 1. Update Clean RAM Bar with dynamic styling
        dirty, wb = SysStatParser.get_ram_buffers()
        ram_txt = Text.from_markup(
            f"[bold {FG}]SYSTEM MEMORY[/]  [{BG}]│[/]  "
            f"[{FG}]Dirty Pages (Wait):[/] [bold {ACCENT}]{dirty:>6.1f} MB[/]  [{BG}]│[/]  "
            f"[{FG}]Writeback (Active):[/] [bold {ERROR}]{wb:>6.1f} MB[/]"
        )
        self.query_one("#ram_bar", Static).update(ram_txt)

        # 2. Hardware Mount Sync
        current_drives = []
        try:
            current_drives = [d for d in os.listdir("/sys/block") if not d.startswith(("loop", "sr", "ram", "dm", "fd"))]
            current_drives.sort()
        except Exception: pass

        scroll_area = self.query_one("#main_scroll", VerticalScroll)
        is_initial = len(self.mounted_drives) == 0

        for dev in list(self.mounted_drives):
            if dev not in current_drives:
                try: self.query_one(f"#drive_{dev}").remove()
                except Exception: pass
                self.mounted_drives.remove(dev)

        for dev in current_drives:
            if dev not in self.mounted_drives:
                scroll_area.mount(DriveWidget(id=f"drive_{dev}", dev_name=dev))
                self.mounted_drives.add(dev)

        # Focus first widget on initial load
        if is_initial and current_drives:
            def focus_first():
                widgets = self.query(DriveWidget)
                if widgets:
                    widgets.first().focus()
            self.call_later(focus_first)

        # 3. Dispatch Stats to Widgets
        for widget in self.query(DriveWidget):
            curr = SysStatParser.get_block_stats(widget.dev_name)
            if curr:
                meta_info = self.meta.get(widget.dev_name, {})
                widget.tick_update(curr, meta_info)

if __name__ == "__main__":
    app = IOMonitorApp()
    app.run()
