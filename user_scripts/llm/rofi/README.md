# rofi_ai_search

A Rofi-driven local web-search assistant. Type a question; it runs a CRAG
(Corrective RAG) pipeline entirely on your own machine — SearXNG search → page
fetch → LLM relevance grading → cited answer synthesis via a local model on
Ollama — and shows the answer with clickable sources in a Rofi window.

The search runs in a **detached background worker** that outlives every Rofi
window, so the launch keybind acts as a toggle:

- **no session** → prompt for a question, spawn the worker, attach live progress
- **search running** → re-attach to live progress (Esc *hides* it; the search keeps running)
- **search done** → re-show the results/sources screen

Pressing Esc **detaches** — it never cancels. If a search finishes while you're
detached, you get a "Search complete" notification. The conversation (including
follow-up memory) persists across keybind presses until you pick **New search**
or reboot.

---

## Requirements

### 1. CLI tools

Standard coreutils (`awk`, `sed`, `grep`, `tail`, `wc`, `nl`, `tr`, `head`,
`date`, `mktemp`, `readlink`, `setsid`) ship with a base Arch install. The rest:

```sh
sudo pacman -S rofi curl jq w3m wl-clipboard libnotify xdg-utils uv
```

| Package         | Provides       | Notes                                            |
|-----------------|----------------|--------------------------------------------------|
| `rofi`          | `rofi`         | The UI. Built/tested against **rofi 2.0.0**.     |
| `curl`          | `curl`         | All HTTP (SearXNG, Ollama, page fetches).        |
| `jq`            | `jq`           | JSON building/parsing.                            |
| `w3m`           | `w3m`          | Page-text extraction (fallback + primary).       |
| `wl-clipboard`  | `wl-copy`      | "Copy answer" — **Wayland only** (see X11 note).  |
| `libnotify`     | `notify-send`  | Completion notifications.                         |
| `xdg-utils`     | `xdg-open`     | Opening source URLs in the browser.              |
| `uv`            | `uvx`          | Runs `trafilatura` for better text extraction.   |

`uvx`/`trafilatura` is **optional**: if missing, fetching silently falls back to
w3m-only extraction (lower quality, still works).

### 2. Ollama + a model

```sh
sudo pacman -S ollama            # or ollama-cuda / ollama-rocm for GPU accel
systemctl enable --now ollama
ollama pull qwen3:8b
```

Expects Ollama at `http://localhost:11434` and the model `qwen3:8b`. Needs enough
VRAM to be usable (it runs many calls per turn: reformulate + per-source grading
+ synthesis). The script **pins the model to VRAM during a turn and evicts it the
instant the turn finishes** — including evicting it out from under anything else
using the same model. That's intentional for a personal machine; be aware of it.

### 3. SearXNG with JSON output

The script queries `http://localhost:8888` and **requires the JSON format**,
which most SearXNG installs disable by default. Run your own instance
(`searxng-docker` or the AUR package) and enable JSON in `settings.yml`:

```yaml
search:
  formats:
    - html
    - json
```

### 4. rofi-blocks plugin (recommended, not required)

Live scrolling progress needs the `rofi-blocks` plugin at
`~/.local/lib/rofi/blocks.so`. It is **not packaged** and must be compiled
against your exact Rofi version — the streaming behavior this script relies on is
specific to a rofi-2.0.0 build.

Without it the script still works: it falls back to a **static spinner** while
searching (`attach_loader`), you just lose the live step-by-step progress.

---

## Install

1. Drop `rofi_ai_search` somewhere on your `PATH` (or reference it by full path)
   and make it executable: `chmod +x rofi_ai_search`.
2. Bind it to a key. The script is WM-agnostic — it just needs *a* launcher. For
   Hyprland (`hyprland.conf`):

   ```
   bind = $mainMod, S, exec, /path/to/rofi_ai_search
   ```

   Bind the **same key** to the same command for the toggle behavior to work
   (the script detects an open window and dismisses it on the second press).

---

## Usage

- Press the key → type a question → Enter.
- Watch live progress (or the spinner). Press **Esc** to hide; the search keeps
  running and notifies you when done. Press the key again to re-attach / re-show.
- On the results screen:
  - **Ask a follow-up** — keeps conversation memory (pronouns resolved against
    the prior turn).
  - **Copy answer** — to the Wayland clipboard.
  - **A source row** — `Enter` opens it and closes the menu; `Shift+Enter` opens
    it and *stays* in the menu (cursor parks on it, so you can open several).
  - **New search** — wipes the conversation and starts fresh (the only
    non-reboot reset).

### Query sigils

Control the research effort per-query (stripped before searching):

- **`!` prefix** → strict relevance grading (e.g. `!who invented the transistor`).
- **`???` suffix** → thorough mode: more research rounds + a reflect-and-retry
  pass (e.g. `obscure lore question???`).
- **bare `???`** as a follow-up → escalate the *previous* question to thorough
  mode, skipping the sources it already found unhelpful.

---

## Configuration

All knobs live near the top of the script:

- **Appearance** (lines ~85–90): `WINDOW_WIDTH`, `WINDOW_HEIGHT`,
  `NUM_VISIBLE_ROWS`, `ANSWER_FONT`, `LIST_FONT`.
- **Endpoints / model** (lines ~92–96): `SEARXNG_URL`, `OLLAMA_URL`, `MODEL`.
- **Pipeline tuning** (lines ~96–108): `NUM_RESULTS`, `MIN_RELEVANT`,
  `MAX_RESEARCH_ROUNDS`, fetch timeouts, char caps, context-window size.
- **Blocked domains** (lines ~111–116): sites never fetched (JS walls / no
  article text).

The `ANSWER_FONT` defaults to `JetBrainsMono Nerd Font` — install
`ttf-jetbrains-mono-nerd` or change it.

---

## Notes / gotchas

- **X11 users:** "Copy answer" uses `wl-copy` (Wayland). On X11, swap it for
  `xclip`/`xsel` (single line, in the `⎘ Copy answer` case of `results_loop`).
- **Session storage:** per-turn state lives on tmpfs under
  `$XDG_RUNTIME_DIR/rofi_ai_search`, so it auto-clears on reboot and never hits
  disk. Falls back to `/tmp` if `$XDG_RUNTIME_DIR` is unset.
- **Debug:** run `AISEARCH_DEBUG=1 rofi_ai_search` to log the full pipeline to
  `~/.config/dusky/settings/rofi_ai_search/debug.log` (`tail -f` it). The
  `dusky/` path is config-specific; harmless on other setups (auto-created).

---

## How it works (one paragraph)

The single file re-execs itself in four roles via a leading flag: the **toggle
client** (default), the detached **`--worker-run`** that runs one search turn,
the **`--blocks-loader`** controller that streams progress into the live Rofi
window, and a **`setsid` client-leader** re-exec used for clean process-group
toggling. State is passed between them through small files in the tmpfs session
dir; the client never blocks the worker, which is what makes attach/detach work.
