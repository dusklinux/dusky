# Verify readiness and completion

Use this checklist to establish readiness, observe completion and leave the
phone in the intended state.

## Before the first action

1. Confirm USB SSH independently of Frida: `doctor --phone`, then `ps`.
   Reuse the intended account/master and check `id`; BOOTSTRAP.md documents
   mobile-plus-sudo and direct root. A mobile master does not make other channels
   root when one interactive channel uses sudo.
2. Check the actual device/OS, current PIDs and installed package versions.
   This manual’s private ABI profile is iPhone10,2 / iOS 16.7.16 / 20H392.
3. Confirm who owns the SSH tunnel, Frida server and any recording. Match client
   and server versions. A working SSH connection is not a working Frida server.
4. After a respring/userspace restart, wait for the relevant tweak’s readiness
   signal before attaching. For Duskie, its runtime report PID must match the
   fresh SpringBoard PID. Check enabled/error fields; do not trust a stale report.
5. Check that the target app is running, foreground/responsive where necessary,
   and contains the expected controller/framework. App launch acceptance is not
   UI readiness. Do not interrupt an existing Camera capture to inspect another app.
6. Read initial state and back up preferences before temporary changes.

## Choose observable completion

| Operation | Verify afterward |
|---|---|
| Wake/unlock/sleep | Fresh PID, screenIsOn and isUILocked; not just request return |
| Launch/navigate | Correct app process and destination controller/view |
| UIControl event | Resulting app state; event dispatch alone is insufficient |
| Gesture / keyboard editor | Real hold/pressure/swipe, editor presentation, text focus, Save/Cancel and persisted data |
| Table/setting selection | Correct specifier/value/checkmark and live behavior |
| Notification | Listener’s fresh acknowledgement/state, not notify_post’s code |
| Saved preferences | Readback, correct owner/mode and cache propagation |
| Start/stop recording | Actual recording state, fresh worker status, file growth/finalization |
| Format selection | Displayed labels, then actual encoded media |
| FTP/USB test | Fresh timestamp, selected transport, login plus passive data result |
| Streaming | Receiver growth before stop and a valid finalized file |
| Auto-Lock | Effective policy, enabled native row and native idle descriptor |
| Tweak install | Installed hash/version, fresh process loading the dylib, intended effect |
| Helper removal | Fresh process without its module/listener after restart |

For media, inspect dimensions, orientation, frame rate, audio and full decode with
ffprobe/ffmpeg as shown in RECIPES.md. Nominal fps does not prove zero drops.
Do not sum process footprints or infer battery/NAND savings from a control action.

## Known capability boundaries

- USB SSH/process discovery and the CLI’s unlock, app-launch, controller,
  notification and custom-JS routes have operated on this profile. Private APIs
  still require a responsive process and the expected live method encoding.
- The built-in screenshot route is **unavailable on this profile** because it
  requests the absent `UICreateScreenUIImage` export. RECIPES.md supplies an
  explicit adapter for the different native API used by the installed capture
  path. Inspect that adapter’s output/orientation; it is not a tested replacement
  built into the CLI.
- Camera mode/recording/format methods have the expected profile in EXTENDING.md.
  Verify each wrapper operation’s actual result; a host mock does not execute iOS.
- `tap` delivers UIControl TouchUpInside, not coordinate/HID input. A synthetic
  gesture-handler invocation does not verify physical touches. Standalone UIKit
  fixtures without an application event dispatcher cannot establish real control
  delivery or scene presentation; test those in the running app or mark them
  unverified. When the owner defers UI testing, report that limit explicitly.
- Generic keyboard input, arbitrary swipe/drag streams, biometric enrollment and
  device-passcode unlock are not bundled. The unlock procedure applies to the
  owner’s no-device-passcode configuration, separate from its SSH password.
- A timeout can occur after an action was queued. Cancellation does not undo it,
  and the RPC deadline does not bound all Frida attach/teardown operations.
- Frida detach does not undo changes or unload separately dlopened callbacks.

## After a failure

Record the exact error and last action. Check fresh processes, responsive main
queue, injection state and relevant crash/log output. Stop the specific failed
probe rather than immediately repeat a mutation. Follow README.md’s diagnosed
recovery procedure. Clear Safe Mode only after identifying/removing the cause;
never normal-reboot this jailbreak setup as a generic recovery step.

For a frozen foreground app, use [DIAGNOSTICS.md](DIAGNOSTICS.md) to capture a
short targeted stack sample over SSH before repeatedly attaching Frida or
enabling more services. A successful password exchange does not prove the next
verification page rendered; a received modem SMS does not prove Messages stored it.

## Before leaving the phone

- Stop/finalize only your intended captures; preserve pending media/uploads.
- Restore temporary Camera format, preferences and radio state unless the task
  intentionally changes them. Remove any diagnostic keep-awake assertion.
- Stop owned server/forward processes and remove owned probe files. Preserve
  pre-existing SSH/receiver services and user files.
- Stop owned log relays and restore temporarily enabled diagnostic collectors
  to their saved override and registration states. Compare final service state
  with the immediate pre-test backup, preserving intentional repairs.
- After a required restart, verify the current PID, expected modules and final
  state. Use userspace restart when already-running app processes all need a new
  injection; a respring alone does not restart every app process.
- Keep credentials, screen contents and private runtime dumps out of published
  source. Update this manual with reusable commands or newly established limits,
  rather than a transcript of the project that happened to exercise them.
