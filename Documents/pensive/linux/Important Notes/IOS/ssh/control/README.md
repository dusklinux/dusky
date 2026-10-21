# iPhone control manual

Use this directory to operate the owner’s jailbroken iPhone from Linux and to
prepare targeted control helpers or tweaks. Commands are for **iPhone 8 Plus
(iPhone10,2), iOS 16.7.16 / 20H392, rootless `/var/jb`, ElleKit 1.2**. Confirm the
live device before applying its private API profile elsewhere. Use **respring or
userspace restart**, never a normal kernel reboot on this setup.

Use the active task and latest owner instructions for current connection/state
facts. This manual supplies procedures and capability limits; a documented
method is not permission to run it or proof that its server/helper is still
installed. [Daemon work](../../daemons/README.md) has a separate operating guide
for presets, native overrides, dependencies and TrollStore installation.

## Choose the procedure

| Task | Instructions |
|---|---|
| USB SSH, authentication, root, Frida server and forwarding | [BOOTSTRAP.md](BOOTSTRAP.md) |
| Commands for display, apps, UI, files and Camera | Command reference below |
| App settings, screenshots, Wi-Fi, recording and storage | [RECIPES.md](RECIPES.md) |
| Add a typed operation or inspect private APIs | [EXTENDING.md](EXTENDING.md) |
| Build, sign, install and remove an iOS control dylib | [DEVELOPMENT.md](DEVELOPMENT.md) |
| Unlock without Frida using a standalone injected helper | [unlock.md](unlock.md) |
| Readiness, completion checks and known capability limits | [VALIDATION.md](VALIDATION.md) |
| Focused logs, frozen apps and daemon batch isolation without Frida | [DIAGNOSTICS.md](DIAGNOSTICS.md) |

`iphone-control` is the host CLI; `runtime.js` is its injected helper. Keep them
together: the CLI loads runtime.js from its own directory. Optional recording
commands require the installed Duskie worker; ordinary UI control uses Frida.

## First-use sequence

1. Work from this directory. Run `./iphone-control --control-path /tmp/iphone-control-ssh doctor` locally. The CLI needs
   Python **3.15+**; UI commands also need the Frida package in that interpreter.
2. Follow BOOTSTRAP.md to reuse or establish USB SSH. Verify `./iphone-control --control-path /tmp/iphone-control-ssh ps`
   before diagnosing Frida. The examples use mobile plus interactive sudo;
   BOOTSTRAP.md also documents direct root when its authentication is verified. Match
   the SSH account and master socket throughout an operation.
3. For UI control, follow BOOTSTRAP.md for a matching server/agent and SSH forward.
   If already prepared, activate it with `source .venv/bin/activate`. Examples
   using `./iphone-control` assume that environment is active when Frida is required.
4. Complete the injection-readiness check below before attaching after a restart.
5. Run `status`, then inspect the correct process before requesting an action.
   Re-read its actual state afterward. An accepted request is not completion.
6. Restore temporary settings and remove only the helpers/forwards you created.

## Connections and options

| Setting | Default | Override |
|---|---|---|
| SSH | `mobile@127.0.0.1:2222` | `--target`, `--port`, or `IPHONE_SSH_TARGET` / `IPHONE_SSH_PORT` |
| Frida endpoint on Linux | `127.0.0.1:27047` | `--frida` / `IPHONE_FRIDA_ENDPOINT` |
| UI process | SpringBoard; Camera for `camera` | `--process` or freshly verified `--pid` |
| SSH/RPC wait | 30 seconds | `--timeout`, greater than 0 and at most 3600 |
| SSH identity | OpenSSH configuration | `--identity PATH` |
| Existing SSH master | None | `--control-path SOCKET` |

Put global options **before** the subcommand:

```sh
./iphone-control --control-path /tmp/iphone-control-ssh ps
./iphone-control --control-path /tmp/iphone-control-ssh --process Preferences controllers
./iphone-control --control-path /tmp/iphone-control-ssh --process Camera ui --max-nodes 800
```

Examples consistently use the authenticated master at `/tmp/iphone-control-ssh`.
Use the actual socket you established, or omit `--control-path` with working key
authentication. The socket is not created by the CLI. BOOTSTRAP.md shows how to create
one interactively. Noninteractive SSH/scp uses BatchMode: use an authenticated
master or working key. `shell` permits normal interactive authentication.
USB SSH, Frida forwarding and USB recording are separate connections. Do not
replace another running connection or interrupt a capture just to inspect it.

## Injection readiness after respring or userspace restart

A new SpringBoard PID is insufficient: attaching before ElleKit/Duskie finishes
initialization can cause a loader/hook lock wait. With Duskie installed:

```sh
./iphone-control --control-path /tmp/iphone-control-ssh ps
./iphone-control --control-path /tmp/iphone-control-ssh ssh -- cat /var/mobile/Library/Caches/Duskie-runtime.json
```

Wait until the report’s top-level `pid` matches the **current** SpringBoard
process and relevant initialization/error fields are satisfactory. An old file
is not readiness evidence. If the report is absent because injection is disabled
or Duskie is not installed, inspect the current injection/log state rather than
wait indefinitely. Other tweaks need their own readiness signal. Package version
comes from `dpkg-query`; a runtime diagnostic label is not a package version.

After a userspace restart, also recheck the Frida server and SSH forward: their
previous PIDs/connections may be gone. Start only missing owned resources using
BOOTSTRAP.md. A working SSH reconnect alone does not establish Frida readiness.

Foreground the target app before main-queue UI work when appropriate. A suspended
app or a locked display can prevent its main queue from servicing a probe.
Do not foreground another app while Camera needs the active capture session.

## Command reference


UI/RPC commands return JSON. `ssh` prints the remote command’s raw output. Frida diagnostic events go to stderr. PIDs are resolved
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
./iphone-control --control-path /tmp/iphone-control-ssh ssh -- df -k /var/mobile
./iphone-control --control-path /tmp/iphone-control-ssh get /var/mobile/Media/DuskieCamera/status.plist ./status.plist
./iphone-control --control-path /tmp/iphone-control-ssh put './file with spaces.plist' '/var/mobile/Media/file with spaces.plist'
./iphone-control --control-path /tmp/iphone-control-ssh ssh -- sh -c 'command -v ldid; command -v sbreload'
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
| `screenshot OUTPUT.png` | **Known broken on this profile**; use the explicit alternative in RECIPES.md |

```sh
./iphone-control --control-path /tmp/iphone-control-ssh status
./iphone-control --control-path /tmp/iphone-control-ssh wake
./iphone-control --control-path /tmp/iphone-control-ssh unlock
./iphone-control --control-path /tmp/iphone-control-ssh launch com.apple.camera
./iphone-control --control-path /tmp/iphone-control-ssh sleep
./iphone-control --control-path /tmp/iphone-control-ssh status
```

`unlock` comes from the owner's no-device-passcode configuration described in
[unlock.md](unlock.md). The SSH password is not a device passcode. It does not
claim to unlock a different device-passcode configuration. Duskie's simulated
lock is separate: `duskie-unlock` uses an installed Duskie class and is not required
on a phone without that feature. Neither command changes a saved passcode or
Touch ID enrollment. The `lock` and `duskie-unlock` wrappers are ABI-guarded but
need verification on the current device; see VALIDATION.md for capability status.

`status.duskieLoaded=false` is a clue, not a complete Safe Mode diagnosis: injection
can also be disabled or the payload may be absent. Pair it with the Safe Mode
marker and current process state. Read state after an action; a return code alone
is not proof that a requested transition completed.

### Inspecting apps and choosing UI controls

```sh
./iphone-control --control-path /tmp/iphone-control-ssh --process Camera controllers
./iphone-control --control-path /tmp/iphone-control-ssh --process Camera ui --max-nodes 800
./iphone-control --control-path /tmp/iphone-control-ssh --process Camera alerts
./iphone-control --control-path /tmp/iphone-control-ssh --process Camera dismiss-alert
./iphone-control --control-path /tmp/iphone-control-ssh --process Preferences methods PSListController --matching specifier
./iphone-control --control-path /tmp/iphone-control-ssh --process SpringBoard methods SpringBoard --matching launch
./iphone-control --control-path /tmp/iphone-control-ssh --process SpringBoard methods UIApplication --class-methods --matching shared
./iphone-control --control-path /tmp/iphone-control-ssh --process Camera modules --matching Duskie
./iphone-control --control-path /tmp/iphone-control-ssh --process YourApp tap --id unique-accessibility-identifier
./iphone-control --control-path /tmp/iphone-control-ssh --process YourApp tap --label 'Exact button title'
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
./iphone-control --control-path /tmp/iphone-control-ssh unlock
./iphone-control --control-path /tmp/iphone-control-ssh launch com.apple.camera
./iphone-control --control-path /tmp/iphone-control-ssh camera status
./iphone-control --control-path /tmp/iphone-control-ssh camera mode video
./iphone-control --control-path /tmp/iphone-control-ssh camera format
./iphone-control --control-path /tmp/iphone-control-ssh camera set-format --resolution 4k --fps 60
./iphone-control --control-path /tmp/iphone-control-ssh camera format
./iphone-control --control-path /tmp/iphone-control-ssh camera start
./iphone-control --control-path /tmp/iphone-control-ssh camera status
# Let the desired test run; separately monitor FTP/storage if needed.
./iphone-control --control-path /tmp/iphone-control-ssh camera stop
./iphone-control --control-path /tmp/iphone-control-ssh camera status
```

| Camera subcommand | Behavior |
|---|---|
| `status` | Current standard mode enum and recording state |
| `mode photo` / `mode video` / `mode slowmo` | Photo=0 / Video=1 / Slo-mo=2, device selector 0 as observed on this profile |
| `format` | Configuration enum, frame-rate/resolution enums and **displayed labels** |
| `fps-next` | Invoke the native frame-rate-indicator tap handler once |
| `resolution-next` | Invoke the native resolution-indicator tap handler once |
| `set-format --resolution ... --fps ...` | Cycle native controls, settle between steps and verify displayed labels |
| `start` | Start Video or Slo-mo; leave an already recording capture running |
| `stop` | Stop if recording; otherwise return unchanged |
| `photo` | Invoke native capturePhoto; actual result may finish asynchronously |

`set-format` accepts resolutions `4k`, `1080p`, `720p` and fps
`24,25,30,50,60,120,240` as requests, **not promises of device/mode support**.
The observed profile labels are `4K`, `HD` and `720P` (matched without case).
Choose `mode slowmo` before requesting 120/240 fps: `set-format` preserves an
existing Video or Slo-mo mode, but chooses Video if starting from Photo. Request
only combinations offered by the current mode and hardware.
If the UI cannot reach a request, the tool exits
with the current and original states rather than inventing a configuration enum.
A failed cycle may leave the Camera selection changed: use the printed original
format to restore it. Mode changes and direct cycle commands return a request;
read the settled state afterward.

Private configuration and frame-rate enums are not human-readable fps values.
Use displayed labels and inspect the finalized media; do not set guessed enums.

The CLI does not redirect native capture storage. Duskie's installed
**Camera app → Remote storage** switch in **Camera & Voice Notes**, and the
**Remote connection (USB / Wi-Fi)** page determine that independently. A standard
native capture normally requires the Camera app's foreground capture session;
do not assume locking the display preserves a native recording. Use Duskie's
worker for its screen-off recording feature.

### Duskie screen-off worker

These commands need SSH and an installed Duskie release, **not Frida**:

```sh
./iphone-control --control-path /tmp/iphone-control-ssh worker status
./iphone-control --control-path /tmp/iphone-control-ssh worker start
./iphone-control --control-path /tmp/iphone-control-ssh worker photo
./iphone-control --control-path /tmp/iphone-control-ssh worker stop
./iphone-control --control-path /tmp/iphone-control-ssh worker upload-ram
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
| `prepare-native` | Prepare selected remote configuration / RAM staging; not read-only |
| `test-connection` | Test selected FTP/USB endpoint without recording; writes a separate result plist |
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
does not silently change them. Inspect the installed package with dpkg-query before assuming a worker action
is supported. See RECIPES.md for transports, connection tests and receiver control.

### Notifications and custom scripts

```sh
./iphone-control --control-path /tmp/iphone-control-ssh notify com.dusklinux.edgedock/settings
./iphone-control --control-path /tmp/iphone-control-ssh --process Camera js ./my-probe.js --args '{"label":"test"}'
```

`notify` posts a Darwin notification through the attached process. Its code 0
means the post succeeded, not that a listener acted or persisted settings. Prefer
the helper's durable queued command interface for recording actions: notifications
can coalesce. Never depend on a stale status.plist as an acknowledgement.

`js` appends an explicitly provided file to the bundled runtime and calls
`rpc.exports.run(parameters)`. That file may use `checked`, `ns`, `text`, `onMain`
and the other helpers. It is an expert extension point, not a guarantee that an
arbitrary supplied script is safe or ABI-correct. See [EXTENDING.md](EXTENDING.md).

## Failure diagnosis

| Symptom | Next step |
|---|---|
| SSH refused/reset | Check USB device, existing iproxy and jailbreak SSH; allow userspace restart to finish |
| SSH authentication fails | Check account, matching master/socket or key; try the verified mobile-plus-sudo route in BOOTSTRAP.md; do not assume direct root works |
| Frida connection refused | Check server process, device listen port and host SSH forward |
| Agent dylib missing | Inspect the exact requested path; follow BOOTSTRAP.md’s conditional path/signing procedure |
| Signed server immediately killed | Check original server entitlements and the modified binary’s signature |
| Process count 0 | Launch it, wait for readiness, resolve a fresh PID |
| Multiple matches | Inspect the process list and choose a fresh explicit PID |
| ABI mismatch or absent class/selector | Stop that call; inspect process, loaded framework and current method encoding |
| RPC/attach hangs | Check injection readiness, app suspension and concurrent tracing; do not blindly attach again |
| Foreground app freezes or a sign-in page never appears | Collect a targeted, symbolicated stack sample using DIAGNOSTICS.md; distinguish a main-thread wait from a missing daemon |
| Action times out | It may already have run; inspect actual state before repeating a toggle/start/stop |
| Duskie missing from modules | Check installation, injection filter and Safe Mode separately |
| Screenshot missing export | Built-in capture is unavailable here; use RECIPES.md’s explicit alternative |
| Auto-Lock greyed | Check profiled, effective policy and LPM; see RECIPES.md |
| USB recording unavailable | SSH alone is insufficient; check receiver and reverse forwards |
| Preview URL returns HTML | Use the media endpoint documented in RECIPES.md |

### Recover from a diagnosed UI crash

1. Inspect the current process, crash report and last operation. Private-object
   `description`, wrong ObjC argument types and absent selectors can execute
   crashing code. Do not repeatedly invoke the same failed probe.
2. Detach and stop only your diagnostic server/forward. Do not broadly kill
   processes or remove another operator’s injection files.
3. Repair/remove the identified cause. If ElleKit placed the diagnosed incident
   in Safe Mode, its marker on this phone is `/var/mobile/.eksafemode`.
4. In an interactive root shell, only for that repaired incident:

```sh
rm -f /var/mobile/.eksafemode
/var/jb/usr/bin/sbreload
```

5. Reconnect, discover the fresh SpringBoard PID, wait for injection readiness
   and verify normal operation. Do not clear the marker to conceal an unresolved
   crash loop. Do not perform a normal kernel reboot.

### Finish an operation

Read the actual resulting state. Restore temporary preferences/Camera format;
preserve user media, pending uploads and deliberately retained changes. Frida
`detach` does not unload a separately dlopened helper or undo native actions.
Stop only owned server/forward processes and remove their recorded probe paths.
A process restart is needed to remove live callbacks/hooks from a loaded helper.

## Local tool checks

These commands do not contact the phone:

```sh
python3 test_control.py
node --check runtime.js
node test_runtime.cjs
./iphone-control --control-path /tmp/iphone-control-ssh --help
./iphone-control --control-path /tmp/iphone-control-ssh doctor
```

Host tests exercise parser/bridge behavior; they do not emulate UIKit or prove a
private ABI works. Use VALIDATION.md’s readiness/completion checks on the phone.
See [Frida’s JavaScript API](https://frida.re/docs/javascript-api/) for its native
bridge and [iproxy](https://github.com/libimobiledevice/libusbmuxd) for USB proxying.
