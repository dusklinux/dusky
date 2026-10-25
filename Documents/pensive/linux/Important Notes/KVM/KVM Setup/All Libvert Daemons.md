---
title: "Libvirt daemon roles"
tags:
  - kvm
  - manual
aliases:
  - Monolithic vs Modular
reviewed: 2026-10-25
---

# Libvirt daemon roles

QEMU management uses virtqemud; networking virtnetworkd; pools virtstoraged; host devices virtnodedevd; filters virtnwfilterd; secrets virtsecretd. virtlogd/virtlockd support running guests, and virtproxyd provides compatibility/remote proxying. Installations need their relevant driver set, not every possible hypervisor daemon.

Continue with [[libvert Modular daemon enable]]. This filename is retained so existing links still work; the complete procedure is maintained in the linked guide.
