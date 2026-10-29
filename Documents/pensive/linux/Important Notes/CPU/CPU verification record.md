# CPU notes: verification record

> [!info] Evidence date and scope
> Reviewed locally on **2026-10-29**, on the running Arch Linux workstation. This record distinguishes executed commands, documented workflows, reproducible failures, and checks that require different hardware or a reboot.
>
> Learning path: [[CPU]] · [[CPU Vulnerabilities]] · [[CPU mitigations flag for bootloader]].

## Tested environment

| Component | Observed value |
|---|---|
| Machine | ASUS TUF Gaming F15 FX507ZE |
| Processor | Intel Core i7-12700H, family 6, model 154, stepping 3 |
| Running kernel | `7.3.0-rc7-dusky-battery` |
| Installed stock kernel package | `linux 7.2.11.arch1-1` |
| Bash | `5.3.20-1` |
| util-linux | `2.42.4-1` |
| systemd | `262.1-1` |
| perf / cpupower | `7.3-1` / `7.3-1` |
| lm_sensors | `1:3.6.2-1` |
| sysstat | `12.8.0-1` |
| stress-ng | `0.22.01-1` |
| mkinitcpio | `42.2-1` |
| glibc | `2.44+r50+g1848099f063e-1` |
| intel-ucode package | `20260925-1` |
| Intel media driver / libva-utils | `26.3.5-1` / `2.24.0-1` |
| intel-gpu-tools | `2.6-1` |
| turbostat | Not installed; Arch `7.3-1` package extracted temporarily for tests |

> [!important] ISO baseline is not established by these observations
> The project plans Linux **7.3 or newer**. The running **7.3 release candidate** and installed stock **7.2.11** do not establish compliance with a final 7.3 floor. The final ISO was not available for a package-manifest or boot test. These notes document the actual development system and leave that release validation explicit.

## CPU and policy observations

Collected with `lscpu`, explicit `cpupower` selections, sysfs, `/proc/config.gz`, service state, and `tlp-stat`:

| Observation | Result |
|---|---|
| Online/present logical CPUs | Both `0-13`, 14 total |
| Exposed cores/socket | 14 / 1 |
| Threads/core in current Linux topology | 1 |
| Kernel CPU maximum | `CONFIG_NR_CPUS=14` |
| SMT status | `active=0`, `control=notsupported` |
| Hybrid PMU masks | `cpu_core/cpus=0-5`, `cpu_atom/cpus=6-13` |
| Frequency driver and mode | `intel_pstate`, `active` |
| Governor | `performance` on all 14 policies at inspection |
| EPP | `performance` on all inspected policies |
| Exposed maximums | P-type policies 4.6–4.7 GHz; E-type policies 3.5 GHz |
| CPUIdle | Driver `intel_idle`, governor `teo`; POLL/C1E/C6/C8/C10 |
| Policy owner | `tlp.service` active/exited; TLP reports enabled |
| tlp-pd | TLP reports `not installed` |
| Platform/power | Performance profile; AC power at inspection |
| Loaded microcode | `0x0000043b`, early update from `0x00000429` |
| glibc-hwcaps | x86-64-v3 and v2 supported/searched; v4 not supported |

[Intel's hardware specification](https://www.intel.com/content/www/us/en/products/sku/132228/intel-core-i712700h-processor-24m-cache-up-to-4-70-ghz/specifications.html) describes 6 P-cores + 8 E-cores and up to 20 threads. The current kernel exposes 14 scheduling targets. The kernel's maximum is verified; firmware SMT configuration was not changed or independently inspected. Do not claim a particular firmware setting caused the topology.

Bare `cpupower frequency-info` selected a currently executing CPU in the observed run, while the installed manual still describes a CPU-zero default. Explicit `-c 0` / `-c all` examples avoid that ambiguity.

## Command and workflow test outcomes

| Check | Outcome | What the result establishes |
|---|---|---|
| `lscpu` summary and explicit extended columns | Passed | Current topology and limits are readable. |
| `cpupower -c all frequency-info` | Passed | All 14 policies inspected. |
| `cpupower -c 0 idle-info` | Passed | Current idle interface readable; counters are cumulative. |
| `sensors` | Passed | CPU/core, fan, and other telemetry present; some fields unavailable. |
| `mpstat -P ALL 1 1` | Passed | Per-CPU interval collection works. |
| `pidstat -u 1 1`, thread/SELF variant | Passed | Per-task interval collection works. |
| `/proc/pressure/cpu` | Readable | PSI interface present; no saturation experiment performed. |
| `lshw -C cpu` under root | Passed | Firmware/inventory view readable. |
| `perf stat -r 3 -e cycles:u,instructions:u` on finite Bash loop | Passed | User-space hardware event counting works on this hybrid CPU. |
| Unprivileged `cycles:k` counting | Rejected | Current permission boundary excludes kernel profiling. |
| Unprivileged system-wide task-clock counting | Rejected | Current permission boundary excludes system-wide collection. |
| User `perf record -e cycles:u -F 99` without callchains | Passed | Flat user-space sampling works. |
| User DWARF recording without `--user-callchains` | Rejected | `:u` alone did not suffice for that callchain configuration. |
| Same DWARF recording with `--user-callchains` | Passed | 42 samples recorded in the tested run. |
| `perf report --stdio` on corrected user capture | Passed | Capture is readable and contains callgraphs. |
| Root DWARF recording and root report | Passed | 26 samples recorded; report exit 0. |
| Root capture reported as ordinary user after read permission granted | Passed | No root-capture format corruption reproduced. |
| Annotate `main`, which had no direct samples | Printed “no samples,” exit 0 | Exit code alone is insufficient validation. |
| Annotate sampled `cfree`, `--stdio --no-source` | Passed | Annotated disassembly produced. |
| Root `perf sched record` | Failed: missing `sched:sched_switch` | Required scheduling tracepoint unavailable. |
| Root `perf trace -- true` | Failed: missing BTF/syscall tracepoints | Required tracing/BPF inputs unavailable in this setup. |
| Low-load `stress-ng`, one worker, 20%, two seconds | Passed | One stressor passed; zero failed. No full-load cooling claim. |
| `vainfo --display wayland` | Passed | Intel iHD VA-API capability enumeration works under Wayland. |
| `vainfo --display drm --device` on discovered Intel node | Passed | DRM VA-API capability enumeration works on that node. |
| Root `intel_gpu_top -L` and finite text telemetry | Passed | Devices/engine counters readable. Playback was not tested. |
| `bootctl status/list` under root | Passed | Selected/current on-disk boot layout inspected. |
| `kernel-install inspect/list` | Passed | Proposed pipeline and installed kernel versions inspected. |
| `lsinitcpio --early` on selected battery initramfs | Passed | `kernel/x86/microcode/GenuineIntel.bin` present. |
| Installed `90-loaderentry.install` in isolated temporary fixture | Passed | Supplied options, kernel copy, relative image paths, and initrd line matched assertions. |

The profiling practice workload was a finite Bash arithmetic loop. Sample counts and elapsed times are **test outcomes**, not statistically meaningful CPU performance scores.

### turbostat validation without installing a package

Downloaded the official Arch `turbostat-7.3-1-x86_64.pkg.tar.zst` and extracted it into a temporary directory. Its version output was `turbostat version 2026.09.28`. Checked its packaged manual and binary help, then exercised under root:

```text
--Summary --quiet --interval 1 --num_iterations 2
--Summary --quiet sleep 1
--list
```

Both sampling modes completed. The two periodic rows reported `Avg_MHz=110/105`, `Busy%=4.50/4.43`, `Bzy_MHz=2440/2378`, and `PkgWatt=13.76/13.32`. They demonstrate the difference between average-over-time and busy frequency. These values were measured during ordinary system activity and are not an idle-power baseline.

Some columns were zero or `nan`; therefore the notes avoid interpreting every displayed domain as a valid calibrated measurement. `/dev/cpu/0/msr` was already available. No module load was needed for this test.

## Tracing limitations observed

```text
# CONFIG_FTRACE is not set
# CONFIG_SCHEDSTATS is not set
```

The following were absent:

```text
/sys/kernel/tracing
/sys/kernel/btf/vmlinux
/proc/schedstat
```

Normal perf recordings emitted `Failed to open /proc/schedstat` but still produced samples and readable reports. Scheduler tracing and syscall tracing failed for their missing interfaces even as root. These facts correct the old claim that root privileges were the only requirement.

## Mitigation snapshot

Running config:

```text
# CONFIG_CPU_MITIGATIONS is not set
CONFIG_CMDLINE_BOOL=y
# CONFIG_CMDLINE_OVERRIDE is not set
```

The compiled-in command line begins with `mitigations=off` and contains other scheduler/power defaults. Runtime logs explicitly reported:

```text
Kernel compiled without mitigations, ignoring 'mitigations'; system may still be vulnerable
```

All 19 exposed status files were read:

| Status | Interface(s) |
|---|---|
| `Vulnerable` | `reg_file_data_sampling`, `spec_store_bypass`, `vmscape` |
| Qualified `Vulnerable` reports | `spectre_v1`, `spectre_v2` |
| `Not affected` | `gather_data_sampling`, `ghostwrite`, `indirect_target_selection`, `itlb_multihit`, `l1tf`, `mds`, `meltdown`, `mmio_stale_data`, `old_microcode`, `retbleed`, `spec_rstack_overflow`, `srbds`, `tsa`, `tsx_async_abort` |

The full v1/v2 strings are preserved in [[CPU Vulnerabilities#3. What the current custom kernel actually reports]]. No exploit checker, mitigation change, or security-hardening change was performed.

## Boot pipeline observations

- Current/default loader entry: `linux-dusky-battery.conf`.
- Loader: systemd-boot 262.1-1-arch.
- Boot filesystem: separate vfat mount at `/boot`; root is Btrfs.
- Selected entry uses flat kernel/initramfs paths, not machine-ID/version directories.
- Stock and stock-fallback entries also exist; both share the stock kernel path.
- `/etc/kernel/cmdline` is absent.
- `kernel-install inspect` proposes BLS output with a token/version-named entry and the installed mkinitcpio/loaderentry plugins.
- The inspected Arch ALPM path invokes the mkinitcpio script/presets. That evidence does not prove it regenerates the selected static entry through kernel-install.
- The running kernel has a compiled-in argument prefix absent from the entry text. Directly copying `/proc/cmdline` would preserve those built-in defaults in another source.

The loaderentry fixture supplied a temporary configuration root, boot root, dummy kernel and initrd. Its assertions checked the exact `options`, `linux`, and `initrd` lines plus the copied kernel's contents. No real boot entry or image was written by that test.

## Documentation and verification limits

Checked local executable paths, package versions, relevant binary help and installed manuals. Inspected directly relevant kernel configuration, sysfs/procfs, boot logs, boot entries, mkinitcpio hooks/presets, and systemd install plugins. Primary online sources are linked at the claims in the three learning notes.

Final review: all **74 Bash code blocks** passed `bash -n` and ShellCheck's error-level checks; all **37 wiki links within these four notes** resolved, including heading targets. The external `Power Management` note was found at its existing location. The five hardware-discovery loops were executed from the extracted examples and each exited successfully. All four files were reviewed against their scoped Git changes; the final diff passed `git diff --check`. These checks validate structure and shell syntax, separately from the runtime outcomes above.

Not exercised:

- Governor, EPP, frequency-limit, profile, or sysctl changes.
- A real boot-entry edit, one-shot selection, kernel-install add, initramfs rebuild, reboot, or mitigation A/B comparison.
- Interactive perf top/report/annotate interfaces.
- Sustained all-core stress, CPU isolation, thermal-throttling causation, or energy-efficiency comparisons.
- Actual application video playback/transcoding offload.
- AMD hardware behavior, UKI rebuilding/signing, or Limine booting.
- The final ISO's installed package set or runtime behavior.

Those procedures are explicitly labeled documented or conditional. Tested command syntax and a successful small fixture cannot establish that every hardware-dependent procedure works on every machine.

Raw investigation files were retained for this session under `/tmp/cpu-notes-review/`; temporary files may disappear on reboot. This note preserves the meaningful outcomes independently of those files. Before editing, the three tracked notes had no scoped staged or unstaged changes. The assistant did not stage, commit, or publish changes. When the review resumed after a user-requested stop, all four notes were already staged; that index state was left untouched.
