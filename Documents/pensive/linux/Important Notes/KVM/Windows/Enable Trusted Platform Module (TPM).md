---
title: "Windows 11 TPM 2.0 — configuration and persistence"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Windows 11 TPM 2.0 — configuration and persistence

The standard Windows 11 route uses an emulated TPM 2.0, supplied by swtpm on the
host. In a powered-off VM: **Add Hardware → TPM → Emulated → Version 2.0**, with
the supported CRB model for this x86 Q35 setup.

Inside `<devices>`:

```xml
<tpm model='tpm-crb'>
  <backend type='emulator' version='2.0'/>
</tpm>
```

The Windows script also enables SMM in its Q35/UEFI feature block. This does not
mean every TPM on every platform requires SMM. Guest UEFI, Secure Boot state and
TPM readiness are separate checks: [[02 Chipset & Firmware — Q35 + UEFI]].
Inside Windows, inspect `tpm.msc` or `Get-Tpm` and `msinfo32`.

TPM state is persistent VM data. Back it up alongside disks and NVRAM before
relocating/reinstalling the host: [[08 Backup and Restore — Disks, Firmware and TPM]].
Replacing the TPM, clearing it or changing firmware can affect encrypted guest
volumes and stored credentials. Keep the corresponding recovery information.
No patched installer, fake Windows 10 OS profile or TPM bypass is needed for
the supported configuration here.

Source: [Microsoft Windows 11 requirements](https://learn.microsoft.com/en-us/windows/whats-new/windows-11-requirements).
