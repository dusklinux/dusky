"""Small terminal UI; redirected output stays plain and readable."""

import os
import shutil
import sys
import time


class UI:
    def __init__(self):
        self.terminal = sys.stderr.isatty() and os.environ.get("TERM") != "dumb"
        self.color = self.terminal and "NO_COLOR" not in os.environ
        self.last_update = 0.0
        self.active = False

    def paint(self, text: str, code: str = "36") -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def clear(self):
        if self.active:
            print("\r\033[2K", end="", file=sys.stderr, flush=True)
            self.active = False

    def message(self, text: str, code: str = "36"):
        self.clear()
        print(self.paint(text, code), file=sys.stderr, flush=True)

    def banner(self):
        self.message("╭─ DUSKY CONVERTER ─────────────────────╮", "1;36")
        self.message("│  GIF · Video · Premiere · Audio      │", "1;36")
        self.message("╰──────────────────────────────────────╯", "1;36")

    def progress(self, label: str, elapsed: float, duration: float | None, speed: str):
        if not self.terminal or time.monotonic() - self.last_update < 0.15:
            return
        self.last_update = time.monotonic()
        if duration:
            fraction = min(max(elapsed / duration, 0), 0.99)
            filled = int(fraction * 20)
            state = f"[{'━' * filled}{'─' * (20 - filled)}] {fraction:5.0%}"
        else:
            state = f"{max(elapsed, 0):.1f}s"
        width = shutil.get_terminal_size().columns
        line = f"  {label}  {state}  {speed}"
        print("\r\033[2K" + self.paint(line[:max(width - 1, 1)]),
              end="", file=sys.stderr, flush=True)
        self.active = True
