# CPU: inspection, power policy, and profiling on Arch Linux

> [!abstract] Start here
> Diagnose in this order: **what Linux can use → whether work is waiting or running → frequency and thermal limits → the functions consuming CPU time**. Change one setting only after identifying the question it answers.
>
> Scope: Arch Linux x86_64, Wayland/Hyprland, modern Intel and AMD CPUs. Local commands checked on **2026-10-29**. Hardware-specific observations are separated into [[CPU verification record]]. Related: [[CPU Vulnerabilities]] · [[CPU mitigations flag for bootloader]] · [[Power Management]].

> [!tip] Shell used in these examples
> Run multiline shell examples in **Bash** (`bash` starts it from Zsh). They target the Bash 5.3 baseline. Zsh treats unmatched filename patterns differently, so a hardware-conditional glob can fail before the loop runs there.

## Reading map

1. [[#First inspection: a small useful command set]]
2. [[#CPU concepts that prevent misdiagnosis]]
3. [[#Packages and local documentation]]
4. [[#Topology: what Linux can actually schedule]]
5. [[#Frequency policy: driver, governor, EPP, and limits]]
6. [[#Idle states, temperatures, and power]]
7. [[#Utilization, waiting, and CPU pressure]]
8. [[#Microcode: installed, included, and loaded are different]]
9. [[#Profiling with perf]]
10. [[#Controlled load and reproducible comparisons]]
11. [[#Intel video offload: an adjacent CPU diagnosis]]
12. [[#Troubleshooting by symptom]]

## First inspection: a small useful command set

These commands inspect the system without changing CPU policy:

```bash
uname -r
lscpu
lscpu -e=CPU,ONLINE,SOCKET,CORE,MAXMHZ,MINMHZ
cpupower -c all frequency-info
cpupower -c 0 idle-info
sensors
mpstat -P ALL 1 3
pidstat -u 1 3
cat /proc/pressure/cpu
```

`1 3` means one sample per second, three reports. Omit the count for a continuous display; press **Ctrl+C** to stop. `cpupower -c 0` deliberately selects logical CPU 0; select another existing CPU when investigating a different policy or core type.

Once `turbostat` is installed:

```bash
sudo turbostat --Summary --quiet --interval 1 --num_iterations 3
```

Use the output to choose the next investigation:

| Observation | Next question |
|---|---|
| One CPU busy while others are idle | Is the application limited by one thread, affinity, or a serialized stage? |
| All usable CPUs busy and CPU pressure rising | Is there more runnable work than available CPU capacity? |
| Application slow but CPUs mostly idle | Is it waiting for I/O, locks, timers, another process, or GPU work? |
| Busy frequency falls during sustained work | Are temperature, package power, policy, or firmware limits involved? |
| CPU frequency looks high at low utilization | Does it spend most of the interval asleep? Check `Busy%` and idle residency. |
| High CPU use during video playback | Is the application actually decoding through the GPU media engine? |

A single snapshot does not establish a bottleneck. Observe the same interval as the slow workload.

## CPU concepts that prevent misdiagnosis

### Package, core, logical CPU, and thread

- **Package/socket:** the processor package; it can contain many cores, caches, and other units.
- **Physical core:** an execution core. SMT siblings share core resources.
- **Logical CPU:** a scheduling target identified by a Linux CPU number.
- **Software thread:** an application's schedulable task. Many threads may take turns on one logical CPU.
- **SMT:** multiple hardware threads on one physical core. Two SMT siblings are not two independent physical cores.
- **Hybrid CPU:** different core types in one processor. The same frequency does not imply the same throughput on each type.

An application can have hundreds of software threads without hundreds of them running simultaneously. Hardware specifications describe possible configurations; the current kernel, firmware, CPU online state, affinity, and cgroups determine what an application can use.

### Frequency, utilization, throughput, and latency

| Quantity | What it answers | Important limitation |
|---|---|---|
| Frequency | How quickly does the clock advance while the CPU runs? | GHz does not measure useful work per cycle. |
| Utilization | How much of an interval was CPU time occupied? | A busy CPU can be stalled on memory or doing avoidable work. |
| Throughput | How much useful work finishes per second? | Define the work and keep the input comparable. |
| Latency | How long does one operation take? | An average can hide intermittent delays. |
| Power | How fast is energy being consumed? | Package watts are not necessarily whole-machine watts. |
| Energy | How much energy did the task consume? | Lower watts can still use more energy if execution takes longer. |

Useful relationships:

```text
CPU utilization for a workload ≈ accumulated CPU time / elapsed time
energy = average power × elapsed time
IPC = retired instructions / counted cycles
```

Each needs matching scope and interval. A multithreaded process can accumulate several seconds of CPU time in one second of elapsed time.

### P-states and C-states

**P-state/performance management** concerns performance while executing. **C-states/idle management** concern what happens when there is no work to run. A CPU can request an aggressive performance policy, execute briefly at high frequency, then spend most of its time in an idle state.

This is why a frequency governor named `performance` does not by itself prove that the processor is awake continuously. Conversely, a low requested frequency does not prove low total energy consumption. See the kernel's [CPU performance scaling](https://docs.kernel.org/admin-guide/pm/cpufreq.html) and [CPU idle management](https://docs.kernel.org/admin-guide/pm/cpuidle.html) documentation.

## Packages and local documentation

### Identify installed commands before installing anything

```bash
command -v lscpu cpupower sensors mpstat pidstat perf turbostat stress-ng
pacman -Q util-linux cpupower perf lm_sensors sysstat stress-ng
pacman -Q turbostat
```

An absent optional package produces an error; that does not mean the other queries failed. For an installed executable, identify its package:

```bash
pacman -Qo /usr/bin/cpupower /usr/bin/perf
```

Core optional diagnostics:

```bash
sudo pacman -S --needed cpupower perf turbostat lm_sensors sysstat stress-ng
```

This assumes a consistently updated Arch system. During a deliberate system update use `pacman -Syu --needed ...` instead; avoid refreshing only the sync databases and then selectively upgrading packages. For an offline ISO, include the packages in the image or provide the matching package set locally.

| Package | Commands | Why use it? |
|---|---|---|
| `util-linux` | `lscpu`, `taskset`, `findmnt` | CPU inventory, affinity, mount discovery |
| `cpupower` | `cpupower` | CPU frequency policy and CPU idle information |
| `lm_sensors` | `sensors` | Kernel-exposed temperature and fan readings |
| `sysstat` | `mpstat`, `pidstat` | Per-CPU and per-task interval statistics |
| `turbostat` | `turbostat` | x86 interval frequency, power, temperature, and idle telemetry |
| `perf` | `perf` | Event counting, statistical profiling, optional tracing |
| `stress-ng` | `stress-ng` | Controlled synthetic workload |
| `lshw` | `lshw` | Optional firmware/hardware inventory |

Arch packages these Linux tools individually. `linux-tools` is a package group; `linux-tools-meta` pulls in the broader set. Check current package membership with `pacman -Sg linux-tools` rather than assuming every tool is required. The current [turbostat package](https://archlinux.org/packages/extra/x86_64/turbostat/) belongs to that group.

### Use documentation that matches the installed binary

```bash
lscpu --help
cpupower help frequency-info
man cpupower-frequency-set
man turbostat
man perf-stat
man perf-record
man perf-report
man stress-ng
```

`perf version --build-options` reports compiled features, including DWARF unwinding and debuginfod. A supported command-line option cannot supply a feature missing from the running kernel.

Package and kernel versions are independent: `perf` 7.3 being installed does not mean the running kernel is final Linux 7.3. The local verification record lists the versions actually tested; the final ISO package manifest still needs a separate check.

## Topology: what Linux can actually schedule

### Summary and explicit columns

```bash
lscpu
lscpu -e=CPU,ONLINE,NODE,SOCKET,CORE,MAXMHZ,MINMHZ
lscpu --list-columns
cat /sys/devices/system/cpu/online
cat /sys/devices/system/cpu/present
```

`CPU` is a logical CPU ID. `CORE` is topology information, not a command-line CPU selector. Use socket/core pairs when counting physical cores across sockets. `NODE` can be unavailable (`-`) when the kernel does not expose NUMA topology.

`MAXMHZ` and `MINMHZ` describe exposed frequency limits, not measured sustained clocks. A difference in `MAXMHZ` is a useful clue on hybrid hardware, but is not a universal core-type detector. Do not infer P/E topology solely from frequency.

For an individual CPU's SMT relationship:

```bash
cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list
cat /sys/devices/system/cpu/smt/active
cat /sys/devices/system/cpu/smt/control
```

A singleton sibling list means Linux currently exposes no sibling for that CPU. `notsupported` in the current kernel's SMT control file does not establish that the processor model lacks SMT hardware.

On Intel hybrid hardware, PMU CPU masks provide another useful view when these PMUs exist:

```bash
for pmu in /sys/bus/event_source/devices/cpu_core /sys/bus/event_source/devices/cpu_atom; do
    if [[ -r "$pmu/cpus" ]]; then
        printf '%s: ' "${pmu##*/}"
        cat "$pmu/cpus"
    fi
done
```

These paths are conditional because other CPUs expose different PMUs. The verification machine exposes `cpu_core` CPUs 0–5 and `cpu_atom` CPUs 6–13; **discover the mapping again on another machine**.

### Kernel limits and process limits

If fewer CPUs are visible than expected, inspect the running kernel and boot arguments before blaming a scheduler:

```bash
zcat /proc/config.gz | grep -E '^CONFIG_NR_CPUS=|^CONFIG_SMP='
cat /proc/cmdline
taskset -pc "$$"
grep -E '^Cpus_allowed_list:|^Mems_allowed_list:' /proc/self/status
```

`CONFIG_NR_CPUS` is a build-time maximum, not the physical core count. Firmware SMT settings, boot-time CPU restrictions, CPU hotplug, cpusets, and affinity can impose additional limits. A cgroup CPU quota can limit CPU time even when affinity allows many CPUs.

If `/proc/config.gz` is absent, the kernel may lack `CONFIG_IKCONFIG_PROC`; use the matching kernel build's configuration. Do not substitute the configuration of another installed kernel.

### Instruction-set baseline

On Arch x86_64 with glibc:

```bash
/usr/lib/ld-linux-x86-64.so.2 --help
```

Read the `glibc-hwcaps` section. `x86-64-v3 (supported, searched)` reports loader-supported capability; it does not mean every installed program is optimized for v3 or that a different repository is automatically faster. This is an instruction-set compatibility check, separate from frequency and core count.

Optional firmware inventory:

```bash
sudo lshw -C cpu
```

Treat firmware capacity/clock fields as inventory information. Use interval measurements for actual behavior.

## Frequency policy: driver, governor, EPP, and limits

### Inspect every policy

```bash
cpupower -c all frequency-info
```

The explicit CPU selection matters. In the installed `cpupower` 7.3, bare `frequency-info` sampled the CPU on which it ran; the installed man page still describes a CPU-zero default. Use explicit `-c` to avoid depending on that discrepancy.

A CPUFreq **policy** represents CPUs whose performance controls are coordinated. There may be one policy per logical CPU or a shared policy. Inspect policies directly:

```bash
for policy in /sys/devices/system/cpu/cpufreq/policy*; do
    [[ -d "$policy" ]] || continue
    printf '\n%s\n' "${policy##*/}"
    for attr in affected_cpus related_cpus scaling_driver scaling_governor \
        scaling_available_governors scaling_min_freq scaling_max_freq \
        cpuinfo_min_freq cpuinfo_max_freq energy_performance_preference \
        energy_performance_available_preferences; do
        if [[ -r "$policy/$attr" ]]; then
            printf '  %s: ' "$attr"
            cat "$policy/$attr"
        fi
    done
done
```

| Attribute | Interpretation |
|---|---|
| `scaling_driver` | Driver actually registered for the policy |
| `scaling_governor` | Selected governor or driver-specific policy algorithm |
| `scaling_available_governors` | Valid governor choices for this policy |
| `scaling_min_freq`, `scaling_max_freq` | Policy limits in kHz; not a promise of sustained performance |
| `cpuinfo_min_freq`, `cpuinfo_max_freq` | Driver-exposed hardware frequency range, in kHz |
| `energy_performance_preference` | Current energy/performance hint, if supported |
| `energy_performance_available_preferences` | Accepted named hints for this driver/hardware |

`scaling_cur_freq`, where present, is driver-dependent and can represent a requested or sampled frequency. Prefer `turbostat` interval measurements when asking what happened during a workload. A cpupower “Unable to call hardware” line can coexist with a useful kernel-reported frequency; it is not proof that frequency scaling is broken.

### Driver modes change the meaning of governor names

| Exposed driver | Mode | Governor interpretation |
|---|---|---|
| `intel_pstate` | Intel active mode | Driver algorithms named `performance` and `powersave`; HWP may select performance in hardware |
| `intel_cpufreq` | Intel passive mode | Generic CPUFreq governors, subject to the installed kernel's available list |
| `amd-pstate-epp` | AMD active mode | CPPC autonomous selection using performance/energy hints |
| `amd-pstate` | AMD passive or guided mode | Different CPPC control model; inspect AMD status and available governors |
| `acpi-cpufreq` | ACPI P-state driver | Generic CPUFreq governors |

Conditional mode inspection:

```bash
for mode in /sys/devices/system/cpu/intel_pstate/status /sys/devices/system/cpu/amd_pstate/status; do
    if [[ -r "$mode" ]]; then
        printf '%s: ' "$mode"
        cat "$mode"
    fi
done
```

With active Intel HWP, `performance` sets EPP/EPB to the performance value and requests the upper allowed performance level; other EPP values are rejected in this configuration. That request does not prevent idle states or guarantee an actual sustained clock under hardware limits. Active HWP `powersave` permits autonomous selection and configurable EPP within policy limits. Generic `powersave` instead requests the lowest allowed frequency. Read the [Intel driver documentation](https://docs.kernel.org/admin-guide/pm/intel_pstate.html) for mode-specific semantics.

AMD active/passive/guided modes are documented in the [AMD P-State reference](https://docs.kernel.org/admin-guide/pm/amd-pstate.html). AMD hardware was not available for local testing. Do not assume Intel EPP names, driver strings, or writable controls apply unchanged to AMD.

### EPP, EPB, boost, and platform profile

- **EPP** is an energy/performance preference used by hardware-managed performance selection. It is a hint, not a fixed MHz setting or a watt limit.
- **EPB** is Intel's energy-performance bias interface. It is distinct from EPP; `x86_energy_perf_policy` is an optional specialist tool, not a mandatory tuning step.
- **Boost/turbo** permits performance above a non-turbo level under current hardware constraints. “Enabled” means permitted, not continuously achieved.
- **Platform profile** is a firmware/platform preference, potentially affecting fan and power behavior in addition to CPU policy.

Inspect existing interfaces:

```bash
for attr in /sys/devices/system/cpu/intel_pstate/no_turbo \
    /sys/devices/system/cpu/cpufreq/boost \
    /sys/firmware/acpi/platform_profile \
    /sys/firmware/acpi/platform_profile_choices; do
    if [[ -r "$attr" ]]; then
        printf '%s: ' "$attr"
        cat "$attr"
    fi
done
```

`intel_pstate/no_turbo=1` means turbo is disallowed by that control. Generic `boost=1` means enabled; **the polarity differs**. The existence and scope of controls vary. Firmware, power supply, temperature, workload, and shared package limits still constrain actual performance.

### Identify the policy owner before making changes

```bash
systemctl list-units --type=service --all --no-pager \
    'tlp*' 'cpupower*' 'power-profiles*' 'tuned*' 'auto-cpufreq*'
```

A boot-time policy service can be **active (exited)**. Searching only `--state=running` misses such services. On the verification machine, `tlp.service` is active/exited and `tlp-pd` is not installed.

When TLP is installed:

```bash
sudo tlp-stat -s -p
```

This reads current TLP status and processor policy. Inspect the settings belonging to the selected policy manager before persisting a manual change. AC/battery events or profile switches may overwrite manual sysfs/cpupower changes. Multiple managers writing the same knobs can make results inconsistent; do not disable services indiscriminately.

### Temporary governor changes

These are **documented write operations**, not part of the read-only checklist:

```bash
sudo cpupower -c all frequency-set -g performance
```

Use only a governor shown in `scaling_available_governors`. To restore, apply the recorded previous governor(s), or let the configured manager reapply its profile. Do not assume `powersave` was the previous state on every policy.

`frequency-set -d` changes the minimum; `-u` changes the maximum. Both accept explicit units such as `MHz` or `GHz`. `-f` requires the `userspace` governor, so it is not a general way to fix frequency under active `intel_pstate`.

There is no universal fastest governor or ideal EPP for every workload. Measure elapsed time, sustained behavior, and energy if energy matters. No policy writes were performed during this notes review.

## Idle states, temperatures, and power

### CPUIdle: distinguish software requests from package residency

```bash
cpupower -c 0 idle-info
```

The CPUIdle **driver** supplies idle states; the CPUIdle **governor** chooses among them. This is separate from the CPUFreq driver/governor. On the tested machine these are `intel_idle` and `teo`.

In `idle-info`, latency is the advertised exit latency, residency is the target residency, usage is an entry counter, and duration is accumulated time. The installed tool's latency/residency/duration values use microseconds. They are not percentages of your last one-second interval.

Deep software idle-state usage does not prove the whole package reached the corresponding physical state. Other cores, graphics, devices, interrupts, firmware, and constraints can keep the package awake. Hardware residency counters in `turbostat` answer a different, complementary question. See [CPU idle management](https://docs.kernel.org/admin-guide/pm/cpuidle.html).

### Temperatures and fans

```bash
sensors
watch -n 1 sensors
```

Read labels: NVMe, battery, memory, GPU, and CPU package temperatures are different measurements. Firmware labels and threshold quality vary. A reading marked `N/A` is unavailable telemetry, not a temperature of zero.

Modern CPU sensor drivers commonly bind automatically. `sensors-detect --auto` is **not** a routine prerequisite: its installed manual describes active hardware probing. If CPU temperatures already appear, it adds no value to this workflow. Investigate the relevant driver if a required sensor is missing.

A high temperature plus low frequency suggests a possible limit; it does not isolate the cause. Look for correlated throttle indicators and power behavior during the same interval. There is no single correct temperature threshold for all CPUs.

### turbostat: use interval data correctly

Small finite sample:

```bash
sudo turbostat --Summary --quiet --interval 1 --num_iterations 3
```

One measurement covering a command:

```bash
sudo turbostat --Summary --quiet sleep 5
```

The `sleep` command defines the observation interval. The measurements cover the **system**, not just `sleep`. Omit `--Summary` for individual CPU rows. In command mode the child runs with the privileges of the invocation; do not casually replace `sleep` with a user application under `sudo`.

List the actual available columns:

```bash
sudo turbostat --list
```

| Field | Correct reading |
|---|---|
| `Avg_MHz` | Cycles divided by elapsed time, **including idle time** |
| `Bzy_MHz` | Average clock rate while not idle |
| `Busy%` | Fraction of the interval in C0/busy execution |
| `TSC_MHz` | TSC rate; not a direct CPU core clock reading |
| `CoreTmp`, `PkgTmp` | Available core/package thermal readings |
| `CPU%c*`, `Pkg%pc*`, `Pk%pc10` | Hardware residency; names and availability vary |
| `C1E%`, `C6%`, `C10%` | CPUIdle software-state accounting when provided |
| `PkgWatt`, `CorWatt`, `GFXWatt` | Average power for the named hardware energy domain |
| `CoreThr`, `PKG_%` | Throttling-related counters; read the manual for the exact field semantics |

Approximately, for a compatible measurement scope: `Avg_MHz ≈ Bzy_MHz × Busy% / 100`. Example: 4,000 MHz while busy at 5% duty cycle gives about 200 average MHz. The old notes incorrectly treated both MHz columns as busy frequency.

Power domains can overlap; do not add every watt column together. Missing, zero, or `nan` fields can indicate unavailable/unsupported telemetry. The local test produced real package readings alongside `nan` graphics residency and zero-valued domains; none is a calibrated whole-laptop wall-power measurement.

Both finite modes above were exercised using an extracted Arch `turbostat` 7.3-1 package. That package is not installed on the machine. See [[CPU verification record]] for exact results and scope.

If a specific error reports missing MSR access, inspect the device/module situation and, when appropriate, load the kernel's matching module:

```bash
sudo modprobe msr
```

Do not treat that as a cure for every failure. Current `turbostat` uses both MSR and perf interfaces. Its documented `--no-msr` and `--no-perf` options disable respective backends and can reduce available telemetry; use them only to isolate a demonstrated backend problem. The normal commands worked without those options here.

## Utilization, waiting, and CPU pressure

### mpstat: which CPUs are occupied?

```bash
mpstat -P ALL 1 5
```

| Column | Meaning |
|---|---|
| `%usr` | User-mode execution |
| `%sys` | Kernel execution, excluding separately accounted interrupt time |
| `%irq`, `%soft` | Hardware/software interrupt handling |
| `%iowait` | CPU idle time while outstanding disk I/O exists; not CPU execution |
| `%steal` | Time a virtual CPU waited while the hypervisor serviced another virtual CPU |
| `%idle` | Idle time without the outstanding-I/O condition |

High iowait is a clue to investigate I/O, not proof that a particular disk or process is responsible. Whole-machine averages can hide a saturated single CPU.

### pidstat: which process or thread is using time?

```bash
pidstat -u 1 5
```

For a known PID, replace `1234` with its actual PID:

```bash
pidstat -u -t -p 1234 1 5
```

`-t` includes threads. By default, a process using two logical CPUs fully can show about **200% CPU**. Add `-I` to normalize usage by the system's processor count. Compare percentages only when their normalization is the same.

A `%wait` column depends on scheduler accounting. The running custom kernel has `CONFIG_SCHEDSTATS` disabled, so do not interpret missing or zero scheduler-delay data as proof that tasks never wait.

### CPU pressure and load average

```bash
cat /proc/pressure/cpu
cat /proc/loadavg
```

CPU PSI `some` quantifies time when at least some work is stalled waiting for CPU. `avg10`, `avg60`, and `avg300` are percentages over the corresponding averaging windows; `total` is cumulative microseconds. System-wide CPU `full` is undefined and reported as zero for compatibility, so use `some` here. This does not mean CPU pressure is zero. See the [PSI interface documentation](https://docs.kernel.org/accounting/psi.html).

Linux load average includes runnable tasks and tasks in uninterruptible sleep, commonly I/O-related. It is neither a CPU-use percentage nor a universal “load divided by core count” performance score. CPU pressure, per-CPU activity, and the application's latency together give a more useful picture.

## Microcode: installed, included, and loaded are different

Choose the package for the CPU vendor:

```bash
sudo pacman -S --needed intel-ucode
```

For AMD hardware use `amd-ucode` instead. An ISO intended for different machines may need both packages; an image built with host autodetection can still include only the build host's microcode. Check the image-building policy.

With `mkinitcpio`, inspect the active configuration and hook help:

```bash
grep '^HOOKS=' /etc/mkinitcpio.conf
mkinitcpio -H microcode
```

The `microcode` hook includes early microcode. If `autodetect` runs before it, inclusion is restricted to the build system's processor. Preset-specific configurations and drop-ins can change the effective configuration; inspect those when rebuilding.

For an identified initramfs, list its early archive. This local example uses the current battery-kernel image:

```bash
sudo lsinitcpio --early /boot/initramfs-linux-dusky-battery.img
```

On another installation, take the image path from its actual boot entry or preset. Look for the relevant `kernel/x86/microcode/` file. This is evidence of image contents, not evidence that this image was booted.

Confirm runtime loading:

```bash
journalctl -k -b --no-pager --grep=microcode
```

Use `sudo journalctl ...` if journal access is restricted. “Updated early from …” demonstrates an early revision change. A current revision without that line does not automatically mean early loading failed: firmware may already have supplied the same revision. Package version, microcode revision, and kernel version are different values.

For mitigation interpretation continue with [[CPU Vulnerabilities]].

## Profiling with perf

### Count first, then sample, then trace if needed

| Tool mode | Question | Result |
|---|---|---|
| `perf stat` | How much CPU work and how many events? | Event counts and elapsed/CPU time |
| `perf top` | Which functions are hot right now? | Live statistical samples |
| `perf record` + `perf report` | Which functions were hot during a reproducible run? | Saved sample data and reports |
| `perf annotate` | Where inside a sampled function? | Sample-weighted disassembly/source |
| `perf trace` / `perf sched` | Which syscalls or scheduling transitions occurred? | Tracepoint-dependent event data |

Sampling shows where execution was observed. It does not directly explain time spent asleep. A wait-heavy problem may need application instrumentation or tracing rather than more CPU samples.

### Check permissions and build features

```bash
sysctl kernel.perf_event_paranoid
perf version --build-options
perf list hw
perf list sw
```

The tested value is **2**. Own-workload user-space event counting and sampling are possible, but kernel-space and system-wide collection are restricted. The exact command matters: “unprivileged perf works” is too broad. Permissions, process ownership, event type, callchain selection, and kernel features all matter. The kernel documents these distinctions in [perf event access control](https://docs.kernel.org/admin-guide/perf-security.html).

Do not lower the global sysctl just to follow a profiling example. Use the supported user-space scope or a deliberate privileged session. `perf top -K` hides kernel symbols in the display; it does **not** grant permission or itself change the event's collection scope.

### A concrete, short practice workload

Use this to learn the workflow, not to benchmark the CPU model:

```bash
perf stat -r 3 -e cycles:u,instructions:u -- \
    bash -c 'for ((i=0; i<400000; i++)); do :; done'
```

`--` ends perf's options. `-r 3` repeats the command three times and reports statistics. `:u` requests user-space events. This invocation was exercised successfully on the local hybrid CPU.

For your real program, replace the entire `bash -c '...'` workload with its executable and arguments. Do not type angle-bracket placeholders such as `<your_command>` into a shell: `<` and `>` are redirection operators.

### perf stat: interpret counters within their scope

General patterns, after replacing `program argument` with a real workload:

```bash
perf stat -- program argument
perf stat -r 5 -e cycles:u,instructions:u,branches:u,branch-misses:u -- program argument
perf stat -d -- program argument
```

`-d` requests detailed events; support depends on the CPU. More counters are not automatically better: limited hardware counters may require multiplexing.

- **Elapsed time:** usually the primary user-visible completion metric.
- **Task-clock:** accumulated task CPU time. With filtering, understand which execution time is included.
- **Instructions/cycles:** IPC for the counted domains. A higher IPC is not automatically faster across different programs, core types, or instruction sets.
- **Branch-misses/branches:** prediction failures for those counted events.
- **Cache-misses:** hardware/event-specific; not a universal measurement of every cache level.
- **Context switches/migrations:** require meaningful counting scope. With user-only filtering, zeros may reflect filtering rather than no actual scheduling.

`<not supported>` means the event cannot be provided in that setup. `<not counted>` can mean no execution on that PMU or a scheduling/resource issue. A reported running percentage below 100% needs interpretation: on hybrid systems it can reflect time outside that PMU's CPU type as well as counter multiplexing. Do not interpret every percentage as measurement accuracy.

For a privileged system-wide interval:

```bash
sudo perf stat -a -e cycles,instructions -- sleep 5
```

The events cover the machine over the interval, not only the sleeping process. This specific system-wide example was documentation-checked, not run during the review.

### Hybrid CPUs and affinity

Generic hardware events may expand into both `cpu_core/...` and `cpu_atom/...`. Counts and sampling populations can differ because a task migrates. Keep comparisons on the same core type when that is the question.

Discover PMU CPU masks and the process's allowed CPUs first. Then choose a valid CPU list:

```bash
cpu_list=0
# CPU 0 is only an example: choose it from the discovered topology/allowed set.
taskset -c "$cpu_list" perf stat -r 5 -e cycles:u,instructions:u -- \
    bash -c 'for ((i=0; i<400000; i++)); do :; done'
```

Affinity limits where this process and inherited threads run. It does not reserve a core, silence interrupts, or prevent other tasks using it. Pinning can improve experimental consistency but can also constrain application performance. Compare pinned and normal behavior when evaluating a real application.

### perf record and report: a tested user-space workflow

Use a fresh output path per experiment so the capture being analyzed is unambiguous:

```bash
profile_dir=$(mktemp -d)
perf record -o "$profile_dir/perf.data" -e cycles:u -F 99 \
    --user-callchains --call-graph dwarf,16384 -- \
    bash -c 'for ((i=0; i<400000; i++)); do :; done'
perf report -i "$profile_dir/perf.data" --stdio
```

This exact collection pattern produced samples and readable callgraphs locally. `-F 99` requests a modest sampling frequency; it is not an optimal rate for every task. Very short workloads may yield few or no samples.

Why the explicit options:

- `cycles:u` excludes kernel execution from the event.
- `--user-callchains` also excludes kernel callchains. On this machine, DWARF recording without it failed with a permission error at paranoid level 2, even with `cycles:u`.
- `--call-graph dwarf,16384` records up to 16 KiB of user stack per sample for unwinding. It costs more recording bandwidth and storage than simple instruction-pointer sampling.
- `-o` and `-i` ensure the report reads the intended file.

For lower overhead, omit callgraph collection and obtain a flat hot-function profile. `-g` normally uses frame-pointer unwinding in user space; it depends on usable frame pointers. DWARF unwinding is appropriate when frame-pointer chains are unavailable, provided unwind information and build support exist. No single unwind mode is guaranteed to work for every executable.

The report's **Self** column attributes samples to a function itself; **Children** includes descendant work in its callchain. Do not sum inclusive percentages as though they were disjoint CPU time. Read separate PMU/event sections separately.

An optional interactive report uses the same file:

```bash
perf report -i "$profile_dir/perf.data"
```

The noninteractive report was exercised; the TUI was not reviewed interactively.

### Live profiling and annotation

For an existing process that you own, replace `1234`:

```bash
perf top -p 1234 -e cycles:u
```

System-wide live profiling:

```bash
sudo perf top
```

These live interfaces were checked against the installed help/manual; they were not used interactively in the review.

After identifying a **directly sampled** function:

```bash
perf annotate -i "$profile_dir/perf.data" --stdio --symbol function_name
```

Replace `function_name` with a symbol in your report. A caller with a large Children percentage but zero Self samples may have nothing to annotate. A local attempt to annotate `main` printed “no samples” despite exit status zero; annotation of the sampled libc `cfree` symbol produced disassembly successfully. Inspect the output, not only the exit code.

### Symbols and debuginfod

```bash
printf '%s\n' "${DEBUGINFOD_URLS-}"
perf version --build-options
```

The local perf build has debuginfod and DWARF support. To permit Arch debug-info downloads when online:

```bash
export DEBUGINFOD_URLS=https://debuginfod.archlinux.org
```

Downloads may add latency to the first report/annotation. Offline operation requires locally available binaries/debug information or an existing cache. For one deliberately offline analysis invocation:

```bash
DEBUGINFOD_URLS= perf report -i "$profile_dir/perf.data" --stdio
```

`[unknown]` can reflect missing symbols, changed binaries, permission restrictions, JIT code, or unwind failure. Distinguish symbol naming from stack unwinding: debug symbols improve names/source, while unwind data is needed to reconstruct callers.

### Trace and scheduler workflows require kernel support

Check the relevant interfaces before attempting these workflows:

```bash
perf list tracepoint
zcat /proc/config.gz | grep -E 'CONFIG_(FTRACE|TRACING|TRACEPOINTS|SCHEDSTATS|DEBUG_INFO_BTF)'
```

On the running custom battery kernel:

- `/sys/kernel/tracing` and its scheduler/syscall events are absent.
- `/sys/kernel/btf/vmlinux` is absent.
- `/proc/schedstat` is absent and `CONFIG_SCHEDSTATS` is disabled.
- Root `perf sched record` failed because `sched:sched_switch` was unavailable.
- Root `perf trace` failed with missing BTF and syscall tracepoint errors.

`sudo` cannot create features compiled out of the kernel. Use a kernel with the needed tracing features before expecting these documented patterns to work:

```bash
sudo perf trace -- program argument
sudo perf sched record -o sched.data -- sleep 5
sudo perf sched timehist -i sched.data
```

Keep tracepoint support, BPF/BTF requirements for the selected implementation, and scheduler statistics distinct: they are related tooling inputs, not the same kernel feature.

### Root captures and diagnostic warnings

A root capture was recorded and read successfully both as root and, after making the file readable, as the normal user. The old blanket claim that sudo captures are corrupted on custom kernels was **not reproduced**.

If a capture cannot be opened, inspect the exact path, file permissions, size, collection exit status, and `perf report --header`. “Permission denied,” an empty file, and “incompatible format” are different failures. Do not attribute them to sudo without reproducing the cause.

The tested recordings printed `Failed to open /proc/schedstat` but still contained samples and produced reports. That warning describes missing scheduler metadata; it did not invalidate the demonstrated CPU profile.

## Controlled load and reproducible comparisons

### Start with a small, finite exercise

```bash
stress-ng --cpu 1 --cpu-load 20 --timeout 2s --metrics-brief --verify
```

This was exercised successfully: one CPU stressor passed, zero failed. It validates the basic invocation, not cooling under maximum load.

For a deliberate sustained CPU experiment:

```bash
stress-ng --cpu 1 --timeout 30s --metrics-brief
stress-ng --cpu 0 --timeout 30s --metrics-brief
```

`--cpu 0` requests workers for the available configured processors as documented by stress-ng; check affinity/cgroup restrictions when interpreting the resulting load. These larger experiments were **not** run during the review.

Observe `sensors` and `turbostat` in a second terminal. Keep the power source, firmware/platform profile, policy manager, governor, EPP, limits, fan policy, and room/cooling conditions comparable. Stop with Ctrl+C if you need to end a run.

`--cpu-load 20` targets a worker duty cycle, not a guarantee of 20% whole-machine utilization. The default CPU method cycles through different operations; choose a documented specific `--cpu-method` if a controlled synthetic comparison needs one.

stress-ng's bogo-ops are stressor-specific accounting. Its manual explicitly rejects them as a reliable general throughput benchmark. Prefer the real application's completion time and correctness.

### A practical comparison procedure

1. Define the actual question: completion time, frame latency, throughput, energy, or stability.
2. Record the starting configuration and input data.
3. Run a warm-up only if the intended workload is a warm-cache workload.
4. Measure multiple runs and keep all outcomes, including failures and variability.
5. Change exactly one intended variable.
6. Repeat with matching conditions. Alternate A/B order if thermal drift could bias results.
7. Restore the previous policy/configuration and verify it.

Record at least:

```bash
uname -r
pacman -Q perf cpupower systemd
lscpu -e=CPU,ONLINE,SOCKET,CORE,MAXMHZ,MINMHZ
cpupower -c all frequency-info
cat /proc/cmdline
journalctl -k -b --no-pager --grep=microcode
grep -H . /sys/devices/system/cpu/vulnerabilities/*
sensors
```

Also record the program version, exact command/input, elapsed time, affinity, available CPU count, and relevant profile. Run metadata collection separately from the timed region; it creates its own work and output.

Do not compare a one-core turbo ceiling with sustained all-core frequency, or results from different microcode/kernel/mitigation states without recording the difference. See [[CPU Vulnerabilities]] for the mitigation-related variables.

## Intel video offload: an adjacent CPU diagnosis

High CPU use while playing video can be a decoder selection problem rather than a CPU policy problem. This section applies to Intel graphics; AMD/NVIDIA need their own device/driver-specific investigation.

### Packages and driver

```bash
sudo pacman -S --needed intel-gpu-tools libva-utils intel-media-driver
```

The Intel [media-driver project](https://github.com/intel/media-driver) lists supported platforms and codec capabilities. `intel-media-driver` supplies the `iHD` VA-API driver used successfully on the local Alder Lake iGPU. Do not infer every codec, bit depth, or encode mode from a broad generation label.

The old `i965`/`libva-intel-driver` advice is outside this modern-hardware reference. It is not an alternate decoder driver for this Alder Lake CPU.

### Wayland capability check

```bash
vainfo --display wayland
```

This succeeded locally and reported Intel iHD 26.3.5. `VAEntrypointVLD` indicates a decode entry point; encode entry points are separate. Successful capability enumeration says the driver offers an operation; it does not prove an application is using it.

For a DRM check, discover the render nodes instead of assuming `renderD128` is Intel:

```bash
for node in /sys/class/drm/renderD*; do
    [[ -e "$node/device" ]] || continue
    printf '%s vendor=' "/dev/dri/${node##*/}"
    cat "$node/device/vendor"
    printf '  driver: '
    readlink -f "$node/device/driver"
done
```

Select the node whose vendor/driver matches the intended Intel GPU. Then set the discovered path:

```bash
intel_render_node=/dev/dri/renderD128  # replace with the discovery result
vainfo --display drm --device "$intel_render_node"
```

The variable here illustrates assignment of a **discovered** value, not a stable device-number promise. On the tested machine the DRM check succeeded on the Intel node; the other node belongs to NVIDIA.

### Observe actual media-engine work

```bash
sudo intel_gpu_top -L
sudo intel_gpu_top
```

Use `-d` with a filter shown by the installed help/manual when more than one Intel GPU exists. The tool supports a DRM-node selector such as `-d "drm:$intel_render_node"`.

During playback, look specifically for **Video/VCS** and relevant VideoEnhance/VECS activity. Render/RCS activity alone can be composition or rendering and does not demonstrate hardware video decoding. Also check the application's decoder diagnostics: it may use software decoding for an unsupported codec/profile, software filters, or only part of a pipeline.

Capability checks and finite GPU telemetry were exercised; actual application playback/transcoding was not. Low CPU use plus media-engine activity supports offload, but high CPU use alone does not prove offload is absent.

## Troubleshooting by symptom

| Symptom | Focused next step |
|---|---|
| CPU count differs from the processor specification | Compare `lscpu`, online/present masks, SMT topology, `CONFIG_NR_CPUS`, firmware, affinity, and cgroups. |
| Only `performance powersave` governors appear | Check driver and mode; this is expected for active Intel and AMD EPP paths. |
| A governor change disappears | Inspect the policy manager and AC/battery/profile events. |
| `cpupower` reports a lower maximum on some CPUs | Inspect all policies and hybrid topology; an E-core ceiling differs from a P-core ceiling. |
| High GHz, little CPU work | Compare `Bzy_MHz`, `Avg_MHz`, and `Busy%` rather than frequency alone. |
| Low all-core sustained clocks | Observe actual load, temperature, power limits, boost permission, and firmware profile together. |
| High idle package power | Compare CPU activity, software idle counters, hardware package residency, graphics/device activity. |
| `perf stat` works but DWARF recording is denied | Use the tested user-only event and `--user-callchains` combination. |
| `perf trace`/`sched` fails as root | Verify required tracepoints and selected BPF/BTF path; missing kernel support is not a permission problem. |
| `perf report` has unknown symbols | Verify file/binary identity, symbol availability, unwind mode, and download/cache state. |
| `perf annotate` says “no samples” | Choose a symbol with actual Self samples; inspect output even if exit status is zero. |
| VA-API initializes but playback CPU remains high | Check the application's selected decoder, codec/profile, filters, and Video-engine activity. |

## Source map

Use installed manuals for exact command syntax and the links above for subsystem behavior. Additional useful references:

- [Kernel hardware vulnerability documentation](https://docs.kernel.org/admin-guide/hw-vuln/index.html), interpreted in [[CPU Vulnerabilities]].
- [Intel i7-12700H specifications](https://www.intel.com/content/www/us/en/products/sku/132228/intel-core-i712700h-processor-24m-cache-up-to-4-70-ghz/specifications.html), distinguished from currently exposed topology in [[CPU verification record]].
- Installed `mpstat(1)`, `pidstat(1)`, `cpupower(1)`, `cpupower-idle-info(1)`, `taskset(1)`, `sensors(1)`, `stress-ng(1)`, and perf subcommand manuals.

[[CPU verification record]] records empirical successes, failures, and what remains untested. It is a dated observation, not a promise about every future update or hardware platform.
