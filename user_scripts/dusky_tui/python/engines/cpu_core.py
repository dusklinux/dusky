#!/usr/bin/env python3
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from python.frontend.core_types import BaseEngine

RAPL_BASE = Path("/sys/class/powercap")

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

def safe_read(path: Path, default: str = "") -> str:
    try:
        if path.is_file():
            return path.read_text().strip()
    except OSError:
        pass
    return default

def safe_write(path: Path, val: str) -> bool:
    try:
        path.write_text(val)
        return True
    except OSError:
        return False

def get_core_status(cpu_id: int) -> bool:
    return safe_read(Path(f"/sys/devices/system/cpu/cpu{cpu_id}/online"), default="1") == "1"

def set_core_status(cpu_id: int, enable: bool) -> tuple[bool, str]:
    online_file = Path(f"/sys/devices/system/cpu/cpu{cpu_id}/online")
    target_state = "1" if enable else "0"
    if not online_file.exists():
        return False, "Locked"
    if safe_read(online_file) == target_state:
        return True, "Already in target state"
    if safe_write(online_file, target_state):
        if safe_read(online_file) == target_state:
            return True, "Success"
        return False, "Ignored"
    return False, "Permission denied or locked"

def get_core_freq(cpu_id: int) -> str:
    val = safe_read(Path(f"/sys/devices/system/cpu/cpu{cpu_id}/cpufreq/scaling_cur_freq"))
    if val.isdigit():
        return f"{int(val) // 1000} MHz"
    return "---"

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

class CpuCoreEngine(BaseEngine):
    def __init__(self, config_path: str = "", systemd_dropin_path: Path | None = None):
        self.config_path = config_path
        self.systemd_dropin_path = systemd_dropin_path or Path("/etc/systemd/system.conf.d/50-dusky-affinity.conf")
        self.p_cores, self.e_cores, self.locked_cores = self.detect_topology()
        
        # Setup telemetry energy reader
        self.domain = self.find_package_domain()
        self.energy_file = self.domain / "energy_uj" if self.domain else None
        self.reader = None
        self.last_e = None
        self.last_t = None
        self.max_energy = int(safe_read(self.domain / "max_energy_range_uj", "0")) or 0 if self.domain else 0
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

    def detect_topology(self) -> tuple[list[int], list[int], set[int]]:
        p_cores = []
        e_cores = []
        locked_cores = set()
        cpu_sysfs = Path("/sys/devices/system/cpu")
        cpu_nodes = sorted([node for node in cpu_sysfs.glob("cpu[0-9]*") if node.is_dir()], key=lambda p: int(p.name[3:]))
        original_states = {}

        for node in cpu_nodes:
            cpu_id = int(node.name[3:])
            online_file = node / "online"
            if not online_file.exists():
                locked_cores.add(cpu_id)
                continue
            current_state = safe_read(online_file)
            original_states[cpu_id] = current_state
            if current_state == "0":
                try:
                    online_file.write_text("1")
                    topology_dir = node / "topology"
                    for _ in range(20):
                        if topology_dir.exists() and (topology_dir / "core_cpus_list").exists():
                            break
                        time.sleep(0.005)
                except OSError:
                    pass

        cppc_perf = {}
        for node in cpu_nodes:
            cpu_id = int(node.name[3:])
            perf_str = safe_read(node / "acpi_cppc" / "highest_perf")
            if perf_str.isdigit():
                cppc_perf[cpu_id] = int(perf_str)

        cppc_classified = False
        if cppc_perf:
            unique_perfs = sorted(list(set(cppc_perf.values())))
            if len(unique_perfs) > 1:
                midpoint = (unique_perfs[0] + unique_perfs[-1]) / 2
                for cpu_id in [int(n.name[3:]) for n in cpu_nodes]:
                    perf = cppc_perf.get(cpu_id, unique_perfs[0])
                    if perf > midpoint:
                        p_cores.append(cpu_id)
                    else:
                        e_cores.append(cpu_id)
                cppc_classified = True

        if not cppc_classified:
            smt_siblings = {}
            for node in cpu_nodes:
                cpu_id = int(node.name[3:])
                topology_dir = node / "topology"
                core_cpus = safe_read(topology_dir / "core_cpus_list")
                siblings = []
                if core_cpus:
                    if "," in core_cpus:
                        siblings = [int(x) for x in core_cpus.split(",") if x.isdigit()]
                    elif "-" in core_cpus:
                        try:
                            start, end = map(int, core_cpus.split("-"))
                            siblings = list(range(start, end + 1))
                        except ValueError:
                            pass
                if not siblings:
                    siblings = [cpu_id]
                smt_siblings[cpu_id] = siblings

            for node in cpu_nodes:
                cpu_id = int(node.name[3:])
                topology_dir = node / "topology"
                core_type_val = safe_read(topology_dir / "core_type")
                if core_type_val in ("1", "0x10", "intel_atom"):
                    e_cores.append(cpu_id)
                elif core_type_val in ("2", "0x20", "intel_core"):
                    p_cores.append(cpu_id)
                else:
                    siblings = smt_siblings.get(cpu_id, [cpu_id])
                    if len(siblings) > 1:
                        p_cores.append(cpu_id)
                    else:
                        is_sibling_of_smt = False
                        for other_id, sib_list in smt_siblings.items():
                            if other_id != cpu_id and cpu_id in sib_list and len(sib_list) > 1:
                                is_sibling_of_smt = True
                                break
                        if is_sibling_of_smt:
                            p_cores.append(cpu_id)
                        else:
                            e_cores.append(cpu_id)

        for cpu_id, original_state in original_states.items():
            if original_state == "0":
                try:
                    Path(f"/sys/devices/system/cpu/cpu{cpu_id}/online").write_text("0")
                except OSError:
                    pass

        all_found = sorted(p_cores + e_cores)
        if not locked_cores and all_found:
            locked_cores.add(all_found[0])

        if not p_cores and e_cores:
            p_cores = e_cores
            e_cores = []

        return sorted(p_cores), sorted(e_cores), locked_cores

    @property
    def target_path(self) -> str:
        return "/sys/devices/system/cpu"

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
        cpuset_file = Path("/sys/fs/cgroup/user.slice/cpuset.cpus")
        if cpuset_file.is_file():
            try:
                val = cpuset_file.read_text(encoding="utf-8").strip()
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
        return "unknown"

    def validate_affinity_mask(self, val: str) -> tuple[bool, str]:
        """
        Validates systemd CPUAffinity string format (e.g. '1-19', '0,2,4', '1-7,12-15').
        Ensures all core IDs are non-negative, in hardware bounds, start <= end for ranges,
        and at least one core is specified.
        """
        raw = val.strip()
        if not raw:
            return False, "Affinity string cannot be empty"

        all_cores = self.p_cores + self.e_cores
        max_core = max(all_cores) if all_cores else (os.cpu_count() or 1) - 1

        parsed_cores: set[int] = set()
        parts = [p.strip() for p in raw.split(",")]
        if not parts or any(p == "" for p in parts):
            return False, "Invalid syntax: empty or consecutive comma token"

        for part in parts:
            if "-" in part:
                sub = part.split("-")
                if len(sub) != 2 or not sub[0].isdigit() or not sub[1].isdigit():
                    return False, f"Invalid range format: '{part}'"
                start, end = int(sub[0]), int(sub[1])
                if start > end:
                    return False, f"Invalid range: start ({start}) > end ({end})"
                if start < 0 or end > max_core:
                    return False, f"Range '{part}' exceeds hardware bounds (0-{max_core})"
                parsed_cores.update(range(start, end + 1))
            else:
                if not part.isdigit():
                    return False, f"Invalid CPU ID: '{part}'"
                cid = int(part)
                if cid < 0 or cid > max_core:
                    return False, f"CPU {cid} exceeds hardware bounds (0-{max_core})"
                parsed_cores.add(cid)

        if not parsed_cores:
            return False, "No cores specified"

        return True, "Valid"

    def set_systemd_affinity(self, val: str, run_daemon_reexec: bool = True) -> tuple[bool, str]:
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

            if run_daemon_reexec:
                try:
                    subprocess.run(["systemctl", "set-property", "user.slice", "AllowedCPUs="], capture_output=True, timeout=5)
                    subprocess.run(["systemctl", "set-property", "system.slice", "AllowedCPUs="], capture_output=True, timeout=5)
                    for ctrl_dir in (Path("/etc/systemd/system.control/user.slice.d"), Path("/etc/systemd/system.control/system.slice.d")):
                        if ctrl_dir.exists():
                            import shutil
                            shutil.rmtree(ctrl_dir, ignore_errors=True)
                    subprocess.run(["systemctl", "daemon-reload"], capture_output=True, timeout=5)
                    subprocess.run(["systemctl", "daemon-reexec"], capture_output=True, timeout=10)
                except Exception as e:
                    return False, f"systemctl reset error: {e}"

            self.save_persistent_state()
            return True, "Removed systemd CPU affinity drop-in and slice limits (all cores active)"

        # 2. Validation
        valid, msg = self.validate_affinity_mask(val_clean)
        if not valid:
            return False, msg

        # 3. Write drop-in atomically for boot persistence (NO .bak files)
        try:
            dropin.parent.mkdir(parents=True, exist_ok=True)
            content = (
                "# Generated by Dusky CPU Core Manager\n"
                "# Configures systemd PID 1 and descendant service/session CPU affinity\n"
                "[Manager]\n"
                f"CPUAffinity={val_clean}\n"
            )
            temp_file = dropin.parent / f".{dropin.name}.tmp-{os.getpid()}"
            temp_file.write_text(content, encoding="utf-8")
            temp_file.replace(dropin)
        except OSError as e:
            return False, f"Failed to write drop-in {dropin}: {e}"

        # 4. Apply live cgroups v2 slice enforcement & re-exec
        if run_daemon_reexec:
            try:
                subprocess.run(["systemctl", "set-property", "user.slice", f"AllowedCPUs={val_clean}"], capture_output=True, timeout=5)
                subprocess.run(["systemctl", "set-property", "system.slice", f"AllowedCPUs={val_clean}"], capture_output=True, timeout=5)
                subprocess.run(["systemctl", "daemon-reexec"], capture_output=True, timeout=10)
            except Exception as e:
                return False, f"systemctl execution error: {e}"

        self.save_persistent_state()
        return True, f"Successfully applied live and persistent CPU affinity: {val_clean}"

    def load_state(self) -> dict[str, Any]:
        state = {}
        for core in self.p_cores + self.e_cores:
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
        if core_id in self.locked_cores:
            return False, f"CPU {core_id} is locked (BSP) and cannot be toggled", ""

        enable = str(new_value).lower() in ("true", "1", "yes")
        success, msg = set_core_status(core_id, enable)
        if success:
            self.save_persistent_state()
            return True, f"Successfully set CPU {core_id} {'online' if enable else 'offline'}", ""
        else:
            return False, f"Failed to toggle CPU {core_id}: {msg}", ""

    def save_persistent_state(self):
        try:
            home = get_user_home()
            config_dir = home / ".config" / "dusky" / "settings"
            config_dir.mkdir(parents=True, exist_ok=True)
            state_file = config_dir / "dusky_cores"

            # Read current active states of all toggleable cores to save
            cores_state = {}
            for core in self.p_cores + self.e_cores:
                cores_state[f"cpu{core}"] = get_core_status(core)
            cores_state["systemd_cpu_affinity"] = self.get_systemd_affinity()

            import json
            state_file.write_text(json.dumps(cores_state, indent=2))
        except Exception:
            pass

    def restore_state(self) -> bool:
        try:
            home = get_user_home()
            state_file = home / ".config" / "dusky" / "settings" / "dusky_cores"
            if not state_file.exists():
                return False
            import json
            cores_state = json.loads(state_file.read_text())
            for k, v in cores_state.items():
                if k.startswith("cpu") and k[3:].isdigit():
                    core_id = int(k[3:])
                    if core_id not in self.locked_cores:
                        set_core_status(core_id, v)
                elif k == "systemd_cpu_affinity":
                    if v and v != "unset":
                        self.set_systemd_affinity(v)
            return True
        except Exception:
            return False

    def get_telemetry(self) -> str:
        all_cores = self.p_cores + self.e_cores
        online_count = sum(1 for c in all_cores if get_core_status(c))

        # Calculate RAPL power
        pkg_watts = 0.0
        if self.reader:
            curr_e = self.reader.read()
            curr_t = time.perf_counter()
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
        bar_w = 16
        total_cores = len(all_cores)
        filled = max(0, min(bar_w, int((online_count / total_cores) * bar_w))) if total_cores else 0
        bar_graph = "█" * filled + "░" * (bar_w - filled)

        eff_aff = self.get_effective_affinity()
        cfg_aff = self.get_systemd_affinity()
        aff_info = f"Affinity: {cfg_aff} (PID 1: {eff_aff})" if cfg_aff != "unset" else f"Affinity: All ({eff_aff})"

        return f" {online_count}/{total_cores} Cores [{bar_graph}] | {aff_info} | {pkg_watts:4.1f} W"
