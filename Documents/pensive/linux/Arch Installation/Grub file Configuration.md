 Part 2: GRUB Configuration (`/etc/default/grub`)

Before installing the bootloader to the disk, you must configure its default behavior by editing the `/etc/default/grub` file. This file controls kernel parameters, appearance, and other boot-time options.

### 1. Open the Configuration File

Use a text editor like `nvim` to open the GRUB default configuration file.

```bash
nvim /etc/default/grub
```

### 2. Configure Kernel Parameters

Locate the `GRUB_CMDLINE_LINUX_DEFAULT` line. This line sets the kernel parameters that are applied during a normal boot. The default is often `"loglevel=3 quiet"`. Change it only if the installation needs different parameters.

Retain the existing parameters needed by your system, especially any required to unlock encrypted root or resume from hibernation. Add or change a parameter only when you understand its purpose for this installation. Replacing the whole line with an unrelated example can remove needed parameters. After editing, regenerate the configuration as described in [[Generating the Final Grub File]].

For example, `loglevel=7` increases kernel message verbosity; `zswap.enabled=0` disables zswap if that is your chosen configuration. Zswap and zram can coexist, though zswap in front of zram may reduce zram's benefit; see [[ZRAM Setup]]. Do not force `pcie_aspm=force` as a general installation default. Hardware and firmware determine whether it is appropriate.

### 3. Enable OS Prober (for Dual-Booting)

If you installed `os-prober` in the previous section, you must enable it in the GRUB configuration. Find the line `#GRUB_DISABLE_OS_PROBER=false` and uncomment it by removing the `#`.

If the line does not exist or is set to `true`, add or change it to:

```ini
GRUB_DISABLE_OS_PROBER=false
```


### 4. GRUB modules

GRUB's installed modules and generated configuration depend on the chosen storage and boot setup. Do not add partition-table preload modules solely because they appear in an unrelated recipe. See the [ArchWiki GRUB guide](https://wiki.archlinux.org/title/GRUB) for the selected layout.
