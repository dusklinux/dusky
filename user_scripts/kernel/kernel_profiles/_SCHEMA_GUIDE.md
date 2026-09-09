# Dusky Kernel Compiler v6.1.0 — Master Exhaustive Profile Template
# Target: Linux 7.2+ / 7.3-rc on Arch Linux (LLVM 21+, Python 3.14+)

# ==============================================================================
# [meta] — Profile Identity, Portability & VM Guardrails
# ==============================================================================
[meta]
# Profile identifier passed to CLI (--profile <name>)
name = "my_custom_kernel"

# Informational description displayed in menus and tables
description = "Custom tailored kernel build for Arch Linux"

# Package and kernel release discriminator: linux-<suffix> and linux-<suffix>-headers
suffix = "dusky-custom"

# Sort priority in interactive selection menus (lower numbers sort first)
priority = 50

# Metadata categorization tags
tags = ["desktop", "custom"]

# Strip hypervisor guest/paravirt drivers (Refuses build on VMs unless --force is passed)
bare_metal_only = false

# Target machine is non-local (Strictly forbids cpu.arch = "native")
portable_package = false


# ==============================================================================
# [release] — Kernel Upstream Branch, Pinning & Signatures
# ==============================================================================
[release]
# Branch choice: "mainline" (7.3-rc), "stable" (7.2.y), "longterm"
channel = "stable"

# Exact version string (e.g. "7.2.4", "7.3-rc2"). Empty ("") tracks newest in channel
pin = ""

# Allow building -rc snapshot releases from mainline
allow_rc = true

# Hard floor version requirement (engine enforces >= 7.2)
min_version = "7.2"

# Enforce PGP cryptographic verification using kernel.org release keys
require_signature = true


# ==============================================================================
# [scheduler] — Scheduling Classes & sched_ext
# ==============================================================================
[scheduler]
# Base fair scheduler: "eevdf" (upstream), "bore" (burst overlay), "bmq" (Project C)
type = "eevdf"

# Dynamic userspace BPF scheduler daemon:
# "none", "scx_lavd", "scx_bpfland", "scx_layered", "scx_rusty", "scx_flash", "scx_p2dq", "scx_cosmos"
# NOTE: sched_ext daemons (like scx_lavd) require memory.tracing = "full" for BPF fentry probes.
# For maximum laptop battery savings, scx = "none" with in-kernel EEVDF + CAS is recommended
# to avoid the constant CPU wakeups and polling overhead of the userspace BPF daemon.
scx = "none"

# Flags passed to SCX daemon (e.g., "--autopilot", "-m performance")
scx_flags = ""

# Compile CONFIG_SCHED_CLASS_EXT, BPF_JIT and BTF infrastructure in-tree
scx_enable_class = true

# Halt compilation if out-of-tree scheduler patch does not apply cleanly
require_patch = false

# Fall back to upstream EEVDF if scheduler patch fails
allow_vanilla_fallback = true

# Automatic per-session task grouping (CONFIG_SCHED_AUTOGROUP)
autogroup = true

# Bandwidth control for RT tasks (CONFIG_RT_GROUP_SCHED; false protects PipeWire)
rt_group = false

# Core scheduling SMT side-channel isolation (CONFIG_SCHED_CORE)
sched_core = false

# Ordered patch resolver backends
patch_sources = ["cachyos", "upstream_author", "tkg"]


# ==============================================================================
# [cache] — Cache-Aware Scheduling (CAS)
# ==============================================================================
[cache]
# LLC affinity balancing for multi-CCD / multi-cluster CPUs (CONFIG_SCHED_CACHE)
sched_cache = true

# Aggregation tolerance in debugfs (0 = disabled, 1 = strict desktop, >1 = aggressive)
llc_aggr_tolerance = 1

# LLC aggregation capacity percentage (-1 = kernel default)
llc_aggr_cap = -1

# Transitional legacy flag (persists across boots; no-op in engine)
persist = false


# ==============================================================================
# [rseq] — Restartable Sequences Slice Extension
# ==============================================================================
[rseq]
# Extend quantum briefly for threads preempted within critical sections
slice_extension = true

# Maximum granted extension in nanoseconds (1000 to 100000; 10000 = 10us)
slice_ext_nsec = 10000


# ==============================================================================
# [dusky] — Build Identity, Enhancement Patches & Kconfig Overrides
# ==============================================================================
[dusky]
# Desktop heuristics: nowatchdog, skip deferred fbcon takeover
enhanced = true

# Inlines finish_task_switch() (~8.6% to 35% context switch speedup)
patch_sched_inline = true

# Asynchronous evdev detach (eliminates input close stalls)
patch_evdev_rcu = true

# Clear Linux 4000ms PCI PME polling interval (reduces idle wakeups)
patch_pci_pme = true

# Build hostname metadata (empty = system hostname)
hostname = ""

# Build user metadata (empty = current user)
user = ""

# Pin KBUILD_BUILD_TIMESTAMP and SOURCE_DATE_EPOCH for reproducibility
reproducible = true

# Base .config seed: "auto", "snapshot", "arch", "running", "headers", "defconfig"
seed = "auto"

# Direct Kconfig override map (applied last; CONFIG_ prefix optional)
extra_config = {}


# ==============================================================================
# [cpu] — Microarchitecture, P-States & Mitigations
# ==============================================================================
[cpu]
# Target CPU instruction set architecture:
# "native", "generic", "generic_v2", "generic_v3", "generic_v4",
# "znver1" to "znver5", "alderlake", "raptorlake", "meteorlake", "arrowlake", etc.
arch = "native"

# Explicit additional -march/-mtune compiler flags
march = ""

# Default boot cpufreq governor: "schedutil", "performance", "powersave", "ondemand", "conservative"
governor = "schedutil"

# AMD P-State driver mode: "active" (autonomous EPP), "guided", "passive", "disable", "undefined"
amd_pstate = "active"

# Energy Performance Preference hint: "default", "performance", "balance_performance", "balance_power", "power"
epp = "balance_performance"

# Vulnerability mitigations: "on", "off" (requires acknowledge_risk), "nosmt"
mitigations = "on"

# CONFIG_NR_CPUS (0 = auto: host threads rounded up to multiple of 8, minimum floor of 64)
# Note: Sizing with a minimum floor of 64 prevents hybrid P+E CPUs (e.g. 14-24 cores), offlined cores,
# or SMT/Hyper-Threading toggling from permanently clipping core counts across boots.
# On x86-64, NR_CPUS <= 64 fits in a single 64-bit machine word (zero memory/instruction penalty).
nr_cpus = 0

# Symmetric Multi-Threading support (CONFIG_SCHED_SMT)
smt = true

# Machine Check Exception support (CONFIG_X86_MCE)
mce = true

# Preferred core ranking / Intel ITMT (CONFIG_SCHED_MC_PRIO)
prefcore = true

# IA32 32-bit binary emulation (CONFIG_IA32_EMULATION; required for Steam/Wine 32-bit)
compat32 = true


# ==============================================================================
# [timing] — Tick Frequency & Preemption Model
# ==============================================================================
[timing]
# Core tick frequency: 100, 250, 300, 500, 600, 750, 1000 Hz
hz = 1000

# Tickless mode: "idle" (NO_HZ_IDLE), "full" (NO_HZ_FULL), "periodic"
tickless = "idle"

# Base preemption model: "lazy" (PREEMPT_LAZY), "full" (PREEMPT), "rt" (PREEMPT_RT)
preempt = "lazy"

# Enable dynamic preemption switching via preempt= parameter (incompatible with RT)
preempt_dynamic = true


# ==============================================================================
# [memory] — Paging, Reclaim, Compressed Swap & Footprint
# ==============================================================================
[memory]
# Footprint preset: "standard", "lean", "minimal", "embedded"
footprint = "standard"

# Transparent Hugepages: "madvise", "always", "never"
thp = "madvise"

# THP defragmentation: "defer+madvise", "defer", "madvise", "always", "never"
thp_defrag = "defer+madvise"

# THP for shmem/tmpfs: "never", "within_size", "advise", "always"
thp_shmem = "never"

# Multi-Generational LRU (sets CONFIG_LRU_GEN and CONFIG_LRU_GEN_ENABLED)
mglru = true

# MGLRU leaf/non-leaf access bitmask (0 to 7; 7 = standard x86)
mglru_mask = 7

# MGLRU anti-thrashing threshold in ms (0 = disabled, >0 = OOM trigger threshold)
mglru_min_ttl_ms = 1000

# Swap backend: "zram", "zswap", "none" (mutually exclusive)
swap_backend = "zram"

# Primary ZRAM algorithm: "zstd", "lz4", "lz4hc", "lzo-rle"
zram_algo = "zstd"

# Secondary recompression algorithm for idle pages
zram_recomp_algo = "zstd"

# ZRAM disk size as a percentage of physical RAM
zram_size_pct = 100

# Multi-algorithm ZRAM and access timestamping (CONFIG_ZRAM_TRACK_ENTRY_ACTIME)
zram_multi_comp = true

# Zswap compression algorithm (used if swap_backend = "zswap")
zswap_compressor = "zstd"

# Maximum RAM percentage for zswap pool
zswap_max_pool_pct = 25

# vm.swappiness runtime recommendation (0 = auto: 180 for zram, 100 for zswap, 60 for none)
swappiness = 0

# vm.vfs_cache_pressure runtime recommendation (0 = auto by footprint)
vfs_cache_pressure = 0

# vm.watermark_scale_factor (proactive kswapd wake threshold; 125 = 1.25%)
watermark_scale_factor = 125

# vm.watermark_boost_factor (fragmentation boost; 0 recommended with zram)
watermark_boost_factor = 0

# vm.compaction_proactiveness (0 = auto: 20 with THP, 0 without THP)
compaction_proactiveness = 0

# vm.dirty_bytes in MiB (0 = default ratio)
dirty_bytes_mb = 0

# Minimal slab allocator (CONFIG_SLUB_TINY; recommended only for <= 4-8 GB and <= 8 cores)
slub_tiny = false

# Dedicated kmalloc buckets for user allocations (CONFIG_SLAB_BUCKETS; requires !slub_tiny)
slab_buckets = false

# Concurrent RCU-protected page fault handling (CONFIG_PER_VMA_LOCK)
per_vma_lock = true

# NUMA topology support (CONFIG_NUMA)
numa = true

# Automatic NUMA balancing (CONFIG_NUMA_BALANCING; overhead on single-socket systems)
numa_balancing = false

# Maximum NUMA nodes as 2^n (CONFIG_NODES_SHIFT; 2 = up to 4 nodes)
nodes_shift = 2

# Kernel Samepage Merging infrastructure (CONFIG_KSM)
ksm = true

# Activate ksmd scanning daemon at boot
ksm_run = false

# DAMON access monitoring and proactive memory reclaim (CONFIG_DAMON_RECLAIM)
damon = false

# Hypervisor free page reporting via virtio-balloon (guest VMs only)
page_reporting = false

# Traditional static hugetlbfs (/dev/hugepages)
hugetlbfs = true

# Export non-function data symbols in kallsyms (CONFIG_KALLSYMS_ALL)
kallsyms_all = true

# cgroup v2 memory controller (CONFIG_MEMCG; required for systemd resource control)
memcg = true

# Shrink core kernel hash tables (CONFIG_BASE_SMALL)
base_small = false

# Kernel log buffer size as 2^n bytes (CONFIG_LOG_BUF_SHIFT; 0 = auto)
log_buf_shift = 0

# Kernel tracing surface: "auto", "full", "minimal"
# Note: "minimal" strips FTRACE and function tracing for power/latency gains, but breaks
# sched_ext daemons (e.g. scx_lavd) whose BPF trampoline probes require CONFIG_FTRACE.
tracing = "auto"

# Fast kernel kexec reboot and crash dump support
kexec = true

# Embed kernel configuration in /proc/config.gz (CONFIG_IKCONFIG_PROC)
ikconfig = true

# Transitional legacy flag (runtime userspace hint)
systemd_oomd = false

# Strip unreferenced exported symbols (CONFIG_TRIM_UNUSED_KSYMS; requires headers = "never")
trim_unused_ksyms = false

# Dead code elimination (inert on x86-64 without out-of-tree patches)
dead_code_elimination = false


# ==============================================================================
# [compiler] — Toolchain, LTO, kCFI & Rust
# ==============================================================================
[compiler]
# Toolchain backend: "llvm" (Clang 21+ and LLD) or "gcc"
toolchain = "llvm"

# Optimization level: "o2", "o3", "size"
optimize = "o2"

# Clang Polly polyhedral loop optimizer (CONFIG_POLLY_CLANG)
polly = false

# Link-Time Optimization: "thin", "full", "none"
lto = "thin"

# Persist ThinLTO bitcode cache across builds
thinlto_cache = true

# ThinLTO disk cache pruning limit in GiB
thinlto_cache_size_gb = 20

# Feedback-Directed Optimization: "none", "autofdo", "autofdo_propeller"
fdo = "none"

# Directory containing kernel.afdo / propeller profile files
fdo_profile_dir = ""

# Clang Control Flow Integrity (kCFI + FineIBT; incompatible with nvidia-dkms)
kcfi = false

# DWARF debug info: "full" (DWARF5; required for BTF, sched_ext & CO-RE eBPF), "reduced" (DWARF5 without struct info; disables BTF), "none"
debug_info = "reduced"

# Module compression codec: "zstd", "xz", "gzip", "none"
module_compress = "zstd"

# Rust-for-Linux support (auto-disabled if both LTO and BTF are enabled)
rust = false

# Parallel build jobs (0 = auto-calculated from CPU threads and memory)
jobs = 0

# Headers package policy: "auto" (if DKMS detected), "always", "never"
headers = "auto"

# Symbol CRCs for module verification (CONFIG_MODVERSIONS)
modversions = false


# ==============================================================================
# [security] — Hardening Profiles & Exploit Mitigations
# ==============================================================================
[security]
# Security posture preset: "balanced", "hardened", "extreme"
profile = "balanced"

# Zero heap memory on allocation (CONFIG_INIT_ON_ALLOC_DEFAULT_ON)
init_on_alloc = true

# Zero heap memory on free (CONFIG_INIT_ON_FREE_DEFAULT_ON; expensive)
init_on_free = false

# Hardened usercopy bounds verification (CONFIG_HARDENED_USERCOPY)
hardened_usercopy = true

# Stack protector canary model: "strong", "regular", "none"
stackprotector = "strong"

# Obfuscate SLAB freelist pointers (CONFIG_SLAB_FREELIST_HARDENED; requires !slub_tiny)
slab_freelist_hardened = true

# Randomize SLAB allocation order (CONFIG_SLAB_FREELIST_RANDOM; requires !slub_tiny)
slab_freelist_random = true

# Randomize kernel stack offset per syscall (CONFIG_RANDOMIZE_KSTACK_OFFSET_DEFAULT)
randomize_kstack = true

# Array bounds runtime instrumentation (CONFIG_UBSAN_BOUNDS)
ubsan_bounds = true

# Build AppArmor and insert into default CONFIG_LSM string
apparmor = false

# Build SELinux
selinux = false

# Early kernel lockdown enforcement (CONFIG_SECURITY_LOCKDOWN_LSM_EARLY)
lockdown_early = false

# Explicit acknowledgement required if profile = "extreme" or cpu.mitigations = "off"
acknowledge_risk = false


# ==============================================================================
# [gaming] — Low-Latency & Windows Interop
# ==============================================================================
[gaming]
# In-tree Windows NT Synchronization primitives driver (CONFIG_NTSYNC=m; /dev/ntsync)
ntsync = true

# Utilization clamping support (CONFIG_UCLAMP_TASK; active under schedutil)
uclamp = true

# Maximum memory mapping count for Wine/Proton/DX12 shader pipelines
max_map_count = 2147483642

# Disable split-lock penalization and rate-limiting (split_lock_detect=off)
split_lock_mitigate = false

# Preserve controller and input drivers during strict module pruning
controllers = true


# ==============================================================================
# [storage] — Block Layer & Filesystems
# ==============================================================================
[storage]
# Dedicated hardware NVMe IOPOLL queues (nvme.poll_queues; 0 = disabled)
nvme_poll_queues = 0

# Default block elevator: "none" (NVMe), "mq-deadline" (SATA), "bfq", "kyber", "keep"
io_scheduler = "none"

# Block writeback throttling (CONFIG_BLK_WBT)
blk_wbt = true

# Proportional cgroup I/O control (CONFIG_BLK_CGROUP_IOCOST)
iocost = false

# Filesystems to preserve as modules after pruning (e.g. ["btrfs", "xfs", "exfat", "ntfs3"])
extra_filesystems = []


# ==============================================================================
# [power] — Power Management & Idle States
# ==============================================================================
[power]
# Direct unbound workqueues to awake CPUs (CONFIG_WQ_POWER_EFFICIENT_DEFAULT)
wq_power_efficient = false

# CPU idle governor: "teo" (Timer Events Oriented), "menu", "haltpoll" (VM guests only)
cpu_idle_governor = "teo"

# Batch non-urgent RCU callbacks for up to 10s (CONFIG_RCU_LAZY; requires NOCB offloading)
rcu_lazy = false

# Energy-Aware Scheduling Energy Model (inert on symmetric x86)
energy_model = false

# System suspend to RAM/idle (CONFIG_SUSPEND)
suspend = true

# ACPI S4 hibernation (CONFIG_HIBERNATION; requires persistent disk swap and resume=)
hibernation = true

# PCIe Active State Power Management: "default", "powersave", "powersupersave", "performance"
pcie_aspm = "default"

# HD audio power saving timeout in seconds (0 = disabled)
hda_power_save = 0


# ==============================================================================
# [network] — Congestion Control & Packet Queueing
# ==============================================================================
[network]
# TCP congestion control: "bbr", "cubic", "reno"
congestion = "bbr"

# Queueing discipline: "fq" (pacing for BBR), "cake" (router AQM), "fq_codel", "fq_pie", "pfifo_fast"
qdisc = "fq"

# Multipath TCP support (CONFIG_MPTCP)
mptcp = true

# eBPF AF_XDP socket support (CONFIG_XDP_SOCKETS)
xdp = false

# Expose legacy /proc/net/nf_conntrack interface
nf_conntrack_procfs = false

# TCP Fast Open runtime hint (net.ipv4.tcp_fastopen = 3)
tcp_fastopen = true


# ==============================================================================
# [modules] — Hardware Driver Pruning & modprobed-db
# ==============================================================================
[modules]
# Driver pruning mode: "strict" (only drivers in census survive) or "expanded" (safety nets)
mode = "strict"

# Use modprobed.db census file for localmodconfig
modprobed_db = true

# Custom path to modprobed.db (e.g., imported hardware bundles)
modprobed_db_path = ""

# Allow fallback to active live lsmod if modprobed.db is missing
allow_lsmod_fallback = false

# Additional source directories to preserve during expanded pruning
lmc_keep_extra = []

# Specific Kconfig driver symbols forced to =m after pruning (e.g. ["WIREGUARD", "TUN", "VETH"])
keep_symbols = []

# Compile all surviving drivers into the core kernel binary (=y)
localyesconfig = false

# Transitional legacy flag (service manager removed in v6)
manage_service = false

# Enforce cryptographic signature on all loaded modules (CONFIG_MODULE_SIG_FORCE)
sig_force = false


# ==============================================================================
# [boot] — Command Line Delivery & Bootloader Management
# ==============================================================================
[boot]
# Delivery method for tuning parameters: "bake" (CONFIG_CMDLINE), "entry" (bootloader entry), "print"
cmdline = "bake"

# Verbatim extra parameters appended to the kernel command line
cmdline_extra = ""

# Automatically generate or update bootloader configuration entries
write_entries = true

# Disable NMI and soft watchdogs to eliminate housekeeping jitter (nowatchdog nmi_watchdog=0)
nowatchdog = true

# Apply downstream PCIe ACS override patch for VFIO IOMMU grouping (pcie_acs_override)
acs_override = false


# ==============================================================================
# [verify] — Invariant Contract Verification
# ==============================================================================
[verify]
# Strict verification: fail build (exit 4) if any required Kconfig contract symbol vanishes
strict = true

# Symbols allowed to vanish without failing the build
optional_symbols = []

# Enforce verification of CONFIG_NTSYNC when gaming.ntsync = true
require_ntsync = true

# Enforce verification of CONFIG_DEBUG_INFO_BTF when sched_ext is active
require_btf = true

# Enforce verification of CONFIG_SCHED_CLASS_EXT when an SCX daemon is selected
require_sched_ext = true
