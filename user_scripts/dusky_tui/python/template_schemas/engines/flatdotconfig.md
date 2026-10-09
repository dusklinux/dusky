# Engine: `flatdotconfig`

- **Class:** `FlatDotConfigEngine` — `engines/flatdotconfig.py`
- **Engine types:** `flatdotconfig`
- **Default target:** `~/.config/gpu-screen-recorder/config_ui`

## Target format

No `[sections]` or braces — flat dot-notated keys, key and value separated by
the **first space** (values may contain spaces). Duplicate keys are allowed
(e.g. multiple audio tracks).

```
record.record_options.fps 60
record.container mp4
screenshot.image_quality very_high
audio.record_audio true
```

## Scope / key mapping

- All dot boundaries have cache aliases: `record.record_options.fps` supports
  both `scope="record"`, `key="record_options.fps"` and
  `scope="record.record_options"`, `key="fps"`.
- Keys without a dot → `scope="DEFAULT"`, `key=<raw>`.
- Duplicates are indexed with `:N` (`audio.track:2`); the base key binds to
  the first occurrence.

## Types & value handling

- `bool` is always written lowercase `true`/`false` (the C++ parser rejects
  Python's capital booleans).
- `"__DELETE__"` / `"nil"` removes the line.
- Color theme vars (`__VAR__`) are stripped on write.

## Quirks

- Preserves exact duplicate keys via index tags during writes. A missing `:N`
  can append only the next occurrence; gaps fail before writing. Base-key and
  `:1` edits refer to the same occurrence, with the last requested value winning.
- Atomic tmpfile + fsync commits; refuses to write if the file was modified
  externally (inode, size, nanosecond mtime and ctime snapshot).

## Example items

```python
ConfigItem(label="FPS", key="fps", scope="record.record_options", type_="int",
           default=60, min_val=1, max_val=240, step=1, group="Record"),
ConfigItem(label="Container", key="container", scope="record", type_="cycle",
           default="mp4", options=["mp4", "mkv"], group="Record"),
```
