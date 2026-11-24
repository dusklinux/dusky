# NVIDIA open 620.73.04: runtime D3cold on Linux 7.3+

Linux 7.3+ starts kernel threads in a private empty nullfs. The NVIDIA open
620.73.04 package probes the GPU from a dedicated kthread and uses `filp_open()`
to retain its PCI sysfs config file. The open returns ENOENT, the saved file
pointer remains NULL, and NVIDIA reports RTD3 as unsupported (`Runtime D3
status: Not supported`), leaving the GPU in PCI D0. Successful DKMS compilation
does not detect this runtime failure.

`nvidia-620.73.04-linux-7.3-rtd3.patch` uses the kernel's
`scoped_with_init_fs()` API around that one open. It preserves NVIDIA's sysfs
validation, saved file handling, runtime PM callbacks, and ACPI checks. It adds
no kernel options, drivers, resident services, or polling.

Upstream 620.73.04 already includes Linux 7.4 build compatibility for
`uvm_hmm.c`, `nvidia-drm-drv.c`, `conftest.sh`, and `header-presence-tests.mk`,
so no separate Linux 7.4 build-fix patch is carried.

## Validation status

- **Source/patch parity:** Untouched `620.73.04` `nv-pci.c` and `nv.c` are
  byte-for-byte identical to the previously verified `620.70.06` sources, and
  the RTD3 patch applies cleanly with zero fuzz to produce the identical
  repaired `nv-pci.c` digest.
- **Prior 620.70.06 live evidence (Linux 7.3-rc7):** Changed RTD3 from
  unsupported/D0 to enabled (fine-grained)/D3cold with video memory off; passed
  clean-build reboot verification with 61/61 D3cold idle samples over 60
  seconds (zero active-time increase), three `nvidia-smi` wake/sleep cycles, a
  600-frame Wayland Vulkan render, and three 3,600-frame Wayland rendering
  cycles returning to D3cold without kernel faults or NVIDIA Xid errors.
- **620.73.04 live reboot evidence (Linux 7.4+):** Pending user build, install,
  and post-reboot verification.

See the [kernel maintainer's explanation of the filesystem change](https://patchew.org/linux/20260814-vfs-v73-6e884cb31ac9@brauner/).

## Compiler integration

The compiler prepares this overlay automatically during local package
installation when NVIDIA PCI hardware and `nvidia-open-dkms` 620.73.04 are
present. It checks the `nv-pci.c` and `nv.c` runtime-PM hashes, verifies the
`kernel-open` DKMS flavor, preserves existing `/etc/dkms` overrides, and runs
before pacman's hooks. The DKMS override applies the RTD3 patch whenever the
target kernel headers define `#define scoped_with_init_fs()` in
`include/linux/fs_struct.h` (Linux 7.3, 7.4, and newer) and skips it on older
trees.

After DKMS runs, the compiler verifies the installed module's actual
`userspace_init_fs` ELF import in RAM, rebuilding only the target kernel if
absent and re-verifying before initramfs generation or boot promotion. Already
repaired source is recognized by its hash. Package-owned `/usr/src` files are
never modified.

Intel/AMD-only hosts skip this path without importing `kernel_nvidia`.
Build-only and remote export operations never prepare the builder's drivers.
Other versions and flavors are not patched automatically. Follow
[AGENTS.md](AGENTS.md) when validating a new release; do not blindly substitute
a new version number.

`--doctor` and `--doctor --json` report passive PCI/proc NVIDIA power state
after boot. They do not force suspend or require a display-driving GPU to sleep.

## Manual driver-only installation

From this directory, with `nvidia-open-dkms` 620.73.04 and `dkms` 3.4.5
installed:

```sh
sudo install -Dm644 nvidia-620.73.04-linux-7.3-rtd3.patch /etc/dkms/nvidia/patches/nvidia-620.73.04-linux-7.3-rtd3.patch
sudo install -Dm644 nvidia-620.73.04.conf /etc/dkms/nvidia-620.73.04.conf
sudo dkms build nvidia/620.73.04 -k "$(uname -r)" --force
sudo dkms install nvidia/620.73.04 -k "$(uname -r)" --force
```

Merge with an existing version-specific override if one exists. The override
checks `${kernel_source_dir}/include/linux/fs_struct.h` for
`#define scoped_with_init_fs()`, so Linux 7.3, 7.4, and newer kernels receive
the RTD3 patch while older trees do not. DKMS copies the patch into its private
build tree; package-owned `/usr/src` files stay unchanged. The version-specific
override does not apply to later NVIDIA releases: recheck the source before
porting.

Reboot into the target kernel to activate the rebuilt module. If that target's
initramfs embeds NVIDIA modules, regenerate its mkinitcpio preset before
rebooting (not needed when `MODULES=()` and no mkinitcpio drop-in includes
NVIDIA modules). Verify `/proc/driver/nvidia/gpus/*/power` and the
corresponding PCI `power_state` after idle, then exercise GPU wake and Wayland
offload rendering followed by return to D3cold.

This opt-in fix targets the open MIT/GPL module: its `userspace_init_fs` kernel
API dependency is `EXPORT_SYMBOL_GPL`. Do not apply it to the proprietary
module flavor.

The older `nvidia-615.71.09-linux-7.3.patch` is a historical, unused artifact
that replaces runtime PM callbacks and is not the fix validated here. Only the
source-validated 620.73.04 repair is integrated into the compiler.
