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
