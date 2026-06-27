#!/usr/bin/env python3
"""memtune — master ZRAM / VM / THP / MGLRU / network tuning orchestrator for Arch Linux (kernel 7.0+, systemd 260+, Python 3.14+)."""

from __future__ import annotations

import argparse
import dataclasses
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

REQUIRED_PY = (3, 14)
RAM_DEMARCATION_GB = 30.0
ZRAM_SWAP_PRIORITY = 100

SYSCTL_FILE = Path("/etc/sysctl.d/99-memtune.conf")
TMPFILES_FILE = Path("/etc/tmpfiles.d/99-memtune.conf")
ZRAM_CONF_FILE = Path("/etc/systemd/zram-generator.conf.d/99-memtune.conf")
JOURNALD_DROPIN = Path("/etc/systemd/journald.conf.d/99-memtune.conf")
OOMD_DROPIN = Path("/etc/systemd/oomd.conf.d/99-memtune.conf")
WB_SERVICE = Path("/etc/systemd/system/memtune-zram-writeback.service")
WB_TIMER = Path("/etc/systemd/system/memtune-zram-writeback.timer")
SCRATCH_DIR = Path("/mnt/zram1")
SCRATCH_UNIT = "mnt-zram1.mount"
SCRATCH_UNIT_FILE = Path("/etc/systemd/system") / SCRATCH_UNIT
KERNEL_CMDLINE = Path("/etc/kernel/cmdline")

THP_DIR = Path("/sys/kernel/mm/transparent_hugepage")
MGLRU_DIR = Path("/sys/kernel/mm/lru_gen")
ZSWAP_PARAM = Path("/sys/module/zswap/parameters/enabled")
ZRAM_GENERATOR = Path("/usr/lib/systemd/system-generators/zram-generator")


# ──────────────────────────── presentation ────────────────────────────

class C:
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[1;31m"
    GRN = "\033[1;32m"
    YLW = "\033[1;33m"
    BLU = "\033[1;34m"
    MAG = "\033[1;35m"
    CYN = "\033[1;36m"
    RST = "\033[0m"

    @classmethod
    def strip(cls) -> None:
        for name in ("BOLD", "DIM", "RED", "GRN", "YLW", "BLU", "MAG", "CYN", "RST"):
            setattr(cls, name, "")


QUIET = False


def info(msg: str) -> None:
    if not QUIET:
        print(f"{C.BLU}[INFO]{C.RST} {msg}")


def ok(msg: str) -> None:
    if not QUIET:
        print(f"{C.GRN}[ OK ]{C.RST} {msg}")


def warn(msg: str) -> None:
    print(f"{C.YLW}[WARN]{C.RST} {msg}")


def err(msg: str) -> None:
    print(f"{C.RED}[FAIL]{C.RST} {msg}", file=sys.stderr)


def die(msg: str, code: int = 1) -> NoReturn:
    err(msg)
    sys.exit(code)


def panel(title: str) -> None:
    pad = "─" * max(0, 66 - len(title))
    print(f"\n{C.BOLD}── {title} {pad}{C.RST}")


def run(*argv: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), text=True, capture_output=capture)


# ──────────────────────────── profiles ────────────────────────────

@dataclass(frozen=True, slots=True)
class Profile:
    key: str
    label: str
    blurb: str
    swappiness: int
    vfs_cache_pressure: int
    watermark_scale_factor: int
    dirty_bytes: int
    dirty_background_bytes: int
    stat_interval: int
    tcp_rmem: str
    tcp_wmem: str
    rwmem_max: int
    netdev_backlog: int
    mglru_min_ttl_ms: int
    thp_enabled: str
    thp_defrag: str
    thp_shmem: str
    thp_shrink_underused: int
    khugepaged_max_ptes_none: int
    khugepaged_scan_sleep_ms: int
    khugepaged_pages_to_scan: int
    mthp_anon: dict[int, str]
    mthp_shmem: dict[int, str]
    zram_size: str
    zram_algorithm: str
    journald_system_max: str
    journald_runtime_max: str


_SHMEM_MAP = {64: "inherit", 128: "inherit", 2048: "inherit"}

PROFILES: dict[str, Profile] = {
    "savings": Profile(
        key="savings",
        label="Strict Savings (<30 GB)",
        blurb="Absolute minimum RAM footprint: aggressive zstd ZRAM, tight THP, high VFS pressure.",
        swappiness=190,
        vfs_cache_pressure=200,
        watermark_scale_factor=50,
        dirty_bytes=134_217_728,
        dirty_background_bytes=33_554_432,
        stat_interval=10,
        tcp_rmem="4096 65536 4194304",
        tcp_wmem="4096 65536 4194304",
        rwmem_max=4_194_304,
        netdev_backlog=1000,
        mglru_min_ttl_ms=2000,
        thp_enabled="madvise",
        thp_defrag="defer+madvise",
        thp_shmem="within_size",
        thp_shrink_underused=1,
        khugepaged_max_ptes_none=180,
        khugepaged_scan_sleep_ms=60_000,
        khugepaged_pages_to_scan=4096,
        mthp_anon={64: "madvise", 128: "madvise", 2048: "madvise"},
        mthp_shmem=_SHMEM_MAP,
        zram_size="ram",
        zram_algorithm="zstd",
        journald_system_max="256M",
        journald_runtime_max="32M",
    ),
    "performance": Profile(
        key="performance",
        label="Max Performance (≥30 GB)",
        blurb="Liberal RAM usage: lz4 latency, 64k/128k mTHP always-on, fat dirty cache, big net buffers.",
        swappiness=150,
        vfs_cache_pressure=100,
        watermark_scale_factor=100,
        dirty_bytes=1_073_741_824,
        dirty_background_bytes=268_435_456,
        stat_interval=1,
        tcp_rmem="4096 262144 16777216",
        tcp_wmem="4096 262144 16777216",
        rwmem_max=16_777_216,
        netdev_backlog=4096,
        mglru_min_ttl_ms=3000,
        thp_enabled="madvise",
        thp_defrag="defer+madvise",
        thp_shmem="within_size",
        thp_shrink_underused=0,
        khugepaged_max_ptes_none=500,
        khugepaged_scan_sleep_ms=15_000,
        khugepaged_pages_to_scan=4096,
        mthp_anon={64: "always", 128: "always", 2048: "madvise"},
        mthp_shmem=_SHMEM_MAP,
        zram_size="ram",
        zram_algorithm="lz4",
        journald_system_max="1G",
        journald_runtime_max="128M",
    ),
}


@dataclass(slots=True)
class Options:
    profile: Profile
    writeback_dev: str | None
    scratch: bool
    network: bool
    daemons: bool
    dry_run: bool


# ──────────────────────────── detection ────────────────────────────

@dataclass(slots=True)
class SystemState:
    kernel: str
    ram_gb: float
    zram_swaps: list[tuple[str, int]]
    disk_swaps: list[tuple[str, int]]
    swap_layout: str
    mthp_sizes: list[int]
    has_thp: bool
    has_mglru: bool
    container: bool
    zram_forbidden: bool


def detect() -> SystemState:
    meminfo = Path("/proc/meminfo").read_text()
    m = re.search(r"^MemTotal:\s+(\d+)\s+kB", meminfo, re.M)
    if not m:
        die("cannot parse MemTotal from /proc/meminfo")
    ram_gb = int(m.group(1)) / 1_048_576

    zram_swaps: list[tuple[str, int]] = []
    disk_swaps: list[tuple[str, int]] = []
    for line in Path("/proc/swaps").read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 5:
            entry = (fields[0], int(fields[4]))
            (zram_swaps if fields[0].startswith("/dev/zram") else disk_swaps).append(entry)

    match (bool(zram_swaps), bool(disk_swaps)):
        case (True, True):
            layout = "hybrid"
        case (True, False):
            layout = "zram-only"
        case (False, True):
            layout = "disk-only"
        case _:
            layout = "none"

    mthp_sizes = []
    if THP_DIR.is_dir():
        mthp_sizes = sorted(
            int(p.name.removeprefix("hugepages-").removesuffix("kB"))
            for p in THP_DIR.glob("hugepages-*kB")
        )

    container = run("systemd-detect-virt", "--quiet", "--container").returncode == 0
    cmdline = Path("/proc/cmdline").read_text()
    zram_forbidden = bool(re.search(r"(^|\s)systemd\.zram=0(\s|$)", cmdline))

    return SystemState(
        kernel=os.uname().release,
        ram_gb=ram_gb,
        zram_swaps=zram_swaps,
        disk_swaps=disk_swaps,
        swap_layout=layout,
        mthp_sizes=mthp_sizes,
        has_thp=THP_DIR.is_dir(),
        has_mglru=MGLRU_DIR.is_dir(),
        container=container,
        zram_forbidden=zram_forbidden,
    )


def recommended_profile(state: SystemState) -> Profile:
    key = "savings" if state.ram_gb < RAM_DEMARCATION_GB else "performance"
    return PROFILES[key]


def writeback_device_error(dev: str) -> str | None:
    p = Path(dev)
    if not p.exists():
        return "device does not exist"
    if not stat.S_ISBLK(p.stat().st_mode):
        return "not a block device"
    real = str(p.resolve())
    for line in Path("/proc/swaps").read_text().splitlines()[1:]:
        if line.split()[0] == real:
            return "device is an active swap area"
    r = run("findmnt", "-rn", "-o", "SOURCE")
    for src in r.stdout.split():
        if not src.startswith("/"):
            continue
        try:
            resolved = str(Path(src).resolve())
        except OSError:
            continue
        if resolved == real:
            return "device is currently mounted"
        if resolved.startswith(real) and re.fullmatch(r"p?\d+", resolved[len(real):]):
            return f"device holds mounted partition {resolved}"
    return None


# ──────────────────────────── artifact rendering ────────────────────────────

def render_sysctl(o: Options, st: SystemState) -> str:
    p = o.profile
    lines = [
        f"# Managed by memtune — profile: {p.label}",
        f"# Detected: RAM={st.ram_gb:.1f} GiB · swap layout={st.swap_layout} · kernel={st.kernel}",
        "",
        "# --- swap & reclaim ---",
        f"vm.swappiness = {p.swappiness}",
        "vm.page-cluster = 0",
        f"vm.vfs_cache_pressure = {p.vfs_cache_pressure}",
        "",
        "# --- dirty page writeback ---",
        f"vm.dirty_bytes = {p.dirty_bytes}",
        f"vm.dirty_background_bytes = {p.dirty_background_bytes}",
        "",
        "# --- allocation, compaction & accounting ---",
        f"vm.watermark_scale_factor = {p.watermark_scale_factor}",
        "vm.watermark_boost_factor = 0",
        "vm.compaction_proactiveness = 0",
        f"vm.stat_interval = {p.stat_interval}",
    ]
    if Path("/proc/sys/vm/page_lock_unfairness").exists():
        lines.append("vm.page_lock_unfairness = 1")
    lines += [
        "",
        "# --- compatibility ---",
        "vm.max_map_count = 2147483",
    ]
    if p.key == "performance" and Path("/proc/sys/kernel/split_lock_mitigate").exists():
        lines += [
            "",
            "# --- latency ---",
            "kernel.split_lock_mitigate = 0",
        ]
    if o.network:
        lines += [
            "",
            "# --- network: BBR + CAKE + eBPF JIT ---",
            "net.ipv4.tcp_congestion_control = bbr",
            "net.core.default_qdisc = cake",
            f"net.ipv4.tcp_rmem = {p.tcp_rmem}",
            f"net.ipv4.tcp_wmem = {p.tcp_wmem}",
            f"net.core.rmem_max = {p.rwmem_max}",
            f"net.core.wmem_max = {p.rwmem_max}",
            f"net.core.netdev_max_backlog = {p.netdev_backlog}",
            "net.ipv4.tcp_fastopen = 3",
            "net.ipv4.tcp_mtu_probing = 1",
            "net.core.bpf_jit_enable = 1",
            "net.core.bpf_jit_harden = 0",
        ]
    return "\n".join(lines) + "\n"


def render_tmpfiles(o: Options, st: SystemState) -> str:
    p = o.profile
    lines = [
        f"# Managed by memtune — profile: {p.label}",
        "# 'w-' = write, ignore failure (interface may be absent on this CPU/kernel).",
    ]
    if st.has_thp:
        lines += [
            "",
            "# --- global THP policy ---",
            f"w- {THP_DIR}/enabled - - - - {p.thp_enabled}",
            f"w- {THP_DIR}/defrag - - - - {p.thp_defrag}",
            f"w- {THP_DIR}/shmem_enabled - - - - {p.thp_shmem}",
        ]
        if (THP_DIR / "shrink_underused").exists():
            lines.append(f"w- {THP_DIR}/shrink_underused - - - - {p.thp_shrink_underused}")
        lines += [
            "",
            "# --- khugepaged daemon ---",
            f"w- {THP_DIR}/khugepaged/max_ptes_none - - - - {p.khugepaged_max_ptes_none}",
            f"w- {THP_DIR}/khugepaged/scan_sleep_millisecs - - - - {p.khugepaged_scan_sleep_ms}",
            f"w- {THP_DIR}/khugepaged/pages_to_scan - - - - {p.khugepaged_pages_to_scan}",
        ]
        if st.mthp_sizes:
            lines += ["", f"# --- multi-size THP matrix ({len(st.mthp_sizes)} sizes detected) ---"]
            for size in st.mthp_sizes:
                anon = p.mthp_anon.get(size, "never")
                lines.append(f"w- {THP_DIR}/hugepages-{size}kB/enabled - - - - {anon}")
            for size in st.mthp_sizes:
                shmem = p.mthp_shmem.get(size, "never")
                if (THP_DIR / f"hugepages-{size}kB/shmem_enabled").exists():
                    lines.append(f"w- {THP_DIR}/hugepages-{size}kB/shmem_enabled - - - - {shmem}")
    if st.has_mglru:
        lines += [
            "",
            "# --- MGLRU: hardware lock + working-set thrash shield ---",
            f"w- {MGLRU_DIR}/enabled - - - - 7",
            f"w- {MGLRU_DIR}/min_ttl_ms - - - - {p.mglru_min_ttl_ms}",
        ]
    return "\n".join(lines) + "\n"


def render_zram_conf(o: Options) -> str:
    p = o.profile
    lines = [
        f"# Managed by memtune — profile: {p.label}",
        "[zram0]",
        f"zram-size = {p.zram_size}",
        f"compression-algorithm = {p.zram_algorithm}",
        f"swap-priority = {ZRAM_SWAP_PRIORITY}",
        "options = discard",
    ]
    if o.writeback_dev:
        lines.append(f"writeback-device = {o.writeback_dev}")
    return "\n".join(lines) + "\n"


def render_scratch_unit(uid: int, gid: int) -> str:
    return f"""# Managed by memtune
[Unit]
Description=memtune: RAM-backed scratch tmpfs at {SCRATCH_DIR}
Before=local-fs.target
ConditionPathExists={SCRATCH_DIR}

[Mount]
What=tmpfs
Where={SCRATCH_DIR}
Type=tmpfs
Options=rw,nosuid,nodev,relatime,size=100%,mode=0755,uid={uid},gid={gid}

[Install]
WantedBy=local-fs.target
"""


def render_wb_service() -> str:
    return f"""# Managed by memtune
[Unit]
Description=memtune: ZRAM idle writeback flush
After=systemd-zram-setup@zram0.service
ConditionPathExists=/sys/block/zram0/writeback

[Service]
Type=oneshot
# Flush pages idle since the previous cycle, then age the rest for the next one.
ExecStart=-/usr/bin/sh -c 'echo idle > /sys/block/zram0/writeback'
ExecStart=-/usr/bin/sh -c 'echo all > /sys/block/zram0/idle'
"""


def render_wb_timer() -> str:
    return """# Managed by memtune
[Unit]
Description=memtune: ZRAM idle writeback (every 20 minutes)

[Timer]
OnCalendar=*:0/20

[Install]
WantedBy=timers.target
"""


def render_journald(o: Options) -> str:
    p = o.profile
    return f"""# Managed by memtune — profile: {p.label}
[Journal]
SystemMaxUse={p.journald_system_max}
RuntimeMaxUse={p.journald_runtime_max}
"""


def render_oomd() -> str:
    return f"""# Managed by memtune
# swappiness {PROFILES['savings'].swappiness}/{PROFILES['performance'].swappiness} keeps ZRAM heavily
# utilised BY DESIGN; oomd's default SwapUsedLimit=90% would kill sessions prematurely.
[OOM]
SwapUsedLimit=95%
DefaultMemoryPressureLimit=60%
DefaultMemoryPressureDurationSec=20s
"""


def target_ids() -> tuple[int, int]:
    return (int(os.environ.get("SUDO_UID", str(os.getuid()))),
            int(os.environ.get("SUDO_GID", str(os.getgid()))))


def build_artifacts(o: Options, st: SystemState) -> list[tuple[Path, str]]:
    uid, gid = target_ids()
    artifacts = [
        (ZRAM_CONF_FILE, render_zram_conf(o)),
        (SYSCTL_FILE, render_sysctl(o, st)),
        (TMPFILES_FILE, render_tmpfiles(o, st)),
    ]
    if o.scratch:
        artifacts.append((SCRATCH_UNIT_FILE, render_scratch_unit(uid, gid)))
    if o.writeback_dev:
        artifacts += [(WB_SERVICE, render_wb_service()), (WB_TIMER, render_wb_timer())]
    if o.daemons:
        artifacts += [(JOURNALD_DROPIN, render_journald(o)), (OOMD_DROPIN, render_oomd())]
    return artifacts


# ──────────────────────────── application ────────────────────────────

def write_file(path: Path, content: str) -> bool:
    if path.exists() and path.read_text() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return True


def ensure_zram_generator() -> None:
    if ZRAM_GENERATOR.exists():
        return
    warn("zram-generator missing — bootstrapping via pacman")
    deadline = time.monotonic() + 60
    while Path("/var/lib/pacman/db.lck").exists():
        if time.monotonic() > deadline:
            die("pacman database is locked by another process")
        time.sleep(2)
    r = subprocess.run(["pacman", "-S", "--needed", "--noconfirm", "zram-generator"])
    if r.returncode != 0:
        die("failed to install zram-generator — run 'pacman -Syu zram-generator' manually")
    ok("zram-generator installed")


def neutralize_zswap(dry: bool) -> bool:
    """Returns True when the kernel cmdline was modified (initramfs rebuild needed)."""
    if ZSWAP_PARAM.exists():
        live = ZSWAP_PARAM.read_text().strip()
        if live in ("Y", "1"):
            if dry:
                info("would live-disable zswap (currently enabled)")
            else:
                try:
                    ZSWAP_PARAM.write_text("0")
                    ok("zswap live-disabled")
                except OSError as e:
                    warn(f"could not live-disable zswap: {e}")
        else:
            ok("zswap already disabled in the running kernel")

    if not KERNEL_CMDLINE.exists():
        warn(f"{KERNEL_CMDLINE} not found — if using GRUB, add 'zswap.enabled=0' manually")
        return False
    tokens = KERNEL_CMDLINE.read_text().split()
    kept = [t for t in tokens if not t.startswith("zswap.enabled=")]
    if kept != tokens or "zswap.enabled=0" not in tokens:
        new = " ".join(kept + ["zswap.enabled=0"]) + "\n"
        if dry:
            info(f"would patch {KERNEL_CMDLINE} → append zswap.enabled=0")
            return False
        KERNEL_CMDLINE.write_text(new)
        ok(f"{KERNEL_CMDLINE} patched with zswap.enabled=0")
        return True
    ok("kernel cmdline already pins zswap.enabled=0")
    return False


def unit_loaded(unit: str) -> bool:
    r = run("systemctl", "show", "-p", "LoadState", "--value", unit)
    return r.stdout.strip() == "loaded"


def zram0_swap_active() -> bool:
    return any(
        line.split()[0] == "/dev/zram0"
        for line in Path("/proc/swaps").read_text().splitlines()[1:]
    )


def mkinitcpio_banner() -> None:
    bar = "═" * 68
    print(f"\n{C.RED}{C.BOLD}{bar}\n  [!] BOOTLOADER CMDLINE MODIFIED — ACTION REQUIRED [!]\n{bar}{C.RST}")
    print(f"{C.YLW}  Regenerate your initramfs/UKI before the next reboot:{C.RST}")
    print(f"    {C.BOLD}mkinitcpio -P{C.RST}\n")


def dry_run_report(o: Options, st: SystemState, artifacts: list[tuple[Path, str]]) -> int:
    neutralize_zswap(dry=True)
    for path, content in artifacts:
        marker = "(unchanged)" if path.exists() and path.read_text() == content else "(would write)"
        panel(f"{path} {marker}")
        print(content, end="")
    panel("planned actions")
    actions = [
        "systemctl daemon-reload (regenerates zram units)",
        f"sysctl --load {SYSCTL_FILE}",
        f"systemd-tmpfiles --create {TMPFILES_FILE}",
    ]
    if o.scratch:
        actions.append(f"systemctl enable --now {SCRATCH_UNIT}")
    if o.writeback_dev:
        actions.append(f"systemctl enable --now {WB_TIMER.name}")
    if o.daemons:
        actions.append("systemctl try-restart systemd-journald systemd-oomd")
    if not zram0_swap_active():
        actions.append("systemctl start dev-zram0.swap")
    for a in actions:
        print(f"  · {a}")
    print(f"\n{C.BOLD}dry run complete — nothing was written.{C.RST}")
    return 0


def apply(o: Options, st: SystemState) -> int:
    artifacts = build_artifacts(o, st)
    if o.dry_run:
        return dry_run_report(o, st, artifacts)

    if st.zram_forbidden:
        die("kernel cmdline carries systemd.zram=0 — zram device creation is disabled by boot policy")

    ensure_zram_generator()
    cmdline_changed = neutralize_zswap(dry=False)

    changed: dict[Path, bool] = {}
    for path, content in artifacts:
        changed[path] = write_file(path, content)
        if changed[path]:
            ok(f"wrote {path}")
        else:
            info(f"{path} already in desired state")

    if not o.writeback_dev and WB_TIMER.exists():
        run("systemctl", "disable", "--now", WB_TIMER.name)
        WB_TIMER.unlink(missing_ok=True)
        WB_SERVICE.unlink(missing_ok=True)
        info("removed stale zram-writeback units (no --writeback requested)")

    if o.scratch:
        SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        os.chown(SCRATCH_DIR, *target_ids())

    run("systemctl", "daemon-reload")

    for unit in ("systemd-zram-setup@zram0.service", "dev-zram0.swap"):
        if not unit_loaded(unit):
            die(f"generated unit not loaded after daemon-reload: {unit}")

    zram_was_active = zram0_swap_active()
    if not zram_was_active:
        r = run("systemctl", "start", "dev-zram0.swap")
        if r.returncode == 0 and zram0_swap_active():
            ok("zram0 swap activated live")
        else:
            warn("could not activate zram0 swap live — it will come up on next boot")
    elif changed.get(ZRAM_CONF_FILE):
        warn("zram0 is in use — new size/algorithm takes effect at next boot")

    if o.scratch:
        run("systemctl", "enable", SCRATCH_UNIT)
        src = run("findmnt", "-rn", "-o", "SOURCE", "--mountpoint", str(SCRATCH_DIR)).stdout.strip()
        if src in ("/dev/zram1", "zram1"):
            warn(f"{SCRATCH_DIR} is mounted from a legacy zram block; tmpfs takes over at reboot")
        elif not src:
            run("systemctl", "start", SCRATCH_UNIT)
            if run("findmnt", "-rn", "-o", "SOURCE", "--mountpoint", str(SCRATCH_DIR)).stdout.strip() == "tmpfs":
                ok(f"tmpfs scratch live-mounted at {SCRATCH_DIR}")

    if o.writeback_dev:
        run("systemctl", "enable", "--now", WB_TIMER.name)
        ok(f"zram idle-writeback timer armed (20 min cycle → {o.writeback_dev})")

    r = run("sysctl", "-q", "--load", str(SYSCTL_FILE))
    if r.returncode != 0:
        warn(f"sysctl reported errors:\n{r.stderr.strip()}")
    run("systemd-tmpfiles", "--create", str(TMPFILES_FILE))

    if o.daemons:
        if changed.get(JOURNALD_DROPIN):
            run("systemctl", "try-restart", "systemd-journald")
        if changed.get(OOMD_DROPIN):
            run("systemctl", "try-restart", "systemd-oomd")

    post = detect()
    if post.swap_layout == "hybrid":
        zmax = max(p for _, p in post.zram_swaps)
        dmax = max(p for _, p in post.disk_swaps)
        if zmax <= dmax:
            warn(f"PRIORITY INVERSION: disk swap prio {dmax} ≥ zram prio {zmax} — "
                 f"swappiness {o.profile.swappiness} will thrash the SSD. Lower the disk swap priority.")
        else:
            ok(f"swap routing sane: zram ({zmax}) overrides disk ({dmax})")

    failures = verify(o, st)

    if cmdline_changed:
        mkinitcpio_banner()

    label = f"{C.BOLD}{o.profile.label}{C.RST}"
    if failures:
        err(f"profile {label} applied with {failures} verification failure(s)")
        return 1
    print(f"{C.GRN}[DONE]{C.RST} profile {label} applied and verified live.")
    return 0


# ──────────────────────────── verification ────────────────────────────

def sysctl_live(key: str) -> str | None:
    p = Path("/proc/sys") / key.replace(".", "/")
    try:
        return " ".join(p.read_text().split())
    except OSError:
        return None


def bracketed(text: str) -> str:
    m = re.search(r"\[([^\]]+)\]", text)
    return m.group(1) if m else text.strip()


def verify(o: Options, st: SystemState) -> int:
    p = o.profile
    checks: list[tuple[str, str, str | None]] = []  # (label, expected, actual|None)

    sysctl_expect = {
        "vm.swappiness": str(p.swappiness),
        "vm.page-cluster": "0",
        "vm.vfs_cache_pressure": str(p.vfs_cache_pressure),
        "vm.dirty_bytes": str(p.dirty_bytes),
        "vm.dirty_background_bytes": str(p.dirty_background_bytes),
        "vm.watermark_scale_factor": str(p.watermark_scale_factor),
        "vm.watermark_boost_factor": "0",
        "vm.compaction_proactiveness": "0",
        "vm.stat_interval": str(p.stat_interval),
        "vm.max_map_count": "2147483",
    }
    if Path("/proc/sys/vm/page_lock_unfairness").exists():
        sysctl_expect["vm.page_lock_unfairness"] = "1"
    if p.key == "performance" and Path("/proc/sys/kernel/split_lock_mitigate").exists():
        sysctl_expect["kernel.split_lock_mitigate"] = "0"
    if o.network:
        sysctl_expect |= {
            "net.ipv4.tcp_congestion_control": "bbr",
            "net.core.default_qdisc": "cake",
            "net.ipv4.tcp_rmem": p.tcp_rmem,
            "net.ipv4.tcp_wmem": p.tcp_wmem,
            "net.core.netdev_max_backlog": str(p.netdev_backlog),
            "net.core.bpf_jit_harden": "0",
        }
    for key, expected in sysctl_expect.items():
        checks.append((key, expected, sysctl_live(key)))

    if st.has_thp:
        for name, expected in (
            ("enabled", p.thp_enabled),
            ("defrag", p.thp_defrag),
            ("shmem_enabled", p.thp_shmem),
        ):
            path = THP_DIR / name
            checks.append((f"thp.{name}", expected, bracketed(path.read_text()) if path.exists() else None))
        if (THP_DIR / "shrink_underused").exists():
            checks.append(("thp.shrink_underused", str(p.thp_shrink_underused),
                           (THP_DIR / "shrink_underused").read_text().strip()))
        for name, expected in (
            ("max_ptes_none", str(p.khugepaged_max_ptes_none)),
            ("scan_sleep_millisecs", str(p.khugepaged_scan_sleep_ms)),
            ("pages_to_scan", str(p.khugepaged_pages_to_scan)),
        ):
            path = THP_DIR / "khugepaged" / name
            checks.append((f"khugepaged.{name}", expected, path.read_text().strip() if path.exists() else None))
        for size in st.mthp_sizes:
            anon_path = THP_DIR / f"hugepages-{size}kB/enabled"
            if anon_path.exists():
                checks.append((f"mthp.{size}kB.enabled", p.mthp_anon.get(size, "never"),
                               bracketed(anon_path.read_text())))
            shmem_path = THP_DIR / f"hugepages-{size}kB/shmem_enabled"
            if shmem_path.exists():
                checks.append((f"mthp.{size}kB.shmem", p.mthp_shmem.get(size, "never"),
                               bracketed(shmem_path.read_text())))

    if st.has_mglru:
        raw = (MGLRU_DIR / "enabled").read_text().strip()
        checks.append(("mglru.enabled", "0x0007", "0x0007" if int(raw, 16) == 7 else raw))
        checks.append(("mglru.min_ttl_ms", str(p.mglru_min_ttl_ms),
                       (MGLRU_DIR / "min_ttl_ms").read_text().strip()))

    failures = 0
    panel("live kernel verification")
    for label, expected, actual in checks:
        if actual is None:
            print(f"  {C.DIM}○ {label:<34} interface absent — skipped{C.RST}")
        elif actual == expected:
            if not QUIET:
                print(f"  {C.GRN}✓{C.RST} {label:<34} {actual}")
        else:
            failures += 1
            print(f"  {C.RED}✗{C.RST} {label:<34} {actual}  {C.RED}(expected {expected}){C.RST}")
    if zram0_swap_active():
        print(f"  {C.GRN}✓{C.RST} {'zram0 swap':<34} active @ priority {ZRAM_SWAP_PRIORITY}")
    else:
        print(f"  {C.YLW}~{C.RST} {'zram0 swap':<34} inactive — comes up at next boot")
    total = len(checks)
    print(f"\n  {C.BOLD}{total - failures}/{total} checks passed{C.RST}")
    return failures


# ──────────────────────────── interactive front-ends ────────────────────────────

def choose(prompt: str, options: list[tuple[str, str]], default: int) -> int:
    print(f"\n  {C.BOLD}{prompt}{C.RST}")
    for i, (label, desc) in enumerate(options):
        marker = f"{C.CYN}→{C.RST}" if i == default else " "
        print(f"   {marker} {C.BOLD}{i + 1}{C.RST}) {label:<38} {C.DIM}{desc}{C.RST}")
    while True:
        raw = input(f"  select [1-{len(options)}, Enter={default + 1}, q=abort]: ").strip().lower()
        if not raw:
            return default
        if raw in ("q", "quit"):
            print(f"{C.YLW}aborted — nothing written.{C.RST}")
            sys.exit(0)
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print(f"  {C.RED}invalid choice{C.RST}")


def step(n: int, total: int, title: str, blurb: str) -> None:
    print(f"\n{C.MAG}{C.BOLD}━━ Step {n}/{total} · {title} {'━' * max(0, 48 - len(title))}{C.RST}")
    print(f"{C.DIM}{blurb}{C.RST}")


def ask_writeback_device() -> str | None:
    while True:
        raw = input("  block device for idle writeback (Enter = none): ").strip()
        if not raw:
            return None
        if (reason := writeback_device_error(raw)) is None:
            return raw
        print(f"  {C.RED}rejected:{C.RST} {reason}")


def detection_panel(st: SystemState, rec: Profile) -> None:
    panel("hardware detection")
    swaps = ", ".join(f"{d} (prio {pr})" for d, pr in st.zram_swaps + st.disk_swaps) or "none"
    side = "<" if st.ram_gb < RAM_DEMARCATION_GB else "≥"
    print(f"  kernel        {st.kernel}")
    print(f"  RAM           {st.ram_gb:.1f} GiB  ({side} {RAM_DEMARCATION_GB:.0f} GB demarcation line)")
    print(f"  swap layout   {st.swap_layout}: {swaps}")
    print(f"  THP / MGLRU   {'yes' if st.has_thp else 'no'} ({len(st.mthp_sizes)} mTHP sizes) / {'yes' if st.has_mglru else 'no'}")
    print(f"  recommended   {C.BOLD}{rec.label}{C.RST} — {C.DIM}{rec.blurb}{C.RST}")


def profile_preview(p: Profile) -> None:
    rows = [
        ("swappiness / vfs_cache_pressure", f"{p.swappiness} / {p.vfs_cache_pressure}"),
        ("dirty bytes (bg)", f"{p.dirty_bytes >> 20} MiB ({p.dirty_background_bytes >> 20} MiB)"),
        ("MGLRU min_ttl_ms", str(p.mglru_min_ttl_ms)),
        ("THP / 64k mTHP", f"{p.thp_enabled} / {p.mthp_anon.get(64, 'never')}"),
        ("khugepaged ptes/sleep", f"{p.khugepaged_max_ptes_none} / {p.khugepaged_scan_sleep_ms} ms"),
        ("zram size · algorithm", f"{p.zram_size} · {p.zram_algorithm}"),
        ("journald system/runtime cap", f"{p.journald_system_max} / {p.journald_runtime_max}"),
    ]
    for k, v in rows:
        print(f"    {k:<32} {C.BOLD}{v}{C.RST}")


def standard_flow(st: SystemState, o: Options, rec: Profile) -> Options:
    if not sys.stdin.isatty():
        die("standard mode is interactive; use --auto (or --profile) for unattended runs", 2)
    detection_panel(st, rec)
    panel(f"proposed dials — {o.profile.label}")
    profile_preview(o.profile)
    alt = "performance" if o.profile.key == "savings" else "savings"
    print(f"\n  {C.CYN}[Enter]{C.RST} apply recommended · "
          f"{C.CYN}{alt}{C.RST} switch profile · {C.CYN}wizard{C.RST} granular setup · {C.CYN}q{C.RST} quit")
    while True:
        try:
            raw = input("  > ").strip().lower()
        except EOFError:
            die("stdin closed — use --auto for unattended runs", 2)
        match raw:
            case "":
                return o
            case "q" | "quit":
                print(f"{C.YLW}aborted — nothing written.{C.RST}")
                sys.exit(0)
            case "savings" | "s":
                o.profile = PROFILES["savings"]
                return o
            case "performance" | "p":
                o.profile = PROFILES["performance"]
                return o
            case "wizard" | "w":
                return wizard_flow(st, o)
            case _:
                print(f"  {C.RED}unrecognized{C.RST} — Enter, '{alt}', 'wizard', or 'q'")


def wizard_flow(st: SystemState, o: Options) -> Options:
    if not sys.stdin.isatty():
        die("wizard mode requires a TTY", 2)
    total = 7
    panel("memtune advanced wizard")
    print(f"{C.DIM}Each dial is explained; Enter accepts the highlighted recommendation.{C.RST}")

    step(1, total, "Baseline Profile", "Seeds every later default; individual dials can still be overridden.")
    keys = ["savings", "performance"]
    idx = choose("baseline", [(PROFILES[k].label, PROFILES[k].blurb) for k in keys],
                 keys.index(recommended_profile(st).key))
    p = PROFILES[keys[idx]]

    step(2, total, "ZRAM Topology",
         "zram is a compressed RAM block device used as swap. Capacity is virtual: pages only\n"
         "cost RAM once swapped, at ~2-3x compression. zstd = best ratio, lz4 = lowest latency.")
    sizes = [("ram", "uncompressed capacity = 100% of RAM"),
             ("ram / 2", "conservative ceiling"),
             ("min(ram, 16384)", "cap at 16 GiB")]
    p = dataclasses.replace(p, zram_size=sizes[choose("zram device size", sizes, 0)][0])
    algos = [("zstd", "highest compression ratio — RAM savings"),
             ("lz4", "fastest codec — lowest swap latency"),
             ("lzo-rle", "legacy balanced fallback")]
    p = dataclasses.replace(
        p, zram_algorithm=algos[choose("compression algorithm", algos,
                                       0 if p.zram_algorithm == "zstd" else 1)][0])
    if o.writeback_dev is None:
        print(f"\n  {C.BOLD}idle writeback{C.RST} {C.DIM}(flushes cold compressed pages to a raw "
              f"block device every 20 min; the device is OVERWRITTEN){C.RST}")
        o.writeback_dev = ask_writeback_device()
    scratch = [("Enable tmpfs scratch at /mnt/zram1", "RAM-backed work area, swaps into zram under pressure"),
               ("Skip", "no scratch mount")]
    o.scratch = choose("scratch mount", scratch, 0 if o.scratch else 1) == 0

    step(3, total, "Swappiness & VFS Pressure",
         "swappiness >100 tells MGLRU that zram I/O is cheaper than dropping file cache —\n"
         "190 compresses inactive anon RAM almost immediately. vfs_cache_pressure >100\n"
         "reclaims dentry/inode slab caches aggressively to cut idle footprint.")
    sw = [("190", "zram-first: compress inactive RAM immediately"),
          ("150", "balanced zram bias"),
          ("100", "neutral"),
          ("60", "kernel default — disk-era conservative")]
    p = dataclasses.replace(p, swappiness=int(sw[choose("vm.swappiness", sw, 0 if p.swappiness == 190 else 1)][0]))
    vfs = [("200", "aggressive slab reclaim — minimum idle RAM"),
           ("100", "kernel default"),
           ("50", "favor metadata caches — faster file walks")]
    p = dataclasses.replace(p, vfs_cache_pressure=int(
        vfs[choose("vm.vfs_cache_pressure", vfs, 0 if p.vfs_cache_pressure == 200 else 1)][0]))

    step(4, total, "Dirty Page Writeback",
         "Caps RAM holding not-yet-written file data. Small = steady flushing, low bloat;\n"
         "large = big transfer bursts absorbed in RAM at the cost of footprint.")
    dirty = [("128 MiB / 32 MiB", "strict — transfers cannot balloon RAM"),
             ("512 MiB / 128 MiB", "middle ground"),
             ("1 GiB / 256 MiB", "throughput — absorb bursts in RAM")]
    di = choose("dirty_bytes / dirty_background_bytes", dirty, 0 if p.dirty_bytes < 200 << 20 else 2)
    vals = [(134_217_728, 33_554_432), (536_870_912, 134_217_728), (1_073_741_824, 268_435_456)]
    p = dataclasses.replace(p, dirty_bytes=vals[di][0], dirty_background_bytes=vals[di][1])

    step(5, total, "MGLRU Thrash Shield",
         "min_ttl_ms forbids reclaiming pages younger than N ms — stops hot pages from\n"
         "cycling through compress/decompress. Too high risks OOM kills under real pressure.")
    ttls = [("0", "off — pure LRU economics"),
            ("1000", "light shield"),
            ("2000", "balanced (savings default)"),
            ("3000", "deep shield (performance default)"),
            ("5000", "maximum — OOM risk on small RAM")]
    p = dataclasses.replace(p, mglru_min_ttl_ms=int(
        ttls[choose("min_ttl_ms", ttls, 2 if p.mglru_min_ttl_ms == 2000 else 3)][0]))

    step(6, total, "THP / mTHP Allocation",
         "Multi-size THP serves 64k-2M pages to cut TLB misses. 'madvise' = only apps that\n"
         "ask; 'always' on 64k/128k = transparent speed for Wayland/games at some bloat.\n"
         "khugepaged dials control how hard the background collapser works.")
    thp = [("Strict madvise everywhere", "tight ptes=180, 60 s scans, shrink bloated THPs"),
           ("mTHP performance", "64k/128k always-on, ptes=500, 15 s scans")]
    if choose("THP strategy", thp, 0 if p.thp_shrink_underused == 1 else 1) == 0:
        sref = PROFILES["savings"]
        p = dataclasses.replace(p, mthp_anon=sref.mthp_anon, thp_shrink_underused=1,
                                khugepaged_max_ptes_none=180, khugepaged_scan_sleep_ms=60_000)
    else:
        pref = PROFILES["performance"]
        p = dataclasses.replace(p, mthp_anon=pref.mthp_anon, thp_shrink_underused=0,
                                khugepaged_max_ptes_none=500, khugepaged_scan_sleep_ms=15_000)

    step(7, total, "Network Stack & Daemon Limits",
         "BBR models bandwidth instead of reacting to loss; CAKE kills bufferbloat at the\n"
         "qdisc. Daemon limits cap journald disk/tmpfs use and relax oomd's swap trigger\n"
         "(mandatory sanity when swappiness keeps zram full by design).")
    net = [("Apply BBR + CAKE + buffers + eBPF JIT", "full network matrix"),
           ("Leave network stack untouched", "skip all net.* keys")]
    o.network = choose("network tuning", net, 0 if o.network else 1) == 0
    dmn = [("Apply journald caps + oomd policy", "recommended"),
           ("Skip daemon limits", "leave journald/oomd defaults")]
    o.daemons = choose("daemon limits", dmn, 0 if o.daemons else 1) == 0

    o.profile = p
    panel("wizard summary")
    profile_preview(p)
    print(f"    {'writeback device':<32} {C.BOLD}{o.writeback_dev or '—'}{C.RST}")
    print(f"    {'scratch / network / daemons':<32} "
          f"{C.BOLD}{o.scratch} / {o.network} / {o.daemons}{C.RST}")
    input(f"\n  {C.CYN}[Enter]{C.RST} apply · Ctrl-C abort ")
    return o


# ──────────────────────────── status ────────────────────────────

def print_status() -> int:
    st = detect()
    detection_panel(st, recommended_profile(st))
    panel("live kernel state")
    for key in ("vm.swappiness", "vm.vfs_cache_pressure", "vm.dirty_bytes",
                "vm.watermark_scale_factor", "vm.compaction_proactiveness",
                "net.ipv4.tcp_congestion_control", "net.core.default_qdisc"):
        print(f"  {key:<36} {sysctl_live(key) or '—'}")
    if st.has_thp:
        print(f"  {'thp.enabled / defrag':<36} "
              f"{bracketed((THP_DIR / 'enabled').read_text())} / {bracketed((THP_DIR / 'defrag').read_text())}")
        su = THP_DIR / "shrink_underused"
        if su.exists():
            print(f"  {'thp.shrink_underused':<36} {su.read_text().strip()}")
    if st.has_mglru:
        print(f"  {'mglru.enabled / min_ttl_ms':<36} "
              f"{(MGLRU_DIR / 'enabled').read_text().strip()} / {(MGLRU_DIR / 'min_ttl_ms').read_text().strip()}")
    if ZSWAP_PARAM.exists():
        print(f"  {'zswap (live)':<36} {ZSWAP_PARAM.read_text().strip()}")
    panel("managed artifacts")
    for path in (ZRAM_CONF_FILE, SYSCTL_FILE, TMPFILES_FILE, SCRATCH_UNIT_FILE,
                 WB_TIMER, JOURNALD_DROPIN, OOMD_DROPIN):
        state = f"{C.GRN}present{C.RST}" if path.exists() else f"{C.DIM}absent{C.RST}"
        print(f"  {str(path):<58} {state}")
    return 0


# ──────────────────────────── entry point ────────────────────────────

def parse_args(argv: list[str]) -> argparse.Namespace:
    extra = {"color": True, "suggest_on_error": True} if sys.version_info >= (3, 14) else {}
    ap = argparse.ArgumentParser(
        prog="memtune",
        description="Master ZRAM / VM / THP / MGLRU / network tuning orchestrator for Arch Linux.",
        epilog=f"Profiles route on a {RAM_DEMARCATION_GB:.0f} GB RAM demarcation line: "
               "'savings' below, 'performance' above.",
        **extra,
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("-a", "--auto", action="store_true",
                      help="zero-touch: detect hardware, apply the optimal profile silently")
    mode.add_argument("-w", "--wizard", "--interactive", action="store_true",
                      help="granular step-by-step tuning wizard")
    ap.add_argument("-p", "--profile", choices=("savings", "performance"),
                    help="force a profile (non-interactive unless combined with --wizard)")
    ap.add_argument("--writeback", metavar="DEV",
                    help="raw block device for zram idle writeback (WILL BE OVERWRITTEN)")
    ap.add_argument("--no-scratch", action="store_true", help=f"skip the {SCRATCH_DIR} tmpfs mount")
    ap.add_argument("--no-network", action="store_true", help="leave the network stack untouched")
    ap.add_argument("--no-daemons", action="store_true", help="skip journald/systemd-oomd limits")
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="render every artifact and planned action; write nothing (no root needed)")
    ap.add_argument("--status", action="store_true", help="print live tuning state and exit")
    ap.add_argument("--no-color", action="store_true")
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    if sys.version_info < REQUIRED_PY:
        die(f"Python {'.'.join(map(str, REQUIRED_PY))}+ required, running {sys.version.split()[0]}")
    args = parse_args(argv)
    if args.no_color or not sys.stdout.isatty() or "NO_COLOR" in os.environ:
        C.strip()
    if args.status:
        return print_status()

    if os.geteuid() != 0 and not args.dry_run:
        info("root privileges required — escalating via sudo")
        os.execvp("sudo", ["sudo", "--", sys.executable, str(Path(__file__).resolve()), *argv])

    state = detect()
    if state.container:
        if args.dry_run:
            warn("container detected — zram-generator is inert in containers")
        else:
            die("container detected — refusing to tune memory inside a container")

    if args.writeback and (reason := writeback_device_error(args.writeback)):
        die(f"--writeback {args.writeback}: {reason}")

    rec = recommended_profile(state)
    opts = Options(
        profile=PROFILES[args.profile] if args.profile else rec,
        writeback_dev=args.writeback,
        scratch=not args.no_scratch,
        network=not args.no_network,
        daemons=not args.no_daemons,
        dry_run=args.dry_run,
    )

    global QUIET
    if args.wizard:
        opts = wizard_flow(state, opts)
    elif args.auto or args.profile:
        QUIET = not args.dry_run
    else:
        opts = standard_flow(state, opts, rec)

    return apply(opts, state)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print(f"\n{C.YLW}aborted — nothing further was written.{C.RST}")
        sys.exit(130)
