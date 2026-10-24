# App containers, preferences and restore operations

Use this for application data work on the documented iOS 16.7.16 rootless phone.
Use [BOOTSTRAP.md](BOOTSTRAP.md) for account/command setup and
[DEVELOPMENT.md](DEVELOPMENT.md) for native fixtures. This is a reusable procedure;
a backup/reset method is not permission to erase an unrelated application's data.

## Find the actual app and storage

Read-only discovery without Frida, from the host with a working account:

```sh
./iphone-control --target root@127.0.0.1 ssh -- /var/jb/usr/bin/uicache -l
./iphone-control --target root@127.0.0.1 ssh -- /var/jb/usr/bin/uicache -i com.example.app
```

Replace `com.example.app` with the intended installed identifier. The information
includes executable name, bundle path, main container, App Group mappings and
extension containers. Output can include private paths; retain only what is needed.
`uicache -i` is inspection. `-p PATH` registers/refreshes an app, `-u PATH`
unregisters it, and `-r` restarts UI processes: those are different, mutating actions.

| Storage | Typical location / meaning |
|---|---|
| Installed bundle | Query its actual `bundleURL` or uicache Path; never write backups into it |
| Main data | `/var/mobile/Containers/Data/Application/<UUID>/` |
| Shared group | `/var/mobile/Containers/Shared/AppGroup/<UUID>/` |
| Extension data | `/var/mobile/Containers/Data/PluginKitPlugin/<UUID>/` |
| Local Files | App Group identified by `group.com.apple.FileProvider.LocalStorage`, then `File Provider Storage/` |
| Remote FTP configuration | Group `group.com.dusklinux.duskie.remoteftp`, then `connection.plist` (contains credentials) |

Container UUIDs change with reinstall. `/private/var/...` and `/var/...` may refer
to the same directory; compare resolved paths/file identity before claiming two
containers. Rootless `/var/jb/var/mobile` is not a substitute for native
`/var/mobile`. Do not fabricate an AppGroup directory or copy its identity metadata
from another installation.

In the owning process or SpringBoard, the observed LaunchServices APIs are:

- `+[LSApplicationProxy applicationProxyForIdentifier:]`: `@24@0:8@16`.
- `dataContainerURL`, `bundleURL`, `groupContainerURLs`: object getters
  `@16@0:8`; groups map identifiers to URLs.
- Check nil and actual classes before path/string/dictionary operations. A System
  app can have a data container (the installed Duskie app does); System/User type
  alone does not prove isolation or justify reset. SpringBoard and other shared
  system locations must not be treated as an ordinary app container.
- `Info.plist` values are not uniformly strings. `MinimumOSVersion` can be an
  NSNumber. Use a type check or `[value description]` when formatting; never send
  `isEqualToString:` blindly to an untyped plist value.

For Files storage without Frida, run this read-only script in a phone Python
shell (or pass it to `ssh ... 'python3 -'` via a quoted heredoc):

```python
import os, plistlib
base = '/var/mobile/Containers/Shared/AppGroup'
wanted = {'group.com.apple.FileProvider.LocalStorage',
          'group.com.dusklinux.duskie.remoteftp'}
for name in os.listdir(base):
    root = os.path.join(base, name)
    try:
        with open(os.path.join(root, '.com.apple.mobile_container_manager.metadata.plist'), 'rb') as f:
            metadata = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException):
        continue
    identifier = metadata.get('MCMMetadataIdentifier')
    if identifier in wanted:
        print(identifier, root)
```

Check unique matches before choosing one. Open Files → On My iPhone once if its
local storage tree has not been created. Duskie's local backups are under
`File Provider Storage/duskie_ftp/Backups/<bundleID>/`; fallback is
`/var/mobile/Library/Duskie/Backups/<bundleID>/` when local Files storage cannot be
resolved. Remote FTP Files is a File Provider cache, not a kernel-mounted FTP
filesystem. A remote URL shown in Files does not prove data exists on the server.

## Quiesce the app before backup, restore or reset

For consistency, stop the target before copying as well as before replacing data.
Visible apps remained running after a cache-cleaning termination assertion was
created on this phone. **An assertion is not proof of process exit.**

The following symbols were resolved from the **SpringBoard framework inside
SpringBoard**, not the similarly named SpringBoardServices framework:

```c
void *SBSApplicationTerminationAssertionCreateWithError(
    void *allocator, NSString *bundleID, int reason, int *error);
void SBSApplicationTerminationAssertionInvalidate(void *assertion);
```

Load `/System/Library/PrivateFrameworks/SpringBoard.framework/SpringBoard` with
dlopen in that process; check both dlsym results. Create with NULL allocator,
reason 1, and an error pointer on main. Keep the assertion until the operation
finishes; invalidate it and `CFRelease` its retained reference, then dlclose your
framework handle. Failure to create must stop a destructive operation.

Resolve the actual current process on main with these checked encodings:

| Receiver / selector | Encoding |
|---|---|
| SBApplicationController class / sharedInstance | `@16@0:8` |
| controller / applicationWithBundleIdentifier: | `@24@0:8@16` |
| SBApplication / processState | `@16@0:8` |
| SBApplicationProcessState / isRunning | `B16@0:8` |
| SBApplicationProcessState / pid | `i16@0:8` |

If running, stop only that resolved target (the verified implementation uses
SIGKILL), reject PID <= 1 or the control process's own PID, and wait boundedly
on a worker queue for `kill(pid, 0)` to fail with ESRCH. EPERM does not mean exit.
Do not perform a multi-second polling wait on SpringBoard's main queue. Retain
the assertion throughout file replacement, but do not treat it as observed proof
of exit or as a filesystem transaction. Fail if stop cannot be verified. A plain SSH utility does not acquire SpringBoard's context merely
because it runs as root. Standalone archive fixtures require their caller to keep
the source stopped. Other apps/extensions sharing a group can still write it;
one target-app assertion does not make all group writers quiescent.

## Preference files and the live cfprefsd cache are separate

A real stopped app relaunched with old NSUserDefaults values after its plist was
replaced; reset could similarly remove the file while leaving cached keys.
A Darwin notification alone does not fix an arbitrary application's defaults.
Avoid killing the global preference daemon to refresh one app.

The following CoreFoundation exports were resolved and tested for this device:

```c
CFDictionaryRef _CFPreferencesCopyMultipleWithContainer(
    CFArrayRef keys, CFStringRef applicationID, CFStringRef user,
    CFStringRef host, CFStringRef containerPath);
void _CFPreferencesSetValueWithContainer(
    CFStringRef key, CFPropertyListRef value, CFStringRef applicationID,
    CFStringRef user, CFStringRef host, CFStringRef containerPath);
Boolean _CFPreferencesSynchronizeWithContainer(
    CFStringRef applicationID, CFStringRef user,
    CFStringRef host, CFStringRef containerPath);
```

Use dlsym and fail clearly if unavailable. These are private APIs for the checked
profile. Run as the owning **mobile user / SpringBoard**, using
`kCFPreferencesCurrentUser`, `kCFPreferencesAnyHost`, and the explicit main/group
container path. A root CLI's current user is root, not mobile.

The tested sequence is:

1. Stop the app. Discover dictionary preference domains in `Library/Preferences`,
   including its bundle ID or group ID even when the file is absent.
2. Synchronize each domain and capture its current dictionary before mutation;
   flush before copying a backup. Treat a malformed managed domain as an error.
3. After installing restored files, read desired dictionaries from those files.
   Remove cached keys absent from the desired dictionary, set desired values,
   then synchronize and check the Boolean result. New desired domains also count.
   Clear Data uses empty dictionaries for removed domains.
4. On failure, roll back files and captured dictionaries. Preserve recovery
   data and report its location if either rollback fails.
5. Relaunch the actual sandboxed app and read its own defaults to verify cache
   propagation. A file read or another unsandboxed program alone is insufficient.

Keep an NSString/NSURL strongly owned while using its bridged CFStringRef. In
particular, `CFStringRef path = (__bridge CFStringRef)url.path;` followed by later
C calls can leave an unowned temporary NSString dangling under ARC. Retain/copy
`url.path` into an ivar owned by the live state object, or use a local with an
explicit lifetime covering the calls:

```objc
__attribute__((objc_precise_lifetime)) NSString *ownedPath = [url.path copy];
CFStringRef path = (__bridge CFStringRef)ownedPath;
// Use path only within this scope; __bridge does not transfer ownership.
```

A plain strong local can be released before lexical scope ends after its last
ownership-relevant use; see [Clang's precise lifetime semantics](https://clang.llvm.org/docs/AutomaticReferenceCounting.html#precise-lifetime-semantics).
The implemented state-owned ivar fix passed a standalone reproducer and real-app
main/group tests.

Custom non-dictionary plists are plain files, not CFPreferences dictionaries.
NSUserDefaults suites, app groups and the main domain are distinct. Do not assume
that the bundle ID is the only preference domain an app uses.

## Cache cleanup, Clear Data and restore have different scope

Cache cleanup removes the contents of `Library/Caches`, `tmp`, and
`Library/Application Support/Caches`; preserve Documents and preferences. WebKit
contains cookies, databases and website state and is **persistent backup data**,
not a directory to discard wholesale as cache.

The current Clear Data operation clears Documents, Library and tmp in the main
container and recreates `Library/Preferences`, then refreshes managed preferences.
It is not uninstall/reinstall: custom main-container root files, shared groups,
extension containers, Keychain records and cloud/server state can remain.
Shared groups may belong to other apps; they are backed up/restored explicitly,
not implicitly erased by main-container reset.

An intentional main-app privacy reset can call `TCCAccessResetForBundle` from
`/System/Library/PrivateFrameworks/TCC.framework/TCC` with the observed declaration
`void TCCAccessResetForBundle(CFStringRef service, CFStringRef bundleID)`, service
`CFSTR("kTCCServiceAll")` and the target bundle ID. Verify that symbol and its
matching call site before applying the profile on another build. This is separate from data restoration.
Restoring an archive does not itself require resetting existing permission grants.
Permission-prompt behavior must be checked separately; the file/cache restore
fixtures did not test every individual TCC permission.

## Archive creation and restore

Use installed `/var/jb/usr/bin/tar` and `/var/jb/usr/bin/gzip` (observed GNU tar
1.35 / gzip 1.13); ZIP extraction uses `/var/jb/usr/bin/unzip`. An explicit PATH
is useful but **did not repair tar spawning its own gzip child in the tested
SpringBoard/helper context on this bootstrap**. An interactive SSH shell has a
different execution context; its result does not establish helper behavior.
Spawn both ends of the pipe yourself or use a checked shell pipeline:

```sh
# Phone /var/jb/bin/bash. Supply actual owned paths before executing.
set -euo pipefail
/var/jb/usr/bin/tar -cf - -C "$staging" . | /var/jb/usr/bin/gzip -c > "$archive_partial"
# After both children succeed, rename the unique .partial to its final .tar.gz.
# Extraction into a fresh owned staging directory:
/var/jb/usr/bin/gzip -dc "$archive" | /var/jb/usr/bin/tar -xf - -C "$extract_staging"
```

Without pipefail a corrupt gzip producer can fail while tar exits successfully.
With posix_spawn, pass argv directly, explicitly connect/close pipe descriptors,
set close-on-exec on unrelated descriptors and wait for **both** children. Check
normal exit and exit code for each, not merely posix_spawn's return value. Use
`PATH=/var/jb/usr/bin:/var/jb/bin:/usr/bin:/bin` for deliberate child lookups.
Do not interpolate imported filenames into shell source. `tar -czf`/`-xzf` is not
the verified route here.

Duskie native layout:

```text
manifest.plist
Container/                  # persistent main-container state, including root files/WebKit
Groups/<groupID>/           # persistent state for each mapped group
```

The manifest is a binary plist with bundleIdentifier, name, version, createdAt,
format `duskie-backup-1.0`, and a groups array. It is **not a cryptographically
signed manifest**. Exclude cache/tmp trees while staging instead of first copying
them in full and deleting them. Preserve links/metadata and fail on copy errors.
Publish only a completed unique archive, never a partial file. Local backups in
Files consume phone storage; the laptop's `/mnt/zram1`, when used as a receiver
parent, is volatile RAM storage. Choose a persistent parent for durable backups.

For restore:

1. Extract and validate in an owned staging directory before changing live data.
   Check archive integrity, manifest format/bundle ID and group membership. Refuse
   a missing installed group. Legacy flat/wrapped ZIP imports lack a native
   manifest; require recognizable Documents/Library state. Ignore `__MACOSX` and
   `.DS_Store` only when locating their payload wrapper.
2. Stop the app and capture its main/group preference cache as above.
3. Prepare replacement entries on each destination's filesystem. Give staged
   replacements the discovered owner UID/GID (mobile was 501:501 here) and `u+rwX`;
   lower-case `x` would wrongly make every file executable. Do not recursively
   chown/chmod the live root or replace its MCM metadata plist.
4. Move existing entries into destination-local recovery storage, install staged
   entries, then apply preferences. Roll back prior moves across **all affected
   containers** on failure. Keep recovery directories if rollback cannot finish.
5. Relaunch the app; verify real document bytes and both main/shared defaults.
   A restore completion callback is not proof that the app sees the intended state.

Native replacement handles root persistent files. Legacy flat imports only
replace established Documents/Library/tmp directories and retain unrelated root
state. Failure rollback is not power-loss atomicity, and concurrent group writers
remain a limitation. A backup does not include Keychain, every extension's data,
or server/cloud state merely because the main/group archive succeeded.

## Build fixtures that really test sandbox behavior

A no-sandbox root or mobile CLI is useful for filesystem edge cases, but is not a
substitute for a registered app. For a disposable app:

- Give it a unique bundle ID and matching Info.plist executable name. Sign with
  `ldid -I<bundleID> -S<its-entitlements.plist> EXECUTABLE`. By default ldid uses
  the executable basename as CodeDirectory identifier; a mismatch caused the
  runtime HOME to select a different container from LaunchServices on this phone.
- Set application-identifier/app-group entitlements consistently with the intended
  fixture; do not grant no-container/no-sandbox when testing normal sandbox data.
- A bundle registered under `/var/jb/Applications` is System. For a normal User
  fixture use a task-owned bundle path under `/var/containers/Bundle/Application`
  and `uicache -p PATH`; verify Type/Container Path with `uicache -i` afterward.
- Write test commands as mobile, or explicitly correct **both** newly created
  directories and files to mobile ownership if using root. A root-owned Documents
  directory created after reset makes rename/rollback fail for SpringBoard.
- Ensure the app actually exits before relaunch. A successful launch request for
  an already-running app does not rerun didFinishLaunching or seed new defaults.
  Check a new PID, fresh app-generated report and expected baseline values before
  backing up. Inspect archive contents to distinguish a bad fixture baseline from
  a restore defect.
- Test original → backup → changed → restore → original, then reset → absent
  main defaults; separately verify the expected shared-group state. Keep all
  fixture data isolated from user apps.
- Unregister the exact task app paths, remove only metadata-verified task-created
  containers/groups/backups, unmount any task-created volume **before** deleting
  its mount directory, remove probe filters/dylibs, restart the target to unload
  callbacks, and verify no fixture processes remain. Preserve original user data.
