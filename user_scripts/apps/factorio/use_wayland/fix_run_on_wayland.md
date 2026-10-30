# Factorio 2.1.x Native-Wayland Crash — Diagnosis & Fix Guide

**Status: SOLVED and verified.** This document is the complete record of what was
wrong, how it was found, and how to re-apply the fix on a fresh system. It was
written on 2026-08-09 after the fix was proven end-to-end (game boots on native
Wayland with no XWayland, reaches the main menu, loads a save, renders frames,
zero GL errors — both with and without the jc141 bubblewrap sandbox).

Everything you need is in this folder:

| File | What it is |
|---|---|
| `fix_run_on_wayland.py` | **Self-contained installer** (embeds the shim source + a verification smoke test + a game-launch verifier). Run it, done. |
| `fix_run_on_wayland.md` | This guide. |
| `sources/` | Dev-only: the original `eglfix.c` + `export.map` used to regenerate the installer. |
| `generate.py` | Dev-only: regenerates `fix_run_on_wayland.py` from the template + `sources/`. |
| `install_fix.template.py` | Dev-only: the template `generate.py` fills in. |
| `stress_test.sh` | Dev-only: the stress-test suite (break/repair, fresh-sim, idempotency, reset). |
| `runsh_test.sh`, `deep_test.sh` | Dev-only: automated game-launch tests. |

---

## 1. TL;DR

Factorio 2.1.14 crashes ~1 second after launch on a **native Wayland** session
(no XWayland) with:

```
Error ShaderOpenGL.cpp:15: Failed to create shader '__core__/graphics/shaders/sprite.vert'.
Error CrashHandler.cpp:616: Received 6
```

The cause is NOT the GPU driver, Mesa, or the shader files. It is that Factorio's
**EGL context stops being current on the main thread** between OpenGL init and
the first shader compile, so every GL call silently fails and `glCreateShader`
returns 0. This only happens on the native Wayland/EGL path — under X11/XWayland
the same game works fine.

The fix is a small `libEGL.so.1` **interposer** loaded via `LD_PRELOAD` that
forwards all EGL calls to the real library but re-binds the remembered EGL
context on the calling thread right before the first `glCreateShader`. After
that, the game renders normally on native Wayland.

**To fix a fresh install:**
```bash
python3 fix_run_on_wayland.py --game-dir /path/to/Factorio_2.1.14
cd /path/to/Factorio_2.1.14 && ./start.n.sh
```
The script installs the packages itself (auto-sudo) if you omit a manual
pacman line. It auto-detects the game dir if you omit `--game-dir`
(known locations incl. `~/Downloads/Factorio-jc141`, `/mnt/zram1/Factorio_2.1.14`,
`FACTORIO_DIR` env var, and `start.n.sh` in the current dir).

---

## 2. The problem — symptoms

Verified environment where it crashed:
- Arch Linux, Hyprland, **Wayland-only session** (`DISPLAY=` empty, XWayland disabled).
- Intel Iris Xe iGPU + NVIDIA RTX 3050 Ti dGPU, Mesa 26.1.6, SDL3 3.4.14.
- Factorio 2.1.14 (build 87180, linux64, steam) from a jc141 repack, launched
  via `./start.n.sh` (DwarFS FUSE mount + optional bubblewrap sandbox).

The exact log (identical with or without the sandbox):

```
   0.521 Video driver: wayland
   0.609 Initialised OpenGL:[3] Mesa Intel(R) Iris(R) Xe Graphics (ADL GT2); driver: 4.6 (Core Profile) Mesa 26.1.6-arch3.1
   0.911 Graphics settings preset: integrated-gpuhigh
Factorio crashed...
Error ShaderOpenGL.cpp:15: Failed to create shader '__core__/graphics/shaders/sprite.vert'.
Error CrashHandler.cpp:616: Received 6
```

Key facts established early:
- The shader file exists and is readable (`sprite.vert`, 401 bytes).
- Standalone EGL test programs compile the *exact same shaders* fine on iris,
  llvmpipe and NVIDIA — so it is not a driver/compiler problem.
- The crash is identical on Intel, llvmpipe (software) and NVIDIA paths.

## 3. Root cause (empirically proven)

A gdb probe at `ShaderOpenGL.cpp:15` (the abort site) showed:

```
eglGetCurrentContext() == 0        <- NO current context on this thread
glCreateShader(GL_VERTEX_SHADER) == 0   <- every GL call fails silently
glGetError() == 0                  <- nothing to report, the call was a no-op
```

At the same time, tracing `eglCreateContext`/`eglMakeCurrent` showed the context
**was** bound successfully during init (~0.4 s), and the shader compile happens
~1 s later on the **same** thread — with no unbind or destroy in between. So the
binding was lost through a mechanism the game never directly triggers
(`eglReleaseThread`, surface recreation, or an internal SDL3/EGL Wayland path).

In short: **the game assumes the EGL context it created is still current when
it starts compiling shaders; on native Wayland it is not.** X11 doesn't hit
this bug, which is why every report says "forcing X11 works".

Why a naive LD_PRELOAD doesn't fix it:
- The game **dlsyms its EGL entry points itself** (proven: a preload interposing
  `gl*` symbols and `eglGetProcAddress` never got called — empty trace log;
  `strings` on the binary confirms it dlsyms `eglCreateContext`,
  `eglMakeCurrent`, `eglGetPlatformDisplayEXT`, etc.).
- Therefore the interception point must be **the library handle itself**: a
  library that declares SONAME `libEGL.so.1` so the game's own
  `dlopen("libEGL.so.1")` returns it. That is exactly what the shim does.

## 4. How the fix works

`libEGL.so.1` (installed to the **persistent** location `~/.factorio/wayland_fix/`,
built from the embedded `eglfix.c` in `fix_run_on_wayland.py`):

1. Declares `SONAME libEGL.so.1` → any `dlopen("libEGL.so.1")` in the process
   (game or SDL) resolves to it.
2. Forwards **every** EGL call to the real glvnd `libEGL.so.1` (resolved by
   absolute path to avoid recursion).
3. Remembers the last successful `eglMakeCurrent` binding (display/surface/context),
   guarded by a mutex; tracks whether the surface is still alive.
4. Wraps `glCreateShader`/`glCreateProgram` (handed out through the wrapped
   `eglGetProcAddress`, exactly like the game resolves GL): right before the
   first call, if the calling thread has no current context, it re-binds the
   remembered context. This is **one-shot per binding** (re-armed only by a new
   `eglMakeCurrent`/`eglCreateContext`) so it can't hijack other contexts.
5. Rebind failures are logged and non-fatal, with a surfaceless retry.
6. Exports only `egl*`/`gl*` symbols (linker version script `export.map`), so
   the interposer can never accidentally shadow libc/libdl symbols.

## 5. Required packages (fresh Arch install)

Installed automatically by `fix_run_on_wayland.py` (auto-elevates to sudo only
for pacman; everything else runs as your user). All are also listed here for
manual install:

| Package | Why it's needed |
|---|---|
| `gcc` | Compiles the shim (`eglfix.c`). |
| `libglvnd` | Provides the **real** `/usr/lib/libEGL.so.1` the shim forwards to, **and** the EGL headers (`/usr/include/EGL/egl.h`) used to build it. |
| `sdl3` | Factorio 2.1.x links host SDL3 (the Wayland/EGL path runs through it). |
| `fuse-overlayfs` | Required by the jc141 launcher to mount `files/game-root` from the DwarFS archive. |
| `bubblewrap` | Required by the jc141 sandbox (`ISOLATE=1` in `~/.jc141rc`). |
| `python-rich` | Pretty tables/panels for `--check`/`--troubleshoot` (optional; the installer falls back to plain text if absent). |
| `wtype`, `grim`, `imagemagick` (optional, `--with-testing-tools`) | Only for automated testing (keyboard injection, screenshots, image stats). Not needed to play. |

Already part of any desktop Arch install (not installed by the script): `mesa`
(GPU drivers), a Wayland compositor (`hyprland`), `python`, `binutils`
(`readelf`, used for SONAME verification). The DwarFS FUSE driver is **shipped
inside the repack** (`files/dwarfs-binary`), so no package needed for that.

## 6. What to save (so a fresh install can be fixed)

Keep **these two files** somewhere safe (USB/cloud/another machine):

1. **`fix_run_on_wayland.py`** — the whole kit in one self-contained file: the
   shim source, the version script, the smoke test, and the game-launch
   verifier. Nothing else is needed to rebuild and reinstall the fix.
2. **`fix_run_on_wayland.md`** — this guide.

Optional to keep (dev/reference): `sources/`, `generate.py`,
`install_fix.template.py`, `stress_test.sh`, `runsh_test.sh`, `deep_test.sh`.
The script regenerates the game-side files anyway, so only the two files above
are strictly required.

### Where the fix lives (persistent — survives game re-downloads)

On a fresh reinstall of the *game*, none of this is lost, because the shim does
NOT live inside the game directory anymore:

| Location | Purpose |
|---|---|
| `~/.factorio/wayland_fix/libEGL.so.1` (+ `eglfix.c`, `export.map`, `README.md`) | The built shim + sources (used for non-sandboxed launches). |
| `<JC_DIRECTORY>/native-docs/.factorio/wayland_fix/libEGL.so.1` | **Mirror** of the shim inside the jc141 sandbox home (`JC_DIRECTORY` from `~/.jc141rc`, default `~/Games/jc141`). The bubblewrap sandbox maps `/home/<you>` → `native-docs`, so this copy is what a sandboxed launch sees. |
| `<game>/local.config` | One line added/updated (see below). |

`local.config` gets:

```
ENV="env LD_PRELOAD=$HOME/.factorio/wayland_fix/libEGL.so.1"
```

`$HOME` expands when `start.n.sh` sources the file, and the resulting path
resolves in **both** launch modes: unsandboxed it is the real `~/.factorio/...`;
inside the sandbox `/home/<you>/...` maps to `native-docs/...` where the mirror
sits. Backup of a pre-existing config is kept at `local.config.bak` (written
only once; deleted on `--reset`).

The `env` prefix in the ENV line is **required**: the jc141 launcher inserts
`$ENV` word-split into the command array (`RUN+=( $ENV ... )`), and inside the
bubblewrap sandbox only a real binary such as `env` can apply the variable — a
bare `LD_PRELOAD=...` prefix is mis-exec'd by bwrap and the launcher dies
silently. (Verified: sandboxed launch failed until the `env` prefix was added.)

## 7. Fresh-install procedure

### Automated (recommended)

```bash
# 1. Put the game repack somewhere, e.g.:
#    /mnt/zram1/Factorio_2.1.14   (this machine's location, zram tmpfs)

# 2. Run the installer (it finds the game dir automatically, or pass --game-dir):
python3 fix_run_on_wayland.py --game-dir /mnt/zram1/Factorio_2.1.14
#    - installs missing packages (sudo prompt, or SUDO_STDIN=1 for piped password)
#    - builds the shim into ~/.factorio/wayland_fix/, mirrors it into the
#      sandbox home, wires local.config, runs a smoke test that MUST pass
#    - exit 0 = fixed; exit 2 in --check mode = problems found

# 3. Launch normally:
cd /mnt/zram1/Factorio_2.1.14 && ./start.n.sh
```

The script auto-detects the game dir from several locations, including
`~/Downloads/Factorio-jc141` (the default in the companion `run.sh` launcher).

> **Launching via `~/user_scripts/apps/factorio/run.sh`?** `run.sh` launches
> the game binary directly (it does not go through `start.n.sh`), so the shim
> must be applied by run.sh itself — it now does this automatically (sets
> `LD_PRELOAD=$HOME/.factorio/wayland_fix/libEGL.so.1` when present, gracefully
> ignored when not). No extra step needed.

### All flags

```
python3 fix_run_on_wayland.py [--game-dir DIR] [--check] [--force] [--skip-packages]
                              [--with-testing-tools] [--verify-game] [--reset] [--troubleshoot] [-y]
```

- `--check` — inspect only (packages, shim, wiring, exec bits), exit 2 if problems.
- `--force` — rebuild the shim even if up to date (deterministic: same hash).
- `--skip-packages` — don't run pacman.
- `--with-testing-tools` — also install wtype/grim/imagemagick.
- `--verify-game` — after installing, launch the real game through `start.n.sh`
  and automatically confirm it reaches `Factorio initialised` on native Wayland
  (needs a live Wayland/X11 session; skips gracefully if there's no display or
  a game is already running; kills its own process tree afterwards).
- `--reset` — **remove everything the fix generated**: both shim dirs, the
  legacy in-game `eglfix/` dir (if any), the `ENV=` line (or the whole
  `local.config` if the script created it), and any stray `.bak`. Asks for
  confirmation unless `-y/--yes` (piped stdin is treated as "no"). **Works even
  if the game dir is gone** (e.g. you deleted the game — the shims live in
  `$HOME`, not the game dir). Then re-run without flags to reinstall.
- `--troubleshoot` — read-only full diagnostic: state table, live smoke test,
  shim log (`/tmp/eglfix.log`), game logs (sandbox + host), and suggested fixes.

### Fresh-install robustness (all handled automatically)
Empirically discovered on a real fresh download (2026-08-09):
- **Missing `local.config`** — some jc141 extractions ship without it (the
  launcher auto-generates one). The installer now **creates it** with the
  `ENV=` fix line when absent.
- **Lost exec bits** — fresh extractions frequently come out `rw-r--r--` on
  `start.n.sh`/`space.age.sh`/`actions.sh`/`files/dwarfs-binary`, which breaks
  `./start.n.sh` with "Permission denied". The installer now **restores the
  exec bit** automatically (and `--check` reports it as a problem).

### Manual (if you prefer, or for non-Arch)

```bash
sudo pacman -S --needed gcc libglvnd sdl3 fuse-overlayfs bubblewrap
mkdir -p ~/.factorio/wayland_fix
# copy eglfix.c + export.map (embedded in fix_run_on_wayland.py) there
gcc -shared -fPIC -O2 -Wall -o ~/.factorio/wayland_fix/libEGL.so.1 \
    ~/.factorio/wayland_fix/eglfix.c -ldl -pthread \
    -Wl,-soname,libEGL.so.1 -Wl,--version-script=~/.factorio/wayland_fix/export.map
# mirror into the sandbox home (same bytes):
mkdir -p ~/Games/jc141/native-docs/.factorio/wayland_fix
cp ~/.factorio/wayland_fix/libEGL.so.1 ~/Games/jc141/native-docs/.factorio/wayland_fix/
# add to <game>/local.config:
echo 'ENV="env LD_PRELOAD=$HOME/.factorio/wayland_fix/libEGL.so.1"' >> local.config
```

## 8. Verification checklist (all performed 2026-08-09)

The game itself, both sandboxed and unsandboxed, with the shim:
- [x] Log shows `Video driver: wayland` + `Initialised OpenGL: Mesa Intel Iris Xe` and **no** `Failed to create shader` crash.
- [x] Reaches the main menu; **keyboard input works** (wtype keypresses navigate the menu).
- [x] Loads a save: `Loading level.dat` + `Map version 2.1.14-1` (in the sandbox too).
- [x] Renders: screenshots show real in-game content (40k-100k+ colors, not a
      black screen) and the view actually changes on WASD/zoom input.
- [x] **Zero** per-frame `INVALID_OPERATION` GL errors (were present pre-fix).
- [x] Shim event log shows `ensure_current: rebind ... -> 1` inside the game process.

The installer's built-in smoke test (runs on every install): creates a
surfaceless EGL context, unbinds it, then calls `glCreateShader` — the shim
must re-bind and return a valid shader. Baseline (no shim) reproduces the
Factorio condition (`glCreateShader -> 0`) as a negative control. Verified
PASS on this machine.

`--verify-game` goes one level deeper and is the strongest automated proof:
it launches the actual game via `start.n.sh`, watches its log for either
`Factorio initialised` (PASS) or `Failed to create shader` (FAIL), then kills
the whole process tree it started. Output on this machine:

```
[*] launching game via .../start.n.sh (up to 75s)...
[*] GAME LAUNCH CHECK PASSED: reached 'Factorio initialised' on native Wayland
```

### Final from-scratch stress test (2026-08-09, everything re-verified)

An end-to-end test on the exact shipped files — wipe-everything-and-rebuild,
no assumptions:

- **Kit integrity:** `generate.py` regenerates the installer; `py_compile` OK;
  embedded `eglfix.c`/`export.map` SHA-identical to `sources/`; all 12 flags
  present in `--help`.
- **Fresh-install simulation:** deleted both shim dirs + `local.config`, broke
  the exec bits on `start.n.sh`/`space.age.sh`/`actions.sh` → `--check`
  reported all 4 problems → one install run restored exec bits, rebuilt +
  mirrored the shim, re-created `local.config`, smoke test passed, exit 0.
- **Stress suite:** **20/20 assertions pass** (break/repair, fresh-sim,
  deterministic `--force` rebuild, idempotency ×4, `--check`=0, `--reset`
  completeness incl. `.bak`, reinstall). Post-stress `--check`: 0 problems.
- **`--verify-game`:** game reached `Factorio initialised` on native Wayland
  (sandboxed), shim rebind fired, processes cleaned up after.
- **`run.sh` launch path:** shim preloaded through the user launcher, game
  booted on Wayland, 40k-color rendering, **0 GL errors**.
- **Deep gameplay (sandboxed):** keyboard-loaded the save
  (`Map version 2.1.14-1`), world rendered (49k colors), WASD/zoom moved the
  camera (screenshot mean changed), **0 GL errors**.
- **`--reset` round-trip:** piped "n" aborts (exit 1, no changes); `--yes`
  removes both shim dirs + config; `--troubleshoot` then correctly reports the
  missing pieces and suggests the fix; reinstall restores everything.
- **Game-independent `--reset`:** works when no game dir can be found (shims
  live in `$HOME`), so you can clean up after deleting the game.

## 9. Troubleshooting

- **Smoke test fails:** check `/tmp/eglfix.log` (shim diagnostics) and the
  script output. Usually: missing EGL headers (`sudo pacman -S libglvnd`) or
  the real `libEGL.so.1` missing.
- **Fresh-install quick check:** if the game crashes right after a fresh
  download, run `python3 fix_run_on_wayland.py --check` first — it reports
  missing shims, unwired config, and lost exec bits in one table.
- **Game still crashes with the old error:** run `python3 fix_run_on_wayland.py`
  again (or `--force` to rebuild), then `--verify-game` to prove it. If that
  still fails, `--reset` and reinstall.
- **Want a full picture before touching anything:** `python3 fix_run_on_wayland.py
  --troubleshoot` — read-only, prints the state table, re-runs the smoke test,
  and tails the shim + game logs with suggested fixes.
- **Reset everything the fix did:** `python3 fix_run_on_wayland.py --reset`
  (add `-y` to skip the confirmation). Removes both shim dirs, the ENV line,
  and any stray `.bak`/legacy `eglfix/`.
- **Launcher dies silently with no banner:** the `env` prefix is missing from
  the `ENV=` line (see §6).
- **You only see the crash on Wayland, X11 is fine:** that is expected — the
  bug is native-Wayland-specific; the shim makes the Wayland path work.
- **Space Age (`space.age.sh`):** uses the same `local.config`, so the fix
  applies automatically; no extra step.
- **Legacy in-game `eglfix/` dir (old layout):** the installer removes it
  automatically; nothing to do.

## 10. Limitations & notes

- The shim is a **targeted fix for this game's single-context Wayland bug**, not
  a general-purpose EGL interposer (it assumes one primary context; the
  auto-rebind is one-shot per binding for that reason).
- The game binary was never modified — only the launcher config and the
  persistent `~/.factorio/wayland_fix/` (+ sandbox mirror) were added. To remove
  the fix: `fix_run_on_wayland.py --reset`.
- The shim lives OUTSIDE the game directory, so it survives game re-downloads
  and repack updates; a re-downloaded game only needs the `ENV=` line
  re-applied, which the installer does automatically.
- The shim writes a tiny diagnostic log to `/tmp/eglfix.log` (bind/ensure
  events only; no hot-path logging).
- This fix is for **Factorio 2.1.x on native Wayland**. If a future Factorio
  build fixes the underlying bug, the `ENV=` line can simply be removed.

---

## 11. Factorio 2.1.23 Verification & Standalone / Custom Compilation (2026-10-30)

### Upstream Status
As of **Factorio 2.1.23 (build 87750, linux64, full, Space Age)**, the upstream bug has **not** been merged/fixed by Wube. Without the interposer shim, running Factorio 2.1.23 under native Wayland still crashes during early shader compilation:

```text
0.558 Initialised OpenGL:[3] Mesa Intel(R) Iris(R) Xe Graphics (ADL GT2); driver: 4.6 Mesa 26.2.6
Factorio crashed. Generating symbolized stacktrace, please wait ...
Error ShaderOpenGL.cpp:15: Failed to create shader '__core__/graphics/shaders/sprite.vert'.
```

With `libEGL.so.1` preloaded, the context is automatically restored on thread 52052:
```text
[eglfix] ensure_current: rebind d=0x55c5d6fa1690 s=0x55c5d6ff3be0 c=0x55c5d6fe72c0 -> 1
```
The game immediately proceeds to load all 8,792 sprite sheets and runs at 144 Hz with zero OpenGL errors.

---

### Compiling Your Own Shim Manually (Zero Repack / Zero Script Dependency)

If you ever need to compile the fix yourself for custom builds or fresh machines without using the repack installer scripts:

#### 1. Prerequisites
Ensure development tools and EGL headers are installed:
```bash
sudo pacman -S --needed gcc libglvnd
```

#### 2. Source Files
The source files are located in `~/.factorio/wayland_fix/` or in `user_scripts/apps/factorio/use_wayland/sources/`:
- `eglfix.c` — The C interposer code.
- `export.map` — Linker version script ensuring only `egl*` and `gl*` symbols are exported.

#### 3. Compile Command
```bash
mkdir -p "$HOME/.factorio/wayland_fix"

gcc -shared -fPIC -O2 -Wall \
    -o "$HOME/.factorio/wayland_fix/libEGL.so.1" \
    "$HOME/.factorio/wayland_fix/eglfix.c" \
    -ldl -pthread \
    -Wl,-soname,libEGL.so.1 \
    -Wl,--version-script="$HOME/.factorio/wayland_fix/export.map"
```

#### 4. Verify the Binary
Verify that the `SONAME` is properly set to `libEGL.so.1` (crucial so `dlopen("libEGL.so.1")` binds to it):
```bash
readelf -d "$HOME/.factorio/wayland_fix/libEGL.so.1" | grep SONAME
# Expected output:
#  0x000000000000000e (SONAME)             Library soname: [libEGL.so.1]
```

---

### Launching Standalone Factorio Versions

To run any standalone Factorio version (e.g. tarball extract from factorio.com, GOG standalone, or uncompressed zram directory):

1. **Direct Shell Execution**:
   ```bash
   LD_PRELOAD="$HOME/.factorio/wayland_fix/libEGL.so.1" ./bin/x64/factorio
   ```

2. **Master Game Runner Integration (`factorio.toml`)**:
   In your profile, define:
   ```toml
   [env]
   LD_PRELOAD = "$HOME/.factorio/wayland_fix/libEGL.so.1"
   ```
   The runner automatically applies this variable, sets `SDL_VIDEODRIVER=wayland`, unsets `DISPLAY`, and binds to your preferred GPU.

