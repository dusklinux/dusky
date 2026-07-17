```markdown
# Dusky STT v8.1 BLEEDING EDGE - Unified Default, Realtime Typing, & D3-cold Safe

**Stack July 2026:** Python 3.14.6 only, Arch bleeding, systemd 261, Native CUDA 13.0 / Driver 610.43.03, 3050 Ti 4GB, 12700H 64GB RAM, PipeWire, Wayland, uv, Parakeet Unified 5.91% WER, wtype, ffmpeg (soxr)

### What's New in v8.1 BLEEDING EDGE

- **Native CUDA 13.0 Support:** Fully embraces CUDA 13 as the recommended default for NVIDIA drivers >=580 (like 610.43.03).
- **Python 3.14 & Torch 2.11.0 Sync:** Explicitly matches `torch==2.11.0` and `torchaudio==2.11.0` to ensure stable compatibility with Python 3.14.6, avoiding version skew crashes.
- **Word-Level Suffix Engine:** Realtime typing now utilizes a Word-Level Longest Common Prefix (LCP) algorithm. This fixes character-slicing glitches (e.g., typing "r" instead of "car") when the rolling ASR context updates previous words.
- **Hallucination & VAD Gates:** Introduces a strict blocklist for common ASR hallucinations (e.g., "Thank you for watching") and gates the realtime evaluation window so it doesn't process ambient noise floors.
- **Atomic File Protections & IPC:** Re-engineered the trigger pipe and PID management to use strict `O_EXCL` and `O_NOFOLLOW` file handles, permanently closing TOCTOU symlink vulnerabilities.
- **Systemd Exponential Backoff:** Implements native systemd 260+ throttling (`RestartSteps=5`, `RestartMaxDelaySec=30`) to prevent silent resource spinning on backend failures.
- **Worker Environment Unmasking:** Safely purges `CUDA_VISIBLE_DEVICES` in the child process so the worker can bind to the GPU, while keeping the main daemon strictly CPU-isolated for D3-cold compliance.

### Files (this is all you need)

```text
dusky_installer.py - Rich installer, auto pacman+uv, pure pip CUDA 13/12 resolution
dusky_main.py      - CPU-only main, word-level realtime typing, blocking audio thread
dusky_worker.py    - GPU worker, dynamic LD_LIBRARY_PATH discovery, D3-cold cleanup
dusky-trigger      - Toggle, secure atomic FIFO trigger, systemd-enforced env tracking
dusky-stt.service  - systemd user service (Type=exec), exponential backoff, memory clamps
README.md          - This file

```

### Install

Place all 6 files into a single directory (e.g., `~/Downloads/dusky-v8.1`), then run:

```bash
cd ~/Downloads/dusky-v8.1
chmod +x dusky-trigger

uv python install 3.14.6
uv run --python 3.14.6 dusky_installer.py

# Installer will:
# - Check pacman deps and auto install missing: pipewire wl-clipboard wtype ffmpeg libnotify yad uv base-devel
# - Ask hardware: 1=CUDA13 pip STABLE (RECOMMENDED for driver 610+), 2=CUDA12 pip LEGACY, 3=AMD, 4=CPU
# - Ask model: [1] unified-en-0.6b DEFAULT 5.91% WER, [2] v2 EN 6.05% WER, [3] v3 25 langs 6.34%
# - Setup isolated venv at ~/contained_apps/uv/dusky_stt_v2/.venv via `uv pip` (no seed pollution)
# - Discover pip CUDA paths and generate `.env` for systemd LD_LIBRARY_PATH injection
# - Prefetch models via `hf_xet` (auto-disabled if RAM < 64GB)
# - Copy files, install trigger to ~/.local/bin/dusky-trigger, and service to ~/.config/systemd/user/

# Enable service
systemctl --user daemon-reload
loginctl enable-linger $USER
systemctl --user enable --now dusky-stt.service
journalctl --user -u dusky-stt -f

# Bind hotkey to: ~/.local/bin/dusky-trigger

```

### Usage - Realtime Default

**Realtime typing into focused window (neovim, notepad):**

```bash
# Focus neovim / text editor, then press hotkey
dusky-trigger  # shows "Streaming Suffix Engine - Focus target input element."
# Speak. It types live into the focused window via wtype.
# "hello world this is realtime"
dusky-trigger  # stop

# Force push-to-talk (paste at end)
dusky-trigger --push  

# Force realtime (if config defaulted to push)
dusky-trigger --realtime

```

How realtime works:

* Main daemon (CPU-only) captures mic via sounddevice on a dedicated thread, chunking every 1.2s into a rolling context window.
* Evaluates the audio segment against a Silero VAD gate; if speech is present, submits to the GPU worker.
* Worker transcribes the phrase with the Parakeet unified model.
* Main diffs the newly returned phrase against the already typed text using a word-level array, isolates the new suffix, blocks hallucinations, and executes `wtype` to type directly into the focused application.

**Podcast / Long File (High Quality / No OOM):**

```bash
dusky-trigger --file ~/Downloads/podcast.mp3
# Transcodes via ffmpeg using soxr:precision=28 to 16k mono.
# VAD splits, incremental save to ~/Transcripts/DuskySTT/

```

**Status/logs:**

```bash
dusky-trigger --status
dusky-trigger --logs
dusky-trigger --restart
dusky-trigger --kill

```

### D3-Cold / Battery Verification

Main daemon never imports CUDA (<50MB RAM). Worker is spawned on demand and explicitly unmasks its GPU view. It exits after 30s idle -> Garbage collection runs -> Torch cache empties -> CUDA context is destroyed -> GPU enters `D3cold` at 0.5W.

Check state:

```bash
cat /sys/bus/pci/devices/0000:01:00.0/power/runtime_status  # suspended
cat /sys/bus/pci/devices/0000:01:00.0/power_state           # D3cold

```

### Troubleshooting

* **No wtype / Virtual Keyboard blocked:** `sudo pacman -S wtype` (Wayland). Ensure your Wayland compositor allows virtual keyboard protocols.
* **Audio failing (xrun/PipeWire):** Ensure `pipewire-pulse` is running. Check `pavucontrol`.
* **Worker fails to load ONNX / CUDA:** Run `dusky-trigger --logs`. The new installer ensures pure pip isolation via the `.env` file, but if `onnxruntime` crashes, verify that PyTorch `2.11.0` was successfully installed during the setup phase.
* **Service fails to start:** `systemctl --user status dusky-stt -l`. The service uses `Type=exec` to accurately report crash loops and will exponential-backoff after 5 failures.

### Uninstall

```bash
systemctl --user disable --now dusky-stt.service
rm -rf ~/contained_apps/uv/dusky_stt_v2 ~/.config/systemd/user/dusky-stt.service $XDG_RUNTIME_DIR/dusky_stt ~/Transcripts/DuskySTT
rm ~/.local/bin/dusky-trigger

```

```

```
