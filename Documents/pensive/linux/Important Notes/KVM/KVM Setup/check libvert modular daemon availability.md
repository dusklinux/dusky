---
title: "Check installed modular libvirt units"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# Check installed modular libvirt units

Inspect the installed package and unit files before enabling services:

```sh
pacman -Q libvirt
pacman -Ql libvirt
systemctl list-unit-files 'virt*.socket' 'virt*.service'
systemctl cat virtqemud.socket
pacman -Qo /usr/lib/systemd/system/virtqemud.socket
```

A masked unit is not proof the package lacks modular daemons: `systemctl cat`
and `systemctl is-enabled` distinguish package content from local masking.
For offline preparation, inspect the supplied package archive/file database;
refreshing repository databases requires network and is not a setup prerequisite.

Continue with [[libvert Modular daemon enable]].
