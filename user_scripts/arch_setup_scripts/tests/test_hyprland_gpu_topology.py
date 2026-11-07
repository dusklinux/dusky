#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_hyprland_gpu_topology.py
Standalone empirical stress-test suite for 383_configure_hyprland_gpu.py
Target: Arch Linux | Kernel 7.3+ | Hyprland 0.55+ | Python 3.15+ | Bash 5.3+
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


@dataclass(slots=True)
class MockCard:
    card_idx: int
    pci_bdf: str  # e.g. "0000:00:02.0" or "platform:soc:gpu"
    vendor_id: str
    device_id: str
    driver: str
    boot_vga: int = 0
    connectors: dict[str, str] = field(default_factory=dict)  # {"eDP-1": "connected"}
    virtio_bus: str | None = None  # e.g. "virtio0" intermediate bus node
    by_path_override: str | None = None


@dataclass(slots=True)
class TopologyCase:
    name: str
    cards: list[MockCard]
    modeset_param: str | None = "Y"
    cmdline: str = "quiet rw"
    nvidia_version: str = "570.124.04"
    vaapi_drivers: tuple[str, ...] = ("iHD", "radeonsi", "nvidia")
    reboot_swap_map: dict[int, int] = field(default_factory=dict)
    expected_primary_node: str = "/dev/dri/card0"
    expected_aq_drm: str = "/dev/dri/card0"
    expected_swapped_aq_drm: str | None = None
    expected_env: dict[str, str] = field(default_factory=dict)
    forbidden_env: tuple[str, ...] = ()
    expected_modeset_ok: bool = True


def load_target_module(script_path: Path) -> ModuleType:
    if not script_path.exists():
        console.print(f"[bold red]Target script not found:[/] {script_path}")
        raise SystemExit(1)
    spec = importlib.util.spec_from_file_location("hypr_gpu_cfg", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hypr_gpu_cfg"] = mod
    spec.loader.exec_module(mod)
    return mod


def ensure_lua_bin() -> str:
    for candidate in ("lua", "luajit", "lua5.4", "lua5.1"):
        if path := shutil.which(candidate):
            return path
    console.print("[yellow]No Lua interpreter found; installing 'lua' via pacman...[/]")
    subprocess.run(["sudo", "pacman", "-S", "--needed", "--noconfirm", "lua"], check=True)
    path = shutil.which("lua")
    if not path:
        raise RuntimeError("Lua interpreter unavailable after installation")
    return path


def build_sandbox(root: Path, case: TopologyCase) -> None:
    """Populate a realistic /sys, /dev/dri, /proc, and /usr/lib hierarchy inside `root`."""
    drm_class = root / "sys/class/drm"
    pci_bus = root / "sys/bus/pci"
    pci_devs = pci_bus / "devices"
    pci_drvs = pci_bus / "drivers"
    plat_bus = root / "sys/bus/platform"
    plat_devs = plat_bus / "devices"
    plat_drvs = plat_bus / "drivers"
    dev_dri = root / "dev/dri"
    by_path = dev_dri / "by-path"
    dri_lib = root / "usr/lib/dri"
    gbm_lib = root / "usr/lib/gbm"

    for d in (drm_class, pci_devs, pci_drvs, plat_devs, plat_drvs, by_path, dri_lib, gbm_lib):
        d.mkdir(parents=True, exist_ok=True)

    for va in case.vaapi_drivers:
        (dri_lib / f"{va}_drv_video.so").write_bytes(b"")
    if "nvidia" in case.vaapi_drivers:
        (gbm_lib / "nvidia-drm_gbm.so").write_bytes(b"")

    (root / "proc").mkdir(parents=True, exist_ok=True)
    (root / "proc/cmdline").write_text(case.cmdline + "\n", encoding="utf-8")
    (root / "etc/modprobe.d").mkdir(parents=True, exist_ok=True)
    (root / "usr/lib/modprobe.d").mkdir(parents=True, exist_ok=True)

    if case.modeset_param is not None:
        ms_dir = root / "sys/module/nvidia_drm/parameters"
        ms_dir.mkdir(parents=True, exist_ok=True)
        (ms_dir / "modeset").write_text(case.modeset_param + "\n", encoding="utf-8")

    nv_dir = root / "sys/module/nvidia"
    nv_dir.mkdir(parents=True, exist_ok=True)
    (nv_dir / "version").write_text(case.nvidia_version + "\n", encoding="utf-8")

    for c in case.cards:
        card_name = f"card{c.card_idx}"
        (dev_dri / card_name).write_text("", encoding="utf-8")

        if c.pci_bdf.startswith("platform:"):
            plat_name = c.pci_bdf.removeprefix("platform:")
            dev_dir = plat_devs / plat_name
            dev_dir.mkdir(parents=True, exist_ok=True)
            (dev_dir / "subsystem").symlink_to(plat_bus)
            drv_dir = plat_drvs / c.driver
            drv_dir.mkdir(parents=True, exist_ok=True)
            (dev_dir / "driver").symlink_to(drv_dir)
            (dev_dir / "uevent").write_text(f"OF_NAME={plat_name}\n", encoding="utf-8")
            sys_dev_target = dev_dir
            bp_name = c.by_path_override or f"platform-{plat_name}-card"
        else:
            pci_dir = pci_devs / c.pci_bdf
            pci_dir.mkdir(parents=True, exist_ok=True)
            (pci_dir / "subsystem").symlink_to(pci_bus)
            (pci_dir / "vendor").write_text(c.vendor_id + "\n", encoding="utf-8")
            (pci_dir / "device").write_text(c.device_id + "\n", encoding="utf-8")
            (pci_dir / "boot_vga").write_text(f"{c.boot_vga}\n", encoding="utf-8")
            drv_dir = pci_drvs / c.driver
            drv_dir.mkdir(parents=True, exist_ok=True)
            (pci_dir / "driver").symlink_to(drv_dir)

            if c.virtio_bus:
                virt_dir = pci_dir / c.virtio_bus
                virt_dir.mkdir(parents=True, exist_ok=True)
                (virt_dir / "driver").symlink_to(drv_dir)
                sys_dev_target = virt_dir
            else:
                sys_dev_target = pci_dir
            bp_name = c.by_path_override or f"pci-{c.pci_bdf}-card"

        card_sys = drm_class / card_name
        card_sys.mkdir(parents=True, exist_ok=True)
        (card_sys / "device").symlink_to(sys_dev_target)
        (by_path / bp_name).symlink_to(f"../{card_name}")

        for conn_suffix, status in c.connectors.items():
            conn_dir = drm_class / f"{card_name}-{conn_suffix}"
            conn_dir.mkdir(parents=True, exist_ok=True)
            if not conn_suffix.startswith("Writeback"):
                (conn_dir / "status").write_text(status + "\n", encoding="utf-8")


def eval_lua_config(lua_bin: str, lua_code: str, sandbox_dev_dri: Path) -> dict[str, str]:
    """Execute generated `gpu.lua` in Lua with `hl.env` mocked and `/dev/dri` redirected to sandbox."""
    harness = f"""
local captured_env = {{}}
hl = {{
  env = function(k, v)
    captured_env[k] = tostring(v)
  end
}}
local orig_popen = io.popen
local sandbox_dri = {json.dumps(str(sandbox_dev_dri))}
local real_root = {json.dumps(str(sandbox_dev_dri.parent.parent))}
io.popen = function(cmd)
  local rewritten = cmd:gsub("/dev/dri", sandbox_dri)
  local h = orig_popen(rewritten)
  if not h then return nil end
  return {{
    read = function(_, fmt)
      local out = h:read(fmt)
      if out then
        out = out:gsub("^" .. real_root, "")
      end
      return out
    end,
    close = function(_)
      return h:close()
    end
  }}
end

{lua_code}

for k, v in pairs(captured_env) do
  print(k .. "=" .. v)
end
"""
    res = subprocess.run([lua_bin, "-e", harness], capture_output=True, text=True, check=True, timeout=5)
    env_out: dict[str, str] = {}
    for line in res.stdout.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env_out[k] = v
    return env_out


def run_case(mod: ModuleType, lua_bin: str, case: TopologyCase) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="hypr_gpu_test_") as tmp:
        root = Path(tmp).resolve()
        build_sandbox(root, case)

        redirect_prefixes = (
            "/sys",
            "/dev/dri",
            "/proc/cmdline",
            "/etc/modprobe.d",
            "/usr/lib/modprobe.d",
            "/usr/lib/dri",
            "/usr/lib64/dri",
            "/usr/lib/gbm",
            "/usr/lib64/gbm",
        )

        def sandboxed_path(p: str | os.PathLike[str]) -> Path:
            s = str(p)
            if any(re.prefixmatch(re.escape(pfx) + r"(?:/|$)", s) for pfx in redirect_prefixes):
                return root / s.lstrip("/")
            return Path(s)

        orig_path = mod.Path
        orig_pyudev = mod.pyudev
        orig_dri_dirs = mod.DRI_DIRS
        orig_gbm_dirs = mod.GBM_DIRS
        try:
            mod.Path = sandboxed_path
            mod.pyudev = None
            mod.DRI_DIRS = (root / "usr/lib/dri",)
            mod.GBM_DIRS = (root / "usr/lib/gbm",)

            cards = mod.detect()
            for c in cards:
                if c.by_path.startswith(str(root)):
                    c.by_path = c.by_path.removeprefix(str(root))

            display_cards = [c for c in cards if c.total_outputs > 0] or cards
            non_bmc = [c for c in display_cards if not c.is_bmc]
            if non_bmc:
                display_cards = non_bmc

            primary, _reason = mod.default_gpu(display_cards)
            secondary = sorted(
                [c for c in display_cards if c.dev_node != primary.dev_node],
                key=lambda c: (-c.connected_outputs, -int(c.has_internal_panel), -c.boot_vga, mod.pci_sort_key(c)),
            )
            ordered = [primary, *secondary]
            lua_code = mod.gen_lua(primary, ordered, "auto")
            modeset_ok = mod.nvidia_modeset_confirmed(cards)

            out_lua = root / "config/hypr/gpu.lua"
            hypr_lua = root / "config/hypr/hyprland.lua"
            hypr_lua.parent.mkdir(parents=True, exist_ok=True)
            hypr_lua.write_text("-- main config\n", encoding="utf-8")
            mod.atomic_write(out_lua, lua_code)
            mod.wire_gpu_require(out_lua)
            assert 'pcall(require, "gpu")' in hypr_lua.read_text(encoding="utf-8"), "wire_gpu_require failed"
        finally:
            mod.Path = orig_path
            mod.pyudev = orig_pyudev
            mod.DRI_DIRS = orig_dri_dirs
            mod.GBM_DIRS = orig_gbm_dirs

        if primary.dev_node != case.expected_primary_node:
            return False, f"Primary mismatch: got {primary.dev_node}, expected {case.expected_primary_node}"

        if modeset_ok != case.expected_modeset_ok:
            return False, f"modeset check mismatch: got {modeset_ok}, expected {case.expected_modeset_ok}"

        env_vars = eval_lua_config(lua_bin, lua_code, root / "dev/dri")
        aq_actual = env_vars.get("AQ_DRM_DEVICES", "")
        if aq_actual != case.expected_aq_drm:
            return False, f"AQ_DRM_DEVICES mismatch: got '{aq_actual}', expected '{case.expected_aq_drm}'"

        for k, v in case.expected_env.items():
            if env_vars.get(k) != v:
                return False, f"Env {k} mismatch: got {env_vars.get(k)!r}, expected {v!r}"

        for k in case.forbidden_env:
            if k in env_vars:
                return False, f"Forbidden env {k}={env_vars[k]!r} leaked into gpu.lua"

        # Simulate reboot where kernel swaps /dev/dri/cardN numbers
        if case.reboot_swap_map and case.expected_swapped_aq_drm:
            by_path_dir = root / "dev/dri/by-path"
            for link in by_path_dir.iterdir():
                if link.is_symlink():
                    target_name = os.readlink(link)
                    m = re.search(r"card(\d+)$", target_name)
                    if m and int(m.group(1)) in case.reboot_swap_map:
                        new_idx = case.reboot_swap_map[int(m.group(1))]
                        link.unlink()
                        link.symlink_to(f"../card{new_idx}")
                        (root / f"dev/dri/card{new_idx}").write_text("", encoding="utf-8")

            swapped_env = eval_lua_config(lua_bin, lua_code, root / "dev/dri")
            swapped_aq = swapped_env.get("AQ_DRM_DEVICES", "")
            if swapped_aq != case.expected_swapped_aq_drm:
                return (
                    False,
                    f"Reboot card-swap resolution failed: got '{swapped_aq}', expected '{case.expected_swapped_aq_drm}'",
                )

        return True, f"Primary={primary.dev_node} | AQ_DRM_DEVICES={aq_actual}"


def build_test_suite() -> list[TopologyCase]:
    return [
        TopologyCase(
            name="1. Hybrid Laptop: Intel Alder Lake-P iGPU (eDP) + NVIDIA RTX dGPU (D3cold)",
            cards=[
                MockCard(
                    0,
                    "0000:00:02.0",
                    "0x8086",
                    "0x46a6",
                    "i915",
                    boot_vga=1,
                    connectors={"eDP-1": "connected", "HDMI-A-1": "disconnected", "Writeback-1": "unknown"},
                ),
                MockCard(
                    1,
                    "0000:01:00.0",
                    "0x10de",
                    "0x25a0",
                    "nvidia",
                    boot_vga=0,
                    connectors={f"DP-{i}": "disconnected" for i in range(1, 5)},
                ),
            ],
            reboot_swap_map={0: 1, 1: 0},
            expected_primary_node="/dev/dri/card0",
            expected_aq_drm="/dev/dri/card0:/dev/dri/card1",
            expected_swapped_aq_drm="/dev/dri/card1:/dev/dri/card0",
            forbidden_env=("GBM_BACKEND", "__GLX_VENDOR_LIBRARY_NAME", "LIBVA_DRIVER_NAME", "NVD_BACKEND"),
            expected_modeset_ok=True,
        ),
        TopologyCase(
            name="2. Hybrid Laptop: AMD Ryzen iGPU (high PCI c4:00.0, eDP) + NVIDIA dGPU (01:00.0)",
            cards=[
                MockCard(
                    0,
                    "0000:01:00.0",
                    "0x10de",
                    "0x2860",
                    "nvidia",
                    boot_vga=0,
                    connectors={"HDMI-A-1": "disconnected", "DP-1": "disconnected"},
                ),
                MockCard(
                    1,
                    "0000:c4:00.0",
                    "0x1002",
                    "0x15bf",
                    "amdgpu",
                    boot_vga=1,
                    connectors={"eDP-1": "connected", "DP-2": "disconnected"},
                ),
            ],
            reboot_swap_map={0: 1, 1: 0},
            expected_primary_node="/dev/dri/card1",
            expected_aq_drm="/dev/dri/card1:/dev/dri/card0",
            expected_swapped_aq_drm="/dev/dri/card0:/dev/dri/card1",
            forbidden_env=("GBM_BACKEND", "__GLX_VENDOR_LIBRARY_NAME", "LIBVA_DRIVER_NAME", "NVD_BACKEND"),
            expected_modeset_ok=True,
        ),
        TopologyCase(
            name="3. Desktop Single-GPU: NVIDIA RTX (modeset=1, 2 DP monitors connected)",
            cards=[
                MockCard(
                    0,
                    "0000:01:00.0",
                    "0x10de",
                    "0x2684",
                    "nvidia",
                    boot_vga=1,
                    connectors={"DP-1": "connected", "DP-2": "connected", "HDMI-A-1": "disconnected"},
                ),
            ],
            expected_primary_node="/dev/dri/card0",
            expected_aq_drm="/dev/dri/card0",
            expected_env={
                "GBM_BACKEND": "nvidia-drm",
                "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
                "NVD_BACKEND": "direct",
            },
            forbidden_env=("LIBVA_DRIVER_NAME",),
            expected_modeset_ok=True,
        ),
        TopologyCase(
            name="4. Desktop Multi-GPU: AMD Radeon (HDMI, boot_vga) + Intel Arc (DP)",
            cards=[
                MockCard(
                    0,
                    "0000:03:00.0",
                    "0x1002",
                    "0x744c",
                    "amdgpu",
                    boot_vga=1,
                    connectors={"HDMI-A-1": "connected", "DP-1": "disconnected"},
                ),
                MockCard(
                    1,
                    "0000:06:00.0",
                    "0x8086",
                    "0x56a0",
                    "xe",
                    boot_vga=0,
                    connectors={"DP-2": "connected"},
                ),
            ],
            expected_primary_node="/dev/dri/card0",
            expected_aq_drm="/dev/dri/card0:/dev/dri/card1",
            forbidden_env=("GBM_BACKEND", "__GLX_VENDOR_LIBRARY_NAME", "LIBVA_DRIVER_NAME"),
            expected_modeset_ok=True,
        ),
        TopologyCase(
            name="5. VM / KVM: virtio-gpu-pci (intermediate virtio0 bus) + Platform SoC GPU",
            cards=[
                MockCard(
                    0,
                    "0000:00:02.0",
                    "0x1af4",
                    "0x1050",
                    "virtio_gpu",
                    boot_vga=1,
                    virtio_bus="virtio0",
                    connectors={"Virtual-1": "connected"},
                ),
                MockCard(
                    1,
                    "platform:soc:gpu",
                    "0x0000",
                    "0x0000",
                    "panfrost",
                    boot_vga=0,
                    connectors={"HDMI-A-1": "disconnected"},
                ),
            ],
            expected_primary_node="/dev/dri/card0",
            expected_aq_drm="/dev/dri/card0:/dev/dri/card1",
            forbidden_env=("GBM_BACKEND", "__GLX_VENDOR_LIBRARY_NAME", "LIBVA_DRIVER_NAME"),
            expected_modeset_ok=True,
        ),
        TopologyCase(
            name="6. Server BMC + Compute-Only: Matrox mgag200 BMC + NVIDIA H100 (0 outputs) + AMD Pro",
            cards=[
                MockCard(
                    0,
                    "0000:07:00.0",
                    "0x102b",
                    "0x0538",
                    "mgag200",
                    boot_vga=1,
                    connectors={"VGA-1": "connected"},
                ),
                MockCard(
                    1,
                    "0000:41:00.0",
                    "0x10de",
                    "0x2330",
                    "nvidia",
                    boot_vga=0,
                    connectors={},  # 0 outputs (compute accelerator)
                ),
                MockCard(
                    2,
                    "0000:81:00.0",
                    "0x1002",
                    "0x7448",
                    "amdgpu",
                    boot_vga=0,
                    connectors={"DP-1": "connected"},
                ),
            ],
            expected_primary_node="/dev/dri/card2",
            expected_aq_drm="/dev/dri/card2",
            forbidden_env=("GBM_BACKEND", "__GLX_VENDOR_LIBRARY_NAME", "LIBVA_DRIVER_NAME"),
            expected_modeset_ok=True,
        ),
        TopologyCase(
            name="7. Edge Case: NVIDIA RTX with explicit nvidia_drm.modeset=0",
            cards=[
                MockCard(
                    0,
                    "0000:01:00.0",
                    "0x10de",
                    "0x2684",
                    "nvidia",
                    boot_vga=1,
                    connectors={"DP-1": "connected"},
                ),
            ],
            modeset_param="N",
            cmdline="quiet rw nvidia_drm.modeset=0",
            expected_modeset_ok=False,
            nvidia_version="570.124.04",
            expected_primary_node="/dev/dri/card0",
            expected_aq_drm="/dev/dri/card0",
            expected_env={
                "GBM_BACKEND": "nvidia-drm",
                "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
                "NVD_BACKEND": "direct",
            },
        ),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="Stress-test 383_configure_hyprland_gpu.py across 7 topologies")
    ap.add_argument(
        "--script",
        type=Path,
        default=Path(__file__).resolve().parent / "383_configure_hyprland_gpu.py",
        help="Path to 383_configure_hyprland_gpu.py",
    )
    args = ap.parse_args()

    mod = load_target_module(args.script)
    lua_bin = ensure_lua_bin()

    tbl = Table(title="Hyprland GPU Topology Stress-Test Suite", header_style="bold cyan")
    tbl.add_column("Scenario", style="bold")
    tbl.add_column("Status")
    tbl.add_column("Details")

    all_passed = True
    for case in build_test_suite():
        ok, detail = run_case(mod, lua_bin, case)
        all_passed = all_passed and ok
        status_str = "[bold green]PASS[/]" if ok else "[bold red]FAIL[/]"
        tbl.add_row(case.name, status_str, detail)

    console.print(tbl)
    if not all_passed:
        console.print(Panel.fit("[bold red]Some topology verification checks failed![/]", style="red"))
        return 1

    console.print(Panel.fit("[bold green]All 7 GPU topologies & Lua executions verified (exit 0)[/]", style="green"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())