# Zram swap

Zram is a compressed block device backed by RAM. Used as swap, it can reduce disk I/O under memory pressure, but compression consumes CPU and memory; its effect depends on workload and hardware. Its configured device size is an **uncompressed capacity**, not RAM reserved in advance. Inspect actual usage with `zramctl` and `swapon --show`.

The offline installer already configures zram using a drop-in. Inspect existing configuration before adding another one; see [[zram-generator config]]. On another system, install `zram-generator`, configure a swap device, and verify it after boot. Kernel VM tuning is optional and should be measured; see [[Optimizing Kernel Parameters for ZRAM]].

Zswap is a separate compressed cache in front of swap. If enabled with zram, pages can be compressed by zswap before reaching zram, reducing the benefit of the latter. Check the running kernel's state instead of assuming a default:

```bash
cat /sys/module/zswap/parameters/enabled
```

If the path is absent, zswap may be unavailable in that kernel. To choose zram as the compressed-swap mechanism, `zswap.enabled=0` can be added to the **active boot loader's** kernel command line; verify after reboot. See [ArchWiki: zram](https://wiki.archlinux.org/title/ZRAM).

Zram swap is volatile and cannot serve as a hibernation target. Hibernation needs appropriately configured persistent swap and early resume; see [ArchWiki: hibernation](https://wiki.archlinux.org/title/Power_management/Suspend_and_hibernate#Hibernation). A separate zram filesystem such as `/mnt/zram1` is temporary storage, not swap, and its contents disappear when reset, unmounted, or powered off.
