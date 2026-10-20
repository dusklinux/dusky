# Silence preprocessing

`preprocess.py` creates a separate float32 WAV cache using the existing Max Plus
threshold, RMS window and all-channel rule. It requires Python 3.15+, FFmpeg and
ffprobe. It does not modify the original recording or installed mpv settings.
This is an experiment, not part of the automatic player or offline installer.

```sh
python3 user_scripts/audio/trim_silence_preprocess/preprocess.py recording.m4a --interactive --play
# Noninteractive example; the output must be on RAM-backed storage:
python3 user_scripts/audio/trim_silence_preprocess/preprocess.py recording.m4a /dev/shm/speech-cache.wav --start 2:23:00
# Trim silence, then bake in 2.5x playback speed while preserving pitch:
python3 user_scripts/audio/trim_silence_preprocess/preprocess.py recording.m4a /dev/shm/speech-2.5x.wav --speed 2.5
```

`--speed` accepts a finite multiplier of at least 1, including `2.5`, `3`, `4`
and higher values. The default is `1`, which bypasses tempo processing and
preserves the previous output. Speedup follows silence trimming, so the ending
guard still covers six seconds of source audio before acceleration. FFmpeg's
pitch-preserving `atempo` stages each run at most 2x to avoid its sample-skipping
mode above 2x. Tempo processing changes samples and can introduce audible
artifacts; very high speeds or very short excerpts may leave too little audio
to produce a cache. Duration is approximate because tempo processing uses
overlapping windows. Always rebuild from the original to change baked-in speed.

`--interactive` asks for a start time (Enter defaults to zero), optional source
duration (Enter defaults to the end), and output WAV path. It suggests an output
under `/dev/shm`, otherwise `/mnt/zram1`; it never substitutes an
SSD path. You can supply an output path and time flags as prompt defaults.
Invalid interactive times can be entered again; EOF or Ctrl+C cancels.
Times accept seconds, `MM:SS`, or `HH:MM:SS`, including fractional seconds.

`--start` selects the source offset; optional `--duration` limits the
input excerpt. Omit both to process the whole recording. Music is not detected
automatically: select the talking section yourself. Existing leading-silence
behavior and the six-second ending guard are retained for the selected excerpt.
The cache has no additional quantization of the float32 decoded samples. A JSON
report records source identity, parameters, output duration/size, wall time,
CPU time and peak processing RSS; a sibling log captures FFmpeg output.
The report includes `playback_speed`; `removed_seconds` is the total duration
reduction from both trimming and speedup, not a silence-only measurement.
Existing output/report/log files are never overwritten.

The default per-cache ceiling is **4 GiB**, adjustable with `--max-cache-gib`.
The tool checks the untrimmed excerpt's maximum size before decoding; an excerpt
that could exceed the budget is rejected even if silence removal might make it
fit. Free output space also limits the budget, reserving 128 MiB of headroom.
This conservative check still uses the unaccelerated excerpt when `--speed`
is set; speedup does not relax the preflight budget.
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

`--play` opens the completed cache in the installed MPV configuration with live
trimming disabled. For an existing cache, use
`mpv --script-opts-append=dusky_silence-enabled=no /dev/shm/speech-cache.wav`.
Keep live trimming disabled on this cache to avoid applying the filter twice.
Speed controls still work and multiply any baked-in `--speed` (use 1x in MPV
to hear the requested cache speed). Its timeline starts at zero and describes the
shortened audio: original timestamps, chapters and bookmarks are not mapped.
Changed trim settings require rebuilding from the original recording.

Uncompressed stereo at 48 kHz uses 384,000 bytes per remaining second (about
1.38 GB/hour). Playback still performs file reads, audio output and any speed
or other effects; only AAC decoding and silence analysis move into preparation.
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
```

The tests use MPV to compare output against its live filter; FFmpeg performs
preprocessing. MPV is otherwise needed only for optional `--play`.

See [AUDIT.md](AUDIT.md) for measurements and limitations.
