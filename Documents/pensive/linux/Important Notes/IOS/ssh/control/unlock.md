# Unlock without a device passcode

Use this procedure only for the owner’s documented **no-device-passcode** setup.
The SSH password is a separate credential. This does not unlock an unknown
passcode or change passcode/Touch ID enrollment.

## Connection

Use USB SSH as described in [BOOTSTRAP.md](BOOTSTRAP.md): mobile with interactive
sudo, or direct root only when authentication is verified. Keep the CLI target and master
socket matched to that account. The owner-supplied SSH/sudo password for this setup is
**2345**; enter it at the prompt, never in command arguments or source files.
Later live checks on 2026-10-21 verified mobile key SSH plus sudo; direct root
password authentication failed despite an earlier owner report that it worked.
Use the working route and recheck current facts. Do not change account
authentication to make this helper usable.

## Prefer the CLI when Frida is ready

```sh
./iphone-control --control-path /tmp/iphone-control-ssh unlock
./iphone-control --control-path /tmp/iphone-control-ssh status
```

The checked sequence runs inside SpringBoard on its main queue:

1. `SBBacklightController.sharedInstance` wakes with source 1.
2. Wait approximately one third of a second.
3. `SBLockScreenManager.sharedInstance` calls
   `_finishUIUnlockFromSource:withOptions:` with C int source 12 and nil options.
4. Confirm the current SpringBoard PID and `isUILocked == NO`.

| Selector | Expected encoding |
|---|---|
| turnOnScreenFullyWithBacklightSource: | `v24@0:8q16` |
| _finishUIUnlockFromSource:withOptions: | `B28@0:8i16@20` |
| isUILocked | `B16@0:8` |

Wake source is NSInteger; unlock source is C int. Recheck after OS changes.
A generic `unlockUIFromSource:` call is not a substitute for this wake/delayed
finish sequence. `_simulateHomeButtonPress` is absent on this profile; the Home
command uses `_simulateHomeButtonPressWithCompletion:` instead.

## Standalone helper when Frida is unavailable

Save the following as `ControlUnlock.m`, then build, sign and install it using
[DEVELOPMENT.md](DEVELOPMENT.md). This source includes the complete implementation;
no old project or diagnostic dylib is needed. Its notification/report names belong
to this helper. The wake/unlock mechanism uses the checked device ABI above;
verify this newly built helper’s fresh-PID report rather than assume it loaded.

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
        writeToFile:@"/var/mobile/Library/Caches/ControlUnlock-state.json" atomically:YES];
}
__attribute__((constructor)) static void initialize(void) {
    @autoreleasepool {
        int unlockToken, snapshotToken;
        notify_register_dispatch("com.dusklinux.control.unlock/unlock", &unlockToken,
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
        notify_register_dispatch("com.dusklinux.control.unlock/snapshot", &snapshotToken,
                                 dispatch_get_main_queue(), ^(__unused int token) { report(); });
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC),
                       dispatch_get_main_queue(), ^{ report(); });
    }
}
```

After respring, wait for
`/var/mobile/Library/Caches/ControlUnlock-state.json` to contain the current
SpringBoard PID. Its initial report is scheduled three seconds after loading.
Do not send an unlock request before initialization.

From a phone SSH shell, using its installed Python (no Frida/notifyutil required):

```sh
/var/jb/usr/bin/python3 -c 'import ctypes; ctypes.CDLL(None).notify_post(b"com.dusklinux.control.unlock/unlock")'
```

After the delayed unlock, request a fresh report and read it:

```sh
/var/jb/usr/bin/python3 -c 'import ctypes; ctypes.CDLL(None).notify_post(b"com.dusklinux.control.unlock/snapshot")'
cat /var/mobile/Library/Caches/ControlUnlock-state.json
```

Notification handling is asynchronous. Verify `pid`, a new `sampledAt`, and
`locked: false`; immediately reading can return the previous file. Posting
success alone is not proof of unlock. If it remains locked, inspect the ABI,
helper readiness and actual owner configuration before repeating.

When finished, remove the helper’s own two injection files and respring using
DEVELOPMENT.md, verify it is unloaded, then remove its owned staging/report files.
Do not remove the owner’s unrelated tweaks, settings or service overrides.
