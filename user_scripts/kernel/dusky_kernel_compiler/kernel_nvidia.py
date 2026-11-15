"""NVIDIA open-driver compatibility and passive diagnostics; no power-policy writes."""

import hashlib
import os
from pathlib import Path
import shutil
lazy import gzip
lazy import lzma
lazy import subprocess
lazy from compression import zstd

VERSION = "620.70.06"

# --- Linux 7.3: kthreads are isolated in nullfs ---------------------------------
# The open module probes PCI from a dedicated kthread; its unscoped
# filp_open("/sys/.../config") fails with ENOENT unless it uses init's fs.
# Without this repair the driver reports RTD3 unavailable and the GPU never
# reaches runtime D3cold (it stays in D0). This patch is the D3cold/sleep fix.
RTD3_PATCH_NAME = f"nvidia-{VERSION}-linux-7.3-rtd3.patch"
RTD3_FILES = ("kernel-open/nvidia/nv-pci.c",)
ORIGINAL_SHA256 = "ef8315e9a82c601a34c88f975a00381c049480ae35fb17fc6b8d584f59701a3a"
REPAIRED_SHA256 = "a2b63c909b3b150f833c8d1677abe46e68e9d9cd3be1e2736661f2059a4ff7b9"
PM_CALLBACKS_SHA256 = "e744d8d7fec01dda39269241246438e83c32ac0e0bcc09d1b50c9e01fd031304"

# --- Linux 7.4: VM_SPECIAL / is_vm_hugetlb_page removal, drm_fb_helper.h move ----
# Internally version-guarded; a no-op on older trees.
BUILD_PATCH_NAME = f"nvidia-{VERSION}-linux-7.4.patch"
BUILD_FILES = (
    "kernel-open/nvidia-uvm/uvm_hmm.c",
    "kernel-open/nvidia-drm/nvidia-drm-drv.c",
    "kernel-open/conftest.sh",
    "kernel-open/header-presence-tests.mk",
)
BUILD_ORIGINAL_SHA256 = {
    "kernel-open/nvidia-uvm/uvm_hmm.c": "6694801342b8a971ff1e83dbd1996070e92e7e55b12ad32251a656c08c072074",
    "kernel-open/nvidia-drm/nvidia-drm-drv.c": "69998070b00424a3548493b50c68e67717a5322083874361a0abcdd52ec4ba14",
    "kernel-open/conftest.sh": "6518c5a65174adc5bee614794e7ca8cb8f6704a5b0550804f78c002ba250edf6",
    "kernel-open/header-presence-tests.mk": "7353d799e50bfd0dee3724faadb1b19707319c690e437896f7262a2a98f3267a",
}
BUILD_REPAIRED_SHA256 = {
    "kernel-open/nvidia-uvm/uvm_hmm.c": "3f5de38bd8f5a4a3815af75db46f8d3c7f298b0765bc59ea919658af467fa62c",
    "kernel-open/nvidia-drm/nvidia-drm-drv.c": "049e05ec3d564e293cafc154dbbc7efc364bdc54e6e644c2860dc3267ecfbd6c",
    "kernel-open/conftest.sh": "873216c91ad58b5b29af8162b47ae41e7cdf1dc46f3fc68c40425904f6b6f6b0",
    "kernel-open/header-presence-tests.mk": "23603dd5d1a073f87d567ae44b7dc43624fcad6b99d49e5f4a6fdbf873a69721",
}

# DKMS override blocks. Each is independently idempotent so a config written by
# the 7.3-only overlay gains the 7.4 block without duplicating the 7.3 one.
CONFIG_HEADER = "# DKMS override for the tested NVIDIA open driver source version only."
RTD3_CONFIG_BLOCK = f"""# Linux 7.3 isolates kthreads in nullfs; opt in to init's fs for sysfs open.
if [[ -r ${{kernel_source_dir}}/include/linux/fs_struct.h ]] &&
    grep -Fq '#define scoped_with_init_fs()' "${{kernel_source_dir}}/include/linux/fs_struct.h"; then
    case " ${{PATCH[*]}} " in
        *" {RTD3_PATCH_NAME} "*) ;;
        *) PATCH+=("{RTD3_PATCH_NAME}") ;;
    esac
fi"""
BUILD_CONFIG_BLOCK = f"""# Linux 7.4 removed VM_SPECIAL/is_vm_hugetlb_page and moved drm_fb_helper.h.
if [[ -r ${{kernel_source_dir}}/include/linux/mm.h ]] &&
    grep -Fq 'vma_is_mm_backed' "${{kernel_source_dir}}/include/linux/mm.h"; then
    case " ${{PATCH[*]}} " in
        *" {BUILD_PATCH_NAME} "*) ;;
        *) PATCH+=("{BUILD_PATCH_NAME}") ;;
    esac
fi"""

_OVERLAY_CONFIG_BLOCKS = (
    (RTD3_PATCH_NAME, RTD3_CONFIG_BLOCK),
    (BUILD_PATCH_NAME, BUILD_CONFIG_BLOCK),
)


def _overlays() -> tuple[tuple[str, tuple[str, ...], dict[str, str], dict[str, str]], ...]:
    """Resolve the overlay table at call time so tests can patch the hash maps."""
    return (
        (RTD3_PATCH_NAME, RTD3_FILES, {RTD3_FILES[0]: ORIGINAL_SHA256}, {RTD3_FILES[0]: REPAIRED_SHA256}),
        (BUILD_PATCH_NAME, BUILD_FILES, BUILD_ORIGINAL_SHA256, BUILD_REPAIRED_SHA256),
    )


def _overlay_state(source: Path, files: tuple[str, ...], original: dict[str, str], repaired: dict[str, str]) -> str:
    """Classify one overlay's files as 'original' or 'repaired'; raise on drift."""
    states: set[str] = set()
    for rel in files:
        path = source / rel
        if not path.is_file():
            raise ValueError(f"NVIDIA {VERSION} source is missing {rel}; review compat/AGENTS.md")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest == repaired[rel]:
            states.add("repaired")
        elif digest == original[rel]:
            states.add("original")
        else:
            raise ValueError(f"NVIDIA {VERSION} source differs from the tested open driver; "
                             "review compat/AGENTS.md before installing this kernel")
    if len(states) != 1:
        raise ValueError(f"NVIDIA {VERSION} source is partially patched; "
                         "review compat/AGENTS.md before installing this kernel")
    return states.pop()


def repair_files(source: Path, assets: Path, etc: Path) -> dict[Path, tuple[str, str]]:
    """Plan a version/source-specific DKMS overlay without modifying package files."""
    source = Path(source)
    assets = Path(assets)
    etc = Path(etc)
    nv_pci = source / "kernel-open/nvidia/nv-pci.c"
    if not nv_pci.is_file():
        raise ValueError(f"NVIDIA {VERSION} source is missing kernel-open/nvidia/nv-pci.c")
    if hashlib.sha256((nv_pci.parent / "nv.c").read_bytes()).hexdigest() != PM_CALLBACKS_SHA256:
        raise ValueError("NVIDIA runtime PM callbacks differ from the validated source; review compat/AGENTS.md")
    if '"kernel-open"' not in (source / "dkms.conf").read_text(encoding="utf-8"):
        raise ValueError(f"NVIDIA {VERSION} source differs from the tested open driver; "
                         "review compat/AGENTS.md before installing this kernel")

    target_config = etc / f"nvidia-{VERSION}.conf"
    existing = target_config.read_text(encoding="utf-8") if target_config.exists() else ""

    files: dict[Path, tuple[str, str]] = {}
    needed: set[str] = set()
    for patch_name, overlay_files, original, repaired in _overlays():
        state = _overlay_state(source, overlay_files, original, repaired)
        if state == "repaired":
            if patch_name in existing:
                raise ValueError(f"NVIDIA source already contains this fix, but an older overlay is still configured; "
                                 f"remove its {patch_name} directive from {target_config} before rebuilding")
            continue
        needed.add(patch_name)
        patch = (assets / patch_name).read_text(encoding="utf-8")
        target_patch = etc / "nvidia/patches" / patch_name
        if not target_patch.exists() or target_patch.read_text(encoding="utf-8") != patch:
            files[target_patch] = (patch, "0644")

    missing_blocks = [block for name, block in _OVERLAY_CONFIG_BLOCKS if name in needed and block not in existing]
    if missing_blocks:
        addition = (CONFIG_HEADER + "\n" if not existing else "") + "\n".join(missing_blocks)
        files[target_config] = ((existing + "\n" if existing else "") + addition, "0644")
    return files


def target_needs_init_fs(headers: Path) -> bool:
    return "#define scoped_with_init_fs()" in (headers / "include/linux/fs_struct.h").read_text(encoding="utf-8")


def target_needs_build_fix(headers: Path) -> bool:
    return "vma_is_mm_backed" in (headers / "include/linux/mm.h").read_text(encoding="utf-8")


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
