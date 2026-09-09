#!/usr/bin/env python3
# ==============================================================================
# Dusky Arch ISO Factory — Python 3.14.6 / 2026-07 final
# Offline official repo + AUR repo + archiso (releng) image
#
# Stack (no legacy compat):
#   Python 3.14.6 · linux 7.x-arch · systemd 261+
#   pacman 7.1 (DownloadUser=alpm, sandbox) · archiso 88+ · rich 15+
# ==============================================================================

from __future__ import annotations

import atexit
import concurrent.futures
import fcntl
import grp
import hashlib
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
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ==============================================================================
# Early help (no third-party, no root)
# ==============================================================================
_HELP = """\
󰏖 Dusky Arch ISO & Offline Repo Factory
=======================================

Usage:
  ./dusky_iso_generator.py [options]

Pipelines (--action):
  official_iso   Download official Pacman repo + build ISO (Default)
  aur_iso        Build AUR repo + build ISO
  full           Full pipeline: Pacman repo + AUR repo + ISO
  iso            Build ISO only (using existing offline repos)
  official       Download official Pacman repo only (no ISO)
  aur            Build AUR repo only (no ISO)
  both           Build both Pacman & AUR repos (no ISO)

Options:
  --action ACTION        Select pipeline action (see list above)
  --official-repo PATH   Official package repo directory (default: /srv/offline-repo/official)
  --aur-repo PATH        AUR package repo directory (default: /srv/offline-repo/aur)
  --workspace PATH       Scratch workspace for ISO build (default: /mnt/zram1 or /tmp)
  --source-dir PATH      Installer payload and dotfiles assets directory
  --auto                 Non-interactive mode (use defaults without prompting)
  -h, --help             Show this help message and exit

Examples:
  # Interactive menu (prompts for options):
  ./dusky_iso_generator.py

  # Full automated build on ZRAM:
  ./dusky_iso_generator.py --action full --auto

  # Build ISO only with custom workspace:
  ./dusky_iso_generator.py --action iso --workspace /tmp/my_iso_build
"""

if "-h" in sys.argv or "--help" in sys.argv:
    print(_HELP)
    raise SystemExit(0)

VERSION = "7.1.2-py314-2026.07"
REPO_NAME = "archrepo"
AUR_RPC = "https://aur.archlinux.org/rpc/v5/info"
PKGNAME_RE = re.compile(r"^[a-z0-9@_+][a-z0-9@._+\-]*$")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHFJ]|\x1b\]8;;.*?\x1b\\")
# pkgname-version-pkgrel-arch.pkg.tar.(zst|xz|gz|...)
PKGFILE_RE = re.compile(
    r"^(?P<name>.+)-(?P<ver>[^-]+)-(?P<rel>[^-]+)-(?P<arch>[^-]+)\.pkg\.tar\.(?P<ext>.+)$"
)
ZRAM_CANDIDATE = Path("/mnt/zram1")
REEXEC_ENV = "DUSKY_FACTORY_REEXEC"
MAX_MIRROR_HTML = 2 * 1024 * 1024
MAX_RPC_BYTES = MAX_MIRROR_HTML  # AUR JSON cap (same 2 MiB)
PACMAN_SW_CHUNK = 120
AUR_RPC_BATCH = 80
REQUIRED_AUR: frozenset[str] = frozenset({"paru"})

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

_MAKEPKG_ENV_SCRUB = (
    "CFLAGS",
    "CXXFLAGS",
    "CPPFLAGS",
    "FFLAGS",
    "FCFLAGS",
    "LDFLAGS",
    "LTOFLAGS",
    "RUSTFLAGS",
    "MAKEFLAGS",
    "NINJAFLAGS",
    "NPROC",
    "CARGO_BUILD_RUSTFLAGS",
    "CARGO_TARGET_CPU",
    "MAKEPKG_CONF",
    "CARCH",
    "CHOST",
    "DEBUG_CFLAGS",
    "DEBUG_CXXFLAGS",
    "DEBUG_RUSTFLAGS",
    "GOAMD64",
)


# ==============================================================================
# Package sets
# ==============================================================================
ALL_GROUPS: Dict[str, List[str]] = {
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
        #"waybar",
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

# Seed list only — runtime queue is a copy that may grow with AUR deps.
AUR_SEED: Tuple[str, ...] = (
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

# ==============================================================================
# Startup: pacman lock, elevation, deps
# ==============================================================================
def _pacman_like_running() -> bool:
    proc = Path("/proc")
    if not proc.is_dir():
        return True
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except (OSError, PermissionError):
            continue
        if comm in {"pacman", "pacman-conf", "yay", "paru", "makepkg"}:
            return True
    return False


def wait_for_pacman_lock(timeout_s: float = 300.0, poll_s: float = 2.0) -> None:
    lock_file = Path("/var/lib/pacman/db.lck")
    if not lock_file.exists():
        return
    print("[!!] Pacman database lock detected. Waiting...", flush=True)
    deadline = time.monotonic() + timeout_s
    while lock_file.exists():
        if not _pacman_like_running():
            print("[!!] Stale pacman lock (no package manager process). Removing.", flush=True)
            try:
                lock_file.unlink(missing_ok=True)
            except OSError as exc:
                print(f"[XX] Cannot remove stale lock: {exc}", flush=True)
                raise SystemExit(1) from exc
            break
        if time.monotonic() >= deadline:
            print(f"[XX] Pacman lock held after {int(timeout_s)}s. Exiting.", flush=True)
            raise SystemExit(1)
        time.sleep(poll_s)
    print("[OK] Pacman lock clear.", flush=True)


def check_startup_elevation_and_deps() -> None:
    if not Path("/etc/arch-release").exists():
        return

    required_tools = {
        "git": "git",
        "mkarchiso": "archiso",
        "paccache": "pacman-contrib",
        "rsync": "rsync",
        "bsdtar": "libarchive",
        "zstd": "zstd",
        "xz": "xz",
    }
    missing: List[str] = []
    for tool, pkg in required_tools.items():
        if shutil.which(tool) is None:
            missing.append(pkg)

    rich_missing = False
    try:
        import rich  # noqa: F401
    except ImportError:
        rich_missing = True
        missing.append("python-rich")

    missing = list(dict.fromkeys(missing))

    if os.geteuid() != 0:
        if os.environ.get(REEXEC_ENV) == "1":
            print("[XX] Elevation failed (already re-exec'd once).", flush=True)
            raise SystemExit(1)
        print("Elevating privileges to root (may prompt for sudo password)...", flush=True)
        env = os.environ.copy()
        env[REEXEC_ENV] = "1"
        sudo_args = ["sudo", "-E", "--"]
        # Avoid indefinite block on non-TTY without askpass.
        if not sys.stdin.isatty() and not env.get("SUDO_ASKPASS"):
            print("[XX] Root required. Re-run from a TTY or via sudo.", flush=True)
            raise SystemExit(1)
        os.execvpe("sudo", sudo_args + [sys.executable, *sys.argv], env)

    if missing:
        print(f"Installing missing dependencies: {', '.join(missing)}...", flush=True)
        wait_for_pacman_lock()
        syn = subprocess.run(["pacman", "-Sy", "--noconfirm"], shell=False)
        if syn.returncode != 0:
            print("[XX] pacman -Sy failed.", flush=True)
            raise SystemExit(1)
        inst = subprocess.run(
            ["pacman", "-S", "--needed", "--noconfirm", *missing],
            shell=False,
        )
        if inst.returncode != 0:
            print("[XX] pacman -S failed for dependencies.", flush=True)
            raise SystemExit(1)
        if rich_missing:
            # Ensure fresh interpreter state imports rich cleanly.
            env = os.environ.copy()
            env[REEXEC_ENV] = "1"
            os.execvpe(sys.executable, [sys.executable, *sys.argv], env)


if __name__ == "__main__":
    check_startup_elevation_and_deps()

try:
    from rich import box
    from rich.align import Align
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
    from rich.prompt import Confirm, Prompt
    from rich.table import Table
except ImportError:
    print("Missing python-rich. Install: sudo pacman -S python-rich", flush=True)
    raise SystemExit(1)

console = Console()

# ==============================================================================
# Process-global cleanup / lock
# ==============================================================================
_cleanup_paths: List[Path] = []
_factory_lock_fd: Optional[int] = None
_exiting = False


def _register_cleanup(path: Path) -> None:
    _cleanup_paths.append(path)


def _run_cleanups() -> None:
    global _factory_lock_fd
    for p in reversed(_cleanup_paths):
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists() or p.is_symlink():
                p.unlink(missing_ok=True)
        except OSError:
            pass
    _cleanup_paths.clear()
    if _factory_lock_fd is not None:
        try:
            fcntl.flock(_factory_lock_fd, fcntl.LOCK_UN)
            os.close(_factory_lock_fd)
        except OSError:
            pass
        _factory_lock_fd = None


def _signal_exit(signum: int, _frame: object) -> None:
    global _exiting
    if _exiting:
        return
    _exiting = True
    try:
        sys.stderr.write(f"\n[XX] Signal {signum}; cleaning up.\n")
        sys.stderr.flush()
    except OSError:
        pass
    send_notification(
        "Dusky Factory",
        f"Process interrupted (signal {signum})",
        icon="dialog-warning",
    )
    _run_cleanups()
    os._exit(128 + signum)


def acquire_factory_lock() -> None:
    global _factory_lock_fd
    candidates = [
        Path("/run/dusky-iso-factory.lock"),
        Path("/var/lock/dusky-iso-factory.lock"),
        Path(tempfile.gettempdir()) / "dusky-iso-factory.lock",
    ]
    last_err: Optional[BaseException] = None
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                die(f"Another factory instance holds {path}")
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())
            _factory_lock_fd = fd
            return
        except OSError as exc:
            last_err = exc
            continue
    die(f"Cannot create factory lock: {last_err}")


# ==============================================================================
# UI + fs helpers
# ==============================================================================
def info(msg: str) -> None:
    console.print(f"\n[bold cyan]==>[/] {msg}")


def step(msg: str) -> None:
    console.print(f"  [bold magenta]->[/] {msg}")


def ok(msg: str) -> None:
    console.print(f"[bold green][OK][/] {msg}")


def warn(msg: str) -> None:
    console.print(f"[bold yellow][!!][/] {msg}")


def err(msg: str) -> None:
    console.print(f"[bold red][XX][/] {msg}")


def send_notification(title: str, msg: str, icon: str = "dialog-information") -> None:
    notify_bin = shutil.which("notify-send")
    if not notify_bin:
        return

    real_user, _ = get_real_user()
    if os.geteuid() == 0 and real_user and real_user != "root":
        try:
            pw = pwd.getpwnam(real_user)
            env = os.environ.copy()
            bus_path = f"/run/user/{pw.pw_uid}/bus"
            if os.path.exists(bus_path):
                env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_path}"
            cmd = [
                "runuser",
                "-u",
                real_user,
                "--",
                notify_bin,
                title,
                msg,
                "-i",
                icon,
                "-a",
                "Dusky Factory",
            ]
            subprocess.run(
                cmd,
                env=env,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception:
            pass

    cmd = [notify_bin, title, msg, "-i", icon, "-a", "Dusky Factory"]
    subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def die(msg: str) -> None:
    err(msg)
    send_notification("Dusky Factory", f"Failed: {msg}", icon="dialog-error")
    _run_cleanups()
    raise SystemExit(1)


def human_bytes(n: int) -> str:
    if n <= 0:
        return "0 B"
    f = float(n)
    for u in ("B", "KiB", "MiB", "GiB", "TiB"):
        if f < 1024 or u == "TiB":
            return f"{int(f)} {u}" if u == "B" else f"{f:.2f} {u}"
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


def secure_mkdtemp(prefix: str, base: Optional[Path] = None) -> Path:
    parent = str(base) if base is not None else None
    p = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    # Refuse whitespace paths (pacman.conf Include / shell injection surface).
    if any(c.isspace() for c in str(p)):
        shutil.rmtree(p, ignore_errors=True)
        die(f"Temp path contains whitespace: {p}")
    p.chmod(0o700)
    _register_cleanup(p)
    return p


def check_is_arch() -> None:
    if not Path("/etc/arch-release").exists():
        die("Not on Arch Linux")


def check_tool(name: str) -> bool:
    return shutil.which(name) is not None


def path_is_safe_conf_value(p: Path) -> bool:
    s = str(p)
    if not s or s.strip() != s:
        return False
    for c in s:
        if c.isspace() or ord(c) < 32 or c == "#":
            return False
    return True


def get_real_user() -> Tuple[str, Path]:
    su = os.environ.get("SUDO_USER")
    if su and re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", su):
        try:
            pw = pwd.getpwnam(su)
            return su, Path(pw.pw_dir)
        except KeyError:
            pass
    uid = os.getuid() if os.geteuid() == 0 else os.geteuid()
    # When root without SUDO_USER, prefer UID 0 home only as last resort.
    try:
        if os.geteuid() == 0 and not su:
            # Prefer non-root from pwd of login uid if available.
            try:
                login = os.getlogin()
                if login and login != "root":
                    pw = pwd.getpwnam(login)
                    return pw.pw_name, Path(pw.pw_dir)
            except (OSError, KeyError):
                pass
        pw = pwd.getpwuid(uid)
        return pw.pw_name, Path(pw.pw_dir)
    except KeyError:
        return f"uid{uid}", Path.home()


def validate_sudo_ids() -> Tuple[Optional[int], Optional[int]]:
    try:
        suid = os.environ.get("SUDO_UID")
        sgid = os.environ.get("SUDO_GID")
        if not suid or not sgid:
            return None, None
        if not re.fullmatch(r"[0-9]{1,9}", suid) or not re.fullmatch(r"[0-9]{1,9}", sgid):
            return None, None
        uid = int(suid)
        gid = int(sgid)
        if uid == 0:
            return None, None
        pwd.getpwuid(uid)
        grp.getgrgid(gid)
        return uid, gid
    except (KeyError, ValueError, OverflowError):
        return None, None


def ensure_sudo_cached() -> None:
    if os.geteuid() == 0:
        return
    if not check_tool("sudo"):
        die("sudo required")
    console.print("[yellow]Caching sudo (may prompt)...[/]")
    if subprocess.run(["sudo", "-v"], shell=False).returncode != 0:
        die("sudo auth failed")


def restore_ownership(path: Path) -> None:
    if not path.exists():
        return
    uid, gid = validate_sudo_ids()
    if uid is None or gid is None:
        return
    subprocess.run(
        ["chown", "-R", "-h", "--no-dereference", f"{uid}:{gid}", str(path)],
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def fsync_path(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def disk_free(path: Path) -> int:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return int(shutil.disk_usage(probe).free)


def ensure_disk_space(path: Path, need_bytes: int, label: str) -> None:
    free = disk_free(path)
    if free < need_bytes:
        die(
            f"Insufficient disk for {label} at {path}: "
            f"need ~{human_bytes(need_bytes)}, free {human_bytes(free)}"
        )


def is_mountpoint(p: Path) -> bool:
    try:
        return p.is_mount()
    except (OSError, ValueError):
        try:
            return os.path.ismount(str(p))
        except OSError:
            return False


def get_alpm_gid() -> Optional[int]:
    try:
        return grp.getgrnam("alpm").gr_gid
    except KeyError:
        return None


def run_cmd(
    cmd: Sequence[str],
    *,
    sudo: bool = False,
    as_user: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[Path] = None,
    capture: bool = False,
    check: bool = True,
    merge_stderr: bool = False,
    timeout: Optional[int] = None,
    non_interactive: bool = False,
) -> subprocess.CompletedProcess[str]:
    full: List[str] = []
    euid_root = os.geteuid() == 0

    if as_user:
        if euid_root:
            full = ["runuser", "-u", as_user, "--"]
        else:
            me = pwd.getpwuid(os.getuid()).pw_name
            if as_user != me:
                full = ["sudo", "-n", "-u", as_user, "--"]
    elif sudo and not euid_root:
        full = ["sudo", "-n", "--"]

    full.extend(cmd)
    res = subprocess.run(
        full,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdin=subprocess.DEVNULL if non_interactive else None,
        stdout=subprocess.PIPE if capture else None,
        stderr=(
            subprocess.STDOUT
            if (capture and merge_stderr)
            else (subprocess.PIPE if capture else None)
        ),
        text=True,
        timeout=timeout,
        shell=False,
        check=False,
    )
    if check and res.returncode != 0:
        if capture:
            err_out = res.stdout if merge_stderr else res.stderr
            console.print(f"[red]Failed: {shlex.join(full)}\n{(err_out or '')[:1000]}[/]")
        raise subprocess.CalledProcessError(res.returncode, list(full), res.stdout, res.stderr)
    return res


# ==============================================================================
# Factory helpers (alpm cache, generic makepkg, versions, chunking)
# ==============================================================================
def assert_x86_64() -> None:
    machine = os.uname().machine
    if machine != "x86_64":
        die(f"This factory only supports x86_64 (uname -m = {machine})")


def prepare_alpm_cache_dir(path: Path) -> None:
    """Make a pkg cache/repo usable with pacman 7 DownloadUser=alpm when possible."""
    path.mkdir(parents=True, exist_ok=True)
    if not path_is_safe_conf_value(path):
        die(f"Unsafe cache/repo path: {path!r}")
    try:
        if path.stat().st_mode & 0o002:
            die(f"Refusing world-writable repo/cache dir: {path}")
    except OSError as exc:
        die(f"Cannot stat {path}: {exc}")
    gid = get_alpm_gid()
    try:
        if gid is not None:
            os.chown(path, 0, gid)
            path.chmod(0o2775)
        else:
            path.chmod(0o755)
    except PermissionError as exc:
        warn(f"alpm perms on {path}: {exc}")


def write_factory_makepkg_conf(dest: Path) -> Path:
    use_mold = check_tool("mold")
    use_ccache = check_tool("ccache")
    ccache_flag = "ccache" if use_ccache else "!ccache"

    rust_common = (
        "-C target-cpu=x86-64 -C opt-level=3 "
        "-C link-arg=-Wl,--as-needed "
        "-C link-arg=-Wl,-z,relro "
        "-C link-arg=-Wl,-z,now "
        "-C link-arg=-Wl,-z,pack-relative-relocs"
    )

    if use_mold:
        ld_line = (
            'LDFLAGS="-Wl,--as-needed -Wl,-z,relro -Wl,-z,now '
            '-Wl,-z,pack-relative-relocs -fuse-ld=mold"'
        )
        rust = f"{rust_common} -C link-arg=-fuse-ld=mold"
        step("makepkg: mold linker enabled")
    else:
        ld_line = (
            'LDFLAGS="-Wl,-O1 -Wl,--sort-common -Wl,--as-needed -Wl,-z,relro -Wl,-z,now '
            '-Wl,-z,pack-relative-relocs"'
        )
        rust = rust_common
        step("makepkg: default linker (install mold for faster link)")
    text = (
        _FACTORY_MAKEPKG_CONF_TEMPLATE.replace("__LDFLAGS_LINE__", ld_line)
        .replace("__RUSTFLAGS__", rust)
        .replace("__CCACHE__", ccache_flag)
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    dest.chmod(0o644)
    return dest


def makepkg_clean_env(conf: Path, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Host -march=native / user CFLAGS must not leak into AUR builds."""
    env = os.environ.copy()
    for k in _MAKEPKG_ENV_SCRUB:
        env.pop(k, None)
    ncores = str(os.cpu_count() or 4)
    env["MAKEPKG_CONF"] = str(conf.resolve())
    env["GOAMD64"] = "v1"
    env["CI"] = "1"
    env["CARGO_BUILD_JOBS"] = ncores
    env["CMAKE_BUILD_PARALLEL_LEVEL"] = ncores
    env["PACKAGER"] = "Dusky Factory <factory@dusky>"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    if extra:
        env.update(extra)
    return env


def git_noninteractive_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env.setdefault("GIT_ASKPASS", "/bin/true")
    if extra:
        env.update(extra)
    return env


def _chunked(items: Sequence[str], n: int) -> Iterable[List[str]]:
    for i in range(0, len(items), n):
        yield list(items[i : i + n])


@dataclass(frozen=True)
class PkgVer:
    epoch: int
    pkgver: str
    pkgrel: str

    @staticmethod
    def parse_rpc(version: str) -> "PkgVer":
        rest = version.strip()
        epoch = 0
        if ":" in rest:
            e, rest = rest.split(":", 1)
            try:
                epoch = int(e)
            except ValueError:
                epoch = 0
        if "-" not in rest:
            return PkgVer(epoch, rest, "0")
        pkgver, pkgrel = rest.rsplit("-", 1)
        return PkgVer(epoch, pkgver, pkgrel)


def parse_pkg_filename(name: str) -> Optional[Tuple[str, str, str, str]]:
    """(pkgname, pkgver, pkgrel, arch) from a package filename."""
    m = PKGFILE_RE.match(name)
    if not m:
        return None
    return m.group("name"), m.group("ver"), m.group("rel"), m.group("arch")


def ensure_archlinux_keyring(isolated: Optional[IsolatedDB] = None) -> None:
    step("Ensuring Arch Linux GPG keys are populated")
    wait_for_pacman_lock()
    subprocess.run(
        ["pacman-key", "--populate", "archlinux"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        check=False,
    )


def estimate_download_bytes(isolated: "IsolatedDB", pkgs: Sequence[str]) -> int:
    """Best-effort; falls back to a safe floor if %s is unsupported."""
    total = 0
    parsed_any = False
    for chunk in _chunked(list(pkgs), PACMAN_SW_CHUNK):
        r = isolated.pacman("-Sp", "--print-format", "%s", "--", *chunk, capture=True)
        if r.returncode != 0:
            continue
        for line in (r.stdout or "").splitlines():
            line = ANSI_RE.sub("", line.strip())
            if line.isdigit():
                total += int(line)
                parsed_any = True
    if not parsed_any or total <= 0:
        return 8 * 1024**3
    return int(total * 1.35) + 512 * 1024**2


# ==============================================================================
# Isolated pacman DB
# ==============================================================================
@dataclass
class IsolatedDB:
    workdir: Path = field(default_factory=lambda: secure_mkdtemp("dusky-isolate-"))
    db_path: Path = field(init=False)
    pacman_d: Path = field(init=False)
    conf_path: Path = field(init=False)

    def __post_init__(self) -> None:
        if any(c.isspace() for c in str(self.workdir)):
            die(f"Isolate workdir whitespace: {self.workdir}")
        self.db_path = self.workdir
        self.pacman_d = self.workdir / "pacman.d"
        self.conf_path = self.workdir / "pacman.conf"
        (self.db_path / "local").mkdir(parents=True, exist_ok=True)
        (self.db_path / "sync").mkdir(parents=True, exist_ok=True)
        self.pacman_d.mkdir(parents=True, exist_ok=True)
        self.db_path.chmod(0o750)
        gid = get_alpm_gid()
        if gid is not None:
            try:
                os.chown(self.db_path, 0, gid)
                os.chown(self.db_path / "sync", 0, gid)
                (self.db_path / "sync").chmod(0o775)
                os.chown(self.db_path / "local", 0, gid)
            except PermissionError:
                pass

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    @staticmethod
    def _patch(text: str) -> str:
        return text.replace("$arch", "x86_64")

    def generate_conf(self) -> None:
        src = Path("/etc/pacman.conf").read_text(encoding="utf-8")
        for f in Path("/etc/pacman.d").glob("*mirrorlist*"):
            if not f.is_file() or f.name.endswith(".pacnew"):
                continue
            if "cachyos" in f.name:
                continue
            dest = self.pacman_d / f.name
            txt = f.read_text(encoding="utf-8")
            txt = self._patch(txt)
            dest.write_text(txt, encoding="utf-8")
            dest.chmod(0o644)

        # Optimize mirrorlist with reflector if available
        if check_tool("reflector"):
            mfile = self.pacman_d / "mirrorlist"
            r = subprocess.run(
                [
                    "reflector",
                    "--latest",
                    "20",
                    "--protocol",
                    "https",
                    "--download-timeout",
                    "3",
                    "--save",
                    str(mfile),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
            )
            if r.returncode == 0 and mfile.is_file() and mfile.stat().st_size > 100:
                step("Isolated mirrorlist optimized via reflector (20 fresh HTTPS mirrors)")

        out: List[str] = []
        skip = False
        for line in src.splitlines():
            s = line.strip()
            if re.match(r"^#?\s*VerbosePkgLists", s):
                continue
            if re.match(r"^#?\s*Color", s):
                continue
            if re.match(r"^#?\s*ILoveCandy", s):
                continue
            if re.match(r"^#?\s*ParallelDownloads", s):
                continue
            if re.match(r"^\s*DownloadUser\b", s):
                continue
            if re.match(r"^\s*Architecture\s*=", s):
                continue
            if re.match(r"^\s*IgnorePkg\b", s):
                continue
            if re.match(r"^\s*IgnoreGroup\b", s):
                continue
            if re.match(r"^\s*DBPath\b", s):
                continue
            if re.match(r"^\s*LogFile\b", s):
                continue

            if s == "[options]":
                out.append(line)
                out.extend(
                    [
                        "Color",
                        "ILoveCandy",
                        "VerbosePkgLists",
                        "ParallelDownloads = 5",
                        "Architecture = auto",
                    ]
                )
                continue

            if s.startswith("[") and s.endswith("]"):
                if s.startswith("[cachyos"):
                    skip = True
                    continue
                skip = False

            if skip:
                continue

            if "Include" in line and "/etc/pacman.d/" in line:
                line = line.replace("/etc/pacman.d/", f"{self.workdir}/pacman.d/")
            if re.match(r"^\s*Server\s*=", line) and "$arch" in line:
                line = line.replace("$arch", "x86_64")
            out.append(line)

        self.conf_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        step(f"Isolated conf at {self.conf_path}")

    def pacman(self, *a: str, capture: bool = False, sudo: bool = False) -> subprocess.CompletedProcess[str]:
        cmd = [
            "pacman",
            "--dbpath",
            str(self.db_path),
            "--gpgdir",
            "/etc/pacman.d/gnupg",
            "--config",
            str(self.conf_path),
            "--disable-download-timeout",
            "--noconfirm",
            "--color",
            "auto",
            *a,
        ]
        return run_cmd(cmd, capture=capture, sudo=sudo, check=False, non_interactive=True)

    def sync(self) -> bool:
        for attempt in range(1, 6):
            step(f"Syncing DB attempt {attempt}/5")
            r = self.pacman("-Sy", capture=True, sudo=True)
            if r.returncode == 0:
                ok("Sync ok")
                return True
            warn(f"Sync failed: {(r.stderr or r.stdout or '')[:500]}")
            if attempt == 3:
                r2 = run_cmd(
                    [
                        "pacman",
                        "--dbpath",
                        str(self.db_path),
                        "--gpgdir",
                        "/etc/pacman.d/gnupg",
                        "--config",
                        str(self.conf_path),
                        "--disable-sandbox-filesystem",
                        "--disable-download-timeout",
                        "--noconfirm",
                        "-Sy",
                    ],
                    capture=True,
                    sudo=True,
                    check=False,
                    non_interactive=True,
                )
                if r2.returncode == 0:
                    ok("Sync ok fallback")
                    return True
            time.sleep(2 + random.uniform(0, 1))
        return False


# ==============================================================================
# Official repo pipeline
# ==============================================================================
def build_master_list(external_path: Optional[Path]) -> List[str]:
    seen: set[str] = set()
    master: List[str] = []
    table = Table(title="Package Groups", box=box.SIMPLE)
    table.add_column("Group", style="magenta")
    table.add_column("Count", style="cyan")
    table.add_column("Unique", style="green")

    for name, pkgs in ALL_GROUPS.items():
        cnt = len(pkgs)
        new = 0
        for p in pkgs:
            if not PKGNAME_RE.fullmatch(p):
                warn(f"Invalid package name {p!r} in {name}")
                continue
            if p not in seen:
                seen.add(p)
                master.append(p)
                new += 1
        table.add_row(name, str(cnt), str(new))
    console.print(table)

    if external_path is not None and external_path.exists():
        try:
            if external_path.is_symlink():
                warn(f"External list is a symlink: {external_path}")
            real = external_path.resolve(strict=True)
            if not real.is_file():
                warn(f"External list not a file: {real}")
            else:
                st = real.stat()
                if st.st_mode & 0o002:
                    die(f"Refusing world-writable external list: {real}")
                txt = (
                    real.read_bytes()
                    .decode("utf-8", errors="strict")
                    .replace("\r\n", "\n")
                    .replace("\r", "\n")
                )
                ext_cnt = 0
                for raw in txt.splitlines():
                    pkg = raw.split("#", 1)[0].strip()
                    if not pkg or any(c.isspace() for c in pkg):
                        continue
                    if not PKGNAME_RE.fullmatch(pkg):
                        continue
                    if pkg not in seen:
                        seen.add(pkg)
                        master.append(pkg)
                        ext_cnt += 1
                step(f"external -> {ext_cnt} unique")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            warn(f"External list fail: {exc}")

    if not master:
        die("Master package list empty")
    ok(f"Master: {len(master)} unique")
    return master


def resolve_official_names(
    isolated: IsolatedDB, names: Sequence[str]
) -> Tuple[List[str], List[str]]:
    """Split master names into (resolvable_official, unresolved)."""
    official: List[str] = []
    unresolved: List[str] = []
    seen_o: set[str] = set()
    seen_u: set[str] = set()

    for chunk in _chunked(list(names), PACMAN_SW_CHUNK):
        r = isolated.pacman(
            "-Sp",
            "--print-format",
            "%n",
            "--color",
            "never",
            "--",
            *chunk,
            capture=True,
        )
        found: set[str] = set()
        if r.returncode == 0:
            for ln in (r.stdout or "").splitlines():
                ln = ANSI_RE.sub("", ln.strip())
                if ln and not ln.lower().startswith("warning"):
                    found.add(ln)

        for n in chunk:
            if n in found:
                if n not in seen_o:
                    seen_o.add(n)
                    official.append(n)
                continue
            si = isolated.pacman("-Si", "--", n, capture=True)
            if si.returncode == 0:
                if n not in seen_o:
                    seen_o.add(n)
                    official.append(n)
            elif n not in seen_u:
                seen_u.add(n)
                unresolved.append(n)

    return official, unresolved


def generate_whitelist(isolated: IsolatedDB, master: List[str]) -> List[str]:
    info("Resolving full closure (exact filenames)")
    if not master:
        die("Cannot resolve closure of empty master list")
    empty = secure_mkdtemp("dusky-empty-")
    try:
        wl: List[str] = []
        for chunk in _chunked(master, PACMAN_SW_CHUNK):
            r = isolated.pacman(
                "-Sw",
                "--print",
                "--print-format",
                "%f",
                "--cachedir",
                str(empty),
                "--color",
                "never",
                "--noprogressbar",
                "--",
                *chunk,
                capture=True,
            )
            if r.returncode != 0:
                die(f"Closure failed: {(r.stderr or r.stdout or '')[:1000]}")
            for line in (r.stdout or "").splitlines():
                line = ANSI_RE.sub("", line.strip())
                if not line or line.lower().startswith(("warning:", "error:", "debug:")):
                    continue
                fname = line.split("/")[-1].split("?")[0]
                if ".pkg.tar." in fname and not fname.endswith(".sig"):
                    wl.append(fname)
        if not wl:
            die("Whitelist empty")
        wl = sorted(set(wl))
        ok(f"Closure: {len(wl)} files")
        return wl
    finally:
        shutil.rmtree(empty, ignore_errors=True)


def _verify_pkg_archive(pkg: Path) -> bool:
    if not pkg.is_file() or pkg.stat().st_size == 0:
        return False
    name = pkg.name
    if name.endswith(".zst"):
        cmd = ["zstd", "-t", "-q", "--", str(pkg)]
    elif name.endswith(".xz"):
        cmd = ["xz", "-t", "-q", "--", str(pkg)]
    else:
        cmd = ["bsdtar", "-tf", str(pkg)]
    return (
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
        ).returncode
        == 0
    )


def verify_package_archives_parallel(
    packages: Sequence[Path], max_workers: Optional[int] = None
) -> List[Tuple[Path, str]]:
    """
    Validates physical package archives in parallel using multi-threaded stream decompression.
    Returns a list of (Path, failure_reason) for any corrupted or invalid archives.
    """
    if not packages:
        return []

    workers = max_workers or min(32, (os.cpu_count() or 4) * 2)

    def _check_one(p: Path) -> Tuple[Path, bool, str]:
        if not p.is_file():
            return (p, False, "File missing on disk")
        try:
            if p.stat().st_size == 0:
                return (p, False, "Zero-byte file")
        except OSError as e:
            return (p, False, f"Stat failed: {e}")

        if not _verify_pkg_archive(p):
            return (p, False, "Archive decompression / checksum verification failed")
        return (p, True, "OK")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_check_one, packages))

    return [(p, reason) for p, is_ok, reason in results if not is_ok]


def audit_repo_database_integrity(
    repo_dir: Path, db_path: Path, max_workers: Optional[int] = None
) -> List[Tuple[str, str]]:
    """
    Full cryptographic audit of a generated pacman repo DB against packages on disk.
    Extracts %FILENAME%, %SHA256SUM%, and %CSIZE% for every entry and verifies:
      1. Physical file existence
      2. Size match
      3. SHA256 cryptographic match
      4. Complete archive stream decompression
    Returns a list of (filename, failure_reason) on any discrepancy.
    """
    if not db_path.is_file():
        return [(db_path.name, "Database file does not exist on disk")]

    db_entries: Dict[str, Dict[str, Any]] = {}
    try:
        with tarfile.open(db_path, "r:*") as tar:
            for member in tar.getmembers():
                if member.name.endswith("/desc"):
                    f = tar.extractfile(member)
                    if not f:
                        continue
                    content = f.read().decode("utf-8", errors="ignore")
                    fn, csum, csize = None, None, None
                    lines = content.splitlines()
                    for idx, line in enumerate(lines):
                        if line == "%FILENAME%":
                            fn = lines[idx + 1].strip()
                        elif line == "%SHA256SUM%":
                            csum = lines[idx + 1].strip()
                        elif line == "%CSIZE%":
                            try:
                                csize = int(lines[idx + 1].strip())
                            except ValueError:
                                pass
                    if fn and csum:
                        db_entries[fn] = {"sha256": csum, "csize": csize}
    except Exception as exc:
        return [(db_path.name, f"Failed to parse database tar archive: {exc}")]

    if not db_entries:
        return [(db_path.name, "Database contains zero valid package descriptors")]

    workers = max_workers or min(32, (os.cpu_count() or 4) * 2)

    def _verify_entry(fn: str, expected: Dict[str, Any]) -> Tuple[str, bool, str]:
        pkg_file = repo_dir / fn
        if not pkg_file.is_file():
            return (fn, False, "Package referenced in DB does not exist on disk")
        try:
            st = pkg_file.stat()
            if expected.get("csize") is not None and st.st_size != expected["csize"]:
                return (
                    fn,
                    False,
                    f"Size mismatch: DB={expected['csize']}, Disk={st.st_size}",
                )
        except OSError as e:
            return (fn, False, f"Stat failed: {e}")

        # Cryptographic SHA256 check
        try:
            hasher = hashlib.sha256()
            with open(pkg_file, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    hasher.update(chunk)
            actual_sha = hasher.hexdigest()
            if actual_sha.lower() != expected["sha256"].lower():
                return (
                    fn,
                    False,
                    f"SHA256 mismatch: DB={expected['sha256']}, Disk={actual_sha}",
                )
        except Exception as e:
            return (fn, False, f"Hashing failed: {e}")

        # Stream decompression test
        if not _verify_pkg_archive(pkg_file):
            return (fn, False, "Decompression verification failed (zstd/xz/tar)")

        return (fn, True, "OK")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_verify_entry, fn, exp) for fn, exp in db_entries.items()
        ]
        results = [f.result() for f in futures]

    return [(fn, reason) for fn, is_ok, reason in results if not is_ok]


def _pkgnames_from_pkgfiles(repo_dir: Path) -> Dict[str, List[Path]]:
    by_name: Dict[str, List[Path]] = {}
    for f in repo_dir.glob("*.pkg.tar.*"):
        if f.name.endswith(".sig") or ".part" in f.name:
            continue
        parsed = parse_pkg_filename(f.name)
        pkgname: Optional[str] = parsed[0] if parsed else None
        if pkgname is None:
            try:
                pr = subprocess.run(
                    ["bsdtar", "-xOqf", str(f), ".PKGINFO"],
                    stdout=subprocess.PIPE,
                    text=True,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    check=False,
                )
                m = re.search(r"^pkgname = (.+)$", pr.stdout or "", re.MULTILINE)
                pkgname = m.group(1).strip() if m else None
            except OSError:
                pkgname = None
        if not pkgname:
            pkgname = f.name.split("-")[0]
        by_name.setdefault(pkgname, []).append(f)
    return by_name


def prune_repo_keep_names(repo_dir: Path, keep_names: set[str]) -> None:
    """Drop pkgs whose pkgname is outside keep_names; keep newest ver per name."""
    info(f"Pruning {repo_dir} (keep {len(keep_names)} names)")
    del_c = 0
    del_b = 0
    by_name = _pkgnames_from_pkgfiles(repo_dir)

    def _version_order(paths: List[Path]) -> List[Path]:
        names = [p.name for p in paths]
        try:
            pr = subprocess.run(
                ["sort", "-V"],
                input="\n".join(names),
                text=True,
                capture_output=True,
                shell=False,
                check=False,
            )
            if pr.returncode == 0 and pr.stdout.strip():
                order = [ln for ln in pr.stdout.splitlines() if ln.strip()]
                rank = {n: i for i, n in enumerate(order)}
                return sorted(paths, key=lambda p: rank.get(p.name, 0))
        except OSError:
            pass
        return sorted(paths, key=lambda p: p.name)

    for pkgname, files in by_name.items():
        if pkgname not in keep_names:
            victims = files
            keep_one: Optional[Path] = None
        else:
            ordered = _version_order(files)
            keep_one = ordered[-1]
            victims = [p for p in files if p != keep_one]

        for f in victims:
            try:
                del_b += f.stat().st_size
            except OSError:
                pass
            step(f"pruned: {f.name}")
            f.unlink(missing_ok=True)
            Path(str(f) + ".sig").unlink(missing_ok=True)
            del_c += 1

    for sig in repo_dir.glob("*.sig"):
        base_name = sig.name[: -len(".sig")] if sig.name.endswith(".sig") else sig.name
        if not (repo_dir / base_name).exists():
            sig.unlink(missing_ok=True)

    if del_c:
        ok(f"Pruned {del_c} files, freed {human_bytes(del_b)}")
    else:
        ok("No orphans")


def prune_unneeded(repo_dir: Path, whitelist: List[str]) -> None:
    """Keep pkgnames represented by the (post-download) filename whitelist."""
    keep: set[str] = set()
    for fn in whitelist:
        parsed = parse_pkg_filename(fn)
        if parsed:
            keep.add(parsed[0])
    if not keep:
        die("prune_unneeded: empty keep set")
    prune_repo_keep_names(repo_dir, keep)


def download_packages(isolated: IsolatedDB, master: List[str], repo_dir: Path) -> None:
    info(f"Downloading -> {repo_dir}")
    if not master:
        die("Nothing to download")
    prepare_alpm_cache_dir(repo_dir)

    need = estimate_download_bytes(isolated, master)
    ensure_disk_space(repo_dir, need, "official package download")

    pending = list(master)
    for attempt in range(1, 13):
        info(f"Download attempt {attempt}/12 ({len(pending)} names)")
        for part in repo_dir.glob("*.part"):
            part.unlink(missing_ok=True)

        any_fail = False
        for chunk in _chunked(pending, PACMAN_SW_CHUNK):
            r = isolated.pacman(
                "-Sw",
                "--cachedir",
                str(repo_dir),
                "--",
                *chunk,
                capture=False,
                sudo=True,
            )
            if r.returncode != 0:
                any_fail = True

        pkg_candidates = [
            p
            for p in repo_dir.glob("*.pkg.tar.*")
            if not p.name.endswith(".sig") and ".part" not in p.name
        ]
        corrupted_list = verify_package_archives_parallel(pkg_candidates)
        for bad_p, reason in corrupted_list:
            step(f"Corrupt removed ({reason}): {bad_p.name}")
            bad_p.unlink(missing_ok=True)
            Path(str(bad_p) + ".sig").unlink(missing_ok=True)
        corrupt = len(corrupted_list)

        # Fresh closure from *current* sync DB (avoids stale pre-download whitelist).
        try:
            fresh_wl = generate_whitelist(isolated, master)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            warn(f"Fresh whitelist failed: {exc}")
            fresh_wl = []

        have = {
            p.name
            for p in repo_dir.glob("*.pkg.tar.*")
            if not p.name.endswith(".sig") and ".part" not in p.name
        }
        missing_files = [f for f in fresh_wl if f not in have] if fresh_wl else ["?"]

        if not any_fail and corrupt == 0 and fresh_wl and not missing_files:
            ok("Download complete")
            prune_unneeded(repo_dir, fresh_wl)
            return

        if fresh_wl and missing_files and missing_files != ["?"]:
            retry_names = sorted(
                {
                    parse_pkg_filename(f)[0]
                    for f in missing_files
                    if parse_pkg_filename(f)
                }
            )
            pending = retry_names or list(master)
            warn(
                f"incomplete: missing_files={len(missing_files)} corrupt={corrupt} "
                f"retry_names={len(pending)}"
            )
        else:
            pending = list(master)
            warn(f"Download attempt {attempt} incomplete (fail={any_fail} corrupt={corrupt})")

        time.sleep(min(30.0, (1.5**attempt) + random.uniform(0, 2)))
    die("Download failed after retries")


def detect_repo_add_impl() -> str:
    p = Path("/usr/bin/repo-add")
    try:
        if p.read_bytes()[:4] == b"\x7fELF":
            return "rust"
    except OSError:
        pass
    return "bash"


def generate_repo_db(repo_dir: Path) -> None:
    info("Generating repo DB")
    for pat in (f"{REPO_NAME}.db*", f"{REPO_NAME}.files*"):
        for f in repo_dir.glob(pat):
            f.unlink(missing_ok=True)

    pkg_paths = [
        p
        for p in repo_dir.glob("*.pkg.tar.*")
        if not p.name.endswith(".sig") and ".part" not in p.name
    ]
    if not pkg_paths:
        die("No packages to index")

    corrupted = verify_package_archives_parallel(pkg_paths)
    if corrupted:
        for cp, reason in corrupted:
            err(f"Corrupt package archive detected in {repo_dir.name} ({reason}): {cp.name}")
        die(f"Aborting repo DB generation due to {len(corrupted)} corrupt package(s).")

    try:
        pr = subprocess.run(
            ["sort", "-V"],
            input="\n".join(p.name for p in pkg_paths),
            text=True,
            capture_output=True,
            shell=False,
            check=False,
        )
        if pr.returncode == 0 and pr.stdout.strip():
            order = [ln for ln in pr.stdout.splitlines() if ln.strip()]
            rank = {n: i for i, n in enumerate(order)}
            pkg_paths.sort(key=lambda p: rank.get(p.name, 0))
        else:
            pkg_paths.sort(key=lambda p: p.name)
    except OSError:
        pkg_paths.sort(key=lambda p: p.name)

    pkg_files = [str(p) for p in pkg_paths]

    env = os.environ.copy()
    env["LC_ALL"] = "C.UTF-8"
    if detect_repo_add_impl() == "rust":
        env["RAYON_NUM_THREADS"] = "1"

    token = secrets.token_hex(6)
    db_tmp = repo_dir / f"{REPO_NAME}-tmp-{token}.db.tar.zst"
    res = subprocess.run(
        ["repo-add", "--remove", "--nocolor", str(db_tmp), *pkg_files],
        env=env,
        shell=False,
        check=False,
    )
    if res.returncode != 0:
        for f in repo_dir.glob(f"{REPO_NAME}-tmp-*"):
            f.unlink(missing_ok=True)
        die("repo-add failed")

    final_db = repo_dir / f"{REPO_NAME}.db.tar.zst"
    final_files = repo_dir / f"{REPO_NAME}.files.tar.zst"
    files_tmp = repo_dir / db_tmp.name.replace(".db.", ".files.")

    if not db_tmp.exists():
        die("repo-add did not produce DB temp file")
    fsync_path(db_tmp)
    os.replace(db_tmp, final_db)
    fsync_path(final_db)

    if files_tmp.exists():
        fsync_path(files_tmp)
        os.replace(files_tmp, final_files)
        fsync_path(final_files)

    for f in repo_dir.glob(f"{REPO_NAME}-tmp-*"):
        f.unlink(missing_ok=True)

    for name, target in (("db", final_db), ("files", final_files)):
        if not target.exists():
            continue
        link = repo_dir / f"{REPO_NAME}.{name}"
        if link.exists() or link.is_symlink():
            link.unlink()
        try:
            link.symlink_to(target.name)
        except OSError as exc:
            warn(f"symlink {link.name}: {exc}")
    fsync_dir(repo_dir)

    # Cryptographic post-flight audit: guarantees DB recorded checksum matches every file on disk
    info(f"Auditing cryptographic database integrity for {repo_dir.name}...")
    db_audit_errors = audit_repo_database_integrity(repo_dir, final_db)
    if db_audit_errors:
        for fn, reason in db_audit_errors:
            err(f"DB audit discrepancy ({fn}): {reason}")
        die(f"Repository database audit failed with {len(db_audit_errors)} error(s).")
    ok(f"Database created and cryptographically verified ({len(pkg_files)} packages indexed)")


# ==============================================================================
# AUR
# ==============================================================================
@dataclass(frozen=True)
class AURPackageInfo:
    name: str
    version: str
    pkgbase: str
    depends: Tuple[str, ...] = ()
    makedepends: Tuple[str, ...] = ()


def aur_rpc_info(pkgs: Sequence[str]) -> Dict[str, AURPackageInfo]:
    """Batch AUR RPC v5/info → {Name: AURPackageInfo}."""
    out: Dict[str, AURPackageInfo] = {}
    if not pkgs:
        return out
    hdr = {
        "User-Agent": f"DuskyISO-Builder/{VERSION}",
        "Accept": "application/json",
    }
    for chunk in _chunked(list(pkgs), AUR_RPC_BATCH):
        q = urllib.parse.urlencode([("arg[]", p) for p in chunk], doseq=True)
        url = f"{AUR_RPC}?{q}"
        data: Optional[dict] = None
        for attempt in range(5):
            try:
                req = urllib.request.Request(url, headers=hdr)
                with urllib.request.urlopen(req, timeout=20) as resp:
                    status = getattr(resp, "status", 200)
                    if status == 429:
                        time.sleep(5 + attempt * 3 + random.uniform(0, 1))
                        continue
                    if status != 200:
                        raise urllib.error.URLError(f"HTTP {status}")
                    raw = resp.read(MAX_RPC_BYTES)
                    data = json.loads(raw.decode())
                break
            except (
                urllib.error.URLError,
                TimeoutError,
                json.JSONDecodeError,
                OSError,
                ValueError,
            ):
                time.sleep((1.5**attempt) + random.uniform(0, 1))
        if not data:
            continue
        for row in data.get("results", []):
            name = row.get("Name")
            ver = row.get("Version")
            pkgbase = row.get("PackageBase") or name
            if isinstance(name, str) and isinstance(ver, str) and isinstance(pkgbase, str):
                deps = tuple(row.get("Depends") or [])
                makedeps = tuple(row.get("MakeDepends") or [])
                out[name] = AURPackageInfo(
                    name=name,
                    version=ver,
                    pkgbase=pkgbase,
                    depends=deps,
                    makedepends=makedeps,
                )
    return out


def aur_get_info(pkg: str) -> Optional[AURPackageInfo]:
    return aur_rpc_info([pkg]).get(pkg)


def aur_get_version(pkg: str) -> Optional[str]:
    info_obj = aur_get_info(pkg)
    return info_obj.version if info_obj else None


def package_is_current(repo: Path, pkg: str, ver: str) -> bool:
    """True if repo has name-pkgver-pkgrel-(x86_64|any). Epoch is not in filenames."""
    if not ver or not repo.is_dir():
        return False
    want = PkgVer.parse_rpc(ver)
    for p in repo.iterdir():
        if not p.is_file() or p.name.endswith(".sig") or ".part" in p.name:
            continue
        parsed = parse_pkg_filename(p.name)
        if not parsed:
            continue
        name, fver, frel, arch = parsed
        if name != pkg:
            continue
        if arch not in {"x86_64", "any"}:
            continue
        if fver == want.pkgver and frel == want.pkgrel:
            return True
        if pkg.endswith(("-git", "-hg", "-svn", "-bzr")):
            return True
    return False


def extract_runtime_deps(pkgfile: Path) -> List[str]:
    try:
        r = subprocess.run(
            ["bsdtar", "-xOqf", str(pkgfile), ".PKGINFO"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            shell=False,
            check=False,
        )
        if r.returncode != 0:
            return []
        deps: List[str] = []
        for line in r.stdout.splitlines():
            if not line.startswith("depend = "):
                continue
            dep = line[len("depend = ") :].strip()
            dep = re.split(r"[<>=]", dep, maxsplit=1)[0].strip()
            if not dep or dep.startswith("so:") or dep.startswith("pkgconfig("):
                continue
            if dep.endswith(".so"):
                continue
            if PKGNAME_RE.fullmatch(dep):
                deps.append(dep)
        return deps
    except OSError:
        return []


def parse_srcinfo_text(text: str) -> Tuple[str, List[str], List[str], List[str]]:
    """Returns (pkgbase, pkgnames, depends, makedepends) bare names."""
    pkgbase = ""
    pkgnames: List[str] = []
    depends: List[str] = []
    makedepends: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("pkgbase = "):
            pkgbase = s[len("pkgbase = ") :].strip()
        elif s.startswith("pkgname = "):
            pn = s[len("pkgname = ") :].strip()
            if pn and PKGNAME_RE.fullmatch(pn):
                pkgnames.append(pn)
        elif s.startswith("depends = "):
            raw = s[len("depends = ") :].strip()
            dep = re.split(r"[<>=]", raw, maxsplit=1)[0].strip()
            if dep and PKGNAME_RE.fullmatch(dep):
                depends.append(dep)
        elif s.startswith("makedepends = "):
            raw = s[len("makedepends = ") :].strip()
            dep = re.split(r"[<>=]", raw, maxsplit=1)[0].strip()
            if dep and PKGNAME_RE.fullmatch(dep):
                makedepends.append(dep)
        elif s.startswith("checkdepends = "):
            raw = s[len("checkdepends = ") :].strip()
            dep = re.split(r"[<>=]", raw, maxsplit=1)[0].strip()
            if dep and PKGNAME_RE.fullmatch(dep):
                makedepends.append(dep)
    return pkgbase, pkgnames, depends, makedepends


def classify_deps(
    isolated: IsolatedDB, deps: Sequence[str]
) -> Tuple[List[str], List[str]]:
    """→ (official_or_sync, not_in_sync_assume_aur). Uses isolated DB only."""
    official: List[str] = []
    aurish: List[str] = []
    for dep in deps:
        r = isolated.pacman(
            "-Sp", "--print-format", "%n", "--color", "never", "--", dep, capture=True
        )
        ok_sync = r.returncode == 0 and bool((r.stdout or "").strip())
        if ok_sync:
            official.append(dep)
            continue
        si = isolated.pacman("-Si", "--", dep, capture=True)
        if si.returncode == 0:
            official.append(dep)
        else:
            aurish.append(dep)
    return official, aurish


def pkg_installed(name: str) -> bool:
    return (
        subprocess.run(
            ["pacman", "-Q", "--", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
        ).returncode
        == 0
    )


def find_repo_pkg_files(repo: Path, pkgname: str) -> List[Path]:
    out: List[Path] = []
    if not repo.is_dir():
        return out
    for p in repo.glob("*.pkg.tar.*"):
        if p.name.endswith(".sig") or ".part" in p.name:
            continue
        parsed = parse_pkg_filename(p.name)
        if parsed and parsed[0] == pkgname:
            out.append(p)
    return out





def download_official_deps(
    isolated: IsolatedDB,
    official: Optional[Path],
    aur_repo: Path,
    deps: List[str],
    aur_queue: List[str],
    aur_known: set[str],
) -> None:
    if not deps:
        return
    prepare_alpm_cache_dir(aur_repo)
    official_list, aurish = classify_deps(isolated, deps)
    for dep in aurish:
        if dep not in aur_known:
            step(f"AUR dep queued: {dep}")
            aur_known.add(dep)
            aur_queue.append(dep)
    if not official_list:
        return

    cache_args: List[str] = ["--cachedir", str(aur_repo)]
    if official is not None and official.exists():
        cache_args += ["--cachedir", str(official)]

    for _ in range(6):
        ok_all = True
        for chunk in _chunked(official_list, PACMAN_SW_CHUNK):
            r = isolated.pacman("-Sw", *cache_args, "--", *chunk, capture=True, sudo=True)
            if r.returncode != 0:
                ok_all = False
        if ok_all:
            ok(f"Official deps fetched: {', '.join(official_list)}")
            return
        time.sleep(2 + random.uniform(0, 1))
    warn(f"Official deps incomplete: {', '.join(official_list)}")


def build_aur_package(
    pkg: str,
    aur_repo: Path,
    official_repo: Optional[Path],
    isolated: IsolatedDB,
    clone_base: Path,
    real_user: str,
    aur_queue: List[str],
    aur_known: set[str],
    makepkg_conf: Path,
) -> Tuple[bool, bool, bool]:
    """
    Returns (success, skipped, deferred).
    deferred=True → caller re-appends pkg (AUR deps not ready); not a hard fail.
    """
    info(f"Processing AUR: {pkg}")
    if not PKGNAME_RE.fullmatch(pkg):
        err(f"Invalid AUR pkg name: {pkg}")
        return False, False, False

    info_obj = aur_get_info(pkg)
    if not info_obj:
        r = isolated.pacman("-Si", "--", pkg, capture=True)
        if r.returncode == 0:
            step(f"{pkg} is in official repos; skipping AUR")
            return True, True, False
        err(f"{pkg} not found on AUR")
        return False, False, False

    ver = info_obj.version
    pkgbase = info_obj.pkgbase or pkg

    if package_is_current(aur_repo, pkg, ver):
        step(f"{pkg}-{ver} already present in ISO repo")
        return True, True, False

    clone_root = clone_base / f"clone_{pkgbase}"
    if clone_root.exists():
        shutil.rmtree(clone_root, ignore_errors=True)
    clone_root.mkdir(parents=True)
    if os.geteuid() == 0:
        subprocess.run(
            ["chown", "-R", "-h", "--no-dereference", f"{real_user}:", str(clone_root)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
        )

    target_dir = clone_root / pkgbase
    git_env = git_noninteractive_env()
    cloned = False
    last_clone_err = ""
    for _ in range(6):
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        r = run_cmd(
            [
                "git",
                "clone",
                "--depth",
                "1",
                f"https://aur.archlinux.org/{pkgbase}.git",
                str(target_dir),
            ],
            as_user=real_user,
            env=git_env,
            capture=True,
            check=False,
        )
        last_clone_err = (r.stderr or r.stdout or "").strip()
        if r.returncode == 0:
            cloned = True
            break
        time.sleep(2)
    if not cloned:
        console.print(f"[bold red][XX] Clone failed {pkg} (pkgbase={pkgbase}): {last_clone_err}[/]")
        shutil.rmtree(clone_root, ignore_errors=True)
        return False, False, False

    if not (target_dir / "PKGBUILD").exists():
        err(f"PKGBUILD missing {pkg} in {pkgbase}.git")
        shutil.rmtree(clone_root, ignore_errors=True)
        return False, False, False

    if os.geteuid() == 0:
        subprocess.run(
            ["chown", "-R", "-h", "--no-dereference", f"{real_user}:", str(clone_root)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
        )

    # --- Pre-build deps from .SRCINFO ---
    env_info = makepkg_clean_env(makepkg_conf)
    ps = run_cmd(
        ["makepkg", "--config", str(makepkg_conf), "--printsrcinfo"],
        as_user=real_user,
        env=env_info,
        cwd=target_dir,
        capture=True,
        merge_stderr=True,
        check=False,
        timeout=120,
    )
    pre_deps: List[str] = []
    sibling_pkgnames: set[str] = {pkg, pkgbase}
    if ps.returncode == 0 and ps.stdout:
        src_pkgbase, src_pkgnames, d_list, m_list = parse_srcinfo_text(ps.stdout)
        sibling_pkgnames.update(src_pkgnames)
        if src_pkgbase:
            sibling_pkgnames.add(src_pkgbase)
        pre_deps = list(dict.fromkeys([*d_list, *m_list]))
    else:
        warn(f"{pkg}: makepkg --printsrcinfo failed")

    if pre_deps:
        download_official_deps(
            isolated, official_repo, aur_repo, pre_deps, aur_queue, aur_known
        )
        official_deps, aurish = classify_deps(isolated, pre_deps)
        blocked: List[str] = []
        aur_files_to_install: List[Path] = []
        for dep in aurish:
            if dep in sibling_pkgnames:
                continue
            if pkg_installed(dep):
                continue
            files = find_repo_pkg_files(aur_repo, dep)
            if files:
                aur_files_to_install.append(files[0])
                continue
            # Need it built first
            if dep not in aur_known:
                aur_known.add(dep)
                aur_queue.append(dep)
            blocked.append(dep)

        if blocked:
            step(f"{pkg}: defer — waiting for AUR deps: {', '.join(blocked)}")
            shutil.rmtree(clone_root, ignore_errors=True)
            return False, False, True

        # Install any already-built AUR packages needed on the host
        if aur_files_to_install:
            step(f"Installing built AUR dependency packages: {', '.join(f.name for f in aur_files_to_install)}")
            subprocess.run(
                ["pacman", "-U", "--needed", "--noconfirm", *[str(f) for f in aur_files_to_install]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
            )

        # Install missing official dependencies on host
        if official_deps:
            t_res = subprocess.run(
                ["pacman", "-T", "--", *official_deps],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                shell=False,
                check=False,
            )
            missing_official = [
                line.strip()
                for line in (t_res.stdout or "").splitlines()
                if line.strip() and PKGNAME_RE.fullmatch(line.strip())
            ]
            if missing_official:
                step(f"Installing missing build dependencies on host: {', '.join(missing_official)}")
                cache_dirs: List[str] = ["--cachedir", str(aur_repo)]
                if official_repo is not None and official_repo.exists():
                    cache_dirs += ["--cachedir", str(official_repo)]
                inst_res = subprocess.run(
                    ["pacman", "-S", "--needed", "--noconfirm", *cache_dirs, *missing_official],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    shell=False,
                    check=False,
                )
                if inst_res.returncode != 0:
                    warn(f"Failed to install host build deps ({', '.join(missing_official)}): {inst_res.stdout[:500]}")

    build_work = clone_base / f"work_{pkgbase}"
    src_dest = build_work / "src"
    pkgdest = build_work / "pkgdest"
    for d in (build_work, src_dest, pkgdest):
        d.mkdir(parents=True, exist_ok=True)

    if os.geteuid() == 0:
        subprocess.run(
            ["chown", "-R", "-h", "--no-dereference", f"{real_user}:", str(build_work)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
        )

    env = makepkg_clean_env(
        makepkg_conf,
        {
            "PKGDEST": str(pkgdest),
            "BUILDDIR": str(build_work),
            "SRCDEST": str(src_dest),
            "GRADLE_OPTS": "-Dorg.gradle.daemon=false -Dorg.gradle.console=plain",
            "GRADLE_USER_HOME": str(build_work / ".gradle"),
        },
    )

    success = False
    last_out = ""
    for attempt in range(1, 7):
        try:
            r = run_cmd(
                ["makepkg", "--config", str(makepkg_conf), "--nodeps", "--noconfirm", "--skippgpcheck", "-C"],
                as_user=real_user,
                env=env,
                cwd=target_dir,
                capture=True,
                merge_stderr=True,
                check=False,
                timeout=3600,
            )
            last_out = r.stdout or ""
            if r.returncode == 0:
                success = True
                break
        except subprocess.TimeoutExpired:
            err(f"Timeout {pkg}")
            shutil.rmtree(clone_root, ignore_errors=True)
            shutil.rmtree(build_work, ignore_errors=True)
            return False, False, False
        time.sleep(2)

    if not success:
        console.print(f"[red]Build log {pkg}:\n{last_out[-3000:]}[/]")
        shutil.rmtree(clone_root, ignore_errors=True)
        shutil.rmtree(build_work, ignore_errors=True)
        return False, False, False

    built = [p for p in pkgdest.glob("*.pkg.tar.*") if not p.name.endswith(".sig")]
    if not built:
        err(f"No package produced for {pkg}")
        shutil.rmtree(clone_root, ignore_errors=True)
        shutil.rmtree(build_work, ignore_errors=True)
        return False, False, False

    published: List[Path] = []
    for bf in built:
        if not _verify_pkg_archive(bf):
            err(f"Built archive failed verification: {bf.name}")
            shutil.rmtree(clone_root, ignore_errors=True)
            shutil.rmtree(build_work, ignore_errors=True)
            return False, False, False
        prepare_alpm_cache_dir(aur_repo)
        tmp = aur_repo / f".tmp.{bf.name}.{secrets.token_hex(4)}"
        shutil.copy2(str(bf), str(tmp))
        fsync_path(tmp)
        final = aur_repo / bf.name
        os.replace(tmp, final)
        fsync_dir(aur_repo)
        ok(f"Built: {final.name}")
        published.append(final)
        deps = extract_runtime_deps(final)
        if deps:
            download_official_deps(
                isolated, official_repo, aur_repo, deps, aur_queue, aur_known
            )

    shutil.rmtree(clone_root, ignore_errors=True)
    shutil.rmtree(build_work, ignore_errors=True)
    return True, False, False


def aur_prune_and_db(
    aur_repo: Path,
    isolated: IsolatedDB,
    aur_targets: Sequence[str],
) -> None:
    info("Pruning old AUR versions (keep 1) + rebuild DB")
    if check_tool("paccache"):
        run_cmd(
            ["paccache", "-r", "-k", "1", "-c", str(aur_repo)],
            sudo=True,
            check=False,
        )
    generate_repo_db(aur_repo)

    conf_txt = isolated.conf_path.read_text(encoding="utf-8")
    if f"[{REPO_NAME}]" not in conf_txt:
        if not path_is_safe_conf_value(aur_repo):
            die(f"Unsafe AUR repo path for pacman Server: {aur_repo!r}")
        with open(isolated.conf_path, "a", encoding="utf-8") as fh:
            fh.write(
                f"\n[{REPO_NAME}]\n"
                f"SigLevel = Optional TrustAll\n"
                f"Server = file://{aur_repo}\n"
            )
        isolated.sync()

    if not aur_targets:
        restore_ownership(aur_repo)
        return

    r = isolated.pacman("-Sp", "--print-format", "%n", "--", *aur_targets, capture=True)
    if r.returncode != 0:
        warn("AUR orphan resolve via -Sp failed; skipping orphan prune")
        restore_ownership(aur_repo)
        return

    wl = {
        ln.strip()
        for ln in (r.stdout or "").splitlines()
        if ln.strip() and not ln.lower().startswith("warning")
    }
    targets_set = set(aur_targets)
    dc = 0
    for f in aur_repo.glob("*.pkg.tar.*"):
        if f.name.endswith(".sig"):
            continue
        pkgname: Optional[str] = None
        try:
            pr = subprocess.run(
                ["bsdtar", "-xOqf", str(f), ".PKGINFO"],
                stdout=subprocess.PIPE,
                text=True,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
            )
            m = re.search(r"^pkgname = (.+)$", pr.stdout, re.MULTILINE)
            if m:
                pkgname = m.group(1).strip()
        except OSError:
            pkgname = None
        if pkgname is None:
            pm = PKGFILE_RE.match(f.name)
            pkgname = pm.group("name") if pm else f.name.split("-")[0]
        if pkgname not in wl and pkgname not in targets_set:
            step(f"orphan removed: {pkgname} ({f.name})")
            f.unlink(missing_ok=True)
            Path(str(f) + ".sig").unlink(missing_ok=True)
            dc += 1
    if dc:
        generate_repo_db(aur_repo)
    restore_ownership(aur_repo)


# ==============================================================================
# ISO
# ==============================================================================
@dataclass
class ISOConfig:
    workspace: Path
    profile_dir: Path
    work_dir: Path
    out_dir: Path
    source_dir: Path
    official_repo: Path
    aur_repo: Optional[Path]
    final_dest: Path


def _umount_tree(path: Path) -> None:
    try:
        out = subprocess.run(
            ["findmnt", "-R", str(path)],
            capture_output=True,
            text=True,
            shell=False,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            warn(f"Unmounting binds under {path}")
            subprocess.run(
                ["umount", "-R", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
            )
    except OSError:
        pass


def setup_clean_room(cfg: ISOConfig) -> None:
    info("Clean room")
    cfg.final_dest.mkdir(parents=True, exist_ok=True)
    for old_file in cfg.final_dest.glob("dusky_*"):
        if old_file.is_file() and (old_file.name.endswith(".iso") or old_file.name.endswith(".sha256")):
            step(f"Removing old build artifact: {old_file.name}")
            old_file.unlink(missing_ok=True)

    if cfg.workspace.exists():
        _umount_tree(cfg.workspace)
        shutil.rmtree(cfg.workspace)
    cfg.workspace.mkdir(parents=True)
    cfg.workspace.chmod(0o700)

    src = Path("/usr/share/archiso/configs/releng")
    if not src.is_dir():
        die("archiso releng not found — install archiso (no baseline fallback)")
    shutil.copytree(src, cfg.profile_dir, symlinks=True)
    _patch_profiledef_compression(cfg.profile_dir / "profiledef.sh")
    ok("Clean room ready")


def _patch_profiledef_compression(profiledef: Path) -> None:
    """Best-effort higher squashfs compression; no-op if layout unknown."""
    if not profiledef.is_file():
        return
    txt = profiledef.read_text(encoding="utf-8")
    new_opts = "airootfs_image_tool_options=('-comp' 'zstd' '-b' '1M' '-Xcompression-level' '19')"
    if "airootfs_image_tool_options=" in txt:
        new = re.sub(r"airootfs_image_tool_options=\([^)]*\)", new_opts, txt)
        if new != txt:
            profiledef.write_text(new, encoding="utf-8")
            step("profiledef: set airootfs_image_tool_options=('-comp' 'zstd' '-b' '1M' '-Xcompression-level' '19')")
            return
    step(
        "profiledef: compression left upstream "
        "(check archiso profiledef.sh manually if you want smaller ISOs)"
    )


def stage_payloads(cfg: ISOConfig) -> None:
    info("Staging payloads")
    airootfs_install = cfg.profile_dir / "airootfs" / "root" / "arch_install"
    airootfs_install.mkdir(parents=True, exist_ok=True)
    if cfg.source_dir.exists():
        for item in cfg.source_dir.iterdir():
            if item.name in {".git", ".gitignore"}:
                continue
            dest = airootfs_install / item.name
            if item.is_dir():
                shutil.copytree(item, dest, symlinks=True, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

    installer = airootfs_install / "000_dusky_arch_install.sh"
    if cfg.source_dir.exists() and not installer.is_file():
        warn(f"Expected installer missing: {installer}")

    # Union releng + asset list (dedupe). inject_dotfiles still strips grml-zsh-config.
    releng_pkg = cfg.profile_dir / "packages.x86_64"
    asset_pkg = cfg.source_dir / "assets" / "iso_temp_packages" / "packages.x86_64"
    seen: set[str] = set()
    out: List[str] = []

    def consume(text: str) -> None:
        for ln in text.splitlines():
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            if s not in seen:
                seen.add(s)
                out.append(s)

    if releng_pkg.is_file():
        consume(releng_pkg.read_text(encoding="utf-8"))
    if asset_pkg.is_file():
        consume(asset_pkg.read_text(encoding="utf-8"))
    if not out:
        die("packages.x86_64 empty after union")
    releng_pkg.write_text("\n".join(out) + "\n", encoding="utf-8")
    ok(f"Payloads staged ({len(out)} packages.x86_64 entries)")


def configure_live_hooks(cfg: ISOConfig) -> None:
    info("Live hooks")
    script = cfg.profile_dir / "airootfs" / "root" / ".automated_script.sh"
    # Live installer UX password — not a hardened appliance image.
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
    ok("Live hooks")


def inject_dotfiles(cfg: ISOConfig) -> None:
    info("Injecting dotfiles")
    skel = cfg.profile_dir / "airootfs" / "etc" / "skel"
    if skel.exists():
        shutil.rmtree(skel)
    skel.mkdir(parents=True)

    profiledef = cfg.profile_dir / "profiledef.sh"
    if profiledef.is_file():
        txt = profiledef.read_text(encoding="utf-8")
        txt = re.sub(
            r"# --- DUSKY PERMISSIONS START ---.*?# --- DUSKY PERMISSIONS END ---\n?",
            "",
            txt,
            flags=re.DOTALL,
        )
        profiledef.write_text(txt, encoding="utf-8")

    pkg_file = cfg.profile_dir / "packages.x86_64"
    if pkg_file.is_file():
        ptxt = pkg_file.read_text(encoding="utf-8")
        ptxt = re.sub(r"^\s*grml-zsh-config\s*$", "", ptxt, flags=re.MULTILINE)
        pkg_file.write_text(ptxt, encoding="utf-8")

    tmp_dot = secure_mkdtemp("dusky-dots-")
    try:
        pin = os.environ.get("DUSKY_DOTFILES_PIN", "").strip()
        expect_sha = os.environ.get("DUSKY_DOTFILES_SHA", "").strip().lower()
        target_repo = tmp_dot / "dusky"
        git_env = git_noninteractive_env()
        for attempt in range(1, 4):
            if target_repo.exists():
                shutil.rmtree(target_repo, ignore_errors=True)
            r = subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "https://github.com/dusklinux/dusky",
                    str(target_repo),
                ],
                env=git_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
            )
            if r.returncode == 0:
                break
            if attempt == 3:
                die("Git clone dusky failed")
            time.sleep(2)

        if pin:
            subprocess.run(
                ["git", "-C", str(target_repo), "fetch", "--depth", "1", "origin", pin],
                env=git_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
            )
            chk = subprocess.run(
                ["git", "-C", str(target_repo), "checkout", pin],
                env=git_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
            )
            if chk.returncode != 0:
                die(f"DUSKY_DOTFILES_PIN checkout failed: {pin}")

        head = subprocess.run(
            ["git", "-C", str(target_repo), "rev-parse", "HEAD"],
            env=git_env,
            capture_output=True,
            text=True,
            shell=False,
            check=False,
        )
        head_sha = (head.stdout or "").strip().lower()
        if expect_sha and head_sha and not (
            head_sha == expect_sha or head_sha.startswith(expect_sha)
        ):
            die(f"Dotfiles SHA mismatch: got {head_sha}, expected {expect_sha}")
        if head_sha:
            step(f"Dotfiles HEAD {head_sha[:12]}")

        for item in target_repo.iterdir():
            if item.name == ".git":
                continue
            if item.is_symlink():
                try:
                    tgt = item.resolve(strict=True)
                    if not tgt.is_relative_to(target_repo.resolve()):
                        warn(f"Skipping symlink escape: {item}")
                        continue
                except OSError:
                    warn(f"Skipping dangling symlink: {item}")
                    continue
            dest = skel / item.name
            if item.is_dir():
                shutil.copytree(
                    item,
                    dest,
                    symlinks=False,
                    dirs_exist_ok=True,
                    ignore_dangling_symlinks=True,
                )
            else:
                shutil.copy2(item, dest)

        marker = "# --- AUTOMATED ISO INJECTION: EDITOR & YAZI WRAPPER ---"
        yazi_fn = (
            "\ny() {\n"
            '  local tmp\n'
            '  tmp="$(mktemp -p "${XDG_RUNTIME_DIR:-/tmp}" -t "yazi-cwd.XXXXXX")"\n'
            '  yazi "$@" --cwd-file="$tmp"\n'
            '  if cwd="$(cat -- "$tmp")" && [ -n "$cwd" ] && [ "$cwd" != "$PWD" ]; then\n'
            '    builtin cd -- "$cwd"\n'
            "  fi\n"
            '  rm -f -- "$tmp"\n'
            "}\n"
        )
        for rc_target in (skel, cfg.profile_dir / "airootfs" / "root"):
            rc_target.mkdir(parents=True, exist_ok=True)
            for rc_name in (".bashrc", ".zshrc"):
                rc_path = rc_target / rc_name
                existing = rc_path.read_text(encoding="utf-8") if rc_path.exists() else ""
                if marker in existing:
                    continue
                with open(rc_path, "a", encoding="utf-8") as fh:
                    fh.write("\n" + marker + "\n")
                    fh.write("export EDITOR='nvim'\nexport VISUAL='nvim'\n")
                    fh.write(yazi_fn)

        hypr_src = cfg.source_dir / "assets" / "hyprland" / "hyprland.lua"
        if hypr_src.is_file():
            hypr_dst_dir = skel / ".config" / "hypr"
            hypr_dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(hypr_src, hypr_dst_dir / "hyprland.lua")

        if not profiledef.is_file():
            die("profiledef.sh missing after clean room setup")
        with open(profiledef, "a", encoding="utf-8") as pf:
            pf.write("\n# --- DUSKY PERMISSIONS START ---\n")
            rootfs = cfg.profile_dir / "airootfs"
            for exec_file in skel.rglob("*"):
                if exec_file.is_file() and not exec_file.is_symlink() and os.access(exec_file, os.X_OK):
                    rel = "/" + str(exec_file.relative_to(rootfs))
                    rel_esc = (
                        rel.replace("\\", "\\\\")
                        .replace('"', '\\"')
                        .replace("`", "\\`")
                        .replace("$", "\\$")
                    )
                    pf.write(f'file_permissions+=(["{rel_esc}"]="0:0:0755")\n')
            pf.write("# --- DUSKY PERMISSIONS END ---\n")
        ok("Dotfiles injected")
    finally:
        shutil.rmtree(tmp_dot, ignore_errors=True)


def configure_iso_pacman_conf(cfg: ISOConfig) -> None:
    info("Patching profile pacman.conf")
    if not path_is_safe_conf_value(cfg.official_repo):
        die(f"Unsafe official repo path: {cfg.official_repo!r}")
    if cfg.aur_repo is not None and not path_is_safe_conf_value(cfg.aur_repo):
        die(f"Unsafe AUR repo path: {cfg.aur_repo!r}")

    prepare_alpm_cache_dir(cfg.official_repo)
    if cfg.aur_repo is not None and cfg.aur_repo.exists():
        prepare_alpm_cache_dir(cfg.aur_repo)

    pc = cfg.profile_dir / "pacman.conf"
    if not pc.is_file():
        die("profile pacman.conf missing")
    txt = pc.read_text(encoding="utf-8")
    txt = re.sub(r"^\s*DownloadUser\b.*\n", "", txt, flags=re.MULTILINE)
    lines = txt.splitlines()
    out: List[str] = []
    for line in lines:
        s = line.strip()
        if re.match(r"^#?\s*Color\b", s):
            continue
        if re.match(r"^#?\s*ILoveCandy\b", s):
            continue
        if re.match(r"^#?\s*VerbosePkgLists\b", s):
            continue
        if re.match(r"^#?\s*ParallelDownloads\b", s):
            continue
        if s == "[options]":
            out.append(line)
            out.extend(["Color", "ILoveCandy", "VerbosePkgLists", "ParallelDownloads = 5"])
            out.append(f"CacheDir = {cfg.official_repo}")
            if cfg.aur_repo is not None and cfg.aur_repo.exists():
                out.append(f"CacheDir = {cfg.aur_repo}")
            out.append("CacheDir = /var/cache/pacman/pkg")
            continue
        if s == "[core]":
            # Add offline repository section before [core] so local cached packages take top precedence
            out.append(f"[{REPO_NAME}]")
            out.append("SigLevel = Optional TrustAll")
            out.append(f"Server = file://{cfg.official_repo}")
            if cfg.aur_repo is not None and cfg.aur_repo.exists():
                out.append(f"Server = file://{cfg.aur_repo}")
            out.append("")
            out.append(line)
            continue
        out.append(line)

    final_txt = "\n".join(out)
    pc.write_text(final_txt + "\n", encoding="utf-8")

    build_d = cfg.profile_dir / "pacman.d"
    build_d.mkdir(exist_ok=True)
    for f in Path("/etc/pacman.d").glob("*mirrorlist*"):
        if f.name.endswith(".pacnew") or not f.is_file():
            continue
        if "cachyos" in f.name:
            continue
        dest = build_d / f.name
        if dest.exists():
            continue
        t = f.read_text(encoding="utf-8")
        if f.name == "mirrorlist":
            t = t.replace("$arch", "x86_64")
        dest.write_text(t, encoding="utf-8")
    ok("pacman.conf patched")


def merge_repos_for_iso(
    official: Path, aur: Optional[Path], staging: Path
) -> None:
    """
    Host-side merge into one directory + repo-add DB.
    Official wins on basename collision. Same layout you inject today.
    """
    info("Merging offline repos for ISO (host-side)")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    def copy_tree_pkgs(src: Path, *, label: str, skip_existing: bool) -> None:
        if not src.is_dir():
            return
        for p in src.glob("*.pkg.tar.*"):
            if p.name.endswith(".sig") or ".part" in p.name:
                continue
            dest = staging / p.name
            if dest.exists() and skip_existing:
                warn(f"ISO repo collision ({label} skipped, official kept): {p.name}")
                continue
            shutil.copy2(p, dest)

    copy_tree_pkgs(official, label="official", skip_existing=False)
    if aur is not None and aur.is_dir():
        copy_tree_pkgs(aur, label="aur", skip_existing=True)

    pkgs = [
        p
        for p in staging.glob("*.pkg.tar.*")
        if not p.name.endswith(".sig") and ".part" not in p.name
    ]
    if not pkgs:
        die("Merged ISO repo has zero packages")

    corrupt_pkgs = verify_package_archives_parallel(pkgs)
    if corrupt_pkgs:
        for cp, reason in corrupt_pkgs:
            err(f"Corrupt package archive in staging ({reason}): {cp.name}")
        die(f"Aborting ISO build: {len(corrupt_pkgs)} corrupt package(s) found in staging repository.")

    keep_names: set[str] = set()
    for p in pkgs:
        parsed = parse_pkg_filename(p.name)
        if parsed:
            keep_names.add(parsed[0])
    prune_repo_keep_names(staging, keep_names)
    fsync_dir(staging)
    generate_repo_db(staging)
    n = len(
        [
            p
            for p in staging.glob("*.pkg.tar.*")
            if not p.name.endswith(".sig") and ".part" not in p.name
        ]
    )
    ok(f"ISO staging repo ready ({n} packages + DB)")


def sanitize_and_validate_iso_packages(cfg: ISOConfig, staging_repo: Path) -> None:
    pkg_file = cfg.profile_dir / "packages.x86_64"
    if not pkg_file.is_file():
        return

    info("Sanitizing and validating ISO live environment packages (packages.x86_64)")

    # 1. Available in staging repo
    staging_pkgs: set[str] = set()
    for p in staging_repo.glob("*.pkg.tar.*"):
        if p.name.endswith(".sig") or ".part" in p.name:
            continue
        parsed = parse_pkg_filename(p.name)
        if parsed:
            staging_pkgs.add(parsed[0])

    # 2. Available in official sync DBs
    sync_pkgs: set[str] = set()
    try:
        pr = subprocess.run(
            ["pacman", "-Slq"],
            capture_output=True,
            text=True,
            shell=False,
            check=False,
        )
        if pr.returncode == 0:
            sync_pkgs = {ln.strip() for ln in pr.stdout.splitlines() if ln.strip()}
    except Exception as exc:
        warn(f"Query sync DB failed: {exc}")

    available_universe = staging_pkgs | sync_pkgs

    ALIASES = {
        "broadcom-wl": "broadcom-wl-dkms",
    }

    lines = pkg_file.read_text(encoding="utf-8").splitlines()
    sanitized: List[str] = []
    seen: set[str] = set()

    for line in lines:
        pkg = line.strip()
        if not pkg or pkg.startswith("#"):
            continue

        if pkg in ALIASES:
            target = ALIASES[pkg]
            if target in available_universe or not available_universe:
                step(f"Auto-mapped obsolete live package: '{pkg}' -> '{target}'")
                pkg = target

        if available_universe and pkg not in available_universe:
            warn(
                f"Excluding unavailable package '{pkg}' from packages.x86_64 "
                f"to prevent mkarchiso resolution failure"
            )
            continue

        if pkg not in seen:
            seen.add(pkg)
            sanitized.append(pkg)

    if not sanitized:
        die("Sanitization resulted in empty packages.x86_64")

    pkg_file.write_text("\n".join(sanitized) + "\n", encoding="utf-8")
    ok(f"ISO live packages sanitized ({len(sanitized)} packages)")


def build_iso_image(cfg: ISOConfig) -> Path:
    info("Building ISO")
    final_name = f"dusky_{datetime.now().strftime('%m_%y')}.iso"
    final_path = cfg.final_dest / final_name
    final_sha = cfg.final_dest / f"{final_path.stem}_iso.sha256"
    for f in (final_path, final_sha):
        if f.exists() or f.is_symlink():
            step(f"Removing existing: {f.name}")
            try:
                f.unlink()
            except OSError:
                subprocess.run(["rm", "-f", "--", str(f)], shell=False, check=False)

    mk_src = Path("/usr/bin/mkarchiso")
    if not mk_src.is_file():
        die("mkarchiso missing")
    mk_custom = cfg.workspace / f"mkarchiso_dusky_{secrets.token_hex(6)}"
    shutil.copy2(mk_src, mk_custom)
    mk_custom.chmod(0o755)

    staging = cfg.workspace / "iso_repo_staging"
    merge_repos_for_iso(
        cfg.official_repo,
        cfg.aur_repo if cfg.aur_repo is not None and cfg.aur_repo.exists() else None,
        staging,
    )
    sanitize_and_validate_iso_packages(cfg, staging)
    stage_q = shlex.quote(str(staging.resolve()))

    inj_lines = [
        '    _msg_info ">>> INJECTING OFFLINE REPOS INTO ISO <<<"',
        '    local isofs_dir="${isofs_dir:?}"',
        '    local install_dir="${install_dir:?}"',
        '    local repo_target="${isofs_dir}/${install_dir}/repo"',
        '    mkdir -p "${repo_target}"',
        f'    if ! rsync -a {stage_q}/ "${{repo_target}}/"; then',
        f'      if ! cp -a {stage_q}/. "${{repo_target}}/"; then',
        '        echo "[ERR] Failed to copy offline repo into ISO" >&2',
        "        return 1",
        "      fi",
        "    fi",
        "    sync",
        "    shopt -s nullglob",
        '    local pkgs=( "${repo_target}/"*.pkg.tar.* )',
        '    local dbs=( "${repo_target}/"*.db.tar.* )',
        '    if (( ${#pkgs[@]} < 1 )); then',
        '      echo "[ERR] No packages in offline repo" >&2',
        "      return 1",
        "    fi",
        '    if (( ${#dbs[@]} < 1 )); then',
        '      echo "[ERR] No repo database in offline repo" >&2',
        "      return 1",
        "    fi",
        '    _msg_info ">>> VERIFYING INJECTED REPOSITORY ARCHIVES <<<"',
        '    for _pkg in "${pkgs[@]}"; do',
        '      [[ "${_pkg}" == *.sig ]] && continue',
        '      if [[ "${_pkg}" == *.zst ]]; then',
        '        zstd -t -q "${_pkg}" </dev/null &>/dev/null || { echo "[ERR] Corrupted ZST package detected inside ISO repo: ${_pkg##*/}" >&2; return 1; }',
        '      elif [[ "${_pkg}" == *.xz ]]; then',
        '        xz -t -q "${_pkg}" </dev/null &>/dev/null || { echo "[ERR] Corrupted XZ package detected inside ISO repo: ${_pkg##*/}" >&2; return 1; }',
        '      fi',
        '    done',
        "    sync",
        "    shopt -u nullglob",
    ]
    injection = "\n".join(inj_lines)

    content = mk_custom.read_text(encoding="utf-8")
    marker = "_build_iso_image() {"
    count = content.count(marker)
    if count != 1:
        die(
            f"mkarchiso: expected exactly one {marker!r}, found {count}. "
            "archiso layout changed — update injection."
        )
    content = content.replace(marker, marker + "\n" + injection, 1)

    # Patch mkarchiso _make_pacman_conf to emit each CacheDir on its own line
    pacman_conf_fix = r'''_make_pacman_conf() {
    _msg_info "Copying custom pacman.conf to work directory..."
    local _pconf="${work_dir}/${buildmode}.pacman.conf"
    pacman-conf --config "${pacman_conf}" \
        | sed '/CacheDir/d;/DBPath/d;/HookDir/d;/LogFile/d;/RootDir/d' > "${_pconf}"
    local _cd
    while IFS= read -r _cd; do
        [[ -n "${_cd}" ]] && sed -i "/\[options\]/a CacheDir = ${_cd}" "${_pconf}"
    done < <(pacman-conf --config "${pacman_conf}" CacheDir)
    sed -i "/\[options\]/a HookDir = ${pacstrap_dir}/etc/pacman.d/hooks/" "${_pconf}"
}'''
    if "_make_pacman_conf() {" in content:
        content = re.sub(
            r"_make_pacman_conf\(\) \{.*?\n\}",
            pacman_conf_fix,
            content,
            flags=re.DOTALL,
        )

    mk_custom.write_text(content, encoding="utf-8")

    for d in (cfg.work_dir, cfg.out_dir):
        if d.exists():
            _umount_tree(d)
            shutil.rmtree(d)

    prepare_alpm_cache_dir(cfg.official_repo)
    if cfg.aur_repo is not None and cfg.aur_repo.exists():
        prepare_alpm_cache_dir(cfg.aur_repo)

    repo_bytes = 0
    for root in (cfg.official_repo, cfg.aur_repo):
        if root is None or not root.is_dir():
            continue
        for f in root.glob("*.pkg.tar.*"):
            if f.is_file() and not f.name.endswith(".sig"):
                try:
                    repo_bytes += f.stat().st_size
                except OSError:
                    pass
    ensure_disk_space(
        cfg.workspace,
        max(12 * 1024**3, int(repo_bytes * 2.5) + 4 * 1024**3),
        "ISO workspace",
    )

    cmd = [
        str(mk_custom),
        "-v",
        "-m",
        "iso",
        "-w",
        str(cfg.work_dir),
        "-o",
        str(cfg.out_dir),
        str(cfg.profile_dir),
    ]
    info(f"Running mkarchiso: {shlex.join(cmd)}")
    r = subprocess.run(cmd, shell=False, check=False)
    if r.returncode != 0:
        die("mkarchiso failed")

    iso_files = sorted(cfg.out_dir.glob("*.iso"))
    if not iso_files:
        die("No ISO produced")
    cfg.final_dest.mkdir(parents=True, exist_ok=True)
    shutil.move(str(iso_files[0]), str(final_path))
    fsync_path(final_path)

    sha = hashlib.sha256()
    with open(final_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            sha.update(chunk)
    digest = sha.hexdigest()
    final_sha.write_text(f"{digest}  {final_path.name}\n", encoding="utf-8")
    fsync_path(final_sha)

    uid, gid = validate_sudo_ids()
    if uid is not None and gid is not None:
        for f in (final_path, final_sha):
            try:
                os.chown(f, uid, gid)
            except OSError:
                pass

    ok(f"ISO built: {final_path} ({human_bytes(final_path.stat().st_size)})")
    return final_path


# ==============================================================================
# Prompts / main
# ==============================================================================
def prompt_action() -> str:
    console.print(
        Align.center(
            Panel(
                "[bold cyan]󰏖 Dusky Arch ISO & Repo Factory[/]",
                style="cyan",
                box=box.ROUNDED,
                expand=False,
            )
        )
    )
    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold bright_white",
        title="[bold yellow]Select Build Pipeline[/]",
        title_justify="center",
    )
    table.add_column("No", style="bold yellow", justify="center", width=4)
    table.add_column("Category", style="bold white", width=14)
    table.add_column("Pipeline Action", style="white")
    table.add_column("Scope", style="dim")

    table.add_row(
        "1",
        "[bold cyan]Pacman + ISO[/]",
        "[bold cyan]Pacman Repo[/] + [bold green]ISO[/] [bold yellow](Default)[/]",
        "Download official packages + generate ISO",
    )
    table.add_row(
        "2",
        "[bold magenta]AUR + ISO[/]",
        "[bold magenta]AUR Repo[/] + [bold green]ISO[/]",
        "Compile AUR packages + generate ISO",
    )
    table.add_row(
        "3",
        "[bold green]Full ISO[/]",
        "[bold cyan]Pacman[/] + [bold magenta]AUR[/] + [bold green]ISO[/]",
        "Complete end-to-end factory build",
    )
    table.add_row(
        "4",
        "[bold blue]ISO Only[/]",
        "[bold green]Build ISO[/] only",
        "Assemble ISO from existing offline repos",
    )
    table.add_section()
    table.add_row(
        "5",
        "[bold cyan]Repo Only[/]",
        "Download [bold cyan]Pacman Repo[/] (no ISO)",
        "Sync & cache official repository",
    )
    table.add_row(
        "6",
        "[bold magenta]Repo Only[/]",
        "Build [bold magenta]AUR Repo[/] (no ISO)",
        "Compile & index AUR packages",
    )
    table.add_row(
        "7",
        "[bold purple]Repos Only[/]",
        "Both Repos ([bold cyan]Pacman[/] + [bold magenta]AUR[/], no ISO)",
        "Prepare all offline repos without ISO",
    )
    console.print(table)
    c = Prompt.ask("Enter choice", choices=["1", "2", "3", "4", "5", "6", "7"], default="1")
    return {
        "1": "official_iso",
        "2": "aur_iso",
        "3": "full",
        "4": "iso",
        "5": "official",
        "6": "aur",
        "7": "both",
    }[c]


def prompt_path(msg: str, default: Path) -> Path:
    console.print(f"[cyan]{msg}[/] (default: [bold]{default}[/])")
    inp = Prompt.ask("Path", default=str(default))
    p = Path(inp).expanduser()
    try:
        return p.resolve()
    except OSError:
        return p.absolute()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--action",
        choices=["official", "aur", "both", "iso", "full", "official_iso", "aur_iso"],
    )
    parser.add_argument("--official-repo", type=Path)
    parser.add_argument("--aur-repo", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    args = parser.parse_args()
    if args.help:
        print(_HELP)
        raise SystemExit(0)

    atexit.register(_run_cleanups)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_exit)
        except (ValueError, OSError):
            pass

    check_is_arch()
    assert_x86_64()
    acquire_factory_lock()

    if args.action:
        action = args.action
    elif args.auto or not sys.stdin.isatty():
        action = "official_iso"
    else:
        action = prompt_action()

    real_user, real_home = get_real_user()
    step(f"Real user: {real_user}  home: {real_home}")
    if real_user == "root" and action in {"aur", "both", "full", "aur_iso"}:
        warn("No non-root SUDO_USER/login user — makepkg via runuser to root is invalid")
        die("Invoke with: sudo -u youruser sudo python3 ...  OR export SUDO_USER")

    default_official = Path("/srv/offline-repo/official")
    default_aur = Path("/srv/offline-repo/aur")
    default_source = real_home / "user_scripts" / "arch_iso_scripts" / "offline_iso"
    official_repo = (args.official_repo or default_official).expanduser()
    aur_repo = (args.aur_repo or default_aur).expanduser()
    source_dir = (args.source_dir or default_source).expanduser()
    external_pkg_list = source_dir / "assets" / "iso_temp_packages" / "packages.x86_64"

    if not args.auto and sys.stdin.isatty():
        if action in {"official", "both", "full", "official_iso"}:
            official_repo = prompt_path("Official repo path", official_repo)
        if action in {"aur", "both", "full", "aur_iso"}:
            aur_repo = prompt_path("AUR repo path", aur_repo)

    try:
        official_repo = official_repo.resolve()
    except OSError:
        official_repo = official_repo.absolute()
    try:
        aur_repo = aur_repo.resolve()
    except OSError:
        aur_repo = aur_repo.absolute()
    try:
        source_dir = source_dir.resolve()
    except OSError:
        source_dir = source_dir.absolute()

    workspace_base: Optional[Path] = args.workspace
    if action in {"iso", "full", "official_iso", "aur_iso"} and workspace_base is None:
        if ZRAM_CANDIDATE.exists() and is_mountpoint(ZRAM_CANDIDATE):
            if not args.auto and sys.stdin.isatty():
                use_z = Confirm.ask(
                    f"Detected {ZRAM_CANDIDATE} mounted — use for speed?", default=True
                )
                workspace_base = ZRAM_CANDIDATE if use_z else Path("/tmp")
            else:
                workspace_base = ZRAM_CANDIDATE
        else:
            workspace_base = Path("/tmp")
    if workspace_base is not None:
        workspace_base = workspace_base.expanduser()
        try:
            workspace_base = workspace_base.resolve()
        except OSError:
            workspace_base = workspace_base.absolute()

    ensure_sudo_cached()

    try:
        start_time = time.monotonic()
        # ----- Official -----
        if action in {"official", "both", "full", "official_iso"}:
            info("=== OFFICIAL REPO BUILD ===")
            for t in ("pacman", "repo-add", "bsdtar", "zstd", "xz"):
                if not check_tool(t):
                    die(f"Missing tool: {t}")
            master = build_master_list(
                external_pkg_list if external_pkg_list.exists() else None
            )
            isolated = IsolatedDB()
            try:
                isolated.generate_conf()
                ensure_archlinux_keyring(isolated)
                if not isolated.sync():
                    die("Sync failed — check network/keyring")
                official_names, maybe_aur = resolve_official_names(isolated, master)
                if maybe_aur:
                    warn(
                        f"{len(maybe_aur)} master names not in official sync "
                        f"(fix or add to AUR_SEED): {', '.join(maybe_aur[:40])}"
                        + ("…" if len(maybe_aur) > 40 else "")
                    )
                if not official_names:
                    die("No official packages resolved from master list")
                download_packages(isolated, official_names, official_repo)
                # download_packages already pruned against fresh whitelist
                if check_tool("paccache"):
                    run_cmd(
                        ["paccache", "-r", "-k", "1", "-c", str(official_repo)],
                        sudo=True,
                        check=False,
                    )
                generate_repo_db(official_repo)
                restore_ownership(official_repo)
                # Stash for AUR phase when action includes AUR
                os.environ["DUSKY_UNRESOLVED_OFFICIAL"] = " ".join(maybe_aur)
            finally:
                isolated.cleanup()

        # ----- AUR -----
        if action in {"aur", "both", "full", "aur_iso"}:
            info("=== AUR REPO BUILD ===")
            if os.geteuid() == 0:
                warn("Running as root — makepkg/git via runuser as " + real_user)
            for t in ("git", "makepkg", "bsdtar", "gcc", "make"):
                if not check_tool(t):
                    die(f"Missing tool: {t} (install base-devel)")

            isolated_aur = IsolatedDB()
            try:
                isolated_aur.generate_conf()
                if not isolated_aur.sync():
                    die("AUR isolated sync failed")
                aur_repo.mkdir(parents=True, exist_ok=True)
                ensure_disk_space(aur_repo, 2 * 1024**3, "AUR builds")

                uid, gid = validate_sudo_ids()
                if os.geteuid() == 0 and uid is not None and gid is not None:
                    subprocess.run(
                        [
                            "chown",
                            "-R",
                            "-h",
                            "--no-dereference",
                            f"{uid}:{gid}",
                            str(aur_repo),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        shell=False,
                        check=False,
                    )

                clone_base = secure_mkdtemp("aur-factory-")
                if os.geteuid() == 0 and uid is not None and gid is not None:
                    subprocess.run(
                        [
                            "chown",
                            "-R",
                            "-h",
                            "--no-dereference",
                            f"{uid}:{gid}",
                            str(clone_base),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        shell=False,
                        check=False,
                    )

                makepkg_conf_path = clone_base / "dusky-makepkg.conf"
                write_factory_makepkg_conf(makepkg_conf_path)
                if os.geteuid() == 0 and uid is not None:
                    try:
                        os.chown(makepkg_conf_path, uid, gid if gid is not None else -1)
                    except OSError:
                        pass

                extra_unresolved = [
                    p
                    for p in os.environ.get("DUSKY_UNRESOLVED_OFFICIAL", "").split()
                    if PKGNAME_RE.fullmatch(p)
                ]
                aur_queue: List[str] = list(dict.fromkeys([*AUR_SEED, *extra_unresolved]))
                aur_known: set[str] = set(aur_queue)
                built = skipped = 0
                failed: List[str] = []
                deferred_rounds: Dict[str, int] = {}
                i = 0
                max_defer = 50

                prepare_alpm_cache_dir(aur_repo)

                with Progress(
                    SpinnerColumn(),
                    TextColumn("{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    console=console,
                ) as prog:
                    task_id = prog.add_task("AUR builds", total=max(len(aur_queue), 1))
                    while i < len(aur_queue):
                        pkg = aur_queue[i]
                        prog.update(
                            task_id,
                            description=f"Building {pkg} ({i + 1}/{len(aur_queue)})",
                            total=len(aur_queue),
                        )
                        try:
                            ok_flag, was_skip, deferred = build_aur_package(
                                pkg,
                                aur_repo,
                                official_repo if official_repo.exists() else None,
                                isolated_aur,
                                clone_base,
                                real_user,
                                aur_queue,
                                aur_known,
                                makepkg_conf_path,
                            )
                            if deferred:
                                n = deferred_rounds.get(pkg, 0) + 1
                                deferred_rounds[pkg] = n
                                if n > max_defer:
                                    err(f"Defer limit exceeded for {pkg}")
                                    failed.append(pkg)
                                else:
                                    aur_queue.append(pkg)
                            elif ok_flag:
                                if was_skip:
                                    skipped += 1
                                else:
                                    built += 1
                            else:
                                failed.append(pkg)
                        except Exception as exc:  # noqa: BLE001 — per-pkg isolation
                            err(f"Exception {pkg}: {exc}")
                            failed.append(pkg)
                        i += 1
                        prog.update(task_id, completed=i, total=len(aur_queue))

                shutil.rmtree(clone_base, ignore_errors=True)
                aur_prune_and_db(aur_repo, isolated_aur, aur_queue)

                table = Table(title="AUR Summary", box=box.ROUNDED)
                table.add_column("Metric", style="cyan")
                table.add_column("Value", style="green")
                table.add_row("Built", str(built))
                table.add_row("Skipped", str(skipped))
                table.add_row("Failed", str(len(failed)))
                table.add_row("Queue final", str(len(aur_queue)))
                console.print(table)
                if failed:
                    console.print(f"[red]Failed: {', '.join(failed)}[/]")
                    hard = sorted(set(failed) & set(REQUIRED_AUR))
                    if hard:
                        die(f"Required AUR package(s) failed: {', '.join(hard)}")
            finally:
                isolated_aur.cleanup()

        # ----- ISO -----
        if action in {"iso", "full", "official_iso", "aur_iso"}:
            info("=== ISO BUILD ===")
            if os.geteuid() != 0:
                die("ISO build requires root")
            for t in ("mkarchiso", "git", "rsync"):
                if not check_tool(t):
                    die(f"Missing tool: {t}")
            if workspace_base is None:
                die("Internal error: workspace_base unset")
            if not official_repo.is_dir():
                die(f"Official repo missing at {official_repo} — build it first")

            workspace = workspace_base / "dusky_iso"
            final_dest = (
                ZRAM_CANDIDATE
                if ZRAM_CANDIDATE.exists() and is_mountpoint(ZRAM_CANDIDATE)
                else (real_home / "dusky_isos")
            )
            cfg = ISOConfig(
                workspace=workspace,
                profile_dir=workspace / "profile",
                work_dir=workspace / "work",
                out_dir=workspace / "out",
                source_dir=source_dir,
                official_repo=official_repo,
                aur_repo=aur_repo if aur_repo.is_dir() else None,
                final_dest=final_dest,
            )
            try:
                setup_clean_room(cfg)
                stage_payloads(cfg)
                configure_live_hooks(cfg)
                inject_dotfiles(cfg)
                configure_iso_pacman_conf(cfg)
                iso_path = build_iso_image(cfg)
                sha_path = iso_path.with_name(f"{iso_path.stem}_iso.sha256")
                sha_full = "?"
                if sha_path.is_file():
                    parts = sha_path.read_text(encoding="utf-8").split()
                    if parts:
                        sha_full = parts[0]
                elapsed_str = format_duration(time.monotonic() - start_time)
                console.print(
                    Panel(
                        f"[bold green]SUCCESS[/]\n"
                        f"ISO: {iso_path}\n"
                        f"Size: {human_bytes(iso_path.stat().st_size)}\n"
                        f"SHA256: {sha_full}\n"
                        f"Time: {elapsed_str}",
                        style="green",
                        box=box.DOUBLE,
                    )
                )
            finally:
                if workspace.exists() and (
                    str(workspace).startswith("/tmp/")
                    or str(workspace).startswith(str(ZRAM_CANDIDATE))
                    or workspace.name == "dusky_iso"
                    or workspace.name.startswith("dusky_iso")
                ):
                    _umount_tree(workspace)
                    shutil.rmtree(workspace, ignore_errors=True)

        # Action-specific success notification & summary
        elapsed_str = format_duration(time.monotonic() - start_time)
        if "iso_path" in locals():
            send_notification(
                "Dusky Factory",
                f"ISO build complete in {elapsed_str}: {iso_path.name}\nSize: {human_bytes(iso_path.stat().st_size)}\nSHA256: {sha_full}",
                icon="dialog-information",
            )
        elif action == "official":
            ok(f"Official Pacman repo download complete! (took {elapsed_str})")
            send_notification(
                "Dusky Factory",
                f"Official Pacman repo download complete in {elapsed_str}!\nLocation: {official_repo}",
                icon="dialog-information",
            )
        elif action == "aur":
            ok(f"AUR repo build complete! (took {elapsed_str})")
            send_notification(
                "Dusky Factory",
                f"AUR repo build complete in {elapsed_str}!\nLocation: {aur_repo}",
                icon="dialog-information",
            )
        elif action == "both":
            ok(f"Official & AUR repos build complete! (took {elapsed_str})")
            send_notification(
                "Dusky Factory",
                f"Official & AUR repos build complete in {elapsed_str}!",
                icon="dialog-information",
            )
        else:
            ok(f"Operation '{action}' completed! (took {elapsed_str})")
            send_notification(
                "Dusky Factory",
                f"Operation '{action}' completed in {elapsed_str}!",
                icon="dialog-information",
            )

    except KeyboardInterrupt:
        die("Cancelled by user (Ctrl+C)")
    except SystemExit:
        raise
    except Exception as exc:
        die(f"Unhandled exception: {exc}")


if __name__ == "__main__":
    main()
