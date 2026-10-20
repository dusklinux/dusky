#!/usr/bin/env python3
"""
Dusky Sites Setup Script (Arch Linux / Python 3.15+ / Firefox 158+)
======================================================================
Provisions XDG configuration directories, installs native messaging host
to ~/.local/share/dusky-sites/dusky_sites_host.py, parses profiles.ini,
registers native messaging host manifests, and configures userChrome CSS.
"""

import sys
import os
import re
import json
import shutil
import configparser
import fcntl
import subprocess
import tempfile
import argparse
import zipfile
from pathlib import Path

# Terminal Styling
C_CYAN = "\033[0;36m"
C_GREEN = "\033[0;32m"
C_BLUE = "\033[0;34m"
C_YELLOW = "\033[1;33m"
C_RED = "\033[0;31m"
C_RESET = "\033[0m"

HOST_INSTALL_NAME = "dusky_sites_host.py"
MANIFEST_NAME = "dusky_sites.json"
EXTENSION_ID = "dusky_sites@dusky.com"
INTERNAL_DOCUMENT_RULE = '@-moz-document regexp("about:(?!blank(?:[?#]|$)|srcdoc(?:[?#]|$)).*"), url("chrome://global/content/print.html"), url("chrome://global/content/commonDialog.xhtml"), url("chrome://browser/content/places/places.xhtml"), url-prefix("chrome://devtools/content/")'

def print_step(msg: str) -> None: print(f"{C_BLUE}==>{C_RESET} {msg}")
def print_success(msg: str) -> None: print(f"{C_GREEN}✓{C_RESET} {msg}")
def print_warn(msg: str) -> None: print(f"{C_YELLOW}[!] {msg}{C_RESET}")
def print_error(msg: str) -> None: print(f"{C_RED}[!] Error:{C_RESET} {msg}"); sys.exit(1)

PREFS_TO_SET = [
    ("toolkit.legacyUserProfileCustomizations.stylesheets", "true"),
    ("extensions.autoDisableScopes", "0"),
    ("extensions.enabledScopes", "15"),
    # Firefox 158 otherwise skips newly copied XPIs after a profile's first run.
    ("extensions.startupScanScopes", "1"),  # AddonManager.SCOPE_PROFILE
]

def atomic_write_text(path: Path, text: str) -> None:
    """Publish a complete file with a unique, same-directory temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                     mode="w", encoding="utf-8", delete=False) as file:
        tmp = Path(file.name)
        try:
            file.write(text)
            file.close()
            tmp.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)


def atomic_copy_file(src: Path, dst: Path) -> None:
    """Publish a complete copy without sharing a temporary name with other runs."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dst.parent, prefix=f".{dst.name}.", delete=False) as file:
        tmp = Path(file.name)
    try:
        shutil.copy2(src, tmp)
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)


def _css_import_matches(content: str, import_line: str) -> list[re.Match[str]]:
    """Find our directives outside CSS strings and comments."""
    protected = list(re.finditer(
        r"""/\*.*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'""",
        content, re.DOTALL,
    ))
    return [match for match in re.finditer(
        r"^[ \t]*" + re.escape(import_line) + r"(?:[ \t]*\r?\n)?", content, re.MULTILINE,
    ) if not any(part.start() <= match.start() < part.end() for part in protected)]


def ensure_css_import(path: Path, import_line: str) -> None:
    """Keep our import before style rules; a late @import is ignored by Gecko."""
    original = path.read_text(encoding="utf-8") if path.is_file() else ""
    bom = "\ufeff" if original.startswith("\ufeff") else ""
    content = original.removeprefix("\ufeff")
    matches = _css_import_matches(content, import_line)
    for match in reversed(matches):
        content = content[:match.start()] + content[match.end():]
    charset = re.prefixmatch(r'^(?:\ufeff)?@charset\s+"[^"\n]+"\s*;', content)
    offset = charset.end() if charset else 0
    prefix = content[:offset]
    if charset:
        prefix += "\n"
    new_content = bom + prefix + import_line + "\n" + content[offset:].lstrip("\r\n")
    if new_content != original:
        atomic_write_text(path, new_content)


def ensure_symlink(link: Path, target: Path) -> None:
    """Atomically publish an absolute palette link; report failures to the caller."""
    target = target.expanduser().absolute()
    if link.is_symlink() and link.readlink() == target:
        return
    if link.is_dir() and not link.is_symlink():
        raise IsADirectoryError(f"Cannot replace palette link directory: {link}")
    with tempfile.NamedTemporaryFile(dir=link.parent, prefix=f".{link.name}.", delete=False) as file:
        tmp = Path(file.name)
    try:
        tmp.unlink()
        tmp.symlink_to(target)
        tmp.replace(link)
    finally:
        tmp.unlink(missing_ok=True)


def remove_css_import(path: Path, import_line: str) -> None:
    """Remove import_line from path if present."""
    if not path.is_file():
        return
    original = path.read_text(encoding="utf-8")
    bom = "\ufeff" if original.startswith("\ufeff") else ""
    content = original.removeprefix("\ufeff")
    matches = _css_import_matches(content, import_line)
    if not matches:
        return
    for match in reversed(matches):
        content = content[:match.start()] + content[match.end():]
    atomic_write_text(path, bom + content)


PREF_BLOCK_START = "// BEGIN DUSKY SITES MANAGED PREFS"
PREF_BLOCK_END = "// END DUSKY SITES MANAGED PREFS"
_PREF_BLOCK_RE = re.compile(
    rf"^{re.escape(PREF_BLOCK_START)}\n.*?^{re.escape(PREF_BLOCK_END)}(?:\n|$)",
    re.MULTILINE | re.DOTALL,
)


def ensure_firefox_prefs(user_js: Path) -> bool:
    """Append owned overrides; preserve original preferences for uninstall."""
    try:
        original = user_js.read_text(encoding="utf-8") if user_js.is_file() else ""
        content = _PREF_BLOCK_RE.sub("", original)
        if content and not content.endswith("\n"):
            content += "\n"
        prefs = "\n".join(f'user_pref("{name}", {value});' for name, value in PREFS_TO_SET)
        content += f"{PREF_BLOCK_START}\n{prefs}\n{PREF_BLOCK_END}\n"
        if content != original:
            atomic_write_text(user_js, content)
        return True
    except (OSError, UnicodeError) as error:
        print_warn(f"Could not write {user_js}: {error}")
        return False


def iter_firefox_profiles(base_dir: Path):
    """Read only Profile sections; honor relative and external profile paths."""
    ini = base_dir / "profiles.ini"
    if ini.is_file():
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(ini.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, configparser.Error) as error:
            print_warn(f"Could not read {ini}: {error}")
            return
        candidates = []
        for section in parser.sections():
            if not section.startswith("Profile") or not parser.has_option(section, "Path"):
                continue
            raw_path = parser[section]["Path"].strip()
            if not raw_path:
                print_warn(f"Ignoring empty profile path in {ini}: {section}")
                continue
            profile = Path(raw_path)
            if parser[section].get("IsRelative", "1") != "0":
                profile = base_dir / profile
            elif not profile.is_absolute():
                print_warn(f"Ignoring non-absolute profile path in {ini}: {profile}")
                continue
            candidates.append(profile)
    else:
        try:
            candidates = [p for p in base_dir.iterdir() if (p / "prefs.js").is_file()]
        except OSError:
            return
    seen = set()
    for profile in candidates:
        if profile.is_dir():
            profile = profile.resolve()
            if profile not in seen:
                seen.add(profile)
                yield profile


MENU_CSS_CONTENT = """/* Dusky Sites — Firefox 158+ browser chrome.
 * LWT owns toolbar/tab/sidebar/urlbar colors. Override design tokens and
 * native menu defaults only; retain Firefox layout, icons and popup parts.
 * Sources: LightweightThemeConsumer, ThemeVariableMap, tokens-shared.css,
 * popup.css, menu.css, findbar.css, global-shared.css.
 */
@-moz-document url("chrome://browser/content/browser.xhtml") {
  :root[lwtheme] {
    /* Resolve these on the root before popups override the public tokens. */
    --dusky-popup-background: var(--panel-background-color);
    --dusky-popup-text: var(--panel-text-color);
    --dusky-popup-border: var(--panel-border-color);
    --text-color: var(--toolbar-text-color) !important;
    --text-color-deemphasized: color-mix(in srgb, var(--toolbar-text-color) 75%, transparent) !important;
    --text-color-disabled: color-mix(in srgb, var(--toolbar-text-color) 40%, transparent) !important;
    --background-color-canvas: var(--toolbar-background-color) !important;
    --background-color-box: var(--toolbar-field-background-color) !important;
    --border-color: var(--chrome-content-separator-color) !important;
    --color-accent-primary: var(--toolbarbutton-icon-fill-attention) !important;
    --focus-outline-color: var(--toolbar-field-border-color-focus) !important;
    --button-background-color: var(--toolbar-field-background-color) !important;
    --button-background-color-hover: var(--toolbarbutton-background-color-hover) !important;
    --button-background-color-active: var(--toolbarbutton-background-color-active) !important;
    --button-background-color-ghost-hover: var(--toolbarbutton-background-color-hover) !important;
    --button-background-color-ghost-active: var(--toolbarbutton-background-color-active) !important;
    --button-text-color: var(--toolbar-text-color) !important;
    --button-background-color-primary: var(--toolbarbutton-icon-fill-attention) !important;
    --button-text-color-primary: var(--lwt-accent-color) !important;
    --input-text-background-color: var(--toolbar-field-background-color) !important;
    --input-text-color: var(--toolbar-field-text-color) !important;
    --input-border-color: var(--toolbar-field-border-color) !important;
    --border-color-interactive-active: var(--toolbar-field-border-color-focus) !important;
    scrollbar-color: var(--toolbarbutton-background-color-hover) var(--toolbar-background-color);
  }

  :root[lwtheme] :is(menupopup, panel):not(.autoscroller) {
    --panel-background-color: var(--dusky-popup-background) !important;
    --panel-text-color: var(--dusky-popup-text) !important;
    --panel-border-color: var(--dusky-popup-border) !important;
    --panel-separator-color: var(--dusky-popup-border) !important;
    --text-color: var(--dusky-popup-text) !important;
    --text-color-disabled: color-mix(in srgb, var(--dusky-popup-text) 40%, transparent) !important;
    --menuitem-border-radius: 6px !important;
    --panel-menuitem-border-radius: 6px !important;
    --menuitem-padding-block: 6px !important;
    --menuitem-padding-inline: 12px !important;
  }

  :root[lwtheme] menupopup :is(menu, menuitem):not([disabled]) {
    color: var(--panel-text-color) !important;
  }
  :root[lwtheme] menupopup :is(menu, menuitem)[_moz-menuactive]:not([disabled]) {
    background-color: var(--urlbarview-background-color-selected) !important;
    color: var(--urlbarview-text-color-selected) !important;
  }
  :root[lwtheme] menuitem > .menu-icon {
    accent-color: var(--toolbarbutton-icon-fill-attention);
  }
  :root[lwtheme] menubar > menu[open] {
    border-bottom-color: var(--color-accent-primary) !important;
  }

  :root[lwtheme] findbar {
    background-color: var(--toolbar-background-color) !important;
    color: var(--toolbar-text-color) !important;
    border-top-color: var(--chrome-content-separator-color) !important;
  }
  :root[lwtheme] .findbar-textbox:not(:focus, [status="notfound"], [flash="true"]) {
    background-color: var(--toolbar-field-background-color) !important;
    color: var(--toolbar-field-text-color) !important;
    border-color: var(--toolbar-field-border-color) !important;
  }

  /* moz-button has its own shadow-root tokens; root overrides do not win there. */
  :root[lwtheme] .searchmode-switcher {
    --button-background-color-muted: var(--toolbar-field-background-color) !important;
    --button-background-color-muted-hover: var(--toolbarbutton-background-color-hover) !important;
    --button-background-color-muted-active: var(--toolbarbutton-background-color-active) !important;
    --button-background-color-muted-selected: var(--toolbarbutton-background-color-active) !important;
    --button-text-color-muted: var(--toolbar-field-text-color) !important;
    --button-text-color-muted-hover: var(--toolbar-field-text-color) !important;
    --button-text-color-muted-active: var(--toolbar-field-text-color) !important;
    --button-text-color-muted-selected: var(--toolbar-field-text-color) !important;
    --focus-outline-color: var(--toolbar-field-border-color-focus) !important;
  }

  /* Host custom properties cross the message bar's shadow boundary.
   * Preserve warning/error/success icons and their status backgrounds. */
  :root[lwtheme] :is(moz-message-bar, notification-message) {
    --message-bar-text-color: var(--toolbar-text-color) !important;
    --message-bar-border-color: var(--chrome-content-separator-color) !important;
  }
  :root[lwtheme] :is(moz-message-bar, notification-message):not([type="warning"], [type="error"], [type="critical"], [type="success"]) {
    --message-bar-background-color: var(--toolbar-field-background-color) !important;
    --message-bar-icon-color: var(--toolbarbutton-icon-fill-attention) !important;
    --message-bar-icon-background-color: transparent !important;
  }

  :root[lwtheme] .pointerlockfswarning,
  :root[lwtheme] tooltip {
    background-color: var(--dusky-popup-background) !important;
    color: var(--dusky-popup-text) !important;
    border: 1px solid var(--dusky-popup-border) !important;
    border-radius: 6px !important;
  }

  /* Native SVGs hardcode black. Mask an overlay to color them without
   * losing the direction-specific shape or Firefox's circular geometry. */
  :root[lwtheme] .autoscroller {
    --panel-background-color: var(--dusky-popup-background) !important;
    --panel-border-color: var(--dusky-popup-border) !important;
    position: relative !important;
  }
  :root[lwtheme] .autoscroller::after {
    content: "" !important;
    position: absolute !important;
    inset: 0 !important;
    background-color: var(--dusky-popup-text) !important;
    mask: var(--autoscroll-background-image) center / auto no-repeat !important;
  }
}
"""

def about_css_content(template: str) -> str:
    """The imported palette is URL-scoped by its Matugen source template."""
    return (
        "/* Live Matugen palette via dusky_palette.css symlink.\n"
        " * Profile stylesheets load at browser start; restart after palette updates. */\n"
        '@import url("dusky_palette.css");\n\n'
        + template
    )


def load_profile_styles(home: Path) -> tuple[Path, str, bool, bool]:
    """Validate shared inputs before provisioning any profile or installed files."""
    config_file = home / ".config/dusky/settings/dusky_sites/config.json"
    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        config = {}
    if not isinstance(config, dict):
        raise ValueError(f"Configuration must be a JSON object: {config_file}")
    colors_path = config.get("colorsPath", str(home / ".config/matugen/generated/dusky_sites.css"))
    if not isinstance(colors_path, str) or not colors_path.strip():
        raise ValueError("colorsPath must be a nonempty string")
    palette_path = Path(os.path.expanduser(colors_path))
    if not palette_path.is_absolute():
        raise ValueError("colorsPath must be absolute or begin with ~")
    chrome_enabled = config.get("userChromeEnabled", True)
    content_enabled = config.get("userContentEnabled", True)
    if not isinstance(chrome_enabled, bool) or not isinstance(content_enabled, bool):
        raise ValueError("userChromeEnabled and userContentEnabled must be JSON booleans")

    template_about = home / ".config/dusky_sites/about.css"
    base_about = template_about.read_text(encoding="utf-8") if chrome_enabled or content_enabled else ""
    if (chrome_enabled or content_enabled) and palette_path.is_file():
        palette = re.sub(r"/\*.*?\*/", "", palette_path.read_text(encoding="utf-8"), flags=re.DOTALL)
        if (not palette.lstrip().startswith(INTERNAL_DOCUMENT_RULE)
                or not {"--dusky-palette-background", "--dusky-palette-primary", "--dusky-palette-on_surface", "--dusky-palette-color-scheme"}
                <= set(re.findall(r"(--[\w-]+)\s*:", palette))):
            raise ValueError("Regenerate the Matugen palette with the updated document-scoped template before running setup")
    return palette_path, base_about, chrome_enabled, content_enabled


def setup_user_chrome(
    home: Path, source_xpi: Path | None = None,
    *, styles: tuple[Path, str, bool, bool] | None = None,
) -> bool:
    browser_dirs = _profile_base_dirs(home)
    try:
        palette_path, base_about, chrome_enabled, content_enabled = styles or load_profile_styles(home)
    except (OSError, ValueError) as error:
        print_warn(f"Cannot provision profile stylesheets: {error}")
        return False

    installed_profiles = 0
    failed_prefs = 0
    failed_profiles = 0
    failed_xpis = 0
    installed_xpis = 0
    seen_profiles: set[Path] = set()

    for base_dir in browser_dirs:
        if not base_dir.is_dir():
            continue
        for profile in iter_firefox_profiles(base_dir):
            if profile in seen_profiles:
                continue
            seen_profiles.add(profile)
            if not ensure_firefox_prefs(profile / "user.js"):
                failed_prefs += 1

            if source_xpi and source_xpi.is_file():
                ext_dir = profile / "extensions"
                try:
                    ext_dir.mkdir(parents=True, exist_ok=True)
                    target_xpi = ext_dir / f"{EXTENSION_ID}.xpi"
                    atomic_copy_file(source_xpi, target_xpi)
                    installed_xpis += 1
                except OSError as e:
                    failed_xpis += 1
                    print_warn(f"Could not copy XPI into {profile}: {e}")

            chrome_dir = profile / "chrome"
            try:
                chrome_dir.mkdir(parents=True, exist_ok=True)
                # Browser chrome (menus, toolbars, panels) -> userChrome.css
                menu_css = chrome_dir / "dusky_menu.css"
                atomic_write_text(menu_css, MENU_CSS_CONTENT)
                chrome_import = ensure_css_import if chrome_enabled else remove_css_import
                chrome_import(chrome_dir / "userChrome.css", '@import url("dusky_menu.css");')

                # Internal documents use both chrome and content docshells.
                # Import the same URL-scoped stylesheet through both entry points;
                # the main browser continues to receive live theme API colors.
                about_css = chrome_dir / "dusky_about.css"
                matugen_gen_css = palette_path
                ensure_symlink(chrome_dir / "dusky_palette.css", matugen_gen_css)

                about_content = about_css_content(base_about)
                atomic_write_text(about_css, about_content)

                chrome_import(chrome_dir / "userChrome.css", '@import url("dusky_about.css");')
                content_import = ensure_css_import if content_enabled else remove_css_import
                content_import(chrome_dir / "userContent.css", '@import url("dusky_about.css");')

                installed_profiles += 1
            except (OSError, UnicodeError) as e:
                failed_profiles += 1
                print_warn(f"Could not write chrome CSS in {profile}: {e}")

    if installed_profiles > 0:
        print_success(f"Context menu styling injected into {installed_profiles} profile(s).")
    else:
        print_warn("No profile directories found for context menu styling.")
    if installed_xpis > 0:
        print_success(f"Signed XPI copied into {installed_xpis} browser profile(s).")
    if failed_prefs:
        print_warn(f"user.js pref write failed for {failed_prefs} profile(s); userChrome may be inert until fixed.")

    return bool(installed_profiles) and not (failed_prefs or failed_profiles or failed_xpis)

def resolve_source_host(script_dir: Path) -> Path:
    candidates = [
        Path.home() / ".config" / "firefox_extentions" / "dusky_sites" / "dusky_sites_host.py",
        script_dir / HOST_INSTALL_NAME,
    ]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]

def resolve_source_xpi(script_dir: Path) -> Path | None:
    """Find a signed package matching shipped source; Firefox validates signing."""
    source_dir = Path.home() / ".config/firefox_extentions/dusky_sites/extension"
    if not source_dir.is_dir():
        source_dir = script_dir / "extension"
    expected_manifest = None
    expected_scripts = {}
    if source_dir.is_dir():
        expected_manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
        expected_scripts = {name: (source_dir / name).read_bytes()
                            for name in ("background.js", "content.js", "defaults.js")}
    stale_packages = []

    def _xpi_has_expected_id(xpi: Path) -> bool:
        try:
            with zipfile.ZipFile(xpi) as zf:
                if not {"META-INF/mozilla.rsa", "META-INF/mozilla.sf"} <= set(zf.namelist()):
                    return False
                with zf.open("manifest.json") as fh:
                    data = json.load(fh)
                root = data.get("browser_specific_settings") if isinstance(data, dict) else None
                gecko = root.get("gecko") if isinstance(root, dict) else None
                if not isinstance(gecko, dict) or gecko.get("id") != EXTENSION_ID:
                    return False
                if expected_manifest is not None:
                    mismatched = [name for name, content in expected_scripts.items()
                                  if name not in zf.namelist() or zf.read(name) != content]
                    if data != expected_manifest:
                        mismatched.append("manifest.json")
                    if mismatched:
                        stale_packages.append(f"{xpi}: {', '.join(mismatched)}")
                        return False
        except (OSError, zipfile.BadZipFile, json.JSONDecodeError, KeyError):
            return False
        return True

    candidates = [
        Path.home() / ".config" / "firefox_extentions" / "dusky_sites" / "xpi" / f"{EXTENSION_ID}.xpi",
        Path.home() / ".config" / "firefox_extentions" / "dusky_sites" / f"{EXTENSION_ID}.xpi",
        script_dir / "xpi" / f"{EXTENSION_ID}.xpi",
        script_dir / f"{EXTENSION_ID}.xpi",
    ]
    seen = set()
    for c in candidates:
        seen.add(c)
        if c.is_file() and _xpi_has_expected_id(c):
            return c

    search_dirs = [
        Path.home() / ".config" / "firefox_extentions" / "dusky_sites" / "xpi",
        Path.home() / ".config" / "firefox_extentions" / "dusky_sites",
        script_dir / "xpi",
        script_dir,
    ]
    for d in search_dirs:
        if d.is_dir():
            for xpi in sorted(d.glob("*.xpi")):
                if xpi in seen:
                    continue
                seen.add(xpi)
                if xpi.is_file() and _xpi_has_expected_id(xpi):
                    return xpi
    if stale_packages:
        raise ValueError("Signed XPI does not match the shipped extension source. "
                         "Update the signed package before running setup.\n  "
                         + "\n  ".join(stale_packages))
    return None

# ─────────────────────────────────────────────────────────────
# Uninstall
# ─────────────────────────────────────────────────────────────
def _browser_data_dirs(home: Path) -> list[Path]:
    """Native Firefox data roots (traditional and current XDG layout)."""
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    if not config_home.is_absolute():
        config_home = home / ".config"
    return [home / ".mozilla", config_home / "mozilla"]


def _profile_base_dirs(home: Path) -> list[Path]:
    return [root / "firefox" for root in _browser_data_dirs(home)]

def ensure_firefox_profiles(home: Path, firefox: str) -> None:
    """Let Firefox select and register its default before installing profile files."""
    bases = _profile_base_dirs(home)
    if any(any(iter_firefox_profiles(base)) for base in bases if base.is_dir()):
        return
    if any((base / "profiles.ini").exists() for base in bases):
        print_error("Firefox has a profile registry but no usable profile directories. Repair the registry before running setup.")

    # Old global copies must not be discovered and disabled before user.js sets
    # extensions.autoDisableScopes=0 in the new profile.
    uninstall_global_xpis(home)
    print_step("Creating Firefox's default profile without opening a browser window...")
    # --CreateProfile does not assign the installation's dedicated default.
    # Screenshot mode uses normal profile selection and exits on about:blank.
    with tempfile.TemporaryDirectory(prefix="dusky-firefox-") as tmp:
        screenshot = Path(tmp) / "blank.png"
        try:
            result = subprocess.run(
                [firefox, "--headless", "--new-instance", "--screenshot", str(screenshot), "about:blank"],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            print_error(f"Could not initialize Firefox's default profile: {error}")
        if result.returncode or not screenshot.is_file():
            print_error(f"Firefox profile initialization failed: {result.stderr.strip() or result.stdout.strip()}")
    if not any(any(iter_firefox_profiles(base)) for base in bases if base.is_dir()):
        print_error("Firefox did not register a usable default profile.")
    print_success("Firefox's default profile is ready for provisioning.")


def _browser_processes_running() -> list[str]:
    """Return names of detected running Firefox browsers."""
    result = subprocess.run(
        ["pgrep", "--euid", str(os.geteuid()), "--exact", "firefox"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode not in (0, 1):
        raise OSError(f"pgrep failed: {result.stderr.strip()}")
    return ["firefox"] if result.returncode == 0 else []

def _remove_file(path: Path) -> bool:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return True
    return False


def _remove_tree(path: Path) -> bool:
    if path.is_symlink():
        path.unlink()
        return True
    if path.is_dir():
        shutil.rmtree(path)
        return True
    return False


def restore_profile_prefs(profile: Path) -> int:
    """Remove only our marked overrides; never guess ownership of older lines."""
    user_js = profile / "user.js"
    if not user_js.is_file():
        return 0
    content = user_js.read_text(encoding="utf-8")
    restored, removed = _PREF_BLOCK_RE.subn("", content)
    if removed:
        atomic_write_text(user_js, restored)
        print_success(f"Removed managed preference block from {user_js}")
    return removed


def restore_user_chrome(profile: Path) -> None:
    """Remove dusky_menu.css and its @import from userChrome.css."""
    chrome_dir = profile / "chrome"
    if not chrome_dir.is_dir():
        return
    menu_css = chrome_dir / "dusky_menu.css"
    if _remove_file(menu_css):
        print_success(f"Removed {menu_css}")
    remove_css_import(chrome_dir / "userChrome.css", '@import url("dusky_menu.css");')

def restore_user_content(profile: Path) -> None:
    """Remove internal-document CSS, palette link, and both entry-point imports."""
    chrome_dir = profile / "chrome"
    if not chrome_dir.is_dir():
        return
    about_css = chrome_dir / "dusky_about.css"
    if _remove_file(about_css):
        print_success(f"Removed {about_css}")
    palette_link = chrome_dir / "dusky_palette.css"
    if _remove_file(palette_link):
        print_success(f"Removed {palette_link}")
    remove_css_import(chrome_dir / "userChrome.css", '@import url("dusky_about.css");')
    remove_css_import(chrome_dir / "userContent.css", '@import url("dusky_about.css");')

def uninstall_manifests(home: Path) -> int:
    """Remove native messaging manifests that belong to this extension."""
    removed = 0
    for base_dir in _browser_data_dirs(home):
        nmh_dir = base_dir / "native-messaging-hosts"
        if not nmh_dir.is_dir():
            continue
        manifest_file = nmh_dir / MANIFEST_NAME
        if not manifest_file.is_file():
            continue
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (isinstance(data, dict) and data.get("name") == "dusky_sites"
                and isinstance(data.get("allowed_extensions"), list)
                and EXTENSION_ID in data["allowed_extensions"]):
            if _remove_file(manifest_file):
                print_success(f"Removed manifest {manifest_file}")
                removed += 1
    return removed

def uninstall_global_xpis(home: Path) -> int:
    """Remove the XPI from global extension paths."""
    global_ext_dirs = [root / "extensions" / "{ec8030f7-c20a-464f-9b0e-13a3a9e97384}"
                       for root in _browser_data_dirs(home)]
    removed = 0
    for g_dir in global_ext_dirs:
        xpi = g_dir / f"{EXTENSION_ID}.xpi"
        if _remove_file(xpi):
            print_success(f"Removed {xpi}")
            removed += 1
    return removed

def uninstall_profile_artifacts(home: Path) -> int:
    """Per profile: remove our XPI, storage data, chrome CSS, and prefs."""
    profiles = 0
    seen: set[Path] = set()
    for base_dir in _profile_base_dirs(home):
        if not base_dir.is_dir():
            continue
        for profile in iter_firefox_profiles(base_dir):
            if profile in seen:
                continue
            seen.add(profile)
            profiles += 1
            xpi = profile / "extensions" / f"{EXTENSION_ID}.xpi"
            if _remove_file(xpi):
                print_success(f"Removed {xpi}")
            data_dir = profile / "browser-extension-data" / EXTENSION_ID
            if _remove_tree(data_dir):
                print_success(f"Removed {data_dir}")
            restore_user_chrome(profile)
            restore_user_content(profile)
            restore_profile_prefs(profile)
    return profiles

def uninstall_host(data_home: Path) -> bool:
    installed_host = data_home / "dusky-sites" / HOST_INSTALL_NAME
    removed = _remove_file(installed_host)
    if removed:
        print_success(f"Removed host {installed_host}")
    # drop the container dir if now empty
    host_dir = data_home / "dusky-sites"
    try:
        if host_dir.is_dir() and not any(host_dir.iterdir()):
            host_dir.rmdir()
            print_success(f"Removed empty directory {host_dir}")
    except OSError as e:
        print_warn(f"Could not remove {host_dir}: {e}")
    return removed

def run_uninstall(home: Path) -> None:
    print(f"\n{C_CYAN}[-] Dusky Sites Uninstaller{C_RESET}\n")

    running = _browser_processes_running()
    if running:
        print_warn(f"Detected running browser(s): {', '.join(running)}")
        print_warn("It is strongly recommended to close them before continuing.")

    xdg_data_home_raw = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg_data_home_raw:
        data_home = Path(xdg_data_home_raw).expanduser()
        if not data_home.is_absolute():
            data_home = home / ".local" / "share"
    else:
        data_home = home / ".local" / "share"

    print_step("Removing native messaging manifests...")
    manifests = uninstall_manifests(home)
    print_success(f"{manifests} manifest(s) removed.") if manifests else print_warn("No manifests found to remove.")

    print_step("Removing global extension XPI copies...")
    global_xpis = uninstall_global_xpis(home)
    print_success(f"{global_xpis} global XPI(s) removed.") if global_xpis else print_warn("No global XPI copies found.")

    print_step("Removing per-profile extension, chrome CSS and prefs...")
    profiles = uninstall_profile_artifacts(home)
    if profiles:
        print_success(f"Cleaned {profiles} browser profile(s).")
    else:
        print_warn("No browser profiles found.")

    print_step("Removing native host...")
    if uninstall_host(data_home):
        print_success("Host removed.")
    else:
        print_warn("No host file found.")

    print_step("Purging user configuration...")
    purged = 0
    config_dir = home / ".config" / "dusky" / "settings" / "dusky_sites"
    if _remove_tree(config_dir):
        print_success(f"Removed {config_dir}")
        purged += 1
    gen_css = home / ".config" / "matugen" / "generated" / "dusky_sites.css"
    if _remove_file(gen_css):
        print_success(f"Removed {gen_css}")
        purged += 1
    if not purged:
        print_warn("No configuration files found to purge.")

    print(f"\n{C_GREEN}[+] Uninstall complete.{C_RESET}")
    print("------------------------------------------------------------------")
    print(f"Removed: {manifests} manifest(s), {global_xpis} global XPI(s), host, {profiles} profile(s) cleaned.")
    print("Purging: User configuration (config.json) and generated CSS removed.")
    print("Saved prefs.js values and unmarked legacy user.js preferences are retained.")
    print("Site templates under ~/.config/dusky_sites and dev files under ~/.config/firefox_extentions/dusky_sites were left intact.")
    print("------------------------------------------------------------------\n")

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uninstall", "--purge", action="store_true", help="Remove installed extension, host, manifests, stylesheets and settings")
    parser.add_argument("--yes", action="store_true", help="Skip the uninstall confirmation")
    parser.add_argument("--update-installed", action="store_true", help="Update an existing installation; skip if its native host is absent")
    args = parser.parse_args()
    if args.uninstall:
        auto_yes = args.yes
        label = "Dusky Sites (extension, host, manifests & user config.json)"
        if not auto_yes:
            resp = input(f"Are you sure you want to uninstall {label}? [y/N] ").strip().lower()
            if resp not in ("y", "yes"):
                print("Aborted.")
                return
        home = Path.home()
        run_uninstall(home)
        return

    home = Path.home()
    xdg_data_home_raw = os.environ.get("XDG_DATA_HOME", "").strip()
    data_home = Path(xdg_data_home_raw).expanduser() if xdg_data_home_raw else home / ".local" / "share"
    if not data_home.is_absolute():
        data_home = home / ".local" / "share"
    install_dir = data_home / "dusky-sites"
    installed_host = install_dir / HOST_INSTALL_NAME
    if args.update_installed and not installed_host.is_file():
        print("Dusky Sites is not installed; skipping automatic update.")
        return

    print(f"\n{C_CYAN}Dusky Sites Setup Script (Arch Linux / Python 3.15+){C_RESET}\n")

    script_dir = Path(__file__).parent.resolve()
    source_host = resolve_source_host(script_dir)
    try:
        source_xpi = resolve_source_xpi(script_dir)
    except (OSError, ValueError) as error:
        print_error(str(error))

    print_step("Performing pre-flight checks...")
    if sys.version_info < (3, 15):
        print_error("Python 3.15 or newer is required.")
    firefox = shutil.which("firefox")
    if firefox is None:
        print_error("Firefox was not found in PATH.")
    try:
        result = subprocess.run([firefox, "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        print_error(f"Could not query Firefox version: {error}")
    version = re.search(r"Firefox (\d+)", result.stdout)
    if result.returncode or version is None or int(version[1]) < 158:
        print_error("Firefox 158 or newer is required.")
    print_success(result.stdout.strip())
    if not source_host.is_file():
        print_error(f"Host script not found. Place {HOST_INSTALL_NAME} next to this setup script.\n  looked for: {source_host}")
    print_success(f"Found host source at {source_host}")

    if source_xpi and source_xpi.is_file():
        print_success(f"Found signed WebExtension package at {source_xpi}")
    else:
        print_error("Signed XPI package not found. Supply the signed extension package before running setup.")

    profile_styles = load_profile_styles(home)
    running = _browser_processes_running()
    if running:
        print_warn("Firefox is running; restart it after setup to load the updated extension and stylesheets.")
    ensure_firefox_profiles(home, firefox)
    install_dir.mkdir(parents=True, exist_ok=True)

    print_step("Installing host to stable XDG path...")
    try:
        if installed_host.is_symlink():
            installed_host.unlink()
        atomic_copy_file(source_host, installed_host)
        installed_host.chmod(0o700)
        print_success(f"Host installed at {installed_host}")
    except OSError as e:
        print_error(f"Failed to install host: {e}")

    print_step("Provisioning configuration directories...")
    config_dir = home / ".config" / "dusky" / "settings" / "dusky_sites"
    config_dir.mkdir(parents=True, exist_ok=True)

    config_file = config_dir / "config.json"
    with config_file.with_name(config_file.name + ".lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not config_file.is_file():
            config_data = {
                "colorsPath": "~/.config/matugen/generated/dusky_sites.css",
                "websitesDir": "~/.config/dusky_sites",
                "webThemeEnabled": False,
                "forceUnthemedWebsites": False,
                "disabledSites": [],
            }
            try:
                atomic_write_text(config_file, json.dumps(config_data, indent=2) + "\n")
                print_success(f"Created primary config file at {config_file}")
            except OSError as e:
                print_error(f"Failed to create config: {e}")
        else:
            print_success(f"Config file exists at {config_file}")

    dusky_sites_dir = home / ".config" / "dusky_sites"
    dusky_sites_dir.mkdir(parents=True, exist_ok=True)
    print_success(f"Ensured templates directory exists at {dusky_sites_dir}")

    matugen_gen_dir = home / ".config" / "matugen" / "generated"
    matugen_gen_dir.mkdir(parents=True, exist_ok=True)
    print_success(f"Ensured Matugen output directory exists at {matugen_gen_dir}")

    print_step("Detecting native Firefox data directories...")
    targets: list[tuple[str, Path]] = []
    candidates = [("Firefox", root) for root in _browser_data_dirs(home)]

    for name, path in candidates:
        nmh_dir = path / "native-messaging-hosts"
        # Firefox 158's XREUserNativeManifests remains ~/.mozilla even when
        # its profile registry is under ~/.config/mozilla/firefox.
        if path == home / ".mozilla" or path.is_dir() or nmh_dir.is_dir():
            targets.append((name, nmh_dir))

    print_step("Installing native messaging manifests...")
    manifest_payload = {
        "name": "dusky_sites",
        "description": "Dusky Sites Native Messaging Host",
        "path": str(installed_host),
        "type": "stdio",
        "allowed_extensions": [EXTENSION_ID],
    }
    manifest_text = json.dumps(manifest_payload, indent=2) + "\n"

    installed_count = 0
    for name, target_dir in targets:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            manifest_file = target_dir / MANIFEST_NAME
            atomic_write_text(manifest_file, manifest_text)
            print_success(f"Manifest installed for {name} → {manifest_file}")
            installed_count += 1
        except OSError as e:
            print_warn(f"Failed to install manifest in {target_dir}: {e}")

    if installed_count == 0:
        print_error("No native messaging manifests were installed.")

    print_step("Provisioning native context menu, userChrome & profile XPI extensions...")
    if installed_count != len(targets):
        print_error("Native messaging registration was incomplete.")
    if not setup_user_chrome(home, source_xpi, styles=profile_styles):
        print_error("Profile provisioning was incomplete; see the errors above.")

    if source_xpi and source_xpi.is_file():
        print_step("Installing signed WebExtension into global extension paths...")
        global_ext_dirs = [root / "extensions" / "{ec8030f7-c20a-464f-9b0e-13a3a9e97384}"
                           for root in _browser_data_dirs(home)]
        g_count = 0
        for g_dir in global_ext_dirs:
            try:
                g_dir.mkdir(parents=True, exist_ok=True)
                atomic_copy_file(source_xpi, g_dir / f"{EXTENSION_ID}.xpi")
                g_count += 1
            except OSError as e:
                print_warn(f"Could not copy XPI to {g_dir}: {e}")
        if g_count != len(global_ext_dirs):
            print_error("Global extension provisioning was incomplete; see the errors above.")
        print_success(f"Signed XPI copied into {g_count} global extension path(s).")

    print(f"\n{C_GREEN}[+] Setup finished. Restart Firefox to load profile stylesheets and discover copied extensions.{C_RESET}")
    print("------------------------------------------------------------------")
    print(f"{C_CYAN}Host path:{C_RESET} {installed_host}")
    print(f"{C_CYAN}Manifest name:{C_RESET} {MANIFEST_NAME} (native app name: dusky_sites)")
    if source_xpi:
        print(f"{C_CYAN}Signed XPI:{C_RESET} {source_xpi}")
    print("------------------------------------------------------------------\n")

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print_error(str(error))
