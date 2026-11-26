#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: DUSKY KOKORO TTS CONFIGURATION SCHEMA
===============================================================================
Target: ~/.config/dusky-kokoro/config.toml
Engine: kokoro (Dusky Kokoro Neural TTS Engine)
===============================================================================
Features:
- Full support for all 54 Kokoro voices with traits and grades from VOICES.md
- Independent controls for neural generation speed vs mpv playback tempo
- Multi-voice blending with automatic weight normalization
- Hardware selection (CUDA, TensorRT, MIGraphX, OpenVINO, CPU) and power control
- Direct trigger action integration for hot-reloading and audio preview
- Zero hardcoded usernames (portable across all Linux machines and accounts)
===============================================================================
"""

import os
import shlex
import subprocess
import sys
from pathlib import Path

_DUSKY_TUI_ROOT = Path(
    os.environ.get("DUSKY_TUI_ROOT") or Path.home() / "user_scripts" / "dusky_tui"
).expanduser()
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING & METADATA
# =============================================================================
ENGINE_TYPE = "kokoro"
TARGET_FILE = os.environ.get("DUSKY_CONFIG") or str(
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "dusky-kokoro"
    / "config.toml"
)
APP_TITLE = "Dusky Kokoro TTS"

DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Presets"

_TRIGGER_CMD = shlex.quote(str(Path(__file__).resolve().with_name("trigger.sh")))

# =============================================================================
# 2. VOICE DEFINITIONS & HINTS CATALOG (54 VOICES FROM VOICES.MD)
# =============================================================================
VOICE_OPTIONS = [
    # Top Tier Voices First
    "af_heart", "af_bella", "af_nicole", "am_michael", "am_fenrir", "bf_emma", "bm_george",
    # American English (Female)
    "af_aoede", "af_kore", "af_sarah", "af_alloy", "af_nova", "af_sky", "af_jessica", "af_river",
    # American English (Male)
    "am_puck", "am_echo", "am_eric", "am_liam", "am_onyx", "am_adam", "am_santa",
    # British English
    "bf_isabella", "bf_alice", "bf_lily", "bm_fable", "bm_daniel", "bm_lewis",
    # Japanese
    "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo",
    # Mandarin Chinese
    "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi", "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
    # Spanish
    "ef_dora", "em_alex", "em_santa",
    # French
    "ff_siwis",
    # Hindi
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
    # Italian
    "if_sara", "im_nicola",
    # Brazilian Portuguese
    "pf_dora", "pm_alex", "pm_santa",
]

VOICE_OPTIONS_V3 = ["none"] + VOICE_OPTIONS

VOICE_HINTS = [
    "🇺🇸 American Female (Grade A - Highest Quality, Warm & Natural)",
    "🇺🇸 American Female (Grade A- - Expressive, Dynamic & Clear)",
    "🇺🇸 American Female (Grade B- - Studio Podcast Style)",
    "🇺🇸 American Male (Grade C+ - Warm, Deep & Natural)",
    "🇺🇸 American Male (Grade C+ - Resonant & Authoritative)",
    "🇬🇧 British Female (Grade B- - Articulate BBC RP Narrator)",
    "🇬🇧 British Male (Grade C - Classic Warm British Narrator)",
    "🇺🇸 American Female (Grade C+ - Soft & Calm)",
    "🇺🇸 American Female (Grade C+ - Balanced & Articulate)",
    "🇺🇸 American Female (Grade C+ - Bright & Friendly)",
    "🇺🇸 American Female (Grade C - Metallic / Robotic)",
    "🇺🇸 American Female (Grade C - Crisp & Fast)",
    "🇺🇸 American Female (Grade C- - Light & Breathy)",
    "🇺🇸 American Female (Grade D - Casual Conversation)",
    "🇺🇸 American Female (Grade D - Deep Female Tone)",
    "🇺🇸 American Male (Grade C+ - Playful & Energetic)",
    "🇺🇸 American Male (Grade D - Radio Broadcast Tone)",
    "🇺🇸 American Male (Grade D - Young Conversational)",
    "🇺🇸 American Male (Grade D - Clean Narrator)",
    "🇺🇸 American Male (Grade D - Deep Baritone)",
    "🇺🇸 American Male (Grade F+ - Direct Speech)",
    "🇺🇸 American Male (Grade D- - Character Santa Voice)",
    "🇬🇧 British Female (Grade C - Warm British Tone)",
    "🇬🇧 British Female (Grade D - Crisp British Accent)",
    "🇬🇧 British Female (Grade D - Youthful British Accent)",
    "🇬🇧 British Male (Grade C - Storybook British Tone)",
    "🇬🇧 British Male (Grade D - Formal British Accent)",
    "🇬🇧 British Male (Grade D+ - Deep British Accent)",
    "🇯🇵 Japanese Female (Grade C+ - Natural Japanese Narration)",
    "🇯🇵 Japanese Female (Grade C - Folk Storyteller Voice)",
    "🇯🇵 Japanese Female (Grade C- - High-Pitched Japanese)",
    "🇯🇵 Japanese Female (Grade C - Storybook Voice)",
    "🇯🇵 Japanese Male (Grade C- - Deep Japanese Voice)",
    "🇨🇳 Mandarin Chinese Female (Grade D - Beijing Style)",
    "🇨🇳 Mandarin Chinese Female (Grade D - Soft Chinese Voice)",
    "🇨🇳 Mandarin Chinese Female (Grade D - Standard Mandarin)",
    "🇨🇳 Mandarin Chinese Female (Grade D - Expressive Mandarin)",
    "🇨🇳 Mandarin Chinese Male (Grade D - Formal Chinese Tone)",
    "🇨🇳 Mandarin Chinese Male (Grade D - Youthful Chinese Voice)",
    "🇨🇳 Mandarin Chinese Male (Grade D - Gentle Chinese Accent)",
    "🇨🇳 Mandarin Chinese Male (Grade D - Broadcast Style)",
    "🇪🇸 Spanish Female (Grade C - Natural Castilian Accent)",
    "🇪🇸 Spanish Male (Grade C - Conversational Spanish)",
    "🇪🇸 Spanish Male (Grade D - Character Santa Voice)",
    "🇫🇷 French Female (Grade C - Classical French Articulation)",
    "🇮🇳 Hindi Female (Grade C - Standard Hindi Accent)",
    "🇮🇳 Hindi Female (Grade C - Conversational Hindi Accent)",
    "🇮🇳 Hindi Male (Grade C - Deep Hindi Tone)",
    "🇮🇳 Hindi Male (Grade C - Clear Hindi Voice)",
    "🇮🇹 Italian Female (Grade C - Expressive Italian Accent)",
    "🇮🇹 Italian Male (Grade C - Natural Italian Voice)",
    "🇧🇷 Brazilian Portuguese Female (Grade C - Natural Carioca/Paulista)",
    "🇧🇷 Brazilian Portuguese Male (Grade C - Conversational Portuguese)",
    "🇧🇷 Brazilian Portuguese Male (Grade D - Character Santa Voice)",
]

VOICE_HINTS_V3 = ["No third voice blended (Dual blend or single voice only)"] + VOICE_HINTS

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "Voices",
    "Speed & Timing",
    "Playback & Audio",
    "Engine & GPU",
    "Text & Pauses",
    "Presets",
]

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: VOICES & MULTI-SPEAKER BLENDING
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Blend Voices",
            key="blend",
            scope="voice",
            type_="bool",
            default=True,
            group="Voice Mode",
            extended_help="**Multi-Voice Blending**\n\nWhen enabled, blends Voice 1 and Voice 2 (and optionally Voice 3) into a unique hybrid speaker vector. When disabled, only Voice 1 is synthesized.",
        ),
        ConfigItem(
            label="Primary Voice (V1)",
            key="voice_1",
            scope="voice",
            type_="picker",
            default="af_heart",
            options=VOICE_OPTIONS,
            hints=VOICE_HINTS,
            group="Primary Voice",
            extended_help="**Primary Speaker**\n\nThe base voice for speech synthesis. `af_heart` is the highest-rated American English female voice (Grade A).",
        ),
        ConfigItem(
            label="Primary Weight (W1)",
            key="weight_1",
            scope="voice",
            type_="float",
            default=0.40,
            min_val=0.05,
            max_val=1.00,
            step=0.05,
            group="Primary Voice",
            extended_help="**Primary Voice Proportion**\n\nRelative weight of Voice 1 in the blend. All voice weights are automatically normalized ($W_1 + W_2 + W_3 = 1.0$).",
        ),
        ConfigItem(
            label="Secondary Voice (V2)",
            key="voice_2",
            scope="voice",
            type_="picker",
            default="af_bella",
            options=VOICE_OPTIONS,
            hints=VOICE_HINTS,
            group="Secondary Voice",
            extended_help="**Secondary Speaker**\n\nSecondary voice blended with Voice 1. `af_bella` adds warmth and dynamic expression (Grade A-).",
        ),
        ConfigItem(
            label="Secondary Weight (W2)",
            key="weight_2",
            scope="voice",
            type_="float",
            default=0.60,
            min_val=0.00,
            max_val=1.00,
            step=0.05,
            group="Secondary Voice",
            extended_help="**Secondary Voice Proportion**\n\nRelative weight of Voice 2 in the blend. Set to 0.00 to disable secondary voice blending.",
        ),
        ConfigItem(
            label="Third Voice (V3)",
            key="voice_3",
            scope="voice",
            type_="picker",
            default="none",
            options=VOICE_OPTIONS_V3,
            hints=VOICE_HINTS_V3,
            group="Third Voice",
            extended_help="**Third Speaker (Optional)**\n\nSelect `none` for standard dual blend, or pick a 3rd voice for complex multi-accent blending.",
        ),
        ConfigItem(
            label="Third Weight (W3)",
            key="weight_3",
            scope="voice",
            type_="float",
            default=0.00,
            min_val=0.00,
            max_val=1.00,
            step=0.05,
            group="Third Voice",
            extended_help="**Third Voice Proportion**\n\nRelative weight of Voice 3. Ignored if Voice 3 is set to `none` or 0.00.",
        ),
        ConfigItem(
            label="Language Tag",
            key="lang",
            scope="voice",
            type_="cycle",
            default="auto",
            options=["auto", "en-us", "en-gb", "ja", "cmn", "es", "fr-fr", "hi", "it", "pt-br"],
            group="Language & Spec",
            extended_help="**G2P Phonemizer Language**\n\n`auto` infers the language directly from the primary voice prefix (`a` -> `en-us`, `b` -> `en-gb`, `j` -> `ja`, `z` -> `cmn`, etc.).",
        ),
        ConfigItem(
            label="Compiled Voice Spec",
            key="spec",
            scope="voice",
            type_="string",
            default="af_heart:0.4,af_bella:0.6",
            group="Language & Spec",
            extended_help="**Kokoro Vector Specification**\n\nUnderlying normalized spec passed to the ONNX runtime. Automatically compiled from voice weights if left empty.",
        ),
        ConfigItem(
            label="Test Voice (Speak Sample)",
            key="action_test_voice",
            scope="voice",
            type_="action",
            default=f'{_TRIGGER_CMD} --text "Hello! This is Dusky Kokoro neural speech synthesis."',
            group="Actions & Audio Test",
            extended_help="**Voice Preview**\n\nSynthesizes a short test utterance through the running daemon to audition current voice blends.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: SPEED & TIMING
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Speech Gen Speed (Kokoro)",
            key="speed",
            scope="voice",
            type_="float",
            default=1.00,
            min_val=0.50,
            max_val=2.00,
            step=0.05,
            group="Speed Controls",
            extended_help="**Neural Duration-Model Speed**\n\nControls the internal speech pacing predicted by Kokoro's duration neural network (0.50x to 2.00x). Affects the physical length and pacing of spoken phonemes.",
        ),
        ConfigItem(
            label="MPV Playback Speed (Tempo)",
            key="mpv_speed",
            scope="playback",
            type_="float",
            default=1.00,
            min_val=0.50,
            max_val=2.00,
            step=0.05,
            group="Speed Controls",
            extended_help="**Pitch-Preserving MPV Tempo**\n\nSecond-stage speed filter applied in real time via mpv `scaletempo2` (0.50x to 2.00x). Preserves natural voice pitch completely.",
        ),
        ConfigItem(
            label="Sentence Pause (ms)",
            key="sentence_pause_ms",
            scope="text",
            type_="int",
            default=140,
            min_val=0,
            max_val=1000,
            step=20,
            group="Pause Durations",
            extended_help="**Sentence Cadence**\n\nDuration of silence inserted between consecutive sentences in milliseconds.",
        ),
        ConfigItem(
            label="Paragraph Pause (ms)",
            key="paragraph_pause_ms",
            scope="text",
            type_="int",
            default=380,
            min_val=0,
            max_val=2000,
            step=50,
            group="Pause Durations",
            extended_help="**Paragraph Cadence**\n\nDuration of silence inserted between paragraphs or double newlines.",
        ),
        ConfigItem(
            label="Trim Leading/Trailing Silence",
            key="trim_silence",
            scope="text",
            type_="bool",
            default=True,
            group="Pause Durations",
            extended_help="**Audio Trimming**\n\nTrims dead silence from the start and end of generated audio segments for smooth continuous playback.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: PLAYBACK & AUDIO
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Audio Volume",
            key="volume",
            scope="playback",
            type_="int",
            default=100,
            min_val=0,
            max_val=150,
            step=5,
            group="Playback Controls",
            extended_help="**Playback Volume**\n\nOutput audio volume percentage in mpv (0% to 150%).",
        ),
        ConfigItem(
            label="Show MPV Window",
            key="window",
            scope="playback",
            type_="bool",
            default=True,
            group="Playback Controls",
            extended_help="**Mini Player Window**\n\nShows a compact floating mpv window with playback controls. Allows `[Space]` pause and `[q]` stop hotkeys.",
        ),
        ConfigItem(
            label="User MPV Config",
            key="use_user_mpv_config",
            scope="playback",
            type_="bool",
            default=False,
            group="Playback Controls",
            extended_help="**Load MPV User Configuration**\n\nDisabled by default (`--no-config`) for fast, isolated TTS playback without video-player scripts, shaders, or IPC socket collisions. Enable to load `~/.config/mpv/` (such as Dusky Player UI).",
        ),
        ConfigItem(
            label="Window Geometry",
            key="window_geometry",
            scope="playback",
            type_="cycle",
            default="420x96",
            options=["420x96", "360x80", "500x120", "300x60"],
            group="Playback Controls",
            extended_help="**Floating Window Dimensions**\n\nWidth x Height pixel geometry for the MPV player window.",
        ),
        ConfigItem(
            label="Prefetch Segments",
            key="prefetch_segments",
            scope="playback",
            type_="int",
            default=4,
            min_val=1,
            max_val=16,
            step=1,
            group="Playback Controls",
            extended_help="**Buffer Pipeline Ahead**\n\nNumber of synthesized speech segments generated ahead of current playback position.",
        ),
        ConfigItem(
            label="Archive Audio to WAV",
            key="enabled",
            scope="archive",
            type_="bool",
            default=True,
            group="Audio Archiving",
            extended_help="**WAV Archiver**\n\nAutomatically saves completed speech jobs as pristine WAV audio files in the audio cache directory (`~/.cache/dusky-kokoro/audio`).",
        ),
        ConfigItem(
            label="Archive Bit Depth",
            key="bit_depth",
            scope="archive",
            type_="cycle",
            default=16,
            options=[16, 24],
            group="Audio Archiving",
            extended_help="**Audio Resolution**\n\n16-bit PCM (standard CD quality, half the pipe bandwidth) or 24-bit PCM (studio high resolution).",
        ),
        ConfigItem(
            label="Max Archive Files",
            key="max_files",
            scope="archive",
            type_="int",
            default=32,
            min_val=1,
            max_val=256,
            step=8,
            group="Audio Archiving",
            extended_help="**Rolling Cache Retention**\n\nMaximum number of archived WAV files preserved before older recordings are purged.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: ENGINE & GPU POWER
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Inference Provider",
            key="provider",
            scope="engine",
            type_="cycle",
            default="auto",
            options=["auto", "cuda", "tensorrt", "migraphx", "openvino", "cpu"],
            group="Hardware Backend",
            extended_help="**Compute Execution Provider**\n\n- `auto`: Auto-detect available GPU accelerator\n- `cuda`: NVIDIA GPUs supported by the installed CUDA runtime\n- `tensorrt`: NVIDIA TensorRT EP with disk engine cache\n- `migraphx`: AMD GPUs with a matching MIGraphX runtime\n- `openvino`: Intel Iris / Arc / NPU\n- `cpu`: CPU inference",
        ),
        ConfigItem(
            label="Model Precision",
            key="precision",
            scope="engine",
            type_="cycle",
            default="auto",
            options=["auto", "fp16-gpu", "f32", "int8", "fp16"],
            group="Hardware Backend",
            extended_help="**Model Weight Precision**\n\n- `auto`: Uses the tested `fp16-gpu` export on CPU and CUDA, and `f32` on OpenVINO.\n- `fp16-gpu`: 177 MB; sole default download and fastest tested export on both CUDA and CPU.\n- `f32` / `int8` / `fp16`: Opt-in exports installed via `kokoro_installer.sh --models`.",
        ),
        ConfigItem(
            label="GPU VRAM Limit (MB)",
            key="gpu_mem_limit_mb",
            scope="engine",
            type_="int",
            default=2048,
            min_val=0,
            max_val=8192,
            step=256,
            group="Hardware Backend",
            extended_help="**VRAM Memory Cap**\n\nLimits the execution provider memory arena. Total VRAM can exceed this cap because driver and other allocations are separate. 0 = unlimited.",
        ),
        ConfigItem(
            label="CPU Inference Threads",
            key="intra_op_threads",
            scope="engine",
            type_="int",
            default=0,
            min_val=0,
            max_val=32,
            step=1,
            group="Hardware Backend",
            extended_help="**ONNX Runtime Intra-Op Threads**\n\n`0` = automatic (up to 8 available CPUs on CPU backend; 2 threads on GPU backends). Set explicitly to override.",
        ),
        ConfigItem(
            label="Model Warmup JIT",
            key="warmup",
            scope="engine",
            type_="bool",
            default=False,
            group="Hardware Backend",
            extended_help="**Optional Warmup Synthesis**\n\nDisabled by default so cold starts do not add a throwaway synthesis pass before your first segment. Enable only for pre-warmed benchmarking.",
        ),
        ConfigItem(
            label="Model Idle Unload (s)",
            key="model_idle_timeout_s",
            scope="engine",
            type_="float",
            default=30.0,
            min_val=0.0,
            max_val=300.0,
            step=5.0,
            group="Power Management & Laptop Battery",
            extended_help="**VRAM Arena Release**\n\nUnloads the synthesis worker after this many seconds of inactivity (`0.0` unloads immediately after synthesis finishes).",
        ),
        ConfigItem(
            label="Process Idle Exit (s)",
            key="process_idle_timeout_s",
            scope="daemon",
            type_="float",
            default=30.0,
            min_val=10.0,
            max_val=600.0,
            step=10.0,
            group="Power Management & Laptop Battery",
            extended_help="**Discrete GPU D3cold Sleep**\n\nTerminates the daemon process after idling. Releases all `/dev/nvidia*` handles, allowing discrete laptop GPUs to enter 0-Watt D3cold sleep state.",
        ),
        ConfigItem(
            label="Exit When Idle",
            key="exit_when_idle",
            scope="daemon",
            type_="bool",
            default=True,
            group="Power Management & Laptop Battery",
            extended_help="**Socket-Activated Standby**\n\nWhen enabled, the daemon exits when idle and relies on `dusky_kokoro.socket` to relaunch on demand with 0% idle CPU and zero battery drain.",
        ),
        ConfigItem(
            label="Default Queue Mode",
            key="default_mode",
            scope="daemon",
            type_="cycle",
            default="interrupt",
            options=["interrupt", "enqueue"],
            group="Daemon Controls",
            extended_help="**Playback Conflict Resolution**\n\n- `interrupt`: Immediately stops current speech and starts new utterance (keeps model warm).\n- `enqueue`: Queues new speech behind current utterance.",
        ),
        ConfigItem(
            label="Desktop Notifications",
            key="desktop_notifications",
            scope="daemon",
            type_="bool",
            default=True,
            group="Daemon Controls",
            extended_help="**Status Notifications**\n\nSends libnotify notifications when speech jobs start, pause, or fail.",
        ),
        ConfigItem(
            label="Restart Daemon Service",
            key="action_restart_daemon",
            scope="daemon",
            type_="action",
            default=f"{_TRIGGER_CMD} --restart",
            group="Daemon Controls",
            extended_help="**Daemon Restart**\n\nExecutes `systemctl --user restart dusky_kokoro.service` directly.",
        ),
        ConfigItem(
            label="Reload Daemon Config",
            key="action_reload_daemon",
            scope="daemon",
            type_="action",
            default=f"{_TRIGGER_CMD} --reload",
            group="Daemon Controls",
            extended_help="**Instant Config Reload**\n\nSignals the running daemon to re-read `config.toml` over Unix socket IPC without unloading the model for voice/text/playback tweaks.",
        ),
        ConfigItem(
            label="Stop Daemon Service",
            key="action_stop_daemon",
            scope="daemon",
            type_="action",
            default=f"{_TRIGGER_CMD} --kill",
            group="Daemon Controls",
            extended_help="**Stop Daemon**\n\nStops the active daemon process without disabling socket activation.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 4: TEXT PROCESSING & SEGMENTATION
    # -------------------------------------------------------------------------
    4: [
        ConfigItem(
            label="Strip Citation Brackets",
            key="strip_citations",
            scope="text",
            type_="bool",
            default=True,
            group="Cleaning & Normalization",
            extended_help="**Citation Filter**\n\nRemoves academic citation markers like `[12]`, `[4, 5]`, `[1-3]`, and footnote refs `[^1]` from spoken text.",
        ),
        ConfigItem(
            label="Read Code Blocks",
            key="read_code_blocks",
            scope="text",
            type_="bool",
            default=False,
            group="Cleaning & Normalization",
            extended_help="**Code Block Handling**\n\nWhen False, skips reading verbatim code blocks (` ```python ... ``` `). When True, reads code verbatim.",
        ),
        ConfigItem(
            label="URL Mode",
            key="url_mode",
            scope="text",
            type_="cycle",
            default="domain",
            options=["domain", "placeholder", "omit"],
            group="Cleaning & Normalization",
            extended_help="**URL Speech Style**\n\n- `domain`: Speaks only the domain (e.g. 'github dot com').\n- `placeholder`: Speaks 'link'.\n- `omit`: Completely ignores URLs and email addresses.",
        ),
        ConfigItem(
            label="Emoji Mode",
            key="emoji_mode",
            scope="text",
            type_="cycle",
            default="strip",
            options=["strip", "name"],
            group="Cleaning & Normalization",
            extended_help="**Emoji Filter**\n\n- `strip`: Silently removes emojis from speech.\n- `name`: Reads emoji names out loud (e.g. 'thumbs up').",
        ),
        ConfigItem(
            label="First Segment Cap",
            key="first_segment_max_chars",
            scope="text",
            type_="int",
            default=110,
            min_val=40,
            max_val=240,
            step=10,
            group="Segmentation Tuning",
            extended_help="**Time-to-First-Audio Cap**\n\nMaximum weighted characters for the opening segment. Shorter opening segments cut start latency, especially on CPU.",
        ),
        ConfigItem(
            label="Target Segment Length",
            key="target_segment_chars",
            scope="text",
            type_="int",
            default=220,
            min_val=50,
            max_val=500,
            step=10,
            group="Segmentation Tuning",
            extended_help="**Segment Chunking**\n\nTarget character count per audio segment. Matches the ideal token sweet spot of Kokoro (100-200 phonemes).",
        ),
        ConfigItem(
            label="Max Segment Length",
            key="max_segment_chars",
            scope="text",
            type_="int",
            default=320,
            min_val=100,
            max_val=1000,
            step=20,
            group="Segmentation Tuning",
            extended_help="**Maximum Segment Cap**\n\nHard boundary for chunking long paragraphs to prevent model rushing.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 5: PRESETS & PROFILES
    # -------------------------------------------------------------------------
    5: [
        ConfigItem(
            label="Default Dual Blend (Heart + Bella)",
            key="preset_default_dual",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Voice Profiles",
            confirm_message="Apply Default Dual Blend profile (af_heart:0.4 + af_bella:0.6)?",
            preset_payload={
                "voice.blend": True,
                "voice.voice_1": "af_heart",
                "voice.weight_1": 0.40,
                "voice.voice_2": "af_bella",
                "voice.weight_2": 0.60,
                "voice.voice_3": "none",
                "voice.weight_3": 0.00,
                "voice.lang": "auto",
                "voice.spec": "af_heart:0.4,af_bella:0.6",
                "voice.speed": 1.00,
                "playback.mpv_speed": 1.00,
            },
            extended_help="**Recommended Default**\n\nBlends Kokoro's two best American female voices (Grade A & A-) for maximum warmth and articulation at 1.0x speed.",
        ),
        ConfigItem(
            label="Podcast Style (Bella + Nicole)",
            key="preset_podcast",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Voice Profiles",
            confirm_message="Apply Studio Podcast profile (af_bella + af_nicole)?",
            preset_payload={
                "voice.blend": True,
                "voice.voice_1": "af_bella",
                "voice.weight_1": 0.50,
                "voice.voice_2": "af_nicole",
                "voice.weight_2": 0.50,
                "voice.voice_3": "none",
                "voice.weight_3": 0.00,
                "voice.lang": "auto",
                "voice.spec": "af_bella:0.5,af_nicole:0.5",
                "voice.speed": 1.05,
                "playback.mpv_speed": 1.00,
            },
            extended_help="**Studio Broadcast Style**\n\nA punchy, clear conversational tone suitable for reading articles, documentation, and podcasts.",
        ),
        ConfigItem(
            label="British Classic (Emma + George)",
            key="preset_british",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Voice Profiles",
            confirm_message="Apply British Classic profile (bf_emma + bm_george)?",
            preset_payload={
                "voice.blend": True,
                "voice.voice_1": "bf_emma",
                "voice.weight_1": 0.60,
                "voice.voice_2": "bm_george",
                "voice.weight_2": 0.40,
                "voice.voice_3": "none",
                "voice.weight_3": 0.00,
                "voice.lang": "en-gb",
                "voice.spec": "bf_emma:0.6,bm_george:0.4",
                "voice.speed": 0.95,
                "playback.mpv_speed": 1.00,
            },
            extended_help="**British RP Narrator**\n\nBlends Britain's highest-rated female and male voices for classic audiobook-style narration.",
        ),
        ConfigItem(
            label="Speed Reader (1.30x)",
            key="preset_speed_reader",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Speed Profiles",
            confirm_message="Apply Speed Reader profile (1.30x)?",
            preset_payload={
                "voice.speed": 1.20,
                "playback.mpv_speed": 1.10,
                "text.sentence_pause_ms": 100,
                "text.paragraph_pause_ms": 250,
            },
            extended_help="**Rapid Listening**\n\nIncreases speech rate to ~1.30x composite speed with shortened pauses for power users reading documentation.",
        ),
        ConfigItem(
            label="Factory Reset All",
            key="preset_factory_reset",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="System",
            confirm_message="Reset all settings to original defaults?",
            preset_payload={"__ALL_DEFAULTS__": True},
            extended_help="**Factory Reset**\n\nReverts all parameters in `config.toml` to clean default settings.",
        ),
    ],
}

# =============================================================================
# 5. DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    script_path = Path(__file__).resolve()
    main_router = _DUSKY_TUI_ROOT / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path), *sys.argv[1:]]).returncode)
    print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
    sys.exit(1)
