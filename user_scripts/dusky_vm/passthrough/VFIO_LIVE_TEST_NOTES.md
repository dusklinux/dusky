# VFIO toggle live test — 2026-10-25

The actual host was an Intel i7-12700H / Intel i915 display with an NVIDIA
RTX 3050 Ti Laptop GPU and its HDMI-audio function in IOMMU group 15. Testing
used kernel `7.3.0-rc7-dusky-battery`, systemd 262.1, Python 3.15.1, mkinitcpio
42.2, QEMU 11.1.2 and libvirt 12.8. The kernel is a release candidate; this does
not certify the declared final 7.3+ ISO baseline.

The original toggle read obsolete bootctl JSON fields, assumed NVIDIA, edited
all initramfs config files, and rebuilt every time. It now shares the reference
GPU disabler's PCI/boot inspection and durable-write helpers. The shared APIs
accept the toggle's own managed drop-ins; the GPU disabler's default behavior
passed its retained suite. GPU bus removal and firmware power controls are
separate from VFIO binding.

## Preserving the VM and first host reboot

Windows was shut down before copying its RAM-backed qcow2 to persistent media.
Both copies passed qcow2 checks, and complete-file SHA-256 hashes matched at
copy time. The domain was changed to the persistent disk. Windows booted from
that copy, answered QGA and shut down cleanly. The disk subsequently changed
normally during testing. The domain, firmware and TPM backup was verified
(51 payloads); the backup tool excludes guest disks.

The actual bind operation rebuilt every configured initramfs, checked their
VFIO content, and verified the selected systemd-boot options. Its converged
rerun preserved the journal inode/mtime and skipped rewriting/rebuilding.
After the authorized host reboot, both NVIDIA functions used vfio-pci and
Intel still used i915. The root boot audit returned 0. A normal-user status
check then exposed a reporting defect: private boot files were mistaken for
drift. The fixed check distinguishes permission failures, advises sudo, and
returns unverified rather than claiming drift or success.

## Windows GPU and workload checks

The test VM retained QXL/SPICE and Looking Glass while both GPU functions were
assigned. Initially Windows exposed the GPU through Basic Display with Code 10.
The latest released notebook package located for this test was NVIDIA 621.01
WHQL. Windows validated its NVIDIA Authenticode signature. The display-driver
installer returned 0, and the loaded driver reported `32.0.16.2101` / 621.01,
with the NVIDIA PnP device healthy. The user's older local Studio installer
was not installed.

A bounded Direct3D test selected the NVIDIA hardware adapter explicitly by
vendor ID through DXGI, rejected software adapters, and used D3D11 feature
level 11.1. It allocated 32 render textures (128 MiB total), cleared them to
known colors, copied each to a staging resource, mapped the result and compared
sampled pixels. Device-removed status was checked afterward. Interface layouts
were checked against Microsoft's SDK headers and documented APIs. The fixture
is saved with the audit artifacts for repetition.

- Initial test: 1,024 clears/readbacks and 3,072 sampled pixel checks passed.
- Longer test: 10 runs, 81,920 clears/readbacks and 245,760 sampled pixel checks
  passed in 34.06 seconds total. Peak texture allocation per run was 128 MiB.
- Twenty NVIDIA telemetry samples reported driver 621.01; observed utilization
  reached 92% and reported temperature reached 64.25°C during the samples.
- Three clean guest shutdown/start cycles each returned with driver 621.01 and
  a passing hardware readback test. No forced guest destruction was used.
- Looking Glass logged a hardware frame graph (`mode=hw`) and D3D12 transport.
  The desktop was captured before the cycles; the user also confirmed a clear
  Looking Glass desktop after the final cold start.

The SPICE console could be black while the usable desktop/sign-in path was on
Looking Glass after the driver changed the saved monitor layout. Try the LG
window as well as SPICE, and wake an idle display before replacing drivers.

## Open finding

One host DMAR DMA-write fault was logged during the rapid shutdown sequence at
17:21:03, just before VM teardown/reset. It was not logged during the longer
render/readback workload. Resets completed and all three
startup checks passed. Its cause has **not** been established; no generic script
configuration fix was substantiated by the investigation. It is retained as an
open hardware/driver/kernel-stack finding, rather than counted as fault-free
validation. Repeat this check on the final supported kernel and investigate any
recurrence or workload fault before declaring the complete GPU stack ready.

A successful toggle status verifies configuration/driver assignments, not
absence of kernel DMA faults. The bounded resource test is not a complete VRAM,
thermal, gaming, HDR or physical-audio qualification.

## Regression checks and portability

The toggle suite passed 20 tests, including first-use discovery without Phase 3
or toggle state, AMD/Intel/NVIDIA selection fixtures, shared host drivers,
duplicate IDs, IOMMU group endpoints/bridges, Phase 3 adoption, quoted options,
UKI fragments, write/build failures, journal validation and converged reruns.
The reference GPU suite passed 68 tests and backup suite passed 45 tests.
AMD/Intel passthrough and other physical machines were not empirically tested.
A complete fresh Arch/Windows installation was not repeated.

## Return-to-host check

Windows shut down cleanly, its tested GPU assignments were removed, and the
current domain/firmware/TPM backup was verified. The actual --unbind rebuilt and
verified the boot images/options. Its converged rerun retained the journal
inode/mtime and skipped rebuilding. The final shutdown added no further DMAR
faults. The second host reboot returned NVIDIA video to `nvidia`, its HDMI
audio to `snd_hda_intel`, and Intel to `i915`. Privileged toggle status returned
0; host `nvidia-smi` worked with Linux driver 615.78.08. No DMAR fault or NVIDIA
Xid was present in the inspected new-boot log. This does not close the isolated
fault from the previous boot.

The temporary boot audit ran at 17:40:49, before HDMI audio bound at 17:40:50,
and correctly returned 2 at that instant. A later status check passed. Status
is an immediate observation, not a wait-for-initialization service; check again
after login before diagnosing a persistent failure. The temporary audit unit
was removed after both boot modes were checked; its log was retained.

Windows booted from persistent storage without GPU hostdevs, and QGA returned
Windows 11 build 26300. Its existing interface referenced `virbr0` directly;
the first start preceded the lazy-started NAT network and failed. After that
network became active, startup passed. The saved domain was updated to refer
to libvirt network `default`, preserving its MAC and e1000e model. The deployment
script already creates named-network interfaces. This VM correction allows
libvirt to manage the network dependency rather than relying on the bridge
already existing.

## Saved evidence

Machine-specific files are under the persistent VM directory in
`vfio-review-20261025-124548/`: original scripts/XML/boot images, verified VM-state
backup, source references, `RESUME.md`, and `postboot-bind/` test programs,
results, telemetry and kernel events. The temporary
`dusky-vfio-postboot-check.service` recorded driver assignments across the test
reboots and has now been removed. Final return evidence includes
`return-host-status.log`, `return-host-kernel.log` and `vfio-postboot-complete.log`.

References: [NVIDIA 621.01 notebook release](https://www.nvidia.com/en-us/geforce/drivers/details/282413/),
[NVIDIA silent installation](https://docs.nvidia.com/datacenter/tesla/driver-installation-guide/windows.html),
[Direct3D device creation](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-d3d11createdevice),
[Microsoft SDK headers](https://github.com/microsoft/win32metadata/tree/main/generation/WinSDK/RecompiledIdlHeaders),
and [kernel VFIO documentation](https://docs.kernel.org/driver-api/vfio.html).
