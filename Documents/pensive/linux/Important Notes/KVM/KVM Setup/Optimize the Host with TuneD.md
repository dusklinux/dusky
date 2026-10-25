---
title: "Host tuning — optional and measured"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# Host tuning — optional and measured

A desktop VM does not require TuneD. First establish a working baseline, then
measure the workload you want to improve. A profile can change power use,
thermals, scheduling and I/O behavior without improving your application.

Inspect the host's existing power manager first. Avoid multiple managers
simultaneously applying conflicting CPU/power policies. Do not remove a working
laptop setup merely because an old VM checklist says “virtual-host”.

If deliberately testing TuneD with its package installed:

```sh
tuned-adm list
tuned-adm active
# Choose only a profile actually listed, for example:
sudo tuned-adm profile virtual-host
sudo tuned-adm verify
```

Record the original profile/manager and compare workload latency, throughput,
temperature and power before/after. Revert using that recorded state. No universal
zero-overhead or FPS improvement is established by selecting a profile.

This is outside mandatory [[+ MOC KVM|host bring-up]].
Reference: [TuneD upstream](https://tuned-project.org/).
