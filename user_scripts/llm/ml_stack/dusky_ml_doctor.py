#!/usr/bin/env python3
#d: Diagnose the dusky ML stack (GPU, runtimes, services)

"""dusky-ml-doctor -- one-shot health check for the dusky ML/GPU stack.

Installed into ~/.local/bin by 400_ml_training_stack.sh; run with the dusky-ml
venv python to also probe PyTorch:

    ~/.local/lib/dusky-ml/.venv/bin/python ~/.local/bin/dusky-ml-doctor

Checks (each independent, failures never abort the report):
  * torch      -- installed? CUDA (name, VRAM) or ROCm/HIP visible?
  * onnxruntime -- execution providers available
  * ollama     -- service state + API reachability
  * llama.cpp  -- llama-server binary + user unit
  * disk       -- footprint of ~/.local/lib/dusky-ml
Exit code is always 0; the report is the point.
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

GREEN, YELLOW, RED, BOLD, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[1m", "\033[0m"
ML_HOME = Path.home() / ".local" / "lib" / "dusky-ml"


def ok(msg: str) -> None:
    print(f"  {GREEN}ok{RESET}   {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}warn{RESET} {msg}")


def bad(msg: str) -> None:
    print(f"  {RED}fail{RESET} {msg}")


def section(name: str) -> None:
    print(f"\n{BOLD}:: {name}{RESET}")


def check_torch() -> None:
    section("PyTorch")
    try:
        import torch  # type: ignore
    except Exception:
        bad("not installed in this interpreter (run 400_ml_training_stack.sh)")
        return
    ok(f"torch {torch.__version__}")
    if getattr(torch, "cuda", None) and torch.cuda.is_available():
        for i in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(i)
            total_gib = props.total_memory / 1024**3
            ok(f"CUDA device {i}: {props.name} ({total_gib:.1f} GiB, capability {props.major}.{props.minor})")
    elif torch.version.hip:
        warn("ROCm build detected but no device usable -- check 'rocminfo' and the render group")
        gfx = os.environ.get("HSA_OVERRIDE_GFX_VERSION", "")
        warn(f"HIP {torch.version.hip}; HSA_OVERRIDE_GFX_VERSION={gfx or 'unset'}")
    elif torch.cuda.is_available() is False and torch.version.cuda:
        warn("CUDA build but driver reports no device -- is nvidia-smi happy?")
    else:
        warn("CPU-only torch (no CUDA device, no HIP)")


def check_onnxruntime() -> None:
    section("onnxruntime")
    try:
        import onnxruntime as ort  # type: ignore
    except Exception:
        bad("not installed in this interpreter")
        return
    providers = ort.get_available_providers()
    ok(f"providers: {', '.join(providers)}")
    gpu_eps = [p for p in providers if p not in ("CPUExecutionProvider",)]
    if not gpu_eps:
        warn("CPUExecutionProvider only -- GPU EP absent")


def service_state(unit: str, user: bool = True) -> str:
    cmd = ["systemctl", *(["--user"] if user else []), "is-active", unit]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return res.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def check_ollama() -> None:
    section("Ollama")
    if not shutil.which("ollama"):
        bad("ollama binary not found (run 399_ml_runtimes.sh)")
        return
    variant = "ollama"
    for pkg in ("ollama-cuda", "ollama-rocm", "ollama"):
        try:
            subprocess.run(["pacman", "-Qq", pkg], capture_output=True, timeout=5, check=True)
            variant = pkg
            break
        except Exception:
            continue
    ok(f"binary present (package: {variant})")
    state = service_state("ollama.service", user=False)
    (ok if state == "active" else warn)(f"system ollama.service: {state}")
    if state != "active":
        state = service_state("ollama.service", user=True)
        (ok if state == "active" else warn)(f"user ollama.service: {state}")
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as resp:
            models = [m.get("name") for m in json.loads(resp.read()).get("models", [])]
        ok(f"API reachable; models: {', '.join(models) if models else '(none pulled yet)'}")
    except Exception:
        warn("API not reachable on 127.0.0.1:11434")


def check_llama_cpp() -> None:
    section("llama.cpp")
    llama_server = shutil.which("llama-server")
    if llama_server:
        ok(f"llama-server at {llama_server}")
    else:
        bad("llama-server not on PATH (run 399_ml_runtimes.sh)")
        return
    state = service_state("llama-server.service")
    (ok if state == "active" else warn)(f"user llama-server.service: {state} (start: systemctl --user start llama-server)")


def check_disk() -> None:
    section("disk footprint")
    if not ML_HOME.exists():
        warn(f"{ML_HOME} does not exist (training stack not provisioned)")
        return

    def du(path: Path) -> int:
        total = 0
        for f in path.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                pass
        return total

    gib = du(ML_HOME) / 1024**3
    ok(f"{ML_HOME}: {gib:.1f} GiB total")


def main() -> None:
    print(f"{BOLD}dusky-ml-doctor{RESET} -- ML stack health check")
    print(f"  interpreter: {sys.version.split()[0]} ({sys.executable})")
    check_torch()
    check_onnxruntime()
    check_ollama()
    check_llama_cpp()
    check_disk()
    print()


if __name__ == "__main__":
    main()