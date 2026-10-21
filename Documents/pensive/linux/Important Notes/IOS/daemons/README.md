# Start here: iPhone daemon work

Use this guide for the operating sequence. The [iOS 16 reference](daemons%20based%20on%20ios%2016.md)
contains dependency evidence and dated results; [daemon.cfg](scripts/daemons/daemon.cfg)
contains preset targets. The older [usage notes](scripts/daemons/instructions.md)
and [config notes](scripts/daemons/README.md) retain history. Their retired
`daemonmanager`/`processkiller` scripts are not prerequisites for the native app.

For USB SSH, authentication, Frida and UI control, start with the separate
[control manual](../ssh/control/README.md) and its [bootstrap procedure](../ssh/control/BOOTSTRAP.md).
SSH is sufficient for launchd inspection; Frida is needed only for relevant
in-process UI inspection/actions.

The host control CLI needs Python 3.15+. The app's embedded scripts target the
phone's separately observed Python 3.9.9; verify that runtime, shell, launchctl
and sudo/su are installed before diagnosing app command failures. Native mode
means the retired daemonmanager script is optional, not that Python or jailbreak
tools are unnecessary. Do not apply the Linux host's Python floor to the phone.

## Resolve current facts before acting

The owner's latest instruction and the authorized task take precedence. Read
live state when the task permits device access. Source describes what that
checkout implements; it does not prove which build is installed or what is
running. A dated test establishes only its recorded device, profile and workload.

Within the historical notes, a later explicit correction supersedes the older
claim about that topic. Several older sections say "current" or "final"; those
words refer to their dates. Do not restore an old profile because a historical
section calls it final. Preserve existing user choices unless the task changes them.

The documented phone is iPhone10,2 / iPhone 8 Plus, iOS 16.7.16 / 20H392,
palera1n rootless with `/var/jb`, ElleKit 1.2 and no device passcode. Earlier
Dopamine attribution is superseded. Confirm the actual runtime before relying
on this profile. Use respring or userspace restart when needed; never normal
kernel reboot this setup. The SSH password is unrelated to a UI passcode.

At the documentation/source check on 2026-10-21:

| Item | Reference value and meaning |
|---|---|
| Daemon Manager checkout | `/mnt/zram1/daemon_manager`, source version 1.0.32/build 33; installed build must be checked separately |
| TrollStore reference checkout | `/mnt/zram1/TrollStore-main`; contains separate full and Lite build paths |
| Presets | 513 entries, 331 Off / 182 On, including 58 Critical On; aliases mean these are not unique installed jobs |
| `profiled` | Recommended On, builtin enabled; preserves native Auto-Lock/configuration policy functionality |
| SSH | Owner reported root and mobile password login working over USB port 2222; older root-login failures are historical |

Both checkouts are on temporary storage. If absent, locate the intended source
again; old `/home/.../daemon_manager`, `daemon_manager-review` and
`/mnt/zram1/ios/...` paths are historical locations. The app repository is
[dusklinux/daemon_manager](https://github.com/dusklinux/daemon_manager).

## Keep the different states separate

| State | Meaning / evidence |
|---|---|
| Builtin target | Compiled in `DefaultPresets.swift`; `daemon.cfg` mirrors it |
| Custom target | App preference overriding a builtin; editing a target does not apply it |
| Saved launchd override | `launchctl print-disabled DOMAIN`; `yes` in config means disable, `no` means enabled, uppercase `NO` marks Critical enabled |
| Registered job | `launchctl print DOMAIN/LABEL`; registration does not require a running PID |
| Running process | Fresh process/PID and launch state; enabled on-demand jobs may be idle |
| Working feature | Actual requested behavior after clients settle/restart; an On label is insufficient |

Installing or updating the app does not apply defaults. Apply Defaults acts on
installed services and can replace manual live choices with the selected targets.
It is not an inspection or a recovery shortcut. Missing overrides and explicit
enabled entries can have the same enabled behavior but different saved maps.
The observed system/mobile maps can expose the same entries; do not double-count them.

## Inspect, change, verify, restore

1. Reuse USB SSH and check the actual account with `id`. Discover command paths,
   installed versions, mobile UID and current PIDs. An interactive `sudo -s`
   elevates that shell only, not other channels sharing its SSH master.
2. Before changes, save native override maps, relevant app/UI preference files
   with ownership/mode, and each affected job's domain, plist path and registration
   state. Keep original backups separate from later snapshots. Record an existing
   capture/upload before any restart. Use a writable host working directory for
   downloaded evidence; avoid long interactive remote here-documents, which had
   a reproduced dash input/interrupt fault in the audit.
3. Resolve a service by its plist's actual `Label`, not the filename. The app
   discovers mobile UID and scans LaunchAgents/LaunchDaemons plus selected
   LaunchAngels. `nehelper-embedded` and `bootps` illustrate filename/label
   differences; some preset entries are aliases or absent on this device.
4. Use native launchctl operations or the app for the authorized labels. General
   batches save overrides and require userspace restart. Only the app's tested
   live allowlist also loads/unloads immediately. Killing a PID is not a saved
   override and can trigger a demand restart. Do not rewrite `disabled.plist`
   directly; the tested persistence metadata is root:wheel, mode 0644.
5. Check command results, re-read saved overrides, then check registered/running
   state after any required restart. Reconnect SSH and rediscover PIDs. Verify
   the actual feature; stale UI clients can retain failed connections. The control
   manual explains injection readiness when a UI probe is necessary.
6. For temporary work, restore only the intended changes and verify the final
   state against the immediate pre-change baseline. Restore collectors and owned
   diagnostic files too. Enabling a job can add an explicit false override where
   none existed; report that distinction rather than claim byte-identical recovery.

There is no transaction rollback for a partly completed batch. Later special
controls are skipped on failure, but earlier successful changes remain. Group
On restores feature jobs and known prerequisites. Group Off preserves Critical
and prerequisite-only jobs; a service explicitly selected as a feature in another
group can still be disabled. Shared services have one underlying override.

Emergency launchd reset is a broad database reset, not restoration of a saved
profile. It does not diagnose a boot failure. Preserve incident evidence before
considering it; the historical broad failing profile was not causally isolated.

## Dependencies and measurement limits

Read the latest evidence before disabling dependencies. `searchd`, `pkd`,
`nehelper`, `bluetoothd` and `BlueTool` have reproduced CPU/retry/crash dependencies.
Core infrastructure remains Critical. Recommended On covers functionality and
other dependencies; it is not a promise that Off is stable. Account/SMS, Photos
conversion, PassKit, cloud-key/context and Auto-Lock repairs remain relevant even
where their older Critical classification has changed.

Dictation needs assistantd + corespeechd in the tested profile, despite both
builtin targets being Off. Lock Screen media controls used PosterBoard +
liveactivitiesd; disabling Wallpaper can affect that shared PosterBoard dependency.
SpringBoard interface flags are separate from daemon.cfg and apply after respring.
Keep profiled's current On state during diagnostics; older restore-to-Off records
predate the Auto-Lock repair. AirDrop's helper restores the state it found.

Use bounded observations appropriate to the change. Short foreground success
missed searchd's delayed locked-idle CPU loop. Continuous USB log streaming itself
caused substantial diagnosticd CPU. Disabled crash collectors limit negative
crash-file evidence. Do not turn process footprints into net RAM savings, free
RAM into pressure, or short tests into battery/overnight/boot guarantees. The
finite sampler in the app's `tools/sample_resources.py` uses Mach timebase CPU
accounting and PID-start identity; the documented phone's ps CPU/RSS was unreliable.

## Source map and installation

| Source in the app checkout | Responsibility |
|---|---|
| `DaemonApp/AppModel.swift` | Enumeration, domains, state reads, actions and process execution |
| `DefaultPresets.swift`, `OverrideServices.swift` | Targets, classifications, descriptions, groups and live allowlist |
| `NativeOverrides.swift`, `FeatureBatchControl.swift` | Saved/live verification and mixed-control sequencing |
| `ElevatedCommand.swift` | Password over stdin and unchanged sudo/su command forwarding |
| `AirDropControl.swift`, `helpers/airdrop-policy.m` | Entitled native policy change and profiled restoration |
| `SpringBoardUIControl.swift`, `helpers/springboard-ui.c` | Companion installation, flags and current-PID hook report |
| `MemoryStatistics.swift`, `MemoryStress.swift`, `DiskDiagnostics.swift` | Accounting, temporary allocations and on-demand diagnostics |
| `project.yml`, `.github/workflows/build.yml`, `tests/`, `audit/` | Build identity, packaging, local checks and dated device evidence |

App preferences were observed under bootstrap mobile home
`/var/jb/var/mobile/Library/Preferences`; discover the actual app domain/location
before backing up. SpringBoard uses native `/var/mobile`: interface settings are
`Library/Preferences/com.dusklinux.DaemonManagerUI.plist`, and its runtime report is
`Library/Caches/daemon-manager-ui-runtime.json` there. Never substitute the account
database home for SpringBoard's native home. Duskie's preferences are a separate domain.

Inspect the installed TrollStore variant and helper before installation. Historical
installs used `/var/jb/Applications/TrollStoreLite.app/trollstorehelper` as root,
with `install force PATH_TO_IPA`. Full TrollStore's CoreTrust support table does
not establish Lite/jailbreak behavior on iOS 16.7.16. In the supplied TrollStore
source, `RootHelper/main.m` dispatches installs and replaces bundles,
`Shared/TSUtil.m` supplies root spawning, and `RootHelper/uicache.m` handles
registration/containerization. `TROLLSTORE_LITE` changes signing behavior.

For a requested update, match the intended source, successful build, IPA hash
and embedded metadata before transfer; compare installed metadata and preserved
preferences/overrides afterward. A successful install or `uiopen` request does
not prove the app stayed running or that a feature works. An update replaces
the bundle; uninstall also removes data and can remove group/plugin containers.
Do not substitute uninstall/reinstall for an update to preserve preferences.
