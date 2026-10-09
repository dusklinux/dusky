# Engine: `ini`

- **Class:** `IniConfigEngine` — `engines/ini.py`
- **Engine types:** `ini`
- **Default target:** `/etc/pacman.conf` (override with `TARGET_FILE`)

## Target format

Classic INI files: `[sections]`, `KEY = VALUE` (or `KEY=VALUE`), and valueless
flags (Arch style).

```ini
[options]
Color
ParallelDownloads = 5
HoldPkg = pacman glibc

[DEFAULT-style root key]
log_level = info
```

## Scope / key mapping

- `scope` = the `[section]` name (e.g. `"options"`).
- `scope="DEFAULT"` = keys at the root of the file (before any section).
- `key` = the INI key. Valueless flags are stored as `True` (bool items).
- Duplicates: on write, all but the first active occurrence of a key are
  commented out (singularity enforcement).
- Section-less writes auto-create `[scope]` headers at the end of the file.

## Types & value handling

- `bool` on a valueless flag: `true` → bare `Key` line; `false` → line is
  commented out.
- `bool` on a `KEY = value` line: writes `KEY = true|false`.
- `"__DELETE__"` / `"nil"` → the line is commented out (safe disable).
- Color theme variables arrive as `__VAR__...` and are stripped on write.
- Assignment-operator style is detected from the file (` = ` vs `=`), and new
  keys reuse the dominant style.

## Dynamic scope templates

Scope strings may contain `{placeholder}` references to other keys in the same
batch (e.g. scope `app-name="{custom_app_1_target}"`); placeholders resolve
from the batch values, then the file cache. Unresolved placeholders mark the
value as `__DELETE__`.

## Special scopes

Scopes starting with `__` are treated as internal UI-only state — they are
cached but **never written** to the file.

## Quirks

- After a changed commit, runs `makoctl reload` for `.../mako/config` or
  `systemctl reload systemd-logind.service` for logind configuration. Reloads
  have a timeout; failures are returned in debug output after the file is saved.
- Writes use a shared atomic commit (temporary file, file/directory sync, and
  `os.replace`). The sudo credential-cache path uses the same commit helper;
  missing authorization returns `AUTH_REQUIRED` for the existing TUI password flow.

## Example items

```python
ConfigItem(label="Color", key="Color", scope="options", type_="bool",
           default=True, group="Pacman",
           extended_help="**Color Output**\n\nValueless flag; inserts `Color` without `=`."),
ConfigItem(label="Parallel Downloads", key="ParallelDownloads", scope="options",
           type_="int", default=5, min_val=1, max_val=20, step=1, group="Pacman"),
ConfigItem(label="Logging", key="log_level", scope="DEFAULT", type_="cycle",
           default="info", options=["debug", "info", "warning", "error"], group="System"),
```
