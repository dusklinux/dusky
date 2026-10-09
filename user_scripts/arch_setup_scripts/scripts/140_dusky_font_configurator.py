#!/usr/bin/env python3
#d: Install prerequisites, deploy and verify the Dusky font configuration
"""Deploy schema defaults through the same engine used by the font TUI.

Run as the desktop user; missing required packages are installed with pacman.
--font-family (or DUSKY_DEFAULT_SANS) overrides the sans-serif default. Missing fonts,
cache errors, toolkit sync errors, and incorrect aliases exit nonzero.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

USER_SCRIPTS = Path(os.environ.get("USER_SCRIPTS", str(Path(__file__).resolve().parents[2]))).expanduser().resolve()
DUSKY_TUI_ROOT = USER_SCRIPTS / "dusky_tui"
SCHEMA_PATH = USER_SCRIPTS / "fonts/tui_fonts.py"


# Official Arch packages required by the default font deployment. Install before
# schema discovery or D-Bus startup so a fresh system can bootstrap both.
FONT_PACKAGES = frozendict({
    "Atkinson Hyperlegible": "ttf-atkinson-hyperlegible",
    "JetBrainsMono Nerd Font Mono": "ttf-jetbrains-mono-nerd",
    "Noto Color Emoji": "noto-fonts-emoji",
    "Liberation Serif": "ttf-liberation",
})
REQUIRED_PACKAGES = (
    "fontconfig", "glib2", "dconf", "gsettings-desktop-schemas", "dbus",
    *FONT_PACKAGES.values(),
)


def ensure_packages() -> None:
    query = subprocess.run(
        ["pacman", "--query", "--quiet", "--", *REQUIRED_PACKAGES],
        capture_output=True, text=True, encoding="utf-8",
    )
    if query.returncode not in (0, 1):
        raise RuntimeError(f"Cannot query installed packages: {query.stderr.strip()}")
    installed = set(query.stdout.splitlines())
    missing = [package for package in REQUIRED_PACKAGES if package not in installed]
    if not missing:
        if query.returncode:
            raise RuntimeError(f"Cannot query installed packages: {query.stderr.strip()}")
        return
    print(f"[INSTALL] Required packages: {', '.join(missing)}", flush=True)
    command = ["pacman", "--sync", "--needed", "--noconfirm", "--", *missing]
    if os.geteuid() != 0:
        # The orchestrator supplies a PTY in a new session without a
        # controlling terminal. Read authentication from its input stream.
        command = ["sudo", "--stdin", "--", *command]
    # Use the installer's existing repository databases and cached packages;
    # no isolated database refresh or unrelated system upgrade here.
    subprocess.run(command, check=True)
    subprocess.run(["pacman", "--query", "--quiet", "--", *REQUIRED_PACKAGES],
                   check=True, stdout=subprocess.DEVNULL)


def ensure_font_cache() -> None:
    # An installed package can still be invisible through a stale ISO cache.
    # Repair before schema discovery and the engine's pre-write validation;
    # the engine's post-write rebuild happens too late for missing families.
    proc = subprocess.run(
        ["fc-list", "--format=%{[]family{%{family}\n}}", ":"],
        check=True, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if proc.stderr.strip():
        raise RuntimeError(proc.stderr.strip())
    installed = {family.strip().casefold() for family in proc.stdout.splitlines()}
    missing = [family for family in FONT_PACKAGES if family.casefold() not in installed]
    if missing:
        print(f"[CACHE] Rebuilding for missing families: {', '.join(missing)}", flush=True)
        subprocess.run(["fc-cache", "--force"], check=True, timeout=120)


def _load_schema():
    sys.path.insert(0, str(DUSKY_TUI_ROOT))
    spec = importlib.util.spec_from_file_location("_dusky_font_schema", SCHEMA_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load schema: {SCHEMA_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def resolve_match(family: str) -> set[str]:
    proc = subprocess.run(
        ["fc-match", "--format=%{[]family{%{family}\n}}", family],
        check=True, capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    if proc.stderr.strip():
        raise RuntimeError(proc.stderr.strip())
    return {value.strip().casefold() for value in proc.stdout.splitlines() if value.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font-family", default=os.environ.get("DUSKY_DEFAULT_SANS"),
                        help="Override the schema's sans-serif default")
    args = parser.parse_args()
    try:
        ensure_packages()
        # dconf writes require a session bus, including during a TTY install.
        if (not os.environ.get("DBUS_SESSION_BUS_ADDRESS")
                and os.environ.get("GSETTINGS_BACKEND", "dconf") == "dconf"):
            os.execvp("dbus-run-session", ["dbus-run-session", "--", sys.executable,
                       str(Path(__file__).resolve()), *sys.argv[1:]])
        ensure_font_cache()
        mod = _load_schema()
        from python.engines.fontconfig import FontconfigEngine

        changes = [(item.key, item.scope,
                    args.font_family if item.key == "sans-serif" and args.font_family else item.default,
                    item.type_)
                   for items in mod.SCHEMA.values() for item in items
                   if item.type_ not in ("action", "preset")]
        defaults = {key: value for key, _scope, value, _type in changes}
        engine = FontconfigEngine(mod.TARGET_FILE)
        ok, message, error = engine.write_batch(changes, force_cache=True)
        if not ok:
            raise RuntimeError(error or message)
        print(message)
        with engine._file_lock():
            checks = {key: str(defaults[key]) for key in ("sans-serif", "serif", "monospace", "emoji")}
            checks.update({name: str(defaults[generic]) for name, generic in engine.FAMILY_REWRITES.items()})
            failures = []
            for family, expected in checks.items():
                resolved = resolve_match(family)
                ok = expected.casefold() in resolved
                print(f"[{'OK' if ok else 'FAIL'}] {family} -> {', '.join(sorted(resolved))}")
                if not ok:
                    failures.append(family)
            if failures:
                raise RuntimeError(f"Incorrect font resolution: {', '.join(failures)}")
        print("[SUCCESS] Font configuration, toolkit sync, cache, and aliases verified.")
        return 0
    except (ImportError, OSError, SyntaxError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
