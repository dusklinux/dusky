# Mastering CPU Management on Arch Linux

> [!note] Scope
> This note is a permanent reference for **CPU inspection, frequency/power policy, thermal verification, and low-level profiling** on Arch Linux, updated for **March 2026**.
>
> It focuses on:
> - **x86_64 Arch Linux** systems
> - modern CPU frequency scaling drivers (`intel_pstate`, `amd_pstate`, `acpi-cpufreq`)
> - **`perf`**, **`cpupower`**, **`turbostat`**, and related diagnostics
> - **Intel iGPU media offload** as an adjacent topic, because successful video offload materially reduces CPU utilization
>
> Related notes: [[Power Management]], [[CPU Vulnerabilities]]

---


some commands to run to find out about your cpu 

```bash
/lib/ld-linux-x86-64.so.2 --help
```

```bash
sudo lshw -C cpu
```

```bash
lscpu
```
## Package Reference

### Core diagnostic packages

```bash
sudo pacman -S --needed perf cpupower lm_sensors sysstat stress-ng
```

### x86-specific low-level tools

```bash
sudo pacman -S --needed linux-tools
```

### Intel iGPU / VA-API offload tools

```bash
sudo pacman -S --needed intel-gpu-tools libva-utils intel-media-driver
```

### Legacy Intel VA-API driver, only when needed

```bash
sudo pacman -S --needed libva-intel-driver
```

### What each package provides

| Package | Key commands | Purpose |
|---|---|---|
| `perf` | `perf` | PMU-based profiling, event counting, tracing |
| `cpupower` | `cpupower` | Inspect/set CPU frequency policy and idle information |
| `linux-tools` | `turbostat`, `x86_energy_perf_policy` | x86 power/frequency telemetry, Intel-oriented tuning |
| `lm_sensors` | `sensors`, `sensors-detect` | Temperature, voltage, fan telemetry |
| `sysstat` | `mpstat`, `pidstat`, `sar` | Per-CPU and per-process utilization |
| `stress-ng` | `stress-ng` | Controlled synthetic load generation |
| `intel-gpu-tools` | `intel_gpu_top` | Verify iGPU/video engine activity |
| `libva-utils` | `vainfo` | Confirm VA-API driver and codec support |
| `intel-media-driver` | VA-API `iHD` driver | Preferred Intel media driver for modern Intel graphics |
| `libva-intel-driver` | VA-API `i965` driver | Legacy Intel VA-API driver, mainly for older or compatibility cases |

> [!tip] Package split changes
> Kernel-adjacent tools occasionally move between Arch packages. If a command is missing, locate its owning package:
>
> ```bash
> pacman -F turbostat
> pacman -F x86_energy_perf_policy
> ```

---

## Quick Inspection Checklist

Use this sequence before changing anything:

| Goal | Command | Notes |
|---|---|---|
| Identify CPU, topology, caches, SMT, vulnerabilities | `lscpu` | Fastest high-level summary |
| Show detailed topology | `lscpu -e=cpu,node,socket,core,maxmhz,minmhz` | Good for hybrid or NUMA systems |
| Show active scaling driver and governors | `cpupower frequency-info` | Detect `intel_pstate`, `amd_pstate`, or `acpi-cpufreq` |
| Show idle states | `cpupower idle-info` | Useful for C-state troubleshooting |
| Show temperatures/fans | `sensors` | Requires `lm_sensors` |
| Show per-CPU usage live | `mpstat -P ALL 1` | From `sysstat` |
| Show per-process CPU usage live | `pidstat -u 1` | From `sysstat` |
| Verify turbo, C-state residency, package power | `sudo turbostat --Summary --quiet sleep 5` | Best x86 power/frequency snapshot |
| Check loaded microcode | `journalctl -k -b | grep -i microcode` | Confirms early microcode load |
| See whether video decode is offloading from CPU | `vainfo` / `intel_gpu_top` | Intel graphics only |

---

## CPU Topology and Baseline Identification

### High-level CPU summary

```bash
lscpu
```

`lscpu` is the preferred summary tool on Arch because it reads from `sysfs` and kernel-exposed topology data. It is usually more useful than reading `/proc/cpuinfo` directly.

Key fields to inspect:

- `Architecture`
- `CPU(s)`
- `Thread(s) per core`
- `Core(s) per socket`
- `Socket(s)`
- `Vendor ID`
- `Model name`
- `CPU max MHz` / `CPU min MHz`
- `NUMA node(s)`
- `Vulnerability ...` lines

### Extended topology view

```bash
lscpu -e=cpu,node,socket,core,maxmhz,minmhz
```

This is especially useful on:

- **hybrid Intel CPUs** with P-cores and E-cores
- multi-socket systems
- NUMA machines
- systems where advertised boost clocks do not match observed per-core behavior

> [!note] Hybrid CPUs
> On Intel hybrid architectures, not every core type has the same maximum frequency. Do **not** compare all-core frequency under load to a single-core turbo specification.

---

## CPU Frequency Scaling and Power Policy

Modern Linux does **not** manage CPU clocks the same way on every system. The active driver matters.

### Common scaling drivers

| Driver | Typical systems | Notes |
|---|---|---|
| `intel_pstate` | Most modern Intel systems | Often operates in hardware-managed mode; governor semantics differ from legacy cpufreq |
| `amd_pstate` | Modern AMD Zen systems with CPPC support | Preferred on supported systems; active/passive/guided behavior depends on kernel and firmware |
| `acpi-cpufreq` | Older x86 systems or fallback cases | Legacy generic cpufreq driver |

### Inspect the active driver and current governor

```bash
cpupower frequency-info
```

Direct sysfs inspection:

```bash
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_driver
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors
```

If present, inspect driver-specific status:

```bash
[[ -r /sys/devices/system/cpu/intel_pstate/status ]] && cat /sys/devices/system/cpu/intel_pstate/status
[[ -r /sys/devices/system/cpu/amd_pstate/status   ]] && cat /sys/devices/system/cpu/amd_pstate/status
```

### Governor semantics on modern systems

#### `intel_pstate`
On most current Intel laptops/desktops, `intel_pstate` is the active driver.

Important nuance:

- `performance` does **not** mean “maximum fixed frequency all the time”
- `powersave` does **not** mean “minimum frequency only”

With modern Intel hardware, especially with HWP/Speed Shift, these names describe **policy preference**, not a simplistic fixed clock.

#### `amd_pstate`
On supported AMD systems, `amd_pstate` uses CPPC-aware policy control. Behavior depends on whether the driver is in `active`, `guided`, or `passive` mode, but the key point is similar: modern policy names are abstractions, not direct MHz locks.

#### `acpi-cpufreq`
On legacy cpufreq systems, governors such as `schedutil`, `performance`, `powersave`, `ondemand`, or `conservative` behave more like the traditional Linux model.

### Temporarily change governor

```bash
sudo cpupower frequency-set -g performance
sudo cpupower frequency-set -g powersave
```

If `schedutil` is available:

```bash
sudo cpupower frequency-set -g schedutil
```

> [!tip] Reasonable default
> On systems using the generic cpufreq stack, **`schedutil`** is usually the best default for normal use. On systems using `intel_pstate` or `amd_pstate` in active mode, available governors may be limited and behave differently.

### Idle-state information

```bash
cpupower idle-info
```

This reports C-state support and is useful when debugging:

- unexpectedly high idle power
- poor battery life
- sleep/thermal anomalies
- systems that never reach deep package C-states

---

## Avoid Conflicting Policy Managers

> [!warning] Run one CPU power-policy authority at a time
> Do **not** let multiple tools fight over governors, EPP, or frequency limits.

Common conflicting components:

- `cpupower.service`
- `power-profiles-daemon`
- `TLP`
- `tuned`
- `auto-cpufreq`

Check what is currently active:

```bash
systemctl --type=service --state=running | grep -E 'cpupower|power-profiles|tlp|tuned|auto-cpufreq'
```

### Why this matters

If one tool sets `performance` and another sets `balanced`/`powersave`, your observed behavior will be inconsistent and difficult to diagnose.

This is especially common on laptops and graphical environments where:

- a desktop utility calls `powerprofilesctl`
- a status bar widget toggles profiles
- `TLP` applies laptop-specific rules after boot
- `cpupower.service` restores a static governor

---

## Thermals, Power, and Idle-State Verification

## `lm_sensors`

### Read temperatures and fan data

```bash
sensors
```

Live refresh:

```bash
watch -n 1 sensors
```

### Optional hardware probing

If `sensors` does not show expected CPU telemetry, you can probe for additional sensor modules:

```bash
sudo sensors-detect --auto
```

> [!note] Usually optional
> On most modern Arch systems, CPU temperature sensors are detected automatically. Run `sensors-detect` only if needed.

---

## `turbostat`

`turbostat` is one of the most useful x86 telemetry tools for:

- effective frequency
- turbo behavior
- package temperature
- package power
- idle-state residency
- per-core activity

### Basic summary

```bash
sudo turbostat --Summary --quiet sleep 5
```

### If `turbostat` complains about missing MSR access

```bash
sudo modprobe msr
```

> [!warning] x86-oriented tool
> `turbostat` is primarily an x86 utility and is most feature-rich on Intel systems. It can still be useful on many AMD systems, but available fields vary by CPU and firmware support.

### Interpreting common fields

| Field | Meaning |
|---|---|
| `Avg_MHz` / `Bzy_MHz` | Effective operating frequency while busy |
| `Busy%` | Fraction of time the CPU spent busy |
| `PkgTmp` | Package temperature |
| `PkgWatt` | Estimated package power draw |
| `Pkg%pcN` | Package C-state residency |

### Typical use cases

- Verify whether turbo is actually engaging
- Confirm whether sustained frequency drops are thermal or power-limit related
- Check whether the system reaches deep package C-states at idle

---

## `sysstat`: Real-Time CPU Utilization

### Per-CPU view

```bash
mpstat -P ALL 1
```

Useful for spotting:

- single-thread bottlenecks
- imbalanced scheduling
- interrupt-heavy workloads
- parked/underutilized cores

### Per-process CPU view

```bash
pidstat -u 1
```

Useful when “the system is busy” but you need to identify **which process** is consuming CPU.

---

## Microcode and Mitigation State

CPU management is incomplete without microcode awareness.

### Install the correct microcode package

#### Intel

```bash
sudo pacman -S --needed intel-ucode
```

#### AMD

```bash
sudo pacman -S --needed amd-ucode
```

> [!warning] Boot integration still matters
> Installing the package is only part of the job. Your boot flow must actually load the microcode image. Exact steps depend on whether you use GRUB, systemd-boot, a unified kernel image, or another boot method.

### Verify microcode at boot

```bash
journalctl -k -b | grep -i microcode
```

### Check mitigation state exposed by the running kernel

```bash
lscpu | grep -i '^Vulnerability'
```

> [!note] Performance impact
> Security mitigations can materially affect performance, especially for syscall-heavy, virtualization, and I/O-heavy workloads. Record mitigation state when benchmarking.

---

## Intel iGPU Media Offload and CPU Load Reduction

> [!note] Adjacent topic, not a CPU feature
> Hardware video decode/encode uses the GPU media engine, not the CPU. It belongs in this note because correct offload can dramatically lower CPU usage and power draw during playback or transcoding.

### Driver selection

#### Preferred modern driver

```bash
sudo pacman -S --needed intel-media-driver
```

- VA-API driver name: `iHD`
- Best default choice for **Broadwell/Gen8 and newer** Intel graphics in most cases

#### Legacy/compatibility driver

```bash
sudo pacman -S --needed libva-intel-driver
```

- VA-API driver name: `i965`
- Mainly relevant for:
  - older Intel graphics generations
  - compatibility testing
  - occasional Broadwell/Skylake-era application quirks

> [!tip] Simpler troubleshooting
> The two drivers can coexist, but troubleshooting is easier if you know which one you intend to use. Do not assume both are required.

### Verify VA-API capability

```bash
vainfo
```

On headless or DRM-only setups:

```bash
vainfo --display drm --device /dev/dri/renderD128
```

### Force a specific VA-API driver for testing

#### Modern Intel media driver

```bash
LIBVA_DRIVER_NAME=iHD vainfo
```

#### Legacy Intel driver

```bash
LIBVA_DRIVER_NAME=i965 vainfo
```

### Monitor actual GPU/video engine activity

```bash
intel_gpu_top
```

If access fails outside a local graphical login, try from an active seat session or with elevated privileges:

```bash
sudo intel_gpu_top
```

### What success looks like

During video playback or hardware transcoding:

- CPU usage drops relative to software decode/encode
- `intel_gpu_top` shows activity on render/video engines
- `vainfo` lists the codec profile actually needed by the application

> [!warning] `vainfo` is necessary but not sufficient
> A valid `vainfo` result only proves the driver stack is available. The **application itself** must also be configured to use VA-API.

---

## Low-Level Profiling with `perf`

`perf` is the canonical Linux interface to:

- hardware PMUs
- software counters
- tracepoints
- sampling profilers
- scheduler analysis
- syscall tracing

It is the correct tool when CPU usage is high but the cause is not obvious.

### Permissions and security model

Check current restrictions:

```bash
sysctl kernel.perf_event_paranoid
```

If unprivileged profiling is blocked, you may need:

- `sudo`
- a lower `kernel.perf_event_paranoid` value
- `CAP_PERFMON` on trusted systems

A reasonable workstation compromise is often:

```bash
printf '%s\n' 'kernel.perf_event_paranoid = 1' | sudo tee /etc/sysctl.d/90-perf.conf
sudo sysctl --system
```

> [!warning] Security tradeoff
> Lowering `perf_event_paranoid` increases observability and can expose more information to local users. On multi-user or hardened systems, keep restrictions tighter.

### Symbol resolution on Arch

For better user-space symbols in `perf report` and `perf top`:

```bash
export DEBUGINFOD_URLS="https://debuginfod.archlinux.org"
```

> [!tip] When symbols show as `[unknown]`
> Ensure:
> - the binary was not stripped beyond usefulness
> - debuginfod is configured
> - call-graph unwinding mode is appropriate
> - you have permission to inspect the target process

---

## `perf list`

Show the events available on the current system:

```bash
perf list
```

Useful when you need to confirm which counters exist on:

- bare metal vs VM
- Intel vs AMD
- laptop vs server
- specific kernel versions

---

## `perf stat`

`perf stat` gives a compact statistical summary of how a command interacts with the CPU.

### Basic usage

```bash
perf stat -- <your_command>
```

Example:

```bash
perf stat -- ls -R /
```

### Better baseline with repeated runs

```bash
perf stat -r 5 -- <your_command>
```

### Request more derived metrics

```bash
perf stat -d -- <your_command>
```

### Track explicit events

```bash
perf stat -e cycles,instructions,branches,branch-misses,cache-misses -- <your_command>
```

### System-wide counters over an interval

```bash
sudo perf stat -a sleep 10
```

### How to read it

Important relationships:

- **Instructions**: total retired instructions
- **Cycles**: total CPU cycles
- **IPC** = instructions / cycles
- **Cache misses**: memory hierarchy pressure
- **Branch misses**: bad branch prediction behavior

> [!note] Event multiplexing
> If you request too many counters at once, the kernel may multiplex them. `perf stat` reports scaling information, but precision decreases.

---

## `perf top`

`perf top` is a real-time sampling profiler.

### System-wide live view

```bash
sudo perf top
```

### Focus on one process

```bash
sudo perf top -p <PID>
```

### Hide kernel symbols

```bash
sudo perf top -K
```

### Hide user-space symbols

```bash
sudo perf top -U
```

Typical use cases:

- identify the hottest functions in a running workload
- distinguish user-space vs kernel hotspots
- quickly confirm whether CPU time is spent in crypto, decompression, rendering, syscalls, or page faults

---

## `perf record`, `perf report`, and `perf annotate`

Use this workflow for detailed offline analysis.

### Record samples

Basic call-graph capture:

```bash
perf record -g -- <your_command>
```

This writes `perf.data` in the current directory.

### More robust user-space unwinding

If stacks are incomplete, use DWARF unwinding:

```bash
perf record --call-graph dwarf,16384 -- <your_command>
```

> [!note] Tradeoff
> `dwarf` unwinding is more reliable for some binaries but has noticeably higher overhead than frame-pointer-based unwinding.

### Analyze the capture

Interactive TUI:

```bash
perf report
```

Non-interactive output:

```bash
perf report --stdio
```

### Inspect assembly annotated with samples

```bash
perf annotate
```

### Practical interpretation workflow

1. Start with `perf stat`
2. If a bottleneck exists, switch to `perf top`
3. If you need call stacks and post-mortem analysis, use `perf record` + `perf report`
4. Use `perf annotate` only after you know which symbol is actually hot

---

## `perf trace`

`perf trace` is a modern tracing interface built on `perf` infrastructure. It overlaps conceptually with `strace`, but integrates with perf events and tracepoints.

### Trace a command

```bash
perf trace -- <your_command>
```

### Trace an existing process

```bash
sudo perf trace -p <PID>
```

Useful for:

- syscall-heavy applications
- wakeup/latency investigations
- filesystem-heavy workloads
- distinguishing CPU-bound behavior from kernel/I/O overhead

> [!note] Root is not always required
> Tracing your own processes may work unprivileged, depending on `kernel.perf_event_paranoid` and system policy. For arbitrary processes, root or equivalent capability is the simplest path.

---

## Optional Advanced `perf` Workflows

### Scheduler latency and run-queue timing

```bash
sudo perf sched timehist
```

Useful when:

- the CPU is not fully utilized
- the workload still feels laggy
- you suspect scheduler delay or run-queue contention

### Pin a benchmark to specific CPUs

```bash
taskset -c 0-3 perf stat -r 5 -- <your_command>
```

This reduces noise when comparing runs.

---

## Controlled Load Generation

Use synthetic load only to validate cooling, boost behavior, or policy response—not as a substitute for real workload profiling.

### Stress all logical CPUs for 60 seconds

```bash
stress-ng --cpu 0 --timeout 60s --metrics-brief
```

### Stress a subset of CPUs

```bash
taskset -c 0-3 stress-ng --cpu 4 --timeout 60s --metrics-brief
```

### Combine with telemetry

In a second terminal:

```bash
watch -n 1 sensors
```

Or:

```bash
sudo turbostat --Summary --quiet sleep 60
```

> [!warning] Watch thermals
> If temperature rises rapidly and frequency drops below expected sustained clocks, the system is likely **thermally** or **power** limited rather than scheduler limited.

---

## Reproducible Benchmarking Checklist

Before comparing CPU results:

- Use the **same power source** each run
- Keep the **same governor/policy**
- Record **microcode** and **mitigation state**
- Stop background package upgrades and heavy browser tabs
- Warm caches if comparing hot-path performance
- Pin CPUs with `taskset` if necessary
- Do not compare a **single-core turbo claim** to **all-core sustained** frequency

Recommended capture set:

```bash
lscpu
cpupower frequency-info
cpupower idle-info
sensors
journalctl -k -b | grep -i microcode
sudo turbostat --Summary --quiet sleep 5
```

---

## Troubleshooting

### `perf_event_open ... Operation not permitted`

Cause:

- restricted `kernel.perf_event_paranoid`
- insufficient privilege/capability
- container or VM policy

Check:

```bash
sysctl kernel.perf_event_paranoid
```

Try with:

```bash
sudo perf stat -- <your_command>
```

---

### `turbostat` cannot access MSRs

Cause:

- `msr` module not loaded

Fix:

```bash
sudo modprobe msr
sudo turbostat --Summary --quiet sleep 5
```

---

### `cpupower frequency-info` shows limited or unexpected governor choices

Cause:

- you are on `intel_pstate` or `amd_pstate`, where governor names and behavior differ from legacy cpufreq

Action:

- check the active driver first
- do not assume `schedutil` will exist on every system
- do not interpret `powersave` on `intel_pstate` as “locked to minimum clock”

---

### CPU does not reach advertised boost clocks

Common reasons:

- thermal limits
- firmware power limits
- battery mode / platform profile
- mixed workload across many cores
- conflicting power managers
- virtualization
- turbo disabled in firmware

Check:

```bash
cpupower frequency-info
sensors
sudo turbostat --Summary --quiet sleep 5
```

Also verify firmware settings such as:

- Intel Turbo Boost / Intel Speed Shift
- AMD Core Performance Boost
- CPPC
- SMT

---

### Idle power is too high

Common reasons:

- a process keeps waking the CPU
- deep C-states are not reached
- GPU/media/USB devices prevent package idle
- a daemon forces a performance-oriented policy

Check:

```bash
cpupower idle-info
sudo turbostat --Summary --quiet sleep 10
mpstat -P ALL 1
```

Look for poor `Pkg%pcN` residency and background activity.

---

### Video playback uses too much CPU on Intel graphics

Check:

```bash
vainfo
intel_gpu_top
```

If necessary, test drivers explicitly:

```bash
LIBVA_DRIVER_NAME=iHD vainfo
LIBVA_DRIVER_NAME=i965 vainfo
```

If `vainfo` succeeds but CPU usage stays high, the application probably is **not actually using** VA-API.

---

### `perf` data from a VM or container looks incomplete

Cause:

- PMU virtualization restrictions
- missing host passthrough
- security policy

Rule of thumb:

- use **bare metal** for trustworthy hardware-counter analysis
- treat VM/container `perf` data as limited unless you know the PMU is fully exposed

---

## Recommended Minimal Workflow

### When the system feels slow

```bash
lscpu
cpupower frequency-info
sensors
mpstat -P ALL 1
sudo turbostat --Summary --quiet sleep 5
```

### When one command is slow

```bash
perf stat -r 5 -- <your_command>
perf top
perf record -g -- <your_command>
perf report
```

### When media playback is unexpectedly CPU-heavy on Intel

```bash
vainfo
intel_gpu_top
```

---

## Bottom Line

For Arch Linux CPU work, the most useful core tools are:

- **`lscpu`** for topology and kernel-exposed CPU state
- **`cpupower`** for frequency/idle policy inspection
- **`sensors`** for thermals
- **`turbostat`** for x86 power/frequency/C-state truth
- **`perf`** for serious profiling
- **`vainfo` + `intel_gpu_top`** when verifying Intel media offload that should reduce CPU usage

> [!tip] Practical rule
> Diagnose in this order:
>
> 1. **Topology / driver / governor**
> 2. **Thermals / power / idle state**
> 3. **Per-process CPU usage**
> 4. **Low-level profiling with `perf`**
> 5. **Media offload verification when applicable**
