# Dusky English dictation: CPU and NVIDIA

Press the hotkey to record; press again to stop. Dusky then transcribes locally,
saves the result, copies it to the Wayland clipboard and sends a notification.
It never types, pastes or presses application keys. Pause/resume and stop buttons
are provided by a native Rust GTK4 layer-shell indicator that takes no keyboard
focus. Rounded waveform bars animate on display frames from 50 ms status samples. Its audio meter uses the capture daemon; there is no second microphone tap.

## Install

Keep this directory, including `requirements.txt` and `indicator/`, together:

```bash
python3.15 dusky_installer.py
# Unattended: retain an existing preference, otherwise use Auto:
python3.15 dusky_installer.py --yes
# After dependencies, model and Rust crates have been cached:
python3.15 dusky_installer.py --yes --offline
```

Use the actual Python 3.15+ interpreter installed on your ISO. `/usr/bin/python`
on the development machine is still 3.14; the installer does not download Python.
Requires Linux 7.3+, systemd 262+, Hyprland/Wayland, PipeWire, PortAudio, ffmpeg,
wl-clipboard, libnotify, uv, GTK4 4.12+, gtk4-layer-shell, Rust and pkgconf. Missing system
packages are installed using pacman during online setup. CPU-only installs need
no CUDA, cuDNN, PyTorch or Python ONNX Runtime. Moonshine's wheel contains its
native C++/ONNX CPU inference runtime. That runtime itself supports CPU only; see
[execution-provider documentation](https://github.com/moonshine-ai/moonshine/blob/main/docs/execution-providers.md).
The optional Parakeet worker temporarily uses **UV-managed Python 3.14**, pinned
by `GPU_PYTHON` in the installer, with the published ONNX Runtime GPU 1.31.0 wheel.
Its CUDA 13.0/cuDNN runtime libraries are installed inside `.venv-gpu`; no compiler
toolkit or system CUDA/cuDNN installation is needed. CUDA never loads into the
microphone/control process. The recorder and CPU worker still use Python 3.15+.

The app and both UV environments live under `~/contained_apps/uv/dusky_stt/`
(`.venv` for capture/CPU, `.venv-gpu` for NVIDIA). Setup imports settings from an
existing `~/.local/lib/dusky-stt/config.json` when the new location has no config;
the old installation files are retained. Launchers use the new location; setup removes the legacy STT service. Shared model/package caches and transcripts retain their
existing paths.

Installation builds a fresh relocatable `.venv`, checks the model on silence,
compiles the Rust indicator using `Cargo.lock`, and stages everything before
replacement. Failure during deployment restores the previous app, launchers and any migrated legacy unit.
Old virtual environments are removed after successful replacement. UV, Rust and
model caches are retained. Existing microphone, transcript-directory and
notification settings migrate; typing stays disabled. An installed backend can be
changed without rebuilding environments using `dusky_trigger --backend MODE`.

Recording runs on demand without an STT service or an autostart process.
Setup stops an existing recording and removes the old `dusky_stt.service` unit.
`--no-systemd` skips legacy unit cleanup for isolated builds; it does not install
a service. Existing model environments and cached artifacts remain reusable.

## Models

English only. The default is **Moonshine Small Streaming**, quantized, using
Moonshine Voice 0.1.5. Small balances CPU latency and accuracy across machines.
Medium is available through the same backend:

```bash
python3.15 dusky_installer.py --model medium_streaming
python3.15 dusky_installer.py --model small_streaming --offline
```

NVIDIA mode adds **English Parakeet TDT 0.6B v2, int8**, alongside Moonshine for
CPU fallback. Setup detects supported NVIDIA GPUs, reports its recommendation,
and offers Auto / CPU / NVIDIA. `--backend` (also `--hardware`) selects directly;
`--yes` accepts the default without a menu. Auto recommends Parakeet when a
supported NVIDIA GPU and a verified GPU runtime are available, otherwise Moonshine.
CPU-only installation avoids GPU dependencies. `--gpu-device INDEX` overrides
automatic GPU selection; runtime discovers the selected card's UUID.

The CUDA 13 runtime requires Turing or newer (compute capability 7.5+) and a
compatible NVIDIA driver. Older NVIDIA cards use Moonshine CPU. Parakeet uses
20-second maximum blocks, a 512 MiB per-session arena ceiling, bounded cuDNN
workspace and no CUDA graphs. Existing larger arena settings are also clamped
to 512 MiB. This targets cards with 2 GiB VRAM, but ONNX Runtime's arena option
is not a total device-memory limit; CUDA contexts and other allocations are
additional. Actual peak memory must be qualified on the ISO's supported GPUs.

The installer requests Python 3.14 explicitly and requires UV-managed Python,
so `/usr/local/bin/python` shadowing the distro interpreter does not select the
RC build for GPU inference. Online setup downloads a managed interpreter if
needed; offline GPU setup needs that interpreter already installed and cached
GPU wheels/model files. It never changes the system Python or your PATH.

```bash
# Use your existing Python 3.15+ interpreter to run setup:
python3.15 dusky_installer.py --backend auto --yes
# The GPU worker uses prebuilt wheels; no source compilation is required.
```

This temporary Python 3.14 exception applies only to the GPU worker. Switch it to
3.15 after compatible ONNX Runtime wheels are published and qualified. A Python
3.15 final release alone does not establish package-wheel availability. When the
temporary `/usr/local` RC build is removed, recreate the CPU environment using the
distro's stable Python 3.15+ by rerunning setup; virtual environments are not
migrated between interpreter installations.

The staged GPU smoke test profiles actual CUDA nodes. Auto mode can finish a CPU
installation if GPU setup fails. Explicit
NVIDIA installation reports GPU setup failures and retains the previous app.
`--gpu-model-dir` uses existing Parakeet files; setup also reuses the old Dusky
English v2 int8 model directory when present. No model download occurs at runtime.

Auto and NVIDIA modes fall back to Moonshine CPU if the GPU becomes unavailable,
initialization fails, inference fails, the worker crashes, or a GPU request times
out. The original recording is retried, so partial GPU text is never published.
Status shows the actual backend and the reason for fallback. A GPU failure selects CPU for that job. The next invocation retries the configured preference.

The name “Streaming” describes the model architecture. Capture performs no
recognition; the worker processes recorded audio only after Stop. Intermediate
hypotheses are disabled. Moonshine detects speech and flushes the final words at
end-of-input. Short silent recordings leave the existing clipboard unchanged.

Models download only during installation into Moonshine's XDG cache. Offline
setup opens cached files directly; `--model-dir DIR` accepts an existing native
`.ort` model directory. Hugging Face `.safetensors` are not runtime model files.
Offline setup also requires cached wheels and resolver metadata, cached Cargo
crates, and preinstalled system packages. A corrupt/incompatible model fails the
staged smoke test before deployment.

Published eight-dataset English WER averages are 7.84% Small and 6.65% Medium,
measured on floating-point references; they are not accuracy guarantees for the
shipped quantized models or your voice. Upstream reports quantized LibriSpeech
clean WER of 2.61% Small and 2.17% Medium. See
[models](https://moonshine-voice.readthedocs.io/en/latest/models/available-models/)
and [accuracy](https://moonshine-voice.readthedocs.io/en/latest/models/accuracy/).

## Use

```bash
dusky_trigger                 # Record / stop and copy
dusky_trigger --start
dusky_trigger --pause          # Pause / resume
dusky_trigger --stop           # Stop capture; during processing, cancel
dusky_trigger --file ~/audio.m4a --wait
dusky_trigger --status         # Does not start a recording process
dusky_trigger --unload         # Release the recognition worker
dusky_trigger --backend cpu    # Select Moonshine, while idle
dusky_trigger --backend auto   # Prefer installed NVIDIA Parakeet; fall back to CPU
dusky_trigger --backend nvidia # Select installed Parakeet; still recover on CPU
dusky_verify                  # Model/dependencies/runtime checks
dusky_trigger --logs
```

Existing `--push` bindings continue to work. `--realtime` reports that live typing
was removed. A hotkey press during finalization reports busy; it never queues a
surprise recording. Audio-file jobs can be cancelled with `--stop`.
After stopping microphone capture, the pill stays visible with processing
progress and elapsed time until the result is saved and copied. While no block
has finished, it shows an animated PROCESSING state; percentages represent
completed audio blocks, not an estimated time remaining. Short Parakeet jobs
may finish in one indivisible block. FINISHING covers final decoding/publication.
Its stop button
cancels transcription; pause is disabled during processing. Long recordings
take longer to process: a six-minute recording may take minutes on a slow CPU.
Progress tracks audio fed to the recognizer; final speech decoding and clipboard
publication can still take time at 99%.

```ini
bind = SUPER, I, exec, dusky_trigger
```

Recordings use an automatically removed disk spool under the configured
`state_dir` (~110 MiB/hour of 16 kHz mono PCM). This bounds capture RAM, preserves
continuous speech without artificial chunk cuts, and excludes paused audio.
Moonshine feeds bounded five-second blocks through its native segmentation API
and retains no completed audio. Stop explicitly flushes pending speech. Parakeet
uses bounded blocks up to 20 seconds, preferring quiet boundaries; continuous
speech can still reach the maximum block boundary. Long
transcripts still require memory proportional to their text/segment metadata.
Input overflows, capture failures and clipboard failures are reported.
Transcripts and exact job results remain under `state_dir/transcripts` and
`state_dir/jobs`. No speech leaves your computer.

Clipboard ownership uses `wl-copy`'s background process, which survives the
recording process exiting, including desktops without a clipboard manager. It
holds the text only, never a speech model. Silence preserves the current clipboard.

Super+I launches a temporary recording process directly. Capture and the pill
start first; NVIDIA discovery and model loading run in the background. Using
CUDA naturally wakes an idle GPU; STT does not alter D3/D0 states or Hyprland's
primary GPU selection. An already active GPU simply loads the model.
Parakeet primes kernels with silent audio before reporting `worker_ready`.
Recorded speech is processed only after Stop; a take shorter than loading still
waits for the model. Workers do not idle-exit during a long take.
After each job, the worker is reaped and the recorder exits. No model remains in
RAM or VRAM. A GPU driving the display can remain awake for the desktop; STT
only releases its own allocations. Exact job files let `--wait` survive exit.
Concurrent hotkeys share one process, and the next invocation waits for an
exiting predecessor before launching. A disconnected recorder channel also
terminates its worker, including during model loading/native inference.

The Moonshine runtime uses `MOONSHINE_ORT_SINGLE_THREAD=1`; NumPy's BLAS thread
count is also one. Parakeet retains up to eight inference threads and disables
ONNX intra-op spinning. The Rust pill, pause, timer and processing UI are unchanged.
`--kill` cancels and exits the current recorder. `--logs` follows `runtime.log`
in the configured state directory. `--status` never launches a process.

```bash
python3.15 dusky_installer.py --uninstall # Keeps caches and transcripts
~/contained_apps/uv/dusky_stt/.venv/bin/python -m unittest discover -s tests -v
```

Development tests are not deployed. `DUSKY_APP_DIR` overrides the application
path; `DUSKY_CONFIG` overrides the control client's config. XDG cache and state paths are discovered at install time. Ship cached artifacts for
the ISO's actual Python version and architecture; x86-64 and ARM64 wheels differ.
