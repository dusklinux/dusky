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

The revised builtin/config list has **504 entries, 324 off, 180 on**, with searchd, biomed, syncdefaultsd, and mobile.lockdown kept enabled. The latter three have direct Biome/KVS/USB failure evidence; searchd is a conservative dependency recommendation. They receive red KEEP ENABLED warnings, distinct from existing CRITICAL warnings; manual disabling remains allowed. Do not newly classify duetexpertd as unsafe to disable solely because its enabled process ran hot.

Critical services from the reference remain on, including mobile.usermanagerd, nanoregistryd, sleepd, rapportd, siriactionsd, nesessionmanager, misagent, FairPlay services, assetsd, OpenSSH, SpringBoard, backboardd, and core networking/security/lifecycle services. Do not disable them automatically without explicit user authorization.

User-confirmed unlock, Notes typing, Camera, Photos, and Safari passed after the full-off reboot. HTTPS and Wi-Fi/USB SSH worked; core processes remained present. The retained post-reboot window had 558,096 records including 215 faults and three Loss records. There was no actual panic/watchdog-timeout/EXC_BAD_ACCESS/SIGSEGV in that reviewed window, but logs were not clean and disabled crash reporters limit negative evidence. An earlier diagnosticd report was simulated/nonfatal.

Baseline median physical free RAM was 215.4 MiB versus 121.2 MiB during the five-minute post-reboot workload; activities differed. Free+speculative medians were 364.7/177.1 MiB, compressor occupancy 351.6/328.1 MiB. Do not claim an isolated memory gain, battery improvement, or the >600 MiB idle goal. CPU proc_pidinfo counters require Mach timebase conversion; this A11 measured 125/3 ns per tick. Summed process RSS includes shared memory.

## App behavior and future work

The tab bar explicitly uses opaque standard and scroll-edge appearances from initial launch. Service labels/chevrons expand details independently from their switches. Details include known descriptions, plist/executable paths, and verified Contacts-related database metadata for relevant services. Unknown private roles are omitted; do not invent descriptions or unrelated database paths.

Builtin targets and explicit custom choices are separated. Legacy saved values matching old builtins migrate as inherited values; explicit differences are preserved. Routine operations save and verify native overrides, retain valid plist metadata, and ask for a userspace restart to fully apply process changes. Do not promise immediate RAM reduction or silently hot-unload dependencies. Batch failure must never produce an all-success banner. Label progress counts completed lines, not arbitrary stdout chunks.

On Linux, use the repository's GitHub Actions macOS workflow for the actual Swift/iOS build. Download the successful intended commit's IPA artifact/release, verify its provenance, install through TrollStore Lite or AppSync when authorized, and exercise app launch. Keep scope focused; do not include private phone logs, database contents, credentials, or duplicate sources in the repository.
