#!/usr/bin/env python3
"""Linux PTY regressions. Python is a test dependency, not a TUI dependency."""
import fcntl
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import tempfile
import termios
import time

ROOT = Path(__file__).resolve().parents[1]


class Session:
    def __init__(self, launcher: Path, target: Path, *, engine: str = "kv", small: bool = False):
        self.master, self.slave = os.openpty()
        self.baseline = termios.tcgetattr(self.slave)
        self.output = bytearray()
        self.status: int | None = None
        self.resize(12 if small else 40, 60 if small else 110)
        self.pid = os.fork()
        if self.pid == 0:
            os.close(self.master)
            os.setsid()
            fcntl.ioctl(self.slave, termios.TIOCSCTTY, 0)
            for fd in (0, 1, 2):
                os.dup2(self.slave, fd)
            if self.slave > 2:
                os.close(self.slave)
            env = os.environ.copy()
            env.update(XDG_RUNTIME_DIR=str(target.parent), DUSKY_DEMO_ENGINE=engine)
            os.execve(str(launcher), [str(launcher), "--config", str(target)], env)

    def resize(self, rows: int, cols: int) -> None:
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def pump(self, timeout: float = 0.05) -> None:
        ready, _, _ = select.select([self.master], [], [], timeout)
        if ready:
            self.output.extend(os.read(self.master, 65536))

    def wait_for(self, needle: bytes, *, start: int = 0) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if needle in self.output[start:]:
                return
            self.pump()
        raise AssertionError(f"Missing {needle!r}: {self.output[-1500:]!r}")

    def send(self, keys: bytes, needle: bytes) -> None:
        start = len(self.output)
        os.write(self.master, keys)
        self.wait_for(needle, start=start)

    def finish(self, expected: int = 0) -> None:
        deadline = time.monotonic() + 5
        while self.status is None and time.monotonic() < deadline:
            self.pump()
            waited, status = os.waitpid(self.pid, os.WNOHANG)
            if waited:
                self.status = status
        assert self.status is not None, "TUI did not exit"
        self.pump(0)
        assert os.waitstatus_to_exitcode(self.status) == expected, self.output[-1500:]
        assert termios.tcgetattr(self.slave) == self.baseline, "Terminal settings were not restored"
        assert b"\x1b[?1049l" in self.output, "Alternate screen was not restored"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.status is None:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(self.pid, 0)
        os.close(self.master)
        os.close(self.slave)


def demo_session(directory: Path, *, engine: str = "kv", small: bool = False, interrupt: bool = False):
    target = directory / f"{engine}-{small}-{interrupt}.conf"
    with Session(ROOT / "apps/demo.sh", target, engine=engine, small=small) as session:
        if small:
            session.wait_for(b"Terminal too small")
            start = len(session.output)
            session.resize(40, 110)
            os.kill(session.pid, signal.SIGWINCH)
            session.wait_for(b"Enable Service", start=start)
        else:
            session.wait_for(b"Enable Service")
        # The label appears before the footer. A PTY can split one printf into
        # several reads, so await the completed frame before measuring idle.
        session.wait_for(b"\x1b[J", start=session.output.rfind(b"Enable Service"))
        start = len(session.output)
        session.pump(0.4)
        assert len(session.output) == start, "Idle TUI redrew unexpectedly"
        session.send(b"l", b"OFF")
        # A pasted reset shortcut must not reset the boolean back to true.
        session.send(b"\x1b[200~R\x1b[201~", b"OFF")
        session.send(b"\t\t\t\n", b"Notifications")
        session.send(b"\x1b", b"Advanced Settings")
        session.send(b"jjj\n", b"Catppuccin Mocha")
        session.send(b"j\n", b"Selected Theme: Nord")
        if interrupt:
            os.kill(session.pid, signal.SIGTERM)
        else:
            os.write(session.master, b"q")
        session.finish(143 if interrupt else 0)
    if engine == "kv":
        assert target.read_text(encoding="utf-8") == "service_enabled=false\n"
    else:
        assert not target.exists(), "Memory engine created a file"
    print(f"PASS: demo PTY engine={engine}, resize={small}, signal_exit={interrupt}")


def future_application(directory: Path):
    """A different schema/launcher works without editing any shared module."""
    launcher = directory / "future-app.sh"
    target = directory / "future.conf"
    launcher.write_text("""#!/usr/bin/env bash
set -Eeuo pipefail
source "$DUSKY_TEST_ROOT/frontend/ui.sh"
source "$DUSKY_TEST_ROOT/engines/kv.sh"
declare APP_TITLE='Future Application' APP_VERSION=v2
declare CONFIG_FILE="${XDG_CONFIG_HOME:-${HOME}/.config}/future/settings.conf"
declare -a TABS=(Settings Actions)
declare -ri MAX_DISPLAY_ROWS=6 BOX_INNER_WIDTH=64 ITEM_PADDING=24 ADJUST_THRESHOLD=30
register_items() {
    register 0 'Feature Enabled' 'feature|bool||||' true
    register 0 Text 'text|string||||'
    register 0 Counter 'counter|int||0|10|1' 3
    register 1 Ping 'ping|action||||'
}
action_ping() { set_status 'Pong from future app'; }
tui_main "$@"
""", encoding="utf-8")
    launcher.chmod(0o755)
    # The root is supplied by the test, not embedded as a machine-specific path.
    previous = os.environ.get("DUSKY_TEST_ROOT")
    os.environ["DUSKY_TEST_ROOT"] = str(ROOT)
    try:
        checked = subprocess.run([str(launcher), "--check"], capture_output=True, text=True, check=True)
        assert "2 tabs" in checked.stdout
        with Session(launcher, target) as session:
            session.wait_for(b"Feature Enabled")
            session.send(b"l", b"OFF")
            session.send(b"j\n", b"blank to UNSET")
            session.send(b"false\n", b"false")
            session.send(b"jl", b"4")
            session.send(b"\t\n", b"Pong from future app")
            start = len(session.output)
            os.kill(session.pid, signal.SIGTSTP)
            deadline = time.monotonic() + 5
            stopped = False
            while time.monotonic() < deadline:
                session.pump()
                waited, status = os.waitpid(session.pid, os.WNOHANG | os.WUNTRACED)
                if waited and os.WIFSTOPPED(status):
                    stopped = True
                    break
                if waited:
                    session.status = status
                    raise AssertionError("TUI exited instead of suspending")
            assert stopped, "TUI did not suspend"
            assert termios.tcgetattr(session.slave) == session.baseline, "Suspend did not restore the terminal"
            os.kill(session.pid, signal.SIGCONT)
            session.wait_for(b"Future Application", start=start)
            os.write(session.master, b"q")
            session.finish()
        assert target.read_text(encoding="utf-8") == "feature=false\ntext=false\ncounter=4\n"
    finally:
        if previous is None:
            del os.environ["DUSKY_TEST_ROOT"]
        else:
            os.environ["DUSKY_TEST_ROOT"] = previous
    print("PASS: new application, custom layout, string input, action, suspend/resume, and saves")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="dusky-terminal-") as temporary:
        directory = Path(temporary)
        demo_session(directory)
        demo_session(directory, engine="memory", small=True)
        demo_session(directory, interrupt=True)
        future_application(directory)
