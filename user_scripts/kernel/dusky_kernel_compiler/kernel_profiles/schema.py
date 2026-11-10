"""Profile defaults, accepted settings and wizard metadata (no build side effects)."""
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final, Literal

HZ_CHOICES: Final = (100, 250, 300, 1000)
TICKLESS_CHOICES: Final = ("periodic", "idle", "full")
PREEMPT_CHOICES: Final = ("lazy", "full", "rt")
SCHED_CHOICES: Final = ("eevdf",)
SCX_CHOICES: Final = ("none", "scx_lavd", "scx_bpfland", "scx_layered", "scx_rusty", "scx_flash", "scx_p2dq", "scx_cosmos")
CHANNEL_CHOICES: Final = ("mainline", "stable", "longterm")
LTO_CHOICES: Final = ("none", "thin", "thin_dist", "full")
OPT_CHOICES: Final = ("o2", "o3", "size")
FDO_CHOICES: Final = ("none", "autofdo", "autofdo_propeller")
THP_CHOICES: Final = ("always", "madvise", "never")
THP_DEFRAG_CHOICES: Final = ("always", "defer", "defer+madvise", "madvise", "never")
THP_SHMEM_CHOICES: Final = ("always", "within_size", "advise", "never")
SWAP_BACKEND_CHOICES: Final = ("zram", "zswap", "none")
ZRAM_ALGO_CHOICES: Final = ("zstd", "lz4", "lz4hc", "lzo-rle")
ZSWAP_COMP_CHOICES: Final = ("zstd", "lz4", "lz4hc", "lzo")
FOOTPRINT_CHOICES: Final = ("standard", "lean", "minimal", "embedded")
TRACING_CHOICES: Final = ("auto", "full", "minimal")
GOV_CHOICES: Final = ("schedutil", "performance", "powersave", "ondemand", "conservative")
EPP_CHOICES: Final = ("default", "performance", "balance_performance", "balance_power", "power")
PSTATE_CHOICES: Final = ("active", "guided", "passive", "disable", "undefined")
MITIGATION_CHOICES: Final = ("on", "off", "nosmt")
IDLE_GOV_CHOICES: Final = ("teo", "menu", "haltpoll")
PCIE_ASPM_CHOICES: Final = ("default", "powersave", "powersupersave", "performance")
CONG_CHOICES: Final = ("bbr", "cubic", "reno")
QDISC_CHOICES: Final = ("fq", "cake", "fq_codel", "fq_pie", "pfifo_fast")
TOOLCHAIN_CHOICES: Final = ("llvm", "gcc")
HEADERS_CHOICES: Final = ("auto", "always", "never")
MODULES_MODE_CHOICES: Final = ("strict", "expanded")
COMPRESS_CHOICES: Final = ("zstd", "xz", "gzip", "none")
DEBUG_CHOICES: Final = ("none", "reduced", "full")
SECURITY_PROFILES: Final = ("balanced", "extreme", "hardened")
STACKPROTECTOR_CHOICES: Final = ("strong", "regular", "none")
IOSCHED_CHOICES: Final = ("none", "mq-deadline", "bfq", "kyber", "keep")
SEED_CHOICES: Final = ("auto", "snapshot", "arch", "running", "headers", "defconfig")
CMDLINE_CHOICES: Final = ("bake", "entry", "print")

# Per-choice context shown by the wizard (section, key) -> {value: explanation}
CHOICE_HELP: Final[dict[tuple[str, str], dict[str, str]]] = {
    ("release", "channel"): {
        "mainline": "Linus' tree: newest features and release candidates when allow_rc=true",
        "stable": "latest stable point release -- recommended for daily drivers",
        "longterm": "LTS branch (still subject to the >= 7.3 floor)",
    },
    ("cpu", "arch"): {
        "native": "-march=native via CONFIG_X86_NATIVE_CPU: fastest, host-only, never portable",
        "generic": "x86-64 baseline (psABI v1): maximally portable",
        "generic_v2": "x86-64-v2: SSE4.2/POPCNT/CX16 (Nehalem+, Bulldozer+)",
        "generic_v3": "x86-64-v3: AVX2/BMI2/FMA/MOVBE (Haswell+, Zen+) -- best portable choice",
        "generic_v4": "x86-64-v4: AVX-512 -- the kernel never emits AVX-512, so this equals v3 for kernel code",
    },
    ("cpu", "governor"): {
        "schedutil": "utilization-driven, integrates with EEVDF/sched_ext (default)",
        "performance": "pin maximum frequency (desktops on wall power)",
        "powersave": "with intel_pstate/amd_pstate=active this is the HWP-driven default and is not slow",
        "ondemand": "legacy sampling governor (only meaningful with acpi-cpufreq)",
        "conservative": "legacy sampling governor with slow ramp (acpi-cpufreq only)",
    },
    ("cpu", "amd_pstate"): {
        "active": "EPP autonomous mode: firmware picks frequency, kernel provides hints (Zen 2+ best)",
        "guided": "kernel provides min/max band, firmware selects inside it",
        "passive": "kernel-driven target frequency (CPPC, non-autonomous)",
        "disable": "fall back to acpi-cpufreq",
        "undefined": "leave the Kconfig default (3 = active)",
    },
    ("cpu", "epp"): {
        "default": "do not touch the firmware EPP hint",
        "performance": "EPP 0: maximum performance bias",
        "balance_performance": "EPP 128: desktop default bias",
        "balance_power": "EPP 192: efficiency bias",
        "power": "EPP 255: maximum efficiency bias (battery)",
    },
    ("cpu", "mitigations"): {
        "on": "build and enable CPU vulnerability mitigations (safe default)",
        "off": "compile out CONFIG_CPU_MITIGATIONS and boot with mitigations=off (faster syscalls/IO; trusted single-user machines only)",
        "nosmt": "keep mitigations, boot with mitigations=auto,nosmt (disables SMT siblings)",
    },
    ("scheduler", "type"): {
        "eevdf": "upstream EEVDF (7.x default) -- required for sched_ext BPF classes",
    },
    ("scheduler", "scx"): {
        "none": "no BPF scheduler daemon (sched_ext class may still be compiled)",
        "scx_lavd": "latency-criticality aware (gaming/handheld); flags: --autopilot or --autopower",
        "scx_bpfland": "interactive-first vruntime scheduler; '-m performance' for gaming",
        "scx_layered": "config-driven layered scheduler (workstations, mixed workloads)",
        "scx_rusty": "multi-domain hybrid user/BPF (servers, large NUMA)",
        "scx_flash": "EDF-style fairness with predictable latency (audio, soft-RT)",
        "scx_p2dq": "pick-2 dispatch queues, LLC-aware, simple and robust",
        "scx_cosmos": "lightweight LLC/hybrid-topology aware scheduler",
    },
    ("timing", "tickless"): {
        "periodic": "always tick (legacy; only for debugging)",
        "idle": "NO_HZ_IDLE: stop ticks on idle CPUs (desktop/laptop default)",
        "full": "NO_HZ_FULL: adaptive ticks -- only useful with nohz_full=/isolcpus= CPU isolation (RT/HPC)",
    },
    ("timing", "preempt"): {
        "lazy": "PREEMPT_LAZY: full-preempt responsiveness with voluntary-like throughput (7.x desktop default)",
        "full": "PREEMPT: lowest scheduling latency",
        "rt": "PREEMPT_RT: hard real-time; disables PREEMPT_DYNAMIC and costs throughput",
    },
    ("memory", "thp"): {
        "always": "THP for all anonymous memory (fastest for games/JVMs; more RSS, khugepaged activity)",
        "madvise": "THP only where applications ask (balanced default)",
        "never": "disable automatic THP allocation; retain support required by in-tree GPU helpers",
    },
    ("memory", "swap_backend"): {
        "zram": "compressed RAM swap; retain an existing manager or create swap with the packaged runtime",
        "zswap": "compressed cache in front of an existing disk swap device",
        "none": "no compressed swap layer (zswap disabled)",
    },
    ("memory", "zram_algo"): {
        "zstd": "best ratio (~3-4x), higher CPU cost",
        "lz4": "fastest, lower ratio (~2-2.5x); pair with zstd recompression",
        "lz4hc": "lz4 high-compression variant (slow compress, fast decompress)",
        "lzo-rle": "kernel default; balanced",
    },
    ("memory", "footprint"): {
        "standard": "distribution-like feature set",
        "lean": "<= 8 GiB: drop debug/tracing/legacy cgroup v1/kexec/kcore, smaller log buffer",
        "minimal": "smaller core buffers/debug surface; allocator, hugepages and DAMON remain separate choices",
        "embedded": "<= 4 GiB headless/appliance: minimal + BASE_SMALL, no 32-bit compat, no hibernation",
    },
    ("memory", "tracing"): {
        "auto": "full tracing when a sched_ext daemon is selected, minimal otherwise",
        "full": "ftrace + kprobes + uprobes + BPF events (perf, bpftrace, scx tooling)",
        "minimal": "tracepoints + BPF syscall only; no function tracer/kprobes (smaller text, fewer pages)",
    },
    ("compiler", "toolchain"): {
        "llvm": "clang/lld: LTO, kCFI, AutoFDO/Propeller, ThinLTO cache",
        "gcc": "GCC + ld.bfd: no LTO/kCFI/FDO paths",
    },
    ("compiler", "optimize"): {
        "o2": "-O2: upstream default, best tested",
        "o3": "-O3: aggressive instruction scheduling, loop pipelining, and x86 SIMD safety guards",
        "size": "-Os: prioritize code size; workload-dependent speed tradeoff",
    },
    ("compiler", "lto"): {
        "none": "no link-time optimization (fastest builds, compatible with Rust+BTF)",
        "thin": "ThinLTO: parallel, optional persistent LLD cache",
        "thin_dist": "Distributed ThinLTO: upstream parallel backend jobs with incremental native objects",
        "full": "monolithic LTO: expensive final link; performance benefit is workload-dependent",
    },
    ("compiler", "fdo"): {
        "none": "no profile-guided optimization",
        "autofdo": "CONFIG_AUTOFDO_CLANG with a perf-derived .afdo profile",
        "autofdo_propeller": "AutoFDO + Propeller basic-block layout (needs create_llvm_prof profiles)",
    },
    ("compiler", "debug_info"): {
        "none": "DEBUG_INFO_NONE (forced to DWARF5 when BTF is required by sched_ext/BPF)",
        "reduced": "reduced DWARF5; promoted to full debug types when BTF is required",
        "full": "full DWARF5 (largest build tree)",
    },
    ("security", "profile"): {
        "balanced": "Arch-like hardening: usercopy checks, init_on_alloc, freelist hardening, UBSAN bounds",
        "extreme": "performance over hardening: disables most runtime checks (requires acknowledge_risk)",
        "hardened": "KSPP-style: adds init_on_free, kCFI, random kmalloc caches, strict IOMMU, lockdown early",
    },
    ("storage", "io_scheduler"): {
        "none": "no scheduler for NVMe (lowest overhead)",
        "mq-deadline": "deadline-based, good for SATA SSDs",
        "bfq": "fairness/latency oriented, best for rotational disks",
        "kyber": "token-based low-latency scheduler for fast SSDs",
        "keep": "do not install I/O scheduler udev rules",
    },
    ("power", "cpu_idle_governor"): {
        "teo": "timer-events oriented: best for tickless desktops/laptops",
        "menu": "classic predictive governor",
        "haltpoll": "for KVM guests (polls before halting)",
    },
    ("network", "congestion"): {"bbr": "model-based, best for internet links", "cubic": "loss-based upstream default", "reno": "classic"},
    ("network", "qdisc"): {
        "fq": "fair queue pacing (pairs with BBR)", "cake": "bufferbloat killer for home links (runtime sysctl; Kconfig falls back to fq_codel)",
        "fq_codel": "upstream default", "fq_pie": "PIE-based AQM", "pfifo_fast": "legacy FIFO",
    },
    ("modules", "mode"): {
        "strict": "hardware-only via modprobed.db (LSMOD): tiny module set, tiny memory",
        "expanded": "modprobed.db + LMC_KEEP safety net (USB/GPU/net/HID/fs stay available)",
    },
    ("compiler", "headers"): {
        "auto": "build -headers when target DKMS modules are installed (nvidia, zfs, v4l2loopback...)",
        "always": "always build the -headers package",
        "never": "never build headers (enables TRIM_UNUSED_KSYMS eligibility)",
    },
    ("dusky", "seed"): {
        "auto": "Arch complete config -> running -> headers -> snapshot -> defconfig",
        "snapshot": "only the saved snapshot for this profile",
        "arch": "Arch Linux packaging config (gitlab.archlinux.org)",
        "running": "/proc/config.gz of the running kernel",
        "headers": "/usr/lib/modules/$(uname -r)/build/.config",
        "defconfig": "make defconfig (last resort; not desktop-complete)",
    },
    ("boot", "cmdline"): {
        "bake": "compile flavor tuning into CONFIG_CMDLINE (bootloader options still win; per-kernel)",
        "entry": "write tuning into the systemd-boot entry for this flavor only",
        "print": "only print the recommended command line",
    },
}


# ---------------------------------------------------------------------------------------------------
# Profile schema
# ---------------------------------------------------------------------------------------------------
type FieldKind = Literal["str", "int", "bool", "list", "table"]


@dataclass(frozen=True, slots=True)
class FieldSpec:
    key: str
    kind: FieldKind
    default: Any
    help: str
    choices: tuple[Any, ...] | None = None
    required: bool = False
    wizard: bool = True
    minimum: int | None = None
    maximum: int | None = None


def F(key: str, kind: FieldKind, default: Any, help: str, choices: Iterable[Any] | None = None, *,
      required: bool = False, wizard: bool = True, minimum: int | None = None, maximum: int | None = None) -> FieldSpec:
    return FieldSpec(key, kind, default, help, tuple(choices) if choices else None, required, wizard, minimum, maximum)


PROFILE_SPEC: Final[dict[str, tuple[FieldSpec, ...]]] = {
    "meta": (
        F("name", "str", "", "Profile id (matches --profile)", required=True, wizard=False),
        F("description", "str", "", "One-line summary", wizard=False),
        F("suffix", "str", "", "LOCALVERSION suffix and pkgbase tail: linux-<suffix>", required=True, wizard=False),
        F("priority", "int", 50, "Sort order in pickers", wizard=False),
        F("tags", "list", [], "Free-form labels", wizard=False),
        F("bare_metal_only", "bool", False, "Refuse to build inside a VM and strip guest paravirt code"),
        F("portable_package", "bool", False, "Package targets another machine (forbids -march=native)"),
        F("manifest_path", "str", "", "Path to target hardware manifest.json for remote builds", wizard=False),
    ),
    "release": (
        F("channel", "str", "stable", "Preferred upstream release channel for the interactive picker", CHANNEL_CHOICES),
        F("pin", "str", "", "Exact unattended version; preselected in the interactive picker (e.g. 7.3.1 or 7.3-rc5)"),
        F("allow_rc", "bool", False, "Allow -rc as the automatic mainline choice; interactive selection can override"),
        F("min_version", "str", "7.3-rc1", "Hard floor; anything older is rejected", wizard=False),
        F("require_signature", "bool", True, "Require PGP or SHA256 verification of release tarballs"),
    ),
    "scheduler": (
        F("type", "str", "eevdf", "Stock upstream EEVDF scheduler", SCHED_CHOICES, wizard=False),
        F("scx", "str", "none", "sched_ext BPF scheduler executable (or none)"),
        F("scx_flags", "str", "", "Flags passed to the scx daemon (e.g. --autopilot)"),
        F("scx_enable_class", "bool", False, "Compile CONFIG_SCHED_CLASS_EXT (+BTF/BPF JIT)"),
        F("autogroup", "bool", True, "SCHED_AUTOGROUP (per-session fairness)"),
        F("rt_group", "bool", False, "RT_GROUP_SCHED bandwidth control"),
        F("sched_core", "bool", False, "SCHED_CORE core scheduling (SMT side-channel isolation; overhead)"),
    ),
    "cache": (
        F("sched_cache", "bool", True, "CONFIG_SCHED_CACHE Cache-Aware Scheduling (LLC affinity)"),
        F("llc_aggr_tolerance", "int", 1, "LLC aggregation tolerance written to debugfs at boot", minimum=0, maximum=100),
        F("llc_overaggr_pct", "int", -1, "LLC over-aggregation percentage (-1 = kernel default)", minimum=-1, maximum=100),
    ),
    "rseq": (
        F("slice_extension", "bool", True, "RSEQ time-slice extension (CONFIG_RSEQ_SLICE_EXTENSION)"),
        F("slice_ext_nsec", "int", 10000, "Requested slice extension in nanoseconds", minimum=5000, maximum=50000),
    ),
    "dusky": (
        F("enhanced", "bool", False, "Desktop heuristics (nowatchdog, faster fbcon takeover)"),
        F("hostname", "str", "", "KBUILD_BUILD_HOST (empty = system hostname)", wizard=False),
        F("user", "str", "", "KBUILD_BUILD_USER (empty = dynamic active user)", wizard=False),
        F("reproducible", "bool", True, "Fixed KBUILD_BUILD_TIMESTAMP / SOURCE_DATE_EPOCH", wizard=False),
        F("seed", "str", "auto", "Seed .config source", SEED_CHOICES),
        F("extra_config", "table", {}, "Arbitrary Kconfig overrides: SYMBOL = true|false|\"m\"|int|\"string\""),
    ),
    "cpu": (
        F("arch", "str", "native", "Target micro-architecture"),
        F("march", "str", "", "Extra -march/-mtune override appended to KCFLAGS", wizard=False),
        F("governor", "str", "schedutil", "Default cpufreq governor", GOV_CHOICES),
        F("amd_pstate", "str", "active", "AMD P-State operation mode", PSTATE_CHOICES),
        F("epp", "str", "balance_performance", "Energy Performance Preference hint applied at boot", EPP_CHOICES),
        F("mitigations", "str", "on", "Speculative-execution mitigations", MITIGATION_CHOICES),
        F("nr_cpus", "int", 0, "CONFIG_NR_CPUS (0 = target possible-CPU slots, minimum 2)", minimum=0, maximum=8192),
        F("smt", "bool", True, "Keep SMT/Hyper-Threading enabled"),
        F("mce", "bool", True, "Machine Check Exception handling"),
        F("prefcore", "bool", True, "AMD preferred-core / Intel ITMT priority (SCHED_MC_PRIO)"),
        F("compat32", "bool", True, "IA32_EMULATION (Steam, Wine, 32-bit binaries)"),
    ),
    "timing": (
        F("hz", "int", 1000, "Timer tick frequency", HZ_CHOICES),
        F("tickless", "str", "idle", "Tickless mode", TICKLESS_CHOICES),
        F("preempt", "str", "lazy", "Preemption model", PREEMPT_CHOICES),
        F("preempt_dynamic", "bool", True, "PREEMPT_DYNAMIC (preempt= boot switch)"),
    ),
    "memory": (
        F("footprint", "str", "lean", "Memory footprint tier (bundles many small Kconfig cuts)", FOOTPRINT_CHOICES),
        F("thp", "str", "madvise", "Transparent Hugepages mode", THP_CHOICES),
        F("thp_defrag", "str", "defer+madvise", "THP defrag strategy", THP_DEFRAG_CHOICES),
        F("thp_shmem", "str", "never", "THP for shmem/tmpfs", THP_SHMEM_CHOICES),
        F("mglru", "bool", True, "Multi-Gen LRU"),
        F("mglru_mask", "int", 7, "lru_gen/enabled bitmask", minimum=0, maximum=7),
        F("mglru_min_ttl_ms", "int", 1000, "lru_gen/min_ttl_ms anti-thrash threshold", minimum=0, maximum=60000),
        F("swap_backend", "str", "zram", "Compressed swap backend", SWAP_BACKEND_CHOICES),
        F("zram_algo", "str", "zstd", "Primary ZRAM compressor", ZRAM_ALGO_CHOICES),
        F("zram_recomp_algo", "str", "zstd", "ZRAM recompression algorithm for idle pages (multi-comp)", ZRAM_ALGO_CHOICES),
        F("zram_size_pct", "int", 100, "ZRAM size as percent of RAM", minimum=10, maximum=400),
        F("zram_multi_comp", "bool", True, "CONFIG_ZRAM_MULTI_COMP (multi-algorithm swap compression in kernel)"),
        F("zswap_compressor", "str", "zstd", "zswap compressor", ZSWAP_COMP_CHOICES),
        F("zswap_max_pool_pct", "int", 25, "zswap.max_pool_percent", minimum=5, maximum=80),
        F("swappiness", "int", 0, "vm.swappiness (0 = auto: 180 zram, 100 zswap, 60 none)", minimum=0, maximum=200),
        F("vfs_cache_pressure", "int", 0, "vm.vfs_cache_pressure (0 = auto by footprint)", minimum=0, maximum=1000),
        F("watermark_scale_factor", "int", 125, "vm.watermark_scale_factor", minimum=10, maximum=3000),
        F("watermark_boost_factor", "int", 0, "vm.watermark_boost_factor (0 recommended with zram)", minimum=0, maximum=30000),
        F("compaction_proactiveness", "int", 0, "vm.compaction_proactiveness (0 disables proactive compaction)", minimum=0, maximum=100),
        F("dirty_bytes_mb", "int", 0, "vm.dirty_bytes in MiB (0 = kernel ratio defaults)", minimum=0, maximum=65536),
        F("slub_tiny", "bool", False, "CONFIG_SLUB_TINY minimal allocator (sacrifices SMP scalability)"),
        F("slab_buckets", "bool", False, "CONFIG_SLAB_BUCKETS hardening buckets (slightly more memory)"),
        F("per_vma_lock", "bool", True, "CONFIG_PER_VMA_LOCK (removed in Linux 7.4; kept for profile compatibility)"),
        F("numa", "bool", True, "NUMA topology support"),
        F("numa_balancing", "bool", False, "Automatic NUMA balancing"),
        F("nodes_shift", "int", 0, "CONFIG_NODES_SHIFT (0 = target NUMA topology)", minimum=0, maximum=10),
        F("ksm", "bool", True, "Kernel Samepage Merging support"),
        F("ksm_run", "bool", False, "Activate ksmd at boot (only merges MADV_MERGEABLE/prctl opted-in memory)"),
        F("damon", "bool", False, "DAMON monitoring + DAMON_RECLAIM proactive reclaim"),
        F("page_reporting", "bool", False, "Free page reporting to the hypervisor (VM guests)"),
        F("hugetlbfs", "bool", True, "hugetlbfs / HugeTLB pages"),
        F("kallsyms_all", "bool", True, "KALLSYMS_ALL (all symbols incl. data; ~1-2 MiB)"),
        F("memcg", "bool", True, "cgroup v2 memory controller (systemd MemoryMax, oomd)"),
        F("base_small", "bool", False, "BASE_SMALL: shrink core hash tables (embedded)"),
        F("log_buf_shift", "int", 0, "CONFIG_LOG_BUF_SHIFT (0 = 17 standard / 16 lean / 15 minimal)", minimum=0, maximum=25),
        F("tracing", "str", "auto", "ftrace/kprobes/uprobes surface", TRACING_CHOICES),
        F("kexec", "bool", True, "kexec + crash dump support"),
        F("ikconfig", "bool", True, "Embed .config (/proc/config.gz)"),
        F("trim_unused_ksyms", "bool", False, "TRIM_UNUSED_KSYMS (breaks out-of-tree modules; only with headers=never)"),
        F("dead_code_elimination", "bool", False, "LD_DEAD_CODE_DATA_ELIMINATION (inert on upstream x86-64)"),
    ),
    "compiler": (
        F("toolchain", "str", "llvm", "Toolchain", TOOLCHAIN_CHOICES),
        F("optimize", "str", "o2", "Optimization level", OPT_CHOICES),
        F("lto", "str", "thin", "Link-time optimization", LTO_CHOICES),
        F("thinlto_cache", "bool", True, "Persist the ThinLTO cache across builds"),
        F("thinlto_cache_size_gb", "int", 20, "Prune the ThinLTO cache above this size", minimum=1, maximum=500),
        F("fdo", "str", "none", "Feedback-directed optimization", FDO_CHOICES),
        F("fdo_profile_dir", "str", "", "Directory holding kernel.afdo / propeller_* profiles"),
        F("kcfi", "bool", False, "Clang kCFI (CONFIG_CFI) + FineIBT auto"),
        F("debug_info", "str", "reduced", "Debug info level", DEBUG_CHOICES),
        F("module_compress", "str", "zstd", "Module compression", COMPRESS_CHOICES),
        F("rust", "bool", True, "Rust support (auto-disabled when LTO + BTF are both required)"),
        F("jobs", "int", 0, "Parallel jobs (0 = auto from CPU threads and RAM)", minimum=0, maximum=1024),
        F("headers", "str", "auto", "Headers package policy", HEADERS_CHOICES),
        F("modversions", "bool", False, "MODVERSIONS symbol CRCs (needs GENDWARFKSYMS with Rust)"),
        F("ccache", "bool", True, "Enable ccache compiler cache if available on the host"),
    ),
    "security": (
        F("profile", "str", "balanced", "Hardening bundle", SECURITY_PROFILES),
        F("init_on_alloc", "bool", True, "Zero memory on allocation"),
        F("init_on_free", "bool", False, "Zero memory on free (expensive)"),
        F("hardened_usercopy", "bool", True, "Hardened usercopy bounds checks"),
        F("stackprotector", "str", "strong", "Stack protector", STACKPROTECTOR_CHOICES),
        F("slab_freelist_hardened", "bool", True, "SLAB freelist pointer obfuscation"),
        F("slab_freelist_random", "bool", True, "Randomized freelists"),
        F("randomize_kstack", "bool", True, "Randomize kernel stack offset per syscall"),
        F("ubsan_bounds", "bool", True, "UBSAN array-bounds instrumentation"),
        F("apparmor", "bool", False, "Build AppArmor and place it in the default LSM order"),
        F("selinux", "bool", False, "Build SELinux (kept out of the default LSM order)"),
        F("lockdown_early", "bool", False, "SECURITY_LOCKDOWN_LSM_EARLY"),
        F("acknowledge_risk", "bool", False, "Acknowledge extreme profile / mitigations=off risks"),
    ),
    "gaming": (
        F("ntsync", "bool", True, "In-tree NTSync driver (CONFIG_NTSYNC=m)"),
        F("uclamp", "bool", True, "UCLAMP_TASK utilization clamping"),
        F("max_map_count", "int", 2147483642, "vm.max_map_count", minimum=65530, maximum=2147483642),
        F("split_lock_mitigate", "bool", False, "Split-lock detection penalty (off = better emulator/game frametimes)"),
        F("controllers", "bool", False, "Keep controller/HID drivers (xpad, playstation, nintendo, steam, uinput)"),
    ),
    "storage": (
        F("nvme_poll_queues", "int", 0, "nvme.poll_queues (IOPOLL) count", minimum=0, maximum=128),
        F("io_scheduler", "str", "none", "NVMe I/O scheduler udev default", IOSCHED_CHOICES),
        F("blk_wbt", "bool", True, "Block writeback throttling"),
        F("iocost", "bool", False, "BLK_CGROUP_IOCOST proportional I/O control"),
        F("extra_filesystems", "list", [], "Additional filesystems to keep as modules (e.g. xfs, f2fs)"),
    ),
    "power": (
        F("wq_power_efficient", "bool", False, "Power-efficient unbound workqueues"),
        F("cpu_idle_governor", "str", "teo", "cpuidle governor", IDLE_GOV_CHOICES),
        F("rcu_lazy", "bool", False, "RCU lazy callbacks on all CPUs (battery)"),
        F("energy_model", "bool", False, "Energy model / EAS"),
        F("suspend", "bool", True, "Suspend-to-idle/RAM"),
        F("hibernation_compress", "str", "lz4", "Hibernation compression codec", ("lzo", "lz4")),
        F("hibernation", "bool", True, "Hibernation (codec selected by hibernation_compress)"),
        F("pcie_aspm", "str", "default", "Default PCIe ASPM policy", PCIE_ASPM_CHOICES),
        F("hda_power_save", "int", 0, "SND_HDA_POWER_SAVE_DEFAULT seconds (0 = off)", minimum=0, maximum=3600),
    ),
    "network": (
        F("congestion", "str", "bbr", "TCP congestion control", CONG_CHOICES),
        F("qdisc", "str", "fq", "Root queueing discipline", QDISC_CHOICES),
        F("mptcp", "bool", True, "Multipath TCP"),
        F("xdp", "bool", False, "AF_XDP sockets"),
        F("nf_conntrack_procfs", "bool", False, "Legacy /proc/net/nf_conntrack"),
        F("tcp_fastopen", "bool", True, "net.ipv4.tcp_fastopen=3"),
    ),
    "modules": (
        F("mode", "str", "strict", "Pruning mode", MODULES_MODE_CHOICES),
        F("modprobed_db", "bool", True, "Use modprobed.db as LSMOD"),
        F("modprobed_db_path", "str", "", "Custom modprobed.db path (imported bundles)"),
        F("allow_lsmod_fallback", "bool", False, "Strict mode may fall back to the live lsmod set"),
        F("lmc_keep_extra", "list", [], "Extra LMC_KEEP paths (expanded mode)"),
        F("keep_symbols", "list", [], "Kconfig symbols forced to =m after pruning (e.g. WIREGUARD, TUN)"),
        F("localyesconfig", "bool", False, "Build pruned modules into the image (localyesconfig)"),
        F("sig_force", "bool", False, "MODULE_SIG_FORCE (auto-generated key)"),
    ),
    "boot": (
        F("cmdline", "str", "bake", "How flavor tuning reaches the kernel command line", CMDLINE_CHOICES),
        F("cmdline_extra", "str", "", "Extra kernel parameters appended to the flavor tuning"),
        F("write_entries", "bool", True, "Write/refresh systemd-boot entries for this flavor"),
        F("set_default", "bool", False, "Make this flavor the systemd-boot default"),
        F("nowatchdog", "bool", True, "Disable NMI/soft watchdog (nowatchdog nmi_watchdog=0)"),
    ),
    "runtime": (
        F("enabled", "bool", True, "Package a kernel-specific one-shot boot tuning service"),
        F("manage_zram", "bool", True, "Create ZRAM swap only when no ZRAM swap is already active"),
    ),
    "verify": (
        F("strict", "bool", True, "Hard-fail when a non-optional Kconfig contract entry is unmet"),
        F("optional_symbols", "list", [], "Extra symbols treated as soft in the contract", wizard=False),
        F("require_ntsync", "bool", True, "Require NTSync when gaming.ntsync=true"),
        F("require_btf", "bool", True, "Require BTF whenever sched_ext is compiled"),
        F("require_sched_ext", "bool", True, "Require SCHED_CLASS_EXT when scx daemon != none"),
    ),
}


@dataclass(frozen=True, slots=True)
class WizardStep:
    title: str
    groups: tuple[tuple[str, tuple[str, ...]], ...]


WIZARD_STEPS: Final[tuple[WizardStep, ...]] = (
    WizardStep("Release", (("release", ("channel", "pin", "allow_rc", "require_signature")),)),
    WizardStep("CPU", (("cpu", ("arch", "governor", "amd_pstate", "epp", "mitigations", "nr_cpus", "smt", "prefcore", "compat32", "mce")),)),
    WizardStep("Scheduler", (("scheduler", ("type", "scx", "scx_flags", "scx_enable_class", "autogroup", "rt_group", "sched_core")),
                             ("cache", ("sched_cache", "llc_aggr_tolerance", "llc_overaggr_pct")),
                             ("rseq", ("slice_extension", "slice_ext_nsec")))),
    WizardStep("Timing", (("timing", ("hz", "tickless", "preempt", "preempt_dynamic")),)),
    WizardStep("Memory & Low-RAM", (("memory", ("footprint", "thp", "thp_defrag", "thp_shmem", "mglru", "mglru_mask", "mglru_min_ttl_ms", "swap_backend",
                                                "zram_algo", "zram_recomp_algo", "zram_size_pct", "zram_multi_comp", "zswap_compressor", "zswap_max_pool_pct",
                                                "swappiness", "vfs_cache_pressure", "watermark_scale_factor", "watermark_boost_factor", "compaction_proactiveness",
                                                "dirty_bytes_mb", "slub_tiny", "slab_buckets", "per_vma_lock", "numa", "numa_balancing", "nodes_shift", "ksm", "ksm_run",
                                                "damon", "page_reporting", "hugetlbfs", "kallsyms_all", "memcg", "base_small", "log_buf_shift", "tracing", "kexec",
                                                "ikconfig", "trim_unused_ksyms", "dead_code_elimination")),)),
    WizardStep("Compiler & Toolchain", (("compiler", ("toolchain", "optimize", "lto", "thinlto_cache", "thinlto_cache_size_gb", "kcfi", "fdo", "fdo_profile_dir",
                                                       "debug_info", "module_compress", "rust", "jobs", "modversions")),
                                        ("dusky", ("seed", "enhanced", "extra_config")))),
    WizardStep("Security", (("security", ("profile", "init_on_alloc", "init_on_free", "hardened_usercopy", "stackprotector", "slab_freelist_hardened",
                                          "slab_freelist_random", "randomize_kstack", "ubsan_bounds", "apparmor", "selinux", "lockdown_early", "acknowledge_risk")),)),
    WizardStep("Gaming / Low-Latency", (("gaming", ("ntsync", "uclamp", "max_map_count", "split_lock_mitigate", "controllers")),)),
    WizardStep("Storage & Power", (("storage", ("nvme_poll_queues", "io_scheduler", "blk_wbt", "iocost", "extra_filesystems")),
                                   ("power", ("cpu_idle_governor", "rcu_lazy", "energy_model", "wq_power_efficient", "suspend", "hibernation", "hibernation_compress", "pcie_aspm", "hda_power_save")))),
    WizardStep("Network", (("network", ("congestion", "qdisc", "mptcp", "xdp", "nf_conntrack_procfs", "tcp_fastopen")),)),
    WizardStep("Runtime tuning", (("runtime", ("enabled", "manage_zram")),)),
    WizardStep("Modules, Headers & Boot", (("modules", ("mode", "modprobed_db", "modprobed_db_path", "allow_lsmod_fallback", "lmc_keep_extra", "keep_symbols",
                                                        "localyesconfig", "sig_force")),
                                           ("compiler", ("headers",)),
                                           ("boot", ("cmdline", "cmdline_extra", "write_entries", "nowatchdog", "set_default")),
                                           ("meta", ("bare_metal_only", "portable_package")),
                                           ("verify", ("strict", "require_ntsync", "require_btf", "require_sched_ext")))),
)

# ---------------------------------------------------------------------------------------------------
# Profile model
# ---------------------------------------------------------------------------------------------------
SECURITY_BUNDLES: Final[dict[str, dict[str, Any]]] = {
    "balanced": {"init_on_alloc": True, "init_on_free": False, "hardened_usercopy": True, "stackprotector": "strong",
                 "slab_freelist_hardened": True, "slab_freelist_random": True, "randomize_kstack": True, "ubsan_bounds": True, "lockdown_early": False},
    "extreme": {"init_on_alloc": False, "init_on_free": False, "hardened_usercopy": False, "stackprotector": "regular",
                "slab_freelist_hardened": False, "slab_freelist_random": False, "randomize_kstack": False, "ubsan_bounds": False, "lockdown_early": False},
    "hardened": {"init_on_alloc": True, "init_on_free": True, "hardened_usercopy": True, "stackprotector": "strong",
                 "slab_freelist_hardened": True, "slab_freelist_random": True, "randomize_kstack": True, "ubsan_bounds": True, "lockdown_early": True},
}
FOOTPRINT_RANK: Final = {name: i for i, name in enumerate(FOOTPRINT_CHOICES)}
