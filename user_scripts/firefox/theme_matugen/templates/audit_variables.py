#!/usr/bin/env python3
"""Read-only source and installation checks for native Firefox 158+.

Inspect the installed browser's shipped resources, not a cached web page.
This checks source contracts and files; it does not claim to test rendering.
"""
import argparse
import configparser
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import struct
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("dusky_setup", ROOT / "dusky_sites_setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class Checks:
    def __init__(self):
        self.total = 0
        self.failed = 0

    def check(self, valid: bool, description: str) -> None:
        self.total += 1
        self.failed += not valid
        print(f"{'PASS' if valid else 'FAIL'} {description}")


def open_archive(path: Path) -> zipfile.ZipFile:
    """Read ordinary ZIPs and Mozilla's optimized omni.ja layout.

    Optimized jars place the directory at offset 4 and duplicate the end record
    at EOF. Relocate a copy of that directory in memory so Python's standard ZIP
    reader can use the original local-file offsets. No browser files are changed.
    """
    try:
        return zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        data = path.read_bytes()
        if data[4:8] != b"PK\x01\x02":
            raise
        end_offset = data.find(b"PK\x05\x06")
        end = bytearray(data[end_offset:end_offset + 22])
        if len(end) != 22:
            raise zipfile.BadZipFile(f"Missing end record: {path}")
        size, offset = struct.unpack_from("<II", end, 12)
        if offset != 4 or offset + size != end_offset:
            raise zipfile.BadZipFile(f"Unrecognized optimized directory: {path}")
        struct.pack_into("<I", end, 16, len(data))
        return zipfile.ZipFile(io.BytesIO(data + data[offset:offset + size] + end))


def firefox_sources(directory: Path) -> dict[str, str]:
    sources = {}
    for path in (directory / "omni.ja", directory / "browser" / "omni.ja"):
        with open_archive(path) as archive:
            for name in archive.namelist():
                # Exclude third-party compatibility injections: those selectors
                # and variables describe websites, not Firefox chrome.
                if (name.endswith(".css") and name.startswith((
                    "chrome/toolkit/skin/", "chrome/toolkit/content/global/",
                    "chrome/browser/skin/", "chrome/browser/content/",
                    "chrome/browser/builtin-addons/newtab/",
                    "chrome/devtools/skin/", "chrome/devtools/content/"))
                    or name in {"modules/LightweightThemeConsumer.sys.mjs",
                                "modules/LightweightThemeManager.sys.mjs",
                                "modules/ThemeVariableMap.sys.mjs"}):
                    sources[name] = archive.read(name).decode("utf-8")
    return sources


def uncomment(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def template_roles(text: str, name: str) -> dict[str, str]:
    match = re.search(rf"\b{name}:\s*\{{([^}}]+)\}}", uncomment(text))
    return dict(re.findall(r"(\w+):\s*['\"]([\w-]+)['\"]", match[1])) if match else {}


def check_css(checks: Checks, label: str, css: str, sources: dict[str, str], palette: set[str]) -> None:
    css = uncomment(css)
    shipped = "\n".join(uncomment(text) for text in sources.values())
    consumed = set(re.findall(r"var\(\s*(--[\w-]+)", shipped))
    declared = set(re.findall(r"(--[\w-]+)\s*:", shipped))
    emitted = set(re.findall(r'"(--[\w-]+)"', shipped))
    own = set(re.findall(r"(--[\w-]+)\s*:", css))
    unknown_targets = sorted(name for name in own if not name.startswith("--dusky-") and name not in consumed)
    unresolved = sorted(set(re.findall(r"var\(\s*(--[\w-]+)", css)) - declared - emitted - own - palette)
    checks.check(not unknown_targets, f"{label}: overridden variables have Firefox consumers" + (f": {unknown_targets}" if unknown_targets else ""))
    checks.check(not unresolved, f"{label}: variable references resolve" + (f": {unresolved}" if unresolved else ""))


def audit(directory: Path, source_only: bool) -> int:
    checks = Checks()
    home = Path.home()
    ini = configparser.ConfigParser()
    ini.read(directory / "application.ini")
    version = ini.get("App", "Version", fallback="")
    checks.check(bool(re.prefixmatch(r"\d+", version)) and int(version.split(".")[0]) >= 158,
                 f"Firefox {version or 'unknown'} (158+ required)")
    print(f"Build: {ini.get('App', 'BuildID', fallback='unknown')}; source: {ini.get('App', 'SourceStamp', fallback='unknown')}")
    sources = firefox_sources(directory)
    checks.check(all(any(name.endswith(required) for name in sources) for required in (
        "LightweightThemeManager.sys.mjs", "LightweightThemeConsumer.sys.mjs", "ThemeVariableMap.sys.mjs", "tokens-shared.css", "popup.css", "menu.css", "tree.css", "organizer.css", "findbar.css", "commonDialog.css", "print.css")),
        f"Shipped Firefox source contracts loaded ({len(sources)} resources)")
    extension = home / ".config/firefox_extentions/dusky_sites"
    background = (extension / "extension/background.js").read_text()
    roles = template_roles(background, "paletteTemplate")
    elements = template_roles(background, "browserTemplate")
    palette_text = (home / ".config/matugen/generated/dusky_sites.css").read_text()
    palette_vars = set(re.findall(r"(--[\w-]+)\s*:", uncomment(palette_text)))
    checks.check(bool(roles) and bool(elements) and all("--dusky-palette-" + value.removeprefix("--") in palette_vars for value in roles.values()),
                 f"Palette roles resolve ({len(roles)} roles; {len(palette_vars)} palette variables)")
    checks.check(all(role in roles for role in elements.values()), "Browser theme roles resolve")
    manager = sources["modules/LightweightThemeManager.sys.mjs"]
    color_loader = manager.split("function loadColors(", 1)[1].split("function ", 1)[0]
    supported_keys = set(re.findall(r'case "(\w+)":', color_loader))
    unknown = sorted(set(elements) - supported_keys)
    checks.check(not unknown, f"Firefox accepts all {len(elements)} mapped theme keys" + (f": {unknown}" if unknown else ""))
    check_css(checks, "Browser chrome", setup.MENU_CSS_CONTENT, sources, palette_vars)
    about = (home / ".config/dusky_sites/about.css").read_text()
    check_css(checks, "Internal pages", about, sources, palette_vars)
    # One shared URL condition for both imports; blank/srcdoc frames stay untouched.
    scope = setup.INTERNAL_DOCUMENT_RULE
    matugen_template = (home / ".config/matugen/templates/dusky_sites.css").read_text()
    checks.check(scope in uncomment(matugen_template) and scope in uncomment(about), "Palette and internal UI rules share the exact about/chrome scope; exclude websites and blank/srcdoc")
    checks.check(bool(re.search(r"webThemeEnabled:\s*false\b", uncomment(background))), "Extension default: webpage theming off")
    host = (extension / "dusky_sites_host.py").read_text()
    checks.check("web_theme_enabled: bool = False" in host, "Native host default: webpage theming off")
    host_spec = importlib.util.spec_from_file_location("dusky_host_audit", extension / "dusky_sites_host.py")
    host_module = importlib.util.module_from_spec(host_spec)
    sys.modules[host_spec.name] = host_module
    host_spec.loader.exec_module(host_module)
    parsed = host_module.parse_colors(home / ".config/matugen/generated/dusky_sites.css")
    checks.check(all(value in parsed for value in roles.values())
                 and not any(name.startswith("--dusky-palette-") for name in parsed),
                 "Private CSS palette names normalize to the signed extension's existing message keys")
    manifest = json.loads((extension / "extension/manifest.json").read_text())
    checks.check(manifest.get("browser_specific_settings", {}).get("gecko", {}).get("id") == setup.EXTENSION_ID
                 and {"theme", "nativeMessaging", "storage"} <= set(manifest.get("permissions", [])),
                 "Extension ID and required theme/host permissions")
    xpi = setup.resolve_source_xpi(ROOT)
    checks.check(xpi is not None, "Signed package matches shipped extension source (Firefox validates the signature)")
    if not source_only:
        checks.check(scope in uncomment(palette_text), "Generated palette has current document scope (regenerate if stale)")
        config = json.loads((home / ".config/dusky/settings/dusky_sites/config.json").read_text())
        checks.check(isinstance(config, dict) and isinstance(config.get("webThemeEnabled", False), bool), "Installed webpage setting is a boolean")
        print(f"NOTE Configured webpage opt-in: {config.get('webThemeEnabled', False)}")
        colors = Path(config.get("colorsPath", "~/.config/matugen/generated/dusky_sites.css")).expanduser()
        websites = Path(config.get("websitesDir", "~/.config/dusky_sites")).expanduser()
        checks.check(colors.is_file() and websites.is_dir(), "Configured palette and website directory exist")
        profiles = {profile for base in setup._profile_base_dirs(home) for profile in setup.iter_firefox_profiles(base)}
        checks.check(bool(profiles), "Native Firefox profiles found")
        for profile in sorted(profiles):
            installed_xpi = profile / "extensions" / f"{setup.EXTENSION_ID}.xpi"
            checks.check(xpi is not None and installed_xpi.is_file()
                         and installed_xpi.read_bytes() == xpi.read_bytes(),
                         f"{profile.name}: installed extension matches current signed package (restart Firefox to load)")
            chrome = profile / "chrome"
            menu = chrome / "dusky_menu.css"
            checks.check(menu.is_file() and menu.read_text() == setup.MENU_CSS_CONTENT, f"{profile.name}: chrome stylesheet matches setup source")
            for filename, import_line in (("userChrome.css", '@import url("dusky_menu.css");'),
                                          ("userChrome.css", '@import url("dusky_about.css");'),
                                          ("userContent.css", '@import url("dusky_about.css");')):
                path = chrome / filename
                raw_text = path.read_text(encoding="utf-8") if path.is_file() else ""
                text = uncomment(raw_text).removeprefix("\ufeff")
                # Imports must precede all qualified rules (not just occur somewhere).
                prefix = re.sub(r'^\s*@charset\s+"[^"\n]+"\s*;', '', text)
                leading = re.prefixmatch(r'(?:\s*@import\s+[^;]+;)*', prefix)[0]
                enabled = config.get("userChromeEnabled" if filename == "userChrome.css" else "userContentEnabled", True)
                valid = import_line in leading if enabled else not setup._css_import_matches(raw_text.removeprefix("\ufeff"), import_line)
                checks.check(valid, f"{profile.name}: {filename} import matches configured {'enabled' if enabled else 'disabled'} state")
            user_js = profile / "user.js"
            text = uncomment(user_js.read_text()) if user_js.is_file() else ""
            matches = re.findall(r'^\s*user_pref\("toolkit\.legacyUserProfileCustomizations\.stylesheets",\s*(true|false)\);', text, re.MULTILINE)
            checks.check(bool(matches) and matches[-1] == "true", f"{profile.name}: stylesheet preference enabled")
            about_path = chrome / "dusky_about.css"
            expected = setup.about_css_content(about if config.get("userChromeEnabled", True) or config.get("userContentEnabled", True) else "")
            checks.check(about_path.is_file() and about_path.read_text() == expected, f"{profile.name}: internal-page stylesheet matches template")
            link = chrome / "dusky_palette.css"
            checks.check(link.is_symlink() and link.resolve() == colors.resolve() and link.is_file(), f"{profile.name}: live palette link resolves to configured colorsPath")
        manifests = [root / "native-messaging-hosts" / setup.MANIFEST_NAME for root in setup._browser_data_dirs(home)]
        checks.check(manifests[0].is_file(), "Firefox native-manifest lookup registration exists under ~/.mozilla")
        found = False
        for path in manifests:
            if not path.is_file():
                continue
            found = True
            data = json.loads(path.read_text())
            executable = Path(data.get("path", ""))
            checks.check(data.get("name") == "dusky_sites" and data.get("type") == "stdio"
                         and setup.EXTENSION_ID in data.get("allowed_extensions", [])
                         and executable.is_absolute() and executable.is_file() and os.access(executable, os.R_OK | os.X_OK),
                         f"{path}: native host registration and executable")
            checks.check(executable.is_file() and executable.read_bytes() == (extension / "dusky_sites_host.py").read_bytes(), "Installed native host matches source")
        checks.check(found, "Native host manifest found")
        cache = home / ".config/dusky/settings/dusky_sites/live_theme_cache.json"
        if cache.is_file():
            snapshot = json.loads(cache.read_text())
            print(f"NOTE Cached theme timestamp: {snapshot.get('timestamp')}; colors: {len((snapshot.get('theme') or {}).get('colors') or {})}. This is not a live rendering test.")
    print(f"{checks.total - checks.failed}/{checks.total} checks passed. Source/file checks only; rendering requires Firefox runtime tests.")
    return int(bool(checks.failed))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firefox-dir", type=Path, default=Path("/usr/lib/firefox"), help="Firefox application directory containing omni.ja (Arch default: /usr/lib/firefox)")
    parser.add_argument("--source-only", action="store_true", help="Check development sources without requiring an up-to-date installation")
    args = parser.parse_args()
    try:
        return audit(args.firefox_dir, args.source_only)
    except (OSError, ValueError, TypeError, AttributeError, KeyError, IndexError, zipfile.BadZipFile, configparser.Error) as error:
        print(f"FAIL Audit could not complete: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
