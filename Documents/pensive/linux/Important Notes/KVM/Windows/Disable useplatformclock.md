---
title: "Windows timers — remove an old forced debugging override"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Windows timers — remove an old forced debugging override

[[+ MOC KVM|KVM home]] · [[+ MOC Windows Installation Through Virt Manager|Windows route]]

`useplatformclock` is a Windows boot debugging option, not a required gaming or
KVM optimization. Do not set it on every fresh VM. Exposing a Hyper-V clock does
not itself require forcing `useplatformclock=No` inside Windows.

If an older guide forced a value, inspect it first in Administrator PowerShell:

```powershell
bcdedit /enum '{current}'
# Only if useplatformclock was explicitly configured and you want OS defaults:
bcdedit /deletevalue '{current}' useplatformclock
```

An absent value is already the default; “element not found” is not a reason to
add it. Record the original state, restart Windows when ready, then compare the
same timing-sensitive workload. Leave unrelated BCD/HPET/dynamic-tick settings
alone unless diagnosing a specific issue.

Guest clock XML is explained by [[Hyper-V Enlightenments]]. A timer change is
not a generic repair for CPU oversubscription, driver faults or a sleeping
Looking Glass display. Source:
[Microsoft BCDEdit options](https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/bcdedit--set).
