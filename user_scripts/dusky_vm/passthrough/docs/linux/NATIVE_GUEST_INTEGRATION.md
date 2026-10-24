# Linux guest integration

Run this **after creating the VM and enabling SSH in the guest**. Use the host's
desktop account. The script asks for SSH/sudo authentication when needed; it does
not save passwords or change SSH authentication.

```bash
python ~/user_scripts/dusky_vm/passthrough/linux/guest_integration.py host \
  --domain YOUR_VM --guest USER@GUEST_ADDRESS \
  --share /path/to/host/folder --restart
```

The default guest location is `~/Documents/a_host`. Change it with
`--mount-path PATH`; relative paths are relative to the guest desktop user's home.
Pass a directory, without `/*`. Sharing a directory includes its contents.

The script builds the clipboard package **inside the guest**, installs it,
enables **dusky_vdagent.service** for graphical sessions, and replaces the packaged
X11 session agent with the native Wayland agent. The SPICE daemon remains the
standard `spice-vdagentd` package component. Host clipboard integration uses your
existing virt-manager/virt-viewer SPICE connection, without an SSH clipboard relay.

The shared folder uses virtiofs, shared VM memory, and a systemd automount. The
initial memory/device change needs a full guest shutdown/start, not an in-guest
reboot. `--restart` requests graceful shutdown and never forces it. Without that
flag the configuration is prepared for the next full start. Re-running setup
doesn't add duplicate devices or overwrite the original VM definition backup.

Use `--no-clipboard` for file sharing alone. Phase 05 includes `virtiofsd` in host
staging. Guest build/runtime dependencies are installed automatically with pacman;
the build tools remain installed for future rebuilds.

## Native builds and temporary storage

Setup transfers the tracked recipe and the host's per-user override at
`$XDG_CONFIG_HOME/pacman/makepkg.conf` (normally `~/.config/pacman/makepkg.conf`).
Use `--makepkg-config FILE` to select another override. The guest's system
makepkg configuration and drop-ins provide the defaults; the supplied override
is applied last without changing the guest's existing configuration. Its
`RUSTFLAGS` (including `-C target-cpu=native`) are used by Cargo. Compilation
inside the guest targets the CPU features exposed to that VM, even when the
host CPU exposes more features.

Sources, Cargo/cache files, build outputs and the temporary package all go into
a private directory under `/tmp` when it is executable tmpfs. Otherwise setup
uses a temporary directory under the guest's
`~/.config/dusky/settings/dusky_vm/guest_integration/`. The directory is removed
after installation, including on build failure. Only `build.log` and a build
fingerprint remain in that settings directory. The installed binary and pacman
database necessarily persist on disk; tmpfs can also be swapped by the kernel.

Repeated setup skips compilation when the package is installed and the recipe,
override, compiler version and native CPU features match the successful build.
Use `--rebuild-clipboard` to force a rebuild, including after VM CPU changes.
Native packages are local artifacts and are ignored by Git; no compiled package
is shipped in the dotfiles. Source commit and Cargo dependencies remain pinned.

The default build downloads its source and Cargo dependencies. For offline native
builds, pass `--build-inputs DIR`: `DIR/sources/` holds the pinned source archive
(with its makepkg cache filename) and `DIR/cargo/` holds a Cargo cache containing
the registry index, crate archives and sources for this lock file. Setup copies
these into the temporary build tree and runs Cargo offline. Prepare these source
inputs on a connected builder and make the pacman dependencies available in the
ISO repository/cache. The offline inputs contain sources, not compiled binaries.
Alternatively `--clipboard-package FILE` installs a package previously built for
the guest CPU. A fresh machine without cached inputs or network access cannot
compile from the recipe alone.

The upstream Wayland agent is an **archived prototype**, not a released Wayland
feature of Arch's `spice-vdagent` 0.23.0. Its development moved to an upstream SPICE
proposal. This package carries a small patch: it marks its clipboard sources with
a MIME type and recognizes those offers, avoiding echo/ownership races with
clipboard persistence managers. The patch also preserves version reporting when
building from a source archive. Keep this distinction when selecting the final
ISO baseline; replace the package with a tested upstream release when available.

The shared directory follows normal Unix permissions and numeric guest UIDs/GIDs.
Both tested desktop users have UID/GID 1000. Other UID/GID arrangements may require
directory permissions or an explicit virtiofs ID map. A RAM-backed source loses
its contents when the **host** reboots. Sharing `/mnt/zram1` also exposes any VM
disk images stored there; use the folder for ordinary shared files and leave
those active disk images alone.

In the **guest**, check startup and logs with:

```bash
systemctl --user status dusky_vdagent.service
journalctl --user -u dusky_vdagent.service -b
systemctl status spice-vdagentd.socket spice-vdagentd.service
findmnt -T ~/Documents/a_host
```

Clipboard sharing covers supported text and image MIME types. The shared folder
is the method for copying files/directories in both directions. File-manager
clipboard entries are not equivalent to transferring the underlying files.
