# Standalone audio preprocessing audit

## Optional preprocessing audit — 2026-10-14

Reviewed the three preprocessing changes with file-scoped `gitdelta`, including
the new tool, tests and README. Normal MPV playback and the offline installer
continue to use the existing Lua filter. Preprocessing remains an explicitly
selected tool; `--play` passes the completed cache to installed MPV with live
trimming disabled. No automatic cache substitution, source-position mapping or
library-wide eviction policy is implemented.

The initial tool had no storage/RAM budget. The reviewed version adds a default
3 GiB per-cache ceiling, a conservative untrimmed-size preflight, output-space
headroom, and a quarter-of-available-memory bound on tmpfs/ramfs. Available memory
includes ancestor cgroup limits. Disk-backed output does not require allocating
the entire cache in process RAM. These checks do not reserve RAM or disk space
against concurrent workloads. Failed or interrupted preparation does not publish
an incomplete cache; source changes and concurrent destination creation are
detected. SIGTERM also cancels the child and discards temporary output. Diagnostic
logs remain after a failed run, so choose another output name before retrying.

Runtime: Python 3.15.0rc3, MPV 0.41.0, FFmpeg 9.0.2, systemd 262.

- 25 tests passed: the ten existing live-playback groups and fifteen cache
  tests, including 16/44.1/48/96/192 kHz, mono/stereo/six channels, isolated quiet
  audio in one channel, all-silent/leading-silence preservation, short excerpt
  boundaries, existing/concurrent output preservation, corrupt input, source
  changes, failure cleanup, interruption and paths containing spaces.
- The supplied 16,609.557333-second AAC recording was processed from source
  offset 8,580 seconds. The 8,029.557333-second excerpt produced 6,202.141042
  seconds of float32 PCM in a 2,381,622,288-byte WAV. Music before the selected
  offset and the original file were left intact.
- Two accelerated software-output runs measured median playback CPU times of
  50.272068 seconds live versus 5.314794 seconds cached. Initial preparation used
  37.259525 CPU seconds and 15.326132 wall seconds. These measurements establish
  a desktop processing tradeoff, not battery savings or whole-system RAM savings.
- Full preparation succeeded in an actual 512 MiB cgroup with swap disabled,
  writing to Btrfs: 15.292266 wall seconds, 37.193576 child CPU seconds and
  105,876 KiB peak child RSS. The service's memory peak reached the 512 MiB limit,
  including file-cache pages; it completed without OOM. Cached playback also
  succeeded under that limit (2.023-second service runtime, 5.392 CPU seconds).
- The same full-cache request to tmpfs was rejected before creation under
  512 MiB, 2 GiB, 3 GiB and 4 GiB cgroups, respecting the effective memory budget.
- A three-minute decoded speech excerpt and synthetic PCM fixtures matched live
  Max Plus byte for byte. Comparing directly against the full AAC recording
  exposed a 14-stereo-sample length difference (0.292 milliseconds): differences
  are confined to the initial 79 live/65 cached frames, at most 1.646 milliseconds
  at the selected seek boundary. The remaining 2,381,621,640 PCM bytes match
  exactly, including the ending. Exact equivalence across compressed seek paths
  is therefore not claimed. The reviewed tool's full WAV also matches the initial
  experimental WAV byte for byte.
- Actual installed MPV configuration: 100 exact seeks and 100 speed changes
  passed on the cache, with the live filter disabled and no Lua errors. This was
  a headless output test; Bluetooth, perceived join quality and battery life were
  not measured.

Recommendation: keep preprocessing opt-in, prefer disk storage on low-RAM
systems, and assess listening quality before integrating automatic substitution
or porting the cache to iOS. The iOS baseline remains unchanged.

Following the audit, the user requested raising the default per-cache ceiling
from 3 GiB to 4 GiB. The quarter-of-available-RAM and free-space checks remain
unchanged. The measurements above describe the original 3 GiB audit setting.

The user subsequently requested RAM-only output and optional interactive prompts.
The tool now accepts `--interactive`, seconds/MM:SS/HH:MM:SS input, start/duration
defaults and a suggested RAM output path. All output files stay on tmpfs/ramfs;
disk destinations are rejected before creating directories. Swappable tmpfs is
accepted only with no active disk-backed swap (zram must have no backing device).
This machine currently uses only `/dev/zram0` swap with `backing_dev=none`.
The script does not remount filesystems or modify swap. Checks describe current
configuration; a noswap tmpfs provides protection against later disk-swap changes.
Earlier disk-backed benchmark measurements above remain historical evidence,
but disk-backed output is no longer supported by this tool.

Verification after this change: all 24 preprocessing tests passed in 13.654
seconds, including nine new groups for time parsing, interactive input/defaults,
EOF cancellation, disk rejection, noswap/ramfs behavior and zram writeback checks.
An actual interactive run on the supplied AAC recording selected `2:23:00`,
processed a 20-second excerpt and published an 18.241104-second WAV on tmpfs.
Development-mode execution produced no runtime warnings. Help output and
file-scoped gitdelta/diff whitespace checks also passed. The live Lua filter was
not changed, so its earlier regression evidence remains applicable.

The tool, documentation and tests now live under
`user_scripts/audio/trim_silence_preprocess/`. The optional playback comparison
tests reference the unchanged MPV Lua filter in its own directory.
