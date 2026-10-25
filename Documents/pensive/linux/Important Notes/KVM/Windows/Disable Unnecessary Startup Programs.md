---
title: "Windows startup apps — optional cleanup"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Windows startup apps — optional cleanup

In Windows Task Manager → **Startup apps**, or Settings → Apps → Startup,
disable user applications you do not want at sign-in. This changes startup
behavior, not the installed application's functionality. Record what changed and
re-enable it there if needed; compare boot/idle behavior rather than assuming
every disabled entry improves a workload.

Startup apps are not the same inventory as services or scheduled tasks. Preserve
components selected for integration: the Looking Glass interactive helper,
QEMU Guest Agent, SPICE agent, VirtIO-FS and optional SSH/balloon services. Do not
apply a blanket service-disable list while editing user startup apps.

See [[Optimize Windows Performance]] and [[Verification — Windows VM]].
