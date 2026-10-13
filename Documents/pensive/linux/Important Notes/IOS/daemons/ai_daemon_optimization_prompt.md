# Agent context: iOS 16 daemon testing and Daemon Manager

## Current USB update — v1.0.21, 2026-10-13

App source is now `/home/dusk/daemon_manager`. The preset still has 512 targets, 318 Off / 194 On; the user reset the phone's native overrides during USB diagnosis, so older reduced-service phone settings below are historical. Do not silently reapply that old profile.

Overrides → USB and wired accessories groups nine computer/accessory jobs and preserves Critical lockdownd/OpenSSH during Off. Both installed menu directions passed after userspace restarts. Final group Off unloaded all nine while USB SSH, paired queries and charging still worked; no single-service cause for the earlier detection failure is proven. The other post-reset choices were left unchanged. Builtin/config defaults are unchanged.

See the [USB test record](daemons%20based%20on%20ios%2016.md#current-usb-update--v1021-2026-10-13) for labels, backup evidence, build/source hashes and limits. The app README records the exact Linux daemon build, foreground startup, iproxy and SSH commands. This is a tested convenience group, not a guarantee that it repairs every USB failure.

## Previous handoff — v1.0.20, 2026-10-12

Read the [current-state summary](daemons%20based%20on%20ios%2016.md#current-usb-update--v1021-2026-10-13) first. Versioned sections below are historical; their older counts, KEEP badges and pending checks must not override later verified results. Current config: **512 targets, 318 Off / 194 On**, with all 89 Critical enabled targets uppercase `NO`; the config matches app builtins, not every custom phone setting.

The user identifies the current jailbreak as palera1n on A11, without a device passcode. Earlier Dopamine wording is superseded. Preserve this setup. Native SpringBoard preferences/report paths are under `/var/mobile`; the bootstrap passwd home `/var/jb/var/mobile` is different. This mismatch caused packaged v1.0.18 Off actions to save without applying; v1.0.20 fixes it and adds a regression test.

Lock Screen playback requires tested PosterBoard + liveactivitiesd, with PaperBoard Off, and userspace restart. Those three hosts default Off and the user wants the media group normally Off. Keep pkd and repaired Critical account/key/scheduling/conversion/PassKit dependencies enabled; group Off preserves them. Fresh-restart iCloud Drive/Photos tests passed and their sync jobs returned Off by request. Menu On/Off and subpages were tested.

V1.0.20 Control Center/App Library switches passed both directions after resprings; final choices are Off. They block UI access, with RAM savings unverified. Cosmetic dock/status-bar hooks were removed and both restored; failed Today View hooks were removed too. Memory stress allocates real random-filled memory in 100 MiB increments and releases it on minus/Clear/panel close; it does not create RAM or remove jetsam limits. Allocation/release controls passed user checks, not exhaustive system stress.

Previous build: v1.0.20/build 21, source `ca663064295eb14b72a669ececcfcf37eeda7277`; CI passed 23 Python checks, Swift checks and iOS packaging. See the parent reference for release hashes, measured limits and feature-specific evidence. FaceTime calls, paid purchases/Apple Pay, all combinations and net RAM/battery gains remain unverified; the WhatsApp Meta AI action remains unresolved.

## Scope and maintained files

App repository: `/home/dusk/daemon_manager`, remote `git@github.com:dusklinux/daemon_manager.git`, main branch. The repository is the sole app source/build location; its root `daemon.cfg` mirrors `scripts/daemons/daemon.cfg` in these notes. Do not recreate duplicate app sources or old daemonmanager/processkiller scripts here.

Keep these five references: this prompt, `daemons based on ios 16.md`, and `scripts/daemons/{daemon.cfg,instructions.md,README.md}`. The raw 2026-10-11 workstation test artifacts were deleted after the results were incorporated into the reference. Device-side originals/backups were left under `/var/mobile/Documents/daemon-test-2026-10-11`.

Do not stage, commit, reset, or otherwise alter `$HOME/dusky`. Only commit/push app changes when the user asks. The user controls authorization for changing critical services, deployment, rebooting, and cleanup.

## Verified environment (2026-10-11)

- iPhone 8 Plus, iPhone10,2, iOS 16.7.16 **20H392**, Dopamine rootless (`/var/jb`). Discover actual versions and paths again on future sessions.
- Tested SSH address `192.168.29.119`, root/mobile access; credentials are supplied by the user/session. Use prompted or stdin authentication, not passwords embedded in shell arguments or committed code.
- `/var/jb/usr/bin/python3` is **3.9.9**, not the 3.11–3.14 versions previously assumed. Keep the app's embedded Python compatible with the actual device runtime.
- launchctl resides at `/var/jb/usr/bin/launchctl`; uiopen and appinst are available. TrollStore Lite helper: `/var/jb/Applications/TrollStoreLite.app/trollstorehelper`.
- `/usr/bin/log` is absent. The shell's `log` is not the unified-log CLI. Test-time live capture used an extracted oslog binary; retained archives were decoded on Linux. Paired USB tools need mobile.lockdown and diagnostic collection needs diagnosticd.
- `sysdiagnose -f /var/tmp/ -n` was exercised with its interactive Enter confirmation. The output path is printed on completion; do not assume a started command means a successful collection.

## Baseline findings and revised defaults

The old 328-entry off preset was tested over roughly 64 minutes: 321 installed targets, seven absent names/aliases. Seven installed entries were initially unregistered. The first userspace reboot failed to retain overrides with the plist owned by mobile:mobile and mode `01204`. Correcting it to root:wheel `0644` and reapplying through launchctl preserved all 321 after the second reboot. Do not rewrite this database manually for routine toggles; let launchctl manage content.

Disable plus killing a loaded process did not prevent demand restarts. Live test bootout exposed intermediate CPU problems in duetexpertd and assetsd. Duetexpertd's trigger was not isolated; unloading it settled the workload. Photos logged thousands of failed Spotlight-query retries during broad live unloading, but fresh full-off boot and a short searchd on/off comparison did not reproduce the loop. Do not present single-service causality as proven.

The revised builtin/config list has **506 entries, 320 off, 186 on**, with searchd, biomed, syncdefaultsd, and mobile.lockdown kept enabled. The latter three have direct Biome/KVS/USB failure evidence; searchd is a conservative dependency recommendation. They receive orange RECOMMENDED ON guidance, distinct from red CRITICAL warnings; recommendations toggle directly, while critical disables retain confirmation. Do not newly classify duetexpertd as unsafe to disable solely because its enabled process ran hot.

Critical services from the reference remain on, including mobile.usermanagerd, nanoregistryd, sleepd, rapportd, siriactionsd, nesessionmanager, misagent, FairPlay services, assetsd, OpenSSH, SpringBoard, backboardd, and core networking/security/lifecycle services. Do not disable them automatically without explicit user authorization.

User-confirmed unlock, Notes typing, Camera, Photos, and Safari passed after the full-off reboot. HTTPS and Wi-Fi/USB SSH worked; core processes remained present. The retained post-reboot window had 558,096 records including 215 faults and three Loss records. There was no actual panic/watchdog-timeout/EXC_BAD_ACCESS/SIGSEGV in that reviewed window, but logs were not clean and disabled crash reporters limit negative evidence. An earlier diagnosticd report was simulated/nonfatal.

Baseline median physical free RAM was 215.4 MiB versus 121.2 MiB during the five-minute post-reboot workload; activities differed. Free+speculative medians were 364.7/177.1 MiB, compressor occupancy 351.6/328.1 MiB. Do not claim an isolated memory gain, battery improvement, or the >600 MiB idle goal. CPU proc_pidinfo counters require Mach timebase conversion; this A11 measured 125/3 ns per tick. Summed process RSS includes shared memory.

## App behavior and future work

The tab bar explicitly uses opaque standard and scroll-edge appearances from initial launch. Service labels/chevrons expand details independently from their switches. Details include known descriptions, plist/executable paths, and verified Contacts-related database metadata for relevant services. Unknown private roles are omitted; do not invent descriptions or unrelated database paths.

Builtin targets and explicit custom choices are separated. Legacy saved values matching old builtins migrate as inherited values; explicit differences are preserved. Routine operations save and verify native overrides, retain valid plist metadata, and ask for a userspace restart to fully apply process changes. Do not promise immediate RAM reduction or silently hot-unload dependencies. Batch failure must never produce an all-success banner. Label progress counts completed lines, not arbitrary stdout chunks.

On Linux, use the repository's GitHub Actions macOS workflow for the actual Swift/iOS build. Download the successful intended commit's IPA artifact/release, verify its provenance, install through TrollStore Lite or AppSync when authorized, and exercise app launch. Keep scope focused; do not include private phone logs, database contents, credentials, or duplicate sources in the repository.

## Messages recovery / current category behavior

The user confirmed conversations returned after enabling com.apple.nehelper alongside imagent and identityservicesd. SQLite quick_check passed; 247 messages and 79 conversations remained. Identity Services had repeatedly aborted at IDSGroupAgent registerAgent / self.interfaceName with nehelper unavailable; the process stayed running after restoration. Nehelper is not exclusive to Messages. Keep the three actual jobs and the nehelper-embedded filename alias enabled in builtin targets as recommendations, not new critical bootloop claims.

Current compiled defaults: 506 entries, 320 off, 186 on. Orange recommended-on services toggle directly without critical confirmation. Red critical services retain confirmation and a separate Critical tab whose membership never depends on switch state. Existing capital-NO classifications are preserved. Defaults includes absent builtin targets as well as installed services; fresh app installations need no config import. RAM displays just the value/unit using kernel free_count, which already includes speculative pages. Installing the app must not silently apply defaults.

## Memory-panel correction (v1.0.3)

Services now groups critical services last; the Critical tab remains independent and puts disabled entries first. Tapping RAM opens live memory details, including physical compressor occupancy and original page size. XNU `free_count` already includes speculative pages: the old app's sum double-counted them. The header now uses `free_count`; physical free is `free_count - speculative_count`. Treat earlier free-plus-speculative sums as historical calculations, not unique physical pages or proof of a memory gain.

System pressure is Normal/Warning/Critical, seeded from the privileged current-pressure sysctl when the panel opens and followed through native Dispatch notifications. Do not interpret a free-RAM percentage as pressure. The verified sysctl exports flags 1/2/4, not internal VM levels 0/1/3. VM statistics on the tested phone returned 38 words with a 16 KiB page size; discover these dynamically. Details do not apply daemon defaults or change overrides.

## Wallpaper host and storage (v1.0.4)

The verified PosterBoard job is `/System/Library/LaunchAngels/com.apple.PosterBoard.plist`, label `com.apple.PosterBoard`, domain `user/501` on this phone (the app discovers the mobile UID). Killing its PID does not save an override. Native disable plus bootout stopped it and most associated poster extensions in the observed window. A userspace reboot preserved the off override and left no PosterBoard/poster-extension processes running. The user confirmed unlock, Home, Notes and Messages work after this reboot. Battery impact and long-term stability remain unmeasured. The app exposes this job under Tools → Background UI and in Services, together with the tested AccessibilityUIServer host. Other untested LaunchAngels remain excluded. The compiled default keeps it enabled for other users; manual disabling remains available, and userspace restart fully applies it. No auto-kill daemon, polling loop, file renaming, or arbitrary app blacklist is installed.

Speculative pages are reclaimable kernel filesystem cache, not a separately disableable SysMain service. Disabling predictive daemons does not eliminate all speculative pages. No useful verified force-compress control was found; no induced memory pressure or forced app eviction is implemented. Core account daemons and siriactionsd retain their existing critical warnings; feature names alone do not establish safe disabling.

The storage driver advertises NVMe SMART capability, but read-only IOCreatePlugInInterfaceForService returned `0xe00002c7` on this device; lifetime writes/endurance/wear could not be obtained. The old capacity section was removed in v1.0.5; current disk health uses on-demand ASP diagnostic reports. No terabyte-write lifetime or rated endurance is invented.


## Instant system transitions and disk health (v1.0.5)

The user clarified that animation removal means system transitions (app open/close, switcher, folders), not animations inside apps. Installed the developer's NoAnimationsX 1.2 rootless package (SHA256 `ddfbeb477b0349a86e41b2e3c66a731724763cd7d15d9147ced035106bef63b1`). Its filter injects only into SpringBoard; inspection showed suppression of animation actions, not a speed multiplier. After sbreload, the user confirmed all listed transitions instant and unlocking/touch work. Do not claim independently tested coverage of every system transition. The v1.0.5 control was removed in v1.0.6; leave the independently managed tweak and its active filter untouched. Other users install this optional dependency in Sileo; its binary is not redistributed in the IPA. No background animation worker is added.

The requested capacity section has been removed from the RAM panel. The older sysdiagnose did contain NAND health despite the failed SMART interface probe. A fresh `sysdiagnose -f /var/tmp/ -n` also produced `ASPSnapshots/asptool_snapshot.log` with percentUsed 9, TLC average erase cycles 92/native endurance cycles 1000, SLC average 8278/endurance 45000, zero critical warnings/grown bad/retired blocks/uncorrectable reads. Write counters are present, but their units were not established independently; show raw values rather than guessed TB or rated TBW. Wear is a driver counter, not a remaining-life guarantee.

Tools → Disk diagnostics reads the latest completed report or collects a new one on demand under `/var/tmp/daemon-manager-diagnostics`. Tested the actual collection helper end to end with sysdiagnose and sysdiagnose_helper originally disabled; temporary enabling/loading and final disabling/bootout restored every prior override exactly. diagnosticd was temporarily enabled during initial investigation and restored too; the app's verified collection only needs the two sysdiagnose jobs. Enter confirmation requires a real PTY; piping newline to the CLI without a terminal exited 64. Collection takes several minutes; keep the app open. Reports stay on-device and are not uploaded. Missing fields or collection failures must remain unavailable, not fall back silently to older data or zero.

The final v1.0.5 IPA from commit `730a3d838efbdc28be9fa9d12e7ab1235f16d9cd` passed all 13 Python tests, Swift memory checks, and the iOS build (Actions run 38121962687). Its asset digest, embedded source revision, and AppIcon metadata were checked before TrollStore Lite installation. Installed Info.plist matches that commit; all launchd overrides remain unchanged and PosterBoard stays off. The supplied frog sticker was prepared with built-in imagegen as a square opaque icon under the app asset catalog.
The user confirmed the installed frog icon, animation switch on, latest diagnostic wear at 9%, and capacity removed from RAM. Both provided disk-health screenshots were reviewed: labels/counters match the parsed report and remain readable, with snapshot provenance and unverified write-unit limits displayed.

## Audit follow-up and app revision (v1.0.6)

Tools → Background UI now exposes PosterBoard and AccessibilityUIServer only. AccessibilityUIServer is **KEEP / Recommended On**, not capital NO; its builtin target is enabled. It remains freely switchable, including in Services. The tested phone retains its manual off override. Re-enable and restart userspace before using accessibility features. Installation never applies defaults. The config and IPA contain **506 targets: 320 off, 186 on**; biomed, syncdefaultsd and Messages dependencies remain enabled by default.

Back Tap, Speak Screen and the general accessibility setting were verified off across userspace reboot. AccessibilityUIServer used about 0.002% of one core with those settings off, before its host was disabled; its roughly 23 MiB RSS includes shared pages and is not a guaranteed net RAM gain. The user repeated Home, unlock, Notes, Camera and Messages checks successfully after disabling it and rebooting. All accessibility features and actual iMessage delivery were not independently tested. Disabling this host produces expected unavailable-host endpoint messages.

biomed and syncdefaultsd were restored on the phone. Final reviewed errors no longer explicitly named their missing Biome/KVS endpoints. Restoring contextstored, duetexpertd and coreduetd did not eliminate redacted context failures, so all three were returned off. Remaining CoreDuet, graphics, preferences and account errors are unresolved; no claim of error-free operation is justified. Backboardd averaged 12.46% of one core on Home and 0.514% locked in the final samples. Pressure remained Normal, with no observed panic/watchdog reboot/thermal emergency in the reviewed window. Privacy redactions, three lost-log records and disabled crash reporters limit these findings. No battery-life or permanent CPU improvement was established.

Animation controls and their helper were removed from the app. The independently installed animation tweak is left active and managed outside this app. RAM remains memory-only. Disk write counters use grouped raw numbers and can be tapped for their meaning and conversion status. GB conversion remains unavailable: the ASP snapshot and installed diagnostic parser did not establish bytes per count; page size or generic NVMe units cannot substitute for that evidence. No guessed conversion is shipped.

The unsupported absd Contacts description and related database links were removed. Its actual plist exports com.apple.absinthed and com.apple.absd; the broader private role remains undocumented here. Verified Contacts database links remain for contactsd and ABDatabaseDoctor.

Deployment verification: v1.0.6 / build 7 from commit `2539b452c68af93be6259d63c0132cd27bab7194`, Actions run `38126774901` passed 11 Python tests, Swift memory checks and iOS build. Release asset SHA256 `ba360f3a5208712453c0a092120b82f530a0ffdfe362879121b4bf16546879f4` matched the download and embedded source revision. TrollStore Lite install returned 0, installed Info.plist matches this revision, uiopen succeeded, and all 333 launchd overrides plus active NoAnimationsX filter remain identical. Reference notes are updated locally; only the app repository was committed/pushed.
The user confirmed all v1.0.6 checks: animation switch absent, both UI hosts off, grouped write counters and explanation working. A final post-launch comparison again preserved every override and the active animation filter.

## Current v1.0.8 behavior (2026-10-12)

The matching config contains 512 targets: 325 off, 187 on. `yes` disables, `no` enables, and uppercase `NO` marks critical enabled targets. Pkd, nehelper and its filename alias, searchd, biomed, syncdefaultsd and mobile.lockdown are Critical under the user's performance/dependency policy; older KEEP/orange classifications in the historical sections are superseded. Searchd's single-service CPU causality remains unproven. AccessibilityUIServer is optional and has no Recommended On badge; its existing On builtin and manually switchable Off choice are retained.

Overrides replaces the Config tab and contains the tested optional UI hosts, screen-recording dependencies and widget-update controls. Tools → Import / Export Config contains configuration actions. Descriptions are visible without expansion; detailed warnings and metadata remain expandable. Single catalog switches load/unload their job immediately and verify the saved/loaded states. Other batches still require userspace restart.

Allow AirDrop controls the native allowAirDrop feature setting for sending and receiving while sharingd stays on for the normal Share Sheet. The on-demand entitled helper temporarily uses profiled and restores its previous override/job state; it does not install a profile or directly edit a policy database. Screen Recording requires both ReplayKitAngel and replayd On, and is separate from Camera video capture. The user wants recording services normally Off and ShortcutsUI On.

See the parent iOS 16 reference's 2026-10-12 section for the isolated pkd CPU loop, optional-host tests, recovered Share Sheet and remaining WhatsApp Meta AI Auto Shortcut failure. No idle RAM saving is claimed for hosts that had no running process. Reference changes remain local in the bare dotfiles work tree.

### v1.0.9 feature groups

Overrides adds 13 group On/Off controls over 40 checked installed job labels. Off preserves Critical and shared prerequisites; individual switches remain available. Broader account/sync groups require userspace restart and are not fully feature-tested. Screen Recording passed with unrelated hosts off after enabling ReplayKitAngel/replayd and respringing; both are normally off by request. See the parent reference’s final verification section for the packaged AirDrop restoration test, user UI checks, 0.08% SpringBoard sample and remaining widget/WhatsApp limitations.

Deployment: v1.0.9 / build 10, source `b40f9cbccb871b0e07b6922b8275275d143236d3`, Actions run `38213950882` succeeded (19 Python tests, Swift memory/group policy checks, iOS build and packaging). IPA SHA256 `fa092542663a7dcfe516af1c481947f29a8728d3f4a725fcb912fcb79213e088` matched the release digest. TrollStore Lite install returned 0 and installed Info.plist matches this version/revision; uiopen succeeded. Phone state preserves pkd/Shortcuts On, recording Off, and diagnosticd/profiled Off. Original 336 override values were unchanged by optional-host testing; six explicit host entries were added. App repository changes were pushed; reference notes remain local.

Correction: although v1.0.9 installed and uiopen returned success, the user reported launch crashes. A duplicate com.apple.FileProvider description key caused Swift dictionary initialization to trap. v1.0.10 removes that duplicate and adds Python uniqueness plus Swift dictionary-initialization checks. Services/Critical now consolidate Search, numeric available MiB and theme control into one row, with the filter below; the separate top bar and pinned category headings are removed. Device launch verification is required after installation.

Deployment v1.0.10 / build 11: source ac933c355c2864225afc0975c56ad9f0051f7893, successful Actions run 38215041614 (20 Python tests, Swift memory/group/dictionary checks and iOS build). Release/download SHA256 7eaf0738b024cfb8d364da29dcfdd59add524e13475fb77f892406f2f25ddd19 matched. TrollStore installation returned 0 and installed Info.plist matched. Unlike v1.0.9, the launched process remained present across repeated checks. The user reported the compact layout and recording group work. Recording after group On/respring passed, but the final phone check still found both jobs enabled. A follow-up Off-button check was requested; root restored both jobs Off with verified native unloads to leave the requested normal state. The last UI Off-button outcome remains pending. Account/sync functionality remains untested. Diagnosticd, ReportCrash and profiled were restored disabled/unloaded after launch investigation.

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

The tested Lock Screen/Notification Center media group needs PosterBoard + liveactivitiesd, with PaperBoard still Off. Keep all three optional/default Off as explicitly requested; complete group Off must remain available. PosterBoard is shared with Wallpaper, while widget updates now controls chronod separately. Restart userspace after media-group changes. Config now matches 512 targets, 318 Off / 194 On; existing Critical NO entries are unchanged. See the [daemon test notes](scripts/daemons/README.md) for the On/Off comparison and recorded MediaRemoteUI rendering failure.
