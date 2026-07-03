#!/usr/bin/env python3
import os
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
    def __init__(self, config_path: str = ""):
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

    def load_state(self) -> dict[str, Any]:
        state = {}
        for core in self.p_cores + self.e_cores:
            status = get_core_status(core)
            state[f"cpu{core}"] = status
            state[f"DEFAULT/cpu{core}"] = status
        return state

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        if not target_key.startswith("cpu") or not target_key[3:].isdigit():
            return False, f"Invalid key: {target_key}", ""

        core_id = int(target_key[3:])
        if core_id in self.locked_cores:
            return False, f"CPU {core_id} is locked (BSP) and cannot be toggled", ""

        enable = new_value.lower() in ("true", "1", "yes")
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
        bar_w = 20
        total_cores = len(all_cores)
        filled = max(0, min(bar_w, int((online_count / total_cores) * bar_w)))
        bar_graph = "█" * filled + "░" * (bar_w - filled)

        return f"⚡ Active: {online_count}/{total_cores} Cores  [{bar_graph}]  Power: {pkg_watts:5.1f} W"
