"""Command line interface for Dusky Keylogger.

Subcommands:
    daemon      Run the always-on logging daemon (used by systemd)
    stats       Print keystroke statistics for today / week / month / all (detailed terminal table)
    dashboard   Open the live Rich dashboard (matugen, tabs: Overview/Keys/Chars/Transcript/Recent, period 1-4)
    status      Show daemon + database status
    devices     List discovered keyboards (diagnostics)
    events      Print recent key events
    text        Print everything typed as readable text (ephemeral /tmp + markdown, also visible in TUI Transcript tab)
    seed        Generate demo data for testing (clearly labeled)
"""

import argparse
lazy import asyncio
lazy import json
import os
lazy import random
lazy import sqlite3
import sys
lazy from datetime import datetime, timedelta
lazy from pathlib import Path

lazy from rich import box
lazy from rich.console import Console
lazy from rich.table import Table

from . import __version__
lazy from . import keycodes as kc
lazy from .daemon import (
    Daemon,
    default_data_dir,
    get_transcript_dir,
    get_transcript_format,
    load_config,
    resolve_path,
)
lazy from .listener import KeyListener, KeyPress
lazy from .stats import daily_series, period_range, summarize
lazy from .storage import SCHEMA_VERSION, EventRow, KeyStore, row_from_press

lazy from rich import get_console
lazy import grp
lazy import re
lazy import subprocess
lazy from .maintenance import ensure_input_group
lazy from .dashboard_tui import main as dashboard_main


def _get_store(args: argparse.Namespace) -> KeyStore:
    data_dir = getattr(args, "data_dir", None)
    # Resolve via env/default; allow explicit override for testing.
    base = resolve_path(data_dir) if data_dir else default_data_dir()
    base.mkdir(parents=True, exist_ok=True, mode=0o700, parent_mode=0o700)
    db_path = base / "keys.db"
    store = KeyStore(db_path)
    if not store.path.exists():
        store.init_db()
    else:
        # Ensure existing DB has correct restrictive permissions (defense in depth).
        try:
            os.chmod(store.path, 0o600)
        except OSError:
            pass
        # If an old-schema (v1) DB is present, migrate before readers query
        # columns that only exist in v2. Cheap read-only check first.
        conn = sqlite3.connect(store.path.absolute().as_uri() + "?mode=ro", uri=True)
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()
        if version > SCHEMA_VERSION:
            raise sqlite3.OperationalError(f"unsupported schema version: {version}")
        if version < SCHEMA_VERSION:
            store.init_db()
    return store


def cmd_daemon(args: argparse.Namespace) -> int:
    if os.geteuid() != 0 and grp.getgrnam("input").gr_gid not in {os.getgid(), *os.getgroups()}:
        ensure_input_group()
        get_console().print("Log out/in, then run the foreground daemon again.")
        return 1

    async def _run() -> int:
        daemon = Daemon(data_dir=args.data_dir)
        await daemon.run()
        return 0

    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        return 0


def _render_stats(store: KeyStore, period: str, top: int) -> None:
    stats = summarize(store, period, limit_keys=top)
    start_s = stats.start.strftime("%Y-%m-%d %H:%M")
    end_s = stats.end.strftime("%Y-%m-%d %H:%M")
    get_console().print(
        f"[bold cyan]Dusky Keylogger[/] -- {period} stats ({start_s} -> {end_s})"
    )

    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    rows = [
        ("Total keystrokes", f"{stats.total_keys:,}"),
        ("Printable characters", f"{stats.printable:,}"),
        ("Backspace", f"{stats.backspace:,}"),
        ("Delete", f"{stats.delete:,}"),
        ("Enter", f"{stats.enter:,}"),
        ("Tab", f"{stats.tab:,}"),
        ("Escape", f"{stats.escape:,}"),
        ("Modifiers", f"{stats.modifiers:,}"),
        ("Navigation keys", f"{stats.navigation:,}"),
        ("Function keys", f"{stats.function:,}"),
        ("Other keys", f"{stats.other:,}"),
        ("Active minutes", f"{stats.active_minutes:,}"),
        ("Keys / minute", f"{stats.keys_per_minute:.1f}"),
        ("Words / minute (est.)", f"{stats.words_per_minute:.1f}"),
        ("Backspace ratio", f"{stats.backspace_ratio * 100:.1f}%"),
    ]
    for label, value in rows:
        table.add_row(label, value)
    get_console().print(table)

    if stats.top_keys:
        keys_table = Table(title="Most used keys", box=box.SIMPLE)
        keys_table.add_column("Key", style="bold")
        keys_table.add_column("Count", justify="right")
        for name, count in stats.top_keys:
            keys_table.add_row(name, f"{count:,}")
        get_console().print(keys_table)

    if stats.top_chars:
        chars_table = Table(title="Most typed characters", box=box.SIMPLE)
        chars_table.add_column("Char", style="bold")
        chars_table.add_column("Count", justify="right")
        for char, count in stats.top_chars:
            display = char if char != " " else "(space)"
            chars_table.add_row(display, f"{count:,}")
        get_console().print(chars_table)

    day_table = Table(title="Daily totals (last 14 days)", box=box.SIMPLE)
    day_table.add_column("Date", style="bold")
    day_table.add_column("Keys", justify="right")
    for day, count in daily_series(store, 14):
        day_table.add_row(day, f"{count:,}")
    get_console().print(day_table)


def cmd_stats(args: argparse.Namespace) -> int:
    store = _get_store(args)
    if args.json:
        stats = summarize(store, args.period, limit_keys=args.top)
        print(
            json.dumps(
                {
                    "period": stats.period,
                    "total_keys": stats.total_keys,
                    "printable": stats.printable,
                    "backspace": stats.backspace,
                    "delete": stats.delete,
                    "enter": stats.enter,
                    "tab": stats.tab,
                    "escape": stats.escape,
                    "modifiers": stats.modifiers,
                    "navigation": stats.navigation,
                    "function": stats.function,
                    "other": stats.other,
                    "active_minutes": stats.active_minutes,
                    "keys_per_minute": round(stats.keys_per_minute, 2),
                    "words_per_minute": round(stats.words_per_minute, 2),
                    "backspace_ratio": round(stats.backspace_ratio, 4),
                    "top_keys": stats.top_keys,
                    "top_chars": stats.top_chars,
                },
                indent=2,
            )
        )
        return 0
    _render_stats(store, args.period, args.top)
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    store = _get_store(args)
    dashboard_main(store.path)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    store = _get_store(args)
    db_path = store.path
    try:
        total = store.total() if db_path.exists() else 0
    except Exception:
        total = 0
    try:
        first, last = store.first_last_ts() if db_path.exists() else (None, None)
    except Exception:
        first, last = None, None

    cfg = load_config()
    tdir = get_transcript_dir(cfg)
    tformat = get_transcript_format(cfg)

    get_console().print("[bold cyan]Dusky Keylogger -- status[/]")
    status = Table(box=box.SIMPLE_HEAVY)
    status.add_column("Item", style="bold")
    status.add_column("Value")
    status.add_row("Version", __version__)
    status.add_row("Database (persistent)", str(db_path))
    status.add_row("Total events (persistent)", f"{total:,}")
    status.add_row("Transcript dir (ephemeral)", f"{tdir}  [dim](cleared on reboot, change via config/env)[/]")
    status.add_row("Transcript format", tformat)
    if first is not None and last is not None:
        status.add_row(
            "First event",
            datetime.fromtimestamp(first / 1000).strftime("%Y-%m-%d %H:%M:%S"),
        )
        status.add_row(
            "Last event",
            datetime.fromtimestamp(last / 1000).strftime("%Y-%m-%d %H:%M:%S"),
        )
    get_console().print(status)

    try:
        active = subprocess.run(
            ["systemctl", "is-active", "dusky_keylogger"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        enabled = subprocess.run(
            ["systemctl", "is-enabled", "dusky_keylogger"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        get_console().print(f"Service: [bold]{active}[/] (enabled: {enabled})")
    except OSError:
        pass
    return 0


def cmd_devices(_args: argparse.Namespace) -> int:
    listener = KeyListener()
    devices = listener._find_keyboards()
    if not devices:
        get_console().print("[yellow]No keyboards found.[/]")
        return 1
    table = Table(title="Discovered keyboards", box=box.SIMPLE_HEAVY)
    table.add_column("Device", style="bold")
    table.add_column("Path")
    for device in devices:
        table.add_row(device.name, device.path)
        device.close()
    get_console().print(table)
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    store = _get_store(args)
    rows = store.recent(args.limit)
    if not rows:
        get_console().print("[yellow]No events recorded yet.[/]")
        return 0
    table = Table(title=f"Recent {len(rows)} events", box=box.SIMPLE_HEAVY)
    for col in ("Time", "Key", "Char", "Kind", "Device"):
        table.add_column(col)
    for row in rows:
        dt = datetime.fromtimestamp(row.ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
        table.add_row(dt, row.key_name, row.char or "", row.kind, row.device)
    get_console().print(table)
    return 0


def _resolve_transcript_path(
    args: argparse.Namespace, period: str, fmt: str, config: dict | None = None
) -> Path:
    """Resolve transcript output path respecting CLI > env > config > /tmp.

    Never hardcodes a username; uses $HOME expansion and Path.home().
    Auto-creates the parent if needed (caller). Returns absolute Path.
    """
    # Explicit --out always wins
    if getattr(args, "out", None):
        p = Path(args.out).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        return p
    # --transcript-dir explicit CLI override (if present)
    cli_tdir = getattr(args, "transcript_dir", None)
    if cli_tdir:
        base = resolve_path(cli_tdir)
    else:
        # config + env (load_config already folds env)
        cfg = config if config is not None else load_config()
        base = get_transcript_dir(cfg)
    # Normalize extension by format
    ext = ".md" if fmt == "markdown" else ".txt"
    day = datetime.now().strftime("%Y-%m-%d")
    # Sanitize period (already validated)
    filename = f"dusky-typed-{period}-{day}-{os.getuid()}{ext}"
    return base / filename


def _format_markdown(
    text: str, period: str, start: datetime, end: datetime, db_path: Path | None = None
) -> str:
    """Wrap raw transcript in markdown with metadata header."""
    db_note = str(db_path) if db_path else "the configured data_dir/keys.db"
    header = (
        f"# Dusky Keylogger — Transcript\n\n"
        f"- **Period:** `{period}`  \n"
        f"- **Range:** `{start.strftime('%Y-%m-%d %H:%M')}` → `{end.strftime('%Y-%m-%d %H:%M')}`  \n"
        f"- **Generated:** `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`  \n"
        f"- **Characters:** `{len(text):,}`  \n"
        f"- **Note:** Backspace rendered as `⌫`, Delete as `⌦`, Enter as newline, Tab as tab.  \n"
        f"  Default exports live in `/tmp`; custom directories may persist.  \n"
        f"  Source events and characters stay in `{db_note}` until you delete them.\n\n"
        f"---\n\n"
    )
    # Choose a longer fence without altering any recorded text.
    fence = "`" * max(3, 1 + max((len(m.group()) for m in re.finditer(r"`+", text)), default=0))
    return header + fence + "text\n" + text + "\n" + fence + "\n"


def cmd_text(args: argparse.Namespace) -> int:
    """Stitch everything typed in a period into readable text / markdown.

    Read-only: derives the transcript from the event store and writes it
    to a configured directory (default /tmp). The source event rows and
    recorded characters remain in the persistent database until deleted.

    The output directory is configurable via:
      1. --out / --transcript-dir CLI flags (highest priority)
      2. env DUSKY_TRANSCRIPT_DIR
      3. config.json "transcript_dir"
      4. default "/tmp"
    Similarly format via --format / env DUSKY_TRANSCRIPT_FORMAT / config.
    The directory is auto-created on first use (fresh install).
    """
    cfg = load_config()
    if not cfg["ephemeral_enabled"] and not getattr(args, "out", None):
        get_console().print("Transcript generation is disabled; use --out to export explicitly.")
        return 0
    store = _get_store(args)
    try:
        start, end = period_range(args.period)
    except ValueError as exc:
        get_console().print(f"[red]Invalid period: {exc}[/]")
        return 2

    # Resolve format: CLI > env > config
    cli_fmt = getattr(args, "format", None)
    if cli_fmt:
        fmt = "markdown" if str(cli_fmt).lower() in {"markdown", "md"} else "text"
    else:
        fmt = get_transcript_format(cfg)

    parts: list[str] = []
    for row in store.iter_between(start, end):
        if row.kind == kc.KIND_PRINTABLE and row.char:
            parts.append(row.char)
        elif row.kind == kc.KIND_BACKSPACE:
            parts.append("\u232b")  # ⌫
        elif row.kind == kc.KIND_DELETE:
            parts.append("⌦")
        elif row.kind == kc.KIND_ENTER:
            parts.append("\n")
        elif row.kind == kc.KIND_TAB:
            parts.append("\t")
    raw_text = "".join(parts)
    # Apply markdown wrapper if requested
    output_text = (
        _format_markdown(raw_text, args.period, start, end, db_path=store.path)
        if fmt == "markdown"
        else raw_text
    )

    out_path = _resolve_transcript_path(args, args.period, fmt, cfg)
    try:
        # Auto-create transcript dir for fresh installs; don't chmod the system
        # tmp root itself, only the file (and leaf dir if we created it).
        out_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700, parent_mode=0o700)
        # Create with 0600 from the start: no world-readable window, and
        # O_NOFOLLOW refuses to write through a pre-planted symlink in /tmp.
        fd = os.open(
            out_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), 0o600)
            fh.write(output_text)
    except OSError as exc:
        get_console().print(f"[red]Could not write transcript to {out_path}: {exc}[/]")
        return 1
    Console(stderr=True).print(
        f"Typed transcript ({args.period}, {fmt}) — {len(raw_text):,} chars → {out_path}",
        markup=False,
    )
    # Also echo to stdout for piping; use raw_text for text, full markdown for md
    sys.stdout.write(output_text)
    return 0


def _synthetic_press(keycode: int, ts_us: int) -> KeyPress:
    return KeyPress(
        keycode=keycode,
        key_name=kc.key_name(keycode),
        char=kc.char_for(keycode, False, False),
        kind=kc.classify_key(keycode),
        device="Test Keyboard",
        ts_us=ts_us,
    )


def cmd_seed(args: argparse.Namespace) -> int:
    if args.days < 1 or args.days > 365:
        get_console().print("[red]--days must be between 1 and 365[/]")
        return 2
    store = _get_store(args)
    store.init_db()
    now = datetime.now()
    random.seed(args.seed)
    keys_pool = [
        (kc.KEY_A, 8),
        (kc.KEY_B, 2),
        (kc.KEY_LEFTSHIFT, 6),
        (kc.KEY_BACKSPACE, 3),
        (kc.KEY_SPACE, 7),
        (kc.KEY_ENTER, 2),
        (kc.KEY_LEFTCTRL, 4),
        (kc.KEY_TAB, 1),
        (kc.KEY_COMMA, 2),
        (kc.KEY_DELETE, 1),
        (kc.KEY_HOME, 1),
        (kc.KEY_ESC, 1),
        (kc.KEY_CAPSLOCK, 1),
        (kc.KEY_LEFTMETA, 2),
        (kc.KEY_F11, 1),
        (kc.KEY_KP5, 1),
    ]
    weights = [w for _, w in keys_pool]
    codes = [c for c, _ in keys_pool]
    rows: list[EventRow] = []
    for day_offset in range(args.days):
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            days=day_offset
        )
        events_today = random.randint(200, 1200)
        for _ in range(events_today):
            ts = day_start + timedelta(
                seconds=random.randint(0, 16 * 3600),
                microseconds=random.randint(0, 999_000),
            )
            keycode = random.choices(codes, weights=weights)[0]
            ts_us = int(ts.timestamp() * 1_000_000)
            press = _synthetic_press(keycode, ts_us)
            rows.append(row_from_press(press))
    # Insert in chunks to avoid huge single transaction for large --days.
    inserted = 0
    CHUNK = 2000
    for i in range(0, len(rows), CHUNK):
        inserted += store.insert_many(rows[i : i + CHUNK])
    get_console().print(
        f"[green]Seeded {inserted:,} demo events across {args.days} day(s).[/]\n"
        f"[dim]These are synthetic test records. Remove them with: "
        f"rm {store.path}[/]"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dusky",
        description="Dusky Keylogger -- always-on keystroke statistics daemon",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_daemon = sub.add_parser("daemon", help="Run the logging daemon (foreground)")
    p_daemon.add_argument("--data-dir", default=None, help="Override data directory")
    p_daemon.set_defaults(func=cmd_daemon)

    p_stats = sub.add_parser("stats", help="Print statistics")
    p_stats.add_argument(
        "--period", choices=["today", "week", "month", "all"], default="today"
    )
    p_stats.add_argument("--top", type=int, default=12, help="Top N keys/chars")
    p_stats.add_argument("--json", action="store_true", help="Machine-readable output")
    p_stats.add_argument("--data-dir", default=None)
    p_stats.set_defaults(func=cmd_stats)

    p_dash = sub.add_parser(
        "dashboard", help="Open the live Rich dashboard (matugen, tabs, detailed stats + transcript)"
    )
    p_dash.add_argument("--data-dir", default=None)
    p_dash.set_defaults(func=cmd_dashboard)

    p_status = sub.add_parser("status", help="Show daemon and database status")
    p_status.add_argument("--data-dir", default=None)
    p_status.set_defaults(func=cmd_status)

    p_dev = sub.add_parser("devices", help="List discovered keyboard devices")
    p_dev.set_defaults(func=cmd_devices)

    p_events = sub.add_parser("events", help="Print recent key events")
    p_events.add_argument("--limit", type=int, default=25)
    p_events.add_argument("--data-dir", default=None)
    p_events.set_defaults(func=cmd_events)

    p_text = sub.add_parser(
        "text",
        help="Print everything typed as readable text (ephemeral /tmp, persistent stats stay in DB)",
    )
    p_text.add_argument(
        "--period", choices=["today", "week", "month", "all"], default="today"
    )
    p_text.add_argument(
        "--out",
        default=None,
        help="Output path (default $DUSKY_TRANSCRIPT_DIR or config transcript_dir or /tmp/dusky-typed-<period>-<date>-<uid>.[txt|md])",
    )
    p_text.add_argument(
        "--transcript-dir",
        default=None,
        help="Override ephemeral directory (default from config/env, fallback /tmp)",
    )
    p_text.add_argument(
        "--format",
        choices=["text", "markdown", "md"],
        default=None,
        help="Output format; text = raw, markdown = header + code fence (default from config)",
    )
    p_text.add_argument("--data-dir", default=None)
    p_text.set_defaults(func=cmd_text)
    # Alias: export = text
    p_export = sub.add_parser("export", help="Alias for 'text' (supports --format markdown)")
    p_export.add_argument(
        "--period", choices=["today", "week", "month", "all"], default="today"
    )
    p_export.add_argument("--out", default=None, help="Output path")
    p_export.add_argument("--transcript-dir", default=None, help="Override ephemeral directory")
    p_export.add_argument(
        "--format", choices=["text", "markdown", "md"], default=None, help="Output format"
    )
    p_export.add_argument("--data-dir", default=None)
    p_export.set_defaults(func=cmd_text)

    p_seed = sub.add_parser("seed", help="Generate demo data (testing only)")
    p_seed.add_argument("--days", type=int, default=14)
    p_seed.add_argument("--seed", type=int, default=42)
    p_seed.add_argument("--data-dir", default=None)
    p_seed.set_defaults(func=cmd_seed)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
