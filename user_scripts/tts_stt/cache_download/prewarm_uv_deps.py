#!/usr/bin/env python3
"""General, self-updating uv dependency cache prewarmer (CPython 3.15+ / Arch Linux).

Prewarms and updates ~/.cache/uv using throwaway per-profile virtual environments.
Never overrides UV_CACHE_DIR, downloads models, touches systemd, or writes ~/contained_apps.
"""
from __future__ import annotations

import argparse
import dataclasses
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if sys.version_info < (3, 15):
    sys.exit(f"[xx] Python >= 3.15 required to run this script (found {sys.version.split()[0]})")

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PY_SERIES = "3.15"
CUDA_MAJOR = 13
KOKORO_ONNX_VERSION = "0.6.1"
KOKORO_ORT_MIN = "1.27"
STT_ORT_GPU_PIN = "1.31.0"
GIB = 1024**3

EMBEDDED_STT_CPU_REQS = """\
certifi==2026.7.22
cffi==2.1.1
charset-normalizer==3.5.2
filelock==4.0.12
google-crc32c==1.9.0
idna==3.20
moonshine-voice==0.1.5
numpy==2.5.3
platformdirs==4.12.4
pycparser==3.1
requests==2.34.2
sounddevice==0.5.6
tqdm==4.70.1
urllib3==2.8.0
"""

EMBEDDED_STT_GPU_REQS = """\
flatbuffers==25.12.19
huggingface-hub==2.0.0
numpy==2.5.3
onnx-asr==0.12.0
packaging==26.3
protobuf==7.36.2
nvidia-cuda-runtime==13.0.88
nvidia-cublas==13.0.2.14
nvidia-cudnn-cu13==9.13.1.26
nvidia-cuda-nvrtc==13.0.88
nvidia-cufft==12.0.0.15
nvidia-curand==10.4.0.35
nvidia-nvjitlink==13.0.88
"""

CUDA_TOOLKIT_PACKAGES = (
    "nvidia-cuda-runtime",
    "nvidia-cuda-cupti",
    "nvidia-cuda-nvcc",
    "nvidia-nvvm",
    "nvidia-cuda-crt",
    "nvidia-cuda-nvrtc",
    "nvidia-cublas",
    "nvidia-cufft",
    "nvidia-curand",
    "nvidia-cusolver",
    "nvidia-cusparse",
    f"nvidia-cudnn-cu{CUDA_MAJOR}",
    "nvidia-nvjitlink",
    "nvidia-nvtx",
    "nvidia-cuda-cccl",
    f"nvidia-nvshmem-cu{CUDA_MAJOR}",
    "nvidia-cufile",
)

PYTORCH_CUDA_PACKAGES = (
    "torch",
    "torchvision",
    "torchaudio",
    "triton",
    "transformers",
    "accelerate",
    "safetensors",
    "tokenizers",
    "huggingface-hub",
    "einops",
)

PYTHON_COMMON_PACKAGES = (
    "numpy",
    "scipy",
    "pillow",
    "soundfile",
    "cffi",
    "psutil",
    "orjson",
    "pydantic",
    "pyyaml",
    "regex",
    "httpx",
    "requests",
    "aiohttp",
    "websockets",
    "rich",
    "textual",
    "click",
    "typer",
    "tqdm",
    "platformdirs",
    "tomli-w",
    "python-dotenv",
    "watchfiles",
    "setuptools",
    "wheel",
    "hatchling",
    "flit-core",
    "pdm-backend",
    "meson-python",
    "scikit-build-core",
    "cython",
    "maturin",
    "packaging",
    "ruff",
    "pytest",
    "ipython",
)

PROFILES: tuple[tuple[str, str, str, str], ...] = (
    ("stt-cpu", "pinned", "cpu", "Dusky STT Moonshine CPU pinned requirements"),
    ("stt-nvidia", "pinned", "nvidia", "Dusky STT Parakeet NVIDIA pins + onnxruntime-gpu"),
    ("kokoro-cpu", "pinned", "cpu", "Kokoro TTS CPU stack (kokoro-onnx metadata override)"),
    ("kokoro-nvidia", "pinned", "nvidia", "Kokoro TTS NVIDIA stack (kokoro-onnx metadata override)"),
    ("cuda13-toolkit", "rolling", "nvidia", f"Full latest CUDA {CUDA_MAJOR} runtime/math/cuDNN/NVSHMEM/cuFile"),
    ("pytorch-cuda", "rolling", "nvidia", "Latest PyTorch CUDA + Triton + HuggingFace runtime libs"),
    ("python-common", "rolling", "cpu", "Common scientific, networking, TUI, build-backend & CLI libs"),
)
PROFILE_NAMES = tuple(p[0] for p in PROFILES)

UPSTREAM_WHEEL_PATTERNS = (
    "no wheels with a matching",
    "has no wheels",
    "matching python abi tag",
    "only has wheels with",
    "doesn't have any wheels",
    "failed to find a matching wheel",
    "source distributions are disabled",
    "no versions of onnxruntime",
)

ACTIVE_PROC: subprocess.Popen[str] | None = None
SCRATCH_ROOT: Path | None = None
VERBOSE = False


@dataclasses.dataclass
class ProfileResult:
    name: str
    tier: str
    status: str  # OK | PARTIAL_UPSTREAM | SKIPPED | FAILED
    pkg_count: int = 0
    elapsed: float = 0.0
    headline: str = "-"
    note: str = ""


class PrewarmError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"    [ok] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"    [!!] {msg}", file=sys.stderr, flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"    [xx] {msg}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def _terminate_active() -> None:
    global ACTIVE_PROC
    proc = ACTIVE_PROC
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
    finally:
        ACTIVE_PROC = None


def _cleanup_scratch() -> None:
    global SCRATCH_ROOT
    if SCRATCH_ROOT is not None and SCRATCH_ROOT.exists():
        shutil.rmtree(SCRATCH_ROOT, ignore_errors=True)
    SCRATCH_ROOT = None


def _handle_signal(signum: int, _frame: object) -> None:
    _terminate_active()
    _cleanup_scratch()
    print(f"\n    [xx] Interrupted by signal {signum}; scratch cleaned up.", file=sys.stderr, flush=True)
    raise SystemExit(128 + signum)


def run_cmd(
    cmd: list[str],
    *,
    env: dict[str, str],
    cwd: Path | None = None,
    stream: bool | None = None,
    check: bool = True,
    merge_stderr: bool = True,
) -> tuple[int, str]:
    global ACTIVE_PROC
    should_stream = (VERBOSE if stream is None else stream) and merge_stderr
    with subprocess.Popen(
        cmd,
        env=env,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as proc:
        ACTIVE_PROC = proc
        try:
            if merge_stderr:
                chunks: list[str] = []
                assert proc.stdout is not None
                for line in proc.stdout:
                    chunks.append(line)
                    if should_stream:
                        sys.stdout.write(line)
                        sys.stdout.flush()
                rc = proc.wait()
                output = "".join(chunks)
                err_text = output
            else:
                out_text, stderr_text = proc.communicate()
                rc = proc.returncode
                output = out_text or ""
                err_text = (stderr_text or "").strip() or output
        finally:
            ACTIVE_PROC = None
    if check and rc != 0:
        tail = "\n".join(err_text.strip().splitlines()[-25:])
        raise PrewarmError(f"Command failed (exit {rc}): {' '.join(cmd)}\n{tail}")
    return rc, output


def is_upstream_wheel_error(output: str, target_pkg: str) -> bool:
    low = output.lower()
    if any(tok in low for tok in ("failed to fetch", "timed out", "connection reset", "dns error", "http status")):
        return False
    return target_pkg.lower() in low and any(pat in low for pat in UPSTREAM_WHEEL_PATTERNS)


def free_bytes(path: Path) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def fmt_gib(num_bytes: int) -> str:
    return f"{num_bytes / GIB:.1f} GiB"


def acquire_lock() -> object:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    lock_dir = Path(runtime) if runtime and os.access(runtime, os.W_OK) else Path("/tmp")
    lock_path = lock_dir / f"prewarm-uv-deps-{os.getuid()}.lock"
    fh = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.seek(0)
        owner = fh.read().strip() or "unknown"
        fh.close()
        die(f"Another prewarm instance is already running (PID {owner}, lock: {lock_path})")
    fh.seek(0)
    fh.truncate()
    fh.write(f"{os.getpid()}\n")
    fh.flush()
    return fh


def validate_cache_dir(env: dict[str, str], min_free: int, warn_free: int) -> tuple[Path, Path]:
    if "UV_CACHE_DIR" in os.environ:
        warn(f"UV_CACHE_DIR is inherited from caller environment: {os.environ['UV_CACHE_DIR']}")
    _, out = run_cmd(["uv", "cache", "dir"], env=env, stream=False, merge_stderr=False)
    cache_path = Path(out.strip())
    if cache_path.is_symlink() and not cache_path.exists():
        die(f"uv cache symlink is broken (drive unmounted?): {cache_path} -> {os.readlink(cache_path)}")
    resolved = cache_path.resolve(strict=False)
    parts = resolved.parts
    mount_root: Path | None = None
    if len(parts) >= 3 and parts[1] == "mnt":
        mount_root = Path("/mnt") / parts[2]
    elif len(parts) >= 5 and parts[1] == "run" and parts[2] == "media":
        mount_root = Path("/run/media") / parts[3] / parts[4]
    if mount_root is not None:
        if not mount_root.exists():
            die(f"Cache mountpoint does not exist: {mount_root} (resolved cache: {resolved})")
        if os.stat(mount_root).st_dev == os.stat(mount_root.parent).st_dev:
            die(f"Cache mountpoint {mount_root} is not mounted (same device as {mount_root.parent}); aborting to avoid filling /")
    resolved.mkdir(parents=True, exist_ok=True)
    if not os.access(resolved, os.W_OK | os.X_OK):
        die(f"uv cache directory is not writable: {resolved}")
    avail = free_bytes(resolved)
    if avail < min_free:
        die(f"Insufficient free space on cache drive ({resolved}): {fmt_gib(avail)} free, need >= {fmt_gib(min_free)}")
    if avail < warn_free:
        warn(f"Low free space on cache drive ({resolved}): {fmt_gib(avail)} free")
    return cache_path, resolved


def _read_tmpfs_mounts() -> list[Path]:
    mounts: list[Path] = []
    try:
        lines = Path("/proc/mounts").read_text(encoding="utf-8").splitlines()
    except OSError:
        return mounts
    for line in lines:
        fields = line.split()
        if len(fields) < 3 or fields[2] != "tmpfs":
            continue
        mp = fields[1].replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")
        p = Path(mp)
        if p == Path("/dev") or str(p).startswith("/dev/"):
            continue
        if p.is_dir() and os.access(p, os.W_OK | os.X_OK):
            mounts.append(p)
    return mounts


def discover_scratch_dir(min_free: int) -> tuple[Path, bool]:
    override = os.environ.get("PREWARM_SCRATCH_DIR")
    if override:
        p = Path(override).expanduser()
        if not (p.is_dir() and os.access(p, os.W_OK | os.X_OK)):
            die(f"PREWARM_SCRATCH_DIR is not a writable directory: {p}")
        avail = free_bytes(p)
        if avail < min_free:
            die(f"PREWARM_SCRATCH_DIR ({p}) has {fmt_gib(avail)} free, need >= {fmt_gib(min_free)}")
        return p, p in set(_read_tmpfs_mounts())

    tmpfs_mounts = _read_tmpfs_mounts()
    tmpfs_set = set(tmpfs_mounts)

    zram1 = Path("/mnt/zram1")
    if zram1.is_dir() and os.access(zram1, os.W_OK | os.X_OK):
        is_ram = zram1 in tmpfs_set
        if not is_ram:
            try:
                for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
                    fields = line.split()
                    if len(fields) >= 3 and fields[1] == "/mnt/zram1" and (
                        fields[2] == "tmpfs" or fields[0].startswith("/dev/zram")
                    ):
                        is_ram = True
                        break
            except OSError:
                pass
        if is_ram and free_bytes(zram1) >= min_free:
            return zram1, True

    tmp_path = Path("/tmp")
    if tmp_path in tmpfs_set and free_bytes(tmp_path) >= min_free:
        return tmp_path, True

    ram_candidates = sorted(
        (m for m in tmpfs_mounts if free_bytes(m) >= min_free and not str(m).startswith("/run/user")),
        key=free_bytes,
        reverse=True,
    )
    if ram_candidates:
        return ram_candidates[0], True

    for fallback in (os.environ.get("TMPDIR"), "/var/tmp", "/tmp"):
        if not fallback:
            continue
        p = Path(fallback)
        if p.is_dir() and os.access(p, os.W_OK | os.X_OK) and free_bytes(p) >= min_free:
            warn(f"No tmpfs scratch with >= {fmt_gib(min_free)} free; using disk-backed {p}")
            return p, False

    die(f"No writable scratch directory found with >= {fmt_gib(min_free)} free")
    raise AssertionError("unreachable")


def probe_nvidia_hardware() -> tuple[bool, bool, str]:
    has_hw = False
    drm = Path("/sys/class/drm")
    if drm.is_dir():
        for dev in drm.glob("card[0-9]*/device"):
            try:
                vendor = (dev / "vendor").read_text(encoding="utf-8").strip()
                cls = (dev / "class").read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if cls.startswith(("0x0300", "0x0302", "0x0380")) and vendor == "0x10de":
                has_hw = True
                break
    driver_loaded = Path("/proc/driver/nvidia/version").is_file()
    if not (has_hw and driver_loaded and shutil.which("nvidia-smi")):
        return has_hw, False, "NVIDIA GPU detected (driver inactive)" if has_hw else "No NVIDIA GPU detected"
    res = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,compute_cap", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0 or not res.stdout.strip():
        return True, False, "NVIDIA GPU detected (nvidia-smi unavailable)"
    lines = [ln.strip() for ln in res.stdout.strip().splitlines() if ln.strip()]
    caps: list[float] = []
    names: list[str] = []
    for ln in lines:
        parts = [x.strip() for x in ln.rsplit(",", 1)]
        if len(parts) == 2:
            names.append(parts[0])
            try:
                caps.append(float(parts[1]))
            except ValueError:
                pass
    if caps and all(c >= 7.5 for c in caps):
        return True, True, f"{names[0]} (sm_{caps[0]:.1f})"
    return True, False, f"{names[0] if names else 'NVIDIA'} (compute cap < 7.5; live CUDA tests disabled)"


def uv_cache_size_human(resolved_cache: Path, env: dict[str, str]) -> str:
    if shutil.which("du"):
        rc, out = run_cmd(
            ["du", "-sh", "--", str(resolved_cache)],
            env=env,
            stream=False,
            check=False,
            merge_stderr=False,
        )
        if rc == 0 and out.strip():
            return out.strip().split()[0]
    rc, out = run_cmd(
        ["uv", "cache", "size", "--human", "--preview-features", "cache-size"],
        env=env,
        stream=False,
        check=False,
        merge_stderr=False,
    )
    return out.strip().splitlines()[-1] if rc == 0 and out.strip() else "unknown"


def bootstrap_managed_python(series: str, update: bool, env: dict[str, str]) -> tuple[Path, str]:
    spec = f"cpython-{series}"
    log(f"Bootstrapping uv-managed GIL-enabled CPython ({spec})")
    run_cmd(["uv", "python", "install", spec], env=env)
    if update:
        run_cmd(["uv", "python", "upgrade", series], env=env)
    _, out = run_cmd(["uv", "python", "find", "--managed-python", spec], env=env, stream=False, merge_stderr=False)
    py_path = Path(out.strip().splitlines()[-1])
    maj, minor = (int(x) for x in series.split("."))
    _, ver_out = run_cmd(
        [
            str(py_path),
            "-c",
            f"import sys; assert sys.version_info[:2] == ({maj}, {minor}) and sys._is_gil_enabled(); print(sys.version.split()[0])",
        ],
        env=env,
        stream=False,
        merge_stderr=False,
    )
    exact_ver = ver_out.strip()
    ok(f"Managed interpreter ready: {py_path} (CPython {exact_ver}, GIL enabled)")
    return py_path, exact_ver


def resolve_manifest(filename: str, embedded: str) -> tuple[str, str]:
    search_dirs: list[Path] = []
    env_dir = os.environ.get("DUSKY_STT_DIR")
    if env_dir:
        search_dirs.append(Path(env_dir).expanduser())
    search_dirs.append(SCRIPT_DIR)
    for d in search_dirs:
        candidate = d / filename
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8"), str(candidate)
    return embedded, "embedded defaults"


def list_venv_packages(venv_py: Path, env: dict[str, str]) -> dict[str, str]:
    _, out = run_cmd(
        ["uv", "pip", "list", "--python", str(venv_py), "--format", "json"],
        env=env,
        stream=False,
        merge_stderr=False,
    )
    try:
        items = json.loads(out)
    except json.JSONDecodeError as exc:
        raise PrewarmError(f"Invalid JSON from uv pip list: {exc}\n{out[:400]}") from exc
    return {item["name"].lower(): item["version"] for item in items}


def make_venv(venv_dir: Path, managed_py: Path, env: dict[str, str]) -> Path:
    run_cmd(["uv", "venv", "--python", str(managed_py), str(venv_dir)], env=env)
    return venv_dir / "bin/python"


def write_kokoro_pyproject(work_dir: Path, series: str, ort_line: str | None) -> None:
    deps = [f'    "kokoro-onnx=={KOKORO_ONNX_VERSION}",', '    "numpy>=2.3",']
    if ort_line:
        deps.append(f'    "{ort_line}",')
    override = ""
    if KOKORO_ONNX_VERSION == "0.6.1":
        override = f"""
[[tool.uv.dependency-metadata]]
name = "kokoro-onnx"
version = "{KOKORO_ONNX_VERSION}"
requires-python = ">=3.10"
requires-dist = [
    "espeakng-loader>=0.2.4",
    "phonemizer>=3.4.0",
    "numpy>=2.0.2",
]
"""
    toml = f"""\
[project]
name = "dusky-kokoro"
version = "5.1.2"
requires-python = ">={series}"
dependencies = [
{chr(10).join(deps)}
]

[tool.uv]
package = false
{override}"""
    (work_dir / "pyproject.toml").write_text(toml, encoding="utf-8")


def run_stt_cpu(work_dir: Path, managed_py: Path, env: dict[str, str]) -> ProfileResult:
    content, src = resolve_manifest("requirements.txt", EMBEDDED_STT_CPU_REQS)
    ok(f"manifest source: {src}")
    req = work_dir / "requirements.txt"
    req.write_text(content, encoding="utf-8")
    py = make_venv(work_dir / ".venv", managed_py, env)
    run_cmd(["uv", "pip", "sync", "--python", str(py), "--only-binary", ":all:", str(req)], env=env)
    run_cmd(["uv", "pip", "check", "--python", str(py)], env=env)
    run_cmd([str(py), "-c", "import moonshine_voice, numpy, sounddevice, requests"], env=env, stream=False)
    pkgs = list_venv_packages(py, env)
    return ProfileResult(
        "stt-cpu", "pinned", "OK", len(pkgs),
        headline=f"moonshine={pkgs.get('moonshine-voice', '?')} numpy={pkgs.get('numpy', '?')}",
    )


def run_stt_nvidia(
    work_dir: Path, managed_py: Path, series: str, env: dict[str, str], update: bool, live_gpu: bool
) -> ProfileResult:
    content, src = resolve_manifest("requirements-gpu.txt", EMBEDDED_STT_GPU_REQS)
    ok(f"manifest source: {src}")
    req = work_dir / "requirements-gpu.txt"
    req.write_text(content, encoding="utf-8")
    py = make_venv(work_dir / ".venv", managed_py, env)
    refresh = ["--refresh"] if update else []
    run_cmd(["uv", "pip", "install", "--python", str(py), "--only-binary", ":all:", *refresh, "-r", str(req)], env=env)
    run_cmd(["uv", "pip", "check", "--python", str(py)], env=env)
    run_cmd(
        [
            str(py), "-c",
            "import numpy, huggingface_hub, google.protobuf; "
            "from importlib.metadata import version; assert version('onnx-asr')",
        ],
        env=env,
        stream=False,
    )

    rc, out = run_cmd(
        [
            "uv", "pip", "install", "--python", str(py), "--only-binary", ":all:",
            *refresh, "-r", str(req), f"onnxruntime-gpu=={STT_ORT_GPU_PIN}",
        ],
        env=env,
        check=False,
    )
    pkgs = list_venv_packages(py, env)
    if rc != 0:
        if is_upstream_wheel_error(out, "onnxruntime-gpu"):
            abi = f"cp{series.replace('.', '')}"
            warn(f"onnxruntime-gpu=={STT_ORT_GPU_PIN} has no matching {abi} wheel yet; cached all 13 base pins")
            return ProfileResult(
                "stt-nvidia", "pinned", "PARTIAL_UPSTREAM", len(pkgs),
                headline=f"onnx-asr={pkgs.get('onnx-asr', '?')} cuda={pkgs.get('nvidia-cuda-runtime', '?')}",
                note=f"onnxruntime-gpu=={STT_ORT_GPU_PIN} lacks {abi} wheel upstream",
            )
        raise PrewarmError("\n".join(out.strip().splitlines()[-25:]))

    run_cmd(["uv", "pip", "check", "--python", str(py)], env=env)
    gpu_check = "assert 'CUDAExecutionProvider' in ort.get_available_providers()" if live_gpu else "pass"
    run_cmd([str(py), "-c", f"import onnx_asr, onnxruntime as ort; {gpu_check}"], env=env, stream=False)
    return ProfileResult(
        "stt-nvidia", "pinned", "OK", len(pkgs),
        headline=f"ort-gpu={pkgs.get('onnxruntime-gpu', '?')} cuda={pkgs.get('nvidia-cuda-runtime', '?')}",
    )


def run_kokoro(
    name: str,
    ort_pkg: str,
    ort_spec: str,
    work_dir: Path,
    managed_py: Path,
    series: str,
    env: dict[str, str],
    update: bool,
    live_gpu: bool,
) -> ProfileResult:
    upgrade = ["--upgrade"] if update else []
    abi = f"cp{series.replace('.', '')}"
    write_kokoro_pyproject(work_dir, series, None)
    run_cmd(["uv", "lock", "--python", str(managed_py), *upgrade], env=env, cwd=work_dir)
    run_cmd(["uv", "sync", "--locked", "--python", str(managed_py)], env=env, cwd=work_dir)
    py = work_dir / ".venv/bin/python"
    run_cmd(
        [
            str(py), "-c",
            f"import numpy, phonemizer, espeakng_loader; from importlib.metadata import version; "
            f"assert version('kokoro-onnx') == {KOKORO_ONNX_VERSION!r}",
        ],
        env=env,
        stream=False,
    )

    write_kokoro_pyproject(work_dir, series, ort_spec)
    no_build_ort = ["--no-build-package", ort_pkg]
    for step_cmd in (
        ["uv", "lock", "--python", str(managed_py), *no_build_ort, *upgrade],
        ["uv", "sync", "--locked", "--python", str(managed_py), *no_build_ort],
    ):
        rc, out = run_cmd(step_cmd, env=env, cwd=work_dir, check=False)
        if rc != 0:
            pkgs = list_venv_packages(py, env)
            if is_upstream_wheel_error(out, ort_pkg):
                warn(f"{ort_spec} has no matching {abi} wheel yet; cached kokoro-onnx + phonemizer + numpy")
                return ProfileResult(
                    name, "pinned", "PARTIAL_UPSTREAM", len(pkgs),
                    headline=f"kokoro={pkgs.get('kokoro-onnx', '?')} numpy={pkgs.get('numpy', '?')}",
                    note=f"{ort_pkg} lacks {abi} wheel upstream",
                )
            raise PrewarmError("\n".join(out.strip().splitlines()[-25:]))
    pkgs = list_venv_packages(py, env)
    ort_dists = [k for k in pkgs if k.startswith("onnxruntime")]
    if len(ort_dists) != 1:
        raise PrewarmError(f"Expected exactly 1 onnxruntime distribution, found {ort_dists}")
    if name == "kokoro-cpu":
        run_cmd(["uv", "pip", "check", "--python", str(py)], env=env)
    gpu_check = (
        "assert 'CUDAExecutionProvider' in ort.get_available_providers()"
        if (name == "kokoro-nvidia" and live_gpu)
        else "pass"
    )
    run_cmd(
        [str(py), "-c", f"import kokoro_onnx, espeakng_loader, phonemizer, numpy, onnxruntime as ort; {gpu_check}"],
        env=env,
        stream=False,
    )
    return ProfileResult(
        name, "pinned", "OK", len(pkgs),
        headline=f"kokoro={pkgs.get('kokoro-onnx', '?')} {ort_dists[0]}={pkgs[ort_dists[0]]}",
    )


def run_cuda_toolkit(work_dir: Path, managed_py: Path, env: dict[str, str], update: bool) -> ProfileResult:
    py = make_venv(work_dir / ".venv", managed_py, env)
    refresh = ["--refresh"] if update else []
    run_cmd(
        ["uv", "pip", "install", "--python", str(py), "--only-binary", ":all:", *refresh, *CUDA_TOOLKIT_PACKAGES],
        env=env,
    )
    run_cmd(["uv", "pip", "check", "--python", str(py)], env=env)
    verify_code = f"""
import sysconfig
from importlib.metadata import version
from pathlib import Path
pkgs = {CUDA_TOOLKIT_PACKAGES!r}
for p in pkgs:
    v = version(p)
    assert v != "0.0.1", f"deprecated stub resolved for {{p}}"
nvidia_dir = Path(sysconfig.get_path("purelib")) / "nvidia"
assert nvidia_dir.is_dir() and any(nvidia_dir.rglob("*.so*")), "no CUDA shared libraries installed"
"""
    run_cmd([str(py), "-c", verify_code], env=env, stream=False)
    pkgs = list_venv_packages(py, env)
    cudnn_key = f"nvidia-cudnn-cu{CUDA_MAJOR}"
    return ProfileResult(
        "cuda13-toolkit", "rolling", "OK", len(pkgs),
        headline=f"cuda={pkgs.get('nvidia-cuda-runtime', '?')} cudnn={pkgs.get(cudnn_key, '?')}",
    )


def run_pytorch_cuda(
    work_dir: Path, managed_py: Path, env: dict[str, str], update: bool, live_gpu: bool
) -> ProfileResult:
    py = make_venv(work_dir / ".venv", managed_py, env)
    refresh = ["--refresh"] if update else []
    run_cmd(
        ["uv", "pip", "install", "--python", str(py), "--only-binary", ":all:", *refresh, *PYTORCH_CUDA_PACKAGES],
        env=env,
    )
    run_cmd(["uv", "pip", "check", "--python", str(py)], env=env)
    live_snippet = (
        "assert torch.cuda.is_available(); "
        "x = torch.ones((4, 4), device='cuda'); assert float((x @ x).sum().item()) == 64.0"
        if live_gpu
        else "pass"
    )
    run_cmd(
        [
            str(py), "-c",
            "import torch, torchvision, torchaudio, triton, transformers, accelerate, "
            "safetensors, tokenizers, huggingface_hub, einops; "
            f"assert torch.version.cuda is not None, 'CPU-only torch wheel installed'; {live_snippet}",
        ],
        env=env,
        stream=False,
    )
    pkgs = list_venv_packages(py, env)
    return ProfileResult(
        "pytorch-cuda", "rolling", "OK", len(pkgs),
        headline=f"torch={pkgs.get('torch', '?')} triton={pkgs.get('triton', '?')}",
    )


def run_python_common(work_dir: Path, managed_py: Path, env: dict[str, str], update: bool) -> ProfileResult:
    py = make_venv(work_dir / ".venv", managed_py, env)
    refresh = ["--refresh"] if update else []
    run_cmd(["uv", "pip", "install", "--python", str(py), *refresh, *PYTHON_COMMON_PACKAGES], env=env)
    run_cmd(["uv", "pip", "check", "--python", str(py)], env=env)
    run_cmd(
        [
            str(py), "-c",
            "import numpy, scipy, PIL, soundfile, cffi, psutil, orjson, pydantic, yaml, regex, "
            "httpx, requests, aiohttp, websockets, rich, textual, click, typer, tqdm, platformdirs, "
            "tomli_w, dotenv, watchfiles, setuptools, hatchling, packaging, pytest, IPython",
        ],
        env=env,
        stream=False,
    )
    pkgs = list_venv_packages(py, env)
    return ProfileResult(
        "python-common", "rolling", "OK", len(pkgs),
        headline=f"numpy={pkgs.get('numpy', '?')} scipy={pkgs.get('scipy', '?')} ruff={pkgs.get('ruff', '?')}",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prewarm and update ~/.cache/uv across pinned apps and rolling ML/Python stacks."
    )
    parser.add_argument("--only", help="Comma-separated profile names to run")
    parser.add_argument("--skip", help="Comma-separated profile names to skip")
    parser.add_argument("--hw", choices=("all", "auto", "cpu"), default="all", help="Hardware filter (default: all)")
    parser.add_argument("--no-update", action="store_true", help="Skip python upgrade and --refresh/--upgrade")
    parser.add_argument("--python", default=DEFAULT_PY_SERIES, metavar="SERIES", help=f"Managed Python series (default: {DEFAULT_PY_SERIES})")
    parser.add_argument("--prune", action="store_true", help="Run safe `uv cache prune` after completion")
    parser.add_argument("--list", action="store_true", help="List available profiles and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="Stream full uv output live")
    return parser.parse_args(argv)


def select_profiles(args: argparse.Namespace, has_nvidia_hw: bool) -> tuple[list[tuple[str, str, str, str]], list[ProfileResult]]:
    def parse_names(raw: str | None, flag: str) -> set[str]:
        if not raw:
            return set()
        names = {x.strip() for x in raw.split(",") if x.strip()}
        unknown = names - set(PROFILE_NAMES)
        if unknown:
            die(f"Unknown profile(s) for {flag}: {', '.join(sorted(unknown))} (use --list)")
        return names

    only_set = parse_names(args.only, "--only")
    skip_set = parse_names(args.skip, "--skip")
    selected: list[tuple[str, str, str, str]] = []
    skipped: list[ProfileResult] = []
    for p in PROFILES:
        name, tier, hw, _ = p
        if only_set and name not in only_set:
            continue
        if name in skip_set:
            skipped.append(ProfileResult(name, tier, "SKIPPED", note="--skip"))
        elif hw == "nvidia" and (args.hw == "cpu" or (args.hw == "auto" and not has_nvidia_hw)):
            reason = "--hw cpu" if args.hw == "cpu" else "--hw auto (no NVIDIA GPU)"
            skipped.append(ProfileResult(name, tier, "SKIPPED", note=reason))
        else:
            selected.append(p)
    return selected, skipped


def main(argv: list[str]) -> int:
    global VERBOSE, SCRATCH_ROOT
    args = parse_args(argv)
    VERBOSE = args.verbose

    if args.list:
        print(f"{'PROFILE':<16} {'TIER':<8} {'HW':<7} DESCRIPTION")
        for name, tier, hw, desc in PROFILES:
            print(f"{name:<16} {tier:<8} {hw:<7} {desc}")
        return 0

    if not re.fullmatch(r"\d+\.\d+", args.python):
        die(f"--python must be a MAJOR.MINOR series like {DEFAULT_PY_SERIES}, got: {args.python}")
    if not shutil.which("uv"):
        die("uv not found on PATH")

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _handle_signal)

    lock_fh = acquire_lock()
    try:
        base_env = dict(os.environ)
        base_env.update({
            "PYTHONNOUSERSITE": "1",
            "ORT_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "UV_HTTP_TIMEOUT": "180",
            "UV_HTTP_RETRIES": "5",
        })

        _, uv_ver = run_cmd(["uv", "--version"], env=base_env, stream=False, merge_stderr=False)
        log(f"uv: {uv_ver.strip()}")

        has_nvidia_hw, live_gpu, gpu_desc = probe_nvidia_hardware()
        ok(f"Hardware probe: {gpu_desc}")

        selected, skipped = select_profiles(args, has_nvidia_hw)
        needs_nvidia = any(hw == "nvidia" for _, _, hw, _ in selected)
        min_cache = 8 * GIB if needs_nvidia else 2 * GIB
        warn_cache = 15 * GIB if needs_nvidia else 4 * GIB
        min_scratch = 6 * GIB if needs_nvidia else 2 * GIB

        cache_path, resolved_cache = validate_cache_dir(base_env, min_cache, warn_cache)
        size_before = uv_cache_size_human(resolved_cache, base_env)
        ok(f"uv cache: {cache_path} -> {resolved_cache} (size: {size_before}, free: {fmt_gib(free_bytes(resolved_cache))})")

        scratch_base, is_ram = discover_scratch_dir(min_scratch)
        SCRATCH_ROOT = Path(tempfile.mkdtemp(prefix="uv-prewarm.", dir=scratch_base))
        if os.stat(SCRATCH_ROOT).st_dev != os.stat(resolved_cache).st_dev:
            base_env["UV_LINK_MODE"] = "copy"
        ok(
            f"Scratch root: {SCRATCH_ROOT} ({'tmpfs/RAM' if is_ram else 'disk'}, "
            f"free: {fmt_gib(free_bytes(SCRATCH_ROOT))}, link_mode={base_env.get('UV_LINK_MODE', 'default')})"
        )

        update = not args.no_update
        managed_py, py_ver = bootstrap_managed_python(args.python, update, base_env)
        work_env = dict(base_env)
        work_env["UV_PYTHON_DOWNLOADS"] = "never"
        work_env["UV_PYTHON_PREFERENCE"] = "only-managed"

        results: list[ProfileResult] = list(skipped)
        for name, tier, _hw, desc in selected:
            log(f"Profile {name} ({tier}): {desc}")
            t0 = time.monotonic()
            pdir = SCRATCH_ROOT / name
            pdir.mkdir(parents=True, exist_ok=True)
            try:
                match name:
                    case "stt-cpu":
                        res = run_stt_cpu(pdir, managed_py, work_env)
                    case "stt-nvidia":
                        res = run_stt_nvidia(pdir, managed_py, args.python, work_env, update, live_gpu)
                    case "kokoro-cpu":
                        res = run_kokoro(name, "onnxruntime", f"onnxruntime>={KOKORO_ORT_MIN}", pdir, managed_py, args.python, work_env, update, live_gpu)
                    case "kokoro-nvidia":
                        res = run_kokoro(name, "onnxruntime-gpu", f"onnxruntime-gpu[cuda,cudnn]>={KOKORO_ORT_MIN}", pdir, managed_py, args.python, work_env, update, live_gpu)
                    case "cuda13-toolkit":
                        res = run_cuda_toolkit(pdir, managed_py, work_env, update)
                    case "pytorch-cuda":
                        res = run_pytorch_cuda(pdir, managed_py, work_env, update, live_gpu)
                    case "python-common":
                        res = run_python_common(pdir, managed_py, work_env, update)
                    case _:
                        raise PrewarmError(f"Unhandled profile: {name}")
                res.elapsed = time.monotonic() - t0
                ok(f"{name}: {res.status} ({res.pkg_count} pkgs, {res.elapsed:.1f}s) {res.headline}")
                results.append(res)
            except PrewarmError as exc:
                elapsed = time.monotonic() - t0
                lines = str(exc).splitlines()
                first_line = lines[0] if lines else str(exc)
                warn(f"{name}: FAILED ({elapsed:.1f}s): {first_line}")
                if not VERBOSE and len(lines) > 1:
                    for ln in lines[1:]:
                        print(f"         {ln}", file=sys.stderr, flush=True)
                results.append(ProfileResult(name, tier, "FAILED", elapsed=elapsed, note=first_line))
            finally:
                shutil.rmtree(pdir, ignore_errors=True)

        if args.prune:
            log("Running safe cache prune (uv cache prune)")
            run_cmd(["uv", "cache", "prune"], env=work_env)
            ok("uv cache prune complete")

        size_after = uv_cache_size_human(resolved_cache, work_env)
        order = {n: i for i, n in enumerate(PROFILE_NAMES)}
        results.sort(key=lambda r: order.get(r.name, 999))

        log(f"Summary (CPython {py_ver}, mode={'update' if update else 'no-update'})")
        print(f"  {'PROFILE':<16} {'TIER':<8} {'STATUS':<17} {'PKGS':>4} {'TIME':>6}  HEADLINE")
        for r in results:
            print(f"  {r.name:<16} {r.tier:<8} {r.status:<17} {r.pkg_count:>4} {r.elapsed:>5.1f}s  {r.headline}")

        partials = [r for r in results if r.status == "PARTIAL_UPSTREAM"]
        if partials:
            print("\n  Upstream wheel blockers (non-fatal; will auto-activate once published):")
            for r in partials:
                print(f"    - {r.name}: {r.note}")

        failed = [r for r in results if r.status == "FAILED"]
        if failed:
            print("\n  Failed profiles:", file=sys.stderr)
            for r in failed:
                print(f"    - {r.name}: {r.note}", file=sys.stderr)

        print(f"\n  Cache size: {size_before} -> {size_after}  |  Cache free: {fmt_gib(free_bytes(resolved_cache))}")
        return 1 if failed else 0
    finally:
        _terminate_active()
        _cleanup_scratch()
        lock_fh.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
