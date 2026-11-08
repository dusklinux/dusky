#!/usr/bin/env python3
"""
Hyprland Screen Shader Engine for Dusky TUI.

Provides shader discovery and categorization, mutually exclusive boolean toggles,
coalesced non-blocking live preview on scroll, flock session locking, rendering
policy management (damage_tracking & VFR), state/memory compatibility with
shader_menu.sh, and automatic rollback of uncommitted previews on exit.
"""

from __future__ import annotations

import atexit
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import types
from pathlib import Path
from typing import Any

from python.frontend.core_types import BaseEngine, ConfigItem

GROUP_ORDER: dict[int, dict[str, int]] = {
    1: {"Tints & Night Light": 0, "Contrast & Palette": 1, "Accessibility": 2},
    2: {"CRT & Scanlines": 0, "Pixel & Glitch": 1, "Lens & Blur": 2},
    3: {"Gentle Pointer": 0, "Vortex, Prism & Portal": 1, "Click Reactive": 2},
    4: {"Floating Glass & Atmosphere": 0, "Water & Curl Fields": 1},
    5: {"Gravity & Tunnels": 0, "Shatter, Melt & Glitch": 1, "Reflections & Mirrors": 2},
}

# Compact catalog: stem -> (tab, group, summary)
SHADER_CATALOG: dict[str, tuple[int, str, str]] = {
    # Tab 1: Color & Tone (01..15)
    "night_light_kelvin": (1, "Tints & Night Light", "Full-color 3400K circadian blackbody filter with luminance preservation."),
    "amber_phosphor": (1, "Tints & Night Light", "Vintage P3 amber CRT terminal phosphor curve with warm cream highlights."),
    "red_tint": (1, "Tints & Night Light", "Weighted Rec.709 grayscale placed in the red channel."),
    "green_tint": (1, "Tints & Night Light", "Weighted Rec.709 grayscale placed in the green channel."),
    "blue_tint": (1, "Tints & Night Light", "Weighted Rec.709 grayscale placed in the blue channel."),
    "cyan_matrix_tint": (1, "Tints & Night Light", "Bioluminescent cyan-teal spectrum grade with deep navy shadows."),
    "violet_synth_tint": (1, "Tints & Night Light", "Outrun ultraviolet-to-neon-magenta duotone spectrum tint."),
    "sunset_gold_tint": (1, "Tints & Night Light", "Golden-hour ruby-shadow to warm solar-gold split tone."),
    "emerald_forest_tint": (1, "Tints & Night Light", "Low-fatigue 555nm photopic emerald and jade color grade."),
    "sepia": (1, "Tints & Night Light", "Classic warm archival sepia toning matrix."),
    "grayscale": (1, "Contrast & Palette", "Luminance-preserving Rec.709 neutral monochrome grayscale."),
    "invert": (1, "Contrast & Palette", "Premultiplied-safe RGB channel inversion."),
    "posterization": (1, "Contrast & Palette", "4x4 Bayer-dithered discrete tonal band quantization."),
    "vibrance": (1, "Contrast & Palette", "Gamut-safe adaptive saturation boost for muted colors."),
    "saturation_contrast": (1, "Contrast & Palette", "Direct luminance saturation and working-space midpoint contrast."),
    # Tab 2: Retro & Optics (16..22)
    "crt": (2, "CRT & Scanlines", "Curved cathode-ray bezel with scanlines, vignette, and radial RGB split."),
    "pixelated": (2, "Pixel & Glitch", "Integer block mosaic using exact base-level texelFetch."),
    "newspaper": (2, "Pixel & Glitch", "Bayer-dithered monochrome newsprint halftone dot screen."),
    "sketch": (2, "Pixel & Glitch", "Adaptive 9-tap Sobel pencil contour sketch on grained paper."),
    "chromatic_aberration": (2, "Pixel & Glitch", "Quadratic radial red/blue channel fringing toward screen edges."),
    "anaglyph_offset": (2, "Pixel & Glitch", "Single-image red/cyan horizontal stereo offset with edge boost."),
    "vignette": (2, "Lens & Blur", "Smooth circular peripheral luminance falloff framing the viewport."),
    # Tab 3: Cursor & Click (23..33)
    "cursor_spotlight": (3, "Gentle Pointer", "Soft ambient desktop dimmer with a feathered spotlight tracking the pointer."),
    "cursor_ripple": (3, "Gentle Pointer", "Local water ripples around the pointer that settle after movement stops."),
    "cursor_lens": (3, "Gentle Pointer", "Clear optical glass magnifier (~1.4x zoom at center) tracking the pointer."),
    "cursor_jelly": (3, "Gentle Pointer", "Soft cross-wave elastic wobble that settles smoothly when the pointer rests."),
    "cursor_vortex": (3, "Vortex, Prism & Portal", "Breathing whirlpool centered on the cursor; movement strengthens the twist."),
    "cursor_prism": (3, "Vortex, Prism & Portal", "Magnifying bubble around the pointer with rotating RGB color dispersion."),
    "cursor_kaleidoscope": (3, "Vortex, Prism & Portal", "Mirrored crystal sector portal around the cursor with a clear aiming eye."),
    "cursor_ferrofluid": (3, "Vortex, Prism & Portal", "Glossy magnetic ferrofluid lens whose perimeter bristles with spikes on movement."),
    "click_shockwaves": (3, "Click Reactive", "Expanding concentric wave packets triggered by the six most recent clicks."),
    "interactive_bubble_pop": (3, "Click Reactive", "Buoyant iridescent bubbles that deflect off the cursor and pop on click or poke."),
    "click_chromatic_pulse": (3, "Click Reactive", "High-velocity RGB prism shock-rings launched from recent click positions."),
    "glass_billiards": (3, "Click Reactive", "Interactive thick-glass billiard spheres that bounce, collide, and scatter on cursor or click."),
    # Tab 4: Ambient & Fluid (34..44)
    "forest_canopy_komorebi": (4, "Floating Glass & Atmosphere", "Dappled wind-swayed tree leaf shadows and warm diagonal sunbeams."),
    "raindrops_on_glass": (4, "Floating Glass & Atmosphere", "Refractive trickling raindrops, wake trails, and condensation micro-beads."),
    "roaming_glass": (4, "Floating Glass & Atmosphere", "Two broad refractive glass bubbles wandering on closed Lissajous orbits."),
    "heat_haze": (4, "Floating Glass & Atmosphere", "Fine rising thermal shimmer modulated by a slow atmospheric wave envelope."),
    "aurora_veil": (4, "Floating Glass & Atmosphere", "Undulating translucent curtains of emerald, cyan, and violet aurora light."),
    "wobble": (4, "Water & Curl Fields", "Multi-harmonic organic spatial wave with protected screen borders."),
    "ripple": (4, "Water & Curl Fields", "Continuous concentric surface water ripples radiating from screen center."),
    "soft_warp": (4, "Water & Curl Fields", "Gentle continuous spatial drift driven by quintic value noise."),
    "water_interference": (4, "Water & Curl Fields", "Overlapping constructive and destructive ripples from two moving sources."),
    "liquid_marble": (4, "Water & Curl Fields", "Smooth swirling fluid eddies driven by a dual analytic curl field."),
    "prismatic_tide": (4, "Water & Curl Fields", "Broad rolling ocean swells combined with spectral RGB crest dispersion."),
    # Tab 5: Extreme & Surreal (45..52)
    "cursor_black_hole": (5, "Gravity & Tunnels", "Strong gravitational lensing twist, dark event-horizon core, and spectral rim."),
    "wormhole": (5, "Gravity & Tunnels", "Folds the entire desktop into a spiraling tunnel with moving walls."),
    "cursor_reality_tear": (5, "Gravity & Tunnels", "Bending vertical spatial rift centered on the pointer that pulls the desktop apart."),
    "fractured_glass": (5, "Shatter, Melt & Glitch", "Triangular glass shards slide and refract independently along geometric cracks."),
    "glitch_storm": (5, "Shatter, Melt & Glitch", "Bursts of displaced horizontal scan bands and separated RGB channels on 12 Hz ticks."),
    "digital_melt": (5, "Shatter, Melt & Glitch", "Columns of desktop pixels liquefy, sink, and recover independently."),
    "orbital_spheres": (5, "Reflections & Mirrors", "Three orbiting refractive glass spheres carrying inverted desktop reflections."),
    "mirror_dimension": (5, "Reflections & Mirrors", "Full-screen mirrored desktop tiles rotate, breathe, and bend across the display."),
}

_ACRONYMS = {"crt", "vhs", "rgb", "ascii", "lut", "uv"}


def _natural_sort_key(text: str) -> list[int | str]:
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", text)]


def _sanitize_key(stem: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", stem.strip()).strip("_").lower()
    return f"shader_{cleaned or 'item'}"


def _safe_text(text: str) -> str:
    return str(text).replace("[", "(").replace("]", ")")


def _format_title(stem: str) -> str:
    def _cap(word: str) -> str:
        return word.upper() if word.lower() in _ACRONYMS else word.capitalize()

    match = re.match(r"^(\d+)[_-](.+)$", stem)
    if match:
        num, rest = match.groups()
        words = " ".join(_cap(w) for w in re.split(r"[_-]+", rest) if w)
        return _safe_text(f"{num} · {words}")
    words = " ".join(_cap(w) for w in re.split(r"[_-]+", stem) if w) or stem
    return _safe_text(words)


class HyprShaderEngine(BaseEngine):
    """Hyprland Screen Shader Engine."""

    editable_target: bool = False
    _active_instance: HyprShaderEngine | None = None
    _instance_lock: threading.RLock = threading.RLock()

    def __new__(cls, *args: Any, **kwargs: Any) -> HyprShaderEngine:
        with cls._instance_lock:
            if cls._active_instance is not None and not cls._active_instance._shutdown_done:
                return cls._active_instance
            instance = super().__new__(cls)
            cls._active_instance = instance
            return instance

    def __init__(
        self,
        config_path: str | Path | None = None,
        target_path: str | Path | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        chosen = config_path if config_path is not None else target_path
        if chosen is None and args and isinstance(args[0], (str, Path)):
            chosen = args[0]

        default_dir = Path.home() / ".config" / "hypr" / "shaders"
        try:
            self.shader_dir = (
                Path(chosen).expanduser().resolve() if chosen else default_dir.resolve()
            )
        except OSError:
            self.shader_dir = default_dir.expanduser()

        self._target_path = str(self.shader_dir)

        try:
            super().__init__(self._target_path)  # type: ignore[call-arg]
        except TypeError:
            try:
                super().__init__()
            except TypeError:
                pass

        # Native DuskyTUI post-write refresh hook
        self.refresh_after_write: bool = True
        self.cache: dict[str, str] = {}

        self.memory_file = (
            Path.home() / ".config" / "dusky" / "settings" / "dusky_shader" / "rofi_shader_memory"
        )
        runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
        sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or "default"
        self.state_file = runtime_dir / f"dusky-shader-{sig}.json"
        self.lock_file = runtime_dir / "dusky-shader-menu.lock"

        self._lock = threading.RLock()
        self._lock_fd: int | None = None
        self._lock_acquired: bool = False
        self._lock_error: str | None = None
        self._shutdown_done: bool = False

        self._initial_shader: str = ""
        self._initial_damage: int = 2
        self._initial_vfr: bool = True

        self._committed_shader: str = ""
        self._committed_damage: int = 2
        self._committed_vfr: bool = True

        self._live_shader: str = ""
        self._live_damage: int = 2
        self._live_vfr: bool = True

        self._base_damage: int = 2
        self._base_vfr: bool = True

        self._restore_needed: bool = False
        self._live_preview_enabled: bool = True

        self._key_to_path: dict[str, str] = {}
        self._shader_items: list[ConfigItem] = []
        self._control_items: dict[str, ConfigItem] = {}
        self._validation_cache: dict[str, int] = {}

        # Live preview state
        self.app: Any = None
        self._preview_timer: Any = None
        self._pending_preview_path: str | None = None
        self._last_highlighted_id: str | None = None

        self._worker_cond = threading.Condition()
        self._worker_target: str | None = None
        self._worker_has_job: bool = False
        self._worker_stop: bool = False
        self._worker = threading.Thread(
            target=self._preview_worker_loop,
            name="dusky-shader-preview",
            daemon=True,
        )

        self._acquire_session_lock()
        self._init_baseline()
        self.build_shader_tabs()
        self._worker.start()
        self._install_signal_handlers()
        atexit.register(self.shutdown)

    @property
    def target_path(self) -> str:
        """Fulfill BaseEngine's abstract target_path property contract."""
        return str(self.shader_dir)

    @target_path.setter
    def target_path(self, value: str | Path) -> None:
        self._target_path = str(value)
        try:
            self.shader_dir = Path(value).expanduser().resolve()
        except OSError:
            self.shader_dir = Path(value).expanduser()

    @classmethod
    def get_active_instance(cls) -> HyprShaderEngine | None:
        with cls._instance_lock:
            return cls._active_instance

    # ------------------------------------------------------------------
    # Locking & Signal Handling
    # ------------------------------------------------------------------

    def _acquire_session_lock(self) -> None:
        if self._lock_acquired:
            return
        try:
            self.lock_file.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.ftruncate(fd, 0)
                os.write(fd, f"{os.getpid()}\n".encode("ascii"))
                self._lock_fd = fd
                self._lock_acquired = True
                self._lock_error = None
            except OSError as exc:
                os.close(fd)
                self._lock_acquired = False
                self._lock_error = f"Another shader picker is running ({exc})"
        except OSError as exc:
            self._lock_acquired = False
            self._lock_error = str(exc)

    def _release_session_lock(self) -> None:
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None
        self._lock_acquired = False

    def _install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        for sig in (signal.SIGHUP, signal.SIGTERM):
            try:
                prev = signal.getsignal(sig)

                def _handler(signum: int, frame: Any, _prev: Any = prev) -> None:
                    self.shutdown()
                    if callable(_prev) and _prev not in (signal.SIG_IGN, signal.SIG_DFL, _handler):
                        _prev(signum, frame)
                    raise SystemExit(128 + signum)

                signal.signal(sig, _handler)
            except (OSError, ValueError):
                pass

    # ------------------------------------------------------------------
    # Hyprland IPC & Persistence (shader_menu.sh compatible)
    # ------------------------------------------------------------------

    def _read_live_shader(self) -> tuple[bool, str, int, bool]:
        if not shutil.which("hyprctl"):
            return False, self._live_shader, self._live_damage, self._live_vfr
        try:
            proc = subprocess.run(
                [
                    "hyprctl",
                    "-j",
                    "--batch",
                    "getoption decoration:screen_shader;getoption debug:damage_tracking;getoption debug:vfr",
                ],
                capture_output=True,
                text=True,
                timeout=2.5,
                check=False,
            )
            raw = proc.stdout.strip()
            if not raw:
                return False, self._live_shader, self._live_damage, self._live_vfr

            decoder = json.JSONDecoder()
            opts: list[dict[str, Any]] = []
            while raw:
                obj, end = decoder.raw_decode(raw)
                if isinstance(obj, dict):
                    opts.append(obj)
                raw = raw[end:].lstrip()

            if len(opts) < 3:
                return False, self._live_shader, self._live_damage, self._live_vfr

            shader = str(opts[0].get("str", "")).strip()
            if shader in ("[[EMPTY]]", "none", "null"):
                shader = ""
            damage = opts[1].get("int", self._live_damage)
            vfr = opts[2].get("bool", bool(opts[2].get("int", self._live_vfr)))

            if type(damage) is not int or damage not in (0, 1, 2) or type(vfr) is not bool:
                return False, self._live_shader, self._live_damage, self._live_vfr

            self._live_shader, self._live_damage, self._live_vfr = shader, damage, vfr
            return True, shader, damage, vfr
        except (OSError, subprocess.SubprocessError, ValueError):
            return False, self._live_shader, self._live_damage, self._live_vfr

    def _init_baseline(self) -> None:
        ok, shader, damage, vfr = self._read_live_shader()
        if not ok:
            return
        self._initial_shader = self._committed_shader = shader
        self._initial_damage = self._committed_damage = self._base_damage = damage
        self._initial_vfr = self._committed_vfr = self._base_vfr = vfr

        try:
            if self.state_file.is_file():
                st = json.loads(self.state_file.read_text(encoding="utf-8"))
                if (
                    st.get("shader") == shader
                    and st.get("damage") == damage
                    and st.get("vfr") is vfr
                    and type(st.get("base_damage")) is int
                    and st["base_damage"] in (0, 1, 2)
                    and type(st.get("base_vfr")) is bool
                ):
                    self._base_damage = st["base_damage"]
                    self._base_vfr = st["base_vfr"]
        except (OSError, ValueError, KeyError):
            pass

    def _write_state_file(self) -> bool:
        try:
            if (self._live_damage, self._live_vfr) == (self._base_damage, self._base_vfr):
                self.state_file.unlink(missing_ok=True)
                return True
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "shader": self._live_shader,
                "damage": int(self._live_damage),
                "vfr": bool(self._live_vfr),
                "base_damage": int(self._base_damage),
                "base_vfr": bool(self._base_vfr),
            }
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            tmp.replace(self.state_file)
            return True
        except OSError:
            return False

    def _write_memory_file(self, path: str) -> bool:
        try:
            self.memory_file.parent.mkdir(parents=True, exist_ok=True)
            lines = (
                self.memory_file.read_text(encoding="utf-8").splitlines()
                if self.memory_file.is_file()
                else []
            )
            lines = [ln for ln in lines if not ln.startswith("shader_menu=")]
            lines.append("shader_menu=" + json.dumps(path, ensure_ascii=True))
            self.memory_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
        except OSError:
            return False

    @staticmethod
    def _paths_match(a: str, b: str) -> bool:
        a, b = (a or "").strip(), (b or "").strip()
        if a == b:
            return True
        if not a or not b:
            return False
        try:
            return os.path.samefile(a, b)
        except OSError:
            return False

    def _inspect_shader_source(self, path: str) -> tuple[int, bool, str, str, str]:
        """Return (target_damage, target_vfr, policy_label, inputs_label, samples_label)."""
        if not path:
            return self._base_damage, self._base_vfr, "Static (Baseline)", "Screen texture (UV)", "1 sample/px"

        mode = 0  # 0=static, 1=full-redraw, 2=animated
        inputs = "Screen texture (UV)"
        samples = "1 sample/px"

        try:
            src = Path(path).read_text(encoding="utf-8", errors="replace")
            for line in src.splitlines():
                s = line.strip()
                if s == "// dusky: animated":
                    mode = 2
                elif s == "// dusky: full-redraw" and mode == 0:
                    mode = 1

            has_press = "pointer_pressed_positions" in src
            has_ptr = "pointer_position" in src
            has_time = bool(re.search(r"\buniform\s+float\s+time\b", src))

            if mode == 0 and (has_press or has_ptr or has_time):
                mode = 2

            if has_press and has_ptr and has_time:
                inputs = "Pointer + press history + time"
            elif has_press:
                inputs = "Native press history"
            elif has_ptr and has_time:
                inputs = "Pointer + time"
            elif has_ptr:
                inputs = "Pointer"
            elif has_time:
                inputs = "Time"

            tex_count = len(re.findall(r"\b(?:texture|textureLod|texelFetch)\s*\(", src))
            if tex_count > 1:
                samples = f"{tex_count} samples/px"
        except OSError:
            pass

        if mode == 2:
            return 0, False, "Animated (damage_tracking=0, vfr=false)", inputs, samples
        if mode == 1:
            dmg = 0 if self._base_damage == 0 else 1
            return dmg, self._base_vfr, "Full-Redraw (damage_tracking=1)", inputs, samples
        return self._base_damage, self._base_vfr, "Static (Baseline VFR & Damage)", inputs, samples

    def _validate_shader(self, path: str) -> tuple[bool, str]:
        if not path:
            return True, ""
        fp = Path(path)
        if not fp.is_file():
            return False, f"Shader file no longer exists: {path}"
        try:
            mtime = fp.stat().st_mtime_ns
        except OSError as exc:
            return False, str(exc)

        if self._validation_cache.get(path) == mtime:
            return True, ""

        validator = shutil.which("glslangValidator")
        if not validator:
            self._validation_cache[path] = mtime
            return True, ""

        try:
            proc = subprocess.run(
                [validator, "-S", "frag", path],
                capture_output=True,
                text=True,
                timeout=2.5,
                check=False,
            )
            if proc.returncode != 0:
                return False, f"Shader syntax check failed: {(proc.stderr or proc.stdout).strip()}"
            self._validation_cache[path] = mtime
            return True, ""
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)

    def _apply_shader(
        self,
        path: str,
        explicit_damage: int | None = None,
        explicit_vfr: bool | None = None,
        *,
        validate: bool = True,
    ) -> tuple[bool, str]:
        with self._lock:
            if validate and path:
                ok, err = self._validate_shader(path)
                if not ok:
                    return False, err

            if explicit_damage is not None and explicit_vfr is not None:
                target_damage, target_vfr = int(explicit_damage), bool(explicit_vfr)
            else:
                target_damage, target_vfr, _, _, _ = self._inspect_shader_source(path)

            encoded = "".join(f"\\{b:03d}" for b in os.fsencode(path))
            vfr_str = "true" if target_vfr else "false"
            lua = (
                f'hl.config({{ decoration = {{ screen_shader = "{encoded}" }}, '
                f"debug = {{ damage_tracking = {target_damage}, vfr = {vfr_str} }} }})"
            )

            try:
                proc = subprocess.run(
                    ["hyprctl", "eval", lua],
                    capture_output=True,
                    text=True,
                    timeout=2.5,
                    check=False,
                )
                if proc.returncode != 0 or "unknown request" in (proc.stdout + proc.stderr).lower():
                    shader_arg = path if path else "[[EMPTY]]"
                    batch = (
                        f"keyword decoration:screen_shader {shader_arg};"
                        f"keyword debug:damage_tracking {target_damage};"
                        f"keyword debug:vfr {1 if target_vfr else 0}"
                    )
                    proc = subprocess.run(
                        ["hyprctl", "--batch", batch],
                        capture_output=True,
                        text=True,
                        timeout=2.5,
                        check=False,
                    )
                    if proc.returncode != 0:
                        return False, (proc.stderr or proc.stdout or "hyprctl failed").strip()
            except (OSError, subprocess.SubprocessError) as exc:
                return False, str(exc)

            ok, live_path, live_dmg, live_vfr = self._read_live_shader()
            if not ok or not self._paths_match(live_path, path) or live_dmg != target_damage or live_vfr is not target_vfr:
                return False, "Shader/rendering readback does not match the requested values."

            return True, f"Applied {Path(path).name if path else 'Off'}"

    # ------------------------------------------------------------------
    # Discovery & Tab Construction
    # ------------------------------------------------------------------

    def _classify_shader(self, stem: str, inputs: str) -> tuple[int, str, str, str | None]:
        lower = stem.lower()
        stripped = re.sub(r"^\d+[_-]+", "", lower)
        entry = SHADER_CATALOG.get(lower) or SHADER_CATALOG.get(stripped)
        if entry:
            tab, group, summary = entry
        elif "press" in inputs.lower() or "click" in lower:
            tab, group, summary = 3, "Click Reactive", "Interactive click-reactive screen shader."
        elif "Pointer" in inputs or "cursor" in lower:
            tab, group, summary = 3, "Gentle Pointer", "Interactive pointer-tracking screen shader."
        elif "Time" in inputs:
            tab, group, summary = 4, "Water & Curl Fields", "Animated ambient screen shader."
        else:
            num_m = re.match(r"^(\d+)", lower)
            num = int(num_m.group(1)) if num_m else 16
            if num <= 15:
                tab, group, summary = 1, "Contrast & Palette", "Color and tonal screen shader."
            elif num <= 22:
                tab, group, summary = 2, "Lens & Blur", "Optical and spatial screen shader."
            elif num <= 33:
                tab, group, summary = 3, "Gentle Pointer", "Interactive pointer-tracking screen shader."
            elif num <= 44:
                tab, group, summary = 4, "Water & Curl Fields", "Animated ambient screen shader."
            else:
                tab, group, summary = 5, "Shatter, Melt & Glitch", "High-displacement distortion shader."

        return tab, group, summary, None

    def build_shader_tabs(self) -> dict[int, list[ConfigItem]]:
        with self._lock:
            files: list[Path] = []
            seen: set[Path] = set()
            if self.shader_dir.is_dir():
                for pat in ("*.glsl", "*.frag"):
                    for p in self.shader_dir.glob(pat):
                        if p.is_file():
                            res = p.resolve()
                            if res not in seen:
                                seen.add(res)
                                files.append(res)
            files.sort(key=lambda p: _natural_sort_key(p.name))

            tabs: dict[int, list[tuple[int, str, list[int | str], ConfigItem]]] = {
                i: [] for i in range(1, 6)
            }
            self._key_to_path.clear()
            self._shader_items.clear()

            for fp in files:
                key = _sanitize_key(fp.stem)
                if key in self._key_to_path:
                    key = _sanitize_key(f"{fp.stem}_{fp.suffix.lstrip('.')}")
                path_str = str(fp)
                self._key_to_path[key] = path_str

                title = _format_title(fp.stem)
                _, _, policy_desc, inputs, samples = self._inspect_shader_source(path_str)
                tab, group, summary, warning = self._classify_shader(fp.stem, inputs)

                help_md = (
                    f"**{title}** (`{fp.name}`)\n\n{summary}\n\n"
                    f"- **Render Policy:** `{policy_desc}`\n"
                    f"- **Uniform Inputs:** `{inputs}`\n"
                    f"- **Texture Samples:** `{samples}`\n"
                    f"- **Path:** `{path_str}`"
                )

                item = ConfigItem(
                    label=title,
                    key=key,
                    scope="shader",
                    type_="bool",
                    default=False,
                    group=group,
                    warning_msg=None,
                    extended_help=help_md,
                )
                self._shader_items.append(item)
                order = GROUP_ORDER.get(tab, {}).get(group, 99)
                tabs[tab].append((order, group, _natural_sort_key(fp.name), item))

            result: dict[int, list[ConfigItem]] = {}
            for idx in range(1, 6):
                rows = sorted(tabs[idx], key=lambda r: (r[0], r[1], r[2]))
                result[idx] = [r[3] for r in rows] if rows else [
                    ConfigItem(
                        label="No shaders found in this category",
                        key=f"empty_tab_{idx}",
                        scope="status",
                        type_="action",
                        default=":",
                        group="Directory Status",
                        read_only=True,
                    )
                ]

            self._sync_telemetry_and_cache()
            return result

    # ------------------------------------------------------------------
    # Live Preview Hook & Coalesced Worker
    # ------------------------------------------------------------------

    def register_control_items(self, items: list[ConfigItem]) -> None:
        with self._lock:
            for item in items:
                self._control_items[item.key] = item
            self._sync_telemetry_and_cache()

    def bind_app(self, app: Any) -> None:
        self.set_app(app)

    def set_app(self, app: Any) -> None:
        self.app = app
        if app is None or getattr(app, "_shader_hook_installed", False):
            return

        orig = app.handle_option_highlight

        def _wrapped(app_self: Any, event: Any) -> None:
            orig(event)
            self._on_option_highlighted(event)

        _wrapped.__name__ = "handle_option_highlight"
        app.handle_option_highlight = types.MethodType(_wrapped, app)
        for handlers in getattr(app, "_decorated_handlers", {}).values():
            for i, entry in enumerate(handlers):
                if isinstance(entry, tuple) and getattr(entry[0], "__name__", "") == "handle_option_highlight":
                    handlers[i] = (_wrapped, *entry[1:])
        app._shader_hook_installed = True

    def _on_option_highlighted(self, event: Any) -> None:
        if not self._live_preview_enabled or not self._lock_acquired or self.app is None:
            return
        ol = getattr(event, "option_list", None)
        opt_id = getattr(event, "option_id", None)
        if ol is not self.app.current_option_list or not opt_id or not getattr(ol, "has_focus", True):
            return
        if opt_id == self._last_highlighted_id:
            return
        self._last_highlighted_id = opt_id

        parsed = self.app._get_item_from_id(opt_id)
        if not parsed:
            return
        _, _, item = parsed
        if item.scope != "shader":
            return

        target = self._key_to_path.get(item.key)
        if target is not None:
            self._pending_preview_path = target
            if self._preview_timer is not None:
                try:
                    self._preview_timer.stop()
                except Exception:
                    pass
            self._preview_timer = self.app.set_timer(0.02, self._flush_preview)

    def _flush_preview(self) -> None:
        self._preview_timer = None
        target = self._pending_preview_path
        self._pending_preview_path = None
        if target is not None and self._live_preview_enabled and self._lock_acquired:
            with self._worker_cond:
                self._worker_target = target
                self._worker_has_job = True
                self._worker_cond.notify_all()

    def _cancel_pending_previews(self) -> None:
        self._pending_preview_path = None
        if self._preview_timer is not None and threading.current_thread() is threading.main_thread():
            try:
                self._preview_timer.stop()
            except Exception:
                pass
            self._preview_timer = None
        with self._worker_cond:
            self._worker_target = None
            self._worker_has_job = False

    def _preview_worker_loop(self) -> None:
        while True:
            with self._worker_cond:
                while not self._worker_has_job and not self._worker_stop:
                    self._worker_cond.wait()
                if self._worker_stop:
                    return
                target = self._worker_target
                self._worker_target = None
                self._worker_has_job = False

            if target is None or self._shutdown_done or not self._lock_acquired or not self._live_preview_enabled:
                continue

            with self._lock:
                dmg, vfr, _, _, _ = self._inspect_shader_source(target)
                if self._paths_match(self._live_shader, target) and self._live_damage == dmg and self._live_vfr is vfr:
                    continue
                self._restore_needed = True
                ok, _ = self._apply_shader(target, dmg, vfr, validate=True)
                if ok:
                    self._restore_needed = not (
                        self._paths_match(self._live_shader, self._committed_shader)
                        and self._live_damage == self._committed_damage
                        and self._live_vfr is self._committed_vfr
                    )
                    self._sync_telemetry_and_cache()

    # ------------------------------------------------------------------
    # State Snapshot & BaseEngine Contract
    # ------------------------------------------------------------------

    def _sync_telemetry_and_cache(self) -> dict[str, str]:
        committed = _safe_text(Path(self._committed_shader).name) if self._committed_shader else "Off (None)"
        live = _safe_text(Path(self._live_shader).name) if self._live_shader else "Off (None)"
        suffix = " (UNCOMMITTED PREVIEW)" if self._restore_needed else " (Committed)"
        lock_txt = "Locked ✓" if self._lock_acquired else f"LOCK BUSY ({_safe_text(self._lock_error or '')})"

        labels = {
            "status_committed": f"Committed Shader: {committed}",
            "status_live": f"Live On-Screen: {live}{suffix}",
            "status_policy": f"Live Render Policy: damage_tracking={self._live_damage}, vfr={str(self._live_vfr).lower()}",
            "status_baseline": f"Saved Baseline: damage_tracking={self._base_damage}, vfr={str(self._base_vfr).lower()}",
            "status_catalog": f"Catalog: {len(self._shader_items)} shaders in {_safe_text(self.shader_dir.name)} ({lock_txt})",
        }
        for k, text in labels.items():
            if k in self._control_items:
                self._control_items[k].label = text

        if "shader_off" in self._control_items:
            self._control_items["shader_off"].options = [
                "trigger:Off ✓" if not self._committed_shader else "trigger:Turn Off"
            ]

        state: dict[str, str] = {
            "control/live_preview": "true" if self._live_preview_enabled else "false",
            "control/shader_off": "false",
            "control/rollback_preview": "false",
            **{f"status/{k}": ":" for k in labels},
            **{
                f"shader/{k}": (
                    "true"
                    if self._committed_shader and self._paths_match(p, self._committed_shader)
                    else "false"
                )
                for k, p in self._key_to_path.items()
            },
        }
        self.cache = state
        if self.app is not None:
            self.app._tab_dirty.add(0)
            if threading.current_thread() is threading.main_thread():
                if self.app._current_tab_index() == 0:
                    self.app._populate_option_list(0)
            elif getattr(self.app, "is_running", False):
                try:
                    self.app.call_from_thread(
                        lambda: self.app._populate_option_list(0)
                        if self.app._current_tab_index() == 0
                        else None
                    )
                except RuntimeError:
                    pass
        return state

    def load_state(self) -> dict[str, str]:
        with self._lock:
            self._read_live_shader()
            if not self._shader_items:
                self.build_shader_tabs()
            return self._sync_telemetry_and_cache()

    def _commit_shader(self, target_path: str) -> tuple[bool, str, str]:
        with self._lock:
            self._cancel_pending_previews()
            self._last_highlighted_id = None
            if not self._lock_acquired:
                self._acquire_session_lock()
            if not self._lock_acquired:
                return False, self._lock_error or "Session lock unavailable.", ""

            self._restore_needed = True
            ok, msg = self._apply_shader(target_path, validate=True)
            if not ok:
                return False, msg, ""

            if not self._write_state_file():
                self._apply_shader(self._committed_shader, self._committed_damage, self._committed_vfr, validate=False)
                return False, "Could not save rendering state file; restored previous shader.", ""

            self._committed_shader = self._live_shader
            self._committed_damage = self._live_damage
            self._committed_vfr = self._live_vfr
            self._restore_needed = False

            mem_ok = self._write_memory_file(self._committed_shader)
            if shutil.which("notify-send"):
                label = Path(self._committed_shader).name if self._committed_shader else "Off"
                subprocess.Popen(
                    ["notify-send", "-i", "video-display", "Hyprshade", f"Applied: {label}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            self._sync_telemetry_and_cache()
            name = Path(self._committed_shader).name if self._committed_shader else "Off (Disabled)"
            dbg = "" if mem_ok else "Shader applied, but selection memory could not be saved."
            return True, f"Committed shader: {name}", dbg

    def write_value(self, key: str, scope: str, value: str, item_type: str = "bool") -> tuple[bool, str, str]:
        val = str(value).strip().lower()
        with self._lock:
            if scope == "control" and key == "live_preview":
                self._live_preview_enabled = val == "true"
                self._last_highlighted_id = None
                if not self._live_preview_enabled:
                    self._cancel_pending_previews()
                    if self._restore_needed:
                        self._apply_shader(
                            self._committed_shader, self._committed_damage, self._committed_vfr, validate=False
                        )
                        self._restore_needed = False
                self._sync_telemetry_and_cache()
                return True, f"Live preview {'enabled' if self._live_preview_enabled else 'disabled'}.", ""

            if scope == "control" and key == "rollback_preview":
                if val == "false":
                    return True, "", ""
                self._cancel_pending_previews()
                self._last_highlighted_id = None
                rb_shader = self._committed_shader if self._restore_needed else self._initial_shader
                rb_dmg = self._committed_damage if self._restore_needed else self._initial_damage
                rb_vfr = self._committed_vfr if self._restore_needed else self._initial_vfr
                ok, msg = self._apply_shader(rb_shader, rb_dmg, rb_vfr, validate=False)
                if ok:
                    self._committed_shader, self._committed_damage, self._committed_vfr = (
                        self._live_shader,
                        self._live_damage,
                        self._live_vfr,
                    )
                    self._restore_needed = False
                    self._write_state_file()
                    self._sync_telemetry_and_cache()
                    return True, "Restored baseline shader and rendering policy.", ""
                return False, msg, ""

            if scope == "control" and key == "shader_off":
                return (True, "", "") if val == "false" else self._commit_shader("")

            if scope == "shader":
                target = self._key_to_path.get(key)
                if target is None:
                    return False, f"Unknown shader key: {key}", ""
                if val == "true":
                    return self._commit_shader(target)
                if val == "false":
                    if (self._committed_shader and self._paths_match(target, self._committed_shader)) or (
                        self._live_shader and self._paths_match(target, self._live_shader)
                    ):
                        return self._commit_shader("")
                    self._sync_telemetry_and_cache()
                    return True, f"{Path(target).name} is already off.", ""

            return False, f"Unsupported setting: {scope}.{key}", ""

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        shader_on = [c for c in changes if c[1] == "shader" and str(c[2]).strip().lower() == "true"]
        shader_off = [c for c in changes if c[1] == "shader" and str(c[2]).strip().lower() == "false"]
        others = [c for c in changes if c[1] != "shader"]
        ordered = [*others, *shader_on] if shader_on else [*others, *shader_off]

        msgs: list[str] = []
        for k, s, v, t in ordered:
            ok, m, dbg = self.write_value(k, s, v, t)
            if not ok:
                return False, m, dbg
            if m:
                msgs.append(m)
        return True, "; ".join(msgs) or "Batch complete.", ""

    def shutdown(self) -> None:
        with self._worker_cond:
            self._worker_stop = True
            self._worker_target = None
            self._worker_has_job = False
            self._worker_cond.notify_all()

        with self._lock:
            if self._shutdown_done:
                return
            self._shutdown_done = True
            self._cancel_pending_previews()
            if self._lock_acquired and (
                self._restore_needed
                or not self._paths_match(self._live_shader, self._committed_shader)
                or self._live_damage != self._committed_damage
                or self._live_vfr is not self._committed_vfr
            ):
                try:
                    self._apply_shader(
                        self._committed_shader, self._committed_damage, self._committed_vfr, validate=False
                    )
                    self._write_state_file()
                except Exception:
                    pass
            self._release_session_lock()
