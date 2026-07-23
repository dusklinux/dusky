# Dusky Arch Linux Master Orchestrator - Profile Configuration & Flags Reference

This guide provides a succinct, comprehensive reference for configuring profiles (`.toml` files) and understanding all task flags, condition specifiers, execution options, and CLI arguments in the Dusky Arch Linux Orchestrator.

---

## 1. Profile Structure (`.toml` Sections)

A profile TOML file (e.g. `01_main.toml`) consists of six main configuration tables:

```toml
[profile]
name = "Main Setup"
description = "Full Arch Linux install with dotfiles"
schema_version = 2
post_script_delay = 0       # Delay in seconds between tasks (default: 0)

[policy]
audio = true                # Enable audio notifications (default: true)
notify = true               # Enable desktop notifications (default: true)
inhibit_sleep = true        # Inhibit system sleep during runs (default: true)
task_timeout = 0            # Task timeout in seconds (0 = disabled)
stop_on_fail = false        # Stop pipeline immediately on failure (default: false)
manual = false              # Prompt before executing every task (default: false)
force = false               # Pass force flags to scripts (default: false)

[git]
enabled = true              # Enable git self-update (default: false)
git_dir = "~/dusky"         # Git repository directory
work_tree = "~/"            # Git working tree directory
remote = "origin"           # Git remote name (default: "origin")

[search_dirs]
dirs = [                    # Directories searched for task scripts
    "~/user_scripts/arch_setup_scripts/scripts",
    "~/user_scripts/arch_setup_scripts",
]

[conflict_resolutions]
# Explicit mappings to resolve duplicate script names across search dirs:
# "script.sh" = "~/user_scripts/arch_setup_scripts/scripts/script.sh"

[sequence]
scripts = [ ... ]           # Compact string task array
tasks = [ ... ]             # Detailed task table array
```

---

## 2. Sequence Task Entry Formats

Tasks can be defined in `[sequence]` using either compact string format (`sequence.scripts`) or detailed TOML tables (`sequence.tasks`).

### Compact String Format (`sequence.scripts`)

Format: `"MODE | FLAGS | COMMAND ARGS"` or `"MODE | COMMAND ARGS"` or `"COMMAND ARGS"`

```toml
scripts = [
    "U | 002_pre_generated_colors.sh",
    "S | 050_pacman_config.sh --auto",
    "U | if:battery,once | 135_battery_notify_service.sh --auto",
]
```

### Execution Modes (`MODE`)
- **`U`** (User): Executed as the regular user.
- **`S`** (Sudo): Executed with administrative privileges via `sudo`.

---

## 3. Task Flags Reference

Flags can be comma-separated inside the string entry flags column or specified in task table keys.

| Flag | Category | Description |
| :--- | :--- | :--- |
| `true`, `ignore`, `ignore-fail` | Failure | Ignore script failure; mark as ignored and continue pipeline. |
| `interactive`, `tui`, `prompt`, `fullscreen`, `tty`, `suspend` | PTY / UI | Force interactive PTY session and suspend TUI if needed. |
| `no-interactive`, `noninteractive`, `inline`, `embedded` | PTY / UI | Disable interactive PTY mode; execute inline. |
| `force`, `--force` | Options | Export `DUSKY_FORCE=1` and append `--force` to command arguments. |
| `always`, `always_run` | Execution | Re-evaluate and run task on every run pass, ignoring past completed state. |
| `on_failure:ask` | Policy | Ask user interactively via modal on task failure (default). |
| `on_failure:abort` | Policy | Abort entire execution pipeline immediately on failure. |
| `on_failure:continue` | Policy | Mark task failed and continue to next task. |
| `on_failure:skip` | Policy | Mark task skipped and continue to next task. |
| `on_failure:manual` | Policy | Open manual terminal resolution prompt on failure. |
| `timeout:<seconds>` | Runtime | Set per-task execution timeout in seconds. |
| `retry:<count>` | Runtime | Set number of automatic retry attempts on task failure. |
| `retry_delay:<seconds>` | Runtime | Set delay in seconds between retries. |

---

## 4. `once` Persistence Flags

Tasks marked with `once` record successful execution in a persistent database (**`~/Documents/state/once.db`**). Unlike standard profile state, `once` markers **persist even if you run `--reset`**.

| Flag | Scope | Behavior |
| :--- | :--- | :--- |
| `once`, `run_once`, `sticky`, `once:content`, `once:hash` | Profile | Runs task **only once**. Skipped on future runs and `--reset`. Reruns only if script file content/hash changes. |
| `once:forever`, `once:exact`, `once:permanent` | Profile | Runs task **strictly once, forever**. Never reruns even if script content changes or `--reset` is called. |
| `once:global`, `once:machine` | Machine | Global scope: shares once marker across **all profiles** on the system. |
| `once:profile`, `once:local` | Profile | Profile scope: scopes once marker to current profile (default). |

*Example:*
```toml
"S | once:forever | 050_pacman_config.sh --auto"
"U | once,once:global | 300_git_config.sh"
```

---

## 5. Condition Evaluator Reference (`if:<condition>`)

Task conditions control whether a script should run based on the system state.

### System & Environment Conditions
- `if:wayland` - Active Wayland display server (`WAYLAND_DISPLAY`).
- `if:x11` - Active X11 display server (`DISPLAY`).
- `if:graphical` - Either Wayland or X11 display active.
- `if:desktop` - Active desktop environment session.
- `if:ssh` - Running inside an active SSH connection.
- `if:vm` - Virtual machine environment (QEMU/KVM, VMware, VirtualBox, etc.).
- `if:baremetal` - Physical hardware (non-VM).
- `if:battery` - System has battery power supply (`/sys/class/power_supply`).
- `if:btrfs` - Root filesystem (`/`) is Btrfs.

### Check & Path Conditions
- `if:command:<cmd>` - Command binary available in system `$PATH`.
- `if:path:<path>` - File or directory exists.
- `if:missing:<path>` - File or directory does NOT exist.
- `if:file:<path>` - File exists.
- `if:dir:<path>` - Directory exists.
- `if:package:<pkg>` - Pacman package installed (`pacman -Qq <pkg>`).
- `if:group:<group>` - Current user belongs to specified user group.
- `if:gpu:<vendor>` - GPU vendor detected (`nvidia`, `intel`, `amd`).
- `if:service_active:<service>` - Systemd system service is active.
- `if:user_service_active:<svc>` - Systemd user service is active.
- `if:env:<var>` - Environment variable is defined and non-empty.

### Logic & Compound Conditions
- `if:not:<condition>` - Inverts condition (e.g. `if:not:vm`).
- `if:always` / `if:true` / `if:yes` - Always evaluates to true.
- `if:never` / `if:false` / `if:no` - Always evaluates to false.
- **Compound conditions**: Multiple conditions can be comma-separated or provided in multiple `if:` flags (e.g. `if:vm,if:command:git` or `if:wayland,battery`).

---

## 6. Detailed TOML Task Table Schema (`sequence.tasks`)

Instead of string lines, tasks can also be configured as TOML tables in `sequence.tasks`:

```toml
[[sequence.tasks]]
cmd = "050_pacman_config.sh"
args = ["--auto"]
mode = "S"
flags = "ignore"
condition = "command:pacman"
timeout = 60.0
retry = 2
retry_delay = 3.0
on_failure = "continue"
once = true
once_mode = "forever"        # "content" or "forever"
once_scope = "profile"        # "profile" or "global"
always = false
force = false
interactive = false
ignore_fail = false
```

---

## 7. Command Line Interface (CLI Flags)

The orchestrator wrapper (`./orchestrator.sh`) and Python engine (`./orchestrator.py`) support the following command line arguments:

### Profile Execution & Selection
- `--profile PROFILE` - Execute specific profile by name, stem (`01_main`), or index number (`1`).
- `--list` - List all available profiles and exit.
- `--list-scripts` - List sequence of selected profile and exit.
- `--reset` - Reset state database for selected profile (`~/Documents/state/<profile>.db`) and exit.
- `--reset-and-run` - Reset profile state database, then immediately run pipeline.

### Persistent Once Markers
- `--list-once` - List all persistent once markers from `~/Documents/state/once.db`.
- `--forget-once SCRIPT` - Delete persistent once marker(s) for a script name or path.

### Execution Controls
- `--dry-run` - Validate manifest and print execution sequence without executing scripts.
- `--explain` - Print detailed decision breakdown for each task in profile and exit.
- `--force` - Export `DUSKY_FORCE=1` and pass `--force` to scripts.
- `--manual`, `-m` - Prompt before executing every script.
- `--stop-on-fail` - Halt execution immediately if any script fails.
- `--task-timeout SECONDS` - Set global per-task timeout in seconds (0 = disabled).
- `--sudo-password PASS` - Provide sudo password non-interactively.
- `--sudo-password-file FILE` - Read sudo password from file.
- `--allow-root` - Allow running orchestrator directly as root user.

### Self-Update & Git Controls
- `--no-git-update` - Skip git self-update check.
- `--git-update-only` - Execute git self-update and exit.
- `--offline` - Skip network checks and git update.
- `--yes`, `-y` - Assume yes for git update prompts.

### Diagnostics & UI Controls
- `--doctor` - Run environment diagnostics, path checks, and dependency verification.
- `--ascii` - Render TUI with ASCII characters instead of Unicode symbols.
- `--no-audio` - Disable sound notifications.
- `--no-notify` - Disable desktop notifications.
- `--no-inhibit` - Disable sleep/idle inhibitor during execution.
- `--version`, `-v` - Display version number and exit.
- `-h`, `--help` - Display help menu and exit.
