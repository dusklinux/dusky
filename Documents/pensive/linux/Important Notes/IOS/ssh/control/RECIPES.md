# Operational recipes and evidence boundaries

Use [README.md](README.md) for command syntax and [BOOTSTRAP.md](BOOTSTRAP.md)
when Frida is unavailable. Commands below can change phone state: use them only
within the active task, after checking for a recording or another operator.
The raw snippets are reviewed examples, not newly device-accepted wrapper tests.

## Find the right process and inspect before acting

```sh
./iphone-control ps
# Frida commands require the prepared interpreter and server/forward:
.venv/bin/python ./iphone-control apps
.venv/bin/python ./iphone-control launch com.dusklinux.duskie.remoteftp
.venv/bin/python ./iphone-control --process DuskieRemoteFTP controllers
.venv/bin/python ./iphone-control --process DuskieRemoteFTP ui
```

Known app identities: native Camera `com.apple.camera` / process `Camera`;
Settings `com.apple.Preferences` / `Preferences`; Duskie
`com.dusklinux.duskie.remoteftp` / `DuskieRemoteFTP`. App display name is **Duskie**.
Discover identifiers for other apps with `apps`; never infer the executable from
the displayed name. A launched app can take time to build its UI. Don't launch
another camera client while one owns an active capture session.

Prefer an exposed semantic action over simulated coordinates. `tap` works only
on a uniquely matched UIControl. Table rows often require the actual table
delegate's `tableView:didSelectRowAtIndexPath:` or an application-specific action,
with the current table/index path and checked ABI. Inspect the current controller
and its data source first; row numbers and addresses from old dumps are stale.
There is no bundled universal swipe, drag, text-entry or three-finger HID injector.
The installed tweak's gesture actions are configured through its settings.

## Read or change shared Duskie settings through its controller

The Home Screen app and Settings tweak pane share `EDRootController` and the
same preference domain. In the app the root navigation stack contains that
controller. Use bounded `controllers()` to find it rather than assuming a window
index or walking a presenting/presented cycle indefinitely.

Save this example as your own `settings-probe.js` outside the core runtime:

```js
rpc.exports.run = parameters => onMain(() => {
    const matches = controllers().filter(x => x.className === 'EDRootController');
    if (matches.length !== 1) throw Error('Open the Duskie settings root first');
    const root = matches[0].object;
    const specs = checked(root, 'specifiers', '@16@0:8');
    const rows = [];
    for (let i = 0; i < count(specs); i++) {
        const spec = at(specs, i);
        const key = text(checked(spec, 'propertyForKey:', '@24@0:8@16',
                                 'pointer', ['pointer'], [ns('key')]));
        if (!key) continue;
        const value = checked(root, 'getValue:', '@24@0:8@16',
                              'pointer', ['pointer'], [spec]);
        rows.push({key, value: value.isNull() ? null : text(send0(value, 'description'))});
    }
    return rows;
});
```

```sh
.venv/bin/python ./iphone-control --process DuskieRemoteFTP js ./settings-probe.js
```

For a deliberate write, find the **actual specifier by its key**, construct an
NSString with `ns()` or an NSNumber with the appropriate checked factory, then
call `setValue:specifier:` (observed standard encoding `v32@0:8@16@24`, return
void, two pointer arguments). Do not pass a raw JS string/boolean as an Objective-C
object. For example `NSNumber numberWithBool:` has `@20@0:8B16`, takes a native
bool represented as **0 or 1**, and returns a pointer. Read `getValue:` afterward
and check the live behavior. This uses the page's own persistence/notification
logic, which is preferable to assuming a direct plist replacement invalidates all
cfprefsd or application caches. Back up before temporary changes and restore them.

Key settings in the reviewed 0.2.65 source:

| Key | Value / role |
|---|---|
| `nativeCameraRemote` | Native Camera remote-storage switch |
| `cameraDestination` | `photos`, `folder`, or `remote` for screen-off capture |
| `cameraRemoteTransport` | `ftp` (default) or `usb`, shared by both capture paths |
| `cameraRAMBufferMiB` | Configured RAM queue choice: 256 / 512 / 1024 MiB |
| `cameraVideoMode_back` | Screen-off back-camera format, e.g. `3840x2160@60` / `1920x1080@240` |
| `cameraCodec` | `auto`, `h264`, `hevc`; hardware capabilities still apply |

Read all other current keys/defaults/specifiers from the controller or source;
this table is an operational guide, not a full schema. The shared plist is
`/var/mobile/Library/Preferences/com.dusklinux.edgedock.plist`; settings notification
is `com.dusklinux.edgedock/settings`. A Darwin post is not proof a listener finished.

Useful root controller actions in 0.2.65 are `testRecordingConnection`,
`openUSBPreview`, `retryRAMUploads`, `cameraStatus` and `stopCamera`. They are void,
no-argument methods (`v16@0:8`); inspect with `methods EDRootController` first and
invoke with `checked` on the main queue. Testing opens a result alert; retry/stop
are mutations. Prefer the SSH worker for capture control when Frida is unnecessary.

## Native Slo-mo and ordinary video

These are format choices, not a whitelist restricting recording to stress formats.
1080p30 was recorded successfully. Only request combinations offered by the phone.

```sh
.venv/bin/python ./iphone-control launch com.apple.camera
.venv/bin/python ./iphone-control camera mode slowmo
# Wait for mode change to settle, then:
.venv/bin/python ./iphone-control camera status
.venv/bin/python ./iphone-control camera set-format --resolution 1080p --fps 240
.venv/bin/python ./iphone-control camera format
.venv/bin/python ./iphone-control camera start
# In a separate invocation, after the intended interval:
.venv/bin/python ./iphone-control camera stop
.venv/bin/python ./iphone-control camera status
```

For normal Video, use `camera mode video` and then `set-format --resolution 4k
--fps 60` or `--resolution 1080p --fps 30`. Confirm the **displayed labels** after
settling, and inspect finalized media with ffprobe/full decode. Camera can reopen
in Photo; a saved preference or private numeric enum is insufficient. Slo-mo mode
is 2 on this device; its high-frame-rate source is not automatically a slowed
playback file. Use player speed controls for slow motion.

Duskie's native hooks own the remote writer. Its explicit viewfinder stop hook
is required after removing the ordinary movie output; stopping only the removed
output was insufficient. Use Camera's own start/stop methods and confirm stopped
state. Do not run a second native session or change mode/format mid-recording.
For screen-off capture, use the worker with its saved format/destination; locking
native Camera is not a substitute for that separate feature.

## USB receiver and preview

These paths refer to the existing laptop checkout; if relocated, use the actual
checkout. The control CLI does not contain the receiver implementation.

```sh
cd /mnt/zram1/duskie
bash laptop/start-receiver.sh --directory /mnt/zram1
systemctl --user status duskie-usb
journalctl --user -u duskie-usb -n 30
```

Reuse an active service. Setup dependencies/flags are in `laptop/README.md`:
Python 3.15, pyftpdlib 2.2.0 in its dedicated environment, OpenSSH, iproxy and
working USB SSH. The launcher starts a runtime user unit with restart/reconnect;
run it again after a login/reboot when needed. It does not install an always-on
boot service. `systemctl --user restart duskie-usb` and `stop duskie-usb` affect
active uploads, so do not use them merely to inspect a working service.

`--directory` is the **parent** of `Duskie Captures`. Current received media is
under **`/mnt/zram1/Duskie Captures`**. This receiver uses its own loopback FTP
2240 and passive ports 2241–2248, reverse-forwarded over SSH, and HTTP 8765.
The existing Wi-Fi FTP server/account/Files provider remain separate. A connected
cable plus iproxy alone is insufficient: the receiver and reverse forwards must
also be alive. USB can operate with Wi-Fi off.

On the phone select **Duskie → Remote recording connection → USB / laptop
receiver**; enable **Camera app → Remote storage** for native Camera or **Save
photos & videos to → Remote storage** for screen-off recording. Use **Test
selected recording connection** before capturing. Selecting Wi-Fi / Remote FTP
preserves the original FTP method; it does not use this managed receiver.

For preview on the laptop:

```sh
mpv http://127.0.0.1:8765/
# Explicit media endpoint:
mpv http://127.0.0.1:8765/live.m3u8
```

The updated receiver redirects media clients to the latest recording playlist.
A browser visiting the root, or `/recordings`, gets the recording index. An older
receiver returned HTML to mpv at the bare URL, causing “Failed to recognize file
format”; that was fixed in repository commit `bda9d3b`. Before any segments arrive,
the media endpoint returns 503 with an explanation. Open after segments arrive.
Opening selects the newest media by media modification time and pins that movie;
it does not switch automatically to the next recording. Seek within received
history. Preview trails capture and is not a zero-latency camera feed.

Per-recording playlists are `.duskie-preview/UUID/index.m3u8`; byte ranges point
at a hard link of the original media, with no second full copy or re-encoding.
Keep that preview directory and media intact during capture. Removing only one
hard link won't release the inode's space. Browser HEVC support varies; mpv was
the tested player. The phone can open the same loopback URL while its preview
reverse forward is active; its USB settings provide copy/open controls.

## Test selected connection without recording

From the control directory:

```sh
./iphone-control worker test-connection
./iphone-control get /var/mobile/Library/Duskie/connection-test.plist ./connection-test.plist
```

Read the result with host `plistlib`. Check **time >= request start**, the expected
`transport` (`ftp`/`usb`), `success`, and `message`. An old result or SSH exit code
is not a fresh connection test. The test checks login and a passive data listing;
it starts no recording, but opens transport connections and writes a result.
The UI performs this timestamp/transport check itself. Failure should show a
phone popup before recording; an active capture transfer failure also alerts.
Missing receiver/network does not deliberately fall back to phone storage.

Worker status is `/var/mobile/Media/DuskieCamera/status.plist`. `prepare-native`
prepares RAM/configuration and is not read-only. `upload-ram` retries retained
media; use the original transport for an existing stream. The first elected
worker may remain attached through the capture/upload; use another terminal
for stop/status and do not kill it just because SSH has not returned.

## Wi-Fi control over USB

Wi-Fi state is available in SpringBoard's `SBWiFiManager`. Save this as a custom
JS file, then run with `--process SpringBoard js FILE --args '{"enabled":false}'`
to disable, or `{}` for a read-only query. Only change radios when the task permits;
turning Wi-Fi off disrupts Wi-Fi FTP even though USB SSH stays connected.

```js
rpc.exports.run = parameters => onMain(() => {
    const manager = shared('SBWiFiManager');
    const before = boolean(manager, 'wiFiEnabled');
    if (parameters.enabled !== undefined) {
        if (typeof parameters.enabled !== 'boolean') throw Error('enabled must be boolean');
        checked(manager, 'setWiFiEnabled:', 'v20@0:8B16', 'void',
                ['bool'], [parameters.enabled ? 1 : 0]);
    }
    return {before, enabled: boolean(manager, 'wiFiEnabled')};
});
```

This known ABI is also checked by the tweak's radio code. Re-read after settling;
radio enabled does not prove association, routing or FTP reachability. Restore
the prior value after a temporary USB isolation test.

## Files, uploads, NAND and storage errors

The Remote FTP Files location is a File Provider, not proof of a kernel FTP mount.
Provider reads can materialize a cache. Discover its app-group UUID from container
metadata matching `group.com.dusklinux.duskie.remoteftp`; then locate the current
connection.plist. Never reuse a UUID from a transcript. Parse binary plists with
host plistlib and keep credentials out of ordinary output. Check both configured
host and FTP control/passive connectivity, not just a successful TCP port probe.

Duskie 0.2.63+ native and screen-off video write RAM segments, upload acknowledged
data, then reclaim segments. A 1-GiB buffer is a **pending backlog**, not a total
movie allowance. If transfer cannot keep up, RAM can still fill and stop capture;
a larger queue cannot fix permanently insufficient throughput. Normal Photos/
local-folder destinations still write locally; temporary RAM/remote capture
paths avoid a full local video staging file, not every iOS system/cache write.
No NAND-write counter or write-amplification reduction was measured.

The old native 0.2.62 seekable-file path could stop around 827 MB despite free
phone NAND; the RAM staging limit was separate. Do not use that old limitation
as an explanation for every modern failure. Check installed version, active
transport, status/error, mount type/capacity, pending acknowledgements, RAM free
space and actual server growth. `mount_tmpfs -o update -s ...` did not resize this
phone's mounted volume even when it returned success. Never overmount retained
captures or delete pending media to hide the failure.

## Tool availability, signing and recovery

Use host tools to parse device output. This phone has `/var/jb/usr/bin/python3`,
ldid and sbreload; `notifyutil` and an installed frida-server were absent at the
review. Don't assume Linux rg/awk/pgrep/plutil are on the phone PATH. The known
process query is `ps -A -o pid,comm`. `./iphone-control ssh -- sh -c 'command -v python3'` checks availability explicitly. A noninteractive PATH may differ from a
root login shell; use confirmed absolute paths when needed.

`notify` can use Frida; the historical unlock note also supplies a phone Python
ctypes/libnotify route when notifyutil is absent. For compiled helpers use the
host iOS SDK/cross toolchain, not a nonexistent device compiler. Inspect SDK
metadata: a directory named iPhoneOS16.5 contained real SDK version 16.4 in the
main setup. Sign modified Mach-O payloads with their required original
entitlements; privileged CLI helpers and UIKit-injected dylibs have different
requirements. Install/package only within the active task; this notes review
needed no deployment. The Duskie repo's build/research notes hold release-specific
build flags and signing policy; do not reconstruct them from old command history.

A generic `dismiss-alert` does not run UIAlertAction handlers. A Duskie-owned
SpringBoard alert window can remain retained/key until its actual handler cleans
it up. `_invokeHandler` was absent on this runtime; `handler` returned a block
with encoding `@?16@0:8`. Executing a block needs a correctly typed ObjC.Block
bridge/native helper and known callback signature; a guessed block memory offset
is not a universal action API. Use supported semantic methods or review that
specific handler rather than blindly poking a label or private offset.

If Safe Mode occurs, collect the actual process/crash state and identify your
last mutation. Wrong NSString/C-string arguments and absent-selector diagnostic
calls caused earlier recoverable incidents. Stop owned Frida before respring;
see README's marker/recovery procedure and unlock.md. After a controlled respring,
SSH may drop briefly; reconnect and resolve all process/object addresses again.
Don't normal-reboot the phone or repeatedly clear a marker to mask a crash loop.

## Compiled helper injection and tweak action probes

Earlier main-session probes also used small signed Objective-C dylibs loaded into
the **target app**, via Frida `dlopen`/`dlsym`. This lets compiled UIKit code handle
blocks and complex signatures without recreating an entire bridge. The preserved
unlock.md supplies a complete minimal source/build/install alternative using a
SpringBoard-filtered tweak and Darwin notifications; that route requires a
controlled respring and explicit removal afterward. The minimal helper has only
unlock/snapshot listeners, not all functions from the larger historical helper.

For an already built/signed helper whose reviewed interface is
`const char *DKReview(const char *operation)`, a custom runtime extension can use:

```js
rpc.exports.run = parameters => onMain(() => {
    if (typeof parameters.path !== 'string' || typeof parameters.operation !== 'string')
        throw Error('Provide the reviewed helper path and operation');
    const handle = native('dlopen', 'pointer', ['pointer', 'int'])(utf8(parameters.path), 2);
    if (handle.isNull()) throw Error('Diagnostic dlopen failed; inspect dlerror/signing');
    const symbol = native('dlsym', 'pointer', ['pointer', 'pointer'])(handle, utf8('DKReview'));
    if (symbol.isNull()) throw Error('DKReview symbol missing');
    const result = new NativeFunction(symbol, 'pointer', ['pointer'])(utf8(parameters.operation));
    if (result.isNull()) throw Error('Diagnostic returned no result');
    return JSON.parse(result.readUtf8String());
});
```

Only use that ABI for a helper whose source actually defines it. Copy the returned
C string before the next invocation can replace its backing storage. An async
helper may return only “queued” and later write a result plist: check its real
completion/timestamp. Different helpers, such as gesture probes, use different
entry signatures. No diagnostic dylib is bundled or assumed still installed.
Detaching Frida **does not unload a dlopen library** or undo hooks; a fresh process
may be required to remove it. Don't dlclose code with live callbacks/objects.
Use a new filename for a rebuilt helper or restart its process deliberately;
dlopen of the same path may reuse the already loaded image.

The earlier `LiveProbe.m` called real table/collection delegates, bar-button
`sendAction:to:from:forEvent:`, FileProvider domain enumeration, native view
inspection and UIKit window screenshot capture. Its source is historical under
`/mnt/zram1/duskie/build/review/`; old row indices and unbounded traversal from a
diagnostic are not reusable control APIs. A framework's domain enumeration callback
also needs a live process/event loop; the initial queued response isn't the result.
The bundled screenshot route uses UICreateScreenUIImage and returns PNG to the
host; that wrapper still needs live acceptance. `idevicescreenshot` failed here
because screenshotr/developer-disk-image support was unavailable. Repeatedly
retrying it is not a substitute for the known UIKit capture route.

For tweak actions, a main-session probe resolved `DKExecuteAction` from
`Duskie.dylib`'s **symbol table**, then called it as `BOOL(NSString *, BOOL dryRun)`
on SpringBoard's main queue. `lowPower` with dryRun=0 toggled the setting; dryRun=1
only checks supported actions. The function is static/internal, not a guaranteed
export: use source `src/HomeButtons.m`, the actual module's enumerateSymbols and
its current signature, never a fixed address or guessed fallback. New builds may
strip it. For capture actions prefer the durable worker interface. Gesture tests
used a compiled UIPanGestureRecognizer subclass with controlled touch count and
translation to exercise `DKThreeFingerHandler`; that verifies routing, not physical
three-finger input delivery. Don't claim a successful handler test proves HID.

## Evidence to carry forward

Installed package read back 2026-10-21: `com.dusklinux.edgedock 0.2.65~duskie1`.
Source checkout: `/mnt/zram1/duskie`, upstream
[https://github.com/dusklinux/duskie](https://github.com/dusklinux/duskie).
Release-specific evidence, if the checkout is still present:

- `research/NATIVE-STREAMING-0.2.63.md`: native segmented writer / Slo-mo.
- `research/USB-PREVIEW-0.2.64.md`: finite stress-format captures / live seek.
- `research/CONNECTION-UI-0.2.65.md`: receiver reconnect, phone test controls, HD30.
- `research/PREVIEW-URL-FIX.md`: bare mpv URL repair.
- `laptop/README.md`: receiver dependencies / controls.

These files substantiate earlier main-session tests; no claim is made that every
example in these new notes has since been live-operated. Long final-release
recordings, sustained full-backlog/cable-loss recovery and battery/heat testing
remain the user's planned tests. Keep new results dated and scoped in VALIDATION.
