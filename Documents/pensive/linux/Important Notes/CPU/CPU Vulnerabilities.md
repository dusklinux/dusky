# CPU vulnerabilities: reading and controlling mitigation state

> [!abstract] What this note teaches
> Understand the running kernel's vulnerability reports, distinguish hardware and microcode from kernel mitigation policy, and choose **documented** boot parameters when conducting a controlled comparison.
>
> Arch Linux x86_64 · reviewed **2026-10-29**. Read [[CPU]] for performance diagnosis, [[CPU mitigations flag for bootloader]] for boot-entry changes, and [[CPU verification record]] for the local evidence.

## 1. Inspect the current report

```bash
grep -H . /sys/devices/system/cpu/vulnerabilities/*
```

The filename identifies the reporting interface and the text describes the running kernel's assessment. List the available interfaces rather than assuming a fixed list:

```bash
ls /sys/devices/system/cpu/vulnerabilities/
```

For the same kernel-exposed information in a summary:

```bash
LC_ALL=C lscpu | grep '^Vulnerability'
```

`LC_ALL=C` makes the English label predictable. These commands inspect status; they do not change it or run an exploit test.

### Interpret the whole line

| Report | Interpretation |
|---|---|
| `Not affected` | The kernel's detection considers the exposed CPU/setup unaffected by this item. |
| `Mitigation: ...` | The named mitigation mechanism is selected; read any trailing qualifications. |
| `Vulnerable` | The report identifies susceptibility without an effective mitigation for the described condition. |
| `Vulnerable: ...` / `Vulnerable; ...` | The rest of the line explains missing or partial protection. |
| A report mentioning missing microcode | The expected microcode capability/revision is unavailable in that setup. |
| `Unknown` or an unfamiliar string | Read that vulnerability's documentation; do not assign a universal meaning. |

A mitigation can be conditional on per-thread controls, SMT state, or another mechanism. A `Mitigation:` prefix is not an assurance that every possible attack path has been eliminated. Similarly, an “unaffected” item is not a statement that the machine has no other CPU issues.

The [kernel hardware-vulnerability index](https://docs.kernel.org/admin-guide/hw-vuln/index.html) links the individual reporting definitions. Treat sysfs as the kernel's **runtime assessment**, not independent proof of exploitability or complete hardware coverage. A VM may expose only the capabilities and state presented by its hypervisor.

## 2. Keep the controlling layers separate

### Hardware and firmware

CPU family, model, stepping, core types, exposed CPUID/MSR capabilities, and firmware configuration influence susceptibility and available mechanisms. A hybrid processor can contain an affected core type alongside a different type.

For example, Intel's [i7-12700H specifications](https://www.intel.com/content/www/us/en/products/sku/132228/intel-core-i712700h-processor-24m-cache-up-to-4-70-ghz/specifications.html) describe 6 P-cores, 8 E-cores, and up to 20 threads. The current Linux instance exposes 14 logical CPUs. Neither the model name nor the generic hardware specification tells you the current mitigation state.

### Microcode

Microcode can provide capabilities used by the kernel and can also change hardware behavior. Inspect the revision actually loaded at boot:

```bash
journalctl -k -b --no-pager --grep=microcode
```

A package being installed is not proof its microcode was included in the booted image or applied. [[CPU#Microcode: installed, included, and loaded are different]] explains that chain.

The local boot log reports current revision `0x0000043b`, updated early from `0x00000429`. This is a dated local observation, not the expected revision for every Intel CPU.

### Kernel build configuration

```bash
zcat /proc/config.gz | grep -E 'CONFIG_CPU_MITIGATIONS|CONFIG_MITIGATION_'
```

Important distinctions:

- `CONFIG_CPU_MITIGATIONS=y` enables the kernel's mitigation framework; individual options and hardware still determine what can be used.
- `# CONFIG_CPU_MITIGATIONS is not set` means that framework was disabled in this build.
- A boot option cannot manufacture kernel code that was excluded at build time.
- Disabled kernel mitigations do not remove every hardware fix, every microcode behavior, or every protection compiled into applications.

If `/proc/config.gz` is absent, use the exact running kernel's build configuration. An installed stock kernel's configuration is not evidence about a running custom kernel.

### Boot policy and per-thread controls

```bash
cat /proc/cmdline
journalctl -k -b --no-pager --grep='mitigat|Unknown kernel command|compiled without'
```

`/proc/cmdline` shows arguments supplied to the kernel, including a compiled-in prefix when configured. Presence alone does not demonstrate that an argument was supported, accepted, or effective.

Some supported mitigations also have per-thread controls such as `prctl`/seccomp-based selection. A global status may therefore describe an available or conditional mechanism rather than identical treatment of all processes. The [Spectre documentation](https://docs.kernel.org/admin-guide/hw-vuln/spectre.html) explains such distinctions for Spectre-related controls.

These layers interact. They are not a simple priority stack in which microcode or a boot string can override missing kernel code.

## 3. What the current custom kernel actually reports

The **running** kernel is `7.3.0-rc7-dusky-battery`. Its configuration says:

```text
# CONFIG_CPU_MITIGATIONS is not set
```

Its boot log explicitly says:

```text
Kernel compiled without mitigations, ignoring 'mitigations'; system may still be vulnerable
```

That directly verifies the `mitigations=` behavior for this build. It does **not** justify claiming that every similarly named parameter is universally ignored by every kernel with a related custom name.

Local sysfs snapshot, 2026-10-29:

| Interface | Exact or abbreviated observed report |
|---|---|
| `reg_file_data_sampling` | `Vulnerable` |
| `spec_store_bypass` | `Vulnerable` |
| `spectre_v1` | `Vulnerable: __user pointer sanitization and usercopy barriers only; no swapgs barriers` |
| `spectre_v2` | `Vulnerable; IBPB: disabled; STIBP: disabled; PBRSB-eIBRS: Vulnerable; BHI: Vulnerable` |
| `vmscape` | `Vulnerable` |

The other 14 exposed files reported `Not affected`. The complete list is recorded in [[CPU verification record]]. Do not reuse this snapshot as a result for another kernel, firmware version, microcode revision, or CPU.

Removing `mitigations=off` from an entry does not enable code missing from the current kernel. A meaningful enabled/disabled comparison needs a kernel built with the relevant mitigation support in both runs. The installed stock kernel was inspected as an available boot entry but was not booted in this review.

## 4. Global policy: mitigations=

The [kernel parameter reference](https://docs.kernel.org/admin-guide/kernel-parameters.html) documents this optional mitigation selector. Its framework requires `CONFIG_CPU_MITIGATIONS=y`.

| Value | Meaning |
|---|---|
| `mitigations=auto` | Use the default mitigation policy, permitting SMT to remain enabled. |
| `mitigations=auto,nosmt` | Also permit disabling SMT where the selected protections require it. |
| `mitigations=off` | Disable the optional CPU mitigations controlled by this policy. |

These are policy requests within the available kernel/hardware implementation. `auto` is not a guarantee against every vulnerability; `off` does not undo hardware redesigns or every microcode change. Disabling these mechanisms changes which isolation boundaries the kernel protects. Measure its performance effect on the actual workload instead of promising a universal speedup or slowdown.

Some vulnerability-specific controls take precedence over the global policy. The documented `kvm.nx_huge_pages=force` exception can also keep that KVM mitigation enabled despite the global `off` choice. Record the **complete** command line and resulting reports for comparisons.

A long hand-maintained expansion of every global equivalent becomes stale as kernel controls evolve. Use the global selector when that is the desired experiment and consult the matching kernel documentation for its current scope.

## 5. Attack-vector controls on current x86 kernels

Current upstream documentation supports selecting classes of mitigation through the same `mitigations=` parameter. These are **documentation-checked**, not reboot-tested on the local machine, whose mitigation framework is disabled.

| Suffix | Class of protection disabled |
|---|---|
| `no_user_kernel` | Userspace → kernel |
| `no_user_user` | Userspace → other userspace |
| `no_guest_host` | Guest → host |
| `no_guest_guest` | Guest → other guest |
| `no_cross_thread` | Across SMT sibling threads |

Syntax example:

```text
mitigations=auto,no_guest_host,no_guest_guest
```

This is a syntax/semantics example, not a recommended preset. An individual mitigation may serve several boundaries, so disabling one vector need not remove that mechanism if another enabled vector still needs it. Vulnerability-specific options can override vector policy. Consult the [attack-vector control specification](https://docs.kernel.org/admin-guide/hw-vuln/attack_vector_controls.html) and verify the implementation in the kernel being booted.

## 6. Reporting filenames are not a parameter namespace

A sysfs reporting name does not imply a matching `name=off` argument. Examples:

| Report interface | Documented disable selector |
|---|---|
| `spectre_v1` | `nospectre_v1` |
| `spectre_v2` | `spectre_v2=off` |
| `spec_store_bypass` | `spec_store_bypass_disable=off` |
| `meltdown` | `pti=off` / `nopti` |
| `reg_file_data_sampling` | `reg_file_data_sampling=off` |
| `vmscape` | `vmscape=off` |

For the remaining x86 entries, the reviewed reference documents these disable forms:

```text
l1tf=off
mds=off
tsx_async_abort=off
mmio_stale_data=off
gather_data_sampling=off
retbleed=off
spec_rstack_overflow=off
srbds=off
indirect_target_selection=off
spectre_bhi=off
spectre_v2_user=off
tsa=off
```

These are a **reference list**, not a block to paste into a boot entry. They were not applied during this review. Defaults and valid enable/force modes differ; consult the individual vulnerability documentation for the target CPU and kernel. Do not replace `off` with `0`, or invent `on`/`force`, unless that parameter documents the value.

In particular, `spectre_v2_user` and `spectre_bhi` are controls that affect related reporting rather than necessarily having their own separate sysfs file. New or architecture-specific reporting entries need their own documentation, not a guessed disable flag.

## 7. Mechanisms worth understanding

### RFDS and the VERW family

RFDS affects particular Intel Atom-derived cores, including affected E-cores in hybrid products. The [RFDS documentation](https://docs.kernel.org/admin-guide/hw-vuln/reg-file-data-sampling.html) lists affected models and explains the combination of microcode support and kernel-executed `VERW` clearing.

Microcode provides the clearing capability; kernel code must invoke it at appropriate boundaries. Therefore “microcode was updated” and “RFDS is mitigated” are different claims.

MDS, TAA, MMIO stale data, and RFDS have related buffer-clearing plumbing. A request to disable one mechanism may leave clearing active because another mitigation still needs it. The parameter reference documents these interactions. A lack of change in a benchmark or status is not automatically a parameter-parser bug.

### Spectre v1 and v2 are different classes

Spectre v1 concerns bounds-check bypass patterns; v2 concerns indirect branch prediction. The report may mention usercopy barriers, swapgs barriers, retpolines, IBRS/eIBRS, IBPB, STIBP, BHI, or return-related protections.

These names describe distinct mechanisms. A report can include an active mechanism and an unmitigated subcase. Read the entire string before summarizing it as “on” or “off.” The [Spectre documentation](https://docs.kernel.org/admin-guide/hw-vuln/spectre.html) describes the mechanisms and userspace controls.

### VMSCAPE is a virtualization boundary issue

The [VMSCAPE reference](https://docs.kernel.org/admin-guide/hw-vuln/vmscape.html) describes a guest influencing branch prediction in host userspace, including userspace VMMs. Its conditional IBPB handling is distinct from treating every guest exit and ordinary process context switch identically.

A `vmscape` report is not proof a VM is currently running. It reports vulnerability/mitigation assessment for that facility. The documented selectors include `off`, `ibpb`, and `force`; their existence does not mean this custom build implements them effectively.

### ITS is not a universal eIBRS failure label

The [ITS reference](https://docs.kernel.org/admin-guide/hw-vuln/indirect-target-selection.html) describes particular affected Intel processors predating Alder Lake. The local Alder Lake machine reports `Not affected`. Do not infer susceptibility solely from the presence of eIBRS or a sysfs filename.

### old_microcode is an age assessment, not an update client

The kernel's [old-microcode report](https://docs.kernel.org/admin-guide/hw-vuln/old_microcode.html) compares boot microcode against its knowledge of released revisions. It may concern functional as well as vulnerability fixes. `Not affected` is not a live query to the newest vendor release and does not prove the installed package repository is current.

## 8. Validate an experiment after boot

A useful observation set is:

```bash
uname -r
cat /proc/cmdline
zcat /proc/config.gz | grep -E 'CONFIG_CPU_MITIGATIONS|CONFIG_MITIGATION_|CONFIG_CMDLINE'
grep -H . /sys/devices/system/cpu/vulnerabilities/*
journalctl -k -b --no-pager --grep='microcode|mitigat|Unknown kernel command|compiled without'
```

If kernel configuration or journal access is unavailable, state that evidence gap. Do not label the experiment fully verified merely because an entry contains the desired text.

Check in this order:

1. Did you boot the intended kernel and entry?
2. Was the relevant framework/individual mitigation compiled in?
3. Does the effective command line contain the intended value, and any duplicate or overriding values?
4. What do the status file and boot messages actually say?
5. Did microcode, SMT state, CPU availability, power policy, or anything else change between runs?
6. Did the real workload change measurably beyond normal variability?

The boot workflow and rollback details live in [[CPU mitigations flag for bootloader]].

## 9. What was corrected in the old notes

- Removed the unverified root-download-and-run checker recipe and its stale CVE-count assertion. The kernel interfaces answer the status questions covered here; no third-party audit was run.
- Replaced “every mitigation parameter is a no-op” with the exact observed `mitigations=` log and build-specific limitations.
- Replaced “sysfs is always the truth” with the narrower kernel-assessment interpretation.
- Removed cross-architecture option lists from the x86_64 learning path.
- Removed unsupported claims that blanket disabling is necessarily faster, necessarily slower, or rarely optimal. Those are workload-dependent questions.
- Corrected the old documentation links: `reg-file-data-sampling.html` and `old_microcode.html` are the reviewed upstream pages.
