---
title: "VM image access — discover QEMU identity and apply ACLs"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# VM image access — discover QEMU identity and apply ACLs

[[+ MOC KVM|KVM home]]

Three identities matter: the desktop operator, the libvirt management daemon,
and the QEMU process. A root daemon does not imply root QEMU. Commented
`user="root"` examples in `qemu.conf` do not reveal the distribution default.

## Discover, then grant access

With modular libvirt available:

```sh
virsh -c qemu:///system capabilities
ps -C qemu-system-x86_64 -o user:24,group:24,pid
getfacl /var/lib/libvirt/images
namei -l /var/lib/libvirt/images
```

In capabilities, the DAC `baselabel` for KVM reports numeric owner/group, such as
`+UID:+GID`. Resolve those with `getent passwd UID` and `getent group GID`.
The example numbers from another computer are not your account settings.
Explicit uncommented `user`/`group` settings override package defaults; after
changing them, account for daemon restart and existing guests before proceeding.

The corrected `07_storage_setup.py` discovers omitted defaults through these
capabilities. The Windows profile runs daemon setup first. It no longer assumes
root when it cannot discover the account.

## Manual ACL example

Set these to your selected directory and discovered QEMU account:

```sh
vm_pool_dir=/var/lib/libvirt/images
vm_operator=$(id -un)
# Replace SERVICE_ACCOUNT with the account resolved above:
vm_qemu_user=SERVICE_ACCOUNT
sudo setfacl -m "u:$vm_operator:rwx,u:$vm_qemu_user:rwx" -- "$vm_pool_dir"
sudo setfacl -d -m "u:$vm_operator:rwx,u:$vm_qemu_user:rwx" -- "$vm_pool_dir"
getfacl -- "$vm_pool_dir"
```

Run the ACL commands only after replacing the placeholder. If QEMU genuinely
runs as root, it does not need an extra named-user ACL. Parent directories need
**search/execute** access. Inspect the full ancestry with `namei`/`getfacl` and
add only the missing traversal rights, for example `setfacl -m u:ACCOUNT:x DIR`.
A protected home-directory ancestor can block an otherwise accessible image.

Default ACLs affect newly created entries, not old images retroactively. A
program's requested creation mode can still limit the effective ACL mask.
For an existing qcow2, grant the needed read/write ACL to that specific file and
check `getfacl`'s effective permissions. Do not recursively strip permissions or
change an entire media mount's owner as a generic repair.

A filesystem must support the ACL operations used here. Test the actual mount
rather than asserting all NTFS, network or removable filesystems behave alike.
No ACL fixes a missing mount, wrong disk path or inaccessible backing image.

Verify as the desktop user with a uniquely named scratch file, remove that file,
and inspect QEMU/libvirt startup errors. The service account's ability to open
an image is distinct from the user's permission to manage the VM.

Source: [libvirt capabilities](https://libvirt.org/formatcaps.html).

Continue: return to [[Symbolic link to zram for image file#Create a directory pool|storage pool setup]]
after verifying directory access.
