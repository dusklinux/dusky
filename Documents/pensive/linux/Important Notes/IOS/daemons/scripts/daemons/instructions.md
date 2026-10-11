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
