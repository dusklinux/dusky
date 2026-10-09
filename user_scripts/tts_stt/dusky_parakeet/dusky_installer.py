#!/usr/bin/env python3
"""Staged Arch installer: Moonshine CPU, optional Parakeet CUDA, Rust UI."""
import argparse
import json
import os
import platform
import re
import shutil
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from dusky_hardware import detect_nvidia, select_gpu

MIN_PYTHON = (3, 15)
# Temporary ABI pin until upstream publishes and we qualify CPython 3.15 wheels.
GPU_PYTHON = "3.14"
MIN_KERNEL = (7, 3)
SCHEMA_VERSION = 3
APP_DIR = Path(os.environ.get("DUSKY_APP_DIR", Path.home() / "contained_apps/uv/dusky_stt")).expanduser()
BIN_DIR = Path.home() / ".local/bin"
UNIT_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user"
UNIT_NAME = "dusky_stt.service"
DEFAULT_STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "dusky-stt"
SOURCE_DIR = Path(__file__).resolve().parent
REQUIRED_SOURCES = ("dusky_main.py", "dusky_worker.py", "dusky_hardware.py", "dusky_trigger.py", "dusky_verify.sh",
                    "README.md", "requirements.txt", "requirements-gpu.txt")
BASE_PACKAGES = ("pipewire", "pipewire-audio", "pipewire-alsa", "pipewire-pulse", "wireplumber",
                 "portaudio", "ffmpeg", "wl-clipboard", "libnotify", "uv", "gtk4", "gtk4-layer-shell", "rust", "pkgconf")
type JsonObject = dict[str, Any]

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
if not sys.stdout.isatty():
    RESET = BOLD = GREEN = RED = YELLOW = ""
VERBOSE = False
OFFLINE = False
LOG_FILE: Path | None = None


class InstallError(RuntimeError):
    pass


def log_step(msg: str) -> None:
    print(f"{BOLD}==> {msg}{RESET}", flush=True)


def log_ok(msg: str) -> None:
    if LOG_FILE:
        with LOG_FILE.open("a") as stream:
            stream.write(msg + "\n")
    if VERBOSE:
        print(f"{GREEN}  ok {RESET}{msg}", flush=True)


def log_warn(msg: str) -> None:
    if LOG_FILE:
        with LOG_FILE.open("a") as stream:
            stream.write("WARNING: " + msg + "\n")
    print(f"{YELLOW}  ** {RESET}{msg}", flush=True)


def report_error(exc: BaseException) -> None:
    message = str(exc)
    if LOG_FILE:
        with LOG_FILE.open("a") as stream:
            stream.write("ERROR: " + message + "\n")
    print(f"Installation failed: {message if VERBOSE else message.splitlines()[0] if message else type(exc).__name__}", file=sys.stderr)
    if LOG_FILE:
        print(f"Log: {LOG_FILE}", file=sys.stderr)


def run(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: float = 3600.0,
    check: bool = True,
    quiet: bool = True,
) -> subprocess.CompletedProcess[str]:
    child_env = dict(os.environ)
    child_env.update(env or {})
    child_env["ORT_DISABLE_TELEMETRY"] = "1"
    if OFFLINE:
        child_env["UV_OFFLINE"] = "1"
        child_env["HF_HUB_OFFLINE"] = "1"
    if LOG_FILE:
        with LOG_FILE.open("a") as stream:
            stream.write("$ " + shlex.join(cmd) + "\n")
    if not quiet and LOG_FILE:
        with LOG_FILE.open("a") as stream:
            with subprocess.Popen(cmd, env=child_env, cwd=cwd, stdout=subprocess.PIPE if VERBOSE else stream,
                                  stderr=subprocess.STDOUT, text=True) as proc:
                reader: threading.Thread | None = None
                if VERBOSE:
                    def show_output() -> None:
                        assert proc.stdout is not None
                        for line in proc.stdout:
                            stream.write(line)
                            stream.flush()
                            print(line, end="", flush=True)
                    reader = threading.Thread(target=show_output, daemon=True)
                    reader.start()
                try:
                    started = time.monotonic()
                    while True:
                        try:
                            code = proc.wait(timeout=min(20.0, timeout))
                            break
                        except subprocess.TimeoutExpired:
                            elapsed = time.monotonic() - started
                            if elapsed >= timeout:
                                proc.kill()
                                proc.wait()
                                raise InstallError(f"Command timed out after {timeout:g}s: {cmd[0]}")
                            if sys.stdout.isatty():
                                print(f"\r  Working… {int(elapsed)}s", end="", flush=True)
                    if sys.stdout.isatty() and time.monotonic() - started >= 20:
                        print("\r" + " " * 32 + "\r", end="", flush=True)
                except BaseException:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()
                    raise
                finally:
                    if reader is not None:
                        reader.join(timeout=5)
        res = subprocess.CompletedProcess(cmd, code)
    else:
        res = subprocess.run(cmd, env=child_env, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
        output = (res.stdout or "") + (res.stderr or "")
        if LOG_FILE:
            with LOG_FILE.open("a") as stream:
                stream.write(output)
        if VERBOSE and output:
            print(output, end="", flush=True)
    if check and res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()
        if not detail and LOG_FILE:
            with LOG_FILE.open("rb") as stream:
                stream.seek(max(0, LOG_FILE.stat().st_size - 4000))
                detail = stream.read().decode(errors="replace")
        raise InstallError(f"Command failed ({res.returncode}): {Path(cmd[0]).name}\n{detail[-4000:]}")
    return res


def assert_runtime() -> None:
    missing = [name for name in REQUIRED_SOURCES if not (SOURCE_DIR / name).is_file()]
    missing += [name for name in ("indicator/Cargo.toml", "indicator/Cargo.lock", "indicator/src/main.rs") if not (SOURCE_DIR / name).is_file()]
    if missing:
        raise InstallError("Incomplete source: " + ", ".join(missing))
    if os.geteuid() == 0:
        raise InstallError("Run as your desktop user, not root")
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    if release.get("ID", "").strip('"') != "arch" and "arch" not in release.get("ID_LIKE", ""):
        raise InstallError("Arch Linux required")
    match = re.prefixmatch(r"(\d+)\.(\d+)", platform.release())
    if not match or tuple(map(int, match.groups())) < MIN_KERNEL:
        raise InstallError("Linux 7.3+ required")
    if sys.version_info < MIN_PYTHON or not sys._is_gil_enabled():
        raise InstallError("GIL-enabled Python 3.15+ required; select its interpreter explicitly")
    if not os.environ.get("XDG_RUNTIME_DIR") or not os.environ.get("WAYLAND_DISPLAY"):
        raise InstallError("Run inside the Hyprland Wayland user session")
    version = run(["systemctl", "--version"]).stdout.splitlines()[0].split()[1]
    if int(version) < 262:
        raise InstallError("systemd 262+ required")
    log_ok(f"Linux {platform.release()}, Python {sys.version.split()[0]}, systemd {version}")


def install_pacman_packages(skip: bool) -> None:
    packages = BASE_PACKAGES
    missing = [p for p in packages if subprocess.run(["pacman", "-Qq", p], capture_output=True).returncode]
    if missing and (OFFLINE or skip):
        raise InstallError("Preinstall system dependencies: " + " ".join(missing))
    if missing:
        # Inherit the terminal for sudo's prompt; never put a password in code.
        subprocess.run(["sudo", "pacman", "-S", "--needed", "--noconfirm", *missing], check=True)


def install_python_environment(stage: Path) -> Path:
    env = {"UV_PYTHON_DOWNLOADS": "never", "PYTHONNOUSERSITE": "1"}
    run(["uv", "venv", "--relocatable", "--python", sys.executable, str(stage / ".venv")], env=env)
    py = stage / ".venv/bin/python"
    run(["uv", "pip", "sync", "--python", str(py), str(stage / "requirements.txt")], env=env, quiet=False)
    run(["uv", "pip", "check", "--python", str(py)], env=env)
    return py


def choose_backend(args: argparse.Namespace, previous: JsonObject, cards: list[dict]) -> str:
    recommended = "NVIDIA Parakeet + Moonshine CPU fallback" if cards else "Moonshine CPU"
    message = f"Recommendation: {recommended}" + (f" ({cards[0]['name']})" if cards else "")
    log_ok(message)
    if not VERBOSE:
        print(message, flush=True)
    if args.backend:
        return args.backend
    default = previous.get("backend", "auto")
    if args.yes or not sys.stdin.isatty():
        return default
    choices = {"1": "auto", "2": "cpu", "3": "nvidia"}
    print("1) Auto: use NVIDIA when installed and available; otherwise CPU\n"
          "2) CPU: Moonshine\n3) NVIDIA: Parakeet, with CPU fallback on failure")
    default_key = next(key for key, value in choices.items() if value == default)
    while True:
        answer = input(f"Backend [{default_key}]: ").strip() or default_key
        if answer in choices:
            return choices[answer]


def find_gpu_wheel(explicit: str | None, previous: JsonObject) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise InstallError(f"GPU wheel not found: {path}")
        return path
    cached = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "dusky-stt/gpu-wheels"
    tag = "cp" + GPU_PYTHON.replace(".", "")
    wheels = list(cached.glob(f"onnxruntime_gpu-*{tag}*.whl"))
    if previous.get("gpu_wheel"):
        old = Path(previous["gpu_wheel"]).expanduser()
        if old.is_file() and tag in old.name:
            wheels.append(old)
    return max(wheels, key=lambda path: path.stat().st_mtime) if wheels else None


def install_gpu_environment(stage: Path, wheel: Path | None = None) -> Path:
    env = {"UV_PYTHON_DOWNLOADS": "never" if OFFLINE else "automatic", "PYTHONNOUSERSITE": "1"}
    run(["uv", "venv", "--relocatable", "--managed-python", "--python", GPU_PYTHON,
         str(stage / ".venv-gpu")], env=env)
    py = stage / ".venv-gpu/bin/python"
    command = ["uv", "pip", "install", "--python", str(py), "-r", str(stage / "requirements-gpu.txt")]
    if wheel:
        command += [str(wheel)]
    else:
        command += ["onnxruntime-gpu==1.31.0"]
    run(command, env=env, quiet=False)
    run([str(py), "-c", "import sys; assert sys.version_info[:2] == (3,14) and sys._is_gil_enabled()"], env=env)
    run(["uv", "pip", "check", "--python", str(py)], env=env)
    return py


def prepare_parakeet(py: Path, explicit: str | None, previous: JsonObject) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    old = previous.get("parakeet", {})
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    candidates = [Path(old["model_dir"])] if old.get("model_dir") else []
    candidates.append(data / "dusky-stt/models/nemo-parakeet-tdt-0.6b-v2")
    for path in candidates:
        if (path / "encoder-model.int8.onnx").is_file():
            return path.resolve()
    code = """
import sys,json
from huggingface_hub import snapshot_download
path=snapshot_download('istupakov/parakeet-tdt-0.6b-v2-onnx',
    allow_patterns=['config.json','vocab.txt','nemo*.onnx','*.int8.onnx'],
    local_files_only=sys.argv[1]=='offline')
print(json.dumps(path))
"""
    result = run([str(py), "-c", code, "offline" if OFFLINE else "online"], timeout=5400)
    return Path(json.loads(result.stdout.strip().splitlines()[-1])).resolve()


def prepare_model(py: Path, model: str, explicit: str | None, previous: JsonObject) -> Path:
    # The downloader is invoked only during online setup. The daemon/worker
    # opens a local directory directly, so runtime can never fetch a model.
    if explicit:
        return Path(explicit).expanduser().resolve()
    if previous.get("model") == model and previous.get("model_dir"):
        path = Path(previous["model_dir"]).expanduser()
        if path.is_dir():
            return path.resolve()
    code = """
import json,sys
from pathlib import Path
from moonshine_voice import ModelArch,get_model_for_language
from moonshine_voice.download import find_model_info
from moonshine_voice.download_file import get_cache_dir
arch = getattr(ModelArch,sys.argv[1].upper())
if sys.argv[2] == 'offline':
    info = find_model_info('en',arch)
    path = get_cache_dir() / info['download_url'].removeprefix('https://')
    if not path.is_dir():
        raise RuntimeError('Requested English model is not cached; use --model-dir or run online setup once')
else:
    path,_ = get_model_for_language('en',arch)
print(json.dumps(str(path)))
"""
    result = run([str(py), "-c", code, model, "offline" if OFFLINE else "online"], quiet=True)
    return Path(json.loads(result.stdout.strip().splitlines()[-1])).resolve()


def verify_worker(py: Path, stage: Path, config_path: Path, backend: str = "cpu") -> JsonObject:
    result = run([str(py), str(stage / "dusky_worker.py"), "--config", str(config_path), "--self-test", "--backend", backend],
                 env={} if backend == "nvidia" else {"CUDA_VISIBLE_DEVICES": "-1"}, cwd=stage, timeout=300)
    try:
        report = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise InstallError("Worker returned no valid self-test report") from exc
    if not isinstance(report, dict) or report.get("ok") is not True:
        raise InstallError(f"Worker self-test failed: {report}")
    return report


def build_indicator(stage: Path) -> None:
    target = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "dusky-stt/rust-target"
    command = ["cargo", "build", "--release", "--locked", "--manifest-path", str(stage / "indicator/Cargo.toml"), "--target-dir", str(target)]
    if OFFLINE:
        command.append("--offline")
    run(command, quiet=False)
    shutil.copy2(target / "release/dusky-rec-indicator", stage / "dusky-rec-indicator")


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="English clipboard dictation; Moonshine CPU and optional NVIDIA Parakeet")
    p.add_argument("--backend", "--hardware", choices=("auto", "cpu", "nvidia"))
    p.add_argument("--gpu-wheel", help="Optional CPython 3.14 GPU wheel; normally use the published prebuilt runtime")
    p.add_argument("--gpu-model-dir", help="Existing English Parakeet v2 int8 ONNX model directory")
    p.add_argument("--gpu-device", type=int, help="NVIDIA index; otherwise choose a supported GPU automatically")
    p.add_argument("--model", choices=("small_streaming", "medium_streaming"))
    p.add_argument("--model-dir", help="Existing native Moonshine .ort model directory")
    p.add_argument("--input-device", help="PortAudio device name; auto selects the default")
    p.add_argument("--state-dir")
    p.add_argument("--offline", action="store_true", help="Use preinstalled packages and cached wheels, Rust crates and model")
    p.add_argument("--skip-pacman", action="store_true")
    p.add_argument("--no-systemd", action="store_true", help="Skip cleanup of a legacy STT service (for isolated builds)")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--yes", "-y", action="store_true", help="Accept the default backend recommendation without a menu")
    p.add_argument("--uninstall", action="store_true")
    return p.parse_args(argv)


def uninstall() -> int:
    stop_recording_process()
    subprocess.run(["systemctl", "--user", "disable", "--now", UNIT_NAME], check=False)
    for path in (UNIT_DIR / UNIT_NAME, BIN_DIR / "dusky_trigger", BIN_DIR / "dusky_verify"):
        path.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)
    print("Removed application; models, caches and transcripts retained")
    return 0


def deploy_stage(stage: Path, *, manage_service: bool = True, was_active: bool = False) -> Path | None:
    log_step("Deploying application")
    stop_recording_process()
    if manage_service and (UNIT_DIR / UNIT_NAME).exists():
        stopped = subprocess.run(["systemctl", "--user", "stop", UNIT_NAME], capture_output=True, check=False)
        if was_active and stopped.returncode != 0:
            raise InstallError("Could not stop the running service before deployment")
    backup: Path | None = None
    try:
        if APP_DIR.exists():
            backup = APP_DIR.parent / f"dusky-stt.backup-{time.time_ns()}"
            APP_DIR.rename(backup)
        stage.rename(APP_DIR)
    except BaseException as exc:
        if backup and backup.exists() and not APP_DIR.exists():
            backup.rename(APP_DIR)
        if manage_service and was_active:
            run(["systemctl", "--user", "start", UNIT_NAME])
        if not isinstance(exc, Exception):
            raise
        raise InstallError(f"Atomic rename failed: {exc}") from exc
    log_ok("Deployed.")
    return backup


def install_entrypoints(*, manage_service: bool = True) -> None:
    log_step("Installing on-demand entry points")
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    for name, command in (("dusky_trigger", 'exec "$app_dir/.venv/bin/python" "$app_dir/dusky_trigger.py" "$@"'),
                          ("dusky_verify", 'exec bash "$app_dir/dusky_verify.sh" "$@"')):
        target = BIN_DIR / name
        target.write_text('#!/usr/bin/env bash\n'
            'app_dir=${DUSKY_APP_DIR:-' + shlex.quote(str(APP_DIR)) + '}\n'
            'export DUSKY_APP_DIR="$app_dir"\n' + command + '\n', encoding="utf-8")
        target.chmod(0o755)
    if manage_service and (UNIT_DIR / UNIT_NAME).exists():
        run(["systemctl", "--user", "disable", "--now", UNIT_NAME])
        (UNIT_DIR / UNIT_NAME).unlink()
        run(["systemctl", "--user", "daemon-reload"])


def stop_recording_process() -> None:
    python = APP_DIR / ".venv/bin/python"
    trigger = APP_DIR / "dusky_trigger.py"
    if python.is_file() and trigger.is_file():
        run([str(python), str(trigger), "--kill"], env={"DUSKY_APP_DIR": str(APP_DIR)}, timeout=30)


def rollback(backup: Path | None, entries: dict[Path, tuple[bytes, int] | None],
             *, manage_service: bool, was_active: bool, was_enabled: bool) -> None:
    if manage_service:
        subprocess.run(["systemctl", "--user", "stop", UNIT_NAME], capture_output=True, check=False)
        if not was_enabled:
            subprocess.run(["systemctl", "--user", "disable", UNIT_NAME], capture_output=True, check=False)
    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)
    if backup is not None and backup.exists():
        backup.rename(APP_DIR)
    for path, saved in entries.items():
        if saved is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(saved[0])
            path.chmod(saved[1])
    if manage_service:
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, check=False)
        if was_enabled:
            subprocess.run(["systemctl", "--user", "enable", UNIT_NAME], capture_output=True, check=False)
        if was_active:
            subprocess.run(["systemctl", "--user", "start", UNIT_NAME], capture_output=True, check=False)


def main(argv: list[str]) -> int:
    global VERBOSE, OFFLINE, LOG_FILE
    args = parse_arguments(argv)
    VERBOSE, OFFLINE = args.verbose, args.offline
    if args.uninstall:
        return uninstall()
    LOG_FILE = DEFAULT_STATE_DIR / "install.log"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text("", encoding="utf-8")
    assert_runtime()
    previous_config = APP_DIR / "config.json"
    if not previous_config.exists() and APP_DIR == Path.home() / "contained_apps/uv/dusky_stt":
        # Preserve settings when relocating an existing installation. Leave its
        # files intact until the user has verified the new installation.
        previous_config = Path.home() / ".local/lib/dusky-stt/config.json"
    previous = json.loads(previous_config.read_text()) if previous_config.exists() else {}
    if previous and previous.get("schema_version") not in (2, 3):
        raise InstallError("Unrecognized existing configuration schema")
    cards = detect_nvidia()
    backend = choose_backend(args, previous, cards)
    gpu = select_gpu(cards, args.gpu_device)
    wheel = find_gpu_wheel(args.gpu_wheel, previous)
    use_gpu = gpu is not None and (backend != "cpu" or args.gpu_wheel is not None)
    if backend == "nvidia" and not gpu:
        raise InstallError("No supported NVIDIA GPU/driver found for the GPU smoke test; choose --backend cpu")
    model = args.model or (previous.get("model") if previous.get("schema_version") == 3 else "small_streaming")
    install_pacman_packages(args.skip_pacman)
    APP_DIR.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".dusky-stage-", dir=APP_DIR.parent))
    backup = None
    deployed = False
    entries = {p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) if p.exists() else None
               for p in (BIN_DIR / "dusky_trigger", BIN_DIR / "dusky_verify", UNIT_DIR / UNIT_NAME)}
    was_active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", UNIT_NAME], capture_output=True).returncode == 0
    was_enabled = subprocess.run(["systemctl", "--user", "is-enabled", "--quiet", UNIT_NAME], capture_output=True).returncode == 0
    if args.no_systemd and was_active:
        shutil.rmtree(stage)
        raise InstallError("Stop dusky_stt.service before installing with --no-systemd")
    try:
        log_step("Building a fresh environment (download caches retained)")
        for name in REQUIRED_SOURCES:
            shutil.copy2(SOURCE_DIR / name, stage / name)
        shutil.copytree(SOURCE_DIR / "indicator", stage / "indicator", ignore=shutil.ignore_patterns("target"))
        py = install_python_environment(stage)
        log_step(f"Preparing English {model}")
        model_dir = prepare_model(py, model, args.model_dir, previous)
        config = {
            "schema_version": 3, "hardware": "cpu", "backend": backend, "model": model, "model_dir": str(model_dir),
            "input_device": previous.get("input_device") if args.input_device is None else
                            None if args.input_device == "auto" else args.input_device,
            "state_dir": str(Path(args.state_dir or previous.get("state_dir", DEFAULT_STATE_DIR)).expanduser().resolve()),
            "notifications": previous.get("notifications", True), "output_mode": "clipboard",
            "idle_timeout_seconds": 90, "finalize_timeout_seconds": 120, "max_inflight_requests": 1,
            "worker_python": ".venv/bin/python", "worker_script": "dusky_worker.py",
        }
        cfg = stage / "config.json"
        gpu_report = None
        if use_gpu:
            try:
                log_step(f"Preparing isolated UV Python {GPU_PYTHON} Parakeet CUDA environment")
                gpu_py = install_gpu_environment(stage, wheel)
                if wheel:
                    config["gpu_wheel"] = str(wheel)
                config["parakeet"] = {
                    "model": "nemo-parakeet-tdt-0.6b-v2",
                    "model_dir": str(prepare_parakeet(gpu_py, args.gpu_model_dir, previous)),
                    "gpu_device": args.gpu_device,
                    "gpu_mem_limit_mb": 512,
                }
                cfg.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
                gpu_report = verify_worker(gpu_py, stage, cfg, "nvidia")
            except (InstallError, OSError, ValueError, subprocess.SubprocessError) as exc:
                if backend != "auto":
                    raise
                log_warn(f"GPU setup failed; CPU fallback remains usable: {str(exc).splitlines()[0]}")
                config.pop("parakeet", None);config.pop("gpu_wheel", None)
                if (stage / ".venv-gpu").exists():
                    shutil.rmtree(stage / ".venv-gpu")
                use_gpu = False
        cfg.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        report = verify_worker(py, stage, cfg)
        if gpu_report:
            report["gpu"] = gpu_report
        log_step("Building native Rust recording indicator")
        build_indicator(stage)
        (stage / "manifest.json").write_text(json.dumps({"schema_version": 3, "python": sys.version,
            "kernel": platform.release(), "self_test": report, "installed": int(time.time())}, indent=2) + "\n", encoding="utf-8")
        backup = deploy_stage(stage, manage_service=not args.no_systemd, was_active=was_active)
        deployed = True
        install_entrypoints(manage_service=not args.no_systemd)
        if not args.no_systemd:
            run([str(BIN_DIR / "dusky_trigger"), "--status", "--json"], env={"DUSKY_APP_DIR": str(APP_DIR)}, timeout=30)
        if backup:
            shutil.rmtree(backup)
        print(f"Ready · backend {backend} · English {model}" + (" + NVIDIA Parakeet" if use_gpu else "") +
              f" · clipboard only\nConfig: {APP_DIR / 'config.json'}\nLog: {LOG_FILE}")
        return 0
    except BaseException:
        if deployed:
            rollback(backup, entries, manage_service=not args.no_systemd, was_active=was_active, was_enabled=was_enabled)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("Installation interrupted; previous application retained", file=sys.stderr)
        sys.exit(130)
    except (InstallError, OSError, ValueError, subprocess.SubprocessError) as exc:
        report_error(exc)
        sys.exit(1)
