# LM Studio + Obsidian AI Writing Setup (Text Generator & Smart Composer)

All relevant server parameters are located under LM Studio's **Developer / Local Server** setup tab.

---

## 1. Automated Setup via Python 3.15 Script

The automated setup script is located at:
`~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py`

It automates hardware detection (NVIDIA RTX 3050 Ti Mobile 4GB VRAM vs. Intel Iris Xe iGPU / CPU fallback), starts the LM Studio server with CORS enabled, loads the configured GGUF model, installs and configures the Obsidian plugins, caches release binaries for offline usage, and injects `/mnt/media/downloads/llm/instructions.md`.

### Usage Commands:

```bash
# Standard automated run (auto-detects hardware and loads default model)
~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py

# Check hardware VRAM/RAM, server status, active models, and plugin status
~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py --status

# Dry-run: preview all actions without modifying files or starting processes
~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py --dry-run

# Switch to a specific model (unloads existing model first to protect 4GB VRAM)
~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py --model qwen3.5-4b
~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py --model nanbeige4.2-3b
~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py --model lfm3-1.7b
~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py --model huihui-qwen3.6-35b-a3b

# Force CPU-only inference (--gpu off) regardless of GPU hardware
~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py --cpu-only

# Specify exact GPU offload ratio (e.g. 0.5 for 50%, off for CPU, max for full GPU)
~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py --gpu-offload 0.5

# Force an immediate unload and reload of the active model with new placement
~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py --force-reload

# Update Obsidian plugin configs and templates without touching running LM Studio server
~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py --skip-server
```

Configuration file:
`~/user_scripts/llm/obsidian_ai_writing/config.toml`

### Choosing GPU vs. Partial Offload vs. CPU Placement (`config.toml` or CLI)

You can control model placement either permanently in `~/user_scripts/llm/obsidian_ai_writing/config.toml` or on-the-fly via CLI flags:

1. **Via `config.toml`**:
   - **Global CPU-Only Mode (ignore NVIDIA GPU completely)**: Set `mode = "cpu"` under `[hardware]`.
   - **Choose Active Model**: Set `active_model = "qwen3.5-4b"` (or `"huihui-qwen3.6-35b-a3b"`, `"nanbeige4.2-3b"`, `"lfm3-1.7b"`, or `"auto"`) under `[models]`.
   - **Per-Model Placement**: Under `[models.catalog."<model-name>"]`, set `preferred_gpu_offload`:
     - `"max"` (or `"gpu"`): Full GPU VRAM offload.
     - `"0.15"` / `"0.5"` (or `"15%"` / `"50%"`): Partial layer offload split between NVIDIA VRAM and system RAM.
     - `"off"` (or `"cpu"`): Pure CPU + system RAM execution (zero NVIDIA VRAM used).

2. **Via CLI Flags (automatically unloads & reloads the model with new placement)**:
   ```bash
   # Move the current model off the NVIDIA GPU onto pure CPU immediately
   ~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py --cpu-only

   # Run Qwen 3.5 4B purely on CPU without touching the NVIDIA GPU
   ~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py -m qwen3.5-4b --gpu-offload off

   # Run the 21.7 GB 35B MoE model with 15% GPU offload + 85% CPU RAM
   ~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py -m huihui-qwen3.6-35b-a3b --gpu-offload 0.15

   # Force an immediate unload + reload of the active model
   ~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py --force-reload
   ```

---

## 2. Manual LM Studio Server & Model Setup (CLI or GUI)

If you ever wish to set up or manage LM Studio manually without running the script:

### Option A: Using `~/.lmstudio/bin/lms` CLI

```bash
# 1. Start the local server on port 1234 with CORS enabled (CORS is required for Obsidian streaming)
~/.lmstudio/bin/lms server start --cors -p 1234

# 2. Unload any resident model to avoid out-of-memory errors on 4GB VRAM
~/.lmstudio/bin/lms unload --all

# 3a. Load model with full GPU offload (RTX 3050 Ti Mobile, 4GB VRAM)
~/.lmstudio/bin/lms load qwen3.5-4b --gpu max -c 6144 -y

# 3b. Load model with 50% partial GPU offload (useful when desktop VRAM is constrained)
~/.lmstudio/bin/lms load qwen3.5-4b --gpu 0.5 -c 4096 -y

# 3c. Load model on CPU only (e.g. on laptops with only integrated Iris Xe graphics, or for massive models)
~/.lmstudio/bin/lms load nanbeige4.2-3b --gpu off -c 4096 -y

# 3d. Load massive 35B MoE model (21.7 GB) on CPU / RAM
~/.lmstudio/bin/lms load huihui-qwen3.6-35b-a3b-abliterated-ggml-model --gpu off -c 4096 -y

# 4. Verify loaded models and API identifier
~/.lmstudio/bin/lms ps
curl -s http://127.0.0.1:1234/v1/models
```

### Option B: Using LM Studio AppImage GUI

1. Launch the AppImage:
   `/mnt/media/downloads/llm/lmstudio/Bionic-1.2.0-2-linux-x64.AppImage`
2. Open the **Developer** (Local Server) tab on the left sidebar.
3. Toggle **CORS (Cross-Origin Resource Sharing)** to **ON**.
4. Verify Server Port is `1234`.
5. Select and load your model from `/mnt/media/downloads/llm/models/`:
   - `qwen3.5-4b` (`unsloth/Qwen3.5-4B-GGUF/Qwen3.5-4B-Q4_K_S.gguf`) — Primary writing engine for 4 GB VRAM.
   - `nanbeige4.2-3b` (`owao/Nanbeige4.2-3B-GGUF/Nanbeige4.2-3B-Q4_K_M.gguf`) — High reasoning density for CPU or 4 GB VRAM.
   - `lfm3-1.7b` (`LiquidAI/LFM3-1.7B-Q4_K_M-GGUF/LFM3-1.7B-Q4_K_M.gguf`) — Ultra-low-latency inline completion.
   - `huihui-qwen3.6-35b-a3b-abliterated-ggml-model` — 35B MoE (3B active parameters); requires >=24 GB system RAM in CPU mode.
6. Note the exact string shown under **`This model's API identifier`** (e.g., `qwen3.5-4b`).

---

## 3. Manual Obsidian Plugin Configuration

### Step 1: Install & Enable Plugins
1. Open Obsidian (`Vault: ~/Documents/pensive`).
2. Go to **Settings → Community plugins → Turn on community plugins**.
3. Browse and install:
   - **Text Generator** (`nhaouari/obsidian-textgenerator-plugin`)
   - **Smart Composer** (`glowingjade/obsidian-smart-composer`)
4. Toggle both plugins to **Enabled**.

*(Note for offline environments: the setup script caches release files in `/mnt/media/downloads/llm/obsidian_plugin_cache/<plugin_id>/`. You can manually copy `main.js`, `manifest.json`, and `styles.css` directly into `~/Documents/pensive/.obsidian/plugins/<plugin_id>/` and register the plugin ID in `~/Documents/pensive/.obsidian/community-plugins.json`).*

### Step 2: Configure Text Generator (`obsidian-textgenerator-plugin`)
Open **Settings → Text Generator**:
- **LLM Provider**: `Default (Custom)`
- **Provider Profile**: Give the profile a name under `Provider Profile`, e.g., `fastgpu`
- **Endpoint**: `http://127.0.0.1:1234/v1/chat/completions`
- **API Key**: `http://127.0.0.1:1234/`
- **Model**: The actual listed name under `This model's API identifier` in LM Studio (e.g., `qwen3.5-4b`, `nanbeige4.2-3b`, `lfm3-1.7b`, or `falcon-h1-3b-instruct`)
- **Streaming**: `ON` (`true`)
- **CORS Bypass**: `OFF` (`false`) — LM Studio server emits `Access-Control-Allow-Origin: *` when started with `--cors`. Leaving CORS Bypass off enables native low-latency `fetch()` streaming instead of Obsidian's buffered `requestUrl()`.
- **Max Tokens**: `2048`
- **Temperature**: `0.35`
- **Custom Default System Prompt**: Paste the contents of `/mnt/media/downloads/llm/instructions.md`.

### Step 3: Configure Smart Composer (`smart-composer`)
Open **Settings → Smart Composer**:
1. Under **Providers**, edit or add **`lm-studio`**:
   - **Base URL**: `http://127.0.0.1:1234/v1`
   - **API Key**: `lm-studio`
2. Under **Models**, add/enable your model:
   - **Provider**: `lm-studio`
   - **Model ID**: Exact LM Studio API identifier (e.g. `qwen3.5-4b`)
3. Set **Chat Model** and **Apply Model** to the active LM Studio model.
4. In **System Prompt**, paste the contents of `/mnt/media/downloads/llm/instructions.md`.

---

## 4. Writing Workflow & Shortcuts

- **Inline Text Generation**: Place the cursor anywhere in a note and press `Ctrl+J` (*Text Generator: Generate Text!*).
- **Template-Based Rewriting**: Highlight any passage, open the Command Palette (`Ctrl+P`), and select **Text Generator: Generate & Insert from Template**:
  - `Compress & Sharpen (PhD U.S. Register)`: Relentlessly compresses prose, maximizes semantic density and U.S. idiomatic force.
  - `Expand Analytically (Mechanisms & Second-Order Effects)`: Unpacks causal mechanisms, hidden premises, and consequences.
  - `Steelman & Incisive Counterargument`: Formulates the strongest counter-thesis and exposes structural vulnerabilities.
  - `U.S. Idiomatic & Lexical Elevation`: Replaces flat or Commonwealth phrasing with high-resolution American English diction.
- **Sidebar Chat & Diff Preview**: Open **Smart Composer** in the sidebar, inject notes via `@note`, and click **Apply** to review side-by-side diffs directly in your editor before accepting changes.
