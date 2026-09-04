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

def get_real_user() -> tuple[str, int, int, Path]:
    """Dynamically resolves real (non-root) user, UID, GID, and home directory."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        try:
            import pwd
            pw = pwd.getpwnam(sudo_user)
            return pw.pw_name, pw.pw_uid, pw.pw_gid, Path(pw.pw_dir)
        except (KeyError, ImportError):
            pass

    uid = os.getuid()
    gid = os.getgid()
    if uid != 0:
        try:
            import pwd
            pw = pwd.getpwuid(uid)
            return pw.pw_name, pw.pw_uid, pw.pw_gid, Path(pw.pw_dir)
        except (KeyError, ImportError):
            pass

    home_env = os.environ.get("HOME")
    if home_env and home_env != "/root" and Path(home_env).is_dir():
        home_path = Path(home_env)
        try:
            import pwd
            st = home_path.stat()
            pw = pwd.getpwuid(st.st_uid)
            return pw.pw_name, pw.pw_uid, pw.pw_gid, home_path
        except Exception:
            pass

    home_dir = Path("/home")
    if home_dir.exists():
        candidates = [p for p in home_dir.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name not in ("lost+found", "shared")]
        if len(candidates) == 1:
            u_name = candidates[0].name
            try:
                import pwd
                pw = pwd.getpwnam(u_name)
                return pw.pw_name, pw.pw_uid, pw.pw_gid, candidates[0]
            except (KeyError, ImportError):
                return u_name, 1000, 1000, candidates[0]

    return "root", 0, 0, Path("~").expanduser()

def get_cpu_model() -> str:
    """Reads processor model name from /proc/cpuinfo across x86, ARM, and RISC-V."""
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines()]
            for l in lines:
                if l.lower().startswith("model name") and ":" in l:
                    val = l.split(":", 1)[1].strip()
                    if val:
                        return val
            for l in lines:
                if (l.startswith("Model") or l.startswith("Hardware") or l.startswith("uarch")) and ":" in l:
                    val = l.split(":", 1)[1].strip()
                    if val:
                        return val
    except Exception:
        pass
    return "Generic CPU"

def get_user_home() -> Path:
    _, _, _, home = get_real_user()
    return home

def ensure_real_user_ownership(path: Path) -> None:
    """Ensures created state, cache files, and parent directories in user home are owned by the real user."""
    if os.geteuid() != 0:
        return
    _, uid, gid, home = get_real_user()
    if uid == 0:
        return
    curr = path
    while curr != home and curr != curr.parent:
        try:
            if curr.stat().st_uid == 0:
                os.chown(curr, uid, gid)
        except Exception:
            pass
        curr = curr.parent

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
        self.all_package_domains = self.find_all_package_domains()
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
        if not RAPL_BASE.exists():
            return None
        domains = list(RAPL_BASE.glob("*rapl*"))
        domains.sort(key=lambda p: (1 if "mmio" in p.name else 0, p.name))
        
        # Priority 1: Primary package zone (package-0, package, or pkg-0)
        for d in domains:
            name_file = d / "name"
            if name_file.exists():
                try:
                    name = name_file.read_text().strip().lower()
                    if name in ("package-0", "package", "pkg-0") and (d / "constraint_0_power_limit_uw").exists():
                        return d.resolve()
                except OSError:
                    continue

        # Priority 2: Any package zone (e.g. package-1 on multi-socket systems)
        for d in domains:
            name_file = d / "name"
            if name_file.exists():
                try:
                    name = name_file.read_text().strip().lower()
                    if name.startswith("package") and (d / "constraint_0_power_limit_uw").exists():
                        return d.resolve()
                except OSError:
                    continue

        # Priority 3: Any RAPL zone with constraint_0 that is not a subzone
        for d in domains:
            name_file = d / "name"
            if name_file.exists():
                try:
                    name = name_file.read_text().strip().lower()
                    if name not in ("core", "uncore", "dram", "psys") and (d / "constraint_0_power_limit_uw").exists():
                        return d.resolve()
                except OSError:
                    continue
        return None

    def find_all_package_domains(self) -> list[Path]:
        if not RAPL_BASE.exists():
            return []
        domains = list(RAPL_BASE.glob("*rapl*"))
        domains.sort(key=lambda p: (1 if "mmio" in p.name else 0, p.name))
        
        pkg_domains: list[Path] = []
        for d in domains:
            name_file = d / "name"
            if name_file.exists():
                try:
                    name = name_file.read_text().strip().lower()
                    if (name in ("package-0", "package", "pkg-0") or name.startswith("package")) and (d / "constraint_0_power_limit_uw").exists():
                        resolved = d.resolve()
                        if resolved not in pkg_domains:
                            pkg_domains.append(resolved)
                except OSError:
                    continue
        if not pkg_domains:
            primary = self.find_package_domain()
            if primary:
                pkg_domains.append(primary)
        return pkg_domains

    def _get_persistent_baseline(self) -> dict[str, int]:
        try:
            _, _, _, home = get_real_user()
            b_file = home / ".config" / "dusky" / "settings" / "dusky_pkg_bios_baseline.json"
            if b_file.exists():
                data = json.loads(b_file.read_text())
                cached_model = data.get("_cpu_model")
                curr_model = get_cpu_model()
                # Invalidate cache if machine hardware/CPU model changed
                if cached_model and curr_model != "Generic CPU" and cached_model != curr_model:
                    return {}
                return {k: int(v) for k, v in data.items() if not k.startswith("_")}
        except Exception:
            pass
        return {}

    def _save_persistent_baseline(self, limits: dict[str, int]) -> None:
        try:
            _, uid, gid, home = get_real_user()
            cfg_dir = home / ".config" / "dusky" / "settings"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            ensure_real_user_ownership(cfg_dir)
            b_file = cfg_dir / "dusky_pkg_bios_baseline.json"
            
            should_save = False
            if not b_file.exists():
                should_save = True
            else:
                try:
                    data = json.loads(b_file.read_text())
                    cached_model = data.get("_cpu_model")
                    curr_model = get_cpu_model()
                    if cached_model and curr_model != "Generic CPU" and cached_model != curr_model:
                        should_save = True
                except Exception:
                    should_save = True

            if should_save and limits:
                payload = {k: int(v) for k, v in limits.items() if not k.startswith("_")}
                payload["_cpu_model"] = get_cpu_model()
                b_file.write_text(json.dumps(payload, indent=2) + "\n")
                ensure_real_user_ownership(b_file)
        except Exception:
            pass

    def _get_initial_state_data(self) -> dict[str, Any]:
        baseline = self._get_persistent_baseline()
        if not baseline:
            baseline = self._capture_power_limits()
            self._save_persistent_baseline(baseline)
        return {
            "domain": str(self.domain) if self.domain else "",
            "boot": baseline,
            "modified": False
        }

    def _ensure_state_exists(self) -> None:
        domain_str = str(self.domain)
        def heal_state(data):
            healed = False
            baseline = self._get_persistent_baseline()
            if not baseline:
                baseline = self._capture_power_limits()
                self._save_persistent_baseline(baseline)

            if data.get("domain") != domain_str:
                data["domain"] = domain_str
                data["boot"] = baseline
                healed = True
            
            boot = data.setdefault("boot", {})
            for k, v in baseline.items():
                if k not in boot:
                    boot[k] = v
                    healed = True
            if healed:
                return data
            return None
        self._atomic_state_update(heal_state)

    def _atomic_state_update(self, callback) -> None:
        try:
            if not STATE_FILE.exists():
                try:
                    STATE_FILE.touch(mode=0o666, exist_ok=True)
                    try:
                        os.chmod(STATE_FILE, 0o666)
                    except OSError:
                        pass
                except OSError:
                    return

            if not os.access(STATE_FILE, os.W_OK):
                return

            with open(STATE_FILE, "r+") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    write_needed = False
                    try:
                        f.seek(0)
                        raw = f.read().strip()
                        if raw:
                            data = json.loads(raw)
                        else:
                            data = self._get_initial_state_data()
                            write_needed = True
                    except (json.JSONDecodeError, ValueError):
                        data = self._get_initial_state_data()
                        write_needed = True
                    
                    updated_data = callback(data)
                    if updated_data is not None:
                        data = updated_data
                        write_needed = True
                    
                    if write_needed:
                        f.seek(0)
                        f.truncate()
                        f.write(json.dumps(data) + "\n")
                        f.flush()
                        try:
                            os.fsync(f.fileno())
                        except OSError:
                            pass
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except OSError:
            pass

    def _capture_power_limits(self) -> dict[str, int]:
        result = {}
        if not self.domain:
            return result
        for c in ["constraint_0_power_limit_uw", "constraint_1_power_limit_uw", "constraint_2_power_limit_uw",
                  "constraint_0_time_window_us", "constraint_1_time_window_us"]:
            val = safe_read_int(self.domain / c)
            if val is not None:
                result[c] = val
        return result

    def get_boot_limits(self) -> dict[str, int]:
        baseline = self._get_persistent_baseline()
        if baseline:
            return baseline
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE) as f:
                    data = json.load(f)
                    b = data.get("boot", {})
                    if b:
                        return b
            except Exception:
                pass
        current = self._capture_power_limits()
        if current:
            self._save_persistent_baseline(current)
        return current

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

        target_path = self.domain / sysfs_file
        if not target_path.exists():
            return False, f"Hardware does not support parameter '{target_key}' ({sysfs_file} not present in sysfs)", ""

        try:
            val_float = float(new_value)
        except ValueError:
            return False, f"Invalid value: {new_value}", ""

        if val_float < 0:
            return False, f"Invalid negative value: {new_value}", ""

        val = int(round(val_float * 1_000_000))

        # Write to system
        if not safe_write(target_path, val):
            return False, f"Failed to write to {sysfs_file} (check root permissions or hardware lock)", ""

        for other_dom in self.all_package_domains:
            if other_dom != self.domain:
                other_path = other_dom / sysfs_file
                if other_path.exists():
                    safe_write(other_path, val)

        # Verify write
        actual = safe_read_int(target_path)
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
            actual_display = f"{actual / 1_000_000:.4f}s"
            if val > 0 and 0.2 <= (actual / val) <= 5.0:
                self.save_persistent_state()
                return True, f"Successfully set {target_key} to {new_value} (quantized to {actual_display})", ""
            else:
                return False, f"Rejected by hardware! Locked at: {actual_display}", ""
        elif val != 0 and (abs(actual - val) / val) <= 0.05:
            actual_display = f"{actual // 1_000_000} W"
            self.save_persistent_state()
            return True, f"Successfully set {target_key} to {new_value} (quantized to {actual_display})", ""
        else:
            actual_display = f"{actual // 1_000_000} W"
            return False, f"Rejected by hardware! Locked at: {actual_display}", ""

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not self.domain:
            return False, "No active RAPL domain found", ""

        mapping = {
            "pl1": "constraint_0_power_limit_uw",
            "pl2": "constraint_1_power_limit_uw",
            "pl4": "constraint_2_power_limit_uw",
            "pl1_time": "constraint_0_time_window_us",
            "pl2_time": "constraint_1_time_window_us",
        }

        success_count = 0
        for key, scope, val_str, itype in changes:
            sysfs_file = mapping.get(key)
            if not sysfs_file:
                continue
            target_path = self.domain / sysfs_file
            if not target_path.exists():
                continue
            try:
                val_float = float(val_str)
                val = int(round(val_float * 1_000_000))
                if safe_write(target_path, val):
                    success_count += 1
                    for other_dom in self.all_package_domains:
                        if other_dom != self.domain:
                            other_path = other_dom / sysfs_file
                            if other_path.exists():
                                safe_write(other_path, val)
            except (ValueError, TypeError):
                continue

        if success_count > 0:
            def flag_modified(data):
                data["modified"] = True
                return data
            self._atomic_state_update(flag_modified)
            self.save_persistent_state()
            return True, f"Successfully updated {success_count} power parameters in batch.", ""
        return False, "Failed to apply power parameters in batch.", ""

    def save_persistent_state(self) -> None:
        if not self.domain:
            return
        try:
            _, uid, gid, home = get_real_user()
            config_dir = home / ".config" / "dusky" / "settings"
            config_dir.mkdir(parents=True, exist_ok=True)
            ensure_real_user_ownership(config_dir)
            state_file = config_dir / "dusky_pkg_power"
            
            # Read current active sysfs limits to save
            pl1 = safe_read_int(self.domain / "constraint_0_power_limit_uw")
            pl2 = safe_read_int(self.domain / "constraint_1_power_limit_uw")
            pl4 = safe_read_int(self.domain / "constraint_2_power_limit_uw")
            pl1_time = safe_read_int(self.domain / "constraint_0_time_window_us")
            pl2_time = safe_read_int(self.domain / "constraint_1_time_window_us")
            
            limits: dict[str, Any] = {"_cpu_model": get_cpu_model()}
            if pl1 is not None: limits["pl1"] = pl1 // 1_000_000
            if pl2 is not None: limits["pl2"] = pl2 // 1_000_000
            if pl4 is not None: limits["pl4"] = pl4 // 1_000_000
            if pl1_time is not None: limits["pl1_time"] = round(pl1_time / 1_000_000, 2)
            if pl2_time is not None: limits["pl2_time"] = round(pl2_time / 1_000_000, 4)
            
            state_file.write_text(json.dumps(limits, indent=2) + "\n")
            ensure_real_user_ownership(state_file)
        except Exception:
            pass

    def restore_state(self) -> bool:
        if not self.domain:
            return False
        try:
            _, _, _, home = get_real_user()
            state_file = home / ".config" / "dusky" / "settings" / "dusky_pkg_power"
            if not state_file.exists():
                return False
            limits = json.loads(state_file.read_text())

            cached_model = limits.get("_cpu_model")
            curr_model = get_cpu_model()
            if cached_model and curr_model != "Generic CPU" and cached_model != curr_model:
                return False
            
            mapping = {
                "pl1": "constraint_0_power_limit_uw",
                "pl2": "constraint_1_power_limit_uw",
                "pl4": "constraint_2_power_limit_uw",
                "pl1_time": "constraint_0_time_window_us",
                "pl2_time": "constraint_1_time_window_us",
            }
            
            restored = 0
            for k, v in limits.items():
                if k.startswith("_"):
                    continue
                sysfs_file = mapping.get(k)
                if sysfs_file:
                    target_path = self.domain / sysfs_file
                    if target_path.exists():
                        val = int(round(float(v) * 1_000_000))
                        if safe_write(target_path, val):
                            restored += 1
                            for other_dom in self.all_package_domains:
                                if other_dom != self.domain:
                                    other_path = other_dom / sysfs_file
                                    if other_path.exists():
                                        safe_write(other_path, val)
            
            if restored > 0:
                def flag_modified(data):
                    data["modified"] = True
                    return data
                self._atomic_state_update(flag_modified)
                return True
            return False
        except Exception:
            return False

    def restore_defaults(self) -> tuple[bool, str]:
        """Restores power limits and time windows to original boot/BIOS defaults."""
        if not self.domain:
            return False, "No active RAPL domain found"
        boot_limits = self.get_boot_limits()
        if not boot_limits:
            return False, "No BIOS boot baseline found in state cache"

        restored = 0
        for sysfs_file, val in boot_limits.items():
            if sysfs_file.startswith("_"):
                continue
            target_path = self.domain / sysfs_file
            if target_path.exists():
                if safe_write(target_path, val):
                    restored += 1
                    for other_dom in self.all_package_domains:
                        if other_dom != self.domain:
                            other_path = other_dom / sysfs_file
                            if other_path.exists():
                                safe_write(other_path, val)

        def reset_mod(data):
            data["modified"] = False
            return data
        self._atomic_state_update(reset_mod)
        self.save_persistent_state()
        return True, f"Successfully restored {restored} power parameters to BIOS defaults."

    def get_telemetry(self) -> str:
        if not self.domain:
            return " Package Power Telemetry: N/A (No RAPL domain)"

        if not self.reader or self.reader.fd is None:
            if self.energy_file and self.energy_file.exists():
                self.reader = FastEnergyReader(self.energy_file)
                self.last_e = self.reader.read()
                self.last_t = time.perf_counter()

        if not self.reader or self.reader.fd is None:
            return " Package: N/A (Root required for live RAPL energy telemetry)"

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
        pl1_raw = safe_read_int(self.domain / "constraint_0_power_limit_uw")
        pl2_raw = safe_read_int(self.domain / "constraint_1_power_limit_uw")
        pl1_w = pl1_raw // 1_000_000 if pl1_raw else 0
        pl2_w = pl2_raw // 1_000_000 if pl2_raw else 0
        dynamic_max = pl1_w or pl2_w or 100
        dynamic_max = max(dynamic_max, 1)

        filled = max(0, min(bar_w, int((pkg_watts / dynamic_max) * bar_w)))
        bar_graph = "█" * filled + "░" * (bar_w - filled)

        return f" Package: {pkg_watts:5.1f} W  [{bar_graph}]  Limit: {dynamic_max} W"

    def get_power_limits(self) -> dict[str, Any]:
        """Returns structured dictionary of active limits, boot defaults, and status."""
        if not self.domain:
            return {}

        boot = self.get_boot_limits()
        _, _, _, home = get_real_user()
        state_file = home / ".config" / "dusky" / "settings" / "dusky_pkg_power"
        persisted = {}
        if state_file.exists():
            try:
                raw_persisted = json.loads(state_file.read_text())
                persisted = {k: v for k, v in raw_persisted.items() if not k.startswith("_")}
            except Exception:
                pass

        is_modified = False
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE) as f:
                    is_modified = json.load(f).get("modified", False)
            except Exception:
                pass

        return {
            "domain": str(self.domain),
            "domain_name": (self.domain / "name").read_text().strip() if (self.domain / "name").exists() else "package-0",
            "modified": is_modified,
            "persistent_file": str(state_file),
            "persistent_data": persisted,
            "limits": {
                "pl1": {
                    "label": "PL1 (Long-Term Limit)",
                    "supported": (self.domain / "constraint_0_power_limit_uw").exists(),
                    "current": (safe_read_int(self.domain / "constraint_0_power_limit_uw") or 0) // 1_000_000,
                    "boot": boot.get("constraint_0_power_limit_uw", 0) // 1_000_000,
                    "unit": "W"
                },
                "pl2": {
                    "label": "PL2 (Short-Term Boost)",
                    "supported": (self.domain / "constraint_1_power_limit_uw").exists(),
                    "current": (safe_read_int(self.domain / "constraint_1_power_limit_uw") or 0) // 1_000_000,
                    "boot": boot.get("constraint_1_power_limit_uw", 0) // 1_000_000,
                    "unit": "W"
                },
                "pl4": {
                    "label": "PL4 (Peak Clamp)",
                    "supported": (self.domain / "constraint_2_power_limit_uw").exists(),
                    "current": (safe_read_int(self.domain / "constraint_2_power_limit_uw") or 0) // 1_000_000,
                    "boot": boot.get("constraint_2_power_limit_uw", 0) // 1_000_000,
                    "unit": "W"
                }
            },
            "time_windows": {
                "pl1_time": {
                    "label": "PL1 Time Window (Tau)",
                    "supported": (self.domain / "constraint_0_time_window_us").exists() and safe_read_int(self.domain / "constraint_0_time_window_us") is not None,
                    "current": round((safe_read_int(self.domain / "constraint_0_time_window_us") or 0) / 1_000_000, 4),
                    "boot": round(boot.get("constraint_0_time_window_us", 0) / 1_000_000, 4),
                    "unit": "s"
                },
                "pl2_time": {
                    "label": "PL2 Time Window",
                    "supported": (self.domain / "constraint_1_time_window_us").exists() and safe_read_int(self.domain / "constraint_1_time_window_us") is not None,
                    "current": round((safe_read_int(self.domain / "constraint_1_time_window_us") or 0) / 1_000_000, 4),
                    "boot": round(boot.get("constraint_1_time_window_us", 0) / 1_000_000, 4),
                    "unit": "s"
                }
            }
        }
