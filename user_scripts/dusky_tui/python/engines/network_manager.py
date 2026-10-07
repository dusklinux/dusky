import os
import sys
import time
lazy import math
lazy import json
import re
lazy import secrets
lazy import uuid as uuid_module
lazy import shutil
import logging
lazy import subprocess
lazy import threading
lazy import tempfile
lazy import select
lazy import termios
lazy import tty
lazy import urllib.parse
lazy import concurrent.futures
lazy from collections import Counter
from dataclasses import dataclass
from pathlib import Path
lazy from typing import Any

from python.frontend.core_types import BaseEngine, ConfigItem

lazy from rich.console import Console
lazy from rich.live import Live
lazy from rich.text import Text
lazy from rich.table import Table
lazy from rich.panel import Panel
lazy from rich.align import Align

logger = logging.getLogger("dusky_network_engine")
HOTSPOT_PROFILE = "Dusky Hotspot"
ROUTE_CHOICE_FILE = Path.home() / ".config/dusky/settings/network/internet_source.json"
HOTSPOT_PREVIOUS_FILE = Path.home() / ".config/dusky/settings/network/hotspot_previous.json"
PREFERRED_ROUTE_METRIC = 1


@dataclass(frozen=True, slots=True)
class NetworkWriteResult:
    ok: bool
    message: str
    actual: str | None = None


def escape_markdown(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+.!<>-])", r"\\\1", value)


def command_failure(exc: BaseException) -> str:
    """Describe timeouts without exposing command arguments such as passwords."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"Command timed out after {exc.timeout} seconds."
    return str(exc)

# =============================================================================
#  NMCLI OUTPUT PARSER & DECODERS
# =============================================================================
def _split_nmcli_line(line: str) -> list[str]:
    """Split an nmcli -t output line by unescaped colons, then unescape fields."""
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line:
        if escaped:
            if char not in (":", "\\"):
                current.append("\\")
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields

def decode_iw_ssid(value: str) -> str:
    """
    Decodes raw hex byte escape sequences from `iw` output (e.g. \\xe2\\x80\\x99 -> ’,
    \\xf0\\x9f\\x98\\x80 -> 😀, \\x20 -> space, \\x5c -> \\) into clean UTF-8.
    Preserves ASCII control characters (< 32 or 127) as escapes to prevent terminal issues.
    """
    raw = str(value or "")
    if "\\x" not in raw:
        return raw

    try:
        encoded = ""
        i = 0
        n = len(raw)
        while i < n:
            if raw[i] == "\\" and i + 3 < n and raw[i + 1] == "x":
                hex_str = raw[i + 2:i + 4]
                try:
                    byte_val = int(hex_str, 16)
                    if byte_val < 32 or byte_val == 127:
                        encoded += urllib.parse.quote(raw[i:i + 4])
                    else:
                        encoded += f"%{hex_str}"
                    i += 4
                    continue
                except ValueError:
                    pass
            encoded += urllib.parse.quote(raw[i])
            i += 1

        try:
            return urllib.parse.unquote(encoded, errors="strict")
        except UnicodeDecodeError:
            return raw
    except Exception:
        return raw

# =============================================================================
#  PURE MODEL FUNCTIONS (Ported directly from Model.js)
# =============================================================================

def parse_network_status(raw: str) -> dict[str, Any]:
    parts = (raw or "disconnected\t\t\t").rstrip("\r\n").split("\t")
    kind = parts[0] if len(parts) > 0 and parts[0] else "disconnected"
    label = parts[1] if len(parts) > 1 else ""
    try:
        signal_strength = int(parts[2]) if len(parts) > 2 and parts[2] != "" else -1
    except ValueError:
        signal_strength = -1
    frequency = parts[3] if len(parts) > 3 else ""
    return {
        "kind": kind,
        "label": label,
        "signal_strength": signal_strength,
        "frequency": frequency
    }

def wifi_icon_for(strength: int) -> str:
    icons = ["󰤯", "󰤟", "󰤢", "󰤥", "󰤨"]
    idx = max(0, min(4, math.ceil(strength / 20) - 1))
    return icons[idx]

def connection_icon(kind: str, signal_strength: int) -> str:
    if kind == "wifi":
        return wifi_icon_for(signal_strength)
    if kind == "ethernet":
        return "󰈀"
    return "󰤮"

def format_header_speed(mbps: str | int | float) -> str:
    try:
        v = int(float(mbps))
    except (ValueError, TypeError):
        return ""
    if v <= 0:
        return ""
    if v >= 1000:
        val = v / 1000.0
        return f"{val:.0f}gbit" if v % 1000 == 0 else f"{val:.1f}gbit"
    return f"{v}mbit"

def format_header_freq(mhz: str | int | float) -> str:
    try:
        v = float(mhz)
    except (ValueError, TypeError):
        return ""
    if not v or v <= 0:
        return ""
    if 2400 <= v < 2500:
        return "2.4ghz"
    if 4900 <= v < 5925:
        return "5ghz"
    if 5925 <= v < 7125:
        return "6ghz"
    if 57000 <= v < 71000:
        return "60ghz"
    ghz = v / 1000.0
    return f"{ghz:.0f}ghz" if ghz % 1 == 0 else f"{ghz:.1f}ghz"

def band_for_freq(mhz: str | int | float) -> str:
    """Classifies frequency in MHz into band: '2.4', '5', '6', '60' or ''."""
    try:
        s = str(mhz).split()[0]
        v = float(s)
    except (ValueError, TypeError, IndexError):
        return ""
    if not v or v <= 0:
        return ""
    if 2400 <= v < 2500:
        return "2.4"
    if 4900 <= v < 5925:
        return "5"
    if 5925 <= v < 7125:
        return "6"
    if 57000 <= v < 71000:
        return "60"
    return ""

def nm_band_for(band_str: str) -> str:
    """Maps human-readable band string to NetworkManager 802-11-wireless.band value."""
    b = str(band_str).lower().replace("ghz", "").strip()
    if b == "2.4":
        return "bg"
    elif b == "5":
        return "a"
    elif b == "6":
        return "6GHz"
    elif b in ("auto", "none", ""):
        return ""
    return ""

def band_from_nm(nm_band: str) -> str:
    """Maps NetworkManager 802-11-wireless.band value to human-readable band string."""
    b = str(nm_band or "").strip()
    if b == "bg":
        return "2.4"
    elif b == "a":
        return "5"
    elif b in ("6GHz", "6"):
        return "6"
    return "auto"

def band_label(band: str) -> str:
    """Formats band string for UI display."""
    b = str(band or "").strip().lower()
    if b in ("auto", ""):
        return "Auto"
    return f"{b} GHz"

def band_section_title(selected: str, current: str) -> str:
    if selected != "auto":
        return "WI-FI BAND"
    lbl = band_label(current)
    if not lbl or lbl == "Auto":
        return "WI-FI BAND"
    return f"WI-FI BAND: {lbl.upper()}"

def header_detail(info: dict[str, Any]) -> str:
    val = info or {}
    t = val.get("type", "")
    if t == "ethernet":
        return format_header_speed(val.get("speed", ""))
    if t == "wifi":
        return format_header_freq(val.get("freq", ""))
    return ""

def throughput_state(previous: dict[str, Any] | None, next_sample: dict[str, Any] | None, now: float) -> dict[str, Any]:
    prev = previous or {}
    sample = next_sample or {}
    iface = sample.get("iface", "")
    try:
        rx = float(sample.get("rx_bytes", "0"))
    except ValueError:
        rx = 0.0
    try:
        tx = float(sample.get("tx_bytes", "0"))
    except ValueError:
        tx = 0.0

    prev_time = float(prev.get("prev_sample_time", 0))

    if iface != prev.get("prev_iface", "") or prev_time == 0:
        return {
            "prev_iface": iface,
            "prev_rx_bytes": rx,
            "prev_tx_bytes": tx,
            "prev_sample_time": now,
            "download_rate": 0.0,
            "upload_rate": 0.0,
            "total_rx": rx,
            "total_tx": tx,
        }

    dl_rate = float(prev.get("download_rate", 0.0))
    ul_rate = float(prev.get("upload_rate", 0.0))
    dt = now - prev_time

    if dt > 0:
        dl_rate = max(0.0, (rx - float(prev.get("prev_rx_bytes", 0))) / dt)
        ul_rate = max(0.0, (tx - float(prev.get("prev_tx_bytes", 0))) / dt)

    return {
        "prev_iface": iface,
        "prev_rx_bytes": rx,
        "prev_tx_bytes": tx,
        "prev_sample_time": now,
        "download_rate": dl_rate,
        "upload_rate": ul_rate,
        "total_rx": rx,
        "total_tx": tx,
    }

def ping_sample_value(raw: Any) -> float | None:
    try:
        v = float(raw)
        if math.isnan(v) or math.isinf(v) or v < 0:
            return None
        return v
    except (ValueError, TypeError):
        return None

def append_ping_sample(samples: list[float | None] | None, raw: Any, limit: int) -> list[float | None]:
    values = list(samples) if isinstance(samples, list) else []
    values.append(ping_sample_value(raw))
    while len(values) > limit:
        values.pop(0)
    return values

def average_ping_latency(samples: list[float | None] | None, limit: int) -> float | None:
    values = list(samples) if isinstance(samples, list) else []
    if not values:
        return None
    sample_limit = max(1, int(limit) if limit else len(values) or 1)
    total = 0.0
    count = 0
    start = max(0, len(values) - sample_limit)
    for i in range(start, len(values)):
        v = values[i]
        if v is not None and isinstance(v, (int, float)) and not math.isnan(v) and v >= 0:
            total += v
            count += 1
    return total / count if count > 0 else -1.0

def ping_packet_loss_percent(samples: list[float | None] | None) -> int | None:
    values = list(samples) if isinstance(samples, list) else []
    if not values:
        return None
    lost = sum(1 for v in values if v is None)
    return round((lost / len(values)) * 100)

def format_packet_loss(percent: int | str | float | None) -> str:
    try:
        val = int(percent)
    except (ValueError, TypeError):
        return "N/A"
    return f"{max(0, val)}%"

def ping_latency_state(previous: dict[str, Any] | None, next_sample: dict[str, Any] | None, limit: int = 24, average_limit: int = 5) -> dict[str, Any]:
    prev = previous or {}
    sample = next_sample or {}
    iface = sample.get("iface", "")
    window = max(1, int(limit) if limit else 24)
    avg_window = max(1, int(average_limit) if average_limit else 5)

    gateway = sample.get("gateway", "")
    target = sample.get("internet_ping_target", "")
    reset = (not iface or iface != prev.get("ping_iface", "")
             or gateway != prev.get("ping_gateway", "") or target != prev.get("internet_ping_target", ""))
    router_samples = [] if reset else prev.get("router_ping_samples", [])
    internet_samples = [] if reset else prev.get("internet_ping_samples", [])

    if "router_ping_ms" in sample:
        router_samples = append_ping_sample(router_samples, sample["router_ping_ms"], window)
    elif reset:
        router_samples = []

    if "internet_ping_ms" in sample:
        internet_samples = append_ping_sample(internet_samples, sample["internet_ping_ms"], window)
    elif reset:
        internet_samples = []

    return {
        "ping_iface": iface,
        "ping_gateway": gateway,
        "internet_ping_target": target,
        "router_ping_samples": router_samples,
        "internet_ping_samples": internet_samples,
        "router_ping_latency": average_ping_latency(router_samples, avg_window),
        "internet_ping_latency": average_ping_latency(internet_samples, avg_window),
        "internet_ping_packet_loss": ping_packet_loss_percent(internet_samples),
    }

def format_bytes(bytes_val: float | int | str) -> str:
    try:
        n = float(bytes_val)
    except (ValueError, TypeError):
        n = 0.0
    if math.isnan(n) or n < 0:
        n = 0.0
    if n < 1024:
        return f"{round(n)} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"

def format_rate(bytes_per_sec: float | int | str) -> str:
    return f"{format_bytes(bytes_per_sec)}/s"

def format_ping_latency(ms: float | int | str | None) -> str:
    if ms is None:
        return "N/A"
    try:
        v = float(ms)
    except (ValueError, TypeError):
        return "N/A"
    if not math.isfinite(v):
        return "N/A"
    if v < 0:
        return "Timeout"
    return f"{v:.1f} ms"

def wifi_row(network: dict[str, Any]) -> dict[str, Any] | None:
    if not network:
        return None
    return {
        "network": network,
        "connected": bool(network.get("connected") or network.get("in_use")),
        "known": bool(network.get("known", False)),
        "ssid": network.get("ssid") or network.get("name") or "",
        "signal": round(network.get("signal", network.get("signalStrength", 0))),
        "security": network.get("security", "Open"),
    }

def sort_wifi_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nets = list(rows) if isinstance(rows, list) else []
    nets.sort(key=lambda x: (not x.get("connected", False), not x.get("known", False), -x.get("signal", 0)))
    return nets

def wifi_section_title(wifi_networks: list[dict[str, Any]], index: int) -> str:
    networks = list(wifi_networks) if isinstance(wifi_networks, list) else []
    if index < 0 or index >= len(networks):
        return ""
    net = networks[index]
    if not net:
        return ""
    if (net.get("known") or net.get("connected")) and index == 0:
        return "Saved Networks"
    if not (net.get("known") or net.get("connected")) and (index == 0 or (index > 0 and (networks[index - 1].get("known") or networks[index - 1].get("connected")))):
        return "Available Networks"
    return ""

def is_protected(security: str, open_security: str = "Open", owe_security: str = "OWE") -> bool:
    sec = str(security or "").strip().upper()
    if sec in (open_security.upper(), owe_security.upper(), "NOPASS", "NONE", "--", ""):
        return False
    return True

def network_failure_reason(reason: str, reasons: dict[str, str] | None = None) -> str:
    r = reasons or {}
    if reason == r.get("NoSecrets"):
        return "Passphrase required"
    if reason == r.get("WifiAuthTimeout"):
        return "Wrong password"
    if reason == r.get("WifiNetworkLost"):
        return "Network lost"
    if reason == r.get("WifiClientDisconnected"):
        return "Disconnected"
    if reason == r.get("WifiClientFailed"):
        return "Connection failed"
    return "Failed to connect"

def escape_wifi_str(s: str) -> str:
    """Escape special characters per Wi-Fi QR code standard (MECARD format)."""
    out = []
    for ch in str(s):
        if ch in ('\\', ';', ',', ':', '"'):
            out.append('\\' + ch)
        else:
            out.append(ch)
    return "".join(out)


def build_wifi_payload(ssid: str, password: str = "", security: str = "WPA", hidden: bool = False) -> str:
    """Builds standard Wi-Fi QR code payload string."""
    sec_upper = security.upper()
    if not password and (sec_upper in ("OPEN", "NONE", "--", "") or "NOPASS" in sec_upper):
        sec_type = "nopass"
    elif "WEP" in sec_upper:
        sec_type = "WEP"
    else:
        sec_type = "WPA"

    parts = [f"S:{escape_wifi_str(ssid)}", f"T:{sec_type}"]
    if sec_type != "nopass" and password:
        parts.append(f"P:{escape_wifi_str(password)}")
    if hidden:
        parts.append("H:true")

    return f"WIFI:{';'.join(parts)};;"


def generate_qr_text(payload: str) -> str:
    """Generates a UTF-8 block QR code string using qrencode."""
    if shutil.which("qrencode"):
        try:
            res = subprocess.run(
                ["qrencode", "-m", "2", "-t", "UTF8", payload],
                capture_output=True, text=True, timeout=5
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout
        except Exception:
            pass
    return ""


def copy_to_clipboard(text: str) -> bool:
    """Copy text to the Wayland clipboard and report the command result."""
    if not text:
        return False
    try:
        # Its background clipboard owner keeps stderr open after the parent exits.
        # Capturing that pipe would wait for clipboard ownership to end.
        result = subprocess.run(
            ["wl-copy", "--type", "text/plain;charset=utf-8"], input=text, text=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def save_qr_image(payload: str, ssid: str) -> str | None:
    """Save a QR image in the configured Pictures directory."""
    if not shutil.which("qrencode"):
        return None

    safe_ssid = "".join(c for c in ssid if c.isalnum() or c in ('-', '_')).strip() or "wifi"
    try:
        location = subprocess.run(
            ["xdg-user-dir", "PICTURES"], capture_output=True, text=True, timeout=3,
        )
        if location.returncode or not location.stdout.rstrip("\n"):
            return None
        out_dir = Path(location.stdout.rstrip("\n"))
        if not out_dir.is_absolute():
            return None
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"wifi_qr_{safe_ssid}.png"
        res = subprocess.run(
            ["qrencode", "-s", "10", "-m", "4", "-o", str(out_file), payload],
            capture_output=True, timeout=5
        )
        if res.returncode == 0 and out_file.exists():
            return str(out_file)
    except Exception:
        pass
    return None


def _read_qr_keypress() -> str | None:
    """Non-blocking single keypress reader."""
    if not sys.stdin.isatty():
        return None
    try:
        r, _, _ = select.select([sys.stdin], [], [], 0.05)
        if r:
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                r2, _, _ = select.select([sys.stdin], [], [], 0.02)
                if r2:
                    sys.stdin.read(2)
                return "\x1b"
            return ch
    except Exception:
        pass
    return None


def show_wifi_qr_interactive(ssid: str, password: str = "", security: str = "WPA", hidden: bool = False, interactive: bool = True) -> None:
    """Renders the interactive QR code viewer directly using Rich."""

    console = Console()
    payload = build_wifi_payload(ssid, password, security, hidden)
    qr_text = generate_qr_text(payload)

    if not interactive or not sys.stdin.isatty():
        # Non-interactive / headless fallback: print once and return
        grid = Table.grid(expand=True)
        grid.add_column(justify="center")
        grid.add_row(Text("󰐳 WI-FI SHARING QR CODE", style="bold cyan"))
        grid.add_row(Text("Scan this QR code with a phone or mobile device to join instantly.", style="dim italic"))
        grid.add_row(Text(""))
        if qr_text:
            qr_panel = Panel(
                Text(qr_text, style="white on black", justify="center"),
                title="[bold yellow] 󰤨 Scan to Connect [/bold yellow]",
                border_style="bright_blue",
                expand=False
            )
            grid.add_row(Align.center(qr_panel))
        else:
            grid.add_row(Text("[!] qrencode not found. Install 'qrencode' to render visual QR code.", style="bold red"))
        grid.add_row(Text(""))
        info_table = Table(show_header=False, show_edge=False, box=None, padding=(0, 2))
        info_table.add_column(style="dim", justify="right")
        info_table.add_column(style="bold white", justify="left")
        info_table.add_row("Network (SSID):", Text(ssid))
        info_table.add_row("Security:", security)
        if password:
            info_table.add_row("Password:", Text(password))
        else:
            info_table.add_row("Password:", "[italic green]None (Open Network)[/italic green]")
        if hidden:
            info_table.add_row("Hidden SSID:", "Yes")
        grid.add_row(Align.center(info_table))
        console.print(grid)
        return

    show_password = True
    status_msg = ""
    status_time = 0.0

    old_settings = None
    try:
        old_settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())
    except Exception:
        old_settings = None

    try:
        with Live(console=console, refresh_per_second=10) as live:
            while True:
                now = time.time()
                key = _read_qr_keypress()

                if key in ("q", "Q", "\x1b", "\r", "\n", " "):
                    break

                elif key in ("p", "P"):
                    show_password = not show_password
                    status_msg = "Password visibility toggled."
                    status_time = now

                elif key in ("c", "C"):
                    if password:
                        if copy_to_clipboard(password):
                            status_msg = "Password copied to clipboard!"
                        else:
                            status_msg = "Could not copy to the Wayland clipboard."
                    else:
                        status_msg = "No password required (Open network)."
                    status_time = now

                elif key in ("w", "W"):
                    if copy_to_clipboard(payload):
                        status_msg = "Wi-Fi connect string copied to clipboard!"
                    else:
                        status_msg = "Could not copy to the Wayland clipboard."
                    status_time = now

                elif key in ("s", "S"):
                    if not shutil.which("qrencode"):
                        status_msg = "'qrencode' not found. Install qrencode to export PNG."
                    else:
                        saved_path = save_qr_image(payload, ssid)
                        if saved_path:
                            status_msg = f"Saved PNG to: {saved_path}"
                        else:
                            status_msg = "Failed to save PNG image."
                    status_time = now

                if status_msg and (now - status_time > 3.0):
                    status_msg = ""

                grid = Table.grid(expand=True)
                grid.add_column(justify="center")

                grid.add_row(Text("󰐳 WI-FI SHARING QR CODE", style="bold cyan"))
                grid.add_row(Text("Scan this QR code with a phone or mobile device to join instantly.", style="dim italic"))
                grid.add_row(Text(""))

                if qr_text:
                    qr_panel = Panel(
                        Text(qr_text, style="white on black", justify="center"),
                        title="[bold yellow] 󰤨 Scan to Connect [/bold yellow]",
                        border_style="bright_blue",
                        expand=False
                    )
                    grid.add_row(Align.center(qr_panel))
                else:
                    grid.add_row(Text("[!] qrencode not found. Install 'qrencode' to render visual QR code.", style="bold red"))

                grid.add_row(Text(""))

                info_table = Table(show_header=False, show_edge=False, box=None, padding=(0, 2))
                info_table.add_column(style="dim", justify="right")
                info_table.add_column(style="bold white", justify="left")

                info_table.add_row("Network (SSID):", Text(ssid))
                info_table.add_row("Security:", security)

                if password:
                    disp_pw = password if show_password else "•" * len(password)
                    info_table.add_row("Password:", Text(disp_pw))
                else:
                    info_table.add_row("Password:", "[italic green]None (Open Network)[/italic green]")

                if hidden:
                    info_table.add_row("Hidden SSID:", "Yes")

                grid.add_row(Align.center(info_table))
                grid.add_row(Text(""))

                if status_msg:
                    grid.add_row(Text(f"  {status_msg}  ", style="bold green on black"))
                    grid.add_row(Text(""))

                footer_text = Text()
                footer_text.append("[p] ", style="bold yellow")
                footer_text.append("Toggle Password  •  ", style="dim")
                footer_text.append("[c] ", style="bold yellow")
                footer_text.append("Copy Password  •  ", style="dim")
                footer_text.append("[w] ", style="bold yellow")
                footer_text.append("Copy Wi-Fi String  •  ", style="dim")
                footer_text.append("[s] ", style="bold yellow")
                footer_text.append("Save PNG  •  ", style="dim")
                footer_text.append("[q/Esc] ", style="bold yellow")
                footer_text.append("Return", style="dim")

                grid.add_row(Align.center(footer_text))

                live.update(grid)
                time.sleep(0.08)

    finally:
        if old_settings and sys.stdin.isatty():
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            except Exception:
                pass


# =============================================================================
#  ENGINE IMPLEMENTATION
# =============================================================================
class NetworkManagerEngine(BaseEngine):
    """
    Full NetworkManager & Dusky Network logic engine for Dusky TUI.
    """
    _instance: "NetworkManagerEngine | None" = None

    def __init__(self, config_path: str = ""):
        NetworkManagerEngine._instance = self
        self.cache_dir = Path.home() / ".cache" / "dusky_tui"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._target_path = str(self.cache_dir / "wifi_cache.json")

        self.app = None
        self.shutdown_event = threading.Event()
        self.rescan_event = threading.Event()
        self._scan_running = False

        # Hotspot fields are loaded from the saved NetworkManager profile when available.
        self._hotspot_ssid = "MyHotspot"
        self._hotspot_password = ""
        self._hotspot_device = "Auto"
        self._hotspot_draft_loaded = False
        self._hotspot_devices_cache: list[dict[str, str]] = []
        self._uplinks_cache: list[dict[str, str]] = []
        self._last_uplink_refresh = 0.0
        self._radio_on = False
        self._active_wifi: dict[str, Any] | None = None
        self._active_wifi_connections: list[dict[str, Any]] = []
        self._saved_wifi: list[dict[str, Any]] = []
        self._profile_ssids: dict[str, tuple[float, str]] = {}
        self._profile_lock = threading.Lock()
        self._default_routes = {4: "none", 6: "none"}
        self._route_selection: dict[str, Any] = {}
        self._available_bands: list[str] = []
        self._pinned_band = "Auto"
        self._bands_by_uuid: dict[str, list[str]] = {}
        self._pinned_bands_by_uuid: dict[str, str] = {}
        self._active_hotspot: dict[str, str] | None = None
        self._hotspot_clients = 0
        self._hotspot_address = "N/A"

        # Live state tracking
        self._tp_state: dict[str, Any] = {}
        self._ping_state: dict[str, Any] = {}
        self._verbose_info: dict[str, str] = {}
        self._dns_provider: str = "DHCP"
        self._clipboard_values: dict[str, str] = {}
        self._device_clip_values: dict[str, str] = {}
        self._devices_cache: list[dict[str, str]] = []
        self._device_details: dict[str, dict[str, str]] = {}

        # Speed test state
        self._speedtest_running: bool = False
        self._speedtest_status: str = "Ready"
        self._speedtest_down_val: str = "--"
        self._speedtest_up_val: str = "--"

        # Load cached scan results for instant startup
        self._cached_scans: list[dict[str, Any]] = []
        cache_path = Path(self._target_path)
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    if isinstance(cached, list):
                        self._cached_scans = [entry for entry in cached if (
                            isinstance(entry, dict)
                            and isinstance(entry.get("ssid"), str)
                            and isinstance(entry.get("security"), str)
                            and isinstance(entry.get("signal"), int)
                            and isinstance(entry.get("in_use"), bool)
                        )]
            except Exception as e:
                logger.error(f"Error loading wifi cache: {e}")

        self._bg_thread: threading.Thread | None = None
        self._scan_thread: threading.Thread | None = None
        self._last_poll_error = ""

    def set_app(self, app) -> None:
        self.app = app
        # Keep the schema's loading placeholders until a real snapshot exists.
        self.rescan_event.set()
        if self._bg_thread is None:
            self._bg_thread = threading.Thread(target=self._background_loop, daemon=True)
            self._bg_thread.start()

    def shutdown(self) -> None:
        self.shutdown_event.set()
        self.app = None

    def _apply_poll_snapshot(self, snapshot: dict[str, Any]) -> None:
        if self.shutdown_event.is_set() or not self.app:
            return
        self._radio_on = snapshot["radio"]
        self._active_wifi = snapshot["active"]
        self._active_wifi_connections = snapshot["active_connections"]
        self._saved_wifi = snapshot["saved"]
        self._default_routes = snapshot["routes"]
        self._route_selection = snapshot["route_choice"]
        self._available_bands = snapshot["bands"]
        self._pinned_band = snapshot["pinned_band"]
        self._bands_by_uuid = snapshot["bands_by_uuid"]
        self._pinned_bands_by_uuid = snapshot["pinned_bands_by_uuid"]
        self._active_hotspot = snapshot["hotspot"]
        self._hotspot_clients = snapshot["clients"]
        self._hotspot_address = snapshot["address"]
        self._verbose_info = snapshot["verbose"]
        self._tp_state = snapshot["throughput"]
        self._ping_state = snapshot["ping"]
        self._dns_provider = snapshot["dns"]
        self._devices_cache = snapshot["devices"]
        self._device_details = snapshot["details"]
        self._uplinks_cache = snapshot["uplinks"]
        self._hotspot_devices_cache = snapshot["hotspot_devices"]
        self._rebuild_schema()

    @property
    def target_path(self) -> str:
        # The scan cache is internal state, not a user-editable config target.
        return ""

    # =========================================================================
    #  BaseEngine Contract
    # =========================================================================

    def load_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {}

        radio = self._run_cmd(["nmcli", "radio", "wifi"], required=True).strip()
        if radio not in {"enabled", "disabled"}:
            raise RuntimeError(f"Unexpected NetworkManager radio state: {radio!r}")
        state["status/wifi_radio"] = "true" if radio == "enabled" else "false"

        for conn in self._get_saved_wifi():
            state[f"saved/{conn['uuid']}"] = "true" if conn["autoconnect"] else "false"

        # Restore saved credentials once in the loading worker. Later refreshes
        # must preserve the user's draft until Start applies it.
        if not self._hotspot_draft_loaded:
            try:
                active_hotspot = self._hotspot_profile(active_only=True)
                saved_hotspot = self._hotspot_profile(uuid_filter=active_hotspot["uuid"]) if active_hotspot else self._hotspot_profile()
                if saved_hotspot:
                    self._hotspot_ssid = saved_hotspot["ssid"]
                    self._hotspot_password = saved_hotspot["password"]
                self._hotspot_draft_loaded = True
            except RuntimeError as exc:
                logger.warning("Hotspot draft was not loaded: %s", exc)

        # Hotspot config
        state["hotspot/hotspot_ssid"] = self._hotspot_ssid
        state["hotspot/hotspot_password"] = self._hotspot_password
        state["hotspot/hotspot_device"] = self._hotspot_device

        hotspot = self._hotspot_profile(active_only=True)
        state["hotspot/hotspot_status_info"] = "Active" if hotspot else "Inactive"

        # Trigger bools
        state["network/rescan"] = "false"
        state["hotspot/start_hotspot_24"] = "false"
        state["hotspot/start_hotspot_5"] = "false"
        state["hotspot/stop_hotspot"] = "false"
        state["hotspot/qr_hotspot"] = "false"
        state["status_action/restart_nm"] = "false"
        state["status_action/rescan"] = "false"

        # Speed test actions
        state["speedtest_action/speedtest_full"] = "false"
        state["speedtest_action/speedtest_down"] = "false"
        state["speedtest_action/speedtest_up"] = "false"

        # Clipboard copy items
        for clip_key in (
            "status_type", "status_ssid", "status_ip", "status_gateway", "status_detail", "status_device",
            "throughput_down", "throughput_up", "throughput_rx_total", "throughput_tx_total",
            "ping_router", "ping_internet", "ping_packet_loss", "dns_current",
            "speedtest_down_result", "speedtest_up_result"
        ):
            state[f"clipboard/{clip_key}"] = "false"

        if self.app and hasattr(self.app, 'schema'):
            for tab_idx in range(len(self.app.schema)):
                for item in self.app.schema.get(tab_idx, []):
                    if item.type_ in ("action", "menu"):
                        continue
                    cache_key = f"{item.scope}/{item.key}" if item.scope else item.key
                    if cache_key not in state:
                        state[cache_key] = item.serialize(item.value)

        return state

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        # Resetting a momentary button must not execute its action.
        trigger = (
            target_key == "rescan"
            or target_scope in {"status_action", "speedtest_action", "clipboard", "route"}
            or (target_scope in {"saved_action", "active_wifi_action"} and not target_key.startswith("band__"))
            or (target_scope == "network" and target_key.startswith(("cn__", "qr_net__")))
            or (target_scope == "hotspot" and target_key in {
                "start_hotspot_24", "start_hotspot_5", "stop_hotspot", "qr_hotspot",
            })
        )
        if trigger and new_value != "true":
            if new_value == "false":
                return True, "Action reset.", ""
            return False, "Action requires true or false.", ""
        if target_key == "wifi_radio" and new_value not in {"true", "false"}:
            return False, "Wi-Fi radio requires true or false.", ""
        try:
            return self._write_value(target_key, target_scope, new_value, item_type)
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            self.rescan_event.set()
            return False, f"Network action failed: {command_failure(exc)}", ""

    def _write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str) -> tuple[bool, str, str]:
        logger.info("write_value: key=%s, scope=%s, val=%s", target_key, target_scope,
                    "[hidden]" if target_key == "hotspot_password" or target_key.startswith("pw__") else new_value)

        # ---- Rescan button ----
        if target_key == "rescan":
            self.rescan_event.set()
            return True, "WiFi rescan triggered.", ""

        # ---- Radio toggle ----
        if target_key == "wifi_radio":
            action = "on" if new_value == "true" else "off"
            res = self._run_nmcli(["nmcli", "radio", "wifi", action], timeout=10)
            if res.returncode == 0:
                self.rescan_event.set()
                return True, f"WiFi radio turned {action}.", ""
            return False, f"Failed to set radio: {res.stderr.strip()}", res.stderr

        # ---- Autoconnect toggle ----
        if target_scope == "saved" and self._is_uuid(target_key):
            if new_value not in {"true", "false"}:
                return False, "Autoconnect requires true or false.", ""
            yn = "yes" if new_value == "true" else "no"
            res = self._run_nmcli(
                ["nmcli", "connection", "modify", "uuid", target_key, "connection.autoconnect", yn],
                timeout=10,
            )
            if res.returncode == 0:
                return True, f"Autoconnect set to {yn}.", ""
            return False, f"Failed: {res.stderr.strip()}", res.stderr

        # ---- Hotspot configuration ----
        if target_scope == "hotspot":
            return self._handle_hotspot(target_key, new_value)

        if target_scope == "route":
            return self._handle_route_choice(target_key)

        # ---- Network actions ----
        if target_scope == "network":
            return self._handle_network_action(target_key, new_value)

        # ---- Saved profile actions ----
        if target_scope in {"saved_action", "active_wifi_action"}:
            return self._handle_saved_action(target_key, new_value)

        # ---- Status tab actions ----
        if target_scope == "status_action":
            return self._handle_status_action(target_key, new_value)

        # ---- Speed Test tab actions ----
        if target_scope == "speedtest_action":
            return self._handle_speedtest_action(target_key)

        # ---- Clipboard copy ----
        if target_scope == "clipboard":
            return self._handle_clipboard(target_key)

        return False, f"Unsupported network setting: {target_scope}/{target_key}", ""

    def write_batch_results(self, changes: list[tuple[str, str, str, str]]) -> dict[tuple[str, str], NetworkWriteResult]:
        """Report each outcome so the UI never retries a failed connect or band change."""
        results = {}
        for key, scope, value, kind in changes:
            ok, message, _ = self.write_value(key, scope, value, item_type=kind)
            results[(key, scope)] = NetworkWriteResult(ok, message)
            if message == "AUTH_REQUIRED":
                break
        return results

    # =========================================================================
    #  ACTION HANDLERS
    # =========================================================================

    def _active_uplinks(self) -> list[dict[str, str]]:
        """Active physical links users may explicitly choose for default routing."""
        result = []
        route_devices: dict[int, set[str]] = {}
        for family in (4, 6):
            try:
                routes = json.loads(self._run_cmd(["ip", "-j", f"-{family}", "route", "show", "default"]))
                route_devices[family] = {route["dev"] for route in routes if isinstance(route, dict) and "dev" in route}
            except (TypeError, ValueError):
                route_devices[family] = set()
        rows = self._run_cmd(["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"])
        for line in rows.splitlines():
            parts = _split_nmcli_line(line)
            if len(parts) < 4 or not parts[3] or parts[3] == "--" or parts[2] in {"tun", "vpn", "wireguard", "loopback", "dummy"}:
                continue
            name, uuid, kind, device = parts[:4]
            props = self._run_cmd([
                "nmcli", "-g", "ipv4.method,ipv4.never-default,ipv6.method,ipv6.never-default",
                "connection", "show", "uuid", uuid,
            ]).splitlines()
            gateways = self._run_cmd([
                "nmcli", "-g", "IP4.GATEWAY,IP6.GATEWAY", "device", "show", device,
            ]).splitlines()
            if len(props) < 4 or len(gateways) < 2 or "shared" in (props[0], props[2]):
                continue
            if (kind in {"bridge", "bond", "team"} and all(gateway in {"", "--"} for gateway in gateways[:2])
                    and device not in route_devices[4] | route_devices[6]):
                continue
            if props[0] in {"disabled", "ignore"} and props[2] in {"disabled", "ignore"}:
                continue
            gateway_v4 = gateways[0] if gateways[0] not in {"", "--"} else "on-link" if device in route_devices[4] else ""
            gateway_v6 = gateways[1] if gateways[1] not in {"", "--"} else "on-link" if device in route_devices[6] else ""
            display_name = name
            if name.startswith("Wired connection "):
                properties = self._run_cmd(["udevadm", "info", "-q", "property", "-p", f"/sys/class/net/{device}"])
                model = next((line.partition("=")[2].replace("_", " ") for line in properties.splitlines()
                              if line.startswith("ID_MODEL=") and line.partition("=")[2]), "")
                if model:
                    display_name = model
            result.append({"name": name, "display_name": display_name, "uuid": uuid, "type": kind, "device": device,
                           "ipv4_gateway": gateway_v4, "ipv6_gateway": gateway_v6,
                           "ipv4_method": props[0], "ipv6_method": props[2],
                           "ipv4_never_default": props[1], "ipv6_never_default": props[3]})
        return result

    def _default_route(self, family: int) -> str:
        output = self._run_cmd(["ip", "-j", f"-{family}", "route", "show", "default"])
        try:
            routes = json.loads(output)
            if not isinstance(routes, list) or not routes:
                return "none"
            route = min(routes, key=lambda item: item.get("metric", 0))
            return f"{route.get('dev', '?')} via {route.get('gateway', 'on-link')}"
        except (ValueError, TypeError):
            return "none"

    @staticmethod
    def _route_choice() -> dict[str, Any]:
        try:
            choice = json.loads(ROUTE_CHOICE_FILE.read_text())
            return choice if isinstance(choice, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _save_route_choice(choice: dict[str, Any]) -> None:
        ROUTE_CHOICE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not choice:
            ROUTE_CHOICE_FILE.unlink(missing_ok=True)
            return
        with tempfile.TemporaryDirectory(dir=ROUTE_CHOICE_FILE.parent) as directory:
            replacement = Path(directory) / ROUTE_CHOICE_FILE.name
            replacement.write_text(json.dumps(choice, indent=2) + "\n")
            replacement.replace(ROUTE_CHOICE_FILE)

    @staticmethod
    def _profile_route_settings(uuid: str) -> tuple[str, str, str, str] | None:
        try:
            result = NetworkManagerEngine._run_nmcli(
                ["nmcli", "-g", "ipv4.route-metric,ipv6.route-metric,ipv4.never-default,ipv6.never-default",
                "connection", "show", "uuid", uuid],
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        values = result.stdout.splitlines()
        return tuple(values[:4]) if result.returncode == 0 and len(values) >= 4 else None

    @staticmethod
    def _set_profile_route_settings(uuid: str, settings: tuple[str, str, str, str]) -> subprocess.CompletedProcess[str]:
        return NetworkManagerEngine._run_nmcli(
            ["nmcli", "connection", "modify", "uuid", uuid,
             "ipv4.route-metric", settings[0], "ipv6.route-metric", settings[1],
             "ipv4.never-default", settings[2], "ipv6.never-default", settings[3]],
            timeout=10,
        )

    def _active_device_for_uuid(self, uuid: str) -> str:
        rows = self._run_cmd(["nmcli", "-t", "-f", "UUID,DEVICE", "connection", "show", "--active"])
        for line in rows.splitlines():
            parts = _split_nmcli_line(line)
            if len(parts) >= 2 and parts[0] == uuid:
                return parts[1]
        return ""

    @staticmethod
    def _reapply_device(device: str) -> subprocess.CompletedProcess[str] | None:
        if not device or device == "--":
            return None
        return NetworkManagerEngine._run_nmcli(
            ["nmcli", "device", "reapply", device],
            timeout=15,
        )

    def _change_route_settings(self, uuid: str, settings: tuple[str, str, str, str]) -> str:
        try:
            changed = self._set_profile_route_settings(uuid, settings)
            if changed.returncode:
                return changed.stderr.strip() or "NetworkManager rejected the profile change."
            if self._profile_route_settings(uuid) != settings:
                return "Saved route settings could not be verified."
            applied = self._reapply_device(self._active_device_for_uuid(uuid))
            if applied is not None and applied.returncode:
                return applied.stderr.strip() or "NetworkManager could not apply the change live."
            return ""
        except (OSError, subprocess.TimeoutExpired) as exc:
            return str(exc)

    @staticmethod
    def _preferred_route_settings(original: tuple[str, str, str, str], link: dict[str, str], selected: bool, families: set[int]) -> tuple[str, str, str, str]:
        desired = list(original)
        for index, family in enumerate((4, 6)):
            if family not in families:
                continue
            if selected:
                if link.get(f"ipv{family}_method") not in {"disabled", "ignore"} and link.get(f"ipv{family}_gateway"):
                    desired[index] = str(PREFERRED_ROUTE_METRIC)
                    desired[index + 2] = "no"
            elif original[index] != "-1":
                # Preserve ordinary priorities and local-only intent. An
                # explicit metric 0/1 must rank below the selected source.
                desired[index] = str(max(PREFERRED_ROUTE_METRIC + 1, int(original[index])))
        return tuple(desired)

    def _restore_route_choice(self, choice: dict[str, Any]) -> str:
        originals = choice.get("original_settings", {})
        applied = choice.get("applied_settings", {})
        if not isinstance(originals, dict) or not isinstance(applied, dict):
            return "Invalid route recovery record."
        problems = []
        for uuid, values in originals.items():
            if not isinstance(uuid, str) or not isinstance(values, list) or len(values) != 4:
                problems.append("Invalid original route settings in recovery record")
                continue
            original = tuple(str(value) for value in values)
            expected_values = applied.get(uuid)
            expected = tuple(expected_values) if isinstance(expected_values, list) and len(expected_values) == 4 else None
            current = self._profile_route_settings(uuid)
            if current is None:
                problems.append(f"{uuid}: profile unavailable")
            elif current not in {expected, original}:
                problems.append(f"{uuid}: changed outside this menu; left alone")
            elif current != original:
                issue = self._change_route_settings(uuid, original)
                if issue:
                    problems.append(f"{uuid}: {issue}")
        return "; ".join(problems)

    def _handle_route_choice(self, key: str) -> tuple[bool, str, str]:
        previous = self._route_choice()
        if key == "automatic":
            issue = self._restore_route_choice(previous)
            if issue:
                return False, issue, issue
            try:
                self._save_route_choice({})
            except OSError as exc:
                return False, f"Could not clear route preference: {exc}", ""
            self.rescan_event.set()
            return True, "Previous route settings restored.", ""

        if not key.startswith("use__"):
            return False, "Unknown internet source.", ""
        uuid = key[5:]
        active_uplinks = self._active_uplinks()
        candidate = next((item for item in active_uplinks if item["uuid"] == uuid), None)
        if not candidate:
            return False, "This connection is not an active selectable link.", ""
        preferred_families = {family for family in (4, 6) if candidate.get(f"ipv{family}_gateway")
                              and candidate.get(f"ipv{family}_method") not in {"disabled", "ignore"}}
        if not preferred_families:
            return False, "This link has no usable default gateway.", ""
        prior_originals = previous.get("original_settings", {})
        prior_applied = previous.get("applied_settings", {})
        originals = dict(prior_originals) if isinstance(prior_originals, dict) else {}
        current_settings = {}
        desired_settings = {}
        # Include previously modified links even if they are now inactive, so
        # switching sources does not leave the previous source pinned at 1.
        links = active_uplinks + [{"uuid": old_uuid} for old_uuid in originals
                                  if old_uuid not in {item["uuid"] for item in active_uplinks}]
        for link in links:
            link_uuid = link["uuid"]
            current = self._profile_route_settings(link_uuid)
            if current is None:
                return False, f"Could not read route settings for {link.get('name', link_uuid)}.", ""
            current_settings[link_uuid] = current
            saved_original = originals.get(link_uuid)
            if not isinstance(saved_original, list) or len(saved_original) != 4:
                originals[link_uuid] = list(current)
            else:
                old_original = tuple(str(value) for value in saved_original)
                recorded = prior_applied.get(link_uuid) if isinstance(prior_applied, dict) else None
                old_expected = tuple(recorded) if isinstance(recorded, list) and len(recorded) == 4 else None
                if current not in {old_original, old_expected}:
                    originals[link_uuid] = list(current)
            original = tuple(str(value) for value in originals[link_uuid])
            desired_settings[link_uuid] = self._preferred_route_settings(original, link, link_uuid == uuid, preferred_families)
        try:
            self._save_route_choice({"uuid": uuid, "original_settings": originals,
                                     "applied_settings": {key: list(values) for key, values in desired_settings.items()}})
        except OSError as exc:
            return False, f"Could not save route preference: {exc}", ""
        changed_uuids = []
        for link in [candidate] + [item for item in links if item["uuid"] != uuid]:
            link_uuid = link["uuid"]
            if current_settings[link_uuid] == desired_settings[link_uuid] and link_uuid != uuid:
                continue
            changed_uuids.append(link_uuid)
            issue = self._change_route_settings(link_uuid, desired_settings[link_uuid])
            if issue:
                rollback_issues = [self._change_route_settings(item, current_settings[item]) for item in reversed(changed_uuids)]
                if any(rollback_issues):
                    issue += "; rollback incomplete; original settings remain in the saved preference file"
                else:
                    try:
                        self._save_route_choice(previous)
                    except OSError:
                        issue += "; could not restore the previous saved preference"
                return False, f"Could not prefer {link.get('name', link_uuid)}: {issue}", issue
        self._uplinks_cache = self._active_uplinks()
        self.rescan_event.set()
        for family in (4, 6):
            if not candidate.get(f"ipv{family}_gateway") or candidate.get(f"ipv{family}_method") in {"disabled", "ignore"}:
                continue
            if family == 6 and not Path("/proc/sys/net/ipv6").exists():
                continue
            current = self._default_route(family)
            if not current.startswith(f"{candidate['device']} via "):
                return True, f"Preferred {candidate['name']}, but IPv{family} still uses {current}; another routing rule may override the preference.", ""
        return True, f"Preferred internet source: {candidate['name']} ({candidate['device']}); other eligible links remain available for failover.", ""

    def _hotspot_profile(self, active_only: bool = False, uuid_filter: str = "") -> dict[str, str] | None:
        rows = self._run_cmd(["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"] if active_only else
                             ["nmcli", "-t", "-f", "NAME,UUID,TYPE", "connection", "show"], required=True)
        matches = []
        for line in rows.splitlines():
            fields = _split_nmcli_line(line)
            if len(fields) < 3 or fields[2] != "802-11-wireless":
                continue
            if uuid_filter:
                if fields[1] != uuid_filter:
                    continue
            elif fields[0] != HOTSPOT_PROFILE:
                continue
            mode = self._run_cmd(["nmcli", "-g", "802-11-wireless.mode", "connection", "show", "uuid", fields[1]], required=True).strip()
            if mode == "ap":
                matches.append(fields)
        if not matches:
            return None
        if len(matches) > 1:
            raise RuntimeError("Multiple profiles named Dusky Hotspot are available. Rename or remove the extra profiles in a profile editor or the Saved page before starting a hotspot.")
        fields = matches[0]
        uuid = fields[1]
        if active_only:
            return {"uuid": uuid, "ssid": "", "password": "", "device": fields[3] if len(fields) > 3 else ""}
        ssid = self._run_cmd(["nmcli", "-e", "no", "-g", "802-11-wireless.ssid", "connection", "show", "uuid", uuid], required=True).rstrip("\n")
        password = self._run_cmd(["nmcli", "--show-secrets", "-e", "no", "-g", "802-11-wireless-security.psk", "connection", "show", "uuid", uuid], required=True).rstrip("\n")
        return {"uuid": uuid, "ssid": ssid, "password": password, "device": ""}

    def _hotspot_devices(self) -> list[dict[str, str]]:
        devices = []
        for line in self._run_cmd(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"]).splitlines():
            fields = _split_nmcli_line(line)
            if len(fields) < 4 or fields[1] != "wifi":
                continue
            device = fields[0]
            features = self._run_cmd(["nmcli", "-g", "WIFI-PROPERTIES.AP,WIFI-PROPERTIES.2GHZ,WIFI-PROPERTIES.5GHZ", "device", "show", device]).splitlines()
            if len(features) >= 3 and features[0] == "yes":
                devices.append({"device": device, "state": fields[2], "connection": fields[3],
                                "2.4": features[1], "5": features[2]})
        return devices

    @staticmethod
    def _remember_hotspot_previous(device: str, uuid: str) -> None:
        HOTSPOT_PREVIOUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not uuid:
            HOTSPOT_PREVIOUS_FILE.unlink(missing_ok=True)
            return
        with tempfile.TemporaryDirectory(dir=HOTSPOT_PREVIOUS_FILE.parent) as directory:
            replacement = Path(directory) / HOTSPOT_PREVIOUS_FILE.name
            replacement.write_text(json.dumps({"device": device, "uuid": uuid}) + "\n")
            replacement.replace(HOTSPOT_PREVIOUS_FILE)

    @staticmethod
    def _hotspot_previous() -> dict[str, str]:
        try:
            data = json.loads(HOTSPOT_PREVIOUS_FILE.read_text())
            return data if isinstance(data, dict) and isinstance(data.get("device"), str) and isinstance(data.get("uuid"), str) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _prepare_hotspot_firewall(device: str) -> str:
        """Allow hotspot DHCP/DNS and routed client traffic when UFW is active."""
        ufw = shutil.which("ufw")
        if not ufw or subprocess.run(
            ["systemctl", "is-active", "--quiet", "ufw"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=5,
        ).returncode:
            return ""
        try:
            rules = Path("/etc/ufw/user.rules").read_text()
            if all(f"-A ufw-user-input -i {device} -p {proto} --dport {port} -j ACCEPT" in rules
                   for port, proto in ((67, "udp"), (53, "udp"), (53, "tcp"))) and (
                       f"-A ufw-user-forward -i {device} -j ACCEPT" in rules):
                return ""
        except OSError:
            pass
        pkexec = shutil.which("pkexec")
        if not pkexec:
            return "UFW is active; install polkit or allow hotspot DHCP/DNS and forwarding on this Wi-Fi adapter."
        program = (
            "import subprocess, sys\n"
            "device, ufw = sys.argv[1:3]\n"
            "commands = [[ufw, 'allow', 'in', 'on', device, 'to', 'any', 'port', str(port), "
            "'proto', protocol, 'comment', 'Dusky hotspot ' + purpose] "
            "for port, protocol, purpose in ((67, 'udp', 'DHCP'), (53, 'udp', 'DNS'), (53, 'tcp', 'DNS'))]\n"
            "commands.append([ufw, 'route', 'allow', 'in', 'on', device, 'comment', 'Dusky hotspot forwarding'])\n"
            "for command in commands:\n"
            "    result = subprocess.run(command, capture_output=True, text=True)\n"
            "    if result.returncode:\n"
            "        sys.stderr.write(result.stderr or result.stdout)\n"
            "        sys.exit(result.returncode)\n"
        )
        try:
            result = subprocess.run([pkexec, "/usr/bin/python3", "-c", program, device, ufw],
                                    capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"Could not prepare UFW for the hotspot: {exc}"
        if result.returncode:
            return f"Could not prepare UFW for the hotspot: {result.stderr.strip()}"
        return ""

    def _handle_hotspot(self, key: str, value: str) -> tuple[bool, str, str]:
        if key == "hotspot_ssid":
            if not value or "\0" in value or len(value.encode("utf-8")) > 32:
                return False, "SSID must be 1–32 bytes.", ""
            self._hotspot_ssid = value
            return True, "Hotspot SSID draft updated; Start applies it.", ""

        if key == "hotspot_password":
            if value and (len(value) < 8 or len(value) > 63 or any(not 32 <= ord(char) <= 126 for char in value)):
                return False, "Password must be 8–63 printable ASCII characters; blank generates one.", ""
            self._hotspot_password = value
            return True, "Hotspot password draft updated; Start applies it.", ""

        if key == "hotspot_device":
            if value != "Auto" and value not in [item["device"] for item in self._hotspot_devices()]:
                return False, "This Wi-Fi adapter cannot host a hotspot.", ""
            self._hotspot_device = value
            return True, f"Hotspot adapter: {value}.", ""

        if key in ("start_hotspot_24", "start_hotspot_5"):
            band = "bg" if key == "start_hotspot_24" else "a"
            if not shutil.which("dnsmasq"):
                return False, "Install dnsmasq; NetworkManager needs it for hotspot DHCP.", ""
            if self._run_cmd(["nmcli", "radio", "wifi"], required=True).strip() != "enabled":
                enabled = self._run_nmcli(["nmcli", "radio", "wifi", "on"], timeout=10)
                if enabled.returncode:
                    return False, f"Could not enable Wi-Fi: {enabled.stderr.strip()}", enabled.stderr
            active = self._hotspot_profile(active_only=True)
            candidates = [item for item in self._hotspot_devices() if item["2.4" if band == "bg" else "5"] == "yes"]
            if self._hotspot_device != "Auto":
                candidates = [item for item in candidates if item["device"] == self._hotspot_device]
            else:
                candidates.sort(key=lambda item: (
                    item["device"] != (active or {}).get("device"), item["state"].startswith("connected")
                ))
            if not candidates:
                return False, "No available Wi-Fi adapter supports this hotspot band.", ""
            device = candidates[0]["device"]
            if active and active["device"] != device:
                return False, "Stop the active hotspot before moving it to another adapter.", ""
            firewall_issue = self._prepare_hotspot_firewall(device)
            if firewall_issue:
                return False, firewall_issue, ""
            previous_uuid = ""
            if not active:
                rows = self._run_cmd(["nmcli", "-t", "-f", "UUID,DEVICE", "connection", "show", "--active"], required=True)
                for line in rows.splitlines():
                    fields = _split_nmcli_line(line)
                    if len(fields) > 1 and fields[1] == device:
                        previous_uuid = fields[0]
                        break
            password = self._hotspot_password or secrets.token_urlsafe(12)
            existing = self._hotspot_profile(uuid_filter=active["uuid"]) if active else self._hotspot_profile()
            settings_fields = (
                "802-11-wireless.mode", "802-11-wireless.ssid", "802-11-wireless.band",
                "802-11-wireless-security.key-mgmt", "802-11-wireless-security.psk",
                "ipv4.method", "ipv6.method", "connection.autoconnect",
            )
            original_settings = None
            if existing:
                try:
                    original_settings = self._run_cmd([
                        "nmcli", "-s", "-e", "no", "-g", ",".join(settings_fields),
                        "connection", "show", "uuid", existing["uuid"],
                    ], required=True).splitlines()
                except RuntimeError as exc:
                    return False, f"Could not read hotspot settings before changing them: {exc}", ""
                if len(original_settings) != len(settings_fields):
                    return False, "Could not read all hotspot settings before changing them.", ""
                if active and (existing["ssid"], existing["password"], original_settings[2]) == (self._hotspot_ssid, password, band):
                    return True, f"Hotspot is already active on {device}.", ""
            if existing:
                cmd = ["nmcli", "connection", "modify", "uuid", existing["uuid"]]
            else:
                new_uuid = str(uuid_module.uuid4())
                cmd = ["nmcli", "connection", "add", "type", "wifi", "ifname", "*",
                       "con-name", HOTSPOT_PROFILE, "connection.uuid", new_uuid]
            desired = ("ap", self._hotspot_ssid, band, "wpa-psk", password, "shared", "disabled", "no")
            cmd += [argument for pair in zip(settings_fields, desired) for argument in pair]
            profile = existing
            attempted_write = False
            recovery_recorded = False
            try:
                # Persist the previous connection before activation can displace
                # it, including when activation times out or the TUI exits.
                if previous_uuid:
                    self._remember_hotspot_previous(device, previous_uuid)
                    recovery_recorded = True
                attempted_write = True
                changed = self._run_nmcli(cmd, timeout=15)
                if changed.returncode:
                    raise RuntimeError(f"Could not save hotspot: {changed.stderr.strip()}")
                if not profile:
                    profile = self._hotspot_profile(uuid_filter=new_uuid)
                if not profile:
                    raise RuntimeError("Hotspot profile was saved but cannot be found.")
                activated = self._run_nmcli(["nmcli", "connection", "up", "uuid", profile["uuid"], "ifname", device], timeout=30)
                if activated.returncode:
                    raise RuntimeError(f"Could not start hotspot: {activated.stderr.strip()}")
            except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
                recovery_errors = []
                if attempted_write and existing and original_settings is not None:
                    restore_cmd = ["nmcli", "connection", "modify", "uuid", existing["uuid"]]
                    restore_cmd += [argument for pair in zip(settings_fields, original_settings) for argument in pair]
                    try:
                        restored = self._run_nmcli(restore_cmd, timeout=15)
                        readback = self._run_cmd([
                            "nmcli", "-s", "-e", "no", "-g", ",".join(settings_fields),
                            "connection", "show", "uuid", existing["uuid"],
                        ], required=True).splitlines()
                        if restored.returncode or readback != original_settings:
                            recovery_errors.append("previous hotspot settings could not be restored")
                    except (OSError, subprocess.TimeoutExpired, RuntimeError):
                        recovery_errors.append("previous hotspot settings could not be verified")
                restore_uuid = previous_uuid or (active["uuid"] if active else "")
                if attempted_write and restore_uuid:
                    try:
                        restored = self._run_nmcli(["nmcli", "connection", "up", "uuid", restore_uuid, "ifname", device], timeout=30)
                        if restored.returncode:
                            recovery_errors.append(f"previous connection did not reconnect: {restored.stderr.strip()}")
                    except (OSError, subprocess.TimeoutExpired) as recovery_exc:
                        recovery_errors.append(f"previous connection recovery failed: {recovery_exc}")
                if recovery_recorded and not recovery_errors:
                    try:
                        self._remember_hotspot_previous("", "")
                    except OSError:
                        recovery_errors.append("could not clear recovery record")
                self.rescan_event.set()
                detail = "; ".join(recovery_errors)
                return False, command_failure(exc) + (f"; {detail}" if detail else "; previous state restored" if existing or previous_uuid else ""), ""
            self._hotspot_password = password
            try:
                if previous_uuid:
                    self._remember_hotspot_previous(device, previous_uuid)
                elif not active:
                    self._remember_hotspot_previous("", "")
            except OSError as exc:
                return True, f"Hotspot active, but previous Wi-Fi could not be remembered: {exc}", ""
            self.rescan_event.set()
            address = self._run_cmd(["nmcli", "-g", "IP4.ADDRESS", "device", "show", device]).strip()
            return True, f"Hotspot active on {device}; laptop address {address or 'pending DHCP setup'}. Internet is optional.", ""

        if key == "stop_hotspot":
            profile = self._hotspot_profile(active_only=True)
            if not profile:
                return True, "Hotspot is already stopped.", ""
            res = self._run_nmcli(
                ["nmcli", "connection", "down", "uuid", profile["uuid"]],
                timeout=10,
            )
            if res.returncode == 0:
                self.rescan_event.set()
                previous = self._hotspot_previous()
                if previous.get("device") == profile["device"] and previous.get("uuid"):
                    try:
                        restored = self._run_nmcli(
                            ["nmcli", "connection", "up", "uuid", previous["uuid"], "ifname", profile["device"]],
                            timeout=30)
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        return True, f"Hotspot stopped; previous Wi-Fi could not be restored: {exc}. Recovery record retained.", ""
                    if restored.returncode:
                        return True, f"Hotspot stopped; previous Wi-Fi did not reconnect: {restored.stderr.strip()}", ""
                try:
                    self._remember_hotspot_previous("", "")
                except OSError as exc:
                    return True, f"Hotspot stopped; could not clear its recovery record: {exc}", ""
                return True, "Hotspot stopped; previous Wi-Fi restored if available.", ""
            return False, f"Failed: {res.stderr.strip()}", res.stderr

        if key == "qr_hotspot":
            active = self._hotspot_profile(active_only=True)
            saved = self._hotspot_profile(uuid_filter=active["uuid"]) if active else None
            if not active or not saved or not saved["password"]:
                return False, "Start the hotspot to generate and save a password first.", ""
            return self._trigger_qr_viewer(saved["ssid"], saved["password"], "WPA2", False)

        return False, f"Unsupported hotspot action: {key}", ""

    def _handle_network_action(self, key: str, value: str) -> tuple[bool, str, str]:
        if key.startswith("pw__"):
            ssid = key[4:]
            if not value:
                return False, "Password cannot be empty.", ""
            return self._async_connect(ssid, value)

        if key.startswith("cn__"):
            return self._async_connect(key[4:], None)

        if key.startswith("qr_net__"):
            ssid = key[8:]
            network = next((network for network in self._cached_scans if network["ssid"] == ssid), None)
            if not network or network.get("security") != "Open":
                return False, "This network is no longer observed as open; share a saved profile instead.", ""
            return self._trigger_qr_viewer(ssid, "", "Open", False)

        return False, f"Unsupported network action: {key}", ""

    def _handle_saved_action(self, key: str, value: str = "") -> tuple[bool, str, str]:
        if key.startswith("cn__"):
            uuid = key[4:]
            return self._async_connect_saved(uuid, uuid)

        if key.startswith("rc__"):
            uuid = key[4:]
            saved = self._get_saved_wifi()
            name = next((c["name"] for c in saved if c["uuid"] == uuid), uuid)
            return self._async_reconnect(name, uuid)

        if key.startswith("qr_prof__"):
            uuid = key[9:]
            return self._share_profile_qr(uuid)

        if key.startswith("dc__"):
            uuid = key[4:]
            return self._async_disconnect(uuid, uuid)

        if key.startswith("fg__"):
            uuid = key[4:]
            res = self._run_nmcli(
                ["nmcli", "connection", "delete", "uuid", uuid],
                timeout=10,
            )
            if res.returncode == 0:
                self.rescan_event.set()
                return True, "Deleted.", ""
            return False, f"Failed: {res.stderr.strip()}", res.stderr

        if key.startswith("band__"):
            uuid = key[6:]
            ok, msg = self.set_wifi_band_with_rollback(uuid, value)
            return ok, msg, ""

        return False, f"Unsupported saved-profile action: {key}", ""

    def _handle_status_action(self, key: str, value: str = "") -> tuple[bool, str, str]:
        if key == "restart_nm":
            res = subprocess.run(
                ["sudo", "-n", "systemctl", "restart", "NetworkManager"],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15
            )
            if res.returncode == 0:
                self.rescan_event.set()
                return True, "NetworkManager restarted.", ""
            err = res.stderr.strip().lower()
            if "password is required" in err or "sudo:" in err or "polkit" in err or "not authorized" in err:
                return False, "AUTH_REQUIRED", res.stderr
            return False, f"Failed: {res.stderr.strip()}", res.stderr

        if key == "rescan":
            self.rescan_event.set()
            return True, "Rescan triggered.", ""

        return False, f"Unsupported status action: {key}", ""

    def _get_active_dns_provider(self, iface: str = "", gateway: str = "") -> str:
        if not iface:
            return "N/A"
        output = self._run_cmd(["resolvectl", "dns", iface])
        if output and ":" in output:
            servers = output.split(":", 1)[1].split()
        else:
            servers = []
        if not servers:
            servers = self._run_cmd(["nmcli", "-e", "no", "-g", "IP4.DNS,IP6.DNS", "device", "show", iface]).splitlines()
        servers = [server for server in servers if server and server != "--"]
        if not servers:
            return "N/A"
        server = servers[0].split("#", 1)[0]
        providers = {
            "1.1.1.1": "Cloudflare", "1.0.0.1": "Cloudflare",
            "2606:4700:4700::1111": "Cloudflare", "2606:4700:4700::1001": "Cloudflare",
            "8.8.8.8": "Google", "8.8.4.4": "Google",
            "2001:4860:4860::8888": "Google", "2001:4860:4860::8844": "Google",
            "9.9.9.9": "Quad9", "149.112.112.112": "Quad9",
            "2620:fe::fe": "Quad9", "2620:fe::9": "Quad9",
        }
        if server == gateway:
            return f"Router ({server})"
        return providers.get(server, f"Custom ({server})")

    def _handle_speedtest_action(self, key: str) -> tuple[bool, str, str]:
        if self._speedtest_running:
            return False, "Speed test is already running.", ""
        if key not in {"speedtest_full", "speedtest_down", "speedtest_up"}:
            return False, f"Unsupported speed test action: {key}", ""

        mode = "full"
        if key == "speedtest_down":
            mode = "down"
        elif key == "speedtest_up":
            mode = "up"

        # Set flag synchronously to avoid race if spammed before callback runs
        self._speedtest_running = True

        if self.app:
            app = self.app
            def run_interactive_speedtest():
                if self.shutdown_event.is_set() or self.app is not app:
                    self._speedtest_running = False
                    return
                self._speedtest_running = True
                self._speedtest_status = f"Running interactive {mode} test..."
                if mode in ("full", "down"):
                    self._speedtest_down_val = "--"
                if mode in ("full", "up"):
                    self._speedtest_up_val = "--"
                if hasattr(self.app, "_option_cache"):
                    self.app._option_cache.clear()
                self.app._rebuild_indexes()
                self.app._refresh_all_ui()

                try:
                    rich_script = str(Path(__file__).parent / "rich_speedtest.py")
                    with tempfile.TemporaryDirectory(dir=self.cache_dir) as directory:
                        res_file = Path(directory) / "result.json"
                        with self.app.suspend():
                            process = subprocess.Popen([sys.executable, rich_script, mode, str(res_file)])
                            try:
                                process.wait(timeout=60)
                            except BaseException:
                                process.terminate()
                                try:
                                    process.wait(timeout=3)
                                except subprocess.TimeoutExpired:
                                    process.kill()
                                    process.wait()
                                raise
                        if process.returncode or not res_file.exists():
                            self._speedtest_status = "Failed"
                        else:
                            data = json.loads(res_file.read_text(encoding="utf-8"))
                            if not isinstance(data, dict):
                                raise ValueError("Invalid speed-test result")
                            values = {}
                            for direction in ("down", "up"):
                                if data.get(direction) is not None:
                                    value = float(data[direction])
                                    if not math.isfinite(value) or value < 0:
                                        raise ValueError("Invalid speed-test measurement")
                                    values[direction] = value
                            if "down" in values:
                                self._speedtest_down_val = f"{values['down']:.1f} Mbps"
                            if "up" in values:
                                self._speedtest_up_val = f"{values['up']:.1f} Mbps"
                            expected = {"down", "up"} if mode == "full" else {mode}
                            self._speedtest_status = "Cancelled" if data.get("status") == "cancelled" else (
                                "Complete" if data.get("status") == "complete" and expected <= values.keys() else "Failed"
                            )
                except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
                    self._speedtest_status = "Failed"
                    logger.error("Interactive speed test failed: %s", exc)
                finally:
                    self._speedtest_running = False
                    self._rebuild_schema()

            try:
                app.call_from_thread(run_interactive_speedtest)
                return self._speedtest_status != "Failed", f"Speed test: {self._speedtest_status}.", ""
            except RuntimeError as exc:
                self._speedtest_running = False
                return False, f"Could not start speed test: {exc}", ""

        self._speedtest_running = False
        return False, "The network UI is not attached.", ""

    def _handle_clipboard(self, key: str) -> tuple[bool, str, str]:
        if not self.app:
            return False, "App not ready.", ""

        target_item = None
        for tab_idx in range(len(self.app.schema)):
            for item in self.app.schema.get(tab_idx, []):
                if item.key == key and item.scope == "clipboard":
                    target_item = item
                    break
            if target_item:
                break

        if not target_item:
            return False, "Item not found.", ""

        val = self._clipboard_values.get(key, "")

        if not val or val in ("N/A", "None", "--"):
            return False, "Nothing to copy.", ""

        if copy_to_clipboard(val):
            return True, f"Copied: {val}", ""
        return False, "Could not copy to the Wayland clipboard.", ""

    def _safe_call_from_thread(self, func, *args) -> None:
        if not self.app:
            return
        try:
            call_fn = getattr(self.app, "call_from_thread", None)
            if call_fn and callable(call_fn):
                call_fn(func, *args)
        except Exception as e:
            logger.debug(f"call_from_thread error: {e}")

    def _async_rescan_wifi(self) -> None:
        try:
            if hasattr(self.app, "notify_status"):
                self._safe_call_from_thread(self.app.notify_status, "Scanning WiFi networks...")
            scans = self._get_scanned_wifi(rescan="yes")
            if self.shutdown_event.is_set():
                return
            self._cached_scans = scans
            try:
                with tempfile.TemporaryDirectory(dir=self.cache_dir) as directory:
                    replacement = Path(directory) / "wifi_cache.json"
                    replacement.write_text(json.dumps(self._cached_scans), encoding="utf-8")
                    replacement.replace(self.cache_dir / "wifi_cache.json")
            except Exception as e:
                logger.error(f"Cache write error: {e}")
            self._safe_call_from_thread(self._rebuild_schema)
        except Exception as e:
            logger.error("Wi-Fi scan failed: %s", e)
            if self.app:
                self._safe_call_from_thread(self.app.notify_status, f"Wi-Fi scan failed: {command_failure(e)}")
        finally:
            self._scan_running = False

    def _enrich_network_status(
        self, verb: dict[str, str], active_wifi: dict[str, Any] | None,
        active_connections: list[dict[str, Any]] | None = None, *,
        devices: list[dict[str, str]] | None = None,
        uplinks: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        enriched = dict(verb)
        devices = self._devices_cache if devices is None else devices
        uplinks = self._uplinks_cache if uplinks is None else uplinks

        # Keep the displayed profile, address, and gateway on the same route.
        route = None
        route_family = 4
        try:
            routes = json.loads(self._run_cmd(["ip", "-j", "-4", "route", "show", "default"]))
            if routes:
                route = min(routes, key=lambda item: item.get("metric", 0))
        except (TypeError, ValueError):
            pass
        if route is None:
            try:
                routes = json.loads(self._run_cmd(["ip", "-j", "-6", "route", "show", "default"]))
                if routes:
                    route = min(routes, key=lambda item: item.get("metric", 0))
                    route_family = 6
            except (TypeError, ValueError):
                pass
        preferred = self._route_choice().get("uuid") if not route else None
        preferred_link = next((item for item in uplinks if item.get("uuid") == preferred), {})
        iface = route.get("dev", "") if route else preferred_link.get("device", "") or (active_wifi or {}).get("device", "")
        active_wifi = next((connection for connection in active_connections or [] if connection.get("device") == iface), active_wifi)
        if iface:
            if enriched.get("iface") != iface:
                for field in ("ip", "prefix", "freq", "bitrate", "speed", "band", "rx_bytes", "tx_bytes"):
                    enriched.pop(field, None)
            enriched["iface"] = iface
            enriched["phy_iface"] = iface
            enriched["gateway"] = route.get("gateway", "on-link") if route else "N/A"
            if Path(f"/sys/class/net/{iface}/wireless").exists() or Path(f"/sys/class/net/{iface}/phy80211").exists():
                enriched["type"] = "wifi"
            else:
                enriched["type"] = next((item.get("type", "unknown") for item in devices
                                         if item.get("device") == iface), "unknown")
            device = next((item for item in devices if item.get("device") == iface), {})
            profile_name = device.get("connection", "")
            if active_wifi and active_wifi.get("device") == iface:
                enriched["ssid"] = active_wifi.get("ssid", "")
            else:
                source = next((item for item in uplinks if item.get("device") == iface), {})
                enriched["ssid"] = source.get("display_name") or (profile_name if profile_name and profile_name != "--" else iface)

        # 2. IP address & prefix fallback
        if iface and (not enriched.get("ip") or enriched.get("ip") == "N/A"):
            try:
                addr_out = self._run_cmd(["ip", "-o", f"-{route_family}", "addr", "show", "dev", iface])
                pattern = (r"inet6 ([0-9a-fA-F:]+)/(?P<prefix>\d+).*?scope global"
                           if route_family == 6 else r"inet ([\d.]+)/(?P<prefix>\d+)")
                m_ip = re.search(pattern, addr_out)
                if m_ip:
                    enriched["ip"] = m_ip.group(1)
                    enriched["prefix"] = m_ip.group("prefix")
            except Exception:
                pass

        # 3. Rx & Tx bytes fallback for live throughput calculation
        if iface and (not enriched.get("rx_bytes") or not enriched.get("tx_bytes")):
            rx_p = Path(f"/sys/class/net/{iface}/statistics/rx_bytes")
            tx_p = Path(f"/sys/class/net/{iface}/statistics/tx_bytes")
            if rx_p.exists():
                try: enriched["rx_bytes"] = rx_p.read_text().strip()
                except Exception: pass
            if tx_p.exists():
                try: enriched["tx_bytes"] = tx_p.read_text().strip()
                except Exception: pass
            enriched["counter_time"] = time.monotonic()

        # 4. Wi-Fi details fallback if missing
        phy_iface = enriched.get("phy_iface", iface)
        if enriched.get("type") == "wifi" and phy_iface:
            if not enriched.get("freq") or not enriched.get("ssid"):
                try:
                    iw_out = self._run_cmd(["iw", "dev", phy_iface, "link"])
                    if iw_out:
                        for line in iw_out.splitlines():
                            line_str = line.strip()
                            if line_str.startswith("SSID:") and not enriched.get("ssid"):
                                enriched["ssid"] = decode_iw_ssid(line.lstrip().removeprefix("SSID:").removeprefix(" "))
                            elif line_str.startswith("freq:"):
                                freq_val = line_str.split("freq:", 1)[1].strip()
                                enriched["freq"] = freq_val
                                b = band_for_freq(freq_val)
                                if b:
                                    enriched["band"] = b
                            elif "tx bitrate:" in line_str:
                                parts = line_str.split("tx bitrate:", 1)[1].strip().split()
                                if len(parts) >= 2:
                                    enriched["bitrate"] = f"{parts[0]} {parts[1]}"
                except Exception:
                    pass

        if enriched.get("type") == "ethernet" and iface:
            try:
                speed = int(Path(f"/sys/class/net/{iface}/speed").read_text())
                if speed > 0:
                    enriched["speed"] = str(speed)
            except (OSError, ValueError):
                pass

        # ICMP samples describe this interface and address family, not a
        # generic claim that the internet is reachable through another link.
        gw = enriched.get("gateway")
        ping_targets: list[tuple[str, str]] = []
        if iface and gw and gw not in {"N/A", "on-link"} and "router_ping_ms" not in enriched:
            ping_targets.append(("router_ping_ms", gw))
        if iface and "internet_ping_ms" not in enriched:
            internet_target = "2606:4700:4700::1111" if route_family == 6 else "1.1.1.1"
            enriched["internet_ping_target"] = internet_target
            ping_targets.append(("internet_ping_ms", internet_target))

        if ping_targets:
            def _probe_ping(target_host: str) -> str | None:
                try:
                    p_res = subprocess.run(
                        ["ping", f"-{route_family}", "-I", iface, "-n", "-c", "1", "-W", "1", target_host],
                        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=2, env=self._get_exec_env(),
                    )
                    m = re.search(r"time[=<]([\d.]+)", p_res.stdout)
                    return m.group(1) if p_res.returncode == 0 and m else None
                except Exception:
                    return None

            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(ping_targets)) as executor:
                    future_to_key = {executor.submit(_probe_ping, host): key for key, host in ping_targets}
                    for fut in concurrent.futures.as_completed(future_to_key):
                        key = future_to_key[fut]
                        val = fut.result()
                        enriched[key] = val
            except Exception:
                pass

        return enriched

    # =========================================================================
    #  BACKGROUND POLLING WORKER
    # =========================================================================

    def _background_loop(self) -> None:
        """Daemon thread: polls radio, active state, live throughput, ping stats every 1.5s."""
        last_scan_time = time.monotonic()

        while not self.shutdown_event.is_set():
            try:
                now = time.monotonic()
                radio = self._run_cmd(["nmcli", "radio", "wifi"], required=True).strip()
                if radio not in {"enabled", "disabled"}:
                    raise RuntimeError(f"Unexpected NetworkManager radio state: {radio!r}")
                active_connections = self._get_active_wifi_connections()
                active = active_connections[0] if active_connections else None
                saved = self._get_saved_wifi()

                devices = self._get_nmcli_devices()
                details = self._get_device_details_map()
                if now - self._last_uplink_refresh >= 10:
                    uplinks = self._active_uplinks()
                    hotspot_devices = self._hotspot_devices()
                    self._last_uplink_refresh = now
                else:
                    uplinks, hotspot_devices = self._uplinks_cache, self._hotspot_devices_cache

                # Enrich status with physical interface & real gateway detection & live throughput natively
                enriched_info = self._enrich_network_status({}, active, active_connections, devices=devices, uplinks=uplinks)
                active = next((connection for connection in active_connections if connection["device"] == enriched_info.get("iface")), active)
                throughput = throughput_state(self._tp_state, enriched_info, enriched_info.get("counter_time", time.monotonic()))
                ping = ping_latency_state(self._ping_state, enriched_info, limit=24, average_limit=5)
                dns = self._get_active_dns_provider(enriched_info.get("iface", ""), enriched_info.get("gateway", ""))

                bands = []
                pinned_band = "Auto"
                bands_by_uuid = {}
                pinned_bands_by_uuid = {}
                for connection in active_connections:
                    current_band = enriched_info.get("band", band_for_freq(enriched_info.get("freq", ""))) if connection is active else ""
                    profile_bands = self.get_available_bands_for_ssid(connection["device"], connection["ssid"], current_band)
                    raw_band = self._run_cmd([
                        "nmcli", "-e", "no", "-g", "802-11-wireless.band",
                        "connection", "show", "uuid", connection["uuid"],
                    ], required=True).strip()
                    profile_band = band_label(band_from_nm(raw_band))
                    bands_by_uuid[connection["uuid"]] = profile_bands
                    pinned_bands_by_uuid[connection["uuid"]] = profile_band
                    if connection is active:
                        bands, pinned_band = profile_bands, profile_band

                hotspot = self._hotspot_profile(active_only=True)
                clients = self._get_hotspot_clients(hotspot["device"]) if hotspot else 0
                address = (self._run_cmd([
                    "nmcli", "-g", "IP4.ADDRESS", "device", "show", hotspot["device"]
                ]).strip() or "pending") if hotspot else "N/A"
                snapshot = {
                    "radio": radio == "enabled", "active": active, "saved": saved,
                    "active_connections": active_connections,
                    "routes": {family: self._default_route(family) for family in (4, 6)},
                    "route_choice": self._route_choice(), "bands": bands,
                    "pinned_band": pinned_band, "hotspot": hotspot,
                    "bands_by_uuid": bands_by_uuid, "pinned_bands_by_uuid": pinned_bands_by_uuid,
                    "clients": clients, "address": address,
                    "verbose": enriched_info, "throughput": throughput,
                    "ping": ping, "dns": dns, "devices": devices, "details": details,
                    "uplinks": uplinks, "hotspot_devices": hotspot_devices,
                }

                should_scan = self.rescan_event.is_set() or (now - last_scan_time > 25.0)

                if should_scan and radio == "enabled" and not self._scan_running:
                    self.rescan_event.clear()
                    last_scan_time = now
                    self._scan_running = True
                    self._scan_thread = threading.Thread(target=self._async_rescan_wifi, daemon=True)
                    self._scan_thread.start()

                self._safe_call_from_thread(self._apply_poll_snapshot, snapshot)
                if self._last_poll_error:
                    self._last_poll_error = ""
                    if self.app and hasattr(self.app, "notify_status"):
                        self._safe_call_from_thread(self.app.notify_status, "NetworkManager status recovered.")

            except Exception as e:
                error = str(e)
                if error != self._last_poll_error:
                    logger.error("Background loop error: %s", e)
                    self._last_poll_error = error
                    if self.app and hasattr(self.app, "notify_status"):
                        self._safe_call_from_thread(self.app.notify_status, "NetworkManager status is stale; polling failed.")

            self.shutdown_event.wait(1.5)

    # =========================================================================
    #  ASYNC CONNECTION HELPERS & QR VIEWER
    # =========================================================================

    def _get_wifi_credentials(self, target_uuid: str) -> tuple[str, str, str, bool]:
        """Read credentials for exactly one profile UUID."""
        fields = [
            "802-11-wireless.ssid",
            "802-11-wireless-security.key-mgmt",
            "802-11-wireless-security.psk",
            "802-11-wireless-security.wep-key0",
            "802-11-wireless-security.wep-tx-keyidx",
            "802-11-wireless-security.wep-key-type",
            "802-11-wireless.hidden",
            "connection.id"
        ]
        cmd = ["nmcli", "-s", "-e", "no", "-g", ",".join(fields), "connection", "show", "uuid", target_uuid]
        out = self._run_cmd(cmd, required=True)
        lines = out.splitlines()

        if len(lines) != len(fields):
            raise RuntimeError("Incomplete Wi-Fi credential readback")
        ssid = lines[0]
        key_mgmt = lines[1].strip() if len(lines) > 1 else ""
        psk = lines[2] if len(lines) > 2 else ""
        wep_key = lines[3] if len(lines) > 3 else ""
        hidden_str = lines[6].strip()
        if key_mgmt.lower() == "none" and lines[4].strip() not in {"", "0"}:
            raise RuntimeError("Standard Wi-Fi QR codes cannot represent a nonzero WEP key index.")
        if key_mgmt.lower() == "none" and wep_key and lines[5].strip().startswith("2"):
            raise RuntimeError("WEP passphrases require a profile editor; this QR format expects the actual WEP key.")
        password = psk or wep_key

        if not key_mgmt or key_mgmt.lower() == "none":
            if wep_key:
                sec_type = "WEP"
            elif psk:
                sec_type = "WPA"
            else:
                sec_type = "Open"
        elif "wpa" in key_mgmt.lower() or "sae" in key_mgmt.lower() or "psk" in key_mgmt.lower() or "802-1x" in key_mgmt.lower():
            sec_type = key_mgmt.upper()
        elif "wep" in key_mgmt.lower():
            sec_type = "WEP"
        else:
            sec_type = key_mgmt.upper()

        is_hidden = hidden_str.lower() in ("yes", "true", "1")
        return ssid, password, sec_type, is_hidden

    def _share_profile_qr(self, uuid: str) -> tuple[bool, str, str]:
        try:
            ssid, password, security, hidden = self._get_wifi_credentials(uuid)
        except RuntimeError as exc:
            return False, f"Could not read Wi-Fi credentials: {exc}", ""
        if not ssid:
            return False, "The saved Wi-Fi profile has no SSID.", ""
        if security == "OWE":
            return False, "OWE cannot be represented by this Wi-Fi QR format.", ""
        if "802-1X" in security:
            return False, "Enterprise Wi-Fi credentials cannot be shared with this QR format.", ""
        if security != "Open" and not password:
            return False, "The saved Wi-Fi password is unavailable.", ""
        return self._trigger_qr_viewer(ssid, password, security, hidden)

    def _trigger_qr_viewer(self, ssid: str, password: str = "", security: str = "WPA", hidden: bool = False) -> tuple[bool, str, str]:
        if not ssid:
            return False, "No SSID specified for QR code.", ""

        if any(term in security.lower() for term in ("eap", "802-1x", "ieee8021x")):
            if self.app:
                self._safe_call_from_thread(
                    self.app.notify_status,
                    f"Enterprise 802.1X Wi-Fi ({ssid}) cannot be shared via standard QR code."
                )
                self._safe_call_from_thread(self.app.play_reset_sound)
            return False, "Enterprise 802.1X Wi-Fi cannot be shared via standard QR code.", ""

        if self.app:
            def run_interactive_qr():
                try:
                    with self.app.suspend():
                        show_wifi_qr_interactive(ssid, password, security, hidden, interactive=True)
                finally:
                    self._rebuild_schema()

            self.app.call_from_thread(run_interactive_qr)
            return True, f"Opened QR share for {ssid}.", ""

        show_wifi_qr_interactive(ssid, password, security, hidden, interactive=False)
        return True, f"Opened QR share for {ssid}.", ""

    def _async_connect(self, ssid: str, password: str | None) -> tuple[bool, str, str]:
        candidate = next((network for network in self._cached_scans if network["ssid"] == ssid), None)
        if candidate and candidate.get("security") == "Mixed":
            return False, "This SSID advertises differing security configurations; connect using an explicit saved profile.", ""
        cmd = ["nmcli", "device", "wifi", "connect", ssid]
        if candidate and candidate.get("device"):
            cmd.extend(["ifname", candidate["device"]])
        if password:
            cmd.extend(["password", password])
        try:
            res = self._run_nmcli(cmd, timeout=30)
            if res.returncode:
                return False, f"Could not connect to {ssid}: {res.stderr.strip() or f'exit {res.returncode}'}", res.stderr
            return True, f"Connected to {ssid}.", ""
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"Could not connect to {ssid}: {command_failure(exc)}", ""
        finally:
            self.rescan_event.set()

    def _async_connect_saved(self, label: str, uuid: str) -> tuple[bool, str, str]:
        try:
            res = self._run_nmcli(
                ["nmcli", "connection", "up", "uuid", uuid],
                timeout=30,
            )
            if res.returncode:
                return False, f"Could not connect to {label}: {res.stderr.strip() or f'exit {res.returncode}'}", res.stderr
            return True, f"Connected to {label}.", ""
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"Could not connect to {label}: {exc}", ""
        finally:
            self.rescan_event.set()

    def _async_disconnect(self, label: str, uuid: str) -> tuple[bool, str, str]:
        try:
            res = self._run_nmcli(
                ["nmcli", "connection", "down", "uuid", uuid],
                timeout=15,
            )
            if res.returncode:
                return False, f"Could not disconnect {label}: {res.stderr.strip() or f'exit {res.returncode}'}", res.stderr
            return True, f"Disconnected from {label}.", ""
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"Could not disconnect {label}: {exc}", ""
        finally:
            self.rescan_event.set()

    def _async_reconnect(self, label: str, uuid: str) -> tuple[bool, str, str]:
        try:
            connection = next((connection for connection in self._get_active_wifi_connections() if connection["uuid"] == uuid), None)
            if connection is None:
                return False, "The selected connection is no longer active.", ""
            up_cmd = ["nmcli", "connection", "up", "uuid", uuid, "ifname", connection["device"]]
            down = self._run_nmcli(
                ["nmcli", "connection", "down", "uuid", uuid],
                timeout=15,
            )
            if down.returncode:
                return False, f"Could not disconnect {label}: {down.stderr.strip() or f'exit {down.returncode}'}", down.stderr
            self.shutdown_event.wait(0.8)
            up = self._run_nmcli(up_cmd, timeout=30)
            if up.returncode:
                return False, f"Could not reconnect {label}: {up.stderr.strip() or f'exit {up.returncode}'}", up.stderr
            return True, f"Reconnected to {label}.", ""
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            return False, f"Could not reconnect {label}: {exc}", ""
        finally:
            self.rescan_event.set()

    def get_available_bands_for_ssid(self, iface: str, ssid: str, current_band: str = "") -> list[str]:
        """
        Returns list of available bands ('2.4', '5', '6') the AP is broadcasting on for this SSID,
        always including current_band so the active operating band is never missing.
        """
        bands = set()
        if current_band:
            bands.add(current_band)

        if not shutil.which("nmcli"):
            return sorted(list(bands))

        try:
            cmd = ["nmcli", "-e", "no", "-g", "FREQ,SSID", "dev", "wifi", "list"]
            if iface:
                cmd.extend(["ifname", iface])
            cmd.extend(["--rescan", "no"])

            res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=5, env=self._get_exec_env())
            for line in res.stdout.splitlines():
                if not line:
                    continue
                parts = line.split(":", 1)
                freq_str = parts[0].strip()
                line_ssid = parts[1] if len(parts) > 1 else ""
                if line_ssid == ssid:
                    b = band_for_freq(freq_str)
                    if b:
                        bands.add(b)
        except Exception:
            pass

        def band_sort_key(x: str) -> float:
            try:
                return float(x)
            except ValueError:
                return 999.0

        return sorted(list(bands), key=band_sort_key)

    def set_wifi_band_with_rollback(self, profile_uuid: str, target_band_str: str) -> tuple[bool, str]:
        """Change one profile and report the verified result of any recovery."""
        desired = nm_band_for(target_band_str)
        if target_band_str not in {"Auto", "2.4 GHz", "5 GHz", "6 GHz"}:
            return False, f"Unknown Wi-Fi band: {target_band_str}."

        saved = self._get_saved_wifi()
        active_connections = self._get_active_wifi_connections()
        match = next((item for item in saved if item["uuid"] == profile_uuid), None)
        if not match:
            return False, "Wi-Fi profile is no longer available."
        uuid, ssid = match["uuid"], match["ssid"]
        active = next((connection for connection in active_connections if connection["uuid"] == uuid), None)
        is_active = active is not None
        iface = active["device"] if is_active else self._get_wifi_device()
        current_band = self._verbose_info.get("band", band_for_freq(self._verbose_info.get("freq", ""))) if is_active and iface == self._verbose_info.get("iface") else ""
        available = self.get_available_bands_for_ssid(iface, ssid, current_band)
        requested = target_band_str.lower().replace("ghz", "").strip()
        if requested != "auto" and available and requested not in available:
            return False, f"{target_band_str} is not available for {ssid}."

        read_cmd = ["nmcli", "-e", "no", "-g", "802-11-wireless.band", "connection", "show", "uuid", uuid]
        modify_cmd = ["nmcli", "connection", "modify", "uuid", uuid, "802-11-wireless.band"]
        up_cmd = ["nmcli", "connection", "up", "uuid", uuid]
        if is_active:
            up_cmd.extend(["ifname", iface])

        def run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
            return self._run_nmcli(command, timeout=timeout)

        previous: str | None = None
        try:
            before = run(read_cmd, 5)
            if before.returncode:
                return False, f"Could not read the current Wi-Fi band: {before.stderr.strip()}"
            previous = before.stdout.strip()
            if previous not in {"", "bg", "a", "6GHz"}:
                return False, f"Unsupported current Wi-Fi band: {previous}."
            if previous == desired:
                return True, f"Wi-Fi band is already {target_band_str}."

            change = run(modify_cmd + [desired], 5)
            if change.returncode:
                failure = change.stderr.strip() or f"exit {change.returncode}"
                observed = run(read_cmd, 5)
                if observed.returncode == 0 and observed.stdout.strip() == previous:
                    return False, f"Could not switch Wi-Fi band: {failure}"
            else:
                observed = run(read_cmd, 5)
                if observed.returncode or observed.stdout.strip() != desired:
                    failure = observed.stderr.strip() or "profile readback did not match the requested band"
                elif not is_active:
                    self.rescan_event.set()
                    return True, f"Saved {target_band_str} for {ssid}; the inactive profile was not connected."
                else:
                    activation = run(up_cmd, 20)
                    if activation.returncode == 0:
                        self.rescan_event.set()
                        return True, f"Wi-Fi band switched to {target_band_str}."
                    failure = activation.stderr.strip() or f"exit {activation.returncode}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            if previous is None:
                return False, f"Could not read the current Wi-Fi band: {exc}"
            failure = str(exc)

        try:
            restored = run(modify_cmd + [previous], 5)
            reconnected = run(up_cmd, 20) if restored.returncode == 0 and is_active else None
            observed = run(read_cmd, 5)
            rollback_ok = (
                restored.returncode == 0
                and (not is_active or (reconnected is not None and reconnected.returncode == 0))
                and observed.returncode == 0
                and observed.stdout.strip() == previous
            )
        except (OSError, subprocess.TimeoutExpired):
            rollback_ok = False
        self.rescan_event.set()
        detail = "previous band restored" if rollback_ok else "rollback incomplete; inspect the connection"
        return False, f"Could not switch Wi-Fi band: {failure}; {detail}."

    # =========================================================================
    #  DYNAMIC SCHEMA REBUILDER
    # =========================================================================

    def _rebuild_schema(self) -> None:
        """Rebuilds Networks/Saved/Devices tabs in-place. Updates live metrics across all tabs."""
        if not self.app or not self.app.schema:
            return

        radio = self._radio_on
        active = self._active_wifi
        active_connections = self._active_wifi_connections
        saved = self._saved_wifi
        profile_name_counts = Counter(profile["name"] for profile in saved)
        verb = self._verbose_info

        expanded = set()
        collapsed = set()
        for tab_idx in (0, 1):
            for item in self.app.schema.get(tab_idx, []):
                if item.is_parent:
                    uid = f"{item.scope}.{item.key}" if item.scope and item.scope != "DEFAULT" else item.key
                    if item.expanded:
                        expanded.add(uid)
                    else:
                        collapsed.add(uid)

        # ----- Tab 0: Networks -----
        t0 = []
        # Radio toggle always visible on first page — succinct 2-word label
        t0.append(self._make_item(
            label="Wi-Fi Radio", key="wifi_radio", scope="status",
            type_="bool", default=radio, group="Hardware",
            extended_help="Toggle Wi-Fi radio on/off."
        ))
        t0.append(self._make_item(
            label="Rescan", key="rescan", scope="network",
            type_="bool", default=False, group="Actions",
            options=["trigger"],
            extended_help="Scan for nearby networks."
        ))

        if not radio:
            t0.append(self._make_item(
                label="Wi-Fi Off",
                key="wifi_off_notice", scope="network", type_="action", default=":", read_only=True,
                group="Networks"
            ))
        else:
            # Sort scanned wifi using pure sort_wifi_rows logic
            wifi_rows_data = []
            for net in self._cached_scans:
                match = [c for c in saved if c["ssid"] == net["ssid"]]
                row = wifi_row({
                    "ssid": net["ssid"],
                    "connected": any(connection["ssid"] == net["ssid"] for connection in active_connections),
                    "known": len(match) > 0,
                    "signal": net.get("signal", 0),
                    "security": net.get("security", "Open"),
                })
                if row:
                    row["raw_net"] = net
                    row["match"] = match
                    wifi_rows_data.append(row)

            sorted_rows = sort_wifi_rows(wifi_rows_data)
            if not sorted_rows:
                t0.append(self._make_item(
                    label="No visible networks; use Rescan", key="empty_scan", scope="network",
                    type_="action", default=":", read_only=True, group="Networks",
                    extended_help="No access points were returned. Check radio availability or rescan. Hidden-network setup requires a profile editor."
                ))

            for r in sorted_rows:
                ssid = r["ssid"]
                signal = r["signal"]
                security = r["security"]
                in_use = r["connected"]
                is_saved = r["known"]
                match = r["match"]
                bar = self._signal_bar(signal)

                if in_use:
                    icon, status_lbl = "●", "Active"
                elif is_saved:
                    icon, status_lbl = "◉", "Saved"
                else:
                    icon, status_lbl = "○", "New"

                group_name = "Saved Networks" if (in_use or is_saved) else "Available Networks"

                label = f"{icon} {status_lbl:<6} {ssid:<24} {security:<10} {signal}% {bar}"
                pkey = f"net__{ssid}"
                parent_uid = f"network.{pkey}"
                is_expanded = (parent_uid in expanded) if parent_uid in expanded else (in_use and parent_uid not in collapsed)

                t0.append(self._make_item(
                    label=label, key=pkey, scope="network", type_="menu", default=None,
                    is_parent=True, expanded=is_expanded, group=group_name,
                    extended_help=f"SSID: {escape_markdown(ssid)}. Security: {security}. Saved actions target individual profile UUIDs."
                ))

                if in_use:
                    for connection in active_connections:
                        if connection["ssid"] != ssid:
                            continue
                        uuid = connection["uuid"]
                        suffix = f" ({connection['device']})"
                        for title, prefix in (("Disconnect", "dc"), ("Reconnect", "rc"), ("Share QR", "qr_prof")):
                            t0.append(self._make_item(
                                label=title + suffix, key=f"{prefix}__{uuid}", scope="saved_action",
                                type_="bool", default=False, parent_ref=parent_uid, options=["trigger"],
                                extended_help=f"{title} for {escape_markdown(ssid)} on {connection['device']}."
                            ))
                        pinned_band = self._pinned_bands_by_uuid.get(uuid, "Auto")
                        available = self._bands_by_uuid.get(uuid, [])
                        band_options = ["Auto"] + [f"{band} GHz" for band in available]
                        if pinned_band not in band_options:
                            band_options.append(pinned_band)
                        t0.append(self._make_item(
                            label=f"Band{suffix}: {pinned_band}", key=f"band__{uuid}", scope="saved_action",
                            type_="cycle", default=pinned_band, options=band_options,
                            parent_ref=parent_uid, extended_help=f"Pin the band for this profile on {connection['device']}."
                        ))
                elif is_saved:
                    for profile in match:
                        uuid = profile["uuid"]
                        profile_name = profile["name"] + (f" [{profile['uuid'][:8]}]" if profile_name_counts[profile["name"]] > 1 else "")
                        suffix = f" ({profile_name})" if len(match) > 1 else ""
                        t0.append(self._make_item(
                            label=f"Connect{suffix}", key=f"cn__{uuid}", scope="saved_action",
                            type_="bool", default=False, parent_ref=parent_uid, options=["trigger"]
                        ))
                        t0.append(self._make_item(
                            label=f"Forget{suffix}", key=f"fg__{uuid}", scope="saved_action",
                            type_="bool", default=False, parent_ref=parent_uid, options=["trigger"],
                            confirm_message=f"Delete **{escape_markdown(profile['name'])}**?"
                        ))
                        t0.append(self._make_item(
                            label=f"Share QR{suffix}", key=f"qr_prof__{uuid}", scope="saved_action",
                            type_="bool", default=False, parent_ref=parent_uid, options=["trigger"],
                            extended_help=f"Show QR for {escape_markdown(profile['name'])}."
                        ))
                        t0.append(self._make_item(
                            label=f"Auto-connect{suffix}", key=uuid, scope="saved",
                            type_="bool", default=profile["autoconnect"], parent_ref=parent_uid
                        ))
                else:
                    if "802.1X" in security.upper() or "802-1X" in security.upper():
                        t0.append(self._make_item(
                            label="Enterprise setup requires a profile editor", key=f"enterprise__{ssid}",
                            scope="network", type_="action", default=":", read_only=True, parent_ref=parent_uid,
                            extended_help="Configure the enterprise authentication method and credentials in a NetworkManager profile editor, then activate its saved profile here."
                        ))
                    elif is_protected(security):
                        t0.append(self._make_item(
                            label="Password", key=f"pw__{ssid}",
                            scope="network", type_="string", default="", parent_ref=parent_uid
                        ))
                    else:
                        t0.append(self._make_item(
                            label="Connect", key=f"cn__{ssid}", scope="network",
                            type_="bool", default=False, parent_ref=parent_uid, options=["trigger"]
                        ))
                        if security == "Open":
                            t0.append(self._make_item(
                                label="Share QR", key=f"qr_net__{ssid}", scope="network",
                                type_="bool", default=False, parent_ref=parent_uid, options=["trigger"],
                                extended_help=f"Show QR for {escape_markdown(ssid)}."
                            ))


        # ----- Tab 1: Saved Profiles -----
        t1 = []
        for conn in saved:
            name, uuid, autocon = conn["name"], conn["uuid"], conn["autoconnect"]
            active_profile = next((connection for connection in active_connections if connection["uuid"] == uuid), None)
            is_active = active_profile is not None
            indicator = "●" if is_active else "◉"
            display_name = name + (f" [{uuid[:8]}]" if profile_name_counts[name] > 1 else "")
            pkey = f"prof__{uuid}"
            parent_uid = f"saved.{pkey}"
            is_expanded = (parent_uid in expanded) if parent_uid in expanded else (is_active and parent_uid not in collapsed)

            t1.append(self._make_item(
                label=f"{indicator} {display_name}", key=pkey, scope="saved", type_="menu",
                default=None, is_parent=True, expanded=is_expanded,
                group="Saved Connections",
                extended_help=f"Profile: {escape_markdown(name)}. SSID: {escape_markdown(conn['ssid'])}. UUID: {uuid}."
            ))

            if is_active:
                t1.append(self._make_item(
                    label="Disconnect", key=f"dc__{uuid}", scope="saved_action",
                    type_="bool", default=False, parent_ref=parent_uid, options=["trigger"]
                ))
                t1.append(self._make_item(
                    label="Forget", key=f"fg__{uuid}", scope="saved_action",
                    type_="bool", default=False, parent_ref=parent_uid, options=["trigger"],
                    confirm_message=f"Delete **{escape_markdown(name)}**?"
                ))
                t1.append(self._make_item(
                    label="Reconnect", key=f"rc__{uuid}", scope="saved_action",
                    type_="bool", default=False, parent_ref=parent_uid, options=["trigger"],
                    extended_help=f"Reconnect to {escape_markdown(name)}."
                ))
                t1.append(self._make_item(
                    label="Share QR", key=f"qr_prof__{uuid}", scope="saved_action",
                    type_="bool", default=False, parent_ref=parent_uid, options=["trigger"],
                    extended_help=f"Show QR for {escape_markdown(name)}."
                ))
                t1.append(self._make_item(
                    label="Auto-connect", key=uuid, scope="saved",
                    type_="bool", default=autocon, parent_ref=parent_uid
                ))
                avail_b = self._bands_by_uuid.get(uuid, [])
                band_opts = ["Auto"] + [f"{b} GHz" for b in avail_b]
                pinned_band = self._pinned_bands_by_uuid.get(uuid, "Auto")
                if pinned_band not in band_opts:
                    band_opts.append(pinned_band)
                t1.append(self._make_item(
                    label=f"Band: {pinned_band}", key=f"band__{uuid}", scope="saved_action",
                    type_="cycle", default=pinned_band, options=band_opts,
                    parent_ref=parent_uid,
                    extended_help=f"Pin {escape_markdown(name)} band."
                ))
            else:
                t1.append(self._make_item(
                    label="Connect", key=f"cn__{uuid}", scope="saved_action",
                    type_="bool", default=False, parent_ref=parent_uid, options=["trigger"]
                ))
                t1.append(self._make_item(
                    label="Forget", key=f"fg__{uuid}", scope="saved_action",
                    type_="bool", default=False, parent_ref=parent_uid, options=["trigger"],
                    confirm_message=f"Delete **{escape_markdown(name)}**?"
                ))
                t1.append(self._make_item(
                    label="Share QR", key=f"qr_prof__{uuid}", scope="saved_action",
                    type_="bool", default=False, parent_ref=parent_uid, options=["trigger"],
                    extended_help=f"Show QR for {escape_markdown(name)}."
                ))
                t1.append(self._make_item(
                    label="Auto-connect", key=uuid, scope="saved",
                    type_="bool", default=autocon, parent_ref=parent_uid
                ))


        # ----- Tab: Devices (nmcli device status) -----
        # Dynamically find Devices tab index by name
        devices_idx = next((idx for idx, name in self.app.tabs.items() if str(name).lower() == "devices"), 3)
        # Preserve expanded state for devices
        dev_expanded = set()
        dev_collapsed = set()
        for item in self.app.schema.get(devices_idx, []):
            if item.is_parent:
                uid = f"{item.scope}.{item.key}" if item.scope and item.scope != "DEFAULT" else item.key
                if item.expanded:
                    dev_expanded.add(uid)
                else:
                    dev_collapsed.add(uid)

        t_devices = []
        device_clip_values = {}
        choice = self._route_selection
        default_v4 = self._default_routes[4]
        default_v6 = self._default_routes[6]
        current_device = default_v4.split(" via ", 1)[0]
        t_devices.append(self._make_item(
            label=f"IPv4 default: {default_v4}", key="default_ipv4", scope="route_info",
            type_="action", default=":", read_only=True, group="Internet Source"))
        t_devices.append(self._make_item(
            label=f"IPv6 default: {default_v6}", key="default_ipv6", scope="route_info",
            type_="action", default=":", read_only=True, group="Internet Source"))
        t_devices.append(self._make_item(
            label="● System priorities" if not choice else "○ System priorities", key="automatic", scope="route",
            type_="bool", default=False, options=["trigger"], group="Internet Source",
            extended_help="Restore the original route metrics and default-route eligibility of every profile changed by this menu. Externally edited profiles are left alone."))
        for uplink in self._uplinks_cache:
            selected = choice.get("uuid") == uplink["uuid"]
            note = " • current IPv4" if uplink["device"] == current_device else ""
            if uplink["ipv4_never_default"] == "yes":
                note += " • local-only"
            elif not uplink["ipv4_gateway"]:
                note += " • no gateway shown"
            t_devices.append(self._make_item(
                label=f"{'●' if selected else '○'} {uplink['display_name']} ({uplink['device']}){note}",
                key=f"use__{uplink['uuid']}", scope="route", type_="bool", default=False,
                options=["trigger"], group="Internet Source",
                extended_help="Persist a preferred metric for this profile in each address family with a usable gateway. Other eligible links retain default routes for failover; existing local-only profiles remain local-only. System priorities restores the original settings. VPN rules and DNS are separate."))
        if not self._uplinks_cache:
            t_devices.append(self._make_item(
                label="No active selectable connections", key="no_uplinks", scope="route_info",
                type_="action", default=":", read_only=True, group="Internet Source"))
        # Show each device as parent menu
        for d in self._devices_cache:
            dev_name = d.get("device", "")
            dtype = d.get("type", "")
            state = d.get("state", "")
            conn = d.get("connection", "") or "--"
            det = self._device_details.get(dev_name, {})

            sl = state.lower().strip()
            if sl.startswith("connected"):
                icon, grp = "●", "Connected"
            elif sl.startswith("disconnected"):
                icon, grp = "○", "Disconnected"
            elif sl.startswith("unavailable"):
                icon, grp = "◯", "Unavailable"
            else:
                icon, grp = "·", "Other"

            pkey = f"dev__{dev_name}"
            parent_uid = f"devices.{pkey}"
            is_exp = (parent_uid in dev_expanded) if parent_uid in dev_expanded else (sl.startswith("connected") and parent_uid not in dev_collapsed)
            label = f"{icon} {dev_name:<14} {dtype:<10} {state}"
            if conn and conn != "--":
                label += f"  {conn}"
            t_devices.append(self._make_item(
                label=label, key=pkey, scope="devices", type_="menu", default=None,
                is_parent=True, expanded=is_exp, group=grp,
                extended_help=f"Device {dev_name}. Expand to inspect its observed link settings."
            ))

            details_rows = [
                ("MAC", "mac", det.get("GENERAL.HWADDR", "")),
                ("MTU", "mtu", det.get("GENERAL.MTU", "")),
                ("Driver", "drv", det.get("GENERAL.DRIVER", "")),
            ]
            for family in (4, 6):
                for field, title in (("ADDRESS", "address"), ("GATEWAY", "gateway"), ("DNS", "DNS")):
                    prefix = f"IP{family}.{field}"
                    for property_key, raw in det.items():
                        if property_key == prefix or property_key.startswith(prefix + "["):
                            details_rows.append((f"IPv{family} {title}", f"{prefix}__{raw}", raw))
            details_rows += [("Type", "type", dtype), ("State", "state", state), ("Connection", "conn", conn)]
            for title, field_key, raw in details_rows:
                if not raw or raw in {"--", "(unknown)"}:
                    continue
                copy_key = f"{field_key}__{dev_name}"
                device_clip_values[copy_key] = raw
                t_devices.append(self._make_item(
                    label=f"{title}: {raw}", key=copy_key, scope="clipboard", type_="bool", default=False,
                    options=["copy"], parent_ref=parent_uid,
                    extended_help=f"Observed {title.lower()} for {dev_name}. Activate to copy the full value."
                ))

        if not self._devices_cache:
            t_devices.append(self._make_item(label="No Devices", key="no_devices", scope="devices", type_="action", default=":", read_only=True, group="Devices"))

        # Status actions retain the displayed profile identity across dialogs and refreshes.
        status_items = [item for item in self.app.schema.get(2, []) if item.scope != "active_wifi_action"]
        for connection in active_connections:
            uuid = connection["uuid"]
            for title, prefix in (("Disconnect", "dc"), ("Reconnect", "rc"), ("Share QR", "qr_prof")):
                status_items.append(self._make_item(
                    label=f"{title}: {connection['ssid']} ({connection['device']})", key=f"{prefix}__{uuid}",
                    scope="active_wifi_action", type_="bool", default=False, options=["trigger"], group="Actions",
                    extended_help=f"{title} for the displayed profile on {connection['device']}."
                ))
        previous_structure = getattr(self.app, "_schema_dirty_counter", None)
        inventory_applied = self.app._replace_dynamic_tabs({0: t0, 1: t1, 2: status_items, devices_idx: t_devices})
        if inventory_applied:
            self._device_clip_values = device_clip_values

        # ----- Update dynamic labels across all tabs -----
        verb = self._verbose_info
        iface_name = verb.get("iface", "")
        conn_type = verb.get("type", "disconnected" if not iface_name else "ethernet")
        ssid_label = verb.get("ssid", "None")
        ip_label = verb.get("ip", "N/A")
        prefix_label = verb.get("prefix", "")
        if ip_label != "N/A" and prefix_label:
            ip_label = f"{ip_label}/{prefix_label}"
        gateway_label = verb.get("gateway", "N/A") or "N/A"

        # Detail string using header_detail pure logic
        link_detail_str = header_detail(verb) or verb.get("bitrate", "N/A")

        # Connection status string using connection_icon pure logic
        sig_dbm = verb.get("signal_dbm", "")
        sig_pct = 70 if sig_dbm else 0
        icon_str = connection_icon(conn_type, sig_pct)
        conn_status_label = f"{icon_str} {conn_type.upper()} ({ssid_label})"

        # Throughput & Ping values
        dl_rate_str = format_rate(self._tp_state.get("download_rate", 0))
        ul_rate_str = format_rate(self._tp_state.get("upload_rate", 0))
        rx_total_str = format_bytes(self._tp_state.get("total_rx", verb.get("rx_bytes", 0)))
        tx_total_str = format_bytes(self._tp_state.get("total_tx", verb.get("tx_bytes", 0)))

        router_ping_str = format_ping_latency(self._ping_state.get("router_ping_latency"))
        internet_ping_str = format_ping_latency(self._ping_state.get("internet_ping_latency"))
        packet_loss_str = format_packet_loss(self._ping_state.get("internet_ping_packet_loss"))

        hotspot = self._active_hotspot
        if hotspot:
            status_text = "Active"
            clients_text = f"{self._hotspot_clients} connected" if self._hotspot_clients is not None else "Unknown"
            address = self._hotspot_address
        else:
            status_text = "Inactive"
            clients_text = "N/A"
            address = "N/A"

        # Mutable controls may contain a draft or an in-flight save. Inventory
        # reconciliation already defers positional replacement in these cases.
        preserve_edits = bool(
            getattr(self.app, "pending_commits", None)
            or getattr(self.app, "_save_tasks", None)
            or getattr(self.app, "_save_timers", None)
            or getattr(self.app, "_save_auth_pending", None)
            or (hasattr(self.app, "_modal_active") and self.app._modal_active())
        )
        mutable_keys = {"wifi_radio", "hotspot_device", "hotspot_ssid", "hotspot_password"}

        # Update all tabs agnostic of exact tab index
        for tab_items in self.app.schema.values():
            for item in tab_items:
                if preserve_edits and item.key in mutable_keys:
                    continue
                if item.key == "wifi_radio":
                    item.value = radio
                elif item.key == "status_type":
                    item.label = f"Connection: {conn_status_label}"
                elif item.key == "status_ssid":
                    item.label = f"{'SSID' if conn_type == 'wifi' else 'Source'}: {ssid_label}"
                elif item.key == "status_ip":
                    item.label = f"IP: {ip_label}"
                elif item.key == "status_gateway":
                    item.label = f"Gateway: {gateway_label}"
                elif item.key == "status_detail":
                    item.label = f"Link: {link_detail_str}"
                elif item.key == "status_device":
                    item.label = f"Iface: {iface_name or 'N/A'}"
                elif item.key == "throughput_down":
                    item.label = f"Down: ↓ {dl_rate_str}"
                elif item.key == "throughput_up":
                    item.label = f"Up: ↑ {ul_rate_str}"
                elif item.key == "throughput_rx_total":
                    item.label = f"Down Total: ↓ {rx_total_str}"
                elif item.key == "throughput_tx_total":
                    item.label = f"Up Total: ↑ {tx_total_str}"
                elif item.key == "ping_router":
                    item.label = f"Router Ping: {router_ping_str}"
                elif item.key == "ping_internet":
                    item.label = f"Internet Ping: {internet_ping_str}"
                elif item.key == "ping_packet_loss":
                    item.label = f"Loss: {packet_loss_str}"
                elif item.key == "dns_current":
                    item.label = f"DNS: {self._dns_provider}"
                elif item.key == "speedtest_status":
                    item.label = f"Status: {self._speedtest_status}"
                elif item.key == "speedtest_down_result":
                    item.label = f"Down: {self._speedtest_down_val}"
                elif item.key == "speedtest_up_result":
                    item.label = f"Up: {self._speedtest_up_val}"
                elif item.key == "hotspot_status_info":
                    item.label = f"Status: {status_text}"
                elif item.key == "hotspot_clients_info":
                    item.label = f"Clients: {clients_text}"
                elif item.key == "hotspot_address_info":
                    item.label = f"Laptop IP: {address}"
                elif item.key == "hotspot_device":
                    options = ["Auto"] + [candidate["device"] for candidate in self._hotspot_devices_cache]
                    unavailable = self._hotspot_device not in options
                    if unavailable:
                        options.append(self._hotspot_device)
                    item.options = options
                    item.value = self._hotspot_device
                    item.label = f"Adapter: {self._hotspot_device}" + (" (unavailable)" if unavailable else "")
                elif item.key == "hotspot_ssid":
                    item.value = self._hotspot_ssid
                elif item.key == "hotspot_password":
                    item.value = self._hotspot_password

        self._clipboard_values = {
            **getattr(self, "_device_clip_values", {}),
            "status_type": conn_type.upper(), "status_ssid": ssid_label,
            "status_ip": ip_label, "status_gateway": gateway_label,
            "status_detail": link_detail_str, "status_device": iface_name,
            "throughput_down": dl_rate_str, "throughput_up": ul_rate_str,
            "throughput_rx_total": rx_total_str, "throughput_tx_total": tx_total_str,
            "ping_router": router_ping_str, "ping_internet": internet_ping_str,
            "ping_packet_loss": packet_loss_str, "dns_current": self._dns_provider,
            "speedtest_down_result": self._speedtest_down_val,
            "speedtest_up_result": self._speedtest_up_val,
        }

        # Label/value changes are already part of the option cache key.
        if hasattr(self.app, "_rebuild_indexes") and (
            previous_structure is None or previous_structure != self.app._schema_dirty_counter
        ):
            self.app._rebuild_indexes()
        if hasattr(self.app, "_refresh_all_ui"):
            self.app._refresh_all_ui()

    # =========================================================================
    #  ITEM FACTORY & HELPER METHODS
    # =========================================================================

    @staticmethod
    def _make_item(**kwargs) -> ConfigItem:
        item = ConfigItem(**kwargs)
        item.exists_in_target = True
        item.initial_value = item.value
        item._initial_loaded = True
        return item

    @staticmethod
    def _get_exec_env() -> dict[str, str]:
        env = os.environ.copy()
        env["LC_ALL"] = "C"
        user_bin = str(Path.home() / ".local" / "bin")
        current_path = env.get("PATH", "")
        if user_bin not in current_path:
            env["PATH"] = f"{user_bin}:{current_path}"
        return env

    @staticmethod
    def _run_nmcli(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
        """Let nmcli report its own timeout before the process deadline expires."""
        return subprocess.run(
            [args[0], "--colors", "no", "--wait", str(max(1, timeout - 2)), *args[1:]],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            env=NetworkManagerEngine._get_exec_env(), timeout=timeout,
        )

    def _run_cmd(self, args: list[str], timeout: int = 5, required: bool = False) -> str:
        try:
            res = self._run_nmcli(args, timeout) if args[0] == "nmcli" else subprocess.run(
                args, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                env=self._get_exec_env(), timeout=timeout,
            )
            if res.returncode:
                if required:
                    raise RuntimeError(res.stderr.strip() or f"{args[0]} exited with status {res.returncode}")
                return ""
            return res.stdout
        except (OSError, subprocess.TimeoutExpired) as exc:
            if required:
                raise RuntimeError(f"{args[0]} failed: {exc}") from exc
            return ""

    def _get_wifi_device(self) -> str:
        for line in self._run_cmd(["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"]).splitlines():
            parts = _split_nmcli_line(line)
            if len(parts) >= 2 and parts[1] == "wifi":
                return parts[0]
        return ""

    def _get_active_wifi_connections(self) -> list[dict[str, Any]]:
        connections = []
        for line in self._run_cmd(["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"], required=True).splitlines():
            if not line:
                continue
            parts = _split_nmcli_line(line)
            if len(parts) >= 4 and parts[2] == "802-11-wireless":
                uuid = parts[1]
                properties = self._run_cmd([
                    "nmcli", "-t", "-f", "802-11-wireless.mode,802-11-wireless.ssid",
                    "connection", "show", "uuid", uuid,
                ], required=True)
                values = dict(_split_nmcli_line(line) for line in properties.splitlines())
                if not {"802-11-wireless.mode", "802-11-wireless.ssid"} <= values.keys():
                    raise RuntimeError("Incomplete active Wi-Fi profile readback")
                mode = "ap" if values["802-11-wireless.mode"] == "ap" else "infra"
                if mode == "ap":
                    continue
                ssid = values["802-11-wireless.ssid"]
                connections.append({"ssid": ssid, "name": parts[0], "uuid": uuid, "device": parts[3], "mode": mode})
        return connections

    def _get_active_wifi_connection(self) -> dict[str, Any] | None:
        connections = self._get_active_wifi_connections()
        current_iface = getattr(self, "_verbose_info", {}).get("iface", "")
        return next((connection for connection in connections if connection["device"] == current_iface),
                    connections[0] if connections else None)

    def _get_saved_wifi(self) -> list[dict[str, Any]]:
        with self._profile_lock:
            conns = []
            now = time.monotonic()
            for line in self._run_cmd(["nmcli", "-t", "-f", "NAME,UUID,TYPE,AUTOCONNECT", "connection", "show"], required=True).splitlines():
                if not line:
                    continue
                parts = _split_nmcli_line(line)
                if len(parts) >= 4 and parts[2] == "802-11-wireless":
                    uuid = parts[1]
                    cached = self._profile_ssids.get(uuid)
                    if cached is None or now - cached[0] >= 10:
                        ssid = self._run_cmd([
                            "nmcli", "-e", "no", "-g", "802-11-wireless.ssid",
                            "connection", "show", "uuid", uuid,
                        ], required=True).rstrip("\n")
                        self._profile_ssids[uuid] = (now, ssid)
                    else:
                        ssid = cached[1]
                    conns.append({"name": parts[0], "ssid": ssid, "uuid": uuid, "autoconnect": parts[3] == "yes"})
            self._profile_ssids = {conn["uuid"]: self._profile_ssids[conn["uuid"]] for conn in conns}
            return conns

    def _get_scanned_wifi(self, rescan: str = "no") -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        output = self._run_cmd([
            "nmcli", "-t", "-f", "IN-USE,SSID,SECURITY,SIGNAL,DEVICE,BSSID",
            "device", "wifi", "list", "--rescan", rescan,
        ], timeout=15 if rescan == "yes" else 5, required=True)
        for line in output.splitlines():
            parts = _split_nmcli_line(line)
            if len(parts) < 6 or not parts[1]:
                continue
            try:
                signal = int(parts[3])
            except ValueError:
                continue
            record = {
                "in_use": parts[0].strip() == "*", "ssid": parts[1],
                "security": parts[2] if parts[2] not in {"", "--"} else "Open",
                "signal": signal, "device": parts[4], "bssid": parts[5],
            }
            group = groups.setdefault(record["ssid"], {**record, "access_points": []})
            group["access_points"].append(record)
            if (record["in_use"], record["signal"]) > (group["in_use"], group["signal"]):
                group.update(record)
        for group in groups.values():
            securities = {record["security"] for record in group["access_points"]}
            if len(securities) > 1:
                group["security"] = "Mixed"
        return list(groups.values())

    def _get_hotspot_clients(self, wifi_dev: str | None) -> int | None:
        if not wifi_dev:
            return 0
        try:
            res = subprocess.run(
                ["sudo", "-n", "iw", "dev", wifi_dev, "station", "dump"],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=5
            )
            if res.returncode != 0:
                res = subprocess.run(
                    ["iw", "dev", wifi_dev, "station", "dump"],
                    capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=5
                )
            return len(re.findall(r"^Station", res.stdout, re.MULTILINE)) if res.returncode == 0 else None
        except Exception:
            return None


    def _get_nmcli_devices(self) -> list[dict[str, str]]:
        """Parse `nmcli device status` into filtered list, handling escaped colons."""
        devices: list[dict[str, str]] = []
        out = self._run_cmd(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"], required=True)
        for line in out.splitlines():
            if not line.strip():
                continue
            parts = _split_nmcli_line(line)
            if len(parts) < 4:
                continue
            dev = parts[0] if parts[0] else ""
            dtype = parts[1] if len(parts) > 1 else ""
            state = parts[2] if len(parts) > 2 else ""
            conn = parts[3] if len(parts) > 3 else "" 
            if not dev:
                continue
            devices.append({"device": dev, "type": dtype, "state": state, "connection": conn})
        return devices

    def _get_device_details_map(self) -> dict[str, dict[str, str]]:
        """Parse `nmcli device show` grouped by DEVICE into dict."""
        details: dict[str, dict[str, str]] = {}
        out = self._run_cmd(["nmcli", "-t", "-m", "multiline", "-e", "no", "-f", "GENERAL.DEVICE,GENERAL.TYPE,GENERAL.HWADDR,GENERAL.MTU,GENERAL.STATE,GENERAL.DRIVER,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,IP6.ADDRESS,IP6.GATEWAY,IP6.DNS", "device", "show"], required=True)
        current_dev = None
        cur_map: dict[str, str] = {}
        for line in out.splitlines():
            if not line.strip():
                if current_dev and cur_map:
                    details[current_dev] = dict(cur_map)
                current_dev = None
                cur_map = {}
                continue
            key, separator, val = line.partition(":")
            if not separator:
                continue
            if key == "GENERAL.DEVICE" and val:
                if current_dev and cur_map:
                    details[current_dev] = dict(cur_map)
                    cur_map = {}
                current_dev = val
                cur_map[key] = val
            else:
                if current_dev is None:
                    continue
                if key.startswith(("GENERAL.", "IP4.", "IP6.", "WIRED-PROPERTIES.", "WIFI-PROPERTIES.")):
                    if key not in cur_map:
                        cur_map[key] = val
        if current_dev and cur_map:
            details[current_dev] = dict(cur_map)
        return details

    @staticmethod
    def _is_uuid(s: str) -> bool:
        return bool(re.prefixmatch(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', s, re.IGNORECASE))

    @staticmethod
    def _signal_bar(signal: int) -> str:
        if signal >= 80: return "▂▄▆█"
        if signal >= 60: return "▂▄▆_"
        if signal >= 40: return "▂▄__"
        if signal >= 20: return "▂___"
        return "____"
