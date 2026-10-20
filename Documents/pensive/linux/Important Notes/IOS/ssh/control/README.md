# iPhone control from Linux over USB SSH

Start here when a future agent needs to inspect or control this jailbroken iPhone.
The executable is **`iphone-control`** and its self-contained Frida helper is
**`runtime.js`**, both beside this document. It does not depend on the temporary
Duskie checkout, a saved SpringBoard PID, a phone-specific UUID or an injected
review dylib. Keep the two files together.

This directory was prepared on **2026-10-20**. It consolidates methods observed
in the main Duskie session on an **iPhone 8 Plus, iOS 16.7.16 / 20H392, rootless
jailbreak, ElleKit 1.2**. The host used Python 3.15.0rc3, Node 26.12.0 and OpenSSH
10.6p1. This is a specific iOS 16 control profile, not a claim of support for every
iPhone or iOS version. The model identification in older notes differs; use
`uname -a` and the device's own reported model when the distinction matters.

**Verification status:** the original underlying probes were exercised during
that session. This consolidated wrapper has host behavioral tests and JavaScript
contract/syntax tests, but **has not been run against the phone**. It was created
in a side conversation while the main conversation was using the device. The
command reference identifies private calls; they check the live Objective-C
method encoding before invoking them. A refusal is preferable to guessing the
ABI. See [VALIDATION.md](VALIDATION.md) for the exact distinction.

## What this actually does

There are two control channels:

1. **SSH** runs commands, reads files, resolves current PIDs, transfers files and
   invokes the installed Duskie camera worker.
2. **Frida** attaches to the selected process. `runtime.js` uses the Objective-C
   runtime to inspect its objects and call known methods. UIKit work is dispatched
   onto that process's main queue. Camera actions run inside Camera; app launching
   and display/lock actions run inside SpringBoard.

The tool normally invokes the code behind a UI action. `tap` sends a UIControl
TouchUpInside event to an identified control. It does **not** synthesize a finger
at screen coordinates. Reading labels is native UI inspection, not OCR. Screenshots
use an existing UIKit image-export function and return PNG data to Linux.

The jailbreak makes these private interfaces reachable. SSH alone does not make
a standalone process the owner of SpringBoard's controllers. Calling a private
method on the wrong object, process or argument type can crash that process.

## Files

| File | Purpose |
|---|---|
| `iphone-control` | Python 3.15+ CLI; executable directly or through an explicitly chosen Python |
| `runtime.js` | Checked private calls, Objective-C bridge, main-queue scheduling, UI inspection |
| `README.md` | Setup, every command, workflows, recovery and limitations |
| `EXTENDING.md` | How to add a command, exact signatures, custom JS and failure lessons |
| `VALIDATION.md` | Tests performed locally and evidence inherited from the original probes |
| `test_control.py` | Host-only parser, quoting, selection, cleanup and format-loop tests |
| `test_runtime.cjs` | Host-only JavaScript bridge contract tests; not an iOS simulator |
| `.gitignore` | Ignore local Python cache/environment artifacts |
| `unlock.md` | Pre-existing detailed unlock history and standalone Objective-C recovery helper; unchanged |

No credentials, binaries, SDKs or Python environments are newly bundled here.
The existing `unlock.md` contains older connection details supplied by the owner;
its IP address and temporary project paths are historical, not current defaults.

## Setup: use existing connections first

From this directory:

```sh
./iphone-control --help
./iphone-control doctor
```

`doctor` is local only. `doctor --phone` also reads `uname -a` and the process list
over SSH. Neither installs, starts, stops or repairs anything.

Global options go **before** the subcommand:

```sh
./iphone-control --target mobile@127.0.0.1 --port 2222 ps
./iphone-control --identity ~/.ssh/iphone --process Camera ui
./iphone-control --pid 12345 controllers
```

Default connection settings:

| Setting | Default | Override |
|---|---|---|
| SSH account/host | `mobile@127.0.0.1` | `--target` / `IPHONE_SSH_TARGET` |
| USB SSH port | `2222` | `--port` / `IPHONE_SSH_PORT` |
| Host Frida endpoint | `127.0.0.1:27047` | `--frida` / `IPHONE_FRIDA_ENDPOINT` |
| Process | SpringBoard; Camera for `camera` | `--process` or `--pid` |
| SSH/RPC wait | 30 seconds | `--timeout`, greater than 0 and at most 3600 |
| SSH identity | normal OpenSSH configuration | `--identity` |
| Multiplexing socket | none | `--control-path` to reuse an existing socket |

Prefer USB over a Wi-Fi address. Noninteractive SSH and scp commands use
`BatchMode=yes`, so missing key authentication fails clearly instead of waiting
for a hidden password prompt. `shell` permits normal interactive authentication.
The tool does not store a password or alter SSH configuration. An existing
interactive authenticated multiplexed connection can also be reused.

### USB SSH

Reuse an already running iproxy listener. If no listener exists, run in a separate
terminal and leave it running:

```sh
./iphone-control usb
# Equivalent syntax checked against the installed iproxy --help:
iproxy -s 127.0.0.1 2222:22
```

For multiple attached devices, use the installed iproxy's `--udid` option and an
explicit discovered device ID; the CLI's `usb` command uses iproxy's normal device
selection. Do not start a second listener on the same port or kill someone else's
tunnel to make space. USB trust/pairing and an operational jailbreak SSH service
are prerequisites; this tool does not repair them.

Test SSH independently of Frida:

```sh
./iphone-control ps
./iphone-control ssh -- uname -a
./iphone-control shell
```

### Python and Frida

The CLI targets **Python 3.15+**, including explicit lazy imports. SSH commands,
local doctor and help work without Frida. UI commands need the `frida` Python
package in **the interpreter actually running this script**.

Create a local environment if needed. Set the version to match the device server
rather than installing the newest release blindly:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install 'frida==17.2.17'
.venv/bin/python ./iphone-control doctor
```

`17.2.17` is the historical client/server version used for the source probes,
not a permanent version requirement. If the installed server differs, choose
its corresponding client version. Creating this environment and installing that
package were **not** performed as part of creating this tool. Do not point the
script at the earlier Python 3.14 environment: its interpreter cannot parse the
new Python 3.15 syntax. Do not globally replace another session's environment.

### Device Frida server

A correctly installed and signed Frida server and its matching agent are needed
on the phone. Use the package appropriate to the actual rootless bootstrap and
architecture; the CLI deliberately does not install or patch it automatically.
The server needs sufficient privilege to attach to SpringBoard.

From an interactive device shell, become root using the owner's existing login
or `sudo -s`. Enter a password at its prompt, not in a command argument. Inspect
the installed executable before starting it:

```sh
command -v frida-server
/var/jb/usr/sbin/frida-server --version
/var/jb/usr/sbin/frida-server --help
```

The rootless path above is the observed path on this phone, not a universal
installation location. If no server is already using port 27042, start it in a
dedicated device terminal:

```sh
/var/jb/usr/sbin/frida-server -l 127.0.0.1:27042
```

Leave it running. Use a separate Linux terminal for the forward:

```sh
./iphone-control tunnel
# Equivalent:
ssh -p 2222 -N -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:27047:127.0.0.1:27042 mobile@127.0.0.1
```

This foreground tunnel remains active until stopped. The `--frida` option selects
the client endpoint, while `tunnel --local-port` and `--device-port` select forward
ports. Configure both consistently. Reuse an existing forward if one is active.

`module not found ... frida-agent.dylib` means transport/server startup succeeded
but agent placement or the package's rootless path mapping is wrong. It does not
mean that Camera needs a new hook. The original session encountered both
`/usr/lib/frida/frida-agent.dylib` and a doubled rootless-prefix path from a
**locally patched diagnostic binary**. Those are diagnostic history, not paths
to recreate blindly. Use the current server's actual error and matching package
layout. A server or agent left in temporary storage by another session may
already have been removed. The CLI does not rely on those leftovers.

## Every command

All RPC output is JSON. Frida diagnostic events go to stderr. PIDs are resolved
from fresh `ps -A -o pid,comm` output on each new invocation. A `--pid` override
is for deliberate use with a PID you have just checked, not an old cached value.

### Connection, files and device commands

| Command | Behavior |
|---|---|
| `doctor` | Local Python/dependency/tool report; no phone connection |
| `doctor --phone` | Also read device uname/processes over SSH |
| `ps` | Fresh PID and full executable list |
| `usb` | Foreground iproxy for SSH; use only if a listener is absent |
| `tunnel` | Foreground SSH forward to an already running Frida server |
| `shell` | Interactive SSH with a TTY |
| `ssh -- COMMAND ARG...` | Run an argv vector; each remote argument is quoted once |
| `get REMOTE LOCAL` | scp one remote file to a local path |
| `put LOCAL REMOTE` | scp one local file to a remote path |

Examples:

```sh
./iphone-control ssh -- df -k /var/mobile
./iphone-control get /var/mobile/Media/DuskieCamera/status.plist ./status.plist
./iphone-control put './file with spaces.plist' '/var/mobile/Media/file with spaces.plist'
./iphone-control ssh -- sh -c 'command -v ldid; command -v sbreload'
```

`ssh --` passes arguments, not an implicit shell program. Use an explicit `sh -c`
when shell operators are intentional. `get`/`put` use the installed scp/SFTP
interface; copies can overwrite their requested destination. They do not preserve
an existing destination as a backup or correct ownership for a privileged install.
None of these commands automatically runs sudo, ldid, dpkg or a reboot.

### SpringBoard and screen control

| Command | Behavior / completion evidence |
|---|---|
| `status` | Current PID, display-on, UI-lock and Duskie module presence |
| `apps` | Installed bundle IDs and iOS displayed app names |
| `launch BUNDLE_ID` | Ask SpringBoard to launch an app; accepted does not mean ready |
| `home` | Invoke the checked Home completion-selector |
| `wake` | Wake display only; does not deliberately extend idle-sleep timers |
| `sleep` | If display is on, invoke Lock button; if already off, leave it off |
| `lock` | Request UI locking with source 12; read status afterward |
| `unlock` | Wake, wait about 340 ms, request the documented no-passcode UI unlock |
| `duskie-unlock` | Call Duskie's own simulated-lock authentication entry point if installed |
| `screenshot OUTPUT.png` | Return a screen PNG to the host; no phone-side output path |

```sh
./iphone-control status
./iphone-control wake
./iphone-control unlock
./iphone-control launch com.apple.camera
./iphone-control screenshot ./screen.png
./iphone-control sleep
./iphone-control status
```

`unlock` comes from the owner's no-device-passcode configuration described in
[unlock.md](unlock.md). The SSH password is not a device passcode. It does not
claim to unlock a different device-passcode configuration. Duskie's simulated
lock is separate: `duskie-unlock` uses an installed Duskie class and is not required
on a phone without that feature. Neither command changes a saved passcode or
Touch ID enrollment. The `lock` and `duskie-unlock` wrappers are ABI-guarded but
were not independently live-tested in this side conversation.

`status.duskieLoaded=false` is a clue, not a complete Safe Mode diagnosis: injection
can also be disabled or the payload may be absent. Pair it with the Safe Mode
marker and current process state. Read state after an action; a return code alone
is not proof that a requested transition completed.

### Inspecting apps and choosing UI controls

```sh
./iphone-control --process Camera controllers
./iphone-control --process Camera ui --max-nodes 800
./iphone-control --process Camera alerts
./iphone-control --process Camera dismiss-alert
./iphone-control --process Preferences methods PSListController --matching specifier
./iphone-control --process SpringBoard methods SpringBoard --matching launch
./iphone-control --process SpringBoard methods UIApplication --class-methods --matching shared
./iphone-control --process Camera modules --matching Duskie
./iphone-control --process YourApp tap --id unique-accessibility-identifier
./iphone-control --process YourApp tap --label 'Exact button title'
```

| Command | Output / limit |
|---|---|
| `controllers` | Parent/presented relationships, class names and process-local object addresses; bounded at 600 |
| `ui --max-nodes N` | View classes, parent addresses, accessibility IDs/labels, text/current titles; 1..5000 nodes |
| `alerts` | Present UIAlertController titles/messages |
| `dismiss-alert` | Dismiss exactly one present alert; does not choose Yes/OK/Delete or invoke an action handler |
| `tap --id ID` | TouchUpInside on exactly one matching UIControl |
| `tap --label TEXT` | Same, matching an exact accessibility label, current title or text |
| `methods CLASS` | Methods declared by the class, with exact Objective-C encodings |
| `methods CLASS --class-methods` | Methods on the metaclass; useful for factories/singletons |
| `modules --matching TEXT` | Loaded module names, paths, bases and sizes |

Inherited selectors are not repeated by `methods`; inspect the relevant superclass
if a selector is absent from the declared-method list. Object addresses are not
persistent identifiers: do not reuse them in another process or after a restart.

UI inspection is bounded because collection views can be large. `truncated=true`
means the report is incomplete. UIControl tapping refuses an ambiguous match or a
disabled/hidden control. UILabels themselves usually are not UIControls. Buttons
inside alerts may require a different semantic action rather than blindly tapping
a label. This does not implement coordinate taps, three-finger HID injection,
scroll/swipe synthesis, keyboard text injection, SpringBoard biometric spoofing,
or generic UIAlertAction handler execution. Use the supported semantic action,
add a reviewed typed extension, or use an appropriate separate automation stack.

### Native Camera: actual Camera app

Launch and allow it to become ready first. Commands default to the `Camera`
process, not the Duskie helper. Camera may reopen in Photo even if the last test
used Video. Status is authoritative:

```sh
./iphone-control unlock
./iphone-control launch com.apple.camera
./iphone-control camera status
./iphone-control camera mode video
./iphone-control camera format
./iphone-control camera set-format --resolution 4k --fps 60
./iphone-control camera format
./iphone-control camera start
./iphone-control camera status
# Let the desired test run; separately monitor FTP/storage if needed.
./iphone-control camera stop
./iphone-control camera status
```

| Camera subcommand | Behavior |
|---|---|
| `status` | Current standard mode enum and recording state |
| `mode photo` / `mode video` | Standard Photo=0 / Video=1, device selector 0 as observed on this profile |
| `format` | Configuration enum, frame-rate/resolution enums and **displayed labels** |
| `fps-next` | Invoke the native frame-rate-indicator tap handler once |
| `resolution-next` | Invoke the native resolution-indicator tap handler once |
| `set-format --resolution ... --fps ...` | Cycle native controls, settle between steps and verify displayed labels |
| `start` | Start standard Video; leave an already recording capture running |
| `stop` | Stop if recording; otherwise return unchanged |
| `photo` | Invoke native capturePhoto; actual result may finish asynchronously |

`set-format` accepts resolutions `4k`, `1080p`, `720p` and fps
`24,25,30,50,60,120,240` as requests, **not promises of device/mode support**.
The observed profile labels are `4K`, `HD` and a possible `720p` label. Only the
4K cycle is supported by inherited live evidence; verify other displayed labels
before extending this mapping. If the UI cannot reach a request, the tool exits
with the current and original states rather than inventing a configuration enum.
A failed cycle may leave the Camera selection changed: use the printed original
format to restore it. Mode changes and direct cycle commands return a request;
read the settled state afterward.

On the original phone, actual UI observations included:

| Private configuration enum | Frame-rate enum | Displayed labels |
|---|---|---|
| 10 | 1 | 4K / 24 |
| 5 | 3 | 4K / 30 |
| 9 | 4 | 4K / 60 |

These numeric values are **evidence only**. The implementation cycles native
controls and reads labels; it does not hardcode those video configuration enums.
A value of `4` in `framerateEnum` is not 4 fps or 60 fps by itself.

The CLI does not redirect native capture storage. Duskie's installed
**Camera app → Remote FTP** setting determines that independently. A standard
native capture normally requires the Camera app's foreground capture session;
do not assume locking the display preserves a native recording. Use Duskie's
worker for its screen-off recording feature.

### Duskie screen-off worker

These commands need SSH and an installed Duskie release, **not Frida**:

```sh
./iphone-control worker status
./iphone-control worker start
./iphone-control worker photo
./iphone-control worker stop
./iphone-control worker upload-ram
```

Every supported worker action is listed below:

| Action | Meaning |
|---|---|
| `start` | Start camera video using saved Duskie settings |
| `stop` | Stop the active capture |
| `toggle` | Start/stop toggle; read state before retrying after an uncertain result |
| `photo` | Request a still, including supported photos during video |
| `voice-start` | Start voice note using saved audio settings |
| `voice-stop` | Stop voice note / active worker capture |
| `voice-toggle` | Toggle voice recording |
| `scan-microphones` | Refresh discovered inputs/data sources |
| `scan-camera` | Refresh supported lens/video-format choices |
| `upload-ram` | Retry ready RAM files / segmented capture manifests |
| `stream-ram` | Scan native recording markers; integration action, not a start-video command |
| `status` | Parse status.plist into JSON on the host; read-only |

Override `--path` and `--status-path` after `worker` for a different installation.
Worker actions deliberately have **no host command timeout**, since terminating
SSH can interrupt the elected recording process. `--timeout` still applies to
`worker status`, other short SSH commands and each RPC wait. Frida connection /
attach and teardown are governed by the Frida library and are not covered by that
RPC deadline. Ctrl-C is an interruption, not a detached background launch.

An installed helper is an on-demand worker, not necessarily a daemon that returns
immediately: the first elected process can remain attached to SSH throughout the
capture/upload and exit when idle. Use a **second terminal** for stop/status if
the first start invocation remains running. A successful enqueue is not proof
that the camera started. Check `state`, `error`, `pid`, timestamps, `exports` and
the actual resulting media. Inspect the installed version before assuming it
supports every action; pre-streaming releases may reject `stream-ram`.

The helper defaults to `/var/jb/usr/libexec/duskie-camera`; its settings and status
conventionally live under `/var/mobile` on this iOS setup. Feature enablement,
lens, format, audio and destination come from the shared tweak plist. This CLI
does not silently change them. Duskie 0.2.62's known architecture is described
in the project release notes; don't assume the still-running main session has
finished installing or publishing a candidate just because this guide exists.

### Notifications and custom scripts

```sh
./iphone-control notify com.dusklinux.edgedock/settings
./iphone-control --process Camera js ./my-probe.js --args '{"label":"test"}'
```

`notify` posts a Darwin notification through the attached process. Its code 0
means the post succeeded, not that a listener acted or persisted settings. Prefer
the helper's durable queued command interface for recording actions: notifications
can coalesce. Never depend on a stale status.plist as an acknowledgement.

`js` appends an explicitly provided file to the bundled runtime and calls
`rpc.exports.run(parameters)`. That file may use `checked`, `ns`, `text`, `onMain`
and the other helpers. It is an expert extension point, not a guarantee that an
arbitrary supplied script is safe or ABI-correct. See [EXTENDING.md](EXTENDING.md).

## Verification recipes

### Verify 4K60 instead of trusting a preference

1. Read `camera format` for native Camera, or the worker's reported `capture`
   dimensions/fps. Neither alone proves the final encoded file.
2. Confirm display state with `status` when testing screen-off worker behavior.
3. Monitor remote file growth **before** stop when testing streaming. An upload
   that appears only after stop is a different behavior.
4. Copy/download the finalized movie or inspect it on the actual FTP server:

```sh
ffprobe -v error -show_entries \
  format=duration,size:stream=index,codec_name,width,height,r_frame_rate \
  -of json recording.mp4
ffmpeg -v error -i recording.mp4 -f null -
```

A portrait capture can report 2160×3840 rather than 3840×2160; inspect orientation
and encoded frame rate rather than treating rotation as lower resolution.
Nominal 60 fps does not prove no drops. Compare encoded frame counts/timestamps
and any capture drop counters; inspect audio continuity too.

A 4K60 screen-off source probe in the main session recorded 174.149660 seconds,
1,170,222,804 bytes, while RAM snapshots stayed around 13–26 MiB; it decoded
without errors. A separate 18-second outage test continued at 4K60, with zero
reported dropped video frames/audio buffers. A final snapshot-during-video test
reported one dropped video frame. Those are finite observed tests, not a guarantee
for every scene, network, memory pressure level or future release. Native 4K60
testing was ongoing when this side conversation began; **no final result from
that unfinished main-thread test is claimed here**.

### Preferences: backup, edit deliberately, verify, restore

The shared Duskie plist is normally
`/var/mobile/Library/Preferences/com.dusklinux.edgedock.plist`. Download a byte-for-byte
backup before changing it. Edit a host copy with `plistlib`, upload that explicit
copy with correct ownership, then post the settings notification if appropriate.
Read it back and inspect the live setting. Restore the original when a temporary
test ends. The CLI exposes transfer/notify primitives but does not invent a
universal preferences-write command: app domains, cfprefsd caching, sandboxes and
root-owned files have different persistence paths.

The FTP provider's connection.plist is in an app-group container with a dynamic
UUID. Discover the container by its metadata identifier
`group.com.dusklinux.duskie.remoteftp`; do not reuse a UUID from an old transcript.
Connection files can contain credentials. Keep them out of published artifacts
and ordinary diagnostic output. This does not require a new credential store.

### Native RAM / FTP behavior worth remembering

- A Files provider can materialize a local cache. Saving through its displayed
  location is not automatically a kernel mount or proof of avoiding NAND writes.
- The Duskie capture tmpfs path observed was
  `/var/mobile/Library/Duskie/CameraRAM`. Verify its filesystem type and real free
  capacity with df/mount; directory existence alone is insufficient.
- The observed tmpfs did not support F_PUNCHHOLE prefix reclamation. Native Camera's
  seekable movie remained in RAM until finalization, even while bytes uploaded.
- `mount_tmpfs -o update -s ...` returned success but did not resize that volume.
  Measure capacity. Never overmount retained captures to make a test look empty.
- A native movie writer may rewrite its header/index or defragment at close.
  Uploading only an appended tail is not enough to prove a valid finalized movie.
- FTP control and passive data are separate connections. A fault-injection proxy
  must relay both or the server can reject passive-source addresses. That failure
  can otherwise be mistaken for a recording bug.
- If you need to hash an open RAM recording after its pathname is removed, use a
  small native helper that retains the file descriptor. `/dev/fd/3` was unavailable
  in the original phone's SSH test. Hash the **finalized** inode, not an earlier
  growing prefix. Test helpers are not bundled here.

## Failure diagnosis and recovery

| Symptom | Check / action |
|---|---|
| SSH connection refused | Existing iproxy, attached USB device, jailbreak SSH service and chosen port |
| SSH key failure | Interactive `shell`, correct account/identity or existing multiplexed connection; don't paste passwords into argv |
| Frida connection refused | Server process, its listen port and the host SSH forward |
| Missing frida-agent.dylib | Matching rootless package, signing and actual agent path; don't assume transport is broken |
| Expected one running process, found 0 | Launch the app, wait for readiness, then resolve its fresh PID |
| More than one matching process | Inspect `ps`, use a deliberate fresh `--pid`; don't select the first arbitrarily |
| ABI mismatch | Stop using that action; inspect methods in this exact runtime and review the typed call |
| Class unavailable | Wrong process, framework not loaded, different iOS/tweak version or absent injection |
| Missing video indicator / Photo mode | Select standard Video, wait for configuration, inspect status/format |
| accepted=true but no result | Check live state, capture errors, permissions/feature enablement and timestamps |
| RPC cancellation/timeout | Action may already have run; inspect actual state before repeating a toggle/start/stop |
| Script destroyed / process gone | Check fresh PID, crash/Safe Mode state and phone responsiveness; don't blindly rerun mutation |
| Duskie missing from modules | Check installation, injection configuration and Safe Mode separately |
| iPhone storage-full warning with free NAND | Distinguish native phone capacity from a remote capture's RAM/clip limit |
| Runtime UI labels differ | Inspect the actual labels; do not infer fps from private numeric enums |

### Safe Mode / UI restart

The original session demonstrated that passing a UTF-8 C string as an NSString
argument can crash SpringBoard. Another earlier probe called an absent Home
selector. The wrapper uses NSString construction, the actual completion-selector
and signature checks to avoid repeating those specific mistakes. This does not
make arbitrary private calls crash-proof.

If recovery is needed, first stop **only the Frida server/tunnel you own**, using
its recorded PID or foreground terminal. Do not use a broad killall while another
agent/session may be attached. Detach diagnostic scripts before a UI restart.

On the documented rootless ElleKit phone, `/var/mobile/.eksafemode` was the marker.
After identifying the diagnostic cause, an interactive root shell used:

```sh
# Stop the identified diagnostic Frida server first.
# Clear this marker only for the diagnosed/repaired incident:
rm -f /var/mobile/.eksafemode
/var/jb/usr/bin/sbreload
```

Do not erase a marker repeatedly to hide an unresolved production crash loop.
After respring, wait for a fresh SpringBoard PID, reattach only if needed, read
status and check that expected tweak injection returned. Follow the owner's
existing preference for **respring or userspace restart**; this guide does not
instruct a normal kernel reboot or jailbreak removal. It does not automatically
run recovery commands on errors.

### Cleanup without disrupting another session

The CLI detaches its own Frida session in `finally`. It installs no dylib, launch
daemon, notification listener, signing profile or server. Native changes made by
an action (a running recording, current Camera format, an app launch) persist until
changed deliberately. There is no undo transaction.

Stop only the forwards/server processes you started; remove only your own probe
files. Restore temporary preferences and Camera format selections. Preserve queued
RAM captures, user media, existing services, app-group settings and SSH keys.
Check that recording/upload activity is finished before deleting diagnostic files.
A parent/main agent may still own a server, tunnel or recording; coordinate in the
main conversation instead of taking it over from a side conversation.

## Local tests

These commands do not contact or control the phone:

```sh
python3 test_control.py
node --check runtime.js
node test_runtime.cjs
./iphone-control --help
./iphone-control camera --help
./iphone-control doctor
```

The test suite exercises meaningful host failure paths and raw-bridge contracts,
not the entire Objective-C runtime. Live acceptance should be done by the main
operator when the device is available. Start with read-only status/inspection,
then one clearly observed action, and check the result. See
[VALIDATION.md](VALIDATION.md) for a reproducible acceptance checklist.

## Primary references and historical sources

- [Frida JavaScript API](https://frida.re/docs/javascript-api/): NativeFunction,
  NativeCallback, module exports, RPC and script lifetime facilities.
- [Frida iOS guide](https://frida.re/docs/ios/): the instrumented-process model.
- [libusbmuxd / iproxy](https://github.com/libimobiledevice/libusbmuxd): USB proxy;
  the actual installed help is authoritative for the shown `LOCAL:DEVICE` syntax.
- Apple's shipped iOS SDK headers: Foundation object types and
  `UIControlEventTouchUpInside = 1 << 6`.
- [Existing unlock evidence](unlock.md): precise wake/unlock signatures, timing,
  historical failed approaches and a standalone Objective-C recovery implementation.
- Parent-session reference sources, if still present:
  `/mnt/zram1/duskie/build/review/remote.py`, `tools/shot/inspect.js`, and
  `build/remote-capture/` / `build/streaming/` probes. These are historical
  provenance only; the new tool does not load them or their temporary dylibs.
