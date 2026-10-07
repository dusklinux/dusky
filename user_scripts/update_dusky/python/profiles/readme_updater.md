# Dusky Updater — Profile Reference

Everything you need to write, edit, and debug updater `.toml` profiles.

> Run `python3 update_dusky.py --doctor` to verify your system and see resolved paths.
> Requires Python 3.14+.
> The canonical entry point is `python/update_dusky.py` (the control center and
> Dusky Tray launch it directly). The `update_dusky.sh` bash file in the parent
> directory is the **legacy v8 engine** — ignore it.
> Git-sync internals (the 5 GIT tasks) are documented separately in
> [`UPDATE_SYNC_BEHAVIOR.md`](../UPDATE_SYNC_BEHAVIOR.md).

---

## 1. Included Profiles

| File | Name | Purpose |
| :--- | :--- | :--- |
| `01_update_default.toml` | Default Arch/Hyprland Update Profile | Standard system update sequence and dotfile synchronization. |

Profiles are `*.toml` files in `profiles/` (or `$DUSKY_UPDATER_PROFILES_DIR`).
`--profile` takes a filename stem, a full filename, or an explicit path.
An unknown or ambiguous explicit profile is a fatal error listing available
profiles; the updater never silently runs a different profile. Malformed TOML
reports the file and parse error; missing-comma auto-repair is in-memory only
for `--dry-run` and otherwise warns with the repair location.

---

## 2. Profile TOML Structure

A profile is a TOML file that tells the updater what to sync and what to run.
The tables each serve one purpose:

- `[profile]` — identity and metadata
- `[git]` — upstream repo, branch, and script search directories
- `[conflict_resolutions]` — pin duplicate script names to exact paths
- `[sequence]` — the ordered task list (the only required table)

```toml
# ─── IDENTITY ────────────────────────────────────────────────────────────────
[profile]
name = "Default Arch/Hyprland Update Profile"  # Display name (defaults to filename stem)
description = "Standard system update sequence" # One-line summary
version = "9.5.0"        # Informational only — NOT read by the updater

# ─── GIT SYNC + SCRIPT SEARCH ────────────────────────────────────────────────
[git]
repo_url = "https://github.com/dusklinux/dusky"  # Upstream repo (default from global settings)
branch = "main"                                   # Upstream tracking branch

# Script search directories, searched IN ORDER for each script name.
# Relative paths resolve against ~ (the work tree). First match wins.
search_dirs = [
    "user_scripts/arch_setup_scripts/scripts",
    "user_scripts/arch_setup_scripts",
]

# ─── CONFLICT RESOLUTIONS ────────────────────────────────────────────────────
# Pin a script name to an exact path when it exists in multiple search dirs.
[conflict_resolutions]
# "update_checker.sh" = "user_scripts/update_dusky/update_checker.sh"

# ─── TASK SEQUENCE ───────────────────────────────────────────────────────────
[sequence]
tasks = [ ... ]             # Compact string entries (see §3)
```

> Everything is optional except `[sequence] tasks` — but without tasks the
> updater only runs the git sync phase.

---

## 3. Task Entry Format

Each line in `tasks = [...]` follows one of these forms:

```
"MODE | FLAGS | SCRIPT ARGS"
"MODE | SCRIPT ARGS"
"SCRIPT ARGS"                       ← defaults to U (user) mode
```

**Modes:** `U` = run as regular user. `S` = run with sudo. `GIT` is reserved for
the five built-in sync tasks that are injected at the start of every sequence.

Lines starting with `#` are comments and are skipped. Comments must be
whole-line — trailing `# ...` text inside a task string becomes script
arguments.

```toml
tasks = [
    # Basic: user mode, no flags
    "U | 002_pre_generated_colors.sh",

    # Sudo mode with arguments
    "S | 050_pacman_config.sh --auto",

    # Only run if a battery exists; re-run when the script content changes
    "U | if:battery,once | 135_battery_notify_service.sh --auto",

    # Ignore failures, retry up to 3 times with 5s between attempts
    "S | ignore-fail,retry:3,retry_delay:5 | 055_pacman_reflector.sh",
]
```

---

## 4. Conditions (`if:<condition>`)

Conditions decide whether a task runs. A false condition **defers** the task: it
is re-checked in later passes (so an earlier task can satisfy it — e.g. a package
installed mid-run), up to `max_defer_passes` (default 3, §12). If it is still
unmet when the passes run out, the task is marked skipped for this run (it is
re-evaluated on every future run).

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
| `if:gpu:<vendor>` | GPU vendor detected — `nvidia`, `intel`, `amd`, `vmware`, `virtio` |
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

> The profile header comments mention `if:pkg:<name>` — that key is stale; the
> real condition is `if:package:<name>`.

### Compound Conditions

Multiple `if:` flags are AND'd. Both colon forms and bare keywords work together:
a bare keyword (`wayland`, `battery`, `x11`, …) is treated as its own condition.
Condition values are case-sensitive (env vars, paths, units); repeated `if:`
flags express AND.

```toml
"U | if:gpu:nvidia,if:not:vm | 380_nvidia_open_source.sh --auto"   # ✓ all parts carry a value
"U | if:wayland,if:not:vm | 455_hyprctl_reload.sh"                 # ✓ bare keyword ANDs correctly
```

> A comma inside a single condition value (`command:ls,battery`) is not
> currently supported — commas separate conditions, and a value is not split
> back. Keep condition values comma-free. Unknown conditions/modes/flags and
> malformed quoting are fatal manifest errors; `not:<unknown>` is false (never
> true via negation).

### Evaluation Caching

Stable hardware/session conditions (`wayland`, `x11`, `graphical`, `ssh`,
`desktop`, `battery`, `btrfs`, `vm`, `baremetal`, `gpu`) are evaluated once
per run and cached. Everything else (`package`, `command`, `path`, `file`,
`dir`, `missing`, `service_active`, `user_service_active`, `group`, `env`) is
re-checked for every task, so earlier tasks can satisfy them (e.g. group
membership changed mid-run).

---

## 5. Task Flags

Flags go in the middle column, separated by commas or spaces (matched
case-insensitively): `"MODE | flags | script.sh"`.

| Flag | Effect |
| :--- | :--- |
| `ignore-fail` | Ignore failure and continue (aliases: `ignore`, `true`) |
| `interactive` | Suspend TUI, give script full terminal control (aliases: `tui`, `prompt`, `fullscreen`, `tty`, `suspend`) |
| `no-interactive` | Force inline execution, no PTY; overrides name/marker auto-detection (aliases: `noninteractive`, `inline`, `embedded`) |
| `once` | Run-once marker, content mode (aliases: `run_once`, `sticky`, `once:content`, `once:hash`) |
| `once:forever` | Run once, **never re-run** (aliases: `once:exact`, `once:permanent`) |
| `once:sealed` | Never re-run; warn once if the script content changes (aliases: `once:locked`) |
| `once:profile` | Marker scoped to current profile only — **default** (alias: `once:local`) |
| `once:global` | Marker shared across all profiles (alias: `once:machine`) |
| `if:<condition>` | Conditional execution; repeatable, all are AND'd (see §4) |
| `timeout:<seconds>` | Per-task execution timeout |
| `retry:<count>` | Auto-retry on failure (default: 0) |
| `retry_delay:<seconds>` | Seconds between retries (default: 1.0) |

### Interactive Auto-Detection

The updater also hands over the terminal when:

- the script name matches one of `reboot_post_lua_update.sh`, `tui_matugen.py`,
  `dusky_firefox_tui.sh`, **or**
- the script contains `#dusky_interactive=true` / `#dusky_interactive=1` in its
  first 20 lines (spaces are ignored, matching is case-insensitive).

The `no-interactive` (or `interactive`) flag **overrides** auto-detection:
explicit flags always win over name and marker heuristics.

---

## 6. `once` Persistence Markers

Tasks flagged with `once` track successful execution in a **separate database**
(`~/Documents/state/once.db`) that is never reset. A marker is created only
after the script exits 0; its key covers mode, name, args, resolved path,
scope, and profile, and is bound to a checksum of the script content.

### Re-running & Resetting Tasks

- **Run on every update**: Remove `once` from the task line in the TOML profile. The script will execute every time `update_dusky.py` runs (`once.db` is ignored).
- **Run one more time**: Keep `once` in the TOML profile and run `python3 update_dusky.py --forget-once SCRIPT`. This deletes the saved record so it executes on the next run.

### When Does It Re-run?

| `once_mode` | Script unchanged | Script changed |
| :--- | :--- | :--- |
| `content` (default) | **skip** | **run** (re-executes) |
| `forever` | skip | skip |
| `sealed` | skip | skip + one-time warning notification |

### Shared Across Profiles?

| Flag | Behavior |
| :--- | :--- |
| `once:profile` | Scoped to current profile only — **default** |
| `once:global` | Shared across **all profiles** on the machine |

---

## 7. Script Resolution & Conflicts

Before anything runs, every task's script is located and validated
(`resolve_and_validate_manifest`):

1. **Bare name** (`005_hypr_custom_config_setup.py`) → each `search_dirs` entry
   (relative to `~`) is tried in order; every readable match is collected.
2. **Name containing `/`** (`user_scripts/foo.py`) → treated as a direct path
   relative to `~` (absolute paths also work).
3. **No match** → task is flagged missing and skipped at runtime with a warning.
4. **One match** → used directly.
5. **Several matches** →
   - a `[conflict_resolutions]` entry for that name wins if it points to a
     readable file;
   - otherwise, if all copies are byte-identical, the first is used silently;
   - otherwise an interactive choice prompt appears — in `--force` mode,
     `--dry-run`, or a non-TTY stdin, the first match is picked automatically.

The interpreter is resolved per script: a shebang wins; otherwise the
extension map applies (`.py` → python3, `.sh` → bash, `.fish` → fish);
otherwise the `default_interpreter` (bash). A `.py` file with a bash shebang
(or vice versa) triggers a prompt, or auto-picks the shebang under `--force` /
`--dry-run` / non-TTY. If Python is needed but not installed, the updater
installs it via pacman (requires sudo).

---

## 8. CLI Reference

```bash
python3 update_dusky.py [OPTIONS]
```

| Flag | Effect |
| :--- | :--- |
| `--help`, `-h` | Show help and exit |
| `--version` | Print version and exit |
| `--doctor` | Print environment diagnostics (paths, profiles) and exit |
| `--profile NAME` | Profile to run: stem, filename, or path (default: `01_update_default`) |
| `--list` | List the active scripts of the selected profile and exit |
| `--list-once` | List persistent run-once markers and exit |
| `--forget-once SCRIPT...` | Delete persistent run-once marker(s) and exit |
| `--dry-run` | Simulate: skip git sync, run every check, execute nothing |
| `--skip-sync` | Skip the git sync phase, run only the script sequence |
| `--sync-only` | Run the git sync phase and exit |
| `--force` | Auto-answer interactive prompts: first match on script conflicts, shebang on interpreter conflicts |
| `--stop-on-fail` | Abort on the first failure, even `ignore-fail` ones |
| `--allow-diverged-reset` | In non-interactive mode, allow a hard reset on diverged or unrelated git history |
| `--post-self-update` | Internal: re-entry after the updater itself was updated by sync |
| `--handoff PATH` | Internal: bound restart-handoff file consumed once after a self-update restart |

---

## 9. Execution Model

**Phase 1 — Git sync.** Five GIT tasks are always injected at the start of the
sequence: `Git Bare Repo Validation`, `Fetch Upstream & Diff`,
`Forensic Collision Backup`, `Snapshot`,
`Apply Bare Updates (Reset)`. A failure here **halts the entire update** to
protect the system. `--skip-sync` and `--dry-run` bypass the phase (tasks shown
skipped). `--sync-only` ends after this phase. If the updater script itself was
changed by the sync, the updater validates the new files first and then
restarts into the new version before starting Phase 2, preserving the
operation lock across exec and carrying exact git outcomes plus the sudo
askpass (never password bytes) in a bound single-use handoff file. A missing
or invalid handoff runs a normal sync instead of trusting skipped tasks.

**Phase 2 — Sequence.** For each task: condition check (§4 — a false condition
defers the task and it is re-checked in later passes up to `max_defer_passes`),
once-marker check (§6), then execution. Failed runs are retried up to `retry` +
1 times with `retry_delay` between attempts. A timeout kills the whole process
group and is reported as exit 124.

**Failure handling:**

| Situation | Outcome |
| :--- | :--- |
| Success | Marked completed |
| Failure (default) | Marked failed, **pipeline continues sequence** |
| Failure, `ignore-fail` set | Marked skipped, pipeline continues |
| Failure + `--stop-on-fail` | **Pipeline aborts** immediately |
| Script missing at runtime | Marked skipped with warning |

**Sudo.** If any task is `S` mode, a sudo preflight runs before the sequence
(sudoers drop-in `99_dusky_*` in `/etc/sudoers.d`, temporary askpass helper in
the runtime dir, credential keep-alive heartbeat — 60 s per the shipped
settings, `[sudo] heartbeat_interval`). Sudo runs non-interactively (`-A` with
the askpass helper, or `-n` fail-fast). By default, child-output password
prompts are ignored (routing auth through askpass only), but can be automatically
fed from the cached sudo credential if `allow_insecure_password_autofeed = true`
is enabled in `[prompts]` (§12). Sudo inherits `DUSKY_*` and other environment
variables via `env_keep` (§12).
Invalid or timed-out drop-ins are never installed live: content is staged
outside the include directory, validated with `visudo` first, installed
atomically, and the install exit status is reported truthfully.

**Recovery data.** Invalid incoming scripts are blocked at the syntax gate and
restored from the previous working local commit to ensure the updater never leaves
a broken machine state. Staged/index content is preserved under
`your_changes_<ts>/.meta/staged/` with modes; staging is never auto-restored
(intentional policy: any staged blob, mode change, or staged deletion keeps
the backup as `pending-staged`; byte equality with the worktree never resolves
staging alone), so backups holding unrestored staging are retained and
never auto-pruned. Unexpected directories are preserved in conflict backups, never deleted.
File restores preserve executable modes (`0755`/`0644`) and are verified
(content/type/mode) before the backup is deleted; failures leave both
destination and payload recoverable.

**Child-input keys.** During PTY work, `Ctrl+O` toggles child-input mode. In
this mode ONLY `Ctrl+O` (exit mode) and `Ctrl+Q` (emergency abort) are
reserved; every other key — including `Escape` (`\x1b`), `Ctrl+F`/`Ctrl+L`,
function keys, `Tab`, arrows, and printable characters — is forwarded to the
child. Unrelated app bindings are disabled in this mode; search inputs and
modals still receive keys normally.

**Dependencies.** No auto-installation. If `textual`/`rich` are missing, the
updater exits with the exact `sudo pacman -S` command before any mutation.
`--help`/`--version`/`--doctor`/`--list`/`--list-once`/`--forget-once` and
`--dry-run` never install packages.

**Settings.** `DUSKY_UPDATER_SETTINGS` selects an explicit settings file; a
malformed or invalid explicitly selected file is rejected (exit 2) before any
mutation — never silently replaced by defaults. Invalid table types are
reported by `validate_global_config`, never traceback at import.

**Reports.** Final reports derive verdicts/counters from real outcomes:
missing required scripts or failures never appear as full success
(`WARNINGS`), aborts show `ABORTED`, dry-runs show `DRY-RUN`. Git/resolution
aborts, cancellations (130), and unexpected exceptions all finalize tails,
write reports, and set exit codes consistently.

Main log files (`dusky_update_*.log`) and *completed* `your_changes_*`
backups older than the retention window (`[paths] log_retention_days` /
`backup_retention_days`, default 14 days) are pruned automatically at the
start of a run. Collision, manual-merge, history, snapshot, staged, and
quarantine recovery data is never auto-pruned. Per-run log folders are kept
indefinitely.

---

## 10. Environment Variables

The updater itself honors two variables — scripts inherit the updater's
environment (there are no per-task `DUSKY_*` exports):

| Variable | Effect |
| :--- | :--- |
| `DUSKY_UPDATER_PROFILES_DIR` | Profile directory override (default: `profiles/` next to the script) |
| `DUSKY_UPDATER_SETTINGS` | Path to an alternative global settings TOML |

---

## 11. File Locations

| What | Path |
| :--- | :--- |
| Profiles | `~/user_scripts/update_dusky/python/profiles/` |
| Per-profile run state DB | `~/Documents/state/<profile-file-identity>.db` (identity = resolved profile file; legacy display-name DBs migrate once by rename) |
| Persistent once markers | `~/Documents/state/once.db` |
| Run logs | `~/Documents/logs/` — main log `dusky_update_<ts>_*.log` plus a per-run folder `<ts>_<Profile>_<run_id>/` with `dusky_update.log`, per-task `NNN_<script>.log`, and `report.json` / `report.md` (reports carry outcome/reason/exit-code/attempts/duration per task) |
| Git sync backups | `~/Documents/dusky_backups/` (`moved_aside_*`, `your_changes_*`, `full_snapshot_*`, `repo_history_*`, `manual_merge_*`; payload under `payload/`, metadata under `.meta/`) |
| Runtime dir (lock, askpass, restart handoff) | `/run/user/<UID>/dusky-updater/` (lock file, `askpass/`; falls back to `/tmp/dusky-updater-<UID>`) |
| Git bare repo | `~/dusky` (work tree: `~`) |

> These are the **defaults** — every path is overridable in
> `profiles/settings/update_dusky.toml` (§12).
> Run `python3 update_dusky.py --doctor` to see exact resolved paths.

---

## 12. Global Settings (`profiles/settings/update_dusky.toml`)

Optional global config next to the profiles. Every key has a safe default, so
the file only needs to exist when you want to change something.

| Table | Controls |
| :--- | :--- |
| `[ui]` | ASCII mode, sidebar width, log buffer size, theme paths, color palette, Unicode/ASCII symbols |
| `[paths]` | `documents_dir`, `namespace`, `lock_file`, askpass prefix, `logs_subdir`, `backups_subdir`, `state_subdir`, log/backup retention days |
| `[logging]` | Log files on/off, per-task logs, run reports |
| `[execution]` | Disk-space minimums, SQLite busy timeout, `max_defer_passes` (condition-deferral pass limit, default 3), default interpreter, extension→interpreter map |
| `[conditions]` | Commands for `package:` / `service_active:` / `user_service_active:` checks, GPU PCI vendor IDs |
| `[notifications]` | Desktop notifications, audio cues, audio players, sound files |
| `[sudo]` | Heartbeat interval, sudoers drop-in dir/prefix/timeout, `env_keep` |
| `[git]` | Upstream branch/repo defaults, fetch/clone timeouts & retries, env strip/inject |
| `[prompts]` | Auto-answer rules for interactive prompts (sudo password, pacman `[Y/n]`), `allow_insecure_password_autofeed`, prompt cooldown |

---

## 13. Typical Workflow

**Add a script to the sequence**

1. Drop the script anywhere under a `[git] search_dirs` folder (e.g.
   `~/user_scripts/arch_setup_scripts/scripts/`).
2. Add one line to `[sequence] tasks` — `"U | my_script.sh --flag"` (or
   `S |` for root; add flags from §5 as needed).
3. Test: `--list` shows it parsed, `--dry-run` runs the full pre-flight
   without executing anything.

**Create a new profile**

1. Copy `01_update_default.toml` → `02_<name>.toml` in `profiles/`.
2. Change `[profile] name`, adjust `[git] search_dirs` and `[sequence] tasks`.
3. Run it with `--profile 02_<name>` (or inspect with `--list` / `--doctor`).

**Change global behavior** — edit `profiles/settings/update_dusky.toml` (§12):
paths, retention, sudo, git, notifications, prompt auto-answers.

**Diagnose** — `--doctor` (paths, profiles), `--list` (parsed tasks),
`--dry-run` (full validation without side effects).
