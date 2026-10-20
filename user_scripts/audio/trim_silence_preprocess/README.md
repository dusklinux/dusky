# Silence preprocessing

`preprocess.py` creates separate AAC `.m4a` or float32 PCM `.wav` audio using the
existing Max Plus threshold, RMS window and all-channel rule. It requires Python 3.15+, FFmpeg and
ffprobe. It does not modify the original recording or installed mpv settings.
This is an experiment, not part of the automatic player or offline installer.

```sh
python3 user_scripts/audio/trim_silence_preprocess/preprocess.py recording.m4a --interactive --play
# Trim silence, then bake in 2.5x playback speed while preserving pitch:
python3 user_scripts/audio/trim_silence_preprocess/preprocess.py recording.m4a /dev/shm/speech-2.5x.m4a --speed 2.5
# Higher AAC bitrate, with a source offset:
python3 user_scripts/audio/trim_silence_preprocess/preprocess.py recording.m4a /dev/shm/speech.m4a --bitrate 192 --start 2:23:00
# Float32 PCM output remains available by choosing .wav:
python3 user_scripts/audio/trim_silence_preprocess/preprocess.py recording.m4a /dev/shm/speech.wav
```

The output extension selects the encoding. Interactive suggestions use `.m4a`
and include the requested speed in the filename. AAC defaults to **128 kbps**
for mono/stereo, or 64 kbps per channel for multichannel recordings; `--bitrate`
sets an integer kbps value for `.m4a` only. Channels are retained. FFmpeg selects
a supported AAC sample rate when the source rate cannot be encoded directly;
the report records source and output rates. WAV preserves the source rate.
AAC adds lossy compression; WAV avoids that additional encoding loss. Rebuild
from the original to avoid accumulating lossy compression or speed adjustments.
Trimming, speedup and encoding share one FFmpeg pipeline with no intermediate WAV.

`--speed` accepts a finite multiplier of at least 1, including `2.5`, `3`, `4`
and higher values. The default is `1`, which bypasses tempo processing and
preserves the previous decoded PCM output when choosing WAV. Speedup follows silence trimming, so the ending
guard still covers six seconds of source audio before acceleration. FFmpeg's
pitch-preserving `atempo` stages each run at most 2x to avoid its sample-skipping
mode above 2x. Tempo processing changes samples and can introduce audible
artifacts; very high speeds or very short excerpts may leave too little audio
to produce a cache. Duration is approximate because tempo processing uses
overlapping windows. Always rebuild from the original to change baked-in speed.

`--interactive` asks for a start time (Enter defaults to zero), optional source
duration (Enter defaults to the end), and output audio path. It suggests an output
under `/dev/shm`, otherwise `/mnt/zram1`; it never substitutes an
SSD path. You can supply an output path and time flags as prompt defaults.
Invalid interactive times can be entered again; EOF or Ctrl+C cancels.
Times accept seconds, `MM:SS`, or `HH:MM:SS`, including fractional seconds.

`--start` selects the source offset; optional `--duration` limits the
input excerpt. Omit both to process the whole recording. Music is not detected
automatically: select the talking section yourself. Existing leading-silence
behavior and the six-second ending guard are retained for the selected excerpt.
WAV has no additional quantization of the float32 decoded samples. Descriptive
tags (title, artist, album, album artist, composer, genre, date, track, disc,
comment, description and copyright) are retained where supported by the output
container. Artwork, other streams, replay gain and original chapters are omitted.
Chapters and gain data would need rebuilding after trimming and speedup. A JSON
report records source identity, parameters, output duration/size, wall time,
CPU time and peak processing RSS; a sibling log captures FFmpeg output.
The report includes `playback_speed`; `removed_seconds` is the total duration
reduction from both trimming and speedup, not a silence-only measurement. It also
records codec, target AAC bitrate, output size estimate and actual sample rate.
Existing output/report/log files are never overwritten.

The default per-cache ceiling is **4 GiB**, adjustable with `--max-cache-gib`.
For WAV, preflight uses the untrimmed, unaccelerated PCM size bound. For AAC,
it estimates the untrimmed duration divided by speed at the requested bitrate,
plus 15% encoding headroom, estimated packet tables and 64 KiB for container
overhead. Neither calculation assumes silence savings. AAC is an estimate,
not a promised maximum: Linux `RLIMIT_FSIZE` enforces the actual byte budget
during encoding for both formats (and the child log). A limit failure discards
temporary output and retains the diagnostic log. Post-encode checks also verify
codec, channel count, duration and size before publication. WAV requires its
source sample rate; AAC may resample to an encoder-supported rate.
Free output space limits the budget, reserving 128 MiB of headroom.
On RAM-backed tmpfs/ramfs, the ceiling additionally uses at most one quarter of
currently available RAM, including ancestor cgroup memory limits. This is a
preparation-time check, not a reservation against other concurrent workloads.
Output is restricted to tmpfs/ramfs under `/dev/shm` or `/mnt/zram1`, including
temporary audio, reports and logs.
Disk output is rejected before creating output directories. Ordinary tmpfs is
accepted only when there is no active disk-backed swap: active swap devices must
be zram without backing storage. A `noswap` tmpfs or ramfs does not need that
check. The tool checks the current swap configuration; it does not change it or
prevent an administrator from enabling disk swap/writeback later. A dedicated
`noswap` tmpfs provides the stronger guarantee for the output file. See the
[kernel tmpfs documentation](https://docs.kernel.org/filesystems/tmpfs.html).
On a low-RAM machine, choose a shorter excerpt or continue using live trimming.
No cache is automatically relocated or
evicted, and the ceiling applies to each cache rather than an entire library.

`--play` opens the completed audio in the installed MPV configuration at 1x with live
trimming disabled. For an existing cache, use
`mpv --speed=1 --script-opts-append=dusky_silence-enabled=no /dev/shm/speech-2.5x.m4a`.
Keep live trimming disabled on this cache to avoid applying the filter twice.
Speed controls still work and multiply any baked-in `--speed` (use 1x in MPV
to hear the requested cache speed). Its timeline starts at zero and describes the
shortened audio: original timestamps, chapters and bookmarks are not mapped.
Changed trim settings require rebuilding from the original recording.

Uncompressed stereo at 48 kHz uses 384,000 bytes per remaining second (about
1.38 GB/hour); AAC at 128 kbps is approximately 57.6 MB/hour plus container
overhead. These figures refer to finished output duration. Playback still performs
file reads, audio output and any speed or other effects. Silence analysis moves
into preparation; AAC output also requires AAC decoding during playback.
The cache need not live in process RAM. On a tmpfs its file storage itself uses
RAM, separate from the measured process RSS. Desktop preparation times and
accelerated software-output benchmarks do not establish iPhone battery savings.
Compressed-file seeking can also differ slightly between FFmpeg and MPV; the
full-recording comparison in the audit found a difference only at the initial
seek boundary, despite identical output on already-decoded test excerpts.

## Verification

For automated episode-start detection, named audio profiles and saved MPV
verification results, see the [detector documentation](auto_detect_when_wavfrom_has_diff_pattern/README.md).

```sh
python3 -B -m unittest discover -s user_scripts/audio/trim_silence_preprocess/tests -v
# Keep preprocessing-test fixtures under a chosen RAM directory:
PREPROCESS_TEST_TMPDIR=/mnt/zram1/audio_test TMPDIR=/mnt/zram1/audio_test python3 -B -m unittest discover -s user_scripts/audio/trim_silence_preprocess/tests -p test_preprocess.py -v
```

The tests use MPV to compare output against its live filter; FFmpeg performs
preprocessing. MPV is otherwise needed only for optional `--play`.

See [AUDIT.md](AUDIT.md) for historical PCM-cache measurements and limitations;
those performance figures do not describe AAC encoding or playback.
