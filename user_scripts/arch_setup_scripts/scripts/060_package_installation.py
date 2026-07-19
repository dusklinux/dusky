#!/usr/bin/env python3
# dusky_interactive=true
# ==============================================================================
# ELITE ARCH LINUX & AUR PACKAGE INSTALLER (v8.0 - Absolute Leading-Edge)
# ==============================================================================
# Architecture: Asynchronous Chunk PTY Streams | Textual Split-Screen TUI
# Compatibility: Python 3.14+ | Pacman v7.1.0+ | Paru / Yay | UWSM / Hyprland
# ==============================================================================

import asyncio
import argparse
import codecs
import fcntl
import os
import pty
import re
import shutil
import signal
import struct
import sys
import termios
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
    Footer, Static, RichLog, ProgressBar, Button, Label, Tree
)
from textual.widgets.tree import TreeNode

# ==============================================================================
# TYPE DEFINITIONS & CONSTANTS (Python 3.14 Strict PEP 695 Syntax)
# ==============================================================================
type PackageList = list[str]
type ProfileMap = dict[str, PackageList]

SCRIPT_DIR: Path = Path(__file__).resolve().parent
PROFILES_DIR: Path = SCRIPT_DIR / "package_profiles"
AUR_PROFILES_DIR: Path = PROFILES_DIR / "aur"
PACMAN_DB_LOCK: Path = Path("/var/lib/pacman/db.lck")

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
# CORE ENVIRONMENT & PRE-FLIGHT VERIFICATION
# ==============================================================================
class PreflightError(Exception):
    """Raised when strict Arch Linux runtime conditions are unmet."""

def verify_runtime_environment() -> str:
    """Verifies non-root execution, detects Arch release, and finds AUR helper."""
    if os.geteuid() == 0:
        raise PreflightError(
            "CRITICAL: Do not run this script as root! makepkg and AUR helpers "
            "forbid root execution for security. Official pacman transactions "
            "will be securely elevated via sudo automatically."
        )

    if not Path("/etc/arch-release").exists():
        raise PreflightError("CRITICAL: This installer is strictly for Arch Linux systems.")

    for cmd in ("pacman", "sudo"):
        if not shutil.which(cmd):
            raise PreflightError(f"CRITICAL: Required system binary not found: {cmd}.")

    aur_helper = ""
    for helper in ("paru", "yay"):
        if shutil.which(helper):
            aur_helper = helper
            break
            
    if not aur_helper:
        raise PreflightError("CRITICAL: No AUR helper detected! Please install paru or yay first.")

    return aur_helper

# ==============================================================================
# PROFILE & MANIFEST RESOLUTION ENGINE
# ==============================================================================
class ProfileParser:
    """Scans, parses, and deduplicates package profiles from directory manifests[cite: 3]."""
    
    @staticmethod
    def ensure_default_profiles() -> None:
        """Creates sample profile structures if directory does not exist[cite: 3]."""
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
            if p_file.is_dir():
                continue
            if any(p_file.name == t or p_file.stem == t or p_file.name.startswith(t) for t in target_names):
                pkgs = cls._read_manifest_file(p_file)
                for pkg_name in pkgs:
                    if pkg_name not in seen_official:
                        seen_official.add(pkg_name)
                        manifest.official_packages.append(
                            PackageItem(name=pkg_name, is_aur=False, profile=p_file.name)
                        )

        for p_file in sorted(aur_files):
            if p_file.is_dir():
                continue
            if any(p_file.name == t or p_file.stem == t or p_file.name.startswith(t) for t in target_names):
                pkgs = cls._read_manifest_file(p_file)
                for pkg_name in pkgs:
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
    """Manages non-blocking ALPM database checks and PTY sub-process execution[cite: 1, 2]."""
    
    @staticmethod
    async def filter_installed_packages(manifest: InstallationManifest) -> None:
        """Queries local ALPM database via pacman -T in batch asynchronously[cite: 2]."""
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
        """Keeps sudo timestamp alive in the background without leaking sub-processes[cite: 2]."""
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
    """Modal screen displayed when package installation encounters a failure or conflict[cite: 2]."""
    
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
    Button {
        margin: 0 1;
    }
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
    """The unified Textual TUI managing async PTY streams and visual telemetry[cite: 3]."""
    
    CSS = """
    Screen {
        background: $surface;
        layout: vertical;
    }
    #top_header {
        height: 1;
        dock: top;
        content-align: center middle;
        background: $primary-darken-2;
        color: $text;
        text-style: bold;
    }
    #main_dashboard {
        layout: horizontal;
        height: 1fr;
    }
    #left_pane {
        width: 38%;
        border-right: vkey $primary-darken-1;
        background: $surface-darken-1;
        padding: 0 1;
        height: 100%;
    }
    #right_pane {
        width: 62%;
        height: 100%;
        layout: vertical;
        background: $surface;
    }
    #telemetry_box {
        height: 5;
        border-bottom: hkey $primary-darken-1;
        padding: 0 1;
        layout: vertical;
        justify: center;
    }
    #status_label {
        text-style: bold;
        color: $accent;
    }
    #speed_label {
        color: $text-muted;
        text-style: italic;
    }
    #progress_bar {
        width: 100%;
        margin-top: 1;
    }
    RichLog {
        height: 1fr;
        border: none;
        scrollbar-size: 1 1;
    }
    #bottom_footer {
        dock: bottom;
        height: 1;
        background: $primary-darken-3;
        color: $text-muted;
    }
    Tree {
        background: $surface-darken-1;
        border: none;
        padding: 0;
    }
    """

    def __init__(self, manifest: InstallationManifest, aur_helper: str):
        super().__init__()
        self.manifest = manifest
        self.aur_helper = aur_helper
        self.sudo_task: Optional[asyncio.Task] = None
        self.active_child_pid: Optional[int] = None
        
        self.tree_widget = Tree("◈ Target Profiles & Packages")
        self.log_widget = RichLog(id="pty_log", highlight=True, markup=True, wrap=True)
        self.progress_bar = ProgressBar(
            total=self.manifest.total_requested,
            show_eta=True,
            show_percentage=True,
            id="progress_bar"
        )
        self.status_label = Label("Initializing installation sequence...", id="status_label")
        self.speed_label = Label("Bandwidth: -- MiB/s | ETA: --:--", id="speed_label")
        self.tree_nodes_map: dict[str, TreeNode] = {}
        self.profile_counts: dict[str, dict[str, int]] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="top_header"):
            yield Static(
                f"◈ ARCH LINUX & AUR ELITE INSTALLER  [Helper: {self.aur_helper}]",
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
                
        yield Footer()

    def on_mount(self) -> None:
        self.build_profile_tree()
        self.progress_bar.advance(self.manifest.already_installed)
        self.log_system("Environment pre-flight validated. Keyring & ALPM engine online.")
        self.log_system(
            f"Profiles loaded: {len(self.tree_nodes_map)} packages "
            f"({self.manifest.already_installed} already installed)."
        )
        self.run_installation_pipeline()

    def build_profile_tree(self) -> None:
        """Populates Left Pane hierarchy with profile folders and live status counters[cite: 3]."""
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
                n_label = f"{badge} {item.name}"
                node = p_node.add_leaf(n_label)
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
        """Updates individual package icon and recalculates parent folder ratios[cite: 3]."""
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
        self.log_widget.write(f"[{color}][SYSTEM][/{color}] {msg}")

    def handle_pty_chunk(self, raw_text: str) -> None:
        """Processes PTY streams, extracting percentage, speed, and ETA metrics[cite: 1]."""
        if match := re.search(r"(\d+)%.*?([\d\.]+\s+[KM]iB/s|--:--|[\d:]+)", raw_text):
            pct = match.group(1)
            metric = match.group(2)
            self.status_label.update(f"⚡ Processing ALPM Transaction... ({pct}%)")
            if "iB/s" in metric:
                self.speed_label.update(f"Bandwidth: {metric} | ETA: Calculating...")
            elif ":" in metric:
                self.speed_label.update(f"Bandwidth: Active | ETA: {metric}")
            
        lines = re.split(r'[\r\n]+', raw_text)
        for line in lines:
            clean = line.strip()
            if clean and not clean.startswith(":: Proceed with installation?"):
                self.log_widget.write(clean)

    async def wait_for_pacman_lock(self) -> bool:
        """Monitors /var/lib/pacman/db.lck asynchronously with a visual countdown[cite: 1]."""
        if not PACMAN_DB_LOCK.exists():
            return True
            
        self.log_system("Pacman database is currently locked by another process! Waiting...", is_err=True)
        elapsed = 0
        while PACMAN_DB_LOCK.exists():
            if elapsed >= 300:
                self.log_system(f"Timed out after 300s waiting for {PACMAN_DB_LOCK} to release[cite: 1].", is_err=True)
                return False
            self.status_label.update(f"⚠️ PACMAN DB LOCKED: Waiting for release ({elapsed}s elapsed)...[cite: 1]")
            await asyncio.sleep(1)
            elapsed += 1
            
        self.log_system("Pacman database lock released. Resuming pipeline[cite: 1].")
        return True

    @work(thread=False)
    async def run_installation_pipeline(self) -> None:
        """Main async loop executing batch and granular package installations[cite: 1, 2]."""
        self.sudo_task = asyncio.create_task(AsyncPackageManager.maintain_sudo_heartbeat())
        
        try:
            if not await self.wait_for_pacman_lock():
                self.exit(1)
                return

            self.status_label.update("Synchronizing databases & performing full system upgrade...[cite: 1, 2]")
            self.log_system("Executing full system upgrade (-Syu)...[cite: 1, 2]")
            upgrade_cmd = ["sudo", "pacman", "-Syu", "--noconfirm"]
            if not await self.execute_pty_command(upgrade_cmd):
                self.log_system("System upgrade failed or interrupted. Aborting suite[cite: 1, 2].", is_err=True)
                return

            pending_official = [
                p for p in self.manifest.official_packages
                if p.status == PackageStatus.PENDING
            ]
            if pending_official:
                await self.process_package_set(pending_official, is_aur=False)

            pending_aur = [
                p for p in self.manifest.aur_packages
                if p.status == PackageStatus.PENDING
            ]
            if pending_aur:
                await self.process_package_set(pending_aur, is_aur=True)

            self.status_label.update("✨ All installation pipelines completed successfully!")
            self.speed_label.update("Bandwidth: Idle | ETA: 00:00")
            self.log_system("Installation sequence finished. All targets resolved[cite: 1, 2].")
            
        finally:
            if self.sudo_task:
                self.sudo_task.cancel()

    async def process_package_set(self, packages: list[PackageItem], is_aur: bool) -> None:
        """Attempts batch installation first; degrades seamlessly to granular recovery[cite: 1, 2]."""
        target_type = "AUR" if is_aur else "Official Repo"
        pkg_names = [p.name for p in packages]
        
        self.log_system(f"Attempting batch installation for {len(packages)} {target_type} package(s)...[cite: 1, 2]")
        for p in packages:
            self.update_package_node(p.name, PackageStatus.INSTALLING)

        if not await self.wait_for_pacman_lock():
            return

        batch_cmd = (
            [self.aur_helper, "-S", "--needed", "--noconfirm"] + pkg_names if is_aur
            else ["sudo", "pacman", "-S", "--needed", "--noconfirm"] + pkg_names
        )

        success = await self.execute_pty_command(batch_cmd)
        
        if success:
            for p in packages:
                self.update_package_node(p.name, PackageStatus.INSTALLED)
                self.progress_bar.advance(1)
            self.log_system(f"Batch transaction for {target_type} completed successfully[cite: 1, 2].")
            return

        self.log_system(
            f"Batch transaction failed for {target_type}. Initiating granular fallback mode...[cite: 1, 2]",
            is_err=True
        )
        
        for p in packages:
            await AsyncPackageManager.filter_installed_packages(self.manifest)
            if p.status == PackageStatus.INSTALLED:
                self.update_package_node(p.name, PackageStatus.INSTALLED)
                self.progress_bar.advance(1)
                continue

            self.update_package_node(p.name, PackageStatus.INSTALLING)
            self.status_label.update(f"Granular Target: {p.name} ({target_type})[cite: 2]")
            
            if not await self.wait_for_pacman_lock():
                return

            cmd = (
                [self.aur_helper, "-S", "--needed", "--noconfirm", p.name] if is_aur
                else ["sudo", "pacman", "-S", "--needed", "--noconfirm", p.name]
            )

            while True:
                pkg_success = await self.execute_pty_command(cmd)
                if pkg_success:
                    self.update_package_node(p.name, PackageStatus.INSTALLED)
                    self.progress_bar.advance(1)
                    self.log_system(f"Successfully installed: {p.name}[cite: 2]")
                    break
                else:
                    self.update_package_node(p.name, PackageStatus.FAILED)
                    
                    action = await self.push_screen_wait(
                        ConflictModalScreen(p.name, "Sub-process exited with non-zero status code[cite: 2].")
                    )
                    
                    match action:
                        case "retry":
                            self.log_system(f"Retrying package: {p.name}...[cite: 2]")
                            self.update_package_node(p.name, PackageStatus.INSTALLING)
                            continue
                        case "manual":
                            self.log_system(f"Suspending TTY for manual intervention on {p.name}...[cite: 2]")
                            with self.suspend():
                                sys.stdout.flush()
                                old_attr = None
                                try:
                                    old_attr = termios.tcgetattr(sys.stdin.fileno())
                                except termios.error:
                                    pass
                                    
                                os.system("clear")
                                print(f"\n--- MANUAL INTERVENTION TTY: {p.name} ---[cite: 2]")
                                manual_cmd = (
                                    f"{self.aur_helper} -S {p.name}" if is_aur
                                    else f"sudo pacman -S {p.name}"
                                )
                                os.system(manual_cmd)
                                print("\n--- Returning to Textual UI in 2 seconds ---")
                                asyncio.run(asyncio.sleep(2))
                                
                                if old_attr:
                                    try:
                                        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_attr)
                                    except termios.error:
                                        pass
                            
                            await AsyncPackageManager.filter_installed_packages(self.manifest)
                            if p.status == PackageStatus.INSTALLED:
                                self.update_package_node(p.name, PackageStatus.INSTALLED)
                                self.progress_bar.advance(1)
                                break
                            continue
                        case "skip":
                            self.update_package_node(p.name, PackageStatus.SKIPPED)
                            self.progress_bar.advance(1)
                            self.log_system(f"Skipped package: {p.name}[cite: 2]", is_err=True)
                            break
                        case "abort":
                            self.log_system("User aborted installation sequence[cite: 2].", is_err=True)
                            self.exit(1)
                            return

    @staticmethod
    def _set_pty_size(fd: int, rows: int = 40, cols: int = 120) -> None:
        """Forces PTY dimensions to 120 columns to prevent pacman progress bar wrapping[cite: 1]."""
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    async def execute_pty_command(self, cmd: list[str]) -> bool:
        """Spawns asynchronous subprocess inside a PTY using incremental UTF-8 decoding[cite: 1, 2]."""
        master_fd, slave_fd = pty.openpty()
        self._set_pty_size(master_fd, rows=40, cols=120)
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
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
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    text = decoder.decode(b"", final=True)
                    if text:
                        self.handle_pty_chunk(text)
                    break
                text = decoder.decode(chunk)
                if text:
                    self.handle_pty_chunk(text)

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
        description="Elite Arch Linux & AUR Package Installer (Python 3.14 / Textual)[cite: 1, 2]"
    )
    parser.add_argument(
        "-p", "--profiles",
        nargs="+",
        default=[],
        help="Specify profile names or prefixes to install (e.g., -p 01_all 03_more). "
             "Defaults to '01_' if omitted[cite: 3]."
    )
    return parser.parse_args()

def main() -> None:
    try:
        aur_helper = verify_runtime_environment()
    except PreflightError as err:
        Console().print(f"[bold red]{err}[/bold red]")
        sys.exit(1)

    args = parse_command_line()
    
    Console().print("[bold cyan]:: Resolving package manifests and querying ALPM database...[/bold cyan][cite: 1, 2]")
    manifest = ProfileParser.resolve_manifests(args.profiles)
    
    if not manifest.official_packages and not manifest.aur_packages:
        Console().print("[bold yellow]:: No packages resolved from profiles! Check package_profiles/ directory.[/bold yellow][cite: 3]")
        sys.exit(0)

    asyncio.run(AsyncPackageManager.filter_installed_packages(manifest))

    Console().print("[bold cyan]:: Authenticating sudo privileges for official repository installations...[/bold cyan][cite: 2]")
    if os.system("sudo -v") != 0:
        Console().print("[bold red]:: Sudo authentication failed. Aborting.[/bold red][cite: 2]")
        sys.exit(1)

    app = EliteInstallerApp(manifest, aur_helper)
    app.run()

if __name__ == "__main__":
    main()
