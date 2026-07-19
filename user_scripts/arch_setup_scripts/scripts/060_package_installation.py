#!/usr/bin/env python3
# dusky_interactive=true
# ==============================================================================
# DUSKY PACKAGE INSTALLER (v10.8 - Enterprise PTY & Telemetry Engine)
# ==============================================================================
# Architecture: Asynchronous Buffered PTY Streams | Textual Split-Screen TUI
# Compatibility: Python 3.14+ | Pacman v7.1.0+ | Paru / Yay | ISO & Chroot Root
# ==============================================================================

import asyncio
import argparse
import codecs
import json
import fcntl
import os
import pty
import pwd
import re
import shutil
import signal
import struct
import sys
import termios
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Any

from rich.console import Console
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Static, RichLog, ProgressBar, Button, Label, Tree
)
from textual.widgets.tree import TreeNode

# ==============================================================================
# TYPE DEFINITIONS & HIGH-PERFORMANCE COMPILED REGEXES (PEP 695 Syntax)
# ==============================================================================
type PackageList = list[str]
type ProfileMap = dict[str, PackageList]

SCRIPT_DIR: Path = Path(__file__).resolve().parent
PROFILES_DIR: Path = SCRIPT_DIR / "package_profiles"
AUR_PROFILES_DIR: Path = PROFILES_DIR / "aur"
PACMAN_DB_LOCK: Path = Path("/var/lib/pacman/db.lck")
TEMP_SUDOERS_FILE: Path = Path("/etc/sudoers.d/99_dusky_temp_aur")
DUSKY_THEME_JSON: Path = Path.home() / ".config/matugen/generated/dusky_tui.json"

# Pre-compiled regex to strip all ANSI escape sequences and terminal control codes
ANSI_STRIP_REGEX = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0?]*[ -/]*[@-~])')

class PackageStatus(Enum):
    PENDING = auto()
    INSTALLED = auto()
    INSTALLING = auto()
    FAILED = auto()
    SKIPPED = auto()

@dataclass
class PackageItem:
    name: str
    is_aur: bool
    profile: str
    status: PackageStatus = PackageStatus.PENDING
    error_msg: Optional[str] = None

@dataclass
class InstallationManifest:
    official_packages: list[PackageItem] = field(default_factory=list)
    aur_packages: list[PackageItem] = field(default_factory=list)
    total_requested: int = 0
    already_installed: int = 0

# ==============================================================================
# CORE ENVIRONMENT & DUAL-CONTEXT PRIVILEGE RESOLUTION
# ==============================================================================
class PreflightError(Exception):
    """Raised when strict Arch Linux runtime conditions are unmet."""

@dataclass
class RuntimeContext:
    is_root: bool
    aur_helper: Optional[str] = None
    aur_user: Optional[str] = None

def verify_runtime_environment(has_aur_targets: bool) -> RuntimeContext:
    """Detects execution environment (Chroot Root vs User Desktop) and validates tools."""
    if not Path("/etc/arch-release").exists():
        raise PreflightError("CRITICAL: This installer is strictly for Arch Linux systems.")

    for cmd in ("pacman", "sudo"):
        if not shutil.which(cmd):
            raise PreflightError(f"CRITICAL: Required system binary not found: {cmd}.")

    is_root = os.geteuid() == 0
    aur_helper = None
    aur_user = None

    if has_aur_targets:
        for helper in ("paru", "yay"):
            if shutil.which(helper):
                aur_helper = helper
                break
                
        if not aur_helper:
            raise PreflightError("CRITICAL: AUR packages requested but no helper (paru/yay) found.")

        if is_root:
            target_user = os.environ.get("TARGET_USER", "").strip()
            if target_user:
                try:
                    pwd.getpwnam(target_user)
                    aur_user = target_user
                except KeyError:
                    pass
            
            if not aur_user:
                for p in pwd.getpwall():
                    if (
                        p.pw_uid >= 1000 
                        and p.pw_name != "nobody" 
                        and not p.pw_name.startswith("systemd-")
                        and "/bin" in p.pw_shell
                    ):
                        aur_user = p.pw_name
                        break

    return RuntimeContext(is_root=is_root, aur_helper=aur_helper, aur_user=aur_user)

# ==============================================================================
# THEME PARSING & GENERATION ENGINE
# ==============================================================================
def load_dusky_theme() -> str:
    """Parses generated matugen colors, generating an elegant neon slider TCSS layout."""
    fallback_css = """
    Screen { background: $surface; layout: vertical; }
    #top_header { height: 1; dock: top; content-align: center middle; background: $primary-darken-2; color: $text; text-style: bold; }
    #top_header .title { width: 100%; text-align: center; }
    #main_dashboard { layout: horizontal; height: 1fr; }
    #left_pane { width: 28%; border-right: vkey $primary-darken-1; background: $surface-darken-1; padding: 0 1; height: 100%; }
    #right_pane { width: 72%; height: 100%; layout: vertical; background: $surface; }
    #telemetry_box { height: 5; border-bottom: hkey $primary-darken-1; padding: 0 1; layout: vertical; align: left middle; }
    #status_label { text-style: bold; color: $accent; }
    #speed_label { color: $text-muted; text-style: italic; }
    #progress_bar { width: 100%; margin-top: 1; }
    RichLog { height: 1fr; border: none; scrollbar-size: 1 1; }
    Tree { background: $surface-darken-1; border: none; padding: 0; }
    """
    if not DUSKY_THEME_JSON.exists():
        return fallback_css
    try:
        data = json.loads(DUSKY_THEME_JSON.read_text(encoding="utf-8"))
        bg = data.get("bg", "#0a1612")
        fg = data.get("fg", "#d8e6df")
        accent = data.get("accent", "#00e0b8")
        error = data.get("error", "#ffb4ab")
        warning = data.get("warning", "#a0d0cb")
        success = data.get("success", "#8dd2da")
        muted = data.get("muted", "#3d4945")
        
        custom_css = f"""
        Screen {{ background: {bg}; layout: vertical; color: {fg}; }}
        #top_header {{ height: 1; dock: top; content-align: center middle; background: {muted}; color: {accent}; text-style: bold; }}
        #top_header .title {{ width: 100%; text-align: center; }}
        #main_dashboard {{ layout: horizontal; height: 1fr; }}
        #left_pane {{ width: 28%; border-right: vkey {muted}; background: {bg}; padding: 0 1; height: 100%; }}
        #right_pane {{ width: 72%; height: 100%; layout: vertical; background: {bg}; }}
        #telemetry_box {{ height: 5; border-bottom: hkey {muted}; padding: 0 2; layout: vertical; align: left middle; }}
        #status_label {{ text-style: bold; color: {accent}; }}
        #speed_label {{ color: {warning}; text-style: italic; }}
        
        /* Corrected DOM Selectors for Textual Progress Bar */
        #progress_bar {{ width: 100%; margin-top: 1; height: 1; }}
        #progress_bar .bar--bar {{ color: {accent}; }}
        #progress_bar .bar--complete {{ color: {success}; }}
        #progress_bar .bar--track {{ background: {muted}; }}
        
        RichLog {{ height: 1fr; border: none; scrollbar-size: 1 1; background: {bg}; color: {fg}; }}
        Tree {{ background: {bg}; border: none; padding: 0; color: {fg}; }}
        Tree > TreeNode {{ color: {fg}; }}
        """
        return custom_css
    except Exception:
        return fallback_css

# ==============================================================================
# PROFILE & MANIFEST RESOLUTION ENGINE
# ==============================================================================
class ProfileParser:
    """Scans, parses, and deduplicates package profiles from directory manifests."""
    
    @staticmethod
    def ensure_default_profiles() -> None:
        if not PROFILES_DIR.exists():
            PROFILES_DIR.mkdir(parents=True, exist_ok=True)
            AUR_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
            
            sample_official = PROFILES_DIR / "01_all"
            sample_official.write_text(
                "# Default Official Arch Packages\nfastfetch\nbtop\ngit\nneovim\nripgrep\n",
                encoding="utf-8"
            )
            sample_aur = AUR_PROFILES_DIR / "01_all"
            sample_aur.write_text(
                "# Default AUR Packages\nwlogout\nhyprshade\npeaclock\ntray-tui\n",
                encoding="utf-8"
            )

    @classmethod
    def _read_manifest_file(cls, file_path: Path) -> PackageList:
        if not file_path.exists() or not file_path.is_file():
            return []
        packages: PackageList = []
        for line in file_path.read_text(encoding="utf-8").splitlines():
            clean_line = line.split("#")[0].strip()
            if clean_line:
                packages.extend(clean_line.split())
        return packages

    @classmethod
    def resolve_manifests(cls, selected_profiles: list[str]) -> InstallationManifest:
        cls.ensure_default_profiles()
        target_names = selected_profiles if selected_profiles else ["01_all", "01_"]
        
        official_files = list(PROFILES_DIR.glob("*"))
        aur_files = list(AUR_PROFILES_DIR.glob("*"))
        
        manifest = InstallationManifest()
        seen_official: set[str] = set()
        seen_aur: set[str] = set()

        for p_file in sorted(official_files):
            if p_file.is_dir(): continue
            if any(p_file.name == t or p_file.stem == t or p_file.name.startswith(t) for t in target_names):
                for pkg_name in cls._read_manifest_file(p_file):
                    if pkg_name not in seen_official:
                        seen_official.add(pkg_name)
                        manifest.official_packages.append(
                            PackageItem(name=pkg_name, is_aur=False, profile=p_file.name)
                        )

        for p_file in sorted(aur_files):
            if p_file.is_dir(): continue
            if any(p_file.name == t or p_file.stem == t or p_file.name.startswith(t) for t in target_names):
                for pkg_name in cls._read_manifest_file(p_file):
                    if pkg_name not in seen_aur:
                        seen_aur.add(pkg_name)
                        manifest.aur_packages.append(
                            PackageItem(name=pkg_name, is_aur=True, profile=f"aur/{p_file.name}")
                        )

        manifest.total_requested = len(manifest.official_packages) + len(manifest.aur_packages)
        return manifest

# ==============================================================================
# ASYNCHRONOUS PACMAN & ALPM INTERACTION ENGINE
# ==============================================================================
class AsyncPackageManager:
    """Manages non-blocking ALPM database checks and PTY sub-process execution."""
    
    @staticmethod
    async def filter_installed_packages(manifest: InstallationManifest) -> None:
        """Queries local ALPM database via pacman -T in batch asynchronously."""
        all_items = manifest.official_packages + manifest.aur_packages
        if not all_items:
            return

        all_names = [item.name for item in all_items]
        proc = await asyncio.create_subprocess_exec(
            "pacman", "-T", *all_names,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        stdout, _ = await proc.communicate()
        uninstalled_names = set(stdout.decode("utf-8", errors="replace").splitlines())
        
        for item in all_items:
            if item.name not in uninstalled_names:
                if item.status != PackageStatus.INSTALLED:
                    item.status = PackageStatus.INSTALLED
                    manifest.already_installed += 1
            else:
                if item.status == PackageStatus.INSTALLED:
                    manifest.already_installed = max(0, manifest.already_installed - 1)
                item.status = PackageStatus.PENDING

    @staticmethod
    async def maintain_sudo_heartbeat() -> None:
        """Keeps sudo timestamp alive in the background without leaking sub-processes."""
        try:
            while True:
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "-n", "-v",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                await proc.wait()
                await asyncio.sleep(45)
        except asyncio.CancelledError:
            pass

# ==============================================================================
# TEXTUAL INTERACTIVE MODALS FOR ERROR / CONFLICT RECOVERY
# ==============================================================================
class ConflictModalScreen(ModalScreen[str]):
    """Modal screen displayed when package installation encounters a failure or conflict."""
    
    CSS = """
    ConflictModalScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.85);
    }
    #modal_dialog {
        width: 70;
        height: auto;
        border: heavy $error;
        background: $surface;
        padding: 1 2;
    }
    #modal_title {
        text-align: center;
        text-style: bold;
        color: $error;
        margin-bottom: 1;
    }
    #error_details {
        color: $text-muted;
        margin-bottom: 2;
    }
    #button_bar {
        layout: horizontal;
        align: center middle;
        height: 3;
    }
    Button { margin: 0 1; }
    """

    def __init__(self, package_name: str, error_msg: str):
        super().__init__()
        self.package_name = package_name
        self.error_msg = error_msg

    def compose(self) -> ComposeResult:
        with Container(id="modal_dialog"):
            yield Static(f"⚠️ INSTALLATION FAULT: {self.package_name}", id="modal_title")
            yield Static(f"Error Diagnostic:\n{self.error_msg}", id="error_details")
            with Horizontal(id="button_bar"):
                yield Button("Retry [R]", variant="primary", id="btn_retry")
                yield Button("Manual TTY [M]", variant="warning", id="btn_manual")
                yield Button("Skip Package [S]", variant="error", id="btn_skip")
                yield Button("Abort Suite [A]", variant="default", id="btn_abort")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn_retry": self.dismiss("retry")
            case "btn_manual": self.dismiss("manual")
            case "btn_skip": self.dismiss("skip")
            case "btn_abort": self.dismiss("abort")

    def on_key(self, event) -> None:
        match event.key.lower():
            case "r": self.dismiss("retry")
            case "m": self.dismiss("manual")
            case "s": self.dismiss("skip")
            case "a": self.dismiss("abort")

# ==============================================================================
# TEXTUAL TUI FRONT-END & ARCHITECTURAL ORCHESTRATOR
# ==============================================================================
class EliteInstallerApp(App):
    """The unified Textual TUI managing async PTY streams and visual telemetry."""
    
    CSS = load_dusky_theme()

    def __init__(self, manifest: InstallationManifest, context: RuntimeContext):
        super().__init__()
        self.manifest = manifest
        self.ctx = context
        self.sudo_task: Optional[asyncio.Task] = None
        self.active_child_pid: Optional[int] = None
        
        self.tree_widget = Tree("◈ Target Profiles & Packages")
        self.log_widget = RichLog(id="pty_log", highlight=True, markup=False, wrap=True)
        
        self.progress_bar = ProgressBar(
            total=100,
            show_eta=False,
            show_percentage=False,
            id="progress_bar"
        )
        self.status_label = Label("Initializing installation sequence...", id="status_label")
        self.speed_label = Label("Bandwidth: -- MiB/s | ETA: --:--", id="speed_label")
        self.tree_nodes_map: dict[str, TreeNode] = {}
        self.profile_counts: dict[str, dict[str, int]] = {}

    def compose(self) -> ComposeResult:
        env_mode = "CHROOT ROOT" if self.ctx.is_root else "USER DESKTOP"
        helper_mode = f" | Helper: {self.ctx.aur_helper}" if self.ctx.aur_helper else " | Pacman Core Only"
        with Horizontal(id="top_header"):
            yield Static(
                f"◈ DUSKY PACKAGE INSTALLER  [{env_mode}{helper_mode}]",
                classes="title"
            )
        with Horizontal(id="main_dashboard"):
            with Vertical(id="left_pane"):
                yield self.tree_widget
            with Vertical(id="right_pane"):
                with Container(id="telemetry_box"):
                    yield self.status_label
                    yield self.speed_label
                    yield self.progress_bar
                yield self.log_widget

    def on_mount(self) -> None:
        self.build_profile_tree()
        self.log_system("Environment pre-flight validated. Keyring & ALPM engine online.")
        if self.ctx.is_root and self.ctx.aur_user:
            self.log_system(f"Chroot Root Mode detected. Delegating AUR builds to: {self.ctx.aur_user}")
        self.log_system(
            f"Profiles loaded: {len(self.tree_nodes_map)} packages "
            f"({self.manifest.already_installed} already installed)."
        )
        self.run_installation_pipeline()

    def build_profile_tree(self) -> None:
        """Populates Left Pane hierarchy with profile folders and live status counters."""
        self.tree_widget.root.expand()
        profiles_dict: dict[str, list[PackageItem]] = {}
        for item in self.manifest.official_packages + self.manifest.aur_packages:
            profiles_dict.setdefault(item.profile, []).append(item)

        for profile_name, items in sorted(profiles_dict.items()):
            total = len(items)
            installed = sum(1 for i in items if i.status == PackageStatus.INSTALLED)
            self.profile_counts[profile_name] = {"total": total, "installed": installed}
            
            p_node = self.tree_widget.root.add(
                f"📁 {profile_name} ({installed}/{total})", expand=True
            )
            for item in items:
                badge = self._get_status_badge(item.status)
                node = p_node.add_leaf(f"{badge} {item.name}")
                self.tree_nodes_map[item.name] = node

    @staticmethod
    def _get_status_badge(status: PackageStatus) -> str:
        match status:
            case PackageStatus.INSTALLED: return "[green]✓[/green]"
            case PackageStatus.INSTALLING: return "[cyan]◉[/cyan]"
            case PackageStatus.PENDING: return "[blue]·[/blue]"
            case PackageStatus.FAILED: return "[red]✗[/red]"
            case PackageStatus.SKIPPED: return "[yellow]─[/yellow]"

    def update_package_node(self, pkg_name: str, status: PackageStatus) -> None:
        """Updates individual package icon and recalculates parent folder ratios."""
        if node := self.tree_nodes_map.get(pkg_name):
            badge = self._get_status_badge(status)
            node.label = Text.from_markup(f"{badge} {pkg_name}")
            
            for item in self.manifest.official_packages + self.manifest.aur_packages:
                if item.name == pkg_name:
                    old_status = item.status
                    item.status = status
                    if old_status != PackageStatus.INSTALLED and status == PackageStatus.INSTALLED:
                        self.profile_counts[item.profile]["installed"] += 1
                    elif old_status == PackageStatus.INSTALLED and status != PackageStatus.INSTALLED:
                        self.profile_counts[item.profile]["installed"] = max(0, self.profile_counts[item.profile]["installed"] - 1)
                        
                    installed = self.profile_counts[item.profile]["installed"]
                    total = self.profile_counts[item.profile]["total"]
                    if node.parent:
                        node.parent.label = Text.from_markup(
                            f"📁 {item.profile} ([green]{installed}[/green]/{total})"
                        )
                    break

    def log_system(self, msg: str, is_err: bool = False) -> None:
        color = "red" if is_err else "cyan"
        self.log_widget.write(Text.from_ansi(f"\033[1;{31 if is_err else 36}m[SYSTEM]\033[0m {msg}"))

    def handle_pty_line(self, line: str) -> None:
        """Processes an intact PTY line. Strips ANSI codes to extract telemetry and discard visual noise."""
        clean = line.strip()
        if not clean:
            return

        # Strip all ANSI escape sequences to perform accurate logic & telemetry extraction
        stripped = ANSI_STRIP_REGEX.sub("", clean).strip()
        if not stripped:
            return

        extracted_pct = None
        extracted_speed = None
        extracted_eta = None
        found_total = False

        if pct_match := re.search(r"(\d+)%", stripped):
            extracted_pct = pct_match.group(1)

        if total_match := re.search(r"Total\s+\(\d+/\d+\).*?(\d+(?:\.\d+)?\s+[A-Za-z]?i?B/s)\s+([\d:]+)", stripped):
            extracted_speed = total_match.group(1)
            extracted_eta = total_match.group(2)
            found_total = True
        elif not found_total:
            if dl_match := re.search(r"(\d+(?:\.\d+)?\s+[A-Za-z]?i?B/s)\s+([\d:]+)", stripped):
                extracted_speed = dl_match.group(1)
                extracted_eta = dl_match.group(2)

        if extracted_pct:
            self.status_label.update(f"⚡ Processing ALPM Transaction... ({extracted_pct}%)")
            try:
                self.progress_bar.progress = float(extracted_pct)
            except ValueError:
                pass

        if extracted_speed and extracted_eta:
            self.speed_label.update(f"Bandwidth: {extracted_speed} | ETA: {extracted_eta}")

        # Comprehensive filter: suppress ALL ephemeral progress bars, counters, and prompt echoes
        has_speed = bool(re.search(r"\d+(?:\.\d+)?\s+[A-Za-z]?i?B/s", stripped))
        has_bar = bool(re.search(r"\[[-#=coC\s]+\]", stripped)) or bool(re.search(r"\[[0-9;]*[mK]?[-#=coC\s]+", clean))
        is_fragment = bool(re.search(r"^[\[\]\-#=coC\s\d%:\.\w]+$", stripped)) and len(stripped) < 25
        is_prompt = stripped.startswith(":: Proceed with installation?") or "checking keyring" in stripped.lower()

        if has_speed or has_bar or is_fragment or is_prompt:
            return

        # Write clean, permanent system logs to the UI
        self.log_widget.write(Text.from_ansi(clean))

    @staticmethod
    def _is_package_manager_active() -> bool:
        """Scans /proc natively to check if any package management binaries are actively running."""
        target_procs = {"pacman", "paru", "yay", "makepkg", "fakeroot"}
        try:
            for entry in Path("/proc").iterdir():
                if entry.name.isdigit():
                    try:
                        comm = (entry / "comm").read_text().strip()
                        if comm in target_procs:
                            return True
                    except (OSError, FileNotFoundError):
                        continue
        except OSError:
            pass
        return False

    async def resolve_pacman_lock(self) -> bool:
        """Monitors db.lck; automatically removes stale locks if no package managers are running."""
        if not PACMAN_DB_LOCK.exists():
            return True
            
        self.log_system("Pacman database lock (/var/lib/pacman/db.lck) detected...", is_err=True)
        elapsed = 0
        
        while PACMAN_DB_LOCK.exists():
            if not self._is_package_manager_active():
                self.log_system("No active package managers detected in /proc. Lock is stale!", is_err=True)
                self.status_label.update("🧹 Removing stale pacman database lock...")
                self.log_system("Auto-removing stale /var/lib/pacman/db.lck...")
                
                if self.ctx.is_root:
                    try:
                        PACMAN_DB_LOCK.unlink(missing_ok=True)
                    except OSError as e:
                        self.log_system(f"Failed to remove lock: {e}", is_err=True)
                        return False
                else:
                    rm_proc = await asyncio.create_subprocess_exec(
                        "sudo", "rm", "-f", str(PACMAN_DB_LOCK),
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL
                    )
                    await rm_proc.wait()
                    if rm_proc.returncode != 0:
                        self.log_system("Failed to remove lock file via sudo.", is_err=True)
                        return False
                        
                self.log_system("Stale lock scrubbed successfully. Resuming pipeline.")
                return True

            if elapsed >= 300:
                self.log_system(f"Timed out after 300s waiting for {PACMAN_DB_LOCK} to release.", is_err=True)
                return False
                
            self.status_label.update(f"⚠️ PACMAN DB LOCKED: Active process running ({elapsed}s elapsed)...")
            await asyncio.sleep(1)
            elapsed += 1
            
        self.log_system("Pacman database lock released. Resuming pipeline.")
        return True

    def build_command(self, targets: list[str], is_aur: bool) -> list[str]:
        """Constructs privilege-aware execution commands for Chroot Root vs User Desktop."""
        if not is_aur:
            if self.ctx.is_root:
                return ["pacman", "-S", "--needed", "--noconfirm"] + targets
            return ["sudo", "pacman", "-S", "--needed", "--noconfirm"] + targets
        
        helper = self.ctx.aur_helper if self.ctx.aur_helper else "paru"
        base_aur = [helper, "-S", "--needed", "--noconfirm"] + targets
        if self.ctx.is_root and self.ctx.aur_user:
            return ["sudo", "-u", self.ctx.aur_user, "--preserve-env"] + base_aur
        return base_aur

    def build_manual_command(self, target: str, is_aur: bool) -> str:
        if not is_aur:
            return f"pacman -S {target}" if self.ctx.is_root else f"sudo pacman -S {target}"
        helper = self.ctx.aur_helper if self.ctx.aur_helper else "paru"
        if self.ctx.is_root and self.ctx.aur_user:
            return f"sudo -u {self.ctx.aur_user} {helper} -S {target}"
        return f"{helper} -S {target}"

    @work(thread=False)
    async def run_installation_pipeline(self) -> None:
        """Main async loop executing batch and granular package installations."""
        if not self.ctx.is_root and self.manifest.aur_packages:
            self.sudo_task = asyncio.create_task(AsyncPackageManager.maintain_sudo_heartbeat())
        
        try:
            if not await self.resolve_pacman_lock():
                self.exit(1)
                return

            self.status_label.update("Synchronizing databases & performing full system upgrade...")
            self.log_system("Executing full system upgrade (-Syu)...")
            
            upgrade_cmd = ["pacman", "-Syu", "--noconfirm"] if self.ctx.is_root else ["sudo", "pacman", "-Syu", "--noconfirm"]
            if not await self.execute_pty_command(upgrade_cmd):
                self.log_system("System upgrade failed or interrupted. Aborting suite.", is_err=True)
                return

            pending_official = [p for p in self.manifest.official_packages if p.status == PackageStatus.PENDING]
            if pending_official:
                await self.process_package_set(pending_official, is_aur=False)

            pending_aur = [p for p in self.manifest.aur_packages if p.status == PackageStatus.PENDING]
            if pending_aur and self.ctx.aur_helper:
                await self.process_package_set(pending_aur, is_aur=True)

            self.status_label.update("✨ All installation pipelines completed successfully!")
            self.speed_label.update("Bandwidth: Idle | ETA: 00:00")
            self.log_system("Installation sequence finished. All targets resolved.")
            
        finally:
            if self.sudo_task: self.sudo_task.cancel()

    async def process_package_set(self, packages: list[PackageItem], is_aur: bool) -> None:
        """Attempts batch installation first; degrades seamlessly to granular recovery."""
        target_type = "AUR" if is_aur else "Official Repo"
        pkg_names = [p.name for p in packages]
        
        self.log_system(f"Attempting batch installation for {len(packages)} {target_type} package(s)...")
        for p in packages:
            self.update_package_node(p.name, PackageStatus.INSTALLING)

        if not await self.resolve_pacman_lock(): return

        batch_cmd = self.build_command(pkg_names, is_aur)
        if await self.execute_pty_command(batch_cmd):
            for p in packages:
                self.update_package_node(p.name, PackageStatus.INSTALLED)
                self.progress_bar.advance(1)
            self.log_system(f"Batch transaction for {target_type} completed successfully.")
            return

        self.log_system(
            f"Batch transaction failed for {target_type}. Initiating granular fallback mode...",
            is_err=True
        )
        
        for p in packages:
            await AsyncPackageManager.filter_installed_packages(self.manifest)
            if p.status == PackageStatus.INSTALLED:
                self.update_package_node(p.name, PackageStatus.INSTALLED)
                self.progress_bar.advance(1)
                continue

            self.update_package_node(p.name, PackageStatus.INSTALLING)
            self.status_label.update(f"Granular Target: {p.name} ({target_type})")
            if not await self.resolve_pacman_lock(): return

            cmd = self.build_command([p.name], is_aur)
            while True:
                if await self.execute_pty_command(cmd):
                    self.update_package_node(p.name, PackageStatus.INSTALLED)
                    self.progress_bar.advance(1)
                    self.log_system(f"Successfully installed: {p.name}")
                    break
                else:
                    self.update_package_node(p.name, PackageStatus.FAILED)
                    action = await self.push_screen_wait(
                        ConflictModalScreen(p.name, "Sub-process exited with non-zero status code.")
                    )
                    match action:
                        case "retry":
                            self.log_system(f"Retrying package: {p.name}...")
                            self.update_package_node(p.name, PackageStatus.INSTALLING)
                            continue
                        case "manual":
                            self.log_system(f"Suspending TTY for manual intervention on {p.name}...")
                            with self.suspend():
                                sys.stdout.flush()
                                old_attr = None
                                try: old_attr = termios.tcgetattr(sys.stdin.fileno())
                                except termios.error: pass
                                    
                                os.system("clear")
                                print(f"\n--- MANUAL INTERVENTION TTY: {p.name} ---")
                                os.system(self.build_manual_command(p.name, is_aur))
                                print("\n--- Returning to Textual UI in 2 seconds ---")
                                # FIXED: Using synchronous time.sleep inside suspend block
                                time.sleep(2)
                                
                                if old_attr:
                                    try: termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_attr)
                                    except termios.error: pass
                            
                            await AsyncPackageManager.filter_installed_packages(self.manifest)
                            if p.status == PackageStatus.INSTALLED:
                                self.update_package_node(p.name, PackageStatus.INSTALLED)
                                self.progress_bar.advance(1)
                                break
                            continue
                        case "skip":
                            self.update_package_node(p.name, PackageStatus.SKIPPED)
                            self.progress_bar.advance(1)
                            self.log_system(f"Skipped package: {p.name}", is_err=True)
                            break
                        case "abort":
                            self.log_system("User aborted installation sequence.", is_err=True)
                            self.exit(1)
                            return

    @staticmethod
    def _set_pty_size(fd: int, rows: int = 40, cols: int = 120) -> None:
        """Forces PTY dimensions to 120 columns to prevent pacman progress bar wrapping."""
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    async def execute_pty_command(self, cmd: list[str]) -> bool:
        """Spawns asynchronous subprocess inside a PTY using an internal stateful line buffer."""
        master_fd, slave_fd = pty.openpty()
        self._set_pty_size(master_fd, rows=40, cols=120)
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                close_fds=True
            )
            os.close(slave_fd)
            slave_fd = -1
            self.active_child_pid = proc.pid
            
            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            await loop.connect_read_pipe(lambda: protocol, os.fdopen(master_fd, "rb"))
            master_fd = -1

            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            line_buffer = ""

            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    if text := decoder.decode(b"", final=True):
                        line_buffer += text
                    if line_buffer:
                        for line in re.split(r'[\r\n]+', line_buffer):
                            if line: self.handle_pty_line(line)
                    break
                
                if text := decoder.decode(chunk):
                    line_buffer += text
                    # Process lines whenever carriage returns or newlines arrive
                    while True:
                        match = re.search(r'[\r\n]', line_buffer)
                        if not match:
                            break
                        idx = match.start()
                        line = line_buffer[:idx]
                        line_buffer = line_buffer[idx+1:]
                        if line:
                            self.handle_pty_line(line)

            rc = await proc.wait()
            self.active_child_pid = None
            return rc == 0
            
        except asyncio.CancelledError:
            if self.active_child_pid:
                try: os.kill(self.active_child_pid, signal.SIGTERM)
                except ProcessLookupError: pass
            raise
        except Exception as e:
            self.log_system(f"PTY Execution Exception: {e}", is_err=True)
            return False
            
        finally:
            self.active_child_pid = None
            if slave_fd != -1:
                try: os.close(slave_fd)
                except OSError: pass
            if master_fd != -1:
                try: os.close(master_fd)
                except OSError: pass

# ==============================================================================
# MAIN ENTRYPOINT & CLI PARSING
# ==============================================================================
def parse_command_line() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dusky Package Installer (Python 3.14 / Textual)"
    )
    parser.add_argument(
        "-p", "--profiles",
        nargs="+", default=[],
        help="Specify profile names or prefixes to install (e.g., -p 01_all 03_more)."
    )
    return parser.parse_args()

def main() -> None:
    args = parse_command_line()
    manifest = ProfileParser.resolve_manifests(args.profiles)
    
    if not manifest.official_packages and not manifest.aur_packages:
        Console().print("[bold yellow]:: No packages resolved from profiles! Check package_profiles/ directory.[/bold yellow]")
        sys.exit(0)

    try:
        has_aur_targets = len(manifest.aur_packages) > 0
        ctx = verify_runtime_environment(has_aur_targets)
    except PreflightError as err:
        Console().print(f"[bold red]{err}[/bold red]")
        sys.exit(1)

    asyncio.run(AsyncPackageManager.filter_installed_packages(manifest))

    if manifest.aur_packages and ctx.is_root:
        if not ctx.aur_user:
            Console().print(
                "[bold red]CRITICAL: AUR packages requested while running as root in Chroot, "
                "but no unprivileged user exists to run makepkg![/bold red]"
            )
            sys.exit(1)
        try:
            TEMP_SUDOERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            TEMP_SUDOERS_FILE.write_text(f"{ctx.aur_user} ALL=(ALL) NOPASSWD: ALL\n", encoding="utf-8")
            TEMP_SUDOERS_FILE.chmod(0o440)
        except OSError as e:
            Console().print(f"[bold red]CRITICAL: Failed to configure temporary chroot sudoers: {e}[/bold red]")
            sys.exit(1)

    if not ctx.is_root:
        Console().print("[bold cyan]:: Authenticating sudo privileges for official repository installations...[/bold cyan]")
        if os.system("sudo -v") != 0:
            Console().print("[bold red]:: Sudo authentication failed. Aborting.[/bold red]")
            sys.exit(1)

    try:
        app = EliteInstallerApp(manifest, ctx)
        app.run()
    finally:
        if TEMP_SUDOERS_FILE.exists():
            try: TEMP_SUDOERS_FILE.unlink()
            except OSError: pass

if __name__ == "__main__":
    main()
