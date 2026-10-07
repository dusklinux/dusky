"""TLP's flat configuration format; edits never apply hardware settings."""

import os
import re
lazy import stat
lazy import subprocess
lazy import tempfile
lazy from pathlib import Path

from python.frontend.core_types import BaseEngine


class TlpConfigEngine(BaseEngine):
    # Match the grammar accepted by tlp-readconfs, including append assignments.
    _assignment = re.compile(r'^([A-Z_]+[0-9]*)(\+=|=)"?([][0-9a-zA-Z _.:-]*)"?\s*$')
    _value = re.compile(r"[][0-9a-zA-Z _.:-]*")

    def __init__(self, config_path: str = "/etc/tlp.conf", *,
                 defaults_path: str = "/usr/share/tlp/defaults.conf",
                 dropin_path: str = "/etc/tlp.d") -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        self.defaults_path = Path(defaults_path)
        self.dropin_path = Path(dropin_path)
        self._fingerprint = None
        self._loaded = False
        self.renames = {}
        version = subprocess.check_output(["tlp", "--version"], text=True)
        release = re.search(r"\b(\d+)\.(\d+)\.", version)
        if release is None or tuple(map(int, release.groups())) < (1, 11):
            raise ValueError("This configurator requires TLP 1.11 or newer (PRF/BAL/SAV profile keys).")
        # Use the mappings shipped with TLP rather than duplicating its version rules.
        for line in Path("/usr/share/tlp/rename.conf").read_text().splitlines():
            fields = line.split("#", 1)[0].split()
            if len(fields) == 2:
                self.renames[fields[0]] = fields[1]

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def _snapshot(self):
        try:
            metadata = self.config_path.stat()
        except FileNotFoundError:
            return None
        return metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns

    @classmethod
    def _parse(cls, line: str):
        # TLP only recognizes double quotes. A # outside them starts a comment.
        clean = re.sub(r'("[^"]*")|#.*$', lambda m: m[1] or "", line.rstrip("\n"))
        return cls._assignment.fullmatch(clean.rstrip())

    def load_state(self) -> dict[str, str]:
        fingerprint = self._snapshot()
        state = {}
        if fingerprint is None:
            self._fingerprint = None
            self._loaded = True
            return state
        # Inherited values matter for +=, but only explicit user keys appear in the UI.
        values = {}
        default_values = {}
        skip_defaults = False
        sources = [self.defaults_path, *sorted(self.dropin_path.glob("*.conf")), self.config_path]
        for source in sources:
            for line in source.read_text().splitlines():
                if match := self._parse(line):
                    key, operator, value = match.groups()
                    key = self.renames.get(key, key)
                    if key == "TLP_DISABLE_DEFAULTS" and value == "1":
                        skip_defaults = True
                    # Match tlp-readconfs: its original default value determines
                    # whether += appends after TLP_DISABLE_DEFAULTS has been set.
                    has_default = default_values.get(key, "") not in ("", "0")
                    if operator == "+=" and source != self.defaults_path and key in values and not (skip_defaults and has_default):
                        value = f"{values[key]} {value}"
                    values[key] = value
                    if source == self.defaults_path:
                        default_values.setdefault(key, value)
                    if source == self.config_path:
                        state[f"DEFAULT/{key}"] = value
        if self._snapshot() != fingerprint:
            raise OSError("TLP configuration changed while being read. Reload before saving.")
        self._fingerprint = fingerprint
        self._loaded = True
        return state

    @classmethod
    def _validate(cls, key: str, value: str) -> None:
        if value in ("nil", "__DELETE__", ""):
            return
        if not cls._value.fullmatch(value):
            raise ValueError(f"{key}: use a single TLP value; quotes and newlines are not allowed")
        allowed = None
        if key in {"TLP_ENABLE", "TLP_DISABLE_DEFAULTS", "NMI_WATCHDOG", "USB_AUTOSUSPEND",
                   "RESTORE_THRESHOLDS_ON_BAT"} or key.startswith(("USB_EXCLUDE_", "CPU_BOOST_ON_", "CPU_HWP_DYN_BOOST_ON_")):
            allowed = {"0", "1"}
        elif key in {"WOL_DISABLE", "SOUND_POWER_SAVE_CONTROLLER"}:
            allowed = {"Y", "N"}
        elif key.startswith("TLP_PROFILE_"):
            allowed = {"PRF", "BAL", "SAV"}
        elif key == "TLP_AUTO_SWITCH":
            allowed = {"0", "1", "2"}
        elif key == "TLP_WARN_LEVEL":
            allowed = {"0", "1", "2", "3"}
        if allowed is not None and value not in allowed:
            raise ValueError(f"{key}: use one of {', '.join(sorted(allowed))}, nil, or an empty value")
        if key.startswith(("DEVICES_TO_ENABLE_", "DEVICES_TO_DISABLE_")):
            if set(value.split()) - {"bluetooth", "nfc", "wifi", "wwan"}:
                raise ValueError(f"{key}: use space-separated bluetooth, nfc, wifi, wwan")
        maximum = None
        numeric = False
        if key.startswith(("CPU_MIN_PERF_", "CPU_MAX_PERF_", "START_CHARGE_THRESH_", "STOP_CHARGE_THRESH_")):
            numeric, maximum = True, 100
        elif key.startswith(("CPU_SCALING_MIN_FREQ_", "CPU_SCALING_MAX_FREQ_", "INTEL_GPU_MIN_FREQ_",
                             "INTEL_GPU_MAX_FREQ_", "INTEL_GPU_BOOST_FREQ_", "MAX_LOST_WORK_SECS_",
                             "SOUND_POWER_SAVE_")) and key != "SOUND_POWER_SAVE_CONTROLLER":
            numeric = True
        elif key == "AHCI_RUNTIME_PM_TIMEOUT":
            numeric = True
        elif key.startswith(("DISK_APM_LEVEL_", "DISK_SPINDOWN_TIMEOUT_")):
            for token in value.split():
                if token != "keep" and (not token.isascii() or not token.isdecimal() or
                                        not 0 <= int(token) <= 255 or
                                        (key.startswith("DISK_APM_LEVEL_") and int(token) == 0)):
                    raise ValueError(f"{key}: use {'1' if 'APM' in key else '0'}..255 or keep per disk")
        if numeric and (not value.isascii() or not value.isdecimal() or
                        (maximum is not None and int(value) > maximum)):
            raise ValueError(f"{key}: use a nonnegative integer" + (f" up to {maximum}" if maximum is not None else ""))

    def write_value(self, target_key: str, target_scope: str, new_value: str,
                    item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No pending changes.", ""
        temporary = None
        try:
            if not self._loaded:
                self.load_state()
            if self._snapshot() != self._fingerprint:
                return False, "TLP configuration changed externally. Reload before saving.", ""
            updates = {}
            for key, scope, value, _ in changes:
                if scope != "DEFAULT" or not re.fullmatch(r"[A-Z_]+[0-9]*", key):
                    raise ValueError(f"Invalid TLP setting: {scope}/{key}")
                key = self.renames.get(key, key)
                self._validate(key, value)
                updates[key] = value
            lines = self.config_path.read_text().splitlines(keepends=True) if self._fingerprint else []
            last_occurrence = {}
            for index, line in enumerate(lines):
                if match := self._parse(line):
                    last_occurrence[self.renames.get(match[1], match[1])] = index
            output = []
            written = set()
            for index, line in enumerate(lines):
                match = self._parse(line)
                key = self.renames.get(match[1], match[1]) if match else None
                if key not in updates:
                    output.append(line)
                elif index == last_occurrence[key] and updates[key] not in ("nil", "__DELETE__"):
                    # Replace in place so ordinary repeated edits do not grow the file.
                    # Find the first unquoted comment, if present.
                    comments = [m[2] for m in re.finditer(r'("[^"]*")|(#.*$)', line) if m[2]]
                    suffix = f" {comments[0]}" if comments else ""
                    output.append(f'{key}="{updates[key]}"{suffix}\n')
                    written.add(key)
                else:
                    # Mute earlier aliases/duplicates/appends and all reset settings.
                    output.append(f"#{line}")
            missing = {key: value for key, value in updates.items()
                       if key not in written and value not in ("nil", "__DELETE__")}
            if missing and output and not output[-1].endswith("\n"):
                output[-1] += "\n"
            for key, value in missing.items():
                output.append(f'{key}="{value}"\n')
            content = "".join(output)
            if content == "".join(lines):
                return True, "No file changes needed.", ""
            metadata = self.config_path.stat() if self._fingerprint else None
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.config_path.parent,
                                             prefix=".tlp-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                if metadata:
                    os.fchown(handle.fileno(), metadata.st_uid, metadata.st_gid)
                os.fchmod(handle.fileno(), stat.S_IMODE(metadata.st_mode) if metadata else 0o644)
                os.fsync(handle.fileno())
            if self._snapshot() != self._fingerprint:
                return False, "TLP configuration changed externally. Reload before saving.", ""
            os.replace(temporary, self.config_path)
            directory_fd = os.open(self.config_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            self._fingerprint = self._snapshot()
            return True, "Saved TLP configuration. Use Apply Saved Settings to activate it.", ""
        except (OSError, ValueError) as error:
            return False, str(error), ""
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
