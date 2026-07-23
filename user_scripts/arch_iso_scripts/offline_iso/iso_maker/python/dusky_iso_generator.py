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
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# ==============================================================================
# Early help (no third-party, no root)
# ==============================================================================
_HELP = """\
Dusky Arch ISO Factory — offline pacman/AUR repos + releng ISO

Usage:
  sudo python3 dusky_factory.py [options]

Options:
  --arch / --cachyos          Repo mode (default: prompt, or cachyos with --auto)
  --action ACTION             official|aur|both|iso|full|official_iso
  --official-repo PATH        Official package repo directory
  --aur-repo PATH             AUR package repo directory
  --workspace PATH            Build workspace (default: /mnt/zram1 or /tmp)
  --source-dir PATH           Installer payload / assets
  --auto                      Non-interactive defaults
  -h, --help                  This help
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

# ==============================================================================
# Package sets
# ==============================================================================
ALL_GROUPS: Dict[str, List[str]] = {
    "offline": [
        "intel-ucode", "amd-ucode", "mkinitcpio", "python-cssselect", "base", "base-devel",
        "python-lxml", "python-certifi", "python-charset-normalizer", "python-idna",
        "python-requests", "python-urllib3", "deno", "yt-dlp", "yt-dlp-ejs", "hunspell",
        "xf86-input-libinput", "xorg-xauth", "boost-libs", "plymouth", "grub", "os-prober",
        "cryptsetup",
    ],
    "graphics": [
        "intel-media-driver", "vpl-gpu-rt", "mesa", "vulkan-intel", "mesa-utils",
        "intel-gpu-tools", "libva", "libva-utils", "vulkan-icd-loader", "vulkan-tools",
        "sof-firmware", "linux-firmware", "linux-headers", "acpi_call", "kernel-modules-hook",
        "linux-firmware-nvidia", "linux-firmware-amdgpu", "linux-firmware-radeon",
        "linux-firmware-intel", "linux-firmware-mediatek", "linux-firmware-broadcom",
        "linux-firmware-atheros", "linux-firmware-realtek", "linux-firmware-cirrus",
        "linux-firmware-other", "linux-firmware-whence",
    ],
    "hyprland": [
        "hyprland", "xorg-xwayland", "xdg-desktop-portal-hyprland", "xdg-desktop-portal-gtk",
        "localsearch", "polkit", "hyprpolkitagent", "xdg-utils", "socat", "inotify-tools",
        "libnotify", "mako", "file",
    ],
    "appearance": [
        "qt5-wayland", "qt6-wayland", "gtk3", "gtk4", "nwg-look", "qt5ct", "qt6ct", "qt6-svg",
        "qt6-multimedia-ffmpeg", "adw-gtk-theme", "upower", "plocate", "matugen",
        "ttf-font-awesome", "ttf-jetbrains-mono-nerd", "otf-atkinsonhyperlegiblemono-nerd",
        "noto-fonts-emoji", "sassc", "python-packaging", "python", "python-gobject",
        "python-cairo", "python-opengl", "gtk-layer-shell", "python-evdev", "python-pyudev",
        "fontconfig", "papirus-icon-theme", "python-pyquery", "python-textual", "python-rich",
    ],
    "desktop": [
        "waybar", "awww", "hyprlock", "hypridle", "hyprsunset", "hyprpicker", "rofi",
        "libdbusmenu-qt5", "libdbusmenu-glib", "brightnessctl",
    ],
    "audio": [
        "pipewire", "pipewire-alsa", "alsa-utils", "wireplumber", "pipewire-pulse", "playerctl",
        "bluez", "bluez-utils", "bluez-hid2hci", "bluez-libs", "bluez-obex", "blueman", "bluetui",
        "pavucontrol", "gst-plugins-base", "gst-libav", "gst-plugins-bad", "gst-plugins-good",
        "gst-plugins-ugly", "gst-plugin-pipewire", "libcanberra", "songrec", "sox",
    ],
    "filesystem": [
        "btrfs-progs", "compsize", "zram-generator", "udisks2", "udiskie", "dosfstools",
        "ntfs-3g", "xdg-user-dirs", "usbutils", "gnome-disk-utility", "unzip", "zip", "unrar",
        "7zip", "cpio", "file-roller", "rsync", "nfs-utils", "nilfs-utils", "smartmontools",
        "dmraid", "hdparm", "hwdetect", "lsscsi", "sg3_utils", "cpupower", "dust", "dkms",
        "thunar", "thunar-archive-plugin", "thunar-volman", "thunar-media-tags-plugin",
        "thunar-shares-plugin", "thunar-vcs-plugin", "tumbler", "ffmpegthumbnailer",
        "webp-pixbuf-loader", "poppler-glib", "libgsf", "libgepub", "libopenraw", "resvg",
        "gvfs", "gvfs-mtp", "gvfs-nfs", "gvfs-smb", "gvfs-gphoto2", "gvfs-afc", "gvfs-dnssd",
        "catfish", "gnome-keyring", "meld", "xreader", "imagemagick",
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
        "ffmpeg", "mpv", "mpv-mpris", "satty", "swayimg", "grim", "slurp", "wl-clipboard",
        "wl-clip-persist", "cliphist", "tesseract-data-eng", "gpu-screen-recorder-ui", "ddcutil",
    ],
    "sysadmin": [
        "btop", "htop", "dgop", "nvtop", "inxi", "sysstat", "sysbench", "logrotate", "acpid",
        "tlp", "tlp-rdw", "thermald", "powertop", "gdu", "iotop", "iftop", "lshw", "hwinfo",
        "dmidecode", "wev", "pacman-contrib", "libsecret", "seahorse", "greetd-agreety",
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
    "otf-atkinson-hyperlegible-next",
    "python-pywalfox",
    "hyprshade",
    "peaclock",
    "tray-tui",
    "xdg-terminal-exec",
    "paru",
    "hyprshutdown",
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


check_startup_elevation_and_deps()

try:
    from rich import box
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
    _run_cleanups()
    raise SystemExit(128 + signum)


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


def die(msg: str) -> None:
    err(msg)
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
    return "\n" not in s and "\r" not in s and s.strip() == s


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
        if not re.fullmatch(r"[0-9]{1,6}", suid) or not re.fullmatch(r"[0-9]{1,6}", sgid):
            return None, None
        uid = int(suid)
        gid = int(sgid)
        if uid < 1000 or gid < 100:
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
# Isolated pacman DB
# ==============================================================================
@dataclass
class IsolatedDB:
    repo_mode: int
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
    def _patch(text: str, kind: str) -> str:
        if kind == "v3":
            return text.replace("$arch_v3", "x86_64_v3").replace("$arch", "x86_64_v3")
        if kind == "v4":
            return text.replace("$arch_v4", "x86_64_v4").replace("$arch", "x86_64_v4")
        return text.replace("$arch", "x86_64")

    def generate_conf(self) -> None:
        src = Path("/etc/pacman.conf").read_text(encoding="utf-8")
        for f in Path("/etc/pacman.d").glob("*mirrorlist*"):
            if not f.is_file() or f.name.endswith(".pacnew"):
                continue
            if self.repo_mode != 2 and "cachyos" in f.name:
                continue
            dest = self.pacman_d / f.name
            txt = f.read_text(encoding="utf-8")
            if "cachyos-v3" in f.name:
                txt = self._patch(txt, "v3")
            elif "cachyos-v4" in f.name:
                txt = self._patch(txt, "v4")
            elif "cachyos" in f.name:
                txt = self._patch(txt, "std")
            elif f.name == "mirrorlist":
                txt = self._patch(txt, "std")
            dest.write_text(txt, encoding="utf-8")
            dest.chmod(0o644)

        if self.repo_mode == 2:
            v3 = self.pacman_d / "cachyos-v3-mirrorlist"
            if not v3.exists():
                v3.write_text(
                    "Server = https://mirror.cachyos.org/repo/x86_64_v3/$repo\n",
                    encoding="utf-8",
                )
            cm = self.pacman_d / "cachyos-mirrorlist"
            if not cm.exists():
                cm.write_text(
                    "Server = https://mirror.cachyos.org/repo/$arch/$repo\n",
                    encoding="utf-8",
                )

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
                        "ParallelDownloads = 10",
                        (
                            "Architecture = x86_64_v3 x86_64"
                            if self.repo_mode == 2
                            else "Architecture = auto"
                        ),
                    ]
                )
                continue

            if s.startswith("[") and s.endswith("]"):
                if s.startswith("[cachyos"):
                    # Always drop host cachyos sections; re-inject controlled ones for mode 2.
                    skip = True
                    continue
                if s == "[core]":
                    if self.repo_mode == 2:
                        ml_v3 = f"{self.workdir}/pacman.d/cachyos-v3-mirrorlist"
                        ml_c = f"{self.workdir}/pacman.d/cachyos-mirrorlist"
                        out.extend(
                            [
                                "# --- INJECTED CACHYOS v3 ---",
                                "[cachyos-v3]",
                                f"Include = {ml_v3}",
                                "",
                                "[cachyos-core-v3]",
                                f"Include = {ml_v3}",
                                "",
                                "[cachyos-extra-v3]",
                                f"Include = {ml_v3}",
                                "",
                                "[cachyos]",
                                f"Include = {ml_c}",
                                "# ----------------------------------------",
                                "",
                            ]
                        )
                    skip = False
                else:
                    skip = False

            if skip:
                continue

            if "Include" in line and "/etc/pacman.d/" in line:
                line = line.replace("/etc/pacman.d/", f"{self.workdir}/pacman.d/")
            if re.match(r"^\s*Server\s*=", line) and "$arch" in line and self.repo_mode != 2:
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
            if self.repo_mode == 2:
                run_cmd(["pacman-key", "--populate", "cachyos"], sudo=True, check=False)
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


def generate_whitelist(isolated: IsolatedDB, master: List[str]) -> List[str]:
    info("Resolving full closure (exact filenames)")
    if not master:
        die("Cannot resolve closure of empty master list")
    empty = secure_mkdtemp("dusky-empty-")
    try:
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
            *master,
            capture=True,
        )
        if r.returncode != 0:
            die(f"Closure failed: {(r.stderr or r.stdout or '')[:1000]}")
        wl: List[str] = []
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
    if pkg.stat().st_size == 0:
        return False
    name = pkg.name
    if name.endswith(".zst"):
        cmd = ["zstd", "-t", "-q", "--", str(pkg)]
    elif name.endswith(".xz"):
        cmd = ["xz", "-t", "-q", "--", str(pkg)]
    else:
        cmd = ["bsdtar", "-tqf", str(pkg)]
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


def download_packages(isolated: IsolatedDB, master: List[str], repo_dir: Path) -> None:
    info(f"Downloading -> {repo_dir}")
    if not master:
        die("Nothing to download")
    if not path_is_safe_conf_value(repo_dir):
        die(f"Unsafe repo path: {repo_dir!r}")
    repo_dir.mkdir(parents=True, exist_ok=True)
    ensure_disk_space(repo_dir, 8 * 1024**3, "official package download")
    gid = get_alpm_gid()
    if gid is not None:
        try:
            os.chown(repo_dir, 0, gid)
            repo_dir.chmod(0o775)
        except PermissionError:
            pass

    for attempt in range(1, 13):
        info(f"Download attempt {attempt}/12")
        for part in repo_dir.glob("*.part"):
            part.unlink(missing_ok=True)
        r = isolated.pacman(
            "-Sw",
            "--cachedir",
            str(repo_dir),
            "--",
            *master,
            capture=False,
            sudo=True,
        )
        if r.returncode == 0:
            corrupt = 0
            for pkg in repo_dir.glob("*.pkg.tar.*"):
                if pkg.name.endswith(".sig") or ".part" in pkg.name:
                    continue
                if not _verify_pkg_archive(pkg):
                    step(f"Corrupt removed: {pkg.name}")
                    pkg.unlink(missing_ok=True)
                    Path(str(pkg) + ".sig").unlink(missing_ok=True)
                    corrupt += 1
            if corrupt == 0:
                ok("Download complete")
                return
            warn(f"{corrupt} corrupt package(s); retrying...")
        else:
            warn(f"Download attempt {attempt} failed")
        time.sleep(min(30.0, (1.5**attempt) + random.uniform(0, 2)))
    die("Download failed after retries")


def prune_unneeded(repo_dir: Path, whitelist: List[str]) -> None:
    info(f"Pruning orphans from {repo_dir}")
    wl_set = set(whitelist)
    del_c = 0
    del_b = 0
    for f in repo_dir.glob("*.pkg.tar.*"):
        if f.name.endswith(".sig"):
            continue
        if f.name not in wl_set:
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


def detect_repo_add_impl() -> str:
    p = Path("/usr/bin/repo-add")
    try:
        if p.read_bytes()[:4] == b"\x7fELF":
            return "rust"
    except OSError:
        pass
    return "bash"


def generate_repo_db(repo_dir: Path, repo_mode: int) -> None:
    info("Generating repo DB")
    for pat in (f"{REPO_NAME}.db*", f"{REPO_NAME}.files*"):
        for f in repo_dir.glob(pat):
            f.unlink(missing_ok=True)

    pkg_files = sorted(
        str(p) for p in repo_dir.glob("*.pkg.tar.*") if not p.name.endswith(".sig")
    )
    if not pkg_files:
        die("No packages to index")

    try:
        pr = subprocess.run(
            ["sort", "-V"],
            input="\n".join(pkg_files),
            text=True,
            capture_output=True,
            shell=False,
            check=False,
        )
        if pr.returncode == 0 and pr.stdout.strip():
            pkg_files = [ln for ln in pr.stdout.splitlines() if ln.strip()]
    except OSError:
        pass

    env = os.environ.copy()
    env["LC_ALL"] = "C.UTF-8"
    if detect_repo_add_impl() == "rust" and repo_mode == 2:
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
    ok("Database created")


# ==============================================================================
# AUR
# ==============================================================================
def aur_get_version(pkg: str) -> Optional[str]:
    q = urllib.parse.urlencode([("arg[]", pkg)], doseq=True)
    url = f"{AUR_RPC}?{q}"
    hdr = {"User-Agent": f"DuskyISO-Builder/{VERSION}", "Accept": "application/json"}
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=15) as resp:
                if getattr(resp, "status", 200) != 200:
                    raise urllib.error.URLError(f"HTTP {getattr(resp, 'status', '?')}")
                raw = resp.read(MAX_MIRROR_HTML)
                data = json.loads(raw.decode())
                for row in data.get("results", []):
                    if row.get("Name") == pkg:
                        return row.get("Version")
                return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
            time.sleep((1.5**attempt) + random.uniform(0, 1))
    return None


def package_is_current(repo: Path, pkg: str, ver: str) -> bool:
    """True if repo already has pkg built for version ver (epoch stripped)."""
    v = ver.split(":")[-1] if ":" in ver else ver
    for p in repo.iterdir():
        if not p.is_file() or p.name.endswith(".sig"):
            continue
        m = PKGFILE_RE.match(p.name)
        if not m:
            continue
        if m.group("name") == pkg and m.group("ver") == v:
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
            if not dep or dep.startswith("so:") or dep.startswith("pkgconfig(") or dep.endswith(".so"):
                continue
            if PKGNAME_RE.fullmatch(dep):
                deps.append(dep)
        return deps
    except OSError:
        return []


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
    gid = get_alpm_gid()
    if gid is not None:
        try:
            os.chown(aur_repo, 0, gid)
            aur_repo.chmod(0o775)
        except PermissionError:
            pass

    official_list: List[str] = []
    for dep in deps:
        r = isolated.pacman("-Si", "--", dep, capture=True)
        if r.returncode == 0:
            official_list.append(dep)
        elif dep not in aur_known:
            step(f"AUR dep queued: {dep}")
            aur_known.add(dep)
            aur_queue.append(dep)

    if not official_list:
        return

    cache_args = ["--cachedir", str(aur_repo)]
    if official is not None and official.exists():
        cache_args += ["--cachedir", str(official)]

    for _ in range(6):
        r = isolated.pacman("-Sw", *cache_args, "--", *official_list, capture=True, sudo=True)
        if r.returncode == 0:
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
) -> Tuple[bool, bool]:
    """Returns (success, skipped)."""
    info(f"Processing AUR: {pkg}")
    if not PKGNAME_RE.fullmatch(pkg):
        err(f"Invalid AUR pkg name: {pkg}")
        return False, False

    ver = aur_get_version(pkg)
    if not ver:
        r = isolated.pacman("-Si", "--", pkg, capture=True)
        if r.returncode == 0:
            step(f"{pkg} is in official repos; skipping AUR")
            return True, True
        err(f"{pkg} not found on AUR")
        return False, False

    if package_is_current(aur_repo, pkg, ver):
        step(f"{pkg}-{ver} already present")
        return True, True

    clone_root = clone_base / f"clone_{pkg}"
    if clone_root.exists():
        shutil.rmtree(clone_root)
    clone_root.mkdir(parents=True)

    target_dir = clone_root / pkg
    cloned = False
    for _ in range(6):
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        r = run_cmd(
            [
                "git",
                "clone",
                "--depth",
                "1",
                f"https://aur.archlinux.org/{pkg}.git",
                str(target_dir),
            ],
            as_user=real_user,
            capture=True,
            check=False,
        )
        if r.returncode == 0:
            cloned = True
            break
        time.sleep(2)
    if not cloned:
        err(f"Clone failed {pkg}")
        return False, False

    pkgbuild_dir = target_dir
    if not (pkgbuild_dir / "PKGBUILD").exists():
        err(f"PKGBUILD missing {pkg}")
        return False, False

    build_work = clone_base / f"work_{pkg}"
    src_dest = build_work / "src"
    pkgdest = build_work / "pkgdest"
    for d in (build_work, src_dest, pkgdest):
        d.mkdir(parents=True, exist_ok=True)

    if os.geteuid() == 0:
        for target in (build_work, clone_root):
            subprocess.run(
                ["chown", "-R", "-h", "--no-dereference", f"{real_user}:", str(target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
            )

    env = os.environ.copy()
    env.update(
        {
            "PKGDEST": str(pkgdest),
            "BUILDDIR": str(build_work),
            "SRCDEST": str(src_dest),
            "GRADLE_OPTS": "-Dorg.gradle.daemon=false -Dorg.gradle.console=plain",
            "GRADLE_USER_HOME": str(build_work / ".gradle"),
            "CI": "1",
        }
    )

    success = False
    last_out = ""
    for attempt in range(1, 7):
        if os.geteuid() != 0:
            subprocess.run(
                ["sudo", "-v"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
            )
        try:
            r = run_cmd(
                ["makepkg", "-s", "--noconfirm", "--cleanbuild", "--cleanafter"],
                as_user=real_user,
                env=env,
                cwd=pkgbuild_dir,
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
            return False, False
        time.sleep(2)

    if not success:
        console.print(f"[red]Build log {pkg}:\n{last_out[-3000:]}[/]")
        shutil.rmtree(clone_root, ignore_errors=True)
        shutil.rmtree(build_work, ignore_errors=True)
        return False, False

    built = [p for p in pkgdest.glob("*.pkg.tar.*") if not p.name.endswith(".sig")]
    if not built:
        err(f"No package produced for {pkg}")
        shutil.rmtree(clone_root, ignore_errors=True)
        shutil.rmtree(build_work, ignore_errors=True)
        return False, False

    for bf in built:
        if not _verify_pkg_archive(bf):
            err(f"Built archive failed verification: {bf.name}")
            shutil.rmtree(clone_root, ignore_errors=True)
            shutil.rmtree(build_work, ignore_errors=True)
            return False, False
        tmp = aur_repo / f".tmp.{bf.name}.{secrets.token_hex(4)}"
        shutil.copy2(str(bf), str(tmp))
        fsync_path(tmp)
        final = aur_repo / bf.name
        os.replace(tmp, final)
        fsync_dir(aur_repo)
        ok(f"Built: {final.name}")
        deps = extract_runtime_deps(final)
        if deps:
            download_official_deps(
                isolated, official_repo, aur_repo, deps, aur_queue, aur_known
            )

    shutil.rmtree(clone_root, ignore_errors=True)
    shutil.rmtree(build_work, ignore_errors=True)
    return True, False


def aur_prune_and_db(
    aur_repo: Path,
    isolated: IsolatedDB,
    repo_mode: int,
    aur_targets: Sequence[str],
) -> None:
    info("Pruning old AUR versions (keep 1) + rebuild DB")
    if check_tool("paccache"):
        run_cmd(
            ["paccache", "-r", "-k", "1", "-c", str(aur_repo)],
            sudo=True,
            check=False,
        )
    generate_repo_db(aur_repo, repo_mode)

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
        generate_repo_db(aur_repo, repo_mode)
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
    repo_mode: int
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
    for old_iso in cfg.final_dest.glob("dusky_*.iso"):
        step(f"Removing old ISO: {old_iso.name}")
        old_iso.unlink(missing_ok=True)

    if cfg.workspace.exists():
        _umount_tree(cfg.workspace)
        shutil.rmtree(cfg.workspace)
    cfg.workspace.mkdir(parents=True)
    cfg.workspace.chmod(0o700)

    src_candidates = [
        Path("/usr/share/archiso/configs/releng"),
        Path("/usr/share/archiso/configs/baseline"),
    ]
    src = next((p for p in src_candidates if p.is_dir()), None)
    if src is None:
        die("archiso releng/baseline not found — install archiso")
    shutil.copytree(src, cfg.profile_dir, symlinks=True)
    ok("Clean room ready")


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

    asset_pkg = cfg.source_dir / "assets" / "iso_temp_packages" / "packages.x86_64"
    if asset_pkg.is_file():
        lines = asset_pkg.read_text(encoding="utf-8").splitlines()
        seen: set[str] = set()
        out_lines: List[str] = []
        for ln in lines:
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            if s not in seen:
                seen.add(s)
                out_lines.append(s)
        (cfg.profile_dir / "packages.x86_64").write_text(
            "\n".join(out_lines) + "\n", encoding="utf-8"
        )

    if cfg.repo_mode == 2:
        with open(cfg.profile_dir / "packages.x86_64", "a", encoding="utf-8") as fh:
            for p in (
                "cachyos-keyring",
                "cachyos-mirrorlist",
                "cachyos-v3-mirrorlist",
                "cachyos-rate-mirrors",
            ):
                fh.write(p + "\n")
        dropin = (
            cfg.profile_dir
            / "airootfs"
            / "etc"
            / "systemd"
            / "system"
            / "pacman-init.service.d"
        )
        dropin.mkdir(parents=True, exist_ok=True)
        (dropin / "cachyos.conf").write_text(
            "[Service]\nExecStart=/usr/bin/pacman-key --populate cachyos\n",
            encoding="utf-8",
        )
    ok("Payloads staged")


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
        "  cd /root/arch_install/ 2>/dev/null && ./000_dusky_arch_install.sh || true\n"
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
        target_repo = tmp_dot / "dusky"
        for attempt in range(1, 4):
            if target_repo.exists():
                shutil.rmtree(target_repo, ignore_errors=True)
            r = subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--filter=blob:none",
                    "https://github.com/dusklinux/dusky",
                    str(target_repo),
                ],
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
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
            )
            chk = subprocess.run(
                ["git", "-C", str(target_repo), "checkout", pin],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
            )
            if chk.returncode != 0:
                die(f"DUSKY_DOTFILES_PIN checkout failed: {pin}")

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
                shutil.copytree(item, dest, symlinks=True, dirs_exist_ok=True)
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
        if cfg.repo_mode == 2 and re.match(r"^\s*Architecture\s*=", s):
            continue
        if s == "[options]":
            out.append(line)
            out.extend(["Color", "ILoveCandy", "VerbosePkgLists", "ParallelDownloads = 10"])
            if cfg.repo_mode == 2:
                out.append("Architecture = x86_64_v3 x86_64")
            out.append(f"CacheDir = {cfg.official_repo}")
            if cfg.aur_repo is not None and cfg.aur_repo.exists():
                out.append(f"CacheDir = {cfg.aur_repo}")
            out.append("CacheDir = /var/cache/pacman/pkg")
            continue
        out.append(line)

    final_txt = "\n".join(out)
    if cfg.repo_mode == 2 and re.search(r"(?m)^\[core\]\s*$", final_txt):
        inj = (
            "# --- INJECTED CACHYOS v3 ---\n"
            "[cachyos-v3]\n"
            "Include = /etc/pacman.d/cachyos-v3-mirrorlist\n\n"
            "[cachyos-core-v3]\n"
            "Include = /etc/pacman.d/cachyos-v3-mirrorlist\n\n"
            "[cachyos-extra-v3]\n"
            "Include = /etc/pacman.d/cachyos-v3-mirrorlist\n\n"
            "[cachyos]\n"
            "Include = /etc/pacman.d/cachyos-mirrorlist\n"
            "# ----------------------------------------\n\n"
            "[core]"
        )
        final_txt = re.sub(r"(?m)^\[core\]\s*$", inj, final_txt, count=1)

    pc.write_text(final_txt + "\n", encoding="utf-8")

    build_d = cfg.profile_dir / "pacman.d"
    build_d.mkdir(exist_ok=True)
    for f in Path("/etc/pacman.d").glob("*mirrorlist*"):
        if f.name.endswith(".pacnew") or not f.is_file():
            continue
        if cfg.repo_mode != 2 and "cachyos" in f.name:
            continue
        dest = build_d / f.name
        if dest.exists():
            continue
        t = f.read_text(encoding="utf-8")
        if "cachyos-v3" in f.name:
            t = t.replace("$arch_v3", "x86_64_v3").replace("$arch", "x86_64_v3")
        elif "cachyos-v4" in f.name:
            t = t.replace("$arch_v4", "x86_64_v4").replace("$arch", "x86_64_v4")
        elif "cachyos" in f.name or f.name == "mirrorlist":
            t = t.replace("$arch", "x86_64")
        dest.write_text(t, encoding="utf-8")
    ok("pacman.conf patched")


def build_iso_image(cfg: ISOConfig) -> Path:
    info("Building ISO")
    ensure_disk_space(cfg.workspace, 12 * 1024**3, "ISO workspace")
    ensure_disk_space(cfg.final_dest, 4 * 1024**3, "ISO output")

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

    off_q = shlex.quote(str(cfg.official_repo.resolve()))
    aur_q = (
        shlex.quote(str(cfg.aur_repo.resolve()))
        if cfg.aur_repo is not None and cfg.aur_repo.exists()
        else None
    )

    inj_lines = [
        '    _msg_info ">>> INJECTING OFFLINE REPOS INTO ISO <<<"',
        '    local isofs_dir="${isofs_dir:?}"',
        '    local install_dir="${install_dir:?}"',
        '    local repo_target="${isofs_dir}/${install_dir}/repo"',
        '    mkdir -p "${repo_target}"',
        (
            f"    rsync -a --exclude='*.db*' --exclude='*.files*' --exclude='*.sig' "
            f'{off_q}/ "${{repo_target}}/" 2>/dev/null '
            f'|| cp -a {off_q}/. "${{repo_target}}/" 2>/dev/null || true'
        ),
    ]
    if aur_q is not None:
        inj_lines.append(
            f"    rsync -a --exclude='*.db*' --exclude='*.files*' --exclude='*.sig' "
            f'{aur_q}/ "${{repo_target}}/" 2>/dev/null '
            f'|| cp -a {aur_q}/. "${{repo_target}}/" 2>/dev/null || true'
        )
    inj_lines.extend(
        [
            "    shopt -s nullglob",
            '    local pkg_files=("${repo_target}/"*.pkg.tar.*)',
            "    local filtered=()",
            '    for f in "${pkg_files[@]}"; do',
            '      [[ "$f" == *.sig ]] && continue',
            '      filtered+=("$f")',
            "    done",
            '    if (( ${#filtered[@]} > 0 )); then',
            '      repo-add --nocolor -q "${repo_target}/archrepo.db.tar.zst" "${filtered[@]}"',
            "    else",
            '      echo "[ERR] No packages in offline repo" >&2',
            "      return 1",
            "    fi",
            "    shopt -u nullglob",
        ]
    )
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
    mk_custom.write_text(content, encoding="utf-8")

    for d in (cfg.work_dir, cfg.out_dir):
        if d.exists():
            _umount_tree(d)
            shutil.rmtree(d)

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
# CachyOS host keyring
# ==============================================================================
def resolve_cachyos_keyring_pkg() -> str:
    url = "https://mirror.cachyos.org/repo/x86_64/cachyos/"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": f"DuskyISO-Builder/{VERSION}"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read(MAX_MIRROR_HTML).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        die(f"Cannot list CachyOS repo for keyring: {exc}")

    names = sorted(set(re.findall(r'href="(cachyos-keyring-[0-9][^"]+\.pkg\.tar\.zst)"', html)))
    if not names:
        die("No cachyos-keyring-*.pkg.tar.zst on mirror listing")
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
            names = [ln for ln in pr.stdout.splitlines() if ln.strip()]
    except OSError:
        pass
    return names[-1]


def ensure_cachyos_keyring_host(*, auto: bool) -> None:
    q = subprocess.run(
        ["pacman", "-Q", "cachyos-keyring"],
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )
    if q.returncode == 0:
        return

    warn("CachyOS mode requires cachyos-keyring on the HOST.")
    if auto or not sys.stdin.isatty():
        die("Missing cachyos-keyring on host (non-interactive).")

    ans = Prompt.ask(
        "Install CachyOS keyring now?",
        choices=["y", "n"],
        default="y",
    )
    if ans.lower() != "y":
        die("Cannot proceed without cachyos-keyring.")

    info("Bootstrapping CachyOS keyring...")
    try:
        subprocess.run(
            [
                "pacman-key",
                "--recv-keys",
                "F3B607488DB35A47",
                "--keyserver",
                "keyserver.ubuntu.com",
            ],
            check=True,
            shell=False,
        )
        subprocess.run(
            ["pacman-key", "--lsign-key", "F3B607488DB35A47"],
            check=True,
            shell=False,
        )
    except subprocess.CalledProcessError as exc:
        die(f"pacman-key bootstrap failed: {exc}")

    pkg_name = resolve_cachyos_keyring_pkg()
    pkg_url = f"https://mirror.cachyos.org/repo/x86_64/cachyos/{pkg_name}"
    step(f"Installing {pkg_name}")
    wait_for_pacman_lock()
    inst = subprocess.run(["pacman", "-U", "--noconfirm", pkg_url], shell=False, check=False)
    if inst.returncode != 0:
        die("Failed to install cachyos-keyring")
    ok("CachyOS keyring installed")


# ==============================================================================
# Prompts / main
# ==============================================================================
def prompt_repo_mode() -> int:
    console.print(Panel("Select Target Repository Mode", style="cyan", box=box.ROUNDED))
    console.print(
        "  [bold]1)[/] Standard Arch Linux (Pure)\n"
        "  [bold]2)[/] CachyOS x86-64-v3 (Optimized + Arch Fallback)"
    )
    return int(Prompt.ask("Enter choice", choices=["1", "2"], default="2"))


def prompt_action() -> str:
    console.print(Panel("Dusky Factory — What to do?", style="cyan", box=box.ROUNDED))
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("No", style="magenta", width=4)
    table.add_column("Action", style="white")
    table.add_row("1", "Official Pacman repo + ISO [default]")
    table.add_row("2", "Download official repo")
    table.add_row("3", "Build AUR repo")
    table.add_row("4", "Both repos")
    table.add_row("5", "Build ISO only")
    table.add_row("6", "Full pipeline: Official + AUR + ISO")
    console.print(table)
    c = Prompt.ask("Enter choice", choices=["1", "2", "3", "4", "5", "6"], default="1")
    return {
        "1": "official_iso",
        "2": "official",
        "3": "aur",
        "4": "both",
        "5": "iso",
        "6": "full",
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
    parser.add_argument("--arch", action="store_true")
    parser.add_argument("--cachyos", action="store_true")
    parser.add_argument(
        "--action",
        choices=["official", "aur", "both", "iso", "full", "official_iso"],
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
    if args.arch and args.cachyos:
        die("Use only one of --arch / --cachyos")

    atexit.register(_run_cleanups)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_exit)
        except (ValueError, OSError):
            pass

    console.print(
        Panel(
            f"Dusky Factory v{VERSION}\nPython {sys.version.split()[0]} · Pacman 7.1 · Archiso 88+",
            style="bold cyan",
            box=box.DOUBLE,
        )
    )
    check_is_arch()
    acquire_factory_lock()

    if args.arch:
        repo_mode = 1
    elif args.cachyos:
        repo_mode = 2
    elif args.auto or not sys.stdin.isatty():
        repo_mode = 2
    else:
        repo_mode = prompt_repo_mode()

    if repo_mode == 2:
        ensure_cachyos_keyring_host(auto=args.auto)

    info(f"Mode: {'Standard Arch' if repo_mode == 1 else 'CachyOS x86-64-v3'}")

    if args.action:
        action = args.action
    elif args.auto or not sys.stdin.isatty():
        action = "official_iso"
    else:
        action = prompt_action()

    real_user, real_home = get_real_user()
    step(f"Real user: {real_user}  home: {real_home}")
    if real_user == "root" and action in {"aur", "both", "full"}:
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
        if action in {"aur", "both", "full"}:
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
    if action in {"iso", "full", "official_iso"} and workspace_base is None:
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

    # ----- Official -----
    if action in {"official", "both", "full", "official_iso"}:
        info("=== OFFICIAL REPO BUILD ===")
        for t in ("pacman", "repo-add", "bsdtar", "zstd", "xz"):
            if not check_tool(t):
                die(f"Missing tool: {t}")
        master = build_master_list(external_pkg_list if external_pkg_list.exists() else None)
        isolated = IsolatedDB(repo_mode=repo_mode)
        try:
            isolated.generate_conf()
            if not isolated.sync():
                die("Sync failed — check network/keyring")
            whitelist = generate_whitelist(isolated, master)
            download_packages(isolated, master, official_repo)
            prune_unneeded(official_repo, whitelist)
            generate_repo_db(official_repo, repo_mode)
            restore_ownership(official_repo)
        finally:
            isolated.cleanup()

    # ----- AUR -----
    if action in {"aur", "both", "full"}:
        info("=== AUR REPO BUILD ===")
        if os.geteuid() == 0:
            warn("Running as root — makepkg/git via runuser as " + real_user)
        for t in ("git", "makepkg", "bsdtar"):
            if not check_tool(t):
                die(f"Missing tool: {t}")

        isolated_aur = IsolatedDB(repo_mode=repo_mode)
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

            aur_queue: List[str] = list(AUR_SEED)
            aur_known: set[str] = set(aur_queue)
            built = skipped = 0
            failed: List[str] = []
            i = 0

            with Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
            ) as prog:
                task_id = prog.add_task("AUR builds", total=len(aur_queue))
                while i < len(aur_queue):
                    pkg = aur_queue[i]
                    prog.update(
                        task_id,
                        description=f"Building {pkg} ({i + 1}/{len(aur_queue)})",
                    )
                    try:
                        ok_flag, was_skip = build_aur_package(
                            pkg,
                            aur_repo,
                            official_repo if official_repo.exists() else None,
                            isolated_aur,
                            clone_base,
                            real_user,
                            aur_queue,
                            aur_known,
                        )
                        if ok_flag:
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
            aur_prune_and_db(aur_repo, isolated_aur, repo_mode, aur_queue)

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
        finally:
            isolated_aur.cleanup()

    # ----- ISO -----
    if action in {"iso", "full", "official_iso"}:
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
            repo_mode=repo_mode,
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
            sha_preview = "?"
            if sha_path.is_file():
                parts = sha_path.read_text(encoding="utf-8").split()
                if parts:
                    sha_preview = parts[0][:16] + "..."
            console.print(
                Panel(
                    f"[bold green]SUCCESS[/]\n"
                    f"ISO: {iso_path}\n"
                    f"Size: {human_bytes(iso_path.stat().st_size)}\n"
                    f"SHA256: {sha_preview}",
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


if __name__ == "__main__":
    main()
