#!/usr/bin/env python3
"""
SystemdBootEngine - Bleeding-Edge systemd-boot (systemd 255-261+ / Linux 6.x-7.x+) Engine.
Implements dynamic boot entry discovery, bidirectional clean kernel name translation,
multi-entry metadata management and direct renaming, atomic loader/entry updates,
and full systemd-boot EFI maintenance capabilities.
"""
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Self

from python.frontend.core_types import BaseEngine
from python.engines.cmdline import BridgedStateDict

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
# KERNEL AND ENTRY NAME NORMALIZATION UTILITIES
# =============================================================================
def slugify_entry_key(clean_name: str) -> str:
    """Creates a deterministic, safe schema key slug for an entry title override."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", clean_name.lower()).strip("_")
    return f"title__{s}"


def clean_kernel_name(raw: str) -> str:
    """
    Transforms any raw kernel version, entry filename, or title into a clean,
    human-readable kernel name (e.g., 'Arch Linux', 'Dusky Battery', 'Dusky Gaming').
    """
    if not raw:
        return "Arch Linux"
    s = str(raw).strip()
    if s.startswith("@"):
        return s

    is_fallback = bool(re.search(r"fallback|recovery", s, flags=re.IGNORECASE))

    # Handle parenthetical annotations:
    # - If it contains a specific kernel name like (7.2.0-dusky-battery), extract it.
    # - If it is a generic tag like (linux), (x86_64), or (fallback initramfs), strip it.
    paren_match = re.search(r"\(([^)]+)\)", s)
    if paren_match:
        inner = paren_match.group(1).strip()
        if re.search(r"fallback|recovery", inner, flags=re.IGNORECASE):
            is_fallback = True
            s = re.sub(r"\s*\([^)]*\)", "", s).strip()
        elif not re.match(r"^(?:linux|default|x86_64|amd64)$", inner, flags=re.IGNORECASE):
            s = inner
        else:
            s = re.sub(r"\s*\([^)]*\)", "", s).strip()

    # Strip file extensions
    for ext in (".conf", ".preset", ".img", ".efi", ".efi.extra", ".bak", ".old", ".uki"):
        if s.endswith(ext):
            s = s[:-len(ext)]

    # Remove 32-hex machine-id prefix if present (Type #1 BLS: e.g. 22d434e8ca4f425482251d8a6ba8ddea-)
    s = re.sub(r"^[a-f0-9]{32}[-_]", "", s)

    # Repeatedly strip redundant prefixes (vmlinuz-, initramfs-, linux-, arch-linux-, etc.)
    while True:
        prev = s
        s = re.sub(r"^(?:vmlinuz|initramfs|linux|Linux|arch-linux|arch_linux|arch)[-_ ]+", "", s)
        if s == prev:
            break

    # Strip SemVer kernel version prefix (e.g., '7.2.0-dusky-battery' -> 'dusky-battery', '7.1.9-arch1-2' -> 'arch1-2')
    s = re.sub(r"^\d+\.\d+(?:\.\d+)?(?:-rc\d+)?(?:-git\d*)?-", "", s)

    # Clean leftover fallback/recovery words from stem
    s = re.sub(r"[-_ ]*(?:fallback|recovery)(?:[-_ ]*initramfs)?[-_ ]*", "", s, flags=re.IGNORECASE).strip()

    sl = s.lower()
    match sl:
        case "" | "arch" | "arch1" | "archlinux" | "linux" | "default":
            title = "Arch Linux"
        case _ if re.match(r"^arch\d*(?:-\d+)?$", sl):
            title = "Arch Linux"
        case _ if sl.startswith(("dusky-", "dusky_")):
            sub = sl[6:].replace("-", " ").replace("_", " ").strip().title()
            title = f"Dusky {sub}"
        case _ if "dusky" in sl:
            clean_sub = sl.replace("dusky", "").replace("-", " ").replace("_", " ").strip().title()
            title = f"Dusky {clean_sub}" if clean_sub else "Dusky"
        case _ if "zen" in sl:
            title = "Arch Linux Zen"
        case _ if "lts" in sl:
            title = "Arch Linux LTS"
        case _ if "hardened" in sl:
            title = "Arch Linux Hardened"
        case _ if "cachyos" in sl:
            clean_sub = sl.replace("cachyos", "").replace("-", " ").replace("_", " ").strip().title()
            title = f"CachyOS {clean_sub}" if clean_sub else "CachyOS"
        case _ if "t2" in sl:
            title = "T2 Linux"
        case _:
            title = s.replace("-", " ").replace("_", " ").strip().title()

    if is_fallback and not title.endswith("(Fallback)"):
        title = f"{title} (Fallback)"
    return title


def kernel_flavor_stem(raw: str) -> str:
    """
    Extracts the base normalized flavor identifier token from a filename, kver,
    or title (e.g. 'dusky-battery', 'dusky-gaming', 'zen', 'lts', 'arch').
    Used for strict 1:1 deduplication across discovery layers.
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


def discover_all_kernels_and_entries() -> EntryMap:
    """
    Dynamically scans ESP boot loader entries, EFI variable history, installed modules
    in /usr/lib/modules, mkinitcpio presets in /etc/mkinitcpio.d, and boot kernels in /boot.
    
    Guarantees strict deduplication: exactly ONE canonical entry per physical boot option / kernel.
    
    Returns a mapping: clean_kernel_name -> metadata dict:
        {
            "clean_name": str,
            "entry_file": str,
            "entry_path": str,
            "kver": str,
            "title": str,
            "hint": str,
            "source": str
        }
    """
    # 1. Primary container indexed by canonical entry identifier (e.g. filename/entry_id)
    canonical_entries: dict[str, dict[str, Any]] = {}

    esp_entry_dirs = [
        Path("/boot/loader/entries"),
        Path("/efi/loader/entries"),
        Path("/boot/efi/loader/entries"),
        Path("/loader/entries"),
    ]

    # Query bootctl JSON (systemd 255-261+ JSON output)
    try:
        res = subprocess.run(["bootctl", "list", "--json=short"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and res.stdout.strip():
            entries_json = json.loads(res.stdout)
            if isinstance(entries_json, list):
                for entry in entries_json:
                    entry_type = entry.get("type", "")
                    entry_id = entry.get("id", "")
                    title_raw = entry.get("title", "")

                    # Skip synthetic firmware/automatic non-kernel entries
                    if entry_type == "Automatic" or entry_id.startswith("auto-"):
                        continue
                    if any(sub in title_raw.lower() for sub in ("reboot into firmware", "efi default loader", "windows boot manager")):
                        continue

                    src = entry.get("source", "")
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
        if title_raw and not re.match(r"^(?:arch linux|linux)$", title_raw.strip(), flags=re.IGNORECASE):
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
            "title": title_raw or unique_clean,
            "hint": hint,
            "source": data.get("source", "bls"),
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
                    if clean not in results and stem not in covered_stems:
                        results[clean] = {
                            "clean_name": clean,
                            "entry_file": "",
                            "entry_path": "",
                            "kver": entry.name,
                            "title": clean,
                            "hint": f"Installed Kernel: {entry.name}",
                            "source": "modules",
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
                if clean not in results and stem not in covered_stems:
                    results[clean] = {
                        "clean_name": clean,
                        "entry_file": "",
                        "entry_path": "",
                        "kver": "",
                        "title": clean,
                        "hint": f"Mkinitcpio Preset: {preset.name}",
                        "source": "mkinitcpio",
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
                if clean not in results and stem not in covered_stems:
                    results[clean] = {
                        "clean_name": clean,
                        "entry_file": "",
                        "entry_path": "",
                        "kver": "",
                        "title": clean,
                        "hint": f"Kernel Image: {vmlinuz.name}",
                        "source": "boot_image",
                    }
                    if stem:
                        covered_stems.add(stem)
        except Exception:
            pass

    # Guarantee stock "Arch Linux" always exists as an available baseline
    if "Arch Linux" not in results:
        results["Arch Linux"] = {
            "clean_name": "Arch Linux",
            "entry_file": "arch-linux.conf",
            "entry_path": "/boot/loader/entries/arch-linux.conf",
            "kver": "",
            "title": "Arch Linux",
            "hint": "Stock Arch Linux kernel (/boot/vmlinuz-linux)",
            "source": "stock_default",
        }

    return results


# =============================================================================
# SYSTEMD-BOOT ENGINE IMPLEMENTATION
# =============================================================================
class SystemdBootEngine(BaseEngine):
    """
    Intelligent engine for systemd-boot (systemd 255 - 261+ / Linux 6.x - 7.x+).
    
    Manages:
    - DEFAULT scope: Kernel command-line parameters in options line of active entry .conf
    - ENTRY scope: Active entry metadata (title, sort-key, version, linux, initrd, architecture)
    - ENTRY_OVERRIDE scope: Direct per-entry title modification for ANY installed kernel entry
    - LOADER scope: Global bootloader settings in loader.conf (default, timeout, console-mode, editor, auto-entries, etc.)
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
            if p.exists():
                return p

        # Check candidate entry directories (prioritizing entries dir next to loader.conf)
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
            if entry_path and entry_path.exists():
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
                                for match in entries_dir.glob(f"*{def_val.strip('*')}*"):
                                    if match.is_file():
                                        return match
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

        return Path("/boot/loader/entries/arch-linux.conf")

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
        """Resolves a clean kernel name to its absolute Path on disk."""
        entries = self.get_entries_map()
        if clean_name in entries:
            src = entries[clean_name].get("entry_path") or entries[clean_name].get("entry_file")
            if src:
                p = Path(src)
                if p.exists():
                    return p

        stem_target = kernel_flavor_stem(clean_name)

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
                                if line.strip().startswith(("title ", "title\t")):
                                    t = line.strip().split(None, 1)[1].strip()
                                    if clean_kernel_name(t) == clean_name:
                                        return conf
                        except Exception:
                            pass
                except Exception:
                    pass
        return None

    def map_raw_to_clean(self, raw_val: str) -> str:
        """Translates a raw loader.conf default value (e.g. long filename) to its clean UI name."""
        if not raw_val or raw_val.startswith("@"):
            return raw_val

        entries = self.get_entries_map()

        # 1. Direct match on entry filename or entry ID
        for clean_name, meta in entries.items():
            entry_file = meta.get("entry_file", "")
            if entry_file and (raw_val == entry_file or raw_val == Path(entry_file).stem):
                return clean_name

        # 2. Match on clean normalization
        clean = clean_kernel_name(raw_val)
        if clean in entries:
            return clean

        # 3. Match by flavor stem
        raw_stem = kernel_flavor_stem(raw_val)
        if raw_stem:
            for clean_name, meta in entries.items():
                if kernel_flavor_stem(clean_name) == raw_stem or kernel_flavor_stem(meta.get("entry_file", "")) == raw_stem:
                    return clean_name

        # 4. Partial / wildcard pattern match
        pattern = raw_val.strip("*").lower()
        if pattern:
            for clean_name in entries:
                if pattern in clean_name.lower():
                    return clean_name

        return clean

    def map_clean_to_raw(self, clean_name: str) -> str:
        """Translates a clean UI selection (e.g. 'Arch Linux' or 'Dusky Battery') to the loader.conf entry format."""
        if not clean_name or clean_name.startswith("@"):
            return clean_name

        entries = self.get_entries_map()

        # 1. Exact match in discovered entries
        if clean_name in entries:
            meta = entries[clean_name]
            entry_file = meta.get("entry_file", "")
            if entry_file:
                return entry_file

        # 2. Check on-disk entry directories for matching filename or title
        p = self.get_entry_path_for_name(clean_name)
        if p and p.is_file():
            return p.name

        # 3. Derive standard filename / glob fallback (per loader.conf(5), entry IDs
        #    include the literal ".conf" suffix and glob matching is case-insensitive)
        cl = clean_name.lower()
        slug = cl.replace(" ", "-")
        match cl:
            case "arch linux":
                return "arch-linux.conf"
            case "arch linux (fallback)":
                return "arch-linux-fallback.conf"
            case _:
                return f"*{slug}*.conf"

    def load_state(self) -> dict[str, Any]:
        self.cache = BridgedStateDict()
        self._cached_entries_map = discover_all_kernels_and_entries()

        # 1. Load global loader.conf (LOADER scope)
        raw_default = ""
        clean_default = "Arch Linux"
        if self.loader_conf_path.exists():
            try:
                with open(self.loader_conf_path, "r", encoding="utf-8", errors="replace") as f:
                    self.loader_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                    for line in f.read().splitlines():
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

        # Cross-check against the LoaderEntryDefault EFI variable, which takes
        # absolute precedence over loader.conf at boot time. If the variable is
        # set, it represents the actual effective default regardless of loader.conf.
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
                    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
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
                with open(self.config_path, "r", encoding="utf-8", errors="replace") as f:
                    self.file_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                    content = f.read()

                initrd_count = 0
                for line in content.splitlines():
                    line_clean = line.strip()
                    if not line_clean or line_clean.startswith("#"):
                        continue

                    if match := re.match(r"^([ \t]*)options([ \t]+)(.*)$", line):
                        tokens = re.split(r'((?:[^\s"\']|"[^"]*"|\'[^\']*\')+)', match.group(3))
                        args = [t for t in tokens if t.strip()]
                        counts: dict[str, int] = {}

                        for arg in args:
                            k, v = arg.split("=", 1) if "=" in arg else (arg, "true")
                            counts[k] = counts.get(k, 0) + 1
                            self.cache[f"DEFAULT/{k}:{counts[k]}"] = v
                            self.cache[f"DEFAULT/{k}"] = v
                    elif " " in line_clean or "\t" in line_clean:
                        k, v = line_clean.split(None, 1)
                        if k == "initrd":
                            initrd_count += 1
                            self.cache[f"ENTRY/initrd:{initrd_count}"] = v.strip()
                            self.cache["ENTRY/initrd"] = v.strip()
                        else:
                            self.cache[f"ENTRY/{k}"] = v.strip()

                self.cache["ENTRY/entry_file"] = self.config_path.name
            except OSError:
                pass

        return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[ChangeTuple]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No pending changes.", ""

        loader_changes = [c for c in changes if c[1] == "LOADER"]
        override_changes = [c for c in changes if c[1] == "ENTRY_OVERRIDE"]
        entry_and_cmdline_changes = [c for c in changes if c[1] in ("DEFAULT", "ENTRY")]

        msgs = []

        # Handle target_entry switch if present in batch
        for k, scope, val, _ in loader_changes:
            if k == "target_entry":
                self._target_override = str(val).strip()
                self.config_path = self._resolve_config_path()

        # Write loader.conf
        if loader_changes:
            ok_l, msg_l = self._write_loader_conf(loader_changes)
            if not ok_l:
                return False, msg_l, ""
            msgs.append(msg_l)

        # Write direct entry overrides (e.g. renaming any installed kernel)
        if override_changes:
            ok_o, msg_o = self._write_entry_overrides(override_changes)
            if not ok_o:
                return False, msg_o, ""
            msgs.append(msg_o)
            # Invalidate cached map so next discovery reflects the new names immediately
            self._cached_entries_map = {}

        # Write active entry config
        if entry_and_cmdline_changes:
            ok_e, msg_e = self._write_entry_conf(entry_and_cmdline_changes)
            if not ok_e:
                return False, msg_e, ""
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
                continue
            clean_name = slug_to_entry[key]
            entry_path = self.get_entry_path_for_name(clean_name)
            if not entry_path or not entry_path.is_file():
                continue

            try:
                lines = entry_path.read_text(encoding="utf-8").splitlines()
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
                with open(self.loader_conf_path, "r", encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except OSError:
                lines = []

        changes_dict = {}
        for key, _, val, _ in changes:
            if key == "target_entry":
                continue  # Virtual UI state
            elif key == "default":
                changes_dict[key] = self.map_clean_to_raw(str(val))
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
                    if val not in ("unset", "__delete__", ""):
                        out_lines.append(f"{k:<32}{val}")
                    continue
            out_lines.append(line)

        for k, val in changes_dict.items():
            if k not in handled_keys:
                val_s = str(val).strip()
                if val_s not in ("unset", "__delete__", ""):
                    out_lines.append(f"{k:<32}{val_s}")

        content = "\n".join(out_lines) + "\n"
        ok, msg = self._atomic_write(self.loader_conf_path, content)
        if not ok:
            return ok, msg

        # Sync the LoaderEntryDefault EFI variable via bootctl set-default.
        # The EFI variable takes absolute precedence over loader.conf at boot time
        # (per loader.conf(5)), so we must update it atomically whenever the default changes.
        if "default" in changes_dict:
            default_val = changes_dict["default"]
            bootctl_arg = default_val if default_val not in ("unset", "__delete__", "") else ""
            try:
                res = subprocess.run(
                    ["bootctl", "set-default", bootctl_arg],
                    capture_output=True, text=True, timeout=5,
                )
                if res.returncode != 0:
                    stderr = (res.stderr or "").strip()
                    msg += f" (Warning: bootctl set-default: {stderr})"
            except Exception:
                pass

        # Sync the LoaderConfigTimeout EFI variable via bootctl set-timeout
        if "timeout" in changes_dict:
            timeout_val = changes_dict["timeout"]
            bootctl_timeout = timeout_val if timeout_val not in ("unset", "__delete__", "") else ""
            try:
                subprocess.run(
                    ["bootctl", "set-timeout", bootctl_timeout],
                    capture_output=True, text=True, timeout=5,
                )
            except Exception:
                pass

        return ok, msg

        return ok, msg

    def _write_entry_conf(self, changes: list[ChangeTuple]) -> tuple[bool, str]:
        lines: list[str] = []
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except OSError as e:
                return False, f"Failed to open config for verification: {e}"

        cmdline_changes = [c for c in changes if c[1] == "DEFAULT"]
        entry_changes = {c[0]: c[2] for c in changes if c[1] == "ENTRY"}

        out_lines: list[str] = []
        options_found = False
        handled_entry_keys: set[str] = set()

        changes_dict = {(scope, key): (val, itype) for key, scope, val, itype in cmdline_changes}
        applied_commits: set[tuple[str, str]] = set()

        for line in lines:
            line_s = line.strip()
            # Handle ENTRY metadata lines (title, sort-key, version, etc.)
            if line_s and not line_s.startswith("#") and not line_s.startswith("options"):
                if " " in line_s or "\t" in line_s:
                    k, _ = line_s.split(None, 1)
                    if k in entry_changes:
                        val = str(entry_changes[k]).strip()
                        handled_entry_keys.add(k)
                        if val not in ("unset", "__delete__", ""):
                            out_lines.append(f"{k:<10} {val}")
                        continue

            # Handle options line (DEFAULT scope)
            if match := re.match(r"^([ \t]*)options([ \t]+)(.*)$", line):
                options_found = True
                leading_space, spacing, options_val = match.groups()
                tokens = re.split(r'((?:[^\s"\']|"[^"]*"|\'[^\']*\')+)', options_val)

                max_counts: dict[str, int] = {}
                for t in tokens:
                    if t.strip():
                        k = t.split("=", 1)[0]
                        max_counts[k] = max_counts.get(k, 0) + 1

                out_tokens: list[str] = []
                counts: dict[str, int] = {}

                for t in tokens:
                    if not t.strip():
                        out_tokens.append(t)
                        continue

                    k = t.split("=", 1)[0]
                    counts[k] = counts.get(k, 0) + 1

                    lookup_exact = ("DEFAULT", f"{k}:{counts[k]}")
                    lookup_base = ("DEFAULT", k)

                    target_val = None
                    target_itype = None
                    matched_lookup = None

                    if lookup_exact in changes_dict:
                        target_val, target_itype = changes_dict[lookup_exact]
                        matched_lookup = lookup_exact
                    elif counts.get(k, 0) == max_counts.get(k, 0) and lookup_base in changes_dict:
                        target_val, target_itype = changes_dict[lookup_base]
                        matched_lookup = lookup_base

                    if target_val is not None:
                        applied_commits.add(matched_lookup)
                        val_str = str(target_val)
                        val_lower = val_str.lower()

                        match (val_lower, target_itype):
                            case ("__delete__" | "unset" | "", _) | ("false", "bool"):
                                if out_tokens and out_tokens[-1].isspace():
                                    out_tokens.pop()
                            case _:
                                if target_itype == "bool" and val_lower == "true":
                                    out_tokens.append(k)
                                else:
                                    out_tokens.append(f"{k}={val_str}")
                    else:
                        out_tokens.append(t)

                for key_raw, scope, val, target_itype in cmdline_changes:
                    lookup = (scope, key_raw)
                    if lookup in applied_commits:
                        continue

                    val_str = str(val)
                    val_lower = val_str.lower()

                    match (val_lower, target_itype):
                        case ("__delete__" | "unset" | "", _) | ("false", "bool"):
                            continue
                        case _:
                            clean_key = key_raw.split(":")[0] if ":" in key_raw else key_raw
                            needs_space = False
                            for tk in reversed(out_tokens):
                                if tk:
                                    needs_space = bool(tk.strip())
                                    break
                            if needs_space:
                                out_tokens.append(" ")

                            if target_itype == "bool" and val_lower == "true":
                                out_tokens.append(clean_key)
                            else:
                                out_tokens.append(f"{clean_key}={val_str}")
                            applied_commits.add(lookup)

                out_lines.append(f"{leading_space}options{spacing}{''.join(out_tokens).strip()}")
            else:
                out_lines.append(line)

        # Append any new ENTRY metadata keys that weren't in file
        for k, val in entry_changes.items():
            if k not in handled_entry_keys:
                val_s = str(val).strip()
                if val_s not in ("unset", "__delete__", ""):
                    out_lines.insert(0, f"{k:<10} {val_s}")

        if not options_found and cmdline_changes:
            new_tokens: list[str] = []
            for key_raw, scope, val, target_itype in cmdline_changes:
                val_str = str(val)
                val_lower = val_str.lower()
                if val_lower in ("__delete__", "unset", "") or (target_itype == "bool" and val_lower == "false"):
                    continue
                clean_key = key_raw.split(":")[0] if ":" in key_raw else key_raw
                if new_tokens:
                    new_tokens.append(" ")
                if target_itype == "bool" and val_lower == "true":
                    new_tokens.append(clean_key)
                else:
                    new_tokens.append(f"{clean_key}={val_str}")
            if new_tokens:
                out_lines.append(f"options\t{''.join(new_tokens)}")

        content = "\n".join(out_lines) + "\n"
        return self._atomic_write(self.config_path, content)

    def _atomic_write(self, target_path: Path, final_content: str) -> tuple[bool, str]:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file_path = None
        success = False
        try:
            with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8", dir=target_path.parent) as tf:
                temp_file_path = Path(tf.name)
                tf.write(final_content)
                tf.flush()
                os.fsync(tf.fileno())

            if target_path.exists():
                try:
                    stat_info = target_path.stat()
                    os.chmod(temp_file_path, stat.S_IMODE(stat_info.st_mode))
                    os.chown(temp_file_path, stat_info.st_uid, stat_info.st_gid)
                except OSError:
                    pass

            os.replace(temp_file_path, target_path)
            success = True
            return True, f"Updated {target_path.name}"
        except PermissionError:
            if temp_file_path and temp_file_path.exists():
                try:
                    temp_file_path.unlink()
                except OSError:
                    pass
            try:
                res = subprocess.run(
                    ["sudo", "-n", "tee", str(target_path)],
                    input=final_content.encode(),
                    capture_output=True,
                    timeout=5,
                )
                if res.returncode == 0:
                    return True, f"Updated {target_path.name} (via sudo)"
                return False, "AUTH_REQUIRED"
            except Exception:
                return False, "AUTH_REQUIRED"
        except OSError as e:
            return False, f"Write error on {target_path.name}: {e}"
        finally:
            if temp_file_path and temp_file_path.exists() and not success:
                try:
                    temp_file_path.unlink()
                except OSError:
                    pass
