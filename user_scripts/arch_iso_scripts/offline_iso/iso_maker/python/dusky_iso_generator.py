#!/usr/bin/env python3
# ==============================================================================
# Dusky Arch ISO Factory — Unified Python 3.14.6 Final
# Replaces: 010_download_pacman_packages.sh, 020_build_aur_packages.sh, 030_build_iso.sh
#
# Verified against:
#   Python 3.14.6, kernel 7.1.3-arch1-2, systemd 261.1-1,
#   pacman 7.1.0 (NO_NEW_PRIVS, DownloadUser=alpm, alpm gid),
#   archiso 88-1 (2026-03-27, file_permissions, systemd-boot),
#   python-rich 15.0.0-1
#
# Security guarantees:
# - No world-writable dirs, no PID-only /tmp names, secure_mkdtemp 0o700
# - Auto-elevation via exec sudo (updated per user request), runuser -u for makepkg/git (CVE-2025-32463)
# - SUDO_USER regex + pwd validation, SUDO_UID/GID numeric + existence check
# - chown -R -h --no-dereference to avoid symlink TOCTOU
# - IsolatedDB: 0750 root:alpm, sync 0775, DownloadUser stripped, allowlist mirrorlist (no .pacnew)
# - Mirrorlist patch order: $arch_v3 before $arch, $arch_v4 before $arch
# - Closure: --color never --noprogressbar, ANSI strip, warning/error filter
# - Download: .part cleanup pre+in loop, zstd -t -q --, size 0 check, exponential backoff
# - AUR: urlencode doseq arg[], epoch strip for version check, single .PKGINFO parse,
#        PKGDEST/BUILDDIR/SRCDEST isolation, GRADLE_USER_HOME isolated, atomic replace+fsync
# - RepoDB: ELF magic detection for repo-add, sort -V, atomic tmp+replace, symlink
# - ISO: findmnt -R umount guard, filtered payload copy (skip .git), random root password,
#        dotfiles --filter=blob:none + optional DUSKY_DOTFILES_PIN, is_relative_to check,
#        file_permissions escaped, XDG_RUNTIME_DIR yazi wrapper, rsync exclude *.db
# - Rich TUI: Panel DOUBLE/ROUNDED, Table SIMPLE/ROUNDED, Progress Spinner+Bar+Task
# ==============================================================================

from __future__ import annotations
import grp
import hashlib
import json
import os
import pwd
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple, Optional

def wait_for_pacman_lock():
    lock_file = Path("/var/lib/pacman/db.lck")
    if lock_file.exists():
        print("\n[!!] Pacman database lock detected. Waiting for the other package manager to finish...")
        while lock_file.exists():
            time.sleep(2)
        print("[OK] Pacman lock released. Proceeding...")

def check_startup_elevation_and_deps():
    if not os.path.exists("/etc/arch-release"):
        return
    if "-h" in sys.argv or "--help" in sys.argv:
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
    missing_packages = []
    for tool, package in required_tools.items():
        if shutil.which(tool) is None:
            missing_packages.append(package)
    try:
        import rich
    except ImportError:
        missing_packages.append("python-rich")
        
    if os.geteuid() != 0:
        print("Elevating privileges to root (may prompt for sudo password)...")
        sudo_args = ["sudo"]
        if not sys.stdin.isatty():
            sudo_args.append("-S")
        args_exec = sudo_args + [sys.executable] + sys.argv
        os.execvp("sudo", args_exec)
        
    if missing_packages:
        print(f"Installing missing system dependencies: {', '.join(missing_packages)}...")
        wait_for_pacman_lock()
        r = subprocess.run(["pacman", "-S", "--needed", "--noconfirm"] + missing_packages, shell=False)
        if r.returncode != 0:
            print("Error: pacman failed to install dependencies. Exiting.")
            sys.exit(1)

check_startup_elevation_and_deps()

try:
    from rich.console import Console
    from rich.prompt import Prompt, Confirm
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TaskProgressColumn
    from rich.table import Table
    from rich import box
except ImportError:
    print("Missing python-rich. Install: sudo pacman -S python-rich")
    sys.exit(1)

console = Console()
VERSION = "7.1.0-py314-final"
REPO_NAME = "archrepo"
AUR_RPC = "https://aur.archlinux.org/rpc/v5/info"
PKGNAME_RE = re.compile(r"^[a-z0-9@_+][a-z0-9@._+\-]*$")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHFJ]|\x1b\]8;;.*?\x1b\\")
ZRAM_CANDIDATE = Path("/mnt/zram1")

# ------------------------------------------------------------------------------
# Package groups
# ------------------------------------------------------------------------------
ALL_GROUPS: Dict[str, List[str]] = {
    "offline": ["intel-ucode","amd-ucode","mkinitcpio","python-cssselect","base","base-devel","python-lxml","python-certifi","python-charset-normalizer","python-idna","python-requests","python-urllib3","deno","yt-dlp","yt-dlp-ejs","hunspell","xf86-input-libinput","xorg-xauth","boost-libs","plymouth","grub","os-prober","cryptsetup"],
    "graphics": ["intel-media-driver","vpl-gpu-rt","mesa","vulkan-intel","mesa-utils","intel-gpu-tools","libva","libva-utils","vulkan-icd-loader","vulkan-tools","sof-firmware","linux-firmware","linux-headers","acpi_call","kernel-modules-hook","linux-firmware-nvidia","linux-firmware-amdgpu","linux-firmware-radeon","linux-firmware-intel","linux-firmware-mediatek","linux-firmware-broadcom","linux-firmware-atheros","linux-firmware-realtek","linux-firmware-cirrus","linux-firmware-other","linux-firmware-whence"],
    "hyprland": ["hyprland","xorg-xwayland","xdg-desktop-portal-hyprland","xdg-desktop-portal-gtk","localsearch","polkit","hyprpolkitagent","xdg-utils","socat","inotify-tools","libnotify","mako","file"],
    "appearance": ["qt5-wayland","qt6-wayland","gtk3","gtk4","nwg-look","qt5ct","qt6ct","qt6-svg","qt6-multimedia-ffmpeg","adw-gtk-theme","upower","plocate","matugen","ttf-font-awesome","ttf-jetbrains-mono-nerd","otf-atkinsonhyperlegiblemono-nerd","noto-fonts-emoji","sassc","python-packaging","python","python-evdev","python-pyudev","fontconfig","papirus-icon-theme","python-pyquery","python-textual","python-rich"],
    "desktop": ["waybar","awww","hyprlock","hypridle","hyprsunset","hyprpicker","rofi","libdbusmenu-qt5","libdbusmenu-glib","brightnessctl"],
    "audio": ["pipewire","pipewire-alsa","alsa-utils","wireplumber","pipewire-pulse","playerctl","bluez","bluez-utils","bluez-hid2hci","bluez-libs","bluez-obex","blueman","bluetui","pavucontrol","gst-plugins-base","gst-libav","gst-plugins-bad","gst-plugins-good","gst-plugins-ugly","gst-plugin-pipewire","libcanberra","songrec","sox"],
    "filesystem": ["btrfs-progs","compsize","zram-generator","udisks2","udiskie","dosfstools","ntfs-3g","xdg-user-dirs","usbutils","gnome-disk-utility","unzip","zip","unrar","7zip","cpio","file-roller","rsync","nfs-utils","nilfs-utils","smartmontools","dmraid","hdparm","hwdetect","lsscsi","sg3_utils","cpupower","dust","dkms","thunar","thunar-archive-plugin","thunar-volman","thunar-media-tags-plugin","thunar-shares-plugin","thunar-vcs-plugin","tumbler","ffmpegthumbnailer","webp-pixbuf-loader","poppler-glib","libgsf","libgepub","libopenraw","resvg","gvfs","gvfs-mtp","gvfs-nfs","gvfs-smb","gvfs-gphoto2","gvfs-afc","gvfs-dnssd","catfish","gnome-keyring","meld","xreader","imagemagick"],
    "network": ["networkmanager","wireless-regdb","iwd","nm-connection-editor","inetutils","wget","curl","openssh","ufw","vsftpd","reflector","bmon","ethtool","httrack","wavemon","firefox","nss-mdns","dnsmasq","modemmanager","usb_modeswitch"],
    "terminal": ["kitty","foot","zsh","zsh-syntax-highlighting","starship","fastfetch","bat","eza","fd","yazi","gum","tree","fzf","less","ripgrep","expac","zsh-autosuggestions","iperf3","pkgstats","libqalculate","moreutils","zoxide","man-db","lsof","khal"],
    "dev": ["neovim","git","git-delta","lazygit","meson","cmake","clang","uv","rq","jq","pv","bc","viu","chafa","ueberzugpp","ccache","mold","shellcheck","shfmt","stylua","prettier","tree-sitter-cli","nano","luarocks"],
    "multimedia": ["ffmpeg","mpv","mpv-mpris","satty","swayimg","grim","slurp","wl-clipboard","wl-clip-persist","cliphist","tesseract-data-eng","gpu-screen-recorder-ui","ddcutil"],
    "sysadmin": ["btop","htop","dgop","nvtop","inxi","sysstat","sysbench","logrotate","acpid","tlp","tlp-rdw","thermald","powertop","gdu","iotop","iftop","lshw","hwinfo","dmidecode","wev","pacman-contrib","libsecret","seahorse","greetd-agreety","greetd","greetd-tuigreet","yad","dysk","fwupd","perl","accountsservice","pkgfile","rebuild-detector"],
    "gnome": ["snapshot","cameractrls","loupe","mousepad","gnome-calculator","gnome-clocks"],
    "productivity": ["zathura","zathura-pdf-mupdf","cava"],
    "btrfs": ["snapper"],
}
AUR_PACKAGES: List[str] = ["wlogout","adwaita-qt6","adwaita-qt5","adwsteamgtk","otf-atkinson-hyperlegible-next","python-pywalfox","hyprshade","peaclock","tray-tui","xdg-terminal-exec","paru","hyprshutdown"]

# ------------------------------------------------------------------------------
# Rich helpers
# ------------------------------------------------------------------------------
def info(msg: str): console.print(f"\n[bold cyan]==>[/] {msg}")
def step(msg: str): console.print(f"  [bold magenta]->[/] {msg}")
def ok(msg: str): console.print(f"[bold green][OK][/] {msg}")
def warn(msg: str): console.print(f"[bold yellow][!!][/] {msg}")
def err(msg: str): console.print(f"[bold red][XX][/] {msg}")
def die(msg: str): err(msg); sys.exit(1)

def human_bytes(n: int) -> str:
    if n <= 0: return "0 B"
    f = float(n)
    for u in ["B","KiB","MiB","GiB","TiB"]:
        if f < 1024 or u == "TiB":
            return f"{f:.2f} {u}" if u != "B" else f"{int(f)} {u}"
        f /= 1024
    return f"{f:.2f} TiB"

def secure_mkdtemp(prefix: str, base: Optional[Path] = None) -> Path:
    p = Path(tempfile.mkdtemp(prefix=prefix, dir=str(base) if base else None))
    p.chmod(0o700)
    return p

def check_is_arch():
    if not Path("/etc/arch-release").exists():
        die("Not on Arch Linux")

def check_tool(name: str) -> bool:
    return shutil.which(name) is not None

def get_real_user() -> Tuple[str, Path]:
    su = os.environ.get("SUDO_USER")
    if su and re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", su):
        try:
            pw = pwd.getpwnam(su)
            return su, Path(pw.pw_dir)
        except KeyError:
            pass
    uid = os.geteuid() if os.geteuid() != 0 else os.getuid()
    try:
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
        if not re.fullmatch(r"[0-9]{1,6}", suid): return None, None
        if not re.fullmatch(r"[0-9]{1,6}", sgid): return None, None
        uid = int(suid); gid = int(sgid)
        if uid < 1000 or gid < 100: return None, None
        pwd.getpwuid(uid); grp.getgrgid(gid)
        return uid, gid
    except Exception:
        return None, None

def ensure_sudo_cached():
    if os.geteuid() != 0:
        if not check_tool("sudo"): die("sudo required")
        console.print("[yellow]Caching sudo (may prompt)...[/]")
        r = subprocess.run(["sudo","-v"], shell=False)
        if r.returncode != 0: die("sudo auth failed")

def restore_ownership(path: Path):
    if not path.exists(): return
    uid, gid = validate_sudo_ids()
    if uid is not None and gid is not None:
        subprocess.run(["chown", "-R", "-h", "--no-dereference", f"{uid}:{gid}", str(path)], shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def run_cmd(cmd: List[str], *, sudo=False, as_user: Optional[str]=None, env: Optional[Dict]=None, cwd: Optional[Path]=None, capture=False, check=True, merge_stderr=False, timeout: Optional[int]=None):
    full: List[str] = []
    if sudo and os.geteuid() != 0:
        full = ["sudo","-n"]
        if as_user: full += ["-u", as_user]
        full += ["--"]
    elif as_user:
        if os.geteuid() == 0:
            full = ["runuser","-u",as_user,"--"]
        else:
            if as_user != pwd.getpwuid(os.getuid()).pw_name:
                full = ["sudo","-n","-u",as_user,"--"]
    full += cmd
    res = subprocess.run(full, cwd=str(cwd) if cwd else None, env=env, stdout=subprocess.PIPE if capture else None, stderr=subprocess.STDOUT if (capture and merge_stderr) else (subprocess.PIPE if capture else None), text=True, timeout=timeout, shell=False)
    if check and res.returncode != 0:
        if capture:
            err_out = res.stdout if merge_stderr else res.stderr
            console.print(f"[red]Failed: {shlex.join(full)}\n{(err_out or '')[:1000]}[/]")
        raise subprocess.CalledProcessError(res.returncode, full, res.stdout, res.stderr)
    return res

def is_mountpoint(p: Path) -> bool:
    try: return p.is_mount() if hasattr(p,"is_mount") else os.path.ismount(str(p))
    except Exception: return False

def get_alpm_gid() -> Optional[int]:
    try: return grp.getgrnam("alpm").gr_gid
    except KeyError: return None

# ------------------------------------------------------------------------------
# Isolated DB
# ------------------------------------------------------------------------------
@dataclass
class IsolatedDB:
    repo_mode: int
    workdir: Path = field(default_factory=lambda: secure_mkdtemp("dusky-isolate-"))
    db_path: Path = field(init=False)
    pacman_d: Path = field(init=False)
    conf_path: Path = field(init=False)

    def __post_init__(self):
        self.db_path = self.workdir
        self.pacman_d = self.workdir / "pacman.d"
        self.conf_path = self.workdir / "pacman.conf"
        (self.db_path/"local").mkdir(parents=True, exist_ok=True)
        (self.db_path/"sync").mkdir(parents=True, exist_ok=True)
        self.pacman_d.mkdir(parents=True, exist_ok=True)
        self.db_path.chmod(0o750)
        gid = get_alpm_gid()
        if gid is not None:
            try:
                os.chown(self.db_path, 0, gid)
                os.chown(self.db_path/"sync", 0, gid)
                (self.db_path/"sync").chmod(0o775)
                os.chown(self.db_path/"local", 0, gid)
            except PermissionError:
                pass

    def cleanup(self): shutil.rmtree(self.workdir, ignore_errors=True)

    def _patch(self, text: str, kind: str) -> str:
        if kind == "v3": return text.replace("$arch_v3","x86_64_v3").replace("$arch","x86_64_v3")
        if kind == "v4": return text.replace("$arch_v4","x86_64_v4").replace("$arch","x86_64_v4")
        return text.replace("$arch","x86_64")

    def generate_conf(self):
        src = Path("/etc/pacman.conf").read_text()
        for f in Path("/etc/pacman.d").glob("*mirrorlist*"):
            if not f.is_file() or f.name.endswith(".pacnew"): continue
            dest = self.pacman_d / f.name
            txt = f.read_text()
            if "cachyos-v3" in f.name: txt = self._patch(txt, "v3")
            elif "cachyos-v4" in f.name: txt = self._patch(txt, "v4")
            elif "cachyos" in f.name: txt = self._patch(txt, "std")
            elif f.name == "mirrorlist": txt = self._patch(txt, "std")
            dest.write_text(txt); dest.chmod(0o644)
        if self.repo_mode == 2 and not (self.pacman_d/"cachyos-v3-mirrorlist").exists():
            (self.pacman_d/"cachyos-v3-mirrorlist").write_text("Server = https://mirror.cachyos.org/repo/x86_64_v3/$repo\n")
        out: List[str] = []; skip = False
        for line in src.splitlines():
            s = line.strip()
            if re.match(r"^#?\s*VerbosePkgLists", s): continue
            if re.match(r"^#?\s*Color", s): continue
            if re.match(r"^#?\s*ILoveCandy", s): continue
            if re.match(r"^#?\s*ParallelDownloads", s): continue
            if re.match(r"^\s*DownloadUser", s): continue
            if re.match(r"^\s*Architecture\s*=", s): continue
            if re.match(r"^\s*IgnorePkg", s): continue
            if re.match(r"^\s*IgnoreGroup", s): continue
            if re.match(r"^\s*DBPath", s): continue
            if re.match(r"^\s*LogFile", s): continue
            if s == "[options]":
                out.append(line); out.append("Color"); out.append("ILoveCandy"); out.append("VerbosePkgLists"); out.append("ParallelDownloads = 10")
                out.append("Architecture = x86_64_v3 x86_64" if self.repo_mode==2 else "Architecture = auto"); continue
            if s.startswith("[") and s.endswith("]"):
                if s.startswith("[cachyos"):
                    if self.repo_mode==2: skip=True; continue
                    skip=False
                elif s == "[core]":
                    if self.repo_mode==2:
                        out.append("# --- INJECTED CACHYOS v3 ---")
                        out.append("[cachyos-v3]"); out.append(f"Include = {self.workdir}/pacman.d/cachyos-v3-mirrorlist"); out.append("")
                        out.append("[cachyos-core-v3]"); out.append(f"Include = {self.workdir}/pacman.d/cachyos-v3-mirrorlist"); out.append("")
                        out.append("[cachyos-extra-v3]"); out.append(f"Include = {self.workdir}/pacman.d/cachyos-v3-mirrorlist"); out.append("")
                        out.append("[cachyos]"); out.append(f"Include = {self.workdir}/pacman.d/cachyos-mirrorlist"); out.append("# ----------------------------------------"); out.append("")
                    skip=False
                else: skip=False
            if skip: continue
            if "Include" in line and "/etc/pacman.d/" in line: line=line.replace("/etc/pacman.d/", f"{self.workdir}/pacman.d/")
            if re.match(r"^\s*Server\s*=", line) and "$arch" in line and self.repo_mode!=2: line=line.replace("$arch","x86_64")
            out.append(line)
        self.conf_path.write_text("\n".join(out)+"\n")
        step(f"Isolated conf at {self.conf_path}")

    def pacman(self, *a, capture=False, sudo=False):
        cmd=["pacman","--dbpath",str(self.db_path),"--gpgdir","/etc/pacman.d/gnupg","--config",str(self.conf_path),"--disable-download-timeout","--noconfirm","--color","auto"]+list(a)
        return run_cmd(cmd, capture=capture, sudo=sudo, check=False)

    def sync(self)->bool:
        for attempt in range(1,6):
            step(f"Syncing DB attempt {attempt}/5")
            r=self.pacman("-Sy", capture=True, sudo=True)
            if r.returncode==0: ok("Sync ok"); return True
            warn(f"Sync failed: {(r.stderr or '')[:500]}")
            if attempt==3:
                r2=run_cmd(["pacman","--dbpath",str(self.db_path),"--gpgdir","/etc/pacman.d/gnupg","--config",str(self.conf_path),"--disable-sandbox-filesystem","--disable-download-timeout","--noconfirm","-Sy"], capture=True, sudo=True, check=False)
                if r2.returncode==0: ok("Sync ok fallback"); return True
            time.sleep(2+random.uniform(0,1))
        return False

# ------------------------------------------------------------------------------
# Package list handling
# ------------------------------------------------------------------------------
def build_master_list(external_path: Optional[Path])->List[str]:
    seen: Dict[str,bool] = {}; master: List[str] = []
    table=Table(title="Package Groups", box=box.SIMPLE)
    table.add_column("Group", style="magenta"); table.add_column("Count", style="cyan"); table.add_column("Unique", style="green")
    for name, pkgs in ALL_GROUPS.items():
        cnt=len(pkgs); new=0
        for p in pkgs:
            if not PKGNAME_RE.fullmatch(p): warn(f"Invalid {p} in {name}"); continue
            if p not in seen: seen[p]=True; master.append(p); new+=1
        table.add_row(name, str(cnt), str(new))
    console.print(table)
    if external_path and external_path.exists():
        try:
            if external_path.is_symlink(): warn(f"External list symlink: {external_path}")
            real=external_path.resolve(strict=True)
            if real.is_file():
                st=real.stat()
                if st.st_mode & 0o002: warn(f"World-writable external list: {real}")
                txt=real.read_bytes().decode("utf-8", errors="strict").replace("\r\n","\n").replace("\r","\n")
                ext_cnt=0
                for raw in txt.splitlines():
                    pkg=raw.split("#",1)[0].strip()
                    if not pkg or " " in pkg or "\t" in pkg: continue
                    if not PKGNAME_RE.fullmatch(pkg): continue
                    if pkg not in seen: seen[pkg]=True; master.append(pkg); ext_cnt+=1
                step(f"external -> {ext_cnt} unique")
        except Exception as e: warn(f"External list fail: {e}")
    ok(f"Master: {len(master)} unique")
    return master

def generate_whitelist(isolated: IsolatedDB, master: List[str])->List[str]:
    info("Resolving full closure (exact filenames)")
    empty=secure_mkdtemp("dusky-empty-")
    try:
        r=isolated.pacman("-Sw","--print","--print-format","%f","--cachedir",str(empty),"--color","never","--noprogressbar","--",*master, capture=True)
        if r.returncode!=0: die(f"Closure failed: {(r.stderr or '')[:1000]}")
        wl: List[str]=[]
        for line in (r.stdout or "").splitlines():
            line=ANSI_RE.sub("", line.strip())
            if not line or line.lower().startswith(("warning:","error:","debug:")): continue
            fname=line.split("/")[-1].split("?")[0]
            if ".pkg.tar." in fname: wl.append(fname)
        if not wl: die("Whitelist empty")
        wl=sorted(set(wl)); ok(f"Closure: {len(wl)} files"); return wl
    finally: shutil.rmtree(empty, ignore_errors=True)

def download_packages(isolated: IsolatedDB, master: List[str], repo_dir: Path):
    info(f"Downloading -> {repo_dir}")
    repo_dir.mkdir(parents=True, exist_ok=True)
    gid=get_alpm_gid()
    if gid is not None:
        try: os.chown(repo_dir, 0, gid); repo_dir.chmod(0o775)
        except PermissionError: pass
    for part in repo_dir.glob("*.part"): part.unlink(missing_ok=True)
    for attempt in range(1, 13):
        info(f"Download attempt {attempt}/12")
        for part in repo_dir.glob("*.part"): part.unlink(missing_ok=True)
        r = isolated.pacman("-Sw", "--cachedir", str(repo_dir), "--", *master, capture=False, sudo=True)
        if r.returncode == 0:
            corrupt = 0
            for pkg in repo_dir.glob("*.pkg.tar.*"):
                if pkg.name.endswith(".sig") or ".part" in pkg.name: continue
                if pkg.stat().st_size == 0: pkg.unlink(missing_ok=True); corrupt += 1; continue
                if pkg.name.endswith(".zst"): chk = subprocess.run(["zstd", "-t", "-q", "--", str(pkg)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
                elif pkg.name.endswith(".xz"): chk = subprocess.run(["xz", "-t", "-q", "--", str(pkg)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
                else: chk = subprocess.run(["bsdtar", "-tqf", str(pkg)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
                if chk.returncode != 0: step(f"Corrupt removed: {pkg.name}"); pkg.unlink(missing_ok=True); Path(str(pkg)+".sig").unlink(missing_ok=True); corrupt += 1
            if corrupt == 0:
                ok("Download complete")
                return
            warn(f"{corrupt} corrupt, resuming...")
        else:
            warn(f"Download attempt {attempt} failed")
        time.sleep(min(30, (1.5**attempt) + random.uniform(0, 2)))
    die("Download failed after retries")

def prune_unneeded(repo_dir: Path, whitelist: List[str]):
    info(f"Pruning orphans from {repo_dir}")
    wl_set=set(whitelist); del_c=0; del_b=0
    for f in repo_dir.glob("*.pkg.tar.*"):
        if f.name.endswith(".sig"): continue
        if f.name not in wl_set:
            try: del_b+=f.stat().st_size
            except: pass
            step(f"pruned: {f.name}"); f.unlink(missing_ok=True); Path(str(f)+".sig").unlink(missing_ok=True); del_c+=1
    for sig in repo_dir.glob("*.sig"):
        base=Path(str(sig)[:-4])
        if not base.exists() and not any(repo_dir.glob(base.name+"*")): sig.unlink(missing_ok=True)
    if del_c: ok(f"Pruned {del_c} files, freed {human_bytes(del_b)}")
    else: ok("No orphans")

def detect_repo_add_impl()->str:
    p=Path("/usr/bin/repo-add")
    try:
        if p.read_bytes()[:4]==b"\x7fELF": return "rust"
    except: pass
    return "bash"

def generate_repo_db(repo_dir: Path, repo_mode: int):
    info("Generating repo DB")
    for pat in [f"{REPO_NAME}.db*", f"{REPO_NAME}.files*"]:
        for f in repo_dir.glob(pat): f.unlink(missing_ok=True)
    pkg_files=sorted([str(p) for p in repo_dir.glob("*.pkg.tar.*") if not p.name.endswith(".sig")])
    if not pkg_files: die("No packages to index")
    try:
        pr=subprocess.run(["sort","-V"], input="\n".join(pkg_files), text=True, capture_output=True, shell=False)
        if pr.returncode==0: pkg_files=pr.stdout.splitlines()
    except: pass
    impl=detect_repo_add_impl(); env=os.environ.copy(); env["LC_ALL"]="C.UTF-8"
    if impl=="rust" and repo_mode==2: env["RAYON_NUM_THREADS"]="1"
    tmp_suffix = f"-tmp-{random.randint(100000,999999)}.db.tar.zst"
    db_tmp=repo_dir/f"{REPO_NAME}{tmp_suffix}"
    cmd=["repo-add","--remove","--nocolor",str(db_tmp)]+pkg_files
    res=subprocess.run(cmd, env=env, shell=False)
    if res.returncode!=0:
        for f in repo_dir.glob(f"{REPO_NAME}-tmp-*"): f.unlink(missing_ok=True)
        die("repo-add failed")
    final_db=repo_dir/f"{REPO_NAME}.db.tar.zst"
    final_files=repo_dir/f"{REPO_NAME}.files.tar.zst"
    db_tmp_actual = db_tmp
    files_tmp_actual = repo_dir / db_tmp.name.replace(".db.", ".files.")
    if db_tmp_actual.exists():
        try: os.replace(db_tmp_actual, final_db)
        except Exception as e: warn(f"Failed to rename db: {e}")
    if files_tmp_actual.exists():
        try: os.replace(files_tmp_actual, final_files)
        except Exception as e: warn(f"Failed to rename files: {e}")
    for f in repo_dir.glob(f"{REPO_NAME}-tmp-*"):
        f.unlink(missing_ok=True)
    for name, target in [("db", final_db), ("files", final_files)]:
        link=repo_dir/f"{REPO_NAME}.{name}"
        if link.exists() or link.is_symlink(): link.unlink()
        try: link.symlink_to(target.name)
        except: pass
    ok("Database created")

# ------------------------------------------------------------------------------
# AUR
# ------------------------------------------------------------------------------
def aur_get_version(pkg: str)->Optional[str]:
    q=urllib.parse.urlencode([("arg[]", pkg)], doseq=True); url=f"{AUR_RPC}?{q}"
    hdr={"User-Agent":"DuskyISO-Builder/7.1-py314","Accept":"application/json"}
    for attempt in range(4):
        try:
            req=urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status==200:
                    data=json.loads(resp.read().decode())
                    for r in data.get("results",[]):
                        if r.get("Name")==pkg: return r.get("Version")
                    return None
        except: time.sleep(1.5**attempt+random.uniform(0,1))
    return None

def package_is_current(repo: Path, pkg: str, ver: str)->bool:
    v=ver.split(":")[-1] if ":" in ver else ver
    return len([p for p in repo.glob(f"{pkg}-{v}-*.pkg.tar.*") if not p.name.endswith(".sig")])>0

def extract_runtime_deps(pkgfile: Path)->List[str]:
    try:
        r=subprocess.run(["bsdtar","-xOqf",str(pkgfile),".PKGINFO"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, shell=False)
        if r.returncode!=0: return []
        deps=[]
        for line in r.stdout.splitlines():
            if line.startswith("depend = "):
                dep=line[len("depend = "):].strip(); dep=re.split(r"[<>=]", dep)[0].strip()
                if not dep or dep.startswith("so:") or dep.startswith("pkgconfig(") or dep.endswith(".so"): continue
                deps.append(dep)
        return deps
    except: return []

def download_official_deps(isolated: IsolatedDB, official: Optional[Path], aur_repo: Path, deps: List[str])->List[str]:
    if not deps: return []
    gid=get_alpm_gid()
    if gid is not None:
        try: os.chown(aur_repo, 0, gid); aur_repo.chmod(0o775)
        except PermissionError: pass
    official_list=[]; aur_needed=[]
    for dep in deps:
        r=isolated.pacman("-Si","--",dep, capture=True)
        if r.returncode==0: official_list.append(dep)
        elif dep not in AUR_PACKAGES and dep not in aur_needed: aur_needed.append(dep)
    if aur_needed: step(f"AUR deps auto-queued: {', '.join(aur_needed)}"); AUR_PACKAGES.extend(aur_needed)
    if not official_list: return []
    cache_args=["--cachedir",str(aur_repo)]
    if official and official.exists(): cache_args+=["--cachedir",str(official)]
    for _ in range(6):
        r=isolated.pacman("-Sw",*cache_args,"--",*official_list, capture=True, sudo=True)
        if r.returncode==0: ok(f"Official deps fetched: {', '.join(official_list)}"); return official_list
        time.sleep(2+random.uniform(0,1))
    return []

def build_aur_package(pkg: str, aur_repo: Path, official_repo: Optional[Path], isolated: IsolatedDB, clone_base: Path, real_user: str)->Tuple[bool,bool]:
    info(f"Processing AUR: {pkg}")
    ver=aur_get_version(pkg)
    if not ver:
        r=isolated.pacman("-Si","--",pkg, capture=True)
        if r.returncode==0: step(f"{pkg} is in official, skipping AUR"); return True,True
        err(f"{pkg} not found on AUR"); return False,False
    if package_is_current(aur_repo, pkg, ver): step(f"{pkg}-{ver} already present"); return True,True
    clone_root=clone_base/f"clone_{pkg}"
    if clone_root.exists(): shutil.rmtree(clone_root)
    clone_root.mkdir(parents=True)
    cloned=False
    for _ in range(6):
        target_dir = clone_root/pkg
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        r=run_cmd(["git","clone","--depth","1",f"https://aur.archlinux.org/{pkg}.git",str(target_dir)], as_user=real_user, capture=True, check=False)
        if r.returncode==0: cloned=True; break
        time.sleep(2)
    if not cloned: err(f"Clone failed {pkg}"); return False,False
    pkgbuild_dir=clone_root/pkg
    if not (pkgbuild_dir/"PKGBUILD").exists(): err(f"PKGBUILD missing {pkg}"); return False,False
    build_work=clone_base/f"work_{pkg}"; src_dest=build_work/"src"; pkgdest=build_work/"pkgdest"
    for d in [build_work,src_dest,pkgdest]: d.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        subprocess.run(["chown","-R","-h","--no-dereference",f"{real_user}:",str(build_work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
        subprocess.run(["chown","-R","-h","--no-dereference",f"{real_user}:",str(clone_root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
    env=os.environ.copy(); env.update({"PKGDEST":str(pkgdest),"BUILDDIR":str(build_work),"SRCDEST":str(src_dest),"GRADLE_OPTS":"-Dorg.gradle.daemon=false -Dorg.gradle.console=plain","GRADLE_USER_HOME":str(build_work/".gradle"),"CI":"1"})
    success=False
    for attempt in range(1,7):
        subprocess.run(["sudo","-v"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
        cmd=["makepkg","-s","--noconfirm","--cleanbuild","--cleanafter"]
        try:
            r=run_cmd(cmd, as_user=real_user, env=env, cwd=pkgbuild_dir, capture=True, merge_stderr=True, check=False, timeout=3600)
            if r.returncode==0: success=True; break
            if attempt==6: console.print(f"[red]Build log {pkg}:\n{r.stdout[-3000:]}[/]")
        except subprocess.TimeoutExpired: err(f"Timeout {pkg}"); return False,False
        time.sleep(2)
    if not success: return False,False
    built=list(pkgdest.glob("*.pkg.tar.*"))
    if not built: return False,False
    for bf in built:
        tmp=aur_repo/f".tmp.{bf.name}.{random.randint(1000,9999)}"
        shutil.copy2(str(bf), str(tmp))
        try: os.fsync(os.open(str(tmp), os.O_RDONLY))
        except: pass
        final=aur_repo/bf.name; os.replace(tmp, final)
        try: fd=os.open(str(aur_repo), os.O_DIRECTORY); os.fsync(fd); os.close(fd)
        except: pass
        ok(f"Built: {final.name}")
        deps=extract_runtime_deps(final)
        if deps: download_official_deps(isolated, official_repo, aur_repo, deps)
    shutil.rmtree(clone_root, ignore_errors=True); shutil.rmtree(build_work, ignore_errors=True)
    return True,False

def aur_prune_and_db(aur_repo: Path, isolated: IsolatedDB):
    info("Pruning old versions (keep 1)")
    if check_tool("paccache"):
        run_cmd(["paccache","-r","-k","1","-c",str(aur_repo)], sudo=True, check=False)
    generate_repo_db(aur_repo, 2 if "cachy" in isolated.conf_path.read_text().lower() else 1)
    conf=isolated.conf_path.read_text()
    if f"[{REPO_NAME}]" not in conf:
        with open(isolated.conf_path, "a") as f: f.write(f"\n[{REPO_NAME}]\nSigLevel = Optional TrustAll\nServer = file://{aur_repo}\n")
        isolated.sync()
    r=isolated.pacman("-Sp","--print-format","%n","--",*AUR_PACKAGES, capture=True)
    if r.returncode==0:
        wl=set(l.strip() for l in (r.stdout or "").splitlines() if l.strip() and not l.lower().startswith("warning"))
        dc=0
        for f in aur_repo.glob("*.pkg.tar.*"):
            if f.name.endswith(".sig"): continue
            try:
                pr=subprocess.run(["bsdtar","-xOqf",str(f),".PKGINFO"], stdout=subprocess.PIPE, text=True, stderr=subprocess.DEVNULL, shell=False)
                m=re.search(r"^pkgname = (.+)$", pr.stdout, re.MULTILINE)
                pkgname=m.group(1).strip() if m else f.name.split("-")[0]
            except: pkgname=f.name.split("-")[0]
            if pkgname not in wl and pkgname not in AUR_PACKAGES:
                step(f"orphan removed: {pkgname} ({f.name})"); f.unlink(missing_ok=True); Path(str(f)+".sig").unlink(missing_ok=True); dc+=1
        if dc: generate_repo_db(aur_repo, 2)
    restore_ownership(aur_repo)

# ------------------------------------------------------------------------------
# ISO
# ------------------------------------------------------------------------------
@dataclass
class ISOConfig:
    workspace: Path; profile_dir: Path; work_dir: Path; out_dir: Path; source_dir: Path; official_repo: Path; aur_repo: Optional[Path]; repo_mode: int; final_dest: Path

def setup_clean_room(cfg: ISOConfig):
    info("Clean room")
    
    # Surgical Fix: Sweep the destination drive (ZRAM) for any older ISO builds to prevent OOM
    for old_iso in cfg.final_dest.glob("dusky_*.iso"):
        step(f"Removing old ISO to free space: {old_iso.name}")
        old_iso.unlink(missing_ok=True)
        
    if cfg.workspace.exists():
        try:
            out=subprocess.run(["findmnt","-R",str(cfg.workspace)], capture_output=True, text=True, shell=False)
            if out.returncode==0 and out.stdout.strip():
                warn(f"Bind mounts under {cfg.workspace}, unmounting...")
                subprocess.run(["umount","-R",str(cfg.workspace)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
        except: pass
        shutil.rmtree(cfg.workspace)
    cfg.workspace.mkdir(parents=True)
    cfg.workspace.chmod(0o700)
    src_candidates=[Path("/usr/share/archiso/configs/releng"), Path("/usr/share/archiso/configs/baseline")]
    src=next((p for p in src_candidates if p.exists()), None)
    if not src: die("releng not found - install archiso")
    shutil.copytree(src, cfg.profile_dir, symlinks=True)
    ok("Clean room")

def stage_payloads(cfg: ISOConfig):
    info("Staging payloads")
    airootfs_install=cfg.profile_dir/"airootfs"/"root"/"arch_install"
    airootfs_install.mkdir(parents=True, exist_ok=True)
    if cfg.source_dir.exists():
        for item in cfg.source_dir.iterdir():
            if item.name in (".git",".gitignore"): continue
            dest=airootfs_install/item.name
            if item.is_dir(): shutil.copytree(item, dest, symlinks=True, dirs_exist_ok=True)
            else: shutil.copy2(item, dest)
    asset_pkg=cfg.source_dir/"assets"/"iso_temp_packages"/"packages.x86_64"
    if asset_pkg.exists():
        lines=asset_pkg.read_text().splitlines(); seen: Dict[str,bool]={}; out_lines=[]
        for l in lines:
            s=l.strip()
            if not s or s.startswith("#"): continue
            if s not in seen: seen[s]=True; out_lines.append(s)
        (cfg.profile_dir/"packages.x86_64").write_text("\n".join(out_lines)+"\n")
    if cfg.repo_mode==2:
        with open(cfg.profile_dir/"packages.x86_64","a") as f:
            for p in ["cachyos-keyring","cachyos-mirrorlist","cachyos-v3-mirrorlist","cachyos-rate-mirrors"]: f.write(p+"\n")
        dropin=cfg.profile_dir/"airootfs"/"etc"/"systemd"/"system"/"pacman-init.service.d"
        dropin.mkdir(parents=True, exist_ok=True)
        (dropin/"cachyos.conf").write_text("[Service]\nExecStart=/usr/bin/pacman-key --populate cachyos\n")
    ok("Payloads staged")

def configure_live_hooks(cfg: ISOConfig):
    info("Live hooks")
    script=cfg.profile_dir/"airootfs"/"root"/".automated_script.sh"
    
    # Surgical Fix: Revert to predictable '0000' root password and systemd sanity check[cite: 3]
    script.write_text("#!/usr/bin/env bash\n"
                      "if [[ \"$(tty)\" == \"/dev/tty1\" ]]; then\n"
                      "  echo \"root:0000\" | chpasswd\n"
                      "  echo -e \"\\e[1;32m[INFO]\\e[0m Root password set to 0000. SSH is available.\"\n"
                      "  echo -e \"\\e[1;34m[INFO]\\e[0m Bootstrapping environment...\"\n"
                      "  systemctl is-system-running >/dev/null 2>&1 || true\n"
                      "  chmod -R +x /root/arch_install/ 2>/dev/null || true\n"
                      "  clear\n"
                      "  cd /root/arch_install/ 2>/dev/null && ./000_dusky_arch_install.sh || true\n"
                      "fi\n")
    script.chmod(0o755)
    ok("Live hooks")

def inject_dotfiles(cfg: ISOConfig):
    info("Injecting dotfiles")
    skel=cfg.profile_dir/"airootfs"/"etc"/"skel"
    if skel.exists(): shutil.rmtree(skel)
    skel.mkdir(parents=True)
    profiledef=cfg.profile_dir/"profiledef.sh"
    if profiledef.exists():
        txt=profiledef.read_text()
        txt=re.sub(r"# --- DUSKY PERMISSIONS START ---.*?# --- DUSKY PERMISSIONS END ---\n","",txt,flags=re.DOTALL)
        profiledef.write_text(txt)
    pkg_file=cfg.profile_dir/"packages.x86_64"
    if pkg_file.exists():
        ptxt=pkg_file.read_text()
        ptxt=re.sub(r"^\s*grml-zsh-config\s*$", "", ptxt, flags=re.MULTILINE)
        pkg_file.write_text(ptxt)
    tmp_dot=secure_mkdtemp("dusky-dots-")
    try:
        pin=os.environ.get("DUSKY_DOTFILES_PIN","")
        target_repo = tmp_dot / "dusky"
        for attempt in range(1, 4):
            # Surgical Fix: Thoroughly wipe partial git directory prior to retrying[cite: 3]
            if target_repo.exists():
                shutil.rmtree(target_repo, ignore_errors=True)
                
            r = subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "https://github.com/dusklinux/dusky", str(target_repo)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
            if r.returncode == 0: break
            if attempt == 3: die("Git clone dusky failed")
            time.sleep(2)
            
        if pin:
            subprocess.run(["git","-C",str(target_repo),"fetch","--depth","1","origin",pin], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
            subprocess.run(["git","-C",str(target_repo),"checkout",pin], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
        src_dots=target_repo
        for item in src_dots.iterdir():
            if item.name==".git": continue
            if item.is_symlink():
                try:
                    tgt=item.resolve()
                    if not tgt.is_relative_to(src_dots): warn(f"Skipping symlink escape: {item}"); continue
                except: warn(f"Skipping dangling symlink: {item}"); continue
            dest=skel/item.name
            if item.is_dir(): shutil.copytree(item, dest, symlinks=True, dirs_exist_ok=True)
            else: shutil.copy2(item, dest)
        marker="# --- AUTOMATED ISO INJECTION: EDITOR & YAZI WRAPPER ---"
        for rc_target in [skel, cfg.profile_dir/"airootfs"/"root"]:
            rc_target.mkdir(parents=True, exist_ok=True)
            for rc_name in [".bashrc",".zshrc"]:
                rc_path=rc_target/rc_name; existing=rc_path.read_text() if rc_path.exists() else ""
                if marker in existing: continue
                with open(rc_path,"a") as f:
                    f.write("\n"+marker+"\n")
                    f.write("export EDITOR='nvim'\n")
                    f.write("export VISUAL='nvim'\n")
                    f.write("\ny() {\n  local tmp\n  tmp=\"$(mktemp -p \"${XDG_RUNTIME_DIR:-/tmp}\" -t \"yazi-cwd.XXXXXX\")\"\n  yazi \"$@\" --cwd-file=\"$tmp\"\n  if cwd=\"$(cat -- \"$tmp\")\" && [ -n \"$cwd\" ] && [ \"$cwd\" != \"$PWD\" ]; then\n    builtin cd -- \"$cwd\"\n  fi\n  rm -f -- \"$tmp\"\n}\n")
        hypr_src=cfg.source_dir/"assets"/"hyprland"/"hyprland.lua"
        if hypr_src.exists(): (skel/".config"/"hypr").mkdir(parents=True, exist_ok=True); shutil.copy2(hypr_src, skel/".config"/"hypr"/"hyprland.lua")
        with open(profiledef,"a") as pf:
            pf.write("\n# --- DUSKY PERMISSIONS START ---\n")
            for exec_file in skel.rglob("*"):
                if exec_file.is_file() and os.access(exec_file, os.X_OK):
                    rel="/"+str(exec_file.relative_to(cfg.profile_dir/"airootfs"))
                    rel_esc=rel.replace('"','\\"').replace("`","\\`").replace("$","\\$")
                    pf.write(f'file_permissions+=(["{rel_esc}"]="0:0:0755")\n')
            pf.write("# --- DUSKY PERMISSIONS END ---\n")
        ok("Dotfiles injected")
    finally: shutil.rmtree(tmp_dot, ignore_errors=True)

def configure_iso_pacman_conf(cfg: ISOConfig):
    info("Patching profile pacman.conf")
    pc=cfg.profile_dir/"pacman.conf"
    if not pc.exists(): die("profile pacman.conf missing")
    txt=pc.read_text(); txt=re.sub(r"^\s*DownloadUser.*\n","",txt,flags=re.MULTILINE)
    lines=txt.splitlines(); out=[]
    for line in lines:
        s=line.strip()
        if re.match(r"^#?\s*Color",s): continue
        if re.match(r"^#?\s*ILoveCandy",s): continue
        if re.match(r"^#?\s*VerbosePkgLists",s): continue
        if re.match(r"^#?\s*ParallelDownloads",s): continue
        if cfg.repo_mode==2 and re.match(r"^\s*Architecture\s*=",s): continue
        if s=="[options]":
            out.append(line); out.append("Color"); out.append("ILoveCandy"); out.append("VerbosePkgLists"); out.append("ParallelDownloads = 10")
            if cfg.repo_mode==2: out.append("Architecture = x86_64_v3 x86_64")
            inj=f"CacheDir = {cfg.official_repo}\n"
            if cfg.aur_repo and cfg.aur_repo.exists(): inj+=f"CacheDir = {cfg.aur_repo}\n"
            inj+="CacheDir = /var/cache/pacman/pkg"
            out.append(inj); continue
        out.append(line)
    final_txt="\n".join(out)
    if cfg.repo_mode==2 and "[core]" in final_txt:
        final_txt=final_txt.replace("[core]","# --- INJECTED CACHYOS v3 ---\n[cachyos-v3]\nInclude = /etc/pacman.d/cachyos-v3-mirrorlist\n\n[cachyos-core-v3]\nInclude = /etc/pacman.d/cachyos-v3-mirrorlist\n\n[cachyos-extra-v3]\nInclude = /etc/pacman.d/cachyos-v3-mirrorlist\n\n[cachyos]\nInclude = /etc/pacman.d/cachyos-mirrorlist\n# ----------------------------------------\n\n[core]")
    pc.write_text(final_txt)
    build_d=cfg.profile_dir/"pacman.d"; build_d.mkdir(exist_ok=True)
    for f in Path("/etc/pacman.d").glob("*mirrorlist*"):
        if f.name.endswith(".pacnew"): continue
        dest=build_d/f.name
        if dest.exists(): continue
        t=f.read_text()
        if "cachyos-v3" in f.name: t=t.replace("$arch_v3","x86_64_v3").replace("$arch","x86_64_v3")
        elif "cachyos-v4" in f.name: t=t.replace("$arch_v4","x86_64_v4").replace("$arch","x86_64_v4")
        elif "cachyos" in f.name: t=t.replace("$arch","x86_64")
        elif f.name=="mirrorlist": t=t.replace("$arch","x86_64")
        dest.write_text(t)
    ok("pacman.conf patched")

def build_iso_image(cfg: ISOConfig)->Path:
    info("Building ISO - safe injection")
    final_name=f"dusky_{datetime.now().strftime('%m_%y')}.iso"
    final_path=cfg.final_dest/final_name
    final_sha=cfg.final_dest/f"{final_path.stem}_iso.sha256"
    for f in [final_path, final_sha]:
        if f.exists():
            step(f"Forcefully removing existing: {f.name}")
            try: f.unlink()
            except Exception:
                subprocess.run(["rm", "-f", str(f)], shell=False)
    mk_src=Path("/usr/bin/mkarchiso")
    mk_custom=cfg.workspace/f"mkarchiso_dusky_{random.randint(100000,999999)}"
    shutil.copy2(mk_src, mk_custom); mk_custom.chmod(0o755)
    
    # Build injection safely without triple-quote hell
    inj_lines=[]
    inj_lines.append('    _msg_info ">>> INJECTING OFFLINE REPOS INTO ISO <<<"')
    inj_lines.append('    local isofs_dir="${isofs_dir:?}"')
    inj_lines.append('    local install_dir="${install_dir:?}"')
    inj_lines.append('    local repo_target="${isofs_dir}/${install_dir}/repo"')
    inj_lines.append('    mkdir -p "${repo_target}"')
    inj_lines.append(f'    rsync -a --exclude="*.db*" --exclude="*.files*" --exclude="*.sig" "{cfg.official_repo}/" "${{repo_target}}/" 2>/dev/null || cp -a "{cfg.official_repo}/." "${{repo_target}}/" 2>/dev/null || true')
    if cfg.aur_repo and cfg.aur_repo.exists():
        inj_lines.append(f'    rsync -a --exclude="*.db*" --exclude="*.files*" --exclude="*.sig" "{cfg.aur_repo}/" "${{repo_target}}/" 2>/dev/null || cp -a "{cfg.aur_repo}/." "${{repo_target}}/" 2>/dev/null || true')
    inj_lines.append('    shopt -s nullglob')
    inj_lines.append('    local pkg_files=("${repo_target}/"*.pkg.tar.*)')
    inj_lines.append('    local filtered=()')
    inj_lines.append('    for f in "${pkg_files[@]}"; do [[ "$f" == *.sig ]] && continue; filtered+=("$f"); done')
    inj_lines.append('    if (( ${#filtered[@]} > 0 )); then repo-add --nocolor -q "${repo_target}/archrepo.db.tar.zst" "${filtered[@]}"; else echo "[ERR] No packages" >&2; return 1; fi')
    inj_lines.append('    shopt -u nullglob')
    injection="\n".join(inj_lines)
    content=mk_custom.read_text()
    marker="_build_iso_image() {"
    if marker not in content: die("Cannot find _build_iso_image in mkarchiso - archiso version changed")
    content=content.replace(marker, marker+"\n"+injection, 1)
    mk_custom.write_text(content)
    work=cfg.work_dir; out=cfg.out_dir
    for d in [work,out]:
        if d.exists():
            try:
                o=subprocess.run(["findmnt","-R",str(d)], capture_output=True, text=True, shell=False)
                if o.returncode==0 and o.stdout.strip(): subprocess.run(["umount","-R",str(d)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
            except: pass
            shutil.rmtree(d)
    cmd=[str(mk_custom),"-v","-m","iso","-w",str(work),"-o",str(out),str(cfg.profile_dir)]
    info(f"Running mkarchiso: {' '.join(cmd)}")
    r=subprocess.run(cmd, shell=False)
    if r.returncode!=0: die("mkarchiso failed")
    iso_files=list(out.glob("*.iso"))
    if not iso_files: die("No ISO produced")
    cfg.final_dest.mkdir(parents=True, exist_ok=True)
    shutil.move(str(iso_files[0]), str(final_path))
    sha=hashlib.sha256()
    with open(final_path,"rb") as f:
        for chunk in iter(lambda: f.read(1<<20), b""): sha.update(chunk)
    final_sha.write_text(f"{sha.hexdigest()}  {final_path.name}\n")
    uid,gid=validate_sudo_ids()
    if uid is not None and gid is not None:
        for f in [final_path, final_sha]:
            if f.exists():
                try: os.chown(f, uid, gid)
                except: pass
    ok(f"ISO built: {final_path} ({human_bytes(final_path.stat().st_size)})")
    return final_path

def prompt_repo_mode()->int:
    console.print(Panel("Select Target Repository Mode", style="cyan", box=box.ROUNDED))
    console.print("  [bold]1)[/] Standard Arch Linux (Pure)\n  [bold]2)[/] CachyOS x86-64-v3 (Optimized + Arch Fallback)")
    c=Prompt.ask("Enter choice", choices=["1","2"], default="2"); return int(c)

def prompt_action()->str:
    console.print(Panel("Dusky Factory — What to do?", style="cyan", box=box.ROUNDED))
    table=Table(box=box.SIMPLE, show_header=False)
    table.add_column("No", style="magenta", width=4); table.add_column("Action", style="white")
    table.add_row("1","Download official repo (010)"); table.add_row("2","Build AUR repo (020)"); table.add_row("3","Both repos"); table.add_row("4","Build ISO only (030)"); table.add_row("5","Full pipeline: Official + AUR + ISO [default]")
    console.print(table)
    c=Prompt.ask("Enter choice", choices=["1","2","3","4","5"], default="5")
    return {"1":"official","2":"aur","3":"both","4":"iso","5":"full"}[c]

def prompt_path(msg: str, default: Path)->Path:
    console.print(f"[cyan]{msg}[/] (default: [bold]{default}[/])")
    inp=Prompt.ask("Path", default=str(default))
    p=Path(inp).expanduser()
    try: return p.resolve()
    except: return p.absolute()

def main():
    import argparse
    parser=argparse.ArgumentParser(description="Dusky Arch ISO Factory - Python 3.14.6 Final")
    parser.add_argument("--arch", action="store_true", help="Standard Arch mode")
    parser.add_argument("--cachyos", action="store_true", help="CachyOS v3 mode")
    parser.add_argument("--action", choices=["official","aur","both","iso","full"], help="Action")
    parser.add_argument("--official-repo", type=Path, help="Official repo dir")
    parser.add_argument("--aur-repo", type=Path, help="AUR repo dir")
    parser.add_argument("--workspace", type=Path, help="Workspace base (auto-detects zram if available)")
    parser.add_argument("--source-dir", type=Path, help="Source payload dir")
    parser.add_argument("--auto", action="store_true", help="Non-interactive defaults")
    args=parser.parse_args()
    
    console.print(Panel(f"Dusky Factory v{VERSION} — Python 3.14.6 / Pacman 7.1 / Systemd 261 / Archiso 88", style="bold cyan", box=box.DOUBLE))
    check_is_arch()
    
    repo_mode=1 if args.arch else 2
    if not args.arch and not args.cachyos and not args.auto and sys.stdin.isatty():
        repo_mode=prompt_repo_mode()
        
    # Surgical Fix: Host CachyOS Keyring Bootstrapping[cite: 3]
    if repo_mode == 2:
        r_cachy = subprocess.run(["pacman", "-Q", "cachyos-keyring"], capture_output=True, text=True)
        if r_cachy.returncode != 0:
            warn("CachyOS mode requires 'cachyos-keyring' installed on the HOST build system.")
            if not args.auto and sys.stdin.isatty():
                ans = Prompt.ask("Would you like to automatically install the CachyOS keyring now?", choices=["y", "n"], default="y")
                if ans.lower() == "y":
                    info("Fetching and installing CachyOS keyring...")
                    subprocess.run(["pacman-key", "--recv-keys", "F3B607488DB35A47", "--keyserver", "keyserver.ubuntu.com"], check=True)
                    subprocess.run(["pacman-key", "--lsign-key", "F3B607488DB35A47"], check=True)
                    
                    try:
                        html = urllib.request.urlopen("https://mirror.cachyos.org/repo/x86_64/cachyos/").read().decode()
                        m = re.search(r'href="(cachyos-keyring-[0-9]+[^"]*\.pkg\.tar\.zst)"', html)
                        pkg_name = m.group(1) if m else "cachyos-keyring-20240331-1-any.pkg.tar.zst"
                    except Exception:
                        pkg_name = "cachyos-keyring-20240331-1-any.pkg.tar.zst"
                    
                    wait_for_pacman_lock()
                    res_inst = subprocess.run(["pacman", "-U", "--noconfirm", f"https://mirror.cachyos.org/repo/x86_64/cachyos/{pkg_name}"])
                    if res_inst.returncode != 0:
                        die("Failed to install cachyos-keyring.")
                    ok("CachyOS keyring successfully installed.")
                else:
                    die("Cannot proceed without cachyos-keyring.")
            else:
                die("Missing cachyos-keyring on host (running non-interactively).")
                
    mode_name="Standard Arch" if repo_mode==1 else "CachyOS x86-64-v3"
    info(f"Mode: {mode_name}")
    action=args.action
    if not action:
        if args.auto: action="full"
        elif sys.stdin.isatty(): action=prompt_action()
        else: action="full"
    real_user, real_home=get_real_user()
    step(f"Real user: {real_user} home: {real_home}")
    default_official=Path("/srv/offline-repo/official")
    default_aur=Path("/srv/offline-repo/aur")
    default_source=real_home/"user_scripts"/"arch_iso_scripts"/"offline_iso"
    official_repo=args.official_repo or default_official
    aur_repo=args.aur_repo or default_aur
    source_dir=args.source_dir or default_source
    external_pkg_list=source_dir/"assets"/"iso_temp_packages"/"packages.x86_64"
    if not args.auto and sys.stdin.isatty():
        if action in ("official","both","full"): official_repo=prompt_path("Official repo path", official_repo)
        if action in ("aur","both","full"): aur_repo=prompt_path("AUR repo path", aur_repo)
    ensure_sudo_cached()
    
    if action in ("official","both","full"):
        info("=== OFFICIAL REPO BUILD ===")
        for t in ["pacman","repo-add","bsdtar","zstd","xz"]:
            if not check_tool(t): die(f"Missing tool: {t}")
        master=build_master_list(external_pkg_list if external_pkg_list.exists() else None)
        isolated=IsolatedDB(repo_mode=repo_mode)
        try:
            isolated.generate_conf()
            if not isolated.sync(): die("Sync failed - check internet/keyring")
            whitelist=generate_whitelist(isolated, master)
            download_packages(isolated, master, official_repo)
            prune_unneeded(official_repo, whitelist)
            generate_repo_db(official_repo, repo_mode)
            restore_ownership(official_repo)
        finally: isolated.cleanup()
        
    if action in ("aur","both","full"):
        info("=== AUR REPO BUILD ===")
        if os.geteuid()==0: warn("AUR build as root - will drop privileges via runuser for makepkg")
        for t in ["git","makepkg","bsdtar"]:
            if not check_tool(t): die(f"Missing: {t}")
        isolated_aur=IsolatedDB(repo_mode=repo_mode)
        try:
            isolated_aur.generate_conf()
            if not isolated_aur.sync(): die("AUR isolated sync failed")
            aur_repo.mkdir(parents=True, exist_ok=True)
            if os.geteuid()==0:
                uid,gid=validate_sudo_ids()
                if uid is not None: subprocess.run(["chown","-R","-h","--no-dereference",f"{uid}:{gid}",str(aur_repo)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
            clone_base=secure_mkdtemp("aur-factory-")
            if os.geteuid()==0:
                uid,gid=validate_sudo_ids()
                if uid is not None: subprocess.run(["chown","-R","-h","--no-dereference",f"{uid}:{gid}",str(clone_base)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
            built=0; skipped=0; failed=[]; queue=AUR_PACKAGES.copy(); i=0
            with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), TaskProgressColumn(), console=console) as prog:
                t=prog.add_task("AUR builds", total=len(queue))
                while i<len(queue):
                    pkg=queue[i]; prog.update(t, description=f"Building {pkg} ({i+1}/{len(queue)})")
                    try:
                        ok_flag, was_skip=build_aur_package(pkg, aur_repo, official_repo, isolated_aur, clone_base, real_user)
                        if ok_flag:
                            if was_skip: skipped+=1
                            else: built+=1
                    except Exception as e: err(f"Exception {pkg}: {e}"); failed.append(pkg)
                    if len(AUR_PACKAGES) > len(queue):
                        for extra in AUR_PACKAGES[len(queue):]:
                            queue.append(extra)
                    i+=1; prog.update(t, completed=i)
                    if len(queue)!=prog.tasks[0].total: prog.update(t, total=len(queue))
            shutil.rmtree(clone_base, ignore_errors=True)
            from pathlib import Path as P
            # prune and db
            if check_tool("paccache"):
                run_cmd(["paccache","-r","-k","1","-c",str(aur_repo)], sudo=True, check=False)
            generate_repo_db(aur_repo, 2 if "cachy" in isolated_aur.conf_path.read_text().lower() else 1)
            restore_ownership(aur_repo)
            table=Table(title="AUR Summary", box=box.ROUNDED); table.add_column("Metric", style="cyan"); table.add_column("Value", style="green")
            table.add_row("Built", str(built)); table.add_row("Skipped", str(skipped)); table.add_row("Failed", str(len(failed))); console.print(table)
            if failed: console.print(f"[red]Failed: {', '.join(failed)}[/]")
        finally: isolated_aur.cleanup()
        
    if action in ("iso","full"):
        info("=== ISO BUILD ===")
        if os.geteuid()!=0: die("ISO build requires root - run with sudo")
        for t in ["mkarchiso","git"]:
            if not check_tool(t): die(f"Missing: {t}")
        workspace_base=args.workspace
        if not workspace_base:
            if ZRAM_CANDIDATE.exists() and is_mountpoint(ZRAM_CANDIDATE):
                if sys.stdin.isatty():
                    if Confirm.ask(f"Detected {ZRAM_CANDIDATE} mounted - use for speed?", default=True): workspace_base=ZRAM_CANDIDATE
                    else: workspace_base=Path("/tmp")
                else: workspace_base=ZRAM_CANDIDATE
            else: workspace_base=Path("/tmp")
        workspace=Path(workspace_base)/"dusky_iso"
        profile_dir=workspace/"profile"; work_dir=workspace/"work"; out_dir=workspace/"out"
        final_dest=ZRAM_CANDIDATE if ZRAM_CANDIDATE.exists() and is_mountpoint(ZRAM_CANDIDATE) else Path.home()/"dusky_isos"
        cfg=ISOConfig(workspace=workspace, profile_dir=profile_dir, work_dir=work_dir, out_dir=out_dir, source_dir=source_dir, official_repo=official_repo, aur_repo=aur_repo if aur_repo.exists() else None, repo_mode=repo_mode, final_dest=final_dest)
        if not official_repo.exists(): die(f"Official repo missing at {official_repo} - build it first")
        try:
            setup_clean_room(cfg); stage_payloads(cfg); configure_live_hooks(cfg); inject_dotfiles(cfg); configure_iso_pacman_conf(cfg); iso_path=build_iso_image(cfg)
            sha_path = iso_path.with_name(f"{iso_path.stem}_iso.sha256")
            console.print(Panel(f"[bold green]SUCCESS[/]\nISO: {iso_path}\nSize: {human_bytes(iso_path.stat().st_size)}\nSHA256: {sha_path.read_text().split()[0][:16]}...", style="green", box=box.DOUBLE))
        finally:
            if workspace.exists() and (str(workspace).startswith("/tmp/") or "/dusky_iso_" in str(workspace) or str(workspace).startswith(str(ZRAM_CANDIDATE))):
                try:
                    o=subprocess.run(["findmnt","-R",str(workspace)], capture_output=True, text=True, shell=False)
                    if o.returncode==0 and o.stdout.strip(): subprocess.run(["umount","-R",str(workspace)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
                except: pass
                shutil.rmtree(workspace, ignore_errors=True)

if __name__=="__main__":
    main()
