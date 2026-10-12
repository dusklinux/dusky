# Audio silence trimming

Dusky Player automatically trims quiet pauses in audio using stock mpv's
libavfilter bridge. No helper process, preprocessing, compiler, extra package,
or custom mpv build is needed at runtime or during offline setup.

- `Ctrl+s`: toggle trimming, keeping the selected level.
- `Alt+s`: cycle Off → Mild → Medium → Max → Off.
- Persistent defaults: `script-opts/dusky_silence.conf` in the mpv configuration
  directory. Set `enabled=no` or `level=mild`, `medium`, or `max` as desired.
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

The Max difference is intentional: this filter cannot wait for a complete gap
and then shorten it retrospectively. Retaining approximately four blocks avoids
removing brief pauses that Pocket Casts also preserves. Exact boundaries depend
on the waveform and moving window; these values are not output guarantees.
Pocket Casts' fades are not reproduced by this filter. Matching perceived sound
quality requires listening to the same recordings in both players.

Video files are excluded even with `vid=no`; attached pictures and cover art
are allowed. Files up to six seconds are left alone. When the duration is known
from metadata at loading, trimming stops six seconds before the end,
conservatively matching Pocket Casts' integer-second ending protection.
Unknown-duration streams can be trimmed but cannot receive ending protection.
Their later growing duration estimates are ignored to avoid repeated filter
reinitialization. Compressed audio passthrough is bypassed.

Source timestamps are retained (`timestamp=copy`, `fix-pts=no`) so seeking,
chapters, the timeline and saved positions refer to the original media. Playback
speed stays under mpv's normal controls. Pausing does not run a polling loop;
configuration updates use property events and a coalesced one-shot callback.

Audio-only files also include music: disable trimming when pauses or very quiet
passages are part of the recording you want to hear.

## Verification

Requires Python 3.15+, mpv and ffmpeg for tests only:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s user_scripts/mpv/tests -v
bash -n user_scripts/arch_setup_scripts/scripts/415_mpv_setup.sh
shellcheck user_scripts/arch_setup_scripts/scripts/415_mpv_setup.sh
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
