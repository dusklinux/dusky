lazy import ast
from contextlib import ExitStack, contextmanager
lazy import fcntl
lazy import xml.etree.ElementTree as ET
lazy from xml.dom import minidom
from pathlib import Path
lazy from typing import Any
lazy import subprocess
lazy import shutil
lazy import math
import os
import re
import sys
lazy import threading
lazy import tempfile
import time

# Make standalone execution (python3 python/engines/fontconfig.py) resolve
# the dusky_tui package layout regardless of CWD or username.
_DUSKY_TUI_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import BaseEngine

class FontconfigEngine(BaseEngine):
    """Serialize managed font choices and rendering settings to fontconfig XML.

    Strong generic aliases and first-family rewrites track the current choices.
    Color fonts retain embedded bitmaps when outline bitmaps are disabled.
    Other configuration elements and conditional rules are preserved.
    """
    ALIAS_CLASSES = ("sans-serif", "serif", "monospace", "emoji", "sans")
    RENDER_PROP_WHITELIST = {
        "antialias", "hinting", "autohint", "embeddedbitmap",
        "hintstyle", "rgba", "lcdfilter",
    }
    DIR_KEYS = {"font_dir", "font_dirs"}
    IGNORED_PATTERN_EDIT_NAMES = {"family", "familylang"}

    @staticmethod
    def _is_bitmap_rule(match: ET.Element, enabled: bool) -> bool:
        """Recognize only the engine's single-test, single-edit bitmap rules."""
        tests, edits = match.findall("test"), match.findall("edit")
        if match.get("target", "pattern") != "font" or len(tests) != 1 or len(edits) != 1:
            return False
        test, edit = tests[0], edits[0]
        if (edit.get("name") != "embeddedbitmap" or edit.get("mode") != "assign"
                or edit.findtext("bool") != str(enabled).lower()):
            return False
        if test.get("compare", "eq") != ("eq" if enabled else "not_eq"):
            return False
        return ((test.get("name") == "color" and test.findtext("bool") == "true")
                or (test.get("name") == "family" and test.findtext("string") == "Noto Color Emoji"))

    _KNOWN_CONSTS = {
        "none", "rgb", "bgr", "vrgb", "vbgr",
        "hintnone", "hintslight", "hintmedium", "hintfull",
        "lcdnone", "lcddefault", "lcdlight", "lcdlegacy",
    }

    FAMILY_REWRITES = {
        "Arial": "sans-serif", "Helvetica": "sans-serif", "Verdana": "sans-serif",
        "Times New Roman": "serif", "Courier New": "monospace",
        "Segoe UI Emoji": "emoji", "Apple Color Emoji": "emoji", "Twemoji Mozilla": "emoji",
    }

    def __init__(self, config_path: str | None = None, defaults: dict[str, Any] | None = None):
        self.config_path = (Path(config_path).expanduser() if config_path else
                            self._config_dir() / "fontconfig/conf.d/99-dusky-fonts.conf").resolve()
        self.defaults = dict(defaults or {})
        self.cache: dict[str, Any] = {}
        self._match_layout: list[tuple[str, str | None]] = []
        self._preserved_elements: list[str] = []
        self._write_lock = threading.RLock()
        # None means idle; False/True request a normal/forced cache refresh.
        self._cache_refresh_pending: bool | None = None

    @contextmanager
    def _file_lock(self):
        """Serialize writes and toolkit sync across TUI/setup processes."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config_path.with_name(f".{self.config_path.name}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    # ------------------------------------------------------------------
    # State loading
    # ------------------------------------------------------------------
    def load_state(self) -> dict[str, Any]:
        with self._write_lock:
            return self._load_state()

    def _load_state(self) -> dict[str, Any]:
        self._match_layout = []
        self._preserved_elements = []
        if not self.config_path.exists() or self.config_path.stat().st_size == 0:
            self.cache = {}
            return {}

        state: dict[str, Any] = {}
        try:
            tree = ET.parse(self.config_path)
            root = tree.getroot()
            if root.tag != "fontconfig":
                raise ValueError("Expected a <fontconfig> root element")
            self._preserved_elements = [ET.tostring(child, encoding="unicode")
                                        for child in root if child.tag not in ("dir", "alias", "match")]

            dir_values: list[str] = []
            for dir_el in root.findall("dir"):
                if dir_el.text and dir_el.text.strip():
                    dir_values.append(dir_el.text.strip())
            if dir_values:
                state["font_dir"] = dir_values[0] if len(dir_values) == 1 else dir_values

            for alias in root.findall("alias"):
                family_el = alias.find("family")
                if family_el is None or not family_el.text:
                    continue
                family = family_el.text.strip()
                if family not in self.ALIAS_CLASSES:
                    self._preserved_elements.append(ET.tostring(alias, encoding="unicode"))
                    continue
                prefer = alias.findall("prefer/family")
                if prefer:
                    fonts = [pf.text.strip() for pf in prefer if pf.text]
                    if fonts:
                        state[family] = fonts[0] if len(fonts) == 1 else fonts

            for match in root.findall("match"):
                target = match.get("target", "pattern")
                has_tests = bool(match.findall("test"))
                has_unmanaged_edits = any(
                    edit.get("name") not in self.RENDER_PROP_WHITELIST
                    or edit.get("mode") != "assign" or len(edit) != 1
                    or edit[0].tag not in ("bool", "const", "double", "int", "string")
                    for edit in match.findall("edit")
                )
                is_emoji_guard = self._is_bitmap_rule(match, False)
                is_emoji_force = self._is_bitmap_rule(match, True)
                is_pattern_rewrite = (target != "font" or has_tests or has_unmanaged_edits) and not is_emoji_guard and not is_emoji_force
                if is_pattern_rewrite:
                    self._match_layout.append(("rule", ET.tostring(match, encoding="unicode")))
                    continue

                # Keep each rendering rule at its position among custom rules.
                slot = "bitmap_guard" if is_emoji_guard else "bitmap_force" if is_emoji_force else "render"
                self._match_layout.append((slot, ET.tostring(match, encoding="unicode") if slot == "render" else None))
                if is_emoji_force:
                    continue

                for edit in match.findall("edit"):
                    name = edit.get("name")
                    if not name or name in self.IGNORED_PATTERN_EDIT_NAMES:
                        continue
                    val = self._extract_edit_value(edit)
                    if val is not None:
                        state[name] = val

            self.cache = state
            return state
        except Exception as e:
            raise ValueError(f"Cannot read font configuration {self.config_path}: {e}") from e

    def _extract_edit_value(self, edit: ET.Element) -> Any:
        bool_node = edit.find("bool")
        if bool_node is not None and bool_node.text:
            return self._text_bool(bool_node.text.strip())

        const_node = edit.find("const")
        if const_node is not None and const_node.text:
            return const_node.text.strip()

        double_node = edit.find("double")
        if double_node is not None and double_node.text:
            return self._to_num(double_node.text.strip(), float)

        int_node = edit.find("int")
        if int_node is not None and int_node.text:
            return self._to_num(int_node.text.strip(), int)

        string_node = edit.find("string")
        if string_node is not None and string_node.text:
            return string_node.text.strip()
        return None

    @staticmethod
    def _to_num(text: str, cast):
        try:
            return cast(text)
        except ValueError:
            return text

    @staticmethod
    def _text_bool(text: str) -> bool:
        return text.lower() in ("true", "1", "yes", "on", "t", "y")

    # ------------------------------------------------------------------
    # Value coercion helpers (shared by load + write paths)
    # ------------------------------------------------------------------
    _TRUTHY = ("true", "1", "yes", "on", "t", "y")

    @classmethod
    def as_bool(cls, val: Any) -> bool:
        if isinstance(val, bool):
            return val
        return str(val).lower() in cls._TRUTHY

    @classmethod
    def _is_numeric_string(cls, s: str) -> bool:
        if s.count(".") > 1 or s.startswith("-"):
            return s.count(".") <= 1 and s.lstrip("-").replace(".", "", 1).isdigit()
        return s.replace(".", "", 1).isdigit()

    def coerce_write_value(self, key: str, val: Any, item_type: str) -> Any:
        if val is None or val == "":
            return None
        if item_type == "bool" or isinstance(val, bool):
            return self.as_bool(val)
        if item_type == "int":
            try:
                return int(val)
            except (ValueError, TypeError):
                return val
        if item_type == "float":
            try:
                return float(val)
            except (ValueError, TypeError):
                return val
        return val

    def render_edit_data_type(self, name: str, val: Any, item_type: str) -> str:
        if isinstance(val, bool) or item_type == "bool":
            return "bool"
        if isinstance(val, int):
            return "int"
        if isinstance(val, float):
            return "float"
        if isinstance(val, str) and self._is_numeric_string(val):
            return "float" if "." in val else "int"
        return "const" if str(val).lower() in self._KNOWN_CONSTS else "string"

    # ------------------------------------------------------------------
    # Mutation paths
    # ------------------------------------------------------------------
    def write_batch(self, changes: list[tuple[str, str, Any, str]], *, force_cache: bool = False) -> tuple[bool, str, str]:
        if not changes:
            return True, "No pending changes.", ""

        with ExitStack() as locks:
            try:
                locks.enter_context(self._write_lock)
                locks.enter_context(self._file_lock())
                loaded = self.load_state()
                state: dict[str, Any] = self.defaults | loaded
            except (OSError, ValueError) as exc:
                return False, str(exc), ""
            old_dirs = {key: loaded.get(key) for key in self.DIR_KEYS}
            for key, scope, val, itype in changes:
                if val is None or val == "":
                    state.pop(key, None)
                else:
                    state[key] = self.coerce_write_value(key, val, itype)

            try:
                dirs_changed = old_dirs != {key: state.get(key) for key in self.DIR_KEYS}
                selected = {key: state[key] for key in self.ALIAS_CLASSES
                            if key in state and (dirs_changed or key not in loaded or any(change[0] == key for change in changes))}
                self.config_path.parent.mkdir(parents=True, exist_ok=True)

                root = ET.Element("fontconfig")

                for raw in self._preserved_elements:
                    root.append(ET.fromstring(raw))
                for fc in self.ALIAS_CLASSES:
                    value = state.get(fc)
                    if not value:
                        continue
                    alias = ET.SubElement(root, "alias", {"binding": "strong"})
                    fam = ET.SubElement(alias, "family")
                    fam.text = fc
                    pref = ET.SubElement(alias, "prefer")
                    if isinstance(value, list):
                        for item in value:
                            node = ET.SubElement(pref, "family")
                            node.text = str(item)
                    else:
                        node = ET.SubElement(pref, "family")
                        node.text = str(value)

                dirs: list[str] = []
                for dk in self.DIR_KEYS:
                    dv = state.get(dk)
                    if dv is None:
                        continue
                    raw_dirs = dv if isinstance(dv, list) else [dv]
                    for d in raw_dirs:
                        d = str(d).strip()
                        if not d:
                            continue
                        expanded = Path(d).expanduser()
                        if not expanded.is_absolute():
                            expanded = expanded.resolve()
                        dirs.append(str(expanded))

                for d in sorted(set(dirs)):
                    node = ET.SubElement(root, "dir")
                    node.text = d

                render_keys = [k for k in state
                               if k not in self.ALIAS_CLASSES
                               and k not in self.DIR_KEYS
                               and k in self.RENDER_PROP_WHITELIST
                               and state[k] is not None
                               and not isinstance(state[k], (list, dict))]

                emoji_guard = "embeddedbitmap" in render_keys and not self.as_bool(state["embeddedbitmap"])
                plain_keys = [k for k in render_keys
                              if k != "embeddedbitmap" or not emoji_guard]

                rendering = {slot: ET.Element("fontconfig")
                             for slot in ("render", "bitmap_guard", "bitmap_force")}
                # Update each property's final scalar assignment in place;
                # earlier assignments retain their precedence around custom rules.
                last_edits: dict[str, tuple[int, int]] = {}
                for index, (slot, raw) in enumerate(self._match_layout):
                    if slot == "render":
                        for edit_index, edit in enumerate(ET.fromstring(raw).findall("edit")):
                            last_edits[edit.get("name")] = (index, edit_index)
                new_keys = [key for key in plain_keys if key not in last_edits]
                if new_keys:
                    match = ET.SubElement(rendering["render"], "match", {"target": "font"})
                    for key in new_keys:
                        self._append_render_edit(match, key, state[key])

                if emoji_guard:
                    self._append_bitmap_rule(rendering["bitmap_guard"], False)
                    self._append_bitmap_rule(rendering["bitmap_force"], True)

                render_slots = {slot for slot, _raw in self._match_layout if slot != "rule"}
                rendered: set[str] = set()

                def emit_render(slot: str) -> None:
                    if slot not in rendered:
                        root.extend(rendering[slot])
                        rendered.add(slot)
                    if slot == "render":
                        for bitmap_slot in ("bitmap_guard", "bitmap_force"):
                            if bitmap_slot not in render_slots:
                                emit_render(bitmap_slot)

                if "render" not in render_slots:
                    emit_render("render")

                emitted_rewrites: set[str] = set()

                def emit_rewrite(name: str) -> None:
                    generic = self.FAMILY_REWRITES[name]
                    value = state.get(generic)
                    if not value:
                        return
                    match = ET.SubElement(root, "match", {"target": "pattern"})
                    test = ET.SubElement(match, "test", {"qual": "first", "name": "family"})
                    ET.SubElement(test, "string").text = name
                    edit = ET.SubElement(match, "edit", {"name": "family", "mode": "assign", "binding": "strong"})
                    for family in value if isinstance(value, list) else [value]:
                        ET.SubElement(edit, "string").text = str(family)

                for index, (slot, raw) in enumerate(self._match_layout):
                    if slot != "rule":
                        emit_render(slot)
                        if slot == "render":
                            match = ET.fromstring(raw)
                            for edit_index, edit in enumerate(match.findall("edit")):
                                key = edit.get("name")
                                if key not in plain_keys:
                                    match.remove(edit)
                                elif last_edits[key] == (index, edit_index):
                                    replacement = ET.Element("match")
                                    self._append_render_edit(replacement, key, state[key])
                                    edit[:] = list(replacement[0])
                            if len(match):
                                root.append(match)
                        continue
                    parsed = ET.fromstring(raw)
                    name = self._managed_rewrite_family(parsed)
                    if name:
                        if name not in emitted_rewrites:
                            emit_rewrite(name)
                            emitted_rewrites.add(name)
                    else:
                        root.append(parsed)

                for name in self.FAMILY_REWRITES:
                    if name not in emitted_rewrites:
                        emit_rewrite(name)

                xmlstr = minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ")
                xmlstr = re.sub(
                    r'^\s*<\?xml[^>]*\?>',
                    '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">',
                    xmlstr,
                    count=1,
                )
                clean_xml = "\n".join(ln for ln in xmlstr.splitlines() if ln.strip()) + "\n"
                if selected:
                    self._validate_families(selected, clean_xml)

                temp_path = self.config_path.with_name(
                    f".{self.config_path.name}.tmp-{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}"
                )
                try:
                    with open(temp_path, "w", encoding="utf-8") as f:
                        f.write(clean_xml)
                        f.flush()
                        os.fsync(f.fileno())
                    temp_path.replace(self.config_path)
                finally:
                    if temp_path.exists():
                        try:
                            temp_path.unlink()
                        except OSError:
                            pass

                self.cache = state

                if force_cache:
                    self._cache_refresh_pending = True
                elif dirs_changed and self._cache_refresh_pending is None:
                    self._cache_refresh_pending = False
                if self._cache_refresh_pending is not None:
                    self.refresh_cache(force=self._cache_refresh_pending)
                    self._cache_refresh_pending = None
                if not self._sync_system_fonts(quiet=True):
                    return False, "Fontconfig saved, but toolkit synchronization failed; retry Sync GTK & Qt Fonts.", ""

                return True, f"Successfully applied {len(changes)} font settings.", ""
            except Exception as e:
                return False, f"Font configuration failed: {e}", ""

    def _validate_families(self, selected: dict[str, Any], candidate_xml: str) -> None:
        """Ask fontconfig about the prospective config without changing live files.

        Proxy the other XDG font configs at their original positions, replacing
        just this file. This retains system/user font directories and scan rules
        while removing directories that only the old managed config supplied.
        """
        original = self._config_dir() / "fontconfig"
        with tempfile.TemporaryDirectory(prefix="dusky-font-check-") as temporary:
            config_home = Path(temporary)
            proxy = config_home / "fontconfig"
            (proxy / "conf.d").mkdir(parents=True)
            candidate = config_home / "candidate.conf"
            candidate.write_text(candidate_xml, encoding="utf-8")
            wrapper = ET.Element("fontconfig")

            def include(path: Path) -> None:
                ET.SubElement(wrapper, "include", {"ignore_missing": "yes"}).text = str(path)

            conf_dir = original / "conf.d"
            entries = {entry.name: entry for entry in conf_dir.iterdir()
                       if entry.name[0] in "0123456789" and entry.suffix == ".conf"} if conf_dir.is_dir() else {}
            original_root = original / "fonts.conf"
            root_is_target = original_root.resolve() == self.config_path
            replaced = False
            for name, entry in entries.items():
                if entry.resolve() == self.config_path:
                    entries[name] = candidate
                    replaced = True
            if not replaced and not root_is_target:
                entries[self.config_path.name] = candidate
            for name in sorted(entries):
                include(entries[name])
            include(candidate if root_is_target else original_root)
            ET.ElementTree(wrapper).write(proxy / "fonts.conf", encoding="utf-8")
            environment = os.environ | {"XDG_CONFIG_HOME": str(config_home)}
            source = environment.get("FONTCONFIG_FILE")
            if source and Path(source).expanduser().resolve() == self.config_path:
                environment["FONTCONFIG_FILE"] = str(candidate)
            proc = subprocess.run(
                ["fc-list", "--format=%{[]family{%{family}\n}}", ":"],
                env=environment, check=True, capture_output=True, text=True, timeout=30,
            )
            if proc.stderr.strip():
                raise ValueError(proc.stderr.strip())
        known = {family.strip().casefold() for family in proc.stdout.splitlines() if family.strip()}
        missing = [family for value in selected.values()
                   for family in (value if isinstance(value, list) else [value])
                   if str(family).casefold() not in known]
        if missing:
            raise ValueError(f"Missing font families: {', '.join(map(str, missing))}; install their font packages first.")

    def _managed_rewrite_family(self, match: ET.Element) -> str | None:
        """Recognize our simple named aliases, preserving other family tests."""
        if match.get("target", "pattern") != "pattern":
            return None
        tests, edits = match.findall("test"), match.findall("edit")
        if len(tests) != 1 or len(edits) != 1:
            return None
        test, edit = tests[0], edits[0]
        name = test.findtext("string", "").strip()
        if (test.get("name") == "family" and test.get("qual") == "first"
                and test.get("compare", "eq") == "eq" and len(test) == 1
                and name in self.FAMILY_REWRITES
                and edit.get("name") == "family" and edit.get("mode") == "assign"
                and edit.get("binding") == "strong" and len(edit)
                and all(child.tag == "string" for child in edit)):
            return name
        return None

    def _append_render_edit(self, match: ET.Element, name: str, val: Any) -> None:
        edit = ET.SubElement(match, "edit", {"mode": "assign", "name": name})
        if isinstance(val, bool):
            kid = ET.SubElement(edit, "bool")
            kid.text = "true" if val else "false"
        elif isinstance(val, int):
            kid = ET.SubElement(edit, "int")
            kid.text = str(val)
        elif isinstance(val, float):
            kid = ET.SubElement(edit, "double")
            kid.text = f"{val:g}"
        elif str(val).lower() in self._KNOWN_CONSTS:
            kid = ET.SubElement(edit, "const")
            kid.text = str(val)
        else:
            kid = ET.SubElement(edit, "string")
            kid.text = str(val)

    def _append_bitmap_rule(self, root: ET.Element, enabled: bool) -> None:
        """Keep color bitmaps available while disabling outline font bitmaps."""
        match = ET.SubElement(root, "match", {"target": "font"})
        test = ET.SubElement(match, "test", {"name": "color", "compare": "eq" if enabled else "not_eq"})
        ET.SubElement(test, "bool").text = "true"
        self._append_render_edit(match, "embeddedbitmap", enabled)

    @staticmethod
    def refresh_cache(force: bool = False) -> None:
        subprocess.run(["fc-cache", *(["-f"] if force else [])], check=True, timeout=120)

    def sync_system_fonts(self, quiet: bool = False) -> bool:
        try:
            with self._write_lock, self._file_lock():
                self.load_state()
                return self._sync_system_fonts(quiet)
        except (OSError, ValueError) as exc:
            print(f"[-] Font synchronization failed: {exc}", file=sys.stderr)
            return False

    def _sync_system_fonts(self, quiet: bool = False) -> bool:
        """Mirror the configured generic families to the toolkit layers that
        pin their own fonts (GTK settings.ini + dconf, Qt qt5ct/qt6ct) so a
        change is truly system-wide.

        fontconfig only governs generic requests; GTK apps read
        gtk-font-name and Qt apps read [Fonts] general/fixed, both of which
        are per-toolkit and separate from fontconfig.

        Reads families straight from self.cache (fresh after write_batch),
        reusing existing sizes when present.
        """
        family = ""
        mono = ""
        for key in ("sans-serif", "monospace"):
            val = self.cache.get(key)
            if not (isinstance(val, str) and val.strip()):
                state = self.load_state()
                val = state.get(key)
            if isinstance(val, list):
                val = val[0] if val else ""
            if isinstance(val, str):
                value = val.strip()
                if key == "sans-serif":
                    family = value
                else:
                    mono = value

        if not family:
            if not quiet:
                print("[FontconfigEngine] No sans-serif family configured; toolkit sync skipped.")
            return False

        # --- GTK ---------------------------------------------------------
        gtk_ok = self._sync_gtk_toolkits(family, mono, quiet)

        # --- Qt (qt5ct / qt6ct) ------------------------------------------
        qt_ok = True
        conf_dir = self._config_dir()
        for conf_path, version in ((conf_dir / "qt5ct" / "qt5ct.conf", "qt5"),
                                   (conf_dir / "qt6ct" / "qt6ct.conf", "qt6")):
            try:
                qt_ok = self._patch_qt_conf(conf_path, version, family, mono, quiet) and qt_ok
            except OSError as e:
                qt_ok = False
                if not quiet:
                    print(f"[-] {conf_path}: {e}")

        if not quiet and qt_ok:
            print("[i] Qt (qt5ct/qt6ct) fonts synced.")

        return gtk_ok and qt_ok

    # ------------------------------------------------------------------
    # GTK
    # ------------------------------------------------------------------
    def _sync_gtk_toolkits(self, family: str, mono: str, quiet: bool) -> bool:
        gs = shutil.which("gsettings")
        size = self._gsettings_font_size(gs, "font-name") if gs else str(self._DEFAULT_GTK_SIZE)
        ok = True
        for path in self._gtk_ini_paths():
            entries = {"gtk-font-name": f"{family} {self._existing_gtk_size(path=path, fallback=size)}"}
            try:
                self._patch_gtk_ini(path, entries, remove_keys={"gtk-monospace-font-name"})
                if not quiet:
                    print(f"[+] {path}: gtk-font-name={entries['gtk-font-name']}")
            except OSError as exc:
                ok = False
                if not quiet:
                    print(f"[-] {path}: {exc}")

        if gs:
            for key, fsize in (
                    ("font-name", size),
                    ("document-font-name", self._gsettings_font_size(gs, "document-font-name")),
                    ("monospace-font-name", self._gsettings_font_size(gs, "monospace-font-name"))):
                try:
                    fam = (self.cache.get("serif", family) if key == "document-font-name"
                           else mono if key == "monospace-font-name" else family)
                    if isinstance(fam, list):
                        fam = fam[0] if fam else ""
                    if not fam:
                        continue
                    written = subprocess.run(
                        [gs, "set", "org.gnome.desktop.interface", key, f"{fam} {fsize}"],
                        check=True, capture_output=True, text=True, timeout=10,
                    )
                    if written.stderr.strip():
                        raise RuntimeError(written.stderr.strip())
                    readback = subprocess.run(
                        [gs, "get", "org.gnome.desktop.interface", key],
                        check=True, capture_output=True, text=True, timeout=5,
                    )
                    if ast.literal_eval(readback.stdout.strip()) != f"{fam} {fsize}":
                        raise RuntimeError("gsettings did not persist the requested font")
                    if not quiet:
                        print(f"[+] gsettings {key}: {fam} {fsize}")
                except (OSError, ValueError, SyntaxError, RuntimeError, subprocess.SubprocessError) as exc:
                    ok = False
                    print(f"[-] gsettings {key}: {exc}", file=sys.stderr)
        else:
            ok = False
            print("[-] gsettings is required for desktop font synchronization.", file=sys.stderr)
        return ok

    # ------------------------------------------------------------------
    # Qt (qt5ct / qt6ct)
    # ------------------------------------------------------------------
    _QT_FONT_TEMPLATES = {
        "qt5": "family,size,-1,5,50,0,0,0,0,0",
        "qt6": "family,size,-1,5,400,0,0,0,0,0,0,0,0,0,0,1",
    }
    _QT_DEFAULT_SIZE = 12

    @staticmethod
    def _qt_swap_family(serialized: str, family: str) -> str:
        """Replace only the family field of a QFont serialization, keeping
        size / weight / flags intact."""
        parts = serialized.split(",", 1)
        parts[0] = family
        return ",".join(parts)

    @staticmethod
    def _qt_make_serial(version: str, family: str, size: str) -> str:
        template = FontconfigEngine._QT_FONT_TEMPLATES[version]
        return ",".join([family, size, *template.split(",")[2:]])

    @staticmethod
    def _is_valid_size(s: str) -> bool:
        if not s:
            return False
        try:
            val = float(s)
            return math.isfinite(val) and val > 0
        except ValueError:
            return False

    @staticmethod
    def _qt_size_from(serialized: str) -> str:
        if serialized:
            parts = serialized.split(",")
            if len(parts) > 1 and FontconfigEngine._is_valid_size(parts[1].strip()):
                return parts[1].strip()
        return str(FontconfigEngine._QT_DEFAULT_SIZE)

    @staticmethod
    def _qt_slots(path: Path) -> dict[str, str]:
        """Extract existing [Fonts] general/fixed serializations."""
        if not path.is_file():
            return {}
        slots: dict[str, str] = {}
        in_fonts = False
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                in_fonts = stripped == "[Fonts]"
                continue
            if in_fonts and "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                if key in ("general", "fixed"):
                    raw = val.strip()
                    if raw.startswith('"') and raw.endswith('"'):
                        raw = raw[1:-1]
                    slots[key] = re.sub(r'\\([\\"nr])', lambda m: {"n": "\n", "r": "\r"}.get(m[1], m[1]), raw)
        return slots

    @staticmethod
    def _qt_write(path: Path, slots: dict[str, str]) -> None:
        """Rewrite a qt5ct/qt6ct.conf with updated [Fonts] general/fixed,
        preserving all other sections byte-for-byte (atomic tmp+rename so
        concurrent readers never see a half-written file)."""
        content = path.read_text() if path.is_file() else ""
        lines = content.splitlines()
        out: list[str] = []
        in_fonts = False
        def encode(value: str) -> str:
            return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
        gen_val = encode(slots.get("general", ""))
        fix_val = encode(slots.get("fixed", ""))
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("["):
                if in_fonts:
                    in_fonts = False
                if stripped == "[Fonts]":
                    in_fonts = True
                    out.append("[Fonts]")
                    out.append(f'general="{gen_val}"')
                    out.append(f'fixed="{fix_val}"')
                    continue
                out.append(line)
                continue
            if in_fonts and "=" in line and line.partition("=")[0].strip() in ("general", "fixed"):
                continue
            out.append(line)
        if not any(ln.strip() == "[Fonts]" for ln in out):
            if out:
                out.append("")
            out.append("[Fonts]")
            out.append(f'general="{gen_val}"')
            out.append(f'fixed="{fix_val}"')
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(out).rstrip("\n") + "\n")
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def _patch_qt_conf(self, conf: Path, version: str, sans: str, mono: str, quiet: bool) -> bool:
        """Update [Fonts] general/fixed in a qt5ct/qt6ct.conf file.

        general -> sans-serif family, fixed -> monospace family. Existing
        QFont serializations keep their size/weight/flags (only the family
        is swapped); new entries use a sensible default size."""
        try:
            slots = self._qt_slots(conf)
            general = slots.get("general", "")
            size = self._qt_size_from(general)
            slots["general"] = self._qt_swap_family(general, sans) if general else self._qt_make_serial(version, sans, size)
            fixed = slots.get("fixed", "")
            slots["fixed"] = self._qt_swap_family(fixed, mono) if fixed else self._qt_make_serial(version, mono, size)
            self._qt_write(conf, slots)
            if not quiet:
                print(f"[+] {conf}: general={slots['general']}")
            return True
        except OSError as e:
            if not quiet:
                print(f"[-] {conf}: {e}")
            return False

    _DEFAULT_GTK_SIZE = 11

    @classmethod
    def _config_dir(cls) -> Path:
        """XDG Base Directory compliant config home resolution ($XDG_CONFIG_HOME or $HOME/.config)."""
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg and xdg.strip():
            if Path(xdg).is_absolute():
                return Path(xdg)
        home = os.environ.get("HOME")
        if home and home.strip():
            return Path(home).expanduser() / ".config"
        return Path.home() / ".config"

    @classmethod
    def _gtk_ini_paths(cls) -> tuple[Path, Path]:
        """Resolve GTK settings.ini paths at call time so XDG_CONFIG_HOME / HOME switches are honored."""
        conf = cls._config_dir()
        return (
            conf / "gtk-3.0" / "settings.ini",
            conf / "gtk-4.0" / "settings.ini",
        )

    @classmethod
    def _existing_gtk_size(cls, keyline: str = "gtk-font-name=", path: Path | None = None, fallback: str | None = None) -> str:
        """Reuse the size from any existing gtk-font-name / gtk-monospace-font-name."""
        target_key = keyline.split("=", 1)[0].strip()
        for ini in (path,) if path else cls._gtk_ini_paths():
            if not ini.is_file():
                continue
            try:
                in_settings = False
                for line in ini.read_text().splitlines():
                    stripped = line.strip()
                    if stripped.startswith("["):
                        in_settings = stripped == "[Settings]"
                        continue
                    if in_settings and "=" in stripped:
                        key, _, val = stripped.partition("=")
                        if key.strip() == target_key:
                            parts = val.strip().rsplit(" ", 1)
                            if len(parts) == 2 and cls._is_valid_size(parts[1]):
                                return parts[1]
            except OSError:
                continue
        return fallback if fallback is not None else str(cls._DEFAULT_GTK_SIZE)

    @classmethod
    def _patch_gtk_ini(cls, path: Path, entries: dict[str, str], remove_keys: set[str] | None = None) -> None:
        content = path.read_text() if path.is_file() else ""
        out: list[str] = []
        replaced: set[str] = set()
        to_remove = remove_keys or set()
        in_settings = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "[Settings]":
                in_settings = True
                out.append(line)
                continue
            if stripped.startswith("[") and in_settings:
                in_settings = False
            if in_settings and "=" in line:
                key = line.split("=", 1)[0].strip()
                if key in to_remove:
                    continue
                if key in entries:
                    out.append(f"{key}={entries[key]}")
                    replaced.add(key)
                    continue
            out.append(line)
        missing = [k for k in entries if k not in replaced]
        if missing:
            headers = [i for i, line in enumerate(out) if line.strip() == "[Settings]"]
            if headers:
                idx = headers[0] + 1
                for k in missing:
                    out.insert(idx, f"{k}={entries[k]}")
            else:
                if out and not out[-1].strip():
                    out.pop()
                out.append("[Settings]")
                out.extend(f"{k}={entries[k]}" for k in entries)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(out) + "\n")
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    @staticmethod
    def _gsettings_font_size(gs: str, key: str) -> str:
        try:
            out = subprocess.run(
                [gs, "get", "org.gnome.desktop.interface", key],
                capture_output=True, text=True, timeout=5,
            )
            val = out.stdout.strip().strip("'")
            parts = val.rsplit(" ", 1)
            if len(parts) == 2 and FontconfigEngine._is_valid_size(parts[1]):
                return parts[1]
        except Exception:
            pass
        return str(FontconfigEngine._DEFAULT_GTK_SIZE)

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])


if __name__ == "__main__":
    # CLI entry: re-run the GTK/Qt/dconf sync from the config file
    # (used by the TUI's "Sync GTK & Qt Fonts" action).
    engine = FontconfigEngine()
    success = engine.sync_system_fonts(quiet=False)
    raise SystemExit(0 if success else 1)
