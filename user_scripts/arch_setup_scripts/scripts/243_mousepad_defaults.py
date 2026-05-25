#!/usr/bin/env python3
import subprocess
import sys
import logging
from typing import NamedTuple, Iterator

# Initialization of a rigorous diagnostic telemetry apparatus.
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(asctime)s - %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z"
)

class ConfigurationImperative(NamedTuple):
    """
    An immutable data structure enforcing strict ontological categorization 
    upon the configuration matrix prior to executable materialization.
    """
    schema: str
    key: str
    value_matrix: str

# The declarative manifest of mandated systemic configurations.
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
org.xfce.mousepad.preferences.view color-scheme 'none'
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
org.xfce.mousepad.state.application session ['1;;', '1;;+']
org.xfce.mousepad.state.search direction uint32 1
org.xfce.mousepad.state.search enable-regex false
org.xfce.mousepad.state.search highlight-all false
org.xfce.mousepad.state.search history-size uint32 20
org.xfce.mousepad.state.search incremental false
org.xfce.mousepad.state.search match-case false
org.xfce.mousepad.state.search match-whole-word false
org.xfce.mousepad.state.search replace-all false
org.xfce.mousepad.state.search replace-all-location uint32 1
org.xfce.mousepad.state.search replace-history @as []
org.xfce.mousepad.state.search search-history @as []
org.xfce.mousepad.state.search wrap-around true
org.xfce.mousepad.state.window fullscreen false
org.xfce.mousepad.state.window height uint32 480
org.xfce.mousepad.state.window left uint32 0
org.xfce.mousepad.state.window maximized true
org.xfce.mousepad.state.window top uint32 0
org.xfce.mousepad.state.window width uint32 640
"""

def _parse_manifest(manifest: str) -> Iterator[ConfigurationImperative]:
    """
    Subjects the raw declarative block to rigorous lexical stratification, 
    yielding structurally guaranteed ConfigurationImperative instances.
    """
    for line_index, line in enumerate(manifest.strip().splitlines(), start=1):
        normalized_line = line.strip()
        if not normalized_line:
            continue
        
        lexical_tokens = normalized_line.split(maxsplit=2)
        if len(lexical_tokens) != 3:
            logging.error(f"Epistemological rupture detected at line {line_index}: '{normalized_line}'. Insufficient token density.")
            raise ValueError(f"Malformed programmatic directive at line {line_index}")
            
        yield ConfigurationImperative(*lexical_tokens)

def instantiate_configuration_hegemony() -> None:
    """
    Executes the deterministic subjugation of the operational environment, 
    aggregating systemic recalcitrance into a unified ExceptionGroup for post-mortem synthesis.
    """
    try:
        imperatives = list(_parse_manifest(GSETTINGS_MANIFEST))
    except ValueError as parse_err:
        logging.critical(f"Manifest evaluation unilaterally aborted: {parse_err}")
        sys.exit(1)

    systemic_failures: list[subprocess.CalledProcessError] = []

    for imperative in imperatives:
        try:
            # We enforce capture_output=True to hermetically seal the subprocess's standard streams,
            # precluding vernacular pollution of the primary execution terminal.
            subprocess.run(
                ["gsettings", "set", imperative.schema, imperative.key, imperative.value_matrix],
                check=True,
                capture_output=True,
                text=True
            )
        except subprocess.CalledProcessError as execution_err:
            logging.error(
                f"Insubordination localized at {imperative.schema} {imperative.key}. "
                f"Diagnostic residue: {execution_err.stderr.strip()}"
            )
            systemic_failures.append(execution_err)

    if systemic_failures:
        # Utilizing Python 3.14's ExceptionGroup dialectic to encapsulate aggregate resistance.
        raise ExceptionGroup(
            "Systemic recalcitrance encountered; absolute hegemony remains unconsummated.", 
            systemic_failures
        )

    logging.info("Total systemic hegemony achieved. The configuration paradigm is irrevocably calcified.")

if __name__ == "__main__":
    try:
        instantiate_configuration_hegemony()
    except ExceptionGroup as eg:
        logging.critical(f"Operational mandate failed. The following localized insubordinations were cataloged:")
        for exc in eg.exceptions:
            logging.critical(f" -> {exc}")
        sys.exit(1)
    except Exception as e:
        logging.critical(f"Unanticipated ontological collapse: {e}")
        sys.exit(1)
