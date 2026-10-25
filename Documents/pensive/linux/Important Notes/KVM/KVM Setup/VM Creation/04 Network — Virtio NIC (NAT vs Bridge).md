---
title: "Guest NIC — device model versus network topology"
tags:
  - kvm
  - manual
aliases:
  - Network Unified
reviewed: 2026-10-25
---

# Guest NIC — device model versus network topology

[[+ MOC KVM|KVM home]] · [[00 Index — VM Creation (Unified)|Virtual hardware]]

Choose topology and NIC model separately. [[Activating Network and Setting it to Autostart|Libvirt NAT]] works on ordinary Wi-Fi and wired hosts. Use
[[Host Networking — NAT and Wired Bridge|a wired bridge]] only when its LAN
visibility is needed. Do not use macvtap as an ordinary Wi-Fi bridging fix.

VirtIO is the efficient device option when the guest driver is available.
Windows also offers e1000e with its inbox driver; the deployment asks explicitly.
Emulation is a useful setup choice, not proof a configuration is invalid.

```xml
<interface type='network'>
  <source network='default'/>
  <model type='virtio'/>
</interface>
```

For the named wired bridge network, use `source network='host-bridge'` instead.
The `network` value is a libvirt network name, not a host interface name.

Windows needs [[Configure Virtual Network Interface|NetKVM]] for VirtIO.
Typical Linux kernels include virtio_net; interface names and DHCP addresses
still vary. After installing or changing drivers, inspect the guest NIC and its
address instead of assuming `192.168.122.x`, `ens3` or `enp1s0` everywhere.

```sh
virsh -c qemu:///system domiflist win11
virsh -c qemu:///system net-dhcp-leases default
virsh -c qemu:///system domifaddr win11 --source agent
```

Agent discovery requires QGA. Guest service/firewall settings determine whether
RDP/SSH works after IP connectivity exists. Host-to-guest NAT access and
LAN-to-guest access are different paths; diagnose the one you are using.

---

Previous: [[03 Storage — Virtio Bus, Cache, io_uring, Discard]]. Continue: [[05 CPU — Host-Passthrough & Topology]].
