# Device use and app installation

## Current USB update — v1.0.21, 2026-10-13

App source is now `/home/dusk/daemon_manager`. The preset still has 512 targets, 318 Off / 194 On; the user reset the phone's native overrides during USB diagnosis, so older reduced-service phone settings below are historical. Do not silently reapply that old profile.

Overrides → USB and wired accessories groups nine computer/accessory jobs and preserves Critical lockdownd/OpenSSH during Off. Both installed menu directions passed after userspace restarts. Final group Off unloaded all nine while USB SSH, paired queries and charging still worked; no single-service cause for the earlier detection failure is proven. The other post-reset choices were left unchanged. Builtin/config defaults are unchanged.

See the [USB test record](../../daemons%20based%20on%20ios%2016.md#current-usb-update--v1021-2026-10-13) for labels, backup evidence, build/source hashes and limits. The app README records the exact Linux daemon build, foreground startup, iproxy and SSH commands. This is a tested convenience group, not a guarantee that it repairs every USB failure.


The app source and build workflow live in `/home/dusk/daemon_manager` and [dusklinux/daemon_manager](https://github.com/dusklinux/daemon_manager). This notes directory keeps references and a matching config, not source/build copies or the retired daemonmanager/processkiller scripts.

On the tested rootless iOS 16 phone, OpenSSH and Python 3.9.9 are available. Use the phone's actual SSH address and root credentials; do not assume the old notes' address or Python version on another device.

1. Build from the repository's GitHub Actions workflow, or generate its Xcode project with XcodeGen on macOS and build the Release iphoneos target.
2. Obtain the IPA from the successful run for the intended commit. The mutable latest release alone does not identify a particular source revision.
3. Transfer the IPA over SSH. Install with TrollStore Lite's `trollstorehelper install force PATH` on the tested phone; its location is `/var/jb/Applications/TrollStoreLite.app/trollstorehelper`. Discover the path on other installations. AppSync Unified with `appinst PATH` is an alternative when present.
4. Launch Daemon Manager. Known descriptions are visible in collapsed rows. Tapping a service's name/chevron expands warnings and metadata; its separate switch changes the saved override. The Defaults tab edits targets; Apply Defaults saves them to launchd.
5. Restart userspace from Tools when ready to apply process changes fully. `launchctl reboot userspace` was exercised on this device. A restart interrupts apps and SSH.

`daemon.cfg`: `yes` means disabled override; `no` means enabled override; `NO` marks critical enabled targets. Import it through Tools → Import / Export Config to inspect/apply it. Critical entries are kept on in builtin targets, but manual disabling is still allowed with the existing warning flow. Custom target choices take precedence.

Keep searchd, biomed, syncdefaultsd, and mobile.lockdown enabled under the revised preset. Their warnings reflect measured dependency errors/functionality loss, not new bootloop claims. In particular, restore mobile.lockdown before computer pairing/backup workflows. USB charging and port-forwarded SSH are separate.

The app uses native launchctl persistence and corrects the disabled plist to root:wheel `0644`; it no longer rewrites that database directly. An emergency launchd reset resets saved overrides, not phone content, and needs a userspace restart. It is not an exact restoration of an earlier override configuration.

See the parent iOS 16 reference for measured limits, known critical services, and the 2026-10-11 test results. Do not treat the absence of crash reports as proof of stability when crash collectors are off.

## Categories and built-in defaults

The IPA includes all 512 curated targets (318 off, 194 on); sharing it does not require a config import. Defaults also shows targets not found on the current phone; Apply Defaults acts on installed services only. Editing a target does not change the phone until applied.

Critical is a separate tab for the existing capital-NO services. Disabled entries sort first and remain members of that tab. Critical disable actions retain confirmation. The current preset has no separate Recommended On entries.

Keep com.apple.imagent, com.apple.identityservicesd, and com.apple.nehelper enabled for Messages; the nehelper-embedded filename alias also defaults On. Disabling nehelper while Identity Services runs caused repeated aborts; enabling it restored the conversations. Nehelper also supports other Network Extension clients. These are Critical dependency classifications under the current policy, not newly established bootloop claims.

The RAM indicator shows only a value and MiB; the header uses kernel free_count, which already includes speculative pages.

## Controls verified in v1.0.20

Overrides provides one subpage per feature and On/Off buttons directly on the feature menu. Group On restores listed jobs and known prerequisites; Off preserves Critical and dependency-only shared jobs. Individual Critical switches remain editable with warnings. Catalog switches load/unload their jobs; broader batches still need userspace restart. Older versioned sections below describe history, including superseded tab locations and classifications. Use the [current-state summary](../../daemons%20based%20on%20ios%2016.md#current-usb-update--v1021-2026-10-13) for the final profile and test limits.

- Screen recording: turn its group On and respring before use; Off restores the requested normal state. Camera recording is separate.
- Lock Screen media controls: PosterBoard + liveactivitiesd, then restart userspace. The group defaults Off; PaperBoard was not required. Wallpaper shares PosterBoard, so disabling Wallpaper can also break media rendering.
- iCloud Drive/Photos: turn the needed sync groups On, restart userspace, then verify sync. Both feature groups were tested and returned Off by request; Critical account/key/context/scheduler dependencies stay enabled.
- SpringBoard interfaces: only Control Center and App Library are available. On/Off applies after respring; both directions were verified. Final phone choices are Off. Dock/status-bar controls were removed because they only hid views; Today View remains accessible. These controls use `/var/mobile/Library/Preferences/com.dusklinux.DaemonManagerUI.plist`, not daemon.cfg or the bootstrap passwd home. Remove interface controls followed by respring removes the companion.
- Memory: +100 MiB commits fresh random-filled memory; −100 MiB releases the last block, Clear releases all blocks and closing the panel clears it. Allocation/footprint/headroom readings are available. This is a stress test, not additional physical RAM or guaranteed cache cleanup; no jetsam exemption is installed.

The native AirDrop policy is Off, with sharingd On. Wallet interface Off preserves Critical passd for App Store confirmation. AccessibilityUIServer has no Recommended On badge; its builtin remains On and the phone's manual Off choice is retained. Current Critical classifications supersede earlier orange recommendations for pkd, Messages/network, account, cloud-key, scheduling and Photos conversion dependencies. Keep those dependencies enabled. Preserve the user's palera1n/no-passcode setup; do not enable a device passcode as part of these controls.

## Historical revisions

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

The tested Lock Screen/Notification Center media group needs PosterBoard + liveactivitiesd, with PaperBoard still Off. Keep all three optional/default Off as explicitly requested; complete group Off must remain available. PosterBoard is shared with Wallpaper, while widget updates now controls chronod separately. Restart userspace after media-group changes. Config now matches 512 targets, 318 Off / 194 On; existing Critical NO entries are unchanged. See the [daemon test notes](README.md) for the On/Off comparison and recorded MediaRemoteUI rendering failure.
