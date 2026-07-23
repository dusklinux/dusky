# Dusky Orchestrator — Profile Reference

Everything you need to write, edit, and debug orchestrator `.toml` profiles.

> Run `./orchestrator.sh --doctor` to verify your system and see resolved paths.

---

## 1. Included Profiles

| File | Name | Purpose |
| :--- | :--- | :--- |
| `01_main.toml` | Main Setup | Full Arch desktop: dotfiles, Hyprland, themes, services, AUR. |
| `02_iso.toml` | ISO Setup | Post-ISO install: skips AUR builds, sleep timeouts, heavy packages. |
| `03_dusk_personal.toml` | Dusk Personal Setup | Personal workstation: ASUS tweaks, TTS/STT, Firefox symlinks, backups. |

---

## 2. Profile TOML Structure

A profile has six top-level tables. Only `[sequence]` is required.

```toml
# ─── IDENTITY ────────────────────────────────────────────────────────────────
[profile]
name = "Main Setup"                 # Display name (defaults to filename stem)
description = "Full Arch install"   # One-line summary shown in profile selector
post_script_delay = 0               # Seconds to pause between tasks (default: 0)

# ─── EXECUTION POLICY ────────────────────────────────────────────────────────
[policy]
audio = true                # Play audio cues on completion/failure (default: true)
notify = true               # Send desktop notifications (default: true)
inhibit_sleep = true        # Inhibit system sleep/idle during the run (default: true)
task_timeout = 0            # Global per-task timeout in seconds; 0 = no limit (default: 0)
stop_on_fail = false        # Abort entire pipeline on first failure (default: false)
manual = false              # Prompt via modal before every task (default: false)
force = false               # Export DUSKY_FORCE=1, append --force to all tasks (default: false)

# ─── GIT SELF-UPDATE ─────────────────────────────────────────────────────────
[git]
enabled = true              # Pull latest dotfiles before running tasks (default: false)
git_dir = "~/dusky"         # Bare repo path (--git-dir)
work_tree = "~/"            # Working tree path (--work-tree)
remote = "origin"           # Remote name (default: "origin")

# ─── SCRIPT SEARCH DIRECTORIES ───────────────────────────────────────────────
# Searched IN ORDER to find each script name. First match wins.
# If a name appears in multiple directories, the orchestrator raises a
# [CONFLICT] error — resolve it in [conflict_resolutions] below.
# Paths support ~ and environment variables.
[search_dirs]
dirs = [
    "~/user_scripts/arch_setup_scripts/scripts",
    "~/user_scripts/arch_setup_scripts",
    "~/user_scripts/rofi",
    "~/user_scripts/images",
]

# ─── CONFLICT RESOLUTIONS ────────────────────────────────────────────────────
# Pin a script name to an exact path when it exists in multiple search dirs.
[conflict_resolutions]
# "wallpaper_selector.py" = "~/user_scripts/images/wallpaper_selector.py"

# ─── TASK SEQUENCE ────────────────────────────────────────────────────────────
[sequence]
scripts = [ ... ]           # Compact string entries (see §3)
tasks = [ ... ]             # Detailed TOML table entries (see §7, optional)
```

---

## 3. Task Entry Format

Each line in `scripts = [...]` follows one of these forms:

```
"MODE | FLAGS | COMMAND ARGS"
"MODE | COMMAND ARGS"
"COMMAND ARGS"                      ← defaults to U (user) mode
```

**Modes:** `U` = run as regular user. `S` = run with sudo.

```toml
scripts = [
    # Basic: user mode, no flags
    "U | 002_pre_generated_colors.sh",

    # Sudo mode with arguments
    "S | 050_pacman_config.sh --auto",

    # Condition + once (only runs if battery is present, only runs once)
    "U | if:battery,once | 135_battery_notify_service.sh --auto",

    # Multiple conditions AND'd: nvidia GPU + not a VM
    "U | if:gpu:nvidia,if:not:vm | 380_nvidia_open_source.sh --auto",

    # Ignore failures, retry up to 3 times with 5s between attempts
    "S | ignore,retry:3,retry_delay:5 | 055_pacman_reflector.sh",
]
```

---

## 4. Conditions (`if:<condition>`)

Conditions decide whether a task runs. If false, the task is **silently skipped**.

| Condition | True when… |
| :--- | :--- |
| **Hardware / Environment** | |
| `if:wayland` | `WAYLAND_DISPLAY` is set |
| `if:x11` | `DISPLAY` is set |
| `if:graphical` | Either Wayland or X11 is active |
| `if:desktop` | Active graphical session and not a pure SSH login |
| `if:ssh` | Inside an SSH connection |
| `if:vm` | Virtual machine (QEMU/KVM, VMware, VirtualBox) |
| `if:baremetal` | Physical hardware (opposite of `if:vm`) |
| `if:battery` | System has a battery in `/sys/class/power_supply` |
| `if:btrfs` | Root filesystem is Btrfs |
| `if:gpu:<vendor>` | GPU vendor detected — `nvidia`, `intel`, or `amd` |
| **File / Binary / Package** | |
| `if:command:<cmd>` | `<cmd>` exists in `$PATH` |
| `if:package:<pkg>` | Pacman package is installed |
| `if:path:<path>` | File or directory exists |
| `if:file:<path>` | Regular file exists |
| `if:dir:<path>` | Directory exists (supports `~`) |
| `if:missing:<path>` | File or directory does **not** exist |
| `if:group:<group>` | User belongs to the group |
| `if:env:<VAR>` | Environment variable is set and non-empty |
| `if:service_active:<unit>` | Systemd system service is active |
| `if:user_service_active:<unit>` | Systemd user service is active |
| **Logic** | |
| `if:not:<condition>` | Inverts any condition — `if:not:vm`, `if:not:command:sddm` |
| `if:always` | Always true (aliases: `if:true`, `if:yes`) |
| `if:never` | Always false (aliases: `if:false`, `if:no`) |

### Compound Conditions

Multiple conditions are **AND**'d. Two equivalent ways to write them:

```toml
# Shorthand: omit repeated "if:" after the first
"U | if:wayland,battery | 455_hyprctl_reload.sh"

# Explicit: each sub-condition gets its own "if:" prefix
"U | if:wayland,if:battery | 455_hyprctl_reload.sh"
```

Both mean: run only if Wayland is active **and** a battery is present.

---

## 5. Task Flags

Flags go in the middle column, comma-separated: `"MODE | flags | script.sh"`.

### Failure Handling

| Flag | Effect |
| :--- | :--- |
| `ignore` | Ignore failure and continue (aliases: `ignore-fail`, `true`) |
| `on_failure:ask` | Show interactive modal on failure **(default)** |
| `on_failure:abort` | Halt entire pipeline immediately |
| `on_failure:continue` | Record task as **failed** in state, continue pipeline |
| `on_failure:skip` | Record task as **skipped** in state, continue pipeline |
| `on_failure:manual` | Drop to a manual terminal prompt to resolve the failure |

> **`skip` vs `continue`:** Both continue the pipeline. The difference is what gets
> written to the state database — `skip` means "intentionally bypassed" and won't
> show as an error in logs; `continue` means "genuinely failed but non-blocking."

> **Shortcut:** `true` as the **first word of the command** (not in the flags column)
> also enables ignore — e.g. `"S | true 050_pacman_config.sh"`.

### Interactive / PTY Control

| Flag | Effect |
| :--- | :--- |
| `interactive` | Suspend TUI, give script full terminal control (aliases: `tui`, `prompt`, `fullscreen`, `tty`, `suspend`) |
| `no-interactive` | Force inline execution, no PTY (aliases: `noninteractive`, `inline`, `embedded`) |

> **Auto-detection:** If neither flag is set, the orchestrator scans the first 20 lines
> of the script for `# dusky_interactive=true`. If found, it runs in interactive mode
> automatically. Override with `no-interactive`.

### Execution Control

| Flag | Effect |
| :--- | :--- |
| `force` | Export `DUSKY_FORCE=1`, append `--force` to args (alias: `--force`) |
| `always` | Re-run every time, even if previously completed (alias: `always_run`) |
| `timeout:<seconds>` | Per-task timeout; overrides `[policy] task_timeout` |
| `retry:<count>` | Auto-retry on failure (default: 0) |
| `retry_delay:<seconds>` | Seconds between retries (default: 1.0) |

---

## 6. `once` Persistence Markers

Tasks flagged with `once` track successful execution in a **separate database**
(`~/Documents/state/once.db`) that **survives `--reset`**.

### When Does It Re-run?

| Flag | Behavior |
| :--- | :--- |
| `once` | Run once; re-run only if the **script file changes** (aliases: `run_once`, `sticky`, `once:content`, `once:hash`) |
| `once:forever` | Run once, **never re-run** — even if the file changes (aliases: `once:exact`, `once:permanent`) |

### Shared Across Profiles?

| Flag | Behavior |
| :--- | :--- |
| `once:profile` | Scoped to current profile only — **default** (alias: `once:local`) |
| `once:global` | Shared across **all profiles** on the machine (alias: `once:machine`) |

Combine mode + scope by listing both flags:

```toml
"U | once | 300_git_config.sh"                          # per-profile, re-run on change
"S | once:forever,once:global | 050_pacman_config.sh"   # machine-wide, never re-run
```

### Managing Markers

```bash
./orchestrator.sh --list-once                            # show all once markers
./orchestrator.sh --forget-once 300_git_config.sh        # delete marker → allows re-run
./orchestrator.sh --forget-once A.sh --forget-once B.sh  # repeatable
```

---

## 7. Detailed TOML Task Tables (`sequence.tasks`)

For complex one-off tasks, use tables instead of compact strings:

```toml
[[sequence.tasks]]
cmd = "050_pacman_config.sh"        # Script name (also accepts: script, path)
args = ["--auto"]                   # Arguments as array or string
mode = "S"                          # "U" or "S" (default: "U")
flags = "ignore"                    # Comma-separated flags (same as §5)
condition = "command:pacman"        # Condition WITHOUT "if:" prefix
timeout = 60.0                      # Per-task timeout in seconds
retry = 2                           # Retry count
retry_delay = 3.0                   # Seconds between retries
on_failure = "continue"             # "ask" | "abort" | "continue" | "skip" | "manual"
once = true                         # Enable once-marker tracking
once_mode = "forever"               # "content" or "forever"
once_scope = "profile"              # "profile" or "global"
always = false                      # Re-run regardless of state
force = false                       # Export DUSKY_FORCE=1, append --force
interactive = false                 # Force interactive PTY
ignore_fail = false                 # Ignore failures
```

> `scripts` and `tasks` can coexist — all `scripts` entries load first, then `tasks`.

---

## 8. Environment Variables Available to Scripts

Every executed script receives these variables automatically:

| Variable | Value |
| :--- | :--- |
| `DUSKY_FORCE` | `1` if force mode active, `0` otherwise |
| `DUSKY_INTERACTIVE` | `1` if running in interactive PTY, `0` otherwise |
| `DUSKY_ALWAYS` | `1` if task has `always` flag, `0` otherwise |
| `DUSKY_PROFILE_NAME` | Active profile name (e.g. `Main Setup`) |
| `DUSKY_PROFILE_FILE` | Absolute path to the `.toml` profile |
| `DUSKY_TASK_SCRIPT` | Script filename being executed |
| `DUSKY_TASK_PATH` | Resolved absolute path to the script |
| `DUSKY_TASK_MODE` | `U` or `S` |
| `DUSKY_TASK_INDEX` | Position in the sequence (1-based) |
| `DUSKY_TASK_LOG_FILE` | Path to this task's individual log file |
| `DUSKY_USER` | Target username |
| `DUSKY_USER_HOME` | Target user's home directory |
| `DUSKY_LOG_DIR` | Log directory path |
| `DUSKY_STATE_DIR` | State database directory path |
| `DUSKY_BACKUP_DIR` | Backup directory path |
| `DUSKY_VERSION` | Orchestrator version |
| `DUSKY_RUN_ID` | Unique session ID for this run |

Example usage in a script:

```bash
if [[ "$DUSKY_FORCE" == "1" ]]; then
    echo "Force mode — overwriting config"
fi
```

---

## 9. CLI Reference

### Profile Selection

```bash
./orchestrator.sh                                # Launch TUI profile selector
./orchestrator.sh --profile 01_main              # By filename stem
./orchestrator.sh --profile "Main Setup"          # By display name
./orchestrator.sh --profile 3                     # By index number
./orchestrator.sh --list                          # List all profiles
./orchestrator.sh --list-scripts                  # Show task list for selected profile
```

### State Management

```bash
./orchestrator.sh --profile 01_main --reset       # Wipe state DB for a profile
./orchestrator.sh --profile 01_main --reset-and-run  # Wipe + run immediately
./orchestrator.sh --list-once                     # Show all once markers
./orchestrator.sh --forget-once script.sh         # Delete once marker for a script
```

### Execution

| Flag | Effect |
| :--- | :--- |
| `--dry-run` | Validate and print sequence without executing |
| `--explain` | Show condition evaluation breakdown for each task |
| `--force` | Global force mode (`DUSKY_FORCE=1` + `--force` on all scripts) |
| `--manual`, `-m` | Prompt before every task |
| `--stop-on-fail` | Abort on first failure |
| `--task-timeout SEC` | Global per-task timeout (0 = disabled) |
| `--allow-root` | Allow running as root (not recommended) |
| `--sudo-password PASS` | Provide sudo password non-interactively |
| `--sudo-password-file FILE` | Read sudo password from a file |

### Git

| Flag | Effect |
| :--- | :--- |
| `--no-git-update` | Skip git self-update |
| `--git-update-only` | Run git update and exit |
| `--offline` | Skip all network-dependent steps |
| `--yes`, `-y` | Auto-confirm git update prompts |

### UI & Notifications

| Flag | Effect |
| :--- | :--- |
| `--ascii` | ASCII-only TUI (no Unicode symbols) |
| `--no-audio` | Disable audio cues |
| `--no-notify` | Disable desktop notifications |
| `--no-inhibit` | Don't inhibit sleep/idle |

### Diagnostics

| Flag | Effect |
| :--- | :--- |
| `--doctor` | Full environment check (versions, paths, deps) |
| `--version` | Print version and exit |
| `-h`, `--help` | Show help |

---

## 10. Interpreter Resolution

The orchestrator determines how to run each script automatically:

1. **ELF binary** → executed directly
2. **Shebang** (`#!/usr/bin/env python3`) → uses the declared interpreter
3. **File extension** → `.py` = Python, `.sh` = Bash, `.fish` = Fish
4. **Fallback** → Bash

---

## 11. File Locations

| What | Path |
| :--- | :--- |
| Profile state databases | `~/Documents/state/<Profile_Name>.db` |
| Persistent once markers | `~/Documents/state/once.db` |
| Execution logs | `~/Documents/logs/` |
| Git backups | `~/Documents/dusky_backups/` |
| Cache | `~/.cache/dusky/` |
| Runtime lock | `/run/user/<UID>/dusky/orchestrator.lock` |

> Run `./orchestrator.sh --doctor` to see exact resolved paths.
