# Device use and app installation

The app source and build workflow live in `/mnt/zram1/daemon_manager-main` and [dusklinux/daemon_manager](https://github.com/dusklinux/daemon_manager). This notes directory keeps references and a matching config, not source/build copies or the retired daemonmanager/processkiller scripts.

On the tested rootless iOS 16 phone, OpenSSH and Python 3.9.9 are available. Use the phone's actual SSH address and root credentials; do not assume the old notes' address or Python version on another device.

1. Build from the repository's GitHub Actions workflow, or generate its Xcode project with XcodeGen on macOS and build the Release iphoneos target.
2. Obtain the IPA from the successful run for the intended commit. The mutable latest release alone does not identify a particular source revision.
3. Transfer the IPA over SSH. Install with TrollStore Lite's `trollstorehelper install force PATH` on the tested phone; its location is `/var/jb/Applications/TrollStoreLite.app/trollstorehelper`. Discover the path on other installations. AppSync Unified with `appinst PATH` is an alternative when present.
4. Launch Daemon Manager. Tapping a service's name/chevron expands details; its separate switch changes the saved override. The Defaults tab edits targets; Apply Defaults saves them to launchd.
5. Restart userspace from Tools when ready to apply process changes fully. `launchctl reboot userspace` was exercised on this device. A restart interrupts apps and SSH.

`daemon.cfg`: `yes` means disabled override; `no` means enabled override. Import it through Config to inspect/apply it. Critical and newly recommended-enabled entries are kept on in builtin targets, but manual disabling is still allowed with the existing warning flow. Custom target choices take precedence.

Keep searchd, biomed, syncdefaultsd, and mobile.lockdown enabled under the revised preset. Their warnings reflect measured dependency errors/functionality loss, not new bootloop claims. In particular, restore mobile.lockdown before computer pairing/backup workflows. USB charging and port-forwarded SSH are separate.

The app uses native launchctl persistence and corrects the disabled plist to root:wheel `0644`; it no longer rewrites that database directly. An emergency launchd reset resets saved overrides, not phone content, and needs a userspace restart. It is not an exact restoration of an earlier override configuration.

See the parent iOS 16 reference for measured limits, known critical services, and the 2026-10-11 test results. Do not treat the absence of crash reports as proof of stability when crash collectors are off.

## Categories and built-in defaults

The IPA includes all 505 curated targets (320 off, 185 on); sharing it does not require a config import. Defaults also shows targets not found on the current phone; Apply Defaults acts on installed services only. Editing a target does not change the phone until applied.

Critical is a separate tab for the existing capital-NO services. Disabled entries sort first and remain members of that tab. Critical disable actions retain confirmation. Orange Recommended On entries have direct toggles and explain feature/dependency consequences.

Keep com.apple.imagent, com.apple.identityservicesd, and com.apple.nehelper enabled for Messages; the nehelper-embedded filename alias also defaults On. Disabling nehelper while Identity Services runs caused repeated aborts; enabling it restored the conversations. Nehelper also supports other Network Extension clients. These are recommendations, not newly established bootloop services.

The RAM indicator shows only a value and MiB; its underlying free-plus-speculative calculation is unchanged.

## Wallpaper host and storage (v1.0.4)

The verified PosterBoard job is `/System/Library/LaunchAngels/com.apple.PosterBoard.plist`, label `com.apple.PosterBoard`, domain `user/501` on this phone (the app discovers the mobile UID). Killing its PID does not save an override. Native disable plus bootout stopped it and most associated poster extensions in the observed window. A userspace reboot preserved the off override and left no PosterBoard/poster-extension processes running. The user confirmed unlock, Home, Notes and Messages work after this reboot. Battery impact and long-term stability remain unmeasured. The app exposes this specific job under Tools → Wallpaper process and in Services; it does not expose other untested LaunchAngels such as input/UI hosts. The compiled default keeps it enabled for other users; manual disabling remains available, and userspace restart fully applies it. No auto-kill daemon, polling loop, file renaming, or arbitrary app blacklist is installed.

Speculative pages are reclaimable kernel filesystem cache, not a separately disableable SysMain service. Disabling predictive daemons does not eliminate all speculative pages. No useful verified force-compress control was found; no induced memory pressure or forced app eviction is implemented. Core account daemons and siriactionsd retain their existing critical warnings; feature names alone do not establish safe disabling.

The storage driver advertises NVMe SMART capability, but read-only IOCreatePlugInInterfaceForService returned `0xe00002c7` on this device; lifetime writes/endurance/wear could not be obtained. The Memory & Storage sheet displays data-filesystem capacity, available and used space; unavailable health counters are not guessed. No terabyte-write lifetime or rated endurance is invented.
