#!/usr/bin/env python3
"""
SystemdBootEngine - Bleeding-Edge systemd-boot (systemd 255-262+ / Linux 6.x-7.x+) Engine.
Implements dynamic boot entry discovery, bidirectional clean kernel name translation,
universal automated entry synthesis for any installed/compiled Linux kernel,
multi-entry metadata management and direct renaming, atomic loader/entry updates,
and full systemd-boot EFI maintenance capabilities.
"""
lazy import fnmatch
lazy import json
import os
import re
lazy import threading
lazy import subprocess
from pathlib import Path
from typing import Any, Self

import sys
_DUSKY_TUI_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import BaseEngine
lazy from python.engines.cmdline import BridgedStateDict, CmdlineEngine
from python.shared.config_io import atomic_write, privileged_atomic_write, read_text, current_stamp, split_lines

# PEP 695 Bleeding-Edge Type Aliases
type KernelMeta = dict[str, Any]
type EntryMap = dict[str, KernelMeta]
type ChangeTuple = tuple[str, str, str, str]

# systemd-boot vendor GUID (Boot Loader Interface UAPI specification, stable)
_SD_BOOT_GUID = "4a67b082-0a4c-41cf-b6c7-440b29bb8c4f"
_EFI_VAR_DIR = Path("/sys/firmware/efi/efivars")
_EFIVAR_LOADER_ENTRIES  = _EFI_VAR_DIR / f"LoaderEntries-{_SD_BOOT_GUID}"
_EFIVAR_ENTRY_DEFAULT   = _EFI_VAR_DIR / f"LoaderEntryDefault-{_SD_BOOT_GUID}"
_EFIVAR_ENTRY_SELECTED  = _EFI_VAR_DIR / f"LoaderEntrySelected-{_SD_BOOT_GUID}"


# =============================================================================
# UNIVERSAL KERNEL & ENTRY NAME NORMALIZATION UTILITIES
# =============================================================================
def slugify_entry_key(clean_name: str) -> str:
    """Creates a deterministic, safe schema key slug for an entry title override."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", clean_name.lower()).strip("_")
    return f"title__{s}"


def clean_kernel_name(raw: str) -> str:
    """
    Transforms any raw kernel version, entry filename, or title into a clean,
    human-readable kernel name without hardcoded lists.
    Dynamically respects Arch Linux, Dusky custom builds, and standalone custom kernels.
    """
    if not raw:
        return "Arch Linux"
    s = str(raw).strip()
    if s.startswith("@"):
        return s

    is_fallback = bool(re.search(r"fallback|recovery", s, flags=re.IGNORECASE))

    # Strip file extensions (.conf, .preset, .img, .efi, etc.)
    for ext in (".conf", ".preset", ".img", ".efi", ".efi.extra", ".bak", ".old", ".uki"):
        if s.endswith(ext):
            s = s[:-len(ext)]

    # Handle parenthetical annotations: (linux-zen), (7.3.0-custom), etc.
    paren_match = re.search(r"\(([^)]+)\)", s)
    if paren_match:
        inner = paren_match.group(1).strip()
        if re.search(r"fallback|recovery", inner, flags=re.IGNORECASE):
            is_fallback = True
            s = re.sub(r"\s*\([^)]*\)", "", s).strip()
        elif not re.prefixmatch(r"^(?:default|x86_64|amd64)$", inner, flags=re.IGNORECASE):
            s = inner
        else:
            s = re.sub(r"\s*\([^)]*\)", "", s).strip()

    # Strip 32-hex machine-id prefix if present (Type #1 BLS: e.g. 22d434e8ca4f425482251d8a6ba8ddea-)
    s = re.sub(r"^[a-f0-9]{32}[-_]", "", s)

    # Strip leading vmlinuz- or initramfs- prefixes
    s = re.sub(r"^(?:vmlinuz|initramfs)[-_ ]+", "", s, flags=re.IGNORECASE)

    # Clean leftover fallback/recovery tokens
    s = re.sub(r"[-_ ]*(?:fallback|recovery)(?:[-_ ]*initramfs)?[-_ ]*", "", s, flags=re.IGNORECASE).strip()

    sl = s.lower().strip()

    if sl in ("", "linux", "arch", "archlinux", "arch-linux", "arch_linux", "default") or re.prefixmatch(r"^arch\d*(?:-\d+)?$", sl):
        title = "Arch Linux"
    elif sl.startswith(("dusky-", "dusky_")) or sl.startswith(("linux-dusky-", "linux_dusky_")):
        # Dusky custom kernel namespace (e.g. linux-dusky-gaming -> Dusky Gaming)
        sub = re.sub(r"^(?:linux[-_])?dusky[-_]", "", sl)
        sub_words = [w.upper() if len(w) <= 3 and w.isalpha() else w.title() for w in re.split(r"[-_ ]+", sub) if w]
        title = f"Dusky {' '.join(sub_words)}" if sub_words else "Dusky"
    elif sl.startswith(("linux-", "linux_")) or sl.startswith(("arch-", "arch_")):
        # Standard Arch flavor namespace (e.g. linux-zen -> Arch Linux Zen, linux-rt-lts -> Arch Linux RT LTS)
        sub = re.sub(r"^(?:linux|arch)[-_]", "", sl)
        sub = re.sub(r"^(?:linux|arch)[-_]", "", sub)
        if sub in ("", "linux", "arch", "default"):
            title = "Arch Linux"
        else:
            def _format_token(w: str) -> str:
                wl = w.lower()
                if wl in ("rt", "lts"):
                    return wl.upper()
                if wl == "cachyos":
                    return "CachyOS"
                if len(wl) <= 3 and wl.isalpha() and wl not in ("zen", "git", "rc", "exp"):
                    return wl.upper()
                return w.title()

            sub_words = [_format_token(w) for w in re.split(r"[-_ ]+", sub) if w]
            title = f"Arch Linux {' '.join(sub_words)}"
    else:
        # Standalone custom kernel (e.g. my-kernel-6.14 -> My Kernel 6.14)
        def _format_token(w: str) -> str:
            wl = w.lower()
            if wl in ("rt", "lts"):
                return wl.upper()
            if wl == "cachyos":
                return "CachyOS"
            return w.title()

        words = [_format_token(w) for w in re.split(r"[-_ ]+", s) if w]
        title = " ".join(words)

    if is_fallback and not title.endswith("(Fallback)"):
        title = f"{title} (Fallback)"
    return title


def kernel_flavor_stem(raw: str) -> str:
    """
    Extracts the base normalized flavor identifier token from any filename, kver,
    or title for strict 1:1 deduplication across discovery layers.
    """
    if not raw:
        return ""
    s = str(raw).strip().lower()
    s = re.sub(r"^[a-f0-9]{32}[-_]", "", s)
    for ext in (".conf", ".preset", ".img", ".efi", ".efi.extra", ".bak", ".old", ".uki"):
        if s.endswith(ext):
            s = s[:-len(ext)]
    while True:
        prev = s
        s = re.sub(r"^(?:vmlinuz|initramfs|linux|arch-linux|arch_linux|arch)[-_ ]+", "", s)
        if s == prev:
            break
    s = re.sub(r"^\d+\.\d+(?:\.\d+)?(?:-rc\d+)?(?:-git\d*)?-", "", s)
    s = re.sub(r"[-_ ]*(?:fallback|recovery)(?:[-_ ]*initramfs)?[-_ ]*", "", s).strip()
    return s


def check_microcode_hook() -> bool:
    """Checks whether the modern 'microcode' hook is active in /etc/mkinitcpio.conf."""
    mkinit_conf = Path("/etc/mkinitcpio.conf")
    if mkinit_conf.is_file():
        try:
            for line in mkinit_conf.read_text(encoding="utf-8", errors="replace").splitlines():
                line_s = line.strip()
                if line_s.startswith("HOOKS=") or line_s.startswith("HOOKS "):
                    return "microcode" in line_s
        except Exception:
            pass
    return False


def resolve_pkgbase_for_kernel(identifier: str) -> str:
    """
    Dynamically resolves the canonical pkgbase identifier for ANY kernel version,
    preset, entry filename, or clean name without hardcoded lists.
    """
    s = str(identifier).strip()
    if not s:
        return "linux"

    # 1. Strip file extensions if passed a filename (e.g. arch-linux-zen.conf -> arch-linux-zen)
    for ext in (".conf", ".preset", ".img", ".efi", ".efi.extra", ".bak", ".old", ".uki"):
        if s.endswith(ext):
            s = s[:-len(ext)]

    # 2. Normalize standard entry filename prefixes (e.g. arch-linux-zen -> linux-zen, arch-linux -> linux)
    sl = s.lower()
    if sl in ("", "arch-linux", "arch_linux", "linux", "arch"):
        return "linux"
    if sl.startswith(("arch-linux-", "arch_linux_")):
        s = f"linux-{s[11:]}"
    elif sl.startswith(("arch-", "arch_")):
        s = s[5:]

    # 1. Direct match in /usr/lib/modules/<identifier>/pkgbase (official Arch metadata)
    mod_dir = Path("/usr/lib/modules") / s
    if mod_dir.is_dir():
        pkgbase_file = mod_dir / "pkgbase"
        if pkgbase_file.is_file():
            try:
                p = pkgbase_file.read_text(encoding="utf-8", errors="replace").strip()
                if p:
                    return p
            except Exception:
                pass

    # 2. Check mkinitcpio presets in /etc/mkinitcpio.d
    for cand in (
        Path("/etc/mkinitcpio.d") / f"{s}.preset",
        Path("/etc/mkinitcpio.d") / f"linux-{s}.preset",
    ):
        if cand.is_file():
            return cand.stem

    # 3. Check /boot/vmlinuz-* files
    for cand in (
        Path("/boot") / f"vmlinuz-{s}",
        Path("/boot") / f"vmlinuz-linux-{s}",
    ):
        if cand.is_file():
            return cand.name.removeprefix("vmlinuz-")

    # 4. Handle clean UI names
    sl = s.lower()
    if sl in ("", "arch linux", "linux"):
        return "linux"
    if s.startswith("Arch Linux "):
        sub = s[11:].strip().lower().replace(" ", "-")
        if sub in ("", "linux"):
            return "linux"
        return f"linux-{sub}"
    if s.startswith("Dusky "):
        sub = s[6:].strip().lower().replace(" ", "-")
        if (Path("/boot") / f"vmlinuz-linux-dusky-{sub}").is_file():
            return f"linux-dusky-{sub}"
        return f"dusky-{sub}"

    # 5. Handle version strings like 7.3.0-rc2-xanmod1 or 7.2.11-zen1-1-zen
    m_ver = re.prefixmatch(r"^\d+\.\d+(?:\.\d+)?(?:-rc\d+)?(?:-git\d*)?-(.+)$", s)
    if m_ver:
        tail = m_ver.group(1)
        base_cand = re.sub(r"\d+-\d+(?:-.+)?$", "", tail).rstrip("-_")
        if not base_cand:
            base_cand = tail.split("-")[0]
        if base_cand in ("arch", "linux"):
            return "linux"
        if base_cand.startswith(("dusky-", "dusky_")):
            return base_cand
        if (Path("/boot") / f"vmlinuz-linux-{base_cand}").is_file():
            return f"linux-{base_cand}"
        if (Path("/boot") / f"vmlinuz-{base_cand}").is_file():
            return base_cand
        return f"linux-{base_cand}"

    return s.lower().replace(" ", "-")


def resolve_kernel_images(pkgbase: str, kver: str = "") -> tuple[str, str, str | None]:
    """
    Dynamically discovers the relative ESP paths for kernel image, primary initramfs,
    and optional fallback initramfs for ANY kernel, checking mkinitcpio presets and /boot.
    Returns:
        (linux_path, initrd_path, fallback_initrd_path_or_none)
    """
    # 1. Inspect mkinitcpio presets
    for preset_name in (pkgbase, f"linux-{pkgbase}", kver):
        if not preset_name:
            continue
        preset_file = Path("/etc/mkinitcpio.d") / f"{preset_name}.preset"
        if preset_file.is_file():
            try:
                kver_path = ""
                img_path = ""
                fb_path = ""
                for line in preset_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    line_s = line.strip()
                    if match := re.prefixmatch(r"^ALL_kver=[\"']?([^\"']+)[\"']?", line_s):
                        kver_path = match.group(1).strip()
                    elif match := re.prefixmatch(r"^default_image=[\"']?([^\"']+)[\"']?", line_s):
                        img_path = match.group(1).strip()
                    elif match := re.prefixmatch(r"^fallback_image=[\"']?([^\"']+)[\"']?", line_s):
                        fb_path = match.group(1).strip()

                if kver_path and img_path:
                    def _to_esp(p_str: str) -> str:
                        p = Path(p_str)
                        return f"/{p.name}"

                    linux_rel = _to_esp(kver_path)
                    img_rel = _to_esp(img_path)
                    fb_rel = None
                    if fb_path:
                        try:
                            if Path(fb_path).is_file():
                                fb_rel = _to_esp(fb_path)
                        except Exception:
                            fb_rel = _to_esp(fb_path)
                    return linux_rel, img_rel, fb_rel
            except Exception:
                pass

    # 2. Discover from /boot directly
    linux_rel = f"/vmlinuz-{pkgbase}"
    img_rel = f"/initramfs-{pkgbase}.img"
    fb_rel = None

    fallback_img = Path("/boot") / f"initramfs-{pkgbase}-fallback.img"
    try:
        if fallback_img.is_file():
            fb_rel = f"/{fallback_img.name}"
    except Exception:
        fb_rel = f"/{fallback_img.name}"

    return linux_rel, img_rel, fb_rel


def resolve_kver_for_pkgbase(pkgbase: str) -> str:
    """
    Dynamically discovers the kernel version (release string) for a given pkgbase
    by checking /usr/lib/modules, mkinitcpio presets, and pacman metadata.
    """
    clean_pb = pkgbase.strip().lower()

    # 1. Inspect /usr/lib/modules/*/pkgbase (authoritative on Arch Linux)
    modules_dir = Path("/usr/lib/modules")
    if modules_dir.is_dir():
        try:
            for d in sorted(modules_dir.iterdir(), reverse=True):
                if d.is_dir() and not d.name.startswith((".", "old", "tmp")):
                    pb_file = d / "pkgbase"
                    if pb_file.is_file():
                        try:
                            if pb_file.read_text(encoding="utf-8", errors="replace").strip().lower() == clean_pb:
                                return d.name
                            continue  # Authoritative pkgbase metadata names another kernel.
                        except Exception:
                            pass
                    if d.name.endswith(f"-{clean_pb}") or d.name.startswith(f"{clean_pb}-") or clean_pb in d.name:
                        return d.name
        except Exception:
            pass

    # 2. Inspect /etc/mkinitcpio.d/<preset>.preset
    for cand in (clean_pb, f"linux-{clean_pb}"):
        preset_file = Path("/etc/mkinitcpio.d") / f"{cand}.preset"
        if preset_file.is_file():
            try:
                for line in preset_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.strip().startswith("ALL_kver="):
                        val = line.split("=", 1)[1].strip("\"' ")
                        # ALL_kver commonly names a binary vmlinuz image. It is
                        # not a text file containing the release string.
                        if re.fullmatch(r'[0-9]+\.[0-9]+[^/\s]*', val):
                            return val
            except Exception:
                pass

    return ""


def canonical_entry_filename(pkgbase: str) -> str:
    """Determines standard BLS Type #1 entry filename from any pkgbase."""
    p = pkgbase.strip().lower()
    if p in ("linux", "arch"):
        return "arch-linux.conf"
    if p.startswith("arch-"):
        return f"{p}.conf"
    if p.startswith("linux-"):
        return f"arch-{p}.conf"
    if p.startswith("dusky-"):
        return f"{p}.conf"
    return f"arch-{p}.conf"


def canonical_fallback_filename(pkgbase: str) -> str:
    """Determines standard BLS Type #1 fallback entry filename from any pkgbase."""
    base = canonical_entry_filename(pkgbase)
    return base.replace(".conf", "-fallback.conf")


def canonical_entry_title(clean_name: str, pkgbase: str) -> str:
    """Generates standard BLS Type #1 title line for any kernel."""
    p = pkgbase.strip().lower()
    if p in ("linux", "arch"):
        return "Arch Linux (linux)"
    if p.startswith("linux-"):
        return f"Arch Linux ({p})"
    if "dusky" in p:
        return clean_name
    return f"Arch Linux ({p})"


def canonical_sort_key(pkgbase: str) -> str:
    """Generates BLS Type #1 sort-key string for any kernel."""
    p = pkgbase.strip().lower()
    if "dusky" in p:
        suffix = p.removeprefix("linux-").removeprefix("dusky-")
        return f"dusky-{suffix}" if suffix else "dusky"
    if p in ("linux", "arch"):
        return "arch"
    if p.startswith("linux-"):
        return f"arch-{p.removeprefix('linux-')}"
    if p.startswith("arch-"):
        return p
    return f"arch-{p}"


def get_baseline_options(esp_entry_dirs: list[Path]) -> str:
    """
    Extracts proven root filesystem, encryption, and kernel parameters from existing
    entries or /proc/cmdline, ensuring 100% forward-compatibility on new kernels.
    """
    for edir in esp_entry_dirs:
        if edir.is_dir():
            candidates = [edir / "arch-linux.conf"] + sorted(edir.glob("*.conf"))
            for cand in candidates:
                if cand.is_file() and "fallback" not in cand.name.lower():
                    try:
                        for line in cand.read_text(encoding="utf-8", errors="replace").splitlines():
                            line_s = line.strip()
                            if line_s.startswith(("options ", "options\t")):
                                opts = line_s.split(None, 1)[1].strip()
                                if opts:
                                    return opts
                    except Exception:
                        pass

    # Fallback to /proc/cmdline (filtered of bootloader/initrd artifacts)
    try:
        proc_cmdline = Path("/proc/cmdline")
        if proc_cmdline.is_file():
            raw = proc_cmdline.read_text(encoding="utf-8", errors="replace").strip()
            tokens = []
            for tok in raw.split():
                k = tok.split("=", 1)[0]
                if k not in ("BOOT_IMAGE", "initrd", "initrdefi"):
                    tokens.append(tok)
            if tokens:
                return " ".join(tokens)
    except Exception:
        pass

    return "rw quiet splash loglevel=3 systemd.show_status=auto rd.udev.log_level=3 vt.global_cursor_default=0"


def get_baseline_fallback_options(esp_entry_dirs: list[Path], baseline_opts: str) -> str:
    """Derives safe fallback recovery options (stripping splash/quiet)."""
    for edir in esp_entry_dirs:
        cand = edir / "arch-linux-fallback.conf"
        if cand.is_file():
            try:
                for line in cand.read_text(encoding="utf-8", errors="replace").splitlines():
                    line_s = line.strip()
                    if line_s.startswith(("options ", "options\t")):
                        opts = line_s.split(None, 1)[1].strip()
                        if opts:
                            return opts
            except Exception:
                pass
    tokens = [t for t in baseline_opts.split() if t not in ("quiet", "splash")]
    return " ".join(tokens)


# =============================================================================
# DYNAMIC KERNEL AND ENTRY DISCOVERY
# =============================================================================
def discover_all_kernels_and_entries() -> EntryMap:
    """
    Dynamically scans ESP boot loader entries, EFI variable history, installed modules
    in /usr/lib/modules, mkinitcpio presets in /etc/mkinitcpio.d, and boot kernels in /boot.
    
    Guarantees strict deduplication: exactly ONE canonical entry per physical boot option / kernel.
    Automatically assigns canonical BLS filenames and paths to unconfigured kernels so they
    can be synthesized and managed seamlessly.
    """
    canonical_entries: dict[str, dict[str, Any]] = {}

    esp_entry_dirs = [
        Path("/boot/loader/entries"),
        Path("/efi/loader/entries"),
        Path("/boot/efi/loader/entries"),
        Path("/loader/entries"),
    ]
    primary_esp_entries_dir = esp_entry_dirs[0]
    for cand in esp_entry_dirs:
        if cand.is_dir():
            primary_esp_entries_dir = cand
            break

    # 1. Query bootctl JSON (systemd 255-262+ JSON output)
    try:
        res = subprocess.run(["bootctl", "list", "--json=short"], capture_output=True, text=True, timeout=2)
        if res.returncode != 0:
            res = subprocess.run(["sudo", "-n", "bootctl", "list", "--json=short"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and res.stdout.strip():
            entries_json = json.loads(res.stdout)
            if isinstance(entries_json, list):
                for entry in entries_json:
                    entry_type = entry.get("type", "")
                    entry_id = entry.get("id", "")
                    title_raw = entry.get("title", "")

                    if entry_type == "Automatic" or entry_id.startswith("auto-"):
                        continue
                    if any(sub in title_raw.lower() for sub in ("reboot into firmware", "efi default loader", "windows boot manager")):
                        continue

                    src = entry.get("source", "") or entry.get("path", "")
                    ver_raw = entry.get("version", "")
                    filename = Path(src).name if src and src != "esp" else (f"{entry_id}.conf" if entry_id and not entry_id.endswith(".conf") else entry_id)
                    key = filename or entry_id

                    canonical_entries[key] = {
                        "entry_id": entry_id,
                        "entry_file": filename if filename != "esp" else entry_id,
                        "entry_path": src if src and src != "esp" else "",
                        "kver": ver_raw,
                        "title": title_raw,
                        "linux": entry.get("linux", ""),
                        "initrd": entry.get("initrd", []),
                        "source": "bootctl_json",
                        "needs_synthesis": False,
                    }
    except Exception:
        pass

    # Direct filesystem scan of ESP entry files (*.conf)
    for entries_dir in esp_entry_dirs:
        if entries_dir.is_dir():
            try:
                for conf in sorted(entries_dir.glob("*.conf")):
                    if not conf.is_file():
                        continue
                    title = ""
                    version = ""
                    linux = ""
                    initrd = []
                    try:
                        for line in conf.read_text(encoding="utf-8", errors="replace").splitlines():
                            line_s = line.strip()
                            if line_s.startswith(("title ", "title\t")):
                                title = line_s.split(None, 1)[1].strip()
                            elif line_s.startswith(("version ", "version\t")):
                                version = line_s.split(None, 1)[1].strip()
                            elif line_s.startswith(("linux ", "linux\t")):
                                linux = line_s.split(None, 1)[1].strip()
                            elif line_s.startswith(("initrd ", "initrd\t")):
                                initrd.append(line_s.split(None, 1)[1].strip())
                    except Exception:
                        pass

                    key = conf.name
                    if key not in canonical_entries:
                        canonical_entries[key] = {
                            "entry_id": conf.stem if not conf.name.endswith(".conf") else conf.name,
                            "entry_file": conf.name,
                            "entry_path": str(conf),
                            "kver": version,
                            "title": title,
                            "linux": linux,
                            "initrd": initrd,
                            "source": "esp_conf",
                            "needs_synthesis": False,
                        }
                    else:
                        canonical_entries[key]["entry_path"] = str(conf)
                        if title and not canonical_entries[key].get("title"):
                            canonical_entries[key]["title"] = title
                        if version and not canonical_entries[key].get("kver"):
                            canonical_entries[key]["kver"] = version
            except Exception:
                pass

    # EFI variable fallback (LoaderEntries is world-readable even without root privileges)
    if _EFIVAR_LOADER_ENTRIES.is_file():
        try:
            raw_bytes = _EFIVAR_LOADER_ENTRIES.read_bytes()
            efi_str = raw_bytes[4:].decode("utf-16-le").rstrip("\x00")
            for entry_id in efi_str.split("\x00"):
                entry_id = entry_id.strip()
                if not entry_id or entry_id.startswith("auto-"):
                    continue
                filename = entry_id if entry_id.endswith(".conf") else f"{entry_id}.conf"
                if filename not in canonical_entries and entry_id not in canonical_entries:
                    canonical_entries[filename] = {
                        "entry_id": entry_id,
                        "entry_file": filename,
                        "entry_path": "",
                        "kver": "",
                        "title": "",
                        "linux": "",
                        "initrd": [],
                        "source": "efi_var",
                        "needs_synthesis": False,
                    }
        except Exception:
            pass

    # Build canonical clean results mapping
    results: EntryMap = {}
    covered_stems: set[str] = set()

    for key, data in canonical_entries.items():
        title_raw = data.get("title", "")
        entry_file = data.get("entry_file", "") or key
        kver = data.get("kver", "")

        is_fallback = bool(re.search(r"fallback|recovery", f"{title_raw} {entry_file} {kver}", flags=re.IGNORECASE))

        # Derive clean display name
        if title_raw and not re.prefixmatch(r"^(?:arch linux|linux)$", title_raw.strip(), flags=re.IGNORECASE):
            clean = clean_kernel_name(title_raw)
        else:
            clean = clean_kernel_name(entry_file or kver or title_raw)

        if is_fallback and not clean.endswith("(Fallback)"):
            clean = f"{clean} (Fallback)"

        # Disambiguate if different files resolve to the same clean name
        unique_clean = clean
        counter = 2
        while unique_clean in results and results[unique_clean].get("entry_file") != entry_file:
            unique_clean = f"{clean} #{counter}"
            counter += 1

        hint = f"Boot entry: {entry_file}" if entry_file else (f"Version: {kver}" if kver else f"Kernel {unique_clean}")
        results[unique_clean] = {
            "clean_name": unique_clean,
            "entry_file": entry_file,
            "entry_path": data.get("entry_path", ""),
            "kver": kver,
            "pkgbase": resolve_pkgbase_for_kernel(entry_file),
            "title": title_raw or unique_clean,
            "hint": hint,
            "source": data.get("source", "bls"),
            "needs_synthesis": False,
        }

        stem = kernel_flavor_stem(entry_file)
        if stem:
            covered_stems.add(stem)
        if kver:
            k_stem = kernel_flavor_stem(kver)
            if k_stem:
                covered_stems.add(k_stem)

    # 2. Scan /usr/lib/modules for installed kernel packages NOT already represented
    modules_dir = Path("/usr/lib/modules")
    if modules_dir.is_dir():
        try:
            for entry in sorted(modules_dir.iterdir()):
                if entry.is_dir() and not entry.name.startswith((".", "old", "tmp")):
                    stem = kernel_flavor_stem(entry.name)
                    clean = clean_kernel_name(entry.name)
                    pkgbase = resolve_pkgbase_for_kernel(entry.name)
                    canonical_conf = canonical_entry_filename(pkgbase)

                    if clean not in results and stem not in covered_stems and canonical_conf not in canonical_entries:
                        target_entry_path = primary_esp_entries_dir / canonical_conf
                        is_on_disk = target_entry_path.is_file()
                        results[clean] = {
                            "clean_name": clean,
                            "entry_file": canonical_conf,
                            "entry_path": str(target_entry_path),
                            "kver": entry.name,
                            "pkgbase": pkgbase,
                            "title": canonical_entry_title(clean, pkgbase),
                            "hint": f"Installed Kernel: {pkgbase} ({entry.name})" + ("" if is_on_disk else " (Entry auto-generates on save)"),
                            "source": "modules",
                            "needs_synthesis": not is_on_disk,
                        }
                        if stem:
                            covered_stems.add(stem)
        except Exception:
            pass

    # 3. Scan /etc/mkinitcpio.d for presets NOT already represented
    preset_dir = Path("/etc/mkinitcpio.d")
    if preset_dir.is_dir():
        try:
            for preset in sorted(preset_dir.glob("*.preset")):
                stem = kernel_flavor_stem(preset.name)
                clean = clean_kernel_name(preset.name)
                pkgbase = preset.stem
                canonical_conf = canonical_entry_filename(pkgbase)

                if clean not in results and stem not in covered_stems and canonical_conf not in canonical_entries:
                    target_entry_path = primary_esp_entries_dir / canonical_conf
                    is_on_disk = target_entry_path.is_file()
                    results[clean] = {
                        "clean_name": clean,
                        "entry_file": canonical_conf,
                        "entry_path": str(target_entry_path),
                        "kver": "",
                        "pkgbase": pkgbase,
                        "title": canonical_entry_title(clean, pkgbase),
                        "hint": f"Mkinitcpio Preset: {preset.name}" + ("" if is_on_disk else " (Entry auto-generates on save)"),
                        "source": "mkinitcpio",
                        "needs_synthesis": not is_on_disk,
                    }
                    if stem:
                        covered_stems.add(stem)
        except Exception:
            pass

    # 4. Fallback check for standard kernels in /boot NOT already represented
    boot_dir = Path("/boot")
    if boot_dir.is_dir():
        try:
            for vmlinuz in sorted(boot_dir.glob("vmlinuz-*")):
                stem = kernel_flavor_stem(vmlinuz.name)
                clean = clean_kernel_name(vmlinuz.name)
                pkgbase = vmlinuz.name.removeprefix("vmlinuz-")
                canonical_conf = canonical_entry_filename(pkgbase)

                if clean not in results and stem not in covered_stems and canonical_conf not in canonical_entries:
                    target_entry_path = primary_esp_entries_dir / canonical_conf
                    is_on_disk = target_entry_path.is_file()
                    results[clean] = {
                        "clean_name": clean,
                        "entry_file": canonical_conf,
                        "entry_path": str(target_entry_path),
                        "kver": "",
                        "pkgbase": pkgbase,
                        "title": canonical_entry_title(clean, pkgbase),
                        "hint": f"Kernel Image: {vmlinuz.name}" + ("" if is_on_disk else " (Entry auto-generates on save)"),
                        "source": "boot_image",
                        "needs_synthesis": not is_on_disk,
                    }
                    if stem:
                        covered_stems.add(stem)
        except Exception:
            pass

    # Offer the stock kernel only when its actual image exists.
    if "Arch Linux" not in results and (Path("/boot") / "vmlinuz-linux").is_file():
        results["Arch Linux"] = {
            "clean_name": "Arch Linux",
            "entry_file": "arch-linux.conf",
            "entry_path": str(primary_esp_entries_dir / "arch-linux.conf"),
            "kver": "",
            "pkgbase": "linux",
            "title": "Arch Linux (linux)",
            "hint": "Stock Arch Linux kernel (/boot/vmlinuz-linux)",
            "source": "stock_default",
            "needs_synthesis": not (primary_esp_entries_dir / "arch-linux.conf").is_file(),
        }

    return results


# =============================================================================
# SYSTEMD-BOOT ENGINE IMPLEMENTATION
# =============================================================================
class SystemdBootEngine(BaseEngine):
    """
    Intelligent engine for systemd-boot (systemd 255 - 262+ / Linux 6.x - 7.x+).
    
    Manages:
    - DEFAULT scope: Kernel command-line parameters in options line of active entry .conf
    - ENTRY scope: Active entry metadata (title, sort-key, version, linux, initrd, architecture)
    - ENTRY_OVERRIDE scope: Direct per-entry title modification for ANY installed kernel entry
    - LOADER scope: Global bootloader settings in loader.conf (default, timeout, console-mode, editor, auto-entries, etc.)
    - UNIVERSAL SYNTHESIS: Dynamically discovers and synthesizes valid BLS Type #1 .conf files
      for ANY Linux kernel installed via pacman, AUR, or custom compiler pipelines.
    """

    def __init__(self, config_path: str = "") -> None:
        self.loader_conf_path: Path = self._resolve_loader_path()
        self._target_override: str = ""
        self._cached_entries_map: EntryMap = {}
        self._explicit_config_path: str = config_path
        self.config_path: Path = self._resolve_config_path(config_path)
        self.cache: BridgedStateDict = BridgedStateDict()
        self.file_mtime_ns: int = 0
        self.loader_mtime_ns: int = 0
        self._snapshots = {}
        self._lock = threading.RLock()

    @staticmethod
    def _resolve_loader_path() -> Path:
        for cand in [
            Path("/boot/loader/loader.conf"),
            Path("/efi/loader/loader.conf"),
            Path("/boot/efi/loader/loader.conf"),
        ]:
            if cand.exists():
                return cand
        return Path("/boot/loader/loader.conf")

    def _resolve_config_path(self, config_path: str = "") -> Path:
        if config_path:
            p = Path(config_path).expanduser().resolve()
            return p

        candidate_dirs = [
            self.loader_conf_path.parent / "entries",
            Path("/boot/loader/entries"),
            Path("/efi/loader/entries"),
            Path("/boot/efi/loader/entries"),
            Path("/loader/entries"),
        ]

        # 1. If explicit target_entry override was chosen in Tab 5
        if self._target_override and not self._target_override.startswith("Auto"):
            entry_path = self.get_entry_path_for_name(self._target_override)
            if entry_path:
                return entry_path

        # 2. Check candidate entry directories for active/default conf
        for entries_dir in candidate_dirs:
            if not entries_dir.is_dir():
                continue

            # Look for active/default conf referenced in loader.conf
            if self.loader_conf_path.exists():
                try:
                    for line in self.loader_conf_path.read_text(encoding="utf-8", errors="replace").splitlines():
                        line_s = line.strip()
                        if line_s.startswith(("default ", "default\t")):
                            def_val = line_s.split(None, 1)[1].strip()
                            if def_val and not def_val.startswith("@"):
                                cand = entries_dir / def_val
                                if cand.is_file():
                                    return cand
                                if not def_val.endswith(".conf"):
                                    cand_conf = entries_dir / f"{def_val}.conf"
                                    if cand_conf.is_file():
                                        return cand_conf
                                for conf_path in entries_dir.glob("*.conf"):
                                    if fnmatch.fnmatch(conf_path.name.lower(), def_val.lower()) or fnmatch.fnmatch(conf_path.stem.lower(), def_val.lower()):
                                        return conf_path
                except Exception:
                    pass

            # Check EFI LoaderEntrySelected / LoaderEntryDefault variable if default was @saved
            for efivar in (_EFIVAR_ENTRY_SELECTED, _EFIVAR_ENTRY_DEFAULT):
                if efivar.is_file():
                    try:
                        raw_bytes = efivar.read_bytes()
                        efi_entry = raw_bytes[4:].decode("utf-16-le").rstrip("\x00").strip()
                        if efi_entry and not efi_entry.startswith("auto-"):
                            cand = entries_dir / (efi_entry if efi_entry.endswith(".conf") else f"{efi_entry}.conf")
                            if cand.is_file():
                                return cand
                    except Exception:
                        pass

            # Look for custom entries first, then stock arch, then first available .conf
            all_confs = sorted(entries_dir.glob("*.conf"))
            if all_confs:
                for cand in all_confs:
                    if "dusky" in cand.name.lower():
                        return cand
                for cand in all_confs:
                    if "arch" in cand.name.lower() and "fallback" not in cand.name.lower():
                        return cand
                return all_confs[0]

        return self.loader_conf_path.parent / "entries" / "arch-linux.conf"

    @classmethod
    def from_path(cls, config_path: str) -> Self:
        return cls(config_path)

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def get_entries_map(self) -> EntryMap:
        if not self._cached_entries_map:
            self._cached_entries_map = discover_all_kernels_and_entries()
        return self._cached_entries_map

    def get_entry_path_for_name(self, clean_name: str) -> Path | None:
        """Resolves a clean kernel name to its absolute Path on disk or canonical target path."""
        entries = self.get_entries_map()
        if clean_name in entries:
            src = entries[clean_name].get("entry_path") or entries[clean_name].get("entry_file")
            if src:
                p = Path(src)
                if not p.is_absolute():
                    p = self.loader_conf_path.parent / "entries" / p
                if p.is_file():
                    return p
                if str(p).endswith(".conf"):
                    return p

        stem_target = kernel_flavor_stem(clean_name)
        pkgbase_target = resolve_pkgbase_for_kernel(clean_name)

        for entries_dir in [
            self.loader_conf_path.parent / "entries",
            Path("/boot/loader/entries"),
            Path("/efi/loader/entries"),
            Path("/boot/efi/loader/entries"),
            Path("/loader/entries"),
        ]:
            if entries_dir.is_dir():
                try:
                    for conf in entries_dir.glob("*.conf"):
                        if clean_kernel_name(conf.name) == clean_name:
                            return conf
                        if stem_target and kernel_flavor_stem(conf.name) == stem_target:
                            return conf
                        try:
                            for line in conf.read_text(encoding="utf-8", errors="replace").splitlines():
                                line_s = line.strip()
                                if line_s.startswith(("title ", "title\t")):
                                    t = line_s.split(None, 1)[1].strip()
                                    if clean_kernel_name(t) == clean_name:
                                        return conf
                                elif line_s.startswith(("linux ", "linux\t")):
                                    lx = line_s.split(None, 1)[1].strip()
                                    if f"vmlinuz-{pkgbase_target}" in lx:
                                        return conf
                        except Exception:
                            pass
                except Exception:
                    pass

        # Canonical target fallback
        entries_dir = self.loader_conf_path.parent / "entries"
        return entries_dir / canonical_entry_filename(pkgbase_target)

    def map_raw_to_clean(self, raw_val: str) -> str:
        """Translates a raw loader.conf default value to its clean UI name."""
        if not raw_val or raw_val.startswith("@") or any(c in raw_val for c in "*?["):
            return raw_val

        entries = self.get_entries_map()

        for clean_name, meta in entries.items():
            entry_file = meta.get("entry_file", "")
            if entry_file and (raw_val == entry_file or raw_val == Path(entry_file).stem):
                return clean_name

        clean = clean_kernel_name(raw_val)
        if clean in entries:
            return clean

        raw_stem = kernel_flavor_stem(raw_val)
        if raw_stem:
            for clean_name, meta in entries.items():
                if kernel_flavor_stem(clean_name) == raw_stem or kernel_flavor_stem(meta.get("entry_file", "")) == raw_stem:
                    return clean_name

        pattern = raw_val.strip("*").lower()
        if pattern:
            for clean_name in entries:
                if pattern in clean_name.lower():
                    return clean_name

        return clean

    def map_clean_to_raw(self, clean_name: str) -> str:
        """Translates a clean UI selection to the exact loader.conf entry format."""
        if not clean_name or clean_name.startswith("@") or any(c in clean_name for c in "*?["):
            return clean_name

        entries = self.get_entries_map()

        if clean_name in entries:
            meta = entries[clean_name]
            entry_file = meta.get("entry_file", "")
            if entry_file:
                return entry_file

        p = self.get_entry_path_for_name(clean_name)
        if p and p.is_file():
            return p.name

        cl = clean_name.lower()
        match cl:
            case "arch linux":
                return "arch-linux.conf"
            case "arch linux (fallback)":
                return "arch-linux-fallback.conf"
            case _:
                pkgbase = resolve_pkgbase_for_kernel(clean_name)
                return canonical_entry_filename(pkgbase)

    def synthesize_entry_for_kernel(self, clean_name: str, custom_title: str = "", custom_options: str = "") -> tuple[bool, Path | None, str]:
        """
        Dynamically synthesizes a full BLS Type #1 entry file for ANY kernel package.
        Resolves vmlinuz, initramfs, microcode, and root topology dynamically.
        """
        entries = self.get_entries_map()
        meta = entries.get(clean_name, {})
        pkgbase = meta.get("pkgbase") or resolve_pkgbase_for_kernel(clean_name)
        kver = meta.get("kver") or resolve_kver_for_pkgbase(pkgbase)

        entries_dir = self.loader_conf_path.parent / "entries"
        entries_dir.mkdir(parents=True, exist_ok=True)
        entry_file = meta.get("entry_file") or canonical_entry_filename(pkgbase)
        entry_path = entries_dir / entry_file

        title = custom_title or meta.get("title") or canonical_entry_title(clean_name, pkgbase)
        sort_key = canonical_sort_key(pkgbase)

        linux_rel, img_rel, fb_rel = resolve_kernel_images(pkgbase, kver)

        lines = [
            f"title      {title}",
        ]
        if kver:
            lines.append(f"version    {kver}")
        lines.extend([
            f"sort-key   {sort_key}",
            f"linux      {linux_rel}",
        ])

        if not check_microcode_hook():
            for img in ("intel-ucode.img", "amd-ucode.img"):
                if (Path("/boot") / img).is_file():
                    lines.append(f"initrd     /{img}")

        lines.append(f"initrd     {img_rel}")

        opts = custom_options or get_baseline_options([entries_dir, Path("/boot/loader/entries")])
        lines.append(f"options    {opts}")

        content = "\n".join(lines) + "\n"
        ok, msg = self._atomic_write(entry_path, content)
        if not ok:
            return False, entry_path, msg

        # Synthesize fallback entry if fallback initramfs exists on disk
        if fb_rel:
            fb_file = canonical_fallback_filename(pkgbase)
            fb_path = entries_dir / fb_file
            fb_title = f"{title} (Fallback Recovery)"
            fb_opts = get_baseline_fallback_options([entries_dir, Path("/boot/loader/entries")], opts)
            fb_lines = [
                f"title      {fb_title}",
            ]
            if kver:
                fb_lines.append(f"version    {kver}")
            fb_lines.extend([
                f"sort-key   {sort_key}",
                f"linux      {linux_rel}",
            ])
            if not check_microcode_hook():
                for img in ("intel-ucode.img", "amd-ucode.img"):
                    if (Path("/boot") / img).is_file():
                        fb_lines.append(f"initrd     /{img}")
            fb_lines.append(f"initrd     {fb_rel}")
            fb_lines.append(f"options    {fb_opts}")
            ok, msg = self._atomic_write(fb_path, "\n".join(fb_lines) + "\n")
            if not ok:
                return False, entry_path, f"Primary entry saved; fallback failed: {msg}"

        self._cached_entries_map = {}
        return True, entry_path, f"Synthesized BLS entry: {entry_file}"

    def ensure_entry_exists(self, clean_name: str) -> tuple[bool, Path | None, str]:
        """Ensures that a physical .conf entry exists on disk for the given clean kernel name."""
        if not clean_name or clean_name.startswith("@") or any(c in clean_name for c in "*?["):
            return True, None, "Special target"

        p = self.get_entry_path_for_name(clean_name)
        if p and p.is_file():
            return True, p, "Entry already exists"

        return self.synthesize_entry_for_kernel(clean_name)

    def auto_synthesize_all_missing_entries(self) -> tuple[bool, list[str]]:
        """Scans for any installed kernels lacking BLS entries and generates them."""
        entries = self.get_entries_map()
        synthesized: list[str] = []
        all_ok = True
        for clean_name, meta in entries.items():
            if clean_name.startswith("@"):
                continue
            if meta.get("needs_synthesis"):
                ok, _, _ = self.synthesize_entry_for_kernel(clean_name)
                if ok:
                    synthesized.append(clean_name)
                else:
                    all_ok = False
        return all_ok, synthesized

    def load_state(self) -> dict[str, Any]:
        with self._lock:
            return self._load_state()

    def _read_loaded(self, path):
        text, snapshot = read_text(path)
        self._snapshots[path] = snapshot
        return text

    def _load_state(self) -> dict[str, Any]:
        self._snapshots = {}
        self.cache = BridgedStateDict()
        self._cached_entries_map = discover_all_kernels_and_entries()

        # 1. Load global loader.conf (LOADER scope)
        raw_default = ""
        clean_default = "Arch Linux"
        if self.loader_conf_path.exists():
            try:
                content = self._read_loaded(self.loader_conf_path)
                self.loader_mtime_ns = self._snapshots[self.loader_conf_path][3] if self._snapshots[self.loader_conf_path] else 0
                for line in split_lines(content):
                    line_clean = line.strip()
                    if line_clean and not line_clean.startswith("#") and (" " in line_clean or "\t" in line_clean):
                        k, v = line_clean.split(None, 1)
                        raw_val = v.strip()
                        if k == "default":
                            raw_default = raw_val
                            clean_default = self.map_raw_to_clean(raw_val)
                            self.cache["LOADER/default"] = clean_default
                            self.cache["LOADER/default_raw"] = raw_val
                        else:
                            self.cache[f"LOADER/{k}"] = raw_val
            except OSError:
                pass

        # Cross-check against the LoaderEntryDefault EFI variable
        if _EFIVAR_ENTRY_DEFAULT.is_file():
            try:
                efi_bytes = _EFIVAR_ENTRY_DEFAULT.read_bytes()
                efi_default = efi_bytes[4:].decode("utf-16-le").rstrip("\x00").strip()
                if efi_default and not efi_default.startswith("auto-"):
                    efi_clean = self.map_raw_to_clean(efi_default)
                    raw_default = efi_default
                    clean_default = efi_clean
                    self.cache["LOADER/default"] = efi_clean
                    self.cache["LOADER/default_raw"] = efi_default
            except Exception:
                pass

        # Target entry selection state (defaults to Auto)
        self.cache["LOADER/target_entry"] = self._target_override or "Auto (Follows Default Kernel)"

        # 2. Load ALL discovered entry files into ENTRY_OVERRIDE scope
        for clean_name, meta in self._cached_entries_map.items():
            slug = slugify_entry_key(clean_name)
            p = self.get_entry_path_for_name(clean_name)
            title_in_file = clean_name
            if p and p.is_file():
                try:
                    for line in split_lines(self._read_loaded(p)):
                        line_s = line.strip()
                        if line_s.startswith(("title ", "title\t")):
                            title_in_file = line_s.split(None, 1)[1].strip()
                            break
                except Exception:
                    pass
            self.cache[f"ENTRY_OVERRIDE/{slug}"] = title_in_file

        # 3. Resolve active entry config file
        self.config_path = self._resolve_config_path(self._explicit_config_path)

        # 4. Load active entry config (DEFAULT cmdline parameters and ENTRY metadata)
        if self.config_path.exists():
            try:
                content = self._read_loaded(self.config_path)
                self.file_mtime_ns = self._snapshots[self.config_path][3] if self._snapshots[self.config_path] else 0

                initrd_count = 0
                option_lines = []
                for line in split_lines(content):
                    line_clean = line.strip()
                    if not line_clean or line_clean.startswith("#"):
                        continue

                    if match := re.prefixmatch(r"^([ \t]*)options([ \t]+)(.*)$", line):
                        option_lines.append(match.group(3))
                    elif " " in line_clean or "\t" in line_clean:
                        k, v = line_clean.split(None, 1)
                        if k == "initrd":
                            initrd_count += 1
                            self.cache[f"ENTRY/initrd:{initrd_count}"] = v.strip()
                            self.cache["ENTRY/initrd"] = v.strip()
                        else:
                            self.cache[f"ENTRY/{k}"] = v.strip()

                self.cache.update(CmdlineEngine._parse(" ".join(option_lines)))
                self.cache["ENTRY/entry_file"] = self.config_path.name
            except OSError:
                pass
        else:
            # Active entry is planned/virtual: pre-populate with proven baseline options
            base_opts = get_baseline_options([self.loader_conf_path.parent / "entries", Path("/boot/loader/entries")])
            self.cache.update(CmdlineEngine._parse(base_opts))

            target_clean = clean_kernel_name(self.config_path.stem)
            pkgbase = resolve_pkgbase_for_kernel(self.config_path.stem)
            kver = resolve_kver_for_pkgbase(pkgbase)
            linux_rel, img_rel, _ = resolve_kernel_images(pkgbase, kver)
            self.cache["ENTRY/entry_file"] = self.config_path.name
            self.cache["ENTRY/title"] = canonical_entry_title(target_clean, pkgbase)
            if kver:
                self.cache["ENTRY/version"] = kver
            self.cache["ENTRY/sort-key"] = canonical_sort_key(pkgbase)
            self.cache["ENTRY/linux"] = linux_rel
            self.cache["ENTRY/initrd"] = img_rel
            self.cache["ENTRY/needs_synthesis"] = "true"

        self._snapshots.setdefault(self.config_path, None)
        self._snapshots.setdefault(self.loader_conf_path, None)
        return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[ChangeTuple]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No pending changes.", ""
        with self._lock:
            try:
                for key, scope, value, _ in changes:
                    if scope not in {'LOADER', 'ENTRY_OVERRIDE', 'DEFAULT', 'ENTRY'}:
                        raise ValueError(f'Unknown boot configuration scope: {scope}')
                    if not key or any(c.isspace() or c == '\0' for c in key):
                        raise ValueError(f'Invalid boot configuration key: {key!r}')
                    if any(c in str(value) for c in '\r\n\0'):
                        raise ValueError('Boot configuration values must be single-line.')
                if any(scope == 'DEFAULT' for _, scope, _, _ in changes) and any(scope == 'ENTRY' and key == 'options' for key, scope, _, _ in changes):
                    raise ValueError('Edit either ENTRY/options or DEFAULT parameters in one batch, not both.')
                override_keys = {}
                if any(c[1] == 'ENTRY_OVERRIDE' for c in changes):
                    for name in self.get_entries_map():
                        slug = slugify_entry_key(name)
                        override_keys[slug] = override_keys.get(slug, 0) + 1
                for key, scope, _, _ in changes:
                    if scope == 'ENTRY_OVERRIDE':
                        if key not in override_keys:
                            raise ValueError(f'Unknown boot entry override: {key}')
                        if override_keys[key] != 1:
                            raise ValueError(f'Ambiguous boot entry override: {key}; edit the entry file directly.')
                for path, expected in self._snapshots.items():
                    if current_stamp(path) != expected:
                        raise OSError(f'{path.name} modified externally. Reload required.')
                return self._write_batch(list({(c[0], c[1]): c for c in changes}.values()))
            except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as exc:
                return False, f'Boot configuration write failed: {exc}', ''

    def _write_batch(self, changes):
        if not changes:
            return True, "No pending changes.", ""

        loader_changes = [c for c in changes if c[1] == "LOADER"]
        override_changes = [c for c in changes if c[1] == "ENTRY_OVERRIDE"]
        entry_and_cmdline_changes = [c for c in changes if c[1] in ("DEFAULT", "ENTRY")]

        msgs = []

        # Handle target_entry switch if present in batch
        for k, scope, val, _ in loader_changes:
            if k == "target_entry":
                requested_target = str(val).strip()
                if not requested_target.startswith("Auto"):
                    ok_ens, _, msg_ens = self.ensure_entry_exists(requested_target)
                    if not ok_ens:
                        return False, msg_ens, ""
                self._target_override = requested_target
                self.config_path = self._resolve_config_path()

        if entry_and_cmdline_changes:
            valid, validation_message = self._prepare_entry_conf(entry_and_cmdline_changes)
            if not valid:
                return False, validation_message, ''

        # Handle default entry selection: ensure entry exists on disk first
        for k, scope, val, _ in loader_changes:
            if k == "default":
                clean_def = str(val).strip()
                if clean_def and clean_def not in {"unset", "nil", "__DELETE__", "__delete__"} and not clean_def.startswith("@") and not any(c in clean_def for c in "*?["):
                    ok_ens, _, msg_ens = self.ensure_entry_exists(clean_def)
                    if not ok_ens:
                        return False, msg_ens, ""

        # Write loader.conf
        if any(c[0] != "target_entry" for c in loader_changes):
            ok_l, msg_l = self._write_loader_conf(loader_changes)
            if not ok_l:
                return False, msg_l, ""
            msgs.append(msg_l)

        # Write direct entry overrides (e.g. renaming any installed kernel)
        if override_changes:
            ok_o, msg_o = self._write_entry_overrides(override_changes)
            if not ok_o:
                return False, ("Earlier updates saved: " + " ".join(msgs) + "; " if msgs else "") + msg_o, ""
            msgs.append(msg_o)
            self._cached_entries_map = {}

        # Write active entry config
        if entry_and_cmdline_changes:
            ok_e, msg_e = self._write_entry_conf(entry_and_cmdline_changes)
            if not ok_e:
                return False, ("Earlier updates saved: " + " ".join(msgs) + "; " if msgs else "") + msg_e, ""
            msgs.append(msg_e)

        return True, " ".join(msgs) or "Successfully saved changes.", ""

    def _write_entry_overrides(self, changes: list[ChangeTuple]) -> tuple[bool, str]:
        """Writes individual entry metadata updates directly to their respective .conf files."""
        entries = self.get_entries_map()
        slug_to_entry: dict[str, str] = {}
        for clean_name in entries:
            slug_to_entry[slugify_entry_key(clean_name)] = clean_name

        updated_files = []
        for key, _, new_title, _ in changes:
            if key not in slug_to_entry:
                return False, f"Unknown boot entry override: {key}"
            clean_name = slug_to_entry[key]

            ok_ens, entry_path, msg_ens = self.ensure_entry_exists(clean_name)
            if not ok_ens:
                return False, msg_ens
            if not entry_path or not entry_path.is_file():
                continue

            try:
                lines = [line.rstrip('\r\n') for line in split_lines(self._read_for_write(entry_path))]
            except OSError as e:
                return False, f"Failed to read {entry_path.name}: {e}"

            out_lines = []
            title_found = False
            for line in lines:
                line_s = line.strip()
                if line_s.startswith(("title ", "title\t")):
                    title_found = True
                    out_lines.append(f"title      {new_title}")
                else:
                    out_lines.append(line)

            if not title_found:
                out_lines.insert(0, f"title      {new_title}")

            content = "\n".join(out_lines) + "\n"
            ok, msg = self._atomic_write(entry_path, content)
            if not ok:
                return False, msg
            updated_files.append(entry_path.name)

        return True, f"Renamed entry in {', '.join(updated_files)}" if updated_files else "No entries updated."

    def _write_loader_conf(self, changes: list[ChangeTuple]) -> tuple[bool, str]:
        lines: list[str] = []
        if self.loader_conf_path.exists():
            try:
                lines = [line.rstrip('\r\n') for line in split_lines(self._read_for_write(self.loader_conf_path))]
            except (OSError, UnicodeError) as exc:
                return False, f"Cannot read loader configuration: {exc}"

        changes_dict = {}
        for key, _, val, _ in changes:
            if key == "target_entry":
                continue
            elif key == "default":
                changes_dict[key] = str(val) if str(val) in {"unset", "nil", "__DELETE__", "__delete__"} else self.map_clean_to_raw(str(val))
            else:
                changes_dict[key] = str(val)

        out_lines: list[str] = []
        handled_keys: set[str] = set()

        for line in lines:
            line_s = line.strip()
            if line_s and not line_s.startswith("#") and (" " in line_s or "\t" in line_s):
                k, _ = line_s.split(None, 1)
                if k in changes_dict:
                    val = str(changes_dict[k]).strip()
                    handled_keys.add(k)
                    if val not in ("unset", "__delete__", "__DELETE__", "nil", ""):
                        out_lines.append(f"{k:<32}{val}")
                    continue
            out_lines.append(line)

        for k, val in changes_dict.items():
            if k not in handled_keys:
                val_s = str(val).strip()
                if val_s not in ("unset", "__delete__", "__DELETE__", "nil", ""):
                    out_lines.append(f"{k:<32}{val_s}")

        content = "\n".join(out_lines) + "\n"
        ok, msg = self._atomic_write(self.loader_conf_path, content)
        if not ok:
            return ok, msg

        for key, command in (('default', 'set-default'), ('timeout', 'set-timeout')):
            if key not in changes_dict:
                continue
            value = changes_dict[key]
            argument = '' if value in ('unset', '__delete__', '__DELETE__', 'nil', '') else value
            try:
                result = subprocess.run(['bootctl', command, argument], capture_output=True, text=True, timeout=5)
                if result.returncode:
                    result = subprocess.run(['sudo', '-n', 'bootctl', command, argument], capture_output=True, text=True, timeout=5)
                if result.returncode:
                    return False, f'{self.loader_conf_path.name} saved, but bootctl {command} failed: {result.stderr.strip() or f"exit {result.returncode}"}'
            except (OSError, subprocess.SubprocessError) as exc:
                return False, f'{self.loader_conf_path.name} saved, but bootctl {command} failed: {exc}'
        return ok, msg

    def _write_entry_conf(self, changes: list[ChangeTuple]) -> tuple[bool, str]:
        valid, content = self._prepare_entry_conf(changes)
        return self._atomic_write(self.config_path, content) if valid else (False, content)

    def _prepare_entry_conf(self, changes: list[ChangeTuple]) -> tuple[bool, str]:
        lines: list[str] = []
        if self.config_path.exists():
            try:
                lines = [line.rstrip('\r\n') for line in split_lines(self._read_for_write(self.config_path))]
            except OSError as e:
                return False, f"Failed to open config for verification: {e}"
        else:
            # Entry file does not exist yet: build complete BLS Type #1 skeleton before applying changes
            target_clean = clean_kernel_name(self.config_path.stem)
            pkgbase = resolve_pkgbase_for_kernel(self.config_path.stem)
            title = canonical_entry_title(target_clean, pkgbase)
            sort_key = canonical_sort_key(pkgbase)
            kver = resolve_kver_for_pkgbase(pkgbase)
            linux_rel, img_rel, _ = resolve_kernel_images(pkgbase, kver)

            lines = [
                f"title      {title}",
            ]
            if kver:
                lines.append(f"version    {kver}")
            lines.extend([
                f"sort-key   {sort_key}",
                f"linux      {linux_rel}",
            ])
            if not check_microcode_hook():
                for img in ("intel-ucode.img", "amd-ucode.img"):
                    if (Path("/boot") / img).is_file():
                        lines.append(f"initrd     /{img}")
            lines.append(f"initrd     {img_rel}")
            base_opts = get_baseline_options([self.loader_conf_path.parent / "entries", Path("/boot/loader/entries")])
            lines.append(f"options    {base_opts}")

        cmdline_changes = [c for c in changes if c[1] == 'DEFAULT']
        metadata = {c[0]: c[2] for c in changes if c[1] == 'ENTRY'}
        # entry_file is descriptive state, not a BLS directive.
        if 'entry_file' in metadata or 'needs_synthesis' in metadata:
            return False, 'Entry filenames and synthesis status are read-only.'
        maximum = {}
        for line in lines:
            parts = line.strip().split(None, 1)
            if parts and not parts[0].startswith('#'):
                maximum[parts[0]] = maximum.get(parts[0], 0) + 1
        pending = {}
        for raw_key, value in metadata.items():
            key, sep, index = raw_key.rpartition(':')
            if sep:
                if not index.isdecimal() or int(index) < 1:
                    return False, f'Invalid BLS directive index: {raw_key}'
                occurrence = int(index)
            else:
                key, occurrence = raw_key, maximum.get(raw_key, 1)
            pending[key, occurrence] = str(value).strip()
        output, applied, counts = [], set(), {}
        options, options_position = [], None
        for line in lines:
            parts = line.strip().split(None, 1)
            if not parts or parts[0].startswith('#'):
                output.append(line)
                continue
            key = parts[0]
            if key == 'options' and cmdline_changes:
                if options_position is None:
                    options_position = len(output)
                options.append(parts[1] if len(parts) == 2 else '')
                continue
            count = counts[key] = counts.get(key, 0) + 1
            target = key, count
            if target not in pending:
                output.append(line)
            else:
                applied.add(target)
                value = pending[target]
                if value not in ('unset', '__DELETE__', '__delete__', 'nil', ''):
                    output.append(f'{key} {value}')
        for target, value in pending.items():
            if target in applied or value in ('unset', '__DELETE__', '__delete__', 'nil', ''):
                continue
            key, occurrence = target
            next_count = counts.get(key, 0) + 1
            if occurrence != next_count:
                return False, f'Cannot append {key}:{occurrence}; next occurrence is {next_count}.'
            counts[key] = next_count
            output.append(f'{key} {value}')
        if cmdline_changes:
            options_value = CmdlineEngine.transform(' '.join(options), cmdline_changes).rstrip('\n')
            output.insert(options_position if options_position is not None else len(output), 'options '+options_value)
        return True, '\n'.join(output)+'\n'

    def _read_for_write(self, path):
        text, snapshot = read_text(path)
        if path in self._snapshots and snapshot != self._snapshots[path]:
            raise OSError(f'{path.name} modified externally. Reload required.')
        self._snapshots[path] = snapshot
        return text

    def _atomic_write(self, target_path: Path, final_content: str) -> tuple[bool, str]:
        try:
            expected = self._snapshots[target_path] if target_path in self._snapshots else current_stamp(target_path)
            try:
                snapshot = atomic_write(target_path, final_content, expected)
            except PermissionError:
                snapshot = privileged_atomic_write(target_path, final_content, expected)
            self._snapshots[target_path] = snapshot
            return True, f'Updated {target_path.name}'
        except PermissionError:
            return False, 'AUTH_REQUIRED'
        except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as exc:
            return False, f'Write error on {target_path.name}: {exc}'


# =============================================================================
# CLI & DIAGNOSTIC RUNNER
# =============================================================================
if __name__ == "__main__":
    import sys
    engine = SystemdBootEngine()

    if "--sync" in sys.argv:
        print("[*] Synchronizing systemd-boot entries for all installed kernels...")
        ok, syn = engine.auto_synthesize_all_missing_entries()
        if syn:
            print(f"[+] Successfully synthesized entries for: {', '.join(syn)}")
        else:
            print("[✓] All installed kernels already have valid BLS entries.")
    else:
        entries = engine.get_entries_map()
        print(f"systemd-boot entries map ({len(entries)} entries):")
        for k, v in entries.items():
            status = "needs synthesis" if v.get("needs_synthesis") else "live on disk"
            print(f"  - {k:<25} -> {v.get('entry_file'):<25} [{status}] ({v.get('source')})")
