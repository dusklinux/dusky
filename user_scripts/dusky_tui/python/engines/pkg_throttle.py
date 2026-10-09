#!/usr/bin/env python3
import os
lazy import pwd
lazy import json
lazy import fcntl
lazy import math
lazy import sys
lazy from python.engines.cpu_core import atomic_write
import time
from pathlib import Path
lazy from typing import Any
from python.frontend.core_types import BaseEngine

RAPL_BASE = Path("/sys/class/powercap")
FIRMWARE_ATTRIBUTES = Path("/sys/class/firmware-attributes")
STATE_FILE = Path("/dev/shm/dusky_rapl_state.json")
CONTROL_KEYS = ("pl1", "pl2", "pl4", "pl1_time", "pl2_time")
CONSTRAINT_KEYS = frozendict(long_term="pl1", short_term="pl2", peak_power="pl4")


class PlatformHardwareExtension:
    """
    Lightweight, vendor-neutral hardware extension manager.
    Coordinates laptop EC / firmware-level power limits that exist outside standard RAPL.
    Designed for zero-overhead fallback: on standard/unsupported platforms (Dell, Lenovo,
    Framework, desktops), all operations gracefully no-op.
    """
    def __init__(self) -> None:
        self.pl1_node, self.pl2_node, self.vendor_name = self._discover_ppt_nodes()

    @staticmethod
    def _discover_ppt_nodes() -> tuple[Path | None, Path | None, str]:
        # Use the firmware-attributes ABI; deprecated WMI nodes report cached values.
        for base in sorted(FIRMWARE_ATTRIBUTES.glob("asus-armoury*/attributes")):
            pl1 = base / "ppt_pl1_spl" / "current_value"
            pl2 = base / "ppt_pl2_sppt" / "current_value"
            if pl1.is_file() or pl2.is_file():
                return (pl1 if pl1.is_file() else None, pl2 if pl2.is_file() else None, "ASUS Armoury PPT")
        return (None, None, "None")

    @property
    def supported(self) -> bool:
        return self.pl1_node is not None or self.pl2_node is not None

    def apply(self, pl1_watts: int | None = None, pl2_watts: int | None = None) -> tuple[bool, str]:
        failures = []
        adjusted = []
        for node, watts in ((self.pl1_node, pl1_watts), (self.pl2_node, pl2_watts)):
            if node is None or watts is None:
                continue
            minimum = safe_read_int(node.parent / "min_value")
            maximum = safe_read_int(node.parent / "max_value")
            target = max(watts, minimum) if minimum is not None else watts
            target = min(target, maximum) if maximum is not None else target
            if target != watts:
                adjusted.append(f"{node.parent.name}: firmware range requires {target} W; RAPL keeps its requested limit")
            if not self._write_limit(node, target):
                failures.append(f"{node.parent.name}: requested {target} W, read back {safe_read_int(node)} W")
        return not failures, "; ".join(failures if failures else adjusted)

    def restore(self, baseline: dict[str, Any], fallback_values: dict[str, int]) -> tuple[bool, str]:
        values = []
        for key in ("pl1", "pl2"):
            val = baseline.get(f"_platform_{key}", baseline.get(f"_asus_{key}"))
            if val is None and key in fallback_values:
                val = round(fallback_values[key] / 1_000_000)
            if val is not None and (type(val) is not int or val < 0):
                return False, f"Invalid platform baseline for {key}"
            values.append(val)
        return self.apply(*values)

    def get_status(self) -> dict[str, Any]:
        if not self.supported:
            return {"supported": False}
        return {
            "supported": True,
            "vendor": self.vendor_name,
            "pl1": safe_read_int(self.pl1_node) if self.pl1_node else None,
            "pl2": safe_read_int(self.pl2_node) if self.pl2_node else None,
        }

    def capture_baseline(self) -> dict[str, int]:
        res: dict[str, int] = {}
        if self.pl1_node:
            v1 = safe_read_int(self.pl1_node)
            if v1 is not None:
                res["_platform_pl1"] = v1
                res["_asus_pl1"] = v1
        if self.pl2_node:
            v2 = safe_read_int(self.pl2_node)
            if v2 is not None:
                res["_platform_pl2"] = v2
                res["_asus_pl2"] = v2
        return res

    @staticmethod
    def _write_limit(path: Path, watts: int) -> bool:
        if safe_read_int(path) == watts:
            return True
        try:
            path.write_text(f"{watts}\n", encoding="ascii")
            return safe_read_int(path) == watts
        except OSError:
            return False


def get_real_user() -> tuple[str, int, int, Path]:
    """Dynamically resolves real (non-root) user, UID, GID, and home directory."""
    pkexec_uid = os.environ.get("PKEXEC_UID")
    if pkexec_uid:
        try:
            pw = pwd.getpwuid(int(pkexec_uid))
            return pw.pw_name, pw.pw_uid, pw.pw_gid, Path(pw.pw_dir)
        except (KeyError, ValueError):
            pass
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        try:
            pw = pwd.getpwnam(sudo_user)
            return pw.pw_name, pw.pw_uid, pw.pw_gid, Path(pw.pw_dir)
        except (KeyError, ImportError):
            pass

    uid = os.getuid()
    gid = os.getgid()
    if uid != 0:
        try:
            pw = pwd.getpwuid(uid)
            return pw.pw_name, pw.pw_uid, pw.pw_gid, Path(pw.pw_dir)
        except (KeyError, ImportError):
            pass

    home_env = os.environ.get("HOME")
    if home_env and home_env != "/root" and Path(home_env).is_dir():
        home_path = Path(home_env)
        try:
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
                pw = pwd.getpwnam(u_name)
                return pw.pw_name, pw.pw_uid, pw.pw_gid, candidates[0]
            except (KeyError, ImportError):
                pass

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
    if uid == 0 or not path.is_relative_to(home):
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
        return int(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None

def safe_write(p: Path, val: int) -> bool:
    try:
        p.write_text(str(val), encoding="ascii")
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
            return int(os.pread(self.fd, 32, 0))
        except (OSError, ValueError):
            self.close()
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
        self.platform = PlatformHardwareExtension()
        self._constraint_cache: dict[Path, dict[str, str]] = {}
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

    def shutdown(self) -> None:
        """Close telemetry resources after the frontend drains pending reads."""
        if self.reader is not None:
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
                    name = name_file.read_text(encoding="utf-8").strip().lower()
                    if name in ("package-0", "package", "pkg-0") and (d / self.constraint_file("pl1", d)).exists():
                        return d.resolve()
                except OSError:
                    continue

        # Priority 2: Any package zone (e.g. package-1 on multi-socket systems)
        for d in domains:
            name_file = d / "name"
            if name_file.exists():
                try:
                    name = name_file.read_text(encoding="utf-8").strip().lower()
                    if name.startswith("package") and (d / self.constraint_file("pl1", d)).exists():
                        return d.resolve()
                except OSError:
                    continue

        # Priority 3: Any RAPL zone with constraint_0 that is not a subzone
        for d in domains:
            name_file = d / "name"
            if name_file.exists():
                try:
                    name = name_file.read_text(encoding="utf-8").strip().lower()
                    if name not in ("core", "uncore", "dram", "psys") and (d / self.constraint_file("pl1", d)).exists():
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
                    name = name_file.read_text(encoding="utf-8").strip().lower()
                    if (name in ("package-0", "package", "pkg-0") or name.startswith("package")) and (d / self.constraint_file("pl1", d)).exists():
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

    def constraint_file(self, key: str, domain: Path | None = None) -> str:
        domain = domain or self.domain
        if domain is None:
            return f"unsupported_{key}"
        if domain not in self._constraint_cache:
            mapping = {}
            for path in sorted(domain.glob("constraint_*_name")):
                try:
                    base_key = CONSTRAINT_KEYS.get(path.read_text(encoding="ascii").strip())
                    if base_key:
                        prefix = path.name.removesuffix("name")
                        mapping[base_key] = prefix + "power_limit_uw"
                        mapping[base_key + "_time"] = prefix + "time_window_us"
                except OSError:
                    continue
            self._constraint_cache[domain] = mapping
        return self._constraint_cache[domain].get(key, f"unsupported_{key}")

    def _get_persistent_baseline(self) -> dict[str, int]:
        try:
            _, _, _, home = get_real_user()
            b_file = home / ".config" / "dusky" / "settings" / "dusky_pkg_bios_baseline.json"
            if b_file.exists():
                data = json.loads(b_file.read_text(encoding="utf-8"))
                cached_model = data.get("_cpu_model")
                curr_model = get_cpu_model()
                # Invalidate cache if machine hardware/CPU model changed
                if cached_model and curr_model != "Generic CPU" and cached_model != curr_model:
                    return {}
                limits = {k: v for k, v in data.items() if not k.startswith("_")}
                if any(type(v) is not int or not 0 <= v < 2**64 for v in limits.values()):
                    return {}
                return limits
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
                    data = json.loads(b_file.read_text(encoding="utf-8"))
                    cached_model = data.get("_cpu_model")
                    curr_model = get_cpu_model()
                    if cached_model and curr_model != "Generic CPU" and cached_model != curr_model:
                        should_save = True
                except Exception:
                    should_save = True

            if should_save and limits:
                payload = {k: int(v) for k, v in limits.items() if not k.startswith("_")}
                payload["_cpu_model"] = get_cpu_model()
                payload["_packages"] = self._capture_packages(raw=True)
                if self.platform.supported:
                    payload.update(self.platform.capture_baseline())
                atomic_write(b_file, json.dumps(payload, indent=2) + "\n", user_owned=True)
                ensure_real_user_ownership(b_file)
        except Exception:
            pass

    def _capture_packages(self, raw: bool = False) -> dict[str, dict[str, int | float]]:
        packages = {}
        for domain in self.all_package_domains:
            limits = {}
            for key in CONTROL_KEYS:
                value = safe_read_int(domain / self.constraint_file(key, domain))
                if value is not None:
                    limits[key] = value if raw else value / 1_000_000
            packages[domain.name] = limits
        return packages

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

            if data.get("domain") != domain_str or data.get("boot") != baseline:
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

            with open(STATE_FILE, "r+", encoding="utf-8") as f:
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
                    if not isinstance(data, dict) or not isinstance(data.get("boot", {}), dict):
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
        for c in [self.constraint_file("pl1"), self.constraint_file("pl2"), self.constraint_file("pl4"),
                  self.constraint_file("pl1_time"), self.constraint_file("pl2_time")]:
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
                with open(STATE_FILE, encoding="utf-8") as f:
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

        pl1 = safe_read_int(self.domain / self.constraint_file("pl1"))
        pl2 = safe_read_int(self.domain / self.constraint_file("pl2"))
        pl4 = safe_read_int(self.domain / self.constraint_file("pl4"))
        pl1_time = safe_read_int(self.domain / self.constraint_file("pl1_time"))
        pl2_time = safe_read_int(self.domain / self.constraint_file("pl2_time"))

        values = {}
        if pl1 is not None:
            values["pl1"] = pl1 / 1_000_000
        if pl2 is not None:
            values["pl2"] = pl2 / 1_000_000
        if pl4 is not None:
            values["pl4"] = pl4 / 1_000_000
        if pl1_time is not None:
            values["pl1_time"] = pl1_time / 1_000_000
        if pl2_time is not None:
            values["pl2_time"] = pl2_time / 1_000_000

        for k, v in values.items():
            state[k] = v
            state[f"DEFAULT/{k}"] = v

        return state

    def _apply_values(
        self, values: dict[str, int], packages: dict[str, dict[str, int]] | None = None,
        platform_baseline: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        if not self.domain:
            return False, "No active RAPL domain found"
        if packages is None:
            unsupported = [key for key in values if not (self.domain / self.constraint_file(key)).exists()]
            if unsupported:
                return False, f"Primary package does not support: {', '.join(unsupported)}"
        elif set(packages) != {d.name for d in self.all_package_domains}:
            return False, "Saved settings do not match the discovered package domains"

        targets = []
        skipped = []
        for domain in self.all_package_domains:
            for key, requested in (packages[domain.name] if packages is not None else values).items():
                path = domain / self.constraint_file(key, domain)
                if not path.exists():
                    if packages is not None or domain == self.domain:
                        return False, f"{domain.name}/{key}: unsupported"
                    skipped.append(f"{domain.name}/{key}: unsupported (skipped)")
                    continue
                if safe_read_int(path) is None:
                    return False, f"{domain.name}/{key}: cannot read current setting"
                targets.append((domain, key, path, requested))

        # Firmware writes can reprogram RAPL, so apply them before the final RAPL pass.
        platform_msg = ""
        if self.platform.supported:
            if platform_baseline is not None:
                ok, msg = self.platform.restore(platform_baseline, values)
            else:
                ok, msg = self.platform.apply(
                    round(values["pl1"] / 1_000_000) if "pl1" in values else None,
                    round(values["pl2"] / 1_000_000) if "pl2" in values else None,
                )
            platform_msg = msg if ok else f"Firmware synchronization unavailable: {msg}; applying RAPL limits"

        failures = []
        quantized = []
        for domain in self.all_package_domains:
            enabled = domain / "enabled"
            if safe_read_int(enabled) == 0 and (not safe_write(enabled, 1) or safe_read_int(enabled) != 1):
                failures.append(f"{domain.name}: cannot enable power enforcement")
        for domain, key, path, requested in targets:
            if safe_read_int(path) != requested and not safe_write(path, requested):
                failures.append(f"{domain.name}/{key}: write failed")
        # Verify all readbacks after writing; one firmware operation can affect another setting.
        for domain, key, path, requested in targets:
            actual = safe_read_int(path)
            tolerance = 0.25 if key.endswith("_time") else 0.05
            if actual is None or (actual != requested and
                    (requested == 0 or abs(actual - requested) / requested > tolerance)):
                failures.append(f"{domain.name}/{key}: requested {requested}, read back {actual}")
            elif actual != requested:
                quantized.append(f"{domain.name}/{key}: quantized to {actual / 1_000_000:g}")
        for domain in self.all_package_domains:
            if safe_read_int(domain / "enabled") == 0 or safe_read_int(domain.parent / "enabled") == 0:
                failures.append(f"{domain.name}: power enforcement is disabled")
        if failures:
            return False, "Settings may be partially applied: " + "; ".join(failures)
        return True, "; ".join(([platform_msg] if platform_msg else []) + quantized + skipped)

    def _parse_values(self, changes: list[tuple[str, str, str, str]]) -> dict[str, int]:
        values = {}
        for key, _, raw, _ in changes:
            if key not in CONTROL_KEYS:
                raise ValueError(f"Unknown key: {key}")
            value = float(raw)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid value for {key}: {raw}")
            scaled = value * 1_000_000
            if not math.isfinite(scaled) or scaled >= 2**64:
                raise ValueError(f"Value exceeds powercap bounds: {raw}")
            values[key] = round(scaled)
        return values

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not self.domain:
            return False, "No active RAPL domain found", ""
        try:
            values = self._parse_values(changes)
        except (ValueError, TypeError, OverflowError) as exc:
            return False, str(exc), ""
        if not values:
            return True, "No changes", ""
        ok, msg = self._apply_values(values)
        def modified(data):
            data["modified"] = True
            return data
        self._atomic_state_update(modified)
        if not ok:
            return False, msg + "; previous saved configuration retained", ""
        try:
            self.save_persistent_state()
        except OSError as exc:
            return False, f"Power settings applied with persistence failure: {exc}; {msg}", ""
        return ok, msg or f"Applied and verified {len(values)} power settings on {len(self.all_package_domains)} domains", ""

    def save_persistent_state(self) -> None:
        if not self.domain:
            return
        limits: dict[str, Any] = {"_cpu_model": get_cpu_model()}
        for key in CONTROL_KEYS:
            value = safe_read_int(self.domain / self.constraint_file(key))
            if value is not None:
                limits[key] = value / 1_000_000
        limits["_packages"] = self._capture_packages()
        if self.platform.supported:
            limits.update(self.platform.capture_baseline())
        path = get_user_home() / ".config" / "dusky" / "settings" / "dusky_pkg_power"
        atomic_write(path, json.dumps(limits, indent=2) + "\n", user_owned=True)

    def restore_state(self) -> bool:
        path = get_user_home() / ".config" / "dusky" / "settings" / "dusky_pkg_power"
        if not path.exists():
            return True
        if not self.domain:
            return False
        try:
            limits = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(limits, dict) or limits.get("_cpu_model") != get_cpu_model():
                return False
            values = self._parse_values([(k, "DEFAULT", v, "float") for k, v in limits.items() if not k.startswith("_")])
            packages = None
            if "_packages" in limits:
                if not isinstance(limits["_packages"], dict):
                    return False
                packages = {}
                for name, entries in limits["_packages"].items():
                    if not isinstance(entries, dict):
                        return False
                    packages[name] = self._parse_values([(k, "DEFAULT", v, "float") for k, v in entries.items()])
                if set(packages) != {d.name for d in self.all_package_domains}:
                    return False
            ok, msg = self._apply_values(values, packages, platform_baseline=limits)
            if msg:
                print(f"Power restore: {msg}", file=sys.stderr)
            if ok:
                def modified(data):
                    data["modified"] = True
                    return data
                self._atomic_state_update(modified)
            return ok
        except (OSError, ValueError, TypeError, OverflowError):
            return False

    def restore_defaults(self) -> tuple[bool, str]:
        if not self.domain:
            return False, "No active RAPL domain found"
        boot = self.get_boot_limits()
        values = {key: boot[file] for key in CONTROL_KEYS
                  if (file := self.constraint_file(key)) in boot}
        if not values:
            return False, "No captured baseline available"
        packages = None
        baseline = {}
        try:
            baseline_path = get_user_home() / ".config" / "dusky" / "settings" / "dusky_pkg_bios_baseline.json"
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            if isinstance(baseline, dict) and baseline.get("_cpu_model") == get_cpu_model() and isinstance(baseline.get("_packages"), dict):
                packages = baseline["_packages"]
                for entries in packages.values():
                    if not isinstance(entries, dict) or any(k not in CONTROL_KEYS
                                                           or type(v) is not int or not 0 <= v < 2**64 for k, v in entries.items()):
                        return False, "Invalid captured package baseline"
        except (OSError, ValueError, TypeError):
            pass
        if packages is not None and set(packages) != {d.name for d in self.all_package_domains}:
            return False, "Captured baseline does not match the discovered package domains"
        if packages is None:
            try:
                names = {(d / "name").read_text(encoding="utf-8").strip() for d in self.all_package_domains}
            except OSError as exc:
                return False, f"Cannot identify package domains: {exc}"
            if len(names) > 1:
                return False, "Legacy baseline lacks per-package defaults; cannot reliably reset multiple sockets"
        ok, msg = self._apply_values(values, packages, platform_baseline=baseline if isinstance(baseline, dict) else {})
        def modified(data):
            data["modified"] = not ok
            return data
        self._atomic_state_update(modified)
        if not ok:
            return False, msg + "; previous saved configuration retained"
        try:
            self.save_persistent_state()
        except OSError as exc:
            return False, f"Baseline applied with persistence failure: {exc}"
        return ok, msg or f"Restored and verified {len(values)} captured baseline settings"

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

        if curr_e is None:
            self.last_e = None
            self.last_t = None
            return " Package: N/A (energy read failed)"
        if self.last_e is None or self.last_t is None:
            self.last_e, self.last_t = curr_e, curr_t
            return " Package: sampling energy..."
        delta_t = curr_t - self.last_t
        # Avoid deriving watts from a near-zero startup interval; accumulate a real sample.
        if delta_t < 0.1:
            return " Package: sampling energy..."
        delta_e = curr_e - self.last_e
        self.last_e, self.last_t = curr_e, curr_t
        if delta_e < 0 and self.max_energy > 0:
            delta_e += self.max_energy
        if delta_e < 0:
            return " Package: N/A (energy counter reset)"
        pkg_watts = delta_e / 1_000_000 / delta_t

        pl1_raw = safe_read_int(self.domain / self.constraint_file("pl1"))
        pl2_raw = safe_read_int(self.domain / self.constraint_file("pl2"))
        pl1_w = pl1_raw / 1_000_000 if pl1_raw is not None else None
        pl2_w = pl2_raw / 1_000_000 if pl2_raw is not None else None
        scale = max(pl1_w or pl2_w or 100, 1)
        filled = max(0, min(20, int(pkg_watts / scale * 20)))
        bar_graph = "█" * filled + "░" * (20 - filled)
        p1 = f"{pl1_w:g} W" if pl1_w is not None else "N/A"
        p2 = f"{pl2_w:g} W" if pl2_w is not None else "N/A"
        return (f" Package: {pkg_watts:5.1f} W ({delta_t:.2f}s sample) [{bar_graph}] "
                f"PL1 avg: {p1} | PL2 avg: {p2}")

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
                raw_persisted = json.loads(state_file.read_text(encoding="utf-8"))
                persisted = {k: v for k, v in raw_persisted.items() if not k.startswith("_")}
            except Exception:
                pass

        is_modified = False
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, encoding="utf-8") as f:
                    is_modified = json.load(f).get("modified", False)
            except Exception:
                pass

        return {
            "domain": str(self.domain),
            "domain_name": (self.domain / "name").read_text(encoding="utf-8").strip() if (self.domain / "name").exists() else "package-0",
            "modified": is_modified,
            "packages": self._capture_packages(),
            "enforcement": {d.name: safe_read_int(d / "enabled") for d in self.all_package_domains},
            "persistent_file": str(state_file),
            "persistent_data": persisted,
            "limits": {
                "pl1": {
                    "label": "PL1 (Long-Term Limit)",
                    "supported": (self.domain / self.constraint_file("pl1")).exists(),
                    "current": (safe_read_int(self.domain / self.constraint_file("pl1")) or 0) / 1_000_000,
                    "boot": boot.get(self.constraint_file("pl1"), 0) / 1_000_000,
                    "unit": "W"
                },
                "pl2": {
                    "label": "PL2 (Short-Term Boost)",
                    "supported": (self.domain / self.constraint_file("pl2")).exists(),
                    "current": (safe_read_int(self.domain / self.constraint_file("pl2")) or 0) / 1_000_000,
                    "boot": boot.get(self.constraint_file("pl2"), 0) / 1_000_000,
                    "unit": "W"
                },
                "pl4": {
                    "label": "PL4 (Peak Clamp)",
                    "supported": (self.domain / self.constraint_file("pl4")).exists(),
                    "current": (safe_read_int(self.domain / self.constraint_file("pl4")) or 0) / 1_000_000,
                    "boot": boot.get(self.constraint_file("pl4"), 0) / 1_000_000,
                    "unit": "W"
                }
            },
            "time_windows": {
                "pl1_time": {
                    "label": "PL1 Time Window (Tau)",
                    "supported": (self.domain / self.constraint_file("pl1_time")).exists() and safe_read_int(self.domain / self.constraint_file("pl1_time")) is not None,
                    "current": (safe_read_int(self.domain / self.constraint_file("pl1_time")) or 0) / 1_000_000,
                    "boot": boot.get(self.constraint_file("pl1_time"), 0) / 1_000_000,
                    "unit": "s"
                },
                "pl2_time": {
                    "label": "PL2 Time Window",
                    "supported": (self.domain / self.constraint_file("pl2_time")).exists() and safe_read_int(self.domain / self.constraint_file("pl2_time")) is not None,
                    "current": (safe_read_int(self.domain / self.constraint_file("pl2_time")) or 0) / 1_000_000,
                    "boot": boot.get(self.constraint_file("pl2_time"), 0) / 1_000_000,
                    "unit": "s"
                }
            },
            "platform_extension": self.platform.get_status(),
            "asus_wmi": self.platform.get_status(),
        }

