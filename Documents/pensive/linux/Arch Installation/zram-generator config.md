# Configure zram-generator

`zram-generator` is a systemd generator, not a continuously running service. It reads configuration at boot and creates units for swap or a temporary filesystem. The example below configures only a swap device; it is not the more elaborate configuration used by the Dusky installer.

## Inspect existing configuration first

```bash
pacman -Q zram-generator
ls -l /etc/systemd/zram-generator.conf /etc/systemd/zram-generator.conf.d /run/systemd/zram-generator.conf.d /usr/lib/systemd/zram-generator.conf.d 2>/dev/null
swapon --show
zramctl
```

Inspect the files that exist. A `.conf.d` drop-in overrides the main file for options it sets; drop-ins are ordered by filename. Your installer uses `/etc/systemd/zram-generator.conf.d/99-zram0.conf`, so adding a main file may not change its effective settings. The installed `man zram-generator.conf` documents precedence and options. Do not blindly create a second configuration for the same device.

## Example for a system without existing zram configuration

Install `zram-generator` from the configured repositories, then create `/etc/systemd/zram-generator.conf`:

```ini
[zram0]
zram-size = min(ram / 2, 4096)
compression-algorithm = zstd
swap-priority = 100
```

`ram` is usable RAM in MiB. The example caps uncompressed device capacity at 4096 MiB; it is not a reservation of 4096 MiB of physical RAM. `zstd` requires kernel support; the generator can continue with another algorithm if the requested one is unavailable, so inspect the actual result. Higher swap priority is preferred. These values are an illustrative starting point, not a universal optimum.

During installation, write the configuration inside the target and verify after boot. On an already booted system, the simplest way to apply configuration is to reboot. To add a new device without rebooting, start the generated swap unit; it requires and starts the device setup service:

```bash
sudo systemctl daemon-reload
sudo systemctl start dev-zram0.swap
swapon --show
zramctl
systemctl status systemd-zram-setup@zram0.service
```

To change an existing device, consult `man zram-generator`: recreating it moves swapped pages elsewhere and resets the device. A zram filesystem is unmounted and its contents are lost. If removing a device or mount point from the configuration, stop its unit before reloading the manager. Check the journal for failures:

```bash
journalctl -b -u systemd-zram-setup@zram0.service
```

A separate `[zram1]` with `mount-point=` creates a temporary filesystem, not swap. It shares physical RAM with the rest of the system. Refer to the installed manual for filesystem settings and lifecycle. See also [[ZRAM Setup]].
