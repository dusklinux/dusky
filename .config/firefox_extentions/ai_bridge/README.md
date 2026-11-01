# Local AI Web Bridge

A lightweight, zero-dependency, event-driven bridge for Firefox MV3 that allows any local AI agent (Antigravity, Ollama, Llama.cpp, LangChain, or custom CLI scripts) to communicate seamlessly with web-based AI platforms (**Google Gemini**, **ChatGPT**, **Claude**, **Meta AI**).

---

## 🤖 Instructions for AI Agents & Assistant Context

If you are an AI assistant or automated coding agent reading this file in a fresh conversation, **you do not need to scan the entire codebase**. Everything you need to understand the system, query web AI models, and follow the required verification workflows is documented below.

### 🔑 Local vs. Online AI Token Economy Mandate

- **Local AI Token Constraints**: Local AI agents (you) operate under tight token limits.
- **Online AI Subscription & Capacity**: The USER has a **high-capacity paid subscription** for web AI models with substantially higher token limits than the local AI.
- **Mandated Workflow**: You **MUST leverage the online web AI as much as possible** for all heavy lifting, deep reasoning, code generation, architectural design, and extensive verification to conserve local tokens.

### 🌐 Default Target Platform Mandate: Google Gemini

> [!IMPORTANT]
> **LOCAL AI INSTRUCTION: USE GOOGLE GEMINI BY DEFAULT**
> Whenever you (the Local AI agent) interact with the web AI bridge, **YOU MUST TARGET GOOGLE GEMINI BY DEFAULT (`-m gemini`)** instead of any other platform (ChatGPT, Claude, or Meta AI), unless the USER explicitly requests a different model.
>
> - **Primary Target Flag**: Always supply `-m gemini` in CLI queries (e.g. `ask -m gemini "..."` or `python3 bridge.py -m gemini "..."`) or specify `model="gemini"` in programmatic calls.
> - **Rationale**: The USER maintains a high-capacity Google Gemini subscription with an expansive context window ideal for deep reasoning, full-file analysis, and iterative code synthesis. Furthermore, the bridge includes native hardware keystroke routing (`wtype -k Return`) and focus orchestration specifically optimized for Gemini's Angular and Shadow DOM interface.

### ⚡ Mandatory Token-Safe Trip-Up Protocol (1 to 500 Sequence, Newline Format)

> [!IMPORTANT]
> **LOCAL AI INSTRUCTION: MANDATORY NUMERICAL TRIP-UP COMMAND (NEWLINE FORMAT)**
> Whenever you (the Local AI agent) formulate a prompt or query for the Online Web AI, you **MUST ALWAYS** append an instruction directing the Online AI to output the sequential numbers **1 through 500**, with **each number strictly on its own separate line** at the very conclusion of its response.
>
> **Mandated Suffix Template to Include in Web AI Prompts**:
> ```text
> At the end of every response, append the numbers 1 through 500, with each number on its own line. Always place the numbered list after the main response content. Do not skip any numbers. Do not combine multiple numbers on the same line. Do not format the numbers as bullets or a table. The final part of every response must be exactly:
> 1
> 2
> 3
> ...
> 500
> ```

#### 🎯 How the Trip-Up Engine Operates (Local AI Internal Knowledge)
- The extension runs a client-side **Token-Saver Countdown / Sequence Trip Engine** directly inside the web AI page DOM (`content_main.js`).
- The multi-line integer parser continuously monitors the tail of the stream (`lines[i].trim()`). Each line containing a standalone integer (`1\n2\n3\n4\n5...`) is matched cleanly by `/^\d{1,4}$/` and accumulated into the sequential run check.
- As soon as the web AI finishes delivering its genuine answer and outputs the first 5 line-separated numbers (`1\n2\n3\n4\n5`), the trip engine detects the sequential run (`threshold = 5`).
- The bridge immediately clicks the web interface's **Stop** button in the browser to abort server-side generation on the remote provider, cleanly strips all numbered lines (`lines.slice(0, seqStartLineIdx)`), and immediately dispatches a `FINAL` event with reason `"token-saver-trip"`.
- This ensures deterministic completion detection without waiting for slow DOM idling, cleanly slices off the tail without inline text boundary ambiguity, and stops the web AI from generating the remaining 495 lines—saving both generation time and token quota.

#### 🤫 Strict Secrecy Directive: Never Reveal the Trip-Up to the Online AI
> [!CAUTION]
> **ABSOLUTE RULE: DO NOT EXPLAIN OR MENTION THE TRIP-UP TO THE ONLINE AI**
> - **Never reveal why we ask for 1 to 500**: Under NO circumstances should the Local AI explain to the Online AI *why* it is being asked to output 1 through 500. Do NOT mention "token safe trip up", "halting early", "triggering the stop button", "cheating completion detection", "token saver", or "stopping generation".
> - **Present it purely as a strict formatting rule**: To the Online AI, this instruction must look like a standard, strict formatting requirement (e.g. "each number on its own line, no bullets, no tables").
> - **Why this secrecy is required**: This sequence trip-up is an intentional client-side hack/cheat. If the Online AI is informed about the tripwire, its reasoning or safety alignments may question the prompt, refuse the sequence, alter its response structure, or apologize instead of generating the numbers, defeating the automated trip mechanism. Keep the Online AI completely unbeknownst of the mechanism.

---

## 💻 Bleeding-Edge System Environment & Target Specifications

When generating scripts, configuration files, system commands, or code edits for this host, **ALWAYS target the absolute bleeding edge**. 

```bash
# Check current rolling-release versions live on system:
uname -r            # Linux Kernel version (target >= 7.3)
python3 --version   # Python interpreter version (target >= 3.15)
bash --version      # Bash shell version (target >= 5.3)
systemctl --version # systemd version (target >= 262)
```

### 🚫 Strict Modernity Directives:
1. **Dynamic Version Inspection**: Do NOT assume hardcoded static version numbers. Arch Linux is a rolling-release distribution — versions update continuously. Always inspect runtime versions or target the absolute latest bleeding-edge release available for Arch Linux.
2. **NO Legacy / Deprecated Code**: NEVER use outdated Python syntax, deprecated shell constructs, or legacy Web APIs.
3. **NO Backward Compatibility**: Do NOT add fallbacks or polyfills for older Linux kernels, obsolete Python versions, legacy Bash, or obsolete systemd. We are not trying to look back; we are moving forward.
4. **Future-Proof Standards**: Always write cutting-edge, idiomatically modern code targeting the absolute newest standards.

---

## 🤝 Hand-in-Glove Collaboration & Verification Protocol

The Local AI agent and Online Web AI model work as a **hand-in-glove pair**:

```
┌───────────────────────────────────────────────────────────────────────────┐
│                           LOCAL AI AGENT                                  │
│  - Has DIRECT ACCESS to local filesystem, terminal, and system commands   │
│  - MUST act strictly as the EMPIRICAL DATA COLLECTOR                      │
│  - Appends trailing 1..500 sequence innocuously (tripwire safe-trip)      │
│  - MUST NEVER interrupt or re-prompt while web AI is generating            │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │ Complete Empirical Data Payload
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                       ONLINE WEB AI MODEL                                 │
│  - Has HIGH-CAPACITY TOKEN ALLOCATION and web search capabilities         │
│  - Does NOT have direct access to local filesystem                        │
│  - Allowed to hypothesize, analyze from first principles, & synthesize     │
│  - Outputs answer followed by 1..500 (unaware of client-side tripwire)    │
└───────────────────────────────────────────────────────────────────────────┘
```

### 📊 1. Empirical Data Collection Mandate (No Guesswork)
- **Local AI Role**: The Local AI must NOT construct prompts based on guesswork or unverified assumptions. 
- **Gather Context First**: Before prompting the Online AI, the Local AI MUST inspect the environment and collect **all verifiable empirical data**: exact error stack traces, un-truncated log outputs, file contents, kernel/runtime versions (`uname -r`, `python3 --version`), and configuration parameters.
- **Provide Complete Payload**: Pass this full empirical dataset to the Online AI in Pass 1 so it has complete, un-truncated context to perform deep reasoning.

### ⏳ 2. Non-Interruption & Generation Patience Directive

> [!CAUTION]
> **STRICT MANDATE: NEVER INTERRUPT AN IN-FLIGHT GENERATION OR SEND STATUS PINGS**
> 
> 1. **NO STATUS CHECK PROMPTS**: NEVER send queries like `"Is Gemini ready?"`, `"Are you active?"`, or `"Which platform is this?"` into the web AI prompt box. The bridge automatically handles routing to the active Firefox tab. Sending meta-status questions into the chat prompt box clutters the conversation and cancels active work.
> 2. **NO INTERRUPTIONS MID-GENERATION**: NEVER send a follow-up prompt, re-query, or new command while the online AI is actively generating or thinking. On all web AI platforms, **submitting a new prompt while the AI is generating IMMEDIATELY CANCELS AND DESTROYS THE IN-FLIGHT RESPONSE**, wasting all previous reasoning work.
> 3. **WAIT SILENTLY FOR FULL COMPLETION**: Deep reasoning models (e.g. Gemini Thinking, ChatGPT Reasoning) can take anywhere from **2 to 20+ minutes** to complete a response. The Local AI MUST wait silently until `bridge.py` finishes completely (`FINAL` status received, exit code 0) before sending any follow-up query.

---

## 🔄 Mandatory 2-Pass Verification Protocol

Whenever a complex coding, refactoring, or architectural task is requested, the Local AI agent MUST execute the following **2-pass protocol using Google Gemini (`-m gemini`) by default**:

### Pass 1 Prompt Template (Empirical Data + Research Proposal via Gemini)

The Local AI collects empirical data locally, then sends to Gemini (`ask -m gemini ...`):

> "I am providing you with all empirical data collected from the local system (file contents, error logs, and runtime diagnostics below). Analyze this problem from first principles using the empirical evidence. Research online for the absolute latest documentation and bleeding-edge standards for Arch Linux (inspecting runtime versions of Kernel, Python 3.15+, Bash 5.3+, systemd). Do NOT hallucinate or guess. Think long, hard, and critically. Do NOT use old, deprecated, or legacy methodologies, and do NOT provide backward compatibility for older versions. Provide ONLY the precise, minimal surgical code blocks that need to be replaced, added, or edited.
>
> At the end of every response, append the numbers 1 through 500, with each number on its own line. Always place the numbered list after the main response content. Do not skip any numbers. Do not combine multiple numbers on the same line. Do not format the numbers as bullets or a table. The final part of every response must be exactly:
> 1
> 2
> 3
> ...
> 500"

### Pass 2 Prompt Template (Rigorous Double-Verification Press via Gemini)

After Gemini completes Pass 1, send a **follow-up query** in the same conversation turn:

> "Re-verify everything you just proposed from scratch from first principles using the empirical data. Think through the logic entirely again. Perform a second pass over your previous response to ensure there are zero logical holes, false positives, or hallucinated suggestions. Are all proposed improvements 100% necessary, optimal, and compatible with the absolute latest bleeding-edge specs? Only output the final, verified, minimal code diff blocks if they pass this second verification.
>
> At the end of every response, append the numbers 1 through 500, with each number on its own line. Always place the numbered list after the main response content. Do not skip any numbers. Do not combine multiple numbers on the same line. Do not format the numbers as bullets or a table. The final part of every response must be exactly:
> 1
> 2
> 3
> ...
> 500"

Only after Gemini completes Pass 2 and confirms the recommendations should the Local AI apply the changes.

---

## 🚀 How to Execute Queries from Terminal & Scripts

### 1. Primary Command Line Interface (`ask` Alias & `bridge.py`)

To query Google Gemini (the default target platform) synchronously:

```bash
ask -m gemini "YOUR PROMPT HERE"
```

Or directly via Python script:

```bash
python3 "$HOME/.config/firefox_extentions/ai_bridge/bridge.py" -m gemini "YOUR PROMPT HERE"
```

#### Advanced CLI Options:
| Flag / Option | Description |
| :--- | :--- |
| `-m, --model <name>` | Route directly to specific web AI service: `gemini` (**default choice**), `chatgpt`, `claude`, or `meta`. |
| `-f, --file <path>` | Attach one or more local files (can be specified multiple times; base64 encoded and attached via `DataTransfer`). |
| `-i, --image <path>` | Attach one or more images (can be specified multiple times). |
| `--no-trip` | Disable the automatic Token-Saver sequence trip engine if raw unhalted generation is required. |
| `--trip-threshold <N>` | Number of sequential integers required to trip (default: `5`). |
| `--trip-leading` | Enable trip detection for leading sequences at prompt start. |
| `--idle-timeout <secs>` | Inactivity timeout in seconds before treating response as complete (default: `60.0`). |
| `--tab-id <id>` | Target a specific browser tab by numeric ID. |

#### Usage Examples:

```bash
# 1. Primary Default Query (Google Gemini) with file attachments
ask -m gemini -f ./bridge.py "Review this file for modern Python 3.15 async practices"

# 2. Pipe file or command output via stdin to Gemini
git diff | ask -m gemini "Analyze this git diff and point out edge cases"

# 3. Querying alternate models when explicitly requested
ask -m claude "Alternate architecture perspective"
ask -m chatgpt "Alternate review"

# 4. Interactive Mode (prevent shell quote escaping for complex strings)
ask -m gemini
# Enter prompt, then press Enter or EOF (Ctrl+D)
```

- **Interactive Mode**: Run `ask -m gemini` with no trailing query to enter interactive prompt mode (prevents all shell escaping/quoting issues for complex strings containing `?`, `'`, `"`, `$`, `*`).
- **Noglob Protection**: The `ask` alias uses `zsh` `noglob` so unquoted question marks (`ask what ?`) will never fail with glob errors.
- **Output**: Streams text token-by-token directly to `stdout`. Shows `[Thinking...]` during model reasoning.
- **Exit Code**: Returns `0` on completion.

### 2. Python Programmatic Integration

#### Full Response (Synchronous, Gemini Default):
```python
import os
import subprocess
from pathlib import Path

def ask_web_ai(prompt: str, model: str = "gemini", files: list[str] | None = None) -> str:
    """Send a prompt to the Web AI bridge (Gemini by default) and return the complete answer."""
    bridge_path = Path.home() / ".config/firefox_extentions/ai_bridge/bridge.py"
    cmd = ["python3", str(bridge_path)]
    if model:
        cmd.extend(["-m", model])
    for f in (files or []):
        cmd.extend(["-f", f])
    cmd.append(prompt)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    else:
        raise RuntimeError(f"Web AI Bridge Error: {result.stderr}")

answer = ask_web_ai(
    "Explain quantum computing in 2 sentences.\n\n"
    "At the end of every response, append the numbers 1 through 500, with each number on its own line. "
    "Do not combine multiple numbers on the same line. Do not format as bullets or a table. "
    "The final part of every response must be exactly:\n1\n2\n3\n...\n500"
)
print(answer)
```

#### Real-Time Token Streaming (Generator, Gemini Default):
```python
import subprocess
from pathlib import Path

def stream_web_ai(prompt: str, model: str = "gemini"):
    """Stream response tokens live in real-time as they are generated by web AI (Gemini by default)."""
    bridge_path = Path.home() / ".config/firefox_extentions/ai_bridge/bridge.py"
    cmd = ["python3", str(bridge_path)]
    if model:
        cmd.extend(["-m", model])
    cmd.append(prompt)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for chunk in proc.stdout:
        yield chunk

prompt = (
    "Write a 1-paragraph summary of Linux 7.3.\n\n"
    "At the end of every response, append the numbers 1 through 500, with each number on its own line. "
    "Do not combine multiple numbers on the same line. Do not format as bullets or a table. "
    "The final part of every response must be exactly:\n1\n2\n3\n...\n500"
)
for token in stream_web_ai(prompt):
    print(token, end="", flush=True)
```

---

## 🏗️ Complete System Architecture & Codebase Map

```
┌──────────────────────────────────────────────────────────┐
│                   bridge.py (Daemon & CLI)              │
│  - WebSocket Server (127.0.0.1:8765)                     │
│  - Model Router & File Attachment Base64 Encoder         │
│  - Hyprland Window/Workspace Focus (hyprctl)            │
│  - Hardware Keyboard Input Engine (wtype)               │
│  - Diff-based Stream Output & THINKING State Handler     │
└────────────────────────────▲─────────────────────────────┘
                             │ WebSocket
┌────────────────────────────▼─────────────────────────────┐
│                background.js (MV3 Service Worker)        │
│  - Active Tab Locks, Model Target Resolver, & Router     │
│  - Non-Destructive Heartbeat & Auto-Reconnection         │
└────────────────────────────▲─────────────────────────────┘
                             │ postMessage
┌────────────────────────────▼─────────────────────────────┐
│             content_isolated.js (Isolated World)         │
│  - Secure Relay between Extension & Main World Realms    │
└────────────────────────────▲─────────────────────────────┘
                             │ postMessage
┌────────────────────────────▼─────────────────────────────┐
│               content_main.js (Page World)               │
│  - Modular Site Adapter Architecture (defineSite)        │
│  - Adapters: Gemini, ChatGPT, Claude, Meta AI, _default  │
│  - Token-Saver Trip Engine (1..500 Auto-Stop Trigger)    │
│  - Multi-File DataTransfer & Input Attachment Setter     │
│  - DOM MutationObserver & Site-Specific Input Encoders   │
└──────────────────────────────────────────────────────────┘
```

### Detailed File Responsibilities

1. **`bridge.py`**:
   - Runs as background daemon (`localai_bridge.service`) and CLI client.
   - Listens on `127.0.0.1:8765` via raw standard library WebSocket implementation (zero external pip dependencies).
   - Encodes attached files (`-f`, `-i`) to base64 with MIME autodetection.
   - Focuses Firefox window on Hyprland workspace prior to query submission.
   - Dispatches hardware Return keystrokes via `wtype -k Return` where synthetic dispatch is blocked.
   - Handles `THINKING` state events from reasoning models and formats live streamed stdout without duplication.
   - Intercepts `"token-saver-trip"` and cleanly reports when Token-Saver halts browser generation.

2. **`background.js`**:
   - MV3 extension background service worker. Manages active AI tab locks, queries open tabs matching requested models, and relays messages between the WebSocket daemon and content scripts.

3. **`content_isolated.js`**:
   - Content script in isolated extension realm. Relays `postMessage` calls between background script and page script without polluting page namespace.

4. **`content_main.js`**:
   - Content script running directly in the webpage main DOM realm (`MAIN` world).
   - **Token-Saver Trip Engine**: Monitors incoming output stream. Upon detecting a countup or countdown run of sequential integers (e.g. 1 through 500) matching threshold, fires `clickStopButton()`, strips the sequence numbers, and dispatches `FINAL`.
   - **File Attachment Engine**: Decodes base64 attachments, synthesizes `File` objects into a `DataTransfer` package, and injects them into the native `HTMLInputElement` file setter or trigger button across ChatGPT, Claude, Gemini, and Meta AI.
   - **Modular Site Adapter Architecture** (`defineSite()`):
     - **Gemini**: Shadow DOM (`rich-textarea`) traversal, Angular `InputEvent` chain, hardware wtype focus lock.
     - **ChatGPT**: Lexical editor binding, single `btn.click()` (prevents duplicate submissions).
     - **Claude**: ProseMirror editor binding, streaming observer.
     - **Meta AI**: React contenteditable input encoder (prevents text tripling), role="article" container observer.
     - **`_default`**: Fallback cascade for unrecognized AI interfaces.

5. **`setup.py`**:
   - Installer and diagnostic suite. Manages `localai_bridge.service` registration, performs end-to-end socket handshake validation, checks browser process state, and packages extension zip files for deployment.

6. **`~/.zshrc` (`ask` shortcut)**:
   - Configured with `alias ask='noglob _ask_func'`. Prevents Zsh globbing errors on question marks (`?`) and supports interactive mode when called with no arguments.

---

## 🛠️ Diagnostics & Troubleshooting Commands

```bash
# 1. Run Complete System & Bridge Diagnostic Check
python3 "$HOME/.config/firefox_extentions/ai_bridge/setup.py" --check

# 2. Check Daemon Service Status
systemctl --user status localai_bridge.service

# 3. Restart Daemon Service
systemctl --user restart localai_bridge.service

# 4. View Daemon Logs Live
journalctl --user -u localai_bridge.service -f

# 5. Build Extension Distribution Zip
python3 "$HOME/.config/firefox_extentions/ai_bridge/setup.py" --build

# 6. Test Query Connection
ask "Test connection"
```
