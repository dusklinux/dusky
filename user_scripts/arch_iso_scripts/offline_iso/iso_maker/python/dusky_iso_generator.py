#!/usr/bin/env python3
"""Dusky Arch ISO Factory — offline official + AUR repositories and an archiso releng ISO (x86_64).

Platform: Arch Linux · Linux 7.2+ · Python 3.14+ · pacman 7.1+ · archiso 88+ · rich 15+.
Runs as root (re-execs itself through sudo); git/makepkg run as the invoking user.
"""

import argparse
import atexit
import concurrent.futures as cf
import fcntl
import functools
import hashlib
import io
import json
import os
import pwd
import random
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from compression import zstd  # noqa: F401  PEP 784: tarfile "w:zst" / "r|*" need it; fail fast if absent
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import NoReturn

# ═══════════════════════════════════ configuration ═══════════════════════════════════
VERSION = "8.0.0-py314-2026.09"
REPO_NAME = "archrepo"
DB_NAME = f"{REPO_NAME}.db.tar.zst"
FILES_NAME = f"{REPO_NAME}.files.tar.zst"
AUR_RPC = "https://aur.archlinux.org/rpc/v5/info"
AUR_RPC_BATCH = 80
MAX_RPC_BYTES = 4 << 20
PARALLEL_DOWNLOADS = 5
SYNC_ATTEMPTS = 5
DOWNLOAD_ATTEMPTS = 5
CLONE_ATTEMPTS = 3
SOURCE_ATTEMPTS = 3
MAX_DEFER = 50
CLONE_TIMEOUT_S = 300
BUILD_TIMEOUT_S = 3600
REPO_ADD_MIN_SHARD = 32
COPY_CHUNK = 1 << 20
ZRAM_CANDIDATE = Path("/mnt/zram1")
LOCK_PATH = Path("/run/dusky-iso-factory.lock")
HOST_DB_LOCK = Path("/var/lib/pacman/db.lck")
HOST_MIRRORLIST = Path("/etc/pacman.d/mirrorlist")
RELENG = Path("/usr/share/archiso/configs/releng")
REEXEC_ENV = "DUSKY_FACTORY_REEXEC"
DEFAULT_OFFICIAL = Path("/srv/offline-repo/official")
DEFAULT_AUR = Path("/srv/offline-repo/aur")
ARCH_REPOS = frozenset({
    "core", "extra", "multilib", "core-testing", "extra-testing", "multilib-testing",
    "gnome-unstable", "kde-unstable",
})
EXCLUDED_REPO_PREFIXES = ("cachyos",)  # x86-64-v3/v4 rebuilds: not portable to generic x86_64
VCS_SUFFIXES = ("-git", "-hg", "-svn", "-bzr")
REQUIRED_AUR = frozenset({"paru"})
LIVE_PKG_ALIASES = {"broadcom-wl": "broadcom-wl-dkms"}
BOOTSTRAP_DEPS = {"git": "git", "mkarchiso": "archiso"}

# action -> (official phase, AUR phase, ISO phase); dict order is the interactive menu order
ACTIONS: dict[str, tuple[bool, bool, bool]] = {
    "official_iso": (True, False, True),
    "aur_iso": (False, True, True),
    "full": (True, True, True),
    "iso": (False, False, True),
    "official": (True, False, False),
    "aur": (False, True, False),
    "both": (True, True, False),
}
ACTION_HELP = {
    "official_iso": "official repo + ISO (default)",
    "aur_iso": "AUR repo + ISO",
    "full": "official repo + AUR repo + ISO",
    "iso": "ISO from existing repos",
    "official": "official repo only",
    "aur": "AUR repo only",
    "both": "official + AUR repos, no ISO",
}

PKGNAME_RE = re.compile(r"[a-z0-9@_+][a-z0-9@._+-]*")
PKGFILE_RE = re.compile(r"(?P<name>[^/]+)-(?P<ver>[^-/]+-[^-/]+)-(?P<arch>[^-/]+)\.pkg\.tar(?:\.[a-z0-9]+)?")
DEP_OP_RE = re.compile(r"[<>=]")
SO_DEP_RE = re.compile(r"\.so(?:\.[0-9]+)*$")
CLOSURE_LINE_RE = re.compile(r"^(\S+) (\S+) ([0-9]+)$", re.MULTILINE)
UNSAT_RE = re.compile(r"unable to satisfy dependency '[^']+' required by (\S+)")
NOT_FOUND_RE = re.compile(r"target not found: (\S+)")
MOUNT_ESCAPE_RE = re.compile(r"\\([0-7]{3})")

_FACTORY_MAKEPKG_CONF_TEMPLATE = r'''#!/hint/bash
# shellcheck disable=2034
# Dusky Factory — generic x86_64 AUR builds (ignore host -march=native)
CARCH="x86_64"
CHOST="x86_64-pc-linux-gnu"

CFLAGS="-march=x86-64 -mtune=generic -O2 -pipe -fno-plt -fno-semantic-interposition -fexceptions \
        -Wp,-D_FORTIFY_SOURCE=3 -Wformat -Werror=format-security \
        -fstack-clash-protection -fcf-protection \
        -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer"
CXXFLAGS="$CFLAGS -Wp,-D_GLIBCXX_ASSERTIONS"

FFLAGS="-march=x86-64 -mtune=generic -O2 -pipe -fno-plt -fno-semantic-interposition \
        -Wp,-D_FORTIFY_SOURCE=3 -fstack-clash-protection -fcf-protection \
        -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer"
FCFLAGS="$FFLAGS"

__LDFLAGS_LINE__
LTOFLAGS="-flto=auto"
MAKEFLAGS="-j$(nproc) -l$(( $(nproc) * 3 / 2 ))"
NPROC="$(nproc)"
RUSTFLAGS="__RUSTFLAGS__"
DEBUG_CFLAGS="-g"
DEBUG_CXXFLAGS="$DEBUG_CFLAGS"

BUILDENV=(!distcc color __CCACHE__ !check !sign)
OPTIONS=(strip docs !libtool !staticlibs emptydirs zipman purge !debug lto autodeps)
INTEGRITY_CHECK=(sha256)
STRIP_BINARIES="--strip-all"
STRIP_SHARED="--strip-unneeded"
STRIP_STATIC="--strip-debug"
MAN_DIRS=({usr{,/local}{,/share},opt/*}/{man,info})
DOC_DIRS=(usr/{,local/}{,share/}{doc,gtk-doc} opt/*/{doc,gtk-doc})
PURGE_TARGETS=(usr/{,share}/info/dir .packlist *.pod)
DBGSRCDIR="/usr/src/debug"
LIB_DIRS=('lib:usr/lib' 'lib32:usr/lib32')

DLAGENTS=('file::/usr/bin/curl -qgC - -o %o %u'
          'ftp::/usr/bin/curl -qgfC - --ftp-pasv --retry 3 --retry-delay 3 -o %o %u'
          'http::/usr/bin/curl -qgb "" -fLC - --retry 3 --retry-delay 3 -o %o %u'
          'https::/usr/bin/curl -qgb "" -fLC - --retry 3 --retry-delay 3 -o %o %u'
          'rsync::/usr/bin/rsync --no-motd -z %u %o'
          'scp::/usr/bin/scp -C %u %o')

VCSCLIENTS=('bzr::breezy'
            'fossil::fossil'
            'git::git'
            'hg::mercurial'
            'svn::subversion')

COMPRESSGZ=(gzip -c -f -n)
COMPRESSBZ2=(bzip2 -c -f)
COMPRESSXZ=(xz -c -z -)
COMPRESSZST=(zstd -c -T0 --auto-threads=logical -)
COMPRESSLRZ=(lrzip -q)
COMPRESSLZO=(lzop -q)
COMPRESSZ=(compress -c -f)
COMPRESSLZ4=(lz4 -q)
COMPRESSLZ=(lzip -c -f)

PKGEXT='.pkg.tar.zst'
SRCEXT='.src.tar.gz'
'''

MAKEPKG_ENV_SCRUB = (
    "CFLAGS", "CXXFLAGS", "CPPFLAGS", "FFLAGS", "FCFLAGS", "LDFLAGS", "LTOFLAGS", "RUSTFLAGS",
    "MAKEFLAGS", "NINJAFLAGS", "NPROC", "CARGO_BUILD_RUSTFLAGS", "CARGO_TARGET_CPU", "MAKEPKG_CONF",
    "CARCH", "CHOST", "DEBUG_CFLAGS", "DEBUG_CXXFLAGS", "DEBUG_RUSTFLAGS", "GOAMD64",
)

# Injected at the top of mkarchiso's _build_iso_image(): runs after pacstrap/mksquashfs, before
# xorriso. The staging dir lives in the same workspace filesystem, so this is one rename(2).
MKARCHISO_HOOK = r'''    # --- dusky factory: offline repository (staged + sha256-verified by the factory) ---
    printf '[dusky] INFO: moving offline repository into the ISO 9660 tree\n'
    install -d -m 0755 -- "${isofs_dir}/${install_dir}"
    mv -T -- @STAGING@ "${isofs_dir}/${install_dir}/repo" \
        || { printf '[dusky] ERROR: offline repository injection failed\n' >&2; exit 1; }
'''

ALL_GROUPS: dict[str, list[str]] = {
    "offline": [
        "intel-ucode", "amd-ucode", "linux", "mkinitcpio", "glaze", "python-cssselect", "base", "base-devel",
        "python-lxml", "python-certifi", "python-charset-normalizer", "python-idna",
        "python-requests", "python-urllib3", "deno", "yt-dlp", "yt-dlp-ejs", "hunspell",
        "xf86-input-libinput", "xorg-xauth", "boost-libs", "plymouth", "grub", "os-prober",
        "cryptsetup", "efibootmgr",
    ],
    "graphics": [
        "intel-media-driver", "vpl-gpu-rt", "mesa", "vulkan-intel", "vulkan-radeon", "mesa-utils",
        "intel-gpu-tools", "libva", "libva-utils", "vulkan-icd-loader", "vulkan-tools",
        "sof-firmware", "linux-firmware", "linux-headers", "acpi_call", "kernel-modules-hook",
        "linux-firmware-nvidia", "linux-firmware-amdgpu", "linux-firmware-radeon",
        "linux-firmware-intel", "linux-firmware-mediatek", "linux-firmware-broadcom",
        "linux-firmware-atheros", "linux-firmware-realtek", "linux-firmware-cirrus",
        "linux-firmware-other", "linux-firmware-whence",
    ],
    "hyprland": [
        "hyprland", "xorg-xwayland", "xdg-desktop-portal-hyprland", "xdg-desktop-portal-gtk",
        "localsearch", "polkit", "dbus", "xdg-utils", "socat", "inotify-tools",
        "libnotify", "mako", "file",
    ],
    "appearance": [
        "qt5-wayland", "qt6-wayland", "gtk3", "gtk4", "glib2", "dconf", "nwg-look", "qt5ct", "qt6ct", "qt6-svg",
        "qt6-multimedia-ffmpeg", "adw-gtk-theme", "upower", "plocate", "matugen",
        "otf-font-awesome", "ttf-jetbrains-mono-nerd", "otf-atkinsonhyperlegiblemono-nerd",
        "ttf-atkinson-hyperlegible", "otf-atkinson-hyperlegible",
        "noto-fonts-emoji", "sassc", "python-packaging", "python", "python-gobject",
        "python-cairo", "python-opengl", "gtk-layer-shell", "python-evdev", "python-pyudev",
        "fontconfig", "python-pyquery", "python-textual", "python-rich", "python-pillow", "papirus-icon-theme",
    ],
    "desktop": [
        "awww", "hyprlock", "hypridle", "hyprsunset", "hyprpicker", "rofi", "hyprshutdown",
        "libdbusmenu-qt5", "libdbusmenu-glib", "brightnessctl",
    ],
    "audio": [
        "pipewire", "pipewire-alsa", "alsa-utils", "wireplumber", "pipewire-pulse", "playerctl",
        "bluez", "bluez-utils", "bluez-hid2hci", "bluez-libs", "bluez-obex", "blueman", "bluetui",
        "pavucontrol", "gst-plugins-base", "gst-libav", "gst-plugins-bad", "gst-plugins-good",
        "gst-plugins-ugly", "gst-plugin-pipewire", "libcanberra", "songrec", "sox", "rnnoise",
    ],
    "filesystem": [
        "btrfs-progs", "compsize", "zram-generator", "udisks2", "udiskie", "dosfstools",
        "xdg-user-dirs", "usbutils", "gnome-disk-utility", "unzip", "zip", "tar", "unrar",
        "7zip", "cpio", "file-roller", "rsync", "nfs-utils", "nilfs-utils", "smartmontools",
        "dmraid", "hdparm", "hwdetect", "lsscsi", "sg3_utils", "cpupower", "dust", "dkms",
        "thunar", "thunar-archive-plugin", "thunar-volman", "thunar-media-tags-plugin",
        "thunar-shares-plugin", "thunar-vcs-plugin", "tumbler", "ffmpegthumbnailer",
        "webp-pixbuf-loader", "poppler-glib", "libgsf", "libgepub", "libopenraw", "resvg",
        "gvfs", "gvfs-mtp", "gvfs-nfs", "gvfs-smb", "gvfs-gphoto2", "gvfs-afc", "gvfs-dnssd",
        "catfish", "gnome-keyring", "meld", "xreader", "imagemagick", "kio-admin",
    ],
    "network": [
        "networkmanager", "wireless-regdb", "iwd", "nm-connection-editor", "inetutils", "wget",
        "curl", "openssh", "ufw", "vsftpd", "reflector", "bmon", "ethtool", "httrack", "wavemon",
        "firefox", "nss-mdns", "dnsmasq", "modemmanager", "usb_modeswitch",
    ],
    "terminal": [
        "kitty", "foot", "zsh", "zsh-syntax-highlighting", "starship", "fastfetch", "bat", "eza",
        "fd", "yazi", "gum", "tree", "fzf", "less", "ripgrep", "expac", "zsh-autosuggestions",
        "iperf3", "pkgstats", "libqalculate", "moreutils", "zoxide", "man-db", "lsof", "khal",
    ],
    "dev": [
        "neovim", "git", "git-delta", "lazygit", "meson", "cmake", "clang", "uv", "rq", "jq",
        "pv", "bc", "viu", "chafa", "ueberzugpp", "ccache", "mold", "shellcheck", "shfmt",
        "stylua", "prettier", "tree-sitter-cli", "nano", "luarocks",
    ],
    "multimedia": [
        "ffmpeg", "mpv", "mpv-mpris", "satty", "swayimg", "resvg", "imagemagick", "libheif",
        "ffmpegthumbnailer", "grim", "slurp", "wl-clipboard", "wl-clip-persist", "cliphist",
        "tesseract-data-eng", "gpu-screen-recorder-ui", "ddcutil",
    ],
    "sysadmin": [
        "btop", "htop", "dgop", "nvtop", "inxi", "sysstat", "sysbench", "logrotate", "acpid",
        "tlp", "tlp-rdw", "thermald", "powertop", "gdu", "iotop", "iftop", "lshw", "hwinfo",
        "dmidecode", "strace", "wev", "pacman-contrib", "libsecret", "seahorse", "greetd-agreety",
        "greetd", "greetd-tuigreet", "yad", "dysk", "fwupd", "perl", "accountsservice",
        "pkgfile", "rebuild-detector",
    ],
    "gnome": [
        "snapshot", "cameractrls", "loupe", "mousepad", "gnome-calculator", "gnome-clocks",
    ],
    "productivity": ["zathura", "zathura-pdf-mupdf", "cava"],
    "btrfs": ["snapper"],
}

AUR_SEED: tuple[str, ...] = (
    "wlogout",
    "adwaita-qt6",
    "adwaita-qt5",
    "adwsteamgtk",
    "hyprshade",
    "peaclock",
    "tray-tui",
    "xdg-terminal-exec",
    "paru",
    "waybar-git",
    "papirus-folders",
    "bibata-cursor-theme-bin",
)


# ═══════════════════════ pre-rich: errors, host lock, CLI, bootstrap ═══════════════════════
class FactoryError(Exception):
    """Fatal, user-facing error (reported without a traceback)."""


class Interrupted(BaseException):
    """Raised in the main thread on SIGTERM/SIGHUP (SIGINT raises KeyboardInterrupt)."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def die(msg: str) -> NoReturn:
    raise FactoryError(msg)


def _lock_holders(lock: Path) -> list[int]:
    """PIDs with `lock` open. libalpm keeps db.lck open for the whole transaction, so
    'file exists but no process holds it' is the one reliable staleness signal (covers
    pacman, paru/yay, pamac, PackageKit — any libalpm frontend)."""
    target = os.fspath(lock)
    holders: list[int] = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        fd_dir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(f"{fd_dir}/{fd}") == target:
                    holders.append(int(pid))
                    break
            except OSError:
                continue
    return holders


def wait_for_host_pacman_lock(say: Callable[[str], object], timeout: float = 600.0) -> None:
    if not HOST_DB_LOCK.exists():
        return
    say(f"{HOST_DB_LOCK} present; waiting for its holder...")
    deadline = time.monotonic() + timeout
    unheld = 0
    while HOST_DB_LOCK.exists():
        if _lock_holders(HOST_DB_LOCK):
            unheld = 0
        else:
            unheld += 1
            if unheld >= 2:  # two observations 1 s apart: not the close()->unlink() window of a live pacman
                say("stale pacman lock (no process holds it open); removing")
                HOST_DB_LOCK.unlink(missing_ok=True)
                break
        if time.monotonic() >= deadline:
            die(f"{HOST_DB_LOCK} still held after {int(timeout)} s")
        time.sleep(1.0)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    actions = "\n".join(f"  {name:<13} {ACTION_HELP[name]}" for name in ACTIONS)
    parser = argparse.ArgumentParser(
        prog="dusky_iso_generator.py",
        description="Dusky Arch ISO Factory: offline official + AUR repositories and an archiso "
        "releng ISO (x86_64 only). Re-executes itself as root through sudo.",
        epilog=f"actions:\n{actions}\n\n"
        "environment:\n"
        "  DUSKY_DOTFILES_PIN   commit/ref of github.com/dusklinux/dusky to inject into /etc/skel\n"
        "  DUSKY_DOTFILES_SHA   expected dotfiles HEAD (full sha or prefix); mismatch aborts\n\n"
        f"version {VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        suggest_on_error=True,
    )
    parser.add_argument("--action", choices=list(ACTIONS), help="what to build (default: official_iso)")
    parser.add_argument("--official-repo", type=Path, metavar="DIR", help=f"default: {DEFAULT_OFFICIAL}")
    parser.add_argument("--aur-repo", type=Path, metavar="DIR", help=f"default: {DEFAULT_AUR}")
    parser.add_argument("--workspace", type=Path, metavar="DIR",
                        help=f"ISO workspace base (default: {ZRAM_CANDIDATE} if mounted, else /tmp)")
    parser.add_argument("--source-dir", type=Path, metavar="DIR",
                        help="installer payload (default: ~/user_scripts/arch_iso_scripts/offline_iso)")
    parser.add_argument("--auto", action="store_true", help="non-interactive; default action official_iso")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser.parse_args(argv)


def bootstrap() -> None:
    """Before rich is importable: enforce Arch/x86_64, re-exec as root, install hard deps.
    No `pacman -Sy` here: -Sy followed by -S is a partial upgrade (unsupported on Arch)."""
    if not Path("/etc/arch-release").exists():
        raise SystemExit("[XX] Not on Arch Linux")
    if (machine := os.uname().machine) != "x86_64":
        raise SystemExit(f"[XX] x86_64 only (uname -m = {machine})")
    if os.geteuid() != 0:
        if os.environ.get(REEXEC_ENV) == "1":
            raise SystemExit("[XX] Elevation failed (already re-exec'd once).")
        if not sys.stdin.isatty() and "SUDO_ASKPASS" not in os.environ:
            raise SystemExit("[XX] Root required. Re-run from a TTY or via sudo.")
        print("Elevating privileges to root (may prompt for sudo password)...", flush=True)
        os.execvpe("sudo", ["sudo", "-E", "--", sys.executable, *sys.argv], os.environ | {REEXEC_ENV: "1"})
    missing = sorted({pkg for tool, pkg in BOOTSTRAP_DEPS.items() if shutil.which(tool) is None})
    try:
        import rich  # noqa: F401
    except ImportError:
        missing.append("python-rich")
    if not missing:
        return
    print(f"Installing missing dependencies: {' '.join(missing)}", flush=True)
    try:
        wait_for_host_pacman_lock(lambda m: print(f"[!!] {m}", flush=True))
    except FactoryError as exc:
        raise SystemExit(f"[XX] {exc}") from None
    if subprocess.run(["pacman", "-S", "--needed", "--noconfirm", "--", *missing]).returncode != 0:
        raise SystemExit("[XX] pacman -S failed (stale host sync DB? run: pacman -Syu)")
    if "python-rich" in missing:
        os.execve(sys.executable, [sys.executable, *sys.argv], os.environ | {REEXEC_ENV: "1"})


if __name__ == "__main__":
    ARGS = parse_args(sys.argv[1:])
    bootstrap()

from rich import box  # noqa: E402  (imported after bootstrap may have installed python-rich)
from rich.console import Console  # noqa: E402
from rich.markup import escape  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn  # noqa: E402
from rich.prompt import Confirm, Prompt  # noqa: E402
from rich.table import Table  # noqa: E402

console = Console(highlight=False)


def info(msg: str) -> None:
    console.print(f"\n[bold cyan]==>[/] {escape(msg)}")


def step(msg: str) -> None:
    console.print(f"  [bold magenta]->[/] {escape(msg)}")


def ok(msg: str) -> None:
    console.print(f"[bold green]\\[OK][/] {escape(msg)}")


def warn(msg: str) -> None:
    console.print(f"[bold yellow]\\[!!][/] {escape(msg)}")


def err(msg: str) -> None:
    console.print(f"[bold red]\\[XX][/] {escape(msg)}")


def human_bytes(n: int) -> str:
    f = float(max(n, 0))
    for unit in ("B", "KiB", "MiB", "GiB"):
        if f < 1024:
            return f"{int(f)} B" if unit == "B" else f"{f:.2f} {unit}"
        f /= 1024
    return f"{f:.2f} TiB"


def format_duration(seconds: float) -> str:
    sec = max(0, int(seconds))
    if sec < 60:
        return f"{sec}s"
    mins, sec = divmod(sec, 60)
    if mins < 60:
        return f"{mins}m {sec}s"
    hours, mins = divmod(mins, 60)
    return f"{hours}h {mins}m {sec}s"


def backoff(attempt: int, cap: float = 30.0) -> None:
    time.sleep(min(cap, 2.0**attempt + random.uniform(0, 1)))


def require_tool(name: str, hint: str = "") -> None:
    if shutil.which(name) is None:
        die(f"missing tool: {name}" + (f" ({hint})" if hint else ""))


# ═══════════════════════════════ process control ═══════════════════════════════
# Every child runs in its own process group (process_group=0). On error/interrupt/timeout the
# whole group (makepkg -> make -> cc, mkarchiso -> pacstrap -> pacman, ...) is terminated, so
# no grandchild keeps writing into directories that cleanup is about to delete.
_children: set[subprocess.Popen[str]] = set()
_children_lock = threading.RLock()  # re-entrant: the signal handler runs on the main thread, which may hold it
_interrupted = False


def _signal_group(proc: subprocess.Popen[str], sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def _kill_group(proc: subprocess.Popen[str], grace: float = 10.0) -> None:
    _signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    _signal_group(proc, signal.SIGKILL)  # stragglers that ignored SIGTERM
    proc.wait()


def terminate_children() -> None:
    with _children_lock:
        procs = tuple(_children)
    for proc in procs:
        _signal_group(proc, signal.SIGTERM)


def _on_signal(signum: int, _frame: object) -> None:
    global _interrupted
    terminate_children()
    if _interrupted:  # second signal: keep cleanup running, just re-signal children
        return
    _interrupted = True
    if signum == signal.SIGINT:
        raise KeyboardInterrupt
    raise Interrupted(signum)


def run(
    cmd: Sequence[str | os.PathLike[str]],
    *,
    user: "RealUser | None" = None,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    capture: bool = False,
    merge: bool = False,
    log: Path | None = None,
    timeout: float | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a child with stdin=/dev/null in its own process group; `user` drops privileges in the
    child via setgroups/setregid/setreuid (no runuser/PAM process per call)."""
    argv = [os.fspath(c) for c in cmd]
    creds: dict[str, object] = {}
    if user is not None and (user.uid != os.geteuid() or user.gid != os.getegid()):
        creds = {"user": user.uid, "group": user.gid, "extra_groups": list(user.groups)}
    log_fh = open(log, "ab", buffering=0) if log is not None else None
    try:
        if log_fh is not None:
            stdout, stderr = log_fh, subprocess.STDOUT
        else:
            stdout = subprocess.PIPE if capture else None
            stderr = subprocess.STDOUT if merge else (subprocess.PIPE if capture else None)
        with subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, cwd=cwd, env=env,
            encoding="utf-8", errors="replace", process_group=0, **creds,
        ) as proc:
            with _children_lock:
                _children.add(proc)
            try:
                out, errout = proc.communicate(timeout=timeout)
            except BaseException:
                _kill_group(proc)
                raise
            finally:
                with _children_lock:
                    _children.discard(proc)
    finally:
        if log_fh is not None:
            log_fh.close()
    result = subprocess.CompletedProcess(argv, proc.returncode, out, errout)
    if check and result.returncode != 0:
        detail = f"{errout or ''}{out or ''}".strip()[-2000:]
        die(f"command failed (exit {result.returncode}): {shlex.join(argv)}" + (f"\n{detail}" if detail else ""))
    return result


def parallel_map[T, R](fn: Callable[[T], R], items: Sequence[T], workers: int) -> list[R]:
    """ThreadPoolExecutor.map that cancels queued work when the caller is interrupted."""
    ex = cf.ThreadPoolExecutor(max_workers=max(1, min(workers, len(items) or 1)))
    try:
        return list(ex.map(fn, items))
    except BaseException:
        ex.shutdown(wait=True, cancel_futures=True)
        raise
    finally:
        ex.shutdown(wait=True)


# ═══════════════════════════════════ users ═══════════════════════════════════
@dataclass(frozen=True, slots=True)
class RealUser:
    name: str
    uid: int
    gid: int
    home: Path
    groups: tuple[int, ...]

    @property
    def is_root(self) -> bool:
        return self.uid == 0


@functools.cache
def real_user() -> RealUser:
    pw: pwd.struct_passwd | None = None
    if (sudo_user := os.environ.get("SUDO_USER")) and sudo_user != "root":
        try:
            pw = pwd.getpwnam(sudo_user)
        except KeyError:
            pw = None
    if pw is None:
        try:
            login = os.getlogin()
            if login != "root":
                pw = pwd.getpwnam(login)
        except (OSError, KeyError):
            pw = None
    if pw is None:
        pw = pwd.getpwuid(os.getuid())
    return RealUser(pw.pw_name, pw.pw_uid, pw.pw_gid, Path(pw.pw_dir),
                    tuple(os.getgrouplist(pw.pw_name, pw.pw_gid)))


def user_env(user: RealUser, **extra: str) -> dict[str, str]:
    env = os.environ | {
        "HOME": str(user.home), "USER": user.name, "LOGNAME": user.name,
        "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
    }
    env.setdefault("GIT_ASKPASS", "/bin/true")
    return env | extra


def notify(title: str, msg: str, icon: str = "dialog-information") -> None:
    """Fire-and-forget desktop notification (never blocks cleanup on a dbus activation timeout)."""
    if (exe := shutil.which("notify-send")) is None:
        return
    user = real_user()
    env = dict(os.environ)
    creds: dict[str, object] = {}
    if not user.is_root:
        runtime = f"/run/user/{user.uid}"
        env |= {"HOME": str(user.home), "USER": user.name, "LOGNAME": user.name, "XDG_RUNTIME_DIR": runtime}
        if os.path.exists(f"{runtime}/bus"):
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime}/bus"
        creds = {"user": user.uid, "group": user.gid, "extra_groups": list(user.groups)}
    try:
        subprocess.Popen([exe, "-a", "Dusky Factory", "-i", icon, title, msg], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
                         start_new_session=True, **creds)
    except OSError:
        pass


# ═══════════════════════════════ filesystem ═══════════════════════════════
_cleanup_paths: list[Path] = []
_lock_fd: int | None = None


def assert_conf_safe(path: Path) -> None:
    """Paths end up in pacman.conf (Server/CacheDir/DBPath): whitespace or '#' changes parsing."""
    s = os.fspath(path)
    if not s or any(c.isspace() or c == "#" or ord(c) < 32 for c in s):
        die(f"path unusable in pacman.conf (whitespace, '#' or control chars): {s!r}")


def make_tempdir(prefix: str, *, owner: RealUser | None = None) -> Path:
    path = Path(tempfile.mkdtemp(prefix=prefix))  # created 0700
    _cleanup_paths.append(path)
    if owner is not None:
        os.chown(path, owner.uid, owner.gid)
    return path


def mounts_under(path: Path) -> list[str]:
    root = os.path.realpath(path)
    prefix = root.rstrip("/") + "/"
    found: list[str] = []
    with open("/proc/self/mountinfo", encoding="utf-8", errors="surrogateescape") as fh:
        for line in fh:
            mp = MOUNT_ESCAPE_RE.sub(lambda m: chr(int(m[1], 8)), line.split(" ", 5)[4])
            if mp == root or mp.startswith(prefix):
                found.append(mp)
    return found


def remove_tree(path: Path, *, strict: bool = False) -> None:
    """rmtree that never descends into mounts (an aborted pacstrap leaves proc/sys/dev binds)."""
    if not os.path.lexists(path):
        return
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
        return
    if mps := mounts_under(path):
        tops = sorted({m for m in mps if not any(m != o and m.startswith(o.rstrip("/") + "/") for o in mps)})
        warn(f"unmounting {len(mps)} mount(s) under {path}")
        run(["umount", "--recursive", "--lazy", "--", *tops])
        if mounts_under(path):
            die(f"refusing to delete {path}: mounts remain beneath it")
    shutil.rmtree(path, ignore_errors=not strict)


def run_cleanups() -> None:
    while _cleanup_paths:
        path = _cleanup_paths.pop()
        try:
            remove_tree(path)
        except Exception as exc:  # atexit context: report, never raise
            print(f"[!!] cleanup of {path} failed: {exc}", file=sys.stderr)


@contextmanager
def atomic_path(dest: Path, *, durable: bool = True) -> Iterator[Path]:
    """Yield a sibling temp path; on success fsync (optional) and rename over `dest`."""
    tmp = dest.with_name(f".{dest.name}.{secrets.token_hex(4)}.tmp")
    try:
        yield tmp
        if durable:
            fd = os.open(tmp, os.O_RDONLY | os.O_CLOEXEC)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def drop_page_cache(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        return
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)


def ensure_disk_space(path: Path, need: int, label: str) -> None:
    probe = path
    while not probe.exists():
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < need:
        die(f"insufficient space for {label} at {path}: need ~{human_bytes(need)}, free {human_bytes(free)}")


def restore_ownership(root: Path, user: RealUser) -> None:
    """chown -R equivalent that only touches inodes whose owner differs (no ctime/journal churn
    on the thousands of files that are already correct)."""
    if user.is_root or not root.exists():
        return
    uid, gid = user.uid, user.gid
    changed = 0

    def fix(path: str, st: os.stat_result) -> None:
        nonlocal changed
        if st.st_uid != uid or st.st_gid != gid:
            os.chown(path, uid, gid, follow_symlinks=False)
            changed += 1

    fix(os.fspath(root), root.lstat())
    stack = [os.fspath(root)]
    while stack:
        with os.scandir(stack.pop()) as it:
            for entry in it:
                fix(entry.path, entry.stat(follow_symlinks=False))
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
    if changed:
        step(f"ownership -> {user.name}: {changed} inode(s) under {root}")


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return -1


def tail_text(path: Path, nbytes: int = 3000) -> str:
    try:
        with open(path, "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            fh.seek(max(0, size - nbytes))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def acquire_factory_lock() -> None:
    global _lock_fd
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        die(f"another factory instance holds {LOCK_PATH}")
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    _lock_fd = fd  # released by the kernel when the process exits


# ═══════════════════════════════ versions & names ═══════════════════════════════
_DIGIT = frozenset("0123456789")
_ALPHA = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_ALNUM = _DIGIT | _ALPHA


def _rpmvercmp(a: str, b: str) -> int:
    """Port of libalpm rpmvercmp() (lib/libalpm/version.c), ASCII classes like the C locale."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    i = j = 0
    while i < la and j < lb:
        pi, pj = i, j
        while i < la and a[i] not in _ALNUM:
            i += 1
        while j < lb and b[j] not in _ALNUM:
            j += 1
        if i >= la or j >= lb:
            break
        if i - pi != j - pj:  # differing separator lengths decide
            return -1 if i - pi < j - pj else 1
        cls = _DIGIT if a[i] in _DIGIT else _ALPHA
        ei, ej = i, j
        while ei < la and a[ei] in cls:
            ei += 1
        while ej < lb and b[ej] in cls:
            ej += 1
        if ej == j:  # segment types differ: numeric is newer than alpha
            return 1 if cls is _DIGIT else -1
        sa, sb = a[i:ei], b[j:ej]
        if cls is _DIGIT:
            sa, sb = sa.lstrip("0"), sb.lstrip("0")
            if len(sa) != len(sb):
                return 1 if len(sa) > len(sb) else -1
        if sa != sb:
            return 1 if sa > sb else -1
        i, j = ei, ej
    if i >= la and j >= lb:
        return 0
    # "the final showdown": a remaining alpha segment never beats an empty string
    if (i >= la and not (j < lb and b[j] in _ALPHA)) or (i < la and a[i] in _ALPHA):
        return -1
    return 1


def _split_evr(evr: str) -> tuple[str, str, str | None]:
    """Port of libalpm parseEVR(): [epoch:]version[-release]."""
    k, n = 0, len(evr)
    while k < n and evr[k] in _DIGIT:
        k += 1
    dash = evr.rfind("-", k)
    if k < n and evr[k] == ":":
        epoch, start = evr[:k] or "0", k + 1
    else:
        epoch, start = "0", 0
    if dash == -1:
        return epoch, evr[start:], None
    return epoch, evr[start:dash], evr[dash + 1:]


def vercmp(a: str, b: str) -> int:
    """alpm_pkg_vercmp() semantics (what `vercmp` and pacman use); -1, 0 or 1."""
    if a == b:
        return 0
    e1, v1, r1 = _split_evr(a)
    e2, v2, r2 = _split_evr(b)
    if c := _rpmvercmp(e1, e2):
        return c
    if c := _rpmvercmp(v1, v2):
        return c
    return _rpmvercmp(r1, r2) if r1 is not None and r2 is not None else 0


def parse_pkg_filename(name: str) -> tuple[str, str, str] | None:
    """'name-[epoch:]ver-rel-arch.pkg.tar[.ext]' -> (name, 'epoch:ver-rel', arch)."""
    m = PKGFILE_RE.fullmatch(name)
    return (m["name"], m["ver"], m["arch"]) if m else None


def dep_name(dep: str) -> str:
    return DEP_OP_RE.split(dep, maxsplit=1)[0].strip()


def is_aur_candidate(name: str) -> bool:
    """Library/pkgconfig provides are never AUR package names."""
    return bool(PKGNAME_RE.fullmatch(name)) and not SO_DEP_RE.search(name)


def newest_files(filenames: Iterable[str], arches: Collection[str] = ("x86_64", "any")) -> dict[str, str]:
    """pkgname -> filename of its newest version (vercmp), ignoring foreign arches."""
    best: dict[str, tuple[str, str]] = {}
    for fn in filenames:
        if (parsed := parse_pkg_filename(fn)) is None or parsed[2] not in arches:
            continue
        name, ver, _ = parsed
        if (cur := best.get(name)) is None or vercmp(ver, cur[0]) > 0:
            best[name] = (ver, fn)
    return {name: fn for name, (_, fn) in best.items()}


def package_files(repo: Path) -> list[str]:
    if not repo.is_dir():
        return []
    with os.scandir(repo) as it:
        return [e.name for e in it if PKGFILE_RE.fullmatch(e.name) and e.is_file(follow_symlinks=False)]


# ═══════════════════════════════ repository databases ═══════════════════════════════
# A repo DB is a tar of "<name>-<ver>/desc" (+ "<name>-<ver>/files" in the .files DB). The .files
# DB is a superset of the .db, so reading it yields everything needed to rewrite both.
_DB_KEYS = frozenset({"NAME", "VERSION", "FILENAME", "CSIZE", "SHA256SUM", "PROVIDES"})
_INDEX_KEYS = frozenset({"NAME", "PROVIDES", "GROUPS"})


def desc_fields(data: bytes, wanted: frozenset[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for block in data.decode("utf-8", "replace").split("\n\n"):
        head, _, body = block.strip("\n").partition("\n")
        if len(head) > 2 and head[0] == "%" == head[-1] and (key := head[1:-1]) in wanted:
            out[key] = body.split("\n") if body else []
    return out


@dataclass(slots=True)
class DbEntry:
    name: str
    version: str
    filename: str
    csize: int
    sha256: str
    provides: tuple[str, ...]
    members: list[tuple[tarfile.TarInfo, bytes | None]]  # directory + desc (+ files)


def read_repo_db(path: Path) -> dict[str, DbEntry]:
    """Parse a repo DB in one sequential pass (stream mode; gzip/zstd auto-detected)."""
    groups: dict[str, list[tuple[tarfile.TarInfo, bytes | None]]] = {}
    with tarfile.open(path, "r|*") as tf:
        for member in tf:
            data: bytes | None = None
            if member.isfile():
                fh = tf.extractfile(member)
                data = fh.read() if fh is not None else b""
            groups.setdefault(member.name.split("/", 1)[0], []).append((member, data))
    entries: dict[str, DbEntry] = {}
    for top, members in groups.items():
        desc = next((d for m, d in members if d is not None and m.name.endswith("/desc")), None)
        if desc is None:
            continue
        f = desc_fields(desc, _DB_KEYS)
        try:
            entry = DbEntry(
                name=f["NAME"][0], version=f["VERSION"][0], filename=f["FILENAME"][0],
                csize=int(f["CSIZE"][0]), sha256=f["SHA256SUM"][0].lower(),
                provides=tuple(dep_name(p) for p in f.get("PROVIDES", ())), members=members,
            )
        except (KeyError, IndexError, ValueError) as exc:
            die(f"{path}: malformed desc for {top!r} ({exc!r})")
        entries[entry.name] = entry
    return entries


def load_db_by_filename(repo: Path) -> dict[str, DbEntry]:
    path = repo / FILES_NAME
    if not path.is_file():
        return {}
    try:
        return {e.filename: e for e in read_repo_db(path).values()}
    except Exception as exc:  # self-heal: an unreadable DB is simply rebuilt
        warn(f"{path} unreadable ({exc}); rebuilding from scratch")
        return {}


def write_repo_db(repo: Path, entries: Iterable[DbEntry], *, durable: bool = True) -> None:
    """Write archrepo.db.tar.zst (desc only) and archrepo.files.tar.zst (all members) atomically,
    plus the archrepo.db / archrepo.files symlinks pacman fetches."""
    ordered = sorted(entries, key=lambda e: e.name)
    with atomic_path(repo / DB_NAME, durable=durable) as db_tmp, \
            atomic_path(repo / FILES_NAME, durable=durable) as files_tmp:
        with tarfile.open(db_tmp, "w:zst") as db_tf, tarfile.open(files_tmp, "w:zst") as files_tf:
            for entry in ordered:
                for info_, data in entry.members:
                    files_tf.addfile(info_, None if data is None else io.BytesIO(data))
                    if data is None or info_.name.endswith("/desc"):
                        db_tf.addfile(info_, None if data is None else io.BytesIO(data))
    for link, target in ((f"{REPO_NAME}.db", DB_NAME), (f"{REPO_NAME}.files", FILES_NAME)):
        tmp_link = repo / f".{link}.{secrets.token_hex(4)}.tmp"
        os.symlink(target, tmp_link)
        os.replace(tmp_link, repo / link)
    if durable:
        fsync_dir(repo)


def repo_add_entries(paths: Sequence[Path]) -> dict[str, DbEntry]:
    """Index package files with repo-add, sharded across CPUs. repo-add is Bash and serial per
    package (bsdtar .PKGINFO read + per-line subshells, sha256sum, full-archive bsdtar -t for the
    files list), so N independent shard DBs merged in Python scale ~linearly with cores."""
    if not paths:
        return {}
    shards = max(1, min(os.process_cpu_count() or 1, len(paths) // REPO_ADD_MIN_SHARD))
    work = make_tempdir("dusky-repoadd-")

    def index_shard(i: int) -> dict[str, DbEntry]:
        db = work / f"s{i}.db.tar.zst"
        r = run(["repo-add", "--quiet", "--nocolor", db, *paths[i::shards]], capture=True, merge=True)
        if r.returncode != 0:
            die(f"repo-add failed (shard {i}):\n{r.stdout[-2000:]}")
        return read_repo_db(work / f"s{i}.files.tar.zst")

    try:
        parts = parallel_map(index_shard, list(range(shards)), shards)
    finally:
        remove_tree(work)
    merged: dict[str, DbEntry] = {}
    for part in parts:
        for name, entry in part.items():
            if name in merged:
                die(f"two files for package {name}: {merged[name].filename} / {entry.filename}")
            merged[name] = entry
    return merged


def update_repo_db(repo: Path, want: Collection[str], old: dict[str, DbEntry] | None = None,
                   force_write: bool = False) -> dict[str, DbEntry]:
    """Make the repo DB describe exactly `want` (filenames in `repo`). Entries whose filename and
    size still match are reused; only new files go through repo-add; unchanged DBs are not
    rewritten at all."""
    if old is None:
        old = load_db_by_filename(repo)
    keep: dict[str, DbEntry] = {}
    fresh: list[Path] = []
    for fn in sorted(want):
        entry = old.get(fn)
        path = repo / fn
        size = file_size(path)
        if size < 0:
            die(f"{fn} is missing from {repo}")
        if entry is not None and size == entry.csize:
            keep[entry.name] = entry
        else:
            fresh.append(path)
    links_ok = all(os.path.lexists(repo / n) for n in (DB_NAME, FILES_NAME, f"{REPO_NAME}.db", f"{REPO_NAME}.files"))
    if not force_write and not fresh and len(keep) == len(old) and links_ok:
        ok(f"{repo.name} DB unchanged ({len(keep)} packages)")
        return keep
    if fresh:
        t0 = time.perf_counter()
        for name, entry in repo_add_entries(fresh).items():
            if name in keep:
                die(f"two files for package {name}: {keep[name].filename} / {entry.filename}")
            keep[name] = entry
        step(f"repo-add indexed {len(fresh)} new file(s) in {time.perf_counter() - t0:.1f}s; "
             f"reused {len(keep) - len(fresh)} entries")
    write_repo_db(repo, keep.values())
    ok(f"{repo.name} DB written ({len(keep)} packages)")
    return keep


def ensure_repo_db(repo: Path) -> bool:
    """True if `repo` has a DB afterwards (indexing its newest files when the DB is missing or out of sync)."""
    files = set(newest_files(package_files(repo)).values())
    if not files:
        return False
    if (repo / FILES_NAME).is_file():
        old = load_db_by_filename(repo)
        if set(old) == files:
            return True
        warn(f"{repo}: DB out of sync with disk ({len(files)} files on disk, {len(old)} in DB); updating index")
        update_repo_db(repo, files, old, force_write=True)
        return True
    warn(f"{repo}: no {FILES_NAME}; indexing the newest file of every package")
    update_repo_db(repo, files, {}, force_write=True)
    return True


def prune_repo(repo: Path, keep: Collection[str]) -> None:
    """Delete package files (and their .sig) not in `keep`, stale .part files and crashed temp files."""
    keep_all = set(keep) | {f"{fn}.sig" for fn in keep}
    removed = freed = 0
    with os.scandir(repo) as it:
        for entry in it:
            name = entry.name
            if name in keep_all or not entry.is_file(follow_symlinks=False):
                continue
            base = name.removesuffix(".sig")
            if (PKGFILE_RE.fullmatch(base) or name.endswith(".part")
                    or (name.startswith(".") and name.endswith(".tmp"))):
                freed += entry.stat(follow_symlinks=False).st_size
                os.unlink(entry.path)
                removed += 1
    if removed:
        ok(f"pruned {removed} file(s) from {repo}, freed {human_bytes(freed)}")


# ═══════════════════════════════ pacman helpers ═══════════════════════════════
type ConfSections = list[tuple[str, list[tuple[str, str]]]]


def parse_pacman_conf(text: str) -> ConfSections:
    """Parse `pacman-conf` output (Includes resolved, $repo/$arch expanded by pacman itself)."""
    sections: ConfSections = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            sections.append((line[1:-1], []))
        elif sections:
            key, _, value = line.partition("=")
            sections[-1][1].append((key.strip(), value.strip()))
    return sections


@functools.cache
def host_conf() -> dict[str, list[tuple[str, str]]]:
    return dict(parse_pacman_conf(run(["pacman-conf"], capture=True, check=True).stdout))


def conf_values(items: Iterable[tuple[str, str]], key: str) -> list[str]:
    return [v for k, v in items if k == key]


def pacman_errors(r: subprocess.CompletedProcess[str], limit: int = 1500) -> str:
    text = f"{r.stderr or ''}\n{r.stdout or ''}"
    lines = [ln for ln in text.splitlines() if ln.startswith(("error:", "warning:", ":: unable", "::"))]
    return ("\n".join(lines) or text.strip())[-limit:]


def host_pacman(*args: str) -> subprocess.CompletedProcess[str]:
    """Host transaction (-S/-U): wait for db.lck; retry if another frontend grabbed it first."""
    r: subprocess.CompletedProcess[str] | None = None
    for _ in range(3):
        wait_for_host_pacman_lock(warn)
        r = run(["pacman", "--noconfirm", "--noprogressbar", "--color", "never", *args], capture=True, merge=True)
        if r.returncode == 0 or "unable to lock database" not in (r.stdout or ""):
            return r
        time.sleep(2)
    assert r is not None
    return r


@dataclass(frozen=True, slots=True)
class SyncIndex:
    names: frozenset[str]
    provides: frozenset[str]
    groups: frozenset[str]

    def has(self, name: str) -> bool:
        return name in self.names or name in self.provides or name in self.groups


def load_sync_index(dbs: Iterable[Path]) -> SyncIndex:
    """names/provides/groups of sync DBs parsed once in-process — replaces one pacman process
    (full sync-DB load each) per dependency lookup."""
    names: set[str] = set()
    provides: set[str] = set()
    groups: set[str] = set()
    for db in dbs:
        with tarfile.open(db, "r|*") as tf:
            for member in tf:
                if not member.name.endswith("/desc") or (fh := tf.extractfile(member)) is None:
                    continue
                f = desc_fields(fh.read(), _INDEX_KEYS)
                names.update(f.get("NAME", ()))
                provides.update(dep_name(p) for p in f.get("PROVIDES", ()))
                groups.update(f.get("GROUPS", ()))
    return SyncIndex(frozenset(names), frozenset(provides), frozenset(groups))


type Closure = list[tuple[str, str, int]]  # (repo, filename, bytes still to download)


def parse_closure(text: str) -> Closure:
    return [(repo, fn, int(size)) for repo, fn, size in CLOSURE_LINE_RE.findall(text) if PKGFILE_RE.fullmatch(fn)]


class IsolatedDB:
    """Private pacman DBPath (empty local DB => full dependency closures), generated from the
    host configuration via pacman-conf. Shared by the official and AUR phases."""

    def __init__(self) -> None:
        self.root = make_tempdir("dusky-isolate-")
        assert_conf_safe(self.root)
        for sub in ("sync", "local"):
            (self.root / sub).mkdir()
        self.conf = self.root / "pacman.conf"
        self.repos: list[str] = []
        self.index = SyncIndex(frozenset(), frozenset(), frozenset())
        self._local_repo: Path | None = None

    def setup(self) -> None:
        self._write_conf()
        self._sync()
        t0 = time.perf_counter()
        self.index = load_sync_index(self.root / "sync" / f"{r}.db" for r in self.repos)
        step(f"sync index: {len(self.index.names)} packages, {len(self.index.provides)} provides "
             f"({time.perf_counter() - t0:.2f}s)")

    def close(self) -> None:
        remove_tree(self.root)

    def _write_conf(self) -> None:
        conf = host_conf()
        opts = conf.get("options", [])
        mirrorlist = self.root / "mirrorlist"
        if HOST_MIRRORLIST.is_file():
            shutil.copyfile(HOST_MIRRORLIST, mirrorlist)
        if shutil.which("reflector"):
            tmp = self.root / "mirrorlist.reflector"
            r = run(["reflector", "--latest", "20", "--protocol", "https", "--download-timeout", "3",
                     "--save", tmp], capture=True, merge=True)
            if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 100:
                os.replace(tmp, mirrorlist)
                step("isolated mirrorlist: reflector (20 freshest HTTPS mirrors)")
            else:
                step("reflector failed; using the host mirrorlist")
        lines = [
            "[options]",
            f"DBPath = {self.root}/",
            f"LogFile = {self.root}/pacman.log",  # never log isolated operations into the host log
            f"GPGDir = {(conf_values(opts, 'GPGDir') or ['/etc/pacman.d/gnupg/'])[0]}",
            "Architecture = x86_64",
            f"ParallelDownloads = {PARALLEL_DOWNLOADS}",
        ]
        for key in ("SigLevel", "LocalFileSigLevel", "RemoteFileSigLevel"):
            if values := conf_values(opts, key):
                lines.append(f"{key} = {' '.join(values)}")
        for name, items in conf.items():
            if name == "options" or name == REPO_NAME or name.startswith(EXCLUDED_REPO_PREFIXES):
                continue
            lines.append(f"[{name}]")
            lines += [f"{k} = {v}" for k, v in items if k in ("Usage", "SigLevel")]
            if name in ARCH_REPOS and mirrorlist.is_file():
                lines.append(f"Include = {mirrorlist}")
            else:
                lines += [f"{k} = {v}" for k, v in items if k in ("Server", "CacheServer")]
            self.repos.append(name)
        if not ARCH_REPOS.intersection(self.repos):
            die("host pacman.conf enables no official Arch repository")
        self.conf.write_text("\n".join(lines) + "\n", encoding="utf-8")
        step(f"isolated pacman.conf: {self.conf} (repos: {' '.join(self.repos)})")

    def pacman(self, *args: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
        color = "never" if capture else "auto"
        return run(["pacman", "--config", self.conf, "--noconfirm", "--color", color, *args], capture=capture)

    def _sync(self) -> None:
        for attempt in range(1, SYNC_ATTEMPTS + 1):
            step(f"syncing isolated DB (attempt {attempt}/{SYNC_ATTEMPTS})")
            r = self.pacman("-Sy", "--noprogressbar")
            if r.returncode == 0:
                ok("isolated DB synced")
                return
            warn(f"sync failed: {pacman_errors(r, 500)}")
            if attempt < SYNC_ATTEMPTS:
                backoff(attempt)
        die("isolated DB sync failed — check network/keyring")

    def resolve_names(self, names: Iterable[str]) -> tuple[list[str], list[str]]:
        official: list[str] = []
        unresolved: list[str] = []
        for n in names:
            (official if self.index.has(n) else unresolved).append(n)
        return official, unresolved

    @staticmethod
    def _cache_args(cachedirs: Sequence[Path]) -> list[str]:
        return [arg for d in cachedirs for arg in ("--cachedir", os.fspath(d))]

    def _closure_raw(self, targets: Sequence[str], cachedirs: Sequence[Path]) -> subprocess.CompletedProcess[str]:
        # -w sets ALPM_TRANS_FLAG_NOCONFLICTS: resolve dependencies only, like the real download.
        # One transaction for the whole target list: consistent provider selection, one DB load.
        return self.pacman("-Swp", "--noprogressbar", "--print-format", "%r %f %s",
                           *self._cache_args(cachedirs), "--", *targets)

    def closure(self, targets: Sequence[str], cachedirs: Sequence[Path]) -> Closure:
        r = self._closure_raw(targets, cachedirs)
        if r.returncode != 0:
            die(f"dependency resolution failed:\n{pacman_errors(r)}")
        return parse_closure(r.stdout)

    def closure_tolerant(self, targets: Sequence[str], cachedirs: Sequence[Path]) -> tuple[Closure, list[str]]:
        """Closure of the satisfiable subset of `targets`; returns (closure, dropped targets)."""
        remaining = list(targets)
        dropped: list[str] = []
        while remaining:
            r = self._closure_raw(remaining, cachedirs)
            if r.returncode == 0:
                return parse_closure(r.stdout), dropped
            text = f"{r.stderr}\n{r.stdout}"
            bad = ({m[1] for m in UNSAT_RE.finditer(text)} | {m[1] for m in NOT_FOUND_RE.finditer(text)})
            bad &= set(remaining)
            if not bad:  # failure caused by a transitive dependency: probe targets individually
                bad = {t for t in remaining if self._closure_raw([t], cachedirs).returncode != 0}
            if not bad:
                die(f"dependency resolution failed:\n{pacman_errors(r)}")
            for name in sorted(bad):
                warn(f"{name}: runtime dependencies unsatisfiable offline — excluded from closure")
            dropped += sorted(bad)
            remaining = [t for t in remaining if t not in bad]
        return [], dropped

    def download(self, targets: Sequence[str], cachedirs: Sequence[Path]) -> None:
        """pacman -Sw fetches what is missing, then signature/checksum-validates EVERY package of
        the transaction (cached ones included) before returning 0 — no second verification pass."""
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            info(f"pacman -Sw attempt {attempt}/{DOWNLOAD_ATTEMPTS} ({len(targets)} targets)")
            r = self.pacman("-Sw", *self._cache_args(cachedirs), "--", *targets, capture=False)
            if r.returncode == 0:
                return
            if attempt < DOWNLOAD_ATTEMPTS:
                backoff(attempt)
        die("package download failed after retries")

    def attach_local_repo(self, repo: Path) -> None:
        """Expose `repo` as [archrepo] without a network sync: its DB is copied into sync/."""
        assert_conf_safe(repo)
        shutil.copyfile(repo / DB_NAME, self.root / "sync" / f"{REPO_NAME}.db")
        if self._local_repo is None:
            with open(self.conf, "a", encoding="utf-8") as fh:
                fh.write(f"[{REPO_NAME}]\nSigLevel = Optional TrustAll\nServer = file://{repo}\n")
            self._local_repo = repo
        elif self._local_repo != repo:
            die("isolated DB already has a different local repository attached")


# ═══════════════════════════════ master list & official phase ═══════════════════════════════
def build_master_list(external: Path | None) -> list[str]:
    seen: set[str] = set()
    master: list[str] = []
    table = Table(title="Package Groups", box=box.SIMPLE)
    table.add_column("Group", style="magenta")
    table.add_column("Count", style="cyan")
    table.add_column("Unique", style="green")
    for group, pkgs in ALL_GROUPS.items():
        new = 0
        for p in pkgs:
            if not PKGNAME_RE.fullmatch(p):
                warn(f"invalid package name {p!r} in {group}")
            elif p not in seen:
                seen.add(p)
                master.append(p)
                new += 1
        table.add_row(group, str(len(pkgs)), str(new))
    console.print(table)
    if external is not None and external.is_file():
        try:
            added = 0
            for raw in external.read_text(encoding="utf-8").splitlines():
                pkg = raw.split("#", 1)[0].strip()
                if pkg and PKGNAME_RE.fullmatch(pkg) and pkg not in seen:
                    seen.add(pkg)
                    master.append(pkg)
                    added += 1
            step(f"external list {external}: {added} unique")
        except (OSError, UnicodeDecodeError) as exc:
            warn(f"external list unreadable: {exc}")
    if not master:
        die("master package list empty")
    ok(f"master list: {len(master)} unique names")
    return master


def ingest_host_cache_packages(repo: Path, files: Collection[str]) -> int:
    """If packages needed by the closure are already in the host pacman cache, copy them into repo
    so pacman -Sw does not redownload them over the internet."""
    host_caches = [Path(d) for d in conf_values(host_conf().get("options", []), "CacheDir")]
    if not host_caches:
        host_caches = [Path("/var/cache/pacman/pkg")]
    copied = 0
    for fn in files:
        if (repo / fn).exists():
            continue
        for cache_dir in host_caches:
            src = cache_dir / fn
            if src.is_file():
                try:
                    shutil.copy2(src, repo / fn)
                    sig = cache_dir / f"{fn}.sig"
                    if sig.is_file():
                        shutil.copy2(sig, repo / f"{fn}.sig")
                    copied += 1
                    break
                except OSError:
                    pass
    if copied:
        step(f"reused {copied} package(s) from host pacman cache (saved network download)")
    return copied


def official_phase(db: IsolatedDB, master: Sequence[str], repo: Path, user: RealUser) -> None:
    info("=== OFFICIAL REPO BUILD ===")
    require_tool("repo-add", "pacman")
    official, unresolved = db.resolve_names(master)
    if unresolved:
        shown = ", ".join(unresolved[:40]) + ("…" if len(unresolved) > 40 else "")
        warn(f"{len(unresolved)} master names not in official repos (AUR phase candidates): {shown}")
    if not official:
        die("no official packages resolved from the master list")
    assert_conf_safe(repo)
    repo.mkdir(parents=True, exist_ok=True)

    closure = db.closure(official, [repo])
    files = {fn for _, fn, _ in closure}
    if ingest_host_cache_packages(repo, files):
        closure = db.closure(official, [repo])
        files = {fn for _, fn, _ in closure}
    need = sum(size for *_, size in closure)
    ok(f"closure: {len(files)} packages, {human_bytes(need)} to download")
    old = load_db_by_filename(repo)
    # Anything not already indexed (new, resized, or left unvalidated by a crashed run) forces one
    # pacman -Sw, which signature-validates the entire closure; a fully indexed closure skips it.
    unindexed = sum(1 for fn in files if (e := old.get(fn)) is None or file_size(repo / fn) != e.csize)
    if need or unindexed:
        ensure_disk_space(repo, int(need * 1.35) + (512 << 20), "official package download")
        db.download(official, [repo])
    else:
        step("closure fully cached and indexed: skipping pacman -Sw")
    prune_repo(repo, files)
    update_repo_db(repo, files, old)
    restore_ownership(repo, user)


# ═══════════════════════════════════ AUR phase ═══════════════════════════════════
@dataclass(frozen=True, slots=True)
class AurPkg:
    name: str
    version: str
    pkgbase: str


class AurRpc:
    """Batched, cached AUR RPC v5 'info' client."""

    def __init__(self) -> None:
        self._cache: dict[str, AurPkg | None] = {}

    def prefetch(self, names: Iterable[str]) -> None:
        todo = [n for n in dict.fromkeys(names) if n not in self._cache and PKGNAME_RE.fullmatch(n)]
        for i in range(0, len(todo), AUR_RPC_BATCH):
            chunk = todo[i : i + AUR_RPC_BATCH]
            found: dict[str, AurPkg] = {}
            for row in self._request(chunk).get("results") or ():
                name, ver = row.get("Name"), row.get("Version")
                base = row.get("PackageBase") or name
                if isinstance(name, str) and isinstance(ver, str) and isinstance(base, str):
                    found[name] = AurPkg(name, ver, base)
            for n in chunk:
                self._cache[n] = found.get(n)

    def get(self, name: str) -> AurPkg | None:
        if name not in self._cache:
            self.prefetch([name])
        return self._cache.get(name)

    @staticmethod
    def _request(names: Sequence[str]) -> dict:
        query = urllib.parse.urlencode([("arg[]", n) for n in names])
        req = urllib.request.Request(f"{AUR_RPC}?{query}", headers={
            "User-Agent": f"DuskyISO-Factory/{VERSION}", "Accept": "application/json"})
        last = ""
        for attempt in range(5):
            delay = 1.5**attempt
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    raw = resp.read(MAX_RPC_BYTES + 1)
                if len(raw) > MAX_RPC_BYTES:
                    raise ValueError("response too large")
                data = json.loads(raw)
                if data.get("type") == "error":
                    raise ValueError(str(data.get("error")))
                return data
            except urllib.error.HTTPError as exc:
                last = f"HTTP {exc.code}"
                if exc.code == 429:
                    try:
                        delay = float(exc.headers.get("Retry-After") or 5)
                    except ValueError:
                        delay = 5.0
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                last = str(exc)
            time.sleep(delay + random.uniform(0, 1))
        die(f"AUR RPC unreachable: {last}")


@dataclass(slots=True)
class SrcInfo:
    pkgbase: str = ""
    pkgnames: list[str] = field(default_factory=list)
    depends: list[str] = field(default_factory=list)
    makedepends: list[str] = field(default_factory=list)


# checkdepends are irrelevant: BUILDENV has !check and makepkg runs with --nodeps
_SRCINFO_DEPS = {"depends": "depends", "depends_x86_64": "depends",
                 "makedepends": "makedepends", "makedepends_x86_64": "makedepends"}


def parse_srcinfo(text: str) -> SrcInfo:
    si = SrcInfo()
    for raw in text.splitlines():
        key, sep, value = raw.strip().partition(" = ")
        if not sep or not value:
            continue
        if key == "pkgbase":
            si.pkgbase = value
        elif key == "pkgname":
            si.pkgnames.append(value)
        elif (kind := _SRCINFO_DEPS.get(key)) is not None:
            getattr(si, kind).append(value)
    return si


def write_factory_makepkg_conf(dest: Path) -> Path:
    use_mold = shutil.which("mold") is not None
    ccache = "ccache" if shutil.which("ccache") else "!ccache"
    rust_common = (
        "-C target-cpu=x86-64 -C opt-level=3 "
        "-C link-arg=-Wl,--as-needed "
        "-C link-arg=-Wl,-z,relro "
        "-C link-arg=-Wl,-z,now "
        "-C link-arg=-Wl,-z,pack-relative-relocs"
    )
    if use_mold:
        ld_line = ('LDFLAGS="-Wl,--as-needed -Wl,-z,relro -Wl,-z,now '
                   '-Wl,-z,pack-relative-relocs -fuse-ld=mold"')
        rust = f"{rust_common} -C link-arg=-fuse-ld=mold"
        step("makepkg: mold linker enabled")
    else:
        ld_line = ('LDFLAGS="-Wl,-O1 -Wl,--sort-common -Wl,--as-needed -Wl,-z,relro -Wl,-z,now '
                   '-Wl,-z,pack-relative-relocs"')
        rust = rust_common
        step("makepkg: default linker (install mold for faster links)")
    text = (_FACTORY_MAKEPKG_CONF_TEMPLATE.replace("__LDFLAGS_LINE__", ld_line)
            .replace("__RUSTFLAGS__", rust).replace("__CCACHE__", ccache))
    dest.write_text(text, encoding="utf-8")
    dest.chmod(0o644)
    return dest


def makepkg_env(user: RealUser, **extra: str) -> dict[str, str]:
    # `makepkg --config X` already skips ~/.makepkg.conf and $XDG_CONFIG_HOME/pacman/makepkg.conf
    # (libmakepkg sources user overrides only for /etc/makepkg.conf); scrub env overrides too.
    env = user_env(user)
    for key in MAKEPKG_ENV_SCRUB:
        env.pop(key, None)
    ncpu = str(os.process_cpu_count() or 1)  # affinity/cpuset-aware, like nproc(1)
    env |= {"GOAMD64": "v1", "CI": "1", "CARGO_BUILD_JOBS": ncpu, "CMAKE_BUILD_PARALLEL_LEVEL": ncpu,
            "PACKAGER": "Dusky Factory <factory@dusky>"}
    return env | extra


class AurBuilder:
    def __init__(self, db: IsolatedDB, repo: Path, official: Path | None, user: RealUser) -> None:
        self.db, self.repo, self.official, self.user = db, repo, official, user
        self.rpc = AurRpc()
        self.work = make_tempdir("dusky-aur-", owner=user)
        self.src_root = self._user_dir(self.work / "src")
        self.logs = self.work / "logs"
        self.logs.mkdir()
        self.makepkg_conf = write_factory_makepkg_conf(self.work / "dusky-makepkg.conf")
        self.index: dict[str, list[tuple[str, str]]] = {}  # pkgname -> [(ver, filename)]
        for fn in package_files(repo):
            name, ver, arch = parse_pkg_filename(fn)  # type: ignore[misc]
            if arch in ("x86_64", "any"):
                self.index.setdefault(name, []).append((ver, fn))
        self.clones: dict[str, Path] = {}  # pkgbase -> clone, kept across deferrals
        self.keep_names: set[str] = set()  # requested packages that belong in the repo
        self.built = self.skipped = 0
        self.failed: list[str] = []
        self.queue: list[str] = []

    def _user_dir(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, self.user.uid, self.user.gid)
        return path

    def newest(self, name: str) -> str | None:
        versions = self.index.get(name)
        if not versions:
            return None
        best = versions[0]
        for cand in versions[1:]:
            if vercmp(cand[0], best[0]) > 0:
                best = cand
        return best[1]

    def run(self, seeds: Sequence[str]) -> None:
        queue = list(dict.fromkeys(seeds))
        known = set(queue)
        defers: dict[str, int] = {}
        try:
            self.rpc.prefetch(queue)
        except FactoryError as exc:
            warn(f"AUR RPC prefetch failed ({exc}); retrying per package")
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), TaskProgressColumn(),
                      console=console) as prog:
            task = prog.add_task("AUR builds", total=len(queue))
            i = 0
            while i < len(queue):
                pkg = queue[i]
                i += 1
                prog.update(task, description=f"AUR {pkg} ({i}/{len(queue)})", total=len(queue))
                try:
                    status, new_deps = self.process(pkg)
                except Exception as exc:  # one broken package must not stop the queue
                    err(f"{pkg}: {exc}")
                    status, new_deps = "failed", []
                fresh = [d for d in new_deps if d not in known]
                known.update(fresh)
                queue += fresh
                if fresh:
                    try:
                        self.rpc.prefetch(fresh)
                    except FactoryError as exc:
                        warn(f"AUR RPC prefetch failed ({exc})")
                match status:
                    case "deferred":
                        defers[pkg] = defers.get(pkg, 0) + 1
                        if defers[pkg] > MAX_DEFER:
                            err(f"{pkg}: defer limit exceeded (dependency cycle?)")
                            status = "failed"
                        else:
                            queue.append(pkg)
                    case "built":
                        self.built += 1
                    case "current" | "official":
                        self.skipped += 1
                if status == "failed":
                    self.failed.append(pkg)
                    if self.newest(pkg):  # keep the last good build rather than shipping nothing
                        self.keep_names.add(pkg)
                prog.update(task, completed=i, total=len(queue))
        self.queue = queue
        remove_tree(self.work)

    def process(self, pkg: str) -> tuple[str, list[str]]:
        info(f"Processing AUR: {pkg}")
        meta = self.rpc.get(pkg)
        if meta is None:
            if self.db.index.has(pkg):
                step(f"{pkg} is provided by the official repos; skipping AUR")
                return "official", []
            die("not found on the AUR")
        if any(ver == meta.version or pkg.endswith(VCS_SUFFIXES) for ver, _ in self.index.get(pkg, ())):
            step(f"{pkg} {meta.version} already in the repo")
            self.keep_names.add(pkg)
            return "current", []

        clone = self._clone(meta.pkgbase)
        si = parse_srcinfo((clone / ".SRCINFO").read_text(encoding="utf-8"))
        siblings = {pkg, meta.pkgbase, si.pkgbase, *si.pkgnames}
        official_deps: list[str] = []
        aur_deps: list[str] = []
        for dep in dict.fromkeys(si.depends + si.makedepends):
            name = dep_name(dep)
            if name in siblings:
                continue
            if self.db.index.has(name):
                official_deps.append(dep)
            elif is_aur_candidate(name):
                aur_deps.append(dep)
            else:
                warn(f"{pkg}: dependency {dep!r} not resolvable (ignored)")
        queued = [dep_name(d) for d in aur_deps]

        unsatisfied: set[str] = set()
        if deps := official_deps + aur_deps:  # one `pacman -T` for all deps (versions/provides aware)
            r = run(["pacman", "-T", "--", *deps], capture=True)
            unsatisfied = {ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()}
        blocked: list[str] = []
        aur_files: list[Path] = []
        for dep in aur_deps:
            if dep in unsatisfied:
                if (fn := self.newest(dep_name(dep))) is not None:
                    aur_files.append(self.repo / fn)
                else:
                    blocked.append(dep_name(dep))
        if dead := [b for b in blocked if b in self.failed]:
            die(f"AUR dependency failed: {', '.join(dead)}")  # don't spin through MAX_DEFER revisits
        if blocked:
            step(f"{pkg}: deferred until AUR deps are built: {', '.join(blocked)}")
            return "deferred", queued

        if need := [dep_name(d) for d in official_deps if d in unsatisfied]:
            step(f"installing host build deps: {' '.join(need)}")
            caches = [a for d in conf_values(host_conf().get("options", []), "CacheDir") for a in ("--cachedir", d)]
            if self.official is not None:
                caches += ["--cachedir", os.fspath(self.official)]  # reuse freshly mirrored files
            r = host_pacman("-S", "--needed", "--asdeps", *caches, "--", *need)
            if r.returncode != 0:
                die(f"host build-dependency install failed:\n{pacman_errors(r)}")
        if aur_files:
            step(f"installing AUR build deps: {' '.join(f.name for f in aur_files)}")
            r = host_pacman("-U", "--needed", "--asdeps", "--", *map(os.fspath, aur_files))
            if r.returncode != 0:
                die(f"AUR build-dependency install failed:\n{pacman_errors(r)}")

        self._publish(self._build(meta.pkgbase, clone))
        self.keep_names.add(pkg)
        remove_tree(self.clones.pop(meta.pkgbase))
        return "built", queued

    def _clone(self, pkgbase: str) -> Path:
        if (cached := self.clones.get(pkgbase)) is not None:
            return cached
        dest = self.src_root / pkgbase
        detail = ""
        for attempt in range(1, CLONE_ATTEMPTS + 1):
            remove_tree(dest)
            try:
                r = run(["git", "clone", "--quiet", "--depth", "1", "--no-tags",
                         f"https://aur.archlinux.org/{pkgbase}.git", dest],
                        user=self.user, env=user_env(self.user), cwd=self.src_root,
                        capture=True, merge=True, timeout=CLONE_TIMEOUT_S)
                if r.returncode == 0:
                    break
                detail = (r.stdout or "").strip()[-500:]
            except subprocess.TimeoutExpired:
                detail = f"timed out after {CLONE_TIMEOUT_S}s"
            if attempt < CLONE_ATTEMPTS:
                backoff(attempt)
        else:
            die(f"git clone failed for {pkgbase}: {detail}")
        if not (dest / "PKGBUILD").is_file() or not (dest / ".SRCINFO").is_file():
            die(f"{pkgbase}.git has no PKGBUILD/.SRCINFO")
        self.clones[pkgbase] = dest
        return dest

    def _build(self, pkgbase: str, clone: Path) -> list[Path]:
        build = self._user_dir(self.work / f"build-{pkgbase}")
        pkgdest = self._user_dir(build / "pkgdest")
        srcdest = self._user_dir(build / "sources")
        log = self.logs / f"{pkgbase}.log"
        env = makepkg_env(self.user, PKGDEST=str(pkgdest), BUILDDIR=str(build), SRCDEST=str(srcdest),
                          GRADLE_OPTS="-Dorg.gradle.daemon=false -Dorg.gradle.console=plain",
                          GRADLE_USER_HOME=str(build / ".gradle"))
        base = ["makepkg", "--config", self.makepkg_conf, "--nodeps", "--noconfirm", "--skippgpcheck"]
        step(f"makepkg {pkgbase} (log: {log})")
        # Only the network part is retried; a compile failure is deterministic.
        for attempt in range(1, SOURCE_ATTEMPTS + 1):
            r = run([*base, "--verifysource"], user=self.user, env=env, cwd=clone, log=log, timeout=BUILD_TIMEOUT_S)
            if r.returncode == 0:
                break
            if attempt < SOURCE_ATTEMPTS:
                backoff(attempt)
        else:
            die(f"source download/verification failed; tail of {log}:\n{tail_text(log)}")
        r = run([*base, "--cleanbuild"], user=self.user, env=env, cwd=clone, log=log, timeout=BUILD_TIMEOUT_S)
        if r.returncode != 0:
            die(f"makepkg failed (exit {r.returncode}); tail of {log}:\n{tail_text(log)}")
        built = sorted(p for p in pkgdest.iterdir() if PKGFILE_RE.fullmatch(p.name))
        if not built:
            die("makepkg produced no package")
        return built

    def _publish(self, built: Sequence[Path]) -> None:
        for bf in built:
            with atomic_path(self.repo / bf.name) as tmp:
                shutil.copyfile(bf, tmp)  # copy_file_range/sendfile: no userspace buffers
            name, ver, _ = parse_pkg_filename(bf.name)  # type: ignore[misc]
            self.index.setdefault(name, []).append((ver, bf.name))
            self.keep_names.add(name)
            ok(f"built: {bf.name}")
        fsync_dir(self.repo)
        remove_tree(built[0].parent.parent)  # build-<pkgbase>


def finalize_aur_repo(db: IsolatedDB, repo: Path, official: Path | None, keep_names: set[str], user: RealUser) -> None:
    """Runtime closure of the kept AUR packages -> fetch missing official deps -> prune -> DB."""
    info("Finalizing AUR repo (runtime closure, prune, DB)")
    old = load_db_by_filename(repo)
    newest = newest_files(package_files(repo))
    aur_names = {n for n in newest if n not in db.index.names}  # official deps kept here are re-resolved
    if not aur_names:
        warn("AUR repo holds no AUR-built packages")
        return
    update_repo_db(repo, {newest[n] for n in aur_names}, old)  # interim DB: AUR-built packages only
    db.attach_local_repo(repo)
    cachedirs = [repo] + ([official] if official is not None else [])
    targets = sorted(keep_names & aur_names)
    closure, dropped = db.closure_tolerant(targets, cachedirs)
    needed = {fn for _, fn, sz in closure if sz > 0}
    if needed and ingest_host_cache_packages(repo, needed):
        closure, dropped = db.closure_tolerant(targets, cachedirs)
    keep = {newest[n] for n in dropped}
    for repo_name, fn, _ in closure:
        if repo_name == REPO_NAME or official is None or not (official / fn).is_file():
            keep.add(fn)
    if sum(size for *_, size in closure):
        ensure_disk_space(repo, 2 << 30, "AUR runtime dependencies")
        db.download([t for t in targets if t not in dropped], cachedirs)
    prune_repo(repo, keep)
    update_repo_db(repo, keep, old, force_write=True)
    restore_ownership(repo, user)


def aur_phase(db: IsolatedDB, master: Sequence[str], repo: Path, official: Path, user: RealUser) -> None:
    info("=== AUR REPO BUILD ===")
    for tool, hint in (("git", "git"), ("makepkg", "pacman"), ("gcc", "base-devel"), ("make", "base-devel")):
        require_tool(tool, hint)
    assert_conf_safe(repo)
    repo.mkdir(parents=True, exist_ok=True)
    ensure_disk_space(repo, 2 << 30, "AUR builds")
    _, unresolved = db.resolve_names(master)
    seeds = [*AUR_SEED, *(n for n in unresolved if is_aur_candidate(n))]
    builder = AurBuilder(db, repo, official if official.is_dir() else None, user)
    builder.run(seeds)
    finalize_aur_repo(db, repo, builder.official, builder.keep_names, user)

    table = Table(title="AUR Summary", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    for metric, value in (("Built", builder.built), ("Skipped", builder.skipped),
                          ("Failed", len(builder.failed)), ("Queue final", len(builder.queue))):
        table.add_row(metric, str(value))
    console.print(table)
    if builder.failed:
        err(f"failed: {', '.join(builder.failed)}")
        if hard := sorted(set(builder.failed) & REQUIRED_AUR):
            die(f"required AUR package(s) failed: {', '.join(hard)}")


# ═══════════════════════════════════ ISO phase ═══════════════════════════════════
@dataclass(frozen=True, slots=True)
class IsoConfig:
    workspace: Path
    source_dir: Path
    official_repo: Path
    aur_repo: Path | None
    final_dest: Path

    @property
    def profile_dir(self) -> Path:
        return self.workspace / "profile"

    @property
    def work_dir(self) -> Path:
        return self.workspace / "work"

    @property
    def out_dir(self) -> Path:
        return self.workspace / "out"

    @property
    def staging(self) -> Path:
        return self.workspace / "iso_repo"


def setup_clean_room(cfg: IsoConfig) -> None:
    info("Clean room")
    if not RELENG.is_dir():
        die("archiso releng profile not found — install archiso")
    remove_tree(cfg.workspace, strict=True)
    cfg.workspace.mkdir(parents=True, mode=0o700)
    shutil.copytree(RELENG, cfg.profile_dir, symlinks=True)
    patch_profiledef_compression(cfg.profile_dir / "profiledef.sh")
    ok("clean room ready")


def patch_profiledef_compression(profiledef: Path) -> None:
    if not profiledef.is_file():
        return
    txt = profiledef.read_text(encoding="utf-8")
    new_opts = "airootfs_image_tool_options=('-comp' 'zstd' '-b' '1M' '-Xcompression-level' '19')"
    new = re.sub(r"airootfs_image_tool_options=\([^)]*\)", new_opts, txt)
    if new != txt:
        profiledef.write_text(new, encoding="utf-8")
        step(f"profiledef: {new_opts}")
    else:
        step("profiledef: compression left upstream (no airootfs_image_tool_options found)")


def stage_payloads(cfg: IsoConfig) -> None:
    info("Staging payloads")
    dest = cfg.profile_dir / "airootfs" / "root" / "arch_install"
    dest.mkdir(parents=True, exist_ok=True)
    if cfg.source_dir.is_dir():
        for item in cfg.source_dir.iterdir():
            if item.name in {".git", ".gitignore"}:
                continue
            if item.is_dir():
                shutil.copytree(item, dest / item.name, symlinks=True, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest / item.name)
        if not (dest / "000_dusky_arch_install.sh").is_file():
            warn(f"expected installer missing: {dest / '000_dusky_arch_install.sh'}")
    releng_pkg = cfg.profile_dir / "packages.x86_64"
    asset_pkg = cfg.source_dir / "assets" / "iso_temp_packages" / "packages.x86_64"
    source = asset_pkg if asset_pkg.is_file() else releng_pkg
    out = list(dict.fromkeys(s for ln in source.read_text(encoding="utf-8").splitlines()
                             if (s := ln.strip()) and not s.startswith("#")))
    if not out:
        die("packages.x86_64 empty")
    releng_pkg.write_text("\n".join(out) + "\n", encoding="utf-8")
    ok(f"payloads staged ({len(out)} packages.x86_64 entries)")


def configure_live_hooks(cfg: IsoConfig) -> None:
    info("Live hooks")
    script = cfg.profile_dir / "airootfs" / "root" / ".automated_script.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$(tty)" == "/dev/tty1" ]]; then\n'
        '  echo "root:0000" | chpasswd\n'
        '  echo -e "\\e[1;32m[INFO]\\e[0m Root password set to 0000. SSH is available."\n'
        '  echo -e "\\e[1;34m[INFO]\\e[0m Bootstrapping environment..."\n'
        "  systemctl is-system-running >/dev/null 2>&1 || true\n"
        "  chmod -R +x /root/arch_install/ 2>/dev/null || true\n"
        "  clear\n"
        "  cd /root/arch_install/ 2>/dev/null && ./000_dusky_arch_install.sh --auto || true\n"
        "fi\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    ok("live hooks")


def inject_dotfiles(cfg: IsoConfig) -> None:
    info("Injecting dotfiles")
    skel = cfg.profile_dir / "airootfs" / "etc" / "skel"
    remove_tree(skel, strict=True)
    skel.mkdir(parents=True)
    profiledef = cfg.profile_dir / "profiledef.sh"
    if not profiledef.is_file():
        die("profiledef.sh missing after clean room setup")
    txt = re.sub(r"# --- DUSKY PERMISSIONS START ---.*?# --- DUSKY PERMISSIONS END ---\n?", "",
                 profiledef.read_text(encoding="utf-8"), flags=re.DOTALL)
    pkg_file = cfg.profile_dir / "packages.x86_64"
    if pkg_file.is_file():
        ptxt = re.sub(r"^\s*grml-zsh-config\s*$", "", pkg_file.read_text(encoding="utf-8"), flags=re.MULTILINE)
        pkg_file.write_text(ptxt, encoding="utf-8")

    tmp = make_tempdir("dusky-dots-")
    repo = tmp / "dusky"
    git_env = os.environ | {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "GIT_ASKPASS": "/bin/true"}
    try:
        for attempt in range(1, 4):
            remove_tree(repo)
            r = run(["git", "clone", "--quiet", "--depth", "1", "https://github.com/dusklinux/dusky", repo],
                    env=git_env, capture=True, merge=True, timeout=CLONE_TIMEOUT_S)
            if r.returncode == 0:
                break
            if attempt == 3:
                die(f"git clone dusky failed: {(r.stdout or '').strip()[-500:]}")
            backoff(attempt)
        pin = os.environ.get("DUSKY_DOTFILES_PIN", "").strip()
        expect_sha = os.environ.get("DUSKY_DOTFILES_SHA", "").strip().lower()
        if pin:
            run(["git", "-C", repo, "fetch", "--quiet", "--depth", "1", "origin", pin], env=git_env, capture=True)
            if run(["git", "-C", repo, "checkout", "--quiet", pin], env=git_env, capture=True).returncode != 0:
                die(f"DUSKY_DOTFILES_PIN checkout failed: {pin}")
        head_sha = (run(["git", "-C", repo, "rev-parse", "HEAD"], env=git_env, capture=True).stdout or "").strip().lower()
        if expect_sha and head_sha and not head_sha.startswith(expect_sha):
            die(f"dotfiles SHA mismatch: got {head_sha}, expected {expect_sha}")
        if head_sha:
            step(f"dotfiles HEAD {head_sha[:12]}")

        repo_real = repo.resolve()
        for item in repo.iterdir():
            if item.name == ".git":
                continue
            if item.is_symlink():  # a top-level link may point outside the checkout (host files!)
                try:
                    if not item.resolve(strict=True).is_relative_to(repo_real):
                        warn(f"skipping symlink escaping the checkout: {item.name}")
                        continue
                except OSError:
                    warn(f"skipping dangling symlink: {item.name}")
                    continue
            if item.is_dir():
                shutil.copytree(item, skel / item.name, symlinks=False, dirs_exist_ok=True,
                                ignore_dangling_symlinks=True)
            else:
                shutil.copy2(item, skel / item.name)
    finally:
        remove_tree(tmp)

    marker = "# --- AUTOMATED ISO INJECTION: EDITOR & YAZI WRAPPER ---"
    yazi_fn = (
        "\ny() {\n"
        "  local tmp\n"
        '  tmp="$(mktemp -p "${XDG_RUNTIME_DIR:-/tmp}" -t "yazi-cwd.XXXXXX")"\n'
        '  yazi "$@" --cwd-file="$tmp"\n'
        '  if cwd="$(cat -- "$tmp")" && [ -n "$cwd" ] && [ "$cwd" != "$PWD" ]; then\n'
        '    builtin cd -- "$cwd"\n'
        "  fi\n"
        '  rm -f -- "$tmp"\n'
        "}\n"
    )
    for rc_dir in (skel, cfg.profile_dir / "airootfs" / "root"):
        rc_dir.mkdir(parents=True, exist_ok=True)
        for rc_name in (".bashrc", ".zshrc"):
            rc_path = rc_dir / rc_name
            if rc_path.exists() and marker in rc_path.read_text(encoding="utf-8"):
                continue
            with open(rc_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n{marker}\nexport EDITOR='nvim'\nexport VISUAL='nvim'\n{yazi_fn}")

    hypr_src = cfg.source_dir / "assets" / "hyprland" / "hyprland.lua"
    if hypr_src.is_file():
        (skel / ".config" / "hypr").mkdir(parents=True, exist_ok=True)
        shutil.copy2(hypr_src, skel / ".config" / "hypr" / "hyprland.lua")

    # mkarchiso copies airootfs with --no-preserve=mode: executables need file_permissions entries
    rootfs = cfg.profile_dir / "airootfs"
    perms: list[str] = []
    for dirpath, _dirs, files in os.walk(skel):
        for name in files:
            path = Path(dirpath, name)
            st = path.lstat()
            if not path.is_symlink() and st.st_mode & 0o111:
                rel = "/" + os.fspath(path.relative_to(rootfs))
                esc = rel.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
                perms.append(f'file_permissions+=(["{esc}"]="0:0:0755")\n')
    profiledef.write_text(txt + "\n# --- DUSKY PERMISSIONS START ---\n" + "".join(sorted(perms))
                          + "# --- DUSKY PERMISSIONS END ---\n", encoding="utf-8")
    ok(f"dotfiles injected ({len(perms)} executable(s) registered)")


def build_local_packages(cfg: IsoConfig, user: RealUser) -> None:
    """Build every recipe into an indexed, generic x86-64 repository for the ISO."""
    skel = cfg.profile_dir / "airootfs" / "etc" / "skel"
    recipes_dir = skel / "user_scripts" / "arch_iso_scripts" / "offline_iso" / "iso_maker" / "python" / "dusky_packages_compile"
    local_dir = cfg.source_dir / "iso_maker" / "python" / "dusky_packages_compile"
    local_recipes = sorted(p for p in local_dir.iterdir() if p.is_dir() and (p / "recipe.toml").is_file()) \
        if local_dir.is_dir() else []
    recipes = sorted(p for p in recipes_dir.iterdir() if p.is_dir() and (p / "recipe.toml").is_file()) \
        if recipes_dir.is_dir() else []
    if [p.name for p in local_recipes] != [p.name for p in recipes]:
        die("local package recipes differ from the injected Git checkout; commit and push the recipes")
    for local, staged in zip(local_recipes, recipes):
        for filename in ("recipe.toml", "PKGBUILD"):
            if not (local / filename).is_file() or not (staged / filename).is_file() \
                    or (local / filename).read_bytes() != (staged / filename).read_bytes():
                die(f"{local.name}/{filename} differs from the injected Git checkout; commit and push it")
    names: list[str] = []
    artifacts: list[tuple[str, str]] = []
    if recipes:
        require_tool("makepkg", "pacman")
        require_tool("repo-add", "pacman-contrib")
        info(f"Building {len(recipes)} local ISO package(s)")
    repo = cfg.workspace / "local_repo"
    for recipe in recipes:
        if not (recipe / "PKGBUILD").is_file():
            die(f"{recipe}: recipe.toml requires a PKGBUILD")
        try:
            spec = tomllib.loads((recipe / "recipe.toml").read_text(encoding="utf-8"))
            name = spec["package"]
            source_rel = Path(spec["source"])
            tools = spec.get("tools", [])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            die(f"{recipe}: invalid recipe.toml ({exc})")
        if (not isinstance(name, str) or not PKGNAME_RE.fullmatch(name)
                or name in names or not isinstance(tools, list)
                or not all(isinstance(tool, str) and re.fullmatch(r"[A-Za-z0-9._+-]+", tool) for tool in tools)
                or source_rel.is_absolute() or ".." in source_rel.parts):
            die(f"{recipe}: invalid package name, tools, or source path")
        for tool in tools:
            require_tool(tool, f"required by {name}")
        source = (skel / source_rel).resolve()
        if not source.is_dir() or not source.is_relative_to(skel.resolve()):
            die(f"{recipe}: source {source_rel} is missing from the ISO dotfiles checkout")

        work = make_tempdir(f"dusky-package-{name}-", owner=user)
        shutil.copytree(source, work / "source", symlinks=False)
        shutil.copytree(recipe, work / "recipe", symlinks=False)
        for sub in ("packages", "build", "cargo-target"):
            (work / sub).mkdir()
        config = write_factory_makepkg_conf(work / "makepkg.conf")
        restore_ownership(work, user)
        env = makepkg_env(
            user, DUSKY_PACKAGE_SOURCE=str(work / "source"), PKGDEST=str(work / "packages"),
            BUILDDIR=str(work / "build"), CARGO_TARGET_DIR=str(work / "cargo-target"),
        )
        log = work / "build.log"
        step(f"makepkg {name} (log: {log})")
        result = run(
            ["makepkg", "--config", config, "--nodeps", "--noconfirm", "--skippgpcheck", "--cleanbuild"],
            user=user, env=env, cwd=work / "recipe", log=log, timeout=BUILD_TIMEOUT_S,
        )
        if result.returncode != 0:
            die(f"makepkg {name} failed (exit {result.returncode}); tail of {log}:\n{tail_text(log)}")
        built = [p for p in (work / "packages").iterdir()
                 if (parsed := parse_pkg_filename(p.name)) is not None and parsed[0] == name
                 and parsed[2] == "x86_64"]
        if len(built) != 1:
            die(f"{recipe}: expected one x86_64 package named {name}, found {len(built)}")
        repo.mkdir(exist_ok=True)
        with atomic_path(repo / built[0].name) as tmp:
            shutil.copyfile(built[0], tmp)
        names.append(name)
        artifacts.append((name, built[0].name))
        ok(f"local ISO package: {built[0].name}")
        remove_tree(work)

    manifest = cfg.profile_dir / "airootfs" / "root" / "arch_install" / "compiled_packages.txt"
    manifest.write_text("".join(f"{name}\t{filename}\n" for name, filename in artifacts), encoding="utf-8")
    if names:
        update_repo_db(repo, set(package_files(repo)))


_copy_buf = threading.local()


def _copy_verified_one(src: Path, dst: Path, entry: DbEntry) -> str | None:
    """Copy src->dst hashing in the same pass (one read of the source). The source's page cache
    is dropped afterwards: a multi-GB one-shot read must not evict RAM a zram workspace needs."""
    buf: bytearray | None = getattr(_copy_buf, "buf", None)
    if buf is None:
        buf = _copy_buf.buf = bytearray(COPY_CHUNK)
    view = memoryview(buf)
    digest = hashlib.sha256()
    try:
        with open(src, "rb", buffering=0) as fin:
            st = os.fstat(fin.fileno())
            if st.st_size != entry.csize:
                return f"{src.name}: size {st.st_size} != DB {entry.csize}"
            os.posix_fadvise(fin.fileno(), 0, 0, os.POSIX_FADV_SEQUENTIAL)
            fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o644)
            try:
                while n := fin.readinto(buf):
                    chunk = view[:n]
                    digest.update(chunk)  # releases the GIL: threads hash in parallel
                    while chunk:
                        chunk = chunk[os.write(fd, chunk):]
                os.utime(fd, ns=(st.st_atime_ns, st.st_mtime_ns))
            finally:
                os.close(fd)
            os.posix_fadvise(fin.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
    except OSError as exc:
        return f"{src.name}: {exc}"
    if digest.hexdigest() != entry.sha256:
        return f"{src.name}: SHA256 mismatch (DB {entry.sha256}, disk {digest.hexdigest()})"
    return None


def stage_iso_repo(cfg: IsoConfig) -> dict[str, DbEntry]:
    """Merge official, AUR, and locally compiled packages; copy each winner
    once into the workspace with inline SHA256 verification against its DB, write the merged DB
    from those entries (no repo-add, no re-hash) and check the result is self-contained."""
    info("Staging merged offline repository (verified single-pass copy)")
    merged: dict[str, tuple[DbEntry, Path]] = {
        n: (e, cfg.official_repo) for n, e in read_repo_db(cfg.official_repo / FILES_NAME).items()}
    if cfg.aur_repo is not None and (cfg.aur_repo / FILES_NAME).is_file():
        for name, entry in read_repo_db(cfg.aur_repo / FILES_NAME).items():
            cur = merged.get(name)
            if cur is None or vercmp(entry.version, cur[0].version) > 0:
                if cur is not None:
                    warn(f"{name}: AUR repo {entry.version} supersedes official {cur[0].version}")
                merged[name] = (entry, cfg.aur_repo)
    local_repo = cfg.workspace / "local_repo"
    if (local_repo / FILES_NAME).is_file():
        for name, entry in read_repo_db(local_repo / FILES_NAME).items():
            if name in merged:
                die(f"local ISO package {name} conflicts with an official or AUR package")
            merged[name] = (entry, local_repo)
    if not merged:
        die("merged ISO repository is empty")
    total = sum(e.csize for e, _ in merged.values())
    ensure_disk_space(cfg.workspace, max(12 << 30, int(total * 2.5) + (4 << 30)), "ISO workspace")
    cfg.staging.mkdir(mode=0o755)

    items = list(merged.values())
    t0 = time.perf_counter()
    errors = [e for e in parallel_map(lambda it: _copy_verified_one(it[1] / it[0].filename,
                                                                    cfg.staging / it[0].filename, it[0]),
                                      items, min(8, os.process_cpu_count() or 1)) if e]
    if errors:
        for msg in errors[:50]:
            err(msg)
        die(f"{len(errors)} package(s) failed verification — rerun the repo phase(s)")
    dt = time.perf_counter() - t0
    step(f"copied+verified {len(items)} packages, {human_bytes(total)} in {dt:.1f}s "
         f"({human_bytes(int(total / max(dt, 1e-6)))}/s)")
    write_repo_db(cfg.staging, (e for e, _ in items), durable=False)  # consumed in-run: no fsync
    verify_repo_closure(cfg.staging, list(merged))
    ok(f"ISO repository staged: {len(items)} packages + DB")
    return {n: e for n, (e, _) in merged.items()}


def verify_repo_closure(repo: Path, names: Sequence[str]) -> None:
    """Every package in the ISO repo must be installable from the ISO repo alone."""
    tmp = make_tempdir("dusky-closure-")
    try:
        for sub in ("sync", "local"):
            (tmp / sub).mkdir()
        shutil.copyfile(repo / DB_NAME, tmp / "sync" / f"{REPO_NAME}.db")
        conf = tmp / "pacman.conf"
        conf.write_text(f"[options]\nDBPath = {tmp}/\nLogFile = {tmp}/pacman.log\nArchitecture = x86_64\n"
                        f"SigLevel = Never\n\n[{REPO_NAME}]\nServer = file://{repo}\n", encoding="utf-8")
        r = run(["pacman", "--config", conf, "--noconfirm", "--color", "never", "-Swp", "--print-format", "%n",
                 "--cachedir", tmp, "--", *sorted(names)], capture=True)
        if r.returncode == 0:
            ok("ISO repository is dependency-closed")
        else:
            warn("ISO repository is NOT dependency-closed; offline installs of these will fail:")
            console.print(escape(pacman_errors(r)), markup=True)
    finally:
        remove_tree(tmp)


def configure_iso_pacman_conf(cfg: IsoConfig) -> None:
    """[archrepo] -> merged staging repo (the one Server pacman actually syncs). CacheDir order:
    host cache first (receives network downloads of releng-only packages), staging second (lets
    pacstrap use repo files in place — nothing is copied)."""
    info("Patching profile pacman.conf")
    assert_conf_safe(cfg.staging)
    pc = cfg.profile_dir / "pacman.conf"
    if not pc.is_file():
        die("profile pacman.conf missing")
    drop = re.compile(r"^#?\s*(Color|ILoveCandy|VerbosePkgLists|ParallelDownloads|DownloadUser|CacheDir)\b")
    out: list[str] = []
    section = ""
    for line in pc.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1]
            if section == "core":
                out += [f"[{REPO_NAME}]", "SigLevel = Optional TrustAll", f"Server = file://{cfg.staging}", ""]
            out.append(line)
            if section == "options":
                out += ["Color", "ILoveCandy", "VerbosePkgLists", f"ParallelDownloads = {PARALLEL_DOWNLOADS}",
                        "CacheDir = /var/cache/pacman/pkg/", f"CacheDir = {cfg.staging}/"]
            continue
        if section == "options" and drop.match(s):
            continue
        out.append(line)
    if f"[{REPO_NAME}]" not in out:
        die("profile pacman.conf has no [core] section to anchor the offline repository")
    pc.write_text("\n".join(out) + "\n", encoding="utf-8")
    ok("pacman.conf patched")


def sanitize_live_packages(cfg: IsoConfig, entries: dict[str, DbEntry]) -> None:
    pkg_file = cfg.profile_dir / "packages.x86_64"
    if not pkg_file.is_file():
        return
    info("Validating live-environment packages (packages.x86_64)")
    universe = set(entries) | {p for e in entries.values() for p in e.provides}
    profile_repos = run(["pacman-conf", "--config", cfg.profile_dir / "pacman.conf", "--repo-list"],
                        capture=True, check=True).stdout.split()
    dbpath = Path((conf_values(host_conf().get("options", []), "DBPath") or ["/var/lib/pacman/"])[0])
    host_dbs = [p for r in profile_repos if r != REPO_NAME and (p := dbpath / "sync" / f"{r}.db").is_file()]
    idx = load_sync_index(host_dbs)
    universe |= idx.names | idx.provides | idx.groups
    sanitized: list[str] = []
    for line in pkg_file.read_text(encoding="utf-8").splitlines():
        pkg = line.strip()
        if not pkg or pkg.startswith("#"):
            continue
        if (target := LIVE_PKG_ALIASES.get(pkg)) is not None and target in universe:
            step(f"mapped obsolete live package {pkg} -> {target}")
            pkg = target
        if pkg not in universe:
            warn(f"excluding unavailable package {pkg!r} from packages.x86_64")
            continue
        sanitized.append(pkg)
    sanitized = list(dict.fromkeys(sanitized))
    if not sanitized:
        die("sanitization left packages.x86_64 empty")
    pkg_file.write_text("\n".join(sanitized) + "\n", encoding="utf-8")
    ok(f"live packages validated ({len(sanitized)})")


def build_iso_image(cfg: IsoConfig, user: RealUser) -> tuple[Path, str]:
    info("Building ISO")
    require_tool("mkarchiso", "archiso")
    mk_text = Path("/usr/bin/mkarchiso").read_text(encoding="utf-8")
    marker = "_build_iso_image() {"
    if (count := mk_text.count(marker)) != 1:
        die(f"mkarchiso: expected exactly one {marker!r}, found {count}; archiso layout changed")
    mk = cfg.workspace / "mkarchiso"
    hook = MKARCHISO_HOOK.replace("@STAGING@", shlex.quote(os.fspath(cfg.staging)))
    mk.write_text(mk_text.replace(marker, f"{marker}\n{hook}", 1), encoding="utf-8")
    mk.chmod(0o755)

    cmd = [mk, "-v", "-m", "iso", "-w", cfg.work_dir, "-o", cfg.out_dir, cfg.profile_dir]
    info(f"Running mkarchiso: {shlex.join(map(os.fspath, cmd))}")
    if run(cmd).returncode != 0:
        die("mkarchiso failed")
    isos = sorted(cfg.out_dir.glob("*.iso"))
    if not isos:
        die("mkarchiso produced no ISO")

    cfg.final_dest.mkdir(parents=True, exist_ok=True)
    final = cfg.final_dest / f"dusky_{datetime.now():%m_%y}.iso"
    sha_path = final.with_name(f"{final.stem}_iso.sha256")
    with atomic_path(final) as tmp_iso:  # previous ISO survives until the new one is complete
        shutil.move(isos[0], tmp_iso)  # rename(2) when the workspace shares the filesystem
        with open(tmp_iso, "rb") as fh:
            digest = hashlib.file_digest(fh, "sha256").hexdigest()
    drop_page_cache(final)
    with atomic_path(sha_path) as tmp_sha:
        tmp_sha.write_text(f"{digest}  {final.name}\n", encoding="utf-8")
    fsync_dir(cfg.final_dest)
    for path in (final, sha_path):
        os.chown(path, user.uid, user.gid)
    for old in cfg.final_dest.glob("dusky_*"):  # only now that the replacement is durable
        if old not in (final, sha_path) and old.is_file() and old.name.endswith((".iso", "_iso.sha256")):
            step(f"removing previous build artifact: {old.name}")
            old.unlink(missing_ok=True)
    ok(f"ISO built: {final} ({human_bytes(final.stat().st_size)})")
    return final, digest


def iso_phase(cfg: IsoConfig, user: RealUser) -> tuple[Path, str]:
    info("=== ISO BUILD ===")
    for tool in ("mkarchiso", "git"):
        require_tool(tool)
    if not cfg.official_repo.is_dir():
        die(f"official repo missing at {cfg.official_repo} — build it first")
    assert_conf_safe(cfg.official_repo)
    if not ensure_repo_db(cfg.official_repo):
        die(f"{cfg.official_repo} holds no packages — run the official phase first")
    if cfg.aur_repo is not None:
        assert_conf_safe(cfg.aur_repo)
        if not ensure_repo_db(cfg.aur_repo):
            step(f"{cfg.aur_repo} holds no packages; ISO repo = official only")
            cfg = replace(cfg, aur_repo=None)
    try:
        setup_clean_room(cfg)
        stage_payloads(cfg)
        configure_live_hooks(cfg)
        inject_dotfiles(cfg)
        build_local_packages(cfg, user)
        entries = stage_iso_repo(cfg)
        configure_iso_pacman_conf(cfg)
        sanitize_live_packages(cfg, entries)
        return build_iso_image(cfg, user)
    finally:
        remove_tree(cfg.workspace)


# ═══════════════════════════════════ UI ═══════════════════════════════════
def prompt_action() -> str:
    table = Table(title=f"Dusky Arch ISO Factory {VERSION}", box=box.ROUNDED)
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column("Action", style="magenta")
    table.add_column("Builds")
    names = list(ACTIONS)
    for i, name in enumerate(names, 1):
        table.add_row(str(i), name, ACTION_HELP[name])
    console.print(table)
    choice = Prompt.ask("Select action", choices=[str(i) for i in range(1, len(names) + 1)], default="1")
    return names[int(choice) - 1]


def prompt_path(msg: str, default: Path) -> Path:
    console.print(f"[cyan]{escape(msg)}[/] (default: [bold]{escape(str(default))}[/])")
    return Path(Prompt.ask("Path", default=str(default))).expanduser().resolve()


# ═══════════════════════════════════ main ═══════════════════════════════════
def main(args: argparse.Namespace) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _on_signal)
    atexit.register(run_cleanups)
    if os.geteuid() != 0:
        die("must run as root (bootstrap re-exec failed)")
    acquire_factory_lock()

    interactive = not args.auto and sys.stdin.isatty()
    action = args.action or (prompt_action() if interactive else "official_iso")
    do_official, do_aur, do_iso = ACTIONS[action]
    user = real_user()
    step(f"action: {action}   real user: {user.name}   home: {user.home}")
    if do_aur and user.is_root:
        die("no non-root invoking user (SUDO_USER/login) — makepkg cannot run as root; "
            "run via sudo from your user account")

    official_repo = (args.official_repo or DEFAULT_OFFICIAL).expanduser()
    aur_repo = (args.aur_repo or DEFAULT_AUR).expanduser()
    source_dir = (args.source_dir or user.home / "user_scripts" / "arch_iso_scripts" / "offline_iso").expanduser()
    if interactive and do_official:
        official_repo = prompt_path("Official repo path", official_repo)
    if interactive and do_aur:
        aur_repo = prompt_path("AUR repo path", aur_repo)
    official_repo, aur_repo, source_dir = (p.resolve() for p in (official_repo, aur_repo, source_dir))

    zram = ZRAM_CANDIDATE.is_mount()
    workspace_base: Path | None = args.workspace
    if do_iso and workspace_base is None:
        use_zram = zram and (not interactive or Confirm.ask(
            f"Detected {ZRAM_CANDIDATE} mounted — use it for the workspace?", default=True))
        workspace_base = ZRAM_CANDIDATE if use_zram else Path("/tmp")

    start = time.monotonic()
    iso: tuple[Path, str] | None = None
    db: IsolatedDB | None = None
    try:
        if do_official or do_aur:
            external = source_dir / "assets" / "iso_temp_packages" / "packages.x86_64"
            master = build_master_list(external)
            db = IsolatedDB()
            db.setup()
            if do_official:
                official_phase(db, master, official_repo, user)
            if do_aur:
                aur_phase(db, master, aur_repo, official_repo, user)
    finally:
        if db is not None:
            db.close()
    if do_iso:
        assert workspace_base is not None
        cfg = IsoConfig(
            workspace=workspace_base.expanduser().resolve() / "dusky_iso",
            source_dir=source_dir,
            official_repo=official_repo,
            aur_repo=aur_repo if aur_repo.is_dir() else None,
            final_dest=ZRAM_CANDIDATE if zram else user.home / "dusky_isos",
        )
        iso = iso_phase(cfg, user)

    elapsed = format_duration(time.monotonic() - start)
    if iso is not None:
        path, digest = iso
        size = human_bytes(path.stat().st_size)
        console.print(Panel(f"[bold green]SUCCESS[/]\nISO: {escape(str(path))}\nSize: {size}\n"
                            f"SHA256: {digest}\nTime: {elapsed}", style="green", box=box.DOUBLE))
        notify("Dusky Factory", f"ISO build complete in {elapsed}: {path.name}\nSize: {size}\nSHA256: {digest}")
    else:
        where = {"official": f"\nLocation: {official_repo}", "aur": f"\nLocation: {aur_repo}"}.get(action, "")
        ok(f"'{action}' complete (took {elapsed})")
        notify("Dusky Factory", f"'{action}' complete in {elapsed}!{where}")


def entry(args: argparse.Namespace) -> int:
    try:
        main(args)
        return 0
    except FactoryError as exc:
        err(str(exc))
        notify("Dusky Factory", f"Failed: {exc}", icon="dialog-error")
        return 1
    except KeyboardInterrupt:
        err("cancelled (SIGINT); child process groups terminated")
        notify("Dusky Factory", "Process interrupted (SIGINT)", icon="dialog-warning")
        return 130
    except Interrupted as exc:
        err(f"terminated by signal {exc.signum}; child process groups terminated")
        notify("Dusky Factory", f"Process interrupted (signal {exc.signum})", icon="dialog-warning")
        return 128 + exc.signum
    except Exception:
        console.print_exception()
        notify("Dusky Factory", "Failed: unhandled exception (see terminal)", icon="dialog-error")
        return 1
    finally:
        terminate_children()
        run_cleanups()


if __name__ == "__main__":
    raise SystemExit(entry(ARGS))
