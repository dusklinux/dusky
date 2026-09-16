# dusky llama-server

OpenAI-compatible local inference server backed by [llama.cpp](https://github.com/ggml-org/llama.cpp),
installed by `user_scripts/arch_setup_scripts/scripts/399_ml_runtimes.sh`.

## What runs where

| Piece | Path |
|---|---|
| systemd user unit | `~/.config/systemd/user/llama-server.service` |
| options file | `~/.config/dusky/llama_server/llama-server.env` |
| model directory | `~/.local/share/dusky-llama/models` |
| endpoint | `http://127.0.0.1:8081/v1` |

## Usage

```bash
# drop a GGUF into the model dir (any Hugging Face GGUF repo works)
mkdir -p ~/.local/share/dusky-llama/models

# point the server at it (edit and restart)
$EDITOR ~/.config/dusky/llama_server/llama-server.env
systemctl --user start llama-server

curl http://127.0.0.1:8081/v1/chat/completions \
  -d '{"model":"local","messages":[{"role":"user","content":"hello dusky"}]}'
```

The unit is deliberately **not** enabled at install time: an idle inference
server still pins VRAM. Enable it with `systemctl --user enable llama-server`
if you want it at every login.

## GPU acceleration

`399_ml_runtimes.sh` installs the vendor-matched build automatically:

- NVIDIA → `llama-cpp` + `ggml-cuda` (extra) — CUDA
- AMD → `llama-cpp` + `ggml-hip` (extra, needs `396_amd_rocm_stack.sh`) — ROCm/HIP
- otherwise → `llama-cpp` + `ggml-vulkan` (extra) — Vulkan

`--n-gpu-layers 999` in `LLAMA_ARGS` offloads all layers to the GPU; remove it
to fall back to CPU.