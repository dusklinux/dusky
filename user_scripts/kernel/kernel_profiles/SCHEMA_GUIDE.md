# Dusky Kernel Compiler v5.0.0 — Profile Schema & Configuration Guide
# ==============================================================================
# Target: Linux 7.2.x+ / x86_64 / Arch Linux Rolling (2026.08+)
# This guide documents every section, every knob, allowed choices, and best
# practices according to the systems engineering audit.
# ==============================================================================

[meta]
# Unique internal identifier for the profile (referenced with: --profile my_name)
name = "my_custom_kernel"

# Brief summary shown in the interactive menu and profile list table
description = "Custom build optimized for my daily workflow"

# LOCALVERSION suffix and package discriminator (e.g. linux-dusky-custom)
suffix = "dusky-custom"

# Sort order in the interactive menu (0 = top of list, 100 = bottom)
priority = 10

# Free-form label tags for categorization
tags = ["desktop", "gaming", "custom"]

# Gates paravirt spinlock stripping (set true only for physical bare metal)
bare_metal_only = true

# Marks distributable package; prevents dangerous machine-specific strict module pruning
portable_package = false


[release]
# Which upstream kernel.org branch to track:
#   - "mainline" : Bleeding-edge upstream branch (Linux 7.2+)
#   - "stable"   : Latest stable point release
#   - "longterm" : Long-Term Support branch (LTS)
channel = "mainline"

# Exact version pin (e.g. "7.2.0"). Leave empty ("") for latest in channel.
pin = ""

# Set to true to allow building -rc (release candidate) tarballs
allow_rc = false

# Minimum version floor (default "7.2"). Zero backward compatibility with <7.0.
min_version = "7.2"


[scheduler]
# Base in-tree or patched scheduler class:
#   - "eevdf" : Pristine mainline Earliest Eligible Virtual Deadline First (Recommended)
#   - "bore"  : Burst-Oriented Response Enhancer (optional patch)
#   - "bmq"   : BitMap Queue alternative scheduler (Note: incompatible with sched_ext/CAS)
type = "eevdf"

# Dynamic runtime BPF scheduler (sched_ext):
#   - "none"        : Run pure base EEVDF
#   - "scx_lavd"    : Latency-critical and gaming scheduler with auto-pilot (Recommended for gaming)
#   - "scx_bpfland" : Interactive desktop responsiveness scheduler
#   - "scx_layered" : Multi-layer scheduling for complex workstations
#   - "scx_rusty", "scx_flash", "scx_p2dq"
scx = "scx_lavd"

# Flags passed to the SCX daemon (e.g. "--autopilot")
scx_flags = "--autopilot"

# Build CONFIG_SCHED_CLASS_EXT in-tree infrastructure
scx_enable_class = true

# Fail build hard if out-of-tree patch fails to apply
require_patch = false

# CONFIG_SCHED_AUTOGROUP: Automatic per-session task grouping for desktop smoothness
autogroup = true

# CONFIG_RT_GROUP_SCHED: cgroup bandwidth control for real-time tasks
rt_group = false

# Fall back to upstream EEVDF if out-of-tree patch fails
allow_vanilla_fallback = true


[cache]
# Linux 7.2 Cache Aware Scheduling (CONFIG_SCHED_CACHE):
# Co-locates communicating tasks into the same Last Level Cache (LLC) domain.
sched_cache = true

# Aggregation tolerance:
#   - 0  : Runtime disabled (clean A/B testing)
#   - 1  : Conservative desktop sweet-spot (Recommended)
#   - >1 : Aggressive server / database clustering
llc_aggr_tolerance = 1

# Install and enable dusky-cache-aware-sched.service to persist debugfs knobs
persist = true


[rseq]
# Linux 7.0+ Restartable Sequences (rseq) Time Slice Extension:
# Extends running quantum by up to N nanoseconds for threads in critical sections.
slice_extension = true

# Extension duration in nanoseconds (5000 to 50000; default 10000 = 10us)
slice_ext_nsec = 10000


[dusky]
# Enables opinionated desktop latency heuristics and umbrella tuning
enhanced = false

# Identifiers baked into kernel build metadata for reproducibility
hostname = ""
user = ""

# Fixes build timestamp to tarball release date (SOURCE_DATE_EPOCH)
reproducible = true

# Escape hatch: inject arbitrary raw Kconfig symbols ({ "SYMBOL" = value })
extra_config = {}


[cpu]
# Target CPU instruction set architecture:
#   - "native"         : Host processor uarch (fastest, unlocks all ISA instructions on this PC)
#   - "generic_v2"     : x86-64-v2 baseline (SSE4.2 / POPCNT)
#   - "generic_v3"     : x86-64-v3 baseline (AVX2, BMI2, FMA)
#   - "generic_v4"     : x86-64-v4 baseline (AVX-512)
#   - Intel families   : "sandybridge", "ivybridge", "haswell", "broadwell", "skylake", "icelake", "tigerlake", "rocketlake", "alderlake", "raptorlake", "meteorlake", "sapphirerapids"
#   - AMD families     : "znver1" (Zen 1), "znver2" (Zen 2), "znver3" (Zen 3), "znver4" (Zen 4), "znver5" (Zen 5)
arch = "native"

# Optional explicit -march string passed via KCFLAGS/KAFLAGS
march = ""

# Default boot-time CPU frequency scaling governor:
#   - "schedutil"   : Dynamic frequency scaling based on scheduler runqueue (Recommended)
#   - "performance" : Locks clock to maximum
#   - "powersave"   : Forces lowest power frequency floor
governor = "schedutil"

# AMD P-State driver mode:
#   - "active" : Hardware autonomous Energy Performance Preference (EPP)
#   - "guided" : Autonomous within kernel-guided min/max range (Best for laptops)
#   - "passive": Kernel-driven software scaling
amd_pstate = "active"

# Energy Performance Preference (EPP):
#   - "balance_performance" : Desktop & gaming sweet-spot
#   - "performance"         : Maximum frequency boost
#   - "balance_power"       : Mobile endurance
epp = "balance_performance"

# Vulnerability mitigations (Spectre, Meltdown, Retbleed):
#   - true  : Full hardware/software mitigations
#   - false : Baked-in mitigations=off (requires security.acknowledge_risk = true)
mitigations = true

# Maximum CPUs (0 = auto-snaps to 64 boundary: 64, 128, etc. flattens RCU tree)
nr_cpus = 0

# Symmetric Multi-Threading (SMT / Hyperthreading)
smt = true

# Machine Check Exception reporting
mce = true

# Preferred core ranking (CONFIG_SCHED_MC_PRIO)
prefcore = true


[timing]
# Core timer tick frequency:
#   - 1000 : 1ms period, lowest interactive input latency (Gaming / Desktop)
#   - 500  : 2ms period, workstation compute sweet spot
#   - 250  : 4ms period, mobile balance
#   - 100  : 10ms period, lowest CPU wakeups & maximum battery endurance (Battery profile)
hz = 1000

# Tickless mode:
#   - "idle" : NO_HZ_IDLE (Recommended: tick disabled when CPU is idle)
#   - "full" : NO_HZ_FULL (Requires core isolation / isolcpus)
tickless = "idle"

# Preemption model (Linux 7.0+ on x86_64):
#   - "lazy" : Hybrid throughput/latency (Preempts SCHED_OTHER at tick, SCHED_FIFO immediately)
#   - "full" : Classic desktop full preemption
#   - "rt"   : Real-time preemption (PREEMPT_RT)
preempt = "lazy"

# Boot-time dynamic preemption switching (preempt= boot parameter)
preempt_dynamic = true

# Offload RCU callbacks to housekeeping CPUs
hz_periodic_rcu = false


[memory]
# Transparent Hugepages (THP) enabled mode:
#   - "madvise" : Opt-in via madvise(MADV_HUGEPAGE) (Zero desktop hitching)
#   - "always"  : Kernel attempts hugepages for all allocations
#   - "never"   : Disabled
thp = "madvise"

# THP defragmentation strategy:
#   - "defer+madvise" : Background khugepaged compaction (Recommended)
#   - "defer"         : Defer sync allocations to background daemon
#   - "madvise"       : Synchronous compaction only for madvise regions
#   - "always", "never"
thp_defrag = "defer+madvise"

# Multi-Generational LRU (MGLRU):
mglru = true
mglru_mask = 7
mglru_min_ttl_ms = 1000

# Virtual Memory Reclaim & Writeback:
watermark_scale_factor = 200
watermark_boost_factor = 0
compaction_proactiveness = 0

# Swap Backend (Mutually exclusive: NEVER run zram and zswap together!):
#   - "zswap" : Compressed RAM writeback cache in front of physical swap
#   - "zram"  : Compressed RAM block device as swap (100% of RAM)
# Memory backend: "zram" (compressed RAM disk), "zswap" (writeback pool), or "none"
swap_backend = "zram"
zswap_compressor = "zstd"
zswap_zpool = "zsmalloc"
zram_size_pct = 100
zram_multi_comp = true

# Low-footprint allocator (hard forbidden on >512MB systems)
slub_tiny = false

# NUMA topology awareness (essential for CAS and multi-CCD Ryzen):
numa = true
numa_balancing = false
nodes_shift = 2
ksm = true
damon = false
page_reporting = false


[compiler]
# Toolchain selection:
#   - "llvm" : Clang 21+ with LLD and LLVM integrated assembler (ThinLTO/Full LTO)
#   - "gcc"  : GNU GCC 15+
toolchain = "llvm"

# Optimization level:
#   - "o2"   : -O2 performance optimization (Mainline choice)
#   - "size" : -Os size optimization (Reduces cache footprint on small systems)
optimize = "o2"

# Allow injecting -O3 via KCFLAGS (not a standard Kconfig option)
allow_unsupported_o3 = false

# Link-Time Optimization (LTO):
#   - "thin"      : ThinLTO (95-99% of Full LTO perf, 80% faster compile, low RAM)
#   - "full"      : Monolithic Full LTO (Recommended only for >=32-64GB RAM systems)
#   - "none"      : No LTO
lto = "thin"

# Persistent disk cache for LLVM ThinLTO objects
thinlto_cache = true
thinlto_cache_size = "20g"

# Feedback-Directed Optimization (AutoFDO / Propeller):
#   - "none"              : Standard build
#   - "autofdo"           : Consumes perf-collected branch samples (.afdo)
#   - "autofdo_propeller" : Consumes basic-block reordering profiles (.propeller)
fdo = "none"
fdo_profile_dir = ""

# Kernel Control Flow Integrity (kCFI):
#   - false : (Recommended default) Disables forward-edge CFI instrumentation.
#             Required when using out-of-tree proprietary drivers (e.g. nvidia-dkms)
#             to prevent symbol relocation and type-hash mismatch kernel errors.
#   - true  : Injects Clang kCFI runtime verification for pure in-tree driver stacks.
kcfi = false

# Image and module compression:
zstd_clevel = 19
module_compress = "zstd"

# DWARF Debug Info:
#   - "reduced" : Reduced DWARF + full split BTF (Fast compile, full BPF/sched_ext observability)
#   - "full"    : Full DWARF + BTF
#   - "none"    : Stripped debug info (Disables BTF and sched_ext!)
debug_info = "reduced"

# Build parallel jobs (0 = auto-detect CPU count)
jobs = 0

# Rust in Linux kernel support
rust = true

# Kernel headers package generation & installation:
#   - "auto"   : Dynamically checks for DKMS (nvidia-dkms, virtualbox-dkms, etc.).
#                If DKMS is present, builds and installs linux-headers.
#                If no DKMS drivers exist (e.g. Intel/AMD integrated graphics),
#                skips headers automatically (saving ~90MB and compile time).
#   - "always" : Always build and install linux-headers package.
#   - "never"  : Never build or install linux-headers (keeps /usr/lib/modules down to ~12MB).
headers = "auto"


[security]
# Defensive security profile:
#   - "balanced" : Modern desktop security (kstack randomization, hardened usercopy)
#   - "hardened" : Maximum defensive postures
#   - "extreme"  : Stripped defensive overhead for pure performance
profile = "balanced"

# Zero memory pages on allocation (Disabling recovers 1-3% CPU overhead):
init_on_alloc = true

# Hardened usercopy bounds checking
hardened_usercopy = true

# Stack protector level ("strong", "regular", "none")
stackprotector = "strong"

# Hardened SLAB freelist pointers (keeps ~0.1% overhead with high security)
slab_freelist_hardened = true

# Randomize kernel stack offset per syscall
randomize_kstack = true

# Speculative execution mitigations ("auto" or "off")
mitigations = "auto"

# Explicit risk acknowledgement (required if mitigations='off' or profile='extreme')
acknowledge_risk = false


[gaming]
# In-tree Windows NT Synchronization driver (CONFIG_NTSYNC=m, mainline since 6.14)
ntsync = true

# Utilization clamping for task power/performance hints
uclamp = true

# Maximum memory mapping count for Wine/Proton/DX12 shader caches
max_map_count = 2147483642

# Disable split-lock penalization under Proton
split_lock_mitigate = false


[storage]
# Hardware NVMe IOPOLL queues (0 = disabled; >0 for dedicated database/workstation cores)
nvme_poll_queues = 0

# Block device I/O scheduler:
#   - "none"        : Direct hardware NVMe queues (Recommended for NVMe)
#   - "mq-deadline" : SATA SSDs
#   - "bfq"         : Rotational HDDs
io_scheduler = "none"

# Block layer Writeback Throttling anti-stutter
blk_wbt = true


[power]
# Power-efficient workqueues
wq_power_efficient = false

# CPU idle governor ("teo" = Timer Events Oriented, "menu", "ladder")
cpu_idle_governor = "teo"

# RCU lazy callback batching for battery endurance
rcu_lazy = false

# Energy-Aware Scheduling (EAS) Energy Model
energy_model = false

# Suspend & Hibernation support
suspend = true


[network]
# TCP Congestion Control algorithm ("bbr", "cubic", "reno", "westwood", "vegas")
congestion = "bbr"

# Root queuing discipline ("fq" [Required for BBR pacing], "cake", "fq_codel")
qdisc = "fq"

# Multipath TCP support
mptcp = true

# Legacy /proc conntrack exposure
nf_conntrack_procfs = false

# eBPF XDP socket support
xdp = false


[modules]
# Driver pruning mode:
#   - "strict"   : Only drivers logged in ~/.config/modprobed.db survive (Fastest compile, ~12MB)
#                  (Default for local machine profiles: personal, gaming, battery, low_ram)
#   - "expanded" : Curated LMC_KEEP safety net (Preserves USB/GPU/audio/hotplug hardware)
#                  (Used for portable packages like generic_v3, generic_v4, and workstation)
mode = "strict"

# Capture and use hardware database from modprobed-db
modprobed_db = true

# Custom path to imported modprobed.db (used when cross-compiling for another PC)
modprobed_db_path = ""

# Additional directories to preserve during localmodconfig
lmc_keep_extra = []

# Install systemd user timer for modprobed-db store
manage_service = true

# Enforce cryptographically signed modules (breaks DKMS if true)
sig_force = false


[verify]
# Strict verification contract: hard fail build if any non-optional symbol vanishes
strict = true

# Allowed optional symbols that may not exist in specific trees
optional_symbols = [
  "SCHED_BORE",
  "SCHED_ALT",
  "THINLTO_CACHE",
  "PER_VMA_LOCK",
  "SLAB_BUCKETS",
  "MEMORY_TIERING",
  "SWAP_TABLE",
  "CFI_ICALL_NORMALIZE_INTEGERS",
  "MODULE_ALLOW_BTF_MISMATCH",
  "TRIM_UNUSED_KSYMS",
  "LD_DEAD_CODE_DATA_ELIMINATION",
]

# Run post-build smoke tests
assert_runtime = true

# Assert subsystem availability in build
require_ntsync = true
require_btf = true
require_sched_ext = true


# ==============================================================================
# Cross-Machine Kernel Compilation (Hardware Bundle Workflow)
# ==============================================================================
#
# Compile highly optimized native kernels on a fast workstation for a slower PC:
#
# 1. On the Slow/Target Machine:
#    Export its hardware telemetry, CPU uarch, and modprobed.db:
#      python3 dusky_kernal_compile.py --export-bundle
#    (Saved to ~/.config/dusky/settings/dusky_kernel_compile/exports/dusky_bundle_<hostname>.tar.gz)
#
# 2. Copy the bundle to your Fast Build PC:
#      scp user@target:~/.config/dusky/settings/dusky_kernel_compile/exports/dusky_bundle_<hostname>.tar.gz .
#
# 3. On your Fast Build PC:
#    Import the bundle (installs target modprobed.db & generated profile):
#      python3 dusky_kernal_compile.py --import-bundle dusky_bundle_<hostname>.tar.gz
#
# 4. Compile on the Fast PC:
#      python3 dusky_kernal_compile.py --profile remote_<hostname> -y
#
# 5. Copy the produced .pkg.tar.zst back to the target machine and install:
#      sudo pacman -U linux-dusky-<hostname>-*.pkg.tar.zst
#
# ==============================================================================
# CLI Flags & Quick Reference
# ==============================================================================
#
# Build with specific profile:
#   python3 dusky_kernal_compile.py --profile battery -y
#
# Skip linux-headers package:
#   python3 dusky_kernal_compile.py --profile battery --no-headers -y
#
# Override module mode ephemerally:
#   python3 dusky_kernal_compile.py --profile battery --modules-mode expanded
#
# Export / Import Hardware Bundle:
#   python3 dusky_kernal_compile.py --export-bundle [output.tar.gz]
#   python3 dusky_kernal_compile.py --import-bundle input.tar.gz
#
# Force fresh source extraction:
#   python3 dusky_kernal_compile.py --profile battery --fresh -y
#
# Diagnostics report (toolchain, DKMS, memory, zram):
#   python3 dusky_kernal_compile.py --doctor
#
# Dry-run Kconfig matrix verification:
#   python3 dusky_kernal_compile.py --print-matrix --all
#
# Clean build artifacts:
#   python3 dusky_kernal_compile.py --clean
# ==============================================================================
