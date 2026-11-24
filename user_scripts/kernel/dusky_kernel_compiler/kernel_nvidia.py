"""NVIDIA open-driver compatibility and passive diagnostics; no power-policy writes."""

import hashlib
import os
from pathlib import Path
lazy import gzip
lazy import lzma
lazy import shutil
lazy import subprocess
lazy from compression import zstd

VERSION = "620.73.04"

# --- Linux 7.3+: kthreads are isolated in nullfs --------------------------------
# The open module probes PCI from a dedicated kthread; its unscoped
# filp_open("/sys/.../config") fails with ENOENT unless it uses init's fs.
# Without this repair the driver reports RTD3 unavailable and the GPU never
# reaches runtime D3cold (it stays in D0). This patch is the D3cold/sleep fix.
RTD3_PATCH_NAME = f"nvidia-{VERSION}-linux-7.3-rtd3.patch"
ORIGINAL_SHA256 = "ef8315e9a82c601a34c88f975a00381c049480ae35fb17fc6b8d584f59701a3a"
REPAIRED_SHA256 = "a2b63c909b3b150f833c8d1677abe46e68e9d9cd3be1e2736661f2059a4ff7b9"
PM_CALLBACKS_SHA256 = "e744d8d7fec01dda39269241246438e83c32ac0e0bcc09d1b50c9e01fd031304"

CONFIG_HEADER = "# DKMS override for the tested NVIDIA open driver source version only."
RTD3_CONFIG_BLOCK = f"""# Linux 7.3 isolates kthreads in nullfs; opt in to init's fs for sysfs open.
if [[ -r ${{kernel_source_dir}}/include/linux/fs_struct.h ]] &&
    grep -Fq '#define scoped_with_init_fs()' "${{kernel_source_dir}}/include/linux/fs_struct.h"; then
    case " ${{PATCH[*]}} " in
        *" {RTD3_PATCH_NAME} "*) ;;
        *) PATCH+=("{RTD3_PATCH_NAME}") ;;
    esac
fi"""


def repair_files(source: Path, assets: Path, etc: Path) -> dict[Path, tuple[str, str]]:
    """Plan a version/source-specific DKMS overlay without modifying package files."""
    source = Path(source)
    assets = Path(assets)
    etc = Path(etc)

    nv_pci = source / "kernel-open/nvidia/nv-pci.c"
    if not nv_pci.is_file():
        raise ValueError(f"NVIDIA {VERSION} source is missing kernel-open/nvidia/nv-pci.c; review compat/AGENTS.md")
    nv_c = nv_pci.parent / "nv.c"
    if not nv_c.is_file():
        raise ValueError(f"NVIDIA {VERSION} source is missing kernel-open/nvidia/nv.c; review compat/AGENTS.md")
    dkms_conf = source / "dkms.conf"
    if not dkms_conf.is_file():
        raise ValueError(f"NVIDIA {VERSION} source is missing dkms.conf; review compat/AGENTS.md")

    if hashlib.sha256(nv_c.read_bytes()).hexdigest() != PM_CALLBACKS_SHA256:
        raise ValueError("NVIDIA runtime PM callbacks differ from the validated source; review compat/AGENTS.md")
    if '"kernel-open"' not in dkms_conf.read_text(encoding="utf-8"):
        raise ValueError(f"NVIDIA {VERSION} source differs from the tested open driver; "
                         "review compat/AGENTS.md before installing this kernel")

    target_config = etc / f"nvidia-{VERSION}.conf"
    existing = target_config.read_text(encoding="utf-8") if target_config.exists() else ""

    digest = hashlib.sha256(nv_pci.read_bytes()).hexdigest()
    if digest == REPAIRED_SHA256:
        if RTD3_PATCH_NAME in existing:
            raise ValueError(f"NVIDIA source already contains this fix, but an older overlay is still configured; "
                             f"remove its {RTD3_PATCH_NAME} directive from {target_config} before rebuilding")
        return {}
    if digest != ORIGINAL_SHA256:
        raise ValueError(f"NVIDIA {VERSION} source differs from the tested open driver; "
                         "review compat/AGENTS.md before installing this kernel")

    files: dict[Path, tuple[str, str]] = {}
    patch = (assets / RTD3_PATCH_NAME).read_text(encoding="utf-8")
    target_patch = etc / "nvidia/patches" / RTD3_PATCH_NAME
    if not target_patch.exists() or target_patch.read_text(encoding="utf-8") != patch:
        files[target_patch] = (patch, "0644")

    if RTD3_CONFIG_BLOCK not in existing:
        if existing:
            prefix = existing if existing.endswith("\n") else existing + "\n"
            content = prefix + RTD3_CONFIG_BLOCK + "\n"
        else:
            content = CONFIG_HEADER + "\n" + RTD3_CONFIG_BLOCK + "\n"
        files[target_config] = (content, "0644")
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
