#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: VISUALIZER CONFIGURATION SCHEMA
===============================================================================
Target: ~/.config/dusky/settings/way_layers/visualizer/visualizer.json
Engine: json_engine
"""

import sys
from pathlib import Path

_dusky_root = Path.home() / "user_scripts" / "dusky_tui"
if str(_dusky_root) not in sys.path:
    sys.path.insert(0, str(_dusky_root))

import sys
from pathlib import Path

_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "json"
TARGET_FILE = "~/.config/dusky/settings/way_layers/visualizer/visualizer.json"
APP_TITLE = "Dusky Visualizer"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "Style",
    "Motion",
    "Effects",
    "Advanced"
]

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: STYLE
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Style Mode",
            key="style",
            scope="DEFAULT",
            type_="cycle",
            options=["bars", "dots", "line", "wave", "segments", "radial", "circle", "spectrum", "aurora", "psychedelic", "kaleidoscope", "lightning", "perimeter"],
            default="bars",
            group="Appearance",
            extended_help="Choose the geometric rendering style for the visualizer.",
        ),
        ConfigItem(
            label="Position",
            key="position",
            scope="DEFAULT",
            type_="cycle",
            options=["top", "bottom", "center"],
            default="top",
            group="Appearance",
            extended_help="Where the visualizer should be anchored on the screen.",
        ),
        ConfigItem(
            label="Mirror Output",
            key="mirror",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Appearance",
            extended_help="Mirrors the visualizer from the center outwards.",
        ),
        ConfigItem(
            label="Rounded Shape",
            key="shape_rounded",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Appearance",
            extended_help="Rounds the edges of the bars and lines.",
        ),
        ConfigItem(
            label="Bars Count",
            key="bars",
            scope="DEFAULT",
            type_="int",
            min_val=16,
            max_val=256,
            step=8,
            default=72,
            group="Appearance",
            extended_help="Number of frequency bands. Higher values use more CPU.",
        ),
        ConfigItem(
            label="Segments Count",
            key="segments_count",
            scope="DEFAULT",
            type_="int",
            min_val=4,
            max_val=32,
            step=2,
            default=18,
            group="Appearance",
            extended_help="How many blocks to split each bar into (only applies to Segments style).",
        ),
        ConfigItem(
            label="Visualizer Height (%)",
            key="height_pct",
            scope="DEFAULT",
            type_="float",
            min_val=0.05,
            max_val=1.00,
            step=0.05,
            default=0.50,
            group="Geometry",
            extended_help="The percentage of the screen height the visualizer can span.",
        ),
        ConfigItem(
            label="Thickness (%)",
            key="thickness",
            scope="DEFAULT",
            type_="float",
            min_val=0.1,
            max_val=1.0,
            step=0.1,
            default=0.8,
            group="Geometry",
            extended_help="The width of each bar or line relative to its allocated space.",
        ),
    ],
    # -------------------------------------------------------------------------
    # TAB 1: MOTION
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Framerate (FPS)",
            key="fps",
            scope="DEFAULT",
            type_="int",
            min_val=15,
            max_val=144,
            step=15,
            default=60,
            group="Performance",
            extended_help="The rendering framerate of the visualizer surface (Cairo/OpenGL).",
        ),
        ConfigItem(
            label="Smoothing Alpha",
            key="smoothing",
            scope="DEFAULT",
            type_="float",
            min_val=0.0,
            max_val=0.99,
            step=0.05,
            default=0.50,
            group="Dynamics",
            extended_help="Controls how fast the bars fall down. Higher values are slower.",
        ),
        ConfigItem(
            label="Visual Gain",
            key="gain",
            scope="DEFAULT",
            type_="float",
            min_val=0.1,
            max_val=5.0,
            step=0.1,
            default=1.0,
            group="Dynamics",
            extended_help="Multiplier for the audio values. If it's too quiet, increase this.",
        ),
    ],
    # -------------------------------------------------------------------------
    # TAB 2: EFFECTS
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Idle Wave Animation",
            key="idle_wave",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Idle State",
            extended_help="Plays a gentle sine wave animation when no audio is playing.",
        ),
        ConfigItem(
            label="Fade Direction",
            key="fade_direction",
            scope="DEFAULT",
            type_="cycle",
            options=["solid", "fade_to_base", "fade_to_tip"],
            default="fade_to_base",
            group="Visual Effects",
            extended_help="Controls transparency fading of shapes. 'fade_to_base' leaves bright tips.",
        ),
        ConfigItem(
            label="Fade Amount",
            key="fade_amount",
            scope="DEFAULT",
            type_="float",
            min_val=0.0,
            max_val=1.0,
            step=0.1,
            default=0.8,
            group="Visual Effects",
            extended_help="How transparent the faded end becomes. 1.0 is fully transparent, 0.0 is solid.",
        ),
        ConfigItem(
            label="Outer Glow / Bloom",
            key="bloom",
            scope="DEFAULT",
            type_="float",
            min_val=0.0,
            max_val=1.0,
            step=0.05,
            default=1.00,
            group="Visual Effects",
            extended_help="Intensity of the neon aura radiating OUTWARDS into the background.",
        ),
        ConfigItem(
            label="Inner Glow / Highlight",
            key="inner_glow",
            scope="DEFAULT",
            type_="float",
            min_val=0.0,
            max_val=1.0,
            step=0.05,
            default=0.70,
            group="Visual Effects",
            extended_help="Intensity of the core over-exposure highlight INWARDS inside the shape body.",
        ),
        ConfigItem(
            label="Liquid Specular Shine",
            key="specular_shine",
            scope="DEFAULT",
            type_="float",
            min_val=0.0,
            max_val=1.0,
            step=0.05,
            default=0.30,
            group="Visual Effects",
            extended_help="Sweeping liquid glass light reflection glinting along wave crests.",
        ),
        ConfigItem(
            label="Stardust Sparkles",
            key="stardust",
            scope="DEFAULT",
            type_="float",
            min_val=0.0,
            max_val=1.0,
            step=0.05,
            default=0.10,
            group="Visual Effects",
            extended_help="Floating magical sparkle particles drifting upwards from active wave peaks.",
        ),
        ConfigItem(
            label="Frosted Glass (Hyprland Blur)",
            key="glass_blur",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Visual Effects",
            extended_help="Applies a background blur to the visualizer (requires Hyprland).",
        ),
    ],
    # -------------------------------------------------------------------------
    # TAB 3: ADVANCED
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Master Toggle",
            key="enabled",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="System",
            extended_help="Enable or disable the visualizer completely.",
        ),
        ConfigItem(
            label="GPU Acceleration (OpenGL)",
            key="gpu_acceleration",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="System",
            extended_help="Use hardware GPU acceleration (Gtk.GLArea/OpenGL) instead of CPU software rendering (Cairo).",
        ),
        ConfigItem(
            label="Noise Reduction",
            key="cava_noise_reduction",
            scope="DEFAULT",
            type_="float",
            min_val=0.0,
            max_val=1.0,
            step=0.01,
            default=0.77,
            group="Cava Settings",
            extended_help="Cava internal noise reduction algorithm. Default is 0.77.",
        ),
        ConfigItem(
            label="Lower Freq Cutoff",
            key="cava_lower_freq",
            scope="DEFAULT",
            type_="int",
            min_val=20,
            max_val=2000,
            step=10,
            default=50,
            group="Cava Settings",
            extended_help="Lower frequency limit passed to Cava (Hz).",
        ),
        ConfigItem(
            label="Upper Freq Cutoff",
            key="cava_upper_freq",
            scope="DEFAULT",
            type_="int",
            min_val=5000,
            max_val=20000,
            step=500,
            default=10000,
            group="Cava Settings",
            extended_help="Upper frequency limit passed to Cava (Hz).",
        ),
    ],
}

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    import sys, subprocess
    from pathlib import Path

    script_path = Path(__file__).resolve()
    main_router = Path.home() / "user_scripts" / "dusky_tui" / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
