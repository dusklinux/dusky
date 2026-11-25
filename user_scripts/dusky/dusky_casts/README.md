# Dusky Casts for Linux

Native Rust podcast and local audio client for Hyprland/Wayland, using Iced 0.14,
SQLite, Pocket Casts account sync, and mpv JSON IPC. Integrated directly into
Dusky (`user_scripts/dusky/dusky_casts`) with both ISO generic `x86-64` packaging
and userspace native (`-C target-cpu=native`) builds compiled on `tmpfs`.

## Build and install

Build requirements on Arch Linux: Rust/Cargo 1.99+, C compiler/linker, `pkgconf`,
`protobuf` (`protoc`), `python`, and `sqlite`.
Runtime requirements: `mpv`, `sqlite`, `wayland`, `libxkbcommon`,
`vulkan-icd-loader`, `mesa`, `libglvnd`, and `ttf-atkinson-hyperlegible`.

```sh
# Install via setup script (reuses matching /usr/bin/dusky_casts ISO package, or compiles native on tmpfs):
./scripts/install.sh

# Force a CPU-native build (-C target-cpu=native) on tmpfs:
./scripts/install.sh --native

# Build a standalone portable x86-64 binary outside the source tree:
./scripts/install.sh --generic --output-dir /tmp/dusky_casts_generic

# Development checks on tmpfs (never launches the GUI):
./scripts/build_on_tmpfs.sh --clippy
./scripts/build_on_tmpfs.sh --test

# Launch:
~/.local/bin/dusky_casts
```

When upgrading from legacy `duskcasts`, `dusky_casts` automatically performs a
one-shot WAL-safe `VACUUM INTO` migration from `$XDG_DATA_HOME/duskcasts/library.db`
into `$XDG_DATA_HOME/dusky_casts/library.db` (non-destructive to the legacy folder).
`make uninstall` removes user-local launcher links and desktop entries while
preserving library data and downloads. `--help` and `--version` work without a display.

## Listening and library

- Podcasts and paged episode lists, local search, history, in-progress,
  starred, archived, downloaded and Up Next views.
- Podcast discovery through the Apple directory. While signed in, directory
  results resolve to Pocket Casts UUIDs so subscriptions and progress sync.
- Local RSS/Atom subscriptions and conditional feed refresh. RSS-only shows
  remain local: their generated IDs are never sent as server episode IDs.
- Streaming and downloaded playback, resume, seek, skip, speed and volume.
- Up Next add-next/add-last/remove/reorder and end-of-episode advancement.
- Downloads with byte progress and cancellation, with up to three transfers
  independent of sync. Downloads publish only after a complete response;
  cancellation or failure preserves any existing complete file. Cancellation
  is checked between reads; a stalled server may delay it until the read ends.
- Manual playlist creation/membership, phone-created smart playlist evaluation,
  folders, renaming, and podcast folder assignment. Episode details display plain-text notes.
- Artwork fetched independently of sync, decoded with bounded allocations,
  resized to 420 pixels and cached on disk.
- Sleep timers and end-of-episode sleep. Player controls wrap in narrow windows.
- Account shows actual listening time today, total listening on this PC, and
  the most recent 14 recorded listening days. While signed in, it also shows a
  combined account listening total using the same device-statistics protocol as
  the phone. The daily history is for this PC; phone days are not reconstructed.
  Playback speed and seeking do not inflate elapsed listening time. Paused,
  buffering and inactive playback are excluded; event-loop gaps over two seconds
  are discarded to avoid counting suspend time. Local calendar days split at
  midnight. Totals persist with playback saves and graceful shutdown; abrupt
  termination can lose the unsaved interval. Sampling has approximately 0.5-second
  resolution, so these are listening estimates rather than an audio-output meter.

Not implemented: a full smart-playlist editor, playlist membership reordering,
chapters, transcripts, bookmarks, uploaded files, video surfaces,
Voice Boost and MPRIS/media keys. Do not treat this release as a
complete port of every iPhone feature. Playback settings are local to this PC;
episode and library state use account sync.

## Local audio folder

Open **Local files** in the sidebar. Enter an absolute folder path (or
`~/Podcasts`, for example), then select **Save & scan**. The folder is saved in
this PC's library settings and remains selected across restarts. Use **Save &
scan**, the refresh button, or Ctrl+R in a local view to pick up filesystem
changes. Scanning is explicit; the app does not watch the whole filesystem.

Subfolders are included. A separate paused, silent mpv instance identifies audio
streams and reads titles/durations; there is no audio-extension allowlist or
additional decoder dependency. This includes extensionless files and audio in
containers supported by the installed mpv build. Non-audio files and playlist
manifests are skipped. Metadata titles fall back to filenames. Scan results are
cached by path, size and modification/change timestamps, including unsupported
files; unchanged files do not need to be reopened by mpv. Files are read in place, never
copied into the application. Folder paths must be UTF-8; filenames inside them
can contain arbitrary Linux filename bytes. Directory symlinks are not traversed
(to avoid loops); symlinks to regular files are supported.

Click Play to listen with the existing speed, silence trim, sleep timer and
resume controls. **Local queue** supports add-next/add-last, reorder, removal
and automatic advancement. It is independent of the phone's Up Next queue.
Local files contribute to this PC's daily statistics and, while signed in, to
account listening time through a cumulative device counter. No audio files,
filenames or filesystem paths are uploaded. Individual local file positions and
queue membership remain on this PC; files do not join account playlists or
appear as playable uploads on the phone. Local playback requires no sign-in.

A successful scan hides missing files and removes them from the local queue,
while retaining their saved positions/stars. Reconnecting a drive and scanning
the same path restores the indexed items. Changing the selected folder hides
the previous folder's items; switching back restores their state. A failed scan
keeps the previous selection/index intact. Renaming or moving a file gives it a
new path identity; its old progress is retained under the original path.
Large first scans can take time; the interface and current playback stay active.
The scan shares the library job worker, so account refresh/sync waits until it
finishes. A file that exceeds the four-second probe timeout aborts the scan
without publishing an incomplete replacement.

## Appearance

The interface follows Dusky Center/Tray's restrained surfaces, rounded controls,
Atkinson Hyperlegible typography and accent hierarchy. Episode rows use compact
SVG actions with tooltips; the main player stays compact. Open its control icon
or press **Ctrl+,** for trim, volume and sleep settings. Speed stays in the player
on wider windows and moves into those settings in compact windows.

The primary palette is `$XDG_CONFIG_HOME/matugen/generated/dusky_center.json`
(normally `~/.config/matugen/generated/dusky_center.json`). It uses the existing
Center keys such as `bg`, `sidebar_bg`, `card_bg`, `card_hover`, `fg`, `fg_muted`,
`accent` and `accent_fg`. Without a valid palette, the app uses a built-in dark
slate palette with a soft blue accent. No username or home path is embedded.

Palette metadata is checked every three seconds; valid changes apply without
restarting. Partial/invalid writes retain the last valid colors until a complete
palette arrives. Colors and SVG icon handles are cached rather than reloaded for
each row. Narrow windows collapse the sidebar to icons and tooltips, while
keeping podcast/folder/playlist navigation and the Account button accessible.
The requested minimum window size is 600 × 540 logical pixels.

## mpv and silence trimming

The app runs mpv with `--no-config` and explicitly loads only the silence script,
so your other player UI/scripts do not run inside the podcast backend.

Silence script lookup:

1. `$DUSKY_CASTS_SILENCE_SCRIPT`, if supplied; a missing explicit path is an error.
2. `$XDG_CONFIG_HOME/mpv/scripts/dusky_silence.lua` (default `~/.config`).
3. The bundled copy of Dusky Player's script, extracted to a temporary directory.

Default trim level/enabled state comes from the installed
`mpv/script-opts/dusky_silence.conf`. Choices are off, mild, medium, max and
max-plus. Changing trim restarts mpv and restores position and pause state.
The script retains source timestamps, preserving seeking and sync positions.
IPC uses an inherited Unix socket pair; no filesystem socket or startup polling
is needed. Normal shutdown reaps mpv; Linux parent-death handling also kills
mpv if the desktop process is killed.

## Account sync

No account ID, email, password or account token is embedded. Enter credentials
in Account. The password is cleared from the form on submission and is never
stored in the database. The account token remains in the SQLite settings table,
matching the previous client. An expired/rejected token requires signing out
and signing in again; the password is not retained for automatic reauthentication.
Signing out retains the local library.

The protocol is taken from the supplied iPhone source, including the initial
snapshot flow missing from the previous desktop client:

1. Capture `user/last_sync_at` before importing snapshots.
2. Fetch `user/podcast/list`, podcast cache metadata, each show's
   `user/podcast/episodes` state, and `user/playlist/list`.
3. Commit the bootstrap watermark only after every snapshot succeeds. Retry an
   interrupted import without advancing the cursor. Preserve dirty local fields.
4. Incremental `user/sync/update` sends at most 2,000 episode changes per batch,
   matching the phone's non-watch limit. Responses and acknowledgements commit
   together with the cursor. Only exact submitted versions are acknowledged.
5. `up_next/sync` uses a durable action log. The first request pulls without
   submitting pre-login edits, then merges local-only entries for a later push.
   Edits during a request retain the local queue until they can be replayed.

Positions use milliseconds in SQLite and seconds on the wire. Incremental
conflicts resolve per field using server modification timestamps. Active local
playback protects position/status/duration/archive fields, while stars can
still update. Paused playback follows a newer phone position after a sync.
Unknown historical records are stored durably and fetched in bounded retry
batches; advancing the cursor does not lose their state. Snapshot episode rows
have no per-field timestamps, so pending local fields are preserved explicitly.
Queue-only podcast metadata does not create a subscription. Unsubscribing logs
queue and playlist removals, and pending unsubscribe tombstones block stale
cache responses from restoring the show.

Sync runs on startup, focus, manual request and local state changes; while
playing it runs about every 20 seconds, otherwise every five minutes. Local
positions save every five seconds, on pause and on graceful exit. Network errors
use bounded exponential retry delays. HTTP errors, 304 responses with pending
edits, empty acknowledgements and regressing cursors retain pending data.
Closing saves locally, stops audio and allows up to eight seconds for a final
network operation; unsent edits retry next launch. Abrupt process termination
can lose the few seconds since the last local position save.

This has been tested with protocol fixtures and mock servers, not with your
live signed-in account. First-login import and two-device handoff still need
verification with your iPhone. No real account or phone was accessed for tests.

## Listening statistics sync

The implementation follows the supplied iPhone's `SyncTask+LocalChanges.swift`,
`StatsManager.swift` and `RetrieveStatsTask.swift`, with the protobuf field numbers
verified against its generated `api.pb.swift`:

- Every playback source contributes to elapsed listening: streaming episodes,
  downloads, local podcast copies and arbitrary local audio.
- `user/sync/update` carries a `Record.device` / `SyncUserDevice` record containing
  this installation's stable device ID, start time and cumulative listening
  seconds. It sends a total, never a per-request increment. Retries therefore
  resend the same counter instead of counting the interval twice.
- The acknowledged seconds advance in the same transaction as the episode sync
  cursor. Listening recorded during an HTTP request remains pending. Fractional
  seconds stay in SQLite until they accumulate to a whole second. Previously
  recorded PC daily totals migrate into this counter without being added again.
- `user/stats/summary` receives this PC's device ID, as on the phone, to exclude
  this device. The Account view adds the latest PC counter exactly once. Failed
  summary requests preserve the last cached total and display a refresh error.
- Higher server counters for this same device can restore its cumulative total;
  explicitly foreign device counters never become part of this PC's counter.
  The phone sees the PC contribution when it next syncs/refreshes statistics.
- Only listening duration is implemented in this statistics record. Silence,
  speed and skip savings are not fabricated. The existing silence-trim playback
  behavior remains available.

This is metadata-only statistics sync. Arbitrary local files do not need account
file registration, cloud storage or audio upload to contribute to the listening
counter. They do not become playable on another device through this feature.
The phone's daily heatmap is a separate local approximation based on last
interaction dates and episode positions; this desktop retains its actual elapsed
PC daily counter. It does not label that counter as a combined daily total.
Live signed-in verification with your phone remains necessary; the wire format,
HTTP behavior, transaction failures and retries are tested with isolated fixtures.

## Storage and migration

Dusky Casts uses `$XDG_DATA_HOME/dusky_casts/library.db`
(default `~/.local/share/dusky_casts/library.db`). When upgrading from an
existing legacy `$XDG_DATA_HOME/duskcasts/library.db`, startup automatically
runs a one-shot WAL-safe `VACUUM INTO` migration while holding the legacy
instance lock (failing cleanly if the old player is still open) and leaves
`~/.local/share/duskcasts/` untouched as a non-destructive backup. Schema
changes remain additive so existing podcasts, episode state, queue, playlists,
folders, downloads, and account tokens are retained.

Downloads use `$XDG_DOWNLOAD_DIR/Dusky Casts`, defaulting to
`~/Downloads/Dusky Casts`; the directory must be exported to override it.
Previously downloaded episodes retain their recorded paths and remain playable.
The normal mpv log is `$XDG_CACHE_HOME/dusky_casts/mpv.log`. Silent tests use
temporary logs. A forcibly interrupted download can leave an unreferenced
`.dusky_casts-*.part` file; it never replaces a completed download.

`proto/{api,sync,bootstrap}.proto` contains all wire schema definitions.

## Verification

```sh
cargo fmt --check
./scripts/build_on_tmpfs.sh --clippy
./scripts/build_on_tmpfs.sh --test
bash tests/install.sh
# Optional 1,000-file filesystem/mpv stress check:
CARGO_TARGET_DIR=/tmp/dusky_casts-target-$UID cargo test --frozen --test local large_local_library_scan_and_cache -- --ignored --nocapture
shellcheck scripts/install.sh scripts/build_on_tmpfs.sh tests/install.sh
```

Tests use isolated databases and local HTTP servers. Real mpv tests always set
`ao=null`, exercise all trim levels, source timestamp filters, seeking, speed,
file replacement and EOF. The stress fixture uses 25,002 episodes, 100 paged
queries and 10,000 conflict merges.

`cargo run --frozen --example paging` measures 100 page loads over 50,000 episodes,
with and without the global ordering index.

Keyboard: Ctrl+Q quits, Ctrl+R refreshes, Ctrl+N opens discovery, Ctrl+, toggles
playback settings. Space toggles
playback and Left/Right skip 15/30 seconds when the event is not consumed by
an input control.

A native Wayland UI smoke check with isolated data:

```sh
XDG_DATA_HOME=/tmp/dusky_casts-demo-data XDG_CACHE_HOME=/tmp/dusky_casts-demo-cache \
DUSKY_CASTS_SILENT=1 DUSKY_CASTS_SCREENSHOT=/tmp/dusky_casts.png \
dusky_casts --quit-after 5
```

`DUSKY_CASTS_SCREENSHOT` captures only the application framebuffer.
`DUSKY_CASTS_SCREENSHOT_DELAY` sets the capture delay in milliseconds (default 2000).
`DUSKY_CASTS_VIEW=Account` (or `Discover`, `Organize`, `Up Next`) selects a view
for that check. `DUSKY_CASTS_API_BASE`, `DUSKY_CASTS_CACHE_BASE` and
`DUSKY_CASTS_REFRESH_BASE` override service roots for test fixtures.
