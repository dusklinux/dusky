# Agent context: iOS 16 daemon testing and Daemon Manager

## Scope and maintained files

App repository: `/mnt/zram1/daemon_manager-main`, remote `git@github.com:dusklinux/daemon_manager.git`, main branch. The repository is the sole app source/build location; its root `daemon.cfg` mirrors `scripts/daemons/daemon.cfg` in these notes. Do not recreate duplicate app sources or old daemonmanager/processkiller scripts here.

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

The revised builtin/config list has **505 entries, 320 off, 185 on**, with searchd, biomed, syncdefaultsd, and mobile.lockdown kept enabled. The latter three have direct Biome/KVS/USB failure evidence; searchd is a conservative dependency recommendation. They receive orange RECOMMENDED ON guidance, distinct from red CRITICAL warnings; recommendations toggle directly, while critical disables retain confirmation. Do not newly classify duetexpertd as unsafe to disable solely because its enabled process ran hot.

Critical services from the reference remain on, including mobile.usermanagerd, nanoregistryd, sleepd, rapportd, siriactionsd, nesessionmanager, misagent, FairPlay services, assetsd, OpenSSH, SpringBoard, backboardd, and core networking/security/lifecycle services. Do not disable them automatically without explicit user authorization.

User-confirmed unlock, Notes typing, Camera, Photos, and Safari passed after the full-off reboot. HTTPS and Wi-Fi/USB SSH worked; core processes remained present. The retained post-reboot window had 558,096 records including 215 faults and three Loss records. There was no actual panic/watchdog-timeout/EXC_BAD_ACCESS/SIGSEGV in that reviewed window, but logs were not clean and disabled crash reporters limit negative evidence. An earlier diagnosticd report was simulated/nonfatal.

Baseline median physical free RAM was 215.4 MiB versus 121.2 MiB during the five-minute post-reboot workload; activities differed. Free+speculative medians were 364.7/177.1 MiB, compressor occupancy 351.6/328.1 MiB. Do not claim an isolated memory gain, battery improvement, or the >600 MiB idle goal. CPU proc_pidinfo counters require Mach timebase conversion; this A11 measured 125/3 ns per tick. Summed process RSS includes shared memory.

## App behavior and future work

The tab bar explicitly uses opaque standard and scroll-edge appearances from initial launch. Service labels/chevrons expand details independently from their switches. Details include known descriptions, plist/executable paths, and verified Contacts-related database metadata for relevant services. Unknown private roles are omitted; do not invent descriptions or unrelated database paths.

Builtin targets and explicit custom choices are separated. Legacy saved values matching old builtins migrate as inherited values; explicit differences are preserved. Routine operations save and verify native overrides, retain valid plist metadata, and ask for a userspace restart to fully apply process changes. Do not promise immediate RAM reduction or silently hot-unload dependencies. Batch failure must never produce an all-success banner. Label progress counts completed lines, not arbitrary stdout chunks.

On Linux, use the repository's GitHub Actions macOS workflow for the actual Swift/iOS build. Download the successful intended commit's IPA artifact/release, verify its provenance, install through TrollStore Lite or AppSync when authorized, and exercise app launch. Keep scope focused; do not include private phone logs, database contents, credentials, or duplicate sources in the repository.

## Messages recovery / current category behavior

The user confirmed conversations returned after enabling com.apple.nehelper alongside imagent and identityservicesd. SQLite quick_check passed; 247 messages and 79 conversations remained. Identity Services had repeatedly aborted at IDSGroupAgent registerAgent / self.interfaceName with nehelper unavailable; the process stayed running after restoration. Nehelper is not exclusive to Messages. Keep the three actual jobs and the nehelper-embedded filename alias enabled in builtin targets as recommendations, not new critical bootloop claims.

Current compiled defaults: 505 entries, 320 off, 185 on. Orange recommended-on services toggle directly without critical confirmation. Red critical services retain confirmation and a separate Critical tab whose membership never depends on switch state. Existing capital-NO classifications are preserved. Defaults includes absent builtin targets as well as installed services; fresh app installations need no config import. RAM displays just the value/unit while preserving the free-plus-speculative calculation. Installing the app must not silently apply defaults.

## Memory-panel correction (v1.0.3)

Services now groups critical services last; the Critical tab remains independent and puts disabled entries first. Tapping RAM opens live memory details, including physical compressor occupancy and original page size. XNU `free_count` already includes speculative pages: the old app's sum double-counted them. The header now uses `free_count`; physical free is `free_count - speculative_count`. Treat earlier free-plus-speculative sums as historical calculations, not unique physical pages or proof of a memory gain.

System pressure is Normal/Warning/Critical, seeded from the privileged current-pressure sysctl when the panel opens and followed through native Dispatch notifications. Do not interpret a free-RAM percentage as pressure. The verified sysctl exports flags 1/2/4, not internal VM levels 0/1/3. VM statistics on the tested phone returned 38 words with a 16 KiB page size; discover these dynamically. Details do not apply daemon defaults or change overrides.

## Wallpaper host and storage (v1.0.4)

The verified PosterBoard job is `/System/Library/LaunchAngels/com.apple.PosterBoard.plist`, label `com.apple.PosterBoard`, domain `user/501` on this phone (the app discovers the mobile UID). Killing its PID does not save an override. Native disable plus bootout stopped it and most associated poster extensions in the observed window. A userspace reboot preserved the off override and left no PosterBoard/poster-extension processes running. The user confirmed unlock, Home, Notes and Messages work after this reboot. Battery impact and long-term stability remain unmeasured. The app exposes this specific job under Tools → Wallpaper process and in Services; it does not expose other untested LaunchAngels such as input/UI hosts. The compiled default keeps it enabled for other users; manual disabling remains available, and userspace restart fully applies it. No auto-kill daemon, polling loop, file renaming, or arbitrary app blacklist is installed.

Speculative pages are reclaimable kernel filesystem cache, not a separately disableable SysMain service. Disabling predictive daemons does not eliminate all speculative pages. No useful verified force-compress control was found; no induced memory pressure or forced app eviction is implemented. Core account daemons and siriactionsd retain their existing critical warnings; feature names alone do not establish safe disabling.

The storage driver advertises NVMe SMART capability, but read-only IOCreatePlugInInterfaceForService returned `0xe00002c7` on this device; lifetime writes/endurance/wear could not be obtained. The Memory & Storage sheet displays data-filesystem capacity, available and used space; unavailable health counters are not guessed. No terabyte-write lifetime or rated endurance is invented.


## Instant system transitions and disk health (v1.0.5)

The user clarified that animation removal means system transitions (app open/close, switcher, folders), not animations inside apps. Installed the developer's NoAnimationsX 1.2 rootless package (SHA256 `ddfbeb477b0349a86e41b2e3c66a731724763cd7d15d9147ced035106bef63b1`). Its filter injects only into SpringBoard; inspection showed suppression of animation actions, not a speed multiplier. After sbreload, the user confirmed all listed transitions instant and unlocking/touch work. Do not claim independently tested coverage of every system transition. Tools → Wallpaper & animations controls its injection filter, with respring required; disabled filter is retained as `NoAnimationsX.plist.daemonmanager-disabled`. Other users install this optional dependency in Sileo; its binary is not redistributed in the IPA. No background animation worker is added.

The requested capacity section has been removed from the RAM panel. The older sysdiagnose did contain NAND health despite the failed SMART interface probe. A fresh `sysdiagnose -f /var/tmp/ -n` also produced `ASPSnapshots/asptool_snapshot.log` with percentUsed 9, TLC average erase cycles 92/native endurance cycles 1000, SLC average 8278/endurance 45000, zero critical warnings/grown bad/retired blocks/uncorrectable reads. Write counters are present, but their units were not established independently; show raw values rather than guessed TB or rated TBW. Wear is a driver counter, not a remaining-life guarantee.

Tools → Disk diagnostics reads the latest completed report or collects a new one on demand under `/var/tmp/daemon-manager-diagnostics`. Tested the actual collection helper end to end with sysdiagnose and sysdiagnose_helper originally disabled; temporary enabling/loading and final disabling/bootout restored every prior override exactly. diagnosticd was temporarily enabled during initial investigation and restored too; the app's verified collection only needs the two sysdiagnose jobs. Enter confirmation requires a real PTY; piping newline to the CLI without a terminal exited 64. Collection takes several minutes; keep the app open. Reports stay on-device and are not uploaded. Missing fields or collection failures must remain unavailable, not fall back silently to older data or zero.

The final v1.0.5 IPA from commit `730a3d838efbdc28be9fa9d12e7ab1235f16d9cd` passed all 13 Python tests, Swift memory checks, and the iOS build (Actions run 38121962687). Its asset digest, embedded source revision, and AppIcon metadata were checked before TrollStore Lite installation. Installed Info.plist matches that commit; all launchd overrides remain unchanged and PosterBoard stays off. The supplied frog sticker was prepared with built-in imagegen as a square opaque icon under the app asset catalog.
The user confirmed the installed frog icon, animation switch on, latest diagnostic wear at 9%, and capacity removed from RAM. Both provided disk-health screenshots were reviewed: labels/counters match the parsed report and remain readable, with snapshot provenance and unverified write-unit limits displayed.
