# Extending the control tool without repeating the investigation

Read the [README](README.md) first. This file explains the implementation and
records the failure lessons that matter when adding a capability. It is not
permission to operate a device that belongs to another active session.

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
Objective-C object through an await after that pool has drained. Convert output
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
creating thousands of duplicate wrappers during a view-tree inspection. No
performance improvement percentage has been measured. Caches do not cross
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
./iphone-control --process Camera js ./controllers-example.js --args '{}'
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
| pointer to a completion block | `pointer` | nil can be ptr(0) only if the method accepts nil |

**A C string is not NSString.** `utf8('com.apple.camera')` returns C bytes suitable
for sel_registerName or notify_post. Passing it to a method expecting NSString
caused a SpringBoard restart in the original main-thread diagnostic. Use
`ns('com.apple.camera')` for the app-launch selector. The new launcher tests cover
that distinction without touching the phone.

**A JavaScript Boolean is not automatically a valid NativeFunction BOOL argument
in the observed client.** Use 0/1 for explicit native bool arguments. A diagnostic
first failed with `expected an integer` when it supplied `false`; the corrected
call uses 0.

**The launch method returns BOOL.** Actual encoding:
`B28@0:8@16B24`, not `v28@0:8@16B24`. Even if ignoring a return happened not to
crash, that is not justification to leave the wrong prototype.

**An absent selector is not a no-op.** The earlier
`_simulateHomeButtonPress` call was absent and crashed a diagnostic SpringBoard.
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

**Don't send NSSet directly to JSON serialization.** An older diagnostic tried
this and raised an Objective-C exception in Camera. Walk the collection and
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
that every action has been run in the new wrapper. The reference evidence is in
[VALIDATION.md](VALIDATION.md). Runtime mismatch stops that action.

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

The main session's typed Camera hooks additionally observed:

| Hook | Actual encoding recorded in the source probes |
|---|---|
| CAMCaptureEngine captureOutput:didStartRecordingToOutputFileAtURL:fromConnections: | `v40@0:8@16@24@32` |
| CAMCaptureEngine captureOutput:didFinishRecordingToOutputFileAtURL:fromConnections:error: | `v48@0:8@16@24@32@40` expected and guarded in the candidate; verify on the live build |
| CAMViewfinderViewController _captureStillImageWithRequest:error: | `B32@0:8@16^@24` |
| CAMViewfinderViewController _startCapturingVideoWithRequest: | `B24@0:8@16` |
| CAMPersistenceController _notifyDelegateOfCompletedStillImageLocalPersistenceForRequest:withResult: | `v32@0:8@16@24` |
| CAMPersistenceController _notifyDelegateOfCompletedVideoLocalPersistenceForRequest:withResult: | `v32@0:8@16@24` |

These hook signatures are reference notes, not persistent hooks installed by the
control tool. Capturing/redirecting media storage belongs to Duskie's source and
needs its own build, installation and regression checks.

## Adding a built-in operation

1. Find the receiver in the correct live process. Inspect controllers, classes,
   declared methods and relevant superclass methods. Read authoritative SDK/API
   documentation for public interfaces. For private interfaces, confirm the
   actual runtime and relevant call sites; iOS 17 header dumps alone do not prove
   an iOS 16 encoding.
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
8. Test on the phone only when its owner/main task has made it available. Record
   the device/OS, method encoding, inputs, actual observed result, side effects
   and cleanup. Update VALIDATION.md without turning a finite test into a universal
   reliability claim.

New commands should be useful actions or observations, not an untyped generic
`invoke CLASS SELECTOR ARGS` interface that forces every later model to guess ABI
rules again. `js` remains available for deliberate expert probes.

## Questions future models should resolve before making claims

- Is the current process still the same PID? Did a respring, app relaunch or Safe
  Mode transition invalidate an earlier pointer or state report?
- Is the receiver truly in SpringBoard, Camera, Settings or the Duskie app? A class
  existing in a framework doesn't mean its active UI instance lives here.
- Did the requested UI transition settle, or did the method merely accept it?
- Did saved settings propagate into a cached process? Does status have a fresh
  timestamp/PID and identify this request?
- Did the actual media encode at the expected resolution/rate, with continuous
  audio/video, or was only a preference label checked?
- Did FTP grow before stop? Were acknowledged segments reclaimed, or did the
  entire native seekable movie remain allocated in RAM?
- Are failure recovery, original preferences, current Camera format and cleanup
  accounted for? Does another agent still own the server/tunnel/capture?

## Deliberate exclusions

The tool does not infer future private selectors, modify binary offsets, patch
Frida server paths, manage jailbreak/bootstrap installation, change injection
filters, enroll biometrics, bypass a device passcode, or automate recovery on every
error. It does not synthesize general touch/HID streams. Those are separate tasks
with different interfaces and validation requirements. Existing typed semantic
handlers are often enough; add another stack only when the requested interaction
actually requires it.
