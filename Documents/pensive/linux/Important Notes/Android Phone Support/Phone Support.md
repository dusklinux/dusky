# Android phone support on Arch Linux

These notes focus on transferring files between an Android phone and Arch Linux over USB. The same approaches apply in a Hyprland session: the file manager and its backend matter, not the desktop compositor.

## Choose a method

| Method | Best use | Phone setup | Linux interface |
| --- | --- | --- | --- |
| MTP with GVFS | Browse and copy files in a GVFS-aware file manager | Unlock; select **File transfer** | Files/Nautilus, Thunar and other GVFS-aware applications; optionally GIO or a GVFS FUSE mount |
| MTP with KIO | Browse and copy files in Dolphin | Unlock; select **File transfer** | Dolphin and other KIO-aware applications |
| ADB | Scriptable file copies and Android debugging | Enable USB debugging and authorize the computer | `adb push`, `adb pull`, `adb shell` |
| PTP | Camera/photo access if the phone offers it | Unlock; select **Photo transfer** | GVFS with `gvfs-gphoto2`; availability varies by phone |
| Separate MTP client | Alternative when a particular MTP backend fails | Unlock; select **File transfer** | Android File Transfer's GUI, CLI or FUSE mount |

MTP and ADB are different protocols. **MTP does not require USB debugging.** ADB is not necessary for ordinary file-manager transfers. A USB cable that only charges cannot carry either protocol. A phone may expose more than one USB function, but competing MTP clients can have trouble opening the same MTP interface: close or unmount one before trying another.

## 1. Install the backend you need

Update the system and install the appropriate packages from the official Arch repositories. Choose the commands for your tools; there is no need to install every MTP client.

For a GVFS-aware file manager:

```bash
sudo pacman -Syu --needed gvfs-mtp
```

`gvfs-mtp` pulls in `gvfs` and `libmtp`. Install a compatible file manager separately if needed. Dolphin uses KIO; the current Arch `dolphin` package depends on `kio-extras`, which supplies its MTP support. Install it with:

```bash
sudo pacman -Syu --needed dolphin
```

For ADB and Fastboot:

```bash
sudo pacman -Syu --needed android-tools
```

Optional packages, installed only when relevant:

```bash
sudo pacman -Syu --needed android-udev usbutils
```

- `android-udev` supplies additional device rules. Many devices work with existing systemd or `libmtp` rules; use this package if additional device recognition or permissions are needed. Reconnect the phone after installation.
- `usbutils` provides `lsusb` for USB diagnostics.
- `gvfs-gphoto2` supplies the GVFS PTP backend; install it with `sudo pacman -Syu --needed gvfs-gphoto2` if you want to try the phone's photo-transfer mode.
- `android-file-transfer` is an alternative MTP implementation with GUI, CLI and FUSE interfaces; see below.

`gvfs-afc` is for Apple mobile devices, not Android MTP. `base-devel`, Git, AUR helpers and multiple FUSE clients are not prerequisites for the standard GVFS, KIO or ADB workflows. Pacman resolves required dependencies, including the appropriate FUSE library. `-Syu` refreshes package databases and performs a full upgrade; `--needed` skips targets already up to date. Review the transaction and confirm it interactively.

## 2. Connect for file-manager transfers

1. Connect the phone with a known data-capable USB cable. If possible, connect directly to the computer while troubleshooting.
2. Unlock the phone. Open its USB notification or USB settings and select **File transfer**, **MTP**, or the manufacturer's equivalent. Charging alone does not establish MTP. Names and prompts vary by device.
3. Open the phone in your file manager's devices or locations list. Select its internal storage or SD card, if exposed, and browse to the desired folder. For photos, `DCIM/Camera` is common but not universal.
4. Copy a small test file in the desired direction. Wait for the operation to finish and check the destination. For important files, keep the source until you have verified the copy.
5. When finished, close files and use the file manager's unmount/eject action if offered before unplugging.

MTP presents objects managed by the phone, not a normal block-device filesystem. It need not support all ordinary filesystem operations, metadata, permissions or random access in the same way as a local disk. Android decides which storage and files it exposes; app-private data is not generally available through MTP. Folder listings and transfer speeds depend on the phone, cable and client. There is no universal client or speed guarantee.

## 3. GVFS from the terminal

With a running graphical user session and `gvfs-mtp` installed, list volumes and mounts:

```bash
gio mount -li
```

`-l` lists user-visible drives, volumes and mounts; `-i` adds details. A detected MTP volume can show an `activation_root` such as `mtp://[usb:002,018]/`. Bus and device numbers are examples and may change when reconnecting. A volume being listed does not necessarily mean it is mounted. Open it in the file manager to mount it, or use the actual URI reported on your system:

```bash
gio mount 'mtp://[usb:BUS,DEVICE]/'
gio list 'mtp://[usb:BUS,DEVICE]/'
```

Replace the entire example URI with the one reported for the current connection; keep the quotes because brackets have special meaning to the shell. Use `gio list -u 'URI'` to print child URIs, then use those actual URIs to navigate. `gio help mount`, `gio help list`, and `gio help copy` show the installed syntax. GIO can work with virtual locations directly; for example, after finding the actual file URI:

```bash
gio copy -p 'FILE_URI' "$HOME/Downloads/"
```

This example copies a file to an existing local directory. `-p` shows progress. Do not assume `gio copy` is a recursive directory-copy replacement for `cp -r`; use the file manager for folders or consult the installed help for the operation you need.

GVFS may also expose mounted locations to ordinary command-line programs through its FUSE bridge under `$XDG_RUNTIME_DIR/gvfs` (normally `/run/user/<UID>/gvfs`). The bridge mount can exist while empty; a phone location appears inside it when that location is mounted and the bridge is working. Inspect it with:

```bash
ls "$XDG_RUNTIME_DIR/gvfs"
```

Directory names are generated by GVFS; inspect them instead of hardcoding one. A GIO URI is not itself a normal local path. Some applications or operations may behave differently through the FUSE bridge. When done, unmount the actual URI or use the file manager:

```bash
gio mount -u 'ACTUAL_MTP_URI'
```

## 4. ADB file transfers

Install `android-tools`, then enable **Developer options** and **USB debugging** on the phone. Connect and unlock it, and accept the computer's RSA authorization prompt. The phone's setting names vary. See [Android's ADB documentation](https://developer.android.com/tools/adb) for the current setup and command reference.

```bash
adb devices -l
```

A device listed as `device` is ready for ADB commands. `unauthorized` means the phone has not authorized this computer; check the unlocked phone for the prompt. `offline` means ADB sees a transport but it is not ready. An empty list can mean USB debugging is off, the connection or permissions are wrong, or the device is disconnected. ADB starts a local server as needed.

Examples for a single authorized device:

```bash
adb shell ls /sdcard/
adb pull /sdcard/DCIM/Camera "$HOME/Downloads/"
adb push "$HOME/Downloads/example.txt" /sdcard/Download/
```

`pull` copies from the phone to the computer; `push` copies in the opposite direction. The first copy can be large: inspect the source and ensure the destination has space. Replace example paths with real ones. `/sdcard` normally refers to shared storage, not necessarily a removable card; paths and access depend on the device and Android permissions. ADB does not automatically grant access to all app-private data. Check the destination and command output after copying.

If multiple devices or emulators are connected, select the serial shown by `adb devices -l`:

```bash
adb -s SERIAL pull /sdcard/Download/example.txt "$HOME/Downloads/"
```

Replace `SERIAL` and the paths. `adb pull -a` requests preservation of timestamps and mode. `adb push --sync` skips files based on timestamp differences; it is not a full two-way synchronization or a content-verification tool. Compression options are documented in `adb help`; their effect depends on the data and devices, so measure before assuming a speed improvement.

To inspect the phone interactively, use `adb shell` and `exit` to leave. Commands inside that shell run on Android; local shell commands run on Arch. Fastboot is a separate bootloader protocol, not needed for normal file transfers; use device-specific instructions for bootloader operations.

## 5. Alternative MTP client and a local mount

If GVFS or KIO has a device-specific problem, try the official `android-file-transfer` package:

```bash
sudo pacman -Syu --needed android-file-transfer
```

First close other MTP clients and unmount the phone in the file manager. The package provides a graphical application and `aft-mtp-cli`; type `help` inside the CLI and `exit` to leave. It also provides a FUSE mount:

```bash
mkdir -p "$HOME/Phone"
aft-mtp-mount "$HOME/Phone"
```

Browse the mounted directory with a file manager or normal shell tools. When finished, close programs using it and unmount it:

```bash
fusermount3 -u "$HOME/Phone"
```

The current Arch package depends on FUSE 3; use `fusermount3` for this mount. The directory remains after unmounting. Check the mount with `findmnt -T "$HOME/Phone"`; after unmounting, that command may report the containing filesystem instead, so inspect its target and filesystem type. The [ArchWiki MTP page](https://wiki.archlinux.org/title/Media_Transfer_Protocol) describes other clients, including `jmtpfs` and `simple-mtpfs` from the AUR. Their FUSE version and unmount command depend on the package; do not assume all clients use the same helper. The wiki reports that this client's FUSE interface can be slower than its direct GUI or CLI; actual performance must be measured on the device.

## 6. Troubleshooting, in order

1. **No USB device:** Unlock the phone, use a known data cable and another port, and reconnect. Run `lsusb` before and after connecting and compare the output. A new USB entry establishes enumeration, not that MTP or ADB works. `lsusb` comes from `usbutils`.
2. **USB is visible but no files:** Select **File transfer/MTP** on the unlocked phone. Confirm the correct file-manager backend is installed. Reconnect after installing it. The phone may ask for additional permission. Wait for storage to become available after unlocking.
3. **MTP volume missing or inaccessible:** Run `gio mount -li` for GVFS, or inspect Dolphin's devices for KIO. Close other MTP clients and unmount their mounts before retrying; then reconnect. `mtp-detect` from `libmtp` can provide diagnostic output, but it also accesses the device and should be run after releasing other MTP clients. Device support and behavior vary. If `gio mount -li` does not return, interrupt it with Ctrl+C and retry; a stalled diagnostic does not establish that the phone is at fault.
4. **Permission or unknown device:** Install `android-udev` and reconnect. Check the exact error, the device's current USB ID from `lsusb`, and the relevant rules before considering a device-specific rule. Reconnecting matters because loading new rules does not retroactively reprocess an already connected device. Do not assume every manufacturer ID or device mode uses the same rule.
5. **File listing hangs or a transfer fails:** Keep the phone unlocked, check space on the destination and retry a small file. Close and unmount the current client before trying a different implementation or ADB. Some phones or MTP implementations have directory-specific problems; consult the [ArchWiki troubleshooting section](https://wiki.archlinux.org/title/Media_Transfer_Protocol#Troubleshooting). PTP plus `gvfs-gphoto2` may provide photo access if the phone supports it, but is not equivalent to full MTP storage access.
6. **ADB unauthorized or offline:** Check USB debugging and the authorization prompt on the unlocked phone. Reconnect; if needed, revoke USB debugging authorizations in Android's developer settings and authorize again. `adb kill-server` followed by `adb devices -l` restarts the server and affects other ADB sessions. For insufficient permissions, check rules and reconnect; running ADB as root is not the normal solution.
7. **Backend still fails:** Note the exact error, package versions (`pacman -Q gvfs-mtp libmtp android-tools`), device model, Android version and selected USB mode. Check the user journal with `journalctl --user -b` and system journal with `journalctl -b` around the failure. Logs and device identifiers can contain private information; review them before sharing.

A reboot is not inherently required after installing these packages. Reconnecting applies device rules to a fresh connection; if a desktop backend does not pick up a newly installed component, restarting the user session or rebooting is a reasonable next diagnostic step.

## References and verification

- [ArchWiki: Media Transfer Protocol](https://wiki.archlinux.org/title/Media_Transfer_Protocol) — clients, desktop integration and troubleshooting.
- [ArchWiki: Android](https://wiki.archlinux.org/title/Android) and [Android Debug Bridge](https://wiki.archlinux.org/title/Android_Debug_Bridge) — connection and ADB guidance.
- [Android Developers: ADB](https://developer.android.com/tools/adb) and [developer options](https://developer.android.com/studio/debug/dev-options) — phone setup and ADB commands.
- [Arch package: gvfs-mtp](https://archlinux.org/packages/extra/x86_64/gvfs-mtp/) and [android-tools](https://archlinux.org/packages/extra/x86_64/android-tools/) — current package details.

On 2026-10-27, the local package database and installed `gio`, `adb`, `fusermount3` and `lsusb` interfaces were checked. A local fixture verified GIO listing and single-file copying; `gio mount -li` initially stalled but returned on a later attempt. Installed versions included GVFS 1.62.0, libmtp 1.1.23 and Android tools 37.0.0p1. No Android phone was connected, so MTP mounting, ADB authorization and actual transfers were **not** tested end to end. The final ISO package set and versions must be checked separately; package updates and phone behavior can change the results.
