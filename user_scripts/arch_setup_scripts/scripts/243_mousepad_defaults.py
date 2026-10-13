#!/usr/bin/env python3
#d: Apply recommended Mousepad text editor settings
"""Apply Mousepad defaults for the current user through GSettings."""

import logging
import subprocess
import sys

# Values use the canonical GVariant format printed by gsettings, so they can
# be compared directly with readback. Leave session/recovery data and search
# histories untouched; they are user data rather than installation defaults.
GSETTINGS_MANIFEST = """
org.xfce.mousepad.preferences.file add-last-end-of-line false
org.xfce.mousepad.preferences.file auto-reload false
org.xfce.mousepad.preferences.file autosave-timer uint32 30
org.xfce.mousepad.preferences.file default-encoding 'UTF-8'
org.xfce.mousepad.preferences.file make-backup false
org.xfce.mousepad.preferences.file monitor-changes true
org.xfce.mousepad.preferences.file monitor-disabling-timer uint32 500
org.xfce.mousepad.preferences.file session-restore 'after-a-crash'
org.xfce.mousepad.preferences.view auto-indent false
org.xfce.mousepad.preferences.view color-scheme 'matugen'
org.xfce.mousepad.preferences.view font-name 'Monospace 10'
org.xfce.mousepad.preferences.view highlight-current-line false
org.xfce.mousepad.preferences.view indent-on-tab true
org.xfce.mousepad.preferences.view indent-width -1
org.xfce.mousepad.preferences.view insert-spaces false
org.xfce.mousepad.preferences.view match-braces false
org.xfce.mousepad.preferences.view right-margin-position uint32 80
org.xfce.mousepad.preferences.view show-line-endings false
org.xfce.mousepad.preferences.view show-line-marks false
org.xfce.mousepad.preferences.view show-line-numbers false
org.xfce.mousepad.preferences.view show-right-margin false
org.xfce.mousepad.preferences.view show-whitespace false
org.xfce.mousepad.preferences.view smart-backspace false
org.xfce.mousepad.preferences.view smart-home-end 'disabled'
org.xfce.mousepad.preferences.view tab-width uint32 8
org.xfce.mousepad.preferences.view use-default-monospace-font true
org.xfce.mousepad.preferences.view word-wrap true
org.xfce.mousepad.preferences.view.show-whitespace inside true
org.xfce.mousepad.preferences.view.show-whitespace leading true
org.xfce.mousepad.preferences.view.show-whitespace trailing true
org.xfce.mousepad.preferences.window always-show-tabs false
org.xfce.mousepad.preferences.window app-name-in-title true
org.xfce.mousepad.preferences.window client-side-decorations false
org.xfce.mousepad.preferences.window cycle-tabs false
org.xfce.mousepad.preferences.window default-tab-sizes '2,3,4,8'
org.xfce.mousepad.preferences.window expand-tabs true
org.xfce.mousepad.preferences.window menubar-visible true
org.xfce.mousepad.preferences.window menubar-visible-in-fullscreen 'auto'
org.xfce.mousepad.preferences.window old-style-menu true
org.xfce.mousepad.preferences.window opening-mode 'tab'
org.xfce.mousepad.preferences.window path-in-title true
org.xfce.mousepad.preferences.window recent-menu-items uint32 10
org.xfce.mousepad.preferences.window remember-position false
org.xfce.mousepad.preferences.window remember-size true
org.xfce.mousepad.preferences.window remember-state true
org.xfce.mousepad.preferences.window statusbar-visible true
org.xfce.mousepad.preferences.window statusbar-visible-in-fullscreen 'auto'
org.xfce.mousepad.preferences.window toolbar-icon-size 'small-toolbar'
org.xfce.mousepad.preferences.window toolbar-style 'icons'
org.xfce.mousepad.preferences.window toolbar-visible true
org.xfce.mousepad.preferences.window toolbar-visible-in-fullscreen 'auto'
org.xfce.mousepad.state.application enabled-plugins @as []
org.xfce.mousepad.state.search direction uint32 1
org.xfce.mousepad.state.search enable-regex false
org.xfce.mousepad.state.search highlight-all false
org.xfce.mousepad.state.search history-size uint32 20
org.xfce.mousepad.state.search incremental false
org.xfce.mousepad.state.search match-case false
org.xfce.mousepad.state.search match-whole-word false
org.xfce.mousepad.state.search replace-all false
org.xfce.mousepad.state.search replace-all-location uint32 1
org.xfce.mousepad.state.search wrap-around true
org.xfce.mousepad.state.window fullscreen false
org.xfce.mousepad.state.window height uint32 480
org.xfce.mousepad.state.window left uint32 0
org.xfce.mousepad.state.window maximized true
org.xfce.mousepad.state.window top uint32 0
org.xfce.mousepad.state.window width uint32 640
"""


def parse_settings(text: str) -> dict[tuple[str, str], str]:
    """Parse schema/key/value lines, preserving spaces and GVariant quotes."""
    settings: dict[tuple[str, str], str] = {}
    # GLib prints Unicode line/paragraph separators literally inside strings.
    # Only LF separates records; splitlines() would split valid user values.
    for line_number, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        fields = line.split(maxsplit=2)
        if len(fields) != 3:
            raise ValueError(f"Invalid settings line {line_number}: {line!r}")
        schema, key, value = fields
        identity = (schema, key)
        if identity in settings:
            raise ValueError(f"Duplicate setting: {schema} {key}")
        settings[identity] = value.strip()
    return settings


def run_gsettings(*arguments: str) -> str:
    """Run a bounded command and reject backend warnings as well as errors."""
    result = subprocess.run(
        ["gsettings", *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    # dconf can report a failed commit on stderr while gsettings exits zero.
    if result.returncode or result.stderr.strip():
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise RuntimeError(f"gsettings {' '.join(arguments)}: {detail}")
    return result.stdout


def apply_defaults() -> None:
    """Check available keys, apply defaults, and verify them in a new process."""
    settings = parse_settings(GSETTINGS_MANIFEST)
    if not settings:
        raise ValueError("Mousepad settings manifest is empty.")
    installed = parse_settings(run_gsettings("list-recursively", "org.xfce.mousepad"))
    missing = settings.keys() - installed.keys()
    if missing:
        names = ", ".join(f"{schema} {key}" for schema, key in sorted(missing))
        raise RuntimeError(f"Mousepad settings are unavailable: {names}")

    # Write even values matching today's schema defaults: explicit user values
    # keep the requested behavior if upstream defaults change in a later update.
    for (schema, key), value in settings.items():
        run_gsettings("set", schema, key, value)

    saved = parse_settings(run_gsettings("list-recursively", "org.xfce.mousepad"))
    mismatches = [
        f"{schema} {key}: expected {value}, got {saved.get((schema, key), '<missing>')}"
        for (schema, key), value in settings.items()
        if saved.get((schema, key)) != value
    ]
    if mismatches:
        raise RuntimeError("Settings verification failed:\n" + "\n".join(mismatches))
    logging.info("Applied and verified %d Mousepad settings.", len(settings))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    try:
        apply_defaults()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        logging.error("Mousepad configuration failed: %s", error)
        return 1
    except KeyboardInterrupt:
        logging.error("Mousepad configuration interrupted.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
