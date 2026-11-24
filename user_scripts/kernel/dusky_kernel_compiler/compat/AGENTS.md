# NVIDIA / kernel update maintenance

Keep the kernel compiler hardware-specific. Do not solve driver compatibility
by retaining unrelated kernel options, enabling Nouveau alongside NVIDIA DKMS,
forcing runtime suspend, or changing a user's compositor/rendering choice.

## Established regression

Linux 7.3+ isolates kernel threads in nullfs. The tested Arch NVIDIA open
620.73.04 package runs PCI probe in a dedicated kthread (`nv-probe-<dev>`). Its
unscoped `filp_open(/sys/bus/pci/devices/.../config)` in
`nv_get_pci_sysfs_config()` returns ENOENT, leaving `nvl->sysfs_config_file`
NULL; `nv_dynamic_power_available()` consequently returns false and the GPU
remains in D0 instead of reaching runtime D3cold. Parent ACPI `_PR3` passes.
This RTD3 patch is the D3cold/sleep fix: without it NVIDIA never goes to sleep.

Wrapping that single open in `scoped_with_init_fs()` fixes the actual defect.
Keep NVIDIA's sysfs validation and runtime PM callbacks intact. Do not use the
historical 615.71.09 patch: it replaces those callbacks and is not the verified
fix. Do not call internal driver functions by guessed addresses: a previous
diagnostic did that and faulted on IBT. No such calls are in production code.

Upstream 620.73.04 resolved the Linux 7.4 UVM/DRM build breaks (`uvm_hmm.c`,
`nvidia-drm-drv.c`, `conftest.sh`, and `header-presence-tests.mk`), so no
separate Linux 7.4 build-fix overlay is maintained.

## Integration contract

- `kernel_nvidia.py` owns the tested version (`620.73.04`), original/repaired
  `nv-pci.c` hashes and unchanged `nv.c` runtime PM callback hash, overlay
  planning, ELF import verification, and passive power telemetry.
- `prepare_nvidia_compat()` gates on actual installation-host PCI hardware and
  installed open-DKMS package/version. It runs before pacman hooks. It does not
  run on build-only/export operations or use the builder's GPU for a remote
  target.
- Source hashes prevent blindly applying this patch to different sources with
  the same version. Preserve existing `/etc/dkms` directives; never modify
  package-owned `/usr/src` files. Repeated preparation must be idempotent.
- The overlay checks the target kernel's `include/linux/fs_struct.h` for
  `#define scoped_with_init_fs()`. The imported `userspace_init_fs` symbol is
  `EXPORT_SYMBOL_GPL`; this fix is for the open MIT/GPL module, not the
  proprietary flavor.
- After the normal DKMS audit, verify the installed ELF actually imports
  `userspace_init_fs` for this known source. A DKMS `installed` status alone is
  insufficient. Repair only the requested kernel, then verify again before
  initramfs and boot promotion. No background watchdog, power-policy rewrite,
  or GPU polling service.

## When NVIDIA or the kernel changes

1. Inspect installed package versions, target headers/API/export, actual DKMS
   source, and exact build toolchain. Read the relevant upstream change. Do not
   update version constants or hashes merely to make a rejected source pass.
2. Determine whether upstream fixed or replaced the sysfs-open dependency (and
   verify `nv.c` runtime PM callbacks and any new kernel build APIs). New
   releases are deliberately not patched automatically. If needed, port the
   narrow fix, review the entire changed function and actual probe call path,
   then update the version, all relevant hashes, assets, and regression
   fixtures together. If the same-version source already contains the fix,
   remove its old managed overlay directive before rebuilding; applying the
   patch twice would fail.
3. Run the full tests with Python 3.15 and temporary files on RAM. Include
   non-NVIDIA hosts, Intel+NVIDIA, AMD+NVIDIA, NVIDIA-only, Nouveau,
   proprietary, unknown versions, changed/already-fixed source, compressed
   modules, repeated config sourcing, stale DKMS modules, and failed repair.
4. Keep build workspace paths runtime-discovered/configured; do not hardcode
   usernames, home directories, or machine-specific device identifiers.
5. Boot the final clean module (not only a logging build), check RTD3 proc
   status, passively sample GPU, sibling audio function, and parent bridge,
   then test wake, real Wayland offload rendering, and return to D3cold. Do not
   run `nvidia-smi` inside idle sampling. Test external-display behavior when
   relevant hardware is available; do not require D3cold while NVIDIA drives an
   active display.

## Verification evidence

- **620.70.06 on Linux 7.3-rc7 (completed):** Fixed clean DKMS build passed
  reboot verification: 61/61 D3cold idle samples over 60 seconds with zero
  active-time increase, three `nvidia-smi` wake/sleep cycles, a 600-frame
  Wayland Vulkan render, and three 3,600-frame Wayland rendering cycles each
  returning to D3cold without kernel faults or NVIDIA Xid errors.
- **620.73.04 on Linux 7.4+ (pending live reboot verification):** Untouched
  `nv-pci.c` and `nv.c` match the validated `620.70.06` SHA-256 digests
  byte-for-byte, the RTD3 patch applies cleanly with zero fuzz to produce the
  validated repaired `nv-pci.c` digest, and upstream `620.73.04` already covers
  Linux 7.4 UVM/DRM build compatibility. Append live reboot D3cold results here
  after post-reboot verification completes.

Raw local evidence stays under `~/Documents/kernel_check/`, never in the source
tree. Tests belong in `tests/`.
