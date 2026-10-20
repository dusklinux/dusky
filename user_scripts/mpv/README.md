# Dusky Player audio features

## Optional whole-track waveform

Press `Ctrl+w`, or choose **Audio waveform on/off** in the right-click menu,
then move the pointer over the bottom timeline. The waveform appears inside
the existing seek bar and follows its visibility and scaling. Audio without
cover art also opens a player window.

Waveforms are **disabled by default**. Set `timeline_waveform=yes` in
`script-opts/dusky_player.conf` to enable them automatically. Keyboard changes
last for the current mpv session.

The first analysis decodes the selected audio track through a background FFmpeg
process. It retains up to approximately 2,048 peak bins, streams audio one bin
at a time, and creates no temporary audio files. Completed results are reused
while that track stays loaded; disabling analysis or changing files cancels an
unfinished scan. Drawing geometry is cached and limited to the visible pixel
resolution. The waveform describes the source audio before playback filters,
including silence trimming.

Only local, finite audio files are analyzed; cover art is allowed. Video files,
network streams, external audio tracks and virtual inputs are skipped. Native
Matroska demuxing is supported when its internal audio track is unambiguous.
Other supported formats use mpv's FFmpeg stream index for the selected track.

On the tested machine, a 2h48m AAC file took approximately 20 seconds to analyze,
with about 78 MiB peak worker memory. Cost depends on duration, codec, channels
and hardware. There is no analysis process while the feature is disabled.

Run `user_scripts/arch_setup_scripts/scripts/415_mpv_setup.sh` to deploy the
bundled implementation and bindings, then relaunch mpv. Setup checks FFmpeg as
a dependency, includes the waveform module in its bundle preflight, and
preserves existing player settings. Fresh installations receive the disabled
default; existing settings files can omit it because the Lua default is also off.

## Audio silence trimming

Dusky Player automatically trims quiet pauses in audio using stock mpv's
libavfilter bridge. No helper process, preprocessing, compiler, extra package,
or custom mpv build is needed at runtime or during offline setup.

- `Ctrl+s`: toggle trimming, keeping the selected level.
- `Alt+s`: cycle Off → Mild → Medium → Max → Max Plus → Off.
- Persistent defaults: `script-opts/dusky_silence.conf` in the mpv configuration
  directory. Set `enabled=no` or `level=mild`, `medium`, `max`, or `max-plus` as desired.
  The default is enabled, Max. Keyboard changes last for the current mpv session.

Run `user_scripts/arch_setup_scripts/scripts/415_mpv_setup.sh` to deploy the
bundled script and bindings. It preserves existing silence settings, backs up
changed files, and supports `XDG_CONFIG_HOME`. Relaunch mpv after installation.
The context menu also exposes the two bindings.

## Relationship to Pocket Casts

The measurements come from `AudioReadTask.swift`, `AudioUtils.swift`, and
`Constants.Audio.defaultFrameSize` in the supplied Pocket Casts iOS fork,
commit `14ab1798cd4974b9d8d3fa60e8471cba6363c935`.

This implementation is an approximation of that engine, not a verbatim port.
Pocket Casts analyzes nonoverlapping 1152-sample PCM blocks, averages stereo
channel RMS values, buffers gaps, discards selected blocks retrospectively, and
fades the joins. mpv Lua cannot perform those PCM buffer operations directly.

Here, FFmpeg's `silenceremove` uses a moving RMS window of `1152 / sample_rate`
seconds. It trims only when **all** channels are quiet, preserving a voice that
is present in only one channel. Existing audio filters remain after trimming.
No channel remixing, forced resampling, volume change, or pitch change is added.

| Level | RMS threshold | Approximate pause retained at 48 kHz | Pocket Casts long-gap retention at 48 kHz |
|---|---:|---:|---:|
| Mild | 0.0055 | 672 ms | 672 ms |
| Medium | 0.00511 | 576 ms | 576 ms |
| Max | 0.005 | 96 ms | 24 ms |
| Max Plus (optional) | 0.005 | 24 ms | No equivalent mode |

The Max difference is intentional: this filter cannot wait for a complete gap
and then shorten it retrospectively. Retaining approximately four blocks avoids
removing brief pauses that Pocket Casts also preserves. Exact boundaries depend
on the waveform and moving window; these values are not output guarantees.
Pocket Casts' fades are not reproduced by this filter. Matching perceived sound
quality requires listening to the same recordings in both players.

Max Plus keeps the same threshold and RMS window as Max, but shortens briefer
pauses and retains less silence. In the 48 kHz synthetic gap test it removes
72 ms more per gap. It is more aggressive than our existing Max; it is not a
claim of improved sound quality or an exact Pocket Casts mode. Max stays the
default. The first three levels' audio parameters are unchanged.

Leading silence and entirely silent recordings are retained: only the repeating
stop stage is enabled. This avoids introducing a separate onset-trimming stage
into the modes whose sound has already been accepted.

Video files are excluded even with `vid=no`; attached pictures and cover art
are allowed. Files up to six seconds are left alone. When the duration is known
from metadata at loading, trimming stops six seconds before the end,
conservatively matching Pocket Casts' integer-second ending protection.
Unknown-duration streams can be trimmed but cannot receive ending protection.
Their later growing duration estimates are ignored to avoid repeated filter
reinitialization. Compressed audio passthrough is bypassed.
The end guard follows source timestamp offsets when `rebase-start-time=no`.
It still depends on accurate duration metadata; format-specific duration guesses
are not applied. The audit records a reproduced metadata limitation.

Source timestamps are retained (`timestamp=copy`, `fix-pts=no`) so seeking,
chapters, the timeline and saved positions refer to the original media. Playback
speed stays under mpv's normal controls. Pausing does not run a polling loop;
configuration updates use property events and a coalesced one-shot callback.

Audio-only files also include music: disable trimming when pauses or very quiet
passages are part of the recording you want to hear.

## Optional standalone preprocessing

The separate [audio preprocessing tool](../audio/trim_silence_preprocess/README.md)
uses FFmpeg to prepare a RAM-backed audio file. MPV is only its optional player.
Normal MPV playback continues to use the live Lua filter.

## Verification

Silence integration tests require Python 3.15+, mpv and FFmpeg. Waveform
lifecycle tests require Lua or LuaJIT:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s user_scripts/mpv/tests -v
bash -n user_scripts/arch_setup_scripts/scripts/415_mpv_setup.sh
shellcheck user_scripts/arch_setup_scripts/scripts/415_mpv_setup.sh
lua user_scripts/mpv/tests/test_waveform.lua
luajit user_scripts/mpv/tests/test_waveform.lua
```

Tests render actual decoded audio through mpv, covering sample rates, mono and
stereo, levels, short pauses, quiet tones, silent endings, compressed formats,
cover art, HTTP playback, an unknown-duration stdin pipe, long gaps, video
exclusion, toggling, seeking, track/file changes, speed controls and preservation
of another audio filter. These are synthetic functional tests, not a listening
comparison or a guarantee for every recording or hardware output.

The supplied mpv source contains an `on_loaded` hook absent from the installed
mpv 0.41.0 package. The script uses the installed package's `on_preloaded` hook
for unambiguous single-track audio and actual decoder properties after track
selection. Verify mpv/libavfilter features again against the final ISO packages.

See [AUDIT.md](AUDIT.md) for the second-pass findings and regression evidence.

The waveform Lua tests exercise bundled-module loading, eligibility, duplicate
property notifications, cache reuse, cancellation, stale callbacks, worker
failures, malformed output and drawing-cache invalidation. Runtime verification
also exercised native Wayland rendering, full-length supplied audio, 8–192 kHz,
eight channels, silence, three-sample audio, cover art, track changes, real decode
failure and recovery, and 100 rapid toggles and file replacements. These checks
do not establish correctness for every possible media file or hardware output.
