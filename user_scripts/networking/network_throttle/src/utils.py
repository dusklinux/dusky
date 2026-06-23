import os
import re
import subprocess
import logging
import json
from pathlib import Path

# Brand name
BRAND_NAME = "Dusky Network Limiter"
SETTINGS_DIR = Path("/home/dusk/.config/dusky/settings/dusky_network_limiter")
SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = SETTINGS_DIR / "network_limiter.db"

class StructuredLogger(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)

def setup_logger(debug: bool = False) -> logging.Logger:
    logger = logging.getLogger("dusky_network_limiter")
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    
    # Avoid duplicate handlers
    if not logger.handlers:
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG if debug else logging.INFO)
        formatter = StructuredLogger()
        ch.setFormatter(formatter)
        logger.addHandler(ch)
        
    return logger

logger = setup_logger()

def parse_bytes(value: str) -> int:
    """Parse human friendly bytes like 50GB, 500MB to integer bytes."""
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([a-zA-Z]+)$", value.strip())
    if not match:
        raise ValueError(f"Invalid byte size format: {value}")
    val, unit = float(match.group(1)), match.group(2).lower()
    units = {
        'b': 1,
        'kb': 1024,
        'mb': 1024**2,
        'gb': 1024**3,
        'tb': 1024**4,
        'k': 1024,
        'm': 1024**2,
        'g': 1024**3,
        't': 1024**4
    }
    if unit not in units:
        raise ValueError(f"Unknown unit: {unit}")
    return int(val * units[unit])

def format_bytes(num_bytes: float) -> str:
    """Format total byte sizes to human friendly strings."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if num_bytes < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PB"

def normalize_rate(value: str) -> str:
    """Normalize rates like '20mbit', '5mbps', '500kbps' to tc-compatible unit strings."""
    value = value.strip().lower()
    match = re.match(r"^(\d+)\s*([a-z]*)$", value)
    if not match:
        raise ValueError(f"Invalid rate format: {value}")
    num, unit = match.group(1), match.group(2)
    if unit in ('', 'm', 'mbit', 'mbps'):
        return f"{num}mbit"
    if unit in ('k', 'kbit', 'kbps'):
        return f"{num}kbit"
    if unit in ('g', 'gbit', 'gbps'):
        return f"{num}gbit"
    if unit in ('b', 'bps', 'bit'):
        return f"{num}bit"
    raise ValueError(f"Unknown rate unit: {unit}")

def format_rate(bytes_per_sec: float) -> str:
    """
    Format live speed.
    Rule: format in KB/s up until 1MB (1000 KB/s) and once it hits 1000 KB/s, switch to MB/s.
    """
    kbs = bytes_per_sec / 1024.0
    if kbs < 1000.0:
        return f"{kbs:.1f} KB/s"
    else:
        mbs = kbs / 1024.0
        return f"{mbs:.2f} MB/s"

def get_default_interface() -> str:
    """Find default egress network interface by parsing routing table."""
    try:
        with open("/proc/net/route", "r") as f:
            lines = f.readlines()
            for line in lines[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "00000000":
                    return parts[0]
    except Exception as e:
        logger.error(f"Failed to read default network interface: {e}")
    return "wlan0"  # Fallback

def send_notification(title: str, message: str) -> None:
    """Send desktop notification via active user DBus under Wayland/Hyprland."""
    # Find display user dusk (UID 1000)
    cmd = [
        "sudo", "-u", "dusk",
        "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
        "notify-send", "-a", BRAND_NAME, title, message
    ]
    try:
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")
