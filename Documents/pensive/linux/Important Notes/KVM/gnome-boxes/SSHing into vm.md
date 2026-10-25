---
title: "SSH into QEMU/KVM Guests (Boxes vs System, Arch)"
tags:
  - kvm
  - ssh
  - gnome-boxes
  - virt-manager
  - arch
  - networking
---

# SSH into QEMU/KVM Guests — Arch (Boxes vs System, Aug 2026)

[[+ MOC KVM|KVM home]] · [[Reference Index — Older Names and Separate Workflows|Separate workflows]]

> [!note] Linux guest review pending
> This page retains older Linux/Boxes or example-XML instructions. It can
> conflict with the current Hyprland/Wayland-only baseline, including older
> clipboard/display recipes. Use [[+ MOC KVM]] for the reviewed
> shared host setup; this page is not yet the current Linux guest guide.

> [!abstract] Detection
> `10.0.2.15` → **user-mode (`slirp`)** → **outbound-only** (host cannot SSH to `10.0.2.15` without port forward). `192.168.122.x` → **libvirt NAT `default`** → **host ↔ guest**. `192.168.1.x` → **bridge `br0`** → LAN-visible. This note gives both **correct** paths for Arch + Hyprland/UWSM.

> [!note] Canonical modernization
> Original recommended `qemu-desktop libvirt … iptables-nft` and traditional `libvirtd.service` with note “modular variant.” Aug 2026 host already modernized via `10_virt_modular_daemon.py` (modular sockets, `nftables`). This note preserves both daemon paths as legacy/alt so old recommendations still make sense, but marks the modern one as preferred.

## Host packages (Aug 2026)

```bash
# minimal system libvirt stack (qemu:///system, NAT)
sudo pacman -S --needed qemu-desktop libvirt virt-manager virt-install edk2-ovmf dnsmasq nftables
# optional desktop/console
sudo pacman -S --needed gnome-boxes virt-viewer spice spice-gtk spice-protocol swtpm
# notes: dnsmasq = DHCP for default NAT; nftables = firewall backend (libvirt network.conf firewall_backend=nftables)
# iproute2/openbsd-netcat are optional netspeed/remote-ssh helpers
```

> `wl-clipboard`/`xclip`/`gvfs-dnssd` not needed for host→guest **SSH**.

## Polkit for Hyprland/UWSM

`virt-manager` on Hyprland without polkit agent → silent auth failures on system connection.

```bash
sudo pacman -S --needed hyprpolkitagent
# ensure it autostarts in your Hyprland/UWSM session
```

## Network cheat sheet

| Mode | Addr | Host→guest SSH? | Use | Caveat |
|---|---|---|---|---|
| **User-mode** (`slirp`) | `10.0.2.15` | **No** | quick disposable | needs `hostfwd` |
| **libvirt NAT** (`default`) | `192.168.122.x` | **Yes** | dev/lab VMs | LAN not inbound without forward |
| **Linux bridge** (`br0`) | `192.168.1.x` | **Yes** | LAN-visible | **Ethernet only**; Wi-Fi bridge fails |
| **macvtap/direct** | LAN | usually no (host) | special | host↔guest broken |

> If goal = `ssh` host→guest, use **libvirt NAT**; not a bridge.

## Guest SSH prereqs

### Arch ISO (live)

```bash
passwd
systemctl is-active --quiet sshd || systemctl start sshd
ip -4 -br addr; ss -ltn | grep ':22'
```

### Installed Arch

```bash
sudo pacman -S --needed openssh
sudo systemctl enable --now sshd
ip -4 -br addr; ss -ltn | grep ':22'
# long-term: normal user + SSH keys, no root password
```

## Preferred — `qemu:///system` + `default` NAT (clean, maintainable)

### 1. `virsh --connect qemu:///session list --all` vs `virsh --connect qemu:///system`

- **system:** `virtqemud` as `root`, host `virbr0`, passthrough/bridges — needs `libvirt` group.
- **session (Boxes):** per-user, `slirp` default, no `virbr0` sharing.

### 2. Enable libvirt (pick matching daemon model on your host)

> [!warning] Don’t blindly run both
> Use the daemon style your `pacman -Q libvirt` actually enables (check `systemctl list-unit-files 'virt*.socket'`).

```bash
# modular (Aug 2026 current, 10_virt_modular_daemon.py) — preferred
sudo systemctl enable --now virtqemud.socket virtnetworkd.socket virtstoraged.socket virtlogd.socket virtproxyd.socket
# or traditional (still shipped but deprecated, masked by pipeline):
sudo systemctl enable --now libvirtd.service
```

> Original note said “if `libvirtd.service` exists it remains simplest” — true pre-2025. On Arch 2026 fresh installs modular sockets are the default; `libvirtd.service` will be **masked** if you ran the pipeline (`10_*` masks it).

### 3. Group

```bash
sudo usermod -aG libvirt "$USER"   # re-login
```

### 4. `default` network

```bash
sudo virsh -c qemu:///system net-list --all
sudo virsh -c qemu:///system net-start default && sudo virsh -c qemu:///system net-autostart default
sudo virsh -c qemu:///system net-define /usr/share/libvirt/networks/default.xml  # if missing
sudo virsh -c qemu:///system net-info default   # → virbr0
```

### 5. VM on `default`

`virt-manager` (system connection) → VM → NIC: `Virtual network 'default' : NAT` → guest gets `192.168.122.x`, host `192.168.122.1` → host can `ssh`.

### 6. Discover + SSH

```bash
ip -4 -br addr   # inside guest
virsh -c qemu:///system net-dhcp-leases default
virsh -c qemu:///system domifaddr <vm> --source lease
ssh user@192.168.122.145
```

## Move Boxes VM into `qemu:///system` (if you already created in Boxes)

Boxes → `qemu:///session`. System → different inventory. Port via `import`.

```bash
virsh -c qemu:///session list --all
virsh -c qemu:///session domblklist --details "<boxes-vm>"
sudo install -d -m 0755 /var/lib/libvirt/images
sudo cp --reflink=auto /path/to/boxes-disk.qcow2 /var/lib/libvirt/images/boxes-import.qcow2
sudo chown root:root /var/lib/libvirt/images/boxes-import.qcow2

virt-install --connect qemu:///system --name boxes-import --memory 4096 --vcpus 4 \
  --disk path=/var/lib/libvirt/images/boxes-import.qcow2,format=qcow2,bus=virtio,driver.cache=none,driver.io=io_uring,driver.discard=unmap \
  --network network=default,model=virtio --graphics spice --video virtio --boot uefi --import
```

## Fallback — Boxes VM `loopback-only` port forward (no rebuild)

Keep `slirp` + `hostfwd` on **session** VM: `127.0.0.1:2222 → guest:22`, then `ssh -p 2222 user@127.0.0.1`.

> [!important] Correct mental model for session VM
> Trying to attach a Boxes `qemu:///session` VM to host `default` NAT is wrong — session doesn’t see system networks. Either **import** to `qemu:///system` or **forward**.

### Steps (virt-manager session, XML editing on)

1. **File → Add Connection → Hypervisor QEMU/KVM → User session**
2. **Edit → Preferences → Enable XML editing**
3. **Shut down VM** (do not edit running NIC)
4. Remove existing user-mode NIC (avoid dual NICs)
5. Ensure `<domain>` has `xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'`
6. Just before `</domain>` add:

```xml
<qemu:commandline xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
  <qemu:arg value='-netdev'/>
  <qemu:arg value='user,id=hostssh,hostfwd=tcp:127.0.0.1:2222-:22'/>
  <qemu:arg value='-device'/>
  <qemu:arg value='virtio-net-pci,netdev=hostssh'/>
</qemu:commandline>
```

- `127.0.0.1:2222` = loopback only (not `::2222` LAN-exposed) — intentional.
- `virtio-net-pci` best for modern Linux guests (else `e1000e`); no forced PCI `addr`.

### Boot + connect

```bash
# guest:
passwd; systemctl is-active --quiet sshd || systemctl start sshd; ss -ltn | grep ':22'
# host:
ssh -p 2222 user@127.0.0.1
```

Stale key after reinstall: `ssh-keygen -R '[127.0.0.1]:2222'`

## Firewall

Don’t `ufw disable` / `iptables -F`. Allow `22/tcp` **inside guest** as needed:

```bash
sudo ufw allow 22/tcp
sudo firewall-cmd --permanent --add-service=ssh && sudo firewall-cmd --reload
# nftables: allow tcp/22 in input chain
```

Arch stock: no firewall enabled — check `sshd` + network mode instead.

## Troubleshoot

| Symptom | Fix |
|---|---|
| `10.0.2.15` | user-mode → needs NAT import or loopback forward |
| Forward fails, `ss -ltn | grep 2222` empty | VM not started / XML invalid / port in use → try `2223` |
| `sshd` down in guest | `systemctl status sshd` → `systemctl start sshd` |
| `localhost` resolves to `::1` | use `127.0.0.1` for `127.0.0.1:2222` forwards |
| `virt-manager` auth fails | no polkit agent / not in `libvirt` / not re-logged |
| `default` missing | `virsh net-list --all`; `net-start`; `net-define /usr/share/libvirt/networks/default.xml` |
| Bridge-on-Wi-Fi broken | use NAT (`default`) |

See: [[gnome-boxes]] (Boxes scope+clipboard), [[+ MOC KVM]] (state not `/tmp`), `20_networking_nmcli.py`.
