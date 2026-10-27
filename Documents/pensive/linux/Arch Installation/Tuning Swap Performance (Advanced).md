# Swap behavior and priority

`vm.swappiness` influences the kernel's relative preference for reclaiming anonymous memory through swap versus reclaiming file-backed pages. It is not a percentage of RAM usage or a threshold at which swapping begins. The accepted range is 0–200; inspect the running system before considering a change:

```bash
sysctl vm.swappiness
swapon --show
```

There is no universally optimal value. Measure responsiveness, memory pressure and I/O under the workloads that matter before and after changing it. See the [kernel VM sysctl documentation](https://docs.kernel.org/admin-guide/sysctl/vm.html). For a temporary experiment, replace `VALUE` with an appropriate integer:

```bash
sudo sysctl -w vm.swappiness=VALUE
```

To persist a chosen value, create a suitable file under `/etc/sysctl.d/`; check other files for overrides. [[Optimizing Kernel Parameters for ZRAM]] discusses scope and verification.

## Swap priority

With multiple active swap areas, higher priorities are preferred; equal priorities can be used together. A generator-managed zram device gets its priority from `swap-priority=` in the applicable zram-generator configuration, **not** a duplicate `/dev/zram0` fstab entry. For a persistent swap partition, an fstab entry can specify a priority, for example:

```fstab
UUID=YOUR-SWAP-UUID none swap defaults,pri=10 0 0
```

Replace the UUID with the actual swap UUID and check `swapon --show` after boot. Priority alone does not guarantee a performance gain or configure hibernation. See `man swapon`, `man 5 zram-generator.conf` and [[Managing Swap on a Running System]].
