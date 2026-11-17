#!/usr/bin/env python3
"""
convert_ubuntu_package_for_arch.sh (debtap-ng)
Modern Debian/Ubuntu (.deb) to Arch Linux (.pkg.tar.zst & PKGBUILD) Converter.
Target Baseline: Arch Linux Rolling (Linux 7.3+, Python 3.15+, systemd 262+, Wayland/Hyprland).
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import contextlib
from dataclasses import dataclass, field
import datetime
import email.parser
import email.policy
import gzip
import hashlib
import io
import lzma
import os
from pathlib import Path
import re
import shutil
import sqlite3
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from typing import Iterable, Iterator

try:
    from compression import zstd as stdlib_zstd  # Python 3.14 / 3.15+ stdlib
except ImportError:
    stdlib_zstd = None

VERSION = "2.0.0"
CACHE_DIR = Path("/var/cache/debtap")
CACHE_DB = CACHE_DIR / "cache.db"

# ANSI Formatting
if sys.stdout.isatty():
    C_GREEN = "\033[1;32m"
    C_RED = "\033[1;31m"
    C_BLUE = "\033[1;34m"
    C_YELLOW = "\033[1;33m"
    C_BOLD = "\033[1m"
    C_RESET = "\033[0m"
else:
    C_GREEN = C_RED = C_BLUE = C_YELLOW = C_BOLD = C_RESET = ""


def log_info(msg: str) -> None:
    print(f"{C_GREEN}==>{C_RESET} {C_BOLD}{msg}{C_RESET}")


def log_step(msg: str) -> None:
    print(f"{C_BLUE}  ->{C_RESET} {C_BOLD}{msg}{C_RESET}")


def log_warn(msg: str) -> None:
    print(f"{C_YELLOW}Warning:{C_RESET} {msg}", file=sys.stderr)


def log_err(msg: str) -> None:
    print(f"{C_RED}Error:{C_RESET} {msg}", file=sys.stderr)


# Ignored Debian-specific package management utilities
IGNORED_DEBIAN_PKGS: frozenset[str] = frozenset({
    "dpkg",
    "apt",
    "apt-get",
    "aptitude",
    "debconf",
    "debconf-2.0",
    "dpkg-dev",
    "init-system-helpers",
    "lsb-base",
    "multiarch-support",
    "python-support",
    "python-central",
    "ucf",
    "install-info",
    "sensible-utils",
})

# Curated Debian/Ubuntu (including Ubuntu 26.04 t64 & GIR) -> Arch Linux package dictionary
STATIC_PKG_MAP: dict[str, str] = {
    # Python ecosystem
    "python3": "python",
    "python3-minimal": "python",
    "python3-dev": "python",
    "python3-all": "python",
    "python-is-python3": "python",
    "python3-gi": "python-gobject",
    "python3-gi-cairo": "python-gobject",
    "python3-cairo": "python-cairo",
    "python3-dbus": "python-dbus",
    "python3-pil": "python-pillow",
    "python3-requests": "python-requests",
    "python3-urllib3": "python-urllib3",
    "python3-yaml": "python-yaml",
    "python3-numpy": "python-numpy",
    "python3-scipy": "python-scipy",
    "python3-matplotlib": "python-matplotlib",
    "python3-lxml": "python-lxml",
    "python3-cryptography": "python-cryptography",
    "python3-psutil": "python-psutil",
    "python3-pydantic": "python-pydantic",
    "python3-aiohttp": "python-aiohttp",
    "python3-bs4": "python-beautifulsoup4",
    "python3-pyqt5": "python-pyqt5",
    "python3-pyqt6": "python-pyqt6",
    "python3-setproctitle": "python-setproctitle",
    # GObject Introspection (gir1.2-*)
    "gir1.2-glib-2.0": "glib2",
    "gir1.2-freedesktop": "gobject-introspection-runtime",
    "gir1.2-gtk-3.0": "gtk3",
    "gir1.2-gtk-4.0": "gtk4",
    "gir1.2-gdkpixbuf-2.0": "gdk-pixbuf2",
    "gir1.2-pango-1.0": "pango",
    "gir1.2-atk-1.0": "at-spi2-core",
    "gir1.2-webkit2-4.0": "webkit2gtk",
    "gir1.2-webkit2-4.1": "webkit2gtk-4.1",
    "gir1.2-webkit-6.0": "webkitgtk-6.0",
    "gir1.2-javascriptcoregtk-4.0": "webkit2gtk",
    "gir1.2-javascriptcoregtk-4.1": "webkit2gtk-4.1",
    "gir1.2-javascriptcoregtk-6.0": "webkitgtk-6.0",
    "gir1.2-soup-2.4": "libsoup",
    "gir1.2-soup-3.0": "libsoup3",
    "gir1.2-notify-0.7": "libnotify",
    "gir1.2-secret-1": "libsecret",
    "gir1.2-adw-1": "libadwaita",
    "gir1.2-gtksource-4": "gtksourceview4",
    "gir1.2-gtksource-5": "gtksourceview5",
    "gir1.2-gst-plugins-base-1.0": "gst-plugins-base-libs",
    "gir1.2-gstreamer-1.0": "gstreamer",
    "gir1.2-ayatanaappindicator3-0.1": "libayatana-appindicator",
    "gir1.2-appindicator3-0.1": "libappindicator-gtk3",
    "gir1.2-handy-1": "libhandy",
    "gir1.2-vte-2.91": "vte3",
    "gir1.2-wnck-3.0": "libwnck3",
    "gir1.2-keybinder-3.0": "keybinder3",
    # WebKit / GTK / GNOME / Wayland stack
    "libwebkit2gtk-4.1-0": "webkit2gtk-4.1",
    "libwebkit2gtk-4.0-37": "webkit2gtk",
    "libwebkitgtk-6.0-4": "webkitgtk-6.0",
    "libjavascriptcoregtk-4.1-0": "webkit2gtk-4.1",
    "libjavascriptcoregtk-4.0-18": "webkit2gtk",
    "libjavascriptcoregtk-6.0-1": "webkitgtk-6.0",
    "libgtk-3-0": "gtk3",
    "libgtk-3-0t64": "gtk3",
    "libgtk-3-common": "gtk3",
    "libgtk-4-1": "gtk4",
    "libgtk-4-common": "gtk4",
    "libglib2.0-0": "glib2",
    "libglib2.0-0t64": "glib2",
    "libglib2.0-bin": "glib2",
    "libgdk-pixbuf-2.0-0": "gdk-pixbuf2",
    "libgdk-pixbuf2.0-0": "gdk-pixbuf2",
    "libpango-1.0-0": "pango",
    "libpangocairo-1.0-0": "pango",
    "libpangoft2-1.0-0": "pango",
    "libpango1.0-0": "pango",
    "libpangomm-1.4-1v5": "pangomm",
    "libcairo2": "cairo",
    "libcairo-gobject2": "cairo",
    "libatk1.0-0": "at-spi2-core",
    "libatk1.0-0t64": "at-spi2-core",
    "libatk-bridge2.0-0": "at-spi2-core",
    "libatk-bridge2.0-0t64": "at-spi2-core",
    "libatspi2.0-0": "at-spi2-core",
    "libatspi2.0-0t64": "at-spi2-core",
    "libsoup-3.0-0": "libsoup3",
    "libsoup2.4-1": "libsoup",
    "libadwaita-1-0": "libadwaita",
    "libhandy-1-0": "libhandy",
    "libnotify4": "libnotify",
    "libsecret-1-0": "libsecret",
    "dconf-gsettings-backend": "dconf",
    "gsettings-desktop-schemas": "gsettings-desktop-schemas",
    # GStreamer stack
    "libgstreamer1.0-0": "gstreamer",
    "gstreamer1.0-plugins-base": "gst-plugins-base",
    "libgstreamer-plugins-base1.0-0": "gst-plugins-base-libs",
    "gstreamer1.0-plugins-good": "gst-plugins-good",
    "libgstreamer-plugins-good1.0-0": "gst-plugins-good",
    "gstreamer1.0-plugins-bad": "gst-plugins-bad",
    "libgstreamer-plugins-bad1.0-0": "gst-plugins-bad-libs",
    "gstreamer1.0-plugins-ugly": "gst-plugins-ugly",
    "gstreamer1.0-libav": "gst-libav",
    "gstreamer1.0-gl": "gst-plugins-base-libs",
    "gstreamer1.0-gtk3": "gst-plugin-gtk",
    "gstreamer1.0-pipewire": "gst-plugin-pipewire",
    "gstreamer1.0-pulseaudio": "gst-plugins-good",
    "gstreamer1.0-alsa": "gst-plugins-base",
    # Core C/C++ / OpenMP / Crypto / System libraries
    "libc6": "glibc",
    "libgcc-s1": "gcc-libs",
    "libgcc1": "gcc-libs",
    "libstdc++6": "gcc-libs",
    "libgomp1": "gcc-libs",
    "libatomic1": "gcc-libs",
    "zlib1g": "zlib",
    "libzstd1": "zstd",
    "liblzma5": "xz",
    "libbz2-1.0": "bzip2",
    "libssl3": "openssl",
    "libssl3t64": "openssl",
    "libssl1.1": "openssl-1.1",
    "libcurl4": "curl",
    "libcurl4t64": "curl",
    "libcurl3-gnutls": "libcurl-gnutls",
    "libsqlite3-0": "sqlite",
    "libxml2": "libxml2",
    "libxslt1.1": "libxslt",
    "libffi8": "libffi",
    "libffi7": "libffi",
    "libpcre2-8-0": "pcre2",
    "libpcre3": "pcre",
    "libicu74": "icu",
    "libicu76": "icu",
    "libdbus-1-3": "dbus",
    "libsystemd0": "systemd-libs",
    "libudev1": "systemd-libs",
    "lib seccomp2": "libseccomp",
    "libseccomp2": "libseccomp",
    "libcap2": "libcap",
    "libgcrypt20": "libgcrypt",
    "libgpg-error0": "libgpg-error",
    "libgnutls30": "gnutls",
    "libgnutls30t64": "gnutls",
    "libnettle8": "nettle",
    "libnettle8t64": "nettle",
    "libhogweed6": "nettle",
    "libhogweed6t64": "nettle",
    "libidn2-0": "libidn2",
    "libunistring5": "libunistring",
    "libtasn1-6": "libtasn1",
    "libp11-kit0": "libp11-kit",
    "libbrotli1": "brotli",
    "libnghttp2-14": "libnghttp2",
    "libpsl5": "libpsl",
    "libpsl5t64": "libpsl",
    "libssh2-1": "libssh2",
    "libssh2-1t64": "libssh2",
    "libarchive13": "libarchive",
    "libarchive13t64": "libarchive",
    # Graphics / Vulkan / Wayland / X11 / Audio
    "libvulkan1": "vulkan-icd-loader",
    "mesa-vulkan-drivers": "vulkan-mesa-layers",
    "libgl1": "libglvnd",
    "libglx0": "libglvnd",
    "libegl1": "libglvnd",
    "libgles2": "libglvnd",
    "libopengl0": "libglvnd",
    "libglu1-mesa": "glu",
    "libglu1": "glu",
    "libgbm1": "mesa",
    "libdrm2": "libdrm",
    "libwayland-client0": "wayland",
    "libwayland-cursor0": "wayland",
    "libwayland-egl1": "wayland",
    "libwayland-server0": "wayland",
    "libxkbcommon0": "libxkbcommon",
    "libxkbcommon-x11-0": "libxkbcommon-x11",
    "libx11-6": "libx11",
    "libx11-xcb1": "libx11",
    "libxcb1": "libxcb",
    "libxext6": "libxext",
    "libxfixes3": "libxfixes",
    "libxi6": "libxi",
    "libxrender1": "libxrender",
    "libxrandr2": "libxrandr",
    "libxcursor1": "libxcursor",
    "libxcomposite1": "libxcomposite",
    "libxdamage1": "libxdamage",
    "libxinerama1": "libxinerama",
    "libxtst6": "libxtst",
    "libxss1": "libxss",
    "libxshmfence1": "libxshmfence",
    "libxxf86vm1": "libxxf86vm",
    "xlibs": "libx11",
    "libasound2": "alsa-lib",
    "libasound2t64": "alsa-lib",
    "libpulse0": "libpulse",
    "libpipewire-0.3-0": "libpipewire",
    "libpipewire-0.3-0t64": "libpipewire",
    "libjack0": "jack2",
    "libjack-jackd2-0": "jack2",
    "libcups2": "libcups",
    "libcups2t64": "libcups",
    "libcupsys2": "libcups",
    # Fonts & Desktop
    "fonts-freefont-ttf": "ttf-freefont",
    "fonts-liberation": "ttf-liberation",
    "fonts-liberation2": "ttf-liberation",
    "fonts-noto-color-emoji": "noto-fonts-emoji",
    "fonts-noto-core": "noto-fonts",
    "fonts-dejavu-core": "ttf-dejavu",
    "ttf-mscorefonts-installer": "ttf-ms-fonts",
    "xdg-utils": "xdg-utils",
    "desktop-file-utils": "desktop-file-utils",
    "shared-mime-info": "shared-mime-info",
    "hicolor-icon-theme": "hicolor-icon-theme",
    "adwaita-icon-theme": "adwaita-icon-theme",
    "ca-certificates": "ca-certificates",
    # Java / Qt
    "default-jre": "java-runtime",
    "default-jdk": "java-environment",
    "qt5-default": "qt5-base",
    "qt6-default": "qt6-base",
    "libqt5core5a": "qt5-base",
    "libqt5gui5": "qt5-base",
    "libqt5widgets5": "qt5-base",
    "libqt5network5": "qt5-base",
    "libqt6core6": "qt6-base",
    "libqt6gui6": "qt6-base",
    "libqt6widgets6": "qt6-base",
    # AppIndicator / NSS
    "libnss3": "nss",
    "libnspr4": "nspr",
    "libayatana-appindicator3-1": "libayatana-appindicator",
    "libappindicator3-1": "libappindicator-gtk3",
}


@dataclass
class CLIOptions:
    update: bool = False
    quiet: bool = False
    Quiet: bool = False
    pseudo: bool = False
    wipeout: bool = False
    pkgbuild: bool = False
    Pkgbuild: bool = False
    output_dir: Path | None = None
    deb_path: Path | None = None


@dataclass
class DebDependencyToken:
    name: str
    operator: str = ""
    version: str = ""


@dataclass
class ControlMetadata:
    pkgname_raw: str = ""
    pkgname: str = ""
    version_raw: str = ""
    epoch: int = 0
    pkgver: str = "1.0.0"
    pkgrel: str = "1"
    pkgdesc: str = ""
    url: str = ""
    arch_deb: str = "amd64"
    arch_alpm: str = "x86_64"
    depends_raw: str = ""
    pre_depends_raw: str = ""
    recommends_raw: str = ""
    suggests_raw: str = ""
    conflicts_raw: str = ""
    breaks_raw: str = ""
    provides_raw: str = ""
    replaces_raw: str = ""
    python_version_raw: str = ""


@dataclass
class PackageBuildContext:
    options: CLIOptions
    control: ControlMetadata
    work_dir: Path
    control_dir: Path
    pkgroot_dir: Path
    data_tar_name: str = "data.tar.zst"
    pkgbuild_ops: list[str] = field(default_factory=list)
    depends: list[str] = field(default_factory=list)
    optdepends: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    replaces: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)
    untranslated_deps: list[str] = field(default_factory=list)
    untranslated_optdeps: list[str] = field(default_factory=list)
    packager: str = "Unknown Packager"
    licenses: list[str] = field(default_factory=lambda: ["custom"])
    installed_size: int = 0


# ---------------------------------------------------------------------------
# Subsystem 1: CLI Argument Parsing & Environment Validation
# ---------------------------------------------------------------------------


def print_help() -> None:
    help_text = (
        "Syntax: debtap -o output_directory [other_options] package_filename\n\n"
        "Options:\n\n"
        "    -h  --help        Print this help message\n"
        "    -u  --update      Update debtap database\n"
        "    -q  --quiet       Bypass all questions, except for editing metadata file(s)\n"
        "    -Q  --Quiet       Bypass all questions (not recommended)\n"
        "    -s  --pseudo      Create a pseudo-64-bit package from a 32-bit .deb package\n"
        "    -w  --wipeout     Wipeout versions from all dependencies, conflicts etc.\n"
        "    -p  --pkgbuild    Additionally generate a PKGBUILD file\n"
        "    -P  --Pkgbuild    Generate a PKGBUILD file only\n"
        "    -o  --output      Output directory for generated package and/or PKGBUILD (optional)\n"
        "    -v  --version     Print version"
    )
    print(help_text)


def parse_cli_args(argv: list[str]) -> CLIOptions:
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("-u", "--update", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("-Q", "--Quiet", action="store_true")
    parser.add_argument("-s", "--pseudo", action="store_true")
    parser.add_argument("-w", "--wipeout", action="store_true")
    parser.add_argument("-p", "--pkgbuild", action="store_true")
    parser.add_argument("-P", "--Pkgbuild", action="store_true")
    parser.add_argument("-o", "--output", type=str, default=None)
    parser.add_argument("-v", "--version", action="store_true")
    parser.add_argument("deb_file", nargs="?", default=None)

    try:
        ns, unknown = parser.parse_known_args(argv)
    except SystemExit:
        log_err("Invalid option or missing argument")
        sys.exit(1)

    if unknown:
        log_err(f"No such file or directory or invalid option: {' '.join(unknown)}")
        sys.exit(1)

    if ns.help:
        print_help()
        sys.exit(0)

    if ns.version:
        print(VERSION)
        sys.exit(0)

    opts = CLIOptions(
        update=ns.update,
        quiet=ns.quiet,
        Quiet=ns.Quiet,
        pseudo=ns.pseudo,
        wipeout=ns.wipeout,
        pkgbuild=ns.pkgbuild,
        Pkgbuild=ns.Pkgbuild,
    )

    if ns.output is not None:
        out_path = Path(ns.output).expanduser().resolve()
        if not out_path.is_dir():
            log_err(f"No output directory or invalid output directory: {ns.output}")
            sys.exit(1)
        opts.output_dir = out_path

    if opts.pseudo:
        multilib_db = Path("/var/lib/pacman/sync/multilib.db")
        multilib_testing_db = Path("/var/lib/pacman/sync/multilib-testing.db")
        if not multilib_db.exists() and not multilib_testing_db.exists():
            log_err(
                "You are either running non-x86_64 Arch Linux, or you haven't enabled multilib repository. "
                "If you are running x86_64 Arch Linux, you must enable a multilib repository and then synchronize pacman database"
            )
            sys.exit(1)

    if opts.update:
        return opts

    if not ns.deb_file:
        log_err("You haven't specified a deb package")
        print_help()
        sys.exit(1)

    deb_candidate = Path(ns.deb_file).expanduser().resolve()
    if not deb_candidate.is_file():
        log_err(f"No such file or directory: {ns.deb_file}")
        sys.exit(1)

    opts.deb_path = deb_candidate
    return opts


# ---------------------------------------------------------------------------
# Subsystem 2: Database & Cache Engine (-u)
# ---------------------------------------------------------------------------


def detect_debian_arch() -> str:
    machine = os.uname().machine
    if machine in ("x86_64", "i686"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    if machine in ("armv7l", "armv7h"):
        return "armhf"
    return "amd64"


def discover_ubuntu_codename(ssl_ctx: ssl.SSLContext) -> str:
    """Queries Ubuntu's official meta-release feed over HTTPS with fallback."""
    meta_url = "https://changelogs.ubuntu.com/meta-release"
    try:
        req = urllib.request.Request(meta_url, headers={"User-Agent": f"debtap-ng/{VERSION}"})
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=15) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        codenames = re.findall(r"^Dist:\s*([a-z]+)\s*$", content, flags=re.MULTILINE)
        if codenames:
            return codenames[-1]
    except Exception:
        pass
    return "noble"


def stream_ingest_contents(
    url: str, source_tag: str, ssl_ctx: ssl.SSLContext
) -> list[tuple[str, str, str]]:
    """Downloads a gzipped Contents index and extracts relevant binary/library/gir basenames."""
    records: list[tuple[str, str, str]] = []
    req = urllib.request.Request(url, headers={"User-Agent": f"debtap-ng/{VERSION}"})
    relevant_re = re.compile(
        r"(?:^|/)(?:s?bin|opt|lib|lib64|girepository-[0-9.]+)/|\.so(?:\.|$)|\.typelib$"
    )

    try:
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=60) as resp:
            with gzip.GzipFile(fileobj=resp) as gz:
                for raw_line in gz:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith("FILE"):
                        continue
                    parts = line.rsplit(None, 1)
                    if len(parts) != 2:
                        continue
                    filepath, pkg_spec = parts
                    if not relevant_re.search(filepath):
                        continue
                    basename = filepath.rsplit("/", 1)[-1]
                    for pkg_entry in pkg_spec.split(","):
                        pkg_name = pkg_entry.rsplit("/", 1)[-1].strip()
                        if pkg_name:
                            records.append((basename, pkg_name, source_tag))
    except Exception as exc:
        log_warn(f"Skipped or failed indexing {url}: {exc}")
    return records


def sync_database() -> None:
    if os.geteuid() != 0:
        log_err("You must run this command with root privileges")
        sys.exit(1)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CACHE_DIR, 0o755)

    if shutil.which("pkgfile"):
        log_info("Synchronizing pkgfile database...")
        res = subprocess.run(["pkgfile", "-u"], check=False)
        if res.returncode != 0:
            log_warn("pkgfile -u returned non-zero status; continuing with debtap SQLite sync...")

    log_info("Synchronizing debtap SQLite database over HTTPS...")
    ssl_ctx = ssl.create_default_context()
    deb_arch = detect_debian_arch()
    ubuntu_codename = discover_ubuntu_codename(ssl_ctx)

    urls: list[tuple[str, str]] = [
        (f"https://deb.debian.org/debian/dists/sid/main/Contents-{deb_arch}.gz", "debian"),
        (f"https://deb.debian.org/debian/dists/sid/contrib/Contents-{deb_arch}.gz", "debian"),
        (f"https://deb.debian.org/debian/dists/sid/non-free/Contents-{deb_arch}.gz", "debian"),
        (f"https://deb.debian.org/debian/dists/sid/non-free-firmware/Contents-{deb_arch}.gz", "debian"),
    ]
    if deb_arch == "amd64":
        urls.append(
            (f"https://archive.ubuntu.com/ubuntu/dists/{ubuntu_codename}/Contents-{deb_arch}.gz", "ubuntu")
        )
    else:
        urls.append(
            (f"https://ports.ubuntu.com/ubuntu-ports/dists/{ubuntu_codename}/Contents-{deb_arch}.gz", "ubuntu")
        )

    tmp_db = CACHE_DIR / "cache.db.tmp"
    if tmp_db.exists():
        tmp_db.unlink()

    conn = sqlite3.connect(tmp_db)
    cur = conn.cursor()
    cur.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        CREATE TABLE pkg_contents (
            pkg_name TEXT NOT NULL,
            basename TEXT NOT NULL,
            distro TEXT NOT NULL
        );
        CREATE TABLE base_packages (
            pkg_name TEXT PRIMARY KEY
        );
        CREATE TABLE aur_packages (
            pkg_name TEXT PRIMARY KEY
        );
        """
    )

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(stream_ingest_contents, url, tag, ssl_ctx): url for url, tag in urls
        }
        for fut in as_completed(futures):
            rows = fut.result()
            if rows:
                cur.executemany(
                    "INSERT INTO pkg_contents (basename, pkg_name, distro) VALUES (?, ?, ?)",
                    rows,
                )
                conn.commit()

    log_info("Downloading latest AUR packages list...")
    try:
        req = urllib.request.Request(
            "https://aur.archlinux.org/packages.gz",
            headers={"User-Agent": f"debtap-ng/{VERSION}"},
        )
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=30) as resp:
            with gzip.GzipFile(fileobj=resp) as gz:
                aur_rows = [
                    (line.decode("utf-8", errors="replace").strip(),)
                    for line in gz
                    if line.strip() and not line.startswith(b"#")
                ]
        cur.executemany("INSERT OR IGNORE INTO aur_packages (pkg_name) VALUES (?)", aur_rows)
        conn.commit()
    except Exception as exc:
        log_warn(f"Could not download AUR package list: {exc}")

    log_info("Generating extended base group packages list...")
    base_pkgs = compute_extended_base_packages()
    cur.executemany(
        "INSERT OR IGNORE INTO base_packages (pkg_name) VALUES (?)",
        [(p,) for p in sorted(base_pkgs)],
    )
    cur.executescript(
        """
        CREATE INDEX idx_pkg_contents_pkg ON pkg_contents(pkg_name, distro);
        CREATE INDEX idx_pkg_contents_base ON pkg_contents(basename);
        """
    )
    conn.commit()
    conn.close()

    tmp_db.replace(CACHE_DB)
    os.chmod(CACHE_DB, 0o644)

    # Also write legacy marker files so legacy scripts or checks recognize the sync
    (CACHE_DIR / "extended-base-packages-list").write_text(
        "\n".join(sorted(base_pkgs)) + "\n", encoding="utf-8"
    )
    log_info("All steps successfully completed!")


def compute_extended_base_packages() -> set[str]:
    base_set: set[str] = {"base", "glibc", "gcc-libs", "filesystem", "bash", "coreutils"}
    try:
        res = subprocess.run(
            ["pacman", "-Qi", "base"],
            capture_output=True,
            text=True,
            check=False,
        )
        direct_deps: list[str] = []
        for line in res.stdout.splitlines():
            if line.startswith("Depends On"):
                rhs = line.split(":", 1)[1].strip()
                if rhs and rhs != "None":
                    direct_deps.extend(rhs.split())
        if shutil.which("pactree"):
            for dep in direct_deps:
                pt = subprocess.run(
                    ["pactree", "-lu", dep],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                for p in pt.stdout.splitlines():
                    p_clean = p.strip()
                    if p_clean:
                        base_set.add(p_clean)
    except Exception:
        pass
    return base_set


# ---------------------------------------------------------------------------
# Subsystem 3: Debian Binary Container & Stream Decoding
# ---------------------------------------------------------------------------


def parse_ar_archive(deb_path: Path) -> Iterator[tuple[str, bytes]]:
    """Pure Python Unix `ar` archive reader with BSD/GNU extended name handling."""
    with deb_path.open("rb") as f:
        magic = f.read(8)
        if magic != b"!<arch>\n":
            raise ValueError("Not a valid Debian binary package (invalid ar magic header)")

        gnu_strtab: bytes = b""
        while True:
            header = f.read(60)
            if not header:
                break
            if len(header) < 60 or header[58:60] != b"`\n":
                raise ValueError("Corrupted ar member header in .deb archive")

            raw_name = header[0:16].decode("ascii", errors="replace").strip()
            size = int(header[48:58].decode("ascii").strip())

            if raw_name == "//":
                gnu_strtab = f.read(size)
                if size % 2 == 1:
                    f.read(1)
                continue

            if raw_name.startswith("#1/"):
                ext_len = int(raw_name[3:])
                name = f.read(ext_len).decode("utf-8", errors="replace").rstrip("\x00")
                payload = f.read(size - ext_len)
            elif raw_name.startswith("/") and raw_name[1:].isdigit() and gnu_strtab:
                offset = int(raw_name[1:])
                end_idx = gnu_strtab.find(b"/\n", offset)
                if end_idx == -1:
                    end_idx = gnu_strtab.find(b"\n", offset)
                name = gnu_strtab[offset:end_idx].decode("utf-8", errors="replace")
                payload = f.read(size)
            else:
                name = raw_name.rstrip("/")
                payload = f.read(size)

            if size % 2 == 1:
                f.read(1)

            yield name, payload


def decompress_tar_bytes(member_name: str, raw_bytes: bytes) -> bytes:
    """Decompresses .tar.{zst,xz,lzma,gz,bz2} member payloads into raw tar bytes."""
    if member_name.endswith(".tar"):
        return raw_bytes
    if member_name.endswith(".tar.gz") or member_name.endswith(".tgz"):
        return gzip.decompress(raw_bytes)
    if member_name.endswith(".tar.xz") or member_name.endswith(".tar.lzma"):
        return lzma.decompress(raw_bytes)
    if member_name.endswith(".tar.bz2"):
        import bz2

        return bz2.decompress(raw_bytes)
    if member_name.endswith(".tar.zst"):
        if stdlib_zstd is not None:
            return stdlib_zstd.decompress(raw_bytes)
        if not shutil.which("zstd"):
            raise RuntimeError(f"Extracting {member_name} requires zstd")
        proc = subprocess.run(
            ["zstd", "-dc"],
            input=raw_bytes,
            capture_output=True,
            check=True,
        )
        return proc.stdout
    raise ValueError(f"Unsupported archive compression format: {member_name}")


def safe_deb_tar_filter(member: tarfile.TarInfo, dest_path: str) -> tarfile.TarInfo | None:
    """PEP 706-hardened filter tailored for OS package root trees."""
    clean_name = member.name.lstrip("./").lstrip("/")
    if not clean_name:
        return None

    norm_rel = os.path.normpath(clean_name)
    if norm_rel.startswith("..") or "/../" in f"/{norm_rel}/":
        raise ValueError(f"Unsafe path traversal detected in archive member: {member.name}")

    if member.ischr() or member.isblk() or member.isfifo():
        return None

    member.name = norm_rel
    member.uid = 0
    member.gid = 0
    member.uname = "root"
    member.gname = "root"

    if member.issym():
        link_target = member.linkname
        if not link_target.startswith("/"):
            resolved = os.path.normpath(os.path.join(os.path.dirname(norm_rel), link_target))
            if resolved.startswith(".."):
                raise ValueError(f"Symlink escapes package root: {member.name} -> {link_target}")
    elif member.islnk():
        link_target = member.linkname.lstrip("./").lstrip("/")
        norm_target = os.path.normpath(link_target)
        if norm_target.startswith(".."):
            raise ValueError(f"Hardlink escapes package root: {member.name} -> {member.linkname}")
        member.linkname = norm_target

    return member


def extract_deb_archive(
    deb_path: Path, control_dir: Path, pkgroot_dir: Path
) -> str:
    """Validates and extracts control.tar.* and data.tar.* into isolated directories."""
    has_debian_binary = False
    has_control = False
    data_tar_name = ""

    for member_name, payload in parse_ar_archive(deb_path):
        if member_name == "debian-binary":
            ver_str = payload.decode("ascii", errors="replace").strip()
            if not ver_str.startswith("2."):
                raise ValueError(f"Unsupported debian-binary version: {ver_str}")
            has_debian_binary = True
        elif member_name.startswith("control.tar"):
            tar_bytes = decompress_tar_bytes(member_name, payload)
            with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
                tf.extractall(path=control_dir, filter=safe_deb_tar_filter)
            has_control = True
        elif member_name.startswith("data.tar"):
            data_tar_name = member_name
            tar_bytes = decompress_tar_bytes(member_name, payload)
            with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
                tf.extractall(path=pkgroot_dir, filter=safe_deb_tar_filter)

    if not has_debian_binary or not has_control or not data_tar_name:
        raise ValueError("You haven't specified a valid deb package")

    return data_tar_name


# ---------------------------------------------------------------------------
# Subsystem 4: Filesystem Hierarchy Normalization & Relocation Algebra
# ---------------------------------------------------------------------------


def merge_tree(src: Path, dst: Path, symlink_Moves: dict[str, str]) -> None:
    """Recursively merges `src` into `dst`, recording old->new root-relative paths."""
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in list(src.iterdir()):
        target = dst / item.name
        if item.is_dir() and not item.is_symlink():
            if target.exists() and target.is_dir() and not target.is_symlink():
                merge_tree(item, target, symlink_Moves)
                item.rmdir()
            else:
                if target.exists() or target.is_symlink():
                    target.unlink()
                shutil.move(str(item), str(target))
        else:
            if target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(item), str(target))
    with contextlib.suppress(OSError):
        src.rmdir()


def normalize_filesystem(ctx: PackageBuildContext) -> None:
    """Transforms Debian/Ubuntu hierarchy into Arch Linux /usr-merged layout."""
    root = ctx.pkgroot_dir
    ops = ctx.pkgbuild_ops
    is_i386 = ctx.control.arch_deb == "i386"
    pseudo = ctx.options.pseudo

    # 1. Relocate systemd units & udev rules under /lib -> /usr/lib
    for sub in ("systemd", "udev", "sysusers.d", "tmpfiles.d", "modprobe.d", "modules-load.d"):
        legacy_sub = root / "lib" / sub
        if legacy_sub.exists():
            merge_tree(legacy_sub, root / "usr" / "lib" / sub, {})
            ops.append(
                f"mkdir -p usr/lib/{sub}; cp -a lib/{sub}/. usr/lib/{sub}/; rm -rf lib/{sub}"
            )

    # 2. Flatten Multi-Arch library triplets (/lib/<triplet>, /usr/lib/<triplet>)
    triplets = (
        "x86_64-linux-gnu",
        "i386-linux-gnu",
        "i686-linux-gnu",
        "aarch64-linux-gnu",
        "arm-linux-gnueabihf",
    )
    target_lib_rel = "usr/lib32" if pseudo else "usr/lib"
    for base_prefix in ("lib", "usr/lib", "usr/local/lib"):
        for triplet in triplets:
            t_dir = root / base_prefix / triplet
            if t_dir.exists() and t_dir.is_dir():
                merge_tree(t_dir, root / target_lib_rel, {})
                ops.append(
                    f"mkdir -p {target_lib_rel}; cp -a {base_prefix}/{triplet}/. {target_lib_rel}/; "
                    f"rm -rf {base_prefix}/{triplet}"
                )

    # 3. Consolidate binary directories into usr/bin
    legacy_bin_dirs = (
        "usr/games",
        "bin",
        "sbin",
        "usr/sbin",
        "usr/local/games",
        "usr/local/bin",
        "usr/local/sbin",
    )
    for bdir in legacy_bin_dirs:
        src_bdir = root / bdir
        if src_bdir.exists():
            merge_tree(src_bdir, root / "usr" / "bin", {})
            ops.append(f"mkdir -p usr/bin; cp -a {bdir}/. usr/bin/; rm -rf {bdir}")

    # 4. Consolidate library directories (/lib, /lib64, /usr/lib64, /lib32)
    if not pseudo:
        for ldir in ("lib", "lib64", "usr/lib64"):
            src_ldir = root / ldir
            if src_ldir.exists():
                merge_tree(src_ldir, root / "usr" / "lib", {})
                ops.append(f"mkdir -p usr/lib; cp -a {ldir}/. usr/lib/; rm -rf {ldir}")
        if (root / "usr/local/lib64").exists():
            merge_tree(root / "usr/local/lib64", root / "usr/local/lib", {})
            ops.append("mkdir -p usr/local/lib; cp -a usr/local/lib64/. usr/local/lib/; rm -rf usr/local/lib64")
        if (root / "lib32").exists():
            dest_32 = "usr/lib" if is_i386 else "usr/lib32"
            merge_tree(root / "lib32", root / dest_32, {})
            ops.append(f"mkdir -p {dest_32}; cp -a lib32/. {dest_32}/; rm -rf lib32")
        if is_i386 and (root / "usr/lib32").exists():
            merge_tree(root / "usr/lib32", root / "usr/lib", {})
            ops.append("mkdir -p usr/lib; cp -a usr/lib32/. usr/lib/; rm -rf usr/lib32")
    else:
        for ldir in ("lib", "lib32", "usr/lib"):
            src_ldir = root / ldir
            if src_ldir.exists():
                merge_tree(src_ldir, root / "usr" / "lib32", {})
                ops.append(f"mkdir -p usr/lib32; cp -a {ldir}/. usr/lib32/; rm -rf {ldir}")
        if (root / "usr/local/lib").exists():
            merge_tree(root / "usr/local/lib", root / "usr/local/lib32", {})
            ops.append("mkdir -p usr/local/lib32; cp -a usr/local/lib/. usr/local/lib32/; rm -rf usr/local/lib")

    # 5. Rewrite .desktop launchers, D-Bus service files, and systemd units
    rewrite_legacy_paths_in_configs(root, ops)

    # 6. Repair broken symlinks pointing to relocated legacy paths
    repair_symlinks(root, triplets, pseudo)

    # 7. Relocate license / copyright files into /usr/share/licenses/<pkgname>/
    relocate_licenses(ctx)

    # 8. Prune empty /usr/local or /lib directories if emptied
    for empty_candidate in ("usr/local/lib", "usr/local", "lib", "lib64"):
        cand_path = root / empty_candidate
        if cand_path.exists() and cand_path.is_dir() and not any(cand_path.iterdir()):
            cand_path.rmdir()
            ops.append(f"rmdir --ignore-fail-on-non-empty {empty_candidate} 2>/dev/null || true")


def rewrite_legacy_paths_in_configs(root: Path, ops: list[str]) -> None:
    path_replacements = [
        ("/usr/local/sbin/", "/usr/bin/"),
        ("/usr/local/bin/", "/usr/bin/"),
        ("/usr/local/games/", "/usr/bin/"),
        ("/usr/games/", "/usr/bin/"),
        ("/usr/sbin/", "/usr/bin/"),
        ("/sbin/", "/usr/bin/"),
        ("/bin/", "/usr/bin/"),
        ("/lib/systemd/", "/usr/lib/systemd/"),
        ("/lib/udev/", "/usr/lib/udev/"),
    ]
    target_globs = [
        "usr/share/applications/*.desktop",
        "usr/share/dbus-1/services/*.service",
        "usr/share/dbus-1/system-services/*.service",
        "usr/lib/systemd/system/*.service",
        "usr/lib/systemd/user/*.service",
    ]
    modified_any = False
    for pattern in target_globs:
        for cfg_file in root.glob(pattern):
            if not cfg_file.is_file() or cfg_file.is_symlink():
                continue
            try:
                text = cfg_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            new_text = text
            for old_p, new_p in path_replacements:
                new_text = re.sub(
                    rf"(^|\s|=|\"|'){re.escape(old_p)}",
                    rf"\1{new_p}",
                    new_text,
                    flags=re.MULTILINE,
                )
            if new_text != text:
                cfg_file.write_text(new_text, encoding="utf-8")
                modified_any = True

    if modified_any:
        ops.append(
            "find usr/share/applications usr/lib/systemd -type f \\(-name '*.desktop' -o -name '*.service'\\) "
            "-exec sed -i -E 's#(^|[[:space:]=\"\\x27])/(usr/games|usr/local/games|usr/local/s?bin|usr/sbin|s?bin)/#\\1/usr/bin/#g' {} + 2>/dev/null || true"
        )


def repair_symlinks(root: Path, triplets: tuple[str, ...], pseudo: bool) -> None:
    target_lib = "/usr/lib32/" if pseudo else "/usr/lib/"
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        raw_target = os.readlink(path)
        new_target = raw_target
        if new_target.startswith(("/bin/", "/sbin/", "/usr/sbin/", "/usr/games/")):
            new_target = "/usr/bin/" + new_target.split("/", 3)[-1]
        elif new_target.startswith(("/lib/", "/lib64/", "/usr/lib64/")):
            suffix = new_target.split("/", 2)[-1] if new_target.startswith("/lib") else new_target.split("/", 3)[-1]
            new_target = target_lib + suffix
        for triplet in triplets:
            if f"/{triplet}/" in new_target:
                new_target = new_target.replace(f"/{triplet}/", "/")
                if new_target.startswith("/lib/"):
                    new_target = target_lib + new_target.removeprefix("/lib/")
        if new_target != raw_target:
            path.unlink()
            path.symlink_to(new_target)


def relocate_licenses(ctx: PackageBuildContext) -> None:
    root = ctx.pkgroot_dir
    pkgname = ctx.control.pkgname
    lic_Files: list[tuple[Path, str]] = []

    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        lower_name = p.name.lower()
        if "license" in lower_name:
            lic_Files.append((p, "LICENSE"))
        elif "copyright" in lower_name:
            lic_Files.append((p, "COPYRIGHT"))
        elif "eula" in lower_name:
            lic_Files.append((p, "EULA"))

    seen_types: set[str] = set()
    for src_path, kind in lic_Files:
        if kind in seen_types:
            continue
        seen_types.add(kind)
        dest_name = "LICENSE" if len(seen_types) == 1 else kind
        rel_src = src_path.relative_to(root)
        dest_dir = root / "usr" / "share" / "licenses" / pkgname
        dest_file = dest_dir / dest_name
        if src_path.resolve() != dest_file.resolve():
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest_file)
            os.chmod(dest_file, 0o644)
            ctx.pkgbuild_ops.append(
                f'install -D -m644 "{rel_src}" "${{pkgdir}}/usr/share/licenses/${{pkgname}}/{dest_name}"'
            )


# ---------------------------------------------------------------------------
# Subsystem 5: RFC-822 Control Parser & Semantic Version Translation
# ---------------------------------------------------------------------------


def sanitize_deb_version(raw_ver: str, keep_pkgrel: bool = True) -> tuple[int, str, str]:
    """
    Decomposes Debian `[epoch:]upstream_version[-debian_revision]` into
    `(epoch, alpm_pkgver, alpm_pkgrel)`.
    """
    ver = raw_ver.strip()
    epoch = 0
    if ":" in ver:
        ep_str, ver = ver.split(":", 1)
        if ep_str.isdigit():
            epoch = int(ep_str)

    # Strip Ubuntu/Debian packaging tags (+ubuntu26.04, ~dfsg, -0ubuntu1, etc.)
    ver = re.sub(
        r"[-+~.]?(?:ubuntu|debian|build|dfsg|nobinonly|ppa|nmu|deb)[0-9A-Za-z.+~-]*",
        "",
        ver,
        flags=re.IGNORECASE,
    )
    ver = re.sub(r"[-._](alpha|beta|pre|rc)", r"\1", ver, flags=re.IGNORECASE)
    if "dev" in ver:
        ver = ver.split("dev", 1)[0]

    # Separate Debian revision if present
    pkgrel = "1"
    if "-" in ver:
        base_v, rev_v = ver.rsplit("-", 1)
        if re.match(r"^[0-9]+(\.[0-9]+)?$", rev_v):
            ver = base_v
        else:
            ver = ver.replace("-", ".")

    # Handle VCS version markers
    for vcs in ("git", "bzr", "darcs", "hg"):
        if vcs in ver:
            after = ver.split(vcs, 1)[1].lstrip(".-+_")
            ver = after.split("-", 1)[0] or "1"
            break

    ver = ver.replace("+~", ".").replace("+", ".").replace("~", ".").replace("-", ".")
    ver = re.sub(r"\.+", ".", ver).strip(".")
    if not ver or not ver[0].isalnum():
        ver = "1.0.0"

    return epoch, ver, pkgrel


def parse_control_file(control_path: Path, pseudo: bool) -> ControlMetadata:
    raw_bytes = control_path.read_bytes()
    msg = email.parser.BytesParser(policy=email.policy.compat32).parsebytes(raw_bytes)

    pkg_raw = (msg.get("Package") or "unknown").strip()
    ver_raw = (msg.get("Version") or "1.0.0").strip()
    arch_deb = (msg.get("Architecture") or "amd64").strip().lower()

    if pseudo and arch_deb != "i386":
        raise ValueError("Invalid package architecture for --pseudo (must be i386)")

    pkgname = pkg_raw.lower()
    for vcs in ("git", "bzr", "darcs", "hg", "svn", "cvs"):
        if vcs in ver_raw.lower() and not pkgname.endswith(f"-{vcs}"):
            pkgname = f"{pkgname}-{vcs}"
            break
    if pkgname.endswith("demo") and not pkgname.endswith("-demo"):
        pkgname = pkgname[:-4] + "-demo"
    elif pkgname.endswith("trial") and not pkgname.endswith("-trial"):
        pkgname = pkgname[:-5] + "-trial"
    pkgname = re.sub(r"-+", "-", pkgname)

    epoch, pkgver, pkgrel = sanitize_deb_version(ver_raw)

    # Description formatting: use synopsis (first line) and clean punctuation
    desc_raw = (msg.get("Description") or "").strip()
    desc_lines = [ln.strip() for ln in desc_raw.splitlines() if ln.strip() and ln.strip() != "."]
    if desc_lines:
        synopsis = desc_lines[0].rstrip(".")
        if len(desc_lines) > 1 and desc_lines[1].lower() != synopsis.lower():
            pkgdesc = f"{synopsis[:1].upper() + synopsis[1:]}."
        else:
            pkgdesc = synopsis[:1].upper() + synopsis[1:]
    else:
        pkgdesc = f"Converted Debian package {pkgname}"

    url = (msg.get("Homepage") or "").strip().rstrip("/")

    arch_map = {
        "i386": "i686",
        "amd64": "x86_64",
        "armhf": "armv7h",
        "arm64": "aarch64",
        "all": "any",
    }
    arch_alpm = "x86_64" if pseudo else arch_map.get(arch_deb, arch_deb)

    return ControlMetadata(
        pkgname_raw=pkg_raw,
        pkgname=pkgname,
        version_raw=ver_raw,
        epoch=epoch,
        pkgver=pkgver,
        pkgrel=pkgrel,
        pkgdesc=pkgdesc,
        url=url,
        arch_deb=arch_deb,
        arch_alpm=arch_alpm,
        depends_raw=msg.get("Depends") or "",
        pre_depends_raw=msg.get("Pre-Depends") or "",
        recommends_raw=msg.get("Recommends") or "",
        suggests_raw=msg.get("Suggests") or "",
        conflicts_raw=msg.get("Conflicts") or "",
        breaks_raw=msg.get("Breaks") or "",
        provides_raw=msg.get("Provides") or "",
        replaces_raw=msg.get("Replaces") or "",
        python_version_raw=(msg.get("Python-Version") or "").strip(),
    )


# ---------------------------------------------------------------------------
# Subsystem 6: Dual-Mode Dependency & Soname Resolution Engine
# ---------------------------------------------------------------------------


class ArchDependencyResolver:
    def __init__(self, pseudo: bool, wipeout: bool, self_pkgname: str) -> None:
        self.pseudo = pseudo
        self.wipeout = wipeout
        self.self_pkgname = self._strip_vcs(self_pkgname)
        self.base_pkgs = self._load_base_packages()
        self.local_arch_pkgs, self.local_provides = self._load_pacman_sync_db()
        self.arch_pkg_versions = self._load_arch_pkg_versions()
        self.db_conn: sqlite3.Connection | None = (
            sqlite3.connect(f"file:{CACHE_DB}?mode=ro", uri=True)
            if CACHE_DB.exists()
            else None
        )
        self.pkgfile_cache: dict[str, str] = {}

    @staticmethod
    def _strip_vcs(name: str) -> str:
        return re.sub(r"-(?:svn|git|cvs|bzr|darcs|hg|bin)$", "", name)

    def _load_base_packages(self) -> set[str]:
        base = {"base", "glibc", "gcc-libs", "filesystem", "bash", "coreutils"}
        legacy_file = CACHE_DIR / "extended-base-packages-list"
        if legacy_file.exists():
            for ln in legacy_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if ln.strip():
                    base.add(ln.strip())
        return base

    def _load_pacman_sync_db(self) -> tuple[set[str], set[str]]:
        pkgs: set[str] = set()
        provs: set[str] = set()
        try:
            res = subprocess.run(
                ["pacman", "-Ssq"],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in res.stdout.splitlines():
                p = line.strip()
                if p:
                    pkgs.add(p)
        except Exception:
            pass
        return pkgs, provs

    def _load_arch_pkg_versions(self) -> dict[str, str]:
        versions: dict[str, str] = {}
        try:
            res = subprocess.run(
                ["pacman", "-Sl"],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in res.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 3:
                    _, pkg, ver = parts[0], parts[1], parts[2]
                    ver_clean = ver.split(":", 1)[-1].split("-", 1)[0]
                    versions[pkg] = ver_clean
        except Exception:
            pass
        return versions

    def close(self) -> None:
        if self.db_conn is not None:
            self.db_conn.close()

    def parse_dependency_clauses(self, raw_field: str) -> list[list[DebDependencyToken]]:
        """
        Parses an RFC-822 dependency string into a list of OR-groups:
        `A (>= 1.0) | B, C (<< 2.0)` -> `[[A, B], [C]]`.
        """
        if not raw_field.strip():
            return []
        groups: list[list[DebDependencyToken]] = []
        token_re = re.compile(
            r"^\s*([A-Za-z0-9.+_-]+)(?::[A-Za-z0-9_-]+)?\s*(?:\(\s*(<<|<=|=|>=|>>|<|>)\s*([^)]+)\s*\))?\s*$"
        )
        op_map = {"<<": "<", ">>": ">", "<=": "<=", ">=": ">=", "=": "=", "<": "<", ">": ">"}

        for clause in raw_field.split(","):
            clause = clause.strip()
            if not clause:
                continue
            alternatives: list[DebDependencyToken] = []
            for alt in clause.split("|"):
                alt = alt.strip()
                m = token_re.match(alt)
                if not m:
                    continue
                deb_name = m.group(1).lower()
                raw_op = m.group(2) or ""
                raw_ver = (m.group(3) or "").strip()
                alpm_op = op_map.get(raw_op, "")
                alpm_ver = ""
                if raw_ver:
                    _, alpm_ver, _ = sanitize_deb_version(raw_ver)
                alternatives.append(
                    DebDependencyToken(name=deb_name, operator=alpm_op, version=alpm_ver)
                )
            if alternatives:
                groups.append(alternatives)
        return groups

    def translate_package_name(self, deb_name: str) -> str | None:
        """Resolves a single Debian package name into its Arch Linux counterpart."""
        if deb_name in IGNORED_DEBIAN_PKGS:
            return None

        # Tier 0: Curated static mapping & pattern rules
        if deb_name in STATIC_PKG_MAP:
            mapped = STATIC_PKG_MAP[deb_name]
            return self._apply_multilib_prefix(mapped)

        # Dynamic pattern rules (OpenJDK, Qt, libstdc++, t64 suffixes)
        m_jdk = re.match(r"^openjdk-([0-9]+)-(jdk|jre)(?:-headless)?$", deb_name)
        if m_jdk:
            return f"{m_jdk.group(2)}{m_jdk.group(1)}-openjdk"

        m_qt = re.match(r"^qt([0-9]+)-default$", deb_name)
        if m_qt:
            return f"qt{m_qt.group(1)}-base"

        if deb_name.startswith("libstdc++"):
            return self._apply_multilib_prefix("gcc-libs")

        # Strip Ubuntu 24.04/26.04 time64 't64' suffix and retry static map
        stripped_t64 = re.sub(r"([0-9])t64$", r"\1", deb_name)
        if stripped_t64 != deb_name and stripped_t64 in STATIC_PKG_MAP:
            return self._apply_multilib_prefix(STATIC_PKG_MAP[stripped_t64])

        # Python module pattern: python3-foo -> python-foo
        if deb_name.startswith("python3-"):
            py_cand = "python-" + deb_name.removeprefix("python3-")
            if py_cand in self.local_arch_pkgs:
                return py_cand

        # Tier 1 & 2: SQLite Contents lookup (if database was synced via -u)
        if self.db_conn is not None:
            for distro in ("debian", "ubuntu"):
                cur = self.db_conn.execute(
                    "SELECT DISTINCT basename FROM pkg_contents WHERE pkg_name = ? AND distro = ?",
                    (deb_name, distro),
                )
                basenames = [r[0] for r in cur.fetchall()]
                if basenames:
                    voted = self._vote_pkgfile(basenames)
                    if voted:
                        return self._apply_multilib_prefix(voted)

        # Tier 3: Heuristic name matching against local pacman sync DB
        cand_no_vcs = self._strip_vcs(stripped_t64)
        cand_no_lib = cand_no_vcs.removeprefix("lib")
        cand_no_trailing_num = re.sub(r"[-.]?[0-9]+(?:\.[0-9]+)*$", "", cand_no_vcs)
        cand_no_lib_num = re.sub(r"[-.]?[0-9]+(?:\.[0-9]+)*$", "", cand_no_lib)

        for candidate in (
            cand_no_vcs,
            cand_no_lib,
            cand_no_trailing_num,
            cand_no_lib_num,
            "lib" + cand_no_lib_num,
        ):
            if candidate and candidate in self.local_arch_pkgs:
                return self._apply_multilib_prefix(candidate)

        return None

    def _apply_multilib_prefix(self, arch_pkg: str) -> str:
        if not self.pseudo:
            return arch_pkg
        if arch_pkg.startswith("lib32-"):
            return arch_pkg
        lib32_cand = f"lib32-{arch_pkg}"
        if lib32_cand in self.local_arch_pkgs:
            return lib32_cand
        return arch_pkg

    def _vote_pkgfile(self, basenames: list[str]) -> str | None:
        counts: dict[str, int] = {}
        official_repos = {"core", "extra", "multilib", "core-testing", "extra-testing", "multilib-testing"}
        for bname in basenames[:25]:
            owner = self.resolve_basename_via_pkgfile(bname, official_repos)
            if owner:
                counts[owner] = counts.get(owner, 0) + 1
        if not counts:
            return None
        return max(counts.items(), key=lambda kv: kv[1])[0]

    def resolve_basename_via_pkgfile(
        self, basename: str, preferred_repos: set[str] | None = None
    ) -> str | None:
        if basename in self.pkgfile_cache:
            return self.pkgfile_cache[basename]

        # Try pkgfile first if available and synced
        if shutil.which("pkgfile"):
            res = subprocess.run(
                ["pkgfile", "--", basename],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                lines = [ln.strip() for ln in res.stdout.splitlines() if "/" in ln]
                if lines:
                    if preferred_repos:
                        for ln in lines:
                            repo, pkg = ln.split("/", 1)
                            if repo in preferred_repos:
                                self.pkgfile_cache[basename] = pkg
                                return pkg
                    pkg = lines[0].split("/", 1)[1]
                    self.pkgfile_cache[basename] = pkg
                    return pkg

        # Fallback to pacman -F if files DB exists
        res_f = subprocess.run(
            ["pacman", "-Fq", "--", basename],
            capture_output=True,
            text=True,
            check=False,
        )
        if res_f.returncode == 0 and res_f.stdout.strip():
            first_line = res_f.stdout.splitlines()[0].strip()
            pkg = first_line.split("/")[-1]
            self.pkgfile_cache[basename] = pkg
            return pkg

        return None

    def inspect_elf_dependencies(self, pkgroot: Path) -> set[str]:
        """
        Mode B: Scans all ELF binaries and shared libraries inside `pkgroot`,
        collects internal DT_SONAME / provided `.so` files, and resolves
        external DT_NEEDED sonames to Arch Linux packages.
        """
        if not shutil.which("readelf"):
            return set()

        internal_sonames: set[str] = set()
        needed_sonames: set[str] = set()
        elf_files: list[Path] = []

        for path in pkgroot.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            if path.name.endswith(".so") or ".so." in path.name:
                internal_sonames.add(path.name)
            try:
                with path.open("rb") as f:
                    if f.read(4) == b"\x7fELF":
                        elf_files.append(path)
            except OSError:
                continue

        needed_re = re.compile(r"\(NEEDED\)\s+Shared library:\s+\[([^\]]+)\]")
        soname_re = re.compile(r"\(SONAME\)\s+Library soname:\s+\[([^\]]+)\]")

        for elf_path in elf_files:
            res = subprocess.run(
                ["readelf", "-d", str(elf_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode != 0:
                continue
            for m in soname_re.finditer(res.stdout):
                internal_sonames.add(m.group(1))
            for m in needed_re.finditer(res.stdout):
                needed_sonames.add(m.group(1))

        external_sonames = needed_sonames - internal_sonames
        resolved_pkgs: set[str] = set()

        # Fast-path static soname mappings for common system libraries
        soname_static_map: dict[str, str] = {
            "libc.so.6": "glibc",
            "libm.so.6": "glibc",
            "libpthread.so.0": "glibc",
            "libdl.so.2": "glibc",
            "librt.so.1": "glibc",
            "ld-linux-x86-64.so.2": "glibc",
            "libstdc++.so.6": "gcc-libs",
            "libgcc_s.so.1": "gcc-libs",
            "libgomp.so.1": "gcc-libs",
            "libglib-2.0.so.0": "glib2",
            "libgobject-2.0.so.0": "glib2",
            "libgio-2.0.so.0": "glib2",
            "libgtk-3.so.0": "gtk3",
            "libgdk-3.so.0": "gtk3",
            "libgtk-4.so.1": "gtk4",
            "libwebkit2gtk-4.1.so.0": "webkit2gtk-4.1",
            "libjavascriptcoregtk-4.1.so.0": "webkit2gtk-4.1",
            "libsoup-3.0.so.0": "libsoup3",
            "libgstreamer-1.0.so.0": "gstreamer",
            "libvulkan.so.1": "vulkan-icd-loader",
            "libX11.so.6": "libx11",
            "libwayland-client.so.0": "wayland",
        }

        for soname in external_sonames:
            if soname in soname_static_map:
                pkg = self._apply_multilib_prefix(soname_static_map[soname])
                resolved_pkgs.add(pkg)
                continue

            # Check local filesystem /usr/lib/<soname> via pacman -Qo
            lib_dir = "/usr/lib32" if self.pseudo else "/usr/lib"
            host_lib = Path(lib_dir) / soname
            if host_lib.exists():
                qo = subprocess.run(
                    ["pacman", "-Qoq", str(host_lib)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if qo.returncode == 0 and qo.stdout.strip():
                    resolved_pkgs.add(qo.stdout.splitlines()[0].strip())
                    continue

            owner = self.resolve_basename_via_pkgfile(soname)
            if owner:
                resolved_pkgs.add(self._apply_multilib_prefix(owner))

        return resolved_pkgs

    def reconcile_constraints(
        self,
        raw_field: str,
        extra_pkgs: Iterable[str] = (),
        prune_base: bool = False,
        strip_versions_force: bool = False,
    ) -> tuple[list[str], list[str]]:
        """
        Translates dependency clauses, reconciles lower/upper version bounds
        against Arch Linux's rolling release versions, and returns
        `(sorted_translated_specs, sorted_untranslated_names)`.
        """
        or_groups = self.parse_dependency_clauses(raw_field)
        pkg_constraints: dict[str, list[tuple[str, str]]] = {}
        untranslated: set[str] = set()

        for group in or_groups:
            translated_token: tuple[str, str, str] | None = None
            for token in group:
                arch_name = self.translate_package_name(token.name)
                if arch_name:
                    translated_token = (arch_name, token.operator, token.version)
                    break
            if translated_token is None:
                first_name = group[0].name
                if first_name not in IGNORED_DEBIAN_PKGS:
                    untranslated.add(first_name)
                continue

            arch_name, op, ver = translated_token
            if self._strip_vcs(arch_name.removeprefix("lib32-")) == self.self_pkgname:
                continue
            if prune_base and self._strip_vcs(arch_name) in self.base_pkgs:
                continue

            pkg_constraints.setdefault(arch_name, [])
            if op and ver and not self.wipeout and not strip_versions_force:
                pkg_constraints[arch_name].append((op, ver))

        for extra in extra_pkgs:
            if not extra:
                continue
            if self._strip_vcs(extra.removeprefix("lib32-")) == self.self_pkgname:
                continue
            if prune_base and self._strip_vcs(extra) in self.base_pkgs:
                continue
            pkg_constraints.setdefault(extra, [])

        final_specs: list[str] = []
        for pkg in sorted(pkg_constraints.keys()):
            bounds = pkg_constraints[pkg]
            if not bounds or self.wipeout or strip_versions_force:
                final_specs.append(pkg)
                continue

            # Separate lower/exact bounds and upper bounds
            lower_bounds = [(op, v) for op, v in bounds if op in (">=", ">", "=")]
            upper_bounds = [(op, v) for op, v in bounds if op in ("<", "<=")]

            # Rolling-release upper-bound reconciliation:
            # Drop restrictive Debian/Ubuntu distribution locks (e.g. python < 3.15)
            # when Arch Linux's repository version is >= the upper lock or when
            # the package name was remapped from a Debian-specific ABI package.
            arch_ver = self.arch_pkg_versions.get(pkg)
            valid_upper: list[tuple[str, str]] = []
            for op, uver in upper_bounds:
                if arch_ver and self._vercmp(arch_ver, uver) >= 0:
                    continue
                if lower_bounds and self._is_minor_lock(lower_bounds[0][1], uver):
                    continue
                valid_upper.append((op, uver))

            chosen_bounds: list[tuple[str, str]] = []
            if lower_bounds:
                # Pick highest lower bound via vercmp
                best_lower = lower_bounds[0]
                for cand in lower_bounds[1:]:
                    if self._vercmp(cand[1], best_lower[1]) > 0:
                        best_lower = cand
                chosen_bounds.append(best_lower)
            if valid_upper:
                best_upper = valid_upper[0]
                for cand in valid_upper[1:]:
                    if self._vercmp(cand[1], best_upper[1]) < 0:
                        best_upper = cand
                chosen_bounds.append(best_upper)

            if not chosen_bounds:
                final_specs.append(pkg)
            else:
                for op, ver in chosen_bounds:
                    final_specs.append(f"{pkg}{op}{ver}")

        return final_specs, sorted(untranslated)

    @staticmethod
    def _is_minor_lock(lower_ver: str, upper_ver: str) -> bool:
        """Detects Debian minor-series pinning like >= 3.14 paired with << 3.15."""
        l_parts = [p for p in lower_ver.split(".") if p.isdigit()]
        u_parts = [p for p in upper_ver.split(".") if p.isdigit()]
        if len(l_parts) >= 2 and len(u_parts) >= 2:
            if l_parts[0] == u_parts[0] and int(u_parts[1]) == int(l_parts[1]) + 1:
                return True
        return False

    @staticmethod
    def _vercmp(v1: str, v2: str) -> int:
        if shutil.which("vercmp"):
            res = subprocess.run(
                ["vercmp", v1, v2],
                capture_output=True,
                text=True,
                check=False,
            )
            with contextlib.suppress(ValueError):
                return int(res.stdout.strip())
        return (v1 > v2) - (v1 < v2)


# ---------------------------------------------------------------------------
# Subsystem 7: Install Script (.INSTALL), .PKGINFO, .MTREE & PKGBUILD Builder
# ---------------------------------------------------------------------------


def translate_maintainer_scripts(control_dir: Path, pseudo: bool, is_i386: bool) -> str:
    """
    Translates Debian maintainer scripts (preinst, postinst, prerm, postrm) into
    an ALPM .INSTALL file, stripping debhelper boilerplate and commands already
    handled by modern Arch Linux ALPM hooks.
    """
    script_map = [
        ("preinst", "pre_install", "pre_upgrade"),
        ("postinst", "post_install", "post_upgrade"),
        ("prerm", "pre_remove", None),
        ("postrm", "post_remove", None),
    ]

    skip_line_re = re.compile(
        r"^(?:#!|set\s+-[eu]|exit\s+0?\s*$|;;\s*$|esac\s*$|"
        r"case\s+.*\$1.*in|install\)|upgrade\)|configure\)|remove\)|purge\)|abort-\w+\)|"
        r"\*\)\s*echo\s+.*unknown\s+argument|"
        r".*(?:dh_installdeb|DEBHELPER|debconf|update-menus|update-desktop-database|"
        r"gtk-update-icon-cache|xdg-icon-resource|glib-compile-schemas|update-mime-database|"
        r"ldconfig|systemctl\s+daemon-reload|apt-get|aptitude|dpkg-maintscript-helper|lintian).*)",
        flags=re.IGNORECASE,
    )

    sections: list[str] = []
    for deb_script_name, hook_name, upgrade_hook in script_map:
        script_path = control_dir / deb_script_name
        if not script_path.is_file():
            continue
        raw_text = script_path.read_text(encoding="utf-8", errors="replace")
        body_lines: list[str] = []
        for raw_ln in raw_text.splitlines():
            stripped = raw_ln.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if skip_line_re.match(stripped):
                continue
            line = raw_ln
            line = re.sub(r"/(?:usr/local/s?bin|usr/sbin|usr/games|s?bin)/", "/usr/bin/", line)
            if pseudo:
                line = re.sub(r"/(?:usr/)?lib/", "/usr/lib32/", line)
            else:
                line = re.sub(r"/lib(?:64)?/", "/usr/lib/", line)
            line = re.sub(r"\bdpkg\s+(?:--listfiles|-L)\b", "pacman -Qql", line)
            body_lines.append(f"\t{line.strip()}")

        if body_lines:
            func_block = f"{hook_name}() {{\n" + "\n".join(body_lines) + "\n}"
            sections.append(func_block)
            if upgrade_hook:
                sections.append(f"{upgrade_hook}() {{\n\t{hook_name}\n}}")

    return "\n\n".join(sections) + ("\n" if sections else "")


def calculate_installed_size(pkgroot: Path) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(pkgroot, followlinks=False):
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            with contextlib.suppress(OSError):
                st = os.lstat(fpath)
                total += st.st_size
        for dname in dirnames:
            dpath = os.path.join(dirpath, dname)
            if os.path.islink(dpath):
                with contextlib.suppress(OSError):
                    total += os.lstat(dpath).st_size
    return max(0, total)


def prompt_packager_and_license(ctx: PackageBuildContext) -> None:
    if ctx.options.quiet or ctx.options.Quiet or not sys.stdin.isatty():
        ctx.packager = "Unknown Packager"
        ctx.licenses = ["custom"]
        return

    default_packager = os.environ.get("PACKAGER", "")
    if not default_packager:
        makepkg_conf = Path("/etc/makepkg.conf")
        if makepkg_conf.is_file():
            m = re.search(
                r'^\s*PACKAGER=["\']?([^"\'\n]+)["\']?',
                makepkg_conf.read_text(encoding="utf-8", errors="replace"),
                flags=re.MULTILINE,
            )
            if m:
                default_packager = m.group(1).strip()

    print(f"\n{C_BLUE}::{C_RESET} Enter Packager name (can be left blank) [{default_packager}]:")
    try:
        user_pkg = input().strip()
    except EOFError:
        user_pkg = ""
    ctx.packager = user_pkg or default_packager or "Unknown Packager"

    print(
        f"\n{C_BLUE}::{C_RESET} Enter package license (can be left blank, you can enter multiple licenses comma separated):"
    )
    try:
        user_lic = input().strip()
    except EOFError:
        user_lic = ""
    if user_lic:
        ctx.licenses = [lic.strip() for lic in user_lic.split(",") if lic.strip()]
    else:
        ctx.licenses = ["custom"]


def write_pkginfo(ctx: PackageBuildContext) -> Path:
    pkginfo_path = ctx.pkgroot_dir / ".PKGINFO"
    c = ctx.control
    full_ver = f"{c.epoch}:{c.pkgver}-{c.pkgrel}" if c.epoch > 0 else f"{c.pkgver}-{c.pkgrel}"
    builddate = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    lines: list[str] = [
        f"# Generated by debtap-ng {VERSION}",
        f"pkgname = {c.pkgname}",
        f"pkgbase = {c.pkgname}",
        f"pkgver = {full_ver}",
        f"pkgdesc = {c.pkgdesc}",
        f"url = {c.url}",
        f"builddate = {builddate}",
        f"packager = {ctx.packager}",
        f"size = {ctx.installed_size}",
        f"arch = {c.arch_alpm}",
    ]
    for lic in ctx.licenses:
        lines.append(f"license = {lic}")
    for rep in ctx.replaces:
        lines.append(f"replaces = {rep}")
    for conf in ctx.conflicts:
        lines.append(f"conflict = {conf}")
    for prov in ctx.provides:
        lines.append(f"provides = {prov}")
    for bkp in ctx.backups:
        lines.append(f"backup = {bkp}")
    for dep in ctx.depends:
        lines.append(f"depend = {dep}")
    for odep in ctx.optdepends:
        lines.append(f"optdepend = {odep}")

    pkginfo_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pkginfo_path


def prompt_interactive_edit(pkginfo_path: Path, install_path: Path | None, quiet_all: bool) -> None:
    if quiet_all or not sys.stdin.isatty():
        return
    files_to_edit = [pkginfo_path]
    if install_path and install_path.exists():
        files_to_edit.append(install_path)
        prompt_msg = (
            f"\n{C_BLUE}::{C_RESET} {C_BOLD}If you want to edit .PKGINFO and .INSTALL files (in this order), "
            f"press (1) For{C_RESET} {C_RED}vi{C_RESET} {C_BOLD}(2) For{C_RESET} {C_RED}nano{C_RESET} "
            f"{C_BOLD}(3) For{C_RESET} {C_RED}default editor{C_RESET} {C_BOLD}(4) For{C_RESET} "
            f"{C_RED}a custom editor{C_RESET} {C_BOLD}or any other key to continue: {C_RESET}"
        )
    else:
        prompt_msg = (
            f"\n{C_BLUE}::{C_RESET} {C_BOLD}If you want to edit .PKGINFO file, "
            f"press (1) For{C_RESET} {C_RED}vi{C_RESET} {C_BOLD}(2) For{C_RESET} {C_RED}nano{C_RESET} "
            f"{C_BOLD}(3) For{C_RESET} {C_RED}default editor{C_RESET} {C_BOLD}(4) For{C_RESET} "
            f"{C_RED}a custom editor{C_RESET} {C_BOLD}or any other key to continue: {C_RESET}"
        )

    print(prompt_msg, end="", flush=True)
    try:
        choice = sys.stdin.read(1).strip()
    except EOFError:
        choice = ""
    print()

    editor_cmd: str | None = None
    if choice == "1":
        editor_cmd = "vi"
    elif choice == "2":
        editor_cmd = "nano"
    elif choice == "3":
        editor_cmd = os.environ.get("EDITOR")
    elif choice == "4":
        print(f"{C_BLUE}::{C_RESET} {C_BOLD}Enter command for running custom editor: {C_RESET}", end="", flush=True)
        editor_cmd = input().strip()

    if editor_cmd:
        for fpath in files_to_edit:
            subprocess.run([editor_cmd, str(fpath)], check=False)


def build_binary_package(ctx: PackageBuildContext, dest_dir: Path) -> Path:
    """Generates .MTREE and packs the final .pkg.tar.zst inside fakeroot."""
    pkgroot = ctx.pkgroot_dir
    c = ctx.control
    full_ver = f"{c.epoch}:{c.pkgver}-{c.pkgrel}" if c.epoch > 0 else f"{c.pkgver}-{c.pkgrel}"
    pkg_filename = f"{c.pkgname}-{full_ver}-{c.arch_alpm}.pkg.tar.zst"
    out_pkg_path = dest_dir / pkg_filename

    entries = [".PKGINFO"]
    if (pkgroot / ".INSTALL").exists():
        entries.append(".INSTALL")

    payload_items = sorted(
        p.name for p in pkgroot.iterdir() if p.name not in (".PKGINFO", ".INSTALL", ".MTREE")
    )
    all_mtree_inputs = entries + payload_items

    log_info("Generating .MTREE file...")
    mtree_cmd = [
        "bsdtar",
        "-czf",
        ".MTREE",
        "--format=mtree",
        "--options=!all,use-set,type,uid,gid,mode,time,size,md5,sha256,link",
        *all_mtree_inputs,
    ]
    res_mtree = subprocess.run(mtree_cmd, cwd=pkgroot, capture_output=True, text=True, check=False)
    if res_mtree.returncode != 0:
        raise RuntimeError(f"bsdtar .MTREE generation failed: {res_mtree.stderr}")

    log_info("Creating final package...")
    archive_inputs = [".PKGINFO"]
    if (pkgroot / ".INSTALL").exists():
        archive_inputs.append(".INSTALL")
    archive_inputs.append(".MTREE")
    archive_inputs.extend(payload_items)

    tmp_tar = ctx.work_dir / "package.pkg.tar"
    tar_cmd = ["tar", "--force-local", "-pcf", str(tmp_tar), *archive_inputs]
    if shutil.which("fakeroot"):
        tar_cmd = ["fakeroot", "--", *tar_cmd]

    res_tar = subprocess.run(tar_cmd, cwd=pkgroot, capture_output=True, text=True, check=False)
    if res_tar.returncode != 0:
        raise RuntimeError(f"tar package creation failed: {res_tar.stderr}")

    zstd_cmd = ["zstd", "-q", "-f", "-T0", "--ultra", "-15", str(tmp_tar), "-o", str(out_pkg_path)]
    res_zst = subprocess.run(zstd_cmd, capture_output=True, text=True, check=False)
    if res_zst.returncode != 0:
        raise RuntimeError(f"zstd compression failed: {res_zst.stderr}")

    os.chmod(out_pkg_path, 0o644)
    log_info("Package successfully created!")
    return out_pkg_path


def generate_pkgbuild_bundle(ctx: PackageBuildContext, dest_dir: Path) -> Path:
    """Generates a modern, makepkg-compliant PKGBUILD directory."""
    log_info("Generating PKGBUILD file...")
    c = ctx.control
    deb_path = ctx.options.deb_path
    assert deb_path is not None

    has_bins = (ctx.pkgroot_dir / "usr" / "bin").exists() and any(
        (ctx.pkgroot_dir / "usr" / "bin").iterdir()
    )
    if not c.pkgname.startswith("lib32-") and not c.pkgname.endswith("-bin") and has_bins:
        pkgbuild_pkgname = f"{c.pkgname}-bin"
    else:
        pkgbuild_pkgname = c.pkgname

    sha512 = hashlib.sha512(deb_path.read_bytes()).hexdigest()

    def fmt_array(name: str, items: list[str]) -> str:
        if not items:
            return ""
        quoted = " ".join(f"'{it}'" for it in items)
        return f"{name}=({quoted})"

    lines: list[str] = [
        f"# Generated by debtap-ng {VERSION}",
        f"# Maintainer: {ctx.packager if not (ctx.options.quiet or ctx.options.Quiet) else ''}",
        f"pkgname={pkgbuild_pkgname}",
        f"pkgver={c.pkgver}",
        f"pkgrel={c.pkgrel}",
    ]
    if c.epoch > 0:
        lines.append(f"epoch={c.epoch}")
    escaped_desc = c.pkgdesc.replace('"', '\\"')
    lines.append(f'pkgdesc="{escaped_desc}"')
    lines.append(f"arch=('{c.arch_alpm}')")
    lines.append(f'url="{c.url}"')
    lines.append(fmt_array("license", ctx.licenses or ["custom"]))

    for arr_name, items in (
        ("depends", ctx.depends),
        ("optdepends", ctx.optdepends),
        ("provides", [re.split(r"[<>=]", p)[0] for p in ctx.provides]),
        ("conflicts", ctx.conflicts),
        ("replaces", ctx.replaces),
        ("backup", ctx.backups),
    ):
        line = fmt_array(arr_name, items)
        if line:
            lines.append(line)

    lines.append("options=('!strip' '!emptydirs')")
    install_file = ctx.pkgroot_dir / ".INSTALL"
    if install_file.exists():
        lines.append("install=${pkgname}.install")

    lines.append(f'source=("{deb_path.name}")')
    lines.append(f"sha512sums=('{sha512}')")
    lines.append("")
    lines.append("package() {")
    lines.append(f'    bsdtar -xf "{ctx.data_tar_name}" -C "${{pkgdir}}"')
    if ctx.pkgbuild_ops:
        lines.append('    cd "${pkgdir}"')
        for op in ctx.pkgbuild_ops:
            lines.append(f"    {op}")
    lines.append("}")

    bundle_dir = dest_dir / f"{pkgbuild_pkgname}-PKGBUILD"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    (bundle_dir / "PKGBUILD").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if install_file.exists():
        shutil.copy2(install_file, bundle_dir / f"{pkgbuild_pkgname}.install")
        log_info(
            f'PKGBUILD and "{pkgbuild_pkgname}.install" are now located in "{bundle_dir}" and ready to be edited'
        )
    else:
        log_info(f'PKGBUILD is now located in "{bundle_dir}" and ready to be edited')

    return bundle_dir


# ---------------------------------------------------------------------------
# Main Conversion Orchestrator
# ---------------------------------------------------------------------------


def convert_package(opts: CLIOptions) -> None:
    assert opts.deb_path is not None
    dest_dir = opts.output_dir if opts.output_dir is not None else opts.deb_path.parent

    with tempfile.TemporaryDirectory(prefix="debtap-ng-") as tmp_root_str:
        work_dir = Path(tmp_root_str)
        control_dir = work_dir / "control"
        pkgroot_dir = work_dir / "pkgroot"
        control_dir.mkdir()
        pkgroot_dir.mkdir()

        log_info("Extracting package data...")
        try:
            data_tar_name = extract_deb_archive(opts.deb_path, control_dir, pkgroot_dir)
        except Exception as exc:
            log_err(str(exc))
            sys.exit(1)

        control_file = control_dir / "control"
        if not control_file.is_file():
            log_err("Malformed deb archive: missing control metadata file")
            sys.exit(1)

        try:
            control_meta = parse_control_file(control_file, opts.pseudo)
        except Exception as exc:
            log_err(str(exc))
            sys.exit(1)

        ctx = PackageBuildContext(
            options=opts,
            control=control_meta,
            work_dir=work_dir,
            control_dir=control_dir,
            pkgroot_dir=pkgroot_dir,
            data_tar_name=data_tar_name,
        )

        log_info("Fixing possible directories structure differencies...")
        normalize_filesystem(ctx)

        # Refine lib32- package name in pseudo mode when package contains only libraries
        if (
            opts.pseudo
            and (pkgroot_dir / "usr" / "lib32").exists()
            and not (pkgroot_dir / "usr" / "bin").exists()
            and not ctx.control.pkgname.startswith("lib32-")
        ):
            ctx.control.pkgname = f"lib32-{ctx.control.pkgname}"

        log_info("Generating .PKGINFO file...")
        prompt_packager_and_license(ctx)
        ctx.installed_size = calculate_installed_size(pkgroot_dir)

        print(
            f"\n{C_GREEN}***{C_RESET} {C_BOLD}Creation of .PKGINFO file in progress. "
            f"Resolving dependencies and ELF sonames...{C_RESET}"
        )

        # Parse conffiles into ALPM backup entries
        conffiles_path = control_dir / "conffiles"
        if conffiles_path.is_file():
            bkp_set: set[str] = set()
            for ln in conffiles_path.read_text(encoding="utf-8", errors="replace").splitlines():
                rel_bkp = ln.strip().lstrip("/")
                if rel_bkp:
                    rel_bkp = re.sub(r"^(?:usr/local/s?bin|usr/sbin|usr/games|s?bin)/", "usr/bin/", rel_bkp)
                    bkp_set.add(rel_bkp)
            ctx.backups = sorted(bkp_set)

        resolver = ArchDependencyResolver(
            pseudo=opts.pseudo,
            wipeout=opts.wipeout,
            self_pkgname=ctx.control.pkgname,
        )
        try:
            ctx.replaces, _ = resolver.reconcile_constraints(ctx.control.replaces_raw)
            combined_conflicts = ", ".join(
                p for p in (ctx.control.conflicts_raw, ctx.control.breaks_raw) if p
            )
            ctx.conflicts, _ = resolver.reconcile_constraints(combined_conflicts)
            ctx.provides, _ = resolver.reconcile_constraints(
                ctx.control.provides_raw,
                strip_versions_force=(opts.pkgbuild or opts.Pkgbuild),
            )

            elf_deps = resolver.inspect_elf_dependencies(pkgroot_dir)
            extra_mandatory = set(elf_deps)
            if ctx.control.python_version_raw:
                extra_mandatory.add("python")

            combined_depends = ", ".join(
                p for p in (ctx.control.depends_raw, ctx.control.pre_depends_raw) if p
            )
            ctx.depends, ctx.untranslated_deps = resolver.reconcile_constraints(
                combined_depends,
                extra_pkgs=extra_mandatory,
                prune_base=True,
            )

            if any("gtk" in d for d in ctx.depends):
                if "hicolor-icon-theme" not in ctx.depends:
                    ctx.depends.append("hicolor-icon-theme")
                    ctx.depends.sort()

            combined_optdeps = ", ".join(
                p for p in (ctx.control.recommends_raw, ctx.control.suggests_raw) if p
            )
            raw_optdeps, ctx.untranslated_optdeps = resolver.reconcile_constraints(combined_optdeps)
            mandatory_names = {re.split(r"[<>=]", d)[0] for d in ctx.depends}
            ctx.optdepends = [
                od for od in raw_optdeps if re.split(r"[<>=]", od)[0] not in mandatory_names
            ]
        finally:
            resolver.close()

        if ctx.untranslated_deps:
            log_warn(
                "These dependencies (depend = fields) could not be translated into Arch Linux packages names:\n"
                + ", ".join(ctx.untranslated_deps)
            )
        if ctx.untranslated_optdeps:
            log_warn(
                "These optional dependencies (optdepend = fields) could not be translated into Arch Linux packages names:\n"
                + ", ".join(ctx.untranslated_optdeps)
            )

        pkginfo_path = write_pkginfo(ctx)

        log_info("Checking and generating .INSTALL file (if necessary)...")
        install_content = translate_maintainer_scripts(
            control_dir, opts.pseudo, ctx.control.arch_deb == "i386"
        )
        install_path: Path | None = None
        if install_content.strip():
            install_path = pkgroot_dir / ".INSTALL"
            install_path.write_text(install_content, encoding="utf-8")

        prompt_interactive_edit(pkginfo_path, install_path, opts.Quiet)

        if not opts.Pkgbuild:
            try:
                build_binary_package(ctx, dest_dir)
            except Exception as exc:
                log_err(str(exc))
                sys.exit(1)

        if opts.pkgbuild or opts.Pkgbuild:
            try:
                generate_pkgbuild_bundle(ctx, dest_dir)
            except Exception as exc:
                log_err(str(exc))
                sys.exit(1)

        log_info("Removing leftover files...")


def main() -> None:
    opts = parse_cli_args(sys.argv[1:])
    if opts.update:
        sync_database()
        return
    convert_package(opts)


if __name__ == "__main__":
    main()
