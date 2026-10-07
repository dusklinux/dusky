# Dusky Converter

An interactive and scriptable media converter for the Linux ISO. Run
`./dusky_converter.py` for the terminal menu, or choose a subcommand:

```sh
python3 ~/user_scripts/media_converter/dusky_converter.py gif clip.mkv --preset web
python3 ~/user_scripts/media_converter/dusky_converter.py gif clip.mkv --start 10 --duration 5 --width 640
python3 ~/user_scripts/media_converter/dusky_converter.py gif clip.mkv --interpolate --fps 60
python3 ~/user_scripts/media_converter/dusky_converter.py premiere recordings/ --output-dir editing/
python3 ~/user_scripts/media_converter/dusky_converter.py premiere clip.mkv --mode prores --fps 30
python3 ~/user_scripts/media_converter/dusky_converter.py video recordings/ --preset fast
python3 ~/user_scripts/media_converter/dusky_converter.py audio clip.mkv --format mp3 --track 1
python3 ~/user_scripts/media_converter/dusky_converter.py info clip.mkv --json
```

Run `<subcommand> --help` for all options. Tab completes source paths in the
interactive prompt; enter the path directly, without shell quotes or escapes.
Use `--` before command-line paths beginning with `-`. Colors respect `NO_COLOR`,
and progress bars appear only on terminals. Reports use stdout; status and errors
use stderr.

## Dependencies and deployment

- Python **3.15+**, including explicit lazy imports and the built-in `frozendict`.
- FFmpeg and ffprobe with `libx264`, `prores_ks`, AAC, FLAC and `libmp3lame` encoders,
  plus the palette, scale and interpolation filters used by the selected feature.
- Optional `yt-dlp` for a single HTTP(S) URL. Downloads are temporary; the converted
  output defaults to the current directory. User yt-dlp configuration is ignored
  so playlists, output templates and postprocessors cannot change this workflow.

Tools are discovered through `PATH`. Missing tools produce an error; the converter
does not install packages. Verified on `/usr/local/bin/python3` 3.15.0rc3,
FFmpeg 9.0.2 (Arch package `2:9.0.2-2`) and yt-dlp 2026.08.19.
The development machine's packaged `/usr/bin/python3` is **3.14.7**; the ISO needs
the Python 3.15+ runtime on its launcher PATH before shipping. Final ISO package
versions remain a deployment verification requirement. No older-runtime fallback
is included.

## Behavior and presets

| Feature | Presets / behavior |
|---|---|
| GIF | `web`: 480px / 15fps; `balanced`: 720px / 24fps; `quality`: 960px / 30fps. Width and frame rate are caps; no forced upscaling or interpolation. Two passes use the same transforms and a global palette without buffering the whole video in memory. `--interpolate` generates frames at the requested rate and scales before the expensive interpolation step. |
| Premiere | `auto` copies H.264, HEVC, ProRes, DNxHD/DNxHR, MPEG-2 and MJPEG video into MOV; otherwise creates ProRes 422. HEVC receives the `hvc1` tag. Every audio track is retained; AAC and supported PCM tracks are copied, others become PCM 24-bit. `prores` encodes all audio as PCM 24-bit. |
| Video | H.264/AAC MP4: `fast` = CRF 23 / fast / 1920px; `balanced` = CRF 22 / medium / 1920px; `quality` = CRF 18 / slow / 3840px. Inspired by HandBrake's quality-based H.264 presets. Lower CRF means higher quality and usually larger files. No guaranteed size reduction. All audio tracks become AAC at 192kbps each. |
| Audio | FLAC, PCM 24-bit WAV, VBR MP3 or AAC M4A. `--track` selects a zero-based audio track. FLAC preserves supported integer depth; decoded float / >24-bit sources become 24-bit. |
| Info | Duration, file size and every stream; `--json` writes a JSON array with ffprobe stream metadata. |

Conversion selects the first non-cover-art video and, where relevant, all audio
tracks. Extra video angles, subtitles, attachments and data streams are omitted
from converted outputs. Info still reports them. Directories are scanned
nonrecursively for common media extensions, in sorted order; duplicate inputs
are processed once. Colliding output names are rejected before conversion.

ProRes uses the detected source frame rate, with 30fps only when the source rate
is unavailable. `--fps` overrides it. MP4 retains variable timing unless `--fps`
requests CFR. CFR duplicates/drops frames; it does not repair existing recording
sync errors. Accurate `--start` / `--duration` trims are available for GIF, MP4,
audio and ProRes; Premiere remux rejects timing changes because stream-copy cuts
can depend on keyframe positions. Indexed inputs use fast seeking. MPEG transport,
program and raw H.264/HEVC streams decode through the requested start to retain
the required keyframe; long seeks in those formats can therefore be slower.
Duration limits also apply to the output timeline so delayed audio cannot extend
the clip. As with other frame-based editors, cuts are quantized to available
video frames/audio samples. GIF timing is quantized to hundredths of a
second by its format. GIF/MP4 presets target SDR and do not tone-map HDR; detected
PQ/HLG inputs produce a warning. ProRes 422 does not retain alpha.
GIF retains native odd dimensions. MP4 pads odd dimensions to even dimensions
for H.264 without stretching the image by a pixel.

Explicit file inputs default beside each source: GIF keeps the stem, MP4 adds
`_compressed`, audio adds `_audioN`, and Premiere uses `premiere_ready/`. Directory
inputs instead use `gifs/`, `converted_video/`, `extracted_audio/` or
`premiere_ready/` within each source directory, keeping generated files out of
later nonrecursive scans. Explicit ProRes mode adds `_ProRes`. `-o` names one
output; `--output-dir` overrides the default destination.
Existing outputs require `--overwrite`. Even with overwrite enabled, FFmpeg writes
to a temporary file on the destination filesystem and publishes it atomically
only after successful completion. No-overwrite publication uses Linux
`renameat2(RENAME_NOREPLACE)`, including destinations without hard-link support
such as exFAT. Every selected output stream must contain packets; an empty track
causes failure instead of silently publishing an incomplete conversion.
Conversion errors and SIGINT/SIGTERM remove temporary output. Cancellation kills
the entire temporary child process group, including TERM-ignoring helpers,
before removing its files. Batch failures do not
stop subsequent conversions, but the overall exit status is nonzero. SIGINT and
SIGTERM return 130 and 143; a closed report/help pipeline returns 141 without a
Python flush traceback. A power loss or SIGKILL cannot execute cleanup and
may leave a `.dusky-*` temporary directory; atomic publication protects the old
destination until conversion succeeds. Remuxing preserves compressed video
packets and does not fully decode them to validate source integrity.

The former Bash scripts have been replaced. Rofi and control-center GIF buttons
now launch `dusky_converter.py gif`.

## Verification

```sh
python3 -X dev -W default -m unittest discover -s ~/user_scripts/media_converter/tests -v
```

Optional `DUSKY_TEST_EXFAT_DIR` enables the regression test on an already-mounted
test exFAT filesystem. `DUSKY_TEST_LOG_DIR` retains each ordinary CLI invocation's
arguments, status, stdout and stderr for inspection; both paths are test-only.

Tests generate small media fixtures and exercise codecs, stream mapping, packet
preservation, faststart layout, frame rates, trimming, dimensions/SAR, filenames,
batch errors, output collisions, publication races, partial failures, process-group
cancellation, terminal interaction and a real yt-dlp download from a local HTTP
server. External website extractors and Premiere itself require separate testing
in those environments.

The implementation uses the installed tool manuals/help and current primary
documentation: [FFmpeg CLI](https://ffmpeg.org/ffmpeg.html),
[Python argparse](https://docs.python.org/3.15/library/argparse.html),
[Python subprocess](https://docs.python.org/3.15/library/subprocess.html) and
[Python import syntax](https://docs.python.org/3.15/reference/simple_stmts.html#the-import-statement).
Linux publication follows the documented
[renameat2 interface](https://man7.org/linux/man-pages/man2/rename.2.html).

## Audit results (2026-10-08)

1. **Structure:** replaced the two Bash implementations with shared probing,
   process management and atomic output, plus one module per conversion feature.
   Removed implicit package installation, forced 1080p/60fps GIF processing,
   unconditional mapping of unsupported MOV streams and silent batch-success
   exit codes. Updated both existing GIF launchers.
2. **Details:** checked installed ffmpeg, ffprobe, yt-dlp and kitty manuals/help,
   along with selected filter/encoder/muxer help. Adopted Python 3.15 lazy imports,
   immutable preset maps, typed paths/data, structured subprocess arguments and
   documented per-stream codec / frame-rate controls. FFmpeg stops on errors
   rather than accepting partially decoded output as a successful conversion.
3. **Verification:** **24 tests passed in 21.027 seconds** under
   `python3 -X dev -W default`, with no runtime warnings. Python compilation,
   launcher Bash syntax, control-center TOML parsing and tracked diff whitespace
   checks also passed. External website extractors, desktop button activation and
   actual Premiere import were not exercised.

A 0.3-second, 128×72 / 24fps synthetic clip took **23.357 seconds** with the old
default GIF filter commands and **0.448 seconds median** across three runs of the
new default command. Outputs were **4,839,924** and **43,639 bytes** respectively.
The old default forced 1080p and interpolated to 60fps; the new default retained
source resolution/rate. This measures the default-policy change, not an equal-
quality encoder speedup. GIF probing decreased from three ffprobe processes to
one; Premiere probing decreased from two to one (concrete command comparison).

### Second pass

Reproduced and fixed six additional failures: exFAT new-output publication,
TERM-ignoring helper survival, closed-pipeline flush errors, repeated directory
jobs consuming previous outputs, transport-stream trims silently losing video,
and duration limits being extended by delayed audio.

**38 tests passed in 31.767 seconds**, with the exFAT test enabled, under Python
development warnings. All 82 recorded ordinary CLI invocation logs were checked
for expected statuses and unexpected error messages. Separate tests exercise real
encoder cancellation, four competing publishers, large stderr output, corrupted
packets, empty tracks, cover art, bit depth and surround audio.

A separate stress run completed 12 conversions covering 4K resolution, fractional
frame rates, 120fps inputs, rotation, interpolation and unusual output names;
all 12 outputs passed a full FFmpeg decode with no errors. A 30-file directory
batch and its overwrite rerun completed all 60 conversions; every final batch
output also passed full decoding. The real control-center command builder's
shell expansion and the Rofi handler's argument forwarding were exercised
without opening desktop windows. Actual GUI button activation and Premiere
import remain external validation tasks. The temporary exFAT filesystem was
created solely for this audit and unmounted afterward.
