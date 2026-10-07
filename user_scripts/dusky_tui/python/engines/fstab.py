#!/usr/bin/env python3
"""Edit one fstab record while preserving other records and unexposed options."""

lazy import fcntl
import logging
import os
lazy import pwd
import re
lazy import stat
lazy import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
lazy from pathlib import Path
lazy from typing import Any

from python.frontend.core_types import BaseEngine

logger = logging.getLogger(__name__)
SUPPORTED_FS = {"btrfs", "vfat", "exfat", "ntfs", "ext4", "ext3", "ext2", "swap"}
BOOL_KEYS = {"btrfs_ops/cow_enabled", "system_flags/auto_mount", "system_flags/gvfs_show"}
TAG_DIRS = {"UUID": "by-uuid", "PARTUUID": "by-partuuid", "LABEL": "by-label", "PARTLABEL": "by-partlabel"}
NEW_ENTRY = "<new>"


@dataclass
class Record:
    index: int
    line: str
    fields: list[str]
    spans: list[tuple[int, int]]

    def render(self, fields: list[str]) -> str:
        # Keep existing separators and the exact trailing comment.
        result = self.line
        if len(self.fields) < 6:
            end = self.spans[-1][1]
            result = result[:end] + "\t" + "\t".join(fields[len(self.fields):]) + result[end:]
        for i in reversed(range(len(self.fields))):
            start, end = self.spans[i]
            result = result[:start] + fields[i] + result[end:]
        return result


class FstabEngine(BaseEngine):
    refresh_after_write = True
    atomic_batches = True

    def __init__(self, config_path: str = "/etc/fstab"):
        self.config_path = Path(config_path).expanduser().resolve()
        self.lock_path = self.config_path.parent / f".{self.config_path.name}.lock"
        self.state = self._default_state()
        self._baseline = self.state.copy()
        self._snapshot: bytes | None = None
        self._signature: tuple[int, ...] | None = None
        self._record: Record | None = None
        self._loaded = False

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "mount_info/uuid": "",
            "mount_info/entry": "",
            "mount_info/mount_point": "/",
            "filesystem/fs_type": "btrfs",
            "filesystem/drive_type": "hdd",
            "btrfs_ops/subvol": "",
            "btrfs_ops/cow_enabled": True,
            "system_flags/auto_mount": True,
            "system_flags/gvfs_show": True,
        }

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    @property
    def cache(self) -> dict[str, Any]:
        return self.state.copy()

    @staticmethod
    def _unescape_token(value: str) -> str:
        # libmount recognizes these escapes; other backslash sequences are literal.
        return re.sub(r"\\(040|011|012|134)", lambda m: chr(int(m[1], 8)), value)

    @staticmethod
    def _escape_token(value: str) -> str:
        return "".join(f"\\{ord(c):03o}" if c in "\\ \t\n" else c for c in value)

    @staticmethod
    def _normalize_identifier(spec: str) -> tuple[str, str]:
        # Decode fstab tokens at the parsing boundary, never user-entered strings.
        spec = spec.lstrip(" \t")
        tag, sep, value = spec.partition("=")
        if sep and tag.upper() in TAG_DIRS:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return tag.upper(), value  # FAT/NTFS UUID case must survive.
        if spec.startswith("/"):
            return "PATH", spec
        if re.fullmatch(r"[0-9A-Fa-f]+(?:-[0-9A-Fa-f]+)*", spec):
            return "UUID", spec
        return "RAW", spec

    def _device_spec(self, spec: str) -> str:
        if any(ord(c) < 32 and c != "\t" for c in spec):
            raise ValueError(f"Invalid control character in device identifier: {spec!r}")
        tag, value = self._normalize_identifier(spec)
        if tag == "RAW" or not value or any(c in value for c in "\x00\n\r"):
            raise ValueError(f"Invalid device identifier: {spec!r}")
        return self._escape_token(value if tag == "PATH" else f"{tag}={value}")

    def _resolve_to_devpath(self, tag: str, value: str) -> str | None:
        if tag == "PATH":
            path = Path(value)
        elif tag in TAG_DIRS:
            # udev escapes unsafe bytes in /dev/disk names, including spaces and '/'.
            parts = []
            for char in value:
                safe = (char.isalnum() or char in "#+-.:=@_") if char.isascii() else not 0xD800 <= ord(char) <= 0xDFFF
                parts.append(char if safe else "".join(f"\\x{b:02x}" for b in os.fsencode(char)))
            escaped = "".join(parts)
            path = Path("/dev/disk") / TAG_DIRS[tag] / escaped
        else:
            return None
        try:
            return str(path.resolve(strict=True))
        except (OSError, RuntimeError):
            return None

    def _match_device(self, first: str, second: str) -> bool:
        if not first or not second:
            return False
        t1, v1 = self._normalize_identifier(first)
        t2, v2 = self._normalize_identifier(second)
        if (t1, v1) == (t2, v2):
            return True
        resolved = self._resolve_to_devpath(t1, v1)
        return resolved is not None and resolved == self._resolve_to_devpath(t2, v2)

    @contextmanager
    def _locked(self, exclusive: bool):
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
        try:
            deadline = time.monotonic() + 2.0
            while True:
                try:
                    fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Timed out waiting for the fstab editor lock") from None
                    time.sleep(0.05)
            yield
        finally:
            os.close(fd)

    @staticmethod
    def _file_signature(st: os.stat_result) -> tuple[int, ...]:
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns

    def _read(self) -> tuple[bytes | None, os.stat_result | None]:
        try:
            with self.config_path.open("rb") as stream:
                before = os.fstat(stream.fileno())
                data = stream.read()
                after = os.fstat(stream.fileno())
            if self._file_signature(before) != self._file_signature(after):
                raise OSError("fstab changed while it was being read; reload and retry")
            return data, after
        except FileNotFoundError:
            return None, None

    @staticmethod
    def _lines(data: bytes | None) -> list[str]:
        # fstab records end at LF, not at Unicode whitespace within a filename/comment.
        parts = (data or b"").decode("utf-8", "surrogateescape").split("\n")
        return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])

    @staticmethod
    def _records(data: bytes | None) -> list[Record]:
        records = []
        for index, line in enumerate(FstabEngine._lines(data)):
            tokens = list(re.finditer(r"[^ \t\r\n]+", line))
            if not tokens or tokens[0][0].startswith("#"):
                continue
            # A comment starts after the four required fields.
            end = next((i for i, token in enumerate(tokens) if i >= 4 and token[0].startswith("#")), len(tokens))
            tokens = tokens[:end]
            if len(tokens) < 4:
                continue
            records.append(Record(index, line, [t[0] for t in tokens], [t.span() for t in tokens]))
        return records

    def _detect_drive_type(self, spec: str) -> str:
        resolved = self._resolve_to_devpath(*self._normalize_identifier(spec))
        if resolved is None:
            return "hdd"
        try:
            device = Path(resolved).stat()
            node = Path(f"/sys/dev/block/{os.major(device.st_rdev)}:{os.minor(device.st_rdev)}").resolve(strict=True)
            seen: set[Path] = set()

            def rotational(block: Path) -> bool:
                if block in seen:
                    return False
                seen.add(block)
                if (block / "partition").exists():
                    block = block.parent
                slaves = list((block / "slaves").iterdir()) if (block / "slaves").is_dir() else []
                if slaves:
                    return any(rotational(slave.resolve()) for slave in slaves)
                return (block / "queue/rotational").read_text().strip() != "0"

            return "hdd" if rotational(node) else "ssd"
        except (OSError, RuntimeError):
            return "hdd"

    def load_state(self, force: bool = False) -> dict[str, Any]:
        with self._locked(False):
            data, st = self._read()
        signature = self._file_signature(st) if st else None
        if not force and self._loaded and signature == self._signature and data == self._snapshot:
            return self.cache
        active = self.state["mount_info/uuid"]
        entry = self.state["mount_info/entry"]
        state = self._default_state()
        state.update({"mount_info/uuid": active, "mount_info/entry": entry})
        state["filesystem/drive_type"] = self._detect_drive_type(active) if active else "hdd"
        matches = [r for r in self._records(data) if self._match_device(self._unescape_token(r.fields[0]), active)
                   and (not entry or self._unescape_token(r.fields[1]) == entry)] if entry != NEW_ENTRY else []
        if entry and entry != NEW_ENTRY and len(matches) != 1:
            raise ValueError(f"Expected one entry for {active!r} at {entry!r}; found {len(matches)}")
        record = matches[0] if matches else None
        if record:
            if len(record.fields) > 6 or any(not v.isdecimal() for v in record.fields[4:]):
                raise ValueError("Selected fstab entry has invalid dump/pass fields")
            fields = record.fields
            options = fields[3].split(",")
            values = {o.partition("=")[0]: o.partition("=")[2] for o in options}
            cow = True
            for option in options:
                name, _, value = option.partition("=")
                if name == "nodatacow":
                    cow = False
                elif name in {"datacow", "datasum"} or (name in {"compress", "compress-force"} and value != "no"):
                    cow = True
            visible = False
            for option in options:
                if option in {"comment=x-gvfs-show", "x-gvfs-show"}:
                    visible = True
                elif option == "x-gvfs-hide":
                    visible = False
            state.update({
                "mount_info/entry": self._unescape_token(fields[1]),
                "mount_info/mount_point": self._unescape_token(fields[1]),
                "filesystem/fs_type": fields[2],
                "btrfs_ops/subvol": self._unescape_token(values.get("subvol", "")),
                "btrfs_ops/cow_enabled": cow,
                "system_flags/auto_mount": next((o == "auto" for o in reversed(options) if o in {"auto", "noauto"}), True),
                "system_flags/gvfs_show": visible,
            })
        self.state = state
        self._baseline = state.copy()
        self._record = record
        self._snapshot = data
        self._signature = signature
        self._loaded = True
        return self.cache

    @staticmethod
    def _first_normal_uid_gid() -> tuple[int, int]:
        for variable in ("SUDO_UID", "PKEXEC_UID"):
            if value := os.environ.get(variable):
                try:
                    user = pwd.getpwuid(int(value))
                    return user.pw_uid, user.pw_gid
                except (ValueError, KeyError):
                    pass
        if user_name := os.environ.get("SUDO_USER"):
            try:
                user = pwd.getpwnam(user_name)
                return user.pw_uid, user.pw_gid
            except KeyError:
                pass
        # The router reconstructs HOME for the invoking user, including su launches.
        home = os.environ.get("HOME")
        if home:
            for user in pwd.getpwall():
                if user.pw_dir == home:
                    return user.pw_uid, user.pw_gid
        return os.getuid(), os.getgid()

    @staticmethod
    def _coerce_bool(key: str, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        match str(value).strip().lower():
            case "true" | "1" | "yes" | "on" | "y" | "t" | "enabled":
                return True
            case "false" | "0" | "no" | "off" | "n" | "f" | "disabled":
                return False
            case _:
                raise ValueError(f"Invalid boolean for {key}: {value!r}")

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No pending changes.", ""
        saved = (self.state.copy(), self._baseline.copy(), self._snapshot, self._signature, self._record, self._loaded)
        try:
            values = {}
            for key, scope, value, _ in changes:
                full_key = f"{scope}/{key}"
                if full_key not in self.state:
                    raise ValueError(f"Unknown setting: {full_key}")
                values[full_key] = self._coerce_bool(full_key, value) if full_key in BOOL_KEYS else value
            selectors = {"mount_info/uuid", "mount_info/entry"}
            if "mount_info/uuid" in values:
                self._device_spec(values["mount_info/uuid"])
                self.state["mount_info/uuid"] = values["mount_info/uuid"]
                self.state["mount_info/entry"] = ""
            if "mount_info/entry" in values:
                self.state["mount_info/entry"] = values["mount_info/entry"]
            if selectors & values.keys():
                self.load_state(force=True)
            elif not self._loaded:
                self.load_state()
            self.state.update({k: v for k, v in values.items() if k not in selectors})
            if values.keys() <= selectors:
                return True, "Loaded selected fstab entry; no file changes.", ""
            ok, message, detail = self.commit_changes(values.keys() - selectors)
            if ok:
                return ok, message, detail
        except (OSError, ValueError) as exc:
            ok, message, detail = False, str(exc), ""
        self.state, self._baseline, self._snapshot, self._signature, self._record, self._loaded = saved
        return ok, message, detail

    def _options(self, fs: str, mp: str, changed: set[str]) -> list[str]:
        fresh = self._record is None or self.state["filesystem/fs_type"] != self._baseline["filesystem/fs_type"]
        if fresh:
            uid, gid = self._first_normal_uid_gid()
            match fs:
                case "btrfs":
                    options = ["defaults", "noatime"]
                case "ntfs":
                    options = ["defaults", "noatime", f"uid={uid}", f"gid={gid}", "umask=002", "windows_names", "iocharset=utf8"]
                case "exfat":
                    options = ["rw", "noatime", f"uid={uid}", f"gid={gid}", "dmask=0022", "fmask=0133", "iocharset=utf8", "errors=remount-ro"]
                case "vfat":
                    options = ["rw", "relatime", "fmask=0133", "dmask=0022", "shortname=mixed", "utf8", "errors=remount-ro"]
                case "swap":
                    options = ["defaults"]
                case _:
                    options = ["defaults", "noatime", "lazytime"]
            if self._record and fs != "swap":
                generic = {"ro", "nosuid", "nodev", "noexec", "user", "users", "owner", "group", "_netdev", "noauto", "nofail"}
                options.extend(o for o in self._record.fields[3].split(",") if o in generic or o.startswith(("x-", "X-", "comment=")))
        else:
            options = self._record.fields[3].split(",")

        def replace(names: set[str], additions: list[str]):
            options[:] = [o for o in options if o.partition("=")[0] not in names]
            options.extend(additions)

        if fs == "btrfs":
            if fresh or "filesystem/drive_type" in changed:
                drive_options = ["ssd", "discard=async"] if self.state["filesystem/drive_type"] == "ssd" else ["nodiscard"]
                if self.state["filesystem/drive_type"] == "hdd" and self.state["btrfs_ops/cow_enabled"]:
                    drive_options.append("autodefrag")
                replace({"ssd", "nossd", "ssd_spread", "discard", "nodiscard", "autodefrag", "noautodefrag"}, drive_options)
            if fresh or "btrfs_ops/cow_enabled" in changed:
                cow = self.state["btrfs_ops/cow_enabled"]
                replace({"nodatacow", "datacow", "nodatasum", "datasum", "compress", "compress-force", "nocompress"},
                        ["datacow", "datasum", "compress=zstd:3"] if cow else ["nodatacow"])
                if not cow:
                    replace({"autodefrag"}, [])
            if fresh or "btrfs_ops/subvol" in changed:
                subvol = self.state["btrfs_ops/subvol"]
                replace({"subvol", "subvolid"}, [f"subvol={self._escape_token(subvol)}"] if subvol else [])
        if fs == "ntfs":
            # This option belongs to a different driver; modern ntfs has its own default preallocation.
            replace({"prealloc"}, [])
        if fresh or changed & {"system_flags/auto_mount", "mount_info/mount_point"}:
            flags = [] if self.state["system_flags/auto_mount"] else ["noauto"]
            if mp not in {"/", "/usr", "/var", "/boot", "/efi", "/boot/efi"}:
                flags.append("nofail")
            replace({"auto", "noauto", "nofail"}, flags)
        if fs != "swap" and (fresh or "system_flags/gvfs_show" in changed):
            options = [o for o in options if o not in {"comment=x-gvfs-show", "x-gvfs-show", "x-gvfs-hide"}]
            options.append("x-gvfs-show" if self.state["system_flags/gvfs_show"] else "x-gvfs-hide")
        return options or ["defaults"]

    def commit_changes(self, requested: set[str] | None = None) -> tuple[bool, str, str]:
        if not self._loaded or not self.state["mount_info/uuid"]:
            return False, "Select a target device first.", ""
        try:
            spec = self._device_spec(self.state["mount_info/uuid"])
            fs = self.state["filesystem/fs_type"]
            mp = self.state["mount_info/mount_point"]
            subvol = self.state["btrfs_ops/subvol"]
            if fs not in SUPPORTED_FS:
                raise ValueError(f"Unsupported filesystem: {fs!r}; use ntfs for NTFS volumes")
            if self.state["filesystem/drive_type"] not in {"ssd", "hdd"}:
                raise ValueError("Drive architecture must be ssd or hdd")
            if fs == "swap":
                mp = "none"
            elif not mp.startswith("/") or any(ord(c) < 32 and c != "\t" for c in mp):
                raise ValueError(f"Invalid absolute mount point: {mp!r}")
            if fs == "btrfs" and ("," in subvol or any(ord(c) < 32 and c != "\t" for c in subvol)):
                raise ValueError("Btrfs subvolume cannot contain commas or control characters")
            changed = {k for k, v in self.state.items() if v != self._baseline[k]} | (requested or set())
            options = ",".join(self._options(fs, mp, changed))
            fields = [spec, self._escape_token(mp), fs, options, "0", "0"]
            if self._record:
                fields[0] = self._record.fields[0]  # Selection does not rename the source.
                fields[4:] = (self._record.fields[4:] + ["0", "0"])[:2]
            if self._record is None or fs != self._baseline["filesystem/fs_type"]:
                fields[5] = ("1" if mp == "/" else "2") if fs in {"ext2", "ext3", "ext4"} else "0"
            elif "mount_info/mount_point" in changed and fs in {"ext2", "ext3", "ext4"} and fields[5] != "0":
                fields[5] = "1" if mp == "/" else "2"
            with self._locked(True):
                data, st = self._read()
                signature = self._file_signature(st) if st else None
                if data != self._snapshot or signature != self._signature:
                    raise OSError("fstab changed since loading; reload before saving")
                records = self._records(data)
                for row in records:
                    if self._record and row.index == self._record.index:
                        continue
                    if fs != "swap" and self._unescape_token(row.fields[1]) == mp:
                        raise ValueError(f"Another fstab entry already uses mount point {mp!r}")
                lines = self._lines(data)
                if self._record:
                    lines[self._record.index] = self._record.render(fields)
                else:
                    if lines and not lines[-1].endswith("\n"):
                        lines[-1] += "\n"
                    lines.append("\t".join(fields) + "\n")
                output = "".join(lines).encode("utf-8", "surrogateescape")
                if output != (data or b""):
                    warning = self._atomic_write(output, st)
                else:
                    warning = ""
                # Update from our own output while the lock still owns this transaction.
                self._snapshot = output
                try:
                    self._signature = self._file_signature(self.config_path.stat())
                except OSError as exc:
                    self._signature = None
                    warning += f" Saved, but metadata refresh failed: {exc}; reload before another edit."
                new_index = self._record.index if self._record else len(lines) - 1
                self._record = next(r for r in self._records(self._snapshot) if r.index == new_index)
                self.state["mount_info/entry"] = mp
                self.state["mount_info/mount_point"] = mp
                self._baseline = self.state.copy()
            return True, "Saved selected fstab entry." + warning + " Run systemctl daemon-reload to refresh generated mount units.", ""
        except (OSError, ValueError) as exc:
            return False, str(exc), ""

    def _atomic_write(self, data: bytes, original: os.stat_result | None) -> str:
        fd, name = tempfile.mkstemp(dir=self.config_path.parent, prefix=f".{self.config_path.name}.")
        temp_path = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                if original:
                    current = os.fstat(stream.fileno())
                    if (current.st_uid, current.st_gid) != (original.st_uid, original.st_gid):
                        os.fchown(stream.fileno(), original.st_uid, original.st_gid)
                    os.fchmod(stream.fileno(), stat.S_IMODE(original.st_mode))
                else:
                    os.fchmod(stream.fileno(), 0o644)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # Recheck immediately before replacing: external editors may ignore our lock.
            current, st = self._read()
            if current != self._snapshot or (self._file_signature(st) if st else None) != self._signature:
                raise OSError("fstab changed while saving; reload before retrying")
            os.replace(temp_path, self.config_path)
            try:
                directory = os.open(self.config_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError as exc:
                # Replacement has already succeeded: don't roll the cache back or report no write.
                logger.warning("fstab saved but directory sync failed: %s", exc)
                return f" Directory durability could not be confirmed: {exc}."
            return ""
        finally:
            temp_path.unlink(missing_ok=True)
