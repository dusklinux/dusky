#!/usr/bin/env python3
import os
import json
import fcntl
import time
from pathlib import Path
from typing import Any
from python.frontend.core_types import BaseEngine

RAPL_BASE = Path("/sys/class/powercap")
STATE_FILE = Path("/dev/shm/dusky_rapl_state.json")

def get_user_home() -> Path:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        user_home = Path(f"/home/{sudo_user}")
        if user_home.exists():
            return user_home
    home_dir = Path("/home")
    if home_dir.exists():
        users = [p for p in home_dir.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name not in ("lost+found", "shared")]
        if len(users) == 1:
            return users[0]
    return Path("~").expanduser()

def safe_read_int(p: Path) -> int | None:
    try:
        return int(p.read_text().strip())
    except (OSError, ValueError):
        return None

def safe_write(p: Path, val: int) -> bool:
    try:
        p.write_text(str(val))
        return True
    except OSError:
        return False

class FastEnergyReader:
    def __init__(self, path: Path):
        try:
            self.fd = os.open(path, os.O_RDONLY)
        except OSError:
            self.fd = None

    def read(self) -> int | None:
        if self.fd is None:
            return None
        try:
            os.lseek(self.fd, 0, os.SEEK_SET)
            return int(os.read(self.fd, 32).decode().strip())
        except (OSError, ValueError):
            return None

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

class PkgThrottleEngine(BaseEngine):
    def __init__(self, config_path: str = ""):
        self.domain = self.find_package_domain()
        self.energy_file = self.domain / "energy_uj" if self.domain else None
        self.reader = None
        self.last_e = None
        self.last_t = None
        self.max_energy = safe_read_int(self.domain / "max_energy_range_uj") or 0 if self.domain else 0
        if self.domain:
            self._ensure_state_exists()

        if self.energy_file and self.energy_file.exists():
            self.reader = FastEnergyReader(self.energy_file)
            self.last_e = self.reader.read()
            self.last_t = time.perf_counter()

    def __del__(self) -> None:
        if hasattr(self, "reader") and self.reader:
            self.reader.close()

    def find_package_domain(self) -> Path | None:
        domains = list(RAPL_BASE.glob("*rapl*"))
        domains.sort(key=lambda p: (1 if "mmio" in p.name else 0, p.name))
        for d in domains:
            name_file = d / "name"
            if name_file.exists() and name_file.read_text().strip() == "package-0":
                if (d / "constraint_0_power_limit_uw").exists():
                    return d.resolve()
        return None

    def _ensure_state_exists(self) -> None:
        domain_str = str(self.domain)
        def heal_state(data):
            healed = False
            if data.get("domain") != domain_str:
                data["domain"] = domain_str
                data["boot"] = self._capture_power_limits()
                healed = True
            
            boot = data.setdefault("boot", {})
            current = self._capture_power_limits()
            for k, v in current.items():
                if k not in boot:
                    boot[k] = v
                    healed = True
            if healed:
                return data
            return None
        self._atomic_state_update(heal_state)

    def _atomic_state_update(self, callback) -> None:
        STATE_FILE.touch(mode=0o644, exist_ok=True)
        with open(STATE_FILE, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                write_needed = False
                try:
                    f.seek(0)
                    data = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    data = {"domain": str(self.domain), "boot": self._capture_power_limits(), "modified": False}
                    write_needed = True
                
                updated_data = callback(data)
                if updated_data is not None:
                    data = updated_data
                    write_needed = True
                
                if write_needed:
                    f.seek(0)
                    f.truncate()
                    json.dump(data, f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _capture_power_limits(self) -> dict[str, int]:
        result = {}
        for c in ["constraint_0_power_limit_uw", "constraint_1_power_limit_uw", "constraint_2_power_limit_uw",
                  "constraint_0_time_window_us", "constraint_1_time_window_us"]:
            val = safe_read_int(self.domain / c)
            if val is not None:
                result[c] = val
        return result

    @property
    def target_path(self) -> str:
        return str(self.domain) if self.domain else "/sys/class/powercap"

    def load_state(self) -> dict[str, Any]:
        state = {}
        if not self.domain:
            return state

        pl1 = safe_read_int(self.domain / "constraint_0_power_limit_uw")
        pl2 = safe_read_int(self.domain / "constraint_1_power_limit_uw")
        pl4 = safe_read_int(self.domain / "constraint_2_power_limit_uw")
        pl1_time = safe_read_int(self.domain / "constraint_0_time_window_us")
        pl2_time = safe_read_int(self.domain / "constraint_1_time_window_us")

        values = {}
        if pl1 is not None:
            values["pl1"] = pl1 // 1_000_000
        if pl2 is not None:
            values["pl2"] = pl2 // 1_000_000
        if pl4 is not None:
            values["pl4"] = pl4 // 1_000_000
        if pl1_time is not None:
            values["pl1_time"] = round(pl1_time / 1_000_000, 2)
        if pl2_time is not None:
            values["pl2_time"] = round(pl2_time / 1_000_000, 4)

        for k, v in values.items():
            state[k] = v
            state[f"DEFAULT/{k}"] = v

        return state

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        if not self.domain:
            return False, "No active RAPL domain found", ""

        mapping = {
            "pl1": "constraint_0_power_limit_uw",
            "pl2": "constraint_1_power_limit_uw",
            "pl4": "constraint_2_power_limit_uw",
            "pl1_time": "constraint_0_time_window_us",
            "pl2_time": "constraint_1_time_window_us",
        }

        sysfs_file = mapping.get(target_key)
        if not sysfs_file:
            return False, f"Unknown key: {target_key}", ""

        try:
            val_float = float(new_value)
        except ValueError:
            return False, f"Invalid value: {new_value}", ""

        if target_key in ("pl1", "pl2", "pl4"):
            val = int(val_float * 1_000_000)
        else:
            val = int(val_float * 1_000_000)

        # Write to system
        if not safe_write(self.domain / sysfs_file, val):
            return False, "Failed to write parameter (unsupported or permission denied)", ""

        # Verify write
        actual = safe_read_int(self.domain / sysfs_file)
        if actual is None:
            return False, "Write verification failed (file unreadable)", ""

        # Mark modified in shared state
        def flag_modified(data):
            data["modified"] = True
            return data
        self._atomic_state_update(flag_modified)

        if actual == val:
            self.save_persistent_state()
            return True, f"Successfully set {target_key} to {new_value}", ""
        elif target_key in ("pl1_time", "pl2_time"):
            actual_display = f"{actual / 1_000_000:.2f}s"
            self.save_persistent_state()
            return True, f"Successfully set {target_key} to {new_value} (quantized to {actual_display})", ""
        elif val != 0 and (abs(actual - val) / val) <= 0.05:
            actual_display = f"{actual // 1_000_000} W"
            self.save_persistent_state()
            return True, f"Successfully set {target_key} to {new_value} (quantized to {actual_display})", ""
        else:
            if target_key in ("pl1_time", "pl2_time"):
                actual_display = f"{actual / 1_000_000:.2f}s"
            else:
                actual_display = f"{actual // 1_000_000} W"
            return False, f"Rejected by hardware! Locked at: {actual_display}", ""

    def save_persistent_state(self):
        try:
            home = get_user_home()
            config_dir = home / ".config" / "dusky" / "settings"
            config_dir.mkdir(parents=True, exist_ok=True)
            state_file = config_dir / "dusky_pkg_power"
            
            # Read current active sysfs limits to save
            pl1 = safe_read_int(self.domain / "constraint_0_power_limit_uw")
            pl2 = safe_read_int(self.domain / "constraint_1_power_limit_uw")
            pl4 = safe_read_int(self.domain / "constraint_2_power_limit_uw")
            pl1_time = safe_read_int(self.domain / "constraint_0_time_window_us")
            pl2_time = safe_read_int(self.domain / "constraint_1_time_window_us")
            
            limits = {}
            if pl1 is not None: limits["pl1"] = pl1 // 1_000_000
            if pl2 is not None: limits["pl2"] = pl2 // 1_000_000
            if pl4 is not None: limits["pl4"] = pl4 // 1_000_000
            if pl1_time is not None: limits["pl1_time"] = round(pl1_time / 1_000_000, 2)
            if pl2_time is not None: limits["pl2_time"] = round(pl2_time / 1_000_000, 4)
            
            state_file.write_text(json.dumps(limits, indent=2))
        except Exception:
            pass

    def restore_state(self) -> bool:
        if not self.domain:
            return False
        try:
            home = get_user_home()
            state_file = home / ".config" / "dusky" / "settings" / "dusky_pkg_power"
            if not state_file.exists():
                return False
            limits = json.loads(state_file.read_text())
            
            mapping = {
                "pl1": "constraint_0_power_limit_uw",
                "pl2": "constraint_1_power_limit_uw",
                "pl4": "constraint_2_power_limit_uw",
                "pl1_time": "constraint_0_time_window_us",
                "pl2_time": "constraint_1_time_window_us",
            }
            
            for k, v in limits.items():
                sysfs_file = mapping.get(k)
                if sysfs_file:
                    if k in ("pl1", "pl2", "pl4"):
                        val = int(v * 1_000_000)
                    else:
                        val = int(v * 1_000_000)
                    safe_write(self.domain / sysfs_file, val)
            return True
        except Exception:
            return False

    def get_telemetry(self) -> str:
        if not self.reader:
            return "Package Power Telemetry: N/A"

        import time
        curr_e = self.reader.read()
        curr_t = time.perf_counter()

        pkg_watts = 0.0
        if curr_e is not None and self.last_e is not None:
            delta_e = curr_e - self.last_e
            delta_t = curr_t - self.last_t
            if delta_t > 0:
                if delta_e < 0 and self.max_energy > 0:
                    delta_e += self.max_energy
                pkg_watts = (delta_e / 1_000_000) / delta_t

        self.last_e = curr_e
        self.last_t = curr_t

        # Build telemetry bar
        bar_w = 20
        pl2_raw = safe_read_int(self.domain / "constraint_1_power_limit_uw")
        pl2_w = pl2_raw // 1_000_000 if pl2_raw else 150
        dynamic_max = max(pl2_w, 100)

        filled = max(0, min(bar_w, int((pkg_watts / dynamic_max) * bar_w)))
        bar_graph = "█" * filled + "░" * (bar_w - filled)

        return f"⚡ Package: {pkg_watts:5.1f} W  [{bar_graph}]  Limit: {dynamic_max} W"
