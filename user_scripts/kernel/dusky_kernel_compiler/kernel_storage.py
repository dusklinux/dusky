"""Machine-local storage configuration and recoverable RAM workspaces."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib


class StorageError(ValueError):
    pass


def load_settings(path: Path, cache_home: Path, build_dir: Path | None = None) -> dict:
    raw = tomllib.loads(path.read_text())
    keys = {'persistent_dir', 'packages_dir', 'ccache_dir', 'thinlto_dir', 'zram_dir', 'ram_reserve_gib'}
    if set(raw) != {'storage'} or not isinstance(raw['storage'], dict) or set(raw['storage']) != keys:
        raise StorageError(f'{path}: expected [storage] with keys: {", ".join(sorted(keys))}')
    s = dict(raw['storage'])
    for key in keys - {'ram_reserve_gib'}:
        if not isinstance(s[key], str):
            raise StorageError(f'{path}: storage.{key} must be a string')
    if type(s['ram_reserve_gib']) is not int or s['ram_reserve_gib'] < 0:
        raise StorageError('storage.ram_reserve_gib must be a nonnegative integer')
    def expand(value):
        return Path(os.path.expandvars(value)).expanduser().resolve()
    s['persistent_dir'] = expand(str(build_dir) if build_dir is not None else os.environ.get('DUSKY_BUILD_DIR') or s['persistent_dir'] or str(cache_home / 'dusky-kernel'))
    for key, env, child in [('packages_dir', 'DUSKY_PKGDEST', 'packages'),
                            ('ccache_dir', 'CCACHE_DIR', 'ccache'),
                            ('thinlto_dir', 'DUSKY_THINLTO_CACHE', 'thinlto-cache')]:
        s[key] = expand(os.environ.get(env) or s[key] or str(s['persistent_dir'] / child))
    if not s['zram_dir']:
        raise StorageError('storage.zram_dir must name a RAM workspace')
    s['zram_dir'] = expand(s['zram_dir'])
    paths = [s['persistent_dir'] / 'src', s['persistent_dir'] / 'seeds',
             s['packages_dir'], s['ccache_dir'], s['thinlto_dir'], s['zram_dir']]
    for i, a in enumerate(paths):
        for b in paths[i + 1:]:
            if a == b or a in b.parents or b in a.parents:
                raise StorageError(f'Storage locations must not overlap: {a} and {b}')
    return s


def ram_mount(path: Path) -> bool:
    parent = path
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    cp = subprocess.run(['findmnt', '-J', '-o', 'SOURCE,FSTYPE', '--target', str(parent)],
                        text=True, capture_output=True, check=False)
    if cp.returncode:
        return False
    rows = json.loads(cp.stdout).get('filesystems', [])
    return bool(rows and (rows[0]['fstype'] == 'tmpfs' or rows[0]['source'].startswith('/dev/zram')))


def tree_bytes(path: Path) -> int:
    """Allocated bytes, excluding symlinks and disposable package staging."""
    if not path.exists():
        return 0
    total = 0
    for parent, dirs, files in path.walk():
        dirs[:] = [name for name in dirs if name != 'pacman']
        for name in files:
            item = parent / name
            if not item.is_symlink() and '.pkg.tar' not in name:
                total += item.stat().st_blocks * 512
    return total


class RamCapacity(tuple):
    """Result of ram_capacity check; behaves as (ready, detail) tuple for compatibility."""
    ready: bool
    detail: str
    phys_ram_sufficient: bool
    total_ram: int
    needed: int
    budget: int

    def __new__(cls, ready: bool, detail: str, *, phys_ram_sufficient: bool = False,
                total_ram: int = 0, needed: int = 0, budget: int = 0):
        obj = super().__new__(cls, (ready, detail))
        obj.ready = ready
        obj.detail = detail
        obj.phys_ram_sufficient = phys_ram_sufficient
        obj.total_ram = total_ram
        obj.needed = needed
        obj.budget = budget
        return obj


def ram_capacity(settings: dict, lto: str, tree_name: str) -> RamCapacity:
    meminfo: dict[str, int] = {}
    try:
        for line in Path('/proc/meminfo').read_text(encoding='utf-8', errors='replace').splitlines():
            parts = line.split()
            if len(parts) >= 2:
                meminfo[parts[0].rstrip(':')] = int(parts[1]) * 1024
    except OSError:
        pass
    mem_total = meminfo.get('MemTotal', 0)
    source_size = tree_bytes(settings['persistent_dir'] / 'src' / tree_name) if tree_name and 'persistent_dir' in settings else 0
    cache_size = sum(tree_bytes(settings[key]) for key in ('thinlto_dir', 'ccache_dir') if key in settings)
    needed = max((30 if lto == 'full' else 22) << 30, source_size) + cache_size
    phys_ram_sufficient = bool(mem_total and mem_total >= needed)

    path = settings.get('zram_dir')
    if not path or not ram_mount(path):
        return RamCapacity(False, f'RAM mount unavailable at {path}; using persistent disk',
                           phys_ram_sufficient=phys_ram_sufficient, total_ram=mem_total, needed=needed, budget=0)
    parent = path
    while not parent.exists():
        parent = parent.parent
    ram = path / hashlib.sha256(str(settings['persistent_dir']).encode()).hexdigest()[:12]
    # Completed work already occupies this mount. It can be reused or discarded
    # by ram_workspace, so do not count it twice when checking the next build.
    reusable = tree_bytes(ram) if not (ram / '.unsaved').exists() else 0
    free = shutil.disk_usage(parent).free
    available = meminfo.get('MemAvailable')
    if available is None:
        try:
            available = next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text(encoding='utf-8', errors='replace').splitlines()
                             if line.startswith('MemAvailable:'))
        except (OSError, StopIteration):
            available = 0
    reserve = settings.get('ram_reserve_gib', 0) << 30
    budget = min(free + reusable, max(0, available + reusable - reserve))
    if budget < needed:
        return RamCapacity(False, f'RAM capacity {budget / (1 << 30):.1f} GiB < estimated workspace {needed / (1 << 30):.1f} GiB; using persistent disk',
                           phys_ram_sufficient=phys_ram_sufficient, total_ram=mem_total, needed=needed, budget=budget)
    return RamCapacity(True, f'Using RAM workspace {path} ({budget / (1 << 30):.1f} GiB budget, {needed / (1 << 30):.1f} GiB estimated)',
                       phys_ram_sufficient=phys_ram_sufficient, total_ram=mem_total, needed=needed, budget=budget)


def sync_tree(source: Path, dest: Path, run) -> None:
    source.mkdir(parents=True, exist_ok=True)
    dest.mkdir(parents=True, exist_ok=True)
    run(['rsync', '-a', '--delete', '--exclude=pacman/', '--exclude=*.pkg.tar.*', '--', str(source) + '/', str(dest) + '/'])


@contextmanager
def ram_workspace(settings: dict, run, note, save_run=None, *, tree_name: str):
    """Caller holds the persistent workspace lock throughout restore/build/save."""
    persistent = settings['persistent_dir']
    ram = settings['zram_dir'] / hashlib.sha256(str(persistent).encode()).hexdigest()[:12]
    if not ram_mount(ram):
        raise StorageError(f'{ram}: not on a mounted tmpfs or ZRAM device')
    pairs = [(persistent / 'src' / tree_name, ram / 'src' / tree_name), (persistent / 'seeds', ram / 'seeds'),
             (settings['thinlto_dir'], ram / 'thinlto-cache'), (settings['ccache_dir'], ram / 'ccache')]
    for disk, volatile in pairs:
        if disk == ram or disk in ram.parents or ram in disk.parents or ram_mount(disk):
            raise StorageError(f'Persistent storage must be on disk and separate from RAM workspace: {disk}')
    if ram_mount(settings['packages_dir']):
        raise StorageError('Package destination must be persistent disk storage')
    ram.mkdir(parents=True, exist_ok=True)
    marker = ram / '.unsaved'
    if marker.exists():
        raise StorageError(
            f"Unsaved RAM workspace at {ram}; persistent disk lacked space during checkpoint.\n"
            f"Free up disk space on {persistent}, then finalize saving by running:\n"
            f"  rsync -a --delete --exclude=pacman/ --exclude='*.pkg.tar.*' {ram}/ {persistent}/ && rm {marker}"
        )
    # Previous sessions have checkpointed these trees. Discard only their RAM
    # copies; never sync/delete the persistent src parent containing other builds.
    source_root = ram / 'src'
    source_root.mkdir(exist_ok=True)
    for previous in source_root.iterdir():
        if previous.name != tree_name:
            if previous.is_dir() and not previous.is_symlink():
                shutil.rmtree(previous)
            else:
                previous.unlink()
    note(f'Restoring selected build {tree_name} and shared caches from {persistent} to {ram}')
    for disk, volatile in pairs:
        sync_tree(disk, volatile, run)
    marker.touch()
    try:
        yield ram
    finally:
        note(f'Saving changed build objects and caches to {persistent}; do not reboot until finished')
        try:
            for disk, volatile in pairs:
                sync_tree(volatile, disk, save_run or run)
            marker.unlink(missing_ok=True)
        except Exception as exc:
            note(
                f"\n[ALERT] Persistent storage has insufficient space to save all caches from RAM: {exc}\n"
                f"Your built kernel packages and RAM workspace are safely intact at:\n"
                f"  {ram}\n"
                f"Free up disk space on {persistent}, then finalize saving by running:\n"
                f"  rsync -a --delete --exclude=pacman/ --exclude='*.pkg.tar.*' {ram}/ {persistent}/ && rm {marker}\n"
            )
            raise
