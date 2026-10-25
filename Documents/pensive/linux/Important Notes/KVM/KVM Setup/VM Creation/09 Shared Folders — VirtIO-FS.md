---
title: "Shared folders — VirtIO-FS host configuration"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# Shared folders — VirtIO-FS host configuration

VirtIO-FS exports an existing host directory to a guest. The common part is the
host backend/device and shared guest-memory backing. Mounting it is OS-specific.
It works independently of the display transport; no Samba or RDP redirection is
needed for this path.

Install `virtiofsd`, choose an existing directory, and check access. The Windows
helper defaults to `/mnt/zram1` and preserves an explicitly selected path.
That pathname must be mounted when it is the intended RAM-backed filesystem.
Do not create files under an absent media/RAM mount and assume it is active.

## Powered-off host XML changes

Inside the domain, merge with an existing memoryBacking element rather than
creating a duplicate:

```xml
<memoryBacking>
  <source type='memfd'/>
  <access mode='shared'/>
</memoryBacking>
```

Preserve the intended backing if the VM deliberately uses hugepages/file-backed
memory. The vhost-user backend needs shared access to guest RAM. This setting is
not an instruction to enable DAX, and it is not required merely for an unrelated
Looking Glass ivshmem device.

Inside `<devices>`:

```xml
<filesystem type='mount' accessmode='passthrough'>
  <driver type='virtiofs' queue='1024'/>
  <binary><cache mode='none'/></binary>
  <source dir='/mnt/zram1'/>
  <target dir='dusky-host'/>
</filesystem>
```

The target is a **mount tag**, not a guest directory or Windows drive letter.
Let libvirt find its installed backend and allocate the device address. Cache
`none` is this workflow's cross-host/guest edit policy, not a claim every
application cache is disabled. Coordinate concurrent writes to the same file.
Power the guest off/start it after adding shared memory or filesystem hardware.

## OS-specific mount

- Windows: [[Setting up Shared Directory Between Guest_win11 and Host]] adds
  WinFsp and viofs, configures an unused drive (default Z:) and the host UID/GID.
- Linux: a kernel VirtIO-FS mount uses the matching tag and Unix numeric IDs;
  systemd automount can make it available on demand. Current Linux scripts use
  their own tag/mount defaults and a tested no-file-handle backend workaround.
  Do not assume Windows `dusky-host`/Z: and Linux defaults are identical.
  Guest-side instructions will be consolidated in the Linux pass.

The source must exist every time the VM starts. For a custom child directory on
RAM storage, arrange its recreation after host boot. Sharing the mount root
avoids that extra child-directory dependency. Files remain on the host source;
copying them elsewhere in the guest writes to the VM disk.

## Check both directions

```sh
virsh -c qemu:///system dumpxml win11 --inactive
pgrep -a virtiofsd
findmnt --target /mnt/zram1
```

Create uniquely named files from each side, read them from the other, compare
hashes, test renames/ownership and retest after guest restart. Do not manipulate
active guest disk images through the shared folder. A VM snapshot does not
snapshot the shared host directory. Back it up separately when needed.

Sources: [libvirt VirtIO-FS](https://libvirt.org/kbase/virtiofs.html),
[VirtIO-Win filesystem setup](https://virtio-win.github.io/Knowledge-Base/Virtiofs-qs.html).
