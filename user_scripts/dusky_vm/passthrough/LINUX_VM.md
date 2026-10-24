# Arch Linux VM workflow

Run `python3 000_dusky_vm.py --linux` from this directory for host preparation
and deployment. See [profile commands and resume behavior](PROFILES.md).
`python3 30_kvm_vm_deploy.py` remains the direct deployment entry point once the
host is prepared. The host code requires Python 3.15+. The Windows profile
selects the existing `30_kvm_vm_deploy.py --windows` workflow.

The Linux launcher:

- Asks for an ISO. Enter discovers case-insensitive `dusky*.iso` files directly
  inside `/mnt/zram1`, independent of the date in their names. Multiple matches
  are presented newest modification time first; unattended mode requires an
  explicit ISO if there are multiple matches.
- Offers persistent storage at `/var/lib/libvirt/images` as the Enter default,
  RAM storage at `/mnt/zram1`, and a custom directory.
- Creates a new sparse **20 GiB qcow2** disk and refuses to overwrite existing
  disk/assets files. RAM storage needs more than 24 GiB currently available,
  plus enough memory for the entire disk, guest RAM and 4 GiB host headroom.
  For a 4 GiB guest that means at least 28 GiB available and 20 GiB free on tmpfs.
- Creates a KVM/Q35/UEFI guest. Firmware is selected by libvirt, not a hardcoded
  OVMF filename. The empty disk falls back to the ISO; after installation the
  disk has boot priority. This launcher does not install the guest OS itself.
- Uses host CPU passthrough, VirtIO networking/storage, discard and io_uring.
  Persistent storage uses cache=none; RAM storage uses writeback to avoid
  bypassing the RAM filesystem's page cache. These are tested settings, not
  a claim of optimal benchmark performance for every workload.
- Uses local SPICE with a UNIX connection (`listen=none`) and VirtIO 2D display
  by default. The test guest intermittently hung during shutdown with VirGL;
  two shutdown/restart cycles with 2D completed cleanly, including one from
  the logged-in desktop. Accelerated rendering
  remains available with `--graphics 3d --rendernode PATH`; `--graphics auto`
  selects an available Intel/AMD render device. 2D can cost desktop rendering
  performance; neither setting is universally fastest or most reliable.
- Opens the console in virt-manager without waiting for its GUI to close.
- Configures SSH through the QEMU guest agent and verifies a real SSH login.
  A per-VM key lives in `STORAGE/.NAME/ssh-key`; guest host keys are obtained
  through the guest agent. No existing host SSH configuration is replaced.
- Exports `/mnt/zram1` at `/media/host` through VirtioFS by default, independently
  of where the VM disk lives. Thus `/mnt/zram1/example` on the host is
  `/media/host/example` in the guest. Enter accepts that default; `--share-dir
  PATH` selects a different host directory, including persistent storage if
  deliberately selected. The default must be RAM-backed: an absent/unmounted
  zram1 directory never silently becomes a persistent share. Existing export
  directories keep their ownership, permissions and ACLs.
  The guest mount deliberately avoids `/mnt`, which the Arch installer uses for
  its target. Files created by either side remain on the host's shared filesystem;
  copying them elsewhere inside the guest writes to the VM disk.
- Disables guest caching on the shared filesystem and retains inode descriptors
  instead of reopening exportfs file handles. Stress tests exposed stale handles
  when the host rapidly replaced files being read by the guest. A small per-VM
  launcher invokes the installed daemon with `--inode-file-handles=never`; the
  daemon's installed path is discovered from its package. The shell immediately
  replaces itself with the daemon. These settings affect the share only, not the
  VM disk's caching policy.

The host must already have the packages staged by `05_virtio_iso.py
--skip-virtio`, functioning modular libvirt sockets, and a libvirt network.
The default is the existing `default` NAT network; `--network NAME` selects
another existing bridge-backed libvirt network. This launcher starts/enables
that network but does not replace it or change the host uplink.

## Installation and reboot

Use the ISO's installer normally. The supplied Dusky ISO starts its own installer
automatically. The live guest includes qemu-guest-agent; other media must provide
that package and start its service for automatic provisioning.

In an Arch live session, a live-only systemd service waits for an installation
root mounted at `/mnt`. It stages `dusky-vm-integration.service` and a Python
setup script into the installed system. It also copies the ISO's QEMU agent,
liburing and numactl package archives when available. On installed boot, that
service installs the agent offline if needed, enables SSH and sets up the shared
folder. It selects the first regular guest user, falling back to root in the live
environment; `--guest-user NAME` overrides that account.

The handoff applies to Arch installations using `/mnt`. If using a different
installer layout, install/start qemu-guest-agent in the installed guest and run:

```sh
python3 30_kvm_vm_deploy.py --configure dusky-linux
```

After installation, this command cleanly shuts down the guest, ejects the ISO,
boots the disk, verifies SSH and starts clipboard integration:

```sh
python3 30_kvm_vm_deploy.py --boot-installed dusky-linux
```

Run it only after the installer has finished. A guest stopped in firmware menus
may need to be shut down in virt-manager first. No forced shutdown is performed.

Open an SSH session without copying key paths or IP addresses:

```sh
python3 30_kvm_vm_deploy.py --ssh dusky-linux
```

## Wayland clipboard

Native Wayland **text/code** sharing uses `wl-clipboard` and SSH. It supports
Unicode and multiple lines. Files use `/media/host`; image/file clipboard formats
are not synchronized. Copies are event-driven, and duplicate values are
suppressed. Marked sensitive selections are not forwarded.

Watcher callbacks carry small change notifications; the bridge samples the
current text when consuming them. Host revisions reject delayed guest replies
to older selections, including echoes between multiple connected VMs. When
copies conflict, a newer host selection takes priority. The bridge uses the
regular clipboard watched by Dusky's existing clipboard manager; its history
and persistence preferences remain under that manager's control.

The host now manages a persistent systemd user service. Deployment and
`--configure` enable it automatically. You do **not** launch a script inside the
guest: the host sends its current endpoint code over SSH. Both ends need
`wl-clipboard` and a Wayland compositor supporting data-control (tested with
Hyprland). The console-only live environment has no graphical clipboard.

The service waits for the VM and desktop, including a delayed guest login. It
reconnects after SSH loss, endpoint crashes or desktop/guest restarts. DHCP
addresses are rediscovered through libvirt, including VMs attached directly to
`virbr0`. Automated guests use QGA to refresh the installed account and SSH host
key on a new boot. Existing manual VMs can use SSH alone.

Enable sharing for an existing VM from the **host** session, once:

```sh
python3 vm_clipboard.py enable DOMAIN --ssh USER@HOST
```

The first setup uses the normal SSH password prompt if needed. Passwords are
not saved. Per-VM SSH keys/configuration live on the host under
`$XDG_CONFIG_HOME/dusky-vm/clipboard/UUID` (normally `~/.config/...`), surviving
host restarts even when the deployment assets are on tmpfs. Services start with
the host user manager. If a VM has been deleted, its service stops cleanly.

Inspect or control an enabled VM:

```sh
python3 vm_clipboard.py status DOMAIN
python3 vm_clipboard.py restart DOMAIN
python3 vm_clipboard.py disable DOMAIN
```

`status` distinguishes an active supervisor from actual readiness:
`Clipboard ready: host ↔ guest (text)`. It also reports waiting for a VM, waiting
for a desktop or reconnecting after an error. Logs are under `$XDG_RUNTIME_DIR`
(RAM), with the exact path printed by `status`. Enabled guests all share the host
text clipboard while connected; disable a VM's service when that is unwanted.
On reconnect, old guest clipboard contents do not overwrite the host selection.

For a VM created by this workflow, the original shortcut still works:

```sh
python3 30_kvm_vm_deploy.py --clipboard dusky-linux
```

It now enables/restarts the supervised service instead of launching a detached
process that disappears after connection loss. Its nonblocking per-VM instance
guard only prevents duplicate bridges, never locks shared files, and releases
automatically. Clipboard sharing does not depend on an X11 SPICE agent.

## Testing and temporary Python setup

Example using RAM storage and a specified ISO:

```sh
python3 30_kvm_vm_deploy.py --iso /mnt/zram1/dusky_10_26.iso \
  --storage /mnt/zram1 --name dusky-linux-test --yes
```

For the current machine only, append `--tool-python /usr/bin/python3` when
launching virt-install/virt-manager built against the distro Python. This generic
interpreter override does not change the deployment interpreter or pin a Python
version. Omit it after the distro packages move to Python 3.15.

`--dump-only --yes` generates XML without creating disks, keys, shares, pools,
domains or networks. virt-install's temporary cache is confined to shared RAM
and removed. For real VMs, assets/NVRAM/viewer logs follow the selected storage;
libvirt still stores its normal small domain registration and service logs.
RAM-backed VM disks and firmware variables disappear when tmpfs is cleared.

Run focused regressions with all scratch files on RAM storage:

```sh
TMPDIR=/mnt/zram1 python3 -B -m unittest discover -s tests -v
```

Stress-test the running guest's RAM-backed share, using automatically cleaned
scratch files inside the export:

```sh
python3 -B tests/stress_virtiofs.py dusky-linux-test
python3 -B tests/stress_virtiofs.py dusky-linux-test \
  --large-mib 512 --workers 8 --files-per-worker 200 --atomic-updates 1000
```

The suite checks hashes, concurrent writers, atomic replacement, sparse files,
links, permissions, renames and deletion in both directions. It does not acquire
advisory file locks. The workflow adds no locks to shared files; applications
retain their normal filesystem behavior. The installed VirtioFS backend does not
coordinate host/guest advisory locks. QEMU's own VM-disk locking remains enabled
to prevent conflicting writers from corrupting the disk image.

Interfaces: [libvirt VirtioFS](https://www.libvirt.org/kbase/virtiofs.html),
[QEMU guest agent](https://www.qemu.org/docs/master/interop/qemu-ga-ref.html),
[QEMU VirtIO GPU](https://www.qemu.org/docs/master/system/devices/virtio/virtio-gpu.html).
