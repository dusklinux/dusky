---
title: "Storage Topology — Persistent vs RAM-Backed (ZRAM)"
tags:
  - kvm
  - storage
  - zram
  - libvirt
  - arch
aliases:
  - Relocating Storage to ZRAM
---

# Storage Topology — Persistent vs RAM-Backed

> [!abstract] Goal
> Choose where qcow2 images live. Canonical: `07_storage_setup.py` — persistent `/var/lib/libvirt/images` vs ephemeral `zram`/`tmpfs` vs custom, with POSIX ACL + backing-fstype audit.

## Options (interactive, `--path` for non-interactive)

| Choice | Example path | Volatile | Use case |
|---|---|---|---|
| **Persistent** | `/var/lib/libvirt/images` | no | daily driver, survives reboot |
| **Ephemeral** | `/mnt/zram1` / `/mnt/tmpfs` | **yes** | throw-away lab, SSD wear avoid, max I/O |
| **Custom** | `/mnt/media/…/kvm` | depends on mount | external NVMe, NAS cache |

```bash
# probe backing fact (longest-prefix /proc/self/mountinfo)
findmnt --target /mnt/zram1
grep zram /proc/self/mountinfo
```

> [!danger] Volatile = data loss on reboot
> Anything on `tmpfs`, `ramfs`, or `/dev/zram*` **vanishes** on power-off/reboot. Use only for VMs you can afford to lose. `07_*.py` warns if you pick persistent atop volatile or vice-versa.

## Legacy pattern (symlink) — retired

Old notes did:

```bash
sudo rmdir /var/lib/libvirt/images
sudo mkdir -p /mnt/zram1/os
sudo ln -nfs /mnt/zram1/os /var/lib/libvirt/images
ls -la /var/lib/libvirt/  # images -> /mnt/zram1/os
```

> [!warning] Legacy — why symlink is no longer recommended
> - Breaks libvirt `dir` pool bookkeeping (`pool-dumpxml` shows symlink target, not declared path).
> - Mount ordering: if `zram` device mounts late, libvirt starts with dangling link.
> - ACL traversal audit (`07_*:provision_acls`) expects a **real directory**, not a symlink chase.
> Prefer a **real pool**: create the directory, provision ACLs, then declare it as a libvirt pool (see below).

## Current approach (no symlink)

```bash
# 1. Create the target
TARGET=/var/lib/libvirt/images          # or /mnt/zram1 / /mnt/media/kvm
sudo mkdir -p "$TARGET"
sudo chmod 0711 "$TARGET"               # pipeline default before ACLs

# 2. Provision ACLs (07_storage_setup.py does this atomically with getfacl/setfacl, never strips)
# --x on every parent above TARGET, rwx + default:rwx on TARGET itself
# Verify:
getfacl "$TARGET"

# 3. Expose as libvirt pool so virt-manager sees it
sudo virsh pool-define-as arsonix-images dir --target "$TARGET"
sudo virsh pool-build arsonix-images
sudo virsh pool-start arsonix-images
sudo virsh pool-autostart arsonix-images
sudo virsh pool-refresh arsonix-images
```

## Verify

```bash
ls -ld "$TARGET"
findmnt --target "$TARGET"
virsh pool-list --all --details
virsh pool-dumpxml arsonix-images | grep -A2 '<target>'
```

## When ephemeral makes sense & RAM Budgeting

- You explicitly want a throwaway Windows/Linux lab and will copy out artifacts before reboot.
- SSD wear avoidance for heavy snapshot churn.

> [!important] RAM Capacity Heuristic (from `linux/deploy_vm.py:ram_storage_reason`)
> When hosting a VM disk in RAM (`/mnt/zram1` / `tmpfs`):
> 1. Host must have $\ge 24\,\text{GiB}$ free RAM available.
> 2. Total host RAM must cover: $\text{Disk Capacity} + \text{Guest RAM} + 4\,\text{GiB host safety reserve}$.
> 3. Example: A $4\,\text{GiB}$ guest with a $20\,\text{GiB}$ qcow2 disk requires at least $28\,\text{GiB}$ RAM available on the host and $20\,\text{GiB}$ free on the tmpfs filesystem.
> 4. Disks created on RAM filesystems should use `driver.cache=writeback` (not `none`) to avoid bypassing tmpfs page cache buffers.

Otherwise: **keep persistent** and use `qcow2` `lazy_refcounts` / `cluster_size=64k` (see `30_kvm_vm_deploy.py:provision_disk`).

See: [[Set ACL on the Image Directory]] (ACL deep dive), `07_storage_setup.py`.
