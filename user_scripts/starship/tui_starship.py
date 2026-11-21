#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: STARSHIP PROMPT CONFIGURATION SCHEMA & SCRIPTING CLI
===============================================================================
This file serves a dual purpose:
1. It is the visual layout schema consumed by the Dusky TUI (`main.py starship.tui_starship`).
2. It is a standalone executable scripting tool (`--apply`, `--apply-state`, `--save-custom`).
===============================================================================
"""

import sys
import os
from pathlib import Path

# --- RESOLVE PATH BEFORE IMPORTS FOR STANDALONE CLI ---
_DUSKY_TUI_CANDIDATES = [
    Path(__file__).resolve().parent.parent / "dusky_tui",
    Path("~/user_scripts/dusky_tui").expanduser().resolve(),
]
for _candidate in _DUSKY_TUI_CANDIDATES:
    if _candidate.is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "starship"
TARGET_FILE = "~/.config/starship.toml"
APP_TITLE = "Dusky Starship"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Presets"

TABS = ["Prompts", "Custom", "Presets"]

# =============================================================================
# DYNAMIC PROMPT DISCOVERY & TUI SCHEMA DEFINITION (IMPORTED BY DUSKY TUI)
# =============================================================================
if __name__ != "__main__":
    from python.frontend.core_types import ConfigItem

    PRESET_ROOT = Path(__file__).resolve().parent / "presets"
    if not PRESET_ROOT.is_dir():
        PRESET_ROOT = Path("~/user_scripts/starship/presets").expanduser().resolve()

    CUSTOM_ROOT = Path("~/.config/dusky/settings/starship/prompts").expanduser().resolve()

    BUNDLED_PRESETS = (
        sorted(p.stem for p in PRESET_ROOT.glob("*.toml")) if PRESET_ROOT.is_dir() else []
    )
    CUSTOM_PRESETS = (
        sorted(p.stem for p in CUSTOM_ROOT.glob("*.toml")) if CUSTOM_ROOT.is_dir() else []
    )

    SCHEMA = {
        0: [
            ConfigItem(
                label="Active Prompt",
                key="active_prompt",
                scope="DEFAULT",
                type_="string",
                default="preset:dusky",
                group="Prompts",
                extended_help=(
                    "**Active Prompt Tracker**\n\n"
                    "Shows the currently applied Starship prompt and strictly tracks "
                    "your selection chronologically. You can type a prompt name here "
                    "(e.g. `preset:dusky-minimal`, `custom:my-prompt`) to apply it directly."
                ),
            ),
            ConfigItem(
                label="Available Prompts",
                key="prompt_menu",
                scope="DEFAULT",
                type_="menu",
                default=None,
                is_parent=True,
                expanded=True,
                group="Prompts",
                extended_help=(
                    "**Starship Prompts**\n\n"
                    "Arrow down and hit Enter on any prompt to instantly apply and "
                    "preview it. The list acts as a strict radio-button selection."
                ),
            ),
        ],
        1: [
            ConfigItem(
                label="Custom Prompt Name",
                key="custom_prompt_name",
                scope="DEFAULT",
                type_="string",
                default="my-custom-prompt",
                group="Custom Prompts",
                extended_help=(
                    "**Custom Prompt Name**\n\n"
                    "Name used when saving the current prompt as a custom preset. "
                    "Custom prompts are stored in "
                    "`~/.config/dusky/settings/starship/prompts/` and survive updates."
                ),
            ),
            ConfigItem(
                label="Save Current Prompt as Custom",
                key="action_save_custom",
                scope="DEFAULT",
                type_="bool",
                default=False,
                options=["trigger"],
                group="Custom Prompts",
                extended_help=(
                    "**Save Current Prompt as Custom**\n\n"
                    "Copies the currently applied `starship.toml` into the custom "
                    "prompts directory using the name above, then switches to it. "
                    "The new prompt appears in the Prompts gallery on next launch."
                ),
            ),
            ConfigItem(
                label="Open Custom Prompts Folder",
                key="action_open_custom_dir",
                scope="DEFAULT",
                type_="action",
                default=(
                    "mkdir -p ~/.config/dusky/settings/starship/prompts && "
                    "xdg-open ~/.config/dusky/settings/starship/prompts"
                ),
                group="Custom Prompts",
                extended_help=(
                    "**Open Custom Prompts Folder**\n\n"
                    "Opens the custom prompts directory in your file manager so you "
                    "can edit existing prompts or delete unused ones."
                ),
            ),
        ],
    }

    # --- Inject dynamic prompt menu items contiguous to the parent folder ---
    dynamic_prompt_items = []

    prompt_entries: list[tuple[str, str]] = [("preset:dusky", "Dusky (Default)")]
    for name in BUNDLED_PRESETS:
        if name != "dusky":
            prompt_entries.append((f"preset:{name}", name))
    for name in CUSTOM_PRESETS:
        prompt_entries.append((f"custom:{name}", f"Custom: {name}"))

    for key, label in prompt_entries:
        dynamic_prompt_items.append(
            ConfigItem(
                label=label,
                key=f"__starship_prompt_{key.replace(':', '_')}",
                scope="DEFAULT",
                type_="preset",
                default=None,
                parent_ref="prompt_menu",
                group="Prompts",
                preset_payload={"active_prompt": key},
                extended_help=f"**Apply {label}**\n\nHit Enter to instantly apply this Starship prompt.",
            )
        )

    SCHEMA[0].extend(dynamic_prompt_items)

# =============================================================================
# STANDALONE CLI MODE
# =============================================================================
if __name__ == "__main__":
    is_apply_state = "--apply-state" in sys.argv[1:]

    try:
        import argparse
        import json

        parser = argparse.ArgumentParser(
            description="Dusky Starship Prompt Manager - Scripting CLI Tool",
            formatter_class=argparse.RawTextHelpFormatter,
            add_help=False,
        )

        parser.add_argument(
            "--apply",
            "--set",
            "-s",
            dest="apply",
            type=str,
            metavar="PROMPT",
            help="Apply a specific Starship prompt (e.g. 'dusky', 'dusky-minimal', 'custom:my-prompt')",
        )
        parser.add_argument(
            "--apply-state",
            dest="apply_state",
            action="store_true",
            help="Re-apply the prompt saved in the state file (used by the update sequence)",
        )
        parser.add_argument(
            "--save-custom",
            dest="save_custom",
            type=str,
            metavar="NAME",
            help="Save the current starship.toml as a custom prompt and apply it",
        )
        parser.add_argument(
            "-h",
            "--help",
            action="help",
            default=argparse.SUPPRESS,
            help="Show this help message and exit",
        )

        args = parser.parse_args()
        is_apply_state = bool(args.apply_state)

        # Behavior 1: If executed with no arguments, launch the TUI
        if not any(vars(args).values()):
            main_script = (
                Path("~/user_scripts/dusky_tui/python/main/main.py").expanduser().resolve()
            )
            for candidate in _DUSKY_TUI_CANDIDATES:
                probe = candidate / "python" / "main" / "main.py"
                if probe.exists():
                    main_script = probe
                    break

            if main_script.exists():
                os.execvp(sys.executable, [sys.executable, str(main_script), __file__])
            else:
                print("[-] Error: Could not locate dusky_tui main.py to launch TUI.")
                sys.exit(1)

        # Behavior 2: If executed with flags, act as a headless mutator script
        try:
            from python.engines.starship import StarshipEngine
        except ImportError:
            if is_apply_state:
                print(
                    "Warning: Could not import StarshipEngine. Ensure dusky_tui is installed correctly.",
                    file=sys.stderr,
                )
                sys.exit(0)
            print(
                "[-] Error: Could not import StarshipEngine. Ensure dusky_tui is installed correctly."
            )
            sys.exit(1)

        engine = StarshipEngine(TARGET_FILE)
        changes: list[tuple[str, str, str, str]] = []

        if args.apply:
            changes.append(("active_prompt", "DEFAULT", args.apply, "string"))
        elif args.apply_state:
            state_file = engine.state_file
            if not state_file.exists():
                print("[i] No starship state file found. Nothing to re-apply.")
                sys.exit(0)
            try:
                state_data = json.loads(state_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                print(
                    f"Warning: Failed to read starship state file: {error}",
                    file=sys.stderr,
                )
                sys.exit(0)
            if not isinstance(state_data, dict):
                print("Warning: Invalid starship state file format.", file=sys.stderr)
                sys.exit(0)
            saved = state_data.get("active_prompt")
            if saved is None or (isinstance(saved, str) and not saved.strip()):
                print("[i] No prompt saved in state file. Nothing to re-apply.")
                sys.exit(0)
            if not isinstance(saved, str):
                print(
                    "Warning: Invalid active_prompt in starship state file.",
                    file=sys.stderr,
                )
                sys.exit(0)
            changes.append(("active_prompt", "DEFAULT", saved, "string"))
        elif args.save_custom:
            changes.append(("custom_prompt_name", "DEFAULT", args.save_custom, "string"))
            changes.append(("action_save_custom", "DEFAULT", "true", "bool"))

        if changes:
            success, msg, _ = engine.write_batch(changes)
            if success:
                print(f"[OK] {msg}")
                sys.exit(0)
            elif is_apply_state:
                print(f"Warning: {msg}", file=sys.stderr)
                sys.exit(0)
            else:
                print(f"[-] Failed: {msg}")
                sys.exit(1)
    except KeyboardInterrupt:
        if is_apply_state:
            print("Warning: Interrupted during --apply-state.", file=sys.stderr)
            sys.exit(0)
        sys.exit(130)
    except SystemExit as exc:
        if is_apply_state and exc.code not in (0, None):
            print(
                f"Warning: Starship state restore exited with status {exc.code}.",
                file=sys.stderr,
            )
            sys.exit(0)
        raise
    except BaseException as error:
        if is_apply_state:
            print(f"Warning: {error}", file=sys.stderr)
            sys.exit(0)
        raise
