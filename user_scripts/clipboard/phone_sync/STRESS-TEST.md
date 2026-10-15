# Phone sync stress verification — 2026-10-16

The expanded checks passed after correcting startup selection handling, subprocess
pipe cleanup and stale-session replacement on the phone. Linux uses Python
3.15.0rc3, wl-clipboard 2.3.0 and systemd 262. The tested phone is an iPhone 8
Plus on iOS 16.7.16 with Duskie 0.2.26.

## Corrections

- Startup now takes one bounded text snapshot before starting the watcher and
  SSH loop. Guessing that the first watcher event was the baseline discarded the
  first text copy when startup clipboard contents were image-only. An isolated
  Wayland data-control server with the installed `wl-paste` reproduced the old
  failure; both image and text startup scenarios passed with the snapshot.
  [wl-clipboard 2.3.0's implementation](https://github.com/bugaevc/wl-clipboard/blob/v2.3.0/src/wl-paste.c)
  skips watcher callbacks for unsupported selection types.
- Subprocess shutdown now drains discarded pipe output concurrently, in fixed
  chunks. Oversized-output testing exposed an unclosed stdout transport with
  the earlier cleanup; the corrected suite finished without resource warnings.
  This avoids both waiting on a full pipe and accumulating unlimited output,
  as described in [Python's subprocess documentation](https://docs.python.org/3.15/library/asyncio-subprocess.html).
- Duskie 0.2.26 replaces an existing phone-sync peer when a new local connection
  arrives. A half-open Wi-Fi SSH session previously prevented USB reconnection.
  Exactly one peer remains active; the newest connection wins. No heartbeat or
  polling timer was added. This is a phone-side change; protocol version 1 and
  its limits are unchanged.

## Exercised cases

Run the retained, isolated Linux checks from this directory:

```sh
python3 -X dev -W error test_phone_sync.py
python3 -X dev -W error test_phone_sync_stress.py
```

All **14 regression tests and seven stress tests passed**. The stress suite uses
real subprocess pipes, Unix sockets and capture CLI children, with fixture
processes standing in for SSH peers and clipboard tools:

| Load / fault | Observed result |
|---|---|
| 1,000 copies of exactly 16 KiB, Unicode and newlines, fragmented transport | All 16,384,000 payload bytes verified by per-copy SHA-256; all acknowledged |
| 30,000 local events while acknowledgment is held | At most one pending acknowledgment and one queued copy; first in-flight copy and final queued value delivered |
| 100 incoming copies and capture-child echo events | All acknowledged; final Unicode/newline value correct; no copies echoed back |
| 50 abrupt peer disconnects followed by fresh connections | Writer, queue and pending acknowledgments cleared; child processes reaped |
| Missing acknowledgment | Real 15-second deadline triggered; peer reaped and state cleared |
| Full stdout/stderr, child ignoring SIGTERM | SIGKILL fallback completed; both pipes reached EOF |
| Text/image startup, oversized output and hanging snapshot process | Correct snapshot result, bounded reads/timeouts and reaped children |

The regression suite also verifies Wi-Fi/USB endpoint order and reconnect delays
of 1, 2, 4, 8, 16, 30 and 30 seconds using an isolated clock substitute.

On the real phone, the old native listener failed the stale-peer replacement
fixture; Duskie 0.2.26 passed **50 replacements with the old socket still open**.
Four relevant native executables passed: phone sync, clipboard bridge, clipboard
storage and copy capture. The strict production build, nine host fixtures, two
package tests and offline dependency verification passed; native analysis of
the changed listener produced no diagnostics.

Five consecutive real Linux service restarts reconnected successfully: four
through Wi-Fi and one through USB. Each settled process had **11 open file
descriptors**. The final service is running the updated Python code and connected
through USB. Exactly one phone bridge remained active. Both phone preference
file hashes were unchanged; all eleven installed binary hashes matched the signed
0.2.26 package. Temporary phone fixture staging was removed.

## Limits and before/after

The high-volume loads used isolated peers and clipboards. They are not thousands
of transfers through the real iPhone pasteboard or a prolonged cable/network
fault soak. The real-device checks establish installation, native protocol
behavior and reconnection across both endpoints. No battery-life or throughput
improvement is claimed; startup adds one bounded snapshot process, with no idle
polling added.

| Area | Before /100 | After /100 | Basis / result |
|---|---:|---:|---|
| Startup correctness | N/A | N/A | Reproduced image-startup failure corrected; better |
| Shutdown and reconnect reliability | N/A | N/A | Full-pipe cleanup and stale-peer regressions passed; better |
| Performance | N/A | N/A | Stable descriptor counts observed; speed/battery gains unverified |
