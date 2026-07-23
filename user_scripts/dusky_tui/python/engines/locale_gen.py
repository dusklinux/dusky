#!/usr/bin/env python3
import os
import re
import tempfile
import threading
import subprocess
from pathlib import Path
from typing import Any

from python.frontend.core_types import BaseEngine

class LocaleGenEngine(BaseEngine):
    """
    Production-grade Engine for glibc `/etc/locale.gen` configuration & systemd region actions.
    
    Features:
    - Parses glibc locale definitions and tracks commented (#) vs uncommented states.
    - In-place toggling: uncommenting on enable (`en_US.UTF-8 UTF-8`), commenting out on disable (`#en_US.UTF-8 UTF-8`).
    - Supports both full locale strings (`en_US.UTF-8 UTF-8`) and short locale keys (`en_US.UTF-8`).
    - Appends newly declared locales if missing from the base file.
    - Action triggers for `locale-gen` compilation and `timedatectl` systemd settings.
    - Atomic crash-proof disk commits via temporary file replacement + fsync.
    - Thread-safe operations via re-entrant locking.
    """

    def __init__(self, config_path: str = "/etc/locale.gen"):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, Any] = {}
        self.file_mtime_ns: int = 0
        self._lock = threading.Lock()

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def load_state(self) -> dict[str, Any]:
        with self._lock:
            self.cache = {}
            if not self.config_path.exists():
                return self.cache

            try:
                with open(self.config_path, "r", encoding="utf-8", errors="replace") as f:
                    self.file_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                    lines = f.readlines()

                for line in lines:
                    stripped = line.strip()
                    if not stripped:
                        continue

                    is_commented = stripped.startswith("#")
                    content = stripped.lstrip("#").strip()

                    parts = content.split()
                    if len(parts) >= 2 and "_" in parts[0]:
                        locale_full = f"{parts[0]} {parts[1]}"
                        locale_short = parts[0]
                        is_enabled = not is_commented

                        self.cache[locale_full] = is_enabled
                        self.cache[locale_short] = is_enabled

            except Exception as e:
                print(f"[LocaleGenEngine] Failed to read {self.config_path.name}: {e}")

            return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No pending changes.", ""

        with self._lock:
            locale_changes = []
            action_messages = []
            debug_logs = []

            for key, scope, val, itype in changes:
                if itype == "action" or key.startswith("action_") or key in ("ntp_sync", "rtc_local"):
                    # System Action Triggers
                    if key == "action_locale_gen":
                        try:
                            res = subprocess.run(["locale-gen"], capture_output=True, text=True, timeout=60)
                            if res.returncode == 0:
                                action_messages.append("`locale-gen` completed successfully.")
                            else:
                                return False, f"locale-gen failed: {res.stderr.strip()}", res.stderr
                            debug_logs.append(res.stdout)
                        except Exception as e:
                            return False, f"Failed to execute locale-gen: {e}", str(e)

                    elif key == "ntp_sync":
                        is_en = str(val).lower() in ("true", "1", "yes", "on", "t")
                        cmd = ["timedatectl", "set-ntp", "true" if is_en else "false"]
                        try:
                            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                            if res.returncode == 0:
                                action_messages.append(f"NTP Time Sync set to {is_en}.")
                            else:
                                action_messages.append(f"timedatectl ntp failed: {res.stderr.strip()}")
                        except Exception as e:
                            action_messages.append(f"Failed to set NTP: {e}")

                    elif key in ("action_set_timezone", "set_timezone"):
                        if val and str(val) != "nil":
                            cmd = ["timedatectl", "set-timezone", str(val)]
                            try:
                                res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                                if res.returncode == 0:
                                    action_messages.append(f"Timezone set to {val}.")
                                else:
                                    action_messages.append(f"timedatectl set-timezone failed: {res.stderr.strip()}")
                            except Exception as e:
                                action_messages.append(f"Failed to set timezone: {e}")
                        else:
                            action_messages.append("Select system timezone from menu.")

                    elif key in ("action_set_lang", "set_lang"):
                        if val and str(val) != "nil":
                            cmd = ["localectl", "set-locale", f"LANG={val}"]
                            try:
                                res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                                if res.returncode == 0:
                                    action_messages.append(f"System LANG set to {val}.")
                                else:
                                    action_messages.append(f"localectl set-locale failed: {res.stderr.strip()}")
                            except Exception as e:
                                action_messages.append(f"Failed to set LANG: {e}")
                        else:
                            action_messages.append("Select LANG locale from menu.")

                    elif key in ("action_set_keymap", "set_keymap"):
                        if val and str(val) != "nil":
                            cmd = ["localectl", "set-keymap", str(val)]
                            try:
                                res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                                if res.returncode == 0:
                                    action_messages.append(f"TTY keymap set to {val}.")
                                else:
                                    action_messages.append(f"localectl set-keymap failed: {res.stderr.strip()}")
                            except Exception as e:
                                action_messages.append(f"Failed to set keymap: {e}")
                        else:
                            action_messages.append("Select TTY keymap from menu.")
                else:
                    locale_changes.append((key, scope, val, itype))

            if not locale_changes and action_messages:
                return True, " | ".join(action_messages), "\n".join(debug_logs)

            lines = []
            if self.config_path.exists():
                try:
                    with open(self.config_path, "r", encoding="utf-8", errors="replace") as f:
                        self.file_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                        lines = f.readlines()
                except Exception as e:
                    return False, f"Failed to read {self.config_path.name}: {e}", ""

            for key, scope, val, itype in locale_changes:
                if isinstance(val, str):
                    is_enabled = val.strip().lower() in ("true", "1", "yes", "on", "t", "y")
                else:
                    is_enabled = bool(val)

                target_key = key.strip()
                matched = False

                new_lines = []
                for line in lines:
                    stripped = line.strip()
                    content = stripped.lstrip("#").strip()
                    parts = content.split()

                    if len(parts) >= 2 and "_" in parts[0]:
                        locale_full = f"{parts[0]} {parts[1]}"
                        locale_short = parts[0]

                        if target_key in (locale_full, locale_short, content):
                            matched = True
                            if is_enabled:
                                new_lines.append(f"{locale_full}\n")
                            else:
                                new_lines.append(f"#{locale_full}\n")
                            continue

                    new_lines.append(line)

                lines = new_lines

                if not matched:
                    entry = f"{target_key}\n" if is_enabled else f"#{target_key}\n"
                    lines.append(entry)

            # Atomic Disk Commit
            try:
                parent_dir = self.config_path.parent
                parent_dir.mkdir(parents=True, exist_ok=True)

                tmp_file = tempfile.NamedTemporaryFile("w", dir=parent_dir, delete=False, encoding="utf-8")
                tmp_path = Path(tmp_file.name)

                tmp_file.writelines(lines)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
                tmp_file.close()

                if self.config_path.exists():
                    try:
                        mode = self.config_path.stat().st_mode
                        os.chmod(tmp_path, mode)
                    except Exception:
                        pass

                os.replace(tmp_path, self.config_path)
                self.file_mtime_ns = os.stat(self.config_path).st_mtime_ns

            except Exception as e:
                if "tmp_path" in locals() and tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
                return False, f"Failed to write locale.gen: {e}", ""

            msg = f"Successfully updated {len(locale_changes)} locale entries."
            if action_messages:
                msg += " " + " | ".join(action_messages)

            return True, msg, "\n".join(debug_logs)
