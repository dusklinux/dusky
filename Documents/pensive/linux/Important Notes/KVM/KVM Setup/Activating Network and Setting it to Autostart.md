---
title: "Libvirt NAT network — provision, start and verify"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# Libvirt NAT network — provision, start and verify

NAT gives guests outbound connectivity and normally lets the **host** contact
them directly. It does not automatically expose guest services to other LAN
machines. The guest firewall and listening service still matter.

```sh
virsh -c qemu:///system net-list --all
virsh -c qemu:///system net-info default
virsh -c qemu:///system net-dumpxml default
```

If `default` exists, preserve its DHCP/subnet configuration. Start it only if
inactive and enable autostart:

```sh
virsh -c qemu:///system net-autostart default
# If currently inactive:
virsh -c qemu:///system net-start default
```

Do not destroy/undefine an existing network to repair an ordinary startup issue.

## Only if the network is absent

Check `ip -4 route` for collisions first. This example uses 192.168.122.0/24;
choose another non-overlapping subnet if already routed elsewhere. Run in Bash:

```bash
vm_net_xml=$(mktemp --suffix=.xml)
cat > "$vm_net_xml" <<'XML'
<network>
  <name>default</name>
  <forward mode='nat'/>
  <bridge name='virbr0' stp='on' delay='0'/>
  <ip address='192.168.122.1' netmask='255.255.255.0'>
    <dhcp><range start='192.168.122.2' end='192.168.122.254'/></dhcp>
  </ip>
</network>
XML
virsh -c qemu:///system net-define "$vm_net_xml"
rm -- "$vm_net_xml"
virsh -c qemu:///system net-autostart default
virsh -c qemu:///system net-start default
```

The network daemon uses dnsmasq and the configured libvirt firewall backend.
`firewall_backend="nftables"` in `/etc/libvirt/network.conf` selects this setup's
backend. An nftables executable alone does not prove that backend is active.
Do not flush the host ruleset or disable its firewall to diagnose guest routing.
Inspect the actual network, DHCP lease and filtering rules first.

## Attach by libvirt network name

```xml
<interface type='network'>
  <source network='default'/>
  <model type='virtio'/>
</interface>
```

Use `e1000e` for the optional Windows inbox-driver route. `virbr0` is the host
bridge made by the NAT network; referencing it directly as an unmanaged bridge
bypasses the named network relationship. That caused a first-start dependency
failure in the live test and was corrected to `source network='default'`.

```sh
ip -4 addr show virbr0
virsh -c qemu:///system net-dhcp-leases default
virsh -c qemu:///system domifaddr win11 --source lease
# Once QGA is ready:
virsh -c qemu:///system domifaddr win11 --source agent
```

Missing lease output does not prove the guest has no IP; static addressing and
other network modes need guest/agent discovery. LAN-visible alternative:
[[Host Networking — NAT and Wired Bridge]]. Source:
[libvirt network XML](https://libvirt.org/formatnetwork.html).
