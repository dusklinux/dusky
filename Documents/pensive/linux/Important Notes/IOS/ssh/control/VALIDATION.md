# Validation record

Current review: **2026-10-21**, recorded below. The original 2026-10-20 account
is retained as dated history; its “no phone commands” and unfinished-test statements
apply to that original side task, not the later main-session evidence.

## Original 2026-10-20 record

Prepared **2026-10-20** in a side conversation, separate from the active Duskie
implementation/testing session. No phone SSH command, Frida attach, recording,
service change, preference change or recovery action was performed to validate
this new wrapper. The main task's worktree and processes were left alone.

## Local checks actually run

| Check | Result |
|---|---|
| Python 3.15.0rc3 CLI --help | Passed without importing optional Frida |
| Local doctor | Passed; reports Python/tool paths and absent Frida package in the default interpreter |
| Python host behavioral tests | 14 passed |
| Node JavaScript syntax check | Passed on Node 26.12.0 |
| Node bridge contract tests | Passed: NSString construction/type validation, explicit BOOL argument, ABI mismatch rejected before invocation, nil receiver rejected, unknown action rejected |
| Default interpreter Frida availability | Not installed; not silently installed or switched to a different environment |
| Existing unlock.md | Read for reference and left byte-for-byte unchanged |

Python test cases cover:

- PID parsing with a header and executable paths containing spaces.
- Remote argument quoting without a local shell or accidental command expansion.
- Help without the optional instrumentation dependency.
- 24 → 30 → 60 fps selection through displayed labels.
- Unsupported frame-rate cycles terminate with current/original state.
- A private numeric frame-rate enum is not mistaken for displayed fps.
- Invalid ports, NaN/zero timeout rejected by the parser.
- Screenshot output Paths kept out of JSON RPC parameters.
- Explicit SSH identity/control-socket arguments and interactive authentication.
- Missing target process rejected before a Frida attach attempt.
- Session cleanup attempted after an RPC error.
- Every documented worker action accepted by the parser.

The Node fixture mocks the native boundary. It validates control flow and argument
representation, **not** the implementation of UIKit, CameraUI, SpringBoard or
Frida's actual injected runtime.

## Evidence inherited from the reference probes

These are observations available before this side task began, not new live
acceptance tests of this wrapper:

- USB SSH port 2222 was the reliable connection. Mobile key login worked; root
  privilege was obtained through mobile's interactive sudo shell when needed.
- Host Frida 17.2.17 attached via 127.0.0.1:27047 forwarded to device port 27042.
- Correctly typed NSString app launching worked. An earlier C-string diagnostic
  caused a SpringBoard restart, diagnosed and recovered in the main thread.
- Current SpringBoard module presence and display state were inspected directly.
- Wake plus delayed UI unlock was exercised on the owner's no-passcode setup.
- Native Camera Photo/Video selection, start/stop/photo and current recording
  state were inspected/called on the actual iOS 16 runtime.
- Native Camera's displayed 4K frame-rate control cycled 24 → 30 → 60; the
  configuration/frame-rate enums differed from the human-readable fps values.
- The Duskie app displayed name/title Duskie and exposed 85 shared settings rows,
  including the RAM-buffer selector, native remote switch, retry, Hardware
  Buttons and Remote FTP.
- UIAlertController inspection read the Duskie remote storage warning and its
  explicit distinction between a RAM clip limit and full iPhone storage.
- The original screen-off 4K60 worker tests covered live FTP growth, RAM segment
  reclamation, a longer-than-1-GiB movie, reconnection, exact transport equality
  fixtures and full FFmpeg decode.
- Native 4K24 media decoded successfully; a separate finalized native RAM inode
  matched its FTP file by SHA-256. Native 4K60 recording testing was still in
  progress at the side-conversation boundary. No completed result is invented.

The following consolidated implementations still need device acceptance:

- Every command as routed through the new Python CLI and bundled runtime.
- Screenshot directly returned as base64 rather than the older phone-file probe.
- Generic UIControl matching/tapping across arbitrary app screens.
- Resolution cycles other than the observed 4K frame-rate cycle.
- lock and Duskie simulated-lock authentication through the new guarded wrapper.
- Error/cancellation behavior with a genuinely stalled process/main queue.

## Main-operator live acceptance checklist

This is a future test procedure, not an action already taken. Coordinate with any
ongoing main task before using the phone.

1. Confirm the actual iOS/bootstrap, installed Frida/server version and ownership
   of existing USB/Frida tunnels. Do not replace an active setup for convenience.
2. Run doctor locally, then ps/doctor --phone. Confirm fresh process discovery.
3. Establish/reuse the server and forward; run status, methods and modules first.
   Check the current ABI profile against the actual runtime.
4. Test wake/sleep and re-read status. An already-off sleep should remain off.
   Test unlock only within the known owner configuration.
5. Launch a harmless already-installed app, read its controllers/UI, capture a PNG
   to the host and verify it opens. No phone-side screenshot file should be added.
6. Test a uniquely identified ordinary button whose effect can be observed and
   reversed. Verify ambiguous/disabled matches fail rather than choosing a target.
7. Launch Camera, explicitly select Video, read format, request 4K60 and read the
   settled labels. Start a finite recording, confirm recording state, stop and
   inspect the final media with ffprobe plus full decode. Restore its original
   format afterward if the change was temporary.
8. If Duskie worker tests are in scope, snapshot relevant settings, run a finite
   start/photo/stop sequence using separate terminals, confirm actual display
   state, inspect fresh status/results, then restore settings. Its remote/local
   destination is not chosen by this CLI.
9. Test one expected missing-process/ABI failure without changing device state.
   Record the exact error and verify normal mode persists.
10. Detach, stop only owned temporary processes, restore test state, preserve user
    media/queued captures and record final normal mode/current SpringBoard PID.

Keep evidence outside public source when it contains credentials, device UUIDs,
screen contents or other personal data. Update this record only for operations
actually exercised.

## Second pass

The local review found and corrected a recording-lifetime issue: worker actions
inherited the generic 30-second SSH timeout. They now stream through an attached
SSH process without that timeout; status remains a bounded read. A mock regression
test verifies that no timeout is passed. Environment-provided ports now go through
the same range validation as command-line ports, covered by another regression
test. Unused connection state was removed.

All 14 host tests and the JavaScript syntax/bridge checks passed after these
changes. No device operation was performed. The historical unlock.md is retained
because it supplies a self-contained compiled recovery helper and detailed
observations not duplicated in the main CLI. README.md remains the current entry
point; the old addresses and temporary helper installation paths are historical.


## 2026-10-21 notes and tooling review

All nine original source/text files in this directory were read in full.
Original copies and initial SHA-256 hashes were saved outside this folder at
`/home/dusk/.codex/iphone-control-review-20261021/` for review. That is an audit
snapshot, not a runtime dependency. `unlock.md` remains byte-for-byte unchanged.

### Checks performed in this review

- Python 3.15.0rc3: **16 host tests passed**, including Slo-mo format-mode
  preservation and parsing; all worker action names are checked by the parser.
- Node 26.12.0: runtime syntax and bridge contracts passed, now including mode 2
  dispatch, Slo-mo start acceptance and rejection of an unsupported mode.
- A local ignored Python 3.15 `.venv` was created; installation/import of Frida
  17.2.17 succeeded and its interpreter's doctor reported the matching package.
- `./iphone-control ssh -- ...`, `ps`, and `doctor --phone` exercised the real USB
  SSH route without a Frida attach. Device uname reported iPhone10,2; dpkg-query
  read back `com.dusklinux.edgedock 0.2.65~duskie1`.
- Phone command discovery found Python3, ldid and sbreload under `/var/jb/usr/bin`;
  notifyutil and an installed frida-server were absent. No server was installed,
  signed, started or attached during this review.
- Existing iproxy and managed `duskie-usb` receiver were active. A user recording
  partial was growing. Capture, receiver, UI, radio and preferences were left alone.
- Cached official Frida DEB matched the SHA-256 published for release 17.2.17.
  Local GNU ar/tar extraction confirmed the documented payload paths; the agent
  path replacement matched exactly twice and preserved executable byte length.
- All inline JavaScript/Python examples passed host syntax checks; Markdown local
  links and code fences passed consistency checks. These are not execution tests
  of the inline device examples.
- README, bootstrap, recipes, extension guidance and links were reviewed against
  the current CLI/source and dated main-session release evidence.

The consolidated **Frida UI routes**, new Slo-mo wrapper route, screenshot,
generic UIControl/alert handling and inline JS examples still require live
acceptance when the phone is available. Mock tests cannot validate private iOS ABI
or a responsive app main queue. No new recordings or reconnect experiments were
performed merely to update these notes.

### Completed main-session evidence available by this review

These are earlier underlying product/probe tests, not acceptance of this CLI:

| Capture | Transport | Format | Duration | Bytes |
|---|---|---|---:|---:|
| Native Camera | USB, Wi-Fi off | 1080p240 | 18.134785 s | 28,190,435 |
| Native Camera | USB, Wi-Fi off | 4K60 | 18.065125 s | 97,460,038 |
| Screen-off | USB | 1080p240 | 24.881667 s | 47,838,933 |
| Screen-off | Wi-Fi FTP | 4K60 | 42.188333 s | 252,162,195 |
| Native Camera | Wi-Fi FTP | 4K60 | 17.742222 s | 96,809,992 |
| Native Camera | USB | 1080p30 | 5.242449 s | 4,123,235 |

All six completed full FFmpeg error-sensitive decode. Screen-off 240 reported
seven video drops and zero audio-buffer drops; the screen-off 60 sample reported
zero drops. Do not turn this into a zero-drop guarantee. Native UI start/stop and
restoration, live USB mpv playback/seek before finalization, phone preview reachability
and explicit unavailable-endpoint alerts were observed in those main sessions.
Both actual phone connection-test UI results passed (FTP and USB); unavailable
USB returned an explicit failure. The managed receiver restarted after its SSH
child was deliberately terminated. Bare-root mpv playback was tested separately
against existing received media after the HTML-response fix.

Main source evidence is retained under `/mnt/zram1/duskie/research/`:
`NATIVE-STREAMING-0.2.63.md`, `USB-PREVIEW-0.2.64.md`,
`CONNECTION-UI-0.2.65.md`, `PREVIEW-URL-FIX.md`. See RECIPES.md for what each covers.
Those volatile paths are provenance, not required control-tool dependencies.
Long final-release recordings, cable removal during active capture, sustained
full-backlog behavior and battery/heat remain unverified here and were left to
the user. Keep that distinction when adding future results.
