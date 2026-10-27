# Evaluating VM parameters for zram

Zram works without a special sysctl profile. Values that help one workload can hurt another, so start with the existing system settings and measure before changing them. In particular, fixed dirty-byte limits, memory reserves, cache pressure and map-count limits are not general requirements for zram. The previous example's table did not match its configuration values; it has been removed.

Inspect current values:

```bash
sysctl vm.swappiness vm.page-cluster vm.vfs_cache_pressure vm.dirty_bytes vm.dirty_background_bytes vm.watermark_scale_factor vm.watermark_boost_factor vm.min_free_kbytes vm.max_map_count
swapon --show
zramctl
```

`vm.swappiness` changes the relative reclaim cost of swap and filesystem-backed memory; `vm.page-cluster` affects swap read-ahead. Their useful values depend on the kernel, swap devices and workload. See [[Tuning Swap Performance (Advanced)]], the [kernel VM sysctl documentation](https://docs.kernel.org/admin-guide/sysctl/vm.html).

For a persistent change, create a file in `/etc/sysctl.d/` using the specific settings you have tested. `sysctl --system` reloads **all** sysctl configuration, not just that file, and changes the running system; use it only when that is intended. Check the effective value afterward and inspect the journal for errors. File ordering and overrides are documented in `man sysctl.d`.
