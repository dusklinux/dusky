# Extend control operations and inspect private APIs

Read the [README](README.md) first. This file explains the implementation and
defines the bridge, ABI profile and procedure for adding a capability.

## Architecture and ownership

`iphone-control` owns one host invocation. It resolves the target PID over SSH,
attaches with Frida, loads `runtime.js`, calls an RPC export and detaches in
`finally`. The `Phone` object does not install or start a server. `runtime.js`
lives inside the target process for that Frida session and provides one checked
operation dispatcher. It has no constructor that runs UI actions on load.

SSH worker commands bypass Frida. The first elected Duskie camera worker may stay
running rather than returning immediately. That is why the README uses separate
start/stop terminals. Don't apply an arbitrary short SSH timeout and interpret
its expiration as proof that a recording stopped.

Every UI operation runs through `onMain(job)`. That helper calls dispatch_async_f
on the exported main queue, retains its NativeCallback and pins the script until
the scheduled callback runs. It establishes an autorelease pool for Foundation
objects created by that job. Job code must be **synchronous**; do not retain an
Objective-C object through an await after that pool has drained. Frida pointers
do not retain the Objective-C object they point to. Compiled ARC code has the
same requirement for an unowned bridged temporary; keep the NSString path owned
for all later C calls. Use a state-owned ivar or a precise-lifetime local as shown
in CONTAINERS.md before keeping a CFStringRef. Convert output
to ordinary JavaScript strings, numbers, arrays and objects before returning.

The host's RPC cancellation bounds its wait. It cannot roll back an operation
already queued on the phone. A process whose main queue is stuck can retain a
pinned job longer than the host wait. Check actual device state before retrying
an action, especially a toggle, camera start or simulated button.

## Bridge helpers available to a custom JS file

A file passed to `iphone-control js FILE` is appended after `runtime.js` and can
use these names directly. Define **`rpc.exports.run(parameters)`** in that file;
the host calls it with the decoded `--args` JSON value.

| Helper | Contract |
|---|---|
| `native(name, resultType, argumentTypes)` | Cached NativeFunction for a global C symbol; fails if missing |
| `utf8(text)` | Temporary UTF-8 **C string** pointer, not an Objective-C object |
| `selector(name)` | Cached SEL from sel_registerName |
| `classNamed(name)` | Cached Class; throws if absent in this process |
| `ns(text)` | Actual NSString constructed using stringWithUTF8String: |
| `text(object)` | Copy NSString to JavaScript text; null for nil/non-NSString |
| `send0(object, selectorName)` | Typed pointer-return, zero-argument ObjC send for known object-return APIs |
| `getMethod(object, selectorName)` | Runtime method for the receiver's actual class, including inherited methods |
| `encoding(method)` | Exact Objective-C encoding as text |
| `checked(object, name, encoding, returnType, argumentTypes, arguments)` | Require the exact live encoding, then invoke objc_msgSend with explicit ABI types |
| `responds(object, name)` | Selector availability check; does not establish its argument/return ABI |
| `classOf(object)`, `className(object)` | Objective-C runtime class queries |
| `isKind(object, className)` | NSObject class-membership query |
| `number(value)` | Convert Frida Int64/UInt64 values to a JavaScript number for small enums/counts |
| `count(array)`, `at(array,index)` | NSArray access for known array-return APIs |
| `shared(className)` | Checked sharedInstance singleton |
| `app()` | Checked UIApplication sharedApplication |
| `windows()` | Current UIApplication windows array |
| `integer(object,name)` | Checked q16@0:8 NSInteger getter |
| `boolean(object,name)` | Checked B16@0:8 BOOL getter |
| `onMain(job)` | Promise resolving to a synchronous main-queue job's JS result |
| `controllers()` | Bounded records containing live controller object pointers and parent relations |
| `camera()` | One current CAMViewfinderViewController or a clear error |
| `cameraStatus()`, `videoFormat()` | Checked native state / labels |
| `views(limit)` | Bounded view records including live view pointers; strip pointers for JSON |
| `methodList(className,classMethods,matching)` | Declared methods; superclass methods are separate |

`native`, selector and class wrappers are cached for the script lifetime to avoid
creating duplicate wrappers during a view-tree inspection. Caches do not cross
processes, invocations or resprings. Missing classes are not cached as successes.

### Minimal read-only extension

This inspects a known controller without any legacy dylib or hardcoded address:

```js
rpc.exports.run = parameters => onMain(() => {
    const records = controllers();
    return records.map(({object, ...metadata}) => metadata);
});
```

Run with an explicitly selected live process:

```sh
./iphone-control --control-path /tmp/iphone-control-ssh --process Camera js ./controllers-example.js --args '{}'
```

### Minimal typed invocation example

The following mirrors the built-in app launcher. It must attach to SpringBoard:

```js
rpc.exports.run = parameters => onMain(() => {
    const accepted = checked(
        app(),
        'launchApplicationWithIdentifier:suspended:',
        'B28@0:8@16B24',
        'bool', ['pointer', 'bool'],
        [ns(parameters.bundleID), 0]
    );
    return {accepted: Boolean(accepted)};
});
```

This is an example to read, not an extra automatic startup action. For routine
use, prefer the built-in `launch` command. `accepted` is the native request result,
not proof that an application scene has finished loading.

## Correct ABI: the lessons that prevented repeat crashes

Objective-C messages include two implicit arguments: the object pointer (`self`)
and selector (`_cmd`). The explicit arguments follow. An Objective-C `id`, Class,
SEL, C string and block are all pointer-shaped but have different meanings.

| Source type | Frida call type in these examples | Meaning |
|---|---|---|
| `id` / NSString* / other ObjC object | `pointer` | Real object pointer; create NSString with `ns` |
| `SEL` | `pointer` | Runtime-registered selector |
| `BOOL` | `bool` | Pass explicit 0 or 1; convert return with Boolean |
| `NSInteger` / signed 64-bit enum | `int64` | AArch64 signed integer |
| `NSUInteger` / options bitmask | `uint64` | AArch64 unsigned integer |
| C `int` | `int` | 32-bit source argument, not int64 |
| `void` | `void` | No return value |
| `CMTime` (`{?=qiIq}`) | `['int64', 'int', 'uint', 'int64']` | Struct fields: value, timescale, flags, epoch; use the array as the NativeFunction struct type |
| pointer to a completion block | `pointer` | nil can be ptr(0) only if the method accepts nil |

**A C string is not NSString.** `utf8('com.apple.camera')` returns C bytes suitable
for sel_registerName or notify_post. Passing it to a method expecting NSString
can crash SpringBoard. Use
`ns('com.apple.camera')` for the app-launch selector.

**A JavaScript Boolean is not automatically a valid NativeFunction BOOL argument
in the observed client.** Use 0/1 for explicit native bool arguments. Use 0 or 1 if a native bool argument rejects a JS Boolean with
`expected an integer`.

**The launch method returns BOOL.** Actual encoding:
`B28@0:8@16B24`, not `v28@0:8@16B24`. Even if ignoring a return happened not to
crash, that is not justification to leave the wrong prototype.

**An absent selector is not a no-op.** `_simulateHomeButtonPress` is absent on this profile.
The observed selector is `_simulateHomeButtonPressWithCompletion:` with encoding
`v24@0:8@?16`. Pass nil for its optional completion, after checking it exists.

**A selector name does not reveal integer width.** The observed unlock source is
C int, while backlight source is NSInteger. Inspect the full runtime encoding.
Offsets in encodings describe layout; do not strip them out and assume an
apparently similar signature is interchangeable.

**Don't send untrusted object-return guesses.** The generic UI inspector checks
optional string getters have an object-return encoding before reading them and
checks the result is NSString before asking for UTF8String. A custom view can
have a method named text with a different return type. `responds` alone doesn't
prove the type of that getter.

**Don't send NSSet directly to JSON serialization.** Walk the collection and
produce normal JavaScript arrays/objects, converting each item deliberately.
The new runtime does not serialize arbitrary native collections as JSON.

**Blocks require a real block representation.** NativeCallback creates a C
function pointer, not an Objective-C block object. The current tool passes nil
only to methods already known to accept an optional nil completion. Do not
substitute a NativeCallback as an arbitrary block argument. Add a proper bridge
or native helper when the API requires a non-nil block.

**64-bit IDs aren't always JavaScript numbers.** `number()` is appropriate for
small enums/counts here. Preserve large IDs/timestamps as strings or explicit
Int64 values if loss of precision can affect an operation.

## Private-method profile used by built-in actions

These are the **expected encodings in the implementation**, not a blanket claim
that every action has been run in the new wrapper. Check capability status in [VALIDATION.md](VALIDATION.md). Runtime mismatch stops that action.

| Receiver / selector | Expected encoding |
|---|---|
| UIApplication class / sharedApplication | `@16@0:8` |
| Singleton classes / sharedInstance | `@16@0:8` |
| SpringBoard / launchApplicationWithIdentifier:suspended: | `B28@0:8@16B24` |
| SpringBoard / _simulateHomeButtonPressWithCompletion: | `v24@0:8@?16` |
| SpringBoard / _simulateLockButtonPress | `v16@0:8` |
| SBBacklightController / screenIsOn | `B16@0:8` |
| SBBacklightController / turnOnScreenFullyWithBacklightSource: | `v24@0:8q16` |
| SBLockScreenManager / isUILocked | `B16@0:8` |
| SBLockScreenManager / _finishUIUnlockFromSource:withOptions: | `B28@0:8i16@20` |
| SBLockScreenManager / lockUIFromSource:withOptions: | `v28@0:8i16@20` |
| DKLockController / enabled | `B16@0:8` |
| DKLockController / authenticateInternally | `v16@0:8` |
| CAMViewfinderViewController / currentCaptureMode | `q16@0:8` |
| CAMViewfinderViewController / isRecording, startRecording, stopRecording, capturePhoto | `B16@0:8` |
| CAMViewfinderViewController / changeToMode:device: | `v32@0:8q16q24` |
| CAMViewfinderViewController / _videoConfigurationStatusIndicator | `@16@0:8` |
| CAMViewfinderViewController / _currentVideoConfiguration | `q16@0:8` |
| CAMVideoConfigurationStatusIndicator / framerate, resolution | `q16@0:8` |
| CAMVideoConfigurationStatusIndicator / _framerateLabel, _resolutionLabel | `@16@0:8` |
| CAMViewfinderViewController / videoConfigurationStatusIndicatorDidTapFramerate: | `v24@0:8@16` |
| CAMViewfinderViewController / videoConfigurationStatusIndicatorDidTapResolution: | `v24@0:8@16` |
| UIViewController / dismissViewControllerAnimated:completion: | `v28@0:8B16@?20` |
| UIControl / sendActionsForControlEvents: | `v24@0:8Q16` |
| UIControl / isEnabled, UIView / isHidden | `B16@0:8` |
| LSApplicationWorkspace class / defaultWorkspace | `@16@0:8` |
| NSData / base64EncodedStringWithOptions: | `@24@0:8Q16` |

For `unlock`, **semantic state matters beyond ABI**: the native finish method
aborted SpringBoard while Duskie's simulated lock was active. The current runtime
wakes, checks lock state, and uses enabled `DKLockController.authenticateInternally`
in that case. Do not bypass that routing in a custom snippet; see unlock.md.
Screenshot is a C export, `_UICreateScreenUIImageWithRotation(BOOL) -> UIImage *`,
not an Objective-C selector. Call with Frida result `pointer`, argument `bool` 1,
on main, then encode/copy the result before the autorelease pool drains.

## Adding a built-in operation

1. Find the receiver in the correct live process. Inspect controllers, classes,
   declared methods and relevant superclass methods. Read authoritative SDK/API
   documentation for public interfaces. For private interfaces, confirm the
   actual runtime and relevant call sites; iOS 17 header dumps alone do not prove
   an iOS 16 encoding. Confirm that the selector actually executes during the
   target operation; existence and matching ABI alone do not establish the active
   call path.
2. Write a read-only probe for the values that determine the operation. Observe
   the real environment rather than assigning an enum from memory.
3. Specify the exact Objective-C encoding and explicit NativeFunction argument
   types. Use NSString conversion for object parameters. Keep UIKit work on the
   main queue. Model ownership/autorelease lifetime when retaining objects.
4. Add one operation to `actions` in runtime.js. Keep private invocation inside
   `checked`. Avoid automatically choosing a guessed selector or offset on failure.
5. Add argparse handling and a minimal parameters dictionary to iphone-control.
   Pass ordinary JSON values, not Path objects, bytes, pointers or a complete
   argparse Namespace.
6. Decide whether the result is a request acknowledgement or verified completion.
   For asynchronous work, expose the read-only state query and a bounded wait that
   observes it. Avoid unbounded polling, permanent loops or automatic restarts.
7. Add a focused host test when there is meaningful host behavior to validate.
   Host tests do not establish the correctness of an iOS private ABI.
8. Exercise the operation in the authorized task. Check the actual resulting
   state and restore temporary changes. Update the capability status if this
   changes a known limitation; do not add a project diary to the manual.

New commands should be useful actions or observations, not an untyped generic
`invoke CLASS SELECTOR ARGS` interface that forces every later model to guess ABI
rules again. `js` remains available for deliberate expert probes.

## Diagnose a new operation

1. Verify the process and fresh PID; a receiver in Camera is not a SpringBoard
   receiver. Inspect current controllers and loaded modules.
2. Use `methods CLASS --matching TEXT` and inspect relevant superclasses. If a
   framework/class is absent, identify the actual lazy-loaded framework before
   invoking anything. An iOS header dump for another version is not a live ABI.
3. Read the values that determine the action before writing them. Select table
   rows by their current data/specifier IDs, not remembered indices. Require one
   intended match; never select an empty search result.
4. Convert returned data to plain JS before leaving `onMain`. Keep large integers
   as strings and handle nil without messaging it as a valid object.
5. Observe settled state after the request. For delayed callbacks/notifications,
   distinguish a queued action from its completion. For media, inspect the file.

## Failure patterns to avoid

- Wait for injection initialization after a UI restart; see README.md. Serialize
  Frida operations. A suspended app or blocked main queue may not answer an RPC.
- An exact ABI match is necessary but does not establish a valid operation in the
  current lock, scene or capture state. Use the owning feature's state machine.
- An assertion or launch acknowledgement is not completion. Stop/wait and fresh
  sandboxed-app checks for data operations are documented in CONTAINERS.md.
- Do not call arbitrary private objects’ `description` to dump them. Getters and
  descriptions can run code, throw KVC exceptions or change state. Read only
  required typed values; use className for unsupported objects.
- NativeCallback is a C function pointer, not an ObjC block. For a required block
  or complex UIKit struct signature use a correctly typed compiled helper; see
  DEVELOPMENT.md. Do not guess a block layout or use an absent `_invokeHandler`.
- `dismiss-alert` dismisses the view; it does not run the UIAlertAction handler.
  App-owned overlay windows may require their actual handler for cleanup.
- Avoid synchronous ObjC exception tracing inside an Interceptor while another
  native call waits for the main queue. This can deadlock. Do not switch every
  NativeFunction to exclusive scheduling to conceal the dependency.
- Walk UI/controller trees with a seen set and a node bound. Parent/presented
  relationships can cycle; UIApplication windows can include windows outside
  connected scenes. Choose the required active controller explicitly.
- Recompute internal symbol addresses from the exact installed build after each
  rebuild; compare payload hashes. A previous offset is not an API. Stripped
  symbols can make that diagnostic unavailable; do not substitute a guessed one.
- Use task-specific probe module names, not Python standard-library names such
  as inspect.py. Check host import paths before blaming a device process.

General coordinate/HID gestures, arbitrary keyboard input, device-passcode unlock
and biometric enrollment are not bundled operations. Prefer the app’s checked
semantic handlers. If the requested task needs a missing interface, implement
and verify that operation deliberately rather than advertise a guessed command.
For native bridge details use [Frida’s JavaScript API](https://frida.re/docs/javascript-api/).
