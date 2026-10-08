"""Observe finite setting commands without blocking the GTK main loop."""

from __future__ import annotations

import logging
import os
import shutil
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from gi.repository import Gio, GLib

from lib import utility

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ActionResult:
    success: bool
    message: str


class ActionHandle:
    def __init__(self, proc: Gio.Subprocess, callback: Callable[[ActionResult], None], timeout: int):
        self.proc = proc
        self.callback = callback
        self.pgid = int(proc.get_identifier() or 0)
        self.finished = False
        self.timed_out = False
        self.timeout_source = GLib.timeout_add_seconds(timeout, self._on_timeout) if timeout else 0
        _active.add(self)
        proc.communicate_utf8_async(None, None, self._on_finished)

    def _on_timeout(self) -> bool:
        self.timeout_source = 0
        self.timed_out = True
        self._stop_process_group()
        return GLib.SOURCE_REMOVE

    def _stop_process_group(self) -> None:
        if self.pgid:
            try:
                os.killpg(self.pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.proc.force_exit()

    def _on_finished(self, proc: Gio.Subprocess, result: Gio.AsyncResult) -> None:
        if self.finished:
            return
        self.finished = True
        if self.timeout_source:
            GLib.source_remove(self.timeout_source)
            self.timeout_source = 0
        self.pgid = 0
        try:
            _, _, stderr = proc.communicate_utf8_finish(result)
            success = proc.get_successful() and not self.timed_out
            status = proc.get_exit_status() if proc.get_if_exited() else None
            detail = " ".join((stderr or "").split())[:180]
            message = "Applied" if success else ("Timed out" if self.timed_out else detail or f"Exit status {status}")
        except GLib.Error as error:
            success = False
            message = "Timed out" if self.timed_out else str(error.message)[:180]
        _active.discard(self)
        self.callback(ActionResult(success, message))


_active: set[ActionHandle] = set()


def run_action(
    action: dict,
    title: str,
    on_result: Callable[[ActionResult], None],
    *,
    timeout: int = 90,
) -> ActionHandle | None:
    """Apply a finite command; launch-only commands report only spawn status."""
    command = action.get("command")
    argv = action.get("argv")
    if isinstance(argv, list) and argv and all(isinstance(arg, str) for arg in argv):
        args = [str(Path(arg).expanduser()) if arg.startswith("~/") else arg.replace("$HOME/", str(Path.home()) + "/", 1) if arg.startswith("$HOME/") else arg for arg in argv]
        if action.get("requires_root"):
            args = ["pkexec", *args]
    elif isinstance(command, str) and command.strip():
        normalized = utility._normalize_command(command)
        args = utility._build_command_list(normalized, title, False, bool(action.get("requires_root")))
        if args is None:
            GLib.idle_add(lambda: (on_result(ActionResult(False, "Invalid command")), GLib.SOURCE_REMOVE)[1])
            return None
    else:
        GLib.idle_add(lambda: (on_result(ActionResult(False, "Missing command")), GLib.SOURCE_REMOVE)[1])
        return None

    if action.get("mode") == "launch" or action.get("terminal"):
        if isinstance(command, str):
            spawned = utility.execute_command(command, title, bool(action.get("terminal")), bool(action.get("requires_root")))
        else:
            spawned = utility.execute_argv(args)
        GLib.idle_add(lambda: (on_result(ActionResult(spawned, "Launched" if spawned else "Could not launch")), GLib.SOURCE_REMOVE)[1])
        return None

    try:
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_PIPE)
        wrapper = shutil.which("dusky-run")
        scoped_args = [wrapper, *args] if wrapper and Path(args[0]).name != "dusky-run" else args
        proc = launcher.spawnv(["/usr/bin/setsid", "--", *scoped_args])
    except GLib.Error as error:
        log.error("Could not start action %s: %s", title, error.message)
        GLib.idle_add(lambda: (on_result(ActionResult(False, "Could not start command")), GLib.SOURCE_REMOVE)[1])
        return None
    action_timeout = action.get("timeout", timeout)
    return ActionHandle(proc, on_result, action_timeout)
