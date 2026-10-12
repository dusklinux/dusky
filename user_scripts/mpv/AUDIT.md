# Silence trimming: second-pass audit

Reviewed the Lua script, settings, input bindings, offline installer, documentation,
and integration tests against the supplied Pocket Casts fork and mpv source.
The initial working-tree comparison was clean for these source files.
Baseline: Dusky commit `0dc620e383df4a0d0ec9dadde318bd0608e4e392`.
Pocket Casts source: `14ab1798cd4974b9d8d3fa60e8471cba6363c935`.
Runtime exercised: mpv 0.41.0, FFmpeg/libavfilter 9.0.2/12.1.102,
Bash 5.3.20, Python 3.15.0rc3, and the installed LuaJIT.

## Algorithm comparison

| Behavior | Pocket Casts source | mpv implementation and decision |
|---|---|---|
| Measurement | Nonoverlapping 1152-sample blocks | Moving window of 1152/sample-rate seconds; approximation retained |
| RMS thresholds | Mild 0.0055, Medium 0.00511, Max 0.005 | Identical numbers; unchanged |
| Stereo | Mean of the first two channel RMS values | Requires all channels to be quiet; intentionally preserves an isolated quiet speaker |
| Long-gap retention | 28/24/1 blocks for Mild/Medium/Max, from the actual inclusive reinsertion loop | Approximately 28/24/4 windows; existing parameters unchanged |
| Gap onset | Waits for the whole gap, then retrospectively removes blocks | Retains an initial portion before dropping samples; cannot reproduce retrospective cuts |
| Joins | Linear fades on retained quiet audio and returning speech | No equivalent fades; do not claim exact waveform reproduction |
| Very long gaps | Flushes after more than 1000 saved buffers | Native filter uses bounded window/queue storage instead of saving a whole gap |
| Ending | Integer-second test protects roughly the last 5–6 seconds | Six-second guard when duration is available at loading |
| Seek reset | Clears queued gaps and resets effects state | mpv lavfi reset frees/recreates its graph; seeking was exercised |
| Video | Explicitly disables trimming for video/HLS | Excludes real video even with vid=no; cover art remains eligible |
| Effects before measurement | VoiceBoostN can modify PCM before RMS calculation | Decoded PCM is measured before user filters; compare with Voice Boost disabled for like-for-like measurements |
| Leading/all-silent input | Can buffer and shorten leading silence | Repeating stop stage retains leading and entirely silent input; accepted behavior left intact |

Pocket Casts' strict `<`/`>` branches also leave an exact-threshold case without
an append operation. This port does not reproduce that control flow or claim
to have executed the Swift/iOS pipeline. All PCM tests execute the installed mpv
and libavfilter, rather than treating a reference model as iOS runtime proof.

## Corrections and extension

- Reproduced invalid runtime `level` silently removing the filter. Startup and
  runtime options now share the same warned fallback to Max.
- Filter reuse now compares the filter name and `fix-pts` as well as its graph.
  A runtime test changes `fix-pts` and confirms the script restores its required
  source-timestamp configuration when settings are reapplied.
- Reproduced an offset transport stream failing to trim with
  `rebase-start-time=no`: its timestamps began around 101 seconds while the guard
  compared against a roughly 15-second duration. The guard now includes the
  demuxer start time when rebasing is disabled. Both timestamp modes pass with
  preserved silent endings.
- Added optional `max-plus`, with the same 0.005 threshold and 1152-sample
  window as Max, and a zero stop-duration. A 48 kHz two-second gap retains about
  24 ms instead of 96 ms, saving another 72 ms. It also shortens briefer pauses;
  this is a deliberate change, not a universal sound-quality improvement.
  Max remains the default. No extra processing stage or dependency was added.

## Verification

- Ten integration test groups pass, including 32 rate/channel/level render
  combinations, compressed audio, HTTP, stdin, long gaps, endings, video,
  cover art, quiet tones, asymmetric stereo, all-silent input, runtime options,
  cycling, seeking, speed controls, track selection and passthrough.
- Separate old-versus-new rendering of all 24 original Mild/Medium/Max
  rate/channel combinations produced byte-identical PCM, including silent tails.
- Bash syntax and ShellCheck pass. Fresh-install and repeat-install checks
  preserve customized settings and require no backup on an unchanged rerun.
- Three accelerated 30-minute synthetic runs per mode: median process CPU
  2.943 s for old Max, 2.958 s for new Max, and 2.936 s for Max Plus. Median peak
  RSS was 71,104/70,924/70,928 KiB respectively. Differences are small; no CPU or
  memory improvement is claimed from these measurements.

## Practical limits

End protection depends on accurate metadata, just as the existing design states.
An offset Matroska fixture reported duration 115 seconds for 15 seconds of actual
audio, with start time 100 seconds. With rebasing enabled, that overestimate
prevented the guard from protecting the actual last three seconds. A format-based
subtraction was not added: other formats report duration as elapsed length, and
subtracting their start time would be incorrect. Unknown/growing stream durations
remain intentionally ignored after loading to avoid repeated filter rebuilds.

The tests are synthetic and use software outputs; they cannot prove equal
perceived sound quality for every recording or hardware output. The user's
positive listening result supports retaining the existing levels. Their settings
are preserved on deployment, and Max Plus is optional. Final ISO package features
still need verification when the ISO package set is finalized.
