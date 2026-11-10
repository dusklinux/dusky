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

# --- Linux 7.3: kthreads are isolated in nullfs ---------------------------------
# The open module probes PCI from a dedicated kthread; its unscoped
# filp_open("/sys/.../config") fails with ENOENT unless it uses init's fs.
# Without this repair the driver reports RTD3 unavailable and the GPU never
# reaches runtime D3cold (it stays in D0). This patch is the D3cold/sleep fix.
RTD3_PATCH_NAME = f"nvidia-{VERSION}-linux-7.3-rtd3.patch"
RTD3_FILES = ("kernel-open/nvidia/nv-pci.c",)
ORIGINAL_SHA256 = "6a5e39f37d86bd6d09cc5867647ecbc8e240298bc138bf651ad7d9346bc1b1c8"
REPAIRED_SHA256 = "0afbf2ed8a66c9eed39bb892a8d41844afb45f0fdc612a297327dad402f6a9f5"
PM_CALLBACKS_SHA256 = "ce04946d288cf0251668a2d847acceb2a39af9dac2f13bb005f53759c774dbf7"

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
    "kernel-open/nvidia-uvm/uvm_hmm.c": "aef3e216ca94a5de98062e1360aa8b101c8c74fe659950a9e6b0d9a32f431bf4",
    "kernel-open/nvidia-drm/nvidia-drm-drv.c": "e8af7b5286eb3a8cfaee5f9d161d1e5474aba0b9c9ba49199e6ab72ba524349a",
    "kernel-open/conftest.sh": "a03e67ecc8e3ec11814b233435a6940f3a906bd8585dc9986ac2a9d812b82d15",
    "kernel-open/header-presence-tests.mk": "013d9c923f550389940fb4822bbe124e106340aa02be4f28ba58c195c32c1c45",
}
BUILD_REPAIRED_SHA256 = {
    "kernel-open/nvidia-uvm/uvm_hmm.c": "f833fa339d26ee16d22eb7224980fe4078131aeda3ca08e709cd1ffea6319324",
    "kernel-open/nvidia-drm/nvidia-drm-drv.c": "bbd2320b926a6ed5c346d1eac992bb3270830335586995700cbdb9a56b8c3c11",
    "kernel-open/conftest.sh": "a156f277a60992d055ceadd5d3769961c8c20c970d27da91b4f0326e93fff456",
    "kernel-open/header-presence-tests.mk": "f0008198dbd2f5416fdf1d96f31dd625158f62a41fff2c928a492d9a2873c841",
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
