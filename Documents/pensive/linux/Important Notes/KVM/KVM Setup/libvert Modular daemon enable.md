---
title: "Modular libvirt sockets — manual setup"
tags:
  - kvm
  - manual
aliases:
  - Enabling Modular Libvirt Daemons
reviewed: 2026-10-25
---

# Modular libvirt sockets — manual setup

[[+ MOC KVM|KVM home]]

These instructions target system libvirt on Arch. A socket listens and starts
its daemon on demand. This can reduce idle daemon residency, but it does not
promise zero VM overhead or that every daemon immediately exits after use.
`virtlogd` and `virtlockd` serve running guests and must not be stopped casually.

## 1. Inspect the installed units

```sh
systemctl list-unit-files 'libvirtd*' 'virt*.socket' 'virt*.service'
systemctl cat virtqemud.socket
systemctl cat libvirt-guests.service
```

## 2. Choose the fresh-install or migration route

On a fresh host, continue to socket activation below. A system connection may
not answer yet because its socket has not been started.

On an existing host, first inspect its current inventory as an administrator:

```sh
sudo virsh -c qemu:///system list --all
```

If migrating from the monolithic daemon, gracefully stop guests and back up
their state. Inspect `Requires=`, `Requisite=` and `BindsTo=` relationships, then
stop/disable the installed `libvirtd` service and its sockets before activating
the modular set. Do not mask a dependency still required by `libvirt-guests`,
and do not run both driver models against the same resources. The supplied
phase 10 discovers the unit fleet and checks these dependencies.

## 3. Activate the required sockets

Enable only installed sockets needed by QEMU and its supporting drivers:

```sh
sudo systemctl enable --now virtqemud.socket virtnetworkd.socket virtstoraged.socket \
  virtnodedevd.socket virtnwfilterd.socket virtsecretd.socket virtlogd.socket virtlockd.socket
```

Confirm these units exist in your installed build. `virtproxyd.socket` is useful
for clients needing the compatibility/proxy endpoint; direct modular clients do
not require every alternate-hypervisor daemon. Do not blindly enable nonexistent
`virtvboxd`, LXC or Cloud Hypervisor sockets from an old exhaustive loop.

## Socket permissions and authorization are separate

When socket-activated, socket ownership/mode comes from systemd. Edit the
appropriate socket drop-in, not only `unix_sock_*` in daemon configuration:

```ini
# sudo systemctl edit virtqemud.socket
[Socket]
SocketUser=root
SocketGroup=libvirt
SocketMode=0660
```

Apply equivalent RW-socket policy to the supporting drivers your users manage.
Keep admin sockets root-only unless deliberately choosing another policy.
This manual example uses mode 0660 with libvirt-group socket access. The script
uses mode 0666 for regular sockets, then authorizes API access through polkit
(and its rule accepts libvirt or wheel). Those are deliberate access-policy
choices, not identical settings. In either case, socket access does not replace
API authorization; keep `auth_unix_rw="polkit"` in the applicable daemon config.

```sh
sudo systemctl daemon-reload
# With no clients/guests depending on the sockets being reconfigured:
sudo systemctl restart virtqemud.socket
ls -l /run/libvirt/virtqemud-sock
```

Polkit authorizes API access after the filesystem socket check. See
[[Give the User System-Wide Permission]] for that separate step. This is the
same distinction encoded by `10_virt_modular_daemon.py`, which also configures
`firewall_backend="nftables"` in `/etc/libvirt/network.conf`.

## NAT firewall backend

Before starting libvirt NAT networks, edit `/etc/libvirt/network.conf` with
`sudoedit` and set its existing `firewall_backend` assignment to the following
value, matching phase 10. Do not add a second conflicting assignment:

```ini
firewall_backend = "nftables"
```

This is a daemon setting, not an instruction to enable the standalone
`nftables.service` or replace the host ruleset. If virtnetworkd is already
running, it needs a controlled restart to read a changed config. Make that
transition with affected guests/networks stopped; the fresh-host route sets
this before the first network start.

## Guest lifecycle at host shutdown

`libvirt-guests.service` coordinates configured shutdown/start actions; read
its installed EnvironmentFile path with `systemctl cat` rather than guessing
between `/etc/conf.d/` and `/etc/sysconfig/`. Choose shutdown versus managed-save
according to the actual guest devices and shared-filesystem support. A saved
RAM state is not a disk backup, and nothing preserves host tmpfs through reboot.

For this Windows/VFIO/VirtIO-FS route, use graceful shutdown rather than relying
on managed-save support. The installed Arch unit inspected for these notes
reads `/etc/conf.d/libvirt-guests`; use the path shown by your own unit. Edit
that file with `sudoedit`, preserving unrelated settings and replacing matching
assignments rather than appending duplicates. These file contents match the
script's lifecycle policy:

```conf
URIS='qemu:///system'
ON_BOOT=start
ON_SHUTDOWN=shutdown
SHUTDOWN_TIMEOUT=120
PARALLEL_SHUTDOWN=4
START_DELAY=0
SYNC_TIME=1
```

Choose a shutdown timeout sufficient for your guests. `ON_BOOT=start` can restart
all guests recorded as running at host shutdown, even if their individual
autostart flag is off. Choose `ON_BOOT=ignore` if you do not want that resume
behavior; guest autostart is a separate setting. Ensure encrypted/external/RAM
mounts and needed directory contents are ready before enabling automatic starts.
`SYNC_TIME=1` uses supported guest-agent time sync on resume; it is not required
for display or ordinary Windows timekeeping.

After choosing that policy, arm it for the next host shutdown:

```sh
sudo systemctl enable --now libvirt-guests.service
systemctl status libvirt-guests.service --no-pager
```

On an existing host, starting this service can perform its selected boot action;
inspect its state/recorded guests first. Enabling sockets alone does not arm
this guest-shutdown policy. Reference:
[libvirt-guests lifecycle variables](https://libvirt.org/manpages/libvirt-guests.html).

## Verify

```sh
systemctl is-active virtqemud.socket virtnetworkd.socket
sudo virsh -c qemu:///system version
sudo virsh -c qemu:///system list --all
systemctl status virtqemud.service --no-pager
```

These initial queries use sudo because regular-user access is the next step.
Repeat them without sudo after that access is configured; everyday GUI/VM use
should run as the desktop user. An active service after a query is normal. Source:
[libvirt daemons and systemd activation](https://libvirt.org/daemons.html).

---

Previous: [[Verify VT-x and Kernel Modules and IOMMU]]. Continue: [[Give the User System-Wide Permission]].
