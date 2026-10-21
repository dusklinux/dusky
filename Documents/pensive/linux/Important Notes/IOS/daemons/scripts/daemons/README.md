# Daemon config notes and historical results

Start with the [daemon operating guide](../../README.md). This directory holds
the mirrored preset config and historical notes, not the retired standalone
scripts. Later dated corrections supersede older "current"/"final" sections;
do not use a past tested profile as the phone's present state. The complete
dependency record is the [iOS 16 reference](../../daemons%20based%20on%20ios%2016.md).

## Siri voices and optional controls — 2026-10-21

The owner verified Siri US English Voice 5 works after enabling only triald, and
regular functions still work with the optional accessibility batch Off. Trial stays
optional/default Off and is included in Siri. AccessibilityUIServer now defaults
Off; Background Sounds/heard stays On. New groups cover AssistiveTouch, Speak Screen,
CarPlay, Handoff, Watch and nearby/AirPods helpers, retaining shared dependencies.

Daemon Manager 1.0.38/build 39 and Duskie 0.2.72 are installed/published. Services
supports mixed-state multiselection; result controls are Apply Now / Restart
Userspace / Dismiss. Optional two-GiB inflation uses the installed Duskie worker;
Release is explicit. Pinned clipboard editing uses hold/3D Touch → Edit. The owner
will test these new UI controls after returning; native/build checks passed.
Current targets: **513 entries, 329 Off / 184 On, 58 Critical On**. Final task-specific
native changes are triald On and AccessibilityUIServer Off. Installation/restart
preserved preferences and clipboard data; diagnosticd is restored Off/unregistered.
The [latest reference section](../../daemons%20based%20on%20ios%2016.md) records evidence,
state distinctions and deployment provenance. Older results below remain dated history.

## SMS/contact recovery and regular-use defaults — 2026-10-21, v1.0.34 installed

`com.apple.accessibility.heard` and `com.apple.DragUI.druid` now default **On**
(`no` / builtin `false`) in the working source and mirrored config, at the owner's
request. **Background sounds and hearing** and **Drag and drop** are separate
optional Groups entries; manual Off remains available. They are outside Critical
and the live-unload allowlist, so group changes require userspace restart. Hearing
On restores the shared audio prerequisite; Hearing Off preserves that prerequisite.

`com.apple.cmfsyncagent` also now defaults **On**, Recommended On, outside
Critical. Disabling it in the tested iOS 16.7.16 profile reproduced missing carrier
SMS and an unresponsive recent-call/contact ⓘ. A 12-service On batch worked; six
then three candidates still worked; restoring all three Off reproduced the fault;
restoring only cmfsyncagent restored SMS and contact details, user-confirmed with
the other 11 candidates Off. Calls and SMS, Messages and FaceTime, and Apple Account
On restore it as a shared prerequisite; their Off actions preserve it. Individual
manual Off remains available. Wi-Fi ⓘ was responsive in the restored profile;
its original intermittent delay was not independently isolated to this service.

Matching source/config: **513 targets, 328 Off / 185 On, including 58 Critical On**.
Only these three Boolean targets changed. Existing explicit custom choices remain
authoritative. The historical full-copy preference migration retains its original
builtin reference values. Installing the app does not apply defaults.
The phone now runs **v1.0.34/build 35**, installed through TrollStore Lite after the
[macOS workflow](https://github.com/dusklinux/daemon_manager/actions/runs/39599823153)
passed Python/native Swift checks and built/signed the IPA. Release, downloaded and
phone IPA hashes matched. App preferences and saved overrides were unchanged by
installation; the source commit is `060c7f33288d9b04e892d2a9478f3bc0f552b8b4`.
Older sections listing these three jobs Off
are superseded as intended targets, not rewritten as historical test results.

The separate Apple sign-in stall was a Duskie Settings code deadlock, not a
missing account daemon: its bundle-load observer waited for main while main waited
for the rendering queue loading that bundle. A native fixture failed with the
installed library and passed with the corrected one. The focused signed library
fix was installed and then packaged/published as Duskie 0.2.71; the owner confirmed
code entry and successful sign-in. The installed package and library hash were verified.
Temporary diagnosticd was restored disabled/unregistered and owned captures stopped.
See the app audit `audit/2026-10-21-feature-repair.md` for the batch and build record.

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

# Daemon Manager reference config

## Current USB update — v1.0.21, 2026-10-13

App source is now `/home/dusk/daemon_manager`. The preset still has 512 targets, 318 Off / 194 On; the user reset the phone's native overrides during USB diagnosis, so older reduced-service phone settings below are historical. Do not silently reapply that old profile.

Overrides → USB and wired accessories groups nine computer/accessory jobs and preserves Critical lockdownd/OpenSSH during Off. Both installed menu directions passed after userspace restarts. Final group Off unloaded all nine while USB SSH, paired queries and charging still worked; no single-service cause for the earlier detection failure is proven. The other post-reset choices were left unchanged. Builtin/config defaults are unchanged.

See the [USB test record](../../daemons%20based%20on%20ios%2016.md#current-usb-update--v1021-2026-10-13) for labels, backup evidence, build/source hashes and limits. The app README records the exact Linux daemon build, foreground startup, iproxy and SSH commands. This is a tested convenience group, not a guarantee that it repairs every USB failure.


The maintained SwiftUI app is at [dusklinux/daemon_manager](https://github.com/dusklinux/daemon_manager), checked out locally at `/home/dusk/daemon_manager`. App source, build instructions, and the CI workflow belong there. The old standalone daemonmanager and processkiller instructions are retired.

This directory contains `daemon.cfg` and device-use notes. The config mirrors the repository's root `daemon.cfg` and its 512 builtin targets: **318 off, 194 on**. `yes` means disable; `no` means keep enabled; `NO` marks critical enabled targets. A recommendation is not a guarantee that every optional feature or device is safe with a service off.

The following repaired dependencies are Critical/default On in the current app (older orange classifications were superseded):

| Service | Reason |
| --- | --- |
| `com.apple.searchd` | Photos logged repeated failed Spotlight queries with sustained CPU during broad live unloading; exact single-service causality remains unproven. |
| `com.apple.biomed` | SpringBoard and keyboard clients logged Biome metadata/lockfile failures. |
| `com.apple.syncdefaultsd` | Provides `com.apple.kvsd`; multiple apps logged failed synchronized key-value-store access. |
| `com.apple.imagent` | Critical for tested Messages/account setup and carrier SMS verification; kept enabled by default. |
| `com.apple.identityservicesd` | Critical for tested Messages/account identity services; keep nehelper enabled with it. |
| `com.apple.nehelper` / `com.apple.nehelper-embedded` | Identity Services crashed repeatedly without its network interface; enabling this job restored Messages. The latter name is a plist filename alias. |
| `com.apple.mobile.lockdown` | Paired USB tools failed when disabled and worked after restoration; needed for pairing/computer backup workflows. |

Critical warnings cover core usage, measured CPU loops, crashes and dependency failures; they are not synonymous with bootloop risk. Every entry remains manually configurable. Existing explicitly customized target differences are retained when migrating the old full-copy preference format; entries that matched old builtin values adopt the revised defaults. The old format cannot distinguish an explicit choice equal to an inherited default. New choices are stored separately from builtins.

The app saves launchctl overrides and reports command/verification failures. Catalog switches load/unload their jobs immediately; broader batches require userspace restart to apply loaded-process changes fully. This addresses both demand restarts seen in testing and disruptive intermediate dependency states. The plist must retain root:wheel ownership and mode `0644` for the tested persistence workflow.

RAM displays only the number and MiB unit using kernel free_count, which already includes speculative pages. Physical free excludes speculative pages. No >600 MiB idle gain or broad battery-life improvement was verified.

Read [instructions.md](instructions.md) for usage/install notes and `../../daemons based on ios 16.md` for the full reference, test results, and limitations. No scripts, IPA builds, raw test logs, or mirrored app source are kept here.

The Critical tab always retains installed capital-NO services, even when disabled, and puts disabled entries first. The current preset has no separate Recommended On entries; red critical disables retain confirmation. All 512 builtin targets are compiled into the IPA and shown in Defaults, including absent entries; config import is optional.

## Previous profile — v1.0.20

See the [current-state summary](../../daemons%20based%20on%20ios%2016.md#current-usb-update--v1021-2026-10-13) for verified feature dependencies, normal phone settings and remaining limits. Historical sections below preserve earlier counts and classifications; their pending checks are superseded only where later tests explicitly passed.

Overrides has feature subpages and direct menu On/Off. Critical/shared prerequisites survive group Off. Screen recording requires ReplayKitAngel + replayd and respring; Lock Screen playback requires PosterBoard + liveactivitiesd and userspace restart. PosterBoard/PaperBoard/liveactivitiesd default Off. Drive/Photos sync groups returned Off after successful web-verified tests, while repaired Critical dependencies stay enabled.

Control Center/App Library switches passed both directions after resprings in installed v1.0.20 and are saved Off. The companion's settings use native `/var/mobile`, not the bootstrap account home; these flags are separate from daemon.cfg. Cosmetic dock/status-bar hooks were removed; Today View remains accessible. Memory stress supports random-filled 100 MiB increments, minus/Clear and cleanup on panel close. None of these checks establishes a net RAM/battery gain or exhaustive stress reliability. The user identifies the current A11 jailbreak as palera1n; preserve the existing no-passcode setup.

## Historical revisions

## Memory-panel correction (v1.0.3)

Services now groups critical services last; the Critical tab remains independent and puts disabled entries first. Tapping RAM opens live memory details, including physical compressor occupancy and original page size. XNU `free_count` already includes speculative pages: the old app's sum double-counted them. The header now uses `free_count`; physical free is `free_count - speculative_count`. Treat earlier free-plus-speculative sums as historical calculations, not unique physical pages or proof of a memory gain.

System pressure is Normal/Warning/Critical, seeded from the privileged current-pressure sysctl when the panel opens and followed through native Dispatch notifications. Do not interpret a free-RAM percentage as pressure. The verified sysctl exports flags 1/2/4, not internal VM levels 0/1/3. VM statistics on the tested phone returned 38 words with a 16 KiB page size; discover these dynamically. Details do not apply daemon defaults or change overrides.

## Wallpaper host and storage (v1.0.4)

The verified PosterBoard job is `/System/Library/LaunchAngels/com.apple.PosterBoard.plist`, label `com.apple.PosterBoard`, domain `user/501` on this phone (the app discovers the mobile UID). Killing its PID does not save an override. Native disable plus bootout stopped it and most associated poster extensions in the observed window. A userspace reboot preserved the off override and left no PosterBoard/poster-extension processes running. The user confirmed unlock, Home, Notes and Messages work after this reboot. Battery impact and long-term stability remain unmeasured. The app exposes this job under Tools → Background UI and in Services, together with the tested AccessibilityUIServer host. Other untested LaunchAngels remain excluded. The compiled default keeps it enabled for other users; manual disabling remains available, and userspace restart fully applies it. No auto-kill daemon, polling loop, file renaming, or arbitrary app blacklist is installed.

Speculative pages are reclaimable kernel filesystem cache, not a separately disableable SysMain service. Disabling predictive daemons does not eliminate all speculative pages. No useful verified force-compress control was found; no induced memory pressure or forced app eviction is implemented. Core account daemons and siriactionsd retain their existing critical warnings; feature names alone do not establish safe disabling.

The storage driver advertises NVMe SMART capability, but read-only IOCreatePlugInInterfaceForService returned `0xe00002c7` on this device; lifetime writes/endurance/wear could not be obtained. The old capacity section was removed in v1.0.5; current disk health uses on-demand ASP diagnostic reports. No terabyte-write lifetime or rated endurance is invented.

## Audit follow-up and app revision (v1.0.6)

Tools → Background UI now exposes PosterBoard and AccessibilityUIServer only. AccessibilityUIServer is **KEEP / Recommended On**, not capital NO; its builtin target is enabled. It remains freely switchable, including in Services. The tested phone retains its manual off override. Re-enable and restart userspace before using accessibility features. Installation never applies defaults. The config and IPA contain **506 targets: 320 off, 186 on**; biomed, syncdefaultsd and Messages dependencies remain enabled by default.

Basic usage passed on the tested phone after host disabling and reboot. Remaining CoreDuet, graphics, preferences and account errors were not eliminated; no battery-life or net RAM improvement was established. See the iOS reference for the measured audit and its limits.

Animation controls and their helper were removed from the app. The independently installed animation tweak is left active and managed outside this app. RAM remains memory-only. Disk write counters use grouped raw numbers and can be tapped for their meaning and conversion status. GB conversion remains unavailable: the ASP snapshot and installed diagnostic parser did not establish bytes per count; page size or generic NVMe units cannot substitute for that evidence. No guessed conversion is shipped.

The unsupported absd Contacts description and related database links were removed. Its actual plist exports com.apple.absinthed and com.apple.absd; the broader private role remains undocumented here. Verified Contacts database links remain for contactsd and ABDatabaseDoctor.

## Current v1.0.8 behavior (2026-10-12)

The matching config contains 512 targets: 325 off, 187 on. `yes` disables, `no` enables, and uppercase `NO` marks critical enabled targets. Pkd, nehelper and its filename alias, searchd, biomed, syncdefaultsd and mobile.lockdown are Critical under the user's performance/dependency policy; older KEEP/orange classifications in the historical sections are superseded. Searchd's single-service CPU causality remains unproven. AccessibilityUIServer is optional and has no Recommended On badge; its existing On builtin and manually switchable Off choice are retained.

Overrides replaces the Config tab and contains the tested optional UI hosts, screen-recording dependencies and widget-update controls. Tools → Import / Export Config contains configuration actions. Descriptions are visible without expansion; detailed warnings and metadata remain expandable. Single catalog switches load/unload their job immediately and verify the saved/loaded states. Other batches still require userspace restart.

Allow AirDrop controls the native allowAirDrop feature setting for sending and receiving while sharingd stays on for the normal Share Sheet. The on-demand entitled helper temporarily uses profiled and restores its previous override/job state; it does not install a profile or directly edit a policy database. Screen Recording requires both ReplayKitAngel and replayd On, and is separate from Camera video capture. The user wants recording services normally Off and ShortcutsUI On.

See the parent iOS 16 reference's 2026-10-12 section for the isolated pkd CPU loop, optional-host tests, recovered Share Sheet and remaining WhatsApp Meta AI Auto Shortcut failure. No idle RAM saving is claimed for hosts that had no running process. Reference changes remain local in the bare dotfiles work tree.

### v1.0.9 feature groups

Overrides adds 13 group On/Off controls over 40 checked installed job labels. Off preserves Critical and shared prerequisites; individual switches remain available. Broader account/sync groups require userspace restart and are not fully feature-tested. Screen Recording passed with unrelated hosts off after enabling ReplayKitAngel/replayd and respringing; both are normally off by request. See the parent reference’s final verification section for the packaged AirDrop restoration test, user UI checks, 0.08% SpringBoard sample and remaining widget/WhatsApp limitations.

## Current account/SMS repair — v1.0.13

Overrides uses feature subpages; navigation was user-tested. The successful account profile restores appleaccountd/akd/adid, absd for NAC initialization, imagent for SMS, and cdpd/followupd/identityservicesd with a userspace restart. The final three were restored together; individual causality was not isolated. These authentication/SMS jobs are protected Critical dependencies in account/Messages group Off actions. Individual switches remain editable.

Config now has 512 targets, 322 Off / 190 On. Absd/cdpd/followupd default On and Critical entries use capital NO. This supersedes older counts and Messages recommendations. See the parent iOS 16 reference’s v1.0.13 section for logs, staged repair evidence and remaining functional-test limits. App/notes configs match; reference edits remain local.

### Photos sharing repair — v1.0.14

ImageConversionService and VideoConversionService each caused a separately logged Photos export failure while disabled. Restoring each in turn and respringing repaired user-confirmed WhatsApp image/video sharing. Both are Critical/default On; individual switches remain editable and group Off preserves them. Current config: 512 targets, 320 Off / 192 On. See the parent iOS 16 reference for the comparisons and limits.

### App Store repair — v1.0.15

Restoring passd plus respring repaired free App Store confirmation, download, install and launch; PassbookUISceneService stayed off. A second new free app passed with amsengagementd off again. Passd is Critical/default On and a shared Store prerequisite; Wallet group Off preserves it. Paid purchases/Apple Pay remain untested. FaceTime calls were unavailable to test. Current config: 512 targets, 319 Off / 193 On. See the parent reference for staged comparisons and deployment results.

### Cloud key dependencies and menu buttons — v1.0.15

ProtectedCloudKeySyncing and SecureBackupDaemon are Critical shared account/Drive/Photos prerequisites after logged key-recovery failures; restoring both and retrying completed key synchronization and cleared the user’s account notice. Drive listing/upload/web download passed after restart; a first Save to Files attempt returned Home and Photos upload remains under investigation. Contextstored is only a temporary comparison so far. Overrides adds menu On/Off beside each subpage link. Current config: 512 targets, 318 Off / 194 On. See the parent reference for evidence, deployment and remaining checks.

### Photos scheduler comparison — v1.0.16

Photos synced two web-verified photos with contextstored restored. Turning only it off and restarting reproduced a new upload stall with failed context reads and explicit DAS network-quality/data-budget rejection. Contextstored is now Critical/default On and a Photos prerequisite with dasd. Current config: 512 targets, 317 Off / 195 On. The user chose Drive/Photos sync Off again after tests, preserving Critical dependencies. See the parent reference for the final recovery and deployment checks.

### Final v1.0.16 verification

The same waiting third photo synced after restoring contextstored again. Repeated Drive export and fresh-restart cloud checks passed after app menu On actions; menu Off actions plus restart returned the four sync jobs disabled/unloaded and preserved Critical dependencies. AirDrop remained Off, temporary diagnostics/profiled were restored Off, and only 12 repaired Critical enable changes remained versus baseline. Settled SpringBoard CPU: 0.015 s / 15.00 s = 0.10%, thermal pressure 0. Current config remains 512 targets, 317 Off / 195 On. See the parent reference for deployment hashes, precise tests and unverified cases. Existing user staging is preserved.


### Lock Screen playback hosts — 2026-10-12, v1.0.17

PosterBoard is also a tested Lock Screen/Notification Center media-rendering dependency, not only a wallpaper host. The tested working set is PosterBoard + liveactivitiesd, with PaperBoard still Off; Control Center playback worked with all three Off. liveactivitiesd On restored the panel, Off/respring removed it again, and liveactivitiesd alone produced a flashing grey panel with repeated MediaRemoteUI CALayerInvalidGeometry exceptions. Adding PosterBoard and restarting userspace restored clear controls and a running UI process. Resprings did not reliably clear that failure state.

The user explicitly requests complete group On/Off and default Off for these optional hosts. Both therefore remain outside Critical protection, and PosterBoard's builtin/config target changes to `yes`; liveactivitiesd and PaperBoard already use `yes`. Overrides now has a Lock Screen media controls group for PosterBoard/liveactivitiesd; widget updates controls chronod separately. Wallpaper still shares PosterBoard, so switching Wallpaper Off can break media rendering while liveactivitiesd remains On. Restart userspace after media-group changes. Current config: 512 targets, 318 Off / 194 On; existing Critical entries retain uppercase NO. No RAM or battery gain is claimed.


The installed v1.0.17/build 18 menu controls passed an Off → userspace restart → absent panel test, followed by On → userspace restart → clear working panel, with PaperBoard still Off and MediaRemoteUI running with one launch. A final menu Off and restart returned all three hosts disabled/unloaded. Temporary diagnostics were restored Off; the final system/user-501 disabled maps exactly matched the saved profile from before this investigation. A 5.00-second final sample measured SpringBoard CPU at 0.015 seconds (0.30% of one core), footprint 60.92 MB; this is not a comparative net-RAM or battery benchmark.

Build source: 345755614b69698b222f8deb13affc510369b964; Actions run 38235699744 passed the 20 Python checks, Swift group/memory/stress checks and iOS compilation/packaging. IPA SHA256 8d04b27e62cd4463828392659e21f41323003f81ecafef263c6e42ca7c3a39a4 matched the release digest and embedded source/version. TrollStore installation returned 0 and the installed version/source matched. App and notes configs match 512 targets, 318 Off / 194 On. Notes were left unstaged for user review.


### SpringBoard interface controls — v1.0.20

Control Center and App Library have no separate launchd jobs in this phone's inspected configuration. The app deploys a minimal SpringBoard-only rootless companion to block their presentation/capability methods, with individual and menu On/Off switches, saved-versus-current-PID reporting and removal. Respring applies changes. This does not establish a reduction in allocated RAM. All 512 daemon targets remain unchanged (318 Off / 194 On).

The prototype passed Off/On checks, but packaged v1.0.18 wrote settings to the bootstrap mobile home (/var/jb/var/mobile) instead of SpringBoard's native /var/mobile. It therefore saved Off without applying it. The corrected script uses the native path and account UID/GID for ownership; after a respring the runtime report showed all requested hooks active, and the user confirmed the interfaces were gone. A regression test covers distinct bootstrap/native homes.

The user requested removing dock/status-bar controls because they only hide views; those switches and layout hooks were removed and both restored. The remaining switches block access to Control Center/App Library; RAM savings remain unverified. Attempts to remove Today View did not work and their hooks were removed, with the user's agreement. Keep pkd and repaired Critical services enabled. No jetsam-limit change or persistent memory stress allocation was introduced.


The installed v1.0.20/build 21 app passed All On → respring → both interfaces available, followed by Off → respring → both inaccessible, confirmed by the user. Dock/status bar were restored and their switches removed. Final saved settings and current-PID runtime report both have Control Center/App Library disabled. CI run 38244546744 passed 23 Python tests, Swift memory/stress/group checks and iOS build/package. Source ca663064295eb14b72a669ececcfcf37eeda7277; IPA SHA256 35e71d9caee42bc50b7e251d69633b10420ebf44ca925c2fbb101d33c6d4bdca matched the release digest and embedded source/version; TrollStore returned 0 and the app process was observed running. Both saved launchd domains matched the pre-interface profile exactly. A settled 10-second sample measured SpringBoard at 0.010 CPU seconds (0.10% of one core), footprint 64.13 MB. That is not a comparative RAM/battery benchmark or exhaustive stress proof.
