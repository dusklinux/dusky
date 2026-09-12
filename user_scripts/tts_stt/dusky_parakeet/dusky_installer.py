#!/usr/bin/env python3
"""Atomic Arch Linux installer for Dusky STT (hardware-agnostic, bleeding-edge).

Targets Arch Linux rolling (kernel 6.10+, CPython 3.14.6+ GIL, uv).
Hardware is explicit and auto-detected -- no hardcoded users or machines:

  --hardware auto    (default) detect nvidia > amd > cpu
  --hardware nvidia  NVIDIA dGPU via onnxruntime-gpu + CUDA 13 (D3cold capable)
  --hardware amd     AMD GPU via Arch's python-onnxruntime-rocm ([extra],
                     exposed through a system-site-packages worker venv)
                     when a usable ROCm stack is present (see detect_rocm);
                     otherwise CPU onnxruntime (reliable fallback)
  --hardware cpu     CPU-only, always works

Layout (username-agnostic, all under $HOME):
  APP_DIR = ~/.local/lib/dusky-stt
    .venv-main    CPU onnxruntime + numpy + sounddevice (daemon, never CUDA)
    .venv-worker  nvidia: onnxruntime-gpu + CUDA 13 + onnx-asr --no-deps
                  amd+ROCm: system onnxruntime-rocm via --system-site-packages
                            + onnx-asr --no-deps
                  cpu/amd (no ROCm): onnxruntime + onnx-asr (CPU, always works)
"""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

MIN_PYTHON = (3, 14, 6)
MIN_KERNEL = (6, 10)
MIN_DRIVER_MAJOR = 580
SCHEMA_VERSION = 2

APP_DIR = Path.home() / ".local" / "lib" / "dusky-stt"
BIN_DIR = Path.home() / ".local" / "bin"
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
UNIT_NAME = "dusky_stt.service"
DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "dusky-stt"
DEFAULT_MODEL_ROOT = Path.home() / ".local" / "share" / "dusky-stt" / "models"

SOURCE_DIR = Path(__file__).resolve().parent
REQUIRED_SOURCES = ("dusky_main.py", "dusky_worker.py", "dusky_trigger.py", "dusky_rec_indicator.py", "dusky_verify.sh", UNIT_NAME)

BASE_PACKAGES = (
    "pipewire",
    "pipewire-audio",
    "pipewire-alsa",
    "pipewire-pulse",
    "wireplumber",
    "portaudio",
    "ffmpeg",
    "wtype",
    "wl-clipboard",
    "libnotify",
    "uv",
)
NVIDIA_PACKAGES = ("nvidia-utils",)

MAIN_PACKAGES = ("onnxruntime==1.29.0", "numpy==2.5.2", "sounddevice==0.5.6")

# CUDA 13.0.x is deliberately HELD (not bumped to 13.3.x): 13.3 needs
# driver >= 610.43 per NVIDIA release notes, which would brick every
# 580-609 user this installer explicitly accepts (MIN_DRIVER_MAJOR=580).
# ORT 1.29 + CUDA 13.0 runtime remain ABI-compatible (SONAME .so.13).
WORKER_CUDA_PACKAGES = (
    "nvidia-cuda-runtime==13.0.88",
    "nvidia-cublas==13.0.2.14",
    "nvidia-cudnn-cu13==9.13.1.26",
    "nvidia-cuda-nvrtc==13.0.88",
    "nvidia-cufft==12.0.0.15",
    "nvidia-curand==10.4.0.35",
    "nvidia-nvjitlink==13.0.88",
)
WORKER_NVIDIA_PACKAGES = ("onnxruntime-gpu==1.29.0", "numpy==2.5.2", "huggingface-hub>=0.34")
WORKER_NVIDIA_NO_DEPS = ("onnx-asr==0.12.0",)
WORKER_CPU_PACKAGES = ("onnxruntime==1.29.0", "numpy==2.5.2", "huggingface-hub>=0.34", "onnx-asr==0.12.0")

# AMD path: Arch's own python-onnxruntime-rocm from [extra], exposed to the
# worker through a --system-site-packages venv -- the same source Kokoro TTS
# calls "--rocm-source arch". AMD's flat wheel index (repo.radeon.com) was
# audited and deliberately NOT used: it stops at ROCm 7.0 / cp312 wheels
# while Arch ships ROCm 7.2 / CPython 3.14, so it cannot serve current
# systems. The [extra] package is built against exactly Arch's python and
# ROCm, and this installer always builds venvs from sys.executable, so the
# interpreters match by construction.
ROCM_SYSTEM_PACKAGE = "python-onnxruntime-rocm"
# onnx-asr must be --no-deps here too: its metadata would pull plain
# onnxruntime (CPU) next to the system onnxruntime-rocm and collide on the
# onnxruntime/ package directory.
WORKER_ROCM_VENV_PKGS = ("numpy>=2.3", "huggingface-hub>=0.34")
WORKER_ROCM_NO_DEPS = ("onnx-asr==0.12.0",)

# Consumer gfx targets without native MIOpen/rocBLAS kernels map onto the
# nearest supported architecture (same table as Kokoro TTS /
# scripts/lib/gpu_detect.sh).
HSA_OVERRIDE_MAP = {
    "gfx1031": "10.3.0", "gfx1032": "10.3.0", "gfx1033": "10.3.0",
    "gfx1034": "10.3.0", "gfx1035": "10.3.0", "gfx1036": "10.3.0",
    "gfx1010": "10.3.0", "gfx1011": "10.3.0", "gfx1012": "10.3.0",
    "gfx1101": "11.0.0", "gfx1102": "11.0.0", "gfx1103": "11.0.0",
}

SILERO_TAG = "v6.2.1"
SILERO_URL = f"https://raw.githubusercontent.com/snakers4/silero-vad/{SILERO_TAG}/src/silero_vad/data/silero_vad.onnx"
SILERO_BYTES = 2_327_524
SILERO_SHA256_PREFIX = "1a153a22"

MODEL_REPOS = {
    "nemo-parakeet-tdt-0.6b-v2": "istupakov/parakeet-tdt-0.6b-v2-onnx",
    "nemo-parakeet-tdt-0.6b-v3": "istupakov/parakeet-tdt-0.6b-v3-onnx",
}

type JsonObject = dict[str, Any]

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"


class InstallError(RuntimeError):
    pass


def log_step(msg: str) -> None:
    print(f"{BOLD}==> {msg}{RESET}", flush=True)


def log_ok(msg: str) -> None:
    print(f"{GREEN}  ok {RESET}{msg}", flush=True)


def log_warn(msg: str) -> None:
    print(f"{YELLOW}  ** {RESET}{msg}", flush=True)


def run(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: float = 3600.0,
    check: bool = True,
    quiet: bool = True,
) -> subprocess.CompletedProcess[str]:
    res = subprocess.run(cmd, env=env, cwd=cwd, capture_output=quiet, text=True, timeout=timeout, check=False)
    if check and res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()
        raise InstallError(f"Command failed ({res.returncode}): {' '.join(cmd)}\n{detail[-4000:]}")
    return res


# ------------------------------------------------------------------ hardware
def detect_hardware() -> tuple[str, JsonObject]:
    """Auto-detect: nvidia > amd > cpu. Never fails; returns (kind, info)."""
    # NVIDIA: nvidia-smi must work and report a GPU with driver >= 580.
    try:
        smi = shutil.which("nvidia-smi")
        if smi:
            res = subprocess.run(
                [smi, "--query-gpu=index,name,driver_version,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                gpus: list[JsonObject] = []
                for line in res.stdout.splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) != 4:
                        continue
                    try:
                        gpus.append({"index": int(parts[0]), "name": parts[1],
                                     "driver": parts[2], "memory_total_mib": int(float(parts[3]))})
                    except ValueError:
                        continue
                if gpus:
                    try:
                        major = int(gpus[0]["driver"].split(".")[0])
                    except (ValueError, IndexError):
                        major = 0
                    if major >= MIN_DRIVER_MAJOR:
                        return "nvidia", {"gpus": gpus, "driver_major": major}
    except (OSError, subprocess.SubprocessError):
        pass
    # AMD: ROCm stack, /dev/kfd, or AMD VGA in lspci. Acceleration is
    # opportunistic (CPU fallback always works), so detection is lenient.
    try:
        if shutil.which("rocm-smi") or Path("/dev/kfd").exists():
            return "amd", {"reason": "rocm-smi or /dev/kfd present"}
        lspci = shutil.which("lspci")
        if lspci:
            res = subprocess.run([lspci, "-nn"], capture_output=True, text=True, timeout=10, check=False)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    low = line.lower()
                    if ("vga" in low or "3d" in low or "display" in low) and ("1002:" in line or " amd/" in low or "amd " in low and "advanced micro" in low):
                        return "amd", {"reason": f"lspci: {line.strip()[:100]}"}
    except (OSError, subprocess.SubprocessError):
        pass
    return "cpu", {}


def detect_rocm() -> JsonObject | None:
    """Probe a usable ROCm userspace for the amd worker path.

    Returns {"release", "gfx", "hsa_override"} or None when ROCm is absent or
    cannot run (no KFD). Mirrors the Kokoro TTS installer's contract.
    """
    version_file = Path("/opt/rocm/.info/version")
    if not Path("/dev/kfd").exists():
        return None  # amdgpu KFD node missing -> ROCm cannot execute
    if not version_file.exists() and not shutil.which("rocm-smi"):
        return None
    release = ""
    if version_file.exists():
        try:
            release = ".".join(version_file.read_text(encoding="utf-8").strip().split(".")[:2])
        except OSError:
            pass
    gfx = ""
    rocminfo = shutil.which("rocminfo")
    if rocminfo:
        res = run([rocminfo], timeout=60, check=False)
        match = re.search(r"gfx[0-9a-f]+", res.stdout or "")
        gfx = match.group(0) if match else ""
    return {"release": release, "gfx": gfx, "hsa_override": HSA_OVERRIDE_MAP.get(gfx, "")}


def query_nvidia_gpu(gpu_device: int) -> tuple[int, str]:
    """Return (total_mib, driver) for the requested NVIDIA index. Raises if missing."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        raise InstallError("nvidia-smi not found; install nvidia-utils for --hardware nvidia.")
    res = run([smi, "--query-gpu=index,memory.total,driver_version", "--format=csv,noheader,nounits"], timeout=30)
    for line in res.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            index = int(parts[0])
            total_mb = int(float(parts[1]))
        except ValueError:
            continue
        if index == gpu_device:
            driver = parts[2]
            try:
                major = int(driver.split(".")[0])
            except (ValueError, IndexError):
                raise InstallError(f"Cannot parse NVIDIA driver version: {driver!r}")
            if major < MIN_DRIVER_MAJOR:
                raise InstallError(f"NVIDIA driver {driver} < required {MIN_DRIVER_MAJOR}+ for CUDA 13.")
            log_ok(f"GPU {gpu_device}: driver {driver}, {total_mb} MiB VRAM")
            return total_mb, driver
    raise InstallError(f"nvidia-smi did not report GPU index {gpu_device}.")


def choose_vram_limit(total_mb: int, requested_mb: int | None) -> int:
    safe_max = total_mb - 768
    if safe_max < 1024:
        raise InstallError(f"GPU has insufficient VRAM ({total_mb} MiB). Need >= 1792 MiB total.")
    if requested_mb is not None:
        if requested_mb > safe_max:
            raise InstallError(f"Requested {requested_mb} MiB exceeds safe ceiling {safe_max} MiB.")
        return requested_mb
    return min(round(total_mb * 0.70), safe_max)


# ------------------------------------------------------------------ preflight
def assert_runtime() -> None:
    log_step("Validating runtime (Arch / kernel / Python / session)")
    if os.geteuid() == 0:
        raise InstallError("Do not run as root; installs into your user home.")
    try:
        os_release = Path("/etc/os-release").read_text(encoding="utf-8")
    except OSError as exc:
        raise InstallError(f"Cannot read /etc/os-release: {exc}") from exc
    release = dict(line.split("=", 1) for line in os_release.splitlines() if "=" in line)
    ident = release.get("ID", "").strip().strip('"')
    like = release.get("ID_LIKE", "")
    pretty = release.get("PRETTY_NAME", "").strip().strip('"')
    if ident != "arch" and "arch" not in like:
        raise InstallError(f"Targets Arch Linux rolling only (found {pretty or ident}).")
    kernel = platform.release()
    match = re.match(r"^(\d+)\.(\d+)", kernel)
    if not match:
        raise InstallError(f"Cannot parse kernel release: {kernel}")
    if (int(match.group(1)), int(match.group(2))) < MIN_KERNEL:
        raise InstallError(f"Kernel {kernel} < required {MIN_KERNEL[0]}.{MIN_KERNEL[1]}+.")
    log_ok(f"Arch / kernel {kernel}")
    if sys.version_info < MIN_PYTHON:
        raise InstallError(f"CPython {MIN_PYTHON[0]}.{MIN_PYTHON[1]}.{MIN_PYTHON[2]}+ required (found {sys.version.split()[0]}).")
    gil = getattr(sys, "_is_gil_enabled", None)
    if gil is None or not gil():
        raise InstallError("GIL-enabled CPython required; onnxruntime 1.27.0 targets the GIL ABI.")
    log_ok(f"CPython {sys.version.split()[0]} (GIL enabled)")
    if not os.environ.get("XDG_RUNTIME_DIR"):
        raise InstallError("XDG_RUNTIME_DIR unset; run inside a systemd user session.")
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise InstallError("WAYLAND_DISPLAY unset; Dusky types via wtype on Wayland.")
    log_ok(f"Wayland {os.environ['WAYLAND_DISPLAY']}")


def install_pacman_packages(packages: tuple[str, ...], skip: bool) -> None:
    log_step("Checking system packages via pacman")
    missing = [p for p in packages if subprocess.run(["pacman", "-Qq", p], capture_output=True).returncode != 0]
    if not missing:
        log_ok("System dependencies present.")
        return
    if skip:
        log_warn(f"Missing (skipped by --skip-pacman): {' '.join(missing)}")
        return
    log_warn(f"Installing: {' '.join(missing)}")
    run(["sudo", "pacman", "-S", "--needed", "--noconfirm", *missing], quiet=False, timeout=1800)
    log_ok("System dependencies installed.")


# ------------------------------------------------------------------ venvs
def install_python_environments(stage: Path, hardware: str, worker_ort: str,
                                rocm: JsonObject | None) -> tuple[Path, Path]:
    log_step(f"Provisioning venvs via uv (hardware={hardware})")
    uv = shutil.which("uv")
    if not uv:
        raise InstallError("uv is required on PATH (pacman -S uv).")
    env = dict(os.environ)
    env["UV_PYTHON_DOWNLOADS"] = "never"
    # NOTE: do NOT set UV_NO_PROGRESS -- pip wheel downloads (~2 GiB CUDA
    # on nvidia, ~600 MiB model later) must stream progress, otherwise the
    # installer looks hung for minutes (see: 4m51s silent hang report).
    env.pop("UV_NO_PROGRESS", None)
    # Cache (~/.cache/uv) and stage (~/.local/lib) are different filesystems
    # here; copy avoids hardlink-fallback warning spam on every wheel.
    env["UV_LINK_MODE"] = "copy"
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    main_venv = stage / ".venv-main"
    worker_venv = stage / ".venv-worker"
    log_step("Creating .venv-main + .venv-worker")
    run([uv, "venv", "--python", sys.executable, "--python-preference", "only-system", str(main_venv)], env=env)
    # The rocm worker sees the system python-onnxruntime-rocm through
    # system-site-packages; every other worker is fully isolated.
    worker_ssp = ["--system-site-packages"] if worker_ort == "rocm" else []
    run([uv, "venv", "--python", sys.executable, "--python-preference", "only-system", *worker_ssp, str(worker_venv)], env=env)
    main_py = main_venv / "bin" / "python"
    worker_py = worker_venv / "bin" / "python"
    log_step("Installing .venv-main packages (onnxruntime CPU, ~50 MiB)")
    run([uv, "pip", "install", "--python", str(main_py), *MAIN_PACKAGES], env=env, quiet=False, timeout=1800)
    if worker_ort == "cuda":
        log_step("Installing CUDA 13 runtime wheels (~2.2 GiB -- expect several minutes, progress below)")
        run([uv, "pip", "install", "--python", str(worker_py), *WORKER_CUDA_PACKAGES], env=env, quiet=False, timeout=3600)
        log_step("Installing onnxruntime-gpu + deps")
        run([uv, "pip", "install", "--python", str(worker_py), *WORKER_NVIDIA_PACKAGES], env=env, quiet=False, timeout=1800)
        log_step("Installing onnx-asr --no-deps (protects CUDA namespace)")
        run([uv, "pip", "install", "--python", str(worker_py), "--no-deps", *WORKER_NVIDIA_NO_DEPS], env=env, quiet=False, timeout=600)
    elif worker_ort == "rocm":
        # onnxruntime-rocm itself comes from the system package; only the
        # helper deps are installed into the venv.
        if subprocess.run(["pacman", "-Qq", ROCM_SYSTEM_PACKAGE], capture_output=True).returncode != 0:
            raise InstallError(
                f"{ROCM_SYSTEM_PACKAGE} is not installed. The amd/ROCm worker reads it "
                "through system-site-packages; install it with "
                "'sudo pacman -S python-onnxruntime-rocm' (or 396_amd_rocm_stack.sh --python), "
                "or re-run with --hardware cpu for the reliable CPU path.")
        release = (rocm or {}).get("release", "")
        log_step(f"Worker uses system {ROCM_SYSTEM_PACKAGE} (ROCm {release or '?'}) via system-site-packages")
        log_step("Installing worker helper deps (numpy, huggingface-hub)")
        run([uv, "pip", "install", "--python", str(worker_py), *WORKER_ROCM_VENV_PKGS], env=env, quiet=False, timeout=1800)
        log_step("Installing onnx-asr --no-deps (protects the ROCm namespace)")
        run([uv, "pip", "install", "--python", str(worker_py), "--no-deps", *WORKER_ROCM_NO_DEPS], env=env, quiet=False, timeout=600)
    else:
        log_step(f"Installing worker packages ({hardware}, CPU ORT)")
        run([uv, "pip", "install", "--python", str(worker_py), *WORKER_CPU_PACKAGES], env=env, quiet=False, timeout=1800)
    run([uv, "pip", "check", "--python", str(main_py)], env=env)
    # A system-site-packages venv also sees system dists, whose dependency
    # state pacman (not pip) owns -- treat pip check as advisory there.
    if worker_ort == "rocm":
        res = run([uv, "pip", "check", "--python", str(worker_py)], env=env, check=False)
        if res.returncode != 0 and (res.stdout or "").strip():
            log_warn(f"uv pip check reported (system packages included): {(res.stdout or '').strip()[:300]}")
    else:
        run([uv, "pip", "check", "--python", str(worker_py)], env=env)
    log_ok("Virtual environments built.")
    return main_py, worker_py


def verify_namespaces(main_py: Path, worker_py: Path, worker_ort: str) -> None:
    log_step("Asserting onnxruntime namespace exclusivity")
    probe = (
        "import importlib.metadata as m, sys; "
        "owners = sorted(set(m.packages_distributions().get('onnxruntime', []))); "
        "assert owners == [sys.argv[1]], f'Namespace collision: {owners}'"
    )
    run([str(main_py), "-c", probe, "onnxruntime"], env={"CUDA_VISIBLE_DEVICES": "-1"})
    # NOTE on the rocm path: Arch's python-onnxruntime-rocm package carries a
    # distribution literally named "onnxruntime" (verified against its
    # dist-info), so the probe expects the same name as the cpu path. The
    # functional cpu/rocm distinction is the worker self-test's provider
    # check, not this name.
    expected = "onnxruntime-gpu" if worker_ort == "cuda" else "onnxruntime"
    run([str(worker_py), "-c", probe, expected],
        env={"CUDA_VISIBLE_DEVICES": "0" if worker_ort == "cuda" else "-1"})
    log_ok("ORT namespaces partitioned.")


def download_silero(stage: Path, expected_sha256: str | None) -> str:
    log_step(f"Downloading pinned Silero VAD {SILERO_TAG}")
    target_dir = stage / "models"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "silero_vad.onnx"
    req = urllib.request.Request(SILERO_URL, headers={"User-Agent": "dusky-installer/2"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    if len(data) != SILERO_BYTES:
        raise InstallError(f"Silero size mismatch: expected {SILERO_BYTES}, got {len(data)}")
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256:
        if digest != expected_sha256.lower():
            raise InstallError(f"Silero SHA-256 mismatch: {digest} != {expected_sha256.lower()}")
    elif not digest.startswith(SILERO_SHA256_PREFIX):
        raise InstallError(f"Silero digest {digest} lacks pinned prefix {SILERO_SHA256_PREFIX}")
    target.write_bytes(data)
    os.chmod(target, 0o644)
    log_ok(f"Silero verified ({digest[:16]}...)")
    return digest


def prefetch_model(worker_py: Path, model: str, model_dir: Path, quantization: str) -> None:
    log_step(f"Prefetching ASR model: {model} ({quantization}) -- ~600 MiB, progress below")
    repo = MODEL_REPOS.get(model)
    if repo is None:
        raise InstallError(f"Unknown model {model!r}; known: {sorted(MODEL_REPOS)}")
    model_dir.mkdir(parents=True, exist_ok=True)
    # Phase 1: snapshot_download the HF repo (load_model alone with
    # local_files_only will NOT download -- that was the
    # ModelFileNotFoundError: encoder-model int8.onnx bug).
    dl_code = (
        "import sys; from huggingface_hub import snapshot_download; "
        "p = snapshot_download(repo_id=sys.argv[1], local_dir=sys.argv[2]); print(p)"
    )
    dl_env = dict(os.environ)
    dl_env["HF_HUB_OFFLINE"] = "0"
    dl_env["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
    run([str(worker_py), "-c", dl_code, repo, str(model_dir)], env=dl_env, timeout=5400, quiet=False)
    onnx_files = list(model_dir.rglob("*.onnx"))
    if not onnx_files:
        raise InstallError(f"No .onnx graphs found in {model_dir} after download of {repo}")
    log_ok(f"Downloaded {repo} ({len(onnx_files)} graphs). Smoke-loading...")
    # Phase 2: smoke-load via CPU EP to prove the graph is valid.
    # NOTE: .venv-worker on nvidia holds onnxruntime-gpu, whose import
    # requires libcudart.so.13 at dlopen time even for CPU EP. Preload
    # the PyPI CUDA libs RTLD_GLOBAL first; no-op on cpu workers.
    code = """
import ctypes, importlib.metadata, os, pathlib, sys
from pathlib import Path as _P
_ORDER = ("libnvJitLink.so.13","libcudart.so.13","libnvrtc-builtins.so.13","libnvrtc.so.13",
 "libcublasLt.so.13","libcublas.so.13","libcufft.so.12","libcurand.so.10",
 "libcudnn_graph.so.9","libcudnn_engines_precompiled.so.9","libcudnn_ops.so.9",
 "libcudnn_adv.so.9","libcudnn_cnn.so.9","libcudnn.so.9")
_DISTS = ("nvidia-cuda-runtime","nvidia-cublas","nvidia-cudnn-cu13","nvidia-cuda-nvrtc","nvidia-cufft","nvidia-curand","nvidia-nvjitlink")
_idx = {}
for _d in _DISTS:
    try: _dist = importlib.metadata.distribution(_d)
    except importlib.metadata.PackageNotFoundError: continue
    for _f in _dist.files or ():
        _p = _P(_dist.locate_file(_f)).resolve()
        if _p.is_file() and ".so" in _p.name:
            _idx.setdefault(_p.name, _p)
            for _s in _ORDER:
                if _p.name == _s or _p.name.startswith(_s + "."): _idx.setdefault(_s, _p)
for _s in _ORDER:
    _m = _idx.get(_s)
    if _m:
        try: ctypes.CDLL(str(_m), mode=ctypes.RTLD_GLOBAL | os.RTLD_NOW)
        except OSError: pass
import onnx_asr
q = None if sys.argv[3] in ('none', 'fp32') else sys.argv[3]
model = onnx_asr.load_model(
    sys.argv[1],
    pathlib.Path(sys.argv[2]),
    quantization=q,
    providers=['CPUExecutionProvider'],
    preprocessor_config={'max_concurrent_workers': 1, 'use_numpy_preprocessors': True}
)
res = model.recognize(__import__('numpy').zeros(16000, dtype='float32'), sample_rate=16000)
assert isinstance(res, str)
"""
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = "-1"
    env["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
    run([str(worker_py), "-c", code, model, str(model_dir), quantization], env=env, timeout=1800, quiet=False)
    log_ok("Model prefetched and smoke-tested.")


def verify_cpu_vad(main_py: Path, vad_path: Path) -> None:
    log_step("Verifying CPU-only VAD and clean address space")
    code = """
import pathlib, sys, numpy as np, onnxruntime as ort
opts = ort.SessionOptions()
opts.intra_op_num_threads = 1
opts.inter_op_num_threads = 1
session = ort.InferenceSession(sys.argv[1], sess_options=opts, providers=['CPUExecutionProvider'])
assert session.get_providers() == ['CPUExecutionProvider']
out = session.run(None, {
    'input': np.zeros((1, 576), dtype=np.float32),
    'state': np.zeros((2, 1, 128), dtype=np.float32),
    'sr': __import__('numpy').array(16000, dtype=__import__('numpy').int64)
})
maps = pathlib.Path('/proc/self/maps').read_text().casefold()
for f in ('libcuda.so', 'libcudart.so', 'libcublas', 'libcudnn', 'onnxruntime_providers_cuda'):
    if f in maps:
        raise SystemExit(f'Forbidden CUDA map: {f}')
"""
    run([str(main_py), "-c", code, str(vad_path)], env={"CUDA_VISIBLE_DEVICES": "-1"})
    log_ok("CPU VAD clean.")


def verify_worker(worker_py: Path, stage: Path, hardware: str, gpu_device: int, config_path: Path,
                  worker_ort: str = "cpu", hsa_override: str = "") -> JsonObject:
    log_step(f"Self-testing worker (hardware={hardware}) -- ~30-60s, silent while encoder loads")
    env = dict(os.environ)
    if hardware == "nvidia":
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_device)
        env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        env["CUDA_MODULE_LOADING"] = "LAZY"
    else:
        env["CUDA_VISIBLE_DEVICES"] = "-1"
    if hsa_override:
        # Consumer gfx without native MIOpen/rocBLAS kernels must be mapped
        # before libamdhip64 initializes (same contract as Kokoro TTS).
        env["HSA_OVERRIDE_GFX_VERSION"] = hsa_override
    env["HF_HUB_OFFLINE"] = "1"
    res = run([str(worker_py), str(stage / "dusky_worker.py"), "--config", str(config_path), "--self-test"],
              env=env, cwd=stage, timeout=600, quiet=True)
    stdout = res.stdout or ""
    try:
        report = json.loads(stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        report = {"ok": True, "raw": stdout[-500:]}
    if hardware == "nvidia" and not report.get("ok"):
        raise InstallError(f"CUDA self-test failed (no CUDA EP nodes): {report}")
    if worker_ort == "rocm":
        providers = [p for ps in (report.get("providers") or [])
                     for p in (ps if isinstance(ps, list) else [])]
        if not any(("ROCmExecutionProvider" in p) or ("MIGraphXExecutionProvider" in p) for p in providers):
            raise InstallError(
                "ROCm self-test failed: no ROCm/MIGraphX EP in session providers "
                f"({providers}). The wheels linked but the runtime did not bind -- "
                "check 'rocminfo' and the render group, or re-run with --hardware cpu.")
    log_ok(f"Worker self-test passed ({hardware}).")
    return report


def deploy_stage(stage: Path) -> Path | None:
    log_step(f"Atomically deploying to {APP_DIR}")
    subprocess.run(["systemctl", "--user", "stop", UNIT_NAME], capture_output=True, check=False)
    backup: Path | None = None
    if APP_DIR.exists():
        backup = APP_DIR.parent / f"dusky-stt.backup-{int(time.time())}"
        APP_DIR.rename(backup)
    try:
        stage.rename(APP_DIR)
    except Exception as exc:
        if backup and backup.exists() and not APP_DIR.exists():
            backup.rename(APP_DIR)
        raise InstallError(f"Atomic rename failed: {exc}") from exc
    log_ok("Deployed.")
    return backup


def install_entrypoints(hsa_override: str = "") -> None:
    log_step("Installing entry points and unit")
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    main_py = APP_DIR / ".venv-main" / "bin" / "python"
    trigger_src = (APP_DIR / "dusky_trigger.py").read_text(encoding="utf-8").splitlines()
    if trigger_src and trigger_src[0].startswith("#!"):
        trigger_src[0] = f"#!{main_py}"
    trigger_dest = BIN_DIR / "dusky_trigger"
    trigger_dest.write_text("\n".join(trigger_src) + "\n", encoding="utf-8")
    os.chmod(trigger_dest, 0o755)
    verify_dest = BIN_DIR / "dusky_verify"
    shutil.copyfile(APP_DIR / "dusky_verify.sh", verify_dest)
    os.chmod(verify_dest, 0o755)
    unit_dest = UNIT_DIR / UNIT_NAME
    shutil.copyfile(APP_DIR / UNIT_NAME, unit_dest)
    if hsa_override:
        # Same mapping the self-test needed; the deployed service must set it
        # before the worker imports libamdhip64.
        unit_text = unit_dest.read_text(encoding="utf-8")
        if "HSA_OVERRIDE_GFX_VERSION" not in unit_text and "[Service]" in unit_text:
            unit_text = unit_text.replace(
                "[Service]\n",
                f"[Service]\n# Consumer gfx without native MIOpen/rocBLAS kernels (set by installer)\n"
                f"Environment=HSA_OVERRIDE_GFX_VERSION={hsa_override}\n", 1)
            unit_dest.write_text(unit_text, encoding="utf-8")
            log_ok(f"HSA_OVERRIDE_GFX_VERSION={hsa_override} baked into {UNIT_NAME}")
    os.chmod(unit_dest, 0o644)
    run(["systemd-analyze", "--user", "verify", str(unit_dest)])
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", "--now", UNIT_NAME])
    log_ok("Service enabled.")


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="dusky_installer", description="Hardware-agnostic installer for Dusky STT")
    p.add_argument("--hardware", default="auto", choices=("auto", "cpu", "nvidia", "amd"),
                   help="ASR backend (default auto-detects nvidia > amd > cpu)")
    p.add_argument("--model", default="nemo-parakeet-tdt-0.6b-v2",
                   choices=sorted(MODEL_REPOS))
    p.add_argument("--quantization", default="int8", choices=("int8", "fp16", "fp32", "none"))
    p.add_argument("--gpu-device", type=int, default=0)
    p.add_argument("--gpu-mem-limit-mb", type=int, default=None)
    p.add_argument("--input-device", default=None)
    p.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    p.add_argument("--model-dir", default=None)
    p.add_argument("--output-mode", default="realtime-both", choices=("clipboard", "both", "realtime-both"))
    p.add_argument("--keep-audio", action="store_true")
    p.add_argument("--idle-timeout-seconds", type=float, default=90.0)
    p.add_argument("--silero-sha256", default=None)
    p.add_argument("--skip-pacman", action="store_true")
    p.add_argument("--uninstall", action="store_true")
    return p.parse_args(argv)


def uninstall() -> int:
    log_step("Uninstalling Dusky STT")
    subprocess.run(["systemctl", "--user", "disable", "--now", UNIT_NAME], check=False)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    for p in (UNIT_DIR / UNIT_NAME, BIN_DIR / "dusky_trigger", BIN_DIR / "dusky_verify"):
        p.unlink(missing_ok=True)
    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)
    log_ok("Uninstalled (transcripts/models retained).")
    return 0


def main(argv: list[str]) -> int:
    args = parse_arguments(argv)
    if args.uninstall:
        return uninstall()
    assert_runtime()
    detected, info = detect_hardware()
    hardware = detected if args.hardware == "auto" else args.hardware
    if args.hardware != "auto" and args.hardware != detected and detected != "cpu":
        log_warn(f"Requested {args.hardware} but auto-detected {detected} {info}; using requested.")
    log_step(f"Hardware backend: {hardware} (auto-detected: {detected})")

    gpu_limit = 4096
    worker_ort = "cpu"
    rocm: JsonObject | None = None
    if hardware == "nvidia":
        worker_ort = "cuda"
        total_mb, _driver = query_nvidia_gpu(args.gpu_device)
        # 2GB-VRAM guard: fp32 encoder alone is ~2.5 GB and can never fit;
        # fail fast with a clear message instead of a post-download OOM.
        if total_mb < 3072 and args.quantization in ("none", "fp32"):
            raise InstallError(
                f"GPU has {total_mb} MiB VRAM: fp32 model needs ~2.5 GB just for "
                "weights. Re-run with --quantization int8 (recommended) or fp16.")
        if total_mb < 3072 and args.quantization == "fp16":
            log_warn(f"Only {total_mb} MiB VRAM with fp16 (~1.25 GB weights + CUDA "
                     "context + activations): tight. Prefer --quantization int8.")
        gpu_limit = choose_vram_limit(total_mb, args.gpu_mem_limit_mb)
    elif hardware == "amd":
        rocm = detect_rocm()
        if rocm:
            worker_ort = "rocm"
            note = f", HSA_OVERRIDE_GFX_VERSION={rocm['hsa_override']}" if rocm["hsa_override"] else ""
            log_step(f"AMD ROCm path: release {rocm['release'] or '?'}, gfx {rocm['gfx'] or 'unknown'}{note}")
            log_warn("python-onnxruntime-rocm pulls the full rocm-hip-sdk dependency chain (~30 GiB installed).")
        else:
            log_warn("AMD GPU present but no usable ROCm stack (/opt/rocm + /dev/kfd).")
            log_warn("Worker will use CPUExecutionProvider (reliable). Run "
                     "396_amd_rocm_stack.sh, reboot, then re-run this installer for the ROCm path.")

    packages = BASE_PACKAGES + (NVIDIA_PACKAGES if hardware == "nvidia" else ())
    if worker_ort == "rocm":
        # The worker reads this through system-site-packages; installing it
        # up front keeps the venv step's guard happy.
        packages += (ROCM_SYSTEM_PACKAGE,)

    install_pacman_packages(packages, args.skip_pacman)

    APP_DIR.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".dusky-stage-", dir=APP_DIR.parent))
    os.chmod(stage, 0o700)
    backup: Path | None = None
    try:
        for name in REQUIRED_SOURCES:
            shutil.copy2(SOURCE_DIR / name, stage / name)
        for name in ("dusky_main.py", "dusky_worker.py", "dusky_trigger.py", "dusky_rec_indicator.py", "dusky_verify.sh"):
            os.chmod(stage / name, 0o755)
        main_py, worker_py = install_python_environments(stage, hardware, worker_ort, rocm)
        silero_hash = download_silero(stage, args.silero_sha256)
        verify_namespaces(main_py, worker_py, worker_ort)
        verify_cpu_vad(main_py, stage / "models" / "silero_vad.onnx")
        model_dir = Path(args.model_dir).expanduser() if args.model_dir else DEFAULT_MODEL_ROOT / args.model
        prefetch_model(worker_py, args.model, model_dir, args.quantization)
        config: JsonObject = {
            "schema_version": SCHEMA_VERSION,
            "hardware": hardware,
            "worker_ort": worker_ort,
            "model": args.model,
            "model_dir": str(model_dir),
            "quantization": None if args.quantization in ("none", "fp32") else args.quantization,
            "gpu_device": args.gpu_device,
            "gpu_mem_limit_mb": gpu_limit,
            "input_device": args.input_device,
            "state_dir": str(Path(args.state_dir).expanduser()),
            "output_mode": args.output_mode,
            "push_type_at_end": True,
            "keep_audio": args.keep_audio,
            "idle_timeout_seconds": args.idle_timeout_seconds,
            "max_inflight_requests": 2,
            "realtime_interval_seconds": 1.2,
            "finalize_timeout_seconds": 120.0,
            "max_request_seconds": 30.0,
            "max_phrase_seconds": 15.0,
            # 20 s (not 25 s): Parakeet TDT is trained on short utterances and
            # onnx-asr caps at 20-30 s per forward; 20 s cuts O(T^2) attention
            # peak ~1.5x vs 25 s on 2 GB VRAM with fewer mid-word seams.
            "file_chunk_seconds": 20.0,
            "pre_roll_seconds": 0.32,
            "phrase_silence_seconds": 0.80,
            "vad_onset_seconds": 0.096,
            "vad_min_speech_seconds": 0.25,
            "vad_start_threshold": 0.50,
            "vad_end_threshold": 0.35,
            "stable_holdback_words": 2,
            "silero_sha256": silero_hash,
            "vad_model_path": "models/silero_vad.onnx",
            "worker_python": ".venv-worker/bin/python",
            "worker_script": "dusky_worker.py",
        }
        cfg_path = stage / "config.json"
        cfg_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        os.chmod(cfg_path, 0o600)
        report = verify_worker(worker_py, stage, hardware, args.gpu_device, cfg_path,
                                   worker_ort, (rocm or {}).get("hsa_override", ""))
        manifest = {
            "schema_version": SCHEMA_VERSION, "hardware": hardware, "detected": detected,
            "worker_ort": worker_ort, "rocm": rocm,
            "kernel": platform.release(), "python": sys.version.split()[0],
            "silero_sha256": silero_hash, "model": args.model,
            "self_test": report, "time": int(time.time()),
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        backup = deploy_stage(stage)
        install_entrypoints((rocm or {}).get("hsa_override", ""))
        if backup and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        log_ok(f"Dusky STT installed ({hardware}) at {APP_DIR}.")
        return 0
    except KeyboardInterrupt:
        shutil.rmtree(stage, ignore_errors=True)
        if backup and backup.exists() and not APP_DIR.exists():
            backup.rename(APP_DIR)
        print(f"\n{YELLOW}Installation cancelled by user (Ctrl-C). Stale stage removed; re-run to resume.{RESET}",
              file=sys.stderr)
        return 130
    except BaseException as exc:
        shutil.rmtree(stage, ignore_errors=True)
        if backup and backup.exists():
            try:
                if not APP_DIR.exists():
                    backup.rename(APP_DIR)
                subprocess.run(["systemctl", "--user", "restart", UNIT_NAME], check=False)
            except BaseException:
                pass
        if isinstance(exc, InstallError):
            print(f"\n{RED}Installation failed: {exc}{RESET}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
