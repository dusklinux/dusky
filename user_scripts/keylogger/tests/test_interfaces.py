import importlib.util
import io
import json
import os
import pwd
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console

from dusky_keylogger import dashboard_tui as dash
from dusky_keylogger import keycodes as kc
from dusky_keylogger.storage import KeyStore, row_from_press
from test_core import press

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args):
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHON_COLORS="0")
    return subprocess.run([sys.executable, "-X", "dev", "-W", "error",
                           "-m", "dusky_keylogger", *args], env=env,
                          text=True, capture_output=True, timeout=10)


def test_module_entry_and_lazy_unused_dependencies():
    proc = run_cli("--version")
    assert proc.returncode == 0 and "2.0.0" in proc.stdout
    code = """
import sys
from dusky_keylogger.cli import main
try:
    main(['--help'])
except SystemExit as exc:
    assert exc.code == 0
assert 'rich' not in sys.modules
assert 'sqlite3' not in sys.modules
assert 'evdev' not in sys.modules
assert 'dusky_keylogger.dashboard_tui' not in sys.modules
"""
    proc = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True,
                          env=dict(os.environ, PYTHONPATH=str(ROOT)), timeout=10)
    assert proc.returncode == 0, proc.stderr


def test_seed_stats_events_and_export_roundtrip(tmp_path):
    data = tmp_path / "data"
    proc = run_cli("seed", "--days", "2", "--data-dir", str(data))
    assert proc.returncode == 0, proc.stderr
    proc = run_cli("stats", "--period", "all", "--json", "--data-dir", str(data))
    assert proc.returncode == 0, proc.stderr
    stats = json.loads(proc.stdout)
    assert 400 <= stats["total_keys"] <= 2400
    assert stats["active_minutes"] > 0
    proc = run_cli("events", "--limit", "5", "--data-dir", str(data))
    assert proc.returncode == 0 and "Recent 5 events" in proc.stdout
    out = tmp_path / "nested/output.txt"
    proc = run_cli("text", "--period", "all", "--out", str(out), "--data-dir", str(data))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == out.read_text(encoding="utf-8")
    assert "Typed transcript" in proc.stderr
    assert out.stat().st_mode & 0o777 == 0o600
    assert KeyStore(data / "keys.db").total() == stats["total_keys"]


def test_transcript_literal_characters_and_markdown_fence(store, tmp_path):
    rows = [row_from_press(press(code)) for code in (
        kc.KEY_A, kc.KEY_GRAVE, kc.KEY_GRAVE, kc.KEY_GRAVE,
        kc.KEY_GRAVE, kc.KEY_BACKSPACE, kc.KEY_DELETE, kc.KEY_ENTER, kc.KEY_TAB,
    )]
    store.insert_many(rows)
    out = tmp_path / "output.md"
    proc = run_cli("export", "--format", "markdown", "--out", str(out),
                   "--data-dir", str(store.path.parent))
    assert proc.returncode == 0, proc.stderr
    assert "`````text\na````⌫⌦\n\t\n`````\n" in proc.stdout
    assert proc.stdout == out.read_text(encoding="utf-8")


@pytest.mark.parametrize("period", dash.PERIOD_LIST)
@pytest.mark.parametrize("view", dash.VIEW_LIST)
@pytest.mark.parametrize("width,height", [(80, 24), (120, 50), (40, 12)])
def test_dashboard_every_view_and_period(store, period, view, width, height):
    store.insert_many([row_from_press(press(code)) for code in (
        kc.KEY_A, kc.KEY_ENTER, kc.KEY_LEFTSHIFT, kc.KEY_DELETE, kc.KEY_TAB,
    )])
    stream = io.StringIO()
    console = Console(file=stream, width=width, height=height, color_system=None)
    panel = dash.build_layout(period, view, dash.DEFAULT_COLORS,
                              dict.fromkeys(dash.VIEW_LIST, 1000), height, store)
    console.print(panel)
    output = stream.getvalue()
    assert "Render error" not in output
    assert output.strip()


@pytest.mark.parametrize("value,expected", [
    ("#abc", "#aabbcc"), ("#123456", "#123456"),
    ("red", "red"), ("bogus", "#efe0d5"), ("#12345678", "#efe0d5"),
    (None, "#efe0d5"), ("red bold", "#efe0d5"),
])
def test_theme_validation(value, expected):
    assert dash._safe_color(value, "#efe0d5") == expected


def test_dashboard_cache_invalidates_after_database_replacement(store):
    store.insert_many([row_from_press(press(kc.KEY_A))])
    first = dash._cached(store, ("replacement",), lambda: store.recent(1)[0].keycode)
    replacement = KeyStore(store.path.with_name("replacement.db"))
    replacement.init_db()
    replacement.insert_many([row_from_press(press(kc.KEY_B))])
    replacement.path.replace(store.path)
    assert store.max_id() == 1
    second = dash._cached(store, ("replacement",), lambda: store.recent(1)[0].keycode)
    assert (first, second) == (kc.KEY_A, kc.KEY_B)


@pytest.mark.parametrize("sequence,command", [
    (b"\t", "next_view"), (b"\x1b[Z", "prev_view"),
    (b"\x1b[A", "up"), (b"\x1b[6~", "page_down"),
    (b"\x1b[<64;1;1M", "scroll_up"), (b"\x1b[<65;1;1M", "scroll_down"),
    (b"\x03", "force_quit"), (b"4", "period_all"),
])
def test_terminal_sequences(sequence, command):
    assert dash.parse_input_sequence(sequence) == (command, len(sequence))
    if sequence.startswith(b"\x1b"):
        for n in range(1, len(sequence)):
            assert dash.parse_input_sequence(sequence[:n]) == (None, 0)


@pytest.fixture
def installer():
    spec = importlib.util.spec_from_file_location("audit_installer", ROOT / "keylogger_installer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_installer_renders_and_verifies_service(tmp_path, monkeypatch, installer):
    service = tmp_path / "dusky_keylogger.service"
    monkeypatch.setattr(installer, "SERVICE_FILE", service)
    commands = []
    monkeypatch.setattr(installer, "run", lambda cmd: commands.append(cmd))
    installer.install_service(sys.executable, pwd.getpwuid(os.getuid()).pw_name,
                              str(tmp_path))
    assert commands == [["systemctl", "daemon-reload"]]
    proc = subprocess.run(["systemd-analyze", "verify", str(service)],
                          text=True, capture_output=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    content = service.read_text(encoding="utf-8")
    assert "$VENV_PYTHON" not in content
    assert ' -m dusky_keylogger daemon' in content


def test_installer_dry_run_does_not_escalate(installer, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("dry-run attempted escalation or mutation")
    monkeypatch.setattr(installer.os, "execv", forbidden)
    monkeypatch.setattr(installer, "run", forbidden)
    assert installer.main(["--dry-run", "--offline"]) == 0


def test_installer_purge_refuses_foreground_owner(tmp_path, monkeypatch, installer):
    data = tmp_path / ".config/dusky/settings/keylogger/data"
    store = KeyStore(data / "keys.db")
    store.init_db()
    store.insert_many([row_from_press(press())])
    monkeypatch.setattr(installer, "_ensure_root_for_uninstall", lambda _: None)
    user = pwd.getpwuid(os.getuid()).pw_name
    monkeypatch.setattr(installer, "original_user", lambda: (user, str(tmp_path)))
    monkeypatch.setattr(installer, "SERVICE_FILE", tmp_path / "absent.service")
    monkeypatch.setattr(installer, "run", lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "", ""))
    with store.collector_lock():
        with pytest.raises(SystemExit):
            installer.main(["--uninstall", "--purge"])
    assert store.total() == 1
    lock = data / "keys.db.lock"
    inode = lock.stat().st_ino
    custom = data.parent / "custom/keys.db"
    custom.parent.mkdir()
    custom.write_bytes(b"retain custom data")
    config = data.parent / "config.json"
    config.write_text("{}", encoding="utf-8")
    assert installer.main(["--uninstall", "--purge"]) == 0
    assert not store.path.exists()
    assert lock.stat().st_ino == inode
    assert custom.read_bytes() == b"retain custom data"
    assert not config.exists()


def test_refused_purge_preserves_installed_files(tmp_path, monkeypatch, installer):
    store = KeyStore(tmp_path / ".config/dusky/settings/keylogger/data/keys.db")
    store.init_db()
    unit = tmp_path / "dusky_keylogger.service"
    unit.write_text("Existing unit", encoding="utf-8")
    venv = installer.venv_dir(str(tmp_path))
    venv.mkdir(parents=True)
    marker = venv / "keep"
    marker.touch()
    user = pwd.getpwuid(os.getuid()).pw_name
    monkeypatch.setattr(installer, "_ensure_root_for_uninstall", lambda _: None)
    monkeypatch.setattr(installer, "original_user", lambda: (user, str(tmp_path)))
    monkeypatch.setattr(installer, "SERVICE_FILE", unit)
    commands = []
    def run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(installer, "run", run)
    with store.collector_lock():
        with pytest.raises(SystemExit):
            installer.main(["--uninstall", "--purge"])
    assert unit.read_text(encoding="utf-8") == "Existing unit"
    assert marker.exists()
    assert commands == [["systemctl", "stop", installer.SERVICE_NAME]]


def test_tui_python_actions_are_shell_valid_and_preserve_failure(tmp_path, monkeypatch):
    # Import the real schema while routing its home-relative frontend import
    # to the directly relevant frontend package in the workspace.
    monkeypatch.syspath_prepend(str(ROOT.parent / "dusky_tui"))
    spec = importlib.util.spec_from_file_location("audit_tui", ROOT / "tui_keylogger.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for items in module.SCHEMA.values():
        for item in items:
            if item.type_ == "action":
                import shlex
                tokens = shlex.split(item.default)
                if tokens[:2] == ["bash", "-c"]:
                    proc = subprocess.run(["bash", "-n", "-c", tokens[2]], capture_output=True, text=True)
                    assert proc.returncode == 0, proc.stderr
    # Running from an unrelated working directory no longer breaks fallback.
    command = module._cli_action("--version")
    proc = subprocess.run(command, shell=True, cwd=tmp_path, input="\n", text=True, capture_output=True)
    assert proc.returncode == 0 and "2.0.0" in proc.stdout, proc.stderr
    command = module._cli_action("not-a-command")
    proc = subprocess.run(command, shell=True, cwd=tmp_path, input="\n", text=True, capture_output=True)
    assert proc.returncode == 2


def test_dashboard_pty_exit_restores_terminal(store):
    import fcntl
    import pty
    import select
    import struct
    import termios
    import time
    master, slave = pty.openpty()
    before = termios.tcgetattr(slave)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
    env = dict(os.environ, PYTHONPATH=str(ROOT), TERM="xterm-256color")
    proc = subprocess.Popen([sys.executable, "-X", "dev", "-W", "error",
                             "-m", "dusky_keylogger", "dashboard", "--data-dir",
                             str(store.path.parent)], stdin=slave, stdout=slave,
                            stderr=slave, env=env)
    output = bytearray()
    sent = False
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                output.extend(os.read(master, 65536))
            if not sent and b"Dashboard" in output:
                os.write(master, b"\t\t\tjq")
                sent = True
            if proc.poll() is not None:
                while select.select([master], [], [], 0)[0]:
                    output.extend(os.read(master, 65536))
                break
        assert proc.poll() == 0, bytes(output).decode(errors="replace")
        assert b"closed cleanly" in output
        assert termios.tcgetattr(slave) == before
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("enable", [False, True])
def test_installer_preserves_service_state(tmp_path, monkeypatch, installer, active, enable):
    user = pwd.getpwuid(os.getuid()).pw_name
    monkeypatch.setattr(installer.os, "geteuid", lambda: 0)
    monkeypatch.setattr(installer, "original_user", lambda: (user, str(tmp_path)))
    monkeypatch.setattr(installer, "python_version", lambda *_: (3, 15, 0))
    monkeypatch.setattr(installer, "user_in_group", lambda *_: True)
    commands = []
    def build_venv(*args, **kwargs):
        commands.append(["update-package"])
        return sys.executable
    monkeypatch.setattr(installer, "build_venv", build_venv)
    def run(command, **kwargs):
        commands.append(command)
        code = 0 if command != ["systemctl", "is-active", installer.SERVICE_NAME] or active else 3
        return subprocess.CompletedProcess(command, code, "active" if active else "inactive", "")
    monkeypatch.setattr(installer, "run", run)
    monkeypatch.setattr(installer, "install_service", lambda *_: commands.append(["replace-unit"]))
    assert installer.main(["--enable"] if enable else []) == 0
    stop = ["systemctl", "stop", installer.SERVICE_NAME]
    start = ["systemctl", "start", installer.SERVICE_NAME]
    enabled = ["systemctl", "enable", "--now", installer.SERVICE_NAME]
    assert (stop in commands) == active
    assert (enabled in commands) == enable
    assert (start in commands) == (active and not enable)
    if active:
        assert commands.index(stop) < commands.index(["update-package"])
        assert commands.index(stop) < commands.index(["replace-unit"])
