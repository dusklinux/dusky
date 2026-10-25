---
title: "Host networking — NAT or wired bridge"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# Host networking — NAT or wired bridge

Use [[Activating Network and Setting it to Autostart|NAT]] for ordinary desktop
VMs and host-to-guest RDP/SSH. A Linux bridge joins a VM to the physical LAN;
it is useful when other LAN devices must reach guest services directly.
Neither choice determines whether the NIC model is VirtIO or e1000e.

| Host/network goal | Choice |
|---|---|
| Ordinary Wi-Fi uplink | Libvirt NAT |
| Wired uplink, guest on physical LAN | NetworkManager bridge and libvirt `host-bridge` |
| Specialized direct/macvtap setup | Separate design; host-to-guest connectivity has limitations |

Macvtap does not magically solve the multiple-MAC limitation of ordinary Wi-Fi
station mode. WDS/4-address support depends on the entire wireless setup; it is
not the baseline. The scripts reject forced ordinary Wi-Fi bridging.

## Wired bridge — plan at the local console

Moving an active uplink changes host networking. Record the existing profile,
addresses, routes and DNS, and prepare the reverse steps before activating it:

```sh
nmcli device status
nmcli -f NAME,UUID,TYPE,DEVICE connection show
ip -4 route
```

For a **DHCP wired uplink**, use these as a template after replacing the two
placeholders with the discovered interface/profile. Static addressing requires
moving the host addresses/gateway/DNS to the bridge rather than using this DHCP
example unchanged.

```sh
vm_uplink=YOUR_WIRED_INTERFACE
vm_old_profile=YOUR_EXISTING_PROFILE
sudo nmcli connection add type bridge ifname br0 con-name br0 \
  ipv4.method auto ipv6.method auto connection.autoconnect yes
sudo nmcli connection add type ethernet ifname "$vm_uplink" \
  con-name vm-br0-port controller br0 port-type bridge connection.autoconnect yes
sudo nmcli connection modify "$vm_old_profile" connection.autoconnect no
sudo nmcli connection down "$vm_old_profile"
sudo nmcli --wait 20 connection up br0
ip -4 route
nmcli device status
```

Verify host connectivity and router DHCP before attaching the VM. If activation
fails, bring down the new bridge, restore autoconnect on the original profile
and bring that profile up; delete only the new profiles you created. Do not
restart NetworkManager or delete an established bridge as a generic repair.
The script's explicit rollback is more elaborate than these manual template
steps because it probes the transition and preserves the previous uplink.

## Advertise an existing bridge to libvirt

Save the following as a local XML file and use `virsh net-define FILE`, then
`net-start host-bridge` and `net-autostart host-bridge` if that network is absent:

```xml
<network>
  <name>host-bridge</name>
  <forward mode='bridge'/>
  <bridge name='br0'/>
</network>
```

Attach the VM with `source network='host-bridge'`. This libvirt definition
**uses** the NetworkManager bridge; it does not create the host uplink or run
its own NAT/DHCP server. The physical LAN normally supplies guest addressing.

References: [NetworkManager nmcli](https://networkmanager.dev/docs/api/latest/nmcli.html),
[libvirt network XML](https://libvirt.org/formatnetwork.html).
