# Unlock without a device passcode

Use this procedure only for the owner’s documented **no-device-passcode** setup.
The SSH password is a separate credential. This does not unlock an unknown
passcode or change passcode/Touch ID enrollment.

## Connection

Use USB SSH as described in [BOOTSTRAP.md](BOOTSTRAP.md): mobile with interactive
sudo, or direct root only when authentication is verified. Keep the CLI target and master
socket matched to that account. The owner-supplied SSH/sudo password for this setup is
**2345**; enter it at the prompt, never in command arguments or source files.
On 2026-10-25, both mobile and root key SSH worked; password login was not
retested. Use the working route and verify `id`. Do not change authentication to
make this helper usable.

## Prefer the CLI when Frida is ready

```sh
./iphone-control --control-path /tmp/iphone-control-ssh unlock
./iphone-control --control-path /tmp/iphone-control-ssh status
```

The current checked sequence runs inside SpringBoard on its main queue:

1. `SBBacklightController.sharedInstance` wakes with source 1.
2. Wait approximately one third of a second.
3. If already unlocked, return unchanged. If `DKLockController` exists and its
   `enabled` getter is true, call `authenticateInternally`; Duskie then completes
   its simulated passcode authentication through its own hook.
4. Only without an enabled Duskie lock, use the historical native no-passcode
   `_finishUIUnlockFromSource:withOptions:` with C int source 12 and nil options.
5. Read fresh `status`: display on, current PID unchanged, `uiLocked == false`.
   An immediate result is a request, not a settled-state guarantee.

**Do not call the native finish method directly while Duskie's simulated lock is
active.** That aborts SpringBoard on this configuration even with the correct
encoding. Waking and calling Duskie's own authentication path was verified on the
live device; the native-only branch is historical and was not retested by disabling
the owner's lock. `duskie-unlock` explicitly calls the enabled Duskie controller;
it does not wake the display itself. Do not feed the SSH password to either route.

| Selector | Expected encoding |
|---|---|
| sharedInstance | `@16@0:8` |
| turnOnScreenFullyWithBacklightSource: | `v24@0:8q16` |
| DKLockController / enabled | `B16@0:8` |
| DKLockController / authenticateInternally | `v16@0:8` |
| _finishUIUnlockFromSource:withOptions: | `B28@0:8i16@20` |
| isUILocked | `B16@0:8` |

Wake source is NSInteger; native unlock source is C int. A matching ABI does not
establish that a call is semantically valid in the current hook/authentication
state. `_simulateHomeButtonPress` is absent; use the checked Home completion
selector in the CLI. Neither this guide nor the helper handles an actual device
passcode, Touch ID enrollment, or another tweak's unknown authentication state.

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

static BOOL matches(id obj, const char *name, const char *type) {
    if (!obj) return NO;
    Method method = class_getInstanceMethod(object_getClass(obj), sel_registerName(name));
    return method && !strcmp(method_getTypeEncoding(method), type);
}
static id shared(const char *name) {
    Class cls = objc_getClass(name);
    if (!matches((id)cls, "sharedInstance", "@16@0:8")) return nil;
    return ((id (*)(id, SEL))objc_msgSend)((id)cls, sel_registerName("sharedInstance"));
}
static void report(NSString *phase, NSString *error) {
    id lock = shared("SBLockScreenManager");
    BOOL ready = matches(lock, "isUILocked", "B16@0:8");
    NSMutableDictionary *state = [@{@"pid": @(getpid()), @"ready": @(ready),
        @"phase": phase, @"sampledAt": @(NSDate.date.timeIntervalSince1970)} mutableCopy];
    if (ready) state[@"locked"] = @(((BOOL (*)(id, SEL))objc_msgSend)(lock, sel_registerName("isUILocked")));
    if (error) state[@"error"] = error;
    [[NSJSONSerialization dataWithJSONObject:state options:0 error:NULL]
        writeToFile:@"/var/mobile/Library/Caches/ControlUnlock-state.json" atomically:YES];
}
static void requestUnlock(void) {
    id backlight = shared("SBBacklightController");
    if (!matches(backlight, "turnOnScreenFullyWithBacklightSource:", "v24@0:8q16")) {
        report(@"failed", @"Backlight ABI unavailable"); return;
    }
    ((void (*)(id, SEL, NSInteger))objc_msgSend)(backlight,
        sel_registerName("turnOnScreenFullyWithBacklightSource:"), 1);
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC / 3), dispatch_get_main_queue(), ^{
        id lock = shared("SBLockScreenManager");
        if (!matches(lock, "isUILocked", "B16@0:8")) {
            report(@"failed", @"Lock-state ABI unavailable"); return;
        }
        if (!((BOOL (*)(id, SEL))objc_msgSend)(lock, sel_registerName("isUILocked"))) {
            report(@"already-unlocked", nil); return;
        }
        if (objc_getClass("DKLockController")) {
            id controller = shared("DKLockController");
            if (!matches(controller, "enabled", "B16@0:8")) {
                report(@"failed", @"Duskie state ABI unavailable"); return;
            }
            if (((BOOL (*)(id, SEL))objc_msgSend)(controller, sel_registerName("enabled"))) {
                if (!matches(controller, "authenticateInternally", "v16@0:8")) {
                    report(@"failed", @"Duskie authentication ABI unavailable"); return;
                }
                ((void (*)(id, SEL))objc_msgSend)(controller, sel_registerName("authenticateInternally"));
                report(@"requested-duskie", nil); return;
            }
        }
        if (!matches(lock, "_finishUIUnlockFromSource:withOptions:", "B28@0:8i16@20")) {
            report(@"failed", @"Native no-passcode ABI unavailable"); return;
        }
        ((BOOL (*)(id, SEL, int, id))objc_msgSend)(lock,
            sel_registerName("_finishUIUnlockFromSource:withOptions:"), 12, nil);
        report(@"requested-native", nil);
    });
}
__attribute__((constructor)) static void installControlUnlock(void) {
    @autoreleasepool {
        static int unlockToken, snapshotToken;
        notify_register_dispatch("com.dusklinux.control.unlock/unlock", &unlockToken,
            dispatch_get_main_queue(), ^(__unused int token) { requestUnlock(); });
        notify_register_dispatch("com.dusklinux.control.unlock/snapshot", &snapshotToken,
            dispatch_get_main_queue(), ^(__unused int token) { report(@"snapshot", nil); });
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC),
            dispatch_get_main_queue(), ^{ report(@"ready", nil); });
    }
}
```

After respring, wait for
`/var/mobile/Library/Caches/ControlUnlock-state.json` to contain the current
SpringBoard PID and `ready: true`. Its initial report is scheduled three seconds
after loading.
Do not send an unlock request before initialization.

From a phone SSH shell, using its installed Python (no Frida/notifyutil required):

```sh
/var/jb/usr/bin/python3 -c 'import ctypes; ctypes.CDLL("/usr/lib/libSystem.B.dylib").notify_post(b"com.dusklinux.control.unlock/unlock")'
```

After the delayed unlock, request a fresh report and read it:

```sh
/var/jb/usr/bin/python3 -c 'import ctypes; ctypes.CDLL("/usr/lib/libSystem.B.dylib").notify_post(b"com.dusklinux.control.unlock/snapshot")'
cat /var/mobile/Library/Caches/ControlUnlock-state.json
```

Notification handling is asynchronous. Verify `pid`, a new `sampledAt`, and
`ready: true`, no `error`, and `locked: false`; immediately reading can return
the previous file. Posting
success alone is not proof of unlock. If it remains locked, inspect the ABI,
helper readiness and actual owner configuration before repeating.

When finished, remove the helper’s own two injection files and respring using
DEVELOPMENT.md, verify it is unloaded, then remove its owned staging/report files.
Do not remove the owner’s unrelated tweaks, settings or service overrides.
