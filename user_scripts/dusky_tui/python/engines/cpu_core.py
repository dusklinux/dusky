#!/usr/bin/env python3
"""
Dusky CPU Core Engine
High-Performance Core Hotplug and Systemd CPU Affinity Manager for Arch Linux (Kernel 7.3+)
"""
import os
lazy import pwd
lazy import json
import re
lazy import tempfile
lazy import subprocess
import time
from pathlib import Path
lazy from typing import Any

from python.frontend.core_types import BaseEngine

RAPL_BASE = Path("/sys/class/powercap")


def get_user_home() -> Path:
    """Resolves the true user home directory even when invoked via sudo or pkexec."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    pkexec_uid = os.environ.get("PKEXEC_UID")
    if pkexec_uid:
        try:
            return Path(pwd.getpwuid(int(pkexec_uid)).pw_dir)
        except (KeyError, ValueError):
            pass
    if os.getuid() != 0:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    home_env = os.environ.get("HOME")
    if home_env and home_env != "/root" and Path(home_env).is_dir():
        return Path(home_env)
    home_dir = Path("/home")
    if home_dir.exists():
        users = [
            p for p in home_dir.iterdir()
            if p.is_dir() and not p.name.startswith(".") and p.name not in ("lost+found", "shared")
        ]
        if len(users) == 1:
            return users[0]
    return Path("~").expanduser()


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


def ensure_real_user_ownership(path: Path) -> None:
    """Ensures created state, cache files, and parent directories in user home are owned by the real user."""
    if os.geteuid() != 0:
        return
    try:
        home = get_user_home()
        uid, gid = None, None
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user and sudo_user != "root":
            pw = pwd.getpwnam(sudo_user)
            uid, gid = pw.pw_uid, pw.pw_gid
        elif os.environ.get("PKEXEC_UID"):
            pw = pwd.getpwuid(int(os.environ["PKEXEC_UID"]))
            uid, gid = pw.pw_uid, pw.pw_gid
        elif home.exists() and home.stat().st_uid != 0:
            st = home.stat()
            uid, gid = st.st_uid, st.st_gid
        
        if uid is not None and gid is not None and uid != 0 and path.is_relative_to(home):
            curr = path
            while curr != home and curr != curr.parent:
                try:
                    if curr.stat().st_uid == 0:
                        os.chown(curr, uid, gid)
                except Exception:
                    pass
                curr = curr.parent
    except Exception:
        pass


def safe_read(path: Path, default: str = "") -> str:
    """Safely reads text from sysfs or disk, returning default on any OS error."""
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        pass
    return default


def safe_write(path: Path, val: str) -> bool:
    """Safely writes text to sysfs or disk."""
    try:
        path.write_text(val, encoding="utf-8")
        return True
    except OSError:
        return False


def atomic_write(path: Path, content: str, user_owned: bool = False) -> None:
    """Replace a small configuration file without exposing a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if user_owned:
        ensure_real_user_ownership(path.parent)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        tmp.chmod(0o644)
        if user_owned:
            ensure_real_user_ownership(tmp)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def format_cpu_list(cores: list[int] | set[int]) -> str:
    """Formats an iterable of core integers into standard Linux CPU list syntax (e.g. '0-3,5,8-11')."""
    if not cores:
        return ""
    sorted_cores = sorted(set(cores))
    ranges: list[str] = []
    start = end = sorted_cores[0]
    for c in sorted_cores[1:]:
        if c == end + 1:
            end = c
        else:
            ranges.append(f"{start}-{end}" if start != end else f"{start}")
            start = end = c
    ranges.append(f"{start}-{end}" if start != end else f"{start}")
    return ",".join(ranges)


def parse_cpu_list(val: str, max_core: int | None = None) -> tuple[bool, str, set[int]]:
    """
    Parses a CPU list string (e.g. '1-19', '0, 2, 4', '1 - 3, 5') into a set of core integers.
    Accepts 'unset', 'none', or 'all'.
    """
    raw = str(val).strip()
    if not raw:
        return False, "CPU mask cannot be empty", set()

    if raw.lower() in ("unset", "none", "__delete__"):
        return True, "unset", set()

    if raw.lower() == "all":
        if max_core is not None:
            return True, "all", set(range(max_core + 1))
        return True, "all", set()

    parsed: set[int] = set()
    raw = re.sub(r"\s*-\s*", "-", raw)
    if raw.startswith(",") or raw.endswith(",") or re.search(r",\s*,", raw):
        return False, "Empty CPU token", set()
    parts = re.split(r"[\s,]+", raw)
    if not parts:
        return False, "No valid CPU tokens found", set()

    for part in parts:
        if "-" in part:
            sub = [s.strip() for s in part.split("-")]
            if len(sub) != 2 or not re.fullmatch(r"[0-9]+", sub[0]) or not re.fullmatch(r"[0-9]+", sub[1]):
                return False, f"Invalid range format: '{part}'", set()
            start, end = int(sub[0]), int(sub[1])
            if start > end:
                start, end = end, start
            if max_core is not None and (start < 0 or end > max_core):
                return False, f"Range '{part}' exceeds hardware bounds (0-{max_core})", set()
            parsed.update(range(start, end + 1))
        else:
            if not re.fullmatch(r"[0-9]+", part):
                return False, f"Invalid CPU ID: '{part}'", set()
            cid = int(part)
            if max_core is not None and (cid < 0 or cid > max_core):
                return False, f"CPU {cid} exceeds hardware bounds (0-{max_core})", set()
            parsed.add(cid)

    if not parsed:
        return False, "No cores specified", set()

    return True, "Valid", parsed


def detect_topology() -> tuple[list[int], list[int], set[int]]:
    """
    Discovers hardware CPU topology, separating Performance Cores,
    Efficient Cores, and Bootstrap Processor (BSP) locked cores.
    Hardware-agnostic: generic across Intel (Alder Lake, Arrow Lake, etc.),
    AMD (homogeneous Zen, hybrid Zen 4/4c, Zen 5/5c), ARM64 (big.LITTLE),
    and virtualized/cloud systems.
    Uses a persistent JSON cache to prevent misclassification when cores are offline.
    """
    cpu_sysfs = Path("/sys/devices/system/cpu")
    cpu_nodes = sorted(
        [node for node in cpu_sysfs.glob("cpu[0-9]*") if node.is_dir() and node.name[3:].isascii() and node.name[3:].isdigit()],
        key=lambda p: int(p.name[3:])
    )
    total_cpus = len(cpu_nodes)

    # Identify locked cores (any node missing 'online', e.g. CPU 0 on x86)
    locked_cores: set[int] = set()
    for node in cpu_nodes:
        cpu_id = int(node.name[3:])
        if not (node / "online").exists():
            locked_cores.add(cpu_id)
    if any(n.name == "cpu0" for n in cpu_nodes):
        locked_cores.add(0)

    # Check persistent cache
    cache_path = get_user_home() / ".config" / "dusky" / "settings" / "cpu_topology.json"
    if cache_path.is_file():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            cached_model = data.get("cpu_model")
            curr_model = get_cpu_model()
            if data.get("version") == 2 and cached_model == curr_model:
                cached_p = [int(c) for c in data.get("p_cores", [])]
                cached_e = [int(c) for c in data.get("e_cores", [])]
                all_cached = set(cached_p + cached_e)
                all_hw = set(int(n.name[3:]) for n in cpu_nodes)
                if all_cached == all_hw and not set(cached_p) & set(cached_e) and len(cached_p + cached_e) == total_cpus:
                    return sorted(cached_p), sorted(cached_e), locked_cores
        except Exception:
            pass

    p_cores: list[int] = []
    e_cores: list[int] = []

    # 1. Check PMU hybrid classification (e.g. Intel /sys/devices/cpu_core and cpu_atom)
    pmu_core_file = Path("/sys/devices/cpu_core/cpus")
    pmu_atom_file = Path("/sys/devices/cpu_atom/cpus")
    if pmu_core_file.exists() and pmu_atom_file.exists():
        ok_core, _, core_set = parse_cpu_list(safe_read(pmu_core_file))
        ok_atom, _, atom_set = parse_cpu_list(safe_read(pmu_atom_file))
        if ok_core and ok_atom and (core_set or atom_set):
            all_known_pmu = core_set | atom_set
            all_hw = set(int(n.name[3:]) for n in cpu_nodes)
            if all_known_pmu == all_hw and not core_set & atom_set:
                p_cores = sorted(core_set)
                e_cores = sorted(atom_set)

    # 2. Check ARM / generic cpu_capacity (e.g. 1024 vs 440)
    if not p_cores and not e_cores:
        caps: dict[int, int] = {}
        for node in cpu_nodes:
            cpu_id = int(node.name[3:])
            cap_val = safe_read(node / "cpu_capacity")
            if cap_val.isdigit():
                caps[cpu_id] = int(cap_val)
        if caps and len(caps) == total_cpus:
            min_c = min(caps.values())
            max_c = max(caps.values())
            if max_c > 0 and (max_c - min_c) / max_c >= 0.20:
                mid = (min_c + max_c) / 2.0
                for cid in [int(n.name[3:]) for n in cpu_nodes]:
                    if caps[cid] > mid:
                        p_cores.append(cid)
                    else:
                        e_cores.append(cid)

    # SMT and CPPC preferred-core rankings do not reliably identify core types.
    # A homogeneous fallback is preferable to inventing a hybrid topology.
    # Default: keep all logical CPUs manageable when no hybrid split is known.
    if not p_cores and not e_cores:
        p_cores = [int(n.name[3:]) for n in cpu_nodes]
        e_cores = []
    elif not p_cores and e_cores:
        p_cores = e_cores
        e_cores = []

    res_p = sorted(set(p_cores))
    res_e = sorted(set(e_cores))

    # Save cache if complete
    if total_cpus and len(res_p + res_e) == total_cpus and all(safe_read(n / "online", "1") == "1" for n in cpu_nodes):
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            ensure_real_user_ownership(cache_path.parent)
            cache_data = {
                "version": 2,
                "cpu_model": get_cpu_model(),
                "total_cores": total_cpus,
                "p_cores": res_p,
                "e_cores": res_e,
                "locked_cores": sorted(locked_cores),
            }
            atomic_write(cache_path, json.dumps(cache_data, indent=2), user_owned=True)
        except OSError:
            pass

    return res_p, res_e, locked_cores


def get_core_status(cpu_id: int) -> bool:
    """Returns True if the core is online or locked (BSP)."""
    node = Path(f"/sys/devices/system/cpu/cpu{cpu_id}")
    return node.is_dir() and safe_read(node / "online", "1") == "1"


def set_core_status(cpu_id: int, enable: bool) -> tuple[bool, str]:
    """Sets a core's online status via sysfs hotplug."""
    online_file = Path(f"/sys/devices/system/cpu/cpu{cpu_id}/online")
    if cpu_id < 0 or not online_file.parent.is_dir():
        return False, f"CPU {cpu_id} does not exist"
    if cpu_id == 0 and not enable:
        return False, "CPU 0 is kernel locked"
    target_state = "1" if enable else "0"
    if not online_file.exists():
        if enable:
            return True, "Already online (BSP Locked)"
        return False, "Locked (BSP)"

    if safe_read(online_file) == target_state:
        return True, "Already in target state"

    if not enable:
        # Offlining the final CPU in a constrained slice can defeat its cpuset.
        online_ok, _, online = parse_cpu_list(safe_read(Path("/sys/devices/system/cpu/online")))
        if online_ok:
            for unit in ("user.slice", "system.slice"):
                raw = safe_read(Path("/sys/fs/cgroup") / unit / "cpuset.cpus")
                ok, _, allowed = parse_cpu_list(raw)
                if ok and allowed & online == {cpu_id}:
                    return False, f"CPU {cpu_id} is the last online CPU allowed by {unit}; change affinity first"
    if safe_write(online_file, target_state):
        for _ in range(10):
            if safe_read(online_file) == target_state:
                return True, "Success"
            time.sleep(0.005)
        return False, "Ignored"
    return False, "Permission denied or locked"


def get_core_freq(cpu_id: int) -> str:
    """Reads the current frequency for a core in MHz."""
    for candidate in ("scaling_cur_freq", "cpuinfo_cur_freq"):
        val = safe_read(Path(f"/sys/devices/system/cpu/cpu{cpu_id}/cpufreq/{candidate}"))
        if val.isdigit():
            return f"{int(val) // 1000} MHz"
    return "---"


class FastEnergyReader:
    """High-speed RAPL energy reader using persistent file descriptor."""

    def __init__(self, path: Path | None):
        self.path = path
        self.fd: int | None = None
        if self.path and self.path.exists():
            self._try_open()

    def _try_open(self) -> None:
        if self.fd is None and self.path:
            try:
                self.fd = os.open(self.path, os.O_RDONLY)
            except OSError:
                self.fd = None

    def read(self) -> int | None:
        if self.fd is None:
            self._try_open()
            if self.fd is None:
                return None
        try:
            os.lseek(self.fd, 0, os.SEEK_SET)
            data = os.read(self.fd, 32).decode(errors="replace").strip()
            return int(data) if data.isdigit() else None
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

    def __del__(self) -> None:
        self.close()


class CpuCoreEngine(BaseEngine):
    """
    Dusky CPU Core Engine
    Manages CPU core online/offline states and systemd CPUAffinity.
    """

    def __init__(self, config_path: str = "", systemd_dropin_path: Path | None = None):
        self.config_path = config_path
        self.systemd_dropin_path = systemd_dropin_path or Path("/etc/systemd/system.conf.d/50-dusky-affinity.conf")
        self.p_cores, self.e_cores, self.locked_cores = detect_topology()
        self.all_cores = sorted(self.p_cores + self.e_cores)
        self.max_core_id = max(self.all_cores) if self.all_cores else (os.cpu_count() or 1) - 1

        # Setup telemetry energy reader
        self.domain = self.find_package_domain()
        self.energy_file = self.domain / "energy_uj" if self.domain else None
        self.reader = FastEnergyReader(self.energy_file)
        self.last_e = self.reader.read()
        self.last_t = time.perf_counter()
        energy_range = safe_read(self.domain / "max_energy_range_uj") if self.domain else ""
        self.max_energy = int(energy_range) if energy_range.isdigit() else 0

    def shutdown(self) -> None:
        """Close telemetry resources after the frontend drains pending reads."""
        self.reader.close()

    @property
    def target_path(self) -> str:
        return "/sys/devices/system/cpu"

    def find_package_domain(self) -> Path | None:
        domains = list(RAPL_BASE.glob("*rapl*"))
        domains.sort(key=lambda p: (1 if "mmio" in p.name else 0, p.name))
        # 1. First priority: standard package domain
        for d in domains:
            name_file = d / "name"
            if name_file.exists() and safe_read(name_file) in ("package-0", "package", "core"):
                if (d / "energy_uj").exists():
                    return d.resolve()
        # 2. Secondary fallback: any valid RAPL domain containing energy_uj
        for d in domains:
            if (d / "energy_uj").exists():
                return d.resolve()
        return None

    def get_systemd_affinity(self) -> str:
        """Read Dusky's configured manager affinity (assignments accumulate)."""
        masks: list[str] = []
        section = ""
        for line in safe_read(self.systemd_dropin_path).splitlines():
            line = line.strip()
            if line.startswith("["):
                section = line
            elif section == "[Manager]" and line.startswith("CPUAffinity="):
                value = line.split("=", 1)[1].strip()
                if not value:
                    masks.clear()
                else:
                    masks.append(value)
        if not masks:
            return "unset"
        ok, _, cores = parse_cpu_list(" ".join(masks), self.max_core_id)
        return format_cpu_list(cores) if ok else " ".join(masks)

    def get_effective_affinity(self) -> str:
        """Reads the live effective allowed CPUs from cgroup user.slice, or PID 1 status."""
        for candidate in (
            Path("/sys/fs/cgroup/user.slice/cpuset.cpus.effective"),
            Path("/sys/fs/cgroup/user.slice/cpuset.cpus"),
        ):
            if candidate.is_file():
                try:
                    val = candidate.read_text(encoding="utf-8").strip()
                    if val:
                        return val
                except Exception:
                    pass

        try:
            status_file = Path("/proc/1/status")
            if status_file.is_file():
                for line in status_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("Cpus_allowed_list:"):
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass

        return "all"

    def get_pid1_affinity(self) -> str:
        """Reads the live Cpus_allowed_list directly from /proc/1/status."""
        try:
            status_file = Path("/proc/1/status")
            if status_file.is_file():
                for line in status_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("Cpus_allowed_list:"):
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass
        return "all"

    def validate_affinity_mask(self, val: str) -> tuple[bool, str]:
        """Validates systemd CPUAffinity string format."""
        val_clean = str(val).strip()
        if not val_clean:
            return False, "Affinity string cannot be empty"
        ok, msg, cores = parse_cpu_list(val_clean, max_core=self.max_core_id)
        if val_clean.lower() == "all":
            return True, "Valid"
        if ok and cores - set(self.all_cores):
            return False, "Mask includes CPUs absent from this machine"
        return ok, msg

    def set_systemd_affinity(
        self,
        val: str,
        run_daemon_reexec: bool = True,
        save_state: bool = True
    ) -> tuple[bool, str]:
        """Apply manager defaults and live slice cpusets; never revert other properties."""
        clean = str(val).strip()
        unset = clean.lower() in ("unset", "none", "__delete__", "all", "")
        ok, msg, cores = parse_cpu_list("unset" if unset else clean, self.max_core_id)
        if not ok:
            return False, msg
        if cores - set(self.all_cores):
            return False, "Mask includes CPUs absent from this machine"
        online = {c for c in self.all_cores if get_core_status(c)}
        if not online:
            return False, "Cannot determine any online CPUs"
        if not unset and not cores & online:
            return False, "Affinity must include at least one online CPU"
        normalized = "" if unset else format_cpu_list(cores)
        dropin = self.systemd_dropin_path
        try:
            previous = dropin.read_text() if dropin.exists() else None
        except OSError as exc:
            return False, f"Cannot read existing affinity configuration: {exc}"
        slice_previous: dict[str, str] = {}
        pid_previous: set[int] | None = None
        try:
            pid_previous = os.sched_getaffinity(1)
            for unit in ("user.slice", "system.slice"):
                result = subprocess.run(["/usr/bin/systemctl", "show", unit, "--property=AllowedCPUs", "--value"],
                                        capture_output=True, text=True, timeout=15, check=True)
                slice_previous[unit] = result.stdout.strip()
            if unset:
                dropin.unlink(missing_ok=True)
            else:
                atomic_write(dropin, "# Generated by Dusky CPU Core Manager\n[Manager]\n"
                             f"CPUAffinity=\nCPUAffinity={normalized}\n")
            # set-property updates only AllowedCPUs and persists it across boots.
            for unit in ("user.slice", "system.slice"):
                subprocess.run(["/usr/bin/systemctl", "set-property", unit,
                                f"AllowedCPUs={normalized}"], capture_output=True,
                               text=True, timeout=15, check=True)
            os.sched_setaffinity(1, online if unset else cores)
            if run_daemon_reexec:
                subprocess.run(["/usr/bin/systemctl", "daemon-reexec"], capture_output=True,
                               text=True, timeout=20, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            rollback_errors = []
            try:
                if previous is None:
                    dropin.unlink(missing_ok=True)
                else:
                    atomic_write(dropin, previous)
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
            for unit, old in slice_previous.items():
                try:
                    subprocess.run(["/usr/bin/systemctl", "set-property", unit, f"AllowedCPUs={old}"],
                                   capture_output=True, text=True, timeout=15, check=True)
                except (OSError, subprocess.SubprocessError) as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
            if pid_previous is not None:
                try:
                    os.sched_setaffinity(1, pid_previous)
                except OSError as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
            detail = getattr(exc, "stderr", None) or str(exc)
            return False, f"Affinity application failed: {detail}" + (f"; rollback incomplete: {'; '.join(rollback_errors)}" if rollback_errors else "")
        if save_state:
            try:
                self.save_persistent_state()
            except OSError as exc:
                return False, f"Affinity applied but persistence failed: {exc}"
        return True, f"Applied manager affinity and live slice limits: {normalized or 'unset'}"

    def load_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {}
        for core in self.all_cores:
            status = get_core_status(core)
            state[f"cpu{core}"] = status
            state[f"DEFAULT/cpu{core}"] = status

        aff = self.get_systemd_affinity()
        state["systemd_cpu_affinity"] = aff
        state["DEFAULT/systemd_cpu_affinity"] = aff
        return state

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        operations: list[tuple[int, bool]] = []
        affinity: str | None = None
        for key, scope, val, _ in changes:
            if key == "systemd_cpu_affinity":
                ok, msg = self.validate_affinity_mask(val)
                if not ok:
                    return False, msg, ""
                affinity = val
                continue
            if not key.startswith("cpu") or not re.fullmatch(r"[0-9]+", key[3:]) or int(key[3:]) not in self.all_cores:
                return False, f"Invalid CPU key: {key}", ""
            clean = str(val).strip().lower()
            if clean not in ("true", "1", "yes", "on", "false", "0", "no", "off"):
                return False, f"Invalid boolean: {val}", ""
            cpu = int(key[3:])
            enable = clean in ("true", "1", "yes", "on")
            if cpu in self.locked_cores and not enable:
                return False, f"CPU {cpu} is kernel locked and cannot be disabled", ""
            operations.append((cpu, enable))
        failures = []
        # Enable CPUs first, apply the new mask, then disable CPUs.
        for cpu, enable in operations:
            if enable:
                ok, msg = set_core_status(cpu, True)
                if not ok:
                    failures.append(f"cpu{cpu}: {msg}")
        if affinity is not None and not failures:
            ok, msg = self.set_systemd_affinity(affinity, save_state=False)
            if not ok:
                failures.append(msg)
        if not failures:
            for cpu, enable in operations:
                if not enable:
                    ok, msg = set_core_status(cpu, False)
                    if not ok:
                        failures.append(f"cpu{cpu}: {msg}")
        if changes:
            try:
                self.save_persistent_state()
            except OSError as exc:
                failures.append(f"Persistence failed: {exc}")
        return not failures, "; ".join(failures) if failures else f"Applied {len(changes)} settings", ""

    def save_persistent_state(self) -> None:
        config_dir = get_user_home() / ".config" / "dusky" / "settings"
        state = {"cpu_model": get_cpu_model(),
                 **{f"cpu{c}": get_core_status(c) for c in self.all_cores},
                 "systemd_cpu_affinity": self.get_systemd_affinity()}
        atomic_write(config_dir / "dusky_cores", json.dumps(state, indent=2), user_owned=True)

    def restore_state(self) -> bool:
        state_file = get_user_home() / ".config" / "dusky" / "settings" / "dusky_cores"
        if not state_file.exists():
            return True
        try:
            state = json.loads(state_file.read_text())
            if not isinstance(state, dict) or state.get("cpu_model") != get_cpu_model():
                return False
            operations = []
            for key, value in state.items():
                if key.startswith("cpu") and re.fullmatch(r"[0-9]+", key[3:]):
                    cpu = int(key[3:])
                    if cpu not in self.all_cores or not isinstance(value, bool):
                        return False
                    if cpu not in self.locked_cores:
                        operations.append((cpu, value))
            ok = True
            for cpu, enable in operations:
                if enable:
                    applied, _ = set_core_status(cpu, True)
                    ok = applied and ok
            if "systemd_cpu_affinity" in state and ok:
                applied, _ = self.set_systemd_affinity(state["systemd_cpu_affinity"],
                                                      run_daemon_reexec=False, save_state=False)
                ok = applied and ok
            if ok:
                for cpu, enable in operations:
                    if not enable:
                        applied, _ = set_core_status(cpu, False)
                        ok = applied and ok
            return ok
        except (OSError, ValueError, TypeError):
            return False

    def get_telemetry(self) -> str:
        online_count = sum(1 for c in self.all_cores if get_core_status(c))
        total_cores = len(self.all_cores)

        # Calculate RAPL power
        pkg_watts = 0.0
        has_power = False
        if self.reader:
            curr_e = self.reader.read()
            curr_t = time.perf_counter()
            if curr_e is not None and self.last_e is not None and self.last_t is not None:
                delta_e = curr_e - self.last_e
                delta_t = curr_t - self.last_t
                if delta_t > 0:
                    if delta_e < 0 and self.max_energy > 0:
                        delta_e += self.max_energy
                    if delta_e >= 0:
                        pkg_watts = (delta_e / 1_000_000.0) / delta_t
                        has_power = True
            if curr_e is not None:
                self.last_e = curr_e
                self.last_t = curr_t

        bar_w = 16
        filled = max(0, min(bar_w, int((online_count / total_cores) * bar_w))) if total_cores else 0
        bar_graph = "█" * filled + "░" * (bar_w - filled)

        eff_aff = self.get_effective_affinity()
        cfg_aff = self.get_systemd_affinity()
        aff_info = f"Affinity: {cfg_aff} (Active: {eff_aff})" if cfg_aff != "unset" else f"Affinity: All ({eff_aff})"
        pwr_str = f" | {pkg_watts:4.1f} W" if has_power else ""

        return f" {online_count}/{total_cores} Cores [{bar_graph}] | {aff_info}{pwr_str}"
