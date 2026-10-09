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
lazy import colorsys
lazy import copy
lazy import json
lazy import shutil
lazy import subprocess
lazy import tempfile
lazy from pathlib import Path
lazy from typing import Any, override

from python.frontend.core_types import BaseEngine

# PEP 695 Type Aliases
type ScopeKeyMap = dict[str, Any]
type ChangeTuple = tuple[str, str, str, str]
type WriteResult = tuple[bool, str, str]

# Regex patterns
_RE_HEX = re.compile(r"^#?[a-fA-F0-9]{6}$")
_RE_RGB_PARSER = re.compile(r"^(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})$")
_RE_HSL_PARSER = re.compile(r"^(\d+(?:\.\d+)?)(?:deg)?[\s,]+(\d+(?:\.\d+)?%?)[\s,]+(\d+(?:\.\d+)?%?)$")

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
})

_ENGINE_TO_STATE_KEY = frozendict({v: k for k, v in _STATE_KEY_TO_ENGINE.items()})

# Default curated exact presets guaranteeing vivid, true-to-palette generation
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

        # Theme controller script path
        user_scripts = Path.home() / "user_scripts"
        self.theme_ctl = user_scripts / "theme_matugen" / "theme_ctl.sh"

        self.cache: dict[str, Any] = {}
        self.favorites: list[tuple[str, str]] = []
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
        self.cache["fav_count"] = len(self.favorites)

        # 3. Parse exact presets
        self._load_exact_presets()
        self.cache["exact_scheme_override"] = self.exact_scheme_override
        self.cache["new_exact_hex"] = self.new_exact_hex
        self.cache["new_exact_scheme"] = self.new_exact_scheme

        # 4. Helper aliases & controls
        last_hex = str(self.cache.get("last_applied_hex", "#0000FF")).upper()
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
        ):
            self.cache[act] = False

        # Expose DEFAULT/ scoped keys for Dusky TUI router compatibility
        scoped = {f"DEFAULT/{k}": v for k, v in self.cache.items()}
        self.cache.update(scoped)

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
                if label and _RE_HEX.match(hex_val):
                    if not hex_val.startswith("#"):
                        hex_val = f"#{hex_val}"
                    self.favorites.append((label, hex_val))
        except OSError:
            pass

    def _save_favorites(self) -> bool:
        """Atomically saves favorites list to theme_preset_fav."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Dusky Matugen Favorites",
            "# Format: Label|#HEXCODE",
        ]
        for label, hex_val in self.favorites:
            lines.append(f"{label}|{hex_val}")
        content = "\n".join(lines) + "\n"
        return self._atomic_write(self.favorites_file, content)

    def _load_exact_presets(self) -> None:
        """Loads curated hardcoded presets from exact_color_presets.json."""
        if not self.exact_file.is_file():
            # Seed defaults
            self.exact_presets = [dict(p) for p in _DEFAULT_EXACT_PRESETS]
            self._save_exact_presets()
            return
        try:
            raw = json.loads(self.exact_file.read_text(encoding="utf-8"))
            if isinstance(raw, list) and raw:
                self.exact_presets = raw
            else:
                self.exact_presets = [dict(p) for p in _DEFAULT_EXACT_PRESETS]
        except (OSError, json.JSONDecodeError):
            self.exact_presets = [dict(p) for p in _DEFAULT_EXACT_PRESETS]

    def _save_exact_presets(self) -> bool:
        """Atomically saves curated exact presets to exact_color_presets.json."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
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
        self.state_dir.mkdir(parents=True, exist_ok=True)

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
                pass

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

        match = _RE_HSL_PARSER.match(raw)
        if not match:
            return None

        try:
            h_str, s_str, l_str = match.groups()
            h_val = float(h_str) % 360.0
            s_val = float(s_str.rstrip("%"))
            l_val = float(l_str.rstrip("%"))

            if s_val > 1.0:
                s_val /= 100.0
            if l_val > 1.0:
                l_val /= 100.0

            s_val = max(0.0, min(1.0, s_val))
            l_val = max(0.0, min(1.0, l_val))
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
        Executes `matugen --dry-run --json hex` in-memory (~8ms) to compute
        Material You token mapping without modifying disk state or templates.
        """
        clean_hex = hex_code.strip().upper()
        if not clean_hex.startswith("#"):
            clean_hex = f"#{clean_hex}"
        if not _RE_HEX.match(clean_hex):
            return {}

        cmd = [
            "matugen",
            "--dry-run",
            "--json", "hex",
            "-m", mode,
            "-t", scheme,
        ]
        if contrast and contrast != "0":
            cmd.extend(["-c", contrast])
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

        if not _RE_HEX.match(clean_hex):
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
        self.cache["active_color"] = clean_hex
        self.cache["DEFAULT/active_color"] = clean_hex
        self.cache["custom_hex"] = clean_hex
        self.cache["DEFAULT/custom_hex"] = clean_hex
        self.cache["custom_rgb"] = self._hex_to_rgb_str(clean_hex)
        self.cache["DEFAULT/custom_rgb"] = self.cache["custom_rgb"]
        self.cache["custom_hsl"] = self._hex_to_hsl_str(clean_hex)
        self.cache["DEFAULT/custom_hsl"] = self.cache["custom_hsl"]

        if scheme_override:
            self.cache["type"] = scheme_override
            self.cache["DEFAULT/type"] = scheme_override
        if contrast_override:
            self.cache["contrast"] = contrast_override
            self.cache["DEFAULT/contrast"] = contrast_override

        self._save_state_file()

        info_scheme = f" [{scheme}]" if scheme_override else ""
        return True, f"Applied: {clean_hex}{info_scheme}"

    def apply_settings(self) -> tuple[bool, str]:
        """Applies currently cached Theme & Animation settings via theme_ctl."""
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
                self._save_state_file()
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
        if not _RE_HEX.match(clean_hex):
            return False, f"Invalid HEX code: '{hex_code}'"

        if any(h == clean_hex for _, h in self.favorites):
            return False, f"Already in favorites: {clean_hex}"

        fav_label = label or f"Color {clean_hex}"
        self.favorites.append((fav_label, clean_hex))
        if self._save_favorites():
            self.cache["fav_count"] = len(self.favorites)
            self.cache["DEFAULT/fav_count"] = len(self.favorites)
            return True, f"♥ Added {fav_label} ({clean_hex}) to favorites"
        return False, "Failed to write favorites file"

    def remove_favorite(self, hex_or_label: str) -> tuple[bool, str]:
        """Removes a favorite by hex code or label."""
        norm = hex_or_label.strip().upper()
        orig_len = len(self.favorites)
        self.favorites = [
            (lbl, hx) for (lbl, hx) in self.favorites
            if hx != norm and lbl != hex_or_label.strip()
        ]
        if len(self.favorites) < orig_len:
            self._save_favorites()
            self.cache["fav_count"] = len(self.favorites)
            self.cache["DEFAULT/fav_count"] = len(self.favorites)
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
        if not _RE_HEX.match(clean_hex):
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
        self.exact_presets.append(entry)
        if self._save_exact_presets():
            return True, preset_id
        return False, "Failed to save exact presets file."

    def delete_exact_preset(self, preset_id: str) -> bool:
        """Deletes an exact preset by ID and saves to disk."""
        orig_len = len(self.exact_presets)
        self.exact_presets = [p for p in self.exact_presets if p.get("id") != preset_id]
        if len(self.exact_presets) < orig_len:
            self._save_exact_presets()
            return True
        return False

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
        """Processes atomic batch changes."""
        if not self.cache:
            self.load_state()
        status_msg = ""
        success = True

        for target_key, _target_scope, new_val_raw, item_type in changes:
            val_str = str(new_val_raw).strip()
            val_lower = val_str.lower()

            # 1. Direct active color mutation
            if target_key in ("last_applied_hex", "active_color"):
                ok, msg = self.apply_color(val_str)
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
                elif _RE_HEX.match(val_str):
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
                self.exact_scheme_override = val_str
                self.cache["exact_scheme_override"] = val_str
                self.cache["DEFAULT/exact_scheme_override"] = val_str
                status_msg = f"Exact scheme strategy set to: {val_str}"

            # 5. Add custom exact preset
            elif target_key == "new_exact_hex":
                if _RE_HEX.match(val_str):
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
                if _RE_HEX.match(val_str):
                    ok, msg = self.apply_color(val_str)
                    if not ok:
                        return False, msg, ""
                    status_msg = f"Applied Custom HEX: {self.cache['last_applied_hex']}"
                else:
                    return False, f"Invalid HEX format: '{val_str}'", ""

            # 7. Custom Input: RGB
            elif target_key == "custom_rgb":
                match = _RE_RGB_PARSER.match(val_str)
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
            elif target_key in ("action_apply_theme", "action_apply_anim", "action_apply_settings"):
                if val_lower in ("true", "1"):
                    ok, msg = self.apply_settings()
                    if not ok:
                        return False, msg, ""
                    status_msg = msg

            # 11. Favorites Management
            elif target_key == "action_add_favorite" and val_lower in ("true", "1"):
                curr = self.cache.get("last_applied_hex", "#0000FF")
                ok, msg = self.add_favorite(curr)
                status_msg = msg

            elif target_key == "action_remove_favorite" and val_lower in ("true", "1"):
                curr = self.cache.get("last_applied_hex", "#0000FF")
                ok, msg = self.remove_favorite(curr)
                status_msg = msg

            elif target_key.startswith("fav_apply_"):
                # Apply favorite
                fav_idx_str = target_key.removeprefix("fav_apply_")
                try:
                    fav_idx = int(fav_idx_str)
                    if 0 <= fav_idx < len(self.favorites):
                        lbl, hx = self.favorites[fav_idx]
                        ok, msg = self.apply_color(hx)
                        if not ok:
                            return False, msg, ""
                        status_msg = f"Applied Favorite: {lbl} ({hx})"
                except ValueError:
                    if _RE_HEX.match(val_str):
                        ok, msg = self.apply_color(val_str)
                        if not ok:
                            return False, msg, ""
                        status_msg = msg

            elif target_key.startswith("fav_remove_"):
                fav_idx_str = target_key.removeprefix("fav_remove_")
                try:
                    fav_idx = int(fav_idx_str)
                    if 0 <= fav_idx < len(self.favorites):
                        lbl, hx = self.favorites.pop(fav_idx)
                        self._save_favorites()
                        status_msg = f"Removed favorite: {lbl} ({hx})"
                except ValueError:
                    pass

            # 11. Settings updates (mode, type, contrast, etc.)
            elif target_key in _STATE_KEY_TO_ENGINE.values():
                norm_val = val_str
                if target_key == "contrast" and norm_val == "0.0":
                    norm_val = "0"
                self.cache[target_key] = norm_val
                self.cache[f"DEFAULT/{target_key}"] = norm_val
                self._save_state_file()
                status_msg = f"Set {target_key} = {norm_val}"

        if not status_msg:
            status_msg = "Settings updated successfully."

        return success, status_msg, ""
