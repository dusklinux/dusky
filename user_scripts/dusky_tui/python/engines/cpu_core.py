#!/usr/bin/env python3
"""
Dusky CPU Core Engine
High-Performance Core Hotplug and Systemd CPU Affinity Manager for Arch Linux (Kernel 7.2+)
"""
import os
import pwd
import sys
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

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
        
        if uid is not None and gid is not None and uid != 0:
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
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return False, "No valid CPU tokens found", set()

    for part in parts:
        if "-" in part:
            sub = [s.strip() for s in part.split("-")]
            if len(sub) != 2 or not sub[0].isdigit() or not sub[1].isdigit():
                return False, f"Invalid range format: '{part}'", set()
            start, end = int(sub[0]), int(sub[1])
            if start > end:
                start, end = end, start
            if max_core is not None and (start < 0 or end > max_core):
                return False, f"Range '{part}' exceeds hardware bounds (0-{max_core})", set()
            parsed.update(range(start, end + 1))
        else:
            if not part.isdigit():
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
        [node for node in cpu_sysfs.glob("cpu[0-9]*") if node.is_dir()],
        key=lambda p: int(p.name[3:])
    )
    total_cpus = len(cpu_nodes)

    # Identify locked cores (any node missing 'online', e.g. CPU 0 on x86)
    locked_cores: set[int] = set()
    for node in cpu_nodes:
        cpu_id = int(node.name[3:])
        if not (node / "online").exists():
            locked_cores.add(cpu_id)
    if not locked_cores and cpu_nodes:
        locked_cores.add(int(cpu_nodes[0].name[3:]))

    # Check persistent cache
    cache_path = get_user_home() / ".config" / "dusky" / "settings" / "cpu_topology.json"
    if cache_path.is_file():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            cached_model = data.get("cpu_model")
            curr_model = get_cpu_model()
            if not (cached_model and curr_model != "Generic CPU" and cached_model != curr_model):
                cached_p = [int(c) for c in data.get("p_cores", [])]
                cached_e = [int(c) for c in data.get("e_cores", [])]
                cached_locked = set(int(c) for c in data.get("locked_cores", []))
                all_cached = set(cached_p + cached_e)
                all_hw = set(int(n.name[3:]) for n in cpu_nodes)
                if all_cached == all_hw and len(all_cached) == total_cpus:
                    final_locked = locked_cores | cached_locked
                    return sorted(cached_p), sorted(cached_e), final_locked
        except Exception:
            pass

    p_cores: list[int] = []
    e_cores: list[int] = []
    original_states: dict[int, str] = {}

    # If running with root and some cores are offline, temporarily bring them online
    # to allow reading ACPI CPPC and topology registers
    if os.geteuid() == 0:
        for node in cpu_nodes:
            cpu_id = int(node.name[3:])
            online_file = node / "online"
            if not online_file.exists():
                continue
            cur_state = safe_read(online_file)
            original_states[cpu_id] = cur_state
            if cur_state == "0":
                try:
                    online_file.write_text("1", encoding="utf-8")
                    top_dir = node / "topology"
                    for _ in range(20):
                        if top_dir.exists() and (top_dir / "core_cpus_list").exists():
                            break
                        time.sleep(0.005)
                except OSError:
                    pass

    # 1. Check PMU hybrid classification (e.g. Intel /sys/devices/cpu_core and cpu_atom)
    pmu_core_file = Path("/sys/devices/cpu_core/cpus")
    pmu_atom_file = Path("/sys/devices/cpu_atom/cpus")
    if pmu_core_file.exists() and pmu_atom_file.exists():
        ok_core, _, core_set = parse_cpu_list(safe_read(pmu_core_file))
        ok_atom, _, atom_set = parse_cpu_list(safe_read(pmu_atom_file))
        if ok_core and ok_atom and (core_set or atom_set):
            all_known_pmu = core_set | atom_set
            all_hw = set(int(n.name[3:]) for n in cpu_nodes)
            if all_known_pmu == all_hw:
                p_cores = sorted(core_set)
                e_cores = sorted(atom_set)

    # 2. Check sysfs core_type (intel_atom / 1 / 0x10 vs intel_core / 2 / 0x20)
    if not p_cores and not e_cores:
        ct_p, ct_e = [], []
        has_core_type = False
        for node in cpu_nodes:
            cpu_id = int(node.name[3:])
            ct_val = safe_read(node / "topology" / "core_type").lower()
            if ct_val in ("1", "0x10", "intel_atom"):
                ct_e.append(cpu_id)
                has_core_type = True
            elif ct_val in ("2", "0x20", "intel_core"):
                ct_p.append(cpu_id)
                has_core_type = True
        if has_core_type and (len(ct_p) + len(ct_e) == total_cpus):
            p_cores = sorted(ct_p)
            e_cores = sorted(ct_e)

    # 3. Check ARM / generic cpu_capacity (e.g. 1024 vs 440)
    if not p_cores and not e_cores:
        caps: dict[int, int] = {}
        for node in cpu_nodes:
            cpu_id = int(node.name[3:])
            cap_val = safe_read(node / "cpu_capacity")
            if cap_val.isdigit():
                caps[cpu_id] = int(cap_val)
        if len(caps) == total_cpus:
            min_c = min(caps.values())
            max_c = max(caps.values())
            if max_c > 0 and (max_c - min_c) / max_c >= 0.20:
                mid = (min_c + max_c) / 2.0
                for cid in [int(n.name[3:]) for n in cpu_nodes]:
                    if caps[cid] > mid:
                        p_cores.append(cid)
                    else:
                        e_cores.append(cid)

    # 4. Check ACPI CPPC highest_perf (Intel & AMD hybrid)
    if not p_cores and not e_cores:
        cppc_perf: dict[int, int] = {}
        for node in cpu_nodes:
            cpu_id = int(node.name[3:])
            perf_str = safe_read(node / "acpi_cppc" / "highest_perf")
            if perf_str.isdigit():
                cppc_perf[cpu_id] = int(perf_str)

        if len(cppc_perf) == total_cpus:
            unique_perfs = sorted(set(cppc_perf.values()))
            min_p = unique_perfs[0]
            max_p = unique_perfs[-1]
            if max_p > 0 and (max_p - min_p) / max_p >= 0.20:
                midpoint = (min_p + max_p) / 2.0
                for cpu_id in [int(n.name[3:]) for n in cpu_nodes]:
                    if cppc_perf[cpu_id] > midpoint:
                        p_cores.append(cpu_id)
                    else:
                        e_cores.append(cpu_id)

    # 5. Check SMT asymmetry fallback (e.g. where P has 2 threads, E has 1 thread)
    if not p_cores and not e_cores:
        smt_siblings: dict[int, list[int]] = {}
        for node in cpu_nodes:
            cpu_id = int(node.name[3:])
            top_dir = node / "topology"
            core_cpus = safe_read(top_dir / "core_cpus_list")
            siblings: list[int] = []
            if core_cpus:
                for part in core_cpus.split(","):
                    part = part.strip()
                    if "-" in part:
                        try:
                            s, e = map(int, part.split("-"))
                            siblings.extend(range(s, e + 1))
                        except ValueError:
                            pass
                    elif part.isdigit():
                        siblings.append(int(part))
            smt_siblings[cpu_id] = siblings or [cpu_id]

        multithread_cores = [cid for cid, s_list in smt_siblings.items() if len(s_list) > 1]
        singlethread_cores = [cid for cid, s_list in smt_siblings.items() if len(s_list) == 1]
        if multithread_cores and singlethread_cores:
            p_cores = sorted(multithread_cores)
            e_cores = sorted(singlethread_cores)

    # 6. Default / Homogeneous fallback: All cores are Performance Cores
    if not p_cores and not e_cores:
        p_cores = [int(n.name[3:]) for n in cpu_nodes]
        e_cores = []
    elif not p_cores and e_cores:
        p_cores = e_cores
        e_cores = []

    # Restore any cores that were temporarily brought online
    for cpu_id, orig_state in original_states.items():
        if orig_state == "0":
            try:
                (cpu_sysfs / f"cpu{cpu_id}" / "online").write_text("0", encoding="utf-8")
            except OSError:
                pass

    res_p = sorted(set(p_cores))
    res_e = sorted(set(e_cores))

    # Save cache if complete
    if len(res_p + res_e) == total_cpus:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            ensure_real_user_ownership(cache_path.parent)
            cache_data = {
                "cpu_model": get_cpu_model(),
                "total_cores": total_cpus,
                "p_cores": res_p,
                "e_cores": res_e,
                "locked_cores": sorted(locked_cores),
            }
            tmp_cache = cache_path.parent / f".cpu_topology.tmp-{os.getpid()}"
            tmp_cache.write_text(json.dumps(cache_data, indent=2), encoding="utf-8")
            ensure_real_user_ownership(tmp_cache)
            tmp_cache.replace(cache_path)
            ensure_real_user_ownership(cache_path)
        except Exception:
            if "tmp_cache" in locals() and tmp_cache.exists():
                try:
                    tmp_cache.unlink(missing_ok=True)
                except OSError:
                    pass

    return res_p, res_e, locked_cores


def get_core_status(cpu_id: int) -> bool:
    """Returns True if the core is online or locked (BSP)."""
    online_file = Path(f"/sys/devices/system/cpu/cpu{cpu_id}/online")
    if not online_file.exists():
        return True
    return safe_read(online_file, default="1") == "1"


def set_core_status(cpu_id: int, enable: bool) -> tuple[bool, str]:
    """Sets a core's online status via sysfs hotplug."""
    online_file = Path(f"/sys/devices/system/cpu/cpu{cpu_id}/online")
    target_state = "1" if enable else "0"
    if not online_file.exists():
        if enable:
            return True, "Already online (BSP Locked)"
        return False, "Locked (BSP)"

    if safe_read(online_file) == target_state:
        return True, "Already in target state"

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
        self.max_energy = int(safe_read(self.domain / "max_energy_range_uj", "0")) or 0 if self.domain else 0

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
        """Reads the currently configured CPUAffinity from the systemd drop-in file."""
        dropin = self.systemd_dropin_path
        if dropin.is_file():
            try:
                for line in dropin.read_text(encoding="utf-8", errors="replace").splitlines():
                    line_s = line.strip()
                    if line_s.startswith("#") or line_s.startswith(";"):
                        continue
                    if "=" in line_s:
                        k, v = line_s.split("=", 1)
                        if k.strip() == "CPUAffinity":
                            val = v.strip()
                            return val if val else "unset"
            except Exception:
                pass
        return "unset"

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
        ok, msg, _ = parse_cpu_list(val_clean, max_core=self.max_core_id)
        return ok, msg

    def set_systemd_affinity(
        self,
        val: str,
        run_daemon_reexec: bool = True,
        save_state: bool = True
    ) -> tuple[bool, str]:
        """
        Applies or removes systemd CPUAffinity via atomic drop-in configuration
        and live cgroups v2 slice enforcement across user.slice and system.slice.
        """
        dropin = self.systemd_dropin_path
        val_clean = str(val).strip()

        # 1. Unset / All Cores
        if val_clean.lower() in ("unset", "__delete__", "", "all"):
            if dropin.exists():
                try:
                    dropin.unlink()
                except OSError as e:
                    return False, f"Failed to remove drop-in {dropin}: {e}"

            if os.geteuid() == 0:
                try:
                    os.sched_setaffinity(1, set(self.all_cores))
                except OSError:
                    pass

            if run_daemon_reexec:
                try:
                    subprocess.run(["systemctl", "revert", "user.slice", "system.slice"], capture_output=True, timeout=10)
                    for ctrl_dir in (Path("/etc/systemd/system.control/user.slice.d"), Path("/etc/systemd/system.control/system.slice.d")):
                        if ctrl_dir.exists():
                            shutil.rmtree(ctrl_dir, ignore_errors=True)
                    subprocess.run(["systemctl", "daemon-reload"], capture_output=True, timeout=10)
                except Exception as e:
                    return False, f"systemctl reset error: {e}"

            if save_state:
                self.save_persistent_state()
            return True, "Removed systemd CPU affinity drop-in and slice limits (all cores active)"

        # 2. Validation & Normalization
        ok, msg, parsed_cores = parse_cpu_list(val_clean, max_core=self.max_core_id)
        if not ok:
            return False, msg
        normalized_mask = format_cpu_list(sorted(parsed_cores))

        # 3. Write drop-in atomically for boot persistence
        try:
            dropin.parent.mkdir(parents=True, exist_ok=True)
            content = (
                "# Generated by Dusky CPU Core Manager\n"
                "# Configures systemd PID 1 and descendant service/session CPU affinity\n"
                "[Manager]\n"
                f"CPUAffinity={normalized_mask}\n"
            )
            temp_file = dropin.parent / f".{dropin.name}.tmp-{os.getpid()}"
            temp_file.write_text(content, encoding="utf-8")
            temp_file.replace(dropin)
        except OSError as e:
            return False, f"Failed to write drop-in {dropin}: {e}"

        # 4. Apply live PID 1, cgroups v2 slice enforcement
        if os.geteuid() == 0:
            try:
                os.sched_setaffinity(1, parsed_cores)
            except OSError:
                pass

        if run_daemon_reexec:
            try:
                subprocess.run(["systemctl", "set-property", "user.slice", f"AllowedCPUs={normalized_mask}"], capture_output=True, timeout=10)
                subprocess.run(["systemctl", "set-property", "system.slice", f"AllowedCPUs={normalized_mask}"], capture_output=True, timeout=10)
                subprocess.run(["systemctl", "daemon-reload"], capture_output=True, timeout=10)
            except Exception as e:
                return False, f"systemctl execution error: {e}"

        if save_state:
            self.save_persistent_state()
        return True, f"Successfully applied live and persistent CPU affinity: {normalized_mask}"

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
        if target_key == "systemd_cpu_affinity":
            ok, msg = self.set_systemd_affinity(new_value)
            return ok, msg, ""

        if not target_key.startswith("cpu") or not target_key[3:].isdigit():
            return False, f"Invalid key: {target_key}", ""

        core_id = int(target_key[3:])
        enable = str(new_value).lower() in ("true", "1", "yes")

        if core_id in self.locked_cores:
            if enable:
                return True, f"CPU {core_id} is locked (BSP) and already online", ""
            return False, f"CPU {core_id} is locked (BSP) and cannot be disabled", ""

        success, msg = set_core_status(core_id, enable)
        if success:
            self.save_persistent_state()
            return True, f"Successfully set CPU {core_id} {'online' if enable else 'offline'}", ""
        return False, f"Failed to toggle CPU {core_id}: {msg}", ""

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        success_count = 0
        failed_keys: list[str] = []
        last_debug = ""
        has_affinity_change = False

        for key, scope, val, itype in changes:
            if key == "systemd_cpu_affinity":
                ok, msg = self.set_systemd_affinity(val, run_daemon_reexec=False)
                if ok:
                    success_count += 1
                    has_affinity_change = True
                else:
                    failed_keys.append(key)
                continue

            if not key.startswith("cpu") or not key[3:].isdigit():
                failed_keys.append(key)
                continue

            core_id = int(key[3:])
            enable = str(val).lower() in ("true", "1", "yes")
            if core_id in self.locked_cores:
                if enable:
                    success_count += 1
                else:
                    failed_keys.append(key)
                continue

            ok, _ = set_core_status(core_id, enable)
            if ok:
                success_count += 1
            else:
                failed_keys.append(key)

        # Batch write persistent state ONCE at the end
        self.save_persistent_state()

        if has_affinity_change:
            try:
                subprocess.run(["systemctl", "daemon-reload"], capture_output=True, timeout=10)
                subprocess.run(["systemctl", "daemon-reexec"], capture_output=True, timeout=15)
            except Exception:
                pass

        if success_count == len(changes):
            return True, f"Successfully batched {success_count} writes.", last_debug
        return False, f"Batch wrote {success_count}/{len(changes)}. Failed keys: {', '.join(failed_keys)}", last_debug

    def save_persistent_state(self) -> None:
        try:
            home = get_user_home()
            config_dir = home / ".config" / "dusky" / "settings"
            config_dir.mkdir(parents=True, exist_ok=True)
            ensure_real_user_ownership(config_dir)
            state_file = config_dir / "dusky_cores"

            cores_state: dict[str, Any] = {"cpu_model": get_cpu_model()}
            for core in self.all_cores:
                cores_state[f"cpu{core}"] = get_core_status(core)
            cores_state["systemd_cpu_affinity"] = self.get_systemd_affinity()

            temp_file = config_dir / f".dusky_cores.tmp-{os.getpid()}"
            temp_file.write_text(json.dumps(cores_state, indent=2), encoding="utf-8")
            ensure_real_user_ownership(temp_file)
            temp_file.replace(state_file)
            ensure_real_user_ownership(state_file)
        except Exception:
            pass

    def restore_state(self) -> bool:
        try:
            home = get_user_home()
            state_file = home / ".config" / "dusky" / "settings" / "dusky_cores"
            if not state_file.exists():
                return False
            cores_state = json.loads(state_file.read_text(encoding="utf-8"))

            cached_model = cores_state.get("cpu_model")
            curr_model = get_cpu_model()
            if cached_model and curr_model != "Generic CPU" and cached_model != curr_model:
                return False

            for k, v in cores_state.items():
                if k.startswith("cpu") and k[3:].isdigit():
                    core_id = int(k[3:])
                    if core_id in self.all_cores and core_id not in self.locked_cores:
                        set_core_status(core_id, bool(v))
                elif k == "systemd_cpu_affinity":
                    self.set_systemd_affinity(v if v else "unset", run_daemon_reexec=False, save_state=False)
            return True
        except Exception:
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
