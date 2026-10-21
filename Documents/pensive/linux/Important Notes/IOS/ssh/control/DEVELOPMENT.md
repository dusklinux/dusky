# Build and deploy an iOS control helper or tweak

Use [EXTENDING.md](EXTENDING.md) to discover the receiver and verify its ABI.
Use a compiled ObjC helper when blocks, structs or persistent hooks make a raw
Frida script awkward. A standalone SSH process does not own SpringBoard’s UI;
UI code must execute inside the target process on its main queue.

## 1. Source and toolchain

For a complete minimal SpringBoard helper, copy the source in [unlock.md](unlock.md).
It is self-contained and does not need an old temporary repository. For another
operation, adapt a separate source file and output name; do not overwrite an
installed unrelated tweak. Private classes can be looked up with objc_getClass,
with exact method/type checks before objc_msgSend or hooking.

Build on Linux with Clang/ld64.lld and an available iPhoneOS SDK. The control
directory does not bundle a compiler, SDK or private headers. Set IOS_SDK to the
actual directory; its SDKSettings version, not its directory name, determines
the linker SDK version. The documented profile targets arm64 iOS 16.0+.

```sh
# Replace this example with the actual installed SDK directory:
export IOS_SDK=/absolute/path/to/iPhoneOS16.5.sdk
control_sdk_version=$(python3 - "$IOS_SDK/SDKSettings.plist" <<'PYSDK'
import plistlib, sys
from pathlib import Path
print(plistlib.loads(Path(sys.argv[1]).read_bytes())['Version'])
PYSDK
)
clang --target=arm64-apple-ios16.0 -isysroot "$IOS_SDK" -fobjc-arc -fblocks \
  -Oz -Wall -Wextra -Werror -c ControlUnlock.m -o ControlUnlock.o
ld64.lld -arch arm64 -platform_version ios 16.0 "$control_sdk_version" \
  -syslibroot "$IOS_SDK" -dylib \
  -install_name /var/jb/Library/MobileSubstrate/DynamicLibraries/ControlUnlock.dylib \
  -framework Foundation -framework UIKit -lobjc -lSystem \
  ControlUnlock.o -o ControlUnlock.dylib
```

For another source, change source/object/output/install-name together and add
only its required frameworks. A project needing private Preferences headers or
other dependencies must supply them; this minimal helper uses SDK headers only.

## 2. Sign and stage

Transfer the dylib to a directory owned by this operation, then sign on the phone
with its installed ldid. Use mobile SSH/scp and the interactive sudo terminal:

```sh
ssh -S /tmp/iphone-control-ssh -p 2222 mobile@127.0.0.1 \
  'mkdir -p /var/mobile/Library/ControlTools'
scp -o ControlPath=/tmp/iphone-control-ssh -P 2222 ControlUnlock.dylib \
  mobile@127.0.0.1:/var/mobile/Library/ControlTools/ControlUnlock.dylib
```

Phone root shell:

```sh
/var/jb/usr/bin/ldid -S /var/mobile/Library/ControlTools/ControlUnlock.dylib
```

Ordinary injected UI dylibs use this signing route. Privileged standalone CLI
executables can need their own platform/no-container/no-sandbox or feature-specific
entitlements. Use the target project’s reviewed entitlement plist and sign with
`ldid -S/path/to/entitlements.plist EXECUTABLE`; do not borrow the Frida server’s
entitlements. A process killed at startup is not proof its ObjC logic is wrong.
The observed standalone fixture entitlement set is shown below; it is not a
universal entitlement set for every private API:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>com.apple.private.security.no-container</key><true/>
  <key>com.apple.private.security.no-sandbox</key><true/>
  <key>platform-application</key><true/>
</dict></plist>
```

## 3. Load into the correct process

For a persistent injected helper, install both the signed dylib and its filter.
Check whether these paths already exist first; retain the original files if
updating an owned helper. In the root shell:

```sh
cp /var/mobile/Library/ControlTools/ControlUnlock.dylib /var/jb/Library/MobileSubstrate/DynamicLibraries/ControlUnlock.dylib.new
mv /var/jb/Library/MobileSubstrate/DynamicLibraries/ControlUnlock.dylib.new /var/jb/Library/MobileSubstrate/DynamicLibraries/ControlUnlock.dylib
printf '{ Filter = { Bundles = ( "com.apple.springboard" ); }; }\n' > /var/jb/Library/MobileSubstrate/DynamicLibraries/ControlUnlock.plist
chmod 755 /var/jb/Library/MobileSubstrate/DynamicLibraries/ControlUnlock.dylib
chmod 644 /var/jb/Library/MobileSubstrate/DynamicLibraries/ControlUnlock.plist
/var/jb/usr/bin/sbreload
```

The filter above is **SpringBoard only**. For an app helper use that app’s actual
bundle identifier, not its executable/display name. Wait for fresh-PID readiness
before issuing notifications or attaching Frida. For ControlUnlock, use its own
report described in unlock.md. A userspace restart is required when all existing
app processes must load a changed broad injection; respring alone is narrower.
Never perform a normal kernel reboot on this setup.

A temporary reviewed dylib may instead be dlopened through a Frida script in the
target process. Call dlopen/dlsym with C strings, check both results, and invoke
only its declared exported ABI. Retain callbacks/objects correctly. A second
dlopen of the same path can reuse the loaded image rather than a rebuilt file.

## 4. Add hooks deliberately

ElleKit’s Substrate-compatible interface is available through
`/var/jb/usr/lib/libsubstrate.dylib` on this profile. Inspect availability before
using it. The native hook function signature is:

```c
void MSHookMessageEx(Class cls, SEL selector, IMP replacement, IMP *original);
```

Verify the original method’s encoding before installing a replacement, implement
the replacement with the exact C ABI, preserve its original IMP, and forward
unchanged behavior when the feature is disabled. UIKit hooks remain on the owning
UI process/main queue. Avoid broad filters and periodic work when an existing
notification or method hook supplies the event. See EXTENDING.md for object,
block, scalar and lifetime rules.

An NSNotificationCenter block observer registered on NSOperationQueue.mainQueue
can make a background poster wait for main. For bundle-load notifications this
can deadlock when main is waiting for the rendering/loader queue that posted it.
Receive that notification with `queue:nil`, then dispatch UI work asynchronously
to main; keep coalescing state on main too. Do not replace the wait with
dispatch_sync to main. A bounded fixture should reproduce the actual callback
while main waits for its worker. See [DIAGNOSTICS.md](DIAGNOSTICS.md) for capturing
and symbolizing a live wait before changing a hook.

## 5. Verify and remove

Check installed file hashes and the **fresh target process’s** loaded module,
readiness report and intended behavior. Saved preferences or a successful copy
alone do not prove the new code is loaded. Test relevant failure/disabled paths;
restore temporary settings and remove diagnostic flags.

To remove this owned helper in the root shell:

```sh
rm /var/jb/Library/MobileSubstrate/DynamicLibraries/ControlUnlock.plist /var/jb/Library/MobileSubstrate/DynamicLibraries/ControlUnlock.dylib
/var/jb/usr/bin/sbreload
```

Verify a fresh process without the helper, then remove its owned staging/report
files. Detaching Frida or deleting a file does not unload live callbacks. Use
README.md’s diagnosed recovery procedure if a hook causes a UI failure. For a
release tweak use the project’s package/build/install workflow; this procedure
is a minimal helper workflow, not a Debian package manager replacement.

## 6. Validate UI delivery and perform a targeted rollback

For keyboard-hosted controls, inspect the actual app/keyboard windows, scenes and
text responder before choosing a presenter. Verify gesture delivery, presentation
and text focus in the running app. Calling a handler in an isolated fixture tests
that handler's logic; it does not establish physical gestures or a usable editor.
See [VALIDATION.md](VALIDATION.md) for completion evidence.

For a requested rollback, identify the commit before the unwanted feature from
its file history; documentation-only commits can make “two commits ago” ambiguous.
Record current diffs, preserve unrelated changes and back up affected preferences,
user data and daemon state. Revert the feature's code, tests and packaging together;
for an exact baseline request, compare the resulting source tree with that baseline.
Keep ignored signed libraries/object caches consistent if they may be reused.

Use the project's installer with the original verified signed package when
available. Rebuild/sign only when necessary. Reload affected processes, using a
userspace restart when existing app/keyboard clients all need replacement. Verify
installed version/payload hashes and preserved user data/settings after reconnecting.
Do not reset daemon overrides or apply preset defaults as part of a tweak rollback.
Record project versions/results in their separate notes, not in this manual.
