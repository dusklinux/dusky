# GPU passthrough toggle

`nvidia_vfio_bind_unbind.py` retains its historical filename but discovers GPU
slots and drivers at runtime. It stages boot configuration; it does not detach
a GPU from a running desktop. Keep `user_scripts/gpu/gpu_disable_toggle.py` in
the same script tree: the toggle shares its PCI discovery, boot-entry lookup,
atomic writes and mkinitcpio inspection, without invoking GPU removal or power
controls. The two tools share a lock and conflicting disable state is rejected.

Run as your normal user; changes request sudo. Python 3.15+, kernel 7.3+,
systemd 262+, systemd-boot and mkinitcpio are the supported platform. Release
candidate kernels can be used for development testing but do not certify the
final ISO baseline. No old bootctl JSON schemas or other initramfs generators
are guessed.

```sh
python3 nvidia_vfio_bind_unbind.py --bind --dry-run
python3 nvidia_vfio_bind_unbind.py --bind
# Reboot, then verify actual drivers:
python3 nvidia_vfio_bind_unbind.py --status

python3 nvidia_vfio_bind_unbind.py --unbind
# Reboot, then verify the host drivers:
python3 nvidia_vfio_bind_unbind.py --status
```

A saved Phase 3 slot is reused. Without one, multiple GPUs are offered in a
menu; `--slot DOMAIN:BUS:SLOT` selects explicitly. Every function in that GPU
slot moves together. `--entry ENTRY_ID` selects a boot entry on first use;
subsequent operations retain it. A boot/last GPU requires explicit
`--allow-boot-vga`. An AMD firmware workaround is available as
`--amd-force-enable`; ordinary AMD hardware uses its default AMD-Vi behavior.

Binding checks for duplicate vendor/device IDs outside the selected slot,
IOMMU group conflicts and active VM consumers. Identical device IDs require a
separate BDF-specific assignment method; this ID-based toggle refuses to claim
unselected matching devices. Bridges in a group are not claimed. Missing live
IOMMU groups are reported: enable firmware IOMMU support and verify after boot
before assigning devices to a VM. PCI topology, reset support, external display
wiring and guest GPU drivers still determine which hardware is usable.

The toggle adopts current Phase 3 `arsonix-vfio.conf` files, preserves unrelated
VFIO IDs/module options and does not blacklist audio/USB infrastructure or a
GPU driver still used by another host GPU. An unrelated competing VFIO IDs
file is reported rather than overwritten. Other main/drop-in configuration
files are retained. The managed modules drop-in loads `vfio_pci` early; its
dependencies are resolved by the kernel rather than prescribing obsolete
module lists. Preset overrides bypassing the supported configuration are
reported before writes. Rebuilt images and staged boot options are verified.

State is journaled at `/var/lib/arsonix/vfio-toggle.json` before configuration
writes. On a write, build or verification failure, **do not reboot**: fix the
reported cause and rerun the same action. The original host baseline is retained
across retries. `--unbind` can stage a return to that baseline. `--no-rebuild`
leaves an incomplete journal deliberately; rerun without it before rebooting.
Converged runs verify the images/configuration without rewriting files or
rebuilding. `--status` returns 0 only when saved configuration and actual drivers
match, and 2 for unconfigured, pending or drifted state; operation failures
return 1. Staging success alone does not mean a live GPU has changed driver.

For RAM-backed VMs, shut down the guest and copy its disk to persistent storage
before a host reboot. Verify the copied qcow2 and update the domain's disk path.
Back up the domain, NVRAM and TPM together using
`../backup_restore/backup_restore_virt_xml_config.py`; that script excludes VM
disks. Keep an original domain XML without hostdev devices for recovery. To test
a GPU, attach every selected function to the guest while preserving its setup
console, then inspect guest drivers, Looking Glass output and reset behavior.

Regression checks:

```sh
python3 -m unittest discover -s tests -p test_vfio_toggle.py -v
python3 -m unittest discover -s ../../gpu/tests -p test_gpu_disable_toggle.py -v
```

These fixture checks cover vendor/driver choices, host-driver sharing, ID
collisions, Phase 3 adoption, quoted boot options, UKI fragments, write/build
failures, retries and no-op convergence. They complement hardware reboot tests;
they do not prove every GPU's reset behavior.

Interfaces were checked against the supplied companion and Phase 3 scripts,
installed command help, [kernel VFIO documentation](https://docs.kernel.org/driver-api/vfio.html),
[bootctl](https://freedesktop.org/software/systemd/man/latest/bootctl.html), and
[mkinitcpio configuration](https://man.archlinux.org/man/core/mkinitcpio/mkinitcpio.conf.5.en).
