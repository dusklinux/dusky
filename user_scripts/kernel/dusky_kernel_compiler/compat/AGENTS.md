# NVIDIA / kernel update maintenance

Keep the kernel compiler hardware-specific. Do not solve driver compatibility
by retaining unrelated kernel options, enabling Nouveau alongside NVIDIA DKMS,
forcing runtime suspend, or changing a user's compositor/rendering choice.

## Established regression

Linux 7.3 isolates kernel threads in nullfs. The tested Arch NVIDIA open
615.78.08 package runs PCI probe in a dedicated kthread. Its unscoped
`filp_open(/sys/bus/pci/devices/.../config)` returned ENOENT, leaving
`nvl->sysfs_config_file` NULL; `nv_dynamic_power_available()` consequently
returned false and the GPU remained D0. Parent ACPI _PR3 passed.

Wrapping that single open in `scoped_with_init_fs()` fixed the actual defect.
Keep NVIDIA's file validation and runtime PM callbacks intact. Do not use the
historical 615.71.09 patch: it replaces those callbacks and is not the verified
fix. Do not call internal driver functions by guessed addresses: a previous
diagnostic did that and faulted on IBT. No such calls are in production code.

## Integration contract

- `kernel_nvidia.py` owns the tested version, original/repaired PCI source and
  unchanged runtime PM callback source hashes,
  overlay planning, ELF import verification and passive power telemetry.
- `prepare_nvidia_compat()` gates on actual installation-host PCI hardware and
  installed open-DKMS package/version. It runs before pacman hooks. It does not
  run on build-only/export operations or use the builder's GPU for a remote target.
- Source hashes prevent blindly applying this patch to different sources with
  the same version. Preserve existing `/etc/dkms` directives; never modify
  package-owned `/usr/src` files. Repeated preparation must be idempotent.
- The overlay checks the target kernel headers for the API. The imported
  `userspace_init_fs` symbol is GPL-exported; this fix is for the open MIT/GPL
  module, not the proprietary flavor.
- After the normal DKMS audit, verify the installed ELF actually imports that
  symbol for this known source. A DKMS 'installed' status alone is insufficient.
  Repair only the requested kernel, then verify again before initramfs and boot
  promotion. No background watchdog, power-policy rewrite or GPU polling service.

## When NVIDIA or the kernel changes

1. Inspect installed package versions, target headers/API/export, actual DKMS
   source and exact build toolchain. Read the relevant upstream change. Do not
   update version constants/hashes merely to make a rejected source pass.
2. Determine whether upstream fixed/replaced the sysfs-open dependency. New
   releases are deliberately not patched automatically. If needed, port the
   narrow fix, review the entire changed function and actual probe call path,
   then update the version, all relevant hashes, assets and regression fixtures together.
   If the same-version source already contains the fix, remove its old managed
   overlay directive before rebuilding; applying the patch twice would fail.
3. Run the full tests with Python 3.15 and temporary files on RAM. Include
   non-NVIDIA hosts, Intel+NVIDIA, AMD+NVIDIA, NVIDIA-only, Nouveau, proprietary,
   unknown versions, changed/already-fixed source, compressed modules, repeated
   config sourcing, stale DKMS modules and failed repair.
4. Use `/mnt/zram1` for builds on this development machine. If a kernel rebuild
   is needed, reuse battery/7.3-rc7 while that cached tree is still the intended
   target; do not change its identity just to test orchestration. Other targets
   use the compiler's discovered/configured storage paths.
5. Boot the final module (not only a logging build), check RTD3 proc status,
   passively sample GPU, sibling functions and parent bridge, then test wake,
   real Wayland offload rendering and return to D3cold. Do not run nvidia-smi
   inside idle sampling. Test external-display behavior when relevant hardware
   is available; do not require D3cold while NVIDIA drives an active display.

Verified development evidence: stock D3cold; unchanged custom kernel initially
D0; fixed logging build D3cold for 61/61 samples over 60 seconds, three successful
nvidia-smi wake/sleep cycles and a 600-frame NVIDIA Wayland Vulkan render followed
by D3cold. The final clean DKMS build subsequently passed reboot verification:
61/61 D3cold idle samples, successful wake/return and NVIDIA Wayland rendering
followed by D3cold, without kernel faults or Xid. Three additional 3,600-frame NVIDIA
Wayland rendering cycles each returned to D3cold without kernel faults/Xid.
Compiler integration passed 109 regression tests, actual source/ELF checks and
repeated live overlay preparation. Raw local evidence stays under
`~/Documents/kernel_check/`, never in the source tree. Tests belong in `tests/`.
