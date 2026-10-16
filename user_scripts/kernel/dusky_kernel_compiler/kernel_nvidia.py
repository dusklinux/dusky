"""NVIDIA open-driver compatibility and passive diagnostics; no power-policy writes."""

import hashlib
import os
from pathlib import Path
import shutil
lazy import gzip
lazy import lzma
lazy import subprocess
lazy from compression import zstd

VERSION = "615.78.08"
PATCH_NAME = f"nvidia-{VERSION}-linux-7.3-rtd3.patch"
ORIGINAL_SHA256 = "6a5e39f37d86bd6d09cc5867647ecbc8e240298bc138bf651ad7d9346bc1b1c8"
REPAIRED_SHA256 = "0afbf2ed8a66c9eed39bb892a8d41844afb45f0fdc612a297327dad402f6a9f5"
PM_CALLBACKS_SHA256 = "ce04946d288cf0251668a2d847acceb2a39af9dac2f13bb005f53759c774dbf7"


def repair_files(source: Path, assets: Path, etc: Path) -> dict[Path, tuple[str, str]]:
    """Plan a version/source-specific DKMS overlay without modifying package files."""
    nv_pci = source / "kernel-open/nvidia/nv-pci.c"
    digest = hashlib.sha256(nv_pci.read_bytes()).hexdigest()
    if hashlib.sha256((nv_pci.parent / "nv.c").read_bytes()).hexdigest() != PM_CALLBACKS_SHA256:
        raise ValueError("NVIDIA runtime PM callbacks differ from the validated source; review compat/AGENTS.md")
    target_config = etc / f"nvidia-{VERSION}.conf"
    existing = target_config.read_text(encoding="utf-8") if target_config.exists() else ""
    if digest == REPAIRED_SHA256:
        if PATCH_NAME in existing:
            raise ValueError("NVIDIA source already contains this fix, but an older RTD3 overlay is still configured; "
                             f"remove its {PATCH_NAME} directive from {target_config} before rebuilding")
        return {}
    if digest != ORIGINAL_SHA256 or '"kernel-open"' not in (source / "dkms.conf").read_text(encoding="utf-8"):
        raise ValueError(f"NVIDIA {VERSION} source differs from the tested open driver; "
                         "review compat/AGENTS.md before installing this kernel")
    patch = (assets / PATCH_NAME).read_text(encoding="utf-8")
    config = (assets / f"nvidia-{VERSION}.conf").read_text(encoding="utf-8")
    target_patch = etc / "nvidia/patches" / PATCH_NAME
    files = {}
    if not target_patch.exists() or target_patch.read_text(encoding="utf-8") != patch:
        files[target_patch] = (patch, "0644")
    if config not in existing:
        # Retain local MAKE/PATCH directives; the appended block deduplicates itself.
        files[target_config] = (existing + "\n" + config if existing else config, "0644")
    return files


def target_needs_init_fs(headers: Path) -> bool:
    return "#define scoped_with_init_fs()" in (headers / "include/linux/fs_struct.h").read_text(encoding="utf-8")


def module_uses_init_fs(module: Path) -> bool:
    """Check the actual ELF undefined symbols, decoding compressed modules in RAM."""
    nm = shutil.which("nm") or shutil.which("llvm-nm")
    if not nm:
        raise ValueError("nm or llvm-nm is required to verify the NVIDIA compatibility fix")
    match module.suffix:
        case ".zst": opener = zstd.open
        case ".xz": opener = lzma.open
        case ".gz": opener = gzip.open
        case _: opener = open
    # memfd avoids a disk-sized temporary copy and works without a RAM mount.
    with os.fdopen(os.memfd_create("dusky-nvidia-check", os.MFD_CLOEXEC), "w+b") as decoded:
        try:
            with opener(module, "rb") as stream:
                shutil.copyfileobj(stream, decoded)
        except (EOFError, lzma.LZMAError, zstd.ZstdError) as exc:
            raise ValueError(f"Cannot decode NVIDIA module {module}: {exc}") from exc
        decoded.flush()
        cp = subprocess.run([nm, "--undefined-only", f"/proc/self/fd/{decoded.fileno()}"],
                            pass_fds=(decoded.fileno(),), capture_output=True, text=True,
                            check=True, timeout=30)
    return any(line.split() == ["U", "userspace_init_fs"] for line in cp.stdout.splitlines())


def power_status(pci: Path = Path("/sys/bus/pci/devices"),
                 proc: Path = Path("/proc/driver/nvidia/gpus")) -> list[dict[str, str]]:
    """Read software-maintained PM attributes only; never query NVML or PCI config."""
    def read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return "unavailable"

    result = []
    for device in sorted(pci.glob("*")):
        if read(device / "vendor") != "0x10de" or not read(device / "class").startswith("0x03"):
            continue
        driver = device / "driver"
        result.append({"pci": device.name, "driver": driver.resolve().name if driver.is_symlink() else "unbound",
                       "power_state": read(device / "power_state"),
                       "runtime_status": read(device / "power/runtime_status"),
                       "control": read(device / "power/control"),
                       "nvidia_power": read(proc / device.name / "power")})
    return result
