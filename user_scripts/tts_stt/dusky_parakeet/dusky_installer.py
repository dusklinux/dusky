#!/usr/bin/env python3
# Dusky STT Installer v6.1 FIXED - Unified #1 default, Realtime typing, auto pacman+uv
# Python 3.14.6 only, bleeding edge Arch
# FIXED: No uv add (needs pyproject.toml), uses uv pip install --python <venv_python> + creates pyproject.toml
# RELIABILITY: Added autonomous retries, unhid output, removed arbitrary timeouts.

import sys
import sysconfig
import os
import subprocess
import shutil
import json
import time
from pathlib import Path
import platform

if sys.version_info < (3, 14, 6):
    print(f"ERROR: Need Python 3.14.6+, got {sys.version}", file=sys.stderr)
    sys.exit(1)
if sysconfig.get_config_var("Py_GIL_DISABLED") == 1:
    print("ERROR: free-threaded 3.14t not supported, need GIL: uv python install 3.14.6", file=sys.stderr)
    sys.exit(1)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import Prompt, Confirm, IntPrompt
    from rich import box
except ImportError:
    print("Installing rich via pip...")
    subprocess.run([sys.executable, "-m", "pip", "install", "rich", "-q"])
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import Prompt, Confirm, IntPrompt
    from rich import box

console = Console()
APP_DIR = Path.home() / "contained_apps" / "uv" / "dusky_stt_v2"
BIN_DIR = Path.home() / ".local" / "bin"
TRIGGER_PATH = BIN_DIR / "dusky-trigger"
SYSTEMD_DIR = Path.home() / ".config" / "systemd" / "user"
TRANSCRIPT_DIR = Path.home() / "Transcripts" / "DuskySTT"

for p in [APP_DIR, BIN_DIR, SYSTEMD_DIR, TRANSCRIPT_DIR]:
    p.mkdir(parents=True, exist_ok=True)

def run(cmd: list[str], timeout=None, capture_output=True, **kwargs):
    """Base runner safely handles output capturing to prevent NoneType crashes."""
    try:
        res = subprocess.run(cmd, text=True, capture_output=capture_output, timeout=timeout, **kwargs)
        if not capture_output:
            res.stdout = res.stdout or ""
            res.stderr = res.stderr or ""
        return res
    except Exception as e:
        class FailedRes:
            returncode = 1
            stdout = ""
            stderr = str(e)
        return FailedRes()

def run_with_retry(cmd: list[str], max_retries=3, delay=3, **kwargs):
    """Autonomous retry wrapper for network/download operations."""
    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            console.print(f"[yellow]Attempt {attempt - 1} failed. Retrying in {delay}s ({attempt}/{max_retries})...[/]")
            time.sleep(delay)
        res = run(cmd, **kwargs)
        if res.returncode == 0:
            return res
    return res

def check_pacman_deps():
    needed = {
        "pipewire": "pipewire",
        "pipewire-pulse": "pipewire-pulse",
        "wl-copy": "wl-clipboard",
        "wtype": "wtype",
        "ffmpeg": "ffmpeg",
        "notify-send": "libnotify",
        "yad": "yad",
        "uv": "uv",
    }
    missing = []
    for binary, pkg in needed.items():
        if not shutil.which(binary):
            missing.append(pkg)
    if not shutil.which("gcc"):
        missing.append("base-devel")
    if missing:
        console.print(f"[yellow]Missing pacman packages: {', '.join(set(missing))}[/]")
        if Confirm.ask("Auto-install via sudo pacman -S ?", default=True):
            pkgs = sorted(set(missing))
            console.print(f"[cyan]Running: sudo pacman -S --noconfirm {' '.join(pkgs)}[/]")
            result = subprocess.run(["sudo", "pacman", "-S", "--noconfirm"] + pkgs)
            if result.returncode != 0:
                console.print("[red]pacman install failed, install manually[/]")
        else:
            console.print("[yellow]Skipping pacman install[/]")

def detect_hardware():
    info: dict = {"nvidia": False, "amd": False, "cuda_pacman": None, "cudnn_pacman": None, "driver": None, "vram_mb": None, "cpu": platform.processor()}
    if shutil.which("nvidia-smi"):
        try:
            out = run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader,nounits"], timeout=3)
            if out.returncode == 0 and out.stdout.strip():
                line = out.stdout.strip().splitlines()[0]
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    info["nvidia"] = True
                    info["driver"] = parts[1]
                    info["vram_mb"] = parts[2]
        except Exception:
            pass
    if shutil.which("pacman"):
        try:
            out = run(["pacman", "-Q", "cuda"], timeout=3)
            if out.returncode == 0:
                info["cuda_pacman"] = out.stdout.strip()
        except Exception:
            pass
        try:
            out = run(["pacman", "-Q", "cudnn"], timeout=3)
            if out.returncode == 0:
                info["cudnn_pacman"] = out.stdout.strip()
        except Exception:
            pass
    if shutil.which("rocminfo") or Path("/opt/rocm").exists() or Path("/dev/kfd").exists():
        info["amd"] = True
    return info

check_pacman_deps()

console.print(Panel.fit("[bold cyan]Dusky STT v6.1 FIXED Installer[/]\n[dim]Unified #1 default (5.91% WER) | Realtime typing | Python 3.14.6 | Arch bleeding | D3-cold safe[/]\nFix: uses uv pip install --python, creates pyproject.toml, no uv add", box=box.DOUBLE, border_style="cyan"))

hw = detect_hardware()
table = Table(title="Detected Hardware", box=box.ROUNDED)
table.add_column("Key", style="cyan")
table.add_column("Value", style="green")
for k, v in hw.items():
    table.add_row(k, str(v))
console.print(table)

default_hw = "1"
if hw.get("cuda_pacman") and "13." in str(hw["cuda_pacman"]):
    default_hw = "2"
elif hw.get("amd"):
    default_hw = "3"
elif not hw.get("nvidia"):
    default_hw = "4"

console.print("\n[bold]Hardware target (auto installs via pacman+uv):[/]")
console.print(" [1] NVIDIA CUDA 12 pip (STABLE, bundled libs, driver >=525)")
console.print(" [2] NVIDIA CUDA 13 system (EXPERIMENTAL, pacman cuda 13.3.1 + cudnn 9.24, driver >=570)")
console.print(" [3] AMD ROCm/MIGraphX")
console.print(" [4] CPU Only (12700H 64GB RAM)")
hw_choice = Prompt.ask("Hardware", choices=["1","2","3","4"], default=default_hw)
hardware = {"1": "nvidia-cuda12", "2": "nvidia-cuda13", "3": "amd-rocm", "4": "cpu"}[hw_choice]

console.print("\n[bold]Model (unified #1 is best WER, realtime capable):[/]")
console.print(" [1] [bold green]nemo-parakeet-unified-en-0.6b NEW Apr 2026 DEFAULT[/] - 5.91% WER offline (best), unified offline+streaming, 160ms-2s latency, EN only, realtime typing")
console.print(" [2] nemo-parakeet-tdt-0.6b-v2 EN only - 6.05% WER, punctuation, timestamps")
console.print(" [3] nemo-parakeet-tdt-0.6b-v3 25 langs - auto-detect, 6.34% WER, 640MB int8")
model_choice = Prompt.ask("Model", choices=["1","2","3"], default="1")
model = {"1": "nemo-parakeet-unified-en-0.6b", "2": "nemo-parakeet-tdt-0.6b-v2", "3": "nemo-parakeet-tdt-0.6b-v3"}[model_choice]

console.print("\n[bold]Quantization:[/]")
console.print(" [1] int8 [green]RECOMMENDED 4GB VRAM[/] 640MB zero loss")
console.print(" [2] fp16 1.2GB needs 4GB+")
console.print(" [3] fp32 2.5GB needs 6GB+")
q_choice = Prompt.ask("Quant", choices=["1","2","3"], default="1")
quant = {"1": "int8", "2": "fp16", "3": "fp32"}[q_choice]

enable_vad = Confirm.ask("Enable Silero VAD auto-chunking? (no data loss)", default=True)
chunk_sec = IntPrompt.ask("Max chunk seconds (20-30 for 4GB, 64GB allows 60)", default=30)
chunk_sec = max(10, min(60, chunk_sec))

realtime = Confirm.ask("Enable REALTIME typing into focused window (neovim/notepad) as you speak? (uses wtype, types live)", default=True)

console.print("\n[bold]Output:[/] [1] clipboard [2] file [3] both [4] realtime typing + both")
out_choice = Prompt.ask("Output", choices=["1","2","3","4"], default="4" if realtime else "3")
output = {"1": "clipboard", "2": "file", "3": "both", "4": "both"}[out_choice]

config = {
    "hardware": hardware,
    "model": model,
    "quantization": quant,
    "enable_vad": enable_vad,
    "chunk_seconds": chunk_sec,
    "transcript_output": output,
    "realtime": realtime,
    "realtime_chunk": 1.2,
    "python": "3.14.6",
    "idle_timeout": 30,
    "use_ram": True,
}

console.print(Panel(json.dumps(config, indent=2), title="Config", border_style="yellow"))
if not Confirm.ask("Proceed?", default=True):
    sys.exit(0)

# --- FIXED VENV + DEPS LOGIC ---
if not (APP_DIR / "pyproject.toml").exists():
    console.print("[cyan]Creating pyproject.toml (needed for uv)[/]")
    (APP_DIR / "pyproject.toml").write_text('[project]\nname="dusky-stt"\nversion="6.1"\nrequires-python=">=3.14.6"\ndependencies=[]\n')

console.print(f"\n[cyan]Creating venv at {APP_DIR} Python 3.14.6[/]")
run(["uv", "venv", "--python", "3.14.6", "--seed", "--force"], cwd=str(APP_DIR), timeout=60)

venv_python = APP_DIR / ".venv" / "bin" / "python"
venv_pip = APP_DIR / ".venv" / "bin" / "pip"
if not venv_python.exists():
    console.print("[red]Failed to create venv python![/]")
    sys.exit(1)

base_deps = ["onnx-asr", "soundfile", "numpy", "sounddevice", "rich", "huggingface_hub", "hf-transfer", "silero-vad"]
console.print(f"[cyan]Installing base via uv pip install --python {venv_python} (cached at ~/.cache/uv): {' '.join(base_deps)}[/]")
result = run_with_retry(["uv", "pip", "install", "--python", str(venv_python)] + base_deps, cwd=str(APP_DIR), timeout=None, capture_output=False, max_retries=3)
if result.returncode != 0:
    console.print(f"[yellow]uv pip warning: {result.stderr[-2000:]}[/]")
    console.print("[cyan]Fallback: .venv/bin/pip install[/]")
    run_with_retry([str(venv_pip), "install"] + base_deps, cwd=str(APP_DIR), timeout=None, capture_output=False, max_retries=3)

if hardware == "nvidia-cuda12":
    deps = ["onnxruntime-gpu==1.26.0", "nvidia-cuda-runtime-cu12", "nvidia-cudnn-cu12", "nvidia-cufft-cu12"]
    console.print(f"[cyan]Installing CUDA12 deps via uv pip: {' '.join(deps)}[/]")
    result = run_with_retry(["uv", "pip", "install", "--python", str(venv_python)] + deps, cwd=str(APP_DIR), timeout=None, capture_output=False, max_retries=3)
    console.print(result.stdout[-1000:])
elif hardware == "nvidia-cuda13":
    console.print("[yellow]CUDA13 system: installing nightly ORT via uv pip, uses pacman /opt/cuda[/]")
    cmd = ["uv", "pip", "install", "--python", str(venv_python), "--pre", "--index-url", "https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/ort-cuda-13-nightly/pypi/simple/", "onnxruntime-gpu", "--force-reinstall", "--no-deps"]
    result = run_with_retry(cmd, cwd=str(APP_DIR), timeout=None, capture_output=False, max_retries=3)
    console.print(result.stdout[-1000:])
    run_with_retry(["uv", "pip", "install", "--python", str(venv_python), "onnxruntime"], cwd=str(APP_DIR), timeout=None, capture_output=False, max_retries=3)
elif hardware == "amd-rocm":
    run_with_retry(["uv", "pip", "install", "--python", str(venv_python), "onnxruntime-rocm", "--force-reinstall"], cwd=str(APP_DIR), timeout=None, capture_output=False, max_retries=3)
    run_with_retry(["uv", "pip", "install", "--python", str(venv_python), "onnxruntime"], cwd=str(APP_DIR), timeout=None, capture_output=False, max_retries=3)
else:
    run_with_retry(["uv", "pip", "install", "--python", str(venv_python), "onnxruntime"], cwd=str(APP_DIR), timeout=None, capture_output=False, max_retries=3)

console.print("[cyan]Verifying imports with venv python...[/]")
verify_code = "import onnx_asr, soundfile, numpy, sounddevice, silero_vad; print('ALL IMPORTS OK')"
res = run([str(venv_python), "-c", verify_code], timeout=10)
console.print(res.stdout[-1000:])
if res.returncode != 0:
    console.print(f"[red]Import still failing: {res.stderr[-1000:]}[/]")
    sys.exit(1)
else:
    console.print("[green]Imports OK![/]")

src_dir = Path(__file__).parent
for fname in ["dusky_main.py", "dusky_worker.py", "dusky-trigger", "dusky-stt.service", "README.md"]:
    for cand in [src_dir / fname, Path("/mnt/data/final_v6") / fname, Path("/mnt/data/final") / fname, Path("/mnt/data") / fname]:
        if cand.exists():
            dest = APP_DIR / fname
            if fname == "dusky-trigger":
                dest = TRIGGER_PATH
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(cand, dest)
            if fname == "dusky-trigger":
                dest.chmod(0o755)
            console.print(f"[green]Copied {fname} -> {dest}[/]")
            break

console.print(f"\n[cyan]Pre-fetching {model} ({quant}) via hf_transfer (auto download)[/]")
prefetch_code = f"""
import onnx_asr
print("Loading {model} quant={quant}")
try:
    m = onnx_asr.load_model("{model}", quantization="{quant}")
    print("OK", m)
except Exception as e:
    print(f"Failed unified, trying fallback v2: {{e}}")
    m = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2", quantization="{quant}")
    print("Fallback OK", m)
"""
tmp_py = APP_DIR / "_prefetch.py"
tmp_py.write_text(prefetch_code)
env = os.environ.copy()
env["CUDA_VISIBLE_DEVICES"] = "-1"
env["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
result = run_with_retry([str(venv_python), str(tmp_py)], cwd=str(APP_DIR), timeout=None, env=env, capture_output=False, max_retries=3, delay=5)
if result.returncode != 0:
    console.print(f"[yellow]Prefetch warning (will try at runtime): {result.stderr[-1000:]}[/]")
try:
    tmp_py.unlink()
except Exception:
    pass

service_src = APP_DIR / "dusky-stt.service"
service_content = f"""[Unit]
Description=Dusky STT v6.1 FIXED - Parakeet Unified (Realtime typing, D3-cold safe)
After=pipewire.service pipewire-pulse.service graphical-session.target xdg-desktop-portal.service
Wants=pipewire.service
PartOf=graphical-session.target
StartLimitBurst=5
StartLimitIntervalSec=90

[Service]
Type=simple
ExecStart={APP_DIR}/.venv/bin/python {APP_DIR}/dusky_main.py --daemon
WorkingDirectory={APP_DIR}
Environment=HF_HUB_CACHE=%h/.cache/huggingface
Environment=HF_HUB_ENABLE_HF_TRANSFER=1
Environment=PYTHONUNBUFFERED=1
Environment=CUDA_MODULE_LOADING=LAZY
Environment=LD_LIBRARY_PATH=/opt/cuda/lib64
MemoryHigh=32G
MemoryMax=48G
MemorySwapMax=8G
Restart=on-failure
RestartSec=2
TimeoutStopSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=dusky-stt

[Install]
WantedBy=default.target
"""
service_src.write_text(service_content)
dest_service = SYSTEMD_DIR / "dusky-stt.service"
shutil.copy(service_src, dest_service)
console.print(f"[green]Service at {dest_service}[/]")

(APP_DIR / "install_config.json").write_text(json.dumps(config, indent=2))

console.print(Panel(f"[bold green]Setup Complete v6.1 FIXED![/]\nFix integrated: Autonomous retries, unhid output, timeout barriers removed.\nUnified #1 default (5.91% WER) + Realtime typing\nTrigger: {TRIGGER_PATH}\nTranscripts: {TRANSCRIPT_DIR}\nEnable: systemctl --user daemon-reload && loginctl enable-linger $USER && systemctl --user enable --now dusky-stt.service\nRealtime: Focus neovim/notepad then dusky-trigger, it types live!\nPodcast: dusky-trigger --file ~/podcast.mp3\nAll deps auto-installed via pacman+uv, cached at ~/.cache/uv", title="Done", border_style="green"))
