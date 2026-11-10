# Automatic episode-start detection

`pattern_check.py` scans a recording in RAM (8 kHz mono, 25 ms frames) and
estimates where the pre-show ends and the episode talking stream begins. It
never writes decoded audio to disk. `verify_episodes.py` replays each detected
start in MPV so the result can be checked by ear.

## Usage

```sh
# Analyze one file or a directory (recursive with -r):
python3 pattern_check.py recording.m4a --print-mpv-commands

# Analyze a directory and save detections.json:
python3 pattern_check.py /path/to/episodes -r --json-out /dev/shm/detections.json

# Replay every detected start in MPV (q advances to the next episode):
python3 verify_episodes.py /dev/shm/detections.json

# Feed a verified start to the silence trimmer:
python3 ../preprocess.py recording.m4a /mnt/zram1/trimmed.m4a --start 02:38:12
```

`--ram-dir` sets the directory used in the generated `preprocess_command`
(default: `/mnt/zram1` when present, otherwise `/dev/shm`).

## Configuration

Copy `config.example.toml` to
`$XDG_CONFIG_HOME/dusky/settings/audio_detection/config.toml` (normally
`~/.config/dusky/settings/audio_detection/config.toml`). All keys are optional.
`[defaults]` applies to every profile; `[profiles.NAME]` inherits and overrides
it. Select a profile with `--profile NAME`. `--min-speech-minutes` overrides the
selected profile's confirmation window.

The defaults require a 10-minute window with at least 60% speech, 70% speech or
quiet, and at most 25% music before a start is confirmed. Pauses and silence
thresholds are described in `config.example.toml`.

## Output

`pattern_check.py` prints a table and writes a JSON list. Each entry contains
the file path, total duration, detected start (`show_start_timestamp`), show
duration, confidence, transition signature, and ready-to-run MPV/preprocess
commands. The default report is
`$XDG_CONFIG_HOME/dusky/settings/audio_detection/detections.json`; a failed
batch leaves the previous complete report untouched.

## Notes

- Detection is a heuristic. Always spot-check a new profile or an unusual
  recording with `verify_episodes.py` before batch-trimming.
- The detector is intentionally conservative: it prefers reporting no
  transition over cutting into the episode.
- Both tools keep all temporary audio in RAM and leave the source file
  untouched.
