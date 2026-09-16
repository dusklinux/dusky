# Dusky ML/GPU Stack

Vendor-matched machine-learning tooling for Arch + Hyprland, wired into the
installer's numbered-script convention. Everything here is hardware-gated and
idempotent -- scripts no-op cleanly on the wrong vendor.

## The vendor truth table

There is no universal GPU API on Linux. Each vendor has exactly one native
compute stack, and dusky installs that one:

| Vendor | Native compute | Graphics/video | Notes |
|---|---|---|---|
| NVIDIA | **CUDA** | NVENC/NVDEC via `nvidia-utils` | CUDA is NVIDIA-proprietary |
| AMD | **ROCm/HIP** | VCN via mesa/VA-API | ROCm is AMD-only |
| Intel | oneAPI/SYCL, OpenVINO | QSV via intel-media-driver | covered by existing scripts |
| any | **Vulkan** (ggml-vulkan) | Vulkan | universal fallback (works on AMD without ROCm too) |

- CUDA-on-AMD shims (ZLUDA) and ROCm-on-NVIDIA are **out of scope**: fragile,
  perpetually broken by driver updates.
- **Metal does not exist on Linux** (macOS only).
- **Apple Silicon (M1-M4) is out of scope**: it requires the Asahi/ARM port of
  Arch; GPUs there speak Mesa/Vulkan only -- no CUDA, no ROCm.
- **Intel Macs (2012-2020) work**: they are ordinary x86_64 PCs
  (`409_intel_mac_quirks.sh` handles Wi-Fi/keyboard/sensors).

## Scripts

| Script | Level | Installs | Approx. size |
|---|---|---|---|
| `396_amd_rocm_stack.sh` | U (sudo) | ROCm/HIP userspace: `rocm-hip-runtime rocblas hipblas miopen-hip rccl hip-runtime-amd rocm-smi-lib rocminfo`; user → `render`+`video` groups; `/etc/profile.d/10-rocm.sh` with `HSA_OVERRIDE_GFX_VERSION` when needed | ~1.1 GiB download, ~25 GiB installed (measured) |
| `397_cuda_toolkit.sh` | U (sudo) | `cuda` + `cudnn` (+ `--tools`: nsight) for compiling/profiling CUDA code | ~2.7 GiB download, ~5.8 GiB installed (measured) |
| `398_nvidia_boot_config.sh` | S | mkinitcpio `MODULES` + `mkinitcpio -P` + `nvidia_drm.modeset=1` for systemd-boot **and** GRUB (automates what `380` used to print) | KBs |
| `399_ml_runtimes.sh` | U | Ollama vendor variant (`ollama-cuda`/`ollama-rocm`), llama.cpp (`llama-cpp`) with the vendor ggml backend (`ggml-cuda`/`ggml-hip`/`ggml-vulkan`, all from extra), `llama-server` user unit (OpenAI API @ 127.0.0.1:8081), `--lmstudio` opt-in (`lmstudio-bin`) | ~0.5-1 GiB |
| `400_ml_training_stack.sh` | U | uv venv `~/.local/lib/dusky-ml/.venv`: PyTorch (CUDA/ROCm/CPU matched), `transformers peft trl datasets accelerate`, Unsloth (+bitsandbytes on NVIDIA), `dusky-ml-doctor` | ~2.5 GiB CUDA / ~2 GiB ROCm |
| `401_ml_serving_stack.sh` | U | vLLM in a separate venv (`.venv-serving`) + `dusky-vllm` user unit (not auto-started) | ~3 GiB |
| `402_ml_notebook_lab.sh` | U | JupyterLab + ipykernel (dusky-ml venv), VS Code, `~/Projects/ml-template` scaffold | ~0.5 GiB |
| `409_intel_mac_quirks.sh` | S | Broadcom Wi-Fi (`broadcom-wl-dkms`), `hid_apple fnmode=2`, lm_sensors/applesmc | small |

Shared detection lives in `scripts/lib/gpu_detect.sh` (sysfs + lspci + VM
guard, same contract as `380_nvidia_open_source.sh`); every script above
sources it.

## Enabling

The ML scripts are registered **commented-out** in `profiles/01_main.toml`
(they pull multi-GiB downloads; the default profile stays lean). Uncomment
what you want, or run directly:

```bash
~/user_scripts/arch_setup_scripts/scripts/396_amd_rocm_stack.sh --auto
~/user_scripts/arch_setup_scripts/scripts/399_ml_runtimes.sh --auto --lmstudio
```

Every script here is opt-in (commented out in `profiles/01_main.toml`),
including `398_nvidia_boot_config.sh` -- it edits mkinitcpio.conf and kernel
command lines, so boot/system modifications only ever run when explicitly
enabled. It is guarded end to end: a failed `mkinitcpio -P` restores the
config backup instead of dying half-applied.

## What runs where (after install)

| Component | Path / endpoint |
|---|---|
| Ollama (LLM side panel backend) | `ollama.service`, API @ 127.0.0.1:11434 |
| llama.cpp server | `systemctl --user start llama-server`, API @ 127.0.0.1:8081/v1 |
| vLLM server | `systemctl --user start dusky-vllm` (configure `~/.config/dusky/vllm/dusky-vllm.env` first) |
| Training venv | `~/.local/lib/dusky-ml/.venv` |
| Health check | `dusky-ml-doctor` |
| Project template | `cp -r ~/Projects/ml-template ~/Projects/<name>` |

## Existing AI tools (already in dusky) get GPU paths too

- **Kokoro TTS** already had the full CUDA/ROCm/OpenVINO/CPU matrix.
- **Parakeet STT** previously fell back to CPU on AMD; the installer now
  uses Arch's `python-onnxruntime-rocm` through a system-site-packages
  worker venv (same source as Kokoro's `--rocm-source arch`) when a usable
  ROCm stack is present -- AMD's own wheel index stops at ROCm 7.0/cp312
  and cannot serve current Arch, so it is deliberately not used, verifies the
  `ROCmExecutionProvider` actually binds, and bakes
  `HSA_OVERRIDE_GFX_VERSION` into the deployed unit when the gfx target needs
  it. No ROCm? The reliable CPU path is unchanged.
- **LLM side panel** (`auto_config.sh`) now hints the vendor-matched Ollama
  package instead of the CPU one.

## Verification

```bash
bash -n <script>          # syntax
shellcheck <script>       # lint
dusky-ml-doctor           # runtime health (on the target machine)
```

Static checks pass on any host; the scripts themselves only run on Arch
(pacman/paru/systemd are hard prerequisites, matching every other dusky
setup script).