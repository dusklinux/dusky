# Engine: `shell_fallback`

- **Class:** `ShellFallbackEngine` — `engines/shell_fallback.py`
- **Engine types:** `shell_fallback`
- **Default target:** any shell env file containing fallback definitions (set
  `TARGET_FILE`)

## Target format

Bash "fallback" definitions:

```bash
readonly KEY="${KEY:-DEFAULT_VAL}"
readonly ENABLE_DEBUG="${ENABLE_DEBUG:-false}"
```

## Scope / key mapping

- `scope` is always `"DEFAULT"`.
- `key` = the variable name.
- State key: `DEFAULT/<key>` → the fallback value.

## Types & value handling

- `bool`: if the current fallback is `true`/`false`, the new value is coerced
  to lowercase `true`/`false`.
- Other values are written inline into the `:-` fallback slot, preserving the
  leading whitespace and trailing comment.

## Quirks

- Atomic commits with permission/ownership preservation and nanosecond mtime
  TOCTOU guard.

## Example items

```python
ConfigItem(label="Debug", key="ENABLE_DEBUG", scope="DEFAULT", type_="bool",
           default=False, group="Runtime"),
ConfigItem(label="Cache Path", key="CACHE_PATH", scope="DEFAULT", type_="string",
           default="~/.cache/app", group="Runtime"),
```
