#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: KEYLOGGER CONFIGURATION SCHEMA
===============================================================================
Target: ~/.config/dusky/settings/keylogger/config.json
Engine: JSON (persistent) + Systemd (service) via override
===============================================================================
Persistence vs Ephemeral model:
  - Persistent: SQLite DB + config JSON in ~/.config/dusky/settings/keylogger/
    (survives reboot, mode 0700/0600, manual delete only). Auto-created on
    fresh install if it doesn't already exist (json engine mkdir parents).
  - Ephemeral: Transcripts in /tmp (cleared on reboot, mode 0600).
    Configurable via transcript_dir/format; directory auto-created on first
    `dusky text` (fresh install).
Intelligent logic: atomic writes, perms, env overrides, old->new migration,
sudo auto-elevation for system service via systemd engine (AUTH_REQUIRED).
===============================================================================
"""

import sys
from pathlib import Path

_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

# The TUI router loads this file by path, outside this package directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# Share configuration defaults/path resolution with the daemon and CLI.
from dusky_keylogger.daemon import default_config_path, load_config
import shlex

load_config()


def _python_action(script: str, *args: str, pause: bool = True) -> str:
    venv_python = Path.home() / "contained_apps/uv/dusky_key_logger/bin/python"
    python = str(venv_python) if venv_python.is_file() else sys.executable
    script_path = Path(__file__).resolve().parent / script
    action = shlex.join([python, str(script_path), *args])
    if not pause:
        return action
    shell = action + '; result=$?; read -r -p "Press Enter to close..."; exit "$result"'
    return shlex.join(["bash", "-c", shell])


def _maintenance_action(command: str) -> str:
    return _python_action("dusky_keylogger/maintenance.py", command)


def _cli_action(*args: str, pause: bool = True) -> str:
    return _python_action("__main__.py", *args, pause=pause)

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "json"
TARGET_FILE = str(default_config_path())
APP_TITLE = "Dusky Keylogger"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Presets"

# Auto-elevate only if system service write needs it (systemd engine handles
# sudo -n prompt). Keep False so user config edits don't require root.
REQUIRE_ROOT = False

# =============================================================================
# 2. TABS + NOTICES
# =============================================================================
TABS = [
    "Persistence",
    "Ephemeral",
    "Daemon",
    "Tools",
    "Presets",
]

TAB_NOTICES = {
    0: {
        "level": "info",
        "position": "top",
        "message": "🔒 **Persistence** — SQLite DB & config in `~/.config/dusky/settings/keylogger/` (survives reboot, mode 0700/0600). Auto-created on fresh install. Contains passwords you typed — treat as secret.",
    },
    1: {
        "level": "warning",
        "position": "top",
        "message": "⚠️ **Ephemeral** — Transcripts in `/tmp` (cleared on reboot, `0600`). Change `Transcript Dir` to `~/tmp` or custom. Persistent stats stay in DB until you manually `rm` it.",
    },
    2: {
        "level": "info",
        "position": "bottom",
        "message": "ℹ️ **Daemon** — `dusky_keylogger.service` (system scope). Toggling may prompt for sudo password (polkit). Ensure your user is in `input` group for `/dev/input` access.",
    },
}

GLOBAL_POPUP = {
    "title": "Dusky Keylogger",
    "message": "Persistent DB is secret (passwords). Ephemeral transcripts in /tmp are cleared on reboot. Change persistence/ephemeral paths in their tabs. Service toggle may ask for sudo.",
    "level": "info",
    "require_confirm": False,
}

# =============================================================================
# 3. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: Persistence — where data survives reboot
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Persistent Logging",
            key="persistent_enabled",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Persistence",
            extended_help="**Persistent Logging**\n\nMaster toggle for SQLite WAL logging.\n- **ON** (default): every physical key press is classified and persisted to `data_dir/keys.db` (WAL, survives reboot, `0600`).\n- **OFF**: daemon discards new keystrokes; exports can still read existing history. Useful for privacy pauses.\n\nThe DB contains literals you typed — keep `data_dir` `0700`.",
            popup_message="Persistent logging toggled. Restart daemon to apply if running.",
        ),
        ConfigItem(
            label="Persistent Data Dir",
            key="data_dir",
            scope="DEFAULT",
            type_="string",
            default="~/.config/dusky/settings/keylogger/data",
            options=[
                "~/.config/dusky/settings/keylogger/data",
            ],
            hints=[
                "Persistent (survives reboot, auto-created, 0700) — the only persistent location",
            ],
            group="Persistence",
            extended_help="**Data Directory**\n\nWhere `keys.db` (WAL) lives. Persistent — survives reboot, mode `0700/0600`. Supports `~` and `$HOME` expansion, no hardcoded username.\n- Default `~/.config/dusky/settings/keylogger/data` (per user request — only persistent location).\n- If relative, resolved via `Path.home()`.\n- Auto-created on fresh install if missing (daemon `init_db` + TUI `mkdir -p`).\n- Override via env `DUSKY_KEYLOGGER_DATA_DIR` (highest priority).\n\nChanging the path does not move existing history. Stop the daemon before copying the DB; custom paths outside the default config tree also need a systemd `ReadWritePaths` override.",
            popup_message="Data dir changed. New DB will be created there on next daemon start. Old DB remains at previous location.",
        ),
        ConfigItem(
            label="Flush Interval (s)",
            key="flush_interval",
            scope="DEFAULT",
            type_="float",
            default=0.5,
            min_val=0.05,
            max_val=5.0,
            step=0.05,
            group="Performance",
            extended_help="**Flush Interval**\n\nSeconds between batched SQLite commits (dedicated writer thread, so evdev never blocks).\n- **0.05–0.2**: low latency, more fsyncs.\n- **0.5** (default): balanced.\n- **1–5**: less I/O, slightly higher loss window on crash.\n\nClamped to 0.05–5s. Change requires daemon restart.",
        ),
        ConfigItem(
            label="Ensure Persistent Dir",
            key="action_ensure_persistent_dir",
            scope="DEFAULT",
            type_="action",
            default=_maintenance_action('ensure-data'),
            group="Maintenance",
            extended_help="**Ensure Persistent Dir**\n\nCreates a missing persistent data directory with mode `0700` and preserves permissions of existing directories.\n\nUses no hardcoded username (`$HOME`/`Path.home()`).\n\nShows the resolved path and permissions for verification.",
        ),
        ConfigItem(
            label="Purge Persistent DB (Danger)",
            key="action_purge_db",
            scope="DEFAULT",
            type_="action",
            default=_maintenance_action('purge'),
            group="Maintenance",
            confirm_message="Permanently delete persistent keystroke DB (keys.db + WAL/SHM)? This cannot be undone and erases all stats.",
            extended_help="**Purge DB**\n\nStops the system service, then deletes `keys.db` + `-wal`/`-shm` in the configured `data_dir`. Stop any foreground daemon first. The service stays stopped; restart it when ready.\n\nUse after testing with `seed` data.",
            force_interactive=True,
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: Ephemeral — transcripts cleared on reboot
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Ephemeral Transcripts",
            key="ephemeral_enabled",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Ephemeral",
            extended_help="**Ephemeral Toggle**\n\nMaster toggle for `dusky text` transcript generation.\n- **ON** (default): `dusky text` writes readable transcripts to `transcript_dir` (default `/tmp`, cleared on reboot, `0600`).\n- **OFF**: `dusky text` warns and still writes if forced with `--out`, but default dir generation is skipped.\n\nTranscripts contain literals — ephemeral dir is `1777` (`/tmp`) so file `0600` protects per-user.",
        ),
        ConfigItem(
            label="Transcript Dir",
            key="transcript_dir",
            scope="DEFAULT",
            type_="string",
            default="/tmp",
            options=[
                "/tmp",
            ],
            hints=[
                "Ephemeral tmpfs (cleared on reboot, 1777) — the only ephemeral location",
            ],
            group="Ephemeral",
            extended_help="**Transcript Directory**\n\nEphemeral — where `dusky text --period today` writes `dusky-typed-<period>-<date>-<uid>.[txt|md]` (default `/tmp`, cleared on reboot, `0600`).\n- Supports `~`, `$HOME`, relative (relative → `Path.home()/...`), and env `DUSKY_TRANSCRIPT_DIR` (highest priority).\n- Auto-created on first `dusky text` if missing (`mkdir -p`). Fresh install dir doesn't exist — TUI ensures creation.\n- Persistent stats stay in `data_dir` (`~/.config/dusky/settings/keylogger/data`) regardless.\n\nIntelligent: `/tmp` leaf not chmodded (keep `1777`).",
            popup_message="Transcript dir changed. Next `dusky text` will use new ephemeral location (old transcripts remain until reboot).",
        ),
        ConfigItem(
            label="Transcript Format",
            key="transcript_format",
            scope="DEFAULT",
            type_="cycle",
            default="text",
            options=["text", "markdown"],
            group="Ephemeral",
            extended_help="**Transcript Format**\n\nDefault format for `dusky text` when `--format` not given.\n- **text**: raw joined chars (⌫ for backspace, \\n for Enter, \\t for Tab).\n- **markdown**: header + metadata (`Period`, `Range`, `Generated`, `Characters`, note about ephemeral vs persistent) + ````text` code fence (chooses a fence longer than any recorded backtick run). File extension `.md` vs `.txt`.\n\nOverride via `DUSKY_TRANSCRIPT_FORMAT` env or `--format` CLI (CLI wins).\n\nChange takes effect immediately for next transcript.",
        ),
        ConfigItem(
            label="Ensure Ephemeral Dir",
            key="action_ensure_ephemeral_dir",
            scope="DEFAULT",
            type_="action",
            default=_maintenance_action('ensure-transcripts'),
            group="Maintenance",
            extended_help="**Ensure Ephemeral Dir**\n\nCreates the configured `transcript_dir` if missing (fresh install). Creates directories as your user with mode `0700`; existing directory permissions are preserved, including `/tmp` mode `1777`.",
            force_interactive=True,
        ),
        ConfigItem(
            label="Clear Ephemeral Transcripts",
            key="action_clear_ephemeral",
            scope="DEFAULT",
            type_="action",
            default=_maintenance_action('clear-transcripts'),
            group="Maintenance",
            confirm_message="Delete all ephemeral transcripts (dusky-typed* in transcript_dir)? Persistent DB will NOT be touched.",
            extended_help="**Clear Ephemeral**\n\nDeletes `dusky-typed-*.[txt|md]` in the configured `transcript_dir` (default `/tmp`). Simulates reboot clear. Persistent `keys.db` in `data_dir` is NOT touched (survives reboot).",
            force_interactive=True,
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: Daemon & Service
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Keylogger Service",
            key="dusky_keylogger.service",
            scope="system",
            type_="bool",
            default=False,
            group="Service",
            engine_type_override="systemd",
            extended_help="**Dusky Keylogger Service**\n\nSystem systemd unit `dusky_keylogger.service` (Type=notify, `User=$USER`, `Group=input`, `DeviceAllow=char-input r`, `WatchdogSec=30`).\n- **ON**: `systemctl enable --now` (auto-start at boot, captures via evdev, needs `input` group).\n- **OFF**: `disable --now` (stops logging, persistent DB retained).\n\nToggling may prompt for sudo password (polkit). Check `log_level` and `flush_interval` in other tabs. Logs to `data_dir/logs/daemon.log` (rotating).",
        ),
        ConfigItem(
            label="Log Level",
            key="log_level",
            scope="DEFAULT",
            type_="cycle",
            default="info",
            options=["debug", "info", "warning", "error"],
            group="Daemon",
            extended_help="**Log Level**\n\nDaemon verbosity for `journalctl -u dusky_keylogger` and `data_dir/logs/daemon.log` (rotating 5 MiB ×3).\n- **debug**: device discovery, inotify, and hydration diagnostics.\n- **info** (default): start/stop, device discovery, SYN_DROPPED.\n- **warning/error**: quieter.\n\nRequires daemon restart to apply (`systemctl restart dusky_keylogger` or toggle service off/on).",
        ),
        ConfigItem(
            label="Restart Service",
            key="action_restart_service",
            scope="DEFAULT",
            type_="action",
            default="bash -c 'set -e; echo \"Restarting dusky_keylogger.service...\"; sudo -p \"[sudo] Restart service: \" systemctl restart dusky_keylogger; sleep 1; systemctl status dusky_keylogger --no-pager -n 20; echo \"---\"; echo \"Done.\"; read -p \"Press Enter...\"'",
            group="Service",
            extended_help="**Restart**\n\nRuns `sudo systemctl restart dusky_keylogger` (prompts for sudo if needed), then `status -n 20`. Use after changing `flush_interval`/`log_level`/`data_dir`.",
            confirm_message="Restart dusky_keylogger.service? Needs sudo.",
            force_interactive=True,
        ),
        ConfigItem(
            label="View Service Status",
            key="action_status",
            scope="DEFAULT",
            type_="action",
            default="bash -c 'systemctl status dusky_keylogger --no-pager -l -n 50; echo \"---\"; echo \"Data: $HOME/.config/dusky/settings/keylogger/data (persistent)\"; ls -lh \"$HOME/.config/dusky/settings/keylogger/data/\" 2>/dev/null | head -n 20; echo \"---\"; echo \"Also old: $HOME/.local/share/dusky-keylogger/\"; ls -lh \"$HOME/.local/share/dusky-keylogger/\" 2>/dev/null | head -n 5; echo \"---\"; echo \"Config: $HOME/.config/dusky/settings/keylogger/config.json (new) or $HOME/.config/dusky-keylogger/config.json (old, auto-migrated)\"; ls -l \"$HOME/.config/dusky/settings/keylogger/config.json\" \"$HOME/.config/dusky-keylogger/config.json\" 2>/dev/null | head; read -p \"Press Enter...\"'",
            group="Diagnostics",
            extended_help="**Status**\n\nShows `systemctl status` plus `ls` of persistent data dir (new `~/.config/dusky/settings/keylogger/data` and old fallback `~/.local/share/dusky-keylogger`) and both config paths.",
            force_interactive=True,
        ),
        ConfigItem(
            label="View Journal Logs",
            key="action_journal",
            scope="DEFAULT",
            type_="action",
            default="bash -c 'journalctl -u dusky_keylogger --no-pager -n 80 --since \"today\"; echo \"--- follow with: journalctl -u dusky_keylogger -f\"; read -p \"Press Enter...\"'",
            group="Diagnostics",
            extended_help="**Journal**\n\nShows `journalctl -u dusky_keylogger -n 80 --since today`. For live follow use `journalctl -f` in terminal.",
            force_interactive=True,
        ),
        ConfigItem(
            label="Check Input Group",
            key="action_check_input_group",
            scope="DEFAULT",
            type_="action",
            default=_maintenance_action('input-group'),
            group="Diagnostics",
            extended_help="**Input Group**\n\n`/dev/input/event*` is `crw-rw---- root:input`. Daemon needs `input` group (`Group=input` in service). This checks account and current-session groups and adds missing membership with `sudo usermod -aG input` (needs sudo, auto-elevates, then logout/login).",
            force_interactive=True,
        ),
        ConfigItem(
            label="List Keyboards",
            key="action_list_keyboards",
            scope="DEFAULT",
            type_="action",
            default=_cli_action("devices"),
            group="Diagnostics",
            extended_help="**List Keyboards**\n\nRuns `dusky devices` via venv python (discovers `EV_KEY <256` keyboards, respects `DUSKY_DEVICE_FILTER`).",
            force_interactive=True,
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: Tools — ephemeral generation + persistent analytics
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Generate Text Transcript",
            key="action_gen_text",
            scope="DEFAULT",
            type_="action",
            default=_cli_action("text", "--period", "today", "--format", "text"),
            group="Ephemeral Tools",
            extended_help="**Generate Transcript (Text)**\n\nRuns `dusky text --period today --format text` → ephemeral `transcript_dir/dusky-typed-today-<date>-<uid>.txt` (`0600`, cleared on reboot). Persistent DB untouched. Change dir/format in Ephemeral tab or via `--transcript-dir`/`--format`.",
            force_interactive=True,
        ),
        ConfigItem(
            label="Generate Markdown Transcript",
            key="action_gen_markdown",
            scope="DEFAULT",
            type_="action",
            default=_cli_action("text", "--period", "today", "--format", "markdown"),
            group="Ephemeral Tools",
            extended_help="**Generate Markdown**\n\nRuns `dusky text --period today --format markdown` → `... .md` with header (`Period`, `Range`, `Generated`, `Characters`, note about ephemeral `/tmp` vs persistent `keys.db`) + ````text` fence.",
            force_interactive=True,
        ),
        ConfigItem(
            label="Dashboard (Live TUI)",
            key="action_dashboard",
            scope="DEFAULT",
            type_="action",
            default=_cli_action("dashboard", pause=False),
            group="Persistent Tools",
            extended_help="**Dashboard**\n\nLaunches `dusky dashboard` (Rich Live + matugen) — period tabs `1:Today 2:Week 3:Month 4:All`, view tabs `Tab: Overview → Keys → Chars → Transcript → Recent`. Shows human-readable keys (Space vs KEY_SPACE), detailed metrics with % + bars, hourly/daily trends, transcript preview (same text as `dusky text` terminal, ephemeral /tmp), and recent events. `q` quit, `r` refresh, `1-4` period, `Tab` view.",
            force_interactive=True,
        ),
        ConfigItem(
            label="Stats Today",
            key="action_stats_today",
            scope="DEFAULT",
            type_="action",
            default=_cli_action("stats", "--period", "today"),
            group="Persistent Tools",
            extended_help="**Stats Today**\n\nRuns `dusky stats --period today` — totals, printable/backspace, keys/min, WPM, top keys/chars, daily 14d. Persistent DB (`keys.db`) survives reboot until manual `rm`.",
            force_interactive=True,
        ),
        ConfigItem(
            label="Stats Week",
            key="action_stats_week",
            scope="DEFAULT",
            type_="action",
            default=_cli_action("stats", "--period", "week", "--top", "12"),
            group="Persistent Tools",
            extended_help="**Stats Week**\n\nRuns `dusky stats --period week` (ISO week, Mon–Sun).",
            force_interactive=True,
        ),
        ConfigItem(
            label="Recent Events",
            key="action_recent",
            scope="DEFAULT",
            type_="action",
            default=_cli_action("events", "--limit", "30"),
            group="Persistent Tools",
            extended_help="**Recent Events**\n\nRuns `dusky events --limit 30` — last 30 `Time Key Char Kind Device` from `keys.db` (persistent).",
            force_interactive=True,
        ),
        ConfigItem(
            label="Seed Demo Data (Testing)",
            key="action_seed",
            scope="DEFAULT",
            type_="action",
            default=_cli_action("seed", "--days", "7"),
            group="Maintenance",
            confirm_message="Seed synthetic demo data into persistent DB (7 days, ~200-1200 events/day)? Will persist until you Purge DB.",
            extended_help="**Seed**\n\nRuns `dusky seed --days 7` — synthetic `Test Keyboard` events (clearly labeled) for testing stats/dashboard without typing. Inserts in 2k chunks. Remove with `Purge Persistent DB`.",
            force_interactive=True,
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 4: Presets
    # -------------------------------------------------------------------------
    4: [
        ConfigItem(
            label="Privacy Pause",
            key="preset_privacy_pause",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Presets",
            preset_payload={
                "DEFAULT.persistent_enabled": False,
                "DEFAULT.ephemeral_enabled": False,
            },
            confirm_message="Pause both persistent and ephemeral logging?",
            extended_help="**Privacy Pause**\n\nRestart the daemon after applying. Disables both `persistent_enabled` and `ephemeral_enabled`. Daemon will drop keystrokes (no DB write). Transcripts still possible with explicit `--out` but default dir generation skipped. Re-enable via Balanced or Full.",
        ),
        ConfigItem(
            label="Balanced (Default)",
            key="preset_balanced",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Presets",
            preset_payload={
                "DEFAULT.persistent_enabled": True,
                "DEFAULT.ephemeral_enabled": True,
                "DEFAULT.flush_interval": 0.5,
                "DEFAULT.log_level": "info",
                "DEFAULT.data_dir": "~/.config/dusky/settings/keylogger/data",
                "DEFAULT.transcript_dir": "/tmp",
                "DEFAULT.transcript_format": "text",
            },
            extended_help="**Balanced**\n\nDefault: persistent ON (`~/.config/dusky/settings/keylogger/data`, `0.5s` flush, `info`), ephemeral ON (`/tmp`, `text`). Recommended for daily use.",
        ),
        ConfigItem(
            label="Full Verbose",
            key="preset_verbose",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Presets",
            preset_payload={
                "DEFAULT.persistent_enabled": True,
                "DEFAULT.ephemeral_enabled": True,
                "DEFAULT.flush_interval": 0.05,
                "DEFAULT.log_level": "debug",
                "DEFAULT.transcript_format": "markdown",
            },
            extended_help="**Verbose**\n\nLowest latency (`0.05s`), `debug` diagnostic logs, `markdown` transcripts. More I/O — use for testing.",
        ),
        ConfigItem(
            label="Export Existing History",
            key="preset_ephemeral_only",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Presets",
            preset_payload={
                "DEFAULT.persistent_enabled": False,
                "DEFAULT.ephemeral_enabled": True,
                "DEFAULT.transcript_dir": "/tmp",
                "DEFAULT.transcript_format": "text",
            },
            confirm_message="Pause new collection and allow exports of existing history? Persistent DB logging will pause (existing DB retained).",
            extended_help="**Export Existing History**\n\nDisables new collection (`persistent_enabled=False`), exports existing DB history as transcripts (`/tmp`, `text`). Existing `keys.db` retained but no new writes until you re-enable.",
        ),
        ConfigItem(
            label="Reset to Defaults",
            key="preset_factory_reset",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Presets",
            confirm_message="Reset all keylogger settings to factory defaults?",
            preset_payload={"__ALL_DEFAULTS__": True},
            extended_help="**Factory Reset**\n\nResets every key in this file to its `default` (strict snapshot — unlisted keys revert). Does NOT delete `keys.db` — use `Purge Persistent DB` for that.",
        ),
    ],
}

# =============================================================================
# DIRECT EXECUTION HANDLER (router invocation)
# =============================================================================
if __name__ == "__main__":
    import subprocess

    script_path = Path(__file__).resolve()
    main_router = Path.home() / "user_scripts" / "dusky_tui" / "python" / "main" / "main.py"
    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
