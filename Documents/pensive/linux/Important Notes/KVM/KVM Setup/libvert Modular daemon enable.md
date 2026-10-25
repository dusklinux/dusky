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

## Guest lifecycle at host shutdown

`libvirt-guests.service` coordinates configured shutdown/start actions; read
its installed EnvironmentFile path with `systemctl cat` rather than guessing
between `/etc/conf.d/` and `/etc/sysconfig/`. Choose shutdown versus managed-save
according to the actual guest devices and shared-filesystem support. A saved
RAM state is not a disk backup, and nothing preserves host tmpfs through reboot.

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
Next: [[Give the User System-Wide Permission]], then
[[Symbolic link to zram for image file|choose storage and apply its directory ACLs]].
