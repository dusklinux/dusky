#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: MATUGEN PRESETS & COLOR PALETTE ENGINE
===============================================================================
Target: Arch Linux / Hyprland / Matugen / Wayland
Python 3.15 implementation with explicit lazy imports, PEP 695 type aliases,
frozendict immutability, zero-hardcoding paths, and atomic file mutations.
===============================================================================
"""

import os
import re
import sys
lazy import colorsys
lazy import json
lazy import subprocess
lazy import tempfile
lazy from pathlib import Path
lazy from typing import Any, override

_VIVID_DIR = Path.home() / "user_scripts" / "theme_matugen" / "vivid"
if str(_VIVID_DIR) not in sys.path:
    sys.path.insert(0, str(_VIVID_DIR))
lazy import vivid_theme
lazy from vivid_data import VALID_SCHEMES

from python.frontend.core_types import BaseEngine

# PEP 695 Type Aliases
type ChangeTuple = tuple[str, str, str, str]
type WriteResult = tuple[bool, str, str]

# Regex patterns
_RE_HEX = re.compile(r"#?[a-fA-F0-9]{6}")
_RE_RGB_PARSER = re.compile(r"(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})")
_RE_HSL_PARSER = re.compile(r"(\d+(?:\.\d+)?)(?:deg)?[\s,]+(\d+(?:\.\d+)?%?)[\s,]+(\d+(?:\.\d+)?%?)")

# Default state settings aligned with theme_ctl.sh & dusky_matugen_presets.sh
_DEFAULT_SETTINGS = frozendict({
    "mode": "dark",
    "type": "scheme-tonal-spot",
    "contrast": "0",
    "index": "0",
    "base16": "disable",
    "t_type": "random",
    "t_dur": "2",
    "t_fps": "60",
    "t_bez": ".54,0,.34,.99",
    "t_ang": "30",
    "t_pos": "center",
    "last_applied_hex": "#0000FF",
    "theme_source": "wallpaper",
    "vivid_preset": "none",
})

# State key mapping between state.conf keys and engine keys
_STATE_KEY_TO_ENGINE = frozendict({
    "THEME_MODE": "mode",
    "MATUGEN_TYPE": "type",
    "MATUGEN_CONTRAST": "contrast",
    "SOURCE_COLOR_INDEX": "index",
    "BASE16_BACKEND": "base16",
    "AWWW_TRANS_TYPE": "t_type",
    "AWWW_TRANS_DURATION": "t_dur",
    "AWWW_TRANS_FPS": "t_fps",
    "AWWW_TRANS_BEZIER": "t_bez",
    "AWWW_TRANS_ANGLE": "t_ang",
    "AWWW_TRANS_POS": "t_pos",
    "LAST_APPLIED_HEX": "last_applied_hex",
    "LAST_THEME_SOURCE": "theme_source",
    "LAST_VIVID_PRESET": "vivid_preset",
})

_ENGINE_TO_STATE_KEY = frozendict({v: k for k, v in _STATE_KEY_TO_ENGINE.items()})

# Curated generation recipes. Material You tones can differ from their seed HEX.
_DEFAULT_EXACT_PRESETS = (
    frozendict({
        "id": "vivid_red",
        "label": "Pure Vivid Red",
        "hex": "#FF0000",
        "scheme": "scheme-fidelity",
        "contrast": "0",
        "mode": "dark",
        "desc": "Ultra-saturated pure red; scheme-fidelity keeps the pure hue without tonal desaturation.",
    }),
    frozendict({
        "id": "crimson_tide",
        "label": "Crimson Tide",
        "hex": "#DC143C",
        "scheme": "scheme-vibrant",
        "contrast": "0",
        "mode": "dark",
        "desc": "Deep vivid crimson; scheme-vibrant boosts chromatic saturation.",
    }),
    frozendict({
        "id": "electric_blue",
        "label": "Electric Blue",
        "hex": "#0000FF",
        "scheme": "scheme-fidelity",
        "contrast": "0",
        "mode": "dark",
        "desc": "Pure electric blue; fidelity preserves the primary wavelength.",
    }),
    frozendict({
        "id": "cyan_punch",
        "label": "Cyan Punch",
        "hex": "#00FFFF",
        "scheme": "scheme-fidelity",
        "contrast": "0",
        "mode": "dark",
        "desc": "Max saturation cyan punch; fidelity guarantees vivid neon cyan accents.",
    }),
    frozendict({
        "id": "toxic_green",
        "label": "Toxic Green",
        "hex": "#00FF00",
        "scheme": "scheme-fidelity",
        "contrast": "0",
        "mode": "dark",
        "desc": "Pure neon toxic green; scheme-fidelity prevents olive/drab shifts.",
    }),
    frozendict({
        "id": "matrix_green",
        "label": "Matrix Cyber Green",
        "hex": "#03A062",
        "scheme": "scheme-vibrant",
        "contrast": "0",
        "mode": "dark",
        "desc": "Cyberpunk terminal green with high vibrancy.",
    }),
    frozendict({
        "id": "pure_magenta",
        "label": "Pure Magenta",
        "hex": "#FF00FF",
        "scheme": "scheme-fidelity",
        "contrast": "0",
        "mode": "dark",
        "desc": "Vivid pure magenta; fidelity guarantees zero hue shift.",
    }),
    frozendict({
        "id": "neon_pink",
        "label": "Neon Cyber Pink",
        "hex": "#FF007F",
        "scheme": "scheme-fruit-salad",
        "contrast": "0.2",
        "mode": "dark",
        "desc": "Electric pink; fruit-salad provides warm chromatic accents.",
    }),
    frozendict({
        "id": "safety_yellow",
        "label": "Safety Yellow",
        "hex": "#FFFF00",
        "scheme": "scheme-content",
        "contrast": "0",
        "mode": "dark",
        "desc": "Pure safety yellow; scheme-content preserves yellow luminance.",
    }),
    frozendict({
        "id": "synthwave_sun",
        "label": "Synthwave Sun Orange",
        "hex": "#FF7E00",
        "scheme": "scheme-vibrant",
        "contrast": "0",
        "mode": "dark",
        "desc": "Retro sunset orange with vivid amber highlights.",
    }),
    frozendict({
        "id": "plasma_purple",
        "label": "Plasma Purple",
        "hex": "#6A0DAD",
        "scheme": "scheme-vibrant",
        "contrast": "0",
        "mode": "dark",
        "desc": "Deep plasma purple with rich violet secondary tones.",
    }),
    frozendict({
        "id": "laser_lemon",
        "label": "Laser Lemon",
        "hex": "#FFFF66",
        "scheme": "scheme-content",
        "contrast": "0",
        "mode": "dark",
        "desc": "High-visibility fluorescent lemon; content prevents muddy tints.",
    }),
    frozendict({
        "id": "violet_ray",
        "label": "Violet Ray",
        "hex": "#EE82EE",
        "scheme": "scheme-fruit-salad",
        "contrast": "0",
        "mode": "dark",
        "desc": "Vivid pastel violet with balanced tonal warmth.",
    }),
    frozendict({
        "id": "pure_black",
        "label": "Pure Pitch Black",
        "hex": "#000000",
        "scheme": "scheme-monochrome",
        "contrast": "0",
        "mode": "dark",
        "desc": "Pure monochrome OLED black; prevents unwanted invented hues.",
    }),
    frozendict({
        "id": "pure_white",
        "label": "Pure Bright White",
        "hex": "#FFFFFF",
        "scheme": "scheme-monochrome",
        "contrast": "0",
        "mode": "dark",
        "desc": "Pure clean monochrome white; avoids cyan/tint artifacts.",
    }),
)


class MatugenPresetsEngine(BaseEngine):
    """
    Production-grade Engine managing Matugen color presets, state, favorites,
    and curated hardcoded schemas for the Dusky TUI ecosystem.
    """

    def __init__(self, config_path: str | Path = "~/.config/dusky/settings/dusky_theme/state.conf") -> None:
        self.state_file = Path(config_path).expanduser().resolve()
        self.state_dir = self.state_file.parent
        self.favorites_file = self.state_dir / "theme_preset_fav"
        self.exact_file = self.state_dir / "exact_color_presets.json"
        self.vivid_favorites_file = self.state_dir / "vivid_favorites.json"

        # Theme controller script path
        user_scripts = Path.home() / "user_scripts"
        self.theme_ctl = user_scripts / "theme_matugen" / "theme_ctl.sh"

        self.cache: dict[str, Any] = {}
        self.favorites: list[tuple[str, str]] = []
        self.vivid_favorites: list[dict[str, str]] = []
        self.vivid_favorites_error: str = ""
        self.exact_presets: list[dict[str, Any]] = []
        self.exact_scheme_override: str = "per-preset"
        self.new_exact_hex: str = "#FF0080"
        self.new_exact_scheme: str = "scheme-fidelity"

    @property
    @override
    def target_path(self) -> str:
        return str(self.state_file)

    # =========================================================================
    # STATE PARSING & LOADING
    # =========================================================================

    @override
    def load_state(self) -> dict[str, Any]:
        """
        Parses state.conf, favorites, and exact presets into self.cache.
        Exposes both bare keys and 'DEFAULT/<key>' aliases for Dusky TUI router.
        """
        self.cache = dict(_DEFAULT_SETTINGS)

        # 1. Parse state.conf if present
        if self.state_file.is_file():
            try:
                content = self.state_file.read_text(encoding="utf-8")
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k in _STATE_KEY_TO_ENGINE:
                        engine_k = _STATE_KEY_TO_ENGINE[k]
                        if engine_k == "contrast" and v == "0.0":
                            v = "0"
                        self.cache[engine_k] = v
            except OSError as exc:
                print(f"[-] MatugenPresetsEngine: Failed to read {self.state_file}: {exc}")

        # 2. Parse favorites
        self._load_favorites()
        self._load_vivid_favorites()
        self.cache["fav_count"] = len(self.favorites) + len(self.vivid_favorites)

        # 3. Parse exact presets
        self._load_exact_presets()
        self.cache["exact_scheme_override"] = self.exact_scheme_override
        self.cache["new_exact_hex"] = self.new_exact_hex
        self.cache["new_exact_scheme"] = self.new_exact_scheme

        # 4. Helper aliases & controls
        last_hex = str(self.cache.get("last_applied_hex", "#0000FF")).upper()
        if not _RE_HEX.fullmatch(last_hex):
            last_hex = _DEFAULT_SETTINGS["last_applied_hex"]
        if not last_hex.startswith("#"):
            last_hex = f"#{last_hex}"
        self.cache["last_applied_hex"] = last_hex
        self.cache["active_color"] = last_hex
        self.cache["custom_hex"] = last_hex
        self.cache["custom_rgb"] = self._hex_to_rgb_str(last_hex)
        self.cache["custom_hsl"] = self._hex_to_hsl_str(last_hex)

        # Action triggers (momentary, always False in state)
        for act in (
            "action_apply_theme",
            "action_apply_anim",
            "action_apply_settings",
            "action_regen",
            "action_add_favorite",
            "action_remove_favorite",
            "action_add_exact_preset",
            "action_resume_wallpaper",
        ):
            self.cache[act] = False

        # 5. Vivid status
        active_id = str(self.cache["vivid_preset"])
        is_vivid = self.cache["theme_source"] == "vivid" and active_id != "none"
        active_id = active_id if is_vivid else "none"
        active_hex = last_hex
        self.cache["vivid_active_id"] = active_id
        self.cache["vivid_active_hex"] = active_hex

        # Expose DEFAULT/ scoped keys for Dusky TUI router compatibility
        scoped = {f"DEFAULT/{k}": v for k, v in self.cache.items()}
        self.cache.update(scoped)
        self.cache["exact/active_color"] = last_hex

        return self.cache

    def _load_favorites(self) -> None:
        """Loads favorites list from theme_preset_fav."""
        self.favorites = []
        if not self.favorites_file.is_file():
            return
        try:
            content = self.favorites_file.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "|" not in line:
                    continue
                label, hex_val = line.split("|", 1)
                label = label.strip()
                hex_val = hex_val.strip().upper()
                if label and _RE_HEX.fullmatch(hex_val):
                    if not hex_val.startswith("#"):
                        hex_val = f"#{hex_val}"
                    self.favorites.append((label, hex_val))
        except OSError:
            pass

    def _save_favorites(self) -> bool:
        """Atomically saves favorites list to theme_preset_fav."""
        lines = [
            "# Dusky Matugen Favorites",
            "# Format: Label|#HEXCODE",
        ]
        for label, hex_val in self.favorites:
            lines.append(f"{label}|{hex_val}")
        content = "\n".join(lines) + "\n"
        return self._atomic_write(self.favorites_file, content)

    def _load_vivid_favorites(self) -> None:
        """Keep pack identities separate from the legacy HEX-only favorites file."""
        self.vivid_favorites = []
        self.vivid_favorites_error = ""
        if not self.vivid_favorites_file.is_file():
            return
        try:
            entries = json.loads(self.vivid_favorites_file.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                raise ValueError("Vivid favorites must contain a list")
            seen: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("Vivid favorite must be an object")
                if not all(isinstance(entry.get(key), str) for key in ("id", "label", "hex")):
                    raise ValueError("Vivid favorite needs id, label, and hex strings")
                if not re.fullmatch(r"[a-z][a-z0-9_]*", entry["id"]):
                    raise ValueError("Invalid Vivid favorite ID")
                if not entry["label"].strip() or not _RE_HEX.fullmatch(entry["hex"]):
                    raise ValueError("Invalid Vivid favorite label or HEX")
                if entry["id"] not in seen:
                    self.vivid_favorites.append({key: entry[key] for key in ("id", "label", "hex")})
                    seen.add(entry["id"])
        except (OSError, ValueError) as exc:
            self.vivid_favorites = []
            self.vivid_favorites_error = str(exc)
            print(f"[-] Cannot load Vivid favorites: {exc}", file=sys.stderr)

    def _save_vivid_favorites(self) -> bool:
        if self.vivid_favorites_error:
            return False
        return self._atomic_write(
            self.vivid_favorites_file, json.dumps(self.vivid_favorites, indent=2) + "\n"
        )

    def _update_favorite_count(self) -> None:
        count = len(self.favorites) + len(self.vivid_favorites)
        self.cache["fav_count"] = self.cache["DEFAULT/fav_count"] = count

    def add_vivid_favorite(self, preset_id: str) -> tuple[bool, str]:
        """Save the canonical pack ID, plus a label and swatch for display."""
        if self.vivid_favorites_error:
            return False, f"Cannot save Vivid favorites: {self.vivid_favorites_error}"
        if any(p["id"] == preset_id for p in self.vivid_favorites):
            return False, f"Vivid pack already in favorites: {preset_id}"
        preset = next((p for p in self.list_vivid_presets() if p["id"] == preset_id), None)
        if preset is None:
            return False, f"Vivid pack '{preset_id}' is unavailable"
        self.vivid_favorites.append({key: preset[key] for key in ("id", "label", "hex")})
        if not self._save_vivid_favorites():
            self.vivid_favorites.pop()
            return False, "Failed to save Vivid favorites"
        self._update_favorite_count()
        return True, f"Added Vivid pack '{preset['label']}' to favorites"

    def remove_vivid_favorite(self, preset_id: str) -> tuple[bool, str]:
        original = self.vivid_favorites
        self.vivid_favorites = [p for p in original if p["id"] != preset_id]
        if len(self.vivid_favorites) == len(original):
            return False, f"Vivid pack '{preset_id}' was not found in favorites"
        if not self._save_vivid_favorites():
            self.vivid_favorites = original
            return False, "Failed to save Vivid favorites"
        self._update_favorite_count()
        return True, f"Removed Vivid pack '{preset_id}' from favorites"

    def add_active_favorite(self) -> tuple[bool, str]:
        if self.cache.get("theme_source") == "vivid":
            return self.add_vivid_favorite(str(self.cache.get("vivid_preset", "none")))
        return self.add_favorite(str(self.cache.get("last_applied_hex", "#0000FF")))

    def remove_active_favorite(self) -> tuple[bool, str]:
        if self.cache.get("theme_source") == "vivid":
            return self.remove_vivid_favorite(str(self.cache.get("vivid_preset", "none")))
        return self.remove_favorite(str(self.cache.get("last_applied_hex", "#0000FF")))

    def _load_exact_presets(self) -> None:
        """Loads curated hardcoded presets from exact_color_presets.json."""
        if not self.exact_file.is_file():
            self.exact_presets = [dict(p) for p in _DEFAULT_EXACT_PRESETS]
            return
        try:
            raw = json.loads(self.exact_file.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("Recipe file must contain a list")
            self.exact_presets = []
            seen: set[str] = set()
            for entry in raw:
                try:
                    preset = self._normalize_recipe(entry)
                    if preset["id"] in seen:
                        raise ValueError("Duplicate recipe ID")
                except ValueError as exc:
                    print(f"[-] Skipping invalid curated recipe: {exc}", file=sys.stderr)
                    continue
                seen.add(preset["id"])
                self.exact_presets.append(preset)
        except (OSError, ValueError) as exc:
            print(f"[-] Cannot load curated recipes: {exc}", file=sys.stderr)
            self.exact_presets = [dict(p) for p in _DEFAULT_EXACT_PRESETS]

    @staticmethod
    def _normalize_recipe(entry: Any) -> dict[str, Any]:
        """Validate persisted recipes before exposing them as TUI actions."""
        if not isinstance(entry, dict):
            raise ValueError("Recipe must be an object")
        for key in ("id", "label", "hex"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise ValueError(f"Recipe needs a nonempty {key}")
        preset = dict(entry)
        preset["hex"] = preset["hex"].strip().upper()
        if not _RE_HEX.fullmatch(preset["hex"]):
            raise ValueError("Invalid recipe HEX")
        if not preset["hex"].startswith("#"):
            preset["hex"] = f"#{preset['hex']}"
        scheme = preset.get("scheme", "scheme-fidelity")
        if not isinstance(scheme, str) or scheme not in VALID_SCHEMES:
            raise ValueError("Invalid recipe scheme")
        if preset.get("mode", "dark") not in ("dark", "light"):
            raise ValueError("Invalid recipe mode")
        if isinstance(preset.get("contrast"), bool):
            raise ValueError("Invalid recipe contrast")
        try:
            contrast = float(preset.get("contrast", "0"))
        except (ValueError, TypeError):
            raise ValueError("Invalid recipe contrast") from None
        if not -1 <= contrast <= 1:
            raise ValueError("Recipe contrast must be between -1 and 1")
        preset["contrast"] = str(preset.get("contrast", "0"))
        return preset

    def _save_exact_presets(self) -> bool:
        """Atomically saves curated exact presets to exact_color_presets.json."""
        try:
            content = json.dumps(self.exact_presets, indent=2) + "\n"
            return self._atomic_write(self.exact_file, content)
        except (TypeError, ValueError):
            return False

    # =========================================================================
    # ATOMIC WRITES & STATE PERSISTENCE
    # =========================================================================

    @staticmethod
    def _atomic_write(path: Path, content: str) -> bool:
        """Zero-corruption atomic file write via tempfile and os.replace."""
        tmp_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", dir=str(path.parent), delete=False, encoding="utf-8") as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
            if path.exists():
                try:
                    st = path.stat()
                    os.chown(tmp_path, st.st_uid, st.st_gid)
                    tmp_path.chmod(st.st_mode)
                except OSError:
                    pass
            os.replace(tmp_path, path)
            return True
        except OSError as exc:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            print(f"[-] MatugenPresetsEngine: Atomic write failed for {path}: {exc}")
            return False

    def _save_state_file(self) -> bool:
        """
        Saves current state settings to state.conf, preserving existing comments,
        blank lines, and unmanaged external keys (e.g. LIGHT_WAL).
        """

        managed_map: dict[str, str] = {}
        for eng_key in (
            "mode",
            "type",
            "contrast",
            "index",
            "base16",
            "t_type",
            "t_dur",
            "t_fps",
            "t_bez",
            "t_ang",
            "t_pos",
        ):
            st_key = _ENGINE_TO_STATE_KEY.get(eng_key)
            if st_key:
                managed_map[st_key] = str(self.cache.get(eng_key, _DEFAULT_SETTINGS[eng_key]))

        last_hex = self.cache.get("last_applied_hex", _DEFAULT_SETTINGS["last_applied_hex"])
        managed_map["LAST_APPLIED_HEX"] = str(last_hex)
        managed_map["LAST_THEME_SOURCE"] = str(self.cache.get("theme_source", "wallpaper"))
        managed_map["LAST_VIVID_PRESET"] = str(self.cache.get("vivid_preset", "none"))

        written_keys: set[str] = set()
        new_lines: list[str] = []

        if self.state_file.is_file():
            try:
                for line in self.state_file.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#") and "=" in stripped:
                        k, _ = stripped.split("=", 1)
                        k = k.strip()
                        if k in managed_map:
                            v = managed_map[k]
                            if k == "LAST_APPLIED_HEX":
                                new_lines.append(f"{k}={v}")
                            else:
                                new_lines.append(f'{k}="{v}"')
                            written_keys.add(k)
                            continue
                    new_lines.append(line)
            except OSError:
                return False

        if not new_lines:
            new_lines.append("# Dusky Theme State File")

        # Append any managed keys that were not already present in the existing file
        for k, v in managed_map.items():
            if k not in written_keys:
                if k == "LAST_APPLIED_HEX":
                    new_lines.append(f"{k}={v}")
                else:
                    new_lines.append(f'{k}="{v}"')

        content = "\n".join(new_lines).rstrip() + "\n"
        return self._atomic_write(self.state_file, content)

    # =========================================================================
    # COLOR CONVERSIONS & HELPERS
    # =========================================================================

    @staticmethod
    def _hex_to_rgb_str(hex_val: str) -> str:
        clean = hex_val.strip().lstrip("#")
        if len(clean) == 6:
            try:
                r = int(clean[0:2], 16)
                g = int(clean[2:4], 16)
                b = int(clean[4:6], 16)
                return f"{r} {g} {b}"
            except ValueError:
                pass
        return "0 0 255"

    @staticmethod
    def _rgb_to_hex(r: int, g: int, b: int) -> str:
        r_c = max(0, min(255, r))
        g_c = max(0, min(255, g))
        b_c = max(0, min(255, b))
        return f"#{r_c:02X}{g_c:02X}{b_c:02X}"

    @staticmethod
    def _hex_to_hsl_str(hex_val: str) -> str:
        clean = hex_val.strip().lstrip("#")
        if len(clean) == 6:
            try:
                r = int(clean[0:2], 16) / 255.0
                g = int(clean[2:4], 16) / 255.0
                b = int(clean[4:6], 16) / 255.0
                h, l, s = colorsys.rgb_to_hls(r, g, b)
                return f"{int(round(h * 360))} {int(round(s * 100))}% {int(round(l * 100))}%"
            except (ValueError, ZeroDivisionError):
                pass
        return "240 100% 50%"

    def _parse_hsl_to_hex(self, hsl_str: str) -> str | None:
        """
        Converts HSL representation into #RRGGBB.
        Supports formats:
          - '340 100% 50%' or '340, 100%, 50%'
          - '340 100 50'
          - 'hsl(340, 100%, 50%)'
        """
        raw = hsl_str.strip()
        if raw.lower().startswith("hsl(") and raw.endswith(")"):
            raw = raw[4:-1].strip()

        match = _RE_HSL_PARSER.fullmatch(raw)
        if not match:
            return None

        try:
            h_str, s_str, l_str = match.groups()
            h_val = float(h_str)
            s_val = float(s_str.rstrip("%"))
            l_val = float(l_str.rstrip("%"))
            if not (0 <= h_val <= 360 and 0 <= s_val <= 100 and 0 <= l_val <= 100):
                return None
            s_val /= 100.0
            l_val /= 100.0
            h_norm = (h_val / 360.0) % 1.0

            r, g, b = colorsys.hls_to_rgb(h_norm, l_val, s_val)
            r_byte = int(round(r * 255))
            g_byte = int(round(g * 255))
            b_byte = int(round(b * 255))
            return f"#{r_byte:02X}{g_byte:02X}{b_byte:02X}"
        except (ValueError, TypeError):
            return None

    def get_palette_preview(
        self,
        hex_code: str,
        scheme: str = "scheme-tonal-spot",
        mode: str = "dark",
        contrast: str = "0",
    ) -> dict[str, str]:
        """
        Executes `matugen --dry-run --json hex` to compute
        Material You token mapping without modifying disk state or templates.
        """
        clean_hex = hex_code.strip().upper()
        if not clean_hex.startswith("#"):
            clean_hex = f"#{clean_hex}"
        if not _RE_HEX.fullmatch(clean_hex):
            return {}

        cmd = [
            "matugen",
            "--dry-run",
            "--json", "hex",
            "-m", mode,
            "-t", scheme,
        ]
        cmd.append(f"--contrast={contrast}")
        cmd.extend(["color", "hex", clean_hex])

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                data = json.loads(res.stdout)
                colors = data.get("colors", {})
                mode_key = "dark" if mode == "dark" else "light"
                preview: dict[str, str] = {}
                for token in (
                    "primary",
                    "on_primary",
                    "primary_container",
                    "on_primary_container",
                    "secondary",
                    "on_secondary",
                    "secondary_container",
                    "tertiary",
                    "on_tertiary",
                    "tertiary_container",
                    "surface",
                    "on_surface",
                    "surface_container",
                    "background",
                    "on_background",
                ):
                    entry = colors.get(token, {})
                    val = entry.get(mode_key, {}).get("color") or entry.get("default", {}).get("color")
                    if val:
                        preview[token] = val
                return preview
        except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
            pass
        return {}

    # =========================================================================
    # CORE MUTATOR & COLOR APPLICATION
    # =========================================================================

    def apply_color(
        self,
        hex_code: str,
        scheme_override: str | None = None,
        contrast_override: str | None = None,
        mode_override: str | None = None,
    ) -> tuple[bool, str]:
        """
        Invokes theme_ctl to generate solid background color and matugen templates.
        Optionally accepts hardcoded scheme/contrast overrides for exact color fidelity.
        """
        clean_hex = hex_code.strip().upper()
        if not clean_hex.startswith("#"):
            clean_hex = f"#{clean_hex}"

        if not _RE_HEX.fullmatch(clean_hex):
            return False, f"Invalid HEX code: '{hex_code}'"

        if not self.theme_ctl.is_file() or not os.access(self.theme_ctl, os.X_OK):
            return False, f"Theme controller not executable at: {self.theme_ctl}"

        # Resolve parameters to pass
        mode = mode_override or self.cache.get("mode", "dark")
        scheme = scheme_override or self.cache.get("type", "scheme-tonal-spot")
        contrast = contrast_override or self.cache.get("contrast", "0")
        index = self.cache.get("index", "0")
        base16 = self.cache.get("base16", "disable")
        t_type = self.cache.get("t_type", "random")
        t_dur = self.cache.get("t_dur", "2")
        t_fps = self.cache.get("t_fps", "60")
        t_bez = self.cache.get("t_bez", ".54,0,.34,.99")
        t_ang = self.cache.get("t_ang", "30")
        t_pos = self.cache.get("t_pos", "center")

        # 1. Update theme_ctl global state with the designated scheme
        set_cmd = [
            str(self.theme_ctl),
            "set",
            "--no-wall",
            "--no-regen",
            "--mode", str(mode),
            "--type", str(scheme),
            "--contrast", str(contrast),
            "--index", str(index),
            "--base16", str(base16),
            "--trans-type", str(t_type),
            "--trans-duration", str(t_dur),
            "--trans-fps", str(t_fps),
            "--trans-bezier", str(t_bez),
            "--trans-angle", str(t_ang),
            "--trans-pos", str(t_pos),
        ]

        try:
            res_set = subprocess.run(set_cmd, capture_output=True, text=True, timeout=15)
            if res_set.returncode != 0:
                err = res_set.stderr.strip() or "theme_ctl set failed"
                return False, f"Error caching settings: {err}"
        except (subprocess.SubprocessError, OSError) as exc:
            return False, f"Failed to execute theme_ctl set: {exc}"

        # 2. Apply the solid color
        color_cmd = [str(self.theme_ctl), "color", clean_hex]
        try:
            res_color = subprocess.run(color_cmd, capture_output=True, text=True, timeout=30)
            if res_color.returncode != 0:
                err = res_color.stderr.strip() or "theme_ctl color failed"
                return False, f"Color generation failed: {err}"
        except (subprocess.SubprocessError, OSError) as exc:
            return False, f"Failed to execute theme_ctl color: {exc}"

        # 3. Synchronize cache and state.conf
        self.cache["last_applied_hex"] = clean_hex
        self.cache["DEFAULT/last_applied_hex"] = clean_hex
        self.cache["exact/active_color"] = clean_hex
        self.cache["active_color"] = clean_hex
        self.cache["DEFAULT/active_color"] = clean_hex
        self.cache["custom_hex"] = clean_hex
        self.cache["DEFAULT/custom_hex"] = clean_hex
        self.cache["custom_rgb"] = self._hex_to_rgb_str(clean_hex)
        self.cache["DEFAULT/custom_rgb"] = self.cache["custom_rgb"]
        self.cache["custom_hsl"] = self._hex_to_hsl_str(clean_hex)
        self.cache["DEFAULT/custom_hsl"] = self.cache["custom_hsl"]

        if mode_override:
            self.cache["mode"] = mode_override
            self.cache["DEFAULT/mode"] = mode_override
        if scheme_override:
            self.cache["type"] = scheme_override
            self.cache["DEFAULT/type"] = scheme_override
        if contrast_override:
            self.cache["contrast"] = contrast_override
            self.cache["DEFAULT/contrast"] = contrast_override

        self.cache["theme_source"] = "custom"
        self.cache["DEFAULT/theme_source"] = "custom"
        self.cache["vivid_preset"] = "none"
        self.cache["DEFAULT/vivid_preset"] = "none"
        self.cache["vivid_active_id"] = self.cache["DEFAULT/vivid_active_id"] = "none"
        self.cache["vivid_active_hex"] = self.cache["DEFAULT/vivid_active_hex"] = clean_hex

        try:
            (self.state_dir / "vivid_resume.json").unlink(missing_ok=True)
        except OSError as exc:
            return False, f"Color applied, but clearing Vivid resume state failed: {exc}"
        if not self._save_state_file():
            return False, "Color applied, but saving theme state failed"

        info_scheme = f" [{scheme}]" if scheme_override else ""
        return True, f"Applied: {clean_hex}{info_scheme}"

    def apply_settings(self) -> tuple[bool, str]:
        """Applies currently cached Theme & Animation settings via theme_ctl."""
        # Generating from a Vivid accent intentionally leaves the baked pack.
        # Use the color path so pack provenance and resume state are cleared too.
        if self.cache.get("theme_source") == "vivid":
            return self.apply_color(str(self.cache.get("last_applied_hex", "#0000FF")))
        if not self.theme_ctl.is_file() or not os.access(self.theme_ctl, os.X_OK):
            return False, f"Theme controller not executable at: {self.theme_ctl}"

        set_cmd = [
            str(self.theme_ctl),
            "set",
            "--no-wall",
            "--mode", str(self.cache.get("mode", "dark")),
            "--type", str(self.cache.get("type", "scheme-tonal-spot")),
            "--contrast", str(self.cache.get("contrast", "0")),
            "--index", str(self.cache.get("index", "0")),
            "--base16", str(self.cache.get("base16", "disable")),
            "--trans-type", str(self.cache.get("t_type", "random")),
            "--trans-duration", str(self.cache.get("t_dur", "2")),
            "--trans-fps", str(self.cache.get("t_fps", "60")),
            "--trans-bezier", str(self.cache.get("t_bez", ".54,0,.34,.99")),
            "--trans-angle", str(self.cache.get("t_ang", "30")),
            "--trans-pos", str(self.cache.get("t_pos", "center")),
        ]

        try:
            res = subprocess.run(set_cmd, capture_output=True, text=True, timeout=20)
            if res.returncode == 0:
                if not self._save_state_file():
                    return False, "Settings applied, but saving theme state failed"
                return True, "Settings Applied & Saved successfully"
            err = res.stderr.strip() or "Failed to apply settings"
            return False, f"Error: {err}"
        except (subprocess.SubprocessError, OSError) as exc:
            return False, f"Execution failed: {exc}"

    def add_favorite(self, hex_code: str, label: str | None = None) -> tuple[bool, str]:
        """Adds a hex color to favorites list and persists to disk."""
        clean_hex = hex_code.strip().upper()
        if not clean_hex.startswith("#"):
            clean_hex = f"#{clean_hex}"
        if not _RE_HEX.fullmatch(clean_hex):
            return False, f"Invalid HEX code: '{hex_code}'"

        if any(h == clean_hex for _, h in self.favorites):
            return False, f"Already in favorites: {clean_hex}"

        fav_label = (label or f"Color {clean_hex}").strip()
        if not fav_label or any(char in fav_label for char in "|\n\r") or fav_label.startswith("#"):
            return False, "Favorite label must be nonempty and contain no pipes or line breaks"
        self.favorites.append((fav_label, clean_hex))
        if self._save_favorites():
            self._update_favorite_count()
            return True, f"♥ Added {fav_label} ({clean_hex}) to favorites"
        self.favorites.pop()
        return False, "Failed to write favorites file"

    def remove_favorite(self, hex_or_label: str) -> tuple[bool, str]:
        """Removes a favorite by hex code or label."""
        norm = hex_or_label.strip().upper()
        if _RE_HEX.fullmatch(norm) and not norm.startswith("#"):
            norm = f"#{norm}"
        original = self.favorites
        orig_len = len(self.favorites)
        self.favorites = [
            (lbl, hx) for (lbl, hx) in self.favorites
            if hx != norm and lbl != hex_or_label.strip()
        ]
        if len(self.favorites) < orig_len:
            if not self._save_favorites():
                self.favorites = original
                return False, "Failed to write favorites file"
            self._update_favorite_count()
            return True, f"Removed {hex_or_label} from favorites"
        return False, f"{hex_or_label} was not found in favorites"

    def add_exact_preset(
        self,
        hex_code: str,
        scheme: str = "scheme-fidelity",
        label: str | None = None,
        contrast: str = "0",
        mode: str = "dark",
        desc: str | None = None,
    ) -> tuple[bool, str]:
        """Adds a new curated or custom exact preset to exact_color_presets.json."""
        clean_hex = hex_code.strip().upper()
        if not clean_hex.startswith("#"):
            clean_hex = f"#{clean_hex}"
        if not _RE_HEX.fullmatch(clean_hex):
            return False, f"Invalid HEX code: '{hex_code}'"

        slug = clean_hex.lstrip("#").lower()
        preset_id = f"custom_{len(self.exact_presets) + 1}_{slug}"
        idx = 1
        while any(p.get("id") == preset_id for p in self.exact_presets):
            preset_id = f"custom_{len(self.exact_presets) + idx}_{slug}"
            idx += 1

        entry = {
            "id": preset_id,
            "label": label or f"Custom {clean_hex}",
            "hex": clean_hex,
            "scheme": scheme,
            "contrast": contrast,
            "mode": mode,
            "desc": desc or f"Hardcoded preset paired with {scheme}.",
        }
        try:
            entry = self._normalize_recipe(entry)
        except ValueError as exc:
            return False, str(exc)
        self.exact_presets.append(entry)
        if self._save_exact_presets():
            return True, preset_id
        self.exact_presets.pop()
        return False, "Failed to save exact presets file."

    def delete_exact_preset(self, preset_id: str) -> bool:
        """Deletes an exact preset by ID and saves to disk."""
        original = self.exact_presets
        orig_len = len(original)
        self.exact_presets = [p for p in self.exact_presets if p.get("id") != preset_id]
        if len(self.exact_presets) < orig_len:
            if self._save_exact_presets():
                return True
            self.exact_presets = original
        return False

    # =========================================================================
    # VIVID PRESETS IMPLEMENTATION
    # =========================================================================

    def list_vivid_presets(self) -> list[dict[str, Any]]:
        """Returns the list of pre-baked vivid color presets from catalog."""
        return vivid_theme.list_vivid_presets()

    def apply_vivid(self, preset_id: str) -> tuple[bool, str, str]:
        """Applies a pre-baked vivid pack instantly via atomic copies and toolkit hooks."""
        ok, msg, hex_code = vivid_theme.apply_vivid(preset_id)
        if ok:
            self.load_state()
        return ok, msg, hex_code

    def resume_wallpaper(self) -> tuple[bool, str, str]:
        """Restores desktop back to dynamic wallpaper theming."""
        ok, msg, hex_code = vivid_theme.resume_wallpaper()
        if ok:
            self.load_state()
        return ok, msg, hex_code

    # =========================================================================
    # WRITE VALUE & BATCH IMPLEMENTATION
    # =========================================================================

    @override
    def write_value(
        self,
        target_key: str,
        target_scope: str,
        new_value: str,
        item_type: str = "string"
    ) -> WriteResult:
        """Dispatches mutations from Dusky TUI or headless CLI."""
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    @override
    def write_batch(self, changes: list[ChangeTuple]) -> WriteResult:
        """Apply changes in order, stopping at the first failure (no rollback)."""
        if not self.cache:
            self.load_state()
        status_msg = ""

        for target_key, _target_scope, new_val_raw, item_type in changes:
            val_str = str(new_val_raw).strip()
            val_lower = val_str.lower()
            # Momentary actions must remain inert during profile/reset false writes.
            if item_type == "bool" and val_lower not in ("true", "1"):
                continue

            # 1. Direct active color mutation
            if target_key in ("last_applied_hex", "active_color"):
                ok, msg = self.apply_color(val_str)
                if not ok:
                    return False, msg, ""
                status_msg = msg

            # Vivid Preset triggers
            elif target_key.startswith("vivid_apply_"):
                if val_lower in ("true", "1"):
                    preset_id = target_key.removeprefix("vivid_apply_")
                    ok, msg, hex_code = self.apply_vivid(preset_id)
                    if not ok:
                        return False, msg, ""
                    status_msg = msg

            # Return to wallpaper trigger
            elif target_key == "action_resume_wallpaper":
                if val_lower in ("true", "1"):
                    ok, msg, hex_code = self.resume_wallpaper()
                    if not ok:
                        return False, msg, ""
                    status_msg = msg

            # 2. Palette preset color triggers (e.g. color_vib_hyper_red)
            elif target_key.startswith("color_"):
                # Extract hex from the key suffix or options in schema
                # Format: color_<tab>_<name>__<hex_without_hash>
                if "__" in target_key:
                    hex_part = "#" + target_key.split("__")[-1]
                    ok, msg = self.apply_color(hex_part)
                    if not ok:
                        return False, msg, ""
                    status_msg = msg
                elif _RE_HEX.fullmatch(val_str):
                    ok, msg = self.apply_color(val_str)
                    if not ok:
                        return False, msg, ""
                    status_msg = msg

            # 3. Exact / Hardcoded schema preset triggers
            elif target_key.startswith("exact_preset_"):
                preset_id = target_key.removeprefix("exact_preset_")
                matched = next((p for p in self.exact_presets if p["id"] == preset_id), None)
                if matched:
                    # Check global strategy override
                    chosen_scheme = (
                        self.exact_scheme_override
                        if self.exact_scheme_override != "per-preset"
                        else matched.get("scheme", "scheme-fidelity")
                    )
                    chosen_contrast = matched.get("contrast", "0")
                    chosen_mode = matched.get("mode", "dark")
                    ok, msg = self.apply_color(
                        matched["hex"],
                        scheme_override=chosen_scheme,
                        contrast_override=chosen_contrast,
                        mode_override=chosen_mode,
                    )
                    if not ok:
                        return False, msg, ""
                    status_msg = f"Applied Exact {matched['label']} [{chosen_scheme}]: {matched['hex']}"
                else:
                    return False, f"Exact preset '{preset_id}' not found.", ""

            # 4. Exact strategy switch
            elif target_key == "exact_scheme_override":
                if val_str != "per-preset" and val_str not in VALID_SCHEMES:
                    return False, f"Invalid scheme: {val_str}", ""
                self.exact_scheme_override = val_str
                self.cache["exact_scheme_override"] = val_str
                self.cache["DEFAULT/exact_scheme_override"] = val_str
                status_msg = f"Exact scheme strategy set to: {val_str}"

            # 5. Add custom exact preset
            elif target_key == "new_exact_hex":
                if _RE_HEX.fullmatch(val_str):
                    hex_norm = val_str.upper()
                    if not hex_norm.startswith("#"):
                        hex_norm = f"#{hex_norm}"
                    self.new_exact_hex = hex_norm
                    self.cache["new_exact_hex"] = hex_norm
                    self.cache["DEFAULT/new_exact_hex"] = hex_norm
                    status_msg = f"Custom exact hex staged: {hex_norm}"
                else:
                    return False, f"Invalid HEX code: {val_str}", ""

            elif target_key == "new_exact_scheme":
                if val_str not in VALID_SCHEMES:
                    return False, f"Invalid scheme: {val_str}", ""
                self.new_exact_scheme = val_str
                self.cache["new_exact_scheme"] = val_str
                self.cache["DEFAULT/new_exact_scheme"] = val_str
                status_msg = f"Custom exact scheme staged: {val_str}"

            elif target_key == "action_add_exact_preset" and val_lower in ("true", "1"):
                ok, res = self.add_exact_preset(self.new_exact_hex, self.new_exact_scheme)
                if ok:
                    status_msg = f"Saved hardcoded preset: {self.new_exact_hex} [{self.new_exact_scheme}]"
                else:
                    return False, f"Failed to save preset: {res}", ""

            elif target_key.startswith("action_del_exact_"):
                preset_id = target_key.removeprefix("action_del_exact_")
                if self.delete_exact_preset(preset_id):
                    status_msg = f"Deleted hardcoded preset: {preset_id}"
                else:
                    return False, f"Exact preset '{preset_id}' not found.", ""

            # 6. Custom Input: HEX
            elif target_key == "custom_hex":
                if _RE_HEX.fullmatch(val_str):
                    ok, msg = self.apply_color(val_str)
                    if not ok:
                        return False, msg, ""
                    status_msg = f"Applied Custom HEX: {self.cache['last_applied_hex']}"
                else:
                    return False, f"Invalid HEX format: '{val_str}'", ""

            # 7. Custom Input: RGB
            elif target_key == "custom_rgb":
                match = _RE_RGB_PARSER.fullmatch(val_str)
                if match:
                    r, g, b = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
                    if 0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255:
                        hex_val = self._rgb_to_hex(r, g, b)
                        ok, msg = self.apply_color(hex_val)
                        if not ok:
                            return False, msg, ""
                        status_msg = f"Applied RGB ({r}, {g}, {b}) -> {hex_val}"
                    else:
                        return False, "RGB values must be between 0 and 255.", ""
                else:
                    return False, "RGB format must be 'R G B' (e.g. 255 0 128)", ""

            # 8. Custom Input: HSL
            elif target_key == "custom_hsl":
                hex_val = self._parse_hsl_to_hex(val_str)
                if hex_val:
                    ok, msg = self.apply_color(hex_val)
                    if not ok:
                        return False, msg, ""
                    status_msg = f"Applied HSL ({val_str}) -> {hex_val}"
                else:
                    return False, f"Invalid HSL format: '{val_str}' (expected 'H S L', e.g. '340 100% 50%')", ""

            # 9. Custom Input: Regenerate Last
            elif target_key == "action_regen" and val_lower in ("true", "1"):
                last_hex = self.cache.get("last_applied_hex", "#0000FF")
                ok, msg = self.apply_color(last_hex)
                if not ok:
                    return False, msg, ""
                status_msg = f"Regenerated: {last_hex}"

            # 10. Apply Settings triggers
            elif target_key == "action_apply_anim" and val_lower in ("true", "1"):
                # awww transition options are consumed on the next wallpaper change.
                # Running theme_ctl set with --mode here would also regenerate colors.
                if not self._save_state_file():
                    return False, "Failed to save animation settings", ""
                status_msg = "Animation settings saved for the next wallpaper transition"

            elif target_key in ("action_apply_theme", "action_apply_settings"):
                if val_lower in ("true", "1"):
                    ok, msg = self.apply_settings()
                    if not ok:
                        return False, msg, ""
                    status_msg = msg

            # 11. Favorites Management
            elif target_key == "action_add_favorite" and val_lower in ("true", "1"):
                ok, msg = self.add_active_favorite()
                if not ok:
                    return False, msg, ""
                status_msg = msg

            elif target_key == "action_remove_favorite" and val_lower in ("true", "1"):
                ok, msg = self.remove_active_favorite()
                if not ok:
                    return False, msg, ""
                status_msg = msg

            elif target_key.startswith("fav_vivid_apply_"):
                preset_id = target_key.removeprefix("fav_vivid_apply_")
                if not any(p["id"] == preset_id for p in self.vivid_favorites):
                    return False, f"Vivid favorite '{preset_id}' no longer exists", ""
                ok, msg, _ = self.apply_vivid(preset_id)
                if not ok:
                    return False, msg, ""
                status_msg = msg

            elif target_key.startswith("fav_vivid_remove_"):
                preset_id = target_key.removeprefix("fav_vivid_remove_")
                ok, msg = self.remove_vivid_favorite(preset_id)
                if not ok:
                    return False, msg, ""
                status_msg = msg

            elif target_key.startswith(("fav_apply_", "fav_remove_")):
                apply = target_key.startswith("fav_apply_")
                suffix = target_key.removeprefix("fav_apply_" if apply else "fav_remove_")
                try:
                    favorite_index = int(suffix)
                except ValueError:
                    return False, f"Invalid favorite index: {suffix}", ""
                if not 0 <= favorite_index < len(self.favorites):
                    return False, "Favorite no longer exists; refresh the list", ""
                label, hex_code = self.favorites[favorite_index]
                ok, msg = self.apply_color(hex_code) if apply else self.remove_favorite(hex_code)
                if not ok:
                    return False, msg, ""
                status_msg = f"Applied Favorite: {label} ({hex_code})" if apply else msg

            # 11. Settings updates (mode, type, contrast, etc.)
            elif target_key in _STATE_KEY_TO_ENGINE.values():
                norm_val = val_str
                if target_key == "contrast" and norm_val == "0.0":
                    norm_val = "0"
                self.cache[target_key] = norm_val
                self.cache[f"DEFAULT/{target_key}"] = norm_val
                if not self._save_state_file():
                    return False, "Failed to save theme settings", ""
                status_msg = f"Set {target_key} = {norm_val}"

        if not status_msg:
            status_msg = "Settings updated successfully."

        return True, status_msg, ""
