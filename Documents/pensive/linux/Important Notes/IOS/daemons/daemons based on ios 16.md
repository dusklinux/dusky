# iOS 16 daemon reference and historical evidence

Start with the [daemon operating guide](README.md) for current entry points,
state definitions, backup/verification steps and source locations. The dated
sections below preserve history: a later explicit correction supersedes an older
claim on the same topic, including sections called "current" or "final". Preset
targets and past observations are not a live snapshot or instructions to reapply
an old phone profile. Use the [control manual](../ssh/control/README.md) for USB SSH.

## Auto-Lock policy recommendation — 2026-10-21, v1.0.32

`com.apple.managedconfiguration.profiled` now defaults **On** (`no` / builtin
`false`) and is **Recommended On**, outside Critical. **Groups → Auto-Lock and
configuration policy** controls this service alone, with both On and manual Off
available. Restart userspace after a group change. Other enrollment, management
and passcode-prompt jobs retain their existing defaults.

On the supplied iPhone 8 Plus / iOS 16.7.16, profiled was disabled/unregistered.
The battery-saver client retained `maxInactivity = 30` even with Low Power Mode
off, Auto-Lock was greyed out, and native writes failed with “Couldn't communicate
with a helper application.” Enabling and bootstrapping this existing on-demand
Apple service cleared the stale restriction. It also needs to remain available
for Duskie's optional normal Auto-Lock timeout during Low Power Mode. See
[Duskie’s device verification](https://github.com/dusklinux/duskie/blob/main/research/FIXES-0.2.67.md).
This establishes a functionality dependency, not a bootloop or high-CPU risk.

Current matching config/builtins: **513 targets, 331 Off / 182 On, including
58 Critical On**. Only profiled's Boolean target changed. Installing the app does
not apply defaults; explicit custom targets remain authoritative. The existing
AirDrop helper still restores profiled's prior state, so an already-enabled job
stays enabled. Historical notes describing profiled Off are superseded by this
recommendation and the October 21 repair. Future diagnostics should preserve
its current enabled state rather than restore the old Off profile. No additional
phone changes, daemon audit, or performance claim accompanies this update.

## Group Off audit — 2026-10-18, v1.0.31

Defaults remain **513 target entries: 332 Off / 181 On, including 58 Critical On**. Counts include aliases. This revision changes no builtin Boolean target and does not apply defaults during installation. Core storage/filesystem/NAND, cache reclamation, memory/resource management, NAT/network configuration and USB/SSH access remain enabled.

**bluetoothd and BlueTool are now Critical and excluded from grouped feature rows/Off actions.** Disabling only bluetoothd reproduced 48,717 Bluetooth audio invalid-connection errors in 12 seconds, with launchd about 61% and mediaserverd 44% of one core; restoring only bluetoothd stopped the flood. Disabling only BlueTool drove bluetoothd to 98–104% of one core, with 2,418 missing-endpoint attempts in ten seconds; restoring BlueTool reduced it below 1%. The group is now **Bluetooth helpers**, preserving both core jobs. Use Settings for ordinary Bluetooth control; disabling helpers is not a verified radio power switch.

**CommCenter remains Recommended On**, preserved by Calls/SMS Off: its isolated Off/restart reproduced SpringBoard CoreTelephony retries, stopped by restoring only CommCenter. This was a repeated client-error dependency, not a demonstrated high-CPU/bootloop failure. Messages/FaceTime Off similarly preserves avconferenced for other CallKit clients. Explicit Calls/SMS Off can disable those clients and AVConference together. Other intentional feature losses do not automatically make a service Critical.

The revised groups completed short individual resource/core-health checks, then a combined **88-label** daemon Off profile (23 newly disabled, 65 already Off) passed a **241-second idle** observation: Normal pressure, 1.27–3.87% aggregate busy, no core exits/relaunches and no new report in that window. AirDrop was already Off: its helper verified/restored that state without a forced On transition. Both SpringBoard interface controls were tested under timed recovery; Control Center was subsequently enabled alone at the user's request and user-confirmed working.

**Foreground testing was added after the user identified the idle-only coverage gap.** The combined daemon Off profile plus App Library Off completed **28 intervals / 426.78 seconds**, covering normal app switching/scrolling/system apps and an untouched tail. Pressure stayed Normal, all 142 checked enabled jobs had no relaunch/abnormal exit, and no new report file appeared. The user reported normal operation but warmth. Unfiltered USB streaming added substantial diagnosticd CPU: 26–71% of one core during the comparison (median 50%); it was absent during paused-stream active samples and approximately 0% in the paused-stream untouched tail. The earlier full-stream phase peaked at 124%. Streaming was paused while phone logging stayed enabled. These workloads were not identical controlled benchmarks; no temperature, GPU utilization, frame pacing, battery or RAM gain was measured. Existing CoreGraphics invalid-context bursts also occurred in the restored baseline before this combined setup and remain unisolated; no clean-log claim is made.

The temporary changes were restored to the working feature profile after testing; keyboard dictation still uses assistantd + corespeechd, with both builtin targets Off and the phone's manual On choice preserved. Logging/reporting was temporarily enabled for the audit; collector restoration and build/install provenance are recorded in the app audit. Core logging and Analytics/diagnostics Off were excluded at the user's request; the interrupted logger-Off step is not a pass. USB/SSH were never selected Off. These are bounded iPhone 8 Plus/iOS 16.7.16 observations, not overnight/full-kernel-boot certification. Dated sections below preserve earlier counts/results; this section gives the current targets.

### v1.0.31 deployment and final restoration

[Actions 39114527964](https://github.com/dusklinux/daemon_manager/actions/runs/39114527964) passed the 32 Python checks, native Swift checks and iOS build/sign/package/release for source **7a0e2d69dab4a42b13ff1ed11f938442eb3d9bf5**. [v1.0.31/build 32](https://github.com/dusklinux/daemon_manager/releases/tag/v1.0.31) IPA SHA256 **1664b23aebe402a197d837ceac913858a3eb6f66ec2c461c8ff4decf6ad333ba** matched GitHub's digest and both downloaded/phone files. TrollStore install and app launch returned 0; installed version/build/source matched. Parsed app preferences and native overrides matched their pre-install snapshots; installation did not apply defaults. The user confirmed v1.0.31 opens, Bluetooth helpers is present, and bluetoothd/BlueTool appear in Critical rather than the group service list. A post-launch comparison again matched saved preferences and overrides.

The five temporary collectors were restored to their saved disabled/unregistered states; main logd/logd_helper stay On. All temporary audit recovery/queue/observer jobs were removed, both UI companion/preferences returned to their original absent state (Control Center and App Library enabled), and the semantic native override difference from the pre-queue working profile was empty. Dictation's manual On pair is preserved. Kernel boot time stayed 10:30:54; only userspace restarts were used. A final **12 intervals / 180.85 seconds** without relay streaming kept pressure Normal, peak sampled process CPU **3.04% of one core** (wifid), and aggregate busy **1.01–1.58%**. This is a final restored-profile observation, not a matched active-use/GPU/thermal benchmark. Local raw logs and preference backups stay private.

## Dictation and group selection — v1.0.30, 2026-10-18

Keyboard dictation on the tested iPhone 8 Plus/iOS 16.7.16 needs **com.apple.assistantd + com.apple.corespeechd** together. The installed assistantd plist exports `com.apple.assistant.dictation`; corespeechd exports the speech/endpointer/local-recognition bridge endpoints. User comparisons: assistantd alone showed the microphone then closed without recording/text; adding only corespeechd produced text; removing only assistantd made dictation fail again. The working pair was restored. Other Siri/Shortcuts services remained disabled. This isolates the two additions in the existing enabled keyboard/audio/network profile, not every possible language/device dependency. Settings → General → Keyboard → Enable Dictation must also be On. Siri activation can remain Off in Settings; the backend processes are shared and cannot be made exclusive to dictation by changing their labels.

**Groups → Dictation** now controls those two jobs and restores keyboard/audio prerequisites on On. Both builtin targets remain Off; the phone's manual On choice is preserved during installation. **Siri** is a separate group: On restores its shared speech prerequisites, while Off preserves the dictation pair. Dictation Off can also break Siri speech. Shortcuts has its own existing group. No builtin Boolean or Critical classification changed in this revision: **513 entries, 332 Off / 181 On, including 56 Critical On**.

Every group row, including SpringBoard interfaces, is alphabetized. Tap **Select**, check several circles, then use **On** or **Off** in the bottom bar; Select All/Clear and Done are available. Bulk actions merge shared jobs into one operation, preserve Critical and prerequisite-only jobs according to the selected feature policy, and serialize native overrides, AirDrop policy and SpringBoard interface changes under one busy state. Explicitly selecting a feature Off can still remove a dependency used by another feature. Failures skip later controls and report partial failure; they do not imply rollback of changes already completed. Interface settings need respring, and broader daemon changes need userspace restart. The selected groups are temporary UI selection, not new default targets.

All 32 local Python checks passed, including mixed-control sequencing and failures. Native Swift checks cover alphabetization, deduplication, speech membership and Critical/prerequisite preservation. The macOS build and device UI checks are recorded below. Diagnostic relay collection was temporary; diagnosticd was restored Off. Raw speech logs remain private and are not committed. No new RAM/battery benefit is claimed.

### v1.0.30 build and installed state

[Actions 39104670380](https://github.com/dusklinux/daemon_manager/actions/runs/39104670380) passed 32 Python tests, native Swift group/migration/elevation/memory checks and the iOS build/sign/package/release for source **778224e0c0116c026177e7d5a34bad131c1d7ed6**. [v1.0.30/build 31](https://github.com/dusklinux/daemon_manager/releases/tag/v1.0.30) SHA256 **7b7fe4da6159ad2ec8520b79cb04a43622d416f51e4273c88df35cdec7ab581c** matched GitHub's digest, the downloaded file and phone copy. TrollStore install and uiopen both returned 0. Installed version/build/source matched; parsed saved overrides and app preferences matched their immediate pre-install snapshots. Installation did not apply defaults. A separate two-minute observation after restoring the working speech pair kept memory pressure Normal: assistantd median CPU 0.0045%, maximum 0.392%; corespeechd median 0%, maximum 0.018% of one core. The phone was in use; this is not a controlled idle or battery benchmark.

Compared with the pre-dictation saved map, exactly assistantd and corespeechd changed from disabled to enabled. The diagnostic relay returned Off; other Siri jobs remain Off. The user confirmed alphabetical order, separate Dictation/Siri pages, a successful multiple-selection Off action on already-disabled Screen recording and Widget updates, and working keyboard dictation. This checked the new control without requesting unrelated feature activation. Native compilation and unit tests do not establish every physical feature-control combination.

## Current defaults and batch review — v1.0.29, 2026-10-18

This section supersedes older counts, classifications, paths and current-state claims below. The maintained app checkout is `/mnt/zram1/daemon_manager-review`, remote [dusklinux/daemon_manager](https://github.com/dusklinux/daemon_manager). The connected iPhone 8 Plus was restored today after a bootloop; older phone profiles are historical. USB SSH currently uses mobile with sudo; root SSH did not authenticate. Discover the address, user and runtime again on another device.

The matching config and compiled builtins contain **513 target entries: 332 Off / 181 On, including 56 Critical On**. This includes aliases, not 513 unique installed jobs. `yes` saves disabled, `no` saves enabled, `NO` marks Critical enabled. The actual `com.apple.bootpd` label is now included alongside its `bootps` filename entry so Apply Defaults reaches that installed job. Explicit custom targets remain authoritative; installing the app does not apply defaults.

Seventeen optional jobs become default Off after the userspace-restart batch and user-confirmed basic checks: progressd, ndoagent, AssetCacheLocatorService, DataDetectorsSourceAccess, synapse.contentlinkingd, reversetemplated, matd, midiserver, nlcd, racoon, bootpd, cmfsyncagent, systemstats.microstackshot_periodic, DragUI.druid, accessibility.heard, nand.aspcarry and ind. This intentionally loses associated education progress, outreach, cache discovery, detected-content/suggestion/linking features, migration, MIDI, link conditioning, IPsec VPN, hotspot DHCP serving, filter synchronization, hearing features, telemetry and iCloud notices. General Wi-Fi/Bluetooth/calls stay enabled. These are scoped checks on iOS 16.7.16, not universal safety or overnight/full-kernel-boot certification.

**Keep logd_helper On** for log ownership/deletion maintenance; short Off tests do not exercise rotation, retention or low-storage cleanup. Main logd, APFS/filesystem/disk and NAND maintenance, cache reclamation, memory housekeeping, process/resource management, and packet-filter/network configuration remain On. NANDTaskScheduler is distinct from optional ASPCarryLog telemetry. Descriptions identify preserved maintenance jobs; untested maintenance is not newly called a demonstrated bootloop risk.

**avconferenced now defaults On.** A live-log comparison found repeated callservicesd AVConference invalid-connection errors while it was Off: enable stopped them, disable reproduced them, enable stopped them again. This was an existing Off target, not one of the 18 batch jobs. It is Recommended On, not a new Critical/bootloop classification. Other retained log errors include disabled-cloud-service lookups, redacted CoreDuet store failures and Wi-Fi statistics. Logs are not error-free; no sustained one-core CPU loop or new crash-report file was found in the reviewed window. Disabled crash collectors limit negative crash evidence, and no net RAM or battery gain is established.

Critical remains reserved for core infrastructure and demonstrated/historical stability concerns. Apple Account, Photos/export, keyboard, Bluetooth profiles, telephony and other ordinary feature dependencies use Recommended On where appropriate. Critical jobs are absent from Feature Groups rows and state summaries; group On can restore their prerequisites and group Off preserves them. searchd, pkd and nehelper retain the reproduced CPU/crash dependencies. Earlier 90-Critical/no-Recommendations statements are historical.

The user confirmed normal use with the batch Off, then locked the phone for a fresh finite CPU/memory check. The temporary batch recovery job was disarmed and removed after verification; 17 optional jobs stay Off, logd_helper and avconferenced stay On. No further comparably low-risk 20–30-job batch was identified among the remaining enabled jobs without touching preserved features, storage/memory/network management or unresolved hardware roles. Complete diagnostics and build/install provenance are tracked in the app's `audit/2026-10-18.md`; final deployment is recorded below. Reference edits stay local; only the app repository is committed/pushed.

### Completed locked check and deployment — v1.0.29

The final user-confirmed locked run covered **301.45 seconds / 20 intervals**, with memory pressure Normal throughout. No sampled process exceeded **2.531% of one core**. Median CPU: SpringBoard **0.149%**, assetsd **0%**, backboardd **0.484%**, callservicesd **0.057%**, avconferenced **0.0013%**. These are observed costs, not an isolated RAM/battery improvement. Core PID/launch/exit states were unchanged, and a second crash-file index found no new/modified report. A wider scan covered 163 registered jobs from the enabled inventory: none had more than one launch. The sole nonzero exit was **us.diatr.shshd**, a separate rootless SHSH-saving task (exit 1, one launch, configured weekly); its cause is unestablished and it was left unchanged. This is not evidence of an ongoing crash loop or proof that every background task succeeded.

[Actions 39103609783](https://github.com/dusklinux/daemon_manager/actions/runs/39103609783) passed all 27 Python tests, native Swift policy/migration/elevation/memory checks, iOS build, entitlement signing/package and release for source **f98b77041986b89cab399ff5e6182c384bcab482**. [v1.0.29/build 30](https://github.com/dusklinux/daemon_manager/releases/tag/v1.0.29) IPA SHA256 **75c88851fe51fa1ff9df2228ad95f83f720b43b486063f4071f142910fbf7819** matched GitHub's digest and both workstation/phone files. TrollStore Lite returned install exit 0; installed version/build/source matched, and uiopen returned 0. Parsed native overrides and app preferences matched their immediate pre-install snapshots. Installation did not apply targets. The current custom-target dictionary is empty. DaemonApp was subsequently observed running as PID 5618. The user confirmed that it opens and that Defaults shows progressd/ndoagent Off and avconferenced On; Apply Defaults was left untouched for that check.

## Current preference: Control Center and App Library On — 2026-10-13

Following the memory comparison, the user chose to keep both interfaces **On**: the small SpringBoard footprint differences did not establish a meaningful net free-RAM gain. The app already defaults both On when there is no saved preference. The phone's explicit test Off settings were changed to On and applied by respring; current-PID runtime verification confirmed both On, with the native saved daemon map unchanged. Manual Off remains available. No new IPA, daemon target change or kernel reboot was needed. The earlier measurement section's restored-Off statement is historical; the current live state is **both On**.

The existing three UI-control tests passed, and the absent-preference read fixture confirmed On defaults without writing settings. This is a usability choice, not a new performance improvement. Detailed evidence is in `/mnt/zram1/ios/daemon_manager/audit/2026-10-13-ui-memory.md`. Only iOS repository documentation is committed/pushed; these reference notes remain local and the Linux Git index is left untouched by this work.

## Measured Control Center / App Library memory — 2026-10-13

The existing rootless companion was tested on iOS 16.7.16 using eight fresh SpringBoard launches (two per combination), each settled for 60 seconds then sampled three times with the screen off. SpringBoard mean physical footprints: both Off **62.31 MiB**, Control Center On only **62.44 MiB**, App Library On only **63.14 MiB**, both On **63.47 MiB**. Both Off was about **1.16 MiB lower** than both On. App Library accounted for most of this small idle difference; Control Center's tiny idle difference was too close to launch variation to promise as a dependable saving. Whole-phone free-memory counters did not establish a net RAM gain. All 24 idle intervals had Normal memory pressure, low CPU and verified current-PID applied hooks.

First-use checks in the same SpringBoard process showed about **+1.16 MiB** after opening/closing Control Center, then another **+2.97 MiB** after App Library categories/list plus keyboard. A separate fresh-launch category-only check without keyboard showed **+3.09 MiB**. Thus keyboard use was not required for the observed App Library-use growth; its exact contribution was not isolated. These figures include wake/Home/background work and are not guaranteed reclaimable RAM. Different runs must not be added together as a saving. Reclaim under pressure, long-term cache behavior and battery effects remain untested.

The hooks block Control Center presentation/gestures and App Library capability checks. They do not remove frameworks, explicitly release controller/cache objects, or prove that app categorization stops. A small measured SpringBoard difference is distinct from turning off the whole underlying implementation. SpringBoard/backboardd remain Critical and enabled. Both interface settings were restored Off; no daemon defaults or saved overrides were changed and no kernel reboot was performed. The app remains v1.0.26; this is a measurement/documentation update, not a new IPA.

Full method, per-launch/first-use data and restoration verification: `/mnt/zram1/ios/daemon_manager/audit/2026-10-13-ui-memory.md` and adjacent CSVs. Only the iOS repository is committed/pushed; these reference notes remain local. The daemon config needs no target changes for this interface-only experiment. Final current-PID verification confirmed both interfaces Off, the original preference bytes restored, and the native saved daemon map unchanged. The settled recovery sample measured SpringBoard 62.49 MiB and backboardd 23.78 MiB, low CPU and Normal pressure.

## Feature Groups navigation and logging controls — v1.0.26, 2026-10-13

The app tab is now **Groups**, with page title **Feature Groups**. Earlier versioned instructions referring to Overrides mean this renamed tab. The old word described saved launchd service overrides; Groups describes the feature-based controls. Internal/native override terminology remains valid for the launchd data, but is no longer the tab name.

Two entries were added: **Core logging** controls logd/logd_helper (default On); **Analytics and diagnostics** controls fifteen optional analytics/legacy-logging/crash/resource/diagnostic collectors (default Off). Collector On restores core logging prerequisites, while collector Off excludes them. Core logging Off deliberately saves both logger Off states, with the documented untested complete-profile/long-term limits. These jobs are outside the hot-unload allowlist; restart userspace to apply process changes. Adding the groups does not apply any settings or change existing targets/Critical policies.

The audit is **not finished for every possible opt-out**. Remaining isolated background/recovery checks include progressd, ndoagent, nand.aspcarry, full Location group Off and complete Core logging Off. None is newly declared safe merely because its control exists. Preserve the reproduced searchd/Photos CPU failure and all prior Critical protections. Config/builtins remain 512 entries, 315 Off / 197 On, 90 Critical enabled. No established groups are redundantly retested for this navigation edit.

Installed v1.0.26/build 27, app source `054b60aa13d4d772169286c6de2e7ebe40668517`, [Actions 38399012415](https://github.com/dusklinux/daemon_manager/actions/runs/38399012415): all 27 Python tests, Swift group/elevation/memory checks and iOS compile/package passed. IPA SHA256 `9d461137f704d99a47d47251430007f6ea7ff45810d031bac1cba7d4e6ccafc4` matched GitHub's digest and workstation/phone copies; embedded and installed source/version/build matched. TrollStore returned 0. The user confirmed Groups, Feature Groups, Core logging and Analytics and diagnostics visible. The phone's parsed saved override map exactly matched the pre-install snapshot. No group On/Off was applied or retested during this UI verification. Core logging/helper and searchd remain On, Siri remains Off, and no userspace/kernel reboot was needed for this installation. Only the iOS app repository is committed/pushed; these iOS references stay local and the Linux Git index is left untouched.

## Logging follow-up and DNS classification — 2026-10-13, v1.0.25

Most optional analytics/diagnostic collectors and legacy syslogd are already Off. The remaining core logd measured 0.098–0.121% of one core and about 7 MiB; logd_helper measured 0% and 1.63 MiB. These are process readings, not guaranteed free-RAM savings. Main logging was not disabled or globally reconfigured.

Only logd_helper was tested Off after a userspace restart. The user checked Photos/Camera, typing and WhatsApp, then turned off the screen (no passcode is enabled). More than five minutes of screen-off sampling found no sustained CPU loop and Normal memory pressure. The helper supports logd maintenance, including ownership/deletion operations; rotation/retention/cleanup over days or low storage remain unverified. Restore/keep it On for normal use; this short test does not establish a reliable benefit from removing it. It has been enabled/bootstrapped again. The sole difference versus the pre-logging saved map is its explicit false entry where no override previously existed. The original pre-audit backup remains intact.

Untested remaining opt-out candidates: progressd (ClassKit education progress, approximately 3.44 MiB), ndoagent (NewDeviceOutreach.framework, 2.77 MiB), nand.aspcarry (ASPCarryLog boot/daily NAND telemetry, 3.03 MiB), and the Location group (location functionality would be lost). These are candidates, not verified safe removals. Many other enabled jobs are not running and cannot yield an immediate resident-process saving. Do not label the optimization exhaustive or universally safe.

Known disabling-induced CPU/dependency failures remain Critical. v1.0.25 fixes a label-policy gap: actual com.apple.mDNSResponder.reloaded and com.apple.mDNSResponderHelper.reloaded now inherit the Critical policy already applied to their legacy DNS labels. No new DNS Off experiment or live override change was made. Config/builtins retain **512 entries, 315 Off / 197 On**; **90 Critical enabled entries** now include these two labels. Prior 88-entry counts below are historical. Candidate/logging descriptions were corrected from installed paths/endpoints.

The app report is `audit/2026-10-13-logging.md` in `/mnt/zram1/ios/daemon_manager`. Core logd and its helper are On; searchd is On and Siri remains Off. Raw diagnostic/resource traces remain private. No Linux commit/push or kernel/full reboot was performed. Installed v1.0.25/build 26, app source `235b5b2125db638eca961209c69b24f22e9395c8`, [Actions 38397364679](https://github.com/dusklinux/daemon_manager/actions/runs/38397364679): all 27 Python tests, Swift policy/elevation/memory checks and iOS compile/package passed. IPA SHA256 `de7960adbf106d49d59f6bf053e1faf7c499c1d0c65c871a915ba14981ecef6e` matched GitHub's digest and workstation/phone copies; embedded and installed source/version/build matched. TrollStore installation and the remote launch request both returned 0. The remote request did not leave an app process while the screen was off; after waking/unlocking, the user directly confirmed v1.0.25 opens normally. Post-install comparison with the pre-logging backup differs only by logd_helper's explicit enabled false entry where no override previously existed. Core logging/helper and searchd remain On; Siri stays Off. No new Critical UI interaction or DNS Off experiment was required; the exact-label policy is covered by the Swift checks.

## Final Spotlight locked-idle result — 2026-10-13

This finding supersedes the early short-sample result below. After the user locked the phone, searchd Off caused **assetsd at 105.633%, 105.581%, 105.592% of one core** over three intervals, while backboardd was only 0.55–0.62%. A 5-second symbolicated sample independently recorded 5.280 CPU seconds, with Photos syndication/background SQL work and CoreSpotlight query-failure callbacks. Only searchd was then enabled and bootstrapped; **the same assetsd PID 5006 fell to 0.009%, 0%, 0%** over three locked intervals. Backboardd stayed near 0.5%, and pressure stayed Normal. This is an isolated Off → delayed failure → On recovery, not merely the older broad-unload inference.

**Keep searchd On for this Photos profile.** The separate Spotlight and search group remains available for deliberate opt-out, but Off can increase CPU and battery use. It does not remove Spotlight's interface. Siri remains Off; no full reboot was performed. Final new disabled jobs are siriactionsd and ShortcutsUI only; searchd now has an explicit enabled (`false`) override where the starting backup had no entry. Its six optional group peers retain their earlier Off states. sysdiagnose and sysdiagnose_helper were restored Off/unregistered after collection.

v1.0.24 corrects the group/critical-service warning to describe the delayed CPU failure and recovery. The builtin/config targets remain **512 entries, 315 Off / 197 On, 88 Critical enabled**. v1.0.23's installed-group visibility passed, but its short-sample warning is superseded. Installed v1.0.24/build 25, app source `06f04adffd588afca8c47751e1026246921668ca`, [Actions 38394070883](https://github.com/dusklinux/daemon_manager/actions/runs/38394070883): all 27 Python tests, Swift checks, iOS compile/package and release succeeded. IPA SHA256 `f7d2680bf6f6cc4615badcdf26648947ecd25c102296f1f897e36813abb34d16` matched GitHub's digest and both workstation/phone copies; embedded and installed source/version/build matched. TrollStore returned 0 and launching returned 0. Post-install overrides retained searchd enabled, siriactionsd/ShortcutsUI disabled and both diagnostic jobs Off. The v1.0.23 group visibility had already been user-confirmed; v1.0.24 changes the warning text without changing group labels/action policy. No further Off experiment is needed for this reproduced failure. No net RAM or measured battery-life gain is claimed. Preserve this result in future handoffs; do not repeat short foreground checks and conclude searchd is safe Off.

## Current verified audit — 2026-10-13, v1.0.23

This section supersedes older current-state summaries below; dated results remain historical. The app repository is `/mnt/zram1/ios/daemon_manager`, remote `dusklinux/daemon_manager`. Only that repository is committed/pushed; these iOS references remain local. The starting phone already had 315 disabled overrides, not the older reset-era profile. System and mobile maps expose the same overrides and must not be counted twice.

Native hardware: iPhone10,2 / D21AP, iOS 16.7.16 / 20H392, rootless `/var/jb`, Python 3.9.9, `hw.memsize=3118219264` bytes (about 2.90 GiB usable, nominal 3 GB). Preserve the jailbreak with userspace restarts only; never perform a kernel/full reboot. Keep the established groups' prior tests; do not repeat them unnecessarily.

Installed v1.0.22/build 23, source `6f087de188055fbb9795c26afe9b6c8cbbdce01e`, Actions `38391072310`: 27 Python tests, Swift checks and iOS build/package passed. Release IPA SHA256 `7e4a73bed2a92ff076677d11e67895e207c5a86770378c3dc90f43226eef38c6` matched GitHub's digest and embedded source/version. TrollStore returned 0. The user confirmed seven new groups visible and used Siri Off → Restart Userspace. Native comparison with the preserved starting backup shows only siriactionsd and ShortcutsUI newly disabled.

Separate groups now cover Bluetooth, Calls/SMS, Wi-Fi, Keyboard, Safari services, Location, and Siri/dictation. The essential groups remain On by default; deliberate Off includes explicitly listed Critical feature jobs while shared prerequisite-only jobs remain enabled. Existing group policies are unchanged. These new essential groups' complete Off/restart/recovery cycles are unverified. Safari service Off does not stop all Safari/WebKit app processes; Wi-Fi/Bluetooth service Off does not establish hardware power-off.

The isolated siriactionsd Off/userspace-restart test passed user checks of Home/unlock, typing, WhatsApp and Camera/photo viewing without reproducing the historical sustained CPU warning. It is the shared VoiceShortcuts/automation broker; Shortcuts may fail while Off. Siriactionsd and ShortcutsUI now default Off. The matching config/builtins contain **512 targets: 315 Off / 197 On, 88 Critical enabled**. Actual messaging credentials/attachment jobs and local Safari history/bookmarks default On. Installation does not apply defaults or override explicit custom choices.

Backboardd: interaction 11.6% of one core, screen-on 8.3%, then user-locked sample 0.49% with the same PID; independent locked intervals were 0.497%, 0.517%, 0.483%. This supports display/input activity rather than a persistent locked-idle loop in those samples. Pressure was Normal; no net RAM/battery improvement or overnight stability is established. Process footprints must not be summed as guaranteed free-RAM gains. Native libproc CPU counters require the measured 125/3 ns Mach timebase; ps resource values were unreliable.

Installed v1.0.23/build 24 adds the separate **Spotlight and search** group: searchd, corespotlightservice, spotlight.IndexAgent, spotlightknowledged, parsecd, parsec-fbf and knowledgeconstructiond. Explicit group Off includes Critical searchd while shared keyboard/Biome/scheduling/extension services stay enabled. App source `7b6dc419c8eae3dcff1ffdeb7f839c3f5037c904`, Actions `38392869308` passed all Python/Swift/build/package checks; IPA SHA256 `beb72e01f3e03eede7d68e198814ea4c9f740d9de31d40f14986dd9485b84700` matched release/local/phone copies and embedded metadata. TrollStore returned 0 and the user confirmed the group visible. Installing does not apply defaults.

Only searchd was newly disabled beyond the verified Siri-Off state, followed by a userspace restart. It was unregistered; all six other search-group jobs were already Off. The user reported normal Photos/Camera and attempted search behavior. Short native samples did not reproduce the old sustained one-core Photos retry loop; assetsd interaction CPU stayed below 0.7% and pressure stayed Normal. Spotlight's app/interface still launched with searchd absent. Backend logs nevertheless recorded four failed Photos index donations, 15 Files Spotlight query failures and eight associated datasource errors. This is a deliberate indexed-search opt-out, not full interface removal or proof every search result works. A matching On comparison did not isolate each error from the prior optional-service profile. searchd stays builtin On/Critical; broad long-term compatibility is unverified.

`sysdiagnose -f /var/tmp/ -n` completed after temporarily enabling/bootstrap-loading sysdiagnose and sysdiagnose_helper; both were restored Off/unregistered afterward. Final saved overrides differ from the starting backup only by siriactionsd, ShortcutsUI and searchd. The 451 MB diagnostic directory and decoded logs remain private, outside Git. The Mandiant UnifiedLogs v0.7.0 parser decoded 7,111,587 entries but could not resolve some strings; several diagnostic tasks failed/timed out and private messages remain redacted, so complete clean-log coverage is not claimed. An earlier PerfPowerServicesSignpostReader report showed 75% of one core over two minutes before this Spotlight experiment. SpringBoard bitmap errors existed before searchd Off too. PullOverPro/Zetsu duplicate-class runtime warnings were observed; neither tweak was changed and causal performance effects remain unproven.

The app audit and all-target CSV live at `audit/2026-10-13.md` and `audit/services-2026-10-13.csv` in the app repository. All 37 original tracked files were byte-read and reviewed; 509 installed plists export 507 unique labels. Exhaustive service combinations were not empirically tested. Shared pkd, Biome, context/scheduling, Photos/export, authentication, charging and SSH dependencies stay protected. Root command forwarding, stale state and overlapping operations were corrected and tested. Raw evidence remains private under `/mnt/zram1/ios/audit-private`; the phone's original override backup is `/var/mobile/Documents/daemon-audit-2026-10-13/disabled.before.plist`.

## Current USB update — v1.0.21, 2026-10-13

The app source now lives at `/home/dusk/daemon_manager`. The preset remains 512 targets, 318 Off / 194 On; it is not a snapshot of the phone after the user's full override reset. The previous reduced-service profile and its dated test results below are historical.

Overrides adds USB and wired accessories: afcd, mobile.house_arrest, both mobile notification proxies, accessoryd, iapd, iap2d, iapauthd and usb.networking.addNetworkInterface. On also restores Critical mobile.lockdown and OpenSSH; Off preserves those shared prerequisites. Restart userspace after changes. These are related host features, not a proven minimum set for USB SSH.

The original workstation usbmuxd required missing libplist-2.0.so.4; a temporary source build linked to the installed .12 and restored the old connection. The same source revision and build flags were recreated today, but still detected 0 devices until the user reset phone overrides and restarted userspace. USB SSH then succeeded. Lockdownd was already running before that reset; its 344-entry disabled.backup was preserved locally for comparison. No single-service cause is proven. Disabling all nine optional group jobs and restarting userspace still passed USB SSH and paired ProductType/ProductVersion queries; the jobs were unloaded while lockdownd/OpenSSH remained loaded. No new Critical classification is justified by the broad reset.

Installed v1.0.21/build 22: source 7e00c5a03d7b22955a952cde7343bad7b92d3c4f, Actions 38381048707 passed 23 Python tests, Swift checks and iOS build/package. IPA SHA256 abb7db08b10703d0b7e4e5d96f897507152ece02b579b3615095b5fbaaac345b matched release digest/source/version. TrollStore returned 0. Installed menu-button verification passed in both directions. Backup/restore, wired audio, accessory coverage and RAM/battery gains remain unverified. See the app README for the exact recovered Linux build, foreground daemon, iproxy and SSH commands.


Installed-menu validation completed: On → userspace restart loaded all nine jobs, preserved lockdownd/OpenSSH and passed authenticated USB SSH, paired ProductType queries and BatteryIsCharging=true. Menu Off → restart then unloaded all nine, with the same SSH/paired queries and charging still working. The user also confirmed charging in the Off state. Final system and user/501 maps differ from the post-reset baseline only by those nine optional Off entries. Other reset-era service choices were left unchanged. Group Off is the final tested state; builtin targets/config were not changed. These checks do not test every accessory or backup protocol and do not identify the original missing-enumeration cause.

## Previous verified profile — v1.0.20, 2026-10-12

The matching app/config preset contains **512 targets: 318 Off / 194 On** (105 `no`, 89 Critical `NO`). The config is a builtin target list, not a snapshot of every manually customized phone setting. Installing an update does not apply defaults. Versioned test sections below retain their historical counts, classifications and pending checks; this summary and the latest feature results supersede older statements of current behavior.

The user identifies the current A11 jailbreak as **palera1n**, with no device passcode enabled. Earlier notes recorded Dopamine; that attribution is not the current setup. Preserve the existing passcode/jailbreak configuration. The tested runtime is iOS 16.7.16 with rootless `/var/jb` and Python 3.9.9. The bootstrap account home `/var/jb/var/mobile` differs from native iOS `/var/mobile`; SpringBoard preferences and its runtime report use the native path.

Keep repaired Critical dependencies enabled: pkd, nehelper (and its filename alias), searchd, biomed, syncdefaultsd, mobile.lockdown, appleaccountd/akd/adid, absd, imagent, identityservicesd, cdpd, followupd, both Photos conversion services, passd, ProtectedCloudKeySyncing, SecureBackupDaemon, contextstored and dasd, alongside the existing core Critical services. Uppercase `NO` records that classification. Pkd's Off → CPU loop → On recovery was reproduced; searchd's individual CPU causality was not isolated. Some account dependencies were restored as a set, not individually isolated.

Overrides has feature subpages and menu On/Off buttons. On enables listed jobs and known prerequisites; Off preserves Critical and dependency-only shared jobs. Individual Critical switches retain warnings. Shared labels in different groups refer to the same native override. Catalog switches load/unload their jobs; broader batches require userspace restart. Tools contains Import / Export Config. Descriptions remain visible without expanding rows; Search, available MiB and theme share one row without pinned category banners.

| Feature | Verified behavior / normal phone state |
| --- | --- |
| Screen recording | ReplayKitAngel + replayd; enable both and respring. Both group directions passed; normally Off. Camera videos use a separate path. |
| AirDrop / Wallet / accessibility | AirDrop policy and UI host Off; sharingd stays On. Wallet UI Off but Critical passd On for tested App Store confirmation. AccessibilityUIServer is optional with no Recommended On badge; builtin On, manually Off on this phone. |
| Accounts / sharing / store | Sign-in and SMS, Photos image/video WhatsApp export and two free-app downloads passed after the recorded repairs. FaceTime calls and paid purchases/Apple Pay remain untested; WhatsApp Meta AI shortcut remains unresolved, while Set Flashlight works. |
| iCloud Drive / Photos | Fresh-restart Drive export and Photos upload were confirmed on the web. Sync jobs bird/cloudd/cloudphotod/assetsd.nebulad returned Off by request; repaired Critical account/key/context/scheduler jobs stay On. |
| Lock Screen media controls | PosterBoard + liveactivitiesd On and userspace restart restored clear playback controls; PaperBoard was not required. Both group directions passed. All three hosts default Off and are normally Off; PosterBoard is shared with Wallpaper. |
| Widgets | chronod updates can be stopped; keep pkd On. Today View remains accessible because the attempted page-removal hooks failed and were removed. |
| SpringBoard interfaces | Control Center and App Library access switches passed On/Off after resprings in v1.0.20 and are saved Off. Settings are separate from daemon.cfg. Dock/status-bar hiding switches and hooks were removed because they only hid views; both interfaces are restored. RAM savings remain unverified. |
| Memory stress | Memory provides +100 MiB, −100 MiB and Clear, with fresh varied random data, allocation/footprint/headroom readings and cleanup on panel close. User checks passed allocation, minus, Clear and close/reopen cleanup. It consumes real memory for stress testing; it does not increase physical RAM, guarantee cache eviction or disable jetsam. |

The installed v1.0.20/build 21 source is `ca663064295eb14b72a669ececcfcf37eeda7277`; the later app documentation commit does not change that build. CI passed 23 Python tests, Swift checks and iOS packaging. Both saved launchd domains matched the pre-interface profile after testing. Short settled CPU samples and feature checks do not prove net RAM/battery gains, every service combination or full-system stress reliability.

## Current preset reference and historical test record

The table below follows the current builtin override for matching targets. It is a readable role index, not proof that an Off choice preserves that feature. The exact complete target list, including aliases, is [daemon.cfg](scripts/daemons/daemon.cfg). `yes` = disabled, `no` = enabled, `NO` = Critical enabled, `N/A` = not in the builtin list. Dated test records after the table retain their historical observations.

| #   | daemon                        | description                                                                                  | default_override |
| --- | ----------------------------- | -------------------------------------------------------------------------------------------- | ------------------------------ |
| 1   | ABDatabaseDoctor               | Contacts database repair helper                                                              | yes                            |
| 2   | abm-helper                     | Apple Business Manager                                                                       | yes                            |
| 3   | absd                           | Absinthe/NAC authentication dependency; restoring it removed -44303 initialization errors | no |
| 4   | accessibility.axassetsd        | Accessibility asset downloads, such as voices                                                | yes                      |
| 5   | accessibility.axremoted        | Remote accessibility services                                                                | yes                      |
| 6   | accessibility.heard            | HearingCore service for hearing features, including background sounds and sound-detection notifications. Disabling can break hearing features/accessories even with Bluetooth enabled.                                             | yes                             |
| 7   | accessibility.motiontrackingd  | Accessibility motion/head-tracking support                                                   | yes                      |
| 8   | accessoryupdaterd              | Firmware updates for Apple/supported accessories                                             | yes                      |
| 9   | accountsd                      | System accounts database and access management                                               | no                             |
| 10  | activityawardsd                | Fitness/Activity awards processing                                                           | yes                      |
| 11  | adid                           | apple id account login                                                                       | no                             |
| 12  | AdminLite                      | system watchdog daemon                                                                       | NO                             |
| 13  | afcd                           | Apple File Conduit for USB file access and Finder/iTunes file sharing                        | yes                            |
| 14  | aggregated                     | System analytics aggregation                                                                 | yes                            |
| 15  | aggregated.addaily             | Advertising-related daily aggregation task                                                   | yes                            |
| 16  | akd                            | apple id account login                                                                       | no                             |
| 17  | amsaccountsd                   | Apple Media Services account handling                                                        | yes                             |
| 18 | amsengagementd | Apple Media Services UI engagement/cache/link endpoints; second new free-app download passed with this job off | yes |
| 19  | analyticsd                     | System analytics and diagnostics collection                                                  | yes                            |
| 20  | and                            | Private Apple daemon, unclear public role                                                    | yes                             |
| 21  | announced                      | Announce Notifications / Announce Messages support                                           | yes                      |
| 22  | anomalydetectiond              | celerometer and gyroscope data for Crash Detection and Fall Detection                        | yes                            |
| 23  | ap.adprivacyd                  | Advertising privacy policy enforcement                                                       | yes                            |
| 24  | ap.promotedcontentd            | Promoted content and App Store recommendations/ads                                           | yes                            |
| 25  | appleaccountd                  | Apple Account management and sync                                                            | no                             |
| 26  | applecamerad                   | Core camera hardware interface                                                               | no                             |
| 27  | AppleCredentialManagerDaemon   | Apple credential management service                                                          | yes                             |
| 28  | AppSSODaemon                   | Single Sign-On service, mainly enterprise                                                    | yes                      |
| 29  | appstorecomponentsd            | App Store support components                                                                 | yes                             |
| 30  | appstored                      | Core App Store download/update service                                                       | yes                             |
| 31  | apsd                           | Apple Push Notification service daemon                                                       | no                             |
| 32  | arkitd                         | ARKit service for augmented-reality apps                                                     | yes                      |
| 33  | asd                            | Private Apple daemon; public role not clearly documented                                     | yes                             |
| 34  | askpermissiond                 | Family Sharing Ask to Buy approvals                                                          | yes                      |
| 35  | AssetCacheLocatorService       | Locates Apple content-cache servers. Disabling can bypass local content-cache discovery; ordinary network access was preserved in the tested batch.                                             | yes                            |
| 36  | assetsd                        | Photos asset library , disabling disables photos and screenshots vieweing in photos          | no                             |
| 37  | assetsd.nebulad                | Photos/iCloud Photos asset sync helper                                                       | yes                      |
| 38  | assistant_cdmd                 | Siri assistant data management                                                               | yes                      |
| 39  | atc                            | Legacy Finder/iTunes sync helper                                                             | yes                            |
| 40  | AuthenticationServicesCore     | AuthenticationServices XPC service for sign-in/passkey flows                                 | no                             |
| 41  | avatarsd                       | Memoji/avatar rendering and sync                                                             | yes                      |
| 42  | avconferenced                  | AVConference backend. With it disabled, callservicesd repeatedly logged invalid-connection errors. Enabling stopped the errors, disabling reproduced them, and enabling again stopped them on iOS 16.7.16. Keep On to avoid these background retries; this is not a demonstrated bootloop or high-CPU failure.                                                             | no                      |
| 43  | backboardd                     | Core iOS input, display, and event-routing service                                           | NO                             |
| 44  | backgroundassets.user          | Background asset downloads for apps/system content                                           | yes                             |
| 45  | BarcodeSupport                 | Barcode support service                                                                      | yes                      |
| 46  | batteryintelligenced           | Battery intelligence and optimized charging support                                          | yes                            |
| 47  | betaenrollmentd                | Beta enrollment management                                                                   | yes                            |
| 48 | biomed | Biome stream access; disabled state produced SpringBoard/keyboard metadata and lockfile failures | no |
| 49  | biomesyncd                     | Biome sync service                                                                           | yes                             |
| 50  | biometrickitd.mesa             | Biometric hardware interface for Touch ID / Face ID                                          | no                             |
| 51  | bird                           | iCloud Drive / CloudDocs sync daemon                                                         | yes                      |
| 52  | BlueTool                       | Bluetooth controller bring-up and management                                                 | NO |
| 53  | bluetoothd                     | Core Bluetooth stack daemon                                                                  | NO |
| 54  | bluetoothuiservice             | Bluetooth pairing/UI support                                                                 | no                      |
| 55  | bluetoothuserd                 | User-level Bluetooth support                                                                 | no                      |
| 56  | bookassetd                     | Apple Books asset management                                                                 | yes                      |
| 57  | bookdatastored                 | Apple Books data storage and sync                                                            | yes                      |
| 58  | bootpd                         | BOOTP/DHCP server used for tethering/network sharing. Disabling can break Personal Hotspot or network sharing; it is separate from the ordinary Wi-Fi DHCP client.                                      | yes                             |
| 59  | BTServer.avrcp                 | Bluetooth media control profile support, Prevents pause/play on the headphones if disabled   | no                             |
| 60  | BTServer.le                    | Bluetooth Low Energy support, saves power                                                    | no                             |
| 61  | BTServer.map                   | Bluetooth Message Access Profile support                                                     | yes                      |
| 62  | BTServer.pbap                  | Bluetooth Phone Book Access Profile support                                                  | yes                      |
| 63  | businessservicesd              | Apple business-related services; public role unclear                                         | yes                             |
| 64  | cache_delete                   | Keep enabled for storage cache reclamation, including low-space handling. This is maintenance rather than optional usage analytics. No Off test was performed.                                                      | no                             |
| 65  | calaccessd                     | Calendar database access control                                                             | yes                             |
| 66  | CallHistorySyncHelper          | iCloud sync helper for call history                                                          | yes                      |
| 67  | captiveagent                   | Captive portal detection for public Wi‑Fi                                                    | yes                      |
| 68  | carkitd                        | CarPlay core daemon                                                                          | yes                      |
| 69  | CarPlayApp                     | CarPlay UI process                                                                           | yes                      |
| 70  | cdpd                           | Core Data Protection; member of tested account sign-in repair set, individual causality unisolated | no |
| 71  | certui.relay                   | Certificate trust prompt relay                                                               | yes                             |
| 72  | cfnetwork.AuthBrokerAgent      | Network authentication broker                                                                | NO                             |
| 73  | cfnetwork.cfnetworkagent       | Core networking agent                                                                        | NO                             |
| 74  | cfprefsd.xpc.daemon            | CoreFoundation preferences daemon                                                            | NO                             |
| 75  | cfprefsd.xpc.daemon.system     | System-level preferences daemon                                                              | NO                             |
| 76  | checkerboardd                  | Recovery/restore or diagnostic UI service                                                    | yes                             |
| 77  | chronod                        | Widget timelines and scheduled widget updates                                                | yes                      |
| 78  | ckdiscretionaryd               | CloudKit background transfer service                                                         | yes                      |
| 79  | ClarityBoard                   | Assistive Access / simplified accessibility UI                                               | yes                      |
| 80  | ClipServices.clipserviced      | App Clips management service                                                                 | yes                      |
| 81  | cloudd                         | CloudKit syncing daemon                                                                      | yes                      |
| 82  | cloudpaird                     | iCloud-based pairing info sync                                                               | yes                      |
| 83  | cloudphotod                    | iCloud Photos background sync                                                                | yes                      |
| 84  | CloudSettingsSyncAgent         | iCloud settings sync agent                                                                   | yes                      |
| 85  | cmfsyncagent                   | CommunicationsFilter synchronization agent with key-value-store and external-store-change triggers. Disabling loses this synchronization; complete blocked-contact synchronization was not tested.                                                | yes                             |
| 86  | commandandcontrol              | Voice Control accessibility daemon                                                           | yes                      |
| 87  | CommCenter                     | Core cellular/telephony; shared SpringBoard prerequisite. Off caused repeated CoreTelephony connection failures. | no                             |
| 88  | CommCenter-noncellular         | Network-service variant for non-cellular devices                                             | no                             |
| 89  | CommCenterMobileHelper         | Cellular helper for carrier/config tasks                                                     | no                             |
| 90  | CommCenterRootHelper           | Privileged cellular configuration helper                                                     | no                             |
| 91  | companion_proxy                | Apple Watch companion proxy service                                                          | yes                      |
| 92  | companionauthd                 | Apple Watch authentication and pairing support                                               | yes                      |
| 93  | configd                        | System Configuration and network configuration                                               | NO                             |
| 94  | contacts.donation-agent        | Contacts suggestions donation agent                                                          | yes                            |
| 95  | contactsd                      | Contacts database daemon                                                                     | no                             |
| 96  | containermanagerd              | App sandbox and container manager                                                            | NO                             |
| 97  | containermanagerd.system       | System-level container manager                                                               | NO                             |
| 98  | contextstored                  | CoreDuet context store for scheduling; Off reproduced Photos upload rejection from failed context reads, zero Wi-Fi quality and inadequate data budget | no |
| 99  | coordinated                    | File/document coordination service                                                           | no                             |
| 100 | CoreAuthentication.daemon      | Local authentication service for biometric/passcode prompts                                  | NO                             |
| 101 | corecaptured                   | Low-level diagnostic capture service                                                         | yes                            |
| 102 | coredatad                      | Core Data support daemon used by system apps/frameworks                                      | no                             |
| 103 | coreduetd                      | On-device usage learning and prediction service                                              | yes                             |
| 104 | coreidvd                       | Digital ID / Wallet identity verification support                                            | yes                      |
| 105 | corerepaird                    | Repair status / parts validation service                                                     | yes                      |
| 106 | coreservices.useractivityd     | NSUserActivity and Handoff support                                                           | yes                      |
| 107 | corespeechd                    | CoreSpeech capture/recognition services, including the local speech recognition bridge. Keyboard dictation on the tested phone required this together with assistantd; either service alone failed. It also serves Siri speech features.                                         | yes                      |
| 108 | corespotlightservice           | Core Spotlight indexing/search support                                                       | yes                            |
| 109 | countryd                       | Country/region determination service                                                         | no                             |
| 110 | ctkd                           | CryptoTokenKit daemon for smart cards/token credentials                                      | yes                      |
| 111 | dasd                           | Duet Activity Scheduler for background tasks                                                 | NO                             |
| 112 | dataaccess.dataaccessd         | Exchange, CalDAV, and CardDAV sync service                                                   | yes                      |
| 113 | DataDetectorsSourceAccess      | Data Detectors source-access helper. Disabling may lose detected-content actions/source access; basic-use batch checks passed.                                                         | yes                             |
| 114 | datastored                     | Private Apple data-storage service, unclear public role                                      | no                             |
| 115 | deferredmediad                 | Deferred camera/photo processing service                                                     | no                             |
| 116 | deleted_helper                 | Keep enabled for the privileged cache-deletion operations used by storage maintenance. No Off test was performed.                                                | no                             |
| 117 | DesktopServicesHelper          | CoreServices file/metadata helper                                                            | yes                             |
| 118 | device-o-matic                 | Private device/hardware helper service                                                       | yes                             |
| 119 | deviceaccessd                  | Device-access / permission-mediation service                                                 | no                             |
| 120 | devicecheckd                   | DeviceCheck / App Attest support service                                                     | no                             |
| 121 | devicedataresetd              | Private Apple daemon, unclear public role                                                    | no                             |
| 122 | devicemanagementclient.mdmd    | Managed device-management client component                                                   | yes                      |
| 123 | devicemanagementclient.teslad  | Managed enrollment / device-management helper                                                | yes                      |
| 124 | dhcp6d                         | DHCPv6 client daemon                                                                         | NO                             |
| 125 | diagnosticd                    | System diagnostics processing                                                                | yes                            |
| 126 | diagnosticextensionsd          | Diagnostic extension collection                                                              | yes                            |
| 127 | dietapplecamerad               | Lightweight camera service used by specific pipelines                                        | no                             |
| 128 | dietappleh13camerad            | Hardware-specific lightweight camera daemon                                                  | no                             |
| 129 | diskarbitrationd               | Keep enabled for disk and mount arbitration. Disk-management infrastructure is outside the optional-service batch; no Off test was performed.                                                        | no                             |
| 130 | diskimagesiod                  | Disk image mount support service                                                             | NO                             |
| 131 | diskimagesiod.ram              | RAM disk image support service                                                               | yes                            |
| 132 | distnoted.xpc.daemon           | Distributed notifications service                                                            | NO                             |
| 133 | dmd                            | Declarative Device Management daemon                                                         | yes                      |
| 134 | DMHelper                       | Device-management helper                                                                     | yes                      |
| 135 | donotdisturbd                  | Do Not Disturb / Focus management                                                            | no                             |
| 136 | dprivacyd                      | Differential Privacy telemetry service                                                       | yes                            |
| 137 | DragUI.druid                   | Drag-and-drop UI broker exporting source, destination and paste-announcement services. Disabling intentionally loses those UI paths; ordinary Home, typing and media checks passed.                                                                     | yes                             |
| 138 | driverkitd                     | DriverKit user-space driver host service                                                     | no                             |
| 139 | dt.automationmode-writer       | Developer Automation Mode support task                                                       | yes                            |
| 140 | dt.AutomationModeUI            | Developer Automation Mode UI service                                                         | yes                            |
| 141 | dt.fetchsymbolsd               | Developer symbol fetch service                                                               | yes                            |
| 142 | dt.previewsd                   | Developer preview support service                                                            | yes                            |
| 143 | dt.remotepairingdeviced        | Developer wireless pairing daemon                                                            | yes                            |
| 144 | duetexpertd                    | Duet/Siri suggestions expert service                                                         | yes                      |
| 145 | email.maild                    | Apple Mail background sync/fetch                                                             | yes                      |
| 146 | EscrowSecurityAlert            | Security escrow alert service                                                                | yes                            |
| 147 | exchangesyncd                  | Microsoft Exchange sync daemon                                                               | yes                      |
| 148 | facemetricsd                   | Face ID metrics / attention support                                                          | yes                             |
| 149 | factory.NFQRCoded              | Factory/diagnostic QR or NFC code support                                                    | N/A                            |
| 150 | fairplayd.H2                   | FairPlay DRM daemon, apps installed from the app store dont launch if disabled               | NO                             |
| 151 | fairplaydeviceidentityd        | FairPlay device identity support, apps installed from the app store dont launch if disabled  | NO                             |
| 152 | familycircled                  | Family Sharing management daemon                                                             | yes                      |
| 153 | FamilyControlsAgent            | Family Controls / Screen Time family enforcement                                             | yes                      |
| 154 | familynotificationd            | Family Sharing notifications daemon                                                          | yes                      |
| 155 | fdrhelper                      | Factory restore / calibration key helper                                                     | no                             |
| 156 | FileCoordination               | File coordination service                                                                    | no                             |
| 157 | FileProvider                   | Files app and third-party file-provider support                                              | no                             |
| 158 | filesystems.apfs_iosd          | APFS filesystem management daemon                                                            | NO                             |
| 159 | filesystems.livefileproviderd  | Live File Provider filesystem support                                                        | no                             |
| 160 | filesystems.smbclientd         | SMB client daemon for network shares                                                         | yes                      |
| 161 | filesystems.userfs_helper      | User-space filesystem helper                                                                 | no                             |
| 162 | filesystems.userfsd            | User-space filesystem daemon                                                                 | no                             |
| 163 | financed                       | Apple financial services support                                                             | yes                      |
| 164 | fitcore                        | Fitness framework core service                                                               | yes                      |
| 165 | fitcore.session                | Fitness active-session support service                                                       | yes                      |
| 166 | fitnesscoachingd               | Fitness coaching notifications                                                               | yes                      |
| 167 | followupd                      | CoreFollowUp account requests; member of tested repair set, individual causality unisolated | no |
| 168 | fontservicesd                  | System font management and font downloads                                                    | no                             |
| 169 | fs_task_scheduler              | Keep enabled for filesystem maintenance scheduling; disabling maintenance is not a measured memory optimization. No isolated Off test was performed.                                                             | no                             |
| 170 | fseventsd                      | Filesystem events daemon                                                                     | NO                             |
| 171 | ftp-proxy-embedded             | Legacy FTP proxy service                                                                     | yes                            |
| 172 | fullkeyboardaccess             | Accessibility Full Keyboard Access support                                                   | yes                      |
| 173 | fusiond                        | Private Apple daemon, unclear public role                                                    | no                             |
| 174 | GameController.gamecontrollerd | Game controller framework daemon                                                             | yes                      |
| 175 | geod                           | GeoServices daemon for Maps/geocoding                                                        | yes                             |
| 176 | gpsd                           | GPS hardware daemon                                                                          | yes                             |
| 177 | griddatad                      | Private Apple grid/layout data service                                                       | yes                             |
| 178 | GSSCred                        | Kerberos credential daemon                                                                   | yes                      |
| 179 | handwritingd                   | Handwriting recognition service                                                              | yes                      |
| 180 | hangreporter                   | Reports app/system hangs                                                                     | yes                            |
| 181 | hangtracerd                    | Collects traces for app/system hangs                                                         | yes                            |
| 182 | healthappd                     | Health app support daemon                                                                    | yes                      |
| 183 | healthrecordsd                 | Health Records sync/storage service                                                          | yes                      |
| 184 | icloud.findmydeviced           | Find My device support daemon                                                                | yes                      |
| 185 | icloud.fmfd                    | Find My People support daemon                                                                | yes                      |
| 186 | icloud.fmflocatord             | Find My location reporting daemon                                                            | yes                      |
| 187 | icloud.searchpartyd            | Find My network / AirTag crowd-location daemon                                               | yes                      |
| 188 | icloudsubscriptionoptimizerd   | iCloud subscription/storage upsell service                                                   | yes                            |
| 189 | iconservices.iconservicesagent | App icon rendering and cache service                                                         | no                             |
| 190 | idamd                          | Private Apple identity/auth daemon                                                           | yes                             |
| 191 | idcredd                        | Identity credential service for digital IDs                                                  | yes                      |
| 192 | identityservicesd              | Identity Services; member of tested account repair set and earlier nehelper dependency recovery | no |
| 193 | idscredentialsagent            | Identity Services credential agent                                                           | no                             |
| 194 | idsremoteurlconnectionagent    | Identity Services network agent                                                              | no                             |
| 195 | imagent                        | Messages backend including carrier SMS; restore plus respring recovered verification SMS | no |
| 196 | imautomatichistorydeletionagent | Automatic message-history deletion support                                                   | yes                            |
| 197 | imtransferagent                | Transfers iMessage attachments/media                                                         | no                             |
| 198 | inboxupdaterd                  | Inbox update helper; public role not well documented                                         | yes                             |
| 199 | ind                            | iCloudNotification helper exporting cloud-feature, notification and quota-related endpoints. Disabling can suppress iCloud notices and quota updates; ordinary call/Wi-Fi/Bluetooth checks passed in the tested batch.                                                    | yes                             |
| 200 | installcoordination_proxy      | Proxy helper for install coordination                                                        | no                             |
| 201 | installcoordinationd           | Coordinates app installs and updates                                                         | no                             |
| 202 | intelligenceplatformd          | On-device intelligence platform for Siri/predictions                                         | yes                      |
| 203 | IOAccelMemoryInfoCollector     | GPU/graphics memory diagnostics collector                                                    | yes                            |
| 204 | iomfb_bics_daemon              | Display subsystem calibration/framebuffer helper                                             | no                             |
| 205 | iomfb_fdr_loader               | Factory display calibration loader                                                           | no                             |
| 206 | iosdiagnosticsd                | Apple diagnostics/support daemon                                                             | yes                            |
| 207 | ioupsd                         | Private power-related daemon, unclear public role                                            | no                             |
| 208 | itunesstored                   | Store services daemon for media purchases/downloads                                          | yes                      |
| 209 | jetsamproperties.D21           | Memory-pressure and Jetsam configuration                                                     | NO                             |
| 210 | jetsamproperties.D211          | Hardware-specific Jetsam configuration                                                       | NO                             |
| 211 | keychainsharingmessagingd      | Keychain sharing / iCloud Keychain messaging support                                         | yes                      |
| 212 | knowledgeconstructiond         | Builds knowledge data for Siri/search                                                        | yes                            |
| 213 | languageassetd                 | Downloads language assets such as dictionaries/voices                                        | yes                      |
| 214 | linkd                          | Linking service for intents/deep links/app routing                                           | no                             |
| 215 | liveactivitiesd | Live Activities and tested Lock Screen/Notification Center playback panel; requires PosterBoard for clear rendering in this profile | yes |
| 216 | localizationswitcherd          | Localization/language-switch helper                                                          | yes                      |
| 217 | locationd                      | Core Location daemon                                                                         | no                             |
| 218 | locationpushd                  | Location-triggered push-notification support                                                 | yes                      |
| 219 | logd                           | Unified logging daemon                                                                       | no                             |
| 220 | logd_helper                    | Keep enabled for unified-log file ownership and deletion maintenance. Short Off tests passed basic use, but log rotation, retention and low-storage cleanup were not exercised; this is not merely an analytics collector.                                                                   | no                             |
| 221 | lsd                            | LaunchServices daemon for app registration/launching                                         | NO                             |
| 222 | lsd.system                     | System-level LaunchServices daemon                                                           | NO                             |
| 223 | lskdd                          | Private Apple daemon, unclear public role                                                    | NO                             |
| 224 | managedconfiguration.passcodenagd | Managed passcode compliance prompt service                                                   | yes                      |
| 225 | managedconfiguration.profiled | Native Auto-Lock/Low Power Mode and configuration-profile policy. Recommended On; disabling left a stale 30-second restriction and blocked Auto-Lock edits on iOS 16.7.16. Manual Off is available under Auto-Lock and configuration policy. | no |
| 226 | ManagedSettingsAgent           | Managed Settings / Screen Time enforcement                                                   | yes                      |
| 227 | maps.destinationd              | Maps destination suggestions service                                                         | yes                      |
| 228 | maps.geocorrectiond            | Maps location-correction / feedback daemon                                                   | yes                      |
| 229 | maps.pushdaemon                | Maps notification daemon                                                                     | yes                      |
| 230 | matd                           | WelcomeKit migration helper. Installed triggers include Setup Assistant completion and finalizing migratable apps. Disabling can affect migration/setup; it is not established as generic telemetry.                                                         | yes                             |
| 231 | mDNSResponder                  | Bonjour and Multicast DNS daemon                                                             | NO                             |
| 232 | mDNSResponderHelper            | Helper for Bonjour/mDNS networking                                                           | NO                             |
| 233 | mdt                            | Private Apple daemon, unclear public role                                                    | yes                             |
| 234 | mediaanalysisd                 | On-device media analysis for Photos                                                          | yes                            |
| 235 | mediaanalysisd.service         | XPC service for media analysis tasks                                                         | yes                            |
| 236 | mediaartworkd                  | Downloads/manages album art and related artwork                                              | yes                      |
| 237 | medialibraryd                  | Media library database daemon                                                                | yes                      |
| 238 | mediaparserd                   | Parses media files and containers                                                            | no                             |
| 239 | mediaremoted                   | Remote media-control daemon                                                                  | no                             |
| 240 | mediaserverd                   | Core audio/video playback service                                                            | no                             |
| 241 | mediasetupd                    | Media device setup support, such as HomePod/Apple TV                                         | yes                      |
| 242 | mediastream.mstreamd           | Shared Photo Streams / related media stream service                                          | yes                      |
| 243 | memory-maintenance             | Keep enabled for memory housekeeping. Its installed executable is mmaintenanced; memory and storage maintenance are outside the optional-service disable batch. No isolated Off test was performed.                                                            | no                             |
| 244 | merchantd                      | Apple Pay merchant-session / transaction support                                             | yes                      |
| 245 | metrickitd                     | MetricKit performance metrics collection                                                     | yes                            |
| 246 | misagent                       | Provisioning profile and signing support agent, disabling will cause self signed to not open | NO                             |
| 247 | mlmodelingd                    | Downloads/manages machine-learning models                                                    | yes                            |
| 248 | mlruntimed                     | Core ML runtime daemon                                                                       | no                             |
| 249 | mobile.assertion_agent         | Manages process assertions and background-execution claims                                   | NO                             |
| 250 | mobile.cache_delete_app_container_caches | Clears app container caches                                                                  | no                             |
| 251 | mobile.cache_delete_daily      | Daily cache-cleanup task                                                                     | no                             |
| 252 | mobile.cache_delete_mobile_backup | Cache cleanup related to device backups                                                      | no                            |
| 253 | mobile.heartbeat               | System heartbeat/watchdog-related service                                                    | yes                             |
| 254 | mobile.house_arrest            | USB file-sharing service for app containers                                                  | yes                            |
| 255 | mobile.insecure_notification_proxy | USB communication with a computer                                                            | yes                      |
| 256 | mobile.installd                | Core app installation daemon                                                                 | no                             |
| 257 | mobile.keybagd                 | Keybag and data-protection key management                                                    | NO                             |
| 258 | mobile.lockdown | USB pairing/trust and host communication; paired USB tools failed when off and worked after restoration | no |
| 259 | mobile.MCInstall               | Mobile configuration install helper                                                          | yes                      |
| 260 | mobile.notification_proxy      | Lockdown notification proxy for host communication                                           | yes                      |
| 261 | mobile.storage_mounter         | Mount service for developer/external storage images                                          | yes                      |
| 262 | mobile.storage_mounter_proxy   | Proxy for storage mount operations                                                           | yes                      |
| 263 | mobile.usermanagerd            | DEVICE WILL BOOT LOOP IF TURNED OFF                                                          | NO                             |
| 264 | mobile_installation_proxy      | Proxy service for app installation operations                                                | no                             |
| 265 | mobileactivationd              | Activation status and server-communication daemon                                            | NO                             |
| 266 | mobileassetd                   | Downloads Apple mobile assets such as trust/language files                                   | no                             |
| 267 | mobilebackup2                  | Finder/iTunes local backup service                                                           | yes                      |
| 268 | mobilecheckpoint.checkpointd   | Update/checkpoint validation daemon                                                          | no                             |
| 269 | MobileFileIntegrity            | AMFI code-signing enforcement                                                                | NO                             |
| 270 | mobilegestalt.xpc              | System capability and hardware-property query service                                        | NO                             |
| 271 | mobilerepaird                  | Parts pairing and repair-status service                                                      | yes                      |
| 272 | mobilestoredemod               | Retail demo mode daemon                                                                      | yes                            |
| 273 | mobilestoredemodhelper         | Retail demo mode helper                                                                      | yes                            |
| 274 | mobiletimerd                   | Clock alarms and timers support                                                              | no                             |
| 275 | momentsd                       | Photos Memories/moments curation                                                             | yes                      |
| 276 | MTLAssetUpgraderD              | Metal asset/shader upgrade service                                                           | no                             |
| 277 | mtmergeprops                   | Multi-touch calibration/property merge support                                               | no                             |
| 278 | nand.aspcarry                  | ASPCarryLog NAND telemetry task with boot-delayed and daily telemetry activities. Separate from NANDTaskScheduler, APFS and filesystem maintenance, which stay On. Daily collection behavior was not exercised.                                                      | yes                             |
| 279 | nand_task_scheduler            | Keep enabled for NAND/flash maintenance scheduling. This is NANDTaskScheduler, separate from the optional ASPCarryLog telemetry task. No isolated Off test was performed.                                                          | no                             |
| 280 | nanobackupd                    | Apple Watch backup daemon                                                                    | yes                      |
| 281 | nanoprefsyncd                  | Apple Watch preference-sync daemon                                                           | yes                      |
| 282 | nanoregistryd                  | CAUSES BOOTLOOP IF DISABLED                                                                  | NO                             |
| 283 | nanoregistrylaunchd            | Apple Watch registry launch helper                                                           | yes                      |
| 284 | nanotimekitcompaniond          | Apple Watch face/TimeKit companion sync                                                      | yes                      |
| 285 | naturallanguaged               | Natural Language framework daemon                                                            | yes                      |
| 286 | navd                           | Turn-by-turn navigation service for Maps                                                     | yes                      |
| 287 | ndoagent                       | NewDeviceOutreach helper for device outreach/prompts. Passed individual and batch userspace-restart/basic-use checks on iOS 16.7.16; upgrade, pairing and language-change paths were not exercised.                                                    | yes                             |
| 288 | neagent-ios                    | Network Extension agent for VPNs/filters                                                     | yes                      |
| 289 | nearbyd                        | Nearby interaction and proximity service                                                     | yes                      |
| 290 | nehelper-embedded | Network Extension helper service | NO |
| 291 | nesessionmanager               | DISABLING CAUSES MASSIVE POWER DRAW                                                          | NO                             |
| 292 | NetworkLinkConditioner         | Developer network-throttling tool (Guideline: yes)                                                            | yes                            |
| 293 | networkserviceproxy            | Network proxy service used by Private Relay and related features                             | yes                      |
| 294 | newsd                          | Apple News background service                                                                | yes                      |
| 295 | nexusd                         | Private Apple network-related daemon                                                         | no                             |
| 296 | nfcd                           | NFC hardware daemon                                                                          | yes                      |
| 297 | nfrestore                      | NFC firmware/restore support service                                                         | yes                      |
| 298 | notifyd                        | Darwin notification center daemon                                                            | NO                             |
| 299 | nsurlsessiond                  | Background transfer daemon for URLSession networking                                         | no                             |
| 300 | online-auth-agent.xpc          | Online authentication helper for sign-in/activation flows                                    | no                             |
| 301 | osanalytics.osanalyticshelper  | OS analytics formatting helper                                                               | yes                            |
| 302 | ospredictiond                  | System prediction daemon                                                                     | yes                            |
| 303 | parsec-fbf                     | Feedback/analytics service for Siri Search/Spotlight                                         | yes                            |
| 304 | parsecd                        | Siri Search / Spotlight web-query service                                                    | yes                      |
| 305 | pasteboard.pasted              | System pasteboard/clipboard daemon                                                           | no                             |
| 306 | pcapd                          | Packet-capture daemon for diagnostics                                                        | yes                            |
| 307 | peakpowermanagerd              | Performance and battery-aging management daemon                                              | no                             |
| 308 | peopled                        | People-intelligence daemon for contact suggestions                                           | yes                            |
| 309 | perfdiagsselfenabled           | Performance diagnostics task                                                                 | yes                            |
| 310 | PerfPowerServicesExtended      | Extended power/performance telemetry                                                         | yes                            |
| 311 | pfd                            | Packet Filter / firewall support daemon                                                      | no                             |
| 312 | photoanalysisd                 | Photos analysis daemon for faces/objects/scenes                                              | yes                            |
| 313 | photos.ImageConversionService | Photos sharing/export image conversion; disabling caused Unable to Share, restoring plus respring repaired WhatsApp photo sharing | no |
| 314 | photos.VideoConversionService | Photos sharing/export video conversion; separate disable/restore plus respring comparison repaired WhatsApp Camera video sharing | no |
| 315 | pipelined                      | Image/camera processing pipeline daemon                                                      | no                             |
| 316 | pluginkit.pkd | App-extension broker; disabled pkd reproduced SpringBoard widget-discovery CPU loop at about one core | NO |
| 317 | pluginkit.pkreporter           | PlugInKit diagnostics reporter                                                               | yes                            |
| 318 | pointerUI.pointeruid           | Pointer UI daemon for mouse/trackpad support                                                 | yes                      |
| 319 | powerd                         | Core power-management daemon                                                                 | NO                             |
| 320 | powerdatad                     | Battery-usage data collection/reporting                                                      | yes                            |
| 321 | powerlogHelperd                | Power logging helper                                                                         | yes                            |
| 322 | PowerUIAgent                   | Power/battery-related UI alerts agent                                                        | no                             |
| 323 | preboardservice                | Pre-boot security / passcode UI service                                                      | NO                             |
| 324 | preboardservice_v2             | Updated pre-boot security UI service                                                         | NO                             |
| 325 | privacyaccountingd             | App Privacy Report accounting service                                                        | yes                            |
| 326 | proactiveeventtrackerd         | Tracks events for proactive Siri suggestions                                                 | yes                            |
| 327 | progressd                      | ClassKit education-progress service. Disabling loses education progress reporting; ordinary progress bars are not its established role. Passed the scoped iOS 16.7.16 optional-service batch, not an overnight or full-kernel-boot test.                                                             | yes                             |
| 328 | protectedcloudstorage.protectedcloudkeysyncing | Protected iCloud encryption-key sync; disabled endpoint caused CloudDocs user-key/zone preparation failures | no |
| 329 | ProxiedCrashCopier.ProxyingDevice | Copies crash logs from proxied/paired devices                                                | yes                            |
| 330 | proximitycontrold              | Proximity-based handoff/device-interaction service                                           | yes                      |
| 331 | ptpcamerad                     | PTP camera access daemon for importing photos                                                | yes                      |
| 332 | ptpd                           | PTP daemon used for photo transfer / related sync                                            | yes                      |
| 333 | purplebuddy.budd               | Setup Assistant daemon                                                                       | yes                      |
| 334 | PurpleReverseProxy             | Reverse proxy used by some USB/device communication flows                                    | yes                      |
| 335 | quicklook.ThumbnailsAgent      | Quick Look thumbnail-generation service                                                      | yes                      |
| 336 | rapportd                       | CAUSED HIGH CPU USAGE IF DISABLED                                                            | NO                             |
| 337 | recentsd                       | Recent contacts and suggestions daemon                                                       | yes                            |
| 338 | relatived                      | Motion/sensor-relative tracking service                                                      | yes                      |
| 339 | remindd                        | Reminders database/sync daemon                                                               | yes                      |
| 340 | remoted                        | Apple remote services daemon; public role not fully clear                                    | yes                             |
| 341 | RemoteManagementAgent          | Remote-management agent for supervised/managed devices                                       | yes                      |
| 342 | remotemanagementd              | Remote-management daemon for supervised/managed devices                                      | yes                      |
| 343 | replayd                        | ReplayKit screen-recording/broadcast daemon                                                  | yes                      |
| 344 | ReportMemoryException          | Reports out-of-memory events                                                                 | yes                            |
| 345 | RestoreRemoteServices.restoreserviced | Restore support service used by host restore workflows                                       | yes                      |
| 346 | retimerd                       | Private Apple hardware daemon, unclear public role                                           | no                             |
| 347 | reversetemplated               | CoreSuggestions helper. Its installed executable is in CoreSuggestions.framework; disabling can lose suggestion processing. Complete suggestion-client coverage was not tested.                                                    | yes                             |
| 348 | revisiond                      | Document revision/versioning support service                                                 | yes                      |
| 349 | routined                       | Significant locations and routine-learning daemon                                            | yes                            |
| 350 | rtcreportingd                  | SIP servers for FaceTime and Wi-Fi Calling                                                   | yes                            |
| 351 | runningboardd                  | Process lifecycle and resource-management daemon                                             | NO                             |
| 352 | Safari.History                 | Safari history support service                                                               | no                      |
| 353 | Safari.passwordbreachd         | Safari password-breach checking service                                                      | yes                            |
| 354 | Safari.SafeBrowsing.Service    | Safari fraudulent-website check service                                                      | yes                            |
| 355 | SafariBookmarksSyncAgent       | Safari bookmarks sync agent                                                                  | yes                      |
| 356 | safarifetcherd                 | Safari Reading List offline fetcher                                                          | yes                      |
| 357 | safetyalertsd                  | Safety alerts service, including Emergency SOS/crash alerts                                  | no                             |
| 358 | SCHelper                       | SystemConfiguration helper                                                                   | no                             |
| 359 | screensharingserver            | Screen-sharing / remote-screen service                                                       | yes                      |
| 360 | ScreenTimeAgent                | Screen Time tracking agent                                                                   | yes                      |
| 361 | scrod                          | Accessibility screen-recognition / OCR daemon                                                | yes                      |
| 362 | SecureBackupDaemon             | CloudServices secure-backup/key-recovery broker; missing endpoint blocked PCS metadata step, restore/retry completed key sync | no |
| 363 | security.CircleJoinRequested   | iCloud Keychain trust-circle approval service                                                | yes                      |
| 364 | security.cloudkeychainproxy3   | iCloud Keychain proxy daemon                                                                 | yes                      |
| 365 | security.cryptexd              | Cryptex and Rapid Security Response management                                               | NO                             |
| 366 | security.swcagent              | Shared Web Credentials agent                                                                 | no                             |
| 367 | securityd                      | Core security and authorization daemon                                                       | NO                             |
| 368 | securityuploadd                | Security telemetry / report upload daemon                                                    | yes                            |
| 369 | seld                           | Secure Element daemon for Apple Pay and related operations                                   | yes                      |
| 370 | SensorKitALSHelper             | SensorKit ambient-light helper                                                               | yes                            |
| 371 | sensorkitd                     | SensorKit daemon for approved sensor APIs                                                    | yes                            |
| 372 | SepUpdateTimer                 | Secure Enclave update scheduling task                                                        | no                             |
| 373 | seserviced                     | Secure Element services daemon                                                               | yes                      |
| 374 | sharingd                       | disabling disables the Share sheet                                                           | no                             |
| 375 | shazamd                        | Music-recognition daemon for Shazam integration                                              | yes                      |
| 376 | sidecar-relay                  | Sidecar relay service                                                                        | yes                      |
| 377 | signpost.signpost_reporter     | Performance signpost reporting task                                                          | yes                            |
| 378 | siriactionsd                   | VoiceShortcuts/automation broker; later isolated Off/restart basic-use checks did not reproduce the older CPU warning. Restore when Shortcuts/automations are needed.                                                                   | yes                             |
| 379 | siriinferenced                 | On-device Siri inference daemon                                                              | yes                      |
| 380 | siriknowledged                 | Siri knowledge-cache daemon                                                                  | yes                      |
| 381 | sirittsd                       | Siri text-to-speech daemon                                                                   | yes                      |
| 382 | sleepd                         | CAUSES BOOT LOOP IF DISABLED                                                                 | NO                             |
| 383 | sntpd                          | Simple Network Time Protocol daemon                                                          | no                             |
| 384 | sociallayerd                   | Shared with You / related social-content integration                                         | yes                      |
| 385 | softposreaderd                 | Tap to Pay on iPhone / SoftPOS reader service                                                | yes                      |
| 386 | sosd                           | Emergency SOS service daemon                                                                 | no                             |
| 387 | speechmodeltrainingd           | On-device speech personalization/training                                                    | yes                            |
| 388 | spindump                       | Collects spindump diagnostics                                                                | yes                            |
| 389 | splashboardd                   | Launch-screen snapshot and splash-screen management                                          | no                             |
| 390 | sportsd                        | Sports data background service for Apple apps                                                | yes                            |
| 391 | Spotlight.IndexAgent           | Spotlight indexing agent                                                                     | yes                             |
| 392 | spotlightknowledged            | Spotlight knowledge integration daemon                                                       | yes                      |
| 393 | SpringBoard                    | Primary iOS home screen and app UI manager                                                   | NO                             |
| 394 | StatusKitAgent                 | Focus status sharing agent                                                                   | yes                      |
| 395 | storagedatad                   | Storage accounting and iPhone Storage calculations                                           | yes                            |
| 396 | storagekitd                    | Keep enabled for storage framework operations. Disk management is outside the optional-service batch; no Off test was performed.                                                                     | no                             |
| 397 | storebookkeeperd               | Store-related bookkeeping such as play-state/metadata                                        | yes                      |
| 398 | storekitd                      | StoreKit and in-app purchase daemon                                                          | no                             |
| 399 | streaming_zip_conduit          | Streamed archive conduit used by install/host tooling                                        | yes                      |
| 400 | subridged                      | Apple Watch bridge service                                                                   | yes                      |
| 401 | suggestd                       | Suggestions daemon for Mail/Messages/proactive features                                      | yes                      |
| 402 | swcd                           | Shared Web Credentials and associated-domains daemon                                         | no                             |
| 403 | symptomsd                      | Network diagnostics and symptom-detection daemon                                             | yes                            |
| 404 | symptomsd-diag                 | Detailed network diagnostics task                                                            | yes                            |
| 405 | symptomsd.helper               | Helper for network diagnostics and symptom reporting                                         | yes                            |
| 406 | synapse.contentlinkingd        | Synapse content-linking helper exporting link-context, backlink and Notes activation services. Disabling can lose those cross-app links; basic-use batch checks passed.                                        | yes                             |
| 407 | SyncAgent                      | Legacy sync agent for media/content sync                                                     | yes                      |
| 408 | syncdefaultsd | Synchronized key-value preferences; provides com.apple.kvsd, whose clients logged lookup failures when off | no |
| 409 | sysdiagnose                    | System diagnostics collection task                                                           | yes                            |
| 410 | sysdiagnose.darwinos           | Low-level Darwin OS diagnostics task                                                         | yes                            |
| 411 | sysdiagnose_helper             | Helper for system diagnostics collection                                                     | yes                            |
| 412 | systemstats.microstackshot_periodic | Daily microstackshot performance/statistics collection task. Disabling loses that collection; a short batch test does not exercise daily maintenance triggers.                                     | yes                             |
| 413 | tailspind                      | Collects performance-degradation diagnostics                                                 | yes                            |
| 414 | tccd                           | Privacy permissions daemon                                                                   | NO                             |
| 415 | telephonyutilities.callservicesd | Call services daemon for CallKit, phone calls, and VoIP                                      | no                             |
| 416 | terminusd                      | Private Apple network-path daemon, unclear public role                                       | no                             |
| 417 | TextInput.kbd                  | Keyboard text-input service                                                                  | no                             |
| 418 | thermalmonitord                | Thermal monitoring and throttling daemon                                                     | NO                             |
| 419 | ThreadCommissionerService      | Thread-network commissioner service for smart-home accessories                               | yes                      |
| 420 | timed                          | System clock and time-maintenance daemon                                                     | NO                             |
| 421 | timesync.audioclocksyncd       | Audio clock-sync service                                                                     | yes                      |
| 422 | timezoneupdates.tzd            | Time-zone data update daemon                                                                 | yes                             |
| 423 | touchsetupd                    | Device-to-device setup and transfer helper                                                   | yes                      |
| 424 | translationd                   | System translation framework daemon                                                          | yes                      |
| 425 | transparencyd                  | Apple transparency verification daemon for security features                                 | no                             |
| 426 | transparencyStaticKey          | Static key/support component for transparency services                                       | no                             |
| 427 | triald                         | Feature-flag and experimentation daemon                                                      | yes                            |
| 428 | trustd                         | Certificate trust-evaluation daemon                                                          | NO                             |
| 429 | tvremoted                      | Apple TV Remote support daemon                                                               | yes                      |
| 430 | tzlinkd                        | Private time-zone helper daemon                                                              | no                             |
| 431 | uikit.eyedropperd              | UIKit color-picker / eyedropper support                                                      | yes                            |
| 432 | UsageTrackingAgent             | Usage-tracking agent for Screen Time / related analytics                                     | yes                      |
| 433 | usb.networking.addNetworkInterface | USB networking interface setup task                                                          | yes                      |
| 434 | usbsmartcardreaderd            | USB smart-card reader daemon                                                                 | yes                      |
| 435 | UserEventAgent-System          | System user-event scheduling and launch agent                                                | NO                             |
| 436 | videosubscriptionsd            | TV provider / video-subscription single sign-on daemon                                       | yes                      |
| 437 | voicemail.vmd                  | Visual Voicemail daemon                                                                      | yes                      |
| 438 | voicememod                     | Voice Memos background support daemon                                                        | yes                      |
| 439 | VoiceOverTouch                 | VoiceOver touch-interaction support daemon                                                   | yes                      |
| 440 | watchdogd                      | System watchdog daemon                                                                       | NO                             |
| 441 | watchlistd                     | TV app watchlist / Up Next sync daemon                                                       | yes                      |
| 442 | watchpresenced                 | Apple Watch proximity / presence detection daemon                                            | yes                      |
| 443 | wcd                            | WatchConnectivity daemon for Apple Watch apps                                                | yes                      |
| 444 | weatherd                       | Weather framework and weather-data service                                                   | yes                      |
| 445 | WebBookmarks.webbookmarksd     | Web bookmarks daemon used by Safari and related apps                                         | no                      |
| 446 | webinspectord                  | WebKit remote-inspection daemon                                                              | yes                      |
| 447 | webkit.adattributiond          | WebKit ad attribution / Private Click Measurement daemon                                     | yes                            |
| 448 | webkit.webpushd                | WebKit web-push notifications daemon                                                         | yes                      |
| 449 | wifi.hostapd                   | Wi‑Fi hotspot access-point daemon for Personal Hotspot                                       | yes                      |
| 450 | wifi.wapic                     | WAPI Wi‑Fi security support daemon                                                           | yes                            |
| 451 | wifianalyticsd                 | Wi‑Fi analytics and telemetry daemon                                                         | yes                            |
| 452 | wifid                          | Core Wi‑Fi network-management daemon                                                         | no                             |
| 453 | WiFiFirmwareLoader             | Wi‑Fi firmware loader service                                                                | no                             |
| 454 | wifip2pd                       | Peer-to-peer Wi‑Fi daemon used by AirDrop/AirPlay                                            | yes                      |
| 455 | wifivelocityd                  | Wi‑Fi diagnostics and measurement daemon                                                     | yes                            |
| 456 | WirelessRadioManager           | Coordinates wireless radios such as cellular/Wi‑Fi/Bluetooth                                 | no                             |
| 457 | xpc.roleaccountd               | Private Apple XPC/account-role daemon, unclear public role                                   | no                             |
| 458 | accessoryd                     | External Lightning/dock accessory hardware communication daemon                              | yes                      |
| 459 | aslmanager                     | Apple System Log file archiving and maintenance daemon                                       | yes                            |
| 460 | assistant_service              | Siri voice helper and intent dispatch service                                                | yes                      |
| 461 | assistantd                     | Shared Siri and keyboard dictation backend. Exports com.apple.assistant.dictation. Keyboard dictation on the tested iOS 16.7.16 phone required both assistantd and corespeechd: each alone failed, the pair produced text. Other Siri/Shortcuts jobs were still disabled.                                                  | yes                      |
| 462 | assistivetouchd                | AssistiveTouch accessibility on-screen pointer and menu service                              | yes                      |
| 463 | AuthenticationServicesAgent    | Web and passkey authentication agent                                                         | no                             |
| 464 | awdd                           | Apple Wireless Diagnostics and telemetry daemon                                              | yes                            |
| 465 | backupd                        | Device backup daemon for local and cloud backup jobs                                         | yes                      |
| 466 | BarcodeNotificationService     | Camera barcode and QR code detection notification service                                    | yes                      |
| 467 | checkerboard                   | Diagnostic and factory setup assistant UI helper                                             | yes                             |
| 468 | coresymbolicationd             | Symbolication service for crash logs and stack traces                                        | yes                            |
| 469 | crash_mover                    | Moves crash logs from temporary to persistent diagnostics directory                          | yes                            |
| 470 | crashreportcopymobile          | Copies mobile user crash reports for diagnostics                                             | yes                            |
| 471 | devicedataresetd               | Handles Erase All Content and Settings device wipe workflows                                 | no                             |
| 472 | DumpBasebandCrash              | Extracts cellular baseband modem crash dumps                                                 | yes                            |
| 473 | DumpPanic                      | Extracts kernel panic logs after an unexpected reboot                                        | yes                            |
| 474 | gamed                          | Game Center friends, achievements, and leaderboard daemon                                    | yes                      |
| 475 | healthd                        | HealthKit core database and sensor pedometer logging                                         | yes                      |
| 476 | homed                          | HomeKit smart home accessory database and coordinator                                        | yes                      |
| 477 | iap2d                          | iPod Accessory Protocol v2 daemon for automotive / MFi accessories                           | yes                      |
| 478 | iapauthd                       | MFi accessory hardware cryptographic authentication                                          | yes                      |
| 479 | iapd                           | Legacy iPod Accessory Protocol daemon                                                        | yes                      |
| 480 | itunescloudd                   | Apple Music cloud library and iTunes in the Cloud sync                                       | yes                      |
| 481 | midiserver-ios                 | CoreMIDI server for MIDI software and instruments, including Bluetooth MIDI connections. The installed label is com.apple.midiserver; disabling loses MIDI functionality.                                             | yes                      |
| 482 | mobile.obliteration            | Low-level NAND filesystem wiping task for factory resets                                     | yes                             |
| 483 | mobile.softwareupdated         | Background software update installation coordinator                                          | yes                      |
| 484 | MobileInternetSharing          | Personal Hotspot tethering network sharing daemon                                            | yes                      |
| 485 | OTACrashCopier                 | Copies over-the-air crash reports for telemetry                                              | yes                            |
| 486 | OTATaskingAgent                | Executes remote Apple diagnostic tasks                                                       | yes                            |
| 487 | passd | Wallet/PassKit backend also required by tested App Store free-app confirmation; restore plus respring repaired download/install/launch | no |
| 488 | printd                         | AirPrint printing spooler daemon                                                             | yes                      |
| 489 | racoon                         | IPsec IKE key exchange daemon for enterprise VPNs (Guideline: if-unused)                                            | yes                      |
| 490 | ReportCrash                    | Core system crash reporter daemon                                                            | yes                            |
| 491 | searchd | Spotlight query coordinator; isolated Off/restart later reproduced assetsd at about 106% of one core; restoring only searchd stopped that loop. | NO |
| 492 | softwareupdateservicesd        | iOS software update check and background download daemon                                     | yes                      |
| 493 | syslogd                        | Core BSD system log socket daemon                                                            | yes                             |
| 494 | tipsd                          | Tips app content sync and notification service                                               | yes                            |
| 495 | voiced                         | VoiceOver speech synthesis audio daemon                                                      | yes                      |
| 496 | AccessibilityUIServer | Optional accessibility UI host; basic usage tested with it off; no Recommended On classification | no |
| 497 | PosterBoard | Wallpaper/Lock Screen host and tested media-panel rendering dependency; default Off, enable with liveactivitiesd for that feature | yes |
| 498 | PaperBoard | Wallpaper-related UI host; not required for tested Lock Screen playback controls | yes |
| 499 | ShortcutsUI | Interactive Shortcuts UI host; kept On on the tested phone, freely switchable | yes |
| 500 | sharing.airdropui | AirDrop UI host; native Allow AirDrop policy is separately controlled | yes |
| 501 | PassbookUISceneService | Wallet interface host; separate from Critical passd needed by tested App Store confirmation | yes |
| 502 | livetranscriptionui | Live Captions UI host on supported devices | yes |
| 503 | replaykitangel | ReplayKit screen recording/broadcast UI host; tested with replayd and respring | yes |

## Device test findings — 2026-10-11

Tested iPhone 8 Plus (iPhone10,2), iOS 16.7.16 **20H392**, Dopamine rootless, Python **3.9.9**. The roughly 64-minute session exercised 321 installed entries from the old 328-entry default-off list; seven names/aliases were absent. Seven installed targets were initially unregistered and had no live behavior to exercise. These results apply to this device and workload.

Two userspace reboots were performed. The first failed to preserve the off state: the launchd plist had mobile:mobile ownership and mode `01204`, and launchd replaced its entries at startup. After backing up the state, correcting it to root:wheel `0644`, and reapplying through launchctl, the second preserved all 321 off targets. The correction/reapplication passed; ownership versus mode causality was not independently isolated.

`disable` plus killing a process did not reliably stop demand restarts. Test unloading used `bootout`, which exposed intermediate CPU problems. The app now saves native overrides without hot-unloading and tells users to restart userspace for full process changes. This avoids promising immediate RAM savings and reduces disruptive transitions.

Duetexpertd sustained roughly one CPU core in an intermediate state; its exact preceding trigger was not identified. Unloading it settled that workload. Later assetsd sustained roughly one core while Photos retried failed Spotlight queries: 5,364 repeated delete-query errors and matching syndication-worker errors occurred in a diagnostic window. Fresh full-off reboot and a short searchd on/off comparison did not reproduce the loop. Searchd is conservatively kept enabled in the revised preset; duetexpertd is not newly classified as unsafe to disable because unloading it resolved rather than caused the observed load.

The post-reboot retained window contained 558,096 records, including 21,430 errors, 215 faults, and three Loss records. Faults included missing FamilyControls services, Biome write/lockfile failures, failed KVS lookup, preferences access fallbacks, CommCenter NECP errors, and mDNSResponder lock errors. Not every error was isolated to a single toggle. No actual panic, watchdog-timeout, EXC_BAD_ACCESS, or SIGSEGV event was found in that reviewed window; two watchdog matches only configured a timeout. A simulated nonfatal diagnosticd report appeared during earlier collection. Disabled crash collectors and lost log records limit the completeness of crash evidence.

User-confirmed unlock, Notes typing, Camera, Photos, and Safari checks passed after the complete off preset survived reboot. Core processes remained present at measured checkpoints; HTTPS returned 200 and Wi-Fi/USB SSH worked. Battery life, overnight activity, calls/SMS, cellular data, notifications, accessories, and all cloud features were not comprehensively tested.

Baseline median physical free RAM was **215.4 MiB** (six samples); the five-minute post-reboot workload median was **121.2 MiB** (29 samples). Free plus speculative was 364.7 versus 177.1 MiB; compressor occupancy 351.6 versus 328.1 MiB. App activity, reboot effects, and collection differed: these are not isolated performance gains. The >600 MiB idle physical-free target was not achieved.

Verified related Contacts stores on this phone: `/var/mobile/Library/AddressBook/AddressBook.sqlitedb` and `AddressBookImages.sqlitedb`. The app shows metadata and paths for contactsd and ABDatabaseDoctor; it does not read contact records or guess private database locations for unrelated services.

The revised 506-entry config has **320 default-off entries** and is mirrored at the app repository root as `daemon.cfg`. Historical raw test files and obsolete script/app copies were removed from this notes directory after these findings were incorporated. Original device-side test/backup files remain under `/var/mobile/Documents/daemon-test-2026-10-11`; they are not distributed with the app.

## Messages recovery and category revision (2026-10-11)

After the broad off profile, Messages appeared empty. Re-enabling imagent and identityservicesd alone exposed repeated identityservicesd aborts and a black Messages launch screen. Live logging showed `IDSGroupAgent registerAgent` asserting `self.interfaceName`, with nehelper/networkd_privileged unavailable. Enabling the actual `com.apple.nehelper` job restored Identity Services; the user confirmed conversations returned. The database passed SQLite quick_check and retained 247 messages and 79 conversations; a snapshot remains on the phone under `/var/mobile/Documents/messages-recovery-2026-10-11`.

Keep imagent, identityservicesd, and nehelper enabled by default as **KEEP**, not capital **NO**. The `nehelper-embedded` plist filename maps to job label `com.apple.nehelper`; both config entries now target On. These are feature/dependency recommendations, manually switchable without critical confirmation. Nehelper also serves other Network Extension clients: disabling it while Identity Services remains enabled caused repeated background crashes. No additional bootloop classification was established by this recovery.

The app's Critical tab retains all installed capital-NO classified services regardless of their toggle state, with disabled entries first. Existing critical classifications remain intact; no service is promoted solely from a guessed risk. Defaults lists all 506 compiled targets, including entries not installed on the current device. Fresh installations need no config import. The RAM label is only a number and MiB; the header uses kernel free_count, which already includes speculative pages.

## Wallpaper host and storage (v1.0.4)

The verified PosterBoard job is `/System/Library/LaunchAngels/com.apple.PosterBoard.plist`, label `com.apple.PosterBoard`, domain `user/501` on this phone (the app discovers the mobile UID). Killing its PID does not save an override. Native disable plus bootout stopped it and most associated poster extensions in the observed window. A userspace reboot preserved the off override and left no PosterBoard/poster-extension processes running. The user confirmed unlock, Home, Notes and Messages work after this reboot. Battery impact and long-term stability remain unmeasured. The app exposes this job under Tools → Background UI and in Services, together with the tested AccessibilityUIServer host. Other untested LaunchAngels remain excluded. The compiled default keeps it enabled for other users; manual disabling remains available, and userspace restart fully applies it. No auto-kill daemon, polling loop, file renaming, or arbitrary app blacklist is installed.

Speculative pages are reclaimable kernel filesystem cache, not a separately disableable SysMain service. Disabling predictive daemons does not eliminate all speculative pages. No useful verified force-compress control was found; no induced memory pressure or forced app eviction is implemented. Core account daemons and siriactionsd retain their existing critical warnings; feature names alone do not establish safe disabling.

The storage driver advertises NVMe SMART capability, but read-only IOCreatePlugInInterfaceForService returned `0xe00002c7` on this device; lifetime writes/endurance/wear could not be obtained. The old capacity section was removed in v1.0.5; current disk health uses on-demand ASP diagnostic reports. No terabyte-write lifetime or rated endurance is invented.

## Audit follow-up and app revision (v1.0.6)

Tools → Background UI now exposes PosterBoard and AccessibilityUIServer only. AccessibilityUIServer is **KEEP / Recommended On**, not capital NO; its builtin target is enabled. It remains freely switchable, including in Services. The tested phone retains its manual off override. Re-enable and restart userspace before using accessibility features. Installation never applies defaults. The config and IPA contain **506 targets: 320 off, 186 on**; biomed, syncdefaultsd and Messages dependencies remain enabled by default.

Back Tap, Speak Screen and the general accessibility setting were verified off across userspace reboot. AccessibilityUIServer used about 0.002% of one core with those settings off, before its host was disabled; its roughly 23 MiB RSS includes shared pages and is not a guaranteed net RAM gain. The user repeated Home, unlock, Notes, Camera and Messages checks successfully after disabling it and rebooting. All accessibility features and actual iMessage delivery were not independently tested. Disabling this host produces expected unavailable-host endpoint messages.

biomed and syncdefaultsd were restored on the phone. Final reviewed errors no longer explicitly named their missing Biome/KVS endpoints. Restoring contextstored, duetexpertd and coreduetd did not eliminate redacted context failures, so all three were returned off. Remaining CoreDuet, graphics, preferences and account errors are unresolved; no claim of error-free operation is justified. Backboardd averaged 12.46% of one core on Home and 0.514% locked in the final samples. Pressure remained Normal, with no observed panic/watchdog reboot/thermal emergency in the reviewed window. Privacy redactions, three lost-log records and disabled crash reporters limit these findings. No battery-life or permanent CPU improvement was established.

Animation controls and their helper were removed from the app. The independently installed animation tweak is left active and managed outside this app. RAM remains memory-only. Disk write counters use grouped raw numbers and can be tapped for their meaning and conversion status. GB conversion remains unavailable: the ASP snapshot and installed diagnostic parser did not establish bytes per count; page size or generic NVMe units cannot substitute for that evidence. No guessed conversion is shipped.

The unsupported absd Contacts description and related database links were removed. Its actual plist exports com.apple.absinthed and com.apple.absd; the broader private role remains undocumented here. Verified Contacts database links remain for contactsd and ABDatabaseDoctor.

## Current revision — 2026-10-12 / v1.0.8

Current config: 512 targets, 325 off and 187 on. Capital `NO` is also used in the config for critical enabled targets. Pkd, nehelper (plus nehelper-embedded alias), searchd, biomed, syncdefaultsd and mobile.lockdown are Critical; this supersedes their earlier KEEP/recommendation classification above. Searchd's isolated CPU causality remains unproven. Imagent and identityservicesd remain optional Messages recommendations. AccessibilityUIServer is optional, not Recommended On or Critical; its builtin target stays On and the user's manual Off choice remains available.

Pkd's disabled state was isolated on this iPhone 8 Plus/iOS 16.7.16: SpringBoard used 5.153 CPU seconds in 5 seconds with repeated widget-extension discovery. Enable/load plus respring reduced settled usage to 0.209 seconds over 15 seconds. Disabling/unloading pkd and starting SpringBoard again reproduced the same stacks (11.434 CPU seconds over 10 seconds). Restoring it and respringing yielded 0.024 seconds over 15.01 seconds and thermal pressure 0. This was an additional manual disable, not an off builtin. Other app clients can retain stale extension connections until userspace restart: the missing WhatsApp/Save to Files Share Sheet entries returned after such a restart with the optional UI hosts still off.

Overrides is now a separate tab, replacing Config. Import/apply/export is under Tools. Descriptions are visible in collapsed rows; warnings, paths and file metadata stay expandable. The tested optional catalog includes PosterBoard, PaperBoard, AccessibilityUIServer, LiveTranscriptionUI, AirDropUI, PassbookUISceneService, ShortcutsUI, ReplayKitAngel/replayd, chronod and liveactivitiesd. Individual switches immediately save and verify overrides and load/unload those jobs; general daemon batches still require userspace restart. InputUI, OverlayUI, ClockAngel and MediaRemoteUI remain excluded.

PaperBoard exports com.apple.paperboard.services and is wallpaper-related; no broader private role is claimed. Disabling AirDropUI, PassbookUISceneService, LiveTranscriptionUI and PaperBoard followed by respring/userspace restart preserved user-tested Camera photo/video capture and the restored normal Share Sheet. A 10-second Camera-active sample used 0.348 SpringBoard CPU seconds with thermal pressure 0. All new hosts were initially idle with no process to reclaim; no net RAM/battery gain is established.

AirDrop's DiscoverableMode was already Off, which only blocked receiving. A feature-entitled client set native MCFeatureAirDropAllowed/allowAirDrop to false; isAirDropAllowed returned false. Ordinary unentitled Python was rejected by profiled with the exact required feature-setting entitlement and made no policy change. The app's on-demand signed helper changes/verifies only that feature. Its wrapper restores profiled's prior override and loaded state. Sharingd stays on for sharing; no profile or direct policy-database edit is used.

ReplayKit is screen recording/broadcasting, separate from Camera recording. The user rarely uses it and wants both ReplayKitAngel and replayd switchable and normally Off; enable both when needed. ShortcutsUI remains On and independently switchable. The WhatsApp Open Meta AI Auto Shortcut still produced 4097 through linkd after userspace restart and restoring the four new optional hosts. Its core brokers remained enabled. The user verified Apple’s Set Flashlight action works; the remaining WhatsApp failure has not been traced to a disabled service.

## Feature groups and final verification — v1.0.9

Overrides has 13 groups covering 40 installed job labels. On enables listed feature jobs and disabled prerequisites; Off preserves Critical and shared prerequisite-only services. Shared services appear in several groups but have one underlying override. Broader account/sync/store groups save overrides and need userspace restart; sign-in, cloud sync, purchases and FaceTime calls were not tested. These are checked roles/known prerequisites, not a complete OS dependency map.

ReplayKitAngel/replayd restoration initially left SpringBoard’s ReplayKit client uninitialized (isAvailable=1, isInitialized=0, 4099). Starting replayd alone did not repair it. Respring made initialization succeed and screen recording save to Photos. With the eight unrelated optional jobs off again and another respring, the user confirmed recording, Save to Files and WhatsApp sharing all work. Both recording jobs were then disabled as requested. Re-enable both and respring before recording. Apple’s Set Flashlight shortcut works; the unused WhatsApp Meta AI action still fails and is not established as a missing-daemon issue.

Installed v1.0.8 preserved the helper entitlement; native AirDrop read/on/off verified policy 0/1/0 and restored profiled disabled/unloaded each time. The user confirmed Overrides, visible descriptions and the Tools import file picker. A final SpringBoard sample with optional/recording hosts off used 0.004 CPU seconds over 5 seconds (0.08%), thermal pressure 0. This is CPU evidence, not proof of a RAM/battery gain. Widget-update jobs are off, but a universal widget disable is not provided: legacy widget paths can remain and pkd must stay enabled.

Deployment: v1.0.9 / build 10, source `b40f9cbccb871b0e07b6922b8275275d143236d3`, Actions run `38213950882` succeeded (19 Python tests, Swift memory/group policy checks, iOS build and packaging). IPA SHA256 `fa092542663a7dcfe516af1c481947f29a8728d3f4a725fcb912fcb79213e088` matched the release digest. TrollStore Lite install returned 0 and installed Info.plist matches this version/revision; uiopen succeeded. Phone state preserves pkd/Shortcuts On, recording Off, and diagnosticd/profiled Off. Original 336 override values were unchanged by optional-host testing; six explicit host entries were added. App repository changes were pushed; reference notes remain local.

Correction: although v1.0.9 installed and uiopen returned success, the user reported launch crashes. A duplicate com.apple.FileProvider description key caused Swift dictionary initialization to trap. v1.0.10 removes that duplicate and adds Python uniqueness plus Swift dictionary-initialization checks. Services/Critical now consolidate Search, numeric available MiB and theme control into one row, with the filter below; the separate top bar and pinned category headings are removed. Device launch verification is required after installation.

Deployment v1.0.10 / build 11: source ac933c355c2864225afc0975c56ad9f0051f7893, successful Actions run 38215041614 (20 Python tests, Swift memory/group/dictionary checks and iOS build). Release/download SHA256 7eaf0738b024cfb8d364da29dcfdd59add524e13475fb77f892406f2f25ddd19 matched. TrollStore installation returned 0 and installed Info.plist matched. Unlike v1.0.9, the launched process remained present across repeated checks. The user reported the compact layout and recording group work. Recording after group On/respring passed, but the final phone check still found both jobs enabled. A follow-up Off-button check was requested; root restored both jobs Off with verified native unloads to leave the requested normal state. The last UI Off-button outcome remains pending. Account/sync functionality remains untested. Diagnosticd, ReportCrash and profiled were restored disabled/unloaded after launch investigation.

## Apple Account / SMS repair and subpages — v1.0.13

Overrides now has one subpage per feature. The user confirmed navigation and Back on Screen Recording, AirDrop and Apple Account in installed v1.0.12.

Settings was stuck at Apple ID Next: com.apple.ak.auth.xpc lookup failed, akd/adid were enabled but unloaded, and appleaccountd was off. Enabling these brokers and restarting userspace restored the password/two-factor stage. CommCenter received carrier SMS while imagent was off; enabling/loading imagent and respringing restored user-confirmed verification SMS delivery.

Absd exports com.apple.absinthed. With absd off, AppleAccount NAC/Absinthe initialization failed with -44303/-4403 and iCloud login returned HTTP 400. Enabling/loading only absd plus respring changed NAC initialization to success. This is a verified authentication-stage role; the old Contacts attribution remains unsupported.

Later verification still failed. Restoring cdpd, followupd and identityservicesd together, then restarting userspace, was followed by user-confirmed full Apple Account sign-in. Logs show LoginAndUpdateAccount Authenticated=1/error=0 and successful Messages/FaceTime service sign-ins. The final three were tested as a set; individual responsibility for the preceding reset/timeout error was not isolated. FaceTime sign-in is not a tested FaceTime call.

Current Critical classification includes absd, imagent, identityservicesd, cdpd and followupd alongside earlier critical dependencies. Account On includes the complete repaired set and SMS prerequisite; account/Messages group Off preserves those Critical jobs. Individual switches remain editable with warnings. New builtin targets keep absd/cdpd/followupd On. Config: 512 targets, 322 Off / 190 On; all Critical enabled entries use capital NO. This supersedes older KEEP/recommendation and 325/187 counts in historical sections. Explicit custom Off choices remain explicit; installing an update does not automatically apply presets.

Further tests must be recorded by feature and state, with respring/userspace restart after changes. Full iCloud sync, App Store downloads, actual FaceTime/phone calls and every service combination are not established by sign-in or the memory allocation stress test. No net RAM or battery-life gain is claimed. Larger suspended apps in a pre-repair sample were Sudoku (~295 MB), Sileo (~108 MB) and WhatsApp (~101 MB); mostly small remaining helpers did not establish another worthwhile safe daemon disable.

## Photos sharing/export repair — v1.0.14

Photos reported Unable to Share with explicit XPC lookup error 3 / NSCocoaErrorDomain 4099 for disabled com.apple.photos.ImageConversionService. Restoring only that job and respringing repaired the user-tested Camera photo/screenshot sharing path. With the image repair retained, Camera video sharing independently failed and logs named disabled com.apple.photos.VideoConversionService. Restoring only the video job and respringing repaired the same video test. Both are now Critical/default On, with uppercase NO in the matching configs. This identifies these export failures; it does not prove the cause of the earlier disappearing WhatsApp chat picker or all media-format combinations.

Overrides adds a Photos sharing and export subpage. Group Off preserves both Critical jobs, so its group Off button is unavailable; individual warned switches remain editable. On restores both and shared assetsd/pkd/sharingd. Current config: 512 targets, 320 Off / 192 On, superseding older counts.

## App Store free-download repair — v1.0.15

Get spun briefly and returned. Logs showed a successful HTTP response followed by Payment Sheet Failed. Restoring amsengagementd alone plus respring did not repair it; PassKit then explicitly reported missing com.apple.passd.in-app-payment. Restoring only passd plus respring restored the password/confirmation sheet, free-app download, installation and user-confirmed launch. The PassbookUISceneService override stayed off; the separate PassbookUIService launched for confirmation. Turning amsengagementd back off and respringing still allowed a second new free app to download and open. It remains optional; its previous telemetry-only description was incomplete.

Passd is Critical/default On and an App Store shared prerequisite. Wallet group Off preserves it while leaving the interface switchable; individual warned switches remain available. Paid purchases, Apple Pay and Wallet passes remain untested. Current matching config: 512 targets, 319 Off / 193 On. FaceTime calls were unavailable to test because the user had no test contact; avconferenced was restored Off. Cloud sync testing is pending.

Deployment v1.0.14 / build 15: source bb70111567f69f3fdbcd31cf29332267ecd11d67; Actions run 38223458480 passed Python/Swift checks and iOS packaging. Release IPA SHA256 2503408a28f6cb4bc1e2b084b1a625d4cd17bfe56c354e9f6f04146cbbd52d24 matched GitHub digest and embedded version. TrollStore installation returned 0, installed Info.plist matched, and process 12682 was present after opening following userspace restart. New sharing subpage user check remains pending.

### Protected iCloud key repair and menu controls — v1.0.15

Drive/Photos initially stalled after their optional jobs were enabled. CloudKit explicitly failed user-key sync at missing ProtectedCloudKeySyncing. Enabling that job and restarting userspace made it reachable, but a two-hour retry deadline deferred recovery. Removing only nextRegistrySync via mobile CFPreferences and restarting that job exposed the next missing endpoint, com.apple.SecureBackupDaemon, at FetchMetaData. After restoring it and requesting another retry, logs showed SynchronizeKeys complete: success. The user completed the account notice and confirmed it cleared, then restarted userspace. Existing Drive files appeared; a photo saved to Drive was confirmed on the web and downloaded intact. The first Save to Files attempt unexpectedly returned Home, while the second passed; that transient failure has not been isolated. Photos upload remained stalled. Contextstored is temporarily restored for a separate comparison of Photos CoreDuet context failures; it is not yet established as the cause of the upload stall.

Both key-recovery jobs are now Critical shared account/Drive/Photos prerequisites. SecureBackupDaemon now defaults On; ProtectedCloudKeySyncing already defaulted On but had been manually disabled. Current matching config: 512 targets, 318 Off / 194 On. The retry-deadline diagnostic is not part of automatic group actions. No account/key reset was performed by the diagnostic code. Preserve the phone’s existing passcode/jailbreak setup; the user identifies this as palera1n on A11.

Overrides menu rows now provide On/Off beside the link, retaining the subpage controls and Critical preservation policy. Deployment v1.0.15 / build 16: source 5647956a28d966b527f0e9de544af6887890f67d; Actions 38227081398 passed Python/Swift checks and packaging; IPA SHA256 2e920d6f1b0a81036f9584673788c1e21fc362859f01c55063872ac1db3832f5 matched release digest/version. TrollStore installation returned 0 and installed version matched. Menu-button user verification remains pending.

### Photos scheduling comparison — v1.0.16

Contextstored exports com.apple.coreduetd.context. After restoring it and restarting userspace, the scheduled Photos retry generated/uploaded derivatives and the user confirmed two fresh photos synced and appeared on the web. Turning only contextstored back off and restarting reproduced a third-photo stall. At the retry, Photos/DAS context reads/writes failed and DAS rejected com.apple.cloudphotod.sync.discretionary for DataBudgetPolicy (remaining ~546 KB vs predicted 10 MiB) and NetworkQualityPolicy (required 100, observed Wi-Fi/cellular 0). The restart ThunderingHerdPolicy was no longer a listed blocker. It was restored for a final same-photo recovery comparison.

Contextstored is Critical/default On and included with dasd in the Photos group. Current matching config: 512 targets, 317 Off / 195 On. The user selected returning iCloud Drive/Photos sync services to their original Off states after testing; Critical account/key/context dependencies stay enabled. Other Siri learning/prediction jobs were not restored for this comparison. Final recovery/menu-button checks remain pending. No net RAM saving or exhaustive combination test is claimed.

### Final verification / normal profile — v1.0.16

Restoring contextstored again completed the same third-photo upload after the scheduled retry. Save to Files into Drive passed again and was visible on the web. The user enabled Drive/Photos from the new menu On buttons, used Tools userspace restart, and reported fresh-restart cloud checks passed. Both menu Off buttons followed by userspace restart then left bird/cloudd/cloudphotod/assetsd.nebulad disabled/unloaded; repaired Critical account/key/context/scheduling dependencies stayed enabled/loaded. The initial unexpected return Home did not recur in later tests, but its precise cause was not isolated.

Final AirDrop policy read returned 0; profiled was restored disabled/unloaded afterward. Diagnosticd/ReportCrash were restored disabled/unloaded. Recording, widgets/Live Activities, wallpaper/captions/Wallet interface hosts, amsengagementd and avconferenced stayed Off. Baseline comparison in both saved domains found only 12 retained enable changes: appleaccountd, absd, cdpd, followupd, identityservicesd, imagent, image/video conversion, passd, ProtectedCloudKeySyncing, SecureBackupDaemon, contextstored. Akd/adid were already saved On and were reloaded earlier.

Settled spindump: SpringBoard 0.015 CPU seconds / 15.00 seconds = 0.10% of one core; thermal pressure 0. Contextstored footprint 6,880 KB, CPU 0.008 seconds. This is a short settled sample, not net RAM/battery proof. No additional worthwhile safe daemon disable was established. FaceTime calls, paid purchases/Apple Pay, exhaustive combinations, memory background-cleanup behavior and the WhatsApp Meta AI action remain unverified/unresolved.

Deployment v1.0.16/build 17: source 0f730f88a1e417af877dc53e23084103b2656b74; Actions 38229538314 passed 20 Python and Swift checks plus iOS build/package. IPA SHA256 67024e577f83ce2e62c08d24d405619edd40f1234b771ee44d2c50f7aeb1a284 matched release digest/version. TrollStore returned 0, installed version matched, launch was verified and menu On/Off actions were exercised. Final config: 512 targets, 317 Off / 195 On, matching app/notes. Bare-repository staging was left to the user.


### Lock Screen playback hosts — 2026-10-12, v1.0.17

The tested Lock Screen/Notification Center media group needs PosterBoard + liveactivitiesd, with PaperBoard still Off. Keep all three optional/default Off as explicitly requested; complete group Off must remain available. PosterBoard is shared with Wallpaper, while widget updates now controls chronod separately. Restart userspace after media-group changes. Config now matches 512 targets, 318 Off / 194 On; existing Critical NO entries are unchanged. See the [daemon test notes](scripts/daemons/README.md) for the On/Off comparison and recorded MediaRemoteUI rendering failure.


### SpringBoard interface controls — v1.0.20

Control Center and App Library have no separate launchd jobs in this phone's inspected configuration. The app deploys a minimal SpringBoard-only rootless companion to block their presentation/capability methods, with individual and menu On/Off switches, saved-versus-current-PID reporting and removal. Respring applies changes. This does not establish a reduction in allocated RAM. All 512 daemon targets remain unchanged (318 Off / 194 On).

The prototype passed Off/On checks, but packaged v1.0.18 wrote settings to the bootstrap mobile home (/var/jb/var/mobile) instead of SpringBoard's native /var/mobile. It therefore saved Off without applying it. The corrected script uses the native path and account UID/GID for ownership; after a respring the runtime report showed all requested hooks active, and the user confirmed the interfaces were gone. A regression test covers distinct bootstrap/native homes.

The user requested removing dock/status-bar controls because they only hide views; those switches and layout hooks were removed and both restored. The remaining switches block access to Control Center/App Library; RAM savings remain unverified. Attempts to remove Today View did not work and their hooks were removed, with the user's agreement. Keep pkd and repaired Critical services enabled. No jetsam-limit change or persistent memory stress allocation was introduced.


The installed v1.0.20/build 21 app passed All On → respring → both interfaces available, followed by Off → respring → both inaccessible, confirmed by the user. Dock/status bar were restored and their switches removed. Final saved settings and current-PID runtime report both have Control Center/App Library disabled. CI run 38244546744 passed 23 Python tests, Swift memory/stress/group checks and iOS build/package. Source ca663064295eb14b72a669ececcfcf37eeda7277; IPA SHA256 35e71d9caee42bc50b7e251d69633b10420ebf44ca925c2fbb101d33c6d4bdca matched the release digest and embedded source/version; TrollStore returned 0 and the app process was observed running. Both saved launchd domains matched the pre-interface profile exactly. A settled 10-second sample measured SpringBoard at 0.010 CPU seconds (0.10% of one core), footprint 64.13 MB. That is not a comparative RAM/battery benchmark or exhaustive stress proof.
