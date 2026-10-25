---
title: "Scheduled tasks — inspect before disabling"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Scheduled tasks — inspect before disabling

[[+ MOC KVM|KVM home]] · [[+ MOC Windows Installation Through Virt Manager|Windows route]]

Keep normal maintenance unless a specific task is demonstrated to interfere
with the workload. `ScheduledDefrag` also participates in storage optimization/
retrim; a host disk setting of `discard=unmap` only permits requests and does
not generate them. Do not disable that task as a generic qcow2/SSD optimization.

Inspect in Task Scheduler or Administrator PowerShell:

```powershell
Get-ScheduledTask -TaskPath '\Microsoft\Windows\Defrag\' | Select-Object TaskPath,TaskName,State
Get-ScheduledTaskInfo -TaskPath '\Microsoft\Windows\Defrag\' -TaskName ScheduledDefrag
```

For a deliberately selected third-party task, record its path/name/settings.
Use `Disable-ScheduledTask -TaskPath PATH -TaskName NAME` only after replacing
those placeholders. Re-enable with `Enable-ScheduledTask` and the same identity.
Rescheduling away from interactive work may be a better fit than disabling it.
Do not disable update, recovery, time or guest integration tasks in bulk.

Source: [Microsoft drive optimization](https://support.microsoft.com/en-us/windows/experience/storage-filemanagement/defragment-optimize-your-data-drives-in-windows).
