# Unlock this iPhone over SSH

Verified on 2026-10-15: iPhone 8 Plus (`iPhone10,2`), iOS **16.7.16 / 20H392**, rootless jailbreak, ElleKit **1.2**. The owner confirmed that **there is no device passcode**. The SSH account password is not a device passcode.

## Connection

- Wi-Fi: `ssh root@192.168.29.111` (port 22). This address worked but subsequently timed out; prefer USB while connected.
- USB device discovery: `idevice_id -l`.
- Start the tunnel if port 2222 is not already listening: `iproxy -s 127.0.0.1 2222:22`. This is the syntax verified against the installed iproxy help; leave the process running.
- Connect: `ssh -p 2222 root@127.0.0.1`.
- Root and mobile SSH passwords supplied by the owner: **2345**. Enter at the SSH prompt; do not place passwords in command arguments.
- This session used multiplexed SSH: `ssh -p 2222 -o ControlMaster=auto -o ControlPath=/tmp/still-iphone-usb -o ControlPersist=600 root@127.0.0.1`. That socket is temporary and may not exist in a later session. A subsequent command can use `ssh -S /tmp/still-iphone-usb -p 2222 root@127.0.0.1 'COMMAND'` while the master remains alive.

## The method that actually unlocked the phone

Run these calls **inside SpringBoard, on its main queue**, through a temporary injected Objective-C helper. A separate SSH command-line process does not own SpringBoard's objects.

1. Get `+[SBBacklightController sharedInstance]` and call `-turnOnScreenFullyWithBacklightSource:` with source **1** to wake the display.
2. After approximately **0.33 seconds**, get `+[SBLockScreenManager sharedInstance]` and call `-_finishUIUnlockFromSource:withOptions:` with source **12**, options `nil`.
3. Verify `-[SBLockScreenManager isUILocked]` becomes `NO` in the **current SpringBoard PID**. A successful notification post only means the notification was sent, not that the phone unlocked.

The exact instance-method encodings observed on this phone were:

| Class / selector | Encoding |
| --- | --- |
| `SBBacklightController turnOnScreenFullyWithBacklightSource:` | `v24@0:8q16` |
| `SBLockScreenManager _finishUIUnlockFromSource:withOptions:` | `B28@0:8i16@20` |
| `SBLockScreenManager isUILocked` | `B16@0:8` |

The unlock source argument is **C `int`**, not `NSInteger`; the wake source is `NSInteger`. Recheck the methods and encodings after OS changes.

Actual working code from the helper (`shared()` sends `sharedInstance`; `matches()` checks the runtime encoding):

```objc
((void (*)(id, SEL, NSInteger))objc_msgSend)(
    shared("SBBacklightController"),
    sel_registerName("turnOnScreenFullyWithBacklightSource:"), 1);

dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC / 3),
               dispatch_get_main_queue(), ^{
    id lock = shared("SBLockScreenManager");
    if (matches(lock, "_finishUIUnlockFromSource:withOptions:",
                "B28@0:8i16@20")) {
        ((BOOL (*)(id, SEL, int, id))objc_msgSend)(
            lock, sel_registerName("_finishUIUnlockFromSource:withOptions:"),
            12, nil);
    }
    report(); // Writes current PID, locked state, and UI diagnostics.
});
```

This procedure was tested for this owner's **no-passcode** configuration. It is not a verified passcode-unlock procedure.

## Helper source and exact commands used in this session

The complete helper is `/mnt/zram1/repo/still/tools/Probe.m`, with the build script `/mnt/zram1/repo/still/tools/build.sh`. It also contains temporary animation-test diagnostics; it is **not** part of the release tweak. The local source is kept for reference. `/mnt/zram1` is temporary storage, so recover the saved project elsewhere if that mount has been recreated.

Build on the Linux host:

```sh
bash /mnt/zram1/repo/still/tools/build.sh Probe
```

Build requirements actually used: Clang/LLD **23.1.1**, Bash **5.3.20**, jq **1.8.2**, and the Theos SDK at `/mnt/zram1/repo/toolchains/theos-sdks/iPhoneOS16.5.sdk`. Its `SDKSettings.json` reports **16.4**, which the linker uses. The SDK source is `https://github.com/theos/sdks`, revision `0222fd5413cf4b9af096f37b4621afa2688572f7`. `IOS_SDK` can override the SDK path. This helper does not require Theos Logos or the separate Theos headers.

If the helper is absent on the phone, install it temporarily, using a plain SSH connection or the existing multiplexed master:

```sh
scp -P 2222 /mnt/zram1/repo/still/build/Probe.dylib root@127.0.0.1:/var/tmp/Probe.dylib
ssh -p 2222 root@127.0.0.1
```

Then on the phone:

```sh
ldid -S /var/tmp/Probe.dylib
cp /var/tmp/Probe.dylib /var/jb/Library/MobileSubstrate/DynamicLibraries/StillProbe.dylib.new
mv /var/jb/Library/MobileSubstrate/DynamicLibraries/StillProbe.dylib.new /var/jb/Library/MobileSubstrate/DynamicLibraries/StillProbe.dylib
printf '{ Filter = { Bundles = ( "com.apple.springboard" ); }; }\n' > /var/jb/Library/MobileSubstrate/DynamicLibraries/StillProbe.plist
sbreload
```

Allow SpringBoard to finish restarting and the helper to load. Its initial method report is written **three seconds after its constructor runs**, to `/var/mobile/Library/Caches/Still-probe.json`. Compare its `pid` with `ps -A -o pid,comm` or `launchctl print user/501/com.apple.SpringBoard`; do not act on an old report. Sending the unlock notification before the new helper loads does nothing. The normal mobile UID on this phone is 501; discover it on another device.

Request unlock from SSH, once the helper is loaded:

```sh
python3 -c 'import ctypes; ctypes.CDLL(None).notify_post(b"com.dusklinux.still.probe/unlock")'
```

After the delayed unlock call has run, request fresh state:

```sh
python3 -c 'import ctypes; ctypes.CDLL(None).notify_post(b"com.dusklinux.still.probe/snapshot")'
cat /var/mobile/Library/Caches/Still-state.json
```

The report must have the current SpringBoard `pid` and `"locked": false`. Notification processing is asynchronous; reading immediately can return the previous file. Once unlocked, `uiopen --bundleid com.apple.Preferences` can launch Settings. `uiopen --help` lists the installed command's syntax.

## Findings that prevent repeating failed attempts

- Calling `unlockUIFromSource:withOptions:` alone, with source 0 or 12, did **not** unlock the post-respring screen in these tests. Waking first and calling `_finishUIUnlockFromSource:withOptions:` did.
- `-[SpringBoard _simulateHomeButtonPress]` is absent on this iOS build. An unguarded call in an earlier test probe restarted SpringBoard. Do not use it. The runtime exposes `_simulateHomeButtonPressWithCompletion:` with encoding `v24@0:8@?16`; the corrected helper checks that signature before calling it.
- This phone also exposes `_turnScreenOnAndUnlockWithCompletion:`, but that alternative was only discovered, **not exercised**. Prefer the tested two-call procedure above.
- `idevicescreenshot` failed because the screenshotr service/developer disk image was unavailable. The helper's optional `capture` notification writes a UIKit window capture to `/var/mobile/Library/Caches/Still-screen.png`; it is a test aid, not a requirement for unlocking.
- Use `sbreload` for these UI restarts. Do not perform a normal kernel reboot; the owner's existing setup specifically uses respring or userspace restart.

When finished, remove only the temporary helper's own two injection files, then `sbreload` to unload it:

```sh
rm /var/jb/Library/MobileSubstrate/DynamicLibraries/StillProbe.plist /var/jb/Library/MobileSubstrate/DynamicLibraries/StillProbe.dylib
sbreload
```

The notification listener is gone after that restart. The standalone **Still** animation tweak does not provide an unlock listener. Duskie, Daemon Manager, their preferences, and their service overrides do not need changes for this procedure.

## Self-contained recovery if the temporary project is gone

Save this minimal helper as `UnlockProbe.m`. It uses the same verified wake/unlock sequence and notification names, so the install, request, verification and cleanup commands above still apply. Build its output as `Probe.dylib`; no animation tweak is needed. The full helper and this reduced version were both built and tested on the phone. The reduced version independently unlocked the phone after a respring (verified current SpringBoard PID 37650, `locked: false`) with Still 0.1.2 installed. It makes these notes independent of zram storage.

```objc
#import <UIKit/UIKit.h>
#import <objc/runtime.h>
#import <objc/message.h>
#include <notify.h>
#include <string.h>
#include <unistd.h>

static id shared(const char *name) {
    Class cls = objc_getClass(name);
    SEL sel = sel_registerName("sharedInstance");
    if (![(id)cls respondsToSelector:sel]) return nil;
    return ((id (*)(id, SEL))objc_msgSend)((id)cls, sel);
}
static BOOL matches(id obj, const char *name, const char *encoding) {
    Method m = class_getInstanceMethod(object_getClass(obj), sel_registerName(name));
    return m && !strcmp(method_getTypeEncoding(m), encoding);
}
static void report(void) {
    id lock = shared("SBLockScreenManager");
    if (!matches(lock, "isUILocked", "B16@0:8")) return;
    BOOL locked = ((BOOL (*)(id, SEL))objc_msgSend)(lock, sel_registerName("isUILocked"));
    NSDictionary *state = @{@"pid": @(getpid()), @"locked": @(locked),
        @"sampledAt": @(NSDate.date.timeIntervalSince1970)};
    [[NSJSONSerialization dataWithJSONObject:state options:0 error:NULL]
        writeToFile:@"/var/mobile/Library/Caches/Still-state.json" atomically:YES];
}
__attribute__((constructor)) static void initialize(void) {
    @autoreleasepool {
        int unlockToken, snapshotToken;
        notify_register_dispatch("com.dusklinux.still.probe/unlock", &unlockToken,
                                 dispatch_get_main_queue(), ^(__unused int token) {
            id backlight = shared("SBBacklightController");
            id lock = shared("SBLockScreenManager");
            if (!matches(backlight, "turnOnScreenFullyWithBacklightSource:", "v24@0:8q16") ||
                !matches(lock, "_finishUIUnlockFromSource:withOptions:", "B28@0:8i16@20")) return;
            ((void (*)(id, SEL, NSInteger))objc_msgSend)(backlight,
                sel_registerName("turnOnScreenFullyWithBacklightSource:"), 1);
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC / 3),
                           dispatch_get_main_queue(), ^{
                ((BOOL (*)(id, SEL, int, id))objc_msgSend)(lock,
                    sel_registerName("_finishUIUnlockFromSource:withOptions:"), 12, nil);
                report();
            });
        });
        notify_register_dispatch("com.dusklinux.still.probe/snapshot", &snapshotToken,
                                 dispatch_get_main_queue(), ^(__unused int token) { report(); });
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC),
                       dispatch_get_main_queue(), ^{ report(); });
    }
}
```

On the Linux build host, set `unlock_sdk` to the available SDK directory, then:

```sh
unlock_sdk=/mnt/zram1/repo/toolchains/theos-sdks/iPhoneOS16.5.sdk
unlock_sdk_version=$(jq -er .Version "$unlock_sdk/SDKSettings.json")
clang --target=arm64-apple-ios16.0 -isysroot "$unlock_sdk" -fobjc-arc -fblocks \
  -Oz -Wall -Wextra -Werror -c UnlockProbe.m -o UnlockProbe.o
ld64.lld -arch arm64 -platform_version ios 16.0 "$unlock_sdk_version" \
  -syslibroot "$unlock_sdk" -dylib \
  -install_name /var/jb/Library/MobileSubstrate/DynamicLibraries/StillProbe.dylib \
  -framework Foundation -framework UIKit -lobjc -lSystem UnlockProbe.o -o Probe.dylib
```

Transfer this `Probe.dylib` instead of the project build output. The reduced helper writes `Still-state.json` after loading, rather than a separate `Still-probe.json` method inventory. Verify the PID in `Still-state.json` to establish readiness. It has only unlock and snapshot listeners, with no Home, Control Center, screenshot, or animation-test functions.
