# NVIDIA open 615.78.08: runtime D3 on Linux 7.3

Linux 7.3 starts kernel threads in a private empty nullfs. The NVIDIA open
615.78.08 package tested here probes the GPU from a dedicated kthread and uses
`filp_open()` to retain its PCI sysfs config file. The open returns ENOENT,
the saved file pointer remains NULL, and NVIDIA reports RTD3 as unsupported.
Successful DKMS compilation does not detect this runtime failure.

`nvidia-615.78.08-linux-7.3-rtd3.patch` uses the kernel's
`scoped_with_init_fs()` API around that one open. It preserves NVIDIA's sysfs
validation, saved file handling, runtime PM callbacks, and ACPI checks.
It adds no kernel options, drivers, resident services or polling.

The isolated test on Linux 7.3-rc7 changed RTD3 from unsupported/D0 to enabled
(fine-grained)/D3cold. GPU video memory was off. The GPU, audio function and
parent bridge stayed D3cold for all 61 samples over 60 seconds, with zero
increase in active time. This establishes the idle fix on the tested machine;
three subsequent `nvidia-smi` wake cycles also succeeded and each returned to
D3cold in approximately 23 seconds without kernel faults or NVIDIA Xid errors.
A 600-frame Vulkan rendering test on NVIDIA through Wayland also succeeded
and returned to D3cold approximately 9.5 seconds after exit. A rebuild/install
through DKMS with the minimal patch and no diagnostic logging succeeded;
the final rebuilt binary still needs its separate reboot verification.
Three longer 3,600-frame Wayland rendering cycles also succeeded (10,800
frames total, about 75–77 seconds rendering per cycle), each returning to
D3cold within 10.5–11.1 seconds. No kernel faults or NVIDIA Xid errors appeared.
This does not establish behavior for every GPU or driver source revision.

See the [kernel maintainer's explanation of the filesystem change](https://patchew.org/linux/20260814-vfs-v73-6e884cb31ac9@brauner/).

## Compiler integration

The compiler prepares this overlay automatically during local package
installation when NVIDIA PCI hardware and nvidia-open-dkms 615.78.08 are
present. It checks the PCI-probe and runtime-PM source hashes, preserves existing DKMS overrides,
and runs before pacman's hooks. Afterwards it checks the installed module's
actual `userspace_init_fs` ELF import, rebuilding only if absent and checking
again before initramfs generation or boot promotion. Already repaired source
is recognized by its hash. The compiler leaves package-owned sources intact.

Intel/AMD-only hosts skip this path without importing the NVIDIA helper.
Build-only and remote export operations never prepare the builder's drivers.
Other versions/flavors are not patched automatically. Follow [AGENTS.md](AGENTS.md)
when validating a new release; do not just substitute a new version number.

`--doctor` and `--doctor --json` report passive PCI/proc NVIDIA power state
after boot. They do not force suspend or require a display-driving GPU to sleep.

## Manual driver-only installation

From this directory, with nvidia-open-dkms 615.78.08 and dkms 3.4.4 installed:

```sh
sudo install -Dm644 nvidia-615.78.08-linux-7.3-rtd3.patch /etc/dkms/nvidia/patches/nvidia-615.78.08-linux-7.3-rtd3.patch
sudo install -Dm644 nvidia-615.78.08.conf /etc/dkms/nvidia-615.78.08.conf
sudo dkms build nvidia/615.78.08 -k "$(uname -r)" --force
sudo dkms install nvidia/615.78.08 -k "$(uname -r)" --force
```

Merge with an existing version-specific override if one exists. The override
checks the target headers for the API, so the working stock 7.2 kernel is not
patched. DKMS copies the patch into its private build tree; package-owned
`/usr/src` files stay unchanged. The version-specific override does not apply
to later NVIDIA releases: recheck the source before porting this patch.

Reboot into the target kernel to activate the rebuilt module. If that target's
initramfs embeds NVIDIA modules, regenerate its mkinitcpio preset before
rebooting. Verify `/proc/driver/nvidia/gpus/*/power` and the corresponding PCI
`power_state` after idle, then exercise GPU wake and return to sleep.

This opt-in fix targets the open MIT/GPL module: its kernel API dependency is
GPL-exported. Do not apply it to the proprietary module flavor.

The older `nvidia-615.71.09-linux-7.3.patch` is a historical, unused artifact.
It replaces runtime PM callbacks and is not the fix validated here. Only the
source-validated 615.78.08 repair is integrated into the compiler.
