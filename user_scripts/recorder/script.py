#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: GPU SCREEN RECORDER SCHEMA
===============================================================================
Flow:
  1. TUI writes settings to ~/.config/gsr-tui/config.conf  (batch mode)
  2. Ctrl+S  — flushes config.conf
  3. Profiles → Save  — runs build_scripts.sh which writes:
       toggle_record.sh  — timestamped -o path, killall toggle

Hyprland keybinds:
  bind = SUPER, F9,  exec, ~/.config/gsr-tui/toggle_record.sh

===============================================================================
"""

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "ini"
TARGET_FILE  = "~/.config/gsr-tui/config.conf"
APP_TITLE    = "GPU Screen Recorder"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE        = "batch"
THEME_FILE          = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB    = "Profiles"

# No device presets — user presets only
ENABLE_DEVICE_PRESETS = False

GLOBAL_POPUP = {
    "title":           "GPU Screen Recorder",
    "message":         "Ctrl+S saves settings. Then go to Profiles → Save to rebuild the toggle scripts.",
    "level":           "info",
    "require_confirm": False,
    "cancel_quits":    False,
}

TAB_NOTICES = {
    3: {
        "level":   "info",
        "message": "Ctrl+S saves config. Hit Save here to rebuild the toggle scripts.",
    }
}

# =============================================================================
# 3. TABS
# =============================================================================
TABS = [
    "Capture",
    "Encoding",
    "Audio",
    "Profiles",
]

# =============================================================================
# 4. SCHEMA
# =============================================================================
SCHEMA = {

    # =========================================================================
    # TAB 0 — CAPTURE
    # =========================================================================
    0: [

        ConfigItem(
            label="Source",
            key="window",
            scope="DEFAULT",
            type_="cycle",
            default="screen",
            options=["screen", "portal", "region"],
            group="Source",
            extended_help=(
                "**Capture Target** (`-w`)\n\n"
                "- `screen`  – entire screen / primary monitor\n"
                "- `portal`  – XDG desktop portal picker (Wayland)\n"
                "- `region`  – arbitrary rectangle (set Region below)\n\n"
                "To target a specific monitor, run "
                "`gpu-screen-recorder --list-monitors` and type the "
                "connector name (e.g. `DP-1`) directly into this field."
            ),
        ),
        ConfigItem(
            label="Region",
            key="region",
            scope="DEFAULT",
            type_="string",
            default="",
            group="Source",
            extended_help=(
                "**Capture Region** (`-region WxH+X+Y`)\n\n"
                "Only used when Source is `region`. Example: `1280x720+100+50`."
            ),
        ),

        ConfigItem(
            label="Container",
            key="container",
            scope="DEFAULT",
            type_="cycle",
            default="mp4",
            options=["mp4", "mkv", "flv", "ts", "mov"],
            group="Output",
            extended_help=(
                "**Container Format** (`-c`)\n\n"
                "`mp4` for general use; `mkv` for lossless/HDR; `flv`/`ts` for streaming."
            ),
        ),
        ConfigItem(
            label="Directory",
            key="output_dir",
            scope="DEFAULT",
            type_="string",
            default="~/Videos",
            group="Output",
            extended_help=(
                "**Output Directory**\n\n"
                "Folder where recordings are saved. "
                "Filename is generated at record time: `Video_YYYY-MM-DD_HH-MM-SS.<ext>`"
            ),
        ),
        ConfigItem(
            label="Size",
            key="size",
            scope="DEFAULT",
            type_="string",
            default="",
            group="Output",
            extended_help=(
                "**Output Size** (`-s WxH`)\n\n"
                "Scale output, e.g. `1920x1080`. Empty = source resolution (flag omitted)."
            ),
        ),

        ConfigItem(
            label="FPS",
            key="fps",
            scope="DEFAULT",
            type_="int",
            default=60,
            min_val=1,
            max_val=360,
            step=5,
            group="Playback",
            extended_help="**Frame Rate** (`-f`)\n\nTarget frames per second.",
        ),
        ConfigItem(
            label="FrameMode",
            key="frame_mode",
            scope="DEFAULT",
            type_="cycle",
            default="none",
            options=["none", "cfr", "vfr", "content"],
            group="Playback",
            extended_help=(
                "**Frame Mode** (`-fm cfr|vfr|content`)\n\n"
                "`none` — omit the flag entirely (let gsr decide)."
            ),
        ),
        ConfigItem(
            label="Cursor",
            key="cursor",
            scope="DEFAULT",
            type_="cycle",
            default="none",
            options=["none", "yes", "no"],
            group="Playback",
            extended_help=(
                "**Show Cursor** (`-cursor yes|no`)\n\n"
                "`none` — omit the flag (gsr default: yes)."
            ),
        ),

        ConfigItem(
            label="Replay",
            key="replay_buffer",
            scope="DEFAULT",
            type_="int",
            default=0,
            min_val=0,
            max_val=600,
            step=5,
            is_parent=True,
            expanded=False,
            group="ReplayBuffer",
            extended_help=(
                "**Replay Buffer** (`-r N`)\n\n"
                "Rolling buffer in seconds. 0 = disabled. "
                "toggle_replay.sh sends SIGUSR1 to save a clip without stopping."
            ),
        ),
        ConfigItem(
            label="Storage",
            key="replay_storage",
            scope="DEFAULT",
            type_="cycle",
            default="ram",
            options=["ram", "disk"],
            parent_ref="replay_buffer",
            extended_help="**Replay Storage** (`-replay-storage ram|disk`)",
        ),
        ConfigItem(
            label="Restart",
            key="restart_replay",
            scope="DEFAULT",
            type_="cycle",
            default="none",
            options=["none", "yes", "no"],
            parent_ref="replay_buffer",
            extended_help=(
                "**Restart Replay on Save** (`-restart-replay-on-save yes|no`)\n\n"
                "`none` — omit the flag."
            ),
        ),

        ConfigItem(
            label="RestorePortal",
            key="restore_portal",
            scope="DEFAULT",
            type_="cycle",
            default="none",
            options=["none", "yes", "no"],
            is_parent=True,
            expanded=False,
            group="Portal",
            extended_help=(
                "**Restore Portal Session** (`-restore-portal-session yes|no`)\n\n"
                "`none` — omit the flag."
            ),
        ),
        ConfigItem(
            label="Token",
            key="portal_token",
            scope="DEFAULT",
            type_="string",
            default="",
            parent_ref="restore_portal",
            extended_help="**Portal Token Filepath** (`-portal-session-token-filepath`)",
        ),
    ],

    # =========================================================================
    # TAB 1 — ENCODING
    # =========================================================================
    1: [

        ConfigItem(
            label="Encoder",
            key="encoder",
            scope="DEFAULT",
            type_="cycle",
            default="gpu",
            options=["gpu", "cpu"],
            group="Encoder",
            extended_help=(
                "**Encoder** (`-encoder gpu|cpu`)\n\n"
                "`gpu` uses NVENC/VAAPI/AMF. `cpu` uses libx264/libx265."
            ),
        ),
        ConfigItem(
            label="FallbackCPU",
            key="fallback_cpu",
            scope="DEFAULT",
            type_="cycle",
            default="none",
            options=["none", "yes", "no"],
            group="Encoder",
            extended_help=(
                "**Fallback to CPU** (`-fallback-cpu-encoding yes|no`)\n\n"
                "`none` — omit the flag."
            ),
        ),
        ConfigItem(
            label="LowPower",
            key="low_power",
            scope="DEFAULT",
            type_="cycle",
            default="none",
            options=["none", "yes", "no"],
            group="Encoder",
            warning_msg="Low-power mode may reduce encoding quality on some GPUs.",
            extended_help=(
                "**Low Power Mode** (`-low-power yes|no`)\n\n"
                "Intel Quick Sync only. `none` — omit the flag."
            ),
        ),

        ConfigItem(
            label="Codec",
            key="codec",
            scope="DEFAULT",
            type_="picker",
            default="h264",
            options=[
                "h264", "hevc", "av1",
                "vp8", "vp9",
                "hevc_hdr", "av1_hdr",
                "hevc_10bit", "av1_10bit",
            ],
            hints=[
                "Broadest compatibility", "H.265 – better compression",
                "AV1 – best compression", "VP8 – open codec",
                "VP9 – open, efficient",  "HEVC + HDR metadata",
                "AV1 + HDR metadata",     "HEVC 10-bit colour",
                "AV1 10-bit colour",
            ],
            group="VideoCodec",
            extended_help="**Video Codec** (`-k`)",
        ),
        ConfigItem(
            label="Quality",
            key="quality",
            scope="DEFAULT",
            type_="cycle",
            default="high",
            options=["ultra", "high", "medium", "low"],
            group="VideoCodec",
            extended_help="**Quality Preset** (`-q ultra|high|medium|low`)",
        ),
        ConfigItem(
            label="BitrateMode",
            key="bitrate_mode",
            scope="DEFAULT",
            type_="cycle",
            default="none",
            options=["none", "auto", "qp", "vbr", "cbr"],
            group="VideoCodec",
            extended_help=(
                "**Bitrate Mode** (`-bm auto|qp|vbr|cbr`)\n\n"
                "`none` — omit the flag."
            ),
        ),
        ConfigItem(
            label="ColorRange",
            key="color_range",
            scope="DEFAULT",
            type_="cycle",
            default="none",
            options=["none", "limited", "full"],
            group="VideoCodec",
            extended_help=(
                "**Color Range** (`-cr limited|full`)\n\n"
                "`none` — omit the flag."
            ),
        ),
        ConfigItem(
            label="Tune",
            key="tune",
            scope="DEFAULT",
            type_="cycle",
            default="none",
            options=["none", "performance", "quality"],
            group="VideoCodec",
            extended_help=(
                "**Tune** (`-tune performance|quality`)\n\n"
                "`none` — omit the flag."
            ),
        ),
        ConfigItem(
            label="Keyframe",
            key="keyint",
            scope="DEFAULT",
            type_="int",
            default=0,
            min_val=0,
            max_val=600,
            step=1,
            group="VideoCodec",
            extended_help=(
                "**Keyframe Interval** (`-keyint N`)\n\n"
                "0 = omit the flag (let encoder decide)."
            ),
        ),
        ConfigItem(
            label="DarkFrame",
            key="dark_frame",
            scope="DEFAULT",
            type_="cycle",
            default="none",
            options=["none", "yes", "no"],
            group="VideoCodec",
            extended_help=(
                "**Dark Frame** (`-df yes|no`)\n\n"
                "`none` — omit the flag."
            ),
        ),

        ConfigItem(
            label="FirstFrameTS",
            key="first_frame_ts",
            scope="DEFAULT",
            type_="cycle",
            default="none",
            options=["none", "yes", "no"],
            group="Advanced",
            extended_help=(
                "**Write First Frame Timestamp** (`-write-first-frame-ts yes|no`)\n\n"
                "`none` — omit the flag."
            ),
        ),
        ConfigItem(
            label="GLDebug",
            key="gl_debug",
            scope="DEFAULT",
            type_="cycle",
            default="none",
            options=["none", "yes", "no"],
            group="Advanced",
            warning_msg="GL debug is very verbose and may impact performance.",
            extended_help=(
                "**GL Debug** (`-gl-debug yes|no`)\n\n"
                "`none` — omit the flag."
            ),
        ),
        ConfigItem(
            label="Verbose",
            key="verbose",
            scope="DEFAULT",
            type_="cycle",
            default="none",
            options=["none", "yes", "no"],
            group="Advanced",
            extended_help=(
                "**Verbose Output** (`-v yes|no`)\n\n"
                "`none` — omit the flag."
            ),
        ),
        ConfigItem(
            label="FFmpegOpts",
            key="ffmpeg_opts",
            scope="DEFAULT",
            type_="string",
            default="",
            group="Advanced",
            warning_msg="Incorrect FFmpeg options can cause recording to fail silently.",
            extended_help="**Extra FFmpeg Options** (`-ffmpeg-opts '...'`)",
        ),

        ConfigItem(
            label="Plugin",
            key="plugin_path",
            scope="DEFAULT",
            type_="string",
            default="",
            group="Plugins",
            extended_help="**Plugin Path** (`-p path`)",
        ),
        ConfigItem(
            label="Script",
            key="script_path",
            scope="DEFAULT",
            type_="string",
            default="",
            group="Plugins",
            extended_help="**Script Path** (`-sc path`)",
        ),
    ],

    # =========================================================================
    # TAB 2 — AUDIO
    # =========================================================================
    2: [

        ConfigItem(
            label="Input",
            key="audio",
            scope="DEFAULT",
            type_="cycle",
            default="auto",
            # "auto" resolves at build time via `pactl get-default-sink`.
            # "none" disables audio entirely (flag omitted from command).
            # Any other value typed in is passed verbatim as the device name.
            options=["auto", "none"],
            group="Source",
            extended_help=(
                "**Audio Input** (`-a`)\n\n"
                "- `auto` — detects your default PipeWire/PulseAudio sink monitor "
                "  via `pactl get-default-sink` at build time. Records desktop audio.\n"
                "- `none` — no audio track (flag omitted entirely)\n\n"
                "For a custom device, type its name directly into this field. "
                "Run `gpu-screen-recorder --list-audio-devices` or "
                "`pactl list short sources` in a terminal to see available names."
            ),
        ),
        ConfigItem(
            label="Codec",
            key="audio_codec",
            scope="DEFAULT",
            type_="cycle",
            default="aac",
            options=["aac", "opus", "flac"],
            group="Codec",
            extended_help=(
                "**Audio Codec** (`-ac aac|opus|flac`)\n\n"
                "Only used when Input is not `none`."
            ),
        ),
        ConfigItem(
            label="Bitrate",
            key="audio_bitrate",
            scope="DEFAULT",
            type_="string",
            default="",
            group="Codec",
            extended_help=(
                "**Audio Bitrate** (`-ab`)\n\n"
                "e.g. `320k`. Empty = codec default (flag omitted). No effect on `flac`."
            ),
        ),
    ],

    # =========================================================================
    # TAB 3 — PROFILES  (user presets only + Save action)
    # =========================================================================
    3: [

        ConfigItem(
            label="Save",
            key="action_save",
            scope="DEFAULT",
            type_="action",
            default="bash ~/user_scripts/recorder/build_scripts.sh",
            group="Scripts",
            popup_message="toggle_record.sh written to ~/user_scripts/recorder/",
            extended_help=(
                "**Save & Build Scripts**\n\n"
                "Runs `build_scripts.sh` which reads `config.conf` and writes "
                "both toggle scripts with the current settings baked in.\n\n"
                "With no config at all it still produces a working minimum:\n"
                "`gpu-screen-recorder -w screen -c mp4 -f 60 -k h264 -q high -encoder gpu "
                "-o ~/Videos/Video_YYYY-MM-DD_HH-MM-SS.mp4`\n\n"
                "Only flags explicitly set to a non-`none` value are included — "
                "everything else is left to gsr's own defaults.\n\n"
                "**Hyprland:**\n"
                "```\n"
                "bind = SUPER, F9,  exec, ~/.config/gsr-tui/toggle_record.sh\n"
                "```"
            ),
        ),

    ],
}
