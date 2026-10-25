---
title: "Windows search preferences — avoid a universal registry promise"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Windows search preferences — avoid a universal registry promise

[[+ MOC KVM|KVM home]] · [[+ MOC Windows Installation Through Virt Manager|Windows route]]

This is a desktop preference, not required VM setup. Windows edition/build and
policy support determine which search controls are available. Use the supported
Search/Start policy in the installed edition, read its applicability and test
actual Start results after sign-out/restart as directed.

The older recipe set `DisableSearchBoxSuggestions` under the Explorer policy key
and promised instant local-only Start search. Microsoft's documented policy is
about **File Explorer search history/suggestions**. Some builds have applied
additional effects to Start/Bing, but that is not a future-proof contract for
all Windows 11 releases. The old broad speed/result guarantee is removed.

For that documented Explorer preference, inspect the original value first:

```powershell
Get-ItemProperty 'HKCU:\Software\Policies\Microsoft\Windows\Explorer' -Name DisableSearchBoxSuggestions -ErrorAction SilentlyContinue
```

If deliberately configuring that policy, use its Local Group Policy setting or
a managed value according to the linked policy documentation, preserving whether
it was originally absent. Undo by restoring the previous state, not by deleting
an entire Explorer policy key. No search-policy registry changes were applied
to the live VM during this notes review.

Source: [Microsoft Explorer search policy](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-admx-windowsexplorer#disablesearchboxsuggestions).
