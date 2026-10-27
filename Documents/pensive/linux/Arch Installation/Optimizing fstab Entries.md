# Review fstab entries

Run this step inside the target chroot after [[Generate the fstab File]]. `/etc/fstab` records mounts to configure at boot. Verify the filesystem UUID, mount point, type and subvolume for each entry against the layout you actually created. The root and home subvolumes of one Btrfs filesystem share a filesystem UUID but use different `subvol=` options. Check the ESP separately. Do not copy UUIDs or device names from [[fstab reference]].

```bash
cat /etc/fstab
blkid
```

The mount options in [[Mounting Partitions]] express the example's choices. Compression and `noatime` are workload-dependent choices, not guaranteed speed improvements. Btrfs normally detects SSDs and appropriate discard behavior; the first mounted subvolume controls most filesystem-wide options. Keep consistent options across entries and change them only for a reason relevant to your hardware and workload. `genfstab` may include kernel-reported defaults; do not remove or add flags merely because an unrelated example does so.

Consult `man fstab`, `man btrfs` and [Btrfs mount options](https://btrfs.readthedocs.io/en/latest/ch-mount-options.html). Inspect the result carefully before rebooting.
