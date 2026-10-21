# Operational recipes

Use [README.md](README.md) for command syntax and [BOOTSTRAP.md](BOOTSTRAP.md)
when Frida is unavailable. Commands below can change phone state: use them only
within the active task, after checking for a recording or another operator.
Custom snippets are templates using checked ABIs. Inspect the current runtime
and verify their result; they are not universal UI automation APIs.

## Find the right process and inspect before acting

```sh
./iphone-control --control-path /tmp/iphone-control-ssh ps
# Frida commands require the prepared interpreter and server/forward:
.venv/bin/python ./iphone-control --control-path /tmp/iphone-control-ssh apps
.venv/bin/python ./iphone-control --control-path /tmp/iphone-control-ssh launch com.dusklinux.duskie.remoteftp
.venv/bin/python ./iphone-control --control-path /tmp/iphone-control-ssh --process DuskieRemoteFTP controllers
.venv/bin/python ./iphone-control --control-path /tmp/iphone-control-ssh --process DuskieRemoteFTP ui
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
        let valueText = null;
        if (!value.isNull()) {
            if (isKind(value, 'NSString')) valueText = text(value);
            else if (isKind(value, 'NSNumber'))
                valueText = text(checked(value, 'stringValue', '@16@0:8'));
            else valueText = '<' + className(value) + '>';
        }
        rows.push({key, valueText});
    }
    return rows;
});
```

```sh
.venv/bin/python ./iphone-control --control-path /tmp/iphone-control-ssh --process DuskieRemoteFTP js ./settings-probe.js
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

For a copyable write operation, save this separately as `settings-write.js`:

```js
rpc.exports.run = parameters => onMain(() => {
    if (typeof parameters.key !== 'string') throw Error('Provide a setting key');
    const roots = controllers().filter(x => x.className === 'EDRootController');
    if (roots.length !== 1) throw Error('Open the Duskie settings root');
    const root = roots[0].object;
    const specs = checked(root, 'specifiers', '@16@0:8'), matches = [];
    for (let i = 0; i < count(specs); i++) {
        const spec = at(specs, i);
        const key = text(checked(spec, 'propertyForKey:', '@24@0:8@16',
                                 'pointer', ['pointer'], [ns('key')]));
        if (key === parameters.key) matches.push(spec);
    }
    if (matches.length !== 1) throw Error('Expected exactly one setting');
    let value;
    if (typeof parameters.value === 'boolean')
        value = checked(classNamed('NSNumber'), 'numberWithBool:', '@20@0:8B16',
                        'pointer', ['bool'], [parameters.value ? 1 : 0]);
    else if (typeof parameters.value === 'string') value = ns(parameters.value);
    else if (Number.isSafeInteger(parameters.value))
        value = checked(classNamed('NSNumber'), 'numberWithInteger:', '@24@0:8q16',
                        'pointer', ['int64'], [parameters.value]);
    else throw Error('This template accepts Boolean, string or safe integer');
    checked(root, 'setValue:specifier:', 'v32@0:8@16@24', 'void',
            ['pointer', 'pointer'], [value, matches[0]]);
    const saved = checked(root, 'getValue:', '@24@0:8@16',
                          'pointer', ['pointer'], [matches[0]]);
    const savedText = saved.isNull() ? null : isKind(saved, 'NSString') ? text(saved) :
        isKind(saved, 'NSNumber') ? text(checked(saved, 'stringValue', '@16@0:8')) : null;
    return {key: parameters.key, savedText, note: 'Verify the feature state too'};
});
```

Example, only when USB recording is the intended destination:

```sh
./iphone-control --control-path /tmp/iphone-control-ssh --process DuskieRemoteFTP js ./settings-write.js \
  --args '{"key":"cameraRemoteTransport","value":"usb"}'
```

The template checks unique membership before changing anything. It deliberately
rejects unsupported value types; for a different type inspect that setting and
add its actual typed factory, rather than force every value through a string.

Useful Duskie keys; inspect the installed controller for its current schema:

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

Useful root controller actions are `testRecordingConnection`,
`openUSBPreview`, `retryRAMUploads`, `cameraStatus` and `stopCamera`. They are void,
no-argument methods (`v16@0:8`); inspect with `methods EDRootController` first and
invoke with `checked` on the main queue. Testing opens a result alert; retry/stop
are mutations. Prefer the SSH worker for capture control when Frida is unnecessary.

## Native Slo-mo and ordinary video

Only request format combinations offered by the current mode and hardware.

```sh
.venv/bin/python ./iphone-control --control-path /tmp/iphone-control-ssh launch com.apple.camera
.venv/bin/python ./iphone-control --control-path /tmp/iphone-control-ssh camera mode slowmo
# Wait for mode change to settle, then:
.venv/bin/python ./iphone-control --control-path /tmp/iphone-control-ssh camera status
.venv/bin/python ./iphone-control --control-path /tmp/iphone-control-ssh camera set-format --resolution 1080p --fps 240
.venv/bin/python ./iphone-control --control-path /tmp/iphone-control-ssh camera format
.venv/bin/python ./iphone-control --control-path /tmp/iphone-control-ssh camera start
# In a separate invocation, after the intended interval:
.venv/bin/python ./iphone-control --control-path /tmp/iphone-control-ssh camera stop
.venv/bin/python ./iphone-control --control-path /tmp/iphone-control-ssh camera status
```

For normal Video, use `camera mode video` and then `set-format --resolution 4k
--fps 60` or `--resolution 1080p --fps 30`. Confirm the **displayed labels** after
settling, and inspect finalized media with ffprobe/full decode. Camera can reopen
in Photo; a saved preference or private numeric enum is insufficient. Slo-mo mode
is 2 on this device; its high-frame-rate source is not automatically a slowed
playback file. Use player speed controls for slow motion.

Duskie's native hooks own the remote writer. Use Camera's own start/stop
methods and confirm stopped state rather than stopping an output the tweak
has replaced. Do not run a second native session or change mode/format mid-recording.
For screen-off capture, use the worker with its saved format/destination; locking
native Camera is not a substitute for that separate feature.

## USB receiver and preview

This optional receiver belongs to the Duskie checkout, not this CLI. Replace the
two example paths below with the actual checkout and desired receive directory.

```sh
cd /absolute/path/to/duskie
bash laptop/start-receiver.sh --directory /absolute/path/to/receive-parent
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
under **`RECEIVE_PARENT/Duskie Captures`**. This receiver uses its own loopback FTP
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

The receiver redirects media clients to the latest recording playlist.
A browser visiting the root, or `/recordings`, gets the recording index. If a receiver returns HTML to mpv at the bare URL, use `/live.m3u8` and inspect
its installed version instead of treating the HTML as a movie. Before any segments arrive,
the media endpoint returns 503 with an explanation. Open after segments arrive.
Opening selects the newest media by media modification time and pins that movie;
it does not switch automatically to the next recording. Seek within received
history. Preview trails capture and is not a zero-latency camera feed.

Per-recording playlists are `.duskie-preview/UUID/index.m3u8`; byte ranges point
at a hard link of the original media, with no second full copy or re-encoding.
Keep that preview directory and media intact during capture. Removing only one
hard link won't release the inode's space. Browser HEVC support varies; use mpv
when browser playback is unsupported. The phone can open the same loopback URL
while its preview
reverse forward is active; its USB settings provide copy/open controls.

## Test selected connection without recording

From the control directory:

```sh
./iphone-control --control-path /tmp/iphone-control-ssh worker test-connection
./iphone-control --control-path /tmp/iphone-control-ssh get /var/mobile/Library/Duskie/connection-test.plist ./connection-test.plist
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

For a segmented Duskie capture, RAM is a pending-upload backlog, not the total
movie allowance. Transfer must keep up over time; a larger RAM queue cannot fix
permanently insufficient throughput. Normal Photos/local destinations still write
locally. A remote capture path does not eliminate every system/cache write.

When storage errors occur, inspect installed worker version, active transport,
status/error, mount type/capacity, pending acknowledgements and actual receiver
file growth. Distinguish free NAND from a full RAM staging queue. On this setup,
`mount_tmpfs -o update -s ...` did not resize an existing volume despite returning
success; measure with df/mount. Never overmount retained media or delete pending
captures to hide a failure. A File Provider location can materialize a local cache.

## Screenshot when the built-in command is unavailable

The bundled `screenshot OUTPUT.png` currently looks up `UICreateScreenUIImage`,
which is absent on this profile. Do not loop that command or rely on
idevicescreenshot when screenshotr/developer-disk-image support is unavailable.
The installed Duskie capture path uses `_UICreateScreenUIImageWithRotation(BOOL)`.
The following explicit adapter uses that known C signature and PNG conversion;
verify its image/orientation on the current runtime before relying on it.

Save as `screen-capture.js` in your work directory:

```js
rpc.exports.run = () => onMain(() => {
    const image = native('_UICreateScreenUIImageWithRotation', 'pointer', ['bool'])(1);
    if (image.isNull()) throw Error('Screen capture unavailable; check display state');
    const data = native('UIImagePNGRepresentation', 'pointer', ['pointer'])(image);
    if (data.isNull()) throw Error('PNG encoding failed');
    const encoded = checked(data, 'base64EncodedStringWithOptions:',
                            '@24@0:8Q16', 'pointer', ['uint64'], [0]);
    return {pngBase64: text(encoded)};
});
```

Run in SpringBoard after readiness, then decode on Linux:

```sh
./iphone-control --control-path /tmp/iphone-control-ssh --process SpringBoard js ./screen-capture.js > ./screen-capture.json
python3 - <<'PYPNG'
import base64, json
from pathlib import Path
result = json.loads(Path('screen-capture.json').read_text())['result']
Path('screen.png').write_bytes(base64.b64decode(result['pngBase64'], validate=True))
PYPNG
```

Inspect the resulting PNG dimensions/orientation. Do not publish screen contents.
The adapter is not a claim that the unchanged built-in screenshot route works.

## Auto-Lock and Low Power Mode policy

Keep `com.apple.managedconfiguration.profiled` available when native Auto-Lock
or managed feature settings are needed. A disabled/unregistered service can leave
a stale battery-saver `maxInactivity = 30` policy even with Low Power Mode off,
grey out Auto-Lock and make native writes fail with “Couldn't communicate with a
helper application.” Check the current override, loaded job and effective
`MCProfileConnection` settings before attributing every grey row to that cause.

The existing job file is
`/System/Library/LaunchDaemons/com.apple.managedconfiguration.profiled.plist`.
Inspect first from an appropriately privileged phone shell:

```sh
/var/jb/bin/launchctl print-disabled system
/var/jb/bin/launchctl print system/com.apple.managedconfiguration.profiled
```

For a repair that requires the service, enable it; bootstrap only if unregistered:

```sh
/var/jb/bin/launchctl enable system/com.apple.managedconfiguration.profiled
/var/jb/bin/launchctl bootstrap system /System/Library/LaunchDaemons/com.apple.managedconfiguration.profiled.plist
```

Check command results and re-read the effective policy/row. Other management,
enrollment and passcode-prompt jobs are separate. Daemon Manager exposes this
service under Auto-Lock and configuration policy with manual Off, outside Critical.

Low Power Mode itself also disables the row through Display & Brightness’s UI
check. Duskie's optional normal-timeout feature handles that check and the
battery-saver client without changing other policy clients or range restrictions.
Read the actual installed feature state before altering system policy directly.

The native picker setter’s Never value is **-1**; the saved policy/idle monitor
can report **2147483647**. Do not pass a stored sentinel blindly to a UI setter.
Use the current specifier and checked setter ABI, then verify the saved setting,
enabled row and native idle descriptor. Do not leave `setIdleTimerDisabled:YES`
behind: it prevents normal timeout behavior and invalidates timeout tests.

## Verify media and restore temporary settings

```sh
ffprobe -v error -show_entries \
  format=duration,size:stream=index,codec_name,width,height,r_frame_rate \
  -of json recording.mp4
ffmpeg -v error -i recording.mp4 -f null -
```

Inspect the actual encoded frame rate, dimensions, orientation, audio and decode
result. A label or nominal frame rate does not prove continuous capture. For live
streaming, confirm receiver growth before stop, then inspect the finalized media.

Before a temporary preference edit, save the original plist bytes. Parse with
host plistlib; preserve its owner/mode on the phone. Prefer the owning controller’s
setter or CFPreferences API to direct replacement when caching matters. If direct
Duskie plist replacement is appropriate, post `com.dusklinux.edgedock/settings`,
then verify the live listener state. A successful Darwin post is not completion.
Restore the original bytes/state after the test unless the change is intended.

## Invoke a compiled helper or tweak action

Use DEVELOPMENT.md to build/sign/stage an owned helper. A reviewed dylib can be
loaded into its target app through Frida’s dlopen/dlsym on the main queue, with
its exact exported ABI. Complex blocks and struct arguments are often simpler in
compiled ObjC. Detaching Frida does not unload that dylib or undo its hooks.
Do not dlclose code with live callbacks; deliberately restart the target process
when unloading is required. Use a new filename for rebuilt diagnostic libraries
or a fresh process so dlopen does not silently reuse an older image.

For an internal tweak dispatcher, read its actual source signature and locate the
symbol in the **matching installed binary**. Recompute after every build; never
reuse an old offset. Synthetic handler invocation checks routing, not physical
HID input. Prefer an existing durable worker command for capture operations.

Phone tools differ from Linux: inspect command -v and installed --help. Use host
rg/plistlib to filter/parse downloaded output rather than assume rg/plutil/notifyutil
exists on the phone. Noninteractive PATH can differ from the interactive root
shell; use confirmed absolute paths. If notifyutil is absent, unlock.md shows a
phone Python ctypes call to libnotify without needing Frida.
