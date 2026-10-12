# Daemon Manager reference config

The maintained SwiftUI app is at [dusklinux/daemon_manager](https://github.com/dusklinux/daemon_manager), checked out locally at `/mnt/zram1/daemon_manager`. App source, build instructions, and the CI workflow belong there. The old standalone daemonmanager and processkiller instructions are retired.

This directory contains `daemon.cfg` and device-use notes. The config mirrors the repository's root `daemon.cfg` and its 512 builtin targets: **325 off, 187 on**. `yes` means disable; `no` means keep enabled; `NO` marks critical enabled targets. A recommendation is not a guarantee that every optional feature or device is safe with a service off.

The 2026-10-11 revision keeps these services enabled and shows orange recommendations in the app:

| Service | Reason |
| --- | --- |
| `com.apple.searchd` | Photos logged repeated failed Spotlight queries with sustained CPU during broad live unloading; exact single-service causality remains unproven. |
| `com.apple.biomed` | SpringBoard and keyboard clients logged Biome metadata/lockfile failures. |
| `com.apple.syncdefaultsd` | Provides `com.apple.kvsd`; multiple apps logged failed synchronized key-value-store access. |
| `com.apple.imagent` | Recommended for Messages accounts and communication; kept enabled by default. |
| `com.apple.identityservicesd` | Recommended for iMessage/FaceTime identity services; keep nehelper enabled with it. |
| `com.apple.nehelper` / `com.apple.nehelper-embedded` | Identity Services crashed repeatedly without its network interface; enabling this job restored Messages. The latter name is a plist filename alias. |
| `com.apple.mobile.lockdown` | Paired USB tools failed when disabled and worked after restoration; needed for pairing/computer backup workflows. |

Critical bootloop/CPU/access warnings remain separate. Every entry remains manually configurable. Existing explicitly customized target differences are retained when migrating the old full-copy preference format; entries that matched old builtin values adopt the revised defaults. The old format cannot distinguish an explicit choice equal to an inherited default. New choices are stored separately from builtins.

The app saves launchctl overrides and reports command/verification failures. It does not hot-unload services: restart userspace to apply loaded-process changes fully. This addresses both demand restarts seen in testing and disruptive intermediate dependency states. The plist must retain root:wheel ownership and mode `0644` for the tested persistence workflow.

RAM displays only the number and MiB unit using kernel free_count, which already includes speculative pages. Physical free excludes speculative pages. No >600 MiB idle gain or broad battery-life improvement was verified.

Read [instructions.md](instructions.md) for usage/install notes and `../../daemons based on ios 16.md` for the full reference, test results, and limitations. No scripts, IPA builds, raw test logs, or mirrored app source are kept here.

The Critical tab always retains installed capital-NO services, even when disabled, and puts disabled entries first. Orange recommendations toggle directly; red critical disables retain confirmation. All 506 builtin targets are compiled into the IPA and shown in Defaults, including absent entries; config import is optional.

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
